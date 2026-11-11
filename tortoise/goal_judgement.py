"""#11822 — the comparator, and the judgement it writes into the graph.

The cybernetic arrow that was missing. Four of the five arrows of the loop were
already measured working on public SDK calls only (reading arrives via
``create_event``; all four endpoint labels are admitted by ``create_operator``;
supra-goal belief is recomputed by EP); **the arrow that compares a number to a
threshold did not exist anywhere in ``tortoise/``** (#11822's own measurement).
This module is that arrow, and nothing else:

    readings + target {comparator, threshold, dwell}  ->  a judgement
    the judgement  ->  a `statement` Point, claimClass 'derived',
                       IMPL direction='->' into the goal

Two shapes, and the split between them is the design of record
(``docs/plans/2026-11-11-goal-measurement-and-reasoning.md`` §5, decided on
#7860):

* **the decision is DECLARED** — :data:`DECISION_TABLE` is a finite, total table
  of the (comparison, dwell, previous state) cases and the state each yields.
  It is enumerable and reviewable, and ``test_11822_goal_judgement.py`` proves it
  is COMPLETE (exactly one row matches every cell of the input product) — a
  hardcoded ``if`` comparator's *missing* case is invisible, which is why §5
  rejected option A.
* **the effect is ENGINE-OWNED** — what happens after a state is decided (the
  previous state stops counting, the new one counts with a window, belief
  re-weighs) is the same for every kind and lives in one place:
  :func:`record_judgement`. No kind declares it, and there is no general rule
  engine (§5's option C, rejected on the ECA literature's own recorded failure
  mode).

⛔ THE ONE TRAP — MEASURED, NOT THEORETICAL.
EP propagation silently NO-OPS unless the judgement's prior is written with
``set_point_baseline``. ``create_point(..., confidence=0.95)`` writes NO prior
at all (``confidence`` is not a belief prop on the create door), so the point
sits at Beta(1,1) and the whole chain computes nothing. Measured on this module's
own shape, embedded lane, 2026-11-11::

    confidence=0.95 only   -> judgement 0.500, goal 0.500, converged True, iters 1
    set_point_baseline(9,1) -> judgement 0.900, goal 0.650, converged True, iters 1
    ... after a flip to Beta(1,9) -> judgement 0.100, goal 0.308

The first line is the defect #11822 exists to remove: a graph that *looks*
wired, reports ``converged: True``, and moves nothing. :func:`record_judgement`
therefore **always** writes the baseline, and never accepts a ``confidence``
kwarg to do it with. ``tests/test_11822_goal_judgement.py`` pins both lines of
that measurement — the trap as a NEGATIVE case and the baseline as the positive
one — so a later "simplification" of this writer cannot quietly restore it.

⛔ THE SECOND TRAP — WHAT THE PRIOR MEANS (also measured).
A baseline alone is not enough: the NUMBER has to mean the right thing. EP's
``IMPL`` reads the SOURCE's belief as the degree of support it lends the TARGET,
so a prior that means *"confidence in the state word"* — 0.9 for BOTH ``met``
and ``unmet`` — transmits the wrong sign. Measured on this module's own shape
(embedded lane, 2026-11-11)::

    symmetric prior  Beta(9,1) -> Beta(9,1) :  goal 0.650 -> 0.692  (+0.042)  ⛔
    support prior    Beta(9,1) -> Beta(1,9) :  goal 0.650 -> 0.308  (-0.342)  ✅

The left line is a graph that argues the OPPOSITE of what it measured: the goal
went UP on the reading that broke it. :func:`prior_for` therefore derives both
directions from ONE strength number (``met`` → support ``s``, ``unmet`` →
``1 - s``, ``unknown`` → no information), so the two can never drift.

`goalState` IS A CACHE, NEVER AN INPUT TO BELIEF.
EP does not read ``goalState`` and must not learn to: it is a plain field on a
goal Object, while a goal Object carries no stored prior of its own
(``set_point_baseline`` is Point-only, #9063) — its ``confidence`` is only the
posterior EP writes from linked claims. Measured at this head: no belief path
reads ``goalState`` (``rg goalState tortoise/ep.py tortoise/live.py
tortoise/why.py`` is empty; the only reader is the write-door vocabulary
validator in ``sdk.py``). That inertness is not the bug to fix by teaching EP the
field — it is exactly *why* the judgement is a claim: a belief can be argued
with, a cached word cannot. :func:`record_judgement` writes ``goalState`` AFTER
the claim, as a display cache; :func:`judge` never reads it.

NO TRIGGER IS DECIDED HERE.
#7860's **D13 — "what triggers the loop?"** — is NOT ruled, and #11822's scope
waits on it (the design of record's §7 says so in the step-1 row). This module
therefore builds no trigger: nothing in it fires on a write, watches a graph, or
runs on a schedule. Every entry point is an explicit CALLER-invoked call. When
D13 is ruled, the chosen trigger calls :func:`judge_and_record`; until then, a
caller that wants a judgement asks for one.

NO PUBLIC SDK METHOD, AND NO MCP TOOL.
Adding either is a surface change that needs owner approval (AGENTS.md hard rule,
``config/surface-manifest.yml``). This module is a library — functions that take
an ``sdk`` — following ``tortoise/decide_wiring.py``'s precedent: the mechanism
gets one home, and the surface it is eventually exposed through is a separate,
approved decision.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "COMPARATORS",
    "DECISION_TABLE",
    "DEFAULT_STRENGTH",
    "GOAL_STATE_CACHE",
    "JUDGEMENT_STATES",
    "Judgement",
    "Reading",
    "Target",
    "compare",
    "compare_values",
    "judge",
    "judge_and_record",
    "latest_judgement",
    "latest_readings",
    "prior_for",
    "record_judgement",
]

#: The three states a comparison can yield. ``unknown`` is a real answer — a
#: target with no reading, or a reading whose value is not a number — and never a
#: silent ``unmet``: "the machine looked and could not tell" must not read as
#: "the goal failed".
JUDGEMENT_STATES = ("met", "unmet", "unknown")

#: The comparator spellings this table recognises, each mapped to the ONE
#: semantic comparison it means. The set converges with the two places in this
#: repo that already compare a measured value against a declared bar
#: (``tools/capstone_preregistration_check.py::COMPARATORS``, whose operator set
#: is ``<=``/``>=``/``<``/``>``/``=``/``≤``/``≥``; and
#: ``battery/config/thresholds.yaml``) rather than inventing a fourth dialect.
#: ``==``/``!=``/``≠`` are admitted as spellings of ``=``/``!=`` because a pack
#: author will write them, and silently judging on an unrecognised operator
#: would be the worst outcome available: a state computed from nothing.
COMPARATORS: Mapping[str, str] = {
    "<=": "<=",
    "≤": "<=",
    ">=": ">=",
    "≥": ">=",
    "<": "<",
    ">": ">",
    "=": "==",
    "==": "==",
    "!=": "!=",
    "≠": "!=",
}

#: How a judgement state caches onto the goal's ``goalState`` word (the closed
#: #7856 vocabulary). ⛔ ``unknown`` maps to ``None`` — NO cache write: the
#: vocabulary has no word for "not decided", and writing ``active`` over a
#: human's ``blocked`` or ``guideline`` because a reading went missing would let
#: the cache outrank a word nobody measured. :func:`record_judgement` reports
#: ``goalState: None`` in that case instead of guessing.
GOAL_STATE_CACHE: Mapping[str, str | None] = {
    "met": "met",
    "unmet": "unmet",
    "unknown": None,
}

#: The provenance token the judgement's prior is written with (#2199 family).
#: ``system-default`` because the number is applied by the MECHANISM, not stated
#: by a writer: ``set-by-author`` would claim an authored belief nobody holds.
#: Both tokens are frozen against source recomputation, so EP behaves identically
#: — the difference is only what the graph says about where the number came from.
JUDGEMENT_BASELINE_SOURCE = "system-default"

#: Default strength of a decided judgement: how much a `met` judgement SUPPORTS
#: the goal's achievement. ``unmet`` supports it at ``1 - strength``; ``unknown``
#: carries no information (Beta(1,1)). The Betas are derived from one number so
#: the two directions can never drift apart — see :func:`prior_for` and the
#: second trap in this module's docstring (#11822 measured both).
DEFAULT_STRENGTH = 0.9


def prior_for(state: str, strength: float = DEFAULT_STRENGTH) -> tuple[float, float]:
    """The Beta prior a judgement of ``state`` carries — the SUPPORT it gives the
    goal, not a credence in the state word.

    ⛔ This is not a stylistic choice; it is the difference between a loop that
    works and one that transmits the wrong sign. EP's ``IMPL`` reads the
    SOURCE's belief as the degree of support it lends the TARGET, so the
    judgement's prior has to mean *"how much does this judgement support 'the
    goal is achieved'"*:

    * ``met``     → Beta(9,1) = 0.90 support;
    * ``unmet``   → Beta(1,9) = 0.10 support;
    * ``unknown`` → Beta(1,1) = 0.50, i.e. no information either way.

    MEASURED on the symmetric alternative ("confidence in the state word": 0.9
    for BOTH ``met`` and ``unmet``), embedded lane, 2026-11-11: a flip to
    ``unmet`` moved the goal **0.650 → 0.692 — the WRONG WAY**, because an
    `unmet` judgement believed 0.9 supports the goal at 0.9. With this mapping
    the same flip moves it 0.650 → 0.308. A judgement point whose belief means
    "confidence in the word" is a graph that looks wired and argues the
    opposite of what it measured.
    """
    if state not in JUDGEMENT_STATES:
        raise ValueError(
            f"judgement state must be one of {JUDGEMENT_STATES}, got {state!r}"
        )
    if isinstance(strength, bool) or not isinstance(strength, (int, float)):
        raise ValueError(f"strength must be a number in (0.5, 1), got {strength!r}")
    # ⛔ The bound is HALF-OPEN below at 0.5 on purpose. The mapping is
    # `met -> s`, `unmet -> 1 - s`, so a strength at or under 0.5 would make a
    # `met` judgement REFUTE the goal — the sign inversion this function exists
    # to prevent, arriving through the parameter instead of through a symmetric
    # constant. A caller that wants a weaker voice passes e.g. 0.6, never 0.4.
    if not 0.5 < float(strength) < 1.0:
        raise ValueError(
            f"strength must be in (0.5, 1), got {strength!r} — at or below 0.5 a "
            "`met` judgement would stop supporting the goal and start refuting it."
        )
    s = float(strength)
    if state == "unknown":
        return (1.0, 1.0)
    support = s if state == "met" else 1.0 - s
    scale = 10.0
    # Rounded so the graph carries a readable prior: `(1 - 0.9) * 10` is
    # 0.9999999999999998 in binary floating point, and a Beta(9, 0.9999999999999998)
    # is the same belief with a number nobody can read back as intent.
    return (round(support * scale, 6), round((1.0 - support) * scale, 6))

#: The four rows of the decision table, as ``(reason, state, flipped)`` predicates
#: over :class:`_Inputs`. Order matters only for readability: the rows are
#: DISJOINT by construction, and the completeness test proves exactly one applies
#: to every cell of the input product.
_ROW_UNREADABLE = "reading-unavailable"
_ROW_UNCHANGED = "state-unchanged"
_ROW_FLIP = "state-flipped"
_ROW_DWELL = "dwell-holding"


def _num(value: Any) -> float | None:
    """``value`` as a float, or ``None`` when it is not a real number.

    ``bool`` is excluded deliberately: ``True`` is an ``int`` in Python and a
    reading of ``True`` against a threshold of ``0.5`` would "pass" — a class of
    accident a numeric comparator must not inherit.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except (TypeError, ValueError):
            return None
    return None


