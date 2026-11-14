"""#12762 — per-org Postgres bytes MEASURED from what we write.

Covers ``tortoise/postgres_bytes.py`` (the meter + the audit wrapper), the
ledger writer that accumulates the figure (``metering.record_postgres_bytes_
written``) and the write-seam wiring (``record_writer._meter_written_bytes``).

⛔ THE FALSIFIER LIVES HERE (its Python half; the SQL half is
``supabase/tests/20261114000001_per_org_postgres_bytes.sql``, which runs the
real functions on a real engine). #12762 was filed on the premise that
``sum(pg_column_size(...))`` reports the 18-byte TOAST pointer for a moved-out
value. MEASURED (PG 16.9 / 17.12 / 18.3): it does not — it follows the pointer
and reports the stored size. So the pin that "fails under the naive
``pg_column_size`` sum" cannot be built; it would be green on both methods.

The pins here therefore test the SAME property against the methods that DO
fail, using figures captured from those engines:

* ``RETURNING`` (stored-state reads)
* ``TEST_NAIVE_RETURNING`` (the pre-TOAST datum) — REFUSED by the parser,
  because a payload cannot exceed the row that contains it;
* the row+chunks DOUBLE COUNT — REFUSED, same reason one level down.

Hermetic: no Docker, no network. Every engine figure below was measured
2026-11-14 and is quoted with its payload shape.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from datetime import UTC, datetime

from tortoise import metering
from tortoise import postgres_bytes as pb
from tortoise.postgres_bytes import (
    AUDIT_RPC,
    DEFAULT_EXCESS_BAND,
    METHOD_NOTE,
    RESIDUAL_NOTE,
    ROWS_RPC,
    measure_written_rows,
    parse_per_org_bytes,
    parse_written_rows,
    per_org_bytes,
    reconcile,
)

def _dt(day: str) -> datetime:
    """A UTC instant from an ISO date — the ledger's window bounds are real
    ``datetime``s, and a naive one is refused by the meter's own boundary."""
    return datetime.fromisoformat(day + "T00:00:00+00:00").astimezone(UTC)


REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATION = (REPO_ROOT / "supabase" / "migrations"
             / "20261114000001_per_org_postgres_bytes.sql")

# ── Engine figures, measured 2026-11-14 (PG 17.12; PG 16.9 and PGlite's 18.3
#    agree within rounding). Each case: the payload shape, its JSON text size,
#    the naive `RETURNING pg_column_size(payload)` figure (pre-TOAST, i.e. the
#    datum before the row is stored), and the STORED figures read back.
MEASURED = {
    "small": {"logical": 47, "returning": 59, "payload": 59, "row": 118,
              "chunks": 0, "chunk_bytes": 0},
    "4k_repeat": {"logical": 4_045, "returning": 4_059, "payload": 114,
                  "row": 174, "chunks": 0, "chunk_bytes": 0},
    "vector_1536": {"logical": 15_428, "returning": 24_606, "payload": 7_177,
                    "row": 7_241, "chunks": 4, "chunk_bytes": 7_177},
    "random_19k": {"logical": 19_245, "returning": 19_260, "payload": 19_256,
                   "row": 19_320, "chunks": 10, "chunk_bytes": 19_256},
}

#: A well-formed meter reply for the vector payload — what the SQL function
#: returns for the rows just written.
STORED_VECTOR = {
    "org_id": "org_v", "graph_id": "g_v", "rows": 1,
    "row_bytes": MEASURED["vector_1536"]["row"],
    "payload_bytes": MEASURED["vector_1536"]["payload"],
    "chunk_bytes": MEASURED["vector_1536"]["chunk_bytes"],
    "chunk_detail": True,
    "captured_at": "2026-11-14T00:00:00+00:00",
}

#: The naive reading for the SAME record: `INSERT … RETURNING
#: pg_column_size(payload)` reports the pre-TOAST datum. Shaped exactly as the
#: SQL function's reply would be, so only the containment guard can see it.
NAIVE_RETURNING = {
    **STORED_VECTOR,
    "row_bytes": MEASURED["vector_1536"]["returning"],      # 24,606
    "payload_bytes": MEASURED["vector_1536"]["returning"],  # 24,606
}


