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
# ═════════════════════════════════════════════════════════════════════════
# ══ HARNESS PURPOSE — READ THIS FIRST ════════════════════════════════════
# tools/longmem_eval/ is a THIN MEASUREMENT LAYER over the product
# (tortoise/): the eval calls the product's OWN engine and measures it.
# Quality improvements belong IN tortoise/ (that is what ships to
# customers). The retry machinery in this module (write-stage retries,
# #1806) is now WIRED — ``retryable_transient`` / ``call_with_predicate`` /
# ``WriteStageRetriesExhausted`` ship in ``tortoise/retry.py`` (product
# inversion) and are RE-EXPORTED here unchanged. The eval still owns its
# run knobs (``INGEST_WRITE_RETRIES`` etc.). See
# docs/audit/2026-08-29-product-cohesion.md.
# ═════════════════════════════════════════════════════════════════════════
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/longmem_eval/errors.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]})"
    )

import logging
from collections import Counter

from tortoise.model_adapters import classify_llm_error
from tortoise.retry import (  # noqa: F401  — re-exported for the eval harness
    WriteStageRetriesExhausted,
    call_with_predicate,
    graph_abort_family,
    retryable_aborted_write,
    retryable_transient,
)

logger = logging.getLogger(__name__)

#: The full eval error-class vocabulary (published in ``integrity.error_census``).
EVAL_ERROR_CLASSES = (
    "fatal", "fatal_config", "transient", "retries_exhausted",
    "ingest", "parse", "unknown",
)

#: Extractor census classes that are HEALTHY SIGNATURES, never extraction
#: failures — the extractor bumps each with **no** paired ``errors`` entry
#: (``tortoise/extractor_v2.py``'s census docstring, "ADDITIVE ONLY") and each
#: names a state the pipeline is DESIGNED to reach:
#:
#:   * ``entity_identity_held`` (#11653) — S3 held an unresolved identity
#:     instead of minting it as a new node. The hold IS the fix, so a healthy
#:     run reaches this state routinely: phase-2 resolution runs on every
#:     normal ingest that has candidates.
#:
#: ⛔ They MUST NOT fold into ``error_census`` (#11653 review, finding 1).
#: ``run_integrity_gate`` reads a non-empty ``error_census`` as "extraction
#: FAILED" and returns ``reasons == []`` — which its own contract calls GREEN
#: — so folding a healthy signature here skipped all five integrity tiers for
#: a question with no error at all (a no-op gate), and stopped ``run.py``
#: writing its post-ingest cache marker, so the question re-ingested on every
#: run. Keep the fold's own stated intent: a session extraction EXCEPTION.
NON_FATAL_EXTRACTOR_CENSUS = frozenset({"entity_identity_held"})


def fatal_census(error_census: dict | None) -> dict:
    """The fatal subset of an extractor census, with the non-fatal
    HEALTHY signatures removed (see ``NON_FATAL_EXTRACTOR_CENSUS``)."""
    return {k: v for k, v in (error_census or {}).items()
            if k not in NON_FATAL_EXTRACTOR_CENSUS}

_SITES = ("reader", "judge", "ingest")

