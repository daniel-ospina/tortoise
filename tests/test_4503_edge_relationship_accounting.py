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

Lane: the graph fixtures request an EMBEDDED db via an explicit ``db_path``,
which is exact in the ``TORTOISE_TEST_CARVE_OUT=1`` lane. This file is **not**
in ``tests/_embedded.py``'s ``TEST_NO_REDIRECT_STEMS``, so in the DOCKER lane
(``TORTOISE_DB_URI`` set + ``TORTOISE_TEST_MODE=1``) the projection's test
redirect re-points those two fixtures at the server graph — the tests still
pass there, but they exercise server semantics rather than embedded. The two
``--org`` guards deliberately ``delenv`` the URI so they stay embedded in both
lanes (declared in ``tests/test_uri_env_mutations_declared.py``). No
``namespace=`` literal is used — ``count_org_usage`` accepts the SDK directly,
so no ``tests/test_markers.py`` ``ROUTED_NAMESPACES`` entry is needed.

Run::

    TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \\
        uv run pytest tests/test_4503_edge_relationship_accounting.py -v
"""
from __future__ import annotations

import itertools
import json
import os

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
    run_probe,
)
from tortoise.ep import TortoiseEP
from tortoise.quota import QuotaCheckError, count_org_usage
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
    on: the relationship COUNT (read 1 -> read 2) and the edge STATE (read 2 ->
    read 3). Both are read because they fail differently — a cap term keyed on
    edge count breaks read 1 == read 2, while a term keyed on edge state
    breaks read 2 == read 3.
    """
    ids = _claims(sdk, 3)

    # Read 1: nodes only, no relationships yet.
    nodes_only = count_org_usage(_ORG, "points", sdk=sdk)
    # ANCHOR the base count. Without it a `count_org_usage` that returned any
    # constant would satisfy all three equalities and this pin would be green
    # against a broken cap — indistinguishable from a correctly-ignoring one.
    assert nodes_only == len(ids), (
        f"the cap's base `points` count for {len(ids)} non-episodic Points "
        f"read {nodes_only} — the pin below compares three reads to this "
        f"number, so a wrong base makes every comparison meaningless")

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

    # THE FINDING: no record carries any edge message slot — in its TOP-LEVEL
    # keys (the `**extra` emit style, sdk._emit_event's `event.update(extra)`)
    # NOR in a nested point snapshot (the other shape the same emitter can
    # write). Checking only the top level would let a snapshot-style journal
    # entry for #5380 pass this pin vacuously — and the pin's whole job is to
    # be the thing that fails when #5380 lands.
    for record in records:
        scopes = [record, record.get("point") or {}]
        for slot in EP_EDGE_SLOTS:
            for scope in scopes:
                assert slot not in scope, (
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
    total_before = proj.g.query(
        "MATCH (:Point)-[r:IMPL]->(:Point) RETURN count(r)").result_set[0][0]
    edges_before = proj.g.query(
        "MATCH (:Point)-[r:IMPL]->(:Point) "
        "WHERE r.msg_alpha IS NOT NULL RETURN count(r)").result_set[0][0]
    node_before = proj.g.query(
        "MATCH (n:Point) WHERE n.posterior_alpha IS NOT NULL "
        "RETURN count(n)").result_set[0][0]
    assert edges_before > 0, "precondition: EP wrote no edge message state"
    assert node_before > 0, "precondition: EP wrote no node belief state"
    # PIN the proxy. `edges_before` counts DRESSED edges and `edges_after`
    # counts every IMPL edge, so comparing them is only sound while the fixture
    # dresses all of them. Asserting it here is what stops a future
    # under-dressed fixture (EP reaching an edge outside max_hops) from making
    # `edges_after == edges_before` true by coincidence — which would silently
    # turn the `msg_after == 0` assertion below into a statement about nothing.
    assert edges_before == total_before, (
        f"the fixture left {total_before - edges_before} of {total_before} "
        f"IMPL edges undressed, so the restored-edge equality below would be "
        f"a proxy that can pass while an edge is genuinely lost")

    proj.rebuild_all(str(events))

    edges_after = proj.g.query(
        "MATCH (:Point)-[r:IMPL]->(:Point) RETURN count(r)").result_set[0][0]
    msg_after = proj.g.query(
        "MATCH (:Point)-[r:IMPL]->(:Point) "
        "WHERE r.msg_alpha IS NOT NULL RETURN count(r)").result_set[0][0]
    node_after = proj.g.query(
        "MATCH (n:Point) WHERE n.posterior_alpha IS NOT NULL "
        "RETURN count(n)").result_set[0][0]

    # The edge is RESTORED (the journal carries the operator) — compared
    # against the TOTAL, which the pin above proved equals the dressed count …
    assert edges_after == total_before, (
        f"the IMPL edges did not survive the rebuild ({total_before} -> "
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
    graph = sdk._get_proj().g
    nodes = node_census(graph)
    assert nodes["point_label"] == len(ids)
    # EXACT, not `>=`: a loose bound is satisfied by `point_label` itself (and
    # by any undercount), so it could not fail on a broken `resident` read —
    # the one count this test exists to pin. `resident` is every node, so it is
    # the internal `:Meta` node plus the Points (and their Objects/Subjects).
    expected_resident = graph.query(
        "MATCH (n) WHERE NOT n:Point RETURN count(n)").result_set[0][0] \
        + len(ids)
    assert nodes["resident"] == expected_resident, (
        f"`resident` read {nodes['resident']}, expected every non-Point node "
        f"plus the {len(ids)} Points = {expected_resident}")
    assert nodes["resident"] > nodes["point_label"], (
        "resident must count MORE than :Point — otherwise the two "
        "denominators are the same number and the census cannot show the "
        "disagreement it exists to expose")
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


# ── the probe's plumbing, and the tool's fail-loud contract ────────────────
# The `probe` subcommand is the source of every headline per-edge figure, and
# its stage construction / cleanup lives in code the arithmetic tests never
# reach. These fakes drive it without docker.

class _Res:
    """Minimal FalkorDB result: ``.result_set`` is all ``_scalar`` reads."""

    def __init__(self, rows: list) -> None:
        self.result_set = rows


class _FakeGraph:
    """A graph that returns queued results and records every query."""

    def __init__(self, results: list) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, dict | None]] = []

    def query(self, cypher: str, params: dict | None = None) -> _Res:
        self.calls.append((cypher, params))
        return _Res(self._results.pop(0))


