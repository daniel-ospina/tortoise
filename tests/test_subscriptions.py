"""Task 5 tests — cursor-based events_poll (SDK level).

Uses the shared sdk_factory fixture (tests/conftest.py, Task 1).
"""
import base64
import json

import pytest


def test_poll_after_cursor_roundtrip(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    p1 = sdk.create_point("statement", "first")
    p2 = sdk.create_point("statement", "second")
    r1 = sdk.events_poll(after=None)
    assert [e["type"] for e in r1["events"]] == ["PointAdded", "PointAdded"]
    r2 = sdk.events_poll(after=r1["next_cursor"])
    assert r2["events"] == []  # nothing after tail
    sdk.retract_point(p2["id"])
    r3 = sdk.events_poll(after=r1["next_cursor"])
    assert [e["type"] for e in r3["events"]] == ["PointRetracted"]


def test_cursor_is_opaque_and_deterministic(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    sdk.create_point("statement", "x")
    r = sdk.events_poll()
    # opaque: base64url JSON {v:1, seq:N}
    decoded = json.loads(base64.urlsafe_b64decode(r["next_cursor"]))
    assert decoded["v"] == 1 and decoded["seq"] == 1
    # same tail cursor always yields the same batch (dedup + stable order)
    r2 = sdk.events_poll()
    assert r2["events"] == r["events"]


def test_empty_graph_cursor_roundtrip(sdk_factory, tmp_path):
    """plan-review P2: the empty-graph cursor uses the SAME opaque format —
    b64url({"v":1,"seq":0}) — and round-trips through _decode_cursor."""
    sdk = sdk_factory(tmp_path)
    r = sdk.events_poll()
    assert r["events"] == []
    decoded = json.loads(base64.urlsafe_b64decode(r["next_cursor"]))
    assert decoded == {"v": 1, "seq": 0}
    assert sdk.events_poll(after=r["next_cursor"])["events"] == []


def test_types_filter(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    sdk.create_point("statement", "a")
    sdk.retract_point(sdk.query()[0]["id"])
    r = sdk.events_poll(types=["PointRetracted"])
    assert [e["type"] for e in r["events"]] == ["PointRetracted"]
    with pytest.raises(ValueError, match="unknown event type"):
        sdk.events_poll(types=["Nope"])


def test_malformed_cursor_raises(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    sdk.create_point("statement", "x")
    with pytest.raises(ValueError, match="invalid cursor"):
        sdk.events_poll(after="not-a-cursor!!")


def test_expired_cursor_raises_valueerror(sdk_factory, tmp_path):
    """plan-review P2: SDK raises a structured ValueError; _safe → MCP error,
    REST maps to 410. Purge triggered via direct Cypher DELETE (Task 5 must
    not depend on Task 7's purge helper)."""
    from tortoise import event_store
    sdk = sdk_factory(tmp_path)
    sdk.create_point("statement", "will-purge")
    old_cursor = sdk.events_poll()["next_cursor"]
    proj = sdk._get_proj()
    proj.g.query("MATCH (e:GraphEvent) SET e.ts = $old_ts",
                 params={"old_ts": "2020-01-01T00:00:00+00:00"})
    sdk.create_point("statement", "kept")  # newer event survives the purge
    # purge helper lands in Task 7 — trigger expiry here via direct Cypher
    # DELETE. The old cursor points at seq 1; purge seq <= 1 so min becomes 2
    # and the cursor (1) is below it → expired. (Plan snippet said seq < 1,
    # which deletes nothing since events start at seq 1 — corrected.)
    proj.g.query("MATCH (n:GraphEvent) WHERE n.seq < 2 DELETE n")
    from tortoise import event_store as _es
    _es._refresh_first_seq(proj)  # watermark, as purge_expired would
    with pytest.raises(ValueError, match="cursor expired"):
        sdk.events_poll(after=old_cursor)


def test_limit_respected(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    for i in range(5):
        sdk.create_point("statement", f"p{i}")
    r = sdk.events_poll(limit=2)
    assert len(r["events"]) == 2
    r2 = sdk.events_poll(after=r["next_cursor"], limit=2)
    assert len(r2["events"]) == 2