# ── #8486 F1: the recoverable-class set — ONE source of truth ──────────────
#: The bare classes a persisted failure entry may be RE-ATTEMPTED from.
#:
#: ``retryable`` in a failure record answers ONE question — *is re-attempting
#: this question plausible?* — and it is deliberately ONE half of the
#: eligibility test, not the whole of it. The gate (``run.py``'s
#: ``_retry_failed_skip_reason``) requires ``error_class ==
#: "ingest:retries_exhausted"`` as well, so a ``reader:``/``judge:`` entry can
#: record ``retryable: true`` and still never be re-attempted — the tier, not
#: the flag, is what scopes ``--retry-failed`` to the write-path surface
#: (the plan's P2-G). Read the flag as "recoverable in principle".
#:
#: It is derived from the entry's ``error_class`` through
#: :func:`class_is_recoverable`, gated by ONE further condition —
#: :func:`failure_is_recoverable` also requires that the exception be INFRA
#: rather than a deterministic bug — and from nothing else. The class alone is
#: deliberately NOT ``retryable_transient`` (the write-path
#: predicate in ``tortoise/retry.py``), which answers a DIFFERENT question —
#: *may THIS write be retried at THIS layer right now* — and returns False for
#: error families that are recoverable at a higher layer (a replaced-graph /
#: write-lock ``ResponseError`` is retried by ``retryable_aborted_write`` at the
#: SDK write seam, so the outer layer must not, per retry.py's own docstring).
#:
#: Borrowing that predicate for the record is #8486 finding 1: a
#: ``server:ResponseError`` graded ``ingest:retries_exhausted`` (recoverable)
#: was written with ``retryable: false``, so the resume meant to pick it up
#: skipped it — silently, because a skip is not an error.
#:
#: ⚠️ The class ALONE is not enough, and that is why the gate is a second
#: function rather than this set read directly. ``eval_failure_class`` widens
#: EVERY ``unknown``-class exception at ingest to ``retries_exhausted``
#: (#1776), so a deterministic bug in our own extractor and a transient server
#: refusal wear the SAME class. The plan pinning ``--retry-failed`` states the
#: complement explicitly — "Deterministic extractor bugs (``retryable=False``)
#: remain excluded — that is the guard against re-burning ~25 min on
#: deterministic failures that also widen to ``retries_exhausted``"
#: (``docs/plans/2026-08-27-1786-infra-resilience.md``, Task 2 Step 2). A
#: class-only flag would drop that guard as collateral damage from fixing
#: finding 1, so :func:`failure_is_recoverable` keeps it.
#:
#: ``transient`` / ``unknown`` cannot reach a persisted entry today
#: (``eval_failure_class`` converts both to ``retries_exhausted``); they are
#: listed so the set is the VOCABULARY's answer, not a restatement of one
#: branch — a future class that stops converting still grades coherently.
RECOVERABLE_EVAL_CLASSES = frozenset({"transient", "unknown", "retries_exhausted"})


def class_is_recoverable(error_class: str | None) -> bool:
    """True iff ``<site>:<class>`` names a RECOVERABLE eval error class.

    Reads the class half of the site-prefixed census key (``ingest:retries_
    exhausted`` → ``retries_exhausted``); a bare class (``ingest``, the
    structurally-fatal/parse ingest veto) is read as itself and is NOT
    recoverable. ``None``/empty → False (an entry with no class is never
    re-attempted — the resume gate rejects it on its own class check first).

    This is the CLASS half of the answer only; the re-attempt decision is
    :func:`failure_is_recoverable`.
    """
    cls = str(error_class or "").rsplit(":", 1)[-1].strip()
    return cls in RECOVERABLE_EVAL_CLASSES


def infra_failure(exc: BaseException) -> bool:
    """True iff ``exc`` is an INFRA failure rather than a deterministic bug.

    The one signal ``error_class`` cannot carry (see the note on
    :data:`RECOVERABLE_EVAL_CLASSES`): ``eval_failure_class`` widens both an
    ``unknown``-class server refusal and an ``unknown``-class ``RuntimeError``
    in our own extractor to ``<site>:retries_exhausted``. This answers the
    other half — *would re-running the same question plausibly succeed?*

    Deliberately narrow, because a false True costs two ~25-minute re-burns
    per question while a false False costs a retained entry an operator can
    see and hand-recover:

    * ``retryable_transient(exc)`` — the write seam's own transport/provider
      predicate (``tortoise/retry.py``); an extractor LLM transient
      (``requests.Timeout``/``URLError``) is in scope by the plan's P2-2 pin.
    * ``graph_abort_family(exc)`` — the engine ABORTED the statement because the
      graph was deleted or replaced (``_GRAPH_ABORT_RE``). This is the smoke's
      OWN failure, and the reason finding 1 exists.
    * ``retryable_aborted_write(exc)`` — the write-slot refusal and the
      MISCONF/disk-full family (``_WRITE_LOCK_RE`` / ``_MISCONF_RE``), which
      the plan's P1-7 pin already treats as re-attemptable. ``graph_exists`` is
      omitted on purpose: that argument gates the graph-abort arm ONLY, which
      the line above decides from the error text, so the omitted probe is not
      a silent refusal of anything in scope here.

    ⛔ NOT ``isinstance(exc, redis.exceptions.RedisError)``. That base class is
    emphatically not infra: it covers the deterministic refusals — ``WRONGTYPE``
    (the client sent the wrong operation for the key), ``NOPERM`` (an ACL that
    will refuse again on every attempt), ``ERR unknown command``, and
    ``redis.exceptions.DataError``, which redis-py's ENCODER raises before a
    byte reaches the server. Marking those recoverable re-introduces exactly
    the burn the plan's guard forbids, at 2 × ~25 min per question.

    Everything else — a ``TypeError``/``KeyError``/``RuntimeError`` from our
    own code, and the deterministic redis refusals above — is excluded.

    ⚠️ Deliberately NOT in scope: a bare ``ResponseError('Query timed out')``
    matches none of the three families, so it stays excluded — which is what
    it did before this change. Adding it would be a new decision, not a fix.
    """
    return (retryable_transient(exc)
            or graph_abort_family(exc)
            or retryable_aborted_write(exc))


