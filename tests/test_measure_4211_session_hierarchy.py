"""#13919 review — the #4211 re-measurement instrument's own guards, pinned.

The instrument's PR added no test and no assertion, so every guard it *claimed* was
attributable only by reading. These pins are behavioural where behaviour is testable
against a fake graph (the turn discriminator, the denominator accounting, the exit
contract) and textual where the guard IS the query text (the Cypher that carries the
filter). Nothing here opens a DB, a socket or a model — the arms are faked at the
``_open_live`` / ``measure_*`` seams the tool itself uses.

The three review findings they cover:

* **P1** — ``sessions_with_gt1_contains_turn``,
  ``sessions_with_gt1_entity_linked_turn`` and
  ``turns_per_session_contains_max_avg`` were named after *turns* and computed over
  *every* CONTAINS-wired Point, so a session holding one turn plus a claim Point read
  as a session with >1 turn — the gate's own falsifier could never fire.
* **P2** — the ``instrument`` arm dropped a whole graph on ANY query error, so a clean
  census and a shrunken denominator were the same artifact.
* **P2** — an unmeasurable run (a replay) exited 0 with the reason in a NESTED block,
  so ``--out <receipt> || exit 1`` accepted it and overwrote a good receipt.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_TOOL = (_REPO / "tools" / "measure_4211_session_hierarchy.py")
_spec = importlib.util.spec_from_file_location("measure_4211_session_hierarchy", _TOOL)
assert _spec and _spec.loader
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


# ── fakes ─────────────────────────────────────────────────────────────────

class _Result:
    def __init__(self, rows):
        self.result_set = rows


_POINTS: dict[str, list[tuple[str, bool]]] = {
    "s1": [("turn", True), ("turn", True), ("turn", False), ("statement", False)],
    "s2": [("turn", True), ("statement", True)],
}


class _FakeHierarchyGraph:
    """Two sessions, one turn + one non-turn Point each — the shape the P1 defect needs.

    ``_POINTS[sid]`` is ``(pointKind, aboutObject_linked)``. The fake applies the turn
    discriminator **only when the Cypher asks for it** (``is_episodic``), which is the
    product's own model: the filter is the query's job, not the graph's.
    """

    def _counts(self, cypher: str) -> list[int]:
        turn_only = "is_episodic" in cypher
        linked_only = "aboutObject" in cypher
        out = []
        for pts in _POINTS.values():
            n = 0
            for kind, linked in pts:
                if turn_only and kind != "turn":
                    continue
                if linked_only and not linked:
                    continue
                n += 1
            out.append(n)
        return out

    def query(self, cypher: str, params=None) -> _Result:
        counts = self._counts(cypher)
        if "max(c)" in cypher:
            return _Result([[max(counts), sum(counts) / len(counts)]])
        if "> 1" in cypher:
            return _Result([[sum(1 for c in counts if c > 1)]])
        raise AssertionError(f"unexpected Cypher in this pin: {cypher!r}")


class _FakeInstrumentGraph:
    def __init__(self, sessions: int, turns: int, raise_on: str | None = None):
        self.sessions = sessions
        self.turns = turns
        self.raise_on = raise_on

    def query(self, cypher: str, params=None) -> _Result:
        if self.raise_on and self.raise_on in cypher:
            raise RuntimeError("graph query failed")
        if cypher.startswith("MATCH (s:Session) RETURN count(s)"):
            return _Result([[self.sessions]])
        if cypher.startswith("MATCH (p:Point) WHERE"):
            return _Result([[self.turns]])
        if "pointKind, count(*)" in cypher:
            return _Result([["event", self.turns]])
        if "aboutObject" in cypher:
            return _Result([[0]])
        return _Result([[1]])


class _FakeLiveGraph:
    """Answers ONLY the node-label read, so every other query records an ERROR.

    That shape is a PARTIAL read, and since #13919 round 9 a partial read is
    unmeasurable by design — so a test that needs `main` to exit 0 must ask for
    `answers_all=True` (a complete read) rather than assuming the raising fake is
    benign. Keeping the default raising matters: it is what the `queries_failed` and
    `empty_or_absent` arms are about.
    """

    def __init__(self, fail_nodes: bool = False, answers_all: bool = False):
        self.fail_nodes = fail_nodes
        self.answers_all = answers_all

    def query(self, cypher: str, params=None) -> _Result:
        if cypher.startswith("MATCH (n) UNWIND labels"):
            if self.fail_nodes:
                raise TimeoutError("nodes read timed out")
            return _Result([["Point", 5]])
        if self.answers_all:
            return _Result([[0]])
        raise RuntimeError("query timeout")


class _FakeDB:
    def __init__(self, graphs: dict):
        self._graphs = graphs
        self.connection = self

    def execute_command(self, command: str):
        assert command == "GRAPH.LIST"
        return list(self._graphs)

    def select_graph(self, name: str):
        return self._graphs[name]


def _run(graph, keys: tuple[str, ...]) -> dict:
    """Run just the named queries, so the fake sees only what the pin is about."""
    wanted = dict(mod.QUERIES)
    out = {}
    for key in keys:
        row = list(graph.query(wanted[key]).result_set[0])
        out[key] = row[0] if len(row) == 1 else row
    return out


#: Keys named `*turn*` that legitimately cannot carry the discriminator, each with the
#: reason. The population of turn-named keys is DERIVED from QUERIES below, never a
#: hand-kept list of the ones that DO carry it — a literal tuple of three names is what
#: silently passed the fourth, unfiltered `objects_with_gt1_turn` (#13919 round-7 item 3).
_TURN_KEY_EXEMPT = {
    "session_turn_count_sum": (
        # The exemption is only valid because the Cypher really is the SDK property sum: a
        # `*turn*` key that neither carries TURN nor sums `turn_count` reds below.
        "sums the SDK's own `s.turn_count` PROPERTY — the value IS the product's turn "
        "counter (a Session property), so the Point discriminator does not apply to it"),
}


# ── P1: the keys named `*turn*` must count TURNS ─────────────────────────

def test_every_turn_named_key_carries_the_turn_discriminator():
    queries = dict(mod.QUERIES)
    named = {k: q for k, q in queries.items() if "turn" in k.lower()}
    assert len(named) >= 4, (
        f"the population is DERIVED from QUERIES; {len(named)} key(s) is too few to "
        "prove anything (the read or the naming changed)")
    for key, reason in _TURN_KEY_EXEMPT.items():
        assert key in queries, f"the exemption {key!r} names no query — drop the entry"
        assert "turn_count" in queries[key], (
            f"{key!r} is exempted on the ground that {reason}, but its Cypher no "
            "longer reads `turn_count` — the exemption is now a hole")
    failures = [k for k, q in named.items()
                if k not in _TURN_KEY_EXEMPT and mod.TURN not in q]
    assert not failures, (
        f"these keys are named after turns but apply no turn discriminator: {failures} — "
        "they count claim/transcript Points as turns (round-7 item 3: "
        "`objects_with_gt1_turn` shipped unfiltered, with `t` as the Point variable as "
        "the tell)")


def test_the_any_kind_reading_is_reported_beside_the_turn_reading():
    queries = dict(mod.QUERIES)
    for pair in (("sessions_with_gt1_contains_turn", "sessions_with_gt1_contains_point"),
                 ("objects_with_gt1_turn", "objects_with_gt1_point")):
        assert pair[1] in queries, (
            f"{pair[0]} carries the turn discriminator, so its any-kind reading must be "
            "published BESIDE it — otherwise the divergence is invisible")
        assert "is_episodic" not in queries[pair[1]], (
            f"{pair[1]} must stay UNFILTERED — it is the visible other half of the "
            "divergence the turn key reports")
        assert mod.TURN not in queries[pair[1]], f"{pair[1]} is not the any-kind reading"


def test_an_object_attached_by_one_turn_and_one_claim_point_is_not_a_gt1_turn_object():
    """The falsifier case for `objects_with_gt1_turn`.

    The committed receipt's writer arm holds 9 Point->Object edges against 8 turns (the
    ninth hangs off an extracted `statement` Point), so this shape is the SHIPPED one,
    not a hypothetical. Unfiltered, an Object with one turn and one claim Point read as
    "more than one turn" — the same defect the `sessions_with_gt1_*` keys were split to
    fix, on the key that was missed (#13919 round-7 item 3).
    """
    class _ObjectsGraph:
        def query(self, cypher, params=None):
            # Turn filter present -> only the one Object whose attachments are all turns.
            return _Result([[1]]) if mod.TURN in cypher else _Result([[2]])

    got = _run(_ObjectsGraph(), ("objects_with_gt1_turn", "objects_with_gt1_point"))
    assert got["objects_with_gt1_turn"] == 1
    assert got["objects_with_gt1_point"] == 2
    assert got["objects_with_gt1_turn"] != got["objects_with_gt1_point"]


def test_a_session_with_one_turn_and_one_claim_point_is_not_a_gt1_turn_session():
    """The falsifier case: every session holds ONE turn plus a claim Point.

    Unfiltered, both sessions read as ">1 turn" and `sessions_with_gt1_contains_turn`
    can never approach 0 — which is exactly the branch the #4211 gate decides on.
    """
    graph = _FakeHierarchyGraph()
    got = _run(graph, ("sessions_with_gt1_contains_turn",
                       "sessions_with_gt1_contains_point"))
    assert got["sessions_with_gt1_contains_turn"] == 1   # only s1 has >1 real turn
    assert got["sessions_with_gt1_contains_point"] == 2  # both hold >1 Point
    assert got["sessions_with_gt1_contains_turn"] != got["sessions_with_gt1_contains_point"]


def test_entity_linked_turn_counts_turns_not_linked_claim_points():
    got = _run(_FakeHierarchyGraph(), ("sessions_with_gt1_entity_linked_turn",))
    # s1 has 2 linked turns; s2 has 1 linked turn + 1 linked claim Point.
    assert got["sessions_with_gt1_entity_linked_turn"] == 1


def test_turns_per_session_is_measured_on_turns_not_on_every_contains_point():
    got = _run(_FakeHierarchyGraph(), ("turns_per_session_contains_max_avg",))
    max_c, avg_c = got["turns_per_session_contains_max_avg"]
    assert max_c == 3, "max counted every CONTAINS-wired Point (4) instead of turns (3)"
    assert avg_c == 2.0, "avg counted claim Points as turns (3.0 instead of 2.0)"


# ── P2: an unreadable graph is NAMED, never silently dropped ──────────────

def test_an_unreadable_node_count_is_UNKNOWN_not_not_empty():
    assert mod._is_empty({"nodes_by_label": {"error": "TimeoutError: boom"}}) is None, (
        "an error dict is truthy, so the old fail-open read it as 'not empty' and the "
        "zeros beside it published as a measurement")
    assert mod._is_empty({"nodes_by_label": [["Point", 3]]}) is False
    assert mod._is_empty({"nodes_by_label": []}) is True


def test_a_failed_query_is_named_in_the_arm_that_failed_in_it(monkeypatch):
    monkeypatch.setattr(
        mod, "_open_live",
        lambda uri: (_FakeDB({"tortoise": _FakeLiveGraph()}), {"graph": "tortoise"}))
    body = mod.measure_live("docker://:@localhost:1/tortoise")
    assert "turns_total" in body["queries_failed"]
    assert body["counts"]["turns_total"]["error"].startswith("RuntimeError")
    assert body["empty_or_absent"] is False


def test_an_unreadable_emptiness_read_makes_the_arm_unmeasurable(monkeypatch):
    monkeypatch.setattr(
        mod, "_open_live",
        lambda uri: (_FakeDB({"tortoise": _FakeLiveGraph(fail_nodes=True)}),
                     {"graph": "tortoise"}))
    body = mod.measure_live("docker://:@localhost:1/tortoise")
    assert body["empty_or_absent"] is None
    assert "unmeasurable" in body


def _instrument(monkeypatch, graphs: dict) -> dict:
    monkeypatch.setattr(mod, "_open_live", lambda uri: (_FakeDB(graphs), {"graph": "x"}))
    return mod.measure_instrument("docker://:@localhost:1/x")


def test_the_census_denominator_is_fully_accounted(monkeypatch):
    body = _instrument(monkeypatch, {
        "g-clean": _FakeInstrumentGraph(sessions=1, turns=6),
        "g-no-sessions": _FakeInstrumentGraph(sessions=0, turns=0),
        "g-no-turns": _FakeInstrumentGraph(sessions=2, turns=0),
    })
    assert body["graphs_with_turn_store"] == 1
    assert body["graphs_without_turn_store"] == 2
    assert body["graphs_skipped_on_error"] == 0
    assert (body["graphs_with_turn_store"] + body["graphs_without_turn_store"]
            + body["graphs_skipped_on_error"]) == body["graphs_on_instance"]
    assert "unmeasurable" not in body


def test_a_graph_that_raises_makes_the_census_unmeasurable_and_is_named(monkeypatch):
    """Pre-fix this graph vanished from `rows` — numerator AND denominator — so the
    headline read '0 aboutObject in 1 of 1' with no trace that a graph was skipped."""
    body = _instrument(monkeypatch, {
        "g-clean": _FakeInstrumentGraph(sessions=1, turns=6),
        "g-raises": _FakeInstrumentGraph(sessions=1, turns=6, raise_on="aboutObject"),
    })
    assert body["graphs_skipped_on_error"] == 1
    assert body["skipped_on_error"] == [
        {"graph": "g-raises", "error": "RuntimeError: graph query failed"}]
    assert body["graphs_on_instance"] == 2
    assert "unmeasurable" in body, (
        "a shrunken denominator must not be published as a complete census")
    assert body["totals"]["sessions"] == 1


# ── P2: the exit contract, and a good receipt that cannot be clobbered ────

def test_a_replayed_writer_exits_2_and_hoists_the_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "measure_writer", lambda **_: {
        "unmeasurable": "sessions replayed, not captured: s1",
        "counts": {"sessions_total": 3},
    })
    out = tmp_path / "receipt.json"
    rc = mod.main(["--arms", "writer", "--out", str(out)])
    assert rc == 2, "a replay reported as a successful measurement is accepted by `|| exit 1`"
    body = json.loads(out.read_text())
    assert body["unmeasurable"]["arms"]["writer"].startswith("sessions replayed")
    assert "PRE-EXISTING" in body["unmeasurable"]["note"]


def test_an_unmeasurable_run_does_not_overwrite_a_good_receipt(monkeypatch, tmp_path):
    good = {"issue": 4211, "arms": {"store": {"counts": {"turns_total": 8}}}}
    out = tmp_path / "receipt.json"
    out.write_text(json.dumps(good) + "\n")
    monkeypatch.setattr(mod, "measure_writer", lambda **_: {"unmeasurable": "replayed"})
    rc = mod.main(["--arms", "writer", "--out", str(out)])
    assert rc == 2
    assert json.loads(out.read_text()) == good, "the good receipt was replaced"
    sibling = tmp_path / "receipt.unmeasurable.json"
    assert json.loads(sibling.read_text())["unmeasurable"]["arms"]["writer"] == "replayed"


def test_an_unmeasurable_instrument_arm_exits_2(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "measure_instrument", lambda uri: {
        "graphs_on_instance": 2, "graphs_with_turn_store": 1,
        "graphs_without_turn_store": 0, "graphs_skipped_on_error": 1,
        "skipped_on_error": [{"graph": "g", "error": "RuntimeError: x"}],
        "totals": {}, "contains_targets_by_kind": {},
        "graphs_with_zero_aboutObject": 1,
        "unmeasurable": "1 of 2 graphs raised",
    })
    rc = mod.main(["--arms", "instrument", "--out", str(tmp_path / "r.json")])
    assert rc == 2


def test_a_measured_run_exits_0_and_writes_the_receipt(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "measure_corpus", lambda d: {
        "stats": {"files": 1, "records": 1}, "sessions": 1, "turns_distinct": 2,
        "turns_naming_a_ref_distinct": 0, "sessions_with_gt1_turn": 1,
    })
    out = tmp_path / "receipt.json"
    rc = mod.main(["--arms", "corpus", "--out", str(out)])
    assert rc == 0
    body = json.loads(out.read_text())
    assert "unmeasurable" not in body
    assert body["arms"]["corpus"]["sessions"] == 1


# ── selection: a tool-only diff must run this file ────────────────────────

def test_a_tool_only_diff_selects_this_pin():
    """`tools/` is a flat `NON_PYTHON_PREFIXES` entry, so a tool-only diff drops to the
    docs-only early return and selects NO surface. Measured before the `CORE_ALSO`
    entry: `--changed-files tools/measure_4211_session_hierarchy.py` -> `surfaces: []`,
    i.e. the very PR that changed the exit contract, the turn filters and the
    denominator accounting ran none of the pins above.
    """
    from tools import ci_selection

    manifest = ci_selection.load_manifest()
    selection = ci_selection.select(
        ["tools/measure_4211_session_hierarchy.py"], "pull_request", manifest)
    assert "core" in (selection.get("surfaces") or []), selection.get("surfaces")
    files = selection.get("test_files")
    if files == "ALL":
        return
    assert "test_measure_4211_session_hierarchy.py" in files, (
        "a diff to the instrument no longer selects its own pins — the `CORE_ALSO` "
        "entry was dropped or the tool-prefix behaviour changed")


# ══════════════════════════════════════════════════════════════════════════
# N1/N2/N3/N4/N5/N7/N8 — the SECOND review round's open findings
#
# The three findings above were fixed first and pinned above. These seven were still
# open at the same head, and the review's mutation table said the same thing about
# them: no row reddens. Each pin below names its finding.
# ══════════════════════════════════════════════════════════════════════════

class _MutatingEngine:
    """Rewrites the file it is opened on — what an embedded RDB open actually does."""

    def __init__(self, path, events: list[str] | None = None):
        self.path = Path(path)
        self.events = events if events is not None else []
        self.path.write_bytes(b"REWRITTEN-BY-THE-SERVER")
        self.connection = self

    def execute_command(self, _cmd):
        return ["tortoise"]

    def select_graph(self, _name):
        return _AnyGraph()

    def close(self):
        self.events.append("close")


class _AnyGraph:
    """Answers every query with one zero row (the store arm runs the whole QUERIES set)."""

    def query(self, cypher: str, params=None):
        return _Result([[0]])


def test_n1_the_object_kind_mix_and_created_at_span_are_measured_queries():
    """N1 (P1): the body's two load-bearing store facts were absent from the artefact.

    The inference "0 `pm:issue` ⇒ no GitHub indexer ⇒ legacy population" stood in the
    PR body while the receipt could not reproduce it: no `objectKind` and no
    `created_at`-range query existed in QUERIES at all.
    """
    queries = dict(mod.QUERIES)
    assert "objectKind" in queries["object_kinds"]
    assert "created_at" in queries["session_created_at_range"]
    assert "min(" in queries["session_created_at_range"]
    assert "max(" in queries["session_created_at_range"]


def test_n2_the_instrument_passes_the_repos_own_redis_guard():
    """N2 (P2): the file tripped redis-guard pattern 2 (a raw redislite import).

    CI was green over it — the workflow runs the guard with no argv, so it only sweeps
    `tortoise/` and `graph-scripts/` — while pre-commit (`pass_filenames: true`) saw
    `tools/`. This runs the repo's own guard over THIS file, which is the sweep that
    reds on the pre-fix line.
    """
    import subprocess
    import sys

    proc = subprocess.run([sys.executable, str(_REPO / "tools/redis-guard.py"), str(_TOOL)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "from tortoise import FalkorDB" in _TOOL.read_text()


def test_n2_the_embedded_server_is_closed_before_its_directory_is_deleted(
        monkeypatch, tmp_path):
    """N2, second half: `rmtree` under a still-running child writes no owner record."""
    events: list[str] = []
    src = tmp_path / "store.db"
    src.write_bytes(b"rdb-bytes")
    monkeypatch.setattr(mod, "_open_embedded",
                        lambda path: _MutatingEngine(path, events))
    real_rmtree = mod.shutil.rmtree

    def _spy_rmtree(path, **kw):
        events.append("rmtree")
        return real_rmtree(path, **kw)

    monkeypatch.setattr(mod.shutil, "rmtree", _spy_rmtree)
    body = mod.measure_store(str(src), "tortoise")
    assert body["graph"] == "tortoise"
    assert events[0] == "close", events
    assert events[-1] == "rmtree", events


def test_n3_source_identity_describes_the_original_before_the_engine_opens_it(
        monkeypatch, tmp_path):
    """N3 (P2): `source_sha256` hashed the COPY after the engine opened it, while
    `source_bytes` read the ORIGINAL — two files at two times."""
    import hashlib

    original = tmp_path / "store.db"
    original.write_bytes(b"original-rdb")
    monkeypatch.setattr(mod, "_open_embedded", _MutatingEngine)

    body = mod.measure_store(str(original), "tortoise")
    assert body["source_sha256"] == hashlib.sha256(b"original-rdb").hexdigest()
    assert body["source_bytes"] == len(b"original-rdb")
    assert body["copy_matches_source"] is True


def test_n4_the_every_graph_row_is_null_over_an_empty_population(monkeypatch):
    """N4 (P2): `all([])` is True, so "every graph satisfies X" with ZERO graphs."""
    fn = mod._every_graph_has_fewer_turns_than_contains
    assert fn([]) is None
    assert fn([{"turns": 2, "contains_edges": 3}]) is True
    assert fn([{"turns": 4, "contains_edges": 3}]) is False

    body = _instrument(monkeypatch, {"empty": _FakeInstrumentGraph(0, 0)})
    assert body["graphs_with_turn_store"] == 0
    assert body["every_graph_has_fewer_turns_than_contains"] is None


def test_n5_a_truncated_per_graph_publishes_what_it_omitted(monkeypatch):
    """N5 (P2): 25 of 60 rows were cut with no marker, so the aggregate was not
    re-derivable from the artefact."""
    graphs = {f"g{i}": _FakeInstrumentGraph(sessions=2, turns=3) for i in range(30)}
    body = _instrument(monkeypatch, graphs)
    assert body["graphs_with_turn_store"] == 30
    assert len(body["per_graph"]) == 25
    assert body["per_graph_omitted"] == 5
    assert mod._head([1, 2, 3], 2) == ([1, 2], 1)
    assert mod._head([1], 5) == ([1], 0)


def test_n5_and_n7_the_receipt_carries_the_omitted_count_and_the_module_digests(
        monkeypatch, tmp_path):
    """N5's warning + N7 (P3): the receipt pinned only the TOOL's bytes, so an SDK
    change moved the numbers with nothing in the artefact to say so."""
    import hashlib

    digests = mod._module_digests()
    assert set(digests) == {"tortoise/sdk.py", "tortoise/session_link.py"}
    for rel, digest in digests.items():
        assert digest == hashlib.sha256((_REPO / rel).read_bytes()).hexdigest()

    graphs = {f"g{i}": _FakeInstrumentGraph(2, 3) for i in range(30)}
    body = _instrument(monkeypatch, graphs)
    assert body["per_graph_omitted"] == 5
    assert any("per_graph" in w for w in
               mod._collect_warnings({"arms": {"instrument": body}}))

    monkeypatch.setattr(mod, "measure_store", lambda *a, **k: {"skipped": "not here"})
    out = tmp_path / "receipt.json"
    # F1: a skip measured NOTHING, so it is an UNMEASURABLE run (exit 2), not a
    # completed capture. The measured half of the receipt still has to carry N5/N7.
    assert mod.main(["--arms", "store", "--out", str(out)]) == 2
    receipt = json.loads(out.read_text())
    assert receipt["instrument"]["measured_through"] == digests
    # a skip recorded only inside the arm body is now also a top-level warning
    assert any(w.startswith("store: skipped") for w in receipt["warnings"])
    assert "not here" in receipt["unmeasurable"]["arms"]["store"]


def test_n8_the_default_arm_set_cannot_write(monkeypatch, tmp_path):
    """N8 (P2): `writer` was in the DEFAULT set and `TORTOISE_DB_URI` is the product's
    connection string, so a bare run annotated whatever graph it named."""
    assert "writer" not in mod.DEFAULT_ARMS.split(",")
    seen = {}

    def _fake_writer(**kwargs):
        seen.update(kwargs)
        return {"skipped": "n/a"}

    monkeypatch.setattr(mod, "measure_writer", _fake_writer)
    mod.main(["--arms", "writer", "--out", str(tmp_path / "r.json")])
    assert seen == {"allow_non_ephemeral": False}


def test_n8_the_writer_refuses_a_non_ephemeral_graph_and_never_copies_the_password(
        monkeypatch):
    """N8: the arm that MUTATES had no target check while the read arms both refuse."""
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:hunter2@db.example:6379/tortoise")
    body = mod.measure_writer()
    assert "refusing to WRITE" in body["skipped"]
    assert body["target"] == {"host": "db.example", "port": 6379, "graph": "tortoise"}
    assert "hunter2" not in json.dumps(body)


def test_n8_an_ephemeral_graph_needs_no_override(monkeypatch):
    assert mod._writer_target("docker://:@127.0.0.1:16379/m4211_hierarchy")[1] is None
    target, refusal = mod._writer_target("docker://:@db:6379/tortoise")
    assert refusal and target["graph"] == "tortoise"


# ── the multiplicity guard the round-2 fix claimed, pinned end to end ───────
def test_the_pair_count_is_node_identity_not_a_property_join():
    """The round-2 "fixed" guard was itself null-blind, and nothing exercised it.

    ``count(DISTINCT s.id + '|' + p.id)`` answers **0** for any graph whose Points
    carry no ``id`` property — every fixture-shape graph this arm measures — because
    ``null + '|' + null`` is null and ``count(DISTINCT null)`` is 0. The pairs column
    then reads 0 beside a non-zero ``contains_edges``, and the duplicate check derived
    from it reports "0 duplicates" unconditionally. MEASURED on an embedded fixture
    (one Session, one Point with no id, one duplicate CONTAINS edge): 2 edges →
    id-concat 0, ``WITH DISTINCT s, p`` 1.
    """
    queries = dict(mod.QUERIES)
    pairs = queries["contains_distinct_pairs"]
    assert "WITH DISTINCT s, p" in pairs
    assert "s.id" not in pairs and "p.id" not in pairs, (
        "the pair count must not depend on a node PROPERTY fixtures do not set")

    # No CODE carries the spelling — a comment that documents the defect is fine, a
    # statement that executes it is the defect (the census arm has its own copy).
    offenders = [ln for ln in _TOOL.read_text(encoding="utf-8").splitlines()
                 if "DISTINCT s.id + '|' + p.id" in ln
                 and not ln.lstrip().startswith("#")]
    assert offenders == [], (
        f"the null-blind pair count still executes at {offenders}")


def test_a_refused_writer_run_is_exit_2_not_a_completed_capture(monkeypatch, tmp_path):
    """N8's refusal must reach the EXIT CODE, not only the arm body.

    A refusal writes nothing, so a ``--out <receipt> || exit 1`` caller that receives
    exit 0 accepts "I declined to measure" as a finished capture. Same class as R3's
    replay: the reason is hoisted to the top-level ``unmeasurable`` block.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@db.example:6379/tortoise")
    out = tmp_path / "r.json"
    rc = mod.main(["--arms", "writer", "--out", str(out)])
    assert rc == 2, "a refused write measured nothing and must not read as success"
    receipt = json.loads(out.read_text())
    assert "refusing to WRITE" in receipt["unmeasurable"]["arms"]["writer"]
    assert "counts" not in receipt["arms"]["writer"]


# ── round 3: an arm that was NOT REQUESTED is not an arm that SKIPPED ─────
#
# Making `writer` opt-in left a hole the round-2 findings did not reach: the documented
# one-line re-run passes no `--arms`, so it now produces a receipt with NO writer arm
# and nothing in the artifact saying whether that arm was skipped, refused, or never
# asked for. That is the same class as R2/N5 — a shrunken population published as a
# complete one — one level up, in the arm set itself.

def _fake_arms(monkeypatch):
    ran: list[str] = []
    # A MEASURED body: these pins are about the arm SET, and a skip would (correctly)
    # make the run unmeasurable — see the F1 pins below for that half.
    for name in ("measure_store", "measure_live", "measure_instrument",
                 "measure_corpus", "measure_writer"):
        monkeypatch.setattr(mod, name, lambda *a, _n=name, **k: (
            ran.append(_n) or {"measured": True}))
    return ran


def test_the_receipt_says_which_arms_it_never_asked_for(monkeypatch, tmp_path):
    ran = _fake_arms(monkeypatch)
    out = tmp_path / "r.json"
    assert mod.main(["--arms", "corpus", "--out", str(out)]) == 0
    inst = json.loads(out.read_text())["instrument"]
    assert inst["arms_requested"] == ["corpus"]
    assert inst["arms_not_requested"] == ["store", "live", "instrument", "writer"], (
        "an arm absent from `arms` is indistinguishable from one that measured nothing; "
        "the artifact must say it was never requested")
    assert ran == ["measure_corpus"]


def test_a_bare_run_records_that_the_write_arm_was_not_requested(monkeypatch, tmp_path):
    _fake_arms(monkeypatch)
    out = tmp_path / "r.json"
    assert mod.main(["--out", str(out)]) == 0
    inst = json.loads(out.read_text())["instrument"]
    assert inst["arms_requested"] == mod.DEFAULT_ARMS.split(",")
    assert "writer" in inst["arms_not_requested"], (
        "the default set is read-only, so the artifact must name the arm it did not run")


def test_a_typo_in_arms_is_refused_not_measured_as_nothing(monkeypatch, tmp_path):
    """`--arms crocodile` used to match no `if "<arm>" in arms` and exit 0 with an
    EMPTY receipt — an empty population published as a measurement."""
    ran = _fake_arms(monkeypatch)
    out = tmp_path / "r.json"
    try:
        rc = mod.main(["--arms", "crocodile", "--out", str(out)])
    except SystemExit as exc:            # argparse's refusal path
        rc = exc.code
    assert rc != 0, "a typo in --arms was accepted as a measurement"
    assert not out.exists()
    assert ran == []


# ── F1: a run that measured NOTHING is not a measurement ──────────────────
#
# Five refusal paths (`store` absent, graph not in the file, `TORTOISE_DB_URI` not
# exported, no capture journal, graph not on the instance) returned a bare
# `{"skipped": …}`, so `main`'s hoist collected nothing and control reached `return 0`.
# Two consequences, both fixed here: "I measured nothing" was reported to a
# `--out <receipt> || exit 1` caller as a finished capture, and — because the receipt had
# no `unmeasurable` — `_publish` was willing to REPLACE the committed receipt with the
# empty artefact.

def test_f1_every_skipped_arm_is_a_top_level_unmeasurable_and_exit_2(
        monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "measure_store", lambda *a, **k: {
        "skipped": "store not found at ~/.tortoise/tortoise.db"})
    monkeypatch.setattr(mod, "measure_corpus", lambda d: {
        "skipped": "no session events under /nope"})
    out = tmp_path / "r.json"
    rc = mod.main(["--arms", "store,corpus", "--out", str(out)])
    assert rc == 2, (
        "a run whose every arm skipped measured NOTHING and must not read as success")
    receipt = json.loads(out.read_text())
    assert set(receipt["unmeasurable"]["arms"]) == {"store", "corpus"}
    assert "store not found" in receipt["unmeasurable"]["arms"]["store"]


def test_f1_a_skipped_run_cannot_replace_the_committed_receipt(monkeypatch, tmp_path):
    """`_publish` protects a good receipt only when `unmeasurable` is set, so a
    skip-only run took the plain write. The tool's own documented re-run command targets
    the committed receipt, so one `--arms store` on a box without the Mac's embedded
    store would replace a 1,353-line measured artefact with a skip line, at exit 0."""
    good = {"issue": 4211, "arms": {"store": {"counts": {"turns_total": 8}}}}
    out = tmp_path / "receipt.json"
    out.write_text(json.dumps(good) + "\n")
    monkeypatch.setattr(mod, "measure_store", lambda *a, **k: {"skipped": "store not found"})
    rc = mod.main(["--arms", "store", "--out", str(out)])
    assert rc == 2
    assert json.loads(out.read_text()) == good, "the measured receipt was replaced"
    sibling = tmp_path / "receipt.unmeasurable.json"
    assert "store not found" in json.loads(sibling.read_text())["unmeasurable"]["arms"]["store"]


def test_f1_a_measured_arm_beside_a_skipped_one_still_exits_2(monkeypatch, tmp_path):
    """The verdict is per-RUN, not per-arm: half a measurement is not a measurement."""
    monkeypatch.setattr(mod, "measure_corpus", lambda d: {
        "stats": {"files": 1, "records": 1}, "sessions": 1, "turns_distinct": 2,
        "turns_naming_a_ref_distinct": 0, "sessions_with_gt1_turn": 1})
    monkeypatch.setattr(mod, "measure_store", lambda *a, **k: {"skipped": "not here"})
    out = tmp_path / "r.json"
    assert mod.main(["--arms", "store,corpus", "--out", str(out)]) == 2
    receipt = json.loads(out.read_text())
    assert receipt["arms"]["corpus"]["sessions"] == 1
    assert list(receipt["unmeasurable"]["arms"]) == ["store"]


# ── F2: a credential never reaches a receipt ──────────────────────────────

def test_f2_no_arm_serialises_the_raw_uri_or_a_slice_of_it():
    """`measure_live`/`measure_instrument` wrote `"uri": uri` verbatim and the writer
    wrote `uri.rsplit("/", 1)[-1]`, which on a PATH-LESS URI returns the whole authority.
    The receipt is committed under `docs/scoping/receipts/`, so either spelling is a
    credential-in-git path."""
    offenders = [ln for ln in _TOOL.read_text(encoding="utf-8").splitlines()
                 if not ln.lstrip().startswith("#")
                 and ('"uri": uri' in ln or '"uri_graph": uri.rsplit' in ln)]
    assert offenders == [], f"the raw URI is still serialised at {offenders}"


def test_f2_the_read_arms_redact_the_password_and_the_writer_records_the_graph(monkeypatch):
    monkeypatch.setattr(mod, "_open_live", lambda uri: (
        _FakeDB({"tortoise": _FakeLiveGraph()}), {"graph": "tortoise"}))
    live = mod.measure_live("docker://:hunter2@localhost:1/tortoise")
    assert "hunter2" not in json.dumps(live)
    assert live["uri"] == "docker://localhost:1/tortoise", (
        "the host/port/graph survive the redaction, the userinfo does not")

    inst = mod.measure_instrument("docker://:hunter2@localhost:1/x")
    assert "hunter2" not in json.dumps(inst)

    # A path-less URI (`_parse_uri` supports it) made `rsplit("/", 1)[-1]` return
    # ':hunter2@db.example:6379' — reachable via --allow-non-ephemeral-writes.
    target, _refusal = mod._writer_target("docker://:hunter2@db.example:6379")
    assert target["graph"] == "tortoise"
    assert "hunter2" not in json.dumps(target)


# ── F3: the provenance is what SUPPLIED the URI, not a literal ────────────

def test_f3_the_uri_source_is_what_supplied_the_uri_not_a_hardcoded_profile(
        monkeypatch, tmp_path):
    """`role_evidence.uri_source` was the literal '~/.zshrc:48' while the URI came from
    `--live-uri`, so a re-run published a receipt asserting a provenance nothing read."""
    monkeypatch.setattr(mod, "_open_live", lambda uri: (
        _FakeDB({"tortoise": _FakeLiveGraph(answers_all=True)}), {"graph": "tortoise"}))
    assert "~/.zshrc:48" not in \
        mod.measure_live("docker://:@localhost:1/tortoise")["role_evidence"]["uri_source"]
    assert mod.measure_live(
        "docker://:@localhost:1/tortoise",
        uri_source="--live-uri (test)")["role_evidence"]["uri_source"] == "--live-uri (test)"

    provided = tmp_path / "provided.json"
    assert mod.main(["--arms", "live", "--live-uri", "docker://:@localhost:1/tortoise",
                     "--out", str(provided)]) == 0
    assert json.loads(provided.read_text())["arms"]["live"]["role_evidence"][
        "uri_source"].startswith("--live-uri")

    defaulted = tmp_path / "defaulted.json"
    assert mod.main(["--arms", "live", "--out", str(defaulted)]) == 0
    src = json.loads(defaulted.read_text())["arms"]["live"]["role_evidence"]["uri_source"]
    assert "built-in DEFAULT_LIVE_URI" in src and "NOT measured" in src, (
        "the default's provenance is asserted by the constant and must say so")


# ── F4: the sentence a reader acts on carries the TRUE count ──────────────

def test_f4_the_shrunken_denominator_names_the_true_count_not_the_truncated_list(
        monkeypatch):
    """`skipped_on_error` is truncated to 25, so with 30 raising graphs the prose said
    '25 of 30 graphs raised' while `graphs_skipped_on_error` in the same body said 30."""
    graphs = {f"g{i}": _FakeInstrumentGraph(1, 6, raise_on="aboutObject") for i in range(30)}
    body = _instrument(monkeypatch, graphs)
    assert body["graphs_skipped_on_error"] == 30
    assert len(body["skipped_on_error"]) == 25          # the list really is truncated
    assert body["unmeasurable"].startswith("30 of 30 graphs raised"), body["unmeasurable"]


# ── F2, the FOURTH site: the recorded argv is committed too ───────────────
#
# Found by live verification, not by reading: the round-4 fix redacted the three arms
# that serialise a URI, and `instrument.argv` was left verbatim. MEASURED at that head:
#   .venv/bin/python tools/measure_4211_session_hierarchy.py --arms live \
#     --live-uri 'docker://:hunter2@127.0.0.1:1/tortoise' --out /tmp/x.json
#   -> 'hunter2' at instrument.argv[3], inside a receipt that is committed under docs/.

def test_f2_the_recorded_argv_does_not_carry_the_password(monkeypatch, tmp_path):
    assert mod._redact_argv(["--arms", "live", "--live-uri", "docker://:pw@h:1/g"]) == [
        "--arms", "live", "--live-uri", "docker://h:1/g"]
    assert mod._redact_argv(["--live-uri=docker://:pw@h:1/g"]) == [
        "--live-uri=docker://h:1/g"]
    assert mod._redact_argv(["--arms", "corpus"]) == ["--arms", "corpus"]

    monkeypatch.setattr(mod, "measure_live", lambda *a, **k: {"measured": True})
    out = tmp_path / "r.json"
    assert mod.main(["--arms", "live", "--live-uri", "docker://:hunter2@h:1/g",
                     "--out", str(out)]) == 0
    text = out.read_text()
    assert "hunter2" not in text, "the connection password reached the committed receipt"
    assert "docker://h:1/g" in text, "the flag's value must stay reproducible"


def test_f2_argv_records_this_invocations_arguments_not_the_interpreters(tmp_path):
    """`sys.argv[1:]` records the PYTEST command line for a programmatic `main([...])`."""
    out = tmp_path / "r.json"
    assert mod.main(["--arms", "corpus", "--sessions-dir", str(tmp_path / "none"),
                     "--out", str(out)]) == 2
    argv = json.loads(out.read_text())["instrument"]["argv"]
    assert argv[:3] == ["--arms", "corpus", "--sessions-dir"], argv
    assert "no:cacheprovider" not in argv, argv
    assert not any("test_measure_4211" in a for a in argv), argv


# ── #13919 review round 5: four ways the guards failed OPEN ──────────────────
def test_f1_a_keyless_replay_is_unmeasurable_and_the_token_is_not_the_invariant(
        monkeypatch, tmp_path):
    """F1 (P1): keying "replay" on ``extraction_mode == "replayed"`` failed OPEN.

    ``TortoiseSDK._capture_session`` puts its KEYLESS branch BEFORE the #1727 replay
    branch on purpose (`tortoise/sdk.py`: "a keyless call must ALWAYS be reported as
    keyless, never as a silent 'replayed'"). So a replay performed under a keyless
    extraction reports ``no-provider``: the ``replayed`` list stayed empty, no
    ``unmeasurable`` was set, the run exited 0 — and ``--out <receipt> || exit 1``
    then OVERWROTE the committed receipt with pre-existing counts. Reachable because
    the arm arms its mock with ``os.environ.setdefault``, which does not override an
    ambient ``TORTOISE_SESSION_LLM_MOCK`` that is present but not ``"1"``.

    The invariant is "this run wrote nothing", which the arm already measured as
    ``delta_this_run`` and then ignored.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:16379/m4211_hierarchy")
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "0")  # present, and not "1"

    counts = {"points_total": 1, "turns_total": 3, "contains_edges": 3,
              "sessions_total": 3, "objects_total": 1,
              "aboutObject_by_endpoint": 0, "turns_with_aboutObject": 0,
              "sessions_with_aboutObject": 0}
    # before == after: this run wrote NOTHING (a replay) — every delta is 0.
    monkeypatch.setattr(mod, "_run_queries", lambda g: dict(counts))

    class _Graph:
        def query(self, *_a, **_k):
            return _Result([[0]])

    class _Proj:
        g = _Graph()

    class _FakeSDK:
        def __init__(self, *_a, **_k):
            pass

        def _get_proj(self):
            return _Proj()

        def create_object(self, *_a, **_k):
            return {}

        def _capture_session(self, conv, session_id=None):
            # A keyless capture of an ALREADY-PRESENT session: no-provider, not
            # "replayed" — the exact shape that slipped through.
            return {"ok": True, "turns": 0, "extraction_mode": "no-provider",
                    "errors": [], "warnings": []}

        def close(self):
            pass

    monkeypatch.setattr("tortoise.sdk.TortoiseSDK", _FakeSDK)

    body = mod.measure_writer()
    assert body.get("unmeasurable"), (
        "a keyless REPLAY reported itself as a measured run — `--out <receipt> || "
        "exit 1` accepts that and overwrites the committed receipt")
    assert "wrote NOTHING" in body["unmeasurable"]
    assert "no-provider" in body["unmeasurable"], (
        "the reason must name the modes observed, so a reader can tell a keyless "
        "extraction from a replayed one without re-running")
    assert body["extraction_modes"]["m4211-ref-2of8"] == "no-provider"

    # …and the EXIT CODE, not only the body (this is what `|| exit 1` reads).
    out = tmp_path / "r.json"
    assert mod.main(["--arms", "writer", "--out", str(out)]) == 2


def test_f1_a_real_capture_under_the_same_keyless_env_still_measures(monkeypatch):
    """The anti-vacuity half of F1: the delta test must not red a genuine capture.

    A keyless extraction still STORES its turns (`tortoise/sdk.py` — the mechanical
    turn loop runs before the provider branch), so a FIRST run in the same keyless
    env writes points and this arm must report a measurement, not a refusal.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:16379/m4211_hierarchy")
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "0")
    before = {"points_total": 0, "turns_total": 0, "contains_edges": 0,
              "sessions_total": 0, "objects_total": 0,
              "aboutObject_by_endpoint": 0, "turns_with_aboutObject": 0,
              "sessions_with_aboutObject": 0}
    after = dict(before, points_total=22, turns_total=22, contains_edges=25,
                 sessions_total=3, objects_total=1)
    seq = iter([dict(before), dict(after)])
    monkeypatch.setattr(mod, "_run_queries", lambda g: next(seq))

    class _Graph:
        def query(self, *_a, **_k):
            return _Result([[0]])

    class _Proj:
        g = _Graph()

    class _FakeSDK:
        def __init__(self, *_a, **_k):
            pass

        def _get_proj(self):
            return _Proj()

        def create_object(self, *_a, **_k):
            return {}

        def _capture_session(self, conv, session_id=None):
            return {"ok": True, "turns": 22, "extraction_mode": "no-provider",
                    "errors": [], "warnings": []}

        def close(self):
            pass

    monkeypatch.setattr("tortoise.sdk.TortoiseSDK", _FakeSDK)
    body = mod.measure_writer()
    assert "unmeasurable" not in body, body.get("unmeasurable")
    assert body["delta_this_run"]["turns_total"] == 22


