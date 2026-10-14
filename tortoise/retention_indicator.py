"""#3839 (lane B7) — the retention indicator: how many TEAMS keep using the
product at day 7 / 15 / 30 / 60 / 90.

THE DEFINITION (settled on #3839; encoded here so the code and the definition
cannot drift):

    A team is RETAINED AT DAY N (N in {7, 15, 30, 60, 90}) if it had at least
    one REAL USE on or after the calendar day that is N days after its start.

Four axes decide whether the number means anything, and each is stated rather
than assumed (the issue thread gives the reason for each):

===== ========================= ==========================================
axis  value                     why
===== ========================= ==========================================
start the date the team's       using *first capture* instead would count a
      account was PROVISIONED   team that signed up and never acted as "not
      (``organizations.created_at``)  started" — hiding the failure we most
                                want to see
use   a SUCCESSFUL CAPTURE or   must exclude logins, dashboard visits and
      a memory SEARCH/RECALL    failed attempts, or a team that logged in and
      that RETURNED something   got nothing counts as retained
day   UTC calendar days         otherwise a team active at 7pm local reads
      boundary                  differently by timezone
day N "on or after day N"       an exact-day reading gives 0% for any sparse
      (cumulative)              cadence; cumulative absorbs it
===== ========================= ==========================================

THE ACTIVITY PREDICATE, CONFIRMED AGAINST WHAT THE PRODUCT EMITS
----------------------------------------------------------------
The two legs are read from ``analytics_events`` (the append-only, team-indexed
stream), never from logins or receipt bookkeeping:

* **successful capture** — ``capture_graph_ops``, emitted once per GENUINE
  (non-replay) capture at the end of the hosted write path. A replay writes no
  new nodes and emits nothing, which is the behaviour this predicate wants: a
  replay is not a fresh use.
* **search / recall that returned something** — ``mcp_tool_call`` whose
  ``tool_name`` is in :data:`activation_scorecard.RETRIEVAL_TOOL_ALLOWLIST` and
  whose ``result_count`` is an integer ``>= 1``. The allowlist and the event
  name are IMPORTED from ``activation_scorecard`` on purpose: "a real use"
  must mean the same thing on the retention surface as it does on the
  activation scorecard, and a second copy is how the two silently diverge.

THE THREE HONEST CONSTRAINTS
----------------------------
1. **Day 60 and day 90 do not exist early.** At beta scale those readings only
   become available with time, so a mark whose day has not arrived is ``null``
   (NOT ``false``) and is excluded from its own denominator. The measurement is
   therefore computable INCREMENTALLY: a partial reading that is honest, rather
   than an all-or-nothing one.
2. **Sparse cadence is expected.** A weekly user shows as active at day 7 only
   if day 7 falls inside a use window; the cumulative definition absorbs this.
3. **The ``result_count`` prop is FORWARD-ONLY** (#3675, merged 2026-10-11). A
   retrieval row written before it carries no readable count, so a day whose
   ONLY candidate use is such a row cannot be classified. Such a day makes the
   activity set a LOWER BOUND, and a lower bound is not a count. The ambiguity
   is applied PER MARK and only where it is DECISIVE: a mark whose window the
   ambiguous day leaves unsettled reads ``null`` and is listed in
   ``unclassifiable_marks``, while a mark a definite use has already settled
   stays ``true``. The team stays ``measured`` (its row carries
   ``REASON_UNCLASSIFIABLE_USE_DAY``), its settled marks stay in their
   denominators, and the cell built from the unsettled marks reads
   ``partial`` — never a lower bound published as churn.

THIS IS AN AGGREGATION, NEVER A RETENTION WINDOW (binding guard D12-b)
---------------------------------------------------------------------
The word "retention" here means *team behaviour*; it does NOT mean a data
retention policy. #3839 must not smuggle a retention window in through the
metric — the owner's standing ruling is *no retention window of any length;
host until asked*. This module is READ-ONLY over existing rows: it defines no
window after which anything is deleted, pruned or purged (see
``AGGREGATION_ONLY`` and ``LIMITATIONS``). The guard is asserted by
``tests/test_retention_indicator.py``.

WHAT THE NUMBER CANNOT DO
-------------------------
No activity-based metric can separate a team that has CHURNED from one that is
merely BETWEEN PROJECTS. That distinction is the whole question for a
per-project tool, and only conversation answers it: the record must carry a
per-team churn DATE and REASON, captured by asking. The standard exit-survey
taxonomy is adopted as-is (:data:`CHURN_REASONS`, decision D11) and reported in
``definition.churn_reasons`` so the recording surface has one home for the
categories. This module does not invent churn from silence.
"""
from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from .activation_scorecard import (
    MCP_TELEMETRY_EVENT,
    RESULT_COUNT_PROP,
    RETRIEVAL_TOOL_ALLOWLIST,
    STATE_MEASURED,
    STATE_NOT_MEASURABLE,
    STATE_UNAVAILABLE,
)

SCHEMA_VERSION = 1
SURFACE = "retention"

#: A cell — or the whole surface — that measured SOME of its population while
#: part of it could not be read. Distinct from `unavailable`: the number exists
#: but a NAMED subset is missing, so it is a lower bound rather than a count.
#: The surface state is DERIVED from its members (never asserted by the
#: caller), so a `measured` surface cannot sit over all-`unavailable` cells —
#: nor over any cohort in which no cell produced a number: a consumer switching
#: on `state` is refused exactly when nothing was measured.
STATE_PARTIAL = "partial"

#: The day-N marks the indicator reports, in ascending order. PINNED as the
#: literal the issue names rather than derived, so adding or dropping a mark
#: is a visible decision rather than a silent change to the cohort shape.
RETENTION_MARKS: tuple[int, ...] = (7, 15, 30, 60, 90)

