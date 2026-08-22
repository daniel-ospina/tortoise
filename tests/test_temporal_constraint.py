"""R5 (#1544) — TR-constraint detection unit tests.

Covers the shapes the issue names (\"between…and…\", \"ago\", \"how many
days\") plus the degradation rules from the R5 plan D5:

  * ``interval`` — explicit date bounds → hard time-window filter
  * ``recency`` — an N-day/week/month bound → window [qdate − N, qdate]
  * ``ordering`` — the question needs the FULL dated set to compute a
    span/ordering (no bound → no filter): \"how many days\", \"how long\",
    \"when did\", bare \"ago\" with no numeric bound
  * ``None`` — no temporal shape → no filter, no reorder (pure date weight)
"""
from __future__ import annotations

from tools.longmem_eval.retrieve import detect_time_constraint


def test_between_dates_interval():
    c = detect_time_constraint("Between June 1 and June 15, what happened?",
                               default_year=2025)
    assert c.kind == "interval" and c.start == "2025-06-01" and c.end == "2025-06-15"


def test_between_iso_dates_interval():
    c = detect_time_constraint("What happened between 2025-06-01 and 2025-06-15?")
    assert c.kind == "interval" and c.start == "2025-06-01" and c.end == "2025-06-15"


def test_recency_n_days_ago():
    c = detect_time_constraint("How many days ago did Ava tell you she adopted a dog?")
    # no NUMERIC bound ("many" is not a day count) → the question needs the
    # full dated set to compute the span — ordering, not a window filter
    # (D5 table: bare \"ago\" with no bound → ordering).
    assert c.kind == "ordering" and c.start is None


def test_recency_numeric_days_ago():
    c = detect_time_constraint("She told me 5 days ago.")
    assert c.kind == "recency" and c.start == "5" and c.end is None


def test_recency_weeks_and_months_ago():
    c = detect_time_constraint("I adopted a dog 2 weeks ago.")
    assert c.kind == "recency" and c.start == "14"
    c = detect_time_constraint("I moved here 3 months ago.")
    assert c.kind == "recency" and c.start == "90"


def test_last_n_weeks_recency():
    assert detect_time_constraint("What did the user do in the last 2 weeks?").kind == "recency"
    c = detect_time_constraint("What did the user do in the last 2 weeks?")
    assert c.start == "14"


def test_how_many_days_ordering():
    assert detect_time_constraint("How many days between X and Y?").kind == "ordering"


def test_no_match_returns_none():
    assert detect_time_constraint("What is the user's preferred coffee order?").kind is None


def test_unparseable_bound_degrades_to_ordering():
    c = detect_time_constraint("Something happened ago but we do not know when")
    assert c.kind == "ordering"


def test_when_did_ordering():
    assert detect_time_constraint("When did Ava tell you about the trip?").kind == "ordering"
