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

import logging
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


@pytest.mark.parametrize("reason", ["degraded", "schema_error", None])
def test_an_unrecognised_unmeasured_reason_is_refused_not_treated_as_absent(
        reason, monkeypatch):
    """⛔ DEFAULT-DENY: only the CERTAIN reason ("absent") may pass as a zero.

    Folding an unknown reason into "absent" would silently disarm a configured
    allowance the day the reader grows a new one (review round 5, P2).
    """
    # Drive the REAL adapter with a stubbed reader instead of the meter.
    monkeypatch.setattr(
        "tortoise.metering.get_capture_usage",
        lambda _org: {"measured": False, "unmeasured_reason": reason})
    with pytest.raises(QuotaCheckError):
        enforce_allowances(_limits(max_extraction_tokens=10),
                           resource="sessions")


def test_a_meter_returning_a_non_MeterReading_is_refused_not_an_AttributeError():
    """A non-``MeterReading`` used to escape as a bare ``AttributeError`` — on
    ``.used`` over the cap, and on ``.measured_at``/``.note``/``.estimated``
    elsewhere — an unmapped 500 (review rounds 6-7)."""
    from types import SimpleNamespace

    for bad in (lambda _o: 100.0, lambda _o: SimpleNamespace(used=11),
                lambda _o: SimpleNamespace(used=1, measured_at="x")):
        with pytest.raises(QuotaCheckError):
            enforce_allowances(_limits(max_extraction_tokens=10),
                               resource="sessions",
                               meters={"capture_extraction": bad})


def test_a_non_reading_on_an_UNLIMITED_dimension_is_logged_not_raised():
    """The observed-only path promises a broken meter cannot refuse a write
    nobody capped — even a non-``MeterReading`` (review round 7).

    ⚠️ The bounded half goes through a SYNTHETIC non-blocked dimension: driving
    it through ``graph_storage`` would refuse at the BLOCKED branch before the
    meter is called, so the test would stay green with the type gate deleted
    (review round 8).
    """
    from tortoise.allowance_gate import _reading

    bounded = AllowanceDimension(
        dimension="capture_extraction", limit_key="max_extraction_tokens",
        unit="tokens", meter=_meter(1), stale_after_seconds=60)
    with pytest.raises(QuotaCheckError):  # bounded: a non-reading is a refusal
        _reading(bounded, ORG, {"capture_extraction": lambda _o: 5.0},
                 observed_only=False)
    # unlimited (observed-only): logged, never raised, meter still called
    assert _reading(bounded, ORG, {"capture_extraction": lambda _o: 5.0},
                    observed_only=True) is None


# ── #9567 D12 (review round 9) — THE ARMING SEAM ──────────────────────────
#
# The gate reads the dict it is GIVEN, so a surface that does not carry an
# allowance key reads it as UNLIMITED. The MCP lane resolves its limits through
# `quota.resolve_org_limits` (where an allowance would be emitted) and carries
# them; every REST lane hand-assembles its own limits dict from its own store
# read and reads none of the allowance columns. So an arming edit made in the
# resolver alone would REFUSE on MCP and SERVE WITHOUT LIMIT on REST — the
# fail-open these tests pin shut at the ONE REST convergence point.

def _rest_org(**extra) -> dict:
    """A REST-shaped auth dict: the six resolved keys, no allowance key."""
    return {"org_id": ORG, "tier": "free", "max_users": None, "max_graphs": None,
            "max_points": None, "max_api_keys": None, "max_sessions": None,
            **extra}


