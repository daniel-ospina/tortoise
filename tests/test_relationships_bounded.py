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
import tempfile  # noqa: F401

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
    # peer state present — and HONEST for unmeasured (no persisted EP) peers:
    # confidence/variance must be None, never a fabricated Beta(1,1) posterior
    # (second-model gate R3, #1353)
    assert all("peer" in e for e in peer_entries)
    assert all(e["peer"]["confidence"] is None and e["peer"]["variance"] is None
               for e in peer_entries), "unmeasured peers must carry None state, not 0.5/0.0833"


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

import random  # noqa: E402
from collections import defaultdict  # noqa: E402


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
    for oi in range(6):  # noqa: B007
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

from tortoise.search_engine import fetch_point_epistemic_state, SearchResult, SearchScores  # noqa: E402, I001


def test_fetch_state_basic(sdk):
    p = _point(sdk, content="plain claim")
    state = fetch_point_epistemic_state(_graph(sdk), [p["id"]])[p["id"]]
    assert {"status", "superseded_by", "supersedes", "subject"} <= set(state)
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
    """Point's event's aboutSubject resolves (≤1 hop via the eventId property).

    #1417: the fallback hop resolves the source-event via the point's eventId
    property (the provenance surface), never via an aboutEvent edge (which is
    content-aboutness).
    """
    p = _point(sdk, content="claim from a session")
    subj = sdk.create_subject("Daniel", subjectKind="legalPerson")
    ev = sdk.create_event("session discussion", eventKind="humanApproval")
    sdk._get_proj().create_about_edge(ev["id"], subj["id"], "aboutSubject")
    # Provenance surface: the point's eventId property — no aboutEvent edge.
    _graph(sdk).query(
        "MATCH (n:Point {id:$id}) SET n.eventId=$eid",
        params={"id": p["id"], "eid": ev["id"]},
    )

    state = fetch_point_epistemic_state(_graph(sdk), [p["id"]])[p["id"]]
    assert state["subject"] == {"id": subj["id"], "name": "Daniel", "kind": "legalPerson"}


def test_fetch_state_subject_via_event_requires_eventid(sdk):
    """#1417: the fallback requires the point's eventId property — an aboutEvent
    edge alone (legacy/content) must NOT resolve the subject anymore."""
    p = _point(sdk, content="claim wired only via content edge")
    subj = sdk.create_subject("Legacy", subjectKind="team")
    ev = sdk.create_event("legacy session", eventKind="humanApproval")
    sdk._get_proj().create_about_edge(ev["id"], subj["id"], "aboutSubject")
    # Only the (content) aboutEvent edge exists — no eventId on the point.
    sdk._get_proj().create_about_edge(p["id"], ev["id"], "aboutEvent")

    state = fetch_point_epistemic_state(_graph(sdk), [p["id"]])[p["id"]]
    assert state["subject"] is None, (
        "aboutEvent is content, not provenance — subject must not resolve"
    )


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


