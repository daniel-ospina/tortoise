"""The record write path's DECLARED-TYPE contract (#11075, epic #7879).

WHAT THIS PINS. A column's declared type has refused its producer's value
**twice**, and both times the row was DROPPED while the journal stayed intact
and `/health` read `ok: true`:

| incident | column | declared | emitted | how it surfaced |
|---|---|---|---|---|
| #9264 | `session.capture_redactions` | `jsonb` | an integer | same day |
| #10593 | `point.search_keys` | `jsonb` | the graph's flat space-joined **string** | ~2 days, `write_failed` climbing |

Neither was visible to any existing pin: the journal carried the field, the
projection placed it, replay was idempotent — every per-seam assertion passed.
The value was simply unacceptable to the column, which is a property no
per-seam assertion covers. This file asks that property directly:

    for every column the writer BINDS a Python value to:
        declared type read from the MIGRATIONS  accepts  that value

WHAT "ACCEPTS" MEANS, AND WHY IT IS NOT A TYPE TABLE OF CONVENIENCE. The value
compared is the value the statement actually carries — i.e. AFTER the writer's
own `_jsonb_or_scalar` encoding — and the predicate models what the DRIVER
sends, because that is what Postgres refuses on:

  * a Python `int`/`bool`/`float` is sent with its own type OID, so a `jsonb`
    column refuses it (`42804 DatatypeMismatch`) — this is #9264's mechanism;
  * a Python `str` is sent as `text`, and `text → jsonb` is an ASSIGNMENT CAST
    that PARSES the text, so a non-JSON string is refused (`22P02`) — this is
    #10593's mechanism. `_jsonb_or_scalar` now JSON-quotes a string bound to a
    `jsonb` column (#9548), so that arm is closed AT THE WRITER; the pin models
    the encoded value, not the raw one, or it would certify a failure that can
    no longer happen and red on a correct tree.

⚠️ THE ISSUE'S OWN RED-ARM INDICATOR IS STALE AT THIS HEAD, AND THIS FILE SAYS
SO RATHER THAN SATISFYING IT LITERALLY. #11075 asks for a test that goes RED on
a reverted `20261102000002_record_point_search_keys_text.sql` (column back to
`jsonb`, producer still emitting a string). Since #11128/#9548 the writer
JSON-quotes that string, so the reverted column would ACCEPT it and no row would
be dropped: a pin that reddened there today would be red on a tree that is
CORRECT, and its obvious "repair" — exempt the column, or revert the narrowing —
is the outage this pin exists to prevent. The discriminating arm is therefore
the one that is still live: a NON-STRING scalar bound to `jsonb` (the #9264
shape), which `_jsonb_or_scalar` passes through unencoded. Both arms are
asserted below, each for the reason that actually holds.

WHY A DERIVED CHECK AND NOT A HAND-KEPT VOCABULARY. `tests/test_9078_record_
written_by_production_path.py` used to hold `_SCHEMA_DISAGREEMENTS`, an
explicit column→error map, and it was made "gone rather than parked" once its
last entry closed (#10717 narrowed `search_keys`). That removal was right for a
hand-kept vocabulary that outlives its defect. This check is a different kind of
object: it is CLOSED OVER THE MIGRATION SET — `test_the_enumeration_accounts_
for_every_declared_column` reds on any column of any record table that is in no
named set — so it cannot go empty, cannot outlive its subject, and cannot be
satisfied by deleting its last entry. That distinction is the reason this one is
not a re-add of a removed decision.

DB-FREE. The declared types come from `supabase/migrations/`, the values from
`replay_plan` over the registry's producer-shaped corpus. No connection, no
graph, no extra dependency — so it rides `TORTOISE_TEST_CARVE_OUT=1`.

READING OF THE WRITER: `replay_plan` emits `Statement(sql, params)`; `columns`
is deliberately `None` whenever a `_SqlExpr` shifts the pairing (#10620), so the
enumeration below pairs columns to parameters from the STATEMENT TEXT (four
shapes: `INSERT … VALUES`, `INSERT … SELECT`, `UPDATE … SET`, and a `DELETE`,
which writes no column and is skipped). That is a mechanical derivation — no
column list is restated anywhere in this file.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from tests.test_record_projection import _everything_records
from tortoise import record_projection as rp

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"

#: The migrations whose UP direction NARROWS a column out of `jsonb`. Named here
#: only to be REMOVED from a copy of the tree (the mutation arms below); nothing
#: in the assertions reads them.
NARROWING_MIGRATIONS = (
    "20261022000001_record_session_capture_redactions_integer.sql",
    "20261102000002_record_point_search_keys_text.sql",
)


# ════════════════════════════════════════════════════════════════════════════
# 1. The enumeration — statement text → (table, column, bound value)
# ════════════════════════════════════════════════════════════════════════════


def _split_top_level(text: str) -> list[str]:
    """`text` split on commas that are not inside parentheses or a literal."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    quoted = False
    for char in text:
        if char == "'":
            quoted = not quoted
        if not quoted:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == "," and depth == 0:
                parts.append("".join(current))
                current = []
                continue
        current.append(char)
    if current:
        parts.append("".join(current))
    return [part.strip() for part in parts]