def test_f2_an_unparseable_uri_refusal_does_not_carry_the_password(tmp_path):
    """F2 (P2): the refusal path interpolated the RAW uri, credential included.

    ``urlsplit`` answers ``hostname=None, password='hunter2'`` for a host-less
    credentialed URI, so this shape takes the refusal path rather than the redacted
    one — and the message is serialised into the receipt and its traceback by every
    arm that catches it.
    """
    uri = "docker://:hunter2@/tortoise"
    try:
        mod._parse_uri(uri)
    except ValueError as exc:
        assert "hunter2" not in str(exc), "the refusal itself carried the password"
    else:  # pragma: no cover - the shape must refuse
        raise AssertionError("a host-less URI was accepted")

    out = tmp_path / "r.json"
    assert mod.main(["--arms", "live", "--live-uri", uri, "--out", str(out)]) == 2
    assert "hunter2" not in out.read_text(), (
        "the password reached the receipt through the arm's error/`traceback` field")


def test_f3_a_graph_whose_name_only_CONTAINS_a_marker_is_not_ephemeral():
    """F3 (P2): substring matching admitted real production names.

    ``test`` is a substring of ``latest`` and of ``contest``, so
    ``any(marker in graph ...)`` let the writer arm mint three fixture sessions, the
    shared claim Point and the Object ``test/repo#42`` into ``tortoise_latest`` /
    ``prod_latest`` / ``contest_graph`` — the consequence N8's refusal exists to
    prevent.
    """
    for prod in ("tortoise", "graphs", "tortoise_latest", "prod_latest",
                 "contest_graph", "protest"):
        assert not mod._looks_ephemeral(prod), (
            f"{prod!r} was admitted as a lane throwaway — the write refusal failed open")
    for lane in ("m4211_hierarchy", "m4211-x", "test_graph", "ci_lane",
                 "tmp.scratch", "e2e", "lane_42"):
        assert mod._looks_ephemeral(lane), lane
    # A name that merely STARTS with a marker but carries no delimiter is NOT
    # admitted: the refusal is the safe direction, so `scratchpad` refuses rather
    # than writes into a graph nobody proved was a throwaway.
    assert not mod._looks_ephemeral("scratchpad")


