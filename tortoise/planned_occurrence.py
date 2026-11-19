"""#13100 (S7) — a planned occurrence is a PLAN; an ``:Event`` is what happened.

The model (``docs/plans/2026-11-15-state-lifecycle-and-pipelines.md`` §8, the
approved design of record for #11824):

    an event is a dated fact, and a thing that has not happened yet is not an
    event — it is a **plan**: an ordinary record with a lifecycle and, where its
    kind declares one, a pipeline (``scheduled → held / missed / cancelled``).
    When it happens, the **event** is created and linked to the plan, and where
    the plan's declared pipeline has a terminal stage the plan reaches it.
    There is no third case.

Before this module there WAS a third case, twice over: an ``:Event`` carried a
writer-supplied non-terminal ``status`` (``scheduled`` / ``draft`` / ``live``)
as well as the ``eventStatus`` ingest marker, so "will happen" and "did happen"
shared a mutable field — and the ``status`` half was load-bearing for operator
promotion (``sdk._promote_incident_operators``). Both halves are retired:
``live.refuse_event_lifecycle_state`` refuses ANY ``status`` on an ``:Event``
at every write door, and "will happen" has the home below.

## What a plan IS here

A plan is an **Object** of the core ``plan`` kind (ONTOLOGY §5's
commitment-state family — no new vocabulary, and it shares no noun with the
``:Event`` label, so an agent cannot pick whichever word the sentence used).
Its position comes from the shipped pipeline machinery (#2792), not from a
mutable status word:

    scheduled   active     the plan is on the books, nothing has happened yet
    held        terminal   the occurrence happened      -> outcome ``completed``
    missed      terminal   it did not happen, and ended -> outcome ``lost``
    cancelled   terminal   it was called off            -> outcome ``withdrawn``

``PIPELINE_OUTCOMES`` is not widened — the three terminal stages reuse the
shipped ``completed`` / ``lost`` / ``withdrawn`` outcomes.

## The link, and why it is the answer to "planned but never happened?"

Occurrence is proven by the graph, never by a word: the ``:Event`` created when
the thing happens is wired to its plan with the shipped ``aboutObject``
predicate (``(e:Event)-[:aboutObject]->(p:Object)``), which is journaled as an
``aboutLinks`` descriptor and replayed — so the proof survives ``rebuild_all``.
:func:`planned_without_occurrence` therefore reads position + link + the linked
Event's kind, and never a ``status`` property:

* a plan with a linked ``:Event`` **of the occurrence kind** has happened;
* a plan without one has not — whether it is still ``scheduled`` (upcoming) or
  already ended ``missed`` / ``cancelled`` (it never will).

## A MENTION is not an occurrence (#13128)

``aboutObject`` is ALSO the predicate an Event uses to *mention* an Object
(§3.2 ``schema:about``): a note, a reminder, an email summary about the offsite
is an ``:Event`` wired to the plan and is no evidence whatsoever that the offsite
happened. So the edge alone is not the proof, and the read tells the two apart
by the linked Event's OWN kind — :data:`OCCURRENCE_EVENT_KINDS`, the core
``occurrence`` kind, which :func:`record_occurrence` writes and which that door
accepts to the exclusion of every other kind. A mention is counted and served
(``mentions`` on each row), never silently dropped and never read as the
occurrence.

⚠️ **STATED RESIDUALS, kept small and named rather than hidden** (the full
argument, and why no better marker was available to this slice, is in
:data:`OCCURRENCE_EVENT_KINDS`):

* the kind is **evidence, not proof** — ``occurrence`` is ONTOLOGY §5's generic
  extracted kind and the repo's declared fallback for an unclassifiable
  ``eventKind``, so a mention written under it still reads as the occurrence;
* the read **fails closed** on everything else — a plan-linked Event of another
  kind reads as a mention, so a plan that DID happen can be reported as
  never-happened where its occurrence Event carries another kind (a pre-change
  ``record_occurrence(..., event_kind=…)``, or an Event linked by another
  writer). The refusal closes that going forward; there is no backfill.

## Why the pipeline key is namespaced

Pipelines key on the NAMESPACED kind (#2767), so the plan Object is created
with ``objectKind="core:plan"``: the bare ``plan`` and ``core:plan``
spellings are ONE kind for reads (`pack_registry.CORE_ALIASED_KINDS`), and the
namespaced spelling is the one a pipeline can bind. No pack is required and no
new kind is declared.
"""
from __future__ import annotations

from typing import Any

#: The core Object kind a plan is. ``plan`` is already in ONTOLOGY §5's object
#: vocabulary (`CANONICAL_OBJECT_KINDS`) — this module mints no kind.
PLAN_KIND = "plan"

#: The namespaced spelling a pipeline binds to (#2767: a pipeline keys on the
#: namespaced kind). The bare and ``core:``-prefixed forms are ONE kind.
PLAN_PIPELINE_KEY = "core:plan"

#: Every spelling a reader must recognise as "this Object is a plan".
PLAN_KINDS: frozenset[str] = frozenset({PLAN_KIND, PLAN_PIPELINE_KEY})

STAGE_SCHEDULED = "scheduled"
STAGE_HELD = "held"
STAGE_MISSED = "missed"
STAGE_CANCELLED = "cancelled"

#: The plan pipeline declaration, in the shipped ``kindDefs[].pipeline`` shape
#: (`pipeline.validate_pipeline_decl`). It is declared here rather than in a
#: pack because a planned occurrence is a core concept: every domain schedules
#: things, and requiring a pack would make the boundary unavailable to a graph
#: that installs none.
PLAN_PIPELINE_DECL: dict[str, Any] = {
    "defaultStage": STAGE_SCHEDULED,
    "stages": [
        {"id": STAGE_SCHEDULED, "label": "Scheduled", "order": 0,
         "slot": "active"},
        {"id": STAGE_HELD, "label": "Held", "order": 1, "slot": "terminal",
         "outcome": "completed"},
        {"id": STAGE_MISSED, "label": "Missed", "order": 2, "slot": "terminal",
         "outcome": "lost"},
        {"id": STAGE_CANCELLED, "label": "Cancelled", "order": 3,
         "slot": "terminal", "outcome": "withdrawn"},
    ],
}

#: The predicate the occurrence Event is wired to its plan with. It is the
#: shipped Event -> Object link (§3.2 `schema:about`), not a new edge: an Event
#: "describes" the plan it realized, the same way it describes any Object it is
#: about.
#: ⛔ THE PREDICATE ALONE IS NOT THE PROOF — see :data:`OCCURRENCE_EVENT_KINDS`.
OCCURRENCE_REL = "aboutObject"

#: The Event kind a bare :func:`record_occurrence` call mints when the caller
#: does not name one. `occurrence` is already a core event kind (ONTOLOGY §5),
#: so nothing new is coined.
DEFAULT_OCCURRENCE_KIND = "occurrence"

#: The bare spelling of the occurrence kind. See
#: :data:`OCCURRENCE_EVENT_KINDS` for the ONE home of the argument: why the linked
#: Event's kind is where a mention is told from an occurrence, what it does NOT
#: prove, every legal spelling of it, and the residuals that remain.
OCCURRENCE_EVENT_KIND = DEFAULT_OCCURRENCE_KIND

