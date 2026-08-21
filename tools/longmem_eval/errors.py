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

from collections import Counter
from typing import Any  # noqa: F401 (re-exported for convenience)

from tortoise.model_adapters import classify_llm_error

#: The full eval error-class vocabulary (published in ``integrity.error_census``).
EVAL_ERROR_CLASSES = (
    "fatal", "fatal_config", "transient", "retries_exhausted",
    "ingest", "parse", "unknown",
)

_SITES = ("reader", "judge", "ingest")


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

    - ``site == "ingest"`` → ``ingest``: the failure is extractor-internal
      by construction (the write path, not an LLM call) — the exception's
      HTTP/parse shape is not the interesting dimension.
    - reader/judge failures classified transient-safe (``transient`` or P2's
      ``unknown`` = transient-safe) have burned their retry budget by the
      time they reach the run-loop → ``retries_exhausted`` (a report must
      distinguish "retryable but exhausted" from "permanent").
    - fatal/fatal_config/parse pass through unchanged.
    """
    if site == "ingest":
        return "ingest"
    cls = classify_eval_error(exc, site=site)
    # Only transient/unknown convert to retries_exhausted — parse (a local
    # decode bug) is permanent and passes through, as do fatal/fatal_config.
    # (is_transient(parse-class) is True per P2's unknown-safe rule, so the
    # coarse class is checked, not is_transient.)
    if cls.split(":", 1)[1] in ("transient", "unknown"):
        return f"{site}:retries_exhausted"
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