def test_f4_the_embedded_arm_takes_the_embedded_resolver_not_the_server_one(
        monkeypatch):
    """F4 (P2): the correction this PR makes is otherwise unpinned.

    ``tests/test_bounded_raw_clients_3065.py`` accepts EITHER shape (an explicit
    ``socket_timeout=`` or a ``**…socket_kwargs…`` spread), so reverting
    ``_open_embedded`` to ``**_bounded_server_socket_kwargs()`` keeps that guard green
    while re-installing both harms: a ``socket_connect_timeout`` that redis-py DROPS
    on the unix-socket branch (a knob that reads as wired and bounds nothing), and the
    HOST lane's retry policy in place of the embedded lane's.
    """
    from tortoise.projection import _socket_timeouts

    seen: dict = {}

    def _fake(path, **kwargs):
        seen["path"] = path
        seen.update(kwargs)
        return object()

    monkeypatch.setattr("tortoise.FalkorDB", _fake)
    mod._open_embedded(Path("/tmp/m4211-embedded-pin.db"))

    assert "socket_connect_timeout" not in seen, (
        "the SERVER-lane pair was spread onto a unix-socket client: redis-py drops "
        "socket_connect_timeout on that branch, so the kwarg is a bound that does "
        "not exist (the trap #3421 removed from projection's own embedded branch)")
    assert seen["socket_timeout"] == _socket_timeouts()[1], (
        "the embedded read bound must come from _socket_timeouts(), like the "
        "product's own embedded branch")
    assert getattr(seen.get("retry"), "_retries", None) == 1, (
        "the embedded lane's retry policy (_embedded_retry: ONE reconnect for a real "
        "transport blip) was replaced by the host lane's (_no_server_retry: zero)")


