"""Type-based event versioning — each event type carries its own version.

No global schemaVersion. Each type evolves independently with its own upcaster
chain. Per research brief: upcasters deployed BEFORE new event versions.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable, Optional

Upcaster = Callable[[dict[str, Any]], dict[str, Any]]
"""An upcaster transforms an event dict from version N to N+1."""


_event_types: dict[str, list[Upcaster]] = {}
_event_types_lock = threading.Lock()
"""Registered event types and their upcaster chains.

Structure: {type_name: [v1→v2, v2→v3, ...]}
The current version = len(upcasters) + 1.
"""


def event_types() -> dict[str, int]:
    """Return {type_name: current_version} for all registered types."""
    return {name: len(chain) + 1 for name, chain in _event_types.items()}


def register_event_type(name: str, upcasters: Optional[list[Upcaster]] = None
                        ) -> None:
    """Register an event type with its upcaster chain.

    Upcasters must be ordered oldest-first: [v1→v2, v2→v3, ...].
    This must be called before any events of this type are written.
    Thread-safe.
    """
    with _event_types_lock:
        if name in _event_types:
            raise ValueError(f"Event type {name!r} already registered")
        _event_types[name] = list(upcasters or [])


class EventCodec:
    """Encode/decode events with type-based versioning."""

    @staticmethod
    def encode(type_name: str, payload: dict[str, Any],
               *,
               timestamp: Optional[float] = None,
               event_id: Optional[str] = None,
               **extra_fields) -> dict[str, Any]:
        """Build an event dict for writing.

        Injects type, version, and timestamp. Each event type carries its own
        current version from the registry.
        """
        import time as _time
        if type_name not in _event_types:
            raise KeyError(f"Unknown event type: {type_name!r}. Register it first.")
        version = len(_event_types[type_name]) + 1
        event: dict[str, Any] = {
            "type": type_name,
            "version": version,
            "timestamp": timestamp if timestamp is not None else _time.time(),
            **extra_fields,
            **payload,
        }
        if event_id is not None:
            event["event_id"] = event_id
        return event

    @staticmethod
    def decode(raw: dict[str, Any]) -> dict[str, Any]:
        """Decode a raw event dict, applying upcasters if needed.

        If the event's version is older than the current registered version,
        runs the upcaster chain to bring it current.
        """
        type_name = raw.get("type")
        if not type_name or type_name not in _event_types:
            return raw  # unknown type — pass through
        version = raw.get("version", 1)
        chain = _event_types[type_name]
        current = len(chain) + 1
        if version >= current:
            return raw
        # Run upcasters from version-1 to current-1
        event = dict(raw)
        for i in range(version - 1, current - 1):
            event = chain[i](event)
            event["version"] = i + 2
        return event

    @staticmethod
    def read_all(log_path: str) -> list[dict[str, Any]]:
        """Read and decode all events from a JSONL file."""
        events: list[dict] = []
        with open(log_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)
                events.append(EventCodec.decode(raw))
        return events


# --- self-check ---
if __name__ == "__main__":
    import tempfile, os, time

    # Register a type
    register_event_type("card_started")
    def _v1_to_v2(e):
        e = dict(e)
        e["duration_ms"] = e.pop("dur", 0)
        return e
    register_event_type("card_completed", upcasters=[_v1_to_v2])

    assert event_types() == {"card_started": 1, "card_completed": 2}

    # Encode
    ev = EventCodec.encode("card_started", {"card_id": "c1"}, event_id="ev-1")
    assert ev["type"] == "card_started"
    assert ev["version"] == 1
    assert ev["event_id"] == "ev-1"
    assert "timestamp" in ev
    assert ev["card_id"] == "c1"

    # Decode old version through upcaster
    old = {"type": "card_completed", "version": 1, "card_id": "c2", "dur": 42}
    decoded = EventCodec.decode(old)
    assert decoded["version"] == 2
    assert decoded["duration_ms"] == 42
    assert "dur" not in decoded  # upcaster renamed it

    # Round-trip through JSONL
    import pathlib
    tmp = pathlib.Path(tempfile.mkdtemp()) / "events.jsonl"
    with open(tmp, "w") as f:
        f.write(json.dumps(EventCodec.encode("card_started", {"card_id": "c1"}, event_id="ev-1")) + "\n")
    events = EventCodec.read_all(str(tmp))
    assert len(events) == 1
    assert events[0]["type"] == "card_started"

    print("✅ events")
    os.unlink(tmp)
    os.rmdir(tmp.parent)


# ── #432 claim/graph event types ────────────────────────────────────────────
# Registered here (indicator 3 — EventCodec is the catalog of record, not a
# parallel mechanism). Registration ONLY: encode/decode wiring into the emit
# hook / read path is deferred to the first real upcaster task (plan-review
# P2 — the codec adds no value until events carry versions that need
# migration; node-level type/event_id/ts are canonical for v1).
# NOTE: ClaimStateChanged is deliberately NOT registered — no code path emits
# it (plan-review P1); challenged is derived from NAND-edge presence, and
# every claim transition maps to one of the five concrete event types below.
CLAIM_EVENT_TYPES = (
    "PointAdded",
    "OperatorAdded",
    "PointRetracted",
    "PointSuperseded",
    "OperatorAnnotated",
    "PointPromoted",      # #785: reviewer-gated draft→live promotion
    "OperatorPromoted",   # #785: R16 zombie-operator prevention
    "DedupeRecorded",     # #784: content-dedup candidate recorded/merged
    "DedupeRejected",     # #784: content-dedup candidate rejected
)


def register_claim_event_types() -> None:
    """Register the #432 claim event types (idempotent)."""
    for name in CLAIM_EVENT_TYPES:
        try:
            register_event_type(name)
        except ValueError:
            pass  # already registered (idempotent)


register_claim_event_types()