def _matching_paren(text: str, start: int) -> int:
    """Index of the `)` matching `text[start] == "("` (quote-aware), else -1."""
    depth = 0
    quoted = False
    for index in range(start, len(text)):
        char = text[index]
        if char == "'":
            quoted = not quoted
        elif not quoted:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return index
    return -1


_INSERT_RE = re.compile(r"INSERT INTO\s+public\.(\w+)\s*\(")
_UPDATE_RE = re.compile(r"UPDATE\s+public\.(\w+)\s+SET\s+")
_UPDATE_WHERE_RE = re.compile(r"\sWHERE\s")
_VALUES_RE = re.compile(r"\bVALUES\s*\(")
_SELECT_RE = re.compile(r"\bSELECT\s+")
_SELECT_FROM_RE = re.compile(r"\s+FROM\s+")
_SET_ITEM_RE = re.compile(r'\s*"?(\w+)"?\s*=\s*(.*)$', re.S)
_PLAIN_PARAM = ("%s", "%s::jsonb")
#: A single parameter the writer CASTS (`COALESCE(%s::timestamptz, now())`,
#: `%s::jsonb`). A Python value IS bound there -- the cast only changes how
#: Postgres parses it -- so it is judged against the DECLARED column type rather
#: than filed as a database expression. `journal_event.recorded_at` is the case
#: that matters: it carries the producer's raw `ts`, and a `ts` Postgres cannot
#: parse refuses the WHOLE statement (#11075 review P2).
_CAST_PARAM_RE = re.compile(
    r"^(?:COALESCE\(\s*)?%s::[a-z][a-z ]*(?:with time zone|without time zone)?",
    re.I,
)


def _binds_a_python_value(item: str) -> bool:
    """Does `item` bind exactly one Python value to its column?"""
    text = item.strip()
    return text in _PLAIN_PARAM or bool(_CAST_PARAM_RE.match(text))


def _bindings(sql: str) -> list[tuple[str, str, str]]:
    """`[(table, column, values_item)]` for one statement, in parameter order.

    A `values_item` is either a bare placeholder (`%s`) — a Python value is bound
    to that column — or an SQL EXPRESSION, in which case the database computes
    the value and no Python value is judged. `DELETE` statements write no
    column at all and yield nothing.
    """
    insert = _INSERT_RE.search(sql)
    if insert:
        table = insert.group(1)
        open_paren = insert.end() - 1
        close_paren = _matching_paren(sql, open_paren)
        columns = [c.strip().strip('"')
                   for c in sql[open_paren + 1:close_paren].split(",")]
        values = _VALUES_RE.search(sql, close_paren)
        if values:
            start = values.end() - 1
            items = _split_top_level(sql[start + 1:_matching_paren(sql, start)])
        else:
            select = _SELECT_RE.search(sql, close_paren)
            if select is None:
                return []
            until = _SELECT_FROM_RE.search(sql, select.end())
            items = _split_top_level(
                sql[select.end():until.start()] if until else sql[select.end():])
        if len(items) != len(columns):
            return []
        return [(table, column, item)
                for column, item in zip(columns, items, strict=True)]

    update = _UPDATE_RE.search(sql)
    if update:
        table = update.group(1)
        where = _UPDATE_WHERE_RE.search(sql, update.end())
        assigns = sql[update.end():where.start() if where else len(sql)]
        found = []
        for item in _split_top_level(assigns):
            match = _SET_ITEM_RE.match(item)
            if match:
                found.append((table, match.group(1), match.group(2)))
        return found
    return []


_TABLE_IN_STATEMENT_RE = re.compile(
    r"(?:INSERT INTO|UPDATE|DELETE FROM)\s+public\.(\w+)")


def _table_of(sql: str) -> str:
    found = _TABLE_IN_STATEMENT_RE.search(sql)
    assert found is not None, f"statement names no record table: {sql[:80]!r}"
    return found.group(1)