class _FakeCursor:
    def __init__(self, value):
        self.value = value
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchone(self):
        return (self.value,)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, value):
        self.cur = _FakeCursor(value)

    def cursor(self):
        return self.cur


# ══════════════════════════════════════════════════════════════════════════
# 1. THE FALSIFIER — the pointer does not fool the meter, the naive does not
#    get in.
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("shape", sorted(MEASURED))
def test_the_meter_counts_a_payload_at_its_full_stored_size_not_a_pointer(shape):
    """Every measured shape round-trips at its STORED size.

    The issue was filed on a ~100x undercount (the 18-byte pointer). The
    measured answer is the opposite: the stored size is what is reported, for
    the small AND the TOASTed shapes. An 18-byte reading anywhere here would be
    the defect the issue predicted and the engine does not produce.
    """
    m = MEASURED[shape]
    written = parse_written_rows({
        "org_id": "org_x", "graph_id": "g", "rows": 1,
        "row_bytes": m["row"], "payload_bytes": m["payload"],
        "chunk_bytes": m["chunk_bytes"], "chunk_detail": True,
        "captured_at": "",
    })
    assert written.row_bytes == m["row"]
    # Never the pointer's 18 bytes — that is the reading the issue predicted
    # and the engine does not produce.
    assert written.row_bytes != 18
    if m["chunks"]:
        # An OUT-OF-LINE payload is reported at its full stored size (within a
        # hundred bytes of the payload itself), which is the property the
        # issue's ~100x undercount would have violated.
        assert written.row_bytes > 1_000
    # The value is INSIDE the row figure: the containment the double count
    # breaks. (For the 4 KB compressible case the STORED size genuinely is 174
    # bytes — pglz compressed it inline — so this is not a "lost payload".)
    assert written.row_bytes >= written.chunk_bytes


def test_the_naive_returning_reading_is_CAUGHT_not_merely_documented():
    """⛔ THE FALSIFIER (Python half). The naive write-time method goes RED.

    `INSERT … RETURNING pg_column_size(payload)` is the natural "free"
    write-time measure. For our own record shape it reports 24,606 bytes for a
    payload that occupies 7,177 — a 3.4x OVER-count, in the direction that
    over-bills. It is internally well-formed (a row of that size is possible),
    so no parser can refuse it; the AUDIT is what catches it, and
    `reconcile` — the pairing this change exists to provide — calls it a
    defect rather than passing it through.
    """
    naive = parse_written_rows(NAIVE_RETURNING)
    stored = parse_written_rows(STORED_VECTOR)
    assert naive.row_bytes == MEASURED["vector_1536"]["returning"]   # 24,606
    assert stored.row_bytes == MEASURED["vector_1536"]["row"]       # 7,241

    # The audit is the ground truth the counter is checked against.
    audited = stored.row_bytes
    r = reconcile("org_v", metered_bytes=naive.row_bytes,
                  audited_bytes=audited)
    assert r.ok is False, "the naive method must GO RED against the audit"
    assert r.excess_bytes == naive.row_bytes - stored.row_bytes
    assert "ABOVE the band" in r.detail

    # The correct method reconciles clean.
    assert reconcile("org_v", metered_bytes=stored.row_bytes,
                     audited_bytes=audited).ok is True


def test_a_payload_bigger_than_its_row_is_refused():
    """The mixed reading: a stored row with a PRE-TOAST payload in it.

    A stored row CONTAINS its payload, so this is internally impossible — and
    it is exactly the shape a half-migrated meter produces (the true row figure
    beside `RETURNING`'s payload figure). Refusing it is what keeps the pin a
    guard: a parser that accepted it would publish the over-count as measured.
    """
    mixed = {**STORED_VECTOR,
             "row_bytes": MEASURED["vector_1536"]["row"],          # 7,241
             "payload_bytes": MEASURED["vector_1536"]["returning"]}  # 24,606
    with pytest.raises(ValueError, match="PRE-TOAST"):
        parse_written_rows(mixed)


