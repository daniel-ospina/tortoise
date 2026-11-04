"""#4051 — the org-limit gate must count the graph it GUARDS.

`_data_graph_name` is documented as "the single value the ADMISSION GATE and the
opener share" (`hosted_api.py`). For a **graph-bound key** (`org["graph_id"]`
set) the opener (`_data_sdk`) passes it through as `graph_name=` and writes into
that FULL graph — a custom `org_<tid>_<gid>`. But `_check_org_limit` called
`enforce_org_limit(org, resource)` with **no sdk**, so `_count_resource` fell
back to its own `_make_sdk(namespace=org_id)` → the org's **DEFAULT** graph.

Net effect on the lane #4051 is about: a graph-bound key could write without
bound into its own graph, because the `points` gate counted a different graph —
present, and vacuous for exactly the credential class it is meant to bound.

Every graph name here is NONCE'd into the `test_…_tortoise` family so both test
lanes sweep it (`wipe_server` detach-deletes only `test_`/`tortoise_test_*`, and
the `org_*` stray sweep never runs on a shared docker) — a product-namespace
`org_<tid>` graph would survive every CI run and grow the shared instance's
graph count, the cache-eviction class that aborts in-flight fleet writes.
"""

from __future__ import annotations

import ast
import inspect
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from fastapi import HTTPException

from tortoise import quota as quota_mod

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import tortoise.hosted_api as ha  # env above must be set before this import
from tortoise.hosted_api import (
    _check_org_limit,
    _data_graph_name,
    _gate_sdk_for,
    _make_sdk,
)

# `_graph_name_for_namespace` maps a `test_` namespace to `{ns}_tortoise`, which
# is in the swept family on BOTH lanes.
_NS = f"test_4051_{os.urandom(3).hex()}"
TEST_ORG_ID = _NS
CUSTOM_GRAPH = f"{_NS}_custom_tortoise"
DEFAULT_GRAPH = f"{_NS}_tortoise"

# A graph-bound credential: `graph_id` set + the resolved FULL graph name, which
# is exactly the pair `_data_sdk` opens the custom graph from.
GRAPH_BOUND_ORG = {
    "org_id": TEST_ORG_ID,
    "key_id": "test-key-bound",
    "legacy_full_access": True,
    "tier": "pro",
    "max_users": 5,
    "max_graphs": 5,
    "max_points": 5,
    "max_api_keys": 5,
    "max_sessions": None,
    "graph_id": "g4051",
    "graph_namespace": CUSTOM_GRAPH,
}


def _seed(graph_name: str, n: int) -> None:
    """Exactly `n` non-episodic Points in `graph_name`."""
    g = _make_sdk(graph_name=graph_name)._get_proj().g
    g.query("MATCH (p:Point) WHERE p.id STARTS WITH 'pt_4051_gg' DETACH DELETE p")
    for i in range(n):
        g.query(
            "MERGE (p:Point {id:$pid}) SET p.content='seed', p.is_episodic=false",
            params={"pid": f"pt_4051_gg_{i}"},
        )


def _count(graph_name: str) -> int:
    return int(
        _make_sdk(graph_name=graph_name)
        ._get_proj()
        .g.query("MATCH (p:Point) WHERE p.is_episodic = false RETURN count(p)")
        .result_set[0][0]
    )


def test_data_graph_name_is_the_custom_graph_for_a_bound_key():
    """Precondition: the opener's graph name is the custom graph, not the default."""
    assert _data_graph_name(GRAPH_BOUND_ORG) == CUSTOM_GRAPH


@pytest.mark.parametrize("resource", ["points", "sessions", "documents"])
def test_gate_sdk_is_the_custom_graph_for_every_graph_scoped_resource(resource):
    """Every graph-scoped resource resolves to the graph the write will open.

    Dropping any one of them from `_GRAPH_SCOPED_QUOTA_RESOURCES` makes this
    return `None` (= count the DEFAULT graph), so the set cannot silently shrink
    back to `points`-only and leave the same hole on the other two lanes.
    """
    sdk = _gate_sdk_for(dict(GRAPH_BOUND_ORG), resource)
    assert sdk is not None, f"{resource} lost its graph-scoped count"
    assert sdk._get_proj().g.name == CUSTOM_GRAPH, (
        f"{resource} gate counts {sdk._get_proj().g.name!r}, not the graph the "
        f"write opens ({CUSTOM_GRAPH!r})"
    )


