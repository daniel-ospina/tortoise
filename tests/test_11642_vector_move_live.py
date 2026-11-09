"""#11642 — the record dense leg against a REAL pgvector Postgres.

⛔ WHY THIS FILE EXISTS, in the reviewer's words (#11660 review F10): the sibling
pins in `test_11642_vector_move.py` assert the CONSTANT
(``HNSW_ITERATIVE_SCAN == "strict_order"``) and the residual algebra, and they
say so honestly. But nothing anywhere ever EXECUTED
``record_vector.vector_search``: not the SQL, not the count, not the shortfall,
not the NULL-key drop, not ``_recency_sql``. Invert ``available > len(rows)`` to
``>=``, or delete the count query, and that suite stays green — i.e. the one
hazard this PR MEASURED (a filtered ANN returning 0 rows where 152 exist) was
asserted by a string.

So this file arms a real ``pgvector/pgvector:pg16`` server and asserts the
behaviour, and `test-record-postgres` runs it with the migration applied for
real (that job provisions the pgvector image precisely so this file can run —
the PGlite schema drill cannot, see `supabase/tests/pglite/validate.mjs`).

The four lanes it pins are the four the review named, plus the two the graph
branches get for free and SQL does not: the terminal-status exclusion (#689) and
the canonical operator identity (#943).

⚠️ Not one test here may SKIP conditionally: `test-record-postgres` fails the
job on ANY skip (#9551's leg gate), so a filesystem- or clock-dependent skip
would be a nondeterministic RED on a required context. The DSN gate is the one
exception and it is the job's own arming condition.
"""
from __future__ import annotations

import json
import os

import pytest

from tortoise import record_vector

#: The recorded tenant scope the seeded rows live under. Deliberately unique to
#: this file: `journal_event` is APPEND-ONLY by trigger, so a scope a sibling
#: test also writes would make "the leg returned N rows" unattributable.
ORG = "org_11642_live"
GRAPH = "graph_11642_live"

RECORD_DB_ENV = "TEST_RECORD_DB_URI"

DIM = 384


# ── plumbing ───────────────────────────────────────────────────────────────

def _conn():
    """A connection to a Postgres carrying the record migrations, or skip."""
    dsn = os.environ.get(RECORD_DB_ENV)
    if not dsn:
        pytest.skip(f"{RECORD_DB_ENV} not set — no Postgres for the dense leg")
    psycopg2 = pytest.importorskip(
        "psycopg2", reason="psycopg2-binary is the record's driver")
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    return conn


def _vec(hot: int, *, dim: int = DIM) -> str:
    """A jsonb-encodable embedding whose cosine order is decided by `hot`.

    ``hot`` is the index of the single non-zero element, so the vector is a unit
    basis vector and the cosine distance between two of them is 1.0 (orthogonal)
    or 0.0 (identical) — an ordering no float noise can reorder.
    """
    values = [0.0] * dim
    values[hot] = 1.0
    return json.dumps(values)


@pytest.fixture(scope="module")
def schema():
    """The record schema, applied by THIS tree's migrations.

    `_ensure_record_schema` is imported rather than re-implemented: it is the ONE
    place that knows the apply order, the `creates`-conditional entries and the
    re-apply set (#9602/#9800), and a second copy here would be a second answer
    to "what does a migrated container look like".
    """
    from tests.test_9078_record_written_by_production_path import _ensure_record_schema

    conn = _conn()
    _ensure_record_schema(conn)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
            "AND table_name='point' AND column_name='embedding_vec'")
        assert cur.fetchone(), (
            "the container has no `public.point.embedding_vec` — the vector "
            "migration did not apply, so every assertion here would report the "
            "graph fallback as a clean empty answer")
    return conn


