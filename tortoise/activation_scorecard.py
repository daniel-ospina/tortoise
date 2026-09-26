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
An unreadable store, a truncating page cap, or an org that has never
produced memory all yield ``value: null`` — never a confident zero. This rule
is the whole point: a scorecard that cannot say "unmeasured" reproduces the bug
it was built to fix.

⚠️ One case the rule CANNOT cover: a window that merely PREDATES the analytics
write-path repair. Nothing records when the repair landed, so such a window is
indistinguishable from a window in which no tool call happened — it reports
``measured 0``, not ``null``. Reconcile a zero against the deploy time before
citing it. (A rejected credential is NOT the same shape: it fails this read
too and surfaces as `unavailable`, not as a zero — see ``LIMITATIONS``.)

MEASURABILITY DEPENDS ON REPAIRS OUTSIDE THIS MODULE
----------------------------------------------------
``analytics_write_path_configured()`` is a LIVE check of the server's own
environment, not a hardcoded date. It exists because the analytics writer
originally read only ``SUPABASE_SERVICE_KEY`` while the hosted deployment
provides ``SUPABASE_SERVICE_ROLE_KEY`` — so every event fell through to a
JSONL file on ephemeral disk. Two consequences the caller must not hide:

* Windows before the repair deployed are **forward-only-unknown** — historical
  events were dropped and are unrecoverable. This surface **cannot detect
  that**: nothing records when the repair landed, so such a window reports
  ``measured 0``. Reconcile a zero against the deploy time before citing it (see
  ``LIMITATIONS``). Equally, a *configured* writer is not a *working* one: a
  failure that rejects the WRITE while letting this READ succeed reports
  ``measured 0`` (a revoked key fails BOTH and surfaces as ``unavailable``;
  the full list of false-zero routes is in ``LIMITATIONS``).
