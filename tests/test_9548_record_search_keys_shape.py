"""#9548 — the record DROPPED every Point carrying `search_keys`.

THE DEFECT. At #9548's head `public.point.search_keys` was `jsonb` (the
narrowing that retired that premise is item 1 below). The graph — and therefore
every `PointAdded`/`PointPromoted` journal snapshot, which carries the NODE's own
value — stores the **space-joined STRING** FalkorDB's Point FULLTEXT index needs
(`projection/entities.py::flatten_search_keys_prop`; a list-valued property is
never indexed, #5482). `record_projection._jsonb_or_scalar` serialised only
`dict`/`list` and passed a `str` through as TEXT, so Postgres read it as raw JSON
and refused EVERY such record with `22P02`:

    psycopg2.errors.InvalidTextRepresentation: invalid input syntax for type json
    DETAIL:  Token "err4032" is invalid.

`record_writer.write_live_record` counted one `write_failed` and dropped the row.
Measured live at #9548: `{"armed":true,"ok":true,
"record_write_failures":{"write_failed":302}}` — 302 Points silently absent from
the record while the receipt said `ok: true`. Nothing in the report named a
column: the seam logged `type(exc).__name__` and threw `str(exc)` away.

WHAT IS PINNED HERE, and why each half is needed.

1. **The ENCODING half is pinned where it still applies, and the COLUMN half has
   since superseded the motivating case.** #9548 fixed the encoder — for a
   `jsonb` column the projection emits a JSON *document*, so the bound value is
   `json.loads`-able and decodes back to the graph's own string; before the fix
   that parameter was the bare `err4032 release deploy`, whose `json.loads`
   raises, i.e. exactly the input Postgres refused with `22P02`.
   ⚠️ **THAT IS NO LONGER WHAT HAPPENS TO `search_keys`.** #10620's migration
   `20261102000002_record_point_search_keys_text.sql` narrowed
   `point.search_keys` to `text`, so on the landed schema the bound value is the
   graph's own string VERBATIM — a JSON-quoting encoder there stores the quotes
   as DATA, with no error and no counter. Both halves are asserted on the SAME
   statement by
   `test_the_bound_search_keys_value_is_the_graph_s_string_verbatim` (its
   docstring carries the full inversion): the verbatim string for this column,
   and a JSON document for every parameter whose column the schema still
   declares `jsonb`.
2. **The NEW shape APPLIES and round-trips through the record's OWN reader.** A
   green write is not enough: the record is a CACHE OF THE GRAPH
   (`cache_projection.node_from_row` reads the column straight back onto the
   node), so the read side must yield what the graph can index. Storing the
   array instead also APPLIES — and reads back a `list`, the array-valued
   `search_keys` the FULLTEXT index cannot match. That arm is pinned as a
   REFUSAL, so "make the write green by storing the array" cannot pass by
   accident.
3. **A failure names its column.** `RecordStatementError` carries the failing
   statement, its first 120 characters, and each bound column's Python type, and
   `RecordWriter.write` no longer discards `str(exc)`.

The DB legs need a Postgres carrying the record migrations (`TEST_RECORD_DB_URI`,
the `#9551` job). Unset ⇒ they SKIP, never pass. The DB-free legs are the ones
that red on the pre-fix code on the PR path.
"""
from __future__ import annotations

import json
import re
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tortoise import monitoring, record_projection, record_writer  # noqa: E402

RECORD_DB_ENV = "TEST_RECORD_DB_URI"

#: The value the GRAPH holds after `flatten_search_keys_prop`: one space-joined
#: string. Every arm below is built from it, so no arm can accidentally test a
#: shape the product never writes.
GRAPH_KEYS = "err4032 release deploy"


def _record(tenant, *, search_keys, content="the deploy released err4032"):
    point = {
        "id": f"pt_{uuid.uuid4().hex[:16]}",
        "pointKind": "event",
        "content": content,
        "status": "live",
        "confidence": 0.8,
        "c_cal": 0.7,
    }
    if search_keys is not None:
        point["search_keys"] = search_keys
    return {"type": "PointAdded", "point": point,
            "ts": "2026-10-31T00:00:00Z"}


def _search_keys_statement(plan):
    """The ONE statement of a Point plan that binds `search_keys`.

    Found through `Statement.param_columns` (the same alignment the failure
    attribution uses), never by scanning `params` for a string that looks right:
    a scan would pass on the wrong parameter. `plan[0]` is the `node_identity`
    FK parent, which binds no `search_keys` at all.
    """
    found = [s for s in plan if "search_keys" in s.param_columns]
    assert len(found) == 1, (
        f"expected one statement binding `search_keys`, got {len(found)}: "
        f"{[s.sql[:120] for s in found]}")
    return found[0]


def _search_keys_param(plan) -> object:
    """The value the plan binds to the `search_keys` column."""
    statement = _search_keys_statement(plan)
    return statement.params[list(statement.param_columns).index("search_keys")]


def _plan_record(record, tenant):
    return record_projection.replay_plan(
        [record], org_id=tenant[0], graph_id=tenant[1], position=False)


def _tenant():
    token = uuid.uuid4().hex[:8]
    return (f"org-9548-{token}", f"graph-9548-{token}")


class _DriverShapeError(Exception):
    """psycopg2's ``str()``: the protocol's fields CONCATENATED (#11121).

    Not a stand-in for a driver's behaviour we are guessing at — it is the
    documented shape. The fields that quote TEXT FROM THE ERROR are the primary
    ``M`` ("accurate but terse (typically one line)") and the OPTIONAL
    ``D``/``W``, which quote the DATA (for `22P02` the refused token; for a
    `CHECK` violation the whole row). libpq builds ``str(exc)`` from those AND
    from whatever else the server sent — a ``HINT``, an internal query, the
    ``LINE n:`` position — and psycopg2 concatenates all of it, while
    ``diag.message_primary`` carries ``M`` alone.

    `_primary_message`/`_driver_fault` exist to keep only the latter, and
    `record_writer._bounded_cause` can only apply the same narrowing to an
    exception that carries ``diag``/``pgcode`` — a `RecordStatementError` does
    not, which is why the encoder of the line is the last place that can
    withhold. This class is that shape with the withheld value made explicit.
    """

    pgcode = "22P02"

    class _Diag:
        message_primary = "invalid input syntax for type json"

    diag = _Diag()

    def __init__(self) -> None:
        # ⚠️ `GRAPH_KEYS` is deliberately the token in `DETAIL`: it is the SAME
        # value `_column_pair` withholds by name-and-length, so one assertion
        # can prove the withhold true of the whole line, not of one half.
        super().__init__(
            "invalid input syntax for type json\n"
            f'DETAIL:  Token "{GRAPH_KEYS}" is invalid.\n'
            f"CONTEXT:  JSON data, line 1: {GRAPH_KEYS}")