def test_the_row_plus_chunks_double_count_is_REFUSED():
    """The live trap: adding chunk bytes to the row figure double counts."""
    doubled = {**STORED_VECTOR, "chunk_bytes": STORED_VECTOR["row_bytes"] + 1}
    with pytest.raises(ValueError, match="double-count"):
        parse_written_rows(doubled)

    # And the AUDIT refuses the same shape per org.
    audit = {
        "orgs": [{"org_id": "o", "rows": 1, "row_bytes": 7_241,
                  "payload_bytes": 7_177, "chunk_bytes": 7_242}],
        "rows": 1, "attributed_bytes": 7_241,
        "residual": {"residual_bytes": 10},
    }
    with pytest.raises(ValueError, match="double count"):
        parse_per_org_bytes(audit)


# ══════════════════════════════════════════════════════════════════════════
# 2. The meter's DB read
# ══════════════════════════════════════════════════════════════════════════


def test_measure_written_rows_reads_the_rows_this_segment_wrote():
    conn = _FakeConn(json.dumps(STORED_VECTOR))
    written = measure_written_rows(
        conn, org_id="org_v", graph_id="g_v", seqs=range(1, 4))
    assert written.row_bytes == 7_241
    sql, params = conn.cur.calls[0]
    assert ROWS_RPC in sql
    assert params == ("org_v", "g_v", [1, 2, 3])


def test_measure_written_rows_is_TOTAL():
    """A meter must never fail a write: every fault path returns None."""
    assert measure_written_rows(None, org_id="o", graph_id="g", seqs=[1]) is None
    assert measure_written_rows(_FakeConn("{}"), org_id="o", graph_id="g",
                                seqs=[1]) is None  # malformed
    assert measure_written_rows(_FakeConn(STORED_VECTOR), org_id="o",
                                graph_id="g", seqs=[]) is None  # empty

    class Boom(_FakeConn):
        def cursor(self):
            raise RuntimeError("connection in a bad state")

    assert measure_written_rows(Boom(None), org_id="o", graph_id="g",
                                seqs=[1]) is None


def test_chunk_detail_unavailable_is_NULL_never_zero():
    """PG < 17 cannot attribute chunks; that must read as absent, not empty."""
    written = parse_written_rows({
        **STORED_VECTOR, "chunk_bytes": None, "chunk_detail": False})
    assert written.chunk_bytes is None
    assert written.chunk_detail is False
    # A zero would claim "there are no chunks" — a different, false statement.
    assert written.chunk_bytes != 0


def test_a_missing_chunk_detail_flag_is_refused():
    payload = dict(STORED_VECTOR)
    del payload["chunk_detail"]
    with pytest.raises(ValueError, match="chunk_detail"):
        parse_written_rows(payload)


# ══════════════════════════════════════════════════════════════════════════
# 3. The audit
# ══════════════════════════════════════════════════════════════════════════

AUDIT = {
    "captured_at": "2026-11-14T00:00:00+00:00",
    "orgs": [
        {"org_id": "org_a", "rows": 2, "row_bytes": 19_320,
         "payload_bytes": 19_256, "chunk_bytes": 19_256},
        {"org_id": "org_b", "rows": 1, "row_bytes": 174,
         "payload_bytes": 114, "chunk_bytes": 0},
    ],
    "rows": 3,
    "attributed_bytes": 19_494,
    "residual": {
        "heap_relation_bytes": 16_384, "index_bytes": 8_192,
        "toast_relation_bytes": 32_768, "total_relation_bytes": 57_344,
        "residual_bytes": 37_850, "residual_ratio": 0.66,
        "toast_chunk_data_bytes": 19_256,
        "toast_chunk_overhead_bytes": 13_512,
    },
    "chunk_detail": "measured",
}


def test_audit_parses_per_org_and_keeps_the_residual_SEPARATE():
    reading = parse_per_org_bytes(AUDIT)
    assert reading.ok is True
    assert reading.attributed_bytes == 19_494
    assert reading.for_org("org_a").row_bytes == 19_320
    assert reading.for_org("nobody") is None  # not covered, never a zero org
    # The residual is carried and labelled — and it is NOT inside a per-org
    # figure. 19,494 attributed against a 57,344 B relation is the separation.
    assert reading.residual_bytes == 37_850
    assert reading.residual_ratio == pytest.approx(0.66)
    assert reading.residual_note  # the caveat travels with the number
    assert reading.attributed_bytes < reading.residual["total_relation_bytes"]