* Stage 4 is org-scoped, so it sees only events the HOSTED MCP dispatch point
  emits for this org. A locally-hosted (stdio) MCP server DOES emit the event
  (the telemetry wrapper is installed at import), but with no request context
  its `org_id` is empty, so this org-filtered read cannot match it. The REST
  recall surface emits no per-call event at all.

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
#
# `tortoise_ask` is deliberately ABSENT: it became eval-only (owner-directed,
# #3849 — removed from the MCP surface / SDK / REST by #3929), and #3849 assigns
# this lane the follow-up that the value scorecard must NOT count ask traffic.
# Counting a non-product eval lane as activation would inflate the denominator.
RETRIEVAL_TOOL_ALLOWLIST: frozenset[str] = frozenset({
    "tortoise_search",
    "tortoise_recall",
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
# The stage-3 predicate has ONE home: it is interpolated into ``FUNNEL_QUERY``
# below AND reported to consumers as ``detail.stage3_predicate``. A hand-copied
# literal in the payload would let the response describe a query that is no
# longer the one executed — a confident claim with no coupling.
STAGE3_PREDICATE = "p.pointKind IS NULL OR p.pointKind <> 'event'"

FUNNEL_QUERY = (
    "MATCH (s:Session) "
    "WHERE s.created_at >= $since AND s.created_at < $until "
    "OPTIONAL MATCH (s)-[:CONTAINS]->(t:Point {pointKind:'event'}) "
    "OPTIONAL MATCH (s)-[:CONTAINS]->(p:Point) "
    f"WHERE {STAGE3_PREDICATE} "
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
    f"WHERE {STAGE3_PREDICATE} "
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
    # module level). The key is resolved through the SAME seam the analytics
    # WRITER uses (``supabase_control._service_key()``) — re-deriving the
    # resolution here is how the write path ended up reading a name the hosted
    # deployment never sets (#3677). A second resolver could report "unset"
    # while the writer is working, darkening every window.
    from tortoise.supabase_control import _service_key

    return bool(os.environ.get("SUPABASE_URL")) and bool(_service_key())


def recall_stages(rows: Iterable[dict] | None, first_memory_at: str | None,
                  reason: str | None = None,
                  truncated: bool = False,
                  memory_sessions: int | None = None,
                  window: tuple[str, str] | None = None) -> tuple[dict, dict]:
    """Derive the stage-4 cell from analytics rows.

    ``rows`` may be ``None`` to signal "the store was not read" (unreachable,
    unconfigured, or the graph leg failed) — in which case ``reason`` must say
    why. Returns ``(stage_cell, detail)``.

    ``window`` is the ``(since, until)`` interval the rows were fetched for. It
    is REQUIRED before any count is emitted: reading rows without re-checking
    that they fall inside the window is the silent-dropped-bound class this PR
    fixed in ``SupabaseControlPlane.query``. A ``None`` window on the counting
    path fails closed (``analytics_window_not_supplied``).

    ``detail.recall_attempted_any`` is the allowlisted count over the WINDOW,
    and is emitted only when it is EXACT — the window verified, the page not
    truncated, and every fetched row placeable and classifiable. Otherwise it
    stays ``None`` and the exclusion counters (``unparseable_analytic_rows``,
    ``unclassifiable_analytic_rows``, ``analytics_window_out_of_range``) say
    why. Those counters follow the same EXACT OR ABSENT rule: ``None`` means
    the fold that computes them never ran, never a literal ``0``.
    """
    # EVERY counter is initialised here, not only on the failure paths: the
    # detail key set is identical on every return path, so a consumer read of
    # (say) the exclusion counts does not vary with `state`.
    #
    # EXACT OR ABSENT (the rule `recall_attempted_any` already follows): every
    # count derived by a fold starts as `None` — "we did not count" — and is
    # replaced by an int only once the fold that computes it has run. A literal
    # `0` here would assert "we counted and found none" on the refusal paths
    # (`analytics_page_cap_truncated`, `analytics_window_not_supplied`,
    # `analytics_window_predicate_not_applied`) where the fold is gated OFF and
    # the truth is that the count was withheld.
    detail: dict[str, Any] = {
        "recall_attempted_any": None,
        "recall_attempted_after_memory": None,
        "recall_attempted_after_memory_wide": None,
        "recall_rows_fetched": None,
        "recall_page_cap": RECALL_PAGE_CAP,
        "recall_truncated": False,
        "unparseable_analytic_rows": None,
        "unclassifiable_analytic_rows": None,
        "unparseable_analytic_rows_wide": None,
        "unclassifiable_analytic_rows_wide": None,
        "analytics_window_out_of_range": None,
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
    # Window re-check BEFORE any count this function reports — including
    # `recall_attempted_any` on the no-memory path, which is otherwise an
    # UNVERIFIED whole-history count presented as a windowed one. It sits above
    # the `first_memory_at is None` and `truncated` branches so those paths
    # carry the same doubt; the STAGE precedence is left unchanged (an org with
    # no memory stays `not_measurable`, it does not become `unavailable`).
    window_failed = False
    if window is not None:
        out_of_range = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                stamp = _parse_iso(row.get("created_at"), "created_at")
            except (WindowError, TypeError):
                # Not placeable on the timeline. An ALLOWLISTED row that fails
                # here is refused below via `unparseable_analytic_rows`; a
                # non-allowlisted row is excluded before its timestamp is read,
                # so it was never part of the count either way.
                continue
            if not (window[0] <= stamp.isoformat() < window[1]):
                out_of_range += 1
        # The scan RAN, so this is an exact count: 0 means "checked, nothing
        # outside the window". It stays `None` only when no window was supplied
        # — the same exact-or-absent rule as the counters below.
        detail["analytics_window_out_of_range"] = out_of_range
        if out_of_range:
            window_failed = True
            _logger.warning(
                "activation scorecard: %d analytics row(s) fell outside the "
                "requested window [%s, %s) — the created_at predicate did not "
                "apply", out_of_range, window[0], window[1])

    # `recall_attempted_any` and its exclusion counters are computed ONCE, for
    # EVERY path: the count is independent of the lifetime value, so a path
    # that refuses the STAGE must not also discard an exact count or report
    # false-zero exclusions. Emitted only when EXACT — the window verified (the
    # scan above), the page not truncated, and every fetched row placeable and
    # classifiable over BOTH allowlists (a WIDE-only unplaceable row makes the
    # count a lower bound too). The counters are published either way, so a
    # withheld count names its reason. The STAGE is unaffected.
    if window is not None and not window_failed and not truncated:
        since_at = _coerce_created_at(window[0])
        if since_at is not None:
            any_total, any_unp, any_unc = _count_allowlisted(rows, since_at)
            _, any_unp_wide, any_unc_wide = _count_allowlisted(
                rows, since_at, RETRIEVAL_TOOL_ALLOWLIST_WIDE)
            detail["unparseable_analytic_rows"] = any_unp
            detail["unclassifiable_analytic_rows"] = any_unc
            detail["unparseable_analytic_rows_wide"] = any_unp_wide
            detail["unclassifiable_analytic_rows_wide"] = any_unc_wide
            if not (any_unp or any_unc or any_unp_wide or any_unc_wide):
                detail["recall_attempted_any"] = any_total

    if first_memory_at is None:
        # Distinguish the two reasons `first_memory_at` can be NULL, because
        # they carry opposite meanings. `LIFETIME_MEMORY_QUERY` returns a
        # memory-Session count alongside the min(created_at): a positive count
        # with a NULL min means memory EXISTS but carries no timestamp — a data
        # gap, not an absence. Reading that as "no memory was ever produced"
        # would report nothing-to-measure for an org that has memory.
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

    # NOTE: this is NOT the same coercion the row timestamps get. The lifetime
    # value is coerced (``_coerce_created_at``, which accepts an epoch int);
    # rows go through ``_parse_iso`` in ``_count_allowlisted``, which does not.
    # The asymmetry is deliberate and fail-closed: an epoch-int ROW timestamp
    # yields ``unparseable_analytic_rows`` → ``unavailable``, never a silent
    # drop. A lifetime value is coerced because refusing it would brick stage 4
    # with a misleading reason that no retry could clear.
    memory_at = _coerce_created_at(first_memory_at)
    if memory_at is None:
        return _stage(None, "calls", "first_memory_at_unparseable"), detail

    if window is None:
        # The window re-check is not optional on the counting path: reading
        # rows without verifying their window is the silent-dropped-bound class
        # this PR fixed one layer down. Fail closed rather than count rows that
        # were never placed on the timeline.
        return _stage(None, "calls",
                      "analytics_window_not_supplied"), detail
    if window_failed:
        return _stage(None, "calls",
                      "analytics_window_predicate_not_applied"), detail

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
    # The reason names the COUNTER that is actually dirty: a WIDE-only
    # exclusion must not be reported under the narrow counter's name while that
    # counter reads 0. (`unclassifiable` is allowlist-independent, so narrow
    # and wide agree there; `unparseable` is not, because a wide-only tool name
    # is skipped by the narrow pass before its timestamp is read.)
    if unparseable:
        # An allowlisted call whose timestamp will not parse cannot be placed
        # relative to first_memory_at, so it is excluded from the count — which
        # makes the count a LOWER BOUND. Reporting a lower bound as `measured`
        # is the same class of bug as counting an unverifiable row in the
        # window guard, so it is refused here for the same reason.
        return _stage(None, "calls", "unparseable_analytic_rows"), detail
    if wide_unparseable:
        return _stage(None, "calls",
                      "unparseable_analytic_rows_wide"), detail
    if unclassifiable:
        # This store holds `mcp_tool_call` rows only (the read filters on
        # event_name), so a call whose tool_name cannot be read is a call we
        # cannot rule OUT of the retrieval set. Excluding it silently would
        # again make the count a lower bound — the number would look exact.
        return _stage(None, "calls", "unclassifiable_analytic_rows"), detail
    if wide_unclassifiable:
        return _stage(None, "calls",
                      "unclassifiable_analytic_rows_wide"), detail
    # Only now is the count EXACT. The memory-conditioned counts are emitted
    # here and nowhere else, so they are never a lower bound that reads like a
    # count (the unconditioned `recall_attempted_any` is computed once, above,
    # on every path).
    detail["recall_attempted_after_memory"] = narrow
    detail["recall_attempted_after_memory_wide"] = wide
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
        if not isinstance(row, dict):
            # A store/proxy can return an array of non-mapping elements; that
            # is a row we cannot read a tool_name out of, so it makes the count
            # a lower bound — exactly like an unreadable `properties` value.
            unclassifiable += 1
            continue
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
    "recall_attempted is org-scoped, so it counts only events carrying this "
    "org's id. A locally-hosted (stdio) MCP server emits the event but with an "
    "empty org_id (no request context), and the REST recall surface emits no "
    "per-call event at all — both are invisible to this read.",
    "Analytics history before the write-path repair is unrecoverable "
    "(forward-only) — the events were written to an ephemeral VM and lost. "
    "This surface cannot detect a window that predates the repair, so such a "
    "window reports `measured 0`, not `unavailable`: reconcile against the "
    "deploy time before citing a zero that straddles it.",
    "The analytics write path being CONFIGURED is not proof it WORKS — but "
    "the failure direction is narrower than it looks. This read and the "
    "telemetry WRITE use the SAME credential in the SAME process, so a "
    "rotated/revoked key fails the read too and surfaces honestly as "
    "`unavailable` (`analytics_store_unreachable`), not as a false zero. The "
    "two routes to a false `measured 0` that arise from the WRITE PATH'S OWN "
    "health are: (1) a window that predates the repair, above; (2) a failure "
    "that rejects the WRITE while letting this READ succeed (an INSERT-only "
    "RLS denial, a partial/limited role, a silent PostgREST drop). That "
    "enumeration is scoped to the writer's health, not to this list's "
    "completeness — other entries here name further false-zero causes from "
    "different sources. Detectability of (2) is tracked in #3677.",
    "memory_produced counts extraction-SUCCEEDED sessions (a non-transcript "
    "Point exists). It is not a measure of memory that HELPED anyone — that is "
    "value_confirmed, which is unmeasurable.",
    "Extraction outcome (capture_ok / capture_extractor) is recorded on the "
    "Session but exposed by no read surface (owned by #3520).",
    "The analytics leg's interval is [since, until) — the same as the graph "
    "legs — so a boundary instant is treated identically by both. This is the "
    "GUARANTEE, not a limitation: it is asserted by a test, because the two "
    "legs are separate queries and could drift.",
    "Self-hosted orgs are not covered: the analytics store is hosted-lane only.",
    "The analytics read assumes the store's page limit is RECALL_PAGE_CAP. A "
    "LOWER server-side cap (PostgREST db-max-rows, a proxy limit) would return "
    "a short page that looks complete, so recall_attempted would read measured "
    "while under-counting. The cap cannot be probed from here.",
    "A BUSY org that HAS produced memory (>= RECALL_PAGE_CAP MCP tool calls "
    "in the window) reads `unavailable` (`analytics_page_cap_truncated`), not "
    "a count: the analytics leg is a single unpaginated read, so a full page "
    "is a lower bound. (An org with no lifetime memory reads "
    "`not_measurable` instead — the no-memory check deliberately precedes the "
    "page-cap check.) Paging the leg to completion is #4038.",
    "Stage 4 is GATED on `first_memory_at` from the org DEFAULT graph while it "
    "COUNTS an org-wide stream. A multi-graph org that produced memory only in "
    "a non-default graph can read `not_measurable` "
    "(`no_memory_produced_in_lifetime`) even though its windowed recall calls "
    "are counted in `detail.recall_attempted_any` when that count is exact "
    "(a windowed, untruncated, fully-placeable read; otherwise it is None and "
    "the exclusion counters name why). The state is graph-scoped, "
    "the count is org-wide. Surfacing the ambiguity is #4039.",
    "`detail.first_memory_at` is the earliest memory-bearing SESSION's capture "
    "time (`min(s.created_at)`), NOT the creation time of the first extracted "
    "memory Point. Extraction runs after the session is written, so this "
    "boundary is at-or-before the true instant and the recall count can include "
    "calls made in the capture->extraction gap — an upward bias, not a "
    "conservative one.",
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
        "stage3_predicate": STAGE3_PREDICATE,
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
