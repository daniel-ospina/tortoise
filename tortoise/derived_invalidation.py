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
  ``check-consistency`` reds a FAITHFUL graph permanently. The block below
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
    "GONE_UNPROVEN",
    "NOT_DURABLE_REASON",
    "RETIRED_GOAL_STATES",
    "STALE_REASONS",
    "Invalidation",
    "Verdict",
    "invalidate",
    "invalidate_stale",
    "invalidation_for",
    "mark_definition_change",
    "reconcile_goal_state_cache",
    "reconcile_goal_states",
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
       everywhere else.
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
    stamping it onto the claim is deliberately NOT done here, and the block below
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
        return {"invalidated": False, "judgementId": judgement_id,
                "alreadyRetired": False, "reason": invalidation.reason,
                "detail": "no such judgement — nothing to retire"}
    if claim is None:
        raise RuntimeError(
            f"could not read judgement {judgement_id!r}; refusing to retire a "
            "claim whose state is unknown"
        )
    status = claim.get("status")
    if (isinstance(status, str) and status in TERMINAL_EXCLUDED_STATUSES) or \
            bool(claim.get("outdated")):
        return {"invalidated": False, "judgementId": judgement_id,
                "alreadyRetired": True, "status": status,
                "reason": invalidation.reason,
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
        after_status = after.get("status") if isinstance(after, dict) else None
        after_flag = bool(after.get("outdated")) if isinstance(after, dict) else False
        if (isinstance(after_status, str)
                and after_status in TERMINAL_EXCLUDED_STATUSES) or after_flag:
            return {"invalidated": False, "judgementId": judgement_id,
                    "alreadyRetired": True, "status": after_status,
                    "reason": invalidation.reason,
                    "detail": f"already terminal ({after_status or 'outdated'})"}
        raise exc

    try:
        cache_report = reconcile_goal_state_cache(sdk, invalidation.goal_id)
    except Exception as exc:
        # The claim IS retired at this point, so this failure must not be
        # swallowed INTO silence nor allowed to mask the retirement: report it.
        # `reconcile_goal_state_cache` is public and idempotent, so the caller's
        # repair route is to call it again with the goal's id.
        cache_report = {"checked": False,
                        "error": f"{type(exc).__name__}: {exc}"}

    result: dict[str, Any] = {
        "invalidated": True,
        "judgementId": judgement_id,
        "reason": invalidation.reason,
        "detail": invalidation.detail,
        "validTo": stamp,
        "state": invalidation.state,
        "goalId": invalidation.goal_id,
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

    ⚠️ AND ``gone`` IS NOT PROOF OF ABSENCE. It means "``sdk.get_entity`` could not
    resolve this id", which an UNADDRESSABLE node produces exactly as a deleted one
    does: this function resolves only the canonical labels, each by its OWN primary
    identity prop (``Event`` is keyed by ``eventId``), so a ``:Widget``, an
    unlabelled node, or a ``:Event {id:…}`` carrying no ``eventId`` all come back
    the same as a node that is really gone. This function cannot tell those apart
    and does not try — that is the caller's job, and a caller that needs proof of a
    deletion must OBSERVE the graph (:data:`_GONE_PROBE` /
    :data:`GONE_UNPROVEN`, #12336/#12457) rather than read this flag as one.
    ⛔ ``invalidate_stale``'s ``counts.gone`` still derives straight from this
    answer, un-probed — #12534.
    """
    if not goal_id:
        return {"checked": False, "reason": "no goalId on the retired claim"}
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


#: Candidates are selected by the PROP, never by the node's kind: this module's
#: gate is the WORD a cache holds (:data:`DERIVED_GOAL_STATES`), so a node holding
#: a word ``record_judgement`` can write IS a cache of a judgement — whatever its
#: ``kind``/``objectKind`` says, including nothing. Selecting by kind would let a
#: goal whose kind is absent or mislabelled keep a stale word forever, which is
#: the read-side twin of the bug being repaired.
#:
#: ⛔ ``NOT n:Point`` — AN EXCLUSION BY THE ONE PROPERTY THAT MATTERS, NOT A
#: WHITELIST OF LABELS. A legacy goal ``:Point`` can hold a derived word (the prop
#: is declared for the point write path, #7856, ``projection/entities.py``) and its
#: repair cannot be durable: ``sdk.update_entity``'s generic POINT branch journals
#: only the annotator dims and ``mentions`` (its own SCOPE note, ``tortoise/sdk.py``
#: — *"Every other prop this generic surface writes live is still unfolded on
#: replay"*), so a ``goalState`` clear there is live-only. MEASURED on this lane,
#: one goal of each label (private test lane, ``check_consistency`` +
#: ``rebuild_all``):
#:
#:     [OBJECT] before_ok=True  report cleared=True  live=None
#:              after_ok=True   divergence=None      rebuilt=None
#:     [POINT]  before_ok=True  report cleared=True  live=None
#:              after_ok=False  divergence='unrecorded-mutation'  rebuilt='met'
#:
#: i.e. on a Point the sweep would (a) red the journal/projection parity with a
#: diagnostic that blames a rogue writer it is itself, and (b) be undone wholesale
#: by the next rebuild — reporting `cleared` on every pass of a repair-then-rebuild
#: loop. That is the same live-only-prop hazard this module already refuses
#: elsewhere (the block below :data:`STALE_REASONS`), so a node carrying that write
#: branch is not repaired here until the point prop write is journalled (the #4094
#: class, its own lane).
#:
#: ⛔ AND THE EXCLUSION IS BY THAT LABEL ALONE — everything else that can hold the
#: word is INCLUDED, because a whitelist of labels is the shape this module calls
#: out as an invisible missing case: a ``:Subject`` goal (the entity door routes a
#: ``subject`` kind there) legitimately takes an engine-written ``goalState`` —
#: MEASURED: ``create_subject`` + ``update_entity(goalState='met')`` survives a
#: ``rebuild_all``, so it is a repairable, durable candidate — and a
#: ``MATCH (n:Object)`` sweep reported ``scanned: 0`` over it, a clean summary over
#: a stale word. The excluded population is COUNTED instead
#: (:data:`_GOAL_STATE_UNDURABLE`), so a sweep over a graph that holds only
#: unrepairable words is distinguishable from a sweep over an empty graph.
#:
#: ``LIMIT`` is asked for ``limit + 1`` rows so the caller can tell a scan that
#: exactly filled its bound from one that OVERFLOWED it. That is NOT the same
#: predicate :func:`invalidate_stale` answers with ``truncated`` — that one reads
#: ``len(found) >= limit``, so a scan that exactly filled its bound reports
#: ``truncated: True`` there and ``False`` here. This side is the deliberate one
#: (a full scan is not a truncated one); the divergence is named so nobody
#: "harmonises" the two into the lossy form. ONE query also means a node can only
#: be enumerated once: the repo's `DEDUPE CONTRACT` (``live.py``) exists because an
#: id-keyed read can be issued PER LABEL, and a ``:Point:Object`` node returned by
#: two scans is counted, reported and repaired twice.
_GOAL_STATE_CANDIDATES = (
    "MATCH (n) WHERE NOT n:Point AND toStringOrNull(n.goalState) IN $words "
    "RETURN n.id AS id, n.goalState AS word ORDER BY n.id LIMIT $lim"
)

#: Asked of the graph when the door answers ``gone`` — see :data:`GONE_UNPROVEN`.
_GONE_PROBE = "MATCH (n) WHERE n.id = $id RETURN n.id LIMIT 1"

#: Why a ``gone`` answer from the door is not, on its own, proof of a deletion.
#:
#: :func:`reconcile_goal_state_cache` decides ``gone`` by ``sdk.get_entity``, which
#: resolves only the CANONICAL labels and resolves each by its OWN primary identity
#: prop (``projection._CANONICAL_ENTITY_ID_PROPS``: ``Event`` is keyed by
#: ``eventId``). This scan is an EXCLUSION (``WHERE NOT n:Point``) that names its
#: candidates by ``n.id`` alone. The two predicates are not the same, so the door's
#: "I could not resolve this id" and "this node is gone" collapse into ONE answer:
#: a node it cannot address is indistinguishable there from a node that does not
#: exist.
#:
#: MEASURED (#12412 review cycle 5; #12457 review cycle 2): ``:Widget
#: {id:'w-1', goalState:'met'}``, an unlabelled ``{id:'nolabel-1', goalState:'met'}``
#: and a ``:Event {id:'e-1', goalState:'met'}`` (id present, ``eventId`` absent) each
#: came back ``{'action': 'gone', 'reason': "the goal 'w-1' no longer exists"}``
#: while ``MATCH (n {id:'w-1'}) RETURN n.goalState`` still answered ``met``. The
#: payload asserted a DELETION that never happened — the same false PASS the rest of
#: this module exists to prevent, in its most literal form.
#:
#: ⛔ SO THE GRAPH IS ASKED, one id-wide read, ONLY on the already-rare ``gone`` arm.
#: A node that is still there is UNADDRESSABLE — counted with the no-``id`` shape —
#: and ``gone`` then means what the GRAPH says: no node carries this id. ⚠️ That is
#: NOT what the door's own un-probed ``gone`` means ("could not resolve this id"),
#: which is the whole reason the probe exists — so the sweep's ``gone`` and
#: ``invalidate_stale``'s ``counts.gone`` are the same bucket NAME and different
#: predicates (#12534). This is deliberately an
#: OBSERVATION rather than a second spelling of the door's label/identity table:
#: restating that table here is how the two would drift apart again, and a
#: label-membership filter over-excludes (it would sweep every ``:Event`` into
#: ``unaddressable``, including the ones that carry their real ``eventId`` and ARE
#: repairable — measured on #12457's review).
#:
#: ⚠️ THE COST, stated rather than implied by "one read": the probe carries no
#: label, so a MISS takes an All-Node scan — ``LIMIT 1`` bounds the ROWS RETURNED,
#: not the scan. It runs only where the sweep was already about to write a
#: falsehood, and the label-less candidate scan immediately before it is the same
#: order of work, so a handful of gone goals is not a cost change. A graph
#: carrying MANY genuinely-deleted goals makes it O(N) per goal; the honest remedy
#: there is an ``id`` index over all labels, not weakening this probe.
#:
#: ⛔ THIS IS A DELIBERATE DEPARTURE, and it is named here because the shape is
#: forbidden elsewhere in this codebase: `sdk.py` carries the rule twice, verbatim
#: as "⛔ LABEL-SCOPED probes, never `MATCH (n) WHERE n.id = $id AND …`" (its
#: entity-resolution path and its `_update_entity` write path), because FalkorDB
#: indexes are label-scoped and an unlabelled pattern can use no label's ``id``
#: index. This probe breaks that rule on purpose, for the one question a labelled
#: pattern cannot ask: presence on ANY label — which is exactly what makes a node
#: unaddressable to the door. ⛔ An earlier version of this comment cited "the note
#: at projection/__init__.py", which says nothing of the kind (#12457 review, cycle
#: 5, verified: that module's index notes are about an un-indexed PROP inside a
#: LABELLED branch); the rule it overrides is the `sdk.py` one above, so an auditor
#: can tell a measured trade-off from an accident.
#: can tell a measured trade-off from an accident.
#:
#: ⚠️ WHAT THE PROBE ESTABLISHES, stated here rather than inside the payload string:
#: it is id-WIDE (``MATCH (n) WHERE n.id = $id``), so it proves that A node with
#: this id exists — not, strictly, that the GOAL node does. That asymmetry is kept
#: deliberately: a same-id stranger outliving a real deletion can only make the
#: sweep under-report a ``gone``, never assert one. ⛔ ``GONE_UNPROVEN`` below is
#: CONSUMER-FACING (it is assigned verbatim to ``record["reason"]``), so it is plain
#: prose with no Sphinx roles and no emoji — the rationale lives in this comment,
#: the way ``NOT_DURABLE_REASON``'s does.
GONE_UNPROVEN = (
    "a node carrying this goal id is still in the graph, but the entity surface "
    "cannot resolve it by that id, so its cache was NOT reconciled and it is "
    "counted rather than reported gone"
)


#: The population the sweep DECLINES, counted (never repaired) so its summary
#: cannot read clean over words it could not durably repair: a node carrying the
#: unwrapped POINT write branch whose cached word is derived. Count-only: one
#: integer, no per-node record, because there is nothing a caller could do with
#: one beyond knowing the sweep did not cover it — the prerequisite is the
#: journalled point write, not a caller-side action.
_GOAL_STATE_UNDURABLE = (
    "MATCH (n:Point) WHERE toStringOrNull(n.goalState) IN $words "
    "RETURN count(n)"
)

#: What ``counts.notDurable`` means, carried in the payload rather than only in
#: prose: a number a consumer cannot interpret is a number it will ignore, and
#: ignoring this one is exactly the false PASS the counter exists to prevent.
NOT_DURABLE_REASON = (
    "a goal on a legacy :Point holds a derived goalState and is NOT repaired: "
    "the generic point write branch is unjournalled for goalState, so the "
    "repair would be reverted by the next rebuild (needs a replayable record, "
    "the #4094 class)"
)


def reconcile_goal_states(
    sdk: Any,
    *,
    dry_run: bool = False,
    limit: int = 10000,
) -> dict:
    """Repair every goal whose CACHED WORD does not stand — the GOAL-side sweep (#12336).

    ⛔ WHY A SWEEP OVER GOALS AND NOT OVER CLAIMS. ``invalidate_stale``
    enumerates LIVE claims, so a claim that stops being live by any route that
    does not go through :func:`invalidate` — a hard delete (a purge, a concurrent
    cleanup), or a retirement written out of band — takes its goal out of every
    later scan. The cached word then names a claim nothing backs, and NO later
    pass revisits the goal: :func:`reconcile_goal_state_cache`'s own docstring
    records the same shape for the goal-retired arm (*"no later pass able to
    repair it (the claim is no longer live, so no sweep revisits the goal)"*).
    Repairing the `alreadyRetired` arm inside the door closes the BUCKET, not
    this: nobody calls the door on a route it never sees. So the enumeration has
    to start from the GOAL — the surface that displays the word — and this is
    that sweep.

    Two shapes are repaired, and they are the same predicate seen twice:

    * **unsupported** — the goal holds a derived word and nothing live backs it
      (route A: the claim was deleted; route B: it was retired out of band), so
      the cache is CLEARED, because 'no live judgement' is precisely the state
      the cache has no word for (:data:`goal_judgement.GOAL_STATE_CACHE` maps
      ``unknown`` to no write for the same reason);
    * **lagging** — the goal holds a derived word while the newest LIVE
      judgement maps to a different one, so the cache is REWRITTEN to the live
      word. This is a real, documented shape rather than a hypothetical: a flip
      whose cache write was refused leaves the PREVIOUS word under a live claim
      (``goal_judgement._cache_then_arrow`` names that arm), and its self-healing
      pass only runs if something re-derives the goal — which nothing does while
      #7860's D13 ("what triggers the loop?") is unruled.

    Both go through the public, idempotent :func:`reconcile_goal_state_cache`, so
    the repair routes through ONE write door and inherits its gates; it adds no
    new vocabulary, and it writes nothing itself. ⚠️ The ``consistent``
    short-circuit BELOW does re-spell the door's mapping
    (:data:`goal_judgement.GOAL_STATE_CACHE`) to avoid a door call per already-
    correct goal — it models none of the door's ``preserved`` gate, which is why
    that branch can only ever REPORT ``consistent`` and never write, and why its
    record's ``to`` is the SCAN-time word rather than a re-read (a word written in
    between is reported as matching a value the graph no longer holds; nothing is
    cleared or rewritten there, so it is a stale report, not a false PASS). A
    change to the door's step-4 gate has to be reflected in that mapping; the two
    are not mechanically linked.

    ⛔ IT IS NOT WIRED INTO ANYTHING, deliberately: it is the repairing caller's
    call today and the trigger's call once D13 rules one — the same posture as
    :func:`invalidate_stale`, and the same reason `mark_definition_change` fires
    on nothing.

    ⛔ WHAT IT DOES NOT REPAIR, stated here rather than discovered by a reader who
    trusted a wider promise (each is a named residual, not a silent gap):

    * **a legacy goal ``:Point``** — a derived word there can be a candidate and is
      NOT enumerated, because its repair cannot be durable: the generic POINT
      write branch journals only the annotator dims and ``mentions``, so a
      ``goalState`` clear there is live-only, reds the journal/projection parity
      and is reverted by the next rebuild. The measurement and the reason are on
      :data:`_GOAL_STATE_CANDIDATES`; the prerequisite is a replayable
      ``goalState`` record for the point path (#4094's class). ⛔ **It is COUNTED,
      not silently skipped** — :data:`_GOAL_STATE_UNDURABLE` returns how many such
      nodes hold a derived word, reported as ``counts.notDurable`` with
      :data:`NOT_DURABLE_REASON` beside it, so a sweep over a graph whose only
      stale words are unrepairable is distinguishable from a sweep over an empty
      graph. That distinction is the whole rule `counts.failed` exists to keep.
    * **a goal holding NO word while a live judgement stands** (the cache write of
      a FIRST judgement was refused, or the goal was created without a
      ``goalState``). The candidate set is "holds a derived word", so that shape
      is not enumerated — the title says CACHED WORD for this reason. The door it
      delegates to CAN write that shape (its gate falls through on ``None``), so
      the repair route is the door plus the goal's id, or the next
      ``judge_and_record`` pass; widening the sweep to it means enumerating goals
      by kind, which is a separate decision rather than a filter tweak.
    * **the DOOR-side partials this sweep does not supply** — :func:`invalidate`
      still returns before its reconcile on three paths (no such judgement, an
      already-terminal claim, and a retirement refused because a concurrent sweep
      got there first), so the issue's own route — ``invalidate`` →
      ``alreadyRetired: True`` → the word still standing — still reproduces through
      the door. Those arms are #12315's (``alreadyRetired``) and are not duplicated
      here; this sweep is the graph-wide repair, and a caller that needs the DOOR
      to be self-healing is waiting on that work, not on this function.
    * **a node holding a derived word but no ``id``** — the entity surface cannot
      address it, so the sweep cannot repair it. Reachability is narrow (the
      ``:Source`` write surface refuses ``goalState``, so only a raw write and an
      url-keyed stub produce it) but it is not zero, so it is COUNTED as
      ``counts.unaddressable`` rather than filtered away silently: a candidate
      read that matched and then vanished would be the same false PASS as the
      declined label above.
    * **a node whose ``id`` sits on a label the entity surface cannot resolve** —
      the same unaddressable population, in its second shape: the scan is
      label-agnostic by design (``WHERE NOT n:Point``) while the door resolves each
      label by its OWN primary identity prop, so an ``:Widget`` node — or an
      unlabelled one — matches the scan and cannot be named to the door. ⛔ **It
      includes a canonical label whose identity is not ``id``**: ``Event`` is keyed
      by ``eventId``, so a ``:Event {id:'…'}`` is unaddressable even though the
      surface CAN resolve an Event that carries its real ``eventId``. Such a node is
      classified ``counts.unaddressable`` on a LIVE sweep, when the ``gone`` arm
      probes the graph (:data:`GONE_UNPROVEN`) — not by pre-filtering on the label,
      and not under ``dry_run``: the door is what ``dry_run`` skips, and the probe
      hangs off the door's own answer (see the ``dry_run`` paragraph). A label filter
      over-excludes and would sweep every repairable ``:Event`` in with them
      (#12457 review cycle 2 measured both directions). Reachability is
      raw/legacy-write only, like the no-``id`` shape above, because no SDK create
      path mints these labels and ``_upsert_event`` sets ``id == eventId``.
    * **a word written inside the DOOR's read→write window** — see the note on
      ``preserved`` below.

    ⚠️ Everything else that can hold the word IS repaired, and that is a
    deliberate breadth rather than a whitelist: a ``:Subject`` goal takes an
    engine-written ``goalState`` through the journalled entity surface (measured:
    it survives a rebuild), so it is a candidate like any other. The exclusion is
    the ONE label whose write branch is unwrapped (:data:`_GOAL_STATE_CANDIDATES`).

    ⛔ THE GATE IS ON THE WORD, AT BOTH ENDS. Only a goal holding a word in
    :data:`DERIVED_GOAL_STATES` is even a candidate (so a human's ``active``,
    ``blocked``, ``guideline``, ``superseded`` or ``abandoned`` is not scanned, let
    alone rewritten), and a word APPEARING BETWEEN THE SCAN AND THE DOOR'S READ
    that is outside :data:`DERIVED_GOAL_STATES` is PRESERVED and reported — never
    cleared, never rewritten. ⚠️ The precise scope of that promise: the door's
    gate is ``current is not None and current not in DERIVED_GOAL_STATES``, so a
    human who writes ``met``/``unmet`` in that window IS overwritten, and is
    counted ``cleared``/``rewritten`` like any other derived word. The sweep
    cannot distinguish a human's ``met`` from the engine's — that is the same
    limitation the door has, and it is why the promise is scoped by VALUE rather
    than by who wrote it.
    ⚠️ The window the door does not close: it is a read-then-write
    (``get_entity`` … ``update_entity``), so a human word landing INSIDE that
    window is overwritten and counted as ``cleared``/``rewritten``. That window
    is the pre-existing door's, not this sweep's — a sweep makes it graph-wide
    rather than shrinking it — and closing it needs a compare-and-set write in
    :func:`reconcile_goal_state_cache`, which is a change to the shared write door
    and its own lane rather than a comment here.

    ``dry_run=True`` reports what it WOULD do and writes nothing: the safe
    preview for a graph-wide call, which is the one shape that can touch every
    goal. ⛔ ONE DISCLOSED DIVERGENCE, structural rather than an oversight: the
    door is never called under ``dry_run`` (it WRITES), and the probe that
    reveals an id-unaddressable goal is reachable only from the door's ``gone``
    answer — so a live sweep reports such a goal ``unaddressable`` while a dry run
    of the SAME graph reports ``would-clear``/``would-rewrite`` for it. Nothing is
    mis-written either way (a dry run writes nothing at all), but a preview cannot
    promise the ``unaddressable`` count of the run it previews.

    ⛔ A FAILED SCAN RAISES. A summary of zero goals over a scan that never ran is
    exactly the false PASS this module exists against, so an unreadable
    candidate read is not caught here (there is no partial sweep to report) —
    unlike a PER-GOAL failure, which IS reported: one goal's refused write is
    recorded on that goal and the rest of the sweep still lands, because a repair
    sweep whose first refusal discarded the remaining repairs would hide the very
    words it can fix.

    Returns, always: ``dryRun``, ``truncated`` (true when the candidate query
    returned MORE rows than ``limit``, i.e. the window was capped — a scan that
    exactly filled its bound is not truncated, and the count is taken BEFORE the
    identity filter so a dropped row cannot hide the cap), ``limit``,
    ``notDurableReason``, ``goals`` (a record per SCANNED goal:
    ``goalId``, ``from``,
    ``to``, ``action`` — ``cleared``/``rewritten``/``consistent``/``preserved``/
    ``gone``/``failed``/``unaddressable``, or ``would-clear``/``would-rewrite``
    under ``dry_run`` — plus ``error`` on a failure and ``reason`` where one was
    given), and ``counts``. ⛔ The actions with NO ``to`` are exactly ``failed``,
    ``gone`` and ``unaddressable`` — the three where the sweep has no cache value to
    name as the outcome; ``failed`` carries ``error`` in its place and the other two
    carry ``reason``. A consumer reading ``to`` unconditionally gets a ``KeyError``
    on those. ⚠️ That is NOT the same statement as "nothing was written":
    ``consistent`` writes nothing and DOES carry ``to`` (the word that already
    stood), as do ``would-clear``/``would-rewrite``, so a consumer must branch on
    ``action`` and must never infer a write from the mere presence of ``to``.
    ``unaddressable``'s
    ``reason`` is the SWEEP's own :data:`GONE_UNPROVEN`, not the door's: the door
    reported ``gone``, and this arm is the sweep saying it does not believe a
    deletion it can disprove. The list is CLOSED — a consumer branching on
    ``action`` must handle this value, so adding a new one here is part of adding
    it in the loop below.

    ``notDurableReason`` is a STRING when ``counts.notDurable`` is non-zero and
    ``None`` otherwise: the counter never appears without its meaning beside it,
    and a consumer can branch on the reason rather than on a bare number.

    ``counts`` is a set of PER-GOAL predicates, not a partition, and it is
    deliberately its OWN (this call is not a retirement sweep, so folding it into
    ``invalidate_stale``'s counters would make one summary answer two questions —
    and #12315 rejected a fifth counter there for exactly that reason):

    * ``scanned`` — candidates the scan returned (bounded by ``limit``);
    * ``repaired`` — ``cleared + rewritten``, i.e. the cache was CHANGED;
    * ``cleared`` — the goal now holds no derived word (nothing live stands);
    * ``rewritten`` — the goal now holds the newest live judgement's word;
    * ``consistent`` — the word already matched, and NOTHING was written (a
      re-write per goal per run would journal a revision for work not done);
    * ``preserved`` — a word appearing between the scan and the door's read that is
      NOT in :data:`DERIVED_GOAL_STATES` (a human's ``active``/``blocked``/
      ``guideline``) was left alone and reported, so a sweep cannot read clean over
      a goal it refused to touch. ⚠️ Scoped by VALUE for the same reason the gate
      above is: a human ``met``/``unmet`` in that window IS overwritten;
    * ``gone`` — the goal node is GONE, so there is no cache to reconcile. The
      record carries the DOOR's ``reason``, not the sweep's. ⚠️ The same BUCKET
      NAME as :func:`invalidate_stale`'s ``counts.gone``, but NOT the same meaning:
      that one still derives from the un-probed door answer (#12534), so a consumer
      must not read the two counters as one predicate.
      ⛔ The door's own ``gone`` answer is NOT sufficient to report this: it
      resolves only the canonical labels, each by its OWN identity prop, while the
      scan matched the node label-agnostically by ``n.id`` — so a node the surface
      cannot address is indistinguishable THERE from one that does not exist. This
      arm therefore probes the graph once (:data:`_GONE_PROBE`) and re-buckets a
      node that is still present as ``unaddressable``; only a node the graph no
      longer holds is reported ``gone``;
    * ``failed`` — the reconcile could not run, its write was refused, or its
      ``gone`` answer could not be VERIFIED (the :data:`_GONE_PROBE` read raised, so
      the sweep could not tell a deletion from an id the surface cannot address).
      A sweep may not read clean while a goal still displays a word it could not
      repair;
    * ``notDurable`` — goals the sweep DECLINED because their repair cannot be
      durable (a node carrying the unwrapped POINT write branch), each holding a
      derived word. It is not a failure and not a repair: it is the population
      this call does not cover, counted so `scanned: 0` cannot be read as "this
      graph is clean" (:data:`NOT_DURABLE_REASON` says the same in the payload);
    * ``unaddressable`` — candidates in the fetched WINDOW that the entity surface
      cannot ADDRESS, in any of its shapes: a node carrying no ``id`` (counted on
      the scan, window-scoped); and, counted later on the ``gone`` arm, a node that
      IS there but whose ``id``-named identity the surface cannot resolve — a node
      on a non-canonical label, on no label, or on a canonical label whose primary
      identity prop is not ``id`` (``Event`` is keyed by ``eventId``). The later
      shapes used to come back ``gone`` — a deletion recorded for a node that still
      exists with its word intact (#12412 review cycle 5, #12457 review cycle 2,
      both measured). The counter is deliberately ONE bucket for both; ``truncated``
      is what says the window itself was capped, so a caller reading both sees the
      whole shape (a capped scan whose tail was unaddressable now reports
      ``truncated: True`` rather than looking exhaustive);
    * ``wouldRepair`` — what a ``dry_run`` WOULD change; always ``0`` when
      ``dry_run`` is false, because nothing was previewed.
    """
    words = sorted(DERIVED_GOAL_STATES)
    result = sdk._get_proj().g.query(
        _GOAL_STATE_CANDIDATES,
        params={"words": words, "lim": limit + 1},
    ).result_set or []
    rows: list[tuple[str, str]] = [(row[0], row[1]) for row in result
                                  if row and row[0]]
    # A matched node with no `id` cannot be named to the entity surface, so it is
    # not repairable here — and it is COUNTED rather than filtered away: a
    # candidate that matched the scan and then vanished from the payload is the
    # same false PASS as the declined label below. ⛔ The label/identity shapes are
    # NOT pre-filtered here: they are caught on the `gone` arm by an OBSERVATION of
    # the graph instead (:data:`GONE_UNPROVEN`), because a label-membership filter
    # over-excludes every `:Event` — including the ones that carry their real
    # `eventId` and ARE repairable.
    unaddressable = len(result) - len(rows)
    # ⛔ THE DECLINED POPULATION IS MEASURED, NOT ASSUMED EMPTY. Without this a
    # sweep over a graph whose only stale words sit on nodes the sweep cannot
    # durably repair returns the SAME payload as a sweep over an empty graph
    # (`scanned: 0`, every counter 0) while those words still display — the false
    # PASS every other counter in this module exists to prevent. It raises with
    # the scan: an unreadable count is not a zero.
    undurable = sdk._get_proj().g.query(
        _GOAL_STATE_UNDURABLE, params={"words": words},
    ).result_set or []
    not_durable = int((undurable[0][0] if undurable and undurable[0] else 0) or 0)
    # `truncated` is answered from the UNTRIMMED row count — BEFORE the identity
    # filter — because a dropped row is still a candidate the window fetched and
    # the scan did not show: `len(rows) > limit` after filtering reported
    # `truncated: False` over a capped scan whose tail was dropped for having no
    # `id`, i.e. an exhaustive-looking payload over goals never fetched (#12412
    # review, cycle 4). `counts.unaddressable` is window-only for the same reason;
    # the flag is what says the window was capped. The bound is asked for
    # `limit + 1` rows so an exactly-full scan is still distinguishable from an
    # overflowed one. ⚠️ The rows are an arbitrary subset in id order, NOT the
    # newest `limit` goals — a caller that must cover the graph passes a bound
    # above the candidate count.
    truncated = len(result) > limit
    rows = rows[:limit]

    goals: list[dict[str, Any]] = []
    counts = {"scanned": 0, "repaired": 0, "cleared": 0, "rewritten": 0,
              "consistent": 0, "preserved": 0, "gone": 0, "failed": 0,
              "wouldRepair": 0, "notDurable": not_durable,
              "unaddressable": unaddressable}
    for goal_id, word in rows:
        counts["scanned"] += 1
        record: dict[str, Any] = {"goalId": goal_id, "from": word}
        try:
            standing = latest_judgement(sdk, goal_id=goal_id)
            state = standing.get("judgementState") if standing else None
            # The SAME mapping the door applies (one predicate, not a second
            # spelling of "what word should this be"): a live claim whose state
            # maps to no word means the cache holds no derived word either.
            expected = GOAL_STATE_CACHE.get(state) if state else None
        except Exception as exc:
            record.update(action="failed",
                          error=f"{type(exc).__name__}: {exc}")
            counts["failed"] += 1
            goals.append(record)
            continue
        if expected == word:
            # ⛔ NOTHING IS WRITTEN HERE, and that is the point of the branch: the
            # word already stands, so re-writing it would journal a revision per
            # goal per sweep and report "repaired" for work not done.
            record.update(action="consistent", to=word)
            counts["consistent"] += 1
            goals.append(record)
            continue
        if dry_run:
            record.update(action="would-clear" if expected is None
                          else "would-rewrite", to=expected)
            counts["wouldRepair"] += 1
            goals.append(record)
            continue
        try:
            repaired = reconcile_goal_state_cache(sdk, goal_id)
        except Exception as exc:
            record.update(action="failed",
                          error=f"{type(exc).__name__}: {exc}")
            counts["failed"] += 1
            goals.append(record)
            continue
        if repaired.get("gone") is True:
            # ⛔ THE DOOR'S `gone` IS NOT PROOF OF A DELETION (:data:`GONE_UNPROVEN`).
            # It resolves only the canonical labels, each by its OWN identity prop,
            # while this scan matched the node label-agnostically by `n.id`. So ask
            # the graph, once, on this already-rare arm: a node that is still there
            # is UNADDRESSABLE and counted — never reported as deleted.
            # ⛔ A PROBE THAT COULD NOT RUN IS NOT A DELETION, and it is not the
            # end of the sweep either: this is a PER-GOAL read, so it is reported
            # on this goal and the rest still lands (the same isolation contract
            # the `latest_judgement` and door calls above honour — see the
            # docstring's "A FAILED SCAN RAISES … unlike a PER-GOAL failure").
            # Letting it escape would discard `goals` for every goal already
            # reconcile in this sweep, which is a throw-away a caller cannot
            # diagnose. `failed` is the module's own word for "this goal could not
            # be accounted for", and a sweep may not read clean over a word it
            # could not account for.
            try:
                present = sdk._get_proj().g.query(
                    _GONE_PROBE, params={"id": goal_id},
                ).result_set or []
            except Exception as exc:
                record.update(action="failed",
                              error=f"{type(exc).__name__}: {exc}")
                counts["failed"] += 1
                goals.append(record)
                continue
            if present:
                record.update(action="unaddressable", reason=GONE_UNPROVEN)
                counts["unaddressable"] += 1
            else:
                record.update(action="gone", reason=repaired.get("reason"))
                counts["gone"] += 1
        elif repaired.get("error"):
            record.update(action="failed", error=repaired["error"])
            counts["failed"] += 1
        elif repaired.get("preserved") is True:
            record.update(action="preserved", to=repaired.get("goalState"))
            counts["preserved"] += 1
        elif repaired.get("checked") is not True:
            # ⛔ A RECONCILE THAT DID NOT RUN IS NEVER A REPAIR, and this arm exists
            # precisely so it cannot be counted as one: the door answers
            # `checked: False` (plus a `reason`, no `error`) when it was handed no
            # goal id at all. Unreachable from THIS call path — the candidate read
            # keeps only rows carrying an id — but a sweep that folded it into
            # `rewritten` would report a repair that never happened over a goal
            # still showing a derived word, which is this module's own defect.
            record.update(action="failed",
                          error=repaired.get("reason")
                          or "the reconcile did not run")
            counts["failed"] += 1
        else:
            # `cleared` is the DOOR's own word for "no derived word stands now"
            # (it is True both when the cache was emptied and when the live claim
            # maps to no word), so the sweep reports the outcome with the door's
            # vocabulary rather than a second definition of it.
            action = "cleared" if repaired.get("cleared") else "rewritten"
            record.update(action=action, to=repaired.get("goalState"))
            counts[action] += 1
            counts["repaired"] += 1
        goals.append(record)
    return {"dryRun": dry_run, "truncated": truncated, "limit": limit,
            "goals": goals, "counts": counts,
            "notDurableReason": NOT_DURABLE_REASON if not_durable else None}


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

    ``counts.gone`` counts the findings whose cache reconcile did not happen
    because the door could not resolve the goal — the retirement landed, but there
    was no resolved node to reconcile the ``goalState`` cache on. ⛔ That is a
    DELETED node AND an id the entity surface merely cannot ADDRESS: it is the
    door's UN-PROBED answer, so the two are counted alike here (#12534 owns the
    code fix; the sweep's wording above is the correct one). It is not a failure of
    the retirement (which did land) and not a success of the reconcile (which did
    not happen) — it is its own bucket, and it exists so a sweep over unresolved
    goals cannot read clean.
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
                invalidated.append({
                    "invalidated": False,
                    "alreadyRetired": False,
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
            # ⛔ A RETIREMENT WHOSE GOAL IS GONE IS ITS OWN BUCKET. The retirement
            # landed; the cache reconcile could not happen (no node to point at
            # anything). Reported per finding as `cache.gone`, and counted here,
            # so neither the summary nor the nested key claims a clear that never
            # ran — the same rule as `failed` below.
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