# ── #13919 review round 6 ───────────────────────────────────────────────────
def test_r6_a_writer_run_whose_counts_RAISED_is_unmeasurable_not_a_zero_delta(
        monkeypatch, tmp_path):
    """R6 (P1): an UNREADABLE delta was silently treated as a delta of zero.

    ``_delta`` emits a key only when BOTH sides are numbers, and ``_run_queries``
    turns a raising query into ``{"error": …}``. So a count that timed out left the
    key ABSENT, ``delta.get(k)`` was ``None``, and ``None == 0`` is ``False`` — the
    round-5 guard fell straight through and the run exited 0 with an empty delta,
    which ``--out <receipt> || exit 1`` accepts before overwriting the committed
    receipt. Reproduced by the reviewer at head ``cd47569012``.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:16379/m4211_hierarchy")
    err = {"error": "TimeoutError: Timeout reading from socket"}
    after_error = {k: dict(err) for k in
                   ("points_total", "turns_total", "contains_edges", "sessions_total",
                    "objects_total", "aboutObject_by_endpoint",
                    "turns_with_aboutObject", "sessions_with_aboutObject")}
    before_ok = {"points_total": 0, "turns_total": 0, "contains_edges": 0,
                 "sessions_total": 0, "objects_total": 0,
                 "aboutObject_by_endpoint": 0, "turns_with_aboutObject": 0,
                 "sessions_with_aboutObject": 0}
    seq = iter([dict(before_ok), dict(after_error)])
    monkeypatch.setattr(mod, "_run_queries", lambda g: next(seq))

    class _Graph:
        def query(self, *_a, **_k):
            return _Result([[0]])

    class _Proj:
        g = _Graph()

    class _FakeSDK:
        def __init__(self, *_a, **_k):
            pass

        def _get_proj(self):
            return _Proj()

        def create_object(self, *_a, **_k):
            return {}

        def _capture_session(self, conv, session_id=None):
            return {"ok": True, "turns": 22, "extraction_mode": "mocked",
                    "errors": [], "warnings": []}

        def close(self):
            pass

    monkeypatch.setattr("tortoise.sdk.TortoiseSDK", _FakeSDK)

    body = mod.measure_writer()
    assert body["delta_this_run"] == {}, body["delta_this_run"]
    assert body.get("unmeasurable"), (
        "an UNREADABLE delta was reported as a measurement — `delta.get(k) is None` "
        "is not `delta.get(k) == 0`")
    # A raising count is ALSO a partial read, so the stricter cycle-6 reason fires
    # first — either wording is a correct verdict, but the reason must NAME the
    # failing keys, because "could not measure" without them is unactionable.
    assert ("not readable" in body["unmeasurable"]
            or "partial read is not a measurement" in body["unmeasurable"])
    assert "turns_total" in body["unmeasurable"]
    assert "queries_failed" in body, (
        "the writer arm must publish which queries it could not run, like "
        "measure_live/measure_store do")

    out = tmp_path / "r.json"
    assert mod.main(["--arms", "writer", "--out", str(out)]) == 2


def test_r6_the_turn_discriminator_is_pinned_against_the_PRODUCTS_source():
    """R6 (P2): `TURN` was pinned only against itself.

    ``test_every_turn_named_key_carries_the_turn_discriminator`` asserts
    ``mod.TURN in queries[key]`` — the tool's own constant on both sides — so
    mutating ``TURN`` to drop its ``pointKind`` half kept all 43 cases green while
    turning the tool back into the conflation its docstring says the key prevents
    (extracted episodic MEMORY points counted as turns). The discriminator is the
    product's, so it is pinned against the product's own words.
    """
    sdk_src = (_REPO / "tortoise" / "sdk.py").read_text()
    anchor = "def _capture_turn_ids("
    assert anchor in sdk_src, "sdk.py lost _capture_turn_ids — re-derive this pin"
    doc = sdk_src.split(anchor, 1)[1].split('"""', 2)[1]
    assert "is_episodic" in doc and "pointKind" in doc, doc
    assert "pointKind='event'" in doc or "pointKind = 'event'" in doc, doc

    # …and the tool's constant carries BOTH halves of that discriminator.
    assert "is_episodic" in mod.TURN, mod.TURN
    assert "pointKind='event'" in mod.TURN.replace('"', "'"), mod.TURN