def test_gate_counts_the_custom_graph_a_bound_key_writes_into():
    """END-TO-END: the gate must see the CUSTOM graph's points.

    Pre-fix the count reads the DEFAULT graph, so a graph already AT its
    `max_points` still passed — a gate that cannot see what it guards.
    """
    _seed(CUSTOM_GRAPH, 5)
    _seed(DEFAULT_GRAPH, 0)
    assert _count(CUSTOM_GRAPH) == 5, "fixture: custom graph must hold 5 points"
    assert _count(DEFAULT_GRAPH) == 0, "fixture: default graph must be empty"

    with pytest.raises(HTTPException) as ei:
        _check_org_limit(dict(GRAPH_BOUND_ORG), "points")
    assert ei.value.status_code == 402, ei.value


def test_gate_still_passes_a_bound_key_under_the_limit():
    """No over-refusal: 4 of 5 points in the custom graph still commits."""
    _seed(CUSTOM_GRAPH, 4)
    _seed(DEFAULT_GRAPH, 0)
    _check_org_limit(dict(GRAPH_BOUND_ORG), "points")  # must not raise


def test_a_FULL_default_graph_cannot_refuse_a_write_into_an_EMPTY_bound_graph():
    """#8906 — the false-refusal direction, which no case ABOVE covers.

    #8906's Test section asks for TWO directions. The cap-hole one IS covered
    above: `test_gate_counts_the_custom_graph_a_bound_key_writes_into` seeds the
    BOUND graph at `max_points` and requires 402, so a gate reading the other
    graph admits and reds there. The false-refusal one is not covered **in this
    file** — it IS covered repo-wide, at DOOR level, by
    `tests/test_8906_points_gate_counts_write_graph.py`'s
    `test_default_graph_full_write_graph_empty_is_ADMITTED` (#10724, `f422799c4`
    — an ANCESTOR of this fold's merge base, so "after" means after the lane's
    own older fork, not after `main`): that file pins the doors, this arm pins the
    omitted-`sdk=` fallback inside `_check_org_limit`. Every case above that
    seeds a graph sets the DEFAULT graph to 0, so none of them can say what a
    FULL counted graph does to a write into an EMPTY bound graph — and that is
    the direction that froze the fleet (the counted graph sat at `max_points`,
    so every REST write behind it was refused, while the bound graph kept
    absorbing ~200 counted Points in 48 minutes; the refusal's `message` began
    `Team points limit reached`).

    DISCRIMINATION: SAME credential, SAME limit, and only the two graphs'
    populations swapped — so only a gate that counts the graph the request
    WRITES yields both verdicts. A gate counting the OTHER graph inverts both
    phases: it raises 402 where admission is required (phase 1: default 5,
    bound 0) and admits where 402 is required (phase 2: default 0, bound 5).

    That is also the one mutation this arm uniquely reds: a gate counting the
    org AGGREGATE (the sum, or the max, of both graphs) passes the cap-hole arm
    above — sum 5 → 402 — and inverts only phase 1, so the aggregate reading of
    the entitlement (the default **that** #8906's recorded decision OVERRIDES) is
    red here and nowhere else in this file.

    RED pre-fix, measured against the pre-#10637 `_check_org_limit` body as
    `hosted_api.py` actually carried it, restored with a throwaway pytest
    plugin:

        enforce_org_limit(org, resource, slot_credit=slot_credit,
                          sdk=(_org_sdk_for_credential(org)
                               if resource in _TENANT_GRAPH_RESOURCES else None))

    (`_TENANT_GRAPH_RESOURCES` is #10180's literal, which #10637 REPLACED with
    `_GRAPH_SCOPED_QUOTA_RESOURCES = TENANT_GRAPH_RESOURCES` — the BARE name,
    imported from `tortoise.quota` — grep `TENANT_GRAPH_RESOURCES`, not a line
    number; same members. A restore must define the old name or use the new
    one; the run reported here substituted the bare name.)

    Note it PASSED an sdk — `_make_sdk(namespace=org_id)` for a graph-bound
    credential — so this class is NOT found by grepping for calls that omit the
    argument. (The module docstring above describes the ORIGINAL #4051 shape,
    which did omit it; #10452 later passed a default-graph handle, and THAT is
    the revision measured here. Same wrong graph, two mechanisms.)

    Measured: this arm failed with `HTTPException: 402: Team points limit
    reached (5)` — `_check_org_limit` re-raises `QuotaExceededError` as that
    HTTPException, which is what this arm therefore observes — and the
    cap-hole arm above reported `Failed: DID NOT RAISE HTTPException`.

    GREEN after #10637, which counts through the same `_data_graph_name(org)`
    the opener writes.

    The five REST points doors are not enumerated here, and at the merge result
    they no longer share this resolution: each passes its OWN write handle
    (`sdk=`, #10724), so what this arm pins is the `sdk=None` fallback
    `_check_org_limit` resolves through `sdk_factory` → `_gate_sdk_for` — the
    arm a door that omits `sdk=` inherits. One contract
    (`quota.enforce_org_limit`'s `sdk:`/`sdk_factory:` docs), two entry forms;
    the doors' `sdk=` form is pinned at door level by
    `tests/test_8906_points_gate_counts_write_graph.py`.
    """
    # Direction 1 (#8906's false refusal): default FULL, write graph EMPTY.
    _seed(DEFAULT_GRAPH, 5)  # GRAPH_BOUND_ORG's max_points == 5
    _seed(CUSTOM_GRAPH, 0)
    assert _count(DEFAULT_GRAPH) == 5, "fixture: counted graph must be AT cap"
    assert _count(CUSTOM_GRAPH) == 0, "fixture: the write graph must be empty"
    _check_org_limit(dict(GRAPH_BOUND_ORG), "points")  # must NOT raise

    # Direction 2 (#8906's cap hole): the same numbers, swapped.
    _seed(DEFAULT_GRAPH, 0)
    _seed(CUSTOM_GRAPH, 5)
    assert _count(DEFAULT_GRAPH) == 0, "fixture: counted graph must be empty"
    assert _count(CUSTOM_GRAPH) == 5, "fixture: the write graph must be AT cap"
    with pytest.raises(HTTPException) as ei:
        _check_org_limit(dict(GRAPH_BOUND_ORG), "points")
    assert ei.value.status_code == 402, ei.value

    # ⛔ RESTORE THE FIXTURE BASELINE (#10807 review P3). This arm ends with the
    # CUSTOM graph parked at `max_points`; the next arm,
    # `test_org_wide_key_still_counts_the_default_graph`, seeds only the DEFAULT
    # graph, so at 5 it would still 402 under a gate that WRONGLY counted the
    # custom graph for an org-wide key (5 >= 5) — losing the only count-level
    # discrimination that case has. Its own arm is made self-contained below as
    # well; leaving the baseline clean means neither is order-dependent.
    _seed(CUSTOM_GRAPH, 0)
    assert _count(CUSTOM_GRAPH) == 0, "fixture: the custom graph is restored"


