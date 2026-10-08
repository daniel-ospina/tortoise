"""Bounded retry with a transport-only predicate — the #1806 resilience
primitive, now a PRODUCT module (product inversion,
fix/invert-retrieval-to-product).

Moved from ``tools/longmem_eval/errors.py`` (where the eval's ingest write
path consumed it) so the product owns the capability: ``retryable_transient``
is the pinned transport-class predicate, ``retryable_aborted_write`` is the
narrower write-path predicate (the SDK's direct graph writes, #7405), and
``call_with_predicate`` is the bounded jittered retry loop. The eval harness
re-exports ``retryable_transient`` / ``call_with_predicate`` /
``WriteStageRetriesExhausted`` unchanged from ``tools/longmem_eval/errors.py``
and keeps its own run knobs (``INGEST_WRITE_RETRIES`` etc.) eval-side.

⚠️ PARTIALLY WIRED (#7405): ``TortoiseSDK._graph_write_with_retry`` retries
``create_point``'s two direct writes through ``retryable_aborted_write``.
Wiring the remaining write surfaces — ``_post_commit`` (tortoise/sdk.py) and
the capture/commit graph writes — is separate work (audit G8); those paths'
robustness today remains idempotent-MERGE + ``client_commit_id`` replay +
server-side dedup.
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

#: Write refusals where the engine ABORTED the statement, so the outcome is
#: definitively *did not land* (#7405). Three measured messages, from two
#: engine code paths:
#:   - a rebuild/replace aborts an in-flight query: ``graph was deleted or
#:     replaced while the query was running, aborting``;
#:   - a concurrent writer holds the write lock, reported on the constraint
#:     path: ``Write query aborted: another write is in progress``;
#:   - a concurrent writer holds the slot, reported on the ``GRAPH.QUERY``
#:     write path: ``ERR another write is in progress, retry the query``.
#:     This is a SEPARATE engine code path from the constraint-path abort
#:     above (``src/graph_core.rs::execute_query_write``, raised before the
#:     slot is claimed), and on the **v6 Rust core** it is the refusal the
#:     SDK's own wrapped statements actually receive — ``_advance_ep_version``'s
#:     ``MERGE`` and ``create_point``'s bare ``CREATE`` both take the write
#:     path, so without this clause a contended ``create_point`` write is
#:     raised instead of retried (the #7405 loss). Version scope: all three
#:     clauses are v6 literals, and the repo's pinned
#:     ``falkordb-server:v4.20.4`` is the older **C** core, which reports
#:     ``Encountered different graph value when opened key <name>`` for the same
#:     race.
#:
#:     ⛔ THAT CORE MESSAGE IS **NOT** MATCHED BY ``_ABORTED_WRITE_RE``, so on that
#:     engine :func:`retryable_aborted_write` returns False and this retry is
#:     INERT. The three clauses below are literals of the v6 core and are absent
#:     from the C core's binary, so the coverage here is ENGINE-SCOPED and does
#:     not reach every engine this repo runs.
#:
#:     Deliberately NO enumeration of which images are covered and which are
#:     not. An earlier revision of this comment listed them and got three of the
#:     specifics wrong (it named one image as the sole carrier of these literals
#:     when another also carries them, mis-stated one image's module version, and
#:     enumerated a set that omitted the CI provision default). That is the very
#:     defect this comment was being fixed for — a claim of coverage that had not
#:     been verified — so the enumeration is deleted rather than corrected. If you
#:     need the matrix, MEASURE it (``MODULE LIST`` for the module version,
#:     ``grep -a`` on ``/var/lib/falkordb/bin/falkordb.so`` for the literals, per
#:     image); do not copy it from here.
#:
#:     ⛔ Do NOT "fix" the gap by adding the C literal to the regex on the
#:     strength of this comment. A FALSE POSITIVE here re-issues a bare,
#:     non-idempotent ``CREATE`` and mints a duplicate point — precisely the
#:     failure :func:`retryable_aborted_write` exists to prevent. Widening the
#:     clause requires establishing the C message's *did-not-land* property
#:     from the engine source first.
#: All three clauses are **retryable**, but ONLY on the write path — see
#: :func:`retryable_aborted_write` for the layering, and
#: :func:`retryable_transient` for why they are deliberately NOT in the
#: transport predicate (putting them there made one error retryable at two
#: nested layers).
#:
#: ANCHORED on the full abort context, deliberately: a bare alternation over
#: ``re.search`` matches ANY message merely CONTAINING the phrases (measured:
#: three crafted non-abort diagnostics all returned True). Because the predicate
#: gates a re-issued bare ``CREATE`` — non-idempotent, no uniqueness constraint
#: on ``Point.id`` — a false positive IS the duplicate-point failure this PR
#: exists to prevent, so the whole refusal clause must be present.
_ABORTED_WRITE_RE = re.compile(
    r"graph was deleted or replaced while the query was running, aborting"
    r"|(?:write query )?aborted:\s*another write is in progress"
    r"|another write is in progress, retry the query",
    re.IGNORECASE)


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

    - the graph was replaced underneath the running query, or a concurrent writer
      held the write slot (#7405) — the engine's own *aborting* refusal, reported
      on two code paths (the constraint path's ``Write query aborted: …`` and the
      ``GRAPH.QUERY`` write path's ``another write is in progress, retry the
      query``), both with the same *did not land* semantics (see
      ``_ABORTED_WRITE_RE`` for why the write-path clause exists);
    - persistence refused the write (``MISCONF`` / ``Can't persist``) — a write
      refusal, not a completed write.

    **The server guarantee this rests on** (no test pins it — it is an engine
    property, measured by reading the engine source; see PR #7615's verification
    table): the replaced-graph check is ``WriteAbort::GraphUnregistered``, whose
    registration test runs BEFORE mutation under one continuous GIL hold, and
    the engine's own test says it *"aborted before mutating"*; ``MISCONF`` is a
    pre-execution command rejection. So neither can come back after the write
    applied — which is what makes a bare ``CREATE`` safe to re-issue.

    This predicate is the SDK write path's own gate; it is intentionally NOT
    :func:`retryable_transient`, which stays the transport-class predicate the
    eval's OUTER phase loops use (see the layering note there).
    """
    import redis.exceptions as _re

    if isinstance(exc, _re.ResponseError):
        return bool(_MISCONF_RE.search(str(exc)) or _ABORTED_WRITE_RE.search(str(exc)))
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
      **Deliberately NOT here: the replaced-graph / write-lock aborts** (#7405).
      They ARE retryable, but only on the write path
      (:func:`retryable_aborted_write`). Adding them to this predicate as well
      made ONE error retryable at two nested layers — the eval's outer phase
      loop (``tools/longmem_eval/ingest_v2.py``) wrapping the SDK's inner write
      retry — which multiplies the budget with no benefit, because the SDK
      retry already covers the write. So this predicate returns False for them.
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
    if isinstance(exc, _re.ResponseError) and _MISCONF_RE.search(str(exc)):
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
