"""§9.1's narrative home, made executable (#9555, R7, epic #7879).

WHAT THIS PINS. `docs/architecture/STORAGE-ARCHITECTURE.md` §9.1 places the S1
narrative and the raw turn transcript in **Postgres, not the graph**, and §9.4
turns the link into the mechanism that reaches the model (*"send the source's
VERBATIM text, not the extracted claim"*). `#9567`'s D7 measures it: *"the S1
narrative and raw turn text come back"* — with the graph destroyed and the
journal unavailable. Five claims, each a test here:

1. **THE HOME EXISTS, AND IT IS SHAPED RIGHT.** One row per session in
   `public.session_narrative`, keyed `(org_id, graph_id, session_id)` with a
   foreign key to **`public.graphs`** (NOT `public.session`) that CASCADES — the
   tenant-erasure path (`purge_org_record`, migration 20261012000005) reaches it
   through organizations → graphs → this table, so a tenant's narrative dies
   with the tenant, while the row's existence does not depend on the session row
   landing first.
2. **IT IS A TENANCY SURFACE LIKE THE OTHER NINE.** RLS enabled, the
   `authenticated` read policy on `app.current_org_id`, the `service_role`
   policy for the write path, and — per R6's recorded decision (`#9554`) — the
   app-role policy with BOTH `USING` and `WITH CHECK`, guarded on the role
   existing because R6's migration (which creates it) is the one that sorts
   first.
3. **IT IS PROJECTED FROM THE RECORDS THAT CARRY IT — AND BOTH CARRIERS NOW
   EXIST.** `capture_redactions`/`turn_count` ride `SessionRecorded` (PRIMARY,
   journal-carried). `narrative`/`summary` were PRIMARY-WITHOUT-CARRIER when
   this suite was written — `story_arc` reached the graph by raw Cypher, which
   is not a journal record — and **#9555 has since landed the carrier**
   (`record_projection.NARRATIVE_RECORD_TYPE`, emitted by the layer-1 commit
   path; see the migration's ✅ block). This suite still pins the PROJECTION
   only: the carrier's own pins live in `tests/test_9555_narrative_carrier.py`.
4. **THE TWO CARRIERS DO NOT CLOBBER EACH OTHER.** They write ONE row, each
   naming only its own columns in the `DO UPDATE SET`. This is the leg that
   makes a replay in journal order equal to a live build: an upsert that wrote
   the whole column set would let whichever record arrived second erase the
   first one's half.
5. **THE READ SIDE READS THE RECORD ONLY.** `tortoise.record_read` serves the
   narrative and the verbatim turns without touching `journal_event` and
   without touching the graph.

WHY THIS IS DB-FREE. The Python lane runs everywhere; the SQL half (the checks
firing, RLS under a real role) is the PGlite harness —
`supabase/tests/20261012000001_record_schema.sql` carries the assertions, and
`supabase/tests/pglite/validate.mjs` runs them. The statement shapes asserted
here are the ones that harness executes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from tortoise import record_projection as rp
from tortoise import record_read as rr

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"
MIGRATION_NAME = "20261023000001_record_session_narrative.sql"
#: The CARRIER's own migration (#9555). The table's creation file is APPEND-ONLY
#: (#1095), so the follow-up that closed the carrier gap records the correction
#: on the SCHEMA with `COMMENT ON` instead — a real catalog object, no column or
#: policy change, and CI's `check-migration-append-only` accepts it.
CARRIER_MIGRATION = "20261024000001_record_session_narrative_carrier.sql"
ORG = "org_9555"
GRAPH = "graph_9555"


def _migration_sql() -> str:
    return (MIGRATIONS / MIGRATION_NAME).read_text(encoding="utf-8")


def _carrier_migration_sql() -> str:
    return (MIGRATIONS / CARRIER_MIGRATION).read_text(encoding="utf-8")


# ── 1. The home exists, is registered, and ships in the wheel ────────────────


def test_the_migration_is_registered_and_exists():
    """A table the reader cannot see is a table the projection cannot write."""
    assert MIGRATION_NAME in rp.RECORD_MIGRATIONS
    assert (MIGRATIONS / MIGRATION_NAME).exists()
    # Both halves: the table's creation file AND the carrier's later, append-only
    # correcting migration (a `COMMENT ON`, which is how a merged migration's
    # story is corrected — #1095 forbids editing it).
    assert CARRIER_MIGRATION in rp.RECORD_MIGRATIONS
    assert (MIGRATIONS / CARRIER_MIGRATION).exists()


def test_the_migration_ships_in_the_wheel():
    """`pyproject.toml` maps `supabase/migrations` to `tortoise.record_migrations`
    with the glob `*_record_*.sql`; a name the glob misses is a record that is
    EMPTY on a user's machine while every test here is green."""
    pattern = re.search(
        r'"tortoise\.record_migrations"\s*=\s*\["([^"]+)"\]',
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"),
    )
    assert pattern is not None, "the wheel's migration glob is gone"
    assert Path(MIGRATION_NAME).match(pattern.group(1)), (
        f"{MIGRATION_NAME} does not match the packaged glob "
        f"{pattern.group(1)!r} — the wheel would ship a record without it"
    )