def failure_is_recoverable(exc: BaseException, *,
                           error_class: str | None) -> bool:
    """The persisted ``retryable`` flag: recoverable class AND infra failure.

    Both conditions are required and they answer different questions, which is
    why a failure record must not carry either alone (that is #8486 finding 1,
    the flag and the class contradicting each other): ``error_class`` says
    *where in the taxonomy this landed* (and hence whether the tier admits
    re-attempts at all), ``infra_failure`` says *whether the blame is the
    environment or our code*.
    """
    return class_is_recoverable(error_class) and infra_failure(exc)

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
#: Resume re-attempt cap (R3): the persisted ``attempts`` field bounds the
#: number of PIPELINE ATTEMPTS a question may consume across processes — the
#: initial attempt is 1, the in-run R2 makes it 2, each failed
#: ``--retry-failed`` re-attempt adds one. At this value the documented budget
#: is preserved EXACTLY: a question that exhausted the write-stage retries and
#: its in-run R2 (2 attempts) still gets its one resume (attempt 3).
#:
#: #8486 finding 1 — why the field and this cap moved together. The field used
#: to count only RE-BURNS (a provider failure that never entered the
#: write-stage loop started at ``0``), so a persisted record could say
#: ``error_class=ingest:retries_exhausted`` — "the retry budget burned" — with
#: ``attempts: 0``, "nothing was tried", next to ``retryable: false``. Two
#: numbers, three wrong readings. Counting attempts MADE is the honest reading
#: of a field named ``attempts``, and it is what an operator needs to tell a
#: 12-minute ingest that failed once from one that failed three times. The cap
#: is +1 (2 → 3) so the SAME worst-case recovery surface is preserved —
#: TWO re-burns either way, ~2 × 25 min: a question that exhausted the
#: write-stage retries and its in-run R2 (attempts 2) burns them as R2 + one
#: resume (3), and a plain initial failure (attempts 1) burns two resumes
#: (2, then 3). ⚠️ Only the PLAIN path gets two resumes; the R2 path gets one
#: because R2 itself was the first re-burn. A resumed checkpoint
#: written under the old accounting is refused by the fingerprint (the value
#: rides ``resume_attempts_cap``), which is the correct outcome — its
#: ``attempts`` numbers would be read two ways.
RESUME_ATTEMPTS_CAP = 3

# ══ PRODUCT-PARITY NOTE (WIRED — shipped to product in this PR) ══════════
# The write-stage retry machinery below (``retryable_transient`` /
# ``call_with_predicate`` / ``WriteStageRetriesExhausted``) now lives in
# the PRODUCT: tortoise/retry.py (product inversion,
# fix/invert-retrieval-to-product) — this module RE-EXPORTS it unchanged,
# so the eval's ingest write path behaves identically while the product
# owns the capability.
#   Product default (#7405): ⛔ The exact COVERAGE LIST lives in exactly one
#       place — ``tortoise/retry.py``'s "PARTIALLY WIRED" header — and is
#       deliberately NOT enumerated here, so this note cannot drift into a
#       second, stale claim (the failure #7405's header exists to prevent).
#       Read it there. The SDK's OTHER mutating surfaces remain un-retried:
#       they are pure GRAPH writers (no ``client_commit_id``, no
#       ``CommitRecordStore`` involvement), so their robustness is still
#       idempotent-MERGE + server-side dedup.
#   Eval role: this module RE-EXPORTS the product primitive unchanged; the
#       eval's outer phase loop is unchanged, so its behaviour is identical
#       and its retry-budget knobs stay eval-side.
# ═════════════════════════════════════════════════════════════════════════


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
        return f"{site}:retries_exhausted"  # recoverable — see class_is_recoverable
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
