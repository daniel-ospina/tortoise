"""#B7 — the activation scorecard: which sessions produce memory, and whether
anything reads it.

WHY THIS EXISTS
---------------
The beta's success criterion is ACTIVATION, not registration. Until this
module there was no way to tell an activated session from a merely-captured
one, and the surfaces that *look* like they answer it are misleading:

* ``website/apps/dashboard/src/captureStatus.js`` marks a harness ``active``
  off the capture RECEIPT alone — i.e. "the transcript was stored", not "the
  product did something".
* ``tortoise/analytics.py`` names ``first_api_call`` the "Activation event";
  it fires on any ``POST /v1/*`` returning < 400, ``api_key_created``
  included.
* ``docs/product-success-eval.md`` §5 defines the real thing —
  **Activation (public) = first capture -> first answered-from-memory, <= 24h**
  — and **no read surface observes it**.

So today's "0 activated" is indistinguishable from "the leg that matters was
never measured". This module exists to make those two states *distinguishable*,
not to dress the first up as the second.

THE DEFINITION, AND ITS HONEST LIMITS
-------------------------------------
THE ONE SENTENCE: a session has *produced memory* when the capture pipeline
wired at least one NON-EPISODIC ``Point`` to it — i.e. the model actually
extracted a claim, rather than the transcript merely being stored.

That is deliberately NARROWER than "activation". It measures the
capture -> memory leg. It does **not** measure whether the user got anything
back, and this module refuses to pretend otherwise: see
``VALUE_CONFIRMED_REFUSAL`` and the ``value_confirmed`` stage, which is a
refusal string rather than a number.

The five stages, and exactly what each one is evidence of:

===== ================== ============= ==========================================
stage  granularity        source        evidence
===== ================== ============= ==========================================
1      session            graph         ``(:Session)`` exists in the window
       ``captured``                     -> the client sent a session
2      session            graph         >= 1 ``(:Point {pointKind:'event'})`` wired
       ``stored``                       by ``CONTAINS`` -> the transcript is durable
3      session            graph         >= 1 non-episodic ``Point`` wired by
       ``memory_produced``              ``CONTAINS`` -> extraction produced a claim
4      org / time         Supabase      >= 1 retrieval-tool ``mcp_tool_call`` AFTER
       ``recall_attempted`` analytics    the first memory was ever produced
5      --                 --            NOT MEASURABLE (see the refusal constant)
       ``value_confirmed``
===== ================== ============= ==========================================

Stage 4 is an ATTEMPT, not an answer. ``mcp_tool_call.status == "ok"`` means
only that the tool did not raise (``tortoise/mcp_server.py``); a search that
returned zero points is still ``ok``. It is reported because it is the only
recorded observation of the read side, and mislabelling it as activation is
precisely the error this lane exists to fix — hence no ``activated`` key, no
activation rate, anywhere in the payload.

ZERO vs NO-SIGNAL
-----------------
Every stage carries ``state`` in ``{measured, unavailable, not_measurable}``
and a ``reason``. A ``0`` is only ever emitted with ``state == "measured"``.
An unreadable store, a truncating page cap, a window predating a working
analytics write path, or an org that has never produced memory all yield
``value: null`` — never a confident zero. This rule is the whole point: a
scorecard that cannot say "unmeasured" reproduces the bug it was built to fix.

MEASURABILITY DEPENDS ON REPAIRS OUTSIDE THIS MODULE
----------------------------------------------------
``analytics_write_path_configured()`` is a LIVE check of the server's own
environment, not a hardcoded date. It exists because the analytics writer
originally read only ``SUPABASE_SERVICE_KEY`` while the hosted deployment
provides ``SUPABASE_SERVICE_ROLE_KEY`` — so every event fell through to a
JSONL file on ephemeral disk. Two consequences the caller must not hide:

* Windows before the repair deployed are **forward-only-unknown** — historical
  events were dropped and are unrecoverable. Stage 4 says ``unavailable``,
  never ``0``.
* Stage 4 is observed only where the telemetry is emitted: a client talking to
  the HOSTED MCP dispatch point. A locally-hosted (stdio) MCP server, and the
  REST recall surface, emit no per-call event.

WHAT THIS MODULE DOES NOT TOUCH
-------------------------------
No writes. No change to the capture path. The capture/install seam (B1), the
Cursor client path (B2c), ``tortoise/monitoring.py`` (B4), the onboarding
wizard (B3) and ``projection/__init__.py`` (B5) are all out of scope. This is
a pure read over data other components already record.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

_logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
SURFACE = "activation_scorecard"

# ── Stage 4: the pinned retrieval-tool allowlist ───────────────────────────
# PINNED BY NAME — deliberately NOT derived from ``readOnlyHint`` / ``_ro()``.
# ``_ro()`` covers 52 tools, including ``tortoise_health``, ``tortoise_status``
# and every ``tortoise_list_*``; agents call those automatically at connect
# time, so deriving the set from the annotation would mark an org "activated"
# the moment it connected. Precedent for a pinned frozenset:
# ``tortoise/mcp_server.py`` (``_QUOTA_GATED``).
#
# NARROW = answer-seeking retrieval: a call made to *get remembered content
# back*. The wide set adds structural / known-id reads, which agents also make
# as dedup-before-create housekeeping. The wide set is reported alongside as a
# sensitivity bound so the definition's arbitrariness is VISIBLE rather than
# baked into a single number.
RETRIEVAL_TOOL_ALLOWLIST: frozenset[str] = frozenset({
    "tortoise_search",
    "tortoise_recall",
    "tortoise_ask",
    "tortoise_search_sessions",
})
RETRIEVAL_TOOL_ALLOWLIST_WIDE: frozenset[str] = RETRIEVAL_TOOL_ALLOWLIST | frozenset({
    "tortoise_query",
    "tortoise_query_points_by_tag",
    "tortoise_paginated_query",
    "tortoise_get_point",
})

# The analytics event the hosted MCP dispatch point emits per client tool call.
MCP_TELEMETRY_EVENT = "mcp_tool_call"

# PostgREST caps an unpaginated read at the project's max-rows (1000 by
# default). ``SupabaseControlPlane.query`` exposes no offset/Range, so a full
# page means the count is only a LOWER BOUND — a lower bound is not a count,
# and is reported as ``unavailable``, never as a measured value.
RECALL_PAGE_CAP = 1000

# Default window = the beta's own <= 24h criterion (product-success-eval.md §5).
DEFAULT_WINDOW = timedelta(hours=24)
# A window this wide is a typo or an attempt to scan the whole history; the
# scorecard is a beta instrument, not an archive query.
MAX_WINDOW = timedelta(days=90)

# ── Stage 5: the refusal ───────────────────────────────────────────────────
# A string, not a number. Nothing in the recorded data distinguishes a
# retrieval that returned remembered content from one that returned nothing.
VALUE_CONFIRMED_REFUSAL = (
    "not_measurable: no recorded signal distinguishes a retrieval that returned "
    "remembered content from one that returned nothing. mcp_tool_call.status='ok' "
    "means only that the tool did not raise, and the question_answered event is an "
    "onboarding question, not retrieval. Blockers: result-bearing telemetry "
    "(result_count/abstained) on the mcp_tool_call properties — a cross-epic change "
    "to the #888/#889-owned allowlist — and #3518/#3519, captured sessions are not "
    "FTS-findable / not semantically retrievable, so storage does not yet imply "
    "retrieval. 'answered-from-memory' stays UNMEASURED, not zero."
)

# ── The queries ────────────────────────────────────────────────────────────
# PINNED. Four alternative shapes were measured as BROKEN on BOTH the embedded
# FalkorDBLite lane and the Docker FalkorDB lane. Each failed SILENTLY, so a
# comment is not protection — ``tests/test_activation_scorecard.py`` pins all
# four:
#
#   1. A ``WHERE`` placed after an ``OPTIONAL MATCH`` is ABSORBED into the
#      optional pattern. Putting the time window there makes it a no-op: the
#      query returns every session ever, with out-of-window rows reading as 0,
#      and raises nothing. The window MUST sit directly after ``MATCH``.
#   2. ``count(p)`` across two optional matches inflates via the cross-product
#      (session with 3 turns + 2 claims reports 6). ``count(DISTINCT ...)``.
#   3. An interposed ``WITH s, p WHERE <optional predicate>`` DELETES the
#      sessions that have only turn points — i.e. exactly the population this
#      scorecard exists to count. Never interpose a ``WITH`` between the
#      optional matches and the ``WHERE`` on them.
#   4. ``sum(CASE WHEN p.pointKind IS NULL OR ... THEN 1 ELSE 0 END)`` counts a
#      session with NO points at all as ``memory_produced``, because a null
#      ``p`` makes ``p.pointKind IS NULL`` true. ``count(DISTINCT p)`` cannot
#      (a null ``p`` does not count). ``sum()`` also returns floats.
#
# The stage-3 predicate is copied VERBATIM from the session-detail endpoint,
# which is the corrected one. The ``list_sessions`` variant
# (``pointKind IN ['decision','statement']``) is a known defect: extraction
# writes NULL / other kinds, so it reports 0 for sessions that did produce
# memory. Never use it here.
FUNNEL_QUERY = (
    "MATCH (s:Session) "
    "WHERE s.created_at >= $since AND s.created_at < $until "
    "OPTIONAL MATCH (s)-[:CONTAINS]->(t:Point {pointKind:'event'}) "
    "OPTIONAL MATCH (s)-[:CONTAINS]->(p:Point) "
    "WHERE p.pointKind IS NULL OR p.pointKind <> 'event' "
    "RETURN s.id, s.created_at, "
    "count(DISTINCT t) AS turn_points, count(DISTINCT p) AS extracted "
    "ORDER BY s.id"
)

# Lifetime memory — used to condition stage 4. NOTE the shape difference is
# deliberate: a ``WHERE`` after a required ``MATCH`` IS a filter (the
# absorption hazard in (1) applies only to a predicate on an ``OPTIONAL
# MATCH``). Because this query spans all time it is intentionally unwindowed.
LIFETIME_MEMORY_QUERY = (
    "MATCH (s:Session)-[:CONTAINS]->(p:Point) "
    "WHERE p.pointKind IS NULL OR p.pointKind <> 'event' "
    "RETURN min(s.created_at) AS first_memory_at, "
    "count(DISTINCT s) AS sessions_with_memory"
)

# ── Stage state vocabulary (one home for the whole surface) ────────────────
STATE_MEASURED = "measured"
STATE_UNAVAILABLE = "unavailable"
STATE_NOT_MEASURABLE = "not_measurable"

# Integrity flags that mean "the window itself could not be trusted" — the
# returned rows cannot be attributed to the requested window, so no count may
# be reported. Distinct from `memory_without_transcript`, which is a data
# anomaly within an in-window population: the counts stand, the flag carries
# the doubt.
_WINDOW_FAILURES = ("window_predicate_not_applied", "unparseable_created_at")


class WindowError(ValueError):
    """Malformed / out-of-range window — a client error, never a silent empty."""


# ── Window handling ────────────────────────────────────────────────────────
def _parse_iso(value: str, field: str) -> datetime:
    """Parse an ISO-8601 timestamp into an aware UTC datetime.

    ``Z`` is folded to ``+00:00``. A naive timestamp is REJECTED rather than
    assumed UTC — assuming would silently shift the window.
    """
    text = str(value).strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise WindowError(f"{field} is not a valid ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise WindowError(f"{field} must carry a UTC offset (got naive {value!r})")
    return parsed.astimezone(UTC)


def _coerce_created_at(value: Any) -> datetime | None:
    """Coerce a row's ``created_at`` to an aware ``datetime``, or ``None``.

    The window re-check is the guard that stops a silently-absorbed Cypher
    predicate from being reported as a plausible number, so it must not have a
    hole. The graph writer stamps
    ``datetime.now(UTC).isoformat()`` (``hosted_api.py``), so a string is the
    production shape — but relying on that alone would leave a non-string
    (a driver returning a native datetime) or a malformed value counted as
    in-window WITHOUT ever being checked. Anything we cannot place on the
    timeline returns ``None`` and is treated as a window failure, not skipped.
    """
    if isinstance(value, str):
        try:
            return _parse_iso(value, "created_at")
        except WindowError:
            return None
    if isinstance(value, datetime):
        # A naive value cannot be placed on the timeline without assuming a
        # zone, and assuming is the bug this avoids.
        return value.astimezone(UTC) if value.tzinfo is not None else None
    if isinstance(value, bool):  # bool is an int subclass — never a timestamp
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def normalize_window(since: str | None, until: str | None,
                     now: datetime | None = None) -> tuple[str, str]:
    """Validate and canonicalize a window. Returns (since, until) as canonical
    ``+00:00`` ISO strings, in the SAME shape the graph writer stamps
    (``datetime.now(UTC).isoformat()``), so the Cypher string comparison is
    never a ``Z``-vs-``+00:00`` mismatch. ``[since, until)`` — half-open.

    Raises :class:`WindowError` (mapped to 422 by the endpoint) for a malformed
    timestamp, a naive one, a non-positive span, or a span beyond
    :data:`MAX_WINDOW`.
    """
    ref = now or datetime.now(UTC)
    until_dt = _parse_iso(until, "until") if until else ref
    since_dt = _parse_iso(since, "since") if since else until_dt - DEFAULT_WINDOW
    if since_dt >= until_dt:
        raise WindowError("since must be earlier than until")
    if until_dt - since_dt > MAX_WINDOW:
        raise WindowError(
            f"window exceeds {MAX_WINDOW.days} days "
            f"(got {(until_dt - since_dt).days} days)")
    return since_dt.isoformat(), until_dt.isoformat()


# ── Stage 1-3: the graph funnel ────────────────────────────────────────────
def _stage(value: Any, unit: str | None, reason: str | None = None,
           state: str | None = None) -> dict:
    """Build one stage cell. ``None`` value => a non-measured state.

    ``state`` overrides the default reason-derived state. The default — "a
    reason means unavailable" — is right for the RECOVERABLE failures (store
    unreachable, write path unconfigured, page cap, bad data): retry or repair
    fixes them, so the metric exists and merely could not be read just now.
    An org that has never produced memory has nothing to recall FROM, and no
    retry changes that — so that case is ``not_measurable`` and must say so
    explicitly rather than inheriting ``unavailable``.
    """
    if value is None:
        return {"state": state or (STATE_UNAVAILABLE if reason
                                   else STATE_NOT_MEASURABLE),
                "value": None, "unit": unit, "reason": reason}
    return {"state": STATE_MEASURED, "value": int(value), "unit": unit,
            "reason": None}


def stage_counts(rows: Sequence[Sequence[Any]], since: str,
                 until: str) -> tuple[dict, dict]:
    """Fold the funnel rows into the three graph stages.

    ``rows`` are ``(session_id, created_at, turn_points, extracted)``.

    Side effect worth naming: EVERY returned ``created_at`` is re-checked
    against the window IN PYTHON. If Cypher's window predicate were ever
    absorbed (measured failure mode 1) the query still returns rows — an
    out-of-window row here turns stages 1-3 into ``unavailable`` and sets an
    ``integrity`` flag, rather than reporting a plausible wrong number. A test
    protects the fixture; this protects production.

    A row whose timestamp cannot be placed on the timeline at all
    (``unparseable_created_at``) is ALSO a window failure, not a row to count:
    counting it would be asserting it is in-window with no evidence. Both
    flags fail closed — refuse the number, never guess it.
    """
    integrity: list[str] = []
    kept: list[tuple[int, int]] = []
    for row in rows:
        sid, created_at, turn_points, extracted = (
            row[0], row[1], int(row[2] or 0), int(row[3] or 0))
        parsed = _coerce_created_at(created_at)
        if parsed is None:
            integrity.append("unparseable_created_at")
            _logger.warning(
                "activation scorecard: session %s created_at=%r (type %s) "
                "cannot be placed on the timeline — the window cannot be "
                "verified for this row", sid, created_at,
                type(created_at).__name__)
            continue
        if not (since <= parsed.isoformat() < until):
            integrity.append("window_predicate_not_applied")
            _logger.warning(
                "activation scorecard: session %s created_at=%s fell "
                "outside the requested window [%s, %s) — the Cypher window "
                "predicate did not apply", sid, created_at, since, until)
            continue
        if extracted >= 1 and turn_points == 0:
            # Turn points are written BEFORE extraction, so this inversion is
            # impossible in production. If it ever appears, the funnel's
            # premise is broken and must not be silent. It is a data anomaly,
            # NOT a window failure — the rows are still in-window, so the
            # counts stand and the flag is what carries the doubt.
            integrity.append("memory_without_transcript")
        kept.append((turn_points, extracted))

    window_failure = next((f for f in integrity
                           if f in _WINDOW_FAILURES), None)
    if window_failure:
        stages = {name: _stage(None, "sessions", window_failure)
                  for name in ("captured", "stored", "memory_produced")}
        return stages, {"integrity": integrity, "captured_sessions": None,
                        "stored_sessions": None, "memory_produced_sessions": None,
                        "turn_points_total": None, "extracted_points_total": None}

    captured = len(kept)
    stored = sum(1 for t, _ in kept if t >= 1)
    memory_produced = sum(1 for _, x in kept if x >= 1)
    stages = {
        "captured": _stage(captured, "sessions"),
        "stored": _stage(stored, "sessions"),
        "memory_produced": _stage(memory_produced, "sessions"),
    }
    detail = {
        "integrity": integrity,
        "captured_sessions": captured,
        "stored_sessions": stored,
        "memory_produced_sessions": memory_produced,
        "turn_points_total": sum(t for t, _ in kept),
        "extracted_points_total": sum(x for _, x in kept),
    }
    return stages, detail


def graph_unavailable_stages(reason: str) -> dict:
    """Stages 1-3 when the org graph cannot be read.

    Fail-soft, mirroring the session-detail endpoint's ``{"session": None}``:
    the caller gets ``unavailable`` and a reason, NEVER a fabricated zero —
    an unreadable graph and an empty graph are different facts.
    """
    return {name: _stage(None, "sessions", reason)
            for name in ("captured", "stored", "memory_produced")}


# ── Stage 4: recall, from the analytics store ──────────────────────────────
def analytics_write_path_configured() -> bool:
    """Whether the in-process analytics writer can reach the analytics store.

    A LIVE environment check, not a hardcoded date. ``_track_analytics_event``
    needs BOTH a URL and a service key; it originally accepted only the legacy
    ``SUPABASE_SERVICE_KEY`` name, which the hosted deployment does not set, so
    every event fell through to a JSONL file on ephemeral disk. Checked here so
    stage 4 can say "the writer is dark, this is not a zero" instead of
    reporting an unmeasurable window as an observed absence.
    """
    import os

    # Function-local import keeps this module import-pure (stdlib only at
    # module level). The env-name tuple has exactly ONE home — re-declaring it
    # here is how the analytics write path ended up reading a name the hosted
    # deployment never sets (#3677).
    from tortoise.supabase_control import _SERVICE_KEY_ENV

    url = os.environ.get("SUPABASE_URL")
    key = next((os.environ.get(n) for n in _SERVICE_KEY_ENV
                if os.environ.get(n)), None)
    return bool(url and key)


def recall_stages(rows: Iterable[dict] | None, first_memory_at: str | None,
                  reason: str | None = None,
                  truncated: bool = False,
                  memory_sessions: int | None = None) -> tuple[dict, dict]:
    """Derive the stage-4 cell from analytics rows.

    ``rows`` may be ``None`` to signal "the store was not read" (unreachable,
    unconfigured, or the graph leg failed) — in which case ``reason`` must say
    why. Returns ``(stage_cell, detail)``.
    """
    detail: dict[str, Any] = {
        "recall_attempted_any": None,
        "recall_attempted_after_memory": None,
        "recall_attempted_after_memory_wide": None,
        "recall_rows_fetched": None,
        "recall_page_cap": RECALL_PAGE_CAP,
        "recall_truncated": False,
        "unparseable_analytic_rows": 0,
        "retrieval_tool_allowlist": sorted(RETRIEVAL_TOOL_ALLOWLIST),
        "retrieval_tool_allowlist_wide": sorted(RETRIEVAL_TOOL_ALLOWLIST_WIDE),
    }
    if rows is None:
        return _stage(None, "calls", reason or "analytics_store_unreachable"), detail

    rows = list(rows)
    detail["recall_rows_fetched"] = len(rows)
    detail["recall_truncated"] = truncated

    # ORDER MATTERS: the no-lifetime-memory fact comes from the GRAPH
    # (`LIFETIME_MEMORY_QUERY`), so it is independent of how many analytics
    # rows were fetched. Testing truncation first would report an org that has
    # never produced memory as `unavailable` (a recoverable reporting failure)
    # whenever it happened to have a full page of calls — the state inversion
    # this module exists to prevent, and one that would land the org in a
    # cohort's `orgs_unavailable` list.
    if first_memory_at is None:
        # Distinguish the two reasons `first_memory_at` can be NULL, because
        # they carry opposite meanings. `LIFETIME_MEMORY_QUERY` returns a
        # memory-Session count alongside the min(created_at): a positive count
        # with a NULL min means memory EXISTS but carries no timestamp — a data
        # gap, not an absence. Reading that as "no memory was ever produced"
        # would report nothing-to-measure for an org that has memory.
        detail["recall_attempted_any"] = _count_allowlisted(rows, None)[0]
        if memory_sessions:
            return _stage(None, "calls", "first_memory_at_missing"), detail
        # No memory has ever been produced, so no retrieval can have been
        # "from memory". Report the raw count for visibility, but the stage is
        # unmeasurable rather than zero.
        return _stage(None, "calls", "no_memory_produced_in_lifetime",
                      state=STATE_NOT_MEASURABLE), detail

    if truncated:
        # A full page with no offset support means the count is a lower bound.
        # A lower bound is not a count: refuse rather than under-report.
        return _stage(None, "calls", "analytics_page_cap_truncated"), detail

    # Same coercion the row timestamps get, so both legs accept exactly the
    # same value shapes (an epoch int is placeable on the timeline; refusing it
    # here would brick stage 4 with a misleading reason and no retry would fix
    # it).
    memory_at = _coerce_created_at(first_memory_at)
    if memory_at is None:
        return _stage(None, "calls", "first_memory_at_unparseable"), detail

    narrow, unparseable, unclassifiable = _count_allowlisted(
        rows, memory_at, RETRIEVAL_TOOL_ALLOWLIST)
    wide, wide_unparseable, wide_unclassifiable = _count_allowlisted(
        rows, memory_at, RETRIEVAL_TOOL_ALLOWLIST_WIDE)
    detail["unparseable_analytic_rows"] = unparseable
    detail["unclassifiable_analytic_rows"] = unclassifiable
    # The wide list is the sensitivity bound (a superset). Its excluded rows are
    # tracked too, so the bound cannot silently under-count the same way.
    detail["unparseable_analytic_rows_wide"] = wide_unparseable
    detail["unclassifiable_analytic_rows_wide"] = wide_unclassifiable
    detail["recall_attempted_after_memory"] = narrow
    detail["recall_attempted_after_memory_wide"] = wide
    detail["recall_attempted_any"] = _count_allowlisted(rows, None)[0]
    if unparseable or wide_unparseable:
        # An allowlisted call whose timestamp will not parse cannot be placed
        # relative to first_memory_at, so it is excluded from the count — which
        # makes the count a LOWER BOUND. Reporting a lower bound as `measured`
        # is the same class of bug as counting an unverifiable row in the
        # window guard, so it is refused here for the same reason.
        return _stage(None, "calls", "unparseable_analytic_rows"), detail
    if unclassifiable or wide_unclassifiable:
        # This store holds `mcp_tool_call` rows only (the read filters on
        # event_name), so a call whose tool_name cannot be read is a call we
        # cannot rule OUT of the retrieval set. Excluding it silently would
        # again make the count a lower bound — the number would look exact.
        return _stage(None, "calls", "unclassifiable_analytic_rows"), detail
    return _stage(narrow, "calls"), detail


def _count_allowlisted(rows: Iterable[dict], after: datetime | None,
                       allow: frozenset[str] | None = None) -> tuple[int, int, int]:
    """Count rows whose ``properties.tool_name`` is allowlisted and whose
    ``created_at`` is at or after ``after`` (None => no time condition).

    Returns ``(total, unparseable, unclassifiable)``. The second and third are
    EXCLUSION counts, and both make ``total`` a lower bound: a row we cannot
    place on the timeline, and a row we cannot read a ``tool_name`` out of.
    They are returned rather than swallowed so the caller can refuse to report
    a lower bound as if it were a count.

    Comparison is on PARSED datetimes, never raw strings — the writer and
    PostgREST agree on the format today, and parsing removes the entire class
    of bug.
    """
    allow = allow if allow is not None else RETRIEVAL_TOOL_ALLOWLIST
    total = 0
    unparseable = 0
    unclassifiable = 0
    for row in rows:
        props = row.get("properties")
        tool: str | None = None
        if isinstance(props, dict):
            tool = str(props.get("tool_name") or "") or None
        elif isinstance(props, str):
            try:
                import json
                tool = str((json.loads(props) or {}).get("tool_name") or "") or None
            except Exception:
                tool = None
        if tool is None:
            unclassifiable += 1
            continue
        if tool not in allow:
            continue
        stamp = row.get("created_at")
        if after is None:
            total += 1
            continue
        try:
            stamp_dt = _parse_iso(stamp, "created_at")
        except (WindowError, TypeError):
            unparseable += 1
            continue
        if stamp_dt >= after:
            total += 1
    return total, unparseable, unclassifiable


def value_confirmed_stage() -> dict:
    """Stage 5 — a refusal, always. Never a number, never nullable-to-mean-zero."""
    return {"state": STATE_NOT_MEASURABLE, "value": None, "unit": None,
            "reason": VALUE_CONFIRMED_REFUSAL}


# ── Assembly ───────────────────────────────────────────────────────────────
LIMITATIONS: tuple[str, ...] = (
    "value_confirmed ('answered-from-memory') is NOT MEASURABLE: no recorded "
    "signal carries a result count, so a retrieval that returned nothing is "
    "indistinguishable from one that returned remembered content.",
    "recall_attempted is an ATTEMPT, not an answer — it must never be read as "
    "activation, and no activation rate is derivable from it.",
    "Per-session recall attribution is IMPOSSIBLE: the telemetry carries no "
    "session_id, so stage 4 is org/time-scoped while stages 1-3 are "
    "session-scoped. This is an activation FUNNEL of mixed granularity, not a "
    "per-session claim.",
    "Stages 1-3 cover the org DEFAULT graph only (the endpoint is an org-level "
    "surface). Stage 4 is org-wide. For a multi-graph org the two denominators "
    "differ; the graph_scope field states this in the payload.",
    "recall_attempted is observed only where telemetry is emitted: the HOSTED "
    "MCP dispatch point. A locally-hosted (stdio) MCP server and the REST "
    "recall surface emit no per-call event, so their recall is invisible here.",
    "Analytics history before the write-path repair is unrecoverable "
    "(forward-only) — the events were written to an ephemeral VM and lost. "
    "This surface cannot detect a window that predates the repair, so such a "
    "window reports `measured 0`, not `unavailable`: reconcile against the "
    "deploy time before citing a zero that straddles it.",
    "The analytics write path being CONFIGURED is not proof it WORKS. A "
    "present-but-rejected credential (rotated/revoked key, a 4xx from the "
    "store) drops every event with no fallback and no signal, so a "
    "configured-but-dark writer reads as `measured 0`. Detectability is "
    "tracked in #3677.",
    "Extraction outcome (capture_ok / capture_extractor) is recorded on the "
    "Session but exposed by no read surface (owned by #3520).",
    "The analytics leg's interval is EXCLUSIVE at both ends (created_at gt "
    "since / lt until — the control-plane query exposes no gte), while the "
    "graph legs are [since, until). An event exactly on a boundary can be "
    "counted by one leg and not the other.",
    "Self-hosted orgs are not covered: the analytics store is hosted-lane only.",
    "The analytics read assumes the store's page limit is RECALL_PAGE_CAP. A "
    "LOWER server-side cap (PostgREST db-max-rows, a proxy limit) would return "
    "a short page that looks complete, so recall_attempted would read measured "
    "while under-counting. The cap cannot be probed from here.",
)


def assemble(*, org_id: str, since: str, until: str, graph_stages: dict,
             graph_detail: dict, recall_cell: dict, recall_detail: dict,
             lifetime: dict | None, analytics_state: str,
             integrity: list[str] | None = None) -> dict:
    """Compose the response. Deliberately contains NO ``activated`` key and no
    activation rate at any depth — ``tests/test_activation_scorecard.py``
    asserts that recursively."""
    stages = dict(graph_stages)
    stages["recall_attempted"] = recall_cell
    stages["value_confirmed"] = value_confirmed_stage()

    detail = {
        **{k: v for k, v in graph_detail.items() if k != "integrity"},
        "first_memory_at": (lifetime or {}).get("first_memory_at"),
        "sessions_with_memory_all_time": (lifetime or {}).get("sessions_with_memory"),
        **recall_detail,
        "analytics_write_path": analytics_state,
        "graph_scope": "org_default_graph",
        "stage3_predicate": "p.pointKind IS NULL OR p.pointKind <> 'event'",
        "stages_1_3_granularity": "session",
        "stage_4_granularity": "org_time",
    }
    return {
        "surface": SURFACE,
        "schema_version": SCHEMA_VERSION,
        "org_id": org_id,
        "window": {"since": since, "until": until},
        "stages": stages,
        "detail": detail,
        "integrity": list(integrity or (graph_detail.get("integrity") or [])),
        "limitations": list(LIMITATIONS),
        "notes": [],
    }
