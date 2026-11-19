"""#8486 findings 1 + 2 — the persisted failure record must not contradict
itself, and it must carry the server's own sentence.

The 10-question smoke (``--limit 10 --retrieval-only``, main ``7d3470909a``)
persisted this entry for question ``c5e8278d``::

    error        server:ResponseError
    error_class  ingest:retries_exhausted      # recoverable  (eval_failure_class)
    retryable    false                          # ...so a resume SKIPPED it
    attempts     0                              # ...as if nothing was tried

Three separate defects, pinned here as one invariant each:

1. ``retryable`` is derived from the entry's ``error_class`` **AND** an infra
   predicate (``errors.py::failure_is_recoverable``) — a recoverable class is
   NOT on its own enough, so a deterministic FAILURE inside a recoverable class
   is written ``retryable: false`` legitimately (the resume gate keys on the
   flag, so what must never happen is a *recoverable, infra* failure graded
   ``false``: that is the silent skip, not a visible error).
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

#: The exception SHAPE the smoke's record is consistent with: a bare
#: ``ResponseError``. ⚠️ Consistency, not identification — the record's ``error``
#: field is ``server:ResponseError`` and carries no server sentence (that is
#: finding 2's premise), so a query timeout would persist the identical record.
#: This fixture therefore models the shape; the rung-10 re-run is what names the
#: family. Its repr is
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


def _entry(exc, *, stage="ingest", prior=None,
           r2_attempted=0, resume_reattempt=False):
    return runner._build_failure_entry(
        "q-1", "single-session-user", exc, stage, r2_attempted,
        resume_reattempt, prior)


# ── finding 1a: class ↔ retryable coherence ────────────────────────────────
@pytest.mark.parametrize("label,exc,site,expected_class", TAXONOMY_TABLE,
                         ids=[row[0] for row in TAXONOMY_TABLE])
def test_retryable_agrees_with_the_graded_class(label, exc, site,
                                                expected_class):
    """A WIRING pin, not the falsifier: ``_failure_entry`` computes exactly
    this expression (``run._failure_entry``), so the row proves the entry is
    wired to ``failure_is_recoverable`` rather than to ``class_is_recoverable``
    or to a constant -- it reds if that call is replaced. It does NOT pin which
    failures are recoverable: a mutation of ``infra_failure`` leaves all rows
    green. The falsifiers for that are the two tables below
    (``DETERMINISTIC_REDIS_REFUSALS`` and ``INFRA_REDIS_FAMILIES``), and
    ``test_the_transient_value_redis_families`` pins the families whose flag
    this PR changes.
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
#: because these four all reach ``classify_llm_error`` as ``unknown`` (they are
#: redis exceptions, so none of them is HTTP/transport/URLError) and
#: ``eval_failure_class`` converts ``unknown`` to ``retries_exhausted`` at any
#: site — so the CLASS cannot separate them from the smoke's abort, and the
#: ``infra_failure`` gate is the only thing that can. ⚠️ It is NOT a general
#: rule that "everything non-HTTP, non-transport widens": ``classify_eval_error``
#: takes a ``parse`` pre-branch for ``KeyError``/``ValueError``/``TypeError``,
#: and ``parse`` does not convert — at ingest it becomes the bare ``ingest``
#: veto, which is what ``test_a_permanent_class_is_never_retryable`` asserts.
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


#: The families whose persisted flag the round-2 narrowing CHANGES, with the
#: direction each must land in, so the record and the code cannot drift apart.
#: Measured, not assumed: ``BusyLoadingError`` is a ``ConnectionError``
#: subclass, so it is the row that keeps this table from reading "every
#: ``ResponseError`` is excluded".
TRANSIENT_VALUE_REDIS_FAMILIES = [
    ("query-timed-out", redis_exc.ResponseError("Query timed out"), False),
    ("readonly-replica", redis_exc.ReadOnlyError(
        "You can't write against a read only replica"), False),
    ("oom", redis_exc.OutOfMemoryError(
        "OOM command not allowed when used memory > 'maxmemory'"), False),
    ("loading", redis_exc.BusyLoadingError(
        "LOADING Redis is loading the dataset in memory"), True),
]


@pytest.mark.parametrize(
    "label,exc,expected", TRANSIENT_VALUE_REDIS_FAMILIES,
    ids=[row[0] for row in TRANSIENT_VALUE_REDIS_FAMILIES])
