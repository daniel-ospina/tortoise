"""Pack Registry — catalogs expansion packs from packs/*/manifest.yaml.

Loads all manifests at startup (no code execution). Validates against schema.
Provides queryable registry: "what packs are installed?" without importing connectors.

Design:
  - Manifest-first: parse YAML only. No connector code executed on load.
  - Idempotent: safe to re-run register(). Duplicates are skipped.
  - Independent layers: ontology, connector status, and tools are queryable separately.
  - Namespace enforcement: all kinds are prefixed with pack namespace on registration.
"""
from __future__ import annotations

import dataclasses
import difflib
import logging
import os
from pathlib import Path
from typing import Any, TypeGuard

import yaml

# The direction vocabulary — the ONE home (#11083 reads it to accept a relation
# row's declared per-edge `direction`). `live.py` is the LEAF module (no imports
# of its own), so this costs the extraction path nothing.
#
# ⛔ `canonical_direction` is imported too, and `effective_direction` MUST resolve
# a declared token through it rather than testing membership itself: the write
# path accepts SIX tokens (`ACCEPTED_DIRECTION_VALUES` = the four model values +
# the two legacy aliases), so a reader that tested against `DIRECTION_VALUES`
# alone warned-and-ignored `unidirectional` while `create_operator` honoured it
# as `->` — the census and the writer disagreeing about one row, which is the
# divergence `live.py`'s one-normalizer invariant exists to prevent (#7852).
from .live import ACCEPTED_DIRECTION_VALUES as _ACCEPTED_DIRECTION_VALUES
from .live import canonical_direction, is_default_direction
from .source_credibility import SOURCE_KIND_DEFAULTS
from .status_folds import (
    StatusFoldIndex,
    bare_lifecycle_kind_collisions,
    build_status_fold_index,
    validate_status_decl,
)

log = logging.getLogger(__name__)

# Warn-once sentinel (epic #1891 WF-2, #1930): fallback/isolation warnings on
# the resolution hot path must fire once per key, not per call — keyed by
# (category, value...). Cleared per test via tests/conftest.py so caplog
# assertions are deterministic.
_WARN_ONCE: set[tuple] = set()


def _warn_packs_once(key: tuple, msg: str, *args) -> None:
    """Log a warning at most once per ``key`` (module-level sentinel)."""
    if key in _WARN_ONCE:
        return
    _WARN_ONCE.add(key)
    log.warning(msg, *args)

# ── Canonical kind vocabularies (ONTOLOGY_v2.5 §1.1) ──────────────────────

CANONICAL_OBJECT_KINDS = frozenset({
    # Core work concepts
    "Project", "WorkItem", "Problem",  # Problem: deviation from desired state — problem-family parent
    # Universal
    "tag", "user", "skill", "tool", "agent",
    "workflow", "agreement", "standard", "other",
    # Commitment-state family (ONTOLOGY §5, state-centric 2026-08-12): the STATE
    # objects commitments produce (lifecycle + derived confidence). Lowercase,
    # like the rest of the §5 object vocabulary. #2727 aligned this set with
    # ONTOLOGY §5 and extractor_v2.CORE_OBJECT_KEYS — the three-way diff is now
    # empty (16 kinds each). `tag` was the other silent omission; both were
    # missing while the §5 doc + extractor already carried them, which is why a
    # pack could not declare `subclassOf: target`.
    # `document` was removed from all three by D10 (#5013, ONTOLOGY v3.15, #5022):
    # `objectKind: document` is retired — a document is a `:Source`, not a graph
    # node, so it is not a subclassable object kind. §5 dropped it first; this set
    # and extractor_v2.CORE_OBJECT_KEYS follow the doc (the doc is canonical).
    # NO §5 object kind is excluded from pack-subclassing: every member here is a
    # valid `subclassOf` parent (this set is the parent allowlist). Kinds the
    # ontology does NOT declare (e.g. `aggregate`/`cluster`) are deliberately
    # absent — the canonical set mirrors §5, it does not invent.
    "strategy", "plan", "goal", "target",
})

CANONICAL_EVENT_KINDS = frozenset({
    "meeting", "decision", "experiment", "deployment", "review",
    "friction", "extraction", "documentCreated", "roleCreated", "pointAdded",
    "sessionCaptured",  # #125 metadata-only session capture
    # #2767: a milestone is REACHED at a moment — a core event, not an Object.
    # Promoted from pm's `objectKinds` (and the base vocabulary's event list)
    # so the bare name has ONE canonical identity, and so the miner's
    # `event_kind="milestone"` write is declared.
    "milestone",
    # #2786 (AF-1): these four are ONTOLOGY §5 core event kinds, and the Layer-1
    # gate has always ACCEPTED them (`commit_schema.EVENT_KINDS`), but they were
    # missing here — the pack-facing half of the vocabulary — so a pack could not
    # reference them (`nearMisses`/`equivalentTo`/chain steps) and
    # `PackRegistry._all_known_kinds()` could not resolve them. Measured at
    # `4d9a0ddef`: `EVENT_KINDS - CANONICAL_EVENT_KINDS` = exactly this set.
    # §5 declares all four (`turn` = capture turn records, `occurrence` = generic
    # extracted occurrence, `AgentSession` = the canonical session-capture kind,
    # `humanApproval` = the #531 approval event), so there is no doc-side fix —
    # this set was the stale layer. Guard: tests/test_kind_vocabulary_consistency.py.
    #
    # ⚠ Not only a widening — these four names also become RESERVED, which is worth
    # knowing before adding the next one: (a) a pack that *declares* one of them in
    # its `ontology.eventKinds` is now refused ("already in canonical vocabulary —
    # no need to register", the pre-existing rule every canonical kind already
    # follows), and a manifest with an error is dropped by `load_all()`; (b) `tortoise
    # pack new` refuses them as namespaces (`_cmd_pack_new` lower-cases the canonical
    # set). No in-repo pack declares any of the four, so the blast radius is
    # third-party manifests — #2786 records the compatibility edge.
    "AgentSession", "humanApproval", "occurrence", "turn",
})

CANONICAL_POINT_KINDS = frozenset({
    "statement", "decision", "vision", "strategy", "plan", "goal",
    "target", "observation", "hypothesis",
})

CANONICAL_DOCUMENT_KINDS = frozenset({
    "research", "reflectPostmortem", "strategyDoc", "visionDoc",
    "planDoc", "decisionDoc", "meetingNotes", "experimentResults",
    "evidenceLog", "handoff", "transcript", "roadmap", "brief",
})

# DEPRECATED (#122): CANONICAL_ACTION_KINDS is no longer canonical.
# Action kinds are still valid in pack manifests but the centralized 17-value
# frozenset is unnecessary — packs declare their own actionKinds.
# Kept for backward compatibility; will be removed in a future version.
#
# ⚠️ "Packs declare their own" means pack-OWNED names (`myAction`), NOT these
# 17: the set is still part of the resolution universe — `CORE_KINDS` unions it
# and `_build_kind_expansions` roots it — so every name here is core-owned and a
# manifest declaring one (on this axis or any other) is refused by the #2786
# guard, which DROPS the pack. Retirement of the *constant* must keep the names
# in `KIND_OWNER_AXES` until nothing resolves them any more.
CANONICAL_ACTION_KINDS = frozenset({
    "research", "scope", "plan", "implement", "verify", "reflect",
    "decompose", "delegate", "loop", "brainstorm", "decide", "agree",
    "meet", "experiment", "deploy", "review", "other",
})

# The five manifest kind axes, in the order the validator walks them. ONE list:
# the declaration loop, the `all_pack_kinds` union and the two-category leg all
# read it, so an axis cannot silently lose a rule (a second hardcoded copy is
# what let `actionKinds` fall out of the guard during review).
KIND_FIELDS = ("objectKinds", "eventKinds", "pointKinds", "documentKinds",
               "actionKinds")

# Pack-DECLARATION entity vocabulary — NOT the graph-label vocabulary. This is the
# type a pack's edge (`from`/`to`) and a bare kind reference may name, and it is
# deliberately kept distinct from the labels a query leg serves:
#   * ``Document`` stays here ON PURPOSE (D10, ONTOLOGY v3.15 §4.4). A pack may
#     declare an edge from/to a document; the WRITE path routes that to a
#     ``:Source`` keyed ``url`` (``sdk._create_entity``), and the READ path maps
#     ``document`` → ``:Source`` (``security.ENTITY_TYPE_LABELS``). Removing the
#     spelling here would break pack declarations over a label that no longer
#     exists, so it is a declaration alias, not a live graph label.
#   * ``Action`` likewise names a declared kind, not a node label.
VALID_ENTITY_TYPES = frozenset({
    "Subject", "Object", "Action", "Point", "Event", "Document", "Source",
})

VALID_CARDINALITIES = frozenset({
    "one_to_one", "one_to_many", "many_to_one", "many_to_many",
})

VALID_PARAM_TYPES = frozenset({
    "string", "integer", "number", "boolean", "array", "object",
})

CANONICAL_KINDS = {
    "objectKinds": CANONICAL_OBJECT_KINDS,
    "eventKinds": CANONICAL_EVENT_KINDS,
    "pointKinds": CANONICAL_POINT_KINDS,
    "documentKinds": CANONICAL_DOCUMENT_KINDS,
    # "actionKinds": CANONICAL_ACTION_KINDS,  # DEPRECATED (#122)
}

# ── Manifest v3 vocabulary (research-r6 §3, epic #909) ────────────────────

# Core entity types (ONTOLOGY §1) — the five conceptual nodes plus Object/
# Action. Valid subclassOf parents AND expansion roots (R6 §6.2a: named
# Document/Source/Subject/Point/Event; Object/Action included so every parent
# the validator accepts also expands).
# Alias of VALID_ENTITY_TYPES (review P2, PR #978) — one source of truth.
CORE_ENTITY_TYPES = VALID_ENTITY_TYPES

# Everything a bare kind reference may resolve to without a namespace.
CORE_KINDS = (
    CANONICAL_OBJECT_KINDS | CANONICAL_EVENT_KINDS | CANONICAL_POINT_KINDS
    | CANONICAL_DOCUMENT_KINDS | CANONICAL_ACTION_KINDS | CORE_ENTITY_TYPES
)

# ── ONTOLOGY §6 — the CORE subclass table (core parentage) ─────────────────
# Which core kinds are subclasses of a core entity type. `get_subclasses` used
# to read ONLY the pack-declared `kind_subclasses`, so this declared parentage
# never reached the expansion table at all (#11000, Leg B). Measured at
# `86aaef8f0271`: `expand_kind("Object") == ["Object"]` and
# `expand_kind("Subject") == ["Subject"]` while §6 declares 16 Object
# subclasses and 5 Subject subclasses, and `get_subclasses("Subject") == []`.
#
# ⛔ These rows are NOT a second vocabulary — each is the canonical set that
# already exists, so a kind is added in ONE place and both readers follow:
#   * ``Object``  ← ``CANONICAL_OBJECT_KINDS``, the §5 object block (pinned
#     three ways against the doc and the extractor by
#     ``tests/test_pack_kinds.py::TestCanonicalObjectKindAlignment``);
#   * ``Subject`` ← ``kind_layers.subject_kind_names()`` — the #9964 ONE
#     authority for the subject vocabulary. It is spelled here rather than
#     imported because that authority pulls ``extractor_v2`` onto the pack-load
#     path, and it is pinned back to it by
#     ``tests/test_pack_registry_gaps.py::TestCoreHierarchy``.
#     ⚠️ THREE-WAY, not a simple doc copy — the doc is not internally
#     consistent here: §5's Subject block (`docs/ONTOLOGY.md` §5) and §6's
#     PROSE both list `other`; only §6's TABLE row omits it. The row follows
#     §6's table + the #9964 authority, which agree, and the missing pin is
#     now exact: §5-subject minus this row == `{"other"}`, asserted by
#     ``test_the_subject_row_matches_s5_except_the_nil_bucket``.
#     `other` is the NIL bucket, so `expand_kind("Subject")` deliberately does
#     NOT match `subjectKind='other'` — the fold's ON-CREATE default.
#   * ``Point``/``Event`` ← ``CANONICAL_POINT_KINDS``/``CANONICAL_EVENT_KINDS``
#     (§5's point and event vocabularies). ⚠️ §6's table has NO row for either,
#     so these two rows are an EXTENSION of §6 that this module makes, not a
#     transcription of it — recorded on #11000 and in the epic plan
#     (`docs/epics/2026-11-05-5087-extractor-v4/05-plan.md` §6), and proposed to
#     the doc's owner rather than edited in (`docs/ONTOLOGY.md` is
#     owner-gated). The relation is the same one §6 declares for Object; the
#     pin asserts §5 equality, NOT a §6 row, so the omission is visible rather
#     than inferred.
# ``Document`` is deliberately ABSENT: §6 states a document is **not** an
# Object subclass (it is a `:Source`, §4.4), so `expand_kind("Document")` keeps
# only its PACK subclasses. ``Source``/``Action`` have no declared core
# hierarchy either — `sourceKind` is its own axis, and `CANONICAL_ACTION_KINDS`
# is retired (#122).
#
# ORDER is sorted, not the doc's table order: the object/point/event sets are
# ``frozenset``s, and an unsorted ``tuple(frozenset)`` iterates in an order that
# varies with PYTHONHASHSEED — which would make `expand_kind`'s list order
# unstable across processes.
CORE_SUBCLASSES: dict[str, tuple[str, ...]] = {
    "Object": tuple(sorted(CANONICAL_OBJECT_KINDS)),
    "Subject": (
        # §6's table row == kind_layers.subject_kind_names() (#9964). `other`
        # is excluded by BOTH (it is the NIL bucket); §5's block and the
        # "Core subclass model (§6)" paragraph — which lives in §1, not §6 —
        # both list it, while §6's TABLE row omits it. That is the divergence,
        # and it is pinned exactly rather than described — see
        # test_the_subject_row_matches_s5_except_the_nil_bucket.
        "legalPerson", "naturalPerson", "organization", "role", "team",
    ),
    "Point": tuple(sorted(CANONICAL_POINT_KINDS)),
    "Event": tuple(sorted(CANONICAL_EVENT_KINDS)),
}

#: Every core kind (plus the §5 Subject kinds ``CORE_KINDS`` omits), so the bare
#: and `core:`-prefixed spellings of a kind form ONE kind and a read in either
#: reaches rows stored under the other (#11000).
#:
#: ⚠️ Membership is deliberately the whole core vocabulary, NOT "the kinds this
#: tree happens to spell with a `core:` prefix": a majority of the members have
#: no `core:` spelling anywhere in the tree today. The prefix is LIVE for a subset
#: — the classifier's candidate index (`extractor_v2.CORE_OBJECT_KEYS` /
#: ``SUBJECTS`` / ``EVENTS``) and the open write doors
#: (`create_object(objectKind="core:organization")`) — and inert for the rest,
#: where the alias names a spelling that matches no stored row (which is harmless
#: and hash-seed stable). Sizing the set to the live subset was considered and
#: rejected: it would make the alias's coverage depend on a grep of today's call
#: sites, so a later door that starts emitting `core:` would silently lose its
#: rows — the exact defect (#11000) this constant exists to prevent.
#:
#: The Subject vocabulary is only in here through ``CORE_SUBCLASSES`` —
#: `organization`/`team`/`role`/`legalPerson`/`naturalPerson` are §5 Subject kinds
#: and are NOT in ``CORE_KINDS`` (which is the pack-facing reference universe plus
#: the entity types). They are exactly the kinds `extractor_v2.SUBJECTS` spells
#: `core:organization`, so leaving them out made `expand_kind("Subject")` unable
#: to reach a `core:team` row — the spelling `create_subject` actually stores.
CORE_ALIASED_KINDS: frozenset[str] = frozenset(CORE_KINDS).union(
    *(frozenset(children) for children in CORE_SUBCLASSES.values())
)

# Kinds that persist in STORED graphs but are no longer DECLARED by any pack, so
# they cannot ride `equivalentTo` (whose targets must resolve to a declared
# kind). Read paths still expand through them, so a query keeps finding
# pre-migration data — `migrate_kinds` is an opt-in normaliser, never a
# precondition for a read (#6146).
# #7848 moved two kind declarations:
#   * `requirement` — dev pointKind → product-strategy objectKind, so the old
#     `dev:requirement` spelling lives on stored Points;
#   * `deployment`  — dev objectKind → core EVENT kind, so `dev:deployment`
#     lives on stored Objects (and the relation names `core:deployment`).
# A legacy spelling is a READ-ONLY alias: it is deliberately NOT in
# `known_kinds`, so writing it still warns.
LEGACY_KIND_ALIASES: dict[str, tuple[str, ...]] = {
    "requirement": ("dev:requirement",),
    "product-strategy:requirement": ("dev:requirement",),
    "deployment": ("dev:deployment",),
}

# kindDefs value keys (R6 §3.1/§3.5)
# `statuses`/`folds` are the #2729 lifecycle declaration: a pack declares a
# kind's status vocabulary and the event kinds that drive it (see
# `tortoise/status_folds.py` — the ONE home of the fold contract).
VALID_KINDDEF_KEYS = frozenset({
    "description", "synonyms", "examples", "nearMisses",
    "extractable", "storeAs", "enforcement",
    "statuses", "folds",
    # #2792: a stage-driven kind may declare its default stage lifecycle. The
    # canonical slot vocabulary and the shape rules live in
    # ``tortoise/pipeline.py`` (one home); this key only opts the kind in.
    "pipeline",
    # #2818: the per-kind VALUE FIELDS a kind carries — the declaration seam
    # #2782's value model needs (`fundingTranche` carries
    # `committedAmountMinor` as a `money` value). The vocabulary and the shape
    # rules live in ``tortoise/pack_values.py`` (one home); this key only opts
    # the kind in. It stays a CLOSED declaration — an unknown key inside an
    # entry, an unknown `valueKind`, or a namespaced name is a hard error.
    "values",
})

# storeAs values — what stream bucket a kind belongs to (R6 §3.1)
VALID_STORE_AS = frozenset({"claim", "decision", "entity", "tag", "event"})

# Enforcement ladder (R6 §5.3): warn | retry | block
VALID_ENFORCEMENT_LEVELS = frozenset({"warn", "retry", "block"})

# pointKinds whose inferred storeAs is `decision` (R6 §3.1)
DECISION_POINT_KINDS = frozenset({
    "decision", "vision", "strategy", "plan", "goal", "target", "humanApproval",
})

# sourceKind vocabulary (ONTOLOGY §4.6) — the §5 core types + connector kinds.
# NOT the full pack-validation set: extraction.sourceTypes validates against
# registered_source_types() (this ∪ the registered source-kind registry ∪ the
# escape hatch). #2726 superseded the R6 §6.1#3 "v1 restrict to conversation/
# document" resolution — a kind registered via register_source_kind_default is a
# first-class sourceKind and validates for packs too (the checks had drifted).
KNOWN_SOURCE_TYPES = frozenset({
    "conversation", "document",
    "github_issue", "slack_message", "linear_card",
})

# Escape hatch for future connectors (R6 §3.5): packs may reference these
# without a registry release. Anything outside registered_source_types() ∪
# SOURCE_TYPE_ESCAPE_HATCH is a validation error (typo protection).
# #2726 open decision (c) — "escape-hatch entries to remove": reviewed, none.
# All four values are absent from KNOWN_SOURCE_TYPES and SOURCE_KIND_DEFAULTS,
# so every entry is still a genuine future-connector placeholder; keep all four.
SOURCE_TYPE_ESCAPE_HATCH = frozenset({
    "email", "webpage", "discord_message", "notion_page",
})

# Source kinds a pack may declare in extraction.sourceTypes. Computed LIVE from
# the canonical registry (never snapshotted at import) so a kind registered
# after import — e.g. a connector calling register_source_kind_default() at load
# time — is valid immediately, with no import-order dependence. The union is:
#   KNOWN_SOURCE_TYPES        — §5 core types + connector kinds
#   SOURCE_KIND_DEFAULTS      — every registered source kind (document,
#                               github_pr/linear_cycle #388, agentSession,
#                               meeting_summary, and
#                               meeting_transcript/meeting_minutes #2726)
# SOURCE_TYPE_ESCAPE_HATCH is unioned at the call site, not here.
# commit_schema.compile_vocab() uses THIS function for its core source-kind leg
# (#2742), so the pack check and the Layer-1 gate read one live definition. There
# is deliberately no `CORE_SOURCE_KINDS` snapshot: an import-time frozenset over
# the mutable registry was accepted by this check immediately but stayed rejected
# by the Layer-1 gate until a process restart — the two validators diverged for
# every runtime `register_source_kind_default` call.
# Before #2726 the check used KNOWN_SOURCE_TYPES alone, so a registered kind was
# valid in create_source/commit_schema but rejected in pack manifests — the
# drift class this closes. Tier-form registry keys (T0-T4) are accepted too: a
# source's sourceKind may legitimately BE a tier form (dual-write #398), so
# `sourceTypes: [T2]` is a meaningful activation predicate, not a typo.


def registered_source_types() -> frozenset[str]:
    """Every source kind currently valid in a pack's ``extraction.sourceTypes``.

    Live view (the registry is mutable via ``register_source_kind_default``),
    so callers must not cache the result across a registration. Does NOT include
    the escape hatch — the validation site unions that separately.
    """
    return KNOWN_SOURCE_TYPES | frozenset(SOURCE_KIND_DEFAULTS)


# The OWNER map — every KIND name core already owns, with the axis that owns it.
# This is NOT ``CANONICAL_KINDS`` alone: that dict deliberately has no
# ``actionKinds`` entry (#122 retired the axis's *canonical* status) while
# ``CORE_KINDS`` and ``_build_kind_expansions`` still treat the action names as
# core — bare-reference resolution and expansion roots. The owner map must cover
# every kind name the resolution universe owns: otherwise
# ``objectKinds: [implement]`` validates clean while ``expand_kind("implement")``
# merges core's action kind with the pack's object kind, which is the #2786
# divergence class one axis over (and a fail-open guard).
#
# ``CORE_ENTITY_TYPES`` (Object/Point/Event/Document/…) is deliberately ABSENT:
# an entity type is PascalCase, so the camelCase shape rule refuses a manifest
# declaring it before the owner check ever runs (and `pack new event` is NOT
# reserved — a namespace is a lowercase word, a different question).
KIND_OWNER_AXES: dict[str, frozenset[str]] = {
    **CANONICAL_KINDS,
    "actionKinds": CANONICAL_ACTION_KINDS,
}