def test_the_table_is_in_the_record_tables_and_has_its_columns():
    assert rr.NARRATIVE_TABLE in rp.THE_RECORD_TABLES
    columns = rp.table_columns()[rr.NARRATIVE_TABLE]
    assert {"narrative", "summary", "capture_redactions", "turn_count"} <= columns
    # `redactions` is a COUNT, not an envelope: the #9264/R4 disagreement one
    # table over was a jsonb column holding an integer.
    assert re.search(r"\n\s*capture_redactions\s+integer\b", _migration_sql()), (
        "`capture_redactions` must be declared `integer` in the table body"
    )


def test_the_foreign_key_cascades_so_erasure_reaches_it():
    sql = _migration_sql()
    assert "REFERENCES public.graphs (org_id, id)" in sql
    assert "ON DELETE CASCADE" in sql


def test_the_carrier_block_states_the_carrier_THAT_LANDED():
    """The SCHEMA must state the PRESENT carrier — and keep the past.

    This pin has been re-derived twice, and both times for the same reason: a
    migration is where the next reader learns what is true. It first guarded
    against the table's creation block inventing a `capture_redactions` gap that
    the #9264/R4 narrowing had already closed. #9555 has since landed the OTHER
    half — the journal carrier for `story_arc`/`summary` — and ⛔ that correction
    could NOT be an edit to `20261023000001`: a merged migration is append-only
    (#1095) and CI's `check-migration-append-only` refuses a modified one. The
    correction therefore lives in `CARRIER_MIGRATION` as `COMMENT ON`, which
    the psql `d+` output shows.

    Two halves, because a comment can fail in either direction:

    * the CLOSED claims must be gone (no `DatatypeMismatch` refusal, no
      "`capture_redactions` NULL" as the live outcome);
    * the CARRIER must be named (`SessionNarrated`, the shape-keyed projection,
      `NoRecordEffect`) AND its history kept (`raw Cypher`, `story_arc`,
      `summary`), because the history is what makes the claim checkable.

    Derived from the tree, not re-typed: the narrowing migration and the
    registry constant are both read.
    """
    carrier_sql = _carrier_migration_sql()
    assert "COMMENT ON TABLE public.session_narrative" in carrier_sql, (
        "the carrier's correction must be a real catalog object (COMMENT ON), "
        "not just a prose paragraph — a merged migration cannot be edited")
    assert "COMMENT ON COLUMN public.session_narrative" in carrier_sql

    # The #9264/R4 settling migration is in THIS tree, and it narrows the
    # column the table's creation block said would refuse the carrier.
    narrowing = (MIGRATIONS
                 / "20261022000001_record_session_capture_redactions_integer.sql")
    assert narrowing.exists(), (
        "the #9264/R4 narrowing migration is gone — re-derive this pin's premise "
        "instead of deleting the assertion")
    assert re.search(
        r"ALTER TABLE\s+public\.session\s+ALTER COLUMN\s+capture_redactions\s+TYPE\s+integer",
        narrowing.read_text(encoding="utf-8"),
    ), (
        "the narrowing no longer ALTERs `public.session.capture_redactions` to "
        "`integer` — the carrier block's premise changed, so this pin must be "
        "re-derived rather than loosened")
    for claim in ("DatatypeMismatch", "`capture_redactions` NULL",
                  "`capture_redactions` is NULL"):
        assert claim not in carrier_sql, (
            f"the carrier block still states the CLOSED gap {claim!r} — the "
            "column is `integer` since 20261022000001 (#9264/R4 settled) and "
            "the count LANDS")

    # The CARRIER that landed is named, and named the way the code names it —
    # read off the registry so the comment cannot drift from the constant.
    from tortoise import record_projection as rp
    assert rp.NARRATIVE_RECORD_TYPE in carrier_sql, (
        f"the block does not name {rp.NARRATIVE_RECORD_TYPE!r} — a reader would "
        "conclude the narrative still has no journal carrier and rebuild it")
    assert "_narrative_statements" in carrier_sql, (
        "the block does not say WHICH projection lands the narrative "
        "(the shape-keyed `_narrative_statements`), so its 'record effect' "
        "claim is unfalsifiable")
    assert "NoRecordEffect" in carrier_sql, (
        "the block does not say the carrier materialises NO node — which is "
        "the whole §9.1 reason it is a carrier and not a node record")

    # History kept: the gap's CAUSE stays stated, because that is what a
    # reader checks the claim against.
    for kept in ("story_arc", "summary", "raw Cypher"):
        assert kept in carrier_sql, (
            f"the carrier block dropped {kept!r} — the history is what makes the "
            "claim auditable, and deleting it is not a correction")


