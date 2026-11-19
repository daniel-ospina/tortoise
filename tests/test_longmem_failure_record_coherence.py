"""#8486 findings 1 + 2 — the persisted failure record must not contradict
itself, and it must carry the server's own sentence.

The 10-question smoke (``--limit 10 --retrieval-only``, main ``7d3470909a``)
persisted this entry for question ``c5e8278d``::

    error        server:ResponseError
    error_class  ingest:retries_exhausted      # recoverable  (errors.py:155)
    retryable    false                          # ...so a resume SKIPPED it
    attempts     0                              # ...as if nothing was tried

Three separate defects, pinned here as one invariant each:

1. ``retryable`` is derived from the entry's ``error_class`` — a recoverable
   class can never be written ``retryable: false`` (the resume gate keys on the
   flag, so the contradiction is a silent skip, not a visible error).
2. ``attempts`` counts pipeline attempts made, so ``retries_exhausted`` can
   never be paired with ``attempts: 0``.
3. ``error_detail`` carries ``str(exc)`` — the ONLY place a redis/FalkorDB
   server message survives, because ``redis.exceptions.RedisError.__repr__``
   returns ``f"{self.error_type.value}:{type(self).__name__}"`` and drops
   ``args`` entirely.

Run::

    uv run pytest tests/test_longmem_failure_record_coherence.py -v
"""
from __future__ import annotations

import redis.exceptions as redis_exc
import pytest
import requests

from tools.longmem_eval import errors as lme_errors
from tools.longmem_eval import run as runner

#: The exact exception the smoke recorded: a bare ``ResponseError``. Its repr is
#: ``server:ResponseError`` (redis-py overrides ``__repr__``), its class is
#: ``ingest:retries_exhausted`` (unknown at ingest → recoverable, #1776), and
#: ``retryable_transient`` says False (it is not a write-layer retry) — which is
#: how the record came to contradict itself.
SMOKE_EXC = redis_exc.ResponseError(
    "graph was deleted or replaced while the query was running, aborting")

#: One exception per taxonomy outcome. ``expected_class`` is what
#: ``eval_failure_class`` grades it at the given site; the coherence invariant
#: is asserted over the TABLE, not over one hand-picked case, so a future
#: re-grade of any row re-checks the flag instead of drifting silently.
TAXONOMY_TABLE = [
    # (label, exception, site, expected class)
    ("response-error-ingest", SMOKE_EXC, "ingest", "ingest:retries_exhausted"),
    ("response-error-reader", redis_exc.ResponseError("ERR bad query"),
     "reader", "reader:retries_exhausted"),
    ("redis-timeout-ingest", redis_exc.TimeoutError("timed out"),
     "ingest", "ingest:retries_exhausted"),
    ("requests-timeout-judge", requests.exceptions.Timeout("slow"),
     "judge", "judge:retries_exhausted"),
    ("value-error-ingest", ValueError("bad body"), "ingest", "ingest"),
    ("key-error-reader", KeyError("choices"), "reader", "reader:parse"),
    ("unknown-obj-ingest", RuntimeError("???"), "ingest",
     "ingest:retries_exhausted"),
    ("unknown-obj-judge", RuntimeError("???"), "judge",
     "judge:retries_exhausted"),
]


def _entry(exc, *, stage="ingest", attempts=1, prior=None,
           r2_attempted=0, resume_reattempt=False):
    return runner._build_failure_entry(
        "q-1", "single-session-user", exc, stage, r2_attempted,
        resume_reattempt, prior)


# ── finding 1a: class ↔ retryable coherence ────────────────────────────────
@pytest.mark.parametrize("label,exc,site,expected_class", TAXONOMY_TABLE,
                         ids=[row[0] for row in TAXONOMY_TABLE])
def test_retryable_agrees_with_the_graded_class(label, exc, site,
                                                expected_class):
    """⛔ THE FALSIFIER for finding 1: a recoverable class recorded with
    ``retryable: false`` — or a non-recoverable class recorded ``true`` —
    fails this test. Both halves matter: the flag must be a FUNCTION of the
    class, so neither direction may drift."""
    entry = _entry(exc, stage=site)
    assert entry["error_class"] == expected_class
    assert entry["retryable"] is lme_errors.class_is_recoverable(
        expected_class), (
        f"{label}: error_class={entry['error_class']!r} recorded "
        f"retryable={entry['retryable']!r} — class and flag must agree, or a "
        f"--retry-failed resume skips (or over-retries) the failure"
    )


def test_the_smoke_record_is_now_coherent():
    """The exact shape the smoke persisted, graded field by field."""
    entry = _entry(SMOKE_EXC)
    assert entry["error_class"] == "ingest:retries_exhausted"  # unchanged
    assert entry["retryable"] is True, (
        "a recoverable class must be retryable — this is the flag the "
        "--retry-failed resume reads")
    assert entry["attempts"] == 1, (
        "the question was attempted once; 0 claims nothing was tried")
    assert runner._retry_failed_skip_reason(entry) is None, (
        "the resume gate must ADMIT the failure the record calls recoverable")


def test_a_permanent_class_is_never_retryable():
    """The other direction: a parse (permanent, local decode bug) and the bare
    ingest veto must stay ``retryable: false``, so the coherence rule cannot be
    satisfied by marking everything recoverable.

    Note the ingest row grades BARE ``ingest``, not ``ingest:parse`` —
    ``eval_failure_class``'s documented ingest widening sends every
    non-transient ingest failure to the hard veto ("structurally-fatal / parse
    at ingest stays bare ``ingest``"), which is a run-level veto and never a
    resume candidate. That is a *decision*, not an accident of this test.
    """
    for exc, site, expected in ((ValueError("bad json"), "ingest", "ingest"),
                                (KeyError("x"), "ingest", "ingest"),
                                (KeyError("choices"), "reader",
                                 "reader:parse")):
        entry = _entry(exc, stage=site)
        assert entry["error_class"] == expected
        assert entry["retryable"] is False