#: **Where the occurrence is told apart from a mention** (#13128) — the ONE home
#: of that argument, and every spelling that names the kind.
#:
#: ``aboutObject`` is BOTH the predicate an occurrence Event is wired to its plan
#: with AND the §3.2 ``schema:about`` predicate any Event uses to *mention* an
#: Object — a note, a reminder, an email summary. The edge therefore cannot say
#: which one it is, and a read that counted every linked Event served a plan
#: with a note about it as *"it happened"*.
#:
#: So the distinguishing fact is the linked Event's OWN kind, and the answer is
#: the core ``occurrence`` kind, which is what :func:`record_occurrence` writes.
#: Every other linked Event is a MENTION, which is a true statement about the
#: plan and no evidence at all that it occurred (``planned_without_occurrence``
#: counts them separately and serves the count).
#:
#: **Two spellings, ONE kind.** ``core:X`` and ``X`` are one kind in this model —
#: ``pack_registry`` declares the equivalence over ``CORE_ALIASED_KINDS`` (which
#: covers ``CANONICAL_EVENT_KINDS``), and its own rule is that a read must not
#: depend on the normalised spelling: ``migrate_kinds`` is an opt-in normaliser,
#: never a precondition for a read. The write door STORES WHAT IT IS GIVEN —
#: measured: ``create_entity('event', …, eventKind='core:occurrence')`` keeps the
#: ``core:`` form — so a bare-string comparison would report a plan that happened
#: as never-happened, and would refuse ``record_occurrence(event_kind=
#: 'core:occurrence')`` although it names the same kind. This module's own
#: ``PLAN_KINDS`` already carries both spellings for the same reason.
#:
#: ⛔ **THE KIND IS EVIDENCE, NOT PROOF, and the difference is stated rather
#: than glossed.** ``occurrence`` is ONTOLOGY §5's *generic extracted
#: occurrence* and the repo's declared fallback for an ``eventKind`` a writer
#: cannot classify — BOTH spellings: ``pack_state._KIND_GENERAL_FALLBACKS
#: ["eventKind"]`` (bare, and `unapproved_kind_refusal` PRESCRIBES it in its
#: remedy text) and ``extractor_v2._EVENT_FALLBACK["kind"] = "core:occurrence"``
#: (namespaced — the spelling this change newly counts). So not every
#: Event of this kind is an occurrence, and a mention written under it still
#: reads as the occurrence. What the kind buys is the whole ORDINARY mention set
#: (``email`` / ``meeting`` / ``note`` / ``turn`` / ``decision`` …) plus a writer
#: guard on the one door this module owns — not exclusivity over the vocabulary.
#: Getting exclusivity needs a dedicated predicate or an edge role (the issue's
#: options 1 and 2): a new predicate is a surface change with its own approval
#: (ONTOLOGY §3 + the surface gate), and a role prop needs a journal/replay
#: carrier — the edge is rebuilt from ``EventRecorded.aboutLinks``
#: (``replay_about_links``), which carries no edge props, so the marker would be
#: lost by ``rebuild_all`` unless three surfaces grew it at once. Neither is this
#: slice's to make, so the honest answer is the issue's option 3 done
#: mechanically: a discriminator that separates the ordinary case, a writer-side
#: guard, and the residual written down.
#:
#: ⚠️ STATED RESIDUALS, both directions:
#:
#: * **A mention can still read as the occurrence** — any Event of this kind, and
#:   the kind is the declared fallback for an unclassifiable one. Also a caller
#:   that LIES about its own Event (``eventKind='occurrence'`` on a note).
#: * **The read now fails CLOSED on everything else** — a plan-linked Event of a
#:   different kind reads as a mention, so a plan that DID happen can be reported
#:   as never-happened when its occurrence Event was written with another kind.
#:   The reachable populations: a pre-change ``record_occurrence(...,
#:   event_kind=…)`` (the parameter was honoured before this guard, and no in-tree
#:   caller passed it), and a non-occurrence-kind Event linked to a plan by
#:   another writer. The guard closes this going forward only — there is no
#:   backfill, and this module has no MCP/SDK/REST door yet
#:   (``STORAGE-ARCHITECTURE.md`` row 44 classes it ``ops audit``).
#: * ⛔ ``event_kind`` on :func:`record_occurrence` therefore stays in the
#:   signature as a LOUD REFUSAL, not as a choice: it documents that the kind is
#:   this helper's contract. Do not "restore" it as a feature without the two
#:   residuals above being answered.
OCCURRENCE_EVENT_KINDS: tuple[str, ...] = (
    OCCURRENCE_EVENT_KIND, f"core:{OCCURRENCE_EVENT_KIND}")


def plan_kinds() -> frozenset[str]:
    """Every ``objectKind`` spelling that names a plan.

    The ONE reader-facing spelling list, so a consumer (the operator-promotion
    gate, a Q5 read) cannot drift from :data:`PLAN_KINDS` by re-spelling it.
    """
    return PLAN_KINDS


def is_plan_kind(object_kind: object) -> bool:
    """True when ``object_kind`` names a plan under any spelling.

    Shape-independent on purpose: an absent/blank/non-string kind is not a
    plan, and a ``str`` subclass cannot make it one by lying to ``__eq__``
    (``str.__str__`` is what the store keeps, the same rule the #11825 guards
    follow).
    """
    if isinstance(object_kind, str):
        return str.__str__(object_kind) in PLAN_KINDS
    return False


#: The props only the STAGE WRITE may set on a plan. `create_plan` refuses them
#: rather than letting `**props` park a position (or a closed marker) that the
#: fresh `scheduled` advance then fails to overwrite — a create that returns a
#: plan with no stage, which is the state `create_plan` exists to prevent.
_STAGE_OWNED_PROPS: tuple[str, ...] = (
    "pipelineStageId", "pipelineSlot", "pipelineOutcome", "pipelineStageSince",
    "pipelineStageKey", "isClosed",
)


#: "the caller did not supply the stored key" — distinct from "the key is
#: absent on the node", which is a real (and unadvanceable) state.
_KEY_UNSPECIFIED = object()


