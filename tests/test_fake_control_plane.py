"""FakeControlPlane type-fidelity locks — uuid filters (#1719) and
``timestamptz`` writes (#4243).

#1719: the fake previously string-compared filter values, so a non-UUID
literal in a ``user_id eq`` filter silently no-matched in CI while PostgREST
22P02'd in prod (the exact "CI green while prod 500s" gap #1719 fixes).
Default-on fidelity mirrors the real seam: a non-UUID value on a registered
uuid column raises the same RuntimeError("... HTTP 400") the production query
raises, so a future unsanitized call site fails the suite.

#4243: the fake also stored ANY JSON value into ANY column, so it could not
represent the Postgres ``timestamptz`` type contract — which is how a real
production failure passed CI (#4216). Stripe delivers the subscription period
bounds as Unix EPOCH INTS and ``update_org_billing`` PATCHes them into
``timestamptz`` columns; real Postgres rejects a bare JSON number, the fake
accepted it. The registry in ``fake_control_plane`` (derived from the
migrations, pinned here by ``test_registry_matches_the_migrations``) makes the
fake raise the same RuntimeError on that whole write class.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.fake_control_plane import (  # noqa: E402
    TIMESTAMPTZ_COLUMNS,
    FakeControlPlane,
)


def _fake() -> FakeControlPlane:
    return FakeControlPlane(tables={"org_memberships": []})


# ── #4243: ``timestamptz`` WRITE fidelity ───────────────────────────────────
# The real rejections these mirror, captured from a live PGlite 0.5.4 (PG 18)
# instance with all migrations applied, via the PostgREST mechanism itself
# (``json_populate_record``):
#   {"current_period_end": 1756348800}    -> date/time field value out of range: "1756348800"
#   {"current_period_end": 2024}          -> invalid input syntax for type timestamp with time zone: "2024"
#   {"current_period_end": true}          -> invalid input syntax for type timestamp with time zone: "true"
#   {"current_period_end": null}          -> ACCEPTED (SQL NULL)
#   {"current_period_end": "2026-11-15"}  -> ACCEPTED (ISO-8601)
# and the reject lands BEFORE the row changes, which is what the fake mirrors
# by checking ahead of the mutation.
_TSTZ_TABLE = "organizations"
_TSTZ_COLUMN = "current_period_end"
_SEEDED = "2026-01-01T00:00:00+00:00"


def _tstz_fake(**kwargs) -> FakeControlPlane:
    return FakeControlPlane(
        tables={_TSTZ_TABLE: [{"id": "t1", _TSTZ_COLUMN: _SEEDED}]}, **kwargs)


def test_numeric_write_to_timestamptz_column_raises_on_patch() -> None:
    """THE #4216 class: an epoch int PATCHed into a ``timestamptz`` column
    must raise, exactly as the real seam does."""
    f = _tstz_fake()
    with pytest.raises(RuntimeError, match="HTTP 400"):
        f.query(_TSTZ_TABLE, method="PATCH", filters=[("id", "eq", "t1")],
                json_body={_TSTZ_COLUMN: 1756348800})


def test_numeric_write_to_timestamptz_column_raises_on_post() -> None:
    """POST carries json_body too — the check is method-agnostic, as 22P02 is."""
    f = FakeControlPlane(tables={_TSTZ_TABLE: []})
    with pytest.raises(RuntimeError, match="HTTP 400"):
        f.query(_TSTZ_TABLE, method="POST",
                json_body={"id": "t2", "current_period_start": 1756348800})
    assert f.tables[_TSTZ_TABLE] == [], "a rejected POST must not create the row"


def test_numeric_write_is_rejected_before_any_mutation() -> None:
    """Prod rejects before the row changes (the timestamptz cast fails while
    building the record). A fake that raised AFTER mutating would leave a test
    with a half-written row and hide the real atomicity."""
    f = _tstz_fake()
    with pytest.raises(RuntimeError, match="HTTP 400"):
        f.query(_TSTZ_TABLE, method="PATCH", filters=[("id", "eq", "t1")],
                json_body={_TSTZ_COLUMN: 1756348800, "tier": "pro"})
    row = f.tables[_TSTZ_TABLE][0]
    assert row[_TSTZ_COLUMN] == _SEEDED
    assert "tier" not in row, "no field of a rejected body may be applied"


@pytest.mark.parametrize("value", [1756348800, 2024, 1756348800.5, True])
def test_every_number_shape_on_a_timestamptz_column_raises(value) -> None:
    """Postgres rejects EVERY JSON number, and a JSON boolean too (JSON ``true``
    is a distinct scalar; that Python ``bool`` is an ``int`` subclass must not
    let it slip through as a 1/0 epoch)."""
    f = _tstz_fake()
    with pytest.raises(RuntimeError, match="HTTP 400"):
        f.query(_TSTZ_TABLE, method="PATCH", filters=[("id", "eq", "t1")],
                json_body={_TSTZ_COLUMN: value})


@pytest.mark.parametrize("value", [_SEEDED, "2026-11-15", None])
def test_legitimate_timestamptz_values_are_written_verbatim(value) -> None:
    """The accepted shapes must be UNCHANGED — ISO-8601 (what the real seam
    returns and what ``update_org_billing`` now normalises to), a date-only
    literal, and JSON null (SQL NULL). The fake stores verbatim; it is not a
    timestamp parser."""
    f = _tstz_fake()
    f.query(_TSTZ_TABLE, method="PATCH", filters=[("id", "eq", "t1")],
            json_body={_TSTZ_COLUMN: value})
    assert f.tables[_TSTZ_TABLE][0][_TSTZ_COLUMN] == value


def test_numeric_value_on_an_unregistered_column_is_untouched() -> None:
    """Fidelity is COLUMN-scoped: ``max_users`` is an integer column, so a
    numeric write there is legitimate and must pass."""
    f = FakeControlPlane(tables={_TSTZ_TABLE: [{"id": "t1", "max_users": 1}]})
    f.query(_TSTZ_TABLE, method="PATCH", filters=[("id", "eq", "t1")],
            json_body={"max_users": 25})
    assert f.tables[_TSTZ_TABLE][0]["max_users"] == 25


def test_numeric_value_on_an_unregistered_table_is_untouched() -> None:
    """A table with no registered columns — and a column NAME that is a
    timestamptz on another table — must pass: the unit is (table, column)."""
    f = FakeControlPlane(tables={"something_else": []})
    f.query("something_else", method="POST",
            json_body={"id": "x", "current_period_end": 1756348800})
    assert f.tables["something_else"][0]["current_period_end"] == 1756348800


def test_body_without_a_registered_column_is_untouched() -> None:
    """A read (no body) and a body with no registered column are unaffected."""
    f = _tstz_fake()
    assert f.query(_TSTZ_TABLE, select=["id"],
                   filters=[("id", "eq", "t1")]) == [{"id": "t1"}]
    f.query(_TSTZ_TABLE, method="PATCH", filters=[("id", "eq", "t1")],
            json_body={"tier": "pro", "max_users": 5})
    assert f.tables[_TSTZ_TABLE][0]["tier"] == "pro"


def test_timestamptz_fidelity_escape_hatch() -> None:
    """``timestamptz_fidelity=False`` opts out — for doubles that must hold the
    raw epoch int the REGISTRY lane stores (``metering._anchor_instant`` reads
    both shapes). Mirrors ``uuid_fidelity=False``."""
    f = _tstz_fake(timestamptz_fidelity=False)
    f.query(_TSTZ_TABLE, method="PATCH", filters=[("id", "eq", "t1")],
            json_body={_TSTZ_COLUMN: 1756348800})
    assert f.tables[_TSTZ_TABLE][0][_TSTZ_COLUMN] == 1756348800


def test_the_two_fidelities_are_independent_switches() -> None:
    """Each guard covers its own type. Turning uuid fidelity off must NOT
    disarm the timestamptz guard, and vice versa — a shared switch would let a
    suite that legitimately needs one opt-out silently lose the other."""
    # uuid fidelity OFF, timestamptz still ON
    uuid_off = _tstz_fake(uuid_fidelity=False)
    assert uuid_off.query("org_memberships",
                          filters=[("user_id", "eq", "api")]) == []
    with pytest.raises(RuntimeError, match="HTTP 400"):
        uuid_off.query(_TSTZ_TABLE, method="PATCH", filters=[("id", "eq", "t1")],
                       json_body={_TSTZ_COLUMN: 1756348800})

    # timestamptz fidelity OFF, uuid still ON
    tstz_off = FakeControlPlane(tables={"org_memberships": []},
                                timestamptz_fidelity=False)
    assert tstz_off.query("org_memberships", method="PATCH",
                          filters=[("org_id", "eq", "t1")],
                          json_body={"role": "x"}) == []
    with pytest.raises(RuntimeError, match="HTTP 400"):
        tstz_off.query("org_memberships", filters=[("user_id", "eq", "api")])


def test_registry_covers_the_4216_columns() -> None:
    """The exact columns #4216 normalised are registered — otherwise the fake
    would keep accepting the write that 400'd in production."""
    for column in ("current_period_start", "current_period_end"):
        assert (_TSTZ_TABLE, column) in TIMESTAMPTZ_COLUMNS


