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

import os

import pytest
from fastapi import HTTPException

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

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


def test_org_wide_key_still_counts_the_default_graph():
    """Unchanged: an org-wide key (no `graph_id`) counts `org_<tid>`."""
    org_wide = {
        k: v for k, v in GRAPH_BOUND_ORG.items() if k not in ("graph_id", "graph_namespace")
    }
    assert _data_graph_name(org_wide) == DEFAULT_GRAPH
    _seed(DEFAULT_GRAPH, 5)
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
    is not None else namespace`, and that dict has no eviction path — so
    resolving the org-wide lane via `graph_name=` would park a SECOND anchor per
    org (`org_<tid>` beside the write's `<tid>`).
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
