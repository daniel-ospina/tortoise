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

The six stages, and exactly what each one is evidence of:

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
5      org / time         Supabase      >= 1 of those calls RETURNED >= 1 item
       ``recall_returned`` analytics    (``result_count``, #3675)
6      --                 --            NOT MEASURABLE (see the refusal constant)
       ``value_confirmed``
===== ================== ============= ==========================================

Stage 4 is an ATTEMPT, not an answer. ``mcp_tool_call.status == "ok"`` means
only that the tool did not raise (``tortoise/mcp_server.py``); a search that
returned zero points is still ``ok``. It is reported because it is the only
recorded observation of the read side, and mislabelling it as activation is
precisely the error this lane exists to fix — hence no ``activated`` key, no
activation rate, anywhere in the payload.

STAGE 4's GATE AND ITS COUNT HAVE DIFFERENT SCOPES — #4039. The gate is the org
DEFAULT graph's ``first_memory_at``; the count is the org-wide analytics stream
(``analytics_events`` carries no ``graph_id``). A multi-graph org that produced
memory only outside the default graph therefore has no gate, and for such an org
``no_memory_produced_in_lifetime`` would be a claim about the ORG derived from a
GRAPH-scoped absence — false, and exactly the state inversion this module
exists to prevent. When that org's windowed retrieval count is EXACT AND
POSITIVE, the stage refuses instead: ``unavailable`` /
``no_memory_in_default_graph_org_scope_unknown``. The ambiguity is surfaced, not
asserted away; the rows where the count cannot be established (withheld) are
disclosed in ``LIMITATIONS``.

Stage 5 is where that stops being true. Since #3675 the ``mcp_tool_call``
props carry ``result_count`` — the number of items the call returned — so a
retrieval that returned content is distinguishable from one that returned
nothing, and the attempt/answer split is MEASURED rather than refused. What it
is NOT is a value judgement: a returned item is a CANDIDATE. Nothing recorded
says the candidate was relevant, read, or used, so stage 5 is an UPPER BOUND on
"answered from memory" and is never reported as one. ``value_confirmed`` (stage
6) therefore stays a refusal — see its constant for exactly which gap remains.
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

SCHEMA_VERSION = 2
SURFACE = "activation_scorecard"

# Stage 5's prop (#3675): the number of items a tool call returned, emitted on
# the ``mcp_tool_call`` analytics row by the hosted MCP dispatch seam. The value
# is an int, or ``None``/absent when the call's RESULT SHAPE made the count
# unknowable (a write op, a refusal, a row written before #3675 shipped).
# ``0`` and ``None`` are different facts and are never collapsed here: ``0`` is
# "the tool ran and returned nothing", ``None`` is "we cannot say".
RESULT_COUNT_PROP = "result_count"

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

# The analytics read's PAGE SIZE, handed to ``SupabaseControlPlane.query_all``.
# PostgREST caps an unpaginated read at the project's max-rows (1000 by
# default), so this is also the hint that makes the common case one request.
# It is a HINT, not an assumption: a deployment capped LOWER now costs more
# pages rather than silently under-counting, because the walk ends on an EMPTY
# page, never on a page that is merely short (#4038 — the pre-fix read took a
# full page for a lower bound and refused; see the limitations).
RECALL_PAGE_CAP = 1000

# The MOST pages the stage-4 walk will read before it refuses to certify the
# count (#4038). A bound is required — its length IS the read's latency and
# memory, and the read sits on a per-org path with a 30s cohort timeout — but
# exceeding it must stay a refusal (`analytics_page_cap_truncated`), never a
# partial count published as measured. 100 pages is 100x the size at which the
# pre-fix read gave up entirely: an org sustaining >100k `mcp_tool_call` rows in
# one window still reads `unavailable`, honestly, and the fix for that tail is
# a server-side aggregate, not a longer walk this surface holds in memory.
RECALL_MAX_PAGES = 100

# #4039: the stage-4 reason for the GRAPH-scope ambiguity. A SEPARATE reason (not
# a reworded ``no_memory_produced_in_lifetime``) because the two are different
# facts: that one says the org has nothing to recall FROM, this one says the org
# cannot be reached by this gate — its memory may live outside the default graph,
# which this read never opens. Consumers switch on ``state``, so the qualifying
# belongs in BOTH the state (`unavailable`, not `not_measurable`) and the reason:
# a scope-qualified reason under a `not_measurable` state would keep the
# mis-scoped classification while fixing only the sentence beside it.
GRAPH_SCOPE_UNKNOWN_REASON = "no_memory_in_default_graph_org_scope_unknown"
# Default window = the beta's own <= 24h criterion (product-success-eval.md §5).
DEFAULT_WINDOW = timedelta(hours=24)
# A window this wide is a typo or an attempt to scan the whole history; the
# scorecard is a beta instrument, not an archive query.
MAX_WINDOW = timedelta(days=90)

# ── Stage 6: the refusal ───────────────────────────────────────────────────
# A string, not a number — and since #3675 the reason is NARROWER than it was.
# The result-bearing telemetry now exists (``result_count`` on ``mcp_tool_call``,
# read into stage 5), so "nothing distinguishes a retrieval that returned
# remembered content from one that returned nothing" is no longer true and must
# not be repeated: stage 5 measures exactly that. What no recorded signal says
# is whether a RETURNED item was USED — relevance, dwell, dismissal and reuse
# are unrecorded, and the two stated blockers (#3518/#3519, captured sessions
# not being findable/retrievable) are CLOSED. So the remaining gap is a claim
# about VALUE, which is what this stage is named for.
VALUE_CONFIRMED_REFUSAL = (
    "not_measurable: the retrieval returned content, but nothing recorded says "
    "that the content was USED. stage 5 (recall_returned) reports calls whose "
    "result_count >= 1 (#3675) — a returned item is a CANDIDATE, not a consumed "
    "answer: relevance, dwell, dismissal and reuse are not recorded by any "
    "surface, so 'answered-from-memory' can be bounded from above and not "
    "confirmed. Per-session attribution is also impossible (the telemetry "
    "carries no session_id and analytics_events no graph_id), and the "
    "result_count prop is forward-only — a window that predates its deploy is "
    "refused by stage 5 rather than read as a zero. 'value_confirmed' stays "
    "UNMEASURED, not zero."
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
# which is the corrected one. The ``list_sessions`` variant it replaced
# (``pointKind IN ['decision','statement']``, unified away in #3555) was a
# known defect: extraction writes NULL / other kinds, so it reported 0 for
# sessions that did produce memory. Never use that filter here.
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

# ── The activation-claim refusal (one home for BOTH surfaces) ─────────────
# The rule that no payload may carry an activation claim is the lane's whole
# point (see VALUE_CONFIRMED_REFUSAL), so it is enforced at the assemblers —
# not only asserted by a test. ONE implementation, TWO callers (`assemble` for
# the org surface, `assemble_cohort` for the operator cohort surface).
# `tools/activation_cohort.py` carries its own copy ON PURPOSE: it is a
# stdlib-only operator client and must be able to refuse a payload the server
# should never have produced, without importing this package (see its note).
def activation_claim_keys(payload: object) -> list[str]:
    """Every KEY at any depth whose name claims activation.

    Deliberately key-based, not prose-based: ``LIMITATIONS`` and the cohort's
    ``note`` must be free to name the claim they refuse to make.
    """
    found: list[str] = []
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if "activat" in str(key).lower():
                    found.append(str(key))
                stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    return found


def assert_no_activation_claim(payload: object) -> None:
    """Fail closed if a payload fabricates an activation claim.

    Raises ``ValueError`` — never a bare ``assert`` (a ``-O`` interpreter
    would strip it) and never a silent filter (a metric mislabelled at the
    source is what this lane exists to remove).
    """
    claims = activation_claim_keys(payload)
    if claims:
        raise ValueError(
            f"refusing to emit a payload that claims activation ({claims}) — "
            f"recall_attempted is an ATTEMPT and value_confirmed is a "
            f"REFUSAL, so no activation field or rate is derivable")


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
    An org that has never produced memory — and has no contradicting org-wide
    retrieval count (see :data:`GRAPH_SCOPE_UNKNOWN_REASON`) — has nothing to
    recall FROM, and no retry changes that, so that case is ``not_measurable``
    and must say so explicitly rather than inheriting ``unavailable``.
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
    """Stages 4 AND 5, and the shared detail (#3675 extended this to stage 5).

    Stage 4 is returned; stage 5's cell rides in ``detail["recall_returned_cell"]``
    for ``assemble`` to lift into ``stages``. The two-cell-in-a-tuple shape is
    deliberately NOT used: ``recall_stages`` has a long-standing 2-tuple
    contract with ~25 call sites (tests included) and the stage-4 cell is what
    they unpack — changing the arity would churn every one of them for a stage
    that is derived FROM stage 4's outcome anyway. The cell is popped back out
    of ``detail`` by ``assemble``, so it never reaches the payload's ``detail``.

    Stage 5 inherits stage 4's refusals verbatim (see ``recall_returned_cell``),
    so this wrapper adds no precedence of its own.
    """
    stage, detail = _recall_attempted_stage(
        rows, first_memory_at, reason=reason, truncated=truncated,
        memory_sessions=memory_sessions, window=window)
    detail["recall_returned_cell"] = recall_returned_cell(stage, detail)
    return stage, detail


def _recall_attempted_stage(rows: Iterable[dict] | None, first_memory_at: str | None,
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
        # #3675 stage 5 — exact-or-absent, like every other fold's output.
        "recall_returned_after_memory": None,
        "rows_without_result_count": None,
        "rows_uncountable_result_count": None,
        "result_count_prop": RESULT_COUNT_PROP,
        "recall_rows_fetched": None,
        "recall_page_cap": RECALL_PAGE_CAP,
        "recall_page_limit": RECALL_MAX_PAGES,
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
    # carry the same doubt; the STAGE precedence is otherwise unchanged (an org
    # with no memory AND a withheld/`0` org-wide count stays `not_measurable`;
    # the one #4039 exception below — an exact, POSITIVE org-wide count — is
    # what becomes `unavailable`).
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
        # `recall_attempted_any` is the count over the ORG-WIDE windowed stream
        # and it is emitted ONLY when exact (above), so a POSITIVE value here is
        # a positive, verified observation that this org made retrieval calls in
        # the window while the DEFAULT graph shows no memory ever produced —
        # #4039. The absence is graph-scoped, the calls are org-wide, so the
        # org-scope claim `no_memory_produced_in_lifetime` is not established:
        # the org's memory may live in a graph this read does not open. Refuse
        # the state (the metric exists and this gate cannot reach it) rather
        # than assert it. The SAME pinned retrieval allowlist is the trigger, so
        # the evidence and the count are one definition. `None` (withheld) or
        # `0` (exact zero) leaves no org-wide activity to contradict the gate,
        # so a single-graph org with no memory stays `not_measurable` as before.
        if detail["recall_attempted_any"]:
            return _stage(None, "calls", GRAPH_SCOPE_UNKNOWN_REASON,
                          state=STATE_UNAVAILABLE), detail
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
    # #3675 stage 5: the result-bearing half of the NARROW population — the
    # same rows stage 4 counts as attempts. Folded here, beside the counts
    # above, so the two stages cannot disagree about which rows they are looking
    # at. `_count_returned`'s unparseable / unclassifiable outputs are DISCARDED
    # on purpose: they are computed over the identical population with the
    # identical helpers, so they are exactly the values stage 4 already refuses
    # on, and stage 5 inherits that refusal (`recall_returned_cell`). Keeping a
    # second copy would invite the two stages to disagree about the same rows.
    #
    # NARROW ONLY — see `recall_returned_cell`: the wide set's known-id reads
    # return a single object with no item collection and are uncountable by
    # shape, so folding them in would permanently refuse the stage.
    returned, missing_count, uncountable_count, _, _ = _count_returned(
        rows, memory_at, RETRIEVAL_TOOL_ALLOWLIST)
    detail["recall_returned_after_memory"] = returned
    detail["rows_without_result_count"] = missing_count
    detail["rows_uncountable_result_count"] = uncountable_count
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
        props = _row_props(row)
        if props is None:
            # A store/proxy can return an array of non-mapping elements, or a
            # `properties` value that will not parse; that is a row we cannot
            # read a tool_name out of, so it makes the count a lower bound —
            # exactly like an unreadable `properties` value.
            unclassifiable += 1
            continue
        tool = _row_tool_name(props)
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


def _row_props(row) -> dict | None:
    """A row's ``properties`` as a mapping, or ``None`` when unreadable.

    The writer stores a jsonb object, but a store or proxy can hand back the
    column as a JSON STRING — both shapes are measured live here, so both are
    read. ``None`` means "this row's properties cannot be read at all", which
    every caller must count as an exclusion rather than skip silently: a
    skipped row makes the count a lower bound that still looks exact.
    """
    if not isinstance(row, dict):
        return None
    props = row.get("properties")
    if isinstance(props, dict):
        return props
    if isinstance(props, str):
        try:
            import json
            parsed = json.loads(props)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _row_tool_name(props: dict) -> str | None:
    """``tool_name`` from a row's properties mapping, or ``None`` when absent.

    ``str(...) or None`` (not a bare ``or None``) is deliberate and is the
    behaviour this replaced: a non-string name (an int, say) is coerced, and an
    empty one reads as unreadable.
    """
    return str(props.get("tool_name") or "") or None


def _count_returned(rows: Iterable[dict], after: datetime | None,
                    allow: frozenset[str] | None = None) -> tuple[int, int, int, int, int]:
    """Count allowlisted rows AT/AFTER ``after`` that RETURNED >= 1 item (#3675).

    Returns ``(returned, missing_result_count, uncountable_result_count,
    unparseable, unclassifiable)``.

    ``returned`` counts rows whose ``properties.result_count`` is an int >= 1.
    A ``0`` — the tool ran and returned nothing — is a real, measured row and is
    deliberately NOT counted.

    The two exclusion counters are DIFFERENT FACTS and are kept apart, because
    they call for different responses:

    * ``missing_result_count`` — the prop is not on the row AT ALL. The
      ``mcp_tool_call`` emitter writes it unconditionally (#3675), so an absent
      key means the row predates that deploy. Forward-only, unrecoverable for
      existing rows, and invisible from inside the payload unless it is counted
      here — the reason the stage refuses instead of publishing the resulting
      lower bound as a count.
    * ``uncountable_result_count`` — the key IS there and is not a count
      (``null``, a bool, a negative). That is a retrieval whose result carried
      no item collection: an SDK failure returned through ``_safe``, an
      unrecognised recall mode, or a result shape that no longer matches the
      three measured ones. Also a lower bound, also refused — and named
      separately so a deploy-boundary window is not confused with a shape
      regression.

    ``False`` is not an int here even though Python says it is, and a NEGATIVE
    is not a count. The row-reading discipline is shared with
    ``_count_allowlisted`` through ``_row_props``/``_row_tool_name``; a second
    copy is how the two folds would come to disagree about which rows they are
    even counting.
    """
    allow = allow if allow is not None else RETRIEVAL_TOOL_ALLOWLIST
    returned = 0
    missing_result_count = 0
    uncountable_result_count = 0
    unparseable = 0
    unclassifiable = 0
    for row in rows:
        props = _row_props(row)
        if props is None:
            unclassifiable += 1
            continue
        tool = _row_tool_name(props)
        if tool is None:
            unclassifiable += 1
            continue
        if tool not in allow:
            continue
        stamp = row.get("created_at")
        if after is not None:
            try:
                stamp_dt = _parse_iso(stamp, "created_at")
            except (WindowError, TypeError):
                unparseable += 1
                continue
            if stamp_dt < after:
                continue
        if RESULT_COUNT_PROP not in props:
            missing_result_count += 1
            continue
        count = props.get(RESULT_COUNT_PROP)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            uncountable_result_count += 1
            continue
        if count >= 1:
            returned += 1
    return (returned, missing_result_count, uncountable_result_count,
            unparseable, unclassifiable)


def value_confirmed_stage() -> dict:
    """Stage 6 — a refusal, always. Never a number, never nullable-to-mean-zero."""
    return {"state": STATE_NOT_MEASURABLE, "value": None, "unit": None,
            "reason": VALUE_CONFIRMED_REFUSAL}


def recall_returned_cell(stage_4: dict, detail: dict) -> dict:
    """Stage 5's cell, derived from stage 4's outcome and the same fold (#3675).

    Stage 5 asks a strictly narrower question than stage 4 ("of those calls,
    how many returned anything"), so it INHERITS every refusal stage 4 can
    raise rather than re-deriving its own precedence — a truncated page, an
    out-of-range row, an unplaceable timestamp and a never-produced-memory org
    all make both stages unreadable, and two copies of that ladder would drift.

    On the exact path it adds TWO refusals of its own, both because they make
    the count a LOWER BOUND that would otherwise be published as if it were
    exact:

    * a call whose row has no ``result_count`` at all — the emitter writes the
      key unconditionally, so this is a row written before #3675 shipped
      (forward-only history);
    * a call whose ``result_count`` is present but is not a count — a result
      with no item collection (an SDK failure surfaced by ``_safe``, an
      unrecognised recall mode, a shape that no longer matches the three
      measured ones).

    The two are named separately so an operator can tell a deploy-boundary
    window from a shape regression.

    The NARROW allowlist only. The wide set exists as a sensitivity bound on
    the ATTEMPT (stage 4), and it deliberately includes known-id reads like
    ``tortoise_get_point``, whose result is a single object with no item
    collection — uncountable BY SHAPE, forever. Folding that into stage 5 would
    refuse the stage for any org that ever read a point by id.
    """
    if stage_4.get("state") != STATE_MEASURED:
        return {"state": stage_4.get("state"), "value": None,
                "unit": "calls", "reason": stage_4.get("reason")}
    if detail.get("rows_without_result_count"):
        return _stage(None, "calls", "result_count_not_recorded")
    if detail.get("rows_uncountable_result_count"):
        return _stage(None, "calls", "result_count_unreadable")
    returned = detail.get("recall_returned_after_memory")
    if returned is None:
        # Exact-or-absent: the fold did not run, so there is no count to report
        # and a literal 0 here would assert one.
        return _stage(None, "calls", "recall_returned_not_counted")
    return _stage(returned, "calls")


# ── Assembly ───────────────────────────────────────────────────────────────
LIMITATIONS: tuple[str, ...] = (
    "value_confirmed ('answered-from-memory') is NOT MEASURABLE, and since "
    "#3675 for a NARROWER reason than before: stage 5 (recall_returned) reads "
    "`result_count` off the mcp_tool_call props, so a retrieval that returned "
    "nothing IS now distinguishable from one that returned remembered content. "
    "What no recorded signal says is whether the RETURNED content was USED — "
    "relevance, dwell, dismissal and reuse are unrecorded. Stage 5 is "
    "therefore an UPPER BOUND on 'answered from memory', not a value measure.",
    "recall_attempted is an ATTEMPT, not an answer — it must never be read as "
    "activation, and no activation rate is derivable from it.",
    "recall_returned is a RETURN, not an answer. It counts retrieval calls "
    "whose `result_count` is >= 1 (#3675), so it says the retrieval surface "
    "handed back candidates — never that a candidate was relevant, read, or "
    "used. Reading it as 'value confirmed' is exactly the mislabelling this "
    "module exists to remove.",
    "recall_returned's `result_count` prop is FORWARD-ONLY: rows written "
    "before #3675 shipped carry no count, so a retrieval row with NO "
    "`result_count` makes the stage `unavailable` "
    "(`result_count_not_recorded`), not a count. The prop's own deploy time "
    "is recorded nowhere, so a window that straddles it cannot be identified "
    "from the payload — the stage refuses, and a window wholly after the "
    "deploy measures normally. (`detail.rows_without_result_count` names how "
    "many rows were unreadable.)",
    "recall_returned refuses on a PRESENT but unreadable `result_count` too "
    "(`result_count_unreadable`, `detail.rows_uncountable_result_count`): an "
    "SDK failure surfaced by `_safe`, an unrecognised recall mode, or a result "
    "shape that no longer matches the three the MCP seam counts. Some of those "
    "rows certainly returned nothing (a failed call did), so this is the "
    "CONSERVATIVE direction — the alternative would publish a lower bound as a "
    "count. The two counters are separate so a shape regression is not read as "
    "a deploy-boundary window. (The underlying reason a failed call is hard to "
    "tell apart here is that `_safe` swallows the exception, so the row reads "
    "`status: 'ok'` — a separate defect.)",
    "recall_returned uses the NARROW retrieval allowlist only, where stage 4 "
    "reports both. The wide set adds known-id reads (`tortoise_get_point` et "
    "al) whose result is a single object with no item collection: uncountable "
    "by construction, so folding them into this stage would refuse it for any "
    "org that ever read a point by id.",
    "recall_returned inherits a WEAKER identity condition than it looks: "
    "`result_count` is an integer (or null), so it is content-free by "
    "construction — but it is also SHAPE-derived, not tool-derived. A tool "
    "that returns a collection under `result`/`results`/`nodes` is counted "
    "whatever its name, and a retrieval tool whose result shape changed to "
    "none of those would stop carrying a count and drive stage 5 to "
    "`unavailable` (`result_count_unreadable`) rather than to a wrong number.",
    "Per-session recall attribution is IMPOSSIBLE: the telemetry carries no "
    "session_id, so stages 4 and 5 are org/time-scoped while stages 1-3 are "
    "session-scoped. This is an activation FUNNEL of mixed granularity, not a "
    "per-session claim.",
    "Stages 1-3 cover the org DEFAULT graph only (the endpoint is an org-level "
    "surface). Stages 4 and 5 are org-wide. For a multi-graph org the two "
    "denominators differ; the graph_scope field states this in the payload.",
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
    "value_confirmed, which is unmeasurable; stage 5 bounds it from above.",
    "Extraction outcome (capture_ok / capture_extractor) is recorded on the "
    "Session but exposed by no read surface (owned by #3520).",
    "The analytics leg's interval is [since, until) — the same as the graph "
    "legs — so a boundary instant is treated identically by both. This is the "
    "GUARANTEE, not a limitation: it is asserted by a test, because the two "
    "legs are separate queries and could drift.",
    "Self-hosted orgs are not covered: the analytics store is hosted-lane only.",
    "The analytics read is keyset-paged to COMPLETION on `id` (#4038): it walks "
    "pages until the store returns an EMPTY one, so a page that is merely SHORT "
    "— a deployment whose PostgREST db-max-rows (or a proxy cap) sits below "
    "`recall_page_cap`, which used to look complete and under-count — costs "
    "more requests instead of a smaller number. Completion is REFUSED, and the "
    "stage reads `unavailable` (`analytics_page_cap_truncated`), when the walk "
    "cannot certify it: it exceeds `recall_page_limit` pages, a page is not "
    "strictly ascending on `id` (the server ignored `order`), a page carries no "
    "usable `id` to carry the cursor, or the store will not advance past the "
    "cursor. A lower bound is never published as a count.",
    "A BUSY org no longer reads `unavailable` for being busy: the pre-#4038 read "
    "stopped at `recall_page_cap` (1000) rows and called a full page a lower "
    "bound, which landed `unavailable` on exactly the most-used orgs. The "
    "binding bound is now `recall_page_cap * recall_page_limit` rows in ONE "
    "window; past it the stage still refuses rather than under-reporting. (An "
    "org with no lifetime memory reads `not_measurable` instead — the "
    "no-memory check deliberately precedes the page-cap check.)",
    "Stage 4 is GATED on `first_memory_at` from the org DEFAULT graph while it "
    "COUNTS an org-wide stream. A multi-graph org that produced memory only in "
    "a non-default graph can read `not_measurable` "
    "(`no_memory_produced_in_lifetime`) even though its windowed recall calls "
    "are counted in `detail.recall_attempted_any` when that count is exact "
    "(a windowed, untruncated, fully-placeable read; otherwise it is None and "
    "the exclusion counters name why). The state is graph-scoped, "
    "the count is org-wide. #4039: a windowed retrieval CALL is counted by its "
    "`tool_name` alone — `status` and `result_count` are not read — so the "
    "count is calls, not answers: a call that FAILED (`status` `exec_error`) or "
    "returned nothing (`result_count` 0) is counted exactly like one that "
    "returned items. When that exact count is POSITIVE the stage no longer "
    "makes the org-scope claim — it reports `unavailable` "
    f"(`{GRAPH_SCOPE_UNKNOWN_REASON}`). The refusal is therefore deliberately "
    "CONSERVATIVE: this read cannot tell an org whose memory lives in another "
    "graph from a single-graph org whose only windowed recall activity failed "
    "or returned nothing, so such a single-graph org reads `unavailable` "
    "rather than `not_measurable`. RESIDUAL, not fixed: "
    "a multi-graph org whose count is withheld (a truncated page, an "
    "unplaceable or unclassifiable row, an out-of-window row) or is an exact "
    "`0` (no retrieval call in the window) still reads "
    "`no_memory_produced_in_lifetime`, because no org-wide call count "
    "contradicts "
    "the graph-scoped absence — this read cannot enumerate the org's other "
    "graphs, so that case is indistinguishable from a single-graph org with no "
    "memory. Making the gate itself org-scoped is the fix that would remove the "
    "residual; it is a new read (an org's graph list, then a lifetime memory "
    "scan per graph) rather than an instrumentation-definition choice — "
    "tracked in #8149.",
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
    # #3675 stage 5. Its cell is produced by ``recall_stages`` — it shares stage
    # 4's window / truncation / memory precedence — and rides in ``detail`` so
    # that long-standing 2-tuple contract (and its ~25 call sites) does not
    # change. Lift it into ``stages`` and keep it OUT of ``detail``: that dict is
    # a payload field, not a place for stage cells. A caller that hand-builds
    # ``recall_detail`` (a test, or a future second producer) gets a refusal
    # naming the omission rather than a fabricated zero.
    stages["recall_returned"] = (recall_detail.get("recall_returned_cell")
                                 or _stage(None, "calls",
                                           "recall_returned_not_computed"))
    stages["value_confirmed"] = value_confirmed_stage()

    detail = {
        **{k: v for k, v in graph_detail.items() if k != "integrity"},
        "first_memory_at": (lifetime or {}).get("first_memory_at"),
        "sessions_with_memory_all_time": (lifetime or {}).get("sessions_with_memory"),
        **{k: v for k, v in recall_detail.items()
           if k != "recall_returned_cell"},
        "analytics_write_path": analytics_state,
        "graph_scope": "org_default_graph",
        "stage3_predicate": STAGE3_PREDICATE,
        "recall_returned_predicate": (
            "calls whose properties.result_count >= 1 (an int; null or absent "
            "is NOT zero — it refuses the stage)"),
        "stages_1_3_granularity": "session",
        "stage_4_granularity": "org_time",
        "stage_5_granularity": "org_time",
    }
    payload = {
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
    assert_no_activation_claim(payload)
    return payload


# ══ The cohort roll-up (#3676) ═════════════════════════════════════════════
# The beta cohort is structurally MULTI-ORG (the free tier allows one org per
# person with no invites), so an org-scoped scorecard alone reports n=1 — a
# dogfood read. `roll_up_cohort` folds N per-org payloads into ONE cohort
# number, and it lives HERE (not in the operator script that first carried it)
# because the cross-tenant endpoint is now a caller: one implementation, so the
# scripted and the served cohort number cannot drift.
#
# It is PURE: no I/O, no globals. The caller owns the reads, the auth, and the
# org list.
COHORT_SURFACE = "activation_cohort"

#: The stages a cohort roll-up folds. Pinned as a literal (not derived from
#: ``assemble``'s output) so a stage added to one surface and not the other is
#: a visible omission in the cohort payload rather than a silent one.
COHORT_STAGE_NAMES = ("captured", "stored", "memory_produced",
                      "recall_attempted", "recall_returned",
                      "value_confirmed")

#: Blast-radius bound on an operator-supplied cohort (see
#: ``hosted_api.activation_cohort``): the endpoint reads every named org, so an
#: unbounded list is an unbounded batch job on shared provider capacity. This is
#: an OPERATIONAL bound, NOT a cohort-size requirement — the owner withdrew the
#: minimum-N aha bar on #3497 §7.4 ("there should eb no number"), and nothing
#: here says a cohort must reach any size. Exceeding it is a 422 that names the
#: bound and tells the caller to split the request.
MAX_COHORT_ORGS = 100

COHORT_LIMITATIONS: tuple[str, ...] = (
    "The cohort is an OPERATOR-SUPPLIED list, echoed verbatim in "
    "cohort_definition. This surface never enumerates orgs, accepts no "
    "wildcard, and returns no org the caller did not name.",
    "Every stage is folded from the SAME per-org payload shape the org-scoped "
    "scorecard returns, read through the same code path — the two surfaces "
    "cannot disagree about one org. A stage counted over fewer orgs than the "
    "cohort is still `partial`/`unavailable` with the org NAMED, never summed "
    "as 0.",
    "`value_confirmed` is `not_measurable` for every org by construction, so "
    "its cohort state is normally `not_measurable` — that is the healthy "
    "answer, not a reporting failure.",
    # #3675: this stage's refusals are COMMON rather than exceptional, so the
    # folding rule above needs saying explicitly — a `partial` here is the
    # honest answer, not a defect.
    "`recall_returned` refuses per org (and so usually folds to `partial`) "
    "whenever an org's window predates the `result_count` prop's deploy or a "
    "retrieval row carries no readable count: the per-org count would be a "
    "LOWER BOUND. The org is named in its `orgs_unavailable`. It is also an "
    "UPPER BOUND on 'answered from memory' — a returned candidate is not a "
    "used one.",
    "`org_errors` explains PER-ORG read failures only (the graph funnel, the "
    "stages read once per org). A failure of the SHARED `recall_attempted` "
    "store is a server-level condition, reported once in `notes` rather than "
    "attributed to every org; that stage's per-org verdict still appears in "
    "its `orgs_unavailable`. EXCEPTION: "
    f"`{GRAPH_SCOPE_UNKNOWN_REASON}` is a per-ORG condition reported instead "
    "with org attribution, because the graph-scope ambiguity it names belongs "
    "to each org rather than to the store (see the org-scoped surface's "
    "`limitations`).",
    # #3676 review: a well-formed name that names NO org is not detected. The
    # endpoint does no registry lookup (the entry above), so a typo reads as an
    # empty graph and is reported as a MEASURED 0 with no `org_errors` entry —
    # indistinguishable from an org with genuinely zero activity. Adding an
    # existence check would BE a registry lookup and is a new decision.
    "A well-formed name that names NO org is NOT detected: with no registry "
    "lookup, it reads as an empty graph and is reported as a measured `0` with "
    "no `org_errors` entry, indistinguishable from an existing org with "
    "genuinely zero activity — so an entirely-typo'd cohort returns a "
    "confident 0. Verify the names against the org list before acting on a 0.",
    LIMITATIONS[1],
)


def roll_up_cohort(orgs: Sequence[str], payloads: Sequence[dict | None],
                   errors: Sequence[str | None] | None = None) -> dict:
    """Sum the per-org stages into one cohort number.

    ONLY ``state == "measured"`` stages are summed. The two non-measured states
    are NOT collapsed into one another:

    * ``unavailable`` — the org failed to report (unreadable, timed out, or a
      malformed cell). Named in ``orgs_unavailable``.
    * ``not_measurable`` — the org answered correctly and there is nothing to
      measure (e.g. ``value_confirmed`` always; ``recall_attempted`` for an org
      that has never produced memory). Named in ``orgs_not_measurable``.

    Collapsing the second into the first would assert that a healthy org failed
    to report — the exact state inversion this lane exists to prevent. So the
    cohort stage is ``not_measurable`` only when every org said so, ``partial``
    when some orgs measured and others did not report or had nothing to
    measure, and ``unavailable`` when nothing was measurable for a reason that
    is not "there is nothing to measure".

    ``payloads`` carries ``None`` for an org that could not be read; ``errors``
    carries the reason per org (only for the orgs that failed). ``errors`` must be
    the SAME LENGTH as ``orgs``.

    ``org_errors`` is a LIST of ``{"org_id", "reason"}`` records, never a map
    keyed by org id: an org id is caller-supplied text, and `org-b7-activation`
    as a KEY trips `assert_no_activation_claim` (the claim rule is a substring
    test on every key, deliberately). No caller-supplied string is a key in this
    payload.
    """
    errors = list(errors) if errors is not None else [None] * len(orgs)
    if len(payloads) != len(orgs) or len(errors) != len(orgs):
        raise ValueError(
            f"roll_up_cohort needs one entry per org: {len(orgs)} orgs, "
            f"{len(payloads)} payloads, {len(errors)} errors")
    stages: dict[str, dict] = {}
    for name in COHORT_STAGE_NAMES:
        values: list[int] = []
        orgs_unavailable: list[str] = []
        orgs_not_measurable: list[str] = []
        for org_id, payload in zip(orgs, payloads, strict=True):
            if payload is None:
                orgs_unavailable.append(org_id)
                continue
            cell = (payload.get("stages") or {}).get(name)
            if not cell:
                orgs_unavailable.append(org_id)
                continue
            state = cell.get("state")
            if state == "measured" and cell.get("value") is not None:
                values.append(int(cell["value"]))
            elif state == "not_measurable":
                orgs_not_measurable.append(org_id)
            else:
                # `unavailable`, or a `measured` cell with no value — a
                # malformed upstream payload. Counting it as 0 would invent a
                # measurement the org never reported.
                orgs_unavailable.append(org_id)

        measured = len(values)
        if measured and (orgs_unavailable or orgs_not_measurable):
            total = sum(values)
            cell = {"state": "partial", "value": total,
                    "reason": "some_orgs_unavailable_or_not_measurable",
                    "orgs_measured": measured,
                    "orgs_unavailable": orgs_unavailable,
                    "orgs_not_measurable": orgs_not_measurable}
            if total == 0 and orgs_unavailable:
                # A partial cohort's sum is a LOWER BOUND: the orgs we could
                # not read are exactly the ones that might carry the activity.
                # A lower bound of 0 is not a fact, so refuse the number rather
                # than emit a zero the cohort never established. (A 0 from
                # `not_measurable` orgs alone IS a fact — they genuinely have
                # nothing — so only `unavailable` triggers the refusal.)
                cell["value"] = None
                cell["reason"] = "partial_zero_from_an_unreadable_cohort"
            stages[name] = cell
        elif measured:
            stages[name] = {"state": "measured", "value": sum(values),
                            "reason": None, "orgs_measured": measured,
                            "orgs_unavailable": [], "orgs_not_measurable": []}
        elif orgs_unavailable:
            stages[name] = {"state": "unavailable", "value": None,
                            "reason": "no_org_measurable",
                            "orgs_measured": 0,
                            "orgs_unavailable": orgs_unavailable,
                            "orgs_not_measurable": orgs_not_measurable}
        elif orgs_not_measurable:
            # Every org answered, and every answer was "nothing to measure".
            stages[name] = {"state": "not_measurable", "value": None,
                            "reason": "no_org_has_a_measurable_value",
                            "orgs_measured": 0,
                            "orgs_unavailable": [],
                            "orgs_not_measurable": orgs_not_measurable}
        else:
            # Unreachable: the endpoint refuses an empty list (422) before
            # folding. Refuse rather than report a fabricated measured zero.
            stages[name] = {"state": "unavailable", "value": None,
                            "reason": "no_orgs", "orgs_measured": 0,
                            "orgs_unavailable": [], "orgs_not_measurable": []}
    return {
        "cohort_definition": {
            "orgs": list(orgs),
            "size": len(orgs),
            "source": "operator_supplied",
        },
        # A one-org cohort is a dogfood measurement, not stranger activation —
        # the product's own gate vocabulary (product-success-eval.md: internal
        # gate vs public gate).
        "dogfood_only": len(orgs) == 1,
        "stages": stages,
        "org_errors": [{"org_id": o, "reason": e}
                       for o, e in zip(orgs, errors, strict=True) if e],
        "note": ("recall_attempted is an ATTEMPT, not an answer; value_confirmed "
                 "is a refusal. No activation rate is derivable from this output."),
    }


#: The stages whose read is PER ORG — the graph funnel. `recall_attempted` is
#: deliberately absent: it reads ONE shared analytics store, so its failures are
#: a property of the SERVER rather than of any single org (see
#: `shared_stage_notes`).
COHORT_ORG_READ_STAGES = ("captured", "stored", "memory_produced")

#: The stage read from ONE shared store for the whole cohort.
COHORT_SHARED_STAGE = "recall_attempted"


def org_failure_reason(payload: dict | None) -> str | None:
    """The reason this org's OWN READ failed, or ``None`` if it reported.

    A per-org read can fail at the TRANSPORT level (unreadable store, timeout)
    or at the FOLD level — ``_scorecard_for_org`` deliberately absorbs the
    latter into the payload as ``unavailable`` stages rather than raising. Both
    are the same fact to the cohort: this org's number is missing. This helper
    makes the cohort's ``org_errors`` entry agree with the roll-up's own verdict
    instead of leaving fold-level failures visible only as a bare name in
    ``orgs_unavailable``.

    SCOPED TO ``COHORT_ORG_READ_STAGES`` (the graph funnel) ON PURPOSE. The
    shared `recall_attempted` stage's failures are not attributable to an org:
    `analytics_write_path_unconfigured` would be repeated for all 100 members of
    a cohort on a box whose analytics writer is dark — accusing every healthy
    org of a server misconfiguration, the state inversion this lane exists to
    prevent. That cause is hoisted ONCE by `shared_stage_notes`; the stage's
    per-org verdict still lives in the roll-up's `orgs_unavailable`.

    ``value_confirmed`` is excluded for the same reason it always is: it is
    ``not_measurable`` for every org by construction, so counting it would
    accuse every healthy org. A ``measured`` cell with no value is a malformed
    upstream payload and IS reported.
    """
    if payload is None:
        return "org_unreadable"
    for name in COHORT_ORG_READ_STAGES:
        cell = (payload.get("stages") or {}).get(name) or {}
        if cell.get("state") == STATE_UNAVAILABLE:
            return str(cell.get("reason") or "unavailable")
        if cell.get("state") == STATE_MEASURED and cell.get("value") is None:
            return "malformed_measured_cell"
    return None


#: Stage-4 reasons that are a property of ONE ORG, not of the shared analytics
#: store. `shared_stage_notes` hoists a shared-stage reason ONCE so it is not
#: repeated per org, and the sentence it writes says the condition is
#: "server-level ... rather than a per-org read failure" — a claim about the
#: STORE. For these reasons that sentence would be FALSE, and a false claim
#: introduced by the fix for a false claim is not a fix (#4039): the graph-scope
#: ambiguity is per-org by definition. They get their own attributed sentence;
#: the affected orgs stay named in `stages.recall_attempted.orgs_unavailable`.
ORG_SCOPED_STAGE_4_REASONS = frozenset({GRAPH_SCOPE_UNKNOWN_REASON})


def shared_stage_notes(payloads: Sequence[dict | None]) -> list[str]:
    """ONE cohort-level note per distinct reason the SHARED stage is unmeasured.

    The counterpart to `org_failure_reason`'s scoping: a condition that belongs
    to the shared analytics store must not be repeated per org, but it must not
    be dropped either — that would leave the operator with `unavailable` and no
    cause anywhere in the payload. Hoisting it here keeps it visible exactly
    once, and the count says how much of the cohort it covers.

    The HOISTED SENTENCE is about the store, so it is only written for reasons
    that actually are store-level — `ORG_SCOPED_STAGE_4_REASONS` get their own.
    """
    counts: dict[str, int] = {}
    for payload in payloads:
        if payload is None:
            continue
        cell = (payload.get("stages") or {}).get(COHORT_SHARED_STAGE) or {}
        if cell.get("state") == STATE_UNAVAILABLE:
            reason = str(cell.get("reason") or "unavailable")
            counts[reason] = counts.get(reason, 0) + 1
    notes: list[str] = []
    for reason, n in sorted(counts.items()):
        if reason in ORG_SCOPED_STAGE_4_REASONS:
            notes.append(
                f"{COHORT_SHARED_STAGE} is unavailable for {n} org(s): {reason} "
                f"— this reason is a property of EACH named org (its memory may "
                f"live outside the org default graph, which this read does not "
                f"open), not of the shared analytics store; the orgs are named "
                f"in stages.{COHORT_SHARED_STAGE}.orgs_unavailable")
            continue
        notes.append(
            f"{COHORT_SHARED_STAGE} is unavailable for {n} org(s): {reason} — "
            f"this stage reads ONE shared analytics store, so it is a "
            f"server-level condition rather than a per-org read failure")
    return notes


def assemble_cohort(*, orgs: Sequence[str], payloads: Sequence[dict | None],
                    errors: Sequence[str | None] | None,
                    since: str, until: str,
                    limits: dict | None = None) -> dict:
    """Compose the cohort response: the roll-up plus its own provenance.

    The WINDOW is carried at the top level because the number is meaningless
    without it and the per-org payloads are not returned (they are N full
    scorecards — the operator reads one org through the org-scoped endpoint).
    ``cohort_definition`` and ``dogfood_only`` come from the roll-up, and the
    payload carries BOTH of the roll-up's standing ``note`` (a string: the
    one-line "no rate is derivable" disclaimer) and its own ``notes`` (a list of
    conditions observed on THIS request, as the org-scoped payload does) — the
    two are different kinds of statement, not a typo.
    """
    payload = roll_up_cohort(orgs, payloads, errors)
    payload.update({
        "surface": COHORT_SURFACE,
        "schema_version": SCHEMA_VERSION,
        "window": {"since": since, "until": until},
        "orgs_measured": len([p for p in payloads if p is not None]),
        "limits": dict(limits or {}),
        "limitations": list(COHORT_LIMITATIONS),
        "notes": shared_stage_notes(payloads),
    })
    assert_no_activation_claim(payload)
    return payload
