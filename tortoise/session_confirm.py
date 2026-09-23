"""Confirm whether a capture's turns are DURABLE on the hosted server (#4675).

``POST /v1/sessions`` can commit the Session **and its turns** and still hand
the client a *retryable* refusal. The transport wait bound does not cancel what
it refuses — the middleware keeps its own task and ANSWERS the caller while the
handler runs to completion (``hosted_api.py::WaitBoundMiddleware``) — so a 504
routinely arrives **after** the commit. A client that reads only the status
therefore cannot tell a post-commit refusal from a pre-commit one, and
``capture_spool`` reads every retryable status as "nothing committed" and
defers: an entry for a session that is already durable retries forever and
``session drain`` never reaches ``filed N, deferred 0``.

THE PROOF IS THE TURN ID SET, NOT THE SESSION'S EXISTENCE. The server writes
``{session_id}_t{i}`` for the whole window in ONE batched transaction
**before** extraction (``hosted_api.py``, the shared ``_write_capture_turns``),
so those ids are durable independently of the extraction the bound may abandon.
Session existence is deliberately NOT sufficient: the Session MERGE precedes
the turn write, so an existence check would confirm a capture whose turns never
landed — the exact false positive #4675 warns against.

The read is INJECTED (``reader``), exactly as ``capture_spool.flush_spool``
injects its ``post``, so the one definition of the ``GET /v1/sessions/<id>``
404→None / other-status-raises contract stays in ``session_verify`` and cannot
drift between callers.

UNKNOWN NEVER FILES. A 404 (the abandoned handler may not have MERGEd yet), a
retryable status on the READ, a transport error, or a short turn set all defer;
only an exact match on the posted ids files.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

__all__ = [
    "FILED",
    "TURNS_MISSING",
    "UNEXTRACTED",
    "UNKNOWN",
    "confirm_capture",
    "expected_turn_ids",
    "session_extracted",
    "turn_point_id",
    "turn_point_ids",
]

#: The turns we posted are durable on the server and extraction was observed.
FILED = "filed"
#: Durable, but no extracted points are visible yet. For the SPOOL this is
#: enough to stop retrying (its contract is turn durability); for the local
#: IMPORT receipt it is NOT — that receipt asserts a completed import, and
#: #4188 forbids writing it for a keyless capture whose extraction never ran.
UNEXTRACTED = "unextracted"
#: The turn id set cannot be established (404 / read refusal / transport). The
#: caller must DEFER: never terminalise, never discard.
UNKNOWN = "unknown"
#: The server answered and does not hold the full posted turn set.
TURNS_MISSING = "turns-missing"

#: Bounded observation of an abandoned write that is racing our read. Sized to
#: the server's own breach pair (its bound and its advertised delay are both
#: 10 s), NOT to the caller's whole retry budget: a caller that is still
#: unconfirmed defers and re-attempts later, which is always safe.
DEFAULT_ATTEMPTS = 3
DEFAULT_DELAY_S = 2.0

#: The read is the cheap leg (one row + its turn ids). The POST's own 30 s
#: timeout is far too long to spend per confirmation attempt.
DEFAULT_READ_TIMEOUT_S = 10.0


def turn_point_id(session_id: str, index: int) -> str:
    """The deterministic per-turn id the hosted writer MERGEs on.

    ``{session_id}_t{index}`` is the server's IDEMPOTENCY CONTRACT for a
    capture's turns — the same id-space ``sdk._TURN_WRITE_CYPHER`` and the
    hosted batch writer MERGE into. It is restated in several modules; this is
    the one constructor for the client's confirmation path, so a change to the
    format changes one place here rather than silently making every confirm
    return "not durable" (a fail-silent deferral, not a failure).
    """
    return f"{session_id}_t{index}"


def expected_turn_ids(session_id: str, count: int) -> set[str]:
    """The ids a complete capture of ``count`` turns must have on the server."""
    return {turn_point_id(session_id, i) for i in range(max(0, int(count)))}


def turn_point_ids(detail: Any) -> set[str]:
    """The turn ids in a ``GET /v1/sessions/<id>`` detail dict.

    Total over a malformed payload: anything that is not a mapping with a
    string ``id`` is skipped rather than raising, because this runs inside a
    failure path whose contract is "never replace the honest error with a
    traceback".
    """
    if not isinstance(detail, dict):
        return set()
    rows = detail.get("turn_points")
    if not isinstance(rows, list):
        return set()
    out: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            tid = row.get("id")
            if isinstance(tid, str) and tid:
                out.add(tid)
    return out


def session_extracted(detail: Any) -> int:
    """Extracted point count from a session detail; 0 when absent/odd.

    Negative or non-integer values read as 0 — an unknown extraction count must
    never satisfy the "extraction observed" test that gates the import receipt.
    """
    if not isinstance(detail, dict):
        return 0
    value = detail.get("extracted")
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def confirm_capture(
    reader: Callable[..., dict[str, Any] | None],
    api_url: str,
    api_key: str,
    session_id: str,
    expected_turns: int,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    delay_s: float = DEFAULT_DELAY_S,
    read_timeout_s: float = DEFAULT_READ_TIMEOUT_S,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Classify a refused-but-possibly-committed capture.

    ``reader(api_url, api_key, session_id, timeout=...)`` must return the
    session detail dict, ``None`` on 404, and raise on every other refusal
    (``session_verify._session_detail`` is that reader).

    Returns one of :data:`FILED`, :data:`UNEXTRACTED`, :data:`UNKNOWN`,
    :data:`TURNS_MISSING`. Any outcome other than the first two must DEFER.
    """
    if not session_id or expected_turns <= 0:
        # Nothing was posted to confirm. Never claim a commitment for a
        # payload we never sent.
        return UNKNOWN
    want = expected_turn_ids(session_id, expected_turns)
    tries = max(1, int(attempts))
    for attempt in range(tries):
        detail: dict[str, Any] | None = None
        try:
            detail = reader(api_url, api_key, session_id,
                            timeout=read_timeout_s)
        except Exception:
            # UNKNOWN, never "not committed": a read we could not complete is
            # not evidence either way, and the caller's safe direction is to
            # defer. Broad on purpose — the reader's failure taxonomy belongs
            # to the reader.
            detail = None
        if detail is not None:
            have = turn_point_ids(detail)
            if have == want:
                return FILED if session_extracted(detail) >= 1 else UNEXTRACTED
            if have:
                # The server holds SOME of this session's turns but not the
                # posted set — a partial write, or a concurrent capture that
                # grew the transcript (the server windows to the same cap).
                # Not ours to call filed.
                return TURNS_MISSING
        if attempt + 1 < tries:
            sleep(delay_s)
    return UNKNOWN
