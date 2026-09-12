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


def recover_from_log(events_dir: str, projection) -> dict:
    """Rebuild a projection from a JSONL event-log dir when its graph was lost.

    Corruption recovery (#428): the event log is the source of truth, the
    projection a derived view. An embedded DB that answers 0 nodes while its
    adjacent JSONL log has events was lost — redislite starts fresh when its
    RDB is corrupt, an interrupted restore left an empty graph, or the DB was
    deleted out from under the log. Rebuild = wipe + full replay.

    Safety (mirrors migrate_db's 3-way discriminator):
      - Only rebuilds when db has 0 total nodes and the log has > 0 events
        (the "lost DB" case). Partial divergence (0 < db < log) is left
        alone — the graph may hold SDK-created points that never appear in
        the log; a rebuild would destroy them. db >= log is healthy (the log
        is append-only).
      - Only rebuilds from an UNambiguous log: exactly one adjacent .jsonl.
        Multiple logs could be mid-restore artifacts (backup copy + live
        log); auto-rebuilding the wrong one loses data, so we refuse.
      - Replays faithfully via projection.apply() (same path as restore) —
        NOT rebuild_all, which drops context for v2+ events (#49). A
        lossy rebuild is worse than no recovery for a transparent path.
      - Query/log failures are caught and reported in the result, never
        raised — the caller decides fail-loud policy. Torn trailing lines
        (crash mid-append) are skipped, not fatal.

    Returns {recovered, log_points, db_points, reason}.
    """
    import json as _json
    import os

    def _node_count() -> int | None:
        try:
            rows = projection.query("MATCH (n) RETURN count(n)").result_set
            return int(rows[0][0]) if rows and rows[0][0] is not None else 0
        except Exception:
            return None

    db_count = _node_count()
    if db_count is None:
        return {"recovered": False, "log_points": 0, "db_points": None,
                "reason": "graph unresponsive — recovery requires a live DB"}
    if db_count > 0:
        return {"recovered": False, "log_points": 0, "db_points": db_count,
                "reason": "graph already has nodes — no rebuild"}

    # db_count == 0: enumerate the adjacent logs (exactly one required).
    try:
        files = sorted(f for f in os.listdir(events_dir)
                       if f.endswith(".jsonl"))
    except OSError as e:
        return {"recovered": False, "log_points": 0, "db_points": 0,
                "reason": f"event-log dir unreadable: {e}"}
    if not files:
        return {"recovered": False, "log_points": 0, "db_points": 0,
                "reason": "no JSONL event log present"}
    if len(files) > 1:
        return {"recovered": False, "log_points": 0, "db_points": 0,
                "reason": f"ambiguous: {len(files)} adjacent JSONL logs "
                           f"({', '.join(files[:3])}...) — refusing auto-rebuild"}

    # Parse the single log, tolerating a torn trailing line.
    log_path = os.path.join(events_dir, files[0])
    events: list[dict] = []
    torn = 0
    try:
        with open(log_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(_json.loads(line))
                except Exception:
                    torn += 1
    except OSError as e:
        return {"recovered": False, "log_points": 0, "db_points": 0,
                "reason": f"event log unreadable: {e}"}
    if not events:
        return {"recovered": False, "log_points": 0, "db_points": 0,
                "reason": "event log empty or unreadable — nothing to recover"}

    # Faithful replay via the apply-based engine (preserves context; the
    # backup restore fallback uses the same path). #2977: routing through
    # apply_replay also defers + folds the Object terminal-status families,
    # which a bare apply() loop silently skipped.
    #
    # TWO replay counters, SEPARATE from each other AND from the parse-time
    # `torn` above: a per-event apply failure is a tolerated legacy-line skip
    # (the pre-#2977 contract), a FOLD failure means the durability fix did not
    # apply, and a torn trailing line is a tolerated crash artifact. Collapsing
    # them makes the first two look fatal (verified live: a single un-appliable
    # EventRecorded flipped `recovered` to False and would have made
    # `_recover_or_raise` refuse to open an embedded DB).
    applied, replay_apply_torn, replay_fold_torn = projection.apply_replay(events)
    after = _node_count()
    # A folded-but-retained (tombstoned) node and an unfolded live node are
    # indistinguishable by node count alone. Until they are distinguishable, a
    # non-zero REPLAY tear means the recovery is NOT trustworthy — and this is
    # the embedding startup auto-recovery lane, where silence resurrects data.
    # Parse tears stay tolerated (`torn` above).
    ok = (applied > 0 and after is not None and after > 0
          and replay_fold_torn == 0)
    _skipped = torn + replay_apply_torn
    return {"recovered": ok, "log_points": len(events),
            "db_points": after if after is not None else 0,
            # The counts are reported on BOTH branches — putting them only in
            # the ok=True form loses them on exactly the path this change is
            # ABOUT (fold_torn > 0 → ok False), and so does the RuntimeError
            # `_recover_or_raise` raises from it.
            "reason": (f"replayed {applied} events from {files[0]}"
                       + (f" ({_skipped} skipped)" if _skipped else "")
                       + (f" ({replay_fold_torn} fold(s) failed)"
                          if replay_fold_torn else "")) if ok else
                      (f"replay produced {applied} events from {files[0]} "
                       f"({_skipped} skipped, {replay_fold_torn} fold(s) "
                       f"failed) — graph empty or fold failed")}