# ── the registry is DERIVED from the migrations, and pinned ─────────────────
_MIGRATIONS = ROOT / "supabase" / "migrations"


def _timestamptz_columns_from_migrations() -> set[tuple[str, str]]:
    """Replay ``supabase/migrations/*.sql`` in filename order and return every
    ``(table, column)`` whose declared type is ``timestamptz``.

    This is a TEXT-LEVEL derivation, not a SQL parser: it reads CREATE TABLE
    bodies and ``ALTER TABLE ... ADD COLUMN`` clauses (multi-column lists
    included), applies ``RENAME COLUMN`` / ``RENAME TO`` transitively, and
    honours DROP COLUMN. (DROP TABLE is NOT handled — no migration issues one
    today, so it is latent; a future migration that drops a table would leave
    its columns in this set until the parser learns the shape.) Comments are
    stripped. Migrations are
    append-only (#1235), so the replay end state is the live schema — this set
    was verified to equal PGlite 0.5.4's ``information_schema.columns`` exactly
    (61 columns over all migrations) when the registry was introduced.

    It deliberately does NOT see function-local ``v_x timestamptz`` declarations
    or COMMENT text, because it only parses those two statement shapes.
    """
    create_re = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?"
        r"([a-z_][a-z0-9_]*)\s*\(", re.I)
    alter_re = re.compile(
        r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:public\.)?"
        r"([a-z_][a-z0-9_]*)([^;]*);", re.I)
    add_re = re.compile(
        r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_][a-z0-9_]*)\s+([^\s,;]+)",
        re.I)
    ren_col_re = re.compile(
        r"RENAME\s+COLUMN\s+([a-z_][a-z0-9_]*)\s+TO\s+([a-z_][a-z0-9_]*)", re.I)
    ren_tbl_re = re.compile(r"^\s*RENAME\s+TO\s+([a-z_][a-z0-9_]*)", re.I)
    drop_col_re = re.compile(
        r"DROP\s+COLUMN\s+(?:IF\s+EXISTS\s+)?([a-z_][a-z0-9_]*)", re.I)

    tables: dict[str, dict[str, str]] = {}
    for path in sorted(_MIGRATIONS.glob("*.sql")):
        sql = "\n".join(re.sub(r"--.*$", "", ln)
                        for ln in path.read_text().splitlines())

        # CREATE TABLE bodies — depth-count to the matching paren so nested
        # CHECK (...)/REFERENCES (...) cannot truncate the column list.
        for m in create_re.finditer(sql):
            depth, i = 0, m.end() - 1
            while i < len(sql):
                if sql[i] == "(":
                    depth += 1
                elif sql[i] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            cols = tables.setdefault(m.group(1), {})
            for part in _split_top_level_commas(sql[m.end():i]):
                cm = re.match(r"\s*([a-z_][a-z0-9_]*)\s+(timestamptz\b)", part, re.I)
                if cm:
                    cols[cm.group(1)] = cm.group(2).lower()

        # ALTER TABLE ... [ADD COLUMN a t, ADD COLUMN b t, RENAME COLUMN ...]
        for m in alter_re.finditer(sql):
            tname, rest = m.group(1), m.group(2)
            cols = tables.setdefault(tname, {})
            for cm in add_re.finditer(rest):
                cols[cm.group(1)] = cm.group(2).lower()
            for cm in ren_col_re.finditer(rest):
                if cm.group(1) in cols:
                    cols[cm.group(2)] = cols.pop(cm.group(1))
            tm = ren_tbl_re.match(rest)
            if tm:
                tables[tm.group(1)] = tables.pop(tname)
            for cm in drop_col_re.finditer(rest):
                cols.pop(cm.group(1), None)

    return {(t, c) for t, cols in tables.items()
            for c, typ in cols.items() if typ == "timestamptz"}


