"""*See what's outside* — what the fast layer does not hold, derived from its own state (#14012).

WHY THIS EXISTS
---------------
The owner-approved capacity notices (`#13998`) and the public docs page they link to
(`#14013`) promise one answer to *"so what went out?"*:

    **See what is outside.** What has stepped out of the fast layer can be listed —
    how much of it, roughly when, and what it was about — so what is outside is
    something you can look at rather than a black box.

So this module answers exactly three questions, and nothing else:

* **how much** — a count of the record's residency-eligible rows the graph does not hold;
* **roughly when** — the oldest and newest `created_at` among them;
* **what it was about** — the dominant point kinds among them, by count.

⛔ IT IS DERIVED FROM THE PROJECTION'S OWN STATE, NOT A SECOND BOOKKEEPING COPY.
The resident set is read from the GRAPH — the thing whose contents the question is
about — and the comparison is made in the record, which is the superset. There is no
"what we evicted" table anywhere: such a table is a second declaration of a fact the
graph already carries, it goes stale the moment anything else changes the graph
(a rebuild, a manual `DETACH DELETE`, a second writer), and the stale copy would answer
this question with a number nobody could check. The graph cannot be stale about what the
graph holds.

⛔ AND IT NEVER IMPLIES COMPLETENESS (`#2952`). Three separate guards, because the
failure here is a number that LOOKS precise:

1. **A truncated resident read is an UNMEASURED answer, not a smaller one.** If the graph
   holds more keys than one read may pull (:data:`DEFAULT_RESIDENT_KEY_LIMIT`), the
   complement cannot be computed — so ``outside`` is ``None`` with a reason, never the
   count that a short resident list would produce (which would OVER-count what is
   outside, the direction that scares a customer for no reason).
2. **The view states what IS, never what is not.** ``outside`` is "not resident in the
   fast layer". It is not "deleted", not "lost", not "gone": §2.0's record keeps
   everything, and the docs page's promise is *"nothing is lost"*. A caller that wants
   to know whether something is retrievable reads the record — which is what this view's
   own count comes from.
3. **``complete`` is a field, not a footnote.** ``True`` only when both halves were read
   in full. A monitor must not read a partial view as the whole population.

THE TIER PREDICATE IS THE PROJECTION'S, VERBATIM
------------------------------------------------
``public.point.cache_tier = 'graph'`` is the §9.1 placement declaration (a turn row at
``record_only`` is deliberately outside the graph and is NOT "outside the fast layer" —
it was never meant to be in it). Reading ``<> 'record_only'`` here would be the exact
defect `cache_projection.read_record_view` documents: the partial index
``idx_point_graph_tier`` is only used when the predicate IMPLIES it, so the ``<>``
spelling reads every row of the record's highest-volume table and discards the ones the
index exists to skip. One spelling, one meaning, in both readers.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import record_projection as _rp
from .cache_projection import NODE_TABLES
from .resident_pin import PinConfigError, bare_identifier

#: How many resident keys one read may pull. A bounded read is what makes the answer
#: either COMPLETE or explicitly UNMEASURED — never a silently short one.
DEFAULT_RESIDENT_KEY_LIMIT = 200_000

#: How many dominant kinds the view names. Enough to recognise the population, small
#: enough that the surface stays a summary rather than an export.
DEFAULT_TOP_TOPICS = 5

#: The record column the "what it was about" answer groups by. `point_kind` is the
#: ontology's own kind vocabulary (`docs/ONTOLOGY.md` §4.1) — a stable, already-populated
#: column, unlike `tags`, which is free-form and often absent.
TOPIC_COLUMN = "point_kind"

#: The columns this module reads. Pinned against `record_projection.table_columns()` by
#: the suite, so a renamed column is a RED rather than a SQL error in production.
READ_COLUMNS: tuple[str, ...] = ("node_key", "created_at", "cache_tier", TOPIC_COLUMN)

#: The span query: how many, and roughly when. One statement, aggregates only — nothing
#: here transfers a row it does not have to.
SPAN_SQL = (
    "SELECT count(*) AS outside, min(created_at) AS oldest, max(created_at) AS newest "
    "FROM public.{table} "
    "WHERE org_id = %s AND graph_id = %s{tier} "
    "AND NOT (node_key = ANY(%s::text[]))"
)

#: The topic query. `ORDER BY n DESC, topic ASC` — the second key makes two runs over the
#: same data agree exactly, which a `LIMIT` over ties otherwise does not.
TOPICS_SQL = (
    "SELECT {topic} AS topic, count(*) AS n "
    "FROM public.{table} "
    "WHERE org_id = %s AND graph_id = %s{tier} "
    "AND NOT (node_key = ANY(%s::text[])) "
    "GROUP BY 1 ORDER BY n DESC, topic ASC NULLS LAST LIMIT %s"
)


@dataclass(frozen=True)
class OutsideBucket:
    """One dominant kind among what is outside the fast layer."""

    topic: str
    count: int

    def as_dict(self) -> dict[str, Any]:
        return {"topic": self.topic, "count": self.count}


@dataclass(frozen=True)
class OutsideView:
    """What the fast layer does not hold for ONE (org, graph) scope.

    ⛔ ``outside is None`` means UNMEASURED, never zero. The distinction is the same one
    ``cache_evictor.EvictionReport`` makes for its byte readings: a monitor that reads a
    missing measurement as a healthy zero is the defect both modules exist to prevent.
    """

    org_id: str
    graph_id: str
    label: str = "Point"
    #: Nodes of this label the GRAPH holds — read from the graph, not counted elsewhere.
    resident: int | None = None
    #: Rows of this label the RECORD holds at the residency-eligible tier.
    record_total: int | None = None
    #: ``record_total`` minus the resident keys. ``None`` when it could not be measured.
    outside: int | None = None
    oldest: str | None = None
    newest: str | None = None
    topics: tuple[OutsideBucket, ...] = ()
    #: ``True`` only when both halves were read IN FULL. A partial view is not a census.
    complete: bool = False
    truncated: bool = False
    reason: str = ""
    source: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)

    # ── the promise, stated where a caller reads it ──────────────────────
    @property
    def headline(self) -> str:
        """One plain sentence a non-technical surface can show verbatim.

        ⛔ The zero case is scoped to THIS VIEW's label, deliberately. It used to read
        *"Everything in your record is currently held in the fast layer"* — a
        COMPLETENESS claim about the whole record, made from a single-label count. Sweep
        `Source` while this view counts `Point` and the customer is told nothing is
        outside while a whole population is (`#2952`: never imply completeness).
        """
        if self.outside is None:
            return (
                "How much of your data is outside the fast layer could not be measured "
                f"({self.reason or 'unreadable'}) — nothing is claimed."
            )
        if self.outside == 0:
            return (
                f"Nothing outside the fast layer for the {self.label} items this view "
                f"covers — they are answered from it, so nothing is fetched back on a "
                f"search."
            )
        span = ""
        if self.oldest or self.newest:
            span = f" (oldest {self.oldest or 'unknown'}, newest {self.newest or 'unknown'})"
        return (
            f"{self.outside} item(s) are outside the fast layer{span}. They are still in "
            f"your record and are fetched when you search for them, so they can take a "
            f"moment longer."
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "graph_id": self.graph_id,
            "label": self.label,
            "resident": self.resident,
            "record_total": self.record_total,
            "outside": self.outside,
            "oldest": self.oldest,
            "newest": self.newest,
            "topics": [b.as_dict() for b in self.topics],
            "complete": self.complete,
            "truncated": self.truncated,
            "reason": self.reason,
            "source": self.source,
            "headline": self.headline,
        }


def _checked(value: str, *, what: str) -> str:
    """Validate an identifier for THIS module's SQL, as a ``ValueError``.

    ``bare_identifier`` is the package's one shape check; its own error type is the pin's
    (``PinConfigError``, a configuration refusal). A bad ``--topic-column`` is a USAGE
    mistake, so it is re-raised as ``ValueError`` — which is what the verb turns into
    exit 1 (usage), not exit 4 (unmeasured).
    """
    try:
        return bare_identifier(value, what=what)
    except PinConfigError as exc:
        raise ValueError(str(exc)) from exc


def _rows(cur: Any, sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
    """Run one statement and return `dict`-shaped rows — the record's own reader shape."""
    cur.execute(sql, params)
    fetched = cur.fetchall()
    if not fetched:
        return []
    if isinstance(fetched[0], Mapping):
        return list(fetched)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in fetched]