def test_r6_the_store_arm_opens_the_COPY_and_never_the_users_original(
        monkeypatch, tmp_path):
    """R6 (P2): "the original is never opened by the engine" was unpinned.

    Mutating ``db = _open_embedded(dst)`` to ``_open_embedded(src)`` — the engine
    opening the user's real store while still reporting the copy path — left all 43
    cases green: the fake engine rewrites whatever it is handed, and ``test_n3``
    hashes the original BEFORE the open, so ``source_sha256`` and
    ``copy_matches_source`` both stayed "correct". The property is stated in the
    arm's own comment, so it is asserted on the artifact that would change.
    """
    opened: list[Path] = []
    src = tmp_path / "store.db"
    src.write_bytes(b"rdb-bytes")

    def _spy(path):
        opened.append(Path(path))
        return _MutatingEngine(path)

    monkeypatch.setattr(mod, "_open_embedded", _spy)
    mod.measure_store(str(src), "tortoise")

    assert opened, "the store arm never opened an engine"
    assert opened[0] != src, (
        "the engine was handed the USER'S OWN store — the arm's contract is a "
        "read-only COPY, and an embedded RDB open rewrites the file it opens")
    assert src.read_bytes() == b"rdb-bytes", (
        "the user's original store was rewritten by this measurement")


# ── #13919 review round 7 ───────────────────────────────────────────────────
def test_r7_the_instrument_note_is_DERIVED_from_the_kinds_it_measured(monkeypatch):
    """R7 (P2): the note named ONE writer for a population the artifact shows is mixed.

    The committed receipt's `arms.instrument.note` (and this tool's source comment)
    attributed all 37,605 CONTAINS targets to
    ``tools/ask_spotcheck.seed_capture_turn_store``, while the same artifact's
    ``contains_targets_by_kind`` is event=24999 / session-transcript=12606 — and
    ``tortoise/retrieval.py`` records ``session-transcript`` as written ONLY by the
    eval ingest lane, "⚠️ NOR the ask-fixture seeder" (which hardcodes ``"k":
    "event"``). An asserted attribution is what the receipt is supposed to be cured
    of, so the note is now a function of what the arm measured.
    """
    class _MixedKinds(_FakeInstrumentGraph):
        """The population the committed receipt actually censused: turns AND chunks."""

        def query(self, cypher: str, params=None) -> _Result:
            if "pointKind, count(*)" in cypher:
                return _Result([["event", 7], ["session-transcript", 3]])
            return super().query(cypher, params)

    # ⛔ Drive the ARM, not the helper: a pin that calls `_instrument_note` directly
    # stays green when the arm goes back to a hardcoded literal (verified — the
    # first version of this test survived exactly that mutant).
    body = _instrument(monkeypatch, {"g": _MixedKinds(sessions=2, turns=7)})
    note = body["note"]
    # It can only carry these counts if it was derived from the kinds MEASURED here.
    assert "event=7" in note and "session-transcript=3" in note, note
    assert "10 total" in note, note
    # It does NOT attribute the chunk kind to the ask-fixture seeder…
    assert "not one writer" in note.lower(), note
    # …and still names the eval ingest lane, which is what actually writes them.
    assert "eval ingest lane" in note, note

    # A population of turns only: the note must not invent a chunk writer either.
    turns_only = mod._instrument_note({"event": 10})
    assert "10 event / 0 session-transcript" in turns_only, turns_only
    assert "12606" not in turns_only
    assert "eval ingest lane" in turns_only

    # An EMPTY breakdown measured nothing about writers — say so, do not attribute.
    empty = mod._instrument_note({})
    assert "says nothing" in empty, empty
    assert "eval ingest lane" not in empty


def test_r7_the_writer_queries_failed_describes_BOTH_reads(monkeypatch):
    """R7 (P2): `queries_failed` read only the AFTER side while the verdict read both.

    ``measure_writer`` publishes ``queries_failed: _failed_keys(after)`` but its
    verdict is ``_failed_keys(after) or _failed_keys(before)``, so a run whose BEFORE
    read raised produced an artifact saying ``unmeasurable: "... not readable
    (points_total, turns_total, …)"`` AND ``queries_failed: []`` — a consumer reading
    the empty list reads "all 36 queries ran".
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:16379/m4211_hierarchy")
    err = {"error": "TimeoutError: Timeout reading from socket"}
    keys = ("points_total", "turns_total", "contains_edges", "sessions_total",
            "objects_total", "aboutObject_by_endpoint", "turns_with_aboutObject",
            "sessions_with_aboutObject")
    before_raised = {k: dict(err) for k in keys}
    after_ok = {k: 0 for k in keys}
    seq = iter([dict(before_raised), dict(after_ok)])
    monkeypatch.setattr(mod, "_run_queries", lambda g: next(seq))

    class _Graph:
        def query(self, *_a, **_k):
            return _Result([[0]])

    class _Proj:
        g = _Graph()

    class _FakeSDK:
        def __init__(self, *_a, **_k):
            pass

        def _get_proj(self):
            return _Proj()

        def create_object(self, *_a, **_k):
            return {}

        def _capture_session(self, conv, session_id=None):
            return {"ok": True, "turns": 0, "extraction_mode": "mocked",
                    "errors": [], "warnings": []}

        def close(self):
            pass

    monkeypatch.setattr("tortoise.sdk.TortoiseSDK", _FakeSDK)
    body = mod.measure_writer()

    assert body.get("unmeasurable"), "an unreadable BEFORE read was not a verdict"
    assert isinstance(body["queries_failed"], dict), (
        "the writer publishes ONE side's failures; the two fields of the artifact "
        "then disagree")
    assert body["queries_failed"]["after"] == []
    assert "turns_total" in body["queries_failed"]["before"], (
        "the side that actually failed must be named — otherwise "
        "`queries_failed: []` reads as 'every query ran'")

# ══════════════════════════════════════════════════════════════════════════
# Review round 7 — the fresh-context review at 75216acaaf (PR #13919)
#
# Four findings: a re-run that measured NOTHING still exited 0 and REPLACED the
# committed receipt; the receipt itself was gated by NOTHING (a receipt-only diff
# selected no surface and no test opened it); `objects_with_gt1_turn` was a
# `*turn*`-named key with no turn discriminator; and the exit-2 contract named a
# receipt location that three of its paths do not write.
# ══════════════════════════════════════════════════════════════════════════

def test_a_census_that_inspected_NO_graph_is_unmeasurable_not_a_zero_measurement(monkeypatch):
    """Round-7 item 1. An instance with no graphs has no error to count, so the old
    checker found nothing to mark: the documented one-line re-run published
    `graphs with a turn store: 0 of 0`, all-zero totals, and exited 0."""
    body = _instrument(monkeypatch, {})
    assert body["graphs_with_turn_store"] == 0
    assert body["graphs_skipped_on_error"] == 0
    assert "unmeasurable" in body, (
        "a census of ZERO graphs published its zeros as a measurement — the same "
        "zero-shape-as-verdict defect `_is_empty` refuses for one graph")
    assert "inspected NO graph" in body["unmeasurable"]
    assert "ZERO graphs" in body["unmeasurable"]


def test_a_census_whose_every_graph_was_filtered_out_is_unmeasurable(monkeypatch):
    """Same zero, other cause: the instance has graphs but none holds a turn store."""
    body = _instrument(monkeypatch, {"g-empty": _FakeInstrumentGraph(sessions=0, turns=0)})
    assert body["graphs_on_instance"] == 1
    assert body["graphs_without_turn_store"] == 1
    assert body["graphs_with_turn_store"] == 0
    assert "unmeasurable" in body
    assert "0 of 1" in body["unmeasurable"]


def test_a_zero_graph_receipt_cannot_replace_the_committed_receipt(monkeypatch, tmp_path):
    """The harm the fix is for: the artifact of record, destroyed, reporting success."""
    good = {"issue": 4211, "arms": {"instrument": {"graphs_with_turn_store": 60}},
            "purpose": "the artifact of record"}
    out = tmp_path / "receipt.json"
    out.write_text(json.dumps(good) + "\n")
    monkeypatch.setattr(mod, "_open_live", lambda uri: (_FakeDB({}), {"graph": "x"}))
    rc = mod.main(["--arms", "instrument", "--live-uri", "docker://:@localhost:1/x",
                   "--out", str(out)])
    assert rc == 2, "a run that measured nothing reported success"
    assert json.loads(out.read_text()) == good, "the receipt of record was replaced"
    sibling = tmp_path / "receipt.unmeasurable.json"
    assert "inspected NO graph" in json.dumps(json.loads(sibling.read_text()))


# ── round-7 item 2: the committed receipt is a GATED artifact ─────────────

_RECEIPT = (_REPO / "docs" / "scoping" / "receipts"
            / "2026-11-19-4211-hierarchy-remeasure.json")


def _receipt() -> dict:
    return json.loads(_RECEIPT.read_text())


def test_a_receipt_only_diff_selects_this_pin():
    """`docs/` is a flat `NON_PYTHON_PREFIXES` entry, so a receipt-only diff selected NO
    surface — measured before the `CORE_ALSO` entry: `surfaces: []`, `full: false`, and a
    hand-edited number shipped green on the artifact the #4211 verdict is argued from."""
    from tools import ci_selection

    manifest = ci_selection.load_manifest()
    selection = ci_selection.select(
        ["docs/scoping/receipts/2026-11-19-4211-hierarchy-remeasure.json"],
        "pull_request", manifest)
    assert "core" in (selection.get("surfaces") or []), selection.get("surfaces")
    files = selection.get("test_files")
    if files == "ALL":
        return
    assert "test_measure_4211_session_hierarchy.py" in files, (
        "a receipt-only diff no longer runs the pin that reads the receipt — the "
        "`CORE_ALSO` entry for the artifact was dropped")


