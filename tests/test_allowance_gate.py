"""The per-dimension allowance gate (#9567 D11/D12, #5331/#4495).

**What these tests falsify.** On the pre-fix code, :mod:`tortoise.allowance_gate`
does not exist, so every test here fails at import: the shipped gate counts NODES
(``quota.enforce_org_limit`` -> ``max_points``) and enforces NONE of the three
dimensions the owner ruled — Postgres bytes, graph MB, extraction tokens
(``#4495``: *"we shouldn't measure 'per node'"*; ``#5331``: MB/GB for Postgres,
MB for the graph, LLM usage for extraction).

**The three D11/D12 outcomes, one test each** — over ⇒ refused · under ⇒ applied ·
**configured with no reading ⇒ refused** (not passed, and not an unhandled
exception reaching the caller as a 500).
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tortoise import cost_dimensions
from tortoise.allowance_gate import (
    ALLOWANCE_GATED_RESOURCES,
    ALLOWANCE_KEYS,
    DIMENSIONS,
    AllowanceDimension,
    MeterReading,
    enforce_allowances,
)
from tortoise.quota import (
    QuotaCheckError,
    QuotaExceededError,
    enforce_org_limit,
    quota_refusal_payload,
)

ORG = "org-allowance-test"


def _limits(**allowances) -> dict:
    """A resolved limits dict, plus whatever allowances a test configures."""
    return {
        "org_id": ORG, "tier": "pro", "max_points": None,
        "max_api_keys": None, "max_sessions": None, "max_users": None,
        "max_graphs": None, **allowances,
    }


def _meter(used, measured_at=None, estimated=False):
    if measured_at is None:
        measured_at = datetime.now(UTC).isoformat()

    def _read(_org_id: str) -> MeterReading | None:
        return MeterReading(used=used, measured_at=measured_at,
                            estimated=estimated, note="test reading")
    return _read


def _no_reading(_org_id: str) -> MeterReading | None:
    return None


def _exploding(_org_id: str) -> MeterReading | None:
    raise RuntimeError("meter backend unavailable")


# ── D11: over-cap refused, under-cap applied ───────────────────────────────


def test_over_allowance_is_refused_and_names_the_dimension():
    limits = _limits(max_extraction_tokens=10)
    with pytest.raises(QuotaExceededError) as caught:
        enforce_allowances(limits, resource="sessions",
                           meters={"capture_extraction": _meter(11)})
    exc = caught.value
    assert exc.resource == "capture_extraction"
    assert (exc.used, exc.limit) == (11, 10)
    assert "capture_extraction" in str(exc) and "tokens" in str(exc)


def test_under_allowance_passes():
    limits = _limits(max_extraction_tokens=10)
    enforce_allowances(limits, resource="sessions",
                       meters={"capture_extraction": _meter(9)})


def test_the_refusal_carries_the_meters_honesty_into_the_payload():
    limits = _limits(max_extraction_tokens=1)
    with pytest.raises(QuotaExceededError) as caught:
        enforce_allowances(
            limits, resource="sessions",
            meters={"capture_extraction": _meter(5, estimated=True)})
    payload = quota_refusal_payload(caught.value)
    assert payload["estimated"] is True
    assert "test reading" in payload["message"]


# ── D12: configured + no reading ⇒ REFUSED (fail closed) ───────────────────


def test_configured_without_a_reading_is_refused_not_passed():
    limits = _limits(max_extraction_tokens=10)
    with pytest.raises(QuotaCheckError) as caught:
        enforce_allowances(limits, resource="sessions",
                           meters={"capture_extraction": _no_reading})
    assert "capture_extraction" in str(caught.value)


def test_a_meter_fault_is_a_refusal_not_an_unhandled_exception():
    limits = _limits(max_extraction_tokens=10)
    with pytest.raises(QuotaCheckError):
        enforce_allowances(limits, resource="sessions",
                           meters={"capture_extraction": _exploding})


def test_a_stale_reading_is_refused():
    """The staleness bound, pinned where it is enforceable.

    ``graph_storage`` is the only dimension with a bound, and it is BLOCKED (see
    ``test_ruled_dimension_two_graph_storage_mb``), so the bound is exercised on
    a synthetic entry through the SAME ``_reading`` seam the real dimensions use.
    """
    from tortoise.allowance_gate import _reading

    bounded = AllowanceDimension(
        dimension="capture_extraction", limit_key="max_extraction_tokens",
        unit="tokens", meter=_meter(1), stale_after_seconds=60)
    with pytest.raises(QuotaCheckError) as caught:
        _reading(bounded, ORG,
                 {"capture_extraction": _meter(
                     1, measured_at="2020-01-01T00:00:00+00:00")},
                 observed_only=False)
    assert "older than" in str(caught.value)


def test_an_unreadable_allowance_is_refused():
    limits = _limits(max_extraction_tokens="not-a-number")
    with pytest.raises(QuotaCheckError):
        enforce_allowances(limits, resource="sessions",
                           meters={"capture_extraction": _meter(1)})


# ── unconfigured = unlimited, and still METERED ────────────────────────────


def test_an_absent_allowance_is_unlimited_and_calls_no_meter():
    calls: list[str] = []

    def _counting(org_id: str) -> MeterReading:
        calls.append(org_id)
        return MeterReading(used=10 ** 9)

    # No allowance key at all: no invented cap, so it must not even read.
    enforce_allowances(_limits(), meters={d.dimension: _counting
                                         for d in DIMENSIONS})
    assert calls == []


def test_an_explicit_null_allowance_is_unlimited_but_still_meters():
    calls: list[str] = []

    def _counting(org_id: str) -> MeterReading:
        calls.append(org_id)
        return MeterReading(used=10 ** 9)

    enforce_allowances(_limits(max_graph_storage_mb=None),
                       meters={"graph_storage": _counting})
    assert calls == [ORG]  # measured, and NOT refused for being enormous


def test_a_broken_meter_does_not_refuse_an_unlimited_dimension():
    enforce_allowances(_limits(max_graph_storage_mb=None),
                       meters={"graph_storage": _exploding})


# ── one test per RULED dimension ───────────────────────────────────────────


def test_ruled_dimension_two_graph_storage_mb_is_BLOCKED_and_refuses():
    """Unit 2 of #5331 — the graph, in MB — is BLOCKED by our own instrument.

    ``graph_storage``'s module docstring: *"Until they are corrected, a cap MUST
    NOT be wired to the ledger figure for vector-bearing tenants"* (the HNSW index
    share under-reports by 8.5x-21.7x, and the ledger returns the uncorrected
    numerics). So the dimension is declared BLOCKED: a configured allowance
    REFUSES and names what is missing, rather than enforcing a figure that would
    not fire for the tenants who grow biggest.
    """
    entry = {d.dimension: d for d in DIMENSIONS}["graph_storage"]
    assert entry.blocked is True
    with pytest.raises(QuotaCheckError) as caught:
        # a `meters=` override must NOT un-block a declared block
        enforce_allowances(_limits(max_graph_storage_mb=100),
                           meters={"graph_storage": _meter(143.0)})
    assert "BLOCKED" in str(caught.value)
    assert "MUST NOT be wired" in str(caught.value)


def test_ruled_dimension_three_extraction_tokens():
    """Unit 3 of #5331 — LLM extraction, in tokens."""
    with pytest.raises(QuotaExceededError) as caught:
        enforce_allowances(_limits(max_extraction_tokens=1000),
                           resource="sessions",
                           meters={"capture_extraction": _meter(1001)})
    assert caught.value.resource == "capture_extraction"
    assert "tokens" in str(caught.value)
    # and under the cap it passes
    enforce_allowances(_limits(max_extraction_tokens=1000), resource="sessions",
                       meters={"capture_extraction": _meter(999)})


