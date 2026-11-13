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

THE OTHER HALF IS ``tortoise/derived_invalidation.py`` (#11926).
A judgement is a claim, and a claim whose justification is gone must stop
counting: that module retires it when the target's bar is revised, when the
definition changes, or when the goal is retired — one closed reason set, one
retire door, and no trigger of its own either (same D13 gate). Two things here
are read by it and are the reason they live HERE rather than there: the bar the
judgement was derived under (``comparator``/``threshold``/``dwell`` and, since
#11926, ``readingAsOf`` — all copied onto the claim by :func:`record_judgement`,
which is what makes a revision detectable on claims written before that module
existed), and the definition boundary (:data:`DEFINITION_BOUNDARY_PROP`, read by
:func:`judge` so a trend is never drawn across it).
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .live import TERMINAL_EXCLUDED_STATUSES as _TERMINAL_EXCLUDED_STATUSES

__all__ = [
    "BOUNDARY_UNREADABLE",
    "COMPARATORS",
    "DECISION_TABLE",
    "DEFAULT_STRENGTH",
    "DEFINITION_BOUNDARY_PROP",
    "GOAL_STATE_CACHE",
    "JUDGEMENT_STATES",
    "Judgement",
    "Reading",
    "Target",
    "compare",
    "compare_values",
    "definition_boundary",
    "instant_at_or_after",
    "judge",
    "judge_and_record",
    "latest_judgement",
    "latest_readings",
    "live_judgements",
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

#: The indicator property that MARKS the instant the definition changed (#11926,
#: capability 9 of the design of record).
#:
#: ⛔ The definition has no comparable VALUE on the node, so it cannot be diffed
#: the way a target's bar can (`comparator`/`threshold`/`dwell` are copied onto
#: every judgement — see ``record_judgement``). What CAN be compared is an
#: INSTANT: "readings before this moment were taken under an older definition of
#: what is being measured, so they are not comparable with readings after it".
#: That is why the boundary is a declared stamp rather than a digest of the
#: indicator's properties: a digest cannot say WHEN the meaning moved, so it
#: cannot bound the readings a trend may be drawn across, which is the half of
#: capability 9 that produces the wrong number rather than a stale one.
#:
#: It is written by ``derived_invalidation.mark_definition_change`` (one door,
#: explicit — nothing fires on its own; #7860's D13 "what triggers the loop?" is
#: still unruled) and read HERE, because the derivation is the consumer that must
#: not trend across it (:func:`judge`). ``derived_invalidation`` owns the
#: retirement of the states derived under the old definition.
DEFINITION_BOUNDARY_PROP = "definitionChangedAt"


class _BoundaryUnreadable:
    """The indicator's boundary could NOT be read — NOT the same fact as "none".

    ⛔ The distinction is the whole reason this sentinel exists. ``None`` already
    means "this indicator declares no boundary" — the state of every indicator
    today, which admits every reading. A FAILED READ is the opposite fact: the
    indicator may well declare one, so no reading can be shown to be comparable
    with the current definition. Folding the two made the derivation fail OPEN,
    which is the one direction it must never fail in: with the indicator read
    patched to raise, :func:`judge` served a PRE-boundary reading, judged it
    ``met``, and :func:`judge_and_record` minted that state and wrote it into the
    goal's ``goalState`` — a durable wrong word produced by a transient database
    hiccup. MEASURED on this lane (one reading at ``2026-11-11T10:00``, boundary
    ``2026-11-11T12:00``, only the indicator read failing)::

        healthy read   -> state='unknown'  reading=None             (nothing written)
        failed read    -> state='met'      reading='<pre-boundary>' (minted + cached)

    ``derived_invalidation`` deliberately does NOT consume this helper for the
    retirement decision (it reads the property off the node it has already
    fetched); this sentinel exists so the DERIVATION can tell the two facts
    apart too, instead of relying on that one caller getting it right.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "BOUNDARY_UNREADABLE"


#: The one instance. Identity-compared, never equality-compared.
BOUNDARY_UNREADABLE = _BoundaryUnreadable()


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
        # ⛔ Presence AND equality, in BOTH directions, with TWO named exceptions
        # — an ABSENT `indicatorId` or `targetId` (see below; those two, and only
        # those two). `want is not None and have is not None and
        # have != want` was an escape that let `indicator_id=None` skip the check
        # entirely (measured: a SECOND live claim and a doubled operator count),
        # so `have != want` is the rule.
        #
        # ⛔ BUT `have is None` on `indicatorId` is NOT "another chain": it is a
        # claim written by the module that shipped in #11942, where
        # `indicator_id` defaulted to `None` and the writer stored the prop only
        # for a truthy id. Such a claim carries `judgementState` and `goalId` —
        # and it may have no `targetId` either (see the second exception below) —
        # and the DERIVED lookup can never see
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
        # `goalId` stays STRICT: this module has ALWAYS written it (with
        # `goal_id` a required argument, unconditionally), so its absence means
        # the Point is not a judgement of this chain at all — and tolerating it
        # would re-open the cross-goal transfer (#6180) this guard exists to
        # stop.
        #
        # ⛔ `targetId` may ALSO be absent on a legacy claim, for the SAME reason
        # as `indicatorId` and by the same mechanism: the pre-#11965 writer's
        # DOOR checked `if target.target_id is None` while its WRITER checked
        # `if target.target_id:` — so `Target(..., target_id="")` passed the door
        # and the prop was dropped, minting a judgement with `judgementState` +
        # `goalId` and NO `targetId`. MEASURED on this head with such a claim:
        # invisible to the derived lookup (the `p.targetId = $targetId` clause is
        # always present now), the explicit seam REFUSED it, and the goal kept two
        # live claims and two IMPL arrows with belief 0.6920 against the
        # single-chain 0.6502 — the same duplicate-support defect, one dimension
        # over. The falsy spelling is closed on the WRITE side now; claims
        # already written are this seam's to retire.
        #
        # So the tolerance is TWO named dimensions, never a blanket rule, and
        # both are the ones whose pre-#11965 door was `is None` while its writer
        # was truthiness. `goalId` is the one that was never optional.
        have = node.get(key)
        if have is None and key in ("indicatorId", "targetId"):
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


def instant_at_or_after(instant: Any, boundary: Any) -> bool:
    """``instant`` names a moment at or after ``boundary`` — and both name one.

    ⛔ THE ANSWER IS ``False`` WHEN EITHER VALUE NAMES NO INSTANT, and that is
    the fail-closed direction rather than a detail: the two questions a caller
    asks of this predicate are "is this reading comparable with the current
    definition?" and "was this judgement derived before the definition moved?".
    An undated value cannot PROVE comparability, so it must not be treated as
    if it had — and 'unparseable' is a real stored state, not a theoretical one
    (``validity.end_state`` reports it as ``END_UNKNOWN`` for the same reason).

    The measure is ``search_engine._created_sort_key``, imported lazily (the
    same route ``validity._names_an_instant`` takes): it is the repo's ONE
    notion of "parseable instant", and comparing the raw strings instead would
    order ``...+00:00`` against ``...Z`` lexically — a bug that reads as correct
    whenever both sides happen to share a spelling.
    """
    from .search_engine import _created_sort_key
    a = _created_sort_key(instant)
    b = _created_sort_key(boundary)
    if a[0] != 0 or b[0] != 0:
        return False
    return a >= b


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
    #: The OLDEST reading instant the decision CONSUMED — set only when the
    #: ``dwell`` window decided it (the flip row), because that is the only row
    #: whose decision rests on more than the newest reading. ``None`` means "the
    #: newest reading is the whole decision", which is every other row.
    #:
    #: ⛔ IT EXISTS SO A DEFINITION BOUNDARY INSIDE THE WINDOW IS VISIBLE (#11926
    #: review cycle 5, MEASURED). A ``dwell=2`` judgement can flip on a window that
    #: STRADDLES the boundary — r1 under the old definition, r2 under the new — and
    #: the claim used to carry only the newest reading's instant, so the boundary
    #: was invisible to the invalidation and the goal kept a state whose
    #: justification was a trend drawn across two definitions (design of record §3
    #: row 9, surviving the module whose job is to detect it). The retirement half
    #: compares THIS instant with the boundary, so any consumed reading that
    #: predates it retires the claim.
    #:
    #: ⛔ AND IT IS ``None`` WHEN ANY READING IN THE WINDOW NAMES NO INSTANT — the
    #: whole window must be placeable for the oldest instant to mean anything.
    #: `started_at` falls back to `createdAt` and both can be absent
    #: (`create_event(..., startedAt=None)` stores neither), and such a reading is
    #: still `available`, so `_dwell_satisfied` counts it as agreement: taking the
    #: oldest DATED reading instead would silently UNDER-report how far back the
    #: window reached, and a boundary inside the real window would stay invisible
    #: (measured: review cycle 6). An unplaceable window is reported as exactly
    #: that by the retirement half, never as a narrower one.
    window_from: str | None = None

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
    # ⛔ THE WINDOW IS CONSUMED ONLY BY THE FLIP ROW. `state-unchanged` decides on
    # the newest reading alone (dwell gates a FLIP, nothing else — see the table),
    # and `dwell-holding` explicitly declines to flip. Reporting a window for those
    # rows would make the invalidation retire claims a single reading justified.
    window_from: str | None = None
    if flipped and target.dwell > 1:
        # `readings` is newest-first, so the OLDEST consumed reading closes the
        # slice. ⛔ The window is placed ONLY when EVERY reading in it names an
        # instant: skipping an undated one (rather than refusing to place the
        # window) reports a NARROWER window than the decision consumed, so a
        # boundary inside the real one stays invisible — silence in the direction
        # a retirement must never fail in.
        consumed = readings[: target.dwell]
        if all(r.started_at for r in consumed):
            window_from = str(consumed[-1].started_at)
    return Judgement(
        state=state,
        reason=row.reason,
        reading=latest,
        target=target,
        comparison=(candidate == "met") if comparable else None,
        dwell_satisfied=dwell_ok,
        flipped=flipped,
        previous_state=previous_state,
        window_from=window_from,
    )


# ── graph reads ──────────────────────────────────────────────────────────────


def definition_boundary(sdk: Any, indicator_id: str) -> Any:
    """The indicator's declared definition boundary — THREE-WAY, never two.

    :data:`BOUNDARY_UNREADABLE` when the indicator could not be read (or does not
    exist), ``None`` when it was read and declares no boundary, else the stamp as
    stored (never coerced to a datetime here — the ordering decision belongs to
    :func:`instant_at_or_after`, the one place that knows what an incomparable
    value means).

    ⛔ ``None`` IS NOT "WE COULD NOT TELL". It is "no boundary was ever
    declared", which is the state of EVERY indicator today: no reading is
    excluded on this ground and no judgement is retired for it. An ABSENT stamp
    is not evidence of a change, and inventing one would retire claims nobody
    changed. A FAILED READ is the opposite fact — the indicator may well declare
    one — and folding it into ``None`` made :func:`judge` trend across a
    definition boundary it could not see (see :data:`BOUNDARY_UNREADABLE` for the
    measurement). The derivation therefore answers ``unknown`` and writes
    nothing, which is #11822's own rule for a missing reading.

    ``derived_invalidation.invalidation_for`` still reads the property from the
    indicator node it has ALREADY fetched rather than calling this, for a second
    reason: a retirement is terminal, so it must also keep "the node is missing"
    distinct from "the property is absent" on the node it read.
    """
    if not indicator_id:
        return None
    try:
        node = sdk.get_entity(indicator_id)
    except Exception:
        return BOUNDARY_UNREADABLE
    if not node:
        # A missing node is a read we cannot make, not a boundary that is absent.
        return BOUNDARY_UNREADABLE
    return node.get(DEFINITION_BOUNDARY_PROP)


#: Rows per keyset page of the readings read. It must stay under whatever
#: ``RESULTSET_SIZE`` the server it is pointed at applies — and that is a
#: DEPLOYMENT choice, not an engine constant. The docker image this repo pins
#: declares ``FALKORDB_ARGS=… RESULTSET_SIZE 10000`` as an image ENV default
#: (``docker image inspect falkordb/falkordb-server:v4.20.6``), which is what a
#: container left on that default reports; a server whose args OMIT it — every
#: ``tools/test_lane.py`` container — reports ``-1`` = NO CAP (measured:
#: ``GRAPH.CONFIG GET RESULTSET_SIZE`` → ``-1``, and a 20 000-row ``UNWIND``
#: returns all 20 000 rows, while the same query on an image-default container
#: stops at 10 000). The local dev ``apps/graph-viz/docker-compose.yml:20`` pins
#: 10000 explicitly; the HOSTED deployment passes no pin of its own — the server
#: there is FalkorDB Cloud, reached through the ``FALKORDB_CLOUD_URI`` secret that
#: ``entrypoint.sh`` resolves into ``TORTOISE_DB_URI`` (``fly.toml`` leaves that
#: key empty on purpose) — and its effective value is NOT measured here. This
#: constant sits ten times under the 10000 the image-default container applies
#: (1000 vs 10000), and under no cap at all on a lane container (``-1``); the
#: local dev compose service pins the same 10000. ⛔ ONE unbounded
#: query is truncated BY THE SERVER in store order
#: and says nothing about it — #4515's silent-truncation class (an UNPAGED read
#: over ``RESULTSET_SIZE`` backs up silently partial and verifies GREEN), whose
#: remedy is
#: ``hosted_backup._paged_rows``. ⛔ A CAP PINNED BELOW THIS NUMBER IS **NOT**
#: SILENT HERE: :func:`latest_readings` compares the rows it collected against an
#: independent ``count(e)`` aggregate — the guard ``hosted_backup`` calls "the
#: load-bearing" one, and the only shape a result-set cap cannot truncate to a
#: lie, because it is ONE row — and RAISES rather than answering from the part it
#: managed to read. ⛔ THE LOSS IS ARBITRARY BY INSTANT, WHICH IS THE POINT, and
#: ``id(e)`` is CREATION order only while nothing has been deleted: the engine
#: REUSES a freed node id (MEASURED on a FRESH lane graph, `graph ver 42006`:
#: create A, B, C -> ids 0, 1, 2; delete the middle one and create D -> D takes
#: id 1, the id that was freed — so the ids are relative to what the graph
#: already held, and only the REUSE is the point). So a
#: truncated tail costs whatever currently holds the HIGHEST LIVE ids, not the
#: newest reading — and a backfilled row can be instant-OLD while landing last
#: (MEASURED: create the 12.0/newest-instant reading first, an older one second,
#: and truncation keeps the 12.0 while the guard reports the loss). The guard's
#: honest guarantee is therefore narrower than "nothing was lost": it fires when
#: the row COUNT comes back short of the count taken before the read (a create
#: that reuses an id already paged past leaves the count equal — a CHANGED set is
#: not a short one). It is fail-closed on "the answer would be taken from an
#: incomplete set", never on "the newest reading is certainly gone" — the two are
#: different claims and only the first is true.
_READINGS_PAGE_SIZE = 1000


def _newest_first_key(reading: Reading) -> tuple[int, Any, str]:
    """Sort key for :func:`latest_readings` — by real INSTANT, never by bytes.

    Total only when the third element differs (see "NOT TOTAL" below). It does
    for every reading written through :meth:`TortoiseSDK.create_event`, whose
    ``e.id`` is a non-empty opaque string, and distinct non-empty strings are
    distinct under ``str``. It does NOT for an event written outside that path
    with an ``id`` that is falsy, duplicated, or collides after ``str`` (``1``
    beside ``"1"``) — the section below enumerates the shapes this rule
    SUBSUMES, and the rule is what the scope is stated in rather than claimed.

    ⛔ THE STORED STRING IS NOT THE ORDER (#12322). A graph holds the same slot
    spelled ``Z``, ``+00:00``, date-only, and as the ingest producer's
    ``2026/11/12 (Thu) 01:00``; their byte order DISAGREES with their instant
    order — ``'…T05:00:00Z'`` sorts above ``'…T01:00:00-05:00'`` although the
    second is an hour LATER, and ``'/'`` (0x2F) sorts above ``'-'`` (0x2D) at
    index 4. Ordering a ``LIMIT``ed page by that string cropped the
    instant-newest reading out of the page a judgement is computed from, and
    ``compare()`` reported it as the current state: MEASURED at ``cd22562c24``,
    a ``>= 10`` target went ``unmet`` from a page whose newest row held 5.0 while
    the graph's newest reading held 12.0.

    The measure is ``search_engine._created_sort_key`` — the repo's ONE notion of
    "parseable instant" — imported lazily for the same reason
    :func:`instant_at_or_after` does it: this module must not add a second
    grammar. That key is ``(0, epoch)`` for a real instant and ``(1, string)``
    for an unparseable value; the flag returned here INVERTS the bucket so the
    caller can sort DESCENDING and still leave an undated reading LAST — an
    unparseable as-of cannot PROVE it is recent, so it must never displace a
    dated one. ``reading_id`` is the tie-break (the ``e.id DESC`` the query
    itself used to carry), so two reads of an unchanged graph agree.

    ⛔ NOT TOTAL, stated rather than implied — and stated as a RULE, because
    enumerating the shapes is how this claim stayed wrong through two rounds.
    The tie-break is ``str(reading_id or "")``, so the key is total exactly when
    that third element DIFFERS; when two same-instant readings produce the same
    string the sort falls back to a stable pass over ``rows``, i.e. ``id(e)``
    ASCENDING — the OPPOSITE direction to the ``e.id DESC`` the pre-fix query
    carried. ``str(id or "")`` is NOT injective on ids, and the reachable shapes
    include (measured, not imagined) two readings that both lack an ``id``, two
    distinct readings sharing one ``e.id`` — legitimate, and the reason no Python
    de-duplication is layered on this read at all (see the projection note below
    ``cypher``) — a stored ``id: 0`` beside one with no id (``0 or ""`` is
    ``""``), and ``1`` beside ``"1"``. The rule subsumes all of them.

    The result is still deterministic — two reads of an unchanged graph agree,
    which is the property that keeps a judgement reproducible — but it is NOT
    the documented direction, and that is the honest scope of the claim. It is
    left as prose rather than repaired in the key because the two rows name the
    SAME instant, so both orderings answer "newest first" equally: the defect is
    the DIRECTION claim, not a wrong answer. What actually blocks the repair is
    narrow and mechanical — ``Reading`` carries no field for ``id(e)``, so
    restoring the documented direction means adding one (a public dataclass
    field) plus its own pin. ⛔ It is NOT the v4/v6 ``id()`` hazard recorded on
    ``hosted_backup.py``: that note is about an OPERAND edge case (``id(n) > -1``
    returning no rows on v6) and says the mechanism there is "deliberately not
    guessed at", while this read already orders and pages on ``id(e)`` in Cypher
    (``ORDER BY id(e)``, ``id(e) >= $cursor``) — if integer ``id(e)`` ordering
    were unsafe across cores, the FETCH would already be broken, not just the
    tie-break.
    """
    from .search_engine import _created_sort_key

    bucket, measure = _created_sort_key(reading.started_at)
    return (1 if bucket == 0 else 0, measure, str(reading.reading_id or ""))


def latest_readings(
    sdk: Any,
    indicator_id: str,
    *,
    event_kind: str | None = "measurement",
    limit: int = 100,
    since: Any = None,
) -> list[Reading]:
    """Up to ``limit`` readings recorded about ``indicator_id``, newest first.

    ⛔ ``limit`` IS A CAP, NOT A COURTESY (#11823 / #12322). It is applied to the
    ORDERED list, so it keeps at most the ``limit`` most recent readings that
    survive ``since`` — AT MOST, not exactly: a declared boundary can leave fewer,
    and a caller reading a bare list cannot tell whether the bound was hit.
    A caller that needs every reading must pass a bound it knows exceeds it;
    the return shape carries no room for a ``truncated`` answer. A NEGATIVE bound
    is refused rather than sliced: ``out[:-1]`` would silently drop the OLDEST
    reading while reading as "everything", the same class of silent bound this
    function exists to remove.

    ⚠️ ``limit`` DOES NOT BOUND THE FETCH. The whole per-indicator history is
    paged and parsed regardless of ``limit``, because the ordering cannot be
    delegated to the server (see below) — so ``limit=1`` still costs ONE page
    query when there is nothing to read (the loop always asks once; an empty
    first page ends it) and ``ceil(N / _READINGS_PAGE_SIZE)`` page queries for
    ``N > 0``, ONE MORE when ``N`` is an exact multiple of the page size (a FULL
    page never ends the loop; only a short one does), PLUS the one aggregate
    ``count(e)`` the truncation guard compares against. Instant PARSES are ``M`` —
    the readings that SURVIVE ``since``, because the filter runs BEFORE the sort
    (``M = N`` when no boundary is given) — and ONLY WHEN A BOUNDARY IS DECLARED
    (``since is not None``) a further ``2N`` for it, because
    ``instant_at_or_after`` parses BOTH operands per reading, boundary included.
    The two cases therefore differ: **no boundary — ``M``, i.e. ``N``**, which is
    what ``judge()`` pays whenever ``definition_boundary`` returns ``None``; **a
    declared boundary — ``2N + M``**, which is ``3N`` when it drops nothing
    (measured: 5 readings parse 15 times at a boundary that drops none, 14 at one
    that drops one) and less when it drops more. Measured pin: 5
    readings at a page size of 2 take 3 page queries. That is the price of a
    correct order (:data:`_READINGS_PAGE_SIZE`); it is not a per-call constant.

    "Newest first" is ordered by the INSTANT each reading names, using the
    repo's one instant comparator (``search_engine._created_sort_key``) — never
    by the stored string, whose byte order disagrees with instant order across
    the spellings a graph actually holds. :func:`_newest_first_key` carries the
    measurement and the pathology. A reading that names no parseable instant
    sorts LAST, deterministically, because it cannot prove it is recent.

    A reading is an ``:Event`` wired ``(e)-[:aboutObject]->(indicator)`` — the
    #2782 value shape (a structural ``value`` + ``unit`` on the event, an
    ``startedAt`` as the as-of slot, ``get_events``' own ordering key). The
    event kind defaults to ``measurement`` but is a parameter because the
    vocabulary for a machine reading is a downstream wave (#11081), and this
    module must not become the thing that decides it.

    ⛔ THE RESULT IS ONE INDICATOR'S READINGS; NO PLAN ARM IS ANCHORED ON IT. The
    query is ``MATCH (e:Event)-[:aboutObject]->(o) WHERE o.id = $indicator``, and
    ``(o)`` carries no label, so ``o.id`` cannot be index-anchored. MEASURED on
    the lane with ``GRAPH.EXPLAIN`` (core ``42006``), the leaf depends on whether
    an ``eventKind`` predicate is present — and NONE of the three is a bounded
    subgraph:

    * PAGE arm, either way: ``Node By Label and ID Scan | (e:Event)`` — the
      ``id(e) >= $cursor`` predicate anchors the label, then ``o.id`` is filtered.
    * ``count(e)`` guard WITH a kind: ``Node By Label Scan | (e:Event)``.
    * ``count(e)`` guard WITHOUT a kind (``event_kind=None`` / ``""``):
      ``All Node Scan | (o)`` → ``Conditional Traverse | (o)->(e:Event)`` — it
      touches EVERY node in the graph, not only the ``:Event`` label.

    So every page scans the whole ``:Event`` label, and the guard scans either
    that label or the entire node set: the cost above is per PAGE, not per
    page-worth-of-rows, and an ``event_kind=None`` caller pays the worst arm.
    Fixing that needs an anchored pattern (or an index the schema cannot express
    for an unlabelled ``(o)``) and is filed as **#12351**; the correctness fix in
    this function does not depend on it.

    The rows are fetched by KEYSET PAGES on ``id(e)`` (see
    :data:`_READINGS_PAGE_SIZE`) rather than in one query, so a server-side
    result-set cap cannot silently truncate the set in store order (#4515; the
    page-size PRECONDITION is stated at the constant). Ordering
    happens in Python because no Cypher-computable instant key exists in this
    codebase today (measured: no ``startedAtEpoch``/``startedAtMs`` property, and
    no ``toInteger(substring(…))`` date arithmetic anywhere under ``tortoise/``),
    so ANY server-side ``ORDER BY … LIMIT`` is a byte-ordered cut. Writing such a
    key at ingest would store a HOST-DEPENDENT number for the producer's shape
    (#11646's residual) — a separate, still-open decision — so ordering here is
    the option that changes no stored byte.

    ⚠️ THE ORDER INHERITS ``_created_sort_key``'s HOST DEPENDENCE for a zone-less
    value. #3982 records that a date-TIME with no offset is read in the READER's
    zone (ECMA-262 §21.4.3.2) while a date-only value is pinned to UTC midnight,
    and #11646 measured the residual: the producer's ``2023/05/20 (Sat) 02:21``
    lands on ``1684529460`` / ``1684549260`` / ``1684574460`` under
    Asia/Kolkata / UTC / America/Los_Angeles — a **12.5 h** spread across those
    three zones (26 h for the full UTC−12…UTC+14 offset range) — so its position
    against a zoned row can differ between hosts. That divergence already existed
    on the ``since`` path (:func:`instant_at_or_after`); this read makes it
    visible in the ORDER too. It is a READ-time residual, not a stored byte: the
    anchor decision is the one #11646 was closed with UNRESOLVED, and writing an
    epoch at ingest would make the divergence durable instead of read-time.

    ``since`` EXCLUDES the readings taken before an instant — the definition
    boundary (#11926 capability 9). It is applied HERE rather than as a Cypher
    predicate on purpose: the stored ``startedAt`` spellings are not one format
    (``Z``, ``+00:00``, date-only, the producer's own), so a string comparison
    in the query would order them lexically and keep the wrong readings;
    :func:`instant_at_or_after` compares real instants. A reading that names no
    instant cannot PROVE it was taken after the boundary, so it is dropped — the
    fail-closed reading, because the alternative is to draw a trend across the
    boundary with a value nobody can date.

    ⚠️ AND A BOUNDARY THAT NAMES NO INSTANT EXCLUDES EVERY READING (the same
    predicate, applied to the same question: nothing can be shown to be after a
    moment nobody can place). That is the loud direction on purpose — the
    derivation answers ``unknown`` and the caller sees an indicator that stopped
    producing states, which is fixable; silently treating the stamp as absent
    would restart the trend across a definition change nobody can date.
    ``mark_definition_change`` refuses to WRITE such a stamp and repairs one it
    finds, so this state is reachable only through an outside write.

    Raises
    ------
    ValueError
        ``_READINGS_PAGE_SIZE < 1`` is a local constant an operator fixes, and it
        is refused BY NAME so they are told which constant; ``limit < 0`` is a
        caller error, refused so a negative bound cannot silently drop the OLDEST
        reading instead of capping the newest.
    RuntimeError
        The collected rows came back SHORT of the independent ``count(e)``. This
        is the fail-closed arm: the read refuses rather than answering from an
        incomplete set. The message enumerates all five causes and states the
        action each takes, and it is the authority — lowering
        ``_READINGS_PAGE_SIZE`` under a pinned ``RESULTSET_SIZE``; a re-run for a
        set that SHRANK (a concurrent DELETE, or an UPDATE that moves a row out of
        the predicate); OBSERVING rather than retrying when the indicator itself
        went away or was re-keyed, because a re-run does not raise there — the
        count is 0 too, so it answers an EMPTY reading set about a node that is no
        longer the one asked for; and #12511 for a duplicated ``o.id``, which is
        deterministic. ``judge()`` does not catch it — a judgement computed from a
        partial set is the defect this guard exists to prevent, and #4515's
        incident is that it used to be silent.
    """
    where = "MATCH (e:Event)-[:aboutObject]->(o) WHERE o.id = $indicator"
    params: dict[str, Any] = {"indicator": indicator_id}
    if event_kind:
        where += " AND coalesce(e.eventKind, '') = $kind"
        params["kind"] = event_kind
    # ⛔ NO INSTANT-ORDERED CUT HERE (#12322). A cut ordered by the stored string
    # keeps rows by BYTE rank, and that string is not the instant (see
    # `_newest_first_key`): the page kept the wrong readings and the judgement
    # was computed from a stale one. The rows are instead fetched by KEYSET PAGES
    # — deterministic, and immune to being SILENTLY truncated by the server's
    # `RESULTSET_SIZE` (a single unbounded query is NOT: it returns a subset and
    # says nothing), because the collected rows are compared with an independent
    # `count(e)` and a short set RAISES — then ordered in Python with `limit`
    # applied AFTER that ordering, so no bound can decide WHICH readings the
    # judgement sees.
    #
    # ONE ROW PER (event, matched object NODE) PAIR — not per event — so `id(e)`
    # alone is a sufficient cursor. MEASURED on a lane running core `graph ver
    # 42006` (v4):
    #   * TWO `aboutObject` edges from one event to the SAME indicator node:
    #     `RETURN id(e), e.id` = **1 row**, while `RETURN count(r)` on the BOUND
    #     spelling `-[r:aboutObject]->` = 2.
    #   * TWO DISTINCT object nodes that SHARE `o.id`, one edge each from ONE
    #     event: `count(e)` = **2** and the same projection returns **2
    #     BYTE-IDENTICAL rows** (`[[2, 'b1', 9.0], [2, 'b1', 9.0]]`) — NOT
    #     collapsed.
    # So the engine IS scoping the pattern by node PAIR: two edges onto the SAME
    # object node collapse, and identical projected tuples from two DIFFERENT
    # object nodes do not. ⚠️ Do not reason from `count(*)` here: it counts PATH
    # rows, and its answer depends on the spelling AND on how many object nodes
    # share the id (measured 2 on both spellings on a two-object-node fixture,
    # and 2-vs-1 on a single-object one), so it is not a stable discriminator —
    # `count(r)` and an edge-derived projection are. What makes the cursor safe
    # is therefore narrower and checkable: distinct events have distinct
    # `id(e)`, and `>= last + 1` advances past EVERY copy of the last event, so
    # a duplicate can never be left behind to be counted twice. ⛔ It can,
    # however, be SKIPPED: when a duplicate group straddles a page boundary the
    # second copy is never collected, so the row count comes back SHORT of
    # `count(e)` and the truncation guard below fires on a graph that is not
    # truncated (measured, page size 2, rows ascending `2,3,3,4`: page 1 is full
    # at `[2,3]`, `cursor = 4` skips the second `3`, so 3 of 4 rows are
    # collected). The rows it costs are COPIES of an event already returned,
    # never a distinct reading — which is why that refusal is a false positive,
    # and why its cause is named in the error instead of being left to a re-run
    # that re-raises deterministically (#12511).
    #
    # ⛔ WHAT IS **NOT** SAFE: a repeated row means a `dwell` of N can be
    # satisfied by ONE measurement counted N times, and the shipped projection
    # does NOT exclude that — a duplicated `o.id` across two object nodes is
    # enough (measured: 2 rows, one event, one reading). An edge-derived column
    # (`id(r)`) is a SECOND route to the same defect, not the only one. Filed
    # as **#12511** with the fixture; closing it needs a de-duplication keyed
    # on `id(e)` (distinct events have distinct `id(e)`), not on `reading_id`.
    #
    # The doubled-edge test pins the OUTCOME (no reading LOST, none invented)
    # for the graph the SDK's own ingest writes; it does not pin #12511's.
    #
    # ⛔ KEEP THE PROJECTION EDGE-FREE. If a later change returns `id(r)` or any
    # other relationship property, duplicate edges become duplicate ROWS — and
    # then a `dwell` of N can be satisfied by ONE measurement repeated. That is
    # the SECOND route to the #12511 defect above, and the one a projection
    # change would OPEN (the first — a duplicated `o.id` — needs no projection
    # change at all, which is why closing that issue cannot be assumed to make
    # this note redundant, and why this note cannot claim to be its only record;
    # an earlier revision of this sentence did, ten lines below the block that
    # contradicts it). The disambiguation still stands on its own: the
    # note at ``hosted_backup.py:360-370`` is the #4515 "TOTAL OR FAIL" rule and
    # its load-bearing aggregate, and the operand table at ``:372-400`` is about
    # ``id(n) > -1`` disagreeing across the v4/v6 cores — an OPERAND edge case,
    # not duplicated rows.
    #
    # No Python de-duplication is layered on top on purpose: keyed on
    # `reading_id` it could DROP a distinct reading that legitimately shares an
    # `e.id`.
    cypher = (
        f"{where} AND id(e) >= $cursor "
        "RETURN id(e), e.id, e.value, e.unit, e.startedAt, e.createdAt "
        "ORDER BY id(e) LIMIT $batch"
    )
    if limit < 0:
        raise ValueError(
            f"limit must be a non-negative cap, got {limit!r} — a negative slice "
            "would drop the OLDEST reading and read as if nothing was bounded"
        )
    batch = int(_READINGS_PAGE_SIZE)
    if batch < 1:
        # ⛔ REFUSED BY NAME, because the count guard below would otherwise report
        # it as something else: `LIMIT 0` returns an empty page on every page, so
        # a MEASURED indicator would reach the truncation check and raise
        # `RuntimeError` — "N row(s) never arrived" — blaming a server cap for a
        # local misconfiguration, while an indicator with NO readings would return
        # `[]`, which is the correct answer. The independent `count(e)` is what
        # makes "nothing was ever measured" unreachable for a measured indicator;
        # this guard's job is the remaining one — name the constant to fix.
        raise ValueError(
            f"_READINGS_PAGE_SIZE must be >= 1, got {_READINGS_PAGE_SIZE!r} — a "
            "zero page size would make every measured indicator raise as if its "
            "page had been truncated"
        )
    g = sdk._get_proj().g
    # ⛔ THE INDEPENDENT COUNT, and it must be an AGGREGATE: #4515's incident is
    # a truncated page set that reported nothing, and `tools/turn_parity_census.py`
    # names the consequence ("silence, not an error"). One row, so a result-set
    # cap cannot truncate it to a lie.
    expected = int(g.query(f"{where} RETURN count(e)", params=params).result_set[0][0])
    rows: list[Any] = []
    cursor = 0
    while True:
        page = g.query(
            cypher, params={**params, "cursor": cursor, "batch": batch}
        ).result_set
        # `if not page` FIRST (the order `_paged_rows` uses): an empty tail must
        # end the loop, never reach `page[-1]`.
        if not page or len(page) < batch:
            rows.extend(page)
            break
        rows.extend(page)
        # `>= last + 1`, never `> last`: the advance is strictly increasing (the
        # loop always terminates), and it never compares a NODE id() negatively —
        # that answers 0 rows on the v6 core (measured —
        # `hosted_backup._paged_rows`, #9226).
        cursor = int(page[-1][0]) + 1
    if len(rows) < expected:
        # A short page is indistinguishable from a last page by shape alone, and
        # the rows it costs are whatever currently holds the highest LIVE ids
        # (``id(e)`` is creation order only while nothing has been deleted — the
        # engine reuses freed ids, measured), so the answer would be taken from an
        # incomplete set. Refuse rather than answer from the part that arrived.
        raise RuntimeError(
            f"latest_readings read {len(rows)} of {expected} rows for indicator "
            f"{indicator_id!r} at a page size of {batch} — {expected - len(rows)} "
            f"row(s) never arrived. FIVE causes, "
            "FOUR of which have a remedy: a server-side RESULTSET_SIZE pinned below "
            "_READINGS_PAGE_SIZE (lower the constant below the cap), or the "
            "matched set SHRANK after the count was taken — a concurrent DELETE "
            "of a reading the count had already seen, OR a concurrent UPDATE that "
            "moves a row OUT of the predicate (an ``eventKind`` change on the "
            "event, or deleting/rewiring its ``aboutObject`` edge) — and those "
            "take the same remedy, re-run, because no setting can prevent them — "
            "or the INDICATOR ITSELF went away between the count and the pages: "
            "it was DELETED, or its ``o.id`` was re-keyed, both of which empty "
            "the predicate for EVERY row at once (the predicate matches on "
            "``o.id``, so the indicator is the thing being matched). That fourth "
            "cause is not a retry problem: the node the caller asked about is "
            "gone or renamed, and a re-run does not raise — the count is 0 too, "
            "so it answers an EMPTY reading set about a node that is no longer "
            "the one asked for. Observe that rather than retry — OR the graph "
            "carries a DUPLICATED ``o.id`` (two ``:Object`` nodes sharing "
            "the indicator's id, each wired to the same events): a duplicate "
            "group that straddles a page boundary has its second copy "
            "skipped, so the count is SHORT by copies that were never a "
            "distinct reading. That cause has NO remedy here — it is "
            "deterministic, a re-run raises identically, and its root is tracked "
            "as #12511. A CREATE that "
            "lands mid-read is NOT among the five: it either reuses "
            "an id already paged past (count unchanged) or appends one (count "
            "short by nothing). Which rows a truncated tail costs is "
            "whatever currently holds the highest live ids (``id(e)`` is creation "
            "order only while nothing has been deleted, and a deleted row is "
            "wherever it was), so the loss is arbitrary by instant: an answer "
            "would be computed from an incomplete set, which is why this refuses "
            "rather than returns."
        )
    out: list[Reading] = []
    for row in rows:
        reading_id = row[1] if len(row) > 1 else None
        raw = row[2] if len(row) > 2 else None
        out.append(
            Reading(
                value=_num(raw),
                raw_value=raw,
                reading_id=reading_id,
                unit=row[3] if len(row) > 3 else None,
                started_at=(row[4] if len(row) > 4 else None)
                or (row[5] if len(row) > 5 else None),
            )
        )
    if since is not None:
        out = [r for r in out if instant_at_or_after(r.started_at, since)]
    out.sort(key=_newest_first_key, reverse=True)
    return out[:limit]


def live_judgements(
    sdk: Any,
    *,
    goal_id: str | None = None,
    indicator_id: str | None = None,
    target_id: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Up to ``limit`` (default 10,000) live judgement Points in scope, newest first.

    ⛔ ``limit`` IS A CAP, NOT A COURTESY. The read is a single ``LIMIT $lim``
    query, so a scope holding more live claims than the bound comes back with a
    list that looks complete and is not — and this function cannot report whether
    the bound was hit, because a list carries no room for that answer. A caller
    that needs the disclosure must ask for one
    (:func:`derived_invalidation.invalidate_stale` returns ``truncated``) or pass a
    bound it knows exceeds the scope.

    :func:`latest_judgement` is this read with ``limit=1`` — one predicate, two
    windows — because the invalidation sweep (#11926) has to answer "which
    claims does this graph still hold?" rather than "which one is current?":
    a second hand-written liveness clause is how a retracted claim gets served
    as the current state on one surface and excluded on another, which is the
    #2901 defect class this module imports ``TERMINAL_EXCLUDED_STATUSES`` to
    avoid.

    Scoping behaviour is identical to :func:`latest_judgement`: an omitted
    dimension is DROPPED from the WHERE clause (so `None` means "any", not
    "the null one") — which is why a caller that needs one chain must name all
    three.
    """
    clauses = ["p.judgementState IS NOT NULL",
               "(p.status IS NULL OR NOT (p.status IN $terminal))",
               "coalesce(p.outdated, false) = false"]
    params: dict[str, Any] = {"lim": limit if limit is not None else 10000,
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
    return [row[0] for row in rows]


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
    found = live_judgements(sdk, goal_id=goal_id, indicator_id=indicator_id,
                            target_id=target_id, limit=1)
    return found[0] if found else None


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

    ⛔ READINGS FROM BEFORE THE INDICATOR'S DEFINITION BOUNDARY ARE NOT SERVED
    (#11926 capability 9). Once the definition moves, an older reading measured
    a different quantity, so a trend drawn across the boundary is a number
    nobody took: the newest reading is the one that must win anyway, and a
    ``dwell`` window reaching back past the boundary would count agreement
    between two definitions. Excluding them here (rather than at each caller)
    means the retraction half — ``derived_invalidation.invalidate_stale`` — and
    the derivation half agree about what a reading is worth. If every reading
    predates the boundary, the result is EMPTY and the judgement is
    ``unknown``: #11822's rule that a missing reading is not a state change then
    retires nothing and mints nothing, which is the honest answer to "the
    definition changed and nothing has been measured under the new one yet".

    ⛔ AND A BOUNDARY THAT COULD NOT BE READ IS ALSO ``unknown``, never "no
    boundary was declared". ``BOUNDARY_UNREADABLE`` means the indicator may
    declare a boundary this pass could not see, so no reading can be shown to be
    comparable with the current definition: the judgement is ``unknown`` (reason
    ``boundary-unreadable``), ``reading`` stays ``None``, and
    :func:`record_judgement` writes nothing. The alternative — serving the
    readings as if none was declared — is what minted a durable ``met`` from a
    pre-boundary reading on a transient read failure (measured; see
    :data:`BOUNDARY_UNREADABLE`), which is exactly the "wrong number rather than a
    stale one" hazard capability 9 exists to remove.
    """
    boundary = definition_boundary(sdk, indicator_id)
    previous = latest_judgement(sdk, goal_id=goal_id, indicator_id=indicator_id,
                               target_id=target.target_id)
    previous_state = (previous or {}).get("judgementState") or "unknown"
    if previous_state not in JUDGEMENT_STATES:
        previous_state = "unknown"
    if boundary is BOUNDARY_UNREADABLE:
        return Judgement(state="unknown", reason="boundary-unreadable",
                         target=target, previous_state=previous_state)
    readings = latest_readings(
        sdk, indicator_id, event_kind=event_kind,
        limit=max(100, target.dwell),
        since=boundary,
    )
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


_READ_FAILED = object()
# ⛔ ABSENT is NOT unreadable (review P1 on #11953): a predecessor whose id does
# not exist cannot have been retired by this call, so its absence is evidence
# that NOTHING landed; a failed read is evidence of nothing at all. Conflating
# them kept the successor over a pure guard refusal, and because the goal's older
# claim is still live and wired, the leak became DURABLE (the next normal pass
# takes the unchanged arm on the leftover) — the over-count this module exists to
# remove.
_ABSENT = object()


def _point_status(sdk: Any, point_id: str) -> Any:
    """The point's stored ``status``; ``_ABSENT`` if there is no such point;
    ``_READ_FAILED`` if the read itself failed."""
    try:
        node = sdk.get_entity(point_id) or {}
    except Exception:
        return _READ_FAILED
    if not node:
        return _ABSENT
    return node.get("status")


def _supersession_evidence(
    sdk: Any,
    previous_point_id: str,
    successor_id: str,
    status_before: Any,
    *,
    guard_class: bool,
) -> str | None:
    """Why ``supersede_point(previous, successor)`` cannot be undone, or ``None``.

    Read from the GRAPH, never from the exception class (#11953). The class is
    not a reliable witness: the lifecycle / claim-class guards (#2498, #2351)
    are documented to raise BEFORE any mutation, but ``supersede_point`` appends
    its ``PointSuperseded`` journal event FIRST and then runs ~15 mutating
    statements, so a driver/infra failure inside that window raises through the
    SAME ``except Exception`` as a guard refusal — and the two demand opposite
    compensation.

    ⛔ THE PREDECESSOR'S `status` IS NOT A WITNESS FOR THAT WINDOW ON ITS OWN.
    `sdk.py` writes it LAST (`SET n.status = 'superseded'`, ~22712) — *after*
    every transfer leg, including the 2a-INPUT mirror migration at ~22235-22252.
    A mid-transfer failure therefore leaves the predecessor reading `live` while
    the successor already owns the migrated mirror, and a status-only test would
    delete exactly the state #11953 is about.

    So the witness is, first, the SUCCESSOR's own edges. This point was created
    by the caller moments earlier and carries none, so ANY incident edge — the
    2a operator re-point (~22136), the 2a-INPUT mirror (~22218), or the
    2a-DIRECT re-point (~22251) — can only have come from the transfer.

    Second, and only against the PRE-CALL baseline: a CHANGE in the
    predecessor's status. Comparing the status against `live` instead would be
    wrong, and wrong in the destructive-to-the-fix direction: a predecessor that
    was ALREADY `draft` or `retracted` reads as "non-live" on a pure guard
    refusal where nothing mutated, so the compensation would be skipped and the
    goal would be left with TWO current claims (`draft` is a CURRENT status —
    `live.CURRENT_POINT_STATUS_VALUES` — so `latest_judgement` can select it).
    `status_before` is the pre-call read; only a transition away from it is
    evidence that this call landed.

    A read we cannot complete is INCONCLUSIVE, and the tie-break is the failure
    class: the lifecycle / claim-class guards (#2498, #2351) are documented to
    raise BEFORE any mutation, so a `ValueError` over an unreadable graph still
    means nothing landed and the compensation is safe. Any OTHER failure may sit
    after the journal append, so it keeps the successor. This matters more than
    it looks: keeping the successor leaves a CURRENT `derived` claim, and the
    module's own next pass would then take the unchanged arm on it — making a
    transition the hard guard refused the goal's recorded state, durably.

    On the arms that do keep it, the trade is taken on irreversibility alone: a
    destroyed mirror cannot be re-read, while a leftover claim can be seen in the
    graph and repaired. Honest statement of the price: the successor is a current
    claim the next pass can adopt, and the extra operator it can leave behind is
    NOT reliably reported — ``_extra_operators`` is INPUT-scoped to ONE point, so
    the never-retired predecessor's operator falls outside it.

    Returns WHY the successor cannot be safely removed (``None`` when nothing
    landed), so the caller's note can state what was actually observed instead of
    asserting a mutation it never saw.
    """

    def _cannot_tell(reason: str) -> str | None:
        # An inconclusive read falls back to the guard invariant: a `ValueError`
        # is the pre-mutation class, so there is nothing to protect.
        return None if guard_class else reason

    try:
        edges = sdk._get_proj().g.query(
            "MATCH (p:Point {id:$i})-[r]-() RETURN type(r) LIMIT 1",
            params={"i": successor_id},
        ).result_set
    except Exception:
        return _cannot_tell(
            "the graph could not be read after the failure, so it cannot be "
            "told whether the supersession had begun")
    if edges:
        return ("the successor already carries an edge, so the supersession "
                "had begun wiring it")
    if status_before is _READ_FAILED:
        return _cannot_tell(
            "the predecessor's status could not be read before the call, and "
            "the failure is not one of the pre-mutation guards")
    if status_before is _ABSENT:
        # It did not exist before the call, so this call cannot have retired it:
        # `supersede_point` refuses a missing point before any mutation. Nothing
        # landed — the compensation is safe.
        return None
    status_now = _point_status(sdk, previous_point_id)
    if status_now is _READ_FAILED:
        return _cannot_tell(
            "the predecessor's status could not be read after the call, and the "
            "failure is not one of the pre-mutation guards")
    if status_now is _ABSENT:
        return _cannot_tell(
            "the predecessor no longer exists after the call — something else "
            "deleted it — so whether this call retired it cannot be ruled out")
    if status_now != status_before:
        return (f"the predecessor's status moved {status_before!r} -> "
                f"{status_now!r}")
    return None


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
        # ⛔ THE READING'S OWN AS-OF, carried on the claim so the claim is
        # self-describing about WHEN the number it judged was taken. #11926's
        # definition-boundary check compares that instant with the boundary: a
        # judgement minted after a BACK-DATED definition change would otherwise
        # read as "derived under the new definition" while its reading was taken
        # under the old one, and the stale state would survive. Absent when the
        # reading carries no instant — never fabricated from the clock, because a
        # fabricated instant is then what the derivation gets judged against.
        if judgement.reading.started_at:
            props["readingAsOf"] = judgement.reading.started_at
    # ⛔ THE OLDEST INSTANT THE DECISION CONSUMED, when the dwell window decided it
    # (see `Judgement.window_from`). Written beside `readingAsOf` so the claim
    # stays self-describing about *what justified it*, not only about which reading
    # happened to be newest — the difference that made a boundary inside a dwell
    # window invisible to the invalidation (#11926 review cycle 5).
    if judgement.window_from:
        props["readingWindowFrom"] = judgement.window_from

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
        status_before = _point_status(sdk, previous_point_id)
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
            #
            # ⛔ BUT THAT COMPENSATION IS ONLY SAFE WHILE NOTHING MUTATED (#11953).
            # `supersede_point` is NOT atomic: it emits `PointSuperseded` first,
            # then runs ~15 mutating statements — including the 2a-INPUT leg that
            # MERGEs `(new)-[:INPUT {idx}]->(op)` and DELETEs `(old)-[:INPUT]->(op)`
            # per row. A failure inside that window reaches THIS handler, where
            # deleting `point_id` restores nothing: it removes the already-
            # migrated mirror and leaves `op` holding `-[:IMPL]->(goal)` with no
            # source — an operator nothing prunes, because the next pass finds no
            # `(p)-[:INPUT]->(op)-[:IMPL]->(g)` match and mints a SECOND operator
            # (the exact double-count this module exists to remove). So the
            # decision is taken from the graph, not from the exception class.
            evidence = _supersession_evidence(
                sdk, previous_point_id, point_id, status_before,
                guard_class=isinstance(exc, ValueError),
            )
            if evidence is not None:
                # The supersession may already hold state this call cannot undo,
                # so the successor is left standing and the ORIGINAL reason is
                # re-raised unchanged (non-masking), carrying what was actually
                # observed — an arm that only saw a failed READ must not claim a
                # mutation happened.
                exc.add_note(
                    f"#11953: the supersession of {previous_point_id!r} could "
                    f"not be undone safely — {evidence}. The successor "
                    f"{point_id!r} was LEFT LIVE rather than deleted. Treat it "
                    "as the live judgement, check the graph, and complete or "
                    "retry the supersession by hand."
                )
                raise
            try:
                sdk.delete_point(point_id)
            except Exception as cleanup_exc:
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