def test_org_wide_key_still_counts_the_default_graph():
    """Unchanged: an org-wide key (no `graph_id`) counts `org_<tid>`.

    ⛔ SELF-CONTAINED, NOT INHERITED (#10807 review P3): the CUSTOM graph is
    seeded EMPTY here, so the 402 below can only come from the DEFAULT graph's
    population. Without this the arm passed under the exact mutation it exists
    to catch — a gate counting the CUSTOM graph — whenever a preceding case left
    that graph at the cap.
    """
    org_wide = {
        k: v for k, v in GRAPH_BOUND_ORG.items() if k not in ("graph_id", "graph_namespace")
    }
    assert _data_graph_name(org_wide) == DEFAULT_GRAPH
    _seed(DEFAULT_GRAPH, 5)
    _seed(CUSTOM_GRAPH, 0)
    assert _count(DEFAULT_GRAPH) == 5, "fixture: counted graph must be AT cap"
    assert _count(CUSTOM_GRAPH) == 0, "fixture: the write graph must be empty"
    with pytest.raises(HTTPException) as ei:
        _check_org_limit(dict(org_wide), "points")
    assert ei.value.status_code == 402, ei.value


def test_unresolved_binding_gets_no_graph_sdk():
    """FAIL-CLOSED arm: a binding that resolves to no graph gets NO sdk.

    Substituting the default graph here would be a fail-OPEN widening; this is
    the branch the fix's docstring argues hardest about, so it is pinned rather
    than left to the reader. The request itself is 403'd by `_data_sdk` before
    any write, which is why `None` (count the default) is safe.
    """
    half_resolved = {**GRAPH_BOUND_ORG, "graph_namespace": None}
    assert _data_graph_name(half_resolved) is None
    assert _gate_sdk_for(half_resolved, "points") is None