def test_ruled_dimension_one_postgres_bytes_is_BLOCKED_and_refuses():
    """Unit 1 of #5331 — Postgres bytes — has NO per-org meter.

    ``postgres_storage`` measures at DATABASE grain and its declared
    ``attribution`` says a per-org split would be an ALLOCATION, not a
    measurement. So configuring the key must REFUSE (fail closed, naming the
    block) rather than pass on a figure nobody measured, and rather than charge
    one tenant for the whole database.
    """
    limits = _limits(max_postgres_storage_bytes=10 ** 9)
    with pytest.raises(QuotaCheckError) as caught:
        enforce_allowances(limits)
    message = str(caught.value)
    assert "postgres_storage" in message
    assert "no per-org byte meter" in message


# ── the gate is ONE gate, on the declared registry ─────────────────────────


def test_every_gated_dimension_is_a_declared_cost_dimension():
    """#1178: one instrument, no second registry."""
    for dim in DIMENSIONS:
        declared = cost_dimensions.by_name(dim.dimension)  # raises if undeclared
        assert declared.name == dim.dimension
        assert ALLOWANCE_KEYS[dim.dimension] == dim.limit_key


def test_one_allowance_key_per_dimension_and_no_node_count_unit():
    keys = [d.limit_key for d in DIMENSIONS]
    assert len(keys) == len(set(keys))
    assert set(ALLOWANCE_KEYS) == {d.dimension for d in DIMENSIONS}
    for dim in DIMENSIONS:
        assert "node" not in dim.limit_key
        assert "node" not in dim.unit.lower()