def test_audit_refuses_an_inconsistent_or_residual_less_reply():
    bad_total = {**AUDIT, "attributed_bytes": 1}
    with pytest.raises(ValueError, match="inconsistent"):
        parse_per_org_bytes(bad_total)

    bad_rows = {**AUDIT, "rows": 99}
    with pytest.raises(ValueError, match="inconsistent"):
        parse_per_org_bytes(bad_rows)

    no_residual = dict(AUDIT)
    del no_residual["residual"]
    with pytest.raises(ValueError, match="residual"):
        parse_per_org_bytes(no_residual)

    dup = {**AUDIT, "orgs": AUDIT["orgs"] + AUDIT["orgs"][:1],
           "rows": 4, "attributed_bytes": 19_494 + 19_320}
    with pytest.raises(ValueError, match="repeats"):
        parse_per_org_bytes(dup)


def test_audit_read_is_TOTAL_and_marks_a_failure():
    assert per_org_bytes(None).ok is False
    assert per_org_bytes(None).error
    # A failed reading's zeros are placeholders, and ok=False is the only thing
    # that says so.
    assert per_org_bytes(None).attributed_bytes == 0


class _Cp:
    def __init__(self, value):
        self.value = value

    def rpc_value(self, name, params):
        assert name == AUDIT_RPC
        return self.value


def test_per_org_bytes_reads_the_audit_rpc():
    reading = per_org_bytes(_Cp(AUDIT))
    assert reading.ok is True
    assert reading.chunk_detail == "measured"


# ══════════════════════════════════════════════════════════════════════════
# 4. Reconciliation — the direction of the disagreement is named
# ══════════════════════════════════════════════════════════════════════════


def test_reconcile_accepts_agreement_and_discloses_history():
    r = reconcile("o", metered_bytes=1_000, audited_bytes=1_000)
    assert r.ok is True and r.excess_bytes == 0 and r.unmetered_bytes == 0

    # The counter BELOW the audit is the expected state for pre-meter rows:
    # disclosed, and NOT an error (under-counting here is not over-billing).
    r = reconcile("o", metered_bytes=1_000, audited_bytes=5_000)
    assert r.ok is True
    assert r.unmetered_bytes == 4_000
    assert "unmetered" in r.detail


def test_reconcile_flags_the_counter_ABOVE_the_audit():
    """Over-counting is the dangerous direction and must not pass silently."""
    r = reconcile("o", metered_bytes=14_418, audited_bytes=7_241)
    assert r.ok is False
    assert r.excess_bytes == 7_177
    assert "double count" in r.detail

    # Inside the band is fine (integer rounding), outside is not. The band is
    # 1% of the audit, so 50 B above 7,241 (0.69%) must PASS and 200 B (2.8%)
    # must FAIL — asserting both sides is what keeps the band from being a
    # constant nobody tested.
    assert reconcile("o", metered_bytes=7_241 + 50, audited_bytes=7_241,
                     band=DEFAULT_EXCESS_BAND).ok is True
    assert reconcile("o", metered_bytes=7_241 + 200, audited_bytes=7_241,
                     band=DEFAULT_EXCESS_BAND).ok is False
    # The counter BELOW the audit is never an excess.
    assert reconcile("o", metered_bytes=7_241, audited_bytes=7_241 + 5_000,
                     band=DEFAULT_EXCESS_BAND).ok is True


def test_reconcile_refuses_a_negative_count():
    with pytest.raises(ValueError):
        reconcile("o", metered_bytes=-1, audited_bytes=10)


# ══════════════════════════════════════════════════════════════════════════
# 5. The ledger writer
# ══════════════════════════════════════════════════════════════════════════


def test_ledger_writer_skips_the_selfhost_transport_and_missing_org():
    assert metering.record_postgres_bytes_written(None, n_bytes=10) is None
    assert metering.record_postgres_bytes_written(
        "org", n_bytes=10, _selfhost_transport=True) is None
    # Nothing was stored: do not mint an empty row / claim a zero reading.
    assert metering.record_postgres_bytes_written("org", n_bytes=0,
                                                 n_records=0) is None