def test_the_row_does_not_depend_on_the_session_row_landing_first():
    """⛔ NOT a foreign key into `public.session`, and this is a REGRESSION pin.

    The narrative's carrier is a `:Source`/`:Event` record that NAMES its
    session, and the record's own `source.session_id` column has no such
    constraint — the live capture writes the transcript Source through raw
    Cypher, so a session id can arrive before (or without) a `public.session`
    row. An FK here would abort the WHOLE record batch on the live door (the
    source row, the journal position, everything) and wedge a journal replay of
    that record. The tenancy/erasure anchor is therefore the GRAPH.
    """
    sql = _migration_sql()
    assert "REFERENCES public.session" not in sql
    # ... and the projection emits no statement that presumes the session row.
    statements = rp.mutations_for(
        {"type": "DocumentCreated", "url": "doc://d", "sessionId": "s_new",
         "story_arc": "an arc"},
        org_id=ORG, graph_id=GRAPH)
    narrative = [s for s in statements if rr.NARRATIVE_TABLE in s.sql]
    assert len(narrative) == 1
    assert "public.session " not in narrative[0].sql


# ── 2. It is a tenancy surface like the other nine ───────────────────────────


def test_the_migration_carries_the_same_tenancy_posture_as_the_nine():
    sql = _migration_sql()
    assert "ENABLE ROW LEVEL SECURITY" in sql
    # The `authenticated` read policy on the GUC every other record table uses.
    assert "CREATE POLICY session_narrative_read_org" in sql
    assert "current_setting('app.current_org_id', true)" in sql
    # The write path's policy.
    assert "CREATE POLICY session_narrative_service_role_all" in sql
    # SELECT-only for `authenticated`: the write path is #7951, not this grant.
    assert "REVOKE ALL ON public.session_narrative FROM anon, authenticated, public" in sql
    assert "GRANT SELECT ON public.session_narrative TO authenticated" in sql


def test_the_app_role_policy_is_guarded_on_the_role_and_checks_the_write():
    """R6 (`#9554`) decides the record's application path connects as a
    NOBYPASSRLS role, so every record table needs that role's policy — with
    `WITH CHECK`, or the write side of the boundary is open. It is guarded on
    the role existing because R6's migration sorts FIRST; where R6 is absent,
    no `tortoise_record_app` role exists to reach the table."""
    sql = _migration_sql()
    assert "rolname = 'tortoise_record_app'" in sql, (
        "the app-role block must be guarded on the role existing — "
        "an unguarded CREATE POLICY TO a missing role aborts the migration"
    )
    app_block = sql.split("rolname = 'tortoise_record_app'", 1)[1]
    assert "CREATE POLICY session_narrative_app_tenant" in app_block
    assert "USING (org_id = current_setting('app.current_org_id', true))" in app_block
    assert "WITH CHECK (org_id = current_setting('app.current_org_id', true))" in app_block


# ── 3./4. The projection, and the no-clobber rule ────────────────────────────


def _narrative_statements(record):
    return [s for s in rp.mutations_for(record, org_id=ORG, graph_id=GRAPH)
            if f"public.{rr.NARRATIVE_TABLE}" in s.sql]


def _set_columns(statement) -> set[str]:
    """The columns a statement writes on conflict (`DO UPDATE SET`)."""
    tail = statement.sql.split("DO UPDATE SET", 1)[1]
    return {c.strip() for c in re.findall(r"([a-z_]+)\s*=\s*EXCLUDED\.", tail)}