def test_the_rest_gate_carries_an_allowance_resolved_by_the_resolver(monkeypatch):
    """`_check_org_limit` must fill the keys a REST lane did not carry.

    Mutation this REDs: drop the ``carry_resolved_allowances(org)`` call in
    ``hosted_api._check_org_limit`` and an allowance configured through
    ``quota.resolve_org_limits`` never reaches the REST door — the gate sees an
    ABSENT key, reads it as UNLIMITED, and the operation is served instead of
    refused (the exact MCP-refuses/REST-serves asymmetry this closes).

    ``graph_storage`` is armed because it is BLOCKED: a configured BLOCKED
    dimension refuses without reading a meter, so this is a pure unit assertion
    with no store and no instrument (the enforcement paths themselves are
    covered above).
    """
    from fastapi import HTTPException

    from tortoise import quota
    from tortoise.hosted_api import _check_org_limit

    monkeypatch.setattr(
        quota, "resolve_org_limits",
        lambda org_id: {"org_id": org_id, "tier": "free",
                        "max_graph_storage_mb": 100})
    with pytest.raises(HTTPException) as exc:
        _check_org_limit(_rest_org(), "points")
    # The documented fail-closed shape, naming the dimension — a dropped key
    # would have returned normally (max_points is None: nothing else refuses).
    assert exc.value.status_code == 500
    detail = str(exc.value.detail)
    assert "graph_storage" in detail, detail
    assert "blocked" in detail.lower(), detail


def test_the_resolver_is_not_consulted_when_the_dict_already_carries_every_key():
    """The MCP lane resolves these per request — carrying must not re-read.

    This is what keeps the fix off the MCP hot path (and off every door that
    already resolved its own allowances): no key is missing → NO store read.
    """
    from tortoise.allowance_gate import carry_resolved_allowances

    def _boom(_org_id: str):
        raise AssertionError("resolver must not be consulted")

    limits = _limits(**{key: None for key in ALLOWANCE_KEYS.values()})
    assert carry_resolved_allowances(limits, resolver=_boom) is limits


def test_the_carry_fills_only_missing_keys_and_never_overrides_the_surface():
    """A surface's own resolution WINS; only absent keys are filled, and the
    caller's dict is not mutated (the REST docstring promises no side effect)."""
    from tortoise.allowance_gate import carry_resolved_allowances

    limits = _limits(max_extraction_tokens=5)
    out = carry_resolved_allowances(
        limits, resolver=lambda _o: {"max_extraction_tokens": 99,
                                     "max_graph_storage_mb": 100})
    assert out["max_extraction_tokens"] == 5, out  # never overridden
    assert out["max_graph_storage_mb"] == 100, out  # the missing one filled
    assert "max_graph_storage_mb" not in limits  # input untouched


def test_a_resolver_fault_refuses_rather_than_reading_unlimited():
    """A config read we cannot complete for a cap that MAY be armed is a
    refusal, not a silent "unlimited" (D12; and it must be the documented
    ``QuotaCheckError``, never an unmapped 500 — #4051)."""
    from tortoise.allowance_gate import carry_resolved_allowances

    def _broken(_org_id: str):
        raise RuntimeError("control plane unreachable")

    with pytest.raises(QuotaCheckError) as exc:
        carry_resolved_allowances(_limits(), resolver=_broken)
    assert "allowances could not be resolved" in str(exc.value), str(exc.value)


# ── #9567 D12 (review round 11) — a LOOKUP MISS is not a FAULT ────────────
#
# Round 10 put the fill at the ONE REST convergence point and mapped its faults
# to the documented refusal — correct for a read we cannot COMPLETE. But the
# resolver also raises `OrgNotFoundError` when the org has NO RECORD, and that
# is a well-defined STATE: a cap cannot be armed from a record that does not
# exist, so the caller's already-authoritative dict stands and the dimension
# keeps the gate's declared default (absent = unconfigured = unlimited, #4051).
# Refusing it made `Team … not found in registry` a 500 on every gated REST door
# with NOTHING armed, while the SAME absence read as unlimited on every door
# that skips the fill — one state, two answers, depending on the door. These
# tests pin the three-way split so neither half can regress silently.

