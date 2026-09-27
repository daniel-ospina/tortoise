"""#5331 — the graph BYTE METER: per-org graph storage in MB.

Covers the meter itself (``tortoise/graph_storage.py``) and its per-org ledger
lane (``metering.record_graph_storage_reading`` / ``get_graph_storage_reading``).

The load-bearing properties, each pinned so it can fail:

* the reading reports a RANGE and its SAMPLES/repeats — never a bare point;
* the two caveats (sampling estimate; excludes per-graph/Redis-key overhead)
  TRAVEL with the reading, including through ``as_dict()``;
* the meter is FAIL-SOFT — a bad handle, a malformed reply or a dead engine
  returns ``ok=False`` and never raises;
* the ledger write is a GAUGE (overwrite), not an increment — adding two
  readings of the same graph would double-count the same bytes.

Embedded registry lane (no Docker), mirroring ``tests/test_metering.py``; the
real-projection test is backend-aware via the shared ``sdk_factory`` fixture.
"""
from __future__ import annotations

import os

import pytest

from tortoise import graph_storage
from tortoise.graph_storage import (
    EXCLUDED_OVERHEAD,
    PRECISION_NOTE,
    SAMPLES_DEFAULT,
    SAMPLES_MAX,
    measure_graph_storage,
    measure_projection_storage,
    parse_memory_usage,
)

# The exact flat key/value shape FalkorDB 4.2 returns (captured live), with a
# nested per-label breakdown.
REPLY = [
    b"total_graph_sz_mb", 4,
    b"label_matrices_sz_mb", 0,
    b"relation_matrices_sz_mb", 0,
    b"amortized_node_block_sz_mb", 0,
    b"amortized_node_attributes_by_label_sz_mb", [b"Point", 3, b"Object", 1],
    b"amortized_unlabeled_nodes_attributes_sz_mb", 0,
    b"amortized_edge_block_sz_mb", 0,
    b"amortized_edge_attributes_by_type_sz_mb", [],
    b"indices_sz_mb", 0,
]

#: The SAME reply as it arrives from a client that decoded it itself: a dict
#: whose nested value is STILL a flat list. Both shapes must normalise — the
#: parser advertises this one, so it has to measure, not merely parse.
DICT_REPLY = {
    "total_graph_sz_mb": 4,
    "indices_sz_mb": 1,
    "amortized_node_attributes_by_label_sz_mb": [b"Point", 3, b"Object", 1],
}


def _reply(total_mb: float, indices_mb: float = 0.0) -> list:
    return [b"total_graph_sz_mb", total_mb, b"indices_sz_mb", indices_mb]


