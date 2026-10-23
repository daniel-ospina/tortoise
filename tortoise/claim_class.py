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
  predecessor.
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

**The read side.** ``tortoise_fts_query`` / MCP ``tortoise_search`` take
``claim_class`` — an optional filter (applied pre-truncation, the same seam as
``exclude_status``) that ALSO annotates every returned Point with its class, so
a consumer can tell "this was told to us" from "this was computed from other
beliefs" without a new tool (+0 MCP tools, #888/#1521 discipline).

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
from typing import Any, Iterable, Sequence

from .live import canonical_direction, direction_flows

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

    Names both endpoints and the two ways out, because a refusal that does not
    say how to proceed gets worked around (the caller's real options are: the
    target is itself computed — mark it ``derived``; or the claim genuinely
    needs to sit above an authored belief, and then the AUTHORED claim is what
    must carry the edge).
    """
    return (
        f"{surface}: claim {source_id!r} is DERIVED and cannot be wired above "
        f"the ASSERTED claim(s) {list(target_ids)!r} (#2351). A computed claim "
        f"never overrides an authored one — through IMPL, or as a NAND attack, "
        f"which lowers the target's belief. Either mark the target "
        f"claim_class='derived' (if it too is computed), or wire the edge from "
        f"an asserted claim."
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
        return direction if direction is not None else "<->"
    if new_fwd and new_bwd:
        # Both legs survive but the token was rewritten from the legacy
        # spelling — canonicalize so the stored/served value is a model value.
        return canonical_direction(direction) or "<->"
    if new_fwd:
        return "->"
    if new_bwd:
        return "<-"
    return None
