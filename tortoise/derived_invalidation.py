"""#11926 — retiring a derived state when the thing it was derived from changes.

#11822 built the arrow that compares a reading to a target and writes the answer
as a claim (``tortoise/goal_judgement.py``). This module is the arrow's OTHER
half: what happens to that claim when one of its three inputs is replaced. Before
this module the answer was **nothing** — measured on #11926 — and the graph kept
a conclusion whose justification was gone, which the design of record calls *"the
worst failure mode the EP layer has"* (``docs/plans/2026-11-11-goal-measurement-and-reasoning.md``
§3 rows 8, 9 and 12):

    ===========================  ==========================================
    what changes                 what used to happen
    ===========================  ==========================================
    the TARGET is revised        the state derived under the old bar stayed
    (comparator/threshold/dwell) current — a goal could sit at ``met`` after
                                 the threshold moved
    the DEFINITION changes       readings under two definitions were trended
    (what the indicator means)   together, and the state built from the old
                                 definition stayed current
    the GOAL is retired,         the states derived from it kept counting
    abandoned or superseded
    ===========================  ==========================================

Three properties of this module are deliberate and are the ones a reviewer should
push back on explicitly if they are wrong:

1. **ONE retire door, and it is an existing one.** A derived state whose
   justification is gone has NO successor, so it is retired with
   ``sdk.retract_point`` — tombstone-retract, terminal, journalled
   (``PointRetracted``), window end stamped. ``supersede_point`` is the wrong verb
   here (it needs a replacement, and would transfer the dead claim's operator onto
   whatever it was handed), and ``invalidate_point`` is the wrong verb too (it
   requires a ``corrected_by`` point and mints a ``CORRECTS`` edge to it). What it
   shares with the reading-change path is the FACTS a reader sees — a terminal
   status, a stamped window end and exclusion from every live read — not the
   mechanism: a reading change goes through ``supersede_point`` (status
   ``superseded``, a SUCCESSOR, the predecessor's operator TRANSFERRED onto it),
   while a retirement has no successor to hand anything to and therefore uses
   ``retract_point``. ⛔ The difference is stated because it has a residual of its
   own (below): a retracted claim's operator is neither transferred nor removed.
   #11824 (the uniform cross-kind ``replace``) is still open; nothing here
   re-implements it, and no migration, backfill or compatibility shim is carried.
2. **The decision is declared and the set is CLOSED.** :data:`STALE_REASONS`
   names every reason a derived state may be retired for; there is no ``reason``
   string a caller can invent, and ``tests/test_11926_derived_invalidation.py``
   proves each one is reachable from a graph a caller can actually build. This is
   §5's posture applied to the invalidation half: a hardcoded ``if`` chain's
   MISSING case is invisible, a declared set's is not.
3. **Nothing fires on its own.** Every entry point here is an explicit caller
   call, exactly as in #11822: #7860's **D13 — "what triggers the loop?" — is
   still unruled**, so this module builds no watcher, no hook and no schedule. A
   caller that wants the loop to react calls :func:`invalidate_stale`; the
   re-derivation that follows a retirement is a separate ``judge_and_record``
   call, which this module does NOT make for you (making it here would be
   deciding D13 by shipping).

⛔ WHAT THIS MODULE DOES NOT DO, stated so the boundary is visible rather than
implied. These are recorded residuals, not oversights:

* **It does not clear a stale EP posterior.** After a judgement is retired, the
  GOAL's ``goalState`` cache is reconciled (below), but the numeric
  ``confidence``/``posterior_alpha``/``posterior_beta`` EP last wrote onto the
  goal Object stay as they were: a node with no factors is not recomputed, and
  writing EP-owned state from here would be a second writer for a quantity EP
  owns. MEASURED (embedded lane, 2026-11-12): goal ``confidence`` 0.6502 before
  the retirement and 0.6502 after, while ``compute_confidence()`` no longer
  reports the goal at all — i.e. the *claim* stops counting immediately and the
  cached number ages out on the next pass that has a factor for it. Filed
  against the EP/``replace`` lane (#11824), not silently repaired here.
* **It does not move the retired claim's OPERATOR.** The door is
  ``retract_point``, which touches no operator, so the IMPL operator that carried
  the claim stays in the graph pointing at a retracted Point. EP is safe — a
  terminal input never participates in a propagation pass (``ep.py``'s shared
  ``TERMINAL_EXCLUDED_STATUSES`` predicate), measured: ``compute_confidence()``
  stops reporting the goal. What is left is graph litter that a supersession would
  have migrated: MEASURED, the documented follow-up ``judge_and_record`` returns a
  NEW ``operator_id`` with ``extraOperators: []`` — no transfer — so each
  retire → re-derive cycle adds one operator. Removing or re-pointing it is the
  ``replace`` primitive's job (#11824), the same lane that owns the EP-posterior
  residual above; doing it here would be a second writer for an edge this module
  did not mint.
* **It does not propagate a goal's retirement to its targets and sub-goals**
  (design of record row 12's second half). That is lifecycle propagation across
  entity kinds — the five-mechanisms problem #11824 exists to unify — and this
  module only retires the states DERIVED from the retired input, which is the
  half #11926's own "The shape" section asks for.
* **It does not retire a reading** (an ``:Event``). #11825 owns that (an Event
  cannot be superseded today), and a reading's value is never invalidated by this
  module — only the state derived from it.
* **It does not re-derive.** See the header's third property.
* **It does not stamp the reason on the retired claim.** ``status='retracted'``
  and the window end ARE durable (``retract_point`` journals them), but the
  human-readable reason is returned to the caller and is recomputable from the
  graph by :func:`invalidation_for` at any time. MEASURED: stamping it with
  ``update_point`` writes a live-only prop that neither replay fold carries, so
  ``check-consistency`` reds a FAITHFUL graph permanently. The block above
  :data:`STALE_REASONS` carries the full measurement; teaching the folds is the
  sanctioned fix and belongs to #11928's explain path, not to this lane.

`goalState` IS A CACHE, AND A RETIREMENT MUST NOT OUTRANK A WORD NOBODY MEASURED.
#11822's rule for ``unknown`` (never write ``active`` over a human's ``blocked``)
extends to the retirement path, and the gate is on the WORD IN THE CACHE — not on
why the judgement went. A cache holding a word this engine wrote
(:data:`DERIVED_GOAL_STATES`) is reconciled to what still stands, or cleared when
nothing does; a cache holding ANY OTHER word (``active``, ``blocked``,
``guideline``, ``superseded``, ``abandoned``) is a decision nobody measured, so it
is preserved and reported. Gating on the REASON instead fails in both directions
and is what a review measured: a goal retired by its ``status`` kept ``met``
forever (the ``goal-retired`` arm reconciled nothing), while a ``blocked`` goal
lost its word to a ``target-revised`` retirement that cleared the cache under it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .goal_judgement import (
    _ROW_FLIP,
    DEFINITION_BOUNDARY_PROP,
    GOAL_STATE_CACHE,
    JUDGEMENT_STATES,
    _declared_dwell,
    _num,
    canonical_comparator,
    instant_at_or_after,
    latest_judgement,
    live_judgements,
)
from .live import TERMINAL_EXCLUDED_STATUSES

__all__ = [
    "DEFINITION_BOUNDARY_PROP",
    "DERIVED_GOAL_STATES",
    "RETIRED_GOAL_STATES",
    "STALE_REASONS",
    "Invalidation",
    "Verdict",
    "invalidate",
    "invalidate_stale",
    "invalidation_for",
    "mark_definition_change",
    "reconcile_goal_state_cache",
    "verdicts",
]

#: Every reason a derived state may be retired for — the CLOSED set, in the order
#: :func:`invalidation_for` tests them. The ordering is the precedence, and it is
#: deliberate: the further UP the chain the change is, the less a narrower reason
#: about one input tells you. A retired GOAL makes every judgement about it moot
#: whatever its target says, so it is tested first; a retired TARGET outranks a
#: revised bar on the same node (there is no bar to compare when the target is no
#: longer live); a moved DEFINITION (what the number means) outranks a moved bar
#: (what the bar is), because a reading taken under another definition is not
#: comparable at all.
STALE_REASONS = (
    "goal-retired",
    "target-retired",
    "indicator-retired",
    "definition-changed",
    "target-revised",
)

#: The ``goalState`` words that mean the goal is no longer a pursued commitment.
#: A SUBSET of the closed #7856 vocabulary (``sdk.GOAL_STATE_VALUES``) and pinned
#: as such by a test — ``met``/``unmet``/``blocked``/``active`` are states OF a
#: live commitment and must never be read as its retirement, and ``guideline`` is
#: a demoted target (#6792), not a retired goal.
RETIRED_GOAL_STATES = frozenset({"superseded", "abandoned"})

#: The only ``goalState`` words :func:`goal_judgement.record_judgement` can write
#: — i.e. the words that ARE the cache of a judgement. Derived from
#: :data:`goal_judgement.GOAL_STATE_CACHE` (never hand-written): a word the writer
#: cannot write is not a cache value, and reconciling over one would erase a
#: decision nobody measured.
DERIVED_GOAL_STATES = frozenset(
    word for word in GOAL_STATE_CACHE.values() if word is not None
)

#: ⛔ THE REASON IS NOT STAMPED ON THE CLAIM, and that is a MEASURED decision
#: rather than an omission. ``update_point`` journals a ``PointRevised`` that
#: CARRIES an arbitrary prop, but neither replay fold carries it back: the pure
#: fold (``projection/__init__.py::_apply_one``'s ``PointRevised`` arm) rebuilds a
#: fixed field set, and the graph fold (``_revise_point``) builds its clauses the
#: same way — so a live-only prop makes ``check-consistency`` report
#: ``divergence="content"`` on a FAITHFUL graph, with advice ("replay the
#: journal") that reproduces exactly that graph and can never clear it. MEASURED
#: on this lane before the decision, with the three props written::
#:
#:     divergent_points: [{fields: ['invalidatedAt','invalidatedBy',
#:                                  'invalidationReason']}]
#:
#: teaching the two folds a new prop family is the sanctioned fix (the
#: #8317/#5048/#3817 class, one field at a time) and it is NOT this issue's: it
#: touches the most sensitive replay code in the repo to carry PROVENANCE, while
#: the reason is recomputable from the graph by :func:`invalidation_for` at any
#: time and is returned to the caller by :func:`invalidate`. So the retirement
#: records WHAT it did durably — ``status='retracted'`` plus the window end, both
#: journalled by ``retract_point`` — and the reason lives in the ANSWER. A durable
#: in-graph reason (what #11928's explain path would want) is a follow-up with its
#: own fold, deliberately not smuggled in here. The claim's own
#: ``judgementState``/``comparator``/``threshold`` are never touched: a retirement
#: records that the claim stopped counting, never a rewrite of what it said
#: (#11822's rule for the supersession path).


class _Missing:
    """The node does not exist. Distinct from an UNREADABLE read (#11953's lesson).

    A missing node is evidence that nothing can be derived from it; a failed read
    is evidence of nothing at all. Folding them together would retire a
    judgement because the database hiccuped, and a retirement is terminal — it
    cannot be undone by the next pass the way a refused write can.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "MISSING"


MISSING = _Missing()


@dataclass(frozen=True)
class Invalidation:
    """One derived state that must stop counting, and the reason it must."""

    judgement_id: str
    reason: str
    detail: str
    #: The instant the justification is KNOWN to have gone away, when the graph
    #: records one (the definition boundary does; a target's or a goal's
    #: lifecycle does not). ``None`` means "we did not observe the instant", and
    #: :func:`invalidate` then stamps the call's own clock rather than inventing
    #: one — an undated retirement would leave the claim's window open forever.
    effective_at: Any = None
    state: str | None = None
    goal_id: str | None = None
    indicator_id: str | None = None
    target_id: str | None = None

    def __post_init__(self) -> None:
        if self.reason not in STALE_REASONS:  # pragma: no cover - closed set
            raise AssertionError(
                f"undeclared invalidation reason {self.reason!r} — the set is "
                f"closed: {STALE_REASONS}"
            )

    def as_dict(self) -> dict:
        return {
            "judgementId": self.judgement_id,
            "reason": self.reason,
            "detail": self.detail,
            "effectiveAt": self.effective_at,
            "state": self.state,
            "goalId": self.goal_id,
            "indicatorId": self.indicator_id,
            "targetId": self.target_id,
        }


@dataclass(frozen=True)
class Verdict:
    """The answer for ONE live judgement: retire it, leave it, or say why that is
    undecidable.

    A HEALTHY verdict sets neither field. ``undecidable`` exists because "we
    could not tell" must never be reported as "nothing to do": a definition
    boundary stored in a spelling that names no instant makes
    ``definition-changed`` undecidable — and an undecidable entry is surfaced by
    :func:`invalidate_stale` in its own bucket, never dropped.
    """

    judgement_id: str
    invalidation: Invalidation | None = None
    undecidable: str | None = None

    def __post_init__(self) -> None:
        # A HEALTHY verdict carries neither field — that is the common case and
        # the one the invariant must not forbid. It forbids the contradiction
        # (both set: retire it AND we cannot tell), which is the only
        # combination a caller could act on wrongly.
        if self.invalidation is not None and self.undecidable is not None:
            raise AssertionError(
                "a Verdict carries AT MOST ONE of an invalidation and an "
                f"undecidable reason ({self!r})"
            )


class _Nodes:
    """Per-sweep node reads, memoised, with MISSING and UNREADABLE kept apart."""

    def __init__(self, sdk: Any) -> None:
        self._sdk = sdk
        self._cache: dict[str, Any] = {}

    def get(self, node_id: Any) -> Any:
        """The node's properties, ``MISSING``, or ``None`` when the read failed."""
        if not node_id:
            return MISSING
        key = str(node_id)
        if key in self._cache:
            return self._cache[key]
        try:
            node = self._sdk.get_entity(key) or MISSING
        except Exception:
            # ⛔ NOT cached: a transient failure must not become this sweep's
            # answer for every judgement on the node.
            return None
        self._cache[key] = node
        return node


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _retirement(node: Any, *, goal: bool = False) -> str | None:
    """Why ``node`` is no longer a live input, or ``None`` when it still is.

    ``node`` is a properties dict, :data:`MISSING`, or ``None`` for a failed read;
    a failed read is the caller's to report, so this returns ``None`` for it and
    :func:`invalidation_for` turns that into ``undecidable``.
    """
    if node is MISSING:
        return "it no longer exists"
    if not isinstance(node, dict):
        return None
    status = node.get("status")
    # ⛔ THE RECALL LANE'S OWN OBJECT VOCABULARY, NOT THE NARROWER FOLD (#11926
    # review, P1). `commit_ops.OBJECT_TERMINAL_STATUSES` omits `outdated` — the
    # legacy supersede/invalidate WORD — and the OBJECT family is SPLIT about it:
    # the four retrieval legs and the recall view still SERVE an Object whose
    # status is `outdated` (`search_engine._status_vocab_for`,
    # `commit_ops.py:1472` states it: *"'outdated' IS visible — it is not in the
    # object vocabulary, only in the POINT-terminal set"*), while the recall
    # fold's own exclusion, ``assembly._RECALL_OBJECT_EXCLUDED_STATUSES``, is
    # `OBJECT_TERMINAL_STATUSES | {"outdated"}` and is frozen by
    # `tests/test_assembly_pure.py`.
    #
    # So this arm follows the RECALL lane, and the reason is STALENESS, not
    # "every reader agrees": `outdated` is the legacy invalidate/stale word, and
    # a state derived from an input carrying it is exactly the stale conclusion
    # #11926 exists to retire (re-derivation mints a fresh claim, so nothing is
    # lost). MEASURED red before this arm existed:
    # `update_entity(<goal>, status='outdated')` is an accepted public write and
    # the verdict came back ``None`` — HEALTHY, not `goal-retired`.
    # `TERMINAL_EXCLUDED_STATUSES` = {retracted, superseded, outdated, archived} ∪
    # {deprecated}: the first four are all writable on an Object and the last is
    # legacy-only, so this is not over-broad (`draft`/`live` are not members).
    if isinstance(status, str) and status in TERMINAL_EXCLUDED_STATUSES:
        return f"its status is {status!r}"
    # The OTHER legacy spelling: `outdated=true` with a live status. No Object
    # WRITER sets it (``live._terminal_excluded`` passes
    # ``include_outdated_flag=False`` for Objects for that reason), so this shape
    # only appears in legacy data — the population a retirement sweep must not
    # serve as live.
    #
    # ⛔ READ BY ``bool()``, not ``is True``, and that is not a detail: it is how
    # ``live.is_terminal_status``'s flag half reads it, and it is what the Cypher
    # predicate agrees with on every BOOLEAN, NULL, non-empty string and non-zero
    # number. An identity test diverges on a truthy non-boolean legacy value —
    # a node holding `outdated = 'true'` is terminal to the flag-aware read
    # surfaces and was LIVE here, which is the same disagreement this arm exists
    # to end. The one CLASS where ``bool()`` and the Cypher predicate still differ —
    # falsy-but-not-``false`` values (``0``/``0.0``, ``""``): live here, terminal in
    # Cypher — is the residual ``is_terminal_status`` documents and no in-repo writer
    # can produce (#7075). Both spellings point the same way there (Python live,
    # graph terminal): the direction this fix CLOSES, not its opposite.
    if bool(node.get("outdated")):
        return "its legacy outdated flag is set"
    successor = node.get("supersededBy") or node.get("superseded_by")
    if successor:
        return f"it was superseded by {successor!r}"
    if goal:
        state = node.get("goalState")
        if isinstance(state, str) and state in RETIRED_GOAL_STATES:
            return f"its goalState is {state!r}"
    return None


def _live_bar(node: dict) -> dict:
    """The target's CURRENT judging bar, with each field independently readable.

    ``Target.from_node`` refuses a target that declares no comparison; this
    reader must NOT refuse, because "the target stopped declaring a comparator"
    is itself a revision to report rather than a raise — so an unreadable field
    is carried through as ``None`` and described by :func:`_drift`.
    """
    comparator = node.get("comparator")
    try:
        canonical = canonical_comparator(comparator) if comparator is not None else None
    except ValueError:
        canonical = None
    dwell = _declared_dwell(node.get("dwell"))
    if isinstance(dwell, bool) or not isinstance(dwell, int) or dwell < 1:
        dwell = None
    return {"comparator": canonical,
            "threshold": _num(node.get("threshold")),
            "dwell": dwell}


def _drift(judgement: dict, node: dict) -> str | None:
    """What moved between the bar the judgement RECORDED and the target's now.

    ⛔ The recorded side is the claim's own stored ``comparator``/``threshold``/
    ``dwell``, written by #11822 at derivation time. That is what makes this
    check work on claims written BEFORE this module existed, with no backfill and
    no shim: the bar was always copied onto the claim, so the derivation is
    self-describing and the comparison needs nothing the graph does not already
    hold.
    """
    live = _live_bar(node)
    changes: list[str] = []

    recorded_comparator = judgement.get("comparator")
    if recorded_comparator is not None:
        try:
            recorded_comparator = canonical_comparator(recorded_comparator)
        except ValueError:  # pragma: no cover - the writer canonicalises it
            recorded_comparator = str(recorded_comparator)
        if live["comparator"] is None:
            changes.append(
                f"comparator {recorded_comparator!r} -> unreadable "
                f"({node.get('comparator')!r})"
            )
        elif live["comparator"] != recorded_comparator:
            changes.append(f"comparator {recorded_comparator!r} -> {live['comparator']!r}")

    recorded_threshold = _num(judgement.get("threshold"))
    if recorded_threshold is not None:
        if live["threshold"] is None:
            changes.append(
                f"threshold {recorded_threshold:g} -> unreadable "
                f"({node.get('threshold')!r})"
            )
        elif live["threshold"] != recorded_threshold:
            changes.append(f"threshold {recorded_threshold:g} -> {live['threshold']:g}")

    recorded_dwell = _declared_dwell(judgement.get("dwell"))
    if isinstance(recorded_dwell, int) and not isinstance(recorded_dwell, bool):
        if live["dwell"] is None:
            changes.append(
                f"dwell {recorded_dwell} -> unreadable ({node.get('dwell')!r})"
            )
        elif live["dwell"] != recorded_dwell:
            changes.append(f"dwell {recorded_dwell} -> {live['dwell']}")

    return ", ".join(changes) if changes else None


def invalidation_for(
    sdk: Any,
    judgement: dict,
    *,
    nodes: _Nodes | None = None,
) -> Verdict:
    """Does this live judgement still stand? A :class:`Verdict`, never a guess.

    ``judgement`` is a live judgement's properties (as
    :func:`goal_judgement.live_judgements` returns them). The checks run in
    :data:`STALE_REASONS` order and the FIRST applicable one wins.
    """
    reader = nodes if nodes is not None else _Nodes(sdk)
    judgement_id = judgement.get("id") or ""
    goal_id = judgement.get("goalId")
    indicator_id = judgement.get("indicatorId")
    target_id = judgement.get("targetId")
    state = judgement.get("judgementState")
    if state not in JUDGEMENT_STATES:  # pragma: no cover - the writer validates
        state = None

    def _verdict(reason: str, detail: str, effective_at: Any = None) -> Verdict:
        return Verdict(judgement_id=judgement_id, invalidation=Invalidation(
            judgement_id=judgement_id, reason=reason, detail=detail,
            effective_at=effective_at, state=state, goal_id=goal_id,
            indicator_id=indicator_id, target_id=target_id,
        ))

    #: Set when a SCOPE dimension (goal / target / indicator) cannot be decided.
    #: Like :data:`undecidable_definition` below, it is reported only when no
    #: DEFINITE finding exists — a claim whose bar has visibly moved is stale
    #: whether or not the claim also fails to name its indicator.
    undecidable_scope: str | None = None

    # 1. the GOAL. A judgement is a claim ABOUT a goal; once the commitment is
    #    retired there is nothing for it to be about.
    #    ⛔ A claim that NAMES no goal is UNDECIDABLE, never `goal-retired`:
    #    `_Nodes.get` maps a falsy id to MISSING (which `_retirement` reads as "it
    #    no longer exists"), so without this guard an unnamed dimension is retired
    #    with a claim about a node that was never named — terminally, since the
    #    retirement cannot be undone. The same guard is stated below for the
    #    target, and it is needed here for the SAME reason (a corrupt or
    #    partial claim, not a graph change).
    if not goal_id:
        undecidable_scope = (
            "the judgement names no goal, so there is no commitment whose "
            "retirement could retire it")
        goal = None
    else:
        goal = reader.get(goal_id)
        if goal is None:
            undecidable_scope = (
                f"the goal {goal_id!r} could not be read, so whether it is "
                "retired is unknown")
        else:
            retired = _retirement(goal, goal=True)
            if retired:
                return _verdict("goal-retired",
                                f"the goal {goal_id!r} is retired — {retired}")

    # 2. the TARGET. A retired target cannot be the bar anything is judged against.
    #    ⛔ A claim that NAMES no target is undecidable rather than stale: the
    #    writer has required `targetId` since #11965, so an unnamed one is a
    #    legacy shape, and retiring it here would turn a writer-side fix into a
    #    mass retirement on a technicality.
    #
    #    ⛔ AND ALL FOUR OF THESE ARE **DEFERRED**, NOT RETURNED. An unnamed or
    #    unreadable dimension is an ABSENCE of evidence; the bar check below is
    #    evidence. Returning here let an unnamed `indicatorId` — the pre-#11965
    #    shape this file's own comment says an unscoped sweep meets — SHIELD a
    #    claim whose bar had visibly moved, so a stale claim survived a target
    #    revision and the goal kept reading `met`. MEASURED (review cycle 7):
    #    judgementState=`met`, `indicatorId` removed, `threshold 10.0 -> 8.0`
    #    (so `9.0 <= 8.0` is false) -> verdict `undecidable (names no
    #    indicator)`, `invalidation: None`. Only DEFINITE findings (a retired
    #    input, a derived-before-the-boundary claim, a revised bar) return early;
    #    the deferred strings are reported at the end, when nothing definite was
    #    found. No retirement is added by this ordering.
    if not target_id:
        undecidable_scope = undecidable_scope or (
            "the judgement names no target, so its bar cannot be compared with "
            "the target that replaced it")
        target = None
    else:
        target = reader.get(target_id)
        if target is None:
            undecidable_scope = undecidable_scope or (
                f"the target {target_id!r} could not be read, so whether it is "
                "retired is unknown")
        else:
            retired = _retirement(target)
            if retired:
                return _verdict("target-retired",
                                f"the target {target_id!r} is retired — {retired}")

    # 3. the INDICATOR (the definition's home).
    #    ⛔ The same unnamed guard, and this one is REACHABLE from real data:
    #    claims written by the pre-#11965 writer carry `judgementState` + `goalId`
    #    and NO `indicatorId` (the argument defaulted to `None` and the writer
    #    stored the prop only for a truthy id), which is exactly what an unscoped
    #    repair sweep meets.
    if not indicator_id:
        undecidable_scope = undecidable_scope or (
            "the judgement names no indicator, so there is no definition whose "
            "change could retire it (a pre-#11965 claim)")
        indicator = None
    else:
        indicator = reader.get(indicator_id)
        if indicator is None:
            undecidable_scope = undecidable_scope or (
                f"the indicator {indicator_id!r} could not be read, so whether "
                "it is retired is unknown")
        else:
            retired = _retirement(indicator)
            if retired:
                return _verdict("indicator-retired",
                                f"the indicator {indicator_id!r} is retired — {retired}")

    # 4. the DEFINITION. The boundary is an INSTANT, so it is comparable with the
    #    instant this judgement was derived at — the one case where the graph
    #    records when the justification went away, which is why it is the only
    #    reason that carries an `effective_at`.
    #        A JUDGEMENT IS JUDGED ON THE READING(S) IT CONSUMED, not on the clock
    #        its claim was written at. The claim carries the reading's own as-of
    #        (``readingAsOf``, written by #11822's writer), and using the mint
    #        instant alone would let a BACK-DATED definition change leave a state
    #        derived from an older reading standing — a real sequence ("the
    #        definition changed on Monday, noticed on Tuesday"), not a corner
    #        case. ⛔ The mint clock is NOT a fallback for a claim that names no
    #        reading instant: it says when the CLAIM was written, never when the
    #        number it judged was taken, and a claim that cannot supply a reading
    #        instant is `undecidable` (see the block inside the boundary branch).
    #
    #    ⛔ THE BOUNDARY IS READ FROM THE INDICATOR DICT `_Nodes` ALREADY FETCHED,
    #    not through `definition_boundary()`. That helper folds "the read failed"
    #    into the same `None` as "no boundary was declared" — which `judge` can
    #    live with (it then serves the readings, and a later pass re-reads), but
    #    an INVALIDATION cannot: a failed read would make a stale claim look
    #    HEALTHY, and silence is the one direction this module must never fail in.
    #    `_Nodes` keeps MISSING and UNREADABLE apart, and `indicator is None` was
    #    already refused above, so a boundary that is absent here really is absent.
    #: Set when the DEFINITION trigger cannot be decided. Reported only if the bar
    #: check below finds nothing — a definite finding outranks an unclear one.
    undecidable_definition: str | None = None
    boundary = (indicator.get(DEFINITION_BOUNDARY_PROP)
                if isinstance(indicator, dict) else None)
    if boundary is not None:
        #        A JUDGEMENT IS JUDGED ON THE READING(S) IT CONSUMED, not on the
        #        clock its claim was written at.
        #
        #        ⛔ THE MINT CLOCK IS NOT A SUBSTITUTE. `evaluatedAt`/`createdAt` say
        #        when the CLAIM was written, never when the number it judged was
        #        taken, so falling back to them is the silence this module must
        #        never produce: a boundary landing between the reading and the mint
        #        reads as "the claim is newer, it stands". MEASURED (review
        #        cycle 6): a claim written before `readingAsOf` existed carries no
        #        reading instant, and the fallback made the retirement half call it
        #        HEALTHY while the derivation half refused to satisfy its window —
        #        the two halves of this same change disagreeing on one graph.
        #
        #        ⛔ AND A ``dwell`` WINDOW CONSUMES MORE THAN THE NEWEST READING. A
        #        flip at ``dwell=2`` rests on BOTH readings it inspected, so the
        #        claim carries the OLDEST of them (``readingWindowFrom``) and THAT
        #        is the instant compared here: a boundary inside the window means
        #        the window is a trend drawn across two definitions (§3 row 9).
        #        MEASURED (cycle 5): comparing only ``readingAsOf`` let such a claim
        #        survive while the goal kept reading ``met``.
        dwell = _declared_dwell(judgement.get("dwell"))
        window_expected = (judgement.get("judgementReason") == _ROW_FLIP
                           and isinstance(dwell, int)
                           and not isinstance(dwell, bool) and dwell > 1)
        derived_at = (judgement.get("readingWindowFrom") if window_expected
                      else judgement.get("readingAsOf"))
        if derived_at is None:
            # ⛔ DEFERRED, NOT RETURNED. "I cannot place this claim against the
            # boundary" is an absence of evidence; a DEFINITE reason further down (a
            # revised bar) is evidence. Returning here would let an unplaceable
            # boundary SHIELD a claim whose bar has visibly moved, so a stale claim
            # would survive a target revision.
            undecidable_definition = (
                f"the definition boundary {boundary!r} cannot be compared with "
                "this judgement: "
                + ("its dwell window cannot be placed (a reading in it names no "
                   "instant, or the claim predates `readingWindowFrom`)"
                   if window_expected else
                   "it records no reading instant (a claim written before "
                   "`readingAsOf` existed)"))
        else:
            # ⛔ THE DECISION IS **STRICT**: a claim derived EXACTLY AT the boundary
            # STANDS. `instant_at_or_after` is `>=`, so at equality BOTH directions
            # are true — a `if boundary_is_later:` test therefore retired a claim
            # the derivation half of this same change ADMITS (the reading filter is
            # `>= boundary`), with a detail string ("before the change") that was
            # false. MEASURED by review: the claim was retracted, the documented
            # `judge_and_record` follow-up re-minted it, and the next sweep retired
            # that one too — a retire/re-derive loop that grows a Point and an
            # operator per cycle.
            #
            # Both directions are still needed, because `instant_at_or_after`
            # answers `False` for ANY pair it cannot order — which is not the same
            # fact as "the judgement predates it".
            derived_is_at_or_after = instant_at_or_after(derived_at, boundary)
            boundary_is_at_or_after = instant_at_or_after(boundary, derived_at)
            if not derived_is_at_or_after and not boundary_is_at_or_after:
                undecidable_definition = (
                    f"the definition boundary {boundary!r} and the judgement's "
                    f"derivation instant {str(derived_at)!r} cannot be ordered "
                    "(one of them names no instant)")
            elif not derived_is_at_or_after:   # strictly before the boundary
                return _verdict(
                    "definition-changed",
                    f"the indicator {indicator_id!r} definition changed at "
                    f"{boundary!r}; this judgement was derived at {derived_at!r}, "
                    "before the change",
                    effective_at=boundary,
                )

    # 5. the BAR. The judgement copied the comparator/threshold/dwell it was
    #    derived under, so a revision is a plain comparison — no history needed.
    #    SKIPPED only when the target could not be read at all; an unreadable
    #    target is already reported through ``undecidable_scope``.
    moved = _drift(judgement, target) if isinstance(target, dict) else None
    if moved:
        return _verdict("target-revised",
                        f"the target {target_id!r} was revised — {moved}")

    # ⛔ NOTHING DEFINITE FIRED, so now the deferred answers are reported —
    # DEFINITION first, because a boundary that cannot be compared is a fact about
    # THIS claim's own history, while an unnamed/unreadable dimension is a fact
    # about the claim's shape (or a transient read). A repair sweep that retries
    # gets the same answer either way; naming the more specific one is what makes
    # the report actionable.
    if undecidable_definition is not None:
        return Verdict(judgement_id=judgement_id, undecidable=undecidable_definition)
    if undecidable_scope is not None:
        return Verdict(judgement_id=judgement_id, undecidable=undecidable_scope)
    return Verdict(judgement_id=judgement_id)


def verdicts(
    sdk: Any,
    *,
    goal_id: str | None = None,
    indicator_id: str | None = None,
    target_id: str | None = None,
    limit: int = 10000,
) -> list[Verdict]:
    """One :class:`Verdict` for each LIVE judgement this scan REACHED.

    ⛔ UP TO ``limit`` (default 10,000), NEWEST FIRST — and this call CANNOT tell
    you whether it reached them all. The graph read is capped
    (:func:`~tortoise.goal_judgement.live_judgements` carries the same bound), so
    a scope holding more live claims than ``limit`` comes back with a list that
    looks complete and is not: pass a ``limit`` you know exceeds the scope, or use
    :func:`invalidate_stale`, whose ``truncated`` key reports exactly that. A
    silent cap is the #11823 defect class this module exists to close, and an
    audit path that hid it would be the same bug one layer up.

    ⛔ An UNSCOPED call narrows no dimension, so it sweeps the whole graph rather
    than one chain. That is a real
    repair sweep rather than a mistake — a graph can hold judgements its goals no
    longer reach — but it is also the call that can retire the most, so
    :func:`invalidate_stale` offers ``dry_run`` for exactly this case.
    """
    claims = live_judgements(sdk, goal_id=goal_id, indicator_id=indicator_id,
                             target_id=target_id, limit=limit)
    reader = _Nodes(sdk)
    return [invalidation_for(sdk, claim, nodes=reader) for claim in claims]


def _reconcile_or_report(sdk: Any, goal_id: str | None) -> dict:
    """Reconcile a goal's cached word, REPORTING a failure instead of raising.

    Every ``alreadyRetired`` answer owes the goal's ``goalState`` cache a
    reconcile (#12336): the retirement is a no-op there, but the WORD a goal
    displays can still name a claim that vanished (#12315's arm) or was retired by
    another route — and no AUTOMATIC path repairs it, because
    :func:`invalidate_stale` scans LIVE claims, so a goal whose last claim is gone
    is never a finding again. ⚠️ "No automatic path" is not "no repair":
    :func:`reconcile_goal_state_cache` is public and idempotent, and calling it
    again with the goal's id IS the repair route — named in the success arm's own
    comment (step 4 below) and in that function's docstring, never in a "failure
    message", because a cache failure is REPORTED as a dict and not raised.
    Measured before this existed: ``goalState`` stayed ``met`` after the claim was
    deleted, through both the sweep and this door.

    Reported, never raised, for the same reason the success arm reports its
    reconcile failure: the retirement outcome is already the idempotent one, and a
    cache that could not follow it is a failure of THIS sweep (``counts.failed``,
    ``counts.gone``), not a reason to discard the sweep's other results. A falsy
    ``goal_id`` is not a failure — ``reconcile_goal_state_cache`` says so itself.

    ⛔ THE CALLER MUST NAME THE GOAL, and every arm that has read a claim (or its
    re-read) sources the id from THAT — the already-terminal, vanish-before-
    retirement and success arms from ``claim.get("goalId")``; and of the TWO
    post-exception arms, the one whose re-read came back ``MISSING`` ALSO comes
    from ``claim.get("goalId")`` (``after`` is the sentinel there and has no
    ``.get``), while the one whose re-read is a dict comes from
    ``after.get("goalId")`` — using the caller's id only for a path that never
    held a claim, or one that names none. The claim wins because its ``goalId``
    says whose displayed word names this claim, which is the state being
    repaired; a caller's id that diverges names a goal that is not the one
    showing the stale word. A skipped check is invisible in the counters — it is
    neither ``failed`` nor ``gone`` — so passing a goal id that is falsy for a
    reason the claim could have supplied is a reconcile that silently does not
    happen over the exact shape this helper exists for (#12336).
    ⚠️ ``.get`` rather than ``[]``: an arm written with the subscript would
    RAISE on a claim that lacks the prop, turning a reconcilable skip into a
    ``failed`` the sweep books with ``found: None``.

    ⚠️ It is deliberately the SAME reconcile the retirement path uses (step 4),
    wrapped once, so there is one set of predicates behind
    ``counts.failed``/``gone`` however the finding reached the door.
    """
    try:
        return reconcile_goal_state_cache(sdk, goal_id)
    except Exception as exc:
        return {"checked": False, "error": f"{type(exc).__name__}: {exc}"}


def invalidate(
    sdk: Any,
    invalidation: Invalidation,
    *,
    at: str | None = None,
) -> dict:
    """Retire ONE derived state — the single retire door of this module.

    Steps, in order:

    1. read the claim. An ABSENT claim is reported, not raised: the caller's scope
       may be stale, and a retirement needs no apology for a claim that is
       already gone.
    2. refuse to re-retire. A claim that is already terminal is reported as
       ``alreadyRetired`` — the shared ``live.TERMINAL_EXCLUDED_STATUSES``
       vocabulary plus the legacy ``outdated`` flag, exactly the predicate the
       read paths use, so "already retired" here means the same thing it means
       everywhere else. An ABSENT claim is reported the same way: it cannot be
       stale, so there is nothing to retire because it is already gone (#12315).
       ⛔ EVERY ``alreadyRetired`` answer still owes the goal's cache its
       reconcile (step 4), including this one: the word a goal displays can name
       a claim that vanished or was retired elsewhere, and no automatic path
       repairs it (#12336). ``found`` says which shape it was — ``False`` when there is
       NO CLAIM NOW (it was absent, or it was read at the top and then vanished
       before ``retract_point`` landed — in that second shape a claim WAS read,
       and its ``goalId`` is still what the reconcile uses), and ``True`` when a
       claim was read and is already terminal.
    3. ``retract_point`` — journal-first ORDER, so a rebuild replays the retirement
       instead of resurrecting a claim the live graph retired. ⚠️ ORDER is what
       the door guarantees and CONFIRMATION is not: its own docstring records
       that a failed append still retracts the cache node and reports success
       (#9124 part 2, open), and that residual is inherited here — ``invalidate``
       reports ``invalidated: True`` from the door's return and cannot observe a
       lost ``PointRetracted``. Named rather than papered over; confirming the
       append belongs to #9124.
    4. reconcile the goal's ``goalState`` cache — gated on the WORD in the cache
       (:func:`reconcile_goal_state_cache`), never on the reason: a word this
       engine wrote is reconciled or cleared, anything else is preserved. A
       failure here is REPORTED, not raised: the retirement is already terminal
       and durable, so aborting would tell a caller nothing landed when the
       important half did — and it would take the rest of a sweep's results with
       it.

    ⛔ There is no fourth WRITE. The reason this state was retired is returned to
    the caller and is recomputable from the graph by :func:`invalidation_for`;
    stamping it onto the claim is deliberately NOT done here, and the block above
    :data:`STALE_REASONS` records the measurement that decided it (a live-only
    prop makes ``check-consistency`` red a FAITHFUL graph forever).

    ``at`` is the claim's window end. Default: ``invalidation.effective_at`` when
    the graph recorded the instant (the definition boundary), else the call's own
    clock — an undated retirement would leave the window open forever, and a
    fabricated past instant would claim knowledge nobody has.
    """
    judgement_id = invalidation.judgement_id
    claim = _read_claim(sdk, judgement_id)
    if claim is MISSING:
        # ⛔ AN ABSENT CLAIM IS `alreadyRetired`, NOT A FIFTH OUTCOME (#12315).
        # A claim that does not exist cannot be stale, so the idempotent reading
        # applies. Reporting `alreadyRetired: False` here left the finding in NO
        # counts bucket at all (`retired`/`alreadyRetired`/`failed`/`gone` all
        # zero), so a sweep that met a claim deleted between the scan and the
        # retirement reported `stale: 1` with every counter at 0 — a summary a
        # consumer totalling the buckets cannot reconcile, which is the same rule
        # the `failed` and `gone` buckets exist to keep.
        #
        # ⛔ AND THE GOAL'S CACHE IS STILL OWED ITS RECONCILE (#12336). The
        # retirement did not happen, but the WORD the goal displays may still name
        # this claim — the vanish-mid-sweep race is exactly the shape where it
        # does, because the claim WAS live when the scan read it. No automatic
        # path repairs it later: `invalidate_stale` scans LIVE claims, so a goal whose last
        # claim is gone is never a finding again (measured: `goalState` stayed
        # `met` after the claim was deleted, through both the sweep and this
        # door). So the idempotent reading is reported AND the dependent state is
        # reconciled — otherwise `alreadyRetired: True` would assert a clean no-op
        # over a goal whose displayed word nothing backs.
        #
        # ⚠️ The two ALREADY-TERMINAL arms below owe the same reconcile, for the
        # same reason: a claim retired OUT OF BAND (a direct status write, a
        # purge) also leaves the goal's word naming it (#12336). ALL FOUR
        # already-retired arms — the two already-terminal ones, the absent one,
        # and the second vanish window below — go through `_reconcile_or_report`,
        # so an absent `cache` key can never be read as "clean".
        #
        # ⛔ THIS ARM IS THE ONE WITH NO CLAIM, SO IT HAS ONLY THE CALLER'S ID.
        # Every other arm sources the goal from the claim — `_reconcile_or_report`'s
        # docstring states that rule and the arm list it applies to. A claim-sourced
        # id is UNREACHABLE here BY CONSTRUCTION, because `_read_claim` folded to
        # `MISSING`. So `invalidation.goal_id` is all there is, and a caller that
        # omits it reconciles nothing — a supported shape, pinned by
        # `test_retiring_a_missing_claim_reports_it_rather_than_raising`, which
        # expects `cache["checked"] is False` on exactly this arm.
        # That is correct for THIS arm rather than a defect: there is no claim
        # whose `goalId` could have named the goal instead.
        # ⚠️ The reconcile is still ATTEMPTED, so the record still says what it
        # did — `cache` carries `checked: False` WITH the guard's own reason
        # (`no goalId was supplied to the reconcile`) instead of the key being
        # absent, which is what keeps an absent `cache` readable as "no reconcile
        # was attempted" on every other shape.
        # ⛔ AND THE RECORD NAMES THE GOAL IT RECONCILED, so the repair route the
        # `reconcile_goal_state_cache` docstring documents ("call this again with
        # the goal's id") is walkable from the record alone (#12315 review cycle
        # 16): a DIRECT `invalidate()` caller has no `report["stale"]` to join
        # against, and on this arm the claim is gone, so the id cannot be re-read
        # from the graph either.
        return {"invalidated": False, "judgementId": judgement_id,
                "alreadyRetired": True, "status": None, "found": False,
                "reason": invalidation.reason,
                "goalId": invalidation.goal_id,
                "cache": _reconcile_or_report(sdk, invalidation.goal_id),
                "detail": "no such judgement — nothing to retire"}
    if claim is None:
        raise RuntimeError(
            f"could not read judgement {judgement_id!r}; refusing to retire a "
            "claim whose state is unknown"
        )
    status = claim.get("status")
    if (isinstance(status, str) and status in TERMINAL_EXCLUDED_STATUSES) or \
            bool(claim.get("outdated")):
        terminal_goal_id = claim.get("goalId") or invalidation.goal_id
        return {"invalidated": False, "judgementId": judgement_id,
                "alreadyRetired": True, "status": status, "found": True,
                "reason": invalidation.reason,
                "goalId": terminal_goal_id,
                "cache": _reconcile_or_report(sdk, terminal_goal_id),
                "detail": f"already terminal ({status or 'outdated'})"}

    stamp = at or invalidation.effective_at or _now()
    try:
        sdk.retract_point(judgement_id, valid_to=stamp)
    except Exception as exc:
        # ⛔ READ THE GRAPH, NOT THE EXCEPTION CLASS (#11953's lesson). A
        # concurrent sweep can retire this claim between the read above and this
        # call; the ValueError that arrives is then indistinguishable from a
        # genuine refusal by its TYPE. "Already terminal now" is the idempotent
        # outcome; anything else keeps its original trace.
        after = _read_claim(sdk, judgement_id)
        if after is MISSING:
            # ⛔ THE SECOND VANISH WINDOW, and it is the SAME fact as the MISSING
            # arm above (#12315 review, P1). A claim deleted between the read at
            # the top of this function and `retract_point` makes the retraction
            # refuse (`sdk.py` raises `ValueError` for a missing point), so this
            # re-read is the ONE place the race is visible. Collapsing it to
            # `None` reported a benign no-op as `failed` AND skipped the goal's
            # reconcile — the #12336 residual every other already-retired arm
            # closes. An absent claim cannot be stale, so it takes the same
            # idempotent reading and the same reconcile: an absent outcome is
            # never a failure. THIS arm HAS read a claim — the one at the top of
            # this function, whose dict is still in scope even though the node it
            # described is gone — so it names the goal from it rather than
            # falling back to a caller id the sweep may not have supplied: the
            # vanished claim's goal is exactly the one whose word may still name
            # it (#12336).
            vanished_goal_id = claim.get("goalId") or invalidation.goal_id
            return {"invalidated": False, "judgementId": judgement_id,
                    "alreadyRetired": True, "status": None, "found": False,
                    "reason": invalidation.reason,
                    "goalId": vanished_goal_id,
                    "cache": _reconcile_or_report(sdk, vanished_goal_id),
                    "detail": "the judgement vanished before the retirement "
                              "landed — nothing to retire"}
        after_status = after.get("status") if isinstance(after, dict) else None
        after_flag = bool(after.get("outdated")) if isinstance(after, dict) else False
        if (isinstance(after_status, str)
                and after_status in TERMINAL_EXCLUDED_STATUSES) or after_flag:
            after_goal_id = after.get("goalId") or invalidation.goal_id
            return {"invalidated": False, "judgementId": judgement_id,
                    "alreadyRetired": True, "status": after_status,
                    "found": True,
                    "reason": invalidation.reason,
                    "goalId": after_goal_id,
                    "cache": _reconcile_or_report(sdk, after_goal_id),
                    "detail": f"already terminal ({after_status or 'outdated'})"}
        raise exc

    # The claim IS retired at this point, so a cache failure must not be swallowed
    # INTO silence nor allowed to mask the retirement: `_reconcile_or_report`
    # reports it, and the caller's repair route is to call the public, idempotent
    # `reconcile_goal_state_cache` again with the goal's id.
    #
    # ⛔ AND THIS ARM — the ONLY one that actually retires — owes the reconcile the
    # same claim-sourced goal id as the terminal arms (#12315 review cycle 12). It
    # was the last arm still reading `invalidation.goal_id` alone, and here the
    # skip is PERMANENT: the claim is terminal by the time a caller could notice,
    # so `invalidate_stale` never scans it again, and the skipped check moves
    # neither `counts.failed` nor `counts.gone` — a sweep reads clean over a goal
    # still displaying a word for the claim this very call retracted.
    resolved_goal_id = claim.get("goalId") or invalidation.goal_id
    cache_report = _reconcile_or_report(sdk, resolved_goal_id)

    result: dict[str, Any] = {
        "invalidated": True,
        "judgementId": judgement_id,
        "found": True,
        "reason": invalidation.reason,
        "detail": invalidation.detail,
        "validTo": stamp,
        "state": invalidation.state,
        "goalId": resolved_goal_id,
        "indicatorId": invalidation.indicator_id,
        "targetId": invalidation.target_id,
        "cache": cache_report,
    }
    return result


def reconcile_goal_state_cache(sdk: Any, goal_id: str | None) -> dict:
    """Point a goal's ``goalState`` cache at what still stands, or clear it.

    ``goalState`` is a CACHE of the newest live judgement (#11822's rule), so once
    a judgement is retired the cache must follow — otherwise the issue's own
    headline survives the fix: *"a goal can sit at ``met`` after the threshold
    moved"*, because the word is what a reader sees.

    ⛔ THE GATE IS ON THE WORD IN THE CACHE, NOT ON WHY THE JUDGEMENT WENT — and
    getting that wrong fails in BOTH directions, which is why it is stated as a
    rule rather than a special case. :data:`DERIVED_GOAL_STATES` names the only
    two words :func:`goal_judgement.record_judgement` can write. Everything else
    on a goal (``active``, ``blocked``, ``guideline``, ``superseded``,
    ``abandoned``) was put there by a human or by the create door, so it must be
    LEFT ALONE: clearing it would erase a decision nobody measured, and rewriting
    it would be worse still. Two failures the reason-keyed version had, both
    MEASURED by review:

    * a goal retired by its ``status`` (``archived``) whose cache said ``met``
      kept ``met`` forever, because the reason was ``goal-retired`` and that arm
      reconciled nothing — the issue's headline surviving one of its own three
      inputs, with no later pass able to repair it (the claim is no longer live,
      so no sweep revisits the goal);
    * a ``blocked`` goal lost the word when a ``target-revised`` retirement
      cleared the cache under it.

    So the rule is: a cache holding a word this engine wrote (or holding nothing)
    is reconciled — to the newest LIVE judgement's state, or CLEARED when nothing
    live remains, because 'no live judgement' is precisely the state the cache has
    no word for (#11822's :data:`goal_judgement.GOAL_STATE_CACHE` maps ``unknown``
    to no write for the same reason). A cache holding any OTHER word is preserved
    and reported.

    Idempotent and re-runnable: a caller can repair a cache by calling this again
    with the goal's id, which is the recovery route when a transient write failure
    left the word stale (``invalidate`` reports that rather than raising).

    ⛔ A GOAL THAT NO LONGER EXISTS IS REPORTED, NEVER REPORTED CLEARED. "The goal
    node is gone" and "the goal holds no cached word" are different facts, and
    folding them (``get_entity(...) or {}``) made this function answer
    ``{"checked": True, "cleared": True}`` for a deleted node — a write that lands
    nowhere, because ``update_entity`` on a missing id is a silent no-op. MEASURED
    by review: a sweep over a deleted goal reported ``cleared: True``, and the
    documented repair route reported it again, forever. The absent case returns
    ``checked: False`` with ``gone: True``, which ``invalidate_stale`` surfaces as
    ``counts.gone`` — a sweep may not read clean while a reconcile did not happen.
    """
    if not goal_id:
        # ⛔ NOT "the claim names no goal" — this function cannot know that: it
        # was handed nothing. Every arm that reads a claim sources the id from it
        # (the claim-first rule AND its arm list are stated in
        # `_reconcile_or_report`'s docstring, which is the single place to keep
        # them true — a fifth arm must update that docstring, not this comment),
        # so a call that lands here means no caller supplied one — and the message
        # says exactly that rather than asserting a fact about a claim it never
        # read.
        return {"checked": False,
                "reason": "no goalId was supplied to the reconcile"}
    try:
        goal = sdk.get_entity(goal_id)
    except Exception as exc:
        return {"checked": False,
                "error": f"{type(exc).__name__}: {exc}"}
    if not goal:
        # ⛔ NOT the same fact as "the goal holds no derived word": there is no
        # node to point at anything, so nothing was reconciled. Reported as
        # `gone` so a sweep cannot read clean on a retirement it could not follow.
        # A falsy read IS the absent case: `get_entity` answers `{}` for an id
        # with no node (measured — a deleted node and a never-existent id both
        # return `{}`, never `None`), which is why the caller must use the same
        # predicate the read paths do rather than truthiness of a prop.
        return {"checked": False, "gone": True,
                "reason": f"the goal {goal_id!r} no longer exists — its cache "
                          "cannot be reconciled"}
    current = goal.get("goalState")
    if current is not None and current not in DERIVED_GOAL_STATES:
        return {"checked": True, "goalState": current, "preserved": True,
                "from": None, "cleared": False}
    remaining = latest_judgement(sdk, goal_id=goal_id)
    if remaining:
        state = remaining.get("judgementState")
        cached = GOAL_STATE_CACHE.get(state) if state else None
        sdk.update_entity(goal_id, goalState=cached)
        return {"checked": True, "goalState": cached,
                "from": remaining.get("id"), "cleared": cached is None}
    sdk.update_entity(goal_id, goalState=None)
    return {"checked": True, "goalState": None, "from": None, "cleared": True}


def invalidate_stale(
    sdk: Any,
    *,
    goal_id: str | None = None,
    indicator_id: str | None = None,
    target_id: str | None = None,
    at: str | None = None,
    dry_run: bool = False,
    limit: int = 10000,
) -> dict:
    """Retire every derived state in scope whose justification is gone.

    This is the call a trigger (once #7860 **D13** rules one) would make, and the
    call a repairing caller makes today. It does NOT re-derive: after a
    retirement the caller's next ``judge_and_record`` mints the state for the new
    input, which is the derivation half of capability 8.

    ``dry_run=True`` reports what it WOULD retire and writes nothing — the safe
    preview for an unscoped sweep, which is the one shape that can touch every
    judgement in a graph.

    Returns, always: ``scanned`` (live judgements read), ``stale`` (the findings,
    each :meth:`Invalidation.as_dict`), ``invalidated`` (what
    :func:`invalidate` returned, per finding — including a per-finding ``error``
    when one retirement refused, so the rest of the sweep's results survive),
    ``undecidable`` (verdicts the module could not decide — surfaced, never
    dropped), ``counts`` (including ``failed`` and ``gone``) and ``truncated``
    (true when the scan hit ``limit``, i.e. the sweep did not see the whole graph).
    A refused finding's record carries ``error``, ``found: None``, and no
    ``cache``, because no reconcile ran.

    ⛔ ``goalId`` IS PRESENT EXACTLY WHERE ``cache`` IS, and absent on the refusal
    record: it names the goal a reconcile RAN AGAINST, so a refusal — which
    reconciled nothing — has none to name (#12315 review cycle 17). That is why
    a caller walking ``invalidated`` for the repair route must read
    ``record.get("goalId")``: every record :func:`invalidate` returns carries the
    key (the absent arm's is the caller's own id, possibly ``None``), and the
    sweep's one synthetic refusal record does not, because it has no ``cache``
    either. An omitted key is otherwise this module's documented defect class —
    see the ``found`` paragraph below.

    ``found`` is PRESENT on every shape, so
    ``record["found"]`` never raises: ``False`` means NO CLAIM NOW — absent at
    the door's first read,
    OR read and then deleted before ``retract_point`` landed — ``True`` a claim
    the retirement read, ``None`` the refusal. ⚠️ TWO arms share ``False`` and the
    only thing that separates them is ``detail`` prose, which is not a contract:
    never parse it to tell "my scope was stale" from "a peer deleted the claim
    under the retirement". Never test falsiness — ``None`` and ``False`` are
    different facts.

    ⚠️ ``found: None`` does NOT mean the claim was ABSENT, and it does not mean
    the sweep measured anything: it is the REFUSAL marker. :func:`invalidate` has
    exactly TWO raise sites — ``claim is None`` before the retirement, and the
    trailing ``raise exc`` after a ``retract_point`` refusal — and the re-read
    that follows a refusal has THREE outcomes:

    1. it came back terminal → :func:`invalidate` RETURNS ``found: True``;
    2. it came back ``MISSING`` → it RETURNS ``found: False`` (the SECOND vanish
       window: the claim was deleted between the read and the retirement, so the
       idempotent reading applies and the goal's cache IS reconciled — #12315);
    3. it came back PRESENT and not terminal (a genuine refusal of a live claim),
       **or the re-read itself failed (``after is None``, so both terminal tests
       cannot answer)** → it RE-RAISES, the exception carries no field, and the
       sweep reports ``found: None`` with ``error`` naming the raise. Those two
       are NOT the same fact: in the first the answer is known (``True`` — the
       retirement read the claim, twice), in the second it is not, and the
       record cannot tell them apart.

    So read a ``found: None`` record carrying an ``error`` as "the retirement
    REFUSED" — never as "the claim was absent", never as a retirement that
    landed — and read ``error`` for which raise it was. The first raise site
    (``claim is None``) means the claim was never readable, so the answer is
    genuinely unknown and ``None`` is the only honest value.

    ``counts.gone`` counts the findings whose goal NO LONGER EXISTS, so its
    ``goalState`` cache could not be reconciled: there is no node to reconcile it
    on. FOUR shapes set it, and it means the same thing in all of them — "the
    cache could not be reconciled", never "a retirement landed": a retirement
    that DID land whose cache follow-up could not; #12315's ABSENT arm (``found:
    False``), where no retirement was needed at all; #12315's SECOND VANISH
    window (also ``found: False`` — the claim was deleted between the read and
    the retirement, so the goal whose word named it may be gone too); and either
    ALREADY-TERMINAL
    arm (``found: True``) whose goal has also gone, where the retirement happened
    out of band. ⛔ So ``gone`` is NOT attributable from ``retired``/``found``
    alone (measured on the FOURTH, the already-terminal arm: a claim that BECOMES ``retracted`` between the
    scan and the retirement, with its goal deleted in that same window, gives
    ``{'stale': 1, 'retired': 0, 'alreadyRetired': 1, 'gone': 1, 'failed': 0}`` —
    both edits made BEFORE the sweep instead give ``stale: 0`` and no finding at
    all, because the scan reads live judgements only). It is neither a failure of
    a retirement nor a success of the reconcile, and it exists so a sweep over
    deleted goals cannot read clean.

    ⛔ THE BUCKETS ARE PER-FINDING PREDICATES, NOT A PARTITION — do not read a
    bucket total as a finding count. A retirement that landed and whose cache
    reconcile then failed is counted in BOTH ``retired`` and ``failed`` (and a
    retired claim whose goal is gone in both ``retired`` and ``gone``), which is
    correct: each number answers its own question.

    In a sweep that actually retires (``dry_run=False``) every finding lands in
    **at least one** bucket — the failure #12315 records was an arm landing in
    none. ⛔ ``counts.alreadyRetired`` names THREE distinguishable facts: a claim
    that was FOUND and already terminal; a claim that was NOT FOUND at all
    (#12315's arm); and a claim that WAS read and then vanished before the
    retirement landed (the second vanish window). ``found`` discriminates READ
    (``True``) from NO CLAIM NOW (``False``) — it does NOT separate the second
    fact from the third, which both report ``False``. ``status`` is NOT a
    discriminator either:
    a claim whose terminal predicate was the legacy ``outdated`` flag reports
    whatever its status property holds (``None``, or a stale word such as
    ``live``), so that field cannot separate it from the ``found: False`` arms. A retirement
    that REFUSED carries ``error``, ``found: None`` and no ``cache``; ``found`` is
    present on all three shapes, so test ``found is False`` (no claim now), ``is
    True`` (read arms) or ``is None`` (refused — the answer never reached the
    sweep, so read ``error``) rather than falsiness, which cannot tell ``False``
    from ``None``.

    ⚠️ That rule does NOT hold for ``dry_run=True``, and the asymmetry is
    by design rather than an unhandled arm: a dry run retires nothing, so
    ``invalidated`` stays empty and the four RETIREMENT buckets — ``retired``,
    ``alreadyRetired``, ``failed``, ``gone`` — are 0 by construction, while
    ``scanned``, ``stale`` and ``undecidable`` keep reporting what the scan saw
    (``stale`` names what it WOULD have retired and ``undecidable`` > 0 is a real
    finding, not a counter that should be zero — the dry-run test asserts the
    ``stale`` 1 / ``invalidated`` empty pair). So reconcile the four retirement
    buckets against ``stale`` ONLY when ``dryRun`` is false, and read
    ``scanned``/``undecidable`` on their own terms either way.
    """
    found = verdicts(sdk, goal_id=goal_id, indicator_id=indicator_id,
                     target_id=target_id, limit=limit)
    stale = [v.invalidation for v in found if v.invalidation is not None]
    undecidable = [{"judgementId": v.judgement_id, "why": v.undecidable}
                   for v in found if v.undecidable is not None]
    invalidated: list[dict] = []
    if not dry_run:
        for inv in stale:
            # ⛔ ONE FINDING'S FAILURE MUST NOT DISCARD THE REST. A retirement is
            # an independent write per claim, so a genuine refusal on one (a
            # concurrent sweep, an infra failure) is recorded ON THAT FINDING and
            # the sweep carries on: an exception out of here would throw away the
            # results of every finding already retired, and a caller could not
            # tell which of them landed.
            try:
                invalidated.append(invalidate(sdk, inv, at=at))
            except Exception as exc:
                # ⛔ `found` IS PRESENT HERE, AS `None`, AND NOT OMITTED (#12315).
                # A caller reads this record first when the sweep reports a
                # failure, and omitting the key made the documented discriminator
                # `record["found"]` a KeyError there, while `.get("found")` read
                # `None` — the conflation of "absent" with "unnamed" that #12315
                # is about, one level out.
                # ⚠️ `None` says the SWEEP could not tell, not that the answer is
                # unknowable for every raise: `invalidate` raises from two places
                # — `claim is None` (the read failed: genuinely unknown) and the
                # trailing `raise exc` when `retract_point` refused a claim this
                # call had ALREADY read as live (answer `True`) UNLESS the re-read
                # that follows found it gone or unreadable (answer unknown, and
                # that is the vanish-mid-sweep race this PR is about). The
                # exception does not carry which, so the conservative `None` is
                # reported and `error` carries the raise. `cache` stays absent: no
                # reconcile ran.
                # ⛔ PRECEDENT STATED AS A DIFFERENCE, NOT AN IDENTITY: #7818 fixed
                # the same CLASS one surface over — an omitted `found` key read
                # through `.get()` as honest absence when a candidate WAS found.
                # That surface closed it with a boolean plus a `status` field; this
                # one has a third shape whose value the sweep does not have, so it
                # is `None`, and a `.get()`/falsiness test here reads "refused" as
                # "absent".
                invalidated.append({
                    "invalidated": False,
                    "alreadyRetired": False,
                    "found": None,
                    "judgementId": inv.judgement_id,
                    "reason": inv.reason,
                    "detail": inv.detail,
                    "error": f"{type(exc).__name__}: {exc}",
                })
    return {
        "scanned": len(found),
        "truncated": len(found) >= limit,
        "dryRun": dry_run,
        "stale": [inv.as_dict() for inv in stale],
        "invalidated": invalidated,
        "undecidable": undecidable,
        "counts": {
            "scanned": len(found),
            "stale": len(stale),
            "retired": sum(1 for r in invalidated if r.get("invalidated")),
            "alreadyRetired": sum(1 for r in invalidated if r.get("alreadyRetired")),
            # ⛔ ``gone`` IS A CACHE FACT, NOT A RETIREMENT FACT. It counts the
            # findings whose goal no longer exists, so its ``goalState`` could not
            # be reconciled — there is no node to reconcile it on. FOUR shapes set
            # it and they mean the same thing in all four (see the
            # ``invalidate_stale`` docstring, which enumerates them):
            #   1. a retirement that DID land whose cache follow-up could not;
            #   2. #12315's ABSENT arm (``found: False``), where no retirement was
            #      needed at all (``retired: 0`` — pinned by
            #      `test_an_absent_claim_whose_GOAL_is_gone_lands_in_GONE_too`);
            #   3. #12315's SECOND VANISH window (also ``found: False`` — the claim
            #      was deleted between the read and the retirement, so the goal
            #      whose word named it may be gone too);
            #   4. an ALREADY-TERMINAL arm (``found: True``) whose goal has also
            #      gone, where the retirement happened out of band.
            # So a maintainer reading this counter may NOT infer that a retirement
            # landed.
            "gone": sum(1 for r in invalidated
                        if (r.get("cache") or {}).get("gone") is True),
            # ⛔ A FAILED CACHE RECONCILE IS A FAILURE OF THIS SWEEP. It is
            # reported per finding (`cache.error`) but a summary that counts only
            # per-finding retirements reads CLEAN while the goal still shows a
            # derived word — which is #11926's own headline, surviving a sweep that
            # said it was fine. Counted here so the top-level numbers cannot say
            # "nothing went wrong" while a nested key does.
            "failed": sum(1 for r in invalidated
                          if r.get("error")
                          or (r.get("cache") or {}).get("error")),
            "undecidable": len(undecidable),
        },
    }


def mark_definition_change(
    sdk: Any,
    indicator_id: str,
    *,
    at: str | None = None,
    definition: str | None = None,
) -> dict:
    """Declare that the indicator's definition changed — and MARK the boundary.

    This is capability 9's own missing piece: *"older readings are not comparable,
    but nothing marks the boundary"*. The boundary is an instant, stored as
    :data:`goal_judgement.DEFINITION_BOUNDARY_PROP` on the indicator. Two
    consequences, both of which is why the boundary is a stamp and not a digest of
    the node's properties:

    * :func:`goal_judgement.judge` stops serving readings taken before it, so the
      next judgement is derived only from readings measured under the new
      definition — the trend half of the defect;
    * :func:`invalidate_stale` retires the judgement that was derived before it —
      the stale-state half.

    ⛔ The boundary is MONOTONE FORWARD, and a backwards move is refused. A
    boundary may only move later: moving it earlier would un-comparable readings
    that a reader already treated as comparable, and — because a retirement is
    terminal — it could never un-retire the claims the earlier boundary already
    justified retiring. Refusing here is the only place that can still say so.

    ⚠️ THAT RULE IS A DOOR RULE, NOT A GRAPH INVARIANT, and the difference is
    stated rather than implied: the stamp is an ordinary indicator property, so
    `sdk.update_entity(indicator_id, definitionChangedAt=…)` (or a raw write) can
    still move it backwards. What this door guarantees is that the SUPPORTED path
    cannot, and that the state it writes is always an orderable instant — the two
    things that made the pre-review version of this function able to store a
    boundary that disabled the indicator's derivation permanently AND refused
    every later repair (measured: `at='TBD'` stored as-is, then every stamp
    "cannot be ordered", so `judge` served no readings forever).

    So the stamp is VALIDATED here, exactly like the one it replaces:

    * a stamp that names no instant is REFUSED (`at=None` keeps the clock) — a
      boundary nobody can order is not a boundary;
    * a STORED value that names no instant is not treated as the monotone floor:
      it can only be a corrupt/legacy stamp, so a well-formed stamp is allowed to
      replace it, and the repair is reported as ``repaired`` rather than silently
      passing a monotonicity check that never happened.

    ``definition`` optionally stores the new definition's prose (the reader's
    answer to "what does this number now mean?"). It is a convenience, not the
    mechanism: the boundary alone is what the derivation and the sweep read.
    """
    if not indicator_id:
        raise ValueError(
            "mark_definition_change needs the indicator whose definition changed"
        )
    node = sdk.get_entity(indicator_id) or {}
    if not node:
        raise ValueError(
            f"indicator {indicator_id!r} does not exist — a boundary cannot be "
            "marked on a node nothing backs."
        )
    # ⛔ ABSENCE IS THE ONLY DEFAULT. `at or _now()` would have made `at=""` (and
    # `at=0`) silently mean "the clock" — the same coalescing `Target.from_node`
    # refuses for `dwell`, and here it would hide a caller's broken instant behind
    # a plausible one.
    stamp = _now() if at is None else at
    # ⛔ The stamp must NAME AN INSTANT. `instant_at_or_after(stamp, stamp)` is the
    # self-comparison that asks exactly that, and it is the repo's one notion of
    # "parseable instant" rather than a second one spelled here.
    if not instant_at_or_after(stamp, stamp):
        raise ValueError(
            f"refusing to mark the definition boundary of {indicator_id!r} at "
            f"{stamp!r}: a boundary must name an instant. An unorderable stamp "
            "would exclude EVERY reading from the derivation (no reading can "
            "prove it is after a moment nobody can place) and would then refuse "
            "every later repair, silently stopping the indicator."
        )
    previous = node.get(DEFINITION_BOUNDARY_PROP)
    repaired = False
    if previous is not None:
        if instant_at_or_after(previous, previous):
            if not instant_at_or_after(stamp, previous):
                raise ValueError(
                    f"refusing to move the definition boundary of "
                    f"{indicator_id!r} from {previous!r} to {stamp!r}: the "
                    "boundary is MONOTONE FORWARD. An earlier boundary would "
                    "make readings comparable that were not, and no retirement "
                    "it already justified can be undone."
                )
        else:
            # A stored value that names no instant is not a monotone floor: it is
            # a corrupt/legacy stamp nothing can be ordered against, so a
            # well-formed stamp REPLACES it. Reported, so the repair is visible.
            repaired = True
    props: dict[str, Any] = {DEFINITION_BOUNDARY_PROP: stamp}
    if definition is not None:
        props["definition"] = definition
    sdk.update_entity(indicator_id, **props)
    return {
        "indicatorId": indicator_id,
        DEFINITION_BOUNDARY_PROP: stamp,
        "previous": previous,
        "repaired": repaired,
        "definition": definition,
    }


def _read_claim(sdk: Any, judgement_id: str) -> Any:
    """The claim's properties, :data:`MISSING`, or ``None`` for a failed read."""
    try:
        node = sdk.get_entity(judgement_id)
    except Exception:
        return None
    return node or MISSING