def test_a_resolver_with_no_record_for_the_org_leaves_the_dict_unarmed():
    """A lookup MISS returns the caller's dict UNCHANGED — never a refusal.

    Mutation this REDs: fold ``OrgNotFoundError`` back into the ``except
    QuotaCheckError: raise`` arm (the round-10 shape) and this raises instead of
    returning, which is the 500 that broke every REST suite whose fixture stubs
    an org with no registry row.
    """
    from tortoise.allowance_gate import carry_resolved_allowances
    from tortoise.quota import OrgNotFoundError

    def _no_record(org_id: str):
        raise OrgNotFoundError(f"Team {org_id!r} not found in registry")

    limits = _limits()
    out = carry_resolved_allowances(limits, resolver=_no_record)
    assert out is limits, out           # nothing synthesized, nothing dropped
    assert not any(k in out for k in ALLOWANCE_KEYS.values()), out


def test_an_org_the_resolver_has_no_record_for_is_served_at_the_rest_seam(
        monkeypatch):
    """The REST convergence point must NOT turn a missing org record into a 500.

    ``sessions`` is the door the capture route takes; ``max_sessions`` is None,
    so nothing else can refuse and a raise here is the lookup miss alone.

    Mutation this REDs: drop the ``except OrgNotFoundError: return limits`` arm
    in ``carry_resolved_allowances`` and this raises ``HTTPException(500)``, i.e.
    a healthy tenant refused over a record the resolver does not have.
    """
    from tortoise import quota
    from tortoise.hosted_api import _check_org_limit

    monkeypatch.setattr(
        quota, "resolve_org_limits",
        lambda org_id: (_ for _ in ()).throw(
            quota.OrgNotFoundError(f"Team {org_id!r} not found in registry")))
    assert _check_org_limit(_rest_org(), "sessions") is None


def test_the_no_record_state_is_still_a_fail_closed_QuotaCheckError():
    """The new distinction must not weaken every OTHER consumer.

    ``mcp_server._enforce_quota`` and the docs job let a ``QuotaCheckError``
    decide, where the resolved dict IS the answer — a missing org must still be
    refused there. Pinned as a subclass relationship, because a non-subclass
    would silently turn a missing org into a served request on those lanes.
    """
    from tortoise.quota import OrgNotFoundError

    assert issubclass(OrgNotFoundError, QuotaCheckError)


def test_resolve_org_limits_reports_a_missing_team_as_the_no_record_state(
        monkeypatch):
    """The resolver's own raise site carries the distinction (registry arm).

    Mutation this REDs: revert either raise site to a bare ``QuotaCheckError``
    and the seam can no longer tell "no record" from "read failed" — it would
    go back to refusing a state.
    """
    from tortoise import quota, supabase_control
    from tortoise.quota import OrgNotFoundError

    class _EmptyRegistry:
        def query(self, *_a, **_kw):
            return type("_R", (), {"result_set": []})()

    class _Sdk:
        def _get_registry(self):
            return _EmptyRegistry()

    monkeypatch.setattr(supabase_control, "is_supabase_enabled", lambda: False)
    monkeypatch.setattr(quota, "_make_sdk", lambda **_kw: _Sdk())
    with pytest.raises(OrgNotFoundError) as exc:
        quota.resolve_org_limits("team-with-no-row")
    assert "not found in registry" in str(exc.value), str(exc.value)


# ── #9567 D12 (review round 10) — the REST seam's REFUSAL SHAPE ───────────
#
# Round 9 put the carry at the ONE REST convergence point (`_check_org_limit`)
# but ABOVE the `try` that maps quota faults. Both of the carry's documented
# fail-closed faults therefore escaped the function unmapped — on the capture
# route that is the route's generic handler reporting OUR fault to the tenant as
# `"internal capture error — see server logs"`, i.e. their capture failing. The
# two tests below pin the mapped shape.

def _fault_resolver(_org_id: str):
    raise RuntimeError("control plane unreachable")


