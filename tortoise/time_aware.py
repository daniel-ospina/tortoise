"""Time-aware query expansion (#2520, C6 of epic #2513).

The measured defect (reproduced on #2520): a knowledge-update question
("where do I live now?") returns the **superseded** fact as current. Three
distinct causes, all fixed here or by the callers of this module:

1. **The query string carries no date.** ``question_date`` reaches only the
   *reader* header (``render_context``); the dense leg embeds the bare
   question, so nothing can express "which version was current on X".
   → :func:`inject_query_date`.
2. **Temporal intent is gated on the question's CATEGORY.** The eval's R5
   stack (``detect_time_constraint`` → ``_apply_time_window``, recency
   weighting, the point+event union) runs only when
   ``question_type == "temporal-reasoning"``. A KU/MSR question phrased
   "currently / now / did I switch X?" gets none of it.
   → :func:`detect_temporal_intent` detects the intent from the **query
   text**, independent of any label.
3. **Rank order is blind to supersession state.** ``superseded_by`` /
   ``validTo`` / ``expiredAt`` (E5 #1537, E6 #1538) exist precisely so a read
   surface can prefer the latest version, and no rank-time surface does.
   → :func:`prefer_latest_order`.

Publication surface: pure functions + one frozen dataclass. No DB access,
no I/O, no LLM calls, no random, no clock — the same inputs always give the
same output. The eval lane (:mod:`tools.longmem_eval.retrieve`) and the SDK
(:meth:`tortoise.sdk.TortoiseSDK.tortoise_fts_query`) are thin callers.

Relationship to the two existing temporal detectors — deliberately distinct,
NOT a third copy of one concept:

* ``tools/longmem_eval/retrieve.py::detect_time_constraint`` (R5 #1544) and
  its declared product mirror
  ``tortoise/coverage_loop.py::facet_date_constraint`` answer a **window**
  question: *"which dated slice of the pool does this question need?"*
  (``interval`` / ``recency`` / ``ordering``), and drive a hard filter /
  census qualifier.
* :func:`detect_temporal_intent` answers a **freshness** question: *"does
  this question want the CURRENT version, or a PINNED past one?"* It drives
  a rank preference, never a filter.

The two vocabularies are therefore deliberately disjoint
(``{"prefer-latest", "date-pinned"}`` vs ``{"interval", "recency",
"ordering"}``); ``recency`` in the window detectors means "older-than-N",
while :data:`PREFER_LATEST` here means "the user asked what is true now".
``tests/test_time_aware_2520.py`` asserts the disjointness so a future edit
cannot silently merge the concepts.

The "never-starve" rule inherited from R5 (#1544 D5) is structural here:
:func:`prefer_latest_order` is a **stable reorder** — membership is never
changed — so no query can be starved of evidence by this module. When no
entry is stale, or every entry is stale, the input order is returned
untouched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .live import is_terminal_status

__all__ = [
    "DATE_PINNED",
    "DEFAULT_TIME_AWARE_RECENCY_WEIGHT",
    "PREFER_LATEST",
    "QUERY_DATE_SUFFIX",
    "TemporalIntent",
    "dense_query_for",
    "detect_temporal_intent",
    "inject_query_date",
    "is_stale_entry",
    "prefer_latest_order",
]

# ── intent kinds ────────────────────────────────────────────────────────────

#: The query asks about the CURRENT state ("now", "currently", "did I switch
#: X") — the fresh/latest version is the answer.
PREFER_LATEST = "prefer-latest"

#: The query pins a PAST window (a year, a month, "back in", "used to",
#: "N days ago") — the version current *then* is the answer, so the fresh
#: bias must NOT apply (the "invert recency" failure mode the research names).
DATE_PINNED = "date-pinned"

#: Default strength of the engine recency re-rank for a non-TR
#: ``prefer-latest`` question. Deliberately the SAME as the TR weight
#: (``retrieve_for_question``'s ``tr_date_weight``, 0.5) — the posture is
#: AutoMem's ``RECALL_RECENCY_BIAS=auto``: apply a bounded recency nudge
#: ONLY when the query expresses temporal intent. Fixed for the sealed run;
#: changing it re-cuts the arm (it is not a harness knob).
DEFAULT_TIME_AWARE_RECENCY_WEIGHT = 0.5

#: The query-side date-anchor format. The *reader* header uses the
#: ``Current Date: <YYYY-MM-DD>`` prefix (``tortoise.retrieval.render_context``);
#: the *query* is an embedding input, so the anchor is a parenthetical
#: suffix instead. The two formats are deliberately different and both are
#: declared — this constant is the single source for the query side.
QUERY_DATE_SUFFIX = " (as of {date})"

# ── detectors ───────────────────────────────────────────────────────────────

#: `date-pinned`: an explicit past window is named. A bare 4-digit year, an
#: ISO date, a month name, or a relative past expression. Checked BEFORE
#: `prefer-latest` — a pinned date wins (the invert-recency guard).
_DATE_PINNED_RE = re.compile(
    # ISO date / 4-digit year ("in 2024", "2025-06-01")
    r"\b\d{4}-\d{2}-\d{2}\b"
    r"|\b(?:19|20)\d{2}\b"
    # month names ("back in March", "during June")
    r"|\b(?:january|february|march|april|may|june|july|august|september"
    r"|october|november|december)\b"
    # explicit past framing
    r"|\bback (?:in|then|when)\b"
    r"|\bused to\b"
    r"|\bpreviously\b|\bformerly\b|\bat the time\b"
    r"|\b(?:yes)?terday\b|\bthe other day\b"
    # relative past spans
    r"|\b\d+\s+(?:days?|weeks?|months?|years?)\s+ago\b"
    r"|\b(?:last|past)\s+(?:week|month|year|night|weekend|summer|spring"
    r"|fall|autumn|winter|monday|tuesday|wednesday|thursday|friday"
    r"|saturday|sunday)\b"
    # explicit interval
    r"|\bbetween\b[^?]{0,60}?\band\b"
)

#: `prefer-latest`: the query asks about the CURRENT version. Current-time
#: adverbs, "still", superlatives, and change phrasing ("has the user
#: changed their mind", "did I switch X") — the KU/MSR phrasings the issue
#: names.
_PREFER_LATEST_RE = re.compile(
    # current-time adverbs / deictics
    r"\bnow\b|\bnowadays\b|\bcurrently\b|\bcurrent\b|\bpresently\b"
    r"|\bthese days\b|\banymore\b|\bany ?more\b|\bthese days\b"
    # continuity / freshness
    r"|\bstill\b|\blatest\b|\bnewest\b|\bmost recent(?:ly)?\b"
    r"|\bup[- ]?to[- ]?date\b|\bso far\b"
    # change phrasing (KU/MSR): "has the user changed their mind", "did I
    # switch X", "have they moved on".
    r"|\b(?:has|have|had|did|do|does|is|are)\b[^?]{0,60}?"
    r"\b(?:chang(?:e|ed|es|ing)|switch(?:ed|es|ing)?|upgrad(?:e|ed|es|ing)"
    r"|mov(?:e|ed|es|ing)|relocat(?:e|ed|es|ing)|stop(?:ped|s)?"
    r"|start(?:ed|s)?|quit|end(?:ed)? up)\b"
)

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class TemporalIntent:
    """The freshness intent of a query.

    ``kind``: :data:`PREFER_LATEST` (want the current version),
    :data:`DATE_PINNED` (a past window is named — do not apply the fresh
    bias), or ``None`` (no temporal-freshness signal; byte-identical
    behaviour).
    ``anchors``: the matched date-bearing phrases, for diagnostics/tests.
    Empty when ``kind`` is ``None``.
    """
    kind: str | None
    anchors: tuple[str, ...] = field(default_factory=tuple)


def detect_temporal_intent(text: str | None) -> TemporalIntent:
    """Detect whether a query wants the CURRENT version or a PINNED past one.

    Deterministic, bounded (two linear regex scans), no model call. A
    ``None``/empty/whitespace query yields ``kind=None`` (full-scan mode is
    safe). :data:`DATE_PINNED` takes precedence over :data:`PREFER_LATEST`
    when both match — "in 2024, do I still …" pins the date, and a pinned
    date must not receive the fresh bias.
    """
    if not text or not str(text).strip():
        return TemporalIntent(None)
    t = " ".join(str(text).lower().split())
    pinned = _DATE_PINNED_RE.findall(t)
    if pinned:
        return TemporalIntent(DATE_PINNED, tuple(dict.fromkeys(pinned)))
    preferred = _PREFER_LATEST_RE.findall(t)
    if preferred:
        return TemporalIntent(PREFER_LATEST, tuple(dict.fromkeys(preferred)))
    return TemporalIntent(None)


# ── query-side date anchor ──────────────────────────────────────────────────


def _as_iso_date(question_date: str | None) -> str | None:
    """The ``YYYY-MM-DD`` form of a question date, or ``None``."""
    if question_date is None:
        return None
    s = str(question_date).strip()
    if len(s) >= 10 and _ISO_DATE_RE.match(s[:10]):
        return s[:10]
    return None


def inject_query_date(query: str, question_date: str | None) -> str:
    """Anchor a query string to the question's date, pre-embed.

    Returns ``query`` unchanged when the query is empty, the date is
    missing or not ``YYYY-MM-DD`` (never invents a date), or the anchor is
    already present (idempotent). Otherwise returns
    ``query + QUERY_DATE_SUFFIX.format(date=...)``.

    The operation is a bounded string concatenation — no tokenization, no
    model call — so it is safe for the hot path, and it changes the string
    *only* by the date: two calls with two dates differ only in the date.
    """
    if not query or not str(query).strip():
        return query
    d = _as_iso_date(question_date)
    if d is None:
        return query
    suffix = QUERY_DATE_SUFFIX.format(date=d)
    if suffix in query:
        return query
    return f"{query}{suffix}"


def dense_query_for(query: str | None, *, time_aware: bool,
                    query_date: str | None = None) -> str | None:
    """The DENSE-leg query for a hybrid search — the SDK's whole anchor
    decision, factored out so it is testable without a graph.

    ``inject_query_date(query, query_date)`` when ``time_aware`` is on and
    the query's freshness intent is :data:`PREFER_LATEST`; otherwise
    ``query`` unchanged (a ``date-pinned`` query must NOT be anchored — the
    invert-recency guard). ``None``/empty queries and a missing/unparseable
    date are no-ops. Pure: no DB, no model, no clock.
    """
    if not time_aware or not query or not str(query).strip():
        return query
    if detect_temporal_intent(query).kind != PREFER_LATEST:
        return query
    return inject_query_date(query, query_date)


# ── rank-time prefer-latest ─────────────────────────────────────────────────


def _as_date(value) -> str | None:
    """Normalize a stored date value to ``YYYY-MM-DD``, or ``None``.

    The codebase writes ISO-8601 strings AND numeric epochs, sometimes into
    the same property (``supersede_point`` copies the successor's stored
    ``validFrom`` into the predecessor's ``validTo``, and a stored numeric
    ``validFrom`` is legal — see ``tests/test_validity_windows.py``). A
    naive ``str(v)[:10]`` misreads an epoch, so epochs are converted through
    ``datetime`` and everything is compared at the same DAY granularity the
    reader's ``[valid …]`` markers use. Unparseable values return ``None``
    (treated as "no window information", never as stale).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), UTC).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    if not s:
        return None
    if len(s) >= 10 and _ISO_DATE_RE.match(s[:10]):
        return s[:10]
    try:
        epoch = float(s)
    except ValueError:
        return None
    try:
        return datetime.fromtimestamp(epoch, UTC).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def is_stale_entry(entry: dict, *, question_date: str | None = None) -> bool:
    """Whether one retrieved entry is a NOT-CURRENT version of its fact.

    Stale when any of:

    * ``superseded_by`` is present (a CORRECTS successor exists), or
    * ``live.is_terminal_status(status)`` — the SINGLE shared status
      predicate, never re-declared here (the legacy ``outdated`` boolean is
      NOT consulted: the eval's annotated surface does not carry it, and it
      is redundant — ``invalidate_point`` writes the window alongside it,
      which the clause below already catches). This is the clause that
      catches ``retract_point``, which writes ``status='retracted'`` and no
      window, or
    * a validity window has closed: ``valid_to``/``expired_at`` casts to a
      date ``<= question_date``. When ``question_date`` is missing or
      unparseable, the presence of the window is the fallback signal. An
      unparseable window value is NOT stale (conservative — never demote on
      a value we cannot read).
    """
    if entry.get("superseded_by"):
        return True
    if is_terminal_status(entry.get("status")):
        return True
    qd = _as_date(question_date) if question_date else None
    for key in ("valid_to", "expired_at"):
        raw = entry.get(key)
        # ``0``/``0.0`` is a VALID (falsy) epoch — absence, not falsiness,
        # means "no window". A truthiness test would silently treat a
        # 1970-01-01 window as no window at all.
        if raw is None or raw == "":
            continue
        d = _as_date(raw)
        if d is None:
            continue
        if qd is None or d <= qd:
            return True
    return False