def _cross_check(statement: Any, authoritative: tuple[str, ...]) -> None:
    """The TEXT-derived columns must agree with the writer's own attribution.

    Both derivations exist on purpose (the writer declines to attribute a
    statement whose `_SqlExpr` shifts the pairing), and two derivations of the
    same fact are exactly how a mis-paired column gets reported on a failure
    line. Where both are available they are compared, so a drift reds here
    instead of silently mis-naming a column.
    """
    binds = _bindings(statement.sql)
    if any(not _binds_a_python_value(item) for _t, _c, item in binds):
        # A `_SqlExpr` item carries the expression's own parameters, so the
        # writer names the SAME column once per parameter (the #9956 key
        # resolution binds `node_key` seven times) while the text derivation
        # names each column once. There is no positional comparison to make;
        # this is the case the writer's attribution exists for.
        return
    derived = [column for _table, column, item in binds
               if _binds_a_python_value(item)]
    stated = [name for name in authoritative if name]
    common = min(len(derived), len(stated))
    assert derived[:common] == stated[:common], (
        "the SQL-text column derivation disagrees with the writer's own "
        f"`param_columns` for {statement.sql[:80]!r}:\n"
        f"  derived: {derived}\n  stated : {stated}"
    )


def _corpus() -> list[dict[str, Any]]:
    """The registry's one-record-per-type payloads, plus the two incident shapes.

    `_everything_records()` is the corpus that pins TOTALITY over the declared
    record types, reused here so this pin cannot drift to a hand-made fixture
    set. It is the MINIMAL payload per type, though, so it carries neither of
    the two columns whose disagreement with the schema dropped 302 rows. The two
    records added below are the PRODUCERS' own shapes, not invented values:

      * `SessionRecorded.captureRedactions` — the capture receipt's count, an
        integer (#9264's emitted value);
      * `PointAdded.point.searchKeys` — the graph's flat space-joined string
        (`projection/entities.py::flatten_search_keys_prop` is the only
        flattener, #10593's emitted value).

    A fixture that cannot be produced pins nothing; these two can.
    """
    # ⚠️ `ts` is REWRITTEN on every record: `journal_event.recorded_at` is bound
    # from `record["ts"]` (through `%s::timestamptz`), so the registry corpus's
    # placeholder `"T"` is a value Postgres REFUSES — a fixture lie the pin
    # would otherwise carry green (#11075 review P2).
    stamp = "2026-11-06T00:00:00Z"
    records = [{**record, "ts": stamp} for record in _everything_records()]
    return [*records,
        {"type": "SessionRecorded", "ts": stamp, "id": "sess-11075",
         "turn_count": 2, "is_episodic": True, "captureRedactions": 3},
        {"type": "PointAdded", "ts": stamp, "id": "p-11075",
         "point": {"id": "p-11075", "content": "c",
                   "searchKeys": "alpha beta gamma"}},
    ]


def _enumeration() -> tuple[list[tuple[str, str, Any]], list[tuple[str, str]]]:
    """`([(table, column, bound_value)], [(table, column)])` over the corpus.

    The first list is what the pin JUDGES; the second is the columns the writer
    fills from a database expression, which no Python value can be judged
    against.
    """
    judged: list[tuple[str, str, Any]] = []
    expressions: list[tuple[str, str]] = []
    for statement in rp.replay_plan(_corpus(), org_id="o", graph_id="g"):
        params = list(statement.params)
        authoritative = statement.param_columns
        if authoritative:
            # The WRITER's own attribution (`Statement.param_columns`, one entry
            # per parameter, `""` for a non-column parameter) — the source of
            # truth, pinned by `test_9548_…::test_every_statement_s_param_columns
            # _align_with_its_params`. The text derivation below is what covers
            # the builders that decline to state it (`_SqlExpr`, INSERT … SELECT,
            # the journal row), and the two are CROSS-CHECKED at the end of this
            # loop so neither can drift alone (#11075 review P3).
            for index, name in enumerate(authoritative):
                if not name or index >= len(params):
                    continue
                judged.append((_table_of(statement.sql), name, params[index]))
            _cross_check(statement, authoritative)
            continue
        offset = 0
        binds = _bindings(statement.sql)
        for table, column, item in binds:
            wanted = item.count("%s")
            chunk = params[offset:offset + wanted]
            offset += wanted
            if _binds_a_python_value(item):
                judged.append((table, column, chunk[0]))
            else:
                expressions.append((table, column))
        # A `VALUES` list is exactly the parameter sequence; an `INSERT … SELECT`
        # adds its subqueries' predicates AFTER it, and an `UPDATE` after its
        # `SET` list — both are checked for a PREFIX match, not equality.
        values_form = _VALUES_RE.search(statement.sql) is not None
        if binds and values_form and offset != len(params):
            raise AssertionError(
                f"the enumeration consumed {offset} of {len(params)} parameters "
                f"for {statement.sql[:90]!r} — the pairing is not exact, so a "
                "mismatch would be reported against the WRONG column"
            )
    return judged, expressions


