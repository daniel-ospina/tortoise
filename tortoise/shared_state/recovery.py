"""Crash recovery: scan incomplete cards on startup, event ID dedup.

At-least-once semantics. Recovery finds cards with no completion event and
re-plays from the last gatePassed checkpoint. Event ID dedup prevents
double-processing on replay.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


def scan_incomplete_cards(card_dir: Path, suffix: str = ".card"
                    ) -> list[dict]:
    """Find cards that were claimed but never completed.

    Returns list of card data dicts for recovery processing.
    Does NOT modify files — caller decides what to do.

    A card is "incomplete" if it exists and has no `completed_at`
    field in its card file — detection is FIELD-based, not event-log
    based: the event log is never consulted here (a cardCompleted event
    in the log does not mark a card complete; only the card file's
    `completed_at` field does).
    """
    if not card_dir.exists():
        return []
    incomplete: list[dict] = []
    for cf in sorted(card_dir.glob(f"*{suffix}")):
        # Skip temp files (from partial atomic_claim)
        if cf.name.startswith("."):
            continue
        try:
            data = json.loads(cf.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        # #331: incomplete = no 'completed_at' field (field-based).
        # Callers that need event-log confirmation do their own lookup.
        if "completed_at" not in data:
            incomplete.append(data)
    return incomplete


def dedup_events(events: list[dict]) -> list[dict]:
    """Remove duplicate events by event_id, keeping first occurrence.

    For at-least-once replay: if the same event_id appears multiple times
    (e.g., after crash recovery + re-emit), only keep the first.
    Events without an event_id are always included.
    """
    seen: set[str] = set()
    out: list[dict] = []
    for ev in events:
        eid = ev.get("event_id")
        if eid is None:
            out.append(ev)
        elif eid not in seen:
            seen.add(eid)
            out.append(ev)
    return out


def find_last_checkpoint(log_path: Path
                         ) -> Optional[dict]:
    """Find the last gatePassed event in the event log.

    This is the recovery point: replay from here after a crash.
    Per research brief §Tension 2: gate events ARE checkpoints.
    """
    if not log_path.exists():
        return None
    last: Optional[dict] = None
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "gatePassed":
                last = ev
    return last


# --- self-check ---
if __name__ == "__main__":
    import tempfile, os

    d = Path(tempfile.mkdtemp())
    cards = d / "cards"
    cards.mkdir()

    # Incomplete: card exists with no completed_at
    (cards / "c1.card").write_text(json.dumps({"card_id": "c1", "owner": "a1"}))
    # Complete: has completed_at
    (cards / "c2.card").write_text(json.dumps({"card_id": "c2", "completed_at": 123}))

    incomplete = scan_incomplete_cards(cards)
    assert len(incomplete) == 1
    assert incomplete[0]["card_id"] == "c1"

    # Dedup
    events = [
        {"event_id": "ev-1", "x": 1},
        {"event_id": "ev-1", "x": 2},  # dup
        {"event_id": "ev-2", "x": 3},
        {"x": 4},  # no event_id — always included
    ]
    deduped = dedup_events(events)
    assert len(deduped) == 3  # ev-1 once, ev-2, and the no-id event
    assert deduped[0]["x"] == 1  # first occurrence kept

    # find_last_checkpoint
    log = d / "events.jsonl"
    log.write_text('\n'.join([
        json.dumps({"type": "cardStarted", "id": "ev-1"}),
        json.dumps({"type": "gatePassed", "gate": "review"}),
        json.dumps({"type": "cardCompleted", "id": "ev-2"}),
    ]))
    cp = find_last_checkpoint(log)
    assert cp is not None
    assert cp["gate"] == "review"

    print("✅ recovery")
    import shutil; shutil.rmtree(d)
