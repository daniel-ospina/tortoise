"""#13084 — the per-account RAM cap armed at the tier number, and its estimate rules.

The cap bounds what a tenant may hold RESIDENT, per account, in the graph meter's
own unit (decimal MB). It never deletes anything, never counts nodes, and never
counts graphs (#11758, #9025/B7).

What is pinned here, in the order the issue asks for it:

* **arming** — solo/pro/team carry the tier's 30 %-of-price hot window, read from
  the artifact; free/anon are UNARMED (their artifact amount is an unset `0`,
  and a 0 cap would refuse the first byte);
* **the at-cap behaviour** — REFUSE the write, naming the cap and the tier and
  what to do, because the only evictable unit on FalkorDB is a whole graph;
* **the meter's honesty** — the reading is a LOWER BOUND (blind to the HNSW
  index), so a deadband warns at the cap and hard-refuses only at 1.5x, and a
  stale/absent reading is NO reading rather than 0 MB.
"""
from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tortoise import pricing
from tortoise.allowance_gate import (
    AT_CAP_WARNING,
    DIMENSIONS,
    HARD_STOP_MULTIPLE,
    UNMEASURED_UNARMED_WARNING,
    MeterReading,
    _graph_storage_mb,
    enforce_allowances,
    reset_signal_state_for_tests,
    unarmed_served_counts,
)
from tortoise.allowance_gate import _reading as _gate_reading
from tortoise.quota import QuotaCheckError, QuotaExceededError, _arm_graph_storage

ORG = "team-13084"


def _limits(**overrides) -> dict:
    return {"org_id": ORG, "tier": "solo", "max_points": None,
            "max_api_keys": None, "max_sessions": None, "max_users": None,
            "max_graphs": None, **overrides}


def _reading(used: float, *, measured_at: str | None = None,
             estimated: bool = True) -> MeterReading:
    return MeterReading(
        used=used,
        measured_at=measured_at or datetime.now(UTC).isoformat(),
        estimated=estimated,
        note="SAMPLING ESTIMATE that excludes the HNSW vector index",
    )


def _meter(used: float, **kwargs):
    return lambda _org: _reading(used, **kwargs)


# ── 1. arming: the tier number, from the artifact ─────────────────────────


def test_solo_pro_team_are_armed_at_the_derived_tier_numbers():
    """The tier numbers of ``SEARCH-ARCHITECTURE.md`` §8 — 77 / 102 / 615.

    §8 solves the window TOGETHER WITH DENSITY
    (``p = (0.30·P − $0.125) ÷ ($43.75/1000 + $85/8000)``, window ``= 0.75·p``),
    because the shared project line is part of the same budget; the "30 % of the
    price ÷ $74/GB-month" shortcut reproduces solo and nothing else (pro would be
    101.4, team 604.1). The figures are asserted against ``product/pricing.json``
    through its ONE accessor, so this test fails if either the cap or the price
    moves — which is the point: two numbers for one allowance is how a cap and a
    price drift.
    """
    assert pricing.storage_limits("solo")["max_graph_storage_mb"] == 77
    assert pricing.storage_limits("pro")["max_graph_storage_mb"] == 102
    assert pricing.storage_limits("team")["max_graph_storage_mb"] == 615
    for tier, expected in (("solo", 77), ("pro", 102), ("team", 615)):
        limits = _arm_graph_storage({"org_id": ORG, "tier": tier}, tier)
        assert limits["max_graph_storage_mb"] == expected, tier


def test_free_and_anon_are_UNARMED_and_say_so():
    """The artifact's ``0`` for those tiers is a RECORDED amount, and arming it is a landmine.

    ``product/pricing.json`` states it (`_included_amounts_status`: *"hot
    capacity 0 is stated by the decision record"*), and §8 of
    `SEARCH-ARCHITECTURE.md` tabulates their window target as 0 — so the
    dimension being UNARMED is a deliberate departure from a recorded figure,
    not an unpopulated artifact. ``enforce_allowances`` refuses at
    ``used >= allowance``, so emitting the ``0`` would refuse EVERY gated write —
    including a tenant's very first byte — on tiers that still grant a graph.
    The amount must arrive as an ABSENT key (unarmed), never as a zero cap.
    """
    for tier in ("free", "anon"):
        limits = _arm_graph_storage({"org_id": ORG, "tier": tier}, tier)
        assert "max_graph_storage_mb" not in limits, tier
        # ...and if a surface ever emits the 0 anyway, the gate is defended.
        enforce_allowances(_limits(tier=tier, max_graph_storage_mb=0),
                           meters={"graph_storage": _meter(0.0)})


def test_a_NEGATIVE_artifact_amount_REFUSES_rather_than_reading_as_unset(monkeypatch):
    """A negative is a corrupt figure, not the artifact's ``0`` unarmed sentinel.

    Reading ``<= 0`` as unarmed left a PAID tier silently UNCAPPED, while the
    money path raises ``ValueError`` on the same value — one corrupt input, two
    opposite answers (#13102 review, P3). The arming seam now fails closed, the
    way it already does for an unreadable amount.
    """
    monkeypatch.setattr(pricing, "storage_limits",
                        lambda _tier: {"max_graph_storage_mb": -5})
    with pytest.raises(QuotaCheckError, match="negative"):
        _arm_graph_storage({"org_id": ORG, "tier": "solo"}, "solo")


