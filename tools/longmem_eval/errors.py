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

import logging
from collections import Counter

from tortoise.model_adapters import classify_llm_error
from tortoise.retry import (  # noqa: F401  — re-exported for the eval harness
    WriteStageRetriesExhausted,
    call_with_predicate,
    retryable_transient,
)

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

# ══ PRODUCT-PARITY NOTE (WIRED — shipped to product in this PR) ══════════
# The write-stage retry machinery below (``retryable_transient`` /
# ``call_with_predicate`` / ``WriteStageRetriesExhausted``) now lives in
# the PRODUCT: tortoise/retry.py (product inversion,
# fix/invert-retrieval-to-product) — this module RE-EXPORTS it unchanged,
# so the eval's ingest write path behaves identically while the product
# owns the capability.
#   Product default: NO bounded retry on the SDK write path yet —
#       ``_post_commit`` is a single POST (tortoise/sdk.py:15240-15252) and
#       graph writes are un-retried; robustness = idempotent MERGE keys +
#       client_commit_id replay (sdk.py:1849-1857) + server-side
#       CommitRecordStore dedup + fail-closed error surfacing. The retry
#       PRIMITIVE is now a product module.
#   Follow-up (NOT this PR): wiring ``tortoise.retry`` into the SDK write
#       path (commit/capture writes) is the audit G8 candidate — the
#       module is the reusable bounded-retry primitive for that decision.
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
