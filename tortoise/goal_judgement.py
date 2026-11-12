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

Two shapes, and the split between them is the design of record — PR **#11923**
(``docs/plans/2026-11-11-goal-measurement-and-reasoning.md``), **unmerged at this
head**, so the §-references below are to that PR's copy; the decisions they record
are on #7860, which is where a reader without that branch should look (§5 is the
"declared decision, engine-owned effect" ruling):

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
    ... after a flip to Beta(1,9) -> judgement 0.100, goal 0.350

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
and ``unmet`` — carries no direction at all. Measured on this module's own shape
(embedded lane, 2026-11-11)::

    symmetric prior  Beta(9,1) -> Beta(9,1) :  goal 0.650 -> 0.650  (no change)  ⛔
    support prior    Beta(9,1) -> Beta(1,9) :  goal 0.650 -> 0.350  (-0.300)     ✅

The left line is a graph that discards the direction of what it measured: the
reading that BROKE the goal reaches it as exactly as much support as the reading
that satisfied it. (With other evidence wired into the same goal the 0.9 would
pull it toward "met" — an inference from the same arithmetic, not a separate
measurement.) :func:`prior_for` therefore derives both directions from ONE
strength number (``met`` → support ``s``, ``unmet`` → ``1 - s``, ``unknown`` → no
information), so the two can never drift.

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

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .live import TERMINAL_EXCLUDED_STATUSES as _TERMINAL_EXCLUDED_STATUSES

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
    ``unmet`` left the goal at **0.650 → 0.650 — no transmission at all**,
    because an `unmet` judgement believed 0.9 supports the goal at 0.9, exactly
    as a `met` one does. With this mapping the same flip moves it 0.650 → 0.350.
    A judgement point whose belief means "confidence in the word" is a graph
    that looks wired and discards the direction of what it measured.
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


def _declared_dwell(raw: Any) -> Any:
    """A declared ``dwell``, handed on unchanged unless it is an exact integer.

    ``None`` (absent) means 1. Anything DECLARED is passed through so
    ``Target.__post_init__`` can refuse what it cannot honour: ``1.9`` must raise,
    not be truncated to 1 — a target whose author declared a 1.9-reading window
    would otherwise be judged against a SMALLER bar than it states (measured:
    ``from_node({..., 'dwell': 1.9})`` returned 1 while
    ``Target(dwell=1.9)`` refused). The string spellings are still tolerated
    (``"2"`` → 2), because a YAML/props round-trip can hand one over.
    """
    if raw is None:
        return 1
    number = _num(raw)
    if number is not None and float(number).is_integer():
        return int(number)
    return raw


