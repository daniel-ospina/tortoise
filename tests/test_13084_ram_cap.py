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
    MeterReading,
    _graph_storage_mb,
    enforce_allowances,
)
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
    assert "LOWER BOUND" in message and "under-reports" in message
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
    ("tortoise/hosted_api.py", "create_object", "points"),
    ("tortoise/hosted_api.py", "create_subject", "points"),
    ("tortoise/hosted_api.py", "create_point", "points"),
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


# ── 3. the meter's honesty: a stale/absent reading is NOT zero ────────────
#
# ⛔ BUT "absent" IS TWO THINGS, and #13102 measured the cost of one of them:
# the reader's zero view carried ``measured_at is None`` for (a) a successful
# read that found NO row — nobody has measured this tenant yet — and (b) a store
# that refused the read / an unageable row. Treating (a) as a refusal refused
# every write of a brand-new paying tenant (a 500 on ``POST /v1/points``), because
# the gauge's only writer is the DEFAULT-OFF backup sweep. The reader now names
# WHY (``unmeasured_reason``, the vocabulary ``get_capture_usage`` already
# publishes) and the gate is DEFAULT-DENY on it: only ``"absent"`` is a fact.


def test_a_tenant_with_NO_ledger_ROW_is_served_not_refused(monkeypatch):
    """The #13102 regression: a fresh paying tenant must be able to write.

    Pre-fix this raises ``QuotaCheckError`` ("has no reading … refusing to fail
    open"), which the REST/MCP seams surface as a 500 on the tenant's very first
    point — the deadlock measured in ``hosted-e2e`` and ``test-slow (b)``: the
    row is written by a scheduled sweep, so refusing the write can never make it
    appear. #3981's ruling forbids refusing a paying org over our own bookkeeping
    state.
    """
    from tortoise import metering

    monkeypatch.setattr(
        metering, "get_graph_storage_reading",
        lambda _org: {"graph_storage_mb": 0.0,
                      "graph_storage_measured_at": None,
                      "measured": False,
                      "unmeasured_reason": "absent"})
    enforce_allowances(_limits(max_graph_storage_mb=102))  # the pro cap
    # …and the cost of that is stated where the arming is declared, not hidden:
    # the cap does not bind until the gauge first reports (under-enforcement).
    dim = {d.dimension: d for d in DIMENSIONS}["graph_storage"]
    assert dim.absent_is_zero, (
        "graph_storage must declare that an absent row is consumed as a fact; "
        "without it the gate refuses a brand-new paid tenant")


@pytest.mark.parametrize("reason", ["unreported", "read_error", "period_error",
                                    "a_reason_this_gate_has_never_seen"])
def test_every_OTHER_unmeasured_reason_STILL_refuses(monkeypatch, reason):
    """The fix must not widen into a fail-open.

    A row that exists but cannot be aged, a store that refused the read and an
    unresolvable window are FAULTS — and an UNKNOWN reason is default-DENY, so a
    future reason cannot silently disarm a configured allowance. Pre-fix these
    already refused; this pins that the absent-row fix did not take them with it.
    """
    from tortoise import metering

    monkeypatch.setattr(
        metering, "get_graph_storage_reading",
        lambda _org: {"graph_storage_mb": 0.0,
                      "graph_storage_measured_at": None,
                      "measured": False,
                      "unmeasured_reason": reason})
    assert _graph_storage_mb(ORG) is None
    with pytest.raises(QuotaCheckError):
        enforce_allowances(_limits(max_graph_storage_mb=102))



def test_a_missing_measured_at_is_no_reading_not_zero(monkeypatch):
    """A reading with no timestamp and NO named reason is still refused.

    The zero view and a degraded read both carry no timestamp, so the reason is
    what decides: an omission (a stub, an older seam, a reason this gate never
    saw) must not be read as ``absent``, or every fault would disarm the cap.
    """
    from tortoise import metering

    monkeypatch.setattr(
        metering, "get_graph_storage_reading",
        lambda _org: {"graph_storage_mb": 0.0,
                      "graph_storage_measured_at": None})
    assert _graph_storage_mb(ORG) is None
    with pytest.raises(QuotaCheckError):
        enforce_allowances(_limits(max_graph_storage_mb=77))


def test_a_stale_reading_is_refused_rather_than_enforced(monkeypatch):
    from tortoise import metering

    stale = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    monkeypatch.setattr(
        metering, "get_graph_storage_reading",
        lambda _org: {"graph_storage_mb": 10.0,
                      "graph_storage_measured_at": stale})
    with pytest.raises(QuotaCheckError) as caught:
        enforce_allowances(_limits(max_graph_storage_mb=77))
    assert "older than" in str(caught.value)


def test_a_fresh_reading_under_the_cap_is_served(monkeypatch):
    from tortoise import metering

    monkeypatch.setattr(
        metering, "get_graph_storage_reading",
        lambda _org: {"graph_storage_mb": 12.5,
                      "graph_storage_measured_at":
                          datetime.now(UTC).isoformat()})
    enforce_allowances(_limits(max_graph_storage_mb=77))


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
    forbidden = {"delete", "evict", "drop_graph", "_delete_nodes",
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
