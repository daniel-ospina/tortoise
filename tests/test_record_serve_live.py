"""#7970 lived — the record answers the anchored read, and the answers MATCH.

WHAT THIS PINS — S4/S5's parity clause and S9's shape, on real stores:

* **PARITY, MEASURED.** A Point is created through the production SDK (the CACHE
  side), projected into Postgres through the production projection
  (``record_projection.replay``, the write door the chokepoint calls), and then
  read back through ``record_serve.read_point`` (the RECORD side). The two
  property maps are compared by ``record_serve.parity_difference``, which fails
  in BOTH directions: a field the record does not answer and the schema's
  carve-out does not explain REDDENS, and so does a field the record answers
  that the cache does not have.
* **S9 — with the CACHE DESTROYED, the record still answers.** The Point is
  deleted from the graph, the cache read is made to RAISE if it is touched at
  all, and the same read still returns the same properties from the record. That
  is the epic's central claim, and the only test that would catch it being false.

WHY IT IS A SEPARATE FILE FROM `test_record_serve.py`. Those tests are DB-free so
they run in every Python lane. This one needs BOTH a live FalkorDB
(``TORTOISE_DB_URI``) and a Postgres carrying the record migrations
(``TEST_RECORD_DB_URI``), so it skips VISIBLY with its own reason rather than
passing vacuously — the #3339 rule. ⚠️ The two live halves are separate skips on
purpose: each names the one store that is missing, so an unarmed lane says which
one to provision instead of reporting a single opaque 'needs a database'.

⚠️ **BOTH LEGS SKIP IN EVERY CI LEG TODAY — this file has no CI coverage of
anything, and that is a measured fact, not a gap someone forgot.** No job
provides both stores: `test-record-postgres` (
`.github/workflows/python-ci.yml`) runs only the POSTGRES-ONLY record legs
(`tests/test_9078_record_written_by_production_path.py`,
`tests/test_9555_narrative_carrier.py`,
`tests/test_9664_capstone_connector_to_record.py`) and exports no
`TORTOISE_DB_URI` by design (`.github/actions/postgres-provision`, #9551), and
the FalkorDB jobs export no record DSN. So this file is registered in
`config/ci-surfaces.yml` so that it is CLASSIFIED (an unclassified test file
reds `integrity_problems`), NOT because a leg runs it. Until a cross-store job
exists it must be run BY HAND on a lane with both stores armed — measured there
2026-10-24: `2 passed in 4.23s` (parity clean, cache 11 fields / record 8; S9
answered `served_from=record` with the cache copy destroyed). Do not read a
green CI run as this file passing.

REUSED, NOT RE-DECLARED: ``TEST_RECORD_DB_URI``, its schema application, its
tenant seeding and the app-role DSN all come from
``tests/test_9078_record_written_by_production_path.py`` — the lane that already
owns "a record carrying these migrations, written by the production path". A
second declaration of any of them is how two record legs start testing two
different databases.

WHY THE READ CONNECTS AS THE APP ROLE. ``record_projection.scope_connection``
refuses a connection whose ``current_user`` can bypass RLS (R6, #9554), and
``TEST_RECORD_DB_URI`` is provisioned as the database's superuser — which is
every bypass at once. So the read connects the way production does, through
``_app_role_dsn``, and that refusal is itself part of what makes the read
honest: an unscoped read under RLS returns ZERO rows, which is exactly the false
absence this module promises never to invent.
"""
from __future__ import annotations

import json
import os

import pytest

from tests._live_utils import _skip_unless_live_uri
from tests.test_9078_record_written_by_production_path import (
    RECORD_DB_ENV,
    _app_role_dsn,
    _ensure_record_schema,
    _seed_tenant,
)
from tortoise import record_serve as rs

GRAPH_NAME = "test_7970_record_serve"
#: A tenant only this file uses, so a re-run is repeatable without clearing a
#: table another leg asserts a delta on (#9307's rule).
TENANT = ("org_7970_readpath", "g_7970_readpath")


def _record_admin():
    """The provisioning superuser: seeding and assertions (see #9078)."""
    dsn = os.environ.get(RECORD_DB_ENV)
    if not dsn:
        pytest.skip(
            f"{RECORD_DB_ENV} not set — no Postgres carrying the record "
            f"migrations for this lane")
    psycopg2 = pytest.importorskip(
        "psycopg2", reason="psycopg2-binary is the record's driver")
    conn = psycopg2.connect(dsn)
    return conn


def _record_app_dsn() -> str:
    dsn = os.environ.get(RECORD_DB_ENV)
    if not dsn:  # pragma: no cover - _record_admin has already skipped
        pytest.skip(f"{RECORD_DB_ENV} not set")
    return _app_role_dsn(dsn)