def _insert_columns(sql: str) -> list[str]:
    """The payload columns an INSERT names, in bind order (bookkeeping removed)."""
    inside = sql.split("(", 1)[1].split(")", 1)[0]
    columns = [c.strip() for c in inside.split(",")]
    return [c for c in columns
            if c not in {"org_id", "graph_id", "session_id", "props"}]


def test_a_narrative_carrying_record_projects_the_narrative_home():
    """A narrative-carrying record lands in the narrative home, in the SAME
    batch as the source row (never as a separate replay that can be lost).

    ⛔ This measures the RECORD's own `story_arc`, not a node's. The layer-1
    commit used to write `story_arc` onto the transcript `:Source` as well, and
    #10000 deleted that write — the `SessionNarrated` carrier is its only
    writer now — so this pin seeds the record directly, which is the fold it is
    about (`record_projection._narrative_statements`).
    """
    record = {
        "type": "SourceCreated",
        "url": "session:s_9555",
        "sessionId": "s_9555",
        "story_arc": "First the capture, then the fold.",
        "summary": "a capture session",
    }
    statements = rp.mutations_for(record, org_id=ORG, graph_id=GRAPH)
    narrative = [s for s in statements
                 if f"INSERT INTO public.{rr.NARRATIVE_TABLE}" in s.sql]
    assert len(narrative) == 1
    assert "First the capture, then the fold." in narrative[0].params
    assert ORG in narrative[0].params and GRAPH in narrative[0].params
    assert "s_9555" in narrative[0].params
    # The declared source upsert is still there, and comes first.
    assert statements.index(narrative[0]) > 0
    assert any("INSERT INTO public.source" in s.sql for s in statements)


def test_the_session_record_projects_the_redaction_and_turn_counts():
    record = {
        "type": "SessionRecorded",
        "id": "s_9555",
        "capture_redactions": 7,
        "turn_count": 12,
    }
    narrative = _narrative_statements(record)
    assert len(narrative) == 1
    assert 7 in narrative[0].params and 12 in narrative[0].params


def test_the_two_carriers_never_erase_each_others_columns():
    """THE IDEMPOTENCE LEG. Two records build one row; each must write only its
    own columns, so the order they arrive in cannot decide the row's content."""
    narrative_record = {
        "type": "DocumentCreated",
        "url": "doc://s_9555.md",
        "sessionId": "s_9555",
        "story_arc": "the arc",
    }
    counts_record = {
        "type": "SessionRecorded",
        "id": "s_9555",
        "capture_redactions": 3,
        "turn_count": 4,
    }
    text_cols = _set_columns(_narrative_statements(narrative_record)[0])
    count_cols = _set_columns(_narrative_statements(counts_record)[0])
    assert {"narrative", "summary"} & text_cols
    assert "capture_redactions" in count_cols and "turn_count" in count_cols
    assert not ({"capture_redactions", "turn_count"} & text_cols), (
        "the narrative record would write NULL counts over the counts the "
        "session record landed — an order-dependent record"
    )
    assert not ({"narrative", "summary"} & count_cols), (
        "the session record would erase the narrative the source record landed"
    )


def test_the_two_carriers_are_independent_of_their_arrival_order():
    """ORDER-INDEPENDENCE, executed rather than asserted about a pure function.

    The row is built by two records. Each writes only its own columns, so the
    row after `[text, counts]` is the row after `[counts, text]` — which is what
    makes a live build and a journal replay converge regardless of the order the
    producers happened to emit in. The batch is built through `replay_plan`, i.e.
    the same call a replay makes, and the two orders are compared statement by
    statement.
    """
    text_record = {"type": "DocumentCreated", "url": "doc://s",
                   "sessionId": "s1", "story_arc": "the arc"}
    counts_record = {"type": "SessionRecorded", "id": "s1",
                     "capture_redactions": 3, "turn_count": 4}

    def narrative_rows(order):
        plan = rp.replay_plan(order, org_id=ORG, graph_id=GRAPH)
        return [(s.sql, s.params) for s in plan
                if f"INSERT INTO public.{rr.NARRATIVE_TABLE}" in s.sql]

    def apply_order(order) -> dict[str, Any]:
        """Simulate the row the two statements build, column by column.

        A statement binds `(org_id, graph_id, session_id, *values, props)` in the
        order its INSERT column list names, so the values line up positionally
        with that list. Applying each statement to a dict is the smallest honest
        model of `ON CONFLICT … DO UPDATE SET c = EXCLUDED.c`, and it is what
        makes the claim executable: the ROW after `[text, counts]` must equal
        the ROW after `[counts, text]`.
        """
        row: dict[str, Any] = {}
        for sql, params in narrative_rows(order):
            columns = _insert_columns(sql)
            for column, value in zip(columns, params[3:3 + len(columns)], strict=False):
                row[column] = value
        return row

    forward = narrative_rows([text_record, counts_record])
    reverse = narrative_rows([counts_record, text_record])
    assert len(forward) == len(reverse) == 2
    # Each carrier's statement is byte-identical in both orders; only their
    # SEQUENCE differs, and the SET lists are disjoint, so the row they build is
    # the same.
    assert set(forward) == set(reverse)
    forward_cols = [_set_columns(rp.Statement(sql, params))
                    for sql, params in forward]
    assert not (forward_cols[0] & forward_cols[1])
    # ... and the row itself, built in both orders.
    assert apply_order([text_record, counts_record]) == \
        apply_order([counts_record, text_record])
    assert apply_order([counts_record, text_record]) == {
        "narrative": "the arc", "capture_redactions": 3, "turn_count": 4,
    }


