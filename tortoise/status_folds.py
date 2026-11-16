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
   two packs declaring the same bare kind name do not collide for an Object
   whose ``objectKind`` carries the namespace — but the lookup and the
   ownership guard are additionally **case-folded and form-widened**
   (``venture:tranche`` and bare ``tranche``), because the write gate admits
   both spellings and the extraction commit strips the namespace. A
   **bare**-spelled ``objectKind`` therefore matches BOTH packs when two of
   them declare a lifecycle for one bare kind name, and the last declaring
   pack's fold wins (the stored value carries no namespace, so the
   namespaced dispatch key cannot resolve it). `bare_lifecycle_kind_collisions`
   detects that case at load and ``PackRegistry`` records it on
   ``lifecycle_warnings`` rather than leaving the ambiguity silent.

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

from .pipeline import (
    CORE_WORK_ITEM_PIPELINE as _CORE_WORK_ITEM_PIPELINE,
    CORE_WORK_ITEM_PIPELINE_KEY as _CORE_WORK_ITEM_PIPELINE_KEY,
)

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
    "pm:cardCompleted": "done",
    "github.issue.closed": "done",
}

#: ⛔ #11824 S0 — THESE ARE **STAGES**, NOT STATUSES. The map above used to
#: write ``in_progress`` / ``completed`` as ``Object.status``, which the
#: four-word lifecycle axis can no longer hold (they are POSITION words: design
#: §3.2). Each value is now a stage id of
#: ``pipeline.CORE_WORK_ITEM_PIPELINE``, and the fold advances the stage through
#: ``pipeline.apply_stage_fold`` — so the words keep their meaning, on the axis
#: they belong to, and the reason the old targets had to move is recorded in
#: that declaration's own comment.
#:
#: ⚠️ The two CLOSING folds target a **terminal stage** and never a lifecycle
#: ending, and that is load-bearing rather than tidy: a `deprecated` ending is
#:  final — no verb clears it (the one recoverable exception is an
#: Object/Subject `superseded` whose write clears every carrier) — while a
#: terminal STAGE is cleared by ``advance_stage(..., reopen=True)``. A closed
#: issue has no successor, so its fold target would have been `deprecated` and
#: an old issue closed at import would have been retired with no way back.
#: The reopen path is exactly ``github.issue.reopened`` below.
BUILTIN_CONNECTOR_REOPENS: frozenset[str] = frozenset({"github.issue.reopened"})

#: The declared pipeline the built-in folds advance — the CORE default (a
#: built-in fold fires on an Object of any kind that declares no lifecycle of
#: its own, so there is no pack to declare it for them; design §4.2).
BUILTIN_CONNECTOR_PIPELINE_KEY: str = _CORE_WORK_ITEM_PIPELINE_KEY
BUILTIN_CONNECTOR_PIPELINE: dict = _CORE_WORK_ITEM_PIPELINE

#: Status words the connector/shared vocabulary already spells. A pack may
#: declare them, but the registry must say so.
#: ⚠️ #11824 S0: ``in_progress``/``completed`` are NO LONGER written as a status
#: by the built-in fold — they are stage ids of ``CORE_WORK_ITEM_PIPELINE``. They
#: stay in this set because it answers a DIFFERENT question: "which words does an
#: external connector already use for state, so a pack declaring one is not a
#: surprise?" A migrated kind puts them on the stage axis; this set is about
#: collision detection (#2725), not about what is writable.
SHARED_CONNECTOR_STATUS_WORDS: frozenset[str] = frozenset({
    "open", "closed", "active", "pending", "in_progress", "completed",
})

#: The only keys a ``folds[]`` entry may carry.
#: #11824 S0: ``stage`` joins them — a fold's target moved onto the POSITION
#: axis, because a pack's own words are that kind's pipeline stages (design
#: §4.2) and ``status`` is the closed four-word LIFECYCLE axis
#: (``lifecycle_vocabulary``). A ``status`` target is still accepted so a pack
#: that has not migrated keeps working, and exactly ONE of the two is required.
VALID_FOLD_KEYS: frozenset[str] = frozenset({"event", "status", "stage"})