def test_a_NEGATIVE_limit_in_the_gate_REFUSES_rather_than_uncapping():
    """The gate's defensive half: only ZERO is the unarmed sentinel.

    A surface that hands the gate a negative allowance (a hand-built dict, an
    older resolver) must not get an uncapped paid tier out of it.
    """
    with pytest.raises(QuotaCheckError, match="negative"):
        enforce_allowances(_limits(max_graph_storage_mb=-5),
                           meters={"graph_storage": _meter(0.0)})


def test_the_armed_allowance_reaches_the_gate_through_the_carry():
    """The arming seam (#9567 D12): a resolved allowance + the shared carry."""
    from tortoise.allowance_gate import carry_resolved_allowances

    resolved = _arm_graph_storage({"org_id": ORG, "tier": "solo"}, "solo")
    limits = carry_resolved_allowances({"org_id": ORG, "tier": "solo"},
                                       resolver=lambda _o: resolved)
    assert limits["max_graph_storage_mb"] == 77
    with pytest.raises(QuotaExceededError):
        enforce_allowances(limits, meters={"graph_storage": _meter(200.0)})


# ── 2. the at-cap behaviour: REFUSE the write, and say what to do ──────────


def test_at_the_tier_number_the_cap_only_WARNS(caplog):
    """An ESTIMATE may not be the sole cause of a hard refusal (deadband)."""
    with caplog.at_level("WARNING"):
        enforce_allowances(_limits(max_graph_storage_mb=77),
                           meters={"graph_storage": _meter(77.0)})
        enforce_allowances(_limits(max_graph_storage_mb=77),
                           meters={"graph_storage": _meter(100.0)})
    assert AT_CAP_WARNING in caplog.text


def test_the_hard_stop_is_1_5x_the_cap_and_refuses_with_a_usable_message():
    hard_stop = 77 * HARD_STOP_MULTIPLE
    assert hard_stop == 115.5
    enforce_allowances(_limits(max_graph_storage_mb=77),
                       meters={"graph_storage": _meter(hard_stop - 0.5)})
    with pytest.raises(QuotaExceededError) as caught:
        enforce_allowances(_limits(max_graph_storage_mb=77),
                           meters={"graph_storage": _meter(hard_stop)})
    message = str(caught.value)
    # the cap, the tier, the hard stop, why they differ, and what to do
    assert "77 MB" in message and "solo plan" in message
    assert "115.5 MB" in message and "1.5x the cap" in message
    # ⛔ AND THE CLAIM IS QUALIFIED (#13102 P1-3). The old text asserted the figure
    # was a LOWER BOUND full stop; the systematic exclusions do remove bytes, but a
    # SAMPLING estimate wobbles both ways, so the lower-bound property holds only
    # UP TO the sampling error — and this reading has no measured spread, so the
    # message must say the error is UNMEASURED rather than quote a number nobody
    # took.
    assert "LOWER BOUND" in message
    assert "UNMEASURED" in message and "single sample" in message
    assert "cannot be refused" not in message
    assert "Upgrade your plan" in message
    assert "reduce what you hold resident" in message
    # the structured payload carries the figures the gate COMPARED — including
    # the FRACTIONAL ones: `int(115.5)` used to reach the payload as 115, AND
    # `limit` reported the 1.0x cap (77) while the gate refused at the 1.5x hard
    # stop (115.5), so a machine reader saw `{used: 115.5, limit: 77}` and had to
    # recover the real threshold from the prose (#13102 payload findings).
    assert caught.value.resource == "graph_storage"
    assert caught.value.used == 115.5
    assert caught.value.limit == 115.5
    assert caught.value.estimated is True  # never presented as exact


def test_the_refusal_names_the_dimension_and_the_meter_caveat():
    with pytest.raises(QuotaExceededError) as caught:
        enforce_allowances(
            _limits(max_graph_storage_mb=77),
            meters={"graph_storage": lambda _o: MeterReading(
                used=200.0, measured_at=datetime.now(UTC).isoformat(),
                estimated=True,
                note="SAMPLING ESTIMATE that excludes the HNSW vector index")})
    assert "HNSW vector index" in str(caught.value)


#: Every way a door reaches the allowance gate. A READ that called one of these
#: could be refused by the armed cap, which is the one thing this cap must never
#: do — so the set of call sites is pinned below, not left to inspection.
_GATE_ENTRY_POINTS = frozenset({
    "_check_org_limit", "_check_org_limit_off_loop", "_enforce_quota",
    "enforce_allowances", "enforce_org_limit",
})

#: ⛔ THE DECLARED DOOR LIST — every allowance-gate call site in the package, as
#: ``(module, enclosing function, resource it gates)``. ``None`` marks a SEAM that
#: forwards a caller-supplied resource (``hosted_api._check_org_limit``,
#: ``mcp_server._enforce_quota``, ``quota.enforce_org_limit``).
#:
#: Each entry is a claim that the enclosing function is a WRITE door. Adding one
#: is a deliberate act with a reviewer looking at it — that is the whole point:
#: an armed cap that a READ could reach would stop a tenant reading its own graph.
_GATE_DOORS: frozenset[tuple[str, str, str | None]] = frozenset({
    # the REST seams (both forward their caller's resource)
    ("tortoise/hosted_api.py", "_check_org_limit", None),
    ("tortoise/hosted_api.py", "_check_org_limit_off_loop", None),
    # the REST write doors
    ("tortoise/hosted_api.py", "_create_object", "points"),
    ("tortoise/hosted_api.py", "_create_subject", "points"),
    ("tortoise/hosted_api.py", "_create_point", "points"),
    ("tortoise/hosted_api.py", "create_api_key", "api_keys"),
    ("tortoise/hosted_api.py", "rotate_api_key", "api_keys"),
    ("tortoise/hosted_api.py", "_capture_session_impl", "sessions"),
    ("tortoise/hosted_api.py", "_commit_region", "points"),
    ("tortoise/hosted_api.py", "_seed_demo_sync", "points"),
    ("tortoise/hosted_api.py", "_run_docs_indexing", "documents"),
    ("tortoise/hosted_api.py", "_docs_quota_check", "documents"),
    # the MCP seam and its two doors
    ("tortoise/mcp_server.py", "_enforce_quota", None),
    ("tortoise/mcp_server.py", "_gated", None),
    ("tortoise/mcp_server.py", "tortoise_onboarding_demo_create", None),
    # the gate itself: the one production call, plus the observed-only report
    ("tortoise/quota.py", "enforce_org_limit", None),
    ("tortoise/quota.py", "org_quota_state", None),
})