def _tier_predicate(table: str) -> str:
    """The §9.1 tier predicate — on `point` ONLY, because it is the only tiered table.

    ⛔ ``= 'graph'``, not ``<> 'record_only'``: see this module's docstring and
    `cache_projection.read_record_view`. Both readers must spell it the same way or the
    partial index serves one of them and the other scans the record.
    """
    return " AND cache_tier = 'graph'" if table == "point" else ""


def resident_keys(graph: Any, *, label: str = "Point", key_prop: str = "id",
                  limit: int = DEFAULT_RESIDENT_KEY_LIMIT) -> tuple[list[str], bool]:
    """The keys the GRAPH holds for one label — ``(keys, truncated)``.

    Reads ``limit + 1`` so a full answer and a truncated one cannot be confused: if the
    extra row arrives, the read is short and the caller must not compute a complement
    from it.
    """
    if label not in NODE_TABLES:
        raise ValueError(
            f"label {label!r} is not a record label {sorted(NODE_TABLES)}"
        )
    _checked(label, what="label")
    _checked(key_prop, what="key property")
    # ⛔ The bound is INTERPOLATED, not parameterised: `LIMIT $limit` is not accepted by
    # every engine version this runs against, and a rejected statement would turn the
    # whole view into "could not be read" — a control that never works rather than one
    # that answers. The value is an int, validated here, so there is nothing to inject.
    # ⛔ The floor is on `limit`, not on the `+1` probe row: `limit=0` used to pass a
    # `bound < 1` check (`0 + 1 = 1`) and return an EMPTY resident set declared
    # truncated — which is exactly the shape the truncation guard reports as unmeasured,
    # reached through an argument that looked harmless.
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError(f"limit must be a positive integer, got {limit!r}")
    bound = limit + 1
    rows = graph.query(
        f"MATCH (n:{label}) RETURN n.{key_prop} AS key LIMIT {bound}",
        params={},
    ).result_set
    keys = [row[0] for row in rows if row and isinstance(row[0], str) and row[0]]
    return keys[:limit], len(rows) > limit