#: The bands whose DROP the indicator reports — the only signal a monotone
#: cumulative line can carry. ``Cumulative(A) >= Cumulative(B)`` for A < B by
#: construction, so the interesting quantity is how many teams were still
#: active at the left edge but had gone quiet by the right edge.
BAND_PAIRS: tuple[tuple[int, int], ...] = ((7, 15), (15, 30), (30, 60), (60, 90))

#: The trailing "still warm" window, and the window the distinct-active-days
#: histogram is read over.
TRAILING_ACTIVE_DAYS = 30
ACTIVITY_HISTOGRAM_DAYS = 90

#: The histogram buckets, in report order, as ``(label, low, high)`` with
#: ``high=None`` meaning open-ended. The issue names 1-2 / 3-9 / 10+; the ``0``
#: bucket is ADDED here so a churned team is not invisible in a chart that
#: exists to separate cadences (a bucket-less team would silently leave the
#: denominator). Its presence is stated in the payload's definition.
HISTOGRAM_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("0", 0, 0),
    ("1-2", 1, 2),
    ("3-9", 3, 9),
    ("10+", 10, None),
)

#: The successful-capture analytics event (``hosted_api._capture_session_impl``
#: emits it once per genuine capture).
CAPTURE_EVENT = "capture_graph_ops"

#: The standard churn-reason taxonomy adopted from exit-survey practice
#: (decision D11), recorded here as the ONE home for the category set the
#: recording surface must map onto.
CHURN_REASONS: tuple[str, ...] = (
    "price",
    "value",
    "competitor",
    "no-longer-needed",
    "onboarding-friction",
    "technical",
)

#: The D12-b guard, stated as a value the payload carries so a reader does not
#: have to infer the posture from prose.
AGGREGATION_ONLY = True

#: Per-team reasons that mean "this team's activity could not be established".
REASON_ANALYTICS_UNREACHABLE = "analytics_store_unreachable"
REASON_ANALYTICS_TRUNCATED = "analytics_walk_truncated"
REASON_ANALYTICS_SHAPE = "analytics_row_shape_unreadable"
REASON_UNPARSEABLE_CREATED_AT = "unparseable_created_at"
REASON_WINDOW_PREDICATE_NOT_APPLIED = "window_predicate_not_applied"
REASON_UNCLASSIFIABLE_USE_DAY = "unclassifiable_use_day_result_count_not_recorded"
REASON_START_UNREADABLE = "org_start_unreadable"

#: Whole-surface reason: a reading was taken but NOTHING in the fleet could be
#: measured, so no cell carries a number. `assemble` derives this from the
#: rows rather than trusting a caller to have noticed.
REASON_NO_TEAM_MEASURED = "no_team_could_be_measured"

#: Whole-surface reason: the teams WERE read, but no cohort cell could be
#: CLASSIFIED, so no number was produced anywhere. This is the forward-only
#: ``result_count`` case (#3675) taken to its limit — every mark left
#: unsettled by a pre-deploy retrieval row — and it must refuse rather than
#: publish `measured`, the inversion this surface exists to avoid.
REASON_NO_CELL_CLASSIFIED = "no_cohort_cell_could_be_classified"

#: Whole-surface reason: nothing failed, but no cell had anything to measure
#: (an empty fleet, or a fleet whose start has not arrived). Mirrors
#: ``activation_scorecard.roll_up_cohort``'s `no_org_has_a_measurable_value`
#: branch: an honest `not_measurable`, never a `measured` zero.
REASON_NO_CELL_MEASURABLE = "no_cohort_cell_has_a_measurable_value"

#: Whole-surface reasons.
REASON_WRITE_PATH_UNCONFIGURED = "analytics_write_path_unconfigured"
REASON_FLEET_INCOMPLETE = "fleet_enumeration_incomplete"
REASON_FLEET_FAILED = "fleet_enumeration_failed"

#: The 95% z for a two-sided Wilson score interval.
_WILSON_Z = 1.959963984540054

DEFINITION: dict[str, Any] = {
    "sentence": (
        "A team is retained at day N (N in 7/15/30/60/90) if it had at least "
        "one real use on or after the calendar day N days after its start."
    ),
    "start": "the date the team's account was provisioned "
             "(organizations.created_at), as a UTC calendar date",
    "real_use": f"one {CAPTURE_EVENT} (a genuine, non-replay capture) OR one "
                f"{MCP_TELEMETRY_EVENT} for a retrieval tool whose "
                f"{RESULT_COUNT_PROP} >= 1",
    "day_boundary": "UTC calendar days",
    "day_n_window": "cumulative — 'on or after day N', not 'exactly day N'",
    "denominator": (
        "every team whose `organizations` row is still enumerable (live OR "
        "soft-deleted/churned — the churned population is IN the denominator, "
        "not filtered out) whose start is at least N days before the reading, "
        "whether or not it is active. A team hard-purged after the restore "
        "grace window has no recoverable start and is NOT enumerable; that "
        "residual is named in `limitations` and makes the denominator a "
        "lower bound over every team that ever signed up"
    ),
    "retrieval_tools": sorted(RETRIEVAL_TOOL_ALLOWLIST),
    "marks": list(RETENTION_MARKS),
    "mark_kind": {
        str(n): ("activation" if n < 30 else "retention")
        for n in RETENTION_MARKS
    },
    "bands": [f"{a}->{b}" for a, b in BAND_PAIRS],
    "trailing_active_days": TRAILING_ACTIVE_DAYS,
    "histogram_days": ACTIVITY_HISTOGRAM_DAYS,
    "histogram_buckets": [b[0] for b in HISTOGRAM_BUCKETS],
    "churn_reasons": list(CHURN_REASONS),
    "aggregation_only": AGGREGATION_ONLY,
}