def test_ledger_writer_refuses_a_corrupt_count():
    """A counter that can go backwards is corruption, not a measurement."""
    with pytest.raises(ValueError):
        metering.record_postgres_bytes_written("org", n_bytes=-1)
    with pytest.raises(ValueError):
        metering.record_postgres_bytes_written("org", n_bytes=1, n_records=-1)
    with pytest.raises(ValueError):
        metering.record_postgres_bytes_written("org", n_bytes=True)


def test_ledger_writer_accumulates_through_the_rpc(monkeypatch):
    """The supabase lane hands the figure to the increment RPC, unchanged."""
    calls = []

    monkeypatch.setattr(metering, "_supabase_mode", lambda: True)
    monkeypatch.setattr(
        metering, "_require_period",
        lambda org_id, what: metering.MeteringPeriod(
            start=_dt("2026-11-01"), end=_dt("2026-12-01")))

    import tortoise.supabase_control as sc

    def fake_increment(cp, org_id, start, end, n_bytes, n_records=0):
        calls.append((org_id, start, end, n_bytes, n_records))

    monkeypatch.setattr(sc, "get_control_plane", lambda: object())
    monkeypatch.setattr(sc, "metering_increment_postgres_bytes", fake_increment)

    out = metering.record_postgres_bytes_written("org_q", n_bytes=7_241,
                                                n_records=1)
    assert calls == [("org_q", "2026-11-01T00:00:00+00:00",
                      "2026-12-01T00:00:00+00:00", 7_241, 1)]
    assert out["postgres_bytes_written"] == 7_241
    assert out["postgres_bytes_records"] == 1


def test_ledger_writer_is_non_fatal_on_a_failed_rpc(monkeypatch):
    monkeypatch.setattr(metering, "_supabase_mode", lambda: True)
    monkeypatch.setattr(
        metering, "_require_period",
        lambda org_id, what: metering.MeteringPeriod(
            start=_dt("2026-11-01"), end=_dt("2026-12-01")))

    import tortoise.supabase_control as sc

    def boom(cp, org_id, start, end, n_bytes, n_records=0):
        raise RuntimeError("control plane down")

    monkeypatch.setattr(sc, "get_control_plane", lambda: object())
    monkeypatch.setattr(sc, "metering_increment_postgres_bytes", boom)

    assert metering.record_postgres_bytes_written("org_q", n_bytes=10) is None


# ══════════════════════════════════════════════════════════════════════════
# 6. Cross-boundary: the Python names must match the SQL text
# ══════════════════════════════════════════════════════════════════════════


def test_rpc_names_and_params_match_the_migration():
    """A rename on either side would 404 at runtime — invisible to both suites."""
    sql = MIGRATION.read_text()
    assert f"FUNCTION public.{ROWS_RPC}(" in sql
    assert f"FUNCTION public.{AUDIT_RPC}(" in sql
    assert "FUNCTION public.metering_increment_postgres_bytes(" in sql
    for param in ("p_org_id", "p_graph_id", "p_seqs"):
        assert f"    {param}" in sql, f"{param} missing from the migration"
    for col in ("postgres_bytes_written", "postgres_bytes_records"):
        assert f"ADD COLUMN IF NOT EXISTS {col} bigint" in sql
    # The counter columns must be NOT NULL DEFAULT 0: a NULL + n sticks at NULL
    # forever, which renders as 0 with no error anywhere (the 20261001000005
    # lesson).
    assert "postgres_bytes_written bigint NOT NULL DEFAULT 0" in sql


def test_the_figure_carries_its_method_and_its_exclusions():
    """The caveat must travel WITH the number, in every serialization."""
    written = parse_written_rows(STORED_VECTOR)
    assert written.as_dict()["method_note"] == METHOD_NOTE
    reading = parse_per_org_bytes(AUDIT)
    d = reading.as_dict()
    assert d["postgres_method_note"] == METHOD_NOTE
    assert d["postgres_residual_note"] == RESIDUAL_NOTE
    assert "ALLOCATION" in RESIDUAL_NOTE
    assert "TOAST" in METHOD_NOTE
