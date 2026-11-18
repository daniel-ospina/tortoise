"""The read whose question is "what ended, and how?" (#13302).

WHY THIS MODULE EXISTS
----------------------
Default-scope reads answer *currency*: what still counts. They are right to leave
ended records out — that is design §6 — but until this module there was **no read
whose question was the other one**. To learn that a claim was retracted or that a
plan was cancelled, a caller had to already know the record's **id** (a by-id
`read_entity`), or already know which surface accepted `include_terminal` /
`include_retracted` for its **kind**, and force it per call. A customer asking
*"has anything we relied on been withdrawn?"* was told, in effect, that nothing
had ever ended.

So this is not a new state, a visibility field, or a second vocabulary. It is the
**other half of the one determinant** the design already fixes (§6): the shared
currency predicate says which records have ended, and this read serves exactly
those, with the ending the record itself carries.

WHAT IT DOES *NOT* DO
---------------------
- **It does not decide what ended.** The selector is
  ``live._terminal_expression`` — the SAME Cypher predicate every reader filters
  on, in the positive direction. No status list, no slot literal, no per-family
  deny-list lives here; a word or a slot the predicate stops treating as an
  ending stops appearing here, and nothing in this module needs editing.
- **It does not name how a record ended in a vocabulary of its own.** The answer
  is the record's own fields: its lifecycle `status`, the successor it names, and
  its pipeline `stage` (``slot`` + ``outcome``). ``deprecated`` means nothing
  replaced it and ``superseded`` names its successor because §3/§7 of the design
  say so — a token minted here would be a second vocabulary for one fact.
- **It does not text-match.** "What ended" is a STATE question, so the selector
  is the state, not the relevance of a query: ``tortoise_fts_query`` refuses a
  ``query`` beside ``ended_scope`` rather than silently ranking the ended set by
  words the caller did not mean as a filter (see that method's refusal).

THE "PLANNED AND NEVER HAPPENED" SHAPE
--------------------------------------
A plan is an Object with a pipeline (design §4), so it ends on the **position**
axis: a stage in the `terminal` slot, whose `outcome` says how. Whether it
*happened* is a separate fact — the occurrence is an Event linked `aboutObject` —
so the row carries ``linkedEvents``. A plan whose ``stage.slot == "terminal"``
and whose ``linkedEvents`` is empty was **planned and never happened**; one that
has an occurrence event did happen, and a live plan is not in this population at
all (it has not ended).

⛔ Nothing here reads a stage NAME. The slot decides (§4.1), so a kind may call
its terminal stage ``cancelled``, ``missed``, ``written-off`` or ``signed`` and
this read needs no knowledge of it — that is the point of the slot being its own
namespace, and it is why the caller does not have to learn a kind's stage names
to ask this question.

THE TIME IT ENDED
-----------------
Where the record carries one, ``endedAt`` is resolved from the record's OWN
ending-time fields, in this order, and every one of them is also returned raw so
the choice is auditable rather than asserted:

``validTo`` (a bi-temporal window end) → ``expiredAt`` (the invalidation stamp
``invalidate_point``/``supersede_point`` write) → ``supersededAt`` (the
Object/Subject supersession stamp) → ``pipelineStageSince`` (when the terminal
stage was entered) → ``updatedAt``.

``updatedAt`` is last and deliberately still included: ``retract_point`` stamps
``expiredAt`` only from #11824 S2 onward, so today a retracted claim's ending
instant survives ONLY in ``updatedAt`` — which any later write moves. Omitting it
would report "no ending time" for the commonest ending in the graph; putting it
first would report the wrong one.
"""
from __future__ import annotations

from .live import _terminal_expression

#: The families this read serves. ``text``/``kind`` name each family's own
#: carriers (an Object's label is its ``name``, a Point's is its ``content``),
#: and ``about`` is the edge its occurrences are wired with — so the enumeration
#: reads each family by ITS OWN spellings instead of one family's, generalised.
#: Only knowledge kinds are here: an Event has no lifecycle (design §8) and an
#: Operator/Source has no ending this model defines.
FAMILIES: dict[str, dict[str, str]] = {
    "point": {
        "label": "Point",
        "text": "content",
        "kind": "pointKind",
        "about": "aboutPoint",
    },
    "object": {
        "label": "Object",
        "text": "name",
        "kind": "objectKind",
        "about": "aboutObject",
    },
    "subject": {
        "label": "Subject",
        "text": "name",
        "kind": "subjectKind",
        "about": "aboutSubject",
    },
}

#: The ending-time fields, in the order ``endedAt`` resolves them. Declared here
#: (not in the Cypher) so the docstring above, the query and the row cannot
#: drift, and so adding a carrier is one edit.
_TIME_FIELDS: tuple[str, ...] = (
    "validTo", "expiredAt", "supersededAt", "pipelineStageSince", "updatedAt",
)