@pytest.fixture()
def point_rows(schema):
    """A clean, uniquely-seeded tenant for one test, and its teardown."""
    conn = schema
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.organizations (id, name) VALUES (%s, %s) "
            "ON CONFLICT (id) DO NOTHING", (ORG, ORG))
        cur.execute(
            "INSERT INTO public.graphs (id, org_id, name) VALUES (%s, %s, %s) "
            "ON CONFLICT (id) DO NOTHING", (GRAPH, ORG, GRAPH))

        def _seed(node_id, *, embedding, status=None, outdated=None,
                  is_operator=None, op_type=None, updated_at=None,
                  created_at=None, kind=None):
            # The node IDENTITY first: `public.point`'s (org, graph, label,
            # key) FK is the #9881 key move, and a point without an identity
            # row is a row the record refuses (measured: 23503).
            cur.execute(
                "INSERT INTO public.node_identity (org_id, graph_id, "
                "node_label, node_key) VALUES (%s, %s, 'Point', %s) "
                "ON CONFLICT DO NOTHING",
                (ORG, GRAPH, node_id or "anon"))
            cur.execute(
                "INSERT INTO public.point (org_id, graph_id, node_key, id, "
                "status, outdated, embedding, is_operator, op_type, "
                "updated_at, created_at, point_kind) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)",
                (ORG, GRAPH, node_id or "anon", node_id, status, outdated,
                 embedding, is_operator, op_type, updated_at, created_at, kind))

        yield _seed


@pytest.fixture(autouse=True)
def _clean_tenant(schema):
    """Every test starts from a known tenant, and leaves none behind."""
    conn = schema
    with conn.cursor() as cur:
        for table in ("point", "node_identity"):
            cur.execute(
                f"DELETE FROM public.{table} WHERE org_id=%s AND graph_id=%s",
                (ORG, GRAPH))
    yield


def _unit(hot: int) -> list[float]:
    values = [0.0] * DIM
    values[hot] = 1.0
    return values


def _search_vec(entity_type="point", *, hot=0, limit=10,
                excluded_statuses=None):
    """One leg call against the armed container.

    Two things are passed EXPLICITLY and both are load-bearing:

    * the DSN — the leg's default resolution reads `TORTOISE_RECORD_DSN` (the
      production arming variable) and this job arms `TEST_RECORD_DB_URI`, so a
      test relying on the default would exercise the arming seam, not the SQL;
    * the APPLICATION role — `scope_connection` refuses a connection whose
      `current_user` can bypass RLS (#9600 cycle 2), and the provisioned DSN is
      the owner/superuser. `_app_role_dsn` is the sibling module's one spelling
      of that (a connect-time `options=-c role=`), imported rather than
      re-derived so the two files cannot disagree about which role is the app.
    """
    from tests.test_9078_record_written_by_production_path import _app_role_dsn

    dsn = _app_role_dsn(os.environ[RECORD_DB_ENV])
    scope = record_vector._Scope(org_id=ORG, graph_id=GRAPH)
    return record_vector.vector_search(
        scope, entity_type, _unit(hot), limit,
        record_dsn=dsn,
        excluded_statuses=excluded_statuses)


# ── the answer itself ──────────────────────────────────────────────────────

def test_the_sql_returns_ids_distances_and_recency_nearest_first(
        schema, point_rows):
    """The statement the two `queryNodes` sites became, executed.

    ⛔ Falsifiable: nothing else in this PR runs the SELECT at all. A typo in a
    column name, a `<=>` where `<->` belongs (a DIFFERENT metric — the graph
    index was COSINE), or a recency projection that drifts from
    `search_engine.recency_key_expr` is a failure this test is the only witness
    to.
    """
    point_rows("p_near", embedding=_vec(0), updated_at="2026-01-02")
    point_rows("p_far", embedding=_vec(1), updated_at="2026-01-01")

    rows = _search_vec("point", hot=0, limit=10)
    assert [row[0] for row in rows] == ["p_near", "p_far"], (
        f"cosine ordering is wrong (got {rows})")
    # distance, not similarity: the caller converts with `1 - d` (#5583), so a
    # perfect match must arrive as 0.0 and an orthogonal one as ~1.0.
    assert rows[0][1] == pytest.approx(0.0, abs=1e-6), rows[0]
    assert rows[1][1] == pytest.approx(1.0, abs=1e-6), rows[1]
    # The recency half rides the row so the caller's ONE ordering pass can use it.
    assert rows[0][2] == "2026-01-02", rows[0]