def canonical_kind_axes(kind: str) -> tuple[str, ...]:
    """Every category that ALREADY owns ``kind`` — empty when the name is free.

    ONE definition of "does core own this name", read by the pack-manifest
    collision check and by the CLI's namespace reservation
    (:func:`canonical_kind_words`). A name can be owned twice (`decision` is both
    a ``pointKinds`` and an ``eventKinds`` kind in ONTOLOGY §5), so this returns
    ALL owners rather than the first: naming one axis would send a pack author
    to declare a kind that core already owns elsewhere.

    The source axis is read LIVE through :func:`registered_source_types` (a
    connector may call ``register_source_kind_default`` at load time) and is
    reported as ``sourceKinds``. No caching on purpose — #2742 is the defect an
    import-time snapshot of that mutable registry caused. The canonical axes
    themselves are immutable frozensets, so reading them directly is not a
    snapshot of anything mutable.
    """
    axes = tuple(axis for axis, kinds in KIND_OWNER_AXES.items() if kind in kinds)
    if kind in registered_source_types():
        axes += ("sourceKinds",)
    return axes


def canonical_kind_words() -> frozenset[str]:
    """The casefolded view of :func:`canonical_kind_axes`' universe.

    For callers that compare a BARE WORD rather than a kind declaration — the
    CLI's namespace reservation, which must not collide with any word core owns
    on any axis. Derived from the same map so the two cannot drift (#2786); the
    live source registry is folded in here because a namespace must not shadow a
    connector's source kind either.
    """
    return frozenset([k.lower() for kinds in KIND_OWNER_AXES.values() for k in kinds]
                     + [k.lower() for k in registered_source_types()])


# Core mechanism predicates — valid chain-edge / enforcement targets without a
# pack relation. IMPL/NAND ONLY. `MITIGATES` is not a predicate: the F1 ruling
# (#2552, ontology v3.17, implemented by #4937 / PR #5225) retired it from the
# OPERATOR MENU, and a mitigation is a Point attached to the operator bridge it
# damps — `(op:Point {is_operator:true})-[:mitigated_by]->(m)`, ontology §3.9.
# `MITIGATES` does survive as a PAYLOAD op_type some writers still emit (see
# `commit_schema`), routed to `sdk.mitigate_operator`; that spelling is not this
# set, and the distinction matters. Admitting it HERE let a pack declare
# `chains[].edges: [MITIGATES]` and validate clean WITHOUT declaring a relation —
# advertising a chain edge the engine cannot build.
# (#4626 / #2766 / #5322)
CORE_PREDICATES = frozenset({"IMPL", "NAND"})

# ── Layer 2 — the semantics vocabulary (ONTOLOGY §8, #7852) ──────────────
# A relation has three layers: `mechanism` (how it acts — IMPL/NAND) +
# `direction` (which way it runs), `semantics` (what it MEANS), and the
# `predicate` (the specific verb). The semantics vocabulary is **CLOSED** —
# core is canonical and a pack aligns to it, so a value outside this set is a
# typo, never a new meaning, and validation names the valid alternatives
# (warn-not-block, per W2 — see `_validate`).
#
# A value is chosen by the CO-VARIANCE TEST: does the relationship make the
# two ends' confidence move together? `SEMANTICS_DIRECTIONS` records the one
# the value carries; the two sets are pinned equal, so a value cannot be added
# without stating the direction it records.
SEMANTICS_VALUES = frozenset({
    # epistemic — confidence moves along the edge
    "addresses", "supports", "refutes", "causes",
    "mutuallySupportive", "mutuallyExclusive",
    # structural / neutral
    "hasPart", "dependsOn", "transacts", "related",
    # core structural rows (ONTOLOGY §3.6) a pack may name as a meaning
    "ownedBy", "managedBy",
    # #11083 — `produces` returns as a MEANING (not a label): production is not
    # causation. Owner ruling 2026-11-05 on epic #5087, relayed to this issue in
    # comment 6667121779, and recorded as an `OVERRIDES:` on #7848
    # (comment 6668793463). W3C PROV defines generation as production and states
    # explicitly that it does NOT assert a general causal relation, so folding
    # `produces` onto `causes` changed the meaning — and `causes` is `->` while
    # the shipped declaration (`venture:producedBy`, asset → program) runs the
    # other way, so the mapping inverted the direction too.
    #
    # ⛔ `funds` does NOT come back with it. The 2026-11-05 note promoted both;
    # #7848's correction (2026-11-08) WITHDREW `funds` on #2766's three-layer
    # test — nothing in the machine changes behaviour because a relation is a
    # funding relation, so it is a layer-3 LABEL riding on an existing meaning
    # (`funds` stays a key of `SEMANTICS_TRANSITION` below). Do not add it here.
    "produces",
})

# The direction each ruled semantics value records (ONTOLOGY §8, #7852):
# `->` source→target, `<-` target→source, `<->` both ways, `-` none (the
# connection exists and NO confidence transfers — direction and
# confidence-transfer are one axis). Every value in `SEMANTICS_VALUES` appears
# here — the two sets are pinned equal by a test, so a value cannot be added
# without stating the direction it records.
SEMANTICS_DIRECTIONS: dict[str, str] = {
    "addresses": "->",
    "supports": "->",
    "refutes": "->",
    "causes": "->",
    "dependsOn": "->",
    "ownedBy": "->",
    "managedBy": "->",
    "mutuallySupportive": "<->",
    "mutuallyExclusive": "<->",
    "hasPart": "<->",
    "transacts": "-",
    "related": "-",
    # #11083 — the FIRST table value that produces `<-` (target→source). The
    # declared edge is `asset → program` and production runs program → asset, so
    # `->` would assert the asset caused the program. `<-` was already a valid
    # PER-EDGE token (`live.DIRECTION_VALUES`), so the write path needed no
    # change — only this row. Pinned by `tests/test_pack_logic_census_11083.py`.
    "produces": "<-",
}

# ── Layer 1 — the MECHANISM each meaning declares (#9963) ──────────────
# `mechanism` is the model's "how does it act" leg: IMPL raises the target's
# confidence, NAND lowers it. ONTOLOGY §8's semantics table states it per
# meaning (#7852's canonical table), and it is RULED, not inferred: `addresses`
# and `causes` are IMPL, `hasPart` is IMPL, `refutes` and `mutuallyExclusive`
# are NAND. Over the values that declare one, the OPPOSITE spelling is a logic
# INVERSION: an IMPL wire labelled `refutes` RAISES the target exactly where the
# meaning says it must lower it (#9963 measured 0.6693602 vs 0.1314086 on one
# claim pair — identical declared meaning, only the mechanism differing — and
# the node recorded `direction_basis: 'semantics'`, i.e. the graph claimed the
# declared meaning WAS honoured).
#
# The four values ONTOLOGY §8 gives NO mechanism (`—`) are deliberately ABSENT:
# `related`, `transacts`, `ownedBy`, `managedBy` are structural/neutral — they
# say what the connection IS, not which way belief moves — so either mechanism
# may be spelled over them and reporting one would invent a ruling the model
# never made. (The layer-3 predicate is where `ownedBy`/`managedBy` carry their
# §3.6 shape; the Layer-2 meaning does not fix a belief polarity.)
#
# ⛔ Read to COMPARE, never to gate (#1934 governance D1/D2 — a recorded
# decision). A mismatched write LANDS with the caller's `op_type` and returns a
# structured warning; converting it into a refusal is a reopen for the owner,
# not a lane change (#9963 states the contract explicitly).
#
# Pinned by test to exactly ONTOLOGY §8's table (the mechanism column) and to a
# subset of `SEMANTICS_VALUES` / `CORE_PREDICATES`, so a vocabulary move cannot
# leave this map behind.
SEMANTICS_MECHANISMS: dict[str, str] = {
    "addresses": "IMPL",
    "causes": "IMPL",
    "dependsOn": "IMPL",
    "hasPart": "IMPL",
    "supports": "IMPL",
    "mutuallySupportive": "IMPL",
    "refutes": "NAND",
    "mutuallyExclusive": "NAND",
    # #11083 — a production RAISES the target's confidence (the product exists
    # because the producer made it), so the mechanism is IMPL. The map stays
    # COMPARED, never gated (#1934's governance ruling, untouched here).
    "produces": "IMPL",
}

# Values shipped packs still declare, ruled to collapse onto the core
# vocabulary. Recognised (never an error, so a pack is not dropped
# mid-transition) and warned naming the canonical word. The pack alignment
# is #7848; no NEW declaration should use a key of this map.
SEMANTICS_TRANSITION: dict[str, str] = {
    "opposes": "mutuallyExclusive",
    "holds": "ownedBy",
    # ⛔ `funds` STAYS — it is a layer-3 LABEL, not a meaning (#7848 correction,
    # 2026-11-08, on #2766's "does the machine have to ACT on it?" test). The
    # 2026-11-05 note that promoted it into `SEMANTICS_VALUES` was withdrawn;
    # the durable `funds` relation rides on an existing meaning with `funds` as
    # its label. `produces` left this map — see `SEMANTICS_VALUES`.
    "funds": "transacts",
}

# ── #7551 — the operator-LABEL declared vocabulary ───────────────────────
# `create_operator`'s declaredness check answers ONE question: is this label
# **declared** — not "is it shaped like a verb", and (the defect) not "does an
# installed pack happen to declare it". It consulted pack relations ONLY, so
# EVERY core predicate read as undeclared and the warning carried no
# information: `supports` — the convention ONTOLOGY §8 gives evidence→claim —
# warned exactly like an invented name, and a lane with no reachable declared
# predicate invented one (`measures`; #7551, ≈25 such edges).
#
# The set is DERIVED, never re-transcribed, so it cannot drift from its two
# sources: the mechanism pair (§3.9 "Epistemic edges (operators): IMPL, NAND")
# UNIONED with the CLOSED Layer-2 semantics vocabulary (§8) — the "(+ semantic
# label)" §3.9 admits. Widening the accepted vocabulary is therefore a change
# to `SEMANTICS_VALUES` or `CORE_PREDICATES` (and to §8's table, which the
# guard pins) — not a quiet addition here.
#
# A RETIRED spelling (`opposes` / `holds` / `funds`, see `SEMANTICS_TRANSITION`)
# is deliberately ABSENT: it must keep warning so the caller learns the canonical
# word.
#
# ⛔ NOT `produces`. `produces` LEFT `SEMANTICS_TRANSITION` when it became a
# Layer-2 meaning (#11083; `SEMANTICS_VALUES` above carries the reasoning), so it
# is in this derived union via `SEMANTICS_VALUES` and is DECLARED — it does not
# warn. A comment that still listed it as retired survived the change and told
# the next reader the opposite of what the code does.
#
# Declaredness is not shape. §8's reification table governs which edges ought
# to carry an operator; a `label` outside that shape is odd but still
# *declared*, and this check was never a shape gate (warn-not-block, #1934).
CORE_OPERATOR_LABELS = CORE_PREDICATES | SEMANTICS_VALUES


def declared_relation_predicates() -> frozenset[str] | None:
    """The declared relation-predicate vocabulary, in ONE home.

    ``CORE_OPERATOR_LABELS`` (§3.9's mechanism pair plus §8's closed semantics
    vocabulary) UNIONED with every predicate a loaded pack declares (#7551).
    Both the write path (``sdk.create_operator``'s warn-not-block declaredness
    check) and the extraction path (``extractor_v2._assemble_relations``' S5
    gate) answer from this call rather than each deriving the set — that second
    derivation is the defect #2766 records: *"the declared registry, the
    writer, the validator and the discoverable API each derived the relation
    vocabulary independently, and an edge no surface constrained was the only
    one that got through."*

    ``None`` means the union could not be read (registry unreadable). A caller
    must fail OPEN on it — keep its item and warn — because the destructive
    direction is dropping data this module could not judge. Never raises.

    Lazy import: ``domain_loader`` imports this module, so a module-level
    import would be a cycle.
    """
    try:
        from .domain_loader import _get_registry

        declared: set[str] = set(CORE_OPERATOR_LABELS)
        reg = _get_registry()
        if reg is not None:
            declared |= {r.get("predicate") for r in reg.list_relations()
                         if isinstance(r, dict) and r.get("predicate")}
        return frozenset(declared)
    except Exception:  # noqa: BLE001, RUF100 — never raise into a caller's gate
        return None

# ── Layer 3 — the relation write-path verdict (#2766) ─────────────────────
# A pack `ontology.relations[]` entry is only *real* if some runtime surface
# accepts its predicate. This is the ONE place that decides which declared
# paths apply — so pack load, `tortoise pack validate` and
# `sdk.list_relations()` cannot disagree about it. That disagreement is the
# defect #2766 reports: the declared registry, the writer, the validator and
# the discoverable API each derived the relation vocabulary independently, and
# an edge no surface constrained was the only one that got through.
#
# The verdict is about the DECLARATION, and the paths are the surfaces a
# relation's OWN declaration makes usable:
#
#   operator   — ``sdk.create_operator(op_type=<mechanism>, label=<predicate>)``
#                (the path ``sdk.ingest`` uses for ``connections[].label``).
#                Requires a declared ``mechanism`` (IMPL|NAND): the mechanism
#                is what tells a caller WHICH op_type the verb means, so a
#                relation without one gives the operator path no declared
#                op_type. The predicate itself is a free Layer-3 label
#                (ONTOLOGY §8, #7852) and is deliberately NOT checked against a
#                vocabulary — any domain verb can ride a declared mechanism.
#   structural — ``sdk.create_edge(predicate, from_id, to_id)`` and the other
#                structural writers (``create_about_edge``, ``sdk.ingest``):
#                the unions of their predicate sets. Read from its single home
#                in the writer (``projection/edges.py::
#                STRUCTURAL_WRITE_PREDICATES``) so a predicate added or
#                removed THERE moves this verdict with it. It is not one of the
#                three subsets — reading only ``_VALID_EDGE_PREDICATES``
#                under-reported ``aboutPoint`` (create_about_edge) and
#                ``extractedFrom`` (ingest) as unwritable (#8285 review P1).
#
# ⛔ DO NOT READ THE VERDICT AS A RUNTIME REFUSAL — it is not one. The two
# writers are NOT symmetric:
#   * ``create_edge`` is STRICT: an unlisted predicate is refused ("Unknown
#     predicate").
#   * ``create_operator`` is WARN-NOT-BLOCK (#1934, and the owner's
#     2026-10-02 measurement on #2766): it accepts ANY label and warns only
#     when the label is not a declared predicate. So a relation reported
#     ``predicate-not-in-allowlist`` is still WRITABLE by a caller who passes
#     an explicit ``op_type`` — what is missing is a DECLARED mechanism (i.e.
#     what a caller would pass as ``op_type``), not the writer's acceptance.
#     The warning text and docs/EXPANSION_PACKS.md must say exactly that, and
#     nothing on this surface may claim the runtime drops the edge.
#
# A relation with neither declared path is no longer silently ambiguous: it is
# ``declaration-only`` when the manifest says so (``declarationOnly: true`` —
# an explicit author opt-in), and ``predicate-not-in-allowlist`` otherwise —
# the state this verdict exists to make loud. Warn-not-block (W2), the same
# contract the Layer-2 semantics check uses: a pack mid-alignment keeps
# loading, and `tortoise pack validate` names every relation whose declaration
# binds no mechanism.
RELATION_WRITABLE = "writable"
RELATION_DECLARATION_ONLY = "declaration-only"
RELATION_PREDICATE_NOT_IN_ALLOWLIST = "predicate-not-in-allowlist"
RELATION_WRITE_VERDICTS = (
    RELATION_WRITABLE,
    RELATION_DECLARATION_ONLY,
    RELATION_PREDICATE_NOT_IN_ALLOWLIST,
)


def relation_write_paths(rel: dict) -> tuple[str, ...]:
    """Name the DECLARED write paths a relation's declaration makes usable.

    The paths are the two surfaces a caller can reach with the relation AS
    DECLARED (see the block above): ``operator`` is gated on the declared
    ``mechanism`` — the declaration's own op_type — and ``structural`` reads
    the writer's own structural vocabulary (``STRUCTURAL_WRITE_PREDICATES``,
    the union of every writers' set). Empty means the declaration binds no
    path.

    ⛔ Empty is NOT "the runtime refuses it": ``create_operator`` accepts any
    label (warn-not-block, #1934), and what is missing is a declared mechanism
    — i.e. what a caller would pass as ``op_type``. See the block above and
    the load warning.
    """
    # Lazy: the pack registry is imported by the extraction path and must not
    # drag the whole projection package in behind it (measured ~60 ms). The
    # write vocabulary stays defined in ONE place — the writer — and is read
    # here. It is the UNION of the writers' structural sets (#8285 review P1):
    # `_VALID_EDGE_PREDICATES` alone excludes `aboutPoint` (create_about_edge)
    # and `extractedFrom` (ingest), both of which the runtime demonstrably
    # writes.
    from .projection.edges import STRUCTURAL_WRITE_PREDICATES

    predicate = rel.get("predicate") or ""
    paths: list[str] = []
    if rel.get("mechanism") in CORE_PREDICATES:
        paths.append("operator")
    if predicate in STRUCTURAL_WRITE_PREDICATES:
        paths.append("structural")
    return tuple(paths)


def relation_write_verdict(rel: dict) -> tuple[str, tuple[str, ...]]:
    """``(verdict, paths)`` for one declared relation — the single source of truth.

    Called by pack load (warn), ``tortoise pack validate`` (report) and
    ``list_relations()`` (the discoverable API), so all three answer from the
    same computation instead of each deriving its own contract (#2766).
    """
    paths = relation_write_paths(rel)
    if paths:
        return RELATION_WRITABLE, paths
    if rel.get("declarationOnly") is True:
        return RELATION_DECLARATION_ONLY, ()
    return RELATION_PREDICATE_NOT_IN_ALLOWLIST, ()


def effective_direction(rel: dict) -> str | None:
    """The direction a declared relation's row ACTUALLY records — per row (#11083).

    Two inputs, and the order between them is the whole point:

    1. the row's own ``direction``, resolved through
       :func:`tortoise.live.canonical_direction` — the ONE normalizer the write
       path also uses, so the six tokens a write accepts (the four model values
       plus the legacy ``unidirectional``/``bidirectional`` aliases) are ALL
       honoured here and normalized to the model's four. This is ruling (c)'s
       carrier: ``ownedBy``'s target is deliberately open (Subject **or**
       Object), so the predicate alone can no longer say whether
       ``investment --ownedBy--> fund`` means *the fund owns the investment*
       (`->`) or *the investment is a stake in it* (`<-`).

       ⛔ A membership test against ``live.DIRECTION_VALUES`` here would be a
       RE-SPELLING of the vocabulary at a call site — which ``live.py`` forbids
       — and it made the census and the writer disagree: a row declaring
       ``direction: unidirectional`` is warned-and-ignored here (so the meaning's
       ``<-`` is reported) while ``create_operator`` honours it as ``->``
       (``is_default_direction("unidirectional") is False`` → basis ``explicit``).
       One wrong sign, which is exactly the hazard the load-time warn exists for.

       ⛔ The declared token is honoured only when it DIFFERENTIATES, because
       that is the condition the writer applies (``sdk.py``: ``derived = …; if
       derived is not None and is_default_direction(direction): direction =
       derived``). ``<->``/``bidirectional`` is the signature default that means
       "not declared" (``live.is_default_direction``), so for a row declaring
       ``{semantics: produces, direction: <->}`` the MEANING wins and the graph
       stores ``<-``. Reporting the literal ``<->`` here would restore exactly
       the divergence above in the opposite direction, on a token a manifest
       spellings-check allows. ``-`` and the one-way tokens DO differentiate and
       therefore outrank the meaning, as they do at the writer.
    2. otherwise the Layer-2 meaning's direction through the SAME chain the write
       path uses: ``SEMANTICS_TRANSITION`` then ``SEMANTICS_DIRECTIONS``. A
       retired spelling is resolved here exactly as `sdk.create_operator`
       resolves it, so the census and the writer cannot disagree about a row.

    ``None`` means the row declares no RESOLVABLE direction: no ``direction`` and
    no meaning, a meaning outside the closed vocabulary, or a ``direction`` token
    the vocabulary does not recognise. It is never a default: a caller that needs
    a token must decide what absence means, which is what stops a plausible-
    looking `->` from standing in for a declaration nobody made.

    ⛔ This does NOT duplicate `TortoiseSDK._direction_from_semantics`: that one
    answers "which way does this MEANING run" for a WRITE, and cannot answer
    "which way did this DECLARED RELATION say". The declaration side needs the
    per-row override that function has no input for.
    """
    declared = rel.get("direction")
    canonical = canonical_direction(declared)
    semantics = rel.get("semantics")
    meaning = None
    if isinstance(semantics, str) and semantics:
        meaning = SEMANTICS_DIRECTIONS.get(
            SEMANTICS_TRANSITION.get(semantics, semantics))
    # The meaning outranks a NON-DECLARATION; everything else answers from the
    # declared token. ⛔ TWO RESIDUALS THE `... and not is_default_direction`
    # SHAPE LEAVES, both measured against `sdk.create_operator`:
    #   * a declared `<->` with NO meaning resolves to `None` there while the
    #     writer STORES `<->` (the semantics step never runs, and `direction` is
    #     not None, so nothing canonicalizes it) — a census cell claiming no
    #     sense for an edge the graph holds;
    #   * a junk token (`sideways`) falls through to the meaning, while the
    #     writer REFUSES it: `is_default_direction` deliberately does not treat a
    #     typo as absent, so the meaning never overwrites it and `create_operator`
    #     raises (`direction must be one of …`). A row whose write path raises has
    #     no sense to report.
    if meaning is not None and is_default_direction(declared):
        return meaning
    return canonical


class _Unset:
    """Sentinel for `declared_mechanisms(registry=…)` (#9963).

    ⛔ A plain ``None`` default cannot say "I already know there is no
    registry": it made a caller that HAS ruled a registry out trigger a second
    `_get_registry()` load, i.e. repeating the exact work it just avoided. Three
    states need three values — unset (load it), ``None`` (known absent), an
    object (use it).
    """

    __slots__ = ()


_UNSET = _Unset()


def _fact_repr(value: object) -> str:
    """``repr(value)`` as a plain ``str`` — BUILDABLE, and never raising.

    Every warn-once KEY component must be a ``str`` that is buildable without
    raising: a value whose ``__repr__`` throws would abort the caller's WRITE —
    from inside the very ``except`` that was catching something else. ``repr``
    is also the right identity, since the messages interpolate ``%r``.
    """
    try:
        text = repr(value)
        # `repr()` may return a `str` SUBCLASS (legal Python): normalise to an
        # exact `str`, or a subclass carrying `__hash__ = None` makes the KEY
        # unhashable — the same escape, one layer further down.
        return text if type(text) is str else str(text)
    except Exception:  # noqa: BLE001, RUF100 — warn-only path, never raise
        pass
    try:
        return f"<unrepr {type(value).__name__}>"
    except Exception:  # noqa: BLE001, RUF100 — even the FALLBACK is guarded:
        # a metaclass can refuse `__name__`, and this whole function's contract
        # is that nothing here can abort the caller's write.
        return "<unrepr object>"