# ── #6976: the id predicate must survive a following clause ──────────────
#
# Measured on the canonical instance (FalkorDB 6.0.0; re-measured on 6.0.1 /
# graph module 60001): `MATCH (n:Point) WHERE n.id IN $ids` followed by a MATCH
# that re-binds `n` OR carries a RELATIONSHIP pattern is planned as a WHOLE-GRAPH
# read — the rows come back carrying OTHER points' ids (or a NULL `n`) and a
# point with live relationships reads as having none. Measured NOT to drop:
# OPTIONAL MATCH, CALL, a node-only MATCH, and `-[r*1..3]-` (see the trigger
# notes in `unbarred`). Every reader here builds its
# result dict from the REQUESTED ids, so those rows were silently discarded and
# a point with live relationships read as having none — `expand_relationships`
# returned `[]` for a point that had two
# (`sdk.py: expand_relationships` → `get_relationships(...).get(point_id, [])`).
#
# The fix is a load-bearing `WITH <var>` between the predicate and the next
# clause (or folding the predicate into the same MATCH as the expansion).
#
# WHICH TEST ACTUALLY GUARDS THIS — measured by mutating the fix and re-running:
#   * ``test_no_query_loses_its_id_predicate`` FAILS without the ``WITH``. It is
#     the regression guard — for every site, once the CALL-body import is
#     excluded (a `WITH` inside `CALL { … }` is not a barrier; see the test).
#   * the behavioural tests CANNOT fire on the unit lane: its 4.20.4 engine
#     binds this shape, so do not "enlarge the fixture" hoping they will catch
#     it. Precisely: ``test_id_predicate_does_not_leak_other_points`` is blind by
#     construction (``get_relationships`` pre-seeds its dict from the requested
#     ids and drops rows whose pid is unknown), but
#     ``test_point_with_relationships_does_not_read_as_empty`` is NOT blind —
#     ``assert out[a["id"]]`` would fail on an engine that drops the predicate.
#     It passes because this lane's engine binds, not because it cannot see it.
#   * the unit lane cannot reproduce the defect at all — the 4.20.4 test engine
#     BINDS this shape while the 6.0.0 canonical instance drops it (see the
#     module comment in tortoise/search_engine.py), which is exactly why the
#     static guard is the rail.
#
# REPRODUCIBILITY (measured on 6.0.1, graph module 60001 @
# 127.0.0.1:16379): the drop IS live here. The VERBATIM pre-fix
# `get_relationships` query on a two-triple graph returns 4 distinct ids for a
# 1-id request; the identical string with `WITH n` returns 1. On 4.20.4
# (`:6379`, module 42004) the unbarred query binds — which is why the unit lane
# cannot see the defect and this static rail is the guard. Treat an offender as
# a LIVE leak, not as a convention.


def test_point_with_relationships_does_not_read_as_empty(sdk):
    a = _point(sdk, content="claim A content for 6976")
    b = _point(sdk, content="claim B content for 6976")
    sdk.create_operator("IMPL", a["id"], [b["id"]])

    out = get_relationships(_graph(sdk), [a["id"]])
    assert set(out) == {a["id"]}, "keys must be exactly the requested ids"
    assert out[a["id"]], (
        "a point with an operator edge read as having none — the id predicate "
        "did not bind (#6976)"
    )
    assert b["id"] in {e["related_id"] for e in out[a["id"]]}


def test_id_predicate_does_not_leak_other_points(sdk):
    """Two independent pairs: asking about one must not return the other's."""
    a = _point(sdk, content="claim A content for 6976")
    b = _point(sdk, content="claim B content for 6976")
    c = _point(sdk, content="claim C content for 6976")
    d = _point(sdk, content="claim D content for 6976")
    sdk.create_operator("IMPL", a["id"], [b["id"]])
    sdk.create_operator("IMPL", c["id"], [d["id"]])

    out = get_relationships(_graph(sdk), [a["id"]])
    assert set(out) == {a["id"]}
    related = {e["related_id"] for e in out[a["id"]]}
    assert d["id"] not in related, (
        "a relationship belonging to an unrequested point leaked into the "
        "result — the id predicate did not bind (#6976)"
    )