#: Both spellings of the successor carrier. `supersededBy` is the field the
#: Object/Subject doors write; `superseded_by` is the shadow SNAKE spelling a
#: generic `update_entity` accepts and which lands a DIFFERENT property
#: (`sdk.py`'s `_SUCCESSOR_COLUMN` note), so a read that looked at one of them
#: would report "nothing replaced it" for a record whose other spelling names a
#: successor.
_SUCCESSOR_PROPS: tuple[str, ...] = ("supersededBy", "superseded_by")


def _coalesce(alias: str, fields: tuple[str, ...]) -> str:
    """``coalesce(n.f1, n.f2, …)`` — a missing property is NULL in Cypher."""
    return "coalesce(" + ", ".join(f"{alias}.{f}" for f in fields) + ")"


def build_cypher(entity_type: str, *, narrow_kinds: bool) -> str:
    """The ONE enumeration statement for a family.

    Kept a function (rather than a dict of three strings) so the selector, the
    ordering and the row contract are written once: the three statements differ
    only in the family's own carriers, which :data:`FAMILIES` supplies.

    ``narrow_kinds`` adds ``… IN $kinds`` to the WHERE. It is a parameter of the
    STATEMENT, not of the read path, because ``$kinds`` may not be passed when
    the caller named no kind (a ``None`` parameter cannot be compared with
    ``IN``) — and a read that filtered on a None would answer every row for a
    caller who asked for one kind.
    """
    family = FAMILIES[entity_type]
    label = family["label"]
    where = _terminal_expression("n")
    if narrow_kinds:
        where = f"({where} AND n.{family['kind']} IN $kinds)"
    # A Point's successor is the CORRECTS edge (`supersede_point` writes the
    # edge and NO `supersededBy`); an Object/Subject's is the carrier property.
    # Both are read, on every family, because a Point's `supersededBy` is
    # writable through `update_knowledge` and a reader that ignored it would
    # report a supersession that names nothing. The empty-list arm is not a
    # shortcut: the successor map for those families is the property, so there
    # is no edge to collect and the row keeps ONE shape on every family (a
    # caller never branches on which key a family happens to carry).
    prelude = (
        "OPTIONAL MATCH (succ:Point)-[:CORRECTS]->(n) "
        "WITH n, collect(DISTINCT succ.id) AS successor_ids "
        if entity_type == "point"
        else "WITH n, [] AS successor_ids "
    )
    return (
        f"MATCH (n:{label}) WHERE {where} "
        + prelude
        + f"OPTIONAL MATCH (e:Event)-[:{family['about']}]->(n) "
        "WITH n, successor_ids, collect(DISTINCT e.id) AS linked_events "
        "RETURN n.id, "
        f"n.{family['text']}, n.{family['kind']}, "
        "n.status, n.goalState, "
        "successor_ids, linked_events, "
        f"{_coalesce('n', _SUCCESSOR_PROPS)} AS named_successor, "
        "n.pipelineStageId, n.pipelineSlot, n.pipelineOutcome, "
        f"{_coalesce('n', _TIME_FIELDS)} AS ended_at, "
        + ", ".join(f"n.{f}" for f in _TIME_FIELDS)
        + ", n.isClosed, n.outdated "
        f"ORDER BY {_coalesce('n', _TIME_FIELDS)} DESC, n.id "
        "LIMIT $limit"
    )


def _successor_ids(successor_ids) -> list[str]:
    """The successor ids a row's CARRIER EDGE names, sorted and deduped.

    ⛔ The CORRECTS edge is the ONLY carrier that holds an ID. The
    ``supersededBy`` property is a **NAME** by the model's own definition (§4.2 /
    §4.3: *"successor NAME (stored verbatim)"*, and ``_supersede_entity`` writes
    the successor's name into it), so serving it under an ``…Id`` key would hand a
    caller a value it cannot resolve — and unioning the two would mix a name with
    ids in one list. That is why the two spellings are served under two keys
    (:func:`_successor_name` owns the other), and why only this list is
    ``successorId``.
    """
    return sorted({i for i in (successor_ids or []) if isinstance(i, str) and i})


def _successor_name(named) -> str | None:
    """The successor NAME the record carries, verbatim, or ``None``.

    Read from BOTH spellings of the carrier: ``supersededBy`` is the field the
    Object/Subject doors write, and ``superseded_by`` is the shadow SNAKE spelling
    a generic ``update_entity`` accepts and which lands a DIFFERENT property
    (``sdk.py``'s ``_SUCCESSOR_COLUMN`` note), so a read that looked at one of them
    would report "nothing replaced it" for a record whose other spelling names a
    successor.
    """
    return named if isinstance(named, str) and named else None