def _fact_text(value: object) -> str:
    """``str(value)`` as a plain ``str`` — never raising (the RETURN-path half).

    The returned ``source`` strings are interpolated text too, so a ``str``
    SUBCLASS whose ``__str__`` raises would abort the caller's write from inside
    a plain f-string — the read half of the same hazard :func:`_fact_repr`
    guards on the warn path. A plain ``str`` is returned byte-identically (so no
    returned pair changes); the fallback is the guarded repr.
    """
    try:
        text = str(value)
        return text if type(text) is str else str(text)
    except Exception:  # noqa: BLE001, RUF100 — warn-only path, never raise
        return _fact_repr(value)


def _canonical_meaning(word: object, *, caller: str) -> str | None:
    """The CANONICAL spelling of a Layer-2 meaning — the ONE transition home.

    ``isinstance`` BEFORE the lookup, because ``SEMANTICS_TRANSITION.get`` raises
    ``TypeError`` on an unhashable key and this runs on the warn-only write path:
    a malformed argument must degrade to "declares nothing", not abort the
    caller's write. A ``str`` SUBCLASS may carry a ``__hash__`` that raises
    anything at all, and this is the argument half of that same NEVER-raises
    contract — the value is only ever returned or discarded, so no lookup
    failure may abort a write. LOGGED rather than merely swallowed: an argument
    this lookup cannot read declares nothing, which is a degradation of a
    governance disclosure ("logged" is a claim about the code).

    ``caller`` names the accessor in the warn key and message, so each caller
    reports its own degradation instead of one silently answering for both.
    """
    if not isinstance(word, str) or not word:
        return None
    try:
        return SEMANTICS_TRANSITION.get(word, word)
    except Exception as exc:  # noqa: BLE001, RUF100 — a `str` SUBCLASS may
        # carry a `__hash__` that raises; see the docstring's NEVER-raises half.
        # The warn KEY arm is the hyphenated accessor name, the established
        # spelling (`declared-mechanisms`).
        _warn_packs_once(
            (caller.replace("_", "-"), "word", type(exc).__name__,
             _fact_repr(word)),
            "%s: an argument this lookup cannot read (%s: %s) declares nothing "
            "for this write",
            caller, type(exc).__name__, _fact_repr(word))
        return None


def _declared_relation_rows(
    label: object, registry: object | _Unset | None, *, caller: str,
) -> list[dict]:
    """The pack relation rows that declare something for ``label`` — never raises.

    The ONE row walk both halves of a relation's declaration read — the
    MECHANISM (:func:`declared_mechanisms`) and its MEANING
    (:func:`declared_meanings`) — so the degrade-and-say-so discipline cannot
    drift between them, and neither can start reading a different set of rows
    than the other. #7852's triple is one declaration; it gets one walk.

    ``registry`` follows :func:`declared_mechanisms`' three-state convention
    (:data:`_UNSET` loads the process-wide default, ``None`` means "already
    known absent"), NOT ``pack_hierarchy``'s ``None``-means-default one. Each
    degradation is LOGGED under ``caller`` and never raised: a registry that
    will not load, a ``list_relations()`` that raises, a row that is not a
    mapping, and a row whose own ``.get`` throws are all named, because a
    silent skip reads as "no pack declares anything".
    """
    if not label:
        return []
    arm = caller.replace("_", "-")
    label_key = _fact_repr(label)
    if isinstance(registry, _Unset):
        try:
            from .domain_loader import _get_registry

            registry = _get_registry()
        except Exception as exc:  # noqa: BLE001, RUF100 — warn-not-block path
            # DEGRADE AND SAY SO (the norm this repo states at
            # `domain_loader.py:103`): swallowing this silently disables the
            # pack-declared half of a governance disclosure permanently, with no
            # instrument anywhere reporting it. Warn-only, once per (arm,
            # exception CLASS): a later, DIFFERENT failure is a different
            # degradation and must not be suppressed by the first for the life
            # of the process. ⛔ Both the KEY and the MESSAGE carry the GUARDED
            # text (`_fact_repr`): `log.warning`'s deferred `%`-formatting is
            # swallowed by the logging machinery, so a raw value whose
            # `__str__`/`__repr__` throws would emit an EMPTY record — while the
            # key was already added, poisoning it for the life of the process.
            _warn_packs_once(
                (arm, "registry", type(exc).__name__),
                "%s: the pack registry would not load (%s: %s) — the "
                "pack-declared half of this declaration is UNAVAILABLE, so "
                "only its semantics/label half is read",
                caller, type(exc).__name__, _fact_repr(exc))
            registry = None
    if registry is None:
        return []
    try:
        rows = list(registry.list_relations())  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001, RUF100 — the WALK is warn-only too
        # The raise may come MID-ITERATION (`list()` is materialised): rows
        # already yielded are discarded with it, so the pack leg can lose
        # declarations it HAS read. The warning therefore reports the read as
        # failed — never as "there was nothing to read".
        _warn_packs_once(
            (arm, "relations", type(exc).__name__),
            "%s: pack relations could not be read (%s: %s) — the "
            "pack-declared half of this declaration is SKIPPED for this write",
            caller, type(exc).__name__, _fact_repr(exc))
        rows = []
    out: list[dict] = []
    for rel in rows:
        # Per-ROW isolation, not one guard around the walk: a single bad row
        # must not truncate the ones after it. The caller renders what this
        # returns as what the write DECLARES, so quietly losing a later
        # declaration is a wrong disclosure, not a shorter one — a row that
        # cannot be read is named in the log rather than dropped (2026-10-25
        # review, P2).
        try:
            if not isinstance(rel, dict):
                # The warn-once key carries the lookup and the row's type:
                # `_WARN_ONCE` is a module-level set never cleared in
                # production, so a key of the type alone would suppress the
                # FIRST degradation of a process for every later label.
                _warn_packs_once(
                    (arm, "row-type", label_key, type(rel).__name__),
                    "%s: skipped a pack relation row that is not a mapping "
                    "(%s) while looking up %s — that row's declaration is "
                    "SKIPPED for this write",
                    caller, type(rel).__name__, label_key)
                continue
            if rel.get("predicate") != label:
                continue
            out.append(rel)
        except Exception as exc:  # noqa: BLE001, RUF100 — warn-only, per row
            _warn_packs_once(
                (arm, "row", label_key, type(rel).__name__,
                 type(exc).__name__),
                "%s: skipped an unreadable pack relation row for %s (%s: %s)",
                caller, label_key, type(exc).__name__, _fact_repr(exc))
    return out


def declared_mechanisms(
    *,
    label: str | None = None,
    semantics: str | None = None,
    registry: object | _Unset | None = _UNSET,
) -> list[tuple[str, str]]:
    """The mechanism(s) a relation write's OWN declaration fixes (#9963).

    ONE lookup, because the three-layer triple (#7852: mechanism + semantics +
    direction) is checked by more than one creator and a per-call-site copy is
    how the legs drift apart. `create_operator` is the only caller TODAY; a
    second creator that checks two legs of the triple reads this instead of
    re-deriving it (the defect #9963 records is exactly a re-derivation that
    read `predicate` and THREW `mechanism` away — the rows it iterated already
    carried it).

    Returns ``[(mechanism, human-readable source), …]`` — the mechanisms the
    write DECLARES, each with where it came from. Empty means the write declares
    none, and then there is nothing to compare against.

    The sources, and why only these:

    * **the pack relation named by `label`** (indicator 2 of #9963). Its
      declaration IS the authority on what op_type that verb means, so a
      declared ``mechanism`` in :data:`CORE_PREDICATES` is returned verbatim.
      Matched on the PREDICATE only: a pack row that merely shares a semantics
      value is a different relation, and treating it as the meaning's
      mechanism would invent an ontology ruling #7852 never made (that is the
      over-warning :data:`SEMANTICS_MECHANISMS` exists to avoid).
    * **the declared `semantics`**, then the `label` read AS a meaning (the
      pre-#7852 spelling, where the Layer-2 word rode the label) — both only
      when the word is in :data:`SEMANTICS_MECHANISMS`, i.e. when ONTOLOGY §8
      gives the meaning a mechanism at all.

    A retired semantics word is canonicalized HERE, through
    :data:`SEMANTICS_TRANSITION`, on BOTH spellings — ``opposes`` is compared as
    ``mutuallyExclusive`` (NAND) whether it arrived as ``semantics`` or as the
    pre-#7852 ``label``. The write door already canonicalizes its `semantics`
    local before calling, and nothing canonicalizes `label` anywhere, so doing
    it on one side only disclosed the same meaning differently by spelling: the
    retired word as ``semantics`` warned and the same word as ``label`` was
    silent. Comparing the pre-canonical spelling would compare nothing, which is
    the wrong-order shape #9963 says to avoid — so this function does not depend
    on the caller's order to get it right.

    A pack relation's own ``mechanism`` is AUTHORITATIVE for its predicate, even
    when the relation's ``semantics`` is one ONTOLOGY §8 gives no mechanism
    (§8's ``—`` column is about the MEANING, and a pack overriding it states a
    ruling of its own: ``investment-portfolio-manager`` declares ``marks`` as
    ``mechanism: IMPL`` with ``semantics: related``). So a NAND write of `marks`
    IS reported — the pack says what that verb means, and §8-neutrality of its
    underlying meaning does not override the pack's declaration (pinned by test).

    ⛔ NEVER raises and never refuses: a registry that will not load, or one that
    yields a row this walk cannot read, degrades to what could be read (the core
    map alone in the worst case). The warn-not-block path must not turn a load
    failure — or a malformed row — into a write failure, so the ROW WALK is
    inside the guard, not just the load, and an unhashable `mechanism` is
    rejected rather than raising out of a frozenset membership test. Every
    degradation is LOGGED (`_warn_packs_once`), never silent: a pack leg that
    stopped disclosing must be visible somewhere other than the missing warning
    it would have produced. That covers all FIVE arms of the walk — the registry
    load, `list_relations()` raising, a row that is not a mapping, a row whose
    ``mechanism`` is present but unusable (not a `str`, or not in
    :data:`CORE_PREDICATES`), and a row whose read itself raises (the per-row
    `except`, e.g. one whose `.get` throws) — because "logged" is a claim about
    the code, not about the arm someone happened to be thinking of. The ONE drop
    that is deliberately silent is a row carrying no ``mechanism`` at all: that
    is the old-format shape (``project-management``), where nothing was declared
    and so nothing was lost. One outcome is PARTIAL rather than skipped: a
    ``list_relations()`` that raises MID-ITERATION loses the rows already
    materialised, so the warning reports the read as FAILED — never as "there
    was nothing to read".

    The same refusal covers the ARGUMENTS: `SEMANTICS_TRANSITION.get` is itself a
    dict lookup, so an unhashable `label`/`semantics` raises `TypeError` — the
    identical exception class the row walk is guarded against, on values this
    function never validated. A non-`str` argument therefore declares nothing
    (it is not a meaning) instead of aborting the caller's write; a good argument
    BESIDE a bad one is still read, so the caller keeps every declaration the
    walk could read. Unreachable from `create_operator`, which tests the same
    values against a set and a dict before calling — but the `registry=` seam
    exists for a second creator, and a contract stated as NEVER must hold for
    every input the signature admits, not only today's caller's.
    """
    found: list[tuple[str, str]] = []

    # The guarded text helpers live at module level (`_fact_repr` /
    # `_fact_text`) because the MEANING accessor interpolates the very same
    # values into its own warn keys and returned sources; the local names stay
    # as aliases so this function's body reads as it always has.
    _key_text = _fact_repr
    _safe_text = _fact_text

    label_key = _key_text(label)

    def _usable(mechanism: object) -> TypeGuard[str]:
        # `isinstance` BEFORE the membership test: `mechanism in CORE_PREDICATES`
        # raises TypeError on an unhashable value (a list from a malformed row),
        # which would abort the caller's write from a comparison documented never
        # to refuse one. A non-str is simply not a mechanism.
        return isinstance(mechanism, str) and mechanism in CORE_PREDICATES

    def _add(mechanism: object, source: str) -> None:
        if _usable(mechanism) and (mechanism, source) not in found:
            found.append((mechanism, source))

    # 1. the pack relation the caller NAMED, by predicate — the row WALK that
    #    loads the registry, reads `list_relations()` and logs every degradation
    #    is SHARED with `declared_meanings` (`_declared_relation_rows`), so the
    #    two halves of one declaration cannot read different rows or report a
    #    degradation differently.
    for rel in _declared_relation_rows(
            label, registry, caller="declared_mechanisms"):
        try:
            mechanism = rel.get("mechanism")
            # KEY PRESENCE, not nullness: an explicit `mechanism: null` is
            # PRESENT and is not a `str`, so it belongs to the arm the
            # docstring promises to log; only a row with no `mechanism` key
            # at all (the old-format shape) legitimately declares nothing.
            if "mechanism" in rel and not _usable(mechanism):
                # Present-but-unusable, as opposed to ABSENT: a row that
                # names a mechanism just said something, and dropping it
                # unread is the silent disable the docstring rules out. The
                # key carries the label AND the value, so a second, DIFFERENT
                # bad declaration is not suppressed by the first.
                _warn_packs_once(
                    ("declared-mechanisms", "row-mechanism", label_key,
                     _key_text(mechanism)),
                    "declared_mechanisms: the pack relation %s declares a "
                    "mechanism this walk cannot use (%s: %s) — that "
                    "declaration is SKIPPED for this write",
                    label_key, type(mechanism).__name__,
                    _key_text(mechanism))
                continue
            _add(mechanism,
                 f"the pack relation '{_safe_text(label)}' "
                 f"({rel.get('pack') or 'pack'})")
        except Exception as exc:  # noqa: BLE001, RUF100 — warn-only, per row
            _warn_packs_once(
                ("declared-mechanisms", "row", label_key,
                 type(rel).__name__, type(exc).__name__),
                "declared_mechanisms: skipped an unreadable pack relation row "
                "for %s (%s: %s)", label_key, type(exc).__name__,
                _key_text(exc))

    # 2. the declared MEANING — the Layer-2 word, whether it arrived as
    #    `semantics` or (pre-#7852 spelling) as the `label` itself. Both are
    #    canonicalized, so a retired word cannot mean two different things
    #    depending on which parameter carried it.
    def _canonical(word: object) -> str | None:
        # The canonicalisation HOME is module level (`_canonical_meaning`): the
        # MEANING accessor canonicalises through the same transition table, and
        # a second copy of it here is the re-derivation this module's own
        # docstring forbids.
        return _canonical_meaning(word, caller="declared_mechanisms")

    semantics_c = _canonical(semantics)
    label_c = _canonical(label)
    if semantics_c in SEMANTICS_MECHANISMS:
        retired = ("" if semantics_c == semantics
                   else f" (retired → '{_safe_text(semantics_c)}')")
        _add(SEMANTICS_MECHANISMS[semantics_c],
             f"the semantics '{_safe_text(semantics)}'{retired}")
    if label_c in SEMANTICS_MECHANISMS and label_c != semantics_c:
        retired = "" if label_c == label else f" (retired → '{_safe_text(label_c)}')"
        _add(SEMANTICS_MECHANISMS[label_c],
             f"the label '{_safe_text(label)}' (a Layer-2 meaning){retired}")

    return found


def declared_meanings(
    *,
    label: str | None = None,
    registry: object | _Unset | None = _UNSET,
) -> list[tuple[str, str]]:
    """The MEANING(s) a relation write's own PACK DECLARATION fixes (#11548).

    The Layer-2 half of the triple (#7852: mechanism + semantics + direction)
    whose mechanism half :func:`declared_mechanisms` answers. The pack relation
    row IS the declaration: ``predicate: partOf`` with ``semantics: hasPart``
    fixes what that verb MEANS, and the meaning is what ONTOLOGY §8's direction
    table is keyed on (:data:`SEMANTICS_DIRECTIONS`) — so a leg that consumes the
    row's mechanism but not its meaning stores an edge whose direction
    contradicts the declaration that licensed the write. That is not a cosmetic
    gap: the row is invisible to the leg's own direction lookup, so a declared
    ``<->`` (``hasPart``) or ``-`` (``related``/``transacts``) meaning is written
    as a one-way edge and EP propagates belief the ruling denies.

    Returns ``[(meaning, human-readable source), …]`` — the row's own
    ``semantics`` CANONICALIZED through :data:`SEMANTICS_TRANSITION` (a retired
    spelling such as ``funds`` collapses onto ``transacts``, ``opposes`` onto
    ``mutuallyExclusive``), then filtered to :data:`SEMANTICS_VALUES`: a word
    outside the closed vocabulary declares no meaning any reader here can rule
    on, and pack load already names it. Empty means the row declares NO meaning
    — never "the meaning is unknown", a state no row can be in.

    Matched on the PREDICATE only, deliberately and for the same reason
    :func:`declared_mechanisms` is: the pack's declaration is the authority on
    what its own verb means, and a row that merely shares a meaning with it is a
    DIFFERENT relation. ⛔ A kind-keyed lookup was considered and REJECTED, and
    the reason is a failure mode, not convenience: pack kinds are spelled through
    their own resolvers (``core:`` prefixes, cross-pack references), so an exact
    ``(fromKind, toKind)`` match that misses an alias would find no declaration
    and silently fall back to the one-way default — trading a hypothetical
    ambiguity for a real wrong direction. Measured on the shipped catalog
    (``_get_registry().list_relations()``, 36 rows), FOUR predicates are declared
    more than once — ``addresses`` ×6, ``contains`` ×2, ``targets`` ×2,
    ``assignedTo`` ×2 — and the predicate-only lookup is exact on today's catalog
    because all rows of each agree on their effective direction (``->``, ``<->``,
    ``->``, ``->``). ⛔ The residual this lookup does NOT resolve is named rather
    than denied: a predicate whose sibling rows diverge in sense or direction is
    answered by whichever row resolves first, so giving ONE of the six
    ``addresses`` rows a different sense applies it to all six. (An earlier
    version of this paragraph claimed the case did not exist at all and named
    ``assignedTo`` as a row that could never reach the direct writer; both were
    false against the shipped manifests — it carries ``mechanism: IMPL`` and
    ``semantics: managedBy`` and its write path is ``('operator',)`` — #11548
    review cycle 2.)

    The row walk, its degradation arms and their logging are SHARED with
    :func:`declared_mechanisms` (:func:`_declared_relation_rows`), and so is the
    canonicalisation (:func:`_canonical_meaning`) — the two halves of one
    declaration cannot drift apart, and a second transition table here is the
    re-derivation this module's docstring forbids.

    ⛔ NEVER raises: a registry that will not load, or a row this walk cannot
    read, degrades to what COULD be read (empty in the worst case) and is
    LOGGED. A direction chosen from a partial read is still a written edge; an
    exception here would abort a capture that was otherwise fine.
    """
    found: list[tuple[str, str]] = []
    label_key = _fact_repr(label)
    label_text = _fact_text(label)
    for rel in _declared_relation_rows(label, registry,
                                      caller="declared_meanings"):
        try:
            meaning = _canonical_meaning(rel.get("semantics"),
                                        caller="declared_meanings")
            if meaning is None or meaning not in SEMANTICS_VALUES:
                continue
            pair = (meaning,
                    f"the pack relation '{label_text}' "
                    f"({rel.get('pack') or 'pack'})")
            if pair not in found:
                found.append(pair)
        except Exception as exc:  # noqa: BLE001, RUF100 — warn-only, per row
            _warn_packs_once(
                ("declared-meanings", "row", label_key, type(rel).__name__,
                 type(exc).__name__),
                "declared_meanings: skipped an unreadable pack relation row "
                "for %s (%s: %s)", label_key, type(exc).__name__,
                _fact_repr(exc))
    return found


def declared_directions(
    *,
    label: str | None = None,
    registry: object | _Unset | None = _UNSET,
) -> list[tuple[str, str]]:
    """The DIRECTION(s) a relation write's own PACK DECLARATION records (#11548).

    The third leg of #7852's triple (mechanism + semantics + direction), and the
    one a write leg may not re-derive: :func:`effective_direction` is already
    THE per-row resolver — a DIFFERENTIATING declared token outranks the
    meaning, and the meaning is the fallback (indicator (c) of #11083, whose
    docstring states the rule this function exists to keep: *"the census and
    the writer cannot disagree about a row"*). So this function adds no rule of
    its own; it walks the SAME rows :func:`declared_mechanisms` and
    :func:`declared_meanings` walk (:func:`_declared_relation_rows`) and asks
    each one the resolver that the census already asks
    (``tools/pack_relation_census.py``'s ``effective_direction`` cell).

    ⛔ Why a leg needs it, measured: without it a writer that consumes the row's
    MEANING but not its own ``direction:`` writes the meaning's default on a row
    that declares the opposite — ``blocks`` (``packs/project-management``:
    ``semantics: dependsOn, direction: "<-"``) and ``holds``
    (``packs/venture``: ``mechanism: IMPL, direction: "<-"``) both landed ``->``,
    while the census reports ``<-`` for the same rows. EP then propagates belief
    the row exists to forbid — for ``holds`` it asserts *the company owns the
    investment* on a claim stating the reverse. ONTOLOGY §3.6 carries the sense
    per declaration and §8's ``ownedBy`` row says the declaration wins over the
    default, so this is the rule, not a refinement of it.

    Returns ``[(direction, human-readable source), …]``; empty means no row
    declares a RESOLVABLE direction (the three states ``None`` covers are named
    at :func:`effective_direction`). Matched on the PREDICATE only, for the
    reasons :func:`declared_meanings` records at length — the same lookup
    semantics, so the two halves of one declaration cannot diverge.

    ⛔ NEVER raises: a registry that will not load, or a row this walk cannot
    read, degrades to what COULD be read and is LOGGED (the shared walk's
    discipline). An exception here would abort a capture that was otherwise
    fine.
    """
    found: list[tuple[str, str]] = []
    label_key = _fact_repr(label)
    label_text = _fact_text(label)
    for rel in _declared_relation_rows(label, registry,
                                      caller="declared_directions"):
        try:
            direction = effective_direction(rel)
            if not direction:
                continue
            pair = (direction,
                    f"the pack relation '{label_text}' "
                    f"({rel.get('pack') or 'pack'})")
            if pair not in found:
                found.append(pair)
        except Exception as exc:  # noqa: BLE001, RUF100 — warn-only, per row
            _warn_packs_once(
                ("declared-directions", "row", label_key, type(rel).__name__,
                 type(exc).__name__),
                "declared_directions: skipped an unreadable pack relation row "
                "for %s (%s: %s)", label_key, type(exc).__name__,
                _fact_repr(exc))
    return found

# ── Manifest v3.1 (epic #909 §1.4/§1.5): extraction behaviour slots (#1026) ──
# Per-pack slots that let a pack shape extraction for its own domain while the
# engine stays pack-agnostic: the pack drives the extractor, the extractor holds
# no domain behaviour.
#
# ``entityCues`` is the DECLARATIVE slot — keyed by a KIND from the pack's
# vocabulary (its own, core, or a kind that resolves to exactly one other
# loaded pack), so a cue names a kind rather than a name pattern. SHAPE is
# checked per-manifest; RESOLUTION is checked across packs after load_all(),
# because a bare name is not decidable from one manifest alone.
VALID_EXTRACTION_KEYS = frozenset({
    "active", "sourceTypes", "enforcement",
    "entityCues", "relationTemplates", "valueGate", "promptFragments",
})