def canonical_comparator(comparator: str) -> str:
    """The canonical spelling of ``comparator``, or ``ValueError``.

    Fail-closed at the boundary on purpose: an unreadable operator is a WRITE
    error on the target, not a judgement. A comparator that silently degraded
    to a default would produce a state nobody declared.
    """
    try:
        return COMPARATORS[str(comparator).strip()]
    except KeyError:
        raise ValueError(
            f"Unknown comparator {comparator!r}. Declared comparators: "
            f"{sorted(COMPARATORS)} — a target must state a comparison this "
            "module can perform, and an unreadable one is refused rather than "
            "defaulted (a defaulted comparison is a judgement made by nobody)."
        ) from None


def compare_values(value: float, comparator: str, threshold: float) -> bool:
    """``value <comparator> threshold``, over the declared comparator set."""
    op = canonical_comparator(comparator)
    if op == "<=":
        return value <= threshold
    if op == ">=":
        return value >= threshold
    if op == "<":
        return value < threshold
    if op == ">":
        return value > threshold
    if op == "==":
        return value == threshold
    if op == "!=":
        return value != threshold
    raise AssertionError(  # pragma: no cover - COMPARATORS is closed
        f"canonical comparator {op!r} has no implementation"
    )


@dataclass(frozen=True)
class Target:
    """A declared target: ``comparator`` + ``threshold`` (+ ``dwell``).

    This is the *judging* half of a target Object (§6 of the design of record
    lists the full Layer-2 field set, of which only these three are used to
    produce a state). ``dwell`` is the number of most-recent consecutive readings
    that must agree before the state FLIPS — the SRE/Prometheus ``for:``
    discipline, counted in readings rather than wall-clock so the result is
    computable from the graph alone and testable without a clock. ``dwell=1``
    (the default) flips on the first reading that disagrees; a larger value makes
    a flapping indicator cost several consistent readings before the goal moves.
    """

    comparator: str
    threshold: float
    dwell: int = 1
    target_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "comparator", canonical_comparator(self.comparator))
        threshold = _num(self.threshold)
        if threshold is None:
            raise ValueError(
                f"Target threshold must be a real number, got {self.threshold!r} "
                "— a comparison against a non-number has no state."
            )
        object.__setattr__(self, "threshold", threshold)
        dwell = self.dwell
        if isinstance(dwell, bool) or not isinstance(dwell, int) or dwell < 1:
            raise ValueError(
                f"Target dwell must be an integer >= 1 (readings), got {dwell!r}."
            )

    @classmethod
    def from_node(cls, node: Mapping[str, Any]) -> Target:
        """Build from a ``target`` Object's properties (``comparator``,
        ``threshold``, ``dwell``). Missing ``comparator``/``threshold`` is a
        ``ValueError``: a target that declares no comparison is not a target this
        module can judge, and inventing one would be the hardcoded-comparator
        failure §5 rejected.
        """
        for key in ("comparator", "threshold"):
            if key not in node or node[key] is None:
                raise ValueError(
                    f"target {node.get('id') or node.get('name')!r} declares no "
                    f"{key!r} — a target must state its comparison (§6 of the "
                    "goal-measurement design of record)."
                )
        return cls(
            comparator=node["comparator"],
            threshold=node["threshold"],
            dwell=int(node.get("dwell") or 1),
            target_id=node.get("id"),
        )