def _declared_statuses(pack, kind: str) -> list:
    """The kind's declared ``statuses`` list, or ``[]`` when absent/ill-typed.

    Ill-typed values are the validator's business (it rejects the manifest);
    every reader here degrades to "no lifecycle declared" so a half-loaded
    registry can never raise on the projection hot path.

    ⚠️ #11824 S0: a kind that declares ``statuses`` still declares a state
    vocabulary (the legacy/compat axis), but a MIGRATED kind declares its words
    as ``pipeline`` stages instead and has NO ``statuses`` — so "owns its state"
    is :func:`_declares_state`, never this alone. Reading only ``statuses`` made a
    migrated kind fall back to the connector fold, which is the collision
    contract 2 exists to prevent.
    """
    kd = pack.kind_defs.get(kind)
    if not isinstance(kd, dict):
        return []
    value = kd.get("statuses")
    return list(value) if isinstance(value, list) else []


def _declared_pipeline(pack, kind: str) -> dict | None:
    """The kind's declared ``pipeline`` (``kindDefs[<kind>].pipeline``), else None.

    Shape is validated by ``pipeline.validate_pipeline_decl`` (the ONE home of
    the stage model); this reader degrades to ``None`` so a half-loaded registry
    can never raise on the projection hot path.
    """
    kd = pack.kind_defs.get(kind)
    if not isinstance(kd, dict):
        return None
    decl = kd.get("pipeline")
    return decl if isinstance(decl, dict) else None