# ════════════════════════════════════════════════════════════════════════════
# 1. The OLD shape — RED on the pre-fix code, with no database at all
# ════════════════════════════════════════════════════════════════════════════


def test_the_bound_search_keys_value_is_the_graph_s_string_verbatim():
    """`public.point.search_keys` is TEXT in the landed schema — verbatim (#10620).

    ⚠️ THIS TEST'S PREMISE INVERTED when the batch integrated current `main`.
    #9548 fixed this column on the ENCODING side (`_jsonb_or_scalar` serialised
    the graph's flat string into a JSON document); #10620 fixed it on the COLUMN
    side (migration `20261102000002_record_point_search_keys_text.sql` narrows
    `point.search_keys` to `text`). Both landed, so the merged tree carries the
    narrowing and the bound value is now the graph's own string — a JSON-quoting
    encoder on a `text` column would store the quotes as DATA.

    The encoding rule is not dead: it still governs every column that IS
    `jsonb`, and that half is asserted below on the SAME statement. The old
    body — `json.loads(bound)` — is retained as the second arm because it is
    the acceptance test Postgres applies to a `jsonb` input.

    ⛔ WHAT REDS HERE: the loop below fails if any parameter bound to a column
    the schema still declares `jsonb` is not a JSON document — the acceptance
    test Postgres applies to a `jsonb` input.

    ⚠️ THAT LOOP IS A SHAPE GUARD ON THIS STATEMENT, NOT THE #9548 PIN. Because
    `search_keys` is `text` since #10620, the only `jsonb` parameter left here
    is `props`, whose `{}` parses EITHER WAY — so the PRE-FIX encoder satisfies
    it. The rule's own discriminating pin is
    `test_the_encoder_still_JSON_quotes_a_bare_string_bound_to_a_jsonb_column`
    below (#10726 review, P2: measured at 171 passed with the pre-fix
    `_jsonb_or_scalar` restored, i.e. this file could not see the defect it was
    written for).
    """
    from tortoise import record_projection as rp

    tenant = _tenant()
    plan = _plan_record(_record(tenant, search_keys=GRAPH_KEYS), tenant)
    bound = _search_keys_param(plan)
    jsonb = rp.jsonb_columns()
    assert "search_keys" not in jsonb, (
        "public.point.search_keys is `text` since "
        "20261102000002_record_point_search_keys_text.sql (#10620) — if it is "
        "back in `jsonb_columns()` the encoding rule starts JSON-quoting a "
        "text column, which stores the quotes as data")
    assert bound == GRAPH_KEYS, (
        f"a `text` column takes the graph's own flat string VERBATIM "
        f"(got {bound!r})")

    # …and the jsonb half of the rule still applies, on the same statement:
    # every parameter bound to a column the schema declares `jsonb` is a JSON
    # document Postgres can parse. This is the #9548 acceptance test, kept
    # alive on the columns that still need it.
    statement = _search_keys_statement(plan)
    for offset, column in enumerate(statement.param_columns):
        if column not in jsonb:
            continue
        value = statement.params[offset]
        if not isinstance(value, str):
            continue
        try:
            json.loads(value)
        except json.JSONDecodeError as exc:
            pytest.fail(
                f"the value bound to `{column}` is not a JSON document "
                f"({value!r}: {exc}) — a `jsonb` column refuses it with 22P02 "
                "and the seam drops the row (#9548)")


def test_the_encoder_still_JSON_quotes_a_bare_string_bound_to_a_jsonb_column():
    """The #9548 ENCODER rule — a pin that actually REDS without it.

    ⛔ WHY THIS EXISTS SEPARATELY (#10726 review, P2). `_jsonb_or_scalar`'s
    `str` -> `json.dumps` arm IS the #9548 fix, and until this test it had NO
    pin that failed when the arm was removed. The reason: `point.search_keys`
    is `text` since #10620, so the only `jsonb` parameter left on THIS file's
    statement is `props`, whose `{}` parses either way — the pre-fix body
    satisfies the shape loop above. Measured, not argued: restoring
    (`json.dumps(value) if isinstance(value, (dict, list)) else value`) and
    running this file plus `tests/test_record_projection.py` gave
    **171 passed, exit 0**.

    `public.journal_event.payload` is still declared `jsonb`, so the rule is
    exercised where it still governs: a bare graph string bound there must come
    back as a JSON DOCUMENT that `json.loads` returns to the graph's own flat
    `str`. Pre-fix the same call returns the bare string, which `json.loads`
    refuses with a `JSONDecodeError` — exactly the 22P02 the seam reports as a
    `write_failed` row drop.

    ⚠️ THE TABLE IS `journal_event`, NOT `event` (#10726 review P3, corrected).
    `payload jsonb NOT NULL` is declared on `public.journal_event`
    (`20261012000001_record_event_stream.sql:89`); `public.event` — opened at
    `:217` of that file — has NO `payload` column at all, so the earlier
    wordings above and in the assert message named a column on a table that
    does not carry it and would have sent the next reader to the wrong file.
    ⛔ The attribution is guarded rather than trusted:
    `test_every_table_column_attribution_in_this_file_resolves_in_the_migrations`
    reds if any `public.<table>.<column>` this file names is not a real column.
    """
    from tortoise import record_projection as rp

    assert "payload" in rp.jsonb_columns(), (
        "`public.journal_event.payload` is the column this pin uses because it "
        "is STILL `jsonb`. If the schema narrowed it too, pick another live "
        "`jsonb` column rather than deleting the pin — the encoder rule is "
        "this file's subject")

    encoded = rp._jsonb_or_scalar("payload", GRAPH_KEYS)
    assert encoded != GRAPH_KEYS, (
        "a bare string bound to a `jsonb` column was passed through UNENCODED "
        "— that is the #9548 defect: Postgres reads it as raw JSON text and "
        f"refuses the row with 22P02 (got {encoded!r})")
    assert json.loads(encoded) == GRAPH_KEYS, (
        "the encoded value must read back as the graph's own flat string — a "
        "JSON ARRAY would read back as a `list`, which is the array-valued "
        f"`search_keys` the FTS index cannot match (got {encoded!r})")