@dataclass(frozen=True)
class Reading:
    """One measurement, as the graph holds it: value + unit + as-of.

    A reading's VALUE is never mutated and never superseded — a measurement is
    true for its moment (§4 of the design of record); a correction is a new
    reading. What this module does with a reading is READ it.
    """

    value: float | None
    raw_value: Any = None
    reading_id: str | None = None
    unit: str | None = None
    started_at: str | None = None

    @property
    def available(self) -> bool:
        return self.value is not None


@dataclass(frozen=True)
class Judgement:
    """The comparator's answer: a state, the row that produced it, and its inputs."""

    state: str
    reason: str
    reading: Reading | None = None
    target: Target | None = None
    comparison: bool | None = None
    dwell_satisfied: bool = False
    flipped: bool = False
    previous_state: str = "unknown"

    def __post_init__(self) -> None:
        if self.state not in JUDGEMENT_STATES:  # pragma: no cover - closed set
            raise AssertionError(f"undeclared judgement state {self.state!r}")


@dataclass(frozen=True)
class _Inputs:
    """One cell of the decision table's input space."""

    comparable: bool          # the latest reading carries a usable number
    candidate: str | None     # 'met' | 'unmet' when comparable, else None
    previous: str             # the state the last judgement reached
    dwell_satisfied: bool     # enough consecutive agreeing readings to FLIP


