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
:func:`planned_without_occurrence` therefore reads position + link and never a
``status`` property:

* a plan with a linked ``:Event`` has happened;
* a plan without one has not — whether it is still ``scheduled`` (upcoming) or
  already ended ``missed`` / ``cancelled`` (it never will).

⚠️ **ACCEPTED RESIDUAL, stated rather than hidden:** ``aboutObject`` is also the
predicate an Event uses to *mention* an Object, so a plan linked by a
non-occurrence Event (a note about the offsite, say) reads here as "it
happened". The model has no separate occurrence predicate, and adding one is a
surface decision (follow-up issue #13128). Treated as an
accepted residual until that decision: the link means "an Event recorded about
this plan", and a writer that wants to mention without claiming occurrence
should not wire the plan.

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
OCCURRENCE_REL = "aboutObject"

#: The Event kind a bare :func:`record_occurrence` call mints when the caller
#: does not name one. `occurrence` is already a core event kind (ONTOLOGY §5),
#: so nothing new is coined.
DEFAULT_OCCURRENCE_KIND = "occurrence"


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
        "outcome": (stage.outcome if stage is not None
                    else (None if unresolvable else outcome)) or None,
        "has_position": bool(stage_id),
        "ended": bool(is_closed) or (stage is not None and stage.is_terminal),
        "unresolvable": unresolvable,
        "unresolvable_cause": unresolvable_cause,
    }