LIMITATIONS: tuple[str, ...] = (
    "This is an AGGREGATION over existing rows, never a data-retention "
    "window (binding guard D12-b): no row is deleted, pruned or purged by "
    "this module at any age. The owner's standing ruling is 'no retention "
    "window of any length' — the word 'retention' here names team BEHAVIOUR.",
    "Day 60 and day 90 are unavailable until enough time has passed. A mark "
    "whose day has not arrived reads `null` and is excluded from its own "
    "denominator — it is NEVER folded in as a `false`.",
    "No activity metric can distinguish a team that has CHURNED from one "
    "merely BETWEEN PROJECTS. The number says THAT a team went quiet; only "
    "asking says WHY. Until a per-team churn date and reason "
    f"({', '.join(CHURN_REASONS)}) is actually recorded, a reading must not "
    "be interpreted as churn. The recording surface does not exist yet "
    "(#8637, a #3839 companion) — the taxonomy below is its category set.",
    "DENOMINATOR RESIDUAL — hard-purged teams are not enumerable. A churned "
    "team keeps its `organizations` row (with `deleted_at` stamped and its "
    "start date intact) through the restore grace window, and this read "
    "ENUMERATES it. After the window the row is hard-deleted "
    "(`purge_org_control_plane`) and the start date is unrecoverable; "
    "`analytics_events` survives the purge by design but carries no start, so "
    "the omission cannot be counted. The denominator is therefore a LOWER "
    "BOUND over every team that ever signed up, and the reading is biased "
    "UPWARD by exactly the purged-not-retained teams it cannot enumerate. Do "
    "not read it as an exact total-ever churn measure.",
    "The activity predicate's retrieval leg depends on the forward-only "
    f"`{RESULT_COUNT_PROP}` prop (#3675, shipped 2026-10-11). A retrieval row "
    "written before it carries no readable count, so a day whose only "
    "candidate use is such a row is UNCLASSIFIABLE. The ambiguity is applied "
    "PER MARK: a mark it leaves UNSETTLED reads `null` and is listed in the "
    "row's `unclassifiable_marks`; the team stays `measured` and its settled "
    "marks stay in their denominators, while a cohort cell built from the "
    "unsettled marks reads `partial`. A definite use is never turned into an "
    "unknown by an ambiguous day in the same window.",
    "The analytics read is bounded by a page walk that FAILS CLOSED: a walk "
    "that cannot be certified complete makes its team `unavailable` rather "
    "than publishing a lower bound as a count (#4038 parity).",
    "Only the two legs above count as use. Logins, dashboard visits, "
    "onboarding steps and FAILED captures are deliberately excluded — a team "
    "that logged in and got nothing is not retained.",
    "A `measured 0` is emitted only when the analytics store was reachable, "
    "the walk was certified complete, and the team genuinely had no "
    "qualifying day. Every other absence is `unavailable` with its reason.",
    "The chart is a rate over a SMALL cohort (n in the tens). It is reported "
    "as k/n plus a Wilson interval and must not be read as a precise point "
    "estimate, a trend between two readings, a population figure, or a claim "
    "of statistical significance: a beta self-selects, which biases retention "
    "UPWARD.",
    "D7 and D15 are ACTIVATION-class signals, not retention (#3839 "
    "correction 2). Retention language is only defensible from D30 onward: a "
    "day-7 or day-15 reading says a team came back once, not that it is "
    "retained, and reading the early marks as retention overstates what the "
    "data supports in the flattering direction. `definition.mark_kind` labels "
    "each mark activation (7/15) or retention (30/60/90).",
)


# ── pure helpers ───────────────────────────────────────────────────────────

def wilson_interval(k: int, n: int, z: float = _WILSON_Z
                    ) -> tuple[float | None, float | None]:
    """Two-sided Wilson score interval for ``k`` of ``n``, or ``(None, None)``.

    Standard practice for small samples: the naive normal interval can produce
    impossible bounds (below 0 or above 1) at the n this beta has. ``n == 0``
    is not a rate at all, so it returns ``(None, None)`` — the caller must not
    read that as a zero-width interval.
    """
    if n <= 0:
        return (None, None)
    k = max(0, min(int(k), int(n)))
    phat = k / n
    denom = 1.0 + (z * z) / n
    center = (phat + (z * z) / (2 * n)) / denom
    half = (z / denom) * math.sqrt((phat * (1 - phat)) / n + (z * z) / (4 * n * n))
    return (max(0.0, center - half), min(1.0, center + half))


def _as_utc(value: Any) -> datetime | None:
    """Coerce a stored timestamp to an aware UTC datetime, or ``None``.

    Mirrors ``activation_scorecard._coerce_created_at``: a naive value cannot
    be placed on the timeline without assuming a zone, and assuming is the bug
    this avoids; a ``bool`` is never a timestamp (it is an ``int`` subclass).
    """
    if isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo is not None else None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _utc_day(value: Any) -> date | None:
    """The UTC calendar day of a stored timestamp, or ``None``."""
    parsed = _as_utc(value)
    return parsed.date() if parsed is not None else None