@dataclass(frozen=True)
class _Row:
    reason: str
    applies: Callable[[_Inputs], bool]
    outcome: Callable[[_Inputs], tuple[str, bool]]  # (state, flipped)


#: THE DECISION TABLE (§5's option B: declared, complete, reviewable).
#:
#: | # | reading        | candidate vs previous | dwell       | state    | flipped |
#: |---|----------------|-----------------------|-------------|----------|---------|
#: | 1 | unavailable    | —                     | —           | unknown  | no      |
#: | 2 | comparable     | equal                 | — (no flip) | candidate| no      |
#: | 3 | comparable     | differs               | satisfied   | candidate| YES     |
#: | 4 | comparable     | differs               | not satisfied| previous| no     |
#:
#: Row 1 covers "no usable reading" — which includes the malformed cell where a
#: caller claims a comparison without a candidate (``comparable`` true,
#: ``candidate`` ``None``). It answers ``unknown`` rather than assuming a
#: direction, so the table is total over the FULL product of its inputs, not over
#: a "declared" subset: there is no combination of the four inputs on which a
#: caller gets no answer or two.
#:
#: Row 2 is its own row rather than a case of row 3 because dwell is a gate on a
#: FLIP only: when the candidate already IS the state, nothing changes and dwell
#: has nothing to gate. That is the one subtlety in the table, and it is the
#: reason the table is written out instead of being four nested ``if``s: the
#: completeness test iterates the input product and asserts exactly one row
#: matches each cell, so an unhandled combination cannot hide.
DECISION_TABLE: tuple[_Row, ...] = (
    _Row(
        _ROW_UNREADABLE,
        lambda i: (not i.comparable) or i.candidate is None,
        lambda i: ("unknown", False),
    ),
    _Row(
        _ROW_UNCHANGED,
        lambda i: i.comparable and i.candidate is not None
        and i.candidate == i.previous,
        lambda i: (i.candidate, False),
    ),
    _Row(
        _ROW_FLIP,
        lambda i: i.comparable and i.candidate is not None
        and i.candidate != i.previous and i.dwell_satisfied,
        lambda i: (i.candidate, True),
    ),
    _Row(
        _ROW_DWELL,
        lambda i: i.comparable and i.candidate is not None
        and i.candidate != i.previous and not i.dwell_satisfied,
        lambda i: (i.previous, False),
    ),
)