def plan_position(stage_id, *, outcome=None, is_closed=False,
                  declared: dict | None = None,
                  pipeline_key=_KEY_UNSPECIFIED) -> dict:
    """The plan's current position, resolved the way the WRITE resolves it.

    ⛔ This is the ONE answer to "is this plan still open, ended, or
    unresolvable?", and it exists because the pre-checks used the stored
    ``pipelineSlot`` PROP as a proxy while ``pipeline.advance_stage`` decides
    terminality from the RESOLVED stage — ``was_closed = bool(cur["isClosed"])
    or (cur_stage is not None and cur_stage.is_terminal)``. A plan whose
    ``pipelineStageId`` names a terminal stage but whose ``pipelineSlot`` prop is
    absent or stale therefore read as OPEN to every pre-check (so the Event was
    written and journaled before the writer's terminal guard refused) and as
    still-upcoming to the Q5 read, while the gate suppressed promotion for ever.

    Three inputs, in the writer's order:

    * ``isClosed`` — the durable marker ends the record (§6);
    * a ``stage_id`` that RESOLVES to a terminal stage of the effective
      pipeline;
    * a ``stage_id`` that does not resolve at all — the writer refuses any move
      on such a plan (`unresolvable-current-stage`), so a pre-check must not
      attempt one either. ⛔ **The stored ``pipelineStageKey`` is part of that
      resolution**, not decoration: the writer resolves the current stage only
      when ``cur_key == pipeline_key`` (`pipeline.advance_stage`), so a stage id
      beside a MISSING or FOREIGN key is exactly as unadvanceable as an unknown
      id — reading the id alone let a refused call write and journal the
      ``:Event`` first, with every retry compounding it.

    ⛔ ``outcome`` is the SECOND parameter (the first keyword-only) and
    deliberately NOT an input: it is never read. It stays in the signature
    because the negative-control pins pass a stored ``pipelineOutcome`` word and
    prove the answer does not come from it, and it must leave only together with
    those pins. The answer is the RESOLVED stage's outcome, or ``None`` (#13100
    review rounds 13–14). ⛔ Do NOT "restore" a read of it: the
    ``else (None if unresolvable else outcome)`` fallback that did exactly that
    was the mixed-cache defect this helper removed — ``pipelineOutcome`` is a
    cache a generic-door ending leaves stale, and reading it paired
    ``ended: False`` with ``outcome: 'lost'`` for a position-less plan.

    ``has_position`` says whether any stage was recorded: a plan with NO position
    (an extractor-minted ``core:plan`` Object) is not evidence of anything, so it
    is neither ended nor "not yet" — that is why the promotion gate needs the
    distinction rather than a boolean.

    ⛔ ``has_position`` and ``ended`` are the ONLY two fields the promotion gate
    reads (`live.endpoint_permits_operator_promotion`), and it reads them as
    "has a position AND has not ended" — so an UNRESOLVABLE row (either cause)
    suppresses exactly as a resolvable non-terminal one does. `unresolvable`
    answers a different question (can a stage MOVE be validated) and must never
    be the gate's discriminant: an unknown stage id is one supported write away
    (`update_knowledge(id, pipelineStageId=...)`) and a plan the writers refuse
    every move on could otherwise never stop suppressing — the fail-OPEN shape
    this helper exists to remove. The repair route keeps it recoverable, which is
    `update_knowledge(id, pipelineStageId=<a declared id>)`
    (see :func:`_unresolvable_message`).

    ⚠️ **STATED RESIDUAL (#13100 review round 3):** this resolves against the
    DECLARED pipeline, not a per-graph `:Pipeline` override — it takes no graph,
    while ``advance_stage`` resolves through ``effective_pipeline``, where an
    override wins. No supported verb can change a stage's SLOT
    (``rename_stage`` changes labels, ``add_stage`` adds ids, ``retire_stage``
    sets a status ``by_id`` ignores), so a divergence needs a hand-written
    override — and it fails CLOSED (a plan reads not-ended and is refused), never
    open. Reading the override here means threading a graph through a pure helper
    the gate calls per endpoint; deliberately not done here.
    """
    from . import pipeline as _pipeline

    pipe = _pipeline._pipeline_from_decl(
        PLAN_PIPELINE_KEY, declared if declared is not None else PLAN_PIPELINE_DECL)
    stage = pipe.by_id(stage_id) if stage_id else None
    # ⛔ The key must be EXACTLY this pipeline's. `None` is not "unjudged" — a
    # stage id beside a cleared or foreign key is precisely what the writer
    # refuses as `unresolvable-current-stage` (it resolves the current stage only
    # when `cur_key == pipeline_key`), so it is unresolvable here too. Only a
    # caller that did not supply the key at all gets the benefit of the doubt.
    if pipeline_key is _KEY_UNSPECIFIED:
        bound_elsewhere = False
    elif not isinstance(pipeline_key, str):
        # ⛔ A NON-STRING key is FOREIGN, not a crash: `str.__str__` on an int is
        # a `TypeError`, and this resolver is on the operator-promotion path
        # (which runs AFTER its CAS has already set the point live) and on the Q5
        # read (where one bad row would fail the whole answer). A prop write
        # reaches it — `update_knowledge(id, pipelineStageKey=5)` is accepted —
        # and the WRITER's own answer on that row is a refusal, not a raise.
        bound_elsewhere = True
    else:
        bound_elsewhere = (pipeline_key is None
                           or str.__str__(pipeline_key) != PLAN_PIPELINE_KEY)
    unresolvable = bool(stage_id) and (stage is None or bound_elsewhere)
    resolved_slot = stage.slot if stage is not None else None
    # ⛔ WHICH input failed, because the repairs differ: an unknown STAGE ID is
    # fixed by writing a declared id, a bad KEY by repairing the binding. One
    # message for both prescribed a no-op repair for the first and promised "still
    # open" to a plan that had already ended (a cleared key beside a terminal
    # stage id).
    if not unresolvable:
        unresolvable_cause = None
    elif stage is None:
        unresolvable_cause = "stage"
    else:
        unresolvable_cause = "key"
    return {
        "stageId": stage_id,
        # ⛔ THE RESOLVED SLOT, never the stored prop: the prop is a cache a
        # generic-door ending leaves stale, and reporting it as "the stage itself
        # resolves to …" told a caller an ENDED plan was "still open (active)"
        # while the row resolved terminal. None = it does not resolve.
        "slot": resolved_slot,
        # ⛔ An UNRESOLVABLE row must not report a stale outcome: `ended: False`
        # beside `outcome: 'lost'` is the same mixed-cache contradiction round 3
        # removed from `ended`, one field over.
        # ⛔ `ended` follows the RESOLVED STAGE, never the key: a stage id that
        # resolves TERMINAL beside a cleared or foreign key has ENDED, and nulling
        # it made the Q5 read answer "upcoming" for exactly the shape an operator
        # must see (an ended plan with no linked Event) — contradicting this
        # module's own refusal text and the promotion gate on the same row.
        # ⛔ `outcome` is the STAGE's outcome and nothing else: when no stage
        # resolves it is None, never the stale `pipelineOutcome` prop (which would
        # pair `ended: False` with `outcome: 'lost'`). `unresolvable`/
        # `unresolvable_cause` answer a different question again: can a stage MOVE
        # be validated.
        # ⛔ NO POSITION is a "no stage resolves" case too, not an exception to
        # this rule: `unresolvable` is gated on `bool(stage_id)`, so a
        # position-less plan is NOT unresolvable and the earlier
        # `else (None if unresolvable else outcome)` fallback reported the STORED
        # prop for exactly that shape — the same mixed-cache pair this rule
        # forbids, one input over (#13100 review round 13, P2, measured through
        # both doors). `ended` still answers the durable `isClosed` marker, so a
        # closed plan with a cleared stage loses no settlement here.
        "outcome": (stage.outcome if stage is not None else None) or None,
        "has_position": bool(stage_id),
        "ended": bool(is_closed) or (stage is not None and stage.is_terminal),
        "unresolvable": unresolvable,
        "unresolvable_cause": unresolvable_cause,
    }


