"""Eval error taxonomy for the LongMemEval runner report (issue #1527, M7).

Aligns to the P2 contract (``tortoise/model_adapters.py::classify_llm_error``
— live since #1530) for the coarse LLM-call class and adds the eval's own
dimensions: ``site`` (reader/judge/ingest) and the eval-only classes
(``parse`` for non-HTTP body-shape errors, ``retries_exhausted`` for a
transient that burned its retry budget, ``ingest`` for extractor-internal
failures). Census keys are site-prefixed (``reader:transient``) so a report
answers "where did the failures come from" at a glance.

P2 alignment rule: when the P2 taxonomy changes, ``classify_eval_error``
delegates to it for the coarse class — this module never forks the
status-code table.
"""
from __future__ import annotations

import errno
import logging
import random
import re
import socket
import time
from collections import Counter
from collections.abc import Callable
from typing import Any  # re-exported for convenience (used in call_with_predicate)

from tortoise.model_adapters import classify_llm_error

logger = logging.getLogger(__name__)

#: The full eval error-class vocabulary (published in ``integrity.error_census``).
EVAL_ERROR_CLASSES = (
    "fatal", "fatal_config", "transient", "retries_exhausted",
    "ingest", "parse", "unknown",
)

_SITES = ("reader", "judge", "ingest")

# ── #1786/#1806 retry budget constants (results-relevant knobs) ────────────
#: Write-stage retry count (R1): a transient redis TimeoutError/ConnectionError
#: on an un-timed graph write is retried up to this many times before the
#: stage raises ``WriteStageRetriesExhausted``. ALWAYS present in the run
#: fingerprint (a results-relevant retry knob — mirrors ``max_retries``).
INGEST_WRITE_RETRIES = 2
#: Whole-question retry count (R2): 1 bounded re-attempt of the full per-
#: question pipeline (ingest + retrieval + reader + judge) when the write-
#: stage retries exhaust on a question's INITIAL attempt. The marker is
#: DISARMED during ``--retry-failed`` resume re-attempts, so the worst-case
#: re-burn budget is 2 x ~25 min (1 in-run R2 + 1 resume).
INGEST_QUESTION_RETRIES = 1
#: Resume re-attempt cap (R3): the persisted ``attempts`` field bounds
#: ``--retry-failed`` re-attempts across processes (the in-run R2 counts as
#: the first attempt).
RESUME_ATTEMPTS_CAP = 2

#: Network errno set the write-stage retry predicate trusts as transport
#: evidence on a bare ``OSError`` (P2-1 narrowing — a deterministic-bug
#: FileNotFoundError/ENOENT or PermissionError/EACCES is never retried).
_NETWORK_ERRNOS = frozenset({
    errno.ECONNRESET, errno.ETIMEDOUT, errno.EHOSTUNREACH,
    errno.ENETUNREACH, errno.EPIPE, errno.ECONNREFUSED,
    errno.ECONNABORTED, errno.ENETDOWN,
})

#: MISCONF / AOF-fsync / disk-full write refusals redis surfaces as
#: ``ResponseError`` — retried (bounded) because the recovery under disk
#: pressure IS the retry; unrelated ResponseErrors (WRONGTYPE, ...) are not.
_MISCONF_RE = re.compile(r"MISCONF|Can't persist")


class WriteStageRetriesExhausted(Exception):
    """Write-stage retry sentinel (R1, #1786/#1806).

    Raised by :func:`call_with_predicate` ONLY when the predicate-true
    retries exhaust — the inner exception is exposed via ``.original`` and
    ``__cause__`` so the run.py handler can unwrap FIRST and derive
    ``error``/``error_class``/``retryable`` from the INNER exception (never
    from the sentinel itself — evaluating the predicate on the sentinel
    would persist ``retryable=False`` and permanently lose the question).

    Never constructed for a predicate-FALSE exception: the loop re-raises
    the ORIGINAL exception unchanged, unwrapped (no sentinel, no marker),
    so a fatal-class inner can never reach the handler sentinel-wrapped.
    """

    def __init__(self, original: BaseException):
        self.original = original
        super().__init__(f"write-stage retries exhausted: {original!r}")