def test_only_storage_writing_resources_are_gated():
    assert {"points", "documents", "sessions"} == ALLOWANCE_GATED_RESOURCES
    # an api_keys check cannot move a storage meter, so it must not be refused
    enforce_allowances(_limits(max_graph_storage_mb=0), resource="api_keys")


def test_each_dimension_gates_the_doors_that_move_its_own_meter():
    """One global resource set could not express this (review round 2, P1).

    ``capture_extraction`` must cover the CAPTURE lane — ``sessions`` is the
    resource ``hosted_api._capture_session_impl`` reaches the shared gate with,
    and it is the only door that SPENDS extraction tokens. A token allowance that
    skipped it would be enforced everywhere except where its meter moves.
    """
    by_dim = {d.dimension: d.resources for d in DIMENSIONS}
    assert by_dim["capture_extraction"] == frozenset({"sessions"})
    # a token allowance exhausted mid-window must not refuse a points write:
    # that door spends no tokens, and a cap that blocks an unrelated write is the
    # anti-pattern the door principle refuses to build.
    assert "points" not in by_dim["capture_extraction"]
    assert "sessions" not in by_dim["graph_storage"]
    assert "sessions" not in by_dim["postgres_storage"]

    # configured + a reading over the cap, on the CAPTURE door → refused
    with pytest.raises(QuotaExceededError) as caught:
        enforce_allowances(_limits(max_extraction_tokens=10),
                           resource="sessions",
                           meters={"capture_extraction": _meter(11)})
    assert caught.value.resource == "capture_extraction"
    # …and the same configured dimension does NOT refuse an api_keys check
    enforce_allowances(_limits(max_extraction_tokens=10), resource="api_keys",
                       meters={"capture_extraction": _meter(11)})


# ── reached THROUGH the shared quota gate (the REST/MCP seam) ─────────────


def test_the_gate_is_reached_through_enforce_org_limit():
    """The real seam: ``enforce_org_limit`` is what REST and MCP both call.

    A configured allowance with no reading must refuse THERE — before the node
    count runs — so the refusal is the documented ``QuotaCheckError`` and never
    an unmapped 500 from a count that should not have been reached.
    """
    from dataclasses import replace

    from tortoise import allowance_gate

    # The entries hold their meter objects, so the seam under test is patched on
    # the DECLARATION (patching the module function would not reach the tuple).
    original = allowance_gate.DIMENSIONS
    allowance_gate.DIMENSIONS = tuple(
        replace(d, meter=(lambda _org: None))  # unmeasurable
        if d.dimension == "capture_extraction" else d
        for d in original)
    try:
        with pytest.raises(QuotaCheckError):
            enforce_org_limit(_limits(max_extraction_tokens=1), "sessions")
    finally:
        allowance_gate.DIMENSIONS = original


def test_the_gate_is_inert_on_a_normal_limits_dict():
    """No allowance configured → the legacy node cap is what runs, unchanged."""
    from tortoise.quota import _RESOURCE_LIMIT_KEYS

    limits = {"org_id": ORG, "tier": "free"}
    limits.update({key: None for key in _RESOURCE_LIMIT_KEYS.values()})
    limits["max_points"] = None  # unlimited ⇒ no count, no refusal
    enforce_org_limit(limits, "points")


# ── review round 1 (fresh-context reviewer) — the fail-open classes ────────


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_reading_is_refused_never_passed_or_a_bare_500(bad):
    """``nan >= allowance`` is False, so a NaN reading would SILENTLY PASS —
    a disarmed allowance (#3665). ``inf`` is over the cap but ``int(inf)`` raises
    a bare OverflowError out of the gate (#4051's class). Both are REFUSED.
    """
    limits = _limits(max_extraction_tokens=10)
    with pytest.raises(QuotaCheckError):
        enforce_allowances(limits, resource="sessions",
                           meters={"capture_extraction": _meter(bad)})


