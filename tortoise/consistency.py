"""Startup consistency check — does the projection still equal `replay(journal)`?

The projection is a derived view folded from the domain event log (the
reconstruction source — not the durability authority; see
docs/durability-posture.md). This module verifies the counts haven't diverged
AND, since #5011, that the projection's CONTENT still equals what a replay of
the journal produces.

#5011 — why content, not count:
    A count check passes a projection that is WRONG but the right size — a
    stale ``status``, a wrong ``content``, a dropped field all satisfy
    ``log_points == db_points``. The invariant is
    ``derived tables = replay(the journal)`` (docs/architecture/
    STORAGE-ARCHITECTURE.md §3), and size is not a proxy for it.

    So the check folds the journal, canonicalises both sides, and compares them
    FIELD BY FIELD over the projection's DECLARED content schema (`_EXCLUSION_REASONS`
    and the tables below), reporting every exclusion it actually exercised. The
    verdict is that field-aware comparison (``hash_match``), NOT a digest
    equality, because the recorded baseline and the journal comparison are
    different jobs:

      * ``db_hash`` — a SHA-256 of the GRAPH's own canonical content, a pure
        function of the graph. A healthy run records it; the next run compares
        it, so a graph that moved while the JOURNAL did not is a divergence in
        its own right (``unrecorded-mutation``), even when the field-aware
        comparison finds no mismatched field.
      * ``hash_match`` — whether the graph equals ``replay(journal)``.

    They are reported side by side and neither is derived from the other; a
    digest equality cannot be the verdict because the writer's non-deterministic
    defaults and the store's float round-trip have to be reconciled first.

    Both halves of the invariant are honestly bounded, and the bounds are
    REPORTED rather than hidden: ``excluded_fields`` / ``one_sided_fields`` name
    every field the comparison did not decide, and ``uncarried_journal_fields``
    names journal content the projection cannot hold. Adoption of a pre-existing
    graph — which has no recorded baseline and must NOT be reported as diverged
    on first run — is the ``adopted`` outcome.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
import math
import os
import re
import stat
import struct
from contextlib import suppress
from datetime import datetime, timezone

from .cypher_guard import tolerates_altered_numbers
from .exceptions import UnrepresentableNumberError

# #8864 review P1: the stub carrier's id is the SHARED deterministic mint
# (`_event_plain_merge` / `_upsert_object` both use it), never a re-derivation.
from .ids import entity_name_id
from .projection import (
    _CANONICAL_ENTITY_LABELS,
    _ENTITY_ID_PROP,
    _ENTITY_MUTATION_STATE_OPS,
    _REFERENCE_FOLD_ENTITY_LABELS,
    WipePreconditionRefused,
    _annotator_value_ok,
    _apply_one,
    _creation_entity_id_from_record,
    _event_mentioned_name_stubs,
    _hard_deleted_any,
    _journal_forward_reference,
    _load_prewipe_snapshot,
    _norm,
    _object_hard_deleted_ids,
    _promotion_point_with_operator,
    _replay_write_scope,  # #7951 §17.1: replay folds are authorized, live ones are not
    _writable_id,
    entities,
    fold_with_entities,
    hard_deleted_pairs,
    journal_epistemic_existence,
    journal_first_materialization,
    journal_hard_delete_seqs,
    journal_object_surviving_keys,
    journal_recreate_seqs,
    plan_point_restamp_folds,
    prewipe_snapshot_path,
)
from .projection.entities import (
    BELIEF_BOOL_PROPS,
    BELIEF_PROPS,
    _EntityHandlers,
    _is_persistable_prop_value,
    _store_holds_prop_value,
    _usable_instant,
    _writable_journalled_vector,
    flatten_search_keys_prop,
    is_non_operator_payload,
    promote_status_applies,
)
from .projection.nonfolded import (  # #3585 — R8/R9 fail-closed set
    SHAPE_POINT_SUPERSEDED_NO_NEW_ID,
    SHAPE_STATE_OP_MISS,
    classify_terminalizer_miss,
    collect_identity_skips,
    collect_non_folded,
    record_non_folded,
    refused_events,
)

logger = logging.getLogger(__name__)

# The writer's OWN declarations of which props it owns, and which list-valued
# props it will persist — READ rather than re-listed, so a change there cannot
# silently desync this check. `_EntityHandlers` is the private point-writer mixin
# and these are class attributes; a rename breaks the import LOUDLY, which is the
# point (the first draft of this file hand-copied its deny-list and was measured
# 11 keys behind it).
_POINT_HANDLED: frozenset = _EntityHandlers._POINT_HANDLED
_POINT_LIST_PROPS: frozenset = _EntityHandlers._POINT_LIST_PROPS
_POINT_DENY: frozenset = _EntityHandlers._POINT_DENY
# #8006: the props the SHARED Point writer normalizes from a payload LIST into
# the graph's flat STRING (`flatten_search_keys_prop`). Read from the writer's
# own declaration for the same reason as the three above: `_uncarried` must not
# call such a value "the projection cannot hold this" when it now carries it,
# and the canonical view must compare the WRITER'S form, not the payload's.
_POINT_FLATTEN_LIST_PROPS: frozenset = entities._POINT_FLATTEN_LIST_PROPS


# ── #5011: the projection's declared content schema ───────────────────────
#
# The comparison is over the projection's DECLARED content fields, not over
# "everything either side happens to hold". Every exclusion is declared HERE
# with its reason, and every exclusion that is actually present in a compared
# run is REPORTED by name (`excluded_fields`) — an exclusion that cannot be
# seen is the same defect as one that is not declared.
#
# Two rules keep the schema honest:
#   * the exclusion tables derive from the WRITER'S OWN declarations wherever
#     one exists — a hand-copied second list drifts (the first draft of this
#     file was already 11 keys behind `_EntityHandlers._POINT_DENY`);
#   * the drop rules of the journal→graph passthrough are READ from the writer's
#     own predicate (`_is_persistable_prop_value` + `_POINT_LIST_PROPS`), so
#     "the projection cannot hold this" is the writer's answer, not ours.

# Keys excluded from the comparison, each with the reason it cannot be a
# journal-derivable content field. `_POINT_DENY` is merged in below rather than
# re-listed (see the merge note).
_EXCLUSION_REASONS: dict[str, str] = {
    # Structural — carried as edges, or derived at write time from the payload.
    "operator": "nested payload → is_operator / op_type / operator edges",
    "about_entities": "carried as about* edges, never a node prop",
    "aboutEntities": "carried as about* edges, never a node prop",
    "context": "retired (Phase 2 #49) — never written",
    "new_context": "retired (Phase 2 #49) — never written",
    "reason": "deny-listed per D4 — never a node property",
    # Recomputed / replay-owned — a pure function of the row, or a write time.
    "content_hash": "pure f(content) — RECOMPUTE (STORAGE-ARCHITECTURE §3)",
    "updatedAt": (
        "excluded because the stamp is not journal-derivable for every writer "
        "— a retraction now replays it from the record (#5048), but "
        "pass-1a/other writers still set it from the replay clock"),
    "_nid": "replay bookkeeping",
    "_graph_id": "replay bookkeeping",
    # #5004: the embedding's IDENTITY is journal PAYLOAD metadata (`_POINT_HANDLED`)
    # — by design it is never a node property. The vector itself IS compared,
    # presence-conditionally, further down.
    "embedding_model": "#5004 — payload-only embedding identity, never a node prop",
    "embedding_revision": "#5004 — payload-only embedding identity, never a node prop",
    "embedding_text_hash": "#5004 — payload-only embedding identity, never a node prop",
    "embedding_preserved": "#5004 — payload-only capture marker, never a node prop",
    # EP-owned runtime state. `ep_dirty`/`ep_dirty_at` are mutated out of band by
    # ep.py/dream.py with NO journal record; they are NOT in `_POINT_DENY`, so
    # the replay passthrough does copy them out of a payload — which is a
    # separate defect (#5166: a rebuild then restores a STALE flag). Excluded
    # here because the journal's last word is not their current value either way.
    "ep_dirty": "EP operational flag — rewritten live by ep.py/dream.py, no journal event (#5166)",
    "ep_dirty_at": "EP operational timestamp — rewritten live by ep.py/dream.py (#5166)",
    # Compared SEPARATELY, not skipped: `provenance` is translated to the flat
    # `provenanceSource`; `embedding` is compared at the store's float width.
    "provenance": "translated — nested source_id → the graph's flat provenanceSource",
    "embedding": "compared separately, presence-conditionally, at the stored width",
}

# Keys in `_META_KEYS` that no fixed clause writes: envelope metadata and the
# `about*`/`ownedBy`/`managedBy` edge carriers. `_persist_extra_props` drops them
# (`skip = _META_KEYS | handled_keys`), so a journal payload carrying one reads
# as a missing graph property unless it is excluded — and a real producer
# (`EventAPI.add_point(**fields)`) does carry them.
_NEVER_A_NODE_PROP: frozenset[str] = (
    (_EntityHandlers._META_KEYS - _POINT_HANDLED) | {"created_at"}
)
_EXCLUSION_REASONS.update({
    k: "`_META_KEYS` — envelope metadata / an edge carrier, dropped by the "
       "passthrough and never written as a node property"
    for k in _NEVER_A_NODE_PROP - {"created_at"}
})
_EXCLUSION_REASONS["created_at"] = (
    "consumed by the writer as the `createdAt` alias (`p.get(\"created_at\")`) "
    "— never a node property of its own name"
)

# D1/D4: the writer's own deny-list — merged rather than re-listed, because a
# hand-copied second list drifts (the first draft of this file was measured 11
# keys behind). BUT the deny-list answers a DIFFERENT question than this one: it
# says "the open-set passthrough must not reload this from a payload", not "the
# journal cannot state this". Six of its entries ARE journal-derived through a
# fixed clause, so excluding them would be a blind spot with a false reason:
#   embedding       — compared separately, at the stored width
#   posterior_alpha / posterior_beta / lastDreamedAt
#                   — folded from `ConfidenceChanged` (BELIEF_PROPS)
#   expiredAt / outdated
#                   — restored verbatim by the PointSuperseded /
#                     PointInvalidated replay fold (entities.py `SET n.outdated`,
#                     `n.expiredAt`), which `_fold_journal` mirrors
# What remains is genuinely never-restorable: EP runtime state written only by
# ep.py/dream.py (`ep_alpha`, `ep_beta`, `c_cal`, `baseline_*`, `inherited_at`)
# plus the recomputed/never-written keys.
_JOURNAL_RESTORABLE_DENY: frozenset[str] = frozenset({
    "embedding", "posterior_alpha", "posterior_beta", "lastDreamedAt",
    "expiredAt", "outdated",
})
_DENY_REASON = (
    "`_EntityHandlers._POINT_DENY`: written only by ep.py/dream.py at runtime, "
    "or recomputed — no journal event states its current value (D1/D4, #2884)"
)
_EXCLUSION_REASONS = {
    **{k: _DENY_REASON for k in (_POINT_DENY - _JOURNAL_RESTORABLE_DENY)},
    **_EXCLUSION_REASONS,
}

# Keys whose drop is NAMED (rather than caught by the generic list rule in
# `_uncarried`) because the loss has its own issue and its own reason. They are
# excluded from the comparison PER POINT, by `_uncarried` — not statically — so a
# graph-only value is still compared and can still be reported as a divergence.
#
# EMPTY since #2897 landed: `tags` was the only entry, and it is now CARRIED
# (`_POINT_HANDLED`, with its own node-property clause plus the pass-2
# `:Tag`/`TAGGED` sync), so the checker COMPARES it instead of exempting it —
# which is what makes a future regression on that door visible instead of
# silently tolerated. ⚠️ That widening also moves the graph FINGERPRINT for
# every baseline whose graph holds a tag, which is why
# `_PROJECTION_STATE_FORMAT` went to 2 in the same change. The dict is kept
# (rather than deleted) because the mechanism is the right home for the next
# declared live/replay asymmetry.
_UNCARRIED_CONTENT_PROPS: dict[str, str] = {}

# A key whose EXISTENCE legitimately depends on the payload, so a one-sided
# presence is a representation asymmetry rather than content the journal
# authored. These ARE compared whenever both sides carry them — which is where a
# real divergence shows — and a one-sided presence is REPORTED (`one_sided_fields`)
# rather than flagged.
_ONE_SIDED_REASONS: dict[str, str] = {
    "speaker": (
        "`fold` mirrors `provenance.speaker` while `_upsert_point_props` writes "
        "it only when the payload carried it; the live `update_point(speaker=…)` "
        "path writes it graph-only — the live/replay parity class (#2164)"
    ),
    "embedding_verbatim": (
        "written `CASE WHEN $evb THEN true ELSE <unchanged>`, so a falsy flag "
        "leaves the node ABSENT while the journal may carry an explicit `false`; "
        "`update_entity(embedding=…)` sets it live-only by design (#5004)"
    ),
}

# Statically excluded + separately handled.
_NOT_COMPARED: frozenset[str] = frozenset(_EXCLUSION_REASONS)

# #5256: `sourceVersionTransit` (the `extractedFrom` read-version carrier —
# the EDGE scalar `sourceVersion` is the model; this node prop is the replay
# transit) is deliberately NOT added to `_EXCLUSION_REASONS`. It is a declared
# node property that the live writer resolves from the :Source and carries in
# the Point's own journaled snapshot (`get_point` → the `PointAdded` payload),
# and pass 2 re-stamps the SAME value from that snapshot. Both sides therefore
# carry it and it MUST be compared; excluding it would be a blind spot. It is
# symmetric-absent for an un-sourced Point or one whose Source has no recorded
# hash; a one-sided presence is reported as a **divergence** (the mismatch
# channel names it: `_uncarried` skips it because it IS `_POINT_HANDLED`, and it
# is not in `_ONE_SIDED_REASONS`) — never silenced here. (`_upsert_point_edges`
# never reads the Source, so the replay can only reproduce what the payload states.)

# #548: operators store NO `content`/`pointKind` as node properties (the live
# writer creates the node without them). The replay writer sets them
# UNCONDITIONALLY (`n.content=$content` with `p.get("content", "")` — the
# `_WRITER_DEFAULTS` empty string; `n.pointKind=coalesce($pk, n.pointKind)`), so
# on an OPERATOR node they are ONE-SIDED by design. #5089 removed the extra
# label the journal seam used to SYNTHESIZE on top of that (`sdk.py`: a
# fabricated `IMPL(inputs)` content that no writer had stored and the replay then
# persisted) — the asymmetry is now the empty string alone.
#
# On an OPERATOR node they are therefore ONE-SIDED by design (the live graph
# lacks them, the journal/replay has them) — but they are NOT silently dropped:
# they are compared whenever BOTH sides carry them, the one-sided case is
# REPORTED (`one_sided_fields`), and they are kept out of the graph fingerprint
# for operators so a live-built and a replay-built operator hash the same. A
# plain `removal` from the canonical view on both sides would have been an
# invisible blind spot.
_OPERATOR_ABSENT_PROPS: frozenset[str] = frozenset({"content", "pointKind"})
_OPERATOR_ABSENT_REASON = (
    "#548 — operators store no `content`/`pointKind`; the replay writer "
    "sets both unconditionally, so the journal side carries them"
)

# Writer defaults for keys it sets with a DETERMINISTIC default. When the
# journal omits one, the graph's value must EQUAL the default: a faithful replay
# produces exactly that, while any other value is the graph holding something
# the journal never authored — a divergence, not a default.
_WRITER_DEFAULTS: dict[str, object] = {"content": "", "status": "live"}

# Writer defaults whose value is NOT deterministic, so a journal that omits the
# key cannot state an expectation. Skipped ONLY in that direction — the key IS
# compared whenever the journal carries it — and every skip is REPORTED, so the
# bound is visible:
#   createdAt  — `coalesce($ca, n.createdAt, $now)`
#   expiredAt  — the PointSuperseded/PointInvalidated fold's own
#                `ev.get("expired_at") or _now_iso()` fallback
_GRAPH_DEFAULTED_PROPS: frozenset[str] = frozenset({"createdAt", "expiredAt"})
_GRAPH_DEFAULTED_REASON = (
    "not journal-stated on this record: the writer fell back to a wall-clock "
    "timestamp (`coalesce($ca, n.createdAt, $now)` / `or _now_iso()`)"
)

# The store does not round-trip a float bit-exactly (measured on the docker
# lane: `0.8214927174495666` reads back `0.821492717449567`), so a bit-equal
# comparison of a faithful replay fails on every float prop (`confidence`, the
# annotator dims, …). Compare numerically at a relative tolerance — 1e-9 is
# ~6 orders of magnitude above the observed round-trip error and far below any
# real value change.
_FLOAT_REL_TOL = 1e-9

# A key the journal omits while the graph holds the writer's own deterministic
# default for it — faithful, not the graph authoring a value.
_WRITER_DEFAULTS_REASON = (
    "the journal omits it and the graph holds the writer's own deterministic "
    "default — faithful, not the graph authoring a value"
)
_MAX_DIVERGENT_POINTS = 50
# The drop rules of the journal→graph open-set passthrough, as ONE reason per
# sub-rule. Read from the writer's own predicate so the two cannot drift.
_UNCARRIED_NULL = "explicit null — the writer cannot persist a null property"
_UNCARRIED_NON_PERSISTABLE = (
    "a value FalkorDB rejects as a node property (map/dict at any depth, bytes, "
    "set) — `_is_persistable_prop_value` filters it before the SET"
)
# #8317 review round 3: the `tags` carriage question asks the writer's EFFECTIVE
# predicate (transport gate + numeric domain), not the shape allowlist — so a
# value that passes the shape test but is nulled by the `#7369` transport gate
# (a non-finite float, a NUL/surrogate string, a null inside an array) or
# CLAMPED by a 4.16–4.20 engine / refused at parameter parse by a 6.x one (an
# int outside INT64) gets its own, accurate reason.
_UNCARRIED_TAGS_UNWRITABLE = (
    "a `tags` value the store cannot hold as stated — the `#7369` transport "
    "gate nulls what `_value_ok` refuses (non-finite float, NUL/surrogate "
    "string, null inside an array, map/bytes/set) and an int outside INT64 is "
    "clamped or refused (`_store_holds_prop_value`)"
)
# ⚠️ BOUND, stated rather than implied, on the `tags` branch that reads the
# reason above (#8317 review round 4):
#   * an `_uncarried` key is skipped on BOTH sides (journal AND graph) and left
#     out of `_graph_fingerprint`, so a GRAPH-only `tags` on a point whose
#     JOURNAL value is unwritable goes uncompared. That is PRE-EXISTING
#     `_uncarried` semantics (every `_UNCARRIED_*` reason behaves this way), not
#     something the `tags` case introduces — `_UNCARRIED_LIST` and
#     `_UNCARRIED_NULL` are the PRE-EXISTING cases with this exact behaviour, and
#     pre-#2897 `tags` was one too (see the reason text above). Recorded on the
#     `tags` branch because carrying `tags` made the hole observable through a
#     property `check_consistency` now COMPARES;
#   * the verdict deliberately differs from the #5011 numeric contract for a
#     GENERIC prop, where an over-range number is a DIVERGENCE
#     (`test_an_over_range_int_is_a_divergence_not_a_crash`): `tags` has a
#     declared carriage rule whose writer states "no property" for that value,
#     so naming the drop is the accurate verdict. The generic rule is untouched.
_UNCARRIED_LIST = (
    "list-valued prop the REPLAYED writer refuses (`_POINT_LIST_PROPS` is empty, "
    "#2795); the live path may still write it raw — the live/replay asymmetry "
    "class (`tags` was the named instance until #2897 replayed it)"
)


def _is_numeric(value) -> bool:
    """True for a real number — `bool` is excluded (it is an `int` subclass)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _vec_raw_equal(a: list, b: list) -> bool:
    """Total, nan-aware element comparison for a vector the STORE cannot hold.

    Used only when at least one side refuses `_writable_journalled_vector`
    (non-numeric / non-finite / over-range element): such a vector never reaches
    the store, so both sides holding one is already abnormal. Compare the raw
    values WITHOUT raising, and treat two identical non-finite elements as equal
    — plain `==` says `nan != nan`, which would report an abnormal-but-faithful
    pair as a divergence on every run with no repair able to clear it.

    The bool check is the SAME rule `_values_equal` applies at top level, and it
    is needed here for the same reason: `True == 1`, so a raw `==` would report a
    vector of flags as faithful against a vector of their int forms.
    """
    if len(a) != len(b):
        return False
    for x, y in zip(a, b, strict=True):
        if isinstance(x, bool) != isinstance(y, bool):
            return False
        if isinstance(x, float) and isinstance(y, float) \
                and math.isnan(x) and math.isnan(y):
            # Before `_values_equal`: nan != nan there by design (a nan is not a
            # value), but two identical non-finite VECTOR elements are the same
            # abnormal-but-faithful input.
            continue
        if not _values_equal(x, y):
            return False
    return True