def test_the_read_side_yields_the_graph_s_string_not_an_array():
    """The SHAPE is a JSON string, never an array — and the why, pinned.

    An array in the column also APPLIES (ARM C of the #9548 reproduction), so a
    write-side test alone cannot tell the two shapes apart. What separates them
    is the reader: `cache_projection.node_from_row` projects the column straight
    onto the rebuilt node, and a JSON array becomes a Python `list` — the
    array-valued `search_keys` FalkorDB's Point FULLTEXT index cannot match
    (#5482), reachable only through `_fix_point_search_keys`'s repair pass.

    WHAT THIS PROVES, NARROWLY: that the RECORD'S OWN READER hands the two
    encodings to the rebuilt node as different Python values (`node_from_row` →
    `_props_from_row` → `_scalar`), which is the fact that makes the shape choice
    matter — through the real reader, not a stand-in, so it reds if
    `_props_from_row` ever starts coercing.

    It does NOT prove the write path refuses an array: nothing refuses one — a
    list `search_keys` on the journal would encode to a JSON array, and the
    `sdk.py` capture lane that can carry a list into the journal has no
    `flatten_search_keys_prop` call. That arm is a SEPARATE open question about
    the producer, not this defect, and is named as remaining scope on #9548
    rather than papered over here.
    """
    from tortoise.cache_projection import node_from_row

    tenant = _tenant()
    record = _record(tenant, search_keys=GRAPH_KEYS)
    bound = _search_keys_param(_plan_record(record, tenant))
    # `text` since #10620's `20261102000002`: the reader gets the graph's flat
    # STRING, not a JSON document and not a token array. `search_keys` may not
    # be read as an array by anything downstream — FalkorDB's Point FULLTEXT
    # index cannot match one (#5482).
    assert isinstance(bound, str) and bound == GRAPH_KEYS

    def _rebuilt(search_keys):
        # The row shape `read_record_view` hands `node_from_row`: the key column
        # plus the columns its placement table authorises.
        return node_from_row("Point", {
            "node_key": "pt_1", "id": "pt_1", "content": "c",
            "search_keys": search_keys}).props["search_keys"]

    assert _rebuilt(bound) == GRAPH_KEYS, (
        "the record's reader must hand the rebuild the graph's flat STRING")
    assert _rebuilt(["err4032"]) == ["err4032"], (
        "a JSON array reads back as a Python list — the array-valued "
        "`search_keys` the Point FULLTEXT index cannot match, which is why the "
        "column must hold a JSON STRING and not the token array")


def test_every_statement_s_param_columns_align_with_its_params():
    """`param_columns` must be a 1:1 map over `params` — the shapes that can break.

    The attribution is only worth having if it is TRUE, and it is aligned by
    construction in two builders — where a `_SqlExpr` can expand one COLUMN into
    several parameters. Seeding the key with a single `node_key` misaligned every
    later name on a `Source` plan (measured: 20 params against 14 names, the real
    `jsonb` parameter unnamed and `url_aliases` reported against a `graph_id`),
    re-creating defect 2 on the table with the most `jsonb` columns. `Point`'s key
    is a literal string, so the round-trip test above cannot see it.

    ⚠️ SCOPE, stated rather than implied: this builds the TWO record types whose
    plans carry a `_SqlExpr` (a resolved `:Source` key, #9956) — the only shapes
    in which a column can expand into several parameters. It does not walk the
    rest of `RECORD_MUTATIONS`, and `tests/test_record_projection.py` asserts
    nothing about `param_columns` either; a NEW builder that expands a value is
    this pin's responsibility to grow.
    """
    from tortoise import record_projection as rp

    tenant = _tenant()
    misaligned: list[tuple] = []
    seen = 0
    declined = 0
    for record_type, build in sorted(_RECORD_BUILDERS.items()):
        for statement in rp.replay_plan([build(tenant)], org_id=tenant[0],
                                        graph_id=tenant[1], position=True):
            # ⚠️ TWO attribution shapes coexist (#10620 ∧ #9548): `columns` is
            # the builder's DECLARED pairing (None when a `_SqlExpr` key shifts
            # it — the Source plan), `param_columns` is the per-parameter map.
            # Either may be the one stating an alignment; when NEITHER does, the
            # statement is a declared gap (the journal row) and is skipped rather
            # than counted as aligned.
            named = statement.columns
            if named is None and not statement.param_columns:
                declined += 1
                continue
            seen += 1
            stated = named if named is not None else statement.param_columns
            if len(stated) != len(statement.params):
                misaligned.append(
                    (record_type, statement.sql[:60], len(stated),
                     len(statement.params)))
    assert seen >= 1, f"no statement carried an attribution to check ({seen})"
    assert declined >= 1, (
        "the `_SqlExpr` shape must DECLINE to state a pairing (#10620) — if "
        f"every statement now states one, this pin no longer covers it ({declined})")
    assert not misaligned, (
        "param_columns and params disagree (record, sql, names, params): "
        f"{misaligned} — the failure attribution would name the wrong column")


def test_the_jsonb_map_is_not_reparsed_per_parameter(monkeypatch):
    """ONE schema read per PLAN, not one per bound string (#9548 review).

    `_jsonb_or_scalar` runs once per string parameter of EVERY statement, so an
    un-memoised parse measured 2.71 ms and turned a 50-record `replay_plan` into
    1182 ms instead of 5.8 ms — a ~200x regression on the SYNCHRONOUS capture
    path, which §17 forbids the seam from adding.

    Counted, not timed: the assertion is the NUMBER of PARSES a plan build
    performs, so it cannot flake on a loaded box.

    ⚠️ TWO counters, because the two readers no longer share a parsing path
    (#9558 review cycle 2, P1). `jsonb_columns()` answers through
    `_declared_types_for_dir`, which reads the migration files DIRECTLY — it does
    not go through `_read_migrations`, which is the only thing an earlier version
    of this test counted. Measured on that version: `reads` was 0 in a warm
    process while the jsonb map was re-parsed once per plan, and the TTL disabled
    (`_JSONB_CACHE_TTL_SECONDS = -1`) produced **100 jsonb parses with
    `reads = 0`** — the assertion passed while the regression it names was
    happening. So:

    * `parses` counts `_declared_types_for_dir` — THE parse — and is the assertion
      that matters: one per plan build, never one per bound parameter (the
      regressed shape measured ~180 for this plan);
    * `reads` counts `_read_migrations` as a cross-check on the column map, which
      is now memoised per TREE (`_entry_sql`), so a cleared-cache build reads once
      and a warm one reads zero.

    Both caches are cleared first (#9558): the column map has its own
    process-global cache, and clearing only the jsonb one left the count
    meaningless.
    """
    from tortoise import record_projection as rp

    reads: list[int] = []
    parses: list[int] = []
    real_reads = rp._read_migrations
    real_parse = rp._declared_types_for_dir

    def _counting_reads(migrations_dir=None):
        reads.append(1)
        return real_reads(migrations_dir)

    def _counting_parse(migrations_dir):
        parses.append(1)
        return real_parse(migrations_dir)

    monkeypatch.setattr(rp, "_read_migrations", _counting_reads)
    monkeypatch.setattr(rp, "_declared_types_for_dir", _counting_parse)
    rp._jsonb_cache_clear()
    rp._release_schema_cache()
    tenant = _tenant()
    record = _record(tenant, search_keys=GRAPH_KEYS)
    rp.replay_plan([record] * 20, org_id=tenant[0], graph_id=tenant[1])
    try:
        assert len(parses) == 1, (
            f"one plan build PARSED the declared types {len(parses)} times for "
            "20 records — the jsonb map is being re-parsed per bound parameter "
            "instead of once per build")
        assert len(reads) <= 1, (
            f"one plan build read the migrations {len(reads)} times for 20 "
            "records — the column map is being re-read instead of served from "
            "its cache")
    finally:
        rp._jsonb_cache_clear()