def test_the_committed_receipt_cannot_be_hand_edited_or_passed_off_as_current():
    """Three invariants a hand-edit or a careless re-emit cannot satisfy silently:

    1. every key the receipt publishes is a query the tool actually has (no invented
       number, no key the instrument stopped producing);
    2. every query the tool has but the receipt does NOT carry is CALIBRATED by name in
       `post_measurement_revision` — an absent measurement must be named, never just
       missing;
    3. an artifact produced by a different instrument revision than HEAD's must say so,
       so a stale receipt is distinguishable from a reproduced one.
    """
    receipt = _receipt()
    assert receipt["issue"] == 4211
    assert "unmeasurable" not in receipt, (
        "the artifact of record carries a top-level UNMEASURABLE block — it is not a "
        "measurement to argue a verdict from")
    queries = {key for key, _ in mod.QUERIES}
    calibration = json.dumps(receipt.get("post_measurement_revision") or {})
    for arm in ("store", "writer", "live"):
        counts = receipt["arms"][arm]["counts"]
        invented = sorted(set(counts) - queries)
        assert not invented, (
            f"{arm}: keys that are not queries of the instrument: {invented} — a number "
            "nobody measured, or one the tool no longer produces")
        undocumented = sorted(k for k in queries - set(counts) if k not in calibration)
        assert not undocumented, (
            f"{arm}: queries the receipt does not carry and does not calibrate: "
            f"{undocumented} — an absent measurement must be named in "
            "post_measurement_revision, or it reads as a measurement that was taken")
    live_sha = mod._sha256(mod._TOOL_PATH)
    assert receipt["instrument"]["sha256"], "the artifact does not pin its instrument"
    if receipt["instrument"]["sha256"] != live_sha:
        assert receipt.get("post_measurement_revision"), (
            "the receipt was produced by a DIFFERENT instrument revision than the one "
            "at HEAD and does not calibrate that — a stale artifact would be "
            "indistinguishable from a reproduced one")


def test_the_receipts_headline_figures_are_pinned():
    """The figures the #4211 verdict is ARGUED FROM.

    Re-measuring must update these deliberately: the test file is what a receipt-only
    diff now selects, so the update is a reviewable line instead of an invisible one.
    """
    receipt = _receipt()
    inst = receipt["arms"]["instrument"]
    assert (inst["graphs_with_turn_store"], inst["graphs_on_instance"]) == (60, 173)
    assert inst["graphs_with_zero_aboutObject"] == 60
    assert inst["totals"]["aboutObject_edges"] == 0
    assert inst["totals"]["turns"] == 24999
    assert receipt["arms"]["store"]["counts"]["sessions_total"] == 1538
    writer = receipt["arms"]["writer"]["counts"]
    assert writer["turns_with_aboutObject"] == 8
    point_edges = [row[2] for row in writer["aboutObject_by_endpoint"]
                   if row[0] == "Point"]
    assert point_edges == [9], (
        "9 Point->Object edges against 8 turns is WHY `objects_with_gt1_turn` needed "
        "the turn discriminator — if this changes, re-read that finding")


#: The attribution the receipt used to assert, and that its own numbers refute
#: (#13919 rounds 7-8). Kept as a literal so a hand-edit BACK to it is a red test.
_REFUTED_ATTRIBUTION = "those stores are written by the fixture seeder"


def test_the_receipts_instrument_note_is_RE_DERIVED_from_its_own_breakdown():
    """The note is the one field in the artifact no other pin could hold.

    Rounds 7-8 corrected it by hand in three places (the note, a comment, the module
    docstring) because they were applied to an artifact that predates the fix. "It
    happens to be byte-equal today" is not a pin: the note is now DERIVED by
    ``_instrument_note()``, so the artifact's copy is checked against that derivation
    over the artifact's OWN ``contains_targets_by_kind``. A hand-edit back to the
    refuted single-writer attribution — the sentence that sent a reader to the wrong
    writer for a third of the population — is a red test, not a silent regression.
    """
    inst = _receipt()["arms"]["instrument"]
    assert _REFUTED_ATTRIBUTION not in inst["note"], (
        "the artifact still asserts the attribution its own contains_targets_by_kind "
        "refutes")
    # ⛔ Re-derive with the SAME inputs the arm passes — the kinds AND the totals.
    # Passing the kinds alone left `aboutObject_edges` unread, which the cycle-9 fix
    # made visible (`reads None here`); a pin that calls the generator differently from
    # the arm is pinning a different string.
    derived = mod._instrument_note(
        inst["contains_targets_by_kind"],
        {"aboutObject_edges": inst["totals"]["aboutObject_edges"],
         "turns_with_aboutObject": inst["totals"]["turns_with_aboutObject"]})
    assert inst["note"] == derived, (
        "the receipt's note is NOT the derivation of the breakdown it publishes — "
        "either it was hand-edited, or it predates _instrument_note() and must be "
        "re-derived")


# ── round-7 item 4: the exit-2 contract names BOTH paths ──────────────────

def test_the_exit_contract_names_where_a_PRE_ARM_refusal_puts_its_reason(tmp_path):
    """The docstring said exit 2 ALWAYS leaves its reason in the receipt's top-level
    `unmeasurable` block. The interpreter guard and the `--arms` refusal exit 2 with NO
    receipt — a caller written to the stated contract reads a missing file."""
    doc = mod.__doc__ or ""
    assert "TOP-LEVEL" in doc
    assert "stderr" in doc and "no receipt at all" in doc, (
        "the exit-2 contract still names only the receipt, so a caller cannot tell a "
        "refusal that wrote one from a refusal that wrote nothing")
    out = tmp_path / "r.json"
    try:
        mod.main(["--arms", "crocodile", "--out", str(out)])
    except SystemExit as exc:  # argparse's refusal path
        assert exc.code == 2, "a pre-arm refusal must use this tool's could-not-measure code"
    assert not out.exists(), (
        "a pre-arm refusal wrote a receipt, which is what made the old contract read "
        "as accurate")


# ── #13919 review round 8 ───────────────────────────────────────────────────
def test_r8_the_docstring_does_not_name_ONE_writer_for_the_mixed_population():
    """R8: round 7 corrected the note, the policy comment and the receipt — and left
    the MODULE DOCSTRING (lines ~33-38), which is what ``--help`` prints and what a
    re-runner reads first, still asserting the refuted single-writer attribution.

    ``tortoise/retrieval.py`` records that ``session-transcript`` is written ONLY by
    the eval ingest lane and "⚠️ NOR the ask-fixture seeder" (which hardcodes
    ``"k": "event"``), while the artifact's ``contains_targets_by_kind`` is
    event=24999 / session-transcript=12606 — so a third of the population cannot be
    that seeder's. This pin makes the correction stick in the one place a prose fix
    forgets.
    """
    # ⛔ Pin the DOCSTRING itself, not the whole file: the word "mixed" already occurs
    # in a source comment (`# Naming one writer for a mixed population`), so a
    # whole-file scan stayed green when the docstring's own sentence was deleted —
    # the exact thing the message says it pins, and the text `--help` prints
    # (#13919 cycle 6). The sibling prose pin already reads `mod.__doc__`.
    doc = mod.__doc__ or ""
    assert "those stores are written" not in doc, (
        "the docstring still attributes the whole instrument population to one writer")
    assert "MIXED" in doc, (
        "the docstring must say the population is MIXED — a whole-file scan cannot "
        "pin this, because 'mixed' also appears in a source comment")
    # …and the module exposes the derivation it should be pointing at.
    assert hasattr(mod, "_instrument_note")


def test_r8_an_ABBREVIATED_flag_cannot_smuggle_a_credential_into_the_receipt(
        tmp_path):
    """R8: ``allow_abbrev`` defeated ``_redact_argv``'s exact-spelling match.

    argparse accepts ``--live-ur`` by default, and ``_redact_argv`` keys on the exact
    flag spelling — so the abbreviated form's value was recorded VERBATIM into
    ``instrument.argv``, which is committed under ``docs/scoping/receipts/``. Measured
    before the fix: ``main(["--arms","live","--live-ur","docker://:hunter2@h:1/g",
    "--out",…])`` wrote ``hunter2`` into the receipt.
    """
    uri = "docker://:hunter2@127.0.0.1:1/tortoise"
    out = tmp_path / "r.json"
    # argparse reports a usage error by RAISING SystemExit(2) — that is the tool's
    # documented "could not run" code, and it fires before any arm runs.
    try:
        rc = mod.main(["--arms", "live", "--live-ur", uri, "--out", str(out)])
    except SystemExit as exc:
        rc = exc.code
    assert rc == 2, (
        "an abbreviated flag must be REFUSED (argparse usage error, exit 2), not "
        "silently accepted with an unredacted value")
    assert not out.exists(), "a refused invocation must not write a receipt at all"