# ⛔ Refusals, named rather than left to the unknown-key rule.
# The design that motivated these two slots dropped artefacts at MINT by
# matching NAME PATTERNS. The owner ruling on #1026 chose the declarative slot
# instead: "keep the slot, as declared entity types only — the pack declares
# which kinds its artefacts are; no name patterns; nothing is dropped at mint."
# OVERRIDES: the common pack-filtering default (pattern-based exclusion at
# ingestion) — we drop nothing at mint; the pack declares kinds, and the value
# gate judges utterances.
# A pack author arriving with a vendor's pattern-filtering page is told WHY at
# the point of adoption, instead of being left to infer it from "unknown key".
REFUSED_EXTRACTION_KEYS = {
    "excludePatterns": (
        "name-pattern dropping is not part of v3.1 — the #1026 ruling is that "
        "nothing is dropped at mint; a pack declares its kinds instead "
        "(entityCues)"
    ),
    "entityPatterns": (
        "name-pattern dropping is not part of v3.1 — the #1026 ruling is that "
        "nothing is dropped at mint; a pack declares its kinds instead "
        "(entityCues)"
    ),
}

# Prompt-fragment budget (R6 §1.5 — "promptFragments token-capped").
# The count is a whitespace-word PROXY, not a tokenizer: this runs at manifest
# load, where no tokenizer is loaded, and the cap exists to stop a pack shipping
# a wall of prose into every value brief rather than to bound a real token
# budget. Deliberately generous — a fragment is one instruction snippet.
MAX_PROMPT_FRAGMENT_TOKENS = 120
MAX_PROMPT_FRAGMENTS_TOKENS = 600

# Character bounds ALONGSIDE the word proxy. The proxy counts whitespace
# tokens, so a whitespace-free blob of any size costs 1 — a minified payload or
# a long single-line code fragment would sail through both word caps. These
# bounds are what make the guard non-evadable by deleting the spaces.
MAX_PROMPT_FRAGMENT_CHARS = 4 * MAX_PROMPT_FRAGMENT_TOKENS
MAX_PROMPT_FRAGMENTS_CHARS = 4 * MAX_PROMPT_FRAGMENTS_TOKENS

#: `valueGate` keys the template documents. A mistyped gate key is silent dead
#: config of exactly the species the unknown-key rule exists to catch, so the
#: neighbouring slot's strictness is matched here.
VALID_VALUE_GATE_KEYS = frozenset({"keep", "drop"})


def prompt_token_proxy(text: str) -> int:
    """Whitespace-word count as a PROXY for a token count.

    Named "proxy" so no caller mistakes it for a token-accurate count. No
    tokenizer is loaded at manifest-load time, and the cap is a guard against a
    pack shipping a wall of prose into every brief it activates — not a budget
    that must be exact. Pair it with a CHARACTER bound (see
    ``MAX_PROMPT_FRAGMENT_CHARS``): a word count alone is evaded by removing
    the spaces.
    """
    return len(text.split())


def _as_map(value: Any) -> dict:
    """``value`` as a dict, else ``{}`` — never raises on a malformed section.

    Validation reports the shape error; normalization must still return the
    documented default shape, so a caller reading ``extraction`` never meets a
    non-dict where the manifest documents a map.
    """
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    """``value`` as a list, else ``[]`` — never raises, never splits a string.

    ``list("abc")`` would silently become ``['a','b','c']``: the isinstance
    guard keeps malformed input visible as the shape error validation already
    reports for it, instead of manufacturing data the manifest never expressed.
    """
    return list(value) if isinstance(value, list) else []


def _kind_ref_shape_error(ref: Any) -> str | None:
    """Why ``ref`` is not a well-formed kind reference, else ``None``.

    SHAPE ONLY — deliberately not a resolution check. Whether a reference
    RESOLVES is a cross-pack question (a bare name may belong to exactly one
    other loaded pack), so it is decided by ``_validate_cross_pack_refs`` once
    every pack is loaded — the same division ``relations`` already uses, and
    the reason an unknown BARE name must NOT be rejected here.

    What it catches is the MALFORMATION itself. ``_resolve_kind_ref`` would
    report ``":kind"``, ``"ns:"`` and ``"a:b:c"`` only as the generic "does
    not resolve to any known kind", which does not say what is wrong with them;
    this check names it, at the per-manifest layer that drops the pack first.
    """
    if not isinstance(ref, str) or not ref.strip():
        return "must be a non-empty string"
    if ":" in ref:
        ns, _, kind = ref.partition(":")
        if not ns.strip() or not kind.strip() or ":" in kind:
            return "is not a well-formed 'namespace:kind' reference"
    return None


# ── Pack data model ───────────────────────────────────────────────────────

@dataclasses.dataclass
class PackManifest:
    """Parsed manifest for one expansion pack."""
    namespace: str
    name: str
    version: str
    tier: str
    description: str
    path: Path

    # Ontology
    object_kinds: list[str] = dataclasses.field(default_factory=list)
    event_kinds: list[str] = dataclasses.field(default_factory=list)
    point_kinds: list[str] = dataclasses.field(default_factory=list)
    document_kinds: list[str] = dataclasses.field(default_factory=list)
    action_kinds: list[str] = dataclasses.field(default_factory=list)
    relations: list[dict] = dataclasses.field(default_factory=list)

    # Manifest v3 (R6 §3.1): per-kind extractor metadata, keyed by pack-local
    # kind name. Allowed value keys: description, synonyms, examples,
    # nearMisses, extractable, storeAs, enforcement, statuses, folds,
    # pipeline (#2792), values (#2818 — the per-kind value-field declaration;
    # vocabulary + shape rules in ``tortoise/pack_values.py``).
    kind_defs: dict[str, dict] = dataclasses.field(default_factory=dict)

    # Manifest v3 (R6 §3.2): first-class business-logic chains.
    # [{id, name?, description?, steps[], enforcement?, edges?}]
    chains: list[dict] = dataclasses.field(default_factory=list)

    # The pack's DECLARED business hierarchy: [{path: 'Theme → Epic', type: hasPart}].
    #
    # ⛔ Carried, not just validated (#11001). `_validate` has shape-checked this
    # list since R6, and until #11001 the parsed value was DROPPED here — the
    # declaration reached no producer, so `hierarchies` was config that could not
    # change the graph. The read is `tortoise.pack_hierarchy.declared_hierarchy`,
    # which resolves each entry to a namespaced (from_kind, predicate, to_kind)
    # and binds it to a write path from `relation_write_paths`. Nothing infers a
    # hierarchy that is not written here.
    hierarchies: list[dict] = dataclasses.field(default_factory=list)

    # Manifest v3 (R6 §3.4): extraction activation + enforcement config,
    # normalized — always present with defaults (backward compat: absent in
    # v2 manifests → active: true, sourceTypes: [], enforcement default warn).
    extraction: dict = dataclasses.field(
        default_factory=lambda: {
            "active": True,
            "sourceTypes": [],
            "enforcement": {
                "default": "warn",
                "kinds": {},
                "relations": {},
                "chains": {},
            },
            # v3.1 slots (#1026): present here too, so a manifest constructed
            # directly (tests, tooling) has the SAME shape _normalize_extraction
            # produces — a consumer indexing a slot must not KeyError on one of
            # the two paths. Pinned by test_dataclass_default_matches_normalize.
            "entityCues": {},
            "relationTemplates": [],
            "valueGate": {},
            "promptFragments": [],
        }
    )

    # Subclass declarations: {kind: parent_kind} — e.g., {"epic": "Project"}
    # Parent must exist in core CANONICAL_OBJECT_KINDS or another pack's kinds.
    kind_subclasses: dict[str, str] = dataclasses.field(default_factory=dict)

    # Equivalence declarations: {kind: [other_ns:kind, ...]} — e.g., {"issue": ["pm:issue"]}
    # Means "this pack's 'issue' is the same concept as 'pm:issue'."
    # Bidirectional: the registry makes it symmetric at query time.
    kind_equivalences: dict[str, list[str]] = dataclasses.field(default_factory=dict)

    # Relations: edge schema declarations
    # New format (preferred): {predicate, fromKind, toKind, mechanism, cardinality?}
    #   - predicate: domain verb — "addresses", "competesWith", "implements"
    #   - fromKind/toKind: specific pack kinds — "product-strategy:feature"
    #   - mechanism: IMPL or NAND — epistemic mechanism
    #   - cardinality: optional (one_to_one, one_to_many, many_to_one, many_to_many)
    # Old format (compat): {predicate, from, to, cardinality?}
    #   - from/to: entity types — "Object", "Subject"

    # Connectors
    connectors: list[dict] = dataclasses.field(default_factory=list)

    # Tools
    tools: list[dict] = dataclasses.field(default_factory=list)

    # Dependencies
    depends_on: list[str] = dataclasses.field(default_factory=list)

    # ── Manifest v3 read-side helpers (consumed by the value-brief compiler) ──

    def is_extractable(self, kind: str) -> bool:
        """kindDefs[].extractable — default True (R6 §3.1)."""
        kd = self.kind_defs.get(kind, {})
        if isinstance(kd, dict) and "extractable" in kd:
            return bool(kd["extractable"])
        return True

    def relation_is_extractable(self, rel: dict) -> bool:
        """relations[].extractable — default False (R6 §3.3)."""
        return bool(rel.get("extractable", False))

    def store_as(self, kind: str) -> str:
        """Resolved storeAs for a pack-local kind (R6 §3.1).

        Explicit kindDefs[].storeAs wins; otherwise inferred from list
        placement: pointKinds → claim (decision/vision/strategy/plan/goal/
        target/humanApproval → decision), objectKinds/documentKinds → entity,
        eventKinds → event.
        """
        kd = self.kind_defs.get(kind, {})
        if isinstance(kd, dict) and kd.get("storeAs"):
            return kd["storeAs"]
        if kind in self.point_kinds:
            return "decision" if kind in DECISION_POINT_KINDS else "claim"
        if kind in self.event_kinds:
            return "event"
        if kind in self.document_kinds or kind in self.object_kinds:
            return "entity"
        return "claim"

    def enforcement_for(self, kind: str) -> str:
        """Kind-level enforcement resolution order (R6 §3.4):
        kindDefs[].enforcement → extraction.enforcement.kinds → default → warn."""
        kd = self.kind_defs.get(kind, {})
        if isinstance(kd, dict) and kd.get("enforcement"):
            return kd["enforcement"]
        kinds_cfg = self.extraction["enforcement"].get("kinds", {})
        if kind in kinds_cfg:
            return kinds_cfg[kind]
        return self.extraction["enforcement"].get("default", "warn")

    def enforcement_for_relation(self, predicate: str) -> str:
        """Relation-level enforcement: extraction.enforcement.relations → default → warn."""
        rel_cfg = self.extraction["enforcement"].get("relations", {})
        return rel_cfg.get(predicate, self.extraction["enforcement"].get("default", "warn"))

    def enforcement_for_chain(self, chain_id: str) -> str:
        """Chain-level enforcement: chain.enforcement → extraction.enforcement.chains
        → default → warn (R6 §3.2/§3.4)."""
        for chain in self.chains:
            if chain.get("id") == chain_id and chain.get("enforcement"):
                return chain["enforcement"]
        chains_cfg = self.extraction["enforcement"].get("chains", {})
        return chains_cfg.get(chain_id, self.extraction["enforcement"].get("default", "warn"))

    def is_active_for(self, source_type: str) -> bool:
        """§3.4 activation: active AND (sourceTypes empty or contains source_type)."""
        if not self.extraction.get("active", True):
            return False
        source_types = self.extraction.get("sourceTypes") or []
        return not source_types or source_type in source_types


# ── Registry ──────────────────────────────────────────────────────────────


def env_packs_dir_value() -> str:
    """Raw stripped ``TORTOISE_PACKS_DIR`` value ('' when unset/blank).

    Blank/whitespace is treated as unset (config.py precedent for
    ``TORTOISE_DB_PATH``) — a blank value must never resolve to the process
    CWD (``Path('') == Path('.')``).
    """
    return os.environ.get("TORTOISE_PACKS_DIR", "").strip()


def _has_loadable_manifests(d: Path) -> bool:
    """True when the dir holds at least one loadable pack manifest.

    Applies the SAME namespace skip rules as ``PackRegistry.load_all``
    (``_``/``.``-prefixed dirs are scaffolding — e.g. ``packs/_template/``)
    so the resolver's emptiness definition can never diverge from the
    loader's: a dir with only ``_template/manifest.yaml`` is EMPTY and must
    fall through, never resolve (the silent-empty G1 class).
    """
    return any(
        m.is_file()
        for child in d.glob("*")
        if not child.name.startswith(("_", "."))
        for m in child.glob("manifest.yaml")
    )


def env_packs_dir() -> Path | None:
    """Resolve the ``TORTOISE_PACKS_DIR`` env leg (#1930, epic #1891 WF-2).

    Returns the dir when set+valid (exists, is a directory, holds ≥1 loadable
    manifest). Returns ``None`` when unset/blank (no warning) or when set but
    missing / not-a-dir / empty (logged warning, warn-once per value — the
    caller falls back to ``default_packs_dir()``). A set-but-broken value
    must never silently resolve to an empty registry (G1 class): the warning
    is the operator's diagnostic; ``registry.errors`` (and the all-broken
    fallback in ``domain_loader._get_registry``) cover manifest-level
    failures.
    """
    val = env_packs_dir_value()
    if not val:
        return None
    env_dir = Path(val).expanduser()
    if not env_dir.exists():
        _warn_packs_once(
            ("env-dir", val, "missing"),
            "TORTOISE_PACKS_DIR=%r does not exist — falling back to the "
            "default pack catalog", val)
        return None
    if not env_dir.is_dir():
        _warn_packs_once(
            ("env-dir", val, "not-a-dir"),
            "TORTOISE_PACKS_DIR=%r is not a directory — falling back to the "
            "default pack catalog", val)
        return None
    if not _has_loadable_manifests(env_dir):
        _warn_packs_once(
            ("env-dir", val, "empty"),
            "TORTOISE_PACKS_DIR=%r contains no pack manifests — falling back "
            "to the default pack catalog", val)
        return None
    return env_dir


def default_packs_dir(*, _skip_env: bool = False) -> Path:
    """Resolve the default packs directory (env → packaged → repo root).

    The resolution order (epic #1891 plan §5, WF-2; #1930):

    0. ``TORTOISE_PACKS_DIR`` (env) — self-host custom pack directory, when
       set+valid (exists + holds ≥1 loadable manifest). Set-but-missing /
       not-a-dir / empty → warn-once + fall through (never silent empty).
    1. Packaged default — ``<package>/packs`` (``site-packages/tortoise/packs``
       on a wheel install, where package-data ships the catalog). Chosen ONLY
       when it actually contains loadable manifests (same skip rules as
       ``load_all`` — ``_template`` alone is not a catalog): the source tree's
       ``tortoise/packs/`` holds the discovery stub (no yamls), so in dev and
       in the Docker build context this leg falls through.
    2. Repo root — ``<package>/../packs`` (dev/editable installs, and the
       Docker image's ``COPY packs/ packs/`` at ``/app``).

    All registry consumers resolve the catalog through this single primitive
    (previously each hardcoded ``Path(__file__).parent.parent / "packs"`` —
    which on a wheel install resolves ``site-packages/packs`` → zero packs,
    the silent empty-registry defect G1, #1929).

    ``_skip_env`` is the internal escape hatch for the registry-level
    all-broken fallback (``domain_loader._get_registry``): it re-resolves the
    default chain WITHOUT the env leg after the env dir proved unusable. Pure
    kwarg — no env/global mutation, safe on concurrent callers.
    """
    if not _skip_env:
        env_dir = env_packs_dir()
        if env_dir is not None:
            return env_dir
    packaged = Path(__file__).resolve().parent / "packs"
    if _has_loadable_manifests(packaged):
        return packaged
    repo_root = Path(__file__).resolve().parent.parent / "packs"
    if packaged.is_dir() and not repo_root.exists():
        # A packaged layout that ships ZERO manifests (package-data /
        # MANIFEST.in regression) falls back to a nonexistent repo-root leg —
        # the silent empty-registry defect class (G1). Keep the fallback
        # (dev/Docker must not break) but say it out loud; the publish
        # smokes are the hard gate.
        _warn_packs_once(
            ("packaged-stub", str(packaged), str(repo_root)),
            "packaged packs dir %s contains no manifests and repo-root "
            "fallback %s does not exist — registry will load 0 packs",
            packaged, repo_root,
        )
    return repo_root