def test_a_summary_without_the_narrative_is_not_the_narrative_column():
    """The capture's own session `:Source` sets `summary` to the transcript's
    first utterance — not the S1 narrative. Projecting it unconditionally would
    let the narrative's companion column hold a prose snippet, and would make a
    D7 read look satisfied against text that is not the narrative."""
    record = {"type": "SourceCreated", "url": "session:s1",
              "sessionId": "s1", "summary": "first thing said"}
    assert _narrative_statements(record) == []


def test_a_nested_carrier_is_read_like_the_declared_mutation_reads_it():
    """`ConversationMiner` emits `{type: "EventRecorded", event: {…}}` and the
    declared mutation accepts that shape; a shape-keyed projection that only
    looked at the envelope would silently drop exactly the mining lane's
    narrative."""
    record = {"type": "EventRecorded",
              "event": {"eventId": "ev1", "sessionId": "s1",
                        "summary": "a summary", "story_arc": "a mined arc"}}
    statements = _narrative_statements(record)
    assert len(statements) == 1
    assert "a mined arc" in statements[0].params
    assert "a summary" in statements[0].params


def test_a_record_without_a_session_coordinate_contributes_nothing():
    record = {"type": "PointAdded", "point": {"id": "p1", "content": "x"}}
    assert _narrative_statements(record) == []


def test_a_record_naming_a_session_but_carrying_no_text_contributes_nothing():
    record = {"type": "SessionRecorded", "id": "s1"}
    assert _narrative_statements(record) == []


def test_a_value_the_column_cannot_hold_is_an_absence_not_a_raise():
    """THE SECONDARY HOME MAY NOT FAIL THE CARRIER'S OWN WRITE.

    `session.capture_redactions` is jsonb and accepts shapes this table's
    INTEGER refuses (`#9264`/R4 is that disagreement, one table over). Raising
    here would turn an additive projection into a NEW way for a live capture to
    fail — so a value the column cannot hold is simply not projected, and the
    reader returns `None` (an absence), never `0` (a false claim) and never a
    coerced value (which would hide the disagreement).
    """
    record = {"type": "SessionRecorded", "id": "s1",
              "capture_redactions": {"total": 7}, "turn_count": -1}
    assert _narrative_statements(record) == []


def test_a_count_this_home_cannot_hold_still_lands_the_records_own_row():
    """The regression pin: `{"total": 7}` is the shape the CARRIER accepts (its
    own column is jsonb) and this home cannot, so the batch must still carry the
    session upsert and no narrative row at all. (A payload the carrier itself
    refuses — an integer `capture_redactions` — would prove nothing here: that
    record never reaches Postgres, `tests/test_9078…` pins the refusal.)"""
    record = {"type": "SessionRecorded", "id": "s1",
              "capture_redactions": {"total": 7}}
    statements = rp.mutations_for(record, org_id=ORG, graph_id=GRAPH)
    assert any("INSERT INTO public.session " in s.sql for s in statements)
    assert not [s for s in statements if rr.NARRATIVE_TABLE in s.sql]