def test_r8_redact_argv_catches_a_URI_VALUE_with_no_recognised_flag():
    """R8, defence in depth: a bare token carrying a DB URI is redacted too.

    With ``allow_abbrev=False`` the abbreviated spelling cannot reach this, but the
    argv redaction should not depend on which flag spelled the value — a token that IS
    a credentialed URI is the thing being kept out of git.
    """
    redacted = mod._redact_argv(["--arms", "live", "docker://:hunter2@h:1/g"])
    assert "hunter2" not in " ".join(redacted), redacted
    assert redacted == ["--arms", "live", "docker://h:1/g"], redacted

    # Ordinary arguments are recorded VERBATIM — the receipt has to stay reproducible.
    plain = ["--arms", "live", "--out", "r.json", "--sessions-dir", "/tmp/x"]
    assert mod._redact_argv(plain) == plain
    # The exact flag still takes the paired-value path.
    assert mod._redact_argv(["--live-uri", "docker://:hunter2@h:1/g"]) == [
        "--live-uri", "docker://h:1/g"]


# ── #13919 review round 9 ───────────────────────────────────────────────────
def test_r9_a_PARTIAL_read_is_not_a_measurement(monkeypatch, tmp_path):
    """R9 (P1): both read arms published `queries_failed` and NOTHING consulted it.

    ``_note_unreadable_emptiness`` set ``unmeasurable`` only when ``nodes_by_label``
    ITSELF raised, so a read where that one query answered and the other 36 timed out
    published as MEASURED: exit 0, no ``.unmeasurable.json`` sibling, and
    ``--out <receipt> || exit 1`` destroyed the artifact of record. The same harm this
    file already closes for a SKIPPED arm and for a zero-graph census — the two read
    arms were the gap.
    """
    class _OneAnswerGraph(_FakeLiveGraph):
        """Answers nodes_by_label, raises on everything else (the read-bound shape)."""

        def query(self, cypher: str, params=None) -> _Result:
            if cypher.startswith("MATCH (n) UNWIND labels"):
                return _Result([["Point", 5]])
            raise TimeoutError("Timeout reading from socket")

    monkeypatch.setattr(mod, "_open_live",
                        lambda uri: (_FakeDB({"tortoise": _OneAnswerGraph()}),
                                     {"graph": "tortoise"}))
    body = mod.measure_live("docker://:@localhost:1/tortoise")
    assert isinstance(body["queries_failed"], list) and body["queries_failed"]
    assert mod._could_not_measure(body), (
        "36 of 37 queries raised and the arm reported itself MEASURED — the receipt "
        "of record would be replaced by a partial read")
    assert "partial read is not a measurement" in mod._could_not_measure(body)

    # …and the EXIT CODE, with a good receipt on disk that must survive.
    good = {"issue": 4211, "arms": {"store": {"counts": {"turns_total": 8}}}}
    out = tmp_path / "receipt.json"
    out.write_text(json.dumps(good) + "\n")
    rc = mod.main(["--arms", "live", "--live-uri", "docker://:@localhost:1/tortoise",
                   "--out", str(out)])
    assert rc == 2, "a partial read exited 0, so `|| exit 1` accepted it"
    assert json.loads(out.read_text()) == good, "the good receipt was replaced"


def test_r9_the_store_arm_is_bound_by_the_same_partial_read_verdict(monkeypatch, tmp_path):
    """R9 symmetric half: the store arm was the other site with no verdict."""
    class _StorePartial(_MutatingEngine):
        def select_graph(self, _name):
            class _G:
                def query(self, _cypher, params=None):
                    raise TimeoutError("Timeout reading from socket")
            return _G()

    src = tmp_path / "store.db"
    src.write_bytes(b"rdb-bytes")
    monkeypatch.setattr(mod, "_open_embedded", lambda path: _StorePartial(path))
    body = mod.measure_store(str(src), "tortoise")
    assert body.get("queries_failed")
    assert mod._could_not_measure(body), (
        "a store whose queries all raised reported itself measured")


def test_r9_the_corpus_counts_records_that_carry_NO_session_id(tmp_path):
    """R9 (P2): a record with no session id was silently given a SYNTHETIC one.

    It became its own session bucket and nothing counted it — so the artifact could not
    say whether `sessions` and `records_per_session` were computed over real ids or
    invented ones, and `sessions_with_gt1_turn` is inflated by every such split (one
    session's transcript arriving as N records becomes N sessions). The drain names
    `no_session_id` as a record class it must classify
    (`tortoise/session_events_drain.py`), so this is a real shape, not a hypothetical.
    The two NEIGHBOURING degradations were already counted.
    """
    d = tmp_path / "events"
    d.mkdir()
    (d / "a.jsonl").write_text("\n".join([
        json.dumps({"session_id": "real-1",
                    "conversation": [{"role": "user", "content": "hello"}]}),
        json.dumps({"conversation": [{"role": "user", "content": "no id here"}]}),
        json.dumps({"sessionId": "real-2",
                    "conversation": [{"role": "user", "content": "second"}]}),
    ]) + "\n")
    body = mod.measure_corpus(str(d))
    assert body["stats"]["records_without_session_id"] == 1, body["stats"]
    assert body["stats"]["records"] == 3
    # The record is still TALLIED (dropping it would understate the corpus) …
    assert body["sessions"] == 3, body.get("sessions")
    # … and the neighbouring degradation counter is untouched by it.
    assert body["stats"]["records_without_text"] == 0


def test_r9_per_session_is_END_STATE_not_a_mid_run_snapshot(monkeypatch):
    """R9 (P2): `per_session` rows were measured INSIDE the capture loop.

    Each row was a snapshot taken right after that session's capture, so the receipt
    refuted itself arithmetically: `contains_points_all_kinds` 9/9/7 = 25 slots against
    `points_total` 23 (the 3 `statement` slots are ONE claim Point wired into three
    sessions), so `shared_points` reading 0 for the first session cannot be its
    end-state value — it was read before the later sessions existed. A row describing a
    moment the graph will never be in again is not a row about the graph.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:16379/m4211_hierarchy")
    counts = {"points_total": 0, "turns_total": 0, "contains_edges": 0,
              "sessions_total": 0, "objects_total": 0,
              "aboutObject_by_endpoint": 0, "turns_with_aboutObject": 0,
              "sessions_with_aboutObject": 0}
    seq = iter([dict(counts), dict(counts, points_total=23, turns_total=22)])
    monkeypatch.setattr(mod, "_run_queries", lambda g: next(seq))

    order: list[str] = []

    class _Graph:
        def query(self, cypher: str, params=None):
            sid = (params or {}).get("sid")
            if sid:
                # A per-session read must see ALL THREE captures already done.
                order.append(f"{sid}:{len(captured)}")
            return _Result([[0]])

    captured: list[str] = []

    class _Proj:
        g = _Graph()

    class _FakeSDK:
        def __init__(self, *_a, **_k):
            pass

        def _get_proj(self):
            return _Proj()

        def create_object(self, *_a, **_k):
            return {}

        def _capture_session(self, conv, session_id=None):
            captured.append(session_id)
            return {"ok": True, "turns": 1, "extraction_mode": "mocked",
                    "errors": [], "warnings": []}

        def close(self):
            pass

    monkeypatch.setattr("tortoise.sdk.TortoiseSDK", _FakeSDK)
    body = mod.measure_writer()
    assert body["per_session"], "no per-session rows were measured"
    assert all(n == "3" for _sid, n in (o.split(":") for o in order)), order


def test_r9_a_turn_named_and_an_any_kind_aboutObject_key_are_both_present():
    """R9 (P2): `sessions_with_gt1_aboutObject_edge` measured distinct OBJECTS.

    `count(DISTINCT o)` collapses a double-wired Session→Object to c=1, so the key
    answered "wired to >1 object" while its NAME promised "carries >1 edge" — the
    `#12244` double-wire class the CONTAINS side already guards
    (`contains_duplicate_pairs`). The edge-count reading is now its own key, with the
    object reading and the duplicate-pair count beside it.
    """
    queries = dict(mod.QUERIES)
    assert "count(r)" in queries["sessions_with_gt1_aboutObject_edge"], (
        "the key is named after EDGES and must count edges, not distinct targets")
    assert "count(DISTINCT o)" in queries["sessions_with_gt1_aboutObject_object"]
    assert "aboutObject_duplicate_pairs" in queries
    assert "count(r)" in queries["aboutObject_duplicate_pairs"]


# ── #13919 review cycle 6 ───────────────────────────────────────────────────
def test_c6_a_writer_run_with_UNRELATED_queries_failing_is_unmeasurable(monkeypatch,
                                                                       tmp_path):
    """C6 (P1): the writer's verdict read 2 of 39 keys, so a PARTIAL read published.

    `turns_total` and `points_total` are cheap counts; `aboutObject_by_endpoint`'s
    UNWIND and `contains_targets_by_kind` can time out on a 25k-turn store — the exact
    failure the read bound exists for. When those two answered and 37 others raised,
    the delta guard passed, `_could_not_measure` skipped the arm (its `queries_failed`
    is a MAPPING, not a list), and `main` exited 0 — replacing the artifact of record
    with an object whose own counts are `{"error": …}` dicts. That is verbatim the
    round-9 harm, closed for live/store with zero tolerance and left open here.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:16379/m4211_hierarchy")
    err = {"error": "TimeoutError: Timeout reading from socket"}
    keys = [k for k, _ in mod.QUERIES]
    # The two gate keys ANSWER on both sides; everything else raises.
    gate = {"points_total": 0, "turns_total": 0}
    before = {k: (0 if k in gate else dict(err)) for k in keys}
    after = {k: (1 if k in gate else dict(err)) for k in keys}
    seq = iter([dict(before), dict(after)])
    monkeypatch.setattr(mod, "_run_queries", lambda g: next(seq))

    class _Graph:
        def query(self, *_a, **_k):
            return _Result([[0]])

    class _Proj:
        g = _Graph()

    class _FakeSDK:
        def __init__(self, *_a, **_k):
            pass

        def _get_proj(self):
            return _Proj()

        def create_object(self, *_a, **_k):
            return {}

        def _capture_session(self, conv, session_id=None):
            return {"ok": True, "turns": 1, "extraction_mode": "mocked",
                    "errors": [], "warnings": []}

        def close(self):
            pass

    monkeypatch.setattr("tortoise.sdk.TortoiseSDK", _FakeSDK)
    body = mod.measure_writer()
    assert body.get("unmeasurable"), (
        f"{len(keys) - 2} of {len(keys)} queries raised and the writer arm reported "
        "itself MEASURED — the two cheap gate keys answered, so the delta guard passed")
    assert "partial read is not a measurement" in body["unmeasurable"]
    assert mod._could_not_measure(body), (
        "main would collect this arm as measured, so exit would be 0")

    good = {"issue": 4211, "purpose": "the of-record artifact"}
    out = tmp_path / "receipt.json"
    out.write_text(json.dumps(good) + "\n")
    assert mod.main(["--arms", "writer", "--out", str(out)]) == 2
    assert json.loads(out.read_text()) == good, "the receipt of record was replaced"


def test_c6_live_publishes_no_number_it_did_not_measure():
    """C6 (P2): ``role_evidence.note`` hardcoded "the 60 sibling graphs".

    That is a measurement claim about a DIFFERENT arm, in an artifact produced by
    ``--arms live`` — which never runs that arm. Same asserted-vs-measured class as the
    ``~/.zshrc:48`` literal in the field directly above it, which F3 fixed.
    """
    monkeypatch_fake = None  # documented: this pin is textual on purpose
    assert monkeypatch_fake is None
    text = _TOOL.read_text()
    assert "the 60 sibling" not in text, (
        "a hardcoded count is asserted in an artifact that may never have measured it")
    assert "graphs_on_instance" in text, (
        "the block must publish the number it DID measure and say so")