def test_a_non_384_embedding_is_stored_but_not_ann_indexed(schema, point_rows):
    """F8's population claim, executed: `record_embedding_vector` returns NULL.

    The leg filters on the DERIVED column, so a 768-wide vector (PR #3018 stores
    caller-supplied embeddings VERBATIM, so this is a live population) and a
    payload carrying a `null` are both invisible to the dense leg — stored, not
    ann-indexed. That is the documented semantics, and it is exactly why
    `run_vector_query` falls through to the graph on a zero-row record answer.
    """
    point_rows("p_ok", embedding=_vec(0))
    point_rows("p_wide", embedding=_vec(0, dim=768))
    bad = [0.0] * DIM
    bad[3] = None
    point_rows("p_null_bearing", embedding=json.dumps(bad))

    with schema.cursor() as cur:
        cur.execute(
            "SELECT id, embedding_vec IS NOT NULL FROM public.point "
            "WHERE org_id=%s AND graph_id=%s ORDER BY id", (ORG, GRAPH))
        derived = dict(cur.fetchall())
    assert derived == {"p_null_bearing": False, "p_ok": True, "p_wide": False}, (
        f"the generated column disagrees with its documented contract: {derived}")

    rows = _search_vec("point", hot=0, limit=10)
    assert [row[0] for row in rows] == ["p_ok"], rows


def test_the_schema_makes_a_null_join_key_UNREACHABLE(schema):
    """The NULL-key drop is defence-in-depth, not a lane a row can reach.

    `vector_search` drops a row whose key is NULL, for the same reason the graph
    leg's `WHERE n.<id_field> IS NOT NULL` exists: the key IS the join back to
    the node, and a `None` in the fused list is a de-reference waiting to happen.
    But the record cannot hold such a row — every served table's
    `chk_*_node_key_is_contract_key` requires its key column NOT NULL (#9881's
    key move: `node_key` IS the contract key) — so the filter is a backstop
    against a legacy row, and pretending otherwise with an invalid INSERT would
    be pinning a state the database refuses.

    ⛔ Measured, not assumed: this asserts the three constraints on the REAL
    schema, which is the guarantee the Python filter can only approximate.
    """
    expected = {"point": "id", "source": "url", "event": "event_id"}
    with schema.cursor() as cur:
        for table, column in expected.items():
            cur.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = %s", (f"chk_{table}_node_key_is_contract_key",))
            row = cur.fetchone()
            assert row, f"{table} has no contract-key CHECK"
            definition = row[0]
            assert f"{column} IS NOT NULL" in definition, (
                f"{table}'s contract-key CHECK does not require {column}: "
                f"{definition}")
            # …and it is the guard `record_vector` drops a NULL for, not a
            # different column.
            assert f"node_key = {column}" in definition, definition


# ── the shortfall assertion ────────────────────────────────────────────────

def test_a_COMPLETE_answer_never_raises_however_small_the_corpus(
        schema, point_rows):
    """The false-shortfall lane, executed — and the one an inverted `>` reds.

    Two rows exist and both come back. The leg counts the predicate's material
    only when the answer is SHORTER than the limit (the common case for a small
    tenant), finds `available == len(rows)`, and must NOT raise: an inverted
    `available >= len(rows)` turns every complete short answer on a small
    tenant into a reported shortfall, which is how a caller is trained to
    switch the assertion off.

    ⛔ The DISCRIMINATING half — that the count runs on the short path, and that
    a full window (`len(rows) == limit`) pays no count at all — is pinned
    without a database in
    `test_11642_vector_move.py::test_the_shortfall_compares_strictly_and_only_
    on_the_short_path`, because a live store cannot force a genuinely short
    answer deterministically (`hnsw.iterative_scan = strict_order` is the fix
    for exactly that, so a correct server will not produce one on demand).
    """
    point_rows("p_a", embedding=_vec(0))
    point_rows("p_b", embedding=_vec(1))

    short_of_limit = _search_vec("point", hot=0, limit=10)
    assert len(short_of_limit) == 2, short_of_limit

    exactly_the_limit = _search_vec("point", hot=0, limit=2)
    assert len(exactly_the_limit) == 2, exactly_the_limit


def test_the_shortfall_count_uses_the_SAME_predicate_as_the_read(
        schema, point_rows):
    """A terminal row is not "material the read owes" — the count mirrors it.

    Three rows exist, two are live, and the limit is generous. If the count
    ignored the status half it would report 3 available against 2 returned and
    raise — a false shortfall on a complete answer, on any store holding one
    retracted row. The read's completeness is the assertion; the count's value
    is read out of the failure message when one happens.
    """
    point_rows("p_live_a", embedding=_vec(0))
    point_rows("p_live_b", embedding=_vec(1))
    point_rows("p_retracted", embedding=_vec(2), status="retracted")

    rows = _search_vec("point", hot=0, limit=10)
    assert sorted(row[0] for row in rows) == ["p_live_a", "p_live_b"], (
        f"a terminal row changed either the answer or the count: {rows}")