def resident_count(graph: Any, *, label: str = "Point") -> int | None:
    """How many nodes of this label the graph holds. ``None`` when unreadable."""
    if label not in NODE_TABLES:
        raise ValueError(f"label {label!r} is not a record label {sorted(NODE_TABLES)}")
    _checked(label, what="label")
    try:
        rows = graph.query(
            f"MATCH (n:{label}) RETURN count(n)", params={}
        ).result_set
    except Exception:
        return None
    if not rows or not rows[0]:
        return None
    try:
        return int(rows[0][0])
    except (TypeError, ValueError):
        return None


def read(
    graph: Any,
    conn: Any,
    *,
    org_id: str,
    graph_id: str,
    label: str = "Point",
    key_prop: str = "id",
    topic_column: str = TOPIC_COLUMN,
    top: int = DEFAULT_TOP_TOPICS,
    key_limit: int = DEFAULT_RESIDENT_KEY_LIMIT,
) -> OutsideView:
    """Read what the fast layer does not hold for ONE tenant's graph.

    The scope predicate is not optional and is installed by the record's own reader —
    ``record_projection.scope_connection`` — which sets the tenancy GUC the row policies
    read. A view built without it would answer with every tenant's rows, which is the
    one shape a "how much is outside" number must never take.
    """
    if not org_id or not graph_id:
        raise ValueError("org_id and graph_id are required — this view is per scope")
    if label not in NODE_TABLES:
        raise ValueError(f"label {label!r} is not a record label {sorted(NODE_TABLES)}")

    table = NODE_TABLES[label]
    # ⛔ THE ONE IDENTIFIER THAT REACHES A **SQL** STRING, and therefore the one that
    # must be allowlisted rather than merely quoted: `TOPICS_SQL` puts `{topic}` in the
    # SELECT list and `GROUP BY 1`, which is an IDENTIFIER position — `%s` covers
    # VALUES only (psycopg docs, and the reason `sql.Identifier` exists). Every other
    # identifier here is already checked (`label`/`key_prop` through `bare_identifier`,
    # `table` through `NODE_TABLES`, `tier` from two literals, `limit` type-checked) —
    # a `.format` that skipped the check would make the injection surface a DEFAULT for
    # the next caller of this library door instead of a decision they have to make.
    # An allowlist, not just quoting: it also refuses a REAL column of the wrong table.
    valid_columns = _rp.table_columns().get(table, frozenset())
    if topic_column not in valid_columns:
        raise ValueError(
            f"topic_column {topic_column!r} is not a column of public.{table} "
            f"— it is interpolated into the SELECT list, so it must name one of "
            f"{sorted(valid_columns)}; nothing was read"
        )
    tier = _tier_predicate(table)
    # ⛔ THE GROUPING COLUMN IS INTERPOLATED, SO IT IS VALIDATED — as a bare identifier
    # AND as a column the record actually has. The CLI feeds `--topic-column` straight
    # through, so without this any SQL EXPRESSION ran against the record under the tool's
    # DSN and its value came back in `topics`: every column of every table was readable
    # through a view that is supposed to describe one grouping dimension, and an
    # expensive subquery could be forced on every call. `READ_COLUMNS` is the declared
    # surface; the schema is the authority it is checked against.
    _checked(topic_column, what="topic column")
    # ⛔ `key_prop` IS THE THIRD INTERPOLATED IDENTIFIER, and it is validated HERE rather
    # than only inside `resident_keys` — which sits behind the guard's `except Exception`.
    # Validated only there, a bad `--key-prop` came back as `complete=False` with
    # `ValueError: key property ...` inside the reason, i.e. exit 4 (UNMEASURED, "retry")
    # for a mistake no retry can fix: the operator was told the MEASUREMENT failed when
    # the FLAG is wrong. The other two identifiers already raise before the guard for
    # exactly this reason; the third must not be the exception. `tools/resident.py`'s
    # `except (ValueError, PinConfigError) -> parser.error` arm is what turns this into
    # exit 1 (usage).
    _checked(key_prop, what="key property")
    columns = _rp.table_columns().get(table)
    if columns is not None and topic_column not in columns:
        raise ValueError(
            f"topic column {topic_column!r} is not a column of public.{table} "
            f"(record_projection.table_columns()) — a grouping dimension this view may "
            f"quote has to be a real column of the table it groups"
        )
    resident_total = resident_count(graph, label=label)
    try:
        keys, truncated = resident_keys(graph, label=label, key_prop=key_prop,
                                        limit=key_limit)
    except Exception as exc:
        return OutsideView(
            org_id=org_id, graph_id=graph_id, label=label,
            resident=resident_total,
            complete=False,
            reason=(f"the fast layer's own key list could not be read "
                    f"({type(exc).__name__}: {exc}) — nothing is claimed"),
            source=f"graph:{label}",
        )

    if truncated:
        # ⛔ A SHORT RESIDENT LIST WOULD OVER-COUNT. The complement of a truncated
        # resident set is "rows that are not in the part of the cache we looked at" —
        # which reports as outside things that are resident, the direction that
        # needlessly worries a customer. So the answer is UNMEASURED, with the bound
        # named.
        return OutsideView(
            org_id=org_id, graph_id=graph_id, label=label,
            resident=resident_total,
            complete=False, truncated=True,
            reason=(f"the fast layer holds more than {key_limit} key(s), which is the "
                    f"most one read may pull — how much is outside cannot be measured "
                    f"from a partial resident set"),
            source=f"graph:{label}",
        )

    # ⛔ THE TENANT SCOPE IS INSIDE THE GUARD, because installing it is one of the
    # things that REFUSES: `record_projection._apply_tenant_scope` raises by design on a
    # role that bypasses RLS, on a GUC it could not set, and on a scope it could not
    # verify (#9600) — and `conn.cursor()` raises on a dead connection. Outside the
    # `try`, those escaped as a traceback out of the verb, whose exit 1 is the USAGE
    # code: a scheduler was told to retry its FLAGS against a DSN that can never work,
    # and the module's own contract (an unanswerable read returns `outside=None` with a
    # reason) was broken on exactly the refusal it exists to report.
    try:
        cur = _rp.scope_connection(conn, org_id)
        span = _rows(cur, SPAN_SQL.format(table=table, tier=tier),
                     (org_id, graph_id, list(keys)))
        buckets = _rows(
            cur, TOPICS_SQL.format(table=table, tier=tier, topic=topic_column),
            (org_id, graph_id, list(keys), int(top)),
        )
    except Exception as exc:
        return OutsideView(
            org_id=org_id, graph_id=graph_id, label=label,
            resident=resident_total,
            complete=False,
            reason=(f"the record could not be read ({type(exc).__name__}: {exc}) — "
                    f"nothing is claimed"),
            source=f"record:{table}",
        )

    row = span[0] if span else {}
    outside_raw = row.get("outside")
    try:
        outside = int(outside_raw) if outside_raw is not None else None
    except (TypeError, ValueError):
        outside = None
    # The record's total is its own cheap aggregate — asked directly rather than derived
    # from the two other numbers, so a disagreement between them is visible instead of
    # silently reconciled.
    record_total = _record_total(cur, table=table, tier=tier,
                                 org_id=org_id, graph_id=graph_id)
    topics = tuple(
        OutsideBucket(topic=str(b.get("topic") or ""), count=int(b.get("n") or 0))
        for b in buckets
        if b.get("topic") is not None
    )
    # ⛔ `complete` is DERIVED, not assumed — and this block is the MERGE of two lanes
    # that closed the same fail-open independently, rather than a pick of one side.
    # Every number on this path can fail ALONE: `resident_total` is its own count query,
    # `_record_total` issues its own statement after SPAN_SQL/TOPICS_SQL have already
    # succeeded (a statement timeout, a dropped connection mid-scope, a `count(*)`
    # permission the aggregates do not need), and an empty span leaves `outside`
    # unmeasured. This field is what a monitor keys on and what `tools/resident.py` turns
    # into `verdict: OK` / exit 0, so an unmeasured number under `complete=True` is the
    # flag failing OPEN over a partial read — exactly what this module's thesis forbids.
    missing = [name for name, value in
               (("resident", resident_total), ("record_total", record_total),
                ("outside", outside)) if value is None]
    reason = ""
    if outside is None:
        reason = ("the record answered its count with no row, so how much is outside "
                  "could not be measured — nothing is claimed")
    elif record_total is None:
        reason = ("the record's own total could not be read, so the cross-check that "
                  "makes a resident/outside disagreement visible is missing — this "
                  "view is not a census")
    elif resident_total is None:
        reason = ("the fast layer's own node count could not be read, so what is "
                  "outside is reported without the resident figure that explains it")
    return OutsideView(
        org_id=org_id,
        graph_id=graph_id,
        label=label,
        resident=resident_total,
        record_total=record_total,
        outside=outside,
        oldest=_text(row.get("oldest")),
        newest=_text(row.get("newest")),
        topics=topics,
        complete=not missing,
        truncated=False,
        reason=reason if missing else "",
        source=f"graph:{label}+record:{table}",
    )


def _text(value: Any) -> str | None:
    """A timestamp as text, or ``None``. The record stores `created_at` as text."""
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    return str(value)


def _record_total(cur: Any, *, table: str, tier: str,
                  org_id: str, graph_id: str) -> int | None:
    """The record's row count at the residency-eligible tier. ``None`` when unreadable."""
    try:
        rows = _rows(
            cur,
            f"SELECT count(*) AS n FROM public.{table} "
            f"WHERE org_id = %s AND graph_id = %s{tier}",
            (org_id, graph_id),
        )
    except Exception:
        return None
    if not rows or rows[0].get("n") is None:
        return None
    try:
        return int(rows[0]["n"])
    except (TypeError, ValueError):
        return None


__all__ = [
    "DEFAULT_RESIDENT_KEY_LIMIT",
    "DEFAULT_TOP_TOPICS",
    "READ_COLUMNS",
    "OutsideBucket",
    "OutsideView",
    "read",
    "resident_count",
    "resident_keys",
]