def test_a_reading_with_no_timestamp_cannot_satisfy_the_staleness_bound():
    """``measured_at=None`` means "no age", which cannot prove freshness — on a
    BOUNDED dimension it is stale, not fresh (else the bound is unenforceable
    for exactly the readings that cannot be aged).
    """
    def _dateless(_org_id: str) -> MeterReading | None:
        return MeterReading(used=1.0, measured_at=None)

    from tortoise.allowance_gate import _reading

    bounded = AllowanceDimension(
        dimension="capture_extraction", limit_key="max_extraction_tokens",
        unit="tokens", meter=_meter(1), stale_after_seconds=60)
    with pytest.raises(QuotaCheckError):
        _reading(bounded, ORG, {"capture_extraction": _dateless},
                 observed_only=False)


def test_the_token_reader_distinguishes_no_row_from_a_recorded_zero():
    """#5045: a capture whose extraction reported no usage records ZERO tokens,
    and that zero IS a reading. Only "no ledger row" is NO READING — the
    distinction the whole extraction dimension's fail-closed behaviour rests on,
    and the adapter must not flatten it."""
    import uuid
    from datetime import UTC, datetime

    from tortoise import metering
    from tortoise.allowance_gate import _extraction_tokens
    from tortoise.metering import get_capture_usage, record_capture_usage

    # A UNIQUE org per run: the test lane's graph persists between runs, and the
    # "no row yet" precondition must not depend on a previous run's cleanup.
    org = f"org-allowance-token-reader-{uuid.uuid4().hex[:12]}"
    # The registry path reads the ledger for the org's window; pin the window so
    # the test measures the READER, not this environment's billing anchor.
    period = metering.MeteringPeriod(
        start=datetime(2026, 10, 1, tzinfo=UTC),
        end=datetime(2026, 11, 1, tzinfo=UTC))
    original = metering._current_period
    metering._current_period = lambda _org: period
    try:
        absent = get_capture_usage(org)
        assert absent["measured"] is False
        assert absent["unmeasured_reason"] == "absent"
        # The ADAPTER is where the deadlock fix lives: a fresh org has no ledger
        # row, and that absence is a real ZERO for a cumulative flow. Deleting the
        # `absent_is_zero` path would refuse the org's first capture forever (the
        # row is created BY that capture), and this assertion is what reds.
        from tortoise.allowance_gate import ABSENT, _reading

        assert _extraction_tokens(org) is ABSENT  # no row yet is not a failure
        entry = {d.dimension: d for d in DIMENSIONS}["capture_extraction"]
        absent_reading = _reading(entry, org, None, observed_only=False)
        assert absent_reading is not None and absent_reading.used == 0
        # …and the GATE, through the real meter with no override, admits it.
        enforce_allowances(_limits(max_extraction_tokens=10), resource="sessions")

        # The WRITE resolves the window too, so it runs under the same pin.
        record_capture_usage(org, calls=1, tokens_in=0, tokens_out=0)
        recorded = get_capture_usage(org)
        reading = _extraction_tokens(org)
    finally:
        metering._current_period = original
    assert recorded["measured"] is True
    assert recorded["unmeasured_reason"] is None
    assert recorded["capture_tokens_total"] == 0  # a real zero, not "no reading"

    assert reading is not None and reading.used == 0


@pytest.mark.parametrize("bad", [None, "five", object()])
def test_a_non_numeric_reading_is_refused_not_a_bare_typeerror(bad):
    """``used=None`` used to raise a bare TypeError at the comparison — an
    unmapped 500, the #4051 class this gate promises to close."""
    def _weird(_org_id: str) -> MeterReading | None:
        return MeterReading(used=bad)

    with pytest.raises(QuotaCheckError):
        enforce_allowances(_limits(max_extraction_tokens=10), resource="sessions",
                           meters={"capture_extraction": _weird})


def test_the_absent_is_zero_semantics_are_declared_on_the_dimension():
    """A FLOW's absence is a zero; a GAUGE's is not. It is a DECLARATION, so a
    future flow dimension gets it without a meter-specific branch."""
    by_dim = {d.dimension: d for d in DIMENSIONS}
    assert by_dim["capture_extraction"].absent_is_zero is True
    assert by_dim["graph_storage"].absent_is_zero is False