def _row_props(row: Any) -> dict:
    """The ``properties`` mapping of an analytics row, or ``{}``.

    Defensive on purpose: a store or proxy that answers with a non-mapping
    ``properties`` must not become an ``AttributeError`` 500 — the row simply
    carries no readable props.

    BOTH stored shapes are read: the writer stores a jsonb object, but a store
    or proxy can hand the column back as a JSON-encoded STRING. That shape is
    measured live on this store and is parsed by
    ``activation_scorecard._row_props`` (pinned there by
    ``test_properties_as_a_json_string_is_counted``); ``raw_state`` and
    ``index_catalogue`` carry the same branch. Without it the retrieval leg is
    DEAD on that shape — every search row reads as unclassifiable while the
    module claims parity with the scorecard "so the two cannot disagree about
    what a use is" — which is exactly the disagreement it causes.
    """
    if not isinstance(row, dict):
        return {}
    props = row.get("properties")
    if isinstance(props, dict):
        return props
    if isinstance(props, str):
        try:
            parsed = json.loads(props)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def row_establishes_use(row: Any) -> bool | None:
    """Whether one analytics row is a qualifying REAL USE.

    Returns:
    * ``True``  — a genuine capture, or a retrieval that returned >= 1 item;
    * ``False`` — a row that is not a use (a retrieval that returned nothing,
                  or another event entirely);
    * ``None``  — a retrieval row whose ``result_count`` is unreadable, i.e.
                  the day it sits on cannot be classified (see LIMITATIONS).

    ``result_count`` is only read on retrieval rows: a capture row carries no
    such prop and its presence is itself the evidence.
    """
    if not isinstance(row, dict):
        return False
    name = row.get("event_name")
    if name == CAPTURE_EVENT:
        return True
    if name != MCP_TELEMETRY_EVENT:
        return False
    props = _row_props(row)
    tool = props.get("tool_name")
    if not isinstance(tool, str) or not tool:
        # No readable tool identity, INCLUDING the empty string. Parity with
        # ``activation_scorecard._row_tool_name``, whose "an empty one reads as
        # unreadable" rule maps ``""`` to ``None`` (and the row to
        # ``unclassifiable``): we cannot rule the row out as a retrieval call,
        # so its day is ambiguous rather than silently "not a use". Publishing
        # an empty name as a definite non-use would emit exactly the churn
        # reading the parity rule refuses and would drop a possibly-retained
        # team from a confident denominator.
        return None
    if tool not in RETRIEVAL_TOOL_ALLOWLIST:
        return False
    count = props.get(RESULT_COUNT_PROP)
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        # Absent, null, a non-int (an SDK failure the MCP seam could not
        # count), or a NEGATIVE count. The negative case is the parity rule:
        # ``activation_scorecard._count_returned`` treats ``count < 0`` as
        # UNCAPTABLE (its ``uncountable_result_count``), not as a zero — the
        # two surfaces import the same allowlist/prop on purpose "so the two
        # cannot disagree about what a use is". Publishing a negative as a
        # definite non-use would emit a churn reading the parity rule refuses
        # to make. Not a use we can evidence, and not a use we can rule out.
        return None
    return count >= 1


def fold_activity_days(rows: Iterable[Any], *, since_day: date, until_day: date
                       ) -> tuple[set[date], set[date], str | None]:
    """Fold analytics rows into ``(active_days, unclassifiable_days, reason)``.

    ``active_days`` is the set of UTC calendar days carrying at least one
    qualifying use, within ``[since_day, until_day]`` inclusive. Every returned
    timestamp is RE-CHECKED against the window in Python: a server-side
    predicate that did not apply returns out-of-window rows, and counting them
    would be a plausible wrong number — the failure mode is refused, not
    absorbed (parity with ``activation_scorecard.stage_counts``).

    ``reason`` is non-``None`` only when the fold FAILED: an unparseable
    timestamp, an out-of-window row (the predicate was absorbed), or a
    non-mapping row (a shape anomaly that must not be skipped silently).
    Unclassifiable retrieval rows are reported in the second set, NOT as a
    fold failure — the caller decides whether they matter for a given mark.
    """
    active: set[date] = set()
    unclassifiable: set[date] = set()
    for row in rows:
        if not isinstance(row, dict):
            # A store that answers with a non-mapping element is a shape
            # anomaly, not a row to skip: skipping it would under-count with
            # no signal, so the fold refuses.
            return set(), set(), REASON_ANALYTICS_SHAPE
        day = _utc_day(row.get("created_at"))
        if day is None:
            return set(), set(), REASON_UNPARSEABLE_CREATED_AT
        if not (since_day <= day <= until_day):
            return set(), set(), REASON_WINDOW_PREDICATE_NOT_APPLIED
        verdict = row_establishes_use(row)
        if verdict is True:
            active.add(day)
        elif verdict is None:
            unclassifiable.add(day)
    return active, unclassifiable, None


def active_at_day(start: date, active_days: set[date], n: int) -> bool:
    """Cumulative day-N activity: any active day on/after ``start + n``."""
    threshold = start + timedelta(days=n)
    return any(day >= threshold for day in active_days)


def mark_available(start: date, as_of: date, n: int) -> bool:
    """Whether day ``n`` for this team has arrived by ``as_of``."""
    return start + timedelta(days=n) <= as_of


def active_days_in_window(active_days: set[date], *, first: date, last: date
                          ) -> int:
    """Distinct active days within ``[first, last]`` inclusive."""
    return sum(1 for day in active_days if first <= day <= last)


def histogram_bucket(count: int) -> str:
    """The bucket label for a distinct-active-days count."""
    for label, low, high in HISTOGRAM_BUCKETS:
        if count >= low and (high is None or count <= high):
            return label
    return HISTOGRAM_BUCKETS[-1][0]


