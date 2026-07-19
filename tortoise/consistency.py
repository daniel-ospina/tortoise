"""Startup consistency check — verify event log Point count matches graph.

The event log is the source of truth; the projection is a derived view.
This module verifies the counts haven't diverged (quick check, not full diff).
"""
from __future__ import annotations

from .projection import fold


def check_consistency(log_path: str, projection) -> dict:
    """Fold the event log and compare Point count against the graph.

    projection must have .query(cypher) returning an object with .result_set.
    Returns {ok, log_points, db_points, delta}.
    """
    from .log import EventLog

    log = EventLog(log_path)
    points = fold(log.read_all())
    log_count = len(points)

    db_rows = projection.query(
        "MATCH (n:Point) RETURN count(n)"
    ).result_set
    db_count = db_rows[0][0] if db_rows else 0

    return {
        "ok": log_count == db_count,
        "log_points": log_count,
        "db_points": db_count,
        "delta": log_count - db_count,
    }