def test_the_no_count_reset_states_the_scope_it_actually_has():
    """A COUNTS-ONLY record emits NOTHING, and the reset's own rule says so.

    The bug this pins is a docstring that spoke for the code (#9638 review P3,
    raised three times because each fix moved the note but not the promise):
    `_narrative_upsert_statement` said an unrepresentable count "must read as no
    count stated" unconditionally, while `_narrative_statements` returns `[]`
    when the record carries no representable value at all — so the one shape
    with NOTHING but an unrepresentable count (`{"type": "SessionRecorded",
    "id": "s1", "capture_redactions": {"total": 7}}`) silently keeps the
    previous capture's number in an existing row.

    BOTH HALVES ARE ASSERTED, and each is a different failure. The BEHAVIOUR is
    the pinned refusal above: no statement, hence no all-NULL row invented for a
    session nothing described. The DOCSTRING is read because it is the only place
    a future reader is told the rule, and the rule it was told was the false one:
    it must scope the reset to a record that also carries a representable value.

    RED on the pre-fix text: the qualifier was absent from the docstring. If a
    later editor wants the reset to fire for counts-only records, that is a
    design change — it has to move this assertion AND
    `test_a_count_this_home_cannot_hold_still_lands_the_records_own_row`, not
    quietly re-promise it in prose.
    """
    counts_only = {"type": "SessionRecorded", "id": "s1",
                   "capture_redactions": {"total": 7}}
    assert _narrative_statements(counts_only) == []

    docstring = " ".join((rp._narrative_upsert_statement.__doc__ or "").split())
    assert "THAT RESET APPLIES ONLY WHEN THE SAME RECORD ALSO CARRIES A " \
           "REPRESENTABLE VALUE" in docstring, (
        "`_narrative_upsert_statement`'s docstring must scope the no-count reset "
        "to a record that ALSO carries a representable value — otherwise it "
        "promises a reset that `_narrative_statements`'s `if not values` guard "
        "never emits for a counts-only record, which is the contradiction "
        "#9638's review raised"
    )


def test_an_unrepresentable_count_is_stated_as_no_count_on_a_recapture():
    """Arrival order must not decide the number. A second capture over the same
    session that carries a count this column cannot hold must READ as "no count
    stated" — leaving the first capture's number untouched would serve a value
    the latest record contradicts (the same false claim as writing 0), so the
    upsert writes NULL in the `DO UPDATE` branch. The first record's statement
    omits the column entirely, which is what makes the omission safe on INSERT
    and stale on CONFLICT."""
    first = rp.mutations_for(
        {"type": "SessionRecorded", "id": "s1", "story_arc": "the arc",
         "turn_count": 2},
        org_id=ORG, graph_id=GRAPH)
    second = rp.mutations_for(
        {"type": "SessionRecorded", "id": "s1", "story_arc": "the arc",
         "turn_count": 2 ** 31},
        org_id=ORG, graph_id=GRAPH)
    insert_first = next(x for x in first if rr.NARRATIVE_TABLE in x.sql)
    insert_second = next(x for x in second if rr.NARRATIVE_TABLE in x.sql)
    assert "turn_count" in insert_first.sql.split("DO UPDATE")[0]
    assert "turn_count = NULL" not in insert_first.sql
    # The re-capture states no count: NULL in the conflict branch, and no stale
    # parameter re-asserting one.
    assert "turn_count = NULL" in insert_second.sql
    assert "turn_count" not in insert_second.sql.split("DO UPDATE")[0]


def test_a_usable_count_is_projected_even_beside_an_unusable_one():
    record = {"type": "SessionRecorded", "id": "s1",
              "capture_redactions": 5, "turn_count": "not-a-count"}
    statements = _narrative_statements(record)
    assert len(statements) == 1
    assert 5 in statements[0].params
    assert "turn_count" not in _set_columns(statements[0])


def test_a_non_text_narrative_is_not_projected_and_does_not_raise():
    """A non-string `story_arc` is an absence here, not a failure.

    The carrier's OWN `story_arc` is a TEXT column (`public.event`,
    `20261012000001_record_event_stream.sql`), and `_jsonb_or_scalar`
    `json.dumps`-es a list/dict into it — so the carrier's write SUCCEEDS with a
    serialised (non-prose) value. This home must not turn that into a failure;
    it projects nothing, because a non-string is not §9.1 prose."""
    record = {"type": "EventRecorded", "eventId": "e1", "sessionId": "s",
              "story_arc": ["not", "prose"]}
    statements = _narrative_statements(record)
    assert statements == []


def test_a_count_larger_than_the_column_accepts_is_an_absence_not_an_overflow():
    """THE OTHER HALF OF "MUST NOT FAIL THE CARRIER". `session.capture_redactions`
    is jsonb and accepts an unbounded integer; this table's INTEGER does not, and
    an `integer out of range` raised inside the carrier's transaction would take
    the session row AND the journal position with it. So the bound is the
    COLUMN's, not just `>= 0`."""
    record = {"type": "SessionRecorded", "id": "s1",
              "capture_redactions": 2 ** 31, "turn_count": 3}
    statements = _narrative_statements(record)
    assert len(statements) == 1
    assert 2 ** 31 not in statements[0].params
    assert "capture_redactions" not in _set_columns(statements[0])
    assert 3 in statements[0].params