# ── the two predicates the graph branches get for free ─────────────────────

def test_terminal_points_are_excluded_and_the_audit_opt_in_includes_them(
        schema, point_rows):
    """#689, executed: a retracted Point must not leak into vector results.

    The graph branches emit `_exclude_status_clause`; the record leg emits the
    SQL twin. The three rows below are the three arms of that rule: a terminal
    status, the legacy `outdated` flag (which `invalidate_point` sets while
    leaving status untouched), and the audit opt-in `()` which means "terminal
    rows IN".
    """
    point_rows("p_live", embedding=_vec(0))
    point_rows("p_retracted", embedding=_vec(1), status="retracted")
    point_rows("p_outdated", embedding=_vec(2), outdated=True)

    rows = _search_vec("point", hot=0, limit=10)
    assert [row[0] for row in rows] == ["p_live"], (
        f"a terminal row leaked into the dense leg: {rows}")

    opt_in = _search_vec("point", hot=0, limit=10, excluded_statuses=())
    assert sorted(row[0] for row in opt_in) == [
        "p_live", "p_outdated", "p_retracted"], (
        "`excluded_statuses=()` is the caller's terminal-rows-IN opt-in")
    assert len(opt_in) == 3


def test_a_hybrid_operator_is_served_by_the_canonical_identity(
        schema, point_rows):
    """#943, executed: an operator is `is_operator = true OR op_type IS NOT NULL`.

    A hybrid operator — `op_type` set, `is_operator` absent — is served by every
    other operator read in the repo (`ep.py`, `sdk.py`, `search_engine.py`). A
    record leg filtering on `is_operator IS TRUE` alone would silently miss it,
    and the miss is invisible: the graph leg keeps finding it, so only a
    comparison between the two legs can see the divergence.
    """
    point_rows("op_flag", embedding=_vec(0), is_operator=True,
               op_type="extract")
    point_rows("op_hybrid", embedding=_vec(1), op_type="extract")
    point_rows("p_claim", embedding=_vec(2))

    rows = _search_vec("operator", hot=0, limit=10)
    assert sorted(row[0] for row in rows) == ["op_flag", "op_hybrid"], (
        f"the record leg missed a canonical operator ({rows})")


def test_the_recency_key_is_the_CASE_not_a_bare_coalesce(schema, point_rows):
    """`search_engine.recency_key_expr` restated in SQL, executed.

    The CASE is load-bearing: a `coalesce(updated_at, created_at)` stops at the
    first NON-NULL, so an `updated_at` of `''` (the record's empty-string
    encoding, not NULL) would suppress a valid `created_at` and sort the row as
    OLDEST in the window while the tie-break called it NEWEST. Both rows below
    have the SAME distance, so the recency value is the only thing that differs.
    """
    point_rows("p_empty_updated", embedding=_vec(0), updated_at="",
               created_at="2026-05-01")
    point_rows("p_stale_updated", embedding=_vec(0), updated_at="2020-01-01",
               created_at="2020-01-01")

    rows = _search_vec("point", hot=0, limit=10)
    recency = {row[0]: row[2] for row in rows}
    assert recency["p_empty_updated"] == "2026-05-01", recency
    assert recency["p_stale_updated"] == "2020-01-01", recency


# ── the armed/unarmed contract, on a real server ───────────────────────────

def test_an_unserved_label_answers_None_not_empty(schema):
    """`object`/`subject` have no column, so the leg must NOT claim to have looked.

    `[]` means "served, asked, the filter selects nothing"; the caller takes its
    graph branch on `None`. Conflating them turns the label's real path (a
    brute-force graph scan) into an empty dense leg.
    """
    scope = record_vector._Scope(org_id=ORG, graph_id=GRAPH)
    from tests.test_9078_record_written_by_production_path import _app_role_dsn

    dsn = _app_role_dsn(os.environ[RECORD_DB_ENV])
    assert record_vector.vector_search(
        scope, "object", _unit(0), 10, record_dsn=dsn) is None
    assert record_vector.vector_search(
        scope, "subject", _unit(0), 10, record_dsn=dsn) is None
