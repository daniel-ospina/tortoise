"""#4492 — the Postgres storage footprint + per-org analytics row-growth meter.

Covers ``tortoise/postgres_storage.py`` (the measurement wrapper) and pins its
two cross-boundary contracts:

* the RPC NAMES and the SQL PARAMETER NAMES actually match the migration text
  (a rename on either side would 404 at runtime and is invisible to the PGlite
  suite, which drives the SQL directly);
* every honesty caveat travels WITH the reading, including through
  ``as_dict()`` — the attribution boundary (bytes are database-grain, rows are
  per-org) and the retention finding (``analytics_events`` has no prune path).

The load-bearing properties, each pinned so it can fail:

* malformed payloads FAIL the reading (``ok=False``) rather than masquerading
  as a zero-byte database or an empty window;
* an internally inconsistent row-growth payload (``total_rows`` ≠ the sum of
  ``rows_by_org``) is refused, not published;
* the meter is FAIL-SOFT: a missing handle, a dead control plane or a bad
  bound returns ``ok=False`` and never raises;
* a NAIVE window bound is rejected (the server would silently reinterpret it);
* an absent table is ``None``/contributes nothing — never a fabricated zero.

Hermetic: no Docker, no network, no PGlite. The SQL itself is asserted by
``supabase/tests/20261015000001_postgres_storage_footprint.sql`` against the
real functions.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tortoise import postgres_storage as ps
from tortoise.postgres_storage import (
    ATTRIBUTION_NOTE,
    FOOTPRINT_RPC,
    FOOTPRINT_TABLES,
    PRECISION_NOTE,
    RETENTION_FINDING,
    ROW_GROWTH_RPC,
    measure_postgres_storage,
    parse_row_growth,
    parse_storage_snapshot,
    read_analytics_row_growth,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATION = (REPO_ROOT / "supabase" / "migrations"
             / "20261015000001_postgres_storage_footprint.sql")

SNAPSHOT = {
    "database_bytes": 1_000_000,
    "captured_at": "2026-05-01T00:00:00+00:00",
    "tables": [
        {"name": "analytics_events", "total_bytes": 400_000,
         "heap_bytes": 300_000, "index_bytes": 100_000, "live_rows": 5000},
        {"name": "metering_records", "total_bytes": 100_000,
         "heap_bytes": 60_000, "index_bytes": 40_000, "live_rows": 700},
        {"name": "organizations", "total_bytes": 20_000,
         "heap_bytes": 12_000, "index_bytes": 8_000, "live_rows": 12},
    ],
}

GROWTH = {
    "since": "2026-05-01T00:00:00+00:00",
    "until": "2026-05-02T00:00:00+00:00",
    "total_rows": 3,
    "rows_by_org": {"org_a": 2, "org_b": 1},
}


class _StubCP:
    """A control-plane double recording every ``rpc_value`` call."""

    def __init__(self, *, footprint=None, growth=None, raises=None):
        self._footprint = footprint
        self._growth = growth
        self._raises = raises
        self.calls: list[tuple[str, dict]] = []

    def rpc_value(self, fn, body=None):
        self.calls.append((fn, dict(body or {})))
        if self._raises is not None:
            raise self._raises
        if fn == FOOTPRINT_RPC:
            return self._footprint
        if fn == ROW_GROWTH_RPC:
            return self._growth
        raise AssertionError(f"unexpected RPC {fn!r}")


# ── The SQL ↔ Python contract ──────────────────────────────────────────────

def test_the_public_api_is_the_documented_set():
    assert set(ps.__all__) == {
        "ATTRIBUTION_NOTE", "FOOTPRINT_RPC", "FOOTPRINT_TABLES",
        "PRECISION_NOTE", "RETENTION_FINDING", "ROW_GROWTH_RPC",
        "AnalyticsRowGrowth", "PostgresStorageReading", "TableFootprint",
        "measure_postgres_storage", "parse_row_growth", "parse_storage_snapshot",
        "read_analytics_row_growth",
    }


def test_both_rpc_names_match_the_migration_text():
    sql = MIGRATION.read_text()
    assert f"FUNCTION public.{FOOTPRINT_RPC}(" in sql
    assert f"FUNCTION public.{ROW_GROWTH_RPC}(" in sql


def test_the_row_growth_bound_parameter_names_match_the_sql():
    """A renamed SQL parameter makes PostgREST 404 the call (PGRST202)."""
    sql = MIGRATION.read_text()
    assert "p_since timestamptz" in sql
    assert "p_until timestamptz DEFAULT NULL" in sql
    cp = _StubCP(growth=GROWTH)
    since = datetime(2026, 5, 1, tzinfo=UTC)
    until = since + timedelta(days=1)
    read_analytics_row_growth(cp, since=since, until=until)
    fn, body = cp.calls[-1]
    assert fn == ROW_GROWTH_RPC
    assert set(body) == {"p_since", "p_until"}
    assert body["p_since"] == since.isoformat()
    assert body["p_until"] == until.isoformat()


def test_open_ended_window_sends_a_null_until():
    cp = _StubCP(growth={**GROWTH, "until": None})
    read_analytics_row_growth(cp, since="2026-05-01T00:00:00+00:00")
    assert cp.calls[-1][1] == {
        "p_since": "2026-05-01T00:00:00+00:00", "p_until": None}


# ── Footprint parsing ──────────────────────────────────────────────────────

def test_snapshot_parses_the_byte_breakdown():
    r = parse_storage_snapshot(SNAPSHOT)
    assert r.ok and r.error is None
    assert r.database_bytes == 1_000_000
    t = r.table("analytics_events")
    assert t is not None
    assert (t.total_bytes, t.heap_bytes, t.index_bytes, t.live_rows) == (
        400_000, 300_000, 100_000, 5000)
    assert r.tables_bytes == 520_000
    assert r.residual_bytes == 480_000


def test_absent_table_is_none_and_contributes_nothing():
    r = parse_storage_snapshot(SNAPSHOT)
    assert r.table("not_a_table") is None
    # An absent name is NOT a zero-byte table — it contributes nothing, and
    # only the present members of the subset are summed.
    assert r.bytes_for("analytics_events", "not_a_table") == 400_000
    assert r.bytes_for(*FOOTPRINT_TABLES) == 520_000


def test_a_table_sum_above_the_database_clamps_the_residual():
    """Two instants can disagree; a NEGATIVE residual is not a measurement."""
    r = parse_storage_snapshot({**SNAPSHOT, "database_bytes": 1})
    assert r.residual_bytes == 0


def test_the_caveats_travel_with_the_reading_and_through_as_dict():
    r = parse_storage_snapshot(SNAPSHOT)
    d = r.as_dict()
    assert d["postgres_precision_note"] == PRECISION_NOTE
    assert d["postgres_attribution_note"] == ATTRIBUTION_NOTE
    assert d["postgres_live_rows_are_estimates"] is True
    assert "per-org byte split would be an ALLOCATION" in d["postgres_attribution_note"]
    # and the whole reading serialises (a NaN/inf would raise here)
    json.dumps(d, allow_nan=False)


def test_a_json_string_payload_is_decoded_not_treated_as_a_failure():
    r = parse_storage_snapshot(json.dumps(SNAPSHOT))
    assert r.ok and r.database_bytes == 1_000_000


@pytest.mark.parametrize("mutate, why", [
    (lambda p: {k: v for k, v in p.items() if k != "database_bytes"},
     "no database_bytes"),
    (lambda p: {**p, "database_bytes": None}, "null database_bytes"),
    (lambda p: {**p, "database_bytes": True}, "boolean database_bytes"),
    (lambda p: {**p, "database_bytes": -1}, "negative database_bytes"),
    (lambda p: {**p, "database_bytes": 1.5}, "fractional database_bytes"),
    (lambda p: {**p, "tables": "nope"}, "tables not an array"),
    (lambda p: {**p, "tables": [{"name": ""}]}, "empty table name"),
    (lambda p: {**p, "tables": [{"name": "x", "total_bytes": 1,
                                 "heap_bytes": 1, "index_bytes": 1},
                                {"name": "x", "total_bytes": 2,
                                 "heap_bytes": 2, "index_bytes": 2}]},
     "duplicate table name"),
    (lambda p: {**p, "tables": [{"name": "x", "total_bytes": True,
                                 "heap_bytes": 1, "index_bytes": 1}]},
     "boolean byte field"),
])
def test_a_malformed_snapshot_fails_the_reading(mutate, why):
    with pytest.raises(ValueError):
        parse_storage_snapshot(mutate(SNAPSHOT))


def test_a_null_live_rows_is_absent_not_zero():
    r = parse_storage_snapshot({
        **SNAPSHOT,
        "tables": [{"name": "t", "total_bytes": 1, "heap_bytes": 1,
                    "index_bytes": 0, "live_rows": None}]})
    assert r.table("t").live_rows is None


# ── Row growth parsing ─────────────────────────────────────────────────────

def test_row_growth_parses_per_org_counts():
    g = parse_row_growth(GROWTH)
    assert g.ok and g.total_rows == 3
    assert g.rows_by_org == {"org_a": 2, "org_b": 1}
    assert g.for_org("org_a") == 2
    assert g.for_org("org_absent") == 0
    assert g.until == "2026-05-02T00:00:00+00:00"


def test_the_retention_finding_travels_with_the_growth_reading():
    g = parse_row_growth(GROWTH)
    assert g.retention_finding == RETENTION_FINDING
    assert "NO retention path" in g.retention_finding
    assert g.as_dict()["analytics_retention_finding"] == RETENTION_FINDING


def test_an_empty_window_is_a_measured_zero():
    g = parse_row_growth({"since": "2020-01-01T00:00:00+00:00", "until": None,
                          "total_rows": 0, "rows_by_org": {}})
    assert g.ok and g.total_rows == 0 and g.rows_by_org == {}
    assert g.for_org("org_a") == 0


@pytest.mark.parametrize("payload", [
    {"since": "2020-01-01T00:00:00+00:00", "total_rows": 5,
     "rows_by_org": {"a": 2}},                              # total ≠ sum
    {"total_rows": 0, "rows_by_org": {}},                   # no since
    {"since": "2020-01-01T00:00:00+00:00", "total_rows": 0,
     "rows_by_org": None},                                  # not an object
    {"since": "2020-01-01T00:00:00+00:00", "total_rows": True,
     "rows_by_org": {}},                                    # boolean total
    {"since": "2020-01-01T00:00:00+00:00", "total_rows": 1,
     "rows_by_org": {"": 1}},                               # empty org key
])
def test_a_malformed_or_inconsistent_growth_payload_is_refused(payload):
    with pytest.raises(ValueError):
        parse_row_growth(payload)


# ── Fail-soft callers ──────────────────────────────────────────────────────

def test_measure_is_fail_soft():
    assert measure_postgres_storage(None).ok is False
    r = measure_postgres_storage(_StubCP(raises=RuntimeError("cp down")))
    assert r.ok is False and "cp down" in r.error
    assert r.database_bytes == 0 and r.tables == ()


def test_measure_parses_a_well_formed_reply():
    r = measure_postgres_storage(_StubCP(footprint=SNAPSHOT))
    assert r.ok and r.table("analytics_events").total_bytes == 400_000


def test_read_growth_is_fail_soft_and_reports_the_window():
    assert read_analytics_row_growth(None, since="2026-05-01T00:00:00+00:00").ok is False
    g = read_analytics_row_growth(
        _StubCP(raises=RuntimeError("cp down")),
        since="2026-05-01T00:00:00+00:00", until="2026-05-02T00:00:00+00:00")
    assert g.ok is False and "cp down" in g.error
    # The failed reading still NAMES the window it was asked for.
    assert g.since == "2026-05-01T00:00:00+00:00"
    assert g.until == "2026-05-02T00:00:00+00:00"
    assert g.rows_by_org == {}


def test_a_naive_window_bound_is_refused():
    """The server would interpret a naive bound in its own timezone."""
    cp = _StubCP(growth=GROWTH)
    g = read_analytics_row_growth(cp, since=datetime(2026, 5, 1))
    assert g.ok is False and "timezone-aware" in g.error
    assert cp.calls == []  # refused BEFORE any RPC was issued


def test_a_missing_since_is_refused():
    cp = _StubCP(growth=GROWTH)
    g = read_analytics_row_growth(cp, since=None)
    assert g.ok is False
    assert cp.calls == []


# ── The real control-plane seam (local HTTP server, no network) ───────────

def test_the_real_seam_sends_the_documented_request_and_parses_it(monkeypatch):
    """Drives the REAL ``SupabaseControlPlane`` against a local HTTPServer.

    The stub tests above pin the parser; this pins the REQUEST the wrapper
    actually issues — the URL's RPC name, the SQL parameter names in the body,
    and the ``return=representation`` header a jsonb-returning function needs.
    A renamed function or parameter would 404/400 only in production, which
    neither the stub nor the PGlite suite can see.
    """
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen: list[tuple[str, dict, str]] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            # ``self.path`` carries the query string (``?select=*``), so the
            # RPC name is matched as a path SEGMENT, not with ``endswith``.
            route = self.path.split("?", 1)[0]
            seen.append((route, body, self.headers.get("Prefer", "")))
            if route.endswith(f"/{FOOTPRINT_RPC}"):
                payload = SNAPSHOT
            elif route.endswith(f"/{ROW_GROWTH_RPC}"):
                payload = GROWTH
            else:  # pragma: no cover - a wrong path never reaches here
                self.send_response(404)
                self.end_headers()
                return
            raw = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):  # silence
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        from tortoise.supabase_control import SupabaseControlPlane
        cp = SupabaseControlPlane(
            url=f"http://127.0.0.1:{server.server_port}", service_key="svc")

        r = measure_postgres_storage(cp)
        assert r.ok and r.table("analytics_events").total_bytes == 400_000
        route, body, prefer = seen[-1]
        assert route.endswith(f"/rest/v1/rpc/{FOOTPRINT_RPC}")
        assert body == {}
        assert "return=representation" in prefer

        g = read_analytics_row_growth(
            cp, since="2026-05-01T00:00:00+00:00")
        assert g.ok and g.for_org("org_a") == 2
        route, body, _ = seen[-1]
        assert route.endswith(f"/rest/v1/rpc/{ROW_GROWTH_RPC}")
        assert body == {"p_since": "2026-05-01T00:00:00+00:00", "p_until": None}
    finally:
        server.shutdown()
        server.server_close()


def test_the_real_seam_fails_soft_on_a_malformed_reply():
    """A 200 with an unusable body is a FAILED reading, not a raise."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = json.dumps({"unexpected": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):  # silence
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        from tortoise.supabase_control import SupabaseControlPlane
        cp = SupabaseControlPlane(
            url=f"http://127.0.0.1:{server.server_port}", service_key="svc")
        r = measure_postgres_storage(cp)
        assert r.ok is False and r.database_bytes == 0
        g = read_analytics_row_growth(cp, since="2026-05-01T00:00:00+00:00")
        assert g.ok is False and g.total_rows == 0
    finally:
        server.shutdown()
        server.server_close()
