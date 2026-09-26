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
import json
import logging
import math
import os
import re
import struct
from contextlib import suppress
from datetime import datetime, timezone

from .projection import (
    _apply_one,
    _load_prewipe_snapshot,
    _promotion_point_with_operator,
    journal_hard_delete_seqs,
    prewipe_snapshot_path,
)
from .projection.entities import (
    _EntityHandlers,
    _is_persistable_prop_value,
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
    "updatedAt": "write time set by every writer (live and replay)",
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
_UNCARRIED_CONTENT_PROPS: dict[str, str] = {
    "tags": (
        "#2897 — the live writer stores the raw list (`SET n += $props`), the "
        "REPLAYED writer refuses it (`_POINT_LIST_PROPS` is empty, #2795), so "
        "live and replay disagree by construction; the loss is REPORTED, not "
        "treated as a divergence"
    ),
}

# D1/D4: the writer's own deny-list — merged rather than re-listed (see the
# merge note at the bottom of this block).

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

# #548: operators store NO `content`/`pointKind` as node properties (the live
# writer creates the node without them). The journal SEAM synthesizes both
# (`sdk.py` — "Operators may not store 'content' as a node property (#548);
# `_upsert_point_props` requires it — synthesize a fallback") because the replay
# writer sets them unconditionally.
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
    "#548 — operators store no `content`/`pointKind`; the journal seam "
    "synthesizes both because the replay writer sets them unconditionally"
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

# The drop rules of the journal→graph open-set passthrough, as ONE reason per
# sub-rule. Read from the writer's own predicate so the two cannot drift.
_WRITER_DEFAULTS_REASON = (
    "the journal omits it and the graph holds the writer's own deterministic "
    "default — faithful, not the graph authoring a value"
)
_MAX_DIVERGENT_POINTS = 50
_UNCARRIED_NULL = "explicit null — the writer cannot persist a null property"
_UNCARRIED_NON_PERSISTABLE = (
    "a value FalkorDB rejects as a node property (map/dict at any depth, bytes, "
    "set) — `_is_persistable_prop_value` filters it before the SET"
)
_UNCARRIED_LIST = (
    "list-valued prop the REPLAYED writer refuses (`_POINT_LIST_PROPS` is empty, "
    "#2795); the live path may still write it (#2897 class)"
)


def _is_numeric(value) -> bool:
    """True for a real number — `bool` is excluded (it is an `int` subclass)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _values_equal(a, b) -> bool:
    """Field equality, with the store's float round-trip tolerance applied."""
    if a == b:
        return True
    if _is_numeric(a) and _is_numeric(b):
        return math.isclose(float(a), float(b), rel_tol=_FLOAT_REL_TOL,
                            abs_tol=0.0)
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
        graph's derived ``is_operator``.

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
    prov = props.get("provenance")
    if isinstance(prov, dict) and prov.get("source_id"):
        out["provenanceSource"] = prov["source_id"]
    op = props.get("operator")
    if "is_operator" not in out:
        # A flat operator snapshot (OperatorPromoted) already carries the key;
        # never clobber it — only the nested payload needs the derivation.
        out["is_operator"] = bool(op) if isinstance(op, dict) else False
    if isinstance(op, dict) and op.get("op_type"):
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
    """
    out: dict[str, dict[str, str]] = {}
    for pid, props in journal_by_id.items():
        if not isinstance(props, dict):
            continue
        found: dict[str, str] = {}
        for k, v in props.items():
            if k in _POINT_HANDLED or k in _NOT_COMPARED:
                continue
            if v is None:
                found[k] = _UNCARRIED_NULL
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

    Returns ``(mismatches, compared_embeddings, excluded_seen, one_sided_seen)``.

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
                same = [float(x) for x in jvec] == [float(x) for x in gvec]
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


def _graph_fingerprint(graph_by_id: dict,
                      uncarried: dict | None = None) -> dict:
    """Pure GRAPH-only canonical view — the baseline a later run compares to.

    Covers the same declared content plus the embedding, so a vector-only
    rewrite of an otherwise-identical graph still moves the baseline (and so
    cannot hide behind the presence-conditional embedding comparison). It is
    independent of the journal by construction: hashing the union of the two
    sides would make it journal-dependent.

    The keys the COMPARISON declares undecidable are dropped here too, because a
    DECLARED asymmetry must not move the baseline and be read as an unrecorded
    mutation:

      * the one-sided parity keys — a live-only `speaker`/`embedding_verbatim`;
      * `_GRAPH_DEFAULTED_PROPS` — the non-deterministic writer fallbacks the
        journal never states (the baseline is what catches a rewrite of those);
      * the per-point `uncarried` keys — a `tags` list survives on a LIVE graph
        and is DROPPED by the replayed writer (#2897), so without this a repair
        reads as an unrecorded mutation for ever;
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
    for pid, props in graph_by_id.items():
        skip = set(_ONE_SIDED_REASONS) | set(_GRAPH_DEFAULTED_PROPS)
        skip |= set(uncarried.get(pid, {}))
        view = _canonical_point_fields(props, frozenset(skip))
        if isinstance(props, dict) and props.get("is_operator"):
            for k in _OPERATOR_ABSENT_PROPS:
                view.pop(k, None)
        if isinstance(props, dict) and isinstance(props.get("embedding"), list):
            view["embedding"] = props["embedding"]
        out[pid] = view
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
# Trade-off, stated: the state is keyed to the JOURNAL path, so it is
# per-projection where one journal feeds one projection — every supported
# deployment shape, and the journal is the reconstruction source the invariant
# names.
_PROJECTION_STATE_SUFFIX = ".projection-state.json"
_PROJECTION_STATE_FORMAT = 1


def projection_state_path(log_path) -> str:
    """The sidecar path for a journal. `.json`, never `.jsonl`, so it can never
    be mistaken for a second journal by `recover_from_log`'s single-log check."""
    return str(log_path) + _PROJECTION_STATE_SUFFIX


def read_projection_state(log_path) -> tuple[dict | None, str | None]:
    """Read ``(state, error)`` for a journal.

    ``(None, None)`` means "never baselined" — the ADOPTION case, which is
    normal. ``(None, "<why>")`` means a baseline exists but cannot be trusted;
    the caller must not treat that as a divergence, and must not silently adopt
    over it either, so the reason is reported.
    """
    path = projection_state_path(log_path)
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as e:  # any unreadable sidecar is untrusted
        return None, f"projection state unreadable: {e}"
    if not isinstance(data, dict):
        return None, "projection state is not a JSON object"
    seq = data.get("last_applied_seq")
    digest = data.get("projection_hash_sha256")
    if data.get("format_version") != _PROJECTION_STATE_FORMAT:
        return None, (f"projection state format {data.get('format_version')!r} "
                      f"is not {_PROJECTION_STATE_FORMAT}")
    if not isinstance(seq, int) or not isinstance(digest, str) \
            or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return None, f"projection state malformed (seq={seq!r})"
    return {
        "last_applied_seq": seq,
        "content_hash": digest,
        "format_version": data.get("format_version"),
        "updatedAt": data.get("updatedAt"),
    }, None


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
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
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


def _fold_journal(events: list[dict]) -> dict:
    """The journal side of the comparison: the writer's own fold, PLUS the
    lifecycle arms `fold` does not have.

    `fold` is the in-memory POINT-only index and has no arm for the four
    point-lifecycle types — a documented, intentional scope gap its own
    `_NO_POINT_FOLD` names. The GRAPH writer folds all four (`apply()` for the
    promotions; `rebuild_all` — the path `recover_from_log` / `tortoise
    rebuild` use — for the terminalizers), so a reference built on `fold` alone
    reports a healthy graph as diverged on any promotion, and on any
    retract/supersede/invalidate.

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
      PointRetracted  — `_retract`: `status='retracted'` + the `decay_clause`
        belief decay.
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
    """
    anchors = journal_hard_delete_seqs(events)
    by_id: dict = {}
    for seq, ev in enumerate(events):
        t = ev.get("type")
        if t in ("PointPromoted", "OperatorPromoted"):
            p = ev.get("point")
            if isinstance(p, dict) and p.get("id"):
                if t == "OperatorPromoted":
                    p = _promotion_point_with_operator(p)
                entry = by_id.get(p["id"])
                if isinstance(entry, dict):
                    merged = dict(entry)
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
                oid = ev.get("id")
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
                continue
            entry = by_id.get(pid)
            if not isinstance(entry, dict):
                continue
            entry.update(_DECAY)
            if t == "PointRetracted":
                entry["status"] = "retracted"
            elif t == "PointSuperseded":
                entry["status"] = "superseded"
            if t in ("PointSuperseded", "PointInvalidated"):
                entry["outdated"] = True
                entry["validTo"] = ev.get("valid_to")
                # Only when the journal states it: the writer falls back to a
                # wall-clock `_now_iso()`, which the journal cannot state.
                if ev.get("expired_at"):
                    entry["expiredAt"] = ev["expired_at"]
            continue
        _apply_one(by_id, ev)
    return by_id


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
                                          "unrecorded-mutation"
      divergent_points                  — per-id field diagnosis (capped at
                                          `_MAX_DIVERGENT_POINTS`)
      divergent_point_count             — the true number of diverged ids
      watermark / journal_events / watermark_lag — the per-projection seq
      adopted                           — True on the FIRST healthy run against
                                          a pre-existing graph (never "diverged")
      state_error                       — a recorded baseline that exists but
                                          cannot be trusted (distinct from
                                          "never baselined"); reported, never
                                          treated as a divergence
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
    journal_by_id = _fold_journal(events)

    # The COUNT is read from the graph directly, NOT from `len(graph_by_id)`.
    # `graph_by_id` is keyed by `n.id`, so two nodes sharing an id (or a node
    # whose id is not a string) would COLLAPSE and the size check would pass
    # while the graph holds more nodes than the journal — a fail-open. The
    # comparison still uses `graph_by_id`; the count does not.
    count_rows = projection.query("MATCH (n:Point) RETURN count(n)").result_set
    db_count = 0
    if count_rows and len(count_rows[0]) > 0:
        raw = count_rows[0][0]
        if isinstance(raw, int):
            db_count = raw

    rows = projection.query(
        "MATCH (n:Point) RETURN n.id, properties(n)"
    ).result_set
    graph_by_id: dict = {}
    for row in rows or []:
        pid = row[0] if len(row) > 0 else None
        props = row[1] if len(row) > 1 else None
        if isinstance(pid, str):
            graph_by_id[pid] = props if isinstance(props, dict) else {}

    log_count = len(journal_by_id)
    uncarried = _uncarried(journal_by_id)
    (mismatches, divergent_count, compared_embeddings, excluded_seen,
     one_sided_seen) = _compare_views(journal_by_id, graph_by_id, uncarried,
                                      projection)
    counts_ok = log_count == db_count
    hash_ok = not mismatches
    # `db_hash` is a pure function of the GRAPH — the fields the verdict covers
    # PLUS the embedding, so a vector-only rewrite of an otherwise-identical
    # graph still moves the baseline (and so cannot hide under `hash_match`).
    # Being graph-only, it is storable and comparable across runs; the verdict
    # itself is the field-aware comparison above, never a digest equality.
    db_hash = _digest(_graph_fingerprint(graph_by_id, uncarried))

    stored, state_error = read_projection_state(log_path)
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
    if not counts_ok or not hash_ok or unrecorded_mutation:
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
    adopted = ok and stored is None
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
                               "unjournalled writer; re-baseline only after "
                               "the change is explained"),
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
        "watermark": watermark,
        "journal_events": len(events),
        "watermark_lag": watermark_lag,
        "adopted": adopted,
        "state_recorded": state_recorded,
        "state_error": state_error,
        "excluded_fields": excluded_seen,
        "one_sided_fields": one_sided_seen,
        "uncarried_journal_fields": sorted(
            {k for per_point in uncarried.values() for k in per_point}),
        "embedding_compared": compared_embeddings,
        "action": action,
    }


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
        raised — the caller decides fail-loud policy. Torn trailing lines
        (crash mid-append) are skipped, not fatal.

    Returns {recovered, log_points, db_points, reason} — plus `onboarding_gap`,
    the trigger flag set whenever a completed replay left the graph's onboarding
    state NOT confirmed intact (non-zero for a confirmed loss, an unverified
    restore, OR a state-UNKNOWN rescue file), plus `onboarding_state_unknown`,
    set ONLY for the rescue-file shape (#4641). `reason` carries an ADDITIVE
    clause naming which of the three applies. `recovered` is still True in
    every one of those cases: the rebuild did complete and refusing to open the
    store would be strictly worse, so the signal is PROPAGATED for the caller
    to branch on rather than swallowed into a success-shaped result.
    """
    import json as _json
    import os

    def _node_count() -> int | None:
        try:
            rows = projection.query("MATCH (n) RETURN count(n)").result_set
            return int(rows[0][0]) if rows and rows[0][0] is not None else 0
        except Exception:
            return None

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
        try:
            counts = projection.rebuild_all(events_dir)
            nodes = int(counts.get("nodes") or 0)
            events = int(counts.get("events") or 0)
            edges = int(counts.get("edges") or 0)
        except Exception as e:
            return {"recovered": False, "log_points": 0, "db_points": 0,
                    "reason": ("rebuild from the pending pre-wipe snapshot "
                               f"failed: {e}")}
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
        return result

    if not files:
        return {"recovered": False, "log_points": 0, "db_points": 0,
                "reason": "no JSONL event log present"}
    if len(files) > 1:
        return {"recovered": False, "log_points": 0, "db_points": 0,
                "reason": f"ambiguous: {len(files)} adjacent JSONL logs "
                           f"({', '.join(files[:3])}...) — refusing auto-rebuild"}

    # Parse the single log, tolerating a torn trailing line.
    log_path = os.path.join(events_dir, files[0])
    events: list[dict] = []
    torn = 0
    try:
        with open(log_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(_json.loads(line))
                except Exception:
                    torn += 1
    except OSError as e:
        return {"recovered": False, "log_points": 0, "db_points": 0,
                "reason": f"event log unreadable: {e}"}
    if not events:
        return {"recovered": False, "log_points": 0, "db_points": 0,
                "reason": "event log empty or unreadable — nothing to recover"}

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
    hard_delete_seqs = journal_hard_delete_seqs(events)
    entity_link_events: list[tuple[int, dict]] = []
    for seq, ev in enumerate(events):
        if isinstance(ev, dict) and ev.get("type") == "EntityLinked":
            entity_link_events.append((seq, ev))
            continue
        try:
            projection.apply(ev)
            applied += 1
        except Exception:
            torn += 1
    if entity_link_events:
        try:
            applied += projection.fold_deferred_entity_links(
                entity_link_events, hard_delete_seqs)
        except Exception:
            torn += len(entity_link_events)
    after = _node_count()
    ok = applied > 0 and after is not None and after > 0
    return {"recovered": ok, "log_points": len(events),
            "db_points": after if after is not None else 0,
            "reason": f"replayed {applied} events from {files[0]}"
            + (f" ({torn} skipped)" if torn else "") if ok
            else "replay produced an empty graph"}
