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

import pytest
import redis.exceptions as redis_exc
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
    class (and of the one infra/determinism gate that the class cannot carry),
    so neither direction may drift.

    The expectation is computed by ``failure_is_recoverable``, NOT by
    ``class_is_recoverable`` alone: ``eval_failure_class`` widens every
    ``unknown``-class exception at ingest to ``ingest:retries_exhausted``, so
    a deterministic ``RuntimeError`` and a refused query wear the same class.
    ``test_a_deterministic_bug_stays_excluded`` is the row that pins which way
    that must fall.
    """
    entry = _entry(exc, stage=site)
    assert entry["error_class"] == expected_class
    assert entry["retryable"] is lme_errors.failure_is_recoverable(
        exc, error_class=expected_class), (
        f"{label}: error_class={entry['error_class']!r} recorded "
        f"retryable={entry['retryable']!r} — class and flag must agree, or a "
        f"--retry-failed resume skips (or over-retries) the failure"
    )


def test_a_deterministic_bug_stays_excluded():
    """The plan's pinned guard, kept alive by the fix rather than dropped as
    collateral damage.

    ``docs/plans/2026-08-27-1786-infra-resilience.md`` (Task 2 Step 2):
    "Deterministic extractor bugs (``retryable=False``) remain excluded — that
    is the guard against re-burning ~25 min on deterministic failures that
    also widen to ``retries_exhausted``".

    The class cannot express that distinction (both widen to the same key), so
    the flag carries it: a ``RuntimeError`` from our own extractor is NOT
    infra, and must not be re-attempted — while the redis refusal that finding
    1 was about must be. Without this pin, deriving ``retryable`` from the
    class alone would silently re-admit deterministic bugs at 2 × ~25 min
    each.
    """
    deterministic = RuntimeError("extractor bug: unparseable turn")
    assert lme_errors.eval_failure_class(deterministic, site="ingest") == (
        "ingest:retries_exhausted")  # ...the SAME class as SMOKE_EXC
    assert lme_errors.class_is_recoverable("ingest:retries_exhausted") is True

    entry = _entry(deterministic)
    assert entry["retryable"] is False, (
        "a deterministic extractor bug must not be re-attempted — re-running "
        "the same code reproduces it and costs ~25 min per attempt")
    assert runner._retry_failed_skip_reason(entry) is not None, (
        "the resume gate must EXCLUDE it, visibly, not skip it silently")

    # ...and the smoke's own failure is the contrast: same class, admitted.
    assert _entry(SMOKE_EXC)["retryable"] is True


#: Redis exceptions that are DETERMINISTIC, not infra: each one reproduces on
#: every attempt because the REQUEST decides it (the wrong operation for the
#: key, an ACL that will refuse the same way forever, a command the server does
#: not have) or because it is raised by redis-py's ENCODER before a byte is
#: sent (``DataError``). Every row still grades ``ingest:retries_exhausted``,
#: because ``eval_failure_class`` widens every non-HTTP, non-transport
#: exception at the ingest site — so the CLASS cannot separate them from the
#: smoke's abort, and the ``infra_failure`` gate is the only thing that can.
DETERMINISTIC_REDIS_REFUSALS = [
    ("wrongtype", redis_exc.ResponseError(
        "WRONGTYPE Operation against a key holding the wrong kind of value")),
    ("noperm", redis_exc.ResponseError(
        "NOPERM User tenant_acme has no permissions to run the "
        "'GRAPH.QUERY' command")),
    ("unknown-command", redis_exc.ResponseError(
        "ERR unknown command 'GRAPH.FROB'")),
    ("data-error", redis_exc.DataError(
        "Invalid input of type: 'dict'. Convert to a bytes, string, int or "
        "float first.")),
]


@pytest.mark.parametrize("label,exc", DETERMINISTIC_REDIS_REFUSALS,
                         ids=[row[0] for row in DETERMINISTIC_REDIS_REFUSALS])
def test_a_deterministic_redis_refusal_stays_excluded(label, exc):
    """⛔ THE FALSIFIER for an ``isinstance(exc, RedisError)`` infra arm.

    ``redis.exceptions.RedisError`` is the BASE class, so it covers the
    deterministic refusals as well as the infra ones. Grading "infra" from
    base-class membership re-admits all four rows below at 2 × ~25 min per
    question, and — because the burn is bounded only by the attempt cap —
    turns one ACL mistake into a multi-hour burn across the whole failure
    list. Measured on that build, each row graded ``retryable: True`` and
    ``_retry_failed_skip_reason`` returned ``None`` (admitted), on a record
    that was ``retryable: False`` before this PR.

    The three NAMED infra families (graph-abort, MISCONF/disk-full, the
    write-slot refusal) are the contrast, pinned separately in
    ``test_the_infra_redis_families_are_admitted`` — so this pair of tests
    fails if the gate is widened back to the base class AND if it is narrowed
    until the smoke's own failure stops being re-attemptable.
    """
    assert lme_errors.eval_failure_class(exc, site="ingest") == (
        "ingest:retries_exhausted"), (
        "precondition: the class cannot carry the distinction — if this row "
        "ever grades non-recoverable, this pin is no longer proving anything"
    )
    assert lme_errors.infra_failure(exc) is False, (
        f"{label}: a deterministic redis refusal is NOT infra — re-running "
        f"the same question reproduces it exactly")

    entry = _entry(exc)
    assert entry["retryable"] is False, (
        f"{label}: admitted as retryable — a --retry-failed resume would "
        f"re-burn ~25 min per attempt on an error that cannot change")
    assert runner._retry_failed_skip_reason(entry) is not None, (
        f"{label}: the resume gate must EXCLUDE it visibly, not admit it")


#: The redis families the gate DOES name as infra, with the text each
#: predicate in ``tortoise/retry.py`` actually matched on (``_GRAPH_ABORT_RE``,
#: ``_MISCONF_RE``, ``_WRITE_LOCK_RE``) — a paraphrase would make the pin vacuous.
INFRA_REDIS_FAMILIES = [
    ("graph-aborted", redis_exc.ResponseError(
        "graph was deleted or replaced while the query was running, aborting")),
    ("misconf", redis_exc.ResponseError(
        "MISCONF Redis is configured to save RDB snapshots, but it's "
        "currently unable to persist to disk")),
    ("write-lock", redis_exc.ResponseError(
        "another write is in progress, retry the query")),
]


@pytest.mark.parametrize("label,exc", INFRA_REDIS_FAMILIES,
                         ids=[row[0] for row in INFRA_REDIS_FAMILIES])
def test_the_infra_redis_families_are_admitted(label, exc):
    """The other half of the round-2 pin: narrowing the arm must not cost the
    smoke's own failure (or the two families the plan already treats as
    re-attemptable) their admission. Without this contrast a future "fix" of
    the form ``return False`` would leave the P1 test above green.
    """
    assert lme_errors.infra_failure(exc) is True, (
        f"{label}: an infra redis family must stay re-attemptable — the "
        f"failure that motivated #8486 is one of these, and excluding it "
        f"is the original silent skip")
    entry = _entry(exc)
    assert entry["retryable"] is True
    assert entry["error_class"] == "ingest:retries_exhausted"
    assert runner._retry_failed_skip_reason(entry) is None, (
        f"{label}: the resume gate must ADMIT it")


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
    # the documented budget is preserved: initial + R2 = 2 attempts still leaves
    # the ONE resume the R3 tier exists for (cap 3), and the gate admits it.
    assert runner._retry_failed_skip_reason(entry) is None
    # ...and the entry the resume leaves behind is the one that is retained.
    spent = _entry(SMOKE_EXC, prior={"attempts": 2}, resume_reattempt=True)
    assert spent["attempts"] == 3
    reason = runner._retry_failed_skip_reason(spent)
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


def test_the_persisted_repr_is_scrubbed_too():
    """``error`` is persisted AND republished, so it may not be the leak either.

    ``RedisError``'s repr drops ``args`` — the one family that CANNOT leak —
    so a credential pin built only on it proves nothing about the rest. This
    uses a plain exception, whose repr embeds the message verbatim, which is
    the shape that actually reaches ``report.py``'s ``failures``.
    """
    exc = ValueError(
        "connect failed for redis://user:s3cr3t-pw@db.internal:6379/0 and "
        f"key sk-{'a' * 40} rejected")
    err = _entry(exc)["error"]
    assert "s3cr3t-pw" not in err
    assert "a" * 40 not in err
    assert "REDACTED" in err
    # the class name the legacy rescue greps for must survive redaction
    assert "ValueError" in err


def test_the_legacy_rescue_still_matches_after_scrubbing():
    """``_legacy_repr_match`` greps the persisted ``error`` for a transport
    class name; redaction must not remove it, or a pre-feature checkpoint stops
    being recoverable."""
    exc = TimeoutError("connect failed for redis://u:pw@db.internal:6379/0")
    err = _entry(exc)["error"]
    assert "TimeoutError" in err
    assert runner._legacy_repr_match(err) is True


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
        def __str__(self):
            raise RuntimeError("no str for you")

    entry = _entry(Hostile())
    assert entry["error_class"] == "ingest:retries_exhausted"
    assert "error_detail" not in entry