class _FakeClient:
    """A client whose ``execute_command`` replays canned replies in order."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls: list[tuple] = []

    def execute_command(self, *args):
        self.calls.append(args)
        return self._replies.pop(0)


class _BoomClient:
    def execute_command(self, *args):
        raise RuntimeError("engine down")


# ── parsing ───────────────────────────────────────────────────────────────

def test_parse_reads_the_engine_flat_kv_shape():
    parsed = parse_memory_usage(REPLY)
    assert parsed["total_graph_sz_mb"] == 4
    assert parsed["indices_sz_mb"] == 0
    # The nested breakdown becomes a per-label dict (the attribution context).
    assert parsed["amortized_node_attributes_by_label_sz_mb"] == {
        "Point": 3, "Object": 1}


def test_parse_rejects_a_reply_that_is_not_a_flat_list():
    """GUARD: a malformed reply must RAISE, never read as a zero-byte graph."""
    with pytest.raises(ValueError):
        parse_memory_usage(None)
    with pytest.raises(ValueError):
        parse_memory_usage([b"total_graph_sz_mb"])  # odd length


def test_parse_rejects_a_reply_without_total():
    """GUARD: a list with no ``total_graph_sz_mb`` is malformed, not empty."""
    with pytest.raises(ValueError):
        parse_memory_usage([b"indices_sz_mb", 0])


def test_parse_normalises_a_client_decoded_dict_reply():
    """GUARD (review finding): the advertised dict shape must be NORMALISED.

    A client that decoded the reply itself hands back a dict whose nested value
    is still a flat list. Returning that dict VERBATIM left the one shape this
    parser advertises failing downstream — the per-label unpack raised
    ``'list' object has no attribute 'items'``, which the outer fail-soft
    handler turned into ``ok=False``, discarding a well-formed reading.
    """
    parsed = parse_memory_usage(DICT_REPLY)
    assert parsed["amortized_node_attributes_by_label_sz_mb"] == {
        "Point": 3, "Object": 1}


def test_measure_reads_a_client_decoded_dict_reply():
    """GUARD (review finding): end to end, the dict shape must MEASURE.

    Measured before the fix: ``ok=False`` with
    ``error="'list' object has no attribute 'items'"`` — a well-formed reply
    silently discarded, which reads to a consumer as "no reading", not as a
    parser defect.
    """
    r = measure_graph_storage(_FakeClient([DICT_REPLY]), "org_x")
    assert r.ok is True, r.error
    assert r.total_mb == 4.0
    assert r.indices_mb == 1.0
    assert r.node_attributes_mb == {"Point": 3.0, "Object": 1.0}


# ── the reading ───────────────────────────────────────────────────────────

def test_measure_reports_a_range_and_the_samples_used():
    client = _FakeClient([_reply(4), _reply(6), _reply(5)])
    r = measure_graph_storage(client, "org_x", samples=SAMPLES_DEFAULT,
                              repeats=3)
    assert r.ok is True
    assert r.samples == SAMPLES_DEFAULT and r.repeats == 3
    assert r.readings_mb == (4.0, 6.0, 5.0)
    assert r.total_mb == 5.0          # median of the repeats
    assert r.min_mb == 4.0 and r.max_mb == 6.0
    assert r.spread_mb == 2.0         # the OBSERVED range travels with it
    assert r.indices_mb == 0.0
    assert r.node_attributes_mb == {}


def test_measure_takes_the_breakdown_from_the_reading():
    client = _FakeClient([REPLY])
    r = measure_graph_storage(client, "org_x")
    assert r.total_mb == 4.0
    assert r.indices_mb == 0.0
    assert r.node_attributes_mb == {"Point": 3.0, "Object": 1.0}


def test_measure_sends_the_samples_it_reports():
    client = _FakeClient([_reply(1)])
    r = measure_graph_storage(client, "org_x", samples=250)
    assert client.calls == [("GRAPH.MEMORY", "USAGE", "org_x", "SAMPLES", 250)]
    assert r.samples == 250


def test_measure_default_is_one_command_at_the_documented_default():
    client = _FakeClient([_reply(1)])
    r = measure_graph_storage(client, "org_x")
    assert client.calls == [
        ("GRAPH.MEMORY", "USAGE", "org_x", "SAMPLES", SAMPLES_DEFAULT)]
    assert r.repeats == 1


def test_measure_clamps_samples_above_the_engine_max():
    """GUARD: SAMPLES above FalkorDB's documented max is clamped, warned."""
    client = _FakeClient([_reply(1)])
    r = measure_graph_storage(client, "org_x", samples=SAMPLES_MAX + 1)
    assert r.samples == SAMPLES_MAX
    assert client.calls[0][-1] == SAMPLES_MAX


def test_measure_clamps_repeats_below_one():
    """GUARD: repeats=0 would read nothing and report an empty range."""
    client = _FakeClient([_reply(1)])
    r = measure_graph_storage(client, "org_x", repeats=0)
    assert r.repeats == 1
    assert len(client.calls) == 1


def test_measure_rejects_a_non_integer_samples():
    """GUARD: an unusable SAMPLES is a FAILED reading, not a silent default."""
    r = measure_graph_storage(_FakeClient([]), "org_x", samples="many")
    assert r.ok is False and "SAMPLES" in (r.error or "")


# ── fail-soft ─────────────────────────────────────────────────────────────

def test_measure_fails_soft_on_engine_error():
    r = measure_graph_storage(_BoomClient(), "org_x")
    assert r.ok is False
    assert r.total_mb == 0.0 and r.error
    assert r.samples == SAMPLES_DEFAULT  # the reading still says what it meant


def test_measure_fails_soft_on_malformed_reply():
    """GUARD: malformed must FAIL, not masquerade as a zero-byte graph."""
    r = measure_graph_storage(_FakeClient([None]), "org_x")
    assert r.ok is False
    assert r.total_mb == 0.0
    assert r.error


def test_measure_fails_soft_on_a_non_finite_total():
    """GUARD: a NaN/inf total is a MALFORMED reply, not a measurement.

    If it were accepted, the writer would drop it (never a false zero), so the
    reading would vanish silently — better to report the failure.
    """
    r = measure_graph_storage(_FakeClient([_reply(float("nan"))]), "org_x")
    assert r.ok is False
    assert "non-finite" in (r.error or "")


