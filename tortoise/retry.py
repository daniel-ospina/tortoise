"""Bounded retry with a transport-only predicate — the #1806 resilience
primitive, now a PRODUCT module (product inversion,
fix/invert-retrieval-to-product).

Moved from ``tools/longmem_eval/errors.py`` (where the eval's ingest write
path consumed it) so the product owns the capability: ``retryable_transient``
is the pinned transport-class predicate and ``call_with_predicate`` is the
bounded jittered retry loop. The eval harness re-exports both unchanged
from ``tools/longmem_eval/errors.py`` and keeps its own run knobs
(``INGEST_WRITE_RETRIES`` etc.) eval-side.

⚠️ FOLLOW-UP (documented, NOT in this PR): wiring this module into the SDK
write path — ``_post_commit`` (tortoise/sdk.py) and the capture/commit
graph writes — is a separate change (audit G8). The product's write
robustness today remains idempotent-MERGE + ``client_commit_id`` replay +
server-side dedup; this module is the reusable bounded-retry primitive for
when the retry decision ships.
"""
from __future__ import annotations

import errno
import logging
import random
import re
import socket
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

#: Network errno set the retry predicate trusts as transport evidence on a
#: bare ``OSError`` — a deterministic-bug FileNotFoundError/ENOENT or
#: PermissionError/EACCES is never retried.
_NETWORK_ERRNOS = frozenset({
    errno.ECONNRESET, errno.ETIMEDOUT, errno.EHOSTUNREACH,
    errno.ENETUNREACH, errno.EPIPE, errno.ECONNREFUSED,
    errno.ECONNABORTED, errno.ENETDOWN,
})

#: MISCONF / AOF-fsync / disk-full write refusals redis surfaces as
#: ``ResponseError`` — retried (bounded) because the recovery under disk
#: pressure IS the retry; unrelated ResponseErrors (WRONGTYPE, ...) are not.
_MISCONF_RE = re.compile(r"MISCONF|Can't persist")

#: A rebuilt/replaced graph invalidates a cached graph HANDLE, and the writer then
#: gets ``ResponseError("graph was deleted or replaced while the query was running,
#: aborting")`` (#7405). Reads keep working (each read re-resolves) while writes on
#: the stale handle are refused. **Retryable**: the recovery is to re-resolve the
#: handle and re-issue — which is what #7405 did by hand; it healed after ~1h, a
#: window no caller will wait for. This is NOT a deterministic caller bug like
#: WRONGTYPE: the statement is valid and succeeds on a fresh handle.
_GRAPH_REPLACED_RE = re.compile(r"graph was deleted or replaced", re.IGNORECASE)


def retryable_aborted_write(exc: BaseException) -> bool:
    """Retryable ONLY for write refusals whose outcome is definitively *did not land*.

    Deliberately narrower than :func:`retryable_transient`, and it exists because
    that predicate is NOT safe for a NON-IDEMPOTENT statement. ``create_point``
    issues a bare ``CREATE`` with a client-minted id and there is **no uniqueness
    constraint on ``Point.id``** (a plain index only, so a duplicate is accepted);
    the eval lane documents the same hazard independently — *"create_point uses
    CREATE (not MERGE), so re-running ingest over the same fresh graph would
    duplicate points"* (``tools/longmem_eval/ingest.py``, ``_point_exists``).

    ``retryable_transient`` also returns True for redis ``TimeoutError`` /
    ``ConnectionError``, where the server may have APPLIED the write and only the
    reply was lost — re-issuing those on a bare ``CREATE`` can mint two points
    with one id, and ``get_point`` would silently return one of them, breaking
    ``derived == replay(journal)``. Both arms below instead mean the engine
    ABORTED the statement, so nothing was applied and re-issuing cannot duplicate:

    - the graph was replaced underneath the running query (#7405) — the verb is
      *aborting*, and the replaced key discards anything written into the old one;
    - persistence refused the write (``MISCONF`` / ``Can't persist``) — a write
      refusal, not a completed write.
    """
    import redis.exceptions as _re

    if isinstance(exc, _re.ResponseError):
        return bool(_MISCONF_RE.search(str(exc)) or _GRAPH_REPLACED_RE.search(str(exc)))
    return False


class WriteStageRetriesExhausted(Exception):
    """Write-stage retry sentinel (R1, #1786/#1806).

    Raised by :func:`call_with_predicate` ONLY when the predicate-true
    retries exhaust — the inner exception is exposed via ``.original`` and
    ``__cause__`` so the caller can unwrap FIRST and derive error class /
    retryable from the INNER exception (never from the sentinel itself —
    evaluating the predicate on the sentinel would persist
    ``retryable=False`` and permanently lose the write).

    Never constructed for a predicate-FALSE exception: the loop re-raises
    the ORIGINAL exception unchanged, unwrapped (no sentinel, no marker),
    so a fatal-class inner can never reach the caller sentinel-wrapped.
    """

    def __init__(self, original: BaseException):
        self.original = original
        super().__init__(f"write-stage retries exhausted: {original!r}")