def test_c6_the_two_event_session_keys_carry_their_own_scope():
    """C6 (P2): `event_session_ids_distinct` promised ALL events, measured ATTACHED ones.

    The Cypher filtered to events carrying an `aboutObject` edge, so the receipt
    published 1042 beside `events_total` 10526 — a 10× smaller, attachment-scoped answer
    under an unscoped name, with the scoping visible only in a source comment. The
    file's own discipline (the turn/any-kind pairs) is: carry the filter in the name, or
    publish both.
    """
    queries = dict(mod.QUERIES)
    unscoped = queries["event_session_ids_distinct"]
    attached = queries["event_attached_session_ids_distinct"]
    assert "aboutObject" not in unscoped, (
        "the UNFILTERED key must not carry the attachment filter")
    assert "aboutObject" in attached, (
        "the ATTACHED key must carry the filter its name declares")


def test_c6_the_write_refusal_names_the_SEGMENT_predicate_it_implements():
    """C6 (P2): the message described the substring match that was removed.

    ``_looks_ephemeral`` splits on ``_ - .`` and matches a SEGMENT, so ``tortoise_latest``
    is refused even though it CONTAINS ``test`` — the old message told such a caller
    their name satisfied a condition it does not.
    """
    _, refusal = mod._writer_target("docker://:@db:6379/tortoise_latest")
    assert refusal, "the fixture assumption changed: this name should be refused"
    assert "segment" in refusal.lower(), refusal
    # ⛔ The whole phrase: `tortoise_latest` CONTAINS `test` and is refused, so any
    # message that describes a CONTAINMENT test tells the caller their name satisfies a
    # condition it does not. (Asserting only the longer "must contain one of" was too
    # weak — a mutant that kept "contain one of" survived it.)
    assert "contain one of" not in refusal.lower(), (
        "the message describes a CONTAINMENT test; the predicate is a delimited SEGMENT")


# ── #13919 review cycle 7 ───────────────────────────────────────────────────
def test_c7_an_EMPTY_live_graph_is_unmeasurable_not_a_zero_measurement(monkeypatch,
                                                                       tmp_path):
    """C7 (P1): `_is_empty` returns `True` and NOTHING read it.

    `_note_unreadable_emptiness` handled only `is None` (the read RAISED), so a graph
    that was READ and holds no nodes published `sessions 0 / turns 0` as MEASURED,
    exited 0, and `--out <receipt> || exit 1` replaced the artifact of record — the
    exact state `_is_empty`'s own docstring says the caller MUST name ("a graph with no
    nodes is INDISTINGUISHABLE from a mistyped/absent graph name"). The absent-name case
    was already refused by the `GRAPH.LIST` membership check; only this one reached 0.
    """
    class _EmptyGraph(_FakeLiveGraph):
        """Read fine, holds nothing — `nodes_by_label` answers an empty list."""

        def query(self, cypher: str, params=None) -> _Result:
            if cypher.startswith("MATCH (n) UNWIND labels"):
                return _Result([])
            return _Result([[0]])

    monkeypatch.setattr(mod, "_open_live",
                        lambda uri: (_FakeDB({"tortoise": _EmptyGraph()}),
                                     {"graph": "tortoise"}))
    body = mod.measure_live("docker://:@localhost:1/tortoise")
    assert body["empty_or_absent"] is True
    assert mod._could_not_measure(body), (
        "an empty graph published its zeros as a measurement")
    assert "ZERO SHAPE" in mod._could_not_measure(body).upper()

    good = {"issue": 4211, "purpose": "the of-record artifact"}
    out = tmp_path / "receipt.json"
    out.write_text(json.dumps(good) + "\n")
    assert mod.main(["--arms", "live", "--live-uri", "docker://:@localhost:1/tortoise",
                     "--out", str(out)]) == 2
    assert json.loads(out.read_text()) == good, "the receipt of record was replaced"


def test_c7_an_EMPTY_store_graph_is_bound_by_the_same_rule(monkeypatch, tmp_path):
    """C7, second site: the store arm reaches the same branch."""
    src = tmp_path / "store.db"
    src.write_bytes(b"rdb-bytes")

    class _EmptyStore(_MutatingEngine):
        def select_graph(self, _name):
            class _G:
                def query(self, cypher, params=None):
                    if cypher.startswith("MATCH (n) UNWIND labels"):
                        return _Result([])
                    return _Result([[0]])
            return _G()

    monkeypatch.setattr(mod, "_open_embedded", lambda path: _EmptyStore(path))
    body = mod.measure_store(str(src), "tortoise")
    assert body["empty_or_absent"] is True
    assert mod._could_not_measure(body), (
        "an empty embedded graph published zeros as a measurement")


def test_c7_a_journal_of_unparseable_records_is_a_zero_shape_not_a_measurement(
        tmp_path):
    """C7, third site: files were read, no session came out, and it published `sessions: 0`.

    `measure_corpus` returns `skipped` when there are no files (no journal here), but a
    directory holding files whose every record is unparseable fell through to
    `sessions: 0 / turns_distinct: 0` and exited 0 — asserting something about the
    population from a read that yielded nothing.
    """
    d = tmp_path / "events"
    d.mkdir()
    (d / "a.jsonl").write_text("{not json\n{also not json\n")
    body = mod.measure_corpus(str(d))
    assert body["stats"]["files"] == 1
    assert body["stats"]["parse_errors"] == 2
    assert body["sessions"] == 0
    assert mod._could_not_measure(body), (
        "a journal whose every record was unparseable published `sessions: 0` as a "
        "measurement")
    assert "zero shape" in mod._could_not_measure(body).lower()

    # …and the genuinely-EMPTY directory is a SKIP (no journal here), not a zero shape.
    empty = tmp_path / "nothing"
    empty.mkdir()
    assert "skipped" in mod.measure_corpus(str(empty))


# ── #13919 review cycle 8 ───────────────────────────────────────────────────
def test_c8_a_ledger_sentence_about_a_QUERY_matches_the_query_it_describes():
    """C8 (P2): the ledger stated the opposite of the artifact's own `queries` block.

    The note for `sessions_with_gt1_aboutObject_edge` claimed its published 0 was the
    EDGE reading, while every arm's recorded Cypher for that key is
    `WITH s, count(DISTINCT o) AS c` — the OBJECT reading. That told a reader the #12244
    double-wire question was measured and answered 0, when `count(r)` was never run, so
    it shipped false comfort about a write-bug class inside the ledger whose stated job
    is to stop exactly that. Nothing caught it: the calibration pin treats the ledger as
    an opaque blob (`json.dumps(...)` for key-name membership only).

    This pin DERIVES which reading a key's recorded Cypher is, and requires the ledger's
    sentence about that key to use the same word — so a hand-edit either way is red.
    """
    receipt = _receipt()
    ledger = json.dumps(receipt.get("post_measurement_revision") or {})
    assert "sessions_with_gt1_aboutObject_edge" in ledger, (
        "the ledger must name this key, because the artifact does not answer the "
        "double-wire question")

    for arm in ("store", "writer", "live"):
        cypher = receipt["arms"][arm]["queries"]["sessions_with_gt1_aboutObject_edge"]
        # Which reading IS this Cypher? Derive it, never assume it.
        is_object_reading = "count(DISTINCT o)" in cypher
        is_edge_reading = "count(r)" in cypher
        assert is_object_reading != is_edge_reading, (
            f"{arm}: the recorded Cypher is neither a clear edge nor a clear object "
            f"reading: {cypher}")
        if is_object_reading:
            # …then the ledger must not call the published value the EDGE reading.
            assert "is the EDGE reading" not in ledger, (
                f"{arm}: the recorded Cypher is `count(DISTINCT o)` (the OBJECT "
                "reading), and the ledger calls the published 0 the EDGE reading — a "
                "reader would take the #12244 double-wire question as answered")
            assert "is the OBJECT reading" in ledger, (
                f"{arm}: the ledger must say which reading this artifact actually took")


# ── #13919 review cycle 9 ───────────────────────────────────────────────────
def test_c9_the_note_cannot_assert_an_aboutObject_count_it_did_not_read(monkeypatch):
    """C9 (P1): the note said ``0 aboutObject`` unconditionally.

    ``_instrument_note`` took only the kind breakdown, while ``measure_instrument``
    computes ``aboutObject_edges`` and ``turns_with_aboutObject`` a few lines above and
    publishes them in the SAME body. So on a population where the edge IS present — the
    very answer the #4211 gate exists to detect — the artifact would say 42 in ``totals``
    and 0 in the note explaining it. A generated field must not assert a number it did
    not read (the ``role_evidence`` / ``~/.zshrc:48`` class).
    """
    class _Attached(_FakeInstrumentGraph):
        """Holds turns AND entity attachment: 42 edges, 42 attached turns."""

        def query(self, cypher: str, params=None) -> _Result:
            if "aboutObject" in cypher and "count" in cypher:
                return _Result([[42]])
            return super().query(cypher, params)

    # Drive the ARM: a helper-only pin would stay green while the arm ignored `totals`.
    body = _instrument(monkeypatch, {"g": _Attached(sessions=3, turns=7)})
    about = body["totals"]["aboutObject_edges"]
    assert about > 0, f"the fixture must produce attachment (got {about})"
    assert "0 `aboutObject`" not in body["note"], (
        f"totals say {about} aboutObject edge(s) and the note says 0")
    assert "`aboutObject` IS present" in body["note"], body["note"]
    assert str(about) in body["note"], body["note"]


def test_c9_the_note_does_not_cite_a_chunk_writer_it_measured_NONE_of():
    """C9, second half: "not one writer's output" was unconditional too.

    On a breakdown of ``event`` only, the sentence offered ``session-transcript``
    (written only by the eval ingest lane) as its evidence of a mix while its own
    parenthetical reported that kind as 0 — naming a writer with no measured output as
    proof of a second writer.
    """
    note = mod._instrument_note({"event": 10},
                                {"aboutObject_edges": 0, "turns_with_aboutObject": 0})
    assert "session-transcript=0" in note or "10 event / 0 session-transcript" in note
    assert "not one writer's output" not in note, (
        "one kind was measured; there is no second writer to evidence")
    assert "only `event` targets" in note, note


def test_c9_a_BEFORE_side_partial_read_is_a_verdict_even_with_a_READABLE_delta(
        monkeypatch, tmp_path):
    """C9 (P2): the BEFORE half of `failed_any` had no test that could red.

    Reverting `failed_any = _failed_keys(before) or _failed_keys(after)` to the
    after-only form left the whole suite green, because the two tests that look like
    they cover it seed a failure set that ALSO makes the delta unreadable (so the next
    branch supplies the verdict) or fail BOTH sides. A before-side partial read with
    three successful captures and a readable non-zero delta is the shape that
    discriminates, and its absence reverted the cycle-6 defect.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:16379/m4211_hierarchy")
    keys = [k for k, _ in mod.QUERIES]
    err = {"error": "TimeoutError: Timeout reading from socket"}
    before = {k: (0 if k != "aboutObject_by_endpoint" else dict(err)) for k in keys}
    after = {k: 1 for k in keys}          # clean, every count readable
    seq = iter([dict(before), dict(after)])
    monkeypatch.setattr(mod, "_run_queries", lambda g: next(seq))

    class _Graph:
        def query(self, *_a, **_k):
            return _Result([[0]])

    class _Proj:
        g = _Graph()

    class _FakeSDK:
        def __init__(self, *_a, **_k):
            pass

        def _get_proj(self):
            return _Proj()

        def create_object(self, *_a, **_k):
            return {}

        def _capture_session(self, conv, session_id=None):
            return {"ok": True, "turns": 1, "extraction_mode": "mocked",
                    "errors": [], "warnings": []}

        def close(self):
            pass

    monkeypatch.setattr("tortoise.sdk.TortoiseSDK", _FakeSDK)
    body = mod.measure_writer()
    assert body["delta_this_run"].get("turns_total") == 1, (
        "the fixture's delta must be READABLE, or the next branch supplies the verdict "
        "and this pin proves nothing")
    assert body.get("unmeasurable"), (
        "one BEFORE-side query raised, the delta was readable, and the arm published "
        "itself as MEASURED")
    assert "aboutObject_by_endpoint" in body["unmeasurable"]