def test_a_deleted_session_takes_its_narrative_with_it():
    """The record's own delete path (`hosted_api._delete_session_sync`) journals
    one `EntityMutated` (`op=delete`) per removed turn Point plus one
    `SessionDeleted`, whose declared mutation removes the session row and its
    `session:` Source stub. The narrative row must go with them — it is keyed on
    `session_id` alone, so a survivor would be served by §9.4's verbatim path for
    a session the graph no longer has, and a same-id re-capture would inherit the
    deleted incarnation's narrative."""
    statements = rp.mutations_for({"type": "SessionDeleted", "id": "s1"},
                                 org_id=ORG, graph_id=GRAPH)
    deletes = [s for s in statements
               if f"DELETE FROM public.{rr.NARRATIVE_TABLE}" in s.sql]
    assert len(deletes) == 1
    assert deletes[0].params == (ORG, GRAPH, "s1")
    # It still removes the session row itself (the declared mutation).
    assert any("DELETE FROM public.session " in s.sql for s in statements)


# ── 5. The read side: the record, and only the record ────────────────────────


class _Cursor:
    def __init__(self, conn, sql="", params=()):
        self._conn = conn
        self._rows: list = []
        for marker, rows in conn.rows_by_marker.items():
            if marker in sql:
                self._rows = list(rows)
                break

    def execute(self, sql, params=()):
        self._conn.executed.append((sql, tuple(params)))
        self._rows = []
        for marker, rows in self._conn.rows_by_marker.items():
            if marker in sql:
                self._rows = list(rows)
                break
        return self

    def fetchall(self):
        return list(self._rows)


class _Conn:
    """A DB-API shaped double that records every statement it is given."""

    def __init__(self, **rows_by_marker):
        self.rows_by_marker = rows_by_marker
        self.executed: list[tuple[str, tuple]] = []

    def cursor(self):
        return _Cursor(self)


def test_the_reader_serves_the_narrative_and_the_verbatim_turns():
    """The fixture rows are the REAL stored shape: a capture turn's `content` is
    framed `"[role] body"` (`sdk._capture_turn_frame`), so a reader that served
    the frame raw (or labelled it twice: `"user: [user] hello"`) would be
    invisible against an unframed fixture."""
    conn = _Conn(**{
        "public.session_narrative": [("the arc", "a capture", 7, 2)],
        "public.point": [
            ("s_9555_t0", "[user] first thing said", "user"),
            ("s_9555_t10", "[assistant] later thing said", "assistant"),
            ("s_9555_t2", "[user] second thing said", None),
        ],
    })
    text = rr.session_text(conn, org_id=ORG, graph_id=GRAPH,
                           session_id="s_9555")
    assert text is not None
    assert text.narrative == "the arc"
    assert text.summary == "a capture"
    assert text.redactions == 7
    assert text.turn_count == 2
    # Ordered by the id's own index — the key contract, not the row order a
    # database happened to return.
    assert [t.index for t in text.turns] == [0, 2, 10]
    # `content` is the STORED framed form (what was captured); `body` is the
    # un-framed text a consumer should read.
    assert text.turns[0].content == "[user] first thing said"
    assert text.turns[0].body == "first thing said"
    assert text.turns[0].speaker == "user"
    assert text.turns[1].speaker is None
    # Un-framed ONCE: the frame's role is not repeated beside `point.speaker`.
    assert "user: first thing said" in text.transcript
    assert "[user]" not in text.transcript
    # A turn with no `speaker` column still gets the frame's role.
    assert "user: second thing said" in text.transcript
    assert text.verbatim_available


def test_the_reader_never_touches_the_journal_or_invents_a_table():
    conn = _Conn(**{
        "public.session_narrative": [("the arc", None, 0, 0)],
        "public.point": [],
    })
    rr.session_text(conn, org_id=ORG, graph_id=GRAPH, session_id="s_9555")
    joined = " ".join(sql for sql, _ in conn.executed)
    assert "journal_event" not in joined, (
        "§9.1's text must be reproducible from the RECORD — reading the journal "
        "here defeats the point of the record being the system of record"
    )
    assert "falkor" not in joined.lower()
    # Every statement is scoped to the caller's tenant pair.
    for _sql, params in conn.executed:
        assert params[:2] == (ORG, GRAPH)