def create_plan(sdk, name: str, *, title: str | None = None,
                at: str | None = None, **props) -> dict:
    """Create a plan Object and put it on the ``scheduled`` stage.

    Returns the created node (``create_entity``'s ``node`` payload), or — on the
    positioned, bindable re-mention arm only (the short-circuit below), which is
    the one success that writes nothing — the existing record's own stored
    properties. ⛔ Neither payload promises a POSITION: the door-props node this
    call returns from the create path is the pre-advance shape, so a caller that
    needs the stage reads it back (``pipeline.current_stage``).

    The Object is created with the NAMESPACED kind so the pipeline can bind to
    it, then advanced to ``scheduled`` from birth: a plan with no stage would
    read as "position unknown", and the one thing a freshly created plan is
    certain of is that it has not happened. The advance goes through
    ``pipeline.advance_stage``, i.e. through ``update_knowledge``, so the move is
    journaled (``EntityMutated``) and replayed.

    ⛔ The returned node is only a *staged* plan if the advance actually applied.
    A create **name** is a natural key that re-mentions its single live holder
    (#452), so this call can land on a plan that already exists. The outcomes for
    an existing LIVE holder, and only the second SUCCEEDS without writing:

    * **live, open and POSITION-LESS** (the extractor-minted `core:plan`): the
      call falls through the re-mention and ATTEMPTS to stage the record —
      "created" is not true of a plan with no position, and a position-less plan
      is exactly the shape the promotion gate is bounded on, so returning it would
      leave the suppression OFF. (If the effective pipeline will not apply the
      move the record stays unstaged and the call raises; see the no-position
      refusal below.)
    * **live, open, positioned, and of the BINDABLE spelling**: a no-op success,
      nothing re-staged, because that is exactly what was asked for. This is the
      only arm that succeeds without writing;
    * **live, open and positioned but stored under a spelling no pipeline can
      bind** (the bare `plan` kind): also falls through, and the door's
      namespaced `objectKind` REPAIRS the binding — a write, deliberately: a
      success here would hand the caller a plan whose next write refuses;
    * **a still-LIVE holder at a TERMINAL stage**: RAISES, naming the stage it is
      at and the route to a new record;
    * **a still-LIVE holder whose position does not RESOLVE** — a drifted stage
      id, or a cleared/foreign `pipelineStageKey`: RAISES through
      `_unresolvable_message`, whose ONE home picks the remedy for the input that
      actually failed (a declared stage id, or the pipeline binding). It is
      deliberately not "a new name": a new name repairs neither input;

    ⛔ A holder that is ENDED but no longer LIVE is NOT in those arms: the
    re-mention resolve is LIVE-only (``live_only=True``), so a plan settled
    through this module (``isClosed``) or retired with a status word is not a
    holder this call can see — its NAME IS FREE and a NEW plan is minted, which
    is correct for a genuinely new occurrence (#3590 D2; pinned by
    ``test_a_CLOSED_plans_name_is_free_for_a_new_plan``). Saying "settled →
    RAISES" for those inputs would be false (#13100 review round 10).

    Two further REFUSALS apply to an existing holder before any of that, and
    neither writes: a holder whose kind is not a plan (this call would retag it),
    and a plan whose position does not resolve (`_unresolvable_message`). And the
    re-mention RESOLVE can refuse before this function has a RESOLVED holder (an
    id) — both are ``ValueError``s and both are recorded as R8 refusals by the
    resolver: a name with two live carriers (``AmbiguousEntityName``), or exactly
    one live holder that carries no id (``UnaddressableEntityName``).
    """
    from . import pipeline

    # ⛔ The KIND is last, so a caller's `objectKind` cannot shadow it. With the
    # reverse order a `create_plan(..., objectKind="core:goal")` wrote a
    # core:goal Object and THEN raised the pipeline's kind-binding refusal — a
    # refused create that left a record in the graph behind it.
    # ⛔ The guard must read the map the DOOR will write, not the kwargs shape:
    # `create_entity` flattens a nested `props=` (top level wins), so a guard that
    # tested the top level only let `create_plan(props={"pipelineStageId": …})`
    # park a position on a fresh plan — the exact record the guard exists to
    # prevent, and one the Q5 read then reports as ended-and-never-happened.
    merged: dict[str, Any] = dict(props)
    _nested = merged.get("props")
    if isinstance(_nested, dict):
        for _k, _v in _nested.items():
            merged.setdefault(_k, _v)
    # ⛔ THE RENDERER UNION, not `k in merged`. A `str` SUBCLASS key that lies
    # about `__hash__`/`__eq__` is invisible to a literal-name membership test
    # while the writer still files it under the name — measured at this very
    # guard: `create_plan(**{KHash("pipelineStageId"): "held"})` parked the
    # position, the advance then failed, and the refused create left the Object
    # behind (the #9287 atomicity rule). `live.lifecycle_props_by_rendering` is
    # built on the ONE definition of "which name does this key land under"
    # (`live._event_state_key_names`), shared with the `:Event` status guards
    # (#9649 review 13) — do not re-spell it here.
    from .live import lifecycle_props_by_rendering

    # The dict's KEYS are the guarded names — naming the key's own content
    # rendering instead indicted `harmless` for a key the writer filed under
    # `pipelineStageId`, which is not what the caller has to remove.
    shadowed = sorted(lifecycle_props_by_rendering(merged, _STAGE_OWNED_PROPS))
    if shadowed:
        raise ValueError(
            f"Refused: {', '.join(shadowed)} cannot be set on a plan at CREATION "
            f"— the stage write owns them. WHAT TO DO: create the plan, then "
            f"advance it (`record_occurrence` / `settle_plan`), or write the "
            f"position through the pipeline's own verb.")
    door_props: dict[str, Any] = {**merged, "objectKind": PLAN_PIPELINE_KEY}
    if title is not None:
        door_props.setdefault("title", title)
    # ⛔ THE RE-MENTION PRE-FLIGHT. `create_entity("object", <name>)` is the
    # documented idempotent re-mention (#452): it resolves the name to its single
    # LIVE holder. Without this check, `create_plan` RETAGGED a live `core:goal`
    # Object into `core:plan` and then drove its stage props — a kind clobber the
    # module's own writers refuse (`_resolve_plan`: "is not a plan — its kind is
    # …"), reached one door over. It also made "a plan with this name already
    # exists and has ENDED" look like a *created* plan that could not be staged.
    from .entity_identity import resolve_entity_id_same_entity

    _graph = sdk._get_proj().g
    _existing = resolve_entity_id_same_entity(_graph, "Object", name)
    if _existing:
        _rows = _graph.query(
            "MATCH (o:Object {id:$o}) RETURN o.objectKind",
            params={"o": _existing}).result_set
        _kind = _rows[0][0] if _rows else None
        if not is_plan_kind(_kind):
            raise ValueError(
                f"Refused: a live Object named {name!r} already exists and is "
                f"not a plan — its kind is {_kind!r}. This call would have "
                f"RETAGGED that record into {PLAN_PIPELINE_KEY!r} and staged it. "
                f"Nothing was changed. WHAT TO DO: pick a distinct name for the "
                f"plan; if the existing record IS what happened (or is what an "
                f"occurrence was about), write the dated fact directly — "
                f"`create_entity(type='event', aboutObject=<its id>)` — because "
                f"`record_occurrence` settles a PLAN and this record is not one.")
        _cur = pipeline.current_stage(_graph, _existing)
        _pos = plan_position(_cur.get("stageId"), outcome=_cur.get("outcome"),
                             is_closed=bool(_cur.get("isClosed")),
                             pipeline_key=_cur.get("pipelineKey"))
        if _pos["ended"]:
            # ⛔ `ended` FIRST, like both writers: an ended plan with a broken key
            # is still ENDED, and the binding message would tell the caller it is
            # "still open" — a sentence `plan_position` itself contradicts.
            raise ValueError(
                f"Refused: a plan named {name!r} already exists and has ENDED "
                f"(stage {_cur.get('stageId')!r}) — it cannot be re-created. WHAT "
                f"TO DO: act on the existing plan (`record_occurrence` / "
                f"`settle_plan`), or choose a distinct name if this is a "
                f"different planned occurrence.")
        if _pos["unresolvable"]:
            # ⛔ The cause decides the remedy (review round 9 + the F1 fix): a
            # drifted STAGE ID is repaired by writing a declared id — never by
            # "use a distinct name", which would be a no-op for the caller's
            # actual problem.
            raise ValueError(_unresolvable_message(name, _pos, _cur))
        # ⛔ ONLY A POSITIONED, unended plan of the BINDABLE spelling short-circuits.
        # A live plan holder with NO position has not been staged, and returning
        # it as a successful re-mention told the caller a plan was on the books
        # when nothing was: an operator over it was then PROMOTED (a plan with no
        # position does not suppress — by design). Such a holder FALLS THROUGH to
        # the normal create+advance below, which re-mentions it (same kind: `plan`
        # and `core:plan` are ONE kind) and stages it.
        #
        # ⛔ AND THE BARE SPELLING FALLS THROUGH EVEN WHEN IT HAS A POSITION.
        # `is_plan_kind` accepts `plan`, but every writer's `_resolve_plan`
        # requires the NAMESPACED kind (a pipeline keys on it, #2767), so
        # returning a positioned bare-kind record as a create's SUCCESS payload
        # left the caller holding a plan whose very next write refused with
        # "…no pipeline can bind … create it with `create_plan`" — which returned
        # this same record again: a refusal loop with no exit, and the docstring
        # contract ("only a *staged* plan if the advance actually applied") broken
        # on the first arm (#13100 review round 3, F1(a)). Falling through BINDS
        # it: `create_entity` re-mentions the record and the door's
        # `objectKind` is the namespaced one, so the kind is repaired rather than
        # reported as a success.
        if (_pos["has_position"]
                and str.__str__(_kind) == PLAN_PIPELINE_KEY):
            # The documented re-mention. Return the record's OWN node payload —
            # the same SHAPE the create path returns (`create_entity`'s `node`),
            # so a caller reading `title`/`description` does not work on one arm
            # and KeyError on the other. Its position is already its own, so
            # nothing is re-staged.
            _node = _graph.query(
                "MATCH (o:Object {id:$o}) RETURN properties(o)",
                params={"o": _existing}).result_set
            return dict(_node[0][0]) if _node else {"id": _existing}


    created = sdk.create_entity("object", name, **door_props)
    node = created.get("node") or {}
    plan_id = node.get("id")
    if not plan_id:
        # A create the store dropped (an unwritable name is the documented
        # case, #3590) must not look like a staged plan.
        return node
    move = pipeline.advance_stage(
        sdk, plan_id, PLAN_PIPELINE_KEY, STAGE_SCHEDULED, at=at,
        declared=PLAN_PIPELINE_DECL)
    if not move.applied:
        # A plan born with NO position reads as "unknown" to every reader, so a
        # create that could not stage it must say so rather than return a
        # success-shaped record.
        #
        # ⛔ AND IT MUST SAY SOMETHING TRUE. "…a fresh plan carries none" was not
        # true on the reachable path, and it left the caller no route out (#13100
        # review F1). Two reachable cases now exist, and neither is "a fresh
        # plan":
        #
        # * the name resolves to a plan that is already `scheduled` and open, and
        #   the observation was STALE (`at` before its `pipelineStageSince`) —
        #   the plan is exactly where this create asked it to be, so that is a
        #   no-op success, not an error (the arm below);
        # * the name resolves to a plan whose stored position does NOT resolve (a
        #   drifted stage id, a cleared or foreign `pipelineStageKey`) — the
        #   writer refuses every move on such a plan and the repair is the
        #   BINDING (`_unresolvable_message`, the same home the writers use), not
        #   a new name.
        #
        # What remains is the defensive arm: a move that did not apply while the
        # plan is neither at `scheduled` nor unresolvable. ⛔ NOT unreachable, and
        # NOT only "a future stage declaration": a per-graph override that
        # RETIRES `scheduled` (`set_default_stage` + `retire_stage`) makes the
        # move `unknown-stage` for a record this read still sees without a
        # position, so a FRESH create lands here (#13100 review round 10). It
        # therefore branches on what is actually known rather than calling a
        # brand-new record "settled".
        cur = pipeline.current_stage(sdk._get_proj().g, plan_id) or {}
        pos = plan_position(
            cur.get("stageId"), outcome=cur.get("outcome"),
            is_closed=bool(cur.get("isClosed")),
            pipeline_key=cur.get("pipelineKey"))
        if (pos["stageId"] == STAGE_SCHEDULED and not pos["ended"]
                and not pos["unresolvable"]):
            # The ONE unapplied move that is not a lie: the plan is already on
            # `scheduled` and open, so the contract — "a scheduled,
            # not-yet-happened plan exists under this name" — holds.
            # ⛔ WHAT MAKES THE MOVE UNAPPLIED IS THE STALE-TRANSITION GUARD, not
            # a same-target no-op: `pipeline.advance_stage` has NO "already at
            # this stage" guard (a move onto the current stage APPLIES and
            # rewrites `pipelineStageSince`), so the ordinary way in is an
            # observation whose `at` precedes the record's `since` — reached,
            # since the positioned bindable re-mention short-circuits above, by a
            # BARE-SPELLING repair carrying one (#13100 review round 8).
            # ⚠️ NOT the only way: this arm answers from the DECLARED pipeline
            # (`plan_position`, which states that residual), while the move
            # resolves through `effective_pipeline` — so a graph override that
            # retires `scheduled` (`set_default_stage` + `retire_stage`) makes the
            # move `unknown-stage` on a fresh `at` while this read still sees
            # `scheduled`/active, and this arm returns the node (#13100 review
            # round 9).
            return node
        if pos["unresolvable"]:
            # The stored position cannot be validated, so NO stage move could be.
            # ONE home for that refusal (the writers cannot drift), and it names
            # the input that actually failed — a distinct name does not repair a
            # drifted binding, so prescribing one here would be a no-op.
            raise ValueError(_unresolvable_message(name, pos, cur))
        if pos["stageId"] is None:
            # ⛔ NOT a settled plan — a record with NO POSITION that could not be
            # staged. The reachable cause is a graph override the MOVE honours and
            # this read does not (`advance_stage` → `effective_pipeline`, this read
            # → the declared pipeline): a retired `scheduled` answers
            # `unknown-stage` and leaves the record unstaged. Saying "settled …
            # give it a distinct name" here would be false about the graph and
            # would mint a second unstaged plan (#13100 review round 10).
            raise ValueError(
                f"Refused: the plan {name!r} resolves to {plan_id!r}, which could "
                f"not be put on {STAGE_SCHEDULED!r} ({move.reason}), and it "
                f"stores no stage. WHAT TO DO: the stage this move resolves and "
                f"the stage this read sees disagree — check the graph's "
                f"{PLAN_PIPELINE_KEY!r} pipeline override (a retired "
                f"{STAGE_SCHEDULED!r} answers 'unknown-stage'), repair the "
                f"override, then advance the plan; it is NOT settled, so a new "
                f"name would not help.")
        raise ValueError(
            f"Refused: the plan {name!r} resolves to {plan_id!r}, which is "
            f"already at {pos['stageId']!r}"
            f"{' (closed marker set)' if cur.get('isClosed') else ''}"
            f", so this create cannot put it on {STAGE_SCHEDULED!r} "
            f"({move.reason}). WHAT TO DO: a settled plan is a closed record, "
            f"and a create does not reopen or restate one — a new incarnation is "
            f"a NEW plan, so give it a distinct name.")
    return node