@pytest.mark.parametrize("resource", ["api_keys", "users", "graphs"])
def test_registry_scoped_resources_build_no_data_sdk(resource):
    """Registry counts are control-plane reads — no data SDK on the key-mint path."""
    assert _gate_sdk_for(GRAPH_BOUND_ORG, resource) is None


def test_org_wide_gate_sdk_mirrors_the_openers_namespace_form(monkeypatch):
    """The gate must build the ORG-WIDE sdk the opener builds (`namespace=`).

    `_make_sdk` keys its embedded keepalive anchor on `graph_name if graph_name
    is not None else namespace`, and that dict never evicts a HEALTHY anchor (it
    is only replaced when `_anchor_usable` fails, #1502 — never to bound its
    size), so resolving the org-wide lane via `graph_name=` would park a SECOND
    anchor per org (`org_<tid>` beside the write's `<tid>`).
    """
    calls = []
    import tortoise.hosted_api as ha

    real = ha._make_sdk

    def spy(**kwargs):
        calls.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(ha, "_make_sdk", spy)
    org_wide = {
        k: v for k, v in GRAPH_BOUND_ORG.items() if k not in ("graph_id", "graph_namespace")
    }
    ha._gate_sdk_for(org_wide, "points")
    assert calls == [{"namespace": TEST_ORG_ID}], calls

    calls.clear()
    ha._gate_sdk_for(dict(GRAPH_BOUND_ORG), "points")
    assert calls == [{"graph_name": CUSTOM_GRAPH}], calls


def test_a_gate_that_never_counts_never_builds_a_sdk(monkeypatch):
    """LAZY: `sessions` is unlimited by #4010, so the gate returns uncounted.

    Measured pre-review as 0 -> 1 `_make_sdk` calls per capture (764 us embedded,
    on the event loop) because the SDK was resolved EAGERLY for a count that
    never ran. The factory must not be called at all on that path.
    """
    calls = []
    import tortoise.hosted_api as ha

    def boom(**kwargs):  # pragma: no cover - must never run
        calls.append(kwargs)
        raise AssertionError("built an SDK for a gate that never counts")

    monkeypatch.setattr(ha, "_make_sdk", boom)
    # `sessions` resolves to an explicit None = unlimited -> early return, no count.
    ha._check_org_limit(dict(GRAPH_BOUND_ORG), "sessions")
    # `documents` derives its cap from `max_points`; an explicit None returns too.
    ha._check_org_limit({**GRAPH_BOUND_ORG, "max_points": None}, "documents")
    assert calls == [], calls


def test_the_gate_set_is_DERIVED_from_quota_not_hand_copied():
    """DRIFT GUARD (round-3 P2): the split is declared ONCE, in `quota`.

    `quota._count_resource`'s tenant-graph branch is its catch-all `else`: every
    resource outside the registry-scoped three is counted against a tenant
    graph. So the truthful set is DERIVED, not enumerated — and this gate used
    to hold a hand-copied literal that equalled it only on the day it was
    written.

    ⛔ THIS TEST MUST NOT HAND-COPY THE UNIVERSE. Its first revision derived the
    expected set as `frozenset(_RESOURCE_LIMIT_KEYS) | {"documents"}` — a
    hand-written `{"documents"}`, which is the very drift it exists to catch,
    one level up. A second derived cap added to `enforce_org_limit` (measured on
    the round-3 review with a `vectors` cap derived from `max_points`) is fully
    gate-able, is in neither declared half, and passed all four of these tests
    while `_gate_sdk_for` returned `None` and the count fell to the DEFAULT
    graph. The universe now comes from `quota.GATEABLE_RESOURCES`, which
    `enforce_org_limit`'s allow-list guard also enforces, so the three cannot
    disagree: the guard, the derivation and this assertion read one object.
    """
    gateable = quota_mod.GATEABLE_RESOURCES
    expected = gateable - quota_mod.REGISTRY_SCOPED_RESOURCES

    # 1. the declaration IS the derivation (not a literal that happens to match)
    assert expected == quota_mod.TENANT_GRAPH_RESOURCES

    # 2. exhaustive and disjoint: every gate-able resource is classified ONCE,
    #    so a new resource cannot land in neither half (silently unscoped) or both.
    classified = quota_mod.TENANT_GRAPH_RESOURCES | quota_mod.REGISTRY_SCOPED_RESOURCES
    assert classified == gateable
    assert not (quota_mod.TENANT_GRAPH_RESOURCES & quota_mod.REGISTRY_SCOPED_RESOURCES)

    # 3. the derived caps are inside the universe the guard admits, and the
    #    registry half cannot name a resource the guard would then refuse (a
    #    registry resource that is not gate-able is a contradiction: its count
    #    would be refused as `unknown quota resource`).
    assert gateable >= quota_mod.DERIVED_CAP_RESOURCES
    assert gateable >= quota_mod.REGISTRY_SCOPED_RESOURCES

    # 4. the gate holds THAT object — identity, not a copy. A copy is the drift.
    assert ha._GRAPH_SCOPED_QUOTA_RESOURCES is quota_mod.TENANT_GRAPH_RESOURCES