def _ended_row(raw: tuple, entity_type: str) -> dict:
    """One served row: the record, the ending it carries, and nothing derived.

    Every key is either the record's own value or an id list read from its own
    edges. There is no ``how`` token and no ``ended`` classification: the design
    fixes WHICH fields say how a record ended (§3, §6), and minting a summary
    beside them would be the second vocabulary this read exists without.
    """
    (node_id, text, kind, status, goal_state, successor_ids, linked_events,
     named_successor, stage_id, slot, outcome, ended_at, *times) = raw
    raw_times = dict(zip(_TIME_FIELDS, times, strict=False))
    is_closed, outdated = times[len(_TIME_FIELDS):][:2]
    id_list = _successor_ids(successor_ids)
    return {
        "id": node_id,
        f"{entity_type}Kind": kind,
        "entity_type": entity_type,
        # The family's own label under the key the caller already reads: a
        # Point's text is its `content`, an Object/Subject's is its `name`.
        ("content" if entity_type == "point" else "name"): text,
        "status": status,
        "goalState": goal_state,
        # ── how it ended, in the record's own fields ──────────────────────
        # The lifecycle word is served verbatim (`superseded`, `deprecated`, …);
        # the successor is TWO facts, never one: an ID (the CORRECTS edge — a
        # Point's successor) and a NAME (the `supersededBy` carrier — an
        # Object/Subject's, and a Point's if a caller wrote the property).
        # Serving the carrier under `successorId` would hand back a value no node
        # carries, which is worse than serving nothing.
        "successorId": id_list[0] if id_list else None,
        "allSuccessorIds": id_list,
        "successorName": _successor_name(named_successor),
        "stage": (
            {
                "id": stage_id,
                "slot": slot,
                "outcome": outcome,
                "since": raw_times.get("pipelineStageSince"),
            }
            if stage_id or slot
            else None
        ),
        "isClosed": is_closed,
        "outdated": outdated,
        # ── when ──────────────────────────────────────────────────────────
        "endedAt": ended_at,
        **raw_times,
        # ── planned vs happened ───────────────────────────────────────────
        # The events that record something about this record. Empty is the
        # "no occurrence was ever recorded" state, which is half of the
        # plan-never-happened answer (the other half is the terminal stage).
        "linkedEvents": sorted(
            {i for i in (linked_events or []) if isinstance(i, str) and i}),
    }


def enumerate_ended(graph, *, entity_type: str = "point", kind: str | None = None,
                    kinds: list[str] | None = None, limit: int = 50,
                    timeout_ms: int | None = None) -> list[dict]:
    """Every record of this family that has ENDED, newest ending first.

    ``graph`` is the live graph handle the read path already holds, so this costs
    no second connection. ``kinds`` is the caller's kind EXPANDED the way the
    read filters expand one (``sdk._read_filter_kinds``) — an unexpanded kind
    here would miss the descendants a caller's own filter would have matched.

    ``timeout_ms`` BOUNDS the statement, and the read path always passes its own
    budget. It is not optional-in-spirit: this is a family-wide
    ``MATCH … OPTIONAL MATCH … collect() … ORDER BY … LIMIT``, i.e. the most
    expensive single statement the read issues, and every other statement on that
    path is bounded for exactly this reason — an unbounded one hangs on a wedged
    store instead of degrading.

    The read is BOUNDED (``LIMIT $limit``) and ordered by the record's own
    ending time, so the caller gets the most recent endings rather than an
    arbitrary page of the population. It returns EXACTLY what it was asked for
    and never claims more: a caller that must tell "the whole population" from
    "this page" asks for one row beyond its page and compares the length — the
    read path does exactly that before it writes its truncation marker, because
    neither a capped read nor a full page can answer the question on its own.

    A family this module does not serve is refused rather than answered empty: an
    empty list is a factual claim ("nothing of that family ever ended") and an
    Event, an Operator or a Source has no ending this model defines at all — the
    two must not read the same.
    """
    if entity_type not in FAMILIES:
        raise ValueError(
            f"ended_scope answers for the knowledge kinds that have a lifecycle "
            f"({', '.join(sorted(FAMILIES))}) — not {entity_type!r}. An Event has "
            f"no lifecycle (design §8) and an Operator/Source has no ending this "
            f"model defines, so there is no ended population to enumerate."
        )
    where_kinds = bool(kinds)
    cypher = build_cypher(entity_type, narrow_kinds=where_kinds)
    params: dict = {"limit": int(limit)}
    if where_kinds:
        params["kinds"] = list(kinds or [])
    # ONE door call, so this read is ONE statement site (the census counts sites, and
    # two spellings of the same query would read as two queries). A caller that named
    # no budget keeps the door's own; the read path always passes one.
    kwargs = {} if timeout_ms is None else {"timeout": int(timeout_ms)}
    rows = graph.query(cypher, params=params, **kwargs).result_set
    return [_ended_row(tuple(r), entity_type) for r in rows
            if r and isinstance(r[0], str)]
