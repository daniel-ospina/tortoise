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

    def __init__(self, sessions, turns, orphans=0) -> None:
        self._sets = [sessions, turns, [(orphans,)]]
        self.queries: list[str] = []

    def query(self, cypher: str):
        self.queries.append(cypher)

        class _R:
            def __init__(self, rows):
                self.result_set = rows

        return _R(self._sets.pop(0))


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
    report = census_mod.census(_FakeGraph(sessions, turns, orphans=3), "g")
    # The record's key is `<session>_t<i>`; an id that does not parse cannot be
    # keyed, so it must be counted — not assumed away.
    assert report["totals"]["turn_ids_not_in_key_shape"] == 1
    assert report["totals"]["orphan_turns"] == 3


def test_census_is_read_only() -> None:
    """The census phase must contain no mutation: it runs on production."""
    graph = _FakeGraph([("s1", 1, "t0")], [("s1", "s1_t0", 1, 0)])
    census_mod.census(graph, "g")
    import re

    # Word-boundary, because `s.created_at` contains "CREATE" as a substring.
    mutation = re.compile(r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DROP)\b")
    for cypher in graph.queries:
        assert mutation.search(cypher.upper()) is None, cypher


@pytest.mark.parametrize("turn_id,session,ok", [
    ("s_t0", "s", True),
    ("01a082cd-41bb-715b-bac3-223225301397_t12",
     "01a082cd-41bb-715b-bac3-223225301397", True),
    ("s_t0", "other", False),   # parses, but for a different session
    ("s_t", "s", False),
    ("s", "s", False),
])
def test_turn_id_shape_parser(turn_id: str, session: str, ok: bool) -> None:
    m = census_mod._TURN_ID_RE.match(turn_id)
    assert (m is not None and m.group("session") == session) is ok
