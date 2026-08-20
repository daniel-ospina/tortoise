"""JSONL event log — append, replay, integrity verification.


Composes with concurrency.locked_append and events.EventCodec.
Event types: cardCreated, stepStarted, stepCompleted, gatePassed, gateBlocked.
Registered via PM domain extension (packs/project-management/manifest.yaml).
"""
from __future__ import annotations  # noqa: I001

import hashlib
import json
from pathlib import Path
from typing import Any

from .concurrency import locked_append


# ── Append ────────────────────────────────────────────────────

def append_event(
    log_path: Path,
    event_type: str,
    data: dict[str, Any],
    *,
    event_id: str | None = None,
) -> bool:
    """Append an event to the JSONL log under flock.

    Dedup by event_id: if an event with the same ID already exists,
    returns True without writing (idempotent). Returns False on lock timeout.
    """
    from .events import EventCodec

    if event_id and _event_id_exists(log_path, event_id):
        return True  # ponytail: idempotent — already recorded, nothing to do

    record = EventCodec.encode(event_type, data, event_id=event_id)
    return locked_append(log_path, record)


def _event_id_exists(log_path: Path, event_id: str) -> bool:
    """Check if an event_id already exists in the log."""
    if not log_path.exists():
        return False
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                if ev.get("event_id") == event_id:
                    return True
            except json.JSONDecodeError:
                continue
    return False


# ── Replay ────────────────────────────────────────────────────

def replay_events(log_path: Path) -> list[dict[str, Any]]:
    """Read and decode all events from the log. Returns parsed event dicts."""
    from .events import EventCodec
    return EventCodec.read_all(str(log_path))


# ── Integrity ─────────────────────────────────────────────────

def verify_hash(
    log_path: Path, expected_sha256: str | None = None
) -> tuple[bool, str]:
    """Verify SHA-256 integrity of the event log.

    Sorts events by (timestamp, event_id) before hashing for determinism.
    Returns (valid, computed_hash).
    """
    h = hashlib.sha256()
    if log_path.exists():
        events = replay_events(log_path)
        events.sort(key=lambda e: (
            e.get("timestamp", 0),
            e.get("event_id", ""),
        ))
        for ev in events:
            h.update(json.dumps(ev, sort_keys=True, default=str).encode())
    computed = h.hexdigest()
    if expected_sha256 is not None:
        return (computed == expected_sha256, computed)
    return (True, computed)


# ── Crash Recovery ────────────────────────────────────────────

def scan_incomplete(
    log_path: Path,
    expected_cards: set[str],
) -> list[str]:
    """Find cards with no completion event (cardCompleted or cardFailed).

    Returns list of card IDs that have been created but not completed.
    """
    completed: set[str] = set()
    if log_path.exists():
        for ev in replay_events(log_path):
            card_id = ev.get("card_id", "")
            if card_id and ev.get("type", "") in ("cardCompleted", "cardFailed"):
                completed.add(card_id)
    return sorted(expected_cards - completed)


def recover(
    log_path: Path,
    card_id: str,
    retry_fn,
) -> dict[str, Any]:
    """Replay log up to card, identify last completed step, and retry.

    retry_fn(card_id, last_step_id | None) is called to resume execution.
    Returns the result of retry_fn.
    """
    events = replay_events(log_path) if log_path.exists() else []

    # Find this card's events, scan for last completed step
    last_step: str | None = None
    for ev in reversed(events):
        if ev.get("card_id") != card_id:
            continue
        if ev.get("type", "") == "stepCompleted":
            last_step = ev.get("step_id")
            break

    return retry_fn(card_id, last_step)


# ── self-check ────────────────────────────────────────────────

if __name__ == "__main__":
    import sys, tempfile, os, shutil  # noqa: E401, I001
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from .events import register_event_type

    register_event_type("cardCreated")
    register_event_type("stepStarted")
    register_event_type("stepCompleted")
    register_event_type("gatePassed")
    register_event_type("gateBlocked")
    register_event_type("cardCompleted")
    register_event_type("cardFailed")

    d = Path(tempfile.mkdtemp())
    log = d / "events.jsonl"

    # Append + dedup
    assert append_event(log, "cardCreated", {"card_id": "c1"}, event_id="ev-1")
    assert append_event(log, "cardCreated", {"card_id": "c1"}, event_id="ev-1")  # idempotent
    assert append_event(log, "stepStarted", {"card_id": "c1", "step_id": "s1"}, event_id="ev-2")
    assert append_event(log, "stepCompleted", {"card_id": "c1", "step_id": "s1"}, event_id="ev-3")
    assert append_event(log, "cardCompleted", {"card_id": "c1"}, event_id="ev-4")
    # Incomplete card
    assert append_event(log, "cardCreated", {"card_id": "c2"}, event_id="ev-5")

    # Replay — 5 unique events (ev-1 dedup'd on second append)
    events = replay_events(log)
    assert len(events) == 5, f"expected 5, got {len(events)}"
    assert events[0]["card_id"] == "c1"

    # Hash
    ok, h = verify_hash(log)
    assert ok
    ok2, h2 = verify_hash(log, expected_sha256=h)
    assert ok2
    # Wrong hash fails
    ok3, _ = verify_hash(log, expected_sha256="deadbeef")
    assert not ok3

    # Scan incomplete
    incomplete = scan_incomplete(log, {"c1", "c2"})
    assert incomplete == ["c2"], f"expected ['c2'], got {incomplete}"

    # Recover
    def fake_retry(cid: str, last_step_id: str | None) -> dict[str, Any]:
        return {"card_id": cid, "last_step": last_step_id}

    result = recover(log, "c2", fake_retry)
    assert result["card_id"] == "c2"
    assert result["last_step"] is None  # no steps completed for c2

    result2 = recover(log, "c1", fake_retry)
    assert result2["last_step"] == "s1"

    print("✅ event_log")
    shutil.rmtree(d)