# ════════════════════════════════════════════════════════════════════════════
# 2. What the declared type accepts — the driver's own behaviour, not a guess
# ════════════════════════════════════════════════════════════════════════════


def _is_json_text(value: str) -> bool:
    try:
        json.loads(value)
    except (TypeError, ValueError):
        return False
    return True


_ISO_LIKE = re.compile(
    r"^\d{4}-\d{2}-\d{2}"
    r"([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}(:?\d{2})?)?)?$"
)


def _accepts(declared: str, value: Any) -> bool:
    """Would Postgres accept `value` (as the driver sends it) in `declared`?

    Deliberately NOT permissive, but not over-strict either: a FALSE red is the
    worst outcome for this pin, because its obvious repair is "exempt the
    column, or revert the narrowing" — the outage the pin exists to prevent
    (#11075). So each arm names a driver/Postgres behaviour and admits the
    Python shapes a real producer can bind; anything that falls through is
    REFUSED, so a column of a type this predicate does not know cannot pass by
    default (the vocabulary closure test is what stops that from being silent).
    """
    from datetime import date, datetime
    from decimal import Decimal

    if value is None:  # a NULL is acceptable to every column, array or not
        return True
    declared = declared.lower()
    if declared.endswith("[]"):
        return isinstance(value, (list, tuple))
    if declared in ("text", "varchar", "character varying", "uuid"):
        return isinstance(value, str)
    if declared == "date":
        return isinstance(value, (str, date)) and not isinstance(value, datetime)
    if declared in ("jsonb", "json"):
        if isinstance(value, str):
            # sent as `text`; `text → jsonb` PARSES it (#10593's 22P02)
            return _is_json_text(value)
        # A bool/number is sent with its own type OID and there is no
        # bool/int/float → jsonb implicit cast (#9264's 42804); a `dict`/`list`
        # is JSON-serialised by the writer before it is bound.
        return isinstance(value, (dict, list))
    if declared in ("integer", "bigint", "smallint", "serial", "bigserial",
                    "smallserial"):
        return isinstance(value, int) and not isinstance(value, bool)
    if declared == "boolean":
        return isinstance(value, bool)
    if declared in ("double precision", "real", "numeric", "decimal"):
        return (isinstance(value, (int, float, Decimal))
                and not isinstance(value, bool))
    if declared in ("timestamptz", "timestamp", "timestamp with time zone",
                    "timestamp without time zone"):
        if isinstance(value, datetime):
            return True
        # A str is PARSED by the cast: a non-date string is `22007`, so the
        # shape is checked rather than assumed (this is what reds on the
        # corpus's placeholder `"ts": "T"`).
        return isinstance(value, str) and bool(_ISO_LIKE.match(value))
    if declared == "bytea":
        return isinstance(value, (bytes, bytearray))
    return False


def _as_the_writer_sends(declared_type: str, value: Any) -> Any:
    """`value` after the writer's `jsonb` encoding rule, under `declared_type`.

    The bound parameters come from `replay_plan`, which encoded them against the
    LIVE schema. A mutation arm judges the same corpus against a DIFFERENT
    schema, so the encoding has to be re-applied under THAT one — otherwise the
    mutation silently also changes the writer's behaviour, and the arm would red
    on a difference the mutation never made. Idempotent for already-valid JSON
    text (#9548's `json.dumps` arm), so the live run is unchanged.
    """
    if declared_type == "jsonb" and isinstance(value, str) and not _is_json_text(value):
        return json.dumps(value)
    return value


def _mismatches(declared: dict[str, dict[str, str]]) -> list[str]:
    judged, _ = _enumeration()
    found = []
    for table, column, value in judged:
        if table not in declared or column not in declared[table]:
            continue
        type_name = declared[table][column]
        sent = _as_the_writer_sends(type_name, value)
        if not _accepts(type_name, sent):
            found.append(
                f"{table}.{column}: declared {type_name!r}, "
                f"writer binds {type(sent).__name__} {sent!r}"
            )
    return found


# ════════════════════════════════════════════════════════════════════════════
# 3. The gate
# ════════════════════════════════════════════════════════════════════════════


def test_every_bound_value_is_acceptable_to_its_declared_column():
    """The pin. A value the column's declared type cannot hold reds HERE."""
    declared = rp.column_types()
    assert declared, "the migrations yielded no declared types"
    mismatches = _mismatches(declared)
    assert mismatches == [], (
        "the record's write path binds a value its column's DECLARED type "
        "cannot accept — this is the #9264 / #10593 row-drop class:\n  "
        + "\n  ".join(mismatches)
    )