def test_a_resolver_fault_at_the_rest_seam_is_the_documented_500(monkeypatch):
    """A carry fault must reach the caller as ``HTTPException(500, "Quota check
    failed: …")`` — this module's documented fail-closed refusal — never as a
    bare ``QuotaCheckError``.

    Mutation this REDs: hoist ``carry_resolved_allowances(org)`` above the
    ``try:`` in ``hosted_api._check_org_limit`` (the round-9 shape) and the bare
    ``QuotaCheckError`` escapes instead of the mapped ``HTTPException`` — the
    #4051 class. Asserted on ``sessions``: the capture door the seam serves.
    """
    from fastapi import HTTPException

    from tortoise import quota
    from tortoise.hosted_api import _check_org_limit

    monkeypatch.setattr(quota, "resolve_org_limits", _fault_resolver)
    with pytest.raises(HTTPException) as exc:
        _check_org_limit(_rest_org(), "sessions")
    assert exc.value.status_code == 500, exc.value
    detail = str(exc.value.detail)
    assert detail.startswith("Quota check failed:"), detail
    assert "allowances could not be resolved" in detail, detail


def test_a_missing_allowance_gate_module_is_the_documented_500(monkeypatch,
                                                              caplog):
    """The gate's drift check is DELIBERATELY LOUD AT IMPORT, and
    ``quota.enforce_org_limit`` wraps the same import so the loudness lands
    inside ``QuotaCheckError`` (#4051). The REST seam imported it bare, on EVERY
    door — ``api_keys``/``users``/``graphs`` included — so an import-time drift
    escaped ``_check_org_limit`` as an ``ImportError``/``KeyError``.

    Mutations this REDs:
    * drop the inner ``try/except`` around the seam's
      ``from tortoise.allowance_gate import …`` and the import failure
      propagates instead of the mapped refusal;
    * drop the ``_logger.error(...)`` above the raise and the refusal is
      emitted with NO ERROR record at all (#686 — an import-time drift refuses
      every gated REST door, so it is the one fault here that must be pageable).
    """
    import sys

    from fastapi import HTTPException

    from tortoise.hosted_api import _check_org_limit

    monkeypatch.setitem(sys.modules, "tortoise.allowance_gate", None)
    with caplog.at_level(logging.ERROR), pytest.raises(HTTPException) as exc:
        # A NON-gated door: the import runs on every door, so the mapping
        # must hold there too (`users` is outside
        # ALLOWANCE_GATED_RESOURCES).
        _check_org_limit(_rest_org(), "users")
    assert exc.value.status_code == 500, exc.value
    detail = str(exc.value.detail)
    assert detail.startswith("Quota check failed:"), detail
    assert "allowance gate could not be loaded" in detail, detail
    # #686: and it must ALERT. The sibling `quota.enforce_org_limit` logs this
    # same fault at ERROR; the REST seam refused without logging anything while
    # the handler below it assumed "quota already logged the refusal" — true on
    # every other refusal path, false on this one, so the fault that takes the
    # whole REST surface down was silent here and alerting on MCP.
    errors = [r.getMessage() for r in caplog.records
              if r.levelno == logging.ERROR]
    assert any("allowance gate unavailable (fail-closed)" in m for m in errors), \
        errors


# ── #9567 review round 12 — the CARRY's LANE, and two records that lied ───
#
# The carry is a CONTROL-PLANE read (`quota.resolve_org_limits`: a PostgREST
# ladder in Supabase mode, a registry `Team` query in self-host mode) while the
# count it feeds is a GRAPH read. `_check_org_limit` used to do both, so the
# points doors submitted the pair through `_graph_offload` and a control-plane
# stall surfaced as `503 graph_unavailable` — the WRONG dependency's copy — with
# the bound miss abandoning a worker that keeps holding its graph-pool slot.


