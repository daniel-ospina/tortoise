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
    """Answers ONLY the node-label read, so every other query records an ERROR."""

    def __init__(self, fail_nodes: bool = False):
        self.fail_nodes = fail_nodes

    def query(self, cypher: str, params=None) -> _Result:
        if cypher.startswith("MATCH (n) UNWIND labels"):
            if self.fail_nodes:
                raise TimeoutError("nodes read timed out")
            return _Result([["Point", 5]])
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


_TURN_KEYS = ("sessions_with_gt1_contains_turn",
              "sessions_with_gt1_entity_linked_turn",
              "turns_per_session_contains_max_avg")


# ── P1: the keys named `*turn*` must count TURNS ──────────────────────────

def test_every_turn_named_key_carries_the_turn_discriminator():
    queries = dict(mod.QUERIES)
    for key in _TURN_KEYS:
        assert mod.TURN in queries[key], (
            f"{key} is named after turns but its Cypher applies no turn discriminator — "
            "it counts claim/transcript Points as turns")


def test_the_any_kind_reading_is_reported_beside_the_turn_reading():
    queries = dict(mod.QUERIES)
    assert "sessions_with_gt1_contains_point" in queries
    assert "is_episodic" not in queries["sessions_with_gt1_contains_point"], (
        "the any-kind key must stay UNFILTERED — it is the visible other half of the "
        "divergence the turn keys report")


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
    assert mod.main(["--arms", "store", "--out", str(out)]) == 0
    receipt = json.loads(out.read_text())
    assert receipt["instrument"]["measured_through"] == digests
    # a skip recorded only inside the arm body is now also a top-level warning
    assert any(w.startswith("store: skipped") for w in receipt["warnings"])


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
    for name in ("measure_store", "measure_live", "measure_instrument",
                 "measure_corpus", "measure_writer"):
        monkeypatch.setattr(mod, name, lambda *a, _n=name, **k: (
            ran.append(_n) or {"skipped": "faked"}))
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