def test_a_synthetic_tree_reaches_the_forward_and_fail_closed_arms(tmp_path):
    """The two branches the real tree cannot exercise (#9548 review round 6).

    `jsonb_columns(migrations_dir=…)` and `_jsonb_cache_clear()` had no caller,
    so nothing could reach either a forward-migration change on a DELETE-shaped
    tree or the fail-closed refusal — a regression in either would stay green.
    Both arms are driven here on a COPY of the record migrations:

    * a narrowing (`point.search_keys jsonb → text`) must drop the name, or the
      rule would JSON-quote strings bound to a now-`text` column: no error, no
      counter, silent corruption;
    * an empty directory must RAISE, because an empty set silently disables the
      encoding and the 22P02 row drops come back.
    """
    import shutil

    from tortoise import record_projection as rp

    tree = tmp_path / "migrations"
    tree.mkdir()
    for name in rp.RECORD_MIGRATIONS:
        source = rp._migrations_dir() / name
        if source.exists():
            shutil.copy(source, tree / name)
    # The real tree FIRST, so the arm is proven to reach the parser at all —
    # and it is the LIVE narrowing `20261102000002` (#10620) that proves the
    # ALTER arm was read: `search_keys` is `jsonb` in the base shape and must
    # NOT be in the set.
    real = rp.jsonb_columns(tree)
    assert "search_keys" not in real, (
        "the real tree's `20261102000002` narrowing was not applied — a "
        "jsonb→text ALTER must remove the column from the encoding rule")
    assert "embedding" in real, (
        "no column survived the parse at all — the arm would pass by reading "
        "nothing")
    # ⚠️ APPENDED to the LAST registry file, never a new file: `_read_migrations`
    # reads exactly `RECORD_MIGRATIONS`, so a file outside the registry would be
    # invisible and the arm would pass by not being read at all (measured).
    last = tree / rp.RECORD_MIGRATIONS[-1]
    last.write_text(
        last.read_text(encoding="utf-8")
        + "\nALTER TABLE public.point\n"
          "    ALTER COLUMN search_keys TYPE jsonb\n"
          "    USING search_keys::jsonb;\n",
        encoding="utf-8")
    assert "search_keys" in rp.jsonb_columns(tree), (
        "a text→jsonb WIDENING must put the column back in the encoding rule — "
        "the ALTER arm was read for the real narrowing above, and this is the "
        "same arm driven the other way")

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(rp.RecordProjectionError):
        rp.jsonb_columns(empty)


def test_the_replay_wrapper_attributes_a_failing_statement():
    """The try/except in `replay()` itself — not a hand-built exception.

    Both other attribution tests bypass the wrapper (one constructs
    `RecordStatementError` directly, the other replaces `replay`), so deleting
    the `raise` in `replay()` left them green. This drives the REAL `replay()`
    through a stub cursor that fails on the plan statement, and asserts the
    chained cause, the index/total, and the column + Python type in the text.
    """
    from tortoise import record_projection as rp

    tenant = _tenant()
    record = _record(tenant, search_keys=GRAPH_KEYS)
    driver_error = ValueError("invalid input syntax for type json")

    class _Cursor:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, sql, params=None):
            self.calls += 1
            # Fail on the statement that binds `search_keys` — the identity
            # insert before it must execute so `index` is a real position.
            if "search_keys" in sql:
                raise driver_error

        def fetchone(self):
            return (tenant[0], False)

    class _Conn:
        def cursor(self):
            return _Cursor()

    import pytest as _pytest

    from tortoise import record_projection as _rp
    original = _rp.scope_connection
    _rp.scope_connection = lambda conn, org_id: conn.cursor()
    try:
        with _pytest.raises(rp.RecordStatementError) as caught:
            rp.replay([record], _Conn(), org_id=tenant[0], graph_id=tenant[1],
                      position=False)
    finally:
        _rp.scope_connection = original
    error = caught.value
    assert error.__cause__ is driver_error, "the driver's cause must be chained"
    text = str(error)
    # The line is composed by `_failure_line(_statement_attribution(...), …)`
    # (#10620): it names the ORDINAL, the table, the declared columns and the
    # driver's own fault clause. `search_keys` is `text` since #10620's
    # migration, so it carries no `→jsonb` annotation — the annotation belongs
    # to columns that are still `jsonb`.
    assert "record statement" in text and "/" in text, text
    assert "public.point" in text, text
    assert "search_keys" in text, text
    assert "invalid input syntax for type json" in text, text


#: The record types with a plan this file can build without a database: the two
#: whose statements carry a `_SqlExpr` (a resolved `:Source` key, #9956).
_RECORD_BUILDERS: dict = {}


def _register_builders() -> None:
    """Fill `_RECORD_BUILDERS` with the `_SqlExpr`-bearing record shapes."""
    def _source(tenant):
        # `SourceCreated` is a FLAT snapshot (`path=None`, `key_from="url"`),
        # and its `url` key resolves through `_source_key_resolution` — the
        # `_SqlExpr` that expands to several parameters.
        return {
            "type": "SourceCreated",
            "url": "https://example.com/a",
            "document_kind": "document",
            "url_aliases": ["https://example.com/a"],
            "ts": "2026-10-31T00:00:00Z",
        }

    def _point(tenant):
        return _record(tenant, search_keys=GRAPH_KEYS)

    _RECORD_BUILDERS["SourceCreated"] = _source
    _RECORD_BUILDERS["PointAdded"] = _point


_register_builders()


# ════════════════════════════════════════════════════════════════════════════
# 2. The schema read that decides the shape
# ════════════════════════════════════════════════════════════════════════════