def test_the_points_door_carries_the_allowance_on_the_control_plane_lane(
        monkeypatch):
    """The carry must be submitted to the CONTROL-PLANE pool, not the graph pool.

    Mutation this REDs: revert the doors to
    ``_graph_offload(lambda: _check_org_limit(org, "points"))`` (the round-11
    shape) and the PostgREST read rides the graph pool again — the list below
    records two `graph` submissions and no `allowance_carry` at all.
    """
    import asyncio

    import tortoise.hosted_api as ha
    from tortoise import quota, supabase_control

    seen: list[tuple[str, str]] = []

    async def _cp(fn, *, op, **_kw):
        seen.append(("cp", op))
        return fn()

    async def _graph(fn, *, op, **_kw):
        seen.append(("graph", op))
        return fn()

    monkeypatch.setattr(ha, "_cp_offload", _cp)
    monkeypatch.setattr(ha, "_graph_offload", _graph)
    monkeypatch.setattr(supabase_control, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(quota, "resolve_org_limits", lambda oid: {"org_id": oid})

    asyncio.run(ha._check_org_limit_off_loop(_rest_org(), "points"))

    assert seen == [("cp", "check_org_limit.points.allowance_carry"),
                    ("graph", "check_org_limit.points")], seen


def test_the_sessions_door_carries_the_allowance_on_the_control_plane_lane(
        monkeypatch):
    """The CAPTURE door's carry rides the control-plane pool; its count does not.

    The same seam serves ``/v1/sessions`` with ``resource="sessions"``, and the
    split IS the seam: the two ALLOWANCE reads sit on two substrates, so they
    must be submitted to two pools — the carry to ``_cp_offload`` (the PostgREST
    lane) and the count to ``_graph_offload``. The capture door had no pin for
    its control-plane half: the route-level pin in
    ``tests/test_capture_loop_responsiveness.py``
    (``test_capture_sessions_gate_carry_rides_its_own_substrate_lane``) runs
    registry/self-host mode, where BOTH halves legitimately ride the graph pool
    (#3718), so a regression that sent this door's carry back to the graph pool
    — the #9567 round-12 defect this seam exists to prevent — would leave every
    capture-door test green.

    Mutation this REDs: move the carry in ``_carry_allowances_off_loop`` from
    ``_cp_offload`` to ``_graph_offload`` (round 12's inversion) and the first
    entry below becomes ``('graph', 'check_org_limit.sessions.allowance_carry')``
    — the carry still exists and still names itself, it simply rides the WRONG
    pool, which is what this list is here to see.

    The other half of the old defect is pinned one door up, in
    ``tests/test_capture_loop_responsiveness.py``
    (``test_capture_sessions_gate_carry_rides_its_own_substrate_lane``): the
    pre-fix ROUTE shape (``asyncio.to_thread(_check_org_limit, …)``) reds THAT
    test, not this one, because this test calls the seam directly and never
    drives ``/v1/sessions``.
    """
    import asyncio

    import tortoise.hosted_api as ha
    from tortoise import quota, supabase_control

    seen: list[tuple[str, str]] = []

    async def _cp(fn, *, op, **_kw):
        seen.append(("cp", op))
        return fn()

    async def _graph(fn, *, op, **_kw):
        seen.append(("graph", op))
        return fn()

    monkeypatch.setattr(ha, "_cp_offload", _cp)
    monkeypatch.setattr(ha, "_graph_offload", _graph)
    monkeypatch.setattr(supabase_control, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(quota, "resolve_org_limits", lambda oid: {"org_id": oid})

    asyncio.run(ha._check_org_limit_off_loop(
        _rest_org(), "sessions", op="check_org_limit.sessions"))

    assert seen == [("cp", "check_org_limit.sessions.allowance_carry"),
                    ("graph", "check_org_limit.sessions")], seen


def test_a_control_plane_bound_miss_on_the_carry_is_the_CONTROL_PLANE_503(
        monkeypatch):
    """A slow resolver on a points door yields the control-plane body.

    This is the review's requested pin: the fault must read as
    ``control_plane_unavailable`` — the dependency that actually failed — and
    never as ``graph_unavailable``.

    Mutation this REDs: put the carry back inside the graph submission and the
    patched control-plane bound is never applied, so the call SUCCEEDS after the
    (short, unpatched) graph bound and no 503 is raised at all.
    """
    import asyncio
    import time

    from fastapi import HTTPException

    import tortoise.hosted_api as ha
    from tortoise import monitoring, quota, supabase_control

    monkeypatch.setattr(monitoring, "CONTROL_PLANE_OFFLOAD_TIMEOUT_S", 0.05)
    monkeypatch.setattr(supabase_control, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(quota, "resolve_org_limits",
                        lambda _oid: time.sleep(0.4))

    async def _run():
        await ha._check_org_limit_off_loop(_rest_org(), "points")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_run())
    assert exc.value.status_code == 503, exc.value
    assert exc.value.detail["error_code"] == "control_plane_unavailable", \
        exc.value.detail


def test_the_capture_door_gets_a_capture_shaped_503_not_the_sign_in_copy(
        monkeypatch):
    """#10967: a control-plane stall on the CAPTURE door must say so.

    ``capture_session`` writes a refused capture's ``detail`` into the
    org-visible last-error slot (``_record_capture_last_error`` stringifies a
    dict detail via its ``message``), and the dashboard renders that string
    under ``Last attempt — …``. With no factory, ``_cp_offload`` raises
    ``_control_plane_unavailable``, whose message is the SIGN-IN line — so a
    tenant whose capture was refused by a QUOTA control-plane stall was told
    that their sign-in failed, on the wire AND on the dashboard.

    Both directions in one test, because the risk in a copy fix is
    OVER-widening: the sign-in line is a contract with
    ``website/apps/dashboard/public/signup.html`` and every other
    control-plane door legitimately carries it.

    * the capture door's factory → the capture body, ``error_code`` unchanged;
    * a door that passes NO factory → the sign-in body, byte for byte (the
      #3498 contract that makes the override opt-in at all).

    Mutation this REDs: drop ``unavailable=unavailable`` from
    ``_carry_allowances_off_loop``'s ``_cp_offload`` call — the first refusal
    becomes the sign-in copy. Apply the factory unconditionally (ignore the
    argument) and the second refusal reds instead.

    The rendered line is ``Last attempt — <message>``, so the message carries NO
    second em dash: this test pins a colon after its first clause for that
    reason (a copy edit that reintroduces one renders two separators in one
    sentence on the dashboard).
    """
    import asyncio
    import time

    from fastapi import HTTPException

    import tortoise.hosted_api as ha
    from tortoise import monitoring, quota, supabase_control

    monkeypatch.setattr(monitoring, "CONTROL_PLANE_OFFLOAD_TIMEOUT_S", 0.05)
    monkeypatch.setattr(supabase_control, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(quota, "resolve_org_limits",
                        lambda _oid: time.sleep(0.4))

    def _refusal(unavailable):
        async def _run():
            await ha._check_org_limit_off_loop(
                _rest_org(), "sessions", unavailable=unavailable)

        with pytest.raises(HTTPException) as exc:
            asyncio.run(_run())
        assert exc.value.status_code == 503, exc.value
        return exc.value.detail

    capture = _refusal(ha._capture_control_plane_unavailable)
    sign_in = _refusal(None)

    # A copy fix must not mint a new client-visible CODE: the failing dependency
    # really is the control plane in both cases.
    assert capture["error_code"] == "control_plane_unavailable", capture
    assert sign_in["error_code"] == "control_plane_unavailable", sign_in

    assert capture["message"] != sign_in["message"], (capture, sign_in)
    assert "Sign-in" not in capture["message"], capture
    assert "not stored" in capture["message"], capture
    # the dashboard prefix is `Last attempt — `, so the message must not carry a
    # SECOND em dash (two separators in one rendered sentence)
    assert "—" not in capture["message"], capture
    assert sign_in["message"] == (
        "Sign-in is temporarily unavailable — try again in a moment."), sign_in


def test_a_no_record_org_is_resolved_ONCE_across_the_two_lanes(monkeypatch):
    """The marker: the graph-lane half must not re-read a LOOKUP MISS.

    ``OrgNotFoundError`` is the one carried state that adds NO key, so without
    ``CARRIED_MARKER_KEY`` the enforcement call would consult the resolver a
    second time — on the very pool the carry was just hoisted off.

    Mutation this REDs: drop the marker check in
    ``carry_resolved_allowances`` and ``calls`` is ``[ORG, ORG]``.
    """
    import asyncio

    import tortoise.hosted_api as ha
    from tortoise import quota, supabase_control

    calls: list[str] = []

    def _no_record(org_id: str):
        calls.append(org_id)
        raise quota.OrgNotFoundError(f"Team {org_id!r} not found in registry")

    monkeypatch.setattr(supabase_control, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(quota, "resolve_org_limits", _no_record)

    asyncio.run(ha._check_org_limit_off_loop(_rest_org(), "points"))
    assert calls == [ORG], calls


def test_a_dimension_with_no_meter_is_not_described_as_BLOCKED():
    """A missing meter is a different state from a ruled-against dimension.

    Mutation this REDs: restore the single message that rendered
    ``"… but is BLOCKED — None"`` for a dimension whose ``blocked_reason`` is
    ``None`` (reachable through the ``meters=`` seam) and a fail-closed refusal
    names a ruling that does not exist.
    """
    with pytest.raises(QuotaCheckError) as exc:
        enforce_allowances(_limits(max_extraction_tokens=10), resource="sessions",
                           meters={"capture_extraction": None})
    msg = str(exc.value)
    assert "blocked" not in msg.lower(), msg
    assert "no meter plumbed" in msg, msg


def test_a_numeric_string_reading_is_COERCED_and_the_record_says_so():
    """The claim is COERCION, not a type gate — pinned so neither can drift.

    The design record once said a "non-numeric" reading is refused, which reads
    as a type gate; measured, ``MeterReading(used="5")`` is the number 5.0 and
    ``"50"`` is refused as OVER the cap, not as non-numeric. Both real meters
    hand back a float, so this is sound — and this test is where that stays
    true: a stricter ``isinstance(used, (int, float))`` gate would red the first
    assertion, and a claim of a type gate would red the second.
    """
    enforce_allowances(_limits(max_extraction_tokens=10), resource="sessions",
                       meters={"capture_extraction": _meter("5")})  # 5.0 < 10

    with pytest.raises(QuotaExceededError):
        enforce_allowances(_limits(max_extraction_tokens=10),
                           resource="sessions",
                           meters={"capture_extraction": _meter("50")})


# ── #9567 D12 (review round 15) — the MCP WRITE lane, not just capture ────
#
# Round 9-14 closed the arming seam at the ONE REST convergence point and at
# the MCP CAPTURE bridge. But `mcp_server._enforce_quota` is the pre-write gate
# for every `_quota_gated` MCP tool (~19 `points` writers, `documents` too), and
# it called `enforce_org_limit` DIRECTLY — never through
# `hosted_api._check_org_limit`, and without carrying the keys itself. In
# Supabase mode the dict it passes is `mcp_auth`'s `limits = org` (the
# `resolve_api_key` dict, whose shape has no allowance key and no column to read
# one from), so arming a dimension in the resolver REFUSED on the REST doors and
# SERVED WITHOUT LIMIT on every MCP points/documents tool — the same fail-open
# the capture bridge closed, one surface short.
#
# `graph_storage` is the armed dimension below because it is BLOCKED: a
# configured BLOCKED dimension refuses without reading a meter, so this is a pure
# unit assertion with no store and no instrument.

_UNSET = object()


def _mcp_lane(monkeypatch, limits=_UNSET):
    """Point `_enforce_quota` at a resolved MCP lane without a store or an SDK.

    ``limits`` distinguishes an ABSENT ContextVar (pass ``None`` — the resolver
    fallback) from "use the standard REST-shaped dict" (the default).
    """
    from tortoise import mcp_auth, mcp_server

    class _Fixed:
        def __init__(self, value):
            self._value = value

        def get(self):
            return self._value

    # `_enforce_quota` imports these from `tortoise.mcp_auth` inside the call.
    monkeypatch.setattr(mcp_auth, "_current_org_id", _Fixed(ORG))
    monkeypatch.setattr(
        mcp_auth, "_current_org_limits",
        _Fixed(_rest_org() if limits is _UNSET else limits))
    # The SDK argument is evaluated eagerly; nothing reads it — the armed BLOCKED
    # dimension refuses before any count.
    monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: None)
    return mcp_server


def test_the_mcp_write_lane_carries_an_allowance_resolved_by_the_resolver(
        monkeypatch):
    """`_enforce_quota` must fill the keys the MCP lane did not carry.

    Mutation this REDs: drop the ``_carry_allowances(limits, resource)`` call in
    ``mcp_server._enforce_quota`` and the allowance configured through
    ``quota.resolve_org_limits`` never reaches the tool — the gate sees an
    ABSENT key, reads it as UNLIMITED, and the write is SERVED instead of
    refused (``max_points`` is None here, so nothing else can refuse).
    """
    from tortoise import quota

    mcp_server = _mcp_lane(monkeypatch)
    monkeypatch.setattr(
        quota, "resolve_org_limits",
        lambda org_id: {"org_id": org_id, "tier": "free",
                        "max_graph_storage_mb": 100})

    with pytest.raises(QuotaCheckError) as exc:
        mcp_server._enforce_quota("points")
    detail = str(exc.value)
    assert "graph_storage" in detail, detail
    assert "blocked" in detail.lower(), detail


def test_the_mcp_write_lane_reads_the_resolver_only_for_a_GATED_resource(
        monkeypatch):
    """An ungated resource (`api_keys`) must pay no control-plane read.

    Mutation this REDs: drop the ``resource not in ALLOWANCE_GATED_RESOURCES``
    guard in ``_carry_allowances`` and a key mint — which no declared dimension
    gates — starts a resolver read whose fault would refuse an unrelated
    operation.
    """
    mcp_server = _mcp_lane(monkeypatch, limits={
        "org_id": ORG, "tier": "free", "max_users": None, "max_graphs": None,
        "max_points": None, "max_api_keys": None, "max_sessions": None})

    from tortoise import quota

    def _boom(_org_id: str):
        raise AssertionError("resolver must not be consulted")

    monkeypatch.setattr(quota, "resolve_org_limits", _boom)
    assert mcp_server._enforce_quota("api_keys") is None


def test_the_resolver_fallback_does_not_read_the_resolver_a_SECOND_time(
        monkeypatch):
    """One gate = at most one resolver read, even on the `limits is None` fallback.

    ``_enforce_quota`` resolves the limits itself when the middleware ContextVar
    is empty, and that answer IS the resolver's — its shape carries whatever an
    arming edit emits. Carrying it afterwards would issue the SAME control-plane
    read twice for one gate (a PostgREST ladder in Supabase mode).

    Mutation this REDs: drop the ``else:`` in ``_enforce_quota`` so
    ``_carry_allowances`` runs on the fallback too, and the resolver is read
    twice for a single pre-write gate.
    """
    mcp_server = _mcp_lane(monkeypatch, limits=None)
    from tortoise import quota

    calls: list[str] = []

    def _resolver(org_id: str):
        calls.append(org_id)
        # The resolver's own shape: no allowance key configured anywhere.
        return {"org_id": org_id, "tier": "free", "max_users": None,
                "max_graphs": None, "max_points": None, "max_api_keys": None,
                "max_sessions": None}

    monkeypatch.setattr(quota, "resolve_org_limits", _resolver)
    assert mcp_server._enforce_quota("points") is None
    assert calls == [ORG], calls