def _assert_supersession_scope(
    sdk: Any, previous_point_id: str, goal_id: str,
    indicator_id: str | None, target_id: str | None,
) -> None:
    """Refuse to supersede a claim that belongs to another chain.

    ``previous_point_id`` is the one value that decides WHAT gets retired, and the
    supersession TRANSFERS its operator edges onto the successor (#6180). A
    predecessor from another goal would therefore be retired (leaving that goal
    with NO live judgement) while its arrow was re-pointed onto this chain, so a
    claim scoped to goal B ends up driving goal A's belief. MEASURED through the
    seam: goal A ended with zero live judgements and its belief still moving.
    """
    node = sdk.get_entity(previous_point_id) or {}
    if not node:
        # ⛔ A MISSING predecessor is not a FOREIGN one, and the distinction is
        # what a caller acts on: `get_entity` answers `{}` for an id no node
        # backs, so folding it into the `judgementState` refusal below told a
        # caller with a typo'd or stale id that the point EXISTS and is merely
        # not a judgement — a false statement about the graph, pointing the
        # investigation at the wrong thing. Pre-diff this input fell through to
        # `supersede_point`, which named the missing point.
        raise ValueError(
            f"refusing to supersede {previous_point_id!r}: no entity has that id. "
            "A supersession transfers the predecessor's operator edges (#6180), "
            "so a predecessor that does not exist can be neither scoped nor "
            "retired."
        )
    if node.get("judgementState") is None:
        # ⛔ A predecessor this module does not own has NO scope props to compare,
        # so an equality check that tolerates absence is a no-op exactly where it
        # matters most. MEASURED: a foreign `derived` Point wired into goal B with
        # no `goalId` was superseded silently, its operator TRANSFERRED (#6180),
        # and goal B's belief went 0.0 -> 0.6502 on a judgement that belongs to
        # goal A — while B's claim was retired with no successor. That is the
        # failure this guard exists to prevent, so a predecessor must BE a
        # judgement before its scope can be compared at all.
        raise ValueError(
            f"refusing to supersede {previous_point_id!r}: it carries no "
            "`judgementState`, so it is not a judgement this module can scope. A "
            "supersession transfers the predecessor's operator edges (#6180), so "
            "retiring a foreign claim would re-point ITS belief onto this chain."
        )
    for key, want in (("goalId", goal_id), ("indicatorId", indicator_id),
                      ("targetId", target_id)):
        # ⛔ Presence AND equality, in BOTH directions, with ONE named exception
        # — an ABSENT `indicatorId`. `want is not None and have is not None and
        # have != want` was an escape that let `indicator_id=None` skip the check
        # entirely (measured: a SECOND live claim and a doubled operator count),
        # so `have != want` is the rule.
        #
        # ⛔ BUT `have is None` on `indicatorId` is NOT "another chain": it is a
        # claim written by the module that shipped in #11942, where
        # `indicator_id` defaulted to `None` and the writer stored the prop only
        # for a truthy id. Such a claim carries `judgementState`/`goalId`/
        # `targetId` and no `indicatorId`, and the DERIVED lookup can never see
        # it — `latest_judgement` adds `p.indicatorId = $indicatorId` to its WHERE
        # clause only when the argument is not `None`, and `indicator_id` is now
        # REQUIRED, so the clause is always present and a Point carrying no
        # `indicatorId` property fails it (Cypher: a null property never equals a
        # value). That leaves THIS seam as the only path
        # that can retire it, and refusing it made a goal that ever received a
        # pre-#11965 judgement PERMANENTLY un-writable through `record_judgement`
        # while the double-count behind it could not be repaired either.
        # MEASURED at the pre-fix head: two live `met` claims and two IMPL
        # arrows into one goal, and every repair call raised. Retiring ONE claim
        # of the same goal+target — the supersession transfers its arrow, #6180 —
        # is exactly the collapse this module exists to produce.
        #
        # `goalId` and `targetId` stay STRICT: this module has ALWAYS written
        # them, so their absence means the Point is not a judgement of this chain
        # at all. The tolerance is one named dimension, never a blanket rule —
        # tolerating `goalId` would re-open the cross-goal transfer (#6180) this
        # guard exists to stop.
        have = node.get(key)
        if have is None and key == "indicatorId":
            continue
        if have != want:
            raise ValueError(
                f"refusing to supersede {previous_point_id!r}: its {key} is "
                f"{have!r} but this judgement is scoped to {want!r}. A "
                "supersession transfers the predecessor's operator edges, so a "
                "cross-scope predecessor would leave its own chain with no live "
                "judgement while driving this one's belief."
            )