def test_the_jsonb_columns_are_read_from_the_migrations_never_restated():
    """`jsonb_columns()` reads the DDL — and sees a FORWARD type change.

    Two things must be true for the encoding rule to be safe:

    * `search_keys` is **no longer** `jsonb` — it is `text` after
      `20261102000002` (#10620), and a reader that stopped at `CREATE TABLE`
      would keep JSON-quoting it, storing the quotes as data; and
    * `session.capture_redactions` is NOT either — it is `jsonb` in the base
      `CREATE TABLE` and `integer` after #9264's forward migration, so a reader
      that stopped at `CREATE TABLE` would call an integer column a document
      container.

    The first is the forward-NARROWING witness (a `jsonb` name the reader must
    drop) and the second a forward change of the other kind; between them this
    is the only pin on `_ALTER_COLUMN_TYPE_RE`.
    """
    jsonb = record_projection.jsonb_columns()
    # `search_keys` is the FORWARD-change witness: `jsonb` in the base shape,
    # `text` after `20261102000002` (#10620). A reader that stopped at
    # `CREATE TABLE` would keep JSON-quoting a text column.
    assert "search_keys" not in jsonb, (
        "public.point.search_keys is `text` since 20261102000002 — a reader "
        "that still calls it jsonb would JSON-quote a text column, storing the "
        "quotes as data (#10620)")
    assert "capture_redactions" not in jsonb, (
        "capture_redactions is jsonb only in the BASE shape; #9264's forward "
        "migration narrows it to integer, and the reader must see that")
    # …and the columns that carry a genuinely multi-valued graph property stay.
    for column in ("embedding", "tags"):
        assert column in jsonb, column


def test_no_jsonb_column_name_is_shared_with_a_non_jsonb_column():
    """The rule is keyed by column NAME, so a name clash must not exist.

    `_jsonb_or_scalar` is handed a column and nothing else, so
    `jsonb_columns()` is a name-keyed set. If a `text` column in one record
    table shared its name with a `jsonb` column in another, the rule would
    JSON-encode that text column's strings — a NEW 22P02-free but
    silently-mangled write. This test is what keeps the assumption honest
    instead of a comment.
    """
    sql = record_projection._read_migrations()
    # ⛔ THE SAME THREE INPUTS THE PARSER FOLDS, not `CREATE TABLE` alone: a
    # forward `ADD COLUMN … jsonb` or an `ALTER COLUMN … TYPE` narrowing can
    # introduce (or remove) a `jsonb` name the base shape never mentions, and a
    # pin that read only the base would stay green through exactly the clash it
    # exists to catch.
    typed: dict[str, set[str]] = {}
    for table, body in record_projection._TABLE_RE.findall(sql):
        if table not in record_projection.THE_RECORD_TABLES:
            continue
        for _quoted, column, type_name in \
                record_projection._COLUMN_TYPE_RE.findall(body):
            typed.setdefault(column, set()).add(type_name)
    for table, body in record_projection._ALTER_ADD_COLUMN_RE.findall(sql):
        if table not in record_projection.THE_RECORD_TABLES:
            continue
        for column, type_name in \
                record_projection._ADDED_COLUMN_TYPED_RE.findall(body):
            typed.setdefault(column, set()).add(type_name.lower())
    for table, column, type_name in \
            record_projection._ALTER_COLUMN_TYPE_RE.findall(sql):
        if table not in record_projection.THE_RECORD_TABLES:
            continue
        typed.setdefault(column, set()).discard("jsonb")
        typed.setdefault(column, set()).add(type_name.lower())
    jsonb = record_projection.jsonb_columns()
    clashes = {c: t for c, t in typed.items() if c in jsonb and t - {"jsonb"}}
    assert not clashes, (
        f"column name(s) {clashes} are `jsonb` somewhere and another type "
        "elsewhere — a name-keyed `jsonb_columns()` cannot tell them apart; "
        "key the rule by (table, column) before adding one")


#: A `public.<table>.<column>` attribution in PROSE, i.e. the shape a note names
#: a real column by. A two-segment `public.<table>` is a TABLE claim with no
#: column to check, so it is deliberately not matched.
_ATTRIBUTION_RE = re.compile(r"public\.([a-z_]+)\.([a-z_]+)")
#: An `INSERT INTO public.<table> (<column>, …)` — the illustrative hand-built
#: statements in this file. Their columns are checked against the same map.
_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+public\.([a-z_]+)\s*\(([^)]*)\)", re.IGNORECASE)


def test_every_table_column_attribution_in_this_file_resolves_in_the_migrations():
    """Every column this file NAMES is read back from the migrations.

    ⛔ WHY (#10726 review P3). This file's prose attributed `payload` to a table
    that does not carry it: `payload jsonb NOT NULL` is declared on
    `public.journal_event`, and the table the earlier wording named is opened
    fifteen tables later in the same migration with no such column. The pin
    itself was never wrong — the column is real, the map really calls it
    `jsonb` — so nothing reddened and nothing was vacuous. The defect is the
    ATTRIBUTION, and it is the one sentence the next lane acts on: it would go
    looking for the column on the wrong table and either "fix" the pin or move
    a real narrowing past a claim the schema refutes.

    That is an instance of the documented failure mode this file's own subject
    is about (a second, hand-written declaration of the schema going stale), so
    the remedy is the same one the module uses: DERIVE the claim instead of
    restating it. Every `public.<table>.<column>` this file names, and every
    column list on an `INSERT INTO public.<table>`, is resolved against
    `table_columns()` — the parser over the record migrations. A hypothetical
    example therefore has to be written WITHOUT the `public.` prefix; that is
    the deliberate escape hatch, and it keeps this scan from needing an
    allowlist that could hide a real defect.
    """
    text = Path(__file__).read_text(encoding="utf-8")
    declared = record_projection.table_columns()
    problems: list[str] = []

    def _check(table: str, column: str, line: int, claim: str) -> None:
        where = f"{Path(__file__).name}:{line} (`{claim}`)"
        if table not in declared:
            problems.append(
                f"{where}: `{table}` is not a record table — the migrations "
                f"declare {sorted(declared)}")
        elif column not in declared[table]:
            problems.append(
                f"{where}: `{table}` has no `{column}` column — it has "
                f"{sorted(declared[table])}")

    def _line(offset: int) -> int:
        return text.count("\n", 0, offset) + 1

    for match in _ATTRIBUTION_RE.finditer(text):
        _check(match.group(1), match.group(2), _line(match.start()),
               match.group(0))
    for match in _INSERT_RE.finditer(text):
        for raw in match.group(2).split(","):
            column = raw.strip().strip('"')
            if column:
                _check(match.group(1), column, _line(match.start()),
                       f"INSERT INTO {match.group(1)}({column})")

    assert not problems, (
        "this file names a column the record migrations do not declare on that "
        "table (#10726 review P3 — a note whose own schema refutes it): "
        + "; ".join(problems)
        + ". Read the table from the `CREATE TABLE` that declares the column "
        "(and note that `public.event` and `public.journal_event` are two "
        "different tables in the same migration) — do NOT widen this scan or "
        "allowlist a name to get green.")


