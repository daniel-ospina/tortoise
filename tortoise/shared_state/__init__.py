"""Shared state layer — infrastructure & observability .

Modules:
  monitoring  — 4 golden signals (latency P99, throughput, error rate, saturation)
  concurrency — fcntl.flock + atomic os.rename for single-machine coordination
  events      — type-based event versioning (no global schemaVersion)
  recovery    — crash recovery: scan incomplete cards on startup, event ID dedup
"""

from .monitoring import GoldenSignals, collect_signals  # noqa: I001
from .concurrency import locked_append, atomic_claim
from .events import EventCodec, event_types, register_event_type
from .recovery import scan_incomplete_cards, dedup_events, find_last_checkpoint

__all__ = [  # noqa: RUF022
    "GoldenSignals", "collect_signals",
    "locked_append", "atomic_claim",
    "EventCodec", "event_types", "register_event_type",
    "scan_incomplete_cards", "dedup_events", "find_last_checkpoint",
]