def test_breakdown_comes_from_the_reported_repeat():
    """GUARD: the index share can never exceed the total it is a share of."""
    replies = [
        [b"total_graph_sz_mb", 10, b"indices_sz_mb", 9],
        [b"total_graph_sz_mb", 2, b"indices_sz_mb", 1],
        [b"total_graph_sz_mb", 3, b"indices_sz_mb", 1],
    ]
    r = measure_graph_storage(_FakeClient(replies), "org_x", repeats=3)
    assert r.total_mb == 3.0          # nearest-rank median (lower-middle)
    assert r.indices_mb == 1.0        # from the SAME repeat as the total
    assert r.indices_mb <= r.total_mb
    assert r.max_mb == 10.0


def test_measure_fails_soft_without_a_handle_or_graph_name():
    assert measure_graph_storage(None, "org_x").ok is False
    assert measure_graph_storage(_FakeClient([]), "").ok is False


def test_measure_fails_soft_when_a_later_repeat_fails():
    """GUARD: a partial repeat set is NOT averaged into a confident point."""
    class _SecondBoom(_FakeClient):
        def execute_command(self, *args):
            if self.calls:
                raise RuntimeError("second repeat failed")
            return super().execute_command(*args)

    r = measure_graph_storage(_SecondBoom([_reply(4)]), "org_x", repeats=2)
    assert r.ok is False and r.error


# ── the caveats travel with the reading ───────────────────────────────────

def test_reading_carries_the_two_caveats():
    r = measure_graph_storage(_FakeClient([_reply(9)]), "org_x")
    assert r.estimated is True
    assert r.excludes == EXCLUDED_OVERHEAD and r.excludes
    assert "SAMPLING ESTIMATE" in r.precision_note
    assert "EXCLUDES" in r.precision_note
    assert "NOT invoice-grade" in r.precision_note


def test_as_dict_carries_the_caveats_too():
    """GUARD: a consumer reading only the dict still gets the honesty fields."""
    d = measure_graph_storage(_FakeClient([_reply(9)]), "org_x").as_dict()
    assert d["graph_storage_mb"] == 9.0
    assert d["graph_storage_estimated"] is True
    assert d["graph_storage_excludes"] == list(EXCLUDED_OVERHEAD)
    assert d["graph_storage_precision_note"] == PRECISION_NOTE
    assert d["graph_storage_samples"] == SAMPLES_DEFAULT


# ── projection handle ─────────────────────────────────────────────────────

class _FakeProj:
    def __init__(self, db, graph_name):
        self.db = db
        self.graph_name = graph_name


def test_measure_projection_uses_the_open_handle():
    client = _FakeClient([_reply(12)])
    proj = _FakeProj(client, "org_abc")
    r = measure_projection_storage(proj)
    assert r.ok is True and r.graph_name == "org_abc"
    assert client.calls[0][2] == "org_abc"


def test_measure_projection_fails_soft_on_a_missing_handle():
    r = measure_projection_storage(object())
    assert r.ok is False
    # Pin the SPECIFIC early-guard message, not just ok=False: without the
    # guard the same input still fails soft (AttributeError caught below), so
    # only the message distinguishes the guard from the generic catch-all.
    assert "no graph handle" in (r.error or "")


def test_measure_projection_against_a_real_handle(sdk_factory):
    """The meter works on a REAL FalkorDB handle (both lanes implement it)."""
    sdk = sdk_factory()
    try:
        proj = sdk._get_proj()
        r = measure_projection_storage(proj, samples=100, repeats=2)
        assert r.ok is True, r.error
        assert r.graph_name == proj.graph_name
        assert r.samples == 100 and r.repeats == 2
        assert r.total_mb >= 0.0
        assert r.min_mb <= r.total_mb <= r.max_mb
    finally:
        sdk.close()


