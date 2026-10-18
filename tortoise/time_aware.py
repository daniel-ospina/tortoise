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
   surface can prefer the latest version, and no rank-time surface did.
   → :func:`prefer_latest_order` (the eval lane, #2520) and
   :func:`prefer_latest_ids` (the SDK read path, #8487).

Publication surface: pure functions + one frozen dataclass. No DB access,
no I/O, no LLM calls, no random — the same inputs always give the same
output, with TWO declared exceptions to that purity claim, both of which take
their nondeterministic input INJECTABLE so a test can pin it:

* :func:`resolve_question_date` reads the WALL CLOCK
  (``datetime.now(UTC)``) when the caller injects neither ``query_date`` nor
  ``today=`` — a ``prefer-latest`` read has to answer "current as of when?",
  and the honest default is today. Consequence a caller must know: result
  order depends on the wall-clock DATE (the same graph + the same query
  reorders across a ``validTo`` boundary). Pass ``query_date`` to pin it.
* :func:`prefer_latest_enabled` reads ``os.environ`` (the #4097 kill-switch
  vocabulary); ``raw=`` injects the value.

The two predicates the read path branches on (:func:`prefer_latest_ids`,
:func:`is_not_yet_effective`) remain deterministic given their
``question_date``, which is why the read path resolves the date once and
threads it. Everything else here stays clock-, env- and I/O-free. The eval
lane (:mod:`tools.longmem_eval.retrieve`) and the SDK
(:meth:`tortoise.sdk.TortoiseSDK.tortoise_fts_query`) are thin callers —
the eval lane has called :func:`prefer_latest_order` since #2520, and the
SDK's rank-time half was wired by **#8487** (:func:`prefer_latest_ids`,
step 5e of the read path: valid time as a SELECTION signal, membership-
preserving, on the pre-truncation fused candidate set).

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
    "VALIDITY_RERANK_ENV",
    "TemporalIntent",
    "dense_query_for",
    "detect_temporal_intent",
    "inject_query_date",
    "is_not_yet_effective",
    "is_stale_entry",
    "prefer_latest_enabled",
    "prefer_latest_ids",
    "prefer_latest_order",
    "resolve_question_date",
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


# ── #8487: the read-path wiring (valid time as a SELECTION signal) ──────────
#
# The measured defect (#8487, re-measured 2026-10-18 on the shipped read
# path): a three-claim knowledge-update graph — an ENDED claim
# (``validTo`` in the past, ``status='live'``), its CURRENT successor and a
# NOT-YET-EFFECTIVE claim — came back from ``tortoise_fts_query`` in pure
# relevance order with every window ignored: the ended claim served as
# current, and on a present-tense query the 2099-dated claim ranked FIRST.
#
# The rank-time machinery already existed (:func:`prefer_latest_order`,
# #2520) and had exactly ONE caller: the eval lane
# (``tools/longmem_eval/retrieve.py``, on its final pool — the placement the
# #2520 plan pinned as D3). The SDK used the dense-leg half of C6
# (``dense_query_for``) and never the rank-time half, so the module
# docstring's "the eval lane and the SDK are thin callers" described one
# caller. D3 justified that with "the default product read path excludes
# terminal points at base retrieval, so there is nothing to demote" — which
# is FALSE for valid TIME: terminal-STATUS exclusion is orthogonal to a
# window, and ``hosted_api`` writes ``validFrom`` on LIVE points. #8487
# (owner-filed) therefore reopened D3's product half; the amendment is
# recorded in that plan doc.
#
# These three helpers are the missing adapter, kept here (not in ``sdk.py``)
# so the decision stays a pure function over already-fetched state: no DB, no
# I/O, and no clock — the same property :func:`prefer_latest_order` carries,
# with the TWO injectable exceptions the module docstring declares (the wall
# clock in :func:`resolve_question_date`, ``os.environ`` in
# :func:`prefer_latest_enabled`).

#: The kill switch for the read-path validity rerank (#8487). Default ON —
#: an OVERRIDE of the #1745 fail-safe convention ("a new retrieval lever ships
#: OFF in the product until a measured delta adopts it", which
#: ``entity_key_expansion`` and ``time_aware`` follow), recorded in the #8487
#: plan-doc amendment, at the lever itself in ``sdk.py``, and on #8487: #8487 is
#: OWNER-filed and requires the read path to select on valid time, so a
#: default-OFF lever would ship it unimplemented. The reorder is
#: MEMBERSHIP-PRESERVING and byte-identical when no candidate carries a
#: closed/terminal window, and the delta that convention asks for is measured
#: (the regenerated ask transcript + the three-claim read-path census).
#: Explicit falsy (0/false/no/off) turns it off for an operator who wants the
#: pre-#8487 relevance order back.
VALIDITY_RERANK_ENV = "TORTOISE_VALID_TIME_RERANK"


def prefer_latest_enabled(raw: object = None) -> bool:
    """Is the read-path validity rerank ON (#8487)?

    ``raw`` is the environment VALUE (``None`` = read ``os.environ``), so a
    test never mutates the process environment. Resolution follows the ONE
    declared env contract (#4097): unset/blank/garbage → the default (ON),
    explicit truthy → ON, explicit falsy → OFF. A typo therefore never flips
    the knob. The value-level resolver is NOT re-implemented here — it lives
    in ``tortoise.env_truthy.env_flag_from_value`` and ``env_flag`` delegates
    to it, so an injected value and the environment cannot drift apart (#8487
    review round 8).
    """
    from .env_truthy import env_flag, env_flag_from_value

    if raw is None:
        return env_flag(VALIDITY_RERANK_ENV, True)
    return env_flag_from_value(raw, True)


def resolve_question_date(query_date: str | None = None, *,
                          today: str | None = None) -> str:
    """The date a ``prefer-latest`` selection is evaluated at.

    ``question_date`` is a parameter of :func:`is_stale_entry`, not its own
    clock: with it absent a window's mere PRESENCE reads as closed (the
    documented fallback), which would demote a claim whose ``validTo`` is in
    the FUTURE — a scheduled end, i.e. still true. A live read therefore has
    to name the instant it is answering for:

    * the caller's ``query_date`` when it names one (the eval lane passes the
      question's date — the #2520 contract), else
    * today's UTC date — "is this true NOW" is precisely the question a
      ``prefer-latest`` query asks.

    ``today`` is injectable so the function's own clock read can be pinned by
    a test. An unparseable ``query_date`` falls through to ``today`` (never
    raises, never invents a bound). The clock read is this module's ONE
    nondeterministic input on the rules side (declared in the module
    docstring); the read path resolves it ONCE per read and threads the
    result, so the selection it feeds is deterministic.
    """
    named = _as_date(query_date) if query_date else None
    if named:
        return named
    if today is not None:
        resolved = _as_date(today)
        if resolved:
            return resolved
    return datetime.now(UTC).date().isoformat()


def is_not_yet_effective(entry: dict, *, question_date: str | None = None) -> bool:
    """Whether one retrieved entry's window has not STARTED at ``question_date``.

    DELIBERATELY a different question from :func:`is_stale_entry`, and kept a
    separate predicate rather than folded into it. ``is_stale_entry`` answers
    "is this a NOT-CURRENT VERSION of its fact" — and its answer for a future
    window is NO, a decision pinned by
    ``tests/test_time_aware_2520.py::test_future_window_is_not_stale`` and
    consumed by the eval lane's stale census, so it must not move.

    This predicate answers the read-path question the ontology's valid time
    (``ONTOLOGY.md`` §4.7) exists to answer: *has this claim started being true
    yet?* A claim whose ``valid_from`` lies AFTER the instant being answered is
    not a stale version of anything — it is a claim that was never true at that
    instant, and serving it as the answer to "what is true now" is the same
    class of error as serving an ended one.

    Only the START is read. An absent/unparseable ``valid_from`` (an open
    start — a legacy undated claim) is never demoted: absence of a window is
    not evidence that the claim has not begun. An unparseable
    ``question_date`` disables the check entirely (nothing to compare to).
    """
    qd = _as_date(question_date) if question_date else None
    if qd is None:
        return False
    raw = entry.get("valid_from")
    if raw is None or raw == "":
        return False
    start = _as_date(raw)
    if start is None:
        return False
    return start > qd


def prefer_latest_ids(
        ids: list[str],
        states: dict[str, dict],
        *,
        query: str | None,
        question_date: str | None = None,
        enabled: bool = True,
) -> tuple[list[str], dict]:
    """The ID-level adapter of :func:`prefer_latest_order` (#8487).

    The read path selects ids before it builds hit dicts, so the reorder has
    to happen there or the stale claim has already consumed a page slot.
    ``states`` is the batch epistemic-state map
    (``search_engine.fetch_point_epistemic_state``) — its keys are exactly
    the ones :func:`is_stale_entry` reads (``status`` / ``superseded_by`` /
    ``valid_to`` / ``expired_at``), so an entry is ``{"id": pid, **state}``
    and no second vocabulary is minted here.

    Returns ``(ids, stats)``. ``stats`` is :func:`prefer_latest_order`'s dict
    plus ``intent`` (the detected kind), ``ended`` / ``not_yet_effective``
    (the two demotion reasons, counted) and ``demoted`` (the ids moved below
    the current rows — the audit trail #8487 asks for: a caller can say WHICH
    version was displaced and why). Semantics, unchanged from the rules
    function: membership is preserved, order within each group is stable, an
    id with no state is NOT stale (an honest absence is never a demotion),
    and an all-stale pool is returned untouched (never-starve).

    Two demotion classes, both validated above, both demoted in ONE stable
    group (the relative relevance order of two non-current claims is not
    re-ranked — neither is "more true now"):

    * ``ended`` — :func:`is_stale_entry` (superseded / terminal / closed
      window) — the version this fact USED to have;
    * ``not_yet_effective`` — :func:`is_not_yet_effective` — a window that has
      not started at the answered instant.
    """
    items = list(ids)
    stats: dict = {"applied": False, "reason": "", "live": 0, "stale": 0,
                   "intent": None, "demoted": [], "ended": 0,
                   "not_yet_effective": 0}
    if not enabled:
        stats["reason"] = "disabled"
        return items, stats
    intent = detect_temporal_intent(query)
    stats["intent"] = intent.kind
    if intent.kind != PREFER_LATEST:
        stats["reason"] = "no-prefer-latest-intent"
        return items, stats

    demoted_flags: list[bool] = []
    for pid in items:
        entry = dict(states.get(pid) or {})
        if is_stale_entry(entry, question_date=question_date):
            stats["ended"] += 1
            demoted_flags.append(True)
        elif is_not_yet_effective(entry, question_date=question_date):
            stats["not_yet_effective"] += 1
            demoted_flags.append(True)
        else:
            demoted_flags.append(False)
    current = [pid for pid, dead in zip(items, demoted_flags, strict=True)
               if not dead]
    demoted = [pid for pid, dead in zip(items, demoted_flags, strict=True)
               if dead]
    stats["live"], stats["stale"] = len(current), len(demoted)
    if not demoted:
        stats["reason"] = "no-stale-entries"
        return items, stats
    if not current:
        stats["reason"] = "all-stale-never-starve"
        return items, stats
    reordered = current + demoted
    if reordered == items:
        stats["reason"] = "already-latest-first"
        return reordered, stats
    stats["applied"] = True
    stats["reason"] = "latest-first"
    stats["demoted"] = demoted
    return reordered, stats