def team_row(*, org_id: str, start: str | None, state: str,
             reason: str | None,
             active_days: set[date] | None,
             ambiguous_days: set[date] | None = None,
             as_of: date) -> dict:
    """One team's row: start date plus the yes/no per mark.

    A mark that has not arrived yet is ``None`` (JSON ``null``), never
    ``False``; an ``unavailable`` team carries ``None`` for every mark — a
    ``false`` there would assert "not retained" with no evidence.

    ``ambiguous_days`` are days whose only candidate use is a retrieval row
    with no readable ``result_count`` (see LIMITATIONS). They are applied
    PER MARK, not to the whole row: a day-10 ambiguity cannot make the day-30
    mark unknown, so refusing the entire team over it would throw away
    measurements that are sound. An ambiguity makes a mark unknown only when
    it is the SOLE candidate evidence — the definition is a disjunction over
    days, so a definite use at/after day N settles the mark ``True`` whatever
    else the window holds. Only a mark with NO definite use at/after day N and
    at least one ambiguous day there is ``None`` and listed in
    ``unclassifiable_marks``. ``active_within_30`` is the same disjunction
    over the trailing window and follows the same rule; ``active_days_90`` is
    a COUNT, so it stays ``None`` whenever an ambiguous day falls in its
    window (the ambiguity changes the number, not just its classification).
    """
    row: dict[str, Any] = {
        "org_id": org_id,
        "start": start,
        "state": state,
        "reason": reason,
        "active_at": {str(n): None for n in RETENTION_MARKS},
        "unclassifiable_marks": [],
        "active_days_90": None,
        "active_within_30": None,
    }
    if state != STATE_MEASURED or active_days is None or start is None:
        return row
    start_dt = date.fromisoformat(start)
    amb = set(ambiguous_days or ())
    active_at: dict[str, bool | None] = {}
    unknown: list[str] = []
    for n in RETENTION_MARKS:
        if not mark_available(start_dt, as_of, n):
            active_at[str(n)] = None
            continue
        # The verdict is computed FIRST: the mark is a disjunction over days
        # ("any active day on/after day N"), so one definite use settles it
        # True even when an ambiguous day also falls in the window. Consulting
        # ambiguity first would return None over a settled True and make the
        # cumulative series non-monotone (active_at[N] is None while
        # active_at[M] for M > N is True). Ambiguity can only decide a mark
        # the definite evidence did NOT settle — i.e. the ambiguous day is the
        # sole candidate evidence for it.
        if active_at_day(start_dt, active_days, n):
            active_at[str(n)] = True
            continue
        if any(d >= start_dt + timedelta(days=n) for d in amb):
            active_at[str(n)] = None
            unknown.append(str(n))
            continue
        active_at[str(n)] = False
    row["active_at"] = active_at
    row["unclassifiable_marks"] = unknown
    first90 = as_of - timedelta(days=ACTIVITY_HISTOGRAM_DAYS - 1)
    first30 = as_of - timedelta(days=TRAILING_ACTIVE_DAYS - 1)
    w90_first = max(first90, start_dt)
    w30_first = max(first30, start_dt)
    # `active_days_90` is a COUNT, not a disjunction: an ambiguous day inside
    # the window genuinely changes the number, so the window stays
    # unclassifiable (`None`) whatever the definite days say.
    if any(w90_first <= d <= as_of for d in amb):
        row["active_days_90"] = None
    else:
        row["active_days_90"] = active_days_in_window(
            active_days, first=w90_first, last=as_of)
    # `active_within_30` is the SAME disjunction as a mark ("at least one
    # active day in the trailing window"), so the mark rule applies: a
    # definite use settles it `True`, and only an unsettled window is `None`.
    # Consulting the ambiguity first would report a provably-warm team as
    # unmeasured and drop it from the trailing denominator.
    if active_days_in_window(active_days, first=w30_first, last=as_of) > 0:
        row["active_within_30"] = True
    elif any(w30_first <= d <= as_of for d in amb):
        row["active_within_30"] = None
    else:
        row["active_within_30"] = False
    return row


def _cell(k: int | None, n: int | None, *, unavailable: int = 0,
          reason: str | None = None) -> dict:
    """One cohort cell: k/n, rate and Wilson interval, or a refusal.

    ``k``/``n`` are ``None`` when nothing is measurable. A ``measured 0`` is
    only ever emitted with ``n > 0`` and ``unavailable == 0`` — a denominator
    with an unmeasured team behind it is ``partial``, never a confident rate.
    """
    if k is None or n is None or n <= 0:
        return {"state": STATE_NOT_MEASURABLE if unavailable == 0
                else STATE_UNAVAILABLE,
                "k": None, "n": None, "rate": None,
                "wilson_95": {"low": None, "high": None},
                "unmeasured_teams": unavailable, "reason": reason}
    low, high = wilson_interval(k, n)
    state = STATE_MEASURED if unavailable == 0 else "partial"
    return {"state": state, "k": int(k), "n": int(n), "rate": k / n,
            "wilson_95": {"low": low, "high": high},
            "unmeasured_teams": unavailable, "reason": reason}


def _unmeasured_teams(team_rows: Sequence[dict], predicate) -> int:
    """Teams that could not be measured AND satisfy ``predicate(row)``."""
    return sum(1 for t in team_rows
               if t["state"] != STATE_MEASURED and predicate(t))