class PackRegistry:
    """Catalog of all installed expansion packs.

    Usage:
        registry = PackRegistry(packs_dir)
        registry.load_all()
        print(registry.list_packs())
        print(registry.list_all_kinds())
    """

    def __init__(self, packs_dir: Path | str):
        # #2729: warnings collected during `_validate` (which runs before the
        # PackManifest exists) and published in `load_all`. Reset per load.
        self._pending_lifecycle_warnings: dict[str, list[str]] = {}
        self.packs_dir = Path(packs_dir)
        self.packs: dict[str, PackManifest] = {}
        self.errors: dict[str, list[str]] = {}  # namespace → error messages
        # #2781: namespace → UNRESOLVED EXTERNAL REFERENCE messages. Distinct
        # from `errors` on purpose: an error isolates the pack (its manifest is
        # unusable), while an unresolved reference is a MISSING PEER — the
        # pack's own manifest is valid and it stays loaded, so one absent pack
        # cannot cascade through the catalog. Named "unresolved_refs" rather
        # than "warnings" because the hosted install path reads it as a
        # FAIL-CLOSED refusal (see pack_manifest_store.validate_manifest).
        self.unresolved_refs: dict[str, list[str]] = {}
        # #2729: namespace → NON-BLOCKING lifecycle declarations. A pack whose
        # declared `statuses` reuses a word from the shared/connector
        # vocabulary loads (the kind owns its own state — see
        # `status_folds.SHARED_CONNECTOR_STATUS_WORDS`), but the overlap must
        # be visible rather than silent. Errors (unknown fold status, orphan
        # fold event, malformed lists) still isolate the pack via `errors`.
        self.lifecycle_warnings: dict[str, list[str]] = {}
        # #2729: lazily compiled `StatusFoldIndex` (invalidated on reload).
        self._status_fold_index = None
        # #1930: set by domain_loader._get_registry when the TORTOISE_PACKS_DIR
        # leg was unusable (0 valid packs / load failure) and the registry
        # fell back to the default catalog — a queryable diagnostic for ops.
        self.fallback_note: str | None = None

    # ── Load ──────────────────────────────────────────────────────────

    def load_all(self) -> int:
        """Load all packs/*/manifest.yaml. Returns count of successfully loaded packs.

        Per-pack load isolation (R-16, epic #909): a manifest that fails
        validation fails THAT pack only — the registry keeps loading every
        other pack, and any pack with a recorded error (load-time or
        cross-pack) is excluded from self.packs so the compiled vocabulary
        (expansions, brief, register_kinds) only ever sees healthy packs.
        Errors remain queryable via self.errors.

        #2781 — a MISSING PEER is not a broken manifest, and it no longer
        cascades. Two buckets, deliberately separated:

          * ``self.errors``      — the manifest is unusable (schema/cross-pack
                                   typo). The pack is ISOLATED (removed from
                                   ``self.packs``).
          * ``self.unresolved_refs`` — the pack's own manifest is valid but a
                                   reference (or a declared ``depends_on``)
                                   names a pack that is not loaded. The pack
                                   STAYS loaded; the reference simply matches
                                   nothing, and the diagnostic names the
                                   missing pack. Availability over strictness:
                                   one absent pack must not take down the
                                   catalog (before #2781, dropping `pm` also
                                   dropped `dev` and `product-strategy`).

        The hosted INSTALL path is the fail-closed half of the same contract:
        ``pack_manifest_store.validate_manifest`` refuses an install whose refs
        or declared dependencies cannot be satisfied by the tenant's installed
        set (see ``_missing_pack_explanation`` for the exact clause split).
        """
        if not self.packs_dir.exists():
            return 0
        # Reset state up front (review P1, PR #978): a re-entry previously
        # accumulated stale errors and the isolation drop loop wiped every
        # pack that had ANY recorded error — including errors from an earlier
        # call. Reload must be idempotent.
        self.packs = {}
        self.errors = {}
        self.unresolved_refs = {}
        self.lifecycle_warnings = {}
        self._pending_lifecycle_warnings = {}
        self._status_fold_index = None
        count = 0
        for manifest_path in sorted(self.packs_dir.glob("*/manifest.yaml")):
            ns = manifest_path.parent.name
            if ns.startswith("_") or ns.startswith("."):
                continue  # skip _template, hidden dirs
            try:
                manifest = self._load_one(manifest_path)
                if manifest.namespace in self.packs:
                    self.errors[ns] = [f"duplicate namespace: {manifest.namespace}"]
                    continue
                self.packs[manifest.namespace] = manifest
                count += 1
            except Exception as e:
                self.errors[ns] = [str(e)]
        self._validate_cross_pack_refs()
        # R-16 isolation: drop packs that failed cross-pack validation so the
        # compiled registry only contains healthy packs.
        for ns in [ns for ns, pack in self.packs.items() if ns in self.errors]:
            del self.packs[ns]
            count -= 1
        # Fixpoint (review P2, PR #978): a pack whose cross-pack references
        # pointed at a now-dropped pack must also be excluded — re-validate
        # against the surviving set so the compiled vocabulary never carries
        # dangling references.
        if self.errors:
            self._validate_cross_pack_refs()
            for ns in [ns for ns, pack in self.packs.items() if ns in self.errors]:
                del self.packs[ns]
                count -= 1
        # #2729: publish the non-blocking lifecycle warnings for SURVIVING
        # packs only (a pack isolated for a hard error has its warnings
        # dropped with it). `_pending_lifecycle_warnings` is the channel
        # `_validate` writes into — the PackManifest does not exist yet at
        # validation time.
        self.lifecycle_warnings = {
            ns: list(msgs)
            for ns, msgs in self._pending_lifecycle_warnings.items()
            if ns in self.packs
        }
        # #2729 review P2: a bare object-kind name a lifecycle is declared for
        # by more than one pack is ambiguous at the fold — the index matches
        # the bare spelling for BOTH packs (`kind_forms_for`) and the last
        # declaring pack wins. Record it on the same non-blocking channel: a
        # namespaced Object is never ambiguous, so this is a warning rather
        # than a pack-isolating error.
        for bare, owners in bare_lifecycle_kind_collisions(self.packs).items():
            for ns in owners:
                co_claimants = [o for o in owners if o != ns]
                self.lifecycle_warnings.setdefault(ns, []).append(
                    f"object kind '{bare}' is declared with a lifecycle by "
                    f"multiple packs ({', '.join(owners)}) — a BARE-spelled "
                    f"`objectKind` cannot be attributed to one of them, so "
                    f"the fold applies the last declaring pack's rule "
                    f"(co-claiming pack(s): {', '.join(co_claimants)}; use the "
                    f"namespaced spelling or declare the lifecycle in one "
                    f"pack)"
                )
        if self.lifecycle_warnings:
            _warn_packs_once(
                ("lifecycle-vocabulary", str(self.packs_dir),
                 tuple(sorted((ns, tuple(msgs))
                              for ns, msgs in self.lifecycle_warnings.items()))),
                "pack registry %s: %d pack(s) carry a non-blocking lifecycle "
                "declaration note — a `statuses` word shared with the connector "
                "vocabulary, or a bare object kind claimed by more than one "
                "pack (see registry.lifecycle_warnings): %s",
                self.packs_dir, len(self.lifecycle_warnings),
                "; ".join(f"{ns}: {msgs[0]}"
                          for ns, msgs in sorted(self.lifecycle_warnings.items())),
            )
        self._build_kind_expansions()
        # #1930 (epic #1891 WF-2 / E2E-2): a pack isolated by R-16 must never
        # be silent — every consumer surfaces the startup warning (registry.errors
        # stays the supplementary diagnostic). Warn-once per (dir, error-set)
        # signature so repeated loads of the same broken dir do not spam.
        if self.errors:
            _warn_packs_once(
                ("load-errors", str(self.packs_dir), tuple(sorted(self.errors))),
                "pack registry %s: %d pack(s) failed validation and were "
                "isolated (see registry.errors): %s",
                self.packs_dir, len(self.errors), ", ".join(sorted(self.errors)),
            )
        # #2781: an unresolved cross-pack reference must never be silent — but
        # it must not take the pack down either. Warn-once per (dir, message
        # set) so a repeated load of the same degraded dir does not spam.
        if self.unresolved_refs:
            _warn_packs_once(
                ("unresolved-refs", str(self.packs_dir),
                 tuple(sorted((ns, tuple(msgs))
                              for ns, msgs in self.unresolved_refs.items()))),
                "pack registry %s: %d pack(s) loaded with UNRESOLVED cross-pack "
                "references — the packs stay loaded; install the named pack(s) "
                "or declare them in depends_on (see registry.unresolved_refs): "
                "%s",
                self.packs_dir, len(self.unresolved_refs),
                "; ".join(
                    f"{ns}: {msgs[0]}"
                    for ns, msgs in sorted(self.unresolved_refs.items())),
            )
        return count

    @property
    def status_fold_index(self) -> StatusFoldIndex:
        """#2729: the compiled pack-declared ``event kind → status`` surface.

        Lazily built from the loaded (R-16-filtered) packs and cached on the
        registry; ``load_all`` resets the cache. Consumed by the projection's
        Object-status fold (``projection/entities.py::_fold_object_status``) —
        the ONE reader, so live writes and rebuild replay compile the same
        index from the same pack set.
        """
        if self._status_fold_index is None:
            self._status_fold_index = build_status_fold_index(self.packs)
        return self._status_fold_index

    def _load_one(self, path: Path) -> PackManifest:
        raw = yaml.safe_load(path.read_text()) or {}
        errors = self._validate(raw)
        if errors:
            raise ValueError(f"Invalid manifest: {'; '.join(errors)}")

        ont = raw.get("ontology", {})
        return PackManifest(
            namespace=raw["namespace"],
            name=raw["name"],
            version=raw.get("version", "0.1.0"),
            tier=raw.get("tier", "free"),
            description=raw.get("description", ""),
            path=path.parent,
            object_kinds=ont.get("objectKinds", []),
            event_kinds=ont.get("eventKinds", []),
            point_kinds=ont.get("pointKinds", []),
            document_kinds=ont.get("documentKinds", []),
            action_kinds=ont.get("actionKinds", []),
            relations=ont.get("relations", []),
            kind_defs=ont.get("kindDefs", {}),
            chains=ont.get("chains", []),
            hierarchies=ont.get("hierarchies", []),
            extraction=self._normalize_extraction(raw.get("extraction")),
            kind_subclasses=ont.get("subclassOf", {}),
            kind_equivalences=ont.get("equivalentTo", {}),
            connectors=raw.get("connectors", []),
            tools=raw.get("tools", []),
            depends_on=raw.get("depends_on", []),
        )

    @staticmethod
    def _normalize_extraction(ext: Any) -> dict:
        """Normalize the extraction section to its default shape (R6 §3.4).

        Absent in v2 manifests → active: true, no sourceTypes, warn default.
        """
        ext = ext if isinstance(ext, dict) else {}
        enforcement = _as_map(ext.get("enforcement"))
        return {
            "active": ext.get("active", True),
            "sourceTypes": _as_list(ext.get("sourceTypes")),
            "enforcement": {
                "default": enforcement.get("default", "warn"),
                "kinds": _as_map(enforcement.get("kinds")),
                "relations": _as_map(enforcement.get("relations")),
                "chains": _as_map(enforcement.get("chains")),
            },
            # v3.1 slots (#1026 §1.4). Empty when absent, so a v3 manifest
            # normalizes to the documented shape unchanged (additive, backward
            # compatible).
            "entityCues": {
                kind: _as_list(cues)
                for kind, cues in _as_map(ext.get("entityCues")).items()
            },
            "relationTemplates": _as_list(ext.get("relationTemplates")),
            "valueGate": _as_map(ext.get("valueGate")),
            "promptFragments": _as_list(ext.get("promptFragments")),
        }

    # ── Validate ───────────────────────────────────────────────────────

    def _validate(self, raw: dict) -> list[str]:
        errors: list[str] = []

        # Required top-level fields
        if not raw.get("namespace"):
            errors.append("missing required field: namespace")
        if not raw.get("name"):
            errors.append("missing required field: name")

        ns = raw.get("namespace", "")

        # Namespace format: no colons (would corrupt kind prefixing) and no
        # whitespace — `_validate` composes the kind form as `f"{ns}:{kind}"`,
        # so a space in the namespace produces exactly the whitespace-bearing
        # kind form the #2786 shape check refuses on the kind side. The hosted
        # path already rejects it (`pack_manifest_store._safe_namespace`); the
        # filesystem/`TORTOISE_PACKS_DIR` path did not.
        if ns and ":" in ns:
            errors.append("namespace must not contain ':'")
        if ns and any(ch.isspace() for ch in ns):
            errors.append("namespace must not contain whitespace")

        ont = raw.get("ontology", {})

        # Collect all pack-declared kinds (used by relation + subclassOf +
        # kindDefs + extraction validation). Empty when ontology is absent.
        all_pack_kinds: set[str] = set()
        seen_chain_ids: set[str] = set()

        # Validate ontology section
        if ont:
            # extends: core is a marker — no actual 'core' manifest file exists.
            # Kind declarations are validated against every canonical category
            # (``canonical_kind_axes``) and the subclassOf checks below.
            if ont.get("extends") != "core":
                errors.append("ontology.extends must be 'core'")

            for kind_field in KIND_FIELDS:
                kinds = ont.get(kind_field, [])
                if not isinstance(kinds, list):
                    errors.append(f"ontology.{kind_field} must be a list")
                    continue
                for k in kinds:
                    if not isinstance(k, str) or not k:
                        errors.append(f"ontology.{kind_field}: invalid kind value: {k}")
                        continue
                    if k[0].isupper():
                        errors.append(
                            f"ontology.{kind_field}: '{k}' should be camelCase "
                            f"(lowercase first letter)"
                        )
                    # A declared kind is a BARE identifier: the registry prefixes it
                    # with the namespace (`f"{ns}:{k}"`), so a `:` double-prefixes, and
                    # whitespace/prose makes a name that cannot match a canonical word
                    # or a Cypher literal. Both spellings slipped past the collision
                    # check below — `" deployment"` and `"dev:deployment"` validated
                    # clean while carrying the #7848 deformation — so the shape check
                    # runs BEFORE the owner check, not instead of it.
                    elif any(ch.isspace() for ch in k) or ":" in k:
                        errors.append(
                            f"ontology.{kind_field}: '{k}' must be a bare kind name — "
                            f"no whitespace and no ':' (the registry prefixes the "
                            f"manifest's namespace)"
                        )
                    # D10 (#5013, ONTOLOGY v3.15, #5022) moved `document` from
                    # the object-kind axis to the SOURCE-kind axis (a document is
                    # a `:Source`), so the canonical-collision check must read that
                    # vocabulary too. Without it a pack could re-register
                    # `objectKinds: [document]`, and `pack.object_kinds` feeds
                    # extractor_v2's writable kind forms and the classification
                    # index — i.e. the retired kind would be writable again.
                    #
                    # #2786 (the durable half of #7848): the check reads EVERY
                    # category, not just the one being declared. Reading only the
                    # same axis let the shipped `dev` pack declare `deployment` —
                    # an ONTOLOGY §5 **eventKind** — in `objectKinds` and validate
                    # clean, which is why #7848 had to fix the declaration by hand
                    # with nothing to refuse the next one. `pack.object_kinds` is
                    # not inert (it feeds extractor_v2's writable kind forms and
                    # the classification index), and `_build_kind_expansions` keys
                    # expansion on the bare literal — so a deformed name is
                    # writable, and expandable, under the wrong axis.
                    else:
                        owners = canonical_kind_axes(k)
                        if owners:
                            errors.append(
                                f"ontology.{kind_field}: '{k}' is already in "
                                f"canonical vocabulary — no need to register "
                                f"(core owns it as {' and '.join(owners)})"
                            )

            # #2786: one bare kind name belongs to exactly ONE category inside a
            # pack. Two declarations claim two meanings for one word, and
            # `store_as()` resolves the collision by silent precedence (point
            # before event/document/object), so for the point-vs-rest pairs the
            # writer mints a :Point for a kind the same manifest declared an
            # :Object — a choice the manifest author never made. The rule is
            # per-manifest on purpose: the expansion table is keyed
            # `f"{ns}:{kind}"`, so the same bare name in the SAME category across
            # two packs collapses to one key and stays legal (§7 `equivalentTo`
            # exists for exactly that).
            category_of: dict[str, list[str]] = {}
            for kind_field in KIND_FIELDS:
                declared = ont.get(kind_field, [])
                if not isinstance(declared, list):
                    continue  # already reported above
                for k in declared:
                    if isinstance(k, str) and k:
                        category_of.setdefault(k, []).append(kind_field)
            for k, fields in category_of.items():
                # len(SET), not len(list): a kind listed twice in ONE axis is a
                # legal (if redundant) declaration — every consumer dedupes through
                # a set — and reporting it as "more than one category" would name
                # one category in the message.
                if len(set(fields)) > 1:
                    errors.append(
                        f"kind '{k}' is declared in more than one category "
                        f"({', '.join(sorted(set(fields)))}) — one bare name has "
                        f"one category (#2786)"
                    )

            # Collect all pack-declared kinds (used by relation, subclassOf,
            # kindDefs, and extraction validation)
            for kf in KIND_FIELDS:
                all_pack_kinds.update(ont.get(kf, []))

            # Pre-built core kind set for relation validation
            ALL_CORE_KINDS = CORE_KINDS

            # Validate relations (new format: fromKind/toKind/mechanism)
            for rel in ont.get("relations", []):
                pred = rel.get("predicate", "")
                if not pred or not pred[0].islower():
                    errors.append(f"relation predicate '{pred}' must be camelCase")

                # New format: fromKind/toKind (specific pack kinds)
                if "fromKind" in rel or "toKind" in rel:
                    if "fromKind" not in rel or "toKind" not in rel:
                        errors.append(
                            f"relation '{pred}': both fromKind and toKind required"
                        )
                    mechanism = rel.get("mechanism", "")
                    if mechanism not in ("IMPL", "NAND"):
                        errors.append(
                            f"relation '{pred}': mechanism must be IMPL or NAND, "
                            f"got {mechanism!r}"
                        )
                    # Referential integrity: fromKind/toKind must reference
                    # existing kinds (core CANONICAL or this pack's own kinds).
                    # Cross-pack refs validated in load_all() after all packs loaded.
                    for direction, kind_val in [("fromKind", rel.get("fromKind", "")),
                                                 ("toKind", rel.get("toKind", ""))]:
                        if not kind_val:
                            continue  # already caught above
                        if kind_val[0].isupper():
                            # Core PascalCase kind — must be in canonical vocab
                            if kind_val not in ALL_CORE_KINDS:
                                errors.append(
                                    f"relation '{pred}': {direction} '{kind_val}' is not "
                                    f"a known core kind"
                                )
                        elif ":" in kind_val:
                            # Prefixed kind: namespace:localKind
                            ref_ns, ref_kind = kind_val.split(":", 1)
                            if not ref_ns or not ref_kind:
                                errors.append(
                                    f"relation '{pred}': {direction} '{kind_val}' "
                                    f"has empty namespace or kind"
                                )
                            elif ref_ns == ns:  # noqa: SIM102
                                # Self-reference — must be in this pack's kinds
                                if ref_kind not in all_pack_kinds:
                                    errors.append(
                                        f"relation '{pred}': {direction} '{kind_val}' "
                                        f"references '{ref_kind}' which is not "
                                        f"declared in this pack"
                                    )
                            # Cross-pack refs (ref_ns != ns): validate in load_all()
                        else:
                            # Unprefixed camelCase — not valid in new format
                            errors.append(
                                f"relation '{pred}': {direction} '{kind_val}' "
                                f"must be a core PascalCase kind or 'namespace:kind'"
                            )
                else:
                    # Old format: from/to (entity types) — backward compat
                    if rel.get("from") not in VALID_ENTITY_TYPES:
                        errors.append(
                            f"relation '{pred}': invalid 'from' type: {rel.get('from')}"
                        )
                    if rel.get("to") not in VALID_ENTITY_TYPES:
                        errors.append(
                            f"relation '{pred}': invalid 'to' type: {rel.get('to')}"
                        )

                if rel.get("cardinality") and rel["cardinality"] not in VALID_CARDINALITIES:
                    errors.append(
                        f"relation '{pred}': invalid cardinality: {rel.get('cardinality')}"
                    )

                # Manifest v3 (R6 §3.3): extractable must be a boolean when present
                if "extractable" in rel and not isinstance(rel["extractable"], bool):
                    errors.append(
                        f"relation '{pred}': 'extractable' must be a boolean"
                    )
                # #2766: the explicit declaration-only opt-in (see
                # `relation_write_verdict`). Shape only here; the effect on the
                # verdict is decided by the ONE resolver below.
                if "declarationOnly" in rel and not isinstance(rel["declarationOnly"], bool):
                    errors.append(
                        f"relation '{pred}': 'declarationOnly' must be a boolean"
                    )

                # #11083 — the two fields the census READS to state a row's
                # disposition. Both are VOCABULARY-shaped, so both are
                # warn-class (`_warn_packs_once`), never `errors`:
                # `_load_one` raises on any `_validate` error and `load_all` then
                # DELETES the pack, so an `errors` arm here would drop a whole
                # pack's kinds over one direction typo. The distinction is
                # deliberate and worth stating in the code: **shape errors drop
                # the pack; vocabulary-shaped fields warn.**
                #
                # `direction`: the per-edge sense ruling (c) makes load-bearing
                # (`ownedBy`'s target is open, so the direction says which end
                # owns which). A typo must NOT read as "no direction" — that is
                # a plausible wrong sign in the census — so it is named.
                # ⛔ The test is `canonical_direction`, not membership in the four
                # model values: the write path also accepts the two legacy
                # aliases, and warning those off would be false (the writer
                # honours them). Only junk returns `None` here — and junk is NOT
                # silently replaced by the meaning: `is_default_direction`
                # refuses to treat a typo as absent, so `create_operator` never
                # overwrites it and then REFUSES the write. The message says that,
                # because "ignored" was a claim the writer does not honour.
                if "direction" in rel:
                    direction_value = rel.get("direction")
                    if canonical_direction(direction_value) is None:
                        _warn_packs_once(
                            ("relation-direction-invalid", ns, pred),
                            "relation '%s': 'direction' %r is not a recognised "
                            "direction token (%s) — the write path REFUSES it "
                            "rather than resolving it from the declared "
                            "semantics, so this relation has no resolvable "
                            "sense (#11083)",
                            pred, direction_value,
                            ", ".join(sorted(_ACCEPTED_DIRECTION_VALUES)),
                        )

                # `declarationOnlyReason`: `declarationOnly: true` is an AUTHOR
                # OPT-IN that silences a real gap, so the reason must exist and
                # be printable by the census — otherwise the marker is a silencer
                # (#11083 R7).
                if "declarationOnlyReason" in rel:
                    reason = rel.get("declarationOnlyReason")
                    if not isinstance(reason, str) or not reason.strip():
                        _warn_packs_once(
                            ("relation-declaration-only-reason", ns, pred),
                            "relation '%s': 'declarationOnlyReason' must be a "
                            "non-empty string — it is what `tortoise pack "
                            "validate` and the #11083 census print to explain "
                            "why the declaration is unwritable",
                            pred,
                        )
                    elif rel.get("declarationOnly") is not True:
                        _warn_packs_once(
                            ("relation-declaration-only-reason-orphan", ns, pred),
                            "relation '%s': 'declarationOnlyReason' is recorded "
                            "but 'declarationOnly' is not true — the reason "
                            "explains a disposition the row does not claim",
                            pred,
                        )

                # Layer 2 (#7852, W2): the semantics vocabulary is CLOSED and
                # every declared value is checked against it —**warn-not-block**,
                # the contract W2 states and the repo's own precedent for an
                # open-vocabulary path (#1934). An out-of-vocabulary or retired
                # word, and a missing meaning, are warned WITH the valid
                # alternatives; none of them drops the pack (a refusal would
                # drop every pack that shares its kinds). The only hard error is
                # a shape error — a non-string value, which no vocabulary can
                # express.
                if "semantics" in rel:
                    semantics = rel.get("semantics")
                    if not isinstance(semantics, str) or not semantics:
                        errors.append(
                            f"relation '{pred}': 'semantics' must be a non-empty "
                            f"string"
                        )
                    elif semantics in SEMANTICS_TRANSITION:
                        _warn_packs_once(
                            ("relation-semantics-transition", ns, semantics),
                            "relation '%s': semantics %r is retired in favour of "
                            "%r (ONTOLOGY §8, #7852) — packs align to the core "
                            "vocabulary (#7848)",
                            pred, semantics, SEMANTICS_TRANSITION[semantics],
                        )
                    elif semantics not in SEMANTICS_VALUES:
                        # An OUT-OF-VOCABULARY word is flagged, never refused.
                        # W2's contract is warn-not-block (the repo's own
                        # precedent for an open-vocabulary write path, #1934):
                        # a refusal here would drop the pack — and the packs
                        # that share its kinds — on a single typo.
                        _warn_packs_once(
                            ("relation-semantics-unknown", ns, semantics),
                            "relation '%s': semantics %r is not in the core "
                            "vocabulary (ONTOLOGY §8, #7852) — valid values: %s",
                            pred, semantics, ", ".join(sorted(SEMANTICS_VALUES)),
                        )
                else:
                    # A missing meaning is flagged, never refused, in a
                    # transitional pack: completeness of the vocabulary is the
                    # target and a pack mid-alignment must keep loading.
                    _warn_packs_once(
                        ("relation-semantics-missing", ns, pred),
                        "relation '%s' declares no semantics — the Layer-2 "
                        "meaning (ONTOLOGY §8, #7852); valid values: %s",
                        pred, ", ".join(sorted(SEMANTICS_VALUES)),
                    )

                # #2766: the write-path verdict, emitted at pack load for EVERY
                # declared relation. A relation the runtime cannot write is no
                # longer silent — it is either declared `declarationOnly` or
                # reported here. Warn-not-block (W2): the pack still loads, and
                # `tortoise pack validate` prints the same verdict.
                write_verdict, write_paths = relation_write_verdict(rel)
                if write_verdict == RELATION_PREDICATE_NOT_IN_ALLOWLIST:
                    _warn_packs_once(
                        ("relation-not-writable", ns, pred),
                        "relation '%s': the declaration binds no write path — "
                        "'%s' is neither a structural edge predicate "
                        "(create_edge refuses it) nor carried by a declared "
                        "mechanism, so create_operator has no declared op_type "
                        "for it. This is a declaration gap, not a runtime "
                        "refusal: create_operator(op_type=..., label='%s') still "
                        "accepts the bare label (warn-not-block, #1934). Declare "
                        "`mechanism: IMPL|NAND`, use a structural predicate, or "
                        "mark it `declarationOnly: true` (#2766)",
                        pred, pred, pred,
                    )
                elif write_verdict == RELATION_WRITABLE \
                        and rel.get("declarationOnly") is True:
                    # A marker that cannot take effect is the silent divergence
                    # this verdict exists to remove — the runtime WILL write this
                    # relation, so say so rather than let the field lie.
                    _warn_packs_once(
                        ("relation-declaration-only-ignored", ns, pred),
                        "relation '%s': `declarationOnly: true` has no effect — "
                        "the runtime CAN write this relation via %s (#2766)",
                        pred, " + ".join(write_paths),
                    )

                # #7852: `nearMisses` names the sibling predicates/semantics a
                # relation is confused with, mirroring kindDefs (R6
                # §3.1/§3.5). SHAPE is checked here; RESOLUTION is cross-pack,
                # after every pack is loaded.
                if "nearMisses" in rel:
                    nms = rel.get("nearMisses")
                    if (not isinstance(nms, list)
                            or any(not isinstance(n, str) or not n for n in nms)):
                        errors.append(
                            f"relation '{pred}': 'nearMisses' must be a list of "
                            f"non-empty strings"
                        )

            # Validate hierarchies
            hierarchies = ont.get("hierarchies", [])
            if not isinstance(hierarchies, list):
                errors.append("ontology.hierarchies must be a list")
            else:
                for h in hierarchies:
                    if not isinstance(h, dict):
                        errors.append(
                            f"hierarchies: expected dict, got {type(h).__name__}"
                        )
                        continue
                    if not h.get("path"):
                        errors.append("hierarchies: entry missing 'path' field")
                    if not h.get("type"):
                        errors.append("hierarchies: entry missing 'type' field")

            # ── Manifest v3 (R6 §3.1/§3.5): kindDefs validation ──────────
            kind_defs = ont.get("kindDefs", {})
            if not isinstance(kind_defs, dict):
                errors.append("ontology.kindDefs must be a map keyed by kind name")
            else:
                for kind, spec in kind_defs.items():
                    if kind not in all_pack_kinds and kind not in CORE_KINDS:
                        errors.append(
                            f"kindDefs: '{kind}' is not declared in this pack's "
                            f"*Kinds lists (nor a core kind)"
                        )
                    if not isinstance(spec, dict):
                        errors.append(f"kindDefs: value for '{kind}' must be a map")
                        continue
                    for key in spec:
                        if key not in VALID_KINDDEF_KEYS:
                            errors.append(
                                f"kindDefs '{kind}': unknown key '{key}' (allowed: "
                                f"{', '.join(sorted(VALID_KINDDEF_KEYS))})"
                            )
                    if "description" in spec and not isinstance(spec["description"], str):
                        errors.append(
                            f"kindDefs '{kind}': 'description' must be a string "
                            f"(it is prompt material for the extractor, R6 §3.1)"
                        )
                    for list_field in ("synonyms", "examples", "nearMisses"):
                        if list_field in spec:
                            val = spec[list_field]
                            if (not isinstance(val, list)
                                    or not all(isinstance(x, str) and x for x in val)):
                                errors.append(
                                    f"kindDefs '{kind}': '{list_field}' must be a "
                                    f"list of non-empty strings"
                                )
                    if "extractable" in spec and not isinstance(spec["extractable"], bool):
                        errors.append(
                            f"kindDefs '{kind}': 'extractable' must be a boolean"
                        )
                    if ("storeAs" in spec
                            and spec["storeAs"] not in VALID_STORE_AS):
                        errors.append(
                            f"kindDefs '{kind}': 'storeAs' must be one of "
                            f"{', '.join(sorted(VALID_STORE_AS))}, "
                            f"got {spec['storeAs']!r}"
                        )
                    if ("enforcement" in spec
                            and spec["enforcement"] not in VALID_ENFORCEMENT_LEVELS):
                        errors.append(
                            f"kindDefs '{kind}': 'enforcement' must be one of "
                            f"{', '.join(sorted(VALID_ENFORCEMENT_LEVELS))}, "
                            f"got {spec['enforcement']!r}"
                        )
                    # #2792: a declared stage pipeline is validated by the
                    # pipeline module (canonical slots, terminal outcomes,
                    # unique ids, a default that exists). Shape only — the
                    # per-graph override layer reads it at runtime.
                    if "pipeline" in spec:
                        from tortoise.pipeline import validate_pipeline_decl
                        for perr in validate_pipeline_decl(spec["pipeline"]):
                            errors.append(f"kindDefs '{kind}': {perr}")
                    # #2818: the per-kind VALUE FIELDS declaration. Shape and
                    # closed vocabulary live in ``pack_values`` (one home) so
                    # the validator, the brief compiler and the prompt
                    # renderers cannot drift. A malformed declaration is an
                    # ordinary validation error here — never a dropped pack or
                    # a TypeError on the way to the prompt.
                    if "values" in spec:
                        from tortoise.pack_values import validate_value_fields
                        errors.extend(validate_value_fields(kind, spec))
                    # #2729: the per-kind lifecycle declaration. Errors isolate
                    # the pack (unknown fold status / orphan fold event /
                    # malformed shape); the shared-vocabulary overlap is a
                    # warning stashed in `_pending_lifecycle_warnings` and
                    # published by `load_all` into `lifecycle_warnings` (the
                    # PackManifest does not exist yet at this point).
                    if "statuses" in spec or "folds" in spec:
                        # Filter to the string entries the ontology loop above
                        # has already validated: an unhashable entry here
                        # (``eventKinds: [[1]]``) would make ``set()`` raise
                        # ``TypeError`` out of ``_validate``, whose contract is
                        # to RETURN its error list. The invalid value is
                        # already recorded as an ``ontology.<field>`` error by
                        # the loop at the top of this section.
                        _decl_errors, _decl_warnings = validate_status_decl(
                            kind, spec,
                            namespace=ns,
                            declared_event_kinds={
                                k for k in ont.get("eventKinds", [])
                                if isinstance(k, str) and k},
                            declared_object_kinds={
                                k for k in ont.get("objectKinds", [])
                                if isinstance(k, str) and k},
                        )
                        errors.extend(_decl_errors)
                        if _decl_warnings:
                            self._pending_lifecycle_warnings.setdefault(
                                ns, []).extend(_decl_warnings)
                    # nearMisses targets resolve post-load (cross-pack pass)

            # ── Manifest v3 (R6 §3.2/§3.5): chains validation ────────────
            chains = ont.get("chains", [])
            if not isinstance(chains, list):
                errors.append("ontology.chains must be a list")
            else:
                for i, chain in enumerate(chains):
                    if not isinstance(chain, dict):
                        errors.append(f"chains[{i}]: expected a map, got {type(chain).__name__}")
                        continue
                    cid = chain.get("id", "")
                    if not isinstance(cid, str) or not cid:
                        errors.append(f"chains[{i}]: missing required 'id'")
                    elif cid in seen_chain_ids:
                        errors.append(f"chains: duplicate id '{cid}'")
                    else:
                        seen_chain_ids.add(cid)
                    steps = chain.get("steps", [])
                    if (not isinstance(steps, list) or not steps
                            or not all(isinstance(s, str) and s for s in steps)):
                        errors.append(
                            f"chains: '{cid or i}': 'steps' must be a non-empty "
                            f"list of kind names"
                        )
                    if ("enforcement" in chain
                            and chain["enforcement"] not in VALID_ENFORCEMENT_LEVELS):
                        errors.append(
                            f"chains: '{cid or i}': 'enforcement' must be one of "
                            f"{', '.join(sorted(VALID_ENFORCEMENT_LEVELS))}"
                        )
                    if "edges" in chain:
                        edges = chain["edges"]
                        if not isinstance(edges, list) or not all(
                                isinstance(e, str) and e for e in edges):
                            errors.append(
                                f"chains: '{cid or i}': 'edges' must be a list of "
                                f"predicate names"
                            )
                        else:
                            declared_preds = {
                                r.get("predicate") for r in ont.get("relations", [])
                            }
                            for edge in edges:
                                if edge not in declared_preds and edge not in CORE_PREDICATES:
                                    errors.append(
                                        f"chains: '{cid or i}': edge '{edge}' is not a "
                                        f"declared relation predicate nor a core "
                                        f"predicate ({', '.join(sorted(CORE_PREDICATES))})"
                                    )
                    # steps resolve post-load (cross-pack pass)

        # ── Manifest v3 (R6 §3.4/§3.5): extraction validation (top-level) ──
        extraction = raw.get("extraction")
        if extraction is not None and not isinstance(extraction, dict):
            errors.append("extraction must be a map")
            # Leave `extraction` a map for the sections below: the shape error is
            # already recorded, and the slot checks that follow cannot introspect
            # a non-map — they would raise instead of reporting.
            extraction = {}
        else:
            extraction = extraction or {}
            if "active" in extraction and not isinstance(extraction["active"], bool):
                errors.append("extraction.active must be a boolean")
            source_types = extraction.get("sourceTypes", [])
            if not isinstance(source_types, list) or not all(
                    isinstance(s, str) and s for s in source_types):
                errors.append("extraction.sourceTypes must be a list of strings")
            else:
                known_source_types = registered_source_types()
                for st in source_types:
                    if st not in known_source_types and st not in SOURCE_TYPE_ESCAPE_HATCH:
                        errors.append(
                            f"extraction.sourceTypes: '{st}' is not a known source "
                            f"type (known: {', '.join(sorted(known_source_types))}; "
                            f"escape hatch: {', '.join(sorted(SOURCE_TYPE_ESCAPE_HATCH))})"
                        )
            enforcement = extraction.get("enforcement", {})
            if not isinstance(enforcement, dict):
                errors.append("extraction.enforcement must be a map")
            else:
                for key in enforcement:
                    if key not in ("default", "kinds", "relations", "chains"):
                        errors.append(
                            f"extraction.enforcement: unknown key '{key}' "
                            f"(allowed: default, kinds, relations, chains)"
                        )
                if ("default" in enforcement
                        and enforcement["default"] not in VALID_ENFORCEMENT_LEVELS):
                    errors.append(
                        f"extraction.enforcement.default must be one of "
                        f"{', '.join(sorted(VALID_ENFORCEMENT_LEVELS))}, "
                        f"got {enforcement['default']!r}"
                    )
                # Per-key overrides: level validated; keys typo-protected.
                declared_preds = {
                    r.get("predicate") for r in ont.get("relations", [])
                } if ont else set()
                for section, valid_keys, label in (
                        ("kinds", all_pack_kinds | CORE_KINDS, "kind"),
                        ("relations", declared_preds | CORE_PREDICATES, "predicate"),
                        ("chains", seen_chain_ids, "chain id"),
                ):
                    if section not in enforcement:
                        continue
                    mapping = enforcement[section]
                    if not isinstance(mapping, dict):
                        errors.append(
                            f"extraction.enforcement.{section} must be a map"
                        )
                        continue
                    for key, level in mapping.items():
                        if level not in VALID_ENFORCEMENT_LEVELS:
                            errors.append(
                                f"extraction.enforcement.{section}: level for "
                                f"'{key}' must be one of "
                                f"{', '.join(sorted(VALID_ENFORCEMENT_LEVELS))}, "
                                f"got {level!r}"
                            )
                        if key not in valid_keys:
                            errors.append(
                                f"extraction.enforcement.{section}: '{key}' is not "
                                f"a declared {label}"
                            )

        # ── Manifest v3.1 (R6 §1.4/§1.5, #1026): extraction behaviour slots ──
        # NOTE: this loop rejects EVERY key outside VALID_EXTRACTION_KEYS,
        # including the two named refusals. Before v3.1 the top-level
        # `extraction` map accepted anything, so a typo such as `sourceType`
        # (singular) parsed clean and silently did nothing.
        for key in extraction:
            if key in VALID_EXTRACTION_KEYS:
                continue
            if key in REFUSED_EXTRACTION_KEYS:
                errors.append(f"extraction.{key}: {REFUSED_EXTRACTION_KEYS[key]}")
            else:
                errors.append(
                    f"extraction: unknown key '{key}' (allowed: "
                    f"{', '.join(sorted(VALID_EXTRACTION_KEYS))})"
                )

        # entityCues: {kind: [surface cue, ...]} — the DECLARATIVE slot. The
        # key must be a WELL-FORMED kind reference; whether it RESOLVES is a
        # cross-pack question, answered in _validate_cross_pack_refs.
        entity_cues = extraction.get("entityCues")
        if entity_cues is not None and not isinstance(entity_cues, dict):
            errors.append(
                "extraction.entityCues must be a map of kind -> [cue, ...]")
        elif isinstance(entity_cues, dict):
            for kind, cues in entity_cues.items():
                shape = _kind_ref_shape_error(kind)
                if shape:
                    errors.append(f"extraction.entityCues: '{kind}' {shape}")
                if not isinstance(cues, list) or not all(
                        isinstance(c, str) and c.strip() for c in cues):
                    errors.append(
                        f"extraction.entityCues.{kind} must be a list of "
                        f"non-empty strings"
                    )

        # relationTemplates: pack-typical IMPL/NAND shapes. The mechanism
        # vocabulary is the pair `relations[].mechanism` already enforces.
        templates = extraction.get("relationTemplates")
        if templates is not None and not isinstance(templates, list):
            errors.append("extraction.relationTemplates must be a list")
        elif isinstance(templates, list):
            for i, tpl in enumerate(templates):
                if not isinstance(tpl, dict):
                    errors.append(
                        f"extraction.relationTemplates[{i}] must be a map")
                    continue
                for tkey in tpl:
                    if tkey not in ("predicate", "mechanism", "fromKind",
                                    "toKind", "description"):
                        errors.append(
                            f"extraction.relationTemplates[{i}]: unknown key "
                            f"'{tkey}' (allowed: predicate, mechanism, fromKind, "
                            f"toKind, description)"
                        )
                mechanism = tpl.get("mechanism")
                # IMPL|NAND — the pair this check hardcodes and
                # `relations[].mechanism` hardcodes. `MITIGATES` is RETIRED
                # (ONTOLOGY v3.17, #4937, the F1 ruling on #2552) and
                # `sdk.create_operator` refuses it, so accepting it here would
                # advertise, on the author-facing template, an edge the engine
                # cannot build. `CORE_PREDICATES` holds that same pair
                # (#2766 / #5322).
                # `predicate` is the one REFERENCE among these slots. The
                # sibling `ontology.relations` requires a non-empty camelCase
                # predicate and both kind sides, and a template naming none of
                # them describes nothing — but this field was checked by
                # NEITHER pass, so `{"predicate": 123}` and `{}` both
                # validated clean.
                template_pred = tpl.get("predicate")
                if not isinstance(template_pred, str) or not template_pred:
                    errors.append(
                        f"extraction.relationTemplates[{i}].predicate must be "
                        f"a non-empty string"
                    )
                elif not template_pred[0].islower():
                    errors.append(
                        f"extraction.relationTemplates[{i}].predicate "
                        f"'{template_pred}' must be camelCase"
                    )
                if ("fromKind" in tpl or "toKind" in tpl) and (
                    "fromKind" not in tpl or "toKind" not in tpl
                ):
                    errors.append(
                        f"extraction.relationTemplates[{i}]: both fromKind "
                        f"and toKind are required when either is given "
                        f"(a half-declared shape cannot be matched)"
                    )
                if mechanism is not None and mechanism not in ("IMPL", "NAND"):
                    errors.append(
                        f"extraction.relationTemplates[{i}].mechanism must be "
                        f"IMPL or NAND, got {mechanism!r}"
                    )
                for side in ("fromKind", "toKind"):
                    ref = tpl.get(side)
                    if ref is None:
                        continue
                    shape = _kind_ref_shape_error(ref)
                    if shape:
                        errors.append(
                            f"extraction.relationTemplates[{i}].{side}: "
                            f"'{ref}' {shape}"
                        )

        # valueGate: this pack's keep/drop HINTS for the value brief — what a
        # valuable utterance looks like in this domain. It is NOT a mint-time
        # drop rule: no hint can remove an artefact, only inform the
        # utterance-level value gate. CONSUMED (§ #4894):
        # ``compile_value_brief`` carries it into the brief's reserved
        # ``pack_hints`` section and extractor_v2 renders it into the S1/S2/S4
        # value-brief slot. It stays a hint — the mechanical, artefact-level
        # predicate is ``tortoise/value_gate.py``, and no hint becomes a rule.
        value_gate = extraction.get("valueGate")
        if value_gate is not None and not isinstance(value_gate, dict):
            errors.append("extraction.valueGate must be a map")
        elif isinstance(value_gate, dict):
            for gkey, gval in value_gate.items():
                if gkey not in VALID_VALUE_GATE_KEYS:
                    errors.append(
                        f"extraction.valueGate: unknown key '{gkey}' (allowed: "
                        f"{', '.join(sorted(VALID_VALUE_GATE_KEYS))})"
                    )
                if not isinstance(gval, list) or not all(
                        isinstance(g, str) and g.strip() for g in gval):
                    errors.append(
                        f"extraction.valueGate.{gkey} must be a list of "
                        f"non-empty strings"
                    )

        # promptFragments: pack-authored instruction snippets declared for the
        # value brief. CONSUMED (§ #4894) into the same S1/S2/S4 slot as the
        # `valueGate` hints. Capped on BOTH words and characters so a pack
        # cannot inflate every brief it activates — and cannot evade the word
        # cap by shipping one whitespace-free blob.
        fragments = extraction.get("promptFragments")
        if fragments is not None and not isinstance(fragments, list):
            errors.append("extraction.promptFragments must be a list")
        elif isinstance(fragments, list):
            total = 0
            total_chars = 0
            for i, frag in enumerate(fragments):
                if not isinstance(frag, str) or not frag.strip():
                    errors.append(
                        f"extraction.promptFragments[{i}] must be a non-empty "
                        f"string"
                    )
                    continue
                cost = prompt_token_proxy(frag)
                chars = len(frag)
                total += cost
                total_chars += chars
                if (cost > MAX_PROMPT_FRAGMENT_TOKENS
                        or chars > MAX_PROMPT_FRAGMENT_CHARS):
                    errors.append(
                        f"extraction.promptFragments[{i}] is ~{cost} words / "
                        f"{chars} chars (caps {MAX_PROMPT_FRAGMENT_TOKENS} "
                        f"words, {MAX_PROMPT_FRAGMENT_CHARS} chars)"
                    )
            if (total > MAX_PROMPT_FRAGMENTS_TOKENS
                    or total_chars > MAX_PROMPT_FRAGMENTS_CHARS):
                errors.append(
                    f"extraction.promptFragments total ~{total} words / "
                    f"{total_chars} chars (caps {MAX_PROMPT_FRAGMENTS_TOKENS} "
                    f"words, {MAX_PROMPT_FRAGMENTS_CHARS} chars)"
                )

        # Validate connectors
        for conn in raw.get("connectors", []):
            if not conn.get("source"):
                errors.append("connector missing 'source' field")
            if not conn.get("entrypoint"):
                errors.append(f"connector '{conn.get('source', '?')}' missing 'entrypoint'")

        # Validate tools
        for tool in raw.get("tools", []):
            if not tool.get("name"):
                errors.append("tool missing 'name' field")
            if not tool.get("entrypoint"):
                errors.append(f"tool '{tool.get('name', '?')}' missing 'entrypoint'")
            for pname, pspec in tool.get("params", {}).items():
                ptype = pspec.get("type", "") if isinstance(pspec, dict) else ""
                if ptype and ptype not in VALID_PARAM_TYPES:
                    errors.append(
                        f"tool '{tool.get('name', '?')}' param '{pname}': "
                        f"invalid type '{ptype}' (must be one of: "
                        f"{', '.join(sorted(VALID_PARAM_TYPES))})"
                    )

        # Validate tier
        tier = raw.get("tier", "free")
        if tier not in ("free", "premium"):
            errors.append(f"tier must be 'free' or 'premium', got: {tier}")

        # Validate subclassOf declarations
        subclass_of = ont.get("subclassOf", {})
        if not isinstance(subclass_of, dict):
            errors.append("ontology.subclassOf must be a dict")
        else:
            for child, parent in subclass_of.items():
                if child not in all_pack_kinds:
                    errors.append(
                        f"subclassOf: '{child}' is not declared in this pack's kinds"
                    )
                # Parent shape: PascalCase core entity types / named object
                # kinds (Subject…Source, Project, WorkItem, Problem) OR a
                # canonical lowercase object kind from ONTOLOGY §5 (tag,
                # strategy, plan, goal, target — #2727). The PascalCase
                # heuristic predates the commitment-state family and would
                # reject those valid parents; it is therefore scoped to refs
                # that are NOT canonical object kinds, so camelCase typos like
                # `workItem` still error (the membership check below is the
                # real typo guard).
                # The lowercase half of the vocabulary is the canonical
                # objectKind set (`tag`, `goal`, `target`, …). ⚠ `document` is
                # NOT in it and is NOT a valid parent: D10 (#5013, ONTOLOGY
                # v3.15 §4.4, #5022) retired `objectKind: document` — a document
                # is a `:Source`, so `Document` (the core entity type) is the
                # only document parent. This comment used to claim both were
                # valid parents and expanded separately; that stopped being true
                # at D10 and `tests/test_pack_kinds.py` pins the refusal (#11000
                # corrected the stale note while touching this pass).
                # The isinstance guard keeps a malformed manifest value (list,
                # int) on the shape-error path instead of raising TypeError out
                # of `_validate` (which surfaced as an opaque dropped-pack error).
                if (not isinstance(parent, str) or not parent
                        or (not parent[0].isupper()
                            and parent not in CANONICAL_OBJECT_KINDS)):
                    errors.append(
                        f"subclassOf: parent kind '{parent}' must be PascalCase "
                        f"or a canonical lowercase object kind "
                        f"(e.g. {', '.join(sorted(CANONICAL_OBJECT_KINDS))})"
                    )
                # Check parent exists in core object kinds or core concepts.
                # isinstance guard: a non-string parent already failed the shape
                # check above — the frozenset membership test would otherwise
                # raise TypeError for an unhashable value (e.g. a list).
                core_parents = CANONICAL_OBJECT_KINDS | CORE_ENTITY_TYPES
                if isinstance(parent, str) and parent not in core_parents:
                    errors.append(
                        f"subclassOf: parent kind '{parent}' not found in core "
                        f"ontology. Must be a core entity type or core objectKind."
                    )

        # Validate equivalentTo declarations
        equiv_to = ont.get("equivalentTo", {})
        if not isinstance(equiv_to, dict):
            errors.append("ontology.equivalentTo must be a dict")
        else:
            for kind, targets in equiv_to.items():
                if kind not in all_pack_kinds:
                    errors.append(
                        f"equivalentTo: '{kind}' is not declared in this pack's kinds"
                    )
                if not isinstance(targets, list):
                    errors.append(
                        f"equivalentTo: value for '{kind}' must be a list of "
                        f"'namespace:kind' strings"
                    )
                    continue
                for target in targets:
                    if ":" not in target:
                        errors.append(
                            f"equivalentTo: '{target}' must use 'namespace:kind' format"
                        )

        # depends_on (#2781): the DECLARED pack-level dependency list. Consumed
        # by load_all (an absent declared dep is reported against the declaring
        # pack and — per #2781 — explains its unresolved references) and by the
        # hosted install validator (which refuses the install). SHAPE is checked
        # here so a malformed value is a schema error instead of a crash or a
        # phantom dependency in the consumer: `depends_on: pm` (a bare string)
        # iterates as characters and would report a dependency on `p`.
        depends_on = raw.get("depends_on") or []
        if not isinstance(depends_on, list) or not all(
                isinstance(d, str) and d.strip() for d in depends_on):
            errors.append(
                "depends_on must be a list of non-empty pack namespaces"
            )

        return errors

    # ── Cross-pack validation ──────────────────────────────────────────

    def _validate_cross_pack_refs(self) -> None:
        """Validate that all kind references resolve across packs.

        Called after load_all() populates all packs. Checks:
          1. Every relation's fromKind/toKind values reference known kinds.
          2. Every equivalentTo target references a kind in another loaded pack.
          3. Every kindDefs nearMisses target resolves (R6 §3.5).
          4. Every chain step resolves — bare steps may be this pack's own
             kinds, core kinds, or kinds declared by exactly ONE other pack
             (ambiguous bare names are errors, R6 §3.2 / plan W-5 failure b).

        An UNRESOLVABLE reference lands in one of two buckets (#2781):

          * ``self.errors`` — a typo. The namespace resolves but the kind does
            not, or a bare name resolves nowhere and the declaring pack's own
            ``depends_on`` is fully satisfied. The pack is isolated.
          * ``self.unresolved_refs`` — a MISSING PACK. A namespaced ref whose
            namespace is not loaded, or any unresolvable ref whose declaring
            pack also has a ``depends_on`` entry that is not loaded. The pack
            stays loaded; the diagnostic names the missing pack.

        See ``_missing_pack_explanation`` for why those two clauses are
        evidence of absence rather than leniency.
        """
        # Rebuilt (never appended to): load_all re-runs this pass against the
        # post-isolation surviving set and unresolved keys keep their packs, so
        # an append would duplicate every message on the second pass.
        self.unresolved_refs = {}

        # Build the set of all known kinds (core + all packs)
        all_known = self._all_known_kinds()
        loaded = set(self.packs)

        # #2781: declared dependencies that are not loaded. This is BOTH a
        # report (below) and the evidence clause `_missing_pack_explanation`
        # uses to tell a MISSING PACK from a typo for a bare kind name.
        missing_deps: dict[str, list[str]] = {
            ns: [d for d in pack.depends_on if d not in loaded]
            for ns, pack in self.packs.items()
        }
        for ns, deps in missing_deps.items():
            for dep in deps:
                self.unresolved_refs.setdefault(ns, []).append(
                    f"depends_on: declared dependency '{dep}' is not loaded — "
                    f"install pack '{dep}' (the pack stays loaded; its "
                    f"references into '{dep}' are unresolved)"
                )

        # Bare-name index for single-namespace resolution (chain steps,
        # nearMisses): bare kind name → full 'ns:kind' forms.
        bare_index: dict[str, list[str]] = {}
        for full in all_known:
            if ":" in full:
                _ns, kind = full.split(":", 1)
                bare_index.setdefault(kind, []).append(full)

        # The relation nearMisses universe (#7852): a relation may be confused
        # with another predicate or with another semantics value, so both are
        # valid targets. Predicates are declared and matched BARE by contract
        # (#2030), so a target is bare too.
        all_predicates = set(CORE_PREDICATES) | set(SEMANTICS_VALUES)
        for _ns, _pack in self.packs.items():
            all_predicates.update(
                r.get("predicate") for r in _pack.relations if r.get("predicate")
            )

        # Check every relation's fromKind/toKind
        for ns, pack in self.packs.items():
            rel_pack_kinds = self._pack_kind_set(pack)
            for rel in pack.relations:
                pred = rel.get("predicate", "?")
                for direction in ("fromKind", "toKind"):
                    kind_val = rel.get(direction, "")
                    if not kind_val:
                        continue  # old-format, already validated
                    # #7848: resolve through the SAME helper chains/nearMisses
                    # use, so a `core:<kind>` reference (a core camelCase kind
                    # such as `core:deployment`) resolves here too. The old
                    # `kind_val not in all_known` test rejected it, because
                    # `_all_known_kinds()` stores core kinds BARE — which left a
                    # pack unable to point a relation at a core camelCase kind
                    # without re-declaring it (the deformation #7848 fixes).
                    # WIDENING only: every previously-valid ref still resolves.
                    # (No `ambiguous` branch: `_validate` rejects an unprefixed
                    # camelCase relation ref before this pass, and only a bare
                    # ref can be ambiguous.)
                    status = self._resolve_kind_ref(
                        kind_val, rel_pack_kinds, all_known, bare_index)
                    if status == "unknown":
                        self._report_unresolved(
                            ns, f"relation '{pred}': {direction} '{kind_val}'",
                            kind_val, loaded, missing_deps[ns])

                # #7852: every relation nearMisses target resolves to a
                # declared predicate or a semantics value — the same split
                # kindDefs uses (shape per-manifest, resolution here, because a
                # bare name is not decidable from one manifest alone).
                # #9663: resolution shares the ONE #2781 bucket decision
                # (`_record_unresolved`) the relation ENDPOINTS above use, rather
                # than writing straight to `self.errors`. A relation may
                # legitimately name another pack's PREDICATE as its near-miss
                # (`constrainedBy` vs venture's `gatedBy`), and a pack that
                # DECLARED that dependency must be reported as having a missing
                # peer — not isolated as if it had a typo, which is what an error
                # here does. Measured on a catalog with `venture` removed:
                # `gatedBy` went to `errors` and `unresolved_refs` stayed empty,
                # so the whole pack was dropped with no diagnostic naming the
                # pack to install.
                #
                # #9663: that gate must NOT swallow a NAMESPACED ref here. The
                # universe this ref resolves against is predicates + semantics
                # values, and those are declared BARE by contract (#2030) — the
                # engine emits and matches them bare. `venture:holds` therefore
                # matches nothing a bare-conforming catalog can supply, so
                # "install pack 'venture'" cannot make it resolve;
                # `bare_only=True` keeps it fatal. (The one way it does resolve:
                # `_validate` only checks a declared predicate's first
                # character, so a pack could itself declare
                # `predicate: venture:holds` — nothing in the shipped catalog
                # does, and rejecting it is a behaviour change filed
                # separately, #9760.)
                # A BARE target still takes the gate, because naming a sibling
                # pack's predicate is the documented, legitimate use of
                # `depends_on` (`constrainedBy` vs venture's `gatedBy`).
                for target in rel.get("nearMisses", []) or []:
                    if target in all_predicates:
                        continue
                    self._record_unresolved(
                        ns,
                        f"relation '{pred}': nearMisses target '{target}' does "
                        f"not resolve to any declared predicate or semantics "
                        f"value (core or loaded pack)",
                        target, loaded, missing_deps[ns], bare_only=True)

            # Check every equivalentTo target resolves
            for kind, targets in pack.kind_equivalences.items():
                for target in targets:
                    if target not in all_known:
                        self._report_unresolved(
                            ns, f"equivalentTo: target '{target}' (for '{kind}')",
                            target, loaded, missing_deps[ns])

            # Manifest v3.1 (#1026): resolve every kind reference the v3.1
            # extraction slots declare. SHAPE was checked per-manifest in
            # _validate; RESOLUTION needs every pack loaded, which is why it
            # happens here — the same split `relations` uses. Without this the
            # slots were the one place a ref was accepted and handed to nobody,
            # so a dangling `ns:kind` validated clean.
            v31_kinds = self._pack_kind_set(pack)
            v31_extraction = pack.extraction or {}
            v31_refs: list[tuple[str, str]] = [
                (f"extraction.entityCues: '{kind}'", kind)
                for kind in (v31_extraction.get("entityCues") or {})
            ]
            for idx, tpl in enumerate(
                    v31_extraction.get("relationTemplates") or []):
                if not isinstance(tpl, dict):
                    continue
                for side in ("fromKind", "toKind"):
                    ref = tpl.get(side)
                    if isinstance(ref, str) and ref.strip():
                        v31_refs.append(
                            (f"extraction.relationTemplates[{idx}].{side}", ref))
            for label, ref in v31_refs:
                status = self._resolve_kind_ref(
                    ref, v31_kinds, all_known, bare_index)
                if status == "unknown":
                    self._report_unresolved(
                        ns, label, ref, loaded, missing_deps[ns])
                elif status == "ambiguous":
                    self.errors.setdefault(ns, []).append(
                        f"{label} is ambiguous — declared by multiple packs; "
                        f"use 'namespace:kind'"
                    )

            # Manifest v3 (R6 §3.1/§3.5): every nearMisses target resolves
            pack_kinds = self._pack_kind_set(pack)
            for kind, kd in pack.kind_defs.items():
                for target in kd.get("nearMisses", []):
                    status = self._resolve_kind_ref(
                        target, pack_kinds, all_known, bare_index
                    )
                    if status == "unknown":
                        self._report_unresolved(
                            ns, f"kindDefs '{kind}': nearMisses target '{target}'",
                            target, loaded, missing_deps[ns])
                    elif status == "ambiguous":
                        msg = (
                            f"kindDefs '{kind}': nearMisses target '{target}' is "
                            f"ambiguous — declared by multiple packs; "
                            f"use 'namespace:kind'"
                        )
                        self.errors.setdefault(ns, []).append(msg)

            # Manifest v3 (R6 §3.2/§3.5): every chain step resolves
            for chain in pack.chains:
                cid = chain.get("id", "?")
                for step in chain.get("steps", []):
                    status = self._resolve_kind_ref(
                        step, pack_kinds, all_known, bare_index
                    )
                    if status == "unknown":
                        self._report_unresolved(
                            ns, f"chain '{cid}': step '{step}'", step,
                            loaded, missing_deps[ns])
                    elif status == "ambiguous":
                        msg = (
                            f"chain '{cid}': step '{step}' is ambiguous — "
                            f"declared by multiple packs; use 'namespace:kind'"
                        )
                        self.errors.setdefault(ns, []).append(msg)

    def _report_unresolved(
        self, ns: str, site: str, ref: str,
        loaded: set[str], missing_deps: list[str],
    ) -> None:
        """Record an unresolvable ``ref`` as FATAL or as an unresolved peer.

        ``site`` is the fully-qualified location prefix (e.g.
        ``"relation 'addresses': toKind 'pm:issue'"``); ``ref`` is the bare
        reference it carries. The FATAL half keeps the pre-#2781 message
        verbatim so existing diagnostics and their assertions are unchanged.
        """
        self._record_unresolved(
            ns, f"{site} does not resolve to any known kind (core or loaded pack)",
            ref, loaded, missing_deps)

    def _record_unresolved(
        self, ns: str, message: str, ref: str,
        loaded: set[str], missing_deps: list[str],
        bare_only: bool = False,
    ) -> None:
        """THE #2781 bucket decision, in one place (#9663 review).

        Every reference site (relation endpoints, `equivalentTo`, kindDefs and
        relation `nearMisses`, chain steps, v3.1 extraction slots) must answer
        the same question — is this reference a MISSING PEER (reported, pack
        survives) or a TYPO (fatal, pack isolated)? `_report_unresolved` used to
        be the only holder of that decision because it also owned the message;
        relation `nearMisses` needs a different message, so it re-implemented the
        branch — and a policy that lives in two places diverges the first time a
        third bucket is added, in the site nobody remembers. This helper owns the
        branch; callers own their wording.

        ``bare_only`` says the universe holds BARE names ONLY, so no namespaced
        spelling of ``ref`` exists at all and the gate must not explain it away
        (#9663). Predicates are exactly such a universe (#2030), so
        `venture:holds` in a relation `nearMisses` cannot be rescued by
        installing `venture`; downgrading it to a survivable gap would print
        advice that cannot make the reference resolve. It also matters to the
        VERDICT: `pack validate` reads `unresolved_refs` into its exit code
        (#9663), so a gap no installation can close would have reported a typo
        as an installable, fixable gap.
        """
        if bare_only and isinstance(ref, str) and ":" in ref:
            explanation = None  # namespaced in a bare universe ⇒ always a typo
        else:
            explanation = self._missing_pack_explanation(
                ref, loaded, missing_deps)
        if explanation is None:
            self.errors.setdefault(ns, []).append(message)
        else:
            self.unresolved_refs.setdefault(ns, []).append(
                f"{message} — {explanation}")

    @staticmethod
    def _missing_pack_explanation(
        ref: str, loaded: set[str], missing_deps: list[str],
    ) -> str | None:
        """Why an unresolvable ``ref`` is a MISSING PEER, not a typo (#2781).

        Returns the explanatory tail of the diagnostic, or ``None`` when the
        ref must stay a hard error (the pack is isolated). Two clauses, each
        carrying its own evidence:

          1. **A namespaced ref whose namespace is not loaded.** The namespace
             does not resolve at all, so the ref cannot be a misspelling
             *inside a present pack* — it names a pack that is simply not
             installed. (A namespaced ref into a PRESENT pack is excluded from
             this clause and every other: there the namespace resolves and only
             the kind can be wrong, which is a typo — `dev:isue` must stay
             fatal.)
          2. **The declaring pack has an unloaded ``depends_on`` entry.** A
             bare name carries no namespace, so its absence is not by itself
             evidence of anything; but a pack that DECLARED a dependency it
             does not have has already told us it expects a pack that is not
             there. That is the declaration doing work — the reason
             ``depends_on`` exists — and it is what turns a catalog-wide
             cascade into one reported, survivable gap.

        Neither clause weakens the typo guard where it can be exact: a bare
        name that resolves nowhere with a complete dependency set, and any
        ambiguous bare name, remain fatal (they go to ``errors``).
        """
        if not isinstance(ref, str):
            # Defence in depth: every caller's own SHAPE check rejects a
            # non-string reference before this pass, so this is unreachable
            # today — but `":" in ref` on a non-string would raise out of a
            # method whose contract is to RECORD a diagnostic. Treat it as
            # unexplained, which keeps it fatal.
            return None
        if ":" in ref:
            if ref.startswith("core:"):
                # A core ref never names a pack, so no declared dependency can
                # explain it away (the engine emits this spelling for core kinds
                # — see _resolve_kind_ref — and a bad kind after it is a typo).
                return None
            target_ns = ref.split(":", 1)[0]
            if target_ns in loaded:
                return None  # present namespace ⇒ only the kind can be wrong
            hint = ""
            close = difflib.get_close_matches(
                target_ns, sorted(loaded), n=1, cutoff=0.8)
            if close:
                hint = f" (did you mean '{close[0]}'?)"
            return (
                f"it references pack '{target_ns}', which is not loaded — "
                f"install pack '{target_ns}' or declare it in depends_on{hint}"
            )
        if missing_deps:
            names = ", ".join(f"'{d}'" for d in missing_deps)
            return (
                f"the pack declares depends_on {names}, which is not loaded — "
                f"install it if this name belongs to that pack"
            )
        return None

    @staticmethod
    def _pack_kind_set(pack: PackManifest) -> set[str]:
        """All kinds a pack declares (all five categories)."""
        kinds: set[str] = set()
        for kind_list in (pack.object_kinds, pack.event_kinds, pack.point_kinds,
                          pack.document_kinds, pack.action_kinds):
            kinds.update(kind_list)
        return kinds

    def _all_known_kinds(self) -> set[str]:
        """Core kinds + every loaded pack's prefixed kinds."""
        all_known: set[str] = set(CORE_KINDS)
        for pack in self.packs.values():
            ns = pack.namespace
            for kind in self._pack_kind_set(pack):
                all_known.add(f"{ns}:{kind}")
        return all_known

    def _resolve_kind_ref(self, ref: str, pack_kinds: set[str],
                          all_known: set[str],
                          bare_index: dict[str, list[str]]) -> str:
        """Resolve a kind reference to "ok" | "unknown" | "ambiguous".

        Namespaced refs must be in all_known. Bare refs resolve against: the
        pack's own kinds, core kinds, then kinds declared by exactly one
        other pack (single-namespace); more than one match → ambiguous.
        """
        if ":" in ref:
            if ref.startswith("core:"):
                # `compile_value_brief` emits core kinds as `core:<kind>`
                # (`core:Project`), and a pack author is most likely to paste a
                # kind straight out of it. Accept the engine's OWN spelling by
                # normalizing to the bare kind, rather than reporting an
                # unresolvable ref for it. WIDENING only: this form was an
                # error before, so no existing valid ref changes meaning.
                return "ok" if ref[len("core:"):] in CORE_KINDS else "unknown"
            return "ok" if ref in all_known else "unknown"
        if ref in pack_kinds or ref in CORE_KINDS:
            return "ok"
        fulls = bare_index.get(ref, [])
        if len(fulls) == 1:
            return "ok"
        if not fulls:
            return "unknown"
        return "ambiguous"

    # Keep backward-compatible alias
    _validate_cross_pack_relations = _validate_cross_pack_refs

    # ── Query ──────────────────────────────────────────────────────────

    def pack_summaries(self) -> dict[str, dict]:
        """Stable shared-catalog read: namespace → {name, version, tier, description}.

        #318 (multi-tenant pack isolation): per-tenant activation records
        (pack_state.PackInstall) join against this catalog — the shared
        catalog stays READ-ONLY for tenants; only install-state is
        per-tenant. Keys are manifest namespaces (the catalog's canonical
        id — note the project-management pack dir declares namespace `pm`).
        Read-at-call-time contract: no caching here; callers resolve the
        catalog when they need it.
        """
        return {
            p.namespace: {
                "name": p.name,
                "version": p.version,
                "tier": p.tier,
                "description": p.description,
            }
            for p in self.packs.values()
        }

    def list_packs(self) -> list[dict]:
        """Return all installed packs with metadata. No connector code executed."""
        return [
            {
                "namespace": p.namespace,
                "name": p.name,
                "version": p.version,
                "tier": p.tier,
                "description": p.description,
                "kind_counts": {
                    "objectKinds": len(p.object_kinds),
                    "eventKinds": len(p.event_kinds),
                    "pointKinds": len(p.point_kinds),
                    "relations": len(p.relations),
                },
                "connector_count": len(p.connectors),
                "tool_count": len(p.tools),
            }
            for p in self.packs.values()
        ]

    def list_all_kinds(self) -> dict[str, list[str]]:
        """Return all registered kinds across all packs, keyed by kind field."""
        result: dict[str, list[str]] = {
            "objectKinds": [], "eventKinds": [], "pointKinds": [],
            "documentKinds": [], "actionKinds": [],
        }
        for p in self.packs.values():
            ns = p.namespace
            result["objectKinds"].extend(f"{ns}:{k}" for k in p.object_kinds)
            result["eventKinds"].extend(f"{ns}:{k}" for k in p.event_kinds)
            result["pointKinds"].extend(f"{ns}:{k}" for k in p.point_kinds)
            result["documentKinds"].extend(f"{ns}:{k}" for k in p.document_kinds)
            result["actionKinds"].extend(f"{ns}:{k}" for k in p.action_kinds)
        return result

    def get_pack(self, namespace: str) -> PackManifest | None:
        """Get a pack by namespace."""
        return self.packs.get(namespace)

    def get_subclasses(self, parent_kind: str) -> list[str]:
        """Return every kind that is a subclass of parent_kind — CORE first, then pack.

        Example: get_subclasses("Project") → ["dev:epic", "venture:program"]
                 get_subclasses("Subject") → ["legalPerson", …, "organization"]

        #11000: the CORE half comes from ``CORE_SUBCLASSES`` (ONTOLOGY §6). This
        used to read ONLY pack-declared ``kind_subclasses``, so no core
        parentage was ever returned — ``get_subclasses("Subject") == []`` — and
        ``_build_kind_expansions`` (built from this method) could not widen a
        core root. Pack declarations are appended AFTER the core ones so the
        returned order is stable across loads.

        #11000: the argument is normalised to the BARE spelling, because
        ``expand_kind`` declares a core kind's bare and ``core:`` forms ONE kind
        (that is what makes the widening reach the rows the classifier writes,
        ``core:WorkItem``) — and this is the method that table is built from.
        Without the fold, ``get_subclasses("core:Subject")`` answered ``[]``
        while ``expand_kind("core:Subject")`` answered with the whole bucket: a
        wrong-empty for a caller that spells a kind the way `extractor_v2.SUBJECTS`
        does (``core:organization``, ``core:team``, …).
        """
        bare = parent_kind[5:] if parent_kind.startswith("core:") else parent_kind
        result = list(CORE_SUBCLASSES.get(bare, ()))
        for p in self.packs.values():
            ns = p.namespace
            for child, parent in p.kind_subclasses.items():
                if parent == bare:
                    result.append(f"{ns}:{child}")
        return result

    def expand_kind(self, kind: str) -> list[str]:
        """Expand a kind to its subclasses, superclasses, equivalents and spellings.

        Returns list of kind strings for Cypher IN clause.
        Example: expand_kind("WorkItem") → ["WorkItem", "dev:issue", "pm:issue", …]

        Four directions, all ADDITIVE:
          * **superclass** — a pack child expands to its declared `subclassOf`
            parent (`expand_kind("dev:issue")` carries `WorkItem`, #11000 Leg A);
          * **subclass** — a parent expands to its children, CORE (ONTOLOGY §6 via
            ``CORE_SUBCLASSES``) then pack (`expand_kind("Subject")` carries
            `organization`, `team`, …; #11000 Leg B);
          * **equivalent** — `equivalentTo`, bidirectionally (§7);
          * **spelling** — the bare and `core:` forms of a core kind are ONE kind
            (`WorkItem` ↔ `core:WorkItem`), because pack declarations spell it
            bare while the classifier's index and the open write doors spell it
            `core:`-prefixed and rows are stored either way.

        INVARIANT (#6146): the argument is ALWAYS in its own expansion —
        ``kind in expand_kind(kind)``. Expansion is additive: it widens a kind to
        its subclasses and equivalents, it never replaces the caller's kind with
        namespace forms. Read paths (``query``/``paginated_query`` build
        ``n.pointKind IN [...]`` from this list) must not turn a valid kind into
        an empty result set, because a wrong-empty is indistinguishable from a
        true-empty to the caller.

        Pre-computed at load_all() time, O(1) dict lookup at query time.
        """
        if not hasattr(self, '_kind_expansions'):
            self._build_kind_expansions()
        return self._kind_expansions.get(kind, [kind])

    def _build_kind_expansions(self) -> None:
        """Build the pre-computed kind expansion table.

        Called once after load_all(). Maps each kind to [self] + subclasses +
        bidirectional equivalents.
        """
        expansions: dict[str, list[str]] = {}

        # The two relations the closure below walks, kept apart on purpose:
        # `direct_parents` is walked UP only, `direct_children` DOWN only, and
        # `equivalences` symmetrically inside both. `None`-free because every
        # lookup is `.get(...)`, `set()`-seeded by the helpers' defaults.
        direct_parents: dict[str, set[str]] = {}
        direct_children: dict[str, set[str]] = {}
        equivalences: dict[str, set[str]] = {}
        bare_inherits: dict[str, set[str]] = {}

        def _equivalent(a: str, b: str) -> None:
            """Record two NAMES of one kind (equivalentTo, alias, spelling)."""
            equivalences.setdefault(a, set()).add(b)
            equivalences.setdefault(b, set()).add(a)

        def _equivalence_class(kind: str) -> set[str]:
            seen = {kind}
            stack = [kind]
            while stack:
                for peer in equivalences.get(stack.pop(), ()):
                    if peer not in seen:
                        seen.add(peer)
                        stack.append(peer)
            return seen

        def _walk(kind: str, links) -> set[str]:
            """Every kind reachable in ONE direction (``links`` is equivalence-aware)."""
            seen: set[str] = set()
            stack = list(links(kind))
            while stack:
                current = stack.pop()
                if current in seen:
                    continue
                seen.add(current)
                stack.extend(links(current))
            return seen

        def _parents_of(kind: str) -> set[str]:
            out: set[str] = set()
            for peer in _equivalence_class(kind):
                out |= direct_parents.get(peer, set())
            return out

        def _children_of(kind: str) -> set[str]:
            out: set[str] = set()
            for peer in _equivalence_class(kind):
                out |= direct_children.get(peer, set())
            return out

        def _ancestors(kind: str) -> set[str]:
            return _walk(kind, _parents_of)

        def _descendants(kind: str) -> set[str]:
            return _walk(kind, _children_of)

        # ONTOLOGY §6's core rows are the first half of the subclass relation.
        for core_parent, core_children in CORE_SUBCLASSES.items():
            for child in core_children:
                direct_parents.setdefault(child, set()).add(core_parent)
                direct_children.setdefault(core_parent, set()).add(child)

        # …and the pack's declared `subclassOf` is the second.
        for p in self.packs.values():
            ns = p.namespace
            for child, parent in p.kind_subclasses.items():
                full_child = f"{ns}:{child}"
                direct_parents.setdefault(full_child, set()).add(parent)
                direct_children.setdefault(parent, set()).add(full_child)

        # The `core:`-prefixed spelling is one of the three equivalence families
        # (`equivalentTo`, `LEGACY_KIND_ALIASES`, and this seed), and a
        # first-class one: a core kind is spelled BARE (`WorkItem`) by pack
        # declarations and `core:`-prefixed (`core:WorkItem`) by the classifier's
        # candidate index (`extractor_v2.CORE_OBJECT_KEYS` / `SUBJECTS` /
        # `EVENTS`), by the open write doors
        # (`create_object(objectKind="core:organization")`), and
        # `subject_binding._normalize_kind` folds the prefix back for comparison.
        # Rows are STORED in either spelling, so a bucket carrying only the bare
        # form returns nothing for a `core:`-kinded row — without this seed the
        # widening is inert on the `search`/object path, where `core:WorkItem` is
        # the spelling the classifier actually emits.
        #
        # It is declared HERE, as an equivalence, rather than copied into each
        # `core:` bucket afterwards, and that is what makes it transitive: the
        # closure seeds every endpoint named in `equivalences`, then re-adds
        # `_equivalence_class(member)` for everything a bucket gains, so a bucket
        # reaches the other spelling AND every stored legacy spelling of it
        # (`expand_kind("core:deployment")` carries `dev:deployment`) whatever
        # order the constants are folded in. A post-closure copy pass used to sit
        # below the closure claiming to be load-bearing; it appended nothing to
        # any of the 311 buckets and was deleted (post-merge review of #11667).
        for kind in CORE_ALIASED_KINDS:
            _equivalent(kind, f"core:{kind}")

        # Core kinds: expand to [self] + all pack subclasses (all 5 categories)
        all_canonical: list[str] = []
        for kind_set in (CANONICAL_OBJECT_KINDS, CANONICAL_POINT_KINDS,
                          CANONICAL_EVENT_KINDS, CANONICAL_DOCUMENT_KINDS,
                          CANONICAL_ACTION_KINDS):
            all_canonical.extend(kind_set)
        # R6 §6.2a (#949): core ENTITY types are subclass parents too —
        # `architecture: Document` validated but did NOT expand because
        # Document/Source/Subject/Point/Event were not expansion roots.
        all_canonical.extend(CORE_ENTITY_TYPES)
        for parent in all_canonical:
            subs = self.get_subclasses(parent)
            expansions[parent] = [parent] + subs  # noqa: RUF005

        # Pack kinds: each maps to [self] initially (all 5 categories)
        for p in self.packs.values():
            ns = p.namespace
            for kind_list in (p.object_kinds, p.point_kinds, p.event_kinds,
                               p.document_kinds, p.action_kinds):
                for kind in kind_list:
                    full = f"{ns}:{kind}"
                    expansions[full] = [full]

        # The declared `subclassOf` parent is supplied by the closure's UP walk
        # below, which reads `direct_parents` straight from `kind_subclasses`.
        #
        # ⚠️ A loop used to sit here appending `parent` to the child's bucket, and
        # it WAS the `#11000` Leg A bug: `parent` was bound and never read (a
        # `B007` suppression on the loop was the tell), so a pack's declared
        # business vocabulary was loaded, validated, and invisible to every read
        # — `expand_kind("dev:issue")` carried only its `equivalentTo` peer
        # (`pm:issue`) and never `WorkItem`. That loop is now content-redundant:
        # the closure adds the same name PLUS the parent's ancestors, so removing
        # it leaves every bucket's MEMBERSHIP identical (only list order shifts —
        # verified by deleting it, suite green, because the property and not the
        # loop is the contract). Removed rather than left as dead weight; the
        # property is still pinned by
        # `test_every_declared_parent_is_in_its_child_expansion`.
        #
        # ⛔ The PARENT NAME only, never the parent's bucket: a `dev:issue` is a
        # `WorkItem`, but it is not a `dev:pullRequest`. That is exactly what the
        # closure's DIRECTIONAL split enforces (up = names, down = subtree).

        # Apply equivalentTo: bidirectional
        for p in self.packs.values():
            ns = p.namespace
            for kind, targets in p.kind_equivalences.items():
                full_kind = f"{ns}:{kind}"
                for target in targets:
                    # Add target to this kind's expansion
                    if full_kind in expansions:  # noqa: SIM102
                        if target not in expansions[full_kind]:
                            expansions[full_kind].append(target)
                    # Add this kind to target's expansion (bidirectional)
                    if target not in expansions:
                        expansions[target] = [target]
                    if full_kind not in expansions[target]:
                        expansions[target].append(full_kind)
                    _equivalent(full_kind, target)

        # Bare-kind resolution: a bare kind name (e.g., "useCase") maps to all
        # pack-prefixed forms (e.g., "product-strategy:useCase").
        #
        # #6146: the bare form ITSELF is kept. Expansion is additive; an
        # expansion that DROPPED the argument made `query(kind=K)` build
        # `n.pointKind IN ['dev:issue','pm:issue']`, which matches no point
        # created with the bare `issue` kind — so a valid kind with live points
        # returned [] and the caller could not tell that from "no points".
        # `migrate_kinds` is a manual, opt-in normaliser (tortoise/migrate_kinds.py,
        # run by hand), never a precondition for a READ, so bare-kinded data is a
        # state the read path must serve rather than assume away.
        # ⚠ SINGLE-SEGMENT only. `bare_inherits` maps a bare kind name to that
        # kind's `ns:kind` forms, and the closure relies on "a value is never
        # itself a key" to close in ONE hop. A multi-segment name would break
        # that: an `equivalentTo` target is only required to CONTAIN ':'
        # (`_validate`), so `x:synth:team` splits to the tail `synth:team` — which
        # IS a key, for the real `synth:team` kind — and the closure would chain
        # an unrelated kind into the bucket — measured on a head WITHOUT this
        # guard, `expand_kind("team")` gained `synth:widget`. Those names stay in
        # `expansions` and remain reachable through `equivalences` — an
        # `equivalentTo` peer is still chained to its declaration, which is
        # correct — they simply never seed a `bare_inherits` chain.
        bare_to_full: dict[str, list[str]] = {}
        for full in expansions:
            if full.count(":") == 1:
                _ns, _kind = full.split(":", 1)
                bare_to_full.setdefault(_kind, []).append(full)
        for bare, fulls in bare_to_full.items():
            if bare in expansions:
                for f in fulls:
                    if f not in expansions[bare]:
                        expansions[bare].append(f)
            else:
                expansions[bare] = [bare, *fulls]
            # ⚠ ONE-DIRECTIONAL on purpose: through THIS map the bare form
            # INHERITS from its namespaced forms (see the closure pass), and the
            # namespaced bucket gains nothing back. That asymmetry is pre-existing
            # (#6146 keeps the bare form additive without making `dev:code` read a
            # `code` row) and is the negative control's anchor.
            # ⚠ It is a property of THIS MAP, not of "namespaced" in general: a
            # bare/namespaced pair that is ALSO recorded as an equivalence is
            # symmetric through `equivalences` — the `core:` alias seed does that
            # for every alias, and `LEGACY_KIND_ALIASES` for its pairs, which is
            # why `expand_kind("dev:deployment")` carries the bare `deployment`
            # while `expand_kind("dev:issue")` carries no bare `issue`.
            bare_inherits.setdefault(bare, set()).update(fulls)

        # Legacy aliases (LEGACY_KIND_ALIASES): a kind whose spelling moved
        # namespaces still has nodes in stored graphs. Add the old spelling to
        # the new kind's expansion (and the reverse) so read paths keep seeing
        # them — the pack cannot declare it, because `equivalentTo` targets must
        # resolve to a declared kind.
        for new_kind, legacy_kinds in LEGACY_KIND_ALIASES.items():
            bucket = expansions.setdefault(new_kind, [new_kind])
            for old_kind in legacy_kinds:
                if old_kind not in bucket:
                    bucket.append(old_kind)
                old_bucket = expansions.setdefault(old_kind, [old_kind])
                if new_kind not in old_bucket:
                    old_bucket.append(new_kind)
                _equivalent(new_kind, old_kind)

        # ── THE CLOSURE (#11000 R6): DIRECTIONAL, not a bucket union ───────
        # The declaration model is depth 2, not depth 1. `_validate` restricts a
        # `subclassOf` parent to `CANONICAL_OBJECT_KINDS | CORE_ENTITY_TYPES`, so
        # a pack kind is never a parent — but all 16 of the
        # `CANONICAL_OBJECT_KINDS` are themselves children of `Object` (the row's
        # members ARE that canonical set), so every declared pair is the chain
        # `pack kind ⊑ core child ⊑ Object`
        # (`dev:bug ⊑ Problem ⊑ Object`). Measured at the one-hop head: 16 of the
        # 17 declared pairs had a parent that is an `Object` child, and
        # `expand_kind("Object")` carried NONE of them — half-fixing the very
        # defect #11000 is. ⚠️ The instrument that reads `kind=` for OBJECTS is the
        # object entity read path — `tortoise_fts_query(..., entity_type="object")`
        # (the `tortoise_search` surface), whose object kind field is `objectKind`.
        # **NOT `sdk.query()`**, which is `MATCH (n:Point)` (`sdk.py`): it can
        # never return an `:Object`, so citing it as evidence that the widening
        # happened is vacuous. ⚠️ It is not inert either — `query(kind=…)` filters
        # `n.pointKind IN expand_kind(kind)` (`sdk.py`), so once `expand_kind(
        # "Object")` carries `goal`/`target` a `query(kind="Object")` DOES return
        # `:Point` rows with those `pointKind`s. It answers a DIFFERENT question
        # (`:Point` rows), before and after this fix — see DM4.
        #
        # ⛔ The closure must be DIRECTIONAL. A plain "add the members of every
        # member's bucket" fixpoint is WRONG: `WorkItem`'s bucket contains all
        # eight of its children, so walking down from an ancestor would put
        # `dev:pullRequest` in `expand_kind("dev:issue")` — precisely the
        # over-wide answer `test_a_child_gains_only_its_own_parent` exists to
        # prevent. Up and down are different relations and are kept apart:
        #
        #   * UP   — a child inherits its ancestors' NAMES (`dev:issue` gains
        #            `WorkItem`, then `Object`), never their other children;
        #   * DOWN — a root gathers all its descendants (`Object` gains
        #            `WorkItem`, then `dev:issue`/`dev:pullRequest`/…).
        #
        # Equivalence (`equivalentTo`, `LEGACY_KIND_ALIASES`, and the `core:`
        # alias seed above) is symmetric and is traversed inside BOTH, because
        # equivalent kinds have the same extension.
        #
        # ⚠ bare↔namespaced is NOT one relation, and sorting them matters — an
        # earlier version of this comment flattened them and was false:
        #   * bare ↔ `core:` IS an equivalence (the alias seed above), so
        #     `expand_kind("core:team")` carries the bare `team`.
        #   * bare ↔ PACK-namespaced is TWO things at once: the one-directional
        #     `bare_inherits` map, AND an equivalence for the few pairs
        #     `LEGACY_KIND_ALIASES` records. So `expand_kind("dev:deployment")`
        #     carries the bare `deployment` through `equivalences`, while
        #     `expand_kind("dev:issue")` carries no bare `issue` because that pair
        #     is not one of them.
        #
        # Termination is structural, not bounded by a counter: `direct_parents`
        # is acyclic (a pack kind is never a parent, and `CORE_SUBCLASSES` rows
        # are disjoint from its keys — both pinned), so both walks reach a
        # fixpoint. A cycle guard would be inert machinery over a state no
        # manifest can construct.
        # Every kind that appears in ANY relation must own a bucket BEFORE the
        # closure below iterates `expansions` — it walks a snapshot, so an
        # endpoint whose bucket is only created by a later pass is silently
        # skipped in the UP direction. Measured before this seed:
        # `expand_kind("team") == ["team", "core:team"]` — no `Subject`, although
        # `CORE_SUBCLASSES["Subject"]` declares it — because §5's five Subject
        # kinds sit in no canonical set, so nothing before the walk created their
        # bucket. This seed is now the only thing that creates it: the post-walk
        # `core:`-alias copy loop that also used to do so was measured inert and
        # deleted (see the `_equivalent` seed above), so nothing runs after the
        # snapshot any more.
        # ⛔ The COST, measured rather than assumed — a first version of this note
        # claimed "no writer stores `subjectKind='Subject'`", which is FALSE:
        # `create_subject(subjectKind="Subject")` and
        # `create_subject(subjectKind="core:Subject")` are both accepted and STORED
        # as written, so `subjectKind IN expand_kind("team")` now also returns a
        # root-kinded Subject row — the read widens by a join on the layer root
        # whenever that `IN` clause is the filter that runs.
        # ⚠️ FORM-SCOPED, exactly like the `Object` read — measured on BOTH, not
        # generalised: the `IN` clause is the POST-retrieval filter, so a
        # text-query read widens while a kind-only read (`query=None`, i.e.
        # `is_full_scan`) runs only the structural leg (`n.subjectKind = $kind`)
        # and does NOT. One `:Subject` with `subjectKind='team'` and one with
        # `subjectKind='Subject'`: `tortoise_fts_query(kind="team",
        # entity_type="subject")` returned the `team` row only;
        # `tortoise_fts_query("widget", kind="team", entity_type="subject")`
        # returned both. Widening the full scan is a BEHAVIOUR change owed by
        # #11690, not a doc fix.
        #
        # …and that is CORRECT, because the `Object` row has had exactly this
        # property since the §6 table was first read: `create_object(objectKind=
        # "Object")` is likewise accepted and STORED, so `objectKind IN
        # expand_kind("dev:issue")` returns it — under the SAME form-scope as the
        # Subject `IN` above (a text-query read only; #11690 owes the full scan).
        # The Subject row was the anomaly,
        # not the Object row. Declining the seed would leave the stated contract
        # (`expand_kind(child) ⊇ {child, parent}`, transitive) violated for one of
        # the four `CORE_SUBCLASSES` rows (`Object`/`Subject`/`Point`/`Event` — §6
        # declares only `Object`/`Document`/`Subject`, so `Point` AND `Event` are
        # this module's extension) while the other three already behave this way.
        # Applied for UNIFORMITY, with the widening named rather than denied.
        for endpoint in (list(direct_parents) + list(direct_children)
                         + list(equivalences) + list(bare_inherits)):
            expansions.setdefault(endpoint, [endpoint])

        for kind in list(expansions):
            gained: set[str] = set()
            sources = _equivalence_class(kind) | bare_inherits.get(kind, set())
            # A peer's bare-inherited members round-trip too, or one spelling of
            # one kind answers narrower than the other: `bare_inherits` is keyed
            # by the BARE name, so a pack that registers `objectKinds: [team]`
            # puts `synth:team` in `expand_kind("team")` and, without this, NOT
            # in `expand_kind("core:team")` — while
            # `test_core_prefixed_and_bare_spellings_are_one_kind` states they
            # are one kind.
            #
            # `bare_inherits` is keyed by the BARE tail of a single-segment
            # `ns:kind` name and valued by that full name — guaranteed by the
            # `bare_to_full` guard above, not assumed here. So a key never contains
            # ':' and a value always does: a value is never itself a key, and ONE
            # round is the whole closure. That is a property of the map's
            # CONSTRUCTION, and the guard is what keeps it true.
            for peer in list(sources):
                sources |= bare_inherits.get(peer, set())
            for peer in sources:
                gained.add(peer)
                gained |= _ancestors(peer) | _descendants(peer)
            # …and the equivalence class of everything just gained, so an
            # ancestor's stored legacy spelling arrives with the ancestor
            # (`expand_kind("Event")` gains `deployment` AND `dev:deployment`).
            for member in list(gained):
                gained |= _equivalence_class(member)
            bucket = expansions[kind]
            for member in sorted(gained.difference(bucket)):
                bucket.append(member)

        self._kind_expansions = expansions

    def list_tools(self) -> list[dict]:
        """Return all tools across all packs with their schemas."""
        tools: list[dict] = []
        for p in self.packs.values():
            for t in p.tools:
                tools.append({
                    "pack": p.namespace,
                    "name": t.get("name"),
                    "description": t.get("description"),
                    "entrypoint": t.get("entrypoint"),
                    "params": t.get("params", {}),
                })
        return tools

    def list_connectors(self) -> list[dict]:
        """Return all connectors with their config requirements."""
        connectors: list[dict] = []
        for p in self.packs.values():
            for c in p.connectors:
                connectors.append({
                    "pack": p.namespace,
                    "source": c.get("source"),
                    "sourceKind": c.get("sourceKind"),
                    "entrypoint": c.get("entrypoint"),
                    "required_config": c.get("config", {}).get("required", []),
                })
        return connectors

    def list_relations(self) -> list[dict]:
        """Return all relation declarations across packs with mechanisms.

        Example: [{"pack": "product-strategy", "predicate": "addresses",
                   "fromKind": "product-strategy:feature",
                   "toKind": "product-strategy:customerNeed",
                   "mechanism": "IMPL"}]
        """
        relations: list[dict] = []
        for p in self.packs.values():
            ns = p.namespace
            for r in p.relations:
                entry = {"pack": ns, "predicate": r.get("predicate")}
                # New format
                if "fromKind" in r:
                    entry["fromKind"] = r["fromKind"]
                    entry["toKind"] = r["toKind"]
                    entry["mechanism"] = r.get("mechanism", "IMPL")
                    # Manifest v3 (R6 §3.3): extractable flag — default false
                    entry["extractable"] = r.get("extractable", False)
                # Old format (backward compat)
                else:
                    entry["from"] = r.get("from")
                    entry["to"] = r.get("to")
                if r.get("cardinality"):
                    entry["cardinality"] = r["cardinality"]
                # Layer 2 (#7852): the MEANING a relation declares, and the
                # siblings it is confused with. Declared-only today — the
                # declaration is what the vocabulary is validated against.
                if r.get("semantics"):
                    entry["semantics"] = r["semantics"]
                if r.get("nearMisses"):
                    entry["nearMisses"] = list(r["nearMisses"])
                # #11083 — the per-row disposition and sense the census reads.
                # `direction` is the DECLARED per-edge sense when the manifest
                # states one (ruling (c): with `ownedBy`'s target left open, the
                # predicate can no longer say which end owns which);
                # `effective_direction` is what the row ACTUALLY records once the
                # declared semantics is resolved through the same transition →
                # table chain the write path uses. ``None`` = the row declares
                # neither, and a reader must decide what absence means rather
                # than receive a plausible default.
                if r.get("direction"):
                    entry["direction"] = r["direction"]
                entry["effective_direction"] = effective_direction(r)
                # #11083 — `declarationOnly: true` is an author opt-in that
                # SILENCES the not-writable warning, so the reason is part of the
                # declaration: without it the marker is a silencer (R7).
                if r.get("declarationOnly"):
                    entry["declarationOnly"] = True
                if r.get("declarationOnlyReason"):
                    entry["declarationOnlyReason"] = r["declarationOnlyReason"]
                # #2766: the SAME verdict pack load warns and `pack validate`
                # prints, surfaced here so the discoverable API cannot assert a
                # relation is bound to a path its declaration does not bind.
                # `write_paths` names the DECLARED paths ("operator" |
                # "structural"); empty when the declaration binds none. NOT a
                # runtime refusal: `create_operator` accepts any label
                # (warn-not-block, #1934); only `create_edge` is strict.
                write_verdict, write_paths = relation_write_verdict(r)
                entry["write_verdict"] = write_verdict
                entry["write_paths"] = list(write_paths)
                relations.append(entry)
        return relations

    # ── Register (idempotent) ──────────────────────────────────────────

    def register_kinds(self) -> dict[str, int]:
        """Register all pack kinds into the global ontology. Idempotent.

        Returns counts of kinds registered. Safe to call multiple times —
        duplicate registrations are skipped.
        """
        counts = {"registered": 0, "skipped": 0}
        seen: set[str] = set()
        for p in self.packs.values():
            ns = p.namespace
            for kind_list in [p.object_kinds, p.event_kinds, p.point_kinds,
                               p.document_kinds, p.action_kinds]:
                for k in kind_list:
                    key = f"{ns}:{k}"
                    if key in seen:
                        counts["skipped"] += 1
                    else:
                        seen.add(key)
                        counts["registered"] += 1
        return counts


