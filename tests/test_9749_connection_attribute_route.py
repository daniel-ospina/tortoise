"""#9749 — `confidence`/`weight` follow the connection's EFFECTIVE route.

The defect: ingest validated `confidence`/`weight` as §8 direct-edge
attributes and then lost them on BOTH write paths.

* **Promoted route.** A plain IMPL/NAND between `:Point` endpoints is
  reified into an operator by the endpoint-kind gate (#8499, owner ruling
  2026-10-13), and `create_operator` writes `(op)-[:IMPL|NAND {idx}]->(endpoint)`
  — no place to put either attribute. Measured on the pre-fix tree: a singular
  plain IMPL between Points with `confidence: 0.5` validated **clean**, wrote
  the operator and its edges, and every edge read back `confidence=None`.
* **Direct route.** `Object↔Object` / `Subject↔…` connections keep the
  operator-less edge, whose primitive (`create_direct_edge`) has always
  accepted `confidence`/`weight` and SET them on the edge — but ingest never
  forwarded them, so that route dropped them too (measured: an
  `Object↔Object` IMPL with `confidence: 0.6` wrote an edge with
  `confidence=None`).

`docs/INGEST_CONTRACT.md` §13 row 4 already claimed the answer for the first
one (a Phase-1 violation) and names the direct edge as the attributes' home;
the code implemented neither. This suite pins both halves, plus the rebuild
durability of the half that now stores values (the `DirectEdgeCreated` replay
sets `direction`/`confidence`/`weight`/`label`/`batch_id`, so a forwarded
value is not a live-only artifact).

⛔ The rule is judged by the EFFECTIVE route, not the request: both spellings
of the promoted shape are covered below. #9749's own comment on the #9173 arm
made that a requirement — the attribute arm had to move WITH the multi-item
arm, or the two rules disagree inside one function.
"""
from __future__ import annotations

import os

import pytest

from tortoise.exceptions import BundleValidationError
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk(tmp_path):
    sdk = TortoiseSDK(os.path.join(str(tmp_path), "attrs.db"))
    yield sdk
    sdk.close()


def _g(sdk):
    return sdk._get_proj().g


def _count(sdk, cypher: str, params: dict | None = None) -> int:
    rows = _g(sdk).query(cypher, params=params or {}).result_set
    return int(rows[0][0]) if rows else 0


def _points_pair(extra: dict | None = None) -> dict:
    """Two bundle-local points + one plain IMPL connection between them."""
    conn = {"from": "pA", "to": "pB", "operator": "IMPL"}
    conn.update(extra or {})
    return {
        "points": [
            {"ref": "pA", "kind": "statement", "content": "A implies B."},
            {"ref": "pB", "kind": "statement", "content": "B."},
        ],
        "connections": [conn],
    }


def _objects_pair(extra: dict | None = None) -> dict:
    """Two bundle-local :Object entities + one plain IMPL between them.

    No plain `:Point` endpoint, so the endpoint-kind gate does NOT fire and the
    connection stays on the operator-less direct route — the attributes' only
    home (INGEST_CONTRACT §13 row 4).
    """
    conn = {"from": "oA", "to": "oB", "operator": "IMPL"}
    conn.update(extra or {})
    return {
        "entities": [
            {"ref": "oA", "type": "object", "name": "Object A"},
            {"ref": "oB", "type": "object", "name": "Object B"},
        ],
        "connections": [conn],
    }


# ═══════════════════════════════════════════════════════════════════════
# Half 1 — the promoted route REFUSES the attributes (Phase 1 AND 2)
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("key,value", [("confidence", 0.5), ("weight", 0.25)])
def test_promoted_point_pair_refuses_the_attribute(sdk, key, value):
    """The promoted spelling: request says `direct`, the write route is
    `operator`. It must NOT be called clean — §13 row 4 calls it a Phase-1
    violation, and pre-#9749 the value was silently discarded."""
    bundle = _points_pair({key: value})

    # Phase 1 — the validation surface a caller inspects first.
    violations = sdk._validate_bundle(bundle)
    assert len(violations) == 1, violations
    msg = violations[0]["message"]
    assert key in msg
    # The message must name the MECHANISM (the promotion) and the remedy, not
    # only the verdict: the caller never asked for the operator route.
    assert "#8499" in msg and "operator-mediated" in msg, msg
    assert "create_direct_edge" in msg, msg

    # Phase 1 must also be ZERO-mutation, and Phase 2 must agree.
    with pytest.raises(BundleValidationError) as exc:
        sdk.ingest(bundle)
    assert key in exc.value.violations[0]["message"]
    assert _count(sdk, "MATCH (n:Point) RETURN count(n)") == 0, (
        "the refusal must not commit the bundle's points"
    )
    assert _count(sdk, "MATCH ()-[r:IMPL|NAND]->() RETURN count(r)") == 0