def cohort_summary(team_rows: Sequence[dict], *, as_of: date) -> dict:
    """Fold the per-team rows into the primary reading, bands, trailing and
    histogram.

    DENOMINATOR RULE: a mark's denominator is every team that could be
    CLASSIFIED for it — its start is at least N days before ``as_of``, and its
    day-N window either carries a definite use or carries no ambiguous day.
    Teams that are not active are IN the denominator. Teams that failed to
    read, and teams whose day-N mark an ambiguous day left UNSETTLED, are
    excluded and counted in ``unmeasured_teams``, which makes the cell
    ``partial`` rather than a confident rate over a silently smaller set. A
    team whose day has NOT YET ARRIVED is a different case: it is too young to
    be classified at all, so it is in neither the denominator nor
    ``unmeasured_teams``, and its cell is ``not_measurable`` (never
    ``partial``).
    """
    measured = [t for t in team_rows if t["state"] == STATE_MEASURED]

    def _old_enough(t: dict, n: int) -> bool:
        return mark_available(date.fromisoformat(t["start"]), as_of, n)

    marks: dict[str, dict] = {}
    for n in RETENTION_MARKS:
        eligible = [t for t in measured if _old_enough(t, n)]
        values = [t["active_at"][str(n)] for t in eligible]
        classified = [v for v in values if v is not None]
        unknown = len(values) - len(classified)
        unavailable = _unmeasured_teams(
            team_rows, lambda t, _n=n: _old_enough(t, _n)) + unknown
        if classified:
            marks[str(n)] = _cell(
                sum(1 for v in classified if v), len(classified),
                unavailable=unavailable,
                reason=("some_teams_old_enough_for_this_mark_are_unmeasurable"
                        if unavailable else None))
        else:
            marks[str(n)] = _cell(
                None, None, unavailable=unavailable,
                reason=("no_team_could_be_classified_for_this_mark"
                        if unavailable else None))

    bands: dict[str, dict] = {}
    for a, b in BAND_PAIRS:
        eligible = [t for t in measured if _old_enough(t, b)]
        usable = [t for t in eligible
                  if t["active_at"][str(a)] is not None
                  and t["active_at"][str(b)] is not None]
        unavailable = _unmeasured_teams(
            team_rows, lambda t, _b=b: _old_enough(t, _b)
        ) + (len(eligible) - len(usable))
        if not usable:
            bands[f"{a}->{b}"] = {
                "state": (STATE_NOT_MEASURABLE if unavailable == 0
                          else STATE_UNAVAILABLE),
                "left_active": None, "right_active": None, "drop": None,
                "unmeasured_teams": unavailable,
                "reason": ("no_team_has_reached_this_band" if unavailable == 0
                           else "no_team_could_be_classified_for_this_band")}
            continue
        left = sum(1 for t in usable if t["active_at"][str(a)])
        right = sum(1 for t in usable if t["active_at"][str(b)])
        bands[f"{a}->{b}"] = {
            "state": STATE_MEASURED if unavailable == 0 else "partial",
            "left_active": left, "right_active": right,
            "drop": left - right,
            "unmeasured_teams": unavailable,
            "definition": (
                "teams still active at the left edge that had gone quiet by "
                "the right edge (cumulative_left - cumulative_right)"),
        }

    # Trailing and histogram share the same population rule: every MEASURED
    # team whose start has arrived, minus the ones whose window carries an
    # ambiguous day (they are counted in `unmeasured_teams`, not dropped).
    def started(t: dict) -> bool:
        return date.fromisoformat(t["start"]) <= as_of

    trailing_elig = [t for t in measured if started(t)]
    trailing_values = [t["active_within_30"] for t in trailing_elig]
    trailing_classified = [v for v in trailing_values if v is not None]
    trailing_unmeasured = _unmeasured_teams(team_rows, started) + (
        len(trailing_values) - len(trailing_classified))
    trailing = _cell(
        sum(1 for v in trailing_classified if v) if trailing_classified else None,
        len(trailing_classified) or None,
        unavailable=trailing_unmeasured,
        reason=("some_teams_are_unmeasurable" if trailing_unmeasured else None))
    trailing["definition"] = (
        "teams with at least one active day in the trailing "
        f"{TRAILING_ACTIVE_DAYS} UTC days; denominator = every measured team "
        "whose start has arrived (including teams that started inside the "
        "window)")

    hist = {label: 0 for label, _low, _high in HISTOGRAM_BUCKETS}
    hist_elig = [t for t in measured
                 if started(t) and t["active_days_90"] is not None]
    for t in hist_elig:
        hist[histogram_bucket(t["active_days_90"])] += 1
    hist_unmeasured = _unmeasured_teams(team_rows, started) + sum(
        1 for t in measured if started(t) and t["active_days_90"] is None)
    histogram = {
        # `_cell`'s rule applies here too: a denominator with an unmeasured
        # team behind it is `partial`, never a confident reading, and a
        # zero-denominator surface with a team behind it is `unavailable`
        # rather than `not_measurable` — the same two-way mapping `_cell`
        # performs. `n` and the buckets are computed over `hist_elig` alone,
        # so `state` is the only signal a consumer switching on it has that a
        # team is missing.
        "state": ("partial" if hist_elig and hist_unmeasured
                  else STATE_MEASURED if hist_elig
                  else STATE_UNAVAILABLE if hist_unmeasured
                  else STATE_NOT_MEASURABLE),
        "n": len(hist_elig) if hist_elig else None,
        "buckets": hist,
        "unmeasured_teams": hist_unmeasured,
        "definition": (
            "distinct active days in the last "
            f"{ACTIVITY_HISTOGRAM_DAYS} UTC days, clipped at the team's start; "
            "the `0` bucket is included so a team with no activity is not "
            "invisible in a chart meant to separate cadences"),
    }

    return {"marks": marks, "bands": bands, "trailing_30_active": trailing,
            "active_days_histogram": histogram}


