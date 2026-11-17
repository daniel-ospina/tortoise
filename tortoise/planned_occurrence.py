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


def create_plan(sdk, name: str, *, title: str | None = None,
                **props) -> dict:
    """Create a plan Object and put it on the ``scheduled`` stage.

    Returns the created node (``create_entity``'s ``node`` payload).

    The Object is created with the NAMESPACED kind so the pipeline can bind to
    it, then advanced to ``scheduled`` from birth: a plan with no stage would
    read as "position unknown", and the one thing a freshly created plan is
    certain of is that it has not happened. The advance goes through
    ``pipeline.advance_stage``, i.e. through ``update_knowledge``, so the move is
    journaled (``EntityMutated``) and replayed.
    """
    from . import pipeline

    door_props: dict[str, Any] = {"objectKind": PLAN_PIPELINE_KEY, **props}
    if title is not None:
        door_props.setdefault("title", title)
    created = sdk.create_entity("object", name, **door_props)
    node = created.get("node") or {}
    plan_id = node.get("id")
    if not plan_id:
        # A create the store dropped (an unwritable name is the documented
        # case, #3590) must not look like a staged plan.
        return node
    pipeline.advance_stage(
        sdk, plan_id, PLAN_PIPELINE_KEY, STAGE_SCHEDULED,
        declared=PLAN_PIPELINE_DECL)
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
    if cur.get("slot") == "terminal":
        raise ValueError(
            f"Refused: the plan {plan_ref!r} is already settled at "
            f"{cur.get('stageId')!r} (outcome {cur.get('outcome')!r}). WHAT TO "
            f"DO: a plan ends once. If the occurrence still needs a record, "
            f"write the :Event directly (`create_entity(type='event')`) — the "
            f"dated fact stands on its own; to correct the plan, that is a new "
            f"plan, not a stage flip on a settled one.")

    event_name = name or cur.get("name") or plan_id
    created = sdk.create_entity(
        "event", event_name, eventKind=event_kind,
        **({"startedAt": at} if at is not None else {}),
        aboutObject=plan_id,
        **event_props)
    node = created.get("node") or {}
    event_id = node.get("eventId") or node.get("id")
    move = pipeline.advance_stage(
        sdk, plan_id, PLAN_PIPELINE_KEY, STAGE_HELD, at=at,
        declared=PLAN_PIPELINE_DECL, source="occurrence")
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
    if cur.get("slot") == "terminal":
        raise ValueError(
            f"Refused: the plan {plan_ref!r} is already settled at "
            f"{cur.get('stageId')!r}. WHAT TO DO: a plan ends once; a new plan "
            f"is a new record.")
    move = pipeline.advance_stage(
        sdk, plan_id, PLAN_PIPELINE_KEY, stage, at=at,
        declared=PLAN_PIPELINE_DECL, reason=reason, source="settle")
    return {"planId": plan_id, "move": move}


def _resolve_plan(sdk, plan_ref: str) -> tuple[str, dict]:
    """``(id, current stage state)`` for a plan reference, or a refusal.

    The SAME resolver the stage write uses (``pipeline._resolve_object``), so the
    link and the stage move cannot address two carriers of one name (#3590 D2 /
    #12120).
    """
    from . import pipeline
    from .entity_identity import resolve_entity_id_same_entity

    graph = sdk._get_proj().g
    plan_id = resolve_entity_id_same_entity(graph, "Object", plan_ref)
    cur = pipeline.current_stage(graph, plan_id) if plan_id else {}
    if not plan_id or not cur:
        raise ValueError(
            f"Refused: no plan Object named or identified by {plan_ref!r}. WHAT "
            f"TO DO: create the plan first (`create_plan`), then record the "
            f"occurrence against it.")
    return plan_id, cur


def planned_without_occurrence(graph) -> list[dict]:
    """Q5 — what was planned and never happened, read from position + link.

    A plan HAS happened when an ``:Event`` is wired to it by
    :data:`OCCURRENCE_REL`; nothing about the plan's own words is consulted to
    decide that. Returns one row per plan with no such Event::

        {"id", "name", "stage", "ended", "outcome"}

    ``stage`` is the plan's current stage (``scheduled`` when it has none yet),
    ``ended`` is True when that stage is terminal (so the plan ended
    ``missed`` / ``cancelled`` rather than still being upcoming), and
    ``outcome`` is the stage's shipped outcome.

    ⛔ No ``status`` is read, and no ``eventStatus`` is read: those answer
    different questions (the lifecycle word is about currency, the ingest marker
    about our bookkeeping), and neither can say whether the thing happened.
    """
    rows = graph.query(
        "MATCH (p:Object) WHERE p.objectKind IN $kinds "
        "OPTIONAL MATCH (e:Event)-[:aboutObject]->(p) "
        "WITH p, collect(DISTINCT e.id) AS occurrences "
        "RETURN p.id, p.name, p.pipelineStageId, p.pipelineSlot, "
        "       p.pipelineOutcome, occurrences",
        params={"kinds": sorted(PLAN_KINDS)}).result_set
    out: list[dict] = []
    for pid, name, stage, slot, outcome, occurrences in rows:
        # The link is the proof. A plan whose plan-side words say "held" but
        # with no Event wired to it has no occurrence in the graph, and this
        # read does not take the plan's word for it.
        if occurrences:
            continue
        out.append({
            "id": pid,
            "name": name,
            "stage": stage or STAGE_SCHEDULED,
            "ended": slot == "terminal",
            "outcome": outcome or None,
        })
    return out