def _resource_string_literals(node: ast.AST) -> set[str]:
    """The string literals in a comparison operand (`"x"`, `("x", "y")`, …)."""
    if isinstance(node, ast.Constant):
        return {node.value} if isinstance(node.value, str) else set()
    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        return {s for elt in node.elts for s in _resource_string_literals(elt)}
    return set()


def _resources_enforce_org_limit_SPECIAL_CASES() -> frozenset[str]:
    """Every resource `enforce_org_limit` names in a literal comparison.

    Derived from the function's own AST, never from a list kept here: a list
    would be the hand-copy this guards against. Comments are invisible to the
    AST, so the historical `if resource == "sessions"` sentence in the code's
    prose cannot be mistaken for a live branch.
    """
    src = textwrap.dedent(inspect.getsource(quota_mod.enforce_org_limit))
    found: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Compare):
            continue
        if not (isinstance(node.left, ast.Name) and node.left.id == "resource"):
            continue
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            # `==` is a derived-cap branch; `in` a hand-written alternative set.
            # `not in GATEABLE_RESOURCES` is the allow-list guard itself and
            # carries no literal, so it contributes nothing (by design).
            if isinstance(op, (ast.Eq, ast.In)):
                found |= _resource_string_literals(comparator)
    return frozenset(found)


def test_a_resource_enforce_org_limit_SPECIAL_CASES_is_DECLARED_and_admitted():
    """DRIFT GUARD (round-3 P2, second half): the BRANCHES read the declaration.

    The failure this pins, measured by the round-3 reviewer: add a
    `documents`-style derived cap for a NEW resource (they used `vectors`,
    derived from `max_points`) to `enforce_org_limit`. The function then gates
    it, but the resource is in neither `_RESOURCE_LIMIT_KEYS` nor
    `DERIVED_CAP_RESOURCES`, so it is in neither half of the classification:
    `_gate_sdk_for` returns `None`, `_count_resource` builds
    `_make_sdk(namespace=org_id)` — the org's DEFAULT graph while a graph-bound
    write opens its own — and BOTH gate-set tests passed. That is #4051's
    defect, re-armed, with nothing red.

    Now the branch itself reds this file (assertion 1) and the allow-list guard
    in `enforce_org_limit` refuses the resource in production (assertion 3), so
    the omission is loud at review time AND fail-closed at runtime.
    """
    branches = _resources_enforce_org_limit_SPECIAL_CASES()

    # 1. every resource the function special-cases must be DECLARED. A new
    #    derived branch whose resource is absent here is the reviewer's
    #    mutation, and it cannot be silently unclassified any more.
    assert branches <= quota_mod.GATEABLE_RESOURCES, (
        f"enforce_org_limit gates {sorted(branches - quota_mod.GATEABLE_RESOURCES)} "
        f"without declaring it — that resource is unclassified, so the gate "
        f"would count the org's DEFAULT graph for it (#4051)"
    )

    # 2. a DECLARED derived cap must actually be handled, or the declaration is
    #    a promise nothing keeps (it would be admitted by the guard and then hit
    #    the `limit_key is None` arm as `unknown quota resource`).
    assert branches >= quota_mod.DERIVED_CAP_RESOURCES

    # 3. and the runtime half of the same invariant: an undeclared resource is
    #    REFUSED, classified, before any branch can accept it.
    for undeclared in ("vectors", "entities", "webhooks"):
        with pytest.raises(quota_mod.QuotaCheckError) as ei:
            quota_mod.enforce_org_limit(
                {"org_id": "test_4051_undeclared", "max_points": 10}, undeclared)
        assert "unknown quota resource" in str(ei.value), ei.value


