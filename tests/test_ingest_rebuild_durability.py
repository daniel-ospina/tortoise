"""Epic #902 A10 (issue #1048) — rebuild durability suite.

Plan §8.6 A10 + E2E-6.4 legs: every bundle-created artifact survives
rebuild_all with its semantics intact. This slice covers the operator
direction/label replay SET (cycle-22/23 P1 fix) + the exactly-once
post-rebuild resubmission + the EP-equality leg.

The wipe-after-parse + line-tolerance ordering pins and the S13/S15
rebuild-semantics split are covered by epic #900's T12 suite
(tests/test_index_restore.py) — the shared machinery.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tortoise.sdk import TortoiseSDK


def _count(g, cypher: str, params: dict | None = None) -> int:
    return g.query(cypher, params=params or {}).result_set[0][0]


@pytest.fixture
def a10(tmp_path):
    """(db, events_dir, sdk) with the journal wired."""
    db = os.path.join(str(tmp_path), "a10.db")
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(db, event_log_path=str(events / "events.jsonl"))
    yield db, events, sdk
    sdk.close()


def _rebuild(sdk, events_dir: Path) -> None:
    sdk._get_proj().rebuild_all(str(events_dir))


# ═══════════════════════════════════════════════════════════════════════
# E2E-6.4 (ii): operator direction/label survive rebuild (cycle-22/23)
# ═══════════════════════════════════════════════════════════════════════

def test_a10_operator_direction_label_survive_rebuild(a10):
    """E2E-6.4 (ii) cycle-23: post-rebuild operator `direction`/`label` equal
    the pre-rebuild values for a LABELED AND a UNIDIRECTIONAL operator (the
    A10 replay-SET extension — previously the fixed SET list dropped them →
    direction=NULL + label=NULL)."""
    db, events, sdk = a10
    pa = sdk.create_point("statement", "A")["id"]
    pb = sdk.create_point("statement", "B")["id"]
    pc = sdk.create_point("statement", "C")["id"]
    labeled = sdk.create_operator("IMPL", pa, [pb], label="supports")["id"]
    unidir = sdk.create_operator("NAND", pa, [pc], direction="unidirectional")["id"]
    g = sdk._get_proj().g
    pre = {
        labeled: g.query("MATCH (o:Point {id:$p}) RETURN o.direction, o.label",
                         params={"p": labeled}).result_set[0],
        unidir: g.query("MATCH (o:Point {id:$p}) RETURN o.direction, o.label",
                        params={"p": unidir}).result_set[0],
    }
    _rebuild(sdk, events)
    for pid, (dir_pre, label_pre) in pre.items():
        post = g.query("MATCH (o:Point {id:$p}) RETURN o.direction, o.label",
                       params={"p": pid}).result_set[0]
        assert post[0] == dir_pre, f"direction drift for {pid}: {post[0]} != {dir_pre}"
        assert post[1] == label_pre, f"label drift for {pid}: {post[1]} != {label_pre}"
    assert pre[labeled][0] == "bidirectional" and pre[labeled][1] == "supports"
    assert pre[unidir][0] == "unidirectional" and pre[unidir][1] is None


def test_a10_post_rebuild_resubmission_exactly_once(a10):
    """E2E-6.4 (ii) cycle-23: post-rebuild resubmission is exactly-once for a
    LABELED operator variant AND a UNIDIRECTIONAL variant — the strict-matching
    builders dedup-hit their own run-1 operators (a duplicate = the exactly-once
    P1 class the cycle-17 clause correction closed, reopened by rebuild prop
    loss)."""
    db, events, sdk = a10
    bundle_l = {
        "points": [
            {"ref": "pA", "kind": "statement", "content": "A supports B"},
            {"ref": "pB", "kind": "statement", "content": "B"},
        ],
        "connections": [{"from": "pA", "to": "pB", "operator": "IMPL",
                         "label": "supports", "reify": True}],
    }
    bundle_u = {
        "points": [
            {"ref": "pA", "kind": "statement", "content": "A attacks B"},
            {"ref": "pB", "kind": "statement", "content": "B"},
        ],
        "connections": [{"from": "pA", "to": "pB", "operator": "NAND",
                         "direction": "unidirectional", "reify": True}],
    }
    first_l = sdk.ingest(bundle_l)
    first_u = sdk.ingest(bundle_u)
    g = sdk._get_proj().g
    assert _count(g, "MATCH (o:Point {is_operator:true}) RETURN count(o)") == 2
    _rebuild(sdk, events)
    # post-rebuild resubmission → dedup hits (exactly-once), no duplicates
    second_l = sdk.ingest(bundle_l)
    second_u = sdk.ingest(bundle_u)
    assert _count(g, "MATCH (o:Point {is_operator:true}) RETURN count(o)") == 2
    assert second_l["deduped"]["connections"] == 1
    assert second_u["deduped"]["connections"] == 1
    assert second_l["ids"]["connections"][0] == first_l["ids"]["connections"][0]
    assert second_u["ids"]["connections"][0] == first_u["ids"]["connections"][0]


def test_a10_post_rebuild_ep_direction_semantics_preserved(a10):
    """E2E-6.4 (ii) cycle-23: a UNIDIRECTIONAL operator does not silently flip
    to bidirectional after rebuild — the direction the EP factor-extraction
    reads (ep.py coalesce(r.direction,'bidirectional')) is preserved."""
    db, events, sdk = a10
    pa = sdk.create_point("statement", "A")["id"]
    pb = sdk.create_point("statement", "B")["id"]
    op = sdk.create_operator("NAND", pa, [pb], direction="unidirectional")["id"]
    g = sdk._get_proj().g
    _rebuild(sdk, events)
    d = g.query("MATCH (o:Point {id:$p}) RETURN o.direction",
                params={"p": op}).result_set[0][0]
    assert d == "unidirectional", f"operator flipped to {d!r} after rebuild"


# ═══════════════════════════════════════════════════════════════════════
# E2E-6.4(i): the CONTENT+KIND fallback scan (cycle-17/18 mechanism)
# ═══════════════════════════════════════════════════════════════════════

def test_a10_content_kind_fallback_dedups_hash_less_sibling(a10):
    """E2E-6.4(i) unit slice: a mid-function crash leaves a live Point
    WITHOUT content_hash (the dedup key) — the content-hash MATCH misses, but
    the CONTENT+KIND FALLBACK SCAN (hash-less sibling detection) makes the
    retry dedup to EXACTLY ONE point (same id, never a permanent duplicate)."""
    db, events, sdk = a10
    g = sdk._get_proj().g
    # simulate the crash partial: a live hash-less Point (content + kind set)
    content = "crash-partial-content"
    g.query(
        "CREATE (n:Point {id:'crash-partial', content:$c, pointKind:'statement', "
        "is_operator:false, status:'live'})",
        params={"c": content})
    # create_point(dedup=True) with the same content+kind → the fallback finds
    # the hash-less sibling → SAME id, no second point
    p = sdk.create_point("statement", content, dedup=True)
    assert p["id"] == "crash-partial"
    assert _count(g, "MATCH (n:Point {content:$c}) RETURN count(n)",
                  {"c": content}) == 1


def test_a10_post_rebuild_fallback_dedups_unpatched_resubmission(a10):
    """E2E-6.4(i) post-rebuild leg (CYCLE-23 nit-fix): a hash-less partial
    SURVIVES rebuild content-intact (the #548 snapshot synthesis) with
    content_hash NULL — an UNPATCHED resubmission dedups via the fallback
    (exactly-once; the hash-less sibling exists post-rebuild is asserted
    FIRST, so a synthesis regression cannot pass the leg vacuously)."""
    db, events, sdk = a10
    g = sdk._get_proj().g
    content = "post-rebuild-partial"
    g.query(
        "CREATE (n:Point {id:'pr-partial', content:$c, pointKind:'statement', "
        "is_operator:false, status:'live'})",
        params={"c": content})
    _rebuild(sdk, events)
    # the hash-less partial exists post-rebuild, content intact, hash NULL
    row = g.query("MATCH (n:Point {id:'pr-partial'}) RETURN n.content, "
                  "n.content_hash").result_set[0]
    assert row[0] == content and row[1] is None
    # unpatched resubmission → fallback dedups to the SAME id, one point total
    p = sdk.create_point("statement", content, dedup=True)
    assert p["id"] == "pr-partial"
    assert _count(g, "MATCH (n:Point {content:$c}) RETURN count(n)",
                  {"c": content}) == 1