def _gate_call_sites(source: str, module: str = "<source>") -> set[tuple[str, str, str | None]]:
    """Every ``(module, enclosing function, resource)`` gate call in *source*.

    AST rather than a text search so a citation in prose (``enforce_org_limit``
    is named in several docstrings) cannot redden the guarantee, and the ENCLOSING
    FUNCTION is what distinguishes a write door from a read one. Aliases are
    resolved too: ``from .allowance_gate import enforce_allowances as _ea``
    followed by ``_ea(...)`` is the same capability under another name, and a
    detector that only knows the canonical spelling is a detector a diff can walk
    past.
    """
    tree = ast.parse(source, filename=module)
    names = set(_GATE_ENTRY_POINTS)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in _GATE_ENTRY_POINTS and alias.asname:
                    names.add(alias.asname)

    sites: set[tuple[str, str, str | None]] = set()

    def walk(node: ast.AST, enclosing: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            enclosing = node.name
        if isinstance(node, ast.Call):
            func = node.func
            name = (func.attr if isinstance(func, ast.Attribute)
                    else getattr(func, "id", None))
            if name in names:
                resource: str | None = None
                for kw in node.keywords:
                    if kw.arg == "resource" and isinstance(kw.value, ast.Constant):
                        resource = kw.value.value
                if (resource is None and len(node.args) >= 2
                        and isinstance(node.args[1], ast.Constant)):
                    resource = node.args[1].value
                sites.add((module, enclosing, resource))
        for child in ast.iter_child_nodes(node):
            walk(child, enclosing)

    walk(tree, "<module>")
    return sites


def test_no_read_path_can_reach_the_allowance_gate():
    """⛔ THE SAFETY HALF OF THIS CHANGE: a READ is never gated.

    An armed cap that a READ could reach would stop an over-cap tenant reading
    its own graph — the ``noeviction`` guarantee the body states, and the one
    thing a residency cap must never do.

    Pinned as the package-wide SET of gate call sites, each declared in
    ``_GATE_DOORS`` with the resource it gates. The earlier version of this test
    scanned ``tortoise/sdk.py`` alone, where no gate call has ever lived — so a
    gate added to a REST GET handler (``hosted_api", ``/v1/search``) left it
    green (#13102 review round 2). Now a new call site — in a read handler, a new
    module, anywhere in the package — reddens this until it is declared, which is
    the moment someone can ask whether it is a WRITE door.
    """
    # 1. the detector: it must SEE a gate call, or an empty result below means
    #    nothing (an alias included, since that is the bypass an alias exists for).
    assert _gate_call_sites(
        "from .allowance_gate import enforce_allowances as _ea\n"
        "def _write_door():\n"
        "    _ea({}, resource='points')\n"
    ) == {("<source>", "_write_door", "points")}

    # 2. every call site in the package, declared or not.
    pkg = Path(__file__).resolve().parents[1] / "tortoise"
    found: set[tuple[str, str, str | None]] = set()
    for path in sorted(pkg.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        # Fast path: a file that never spells one of these cannot call one (an
        # aliased import still spells the canonical name it is aliased FROM).
        if not any(name in source for name in _GATE_ENTRY_POINTS):
            continue
        found |= _gate_call_sites(
            source, str(path.relative_to(pkg.parent)))

    added = sorted(found - _GATE_DOORS)
    gone = sorted(_GATE_DOORS - found)
    assert not added, (
        "new allowance-gate call site(s) — a READ path reaching the gate would "
        "make reads refusable by the armed cap. If the new one IS a write door, "
        f"declare it in _GATE_DOORS with the resource it gates: {added}")
    assert not gone, (
        "a declared allowance-gate call site is gone (a renamed or removed door) "
        f"— update _GATE_DOORS: {gone}")


def test_the_armed_cap_refuses_only_its_own_WRITE_doors():
    """The behavioural half: over-cap REFUSES on this dimension's doors, serves
    elsewhere — with the over-cap meter proven to reach a comparison.

    The earlier version of this test drove ``resource="api_keys"`` and
    ``resource="sessions"`` only, and that passed for a SKIP reason rather than a
    read reason: neither is a door of this dimension (``api_keys`` is not in
    ``ALLOWANCE_GATED_RESOURCES`` at all, and ``graph_storage``'s doors are
    ``{"points", "documents"}``), so both calls returned before any comparison
    (#13102 review, finding 3). The positive control is what fixes that.

    ⚠️ ``sessions`` serving here is NOT a virtue: the capture lane writes turn
    Points into the tenant's graph and this dimension does not gate it, so the
    armed cap can be walked past on that door — tracked on #13134. This line is
    also what reddens the day the door set is widened to close it, so the change
    is made deliberately rather than silently.
    """
    armed = _limits(max_graph_storage_mb=77)
    over_cap = {"graph_storage": _meter(10_000.0)}

    # 1. positive control: the WRITE doors refuse, far over the cap.
    for door in ("points", "documents"):
        with pytest.raises(QuotaExceededError):
            enforce_allowances(armed, resource=door, meters=over_cap)

    # 2. a resource this dimension does not gate still serves (see the caveat).
    for door in ("api_keys", "sessions"):
        enforce_allowances(armed, resource=door, meters=over_cap)


# ── 3. the meter's honesty: UNMEASURED is a state, never a refusal, here ───
#
# ⛔ THE P0 OF #13102 (#13102 P0-1). "Absent" was already served (a row the
# scheduled sweep has not written yet), but EVERY OTHER unmeasured state — a read
# fault, an unageable row, a reading older than the bound — raised
# ``QuotaCheckError``, which the REST/MCP seams surface as **HTTP 500**. The gauge
# has exactly ONE production writer (the backup sweep), so that turned a stopped
# sweep into a customer-wide write outage, blamed the tenant for our bookkeeping
# state, and refused a brand-new paid org its FIRST write. The dimension now
# declares ``unmeasured_is_unarmed``: the write is SERVED, the cap does not bind,
# and the operator is told (log + counter + a durable incident for the fault
# reasons). Default-deny is preserved as a DECLARATION — see the closing test.


def _ledger_reading(monkeypatch, **fields):
    """Stub the reader with one ledger payload (the real one's key vocabulary)."""
    from tortoise import metering

    payload = {"graph_storage_mb": 0.0, "graph_storage_measured_at": None,
               "measured": False, "unmeasured_reason": "absent"}
    payload.update(fields)
    monkeypatch.setattr(metering, "get_graph_storage_reading",
                        lambda _org: dict(payload))


@pytest.fixture
def signals():
    """Reset the gate's signal state and capture the operator alerts it fires."""
    from tortoise import operator_alert

    reset_signal_state_for_tests()
    fired: list[tuple[str, str | None, dict]] = []

    def _capture(kind, org_id, detail=None):
        fired.append((kind, org_id, dict(detail or {})))
        return None

    original = operator_alert.alert_operator
    operator_alert.alert_operator = _capture
    try:
        yield fired
    finally:
        operator_alert.alert_operator = original
        reset_signal_state_for_tests()


def test_a_tenant_with_NO_ledger_ROW_is_served_not_refused(monkeypatch, signals):
    """The #13102 regression: a fresh paying tenant must be able to write.

    Pre-fix this raises ``QuotaCheckError`` ("has no reading … refusing to fail
    open"), which the REST/MCP seams surface as a 500 on the tenant's very first
    point — the deadlock measured in ``hosted-e2e`` and ``test-slow (b)``: the
    row is written by a scheduled sweep, so refusing the write can never make it
    appear. #3981's ruling forbids refusing a paying org over our own bookkeeping
    state.
    """
    _ledger_reading(monkeypatch, unmeasured_reason="absent")
    enforce_allowances(_limits(max_graph_storage_mb=102))  # the pro cap
    # …and the cost of that is stated where the arming is declared, not hidden:
    # the cap does not bind until the gauge first reports (under-enforcement).
    dim = {d.dimension: d for d in DIMENSIONS}["graph_storage"]
    assert dim.absent_is_zero and dim.unmeasured_is_unarmed, (
        "graph_storage must declare that an absent row is consumed as a fact AND "
        "that an unmeasured one never refuses; without either the gate refuses a "
        "brand-new paid tenant")
    # LOUD, but not an incident: this is every new tenant and every window
    # boundary, so it is counted, not paged.
    assert unarmed_served_counts()[("graph_storage", "absent")] == 1
    assert not [f for f in signals if "ALLOWANCE_CAP" in f[0]], signals


def test_an_UNARMED_dimension_counts_NOTHING_as_under_enforcement(
        monkeypatch, signals, caplog):
    """#13102 review round 8 (F2): on the UNLIMITED path, `absent` is not a cost.

    ``enforce_allowances`` calls ``_reading(..., observed_only=True)`` exactly
    when the dimension is unarmed BY CONFIGURATION (``allowance is None``, or the
    artifact's ``0`` unset sentinel with ``zero_means_unarmed`` — free/anon, and
    any limits dict that spreads ``pricing.storage_limits()``). ``_reading``'s
    ``ABSENT`` + ``absent_is_zero`` branch signalled anyway, so a deliberately
    uncapped tenant produced, per org per hour, "the cap does NOT bind for this
    org **until a usable reading exists** (under-enforcement)" — two false causal
    claims, since nothing is armed and no reading can ever make a cap bind.

    The counter is the instrument #9559 reads to tell *"the meter never reports
    for a PAID tenant"* from *"free-tier traffic exists"*, so counting an unarmed
    dimension there destroyed the very distinction the signal exists to make.
    Every neighbouring branch already handled the unlimited case explicitly (a
    fault and a non-reading both log "dimension unlimited, no refusal" and leave
    the counter alone); ``absent`` was the one that did not.

    RED on the pre-fix tree: the counter reads ``{('graph_storage', 'absent'): 2}``
    and ``UNMEASURED_UNARMED_WARNING`` is in the log.
    """
    _ledger_reading(monkeypatch, unmeasured_reason="absent")
    with caplog.at_level("WARNING"):
        for limits in (
                _limits(tier="free", max_graph_storage_mb=0),   # the unset sentinel
                _limits(tier="free", max_graph_storage_mb=None),  # documented unlimited
                # the "second arming seam" this PR guards against: a limits dict
                # that spreads the artifact's figures, so an UNPAID tier arrives
                # with the amount present-and-zero rather than absent.
                {**_limits(tier="anon"),
                 **pricing.storage_limits("anon")}):
            enforce_allowances(limits)
    assert unarmed_served_counts() == {}, (
        "an unarmed dimension must not be counted as under-enforcement — that "
        f"counter is how #9559 tells a broken meter from free-tier traffic: "
        f"{unarmed_served_counts()}")
    assert UNMEASURED_UNARMED_WARNING not in caplog.text, caplog.text
    assert signals == [], signals


@pytest.mark.parametrize("reason", ["unreported", "read_error", "period_error"])
def test_a_FAULT_on_the_gauge_is_served_AND_FILES_AN_INCIDENT(
        monkeypatch, signals, caplog, reason):
    """A fault is OURS, so it cannot refuse the tenant — and it must page.

    RED pre-fix: ``QuotaCheckError`` → HTTP 500 for every gated write of the org
    while the store is degraded (the review's "customer-wide write outage whose
    message presents it as the tenant's own state").
    """
    _ledger_reading(monkeypatch, unmeasured_reason=reason)
    with caplog.at_level("ERROR"):
        enforce_allowances(_limits(max_graph_storage_mb=102))
    assert UNMEASURED_UNARMED_WARNING in caplog.text
    assert reason in caplog.text
    assert unarmed_served_counts()[("graph_storage", reason)] == 1
    kinds = [(kind, org, detail.get("reason"))
             for kind, org, detail in signals]
    assert kinds == [("ALLOWANCE_CAP_UNENFORCEABLE", ORG, reason)], kinds


def test_an_UNKNOWN_reader_reason_is_unarmed_here_but_never_read_as_absent(
        monkeypatch, signals):
    """Default-deny still governs the TOKEN: an unrecognised reason is not `absent`.

    It is reported as ``unknown_reason`` (so a future reader reason cannot mint
    free text in a create-once incident body) and, on this dimension, it is
    unarmed rather than a refusal — because whatever the reason, the row is still
    written by the sweep and the tenant still cannot influence it.
    """
    from tortoise.allowance_gate import MeterUnavailable

    _ledger_reading(monkeypatch,
                    unmeasured_reason="a_reason_this_gate_has_never_seen")
    with pytest.raises(MeterUnavailable) as caught:
        _graph_storage_mb(ORG)
    assert caught.value.reason == "unknown_reason"
    enforce_allowances(_limits(max_graph_storage_mb=102))
    assert [d.get("reason") for _k, _o, d in signals] == ["unknown_reason"]


def test_a_missing_measured_at_is_no_reading_and_serves_loudly(
        monkeypatch, signals):
    """A reading with no timestamp and NO named reason is still not a zero.

    The zero view and a degraded read both carry no timestamp, so the reason is
    what decides: an omission (a stub, an older seam) must not be read as
    ``absent``. It becomes the gate's ``unknown_reason`` and — on this dimension —
    an unarmed serve, never a 0 MB comparison.
    """
    from tortoise.allowance_gate import MeterUnavailable

    _ledger_reading(monkeypatch, unmeasured_reason=None)
    with pytest.raises(MeterUnavailable):
        _graph_storage_mb(ORG)
    enforce_allowances(_limits(max_graph_storage_mb=77))
    assert [d.get("reason") for _k, _o, d in signals] == ["unknown_reason"]


def test_a_PRESENT_timestamp_with_a_NULL_figure_is_not_zero(monkeypatch, signals):
    """#13102 P2: ``float(x or 0.0)`` read a null figure as 0 MB — a fail-open.

    A row can carry a valid age and no figure at all (the writer deposited an
    unageable/absent total). The old line compared it as ``0 MB`` and passed it as
    "under the cap" silently, which is the exact fail-open this dimension's
    ``absent_is_zero=False`` posture exists to stop, one field narrower.
    """
    from tortoise.allowance_gate import MeterUnavailable

    _ledger_reading(monkeypatch, graph_storage_mb=None,
                    graph_storage_measured_at=datetime.now(UTC).isoformat())
    with pytest.raises(MeterUnavailable) as caught:
        _graph_storage_mb(ORG)
    assert caught.value.reason == "missing_figure"
    enforce_allowances(_limits(max_graph_storage_mb=77))
    # served (this dimension is unarmed on an unmeasurable figure) and NAMED —
    # never compared as a zero.
    assert [d.get("reason") for _k, _o, d in signals] == ["missing_figure"]


def test_a_NON_FINITE_figure_is_not_a_reading(monkeypatch):
    from tortoise.allowance_gate import MeterUnavailable

    _ledger_reading(monkeypatch, graph_storage_mb=float("nan"),
                    graph_storage_measured_at=datetime.now(UTC).isoformat())
    with pytest.raises(MeterUnavailable) as caught:
        _graph_storage_mb(ORG)
    assert caught.value.reason == "missing_figure"


def test_a_STALE_reading_is_served_and_pages_rather_than_refusing(
        monkeypatch, signals):
    """The 24 h bound still exists — but it ARM-DISARMS, it does not refuse.

    Enforcing a stale figure would decide from a number we cannot defend;
    refusing it turns "the sweep stopped" into "the tenant cannot write". So the
    gate serves, and the operator gets an incident naming ``stale``.
    """
    stale = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    _ledger_reading(monkeypatch, graph_storage_mb=10.0,
                    graph_storage_measured_at=stale)
    enforce_allowances(_limits(max_graph_storage_mb=77))
    assert unarmed_served_counts()[("graph_storage", "stale")] == 1
    assert [d.get("reason") for _k, _o, d in signals] == ["stale"]


def test_a_fresh_reading_under_the_cap_is_served(monkeypatch):
    _ledger_reading(monkeypatch, graph_storage_mb=12.5,
                    graph_storage_measured_at=datetime.now(UTC).isoformat())
    enforce_allowances(_limits(max_graph_storage_mb=77))
    assert unarmed_served_counts() == {}


def test_a_dimension_that_does_NOT_declare_it_STILL_refuses(monkeypatch):
    """⛔ THE DEFAULT-DENY HALF — the declaration must not have widened anything.

    ``capture_extraction``'s figure is a ledger row its OWN writes create, so an
    absence there is genuinely a refusal (#686). Pinned twice: once at the gate on
    a synthetic dimension, once on the real dimension's declaration.
    """
    from tortoise.allowance_gate import AllowanceDimension, MeterUnavailable

    dim = {d.dimension: d for d in DIMENSIONS}
    assert dim["capture_extraction"].unmeasured_is_unarmed is False
    assert dim["postgres_storage"].unmeasured_is_unarmed is False
    synthetic = AllowanceDimension(
        dimension="capture_extraction", limit_key="max_extraction_tokens",
        unit="tokens", meter=lambda _o: None, stale_after_seconds=60)
    with pytest.raises(QuotaCheckError):
        _gate_reading(synthetic, ORG, {"capture_extraction": lambda _o: None},
                      observed_only=False)
    with pytest.raises(MeterUnavailable):
        _extraction_probe(monkeypatch)


def _extraction_probe(monkeypatch):
    """Drive ``_extraction_tokens``'s own fault arm (a reader with no window)."""
    from tortoise import metering

    monkeypatch.setattr(metering, "get_capture_usage",
                        lambda _o: {"measured": False,
                                    "unmeasured_reason": "period_error"})
    from tortoise.allowance_gate import _extraction_tokens
    return _extraction_tokens(ORG)


# ── 3b. the deadband rests on a MEASURED spread where one exists ──────────
#
# #13102 P1-2: the 1.5x band used to be justified by "the meter's own reported
# spread (a few percent)" — a figure the gate never read. It now reads the
# ledger's own ``spread_mb``/``repeats``: where they are a MEASUREMENT (two or
# more repeats) the hard stop moves out to the point where even the LOW end of the
# observed range is over the cap; where they are not, the refusal says the error
# is UNMEASURED.


def test_the_measured_range_sets_the_hard_stop(monkeypatch):
    """Where the dispersion is measured, the stop rests on the OBSERVED MINIMUM.

    ``used`` is the MEDIAN of the repeats, not their midpoint, so ``used -
    spread/2`` is NOT the low end of the reading (repeats ``[50, 200, 201]`` →
    used 200, spread 151, observed minimum 50). The gate therefore compares the
    recorded minimum: it refuses only once the lowest SAMPLE it took is itself over
    the cap. With used 127 / min 77 on a 77 MB cap the stop is 127 MB, so 126.9 is
    served and 127 is refused — where the pre-fix code refused both at 115.5, on a
    band whose justification ("the meter's own spread") it never read.
    """
    fresh = datetime.now(UTC).isoformat()

    def _ranged(used: float, low: float = 76.9):
        return lambda _org: MeterReading(
            used=used, measured_at=fresh, estimated=True, spread=50.0,
            repeats=3, low=low)

    enforce_allowances(_limits(max_graph_storage_mb=77),
                       meters={"graph_storage": _ranged(126.9)})
    with pytest.raises(QuotaExceededError) as caught:
        enforce_allowances(_limits(max_graph_storage_mb=77),
                           meters={"graph_storage": _ranged(127.0, low=77.0)})
    message = str(caught.value)
    assert "50 MB across 3 samples" in message, message
    assert "lowest was 77 MB" in message, message
    assert "from the observed samples" in message, message
    assert "UNMEASURED" not in message
    # the payload's `limit` is still the threshold the gate COMPARED
    assert caught.value.limit == 127.0


def test_a_measured_minimum_over_the_cap_MAY_refuse_before_the_declared_band():
    """A refusal the measurement supports is not a "sole estimate" refusal.

    The declared 1.5x margin exists because an UNMEASURED estimate must not be the
    sole cause of a refusal. Once the reading carries several samples and ALL of
    them are over the cap, the refusal rests on the measurement — so it may fire
    inside the band (here at 70+ used with min 78 on a 77 MB cap).
    """
    fresh = datetime.now(UTC).isoformat()
    with pytest.raises(QuotaExceededError):
        enforce_allowances(_limits(max_graph_storage_mb=77),
                           meters={"graph_storage": lambda _o: MeterReading(
                               used=90.0, measured_at=fresh, estimated=True,
                               spread=24.0, repeats=3, low=78.0)})
    # …and the same figure with NO measured range is served (declared margin)
    enforce_allowances(_limits(max_graph_storage_mb=77),
                       meters={"graph_storage": _meter(90.0)})


def test_the_half_spread_is_never_used_as_the_low_end():
    """The counterexample, pinned: median 200 with a dip to 50 is NOT a 124.5 floor."""
    fresh = datetime.now(UTC).isoformat()
    enforce_allowances(_limits(max_graph_storage_mb=77),
                       meters={"graph_storage": lambda _o: MeterReading(
                           used=200.0, measured_at=fresh, estimated=True,
                           spread=151.0, repeats=3, low=50.0)})


def test_a_single_repeat_reports_NO_spread_or_low_not_zero(monkeypatch):
    """``repeats == 1`` records ``0.0`` — that is ABSENCE, not precision."""
    _ledger_reading(monkeypatch, graph_storage_mb=10.0,
                    graph_storage_measured_at=datetime.now(UTC).isoformat(),
                    graph_storage_repeats=1, graph_storage_spread_mb=0.0,
                    graph_storage_min_mb=0.0)
    reading = _graph_storage_mb(ORG)
    assert reading is not None
    assert reading.spread is None and reading.low is None
    assert reading.repeats == 1


def test_a_measured_range_is_carried_from_the_ledger(monkeypatch):
    _ledger_reading(monkeypatch, graph_storage_mb=10.0,
                    graph_storage_measured_at=datetime.now(UTC).isoformat(),
                    graph_storage_repeats=3, graph_storage_spread_mb=4.0,
                    graph_storage_min_mb=8.0)
    reading = _graph_storage_mb(ORG)
    assert reading is not None
    assert (reading.spread, reading.repeats, reading.low) == (4.0, 3, 8.0)


def test_a_half_measured_range_is_discarded_WHOLE(monkeypatch):
    """A usable minimum with an unusable spread is NO range — not a crash.

    The verification pass measured the crash this prevents: with ``low`` present
    and ``spread`` absent, the refusal text formatted ``None`` with ``:g`` and the
    refusal path raised ``TypeError`` instead of a ``QuotaExceededError``. The pair
    is now validated once (``_dispersion``), so a half-measured row degrades to the
    declared margin rather than to an exception.
    """
    _ledger_reading(monkeypatch, graph_storage_mb=10.0,
                    graph_storage_measured_at=datetime.now(UTC).isoformat(),
                    graph_storage_repeats=3, graph_storage_spread_mb=None,
                    graph_storage_min_mb=8.0)
    reading = _graph_storage_mb(ORG)
    assert reading is not None
    assert (reading.spread, reading.low) == (None, None)
    # …and a hand-built reading with a minimum but no spread renders a refusal,
    # rather than raising out of the formatter.
    fresh = datetime.now(UTC).isoformat()
    with pytest.raises(QuotaExceededError) as caught:
        enforce_allowances(_limits(max_graph_storage_mb=77),
                           meters={"graph_storage": lambda _o: MeterReading(
                               used=200.0, measured_at=fresh, estimated=True,
                               low=100.0)})
    assert "UNMEASURED" in str(caught.value)


def test_a_low_above_the_measured_figure_is_discarded(monkeypatch):
    """A bound the instrument could not have produced must not ground a refusal."""
    _ledger_reading(monkeypatch, graph_storage_mb=10.0,
                    graph_storage_measured_at=datetime.now(UTC).isoformat(),
                    graph_storage_repeats=3, graph_storage_spread_mb=4.0,
                    graph_storage_min_mb=12.0)
    reading = _graph_storage_mb(ORG)
    assert reading is not None and reading.low is None


# ── 3c. the at-cap signal is THROTTLED (it is on the hot write path) ───────


def test_the_at_cap_warning_fires_ONCE_per_org_not_once_per_write(caplog):
    """#13102 P2: every gated write in the 1.0–1.5x band used to log a line."""
    reset_signal_state_for_tests()
    with caplog.at_level("WARNING"):
        for _ in range(25):
            enforce_allowances(_limits(max_graph_storage_mb=77),
                               meters={"graph_storage": _meter(100.0)})
    assert caplog.text.count(AT_CAP_WARNING) == 1, caplog.text
    # …and the throttle is per ORG: a second tenant still gets its own signal.
    with caplog.at_level("WARNING"):
        enforce_allowances(_limits(org_id="team-other", max_graph_storage_mb=77),
                           meters={"graph_storage": _meter(100.0)})
    assert caplog.text.count(AT_CAP_WARNING) == 2, caplog.text
    reset_signal_state_for_tests()


def test_an_at_cap_WARN_cannot_swallow_a_faults_INCIDENT(monkeypatch, signals):
    """The two signals are throttled SEPARATELY (#13102 VGATE F2).

    "Over the cap and still enforcing" and "not enforcing at all, our plumbing
    broke" are different facts. A shared throttle key let a WARN emitted a minute
    earlier suppress the durable incident for a later fault in the same hour —
    measured: the counter moved and NOTHING was filed.
    """
    _ledger_reading(monkeypatch, unmeasured_reason="absent")
    enforce_allowances(_limits(max_graph_storage_mb=77),
                       meters={"graph_storage": _meter(100.0)})  # at-cap WARN
    _ledger_reading(monkeypatch, unmeasured_reason="read_error")
    enforce_allowances(_limits(max_graph_storage_mb=77))
    assert [d.get("reason") for _k, _o, d in signals] == ["read_error"], signals


# ── 3d. THE P0 PROOF: a real org row, a real meter, no gauge ─────────────


def test_a_brand_new_PAID_orgs_first_write_succeeds_against_a_REAL_ledger(
        provision_test_user, signals):
    """The definition-of-done test: a real Team row, the real reader, no refusal.

    Nothing is stubbed on the store side: ``provision_test_user`` writes a real
    ``Team`` row at tier ``pro`` (in the live lane's own per-test namespace), the
    allowance is resolved from the REAL artifact through ``_arm_graph_storage``
    for the tier that row actually stores, and the usage figure is read by the
    REAL ``metering.get_graph_storage_reading`` — which, for this org's window,
    has no row at all (the gauge's only writer is the backup sweep).

    RED pre-fix: ``QuotaCheckError("graph_storage usage has no reading … rejecting
    to fail open")``, i.e. HTTP 500 on the onboarding demo's first point.
    """
    from tortoise import metering

    org = provision_test_user(tier="pro")
    org_id = org["org_id"]
    rows = org["sdk"]._get_registry().query(
        "MATCH (t:Team {id:$id}) RETURN t.tier", params={"id": org_id}
    ).result_set
    tier = rows[0][0]
    assert tier == "pro"

    # the REAL reader, over the REAL store: this org has no gauge yet
    reading = metering.get_graph_storage_reading(org_id)
    assert reading["measured"] is False, reading
    assert reading["graph_storage_measured_at"] is None, reading
    assert reading["unmeasured_reason"] in {"absent", "period_error",
                                            "read_error"}, reading

    limits = _arm_graph_storage({"org_id": org_id, "tier": tier}, tier)
    assert limits["max_graph_storage_mb"] == 102  # the artifact's pro window
    limits["org_id"] = org_id

    # THE ASSERTION THIS TEST EXISTS FOR: the first write is SERVED.
    enforce_allowances(limits, resource="points")
    counters = unarmed_served_counts()
    assert counters.get(("graph_storage", "absent"), 0) + counters.get(
        ("graph_storage", "period_error"), 0) + counters.get(
        ("graph_storage", "read_error"), 0) == 1, counters


def test_the_REST_door_serves_where_it_used_to_answer_500(monkeypatch, signals):
    """The seam the review measured: ``_check_org_limit`` → HTTP 500 → 200.

    No DB needed for this half — the DB-backed half is the test above. This pins
    that a configured allowance whose meter has NO reading no longer raises
    ``HTTPException(500)`` at the ONE REST convergence point.
    """
    from tortoise import quota
    from tortoise.hosted_api import _check_org_limit

    _ledger_reading(monkeypatch, unmeasured_reason="read_error")
    monkeypatch.setattr(
        quota, "resolve_org_limits",
        lambda org_id: {"org_id": org_id, "tier": "pro",
                        "max_graph_storage_mb": 102})
    _check_org_limit({"org_id": ORG, "tier": "pro", "max_points": None,
                      "max_api_keys": None, "max_sessions": None,
                      "max_users": None, "max_graphs": None},
                     "points")  # must not raise


# ── 4. the contracts the cap must not break ───────────────────────────────


def test_the_cap_deletes_nothing():
    """#9025/B7: this bounds residency; it never removes stored history.

    Two halves — the gate CALLS no delete/eviction function (checked on the
    parsed AST, so prose that merely names one does not satisfy the test), and a
    refusal writes nothing to the meter (a refused write must not look like a
    measurement, and must not touch the graph at all).
    """
    import ast

    from tortoise import allowance_gate, graph_storage, metering

    tree = ast.parse(inspect.getsource(allowance_gate))
    forbidden = {"_delete", "evict", "drop_graph", "_delete_nodes",
                 "execute_command"}
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute)
        else getattr(node.func, "id", "")
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    }
    assert not (called & forbidden), called & forbidden

    def _must_not_be_called(*_a, **_k):
        raise AssertionError("a cap refusal must not write/mutate the store")

    orig_rec, orig_gs = (metering.record_graph_storage_reading,
                         graph_storage.record_graph_storage)
    metering.record_graph_storage_reading = _must_not_be_called
    graph_storage.record_graph_storage = _must_not_be_called
    try:
        with pytest.raises(QuotaExceededError):
            enforce_allowances(_limits(max_graph_storage_mb=77),
                               meters={"graph_storage": _meter(500.0)})
    finally:
        metering.record_graph_storage_reading = orig_rec
        graph_storage.record_graph_storage = orig_gs