def prefer_latest_order(
        entries: list[dict], *,
        intent: TemporalIntent | None,
        question_date: str | None = None,
) -> tuple[list[dict], dict]:
    """Stable-reorder ``entries`` so live versions precede stale ones.

    Applies ONLY when ``intent.kind == PREFER_LATEST``. Live entries keep
    their relative order, then stale entries keep theirs — the reorder is
    deterministic and **membership-preserving** (never-starve by
    construction: no entry is added or removed, and if every entry is stale
    the input order is returned untouched).

    Returns ``(entries, stats)`` where ``stats`` is
    ``{"applied": bool, "reason": str, "live": int, "stale": int}``.
    """
    items = list(entries)
    stats: dict = {"applied": False, "reason": "", "live": 0, "stale": 0}
    if intent is None or intent.kind != PREFER_LATEST:
        stats["reason"] = "no-prefer-latest-intent"
        return items, stats
    flags = [is_stale_entry(e, question_date=question_date) for e in items]
    live = [e for e, stale in zip(items, flags, strict=True) if not stale]
    stale = [e for e, stale in zip(items, flags, strict=True) if stale]
    stats["live"] = len(live)
    stats["stale"] = len(stale)
    if not stale:
        stats["reason"] = "no-stale-entries"
        return items, stats
    if not live:
        # never-starve: every membership is stale — leave the order alone
        # rather than returning a list with no live head.
        stats["reason"] = "all-stale-never-starve"
        return items, stats
    reordered = live + stale
    if reordered == items:
        stats["reason"] = "already-latest-first"
        return reordered, stats
    stats["applied"] = True
    stats["reason"] = "latest-first"
    return reordered, stats