def test_no_query_loses_its_id_predicate():
    """#6976 — forbid the shape outright so a silent wrong read cannot return.

    The scan is parser-based: it walks every string constant (implicit
    concatenation is already folded into one), the string fragments inside a
    list/tuple/set/dict literal, and `+`-joined expressions. What it cannot see
    is what the parser cannot see — a query assembled at runtime from a
    variable, or from pieces joined by a call whose arguments are not literals.
    Prose in a docstring IS scanned (it is a string constant), so quoting the
    bad shape in prose will be flagged; that is a false positive to fix at the
    prose, not a hole.

    Boundaries and LIMITATIONS, stated as measured rather than as guarantees:
      * the window ends at `WITH` or `UNION`. A `WITH` INSIDE a `CALL { … }`
        body therefore truncates the window — incidentally, not because the
        subquery's argument import is being modelled — and a `UNION` leg counts
        as a separate query (reading the second leg's `(m)` as a re-binding of
        the first leg's variable was a false positive at navigation.py:62).
      * the trigger is a DELIBERATE OVER-APPROXIMATION (see `rel_pattern`): a hit
        may be a false positive, and the fix is to ADD the barrier, never to
        weaken the trigger.
      * KNOWN LIMITATION — this is a coverage-increasing REGRESSION GUARD, not a
        soundness proof. Four review cycles each found predicate/relationship
        SYNTAX that this scanner cannot recognise, so it reports SAFE while
        6.0.1 LEAKS (each measured; each had ZERO live sites in the tree, but
        re-scan before assuming a shape is absent): `(n.id) IN $ids`,
        `n['id'] IN $ids`, `$want = n.id`, `n.id /* c */ IN $ids`, and the
        whitespace relationship form `MATCH (o) - - (p)`. A green rail is
        COVERAGE, not absence — the sound alternative (demand a barrier after
        ANY `WHERE`, no predicate parsing at all) was measured at 63 tree sites
        and is therefore not adoptable. A query-executor-level assertion is the
        structural fix for this class; this rail does not claim to be it.
      * the predicate regex is property-GENERAL (`<var>.<prop> <op> …`),
        case-insensitive, tolerant of backtick quoting and of a parenthesised
        RIGHT-hand side — because the engine drops the predicate for ANY
        property, not only `id` (measured on 6.0.0 for `.pointKind` and
        `.status` as well).
    """
    import ast
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent / "tortoise"
    pred = re.compile(
        r"(?:WHERE|AND|OR)\s*\(?\s*(?:NOT\s*)?\(?\s*`?([A-Za-z_][A-Za-z0-9_]*)`?\s*\.\s*`?"
        r"[A-Za-z_][A-Za-z0-9_]*`?\s*(?:"
        # ANY right-hand side. `\x00` (a runtime fragment) is allowed: excluding
        # it made an f-string RHS a silent miss — `f"…WHERE n.id IN {ids_} …"`.
        # `\x01` cannot appear here: the scan splits on it before matching.
        r"(?:<>|!=|>=|<=|CONTAINS|STARTS\s+WITH|ENDS\s+WITH|IN|LIKE|[=><])\s*(?=[^\s)\]\x01])"
        # `IS [NOT] NULL` has no right-hand side and was a silent miss too
        # (measured leaking on 6.0.1; 11 live sites, none in the leaking shape).
        r"|IS\s+(?:NOT\s+)?NULL"
        r")",
        re.IGNORECASE,
    )

    # A RELATIONSHIP pattern in a following clause CAN drop the predicate even
    # when that clause never mentions the variable: measured on 6.0.1,
    # `… WHERE n.id IN $ids MATCH (o)-[r]-(p) RETURN n` returns rows whose n.id
    # is None for a 1-id request, while `MATCH (o:Point)` (node-only) binds.
    #
    # THE DISCRIMINATOR (measured 2026-10-04, and the reason this is not simply
    # "any relationship pattern"). A following required MATCH DROPS the
    # predicate iff it either
    #   (a) mentions the PREDICATE variable — `MATCH (n)-[r]-(o)`; or
    #   (b) carries a relationship pattern and mentions NO variable already
    #       bound by the predicate's OWN MATCH clause — `MATCH (o)-[r]-(p)`,
    #       `MATCH (o)--(p)`.
    # It BINDS when it is node-only, or when it carries a relationship pattern
    # but re-uses a pre-bound NON-predicate variable. That last case is not
    # hypothetical: running this rail against a clean origin/main tree flagged 5
    # sites — commit_ops.py:384/385/421, hosted_api.py:13476 — which are all the
    # SAME operator-lookup shape,
    #   MATCH (o:Point {is_operator:true, op_type:'IMPL'})-[:IMPL {idx:0}]->(s)
    #   WHERE s.id = $src  MATCH (o)-[:IMPL {idx:1}]->(d)  WHERE d.id = $dst
    # where the following MATCH re-uses the anchor `o`. Measured on 6.0.1 with
    # two operators holding disjoint idx:0/idx:1 endpoints: UNBARRED returns
    # [['A','B']] for $src='A',$dst='B' — the predicate is HONOURED — so all 5
    # were FALSE POSITIVES, and without this clause the rail cannot be green on
    # main and cannot land. Check (b) is what removes them.
    #
    # STILL AN OVER-APPROXIMATION: a node-only MATCH INTERVENING before the
    # relationship MATCH (`MATCH (x:Point) MATCH (o)-[r]-(p)`) binds but is still
    # flagged, and `-[r*1..3]-` binds while `-[r*1]` drops. Both are latent (no
    # live site). Resolve a hit by ADDING THE BARRIER, never by weakening this
    # trigger. `--` is included because the bracket-less undirected form leaks.
    rel_pattern = re.compile(r"-\s*\[|<-|->|--")
    # Clause keywords that END a clause (so the predicate's own pattern text can
    # be isolated) and the node-pattern variable extractor.
    clause_any = re.compile(
        r"\b(?:OPTIONAL\s+MATCH|MATCH|WITH|UNION|CALL)\b", re.IGNORECASE
    )
    pat_var = re.compile(r"\(\s*`?([A-Za-z_][A-Za-z0-9_]*)`?\s*(?::|\s*[,)\]\s])")

    def _vars(text: str) -> set[str]:
        """Node-pattern variables in a clause's pattern text."""
        return set(pat_var.findall(text))

    def _rebinds(var: str, text: str) -> bool:
        """Does ``text`` re-bind ``var`` as a NODE PATTERN endpoint?

        Necessary but NOT sufficient: a following clause that drops the variable
        need not mention it at all (a relationship pattern does it — see
        `rel_pattern`). Do not read a False from here as "safe".
        """
        return (
            re.search(
                # The `(?<![A-Za-z0-9_])` lookbehind is LOAD-BEARING: without it
                # `RETURN count(m)` matches `(m)` as if it were a node pattern and
                # the rail reports a live leak that does not exist (found while
                # widening the operator set — hosted_api.py:14998 and
                # projection/entities.py:2887 were both this false positive).
                r"(?<![A-Za-z0-9_])\(\s*`?" + re.escape(var) + r"`?\s*(?::|\s*[,)\]\s])",
                text,
                re.IGNORECASE,
            )
            is not None
        )
    clause = re.compile(r"\b(OPTIONAL\s+MATCH|MATCH|CALL)\b", re.IGNORECASE)
    with_any = re.compile(r"\bWITH\b", re.IGNORECASE)
    # A `UNION` starts a NEW query branch, so the predicate's branch ends there.
    # navigation.py:62's `WHERE NOT m.id IN $visited … UNION MATCH (…)<-[r]-(m)`
    # is two independent legs; reading the second leg's `(m)` as a re-binding of
    # the first leg's variable was a FALSE POSITIVE of the widened walk.
    branch_end = re.compile(r"\b(WITH|UNION)\b", re.IGNORECASE)
    # ANY clause keyword ends a clause body. The re-bind test must not look past
    # the MATCH it is judging into a later clause — projection/entities.py:2887
    # is safe (`MATCH (s:Source {url:$url})` does not re-bind the predicate's
    # `old`) but a `WITH old, …, size([(old)-[x]-(y) | x])` further down made an
    # unbounded scan call it a live leak.
    body_end = re.compile(
        r"\b(OPTIONAL\s+MATCH|MATCH|CALL|WITH|WHERE|RETURN|DELETE|DETACH|SET|"
        r"REMOVE|CREATE|MERGE|UNWIND|ORDER\s+BY|SKIP|LIMIT|UNION|FOREACH)\b",
        re.IGNORECASE,
    )

    def _strings(node):
        """The query text a node contributes, or None if it contributes none.

        Using the PARSER is what makes this correct: implicit concatenation
        across lines is already folded into ONE Constant, a comment is not a
        string at all, an assignment line is irrelevant, and an inline barrier
        (`… WHERE n.id IN $ids WITH n `) is part of the same string. A
        line-based scan got every one of those wrong (#7050 review P2).
        """
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            parts: list[str] = []
            for piece in node.values:
                if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                    parts.append(piece.value)
                else:
                    parts.append(" \x00 ")  # expression placeholder
            return "".join(parts)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = _strings(node.left), _strings(node.right)
            if left is not None or right is not None:
                return (left or " \x00 ") + (right or " \x00 ")
        if isinstance(node, ast.Call):
            # ``"…".join([frag, frag])`` IS one query — the elements are joined
            # at runtime, so a predicate in one and a MATCH in another belong to
            # the same statement (review P2: an earlier AST rewrite lost this).
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "join"
                and len(node.args) == 1
                and isinstance(node.args[0], (ast.List, ast.Tuple))
            ):
                parts = [
                    p
                    for p in (_strings(e) for e in node.args[0].elts)
                    if p is not None
                ]
                return " \x00 ".join(parts) if parts else None
            return " \x00 "  # a fragment only known at runtime
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            # SIBLINGS, not one query: a list of query strings is not joined by
            # the AST, so splicing them would FABRICATE an offender that exists
            # in no query (review P3). \x01 is a hard boundary the scan splits
            # on — a predicate cannot see across it.
            parts = [p for p in (_strings(e) for e in node.elts) if p is not None]
            return " \x01 ".join(parts) if parts else None
        if isinstance(node, ast.Dict):
            parts = [p for p in (_strings(v) for v in node.values) if p is not None]
            return " \x01 ".join(parts) if parts else None
        if isinstance(node, (ast.Name, ast.Attribute, ast.Subscript)):
            return " \x00 "  # a fragment only known at runtime
        return None

    def unbarred(source: str) -> list[str]:
        """#6976 offenders in one module's source text."""
        found: set[str] = set()
        tree = ast.parse(source)
        # The fragments INSIDE an f-string are not queries. The JoinedStr parent
        # already reconstructs the whole string (with `\x00` standing in for the
        # runtime part), and scanning a fragment ALONE TRUNCATES the query — it
        # cut `… WHERE s.id = $src MATCH (o)-[:` mid-pattern (the `{_edge_type}`
        # that finishes it lives in the next fragment) and fabricated a hit at
        # commit_ops.py:385 while the parent, which sees the whole pattern, was
        # correctly silent. Skip them.
        inner: set[int] = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.JoinedStr):
                inner.update(id(v) for v in n.values)
        for node in ast.walk(tree):
            if id(node) in inner:
                continue
            text = _strings(node)
            if text is None:
                continue
            # \x01 separates SIBLING string literals (a list/dict of templates),
            # which are not one query; \x00 is a runtime fragment inside one
            # query. Splitting on the hard boundary is what stops a predicate in
            # one template matching a MATCH in the next (review P3).
            for segment in text.split("\x01"):
                for m in pred.finditer(segment):
                    var = m.group(1)
                    after = segment[m.end():]
                    # The variables bound by the PREDICATE'S OWN MATCH CLAUSE —
                    # needed by the discriminator below, because a following
                    # relationship MATCH that merely re-uses one of them (and
                    # does not name the predicate variable) BINDS. Measured.
                    _before = segment[: m.start()]
                    _kw = list(clause_any.finditer(_before))
                    clause_vars = _vars(_before[_kw[-1].end():] if _kw else _before)
                    # Walk EVERY following clause, not just the first — a
                    # re-binding clause that comes SECOND still leaks. Measured
                    # (6.0.1): `MATCH (n:Thing) WHERE n.id IN $ids MATCH
                    # (o:Thing) MATCH (n)-[r:LINK]-(p:Thing)` returns 4 distinct
                    # ids for a 1-id request, while a first-clause-only walk
                    # reported it SAFE.
                    barrier = branch_end.search(after)
                    window = after[: barrier.start()] if barrier else after
                    for cm in clause.finditer(window):
                        keyword = cm.group(1).upper()
                        # OPTIONAL MATCH and CALL were MEASURED to bind on the
                        # canonical instance, so they are not triggers (the
                        # review's P3). NB tortoise/search_engine.py's own comment
                        # claims they do trigger — the measurement is the
                        # authority; the branch's barriers in front of them are
                        # belt-and-braces (and go unprotected by this rail).
                        if keyword.startswith("OPTIONAL") or keyword == "CALL":
                            continue
                        # The judge is THAT CLAUSE'S BODY ONLY: scanning further
                        # re-flags a safe query whose LATER clause uses the
                        # variable, e.g. projection/entities.py:2887
                        # `MATCH (s:Source {url:$url})` followed by
                        # `WITH old, …, size([(old)-[x]-(y) | x])`.
                        body = window[cm.end():]
                        following = body_end.search(body)
                        if following:
                            body = body[: following.start()]
                        if _rebinds(var, body) or (
                            rel_pattern.search(body)
                            and not (_vars(body) & clause_vars)
                        ):
                            found.add(f"{node.lineno} ({var})")
                            break
        return sorted(found)

    # The five shapes a line-based scan got wrong (review P2). Pinned here so
    # the holes cannot quietly reopen.
    assignment = 'Q = ("MATCH (n:Point) WHERE n.id IN $ids " "MATCH (n)-[r:IMPL]-(o) RETURN n")'
    assert unbarred(assignment) == ["1 (n)"], assignment
    inline = 'Q = ("MATCH (n:Point) WHERE n.id IN $ids WITH n " "MATCH (n)-[r]-(o) RETURN n")'
    assert unbarred(inline) == [], inline
    long_block = (
        'Q = (\n    "MATCH (n:Point) WHERE n.id IN $ids "\n    "WITH n "\n'
        '    "MATCH (n)-[r]-(o) "\n    "MATCH (o)-[r2]-(p) "\n'
        '    "WHERE p.id <> n.id "\n    "RETURN n"\n)'
    )
    assert unbarred(long_block) == [], long_block
    mixed = 'Q = ("MATCH (n:Point) WHERE n.id IN $ids " + extra + " MATCH (n)-[r]-(o) RETURN n")'
    assert unbarred(mixed) == ["1 (n)"], mixed
    call_body = 'Q = ("MATCH (n:Point) WHERE n.id IN $ids " "CALL { WITH n MATCH (n)-[r]-(o) } RETURN n")'
    # CALL never triggers the drop (measured), so the OLD expectation of an
    # offender here encoded the over-approximation the review flagged. Pinned as
    # SAFE now — if the engine's behaviour changes, this line fails loudly.
    assert unbarred(call_body) == [], call_body
    # A function call that merely LOOKS like a node pattern is not a re-binding:
    # pinned because the first cut of the widened rail reported `RETURN count(m)`
    # as a live leak at hosted_api.py:14998 (a false positive that would have sent
    # a lane hunting a non-existent #6976 site).
    fn_call = 'Q = ("MATCH (m:Membership) WHERE m.org_id <> \'\' " "MATCH (t:Team {id:m.org_id}) RETURN count(m)")'
    assert unbarred(fn_call) == [], fn_call
    # OPTIONAL MATCH never triggers it either.
    optional = 'Q = ("MATCH (n:Point) WHERE n.id IN $ids " "OPTIONAL MATCH (n)-[r]-(o) RETURN n")'
    assert unbarred(optional) == [], optional
    # A following MATCH that never MENTIONS the variable is not automatically
    # safe: with a RELATIONSHIP pattern the engine drops the predicate and NULLs
    # the variable (measured 6.0.1, 4 rows / n.id None for a 1-id request). The
    # `_rebinds`-only trigger declared this shape SAFE and pinned the leak as
    # safe — the same hole class as the first-clause-only walk.
    other_var = 'Q = ("MATCH (n:Point) WHERE n.id IN $ids " "MATCH (o)-[r]-(p) RETURN n")'
    assert unbarred(other_var) == ["1 (n)"], other_var
    # …whereas a NODE-ONLY following MATCH binds (measured), and is the shape of
    # two real sites (hosted_api.py:14998, projection/entities.py:2887) — flagging
    # those would be a false positive that sent a lane chasing a non-leak.
    node_only = 'Q = ("MATCH (n:Point) WHERE n.id IN $ids " "MATCH (o:Point) RETURN n")'
    assert unbarred(node_only) == [], node_only
    # The 5 measured FALSE POSITIVES on main's tree (commit_ops.py:384/385/421,
    # hosted_api.py:13476): the following relationship MATCH re-uses the
    # pre-bound anchor `o` and never names the predicate variable `s`, so it
    # BINDS (measured on 6.0.1: UNBARRED returns [['A','B']] for $src='A').
    # Without this the rail is red on main and cannot land.
    prebound_reuse = (
        'Q = ("MATCH (o:Point {is_operator:true, op_type:\'IMPL\'})-'
        '[:IMPL {idx:0}]->(s) WHERE s.id = $src "'
        ' "MATCH (o)-[:IMPL {idx:1}]->(d) WHERE d.id = $dst RETURN s.id")'
    )
    assert unbarred(prebound_reuse) == [], prebound_reuse
    # …but a relationship MATCH naming NOTHING pre-bound still leaks (check (b)).
    no_prebound = 'Q = ("MATCH (s:Point {id:1})-[:r]->(o) WHERE s.id = $src " "MATCH (a)-[:q]->(b) RETURN s.id")'
    assert unbarred(no_prebound) == ["1 (s)"], no_prebound
    # The bracket-less undirected form leaks too, and a bracket-requiring trigger
    # missed it (third-cycle P1): measured 6 rows, n.id None for a 1-id request.
    bare_dash = 'Q = ("MATCH (n:Point) WHERE n.id IN $ids " "MATCH (o)--(p) RETURN n")'
    assert unbarred(bare_dash) == ["1 (n)"], bare_dash
    # `NOT(` with no space, and `IS [NOT] NULL` (no right-hand side): both were
    # invisible to `pred` and both leak on 6.0.1.
    not_nospace = 'Q = ("MATCH (n:Point) WHERE NOT(n.id IN $v) " "MATCH (n)-[r]-(o) RETURN n")'
    assert unbarred(not_nospace) == ["1 (n)"], not_nospace
    is_null = 'Q = ("MATCH (n:Point) WHERE n.status IS NULL " "MATCH (n)-[r]-(o) RETURN n")'
    assert unbarred(is_null) == ["1 (n)"], is_null
    # …unless a LATER clause re-binds it — the first-clause-only walk called this
    # SAFE while it leaks 4 distinct ids for a 1-id request on 6.0.1.
    second_clause = 'Q = ("MATCH (n:Thing) WHERE n.id IN $ids " "MATCH (o:Thing) MATCH (n)-[r:LINK]-(p:Thing) RETURN n.id")'
    assert unbarred(second_clause) == ["1 (n)"], second_clause
    # A NEGATED predicate is the same drop and was invisible (4 live sites:
    # sdk.py:20567, hosted_api.py:8042, navigation.py:63,65).
    negated = 'Q = ("MATCH (n:Point) WHERE NOT n.id IN $v " "MATCH (n)-[r]-(o) RETURN n")'
    assert unbarred(negated) == ["1 (n)"], negated
    # An f-string RHS is a runtime fragment (\x00), not a reason to miss it.
    fstring = 'Q = f"MATCH (n:Point) WHERE n.id IN {ids_} MATCH (n)-[r]-(o) RETURN n"'
    assert unbarred(fstring) == ["1 (n)"], fstring
    # The widened operator set (review P2): every one of these was MEASURED
    # leaking on the canonical engine and every one was invisible before.
    for op in ("<>", "!=", ">=", ">", "<", "CONTAINS", "STARTS WITH"):
        q = f'Q = ("MATCH (n:Point) WHERE n.id {op} $v " "MATCH (n)-[r]-(o) RETURN n")'
        assert unbarred(q) == ["1 (n)"], (op, q)
    # …and non-parameter right-hand sides, also measured leaking.
    for rhs in ("true", "42", "toLower($v)", "['p0']", "'p0'"):
        q = f'Q = ("MATCH (n:Point) WHERE n.id = {rhs} " "MATCH (n)-[r]-(o) RETURN n")'
        assert unbarred(q) == ["1 (n)"], (rhs, q)
    # Coverage the AST rewrite initially LOST against the line-scan it replaced
    # (review P2): a query assembled from string fragments in a list/tuple/dict.
    joined = 'Q = "\\n".join(["MATCH (n:Point) WHERE n.id IN $ids ", "MATCH (n)-[r]-(o) RETURN n"])'
    assert unbarred(joined) == ["1 (n)"], joined
    # The defect is property-GENERAL, not `.id`-only (measured on 6.0.0 for
    # `.pointKind` and `.status` too) — so the rail must not be either.
    other_prop = 'Q = ("MATCH (n:Point) WHERE n.pointKind IN $k " "MATCH (n)-[r]-(o) RETURN n")'
    assert unbarred(other_prop) == ["1 (n)"], other_prop
    # Tolerances: parentheses, uppercase variable, lowercase keyword.
    tolerant = 'Q = ("MATCH (N:Point) WHERE (N.id IN $ids) match (N)-[r]-(o) RETURN N")'
    assert unbarred(tolerant) == ["1 (N)"], tolerant
    # Backtick-quoted variable.
    backtick = 'Q = ("MATCH (`n`:Point) WHERE `n`.id IN $ids MATCH (`n`)-[r]-(o) RETURN `n`")'
    assert unbarred(backtick) == ["1 (n)"], backtick
    # The drop is RHS-AGNOSTIC (measured on 6.0.0 with a LITERAL right-hand side:
    # unbarred 10000 rows / 2642 foreign ids, barred 8 rows / 1 id), so a rail
    # that only saw `$param` was blind to the natural literal form.
    literal_rhs = 'Q = ("MATCH (n:Point) WHERE n.id = \'p0\' " "MATCH (n)-[r]-(o) RETURN n")'
    assert unbarred(literal_rhs) == ["1 (n)"], literal_rhs
    list_rhs = 'Q = ("MATCH (n:Point) WHERE n.id IN [\'p0\'] " "MATCH (n)-[r]-(o) RETURN n")'
    assert unbarred(list_rhs) == ["1 (n)"], list_rhs
    # No whitespace between the keyword and the predicate (also measured leaking).
    no_space = 'Q = ("MATCH (n:Point) WHERE(n.id = \'p0\') " "MATCH (n)-[r]-(o) RETURN n")'
    assert unbarred(no_space) == ["1 (n)"], no_space

    offenders: list[str] = []
    scanned = 0
    unparseable: list[str] = []
    paths = sorted(root.rglob("*.py"))
    for path in paths:
        try:
            hits = unbarred(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            # Counted, not skipped: a file the scan cannot read is COVERAGE THIS
            # RAIL DOES NOT HAVE, and silently excluding it let the count stay
            # green while the file went unscanned (review P3).
            unparseable.append(path.name)
            continue
        scanned += 1
        offenders.extend(f"{path.name}:{hit}" for hit in hits)
    # A scan that reads nothing passes vacuously. The floor is measured, not
    # nominal: 190 files under tortoise/ at the time of writing, so a rename or
    # relocation that drops the package out from under the scan reds this instead
    # of silently disarming the only rail for this defect (review P3). Neither
    # this floor nor the margin below can catch a SINGLE small subpackage
    # disappearing (the largest is 6 files against 9 files of headroom); they
    # catch a collapse of 10+ files. Stated plainly rather than overclaimed.
    assert scanned >= 180, (
        f"the shape scan read only {scanned} files under {root} — it is not "
        "looking at the package, so its silence means nothing"
    )
    # The subpackages hold 24 of the 190 files; <15 means 10+ subpackage files
    # went missing or the scan stopped descending. Deliberately coarse: naming the
    # directories would break on every legitimate package reshuffle.
    non_root = sum(1 for p in paths if p.parent != root)
    assert non_root >= 15, (
        f"the shape scan reached only {non_root} files outside {root} — "
        "subpackage coverage has collapsed (#6976)"
    )
    assert not unparseable, (
        "the shape scan could not parse " + ", ".join(unparseable)
        + " — those files are unscanned, so this rail's silence does not cover "
        "them (#6976)"
    )
    assert not offenders, (
        "these queries filter a variable and then re-bind it in a following "
        "clause with no load-bearing `WITH <var>` in between. On the canonical "
        "6.0.1 instance the predicate IS dropped and the query returns OTHER "
        "points' rows (#6976) — add the `WITH <var>` between the predicate and "
        "the next clause: " + ", ".join(offenders)
    )