def record_occurrence(sdk, plan_ref: str, *, name: str | None = None,
                      event_kind: str = DEFAULT_OCCURRENCE_KIND,
                      at: str | None = None, **event_props) -> dict:
    """Record that the plan HAPPENED: create the ``:Event``, link it, settle plan.

    The three steps are the model's one write path (§8): the Event exists
    because the thing happened, it is linked to the plan it realized, and the
    plan reaches its terminal ``held`` stage with the ``completed`` outcome.

    A plan that did NOT happen ends through :func:`settle_plan` instead — never
    through this function, because this function's whole contract is that a
    dated fact now exists. The position is read FIRST, so an already-settled
    plan (``held`` / ``missed`` / ``cancelled``) is refused before anything is
    written: nothing is left behind by a refused call.

    ⛔ ``event_kind`` is REFUSED unless it names :data:`OCCURRENCE_EVENT_KINDS`
    (both legal spellings), and that is the writer half of #13128
    (:data:`OCCURRENCE_EVENT_KINDS` records why the kind is the marker, and what
    it does NOT prove): this call's link IS the claim that the plan happened, and
    the reader tells an occurrence from a *mention* by the linked Event's kind.
    Accepting ``event_kind='meeting'`` here would mint a link that the read
    honestly classifies as a mention — a plan that happened, recorded as never
    having happened, with nothing failing. The refusal names the route for a
    mention (write the ``:Event`` yourself with ``aboutObject=<plan>``), which is
    exactly the shape `create_entity` already supports and
    :func:`planned_without_occurrence` counts separately.

    ⛔ The parameter is therefore retained as a LOUD REFUSAL, not as a choice:
    it stays in the signature so that a caller reaching for it is told what the
    kind means instead of silently losing its classification. Do not "restore"
    it as a feature — that needs the two residuals in
    :data:`OCCURRENCE_EVENT_KINDS` answered first (a mention can still read as
    the occurrence; and a plan-linked Event of another kind now reads as a
    mention, so the read fails closed on such a plan).

    Returns ``{"event": <node>, "eventId": <id>, "planId": <id>, "move":
    StageMove}``. ⚠️ Ordering, stated because it is observable: the Event is
    written before the stage move, and a move the pipeline refuses then raises
    with the Event already in the graph. That is the recoverable direction — a
    stray dated fact can be read, a terminal plan with no fact cannot.
    """
    from . import pipeline

    # ⛔ THE RENDERING, not `__eq__`. A `str` subclass whose `__ne__` lies
    # returns False for `event_kind != OCCURRENCE_EVENT_KIND` and slips past this
    # refusal while `create_entity` files it under the name — the exact inversion
    # this guard exists to make unrepresentable: the plan advances to terminal
    # `held` and the read serves it as *planned and never happened*, with nothing
    # raising. That is the same rule `is_plan_kind` (:176) and
    # `lifecycle_props_by_rendering` (:730) already follow (#13578 review,
    # finding 1). ⛔ Its TWIN DOOR is `settle_plan`'s `stage not in (…)` below — a
    # caller-supplied-`str` comparison that trusted `__eq__` the same way, and
    # it is repaired in this same change (#13586 review, finding 1): closing one
    # door and stating the class is shut is how the other one stays open.
    # Normalising ONCE means the value WRITTEN is the same value CHECKED —
    # otherwise the guard compares one kind and the store keeps another.
    kind = (str.__str__(event_kind) if isinstance(event_kind, str)
            else event_kind)
    # ⛔ BEFORE the resolve and before ANY write: a refused call leaves nothing
    # behind (the same rule the two `_owned`/collision guards below follow).
    # ⛔ A NON-`str` is REFUSED, never compared: `!=` on an arbitrary object is
    # that object's OWN `__ne__`, so a non-`str` liar (`__ne__` → False) skipped
    # this refusal and reached `create_entity(eventKind=<object>)` — the same
    # inversion, one type-check away (#13586 review, finding 2). `is_plan_kind`
    # (:176) already fails CLOSED for a non-`str`; this guard follows the same
    # rule, and the kind test below is over the RENDERING (`kind`), never over
    # the caller's `__eq__`.
    if not isinstance(kind, str) or kind not in OCCURRENCE_EVENT_KINDS:
        raise ValueError(
            f"Refused: `event_kind={kind!r}` cannot be the Event that "
            f"records an OCCURRENCE. This call's `{OCCURRENCE_REL}` link is the "
            f"claim that the plan happened, and an occurrence is told apart from "
            f"a MENTION of the plan by the linked Event's kind — so the Event "
            f"this helper writes is always {OCCURRENCE_EVENT_KIND!r} (ONTOLOGY "
            f"§5; the `core:` spelling names the same kind). WHAT TO DO: call "
            f"this with no `event_kind` to record that the plan happened; to "
            f"record something merely ABOUT the plan (a note, an email summary, "
            f"a reminder), write the Event directly — "
            f"`create_entity('event', <name>, eventKind={kind!r}, "
            f"aboutObject=<plan id>)` — which is a mention and never reads as "
            f"the occurrence. Nothing has been written.")

    plan_id, cur = _resolve_plan(sdk, plan_ref)
    # ⛔ The WRITER's question, asked before the write: is this plan ended (marker
    # or resolved-terminal stage), or on a stage that does not resolve (which the
    # writer refuses)? Reading the stored `pipelineSlot` prop instead let a plan
    # ended through the generic door — `update_knowledge(id,
    # pipelineStageId='held')`, a stale/absent slot prop — get its :Event written
    # and journaled before the terminal guard refused, so every retry compounded
    # an occurrence behind a refusal.
    pos = plan_position(cur.get("stageId"), outcome=cur.get("outcome"),
                        is_closed=bool(cur.get("isClosed")),
                        pipeline_key=cur.get("pipelineKey"))
    if pos["ended"]:
        raise ValueError(
            f"Refused: the plan {plan_ref!r} is already settled at "
            f"{pos.get('stageId')!r} (outcome {pos.get('outcome')!r}"
            f"{', closed marker set' if cur.get('isClosed') else ''}). WHAT TO "
            f"DO: a plan ends once. If the occurrence still needs a record, "
            f"write the :Event directly (`create_entity(type='event')`) — the "
            f"dated fact stands on its own; to correct the plan, that is a new "
            f"plan, not a stage flip on a settled one.")
    if pos["unresolvable"]:
        # ⛔ NOT "already settled": the plan has not ended. WHICH repair is
        # prescribed depends on which input failed — a bad STAGE ID is not fixed
        # by repairing the key, and "still open" may not be true at all.
        raise ValueError(_unresolvable_message(plan_ref, pos, cur))

    # ⛔ A caller prop that collides with one of this helper's OWN kwargs would
    # reach `create_entity` as a duplicate keyword and surface as a bare
    # `TypeError` — an unnamed 500-class with no remedy (and, worse, one that
    # looks like this helper is broken rather than the call).
    _owned = {"eventKind", "aboutObject"}
    if at is not None:
        _owned.add("startedAt")
    # The RENDERER UNION again, not a literal-name intersection: a `str` subclass
    # key that lies about `__hash__`/`__eq__` slips past `set.intersection` while
    # `create_entity` files it under the name — measured, and the resulting node
    # carried a prop contradicting its own edge.
    from .live import lifecycle_props_by_rendering

    collided = sorted(lifecycle_props_by_rendering(event_props, _owned))
    if collided:
        raise ValueError(
            f"Refused: {', '.join(collided)} is set by `record_occurrence` "
            f"itself — the Event's kind and its link to the plan are this "
            f"helper's contract, not caller props, and since #13128 the kind is "
            f"always {OCCURRENCE_EVENT_KIND!r} (`event_kind=` accepts no other "
            f"value either). WHAT TO DO: pass the plan reference and drop "
            f"{', '.join(collided)} from the props; to record something merely "
            f"ABOUT the plan — a note, an email summary, a reminder — write the "
            f"Event directly with "
            f"`create_entity('event', <name>, eventKind=…, aboutObject=<plan "
            f"id>)`, which is a MENTION and never reads as the occurrence.")
    event_name = name or cur.get("name") or plan_id
    created = sdk.create_entity(
        "event", event_name, eventKind=kind,
        **({"startedAt": at} if at is not None else {}),
        aboutObject=plan_id,
        **event_props)
    node = created.get("node") or {}
    event_id = node.get("eventId") or node.get("id")
    # ⛔ ``correction=True`` whenever the caller supplies ``at``. The pipeline's
    # stale-transition guard exists to stop an OUT-OF-ORDER observation moving a
    # record backwards; a caller who states the date an occurrence actually
    # happened is not that — a backfilled "it happened last March" is the
    # ordinary case, and without this the plan is created NOW and every backfill
    # is refused as stale. The regression half of the guard is moot here anyway:
    # the target stage is terminal, i.e. last in order.
    move = pipeline.advance_stage(
        sdk, plan_id, PLAN_PIPELINE_KEY, STAGE_HELD, at=at,
        declared=PLAN_PIPELINE_DECL, source="occurrence",
        correction=at is not None)
    if not move.applied:
        raise ValueError(
            f"Refused: the :Event {event_id!r} was recorded but the plan "
            f"{plan_id!r} could not reach {STAGE_HELD!r} ({move.reason}). WHAT "
            f"TO DO: inspect the plan's current stage — a plan already at a "
            f"terminal stage is settled, and the dated :Event stands on its "
            f"own.")
    return {"event": node, "eventId": event_id, "planId": plan_id,
            "move": move}