def _values_equal(a, b) -> bool:
    """Field equality, with the store's float round-trip tolerance applied.

    TOTAL, and STRICT about booleans — both are correctness boundaries, not
    style:

    * a journal record is a FILE (hand-editable, or written by an older/newer
      code path), so a value no double can hold must compare UNEQUAL rather
      than raise. `_belief_prop_value_ok` (projection/entities.py) documents the
      same class — "a >308-digit journaled posterior raised `OverflowError`";
      here it would abort the whole check, durably, on every run.
    * `isinstance(True, int)` is exactly why a flag cannot go through the
      numeric branch: `True == 1`, so the plain `==` would report a graph
      holding `1` for a journal `True` as faithful — a fail-open on the check's
      own job (`_belief_bool_value_ok` documents the same boundary for the
      writer). The rule applies at EVERY depth: `[True] == [1]` and
      `{"k": True} == {"k": 1}` in Python, and a list-valued prop is compared
      verbatim (it is `_POINT_HANDLED`, so `_uncarried` exempts it), so a
      top-level-only check left the container case open.
    """
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_values_equal(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(
            _values_equal(x, y) for x, y in zip(a, b, strict=True))
    if a == b:
        return True
    if _is_numeric(a) and _is_numeric(b):
        # ONLY a float/float pair gets the round-trip tolerance. Every other
        # numeric pairing is exact:
        #   * int/int — the store holds an int64 without loss, so routing these
        #     through `float()` made `2**53` equal to `2**53 + 1`;
        #   * int/float — Python's `==` compares them exactly, and a faithful
        #     replay of an INT prop yields an int, so a differing pair is a
        #     divergence rather than precision loss (measuring it through
        #     `isclose` made `1000000000` equal to `1000000001.0`);
        # and neither can raise, so there is no `float()` conversion left to
        # overflow on an int no double can hold (a journal record is a FILE).
        if isinstance(a, float) and isinstance(b, float):
            return math.isclose(a, b, rel_tol=_FLOAT_REL_TOL, abs_tol=0.0)
        return a == b
    return False


def _canonical_point_fields(props: dict, skip: frozenset = frozenset()) -> dict:
    """The projection's declared content view of ONE point (side-agnostic).

    Applied to both the journal fold and the graph read, so the two views are
    comparable. Three normalisations make a faithful replay compare equal:

      * a ``None`` value is dropped — the writer cannot persist a null property
        (`coalesce(...)` for the fixed clauses, an explicit `v is None` filter
        in `_persist_extra_props`), so a journal-side null is "absent", not a
        value;
      * nested ``provenance.source_id`` → the graph's flat ``provenanceSource``;
        nested ``operator.op_type`` → the graph's flat ``op_type``, plus the
        graph's derived ``is_operator`` — EXCEPT for a point whose own payload
        states ``is_operator: false`` (a mitigation, see below).

    ``#5048``: a MITIGATION is a NON-operator Point that still carries an
    ``operator`` EDGE descriptor (``#4937``) solely so the replay fold can
    rebuild its ``(m)-[:IMPL]->(op)`` half. Reading that descriptor back as a
    flat ``op_type`` re-typed the mitigation's JOURNAL view as an operator
    while the (faithfully replayed) GRAPH node carries no ``op_type`` — so
    ``check_consistency`` reported a FALSE ``divergence="content"`` on
    ``op_type`` for a rebuild that was in fact byte-faithful, and its advice
    ("reconcile by replaying the journal") could never clear it. The point's
    OWN explicit-False identity therefore outranks the descriptor, exactly as
    it does in ``projection/entities.py::_upsert_point_props``.

    #8006: the ``_POINT_FLATTEN_LIST_PROPS`` keys are normalized to the graph's
    canonical STRING form on BOTH sides, through the writer's own function. The
    journal records E3's ``list[str]`` while the node holds the space-joined
    string, so comparing the raw values would report a FALSE content divergence
    on every faithfully replayed point that carries one — and the graph's
    legacy ARRAY form (#5444, which the writer's fixup repairs) normalizes too,
    so a legitimate repair is not reported as a divergence. Presence is still
    compared: a journal list with NO graph value reports the key, which is the
    coverage the pre-#8006 ``uncarried`` classification did not have.

    Operator ``content``/``pointKind`` are NOT dropped here: `_compare_views`
    decides them (compared when both sides carry them, reported when one-sided),
    so the exclusion stays visible instead of becoming a silent hole.
    """
    if not isinstance(props, dict):
        return {}
    out = {
        k: v for k, v in props.items()
        if k not in _NOT_COMPARED and k not in skip and v is not None
    }
    # The writer's OWN normalization, so the checker's view of a point matches
    # what the writer makes of it (#8006).
    flatten_search_keys_prop(out)
    prov = props.get("provenance")
    if isinstance(prov, dict) and prov.get("source_id"):
        out["provenanceSource"] = prov["source_id"]
    op = props.get("operator")
    # Read the identity from the RAW payload, not from ``out``: ``skip`` may
    # legitimately exclude ``is_operator``, and reading it back out of ``out``
    # would then re-derive it from the descriptor (True) and undo this fix.
    # The rule is IMPORTED from the graph writer, not re-spelled: the checker's
    # view of a point must match what the writer makes of it, or the two
    # disagree only on the journal side — which is a false divergence.
    explicit_non_operator = is_non_operator_payload(props)
    if "is_operator" not in out:
        # A flat operator snapshot (OperatorPromoted) already carries the key;
        # never clobber it — only the nested payload needs the derivation.
        out["is_operator"] = (
            False if explicit_non_operator
            else (bool(op) if isinstance(op, dict) else False))
    if (isinstance(op, dict) and op.get("op_type")
            and not explicit_non_operator):
        out["op_type"] = op["op_type"]
    return out


def _as_f32(vec: list) -> list | None:
    """Round a vector to the store's `vecf32` width, or None if not numeric.

    The journal carries the ingest encoder's float64 (`encode_for_store`), while
    the node stores `vecf32($embedding)` — so an exact comparison of a faithful
    replay fails on the last mantissa bits. Compare at the stored width.
    """
    try:
        return [struct.unpack("<f", struct.pack("<f", float(x)))[0] for x in vec]
    except (TypeError, ValueError, struct.error, OverflowError):
        return None


def _uncarried(journal_by_id: dict) -> dict[str, dict[str, str]]:
    """Per point: journal content the projection cannot hold → ``{key: reason}``.

    Per POINT, not per key: a key that is list-valued on one point must not
    suppress the comparison of a same-named SCALAR on another (that made one
    point's list a global blind spot).

    The predicate is the writer's own: a key it explicitly handles
    (`_POINT_HANDLED`) or one already excluded with its own reason is not a
    "loss", and anything else that `_persist_extra_props` would drop (null,
    non-persistable value, undeclared list) is.

    #8006: a list-valued `_POINT_FLATTEN_LIST_PROPS` key is NOT a loss — the
    shared writer flattens it to the graph's string form, so the journal's list
    is carried (in its canonical form) rather than dropped. Excluding it here
    is what lets `_canonical_point_fields` compare it: leaving it out would have
    made the checker blind to a future regression on that very door.
    """
    out: dict[str, dict[str, str]] = {}
    for pid, props in journal_by_id.items():
        if not isinstance(props, dict):
            continue
        found: dict[str, str] = {}
        for k, v in props.items():
            if k == "tags":
                # #2897: the one `_POINT_HANDLED` key whose carriage depends on
                # the VALUE's type rather than the key's presence. Its writer's
                # clause is presence+`_store_holds_prop_value` gated
                # (`_stated_tags`), so a value the store cannot HOLD AS STATED
                # is genuinely uncarried and must be NAMED — the `_POINT_HANDLED`
                # short-circuit below would swallow it, and an unnamed drop
                # surfaced as an UNCLEARABLE `divergence="content"` whose
                # `action` ("replay the journal") produces exactly the graph that
                # is already there (#8317 review round 2 with a map; round 3 with
                # `[nan]`/`[NUL]`/`[2**70]`, all reproduced). The predicate is
                # the writer's EFFECTIVE one, NOT `_is_persistable_prop_value`:
                # the shape allowlist admits a non-finite float and a huge int,
                # which the transport gate nulls and a 4.16–4.20 engine clamps
                # (6.x refuses it at parameter parse) respectively. (Deliberately
                # a NARROW `tags` case, not a reorder
                # of the generic list/persistable tests above the
                # `_POINT_HANDLED` check: `provenance` is also
                # handled-and-dict-valued, and a generic reorder would newly flag
                # it.)
                if v is not None and not _store_holds_prop_value(v):
                    found[k] = _UNCARRIED_TAGS_UNWRITABLE
                continue
            if k in _POINT_HANDLED or k in _NOT_COMPARED:
                continue
            if v is None:
                found[k] = _UNCARRIED_NULL
            elif k in _POINT_FLATTEN_LIST_PROPS:
                # Carried in canonical form — not uncarried, whatever its
                # payload type (a list flattens, a str is already canonical).
                continue
            elif isinstance(v, list) and k not in _POINT_LIST_PROPS:
                found[k] = _UNCARRIED_CONTENT_PROPS.get(k, _UNCARRIED_LIST)
            elif not _is_persistable_prop_value(v):
                found[k] = _UNCARRIED_NON_PERSISTABLE
        if found:
            out[pid] = found
    return out


def _compare_views(journal_by_id: dict, graph_by_id: dict,
                   uncarried: dict | None = None,
                   projection=None) -> tuple[list[dict], int, int, dict, dict]:
    """The field-aware verdict, the per-id/field diagnosis, and what was excluded.

    Presence is compared for EVERY id (a ghost, or a lost write, is a
    divergence even when the SIZE is unchanged). Content is compared
    presence-conditionally — see the declared tables above.

    Returns ``(mismatches, divergent_count, compared_embeddings, excluded_seen,
    one_sided_seen)``

    — `mismatches` is CAPPED at `_MAX_DIVERGENT_POINTS` while
    `divergent_count` is the true total, so a caller must never derive the
    count from the list.

    Three rules are worth reading the code for rather than the summary:

    * the EMBEDDING is direction-sensitive. A graph-only vector is legitimate
      (the pre-#5004 recompute path for a strip-era journal); a JOURNAL-only
      vector is legitimate only where the writer declares a refusal (an operator
      node, or a width the store cannot hold, #5004) — anywhere else it is a
      divergence, which is the case a presence-conditional comparison used to
      lose;
    * the one-sided keys are reported, never silently skipped;
    * a graph-only `createdAt`/`expiredAt` is skipped because the writer's own
      fallback is a wall-clock timestamp — and the skip is reported.
    """
    uncarried = uncarried or {}
    mismatches: list[dict] = []
    divergent_count = 0
    compared_embeddings = 0
    excluded_seen: dict[str, str] = {}
    one_sided_seen: dict[str, str] = {}
    required_dim = getattr(projection, "required_embedding_dim", None)
    for pid in sorted(set(journal_by_id) | set(graph_by_id)):
        if pid not in journal_by_id or pid not in graph_by_id:
            divergent_count += 1
            if len(mismatches) < _MAX_DIVERGENT_POINTS:
                mismatches.append({"id": pid, "fields": ["__present__"]})
            continue
        jprops = journal_by_id.get(pid) or {}
        gprops = graph_by_id.get(pid) or {}
        # Report every excluded key actually present, so the un-modelled surface
        # is visible on a green run too.
        for k in set(jprops) | set(gprops):
            if k in _EXCLUSION_REASONS:
                excluded_seen.setdefault(k, _EXCLUSION_REASONS[k])
        skip = frozenset(uncarried.get(pid, {}))
        jf = _canonical_point_fields(jprops, skip)
        gf = _canonical_point_fields(gprops, skip)
        is_operator = bool(jf.get("is_operator") or gf.get("is_operator"))
        fields: list[str] = []
        for k in sorted(set(jf) | set(gf)):
            in_journal, in_graph = k in jf, k in gf
            if in_journal != in_graph:
                if k in _ONE_SIDED_REASONS:
                    one_sided_seen.setdefault(k, _ONE_SIDED_REASONS[k])
                    continue
                if k in _OPERATOR_ABSENT_PROPS and is_operator:
                    # #548 — the journal seam synthesizes them; the live operator
                    # node never has them. Compared when BOTH sides carry them
                    # (below), reported when only one does.
                    one_sided_seen.setdefault(k, _OPERATOR_ABSENT_REASON)
                    continue
            if not in_journal:
                # A graph-only key is faithful when the writer's own default put
                # it there, or when its value is a non-deterministic fallback.
                if k in _WRITER_DEFAULTS and _values_equal(
                        gf[k], _WRITER_DEFAULTS[k]):
                    excluded_seen.setdefault(
                        k, _WRITER_DEFAULTS_REASON)
                    continue
                if k in _GRAPH_DEFAULTED_PROPS:
                    excluded_seen.setdefault(k, _GRAPH_DEFAULTED_REASON)
                    continue
            if not (in_journal and in_graph) \
                    or not _values_equal(jf[k], gf[k]):
                fields.append(k)
        jvec = jprops.get("embedding") if isinstance(jprops, dict) else None
        gvec = gprops.get("embedding") if isinstance(gprops, dict) else None
        jvec_present = isinstance(jvec, list) and len(jvec) > 0
        gvec_present = isinstance(gvec, list) and len(gvec) > 0
        if jvec_present and gvec_present and len(jvec) == len(gvec):
            compared_embeddings += 1
            # The journal is the AUTHORITY on the storage FORM (`embedding_verbatim`
            # rides the journal payload). Reading it from the graph would let a
            # narrowed vector hide behind a dropped flag on its own node.
            verbatim = bool(jprops.get("embedding_verbatim")) \
                or bool(gprops.get("embedding_verbatim"))
            if verbatim:
                # Through the writer's OWN normaliser: a journal vector is a
                # FILE, so a non-numeric / non-finite / over-range element must
                # DEGRADE to "not equal" rather than raise inside the check
                # (`_writable_journalled_vector` handles exactly this class,
                # #5004/#19 — a bare `float()` raised ValueError/OverflowError
                # here and made the gate un-runnable until the journal was
                # hand-repaired). A non-writable vector on either side falls
                # back to a raw comparison, which cannot raise.
                jnorm = _writable_journalled_vector(jvec)
                gnorm = _writable_journalled_vector(gvec)
                if jnorm is not None and gnorm is not None:
                    same = jnorm == gnorm
                else:
                    same = _vec_raw_equal(list(jvec), list(gvec))
            else:
                jf32, gf32 = _as_f32(jvec), _as_f32(gvec)
                same = (jf32 is not None and jf32 == gf32)
            if not same:
                fields.append("embedding")
        elif jvec_present and gvec_present:
            # Both sides carry a vector, of DIFFERENT width. `replay(journal)`
            # cannot produce that (`n.embedding=vecf32($embedding)` writes the
            # journal's width), so the graph holds a vector the journal does not
            # describe — a divergence, not a near-miss (#4194/#4280).
            fields.append("embedding")
        elif jvec_present and not gvec_present:
            # DIRECTION MATTERS. A graph-only vector is faithful (the pre-#5004
            # recompute path for a strip-era journal), but a JOURNAL-only vector
            # is only faithful where the writer DECLARES a refusal — an operator
            # node (no content to rank) or a width the store cannot hold
            # (#5004, recorded decision). Anywhere else the graph lost a vector
            # the journal recorded, and a presence-conditional comparison let
            # that through.
            if is_operator:
                one_sided_seen.setdefault(
                    "embedding", "#5004 — the writer refuses a journalled "
                    "vector for an operator point (no content to rank)")
            elif required_dim is not None and len(jvec) != required_dim:
                one_sided_seen.setdefault(
                    "embedding", "#5004 — the writer refuses a journalled "
                    f"vector of width {len(jvec)} in a store of width "
                    f"{required_dim} rather than rewriting it")
            else:
                fields.append("embedding")
        if fields:
            divergent_count += 1
            if len(mismatches) < _MAX_DIVERGENT_POINTS:
                mismatches.append({"id": pid, "fields": sorted(set(fields))})
    return (mismatches, divergent_count, compared_embeddings,
            excluded_seen, one_sided_seen)


def _digest(views: dict) -> str:
    """Order-independent SHA-256 of a canonical (sorted, compact) JSON view."""
    canonical = json.dumps(
        views, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _entity_node_view(props: dict) -> dict:
    """The canonical view of ONE non-Point reference-fold node (#7936).

    Exactly the fields the entity leg DECIDES (``_ENTITY_BELIEF_FIELDS`` plus
    ``status``). ONE builder, read by both ``_graph_entities`` (the
    comparison) and ``_graph_fingerprint`` (the baseline) — a second hand-copied
    field list is how a widened writer drifts out of the baseline silently.

    The two consumers do NOT decide the same field set, and the difference is
    stated rather than flattened: the COMPARISON bounds ``status`` to
    ``Object``/``Subject`` (``entity_parity_bounds["status_compared"]`` — the
    ``Event`` leg folds no ``status`` lifecycle), while this view carries
    ``status`` on EVERY reference-fold label, so the BASELINE sees a ``status``
    write on an ``:Event`` too. Covering a field the comparison declares
    undecidable is deliberate on the baseline's side: a field nobody folds is
    exactly a field a graph-only write can move unnoticed.

    An ``:Event``'s ``status`` cannot manufacture a false red either, but NOT
    because the value is constant — in-tree Events DO carry it (``status`` is
    not in ``_EVENT_HANDLED``, so ``_persist_extra_props`` puts a
    writer-supplied ``status`` on the node, and ``sdk._promote_incident_operators``
    reads it as a live/draft gate; see the `_EVENT_HANDLED` note in
    ``projection/entities.py``). The reason is SYMMETRY: live and replay write
    that prop through the same ``_upsert_event`` → ``_persist_extra_props``
    path, and a live ``status`` change is journalled as ``EntityMutated``, so a
    reproduced value cannot move ``db_hash`` and the baseline only moves on a
    write ``replay(journal)`` cannot reproduce — the #4240 class it exists to
    catch. Read this as a bound on the WRITERS, not on the value.

    ``status`` is always present (``None`` when the node carries none) because
    the entity leg compares it presence-conditionally: a stub minted by
    ``_event_plain_merge`` has NO ``status`` at all (``MERGE … ON CREATE SET
    s.id=…, s._stub=true``), and a stub that later gains ``live`` is a real
    move — the digest must see it as one. ``_stub`` itself is deliberately NOT
    covered: it is a discriminator the canonical registration CLEARS, so
    including it would move the baseline on the adoption of a node the journal
    did describe.
    """
    rec = {"status": props.get("status")}
    for key in _ENTITY_BELIEF_FIELDS:
        if key in props:
            rec[key] = props[key]
    return rec


def _graph_fingerprint(graph_view: dict,
                      uncarried: dict | None = None) -> dict:
    """Pure GRAPH-only canonical view — the baseline a later run compares to.

    ``graph_view`` is the ONE ``(label, id)`` graph index (:func:`_graph_view`),
    covering every label the reference fold models — listed here from
    ``_GRAPH_VIEW_LABELS`` (the one home) rather than re-typed from memory:
    ``Point`` through ``_canonical_point_fields`` and every label in
    ``_GRAPH_VIEW_LABELS`` after it through :func:`_entity_node_view`.
    Keying it by label is what stops a non-Point node
    from being invisible to the baseline (#7936): with a Point-only index a
    graph-only ``Object``/``Subject``/``Event`` — or a belief/status rewrite on
    one — left ``db_hash`` unmoved, so the check reported a clean graph for a
    mutation ``replay(journal)`` cannot reproduce while the SAME mutation on a
    ``:Point`` was ``unrecorded-mutation``. Pinned by
    ``tests/test_7936_reference_fold_label_rekey.py``.

    The returned view is NESTED by label (``{label: {id: view}}``) rather than
    keyed by a tuple: :func:`_digest` hands this to ``json.dumps``, whose object
    keys must be strings — a tuple key raises ``TypeError``. Nesting is also the
    unambiguous form: a ``:Point`` and an ``:Event`` may legitimately share an
    id (#7936 P2), so a flat ``{id: view}`` would collapse them.

    Covers the same declared content plus the embedding, so a vector-only
    rewrite of an otherwise-identical graph still moves the baseline (and so
    cannot hide behind the presence-conditional embedding comparison).

    It reads ONLY the graph, but it is NOT independent of the journal: one skip
    set below (`uncarried`) is journal-derived, deliberately — a digest that
    ignored it would move on every legitimate repair of an uncarried list prop
    and report that repair as an unrecorded mutation. The journal dependence is
    therefore narrow and declared, not absent.

    The keys the COMPARISON declares undecidable are dropped here too, because a
    DECLARED asymmetry must not move the baseline and be read as an unrecorded
    mutation:

      * the one-sided parity keys — a live-only `speaker`/`embedding_verbatim`;
      * `_GRAPH_DEFAULTED_PROPS` — the non-deterministic writer fallbacks the
        journal never states (the baseline is what catches a rewrite of those);
      * the per-point `uncarried` keys — an undeclared list survives on a LIVE
        graph and is DROPPED by the replayed writer (#2795), so without this a
        repair reads as an unrecorded mutation for ever;
      * the operator `content`/`pointKind` (#548) — the live node lacks them and
        a replayed one has them.

    KNOWN BOUND, stated rather than implied: a graph-only change to a key in
    those skip sets cannot be attributed to a journal delta once the journal has
    advanced, so it can neither be flagged nor distinguished from the legitimate
    live/replay asymmetry. The sets are REPORTED on every run
    (`uncarried_journal_fields`, `one_sided_fields`, `excluded_fields`), so the
    bound is visible; the follow-up that would close it (per-point hashes
    attributed to the journal delta) is filed separately.
    """
    uncarried = uncarried or {}
    out: dict = {}
    for (label, nid), props in graph_view.items():
        props = props if isinstance(props, dict) else {}
        if label != "Point":
            out.setdefault(label, {})[nid] = _entity_node_view(props)
            continue
        skip = set(_ONE_SIDED_REASONS) | set(_GRAPH_DEFAULTED_PROPS)
        skip |= set(uncarried.get(nid, {}))
        view = _canonical_point_fields(props, frozenset(skip))
        if props.get("is_operator"):
            for k in _OPERATOR_ABSENT_PROPS:
                view.pop(k, None)
        if isinstance(props.get("embedding"), list):
            view["embedding"] = props["embedding"]
        out.setdefault(label, {})[nid] = view
    return out


# ── #5011: the watermark + baseline, stored WITH the reconstruction source ──
#
# A sidecar next to the event log — the same shape `recover_from_log` already
# reads for the #2943 pre-wipe snapshot — NOT a graph node. A graph node would
# have to be excluded from every node-counting surface (the export skip set in
# `hosted_api.py`, the DR dump's data-node count, `migrate_db`, `backup`'s RDB
# probe, the recovery emptiness test), and `_EXPORT_SKIP_LABELS` is outside this
# lane's file family. A sidecar touches none of them.
#
# ⚠️ This OVERRIDES the design decision recorded on #5011 (§3), which specified a
# `:ProjectionState` node in the projection's own graph; the issue carries the
# `OVERRIDES:` line and the reason (the export skip set is out of family). The
# override has a CONSEQUENCE that the node form did not: a rebuild wipes the
# graph and so would have cleared a node baseline, but nothing clears this file —
# so a repair that lands without a check makes the next run report
# `unrecorded-mutation` until the operator re-baselines, and that divergence's
# `action` names the repair case.
#
# Trade-off, stated: the state is keyed to the JOURNAL path, so it is
# per-projection where one journal feeds one projection — every supported
# deployment shape, and the journal is the reconstruction source the invariant
# names.
_PROJECTION_STATE_SUFFIX = ".projection-state.json"
# The format HISTORY, oldest first. `_PROJECTION_STATE_FORMAT` is its LAST
# entry; every earlier one is a KNOWN PREDECESSOR the migration below
# re-baselines instead of refusing for ever.
#
# ⛔ BUMPING THE FORMAT MEANS APPENDING HERE. A new format whose predecessor is
# not appended makes that predecessor an UNKNOWN format, which falls back to the
# pre-migration reading (UNTRUSTED, baseline inert for ever) — the fail-safe
# direction, and the suite's `_PROJECTION_STATE_FORMAT - 1` predecessor fixture
# reds loudly when it happens, so the omission cannot ship silently.
#
# v1 → v2 (#2897): the per-point canonical view widened by `tags`. It left
# `_UNCARRIED_CONTENT_PROPS` so the field-aware comparison would SEE it, and the
# same view feeds `_graph_fingerprint` — so `projection_hash_sha256` MOVES for
# every existing baseline whose graph holds a tag. Without a migration the next
# run would find `journal_moved=False, graph_moved=True` and report
# `unrecorded-mutation` on a healthy graph — an `action` telling the operator to
# hunt an unjournalled writer that does not exist.
#
# #8268/#7936: the bump ALONE was the precursor, and it was not enough. A
# superseded format was read as untrusted, and `record_projection_state` is
# gated on `state_error is None` — so the baseline half stayed INERT for ever
# and the migration was unreachable. Recognising a well-formed predecessor and
# re-baselining it is what makes a digest widening landable at all, and it is
# the prerequisite #7936's `(label, id)` re-key names: that is a SECOND widening
# of the same digest and needs `_PROJECTION_STATE_FORMAT` → 3 (append `3`; `2`
# then migrates too, exactly like `1` does now).
#
# v2 → v3 (#7936): the `(label, id)` re-key. `_graph_fingerprint` now folds the
# WHOLE graph index (`_graph_view`: Point + Object/Subject/Event), so
# `projection_hash_sha256` moves again for every existing baseline that holds a
# non-Point node — which is every real graph. Same consequence as v1 → v2,
# same answer: append the format, let a v2 sidecar migrate on the next healthy
# recording run. Without the bump the first post-upgrade run would read a
# healthy graph as `journal_moved=False, graph_moved=True` — an
# `unrecorded-mutation` whose `action` sends the operator hunting an
# unjournalled writer that does not exist.
_PROJECTION_STATE_FORMAT_HISTORY: tuple[int, ...] = (1, 2, 3)
_PROJECTION_STATE_FORMAT: int = _PROJECTION_STATE_FORMAT_HISTORY[-1]
_PROJECTION_STATE_SUPERSEDED_FORMATS: frozenset[int] = frozenset(
    _PROJECTION_STATE_FORMAT_HISTORY[:-1])


def projection_state_path(log_path) -> str:
    """The sidecar path for a journal. `.json`, never `.jsonl`, so it can never
    be mistaken for a second journal by `recover_from_log`'s single-log check."""
    return str(log_path) + _PROJECTION_STATE_SUFFIX


def baseline_warning(result: dict, log_path) -> str | None:
    """The operator-visible warning for a STRANDED baseline, or ``None``.

    #2897 review round 2: ``check_consistency`` can return ``ok=True`` while the
    BASELINE half of the invariant is inert. An untrusted sidecar — a torn file,
    a planted FIFO/symlink, a malformed watermark — makes
    :func:`read_projection_state` error, so ``stored`` is ``None`` and every run
    ADOPTS. ``state_recorded`` is gated on ``state_error is None``
    (deliberately: recording over an untrusted state would destroy the only
    evidence of what the graph looked like before), so the state is never
    replaced either. The green verdict says nothing about any of that, which is
    how an inert guard reads as a healthy graph — so the surface names it, with
    the recovery, instead of leaving it to a field the CLI never printed.

    #8268/#7936: a WELL-FORMED predecessor format is no longer in that class —
    it is MIGRATED (re-baselined at the current format) on a healthy recording
    run, so it is reported as the migration it is, not as an inert/stranded
    guard. The only remaining inert case is a read-only run
    (``record_state=False``), where the predecessor is still awaiting its
    re-baseline.

    A function rather than inline prose in ``__main__`` so the surface is
    PINNED by a test without needing a live URI (the CLI's ``--db`` resolves
    only ``docker``/``redis``/``rediss``).
    """
    superseded = result.get("state_superseded_format")
    if superseded is not None and not result.get("state_error"):
        if result.get("state_recorded"):
            return (
                f"projection baseline MIGRATED (format {superseded} -> "
                f"{_PROJECTION_STATE_FORMAT}) — the recorded digest was written "
                "by an older format, so it was re-baselined on this healthy "
                "run. A graph write that bypassed the journal BEFORE this run "
                "cannot be seen by the new baseline."
            )
        return (
            f"projection baseline SUPERSEDED (format {superseded} -> "
            f"{_PROJECTION_STATE_FORMAT}) and the baseline half is INERT until a "
            "RECORDING run re-baselines it — a graph write that bypasses the "
            "journal cannot be detected while it is inert. Re-run with recording "
            "enabled to migrate it."
        )
    error = result.get("state_error")
    if not error:
        return None
    return (
        f"projection baseline UNTRUSTED ({error}) — the baseline check is INERT "
        "until it is re-baselined, so a graph write that bypasses the journal "
        "cannot be detected on this graph. Confirm no unjournalled writer "
        f"exists, then remove {projection_state_path(log_path)} and re-run to "
        "record a fresh baseline."
    )


def read_projection_state(log_path) -> tuple[dict | None, str | None]:
    """Read ``(state, error)`` for a journal.

    ``(None, None)`` means "no TRUSTED baseline" — never baselined (the normal
    ADOPTION case), OR a well-formed predecessor format awaiting migration
    (:func:`read_projection_state_detail` reports that predecessor separately;
    this view collapses it to "no trusted baseline" so a caller that cannot
    migrate still adopts rather than mistaking version skew for corruption).
    ``(None, "<why>")`` means a baseline exists but cannot be trusted; the
    caller must not treat that as a divergence, and must not silently adopt
    over it either, so the reason is reported.
    """
    state, error, superseded = read_projection_state_detail(log_path)
    if superseded is not None:
        return None, None
    return state, error


def read_projection_state_detail(
        log_path) -> tuple[dict | None, str | None, int | None]:
    """``(state, error, superseded_format)`` for a journal.

    The third element is the format of a WELL-FORMED predecessor sidecar
    (#8268/#7936) — version skew, not evidence of corruption. It is reported so
    ``check_consistency`` can MIGRATE it (re-baseline at the current format on
    the next healthy run) instead of reading it as untrusted and leaving the
    baseline half of the invariant inert for ever.

    A malformed sidecar (a bad watermark/digest, a FIFO, a symlink, a torn
    file, an UNKNOWN format) keeps ``superseded_format = None`` and stays
    exactly as untrusted as it was: "never overwrite a baseline we could not
    read" is unchanged for anything that is not a known, well-formed
    predecessor.
    """
    path = projection_state_path(log_path)
    try:
        # `lstat`, then a regular-file check: a FIFO planted at this path would
        # hang an unbounded `open()` read, and a symlink would read a file the
        # sidecar does not own. Both are operator-tier (the journal DIRECTORY is
        # writable), and both are cheap to refuse.
        st = os.lstat(path)
    except FileNotFoundError:
        return None, None, None
    except OSError as e:
        return None, f"projection state unreadable: {e}", None
    if not stat.S_ISREG(st.st_mode):
        return None, "projection state is not a regular file", None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as e:  # any unreadable sidecar is untrusted
        return None, f"projection state unreadable: {e}", None
    if not isinstance(data, dict):
        return None, "projection state is not a JSON object", None
    seq = data.get("last_applied_seq")
    digest = data.get("projection_hash_sha256")
    fmt = data.get("format_version")
    # The FORMAT gate runs first so its message is the one reported, but a known
    # predecessor is NOT thereby exempt from the structural gate below:
    # `superseded_format` is a verdict on a WELL-FORMED older sidecar, never a
    # bypass of the malformed/tampered checks. An unknown format (0, 99, a
    # string, `true`, `1.0`, a FUTURE version written by newer code) is
    # untrusted — an older build must never overwrite a newer baseline.
    #
    # `isinstance(fmt, bool)`/`not isinstance(fmt, int)` FIRST, before the
    # membership test: `_PROJECTION_STATE_SUPERSEDED_FORMATS` is a frozenset of
    # ints and `in` binds by `==`/`hash`, so `True in frozenset({1})` and
    # `1.0 in frozenset({1})` are BOTH True. Without the type check a boolean
    # or float `format_version` is coerced into the known-predecessor set and
    # MIGRATED (re-baselined and OVERWRITTEN) instead of refused — the one
    # class the docstring above promises is still treated as untrusted. Same
    # strict-bool rule as the watermark and `_values_equal`, on the path that
    # reads operator-planted sidecars.
    if isinstance(fmt, bool) or not isinstance(fmt, int) \
            or (fmt != _PROJECTION_STATE_FORMAT
                and fmt not in _PROJECTION_STATE_SUPERSEDED_FORMATS):
        return None, (f"projection state format {fmt!r} "
                      f"is not {_PROJECTION_STATE_FORMAT}"), None
    # `isinstance(seq, bool)` first: `isinstance(True, int)` is true, so a
    # boolean watermark would otherwise be accepted as a valid baseline (and
    # `True == 1` would then trust it against a 1-event journal). Same strict
    # bool rule as `_values_equal`, on the path that reads operator-planted
    # sidecars.
    if isinstance(seq, bool) or not isinstance(seq, int) \
            or not isinstance(digest, str) \
            or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return None, f"projection state malformed (seq={seq!r})", None
    if fmt != _PROJECTION_STATE_FORMAT:
        return None, (f"projection state format {fmt!r} is a superseded "
                      f"predecessor of {_PROJECTION_STATE_FORMAT}"), fmt
    return {
        "last_applied_seq": seq,
        "content_hash": digest,
        "format_version": fmt,
        "updatedAt": data.get("updatedAt"),
    }, None, None


def record_projection_state(log_path, *, last_applied_seq: int,
                            content_hash: str) -> bool:
    """Atomically write the watermark + baseline digest next to the journal.

    Best-effort: a read-only directory must not fail the check, so the outcome
    is returned (and reported) rather than raised.
    """
    if not re.fullmatch(r"[0-9a-f]{64}", content_hash or ""):
        return False
    payload = {
        "format_version": _PROJECTION_STATE_FORMAT,
        "last_applied_seq": int(last_applied_seq),
        "projection_hash_sha256": content_hash,
        "updatedAt": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
    }
    path = projection_state_path(log_path)
    # Per-process tmp: two concurrent runs sharing one tmp path would interleave
    # and publish a TORN sidecar, and the next run would read it as unreadable
    # and (correctly) refuse to re-baseline — permanently disabling the baseline
    # half. `O_EXCL|O_NOFOLLOW` also refuses a planted symlink/FIFO at the tmp
    # path (`embedded_reaper.py` uses the same per-pid convention).
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                     0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, sort_keys=True, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
        return True
    except Exception as e:  # report, never raise (best-effort)
        logger.warning("could not record projection state at %s: %s", path, e)
        with suppress(OSError):
            os.unlink(tmp)
        return False


# The BELIEF half of a terminalizing write: `decay_clause` (live.py) appends
# `confidence=0.5, posterior_alpha=1.0, posterior_beta=1.0` — crash-atomic with
# the status/flag write it rides. The replay folds the same values (at the
# event's own seq, per #2884), so the journal side must too.
_DECAY = {"confidence": 0.5, "posterior_alpha": 1.0, "posterior_beta": 1.0}


class _RestampSignatureError(TypeError):
    """#7719: the terminalizer consumer was called with a bad signature.

    Distinct from a bare ``TypeError`` so ``recover_from_log``'s per-record
    ``except Exception`` cannot degrade a programming error into a ``torn``
    skip — which would leave ``recovered=True`` standing over a journal whose
    terminalizers were never folded (the exact false PASS #7719 closes).
    """


def _fold_journal(events: list[dict]) -> dict:
    """The journal side of the comparison: the writer's own fold, PLUS the
    lifecycle arms `fold` does not have.

    `fold` is the in-memory POINT-only index. It HAS an arm for `PointRetracted`
    (status, the vacuity belief, and the #5048 recorded `updatedAt` — no belief
    decay), and none for `PointPromoted`,
    `OperatorPromoted`, `PointSuperseded` or `PointInvalidated` — a documented,
    intentional scope gap its own `_NO_POINT_FOLD` names (#3692 records the same
    four). The GRAPH writer folds all of them: `apply()` for the promotions,
    and `rebuild_all`'s deferred pass for supersede/invalidate, so a reference
    built on `fold` alone reports a healthy graph as diverged on any promotion
    and on any retract/supersede/invalidate.

    ⚠️ `recover_from_log` does NOT reach `rebuild_all` on its normal path — it
    replays through `projection.apply()`, which has no arm for
    `PointSuperseded`/`PointInvalidated` (#3305) and so cannot rebuild the props
    this arm folds. The reference is still `rebuild_all`'s full-fidelity replay
    (it is what the invariant `derived = replay(journal)` means), but a graph
    repaired that way stays diverged until #3305 lands, and the returned
    `action` says so rather than sending the operator round a repair loop.

    Folded in ONE ORDERED PASS, deliberately: the writer applies each
    terminalizer's decay at its own journal position (#2884 A3 — the fold used
    to apply it post-hoc and clobbered every later belief writer), so a
    PointRevised AFTER a retract must win. Taking `fold(events)` and then
    re-applying the lifecycle arms over the whole list would reintroduce the
    exact defect the writer removed.

    Ground truth is `_apply_one` — the same function `fold` loops over — so the
    base types cannot drift from the writer's fold; only the four missing arms
    are ours:

      PointPromoted / OperatorPromoted  — upsert the snapshot (with the
        operator-ness synthesis that prevents a silent operator→claim
        conversion). Merge, not replace: `_upsert_point_props` only SETs the
        keys the payload carries, except `content`/`is_operator`/`op_type`,
        which it sets UNCONDITIONALLY — so a snapshot that omits those RESETS
        them, and this arm pins them the same way.
      PointRetracted  — `_retract` (`_apply_one`'s arm sets the same three:
        status, `VACUITY_BELIEF`, and the #5048 recorded `updatedAt`):
        `status='retracted'` + the `decay_clause` belief decay, which is why
        this arm exists at all rather than deferring to `_apply_one`.
      PointSuperseded — `_fold_point_superseded`: requires `new_id` (a
        PointSuperseded without it is a no-op on the graph), then
        `status='superseded'`, `outdated=true`, `validTo`/`expiredAt` from the
        payload, + the decay.
      PointInvalidated — `_fold_point_invalidated`: `outdated=true`,
        `validTo`/`expiredAt`, + the decay (no status write).

    A terminalizer is also gated on the id's last hard delete seq (as the writer
    is): one at or before that boundary belongs to a dead incarnation. The
    ordered pass is what actually makes the recreate case correct — a later
    PointAdded replaces the entry outright — so the gate is defence in depth
    that mirrors the writer's own boundary rather than the load-bearing half.

    The right long-term fix is the arms on `fold` itself and lives outside this
    lane's file family (#3692 covers the promotions).

    #3305 KNOWN GAP: these terminalizer arms are NOT driven by
    ``plan_point_restamp_folds``, the selection the replay engines obey, so this
    reference fold can disagree with a correctly replayed graph. Measured on a
    bare same-id ``PointAdded`` re-emit after an invalidate: the graph holds the
    decayed belief (both engines keep it — a bare re-emit MERGEs live), while
    this fold's ``_apply_one`` PointAdded arm REPLACES the entry and drops the
    decay, so ``check_consistency`` reports ``divergence="content"`` on
    ``confidence``/``posterior_alpha``/``posterior_beta`` for a healthy replay.
    That disagreement with the ``rebuild_all`` graph predates #3305; the #3305
    fix widened it to the apply() arm by making that arm agree with
    ``rebuild_all``. Driving these arms from the plan is the durable fix and is
    deliberately left to the consistency lane.
    """
    # `journal_hard_delete_seqs` normalises internally, and stays on the RAW
    # list (the anchor boundary is an envelope property).
    anchors = journal_hard_delete_seqs(events)
    # #7719: the anchor-gated MEMBERSHIP map, hoisted ONCE beside ``anchors`` —
    # never rebuilt inside the per-event loop (that is the O(N²) class this
    # file already fixed). ``anchors`` remains the ORDERED-boundary reader; the
    # gated map is the exemption predicate shared with the graph engines.
    hard_deleted = hard_deleted_pairs(events)
    # #3585 re-review: the same journal-wide existence map `fold` passes — an
    # order-dependent refusal (a belief/annotator write, a terminalizer) is
    # mirrored only when the target is NOT a forward reference. The ORDERED map
    # (first materialization seq), not a creation-id set, is what lets a
    # create→hard-delete→fold journal still refuse.
    _first_materialized = journal_first_materialization(events)
    # #7936 review: the ORDERED creation/hard-delete boundary, hoisted ONCE
    # beside the existence map. `_apply_one`'s non-Point belief exemption asks
    # whether the target EXISTS at the record — a first-materialization map plus
    # a last-delete-wins map cannot express a re-creation or a delete on either
    # side of the write, so the reference verdict would disagree with
    # `rebuild_all`.
    _epistemic_existence = journal_epistemic_existence(events)
    # #8317 review round 3: the re-creation boundary, so `_apply_one` preserves a
    # prop (`tags`) the payload states no WRITABLE value for on a bare re-emit,
    # and drops it on a real delete→recreate — the writer's own MERGE-vs-wipe
    # semantics.
    _recreate_seqs = journal_recreate_seqs(events)
    by_id: dict = {}
    for seq, ev in enumerate(events):
        # Same normalisation the writer applies (`_apply_one`/`apply`): the
        # nested envelope shape is NOT a difference, so a terminalizer written
        # as `{"type": ..., "point": {...}}` must not be read as id-less and
        # dropped — that made a healthy graph report a `content` divergence and
        # advise a wipe+replay (#3722).
        ev = _norm(ev)
        t = ev.get("type")
        if t in ("PointPromoted", "OperatorPromoted"):
            p = ev.get("point")
            if isinstance(p, dict) and p.get("id"):
                if t == "OperatorPromoted":
                    p = _promotion_point_with_operator(p)
                entry = by_id.get(p["id"])
                if isinstance(entry, dict):
                    merged = dict(entry)
                    if (t == "PointPromoted"
                            and not promote_status_applies(
                                entry.get("status"))):
                        # #6239: live's promote emitters are gated on a
                        # ``draft``/status-less Point, so a ``PointPromoted``
                        # replayed over a terminal status (a supersede is
                        # status-TERMINAL) is a status NO-OP — fold the rest of
                        # the snapshot, withhold the status column. Without
                        # this the reference fold re-lived the Point while the
                        # rebuilt graph kept it terminal, and
                        # ``check_consistency`` red a faithful replay. Same
                        # predicate as the graph engines, so the two cannot
                        # drift (see ``promote_status_applies``).
                        merged.update({k: v for k, v in p.items()
                                       if k != "status"})
                    else:
                        merged.update(p)
                    # The writer's NON-coalesce SET clauses: an omitted key is
                    # RESET, not preserved (`n.content=$content` with
                    # `p.get("content", "")`; `n.is_operator=$isop` /
                    # `n.op_type=$opt` from the NESTED operator key).
                    merged["content"] = p.get("content", "")
                    if "operator" not in p:
                        merged.pop("operator", None)
                        merged["is_operator"] = False
                        merged["op_type"] = None
                    by_id[p["id"]] = merged
                else:
                    by_id[p["id"]] = p
            elif t == "OperatorPromoted":
                # `apply()`'s fallback arm: no snapshot means a status-SET on an
                # existing node (a MATCH-SET that no-ops when it is absent).
                # The writer's replay reads the id as `id or event_id` (both
                # `apply()` and `rebuild_all`), so this fold MUST read it the
                # same way: reading only `id` makes a faithful graph -- one the
                # writer's own replay produced -- report as `content` divergent.
                oid = ev.get("id") or ev.get("event_id")
                entry = by_id.get(oid) if isinstance(oid, str) else None
                if isinstance(entry, dict):
                    entry["status"] = "live"
            continue
        if t in ("PointRetracted", "PointSuperseded", "PointInvalidated"):
            pid = ev.get("id")
            if not isinstance(pid, str):
                continue
            # The writer's delete/recreate boundary: a terminalizer at or
            # before the id's last hard delete must not touch the new
            # incarnation.
            if (anchors.get(pid, {}).get("Point") or -1) >= seq:
                continue
            if t == "PointSuperseded" and not ev.get("new_id"):
                # #3585: the NAMED exemption — the graph fold treats a
                # new_id-less supersede as a documented no-op, so the
                # reference fold mirrors it. Recorded, not fatal.
                record_non_folded(
                    SHAPE_POINT_SUPERSEDED_NO_NEW_ID,
                    event_id=ev.get("event_id"),
                    event_type=t, seq=seq,
                    detail="reference fold: supersede with no new_id is a no-op",
                )
                continue
            entry = by_id.get(pid)
            if not isinstance(entry, dict):
                # #3585 (R8): the reference fold could not resolve the
                # terminalizer to a point — the journal claims a state change
                # this fold cannot produce. Recording it is what makes the
                # #5011 content comparison non-vacuous.
                #
                # #3585 review: a supersede/invalidate whose target the journal
                # HARD-DELETED earlier is the GRAPH fold's named exemption too
                # (the deferred sweep runs after the delete, so a 0-row match is
                # legitimate). Refusing it here made `check_consistency` red on
                # exactly the journal `rebuild_all` accepts — the two classifiers
                # must agree, or the fail-closed set is not one set.
                _del_seq = anchors.get(pid, {}).get("Point")
                # #7719: the exemption predicate is the SHARED anchor-gated map,
                # with the EXISTING ordered boundary retained as an ADDITIONAL
                # exemption — the journal [PointAdded X(0), delete X(1),
                # PointSuperseded X→Y(2), PointAdded X(3)] is GREEN on both
                # engines today, and dropping the ordered test would refuse a
                # record the shared plan DROPS.
                _target_deleted = (
                    _hard_deleted_any(hard_deleted, "Point", pid)
                    or (isinstance(_del_seq, int) and _del_seq < seq))
                if (_target_deleted
                        and t in ("PointSuperseded", "PointInvalidated")):
                    record_non_folded(
                        classify_terminalizer_miss(
                            t, has_successor=bool(ev.get("new_id")),
                            target_deleted=True),
                        event_id=ev.get("event_id"), event_type=t, seq=seq,
                        id=pid,
                        candidates=(ev.get("new_id"), ev.get("corrected_by")),
                        detail=("reference fold: target hard-deleted earlier "
                                "(graph fold's named exemption)"),
                    )
                    continue
                # #3585 (P1-1): a terminalizer whose target the JOURNAL
                # materializes only LATER is a forward reference — the live
                # write no-op'd it and `rebuild_all`'s creation hoist skips
                # it, so refusing it here reds a journal the graph reproduces
                # exactly. The two named exemptions above are unaffected; the
                # ORDERED map keeps a create→hard-delete→terminalizer journal
                # (first <= seq) a genuine miss.
                if _journal_forward_reference(
                        _first_materialized, seq, "Point", pid):
                    continue
                record_non_folded(
                    classify_terminalizer_miss(
                        t, has_successor=bool(ev.get("new_id")),
                        target_deleted=_target_deleted),
                    event_id=ev.get("event_id"), event_type=t, seq=seq, id=pid,
                    candidates=(ev.get("new_id"), ev.get("corrected_by")),
                    detail="reference fold: terminalizer matched no point",
                )
                continue
            entry.update(_DECAY)
            if t == "PointRetracted":
                entry["status"] = "retracted"
                # #5048: `updatedAt` is RECORDED, and this is the reference
                # fold `check_consistency` compares the graph against — its own
                # docstring says ground truth is `_apply_one`, which now stamps
                # this field. Same predicate as both replay arms
                # (`_usable_instant`, NOT bare `_writable_id`, which accepts
                # the empty string), and only "when the journal states it": a
                # writer's wall-clock fallback states nothing, exactly as
                # `expired_at` below. (Without this the reference disagreed
                # with BOTH `fold()` and the rebuilt graph on the one field
                # this change makes recorded — invisible only while the field
                # is excluded from comparison.)
                if _usable_instant(ev.get("ts")):
                    entry["updatedAt"] = ev["ts"]
            elif t == "PointSuperseded":
                entry["status"] = "superseded"
            if t in ("PointSuperseded", "PointInvalidated"):
                entry["outdated"] = True
                entry["validTo"] = ev.get("valid_to")
                # #2350: `validToPrecision` rides the SAME node the live writers
                # stamp (`sdk.py` `invalidate_point`/`supersede_point` SET
                # `n.validToPrecision`, and the graph/replay folds restore it).
                # Mirroring `validTo` keeps this reference (the ground truth
                # `check_consistency` compares the graph against) able to state
                # it: without this the key was graph-ONLY, in neither
                # `_EXCLUSION_REASONS` nor `_ONE_SIDED_REASONS`, so a health
                # check reddened every precision-carrying terminalizer with a
                # divergence its own advice could not clear — the #5048/#3817
                # class. A `None` value is dropped by `_canonical_point_fields`
                # (the writer cannot persist a null property), so the legacy
                # no-precision path stays symmetric-absent.
                entry["validToPrecision"] = ev.get("valid_to_precision")
                # Only when the journal states it: the writer falls back to a
                # wall-clock `_now_iso()`, which the journal cannot state.
                if ev.get("expired_at"):
                    entry["expiredAt"] = ev["expired_at"]
            continue
        _apply_one(by_id, ev,
                   journal_first_materialized=_first_materialized,
                   journal_seq=seq,
                   journal_existence=_epistemic_existence,
                   journal_recreate=seq in _recreate_seqs)
        # #4208: a content EDIT re-derives the vector. The live `update_point`
        # and the replay's `_revise_point` both re-encode from the new content,
        # so the journal states no vector for the edited point — yet the entry
        # `_apply_one` just mutated still carries the CREATION vector, which the
        # graph no longer holds. Dropping it keeps this reference honest: the
        # comparison's own embedding arm already declares a graph-only vector
        # faithful for the recompute path (see the `jvec_present and not
        # gvec_present` note), while leaving the stale creation vector in made a
        # healthy content edit read as a `content` divergence on EVERY update.
        # Mirrors the static `content_hash` exclusion ("pure f(content) —
        # RECOMPUTE"): the gate is `_apply_one`'s OWN content gate (normalize,
        # then `new_content is not None and _annotator_value_ok`), read from the
        # same projection function rather than re-spelled, so a content value
        # the fold refused cannot drop a vector here.
        #
        # #5238 review P2: the drop is correct ONLY when the vector was
        # DERIVED — i.e. the record owns no `embedding`. When a caller supplies
        # BOTH `content` and `embedding` (a supported #5046 shape), the vector
        # is caller-owned, NOT recomputed: the graph holds the caller's vector
        # while `replay(journal)` re-encodes (the declared #5046 exemption), so
        # live != rebuild. Popping here would delete the fold's only vector,
        # the comparison would declare the caller-owned graph-only vector
        # faithful for a recompute path it is not on, and the durability gate
        # would go blind to a real divergence. Leaving the fold's existing
        # (creation) vector in place is the honest signal the fold CAN give:
        # it does not equal the caller-owned graph vector, so the divergence is
        # reported. (Writing the record's claimed vector onto the fold instead
        # would make both sides equal — but `replay` does NOT honour that
        # vector, so the fold would lie about replay and hide the divergence
        # for ever.) `"embedding" not in` is deliberate: an explicit
        # `embedding: null` clear is a caller write too, and the same blindness
        # applies.
        if t == "PointRevised":
            _nev = _norm(ev)
            _nc = _nev.get("new_content")
            if (_nc is not None and _annotator_value_ok(_nc)
                    and "embedding" not in _nev):
                # #5238 review P3: `_apply_one` guards every id lookup with
                # `_writable_id` (#331 review r4); a JSON-legal non-string id
                # (list/dict) must degrade to "no entry", not raise
                # `TypeError: unhashable type` out of the durability fold.
                _rid = _nev.get("id")
                _entry = by_id.get(_rid) if _writable_id(_rid) else None
                if isinstance(_entry, dict):
                    _entry.pop("embedding", None)
    return by_id


# ── #3585: entity parity — the invariant's non-Point leg ─────────────────
#
# `_fold_journal` above is POINT-only (it returns a `{id: point}` index), so
# before #3585 the `rebuild == live` invariant was structurally blind to a
# non-Point burial: shapes A and B of #3573 flip an OBJECT's `status`, and the
# point-only check cannot see the Object at all. This leg closes that hole for
# the fields a burial actually moves — entity PRESENCE and `status`.
#
# Deliberate bounds:
#   * `Source` is excluded — its graph identity is ``url`` while the journal's
#     delete/mutation records carry ``id`` (#4649's url-keyed no-op class), so
#     a presence comparison there would be a false positive by construction.
#   * Only `status` is compared, not `name` — `#3574`'s ``name[:200]``
#     truncation is a known journal/graph asymmetry.
#   * Object/Subject PRESENCE is resolved by NAME as well as id, because the
#     graph MERGEs those labels by name (`_upsert_object`/`_upsert_subject`):
#     two journal registrations of one name under different ids are ONE node
#     carrying the LAST id, and an id-only key would report the older one
#     `absent-from-graph` on a correctly-replayed graph.
#   * A name-only `ObjectSuperseded` (no id, >1 carrier) leaves the object's
#     status AMBIGUOUS: the fold's own resolution is heuristic, so those
#     objects' STATUS leg is excluded from the comparison — PRESENCE is still
#     compared — and the exclusion is REPORTED to the caller
#     (`entity_parity_ambiguous*`), never silently absorbed. A name-only
#     supersede with NO carrier at all is NOT ambiguity: it records a refused
#     `object-superseded-miss`, matching the graph fold.
#   * The COMPARISON runs in ONE direction, journal→graph (`entity_parity_bounds`
#     states it): a node the GRAPH holds with no journal record is not a CONTENT
#     divergence here. That is not a licence to hold such a node, it is a
#     division of labour: a graph-only node is the baseline's question
#     (`unrecorded-mutation`, #7936), not the field comparison's — the baseline
#     is RECORDED on the first healthy run, so a pre-existing
#     graph-only node from a legacy/unscoped writer is baselined once instead
#     of red-ing every run, while a CHANGE to the entity set with a still
#     journal is reported. Widening the COMPARISON instead would report every
#     such node on every run, with no way to tell a legacy graph from a fresh
#     burial. On a journal-backed projection (#7951 §17.1) the FOLD and the
#     public raw-query lane are REFUSED outright, so the sanctioned writers of
#     a journal-backed graph journal first — a graph-only node there is the
#     #4240 bypass class the baseline exists to surface. ⛔ But that refusal is
#     NOT complete, and this bound must not be read as if it were: a mutating
#     statement issued straight at the guarded handle (`proj.g.query(...)` —
#     also the live lane of every SDK writer) is not refused, because
#     converting that 300+-site surface IS §17.1's atomic write, still open on
#     #7951 (`tortoise/projection/__init__.py` states the same scope, so the
#     two cannot disagree). Journalling first is therefore a CONVENTION for
#     that lane, and that is why the baseline REPORTS a graph-only entity
#     belief/`status` write instead of the guard preventing it.
#   * ACCEPT/REFUSE parity is JOURNAL-WIDE; CONTENT parity is not, for one
#     DEFERRED fold. `rebuild_all` folds an `ObjectSuperseded` in a trailing
#     sweep, so a supersede the sweep resolves differently from a chronological
#     inline fold — a FORWARD reference (its `ObjectRegistered` later in the
#     journal), or a delete-then-recreate (the sweep folds the LAST
#     incarnation) — is folded there. The apply-based engines (`rebuild`,
#     `recover_from_log`, `backup.restore`) fold it inline, chronologically,
#     and leave the final node `live`. All the engines therefore AGREE on
#     accept/refuse, but `check_consistency` run against an apply-replayed
#     graph reports `divergence="content"` for that one field where the same
#     journal checked against a `rebuild_all` graph is `ok/None`. This is the
#     SAME class as the deliberately-deferred
#     `DirectEdgeRepoint` fold in the one-record `apply()` engine (A10 #1048):
#     a STATED bound, not a hidden caveat. (`DirectEdgeCreated` is no longer in
#     that class for `rebuild_all` — #8499 landed its pass-2b fold — but the
#     `apply()` engine still does not fold either type, so the parity bound
#     below holds for that engine on both.) Closing it means re-implementing
#     the sweep's
#     after-creations pass + #4743 state re-fold inside the apply engines —
#     the larger change this lane declines in favour of one canonical sweep.
#     The invariant this leg enforces is accept/refuse; the content bound is
#     named here, in the `apply()` branch's STATED PARITY BOUND comment, and in
#     the PR body.
#   * NO event-driven status fold is modelled (#8652). This reference fold
#     carries the creation default (`live`), the `status`-writing state ops
#     and `ObjectSuperseded` — not the built-in connector fold
#     (`github.issue.closed` → `completed`) nor the #2729 pack-declared
#     per-kind fold (`venture:disbursement` → `released`). `check_consistency`
#     therefore reports `divergence="content"` for an Object whose `status` an
#     `EventRecorded` folded, on a faithful journal — the #3311 class. The
#     shape PRE-EXISTS #2729 (the built-in fold already hit it); #2729 widens
#     the kinds it can hit. Closing it means teaching this fold the fold
#     vocabulary AND the ownership rule (a lifecycle-declaring kind suppresses
#     the built-in connector fold), which is its own change (#8652).
#: The non-Point labels the ENTITY leg models, with the graph MATCH key each is
#: addressed by (``_ENTITY_ID_PROP`` — Event differs on purpose: it is the one
#: label whose GRAPH key is not ``id``). Kept as a declaration even though
#: :func:`_graph_view` indexes the WHOLE ``(label, id)`` set, so the entity half
#: has a named home and a reader can see the two sets are different questions.
_ENTITY_PARITY: tuple[tuple[str, str], ...] = tuple(
    (label, _ENTITY_ID_PROP[label])
    # #8859 follow-up review round 2 (P2, same fail-open-drift class): DERIVED from
    # the one home, never re-listed. The literal this replaced
    # (`("Object", "Subject", "Event")`) was a hand-copied second home two lines
    # above `_ENTITY_CREATION`, whose own comment says "the hand-copied second list
    # is what this file's doctrine forbids" — and unlike the three surfaces a
    # widening IS pin-forced into, no test referenced this table at all. Measured
    # on a simulated fourth creation row: the comparison DROPPED the graph node
    # (`_entities_from_view` builds its `id_prop` map from this table) and reported
    # `divergence="content"`, `ok=False` on a FAITHFUL graph, with an `action`
    # telling the operator to replay/repair a graph whose only defect was the
    # checker's label table. Latent, not live — the sets are equal today.
    for label in sorted(_REFERENCE_FOLD_ENTITY_LABELS)
)
# (label, default status) per creation record. The ID PROP is NOT re-listed:
# `_creation_entity_id` reads the writer's own `_CANONICAL_ENTITY_ID_PROPS`,
# so a change there cannot silently desync this leg (the hand-copied second
# list is what this file's doctrine forbids).
_ENTITY_CREATION: dict[str, tuple[str, str | None]] = {
    "ObjectRegistered": ("Object", "live"),
    "SubjectAdded": ("Subject", "live"),
    # D10 (#5127) retired the `:Document` NODE label: `_upsert_document` MERGEs a
    # document as a `:Source` keyed `url`, and `doc_status` was retired with it.
    # `DocumentCreated` is deliberately absent here for the same reason `Source`
    # is absent from the entity leg — a url-keyed node compared against
    # an id-keyed journal record is a false positive by construction (#4649).
    "EventRecorded": ("Event", None),
}
#: The non-Point labels the entity reference fold models. ONE home: the
#: projection's `_REFERENCE_FOLD_ENTITY_LABELS` is the same set the GRAPH fold
#: reads to decide whether a state-op miss is recordable at all (the two
#: classifiers must agree, #3585). The suite pins it equal to the labels of
#: `_ENTITY_CREATION` above, so adding a row here cannot leave the two folds
#: disagreeing silently.
_ENTITY_CREATION_LABELS: frozenset[str] = _REFERENCE_FOLD_ENTITY_LABELS
#: Labels whose graph fold MERGEs the NODE by `name` (`_upsert_object` /
#: `_upsert_subject`). A second registration of ONE name under a fresh id
#: therefore COLLAPSES the node onto the LAST id, and a state op naming the
#: collapsed-away id folds 0 rows there (`MATCH (n:$label {id:$rid})`). An
#: id-keyed label (`Event`) never collapses, so it is absent. This is the
#: DECLARATION for the name-resolution leg, the state-op guard below, and the
#: emitted `name_keyed`/`identity` bounds —
#: `journal_object_surviving_keys` is deliberately Object-only (it models
#: `_upsert_object` alone) and `_graph_entities` indexes EVERY label, so
#: neither can share this constant without changing what it means.
_NAME_MERGE_LABELS: frozenset[str] = frozenset({"Object", "Subject"})

#: The labels whose ``status`` the COMPARISON decides. ``Event`` carries
#: ``eventStatus`` under a different name and its lifecycle has no such fold, so the
#: status leg stays bounded to the families that model the field. ONE home (#8859
#: follow-up review round 2, P3): the status comparison in `_compare_entities` reads
#: this, and `entity_parity_bounds["status_compared"]` emits it — the set used to be
#: re-typed at both, with nothing pinning them together, so a widened status leg
#: would have left the emitted bound telling a reader the check does not decide what
#: it does decide (the round-1 dismissal-by-out-of-scope path).
_STATUS_COMPARED_LABELS: frozenset[str] = frozenset({"Object", "Subject"})

#: #7936 — the belief properties the entity leg COMPARES, read from the
#: writer's own declarations (``_fold_confidence_changed`` writes exactly this
#: set), so a key added to the writer cannot silently stay outside the check.
#: Before this the entity leg compared entity PRESENCE and Object/Subject
#: ``status`` only, so a wrong, stale or lost belief on a ``:Subject`` /
#: ``:Object`` / ``:Event`` was invisible to the whole durability gate: the
#: Point-only ``_compare_views`` never sees the id, and (being graph-only) the
#: Point-only fingerprint could not move either. Measured: corrupting an
#: ``:Event``'s ``confidence`` left ``ok=True, divergence=None``.
#:
#: STATED BOUND: the COMPARISON leg still runs journal→graph only, so a
#: graph-only belief write on a non-Point node with NO ``ConfidenceChanged``
#: anywhere in the journal (an unjournalled producer — the #4240 class) is not
#: this leg's to decide. The ``db_hash`` BASELINE covers it instead (#7936):
#: ``_graph_fingerprint`` now folds the whole ``(label, id)`` graph view, so
#: such a write moves the digest and is reported as ``unrecorded-mutation``
#: when the journal did not move. That widening is why
#: ``_PROJECTION_STATE_FORMAT`` is 3 — every existing baseline's
#: ``content_hash`` moves with it, and the previous format is a KNOWN
#: PREDECESSOR that is MIGRATED (re-baselined) on the next healthy recording
#: run instead of reporting for ever (#8268/#8644).
_ENTITY_BELIEF_FIELDS: tuple[str, ...] = tuple(BELIEF_PROPS) + tuple(
    BELIEF_BOOL_PROPS)


def _creation_entity_id(t: str, ev: dict) -> object:
    """The id a creation record registers, resolved as the GRAPH fold does.

    Delegates to the projection's ``_creation_entity_id_from_record`` — the
    SAME reader ``journal_first_materialization`` uses — so the reference
    fold's key and the existence map's key cannot drift (a shared key is what
    makes the non-Point forward-reference gate agree with the graph fold).
    """
    return _creation_entity_id_from_record(t, ev)


def _fold_journal_entities(events: list[dict]) -> tuple[dict, set, set]:
    """Reference fold for non-Point entities (#3585).

    Returns ``(entities, deleted, ambiguous)``:
      entities  — ``{(label, id): {"status": ...}}``
      deleted   — ``{(label, id)}`` the journal hard-deletes
      ambiguous — Object names a name-only supersede could not resolve
    """
    entities: dict = {}
    deleted: set = set()
    ambiguous: set = set()
    # #3585 re-review (cycle 2, FIX B): `rebuild_all` DEFERS the
    # `ObjectSuperseded` fold to a sweep AFTER every Object-creation event AND
    # every delete, so a supersede that PRECEDES its own `ObjectRegistered` is
    # legitimately foldable there. This reference fold therefore resolves EVERY
    # supersede in ONE trailing sweep against the journal's END index.
    #
    # #3585 (P1-1): the whole-journal EXISTENCE map — the non-Point leg of
    # `journal_first_materialization`. A state op whose entity the journal
    # materializes only LATER is a forward reference: the live write no-op'd
    # it and the graph fold skips it too, so it is neither folded nor recorded.
    _first_materialized = journal_first_materialization(events)
    #
    # #5285 cycle-4 (FIX 1): there is exactly ONE resolution path. An earlier
    # cut applied non-held supersedes INLINE at their own seq and re-evaluated
    # only the held ones; an inline resolve is never re-examined, so a later
    # delete+recreate left the reference fold at `superseded` while
    # `rebuild_all`'s sweep folds the LAST incarnation (a false `content`
    # divergence on a correct journal). Deferring EVERY supersede subsumes the
    # forward-reference case and removes the second path that kept drifting.
    surviving_ids, _surviving_names = journal_object_surviving_keys(events)
    # #7719: the anchor-gated MEMBERSHIP map — the named
    # `supersede-target-deleted` exemption `rebuild_all`'s sweep carries. The
    # ungated `journal_object_hard_deleted_ids` used here before exempted an
    # anchor-suppressed delete too (a reachable divergence from the graph
    # fold); the gated map is the shared predicate.
    hard_deleted = hard_deleted_pairs(events)
    # EVERY `ObjectSuperseded`, in journal order, resolved by the ONE trailing
    # sweep below — never applied inline.
    supersede_events: list[tuple[int, dict]] = []
    # The seq of the last `status`-writing state op per entity, so the
    # trailing sweep honours `rebuild_all`'s #4743 ordering.
    last_status_seq: dict = {}
    # #5285 (DEFECT 1): the ids CURRENTLY carrying each name-MERGE key, built
    # as the walk proceeds. Since the graph MERGEs Object/Subject by NAME under
    # the LAST registration's id, a state op folds there iff its id is a live
    # carrier AT ITS OWN SEQ — the journal-END `surviving_ids` key set is the
    # wrong question for an INLINE state op (an id that was the carrier early
    # and collapsed away later genuinely folded at that earlier point). Keyed by
    # ``(label, name)`` because Object and Subject MERGE in SEPARATE spaces, so
    # a shared name must not alias one label's carrier onto the other. The value
    # is a SET, not a single id: a RENAME `SET`s the name property in place, so
    # two ids can legitimately share a name (a rename onto an existing name
    # does NOT MERGE), and BOTH then MATCH by id. Collapsing them to one id
    # produced a FALSE REFUSAL of a live id in exactly that shape — a
    # disagreement with the graph fold in the fail-CLOSED direction, the one
    # error this guard must never make.
    carriers_by_name: dict[tuple[str, str], set] = {}
    # #5285 cycle-6: the GRAPH's MERGE key per id — `_upsert_object` /
    # `_upsert_subject` MERGE on `name` ALONE, and an empty name is skipped by
    # `_writable_id` (no node is created), so `title` is NEVER a merge key. The
    # display name below legitimately falls back to `title` for PARITY, but the
    # carrier index must not: keying it on the display name let a title-only
    # registration EVICT the live carrier of the real node, and the state-op
    # guard then falsely refused `state-op-miss` a journal all three replay
    # engines fold (review round 6, P1). An id with no merge key recorded here
    # has no graph node this fold can model, so the guard stays fail-OPEN for it.
    merge_key_by_id: dict[tuple[str, str], str] = {}
    # #5285 cycle-7: ids whose journal CREATION produced NO graph node at all,
    # because `_writable_id` skips an empty / non-writable MERGE key. The graph's
    # state-op fold is `MATCH (n:$label {id:$rid})`, so for these ids it is a
    # 0-row miss and every replay engine refuses `state-op-miss`. "No merge key"
    # is therefore DECIDABLE here (`rec` exists, so the journal DID create the
    # id) and must not be treated as unmodelled: review round 7 found 12 journals
    # the three replay engines refuse while this fold accepted them, in the
    # fail-OPEN direction, on the previous head.
    no_node_ids: set[tuple[str, str]] = set()
    # #5285 (DEFECT 2): ``name -> [keys]``, populated ONCE after the walk and
    # read by `_object_target`'s trailing sweep. Declared here so the closure
    # below resolves it at call time.
    object_keys_by_name: dict[str, list] = {}

    def _object_target(oid, oname):
        """``(target_key, ambiguous_here)`` against the CURRENT index.

        ``ambiguous_here`` is LOCAL to THIS event: the >1-carrier carve-out
        must not leak through the never-cleared ``ambiguous`` reporting set,
        or every LATER zero-carrier supersede of a once-ambiguous name is
        silently exempted — passing a journal `rebuild_all` refuses.

        #5285 cycle-3: a `status != "superseded"` predicate here gated
        # target EXISTENCE, not just the ambiguity decision. The graph
        fold matches a name-only supersede on NAME and re-folds
        UNCONDITIONALLY (`_fold_object_superseded`, `cas=False`), so a SINGLE
        carrier is resolvable whether or not it is already terminal: a SECOND
        name-only supersede of a once-superseded name is a 1-row fold there,
        never a miss. Filtering terminal carriers out made the carrier list
        empty, so the reference fold held the second supersede `pending` and
        flushed it as `object-superseded-miss` — a false refusal of a journal
        all three replay engines accept (and a `divergence="non-folded"` on a
        correct graph). The predicate now gates only the >1-carrier ambiguity
        decision: `EXISTENCE` is "any carrier at all", and one already-terminal
        carrier resolves.
        """
        if (isinstance(oid, str) and ("Object", oid) in entities
                and oid in surviving_ids):
            # #5285 cycle-4 (FIX 2): the graph MERGEs Objects by NAME, so two
            # ids sharing a name collapse into ONE node carrying the LAST id —
            # `MATCH (o:Object {id:$oid})` then folds 0 rows for the
            # collapsed-away id and every replay engine REFUSES
            # (`object-superseded-miss`). A bare `entities` membership test
            # recorded NO miss (the collapsed-away id is still in the
            # reference index), so `check_consistency` passed a journal the
            # graph refuses — the fail-open this lane exists to remove. An id
            # is a live carrier only when it is the SURVIVING id for its name
            # (the same journal-end key set the apply engines' refusal gate
            # reads); otherwise fall through to the name branch / miss path.
            return ("Object", oid), False
        if isinstance(oname, str) and oname:
            # #5285 (DEFECT 2): read the ONCE-built `name -> [keys]` index
            # instead of rescanning ALL of `entities` on EVERY call. The scan
            # was O(len(entities)) PER SUPERSEDE, so a journal of N name-only
            # supersedes cost O(N^2) (measured: 4000 supersedes = 17.5s, 8000 =
            # 61.7s) — a denial of service on this very health gate. The index
            # preserves the scan's EXACT semantics: same membership, same
            # `entities` insertion order, and it is built only after the walk,
            # so nothing mutates underneath it.
            carriers = object_keys_by_name.get(oname, [])
            if len(carriers) == 1:
                return carriers[0], False
            if len(carriers) > 1:
                # More than one carrier for one MERGE key is the ambiguity the
                # status leg reports — but exactly ONE still-live carrier is
                # the only one a supersede could still claim, so it resolves.
                live = [k for k in carriers
                        if entities[k].get("status") != "superseded"]
                if len(live) == 1:
                    return live[0], False
                return None, True
        return None, False

    def _object_hard_deleted(oid) -> bool:
        # #7719: the SHARED gated predicate, so this Object leg and
        # `rebuild_all`'s Object arm reach one verdict.
        return _hard_deleted_any(hard_deleted, "Object", oid)

    def _record_supersede_miss(seq: int, ev: dict) -> None:
        candidate = ev.get("name") if isinstance(ev.get("name"), str) \
            and ev.get("name") else ev.get("id")
        record_non_folded(
            classify_terminalizer_miss("ObjectSuperseded",
                                       target_deleted=False),
            event_id=ev.get("event_id"), event_type="ObjectSuperseded",
            seq=seq,
            id=ev.get("id") if isinstance(ev.get("id"), str) else None,
            candidates=(candidate,),
            detail="reference fold: supersede matched no Object",
        )

    for seq, ev in enumerate(events):
        t = ev.get("type")
        if t in _ENTITY_CREATION:
            label, default_status = _ENTITY_CREATION[t]
            eid = _creation_entity_id(t, ev)
            if not isinstance(eid, str) or not eid:
                continue
            # The nested EventRecorded shape carries its fields one level down;
            # unwrap it the same way so status/name are read where the writer
            # put them.
            payload = ev
            if t == "EventRecorded" and isinstance(ev.get("event"), dict):
                payload = ev["event"]
            rec = entities.setdefault((label, eid), {})
            # A later creation RE-CREATES the incarnation: the id is live
            # again, so an earlier journaled delete no longer owns it.
            deleted.discard((label, eid))
            status = (payload.get("status") or payload.get("eventStatus")
                      or default_status)
            if isinstance(status, str) and status and "status" not in rec:
                rec["status"] = status
            # #5285 cycle-6 (P1): the MERGE key is `name` ALONE — `title` is a
            # DISPLAY fallback the graph never merges on, and an empty name
            # creates no node at all. The carrier index must use the merge key,
            # never the display name.
            # #5285 cycle-8 (P2): the predicate is the GRAPH's own writability
            # check, not "a non-empty str". `_writable_id` is what
            # `_upsert_object` / `_upsert_subject` use, and it rejects a
            # non-empty but NON-writable MERGE key (NUL / lone surrogate — the
            # #7369 class). For such a name the graph writes NO node, so a state
            # op on that id is a 0-row miss and must be refused; the previous
            # `isinstance(str) and truthy` test agreed with the graph only for
            # the EMPTY key, so that fold-leg accepted a `state-op-miss` the
            # three replay engines refuse.
            _merge_key = payload.get("name")
            _merge_key = (_merge_key if _writable_id(_merge_key) else None)
            if label in _NAME_MERGE_LABELS:
                if _merge_key is not None:
                    # The graph MERGEs Object/Subject by name, so the LAST
                    # registration of a merge key OWNS its node id — the MERGE
                    # re-ids the existing node, REMOVING the earlier id. Update
                    # the carriers AT THIS SEQ; a re-creation of the same id
                    # under a new key drops the id from its OLD key's set first.
                    _old = merge_key_by_id.pop((label, eid), None)
                    if _old is not None:
                        _prev = carriers_by_name.get((label, _old))
                        if _prev is not None:
                            _prev.discard(eid)
                    merge_key_by_id[(label, eid)] = _merge_key
                    no_node_ids.discard((label, eid))
                    _live = carriers_by_name.setdefault(
                        (label, _merge_key), set())
                    if len(_live) > 1:
                        # The key ALREADY has >1 live carrier (a rename
                        # collision). Since #8864 the graph stops keying on
                        # that name entirely — it merges on the record's own
                        # `id` (`_name_holder_count`), so NO carrier is re-id'd
                        # and all of them survive. Keeping them all is
                        # therefore both the graph's behaviour and the only
                        # choice that cannot refuse a node the graph still
                        # holds (fail OPEN, never closed).
                        _live.add(eid)
                    else:
                        _live.clear()
                        _live.add(eid)
                elif (label, eid) not in merge_key_by_id:
                    # #5285 cycle-7: the graph writes NOTHING for this creation —
                    # no node exists for the id, so a state op on it is refused
                    # `state-op-miss` exactly as the graph's 0-row MATCH is.
                    no_node_ids.add((label, eid))
                # else: an UNUSABLE name on an id that ALREADY has a merge key is
                # a graph NO-OP — `MERGE` on the unchanged key keeps the node and
                # its id. The existing key and carriers are therefore PRESERVED;
                # popping them hid the later collapse from the guard (review
                # round 7, P1).
            if t == "EventRecorded":
                # #8864 review P1: this record is the SECOND carrier source, and
                # it runs on replay too. `_event_plain_merge` MERGEs a
                # name-STUB for the mention's `subject` / `object` (unless
                # `objectType` is `Document`) / every `uses[]` name, writing a
                # DETERMINISTIC id `ON CREATE` only (#7936). The stub is a real
                # graph carrier that this fold could not see, so the carrier
                # COUNT below — the thing the graph's MERGE-key branch is
                # decided by (`_name_holder_count`) — undercounted: a name whose
                # only OTHER carrier was a registration took the name-keyed
                # (collapsing) branch here while the graph, holding a stub as
                # well, keys on the record's own `id` and moves NOTHING. The
                # stub carrier was then evicted from `carriers_by_name`, and the
                # state-op guard below recorded a `state-op-miss` for an id the
                # graph still holds — a fail-CLOSED refusal of a legal journal
                # (`recover_from_log` refuses to open, `rebuild` parity
                # fail-loud, `backup.restore`'s `assert_no_non_folded`).
                # The two read-side models got exactly this seeding in the same
                # review round (`journal_epistemic_existence`,
                # `journal_object_surviving_keys`); `_fold_journal` is the third
                # model of one fold and must not read a different carrier table.
                # `edges._mint_subject_stub` remains the unmodelled source
                # (#8914) — recorded in `_event_mentioned_name_stubs`.
                for _stub_label, _stub_name in _event_mentioned_name_stubs(ev):
                    if _stub_label not in _NAME_MERGE_LABELS:
                        continue
                    _stub_carriers = carriers_by_name.setdefault(
                        (_stub_label, _stub_name), set())
                    if _stub_carriers:
                        # `MERGE … {name:$name}` matched an existing carrier, so
                        # the stub id (an `ON CREATE` write) does NOT land and
                        # no carrier moves. Same rule the fold applies.
                        continue
                    _stub_carriers.add(entity_name_id(_stub_label, _stub_name))
            name = payload.get("name") or payload.get("title")
            if isinstance(name, str) and name:
                rec["name"] = name
        elif t == "EntityMutated":
            label, eid, op = ev.get("label"), ev.get("id"), ev.get("op")
            if not isinstance(eid, str):
                continue
            if op == "delete":
                if (isinstance(label, str)
                        and label in _CANONICAL_ENTITY_LABELS):
                    deleted.add((label, eid))
                    entities.pop((label, eid), None)
                else:
                    # Legacy id-wide delete: `_delete_entity_by_id` scopes ONLY
                    # a CANONICAL label and falls back to the id-wide delete
                    # for a missing/unknown/non-canonical one — so a bare
                    # `isinstance(label, str)` here marked the wrong kind and
                    # reported a false `absent-from-graph`.
                    for k in [k for k in entities if k[1] == eid]:
                        deleted.add(k)
                        entities.pop(k, None)
                continue
            if isinstance(label, str) and op in _ENTITY_MUTATION_STATE_OPS:
                rec = entities.get((label, eid))
                state = ev.get("state")
                if rec is None or not isinstance(state, dict):
                    # #3585 re-review: the graph fold records `state-op-miss`
                    # (refused) when a state op resolves to no entity or
                    # carries no applied map, so the reference fold must agree.
                    # Only the labels this fold models are handled here —
                    # `_apply_one` owns the Point case.
                    # #3585 (P1-1): a WELL-FORMED state op whose entity the
                    # journal materializes only LATER is a forward reference —
                    # the live write no-op'd it and the graph fold skips it, so
                    # neither side records it. The malformed-state case still
                    # records in either order, exactly as `_fold_entity_mutation`
                    # does (its forward gate sits AFTER the state guard).
                    if (rec is None and isinstance(state, dict)
                            and _journal_forward_reference(
                                _first_materialized, seq, label, eid)):
                        continue
                    # `Source` is canonical to the graph fold but has no entry
                    # in `_ENTITY_CREATION` (no `SourceCreated` shape), so this
                    # fold cannot refuse a Source state-op miss. #3585 (P1-2)
                    # resolved the asymmetry in THAT direction: the graph
                    # fold's `_warn_entity_mutation_fold_miss` records the miss
                    # only for a label in `_REFERENCE_FOLD_ENTITY_LABELS`, so
                    # neither classifier refuses it (the warning is retained).
                    # Adding a `Source` row here would need a `Source` entry in
                    # the entity-parity comparison, which the parity leg still
                    # does not model; the label stays out of BOTH folds.
                    if label in _ENTITY_CREATION_LABELS:
                        record_non_folded(
                            SHAPE_STATE_OP_MISS, event_id=ev.get("event_id"),
                            event_type="EntityMutated", label=label, id=eid,
                            op=op, seq=seq,
                            detail=("reference fold: state op matched no "
                                    "entity"),
                        )
                    continue
                # #5285 (DEFECT 1): the graph fold MATCHes a state op by primary
                # ID (`MATCH (n:$label {id:$rid}) SET n += $s`), but
                # `_upsert_object`/`_upsert_subject` MERGE the NODE by NAME — so
                # when ONE name is registered under two ids the node collapses
                # onto the LAST id and a state op naming the collapsed-away id
                # folds 0 rows there. `entities` still holds the collapsed-away
                # id (the reference index is keyed by every id the journal ever
                # saw), so the `rec is None` gate above does NOT catch it: this
                # fold used to APPLY state the graph dropped, and
                # `check_consistency` passed a journal all three replay engines
                # refuse — the fail-open this lane exists to remove. Refuse with
                # the SAME `state-op-miss` shape the malformed branch records.
                # SEQ-AWARE by construction: `carriers_by_name` is the carrier
                # SET at THIS point in the walk, so an id that WAS the sole
                # carrier before a later same-name registration still folds here
                # (a journal-END `surviving_ids` test would wrongly refuse it).
                # A name the fold never indexed stays fail-OPEN, exactly as
                # `_object_target` falls through — an unmodelled shape must not
                # become a false refusal. The guard fires only when the name IS
                # indexed and this id is NOT among its live carriers (the
                # collapsed-away case).
                # #5285 cycle-7 (P1): an id whose CREATION produced no graph node
                # (an empty / non-writable merge key) is a 0-row miss there, so
                # the reference fold must refuse it too. This check sits BEFORE
                # the fold, so it covers the rename arm as well.
                if (label in _NAME_MERGE_LABELS
                        and (label, eid) in no_node_ids):
                    record_non_folded(
                        SHAPE_STATE_OP_MISS, event_id=ev.get("event_id"),
                        event_type="EntityMutated", label=label, id=eid,
                        op=op, seq=seq,
                        detail=("reference fold: state op matched no "
                                "entity"),
                    )
                    continue
                # #5285 cycle-6 (P1): read the id's GRAPH merge key, never the
                # display name — see `merge_key_by_id`. An id with no merge key
                # has no node this fold models, so the guard stays fail-open.
                _carrier_name = (merge_key_by_id.get((label, eid))
                                 if label in _NAME_MERGE_LABELS else None)
                _live_carriers = (
                    carriers_by_name.get((label, _carrier_name))
                    if _carrier_name else None)
                if _live_carriers is not None and eid not in _live_carriers:
                    record_non_folded(
                        SHAPE_STATE_OP_MISS, event_id=ev.get("event_id"),
                        event_type="EntityMutated", label=label, id=eid,
                        op=op, seq=seq,
                        detail=("reference fold: state op matched no "
                                "entity"),
                    )
                    continue
                status = state.get("status")
                if isinstance(status, str) and status:
                    rec["status"] = status
                    # Remember WHERE a status write sits so the trailing
                    # supersede sweep below honours `rebuild_all`'s #4743
                    # un-clobber (a state op AFTER the deferred supersede
                    # wins); see that loop.
                    last_status_seq[(label, eid)] = seq
                newname = state.get("name")
                if label in _NAME_MERGE_LABELS and "name" in state:
                    # #5285 cycle-6 (P2): the graph runs `SET n += $s`, so ANY
                    # `name` key in `state` rewrites the MERGE key — including a
                    # falsy or non-string one. Acting only on a non-empty str
                    # left the index keyed on a name the graph no longer had, and
                    # a later registration of the OLD key then evicted a
                    # still-live id: a false `state-op-miss` on a journal all
                    # three replay engines fold (review round 6, P2). When the
                    # new key is not a usable non-empty str the graph's node
                    # identity is undefined for this fold, so the id is DROPPED
                    # from the index — fail-OPEN, because an unmodelled shape
                    # must not become a false refusal.
                    _old = merge_key_by_id.pop((label, eid), None)
                    if _old is not None:
                        _prev = carriers_by_name.get((label, _old))
                        if _prev is not None:
                            _prev.discard(eid)
                    if isinstance(newname, str) and newname:
                        merge_key_by_id[(label, eid)] = newname
                        carriers_by_name.setdefault(
                            (label, newname), set()).add(eid)
                if isinstance(newname, str) and newname:
                    # The graph's rename SETs `name` in place; the node KEEPS
                    # its id, so `eid` joins the NEW key's live carriers (and
                    # leaves the old key's). A rename onto a key another id
                    # already carries does NOT MERGE — both nodes stay live
                    # under one name — which is why the carrier value is a SET,
                    # not a single id.
                    rec["name"] = newname
        elif t == "ObjectSuperseded":
            # #5285 cycle-4 (FIX 1): defer EVERY supersede to the ONE trailing
            # sweep — never apply it here. See the sweep below.
            supersede_events.append((seq, ev))
    # ── #5285 cycle-4 (FIX 1): the ONE trailing supersede sweep.
    #
    # EVERY `ObjectSuperseded` resolves here, against the journal's END index —
    # exactly the query `rebuild_all`'s deferred sweep runs. Nothing is applied
    # inline: an inline resolve happens at its own seq and is never
    # re-evaluated, so `[Reg(x,X), Sup(id=x), Del(x), Reg(x,X)]` left the
    # reference fold `superseded` (the resolve died with the deleted
    # incarnation) while the sweep folds the LAST incarnation — a false
    # `content` divergence on a correct journal. Resolving after the whole loop
    # also subsumes the held-forward-reference case, so there is no second path
    # to drift from.
    #
    # The `seq > last_status_seq` guard reproduces the sweep's ORDER, not just
    # its membership: `rebuild_all` applies the deferred supersede LAST and
    # then re-folds every state op the journal puts AFTER it (#4743), so a
    # status write later than the supersede must win. Applying the supersede
    # at the end unconditionally would clobber it and invent a divergence on a
    # journal the graph folds correctly. A creation's default status is NOT
    # such a write (it precedes the sweep), so it is deliberately not counted.
    # #5285 (DEFECT 2): build the `name -> [keys]` index ONCE, against the
    # FINAL `entities` state this sweep resolves against. Built here (not
    # incrementally) so it mirrors the per-call scan it replaces exactly —
    # including the `entities` insertion order the ambiguity branch relies on.
    for _key, _rec in entities.items():
        _nm = _rec.get("name")
        if _key[0] == "Object" and isinstance(_nm, str) and _nm:
            object_keys_by_name.setdefault(_nm, []).append(_key)
    for seq, ev in supersede_events:
        oid, oname = ev.get("id"), ev.get("name")
        target, ambiguous_here = _object_target(oid, oname)
        if target is not None:
            if seq > last_status_seq.get(target, -1):
                entities[target]["status"] = "superseded"
        elif ambiguous_here:
            # Genuine >1-carrier ambiguity: the fold's STATUS resolution is
            # heuristic, so the status leg is excluded (and reported). The
            # carve-out is LOCAL to THIS event — never the never-cleared
            # `ambiguous` set consulted by later events, which would silently
            # exempt every later zero-carrier supersede of a once-ambiguous
            # name.
            ambiguous.add(oname)
        elif _object_hard_deleted(oid):
            # Named exemption: the journal HARD-DELETED the target, so the
            # deferred sweep's 0-row match is legitimate
            # (`supersede-target-deleted`), never a miss.
            pass
        else:
            # Genuinely unresolvable: an id no journaled registration leaves
            # in place (and no usable name) matched nothing in the graph fold
            # either, which records `object-superseded-miss` and fails — so
            # the reference fold must refuse too, or `check_consistency`
            # passes a journal `rebuild_all` refuses.
            _record_supersede_miss(seq, ev)
    return entities, deleted, ambiguous


#: The labels :func:`_graph_view` indexes: the Point lifecycle (``_apply_one``)
#: plus every label the reference fold models. DERIVED from
#: `_REFERENCE_FOLD_ENTITY_LABELS`, never re-listed — the literal it replaced
#: was a second home for the same three labels, and the fail-open that created
#: was invisible: a new creation row widened the fold while the digest kept
#: indexing the old four labels, leaving a graph-only node of the new label
#: with `db_hash` unmoved and every existing label-set pin still green.
#:
#: WHERE THE SET ACTUALLY GROWS (the direction matters, and this comment used
#: to state the reverse): `_REFERENCE_FOLD_ENTITY_LABELS` is derived in
#: ``projection/__init__.py`` from `projection._ENTITY_CREATION_EVENT_LABEL`,
#: NOT from this module's `_ENTITY_CREATION`. Adding a creation row there grows
#: the label set; adding it here alone does NOT, and it REDS the one-home pin
#: (`tests/test_rebuild_parity_fail_loud_3585.py::
#: TestReferenceFoldEntityLabelSetHasOneHome`) because
#: `consistency._ENTITY_CREATION` is the ALIAS kept in step with it (see
#: `_ENTITY_CREATION_LABELS` above). So: row lands in
#: `projection._ENTITY_CREATION_EVENT_LABEL`, and this module's
#: `_ENTITY_CREATION` follows because the pin forces it.
#:
#: ONE further declaration this depends on, named so it is not discovered by
#: surprise: the index KEY comes from `_ENTITY_ID_PROP`
#: (`projection._CANONICAL_ENTITY_ID_PROPS`), and :func:`_graph_view_key`
#: returns ``None`` — DROPPING the node — for a label with no entry there, or a
#: node whose entry is absent. So a creation row added to
#: `projection._ENTITY_CREATION_EVENT_LABEL` but not to `_ENTITY_ID_PROP` would
#: reach the query and vanish from the index: the same fail-open one
#: declaration further along. Pinned (not merely stated) by
#: `tests/test_7936_reference_fold_label_rekey.py`, which asserts this set is a
#: subset of `_ENTITY_ID_PROP`.
_GRAPH_VIEW_LABELS: tuple[str, ...] = (
    "Point", *sorted(_REFERENCE_FOLD_ENTITY_LABELS))


def _graph_view_query() -> str:
    """The ONE read query behind :func:`_graph_view`.

    A function rather than a module constant so the label set is read at CALL
    time: the labels have one home (``_GRAPH_VIEW_LABELS``) and a test can
    prove the emitted Cypher really tracks it by patching that home.
    """
    return ("MATCH (n) WHERE "
            + " OR ".join(f"n:{label}" for label in _GRAPH_VIEW_LABELS)
            + " RETURN labels(n), properties(n)")


def _graph_view_key(label: str, props: dict) -> tuple[str, bool] | None:
    """``(id, via_fallback)`` — the identity a node of ``label`` is indexed
    under, and whether that identity came from the declared fallback.

    ``_ENTITY_ID_PROP[label]`` is the key the entity WRITERS and the entity
    comparison address a node by (``id`` for Point/Object/Subject, ``eventId``
    for Event), and it is tried first so the in-tree shape (an ``:Event`` whose
    ``id == eventId``) gets exactly ONE key.

    ONE declared fallback, for ``Event`` only: the BELIEF fold does not use
    ``eventId`` — ``_fold_confidence_changed`` expands
    ``epistemic_label_queries("MATCH (n:{label} {id:$id})")``, so it matches an
    ``:Event`` by ``id`` like the other three labels. An ``:Event`` carrying
    ``id`` but no ``eventId`` (reachable from a raw writer; the in-tree mirror
    legacy shape is the reverse, ``eventId`` only) therefore has a WRITABLE
    belief while ``eventId`` yields nothing — and dropping it from the index
    would leave exactly the residual this file's fix removes, a graph-only
    belief write on a non-Point node that leaves ``db_hash`` unmoved.

    ``via_fallback`` is returned because the fallback WIDENS an existing
    key-collision surface (see :func:`_graph_view`): caller-side, a node keyed
    by its own match key must outrank one keyed only by the fallback, or the
    digest depends on the order the store happened to return the rows.
    """
    prop = _ENTITY_ID_PROP.get(label)
    if not prop:
        return None
    nid = props.get(prop)
    if isinstance(nid, str):
        return nid, False
    if label == "Event":
        alt = props.get("id")
        if isinstance(alt, str):
            return alt, True
    return None


def _canonical_props(props: dict) -> str:
    """A stable rendering of a node's properties, for deterministic ties."""
    return json.dumps(props, sort_keys=True, default=str)


def _graph_view(projection) -> dict:
    """THE ``(label, id)`` graph index the reference fold reads (#7936).

    ``{(label, id): properties(n)}`` over ``_GRAPH_VIEW_LABELS`` — the Point
    lifecycle (``_apply_one``) plus every label the entity fold models, the
    set DERIVED rather than re-listed (``_graph_view_query`` emits it from
    ``_GRAPH_VIEW_LABELS``). ONE query and ONE index, because the two
    consumers that used to read the graph separately (the Point view and the
    entity view) are answering the same question about the same graph: what
    does the store actually hold? Two queries is how the reference fold came to
    index ``:Point`` only while the graph held the belief on an ``:Event``.

    The key comes from :func:`_graph_view_key` — the label's own graph MATCH
    key with the ``Event``/``id`` fallback the belief fold's match implies. A
    node with no (or a non-string) identity is DROPPED, because one no writer
    and no fold can address is not one replay can reproduce or compare. The id
    is carried as the LABELED tuple, so a ``:Point`` and an ``:Event``
    legitimately sharing an id stay two distinct entries (each is a different
    node the graph really holds).

    KEY COLLISIONS — resolution and the residual (the review's stated bound).
    Two DISTINCT nodes of one label can resolve to the same key: a faithful
    ``:Event {eventId: "X"}`` and a graph-only ``:Event {id: "X"}`` both key as
    ``("Event", "X")``. ``db_hash`` is documented above as a pure function of
    the GRAPH, so which node's properties win may NOT be the order the store
    returned the rows (that would make the same graph hash two ways and read as
    a false ``unrecorded-mutation``). Resolution is therefore deterministic:
    the node keyed by the label's OWN match key outranks one keyed by the
    ``Event``/``id`` fallback, and within a class the winner is the smaller
    canonical rendering of its properties.

    The RESIDUAL, stated rather than silent: a collision still keeps ONE entry,
    so the loser is not in the digest — a graph-only ``id``-only ``:Event``
    whose id also appears as a faithful ``:Event {eventId}`` is invisible to
    ``db_hash`` (the false-GREEN direction of #7936, now narrowed to two nodes
    that claim one id). Closing it needs a key that can hold both (a
    ``(label, id, key-source)`` triple, or reporting the collision on the
    verdict beside ``uncarried_journal_fields``); filed as #8879, not absorbed
    here — no in-tree writer mints the shape (``_upsert_event`` always sets
    ``id == eventId``).
    """
    view: dict = {}
    primary: dict = {}
    rows = projection.query(_graph_view_query()).result_set or []
    for row in rows:
        if len(row) < 2:
            continue
        labels, props = row[0], row[1]
        if not isinstance(props, dict) or not isinstance(labels, (list, tuple)):
            continue
        for label in labels:
            resolved = _graph_view_key(label, props)
            if resolved is None:
                continue
            nid, via_fallback = resolved
            key = (label, nid)
            held = view.get(key)
            if held is None:
                view[key] = props
                primary[key] = not via_fallback
                continue
            # Collision: prefer the node keyed by the label's own match key,
            # then break the tie canonically so row order cannot decide.
            if (not via_fallback) != primary.get(key, False):
                keep_new = not via_fallback
            else:
                keep_new = _canonical_props(props) < _canonical_props(held)
            if keep_new:
                view[key] = props
                primary[key] = not via_fallback
    return view


def _graph_entities(projection) -> tuple[dict, dict]:
    """The graph's non-Point entities, indexed two ways.

    Returns ``({(label, id): {"status": …}}, {(label, name): same-record})``.
    The NAME index exists because `_upsert_object`/`_upsert_subject` MERGE on
    name, so the id a journal record registered may not be the id the node
    carries (#3585 review).

    A projection over :func:`_graph_view` — the SAME ``(label, id)`` index the
    baseline digest folds (:func:`_graph_fingerprint`), so the comparison and
    the digest cannot disagree about which nodes exist.
    """
    return _entities_from_view(_graph_view(projection))


def _entities_from_view(graph_view: dict) -> tuple[dict, dict]:
    """The entity half of a :func:`_graph_view` index — see :func:`_graph_entities`.

    Keys are ``(label, id)`` resolved against the SAME graph MATCH key the
    writers use (``_ENTITY_PARITY``), so a key here is one the entity leg's
    journal side also produces.
    """
    out: dict = {}
    by_name: dict = {}
    id_prop = dict(_ENTITY_PARITY)
    for (label, eid), props in graph_view.items():
        if label not in id_prop or not eid:
            continue
        # ⛔ THE COMPARISON ADDRESSES A NODE BY ITS DECLARED KEY ONLY (#8873
        # review, P2). `_graph_view` also indexes an `:Event` that carries `id`
        # but no `eventId`: the BELIEF fold matches that shape (`{id:$id}`), so
        # the DIGEST must see it or a graph-only belief write there moves no
        # hash — the residual #7936 exists to close.
        #
        # But admitting that fallback HERE would let an `id`-only node satisfy
        # PRESENCE for a journal `:Event` keyed by `eventId`. Bury the faithful
        # node, leave an impostor carrying only `id` at the same value, and the
        # presence check finds the impostor, `_entity_node_view` (status +
        # belief) compares equal, and the burial reads CLEAN — a false GREEN,
        # which is the one failure mode this file must never produce.
        #
        # So: the digest keeps the fallback, the comparison leg does not. A
        # fallback-keyed node is simply not an entity-carrier here, and its
        # journal counterpart correctly reports `absent-from-graph`.
        if props.get(id_prop[label]) != eid:
            continue
        rec = _entity_node_view(props)
        out[(label, eid)] = rec
        name = props.get("name")
        if isinstance(name, str) and name:
            by_name[(label, name)] = rec
    return out, by_name


def _compare_entities(journal_entities, graph_entities, graph_names,
                      deleted, ambiguous) -> tuple[list, int, list]:
    """Field-aware non-Point comparison.

    Returns ``(mismatches, count, excluded_ambiguous)``. The mismatch LIST is
    capped at ``_MAX_DIVERGENT_POINTS`` (the Point leg's contract) while
    ``count`` is the true number; ``excluded_ambiguous`` names the Objects whose
    STATUS leg the ambiguity bound skipped, so the verdict states what it did
    NOT compare. PRESENCE is still compared for those objects.
    """
    mismatches: list = []
    count = 0
    excluded_ambiguous: list = []
    for key, rec in journal_entities.items():
        label, eid = key
        # #3585 re-review: the ambiguity bound applies to the STATUS leg only.
        # Presence is decidable by name (the graph MERGEs Object/Subject by
        # name), so excluding it too could hide a genuine burial — and a
        # name-only supersede with NO carrier no longer marks ambiguity at all.
        ambiguous_status = label == "Object" and rec.get("name") in ambiguous
        if ambiguous_status:
            excluded_ambiguous.append((label, eid, rec.get("name")))
        g = graph_entities.get(key)
        if g is None and label in _NAME_MERGE_LABELS:
            # The graph MERGEs these labels by NAME, so a second registration
            # of the same name under a fresh id is ONE node carrying the LAST
            # id — resolve by name before declaring a burial (#3585 review).
            #
            # This is the PRESENCE leg's name resolution, not the STATUS leg, so it
            # reads `_NAME_MERGE_LABELS` — the home whose declared meaning is exactly
            # the sentence above. Round 2 hoisted the status pair and pointed BOTH
            # `_compare_entities` sites at it; the two constants coincide in value
            # today, which is why the mis-wiring was invisible. When a reference-fold
            # label becomes name-MERGE'd (forced into `_NAME_MERGE_LABELS` by
            # `test_the_name_merge_label_sets_have_one_home`) while the status set
            # stays Object/Subject, this site would miss the name resolution and
            # report `absent-from-graph` on a FAITHFUL graph — measured. There is no
            # live verdict change today (#8859 follow-up review round 3, P2).
            g = graph_names.get((label, rec.get("name")))
        if g is None:
            count += 1
            if len(mismatches) < _MAX_DIVERGENT_POINTS:
                mismatches.append({
                    "id": eid, "label": label, "field": "presence",
                    "expected": "registered-in-journal",
                    "found": "absent-from-graph",
                })
            continue
        # `status` is a NODE PROPERTY for the Object AND Subject families:
        # #1423 made the Subject's status projection-owned (`_upsert_subject`
        # SEEDS it ON CREATE and `_SUBJECT_HANDLED` keeps a re-mention from
        # clobbering it), so a superseded team/role/person is exactly the
        # "buried behind a live status" shape this leg exists to catch.
        # `Event` carries `eventStatus` under a different name and its
        # lifecycle has no such fold, so the STATUS leg stays bounded to the
        # families that model the field. Presence is compared for every kind.
        # (The Object-only `ambiguous_status` bound is unaffected: a Subject
        # name-only ref is REFUSED as never-guess, so it never becomes
        # ambiguous.)
        if label in _STATUS_COMPARED_LABELS and not ambiguous_status:
            jv = rec.get("status")
            if jv is not None:
                gv = g.get("status")
                if gv != jv:
                    count += 1
                    if len(mismatches) < _MAX_DIVERGENT_POINTS:
                        mismatches.append({
                            "id": eid, "label": label, "field": "status",
                            "expected": jv, "found": gv,
                        })
        # #7936: the BELIEF leg — every reference-fold label, `Event` included
        # (the belief fold matches it on its `id` property exactly like the
        # other three, so `eventStatus`'s separate lifecycle is irrelevant
        # here). Presence-conditional from the JOURNAL side, like the Point
        # leg: only a key the journal's own fold carries can state an
        # expectation, and a journal-side `None` is "absent" rather than a
        # value (`_canonical_point_fields`'s rule — the writer cannot persist a
        # null property, so the graph removes it).
        for field in _ENTITY_BELIEF_FIELDS:
            if field not in rec:
                continue
            jv = rec.get(field)
            if jv is None:
                continue
            gv = g.get(field)
            if not _values_equal(jv, gv):
                count += 1
                if len(mismatches) < _MAX_DIVERGENT_POINTS:
                    mismatches.append({
                        "id": eid, "label": label, "field": field,
                        "expected": jv, "found": gv,
                    })
    for key in deleted:
        if key in graph_entities:
            count += 1
            if len(mismatches) < _MAX_DIVERGENT_POINTS:
                mismatches.append({
                    "id": key[1], "label": key[0], "field": "presence",
                    "expected": "hard-deleted-in-journal",
                    "found": "present-in-graph",
                })
    return mismatches, count, excluded_ambiguous


def check_consistency(log_path: str, projection, *,
                      record_state: bool = True) -> dict:
    """Fold the event log and compare the projection against `replay(journal)`.

    projection must have .query(cypher) returning an object with .result_set.

    Returns the original keys {ok, log_points, db_points, delta} — so existing
    callers (the `check-consistency` CLI, tests) keep working — plus the #5011
    content verdict:

      db_hash                           — canonical GRAPH digest (the recorded
                                          baseline; a pure function of the graph)
      hash_match                        — graph == replay(journal), field-aware
      divergence                        — None | "content" | "lag" |
                                          "unrecorded-mutation" | "non-folded"
      divergent_points                  — per-id field diagnosis (capped at
                                          `_MAX_DIVERGENT_POINTS`)
      divergent_point_count             — the true number of diverged ids
      divergent_entities                — per-entity (label, id) diagnosis for
                                          the non-Point leg (#3585)
      divergent_entity_count            — non-Point divergences
      non_folded_events                 — the R8 set, as strings (#3585)
      non_folded_count / non_folded_refused_count
                                        — size of the set / the failing subset
      watermark / journal_events / watermark_lag — the per-projection seq
      adopted                           — True on the FIRST healthy run against
                                          a pre-existing graph (never "diverged")
      state_error                       — a recorded baseline that exists but
                                          cannot be trusted (distinct from
                                          "never baselined"); reported, never
                                          treated as a divergence
      state_superseded_format           — the format of a WELL-FORMED
                                          predecessor baseline this run
                                          OBSERVED (#8268/#7936); version skew,
                                          not corruption — so it is NOT a
                                          divergence and NOT a state_error.
                                          The migration is APPLIED only when
                                          `state_recorded` is True: a read-only
                                          or non-healthy run reports the
                                          predecessor and leaves the
                                          re-baseline to the next recording run
      excluded_fields                   — {key: reason} for every field the
                                          comparison did NOT decide but saw
      one_sided_fields                  — {key: reason} for a field present on
                                          only one side, where the writer's
                                          treatment makes that legitimate
      uncarried_journal_fields          — journal content the projection drops
      embedding_compared                — vectors actually compared
      action                            — the defined action on a mismatch

    The check is READ-ONLY apart from recording the watermark/baseline, and it
    only records when the run is already healthy. Repair is the existing
    guarded path (`recover_from_log`) and stays the caller's decision — never a
    silent auto-wipe.
    """
    from .log import EventLog

    log = EventLog(log_path)
    events = log.read_all()
    # #3585 (R9): the reference fold runs inside the non-folded collector. A
    # non-object the fold could not resolve makes BOTH sides of the content
    # comparison equally incomplete, so the comparison alone would pass
    # vacuously — the collected set is what makes that a failure.
    with collect_non_folded() as _nf_entries:
        journal_by_id = _fold_journal(events)
        # #3585 review: the ENTITY reference fold belongs inside the collector
        # too — a fold that cannot resolve an entity is the same class of event,
        # and outside the block its `record_non_folded` calls would be no-ops.
        journal_entities, entity_deleted, entity_ambiguous = (
            _fold_journal_entities(events))
    non_folded = list(_nf_entries)
    non_folded_refused = refused_events(non_folded)

    # #7936: the non-Point BELIEF leg. `_fold_journal` above is the point-only
    # index and `_fold_journal_entities` models the entity LIFECYCLE (creation /
    # rename / collapse / supersede) but folds no belief, so before this the
    # whole check was blind to a belief on a `:Subject`/`:Object`/`:Event`.
    # ONE authority folds it — `projection.fold_with_entities`, through the very
    # `_apply_belief_props` the Point arm uses — and the result is merged onto
    # the records `_compare_entities` already reads (a record the entity fold
    # does not model is NOT invented here: presence stays the entity fold's
    # question). Called OUTSIDE the non-folded collector on purpose: this walk
    # visits every record, so an unknown/unimplemented type would be recorded a
    # SECOND time and the diagnostic's own count would double.
    for _key, _belief in fold_with_entities(events)[1].items():
        _rec = journal_entities.get(_key)
        if isinstance(_rec, dict):
            _rec.update(_belief)

    # The COUNT is read from the graph directly, NOT from the index's Point
    # slice. The slice is keyed by `id`, so two Point nodes sharing an id (or a
    # node whose id is not a string) would COLLAPSE and the size check would
    # pass while the graph holds more nodes than the journal — a fail-open. The
    # comparison and the digest use the index; the count does not.
    #
    # ⛔ #7936 CLOSED (content leg): the non-Point BELIEF blind spot is removed.
    # The entity leg (`_fold_journal_entities` + `_compare_entities`) folds and
    # compares the belief properties (`_ENTITY_BELIEF_FIELDS`) on every
    # `:Subject`/`:Object`/`:Event`, so a wrong, stale or lost belief there is a
    # `divergence="content"` instead of `ok=True`. Measured before/after on the
    # shape `rebuild_all` had just produced (`hash_match=True`): corrupting the
    # Event's `confidence` read `ok=True, divergence=None`; it now reports
    # `ok=False, divergence="content"` with the `confidence` field named.
    # Pinned by `tests/test_7853_confidence_label_propagation.py::
    # test_content_comparison_sees_a_non_point_belief_divergence` (the inverted
    # form of the gap pin that used to assert the blind spot).
    #
    # ⛔ #7936 CLOSED (baseline leg): the `(label, id)` re-key landed.
    # `_graph_view` is now ONE index over every reference-fold label and
    # `db_hash` folds ALL of it (`_graph_fingerprint`), so a graph-only
    # `:Object`/`:Subject`/`:Event` node — or a belief/status write on one with
    # no journal record — moves the baseline and is reported as
    # `unrecorded-mutation`, exactly as the same mutation on a `:Point` always
    # was. Before it, `db_hash` was Point-only and `_compare_views` never saw a
    # non-Point id, so the mutation was INVISIBLE (`ok=True, divergence=None`).
    # The digest widening moves `content_hash` for every existing baseline,
    # which is why `_PROJECTION_STATE_FORMAT` is 3: a v2 predecessor is a KNOWN
    # predecessor (#8268/#8644) that the next healthy recording run MIGRATES,
    # so no deployment is stranded and none is told to hunt an unjournalled
    # writer that does not exist.
    #
    # STILL OPEN on #7936 (stated, not implied): `log_points` / `db_points` /
    # `_compare_views` remain the POINT leg — they are the Point SLICE of this
    # same index, and the non-Point content question is decided by the entity
    # leg (`_compare_entities`), not by a re-keyed `_compare_views`. Unifying
    # the two folds into one `(label, id)` fold and one field-aware comparison
    # is the remaining step: it is not needed for either leg's correctness
    # today (presence + beliefs on the entity labels are covered by the entity
    # leg, and graph-only moves by the digest), and re-keying
    # `log_points`/`db_points` would change a published verdict field's
    # MEANING (the count of POINTS), which is a separate decision.
    count_rows = projection.query("MATCH (n:Point) RETURN count(n)").result_set
    db_count = 0
    if count_rows and len(count_rows[0]) > 0:
        raw = count_rows[0][0]
        if isinstance(raw, int):
            db_count = raw

    # THE one graph index (#7936): every label the reference fold models, keyed
    # `(label, id)`. Both consumers below are SLICES of it — the Point one for
    # the field-aware comparison, the whole thing for the baseline — so the two
    # cannot read different graphs.
    graph_view = _graph_view(projection)
    graph_by_id = {nid: props for (label, nid), props in graph_view.items()
                   if label == "Point"}

    log_count = len(journal_by_id)
    uncarried = _uncarried(journal_by_id)
    (mismatches, divergent_count, compared_embeddings, excluded_seen,
     one_sided_seen) = _compare_views(journal_by_id, graph_by_id, uncarried,
                                      projection)
    counts_ok = log_count == db_count
    hash_ok = not mismatches
    # #3585: the non-Point leg. `_fold_journal`/`_compare_views` above are
    # Point-only, so an Object/Subject burial would be invisible; this compares
    # the journal's entity reference against the graph's entity nodes.
    graph_entities, graph_entity_names = _entities_from_view(graph_view)
    entity_mismatches, entity_divergent_count, entity_ambiguous_excluded = (
        _compare_entities(journal_entities, graph_entities,
                          graph_entity_names, entity_deleted,
                          entity_ambiguous))
    entities_ok = not entity_mismatches
    # `db_hash` is a pure function of the GRAPH — the fields the verdict covers
    # PLUS the embedding, so a vector-only rewrite of an otherwise-identical
    # graph still moves the baseline (and so cannot hide under `hash_match`).
    # Being graph-only, it is storable and comparable across runs; the verdict
    # itself is the field-aware comparison above, never a digest equality.
    db_hash = _digest(_graph_fingerprint(graph_view, uncarried))

    stored, state_error, state_superseded_format = (
        read_projection_state_detail(log_path))
    # #8268/#7936: a WELL-FORMED predecessor format is version skew, not
    # corruption — so it must NOT keep reading as `state_error`. The gate below
    # (`state_recorded = ... and state_error is None`) is what made the
    # migration unreachable: a predecessor sidecar raised the error on EVERY
    # run, so the re-record was refused for ever and the baseline half stayed
    # INERT. Clearing the error here lets THIS healthy run re-baseline at the
    # current format, and `state_superseded_format` on the verdict reports what
    # was migrated. The MALFORMED/tampered case still sets `state_error`, so
    # "never overwrite a baseline we could not read" is unchanged where the
    # state is evidence rather than skew.
    if state_superseded_format is not None:
        state_error = None
    # Both halves of the invariant are checked. The field-aware comparison
    # catches a graph that disagrees with the journal; the BASELINE catches a
    # graph that moved while the journal did NOT — the case the field-aware
    # comparison cannot see, because every field it would have checked is
    # deliberately excluded (an embedding the journal side lacks, an
    # operator-only key, a one-sided parity field) at the moment it matters.
    # Without this, such a mutation left `hash_match` True and was silently
    # re-baselined by the very next run.
    journal_moved = stored is None or stored.get("last_applied_seq") != len(events)
    graph_moved = stored is not None and stored.get("content_hash") != db_hash
    unrecorded_mutation = graph_moved and not journal_moved

    divergence = None
    if non_folded_refused:
        # #3585 (R9): an event the reference fold could not resolve makes the
        # content comparison vacuously incomplete on BOTH sides. This is the
        # first-class failure R9 exists for — it outranks the content classes.
        divergence = "non-folded"
    elif not counts_ok or not hash_ok or unrecorded_mutation or not entities_ok:
        # Divergence, classified by WHICH side moved since the baseline. Each
        # cause names a different root cause:
        #   journal moved, graph did not → the projection is behind (a dropped
        #     projection write) — "lag".
        #   journal still, graph moved → a write that bypassed the journal
        #     (the #4240 class).
        #   both moved but disagree → a wrong/partial fold.
        if stored is None:
            divergence = "content"
        elif unrecorded_mutation:
            divergence = "unrecorded-mutation"
        elif journal_moved and not graph_moved:
            divergence = "lag"
        else:
            divergence = "content"

    ok = divergence is None
    # Adoption is a property of the RUN, not of whether we were allowed to write
    # the baseline: a read-only probe against a pre-existing graph is still the
    # first healthy sighting of it.
    # A MIGRATED predecessor is a re-baseline of a graph we already knew about,
    # not a first sighting — so it is not "adopted" (`adopted` stays the
    # first-healthy-run signal); `state_superseded_format` names it instead.
    adopted = ok and stored is None and state_superseded_format is None
    state_recorded = False
    if ok and record_state and state_error is None:
        # Never overwrite a baseline we could not read: recording over a
        # malformed/untrusted state would destroy the only evidence of what the
        # graph looked like before, and adopt silently on the next run.
        state_recorded = record_projection_state(
            log_path, last_applied_seq=len(events), content_hash=db_hash)

    # The reported watermark is the POST-RUN truth: whenever this healthy run
    # recorded the baseline, it advanced to (or was created at) this journal's
    # length.
    watermark = stored.get("last_applied_seq") if stored else None
    if state_recorded:
        watermark = len(events)
    watermark_lag = (len(events) - watermark) if watermark is not None else None

    action = {
        "content": ("derived != replay(journal): reconcile by replaying the "
                    "journal through the guarded repair (recover_from_log / "
                    "`tortoise rebuild`) — the check itself never wipes"),
        "lag": ("the journal advanced and the projection did not: re-apply "
                "the missing events (or guarded replay) — do NOT wipe"),
        "unrecorded-mutation": ("a graph write moved the projection without "
                               "advancing the journal (#4240 class): find the "
                               "unjournalled writer. A REPAIR/rebuild that "
                               "landed without a check reaches this branch "
                               "too (the baseline is not cleared by a wipe, "
                               "and this lane may not edit the rebuild path), "
                               "so confirm no unjournalled writer exists "
                               "before re-baselining"),
        "non-folded": ("the journal contains event(s) the reference fold "
                       "could not resolve to exactly one node, so both "
                       "projections are equally incomplete (R8/#3585): fix "
                       "the journal (a missing creation event, an out-of-order "
                       "append, an unjournaled producer) — re-baselining would "
                       "bake in the loss"),
    }.get(divergence)

    return {
        "ok": ok,
        "log_points": log_count,
        "db_points": db_count,
        "delta": log_count - db_count,
        # ── #5011 content verdict ──────────────────────────────────────
        "db_hash": db_hash,
        "hash_match": hash_ok,
        "divergence": divergence,
        "divergent_points": mismatches,
        "divergent_point_count": divergent_count,
        # ── #3585 non-Point leg + the non-folded set (R8/R9) ───────────
        "divergent_entities": entity_mismatches,
        "divergent_entity_count": entity_divergent_count,
        # #3585 review: the bounds are REPORTED, not silent. `…ambiguous` names
        # the Objects the ambiguity rule excluded (so `ok` states what it did
        # not compare), and `…bounds` states the direction and the compared
        # field set.
        "entity_parity_ambiguous": [
            {"label": lbl, "id": iid, "name": nm}
            for lbl, iid, nm in entity_ambiguous_excluded],
        "entity_parity_ambiguous_count": len(entity_ambiguous_excluded),
        "entity_parity_bounds": {
            "direction": "journal->graph",
            "status_compared": sorted(_STATUS_COMPARED_LABELS),
            # #7936: the non-Point belief leg. `fields` are the properties
            # compared on every reference-fold label. `baseline_digest_covers`
            # names what the BASELINE sees; the bound below names what NEITHER
            # half decides, so the check states its own blind spots instead of
            # implying a coverage it does not have.
            "belief_compared": list(_ENTITY_BELIEF_FIELDS),
            # #7936: the `(label, id)` re-key. The digest is no longer
            # Point-only, so a graph-only non-Point node (or a belief/status
            # write on one) MOVES it and is reported as `unrecorded-mutation`.
            # #8859 follow-up review P2 (fail-open-drift): the label list is
            # emitted from `_GRAPH_VIEW_LABELS`, never re-listed here. The
            # literal this replaced (`Point/Object/Subject/Event`) was a SECOND
            # home for the set: a new `projection._ENTITY_CREATION_EVENT_LABEL`
            # row widened the query and the digest while this bound kept naming
            # the old four — so an operator was told the digest does not cover
            # the new label and could dismiss a genuine `unrecorded-mutation` on
            # it as out of scope. That is the false-GREEN #7936 exists to close.
            "baseline_digest_covers": (
                "(label, id) over "
                + "/".join(_GRAPH_VIEW_LABELS)
                + ": the Point "
                "content fields plus, per entity, `status` and "
                "`belief_compared` (graph-only, so a moved entity node moves "
                "the baseline). `status` is covered on EVERY label here, "
                "including Event, where the COMPARISON bounds it out "
                "(`status_compared`) — so a graph-only Event `status` write "
                "is the baseline's to catch, not the comparison's"),
            "baseline_digest_does_not_cover": (
                "non-Point fields outside `belief_compared`+`status` (e.g. a "
                "graph-only `name`/`eventKind` edit with no id change), "
                "the Source/url-keyed labels the reference fold does not "
                "model, nodes with no identity property at all (no "
                "`_ENTITY_ID_PROP` entry, or the property absent or not a "
                "string — `_graph_view_key` drops the node, so it stays "
                "invisible; raw-writer only), and — the one `(label, id)` key "
                "that can hold TWO distinct nodes — a graph-only `id`-only "
                "Event whose id is also an Event's `eventId`: one node wins "
                "the key deterministically (the `eventId`-keyed one) and the "
                "other is not in the digest (#8879)"),
            "point_counts_cover": (
                "the POINT leg only: `log_points`/`db_points` remain point "
                "counts (a graph-only non-Point node is caught by the "
                "baseline digest, not by this count)"),
            # #8859 follow-up review round 2 (P3): all three emitted from their homes,
            # never re-typed. Same bytes today; the point is that a widening which
            # moves the DECISION also moves what the verdict SAYS it decided.
            "name_keyed": sorted(_NAME_MERGE_LABELS),
            "identity": {
                label: ("name" if label in _NAME_MERGE_LABELS
                        else _ENTITY_ID_PROP[label])
                for label, _ in _ENTITY_PARITY
            },
        },
        # #5285 (DEFECT 3): the COUNT (`non_folded_count` below) is
        # authoritative; this list is a bounded SAMPLE. An unrecognised-type
        # journal (the mixed-version case) would otherwise format O(n) strings
        # into the verdict payload; the cap matches the sibling diagnostic
        # lists (`_MAX_DIVERGENT_POINTS`).
        "non_folded_events": [str(e) for e in non_folded[:_MAX_DIVERGENT_POINTS]],
        "non_folded_count": len(non_folded),
        "non_folded_refused_count": len(non_folded_refused),
        "watermark": watermark,
        "journal_events": len(events),
        "watermark_lag": watermark_lag,
        "adopted": adopted,
        "state_recorded": state_recorded,
        "state_error": state_error,
        # #8268/#7936: the format of a WELL-FORMED predecessor baseline this
        # run OBSERVED (None when there was none). `state_recorded` says whether
        # the migration was APPLIED (a read-only run, or a non-healthy run —
        # `state_recorded` is gated on `ok` — reports the predecessor and leaves
        # the re-baseline to the next recording healthy run). Distinct from
        # `state_error`, which now only ever names an UNTRUSTED
        # (malformed/tampered/unknown-format) sidecar.
        "state_superseded_format": state_superseded_format,
        "excluded_fields": excluded_seen,
        "one_sided_fields": one_sided_seen,
        "uncarried_journal_fields": sorted(
            {k for per_point in uncarried.values() for k in per_point}),
        "embedding_compared": compared_embeddings,
        "action": action,
    }


def _corrupt_skip_note(n: int) -> str:
    """The one sentence that reports a replay which salvaged over damage (#5612).

    ONE home for the wording on the two routes a replay reaches a RESULT
    (the ordinary replay and the pending pre-wipe snapshot); the refusal route
    names the same count in its own bracket form, because its sentence is
    about the refusal rather than about the graph. A salvaged replay reported
    as clean on either result route is the silent partial loss this clause
    exists to prevent, and one home is what keeps the two from drifting. It is
    a pure function so the sentence is pinned without a live store.
    """
    if not n:
        return ""
    lost = ("the record it carried is" if n == 1
            else "the records they carried are")
    return (f" ({n} corrupt mid-file line(s) SKIPPED — {lost} NOT in this "
            f"graph, #5612)")


@tolerates_altered_numbers
def recover_from_log(events_dir: str, projection) -> dict:
    """Rebuild a projection from a JSONL event-log dir when its graph was lost.

    Corruption recovery (#428): the projection is a derived view rebuilt from
    the domain event log (the reconstruction source — not the durability
    authority; see docs/durability-posture.md). An embedded DB that answers
    0 nodes while its adjacent JSONL log has events was lost — redislite
    starts fresh when its RDB is corrupt, an interrupted restore left an
    empty graph, or the DB was deleted out from under the log. Rebuild =
    wipe + full replay.

    Safety (mirrors migrate_db's 3-way discriminator):
      - Only rebuilds when db has 0 total nodes and the log has > 0 events
        (the "lost DB" case). Partial divergence (0 < db < log) is left
        alone — the graph may hold SDK-created points that never appear in
        the log; a rebuild would destroy them. db >= log is healthy (the log
        is append-only).
      - Only rebuilds from an UNambiguous log: exactly one adjacent .jsonl.
        Multiple logs could be mid-restore artifacts (backup copy + live
        log); auto-rebuilding the wrong one loses data, so we refuse.
      - Replays faithfully via projection.apply() (same path as restore) —
        NOT rebuild_all, whose replay chain is a different two-pass
        implementation. A lossy rebuild is worse than no recovery for a
        transparent path.
      - EXCEPTION (#2943): for the 0-node case this function handles, a
        durable pre-wipe snapshot sidecar (a previous rebuild_all was
        interrupted after its wipe) routes recovery through
        projection.rebuild_all instead, because that is the only path that
        re-merges graph-only Points / :Batch markers — the apply()-only
        replay cannot restore them (the JSONL has no event for them by
        definition). This is CONTINUOUS with the interrupted run rather than
        a substitution: a sidecar is written only by a rebuild_all that was
        in flight for this very directory, so completing it with rebuild_all
        reproduces exactly the replay the operator asked for. (A sidecar can
        also outlive a COMPLETED rebuild if its retirement could not be
        written; it is then entry-less, `_load_prewipe_snapshot` reports it
        as absent, and this route does not fire — see
        `_clear_prewipe_snapshot`.) Either way the #428 single-log
        discriminator still governs the route (it is a destructive
        wipe+replay, so an ambiguous log set is still refused), and the
        db_count > 0 early return above is unchanged: a PARTIALLY replayed
        graph keeps its sidecar (nothing is lost) for a later explicit
        rebuild — this function never rebuilds a non-empty graph.
      - Query/log failures are caught and reported in the result, never
        raised — the caller decides fail-loud policy. A torn trailing line
        (crash mid-append) is skipped when its loss is the data-LOSS
        direction the tolerance was written for; when the dropped record is a
        removal/terminal one, dropping it would RESURRECT the state it
        removed, so the replay is refused before any event is applied
        (#3316). Mid-file damage is treated the same way: the read SALVAGES it
        (#5612 — skipped, counted, logged) and the #3316 classifier refuses the
        replay when the loss could revive removed state; a loss in the
        data-LOSS direction replays and is REPORTED. The count is named on
        every route. The ONE deliberate exception is
        a PROGRAMMING error in this engine's own wiring: if the projection's
        ``apply_journal_point_restamp`` cannot accept the required
        ``hard_deleted=`` context, this raises ``_RestampSignatureError``
        (a ``TypeError``) instead of reporting a ``torn`` skip — the
        per-record tolerance would otherwise turn a bad call signature into
        `recovered=True` over a journal whose terminalizers were never folded
        (#7719).

    Returns {recovered, log_points, db_points, reason} — plus `onboarding_gap`,
    the trigger flag set whenever a completed replay left the graph's onboarding
    state NOT confirmed intact (non-zero for a confirmed loss, an unverified
    restore, OR a state-UNKNOWN rescue file), plus `onboarding_state_unknown`,
    set ONLY for the rescue-file shape (#4641). `reason` carries an ADDITIVE
    clause naming which of the three applies. `recovered` is still True in
    every one of those cases: the rebuild did complete and refusing to open the
    store would be strictly worse, so the signal is PROPAGATED for the caller
    to branch on rather than swallowed into a success-shaped result.

    #5353 adds the `:TeamMeta` first-org marker on the SAME four-part shape —
    `team_meta_gap`, `team_meta_state_unknown`, and their own additive `reason`
    clauses — because the class is the entire `#2789` first-org guard: a marker
    the rebuild did not carry back leaves that guard reading 0, so the next
    provisioning call re-mints it with the control plane's name and a fresh
    `created`. The two classes report SEPARATELY (neither aggregate absorbs the
    other) for the same reason the three onboarding shapes do.

    ATOMICITY on refusal (#7929): the refusals this function can only reach
    AFTER the replay has landed (the non-folded set and a non-empty `ok: False`
    verdict) are UNDONE before the `recovered: False` result is returned — the
    freshly replayed nodes are wiped, restoring the empty state the call found.
    This is an exact rollback, not a loss: the function returns early on
    `db_count > 0`, so every node present at a post-replay refusal was created
    by that replay, and the JSONL it replayed is a file it never writes. The
    result carries the additive key `replay_rolled_back` (the number of nodes
    the declined replay had produced and this function then discarded) and
    reports `db_points: 0`, because the store really is empty again.

    The destructive pending-snapshot route is the exception, and it is a
    deliberate one: its refusal can arrive only after `rebuild_all` has retired
    its own #2943 sidecar, so the graph is then the last copy of the graph-only
    nodes. A rollback there is REFUSED (with the reason naming it) rather than
    performed, and the partial graph is left for an explicit
    `tortoise rebuild --dir`. A rollback that could not run for any other
    reason (no `_wipe_all_nodes`, or a backend that died) appends a named
    WARNING to `reason` instead and leaves the partial state visible.

    Without this the store was left partially populated and the NEXT open's
    `db_count > 0` refusal ("graph already has nodes — no rebuild") destroyed
    the retry path, serving a store that was neither empty nor correct.

    #3044 adds the one refusal that is NOT rolled back, because it happens
    BEFORE the wipe: the pending-snapshot route's ``rebuild_all`` is called
    with ``require_empty=True``, so it re-validates the 0-node probe this
    function took before the journal parse. If the graph gained nodes in that
    window the wipe is refused with :class:`WipePreconditionRefused` and the
    graph is returned to the caller UNTOUCHED — rolling back there would be
    the very destruction the refusal exists to prevent (the nodes present were
    not created by a declined replay, which is the only shape the rollback is
    sound for).

    #8702 adds `identity_skipped` and its additive `reason` clause: the number
    of records this replay DROPPED because the fold refused their identity
    (``_writable_id``) instead of aborting. The #7406 skip is deliberate and is
    KEPT — a corrupt identity must not make this gate un-runnable on every
    retry — but a skipped record is not a replayed one. It is counted apart
    from `applied` (it used to be counted AS applied, so a journal
    `{healthy PointAdded, PointAdded with a NUL-bearing id}` reported
    "replayed 2 events" over a graph holding one node) and apart from `torn`
    (whose cause is crash damage), and the clause names the TRUE cause: an
    identity that is PRESENT but unwritable, never "missing". `log_points`
    still counts the records READ, so reads = applied + identity_skipped +
    torn + refused reconciles.

    ⛔ GRANULARITY (#8840 review P1): the collector these counts read sees
    every refused identity, and most of them are NESTED inside a record that
    FOLDS (an ``aboutEntities``/``extractedFrom``/``contains_session`` name
    refused while the Point node was already written). Counting any entry as
    "this record was dropped" under-counted `applied`, and on a single-record
    journal that flipped `ok` to False — which WIPES the replay that had just
    succeeded (``_roll_back_declined_replay``), leaving a permanently
    un-openable store. So a record is counted as dropped ONLY from the entries
    its own fold declared (`IdentitySkip.drops_record`, set at the creation
    anchors); nested skips are reported as their own clause — the record IS in
    the graph, only the refused edge/stub is missing — and never suppress
    `applied`. This is also why no ``max(...)`` against the collector length is
    needed: a trailing sweep's skips are nested, and a fold that recorded a
    drop and then raised is counted ONCE, as `torn`.

    KNOWN, PRE-EXISTING and NOT an identity skip: ``apply()``'s malformed
    envelopes (a missing/non-string ``type``, a non-dict ``point``) are dropped
    silently and still increment `applied`. They carry no identity to attribute,
    so the invariant above does not hold for them either way — recorded here so
    the next reader does not assume the counting is exhaustive (#8843).
    """
    import os

    def _node_count() -> int | None:
        try:
            rows = projection.query("MATCH (n) RETURN count(n)").result_set
            return int(rows[0][0]) if rows and rows[0][0] is not None else 0
        except Exception:
            return None

    def _roll_back_declined_replay(result: dict, after: int | None, *,
                                   source_durable: bool = True) -> dict:
        """Restore the EMPTY pre-call state for a refusal found AFTER the
        replay landed, and return `result` (#7929).

        Every refusal below this point is discovered late: the non-folded set
        and the final `ok` verdict both depend on the graph the replay LANDS on
        — which is why #7767 rejected a journal-only pre-flight for the same
        family on `backup.restore` — and the pending-snapshot route's
        `rebuild_all` wipes before it can raise. A verdict after the mutation
        cannot un-apply it by ordering, so it is undone instead.

        SAFE BY THE ENTRY INVARIANT, not by inspection: this function returns
        early on `db_count is None` and on `db_count > 0`, so reaching any
        post-replay branch PROVES the graph held 0 nodes when the call started
        and that every node now present was created by the replay being
        declined. Wiping it back is therefore an exact rollback, not a loss —
        PROVIDED the source those nodes were reconstructed from is still
        durable, which is what `source_durable` asserts:

          * the apply-replay legs rebuild from the adjacent JSONL, a file this
            function never writes, so their source is ALWAYS durable and the
            rollback is always safe; the retry re-reads the same log.
          * the pending-snapshot route goes through `rebuild_all`, which is
            DESTRUCTIVE and whose `@_fail_closed` assertion runs AFTER the
            function body — so a post-replay refusal retires the #2943 sidecar
            (``_clear_prewipe_snapshot``) before it raises. There the graph is
            the last copy of those graph-only nodes, and wiping it would
            destroy data rather than undo a derivation. The rollback is
            therefore REFUSED for that shape, and the partial graph is kept
            for an explicit `tortoise rebuild --dir` (the #2943 "no loss
            without proof" posture) with the reason naming why.

        WITHOUT the rollback the store is left PARTIALLY populated by a
        recovery the caller is about to refuse. The next open then reads
        `db_count > 0` and takes the OTHER refusal (`"graph already has nodes —
        no rebuild"`), so the retry path is gone and a store that is neither
        empty nor correct is served — loudly on the `_recover_or_raise` leg,
        but only as a warning on the lost-graph leg. Restoring the empty state
        keeps the failure loud AND retryable: a repaired journal, or a
        deliberate node-less reset, re-enters this same path.

        Never raises — this function's contract is to REPORT a replay failure
        (`recovered: False`), so a refused or failed rollback becomes an
        additive `reason` clause rather than an exception. A `db_count` of
        0/None means there is nothing to undo (the replay applied nothing, or
        the backend died before the verdict and cannot be asked to wipe).
        """
        if not after:
            return result
        if not source_durable:
            result["reason"] = (
                f"{result.get('reason')}; the declined replay had already "
                f"populated the graph, but its reconstruction source is no "
                f"longer durable — the destructive route retired the #2943 "
                f"pre-wipe sidecar before the refusal — so the rollback is "
                f"REFUSED: wiping here would destroy the last copy of the "
                f"graph-only nodes. The partial graph is kept; replay it "
                f"explicitly with `tortoise rebuild --dir {events_dir}` "
                f"(#7929/#2943).")
            return result
        wipe = getattr(projection, "_wipe_all_nodes", None)
        if not callable(wipe):
            result["reason"] = (
                f"{result.get('reason')}; WARNING: the declined replay had "
                f"already populated the graph and {type(projection).__name__} "
                f"provides no rollback wipe (_wipe_all_nodes) — the store is "
                f"left PARTIALLY rebuilt (#7929)")
            return result
        try:
            # #2944: a non-empty wipe routes through the REBUILD-LANE path,
            # which owns the per-call destructive opt-in token. The wipe is not
            # a new deletion policy — it undoes nodes this very call created.
            wipe(confirm_destructive=True,
                 operation="recover_from_log rollback")
        except Exception as e:
            result["reason"] = (
                f"{result.get('reason')}; WARNING: the declined replay had "
                f"already populated the graph and the rollback wipe FAILED "
                f"({e}) — the store is left PARTIALLY rebuilt; replay it "
                f"explicitly with `tortoise rebuild --dir {events_dir}` "
                f"(#7929)")
            return result
        result["replay_rolled_back"] = after
        # The store IS empty again, so the reported count must say so: leaving
        # the pre-wipe number here would contradict the graph a caller that
        # ignores `replay_rolled_back` goes on to query. The discarded count is
        # preserved on the flag itself.
        result["db_points"] = 0
        return result

    db_count = _node_count()
    if db_count is None:
        return {"recovered": False, "log_points": 0, "db_points": None,
                "reason": "graph unresponsive — recovery requires a live DB"}
    if db_count > 0:
        return {"recovered": False, "log_points": 0, "db_points": db_count,
                "reason": "graph already has nodes — no rebuild"}

    # db_count == 0: enumerate the adjacent logs (exactly one required).
    try:
        files = sorted(f for f in os.listdir(events_dir)
                       if f.endswith(".jsonl"))
    except OSError as e:
        return {"recovered": False, "log_points": 0, "db_points": 0,
                "reason": f"event-log dir unreadable: {e}"}

    # #2943: a durable pre-wipe snapshot next to the log means a previous
    # rebuild_all was interrupted after its wipe — graph-only Points (and
    # :Batch markers) live only in that sidecar. Only rebuild_all re-merges
    # it; the apply()-only replay below would rebuild from the JSONL alone
    # and destroy them permanently (their defining property is that the
    # JSONL has no event for them). Reached only with db_count == 0 — a
    # partially replayed graph keeps the sidecar and is left alone, above.
    #
    # The route is a destructive wipe+replay, so the #428 single-log
    # discriminator applies: with an AMBIGUOUS log set (more than one
    # adjacent .jsonl) we refuse and leave the sidecar in place for an
    # explicit `tortoise rebuild --dir <dir>`. Zero logs is NOT ambiguous —
    # the sidecar is then the only record of anything, and rebuild_all
    # replays it without a journal (the documented #428 ">0 events" clause is
    # knowingly waived here, and only while a sidecar is pending).
    #
    # Presence is decided by the LOADER, not by `os.path.lexists`: a sidecar
    # that exists but is entry-less (a retirement artifact whose unlink
    # failed) must NOT divert this transparent path into a destructive
    # wipe+replay, and one that is unreadable/untrustworthy must be refused
    # here rather than fall through to the apply()-only replay, which would
    # report success while the graph-only nodes it alone held stay lost.
    snapshot_path = prewipe_snapshot_path(events_dir)
    try:
        pending = _load_prewipe_snapshot(snapshot_path) is not None
    except Exception as e:
        return {"recovered": False, "log_points": 0, "db_points": 0,
                "reason": (f"a pre-wipe snapshot at {snapshot_path} cannot "
                           f"be trusted: {e}")}
    if pending:
        if len(files) > 1:
            return {"recovered": False, "log_points": 0, "db_points": 0,
                    "reason": (f"pending pre-wipe snapshot but {len(files)} "
                               f"adjacent JSONL log(s) — refusing to "
                               f"auto-rebuild from an ambiguous log set "
                               f"(#2943/#428); run `tortoise rebuild --dir "
                               f"{events_dir}`")}
        if not callable(getattr(projection, "rebuild_all", None)):
            return {"recovered": False, "log_points": 0, "db_points": 0,
                    "reason": ("pending pre-wipe snapshot needs "
                               "projection.rebuild_all, which "
                               f"{type(projection).__name__} does not "
                               "provide — refusing to replay the JSONL "
                               "alone (#2943)")}
        #2944 L1: rebuild_all is destructive, so the caller opts in at
        # this call site. Safe here because this branch is reached only
        # with db_count == 0 (the early return above) — the wipe is a
        # no-op on an empty graph, but the token is still required.
        #
        # #3044: `require_empty=True` makes that safety RE-VALIDATED at the
        # wipe instead of assumed from the probe ~200 lines above (which
        # happens before the whole journal parse). A graph that gained nodes
        # since is refused, not wiped — see the ATOMICITY note above.
        try:
            counts = projection.rebuild_all(events_dir,
                                            confirm_destructive=True,
                                            require_empty=True)
            nodes = int(counts.get("nodes") or 0)
            events = int(counts.get("events") or 0)
            edges = int(counts.get("edges") or 0)
            # #5612 review (P2): the lines `rebuild_all`'s salvage read had to
            # skip. Carried into `reason` below — this route is a WIPE+REPLAY
            # the caller believes succeeded, so a dropped record must not be
            # visible only in a log line.
            rebuilt_corrupt = int(counts.get("corrupt_lines") or 0)
        except WipePreconditionRefused as e:
            # #3044: nothing was wiped, so there is nothing to roll back — and
            # a rollback would BE the destruction this refusal exists to
            # prevent. `_roll_back_declined_replay` is sound only when every
            # node present was created by the declined replay; no replay ever
            # ran here. Report the guard's OWN measurement, which travels on
            # the exception: re-querying with `_node_count()` would flatten
            # the UNMEASURABLE shape (an absent/NULL count row) to 0 and so
            # describe a store that was never observed as EMPTY, contradicting
            # the refusal's own "could not be MEASURED" message in the very
            # line `_auto_health_recover` reads. `None` = UNMEASURABLE.
            return {"recovered": False, "log_points": 0,
                    "db_points": getattr(e, "db_points", None),
                    "reason": ("rebuild from the pending pre-wipe snapshot "
                               f"refused: {e}")}
        except Exception as e:
            # #3044 review (P1): the rollback is bound to the ONE shape it is
            # sound on. `rebuild_all` exposes `_rebuild_wiped`, set only as its
            # destructive statement is issued; a failure BEFORE that point ran
            # no wipe and applied no replay, so every node present — including
            # one a writer landed in the #3044 window (the seconds-long journal
            # parse IS that window) — came from somewhere other than a declined
            # replay. `_roll_back_declined_replay`'s entry invariant is
            # therefore false on this shape, and its `MATCH (n) DETACH DELETE n`
            # would destroy the very node this route's `require_empty` guard
            # exists to protect. Return untouched instead. Absent attribute
            # (a projection whose `rebuild_all` is a test double) keeps the
            # pre-existing rollback behaviour.
            if getattr(projection, "_rebuild_wiped", True) is False:
                return {"recovered": False, "log_points": 0,
                        "db_points": _node_count(),
                        "reason": ("rebuild from the pending pre-wipe snapshot "
                                   f"failed before the wipe: {e}")}
            # #7929: `rebuild_all` wipes before it can raise, so this refusal
            # can land on a partially replayed graph. Roll it back ONLY while
            # the sidecar still exists — a post-replay `NonFoldedEventsError`
            # (the `@_fail_closed` decorator, which runs after `rebuild_all`'s
            # body) has ALREADY retired it, and the graph is then the last copy
            # of the graph-only nodes: wiping there would lose data, so the
            # rollback is refused and named instead. `db_points` reports the
            # MEASURED count, not a hard-coded 0 — the store is not empty on
            # the refused shape, and saying otherwise is the same lie about the
            # store this change removes.
            after = _node_count()
            return _roll_back_declined_replay(
                {"recovered": False, "log_points": 0,
                 # `db_points` reports the MEASURED count, not a hard-coded 0,
                 # and `None` reports it as UNMEASURABLE — the comment above
                 # said this while the code flattened None to 0 (#7929
                 # review). `_node_count` returns None on a dead backend, and
                 # flattening it asserted an empty store that was never
                 # observed.
                 "db_points": after,
                 "reason": ("rebuild from the pending pre-wipe snapshot "
                            f"failed: {e}")}, after,
                source_durable=os.path.exists(snapshot_path))
        # rebuild_all RAISES on failure, so reaching here IS a completed
        # recovery — not `nodes > 0`: a snapshot can legitimately carry only
        # the #990 half (a quarantined :Batch with no Points), and reporting
        # that as `recovered: False` makes the caller (`_recover_or_raise`)
        # refuse to open a DB whose quarantine state was just restored.
        #
        # `rebuild_all` CAN, however, complete with a gap it could not close
        # (#4641): onboarding state/edges are raw writes no journal event
        # carries, so a post-wipe raise would strand the store empty (#2943).
        # Reporting `recovered: True` while swallowing that gap is the silent
        # partial loss itself, so the counts are PROPAGATED — the new
        # `onboarding_gap` key is additive, but note this is NOT a
        # purely-value-preserving change: `reason` has a suffix APPENDED below
        # for the gap case (in-repo callers only log it). `recovered` itself is
        # unchanged, exactly as the sticky config-reset marker is.
        # The projection returns the shapes as canonical counts — it owns the
        # definitions. Summing the granular keys here double-counted a single
        # destroyed org (it lands in BOTH `onboarding_restore_failures` and
        # `onboarding_missing_orgs`) and let a transient restore failure read
        # as loss. `onboarding_gap` is the trigger (non-zero for all three
        # shapes) and `onboarding_missing_total` discriminates a real loss from
        # an UNKNOWN/unverified one, so a caller that must not describe all
        # three as loss reads the key it needs rather than re-deriving either
        # from the granular keys (#4641 review rounds 6-7).
        onboarding_gap = int(counts.get("onboarding_gap") or 0)
        onboarding_missing_total = int(
            counts.get("onboarding_missing_total") or 0)
        onboarding_unknown = bool(counts.get("onboarding_state_unknown"))
        result = {"recovered": True,
                  "log_points": events,
                  "db_points": nodes,
                  "reason": ("rebuilt from the pending pre-wipe snapshot "
                             f"(#2943): {nodes} nodes, {edges} edges")}
        result["reason"] += _corrupt_skip_note(rebuilt_corrupt)
        result["corrupt_lines"] = rebuilt_corrupt
        if onboarding_gap:
            result["onboarding_gap"] = onboarding_gap
            # Additive, not a chain: a confirmed partial loss and a
            # pre-preservation UNKNOWN can coexist, and one must not suppress
            # the other (#4641 review round 7).
            if counts.get("onboarding_verified") is False:
                result["reason"] += (
                    "; WARNING: the onboarding post-restore verification "
                    "COULD NOT RUN, so the rebuilt graph's onboarding state "
                    "is UNVERIFIED (not confirmed intact, and not observed "
                    "gone) — see #4641")
            if onboarding_missing_total:
                result["reason"] += (
                    f"; WARNING: {onboarding_missing_total} onboarding "
                    "state/edge restore gap(s) the replay could not close — "
                    "see the rebuild ERROR log (#4641)")
            if onboarding_unknown:
                result["onboarding_state_unknown"] = True
                result["reason"] += (
                    "; WARNING: the pending pre-wipe snapshot does not "
                    "carry a usable onboarding record — it either predates "
                    "onboarding preservation, carries only one of the two "
                    "onboarding sections, or inherits a state-UNKNOWN marker "
                    "from an earlier interrupted rebuild — so this graph's "
                    "onboarding state is UNKNOWN (not confirmed absent) — "
                    "see #4641")
        # #5353: the `:TeamMeta` first-org marker, reported separately for the
        # same reason as the onboarding legs — its gap key is a max over three
        # shapes (a confirmed loss, an unverified restore, a pre-preservation
        # UNKNOWN), so a caller must read the shape it needs rather than infer
        # it from the aggregate. A marker that did not come back leaves the
        # `#2789` guard reading 0, which is the whole reason the class is
        # carried.
        team_meta_gap = int(counts.get("team_meta_gap") or 0)
        if team_meta_gap:
            result["team_meta_gap"] = team_meta_gap
            if counts.get("team_meta_verified") is False:
                result["reason"] += (
                    "; WARNING: the :TeamMeta post-restore verification "
                    "COULD NOT RUN, so the rebuilt graph's first-org marker "
                    "is UNVERIFIED (not confirmed intact, and not observed "
                    "gone) — see #5353")
            elif (counts.get("team_meta_expected")
                  and not counts.get("team_meta_restored")):
                result["reason"] += (
                    "; WARNING: the pre-wipe :TeamMeta first-org marker was "
                    "NOT restored, so the #2789 guard reads 0 and a later "
                    "provisioning call can re-mint it with the control "
                    "plane's name — see the rebuild ERROR log (#5353)")
            if counts.get("team_meta_state_unknown"):
                result["team_meta_state_unknown"] = True
                result["reason"] += (
                    "; WARNING: the pending pre-wipe snapshot does not carry "
                    "a usable :TeamMeta record — it either predates marker "
                    "preservation or inherits a state-UNKNOWN marker from an "
                    "earlier interrupted rebuild — so whether this graph's "
                    "first-org marker was destroyed CANNOT be determined — "
                    "see #5353")
        return result

    if not files:
        return {"recovered": False, "log_points": 0, "db_points": 0,
                "reason": "no JSONL event log present"}
    if len(files) > 1:
        return {"recovered": False, "log_points": 0, "db_points": 0,
                "reason": f"ambiguous: {len(files)} adjacent JSONL logs "
                           f"({', '.join(files[:3])}...) — refusing auto-rebuild"}

    # Parse the single log. THE SHARED READER, so this engine cannot diverge
    # from the others: a torn TRAILING line is skipped + counted, and a
    # malformed MID-FILE line is skipped + counted + CLASSIFIED by the salvage
    # read (#5612) and refused by the #3316 refusal below when its loss could
    # revive state — instead of being dropped one-by-one as a local
    # `except: torn += 1` loop did. That loop discarded corruption ANYWHERE in
    # the file with no count, no classification and no report; here the skip is
    # counted (`corrupt_line_count`), classified, and named in `reason`.
    log_path = os.path.join(events_dir, files[0])
    from tortoise.log import (
        EventLog,
        TornTailResurrectionError,
        read_for_replay,
        refuse_torn_tail_revival,
    )

    log = EventLog(log_path)
    try:
        # #5612: SALVAGE read. This is the recovery entry point the journal
        # exists for, so a damaged mid-file line must not make it refuse
        # forever — the skipped lines are counted and classified below, and
        # the #3316 refusal still fires (before any fold) when the loss could
        # revive removed state.
        events = read_for_replay(log)
    except OSError as e:
        return {"recovered": False, "log_points": 0, "db_points": 0,
                "reason": f"event log unreadable: {e}"}
    except ValueError as e:
        # DEFENSIVE since #5612: a salvage read does not raise for a damaged
        # line (it skips and counts it, and the #3316 refusal below decides).
        # Kept because a reader other than `EventLog` — an injected one in a
        # test, or a future format gate — can still refuse to parse, and
        # replaying the surviving records would produce a graph that
        # contradicts the journal. It must be refused BEFORE the replay, since
        # a verdict after the mutation cannot un-apply it.
        return {"recovered": False, "log_points": 0, "db_points": 0,
                "reason": (f"refusing to replay {files[0]}: {e} — the graph "
                           "was NOT rebuilt")}
    # #5612: the mid-file lines the salvage read had to skip. Its own tally and
    # its own clause: it is neither crash damage (`torn`) nor a fold refusal,
    # and folding it into either would name a cause that is false for the
    # records it describes. Read BEFORE the empty-log return below — a journal
    # whose every line is damaged parses to ZERO records, and calling that
    # "empty or unreadable" invites deleting the one artifact that still holds
    # them.
    corrupt = int(getattr(log, "corrupt_line_count", 0) or 0)

    if not events:
        reason = "event log empty or unreadable — nothing to recover"
        if corrupt:
            reason += _corrupt_skip_note(corrupt) + (
                " — the journal is NOT empty: every line failed to parse")
        return {"recovered": False, "log_points": 0, "db_points": 0,
                "reason": reason, "corrupt_lines": corrupt}

    # #3316: a torn TRAILING record whose loss can REVIVE state is not the
    # data-LOSS direction the tear tolerance was written for. A truncated
    # record cannot be reconstructed, so the retraction survives only by not
    # being contradicted: refuse the whole replay BEFORE applying anything,
    # rather than rebuilding a graph that serves removed state as current.
    # The classification AND the message come from the shared home in
    # :mod:`tortoise.log` — this engine only converts the raise into its own
    # dict-shaped result, so there is exactly ONE refusal string to keep true.
    revival = log.torn_tail_revival_records()
    if revival:
        try:
            refuse_torn_tail_revival(revival)
        except TornTailResurrectionError as e:
            # #5612: state the skipped count in the refusal too — the operator
            # needs to know how much of the journal the reader consumed
            # without replaying, not only which type it could not prove
            # harmless.
            _skipped = (f" [{corrupt} mid-file line(s) skipped by the "
                        f"salvage read]" if corrupt else "")
            return {"recovered": False, "log_points": len(events),
                    "db_points": 0, "reason": f"{files[0]}: {e}{_skipped}"}

    torn = log.torn_trailing_count

    # Faithful replay via apply() (preserves context; restore uses the same
    # path). Per-event guard: one bad event must not abort the whole recovery.
    # #3664: EntityLinked records are buffered and folded AFTER the pass —
    # apply() is a one-record API, so folding the type inline would lose a link
    # whose endpoint is created later in the log. This is the same trailing
    # sweep ``rebuild_all``/``rebuild`` give the type, so all three replay
    # engines agree on a forward-reference journal. The records carry their
    # journal seq so the sweep can suppress a link whose endpoint was
    # HARD-DELETED afterwards (#3722 review P2), and the sweep returns the
    # number of links actually APPLIED so a dropped record is not counted as
    # replayed.
    applied = 0
    # #8702: the SKIP is not `applied` and is not `torn`. #7406 made a record
    # whose identity the store cannot take be DROPPED rather than raising (so a
    # corrupt journal cannot make this gate un-runnable on every retry), but the
    # loop still counted the normal return as a replay: a journal
    # `{healthy PointAdded, PointAdded with a NUL-bearing id}` reported
    # "replayed 2 events" over a graph holding ONE node, with the drop visible
    # only in a log warning. The projection records each skip into the run's
    # collector (`collect_identity_skips`), which is what makes it countable
    # here. It keeps its OWN tally and its own clause: `torn` means crash
    # damage, and reporting a deliberate degrade as damage is the confusion
    # #7174 removed for the numeric-domain refusal.
    identity_skipped = 0
    # #7174: a record the store's numeric domain would ALTER is not a TORN
    # record, so it gets its own tally and its own ADDITIVE clause in `reason`.
    # DEFENSIVE by construction: this function runs under the journal-path
    # tolerance (see the decorator), so the param guard itself cannot raise here —
    # the clause covers a projection that refuses DIRECTLY (an injected backend,
    # or a future in-scope raiser). Not dead code: without it such a refusal
    # would be counted as crash damage and reported as a skipped line.
    refused = 0
    first_refusal = ""
    hard_delete_seqs = journal_hard_delete_seqs(events)
    # #7719: the anchor-gated hard-delete MEMBERSHIP map, hoisted ONCE for the
    # whole journal (never per record).
    hard_deleted = hard_deleted_pairs(events)
    entity_link_events: list[tuple[int, dict]] = []
    # #3585 re-review (cycle 2, FIX A): the whole-journal surviving Object
    # keys (`apply()` refuses an ObjectSuperseded whose target the journal
    # never leaves in place, while a forward reference is NOT refused) plus the
    # ids the journal hard-deletes (the named `supersede-target-deleted`
    # exemption).
    # Passed only to a projection whose ``apply()`` accepts them — the fake /
    # injected backends used in tests take ``apply(ev)`` alone, and a kwarg
    # they do not accept would be miscounted as a TORN record.
    journal_object_surviving = journal_object_surviving_keys(events)
    # #7719: derived from the GATED map via `_hard_deleted_any`, so an
    # anchor-suppressed delete no longer exempts the supersede.
    journal_object_deleted = _object_hard_deleted_ids(hard_deleted)
    # #3585 (P1-1): the whole-journal EXISTENCE map — a retract/state-op that
    # PRECEDES its own creation is folded by `rebuild_all`'s hoist and no-op'd
    # live, so this chronological engine must not refuse it. `journal_seq` is
    # passed per record below (the map alone cannot decide the order).
    first_materialized = journal_first_materialization(events)
    # #7936 review: the ORDERED creation/hard-delete boundary for the belief
    # gate — label-agnostic and delete-aware, the sibling of the map above.
    epistemic_existence = journal_epistemic_existence(events)
    apply_kwargs: dict = {}
    _pass_seq = False
    try:
        _apply_params = inspect.signature(projection.apply).parameters
    except (TypeError, ValueError):
        _apply_params = {}
    if "journal_object_surviving" in _apply_params:
        apply_kwargs["journal_object_surviving"] = journal_object_surviving
        apply_kwargs["journal_object_deleted"] = journal_object_deleted
    if "journal_first_materialized" in _apply_params:
        apply_kwargs["journal_first_materialized"] = first_materialized
        _pass_seq = "journal_seq" in _apply_params
    if "journal_existence" in _apply_params:
        apply_kwargs["journal_existence"] = epistemic_existence
    # #3585 (R8/R9): this apply-based engine folds inside the non-folded
    # collector too. A refused event means the recovery REPLAYED an incomplete
    # journal — reporting `recovered: True` there is the false PASS #3947's
    # guard exists to prevent, so the run fails loudly (recovered=False, with
    # the set named) and the caller refuses to open the DB.
    # #8702: the identity-skip collector rides the SAME run boundary — a skip
    # is recorded by the fold and counted below, so the two views of "this
    # record was not folded" cannot drift apart.
    with collect_non_folded() as _nf_entries, \
            collect_identity_skips() as _id_skips:
        # #3305: the Point lifecycle terminalizers are folded by the SHARED
        # whole-journal plan, not by ``apply()``'s inline branch — that branch
        # folds every terminalizer, while ``rebuild_all`` deliberately drops the
        # pre-recreation ones and canonicalizes supersedes. Computing the plan
        # here keeps this recovery engine on the same selection.
        restamp_plan, _ = plan_point_restamp_folds(events)
        # #3305: defer the terminalizers' CORRECTS edges — an endpoint created later
        # in the journal cannot be merged chronologically (see
        # ``fold_deferred_corrects_edges``), and ``rebuild_all``'s after-creations
        # sweep resolves it, so the engines would disagree.
        # #7719 (E7): validate the consumer's contract ONCE, before the loop.
        # A missing/renamed kwarg must fail LOUD — the per-record
        # `except Exception` below would otherwise count it as crash damage
        # (`torn`) and the function would return `recovered=True` over a journal
        # whose terminalizers were never folded (the exact false PASS this
        # change closes). Probing the signature is the same idiom `apply`'s
        # kwargs use above, and it keeps the per-record handler free to count
        # REAL fold failures.
        #
        # Guarded on a NON-EMPTY plan: a journal with no terminalizer folds
        # calls the consumer zero times, so an injected projection that does not
        # implement it is not violating the contract (#7174's fake projection).
        # Only a journal that WILL call it is refused a missing kwarg.
        if restamp_plan:
            try:
                _restamp_params = inspect.signature(
                    projection.apply_journal_point_restamp).parameters
            except (AttributeError, TypeError, ValueError):
                # AttributeError: no such method at all; TypeError: not
                # callable. Both mean the required kwarg cannot be supplied.
                _restamp_params = {}
            if "hard_deleted" not in _restamp_params and not any(
                    _p.kind is inspect.Parameter.VAR_KEYWORD
                    for _p in _restamp_params.values()):
                raise _RestampSignatureError(
                    "recover_from_log: the projection's "
                    "apply_journal_point_restamp does not accept "
                    "hard_deleted=...; refusing rather than reporting a false "
                    "recovery (#7719)")
        deferred_corrects: list[tuple[int, str, str]] = []
        for seq, ev in enumerate(events):
            if isinstance(ev, dict) and ev.get("type") == "EntityLinked":
                entity_link_events.append((seq, ev))
                continue
            try:
                # Keyed on the PLAN, not the raw envelope type — the plan selects
                # by the NORMALIZED type (``_norm`` splices a nested payload), so a
                # raw-type guard would let a ``type``-in-``point`` terminalizer fall
                # through to ``apply()``'s inline branch and its unshared selection
                # (#325/#3722's raw-vs-normalized class).
                # #8702: an identity skip is recorded BY the fold (deep inside
                # `apply()`, where the `_writable_id` gate lives), so the
                # record's outcome is read from the collector around its own
                # fold — not from what `apply` returned, which is `None` for a
                # drop and for a fold alike.
                # #8840 review P1: the delta is a slice, and only the entries
                # whose fold DECLARED a record drop count as one (see the
                # docstring's GRANULARITY note).
                skips_before = len(_id_skips)
                if seq in restamp_plan:
                    # #7719 (E7): the consumer's contract was validated ONCE
                    # before this loop, so a genuine fold failure inside this
                    # call is NOT reclassified as a signature error — it stays a
                    # per-record failure the `except Exception` below counts as
                    # `torn`, exactly as before this change.
                    edge = projection.apply_journal_point_restamp(
                        ev, seq, restamp_plan, hard_deleted=hard_deleted)
                    if edge is not None:
                        deferred_corrects.append(edge)
                else:
                    if _pass_seq:
                        with _replay_write_scope():
                            projection.apply(ev, journal_seq=seq, **apply_kwargs)
                    else:
                        with _replay_write_scope():
                            projection.apply(ev, **apply_kwargs)
            except UnrepresentableNumberError as exc:
                refused += 1
                if not first_refusal:
                    first_refusal = str(exc)
            except _RestampSignatureError:
                # #7719 (E7): a bad call signature is a programming error, not
                # crash damage (``torn``). Fail LOUD rather than letting the
                # generic handler below turn it into a skip that leaves
                # ``recovered=True`` standing.
                raise
            except Exception:
                # #8840 review P2: a fold that recorded a drop and THEN raised is
                # counted ONCE — as crash damage. This arm never reaches the
                # `else` below, so the entries it recorded are never promoted
                # into `identity_skipped` as well (the old
                # `max(identity_skipped, len(_id_skips))` fallback did exactly
                # that).
                torn += 1
            else:
                # #8840 review P1: attribute at RECORD granularity. A record is
                # DROPPED only when its own fold declared it (the creation
                # anchors pass `drops_record=True`); an identity refused NESTED
                # inside a record that folded still leaves that record's node in
                # the graph, so it is `applied` — its skip is evidence in the
                # clause, not a drop. `any()` (not a count): the tally is
                # records, and one record cannot be dropped twice.
                if any(s.drops_record for s in _id_skips[skips_before:]):
                    identity_skipped += 1
                else:
                    applied += 1
        if deferred_corrects:
            try:
                projection.fold_deferred_corrects_edges(
                    deferred_corrects, hard_delete_seqs)
            except Exception:
                logger.exception(
                    "recover_from_log: deferred CORRECTS fold failed; %d "
                    "edge(s) not replayed", len(deferred_corrects))
        if entity_link_events:
            try:
                applied += projection.fold_deferred_entity_links(
                    entity_link_events, hard_delete_seqs)
            except Exception:
                torn += len(entity_link_events)
    # #4125: re-derive the migrated session-Source identity from the journal
    # just replayed. The pre-#4005 basename node is re-materialised by the
    # replay (its `SourceCreated` line, and `_link_source`'s stub mint from any
    # PointAdded naming the bare basename) while the canonical `session:<id>`
    # node is recreated beside it; this engine's trailing sweep is the only
    # place that can converge them, exactly as `rebuild`/`rebuild_all` do.
    # Deliberately OUTSIDE the non-folded collector above: this is a post-replay
    # identity reconciliation, not a fold of any record, so it must not be
    # counted as one. Never raises (post-wipe: #2943).
    from tortoise.session_source_migration import reconcile_after_replay
    reconcile_after_replay(projection, engine="recover_from_log")
    after = _node_count()
    # #8840 review P1/P2: NO `max(identity_skipped, len(_id_skips))` fallback.
    # Every entry a TRAILING sweep records (`fold_deferred_entity_links`) is a
    # nested identity of a record whose links are already counted in `applied`,
    # and a fold that recorded a drop and then raised is already counted as
    # `torn` — so the per-record attribution above is the whole tally. The old
    # `max` could only inflate it (the per-record deltas are disjoint), which
    # broke the documented reconciliation.
    #
    # Nested skips are still EVIDENCE and are reported — as their own clause,
    # which never claims the record is missing from the graph (it is not).
    nested_skips = [s for s in _id_skips if not s.drops_record]
    nf_events = refused_events(_nf_entries)
    if nf_events:
        # #7929: the refused set is only knowable AFTER the replay, so this
        # refusal lands on a graph the replay already populated. Restore the
        # empty pre-call state (the entry invariant proves it), so the caller's
        # refusal does not destroy the retry path.
        return _roll_back_declined_replay({
            "recovered": False, "log_points": len(events),
            # #7929 review: `after is None` means the graph could not be
            # MEASURED (the backend died between the replay and the count), not
            # that it is empty — and the replayed nodes are necessarily left in
            # place, because there is no reachable backend to wipe them. `None`
            # is this surface's established "unmeasurable" value (see the
            # `db_count is None` early return above); reporting 0 here would be
            # the same false "the store is empty" claim this change exists to
            # remove.
            "db_points": after,
            "reason": (
                f"replay refused {len(nf_events)} journal event(s) it could not "
                f"resolve to exactly one node (R8/#3585) — the rebuilt graph "
                f"would be silently incomplete: "
                + "; ".join(str(e) for e in nf_events[:5])),
            # #5285 (DEFECT 3): a bounded SAMPLE; `reason` carries the true
            # count, so the cap cannot make a large refusal look small.
            "non_folded_events": [
                str(e) for e in _nf_entries[:_MAX_DIVERGENT_POINTS]],
        }, after)
    ok = applied > 0 and after is not None and after > 0
    if refused:
        logger.warning(
            "recover_from_log: %d record(s) NOT replayed — the store's numeric "
            "domain (#7174) would have altered them; first: %s",
            refused,
            first_refusal,
        )
    extra = f" ({torn} skipped)" if torn else ""
    if corrupt:
        # #5612: NEVER silent. A salvage read that skipped a damaged mid-file
        # line replayed the records around it, so the recovery SUCCEEDED over a
        # journal it could only partly read — the one thing an operator must
        # not have to infer from silence.
        extra += _corrupt_skip_note(corrupt)
    if refused:
        extra += f" ({refused} refused by the numeric domain)"
    if identity_skipped:
        # #8702: the skip gets its OWN clause, never folded into `torn` (crash
        # damage) and never counted as replayed. It names the TRUE cause: a
        # present-but-unwritable id is not a missing one, and "missing point
        # id" for a record that carries one sends the operator to the wrong
        # place.
        # #8840 review P1(c): the exemplars are the DROPPED records only, and
        # `str(entry)` carries `event_id` — an operator must be able to tell
        # WHICH record is missing from the graph.
        _dropped = [s for s in _id_skips if s.drops_record]
        _named = "; ".join(str(s) for s in _dropped[:3])
        _more = (f" (+{len(_dropped) - 3} more)"
                 if len(_dropped) > 3 else "")
        extra += (
            f" ({identity_skipped} dropped — the fold SKIPPED "
            f"{'a record' if identity_skipped == 1 else 'records'}"
            f" rather than aborting the replay (#7406), so "
            f"{'it is' if identity_skipped == 1 else 'they are'} NOT in this "
            f"graph: {_named}{_more})")
    if nested_skips:
        # #8840 review P1(c): the OTHER direction, and the reason it gets its
        # own sentence rather than sharing the clause above: these records DID
        # replay — their node is in this graph. Only the refused identity's
        # edge/stub is missing, so saying "NOT in this graph" about them would
        # be the #8018 class (a report naming a cause that is false for the
        # record it describes) reintroduced at a new site.
        _nested_more = (f" (+{len(nested_skips) - 3} more)"
                        if len(nested_skips) > 3 else "")
        extra += (
            f" ({len(nested_skips)} identit"
            f"{'y' if len(nested_skips) == 1 else 'ies'} refused INSIDE a "
            f"record that still replayed — the record IS in this graph, only "
            f"the refused edge/stub is missing: "
            + "; ".join(str(s) for s in nested_skips[:3]) + _nested_more + ")")
    result = {"recovered": ok, "log_points": len(events),
              # #5612: additive, and read by the auto-recovery legs
              # (`_auto_health_recover` / `_recover_or_raise`), which DROP
              # `reason` on their clean line — without this key a salvaged
              # replay is a success shape with no loss signal at all.
              "corrupt_lines": corrupt,
              # #7929 review: `after` is `None` when the count could not be
              # taken — pass it through rather than flattening it to 0, so a
              # reader can tell "the store is empty" from "the store could not
              # be measured".
              "db_points": after,
              # The clause is ADDITIVE, so it rides BOTH branches — an `ok: False`
              # result is exactly when a refused count matters most.
              "reason": (f"replayed {applied} events from {files[0]}{extra}"
                         if ok else
                         # #7929 review: with `applied > 0` and no measurement
                         # the graph is NOT empty — the replayed nodes are still
                         # there. Say that, instead of asserting an emptiness
                         # that was never observed.
                         (f"replayed {applied} events from {files[0]}{extra} "
                          f"but the graph could not be measured afterwards, so "
                          f"the replayed state may remain"
                          if applied > 0 and after is None
                          else f"replay produced an empty graph{extra}"))}
    if identity_skipped:
        # #8702: the count as a FIELD, not only as prose — a caller branching on
        # the returned dict must be able to see the drop without parsing
        # `reason`. Additive, and present only when non-zero (the same shape as
        # `onboarding_gap` / `team_meta_gap`).
        result["identity_skipped"] = identity_skipped
    # #7929: `ok is False` is the other late verdict this replay can reach —
    # nothing applied yet the graph is non-empty (a deferred CORRECTS/link fold
    # landed) — and it must not leave that partial population behind either.
    return result if ok else _roll_back_declined_replay(result, after)
