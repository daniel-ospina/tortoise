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
from .cache_projection import KEY_PROPERTY, NODE_TABLES
from .resident_pin import PinConfigError, bare_identifier

#: How many resident keys one read may pull. A bounded read is what makes the answer
#: either COMPLETE or explicitly UNMEASURED — never a silently short one.
DEFAULT_RESIDENT_KEY_LIMIT = 200_000

#: How many dominant kinds the view names. Enough to recognise the population, small
#: enough that the surface stays a summary rather than an export.
DEFAULT_TOP_TOPICS = 5

#: The record column the "what it was about" answer groups by for a ``Point``.
#: `point_kind` is the ontology's own kind vocabulary (`docs/ONTOLOGY.md` §4.1) — a
#: stable, already-populated column, unlike `tags`, which is free-form and often absent.
TOPIC_COLUMN = "point_kind"

#: ⛔ THE LABEL AXIS IS SIX TABLES AND THE COLUMN AXIS WAS ONE. `read()` accepts every key
#: of `NODE_TABLES` (`point, event, subject, object, source, session`), but the topic
#: column was hardcoded to `point_kind` — which exists ONLY on `public.point`. So every
#: other label failed out of the box with a usage error naming a column the CALLER never
#: passed (the default did), and the CLI's own exit table described neither case. The
#: default is now per table: `<label>_kind` where the record has it.
TOPIC_COLUMN_BY_LABEL: dict[str, str] = {
    "Point": "point_kind",
    "Event": "event_kind",
    "Subject": "subject_kind",
    "Object": "object_kind",
    "Source": "source_kind",
    #: The session table carries no `*_kind` column at all, so "what it was about" has no
    #: default here: the caller must name a grouping column. `read()` refuses otherwise,
    #: as a USAGE error that lists what the table does have.
    "Session": "",
}

#: ⛔ The same single-table assumption broke the SPAN: `created_at` does not exist on
#: `public.event`, so `outside --label Event` read as UNMEASURED (exit 4) forever — a
#: scheduler retrying a command no retry can fix, for a table that answers perfectly well
#: about "roughly when" through `started_at`.
TIMESTAMP_COLUMN_BY_LABEL: dict[str, str] = {
    "Point": "created_at",
    "Event": "started_at",
    "Subject": "created_at",
    "Object": "created_at",
    "Source": "created_at",
    "Session": "created_at",
}

#: The columns this module reads for the default label. Pinned against
#: `record_projection.table_columns()` by the suite, so a renamed column is a RED rather
#: than a SQL error in production — and the OTHER five tables are pinned the same way by
#: that suite's per-label arm, because pinning `point` alone is what let the two
#: single-table assumptions above survive.
READ_COLUMNS: tuple[str, ...] = ("node_key", "created_at", "cache_tier", TOPIC_COLUMN)