def _split_top_level_commas(body: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def test_registry_matches_the_migrations() -> None:
    """The registry must equal what the migrations declare — that is what makes
    it DERIVED rather than a hand-copied list that rots on the next migration.

    A forgotten column is the #4243 bug recurring (the fake silently accepts a
    write the real seam rejects); a stale column makes the fake report a
    failure production does not have."""
    derived = _timestamptz_columns_from_migrations()
    # Guard against a silent parse collapse: if the derivation broke it would
    # return a tiny set, and a vacuous equality would hide every column.
    assert len(derived) >= 50, (
        f"migration derivation returned only {len(derived)} columns — the "
        "parser or the migration layout changed; fix the derivation before "
        "trusting this pin")
    assert derived == TIMESTAMPTZ_COLUMNS


def test_registry_entries_are_all_declared_timestamptz() -> None:
    """Every registered column exists AND is declared timestamptz — no orphan
    entry left behind by a rename or a drop."""
    derived = _timestamptz_columns_from_migrations()
    assert derived >= TIMESTAMPTZ_COLUMNS


def test_non_uuid_user_id_eq_filter_raises() -> None:
    """A non-UUID literal on org_memberships.user_id raises the same
    RuntimeError surface PostgREST produces (22P02 → HTTP 400)."""
    f = _fake()
    with pytest.raises(RuntimeError, match="HTTP 400"):
        f.query("org_memberships", filters=[("user_id", "eq", "api")])


def test_non_uuid_user_id_eq_filter_raises_on_patch_and_delete() -> None:
    """Fidelity covers PATCH/DELETE too — those flow through _matches, and
    22P02 in prod is method-agnostic."""
    f = _fake()
    with pytest.raises(RuntimeError, match="HTTP 400"):
        f.query("org_memberships", method="PATCH",
                filters=[("user_id", "eq", "anon-abc")], json_body={"role": "x"})
    with pytest.raises(RuntimeError, match="HTTP 400"):
        f.query("org_memberships", method="DELETE",
                filters=[("user_id", "eq", "reg-xyz")])


def test_uuid_user_id_eq_filter_ok() -> None:
    """A real UUID filter behaves normally (returns rows / [])."""
    import uuid
    uid = str(uuid.uuid4())
    f = FakeControlPlane(tables={"org_memberships": [
        {"org_id": "t1", "user_id": uid, "role": "owner", "status": "active"},
    ]})
    rows = f.query("org_memberships", select=["role"],
                   filters=[("user_id", "eq", uid)])
    assert rows == [{"role": "owner"}]


def test_user_id_is_null_filter_ok() -> None:
    """is.null has no cast — unaffected by fidelity."""
    f = FakeControlPlane(tables={"org_memberships": [
        {"org_id": "t1", "user_id": None, "role": "member", "status": "active"},
    ]})
    rows = f.query("org_memberships", select=["role"],
                   filters=[("user_id", "is", None)])
    assert rows == [{"role": "member"}]


def test_non_uuid_on_unregistered_column_ok() -> None:
    """Only registered uuid columns are checked — other columns (text) are
    untouched by fidelity."""
    f = FakeControlPlane(tables={"api_keys": [
        {"org_id": "t1", "created_by": "anon-abc", "enabled": True},
    ]})
    rows = f.query("api_keys", select=["org_id"],
                   filters=[("created_by", "eq", "anon-abc")])
    assert rows == [{"org_id": "t1"}]


def test_uuid_fidelity_escape_hatch() -> None:
    """uuid_fidelity=False opts out (suites deliberately testing pre-#1511
    non-UUID data)."""
    f = FakeControlPlane(tables={"org_memberships": []}, uuid_fidelity=False)
    assert f.query("org_memberships",
                   filters=[("user_id", "eq", "api")]) == []


def test_unsupported_filter_op_raises_on_patch_and_delete() -> None:
    """#3665 review: ``_matches`` (PATCH/DELETE) must RAISE on an op it does
    not implement instead of silently treating it as "matches".

    The GET path already raises for an unsupported op; letting the
    PATCH/DELETE path ignore one makes the fake mutate MORE rows than the real
    client would (the ignored predicate drops out), so a test can pass against
    behaviour production does not have — the same "CI green while prod
    differs" class this file exists to lock down.

    The PATCH and DELETE branches pre-validate the whole filter list before
    evaluating it, so the cases below rest on ``_validate_filter_ops`` and not
    on ``_matches``' own final op check; removing either call makes the
    corresponding case return ``[]`` instead of raising.
    GREEN legitimate form: an op every branch covers (``eq``)."""
    f = FakeControlPlane(tables={"org_memberships": [
        {"org_id": "t1", "user_id": "00000000-0000-0000-0000-000000000001",
         "role": "member"},
    ]}, uuid_fidelity=False)
    with pytest.raises(ValueError, match="unsupported filter op"):
        f.query("org_memberships", method="DELETE",
                filters=[("org_id", "in", ["t1"])])

    # ...but the row scan is not the DELETE guard's only line of defence. With
    # no row to test (an EMPTY table) _matches is never called, so the guard
    # must come from the branch itself (#2642 re-review P2).
    with pytest.raises(ValueError, match="unsupported filter op"):
        FakeControlPlane(tables={"org_memberships": []},
                         uuid_fidelity=False).query(
            "org_memberships", method="DELETE",
            filters=[("org_id", "in", ["t1"])])

    # Same gap when a row exists but an EARLIER filter already failed: _matches
    # short-circuits before reaching the unsupported op, so the whole list must
    # be validated up front, not only as the scan reaches each predicate
    # (#2642 re-review P2).
    with pytest.raises(ValueError, match="unsupported filter op"):
        f.query("org_memberships", method="DELETE",
                filters=[("org_id", "eq", "NOMATCH"),
                         ("org_id", "in", ["t1"])])

    # The guard is NOT scoped to a non-empty body: the empty-body early
    # return scans no rows, so without an explicit pre-return validation it
    # carried the query past ``_matches`` and returned ``[]`` with no error
    # (#2642 re-review P2). An op the fake cannot model must raise whether or
    # not a body is supplied.
    with pytest.raises(ValueError, match="unsupported filter op"):
        f.query("org_memberships", select=["org_id"], method="PATCH",
                filters=[("org_id", "in", ["t1"])], json_body={})

    # ...and the DELETE-branch validation must NOT reach POST, which ignores
    # ``filters`` (it inserts the body). Hoisting ``_validate_filter_ops`` to
    # the top of ``_query_impl`` would newly reject this query — the placement
    # the #2642 re-review P2 prescription called out explicitly.
    assert f.query("org_memberships", method="POST",
                   filters=[("org_id", "in", ["t1"])],
                   json_body={"org_id": "t1", "user_id": "u"}) == [
        {"org_id": "t1", "user_id": "u"}]

    # the supported-op control: the same PATCH/DELETE path still works
    assert f.query("org_memberships", method="DELETE",
                   filters=[("org_id", "eq", "t1")]) == []


# ── #4037: PostgREST `order` grammar fidelity ─────────────────────────────

def _order_fake() -> FakeControlPlane:
    return FakeControlPlane(tables={"events": [
        {"id": "old", "created_at": "2026-01-01T00:00:00+00:00"},
        {"id": "new", "created_at": "2026-03-01T00:00:00+00:00"},
        {"id": "mid", "created_at": "2026-02-01T00:00:00+00:00"},
    ]})


def test_order_legacy_dash_prefix_raises() -> None:
    """#4037: the fake used to ACCEPT ``-created_at`` (``order.lstrip("-")``)
    while PostgREST answers PGRST100 / HTTP 400. That inverted dialect is why
    ``SupabaseAbuseStore``'s invalid order stayed green in CI while 400ing in
    prod (Stage-2 suspension never ran; ``/v1/team/alerts`` was always empty).

    REDs on: reverting the order handling to ``order.lstrip("-")`` /
    ``order.startswith("-")``. This is the SOLE witness for the double —
    under the old dialect every fake-backed integration test still passed."""
    f = _order_fake()
    with pytest.raises(RuntimeError, match="HTTP 400"):
        f.query("events", order="-created_at")


def test_order_dot_desc_sorts_newest_first() -> None:
    """The valid ``col.desc`` form must actually order. The old dialect looked
    up a column literally named ``created_at.desc`` → ``None`` → every key
    equal → silent no-op, so this ordering was never exercised.

    REDs on: reverting to the ``lstrip("-")`` dialect (no reorder) or
    dropping the ``.desc`` direction."""
    f = _order_fake()
    rows = f.query("events", select=["id"], order="created_at.desc")
    assert [r["id"] for r in rows] == ["new", "mid", "old"]


def test_order_bare_column_and_dot_asc_ascend() -> None:
    f = _order_fake()
    assert [r["id"] for r in f.query(
        "events", select=["id"], order="created_at")] == ["old", "mid", "new"]
    assert [r["id"] for r in f.query(
        "events", select=["id"], order="created_at.asc")] == [
            "old", "mid", "new"]


def test_order_null_placement_follows_postgres_defaults() -> None:
    """Postgres places NULLs LAST on ``asc`` and FIRST on ``desc``; the old
    key (``value or ""``) was the inverse in both directions. Explicit
    ``nullsfirst``/``nullslast`` override."""
    f = FakeControlPlane(tables={"events": [
        {"id": "null", "created_at": None},
        {"id": "a", "created_at": "2026-01-01T00:00:00+00:00"},
    ]})
    assert [r["id"] for r in f.query(
        "events", select=["id"], order="created_at")] == ["a", "null"]
    assert [r["id"] for r in f.query(
        "events", select=["id"], order="created_at.desc")] == ["null", "a"]
    assert [r["id"] for r in f.query(
        "events", select=["id"], order="created_at.nullsfirst")] == [
            "null", "a"]
    assert [r["id"] for r in f.query(
        "events", select=["id"], order="created_at.desc.nullslast")] == [
            "a", "null"]


def test_order_by_a_missing_column_is_refused_like_select_and_filter() -> None:
    """Ordering by an absent column is the SAME PostgREST rejection as the
    ``select``/``filter`` drift (#1001/#302): real PostgREST 400s on an
    undefined column rather than returning the rows unordered. Left accepted,
    it is the #4037 mask again — an invalid order term that 400s in production
    and stays invisible in CI, with a fail-soft consumer reading ``rows[0]``."""
    f = FakeControlPlane(
        tables={"events": [
            {"id": "a", "created_at": "2026-01-01T00:00:00+00:00"}]},
        missing_columns={"events": {"ghost"}})
    with pytest.raises(RuntimeError, match="HTTP 400"):
        f.query("events", select=["id"], order="ghost.desc")
    # Not a blanket refusal: the declared column still orders.
    assert [r["id"] for r in f.query(
        "events", select=["id"], order="created_at.desc")] == ["a"]


def test_order_multi_term_is_stable() -> None:
    """``a.desc,b.asc`` — ties on ``a`` resolve by ``b``, and the relative
    order of ``a``-ties is preserved (successive stable per-term sorts)."""
    f = FakeControlPlane(tables={"events": [
        {"id": "1", "g": "x", "n": 2},
        {"id": "2", "g": "y", "n": 5},
        {"id": "3", "g": "x", "n": 1},
    ]})
    rows = f.query("events", select=["id"], order="g.desc,n.asc")
    assert [r["id"] for r in rows] == ["2", "3", "1"]


@pytest.mark.parametrize("term", ["-x", "a b", "a.desc.desc",
                                  "a.asc.nullslast.desc", "a..b", "a.desc."])
def test_order_unparseable_term_raises(term: str) -> None:
    """#4037: an unparseable order term must RAISE, never silently no-op —
    a permissive fallback is the mask this issue removes.

    REDs on: any permissive fallback (the old ``lstrip("-")`` dialect, or
    "accept if the leftmost segment names a known column")."""
    f = _order_fake()
    with pytest.raises(RuntimeError, match="HTTP 400"):
        f.query("events", order=term)


def test_order_validation_is_method_agnostic() -> None:
    """A malformed order 400s regardless of method (PGRST100 is a query-string
    parse error), so the parse must precede the PATCH/DELETE dispatch, exactly
    as the 22P02 uuid check does.

    REDs on: moving the parse into the GET branch only."""
    f = _order_fake()
    with pytest.raises(RuntimeError, match="HTTP 400"):
        f.query("events", method="PATCH", order="-created_at",
                filters=[("id", "eq", "old")], json_body={"id": "old"})


def test_falsy_order_is_accepted_like_the_real_seam() -> None:
    """``None`` and ``""`` are dropped by the real client
    (``supabase_control.query`` guards ``if order:``), so the double must not
    400 on them — that would be a false refusal the wire never sees."""
    f = _order_fake()
    assert len(f.query("events", order=None)) == 3
    assert len(f.query("events", order="")) == 3


@pytest.mark.parametrize("order", ["created_at", "created_at.asc",
                                   "created_at.desc", "deleted_at",
                                   "created_at.nullsfirst",
                                   "created_at.desc.nullslast"])
def test_every_order_form_used_in_the_repo_is_accepted(order: str) -> None:
    """False-refusal guard: every order spelling this repo transmits — and
    the optional modifiers the issue names — must parse. ``supabase_control``
    sends ``created_at`` / ``created_at.asc`` / ``deleted_at``; ``abuse.py``
    and ``hosted_api.py`` send ``created_at.desc``."""
    _order_fake().query("events", order=order)  # must not raise


def test_patch_empty_body_makes_no_updates_and_returns_no_representation() -> None:
    """#2642 re-review P2 — the PATCH empty-body branch is load-bearing.

    PostgREST "makes no updates" for a bodyless PATCH, so the representation
    is ``[]`` even when the filter matches (UpdateSpec.hs:
    ``PATCH /items?select=id`` + ``{}`` → ``[]``). Without that branch the
    fake returns the MATCHED row's representation, which is precisely the
    fake/production divergence that hid the empty-body 404 — so the branch
    must be pinned by a test, not merely exercised when the seam fix is
    absent.

    REDs on: deleting the ``if not json_body: return []`` branch (case 1
    then returns ``[{"id": "A"}]`` instead of ``[]``)."""
    def _cp() -> FakeControlPlane:
        return FakeControlPlane(tables={"t": [{"id": "A", "org_id": "O"}]})

    match: list[tuple[str, str, object]] = [("org_id", "eq", "O"), ("id", "eq", "A")]

    # 1. empty body + select, filter MATCHES → no representation, no mutation.
    #    This is the assertion that discriminates the empty-body branch.
    cp = _cp()
    assert cp.query("t", select=["id"], method="PATCH", filters=match, json_body={}) == []
    assert cp.tables["t"] == [{"id": "A", "org_id": "O"}]

    # 2. empty body, select-less → still no representation
    assert _cp().query("t", method="PATCH", filters=match, json_body={}) == []

    # 3. non-empty body, select-less, filter MATCHES → no representation
    #    (PostgREST echoes a representation only when ``?select=`` is given)
    cp = _cp()
    assert cp.query("t", method="PATCH", filters=match, json_body={"org_id": "O"}) == []
    assert cp.tables["t"] == [{"id": "A", "org_id": "O"}]

    # 4. non-empty body + select, filter does NOT match → no representation
    #    (a genuinely non-matching filter, so this is empty for the ordinary
    #    reason — it guards against the empty-body branch ever being widened)
    nomatch: list[tuple[str, str, object]] = [("org_id", "eq", "OTHER")]
    cp = _cp()
    assert cp.query("t", select=["id"], method="PATCH", filters=nomatch,
                    json_body={"org_id": "B"}) == []
    assert cp.tables["t"] == [{"id": "A", "org_id": "O"}]

    # 5. positive control — SAME shape as (1) but with a non-empty body: the
    #    filter matches and the updated row IS returned. This proves (1)'s
    #    ``[]`` came from the empty-body branch, not from a non-matching
    #    filter.
    cp = _cp()
    assert cp.query("t", select=["id"], method="PATCH", filters=match,
                    json_body={"org_id": "O"}) == [{"id": "A"}]
    assert cp.tables["t"] == [{"id": "A", "org_id": "O"}]

    # 5b. and the match really MUTATES (not just echoes): a changing body
    #     lands on the stored row
    cp = _cp()
    assert cp.query("t", select=["id", "org_id"], method="PATCH", filters=match,
                    json_body={"org_id": "O2"}) == [{"id": "A", "org_id": "O2"}]
    assert cp.tables["t"] == [{"id": "A", "org_id": "O2"}]