def test_an_absent_session_is_an_honest_absence():
    conn = _Conn(**{"public.session_narrative": [], "public.point": []})
    assert rr.session_text(conn, org_id=ORG, graph_id=GRAPH,
                           session_id="s_none") is None


def test_the_inverse_sentinel_is_not_served_as_a_speaker_label():
    """`"unknown"` is what the writer's inverse returns for un-framed text — and
    the writer stores it in `point.speaker` too (`_capture_turn_frame` emits
    `[unknown] …` for a role-less turn). Filtering it on one side only would
    serve every role-less turn as `"unknown: …"`, which is not verbatim text."""
    text = rr.SessionText(session_id="s", turns=(
        rr.Turn(turn_id="s_t0", index=0, content="[unknown] hi there",
                speaker="unknown"),
        rr.Turn(turn_id="s_t1", index=1, content="no frame at all",
                speaker=None),
    ))
    assert text.transcript == "hi there\n\nno frame at all"


def test_a_session_with_turns_and_no_narrative_yet_still_serves_the_turns():
    """The two halves arrive from different records in either order."""
    conn = _Conn(**{
        "public.session_narrative": [],
        "public.point": [("s_t0", "hello", None)],
    })
    text = rr.session_text(conn, org_id=ORG, graph_id=GRAPH, session_id="s")
    assert text is not None and text.narrative is None
    assert text.transcript == "hello"


def test_the_turn_shape_rule_does_not_cross_sessions():
    """The prefix alone is not the rule: a session whose id ends in `_t<i>`
    would otherwise match another session's turns (`sec` vs `sec_t0`), which is
    the bug `sdk._capture_turn_ids` records. The reader's regex is anchored."""
    sec = rr._turn_pattern("sec")
    assert re.match(sec, "sec_t0")
    assert not re.match(sec, "sec_t0_t0")
    assert not re.match(sec, "sec_extra_t0")
    assert re.match(rr._turn_pattern("a+b"), "a+b_t3")
    assert not re.match(rr._turn_pattern("a+b"), "aab_t3")


def test_a_turn_key_that_carries_no_index_is_a_loud_failure():
    # The SQL pattern would not return it; the direct call proves the refusal
    # rather than an invented order.
    with pytest.raises(rr.RecordReadError):
        rr._turn_index("s_tX", "s")


def test_a_digit_run_past_the_conversion_limit_is_the_typed_refusal():
    """`isdecimal()` being true does not imply `int()` parseable: a 5000-digit
    suffix matches the shape rule and would raise a bare `ValueError`, escaping
    this module's `RecordReadError` contract and taking the whole session read
    (narrative included) with it — the trap `sdk._capture_turn_ids` records."""
    with pytest.raises(rr.RecordReadError):
        rr._turn_index("s_t" + "9" * 5000, "s")


def test_the_source_verbatim_fetch_resolves_through_the_record_not_an_edge():
    """§9.4's link: `source.session_id` is a COLUMN, so it survives the graph
    being destroyed — which is the whole point of D7."""
    conn = _Conn(**{
        "public.source": [("s_9555",)],
        "public.session_narrative": [("the arc", "sum", 1, 1)],
        "public.point": [("s_9555_t0", "[user] verbatim", "user")],
    })
    text = rr.source_narrative(conn, org_id=ORG, graph_id=GRAPH,
                              url="session:s_9555")
    assert text is not None
    assert text.narrative == "the arc"
    assert text.transcript == "user: verbatim"
    # ⚠️ The lookup must probe the PRIMARY KEY, not the unindexed `url` column:
    # for a `:Source` the canonical key IS the url, so both hold the same string
    # while only `node_key` is indexed — `url = %s` would filter the tenant's
    # whole source set on §9.4's answer-time path. The double matches on a SQL
    # substring, so this is the only thing that can pin the choice.
    source_sqls = [sql for sql, _p in conn.executed if "public.source" in sql]
    assert source_sqls
    assert all("node_key = %s" in sql for sql in source_sqls)
    assert not any("url = %s" in sql for sql in source_sqls)


def test_a_source_with_no_session_is_an_honest_absence():
    conn = _Conn(**{"public.source": [("",)], "public.session_narrative": [],
                    "public.point": []})
    assert rr.source_narrative(conn, org_id=ORG, graph_id=GRAPH,
                               url="corpus://x") is None