def retryable_transient(exc: BaseException) -> bool:
    """True ONLY for transport-class transients a write/retry path may
    retry — never for parse/structural/fatal-class errors.

    Pinned matrix (#1786 Task 1 Step 2 / #1806 indicator 1):
    - redis ``TimeoutError`` / ``ConnectionError`` (redis-py 8.x: NOT the
      builtin classes, reprs ``network:TimeoutError`` / ``network:ConnectionError``)
      → True — the verified write-path loss mechanism.
    - redis ``ResponseError`` matching ``/MISCONF|Can't persist/`` (AOF
      fsync / disk-full write refusal) → True; unrelated ResponseErrors → False.
    - redis ``ResponseError`` for a REPLACED graph handle (``graph was deleted
      or replaced while the query was running``, #7405) → True: a rebuild
      invalidates the handle, and the recorded recovery is re-resolve +
      re-issue (the wedge healed on its own after ~1h, which is why it looked
      permanent). The caller must re-resolve via ``on_retry`` — retrying the
      same stale handle is the failure mode #7405 describes.
    - ``requests``/``urllib`` provider-network errors (LLM provider
      transients) → True.
    - ``OSError`` narrowed to transport errnos (ECONNRESET/ETIMEDOUT/
      EHOSTUNREACH/ENETUNREACH/EPIPE/ECONNREFUSED/ECONNABORTED/ENETDOWN)
      or a socket.timeout cause → True; a deterministic-bug OSError
      (ENOENT/EACCES/...) → False.
    - builtin ``TimeoutError`` (≡ ``socket.timeout`` on 3.10+, where it
      subclasses ``OSError`` — the OSError branch below consumes it) → True
      ONLY with a socket-origin cause (``isinstance(exc.__cause__,
      socket.timeout)``) or a network errno; a BARE local timeout (no
      cause, no errno) → False — a direct ``socket.timeout`` without a
      cause/errno is deliberately NOT retried; drivers wrap socket
      timeouts into the classes above, and the conservative rule protects
      local concurrent.futures/asyncio deadline timeouts.
    - ``urllib.error.HTTPError`` / ``requests.HTTPError`` are EXCLUDED
      FIRST (HTTPError IS-A URLError IS-A OSError on 3.12) — deterministic
      status responses are never retryable.
    """
    import urllib.error

    import redis.exceptions as _re
    import requests

    # HTTPError classes first — they subclass URLError which subclasses
    # OSError (Python 3.12), so they must be excluded before either branch.
    if isinstance(exc, (urllib.error.HTTPError, requests.HTTPError)):
        return False
    if isinstance(exc, _re.TimeoutError):
        return True
    if isinstance(exc, _re.ConnectionError):
        return True
    if isinstance(exc, _re.ResponseError) and (
        _MISCONF_RE.search(str(exc))
        or _GRAPH_REPLACED_RE.search(str(exc))
    ):
        return True
    if isinstance(exc, requests.exceptions.Timeout):
        return True
    if isinstance(exc, requests.exceptions.ConnectionError):
        return True
    if isinstance(exc, urllib.error.URLError):
        return True
    if isinstance(exc, OSError):
        if exc.errno in _NETWORK_ERRNOS:
            return True
        # socket-origin context (on 3.10+ builtin TimeoutError IS-A OSError,
        # so every TimeoutError lands here — bare local timeouts have no
        # cause/errno and correctly resolve False).
        return isinstance(exc.__cause__, socket.timeout)
    return False


def call_with_predicate(fn: Callable[[], Any], *, predicate: Callable[[BaseException], bool],
                        retries: int, what: str, base: float = 2.0,
                        cap: float = 30.0, marker_armed: bool = True,
                        on_retry: Callable[[BaseException], None] | None = None) -> Any:
    """Bounded jittered retry of ``fn`` gated by ``predicate`` (R1, #1786).

    Shared single-source retry helper — any caller that must NEVER retry a
    parse/structural/fatal-class exception uses this (the alternative of a
    retry-everything loop would violate "only transients are retried").

    - predicate-FALSE exception → re-raised IMMEDIATELY, unchanged,
      unwrapped (never a sentinel).
    - predicate-true transients → retried with half-jitter
      ``(0.5 + rand/2) * 2**attempt``; when the budget exhausts the loop
      raises ``WriteStageRetriesExhausted(inner) from inner`` (the R2
      whole-question marker).
    - ``marker_armed=False`` (e.g. a resume re-attempt): the exhausted
      re-raise is the ORIGINAL exception, unwrapped — no sentinel, no R2
      marker (no resume-internal whole-question retry gets a second budget).
    - ``on_retry`` (optional): called with the exception before each
      retry sleep (the caller's per-write retry counter).
    """
    for attempt in range(1, retries + 2):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001, RUF100
            if not predicate(e):
                raise
            if attempt > retries:
                if marker_armed:
                    raise WriteStageRetriesExhausted(e) from e
                raise
            wait = min(base ** attempt, cap) * (0.5 + random.random() / 2)
            if on_retry is not None:
                on_retry(e)
            logger.warning("%s failed (attempt %d/%d): %s; retrying in ~%.1fs",
                           what, attempt, retries, e, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")  # pragma: no cover