def _dwell_satisfied(
    readings: Sequence[Reading], target: Target, candidate: str
) -> bool:
    """Have the last ``dwell`` readings all agreed with ``candidate``?

    ``readings`` is newest-first. A reading that is unavailable breaks the run —
    an unreadable measurement is not agreement.
    """
    if target.dwell <= 1:
        return True
    if len(readings) < target.dwell:
        return False
    want = candidate == "met"
    for r in readings[: target.dwell]:
        if not r.available:
            return False
        if compare_values(r.value, target.comparator, target.threshold) != want:
            return False
    return True


def compare(
    readings: Sequence[Reading],
    target: Target,
    *,
    previous_state: str = "unknown",
) -> Judgement:
    """``indicator`` readings + a ``target`` → a :class:`Judgement` (pure).

    No graph, no clock, no I/O: the decision table is a function of the newest
    reading, the target, and the state the last judgement reached. ``readings``
    is newest-first (as :func:`latest_readings` returns it).
    """
    if previous_state not in JUDGEMENT_STATES:
        raise ValueError(
            f"previous_state must be one of {JUDGEMENT_STATES}, got {previous_state!r}"
        )
    latest = readings[0] if readings else None
    candidate: str | None
    if latest is None or not latest.available:
        comparable, candidate = False, None
        dwell_ok = False
    else:
        comparable = True
        candidate = (
            "met"
            if compare_values(latest.value, target.comparator, target.threshold)
            else "unmet"
        )
        dwell_ok = _dwell_satisfied(readings, target, candidate)

    inputs = _Inputs(
        comparable=comparable,
        candidate=candidate,
        previous=previous_state,
        dwell_satisfied=dwell_ok,
    )
    matches = [row for row in DECISION_TABLE if row.applies(inputs)]
    if len(matches) != 1:  # pragma: no cover - completeness is tested
        raise AssertionError(
            f"decision table is not total: {len(matches)} rows matched {inputs!r}"
        )
    row = matches[0]
    state, flipped = row.outcome(inputs)
    return Judgement(
        state=state,
        reason=row.reason,
        reading=latest,
        target=target,
        comparison=(candidate == "met") if comparable else None,
        dwell_satisfied=dwell_ok,
        flipped=flipped,
        previous_state=previous_state,
    )