def _declares_state(pack, kind: str) -> bool:
    """Does this kind declare a state surface of its OWN (either axis)?

    A declared ``statuses`` list OR a declared ``pipeline`` — the same question
    contract 2 asks ("a kind that declares a lifecycle OWNS its state"), and the
    one that keeps the built-in connector fold away from it. #11824 S0 moved a
    pack's words from the first axis to the second without changing the answer.
    """
    return bool(_declared_statuses(pack, kind)) or \
        _declared_pipeline(pack, kind) is not None


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

    if ("statuses" in spec or "folds" in spec or "pipeline" in spec) \
            and declared_object_kinds is not None \
            and kind not in declared_object_kinds:
        errors.append(
            f"kindDefs '{kind}': 'statuses'/'folds'/'pipeline' requires '{kind}' "
            f"in this pack's objectKinds — the fold dispatches on the namespaced "
            f"kind ({namespace}:{kind}), which does not exist for a core or "
            f"non-Object kind"
        )

    statuses_present = "statuses" in spec
    statuses = spec.get("statuses")
    # #11824 S0: a migrated kind declares its words as pipeline STAGES instead of
    # statuses. The stage ids a stage-targeted fold may name come from HERE, so
    # the consistency rule ("a fold's target must be declared where the kind
    # declares it") holds for whichever axis the kind uses.
    pipeline_decl = spec.get("pipeline") if isinstance(
        spec.get("pipeline"), dict) else None
    pipeline_present = pipeline_decl is not None
    declared_stage_ids: list[str] = []
    if pipeline_present:
        for _stage in pipeline_decl.get("stages") or ():
            if isinstance(_stage, dict) and isinstance(_stage.get("id"), str):
                declared_stage_ids.append(_stage["id"])
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
            # The duplicate check runs over the STRING entries only: a
            # payload-controlled non-string entry (``statuses: [{a: 1}]`` on
            # the hosted upload route) is unhashable, and ``set(statuses)``
            # raised ``TypeError`` out of a function whose contract is
            # ``-> (errors, warnings)`` — an internal exception string instead
            # of the kindDef diagnostic above (the type error is already
            # recorded by the loop). Guarding here keeps the validator total.
            str_statuses = [v for v in statuses if isinstance(v, str)]
            if len(set(str_statuses)) != len(str_statuses):
                errors.append(
                    f"kindDefs '{kind}': 'statuses' contains duplicates"
                )
            if len(str_statuses) == len(statuses):
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
    # A second fold naming the same event on the same kind is a declaration
    # conflict the index cannot resolve: `build_status_fold_index` appends
    # both and `_fold_object_status` applies them in declaration order, so the
    # LAST one silently wins. #2729's open decision (b) ("precedence when
    # multiple folds apply to one event") is not settled by this PR, and an
    # orphan/miss lint already exists for the adjacent class, so an ambiguous
    # duplicate is a validation error rather than an undocumented last-wins.
    # Keyed on the RESOLVED local name (case-folded), not the raw declaration
    # string: the index maps `disbursement`, `venture:disbursement` and any
    # case variant to ONE key, so comparing raw strings let the two admitted
    # spellings of one event both index and the second silently win.
    seen_fold_events: dict[str, str] = {}
    if folds_present and not (statuses_present or pipeline_present):
        errors.append(
            f"kindDefs '{kind}': 'folds' requires 'statuses' or 'pipeline' — a "
            f"fold target must be one of the kind's declared status values or "
            f"one of its declared stages"
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
            # ``fold`` is a raw YAML map, so a key can be a non-string
            # (``1: x``). Sorting a mixed key set raises ``TypeError`` and
            # ``', '.join()`` raises on a non-string element — out of a
            # function whose contract is ``-> (errors, warnings)``. Coerce at
            # the message boundary and record the non-string key as its own
            # diagnostic, exactly as the ``statuses`` loop does for a
            # non-string entry.
            unknown = sorted((k for k in set(fold) - VALID_FOLD_KEYS),
                             key=str)
            non_string_keys = [k for k in unknown if not isinstance(k, str)]
            if non_string_keys:
                errors.append(
                    f"kindDefs '{kind}': folds[{i}] has non-string key(s) "
                    f"{', '.join(repr(k) for k in non_string_keys)} — a fold "
                    f"key must be a string"
                )
            if unknown:
                errors.append(
                    f"kindDefs '{kind}': folds[{i}] has unknown key(s) "
                    f"{', '.join(str(k) for k in unknown)} (allowed: "
                    f"{', '.join(sorted(VALID_FOLD_KEYS))})"
                )
            event = fold.get("event")
            if not isinstance(event, str) or not event:
                errors.append(
                    f"kindDefs '{kind}': folds[{i}] 'event' must be a "
                    f"non-empty string"
                )
            else:
                # Resolve to the key the index will actually use (the local
                # name, case-folded) before testing for a duplicate, so the
                # two admitted spellings of one event (`disbursement`,
                # `venture:disbursement`) and its case variants collide here
                # instead of indexing twice and last-winning at runtime.
                key = (event.partition(":")[2] or event).lower()
                first_seen = seen_fold_events.get(key)
                if first_seen is not None:
                    errors.append(
                        f"kindDefs '{kind}': folds[{i}] names event {event!r} "
                        f"more than once — it resolves to the same event as "
                        f"{first_seen!r} (a bare spelling, a self-namespaced "
                        f"spelling and case variants share one index key); "
                        f"the second entry would silently win (declare one "
                        f"status per event)"
                    )
                elif not _fold_event_resolves(event, namespace,
                                              declared_event_kinds):
                    errors.append(
                        f"kindDefs '{kind}': folds[{i}] 'event' {event!r} is not "
                        f"an event kind declared by this pack (declared: "
                        f"{', '.join(str(k) for k in sorted(declared_event_kinds, key=str)) or 'none'}) — "
                        f"an orphan fold key would never fire"
                    )
                seen_fold_events[key] = event
            status = fold.get("status")
            stage = fold.get("stage")
            # #11824 S0: exactly ONE target, on one of the two axes. Both at
            # once has no meaning (which axis moved?) and neither is a fold
            # that folds nothing.
            if "status" in fold and "stage" in fold:
                errors.append(
                    f"kindDefs '{kind}': folds[{i}] names BOTH 'status' and "
                    f"'stage' — a fold targets one axis: 'stage' (the kind's "
                    f"declared pipeline) or 'status' (its declared statuses)"
                )
            if stage is not None:
                if not isinstance(stage, str) or not stage:
                    errors.append(
                        f"kindDefs '{kind}': folds[{i}] 'stage' must be a "
                        f"non-empty string"
                    )
                elif not pipeline_present:
                    errors.append(
                        f"kindDefs '{kind}': folds[{i}] 'stage' {stage!r} needs "
                        f"a declared 'pipeline' on this kind — a stage target "
                        f"must resolve in the declaration it advances"
                    )
                elif stage not in declared_stage_ids:
                    errors.append(
                        f"kindDefs '{kind}': folds[{i}] 'stage' {stage!r} is "
                        f"not in the kind's declared pipeline stages "
                        f"({', '.join(declared_stage_ids)})"
                    )
            elif status is None:
                errors.append(
                    f"kindDefs '{kind}': folds[{i}] must name 'stage' or "
                    f"'status'"
                )
            if isinstance(statuses, list) and all(
                    isinstance(v, str) for v in statuses):
                if status not in statuses:
                    errors.append(
                        f"kindDefs '{kind}': folds[{i}] 'status' {status!r} is "
                        f"not in the kind's declared statuses "
                        f"({', '.join(statuses)})"
                    )
            elif not isinstance(status, str) or not status:
                # A stage-targeted fold has no `status`, and that is legal —
                # only a fold that NAMED `status` must have named a real one.
                if status is not None or stage is None:
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

    Every key here is **case-folded and form-widened**: the write gate admits
    a pack kind as both ``ns:local`` and ``local`` (``extractor_v2``'s
    ``_object_kind_forms``/``_event_kind_forms`` ``.lower()`` every form and
    the projection stores ``objectKind``/``eventKind`` verbatim), and the
    extraction pipeline's own event commit strips the namespace
    (``sdk.py`` ``_extract_session_v2`` writes ``ekind.rsplit(":", 1)[-1]``).
    Keying the index on the namespaced spelling alone made a declared fold
    inert for every event the pipeline emits — the #2729 review P1.
    """

    __slots__ = ("by_event", "kind_forms", "lifecycle_kinds",
                 "statuses_by_kind")

    def __init__(self) -> None:
        #: case-folded event kind (``ns:local`` OR bare ``local``) →
        #: [(namespaced object kind, status), ...]
        self.by_event: dict[str, list[tuple[str, str]]] = {}
        #: namespaced object kinds that declare a `statuses` lifecycle
        self.lifecycle_kinds: frozenset[str] = frozenset()
        #: namespaced object kind → declared status tuple (order preserved)
        self.statuses_by_kind: dict[str, tuple[str, ...]] = {}
        #: every case-folded spelling (``ns:local`` AND bare ``local``) of a
        #: kind that declares a lifecycle — the built-in connector fold's
        #: ``$declared`` guard, so a bare-spelled ``objectKind`` is suppressed
        #: exactly like its namespaced twin.
        self.kind_forms: frozenset[str] = frozenset()

    def folds_for_event(self, event_kind: str) -> list[tuple[str, str]]:
        """The declared ``(kind, status)`` targets for one event kind.

        Case-insensitive and form-widened: a bare ``disbursement`` and a
        namespaced ``venture:disbursement`` both resolve. Empty for a
        built-in-only or unknown event — the caller then applies the
        compatibility fold, which knows nothing about kinds.
        """
        return list(self.by_event.get((event_kind or "").lower(), ()))

    def statuses_for(self, namespaced_kind: str) -> tuple[str, ...]:
        return self.statuses_by_kind.get(namespaced_kind, ())

    def owns_state(self, namespaced_kind: str | None) -> bool:
        return bool(namespaced_kind) and namespaced_kind.lower() in self.kind_forms

    def kind_forms_for(self, namespaced_kind: str) -> list[str]:
        """Every case-folded spelling a target kind may be stored under.

        ``venture:tranche`` → ``["venture:tranche", "tranche"]``; a bare
        declaration is returned as-is. The caller binds these as one
        ``IN $kinds`` parameter, so a bare-spelled Object is folded by the
        same rule as its namespaced twin.
        """
        forms = [namespaced_kind.lower()]
        local = namespaced_kind.partition(":")[2]
        if local and local.lower() not in forms:
            forms.append(local.lower())
        return forms


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
    kind_forms: set[str] = set()
    for ns, pack in packs.items():
        for kind in getattr(pack, "object_kinds", ()) or ():
            statuses = _declared_statuses(pack, kind)
            if not statuses:
                continue
            namespaced = f"{ns}:{kind}"
            statuses_by_kind[namespaced] = tuple(statuses)
            # The built-in fold's `$declared` guard reads these, and the write
            # gate stores the kind verbatim — so BOTH the namespaced and the
            # bare spelling are registered, case-folded, or a bare-spelled
            # Object inherits the connector fold it declared away (the
            # #2729 review P2).
            kind_forms.add(namespaced.lower())
            kind_forms.add(str(kind).lower())
            for fold in _declared_folds(pack, kind):
                if not isinstance(fold, dict):
                    continue
                event = fold.get("event")
                status = fold.get("status")
                if not isinstance(event, str) or not isinstance(status, str):
                    continue
                local = event.partition(":")[2] or event
                # Index under the namespaced AND the bare spellings — the two
                # forms the write gate admits (`extractor_v2._event_kind_forms`)
                # and the form the extraction commit actually writes
                # (`sdk.py::_extract_session_v2` strips the namespace).
                for key in {f"{ns}:{local}".lower(), local.lower()}:
                    by_event.setdefault(key, []).append((namespaced, status))
    index.statuses_by_kind = statuses_by_kind
    index.lifecycle_kinds = frozenset(statuses_by_kind)
    index.kind_forms = frozenset(kind_forms)
    index.by_event = by_event
    return index


def bare_lifecycle_kind_collisions(packs) -> dict[str, list[str]]:
    """Bare object-kind names a lifecycle is declared for by MORE THAN ONE pack.

    The fold index keys the declaring kind on ``ns:kind`` AND on the bare
    ``kind`` (:meth:`StatusFoldIndex.kind_forms_for`), because the write gate
    stores ``objectKind`` verbatim and admits the bare spelling. So for a
    **bare**-spelled Object two packs that declare a lifecycle for the same
    bare kind name both match and the last declaring pack's fold wins — the
    ambiguity the namespaced dispatch key cannot resolve, because the stored
    value carries no namespace.

    Returns ``{bare_kind: [namespace, ...]}`` for every bare kind claimed by
    more than one lifecycle-declaring pack. ``PackRegistry.load_all`` records
    each one on ``lifecycle_warnings`` (a WARNING, not an error: a
    namespaced Object is never ambiguous, the declaration is otherwise valid,
    and isolating a pack for this would take down a legitimate catalog).
    Empty when every bare lifecycle kind has exactly one declaring pack — the
    shipped catalog's shape.
    """
    owners: dict[str, list[str]] = {}
    for ns, pack in packs.items():
        for kind in getattr(pack, "object_kinds", ()) or ():
            if not _declared_statuses(pack, kind):
                continue
            owners.setdefault(str(kind), []).append(ns)
    return {
        bare: sorted(nss)
        for bare, nss in owners.items()
        if len(nss) > 1
    }


def builtin_fold_status(event_kind: str) -> str | None:
    """The compatibility fold's status for an event kind, or ``None``.

    The caller must only apply it to an Object whose ``objectKind`` is NOT a
    declared lifecycle kind (contract 2) — this function answers only "is this
    a built-in fold key", not "may it apply".
    """
    return BUILTIN_CONNECTOR_FOLDS.get(event_kind)
