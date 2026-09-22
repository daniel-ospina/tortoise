"""Per-harness capture receipt / last-error STATE key names — ONE definition.

The dashboard's capture-status surface reads these server-written onboarding
state keys (never client state):

* ``session_capture_receipt_<harness>`` proves a durable hosted 2xx capture
  (the bare ``session_capture_receipt`` is the legacy no-harness key);
* ``session_capture_last_error_<harness>`` carries the last non-2xx attempt's
  detail (the per-harness failure sub-line).

Both the hosted API (``tortoise/hosted_api.py``) and the CLI's
``tortoise session verify`` (#3809) derive them from THIS module, so the two
can never disagree about the key spelling.  A local copy in either caller is
exactly the sibling-divergence class the capture lane has paid review cycles
for — one function, two importers.
"""
from __future__ import annotations

__all__ = ["capture_last_error_key", "capture_receipt_key"]


def capture_receipt_key(harness: str | None) -> str:
    """Receipt state key for a harness — per-harness when present, the bare
    legacy key for no-harness hooks (T1-P3 None-guard)."""
    return f"session_capture_receipt_{harness}" if harness else \
        "session_capture_receipt"


def capture_last_error_key(harness: str | None) -> str | None:
    """Per-harness last-error state key. No bare variant is registered — a
    legacy no-harness hook has no per-harness dashboard row to read it."""
    if not harness:
        return None
    return f"session_capture_last_error_{harness}"