def test_the_pin_judges_a_real_population():
    """A vacuous pin is a green pin. The enumerated population is asserted.

    Without this, a refactor that made `_bindings` return `[]` (or that emptied
    the corpus) would leave the gate above passing over NOTHING — the failure
    mode every one of these pins has to refuse.
    """
    declared = rp.column_types()
    judged, _expressions = _enumeration()
    assert len(judged) >= 80, f"only {len(judged)} bound values were judged"
    assert len({table for table, _c, _v in judged}) >= 9
    for table, column, _value in judged:
        assert column in declared.get(table, {}), (
            f"{table}.{column} is written by the projection and is in NO "
            "migration of a record table"
        )
    # Both incident columns are in the JUDGED population — the pin is not
    # pointed at a corner of the schema where neither incident lived.
    columns = {(table, column) for table, column, _value in judged}
    assert ("session", "capture_redactions") in columns, (
        "the #9264 column is not judged — the pin says nothing about the shape "
        "that dropped rows"
    )
    assert ("point", "search_keys") in columns, (
        "the #10593 column is not judged — the pin says nothing about the shape "
        "that dropped rows"
    )


# ════════════════════════════════════════════════════════════════════════════
# 4. Closure over the migration set — a new column with no expectation reds
# ════════════════════════════════════════════════════════════════════════════

#: Declared columns the corpus's record payloads never bind a Python value to,
#: so this pin makes NO claim about them. It is not an exemption list and it
#: carries no per-column escape: every entry is a column of a record table that
#: the registry's minimal payloads do not populate, and the assertion below is
#: EXACT in both directions, so
#:   * a column ADDED to a migration lands in none of the three sets → RED
#:     (the closure property: a third instance cannot ship silently),
#:   * an entry that the corpus DOES judge → RED (the list cannot rot into a
#:     stale exemption),
#:   * an entry for a column that no migration declares → RED.
#: ⚠️ What the gap means, stated plainly rather than implied: a producer that
#: sends a wrong-typed value for one of these columns is NOT caught here. The
#: honest fix is a corpus record that carries the property (a producer's own
#: payload, not a value invented here — an invented value proves only that the
#: predicate agrees with itself).
NOT_BOUND_BY_A_PYTHON_VALUE: frozenset[str] = frozenset({
    # ── event ───────────────────────────────────────────────────────
    "event.captured_at",
    "event.classification_level",
    "event.content",
    "event.embedding",
    "event.embedding_repair_failed_at",
    "event.ended_at",
    "event.event_status",
    "event.format",
    "event.is_episodic",
    "event.is_operator",
    "event.keywords",
    "event.mined_at",
    "event.mined_hash",
    "event.name",
    "event.object",
    "event.parent_event",
    "event.participants",
    "event.session_id",
    "event.source_file",
    "event.source_ref",
    "event.started_at",
    "event.story_arc",
    "event.subject",
    "event.summary",
    "event.updated_at",
    # ── journal_event ───────────────────────────────────────────────
    "journal_event.node_key",
    "journal_event.node_label",
    # ── node_identity ───────────────────────────────────────────────
    "node_identity.created_at",
    "node_identity.key_property",
    # ── object ──────────────────────────────────────────────────────
    "object.canonical_name",
    "object.complexity",
    "object.created_at",
    "object.issue_number",
    "object.repo",
    "object.routed_product",
    "object.routed_role",
    "object.routed_team",
    "object.title",
    "object.url",
    "object.ux_rating",
    # ── point ───────────────────────────────────────────────────────
    "point.amount_minor",
    "point.annotator_consistency",
    "point.annotator_directness",
    "point.annotator_precision",
    "point.as_of",
    "point.assessor",
    "point.authored_by",
    "point.baseline_alpha",
    "point.baseline_beta",
    "point.baseline_source",
    "point.currency",
    "point.dedup_candidate",
    "point.dedup_method",
    "point.dedup_reviewed",
    "point.dedup_similarity",
    "point.dedup_target_id",
    "point.direction",
    "point.embedding_verbatim",
    "point.event_id",
    "point.extracted_from",
    "point.goal_state",
    "point.grounding",
    "point.ingested_at",
    "point.label",
    "point.lme_question_id",
    "point.minor_unit_exponent",
    "point.mitigation_strength",
    "point.promoted_at",
    "point.provenance_source",
    "point.quote",
    "point.reviewed",
    "point.source_date",
    "point.source_harness",
    "point.source_session",
    "point.source_turn_id",
    "point.source_version_transit",
    "point.span_end",
    "point.span_start",
    "point.tags",
    "point.target_source",
    "point.temporal_candidate",
    "point.temporal_replacement",
    "point.temporal_reviewed",
    "point.temporal_target_id",
    "point.temporal_target_ids",
    "point.updated_at",
    "point.valid_from",
    "point.verbatim",
    "point.when",
    # ── relation ────────────────────────────────────────────────────
    "relation.batch_id",
    "relation.confidence",
    "relation.direction",
    "relation.id",
    "relation.label",
    "relation.source_version",
    "relation.weight",
    # ── session ─────────────────────────────────────────────────────
    "session.actor_user_id",
    "session.capture_extractor",
    "session.capture_lane",
    "session.capture_ok",
    "session.client_captured_at",
    "session.client_captured_at_source",
    "session.commit_count",
    "session.created_at",
    "session.draft_count",
    "session.entity_links_attempted",
    "session.entity_links_created",
    "session.harness",
    "session.machine_id",
    "session.model",
    "session.updated_at",
    "session.value_nodes_created",
    "session.value_nodes_held",
    # ── session_narrative ───────────────────────────────────────────
    "session_narrative.capture_redactions",
    # ── source ──────────────────────────────────────────────────────
    "source._search_text",
    "source.about_entities",
    "source.authored_by",
    "source.captured_at",
    "source.created_at",
    "source.credibility_tier",
    "source.document_kind",
    "source.document_knowledge_domain",
    "source.domain",
    "source.embedding",
    "source.event_id",
    "source.external_id",
    "source.format",
    "source.governing_agreement",
    "source.ingested_at",
    "source.is_episodic",
    "source.managed_by",
    "source.name",
    "source.needs_extraction",
    "source.owned_by",
    "source.provenance_spans",
    "source.raw_state",
    "source.raw_state_at",
    "source.session_id",
    "source.source_date",
    "source.source_path",
    "source.status",
    "source.story_arc",
    "source.summary",
    "source.team",
    "source.title",
    "source.topics",
    "source.updated_at",
    "source.url_aliases",
    "source.version",
    # ── subject ─────────────────────────────────────────────────────
    "subject.created_at",
    "subject.superseded_at",
    "subject.superseded_by",
})