def retryable_transient(exc: BaseException) -> bool:
    """True ONLY for transport-class transients the eval write/retry path
    may retry — never for parse/structural/fatal-class errors.

    Pinned matrix (#1786 Task 1 Step 2 / #1806 indicator 1):
    - redis ``TimeoutError`` / ``ConnectionError`` (redis-py 8.x: NOT the
      builtin classes, reprs ``network:TimeoutError`` / ``network:ConnectionError``)
      → True — the verified write-path loss mechanism.
    - redis ``ResponseError`` matching ``/MISCONF|Can't persist/`` (AOF
      fsync / disk-full write refusal) → True; unrelated ResponseErrors → False.
    - ``requests``/``urllib`` provider-network errors (ingest-site LLM
      provider transients) → True.
    - ``OSError`` narrowed to transport errnos (ECONNRESET/ETIMEDOUT/
      EHOSTUNREACH/ENETUNREACH/EPIPE/ECONNREFUSED/ECONNABORTED/ENETDOWN)
      or a socket.timeout cause → True; a deterministic-bug OSError
      (ENOENT/EACCES/...) → False.
    - builtin ``TimeoutError`` (≡ ``socket.timeout`` on 3.10+, where it
      subclasses ``OSError`` — the OSError branch below consumes it) → True
      ONLY with a socket-origin cause (``isinstance(exc.__cause__,
      socket.timeout)``) or a network errno; a BARE local timeout (no
      cause, no errno) → False (P1-7 — a direct ``socket.timeout`` without
      a cause/errno is deliberately NOT retried; drivers wrap socket
      timeouts into the classes above, and the conservative rule protects
      local concurrent.futures/asyncio deadline timeouts).
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
        # cause/errno and correctly resolve False per P1-7).
        return isinstance(exc.__cause__, socket.timeout)
    return False


def call_with_predicate(fn: Callable[[], Any], *, predicate: Callable[[BaseException], bool],
                        retries: int, what: str, base: float = 2.0,
                        cap: float = 30.0, marker_armed: bool = True,
                        on_retry: Callable[[BaseException], None] | None = None) -> Any:
    """Bounded jittered retry of ``fn`` gated by ``predicate`` (R1, #1786).

    Shared single-source retry helper BOTH ``run.py`` and ``ingest_v2.py``
    import (P2-1 — the write-stage loop MUST NOT use run.py's
    ``_call_with_backoff``, whose default retries ANY non-fatal exception
    and would violate the "parse/structural/fatal are never retried"
    acceptance).

    - predicate-FALSE exception → re-raised IMMEDIATELY, unchanged,
      unwrapped (never a sentinel).
    - predicate-true transients → retried with half-jitter
      ``(0.5 + rand/2) * 2**attempt`` (the same spread family as the
      reader/judge backoff); when the budget exhausts the loop raises
      ``WriteStageRetriesExhausted(inner) from inner`` — the R2 marker.
    - ``marker_armed=False`` (a ``--retry-failed`` resume re-attempt): the
      exhausted re-raise is the ORIGINAL exception, unwrapped — no
      sentinel, no R2 marker (P1-1: no resume-internal whole-question
      retry gets a second budget).
    - ``on_retry`` (optional): called with the exception before each
      retry sleep — the eval's ``ingest_retries`` per-question counter
      (Task 1 Step 5, #1786).
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


def classify_eval_error(exc: BaseException, *, site: str) -> str:
    """Classify an exception at an eval call site → ``"<site>:<class>"``.

    P2 alignment: the coarse class IS ``tortoise.model_adapters.
    classify_llm_error`` (401/402/403→fatal, 400/404+other-4xx→fatal_config,
    408/425/429/500/502/503/504+other-5xx→transient,
    connection/timeout/URLError/network-OSError→transient, else unknown).
    The eval adds ONE pre-branch: non-HTTP body-shape errors (KeyError /
    ValueError incl. JSONDecodeError / TypeError on a parsed payload) are
    ``parse`` — a local decode bug, not a provider condition.
    """
    if site not in _SITES:
        raise ValueError(f"site must be one of {_SITES}, got {site!r}")
    if isinstance(exc, (KeyError, ValueError, TypeError)):
        return f"{site}:parse"
    return f"{site}:{classify_llm_error(exc).value}"


def eval_failure_class(exc: BaseException, *, site: str) -> str:
    """Final failure-entry class (site-prefixed, one of EVAL_ERROR_CLASSES).

    - at ANY site, the coarse classes ``transient`` AND ``unknown`` convert
      to ``<site>:retries_exhausted`` (the P2 taxonomy treats unknown as
      transient-safe — the same rule as reader/judge). The site-level retry
      budget has burned by the time the failure reaches the run-loop; the
      class records "retryable but exhausted" as distinct from
      "permanent".
    - at ingest this conversion is a deliberate, documented WIDENING
      (#1776) beyond network blips: unknown-class exceptions (incl.
      extractor-internal bugs) are rate-limited rather than vetoed — a
      transient/unknown ingest failure (e.g. a FalkorDB/network blip in
      ``--db`` mode, or an unclassified extractor exception) grades
      ``ingest:retries_exhausted`` (recoverable) instead of vetoing the
      run at any threshold. Structurally-fatal / parse at ingest stays bare
      ``ingest`` (unchanged, hard veto) — the extractor-internal failure is
      permanent by construction.
    - fatal/fatal_config/parse pass through unchanged.
    """
    cls = classify_eval_error(exc, site=site)
    # Only transient/unknown convert to retries_exhausted — parse (a local
    # decode bug) is permanent and passes through, as do fatal/fatal_config.
    # (is_transient(parse-class) is True per P2's unknown-safe rule, so the
    # coarse class is checked, not is_transient.)
    if cls.split(":", 1)[1] in ("transient", "unknown"):
        return f"{site}:retries_exhausted"
    if site == "ingest":
        return "ingest"
    return cls


def class_for_ingest_error_text(text: str) -> str:
    """Classify one v2-ingest error string (not an exception).

    ``ingest_stats['errors']`` records extractor-stage failures as strings —
    they are ingest-site failures by construction, so the ``ingest`` class
    is honest; the string itself carries the detail.
    """
    return "ingest"


def census_classes(class_strings: list[str]) -> dict[str, int]:
    """Counter over site-prefixed class strings — the report's
    ``integrity.error_census`` (sorted keys, stable serialization)."""
    return dict(sorted(Counter(s for s in class_strings if s).items()))