def settle_plan(sdk, plan_ref: str, stage: str, *, at: str | None = None,
                reason: str = "") -> dict:
    """End a plan that did NOT happen — ``missed`` or ``cancelled``.

    No ``:Event`` is created, and that absence is the answer to *"what was
    planned and never happened?"* (:func:`planned_without_occurrence`): the plan
    is terminal with an outcome, and no occurrence is linked to it.

    ``stage`` is limited to the two no-occurrence endings. ``held`` is refused
    here because it asserts the thing happened, which is
    :func:`record_occurrence`'s job — a caller that wants ``held`` and no Event
    is asking for a plan that reads as held with no record of it, exactly the
    mutable-word shape this slice removes.

    Returns ``{"planId": <id>, "move": StageMove}``.
    """
    from . import pipeline

    # ⛔ THE RENDERING, not `__eq__` — the same rule (and the same measured
    # bypass) as `record_occurrence` above. `stage not in (…)` IS an `__eq__`
    # test, so a `str` subclass whose `__eq__` returns True is CONTAINED and this
    # refusal does not fire; the value then reaches `advance_stage`, whose
    # `resolve` does `ref.strip()` — and `str.strip()` on a subclass returns an
    # exact `str` — so `held` resolves and the terminal move APPLIES: the plan
    # ends as held with no :Event recording it, and
    # :func:`planned_without_occurrence` serves it as *planned and never
    # happened*. That is the unrecoverable direction this module names
    # (:648-651) and the exact inversion this refusal exists to make
    # unrepresentable (#13586 review, finding 1). Normalising ONCE means the
    # value CHECKED is the value MOVED.
    # ⛔ The same NON-`str` arm as `record_occurrence` above: `not in` on an
    # arbitrary object consults its own `__eq__`, so a non-`str` liar is
    # "contained" and this refusal never fires (#13586 review, finding 2). Refuse
    # it, rather than ask it.
    stage = str.__str__(stage) if isinstance(stage, str) else stage
    if not isinstance(stage, str) or stage not in (STAGE_MISSED, STAGE_CANCELLED):
        raise ValueError(
            f"Refused: {stage!r} is not a no-occurrence ending. WHAT TO DO: use "
            f"{STAGE_MISSED!r} or {STAGE_CANCELLED!r} here; if it DID happen, "
            f"call `record_occurrence`, which writes the :Event and settles the "
            f"plan at {STAGE_HELD!r}.")
    plan_id, cur = _resolve_plan(sdk, plan_ref)
    pos = plan_position(cur.get("stageId"), outcome=cur.get("outcome"),
                        is_closed=bool(cur.get("isClosed")),
                        pipeline_key=cur.get("pipelineKey"))
    if pos["ended"]:
        # The same resolved answer `record_occurrence` uses, and the only place
        # that NAMES the marker when the stage itself does not.
        raise ValueError(
            f"Refused: the plan {plan_ref!r} is already settled at "
            f"{pos.get('stageId')!r}"
            f"{' (closed marker set)' if cur.get('isClosed') else ''}. WHAT TO "
            f"DO: a plan ends once; a new plan is a new record.")
    if pos["unresolvable"]:
        raise ValueError(_unresolvable_message(plan_ref, pos, cur))
    move = pipeline.advance_stage(
        sdk, plan_id, PLAN_PIPELINE_KEY, stage, at=at,
        declared=PLAN_PIPELINE_DECL, reason=reason, source="settle",
        correction=at is not None)
    if not move.applied:
        # ⛔ A settlement that did NOT land must not look like one: the caller
        # asked for an ending, and a returned-but-unapplied StageMove leaves the
        # plan reading as still-upcoming to every reader (#13100 review R5).
        raise ValueError(
            f"Refused: the plan {plan_id!r} could not reach {stage!r} "
            f"({move.reason}). WHAT TO DO: inspect the plan's current stage — an "
            f"already-terminal plan is settled, and a stale date needs the "
            f"ending recorded at or after `pipelineStageSince`.")
    return {"planId": plan_id, "move": move}


