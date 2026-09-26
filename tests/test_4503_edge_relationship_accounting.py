"""#4503 — the **edge** class is outside every accounting surface.

Everything that grows Tortoise's RAM is measured in **nodes**: the cap counts
``Point``/``Object``/``Subject`` nodes, the meter counts
``write_ops`` + ``nodes_written``, and the declared cost basis is
``product/pricing.json`` → ``billing.cost_basis.bytes_per_node``. No accounting
surface has a relationship term. On top of that, EP persists four belief
properties **on the relationship** (``r.msg_alpha``, ``r.msg_beta``,
``r.back_msg_alpha``, ``r.back_msg_beta``), so the state that grows with
relationship **density** rather than node **volume** is uncapped, unmetered and
unpriced.

This module pins that as **behaviour**, not as a text grep. Three surfaces are
measured, each against a real graph:

  (a) **the cap is invariant to edge COUNT and edge STATE** —
      ``count_org_usage(org, "points")`` returns the SAME number at three
      reads: nodes only; after the relationships exist; and after EP writes four
      properties onto every edge (while the edges demonstrably now carry them).
      Both growth axes are read because they fail differently — a cap term keyed
      on edge count would pass a two-read state-only pin. This is the shape that
      would go RED the day an edge term entered the cap, which is precisely the
      change this measurement forecasts.
  (b) **the EP edge flush journals nothing** — the node posterior flush emits
      ``ConfidenceChanged`` (the #2884 fix) and the message-cache flushes that
      follow it emit nothing at all, so no journal record can carry an edge
      slot. The first half of the assertion is the GUARD: without it a broken
      emitter would make the second half pass vacuously.
  (c) **a rebuild loses the edge messages while the node belief survives** —
      ⚠️ a CHARACTERISATION PIN. It asserts the CURRENT (defective) behaviour of
      a gap filed as #5380 and must be INVERTED by that fix: after it lands,
      ``r.msg_alpha`` is expected to survive ``rebuild_all`` and this test
      should be updated to assert that. It is here because the gap is otherwise
      invisible — the node half was fixed by #2884 and the edge half looks
      "restored" only because nothing checks it.

Plus the census tool's own arithmetic (``tools/edge_census.py``) and the
``--org`` wiring of the cap's count to the graph actually censused — both in the
helper and through ``edge_census.main()`` — which is what makes the measurement
re-runnable *and* honest.

Lane: the graph fixtures are EMBEDDED (explicit ``db_path``), so this file runs
in both the default docker lane and the ``TORTOISE_TEST_CARVE_OUT=1`` lane. No
``namespace=`` literal is used — ``count_org_usage`` accepts the SDK directly,
so no ``tests/test_markers.py`` ``ROUTED_NAMESPACES`` entry is needed.

Run::

    TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \\
        uv run pytest tests/test_4503_edge_relationship_accounting.py -v
"""
from __future__ import annotations

import itertools
import json

import pytest

import tools.edge_census as edge_census
from tools.edge_census import (
    EP_EDGE_SLOTS,
    CensusError,
    Stage,
    _org_capped_points,
    accounting_view,
    assert_subset_ratio_available,
    node_census,
    probe_marginals,
    relationship_census,
)
from tortoise.ep import TortoiseEP
from tortoise.quota import count_org_usage
from tortoise.sdk import TortoiseSDK

#: An org id is only used by ``count_org_usage`` when ``sdk`` is None (it then
#: derives the namespace). Every call here passes ``sdk=``, so this label is
#: inert — it exists only to satisfy the signature.
_ORG = "org_edge_accounting_4503"


@pytest.fixture
def sdk(tmp_path):
    """An embedded SDK on its OWN db file.

    An explicit ``db_path`` wins over ``TORTOISE_DB_URI`` in
    ``TortoiseSDK.__init__``, so this is embedded in both lanes without
    mutating the environment (which a session-scoped fixture might not see).
    """
    sdk = TortoiseSDK(str(tmp_path / "edge_accounting_4503.db"))
    yield sdk
    sdk.close()