# ── graph reads ──────────────────────────────────────────────────────────────


def latest_readings(
    sdk: Any,
    indicator_id: str,
    *,
    event_kind: str | None = "measurement",
    limit: int = 100,
) -> list[Reading]:
    """The readings recorded about ``indicator_id``, newest first.

    A reading is an ``:Event`` wired ``(e)-[:aboutObject]->(indicator)`` — the
    #2782 value shape (a structural ``value`` + ``unit`` on the event, an
    ``startedAt`` as the as-of slot, ``get_events``' own ordering key). The
    event kind defaults to ``measurement`` but is a parameter because the
    vocabulary for a machine reading is a downstream wave (#11081), and this
    module must not become the thing that decides it.
    """
    order = "coalesce(e.startedAt, e.createdAt, '')"
    where = "MATCH (e:Event)-[:aboutObject]->(o) WHERE o.id = $indicator"
    params: dict[str, Any] = {"indicator": indicator_id, "lim": limit}
    if event_kind:
        where += " AND coalesce(e.eventKind, '') = $kind"
        params["kind"] = event_kind
    cypher = (
        f"{where} RETURN e.id, e.value, e.unit, e.startedAt, e.createdAt "
        f"ORDER BY {order} DESC, e.id DESC LIMIT $lim"
    )
    rows = sdk._get_proj().g.query(cypher, params=params).result_set
    out: list[Reading] = []
    for row in rows:
        raw = row[1] if len(row) > 1 else None
        out.append(
            Reading(
                value=_num(raw),
                raw_value=raw,
                reading_id=row[0] if row else None,
                unit=row[2] if len(row) > 2 else None,
                started_at=(row[3] if len(row) > 3 else None)
                or (row[4] if len(row) > 4 else None),
            )
        )
    return out


def latest_judgement(
    sdk: Any,
    *,
    goal_id: str | None = None,
    indicator_id: str | None = None,
    target_id: str | None = None,
) -> dict | None:
    """The newest LIVE judgement Point matching the given scope, or ``None``.

    "Live" excludes ``superseded`` — a flip retires the previous judgement, so
    the newest non-superseded point IS the current state. This is a read of the
    CLAIM, never of ``goalState``: the cache is downstream of this, and asking it
    what the state is would be the inversion this module exists to prevent.
    """
    clauses = ["p.judgementState IS NOT NULL",
               "coalesce(p.status, 'live') <> 'superseded'"]
    params: dict[str, Any] = {"lim": 1}
    for key, val in (("goalId", goal_id), ("indicatorId", indicator_id),
                     ("targetId", target_id)):
        if val is not None:
            clauses.append(f"p.{key} = ${key}")
            params[key] = val
    cypher = (
        "MATCH (p:Point) WHERE " + " AND ".join(clauses) +
        " RETURN properties(p) ORDER BY coalesce(p.evaluatedAt, p.createdAt, '') DESC,"
        " p.id DESC LIMIT $lim"
    )
    rows = sdk._get_proj().g.query(cypher, params=params).result_set
    return rows[0][0] if rows else None