# ════════════════════════════════════════════════════════════════════════════
# 3. Attribution — a failure names its column, its value type, its statement
# ════════════════════════════════════════════════════════════════════════════


def test_a_failed_statement_names_the_column_the_value_type_and_the_sql():
    """The three facts #9548 was missing, on ONE line (#9548 defect 2).

    302 lost rows were reported as nothing but `InvalidTextRepresentation`:
    no column, no Python type, no statement. `RecordStatementError` carries all
    three and `RecordWriter.write` no longer discards `str(exc)`.
    """
    tenant = _tenant()
    statement = _search_keys_statement(
        _plan_record(_record(tenant, search_keys=GRAPH_KEYS), tenant))
    cause = ValueError("invalid input syntax for type json")
    error = record_projection.RecordStatementError(
        statement, cause, index=0, total=1)
    text = str(error)
    assert "search_keys" in text, text
    assert "search_keys=str" in text, (
        f"the bound column and the PYTHON type of its value must be named — "
        f"they are the halves Postgres never reports: {text}")
    assert "search_keys=str→jsonb" not in text, (
        "`search_keys` is `text` since 20261102000002 (#10620); a `→jsonb` "
        f"annotation here means the schema map is stale: {text}")
    assert statement.sql[:120] in text, text
    assert "invalid input syntax for type json" in text, (
        f"the cause's own text was discarded, which is defect 2 itself: {text}")


def test_a_message_string_with_a_cause_degrades_instead_of_raising():
    """The cause form admits a bare string, and must not raise from the reporter.

    ⛔ WHY (#10648 review P3). `__init__` is annotated `statement: Statement |
    str` and takes an optional `cause`, but the cause branch called
    `statement.attribution()` unconditionally. A repair script or a backfill that
    followed the signature — `RecordStatementError(f"row {i}", exc)` — got
    `AttributeError: 'str' object has no attribute 'attribution'` raised FROM the
    error reporter, replacing the driver's real cause with a traceback from the
    report. That is the "lost row nobody can attribute" failure this class exists
    to end, so the reporter is pinned to DEGRADE, not raise.

    Non-vacuous: restore the unconditional `.attribution()` call and this test
    reds with that `AttributeError`.
    """
    cause = ValueError("invalid input syntax for type json")
    error = record_projection.RecordStatementError(
        "backfill row 7", cause, index=6, total=9)
    text = str(error)
    assert "statement 7/9" in text, text
    assert "ValueError: invalid input syntax for type json" in text, (
        f"the driver's cause is the half the reporter must never replace: {text}")
    assert "backfill row 7" in text, (
        f"a bare message string must still name its subject: {text}")


def test_the_cause_form_withholds_the_drivers_detail_and_context():
    """The cause arm composes `_driver_fault`, not `str(cause)` (#11121).

    ⛔ WHY. `__init__` composed the cause clause as
    ``type(cause).__name__: {cause}`` — ``str(cause)`` — while the class
    docstring one page above states, in as many words, that ``str(exc)`` IS NOT
    CARRIED because on a driver error it is the protocol's fields CONCATENATED:
    ``DETAIL``/``CONTEXT`` quote the refused row or a prefix of its values. The
    message arm learned this first (#10648 review P2: `replay()` composes
    `_failure_line(…, _driver_fault(exc))`); the cause arm kept interpolating, so
    the withhold
    documented for the line was true of one arm and false of the other.

    ⛔ AND NOTHING DOWNSTREAM REPAIRED IT — which is why the fix belongs HERE.
    `record_writer._bounded_cause` narrows only when the OUTER exception carries
    `diag`/`pgcode`; a `RecordStatementError` carries neither, so pre-fix it took
    its `str(exc)` branch with the driver's ``DETAIL`` already embedded here —
    which is what made a leak in this class a leak all the way to the log. The
    narrowing still runs (those two facts are unchanged); it no longer has a
    driver ``DETAIL`` to find, because the clause this test pins is composed by
    `_driver_fault` (#11124 review cycle 4: the tense is the claim).

    This is not a live exposure (no in-tree PRODUCTION caller builds this form:
    only :func:`replay` raises the class, via the message form; the cause form
    is built by this module's own tests), and that is exactly why it is pinned —
    the docstring advertises the form for the caller that logs it (a repair
    script, a backfill). The five in-tree constructions are all THIS file's
    pins, which is why the qualifier matters: an unqualified "no in-tree caller"
    is refuted by its own assertions (#11124 review P3).

    Non-vacuous: restore ``{type(cause).__name__}: {cause}`` and this test reds on
    the first assertion — the DETAIL token is the SAME string the attribution
    half withholds, so one value proves both halves of the line at once.
    """
    tenant = _tenant()
    statement = _search_keys_statement(
        _plan_record(_record(tenant, search_keys=GRAPH_KEYS), tenant))
    cause = _DriverShapeError()
    error = record_projection.RecordStatementError(
        statement, cause, index=0, total=1)
    text = str(error)

    # The VALUE, by its own spelling: `DETAIL`'s token IS the bound search key.
    assert GRAPH_KEYS not in text, (
        "the driver's DETAIL clause reached the failure line — it quotes the "
        f"refused value, which this same line withholds by name: {text}")
    assert "DETAIL:" not in text and "CONTEXT:" not in text, (
        f"the protocol's DATA fields must not be carried: {text}")

    # …and the withhold is the ATTRIBUTION half's, not a blanket silence: the
    # column is still NAMED (by its Python type — `Statement.attribution()`
    # reports `value_types()`, not the bound values), so the line still says
    # which column and which row, which is the whole point of the class.
    assert "search_keys=str" in text, (
        f"the attribution half must still name the column: {text}")

    # …and the line stays DIAGNOSABLE: the SQLSTATE and the terse `M` field.
    assert "22P02" in text, (
        f"`_driver_fault` carries the SQLSTATE — the datum the pre-fix line did "
        f"NOT have; the 302 lost rows were reported as nothing but the cause's "
        f"CLASS NAME (`InvalidTextRepresentation`), and naming no column is "
        f"what made them unreadable: {text}")
    assert "invalid input syntax for type json" in text, (
        f"the driver's PRIMARY message is the other half of the fault: {text}")