# ── Self-check ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    packs_dir = default_packs_dir()
    registry = PackRegistry(packs_dir)
    loaded = registry.load_all()

    print(f"Loaded {loaded} pack(s)")
    if registry.errors:
        print(f"Errors: {registry.errors}")
        sys.exit(1)

    print("\nPacks:")
    for p in registry.list_packs():
        print(f"  {p['namespace']}: {p['name']} v{p['version']} ({p['tier']})")

    print("\nAll kinds:")
    for field, kinds in registry.list_all_kinds().items():
        if kinds:
            print(f"  {field}: {', '.join(kinds)}")

    print("\nTools:")
    for t in registry.list_tools():
        print(f"  {t['pack']}/{t['name']}: {t['description']}")

    print("\nOK")

    # ── Error path tests ──
    errors = registry._validate({"name": "MissingNamespace"})
    assert errors, f"Expected errors for missing namespace, got none"  # noqa: F541
    print("  ✓ missing namespace detected")

    errors = registry._validate({"namespace": "bad:ns", "name": "X",
                                 "ontology": {"extends": "core"}})
    assert errors, f"Expected errors for colon in namespace"  # noqa: F541
    print("  ✓ namespace colon rejected")

    errors = registry._validate({"namespace": "x", "name": "X",
                                 "ontology": {"extends": "core",
                                              "eventKinds": ["BadCase"]}})
    assert any("camelCase" in e for e in errors), f"Expected camelCase error: {errors}"
    print("  ✓ camelCase enforced")

    errors = registry._validate({"namespace": "x", "name": "X",
                                 "ontology": {"extends": "core",
                                              "objectKinds": ["document"]}})
    assert any("canonical" in e for e in errors), f"Expected canonical error: {errors}"
    print("  ✓ canonical conflict detected")

    errors = registry._validate({"namespace": "x", "name": "X",
                                 "ontology": {"extends": "core"},
                                 "tools": [{"name": "t", "entrypoint": "f",
                                             "params": {"p": {"type": "badtype"}}}]})
    assert any("invalid type" in e for e in errors), f"Expected param type error: {errors}"
    print("  ✓ invalid tool param type detected")

    errors = registry._validate({"namespace": "x", "name": "X",
                                 "ontology": {"extends": "core"},
                                 "connectors": [{"source": "gh"}]})
    assert any("entrypoint" in e for e in errors), f"Expected entrypoint error: {errors}"
    print("  ✓ missing connector entrypoint detected")

    errors = registry._validate({"namespace": "x", "name": "X", "tier": "enterprise"})
    assert any("tier" in e for e in errors), f"Expected tier error: {errors}"
    print("  ✓ invalid tier rejected")

    print("\nAll validation checks passed")