def test_the_enumeration_accounts_for_every_declared_column():
    """Closure. Every declared column is judged, an expression, or named above."""
    declared = rp.column_types()
    judged, expressions = _enumeration()
    judged_keys = {f"{t}.{c}" for t, c, _v in judged}
    expression_keys = {f"{t}.{c}" for t, c in expressions}
    accounted = judged_keys | expression_keys | set(NOT_BOUND_BY_A_PYTHON_VALUE)
    every = {f"{table}.{column}"
             for table, columns in declared.items() for column in columns}

    unaccounted = sorted(every - accounted)
    assert unaccounted == [], (
        "these declared columns are in NO named set — a column the writer can "
        "fill, or a column nothing knows about, must be enumerated before it "
        "can ship:\n  " + "\n  ".join(unaccounted)
        + "\nAdd the record that carries it to the corpus, or name it in "
        "NOT_BOUND_BY_A_PYTHON_VALUE with the reason."
    )
    stale = sorted(set(NOT_BOUND_BY_A_PYTHON_VALUE) - every)
    assert stale == [], f"named as not-bound but declared nowhere: {stale}"
    overlapping = sorted(set(NOT_BOUND_BY_A_PYTHON_VALUE)
                         & (judged_keys | expression_keys))
    assert overlapping == [], (
        "these columns are judged by the corpus AND named as not-bound — the "
        f"list has gone stale: {overlapping}"
    )


#: Words that begin a TABLE CONSTRAINT inside a `CREATE TABLE` body, not a column.
_CONSTRAINT_LEADERS = ("constraint", "primary", "unique", "check", "foreign",
                       "exclude", "like")


def test_the_vocabulary_captures_every_column_the_migrations_declare():
    """Closure over the MIGRATION TEXT, not over the reader's own output.

    The closure test above derives "every" from `column_types()`, so on its own
    it cannot see a column of a type OUTSIDE `_DECLARED_TYPE_ALTERNATION` -- such
    a column would be skipped by the reader and therefore absent from both the
    accounting and the unexplained set. This arm scans the `CREATE TABLE` bodies
    with a deliberately DUMB reader (name + any first token) and requires every
    column it finds to be one the vocabulary captured.
    """
    declared = rp.column_types()
    loose = re.compile(r'^\s*"?([a-z_][a-z0-9_]*)"?\s+\S', re.M)
    missed: list[str] = []
    for name in rp.RECORD_MIGRATIONS:
        path = MIGRATIONS / name
        if not path.exists():
            continue
        sql = rp._strip_sql_comments(path.read_text(encoding="utf-8"))
        for table, body in rp._TABLE_RE.findall(sql):
            if table not in rp.THE_RECORD_TABLES:
                continue
            for found in loose.finditer(body):
                column = found.group(1)
                if column in _CONSTRAINT_LEADERS:
                    continue
                if column not in declared.get(table, {}):
                    missed.append(f"{table}.{column} ({name})")
    assert missed == [], (
        "these columns are DECLARED in a migration and were not captured by "
        "`_DECLARED_TYPE_ALTERNATION`, so they are outside every pin -- widen "
        "the vocabulary:\n  " + "\n  ".join(missed)
    )


