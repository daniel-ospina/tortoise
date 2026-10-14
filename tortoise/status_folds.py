"""Pack-declared Object status folds (#2729) — THE ONE HOME of the
``event kind → Object.status`` vocabulary.

Before this module the fold lived as a hand-typed ``if/elif`` in
``projection/entities.py`` (``github.issue.open``/``reopened`` →
``in_progress``; ``github.issue.closed``/``pm:cardCompleted`` →
``completed``), so a domain pack could not declare *its own* kind's status
vocabulary or the event→status rules that update it. Meeting/venture lifecycle
events (an action item completed, a tranche released, a condition met) had no
way to move state without an engine edit — the defect #2729 exists to remove.

The declaration surface is per-kind, in the pack manifest::

    ontology:
      kindDefs:
        tranche:
          statuses: [pending, conditions-met, released, denied]
          folds:
            - event: disbursement      # this pack's local event kind
              status: released

Three contracts this module owns:

1. **The declaration is a closed vocabulary.** A fold's ``status`` must be one
   of the kind's declared ``statuses``; ``statuses`` must be a non-empty list
   of unique non-blank strings; a fold's ``event`` must be an event kind THIS
   pack declares (bare local name or ``self_ns:local``). An unknown status or
   an orphan event is a manifest-validation ERROR — the pack is isolated, the
   same contract every other kindDef field already carries.

2. **A kind that declares a lifecycle OWNS its state.** The built-in connector
   folds are retained verbatim (``BUILTIN_CONNECTOR_FOLDS``) as a
   compatibility pack, but they do NOT apply to an Object whose ``objectKind``
   is a pack-declared lifecycle kind. This is the structural fix for the
   collision the #2725 conflict check found: ``open`` folds to ``in_progress``
   and ``closed`` to ``completed`` in the connector vocabulary, so a new kind
   declaring ``open``/``closed`` for its own lifecycle would otherwise
   silently inherit connector semantics — one word, two incompatible
   lifecycles. Dispatch is on the NAMESPACED kind (``venture:tranche``), so
   two packs declaring the same bare kind name cannot collide at the fold.

3. **The overlap is DETECTED, not silent.** When a declared ``statuses`` list
   contains a word the shared connector vocabulary also uses (``open``,
   ``closed``, ``active``, ``pending``, ``in_progress``, ``completed``) the
   registry records an explicit warning
   (``PackRegistry.lifecycle_warnings``) naming the colliding words. It is a
   WARNING, not an error: an owner-approved pack (the venture pack's
   ``fundingAgreement`` uses ``active``/``closed``) legitimately reuses the
   spelling with a different meaning, and contract 2 already makes the write
   safe — what is left is reader confusion, which must be visible rather than
   silent.

This module imports nothing from ``pack_registry`` (which imports IT), so the
dependency is one-directional and cycle-free.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: The connector compatibility pack. Retained verbatim as a BUILT-IN (open
#: decision (c) on #2729: "whether pack-less graphs keep built-in github/pm
#: folds as a compatibility pack" — yes) so a graph with no lifecycle-declaring
#: pack behaves byte-for-byte as before. The two ``pm:`` keys are the LEGACY
#: pre-namespacing spellings that still live in old journals and are replayed
#: on every rebuild (``tortoise/migrate_kinds.py`` maps ``cardCompleted`` →
#: ``pm:cardCompleted``); ``cardCompleted`` is deliberately NOT in the
#: project-management pack's ``eventKinds``, so this map is the only home that
#: keeps those graphs folding — which is why the orphan lint below exempts
#: built-ins rather than requiring them to be declared.
BUILTIN_CONNECTOR_FOLDS: dict[str, str] = {
    "pm:cardCreated": "in_progress",
    "github.issue.open": "in_progress",
    "github.issue.reopened": "in_progress",
    "pm:cardCompleted": "completed",
    "github.issue.closed": "completed",
}

#: Status words the connector/shared vocabulary already spells. A pack may
#: declare them (the venture pack does), but the registry must say so.
#: ``in_progress``/``completed`` are the two values the built-in fold WRITES;
#: ``open``/``closed``/``active``/``pending`` name the same lifecycle in the
#: connector spelling (the collision the #2725 check found).
SHARED_CONNECTOR_STATUS_WORDS: frozenset[str] = frozenset({
    "open", "closed", "active", "pending", "in_progress", "completed",
})

#: The only keys a ``folds[]`` entry may carry.
VALID_FOLD_KEYS: frozenset[str] = frozenset({"event", "status"})


def _declared_statuses(pack, kind: str) -> list:
    """The kind's declared ``statuses`` list, or ``[]`` when absent/ill-typed.

    Ill-typed values are the validator's business (it rejects the manifest);
    every reader here degrades to "no lifecycle declared" so a half-loaded
    registry can never raise on the projection hot path.
    """
    kd = pack.kind_defs.get(kind)
    if not isinstance(kd, dict):
        return []
    value = kd.get("statuses")
    return list(value) if isinstance(value, list) else []


def _declared_folds(pack, kind: str) -> list:
    kd = pack.kind_defs.get(kind)
    if not isinstance(kd, dict):
        return []
    value = kd.get("folds")
    return list(value) if isinstance(value, list) else []


def validate_status_decl(
    kind: str,
    spec: dict,
    *,
    namespace: str,
    declared_event_kinds: set[str],
    declared_object_kinds: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Validate one kindDef's ``statuses``/``folds`` declaration.

    Returns ``(errors, warnings)``. An error isolates the pack (the caller
    routes it into ``PackRegistry.errors``); a warning is recorded in
    ``PackRegistry.lifecycle_warnings`` and never blocks the load.

    ``declared_event_kinds`` is the DECLARING pack's own bare event kinds —
    a fold's ``event`` resolves against it, never against the shared/connector
    vocabulary (contract 2 makes that reference unnecessary, and forbidding it
    keeps the collision from re-entering through the declaration).

    ``declared_object_kinds`` is the pack's own ``objectKinds`` list. A
    declaration on a CORE kind (valid in ``kindDefs``, but not this pack's own
    Object kind) is a **silent no-op** in the index — the fold dispatches on the
    namespaced `ns:kind`, and there is no `ns:core-kind` — so it is an error,
    not a documented liberty.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if ("statuses" in spec or "folds" in spec) and declared_object_kinds is \
            not None and kind not in declared_object_kinds:
        errors.append(
            f"kindDefs '{kind}': 'statuses'/'folds' requires '{kind}' in this "
            f"pack's objectKinds — the fold dispatches on the namespaced kind "
            f"({namespace}:{kind}), which does not exist for a core or "
            f"non-Object kind"
        )

    statuses_present = "statuses" in spec
    statuses = spec.get("statuses")
    if statuses_present:
        if not isinstance(statuses, list) or not statuses:
            errors.append(
                f"kindDefs '{kind}': 'statuses' must be a non-empty list of "
                f"strings"
            )
            statuses = None
        else:
            for value in statuses:
                if not isinstance(value, str) or not value.strip():
                    errors.append(
                        f"kindDefs '{kind}': 'statuses' entries must be "
                        f"non-empty strings, got {value!r}"
                    )
            if len(set(statuses)) != len(statuses):
                errors.append(
                    f"kindDefs '{kind}': 'statuses' contains duplicates"
                )
            if all(isinstance(v, str) for v in statuses):
                collisions = sorted(
                    set(statuses) & SHARED_CONNECTOR_STATUS_WORDS)
                if collisions:
                    warnings.append(
                        f"kindDefs '{kind}': 'statuses' reuses the shared "
                        f"connector vocabulary word(s) {', '.join(collisions)} — "
                        f"this kind owns its own state (the built-in connector "
                        f"fold does not apply to it), but the spelling is "
                        f"ambiguous to a reader; rename or record the intent"
                    )

    folds_present = "folds" in spec
    if folds_present and not statuses_present:
        errors.append(
            f"kindDefs '{kind}': 'folds' requires 'statuses' — a fold target "
            f"must be one of the kind's declared status values"
        )
    if folds_present:
        folds = spec.get("folds")
        if not isinstance(folds, list) or not folds:
            errors.append(
                f"kindDefs '{kind}': 'folds' must be a non-empty list"
            )
            folds = []
        for i, fold in enumerate(folds):
            if not isinstance(fold, dict):
                errors.append(
                    f"kindDefs '{kind}': folds[{i}] must be a map with "
                    f"'event' and 'status'"
                )
                continue
            unknown = sorted(set(fold) - VALID_FOLD_KEYS)
            if unknown:
                errors.append(
                    f"kindDefs '{kind}': folds[{i}] has unknown key(s) "
                    f"{', '.join(unknown)} (allowed: "
                    f"{', '.join(sorted(VALID_FOLD_KEYS))})"
                )
            event = fold.get("event")
            if not isinstance(event, str) or not event:
                errors.append(
                    f"kindDefs '{kind}': folds[{i}] 'event' must be a "
                    f"non-empty string"
                )
            elif not _fold_event_resolves(event, namespace,
                                          declared_event_kinds):
                errors.append(
                    f"kindDefs '{kind}': folds[{i}] 'event' {event!r} is not "
                    f"an event kind declared by this pack (declared: "
                    f"{', '.join(sorted(declared_event_kinds)) or 'none'}) — "
                    f"an orphan fold key would never fire"
                )
            status = fold.get("status")
            if isinstance(statuses, list) and all(
                    isinstance(v, str) for v in statuses):
                if status not in statuses:
                    errors.append(
                        f"kindDefs '{kind}': folds[{i}] 'status' {status!r} is "
                        f"not in the kind's declared statuses "
                        f"({', '.join(statuses)})"
                    )
            elif not isinstance(status, str) or not status:
                errors.append(
                    f"kindDefs '{kind}': folds[{i}] 'status' must be a "
                    f"non-empty string"
                )
    return errors, warnings


def _fold_event_resolves(
    event: str, namespace: str, declared_event_kinds: set[str]
) -> bool:
    """Does ``event`` name an event kind this pack declares?

    Accepts the bare local spelling and the self-namespaced one
    (``disbursement`` / ``venture:disbursement``). A namespaced reference into
    another pack is NOT accepted: cross-pack fold targets are out of scope for
    #2729, and admitting them would let the connector vocabulary back in
    through the declaration.
    """
    if ":" in event:
        ns, _, local = event.partition(":")
        return ns == namespace and local in declared_event_kinds
    return event in declared_event_kinds


class StatusFoldIndex:
    """The compiled ``event kind → (namespaced kind, status)`` fold surface.

    Built from a loaded ``PackRegistry`` (see :func:`build_status_fold_index`).
    Immutable by convention: built once per registry and cached on it.
    """

    __slots__ = ("by_event", "lifecycle_kinds", "statuses_by_kind")

    def __init__(self) -> None:
        #: namespaced event kind → [(namespaced object kind, status), ...]
        self.by_event: dict[str, list[tuple[str, str]]] = {}
        #: namespaced object kinds that declare a `statuses` lifecycle
        self.lifecycle_kinds: frozenset[str] = frozenset()
        #: namespaced object kind → declared status tuple (order preserved)
        self.statuses_by_kind: dict[str, tuple[str, ...]] = {}

    def folds_for_event(self, event_kind: str) -> list[tuple[str, str]]:
        """The declared ``(kind, status)`` targets for one event kind.

        Empty for a built-in-only or unknown event — the caller then applies
        the compatibility fold, which knows nothing about kinds.
        """
        return list(self.by_event.get(event_kind, ()))

    def statuses_for(self, namespaced_kind: str) -> tuple[str, ...]:
        return self.statuses_by_kind.get(namespaced_kind, ())

    def owns_state(self, namespaced_kind: str | None) -> bool:
        return bool(namespaced_kind) and namespaced_kind in self.lifecycle_kinds


def build_status_fold_index(packs) -> StatusFoldIndex:
    """Compile the fold index from ``{namespace: PackManifest}``.

    Every declared ``statuses`` list becomes a lifecycle kind, EVEN WHEN the
    kind declares no folds: owning the vocabulary is what suppresses the
    built-in connector fold for that kind, and a kind whose state moves only
    through an explicit write (the SDK's ``restatus``) still must not be
    silently re-labelled by a connector event.

    CATALOG-level, not graph-gated: the index is compiled from every loaded
    pack, never from a graph's ``:PackInstall`` approval set. A kind already
    stored keeps folding after an approval is revoked — the declaration
    describes the kind's lifecycle and `Object.status` is a cache over the
    events that name the Object, so stranding existing state would be a data
    bug. `:PackInstall` still gates EXTRACTION (which kinds may be minted).
    """
    index = StatusFoldIndex()
    statuses_by_kind: dict[str, tuple[str, ...]] = {}
    by_event: dict[str, list[tuple[str, str]]] = {}
    for ns, pack in packs.items():
        for kind in getattr(pack, "object_kinds", ()) or ():
            statuses = _declared_statuses(pack, kind)
            if not statuses:
                continue
            namespaced = f"{ns}:{kind}"
            statuses_by_kind[namespaced] = tuple(statuses)
            for fold in _declared_folds(pack, kind):
                if not isinstance(fold, dict):
                    continue
                event = fold.get("event")
                status = fold.get("status")
                if not isinstance(event, str) or not isinstance(status, str):
                    continue
                local = event.partition(":")[2] or event
                by_event.setdefault(f"{ns}:{local}", []).append(
                    (namespaced, status))
    index.statuses_by_kind = statuses_by_kind
    index.lifecycle_kinds = frozenset(statuses_by_kind)
    index.by_event = by_event
    return index


def builtin_fold_status(event_kind: str) -> str | None:
    """The compatibility fold's status for an event kind, or ``None``.

    The caller must only apply it to an Object whose ``objectKind`` is NOT a
    declared lifecycle kind (contract 2) — this function answers only "is this
    a built-in fold key", not "may it apply".
    """
    return BUILTIN_CONNECTOR_FOLDS.get(event_kind)