def _unresolvable_message(plan_ref, pos: dict, cur: dict) -> str:
    """The refusal for a plan whose stored position cannot be validated.

    ONE home, so the two writers cannot drift, and so the sentence names the
    input that ACTUALLY failed: telling an agent to repair the key when the STAGE
    ID is unknown prescribes a no-op, and promising "still open" for a cleared
    key beside a terminal stage id is simply false.
    """
    stage_id = pos.get("stageId")
    if pos.get("unresolvable_cause") == "stage":
        return (
            f"Refused: the plan {plan_ref!r} stores pipelineStageId "
            f"{stage_id!r}, which is not a stage of the "
            f"{PLAN_PIPELINE_KEY!r} pipeline, so no stage move can be validated "
            f"(the writer refuses it as unresolvable). WHAT TO DO: write a "
            f"declared stage id — one of "
            f"{', '.join(s['id'] for s in PLAN_PIPELINE_DECL['stages'])!s} — to "
            f"`pipelineStageId`, or leave the plan alone: nothing has been "
            f"changed here.")
    repair = (f"repair the binding first — "
              f"`update_knowledge(<id>, pipelineStageKey="
              f"{PLAN_PIPELINE_KEY!r})`")
    # No terminal sub-branch: `ended` is derived from the RESOLVED stage, so a
    # terminal stage beside a broken key is refused by the writers' "already
    # settled" arm (which states the same fact more accurately).
    return (
        f"Refused: the plan {plan_ref!r} sits at stage {stage_id!r}, but its "
        f"stored `pipelineStageKey` ({cur.get('pipelineKey')!r}) is not this "
        f"pipeline's {PLAN_PIPELINE_KEY!r}, so no stage move can be validated. "
        f"WHAT TO DO: {repair} — then call this again. The plan is still open "
        f"(its stage resolves to {pos.get('slot')!r}).")


def _resolve_plan(sdk, plan_ref: str) -> tuple[str, dict]:
    """``(id, current stage state)`` for a plan reference, or a refusal.

    The SAME resolver the stage write uses (``pipeline._resolve_object``), so the
    link and the stage move cannot address two carriers of one name (#3590 D2 /
    #12120).

    ⛔ ``live_only=False``, for the reason ``pipeline._resolve_object`` states
    (#13034): the position axis's own read/write must be able to name a CLOSED
    object, or the terminal guard becomes unreachable by name. This module SETTLES
    plans — ``record_occurrence`` leaves ``isClosed=True`` — so a live-only
    coordinate could not address a settled plan by name at all, and the three
    refusals crafted below ("already settled", the closed-marker arm,
    ``_unresolvable_message``) were dead code on the coordinate the API
    advertises. Worse, the refusal they never got to reach advised
    ``create_plan``, which resolves LIVE holders only — so following the advice
    minted a SECOND plan under the same name instead of reporting the settled one
    (#13100 review round 3, F2).

    ⛔ WIDEN ONLY WHEN THE LIVE ARM FINDS NOTHING. `live_only` drops the WHOLE
    currency predicate — the lifecycle word and the ``outdated`` flag as well as
    the position pair (``live._terminal_excluded``) — and `resolve_entity_id`'s
    documented rule is "exactly one LIVE holder -> its id". Widening
    unconditionally over-blocked a name this module's own ``create_plan``
    produces (a settled plan plus its same-name live successor, legal under #3590
    D2): where the live arm resolved the one open plan, the widened arm saw two
    holders and refused as ambiguous, so a by-name call that worked stopped
    working, permanently, with only ids as an exit (#13100 review round 4, F2).
    Live-first keeps a unique open plan resolving and still reaches the settled
    record when nothing is live.

    ⛔ AND THE DROPPED STATUS AXIS IS GATED EXPLICITLY, because `plan_position`
    reads the POSITION axis only: a plan retired by a status word
    (``archived`` / ``deprecated`` / ``superseded``) or by the legacy
    ``outdated`` flag would otherwise resolve here, report ``ended: False`` and be
    WRITTEN to — a durable, journaled settlement of a record the currency rule
    calls ended, and in the ``supersede_knowledge`` shape the occurrence would
    land on the retired predecessor whose successor never sees it (#13100 review
    round 4, F1).
    """
    from . import pipeline
    from .entity_identity import (
        AmbiguousEntityName,
        non_folded_entries,
        record_non_folded,
        resolve_entity_id_same_entity,
    )
    from .live import is_terminal_status

    graph = sdk._get_proj().g
    # `record_misses=False` on the first probe: it is SPECULATIVE — an empty
    # answer is retried over the wider arm below — and a successful widened
    # resolve must not leave a phantom `no-live-holder` refusal in the R8 record
    # for a name that resolved (#12047 N6/N1). The widened call keeps the
    # default, so a genuinely absent name is still recorded as a miss.
    # ⛔ AMBIGUITY is the case that justification does NOT cover: the live arm
    # raises before the retry can run, so suppressing the record there would drop
    # the only evidence of an ambiguous plan name. Record it once from the caught
    # refusal — NEVER by re-invoking the resolver: that arm records through
    # `_refuse`, which ignores `record_misses` on the duplicate-id shape, so a
    # second call would file the SAME entry twice and inflate the refused-event
    # set (#13100 review round 6).
    # ⛔ THE GUARD IS BY COORDINATE, not by entry COUNT: the R8 record is
    # process-global and `_non_folded_lock` exists because several threads record
    # into it, so a refusal from ANOTHER thread between the snapshot and this
    # `except` would move the count and silently skip OUR record — losing the
    # very evidence this arm exists to keep (#13100 review round 7).
    _entries_before = len(non_folded_entries())
    try:
        plan_id = resolve_entity_id_same_entity(graph, "Object", plan_ref,
                                                record_misses=False)
    except AmbiguousEntityName as exc:
        _filed_since = non_folded_entries()[_entries_before:]
        if not any(str(e.label) == str(exc.label)
                   and str(e.name) == str(exc.name)
                   for e in _filed_since):
            record_non_folded(exc.label, exc.name, exc.candidate_ids)
        raise
    if plan_id is None:
        plan_id = resolve_entity_id_same_entity(graph, "Object", plan_ref,
                                                live_only=False)
    cur = pipeline.current_stage(graph, plan_id) if plan_id else {}
    if not plan_id or not cur:
        raise ValueError(
            f"Refused: no plan Object named or identified by {plan_ref!r}. WHAT "
            f"TO DO: create the plan first (`create_plan`), then record the "
            f"occurrence against it.")
    # ⛔ The KIND is checked HERE, before any write. `create_entity` accepts any
    # Object reference, so without this a caller could name a goal, get the
    # `:Event` written and JOURNALED (with its durable `aboutObject` edge), and
    # only then hit the pipeline's kind-binding refusal — a refusal that leaves
    # an orphan occurrence behind and names the wrong problem (#13100 review).
    rows = graph.query(
        "MATCH (o:Object {id:$o}) RETURN o.objectKind, o.status, "
        "coalesce(o.outdated, false)",
        params={"o": plan_id}).result_set
    object_kind, stored_status, outdated = (rows[0][:3] if rows
                                           else (None, None, False))
    if not is_plan_kind(object_kind):
        raise ValueError(
            f"Refused: {plan_ref!r} is not a plan — its kind is "
            f"{object_kind!r}, and this write settles a plan. WHAT TO DO: "
            f"create the plan with `create_plan` (kind {PLAN_PIPELINE_KEY!r}), or "
            f"if the thing is not a planned occurrence, record the :Event "
            f"directly with `create_entity(type='event')`.")
    if str.__str__(object_kind) != PLAN_PIPELINE_KEY:
        # ⛔ THE BARE SPELLING IS A PLAN BUT NOT A BINDABLE ONE. Pipelines key on
        # the NAMESPACED kind (#2767), so `plan` and `core:plan` are one kind to
        # a READ and two different strings to the stage write. Accepting the bare
        # form here let the :Event be written and JOURNALED (with its durable
        # aboutObject edge) before the binding refusal — the same
        # refused-call-that-left-a-write behind round 1's finding, one spelling
        # over, and with a message that called a plan "not a plan".
        raise ValueError(
            f"Refused: {plan_ref!r} is a plan, but its kind is the bare "
            f"{object_kind!r} spelling, which no pipeline can bind (a pipeline "
            f"keys on the namespaced kind). WHAT TO DO: create it with "
            f"`create_plan` (kind {PLAN_PIPELINE_KEY!r}), or update its "
            f"objectKind to {PLAN_PIPELINE_KEY!r} before recording the "
            f"occurrence. Nothing has been written.")
    # ⛔ A NON-STRING `status` NEVER REACHES THE PREDICATE. The store keeps what a
    # caller wrote — `update_knowledge(<id>, status=[…])` is accepted — and
    # `is_terminal_status` does `(status or "") in TERMINAL_EXCLUDED_STATUSES`,
    # which raises `TypeError: unhashable type` for a list. That is an unnamed
    # 500-class with no remedy, on a plan the REST of the system reads as live
    # (the Cypher arm compares against strings, so no list can equal a status
    # word), so the shape is normalised to "no word" here — the same rule
    # `plan_position` applies to a non-string pipeline key (FOREIGN, not a
    # crash). The legacy `outdated` FLAG is still read: it is a boolean whatever
    # the status is (#13100 review round 5).
    if is_terminal_status((stored_status if isinstance(stored_status, str)
                           else None), bool(outdated)):
        # ⛔ The STATUS axis, which the widened name arm no longer filters: an
        # archived/deprecated/superseded plan (or the legacy `outdated` flag) is
        # a record the currency rule calls ENDED, so it is not one this write may
        # settle. `plan_position` cannot answer this — it reads the position axis
        # only — so the gate lives here, at the ONE resolver both writers share,
        # and lands before any write.
        raise ValueError(
            f"Refused: the plan {plan_ref!r} is already settled — its status is "
            f"{stored_status!r}"
            f"{' (outdated marker set)' if outdated else ''}, so the record has "
            f"ENDED. WHAT TO DO: a plan ends once; a new incarnation is a NEW "
            f"plan, so give it a distinct name.")
    return plan_id, cur