def test_comment_stripping_is_what_keeps_a_DOWN_direction_out_of_the_read():
    """The stripper is load-bearing, and this is the input that proves it.

    Both narrowing migrations record their reversal in a trailing comment. A
    reader that parses the raw file sees the UP narrowing and the DOWN reversal
    and keeps whichever comes LAST -- the DOWN -- i.e. the superseded type.
    """
    sql = (
        "ALTER TABLE public.point\n"
        "    ALTER COLUMN search_keys TYPE text;\n"
        "/*\n"
        "  ALTER TABLE public.point\n"
        "      ALTER COLUMN search_keys TYPE jsonb;\n"
        "*/\n"
        "DO $$ BEGIN -- not a comment inside the body\n"
        "  EXECUTE 'x'; END $$;\n"
    )
    stripped = rp._strip_sql_comments(sql)
    assert "TYPE jsonb" not in stripped, stripped
    assert "TYPE text" in stripped
    # A `$$`-quoted body is kept VERBATIM, because that is where both narrowing
    # migrations put the `ALTER COLUMN ... TYPE` the reader must see: a stripper
    # that edited inside a dollar-quoted block would be the thing that hid it.
    # (A commented-out ALTER there cannot be misread anyway: its `--` prefix
    # breaks the `\s+` joins the statement regexes need.)
    assert "not a comment inside the body" in stripped
    assert "EXECUTE 'x'; END $$;" in stripped
    # An escaped quote does not desynchronise the scanner: everything after it
    # still parses, which is the property that keeps a later DOWN comment out.
    escaped = "COMMENT ON COLUMN public.t.c IS E'it\\'s';\n-- ALTER COLUMN x TYPE jsonb\n"
    assert "ALTER COLUMN x TYPE jsonb" not in rp._strip_sql_comments(escaped)


def test_the_reader_sees_the_two_narrowings_that_broke_production():
    """The anti-false-red arm: the pin must read the NARROWED type, not the base.

    A reader that only understood `CREATE TABLE` reports `jsonb` for both of
    these, and the only ways to make it green — exempt the column, or revert the
    narrowing — re-create the outage (#11075).
    """
    declared = rp.column_types()
    assert declared["point"]["search_keys"] == "text"
    assert declared["session"]["capture_redactions"] == "integer"
    # …and the encoding rule reads the SAME map, so the two cannot disagree.
    assert "search_keys" not in rp.jsonb_columns()
    assert "capture_redactions" not in rp.jsonb_columns()


# ════════════════════════════════════════════════════════════════════════════
# 5. The mutation arms — the pin is not vacuous
# ════════════════════════════════════════════════════════════════════════════


def _tree_without_narrowings(tmp_path: Path) -> Path:
    """A copy of the migrations with the narrowing files REMOVED.

    The copy keeps every `CREATE TABLE` (so the schema is complete) and drops
    only the two forward narrowings, which reproduces the pre-fix schema — the
    state each incident shipped from. `column_types()` skips a missing file, so
    the base declaration is what the reader sees.
    """
    copy = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS, copy)
    for name in NARROWING_MIGRATIONS:
        (copy / name).unlink()
    return copy


def test_the_9264_arm_reds_when_the_capture_column_is_jsonb_again(tmp_path):
    """`jsonb` ← integer (`42804`): the pin finds it. #9264's live shape.

    ⚠️ This is the arm that still CAN drop rows: `_jsonb_or_scalar` encodes a
    `dict`/`list`/`str`, but passes a numeric scalar through untouched, so an
    integer bound to a `jsonb` column is refused by Postgres exactly as it was
    in #9264. The value is the corpus's producer value (`captureRedactions: 3`),
    so what reds is the real bound parameter, not a value built for the test.
    """
    reverted = rp.column_types(_tree_without_narrowings(tmp_path))
    assert reverted["session"]["capture_redactions"] == "jsonb", (
        "the mutation did not take — the fixture no longer reproduces #9264"
    )
    mismatches = _mismatches(reverted)
    assert any(m.startswith("session.capture_redactions: declared 'jsonb'")
               for m in mismatches), (
        "the pin did NOT red on a `jsonb` column fed an integer — it is vacuous "
        f"for the #9264 shape. reported: {mismatches}"
    )
    # …and the CURRENT tree is green on the same value, so the arm discriminates
    # on the DECLARED TYPE rather than on the corpus.
    assert not any(m.startswith("session.capture_redactions: ")
                   for m in _mismatches(rp.column_types()))