def create_plan(sdk, name: str, *, title: str | None = None,
                at: str | None = None, **props) -> dict:
    """Create a plan Object and put it on the ``scheduled`` stage.

    Returns the created node (``create_entity``'s ``node`` payload), or — on the
    has-position re-mention arm, which writes nothing — the existing record's own
    stored properties. ⛔ Neither payload promises a POSITION: the door-props
    node this call returns from the create path is the pre-advance shape, so a
    caller that needs the stage reads it back (``pipeline.current_stage``).

    The Object is created with the NAMESPACED kind so the pipeline can bind to
    it, then advanced to ``scheduled`` from birth: a plan with no stage would
    read as "position unknown", and the one thing a freshly created plan is
    certain of is that it has not happened. The advance goes through
    ``pipeline.advance_stage``, i.e. through ``update_knowledge``, so the move is
    journaled (``EntityMutated``) and replayed.

    ⛔ The returned node is only a *staged* plan if the advance actually applied.
    A create **name** is a natural key that re-mentions its single live holder
    (#452), so this call can land on a plan that already exists. FOUR arms, and
    only the second SUCCEEDS without writing:

    * **live, open and POSITION-LESS** (the extractor-minted `core:plan`): the
      call falls through the re-mention and STAGES the record — "created" is not
      true of a plan with no position, and a position-less plan is exactly the
      shape the promotion gate is bounded on, so returning it would leave the
      suppression OFF;
    * **live, open, positioned, and of the BINDABLE spelling**: a no-op success,
      nothing re-staged, because that is exactly what was asked for. This is the
      only arm that succeeds without writing;
    * **live, open and positioned but stored under a spelling no pipeline can
      bind** (the bare `plan` kind): also falls through, and the door's
      namespaced `objectKind` REPAIRS the binding — a write, deliberately: a
      success here would hand the caller a plan whose next write refuses;
    * **settled / ENDED** — the durable marker, a terminal stage, or a retired
      status word: RAISES, naming the stage it is at and the route to a new
      record.

    Two further REFUSALS apply to an existing holder before any of that, and
    neither writes: a holder whose kind is not a plan (this call would retag it),
    and a plan whose position does not resolve (`_unresolvable_message`).
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
        # plan is neither at `scheduled` nor unresolvable — which the plan
        # pipeline cannot produce today (only `scheduled` is non-terminal), so it
        # exists so a future stage declaration cannot re-open the silent
        # success. It names the stage the plan is ACTUALLY at. REFUSED rather
        # than `reopen=True`d, deliberately: reopening would restate a recorded
        # ending as upcoming, which §8 forbids ("a later event does not rewrite
        # an earlier one").
        cur = pipeline.current_stage(sdk._get_proj().g, plan_id) or {}
        pos = plan_position(
            cur.get("stageId"), outcome=cur.get("outcome"),
            is_closed=bool(cur.get("isClosed")),
            pipeline_key=cur.get("pipelineKey"))
        if (pos["stageId"] == STAGE_SCHEDULED and not pos["ended"]
                and not pos["unresolvable"]):
            # The ONE unapplied move that is not a lie: the name resolved to a
            # plan already on `scheduled` and open, so the contract — "a
            # scheduled, not-yet-happened plan exists under this name" — holds.
            # This is the stale-`at` re-mention, and it is not an error.
            return node
        if pos["unresolvable"]:
            # The stored position cannot be validated, so NO stage move could be.
            # ONE home for that refusal (the writers cannot drift), and it names
            # the input that actually failed — a distinct name does not repair a
            # drifted binding, so prescribing one here would be a no-op.
            raise ValueError(_unresolvable_message(name, pos, cur))
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

    Returns ``{"event": <node>, "eventId": <id>, "planId": <id>, "move":
    StageMove}``. ⚠️ Ordering, stated because it is observable: the Event is
    written before the stage move, and a move the pipeline refuses then raises
    with the Event already in the graph. That is the recoverable direction — a
    stray dated fact can be read, a terminal plan with no fact cannot.
    """
    from . import pipeline

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
            f"helper's contract, not caller props. WHAT TO DO: pass "
            f"`event_kind=` / the plan reference instead, and drop "
            f"{', '.join(collided)} from the props.")
    event_name = name or cur.get("name") or plan_id
    created = sdk.create_entity(
        "event", event_name, eventKind=event_kind,
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

    if stage not in (STAGE_MISSED, STAGE_CANCELLED):
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
    # set (#13100 review round 6). The snapshot makes "once" true on both shapes.
    _entries_before = len(non_folded_entries())
    try:
        plan_id = resolve_entity_id_same_entity(graph, "Object", plan_ref,
                                                record_misses=False)
    except AmbiguousEntityName as exc:
        if len(non_folded_entries()) == _entries_before:
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

    A plan HAS happened when an ``:Event`` is wired to it by
    :data:`OCCURRENCE_REL`; nothing about the plan's own words is consulted to
    decide that. Returns one row per plan with no such Event::

        {"id", "name", "stage", "ended", "outcome"}

    ``stage`` is the plan's current stage (``scheduled`` when it has none yet),
    ``ended`` is True when the record has ENDED — the durable ``isClosed``
    marker (a cleared or legacy stage still ends) **or** a terminal slot — and
    ``outcome`` is the stage's shipped outcome. An ended-but-occurrence-less plan
    is the ``missed`` / ``cancelled`` case: it never happened and never will.

    ⛔ No ``status`` is read, and no ``eventStatus`` is read: those answer
    different questions (the lifecycle word is about currency, the ingest marker
    about our bookkeeping), and neither can say whether the thing happened.
    """
    rows = graph.query(
        "MATCH (p:Object) WHERE p.objectKind IN $kinds "
        "OPTIONAL MATCH (e:Event)-[:aboutObject]->(p) "
        # ⛔ count(e), NOT collect(DISTINCT e.id): `collect` DROPS a null, and an
        # `:Event` minted by the session/ingest writers carries `eventId` only —
        # `id` is absent. Counting a property therefore reported a plan with a
        # real linked occurrence as "planned and never happened", the exact
        # inversion this read exists to prevent.
        "WITH p, count(e) AS occurrences "
        "RETURN p.id, p.name, p.pipelineStageId, "
        "       p.pipelineOutcome, coalesce(p.isClosed, false), "
        "       p.pipelineStageKey, occurrences",
        params={"kinds": sorted(PLAN_KINDS)}).result_set
    out: list[dict] = []
    for pid, name, stage, outcome, closed, key, occurrences in rows:
        # The link is the proof. A plan whose plan-side words say "held" but
        # with no Event wired to it has no occurrence in the graph, and this
        # read does not take the plan's word for it.
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
        })
    return out