def planned_without_occurrence(graph) -> list[dict]:
    """Q5 — what was planned and never happened, read from position + link.

    A plan HAS happened when an ``:Event`` **which is the occurrence** is wired
    to it by :data:`OCCURRENCE_REL`; nothing about the plan's own words is
    consulted to decide that. Returns one row per plan with no such Event::

        {"id", "name", "stage", "ended", "outcome", "mentions"}

    ``stage`` is the plan's current stage (``scheduled`` when it has none yet),
    ``ended`` is True when the record has ENDED — the durable ``isClosed``
    marker (a cleared or legacy stage still ends) **or** a terminal slot — and
    ``outcome`` is the stage's shipped outcome. An ended-but-occurrence-less plan
    is the ``missed`` / ``cancelled`` case: it never happened and never will.

    ⛔ **A position is not a second determinant of "happened".** The LINK is the
    proof, so a plan whose own position says it was ``held`` (outcome
    ``completed``) but which has no occurrence-kind Event is reported here as
    never-happened, with that stage and outcome served beside it — the row states
    the contradiction rather than resolving it in either direction (reading
    "happened" off the outcome word would be a second vocabulary; see
    :data:`OCCURRENCE_EVENT_KINDS` for why the link is the one that decides). That
    population is pre-existing and is widened, not created, by #13128: a
    plan-linked Event of a non-occurrence kind used to suppress the row and now
    counts as a mention, because the read fails CLOSED — a plan that DID happen
    can therefore read as never-happened when its occurrence Event carries
    another kind (see that constant's stated residuals).

    ``mentions`` is the second fact, counted rather than absorbed: how many
    linked Events are about the plan WITHOUT being its occurrence (a
    note, a reminder, an email summary). It is served so a caller can say
    *"mentioned twice, never happened"* — which is a different statement from
    *"nothing was ever recorded about it"* — and so the two predicates cannot
    be re-conflated by a reader who only sees one number.

    ⛔ No ``status`` is read, and no ``eventStatus`` is read: those answer
    different questions (the lifecycle word is about currency, the ingest marker
    about our bookkeeping), and neither can say whether the thing happened.
    """
    rows = graph.query(
        "MATCH (p:Object) WHERE p.objectKind IN $kinds "
        "OPTIONAL MATCH (e:Event)-[:aboutObject]->(p) "
        # ⛔ TWO counts, not one (#13128). ``aboutObject`` is ALSO the §3.2
        # ``schema:about`` MENTION predicate, so counting every linked Event made
        # a plan with a note about it read as "it happened" — the one inversion
        # this read exists to prevent. `occurrences` counts the Events of the
        # occurrence kind (ONTOLOGY §5 — ``OCCURRENCE_EVENT_KINDS`` carries both
        # legal spellings, because the door stores what it is given and
        # `core:X ≡ X`); `mentions` counts every other linked Event. The kind is
        # EVIDENCE, not proof — see that constant for the residuals.
        #
        # ⛔ Both arms test `e IS NOT NULL`: an OPTIONAL MATCH that found nothing
        # yields ONE row with `e` null, and `NOT ('' IN $occurrence_kinds)` is
        # TRUE for it, which would report a plan with no linked Event at all as
        # mentioned.
        # ⛔ Not `collect(DISTINCT e.id)` and not a property count of `id`: an
        # `:Event` minted by the session/ingest writers carries `eventId` only,
        # so counting `id` reported a plan with a real linked occurrence as
        # "planned and never happened". `count`/`sum` over the NODE has no such
        # hole; the kind test is on the node for the same reason.
        "WITH p, "
        "     sum(CASE WHEN e IS NOT NULL AND coalesce(e.eventKind, '')"
        " IN $occurrence_kinds THEN 1 ELSE 0 END) AS occurrences, "
        "     sum(CASE WHEN e IS NOT NULL AND NOT (coalesce(e.eventKind, '')"
        " IN $occurrence_kinds) THEN 1 ELSE 0 END) AS mentions "
        "RETURN p.id, p.name, p.pipelineStageId, "
        "       p.pipelineOutcome, coalesce(p.isClosed, false), "
        "       p.pipelineStageKey, occurrences, mentions",
        params={"kinds": sorted(PLAN_KINDS),
                "occurrence_kinds": list(OCCURRENCE_EVENT_KINDS)}).result_set
    out: list[dict] = []
    for pid, name, stage, outcome, closed, key, occurrences, mentions in rows:
        # The occurrence link is the proof. A plan whose plan-side words say
        # "held" but with no occurrence Event wired to it has no occurrence in
        # the graph, and this read does not take the plan's word for it — nor a
        # mention's word, which is what #13128 was.
        if occurrences:
            continue
        # ONE resolution per row, through the same helper the two writers use, so
        # the read cannot answer from a different input than they do: the stored
        # `pipelineSlot` PROP is not the writer's answer (a plan ended through the
        # generic door carries a terminal `pipelineStageId` beside a stale slot).
        pos = plan_position(stage, outcome=outcome, is_closed=bool(closed),
                            pipeline_key=key)
        out.append({
            "id": pid,
            "name": name,
            "stage": pos["stageId"] or STAGE_SCHEDULED,
            "ended": pos["ended"],
            "outcome": pos["outcome"],
            "mentions": int(mentions or 0),
        })
    return out