def test_class_is_recoverable_reads_the_class_half_of_the_key():
    assert lme_errors.class_is_recoverable("ingest:retries_exhausted") is True
    assert lme_errors.class_is_recoverable("reader:transient") is True
    assert lme_errors.class_is_recoverable("judge:unknown") is True
    assert lme_errors.class_is_recoverable("ingest") is False  # bare veto
    assert lme_errors.class_is_recoverable("reader:parse") is False
    assert lme_errors.class_is_recoverable("judge:fatal") is False
    assert lme_errors.class_is_recoverable(None) is False
    assert lme_errors.class_is_recoverable("") is False


def test_the_recovery_tier_preservation_stays_coherent():
    """P1-1: a re-attempt that failed at a NON-ingest stage keeps the
    ``ingest:retries_exhausted`` class. The flag must follow the PRESERVED
    class, not the new exception's own grade — otherwise the preserved tier and
    the flag disagree again."""
    prior = {"question_id": "q-1", "error_class": "ingest:retries_exhausted",
             "retryable": True, "attempts": 1}
    entry = runner._build_failure_entry(
        "q-1", "single-session-user", requests.exceptions.Timeout("slow"),
        "reader", 0, True, prior)
    assert entry["error_class"] == "ingest:retries_exhausted"
    assert entry["retryable"] is True
    assert entry["attempts"] == 2  # prior 1 + this re-attempt


# ── finding 1b: attempts reflects what happened ────────────────────────────
def test_initial_attempt_counts_as_one():
    assert _entry(SMOKE_EXC)["attempts"] == 1


def test_r2_counts_a_second_pipeline_attempt():
    entry = _entry(SMOKE_EXC, r2_attempted=1)
    assert entry["attempts"] == 2
    # R2 already spent the whole budget: the entry is retained, not re-attempted
    reason = runner._retry_failed_skip_reason(entry)
    assert reason is not None and "attempt budget exhausted" in reason


def test_resume_reattempt_increments_from_the_on_disk_prior():
    prior = {"question_id": "q-1", "error_class": "ingest:retries_exhausted",
             "retryable": True, "attempts": 1}
    entry = _entry(SMOKE_EXC, prior=prior, resume_reattempt=True)
    assert entry["attempts"] == 2


def test_a_recoverable_class_never_records_zero_attempts():
    """The pair the smoke published — ``retries_exhausted`` + ``attempts: 0`` —
    is unrepresentable now, at every producer path this builder has."""
    for kwargs in ({}, {"r2_attempted": 1},
                   {"prior": {"attempts": 1}, "resume_reattempt": True}):
        entry = _entry(SMOKE_EXC, **kwargs)
        assert entry["error_class"] == "ingest:retries_exhausted"
        assert entry["attempts"] >= 1, entry


# ── finding 2: the server's own message ────────────────────────────────────
def test_error_detail_carries_the_server_message():
    """⛔ FALSIFIER for finding 2: the driven ``ResponseError``'s sentence must
    survive into the record — ``repr`` cannot carry it (it is
    ``'server:ResponseError'``), so this fails on any build that persists only
    ``repr(exc)``."""
    entry = _entry(SMOKE_EXC)
    assert entry["error"] == "server:ResponseError"  # unchanged, byte-identical
    assert "graph was deleted or replaced" in entry["error_detail"]


def test_error_detail_distinguishes_a_refused_query_from_a_dropped_graph():
    """The diagnosis the record exists to support: two different server
    sentences, one class, and now one readable difference."""
    refused = _entry(redis_exc.ResponseError(
        "graph.RO_QUERY is to be executed only on read-only replica"))
    dropped = _entry(SMOKE_EXC)
    assert refused["error_class"] == dropped["error_class"]
    assert refused["error_detail"] != dropped["error_detail"]
    assert "read-only replica" in refused["error_detail"]


def test_error_detail_is_scrubbed_of_credentials():
    """The record is persisted and republished (``report.py`` passes
    ``failures`` through verbatim), so the detail goes through the product's
    credential redactor: no password may survive."""
    exc = redis_exc.ResponseError(
        "connect failed for redis://user:s3cr3t-pw@db.internal:6379/0 and "
        f"key sk-{'a' * 40} rejected")
    detail = _entry(exc)["error_detail"]
    assert "s3cr3t-pw" not in detail
    assert "a" * 40 not in detail
    assert "REDACTED" in detail
    # ...and the record stays diagnostic: the host and the sentence survive.
    assert "db.internal" in detail and "connect failed" in detail


def test_error_detail_is_bounded_and_collapsed():
    exc = redis_exc.ResponseError("line one\nline two " + "x" * 5000)
    detail = _entry(exc)["error_detail"]
    assert "\n" not in detail
    assert len(detail) <= runner.ERROR_DETAIL_CAP + len("…<truncated>")
    assert detail.endswith("…<truncated>")


def test_an_empty_message_omits_the_key_entirely():
    entry = _entry(redis_exc.ResponseError())
    assert "error_detail" not in entry


def test_a_broken_str_does_not_mask_the_failure():
    class Hostile(Exception):
        def __str__(self):  # noqa: D105
            raise RuntimeError("no str for you")

    entry = _entry(Hostile())
    assert entry["error_class"] == "ingest:retries_exhausted"
    assert "error_detail" not in entry