class _FakeProc:
    def __init__(self, returncode: int = 0, stdout: str = "",
                 stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_relationship_census_binds_type_names_instead_of_interpolating():
    """⛔ The injection regression guard.

    Relationship-type names are READ FROM THE GRAPH. Interpolating one into
    `MATCH ()-[r:TYPE]->()` lets a crafted name close the pattern and inject
    clauses: review demonstrated a type stored as
    ``IMPL]->() DELETE r WITH 1 AS x MATCH ()-[r`` producing a query that
    DELETED the graph's relationships and returned the deletion count as a
    "count". The tool must bind the name, never splice it.
    """
    evil = "IMPL]->() DELETE r WITH 1 AS x MATCH ()-[r"
    graph = _FakeGraph([
        [[2]],            # total
        [["OK"], [evil]],  # CALL db.relationshipTypes()
        [[1]],            # by_type['IMPL]->() ...'] (sorted first: 'I' < 'O')
        [[1]],            # by_type['OK']
        [[0]], [[0]], [[0]], [[0]],   # by_slot x4
        [[0]],            # ep_bearing
        [[0]],            # all_four_slots
    ])

    census = relationship_census(graph)

    for cypher, _params in graph.calls:
        assert "DELETE" not in cypher, (
            f"a graph-sourced type name reached the query TEXT: {cypher!r}")
        assert evil not in cypher, (
            f"a graph-sourced type name was interpolated into a Cypher "
            f"pattern: {cypher!r}")
    bound = [p for _c, p in graph.calls if p]
    assert {"rtype": evil} in bound, (
        f"the type name was never bound as a parameter — bound params were "
        f"{bound!r}")
    assert census["by_type"] == {evil: 1, "OK": 1}


def test_relationship_census_refuses_a_breakdown_that_does_not_reconcile():
    # 3 relationships, but the per-type counts only account for 2 — an empty or
    # partial `by_type` beside a non-zero total reads to a human as "there are
    # relationships and none of any type". Fail closed instead.
    graph = _FakeGraph([
        [[3]],            # total
        [["IMPL"], ["NAND"]],
        [[1]],            # IMPL
        [[1]],            # NAND  (1 + 1 != 3)
        [[0]], [[0]], [[0]], [[0]],
        [[0]],
        [[0]],
    ])
    with pytest.raises(CensusError, match="do not reconcile"):
        relationship_census(graph)


def test_scalar_refuses_every_unreadable_result_shape():
    # Empty result, and a result whose first row is empty.
    with pytest.raises(CensusError, match="no rows"):
        edge_census._scalar(_FakeGraph([[]]), "MATCH (n) RETURN count(n)")
    with pytest.raises(CensusError, match="no rows"):
        edge_census._scalar(_FakeGraph([[[]]]), "MATCH (n) RETURN count(n)")
    # A non-integer (including a bool, which IS an int in Python) is not a
    # count, and must not be coerced or printed as one.
    with pytest.raises(CensusError, match="not return an integer"):
        edge_census._scalar(_FakeGraph([["2000"]]), "MATCH (n) RETURN count(n)")
    with pytest.raises(CensusError, match="not return an integer"):
        edge_census._scalar(_FakeGraph([[[True]]]), "MATCH (n) RETURN count(n)")

    class _Raising:
        def query(self, cypher, params=None):
            raise RuntimeError("connection reset")

    with pytest.raises(CensusError, match="query failed"):
        edge_census._scalar(_Raising(), "MATCH (n) RETURN count(n)")


def test_relationship_types_refuses_an_unreadable_type_listing():
    class _NoResultSet:
        def query(self, cypher, params=None):
            return object()          # no `.result_set` at all

    with pytest.raises(CensusError, match="no result set"):
        edge_census._relationship_types(_NoResultSet())


def test_container_query_treats_a_server_side_error_as_a_failure(monkeypatch):
    """`redis-cli` exits 0 on a server-side error — the exit code is not enough.

    Verified against a live FalkorDB: `GRAPH.QUERY g "THIS IS NOT CYPHER"` comes
    back exit 0 with `errMsg: ...` on STDOUT. Accepting that leaves the stage's
    `used_memory` delta attributed to a write that never happened.
    """
    monkeypatch.setattr(
        edge_census, "_docker",
        lambda *a, check=True: _FakeProc(
            0, "errMsg: Invalid input 'THIS IS NOT CYPHER'", ""))
    with pytest.raises(CensusError, match="probe query failed"):
        edge_census._container_query("c", "g", "THIS IS NOT CYPHER")

    monkeypatch.setattr(edge_census, "_docker",
                        lambda *a, check=True: _FakeProc(1, "", "boom"))
    with pytest.raises(CensusError, match="probe query failed"):
        edge_census._container_query("c", "g", "GOOD CYPHER")


def test_probe_marginals_refuses_a_declared_stage_that_did_not_grow():
    """The mirror of the false-zero guard: the one that is easy to miss.

    A stage that DECLARES elements while `used_memory` did not move at all did
    not run. `marginal_bytes: 0.0` there is not a small number — it is an
    absent measurement wearing a number's clothes, and adding N>=100 elements
    to a fresh instance always grows `used_memory`.
    """
    with pytest.raises(CensusError, match="did not move"):
        probe_marginals([
            Stage("baseline", 1_000, 0),
            Stage("declared but absent", 1_000, 5_000),
        ])


def test_run_probe_builds_the_stage_sequence_and_removes_the_container(
        monkeypatch):
    """The probe's plumbing: stage order, N, isolation flags, and teardown."""
    docker_calls: list[tuple] = []
    readings = iter([1_000, 6_000, 7_000, 8_000, 9_000, 10_000])

    def fake_docker(*args, check=True):
        docker_calls.append((args, check))
        if args[:2] == ("exec",) or args[0] == "exec":
            return _FakeProc(0, "PONG", "")
        return _FakeProc(0, "", "")

    queries: list[str] = []
    monkeypatch.setattr(edge_census, "docker_available", lambda: True)
    monkeypatch.setattr(edge_census, "_docker", fake_docker)
    monkeypatch.setattr(edge_census, "_container_used_memory",
                        lambda _c: next(readings))
    monkeypatch.setattr(edge_census, "_container_query",
                        lambda _c, _g, cypher: queries.append(cypher))

    result = run_probe(n=5_000)

    run_args = [a for a, _c in docker_calls if a[0] == "run"]
    assert len(run_args) == 1, f"expected exactly one container start: {run_args}"
    flags = run_args[0]
    assert "--rm" in flags, "the probe container must remove itself"
    assert "--network" in flags and "none" in flags, (
        "the probe must not share a network with the caller's containers — "
        "the documented local URI is itself a bridge container")
    assert any(str(f).startswith("edge-census-probe=") for f in flags), (
        "an orphaned probe container must be identifiable (SIGKILL leaves one)")
    # Teardown ran, on the same container that was started.
    name = flags[flags.index("--name") + 1]
    assert (("rm", "-f", name), False) in docker_calls, (
        f"the container was not removed: {docker_calls}")

    assert [s["label"] for s in result["raw_stages"]] == [
        "baseline (fresh instance)",
        "bare :Point node (label + id only)",
        "+ keyword-only Point props",
        "bare :IMPL edge (no properties)",
        "+ real IMPL attrs (direction,confidence,weight,label,batch_id)",
        "+ the four EP message slots",
    ]
    assert result["n_edges"] == 4_999, "edges is n-1, not n"
    assert all(q.startswith(("UNWIND", "MATCH")) for q in queries)


def test_run_probe_removes_the_container_when_a_stage_fails(monkeypatch):
    """Teardown must survive a failing stage — the container start is inside
    the try for the same reason: the failure paths are where orphans come from."""
    docker_calls: list[tuple] = []

    def fake_docker(*args, check=True):
        docker_calls.append((args, check))
        return _FakeProc(0, "PONG", "")

    def boom(_c, _g, _cypher):
        raise CensusError("stage refused")

    monkeypatch.setattr(edge_census, "docker_available", lambda: True)
    monkeypatch.setattr(edge_census, "_docker", fake_docker)
    monkeypatch.setattr(edge_census, "_container_used_memory", lambda _c: 1_000)
    monkeypatch.setattr(edge_census, "_container_query", boom)

    with pytest.raises(CensusError, match="stage refused"):
        run_probe(n=5_000)

    removed = [a for a, _c in docker_calls if a[0] == "rm"]
    assert removed, "a failing stage left the probe container behind"
    # Removal is best-effort on the way out — never fatal, never silent.
    assert all(check is False for a, check in docker_calls if a[0] == "rm")


def test_main_returns_exit_2_when_the_cap_count_cannot_be_read(
        monkeypatch, capsys):
    """A fail-closed cap read must reach the tool's exit-2 path, not a traceback."""
    class _FakeProj:
        g = _FakeGraph([
            [[0]],            # total
            [["IMPL"]],       # types (reconciled: 0 == 0)
            [[0]],            # by_type['IMPL']
            [[0]], [[0]], [[0]], [[0]],
            [[0]],
            [[0]],
            [[0]], [[0]],     # node_census: resident, point_label
        ])

    class _FakeSDK:
        def _get_proj(self):
            return _FakeProj()

        def close(self):
            pass

    monkeypatch.setattr(edge_census, "_open_sdk", lambda *a, **k: _FakeSDK())

    def unreadable(_org, _sdk):
        raise QuotaCheckError("registry unreachable")

    monkeypatch.setattr(edge_census, "_org_capped_points", unreadable)

    rc = edge_census.main(["census", "--embedded", "/nonexistent",
                           "--org", _ORG, "--json"])

    assert rc == 2
    assert "cap's own count could not be read" in capsys.readouterr().err


def test_open_sdk_restores_the_callers_uri(monkeypatch):
    """`main()` is called in-process, so an SDK open must not re-point the
    caller's environment for the next invocation."""
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@localhost:6379/before")

    class _FakeSDK:
        def __init__(self, *_a, **_k):
            pass

    import tortoise.sdk as sdk_mod
    monkeypatch.setattr(sdk_mod, "TortoiseSDK", _FakeSDK)

    edge_census._open_sdk("docker://:other@localhost:6379/after", None, None)

    assert os.environ["TORTOISE_DB_URI"] == \
        "docker://:pw@localhost:6379/before", (
        "the SDK open left the caller's TORTOISE_DB_URI mutated")


def test_uri_census_without_org_never_constructs_the_sdk(monkeypatch, capsys):
    """The read-only boundary the docstring promises, pinned.

    A **URI** census with no `--org` must not open the SDK at all — that is
    precisely what makes it DDL-free. Opening the SDK constructs a projection,
    which runs `_ensure_indexes()` (CREATE INDEX / DROP INDEX) against the
    target graph, so a regression here silently puts schema writes back on a
    graph the tool advertises itself as only reading.
    """
    graph = _FakeGraph([
        [[0]],            # total
        [["IMPL"]],       # types (reconciled: 0 == 0)
        [[0]],            # by_type['IMPL']
        [[0]], [[0]], [[0]], [[0]],   # by_slot x4
        [[0]],            # ep_bearing
        [[0]],            # all_four_slots
        [[0]], [[0]],     # node_census: resident, point_label
    ])
    monkeypatch.setattr(edge_census, "_raw_graph_from_uri",
                        lambda _uri, _graph: graph)

    def no_sdk(*_args, **_kwargs):
        raise AssertionError(
            "a URI census without --org opened the SDK — that path issues "
            "CREATE INDEX DDL against the target graph, which is the exact "
            "overclaim this boundary exists to prevent")

    monkeypatch.setattr(edge_census, "_open_sdk", no_sdk)

    rc = edge_census.main(["census", "--uri", "docker://:pw@host:6379/g",
                           "--json"])

    assert rc == 0
    view = json.loads(capsys.readouterr().out)
    assert view["nodes"]["capped_points"] is None, (
        "a URI census without --org must not report a cap denominator — "
        "reading it would require the SDK")