def _counting_sdks(monkeypatch) -> list[dict]:
    """Replace `quota._make_sdk` with a fake that RECORDS how it was built.

    Both `_count_resource` branches construct through `_make_sdk` — the tenant
    branch with `namespace=org_id`, the registry branch with
    `namespace="registry"` — so the recorded kwargs are what identify WHICH
    branch ran, independently of what it returned.
    """
    built: list[dict] = []

    class _Result:
        def __init__(self):
            self.result_set = [[0]]

    class _Graph:
        def query(self, *a, **k):
            return _Result()

    class _Proj:
        def __init__(self):
            self.g = _Graph()

    class _Registry:
        def query(self, *a, **k):
            return _Result()

    class _FakeSDK:
        def __init__(self, **kwargs):
            built.append(kwargs)

        def _get_proj(self):
            return _Proj()

        def _get_registry(self):
            return _Registry()

    monkeypatch.setattr(quota_mod, "_make_sdk", lambda **kw: _FakeSDK(**kw))
    return built


@pytest.mark.parametrize("resource", sorted(quota_mod.TENANT_GRAPH_RESOURCES))
def test_every_declared_graph_resource_is_counted_through_a_graph_sdk(
        monkeypatch, resource):
    """`_count_resource` counts every TENANT-declared resource in a tenant graph.

    For every resource the declaration calls graph-scoped, the count must build
    the tenant-graph SDK and take the graph branch. A fake SDK is enough: the
    claim under test is WHICH branch ran, not what it returned.

    ⛔ HONEST SCOPE (corrected — the first revision overclaimed). The docstring
    here used to say that reverting the branch to its old hardcoded tuple
    `("api_keys", "users", "graphs")` would "take the control-plane branch
    instead and no graph SDK would be built — red here". **It would not red, and
    it cannot**: that tuple is value-identical to `REGISTRY_SCOPED_RESOURCES`,
    and no member of `TENANT_GRAPH_RESOURCES` is inside it, so the reversion is
    behaviourally invisible for every case below (measured: 18/18 pass). That is
    not a hole in the branch — it is the point of the declaration: a literal
    that EQUALS the truth cannot be distinguished from it, and the constant is
    what lets the two be edited as one thing.

    The direction that IS falsifiable, and went uncovered until now, is a branch
    that DIVERGES — a tuple that disagrees with the declaration. Its twin below
    covers it from the registry side; this half covers the tenant side.
    """
    built = _counting_sdks(monkeypatch)
    assert quota_mod._count_resource("org_test_4051", resource) == 0
    assert built == [{"namespace": "org_test_4051"}], (
        f"{resource} is declared graph-scoped but was not counted in a tenant "
        f"graph — `_count_resource` branched away from the declaration"
    )


@pytest.mark.parametrize("resource", sorted(quota_mod.REGISTRY_SCOPED_RESOURCES))
def test_every_declared_REGISTRY_resource_is_NOT_counted_through_a_graph_sdk(
        monkeypatch, resource):
    """The OTHER half: a registry-declared resource never builds a TENANT SDK.

    THIS is the direction that makes "the branch and the declaration cannot
    disagree" a measured fact instead of an assertion. Diverging the branch's
    tuple — e.g. `if resource in ("api_keys", "users"):` — drops `graphs` into
    the tenant catch-all, so a control-plane count would be taken against a
    tenant graph. MEASURED on the pre-fix file: that mutation left
    **18/18 passing**; with this case it reds on `graphs`, which is the case
    whose absence made the claimed mutation inert.

    `{"namespace": "registry"}` is the control-plane handle both
    `_count_resource` and the gate build for these, so it is explicitly allowed;
    what must never appear is the TENANT handle.
    """
    built = _counting_sdks(monkeypatch)
    assert quota_mod._count_resource("org_test_4051", resource) == 0
    assert {"namespace": "org_test_4051"} not in built, (
        f"{resource} is declared REGISTRY-scoped but was counted in a tenant "
        f"graph — `_count_resource` branched away from the declaration: {built}"
    )


