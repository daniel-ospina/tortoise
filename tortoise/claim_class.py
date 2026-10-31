"""The asserted-vs-derived CLAIM CLASS (#2351, epic #2349 workstream E-derived).

**What this is for.** Tortoise had no marker separating a claim someone
AUTHORED from one the system COMPUTED out of other beliefs. Both are Points,
both carry EP beliefs, and nothing distinguished them — so an EP-derived or
aggregation-written claim could out-rank, supersede, or belief-cascade over an
authored ground truth (the failure mode Utopia's *"derived facts are
second-class"* design exists to prevent, and the one the epic's indicator (3)
names: *"derived/inferred claims are distinguishable from asserted claims and
CANNOT override an asserted claim through EP or the write path"*).

**The marker.** One Point property, ``claimClass``, with a closed two-value
vocabulary: ``asserted`` (default — authored) and ``derived`` (computed).

⛔ ``claimClass`` is written ONLY when the class is ``derived``. Absence IS
``asserted``: every claim already in every graph is asserted by construction,
so the marker needs **no migration and no re-stamp** (the issue's indicator
(4)). Writing the string ``"asserted"`` explicitly is deliberately NOT stored —
two spellings of one class in the graph is exactly how a later reader comes to
treat one of them as a third state.

**The guard, one rule, three write surfaces plus the EP factor load.** A
DERIVED claim may not override an ASSERTED one:

* ``sdk.create_operator`` / ``sdk.create_direct_edge`` refuse to wire a derived
  source claim above an asserted target claim (IMPL and NAND — a NAND attack
  lowers the target's belief, which is the same override in the other
  direction).
* ``sdk.supersede_point`` refuses a derived successor replacing an asserted
  predecessor, and refuses a supersede whose **operator re-point** would
  rebuild an operator's factor as derived-over-asserted (#10195): the
  supersession moves the predecessor's whole edge set, so the successor
  inherits its edges at the SAME slot positions — the rebuilt factor is judged
  with the predicate below rather than a second copy of the rule. The
  method's direct-edge re-point arm is the same defect class, tracked as #9727.
* ``sdk.ingest``'s partial-absorb branch refuses to complete an operator's
  input set into a derived-over-asserted wiring.
* ``ep.TortoiseEP._affected_factors`` is the BACKSTOP for edges that already
  exist (written before this guard, or bulk-written): it blocks the
  derived→asserted leg of a factor, or drops the factor when that leg is the
  only one it has — the same discipline as the #689/#2422 terminal-state filter,
  and reported through ``_last_skipped_factors`` (#6105) rather than silently
  discarded.

⛔ The rule is DIRECTIONAL, and that is the point: a derived claim may still be
CORROBORATED BY an asserted one (asserted → derived flows). Dropping the whole
factor would make every derived claim EP-inert the moment it shares an operator
with any asserted claim — i.e. it would make the class a degradation rather than
a provenance marker. Only the leg that would let the computed claim push belief
INTO the authored one is blocked.

⚠️ The DOOR is strict and the BACKSTOP is lenient, on purpose. The write
surfaces refuse any wiring whose FLOWING legs include an override leg, which
means a class-MIXED edge must state the leg it means: `asserted --IMPL--> 
derived` is the corroboration wiring (`direction='->'`), and asking for it
bidirectionally is refused because the reverse leg is the override. The EP
backstop is the other half — it NARROWS an edge that already exists (a
pre-guard or bulk-written one) rather than refusing it, which is why a
``<->`` can appear as a ``<-`` in a factor's effective direction. Judging a
wiring from the source slot's class ALONE, ignoring the direction, inverted
both arms: it refused the legal ``<-`` FROM a derived claim (only
target->source flows) and admitted the illegal ``<-`` INTO one whose flowing
leg is the derived->asserted push.

⚠️ The narrowing is WHOLE-FACTOR, not per pair. ``factor_direction`` returns a
direction TOKEN, and ``_update_nary_factor`` derives ``mutual = fwd and bwd``
from it — so narrowing a ``<->`` to ``<-`` turns a mutual NAND's ``C(n,2)``
clique into a star and changes the conservation scale, and the whole forward leg
is blocked even into targets that are themselves ``derived``. That is wider than
the sentence above, deliberately accepted (a per-pair guard is a bigger change
to the update path than #2351 justifies) and recorded here so the next reader
does not read the rule as per-leg.

⚠️ The read-side parameter is named ``claim_class``, which is a HOMONYM of
``sufficiency_router``'s ``claim_class`` (``semantic`` | ``episodic``). The two
closed vocabularies are unrelated and neither is a superset of the other; they
live on different surfaces (this module's is the Point provenance marker,
``claimClass``). A caller mixing them gets a loud ``ValueError`` naming the
Point vocabulary, not a silent wrong answer.

⚠️ KNOWN RESIDUAL — narrowing does not CLEAR previously persisted messages.
``_update_factor`` writes only the legs a direction flows and never zeroes the
blocked one, and graph-mode ``_update_claim_posterior`` sums every persisted
``msg_*`` on the edge. So on an edge that PREDATES this guard **and** already
carries a message on the derived→asserted leg (written by a run before the
class was stamped), that stored message keeps contributing even though this
module now narrows the direction for the factor it builds. The write path
refuses such edges outright, and every factor built from now on writes only the
permitted leg — but the backstop does not retroactively neutralise history it
did not write. Clearing the blocked slots needs a change in the message-WRITE
path (``_update_factor`` / ``_update_nary_factor``), deliberately not made here:
it would change behaviour for every ``->`` / ``<-`` factor, well beyond this
issue. Tracked as #9722.

**The read side.** ``tortoise_fts_query`` takes ``claim_class`` — an optional
filter (applied pre-truncation, the same seam as ``exclude_status``) that ALSO
annotates every returned Point with its class, so a consumer can tell "this was
told to us" from "this was computed from other beliefs" without a new tool
(+0 MCP tools, #888/#1521 discipline).

⚠️ It is reachable from the SDK ONLY. ``sdk_method`` in ``tool_registry.py`` is
METADATA, not a dispatcher — MCP handlers are resolved by tool NAME and their
advertised input schema IS their own signature, so a kwarg added to the SDK verb
propagates to no served surface by itself. Concretely, the MCP
``tortoise_search`` handler and the hosted ``/v1/search`` route do NOT take or
forward ``claim_class`` (their kwargs stop at ``traversal_path``), so a tool
caller cannot reach this filter over MCP/REST today. That is a tool-SCHEMA
change and needs surface approval, so it is scoped out of #2351 and tracked as
#9725.

**What this deliberately does NOT do.** It does not decide WHAT may be derived.
Which producers (dream, projection-derived, extraction cross-links) should
stamp ``derived`` is a POLICY question, owned by the epic's policy layer — the
issue states it outright (*"it deliberately does NOT change what may be derived
(that stays policy-level)"*). This module ships the vocabulary, the guard and
the read contract; a producer adopts them by passing ``claim_class="derived"``.

**Uniqueness / ONTOLOGY.** The marker is a Point property in the §4.1 sense
(camelCase, like ``validFrom`` / ``goalState``) and it is DECLARED for replay
parity in ``projection.entities._POINT_DECLARED_PROPS``.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from typing import Any

from .live import direction_flows

logger = logging.getLogger(__name__)

#: The two classes. ``asserted`` is the default and the absence of the property.
ASSERTED = "asserted"
#: A claim computed from other claims — second-class by construction.
DERIVED = "derived"

#: The closed vocabulary (validated on every write).
CLAIM_CLASS_VALUES: frozenset[str] = frozenset({ASSERTED, DERIVED})

#: What an absent / unreadable marker MEANS. Absence is assertED, not unknown:
#: every claim authored before this marker existed is asserted.
DEFAULT: str = ASSERTED

#: The Point property. CamelCase — the §4.1 convention of ``validFrom`` /
#: ``goalState``. It is persisted ONLY for ``derived``.
PROP: str = "claimClass"

#: The read-side marker: the ONE fan-out statement (see ``classes_for_ids``).
#: Modelled on ``live.LIFECYCLE_FANOUT_QUERY`` (#7822) — every node carrying an
#: id participates in the verdict, because ids are not unique.
FANOUT_QUERY: str = (
    "MATCH (n:Point) WHERE n.id IN $ids "
    "RETURN n.id, coalesce(n.claimClass, 'asserted')"
)

#: Read-side junk (a hand-edited or foreign graph) is warned ONCE per distinct
#: value, not once per row — a search over a graph holding a bad token must not
#: flood the log.
_UNKNOWN_WARNED: set[str] = set()


def validate(value: Any) -> str:
    """Validate a CALLER-supplied class. ``None`` means "not declared".

    Write surfaces fail LOUD on anything outside the closed vocabulary — a
    typo'd class must never be persisted as an unknown third state (the same
    fail-closed posture as the status and direction vocabularies).
    """
    if value is None:
        return DEFAULT
    if not isinstance(value, str) or value not in CLAIM_CLASS_VALUES:
        raise ValueError(
            f"Unknown claim_class {value!r}. Valid values: "
            f"{sorted(CLAIM_CLASS_VALUES)} (or None for the default "
            f"{DEFAULT!r} — the class is written only when it is 'derived')."
        )
    return value


def normalize(value: Any) -> str:
    """Read-side classification of a stored value. TOTAL — never raises.

    ``None`` / empty / an unknown token all read as ``asserted``: absence is the
    documented default, and a read path (search, EP factor load) must never fail
    because a graph carries a token the writer would have refused. An unknown
    token is warned once so the junk is visible rather than absorbed.
    """
    if value is None or value == "":
        return DEFAULT
    if isinstance(value, str) and value in CLAIM_CLASS_VALUES:
        return value
    token = str(value)
    if token not in _UNKNOWN_WARNED:
        _UNKNOWN_WARNED.add(token)
        logger.warning(
            "claim class %r is not in the closed vocabulary %s — reading it as "
            "%r (absent/unknown IS the default class, #2351)",
            value, sorted(CLAIM_CLASS_VALUES), DEFAULT,
        )
    return DEFAULT


def is_derived(value: Any) -> bool:
    """True when the value names the derived class (read-side; total)."""
    return normalize(value) == DERIVED


def classes_for_ids(graph, ids: Sequence[str]) -> dict[str, frozenset[str]]:
    """Every class each id carries, ACROSS its whole node fan-out (#7822).

    Point ids are not unique (``create_point(..., id=X)`` twice is a tested,
    graph-reachable shape), so a class verdict read off one row would be
    server-row-order dependent — the #7822 defect class. An id is described by
    the SET of classes its nodes carry, and the guard is applied on "any
    derived" / "any asserted", which is the fail-closed direction either way.

    An id with NO node (or none carrying the marker) is ABSENT from the result —
    callers read absence as ``{asserted}`` (``DEFAULT``). One query, id-anchored,
    label-anchored on ``:Point`` (the marker's only home).
    """
    wanted = [pid for pid in ids if isinstance(pid, str) and pid]
    if not wanted:
        return {}
    rows = graph.query(
        FANOUT_QUERY, params={"ids": wanted}).result_set
    out: dict[str, set[str]] = {}
    for pid, cls in rows:
        out.setdefault(pid, set()).add(normalize(cls))
    return {pid: frozenset(v) for pid, v in out.items()}


def any_derived(classes: Iterable[str] | None) -> bool:
    """True when ANY node of an id carries the derived class."""
    return any(normalize(c) == DERIVED for c in (classes or ()))


def any_asserted(classes: Iterable[str] | None) -> bool:
    """True when ANY node of an id carries the asserted class.

    ⛔ An EMPTY set is "no node carried a marker", i.e. the default class —
    which IS asserted. That is what makes the override guard bite on ordinary
    (unmarked) claims without a migration.

    ⚠️ Because of that, an empty set is NOT how a caller says "this id is not a
    claim": a non-``:Point`` endpoint (``:Subject``/``:Object``/``:Event``,
    #7902) has no ``claimClass`` at ALL, and reading it as the asserted default
    invents an override that cannot exist. Callers use ``None`` for that — see
    :func:`factor_direction_over_claims` (the load side) and the ``:Point``-only
    class sets the write doors build.
    """
    classes = list(classes or ())
    if not classes:
        return True
    return any(normalize(c) == ASSERTED for c in classes)


def blocks_override(source_classes: Iterable[str] | None,
                    target_classes: Iterable[str] | None) -> bool:
    """The ONE rule: a DERIVED source may not override an ASSERTED target."""
    return any_derived(source_classes) and any_asserted(target_classes)


def override_refusal(*, source_id: str, target_ids: Sequence[str],
                     surface: str) -> str:
    """The refusal a write surface raises for a derived-over-asserted wiring.

    Names both endpoints and the ways out, because a refusal that does not say
    how to proceed gets worked around (the caller's real options are: the
    computed side is marked ``derived``; or the direction is restated so only
    the corroboration leg flows; or the edge is wired FROM an asserted claim).

    ⛔ DIRECTION-AGNOSTIC ON PURPOSE. :func:`direction_blocks_create` is fed the
    requested direction and also blocks on its BACKWARD leg
    (``blocks_override(target, source)`` — a DERIVED *target* under an ASSERTED
    *source*), so a message asserting that ``source_id`` is the derived one
    names the wrong claim in that arm and exonerates the claim that actually
    triggers the refusal (#10195 review P3). This names the pair and the rule,
    which is true on both legs.
    """
    return (
        f"{surface}: the claims {source_id!r} and {list(target_ids)!r} cannot "
        f"be wired this way — on a leg this wiring FLOWS, a DERIVED claim would "
        f"override an ASSERTED one (#2351). A computed claim never overrides an "
        f"authored one — through IMPL, or as a NAND attack, which lowers the "
        f"target's belief. Either give BOTH sides the same class (mark every claim "
        f"on the COMPUTED side 'derived' — the marker lives only on claims that "
        f"are computed, and its absence IS 'asserted'), or restate the direction "
        f"so the only leg that flows is the corroboration one, asserted -> "
        f"derived."
    )


def supersede_refusal(*, old_id: str, new_id: str) -> str:
    """The refusal ``supersede_point`` raises for a derived successor."""
    return (
        f"supersede_point: successor {new_id!r} is DERIVED and cannot replace "
        f"the ASSERTED claim {old_id!r} (#2351). Supersession is an override — "
        f"a computed claim never terminalises an authored belief. Create the "
        f"replacement as an asserted claim (the authored path), or correct the "
        f"authored claim directly."
    )


#: The three ways the caller can know (or not know) which slot is the SOURCE.
#: A closed vocabulary rather than a bool because the CANDIDATE arm is entered by
#: two layouts with two DIFFERENT causes, and a single "not proven" phrase named
#: the wrong one (#10418 review P3).
_SLOT_ORDER_PHRASES: dict[str, str] = {
    # ``ep.py`` orders ``coalesce(r.idx, 0), c.id`` — position is proven.
    "proven": "at the SOURCE position",
    # Cross-type comparison is the ENGINE's inter-type hierarchy (``STRING``
    # groups ahead of numeric, ``null`` last), which this door deliberately does
    # not re-implement — so no numeric reading of a mixed layout names the
    # source the loader reads.
    "mixed": (
        "at a position the load side may read as the SOURCE (the slots' idx "
        "values are of MIXED types, and this door does not re-derive the "
        "engine's cross-type slot order)"),
    # One type group IS ordered by the engine (comparison inside a group is
    # type-local), but the door still declines to re-implement that per-type
    # order — so the slots ARE orderable and saying otherwise is false.
    "uniform-non-int": (
        "at a position the load side may read as the SOURCE (the slots' idx "
        "values are non-integer, and this door does not re-derive the "
        "engine's per-type slot order)"),
}


def repoint_refusal(*, operator_id: str, op_type: str, old_id: str,
                    new_id: str, source_id: str,
                    target_ids: Sequence[str],
                    direction: str,
                    slot_order: str = "proven") -> str:
    """The refusal ``supersede_point``'s OPERATOR re-point raises (#10195).

    A separate message from :func:`override_refusal` because the caller's ways
    out are different: the wiring is not the thing they asked for — they asked
    for a SUPERSEDE, and the operator edges are riding along. The predicate is
    the same one every other door uses (:func:`direction_blocks_create`); only
    the remedy differs.

    It also states the ORDER, because a refusal that lands after the transfer
    would be the #329/#4021 partial-mutation defect regardless of what it says.

    ⛔ DIRECTION-AGNOSTIC ON PURPOSE, and it names the operator's DIRECTION as
    well as its slots. ``direction_blocks_create`` blocks on either leg, so a
    message that hardcoded "``source_id`` is DERIVED … above the ASSERTED
    ``target_ids``" was backwards on the backward leg (a DERIVED *target* under
    an ASSERTED *source*) — exonerating the very claim that triggered it, in the
    message a caller reads to decide what to do (#10195 review P3).

    ``direction`` is REQUIRED: the only caller feeds
    ``_canonical_direction``'s result, which is never ``None``, and a default
    here would be a SECOND copy of the absent->default rule (and would mislabel
    a NAND, whose absent default is ``unidirectional``).

    ``slot_order`` is one of :data:`_SLOT_ORDER_PHRASES`' keys and states WHY
    the source position is (or is not) provable — ``proven``, ``mixed`` (a
    cross-type layout), or ``uniform-non-int`` (one non-``int`` type group).
    ⛔ It is NOT a bool, and the candidate arm's phrase is NOT "cannot be
    ordered": a uniform non-``int`` layout IS ordered by the engine (comparison
    inside one type group is type-local) — the door declines to re-derive that
    order, which is a different thing, and a refusal that names the wrong cause
    sends the caller looking for a defect in their own graph (#10418 review P3).
    """
    _source_phrase = _SLOT_ORDER_PHRASES[slot_order]
    return (
        f"supersede_point: re-pointing the OPERATOR {operator_id!r} ({op_type}, "
        f"direction {direction!r}) from {old_id!r} onto {new_id!r} would leave a "
        f"DERIVED claim overriding an ASSERTED one on a leg this operator FLOWS "
        f"(#2351) — the rebuilt factor's slots are {source_id!r} "
        f"{_source_phrase} and {list(target_ids)!r} at the target position(s), "
        f"and one side of that wiring is DERIVED while the other is ASSERTED. A "
        f"computed claim never overrides an authored one — through IMPL, or as a "
        f"NAND attack, which lowers the target's belief. A supersede moves the "
        f"predecessor's whole edge set, so the successor inherits the factor "
        f"with its slots rebuilt — and that rebuilt wiring is one "
        f"create_operator refuses to write. Either create the successor with the "
        f"class that keeps the factor's slots on one side of the boundary "
        f"(`claim_class` is a CREATE-time marker — see the supersede refusal "
        f"above for the override rule itself), or rebuild the operator on the "
        f"successor before superseding. The supersede was refused BEFORE any "
        f"mutation."
    )


def direction_blocks_create(direction: str | None,
                            source_classes: Iterable[str] | None,
                            target_classes: Iterable[str] | None) -> bool:
    """True when the requested WIRING would let a derived claim override an asserted one.

    The door-side counterpart of :func:`factor_direction`: it asks the question
    of the legs the requested direction actually FLOWS, so a one-way ``<-`` is
    judged on its target->source leg rather than on the source slot's class.
    Judging it on the source alone inverted both arms — it refused the legal
    shape (``<-`` from a derived source CORROBORATES: only target->source
    flows) and admitted the illegal one (``<-`` into a derived target, whose
    flowing leg is exactly the derived->asserted push).

    Keeping this in ONE place is the point: the write doors and the EP backstop
    read the same rule, so the door cannot refuse a shape the load side keeps,
    or create one the load side must drop.
    """
    fwd, bwd = direction_flows(direction)
    return bool(
        (fwd and blocks_override(source_classes, target_classes))
        or (bwd and blocks_override(target_classes, source_classes))
    )


def factor_direction_over_claims(
        direction: str | None,
        source_classes: Iterable[str] | None,
        target_classes: Iterable[str] | None,
) -> str | None:
    """``factor_direction`` for a factor whose endpoints may NOT be claims.

    ⛔ ``claimClass`` is a ``:Point``-only property, so an operator or direct
    edge may legitimately touch a ``:Subject`` / ``:Object`` / ``:Event``
    (#7902). Such an endpoint is not "an ASSERTED claim" — it is not a claim at
    all: nothing can override it, and it can override nothing. And because
    ``any_asserted(∅)`` is True (absence of a marker on a CLAIM is the asserted
    default), an absent/empty side MUST NOT be passed through to
    :func:`factor_direction` — that reads the non-claim as an authored belief and
    silently narrows an edge the write doors deliberately keep.

    ``None`` (or an empty collection) therefore means "no claim on this side":
    the guard does not apply and the direction is returned UNCHANGED. This is
    the load-side half of the SAME rule the write doors apply by building their
    class sets from ``:Point`` rows only — the door cannot refuse a shape the
    load side drops, nor the load side drop one the door keeps.
    """
    if not source_classes or not target_classes:
        # Mirror ``factor_direction``'s unchanged exit: a bare ``None`` is the
        # DROP signal, and an absent token means the mutual default.
        return direction if direction is not None else "<->"
    return factor_direction(direction, source_classes, target_classes)


def factor_direction(direction: str | None, source_classes: Iterable[str] | None,
                     target_classes: Iterable[str] | None) -> str | None:
    """The direction an EP factor may actually use, after the override guard.

    ``None`` means NO leg may flow — the caller drops the factor (the
    #689/#2422 discipline: a dead arm contributes nothing, and the drop is
    reported).

    Semantics, mirroring ``ep._update_factor``: the factor's SOURCE is
    ``input_ids[0]`` (positional, for an operator the idx-0 input; for a direct
    edge the source endpoint) and ``fwd`` is the source→target leg, ``bwd`` the
    target→source leg. The guard blocks exactly one leg per class direction:

      * source DERIVED, target ASSERTED → ``fwd`` blocked (the computed claim
        may not push belief into the authored one); ``bwd`` stays, so the
        derived claim is still corroborated by the authored belief.
      * source ASSERTED, target DERIVED → ``bwd`` blocked (the computed claim
        may not push back into the authored one).

    A direction that already flows NOTHING (``-``) is returned verbatim: there
    is no cascade to guard, so there is nothing to report either.
    """
    fwd, bwd = direction_flows(direction)
    if not fwd and not bwd:
        return direction
    new_fwd, new_bwd = fwd, bwd
    if fwd and blocks_override(source_classes, target_classes):
        new_fwd = False
    if bwd and blocks_override(target_classes, source_classes):
        new_bwd = False
    if (new_fwd, new_bwd) == (fwd, bwd):
        # Unchanged. NEVER return a bare ``None`` here: ``None`` is this
        # function's DROP signal, and ``direction_flows(None)`` is (True, True)
        # — an absent token means the mutual default, which is a FLOWING
        # direction, so it must not be confused with "no leg may flow".
        # (This is also the ONLY both-legs-survive exit: the two blocks below
        # can only ever clear a leg, never restore one, so "both still flow but
        # the token changed" cannot occur.)
        return direction if direction is not None else "<->"
    if new_fwd:
        return "->"
    if new_bwd:
        return "<-"
    return None