@pytest.fixture
def journaled_sdk(tmp_path):
    """The same, with the JSONL journal wired (the #2884 harness shape)."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "edge_accounting_4503_journal.db"),
                      event_log_path=str(events / "events.jsonl"))
    yield sdk, events
    sdk.close()


# ── helpers ────────────────────────────────────────────────────────────────

def _direct_impl_edge(sdk: TortoiseSDK, a: str, b: str) -> None:
    """An operator-less direct IMPL edge — the #888 W5 shape whose message
    state lives ON the edge (``r.msg_alpha``/``r.back_msg_*``)."""
    sdk._get_proj().g.query(
        "MATCH (a:Point {id:$a}), (b:Point {id:$b}) "
        "CREATE (a)-[:IMPL {direction: 'bidirectional'}]->(b)",
        params={"a": a, "b": b},
    )


def _claims(sdk: TortoiseSDK, n: int = 3) -> list[str]:
    return [
        sdk.create_point("statement", f"claim {i}", dedup=False,
                         status="live")["id"]
        for i in range(n)
    ]


def _run_ep(sdk: TortoiseSDK, seeds: list[str],
            emit=None) -> None:
    """Run a real EP pass, batched (the path ``_flush_cache`` serves).

    The batched path is what makes the assertion meaningful: it is
    ``_flush_cache`` that writes the message caches, and it is there that the
    emit asymmetry lives. ``emit`` is what the SDK passes in production.
    """
    proj = sdk._get_proj()
    ev_rows = proj.g.query(
        "MATCH (n:Point) WHERE n.baseline_set = true "
        "AND n.ep_alpha IS NOT NULL RETURN n.id, n.ep_alpha, n.ep_beta",
    ).result_set
    evidence = {r[0]: (r[1], r[2]) for r in ev_rows} if ev_rows else {}
    TortoiseEP(proj, damping=0.5, n_quad=8, max_iter=50, tol=1e-3,
               evidence=evidence, emit=emit).run(seeds, max_hops=2)


def _seed_evidence(sdk: TortoiseSDK, pid: str, a: float, b: float) -> None:
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.ep_alpha=$a, n.ep_beta=$b, "
        "n.baseline_set=true",
        params={"id": pid, "a": a, "b": b},
    )


def _edge_slots(sdk: TortoiseSDK, a: str, b: str) -> dict:
    """The four slots read straight off the edge."""
    rows = sdk._get_proj().g.query(
        "MATCH (x:Point {id:$a})-[r:IMPL]->(y:Point {id:$b}) "
        "RETURN " + ", ".join(f"r.{slot}" for slot in EP_EDGE_SLOTS),
        params={"a": a, "b": b},
    ).result_set
    assert rows, f"no IMPL edge {a} -> {b}"
    return dict(zip(EP_EDGE_SLOTS, rows[0], strict=True))


def _build_edge_graph(sdk: TortoiseSDK, n: int = 3) -> list[str]:
    ids = _claims(sdk, n)
    for a, b in itertools.pairwise(ids):
        _direct_impl_edge(sdk, a, b)
    return ids


# ── (a) the cap is invariant to edge count AND edge state ──────────────────
# pin — GREEN before any change by construction: no production code is touched
# by this lane. It goes RED the day an edge term enters the cap predicate, which
# is the change this measurement exists to inform (an owner decision, not ours).
# Both axes are read (count, then state) because they fail differently.

def test_cap_count_is_invariant_to_edge_state_growth(sdk):
    """The cap's `points` count is invariant to BOTH axes the edge class grows
    on: the relationship COUNT (read 2) and the edge STATE (read 3). Both are
    read because they fail differently — a cap term keyed on edge count would
    leave read 1 == read 2, while a term keyed on edge state would leave read 2
    == read 3.
    """
    ids = _claims(sdk, 3)

    # Read 1: nodes only, no relationships yet.
    nodes_only = count_org_usage(_ORG, "points", sdk=sdk)

    for a, b in itertools.pairwise(ids):
        _direct_impl_edge(sdk, a, b)
    for a, b in itertools.pairwise(ids):
        assert _edge_slots(sdk, a, b) == dict.fromkeys(EP_EDGE_SLOTS), \
            "precondition: the fixture edges start with no message state"

    # Read 2: the relationships now EXIST (edge count grew), still no state.
    edges_present = count_org_usage(_ORG, "points", sdk=sdk)

    _seed_evidence(sdk, ids[-1], 8.0, 2.0)
    _run_ep(sdk, ids)

    # The edge state demonstrably GREW — all four slots are now resident.
    grown = [_edge_slots(sdk, a, b) for a, b in itertools.pairwise(ids)]
    assert all(all(v is not None for v in slots.values()) for slots in grown), (
        f"the EP run did not write the edge message state, so this test would "
        f"pass vacuously: {grown}")

    # Read 3: edge STATE grew on top of the same edge count.
    state_grown = count_org_usage(_ORG, "points", sdk=sdk)

    for label, value in (("the relationships came into existence", edges_present),
                         ("the edge state grew", state_grown)):
        assert value == nodes_only, (
            f"the cap's `points` count changed after {label} "
            f"({nodes_only} -> {value}). If that is deliberate, the edge class "
            f"has entered the quota model (owner territory — see #4503) and "
            f"this pin must be updated rather than deleted.")


# ── (b) the EP edge flush journals nothing ─────────────────────────────────

def test_ep_edge_flush_journals_no_edge_slot(journaled_sdk):
    sdk, events = journaled_sdk
    ids = _build_edge_graph(sdk)
    _seed_evidence(sdk, ids[-1], 8.0, 2.0)

    _run_ep(sdk, ids, emit=sdk._emit_event)

    # Edges really did receive their message state in this run.
    for a, b in itertools.pairwise(ids):
        assert all(v is not None for v in _edge_slots(sdk, a, b).values()), \
            "the EP run wrote no edge message state — the assertion below " \
            "would be vacuous"

    path = events / "events.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()
               if line.strip()]

    # GUARD: the emitter is wired and the NODE flush committed. Without this,
    # a silently-dead emitter would make the slot assertion pass for the wrong
    # reason — the failure mode this test exists to exclude.
    node_records = [r for r in records if r.get("type") == "ConfidenceChanged"]
    assert node_records, (
        "no ConfidenceChanged record at all — the node posterior flush emits "
        "one (#2884 fixed that half); a run without it means the emitter, not "
        "the edge flush, is what is being measured")
    assert any(
        r.get("posterior_alpha") is not None for r in node_records
    ), f"the node flush committed nothing: {node_records}"

    # THE FINDING: no record carries any edge message slot.
    for record in records:
        for slot in EP_EDGE_SLOTS:
            assert slot not in record, (
                f"an edge message slot reached the journal: {slot!r} in "
                f"{record.get('type')!r}. If #5380 landed, this pin is "
                f"superseded — update it to assert the slot IS journaled "
                f"rather than deleting it.")


# ── (c) rebuild loses the edge messages, keeps the node belief ─────────────
# ⚠️ CHARACTERISATION PIN — asserts CURRENT defective behaviour. #5380 is the
# issue that must invert it. Kept because the gap is invisible otherwise: the
# node half was fixed by #2884, so a rebuild looks "restored" and nothing
# checks the edge half.

def test_rebuild_all_loses_edge_messages_while_node_belief_survives(
        journaled_sdk):
    sdk, events = journaled_sdk
    ids = _claims(sdk, 3)
    # create_operator JOURNALS the operator + its edges, so a rebuild recreates
    # the EDGE — which is what makes the assertion about its PROPERTY meaningful
    # rather than an assertion that a missing edge is missing.
    for a, b in itertools.pairwise(ids):
        sdk.create_operator("IMPL", a, [b])

    _seed_evidence(sdk, ids[-1], 8.0, 2.0)
    _run_ep(sdk, ids, emit=sdk._emit_event)

    proj = sdk._get_proj()
    edges_before = proj.g.query(
        "MATCH (:Point)-[r:IMPL]->(:Point) "
        "WHERE r.msg_alpha IS NOT NULL RETURN count(r)").result_set[0][0]
    node_before = proj.g.query(
        "MATCH (n:Point) WHERE n.posterior_alpha IS NOT NULL "
        "RETURN count(n)").result_set[0][0]
    assert edges_before > 0, "precondition: EP wrote no edge message state"
    assert node_before > 0, "precondition: EP wrote no node belief state"

    proj.rebuild_all(str(events))

    edges_after = proj.g.query(
        "MATCH (:Point)-[r:IMPL]->(:Point) RETURN count(r)").result_set[0][0]
    msg_after = proj.g.query(
        "MATCH (:Point)-[r:IMPL]->(:Point) "
        "WHERE r.msg_alpha IS NOT NULL RETURN count(r)").result_set[0][0]
    node_after = proj.g.query(
        "MATCH (n:Point) WHERE n.posterior_alpha IS NOT NULL "
        "RETURN count(n)").result_set[0][0]

    # NOTE: `edges_before` counts only the DRESSED edges (msg_alpha present)
    # while `edges_after` counts every IMPL edge, so this equality is a proxy.
    # It is exact here only because the fixture dresses all of them
    # (verified: edges_before == the total IMPL edge count).
    # The edge is RESTORED (the journal carries the operator) …
    assert edges_after == edges_before, (
        f"the IMPL edges did not survive the rebuild ({edges_before} -> "
        f"{edges_after}); then the property assertion below would be about a "
        f"missing edge, not lost state")
    # … and the NODE belief survives (the #2884 fix) …
    assert node_after == node_before, (
        f"node belief state was lost across rebuild_all ({node_before} -> "
        f"{node_after}) — that is a REGRESSION of #2884, not this finding")
    # … but its message state does not. #5380 inverts this assertion.
    assert msg_after == 0, (
        f"{msg_after} edges kept r.msg_alpha across rebuild_all — if #5380 "
        f"landed, invert this assertion (the state is now durable); if it did "
        f"not, something else started journaling edge messages and the census "
        f"in #4503 needs re-measuring")


# ── the census tool's own arithmetic (the re-runnable receipt) ─────────────

def test_relationship_census_counts_types_slots_and_ep_bearing(sdk):
    proj = sdk._get_proj()
    proj.g.query("UNWIND range(1,5) AS i CREATE (:Point {id:'p'+toString(i)})")
    proj.g.query(
        "MATCH (a:Point {id:'p1'}), (b:Point {id:'p2'}) "
        "CREATE (a)-[:IMPL {direction:'bidirectional'}]->(b)")
    proj.g.query(
        "MATCH (a:Point {id:'p2'}), (b:Point {id:'p3'}) CREATE (a)-[:NAND]->(b)")
    # one edge with all four slots, one with a single slot
    proj.g.query(
        "MATCH ()-[r:IMPL]->() SET r.msg_alpha=0.5, r.msg_beta=0.5, "
        "r.back_msg_alpha=0.5, r.back_msg_beta=0.5")
    proj.g.query("MATCH ()-[r:NAND]->() SET r.msg_alpha=0.5")

    census = relationship_census(proj.g)

    assert census["total"] == 2
    assert census["by_type"] == {"IMPL": 1, "NAND": 1}
    assert census["by_slot"] == {
        "msg_alpha": 2, "msg_beta": 1,
        "back_msg_alpha": 1, "back_msg_beta": 1,
    }
    # both edges carry >= 1 slot; exactly one carries all four. Reported
    # separately because a half-written edge is a different object.
    assert census["ep_bearing"] == 2
    assert census["all_four_slots"] == 1


def test_relationship_census_reports_zero_on_an_empty_graph(sdk):
    census = relationship_census(sdk._get_proj().g)
    assert census["total"] == 0
    assert census["by_type"] == {}
    assert census["by_slot"] == dict.fromkeys(EP_EDGE_SLOTS, 0)
    assert census["ep_bearing"] == 0
    assert census["all_four_slots"] == 0


def test_node_census_counts_nodes_but_never_the_cap_denominator(sdk):
    ids = _build_edge_graph(sdk)
    nodes = node_census(sdk._get_proj().g)
    assert nodes["point_label"] == len(ids)
    assert nodes["resident"] >= len(ids)
    # The cap's own predicate is deliberately NOT re-implemented here — a
    # second implementation is the drift this finding is about.
    assert "capped_points" not in nodes


def test_accounting_view_never_guesses_the_cap_denominator():
    view = accounting_view(
        edges={"total": 30}, nodes={"resident": 100, "point_label": 40})
    assert view["nodes"]["capped_points"] is None
    assert "resident_over_capped" not in view["ratios"]
    assert view["ratios"]["edges_over_resident"] == 0.3
    with pytest.raises(CensusError):
        assert_subset_ratio_available(view)

    with_cap = accounting_view(
        edges={"total": 30}, nodes={"resident": 100, "point_label": 40},
        capped_points=25)
    assert with_cap["ratios"]["resident_over_capped"] == 4.0
    assert_subset_ratio_available(with_cap)  # does not raise

    # A cap count of 0 is a legitimately unratio-able graph, NOT a missing read.
    zero_cap = accounting_view(
        edges={"total": 0}, nodes={"resident": 0, "point_label": 0},
        capped_points=0)
    assert "resident_over_capped" not in zero_cap["ratios"]
    assert_subset_ratio_available(zero_cap)  # does not raise


def test_probe_marginals_derives_per_element_bytes():
    rows = probe_marginals([
        Stage("baseline", 2_000_000, 0),
        Stage("bare node", 2_500_000, 5_000),
        Stage("bare edge", 2_600_000, 1_000),
    ])
    assert rows[0]["delta"] is None and rows[0]["marginal_bytes"] is None
    assert rows[1]["delta"] == 500_000
    assert rows[1]["marginal_bytes"] == 100.0
    assert rows[2]["delta"] == 100_000
    assert rows[2]["marginal_bytes"] == 100.0


def test_org_capped_points_reads_the_census_graph_not_a_neighbour(
        sdk, monkeypatch, tmp_path):
    """``--org`` must count the cap in the graph the CENSUS read.

    Regression guard for a P1 found in review, pre-merge: the first version took
    the graph ``uri`` as its second parameter and **ignored it**, calling
    ``count_org_usage(org, "points")`` with ``sdk=None``. That makes the callee
    build its OWN SDK from the environment (``quota.py``: ``if sdk is None: sdk =
    _make_sdk(namespace=org_id)``) — ``TORTOISE_DB_PATH`` in embedded mode, the
    graph ``org_<org>`` in URI mode — so the cap count came from a DIFFERENT
    database and a wrong-database ``0`` was indistinguishable from a legitimate
    empty-org ``0`` (``assert_subset_ratio_available`` keys on ``None``).

    Pointing the environment at a second, EMPTY database is what makes the two
    paths distinguishable: with the count taken from the census's own ``sdk``,
    it must still be this graph's count.
    """
    ids = _claims(sdk, 3)
    assert count_org_usage(_ORG, "points", sdk=sdk) == len(ids)

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("TORTOISE_DB_PATH", str(elsewhere / "other.db"))
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)

    assert _org_capped_points(_ORG, sdk) == len(ids), (
        "the cap count was read from a different graph than the census — "
        "passing sdk=None lets count_org_usage build its own SDK from the "
        "environment (TORTOISE_DB_PATH / the org_<org> URI graph)")


def test_census_cli_passes_its_own_graph_to_the_cap_count(tmp_path, monkeypatch,
                                                         capsys):
    """End-to-end: the `--org` count in the CLI comes from the censused graph.

    This is the wiring the P1 was actually in — ``main()`` built a graph handle
    and then let the cap count resolve a *second*, unrelated one. Covers the
    hand-off, which the helper-level guard above cannot.
    """
    db = tmp_path / "cli_graph.db"
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "NEIGHBOUR.db"))

    seeded = TortoiseSDK(str(db))
    for i in range(3):
        seeded.create_point("statement", f"claim {i}", dedup=False,
                            status="live")
    seeded.close()

    rc = edge_census.main(["census", "--embedded", str(db), "--org", _ORG,
                           "--json"])
    assert rc == 0
    view = json.loads(capsys.readouterr().out)
    assert view["nodes"]["point_label"] == 3
    assert view["nodes"]["capped_points"] == 3, (
        "the CLI read the cap count from a different graph than the census")


def test_probe_marginals_refuses_to_report_a_false_zero():
    # A stage that added bytes but declares no elements must RAISE: reporting
    # 0 would be a false measurement, and silence is how a probe lies.
    with pytest.raises(CensusError):
        probe_marginals([
            Stage("baseline", 1_000, 0),
            Stage("unattributed growth", 1_500, 0),
        ])
    # A non-monotonic fresh-instance reading invalidates the whole probe.
    with pytest.raises(CensusError):
        probe_marginals([
            Stage("baseline", 1_000, 0),
            Stage("went down", 900, 100),
        ])
    with pytest.raises(CensusError):
        probe_marginals([])