def test_a_factory_failure_is_fail_closed_and_attributable(monkeypatch, caplog):
    """A gate-SDK construction fault stays a CLASSIFIED refusal (#4051).

    `_count_resource` used to own this construction, so every failure became a
    `QuotaCheckError` → 500 with an ERROR log carrying team + resource.
    `_resolve_count_sdk` must keep that contract: without it the fault escapes
    `_check_org_limit`'s `except QuotaCheckError` as an unclassified 500 with no
    fail-closed line, which is the defect the lazy factory introduced.
    """
    import logging

    import tortoise.hosted_api as ha

    def boom(**kwargs):
        raise RuntimeError("anchor unavailable")

    monkeypatch.setattr(ha, "_make_sdk", boom)
    with (
        caplog.at_level(logging.ERROR, logger="tortoise.quota"),
        pytest.raises(HTTPException) as ei,
    ):
        ha._check_org_limit(dict(GRAPH_BOUND_ORG), "points")

    assert ei.value.status_code == 500, ei.value
    assert str(ei.value.detail).startswith("Quota check failed: quota count sdk unavailable"), (
        ei.value.detail
    )

    lines = [
        r.getMessage()
        for r in caplog.records
        if r.name == "tortoise.quota" and r.levelno == logging.ERROR
    ]
    assert any("sdk unavailable" in m for m in lines), lines
    # attributable: the #686 promise is team= + resource= on every fail-closed line
    assert any(f"team={TEST_ORG_ID}" in m and "resource=points" in m for m in lines), lines


def _ruff_binary() -> str | None:
    """The pinned `ruff` the REQUIRED lint leg runs, or `None` if it is absent.

    `ruff` is a dev dependency (`pyproject.toml`, `ruff==0.16.4`, the same pin
    the leg installs), so the `.venv` a lane tests from carries it; an
    environment stripped of the dev group cannot run the check, and the case
    below SKIPS there rather than asserting a claim it did not measure.
    """
    root = Path(__file__).resolve().parent.parent
    pinned = root / ".venv" / "bin" / "ruff"
    if pinned.exists():
        return str(pinned)
    return shutil.which("ruff")


def test_this_file_is_clean_under_the_REQUIRED_lint_leg():
    """A lint error HERE reds a REQUIRED check with every case in this file green.

    MEASURED, not pre-emptive: `agent-infra-ci / lint` runs `ruff check .` over
    the whole tree (`.github/workflows/ci.yml` — "ruff check . + mypy
    tortoise/") and was **RED at `b0b77730a617`** with exactly three SIM300
    findings in this file — `assert quota_mod.DERIVED_CAP_RESOURCES <= gateable`
    and its two siblings, Yoda comparisons ruff's `SIM` select flags. Nobody saw
    it: both round-4 reviewers listed `ruff` under UNVERIFIED (they had no
    `.venv`), and no case in a file of green assertions can observe its own
    lint. The check that actually blocked the merge was the one nothing covered,
    which is the same shape `tests/test_ci_new_failure_gate.py`'s own
    lint case exists for (#10564).

    The config is the tree's own: `ruff` resolves the nearest `pyproject.toml`
    for each analyzed path, so an explicit path is judged exactly as
    `ruff check .` judges it. Reds on the pre-fix file (rc=1, three SIM300);
    inert only where `ruff` is absent, and a SKIP there — never a false green.
    """
    binary = _ruff_binary()
    if binary is None:
        pytest.skip("ruff is not installed in this environment")
    this_file = Path(__file__).resolve()
    proc = subprocess.run(
        [binary, "check", "--no-cache", str(this_file)],
        cwd=this_file.parent.parent,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"{this_file.name} must be ruff-clean — the `agent-infra-ci / lint` leg "
        f"runs `ruff check .` over this path and is REQUIRED; got "
        f"rc={proc.returncode}:\n{proc.stdout}{proc.stderr}"
    )