# ── the per-org ledger lane ───────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _embedded_env(monkeypatch, tmp_path):
    """Route the metering SDKs to an embedded temp DB (no Supabase, no URI)."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "metering.db"))
    import tortoise.metering as metering_mod
    monkeypatch.setattr(metering_mod, "_supabase_mode", lambda: False)


@pytest.fixture
def org(monkeypatch, tmp_path):
    from tortoise.sdk import TortoiseSDK
    db = os.path.join(str(tmp_path), "metering.db")
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", db)
    sdk = TortoiseSDK(db, namespace="registry")
    tid = sdk.org_create(name="byte-meter-test")["id"]
    yield tid
    sdk.close()


def test_ledger_roundtrip(org):
    from tortoise import metering
    written = metering.record_graph_storage_reading(
        org, total_mb=143.0, indices_mb=46.0, samples=100, repeats=3,
        min_mb=141.0, max_mb=145.0, spread_mb=4.0,
        measured_at="2026-09-26T00:00:00+00:00")
    assert written is not None
    got = metering.get_graph_storage_reading(org)
    assert got["graph_storage_mb"] == 143.0
    assert got["graph_storage_indices_mb"] == 46.0
    assert got["graph_storage_samples"] == 100
    assert got["graph_storage_repeats"] == 3
    assert got["graph_storage_min_mb"] == 141.0
    assert got["graph_storage_max_mb"] == 145.0
    assert got["graph_storage_spread_mb"] == 4.0
    assert got["graph_storage_measured_at"] == "2026-09-26T00:00:00+00:00"


def test_ledger_write_is_a_gauge_not_an_increment(org):
    """GUARD: a second reading of the SAME graph must OVERWRITE, not add.

    An increment would report 30.0 here — double-counting the same bytes, the
    exact defect the gauge design exists to avoid.
    """
    from tortoise import metering
    metering.record_graph_storage_reading(org, total_mb=10.0, samples=100)
    metering.record_graph_storage_reading(org, total_mb=20.0, samples=100)
    assert metering.get_graph_storage_reading(org)["graph_storage_mb"] == 20.0


def test_reader_degrades_to_a_zero_view_for_an_unknown_org(org):
    from tortoise import metering
    got = metering.get_graph_storage_reading("org_does_not_exist")
    assert got["graph_storage_mb"] == 0.0
    assert got["graph_storage_samples"] == 0
    assert got["graph_storage_measured_at"] is None


def test_reader_never_raises_when_the_registry_is_broken(org, monkeypatch):
    import tortoise.metering as metering_mod
    from tortoise import metering
    metering.record_graph_storage_reading(org, total_mb=5.0)
    monkeypatch.setattr(metering_mod, "_reg_sdk",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    got = metering.get_graph_storage_reading(org)
    assert got["graph_storage_mb"] == 0.0  # degraded, not raised


def test_writer_drops_a_non_finite_total(org):
    """GUARD: a nan DROPS the write; it is never a fabricated 0.0.

    A gauge overwrite of 0.0 would clobber a real prior measurement — the
    false-zero the meter's own contract forbids.
    """
    from tortoise import metering
    metering.record_graph_storage_reading(org, total_mb=143.0)
    assert metering.record_graph_storage_reading(
        org, total_mb=float("nan")) is None
    assert metering.get_graph_storage_reading(org)["graph_storage_mb"] == 143.0


def test_writer_drops_non_integer_samples(org):
    """GUARD: a stored figure must not misstate its own precision."""
    from tortoise import metering
    assert metering.record_graph_storage_reading(
        org, total_mb=5.0, samples="many") is None


def test_writer_maps_a_non_finite_index_share_to_absent(org):
    """GUARD (review finding): a non-finite index share is ABSENT, not 0.0.

    ``graph_storage_indices_mb`` is nullable precisely so "the engine did not
    report an index share" stays distinguishable from "reported as 0". The
    writer previously coerced a NaN to ``0.0`` — a fabricated clean figure
    written into the one column whose whole point is that distinction, and one
    a consumer cannot tell apart from a real measurement.

    MEASURED non-vacuity: reverting ``safe_indices`` to
    ``_finite_or(indices_mb, 0.0)`` makes THIS test red and nothing else —
    before it existed the whole suite stayed green under that revert, so the
    property was correct but unfalsifiable.
    """
    from tortoise import metering
    # Populate the column with a real reading first: a NaN must CLEAR it to
    # absent, and must not leave 0.0 or keep the stale 9.0.
    metering.record_graph_storage_reading(org, total_mb=143.0, indices_mb=9.0)
    assert metering.get_graph_storage_reading(org)["graph_storage_indices_mb"] == 9.0
    metering.record_graph_storage_reading(
        org, total_mb=144.0, indices_mb=float("nan"))
    reading = metering.get_graph_storage_reading(org)
    assert reading["graph_storage_indices_mb"] is None, (
        "a non-finite index share must be stored as ABSENT — 0.0 is a "
        f"fabricated measurement and a kept value is a stale one: {reading!r}")
    # ...and the TOTAL, which IS finite, still landed: the drop is scoped to the
    # field that was non-finite, not to the whole reading.
    assert reading["graph_storage_mb"] == 144.0


def test_record_graph_storage_skips_a_failed_reading(org):
    """GUARD: a failed measurement must NOT be written as a zero-byte graph."""
    from tortoise import metering
    failed = graph_storage._failed_reading("org_x", 100, 1, "now", "dead")
    assert graph_storage.record_graph_storage(org, failed) is None
    assert metering.get_graph_storage_reading(org)["graph_storage_mb"] == 0.0


def test_record_graph_storage_is_fail_soft_when_metering_raises(org, monkeypatch):
    from tortoise import metering
    monkeypatch.setattr(
        metering, "record_graph_storage_reading",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ledger down")))
    reading = measure_graph_storage(_FakeClient([_reply(7)]), "org_x")
    assert graph_storage.record_graph_storage(org, reading) is None


def test_measure_and_record_writes_on_success(org):
    from tortoise import metering
    proj = _FakeProj(_FakeClient([_reply(8)]), "org_abc")
    r = graph_storage.measure_and_record_graph_storage(proj, org)
    assert r.ok is True
    assert metering.get_graph_storage_reading(org)["graph_storage_mb"] == 8.0


def test_measure_and_record_does_not_write_a_failed_reading(org):
    from tortoise import metering
    client = _FakeClient([])
    client.execute_command = lambda *a: (_ for _ in ()).throw(
        RuntimeError("dead"))
    proj = _FakeProj(client, "org_abc")
    r = graph_storage.measure_and_record_graph_storage(proj, org)
    assert r.ok is False
    assert metering.get_graph_storage_reading(org)["graph_storage_mb"] == 0.0


# ── the Supabase lane ─────────────────────────────────────────────────────

@pytest.fixture
def supabase_lane(monkeypatch):
    """Force the Supabase mode + a FakeControlPlane, with a fixed window."""
    from datetime import UTC, datetime

    import tortoise.metering as metering_mod
    from tests.fake_control_plane import FakeControlPlane
    from tortoise.metering import MeteringPeriod

    fake = FakeControlPlane()
    monkeypatch.setattr(metering_mod, "_supabase_mode", lambda: True)
    fixed = MeteringPeriod(
        start=datetime(2026, 9, 1, tzinfo=UTC),
        end=datetime(2026, 10, 1, tzinfo=UTC))
    monkeypatch.setattr(metering_mod, "_require_period", lambda *a, **k: fixed)
    monkeypatch.setattr(metering_mod, "_current_period", lambda *a, **k: fixed)
    import tortoise.supabase_control as sc
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
    return metering_mod, fake


def test_supabase_gauge_roundtrip(supabase_lane):
    metering_mod, fake = supabase_lane
    metering_mod.record_graph_storage_reading(
        "org_s", total_mb=55.0, indices_mb=12.0, samples=100, repeats=2,
        min_mb=54.0, max_mb=56.0, spread_mb=2.0)
    assert [fn for fn, _ in fake.rpc_calls] == ["metering_set_graph_storage"]
    got = metering_mod.get_graph_storage_reading("org_s")
    assert got["graph_storage_mb"] == 55.0
    assert got["graph_storage_indices_mb"] == 12.0
    assert got["graph_storage_spread_mb"] == 2.0


def test_supabase_gauge_overwrites(supabase_lane):
    """GUARD: the SQL/fake RPC is ``= EXCLUDED``, not an increment."""
    metering_mod, fake = supabase_lane
    metering_mod.record_graph_storage_reading("org_s", total_mb=10.0)
    metering_mod.record_graph_storage_reading("org_s", total_mb=30.0)
    assert len(fake.rpc_calls) == 2
    assert metering_mod.get_graph_storage_reading(
        "org_s")["graph_storage_mb"] == 30.0


def test_supabase_drops_a_non_finite_total(supabase_lane):
    """GUARD: the NaN is dropped, preserving the last good reading.

    The fake control plane stores the value verbatim, so only the writer's
    sanitizer keeps a real prior measurement from being clobbered by a NaN.
    """
    metering_mod, fake = supabase_lane
    metering_mod.record_graph_storage_reading("org_s", total_mb=143.0)
    assert metering_mod.record_graph_storage_reading(
        "org_s", total_mb=float("nan")) is None
    assert fake.tables["metering_records"][0]["graph_storage_mb"] == 143.0
    assert metering_mod.get_graph_storage_reading(
        "org_s")["graph_storage_mb"] == 143.0