def test_explicit_operator_spelling_refuses_the_attribute(sdk):
    """The other spelling of the same route (`reify: true`): the caller DID ask
    for the operator route, and gets the operator-route sentence."""
    bundle = _points_pair({"reify": True, "confidence": 0.5})
    violations = sdk._validate_bundle(bundle)
    assert len(violations) == 1, violations
    msg = violations[0]["message"]
    assert "confidence" in msg and "operator route" in msg, msg
    with pytest.raises(BundleValidationError):
        sdk.ingest(bundle)
    assert _count(sdk, "MATCH (n:Point) RETURN count(n)") == 0


def test_mitigation_route_refuses_the_attribute_too(sdk):
    """`mitigation` also selects the operator route (`_connection_route`), so
    the attribute arm applies there for the same reason."""
    bundle = _points_pair({
        "mitigation": {"reason": "weak source", "strength": 0.2},
        "weight": 0.4,
    })
    violations = sdk._validate_bundle(bundle)
    assert any("weight" in v["message"] for v in violations), violations


def test_a_point_pair_without_the_attributes_still_ingests(sdk):
    """The refusal is scoped to the attributes: the plain promoted spelling
    keeps working (it reifies and warns, #8499)."""
    res = sdk.ingest(_points_pair())
    assert res["created"]["connections"] == 1
    assert _count(sdk, "MATCH (n:Point {is_operator:true}) RETURN count(n)") == 1
    assert any(w.startswith("point_mediated_route:")
               for w in res.get("warnings") or [])


# ═══════════════════════════════════════════════════════════════════════
# Half 2 — the direct route STORES them (and the graph agrees with validate)
# ═══════════════════════════════════════════════════════════════════════

def test_direct_route_writes_confidence_and_weight(sdk):
    """The regression proper: validation must not be the only place the value
    exists. `_validate_bundle` clean ⇒ the written edge carries both."""
    bundle = _objects_pair({"confidence": 0.6, "weight": 0.3,
                            "direction": "unidirectional"})
    assert sdk._validate_bundle(bundle) == []

    sdk.ingest(bundle)

    rows = _g(sdk).query(
        "MATCH (a:Object {name:'Object A'})-[r:IMPL]->(b:Object {name:'Object B'}) "
        "RETURN r.confidence, r.weight, r.direction, r.batch_id"
    ).result_set
    assert len(rows) == 1, rows
    confidence, weight, direction, batch_id = rows[0]
    assert confidence == pytest.approx(0.6), (
        "the connection's confidence was validated and then not written"
    )
    assert weight == pytest.approx(0.3)
    assert direction == "unidirectional"
    assert batch_id, "the §16 stamp rides the same edge"


def test_direct_route_with_subject_pair_also_stores_the_value(sdk):
    """Subject↔Subject is non-Point, so it takes the same direct route — the
    attribute home is every pair the endpoint-kind gate does not promote."""
    bundle = {
        "entities": [
            {"ref": "sA", "type": "subject", "name": "Subject A",
             "subjectKind": "organization"},
            {"ref": "sB", "type": "subject", "name": "Subject B",
             "subjectKind": "organization"},
        ],
        "connections": [{"from": "sA", "to": "sB", "operator": "IMPL",
                         "confidence": 0.4}],
    }
    assert sdk._validate_bundle(bundle) == []
    sdk.ingest(bundle)
    assert _count(
        sdk,
        "MATCH (:Subject {name:'Subject A'})-[r:IMPL]->(:Subject {name:'Subject B'}) "
        "WHERE r.confidence = 0.4 RETURN count(r)",
    ) == 1


def test_direct_route_range_check_still_applies(sdk):
    """The forwarding must not swallow the §8 validity rule on the route that
    accepts the attribute (an out-of-band value stays a Phase-1 violation)."""
    violations = sdk._validate_bundle(_objects_pair({"confidence": 1.7}))
    assert len(violations) == 1, violations
    assert "must be a number in [0, 1]" in violations[0]["message"]
    with pytest.raises(BundleValidationError):
        sdk.ingest(_objects_pair({"confidence": 1.7}))


def test_direct_route_attributes_survive_rebuild(tmp_path):
    """Durability of the half that stores values: the forwarded attributes ride
    the `DirectEdgeCreated` descriptor the primitive already emitted, so
    `rebuild_all` restores them (a live-only value would be a new silent drop —
    the class this issue is about, one layer down)."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(os.path.join(str(tmp_path), "durable.db"),
                      event_log_path=str(events / "events.jsonl"))
    try:
        sdk.ingest(_objects_pair({"confidence": 0.6, "weight": 0.3,
                                  "label": "supports"}))
        query = (
            "MATCH (:Object {name:'Object A'})-[r:IMPL]->"
            "(:Object {name:'Object B'}) "
            "RETURN r.confidence, r.weight, r.label"
        )
        before = list(_g(sdk).query(query).result_set)
        assert before and before[0][0] == pytest.approx(0.6), before

        sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

        after = list(_g(sdk).query(query).result_set)
        assert len(after) == 1, f"the direct edge did not survive rebuild: {after}"
        assert after[0][0] == pytest.approx(0.6), (
            f"confidence dropped by the rebuild: {after[0]}"
        )
        assert after[0][1] == pytest.approx(0.3)
        assert after[0][2] == "supports"
    finally:
        sdk.close()
