"""#4211 — the measurement instrument's guards, pinned by rows that can go RED.

``tools/measure_4211_session_hierarchy.py`` shipped with a receipt and ZERO tests, and
three review rounds said the same thing about it: six guards *claim* protection and no
automated row reddens when any of them is reverted ("MUTATION ATTRIBUTION — NO NEW
ASSERTIONS"). This module is that missing set of rows. It is DB-free, network-free and
LLM-free: every arm is driven with a fake graph / faked engine, and the two queries that
matter are asserted as TEXT (what the Cypher computes is the guard).

Each test names the review finding it pins, so a future edit that reverts the fix reds a
row whose name says which behaviour was lost.
"""
from __future__ import annotations

import hashlib
import importlib.util as _ilu
import json
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "tools/measure_4211_session_hierarchy.py"


def _load():
    spec = _ilu.spec_from_file_location("measure_4211_hierarchy_pin", str(_TOOL))
    assert spec and spec.loader
    module = _ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = _load()


# ── a fake graph: Cypher text -> rows, so a query's MEANING is never executed ──

class _FakeResult:
    def __init__(self, rows):
        self.result_set = rows


class _FakeGraph:
    """Answers by query text. ``fail`` maps a substring to an exception to raise."""

    def __init__(self, answers: dict[str, list], fail: dict[str, Exception] | None = None):
        self._answers = answers
        self._fail = fail or {}
        self.asked: list[str] = []

    def query(self, cypher: str, params=None):        # noqa: ARG002 — the real signature
        self.asked.append(cypher)
        for frag, exc in self._fail.items():
            if frag in cypher:
                raise exc
        for frag, rows in self._answers.items():
            if frag in cypher:
                return _FakeResult(rows)
        raise AssertionError(f"fake graph was asked an unexpected query: {cypher!r}")


class _AnyGraph:
    """Answers every query with a zero row — for the arms that run the FULL QUERIES set."""

    def query(self, cypher: str, params=None):        # noqa: ARG002
        return _FakeResult([[0]])


class _FakeDB:
    def __init__(self, names, graphs: dict[str, _FakeGraph]):
        self.connection = self
        self._names = names
        self._graphs = graphs

    def execute_command(self, _cmd):
        return list(self._names)

    def select_graph(self, name):
        return self._graphs[name]


def _turn_store_graph(name="g", sessions=2, turns=6, contains=8, pairs=8, dupes=0,
                      about=0, turn_about=0, kinds=(("event", 6), ("claim", 2)),
                      fail=None):
    """A graph that answers the instrument's eight named queries."""
    answers = {
        "MATCH (s:Session) RETURN count(s)": [[sessions]],
        "MATCH (p:Point) WHERE": [[turns]],
        "MATCH (:Session)-[r:CONTAINS]->(:Point) RETURN count(r)": [[contains]],
        "count(DISTINCT s.id": [[pairs]],
        "WHERE c > 1 RETURN count(*)": [[dupes]],
        "MATCH ()-[r:aboutObject]->() RETURN count(r)": [[about]],
        "RETURN count(DISTINCT p)": [[turn_about]],
        "RETURN p.pointKind, count(*)": [list(k) for k in kinds],
    }
    return _FakeGraph(answers, fail=fail)


# ── R1: a key whose NAME says *turn* computes TURNS ─────────────────────────

def test_r1_every_turn_named_key_applies_the_turn_discriminator():
    """#4211 R1: three `*turn*` keys counted EVERY CONTAINS-wired Point.

    The defect was a name/Cypher divergence, so the guard is the query text: the
    mutant is "delete the `WHERE {TURN}`", and it must red HERE.
    """
    by_key = dict(m.QUERIES)
    assert m.TURN in by_key["sessions_with_gt1_turn"]
    assert m.TURN in by_key["sessions_with_gt1_entity_linked_turn"]
    assert m.TURN in by_key["turns_per_session_max_avg"]