#: The span query: how many, and roughly when. One statement, aggregates only — nothing
#: here transfers a row it does not have to.
SPAN_SQL = (
    "SELECT count(*) AS outside, min({ts}) AS oldest, max({ts}) AS newest "
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
    key_prop: str | None = None,
    topic_column: str | None = None,
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
    columns_for_table = _rp.table_columns().get(table, frozenset())
    # ⛔ THE GRAPH IS KEYED PER LABEL, AND `id` IS NOT ITS CONTRACT KEY FOR TWO OF THEM.
    # `cache_projection.KEY_PROPERTY` is the authority —
    # `{'Point': 'id', 'Event': 'eventId', 'Subject': 'id', 'Object': 'id',
    #   'Source': 'url', 'Session': 'id'}` — and the two halves of this view only
    # describe the same datum when `key_prop` IS that key: `resident_keys` reads the
    # graph by `n.{key_prop}` while the complement is computed in the record on
    # `node_key`, and the schema's own identity CHECKs are `node_key = event_id` and
    # `node_key = url` (`record_writer.py` states the precedence: "`eventId` is the
    # fold's MERGE key for a Point/Event row, so it wins over a generic `id`").
    #
    # ⛔ MEASURED, and this is the fabricated-measurement shape: with `id` on an
    # `:Event` graph carrying `eventId` (and a NULL `id`, which placement does not
    # populate), `MATCH (n:Event) RETURN n.id AS key` yields NULLs, the read drops them,
    # `truncated` stays False (only `len(rows)` is compared) and the SQL complement runs
    # against an EMPTY resident array — so the view reported the WHOLE population as
    # outside with `complete=True`, i.e. `verdict: OK` and exit 0. The over-count that
    # `--label Event`/`--label Source` would then state to a customer is the direction
    # this module's truncation guard exists to prevent, reached through the one axis it
    # does not look at.
    default_key_prop = KEY_PROPERTY.get(label)
    if key_prop is None:
        if not default_key_prop:
            raise ValueError(
                f"no contract key property is declared for {label} "
                f"(cache_projection.KEY_PROPERTY), so the graph's keys cannot be "
                f"compared with the record's node_key: name one with --key-prop"
            )
        key_prop = default_key_prop
    elif default_key_prop and key_prop != default_key_prop:
        raise ValueError(
            f"key property {key_prop!r} is not the contract key for {label} "
            f"({default_key_prop!r}, cache_projection.KEY_PROPERTY) — the resident side "
            f"reads the GRAPH by this property and the outside side compares the "
            f"RECORD's node_key, so any other property makes the two halves measure "
            f"different things and the complement is meaningless"
        )
    # ⛔ RESOLVED PER TABLE, NEVER ASSUMED FROM `point`. A `None` here means the caller
    # named none, so the label's own default applies; a label whose table carries no
    # `*_kind` column (today: `session`) has NO default and must be told one. That is a
    # USAGE error (the caller can fix it by naming a column), stated with the columns the
    # table actually has — not the `point_kind` default this used to apply to every
    # label, which made `--label Source|Event|Subject|Object|Session` fail out of the box
    # naming a column nobody passed.
    if topic_column is None:
        topic_column = TOPIC_COLUMN_BY_LABEL.get(label, "")
        if not topic_column:
            raise ValueError(
                f"the {label} table (public.{table}) carries no kind column, so the "
                f"grouping dimension has no default: name one with --topic-column from "
                f"{sorted(columns_for_table) or 'the record schema'}"
            )
    # ⛔ AND THE SPAN USES THE LABEL'S OWN TIMESTAMP. `created_at` does not exist on
    # `public.event`, so the span raised there and the whole view came back UNMEASURED
    # (exit 4) — for a table that answers "roughly when" perfectly well through
    # `started_at`. A label with no timestamp at all is refused as a USAGE error rather
    # than read as a failed measurement.
    timestamp_column = TIMESTAMP_COLUMN_BY_LABEL.get(label, "")
    if not timestamp_column or timestamp_column not in columns_for_table:
        raise ValueError(
            f"the {label} table (public.{table}) has no timestamp this view can read, "
            f"so 'roughly when' cannot be answered for it: name a label whose table "
            f"carries one, or ask this view for a different label"
        )
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
    _checked(timestamp_column, what="timestamp column")
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
    # ⛔ THE BOUNDS ARE FLAGS TOO, so they are validated HERE for the same reason as the
    # identifiers: inside the guard a `--max-keys 0` came back as UNMEASURED (exit 4,
    # "retry") instead of a usage error the caller can fix. `tools/resident.py` had a
    # second half of this defect — `args.max_keys or DEFAULT` rewrote `0` to 200,000, the
    # WIDENING direction — and both are needed for the floor to be reachable at all.
    if not isinstance(key_limit, int) or isinstance(key_limit, bool) or key_limit < 1:
        raise ValueError(f"--max-keys must be a positive integer, got {key_limit!r}")
    if not isinstance(top, int) or isinstance(top, bool) or top < 1:
        raise ValueError(f"--top must be a positive integer, got {top!r}")
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

    # ⛔ A GRAPH THAT REPORTS RESIDENTS BUT YIELDS NO KEYS IS A DISAGREEMENT BETWEEN ITS
    # OWN TWO READS, and the complement of an empty resident set is the WHOLE population.
    # That is the shape a wrong `key_prop` produced (see above), and it is also what a
    # partially-populated key property produces on its own. Either way the view cannot
    # claim a number: it is unmeasured, not "everything is outside".
    if not keys and (resident_total or 0) > 0:
        return OutsideView(
            org_id=org_id, graph_id=graph_id, label=label,
            resident=resident_total,
            complete=False,
            reason=(f"the graph reports {resident_total} resident {label} node(s) but "
                    f"its key read ({key_prop!r}) returned none — the two halves of this "
                    f"view disagree, so how much is outside cannot be measured"),
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
        span = _rows(cur, SPAN_SQL.format(table=table, tier=tier, ts=timestamp_column),
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