def judge(
    sdk: Any,
    *,
    indicator_id: str,
    target: Target,
    goal_id: str | None = None,
    event_kind: str | None = "measurement",
) -> Judgement:
    """Read the latest readings + the previous judgement, then :func:`compare`.

    Reads only. Nothing is written, nothing is triggered — the write is a
    separate, explicit call (:func:`record_judgement`), which is what keeps #7860
    D13 ("what triggers the loop?") an open owner decision rather than something
    this module decided by shipping.

    ``goal_id`` scopes the PREVIOUS-judgement lookup and is not decorative: D4
    makes an indicator's targets 0..n and lets two goals share one indicator, so
    a lookup keyed on the indicator alone would let goal A's last state become
    goal B's dwell baseline. Omit it only where the indicator has one goal.
    """
    readings = latest_readings(sdk, indicator_id, event_kind=event_kind)
    previous = latest_judgement(sdk, goal_id=goal_id, indicator_id=indicator_id,
                               target_id=target.target_id)
    previous_state = (previous or {}).get("judgementState") or "unknown"
    if previous_state not in JUDGEMENT_STATES:
        previous_state = "unknown"
    return compare(readings, target, previous_state=previous_state)


# ── the effect: one writer, engine-owned ─────────────────────────────────────


def _judgement_content(
    subject: str, judgement: Judgement, target: Target
) -> str:
    """The claim's prose: the state, and the numbers that produced it.

    Stable and self-describing on purpose — a superseded judgement is read by a
    human asking "why did the goal flip?", and the answer has to be in the claim
    itself, not only in the reading it points at.
    """
    reading = judgement.reading
    if reading is None or not reading.available:
        return (
            f"{subject}: {judgement.state} — no usable reading against "
            f"{target.comparator} {target.threshold:g} "
            f"({judgement.reason})"
        )
    unit = f" {reading.unit}" if reading.unit else ""
    as_of = f" as of {reading.started_at}" if reading.started_at else ""
    return (
        f"{subject}: {judgement.state} — latest reading {reading.value:g}{unit} "
        f"{target.comparator} {target.threshold:g} "
        f"(dwell {target.dwell}, {judgement.reason}{as_of})"
    )


def _subject_for(sdk: Any, indicator_id: str | None, goal_id: str) -> str:
    for node_id in (indicator_id, goal_id):
        if not node_id:
            continue
        try:
            node = sdk.get_entity(node_id)
        except Exception:  # pragma: no cover - a missing node is not fatal here
            node = None
        if node and node.get("name"):
            return str(node["name"])
    return indicator_id or goal_id


