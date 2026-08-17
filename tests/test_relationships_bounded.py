"""Tests for get_relationships_bounded — #1353 bounded state-centric decoration.

Covers the D1-D14 locked design:
- class-aware cap (critical classes exempt from per-point AND global budgets)
- peer EP state derived from coalesced posterior/ep alpha/beta (annotate_ep_batch parity)
- mitigation points surfaced as mitigated_by, excluded from IMPL endpoints (both directions)
- retracted operators excluded; self-peers excluded
- legacy keys preserved; related_content only via expand (get_relationships regression)
- global budget exhaustion → structure counts for tail results
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK
from tortoise.search_engine import get_relationships, get_relationships_bounded

TEST_GRAPH = "tortoise_test_1353_relationships_bounded"


@pytest.fixture
def sdk(tmp_path):
    sdk = TortoiseSDK(str(tmp_path / "topic.db"), namespace=TEST_GRAPH)
    yield sdk
    try:
        sdk.test_guard()
        proj = sdk._get_proj()
        proj.g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    sdk.close()


def _graph(sdk):
    return sdk._get_proj().g


def _point(sdk, kind="statement", content="some claim content here"):
    return sdk.create_point(kind, content)


def _set_ep(sdk, pid, alpha, beta):
    """Persist EP posterior (ep_* path — exercises the coalesce fallback)."""
    _graph(sdk).query(
        "MATCH (n:Point) WHERE n.id = $id SET n.ep_alpha = $a, n.ep_beta = $b",
        params={"id": pid, "a": alpha, "b": beta},
    )


def _set_status(sdk, pid, status):
    _graph(sdk).query(
        "MATCH (n:Point) WHERE n.id = $id SET n.status = $s",
        params={"id": pid, "s": status},
    )


# ── Basics ──────────────────────────────────────────────────────────────

def test_empty_ids(sdk):
    assert get_relationships_bounded(_graph(sdk), []) == {}


def test_no_edges_returns_empty_lists(sdk):
    p = _point(sdk)
    out = get_relationships_bounded(_graph(sdk), [p["id"]])
    assert out == {p["id"]: []}


# ── Cap + family_size ───────────────────────────────────────────────────

def test_support_mass_capped_family_size_reported(sdk):
    """1 result point, 1 IMPL op with 30 endpoints → ≤10 support entries + family_size."""
    a = _point(sdk, content="alpha claim target")
    peers = [_point(sdk, content=f"peer {i} claim") for i in range(30)]
    sdk.create_operator("IMPL", a["id"], [p["id"] for p in peers])

    out = get_relationships_bounded(_graph(sdk), [a["id"]])
    entries = out[a["id"]]
    peer_entries = [e for e in entries if "peer" in e]
    # 30 IMPL support-mass members, per-op capped → ≤10 peer entries
    assert len(peer_entries) <= 10, f"expected ≤10 peer entries, got {len(peer_entries)}"
    # family_size discloses the true operator family (a + 30 peers = 31 endpoints)
    family = {e.get("family_size") for e in peer_entries}
    assert 31 in family, f"family_size=31 expected, got {family}"
    # legacy keys preserved
    assert all(
        {"predicate", "mechanism", "operator_id", "related_id", "related_kind", "direction"}
        <= set(e) for e in peer_entries
    )
    # related_content NOT in list view
    assert all("related_content" not in e for e in peer_entries)
    # peer state present
    assert all("peer" in e for e in peer_entries)


def test_operator_with_zero_non_operator_endpoints(sdk):
    """Op whose only endpoints are operators → no entries for the point."""
    a = _point(sdk, content="alpha claim")
    other_op = sdk.create_operator("IMPL", a["id"], [])
    # connect a to another operator only (no non-operator endpoints)
    op = other_op["id"]
    _graph(sdk).query(
        "MATCH (a:Point {id:$aid}), (o:Point {id:$oid}) "
        "MATCH (o)-[r]-(other:Point) WHERE other.is_operator = true "
        "WITH a, o, count(r) AS c WHERE c = 0 "
        "CREATE (o)-[:IMPL {idx:0}]->(a)",
        params={"aid": a["id"], "oid": op},
    )
    out = get_relationships_bounded(_graph(sdk), [a["id"]])
    # self-exclusion: no peer entries for a point whose only member is itself
    assert not any("peer" in e for e in out[a["id"]]), "self must be excluded"


# ── Critical classes always survive ─────────────────────────────────────

def test_nand_always_survives(sdk):
    """10 NAND + 30 IMPL on one point → all 10 NANDs present, IMPLs capped."""
    a = _point(sdk, content="alpha claim target")
    nand_peers = [_point(sdk, content=f"nand peer {i}") for i in range(10)]
    impl_peers = [_point(sdk, content=f"impl peer {i}") for i in range(30)]
    for p in nand_peers:
        sdk.create_operator("NAND", a["id"], [p["id"]])
    sdk.create_operator("IMPL", a["id"], [p["id"] for p in impl_peers])

    out = get_relationships_bounded(_graph(sdk), [a["id"]])
    mechs = [e["mechanism"] for e in out[a["id"]]]
    nand_count = mechs.count("NAND")
    assert nand_count == 10, f"all NANDs must survive, got {nand_count}"
    assert mechs.count("IMPL") <= 10


def test_twelve_nand_all_kept_cap_waived(sdk):
    """12 NAND on one point → all kept (critical classes exempt from the count cap)."""
    a = _point(sdk, content="alpha claim target")
    peers = [_point(sdk, content=f"nand peer {i}") for i in range(12)]
    for p in peers:
        sdk.create_operator("NAND", a["id"], [p["id"]])

    out = get_relationships_bounded(_graph(sdk), [a["id"]])
    mechs = [e["mechanism"] for e in out[a["id"]]]
    assert mechs.count("NAND") == 12, "critical classes are cap-exempt"


def test_contested_peer_always_survives(sdk):
    """Peer with elevated EP variance (ep_* coalesce path) survives beyond the cap."""
    a = _point(sdk, content="alpha claim target")
    contested = _point(sdk, content="disputed peer claim")
    _set_ep(sdk, contested["id"], 2, 2)  # variance 0.05 > 0.04 → contested
    peers = [_point(sdk, content=f"impl peer {i}") for i in range(20)]
    sdk.create_operator("IMPL", a["id"], [contested["id"]] + [p["id"] for p in peers])

    out = get_relationships_bounded(_graph(sdk), [a["id"]])
    related = {e["related_id"] for e in out[a["id"]] if "related_id" in e}
    assert contested["id"] in related, "contested peer must survive the cap"
    peer_state = next(e["peer"] for e in out[a["id"]] if "peer" in e and e["related_id"] == contested["id"])
    assert peer_state["contested"] is True
    assert peer_state["variance"] > 0.04


def test_superseded_peer_always_survives(sdk):
    a = _point(sdk, content="alpha claim target")
    stale = _point(sdk, content="stale superseded peer")
    _set_status(sdk, stale["id"], "superseded")
    peers = [_point(sdk, content=f"impl peer {i}") for i in range(20)]
    sdk.create_operator("IMPL", a["id"], [stale["id"]] + [p["id"] for p in peers])

    out = get_relationships_bounded(_graph(sdk), [a["id"]])
    related = {e["related_id"] for e in out[a["id"]] if "related_id" in e}
    assert stale["id"] in related, "superseded peer must survive the cap"


def test_more_than_budget_criticals_all_kept(sdk):
    """>140 critical entries → all kept (global budget governs support-mass only)."""
    a = _point(sdk, content="alpha claim target")
    nand_peers = [_point(sdk, content=f"nand peer {i}") for i in range(45)]
    for p in nand_peers:
        sdk.create_operator("NAND", a["id"], [p["id"]])

    out = get_relationships_bounded(_graph(sdk), [a["id"]], global_budget=10)
    mechs = [e["mechanism"] for e in out[a["id"]]]
    assert mechs.count("NAND") == 45, "criticals are exempt from the global budget"


# ── Mitigation ──────────────────────────────────────────────────────────

def test_mitigation_surfaced_and_excluded_from_impl_endpoints(sdk):
    """Mitigation point appears as mitigated_by entry; never as an IMPL endpoint."""
    a = _point(sdk, content="alpha claim target")
    b = _point(sdk, content="beta claim")
    sdk.create_operator("IMPL", a["id"], [b["id"]])
    op_id = sdk.create_operator("IMPL", b["id"], [a["id"]])["id"]

    # create mitigation: (m)-[:IMPL]->(op), (op)-[:mitigated_by]->(m)
    m = sdk.create_point("mitigation", "this claim is weakened by missing evidence")
    _graph(sdk).query(
        "MATCH (op:Point {id:$oid}), (m:Point {id:$mid}) "
        "CREATE (m)-[:IMPL]->(op), (op)-[:mitigated_by]->(m)",
        params={"oid": op_id, "mid": m["id"]},
    )

    out = get_relationships_bounded(_graph(sdk), [a["id"]])
    entries = out[a["id"]]
    mechs = [e["mechanism"] for e in entries]
    assert "mitigated_by" in mechs, "mitigation must be surfaced"
    # mitigation point must NOT appear as an IMPL peer
    for e in entries:
        if e["mechanism"] == "IMPL":
            assert e["related_id"] != m["id"], "mitigation must not leak as IMPL endpoint"


# ── Self-peer + retracted operator ─────────────────────────────────────

def test_self_peer_excluded(sdk):
    """other == n rows are excluded in assembly (no self-referential peers)."""
    a = _point(sdk, content="alpha claim target")
    b = _point(sdk, content="beta claim")
    sdk.create_operator("IMPL", a["id"], [b["id"]])

    out = get_relationships_bounded(_graph(sdk), [a["id"]])
    related = {e["related_id"] for e in out[a["id"]]}
    assert a["id"] not in related, "self-peer must be excluded"
    assert b["id"] in related


def test_retracted_operator_edges_excluded(sdk):
    a = _point(sdk, content="alpha claim target")
    b = _point(sdk, content="beta claim")
    op = sdk.create_operator("IMPL", a["id"], [b["id"]])
    _set_status(sdk, op["id"], "retracted")

    out = get_relationships_bounded(_graph(sdk), [a["id"]])
    assert out[a["id"]] == [], "edges through retracted operators must be excluded"


# ── Role / direction ────────────────────────────────────────────────────

def test_role_and_direction_from_idx(sdk):
    """idx=0 → source/outgoing; idx>0 → target/incoming."""
    src = _point(sdk, content="source claim")
    tgt = _point(sdk, content="target claim")
    sdk.create_operator("IMPL", src["id"], [tgt["id"]])

    out = get_relationships_bounded(_graph(sdk), [src["id"], tgt["id"]])
    src_entry = next(e for e in out[src["id"]] if e["related_id"] == tgt["id"])
    assert src_entry["role"] == "source" and src_entry["direction"] == "outgoing"
    tgt_entry = next(e for e in out[tgt["id"]] if e["related_id"] == src["id"])
    assert tgt_entry["role"] == "target" and tgt_entry["direction"] == "incoming"


# ── Global budget exhaustion → structure counts ─────────────────────────

def test_global_budget_exhaustion_degrades_to_counts(sdk):
    """20 points × ~9 support peers > global budget 140 → tail results get counts."""
    points = [_point(sdk, content=f"claim {i}") for i in range(20)]
    for i, p in enumerate(points):
        peers = [_point(sdk, content=f"p{i} peer {j}") for j in range(10)]
        sdk.create_operator("IMPL", p["id"], [q["id"] for q in peers])

    ids = [p["id"] for p in points]
    # full expansion so the GLOBAL budget (not top-K) is what cuts the tail
    out = get_relationships_bounded(_graph(sdk), ids, global_budget=140, expand_top_k=100)
    peer_entries = [e for v in out.values() for e in v if "peer" in e]
    count_entries = [e for v in out.values() for e in v if "count" in e]
    assert len(peer_entries) <= 140, f"global budget exceeded: {len(peer_entries)} > 140"
    assert count_entries, "exhaustion must degrade to structure counts"
    # at least one result retained full peer entries; none crash
    assert any(len(v) > 0 for v in out.values())


def test_tail_private_op_points_get_counts_with_default_topk(sdk):
    """Review-fix (R1): beyond expand_top_k (default 14) points with PRIVATE
    operators still degrade to structure counts — Q2f covers ALL result ops."""
    points = [_point(sdk, content=f"claim {i}") for i in range(20)]
    for i, p in enumerate(points):
        peers = [_point(sdk, content=f"p{i} peer {j}") for j in range(10)]
        sdk.create_operator("IMPL", p["id"], [q["id"] for q in peers])

    ids = [p["id"] for p in points]
    # DEFAULT expand_top_k=14: points 15-20 have private (unshared) operators
    out = get_relationships_bounded(_graph(sdk), ids)
    tail = out[ids[19]]
    assert not any("peer" in e for e in tail), "tail point must not expand peers"
    counts = [e for e in tail if "count" in e]
    assert counts, f"tail point must degrade to structure counts, got {tail}"
    assert any(c["count"] > 0 for c in counts)


# ── get_relationships regression (unbounded path intact — D12) ──────────

def test_corrects_no_duplicates(sdk):
    """Review-fix: chained OPTIONAL MATCH cartesian (2 in × 1 out) must dedupe."""
    old = _point(sdk, content="old claim")
    x = _point(sdk, content="middle claim")
    n1 = _point(sdk, content="newer one")
    n2 = _point(sdk, content="newest two")
    _graph(sdk).query(
        "MATCH (a:Point {id:$old}), (x:Point {id:$x}), (n1:Point {id:$n1}), (n2:Point {id:$n2}) "
        "CREATE (x)-[:CORRECTS]->(a), (n1)-[:CORRECTS]->(x), (n2)-[:CORRECTS]->(x)",
        params={"old": old["id"], "x": x["id"], "n1": n1["id"], "n2": n2["id"]},
    )
    out = get_relationships_bounded(_graph(sdk), [x["id"]])
    rels = [e for e in out[x["id"]] if e["mechanism"] == "CORRECTS"]
    related = [e["related_id"] for e in rels]
    assert len(related) == len(set(related)) == 3, f"CORRECTS must be deduped: {related}"
    state = fetch_point_epistemic_state(_graph(sdk), [x["id"]])[x["id"]]
    assert len(state["supersedes"]) == 1, "supersedes must dedupe"
    assert state["superseded_by"]["id"] == n2["id"], "newest correcting point wins"


def test_retracted_correcting_point_not_authority(sdk):
    """Review-fix: a retracted correcting point is not superseding authority."""
    old = _point(sdk, content="old")
    x = _point(sdk, content="middle")
    n = _point(sdk, content="new")
    _graph(sdk).query(
        "MATCH (x:Point {id:$x}), (old:Point {id:$old}), (n:Point {id:$n}) "
        "CREATE (x)-[:CORRECTS]->(old), (n)-[:CORRECTS]->(x)",
        params={"x": x["id"], "old": old["id"], "n": n["id"]},
    )
    _set_status(sdk, n["id"], "retracted")
    state = fetch_point_epistemic_state(_graph(sdk), [x["id"]])[x["id"]]
    assert state["superseded_by"] is None, "retracted correcting point must not be authority"


def test_criticals_do_not_consume_support_room(sdk):
    """Review-fix (D3): criticals are exempt from the per-point cap."""
    a = _point(sdk, content="alpha")
    peers = [_point(sdk, content=f"peer {i}") for i in range(5)]
    sdk.create_operator("IMPL", a["id"], [p["id"] for p in peers])
    for i in range(12):
        sdk.create_operator("NAND", a["id"], [_point(sdk, content=f"nand {i}")["id"]])
    out = get_relationships_bounded(_graph(sdk), [a["id"]])
    mechs = [e["mechanism"] for e in out[a["id"]]]
    assert mechs.count("NAND") == 12
    assert mechs.count("IMPL") == 5, "criticals must not consume support room"


def test_mixed_createdat_formats_no_crash(sdk):
    """Review-fix: epoch-int + ISO-string createdAt coexist without TypeError."""
    a = _point(sdk, content="alpha")
    p1 = _point(sdk, content="peer iso")
    p2 = _point(sdk, content="peer epoch")
    sdk.create_operator("IMPL", a["id"], [p1["id"], p2["id"]])
    _graph(sdk).query(
        "MATCH (n:Point) WHERE n.id = $id SET n.createdAt = 1755500000",
        params={"id": p2["id"]},
    )
    out = get_relationships_bounded(_graph(sdk), [a["id"]])
    assert len([e for e in out[a["id"]] if "peer" in e]) == 2, "mixed formats must not crash"


def test_get_relationships_regression_full_content(sdk):
    """The unbounded shared function still returns full payloads with content."""
    a = _point(sdk, content="alpha claim target")
    b = _point(sdk, content="beta claim with unique content payload")
    sdk.create_operator("IMPL", a["id"], [b["id"]])

    out = get_relationships(_graph(sdk), [a["id"]])
    entries = out[a["id"]]
    assert len(entries) == 1
    assert "related_content" in entries[0]
    assert "beta claim" in entries[0]["related_content"]
    assert "peer" not in entries[0], "unbounded path shape unchanged"


# ── Task 5: dense-corpus fuzz — preservation + payload budget (#1353 D3/D4) ──

import random
from collections import defaultdict


def test_fuzz_critical_classes_always_survive(sdk, seed=42):
    """Oracle-based preservation fuzz on a dense random graph.

    Every NAND edge, contested peer, superseded/retracted peer, mitigation and
    CORRECTS edge reachable from a subset point MUST survive the bounded
    decoration (critical classes are exempt from both caps, D3/D4). The oracle
    is computed from the fixture structure + EP/status knowledge — independent
    of the function under test.
    """
    rng = random.Random(seed)
    points = [sdk.create_point("statement", f"claim {i}") for i in range(40)]
    ids = [p["id"] for p in points]

    ops_of: dict[str, set] = defaultdict(set)      # point → op ids it participates in
    op_members: dict[str, set] = defaultdict(set)  # op id → member points
    op_mech: dict[str, str] = {}
    mitigated: dict[str, str] = {}                  # op id → mitigation point id
    supersedes_out: dict[str, str] = {}             # new → old

    # 6 IMPL ops, ~10 endpoints each
    for oi in range(6):
        src = ids[rng.randrange(len(ids))]
        tgts = rng.sample([i for i in ids if i != src], 10)
        op = sdk.create_operator("IMPL", src, tgts)
        op_members[op["id"]] = {src, *tgts}
        op_mech[op["id"]] = "IMPL"
        for m in op_members[op["id"]]:
            ops_of[m].add(op["id"])

    # 3 NAND ops
    for _ in range(3):
        src = ids[rng.randrange(len(ids))]
        tgt = rng.choice([i for i in ids if i != src])
        op = sdk.create_operator("NAND", src, [tgt])
        op_members[op["id"]] = {src, tgt}
        op_mech[op["id"]] = "NAND"
        for m in op_members[op["id"]]:
            ops_of[m].add(op["id"])

    # 4 contested peers (variance 0.05 > 0.04 via ep_* coalesce path)
    contested: set[str] = set()
    for _ in range(4):
        pid = ids[rng.randrange(len(ids))]
        _set_ep(sdk, pid, 2, 2)
        contested.add(pid)

    # 3 superseded peers
    superseded: set[str] = set()
    for _ in range(3):
        pid = ids[rng.randrange(len(ids))]
        _set_status(sdk, pid, "superseded")
        superseded.add(pid)

    # 2 mitigated operators
    for _ in range(2):
        src = ids[rng.randrange(len(ids))]
        tgt = rng.choice([i for i in ids if i != src])
        op = sdk.create_operator("IMPL", src, [tgt])
        op_members[op["id"]] = {src, tgt}
        op_mech[op["id"]] = "IMPL"
        m = sdk.create_point("mitigation", "weakened by missing evidence")
        _graph(sdk).query(
            "MATCH (op:Point {id:$o}), (m:Point {id:$m}) "
            "CREATE (m)-[:IMPL]->(op), (op)-[:mitigated_by]->(m)",
            params={"o": op["id"], "m": m["id"]},
        )
        mitigated[op["id"]] = m["id"]
        for mm in op_members[op["id"]]:
            ops_of[mm].add(op["id"])

    # CORRECTS chain — supersede_point marks `old` superseded AND transfers
    # old's operator edges to `new` (documented write-path behavior). The oracle
    # mirrors the transfer so its membership matches the graph.
    old, new = ids[0], ids[1]
    sdk.supersede_point(old, new)
    supersedes_out[new] = old
    superseded.add(old)
    for op in list(ops_of[old]):
        op_members[op].discard(old)
        op_members[op].add(new)
        ops_of[new].add(op)
    ops_of[old] = set()

    # Oracle per point: expected critical entries as a MULTISET of
    # {(mechanism, related_id)} — the function emits one entry per (point, op,
    # other), so a peer reachable via several ops counts several times.
    def expected_criticals(x: str) -> list:
        exp = []
        for op in ops_of[x]:
            for other in op_members[op]:
                if other == x:
                    continue
                if op_mech[op] == "NAND" or other in contested or other in superseded:
                    exp.append((op_mech[op], other))
            if op in mitigated:
                exp.append(("mitigated_by", mitigated[op]))
        if x in supersedes_out:
            exp.append(("CORRECTS", supersedes_out[x]))
        if x == old:
            exp.append(("CORRECTS", new))
        return exp

    # 10 random subset trials
    for trial in range(10):
        subset = rng.sample(ids, rng.randint(5, 25))
        out = get_relationships_bounded(_graph(sdk), subset, expand_top_k=1000)
        total_peer_entries = 0
        total_expected_criticals = 0
        for x in subset:
            exp = expected_criticals(x)
            total_expected_criticals += len(exp)
            entries = out.get(x, [])
            present = {(e["mechanism"], e["related_id"]) for e in entries if "related_id" in e}
            missing = set(exp) - present
            assert not missing, f"trial {trial}: point {x} lost critical edges: {missing}"
            total_peer_entries += len([e for e in entries if "peer" in e])
        # payload budget: support-mass capped at 140; criticals always counted on top
        assert total_peer_entries <= 140 + total_expected_criticals, (
            f"trial {trial}: budget blown {total_peer_entries} > 140 + {total_expected_criticals}"
        )


# ── Task 2: fetch_point_epistemic_state + SearchResult promoted fields ───

from tortoise.search_engine import fetch_point_epistemic_state, SearchResult, SearchScores


def test_fetch_state_basic(sdk):
    p = _point(sdk, content="plain claim")
    state = fetch_point_epistemic_state(_graph(sdk), [p["id"]])[p["id"]]
    assert set(state) == {"status", "superseded_by", "supersedes", "subject"}
    assert state["subject"] is None
    assert state["superseded_by"] is None
    assert state["supersedes"] == []


def test_fetch_state_subject_direct(sdk):
    p = _point(sdk, content="claim about the team")
    subj = sdk.create_subject("Epistemic Team", subjectKind="team")
    sdk._get_proj().create_about_edge(p["id"], subj["id"], "aboutSubject")

    state = fetch_point_epistemic_state(_graph(sdk), [p["id"]])[p["id"]]
    assert state["subject"] == {"id": subj["id"], "name": "Epistemic Team", "kind": "team"}


def test_fetch_state_subject_via_event(sdk):
    """Point's event's aboutSubject resolves (≤1 hop via aboutEvent)."""
    p = _point(sdk, content="claim from a session")
    subj = sdk.create_subject("Daniel", subjectKind="legalPerson")
    ev = sdk.create_event("session discussion", eventKind="humanApproval")
    sdk._get_proj().create_about_edge(ev["id"], subj["id"], "aboutSubject")
    sdk._get_proj().create_about_edge(p["id"], ev["id"], "aboutEvent")

    state = fetch_point_epistemic_state(_graph(sdk), [p["id"]])[p["id"]]
    assert state["subject"] == {"id": subj["id"], "name": "Daniel", "kind": "legalPerson"}


def test_fetch_state_subject_chain_not_resolved(sdk):
    """Subject reachable only via operator 2-hop → None (fail-closed, D10)."""
    p = _point(sdk, content="fact about something")
    other = _point(sdk, content="the actual subject claim")
    subj = sdk.create_subject("Wrong Subject", subjectKind="other")
    sdk._get_proj().create_about_edge(other["id"], subj["id"], "aboutSubject")
    sdk.create_operator("IMPL", p["id"], [other["id"]])

    state = fetch_point_epistemic_state(_graph(sdk), [p["id"]])[p["id"]]
    assert state["subject"] is None, "chain-derived subject must NOT resolve (fail-closed)"


def test_fetch_state_superseded_by(sdk):
    old = _point(sdk, content="old claim that is now wrong")
    new = _point(sdk, content="replacement claim with the truth")
    sdk.supersede_point(old["id"], new["id"])

    state = fetch_point_epistemic_state(_graph(sdk), [old["id"]])[old["id"]]
    assert state["status"] == "superseded"
    assert state["superseded_by"] is not None
    assert state["superseded_by"]["id"] == new["id"]
    assert "replacement claim" in state["superseded_by"]["content_snippet"]


def test_fetch_state_supersedes(sdk):
    old = _point(sdk, content="old claim")
    new = _point(sdk, content="new claim")
    sdk.supersede_point(old["id"], new["id"])

    state = fetch_point_epistemic_state(_graph(sdk), [new["id"]])[new["id"]]
    assert any(s["id"] == old["id"] for s in state["supersedes"])


def test_searchresult_to_dict_additive(sdk):
    """Promoted fields emitted when set, absent when not — additive contract."""
    plain = SearchResult(id="p1", content="c", point_kind="statement", scores=SearchScores(rrf=0.01))
    d = plain.to_dict()
    assert "status" not in d and "superseded_by" not in d and "subject" not in d

    decorated = SearchResult(
        id="p2", content="c", point_kind="statement", scores=SearchScores(rrf=0.01),
        status="superseded",
        superseded_by={"id": "new-1", "content_snippet": "replacement", "created_at": "x"},
        supersedes=[{"id": "old-1", "content_snippet": "old", "created_at": "y"}],
        subject={"id": "s-1", "name": "Team", "kind": "team"},
    )
    d2 = decorated.to_dict()
    assert d2["status"] == "superseded"
    assert d2["superseded_by"]["id"] == "new-1"
    assert d2["supersedes"][0]["id"] == "old-1"
    assert d2["subject"]["name"] == "Team"
    # legacy keys still present
    assert d2["id"] == "p2" and d2["similarity"] == 0.01
