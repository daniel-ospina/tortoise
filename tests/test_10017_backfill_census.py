"""#10017 — the pre-delete census must be PER SESSION, so a total cannot lie.

Measured on production (`team_7a3b5403935f865c27d3fb1751`, 2026-10-26) while
building this: 6,432 turns across 872 sessions, all ids on the
``<session>_t<index>`` shape the record key is rebuilt from, **0 embedded**
(nothing to re-encode), 0 orphans — and **21 sessions whose declared
`turn_count` disagrees with the number of turn points attached to them**
(`declared=6 attached=12` is the smallest). That last number is exactly what a
total-only census would have hidden, and it is why `verify` compares per
session and against BOTH numbers rather than trusting `turn_count`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_TOOL = Path(__file__).resolve().parent.parent / "tools" / "backfill_graph_turns.py"
_spec = importlib.util.spec_from_file_location("backfill_graph_turns", _TOOL)
assert _spec and _spec.loader
census_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(census_mod)


class _FakeGraph:
    """Returns the queued result sets in call order (sessions, turns, orphans)."""

    def __init__(self, sessions, turns, orphan_ids=()) -> None:
        self._sets = [sessions, turns, [(i,) for i in orphan_ids]]
        self.queries: list[str] = []

    def query(self, cypher: str):
        self.queries.append(cypher)

        class _R:
            def __init__(self, rows):
                self.result_set = rows

        return _R(self._sets.pop(0))


class _FakeClient:
    """The FalkorDB client surface `_assert_graph_exists` touches: GRAPH.LIST."""

    def __init__(self, names) -> None:
        self._names = list(names)
        self.calls = 0

    def list_graphs(self):
        self.calls += 1
        return list(self._names)


def test_census_is_per_session_and_flags_count_mismatch() -> None:
    sessions = [("s1", 2, "t0"), ("s2", 3, "t0"), ("s3", 0, "t0")]
    turns = [
        ("s1", "s1_t0", 10, 0),
        ("s1", "s1_t1", 20, 0),
        ("s2", "s2_t0", 5, 1),
        ("s2", "s2_t1", 5, 1),
        ("s2", "s2_t2", 5, 1),
        ("s2", "s2_t3", 5, 1),
        ("s2", "s2_t4", 5, 1),
    ]
    report = census_mod.census(_FakeGraph(sessions, turns), "g")

    assert report["per_session"]["s1"]["turns"] == 2
    assert report["per_session"]["s2"]["turns"] == 5
    assert report["per_session"]["s2"]["embedded"] == 5
    assert report["totals"]["turns"] == 7
    assert report["totals"]["chars"] == 55
    # s2 DECLARES 3 but carries 5 attached turns: reported, never silently fixed.
    assert report["count_mismatch"] == {"s2": {"declared": 3, "attached": 5}}
    assert report["totals"]["sessions_in_count_mismatch"] == 1


def test_census_counts_ids_off_the_key_shape_and_orphans() -> None:
    sessions = [("s1", 2, "t0")]
    turns = [("s1", "s1_t0", 1, 0), ("s1", "not-a-turn-key", 1, 0)]
    report = census_mod.census(
        _FakeGraph(sessions, turns, orphan_ids=["orph_b", "orph_a"]), "g")
    # The record's key is `<session>_t<i>`; an id that does not parse cannot be
    # keyed, so it must be counted — not assumed away.
    assert report["totals"]["turn_ids_not_in_key_shape"] == 1
    assert report["turn_ids_not_in_key_shape"] == ["not-a-turn-key"]
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
    sessions = [("s1", 5, "t0"), ("s2", 0, "t0")]
    report = census_mod.census(_FakeGraph(sessions, []), "g")

    assert report["count_mismatch"] == {"s1": {"declared": 5, "attached": 0}}
    assert report["totals"]["sessions_in_count_mismatch"] == 1
    assert report["sessions_without_turns"] == ["s1", "s2"]
    assert report["totals"]["sessions_without_turns"] == 2
    # Both numbers reach the artifact for BOTH sessions, so `verify` can compare.
    assert report["per_session"]["s1"] == {
        "declared": 5, "turns": 0, "chars": 0, "embedded": 0,
        "indexes_seen": 0, "first_key": None, "last_key": None,
        "non_turn_members": 0}
    # A declared 0 with nothing attached is agreement, not a mismatch.
    assert "s2" not in report["count_mismatch"]
    # and a bucket with no turns must never inflate the "with turns" count
    assert report["totals"]["sessions_with_turns"] == 0


def test_a_point_that_is_not_this_sessions_turn_is_not_counted_as_one() -> None:
    """The extraction lane CONTAINS-wires non-turn Points into a :Session.

    Counting them as turns turns a real loss into "no mismatch": a session that
    declares 1 turn and holds one claim Point would read as complete.
    """
    sessions = [("s3", 1, "t0"), ("s1", 2, "t0")]
    turns = [
        ("s3", "claim-abc", 99, 1),          # episodic, attached, not a turn
        ("s1", "s1_t0", 10, 0),
        ("s1", "s2_t99", 7, 0),              # ANOTHER session's turn id
    ]
    report = census_mod.census(_FakeGraph(sessions, turns), "g")

    assert report["per_session"]["s3"]["turns"] == 0
    assert report["per_session"]["s3"]["non_turn_members"] == 1
    assert report["per_session"]["s3"]["chars"] == 0
    # A bucket is not a turn: "with turns" counts TURNS, so a session holding
    # only non-turn members is not one of them.
    assert report["totals"]["sessions_with_turns"] == 1
    assert "s3" in report["sessions_without_turns"]
    assert report["count_mismatch"]["s3"] == {"declared": 1, "attached": 0}
    # A foreign-session id must not poison the bucket it is attached to: s1 holds
    # ONE of its own turns, and `s2_t99` must not set its high-water mark.
    assert report["per_session"]["s1"]["turns"] == 1
    assert report["per_session"]["s1"]["indexes_seen"] == 0
    assert report["per_session"]["s1"]["last_key"] == "s1_t0"
    assert report["per_session"]["s1"]["non_turn_members"] == 1
    assert report["count_mismatch"]["s1"] == {"declared": 2, "attached": 1}
    assert sorted(report["turn_ids_not_in_key_shape"]) == ["claim-abc", "s2_t99"]


def test_census_is_read_only() -> None:
    """The census phase must contain no mutation: it runs on production."""
    graph = _FakeGraph([("s1", 1, "t0")], [("s1", "s1_t0", 1, 0)])
    census_mod.census(graph, "g")
    import re

    # Word-boundary, because `s.created_at` contains "CREATE" as a substring.
    mutation = re.compile(r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DROP)\b")
    for cypher in graph.queries:
        assert mutation.search(cypher.upper()) is None, cypher


def test_census_issues_no_query_before_the_graph_is_known_to_exist() -> None:
    """A read against a missing graph CREATES its keyspace — the phase's one
    real write. `GRAPH.LIST` itself creates nothing, so it is the gate."""
    client = _FakeClient(["other_graph"])
    with pytest.raises(SystemExit) as exc:
        census_mod._assert_graph_exists(client, "typo_graph")
    assert "does not exist" in str(exc.value)
    assert client.calls == 1


def test_the_graph_existence_gate_admits_a_real_graph_and_an_explicit_opt_in() -> None:
    census_mod._assert_graph_exists(_FakeClient(["team_x"]), "team_x")
    # the escape hatch is explicit, and it is the CALLER that accepts the write
    census_mod._assert_graph_exists(_FakeClient([]), "typo_graph",
                                    create_if_missing=True)



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
    # Unicode digits are NOT the record's key: record_read._turn_index refuses
    # `[0-9]+`-only, so `\d` would count a key that cannot be rebuilt.
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