def test_the_cap_is_per_account_in_MB_with_no_node_or_graph_count():
    """#11758's declared shape: ONE account-scoped MB allowance, no per-graph rule.

    The live pin is the ARTIFACT's declaration (#13102 F6). The deleted
    assertion here was ``"graph" not in entry.unit.lower()``, which could not
    fail while ``entry.unit == "MB"`` stood two lines above — any unit
    containing "graph" reddens the equality first — so it read as coverage of
    "no per-graph rule" without being able to detect one. What actually
    falsifies a per-graph rule is the artifact: a dimension whose ``scope`` is
    not ``account``, or a ``*_per_graph`` key, would appear here.
    """
    entry = {d.dimension: d for d in DIMENSIONS}["graph_storage"]
    declared = pricing.storage_dimensions()
    assert entry.unit == "MB"
    assert "node" not in entry.limit_key.lower()
    # one dimension, one key, one account: the allowance is a scalar, not a map
    assert isinstance(entry.limit_key, str)
    # the key the gate enforces IS the artifact's declared key...
    assert entry.limit_key == declared["graph_storage"]["allowance_key"]
    # ...the artifact declares the allowance ACCOUNT-scoped (no per-graph unit)
    assert declared["graph_storage"]["scope"] == "account"
    assert all(d["scope"] == "account" for d in declared.values())
    # ...and no declared dimension counts nodes or graphs
    assert not any("node" in k.lower() or "graph_count" in k.lower()
                   for k in declared)