def test_the_10593_arm_is_CLOSED_AT_THE_WRITER_and_that_is_pinned(tmp_path):
    """`jsonb` ← the flat space-joined string: no longer a drop, on purpose.

    #11075 asks for this arm to red on a reverted `search_keys` narrowing. It
    does not, and it must not: since #9548 the writer JSON-quotes a string bound
    to a `jsonb` column, so the reverted column would ACCEPT the producer's
    value. What is asserted instead is the honest pair:

      * the RAW string would be refused (`22P02`) — the incident's mechanism,
        which is why the column was narrowed in the first place;
      * the ENCODED value the writer now sends is accepted — so a revert of the
        narrowing is not a row-drop, and a pin that reddened there would be red
        on a correct tree.
    """
    raw = "the session capture redactions"
    assert not _accepts("jsonb", raw), (
        "a non-JSON string must be refused by a jsonb column (22P02) — that is "
        "#10593's mechanism"
    )
    # `payload` is a real `jsonb` column (`journal_event.payload`), so this is
    # the writer's own encoding arm and not a name invented for the test.
    assert "payload" in rp.jsonb_columns()
    encoded = rp._jsonb_or_scalar("payload", raw)
    assert isinstance(encoded, str) and _is_json_text(encoded)
    assert _accepts("jsonb", encoded), (
        "the writer's own encoding must be acceptable to a jsonb column; if "
        "this fails the ENCODING is gone and a reverted narrowing drops rows "
        "again"
    )
    # The stated indicator, measured: with the narrowing reverted, the pin does
    # NOT red for `search_keys` — because the writer's encoding rescues it.
    reverted = rp.column_types(_tree_without_narrowings(tmp_path))
    assert reverted["point"]["search_keys"] == "jsonb"
    assert not any(m.startswith("point.search_keys: ")
                   for m in _mismatches(reverted)), (
        "a reverted `search_keys` narrowing must NOT red while the writer "
        "JSON-quotes the producer's string"
    )
    # `journal_event.recorded_at` is bound from the producer's `ts` through
    # `COALESCE(%s::timestamptz, now())` -- a Python value the pin judges, and a
    # `ts` Postgres cannot parse refuses the whole statement.
    judged, _expressions = _enumeration()
    assert ("journal_event", "recorded_at") in {
        (table, column) for table, column, _value in judged
    }, "the journal's `recorded_at` is not judged -- a bad `ts` would go unseen"
    assert not _accepts("timestamptz", "T")
    assert _accepts("timestamptz", "2026-11-06T00:00:00Z")
    # And the `dict`/`list` arm, unchanged by #9548.
    assert _accepts("jsonb", rp._jsonb_or_scalar("props", {"a": 1}))
    # (`'3'` bound to an `integer` column IS accepted by Postgres — an untyped
    # literal is coerced by the assigned column type — so this predicate must
    # not claim otherwise; the refusal class is the TYPED parameter OID.)
    assert not _accepts("jsonb", 3), (
        "an int carries a numeric OID, and there is no int → jsonb cast (#9264)"
    )


def test_a_column_of_an_unknown_type_is_not_silently_skipped():
    """The vocabulary widening, asserted from the outside.

    `_DECLARED_TYPE_ALTERNATION` is the ONE list every reader matches through. A
    type missing from it is skipped by `column_types()` — the column joins the
    schema outside every pin with nothing red — so the vocabulary must NAME the
    types PostgreSQL's record schema actually uses and refuses to match a bare
    unknown word.
    """
    for named in ("numeric", "uuid", "date", "smallint", "real", "json",
                  "varchar", "character varying", "timestamp",
                  "timestamp with time zone", "bytea", "jsonb", "text", "integer"):
        assert re.fullmatch(rp._DECLARED_TYPE_ALTERNATION, named), (
            f"{named!r} is outside the declared-type vocabulary, so a column "
            "of that type is invisible to every reader"
        )
    for unknown in ("geography", "citext", "int4range"):
        assert not re.fullmatch(rp._DECLARED_TYPE_ALTERNATION, unknown)
    # The array suffix is part of a column's declared TYPE (the three readers
    # append it), so `text[]` must be readable as an ARRAY, not as `text`.
    assert rp._COLUMN_TYPE_RE.search("    tags text[] NOT NULL,").group(3) == "text[]"