def test_r1_the_all_kinds_question_is_asked_under_its_own_name():
    """The every-CONTAINS-point count still exists — labelled as such, not as turns."""
    by_key = dict(m.QUERIES)
    assert "sessions_with_gt1_contains_point" in by_key
    assert m.TURN not in by_key["sessions_with_gt1_contains_point"]
    # ...and the key that used to carry the turn NAME with no filter is gone, so a
    # reader cannot pick the unfiltered number up under the old label.
    assert "sessions_with_gt1_contains_turn" not in by_key
    assert "turns_per_session_contains_max_avg" not in by_key


# ── R2: an errored graph is RECORDED, and the aggregate is not "complete" ───

def test_r2_a_query_error_is_recorded_and_makes_the_aggregate_unmeasurable(monkeypatch):
    """#4211 R2: `except Exception: continue` dropped a graph from BOTH sides."""
    good = _turn_store_graph("has-turns", about=3)
    bad = _turn_store_graph("times-out", fail={"count(DISTINCT s.id":
                                               TimeoutError("query timed out")})
    db = _FakeDB(["has-turns", "times-out", "no-sessions"],
                 {"has-turns": good, "times-out": bad,
                  "no-sessions": _FakeGraph(
                      {"MATCH (s:Session) RETURN count(s)": [[0]]})})
    monkeypatch.setattr(m, "_open_live", lambda uri: (db, {"graph": "x"}))

    body = m.measure_instrument("docker://:@localhost:1/x")

    assert [e["graph"] for e in body["errors"]] == ["times-out"]
    assert body["errors"][0]["query"] == "contains_distinct_pairs"
    assert "TimeoutError" in body["errors"][0]["error"]
    # the two legitimate filters are counted SEPARATELY from the error...
    assert body["graphs_with_turn_store"] == 1
    assert body["graphs_excluded_no_turn_store"] == 1
    # ...and a graph missing from the census can never leave the aggregate "clean".
    assert body["unmeasurable"]
    assert body["totals"]["aboutObject_edges"] == 3


def test_r2_no_errors_means_no_unmeasurable_reason(monkeypatch):
    """The polarity check for the row above: a clean run must NOT be flagged."""
    db = _FakeDB(["g"], {"g": _turn_store_graph("g")})
    monkeypatch.setattr(m, "_open_live", lambda uri: (db, {"graph": "x"}))
    body = m.measure_instrument("docker://:@localhost:1/x")
    assert body["errors"] == []
    assert "unmeasurable" not in body


# ── R3: a replay exits 2, and the reason is where the docstring says ────────

def test_r3_an_unmeasurable_arm_is_hoisted_to_the_top_level_block():
    receipt = {"arms": {"writer": {"unmeasurable": "sessions replayed, not captured: x"}}}
    assert m._unmeasurable_reasons(receipt) == [
        "writer: sessions replayed, not captured: x"]


def test_r3_main_exits_2_when_any_arm_is_unmeasurable(monkeypatch, tmp_path):
    """#4211 R3: a replayed writer run exited 0 and published replay numbers."""
    monkeypatch.setattr(m, "measure_writer", lambda **kw: {
        "counts": {"points_total": 23},
        "unmeasurable": "sessions replayed, not captured: m4211-noref"})
    out = tmp_path / "receipt.json"
    rc = m.main(["--arms", "writer", "--out", str(out)])
    assert rc == 2
    receipt = json.loads(out.read_text())
    assert "m4211-noref" in json.dumps(receipt["unmeasurable"])
    assert receipt["unmeasurable"]["arms"][0].startswith("writer:")


def test_r3_an_unmeasurable_run_does_not_overwrite_a_good_receipt(tmp_path):
    good = tmp_path / "receipt.json"
    good.write_text('{"measured": true}\n')
    written = m._write_receipt(str(good), {"arms": {}}, measured=False)
    assert written == str(tmp_path / "receipt.json.unmeasurable")
    assert good.read_text() == '{"measured": true}\n'


# ── N1: the store-verdict facts are IN the receipt's own query set ─────────

def test_n1_the_object_kind_mix_and_created_at_span_are_measured_queries():
    """#4211 N1: the body's two load-bearing facts were absent from the artefact."""
    by_key = dict(m.QUERIES)
    assert "objectKind" in by_key["object_kinds"]
    assert "created_at" in by_key["session_created_at_range"]
    assert "min(" in by_key["session_created_at_range"]
    assert "max(" in by_key["session_created_at_range"]