def test_a_none_pairing_cannot_make_the_reporter_raise():
    """`Statement(sql, params, None)` must not turn the reporter into the fault.

    ⛔ WHY (#10726 review P3). On `main` the third positional field WAS
    `columns`, so `Statement(<UPDATE sql>, <params>, None)` was the real spelling
    of "this statement declines to state its pairing" — and an in-tree test
    still writes it that way (`tests/test_record_searchkeys_10620.py::
    test_an_unattributed_update_names_every_set_column`). After the fold put
    `param_columns` third, that `None` landed in `param_columns`, which
    `value_types()` iterated unguarded: a `RecordStatementError` built around
    such a statement raised

        TypeError: 'NoneType' object is not iterable

    from INSIDE the reporter, replacing the driver's real cause with a traceback
    from the diagnostic. That is the "lost row nobody can attribute" failure the
    cause form exists to end, and the class's own docstring claims "no call shape
    is a trap". It is reachable by exactly the caller the form is designed for (a
    repair script, a backfill), so it is pinned here.

    Non-vacuous, and the ORDER below is the proof: the error is CONSTRUCTED
    first, so dropping the fix reds on the `TypeError` above — on the
    construction of the diagnostic itself, not on any assertion. Dropping
    `__post_init__`'s normalization ALONE is already enough to red it, because
    the statement reaches `value_types()` the way a repair script's would.
    """
    cause = ValueError("invalid input syntax for type json")
    statement = record_projection.Statement(
        "UPDATE public.point SET content = %s WHERE org_id = %s",
        ("c", "o"),
        None,  # `main`'s spelling: the pairing is declined
    )

    # ⚠️ BEFORE any assertion: this is the line that raised the `TypeError` at
    # the pre-fix head. An assertion first would have caught the same defect and
    # proved something weaker (that the field was not normalized) rather than
    # that the reporter stays a reporter.
    error = record_projection.RecordStatementError(
        statement, cause, index=0, total=1)
    text = str(error)
    assert "statement 1/1" in text, text
    assert "ValueError: invalid input syntax for type json" in text, (
        f"the driver's cause is the half the reporter must never replace: {text}")
    assert "UPDATE public.point" in text, (
        f"the failing statement must still be named: {text}")

    # The field-level invariant behind that: a declined pairing is the EMPTY
    # tuple, so no reader — this one or a later one — can trip over it.
    assert statement.param_columns == (), (
        "a declined pairing must normalize to the empty tuple, not stay `None`: "
        f"{statement.param_columns!r}")
    assert statement.value_types() == "", (
        "a statement that names no columns has no value types to report: "
        f"{statement.value_types()!r}")


def test_the_pre_fix_binding_shape_is_still_readable_when_built_by_hand(
        monkeypatch):
    """`value_types()` is READABLE in all three shapes, incl. the map MISS.

    ⛔ WHY A HAND-BUILT `Statement` (#10692 review P2). The
    `→jsonb(NOT a JSON document)` arm of `Statement.value_types()` is
    UNREACHABLE for every statement this module builds — `_jsonb_or_scalar`
    `json.dumps`-es each `str` bound to a `jsonb` column, and the only other
    `str` parameters (`org_id`, `graph_id`, `node_key`, `id`, `url`) are not
    `jsonb` columns — so before this test NOTHING exercised it, while the method
    sold it as the read-out that "identifies #9548". A diagnostic that cannot
    fire reads as coverage that is not there. It is kept (a repair script or a
    backfill builds its own `Statement`), so it is pinned here as the
    DEFENSIVE-ONLY branch it is.

    The three shapes are the whole vocabulary, and the THIRD is the one a stale
    `jsonb_columns()` map produces — the encoder reads the SAME map, so the
    ABSENCE of `→jsonb` (not the NOT-a-document arm) is that miss's signal.
    Also pins the "never raises" clause: a map that cannot be read must not
    replace the driver's cause with its own exception.
    """
    from tortoise.record_projection import Statement

    # ⚠️ `payload`, not `search_keys`: `search_keys` is `text` since #10620's
    # `20261102000002`, so it can no longer carry the `→jsonb` shapes this test
    # exists to pin. `payload` is still declared `jsonb` — on
    # `public.journal_event`, which is the table this statement names — so all
    # three shapes remain reachable through a hand-built statement. The table is
    # illustrative only (`value_types()` resolves the column by NAME through
    # `jsonb_columns()`), but a statement that names a table NOT carrying the
    # column is the defect #10726's review found, so it names the real one.
    def _types(*, value: str, param_columns: tuple[str, ...] = ("payload",)):
        # ⚠️ `param_columns=`, BY KEYWORD. `Statement`'s fields are
        # `(sql, params, param_columns, columns)`, so passing this third
        # positionally binds it to `param_columns` and leaves `columns` None —
        # which is what `value_types()` reads, so the pin would still hold, but
        # the spelling would reproduce the exact mis-binding `_upsert_statement`
        # names as the #9956 defect.
        return Statement(
            "INSERT INTO public.journal_event (payload) VALUES (%s)",
            (value,), param_columns=param_columns).value_types()

    # 1. The GOOD binding: a `str` the map calls `jsonb` whose value IS a
    #    JSON document — what `_jsonb_or_scalar` emits for a `jsonb` column.
    assert _types(value=json.dumps(GRAPH_KEYS)) == "payload=str→jsonb"

    # 2. DEFENSIVE ONLY: the PRE-FIX binding shape (a bare string on a `jsonb`
    #    column) is still NAMED rather than printed as an opaque `str`. No
    #    builder in this module can produce it — this hand-built statement is
    #    the only way in, which is exactly the point.
    assert _types(value=GRAPH_KEYS) == (
        "payload=str→jsonb(NOT a JSON document)"), (
        "the pre-fix binding shape must stay readable — it is the read-out a "
        "hand-built statement (repair script, backfill) needs")

    # 3. A map MISS (stale/partial migrations parse): the column is bound
    #    VERBATIM — the shape the encoder cannot see either, because it asks the
    #    same map — and it reads as NO suffix. That absence is the signal; a
    #    reader must not take `payload=str` as a clean binding.
    monkeypatch.setattr(record_projection, "jsonb_columns",
                        lambda *a, **k: frozenset())
    assert _types(value=GRAPH_KEYS) == "payload=str", (
        "an un-suffixed string parameter on a column the migrations declare "
        "`jsonb` is the #9548 condition, not a clean binding")

    # 4. A schema read that RAISES must degrade to no attribution, never
    #    replace the driver's cause with its own exception.
    def _boom(*_a, **_k):
        raise OSError("migrations unreadable")

    monkeypatch.setattr(record_projection, "jsonb_columns", _boom)
    assert _types(value=GRAPH_KEYS) == "payload=str"


