#!/usr/bin/env python3
"""#7108 — the operator-scope predicate on the Point read paths.

Two independent defects lived in the SAME two lines of ``query()`` and
``paginated_query()``, and both are pinned here.

(1) THE BARE FORM LIES. The clause was a bare ``n.is_operator = false``, which
    #2205 documented as making per-kind stats lie on imported graphs (a Point may
    carry no ``is_operator`` property at all, so ``= false`` drops it; a legacy
    operator carries ``op_type`` without it, so it leaks into non-operator
    results), and which #3154 measured as matching ZERO rows on graphs copied
    with a boolean index. ``summarize_structure``/``list_pointkinds`` were moved to
    the canonical predicate years ago; the MAIN READ PATH never was.

(2) THE EXCLUSION CANNOT BE UNCONDITIONAL. Every node carrying
    ``pointKind = 'operator'`` also has ``is_operator = true``, so pairing a
    non-operator predicate with that kind filter is an EMPTY INTERSECTION by
    construction — a WRONG-EMPTY, indistinguishable from a true-empty to the
    caller, which is exactly what read-path invariant #6146 forbids.

NOTE ON SCOPE, because it is easy to over-claim here. ``sdk.py`` declares the kind
field per entity type, and for operators it is ``op_type``, NOT ``pointKind``
(``{"...": ..., "operator": "op_type"}``). So these tests do NOT assert that
``query(kind="operator")`` enumerates the operator population — that would invent a
kind<->``op_type`` equivalence the codebase deliberately does not have, and would be
a surface/data-model decision. What is pinned is the narrower, undecided thing:
a request that names a kind must not be answered with a guaranteed-empty result.

MUST run against a live FalkorDB (Docker). Uses an isolated ``tortoise_test_*``
graph so the graph guard permits bulk cleanup.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import _live_utils
from tortoise.sdk import TortoiseSDK

_DB_URI = _live_utils.docker_uri(f"tortoise_test_7108_{uuid.uuid4().hex[:6]}")

FALKORDB_AVAILABLE = False
_OLD_URI = os.environ.get("TORTOISE_DB_URI")
try:
    os.environ["TORTOISE_DB_URI"] = _DB_URI
    _probe = TortoiseSDK()
    _probe._get_proj().g.query("RETURN 1")
    _probe.close()
    FALKORDB_AVAILABLE = True
except Exception:
    pass
finally:
    if _OLD_URI is not None:
        os.environ["TORTOISE_DB_URI"] = _OLD_URI
    else:
        os.environ.pop("TORTOISE_DB_URI", None)

pytestmark = pytest.mark.skipif(
    not FALKORDB_AVAILABLE, reason="Live FalkorDB (Docker) not available")


@pytest.fixture()
def sdk():
    """The module's isolated graph.

    The URI is scoped per-test (#176 contamination). Cleanup is deliberately
    TARGETED rather than a bulk ``DETACH DELETE``: the projection's graph guard
    requires a graph name starting with ``test_``/``tortoise_test``, and the
    SDK's namespace prefix means the resulting graph name does not, so a bulk
    delete is refused. Every assertion here is relative, so residue in this
    throwaway graph is harmless.
    """
    _old = os.environ.get("TORTOISE_DB_URI")
    os.environ["TORTOISE_DB_URI"] = _DB_URI
    s = TortoiseSDK()
    try:
        yield s
    finally:
        if _old is not None:
            os.environ["TORTOISE_DB_URI"] = _old
        else:
            os.environ.pop("TORTOISE_DB_URI", None)


def _mk_point(sdk, kind: str, content: str) -> str:
    p = sdk.create_point(kind, content)
    return p["id"] if isinstance(p, dict) else p


def _raw(sdk, cypher: str):
    return sdk._get_proj().g.query(cypher).result_set


# ═══════════════════════════════════════════════════════════════════
# (1) the bare form must not drop a Point that carries no is_operator
# ═══════════════════════════════════════════════════════════════════

def test_a_point_without_the_is_operator_property_is_not_dropped(sdk):
    """The bare `= false` drops it; the canonical predicate keeps it (#2205).

    ``create_point`` stamps ``is_operator = false`` explicitly, so the NULL arm is
    only reachable by the shape #2205 measured: a node that PREDATES the property
    (an imported/legacy graph). That shape is built directly, because it is the one
    the bare form silently loses.
    """
    legacy = f"legacy-{uuid.uuid4().hex[:8]}"
    sdk._get_proj().g.query(
        "CREATE (n:Point {id:$id, pointKind:'evidence', content:'imported before "
        "is_operator existed', status:'live'})",
        params={"id": legacy},
    )
    # Prove the premise: this Point really does lack the property.
    assert _raw(sdk, f"MATCH (n:Point {{id:'{legacy}'}}) RETURN n.is_operator")[0][0] is None, (
        "precondition: the node must have NO is_operator property"
    )
    # ...and that a bare `= false` would therefore lose it (the old behaviour).
    assert _raw(
        sdk,
        f"MATCH (n:Point {{id:'{legacy}'}}) WHERE n.is_operator = false RETURN count(n)",
    )[0][0] == 0, "precondition: the bare form must drop this node"

    returned = {r.get("id") for r in sdk.query(kind="evidence")}
    assert legacy in returned, (
        "a Point with no is_operator property was dropped by the read path — the bare "
        "`n.is_operator = false` form is back (#2205)"
    )


# ═══════════════════════════════════════════════════════════════════
# (2) an explicit operator-kind request must not be a guaranteed empty
# ═══════════════════════════════════════════════════════════════════

def test_an_explicit_operator_kind_is_not_answered_with_a_guaranteed_empty(sdk):
    """The #6146 wrong-empty: the exclusion must yield to an explicit request."""
    # A node that carries the literal operator kind — the shape the exclusion
    # empties by construction (every such node also has is_operator = true).
    sdk._get_proj().g.query(
        "CREATE (n:Point {id:$id, pointKind:'operator', is_operator:true, "
        "op_type:'IMPL', content:'operator-kind probe', status:'live'})",
        params={"id": f"probe-{uuid.uuid4().hex[:8]}"},
    )
    probe = _raw(sdk, "MATCH (n:Point {pointKind:'operator'}) RETURN n.id")[0][0]

    # Precondition: the two conjuncts really are mutually exclusive for this row,
    # which is what makes the old clause a guaranteed-empty.
    assert _raw(
        sdk, f"MATCH (n:Point {{id:'{probe}'}}) RETURN n.is_operator = false")[0][0] is False

    returned = {r.get("id") for r in sdk.query(kind="operator")}
    assert probe in returned, (
        "query(kind='operator') returned a wrong-empty: an unconditional "
        "is_operator=false clause makes the kind filter unreachable (#7108)"
    )


def test_paginated_query_agrees_with_query_on_an_operator_kind(sdk):
    """The two read paths must not disagree — they share the clause builder."""
    for i in range(3):
        sdk._get_proj().g.query(
            "CREATE (n:Point {id:$id, pointKind:'operator', is_operator:true, "
            "op_type:'IMPL', content:$c, status:'live'})",
            params={"id": f"probe-{i}-{uuid.uuid4().hex[:8]}", "c": f"op {i}"},
        )
    n = len(sdk.query(kind="operator"))
    page = sdk.paginated_query(kind="operator", limit=2)
    assert n > 0, "query(kind='operator') is still a guaranteed empty"
    assert page["total"] == n, (
        f"paginated_query total ({page['total']}) disagrees with query() ({n}) — "
        "the two read paths have drifted"
    )


# ═══════════════════════════════════════════════════════════════════
# (3) the DEFAULT must be untouched: operators stay out of point queries
# ═══════════════════════════════════════════════════════════════════

def test_operators_stay_out_of_default_and_non_operator_queries(sdk):
    """The fix yields only to an explicit operator request — nothing else moves.

    NOTE: an operator created by ``create_operator`` carries ``is_operator=true`` and
    ``op_type`` but NO ``pointKind`` (the SDK declares the operator kind field to be
    ``op_type``, not ``pointKind``), so it is identified here from raw Cypher rather
    than through a kind query. What this pins is the exclusion: such a node must not
    surface in a default or non-operator point query.
    """
    a = _mk_point(sdk, "evidence", "src")
    b = _mk_point(sdk, "evidence", "tgt")
    sdk.create_operator("IMPL", a, [b], label="supports")

    op_ids = {r[0] for r in _raw(
        sdk, "MATCH (n:Point) WHERE n.is_operator = true RETURN n.id")}
    assert op_ids, "create_operator produced no operator node"

    for label, rows in (
        ("query()", sdk.query()),
        ("query(kind='evidence')", sdk.query(kind="evidence")),
    ):
        returned = {r.get("id") for r in rows}
        leaked = sorted(returned & op_ids)
        assert not leaked, f"{label} returned operator node(s): {leaked}"
        stray = sorted(
            r.get("id") for r in rows
            if r.get("is_operator") is True or r.get("op_type")
        )
        assert not stray, f"{label} leaked operator-shaped row(s): {stray}"
