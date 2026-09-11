"""Startup consistency check — verify event log Point count matches graph.

The event log is the source of truth; the projection is a derived view.
This module verifies the counts haven't diverged (quick check, not full diff).
"""
from __future__ import annotations

from .projection import fold, prewipe_snapshot_path


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
        NOT rebuild_all, whose replay chain is a different two-pass
        implementation. A lossy rebuild is worse than no recovery for a
        transparent path.
      - EXCEPTION (#2943): for the 0-node case this function handles, a
        durable pre-wipe snapshot sidecar (a previous rebuild_all was
        interrupted after its wipe) routes recovery through
        projection.rebuild_all instead, because that is the only path that
        re-merges graph-only Points / :Batch markers — the apply()-only
        replay cannot restore them (the JSONL has no event for them by
        definition). This is CONTINUOUS with the interrupted run rather than
        a substitution: a sidecar is written only by a rebuild_all that was
        in flight for this very directory, so completing it with rebuild_all
        reproduces exactly the replay the operator asked for. (A sidecar can
        also outlive a COMPLETED rebuild if its retirement could not be
        written; it is then entry-less and this route does not fire — see
        `_clear_prewipe_snapshot`.) Either way the #428 single-log
        discriminator still governs the route (it is a destructive
        wipe+replay, so an ambiguous log set is still refused), and the
        db_count > 0 early return above is unchanged: a PARTIALLY replayed
        graph keeps its sidecar (nothing is lost) for a later explicit
        rebuild — this function never rebuilds a non-empty graph.
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

    # #2943: a durable pre-wipe snapshot next to the log means a previous
    # rebuild_all was interrupted after its wipe — graph-only Points (and
    # :Batch markers) live only in that sidecar. Only rebuild_all re-merges
    # it; the apply()-only replay below would rebuild from the JSONL alone
    # and destroy them permanently (their defining property is that the
    # JSONL has no event for them). Reached only with db_count == 0 — a
    # partially replayed graph keeps the sidecar and is left alone, above.
    #
    # The route is a destructive wipe+replay, so the #428 single-log
    # discriminator applies: with an AMBIGUOUS log set (more than one
    # adjacent .jsonl) we refuse and leave the sidecar in place for an
    # explicit `tortoise rebuild --dir <dir>`. Zero logs is NOT ambiguous —
    # the sidecar is then the only record of anything, and rebuild_all
    # replays it without a journal (the documented #428 ">0 events" clause is
    # knowingly waived here, and only while a sidecar is pending).
    if os.path.lexists(prewipe_snapshot_path(events_dir)):
        if len(files) > 1:
            return {"recovered": False, "log_points": 0, "db_points": 0,
                    "reason": (f"pending pre-wipe snapshot but {len(files)} "
                               f"adjacent JSONL log(s) — refusing to "
                               f"auto-rebuild from an ambiguous log set "
                               f"(#2943/#428); run `tortoise rebuild --dir "
                               f"{events_dir}`")}
        if not callable(getattr(projection, "rebuild_all", None)):
            return {"recovered": False, "log_points": 0, "db_points": 0,
                    "reason": ("pending pre-wipe snapshot needs "
                               "projection.rebuild_all, which "
                               f"{type(projection).__name__} does not "
                               "provide — refusing to replay the JSONL "
                               "alone (#2943)")}
        try:
            counts = projection.rebuild_all(events_dir)
            nodes = int(counts.get("nodes") or 0)
            events = int(counts.get("events") or 0)
            edges = int(counts.get("edges") or 0)
        except Exception as e:
            return {"recovered": False, "log_points": 0, "db_points": 0,
                    "reason": ("rebuild from the pending pre-wipe snapshot "
                               f"failed: {e}")}
        # rebuild_all RAISES on failure, so reaching here IS a completed
        # recovery — not `nodes > 0`: a snapshot can legitimately carry only
        # the #990 half (a quarantined :Batch with no Points), and reporting
        # that as `recovered: False` makes the caller (`_recover_or_raise`)
        # refuse to open a DB whose quarantine state was just restored.
        return {"recovered": True,
                "log_points": events,
                "db_points": nodes,
                "reason": ("rebuilt from the pending pre-wipe snapshot "
                           f"(#2943): {nodes} nodes, {edges} edges")}

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

    # Faithful replay via apply() (preserves context; restore uses the same
    # path). Per-event guard: one bad event must not abort the whole recovery.
    applied = 0
    for ev in events:
        try:
            projection.apply(ev)
            applied += 1
        except Exception:
            torn += 1
    after = _node_count()
    ok = applied > 0 and after is not None and after > 0
    return {"recovered": ok, "log_points": len(events),
            "db_points": after if after is not None else 0,
            "reason": f"replayed {applied} events from {files[0]}"
            + (f" ({torn} skipped)" if torn else "") if ok
            else "replay produced an empty graph"}