def cohort_cell_states(cohort: dict | None) -> list[str]:
    """Every cohort sub-cell's state, in payload order.

    The ONE place the set of "cells" is enumerated, so the surface-state
    derivation below, the endpoint's note about the cells an unmeasured team
    feeds, and any other consumer cannot disagree about which cells exist.
    ``[]`` for a refused surface (``cohort is None``). The order is stable
    (marks, bands, trailing, histogram) but no caller may depend on it beyond
    iteration: this is a collection of states, not a positional contract.
    """
    if not cohort:
        return []
    states: list[str] = []
    for group in ("marks", "bands"):
        states.extend((cell or {}).get("state")
                      for cell in (cohort.get(group) or {}).values())
    states.extend((cohort.get(key) or {}).get("state")
                  for key in ("trailing_30_active", "active_days_histogram"))
    return [s for s in states if s is not None]


def _derive_surface_state(cohort: dict | None) -> tuple[str, str | None]:
    """The surface state the cohort's OWN cells support: ``(state, reason)``.

    MIRRORS ``activation_scorecard.roll_up_cohort``'s member-derived rule, and
    closes the one state inversion this surface exists to avoid (see
    ``STATE_PARTIAL``):

    * some cell carried a number, and no other cell is `partial`/`unavailable`
      -> ``measured`` (a `not_measurable` cell is NOT a gap: the comparable
      surface treats "the member answered, and there is nothing to measure" as
      compatible with a measured cohort);
    * some cell carried a number while another could not be read -> ``partial``;
    * NO cell carried a number and something was UNREADABLE -> ``unavailable``
      with ``REASON_NO_CELL_CLASSIFIED`` — the field a consumer switches on
      must never say "the number is here" when nothing was produced, which is
      exactly the all-marks-ambiguous (#3675) and all-teams-failed shapes;
    * NO cell carried a number and nothing was unreadable (an empty fleet, or a
      fleet whose start has not arrived) -> ``not_measurable``, never
      ``measured``: `roll_up_cohort`'s "every member said there is nothing to
      measure" branch.
    """
    states = cohort_cell_states(cohort)
    if any(s == STATE_PARTIAL for s in states):
        # A `partial` cell CARRIED a number over a named gap, so the surface
        # cannot honestly read `measured` over it (the original rule).
        return STATE_PARTIAL, None
    if any(s == STATE_MEASURED for s in states):
        if any(s == STATE_UNAVAILABLE for s in states):
            return STATE_PARTIAL, None
        return STATE_MEASURED, None
    if any(s == STATE_UNAVAILABLE for s in states):
        return STATE_UNAVAILABLE, REASON_NO_CELL_CLASSIFIED
    return STATE_NOT_MEASURABLE, REASON_NO_CELL_MEASURABLE


def assemble(*, as_of: date, team_rows: Sequence[dict],
             surface_state: str = STATE_MEASURED,
             surface_reason: str | None = None,
             limits: dict | None = None) -> dict:
    """Compose the retention payload from per-team rows.

    The surface state is DERIVED FROM ITS MEMBERS, never asserted (see
    ``_derive_surface_state``):

    * a reading that measured NOTHING is `unavailable` —
      ``REASON_NO_TEAM_MEASURED`` when no team could be read at all, and
      ``REASON_NO_CELL_CLASSIFIED`` when every team WAS read but no cell could
      be classified — even though the walk itself succeeded;
    * a reading that measured only part of the fleet is `partial`;
    * a reading in which nothing failed and nothing was measurable (an empty
      fleet, or a fleet whose start has not arrived) is `not_measurable`.

    A `measured` surface over a cohort that produced no number anywhere is the
    one state inversion this surface exists to avoid — the field a consumer
    switches on would say "the number is here" while no number was produced.
    `not_measurable`, `partial` and `unavailable` change which cells exist; a
    caller passing `surface_state` explicitly still OVERRIDES the derivation
    for a whole-read failure (fleet enumeration incomplete, analytics writer
    dark), in which case no cohort cell is computed and no rate is emitted. The
    per-team rows are still returned when they exist, each carrying its own
    state, so an operator sees WHAT was and was not measurable rather than a
    single blank.
    """
    measured_rows = [t for t in team_rows if t["state"] == STATE_MEASURED]
    state = surface_state
    reason = surface_reason
    cohort = (cohort_summary(team_rows, as_of=as_of)
              if surface_state == STATE_MEASURED else None)
    if surface_state == STATE_MEASURED:
        if team_rows and not measured_rows:
            # A whole-fleet analytics outage is not a measured reading of zero:
            # refuse the surface the same way a dark write path is refused, and
            # drop the cohort so no consumer can read an empty denominator as a
            # rate. Every team row still names its own failure.
            state = STATE_UNAVAILABLE
            reason = REASON_NO_TEAM_MEASURED
            cohort = None
        else:
            # The teams may all have been read and STILL have produced no
            # number anywhere (every mark left ambiguous by a pre-#3675
            # retrieval row). Derive the surface state from the cells, not
            # from the rows: `measured` may only sit over a cohort in which at
            # least one cell carries a number.
            derived, derived_reason = _derive_surface_state(cohort)
            if derived != STATE_MEASURED:
                state = derived
                reason = derived_reason
                if derived == STATE_UNAVAILABLE:
                    # Same refusal as the all-teams-failed arm above: a surface
                    # with no number anywhere is dropped, never published as an
                    # empty denominator.
                    cohort = None
    payload: dict[str, Any] = {
        "surface": SURFACE,
        "schema_version": SCHEMA_VERSION,
        "as_of": as_of.isoformat(),
        "state": state,
        "reason": reason,
        "definition": dict(DEFINITION),
        "definition_hash": _definition_hash(),
        "teams": list(team_rows),
        "teams_measured": len(measured_rows),
        "teams_total": len(team_rows),
        "cohort": cohort,
        "limits": dict(limits or {}),
        "limitations": list(LIMITATIONS),
        "notes": [],
    }
    return payload