def _seed(conn) -> None:
    """The scope's two FK parents — plus the full-migrations shape's NOT NULL."""
    psycopg2 = pytest.importorskip("psycopg2")
    try:
        _seed_tenant(conn, TENANT)
    except psycopg2.errors.NotNullViolation:
        # A database carrying EVERY migration (not only the record's, as CI
        # provisions) declares `organizations.graph_name NOT NULL`; a record-only
        # database does not. Supply it rather than depend on which shape ran —
        # after clearing the aborted transaction the refusal left behind.
        conn.rollback()
        org, graph = TENANT
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.organizations (id, name, graph_name) "
                "VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
                (org, org, graph))
            cur.execute(
                "INSERT INTO public.graphs (id, org_id, name, namespace) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                (graph, org, graph, f"test-{graph}"))


def _sdk():
    """The CACHE side, on a live FalkorDB named for this test."""
    _skip_unless_live_uri()
    from tortoise.projection import FalkorProjection
    from tortoise.residency import clear_residency
    from tortoise.sdk import TortoiseSDK

    proj = FalkorProjection.from_uri(os.environ["TORTOISE_DB_URI"],
                                     graph_name=GRAPH_NAME)
    proj._wipe_all_nodes(confirm_destructive=True, operation="test-setup")
    clear_residency(proj.g)
    proj.close()
    return TortoiseSDK()


def _write_and_read_record(point_id: str, snapshot: dict) -> dict:
    """Write through the PRODUCTION projection; read back through the record."""
    from tortoise.record_projection import replay

    psycopg2 = pytest.importorskip("psycopg2")
    conn = psycopg2.connect(_record_app_dsn())
    try:
        report = replay(
            [{"type": "PointAdded", "ts": "2026-10-24T00:00:00Z",
              "id": point_id, "point": dict(snapshot)}],
            conn, org_id=TENANT[0], graph_id=TENANT[1])
        conn.commit()
        assert report.events == 1, report
        props = rs.read_point(conn, org_id=TENANT[0], graph_id=TENANT[1],
                              point_id=point_id)
    finally:
        conn.close()
    assert props is not None, (
        "the record answered nothing for a point its own projection just wrote")
    return props


def _prepare() -> None:
    """Migrate + seed the record scope (idempotent)."""
    conn = _record_admin()
    try:
        _ensure_record_schema(conn)
        _seed(conn)
        conn.commit()
    finally:
        conn.close()


def _fresh_point(sdk, key: str, content: str) -> str:
    point = sdk.create_point("claim", content, key=key, dedup=False)
    point_id = point["id"] if isinstance(point, dict) else point
    sdk.update_point(point_id, status="live")
    return point_id


def test_parity_a_record_served_read_equals_the_cache_answer():
    """S4/S5 — the same answer, with every difference EXPLAINED."""
    _prepare()
    sdk = _sdk()
    point_id = _fresh_point(sdk, "T7970-PARITY",
                            "the record serves anchored reads")
    cache_props = sdk.get_point(point_id)

    record_props = _write_and_read_record(point_id, cache_props)
    diff = rs.parity_difference(cache_props, record_props, label="Point")
    print("\nPARITY MEASUREMENT " + json.dumps(
        {"cache_fields": len(cache_props), "record_fields": len(record_props),
         "diff": diff.as_dict()}, default=str, sort_keys=True))
    assert diff.clean, diff.as_dict()

    # The carve-out, not a coincidence: the fields the record did not answer are
    # exactly the ones the SCHEMA refuses, and there are none outside that set.
    from tortoise.cache_projection import _refused

    assert set(diff.explained_missing) <= _refused("point")
    assert set(diff.explained_missing) == set(cache_props) - set(record_props)
    # And the record never carries a name it is forbidden to hold as authority.
    assert not (set(record_props) & _refused("point"))


def test_s9_the_record_answers_with_the_cache_destroyed():
    """S9 — delete the cache's copy; the record still answers, unchanged."""
    _prepare()
    sdk = _sdk()
    point_id = _fresh_point(sdk, "T7970-S9", "the record outlives the cache")
    cache_props = sdk.get_point(point_id)
    record_props = _write_and_read_record(point_id, cache_props)

    # DESTROY the cache's copy. The graph is now unable to answer this id.
    sdk._get_proj().g.query(
        "MATCH (p:Point {id:$id}) DETACH DELETE p", params={"id": point_id})
    assert sdk.get_point(point_id) == {}, "the cache copy was not destroyed"

    def _cache_was_touched():  # pragma: no cover - must never run here
        raise AssertionError("the cache was consulted while serving S9")

    answer = rs.serve_point(tenant=TENANT, point_id=point_id,
                            cache_answer=_cache_was_touched,
                            record_dsn=_record_app_dsn(), mode=rs.MODE_RECORD)
    assert answer.served_from == "record"
    assert answer.props == record_props, (
        "the record answer changed once the cache was gone — then it was never "
        "the record's answer")
    print("\nS9 " + json.dumps({"read": answer.read,
                                "served_from": answer.served_from,
                                "fields": sorted(answer.props)},
                               sort_keys=True))