def test_the_write_error_no_longer_discards_the_cause(monkeypatch):
    """`RecordWriter.write` reports the cause text, not only its class name."""
    def _boom(*_args, **_kwargs):
        raise record_projection.RecordStatementError(
            record_projection.Statement(
                "INSERT INTO public.point (org_id, search_keys) VALUES (%s, %s)",
                ("o", GRAPH_KEYS), ("org_id", "search_keys")),
            ValueError("invalid input syntax for type json"),
            index=0, total=1)

    monkeypatch.setattr(record_projection, "replay", _boom)
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV,
                       "postgresql://u:p@127.0.0.1:1/db")
    writer = record_writer.RecordWriter(
        connect=lambda dsn: _RefusingConnection())
    with pytest.raises(record_writer.RecordWriteError) as caught:
        writer.write([_record(_tenant(), search_keys=GRAPH_KEYS)],
                     org_id="o", graph_id="g")
    text = str(caught.value)
    assert "record write failed" in text, text
    assert "invalid input syntax for type json" in text, (
        f"the cause text is still discarded, which is defect 2 itself: {text}")
    assert "search_keys=str" in text, (
        f"the failing column and its value type must ride the wrap: {text}")


class _RefusingConnection:
    """A connection the seam opens itself, so `write()` closes it on failure."""

    def close(self) -> None:
        return None


# ════════════════════════════════════════════════════════════════════════════
# 4. The record APPLIES the live shape — against a real Postgres (#9551)
# ════════════════════════════════════════════════════════════════════════════


def _record_db():
    import os
    dsn = os.environ.get(RECORD_DB_ENV)
    if not dsn:
        pytest.skip(f"{RECORD_DB_ENV} unset — no record database to write to")
    psycopg2 = pytest.importorskip("psycopg2")
    conn = psycopg2.connect(dsn)
    conn.commit()
    return conn


def _app_role_dsn(dsn: str) -> str:
    """`dsn` connecting as the record's APPLICATION role (the gate's role)."""
    from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit

    from test_9078_record_written_by_production_path import RECORD_APP_ROLE
    parts = urlsplit(dsn)
    query = parse_qs(parts.query)
    # ⚠️ ONE `options` VALUE, several `-c` settings inside it. libpq keeps only
    # the LAST value of a repeated URI keyword, so two `options=` pairs silently
    # drop the first — measured here: `role=tortoise_record_app` vanished and the
    # session opened as the provisioning superuser, which the role gate then
    # refused. The timeouts are BOUNDED so a blocked write is a NAMED error
    # instead of a hung test.
    query["options"] = [
        f"-c role={RECORD_APP_ROLE} -c lock_timeout=15000 "
        "-c statement_timeout=30000"]
    return urlunsplit(parts._replace(
        query=urlencode(query, doseq=True, quote_via=quote)))


def _seed(conn, tenant) -> None:
    from test_9078_record_written_by_production_path import _ensure_record_schema, _seed_tenant
    _ensure_record_schema(conn)
    _seed_tenant(conn, tenant)
    conn.commit()


def _failures() -> int:
    return int(monitoring.record_write_failure_counts().get("write_failed", 0))


def test_the_record_applies_the_live_search_keys_shape_and_reads_it_back(
        monkeypatch, caplog):
    """PRODUCTION-FIRST: `write_failed` stops climbing, `write_ok` climbs.

    Drives the LIVE DOOR the armed deployment drives
    (`record_writer.write_live_record` — the one the three journal-append sites
    call), on the app-role DSN `replay` requires, and asserts all three things a
    green unit test alone cannot:

    * every write returns APPLIED (the `write_ok` half — there is no separate
      counter for it; the outcome on the returned `RecordWrite` IS the record of
      a successful write, and `monitoring.record_write_failure_counts()` is the
      failure half);
    * `write_failed` did NOT move — and this assertion now DISCRIMINATES.
      ⛔ `write_failed` is incremented in exactly ONE place: `write_live_record`'s
      own `except`. `RecordWriter.write` merely raises, so an earlier version of
      this test driving `write_records` compared two identical integers for ANY
      input, including the pre-fix code — a decorative assertion wearing the
      fix's headline claim (#9548 review round 4, P2). Driving the live door is
      what makes `after == before` mean "no row was dropped";
    * the row is REALLY there and the record's own reader gives back the graph's
      flat string.

    ⚠️ A REFUSAL IS REPORTED WITH THE LOG, not just its reason code: the cause
    lives only in `write_live_record`'s WARNING (that is the door's contract —
    it never raises), and `refusal='write_failed'` alone is precisely the
    unattributable string this issue exists to end. `caplog.text` is asserted
    into only on the failure path, so a red in the gated `#9551` job carries the
    column, the statement and the driver's own message.
    """
    import logging

    from tortoise.cache_projection import node_from_row
    caplog.set_level(logging.WARNING, logger="tortoise.record_writer")

    conn = _record_db()
    tenant = _tenant()
    _seed(conn, tenant)
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV,
                       _app_role_dsn(_dsn()))
    records = [_record(tenant, search_keys=GRAPH_KEYS,
                       content=f"deploy {i} released err4032") for i in range(3)]

    before = _failures()
    applied = 0
    for index, record in enumerate(records):
        # One record per call, each with its OWN journal ordinal (`seq`), so
        # `journal_event`'s `(org, graph, seq)` upsert never absorbs a
        # sibling's position row. `write_live_record` commits and closes the
        # connection the seam opens for it (unlike `write_records`, whose caller
        # owns the transaction and which therefore increments NO counter).
        write = record_writer.write_live_record(
            record, seq=index + 1, tenant=tenant)
        assert write is not None, "the record was not armed — nothing attempted"
        assert write.outcome is record_writer.RecordWriteOutcome.APPLIED, (
            f"the live shape was refused ({write.refusal!r}): {write!r} — the "
            f"door's WARNING is the only place the cause is carried: "
            f"{caplog.text}")
        applied += 1
    after = _failures()
    assert applied == len(records)
    assert after == before, (
        f"`write_failed` climbed from {before} to {after} over {applied} "
        "write(s) — the record is still dropping rows (#9548)")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT node_key, search_keys FROM public.point "
            "WHERE org_id=%s AND graph_id=%s ORDER BY node_key",
            (tenant[0], tenant[1]))
        rows = cur.fetchall()
    assert len(rows) == len(records), (
        f"the record holds {len(rows)} row(s) for {len(records)} write(s)")
    for _key, stored in rows:
        assert stored == GRAPH_KEYS, (
            f"the column holds {stored!r}; the graph's value is the flat string "
            f"{GRAPH_KEYS!r}")

    # …and through the RECORD's OWN reader, which is what a rebuild consumes.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM public.point WHERE org_id=%s AND graph_id=%s "
            "ORDER BY node_key", (tenant[0], tenant[1]))
        cols = [d[0] for d in cur.description]
        node_rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    conn.close()
    for row in node_rows:
        props = node_from_row("Point", row).props
        assert props.get("search_keys") == GRAPH_KEYS, (
            f"the rebuild reads search_keys back as {props.get('search_keys')!r} "
            f"({type(props.get('search_keys')).__name__}) — only a str is what "
            "the Point FULLTEXT index can match")


def _dsn() -> str:
    import os
    return os.environ[RECORD_DB_ENV]