def _definition_hash() -> str:
    """A stable digest of the definition, so a stored reading names its shape.

    The number is meaningless without the definition that produced it; a
    reading whose ``definition_hash`` differs from the current one is not
    comparable.
    """
    import hashlib
    blob = json.dumps(DEFINITION, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ── readers (fail-closed; every failure is a named reason) ─────────────────

def read_team_starts(cp) -> tuple[list[dict] | None, bool, str | None]:
    """Every enumerable org's ``(org_id, start)`` plus a completeness flag.

    Returns ``(rows, complete, reason)``. ``rows`` is ``None`` on a read
    failure, and ``complete`` is ``False`` when the walk could not be certified
    — either way the caller MUST NOT publish a denominator (a partial fleet is
    exactly the lower-bound-as-a-count defect #5388 fixed).

    THE WALK IS UNFILTERED ON PURPOSE (#3839 review P1): the denominator must
    include teams that CHURNED. ``soft_delete_org`` stamps
    ``organizations.deleted_at`` and keeps the row — and its ``created_at``
    start — through the restore grace window, so a ``deleted_at IS NULL``
    filter silently drops every churned team from every denominator, biasing
    the rate UPWARD (the direction the settled decision on #3839 warns
    against: *"all teams signed up >= N days ago, including churned"*). The
    row is enumerated whether or not ``deleted_at`` is stamped. Only a team
    hard-purged after the window leaves the set; that residual cannot be
    counted (no start survives) and is stated in ``LIMITATIONS``.

    ``start`` is the org's ``created_at`` as a UTC calendar date, from the
    control plane (`organizations`), which is the authoritative "account
    provisioned" fact. A row whose ``created_at`` cannot be placed on the
    timeline is reported through ``reason`` rather than dropped: dropping it
    would silently shrink the cohort.
    """
    try:
        rows, complete = cp.query_all(
            "organizations",
            select=["id", "created_at"],
            filters=None,
            cursor="id",
        )
    except Exception:
        return None, False, REASON_FLEET_FAILED
    teams: list[dict] = []
    for row in rows or ():
        if not isinstance(row, dict):
            return None, False, REASON_FLEET_FAILED
        org_id = row.get("id")
        if not org_id:
            continue
        day = _utc_day(row.get("created_at"))
        if day is None:
            return None, False, REASON_START_UNREADABLE
        teams.append({"org_id": str(org_id), "start": day})
    return teams, bool(complete), None


def read_org_activity(cp, org_id: str, *, since_day: date, until_day: date
                      ) -> tuple[set[date] | None, set[date], str | None]:
    """One team's qualifying activity days over ``[since_day, until_day]``.

    Two certified walks (the capture event, the retrieval event) filtered by
    org and window. Returns ``(active_days, unclassifiable_days, reason)``:
    ``active_days`` is ``None`` on any failure, with ``reason`` naming it. A
    walk that cannot be certified complete is a FAILURE
    (``analytics_walk_truncated``), never a shorter count (#4038 parity).
    """
    since_iso = datetime.combine(since_day, datetime.min.time(),
                                 tzinfo=UTC).isoformat()
    until_iso = datetime.combine(until_day + timedelta(days=1),
                                 datetime.min.time(), tzinfo=UTC).isoformat()
    active: set[date] = set()
    unclassifiable: set[date] = set()
    for event in (CAPTURE_EVENT, MCP_TELEMETRY_EVENT):
        try:
            rows, complete = cp.query_all(
                "analytics_events",
                select=["id", "event_name", "properties", "created_at"],
                filters=[("org_id", "eq", org_id),
                         ("event_name", "eq", event),
                         ("created_at", "gte", since_iso),
                         ("created_at", "lt", until_iso)],
                cursor="id",
            )
        except Exception:
            return None, set(), REASON_ANALYTICS_UNREACHABLE
        if not complete:
            return None, set(), REASON_ANALYTICS_TRUNCATED
        days, bad_days, reason = fold_activity_days(
            rows or (), since_day=since_day, until_day=until_day)
        if reason is not None:
            return None, set(), reason
        active |= days
        unclassifiable |= bad_days
    # A day carrying a qualifying use is classified regardless of an
    # unclassifiable row on the same day; only days left ambiguous count.
    unclassifiable -= active
    return active, unclassifiable, None


def build_team_rows(*, teams: Sequence[dict],
                    activity: dict[str, tuple[set[date] | None, set[date],
                                               str | None]],
                    as_of: date) -> list[dict]:
    """Turn the read results into per-team rows (pure; no I/O).

    A team is ``unavailable`` only when its activity read FAILED. An
    unclassifiable day does NOT refuse the whole team: it is passed through so
    each mark whose window contains it reads ``null`` while the marks that are
    unaffected stay measured (``team_row`` applies ambiguity per mark).
    """
    out: list[dict] = []
    for team in teams:
        org_id = team["org_id"]
        start: date = team["start"]
        days, ambiguous, reason = activity.get(
            org_id, (None, set(), REASON_ANALYTICS_UNREACHABLE))
        if days is None:
            out.append(team_row(org_id=org_id, start=start.isoformat(),
                                state=STATE_UNAVAILABLE, reason=reason,
                                active_days=None, as_of=as_of))
            continue
        out.append(team_row(org_id=org_id, start=start.isoformat(),
                            state=STATE_MEASURED,
                            reason=(REASON_UNCLASSIFIABLE_USE_DAY
                                    if ambiguous else None),
                            active_days=days, ambiguous_days=ambiguous,
                            as_of=as_of))
    return out