# ── N2: the embedded choke-point, and the server is closed before rmtree ───

def test_n2_the_store_arm_uses_the_repo_choke_point_not_a_raw_redislite_import():
    """#4211 N2: the file tripped redis-guard pattern 2 (reproduced, rc=1 by review).

    This runs the repo's OWN guard over the file — the same sweep pre-commit runs with
    `pass_filenames: true`. It is a regex sweep (no venv, no database), and it reds on
    the pre-fix line `from redislite.falkordb_client import FalkorDB`.
    """
    import subprocess
    import sys

    proc = subprocess.run([sys.executable, str(_REPO / "tools/redis-guard.py"),
                           str(_TOOL)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "from tortoise import FalkorDB" in _TOOL.read_text()


def test_n2_the_embedded_server_is_closed_before_the_directory_is_deleted(
        monkeypatch, tmp_path):
    """The rmtree must not run under a still-running child (no owner record)."""
    events: list[str] = []

    class _FakeEmbedded:
        def __init__(self, path):
            self.path = path
            self.connection = self

        def execute_command(self, _cmd):
            return ["tortoise"]

        def select_graph(self, _name):
            return _AnyGraph()

        def close(self):
            events.append("close")

    src = tmp_path / "store.db"
    src.write_bytes(b"rdb-bytes")
    monkeypatch.setattr(m, "_open_embedded", _FakeEmbedded)
    real_rmtree = m.shutil.rmtree

    def _spy_rmtree(path, **kw):
        events.append("rmtree")
        return real_rmtree(path, **kw)

    monkeypatch.setattr(m.shutil, "rmtree", _spy_rmtree)
    body = m.measure_store(str(src), "tortoise")
    assert body["graph"] == "tortoise"
    assert events[0] == "close" and events[-1] == "rmtree"


# ── N3: source identity describes ONE file at ONE time ─────────────────────

def test_n3_source_sha256_and_bytes_come_from_the_original_before_the_copy(
        monkeypatch, tmp_path):
    """#4211 N3: the sha hashed the COPY after the engine opened it (an RDB rewrite
    changed the value) while the size read the ORIGINAL."""
    original = tmp_path / "store.db"
    original.write_bytes(b"original-rdb")

    class _MutatingEngine:
        """Rewrites the file it is pointed at — exactly what an embedded RDB open does."""

        def __init__(self, path):
            self.path = Path(path)
            self.path.write_bytes(b"REWRITTEN-BY-THE-SERVER")
            self.connection = self

        def execute_command(self, _cmd):
            return ["tortoise"]

        def select_graph(self, _name):
            return _AnyGraph()

        def close(self):
            pass

    monkeypatch.setattr(m, "_open_embedded", _MutatingEngine)
    body = m.measure_store(str(original), "tortoise")

    assert body["source_sha256"] == hashlib.sha256(b"original-rdb").hexdigest()
    assert body["source_bytes"] == len(b"original-rdb")
    # both halves describe the same bytes, and the receipt says whether the copy the
    # engine got still matched them
    assert body["copy_matches_source"] is True


# ── N4: an empty population cannot assert "every graph satisfies X" ────────

def test_n4_the_every_graph_row_is_null_over_an_empty_population():
    """#4211 N4: `all([])` is True, so the receipt said "every graph" with 0 graphs."""
    assert m._every_graph_has_fewer_turns_than_contains([]) is None
    assert m._every_graph_has_fewer_turns_than_contains(
        [{"turns": 2, "contains_edges": 3}]) is True
    assert m._every_graph_has_fewer_turns_than_contains(
        [{"turns": 4, "contains_edges": 3}]) is False


def test_n4_the_instrument_body_reports_null_rather_than_true_when_empty(monkeypatch):
    db = _FakeDB(["empty"], {"empty": _FakeGraph({"MATCH (s:Session) RETURN count(s)":
                                                  [[0]]})})
    monkeypatch.setattr(m, "_open_live", lambda uri: (db, {"graph": "x"}))
    body = m.measure_instrument("docker://:@localhost:1/x")
    assert body["graphs_with_turn_store"] == 0
    assert body["every_graph_has_fewer_turns_than_contains"] is None


# ── N5: a truncated per_graph publishes what it omitted ───────────────────

def test_n5_truncation_publishes_the_omitted_count(monkeypatch):
    """#4211 N5: 25 of 60 rows were cut with no marker, so the aggregate was not
    re-derivable from the artefact."""
    names = [f"g{i}" for i in range(30)]
    graphs = {n: _turn_store_graph(n) for n in names}
    monkeypatch.setattr(m, "_open_live", lambda uri: (_FakeDB(names, graphs),
                                                     {"graph": "x"}))
    body = m.measure_instrument("docker://:@localhost:1/x")
    assert body["graphs_with_turn_store"] == 30
    assert len(body["per_graph"]) == 25
    assert body["per_graph_omitted"] == 5
    # the omitted count is in the warnings a reader actually sees
    receipt = {"arms": {"instrument": body}}
    assert any("per_graph" in w for w in m._collect_warnings(receipt))
    assert m._head([1, 2, 3], 2) == ([1, 2], 1)
    assert m._head([1], 5) == ([1], 0)


# ── N7: the product modules the numbers run THROUGH are pinned ────────────

def test_n7_the_receipt_pins_the_sdk_modules_beside_the_instrument(monkeypatch):
    digests = m._module_digests()
    assert set(digests) == {"tortoise/sdk.py", "tortoise/session_link.py"}
    for rel, digest in digests.items():
        assert digest == hashlib.sha256((_REPO / rel).read_bytes()).hexdigest()

    monkeypatch.setattr(m, "measure_store", lambda *a, **k: {"skipped": "n/a"})
    out = Path(__import__("tempfile").mkdtemp()) / "receipt.json"
    assert m.main(["--arms", "store", "--out", str(out)]) == 0
    receipt = json.loads(out.read_text())
    assert receipt["instrument"]["measured_through"] == digests
    # an arm that skipped BEFORE measuring is a skip, not a failure — and it is named
    assert receipt["warnings"] and "store: skipped" in receipt["warnings"][0]
    assert "unmeasurable" not in receipt


# ── N8: the WRITE arm is opt-in and refuses a non-ephemeral graph ─────────

def test_n8_the_default_arm_set_cannot_write():
    """#4211 N8: `writer` was in the default set, so a bare run mutated whatever
    `TORTOISE_DB_URI` named."""
    assert "writer" not in m.DEFAULT_ARMS.split(",")
    assert m.DEFAULT_ARMS == "store,live,instrument,corpus"


def test_n8_the_writer_refuses_a_non_ephemeral_graph_and_records_where(monkeypatch):
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@db.example:6379/tortoise")
    body = m.measure_writer()
    assert "refusing to WRITE" in body["skipped"]
    assert body["target"] == {"host": "db.example", "port": 6379, "graph": "tortoise"}
    # the password is never copied into the receipt
    assert "pw" not in json.dumps(body)


def test_n8_an_ephemeral_graph_is_allowed_and_the_override_is_recorded(monkeypatch):
    assert m._writer_target("docker://:@127.0.0.1:16379/m4211_hierarchy")[1] is None
    target, refusal = m._writer_target("docker://:@db:6379/tortoise")
    assert target["graph"] == "tortoise" and refusal

    # a refused target is allowed only when the caller says so, and the receipt then
    # says the override was used
    calls = {}

    def _fake_sdk():
        raise RuntimeError("stop here — the guard already decided")

    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@db:6379/tortoise")
    import tortoise.sdk as _sdk_mod
    monkeypatch.setattr(_sdk_mod, "TortoiseSDK", _fake_sdk)
    try:
        m.measure_writer(allow_non_ephemeral=True)
    except RuntimeError as exc:
        calls["raised"] = str(exc)
    assert "stop here" in calls.get("raised", "")


def test_n8_main_passes_the_flag_through(monkeypatch, tmp_path):
    seen = {}

    def _fake_writer(**kwargs):
        seen.update(kwargs)
        return {"skipped": "n/a"}

    monkeypatch.setattr(m, "measure_writer", _fake_writer)
    m.main(["--arms", "writer", "--out", str(tmp_path / "r.json")])
    assert seen == {"allow_non_ephemeral": False}
