"""#10017 — the pre-delete census must be PER SESSION, so a total cannot lie.

Measured on production (`team_7a3b5403935f865c27d3fb1751`, 2026-10-26) while
building this: 6,432 turns across 872 sessions, all ids on the
``<session>_t<index>`` shape the record key is rebuilt from, **0 embedded**
(nothing to re-encode), 0 orphans — and **21 sessions whose declared
`turn_count` disagrees with the number of turn points attached to them**
(`declared=6 attached=12` is the smallest). That last number is exactly what a
total-only census would have hidden, and it is why `verify` compares per
session and against BOTH numbers rather than trusting `turn_count`.

The census reports MEASUREMENTS, not causes: a disagreement between the declared
window length and the attached turns is not attributed to anything here (a
journal+record deployment sets `turn_count` while deliberately putting no turns
in the graph), and a turn's `pointKind` divergence from what the product's
readers require is counted rather than silently filtered.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

_TOOL = Path(__file__).resolve().parent.parent / "tools" / "backfill_graph_turns.py"
_spec = importlib.util.spec_from_file_location("backfill_graph_turns", _TOOL)
assert _spec and _spec.loader
census_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(census_mod)


class _FakeGraph:
    """Returns the queued result sets in call order (sessions, turns, orphans).

    Sessions rows are ``(id, turn_count, turn_max_index)``; turn rows are
    ``(session_id, point_id, chars, is_embedded, point_kind)``.
    """

    def __init__(self, sessions, turns, orphan_ids=()) -> None:
        self._sets = [sessions, turns, [(i,) for i in orphan_ids]]
        self.queries: list[str] = []

    def query(self, cypher: str):
        self.queries.append(cypher)

        class _R:
            def __init__(self, rows):
                self.result_set = rows

        return _R(self._sets.pop(0))


class _FakeDB:
    """The client surface `main()` touches, recording the ORDER of the calls."""

    def __init__(self, graph, names=("g",)) -> None:
        self._graph = graph
        self._names = list(names)
        self.calls: list[str] = []

    def list_graphs(self):
        self.calls.append("list_graphs")
        return list(self._names)

    def select_graph(self, name):
        self.calls.append(f"select_graph:{name}")
        return self._graph


def _run_main(monkeypatch, db, argv):
    monkeypatch.setattr(census_mod, "_connect",
                        lambda uri: (db, "redis://db.example.com:6379"))
    monkeypatch.setenv("CENSUS_TEST_URI", "redis://db.example.com:6379")
    return census_mod.main(list(argv))


def test_census_is_per_session_and_flags_count_mismatch() -> None:
    sessions = [("s1", 2, 1), ("s2", 3, 3), ("s3", 0, None)]
    turns = [
        ("s1", "s1_t0", 10, 0, "event"),
        ("s1", "s1_t1", 20, 0, "event"),
        ("s2", "s2_t0", 5, 1, "event"),
        ("s2", "s2_t1", 5, 1, "event"),
        ("s2", "s2_t2", 5, 1, "event"),
        ("s2", "s2_t3", 5, 1, "event"),
        ("s2", "s2_t4", 5, 1, "event"),
    ]
    report = census_mod.census(_FakeGraph(sessions, turns), "g")

    assert report["per_session"]["s1"]["turns"] == 2
    assert report["per_session"]["s2"]["turns"] == 5
    assert report["per_session"]["s2"]["embedded"] == 5
    assert report["totals"]["turns"] == 7
    assert report["totals"]["chars"] == 55
    # s2 DECLARES 3 but carries 5 attached turns: reported, never silently fixed.
    assert report["count_mismatch"] == {"s2": {
        "declared": 3, "attached": 5,
        "declared_max_index": 3, "max_index_seen": 4}}
    assert report["totals"]["sessions_in_count_mismatch"] == 1
    # s1 runs 0..1 with no hole; s2 runs 0..4 with no hole either.
    assert report["sessions_with_id_space_holes"] == []


def test_census_counts_ids_off_the_key_shape_and_orphans() -> None:
    sessions = [("s1", 2, 1)]
    turns = [("s1", "s1_t0", 1, 0, "event"), ("s1", "not-a-turn-key", 1, 0, "event")]
    report = census_mod.census(
        _FakeGraph(sessions, turns, orphan_ids=["orph_b", "orph_a"]), "g")
    # The record's key is `<session>_t<i>`; an id that does not parse cannot be
    # keyed, so it must be counted — and NAMED — not assumed away.
    assert report["totals"]["turn_ids_off_shape"] == 1
    assert report["turn_ids_off_shape"] == ["not-a-turn-key"]
    # Orphans are MEASURED, not just counted: an empty id list beside a non-zero
    # count would read to `verify` as "no orphan ids".
    assert report["totals"]["orphan_turns"] == 2
    assert report["orphan_turn_ids"] == ["orph_b", "orph_a"]


def test_a_session_that_declares_turns_and_holds_none_is_a_mismatch() -> None:
    """The `872 of 873 declared` case: the census must not drop it.

    Driven from `per_session` alone, a session with `turn_count=5` and no turn
    points appears nowhere — so the worst instance of the class it exists to
    report would be invisible, and `verify` would have no id to compare.
    """
    sessions = [("s1", 5, 4), ("s2", 0, None)]
    report = census_mod.census(_FakeGraph(sessions, []), "g")

    assert report["count_mismatch"] == {"s1": {
        "declared": 5, "attached": 0, "declared_max_index": 4,
        "max_index_seen": 0}}
    assert report["totals"]["sessions_in_count_mismatch"] == 1
    assert report["sessions_without_turns"] == ["s1", "s2"]
    assert report["totals"]["sessions_without_turns"] == 2
    # The printed class is the SUBSET that actually declares turns — `s2` holds
    # none and declares none, which is a different (and uninteresting) thing.
    assert report["sessions_declared_and_empty"] == ["s1"]
    assert report["totals"]["sessions_declared_and_empty"] == 1
    # Both declared numbers reach the artifact for BOTH sessions.
    assert report["per_session"]["s1"] == {
        "declared": 5, "declared_max_index": 4, "turns": 0, "chars": 0,
        "embedded": 0, "indexes_seen": 0, "first_index": None,
        "first_key": None, "last_index": None, "last_key": None,
        "non_turn_members": 0, "turns_not_point_kind_event": 0,
        "id_space_hole": False}
    # A declared 0 with nothing attached is agreement, not a mismatch.
    assert "s2" not in report["count_mismatch"]
    # and a bucket with no turns must never inflate the "with turns" count
    assert report["totals"]["sessions_with_turns"] == 0


def test_a_point_that_is_not_this_sessions_turn_is_not_counted_as_one() -> None:
    """The extraction lane CONTAINS-wires non-turn Points into a :Session.

    Counting them as turns turns a real loss into "no mismatch": a session that
    declares 1 turn and holds one claim Point would read as complete.
    """
    sessions = [("s3", 1, 0), ("s1", 2, 1)]
    turns = [
        ("s3", "claim-abc", 99, 1, "claim"),   # episodic, attached, not a turn
        ("s1", "s1_t0", 10, 0, "event"),
        ("s1", "s2_t99", 7, 0, "event"),       # ANOTHER session's turn id
    ]
    report = census_mod.census(_FakeGraph(sessions, turns), "g")

    assert report["per_session"]["s3"]["turns"] == 0
    assert report["per_session"]["s3"]["non_turn_members"] == 1
    assert report["per_session"]["s3"]["chars"] == 0
    # A bucket is not a turn: "with turns" counts TURNS, so a session holding
    # only non-turn members is not one of them.
    assert report["totals"]["sessions_with_turns"] == 1
    assert "s3" in report["sessions_without_turns"]
    assert report["count_mismatch"]["s3"] == {
        "declared": 1, "attached": 0, "declared_max_index": 0,
        "max_index_seen": 0}
    # A foreign-session id must not poison the bucket it is attached to: s1 holds
    # ONE of its own turns, and `s2_t99` must not set its high-water mark.
    assert report["per_session"]["s1"]["turns"] == 1
    assert report["per_session"]["s1"]["indexes_seen"] == 0
    assert report["per_session"]["s1"]["last_key"] == "s1_t0"
    assert report["per_session"]["s1"]["non_turn_members"] == 1
    assert report["count_mismatch"]["s1"] == {
        "declared": 2, "attached": 1, "declared_max_index": 1,
        "max_index_seen": 0}
    # The two findings are DIFFERENT and are reported apart: an off-shape id
    # cannot be keyed at all, a foreign id keys fine but to the wrong session.
    assert report["turn_ids_off_shape"] == ["claim-abc"]
    assert report["turn_ids_of_another_session"] == ["s2_t99"]
    assert report["totals"]["turn_ids_off_shape"] == 1
    assert report["totals"]["turn_ids_of_another_session"] == 1


def test_an_id_space_hole_is_reported_even_when_the_count_agrees() -> None:
    """`turn_count` is a WINDOW LENGTH; the indices are the ID SPACE (#10000).

    A session that lost its middle turns — ids 0,1,5,6 — declares 4 and holds 4,
    so a count-only check calls it clean. `turn_max_index` is read for exactly
    this, and it is the number the stale-turn sweep needs.
    """
    sessions = [("s9", 4, 6)]
    turns = [("s9", f"s9_t{i}", 1, 0, "event") for i in (0, 1, 5, 6)]
    report = census_mod.census(_FakeGraph(sessions, turns), "g")

    assert report["count_mismatch"] == {}          # the count agrees …
    assert report["per_session"]["s9"]["indexes_seen"] == 6
    assert report["per_session"]["s9"]["id_space_hole"] is True
    assert report["sessions_with_id_space_holes"] == ["s9"]   # … the ids do not
    assert report["totals"]["sessions_with_id_space_holes"] == 1
    # first/last are the ids at the LOWEST/HIGHEST INDEX, not lexicographic order
    assert report["per_session"]["s9"]["first_key"] == "s9_t0"
    assert report["per_session"]["s9"]["last_key"] == "s9_t6"


def test_first_and_last_key_follow_the_index_not_the_sort_order() -> None:
    """`s1_t9` sorts AFTER `s1_t10`; the artifact must not report that as last."""
    sessions = [("s1", 11, 10)]
    turns = [("s1", f"s1_t{i}", 1, 0, "event") for i in range(11)]
    turns = sorted(turns, key=lambda r: r[1])     # exactly what ORDER BY p.id gives
    bucket = census_mod.census(_FakeGraph(sessions, turns), "g")["per_session"]["s1"]
    assert bucket["last_key"] == "s1_t10"
    assert bucket["last_index"] == 10
    assert bucket["first_key"] == "s1_t0"
    assert bucket["id_space_hole"] is False


def test_turns_not_carrying_point_kind_event_are_measured_not_filtered() -> None:
    """The product's readers disagree about `pointKind`; the census measures it.

    `sdk._capture_turn_ids` and `session_schema.audit_session_retrofit` require
    `pointKind = 'event'`; the record's own turn read does not. Silently
    filtering here could zero a legacy census, and silently keeping them could
    report a graph as complete that the SDK's reader calls empty — so the
    divergence is a NUMBER in the artifact.
    """
    sessions = [("s1", 2, 1)]
    turns = [("s1", "s1_t0", 1, 0, "event"), ("s1", "s1_t1", 1, 0, "claim")]
    report = census_mod.census(_FakeGraph(sessions, turns), "g")

    assert report["per_session"]["s1"]["turns"] == 2          # both key cleanly
    assert report["per_session"]["s1"]["turns_not_point_kind_event"] == 1
    assert report["totals"]["turns_not_point_kind_event"] == 1


def test_an_unconvertible_index_is_reported_not_raised() -> None:
    """One malformed row must not abort a census of a production graph.

    `int()` raises on a digit run past CPython's conversion limit, and
    `sdk._capture_turn_ids` records that trap for the same reason. The id must
    land in `turn_ids_off_shape` — NAMED — instead of a bare ValueError that
    aborts the run and does not say which row did it.
    """
    huge = "s1_t" + "1" * 5000
    sessions = [("s1", 1, 0)]
    turns = [("s1", "s1_t0", 1, 0, "event"), ("s1", huge, 1, 0, "event")]
    report = census_mod.census(_FakeGraph(sessions, turns), "g")

    assert report["turn_ids_off_shape"] == [huge]
    assert report["per_session"]["s1"]["turns"] == 1
    assert report["per_session"]["s1"]["non_turn_members"] == 1


def test_census_is_read_only() -> None:
    """The census phase must contain no mutation: it runs on production."""
    graph = _FakeGraph([("s1", 1, 0)], [("s1", "s1_t0", 1, 0, "event")])
    census_mod.census(graph, "g")
    assert len(graph.queries) == 3
    for cypher in graph.queries:
        # Stronger than a keyword scan: EVERY statement is a MATCH-shaped read,
        # and none carries a CALL (which is how a write hides without a word
        # boundary — `CALL db.idx.fulltext.createNodeIndex(...)`).
        assert cypher.lstrip().upper().startswith("MATCH"), cypher
        assert not re.search(r"\bCALL\b", cypher.upper()), cypher
        assert not re.search(r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DROP)\b",
                             cypher.upper()), cypher


def test_the_statements_measure_what_the_report_claims() -> None:
    """The fake graph cannot catch a query that stops measuring the right thing.

    Dropping `is_episodic` from the turns query, or the `NOT ... CONTAINS` from
    the orphan query, changes every number on a real graph and would leave a
    result-set-driven test green — so the predicates are pinned here.
    """
    graph = _FakeGraph([("s1", 1, 0)], [("s1", "s1_t0", 1, 0, "event")])
    census_mod.census(graph, "g")
    sessions_q, turns_q, orphan_q = graph.queries
    assert "s.turn_count" in sessions_q and "s.turn_max_index" in sessions_q
    assert "is_episodic" in turns_q and ":CONTAINS]" in turns_q
    assert "p.pointKind" in turns_q, "the pointKind divergence would be invisible"
    assert "is_episodic" in orphan_q and "NOT (:Session)-[:CONTAINS]->(p)" in orphan_q


# ═══════════════════════════════════════════════════════════════════════════
# main() — the refusals, and the ORDER that keeps the artifact honest
# ═══════════════════════════════════════════════════════════════════════════

def test_main_checks_existence_before_it_selects_or_queries(monkeypatch) -> None:
    graph = _FakeGraph([("s1", 1, 0)], [("s1", "s1_t0", 1, 0, "event")])
    db = _FakeDB(graph)
    assert _run_main(monkeypatch, db, ["census", "--graph", "g",
                                       "--uri-env", "CENSUS_TEST_URI"]) == 0

    assert db.calls == ["list_graphs", "select_graph:g"], (
        "GRAPH.LIST must run before the graph is selected: a read against a "
        "missing graph CREATES its keyspace")
    assert len(graph.queries) == 3


def test_main_refuses_a_missing_graph_without_issuing_one_query(monkeypatch) -> None:
    graph = _FakeGraph([("s1", 1, 0)], [("s1", "s1_t0", 1, 0, "event")])
    db = _FakeDB(graph, names=("other",))
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, db, ["census", "--graph", "typo",
                                    "--uri-env", "CENSUS_TEST_URI"])
    assert "does not exist" in str(exc.value)
    assert db.calls == ["list_graphs"]
    assert graph.queries == []


def test_main_admits_a_missing_graph_only_with_the_explicit_opt_in(monkeypatch) -> None:
    graph = _FakeGraph([("s1", 1, 0)], [("s1", "s1_t0", 1, 0, "event")])
    db = _FakeDB(graph, names=())
    assert _run_main(monkeypatch, db, ["census", "--graph", "typo",
                                       "--uri-env", "CENSUS_TEST_URI",
                                       "--create-if-missing"]) == 0
    assert db.calls == ["list_graphs", "select_graph:typo"]


def test_main_refuses_a_sessionless_census_and_writes_no_artifact(
        monkeypatch, tmp_path) -> None:
    """An all-zero census is what a wrong graph looks like too."""
    graph = _FakeGraph([], [])
    out = tmp_path / "census.json"
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, _FakeDB(graph),
                  ["census", "--graph", "g", "--uri-env", "CENSUS_TEST_URI",
                   "--out", str(out)])
    assert "no :Session nodes" in str(exc.value)
    assert not out.exists(), "the refusal must precede the --out write"


def test_main_writes_the_artifact_once_the_census_is_positive(
        monkeypatch, tmp_path) -> None:
    graph = _FakeGraph([("s1", 1, 0)], [("s1", "s1_t0", 1, 0, "event")])
    out = tmp_path / "census.json"
    assert _run_main(monkeypatch, _FakeDB(graph),
                     ["census", "--graph", "g", "--uri-env", "CENSUS_TEST_URI",
                      "--out", str(out)]) == 0
    import json

    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["totals"]["turns"] == 1
    assert report["totals"]["sessions_declared"] == 1


def test_main_never_echoes_a_uri_pasted_into_uri_env(monkeypatch) -> None:
    """`--uri-env` is the natural place to paste a URI by mistake."""
    secret = "rediss://user:SUPERSECRETPW@db.example.com:6379"
    monkeypatch.setattr(census_mod, "_connect",
                        lambda uri: pytest.fail("must not connect"))
    with pytest.raises(SystemExit) as exc:
        census_mod.main(["census", "--graph", "g", "--uri-env", secret])
    assert "SUPERSECRETPW" not in str(exc.value)
    assert "not the URI" in str(exc.value)


def test_main_names_an_unset_variable_but_never_its_value(monkeypatch) -> None:
    monkeypatch.delenv("NO_SUCH_URI_VAR", raising=False)
    monkeypatch.setattr(census_mod, "_connect",
                        lambda uri: pytest.fail("must not connect"))
    with pytest.raises(SystemExit) as exc:
        census_mod.main(["census", "--graph", "g",
                         "--uri-env", "NO_SUCH_URI_VAR"])
    # the variable NAME is the useful part, and it is safe to print
    assert "NO_SUCH_URI_VAR" in str(exc.value)
    assert "not set" in str(exc.value)


# ═══════════════════════════════════════════════════════════════════════════
# The turn-id rule
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("turn_id,session,ok", [
    ("s_t0", "s", True),
    ("01a082cd-41bb-715b-bac3-223225301397_t12",
     "01a082cd-41bb-715b-bac3-223225301397", True),
    ("s_t0", "other", False),   # parses, but for a different session
    ("s_t", "s", False),
    ("s", "s", False),
    # A session id that itself ends in `_t<i>`: GREEDY, so the last index wins
    # and this is s_t3's turn 7 — not `sess`'s turn 3. Non-greedy would make one
    # session's turns prefix-match another's (the trap sdk._capture_turn_ids
    # documents).
    ("sess_t3_t7", "sess_t3", True),
    ("sess_t3_t7", "sess", False),
    # Unicode digits are NOT the record's key: record_read._turn_index accepts
    # `[0-9]+` only, so `\d` would count a key that cannot be rebuilt.
    ("s_t\u0663", "s", False),
])
def test_turn_id_shape_parser(turn_id: str, session: str, ok: bool) -> None:
    m = census_mod._TURN_ID_RE.match(turn_id)
    assert (m is not None and m.group("session") == session) is ok


def test_the_id_rule_is_stricter_than_the_records_own_reader() -> None:
    """One definition of the ID, and the census is never LOOSER than the record.

    The census accepts only ids the record accepts (subset): what it keys, the
    record can key. It is deliberately STRICTER on the prefix-collision case —
    the record's `startswith(session_id + "_t")` admits `sess_t3_t7` for session
    `sess` (it also matches `_t7` at the end), while this census attributes that
    id to `sess_t3`, the session that actually mints it. That direction is the
    safe one: over-reporting a member as not-this-session's-turn can only make
    the census refuse more, never manufacture a false "complete".
    """
    from tortoise.record_read import RecordReadError, _turn_index

    for turn_id, session in [("s_t0", "s"), ("sess_t3_t7", "sess_t3"),
                             ("s_t12", "s")]:
        m = census_mod._TURN_ID_RE.match(turn_id)
        assert m is not None and m.group("session") == session
        assert int(m.group("index")) == _turn_index(turn_id, session)

    # the unicode-digit id: the record REFUSES it, so the census must not key it
    with pytest.raises(RecordReadError):
        _turn_index("s_t\u0663", "s")
    assert census_mod._TURN_ID_RE.match("s_t\u0663") is None

    # PINNED DIVERGENCE (over-report, never under-report): the record reader is
    # LOOSER here — it will key s_t3's turn 7 to `sess`. The census must NOT be
    # "fixed" toward that, or one session's turns would satisfy another's count.
    assert _turn_index("sess_t3_t7", "sess") == 7
    m = census_mod._TURN_ID_RE.match("sess_t3_t7")
    assert m is None or m.group("session") != "sess"