def record_judgement(
    sdk: Any,
    *,
    goal_id: str,
    judgement: Judgement,
    target: Target,
    indicator_id: str | None = None,
    previous_point_id: str | None = None,
    subject: str | None = None,
    strength: float = DEFAULT_STRENGTH,
    evaluated_at: str | None = None,
) -> dict:
    """Write the judgement as a claim and let the engine take it from there.

    The effect, in one place, for every kind:

    1. a ``statement`` Point, ``claimClass: 'derived'`` — the state as a claim,
       so it can be argued with (that is the whole reason it is not a field);
    2. **``set_point_baseline``** — ⛔ non-negotiable: without it EP computes
       nothing (see the measurement at the head of this module). ``confidence=``
       is not a substitute and is deliberately not accepted here. The prior is
       :func:`prior_for`'s SUPPORT value for the state, never a symmetric
       "confidence in the word" (the second measured trap);
    3. ``IMPL direction='->'`` from the judgement into the goal — the arrow the
       cybernetic loop was missing;
    4. the previous judgement is SUPERSEDED (never mutated in place), with the
       window starting at the reading's own as-of instant when the reading
       carries one (§4: the old fact stops being true when the new one became
       true);
    5. ``goalState`` is refreshed as a CACHE of the claim — and not written at all
       when the judgement is ``unknown`` (:data:`GOAL_STATE_CACHE`).

    An UNCHANGED state writes nothing: re-asserting "still met" every pass would
    mint a supersession chain that says nothing and supersede a claim that is
    still true. ``{"written": False}`` says so explicitly.

    Returns a dict describing what landed: ``point_id``, ``superseded_id``,
    ``written``, ``goalState``, ``judgement`` (the state + reason), and the prior
    actually written.
    """
    if previous_point_id is None:
        prior_point = latest_judgement(sdk, goal_id=goal_id,
                                       indicator_id=indicator_id,
                                       target_id=target.target_id)
        previous_point_id = (prior_point or {}).get("id")

    if (previous_point_id is not None
            and judgement.state == judgement.previous_state
            and not judgement.flipped):
        return {
            "written": False,
            "point_id": previous_point_id,
            "superseded_id": None,
            "goalState": None,
            "judgement": {"state": judgement.state, "reason": judgement.reason},
            "prior": None,
        }

    text = _judgement_content(subject or _subject_for(sdk, indicator_id, goal_id),
                              judgement, target)
    props: dict[str, Any] = {
        "judgementState": judgement.state,
        "judgementReason": judgement.reason,
        "comparator": target.comparator,
        "threshold": target.threshold,
        "dwell": target.dwell,
        "goalId": goal_id,
        "evaluatedAt": evaluated_at or datetime.now(UTC).isoformat(),
    }
    if indicator_id:
        props["indicatorId"] = indicator_id
    if target.target_id:
        props["targetId"] = target.target_id
    if judgement.reading is not None and judgement.reading.reading_id:
        props["readingId"] = judgement.reading.reading_id
        if judgement.reading.value is not None:
            props["readingValue"] = judgement.reading.value

    point = sdk.create_point(
        "statement", text, claim_class="derived", status="live", **props
    )
    point_id = point["id"]

    # ⛔ THE TRAP: the prior is what makes EP move at all. Written BEFORE the
    # edge so a refused baseline cannot leave an operator wired to a point that
    # carries no belief — the "looks wired, computes nothing" graph.
    alpha, beta = prior_for(judgement.state, strength)
    sdk.set_point_baseline(
        point_id, alpha, beta, source=JUDGEMENT_BASELINE_SOURCE,
    )

    operator = sdk.create_operator("IMPL", point_id, [goal_id], direction="->")

    superseded_id = None
    window_from = "successor"
    if previous_point_id:
        # The window starts when the new fact became true — the reading's own
        # as-of instant. An out-of-order reading (older than the predecessor's
        # window start) is REFUSED by supersede_point as an inverted window, so
        # the reading instant is dropped rather than the supersession lost; the
        # fallback is the successor's own validFrom/createdAt, and the result
        # says which happened.
        reading_at = (judgement.reading.started_at
                      if judgement.reading is not None else None)
        if reading_at:
            try:
                sdk.supersede_point(previous_point_id, point_id,
                                    valid_from=str(reading_at))
                superseded_id, window_from = previous_point_id, "reading"
            except ValueError:
                sdk.supersede_point(previous_point_id, point_id)
                superseded_id = previous_point_id
        else:
            sdk.supersede_point(previous_point_id, point_id)
            superseded_id = previous_point_id

    cached = GOAL_STATE_CACHE[judgement.state]
    if cached is not None:
        sdk.update_entity(goal_id, goalState=cached)

    return {
        "written": True,
        "point_id": point_id,
        "operator_id": (operator or {}).get("id"),
        "superseded_id": superseded_id,
        "windowFrom": window_from,
        "goalState": cached,
        "judgement": {"state": judgement.state, "reason": judgement.reason,
                      "flipped": judgement.flipped},
        "prior": {"alpha": alpha, "beta": beta,
                  "source": JUDGEMENT_BASELINE_SOURCE,
                  "support": alpha / (alpha + beta)},
        "content": text,
    }


def judge_and_record(
    sdk: Any,
    *,
    goal_id: str,
    indicator_id: str,
    target: Target,
    event_kind: str | None = "measurement",
    strength: float = DEFAULT_STRENGTH,
    subject: str | None = None,
) -> dict:
    """Read, decide, and write — the one call a trigger (once #7860 D13 rules
    one) would make. Still explicit: nothing here fires on its own."""
    judgement = judge(sdk, indicator_id=indicator_id, target=target,
                      goal_id=goal_id, event_kind=event_kind)
    result = record_judgement(
        sdk, goal_id=goal_id, judgement=judgement, target=target,
        indicator_id=indicator_id, strength=strength, subject=subject,
    )
    result["judged"] = {
        "state": judgement.state,
        "reason": judgement.reason,
        "comparison": judgement.comparison,
        "dwellSatisfied": judgement.dwell_satisfied,
        "value": judgement.reading.value if judgement.reading else None,
        "readingId": judgement.reading.reading_id if judgement.reading else None,
    }
    return result