def test_the_transient_value_redis_families(label, exc, expected):
    """⛔ The pin for the ONE behaviour delta the round-2 narrowing makes.

    ``READONLY``, ``OOM`` and a bare query timeout are transient-VALUE
    conditions: the removed ``isinstance(exc, RedisError)`` arm admitted them
    and the three named predicates do not, so they must persist
    ``retryable: false`` — the safe direction (a retained entry an operator can
    see and hand-recover, versus ~25 min per attempt spent on a flag nobody
    reads). If a future arm admits one of them, or a future ``return False``
    costs ``LOADING`` its admission, this table reds instead of the record
    quietly disagreeing with the code — which is how the previous version of
    the §12 amendment came to name ``LOADING`` as excluded when
    ``retryable_transient`` admits it through ``ConnectionError``.

    Admitting the other three is a deliberate non-goal of #8486 (it is a budget
    decision, not a fix), recorded in the plan's §12.
    """
    assert lme_errors.infra_failure(exc) is expected, (
        f"{label}: infra_failure disagrees with the §12 record")
    entry = _entry(exc)
    assert entry["error_class"] == "ingest:retries_exhausted", (
        "precondition: the class cannot carry the distinction — these families "
        "all widen to the same key at ingest")
    assert entry["retryable"] is expected
    assert (runner._retry_failed_skip_reason(entry) is None) is expected, (
        f"{label}: the resume gate must agree with the flag")


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
    credential redactor.

    What this pins is the SHAPES that redactor recognises — a connection-URL
    password and an ``sk-`` key — not a blanket "no secret survives": it is
    shape-based, so ``password=hunter2`` and an ``Authorization: Basic`` header
    pass through untouched (measured). Please do not widen this claim without
    widening the redactor first.
    """
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


# ── the scrub has no input budget that can be bisected (#8486 round 7) ─────
#: The credential the REVERTED ``SCRUB_INPUT_CAP = 64_000`` let through into the
#: persisted record, and the windowed fragments a leak is measured by: a
#: whole-value substring test misses the PREFIX-only leaks a cut produces.
_LEAK_PW = "s3cr3t-pw-0123456789"
_LEAK_WINDOWS = tuple(
    _LEAK_PW[i:i + 8] for i in range(len(_LEAK_PW) - 7))
_LEAK_URL = "redis://lme:" + _LEAK_PW + "@db.internal:6379/0"

#: A non-secret token placed AFTER the credential. It survives only if the WHOLE
#: message reached the redactor, which is what makes this assertion independent
#: of WHERE a budget is drawn: any truncation before the end drops the sentinel,
#: whatever value the cap has.
_TAIL_SENTINEL = "SENTINEL-AFTER-THE-CREDENTIAL"


def _leak_shaped_message(pem_body: int) -> str:
    """A TERMINATED PEM block, then a credentialed URL, then the sentinel.

    The block matters: the redactor collapses it to one ~20-char marker (≈3,000×
    compression at this size), so the text AFTER it still lands inside the
    stored ``ERROR_DETAIL_CAP``/``ERROR_REPR_CAP`` window once the block is
    gone. Without that compression a bisected credential would be pushed past
    the window by the padding and this shape could not leak at all.
    """
    return ("-----BEGIN RSA PRIVATE KEY-----\n" + "M" * pem_body
            + "\n-----END RSA PRIVATE KEY-----\n"
            + _LEAK_URL + _TAIL_SENTINEL)


@pytest.mark.parametrize("pem_body", [200, 63_905, 130_000],
                         ids=["short", "at-the-reverted-cap", "past-it"])
def test_the_scrub_scans_the_whole_message_not_a_prefix(pem_body):
    """Truncating the input BEFORE redacting is a leak, not an optimisation.

    This module tried it once (``SCRUB_INPUT_CAP = 64_000``, reverted by
    ``516904ad91``). A credential whose TERMINATOR falls past the cut is not
    recognised at all, so its cleartext prefix was stored: measured on
    ``pem_body=63_905`` — the value whose password ends exactly at the cut —
    ALL THIRTEEN windows of the password survived, the full ``s3cr3t-pw-…``
    text sitting in ``error_detail``.

    ⛔ Two assertions, because either alone is satisfiable by a wrong
    implementation. The SENTINEL makes the test independent of the cap's value
    (anything that drops the tail of the message reds it), and the windowed
    fragments pin the property the sentinel only implies. ``pem_body=63_905``
    is the exact reproduction, kept so a future cap of the same value cannot be
    reintroduced green; the other two sizes keep the sentinel assertion honest.
    """
    scrubbed = runner._scrub(_leak_shaped_message(pem_body))
    assert _TAIL_SENTINEL in scrubbed, (
        f"pem_body={pem_body}: the tail of the message never reached the "
        "redactor — something truncated the input, and a truncated input is a "
        "leak (a credential whose terminator falls past the cut is not "
        "recognised, and the redactor LENGTHENS a connection-URL span, so it "
        "cannot be assumed to shrink the text back under the window)")
    leaked = [w for w in _LEAK_WINDOWS if w in scrubbed]
    assert not leaked, (
        f"pem_body={pem_body}: {len(leaked)} fragment(s) of the password "
        f"survived the redactor, e.g. {leaked[:3]}")


def test_a_credential_starting_a_line_is_not_hidden_by_repr_escaping():
    """``repr`` ESCAPES a newline, and the escape is what hid the secret.

    ``error`` is scrubbed from ``repr(exc)``, and the redactor's connection-URL
    anchor is ``(?<![A-Za-z0-9])``. A newline in the message reaches the
    redactor as the two characters ``\\n`` — whose trailing ``n`` is alphanumeric
    — so a credential that BEGINS a line was never matched and its cleartext
    password was persisted to the checkpoint and republished by ``report.py``.

    Measured on the pre-fix head: this fixture's ``error`` read
    ``ValueError('redis connect failed\\nredis://user:s3cr3t-pw@db.internal:6379/0')``.
    The pre-existing repr pin passed throughout because its fixture keeps the
    URL on the message's FIRST line, where the preceding character is a space.
    Both are in the table below so neither shape can regress unnoticed.
    """
    exc = ValueError(
        "redis connect failed\nredis://user:s3cr3t-pw@db.internal:6379/0")
    err = _entry(exc)["error"]
    assert "s3cr3t-pw" not in err, (
        "the password reached `error` in cleartext — repr escaping defeated "
        "the redactor's anchor")
    assert "REDACTED" in err, "the record must still SAY a credential was stripped"
    assert "db.internal" in err, "and stay diagnostic about the target host"
    # the class name the legacy rescue greps for must survive either rendering
    assert "ValueError" in err


def test_a_credential_on_the_second_line_of_a_redis_error_survives_redaction():
    """The same shape on the family whose repr DROPS ``args``.

    A ``RedisError`` cannot leak through ``error`` (its repr is
    ``server:ResponseError``), so a pin built only on one proves nothing about
    ``error_detail``, which is ``str(exc)`` and carries the message verbatim.
    """
    exc = redis_exc.ResponseError(
        "graph was deleted\nredis://user:s3cr3t-pw@db.internal:6379/0")
    entry = _entry(exc)
    for field in ("error", "error_detail"):
        assert "s3cr3t-pw" not in entry[field], f"{field} leaked the password"
    assert "REDACTED" in entry["error_detail"]


def test_the_persisted_record_cannot_carry_a_credential_past_a_cut():
    """The same shape through the PERSISTED builder, both persisted fields.

    ``error`` and ``error_detail`` are both written to the checkpoint and both
    republished by ``report.py``, so the leak is measured on the record a reader
    and a resume actually see — not on ``_scrub``'s return value alone. This is
    the end-to-end half of the pin above; on the pre-revert code it read
    ``redis://lme:s3cr3t-pw-0123456789`` in ``error_detail``.
    """
    entry = _entry(ValueError(_leak_shaped_message(63_905)))
    for field in ("error", "error_detail"):
        leaked = [w for w in _LEAK_WINDOWS if w in entry[field]]
        assert not leaked, (
            f"{field}: {len(leaked)} fragment(s) of the password reached the "
            f"persisted record, e.g. {leaked[:3]}")
    # ...and the record stays diagnostic: both redactions are NAMED, so a reader
    # can tell a credential was stripped rather than the text being lost.
    assert "[REDACTED:private_key]" in entry["error_detail"]
    assert "[REDACTED:connection_url]" in entry["error_detail"]


def test_the_legacy_rescue_still_matches_after_scrubbing():
    """Redaction must not remove the transport CLASS NAME from ``error``.

    This is an over-redaction guard, not a rescue-path guard: ``_legacy_repr_match``
    is consulted only when ``retryable is None``
    (``run._retry_failed_skip_reason``), and every entry ``_failure_entry``
    writes carries a bool, so no entry this version scrubs can reach that
    branch. What it pins is the weaker property that makes
    the legacy path safe to keep: ``redact_secrets`` rewrites credential-SHAPED
    spans only, so the substring ``TimeoutError`` survives byte-identical. A
    redactor that rewrote whole tokens would break the legacy read of an
    on-disk checkpoint, which is the failure this guards against.
    """
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


def test_a_redactor_failure_does_not_mask_the_failure(monkeypatch):
    """The OTHER half of "a failure record must never mask the failure".

    ``_error_detail`` guards ``str(exc)`` but the round-2 review found the
    redactor sitting OUTSIDE that guard: ``redact_secrets`` walks arbitrary
    provider/FalkorDB text, so a raise inside it is the same class of hazard —
    while ``_failure_entry`` calls ``_scrub(repr(exc))`` for ``error`` at the
    top level, where a raise aborts the ENTRY BUILD and loses the record
    entirely.

    Non-vacuity: on the pre-fix head the monkeypatched redactor escapes
    ``_build_failure_entry``, so this row errors instead of asserting — the
    failure is masked by its own report. Post-fix it must FAIL CLOSED:
    ``error`` falls back to the class name (no ``args``, so no credential can
    ride it), ``error_detail`` is omitted, and the three fields that make the
    entry actionable are untouched.
    """
    def boom(*_a, **_kw):
        raise RuntimeError("redactor exploded")

    monkeypatch.setattr(runner, "_scrub", boom)
    exc = redis_exc.ResponseError(
        "graph was deleted or replaced while the query was running, aborting "
        "(redis://u:s3cr3t-pw@h)")
    entry = _entry(exc)

    assert entry["error_class"] == "ingest:retries_exhausted"
    assert entry["retryable"] is True
    assert entry["attempts"] == 1
    assert "error_detail" not in entry, (
        "the detail cannot be emitted unscrubbed — fail closed")
    assert entry["error"] == "ResponseError", (
        "the class-name fallback keeps the record readable without carrying "
        "args")
    assert "s3cr3t-pw" not in entry["error"]


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
