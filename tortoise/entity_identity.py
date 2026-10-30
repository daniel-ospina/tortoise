"""Route-then-refuse resolution for Object/Subject identity reads (#3633).

An ``Object``/``Subject`` identity coordinate reaches a read site in one of two
shapes:

* an **id** — opaque and stable, so it names exactly one node; or
* a **name** — a mutable *natural key* that, after #3590's D2, **two or more
  live entities may hold at the same time** (same-name coexistence is legal and
  is produced by explicit-id writers).

A read that resolves a name as if it were an id therefore has a choice the
caller never expressed: *which* of the live carriers did it mean? The historic
answers were both wrong — ``MATCH`` returning several rows and unioning their
result sets **folds two identities into one value**, and a bare ``LIMIT 1``
**silently picks** one carrier (``result_set[0]`` is backend-order-dependent).

This module is the ONE place that answers that question. It applies the
governing plan's §B.1 disposition, *route then refuse*
(``docs/plans/2026-09-15-3590-minted-identity.md``):

1. **route** — resolve the coordinate to **exactly one id**. An exact id match
   wins outright (an id is an unambiguous identity; a name that happens to equal
   another node's id loses). Otherwise the name is resolved over **live**
   holders only.
2. **refuse** — when a name is held by two or more live entities, do not guess
   and do not union: record a **structured** non-folded entry (see
   :func:`record_non_folded` / :func:`non_folded_entries`) and raise
   :class:`AmbiguousEntityName`. A name held only by TERMINAL carriers (D2's
   ``single_terminal_holder_reference``) is likewise refused — never resolved
   to a terminal id — and recorded (shape ``no-live-holder``) rather than
   silently returning ``None``, so "the holder is terminal" stays
   distinguishable from "no such name". A refusal is a record, never a silently
   passing warning: the decision record R8 requires the refused-event set to be
   asserted on, and the log line is operator visibility *alongside* the entry,
   not the entry itself.

The "live" predicate is ``live._terminal_excluded`` — the same vocabulary every
other read surface uses — so a terminal (superseded/deprecated/archived/
retracted/outdated) holder never resolves a name.

SCOPE BOUNDARY: the plan's Slice 0 owns the *durable* per-projection
non-folded journal (``_record_non_fold`` in ``projection``) and the R9
replay-time assertion over it; that slice is NOT on ``main``. Until it lands,
:func:`non_folded_entries` is a process-level assertable record, and a refusal
that raises is also carried structurally on
:class:`AmbiguousEntityName` / :class:`UnaddressableEntityName`.

Do NOT re-implement this resolution at a call site. A hand-rolled
``id = $x OR name = $x`` disjunction is exactly the defect this module replaces,
and a hand-rolled ``LIMIT 1`` is the silent-pick variant of it.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from typing import NamedTuple

from tortoise.live import _terminal_excluded

logger = logging.getLogger(__name__)

#: The only labels whose ``name`` is a legal *natural key*. Anything else is
#: refused loudly rather than interpolated into a query (parity with
#: ``resolve_structural_target``'s runtime label defense).
_IDENTITY_LABELS = ("Object", "Subject")

#: The properties a name coordinate may be matched against. ``name`` is the
#: canonical natural key; ``canonical_name`` is the SECOND spelling an Object
#: may be addressed by (``mining``'s reification writes it, ``belief_timeline``
#: reads it). Both are mutable strings — neither is an identity, so both are
#: resolved by the SAME holder-count discipline. A property outside this set is
#: refused (it is interpolated into the statement).
_NAME_PROPERTIES = ("name", "canonical_name")

#: The labels an id-typed edge endpoint is resolved over by the write path
#: (``projection.__init__``'s ``_RESOLVE_BRANCHES``). A resolved id's
#: uniqueness is checked in the CALLER's space: ``create_edge`` resolves by
#: id|eventId|url for the SOURCE and id|eventId for the TARGET, dedupes by the
#: node's own identity property, and MERGEs one edge per resolved endpoint — so
#: an id two endpoints would resolve to silently wires the edge to BOTH, or
#: re-targets it. ``Event`` keys on ``eventId``; a ``Source`` may also key on
#: ``url`` (source side only).
_ID_ENDPOINT_BRANCHES = (("Point", "id"), ("Subject", "id"),
                         ("Object", "id"), ("Source", "id"),
                         ("Event", "eventId"))
_URL_ENDPOINT_BRANCHES = (("Source", "url"),)

#: ``addressing`` modes: the SOURCE OR-set includes ``Source.url``; the TARGET
#: OR-set does not (a url-only stub is not a valid target — preserving prior
#: ``create_edge`` behaviour).
ADDRESSING_SOURCE = "source"
ADDRESSING_TARGET = "target"


class AmbiguousEntityName(ValueError):
    """A name resolves to two or more live carriers — refuse, never guess.

    Carries structured fields so an MCP/HTTP boundary can map it to a 409
    without string-matching the message (the plan's "Open for the implementer"
    shape: ``label``, ``name``, ``candidate_ids``).
    """

    def __init__(self, label: str, name: str, candidate_ids):
        self.label = label
        self.name = name
        self.candidate_ids = tuple(str(c) for c in candidate_ids)
        super().__init__(
            f"{label} {name!r} resolves to {len(self.candidate_ids)} live "
            f"carriers {list(self.candidate_ids)} — refusing to guess "
            f"(route-then-refuse, #3633)")


def _require_identity_label(label: str) -> None:
    if label not in _IDENTITY_LABELS:
        raise RuntimeError(
            f"entity identity: unsafe label {label!r} "
            f"({'/'.join(_IDENTITY_LABELS)} only)")


class NonFoldedEntry(NamedTuple):
    """One structured refusal: the coordinate and the carriers NOT folded.

    This is the record — not the log line. The durable, per-projection
    refused-event set and the R9 invariant that asserts on it live in the
    #3590 plan's Slice 0 (``_record_non_fold`` in ``projection/__init__``),
    which is NOT on ``main``; until it lands, this process-level set is the
    assertable record (see :func:`non_folded_entries`).
    """

    label: str
    name: str
    candidate_ids: tuple
    shape: str  # "ambiguous-name" | "unaddressable-name" | "duplicate-id"
    #            | "endpoint-collision" | "no-live-holder"


_non_folded: list[NonFoldedEntry] = []
_non_folded_lock = threading.Lock()


def record_non_folded(label: str, name: str, candidate_ids, *,
                      shape: str = "ambiguous-name") -> NonFoldedEntry:
    """Record a refused coordinate as a structured, assertable entry.

    The ENTRY is the record; the log line is operator visibility alongside it.
    (The decision record R8 is explicit that "a refused fold is an assertion
    failure, not a log line" — the durable assertion is Slice 0's, see
    :class:`NonFoldedEntry`. Do not describe the log line as the record.)

    A refusal site that must keep running (a candidate resolver, a batch probe
    that skips one ref) calls this before dropping the ref; a site that must
    fail loud calls :func:`_refuse`, which records and then raises.
    """
    entry = NonFoldedEntry(str(label), str(name),
                           tuple(sorted(str(c) for c in candidate_ids)), shape)
    with _non_folded_lock:
        _non_folded.append(entry)
    logger.warning(
        "entity-identity: refusing a name-keyed read — %s %r resolves to %d "
        "carriers %s (shape=%s); recorded a non-folded entry and refused to "
        "guess (#3633 route-then-refuse)",
        label, name, len(entry.candidate_ids), list(entry.candidate_ids),
        shape)
    return entry


def non_folded_entries() -> tuple[NonFoldedEntry, ...]:
    """Every non-folded entry recorded this process (assertable).

    INTERIM: the plan's Slice 0 makes this per-projection and drives the R9
    invariant; until it lands the harness asserts on this process-level tuple.
    """
    with _non_folded_lock:
        return tuple(_non_folded)


def clear_non_folded_entries() -> None:
    """Drop the recorded entries (test isolation; no production caller)."""
    with _non_folded_lock:
        _non_folded.clear()


def _refuse(label: str, name: str, candidate_ids, *,
            shape: str = "ambiguous-name") -> None:
    """Record the non-folded entry, then refuse.

    Recording before raising keeps the evidence even when a caller catches the
    exception.
    """
    record_non_folded(label, name, candidate_ids, shape=shape)
    raise AmbiguousEntityName(label, name, candidate_ids)


def live_name_holder_ids(g, label: str, name: str, *,
                         case_insensitive: bool = False,
                         name_props: tuple = ("name",)) -> list:
    """Every LIVE holder of ``name`` for ``label``, by ``n.id`` (raw).

    The list is **row-per-holder**, so an id-less legacy/hosted-stub holder
    (``MERGE (o:Object {name:$name})`` mints those) contributes an entry whose
    value is ``None``. Refusal MUST count holders, not truthy ids: a name held
    by one id-carrying and one id-less live entity is still TWO live holders
    and must refuse — filtering the ``None`` out would silently pick one.

    ``case_insensitive`` opts into ``toLower`` name matching, needed by read
    surfaces that historically matched names case-insensitively (e.g.
    ``provenance``). It is OFF by default so identity resolution stays an
    exact-key match everywhere else.

    Zero rows is a legal "no live holder"; the caller decides refusal. This is
    the primitive a refusal site uses when it needs the raw holder set (e.g. a
    batch probe that must keep ``id``/``name`` matching apart).
    """
    return _name_holder_ids(g, label, name,
                            case_insensitive=case_insensitive, live_only=True,
                            name_props=name_props)


def _adopt_idless_holder(g, label: str, name: str, node_id: str) -> None:
    """Stamp a freshly minted ``node_id`` onto the single id-less LIVE holder.

    #3590 review F2: ``MERGE (o:Object {name:$name})`` (a legacy / raw /
    foreign writer's construction) mints a row with NO id. A create that merely
    minted a fresh id BESIDE such a row left TWO live carriers of one name —
    after which :func:`resolve_or_mint` refuses the name
    (:class:`AmbiguousEntityName`) and there is no by-name repair path at all:
    the name is create-poisoned, permanently and with nothing recording how it
    got that way.

    ADOPTING keeps ONE carrier: the row that already holds the name becomes
    addressable, the caller's write (which MERGEs on ``id``) folds onto it
    instead of opening a second node, and the name stays create-able. The id is
    MINTED here — never derived from the name (#3590 R1/R2), and not a reuse of
    any other entity's id (R5).

    MUST run with the ``(label, name)`` create lock held — every caller of
    :func:`resolve_or_mint` holds it (:func:`entity_create_lock`, reentrant for
    the resolve), and the write has to land before the lock is released or a
    concurrent create re-mints the very second carrier this prevents.
    """
    _require_identity_label(label)
    g.query(
        f"MATCH (n:{label}) "
        f"WHERE {_name_predicate(False)} "
        f"AND {_terminal_excluded('n.status')} "
        "AND n.id IS NULL "
        "SET n.id = $id",
        params={"name": name, "id": node_id})


#: One lock per ``(label, name)`` for the CREATE-side resolve-or-mint
#: (:func:`resolve_or_mint`) **and the write it authorises**. The resolve is
#: check-then-act and D2 legalises same-name coexistence, so no unique index can
#: close the race — the lock closes it WITHIN A PROCESS. The registry is
#: module-level (mirroring ``sdk._source_merge_locks``), so two processes can
#: still each see zero live holders and each mint; that residual is named in the
#: issue and is bounded by the pre-beta premise (no production Object/Subject
#: graph).
#:
#: ⛔ Every door that can make a name held takes it, not just the create: a door
#: that writes a holder is a door the create's resolve must not pass, or the two
#: interleave and leave two LIVE carriers of one name (create-poisoning it, with
#: a NON-exempt ``ambiguous-entity-name`` fold record, so ``rebuild_all`` stops).
#: The doors are ``sdk.create_entity``, ``sdk._update_entity``'s rename
#: (``_refuse_ambiguous_rename`` + its write) and
#: ``projection.edges._mint_subject_stub`` — the last name-keyed WRITE in the
#: tree, whose two orderings against a create both converge once locked (#9876
#: review).
#:
#: ⛔ It is an ``RLock`` because the critical section is **resolve AND write**, not
#: the resolve alone (#3590 review, finding 1): ``sdk.create_entity`` holds
#: :func:`entity_create_lock` across ``resolve_or_mint`` and the ``MERGE`` that
#: makes the holder visible, so a second thread cannot pass the empty-holder
#: check while the first is still between its resolve and its write. A plain
#: ``Lock`` would deadlock there, because the resolve re-enters for the same key.
#:
#: ⚠️ DECLARED RESIDUAL (same family as ``sdk._source_merge_locks``): the
#: registry gains one lock per distinct ``(label, name)`` and nothing prunes it,
#: so a long-lived hosted process keeps every entity name it has ever created.
#: Pruning is deliberately NOT done here: removing an entry another thread has
#: already fetched but not yet acquired would let a third thread mint a SECOND
#: lock for the same key, which reopens the very race this closes. Bounded
#: memory here is a name-key-lifecycle question, not a lock-plumbing one.
_holder_locks: dict[tuple, threading.RLock] = {}
_holder_locks_guard = threading.Lock()


def _holder_lock(label: str, name: str) -> threading.RLock:
    with _holder_locks_guard:
        lock = _holder_locks.get((label, name))
        if lock is None:
            lock = _holder_locks[(label, name)] = threading.RLock()
        return lock


@contextlib.contextmanager
def entity_create_lock(label: str, name: str):
    """Hold the ``(label, name)`` create lock across resolve **and** write.

    #3590 review (finding 1): :func:`resolve_or_mint` alone closes nothing — it
    returns a minted id and then RELEASES the lock, so two threads both see zero
    live holders, both mint, and both write a node of their own. The holder
    becomes visible only at the caller's ``MERGE``, so the guard has to span
    that write. ``RLock`` makes the nesting legal for the resolve inside it.

    Reentrant for the same thread, so a writer that calls :func:`resolve_or_mint`
    inside the block (or a helper that re-enters) behaves exactly as before.
    """
    with _holder_lock(label, name):
        yield


def resolve_or_mint(g, label: str, name: str, *,
                    explicit_id: str | None = None) -> tuple[str, bool]:
    """The CREATE-side resolver: ``(id, created)``. Never derives from the name.

    ``#3590`` R1/R2: an entity's durable identity is an opaque id minted ONCE at
    creation; ``name`` is a mutable natural key. So a create either

    * resolves to the **single live holder** of ``name`` (``created=False``) —
      the idempotent re-mention (#452) — or
    * **adopt** the single LIVE holder that carries NO id (a legacy / raw row)
      — stamping a minted id onto it, so the create lands on ONE carrier
      instead of opening a second one (#3590 review F2), or
    * **mint** a fresh opaque id (``created=True``) when NO live entity holds
      the name. A terminal holder does not block a create: it is a different
      incarnation, and re-using its id would be id reuse (R5).

    Two or more LIVE holders is not a choice a create may make for the caller:
    it refuses with :class:`AmbiguousEntityName` (route-then-refuse, R8), so a
    duplicate-name graph is never create-poisoned by picking one.

    ``explicit_id`` wins outright — an explicit-id channel (a connector's
    ``github-issue-{repo}-{n}``, ``_server_id``) supplies the identity, which is
    exactly what an opaque-id model allows and what ``add_object(id=…)``
    promises. It is never a name derivation.

    Called ONLY from a write surface that can JOURNAL the mint (the SDK's
    ``create_entity`` and ``EventAPI``'s registration emitters). The projection
    never mints: a fold-time mint is not replay-deterministic, so it would make
    ``rebuild != live`` by construction.
    """
    _require_identity_label(label)
    if explicit_id:
        return str(explicit_id), True
    with _holder_lock(label, name):
        holders = live_name_holder_ids(g, label, name)
        # COUNT holders, REPORT holders (#9876 review, this head): the
        # earlier ``[h for h in holders if h]`` performed exactly the
        # filter `live_name_holder_ids` forbids, so a name held by one
        # id-carrying and one id-less LIVE entity reported "resolves to 1
        # live carriers" for a refusal that happened because there were
        # TWO — the #8018 class (a report false for the record it
        # describes). It also bypassed :func:`_refuse`, so the create
        # side left NO non-folded entry: R8's "a refusal is a record"
        # was unsatisfied at the one door an SDK caller reaches, and
        # ``sdk._connect_issue_objects`` swallows the exception. Routing
        # through ``_refuse`` fixes both halves — it records, and
        # ``display_holder_ids`` labels the id-less carrier instead of
        # dropping it (the same evidence `NameAlreadyHeld` ships).
        # The threshold itself lives in `refuse_if_ambiguous` so the
        # pre-flight probe (`refuse_ambiguous_create`) cannot disagree.
        refuse_if_ambiguous(label, name, holders)
        if holders and holders[0]:
            return str(holders[0]), False
        from .ids import ulid
        if holders:
            # ONE LIVE holder, carrying NO id (a legacy / foreign / raw
            # ``MERGE (n:Label {name:…})`` row). ⛔ ADOPT it, never mint beside
            # it (#3590 review F2): a second live carrier makes every later
            # create of this name refuse `AmbiguousEntityName`, with no
            # by-name repair path — the name create-poisoned for good, and
            # silently. Stamping the id here makes that row addressable, and
            # the caller's write (which MERGEs on `id`) then folds onto it, so
            # ONE carrier remains. `created=True` because the node carries no
            # registration of its own: the caller's journal line is what makes
            # the adopted id replayable.
            adopted = ulid()
            _adopt_idless_holder(g, label, name, adopted)
            return adopted, True
        # Zero live holders: mint an opaque id. The returned id is only half of
        # the contract: the WRITE that makes it visible must happen before this
        # lock is released, or a concurrent create passes the same empty-holder
        # check (#3590 review, finding 1). A caller that cannot write under the
        # lock must wrap resolve+write in :func:`entity_create_lock` —
        # ``sdk.create_entity`` does.
        return ulid(), True


def resolve_fold_name(g, label: str, name: str, *,
                      context: str = "", mention: bool = False) -> str | None:
    """Resolve a NAME to one live id **for a fold** — never mints, never guesses.

    #3590 D9: the projection is not the minter. A fold-time mint cannot be
    journaled, so replay would mint a different id and ``rebuild != live`` by
    construction — the exact defect the whole change exists to remove. So a
    record that references an entity by NAME resolves to the single LIVE holder
    and otherwise is left unfolded:

    * one live holder -> its id (the edge/link is wired on that id);
    * two or more live holders -> ``ambiguous-name`` recorded, nothing folded;
    * no live holder (or only a terminal/id-less one) -> ``unregistered-
      reference`` recorded, nothing folded.

    ``mention=True`` classifies the caller's reference as loose ATTRIBUTION (an
    Event's ``subject``/``object``/``uses[]``, an about-edge name) rather than
    the identity of the record being folded, and records the EXEMPT
    :data:`SHAPE_UNFOLDED_MENTION` instead. The
    distinction is load-bearing: a mention that cannot resolve drops ONE link
    and moves no entity's state, and the live pass and the replay pass drop the
    same one (they run this same resolver) — so it is a producer omission, not a
    live/replay divergence. The IDENTITY shapes stay NON-exempt: a record whose
    own target cannot be resolved (an id-less ``ObjectSuperseded``, a rename/
    delete/restatus miss) must still fail the run rather than silently not
    apply.

    ⛔ **The classification is per USE, not per field** (#3590 review F1/F2).
    The same event field can be both, and the two uses must be resolved
    separately:

    * ``_fold_object_status``'s ``about_entities`` target DOES pass
      ``mention=True``: the fold's own contract says a non-match on that LOOSE
      list is not a violation, and a non-exempt record there made
      ``rebuild_all`` refuse journals that replay on ``main`` — one extracted
      event naming an unregistered subject was enough (the durable-copy
      regression this exemption exists to prevent).
    * the event's EXPLICIT ``object``, once it reaches the same fold, must NOT:
      it is the fold's declared target and the fold ``SET o.status`` on it, so
      its miss is re-resolved WITHOUT ``mention=True`` and fails the run.
      Taking the exemption there left a lifecycle event that folded nothing
      behind a green run.

    Why the mention exemption exists at all: this repo's own journal writers
    reference names they never registered, and the retired fold minted a stub for
    them. Without the exemption every such journal becomes UN-REBUILDABLE — the
    durable copy of the data can no longer be recovered from its journal (#3590
    review P1).

    ⛔ **D1 is RULED (owner, 2026-10-26): a bare mention does not create an
    entity.** So the remedy is NOT "the producer registers what it references" —
    that was this docstring's earlier claim, and it is the minting arm D1
    forbids. The ruling's own sentence: a name written inside a statement is a
    mention, "never a creation, so the entity set grows only from an explicit
    registration … that mints the id; an unresolvable mention drops its link,
    records the miss, and mints nothing." The entity set is grown by
    ``create_entity`` alone; a caller that wants the attribution edge registers
    the target explicitly, and every unregistered mention is dropped here.

    ⚠️ **These live in-repo producers do NOT register every name they mention,
    and they are named here so the cost is recorded rather than silent:**
    ``tortoise.ingest --capture-metadata`` writes ``sessionCaptured`` through
    ``EventAPI.add_event`` with ``subject="pi-agent"`` and
    ``uses=[{"name": "tortoise-capture", "kind": "skill"}]``
    (``tortoise/ingest.py``), and the SDK's own ``create_event`` door leaves its
    ``subject``/``object``/``uses`` names unregistered (D1). Those attribution
    edges are dropped, on the live pass and on replay alike, and recorded as
    exempt :data:`SHAPE_UNFOLDED_MENTION` (bounded and reconstructible: the
    journal still replays, which is the whole point of the exemption).
    ⛔ **The ``uses`` half of the ``--capture-metadata`` record is NOT one of
    them**: that producer WANTS the ``uses→skill`` attribution edge (#125 pins
    it), so it takes D1's own remedy for a wanted edge and registers the target
    explicitly — ``api.add_object("tortoise-capture", "skill")`` before the
    event — and the fold then resolves it. Measured on CI run 40917794096: while
    it did not, the #125 assertion ``uses >= 1`` was red, because nothing in the
    journal ever grew the entity the mention named. What this door keeps losing
    for that producer is its ``subject="pi-agent"`` mention alone.
    ``tests/test_3590_minted_identity_acceptance.py::
    test_a_bare_mention_mints_nothing_and_drops_its_link`` is the pin;
    ``test_the_event_api_door_is_the_recorded_producer_that_loses_its_mentions``
    pins the door-level loss.
    """
    holders = live_name_holder_ids(g, label, name)
    ids = [str(h) for h in holders if h]
    if len(holders) == 1 and ids:
        return ids[0]
    from tortoise.projection.nonfolded import (
        SHAPE_AMBIGUOUS_ENTITY_NAME,
        SHAPE_UNFOLDED_MENTION,
        SHAPE_UNREGISTERED_REFERENCE,
        record_non_folded,
    )
    if mention:
        shape = SHAPE_UNFOLDED_MENTION
    else:
        shape = (SHAPE_AMBIGUOUS_ENTITY_NAME if len(holders) > 1
                 else SHAPE_UNREGISTERED_REFERENCE)
    # ``nonfolded.record_non_folded(shape, **fields)`` takes the R8 vocabulary
    # (label / candidates / detail) — NOT the identity module's positional
    # ``(label, name, candidate_ids, shape=)``. Passing ``name=``/``ids=``/
    # ``context=`` raised a TypeError INSIDE the fold (after a rebuild has
    # already wiped the graph), i.e. the fail-closed path crashed instead of
    # recording. Verified against ``nonfolded.NonFoldedEvent``'s fields.
    # ``candidates`` is R8's "the candidates" — so it must name every holder the
    # refusal counted, including the id-less one. ``ids`` (truthy only) stays
    # the local for the resolve arm and the log line, which says "id-carrying";
    # the RECORD uses ``display_holder_ids`` (#9876 review, this head): an
    # id-less LIVE carrier routes to the NON-exempt shapes and would otherwise
    # be recorded as ``candidates=()`` — "refused with no candidates" for a
    # refusal that happened because there was one.
    record_non_folded(shape, label=label,
                      candidates=tuple(display_holder_ids(holders)),
                      detail=f"name={name!r}" + (
                          f" cause={'ambiguous-holders' if len(holders) > 1 else 'no-live-holder'}"
                          if mention else "") + (
                          f" context={context}" if context else ""))
    logger.warning(
        "entity-identity: a journal record references %s %r by NAME and it "
        "resolves to %d live id-carrying holder(s) %s — NOT folding it. The "
        "projection never mints an id (a fold-time mint is not replay-"
        "deterministic), and D1 (RULED: a bare mention does not create an "
        "entity) means NOTHING will register a mention on its behalf — this "
        "reference drops its link and the miss is recorded%s. If the "
        "attribution edge is wanted, register the target explicitly "
        "(create_entity).%s", label, name, len(ids), ids,
        " as the exempt unfolded-mention" if mention else " above",
        f" context={context}" if context else "")
    return None


def _require_name_properties(name_props: tuple) -> tuple:
    """The name properties a coordinate may be matched against, validated."""
    props = tuple(name_props)
    if not props or any(p not in _NAME_PROPERTIES for p in props):
        raise RuntimeError(
            f"entity identity: unsafe name property {name_props!r} "
            f"({'/'.join(_NAME_PROPERTIES)} only)")
    return props


def _name_predicate(case_insensitive: bool,
                    name_props: tuple = ("name",)) -> str:
    """The name-match predicate — ONE copy, shared by every holder probe.

    A second copy is a drift site: the live probe and the all-status probe
    must compare names identically or a name can be live-looking to one and
    terminal-looking to the other.

    ``name_props`` widens the match to every name spelling a caller accepts
    (``{"name", "canonical_name"}`` for ``belief_timeline``). The disjunction
    is PARENTHESISED: it is composed with ``AND`` by every caller, and an
    unparenthesised ``a = $x OR b = $x AND <live>`` would let the status
    predicate bind to the second arm only.
    """
    props = _require_name_properties(name_props)
    arms = [f"toLower(n.{p}) = toLower($name)" if case_insensitive
            else f"n.{p} = $name" for p in props]
    name_pred = " OR ".join(arms)
    return f"({name_pred})" if len(arms) > 1 else name_pred


def _name_holder_ids(g, label: str, name: str, *,
                     case_insensitive: bool,
                     live_only: bool,
                     name_props: tuple = ("name",)) -> list:
    """Raw holder ids for ``name``; ``live_only`` applies D2's predicate."""
    _require_identity_label(label)
    live_pred = f"AND {_terminal_excluded('n.status')} " if live_only else ""
    rows = g.query(
        f"MATCH (n:{label}) "
        f"WHERE {_name_predicate(case_insensitive, name_props)} "
        f"{live_pred}"
        "RETURN n.id",
        params={"name": name}).result_set
    return [r[0] for r in rows]


def all_name_holder_ids(g, label: str, name: str, *,
                        case_insensitive: bool = False,
                        name_props: tuple = ("name",)) -> list:
    """Every holder of ``name`` for ``label`` — ANY status (raw ids).

    The complement of :func:`live_name_holder_ids`: used ONLY to tell a
    terminal-only reference (a holder exists, none live) from a genuinely
    absent name, so the former can be recorded as a non-folded
    ``no-live-holder`` refusal instead of silently returning ``None``
    (#3633 §B.1 "refuse + record").
    """
    return _name_holder_ids(g, label, name,
                            case_insensitive=case_insensitive, live_only=False,
                            name_props=name_props)


def unique_name_holder_id(g, label: str, name: str) -> str | None:
    """The ONE id-carrying holder of ``name`` (**any** status) — else ``None``.

    The FOLD-SIDE address resolution: it answers "which id does this name
    denote", never "which node should I write". It resolves only when there is
    exactly ONE carrier, which is what makes it safe where a name-keyed
    ``MERGE`` was not — a name with 2+ carriers has no single answer, so the
    caller folds NOTHING and lets its own classifier decide (fail-closed, and
    no bailment of a carrier the caller never chose — #3573's burial class).

    **ANY status, deliberately.** ``main``'s ``MATCH (o:Object {name:$name})``
    fallback in ``_fold_object_superseded`` carried no status predicate, so an
    already-terminal target re-folded blind (``cas=False`` replay is last-wins).
    A live-only index would refuse those journals instead — one journal, two
    verdicts, across the release boundary.

    ⛔ It RECORDS NOTHING. A miss here is classified ONE level up, by the same
    predicate the sweep and ``check_consistency`` use
    (``classify_terminalizer_miss`` + ``supersede_object_address``), because a
    fold-site ``unregistered-reference`` would refuse a journal the recorded
    D2 exemption covers (``object-superseded-unregistered``: the journal
    established NEITHER address the record carries, so live and replay agree by
    construction). This is the quiet half of :func:`resolve_fold_name`, which
    is the same resolution for the folds that own their own miss.

    An id-less carrier is NOT addressable (:func:`_require_unique_id`'s
    precondition is an id to fold on), so a name whose only holder carries no
    id answers ``None`` — the same shape ``apply_supersessions`` refuses on the
    write side (``unaddressable-name``).
    """
    holders = all_name_holder_ids(g, label, name)
    ids = [str(h) for h in holders if h]
    if len(holders) == 1 and ids:
        return ids[0]
    return None


def _endpoint_idents(g, value: str, *, include_url: bool) -> set:
    """The distinct endpoint idents ``create_edge`` would resolve for ``value``.

    Mirrors ``projection._resolve_entity``: one indexed lookup per branch, then
    DEDUPE BY THE NODE'S OWN IDENTITY (``id | eventId | url``, in that order) —
    the same dedup the caller applies. Counting NODES instead would over-count
    a Source matched by both its id and its url; counting only ``n.id`` (the
    previous probe) UNDER-counts a Source whose ``url`` equals the value but
    whose ``id`` differs — which MERGEs a SECOND edge.
    """
    branches = list(_ID_ENDPOINT_BRANCHES)
    if include_url:
        branches += list(_URL_ENDPOINT_BRANCHES)
    union = " UNION ".join(
        f"MATCH (n:{label} {{{key}:$v}}) RETURN n.id, n.eventId, n.url"
        for label, key in branches)
    rows = g.query(union, params={"v": value}).result_set
    idents = set()
    for rid, eid, url in rows:
        ident = rid or eid or url
        if ident:
            idents.add(ident)
    return idents


def _require_unique_id(g, label: str, node_id: str, *,
                       addressing: str | None = None,
                       claimant_predicate: str | None = None) -> str:
    """A name resolved to ``node_id`` — but the id itself must be unclaimed.

    TWO independent corruption checks, both required:

    1. **Same-space claimant count** — more than one node in the resolver's own
       candidate space claims the id. This is raw corruption and it is checked
       FIRST, independently of ``addressing``: the endpoint-ident dedup below
       COLLAPSES same-ident claimants, so relying on it alone would let two
       nodes of one label claiming one id through (they would then be folded by
       every downstream ``id = $sid`` leg). ``claimant_predicate`` overrides the
       candidate space (``_DOCUMENT_PREDICATE`` for the document resolver,
       whose space is an Object OR a document Source).
    2. **Caller-space endpoint collision** (``addressing``) — one coordinate
       resolves to TWO DISTINCT endpoints in the caller's OR-set (e.g. a
       ``Source`` whose ``url`` equals the value but whose ``id`` differs),
       because ``create_edge`` MERGEs an edge per resolved endpoint. See
       :func:`_endpoint_idents`.

    The document/approver paths pass ``addressing``; label-narrowed readers do
    not.

    LIMIT (check 2 only): a same-ident cross-label collision (two labels
    claiming one id) is a single endpoint under the caller's own first-branch-
    wins precedence — pre-existing ``create_edge`` semantics, not a fold, and
    not detected here.
    """
    claimant_space = claimant_predicate or f"(n:{label})"
    claimants = g.query(
        f"MATCH (n) WHERE {claimant_space} AND n.id = $id RETURN n.id",
        params={"id": node_id}).result_set
    if len(claimants) > 1:
        _refuse(label, node_id, [node_id] * len(claimants),
                shape="duplicate-id")
    if addressing:
        idents = _endpoint_idents(
            g, node_id, include_url=addressing == ADDRESSING_SOURCE)
        if len(idents) > 1:
            _refuse(label, node_id, sorted(idents),
                    shape="endpoint-collision")
    return node_id


def display_holder_ids(candidate_ids) -> list:
    """Human/log ids for a refused holder set — an id-less holder is named.

    Public so every refusal site (the resolver, ``assembly``, the onboarding
    seed) records the SAME evidence: a bare ``if c`` filter would drop the
    id-less holder that made the read ambiguous in the first place.
    """
    return [c if c is not None else "<id-less>" for c in candidate_ids]


def refuse_if_ambiguous(label: str, name: str, holders) -> None:
    """Refuse a CREATE whose name 2+ LIVE entities hold — records and raises.

    ⛔ THE ONE REFUSAL PREDICATE (#9876 review, this head). ``holders`` is the
    raw :func:`live_name_holder_ids` row list (id-less LIVE rows INCLUDED, per
    that function's own contract), and ``len(holders) > 1`` is the create's
    route-then-refuse (R8) threshold. Two sites ask it and both call here, so
    they cannot answer differently:

    * :func:`resolve_or_mint` — the write door, which already holds the rows;
    * :func:`refuse_ambiguous_create` — the READ-ONLY pre-flight probe a
      caller runs before its first write (``hosted_api``'s commit path), so
      the refusal it predicts is byte-identical to the one the door raises.

    Recording BEFORE raising keeps the R8 evidence (the non-folded entry) even
    though the caller catches the exception — and it is why a pre-flight probe
    that short-circuits the door does not silently lose the record.
    """
    if len(holders) > 1:
        _refuse(label, name, display_holder_ids(holders))


def refuse_ambiguous_create(g, label: str, name: str) -> None:
    """The **read-only** pre-flight twin of :func:`resolve_or_mint`'s refusal.

    Same predicate, same evidence, ONE graph read — no lock, no mint, no write:
    a caller that must refuse an ambiguous create BEFORE its first write (the
    hosted commit path, where the create lands at step 6 of the write phase and
    everything before it has already been journaled) probes here instead of
    re-deriving "is this name doubly held?". Re-deriving it is how the two
    answers drift: the count that refuses is LIVE ROWS, id-less included.

    Raises :class:`AmbiguousEntityName` after recording the non-folded entry;
    returns ``None`` when the create would proceed (0 or 1 live holder — a
    terminal holder does not block a create, R5).
    """
    refuse_if_ambiguous(label, name, live_name_holder_ids(g, label, name))


class UnaddressableEntityName(ValueError):
    """A name resolves to exactly ONE live holder that carries **no id**.

    Unambiguous but unaddressable by an id-keyed read: every post-#3590 write
    path mints an id, so an id-less holder is legacy/raw. Refuse loudly (with a
    recorded non-folded entry) rather than return a vacuous/empty value that is
    indistinguishable from "no data".
    """

    def __init__(self, label: str, name: str):
        self.label = label
        self.name = name
        super().__init__(
            f"{label} {name!r} resolves to exactly one live holder with no id — "
            f"unaddressable by an id-keyed read; refusing (#3633)")


def _record_unaddressable(label: str, name: str) -> None:
    """Record the non-folded entry, then refuse an id-less single holder."""
    record_non_folded(label, name, ["<id-less>"], shape="unaddressable-name")
    raise UnaddressableEntityName(label, name)


def _record_if_any_holder(g, label: str, value: str,
                          case_insensitive_name: bool,
                          *, predicate: str | None = None,
                          name_props: tuple = ("name",)) -> None:
    """Record a ``no-live-holder`` refusal iff ``value`` has ANY holder.

    Called only on the resolver's "no live holder" path: a name with at least
    one (necessarily terminal) holder is D2's refused terminal-only reference;
    a name with NO holder is simply absent and must NOT be recorded.
    """
    if predicate is None:
        holders = all_name_holder_ids(
            g, label, value, case_insensitive=case_insensitive_name,
            name_props=name_props)
    else:
        holders = [r[0] for r in g.query(
            f"MATCH (n) WHERE {predicate} "
            f"AND {_name_predicate(case_insensitive_name, name_props)} "
            "RETURN n.id",
            params={"name": value}).result_set]
    if holders:
        record_non_folded(label, value, display_holder_ids(holders),
                          shape="no-live-holder")


def resolve_entity_id(g, label: str, value: str | None, *,
                      case_insensitive_name: bool = False,
                      addressing: str | None = None,
                      name_props: tuple = ("name",)) -> str | None:
    """Route-then-refuse: the single live id for an id-or-name coordinate.

    Exact id match wins (returned regardless of status — an id is unambiguous
    even when its carrier is terminal; if two nodes CLAIM one id, that is
    corruption and refuses). Otherwise a NAME is resolved over live holders:
    exactly one -> its id (verified to be uniquely claimed); none live ->
    ``None`` (a TERMINAL-only holder is refused per D2 and recorded as a
    ``no-live-holder`` non-folded entry, so it is distinguishable from a
    genuinely absent name); **two or more live -> refuse**
    (:class:`AmbiguousEntityName`, non-folded entry recorded).

    ``addressing`` (``ADDRESSING_SOURCE`` / ``ADDRESSING_TARGET``) makes the
    uniqueness check cover the CALLER's full endpoint-resolution space (see
    :func:`_endpoint_idents`).

    ``name_props`` names the properties the NAME arm matches (default
    ``("name",)``). A caller whose coordinate may be spelled by a deprecated
    alias (``belief_timeline``'s ``canonical_name``) passes the wider set so the
    holder count — and therefore the refusal — is taken over the SAME key space
    the read used to union, instead of re-implementing a second resolution.
    """
    if value is None:
        return None
    _require_identity_label(label)
    by_id = g.query(
        f"MATCH (n:{label} {{id:$value}}) RETURN n.id",
        params={"value": value}).result_set
    id_hits = [r[0] for r in by_id if r[0]]
    if len(id_hits) > 1:
        # Two nodes claiming one id is raw corruption, never a resolvable read.
        _refuse(label, value, id_hits, shape="duplicate-id")
    if id_hits:
        if addressing:
            return _require_unique_id(g, label, id_hits[0],
                                      addressing=addressing)
        return id_hits[0]
    by_name = live_name_holder_ids(g, label, value,
                                  case_insensitive=case_insensitive_name,
                                  name_props=name_props)
    if len(by_name) > 1:
        _refuse(label, value, display_holder_ids(by_name))
    if by_name:
        if by_name[0] is None:
            _record_unaddressable(label, value)
        return _require_unique_id(g, label, by_name[0], addressing=addressing)
    # No LIVE holder. D2's ``single_terminal_holder_reference`` vector refuses
    # a terminal-only reference (a resolver must never hand a caller a terminal
    # id). Record it so "the holder is terminal" is distinguishable from "no
    # such name" — both return None, but only one is a refusal (#3633 §B.1).
    _record_if_any_holder(g, label, value, case_insensitive_name,
                          name_props=name_props)
    return None


#: A document is a ``:Source`` carrying ``documentKind`` (D10, ONTOLOGY v3.15
#: §4.4); the pre-D10 ``:Document`` label is retired.
_DOCUMENT_PREDICATE = "(n:Object OR (n:Source AND n.documentKind IS NOT NULL))"


def resolve_document_target_id(g, value: str | None) -> str | None:
    """Route-then-refuse for an Object **or** document Source coordinate.

    The candidate space is the one ``file_human_approval`` has always probed:
    an ``Object``, or a ``Source`` carrying ``documentKind``. ``url`` is a legal
    coordinate on EITHER (``create_object(..., url=...)`` stores it on an
    Object), so the url arm probes the whole predicate, not just ``Source``.
    Precedence is id > url > name — the two exact identifiers first (an id is
    unambiguous; a document's ``url`` is its identity key), the natural key
    last. A name/url held by two or more live candidates, or an id claimed by
    two nodes, refuses rather than unioning. A resolved id is then verified
    unique across the CALLER's addressing space (``ADDRESSING_LABELS``), since
    the id-keyed write keeps the first label match.

    Returns the resolved node's ``id``, or ``None`` when nothing matches.
    """
    if value is None:
        return None
    id_rows = g.query(
        f"MATCH (n) WHERE {_DOCUMENT_PREDICATE} AND n.id = $value "
        "RETURN n.id",
        params={"value": value}).result_set
    id_hits = [r[0] for r in id_rows if r[0]]
    if len(id_hits) > 1:
        _refuse("Object|document-Source", value, id_hits,
                shape="duplicate-id")
    if id_hits:
        return _require_unique_id(
            g, "Object", id_hits[0], addressing=ADDRESSING_TARGET,
            claimant_predicate=_DOCUMENT_PREDICATE)
    url_rows = g.query(
        f"MATCH (n) WHERE {_DOCUMENT_PREDICATE} AND n.url = $value "
        "RETURN n.id, n.url",
        params={"value": value}).result_set
    # Holder-count refusal first (two candidates sharing a url is ambiguity,
    # not a pick), then addressability: a node carrying a ``url`` but NO ``id``
    # has no id for an id-keyed write to address — `create_edge`'s TARGET OR-set
    # is id|eventId only (`projection/edges.py`), so returning its url would
    # make `file_human_approval` report success while silently dropping the
    # `uses` edge. That is the "vacuous value indistinguishable from no data"
    # this module refuses.
    if len(url_rows) > 1:
        _refuse("Object|document-Source", value,
                display_holder_ids([r[0] for r in url_rows]))
    if url_rows:
        if url_rows[0][0] is None:
            _record_unaddressable("Object|document-Source", value)
        return _require_unique_id(
            g, "Object", url_rows[0][0], addressing=ADDRESSING_TARGET,
            claimant_predicate=_DOCUMENT_PREDICATE)
    name_rows = g.query(
        f"MATCH (n) WHERE {_DOCUMENT_PREDICATE} "
        f"AND {_name_predicate(False)} "
        f"AND {_terminal_excluded('n.status')} "
        "RETURN n.id",
        params={"name": value}).result_set
    # Holder-count refusal (a None entry is an id-less live holder).
    name_hits = [r[0] for r in name_rows]
    if len(name_hits) > 1:
        _refuse("Object|document-Source", value,
                display_holder_ids(name_hits))
    if name_hits:
        if name_hits[0] is None:
            _record_unaddressable("Object|document-Source", value)
        return _require_unique_id(
            g, "Object", name_hits[0], addressing=ADDRESSING_TARGET,
            claimant_predicate=_DOCUMENT_PREDICATE)
    # Same D2 refusal as the label resolver: a terminal-only holder is refused
    # and recorded, so it is distinguishable from an absent name.
    _record_if_any_holder(g, "Object|document-Source", value, False,
                          predicate=_DOCUMENT_PREDICATE)
    return None
