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
#: definitively *did not land* (#7405) AND the graph's own state is irrelevant
#: to the decision — the same graph is still there when the retry is issued:
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
#:     raised instead of retried (the #7405 loss).
#: Both are v6 literals; the engine's own concurrency test documents the
#: message as retryable and the message itself instructs a retry.
#:
#: ANCHORED on the full abort context, deliberately: a bare alternation over
#: ``re.search`` matches ANY message merely CONTAINING the phrases (measured:
#: three crafted non-abort diagnostics all returned True). Because the predicate
#: gates a re-issued bare ``CREATE`` — non-idempotent, no uniqueness constraint
#: on ``Point.id`` — a false positive IS the duplicate-point failure #7405's fix
#: exists to prevent, so the whole refusal clause must be present.
_WRITE_LOCK_RE = re.compile(
    r"(?:write query )?aborted:\s*another write is in progress"
    r"|another write is in progress, retry the query",
    re.IGNORECASE)

#: The graph-deleted-or-replaced abort family — the ONE class whose retry
#: decision depends on the GRAPH's STATE, not on the error text (#7685). Two
#: engine cores, three measured literals, and on v6 a single sentence for two
#: different realities:
#:
#:   - **v6 core** (``MODULE LIST`` ver **60001**, Rust): one sentence, because
#:     the engine cannot tell the caller which happened — ``graph was deleted or
#:     replaced while the query was running, aborting``;
#:   - **C core** (``MODULE LIST`` ver **42004** = the ``docker-compose.yml``-
#:     pinned ``falkordb-server:v4.20.4``, the documented default lane; 42006 =
#:     v4.20.6): two literals, measured live on the same race —
#:     ``Encountered different graph value when opened key <name>`` for a
#:     REBUILD and ``Encountered an empty key when opened key <name>`` for a
#:     plain DELETION.
#:
#: All three mean the statement did NOT land in the graph the caller is writing
#: to, so re-issuing cannot duplicate. But **neither core's text is by itself
#: sufficient to authorize a retry**, because the caller's graph may be GONE:
#: FalkorDB auto-creates a graph on write, so a re-issued bare ``CREATE`` would
#: succeed into a fresh EMPTY graph and the caller would be told SUCCESS while
#: its data vanished (#6666 / #7685(A)). The discriminator is STATE —
#: ``EXISTS <name>`` is 0 after a deletion and 1 after a rebuild's recreate
#: (measured on both cores) — which is why :func:`retryable_aborted_write`
#: REFUSES this family unless it is handed a ``graph_exists`` probe.
#:
#: The two C-core literals are additionally LINE-ANCHORED (``^\s*``): they are
#: whole engine messages, so a diagnostic that merely quotes them must not
#: match.
_GRAPH_ABORT_RE = re.compile(
    r"graph was deleted or replaced while the query was running, aborting"
    r"|^\s*encountered an empty key when opened key "
    r"|^\s*encountered different graph value when opened key ",
    re.IGNORECASE | re.MULTILINE)


def retryable_aborted_write(
    exc: BaseException,
    *,
    graph_exists: Callable[[], bool] | None = None,
) -> bool:
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

    - a concurrent writer held the write slot (#7405) — the engine's own
      *aborting* refusal, reported on two code paths (the constraint path's
      ``Write query aborted: …`` and the ``GRAPH.QUERY`` write path's
      ``another write is in progress, retry the query``), both with the same
      *did not land* semantics (see ``_WRITE_LOCK_RE``);
    - persistence refused the write (``MISCONF`` / ``Can't persist``) — a write
      refusal, not a completed write.

    **The graph-deleted-or-replaced family needs the caller's state (#7685).**
    The engine's abort text (``_GRAPH_ABORT_RE``) admits BOTH a legitimate
    rebuild and a plain deletion, and on the v6 core it is literally the same
    sentence. Retrying a deletion re-issues the ``CREATE`` into a graph FalkorDB
    auto-created EMPTY, so the caller is told SUCCESS while its data is gone
    (#6666 class). The predicate therefore refuses this family unless
    *graph_exists* answers, and the answer is False:

    - ``graph_exists`` — a zero-argument callable answering *"does the graph
      this write targets currently exist?"* (the SDK passes a raw
      ``EXISTS <name>`` probe). ``True`` (a rebuild left the key present) →
      retry; ``False`` (a deletion, or a rebuild whose recreate has not landed)
      → refuse, so the engine's abort surfaces LOUD.
    - **Omitted, or the probe raises → refuse.** A retry this predicate cannot
      authorize is never guessed: a missed retry surfaces the engine's error,
      while a false retry reports success against a graph the caller never wrote
      to.

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

    if not isinstance(exc, _re.ResponseError):
        return False
    text = str(exc)
    if _MISCONF_RE.search(text) or _WRITE_LOCK_RE.search(text):
        return True
    if not _GRAPH_ABORT_RE.search(text):
        return False
    # The graph-abort family. The text cannot distinguish a rebuild from a
    # deletion, so the STATE decides — and a retry we cannot authorize is
    # refused (loud), never guessed.
    if graph_exists is None:
        return False
    try:
        return bool(graph_exists())
    except Exception:  # noqa: BLE001, RUF100 — a failed probe is not a rebuild
        logger.warning(
            "graph-existence probe failed; refusing to retry the aborted write "
            "(a silent success is worse than a loud miss)", exc_info=True)
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
      (:func:`retryable_aborted_write`) — and the graph-abort family only when
      the graph still exists (#7685). Adding them to this predicate as well
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