def _num(value: Any) -> float | None:
    """``value`` as a float, or ``None`` when it is not a real number.

    ``bool`` is excluded deliberately: ``True`` is an ``int`` in Python and a
    reading of ``True`` against a threshold of ``0.5`` would "pass" — a class of
    accident a numeric comparator must not inherit.
    """
    if isinstance(value, bool) or value is None:
        return None
    out: float | None
    if isinstance(value, (int, float)):
        out = float(value)
    elif isinstance(value, str):
        try:
            out = float(value.strip())
        except (TypeError, ValueError):
            return None
    else:
        return None
    # ⛔ `nan`/`±inf` are NOT readings. `nan` arrives easily — a metric backend
    # emits a gap as NaN, and its string form is storable — and it breaks two
    # things at once, MEASURED: (a) `nan <= 10` is False, so the "could not tell"
    # case was judged `unmet` at Beta(1,9), i.e. the gap PUSHED BELIEF DOWN,
    # contradicting this module's own rule that an unreadable value is `unknown`;
    # (b) `record_judgement` then wrote `readingValue=nan`, which the store's
    # numeric-domain guard refuses, so EVERY later pass for that indicator raised
    # and no judgement could ever be minted again. The repo's numeric domain
    # refuses both, so this is the only place in the chain that could disagree
    # with it — and now it does not.
    if not math.isfinite(out):
        return None
    return out


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
            # ⛔ Do NOT coalesce a declared value with `or 1`: `dwell: 0` (or
            # `""`, or `False`) would silently become 1 — the target's author
            # tried to set a bar and got "flip on the first disagreeing reading"
            # with no error and no record. Absence is the ONLY default; anything
            # declared is handed to `__post_init__`, which refuses what it cannot
            # honour.
            dwell=_declared_dwell(node.get("dwell")),
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

    def __post_init__(self) -> None:
        # ⛔ THE VALUE IS NORMALISED HERE, so EVERY path agrees with `_num` — the
        # pure `compare()` included. MEASURED before this: `compare([Reading(value=True)],
        # Target("<=", 10.0))` returned `met`, because `True` is an `int` in Python
        # and only the graph loader applied `_num`; a stored `create_event(value=True)`
        # was therefore a numeric "measurement" on one path and a boolean on the
        # other. `_num` refuses bool, strings that are not numbers, and
        # non-finite values, so `available` is False for all of them.
        normalised = _num(self.value)
        if normalised != self.value:
            object.__setattr__(self, "value", normalised)

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

    "Live" is the SHARED terminal vocabulary (``live.TERMINAL_EXCLUDED_STATUSES``
    plus the legacy ``outdated`` flag)

    This is a read of the CLAIM, never of ``goalState``: the cache is downstream
    of this, and asking it what the state is would be the inversion this module
    exists to prevent.
    """
    clauses = ["p.judgementState IS NOT NULL",
               "(p.status IS NULL OR NOT (p.status IN $terminal))",
               "coalesce(p.outdated, false) = false"]
    params: dict[str, Any] = {"lim": 1,
                              "terminal": sorted(_TERMINAL_EXCLUDED_STATUSES)}
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

    The read window scales with the target: ``dwell`` is a count of consecutive
    READINGS, so a dwell above the default page would never be satisfiable (every
    pass would report ``dwell-holding`` and the goal would never flip).
    """
    readings = latest_readings(sdk, indicator_id, event_kind=event_kind,
                              limit=max(100, target.dwell))
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
    # "against", not the bare operator: `12 minutes <= 10` beside `unmet` reads
    # as an assertion that 12 <= 10. The claim states the state and the reading;
    # the target's comparator is quoted as the BAR, never as the result.
    return (
        f"{subject}: {judgement.state} — latest reading {reading.value:g}{unit} "
        f"against {target.comparator} {target.threshold:g} "
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


def _write_goal_state_cache(sdk: Any, goal_id: str, state: str) -> str | None:
    """Write the goal's ``goalState`` CACHE from the judgement, or nothing.

    ``unknown`` writes NOTHING (:data:`GOAL_STATE_CACHE`) rather than putting
    ``active`` over a human's ``blocked``/``guideline``: a judgement that reaches
    no state cannot improve the cache, and the closed vocabulary has no word for
    "not decided". The write is unconditional (rather than "only if it differs")
    because checking would mean READING ``goalState`` in this module — the one
    thing the module exists not to do.
    """
    cached = GOAL_STATE_CACHE[state]
    if cached is not None:
        sdk.update_entity(goal_id, goalState=cached)
    return cached


def _ensure_judgement_edge(sdk: Any, point_id: str, goal_id: str) -> str | None:
    """Make sure ``point_id`` has AT LEAST ONE IMPL arrow into ``goal_id``.

    ⚠️ "At least one", not "exactly one": there is no operator-delete API, so an
    arrow another writer left (or a transferred one, #6180) cannot be removed
    here. What this guarantees is that the arrow EXISTS — the belief path is
    never missing — and it returns the first one found. A point that already
    carries several is reported through ``extraOperators`` in the write result
    rather than silently presented as healthy: the evidence is being counted more
    than once, and only the caller can decide whether to repair the graph.

    IDEMPOTENT ON PURPOSE. Two facts drive it:
      * ``supersede_point`` TRANSFERS the predecessor's operator edges onto the
        successor (#6180) — so after a flip the successor already carries the
        arrow and creating another would give the live judgement TWO operators
        for the same evidence. MEASURED on the five-pass sequence met, unmet,
        met, unmet, met (identical evidence bar the reading), goal belief per
        pass PRE-FIX: ``0.6502 -> 0.3080 -> 0.7197 -> 0.2600 -> 0.7556`` — a
        flapping metric HARDENED the goal it was supposed to be tracking, and the
        graph grew an operator Point + two edges per flip, never pruned. The same
        sequence POST-FIX reads ``0.6502 -> 0.3498 -> 0.6502 -> 0.3498 ->
        0.6502``.
      * a write that failed after its Point landed leaves a live judgement with
        no arrow at all — nothing else re-creates it, which is why this function
        checks for the missing edge on every pass (the unchanged arm calls it
        unconditionally).

    So the edge is (re)created only when the point has none INTO THIS GOAL — the
    check is goal-scoped, because "has an operator" is not the question: a point
    already wired into goal A and then recorded for goal B would otherwise be
    reported as wired while goal B's belief never moves (measured: `written:
    False`, an `operator_id` reaching goal A, and `MATCH (op)-[:IMPL]->(B)`
    empty). The result names the operator the goal's belief actually flows
    through.
    """
    rows = sdk._get_proj().g.query(
        "MATCH (p:Point {id:$i})-[:INPUT]->(op:Point)-[:IMPL]->(g) "
        "WHERE g.id = $goal RETURN op.id LIMIT 5",
        params={"i": point_id, "goal": goal_id},
    ).result_set
    if rows:
        return rows[0][0]
    operator = sdk.create_operator("IMPL", point_id, [goal_id], direction="->")
    return (operator or {}).get("id")


def _cache_then_arrow(
    sdk: Any, point_id: str, goal_id: str, state: str
) -> tuple[str | None, str | None]:
    """Write the ``goalState`` cache and the IMPL arrow — NEITHER suppressing the other.

    ORDER: the cache goes FIRST, and what that buys is bounded and exact — a
    failed ARROW leaves the cache already agreeing with the live claim (pinned by
    ``test_a_failed_arrow_leaves_the_cache_agreeing_with_the_claim``). It does NOT
    make a refused ``update_entity`` harmless: on a flip the supersession has
    already happened, so a refused cache write leaves ``goalState`` on the PREVIOUS
    word under a live claim whichever of the two writes goes first. That
    staleness is repaired on the next pass — the unchanged arm re-writes the cache
    unconditionally (``_write_goal_state_cache`` does not read-then-compare), so a
    failed CACHE and a failed ARROW both have a self-healing arm.

    ⛔ BUT THE ARROW IS WRITTEN EITHER WAY, and that is the half a plain reorder
    gets wrong: with the two writes merely SEQUENCED, a refused cache write skips
    ``_ensure_judgement_edge`` entirely, so the judgement is live with NO belief
    arrow (measured: with ``update_entity`` raising, the goal ended with ZERO IMPL
    arrows while a live ``met`` judgement existed). Ordering two writes must
    therefore not make one a precondition of the other: the cache refusal is
    captured, the arrow is written regardless, and only then is the original
    exception re-raised so the caller still sees it. The asymmetry that IS real is
    about what each write is FOR, not about which one self-heals: the cache is a
    word a reader sees, while an arrow is the belief path itself, and the two
    comments above say which failure each repair arm covers.

    Neither step has a compensating delete: the claim and its prior are correct,
    and removing them would destroy the judgement.
    """
    cache_exc: Exception | None = None
    cached: str | None = None
    try:
        cached = _write_goal_state_cache(sdk, goal_id, state)
    except Exception as exc:  # captured, never swallowed: re-raised below
        cache_exc = exc
    try:
        operator_id = _ensure_judgement_edge(sdk, point_id, goal_id)
    except Exception as arrow_exc:
        if cache_exc is not None:
            # BOTH writes failed. The arrow error is the more actionable one, but
            # the cache refusal came first; keep both traces on the way out.
            raise cache_exc from arrow_exc
        raise
    if cache_exc is not None:
        raise cache_exc
    return cached, operator_id


def _extra_operators(
    sdk: Any, point_id: str, goal_id: str, chosen: str | None
) -> list[str]:
    """The other IMPL operators this judgement has into ``goal_id``, if any."""
    rows = sdk._get_proj().g.query(
        "MATCH (p:Point {id:$i})-[:INPUT]->(op:Point)-[:IMPL]->(g) "
        "WHERE g.id = $goal RETURN op.id",
        params={"i": point_id, "goal": goal_id},
    ).result_set
    ids = [r[0] for r in rows]
    if chosen in ids:
        ids.remove(chosen)
    return ids


def record_judgement(
    sdk: Any,
    *,
    goal_id: str,
    judgement: Judgement,
    target: Target,
    indicator_id: str,
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
    # ⛔ VALIDATE AND DERIVE THE PRIOR BEFORE ANY WRITE. Both of these can
    # refuse, and a refusal that lands AFTER `create_point` leaves a live
    # `claimClass: 'derived'` Point carrying `judgementState`/`goalId` with no
    # prior and no IMPL arrow — measured: `latest_judgement` then returns the
    # orphan as the current state, the unchanged arm answers `written: False`
    # forever, and `compute_confidence()` raises `CalibrationError` for the whole
    # graph because the orphan is uncalibrated. A refused parameter must not be
    # able to brick the judgement (or the EP run that reads it).
    alpha, beta = prior_for(judgement.state, strength)

    # ⛔ The INDICATOR must be NAMED. It is the chain's identity, and the whole
    # point of this module is "indicator + target + reading -> a judgement": a
    # judgement that names neither indicator cannot be found by any scoped pass
    # (`latest_judgement` drops an undeclared dimension from its WHERE clause), so
    # it is PERMANENTLY un-supersedable — and two unscoped passes on one
    # goal+target supersede each other, because the scope guard's `None == None`
    # cannot tell "both absent" from "same chain". MEASURED, both: an unscoped
    # pass followed by a scoped one left 2 live judgements and 2 IMPL arrows with
    # goal belief 0.5 (the single-chain value is 0.3498), and two unscoped passes
    # on one target retired each other's live claim and transferred the arrow.
    # ⛔ TRUTHINESS, not identity. `is None` leaves the OTHER unscoped spelling
    # open: `""` is not `None`, so it passed this refusal, was then never written
    # (`if indicator_id:` below is truthiness) while the reader filtered on
    # `p.indicatorId = ""` (`if val is not None`) — and the next pass minted a
    # SECOND live judgement with no supersession, the exact double-count this
    # refusal exists to prevent. MEASURED at the head this was found on:
    # `record_judgement(..., indicator_id="")` returned `written=True`, stored no
    # `indicatorId`, and left 2 live judgements with `extraOperators: []`.
    # A name that is the empty string names nothing, so it is refused here too.
    if not indicator_id:
        raise ValueError(
            "record_judgement needs `indicator_id`: the indicator is the chain a "
            "judgement belongs to, and an unscoped judgement can neither be found "
            "by a scoped pass (so it can never be superseded) nor be told apart "
            "from another unscoped chain (so they would supersede each other). An "
            "empty or falsy id is no better than a missing one."
        )

    # ⛔ The target must be NAMED. `targetId` is what scopes the supersession
    # chain: with it absent, two targets on the same goal+indicator (D4 allows
    # n targets) both look up "the last judgement for this goal+indicator" and
    # supersede EACH OTHER's live claim, so the goal can hold only one live
    # judgement no matter how many targets it has.
    # ⛔ Truthiness for the same reason as `indicator_id` above: an empty
    # `target_id` would be dropped by the writer and filtered on by the reader,
    # which is the same silent second-live-claim shape.
    if not target.target_id:
        raise ValueError(
            "record_judgement needs a Target carrying `target_id` (build it with "
            "Target.from_node(<target Object>)). Without it the supersession "
            "chain is scoped by goal+indicator only, so a second target on the "
            "same pair would supersede this target's live judgement and the goal "
            "could never hold one judgement per target."
        )

    if judgement.state == "unknown":
        # ⛔ A MISSING READING IS NOT A STATE CHANGE. It mints no claim, retires
        # nothing and touches no cache: the last real measurement is still the
        # best available answer and its window stays open. Writing an `unknown`
        # judgement would instead (a) SUPERSEDE a live `met`, and (b) transmit
        # Beta(1,1) — 0.5 support — through IMPL, driving the goal to exactly
        # the belief of a graph nobody ever judged (measured: 0.6502 -> 0.5000
        # on one unreadable reading). That is the degradation the cache path
        # already refuses, so the claim path must refuse it too rather than be
        # the one place a telemetry gap moves belief.
        return {
            "written": False,
            # ONLY the live read: a caller-supplied `previous_point_id` may name a
            # claim that is already terminal, and echoing it as "the judgement's
            # point" would report a dead claim as the current state.
            "point_id": (latest_judgement(sdk, goal_id=goal_id,
                                          indicator_id=indicator_id,
                                          target_id=target.target_id) or {}).get("id"),
            "operator_id": None,
            "superseded_id": None,
            "goalState": None,
            "judgement": {"state": judgement.state, "reason": judgement.reason},
            "prior": None,
        }

    # ⛔ THE STORED CLAIM IS THE AUTHORITY, not the Judgement's self-report.
    # `judgement.previous_state` is the caller's word; the claim it is about to
    # re-own is fetched anyway, so the unchanged arm is taken only when the LIVE
    # claim already asserts this state. Taking the caller's word (measured) left a
    # live claim saying `unmet` while the call reported `written: False` for
    # `met` and overwrote the cache to contradict it — a state change that never
    # got recorded, reachable through the seam and a plain TOCTOU under
    # concurrency.
    prior_point = latest_judgement(sdk, goal_id=goal_id,
                                   indicator_id=indicator_id,
                                   target_id=target.target_id)
    if previous_point_id is None:
        previous_point_id = (prior_point or {}).get("id")
    # ⛔ THE SCOPE IS ASSERTED ON THE RESOLVED ID, WHICHEVER BRANCH PRODUCED IT.
    # With `indicator_id` and `target_id` both REQUIRED, the derived lookup is
    # fully scoped and this assert is belt-and-braces there (a predecessor it can
    # return already matches). It stays because it is the seam's ONLY guard, and
    # because "the guard runs whichever branch produced the id" is a property
    # worth holding by construction rather than by the argument above.
    # Asserting only in the explicit-seam branch left the DERIVED branch
    # unguarded, and `latest_judgement` DROPS a `None` dimension from its WHERE
    # clause — so a caller who omitted `indicator_id` had its previous judgement
    # taken from an indicator it never declared. MEASURED: the indicator-scoped
    # claim was superseded, its arrow transferred, the successor carried no
    # `indicatorId`, and no later scoped pass could ever see it again — 2 live
    # judgements in one goal, 2 IMPL arrows, the successor PERMANENTLY
    # un-supersedable, and `extraOperators` empty because it counts only the
    # successor's own edges.
    if previous_point_id is not None:
        _assert_supersession_scope(sdk, previous_point_id, goal_id,
                                   indicator_id, target.target_id)

    if (previous_point_id is not None
            and (prior_point or {}).get("id") == previous_point_id
            and (prior_point or {}).get("judgementState") == judgement.state):
        # An UNCHANGED state mints nothing (re-asserting "still met" every pass
        # would churn a supersession chain that says nothing), but it still owns
        # the cache and the wiring. Two things are therefore done on this arm:
        #   * `goalState` is refreshed — otherwise a hand-edited cache word
        #     contradicts the live claim FOREVER (no later pass would fix it,
        #     because the state never changes again);
        #   * the IMPL arrow is re-asserted when it is missing, which is the
        #     self-healing arm for a write that failed after its point landed.
        cached, wired = _cache_then_arrow(sdk, previous_point_id, goal_id,
                                          judgement.state)
        return {
            "written": False,
            "point_id": previous_point_id,
            "operator_id": wired,
            # Reported on THIS arm too: it is the arm that re-asserts the arrow,
            # so it is the one that meets a graph another writer left with two of
            # them, and a double-count collapsed to an empty list here would read
            # as healthy.
            "extraOperators": _extra_operators(sdk, previous_point_id, goal_id,
                                              wired),
            "superseded_id": None,
            "goalState": cached,
            "judgement": {"state": judgement.state, "reason": judgement.reason},
            # ⛔ `None`, not the would-be prior: this arm wrote NO baseline, and
            # a provenance consumer logging 0.9 for a claim that holds Beta(6,4)
            # records a number the graph does not hold.
            "prior": None,
        }

    # ⛔ The goal is validated HERE because the only step that would otherwise
    # refuse it is `create_operator`, which runs AFTER the point and its baseline
    # have landed — leaving a live `derived` claim with `goalId` pointing at
    # nothing and no arrow (measured with a stale/typo'd id).
    if not (sdk.get_entity(goal_id) or {}):
        raise ValueError(
            f"goal {goal_id!r} does not exist — refusing to mint a judgement for "
            "a goal that cannot receive its belief arrow."
        )

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

    # ⛔ THE TRAP: the prior is what makes EP move at all, and it is written
    # BEFORE any edge so a refused baseline cannot leave an operator wired to a
    # point that carries no belief — the "looks wired, computes nothing" graph.
    # If it is refused anyway, the point is REMOVED rather than left behind:
    # nothing has been superseded at this step, so the deletion cannot orphan a
    # state.
    try:
        sdk.set_point_baseline(
            point_id, alpha, beta, source=JUDGEMENT_BASELINE_SOURCE,
        )
    except Exception as exc:
        # Best-effort and NON-MASKING: `delete_point` can itself raise
        # `WriteNotLandedError` when its journal append does not confirm, and a
        # delete failure must not replace the reason the baseline was refused
        # (it is chained instead, so both are visible).
        try:
            sdk.delete_point(point_id)
        except Exception as cleanup_exc:  # pragma: no cover - infra failure
            raise exc from cleanup_exc
        raise

    # The supersession TRANSFERS the predecessor's operator edges onto this
    # point (#6180), so creating one here too would give the live judgement TWO
    # operators for the same evidence — and the transfer MERGEs edges, not
    # operators, so it does not collapse them. MEASURED on the five-pass
    # sequence met, unmet, met, unmet, met, goal belief per pass PRE-FIX:
    # 0.6502 -> 0.3080 -> 0.7197 -> 0.2600 -> 0.7556 — a flapping metric HARDENED
    # the goal instead of tracking it (see this function's docstring for the
    # POST-FIX series). So the edge is created only when nothing was inherited.
    superseded_id = None
    window_from = "successor"
    if previous_point_id:
        # The window starts when the new fact became true — the reading's own
        # as-of instant (§4.7's contiguity intent: the old fact stops being true
        # exactly when the new one became true).
        #
        # An instant that PRECEDES the predecessor's stored `validFrom` is refused
        # by `supersede_point` as an inverted window (satisfiable by no query
        # instant), so the instant is dropped rather than the supersession lost —
        # the predecessor's own window end then comes from the successor's
        # `validFrom`/`createdAt`, and `windowFrom` in the result says which
        # happened. ⚠️ THIS WRITER DOES NOT STORE a `validFrom` on the claims it
        # mints (§4.7's closed-vs-half-open question is open at #7818), so the
        # refusal needs a predecessor whose window another writer recorded; that
        # is a real shape (an older graph, a repair, a script), which is why the
        # fallback is kept and pinned rather than deleted as unreachable.
        reading_at = (judgement.reading.started_at
                      if judgement.reading is not None else None)
        try:
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
        except Exception as exc:
            # ⛔ A REFUSED SUPERSESSION MUST NOT LEAVE A SECOND LIVE CLAIM. The
            # lifecycle / claim-class guards (#2498, #2351) raise BEFORE any
            # mutation, so removing the point this call just created restores the
            # graph: leaving it live would give one goal two live judgements (the
            # double-count this module exists to remove), and the next pass would
            # take the unchanged arm on the NEWEST live claim and wire the orphan
            # while the predecessor was never superseded.
            try:
                sdk.delete_point(point_id)
            except Exception as cleanup_exc:  # pragma: no cover - infra failure
                raise exc from cleanup_exc
            raise

    # The cache is written BEFORE the arrow, deliberately — but neither write is
    # a precondition of the other; see `_cache_then_arrow` for why the ordering
    # alone was not enough.
    cached, operator_id = _cache_then_arrow(sdk, point_id, goal_id,
                                            judgement.state)
    extra_operators = _extra_operators(sdk, point_id, goal_id, operator_id)

    return {
        "written": True,
        "point_id": point_id,
        "operator_id": operator_id,
        # ⚠️ More than one IMPL into this goal means the evidence is counted more
        # than once (another writer, a restored graph, a transferred arrow). It
        # cannot be repaired here — there is no operator-delete API — so it is
        # REPORTED instead of being presented as a healthy single arrow.
        "extraOperators": extra_operators,
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
