"""Entity CRUD handlers for FalkorProjection — Point, Subject, Object, Document, Event, Source.

#2490 rebuild-decay note (APPLIED, then MOVED by #2884 A3): the terminalizing
folds must decay the claim's belief (``decay_clause('n')``) or INVALIDATED /
SUPERSEDED claims resurrect their frozen posterior post-rebuild (the ghost
class #2490 eliminates). Both decays now fold INLINE in pass-1b via
``_decay_point_belief`` — at the event's own journal seq — rather than riding
``_fold_point_invalidated`` / ``_fold_point_superseded``: those run in the
trailing sweep AFTER the whole pass-1b loop, so a decay applied there
clobbered every later inline belief writer (replay != live). The two folds
below own the status/flag/stamp/CORRECTS half only.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

# #2795: cycle-free helper (tortoise/ids.py is stdlib-only). sdk.py imports
# projection at module top, so the sdk-private `_content_hash` is NOT
# importable here.
# #7936: the same leaf supplies the CANONICAL entity id for a `(label, name)` —
# the name stubs this file mints must carry the identity the entity path
# registers, or a node's IDENTITY is not a pure function of the journal (a
# random stub id moved on every replay) and the reference fold cannot model
# the stub at all.
from tortoise.entity_identity import all_name_holder_ids
from tortoise.ids import content_hash as _content_hash
from tortoise.ids import entity_name_id as _entity_name_id
from tortoise.live import (
    decay_clause,  # #2490: rebuild folds decay terminal posteriors
    epistemic_disjunction,  # #5380: the target-position widening
    epistemic_label_queries,  # #7853: id-anchored reads sweep labels, one query per label
)

# The store's NUMERIC domain (INT64 / non-finite reject) — a leaf module, so
# this cannot cycle. `_annotator_value_ok` answers the shape + `#7369`
# parameter-parse half; the numeric axis is added to BOTH questions (write and
# comparison — `_store_holds_prop_value`), because a value the domain alters or
# refuses is not held as stated: FalkorDB 4.16–4.20 CLAMPS an int outside INT64
# while FalkorDB 6.x REFUSES it at parameter parse, so writing it would abort a
# replay after the wipe on 6.x and comparing it would report a divergence no
# journal value can explain.
from tortoise.numeric_domain import numeric_alteration_reason

# #7719: the SHARED terminalizer-miss classifier and the non-folded recorder.
# ``nonfolded`` is a stdlib-only leaf module, so this import cannot cycle.
from tortoise.projection.nonfolded import (
    SHAPE_EDGE_MESSAGE_UNREPLAYED,
    classify_terminalizer_miss,
    record_non_folded,
)
from tortoise.source_identity import normalize_source_url, resolve_source_key
from tortoise.vector_write import (
    clear_embedding,
    clear_then_set_prefix,
    set_embedding_clause,
)

logger = logging.getLogger(__name__)


#: ``_name_holder_count`` DELEGATES to ``entity_identity.all_name_holder_ids``
#: — that module owns both the label vocabulary (it is what
#: ``_require_identity_label`` refuses on) and the name-holder predicate
#: (``_name_predicate``). A second hand-written query here is how the fold's
#: MERGE-key decision silently stops following the shared predicate — if
#: ``_name_predicate`` ever gains case-folding or a second name spelling, the
#: count and the identity probes would disagree. The same #2901 drift lesson
#: that keeps the label tuple shared applies to the count itself.
#:
#: See :func:`_name_holder_count` for why the count matters.
def _name_holder_count(g, label: str, name: str) -> int:
    """How many ``label`` nodes hold ``name`` — **any** status.

    The two name-keyed labels MERGE by NAME (``MERGE (o:Object {name:$name})``,
    #7369), so this count decides whether the name can serve as the MERGE key at
    all. When it exceeds one, that pattern matches EVERY carrier and the
    ``ON MATCH`` id write stamps ONE id onto ALL of them — duplicate-id
    corruption (#8864): two distinct entities then answer every id-keyed read
    (``MATCH (n {id:$x})``) as one.

    ⛔ Count EVERY carrier, not only the LIVE ones. ``MERGE … {name:$name}``
    matches terminal carriers too, so a ``status='superseded'`` twin is re-id'd
    by the same ``ON MATCH`` clause — measured: one live + one superseded
    carrier of a single name both come out carrying the incoming id. The
    read-side predicate (``live._terminal_excluded``, as used by
    ``entity_identity.resolve_entity_id``) is the WRONG count here precisely
    because it excludes the terminal twin that participates in the corruption.
    ``all_name_holder_ids`` is the ANY-status probe for exactly that reason.

    Cost: one index seek — ``Object.name`` / ``Subject.name`` are range-indexed
    (``_RANGE_INDEX_LABEL_PROPS``).

    ⛔ This counts the GRAPH, and the graph has MORE carrier sources than the
    journal models read (#8864 review P2): explicit-id writers, connector
    writers, and the name-STUBs ``_event_plain_merge`` / ``_mint_subject_stub``
    mint during replay. Those models now materialise the mention-stub half
    (``projection._event_mentioned_name_stubs``) because an undercount there
    puts them on the opposite MERGE-key branch and they bury an id the graph
    keeps; ``edges._mint_subject_stub`` is still unmodelled (#8914). A future
    carrier source must therefore be taught to the models as well — counting
    the graph is right HERE (the MERGE sees every node), and the models are the
    side that must catch up.
    """
    return len(all_name_holder_ids(g, label, name))


# #6239: live's TWO ``PointPromoted`` emitters are both gated on the Point's
# status at the instant they fire, so a replay that re-applies the snapshot's
# ``status='live'`` unconditionally can re-liven a Point live never re-livened:
#
#   * ``sdk.promote_point`` promotes through a ``WHERE n.status = 'draft'`` CAS
#     (the reviewer-gated draft→live door);
#   * the capture auto-promotion's candidate query is
#     ``n.status IS NULL OR n.status = 'draft'``.
#
# ``None`` therefore answers BOTH "the node carries no status" and "the node
# does not exist yet" — the #2256 shape where a ``PointPromoted`` snapshot is
# the node's ONLY durable record and the replay must materialize it live.
_PROMOTE_LEGAL_PRIOR_STATUS: frozenset = frozenset({"draft"})


def promote_status_applies(prior_status) -> bool:
    """#6239: may a ``PointPromoted``'s ``status='live'`` be written over
    ``prior_status``?

    ``True`` only where live itself could have emitted the record — see
    ``_PROMOTE_LEGAL_PRIOR_STATUS``. Anything else (``superseded`` — a supersede
    is status-TERMINAL — ``retracted``, ``archived``, a legacy word, the empty
    string) is a status the promote could not have overwritten live, so the
    record's status write is a NO-OP on every replay engine.

    This is the ONE predicate shared by the graph replay engines
    (``apply()``, ``rebuild_all``'s inline pass and its post-supersede re-fold)
    and by the reference fold (``consistency._fold_journal``), so the promoted
    status cannot drift between them — before #6239 ``rebuild_all`` kept the
    terminal ``superseded`` while the chronological ``apply()`` arm re-livened
    it, and neither engine agreed with the other.

    The rest of the snapshot still folds (content/embedding/…), exactly as
    ``rebuild_all``'s inline pass has always applied it; only the status column
    is withheld.
    """
    return prior_status is None or prior_status in _PROMOTE_LEGAL_PRIOR_STATUS


def gated_promote_payload(p: dict, prior_status) -> dict:
    """``p`` with its ``status`` withheld when
    :func:`promote_status_applies` is False.

    The withheld form is ``{"status": None}``, which the shared Point writer
    reads as ``n.status=coalesce($st, n.status, 'live')`` — i.e. it PRESERVES
    the existing terminal status instead of re-livening it. A copy is returned
    only when something is withheld, so the un-gated path stays byte-identical
    to the pre-#6239 payload.
    """
    if promote_status_applies(prior_status):
        return p
    return {**p, "status": None}


def is_non_operator_payload(payload: dict) -> bool:
    """True when a journal Point payload states its OWN non-operator identity.

    ONE home for that identity, because the record's replay is split across two
    modules: ``_upsert_point_props`` (here) decides what the node IS, and
    ``edges._create_edges`` decides whether to rebuild the canonical reverse
    ``(op)-[:mitigated_by]->(m)``. Two hand-spelled copies of it would drift —
    and did. ``consistency._canonical_point_fields`` spells the same one-line
    rule on the journal side and must keep matching it.

    The polarity is load-bearing. ``not payload.get("is_operator")`` is ALSO
    true when the key is ABSENT, and the EventAPI / extractor / ingest producer
    (``api.py::_point``) emits an ``OperatorAdded`` with no flag at all — that
    form would re-type every generic IMPL operator on the main write path.

    This is the IDENTITY half only. Whether the record also authorises the
    canonical reverse edge is a separate question — see
    ``has_usable_mitigation_strength``.
    """
    return payload.get("is_operator") is False


def has_usable_mitigation_strength(payload: dict) -> bool:
    """True when the payload carries a strength ``mitigate_operator`` could write.

    A mitigation is a NON-operator Point that carries the mitigation's own
    ``mitigation_strength`` and an ``operator`` EDGE descriptor (``#4937``)
    solely so the rebuild fold can restore its ``(m)-[:IMPL]->(op)`` half. The
    descriptor alone does NOT make one: ``EventAPI.add_point(content, prov,
    **fields)`` forwards arbitrary fields, and ``_create_edges`` is the SHARED
    live+replay edge writer, so a low-level producer can attach a descriptor to
    a non-operator point. Minting ``(op)-[:mitigated_by]->(m)`` for such a
    payload dampens that operator by the fallback strength (measured ``w_eff``
    1.0 -> 0.7) — a belief change the producer never asked for.

    Nor is mere PRESENCE enough. The writer's own accepted domain is what the
    fold must mirror, and ``_persist_extra_props`` decides what actually
    reaches the node:

    * a map/list/``None`` strength is dropped by that passthrough (the Point
      layer's ``_POINT_LIST_PROPS`` is EMPTY), so the node ends up with no
      strength and a fabricated edge dampens at the fallback anyway;
    * a non-numeric value is persisted. It used to make
      ``weights.compute_operator_weight`` RAISE on every read of that operator;
      since #6151 that read degrades an unreadable strength to the fallback, so
      the value is merely useless rather than poisonous — but the fold below
      still refuses it, because admitting it would persist a strength nothing
      can ever read.
    * an out-of-band value (``-0.5``, ``5.0``, ``2**62``) is refused by the
      writer's own band check (``sdk.mitigate_operator``: ``if not 0 <=
      strength <= 1: raise ValueError``), so admitting it would authorise a
      dampening at the read-clamped band edge that no record ever asked for —
      a belief change with no writer behind it.

    That band IS the writer's own accepted domain, and the fold mirrors it
    exactly. A ``bool`` is accepted on purpose, because the writer accepts one:
    ``mitigate_operator``'s range check passes for ``False``/``True``,
    ``weights.mitigation_dampening_factor`` clamps it to the band edge, and the
    node stores it — so refusing it here would silently drop a REAL
    mitigation's edge (measured ``w_eff`` 0.9 live -> 1.0 rebuilt for ``False``).
    Rejecting it belongs at the writer, as its own decision.

    A rejection must also be safe for a corrupt/hand-edited journal, which is
    the input this whole fold exists to tolerate: refusing never raises, so one
    hand-edited record can never abort the rebuild after the wipe. Refusal is
    TOTAL — any exception out of the band comparison is a refusal, not an
    abort, because ``isinstance`` also admits a hostile ``int``/``float``
    SUBCLASS whose comparison methods raise, and ``_create_edges`` is the live
    writer too (``EventAPI.add_point(**fields)`` forwards caller objects).
    Refusal is fail-safe — no edge, no fabricated dampening — so a hand-edited
    record keeps the PRE-#5048 behaviour (no reconstructed edge) rather than
    acquiring a dampening no writer asked for.
    """
    value = payload.get("mitigation_strength")
    if not isinstance(value, (int, float)):
        return False
    # The band check IS the finiteness check, and it must come FIRST. A
    # comparison on an ``int``/``float`` does not convert, so a value past
    # float range (``10**400``) is refused by the comparison itself — no
    # ``OverflowError`` — and ``nan``/``inf`` fail it too. ``math.isfinite``
    # here would be a second, unreachable opinion.
    try:
        # ``bool(...)``: a numpy scalar (``np.True_``) or an ``int`` subclass
        # with a truthy comparison would otherwise leak through this ``-> bool``
        # annotation, and the predicate is used as a value by ``consistency``.
        return bool(0 <= value <= 1)
    except Exception:  # total refusal — see the docstring
        return False


def _terminal_object_statuses() -> list:
    """The canonical Object TERMINAL status list, for a Cypher ``excluded``
    parameter (#3309).

    ONE source of truth: ``commit_ops.OBJECT_TERMINAL_STATUSES``, which is also
    what ``recall_state``'s default view filters on. Imported at call time to
    keep ONE source of truth rather than restating the literal here — the defect
    #3309 fixes. (Cycle-freedom is a property, not the motive: ``commit_ops``
    imports only ``tortoise.live``, which never reaches ``projection``, so there
    is no path back. The supersede fold below does the same import for the same
    reason.)

    Returned as a ``list`` because that is what the engine accepts for an ``IN``
    parameter (a ``frozenset`` reaches it as an unserialisable type).
    """
    from tortoise.commit_ops import OBJECT_TERMINAL_STATUSES

    return list(OBJECT_TERMINAL_STATUSES)


# #2729 review P2: the last successfully compiled set of declared-lifecycle
# kind spellings. `_status_fold_index` fails open (an empty index) when the
# pack registry is unavailable, but an empty index also empties the built-in
# fold's `$declared` ownership guard — so a lifecycle-owning kind would take
# the connector fold again (`github.issue.closed` → `completed`, a status
# outside its declared vocabulary). Retaining the last-good forms keeps the
# OWNERSHIP half through an outage; only the declared folds go silent (the
# registry, their source, is gone), which is the honest degradation.
_LAST_GOOD_KIND_FORMS: frozenset = frozenset()


def _status_fold_index():
    """#2729: the compiled pack-declared fold surface, or an EMPTY index.

    Reads the process pack registry (``domain_loader._get_registry``) at CALL
    time — a function-level import keeps the projection free of a module-level
    cycle, and a registry that is unavailable (packs missing, load degraded)
    yields an empty index so the built-in compatibility folds keep working
    rather than raising on the write/replay hot path.

    Never raises: the fold is a state cache and a pack-resolution failure must
    not take down an event write (the #1930 ponytail contract).

    ⚠️ The EMPTY index is not fully empty: its ``kind_forms`` carries the
    last-good declared-lifecycle spellings (see `_LAST_GOOD_KIND_FORMS`), so
    an outage does not re-open the connector-fold collision for a kind that
    owns its state. What IS lost is the declared folds themselves — they are
    compiled FROM the unavailable registry — and the registry-unavailable
    warn-once is the only signal. A warm process keeps the ownership guard; a
    cold process with no registry cannot know the catalog and degrades to the
    pre-#2729 behaviour.
    """
    global _LAST_GOOD_KIND_FORMS
    from tortoise.status_folds import StatusFoldIndex

    try:
        from tortoise.domain_loader import _get_registry

        reg = _get_registry()
        if reg is None:
            empty = StatusFoldIndex()
            empty.kind_forms = _LAST_GOOD_KIND_FORMS
            return empty
        index = reg.status_fold_index
        _LAST_GOOD_KIND_FORMS = index.kind_forms
        return index
    except Exception as e:  # fail-open: pack resolution never breaks a write
        # Warn-ONCE (#1930): this runs per event, and an unavailable registry
        # must not spam the log on the write/replay hot path.
        from tortoise.pack_registry import _warn_packs_once

        _warn_packs_once(
            ("status-fold-index", "unavailable"),
            "status-fold index unavailable; built-in folds only: %s", e)
        empty = StatusFoldIndex()
        empty.kind_forms = _LAST_GOOD_KIND_FORMS
        return empty


def _emit_fold_violation(target_kind: str, event_kind: str, name: str) -> None:
    """#2729: a declared fold's explicit ``object`` target names no Object.

    The kind's #1934 enforcement level (``warn | retry | block``) is RESOLVED
    and recorded in the violation detail, then reported through the shared
    ``enforcement.emit_violation`` seam. ⚠️ The level does not currently
    CHANGE behaviour: ``emit_violation`` logs every violation at WARNING and
    nothing reads the emitted ``status_fold_unapplied`` code, so ``warn``,
    ``retry`` and ``block`` are identical here. There is no retry loop on the
    projection write path, and the one real retry consumer
    (``kind_classifier``) retries near-miss kind CLASSIFICATION, not a status
    fold — so this is a recorded level, not a routed ladder. Making it
    level-aware (or giving the code a consumer) is a separate change; do not
    describe it here as if the ladder already routes it.

    Fail-open: the reporter never raises into the fold.
    """
    try:
        from tortoise.enforcement import emit_violation, resolve_enforcement

        level = resolve_enforcement(kind=target_kind)
        emit_violation(
            code="status_fold_unapplied",
            kind=target_kind,
            detail=(f"declared fold for event kind {event_kind!r} on Object "
                    f"{name!r} found no Object of kind {target_kind!r} "
                    f"(enforcement={level})"),
        )
    except Exception:  # a report never breaks the fold
        logger.debug("status-fold violation reporting failed", exc_info=True)

# #2894: FalkorDB stores scalars and arbitrarily NESTED arrays of scalars (a
# tuple is encoded as an array); a map/dict-valued property — including an
# array that contains one at any depth — raises on `SET n += $extra`.
# Verified empirically against the docker lane (#2958 review): `[[1, 2], [3, 4]]`,
# `[[[['deep']]]]` and `(1, 2)` are accepted and stored, while `{'k': 1}`,
# `[1, {'a': 1}]`, bytes and sets are rejected. Shared predicate used by every
# `_persist_extra_props` layer (Subject/Object/Document/Event/Source) and by
# the Point open-set writer (#2795).
_PERSISTABLE_SCALAR_TYPES: tuple = (str, bool, int, float)
# Depth cap for the recursive array check — a self-referential structure must
# not recurse without bound (a JSON payload cannot contain one, but the props
# passthrough accepts a plain Python object).
_PERSISTABLE_MAX_DEPTH: int = 32


def _is_persistable_prop_value(value, _depth: int = 0) -> bool:
    """True for values FalkorDB accepts as node properties (#2894, #2795).

    Scalars and arbitrarily nested arrays of scalars (lists AND tuples) are
    stored. Maps/dicts — including any array that contains one, at any depth —
    and bytes/sets are rejected by the engine, so they are filtered before
    the SET rather than crashing it. `bool` is a subclass of `int`, so it is
    covered by `_PERSISTABLE_SCALAR_TYPES`.
    """
    if isinstance(value, _PERSISTABLE_SCALAR_TYPES):
        return True
    if _depth >= _PERSISTABLE_MAX_DEPTH:
        return False
    if isinstance(value, (list, tuple)):
        return all(_is_persistable_prop_value(x, _depth + 1) for x in value)
    return False


def _store_holds_prop_value(value) -> bool:
    """True when the store holds ``value`` EXACTLY as the payload states it.

    ⛔ This is the bar for BOTH questions the `tags` fix asks — *does the writer
    put the property on the node?* (``_stated_tags``, ``_revise_point``) and
    *may the journal's value be compared against the node's?*
    (``consistency._uncarried``) — because the OUTCOME for a value the numeric
    domain refuses is NOT uniform across ENGINE VERSIONS (and a "lane" is not
    an engine identity — ``FalkorProjection.__init__`` redirects a path-based
    projection onto ``TORTOISE_DB_URI`` when that variable is set, so an
    "embedded lane" run can be measuring docker): FalkorDB 4.16–4.20 — the
    embedded ``falkordblite 0.10.0`` build and ``falkordb-server:v4.20.6`` —
    CLAMP a Python ``int`` outside INT64 (the ``numeric_alteration_reason``
    reason says "would be SILENTLY clamped"), while FalkorDB 6.x
    (``falkordb-server:latest``) RAISES ``Failed to parse the value of parameter
    'tags'`` (all measured — #8317 review round 5). Writing it would therefore
    ABORT a ``rebuild_all`` after the wipe on a 6.x engine — the class the
    content and dim
    clauses above refuse to crash on (#325/#3689 review P1) — so a recovery fold
    must contribute NO property for it and a comparison must not compare it.
    "The store holds it exactly as stated" is the only bar safe everywhere,
    which is why the WRITE question is answered here rather than by the
    transport gate alone (#8317 review rounds 3-4).

    On top of the shape allowlist ``_is_persistable_prop_value`` and the write
    gate, the numeric domain is the second test — a value either gate would
    ALTER (or refuse to parse) is carried in a different form than the journal
    states (or not at all), so it must not be written or compared:

      * the ``#7369`` transport gate nulls anything ``_value_ok`` refuses — a
        non-finite float, a NUL/lone-surrogate string, a null inside an array,
        a map/bytes/set;
      * a 4.16–4.20 engine CLAMPS a Python ``int`` outside INT64 (``2**70``
        reads back ``9223372036854775807``) while a 6.x engine refuses to parse
        it (both measured — ``tortoise/numeric_domain.py``), so the journal's
        value is not the graph's either way.

    Callers — ``_stated_tags`` and ``_revise_point`` (the write decision) and
    ``consistency._uncarried`` (the comparison decision) — i.e. the SAME bar for
    both questions, for the engine-divergence reason stated above. Declaring
    "the graph holds the journal's value" for something an engine alters
    (4.16–4.20 clamps it; 6.x refuses it at parameter parse) contradicts the
    graph that was just read — reproduced with a hand-written
    ``PointAdded(tags=[nan])`` and with ``tags=[2**70]``.

    ``_annotator_value_ok`` lives in the package ``__init__`` — this module is
    imported BY that package, so the import is function-local; by the time any
    replay calls this, the package is fully initialised.
    """
    from tortoise.projection import _annotator_value_ok
    return (_annotator_value_ok(value)
            and numeric_alteration_reason("tags", value) is None)


def _stated_tags(payload) -> tuple[bool, object]:
    """``(writes_property, value)`` for a payload's ``tags`` key (#2897).

    The ONE rule for *"the payload states a ``tags`` value this writer must
    apply"* to the Point create/upsert fold (``_upsert_point_props``) and the
    pass-1b boundary report that reads its outcome — never re-spelled at either
    site (#5048).

    A value ``_store_holds_prop_value`` accepts is written VERBATIM (a
    non-list scalar included — live stores one, and only the EDGE half is
    list-only). ``None``/absent, and any value the transport gate refuses (a
    map, a non-finite float, a NUL string) or the numeric domain would alter or
    reject, states NO property. The predicate is the store-EXACTNESS gate the
    comparison also uses, deliberately: a value whose fate is not uniform across
    engine versions (4.16–4.20 CLAMP an over-range int; 6.x refuses it at
    parameter parse) contributes no
    property rather than aborting the rebuild after the wipe (#8317 review
    rounds 3-4). The same gate answers ``_revise_point``'s property clause, so
    the add and edit arms cannot disagree.

    ⚠️ Three corrections to an earlier version of this paragraph, all MEASURED
    on the embedded engine — the helper's ANSWERS were right, its reasoning was
    not, and a wrong reason is what the next lane copies:

      * a create here builds the node from a ``CREATE (n:Point {…})`` property
        MAP; it is ``update_point`` whose write is ``SET n += $props`` (the
        post-CREATE passthrough loop was removed in #2952). The rule is the same
        either way — the payload's value IS the property — but do not attribute
        the create path to ``SET``.
      * ``SET n += {tags: null}`` does NOT "leave the node's own value alone":
        it REMOVES the property. That is why ``None`` is a create-side no-op
        (a fresh node has nothing to remove) while the EDIT path must spell the
        clear explicitly — ``_revise_point`` emits ``n.tags = NULL``.
      * a value the driver cannot send (a dict) is degraded to null by the
        ``#7369`` guard BEFORE dispatch, so live ALSO ends with no property
        (measured: ``update_point(tags={"a": 1})`` → ``'tags' IN keys(n)`` is
        False). Answering ``(False, None)`` here therefore matches live on the
        create path; the edit path's clear is ``_revise_point``'s, which spells
        it explicitly for exactly this case.

    A LIST-only reading was a live/replay divergence, not a safety property: a
    scalar ``tags`` is stored live and the replayed node kept nothing — which
    ``check_consistency`` then reported as a ``tags`` divergence (#8317 review
    P1, reproduced).

    Total, deliberately: this reads a possibly hand-edited journal line, and a
    raise here would abort a replay whose wipe has already run — the recovery
    path's own rule. A non-dict payload, a missing key, a ``None`` and a value
    this store cannot persist all answer ``(False, None)``.
    """
    if not isinstance(payload, dict) or "tags" not in payload:
        return False, None
    value = payload.get("tags")
    if value is None or not _store_holds_prop_value(value):
        return False, None
    return True, value


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def _usable_instant(val) -> bool:
    """True when a journal record states an instant a replay may write.

    #5048: an instant read from a record rides as a Cypher parameter, so it
    needs the same ``_writable_id`` gate the ``id`` does — ungated, a corrupt
    line raises ``ResponseError: Property values can only be of primitive
    types`` from inside a replay that has ALREADY wiped the graph, and pass-1b
    has no per-event try/except (measured end-to-end: a ``PointRetracted``
    carrying ``ts={"evil": 1}`` aborted ``rebuild_all`` and left the claim
    LIVE).

    The extra non-empty test is for the FALSEY half: the ``or _now_iso()``
    this replaces treated ``""`` as "no instant stated", so an empty string
    must keep falling back rather than become a written ``updatedAt``.
    """
    from tortoise.projection import _writable_id
    return bool(val) and _writable_id(val)


def _journal_instant(ev: dict, key: str = "ts") -> str:
    """The record's instant when it states a usable one, else the writer's
    clock.

    Byte-identical to the ``ev.get(key) or _now_iso()`` it replaces for every
    value that expression accepted AND that `_usable_instant` accepts: a
    truthy, driver-writable **string**. The differing family is the TRUTHY
    values `_usable_instant` rejects — any non-string, a string carrying a NUL
    or lone surrogate, and an ARRAY of primitive strings (which FalkorDB would
    have written, but which is not an instant this schema stores). A FALSY
    value (``""``, ``None``, ``0``) is NOT a difference from the predecessor:
    the replaced expression fell back to the clock for it too. For those the
    difference is between this helper and ``_retract`` (see the NOTE below),
    which is the case this PR turns on.

    Also NOT covered here, and measured: a live caller that folds an event dict
    it built itself (``commit_ops``'s ``fold_ev`` for ``ObjectSuperseded``)
    passes no ``ts``, so the live write, the record's envelope ``ts`` and this
    helper each hold a different read. Journal-derived values still ride
    parameters ungated elsewhere (``valid_to``) — #7369.

    NOTE the policy difference from ``_retract``, which is deliberate and not
    an oversight: when a record states NO usable instant this helper writes the
    clock (these arms have always stamped ``updatedAt`` as part of their SET),
    while ``_retract`` writes no column at all, because it has a ``#330`` twin
    in ``_apply_one`` and neither may invent a value the journal never stated.
    """
    val = ev.get(key)
    return val if _usable_instant(val) else _now_iso()


# ── #2884 D3: the belief-state value gate ─────────────────────────────────
# The four properties the EP/dream write-backs carry. ONE gate, shared by
# BOTH replay folds (`_fold_confidence_changed` here and `_apply_one` in
# `tortoise/projection/__init__.py`), so a corrupt journal line cannot be
# admitted by one dispatcher and rejected by the other — the #330 parity
# contract, and the reason this is a single helper rather than two copies.
BELIEF_PROPS: tuple = ("confidence", "posterior_alpha", "posterior_beta",
                       "lastDreamedAt")
# #2884 A5: the terminalizing flag that rides the SAME record. `assess_source`
# flags a stale assessment EP-dead in the one live statement that decays it
# (`SET p.outdated = true, {decay_clause}`), so the ConfidenceChanged record
# must carry the flag too — otherwise a rebuilt graph treats the superseded
# assessment as EP-active while live treats it as dead (`_apply_source_
# inheritance` filters its factor query on `outdated`). It is a STRICT BOOL:
# `isinstance(True, int)` is exactly why it cannot go through the numeric
# gate, which would persist `1` where every lifecycle reader expects a flag.
BELIEF_BOOL_PROPS: tuple = ("outdated",)
# The numeric keys must be a REAL FINITE number. ``bool`` is rejected
# EXPLICITLY: ``True`` is an ``int`` to Python, but it is not a belief, and
# persisting it verbatim poisons the next EP run's ``float(...)`` read.
_BELIEF_NUMERIC_PROPS: tuple = ("confidence", "posterior_alpha",
                                "posterior_beta")


def _belief_bool_value_ok(value) -> bool:
    """True when a boolean belief flag (``outdated``) may be folded.

    STRICT bool: ``1``/``0`` and every other truthy value are dropped rather
    than coerced — the lifecycle writers read a boolean, and a coerced int
    would round-trip into a graph state no live write produces. ``None`` is
    valid (the journaled clear, mirroring the numeric gate). A corrupt line
    degrades to a dropped flag, never an engine error.
    """
    return value is None or isinstance(value, bool)


def _belief_prop_value_ok(key: str, value) -> bool:
    """True when ``value`` may be folded as the belief property ``key``.

    ``None`` is VALID for every key — it is the journaled CLEAR (the EP
    run-evidence pre-write and ``set_point_baseline`` both write JSON null),
    and dropping it would leave a stale prior in place across a rebuild.

    The three numeric keys accept ``int``/``float`` but NOT ``bool`` and NOT
    a non-finite float (NaN/±Inf). FalkorDB's parameter parse does NOT reject
    a string/list/bool for a numeric prop — it persists it verbatim — and the
    next EP run then raises ``ValueError`` from ``float(rows[0][0])``,
    bricking the EP lane until a hand repair. ``lastDreamedAt`` is an ISO
    TIMESTAMP string and takes its own string gate, rejecting a NUL or
    lone-surrogate value the driver cannot encode as a parameter. A corrupt
    line degrades to a DROPPED value, never an aborted recovery AFTER the
    wipe — the guard's own documented purpose (#2884).

    The numeric branch is TOTAL: an ``int`` no double can hold must return
    ``False``, not raise. ``json.loads`` parses an integer literal of ANY
    magnitude as an arbitrary-precision ``int``, and ``math.isfinite``
    coerces its argument to a C double, so a >308-digit journaled posterior
    raised ``OverflowError`` — inside pass-1b, AFTER ``DETACH DELETE`` —
    which is the exact abort-after-the-wipe failure this guard exists to
    prevent. A belief no double can hold is not a belief (the JSON
    reader would read it as ``Infinity``); the same rule `hook_install`
    applies to an over-large ``timeout`` int (#3808 R16).
    """
    if value is None:
        return True
    if key in _BELIEF_NUMERIC_PROPS:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        try:
            return math.isfinite(value)
        except OverflowError:
            # int too large to convert to float — a value no double holds.
            # Reject (drop the key), never abort the recovery pass.
            return False
    # lastDreamedAt (and any future non-numeric belief key): an encodable str.
    if not isinstance(value, str) or "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False  # lone surrogate (driver rejects at encode)
    return True


#: #5380: the four edge-resident EP message slots, paired by the direction
#: each pair is written by. Read by the edge-message fold below. The producer
#: constant ``TortoiseEP.EDGE_MESSAGE_SLOTS`` lives in ``tortoise/ep.py``,
#: which this module cannot import (import cycle), and the census tool has a
#: third copy in ``tools/edge_census.py`` — so this tuple and those two are
#: pinned equal by ``tests/test_4503_edge_relationship_accounting.py``. That
#: pin is the ONLY thing that reds when a fifth slot is added to one copy and
#: not the others: the emit-site assertion compares the journaled RECORDS to
#: ``EP_EDGE_SLOTS``, never to this fold's tuple.
EDGE_MESSAGE_PROPS: tuple = ("msg_alpha", "msg_beta",
                             "back_msg_alpha", "back_msg_beta")


def _edge_message_value_ok(value) -> bool:
    """True when ``value`` may be folded as an EP edge message slot.

    Mirrors ``_belief_prop_value_ok``'s numeric branch EXACTLY, and for the
    same reasons (#2884): ``None`` is VALID — it is the journaled CLEAR, which
    the live ``SET r.x = null`` also performs — while ``bool`` and a
    non-finite float are refused, because the driver persists them verbatim
    and the next EP run's ``float(rows[0][0])`` read then raises, bricking the
    EP lane until a hand repair. A non-numeric (str/list) value is refused for
    the same reason. A corrupt line degrades to a DROPPED slot, never an
    aborted recovery pass AFTER the wipe.

    The numeric branch is TOTAL: an ``int`` no double can hold returns
    ``False``, never raises (``json.loads`` parses an integer literal of ANY
    magnitude as an arbitrary-precision ``int``; ``math.isfinite`` coerces to a
    C double), which is the exact abort-after-the-wipe failure the guard in
    ``_belief_prop_value_ok`` documents.
    """
    if value is None:
        return True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


# #388: connector sourceKinds eligible for choke-point Source materialization in
# _upsert_event. Pinned explicitly (NOT SOURCE_KIND_DEFAULTS registry membership,
# which also contains 'document' + T0-T4 tier forms) so the mining.py exclusion is
# precise: mining events carry bare `source` without sourceKind/sourceUrl and must
# never materialize a Source node.
_CONNECTOR_SOURCE_KINDS: frozenset = frozenset({
    "github_issue", "github_pr", "linear_card", "slack_message",
    # #388: linear_cycle rides BOTH legs — the explicit container-level
    # fallback sourceUrl (linear.py) and the kind leg (belt-and-suspenders,
    # mirroring the github.py wiring comment): a cycle event missing
    # sourceUrl must still materialize via its registered kind.
    "linear_cycle",
})


def _is_real_source_url(url: str) -> bool:
    """#388 conf-60: a REAL web URL (http(s) permalink / resource) vs a
    container-level fallback key (`slack:{channel}`, `linear:{team_key}`,
    bare `source` string). The stale-sweep direction guard keys off this:
    fallback keys are never authoritative over a real URL (see
    ``_materialize_connector_source``)."""
    return url.startswith(("http://", "https://"))


# #5214: the keys of a materialized connector payload that are NOT item content.
#
# The connector Source's `contentHash` is the digest of the payload the
# projection was handed for that item (`_connector_content_hash` below), so what
# is EXCLUDED here decides what a "new version" means. Two classes go:
#
#   1. ENVELOPE — `type`/`projection_version`/`version` are the record's own
#      shape, and the SDK/journal keys (`event_id`, `ts`, `initiated_by`,
#      `agent_id`, `actor_user_id`, `corrects`, `_merge_run_id`, `__runId`) are
#      stamped on the journal line rather than by the connector. A change to any
#      of them is a change to how we WRITE, never to what we hold, and folding
#      them in would advance a version on a journal-format bump.
#
#   2. IDENTITY of the graph record — `eventId`/`id`. This exclusion is the one
#      that carries weight: ONE item state is materialized by MORE than one
#      producer with DIFFERENT ids. GitHub's poll path mints
#      `github-issue-{repo}-{n}-created` for an already-closed issue, while the
#      webhook path mints `…-closed` for the same closed state
#      (`github_map.issue_to_event`), and `github.py`'s entity path re-emits the
#      same event again. Hashing the id would give one unchanged item two
#      different digests depending on which producer ran last — a version
#      transition with no content change, i.e. a FALSE stale on every other
#      poll. The digest is defined over the item's materialized content, so
#      the record that carried it is deliberately not part of it.
#
#   3. IDENTITY of the SOURCE NODE — `source`/`sourceUrl`. Both are the node's
#      own MERGE key, resolved to ONE node by `resolve_source_key` /
#      `normalize_source_url` (`tortoise/source_identity.py`) BEFORE this
#      digest is computed: those collapse spelling variants (trailing slash,
#      host case, tracking params) onto the same `:Source`. Hashing the raw
#      spelling would give two records of one UNCHANGED item two digests — a
#      version bump on a respelled URL, the same false stale the id exclusion
#      prevents for a producer switch. The digest is over item CONTENT; the
#      node's identity is not content.
#
# `embedding` is excluded for the same reason in a different medium: it is a
# DERIVED vector, so re-encoding (or an embedding-model change) would advance
# the version with no item change.
_CONNECTOR_HASH_EXCLUDED: frozenset = frozenset({
    "type", "projection_version", "version",
    "id", "eventId",
    "source", "sourceUrl",
    "embedding",
    "event_id", "ts", "initiated_by", "agent_id", "actor_user_id",
    "corrects", "_merge_run_id", "__runId",
})


def _connector_content_hash(inner: dict) -> str:
    """#5214: the connector Source's `contentHash` — sha256 over the
    MATERIALIZED CONNECTOR PAYLOAD.

    Why this is the honest digest (ONTOLOGY §4.6: *"the digest of the content
    read"*, *"a re-fetched source whose content differs is a new version"*): for
    a connector item the graph IS the only copy we hold — §9.6 bounds a version
    at *"windows and hashes only, never content copies"* — so the payload the
    projection materialized for that item is exactly the content read. That is
    also why this is NOT per-sourceKind code: each connector emits its own
    payload shape (`github_map`'s single vocabulary for GitHub, the connector's
    own mapper for Slack/Linear), so keying the digest to the payload scopes it
    per kind by construction, with no registry to keep in sync.

    Determinism is a requirement, not a nicety: this is the input to a version
    transition, so two records of ONE item state must hash equal (see
    ``_CONNECTOR_HASH_EXCLUDED`` — the excluded id and the node identity are
    the cases that make it true). Canonical form: sorted keys, no insignificant
    whitespace, UTF-8.

    THE INPUT IS A CANONICAL PROJECTION, NOT THE PRODUCER'S PAYLOAD. When the
    event carries the shared mapper's ``itemState`` projection
    (``github_map._item_state``), that projection is the ENTIRE hashed body.
    The producer-shaped envelope — the record id it minted, the action label it
    stamped on ``eventKind``, the extra keys it happened to include — is
    structurally excluded, so no producer can diverge from another by carrying
    a different envelope. `eventKind` is why this matters: the poll mints
    `github.issue.open` for the open state a webhook's `reopened` action mints
    as `github.issue.reopened`, and hashing the label made one unchanged item
    two versions. The projection carries `state` (and, for PRs, `merged`)
    instead, so the signal the label used to hold survives the exclusion.
    Producers that declare no projection (Slack, Linear) keep the generic
    payload path — their `eventKind` may be the only carrier of a state change.

    The encoder is STRICT — deliberately no ``default=str``. Falling back to
    ``str()`` for an arbitrary object makes the digest process-dependent: a
    `set` iterates in hash order, so `PYTHONHASHSEED` alone changes the digest
    and mints a version bump on every restart with no content change. A payload
    we cannot canonicalize is one we cannot honestly anchor.

    Returns `""` (never raises) when the payload cannot be canonicalized — a
    connector Source with no computable digest stays honestly unanchored, the
    same state as a `_mint_source_stub`. A raise here would abort a connector
    poll mid-ingest.
    """
    import hashlib
    import json
    projection = inner.get("itemState")
    if isinstance(projection, dict):
        body = {"itemState": projection}
    else:
        body = {k: v for k, v in inner.items() if k not in _CONNECTOR_HASH_EXCLUDED}
    try:
        blob = json.dumps(body, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
    except (TypeError, ValueError):
        return ""


def _build_search_text(title, summary=None, topics=None) -> str:
    """#125: compute the Document FTS search surface.

    Concatenates title + summary + topics (None-safe). Always includes title
    so every Document has a search floor.
    """
    parts = [title, summary] + list(topics or [])  # noqa: RUF005
    return " ".join(filter(None, parts))


def _seq_events(events):
    """Yield ``(journal_seq, record)`` for a deferred-fold batch (#3722 P2).

    ``rebuild_all``/``rebuild``/``recover_from_log`` buffer the deferred
    records as ``(seq, record)`` pairs so the hard-delete staleness rule can
    compare against the faithful journal order; a bare record is also
    accepted (its list index is the seq), so a caller holding no envelope
    still works and an older call site does not have to change shape.
    """
    for idx, item in enumerate(events):
        if (isinstance(item, tuple) and len(item) == 2
                and isinstance(item[1], dict)):
            yield item[0], item[1]
        else:
            yield idx, item


#: Defensive bound on a journalled vector's length. The writer emits exactly
#: `EMBEDDING_DIM`; a hand-edited/corrupt record must not make a rebuild
#: materialise an unbounded list. Far larger than any real width.
_MAX_JOURNALLED_VECTOR_LEN = 8192


def _writable_journalled_vector(value) -> list[float] | None:
    """#5004: normalise a journal-carried vector, or None if unusable.

    A journal record is a FILE — it can be hand-edited, truncated, or written
    by an older/newer code path. Mirrors the #19/#4305 recovery-path rule: an
    unusable value must DEGRADE to "not restored", never raise after the wipe
    and strand the rebuilt graph.

    ``float`` alone is NOT sufficient, which an earlier version of this helper
    got wrong (verified): ``float('nan')`` and ``float('inf')`` succeed, and
    ``json`` round-trips ``NaN``/``Infinity`` by default, so a crafted line
    reached ``vecf32()`` and raised AFTER the wipe on every retry. A huge JSON
    integer raises ``OverflowError``, which is not a ``TypeError``; both are
    handled here. Non-finite and non-numeric values are refused, and an
    implausibly long list is refused before it is materialised.
    """
    if not isinstance(value, (list, tuple)) or not value:
        return None
    if len(value) > _MAX_JOURNALLED_VECTOR_LEN:
        return None
    out: list[float] = []
    for x in value:
        try:
            f = float(x)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(f):
            return None
        out.append(f)
    return out or None


def _record_embedding_identity(p: dict, warned: set) -> None:
    """#5004: record (do not silence) a journalled vector's identity mismatch.

    The decision required by the design is that the journal carries the vector
    AS WRITTEN — the past cannot be recomputed — so it is restored verbatim,
    and the divergence from the configured embedder is RECORDED here. A model
    change therefore becomes explicit: the operator re-embeds deliberately, or
    not at all. It must never be resolved by silently re-encoding, which is
    exactly how a changed embedder yielded a different graph from the same
    journal. (The canonical durability authority is
    `docs/durability-posture.md` — this docstring deliberately makes no
    competing source-of-truth claim.)

    ``warned`` is the caller's per-rebuild-pass de-dup set (the `#2958`
    `_deny_drop_warned` pattern): a swap would otherwise emit one line per
    Point and bury the signal in O(N) identical warnings. The mismatch is
    keyed by the identity pair, so every DISTINCT divergence still reports.
    """
    try:
        from tortoise.embeddings import embedding_identity
        cur_model, cur_rev = embedding_identity()
    except Exception:  # noqa: BLE001, RUF100
        # #5119/#5148 review: this is an ADVISORY warning path, and it runs
        # inside the recovery replay (`_upsert_point_props` -> here, once per
        # journalled vector). Letting it raise aborted EVERY replayed event, so
        # `recover_from_log` counted zero applied and refused the DB with
        # "replay produced an empty graph" — the #5119 failure shape,
        # re-entered through the identity CHECK rather than the write. A
        # replay must never fail on a warning: with no configured identity
        # there is nothing to compare the record against, so say nothing.
        return
    j_model = p.get("embedding_model")
    j_rev = p.get("embedding_revision")
    if j_model is None and j_rev is None:
        # No identity on the record. That is EXPECTED for a re-emitted snapshot
        # (`stamp_journal_embedding(creating=False)` deliberately omits it, since
        # the vector may predate a model change) and for a hand-built/foreign
        # record. Restore verbatim — the journal is still the truth — and stay
        # silent: warning here would fire on every routine promote, which is the
        # false-alarm class the design's "record which" is not asking for. The
        # creating record, where one exists, owns the attestation and WILL warn
        # on a genuine mismatch.
        return
    if j_model != cur_model or j_rev != cur_rev:
        key = ("identity", j_model, j_rev)
        if key not in warned:
            warned.add(key)
            logger.warning(
                "replay: journaled embedding was computed by %s@%s but this "
                "store's embedder is %s@%s — restoring the JOURNALED vector "
                "verbatim (the past cannot be recomputed); re-embed "
                "deliberately to change it (#5004)",
                j_model, j_rev, cur_model, cur_rev)
    j_text = p.get("embedding_text_hash")
    content = p.get("content")
    if not (isinstance(j_text, str) and isinstance(content, str)):
        return
    # #5004 round-3: `_content_hash` is `text.encode("utf-8")`, which RAISES
    # `UnicodeEncodeError` on a lone surrogate. This runs INSIDE `rebuild_all`,
    # after the wipe and before the graph write, so an unguarded call would
    # destroy the graph and strand every retry on one foreign/hand-edited line
    # — the same after-the-wipe class the NaN/Overflow guard closes for the
    # vector. `_revise_point` already wraps the same call (#19); a hash that
    # cannot be computed simply cannot be compared.
    try:
        cur_text_hash = _content_hash(content)
    except Exception:  # noqa: BLE001, RUF100
        return
    if j_text != cur_text_hash:
        key = ("text", j_text, cur_text_hash)
        if key not in warned:
            warned.add(key)
            logger.warning(
                "replay: a journaled embedding's text-hash does not match "
                "its content (Point %s: %s != %s) — the vector was computed "
                "from different text (#5004)",
                p.get("id"), j_text, cur_text_hash)


def _warned_set(handler) -> set:
    """Per-handler de-dup set for the #5004 replay warnings (the `#2958`
    `_deny_drop_warned` pattern). Reset by `rebuild_all` alongside it, so each
    rebuild pass reports every distinct divergence once."""
    warned = getattr(handler, "_embed_identity_warned", None)
    if warned is None:
        warned = handler._embed_identity_warned = set()
    return warned


def _valid_transit_pairs(value):
    """#5256: the ONE shape check for the `extractedFrom` read-version carrier.

    Returns the value when it is a non-empty list/tuple of 2-element
    ``[str, str]`` pairs whose members are BOTH non-empty/non-blank, else
    ``None``. BOTH the node-prop clause (`_upsert_point_props`) and the edge
    fold (`_upsert_point_edges`) call THIS through the shared
    `_point_source_transit` selector (which adds the own-ref gate/filter) — an
    all-or-nothing predicate shared by both writers.

    Why one predicate and all-or-nothing: the two writers must agree on a
    corrupt payload. With a partial filter on the edge side, a
    partially-malformed carrier (``[[DOC,'h9'], {"bad":1}]``) stamped
    ``r.sourceVersion`` from the one valid pair while the node clause wrote NO
    carrier — an edge anchor with no gate-compared record, the exact opposite
    of "a corrupt journal contributes no anchor". A body value that is a dict
    (or a list containing one) is also what would make Falkor raise mid-replay.

    Why the non-empty member rule: an empty (or blank) hash is an ABSENT read
    version, and ``resolve_source_versions`` now omits both (``h.strip()``),
    while ``_anchor_on_create`` nulls ``''`` on the edge — so a blank member is
    never a legitimate value. Enforcing it here keeps the two writers in
    lockstep even on a hand-written or foreign journal line: without the rule,
    ``[[DOC, '']]`` left the node carrier claiming a per-link pair for DOC
    while the authoritative edge carried NULL (and a blank ``'   '`` stamped
    garbage on the edge, since the CASE only matches ``''``) — a gate-invisible
    disagreement between the two writers, re-derived from the same journal line
    so `check_consistency` saw no divergence. (Note: only the SDK
    ``create_point`` CREATE-map write bypasses this predicate; the EventAPI lane
    sets the carrier and then emits ``PointAdded``, which reaches
    ``_upsert_point_props`` and so runs the SAME predicate as replay does. The
    agreement is a property of both sides applying the same absent rule, which is
    why ``resolve_source_versions`` must strip-test too.)
    """
    if not isinstance(value, (list, tuple)) or not value:
        return None
    for pair in value:
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2
                and isinstance(pair[0], str) and pair[0].strip()
                and isinstance(pair[1], str) and pair[1].strip()):
            return None
    return value


def _point_source_refs(extracted_from) -> list:
    """The Point's OWN ``extractedFrom`` refs, normalized exactly as
    ``_link_source`` fans them out: a bare ``str`` is ONE ref (never iterated
    character-wise), any other value is a sequence, and falsy members are
    skipped. ``[]`` when the Point has no ``extractedFrom`` at all."""
    if not extracted_from:
        return []
    refs = ([extracted_from] if isinstance(extracted_from, str)
            else list(extracted_from))
    return [r for r in refs if r]


def _point_source_transit(p: dict):
    """#5256: the carrier to WRITE for a journal payload — the shape-validated
    ``sourceVersionTransit`` pairs, gated on the Point OWNING an
    ``extractedFrom`` and filtered to that Point's own raw refs.

    ``None`` when there is nothing to record. The gate matters because the
    carrier is a transit for an edge that will be written: without it a
    hand-written/foreign journal line plants a stray carrier with no
    ``extractedFrom`` edge at all. (Before this gate the stray made the node equal
    its own journal payload, so the #5011 gate stayed GREEN and the corruption was
    invisible; after it the graph is unfaithful to that line and the gate REPORTS
    the divergence — a deliberate behaviour change, pinned by
    ``test_carrier_without_an_extractedfrom_is_not_written``.) The filter matters
    because the pairs are keyed by the RAW ref (see ``resolve_source_versions``),
    so a pair whose key is not one of the Point's own refs is not one the edge
    fold can consume — writing it would record an anchor for an edge this Point
    never has. BOTH writers (``_upsert_point_props`` and ``_upsert_point_edges``)
    use THIS selector, so the two writers always select the same pair SET. (That
    is a claim about the SET, not about per-pair VALUES: a foreign/hand-written
    carrier may list two raw refs that resolve to ONE Source node with different
    hashes, and the edge fold stamps the resolved-first value while the node keeps
    both pairs.)
    """
    pairs = _valid_transit_pairs(p.get("sourceVersionTransit"))
    if pairs is None:
        return None
    refs = _point_source_refs(p.get("extractedFrom"))
    if not refs:
        return None
    kept = [[k, h] for k, h in pairs if any(k == r for r in refs)]
    return kept or None


#: #8006: the declared payload-LIST props whose canonical GRAPH form is a
#: space-joined STRING. ONE declaration, read by ``_upsert_point_props`` (which
#: applies the flatten rule before the list policy can deny them), by
#: ``flatten_search_keys_prop`` below, and by ``consistency.py`` (which must not
#: call a value "uncarried" that the writer in fact carries — its tables are
#: read from the writer's own declarations precisely so a change here cannot
#: silently desync it).
#: ⛔ NOT a substitute for ``_POINT_LIST_PROPS``: these keys are NOT written as
#: arrays. They are flattened to a string, the only form the fulltext index can
#: match (#5482/#5444).
_POINT_FLATTEN_LIST_PROPS: frozenset = frozenset({"search_keys"})


def flatten_search_keys_prop(props: dict) -> None:
    """R2 (#1541) D3 — THE ONE HOME of the flatten rule for
    ``_POINT_FLATTEN_LIST_PROPS`` keys (today: ``search_keys``).

    Those props must be FLAT space-joined STRINGS in the graph: FalkorDB's
    fulltext index (``db.idx.fulltext.createNodeIndex``,
    ``("Point", ["content", "search_keys"])``) does NOT index array-valued
    properties (verified on server v4.16.7: a list-valued ``search_keys`` never
    matches, the flat string does). E3's canonical PAYLOAD keeps the list
    (``commit_schema`` pins ``list[str]``); the GRAPH value is derived here.

    #8006: this lives in the PROJECTION, not on the SDK write doors, because
    the projection's Point writer is the SHARED door every write reaches —
    ``TortoiseSDK.create_point``/``update_point``/``update_entity`` flatten on
    their way in, and ``EventAPI.add_point`` (the E3 producer seam, whose
    payload the SDK never touches) reaches the node only through
    ``_upsert_point_props``. Keeping the rule on the doors alone left that
    third door storing NOTHING: the list policy in ``_persist_extra_props``
    drops undeclared lists/arrays BEFORE the declared-key check, and
    ``_POINT_LIST_PROPS`` is deliberately EMPTY — an ARRAY is never the right
    answer here, it is the #5482/#5444 shape (unfindable by FTS).
    ``_upsert_point_props`` therefore applies this rule on the payload, so the
    live write and the rebuild replay agree by construction (#330 parity).

    No-op for str values, None, and absent keys (absent search_keys → no prop
    written, the pre-E3 shape). An empty/blank list POPS the key (E3's
    ``or None`` convention), so a blank list never clears an existing value.
    """
    for key in _POINT_FLATTEN_LIST_PROPS:
        value = props.get(key)
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            flat = " ".join(str(s).strip() for s in value if str(s).strip())
            if flat:
                props[key] = flat
            else:
                props.pop(key, None)


class _EntityHandlers:
    """Mixin: entity upsert/delete methods for FalkorProjection."""

    # #2958/#5004: the per-rebuild-pass warning de-dup sets. Declared HERE so
    # mypy does not have to infer them from the runtime
    # `getattr(...)`/chained-assignment sites below: leaving them undeclared
    # built a PARTIAL type that never resolved and surfaced as a spurious
    # `Cannot determine type of "_deny_drop_warned" [has-type]` at its OTHER
    # assignment (`rebuild_all` in `projection/__init__.py`) — verified by
    # bisecting the mypy failure to this file.
    _deny_drop_warned: set
    _embed_identity_warned: set

    # Event dict keys that are never stored as node properties (#228).
    _META_KEYS: frozenset = frozenset({
        "type",              # event type
        "projection_version",# internal version tracking
        "version",           # event format version
        "about_entities",    # handled as graph edges
        "authoredBy",        # handled as authoredBy edge
        "ownedBy",           # handled as ownedBy edge
        "managedBy",         # handled as managedBy edge
        "aboutSubject",      # handled as aboutSubject edge
        "aboutObject",       # handled as aboutObject edge
        "aboutEvent",        # handled as aboutEvent edge
        "aboutPoint",        # handled as aboutPoint edge
        "aboutDocument",     # handled as aboutDocument edge
        # #3947 review (security): the capture turn loop's session-container
        # link. A structural EDGE carrier exactly like the `about*` keys above
        # — never a node property. The replay reads it from the RAW envelope
        # before `_norm` (`{**ev, **ev["point"]}`) can splice the payload over
        # it, so a point key of the same name cannot shadow (or forge) it; this
        # entry makes `_persist_extra_props` drop an IN-PAYLOAD forgery (its
        # `skip` set is `_META_KEYS | handled_keys`), which is the only way the
        # key can reach a node at all — the envelope half never enters the
        # payload dict the extra-props walk reads. It sits here rather than in
        # `_POINT_DENY` because that list is for payload keys the replay
        # DELIBERATELY drops, and a forgery is not a policy-drop; the boundary
        # rejects in `sdk._sanitize_props` / mcp `_SERVER_MANAGED_PROPS` are
        # where a tenant is told no.
        "contains_session",
        # Lane L5 (#5097): the `SourceCreated` record's OWN discriminator, set by
        # the one producer whose record COMPLETES an existing stub instead of
        # creating a Source (`sdk._materialize_session_source`'s trailing
        # record). `_upsert_source` reads it as a PARAMETER, never as a node
        # property: it selects the ON CREATE arm's non-creator form and admits
        # the capture writer's replay-only props. It sits here so
        # `_persist_extra_props` can never put it on a `:Source` — the same
        # reason `_stub` (the Event stub discriminator) is in `_SUBJECT_HANDLED`
        # / `_OBJECT_HANDLED`, and it is not a declared node prop.
        "_stub_completion",
        # journal meta keys (epic #900 T3, §4.2 cycle-16/17): the SDK's
        # _emit_event style-3 lines carry event_id/ts/initiated_by (+agent_id
        # on api._emit) + corrects — structural, never node properties. One
        # global skip-set keeps live/replay consistent across entity types.
        # #2600: actor_user_id rides every SDK journal envelope (_emit_event
        # stamps it inline) — rebuild replay must NOT leak it as a node
        # property (the live apply-dict is built from sanitized props that
        # exclude it).
        "event_id",
        "ts",
        "initiated_by",
        "agent_id",
        "actor_user_id",
        "corrects",
    })

    # Keys explicitly handled by each _upsert_* method.
    _SUBJECT_HANDLED: frozenset = frozenset({
        "id", "name", "subject_kind", "subjectKind", "createdAt", "embedding",
        # #7936: the event-stub discriminator is projection-owned — an
        # EventRecorded replay must NOT persist a caller-forged `_stub` via
        # _persist_extra_props (it would then be read back by the
        # first-registration probe). Mirrors the Object set below.
        "_stub",
        "status",  # #1423: status is projection-owned — a SubjectAdded replay
        # must NOT rewrite it via _persist_extra_props (the clobber guard ported
        # from #1350's Object clause: a re-mention of a superseded team/role/
        # person cannot reset superseded→live). Mirrors _OBJECT_HANDLED below.
    })
    _OBJECT_HANDLED: frozenset = frozenset({
        "id", "name", "object_kind", "objectKind", "createdAt", "title",
        # #7936: the event-stub discriminator (see _SUBJECT_HANDLED): kept out
        # of the extra-props persistence lane so a caller prop can never seed
        # the first-registration probe's exclusion.
        "_stub",
        "embedding", "status",  # #1350: status is projection-owned — an
        # ObjectRegistered replay must NOT rewrite it via _persist_extra_props
        # (the clobber guard: superseded stays superseded on re-mention).
    })
    _DOCUMENT_HANDLED: frozenset = frozenset({
        "id", "title", "document_kind", "documentKind", "content",
        "topics", "summary", "session_id", "event_id", "doc_status",
        "source_path", "format", "embedding", "updatedAt",
        "about_entities", "objectKind", "status",
        # epic #900 T3 (§4.1 route pin): the indexer's source_url override
        # (→ the #205 auto-wire target) and the embedding-suppression flag
        # ride the journaled DocumentCreated event but must NEVER persist as
        # node props (_persist_extra_props skip-set membership).
        "source_url",
        "suppress_embedding",
    })
    _EVENT_HANDLED: frozenset = frozenset({
        "id", "eventId", "eventKind", "event",
        "subject", "object", "startedAt", "endedAt",
        "parentEvent", "participants", "classificationLevel",
        "format", "embedding",
        "aboutSubject", "aboutObject", "objectType", "uses",
        "childEvents", "scopedFacts",
        # #8019: the connector content carrier. `itemState` is the canonical
        # projection the version digest reads — it is a dict, so
        # `_is_persistable_prop_value` would drop it anyway; naming it here
        # declares that it is an envelope field, not Event content. `labels`
        # and `assignees` are declared for the same reason (a connector event
        # that ever emits them top-level must not grow the Event surface).
        # `title` is deliberately NOT added: meeting Events persist their
        # title through this same skip set (`sdk._meeting_event_write` →
        # `_event_guarded_merge`), and `backfill_sources` reads it back — the
        # connector content fields ride `itemState` instead of the envelope.
        "itemState", "labels", "assignees",
        # #8398 (review P2-2): `aboutLinks` is the ``EventRecorded`` record's
        # edge DATA, never Event content — the links are wired as graph edges
        # and must not become node properties. It was dropped only
        # incidentally, by ``_is_persistable_prop_value`` filtering a list of
        # dicts; a FLAT list is a persistable value, and measured, replaying
        # such a record yielded ``node aboutLinks = [['aboutObject:obj-1']]``
        # — a property live never had. DECLARING it here makes the drop a
        # property of the skip set rather than of a value's shape.
        "aboutLinks",
        # #8010: the Slack/Linear connectors' full-text hash. It is an EDIT
        # SIGNAL, not node content — the raw text it hashes must not reach the
        # graph, and an undeclared digest string would be a persistable prop
        # (the open passthrough the #3998 note below closes for :Source).
        # Declared here so it rides the version digest without growing the
        # Event surface.
        "textDigest",
        # #2788: `name` / `eventStatus` / `createdAt` are deliberately NOT in
        # this skip set — the omission is what lets `_persist_extra_props`
        # carry them onto the node (its skip is `_META_KEYS |
        # _EVENT_HANDLED`). This is a PROP lane, never a projection-owned STATE
        # lane: an Event is a dated fact (§4.5), so no fold reads any of the
        # three back into Event state. `status` is not in this skip set either
        # (so a writer-supplied `status` also lands as a prop), and the
        # consistency gate compares `status` only for the Object/Subject
        # families — but a `status` prop on an Event is NOT inert:
        # `_promote_incident_operators` (sdk.py, #7853) reads an Event
        # endpoint's `status` as a live/draft gate, so it suppresses operator
        # promotion exactly as a draft Point does. Of the three omitted keys,
        # only `name` has a reader (`sdk.backfill_about_entities` matches an
        # Event against a Point's content); `eventStatus` is an INGEST marker
        # (`scheduled` → `completed`, written by the session/meeting indexers),
        # not a state read. The typed commit path cannot populate the lane at
        # all — `CommitEvent` is `extra="forbid"` (`commit_schema.py`) — so
        # only in-tree SDK/indexer writers reach it. The pack-declared fold
        # generalization is Object-scoped and does NOT extend to Events
        # (#2729); #2725 answered the Event-carries-state question "no".
    })
    # #3998 (D30): the DECLARED extra-property surface of a :Source. The graph
    # is the INDEX of the raw, not its store — so this is a CLOSED set, not an
    # open passthrough (mirroring `_POINT_LIST_PROPS`' undeclared-list denial).
    #
    # ⛔ A blocklist of payload KEY NAMES is not a fix and cannot be one:
    # measured on the open passthrough, `text`, `snippet`, `excerpt`,
    # `transcript`, `bodyText`, `rawText`, `raw_text`, `data`, `blob`, `bytes`,
    # `payloadText`, `contentText`, `content_text`, `fullText`, `full_text` AND
    # a list-valued `chunks` ALL persisted a 2 KB raw body verbatim on the node,
    # so "0 raw payload bytes" was false for every name nobody had thought of
    # yet. Declaring the surface is what makes the criterion a property of the
    # WRITE PATH rather than of a list someone must keep extending.
    #
    # ⛔ Enforced HERE, not only in the SDK: `create_source` is not the only
    # producer — `rebuild_all` replays `SourceCreated` through this same method,
    # so a journal written while the passthrough was open would otherwise
    # re-materialise its payload on every rebuild, the documented recovery path
    # restoring the exact bytes the contract forbids.
    # #3998 (D30): the full DECLARED node-property surface of a :Source — the
    # single source of truth for "what may be on this node". Three things read
    # it: the closed passthrough below (:data:`_SOURCE_EXTRA_PROPS` is its
    # extra-props subset), the generic entity write in `sdk._update_entity`
    # (which reaches `:Source` too and would otherwise bypass the declaration),
    # and the provenance read, which filters the returned bag to it rather than
    # handing back whatever a pre-#3998 writer left behind.
    #
    # ⚠️ WHAT THIS DOES AND DOES NOT ESTABLISH. It establishes that the node's
    # property surface is DECLARED: a caller-supplied property map cannot carry
    # an undeclared key onto a `:Source` — and so no un-named-in-advance spelling
    # of a raw payload can reach the node — on the caller-prop write paths
    # (`create_source`/`_upsert_source`, `_update_entity`) and on journal replay
    # (the `EntityMutated` fold arm below). It does NOT bound the VALUES of the
    # declared metadata fields: a caller who deliberately writes a 2 KB body
    # into `title` still stores 2 KB under `title`. Bounding those is a value
    # policy for short metadata (what IS the maximum title?), which is a product
    # decision this change does not take — stated here rather than implied away.
    #
    # This is the set of properties that may APPEAR on the node, which is why it
    # is what the READ filter and the fold consult. It is deliberately BROADER
    # than `_SOURCE_EXTRA_PROPS` (what a CALLER may set through the passthrough):
    # `reliability*` is minted by an in-tree producer, not by a caller, so it
    # must survive the read filter and must still be refused on the write side.
    # `test_the_declaration_covers_every_in_tree_source_writer` enforces that
    # this stays a superset of what the in-tree writers actually produce — the
    # check whose absence let `reliability*` be dropped from the read bag.
    _SOURCE_NODE_PROPS: frozenset = frozenset({
        # identity
        "id", "url", "canonicalUrl", "urlAliases", "sourceKind", "externalId",
        # version
        "contentHash", "version", "ingestedAt", "updatedAt", "sourceDate",
        # #3644 (§4.6 window half): the CURRENT version's valid-time start.
        # Declared because it IS written to the node now, and the declaration is
        # the read filter's contract (an undeclared property is dropped from
        # every read bag — the #3998 'declared vs dropped' class). `validTo` /
        # `expiredAt` are deliberately NOT declared yet: no writer writes them
        # on a `:Source` (the removal field is #3590 S2's decision), so
        # declaring them would open a read/write surface with nothing behind
        # it. The PRIOR version's close is a journal fact on the
        # `SourceVersioned` record (`previousValidTo`), not a node property.
        "validFrom",
        # availability (#3998 — the third value on the source record)
        "rawState", "rawStateAt",
        # transient merge token (`_index_source_merge`'s `s.__runId = $rid`).
        # Declared because the declaration is "the single source of truth for
        # what may be on this node" and this IS written to the node: measured,
        # `create_source(url, k, _merge_run_id="RID")` leaves `__runId` on the
        # node. It is read back only by raw Cypher and removed best-effort, so
        # declaring it is what keeps the coverage invariant true (#5196
        # review round 3, P2).
        "__runId",
        # declared metadata
        "title", "format", "name", "team", "credibilityTier", "is_episodic",
        "sourcePath", "_searchText", "provenance_spans",
        # the session-capture writer (`sdk._materialize_session_source`)
        "sessionId", "capturedAt", "summary", "topics", "eventId",
        # the reliability cache (`sdk.get_source_reliability`) — an in-tree
        # producer, so these must survive the read filter. Omitting them made
        # `get_provenance_chain` silently drop the cache it had just returned.
        "reliability", "reliabilityComponents", "reliability_derived_at",
        # D10 B6 non-over-reach (#228, #5026): a document IS a :Source, and
        # these ride either the document fixed clause or a deliberate
        # main-side allowance — `domain` is written through `update_entity` and
        # read back by the B6 non-over-reach pin, and the rest are exactly the
        # keys main's deny-set was pinned NOT to retire. They MUST be declared
        # here: the read filter drops anything undeclared, so omitting one
        # silently loses a property that is on the node.
        "domain", "status", "embedding", "about_entities",
        "documentKind", "document_kind",
        # #3998: the DOCUMENT writer's own payload (`api.DocumentCreated` →
        # `_upsert_document`, whose passthrough passes no `allow_keys`, so every
        # one of these lands on the document `:Source`). They are the same
        # undeclared-drop class as the line above and were found by reading what
        # that writer actually emits rather than what this declaration assumed:
        # `needs_extraction` is read back by `ingest` (`coalesce(s.needs_extraction,
        # false)`) as the extraction signal, and `authored_by`/`owned_by`/
        # `managed_by`/`governing_agreement`/`document_knowledge_domain`/`createdAt`
        # are the document's own provenance metadata. Both spellings are listed
        # for the session/source pair because the document writer emits the
        # snake_case forms while the session-capture writer mints the camelCase.
        "needs_extraction", "story_arc", "document_knowledge_domain",
        "authored_by", "owned_by", "managed_by", "governing_agreement",
        "createdAt", "session_id", "event_id", "source_path",
    })
    _SOURCE_EXTRA_PROPS: frozenset = frozenset({
        "credibilityTier",   # create_source(tier=)
        "sourceDate",        # create_source(sourceDate=) — the evidence-age clock
        "is_episodic",       # create_source(is_episodic=) — the quota discriminator
        "name", "team", "format", "provenance_spans",  # in-tree callers
        # D10 B6 non-over-reach (#228): a document IS a :Source, so a Source
        # may legitimately carry keys the document fixed clause owns but does
        # NOT retire. They MUST stay declared here — the closed surface denies
        # anything undeclared, so omitting one silently drops it —
        # `test_projection.py` pins `domain`/`summary`/`topics`/`status`.
        # `source_url` and `suppress_embedding` are deliberately ABSENT: they
        # ride the DocumentCreated event and must never persist as node props.
        "domain", "summary", "topics", "status", "embedding", "about_entities",
        "document_kind", "documentKind", "session_id", "event_id",
        "source_path",
    })
    # Lane L5 (#5097): the CAPTURE COMPLETION record's own props — admitted to
    # the `:Source` passthrough ONLY for a record that declares itself one
    # (`_stub_completion`, set by `sdk._materialize_session_source`).
    #
    # They are minted by an IN-TREE PRODUCER (the session-capture writer), not by
    # a caller, which is exactly the shape the comment above `_SOURCE_NODE_PROPS`
    # says belongs on the READ surface and must still be REFUSED on the write
    # side (`reliability*` is the sibling case). Declared on the read surface
    # since #1352, so they survive the read filter either way.
    #
    # ⛔ NOT `_SOURCE_EXTRA_PROPS` (the #3998 D30 caller allowlist). That set is
    # also the CALLER-writable one — `create_source(**props)` rides the same
    # replay passthrough — so putting them there would make
    # `create_source(url, k, sessionId=…)` persist provenance a caller could
    # forge (it was DENIED with a warning before #5097 and is again), and would
    # let ONE call carry both spellings (`session_id` IS caller-settable, a
    # pre-existing asymmetry) instead of one.
    #
    # ⚠️ The journal is trusted: a hand-written record carrying
    # `_stub_completion` can still set them. That is the same boundary every
    # record-carried fact has (a forged `contentHash` replays too); the door this
    # closes is the PUBLIC caller surface, not the journal. The DISCRIMINATOR is
    # itself refused on every caller surface — `sdk.create_source`'s `**props`
    # guard, `sdk._sanitize_props` (the direct-SDK backstop) and
    # `mcp_server._SERVER_MANAGED_PROPS` (#5097 review P1: it rode in through
    # `create_source(**props)` and made this whole set caller-writable again,
    # while also suppressing the ON CREATE identity props) — so the capture
    # writer is its only legitimate producer.
    _SOURCE_CAPTURE_REPLAY_PROPS: frozenset = frozenset({
        "sessionId", "capturedAt", "eventId",
    })
    _SOURCE_HANDLED: frozenset = frozenset({
        "id", "url", "sourceKind", "contentHash",
        "title", "ingestedAt", "version", "externalId", "updatedAt",
        # #5024: the version-transition record's own key. It describes the
        # TRANSITION (which version this one superseded), never a node property
        # — it is the fact that keeps the prior version addressable from the
        # record alone, and `_persist_extra_props` must not also write it (the
        # #330/#3312 one-sided-property class).
        "previousContentHash",
        # #3644 (§4.6 window half): the `SourceVersioned` record's PRIOR-window
        # close. Like `previousContentHash`, it describes the TRANSITION, not
        # the node — the node holds the CURRENT version's window (open end), so
        # `_persist_extra_props` must not also write this one. Same #330/#3312
        # one-sided-property class.
        "previousValidTo",
        # #3644: the window start is owned by the fixed SET clauses above (ON
        # CREATE + the hash-diff ON MATCH gate), never by the open-set
        # passthrough — a payload must not be able to move a version's window
        # without moving the version (see `_SOURCE_SERVER_MANAGED_PROPS`). The
        # `_upsert_document` lane mints no window of its own (a document IS a
        # `:Source`, but its window belongs to the versioned `create_source`
        # lane), so a `validFrom` in a `DocumentCreated` payload is skipped
        # there rather than persisted: before this key was handled it landed as
        # an UNDECLARED node property, which `filter_source_props` then dropped
        # from every read bag — i.e. it was never served on any surface, and
        # adding it to the declaration is what makes that explicit.
        "validFrom",
        # S0a/S0b (#5012): server-managed source-identity props.  They are
        # written by `_upsert_source`'s fixed SET clauses, never by the
        # open-set passthrough (which would let a payload clobber the
        # canonical identity).
        "canonicalUrl", "urlAliases",
        # D10 (ONTOLOGY v3.15 §4.4/§9.5 Q3): a document is a :Source, and
        # `format` moves onto :Source. It belongs to the fixed clause so a
        # caller-supplied `format` lands as a node property here instead of
        # riding the open passthrough.
        "format",
        # #3998 (D30): the ABSENT-RAW state — the THIRD value on the source
        # record (identity = url/canonicalUrl, version = contentHash,
        # availability = rawState; STORAGE §9.4 ③ "a third value on that
        # record, not a fourth kind of source").  Like the identity props
        # above, these are owned by the fixed SET clauses and never by the
        # open-set passthrough, and for the same reason plus one more: a
        # payload must not be able to CLEAR a recorded absence, and an upsert
        # that carries no state must PRESERVE the stored one (the contentHash
        # anchor rule, applied to availability).
        "rawState", "rawStateAt",
        # `raw_state` (snake_case) is a DEFENSIVE ALIAS: `create_source` maps the
        # sanctioned `raw_state=` keyword to the camelCase ev key, so no writer
        # emits it today — but a future or out-of-tree producer building a
        # SourceCreated payload by hand must not be able to persist the
        # snake_case spelling verbatim, which would put the state in TWO places
        # and make the read-side default ambiguous.
        "raw_state",
        # epic #900 T3 (§4.1): the ev keys `source_path` (→ s.sourcePath via
        # the MERGE clause, never persisted verbatim snake_case) and
        # `_searchText` (set by the write path, coalesce-on-create /
        # overwrite-on-hash-diff — §4.1 cycle-4 merge semantics).
        "source_path",
        "_searchText",
    })
    # D10 B6 (#5135): the RETIRED document fields — THE single declaration of
    # the retirement for the write/replay doors THAT CONSULT IT. Those are:
    #   * `sdk._update_entity`'s target-aware guard, which reads it directly;
    #   * `_upsert_document`'s open passthrough and its promotion scrub, which
    #     both compose with it at CALL time (see below) — so a member added
    #     here is denied and scrubbed in this class without a second edit.
    # (The historical `_DOCUMENT_HANDLED` overlap on three of these keys is
    # incidental: `object_kind`/`docStatus` reach the document passthrough ONLY
    # through the registry union at the call site, not through that set.)
    # ⛔ Two further graph-side doors deny the retirement by a DIFFERENT route
    # and so do NOT consult this set. They are named here so the next member is
    # not assumed covered:
    #   * `_upsert_source`'s open passthrough denies by its CLOSED ALLOWLIST
    #     (`_SOURCE_HANDLED` ∪ `_SOURCE_EXTRA_PROPS`). Every retired key is
    #     outside both today, so the denial holds — but a key added here that is
    #     ALSO declared in `_SOURCE_EXTRA_PROPS` would pass that door. Widen
    #     that allowlist check; do not assume this set covers it.
    #   * the `EntityMutated` fold drops by the DECLARED SURFACE: the keys are
    #     absent from `_SOURCE_NODE_PROPS`, the surface every replay filters to
    #     (`filter_source_props`). That is a different question from the
    #     retirement, answered by a different set.
    # NOT in this module's reach, and therefore NOT consolidated by this change:
    # the record mirror in `record_projection.py` spells the same five keys as a
    # literal `drop` on its `DocumentCreated` mutation (that layer imports no
    # graph projection, by design), and `log.py`'s tear-immunity note lists them
    # in prose. A member added HERE must be added THERE too.
    # ⛔ ONE list — for this class. The divergence between this set and the
    # per-path copies it used to have is what let the #5135 third door
    # (`update_entity`) and the promotion fourth door exist at all: a writer
    # that consults a stale copy silently misses the newest member.
    # BOTH spellings are denied: the projection normalizes to camelCase for
    # the FIXED clauses, but a raw journal payload (a hand-written JSONL line,
    # `EventAPI.add_document`, or a producer's `create_source(**props)`) can
    # carry the snake_case spelling, which would otherwise persist verbatim as
    # a node property no reader owns — a B6 re-entry through the snake door.
    # ``object_kind`` (synonym of ``objectKind``) and ``docStatus`` (synonym of
    # ``doc_status``) are the two the write path can actually produce;
    # ``content`` has no second spelling.
    _DOC_RETIRED_KEYS: frozenset = frozenset({
        "content", "doc_status", "docStatus", "objectKind", "object_kind",
    })
    # D10 (ONTOLOGY v3.15 §4.4): the document write path now targets a :Source,
    # so its passthrough skip-set is
    # ``_SOURCE_HANDLED | _DOC_PASSTHROUGH_HANDLED | _DOC_RETIRED_KEYS``. This
    # keeps every fixed-clause key off the passthrough. It carries no
    # INDEPENDENT copy of the retirement: the three registry keys it holds do
    # so only as historical `_DOCUMENT_HANDLED` members, while the registry is
    # unioned at the call site — which is the only thing that can cover a key
    # added to it (e.g. `object_kind`/`docStatus` are not in
    # `_DOCUMENT_HANDLED`), so the two cannot drift (#5135).
    # The historical `_DOCUMENT_HANDLED` set is retained as the base so no
    # previously-handled key becomes an accidental passthrough key.
    # ⛔ Distinct from the registry: a SUPERSET of the historical
    # `_DOCUMENT_HANDLED` is only safe on the document path (there,
    # `_upsert_document`'s fixed clause owns every other key). Applying the
    # full `_DOCUMENT_HANDLED` union to a Source write would ALSO deny
    # `summary` / `topics` / `embedding` / `status` / `about_entities`, which
    # `_upsert_source`'s fixed clause does NOT write and which a Source
    # legitimately carries (a `SourceCreated` passthrough write of `summary` or
    # an `embedding` — read by the vector retrieval leg — would be silently
    # dropped). Deny the retirement, not the history.
    _DOC_PASSTHROUGH_HANDLED: frozenset = _DOCUMENT_HANDLED | frozenset({
        "needs_extraction",
        # #5422: the snake_case spelling of the version anchor. The fixed
        # clause above writes `contentHash` from `ev["content_hash"]`; the
        # raw journal spelling must therefore ALSO be denied by the
        # passthrough, or it would persist verbatim as a second, unread
        # `s.content_hash` property beside the camelCase the readers use
        # (the same snake-door class as `needs_extraction` below).
        "content_hash",
    })
    # #2795 (D2): every key owned by the fixed SET clauses of
    # `_upsert_point_props` (plus the MERGE key and the structural/edge-carried
    # keys). The open-set passthrough skips these so the declared writers keep
    # precedence — `updatedAt`/`embedding` must never be reloaded from a
    # payload. `content_hash` is NOT here: it is recomputed (see _POINT_DENY).
    _POINT_HANDLED: frozenset = frozenset({
        "id",  # MERGE key (n:Point {id:$id}) — not a SET clause
        "content", "is_operator", "op_type", "pointKind", "status",
        "authoredBy", "confidence", "createdAt", "created_at",
        "validFrom", "validTo", "updatedAt", "embedding",
        # #5004: the embedding's IDENTITY travels with the vector in the journal
        # payload (R1: STORE, do not regenerate). `_upsert_point_props` reads it
        # to tell a faithful verbatim restore from a recorded model change, and
        # it must NEVER become a node property: the design puts the identity in
        # the PAYLOAD, and the live writer sets it only on the journal copy, so
        # persisting it here would make replay diverge from live by three
        # properties (caught by the #3312 round-trip guard before this entry).
        "embedding_model", "embedding_revision", "embedding_text_hash",
        # #5004 round-3: a DECLARED node property (own SET clause below), not
        # journal-payload metadata. It marks a CALLER-supplied vector stored
        # verbatim (`$embedding`, not `vecf32` — `create_point`'s recorded
        # PR #3018 decision), so it must live on the NODE for a later re-emit
        # (`promote_point` &c., which read the point back through `get_point`)
        # to carry it; a payload-only flag was lost there and the replay then
        # narrowed the vector to float32. In `_POINT_HANDLED` because its own
        # clause owns it — the open-set passthrough must not also write it.
        "embedding_verbatim",
        # #5004 round-3: journal-payload metadata (
        # `_write_capture_turns`): this capture did NOT encode a vector, it
        # PRESERVED the node's existing one, so the record must not attest the
        # ACTIVE model as its origin (`stamp_journal_embedding`). Never a node
        # property — the node's vector identity is not a fact about the node.
        "embedding_preserved",
        # A10 operator-scoped replay extension
        "direction", "label",
        # #7852 W1: the basis the direction chain used. Written by
        # `create_operator` beside `direction`; handled here (its own clause
        # below) so replay restores it instead of the open passthrough, exactly
        # as `direction` is.
        "direction_basis",
        # structural / edge-carried — never node props via passthrough
        "operator", "provenance", "about_entities", "aboutEntities",
        "extractedFrom", "is_episodic", "_nid", "_graph_id",
        # #2897 (OD-4 disposition #3856: ADOPT — a Tag is first-class data by
        # construction: a SHARED, MERGEd node is data. The ontology specifies
        # an `:Object` subclass while the code creates a bare `:Tag`; that
        # doc-vs-code gap is filed as #8325, and the durability argument rests
        # on "shared and MERGEd", not on the label). Its own clause
        # (`_upsert_point_props`) owns the raw-list node property; pass 2
        # (`_upsert_point_edges` → `_sync_tags`) reconciles the `:Tag`/`TAGGED`
        # half from the NODE'S FINAL value — never from this payload, which
        # holds only the creation list and would resurrect a removed tag (the
        # `#3299`/`#2488` class) — the `extractedFrom` split (#2489). Handled
        # also keeps the undeclared-list drop report from firing for a prop the
        # fold restores.
        "tags",
        # #5256: a DECLARED node property (own conditional clause below), the
        # extractedFrom READ-VERSION transit. It rides on the NODE because
        # `extractedFrom` is a replay-derived projection (the `#4042` recreate
        # wipe clears only node props, and pass 2 boots edges from scratch) —
        # the edge is authoritative, but the value must survive in the Point's
        # own journaled snapshot for pass 2 to re-stamp it without reading the
        # Source (whose in-place contentHash bump is unjournalled, #5024).
        # Handled because its own clause owns it — the open-set passthrough
        # must not also write it.
        # DELIBERATELY NAMED `sourceVersionTransit`, not `sourceVersion`: the
        # latter is the EDGE scalar (ONTOLOGY §4.6). A Point read returns this
        # list-of-pairs carrier, so reusing the canonical name would hand a
        # consumer a value of a different TYPE than §4.6 documents. The name
        # states the role: the edge is the model, this is the replay carrier.
        "sourceVersionTransit",
        # #2958 review: written by its own explicit clause in
        # `_upsert_point_props` (`SET n.provenanceSource=$sid`), gated on
        # provenance.source_id — the open passthrough must not supply it when
        # that gate is closed.
        "provenanceSource",
        # Phase 2 #49: context was removed and is never written as a node
        # prop — the old closed writer enforced this by omission; the open
        # passthrough must keep it dropped.
        "context", "new_context",
    })
    # #2795 (D2): recompute / non-persistable deny-list — the passthrough must
    # never copy these from a payload. `embedding`/`content_hash`/`updatedAt`
    # are recomputed by `_upsert_point_props`; `_nid`/`_graph_id` are replay
    # bookkeeping. EP-owned props are denied because they are written only by
    # ep.py/dream.py — an open-set passthrough would let a caller-supplied
    # `posterior_alpha` overwrite EP state (D1 `payload_writable=False`).
    # `reason` is deny-listed per D4. All of these were dropped by the old
    # closed writer, so denying them preserves existing behaviour.
    _POINT_DENY: frozenset = frozenset({
        "embedding", "content_hash", "updatedAt", "_nid", "_graph_id",
        "reason",
        "c_cal", "posterior_alpha", "posterior_beta",
        "ep_alpha", "ep_beta", "baseline_set", "baseline_source",
        "inherited_at", "lastDreamedAt", "expiredAt", "outdated",
    })
    # #2795 (D2): D1's declared payload/capture props (contract.py has not
    # landed yet) — known passthrough keys that must NOT trip the drift
    # warning. NOTE (#2958 review): a LIST-valued entry here is INERT for that
    # warning — the list policy in `_persist_extra_props` filters lists out
    # BEFORE they can reach the extras dict the drift warning inspects.
    # `search_keys` is the case that does NOT reach that policy: its list form
    # is flattened to the canonical graph STRING by `_upsert_point_props`
    # before the list rule can be consulted (#8006). (`tags` was the other
    # case this NOTE described until #2897 landed: it is replayed now, so it
    # is in `_POINT_HANDLED` and needs no entry here.)
    _POINT_DECLARED_PROPS: frozenset = frozenset({
        "quote", "when", "search_keys", "speaker", "source_turn_id",
        # E4 (#5007): the verbatim span POINTER — offsets into the Source's raw
        # text, declared so the replay open-set passthrough does not log a
        # FALSE undeclared-prop drift warning on every rebuild.
        "span_start", "span_end",
        # #1724: the STABLE external anchor carried by extractor-minted
        # statement Points (``github:issue:*`` from the GitHub indexer's
        # dormant statement path, ``docs:<doc_id>#<claim key>`` from the docs
        # miner). Both are IN-TREE producers, so the reason the two entries
        # above carry applies verbatim: without this, the live write and every
        # rebuild log a false "not declared" drift warning for a prop that is
        # part of the documented statement contract.
        "externalId",
        # #3689 review P2 (A): the four canonical annotator dims are legitimately
        # carried on a PointAdded snapshot by `create_point(annotator_*=…)` /
        # `_update_entity` — declaring them keeps the replay open-set
        # passthrough from logging a FALSE `"Point prop %r is not declared"`
        # drift warning on every rebuild.
        "annotator_bias", "annotator_precision",
        "annotator_consistency", "annotator_directness",
        # #7856: the goal ACHIEVEMENT state (a validated closed vocabulary,
        # `sdk.GOAL_STATE_VALUES` — separate from the `status` lifecycle). A
        # legacy goal POINT carries it via the Point write path; declaring it
        # keeps the replay open-set passthrough from logging a FALSE
        # `"Point prop %r is not declared"` drift warning on every rebuild.
        "goalState",
        # #6110/#8400: the per-kind dedup IDENTITY supplied to
        # `create_point(key=…)`. It rides the `PointAdded` snapshot and is
        # restored through this same open-set passthrough — declaring it keeps
        # the shared live+replay writer from logging a FALSE `"Point prop %r
        # is not declared"` drift warning for a documented identity prop (and
        # removes the one-warning-per-keyed-point rebuild noise the #6110
        # review measured). Declaration is warning-suppression only: the
        # passthrough persists `key` either way.
        "key",
        # #398 (owner ruling 2026-10-12): the author's declared credibility
        # WORD, persisted by `create_point(credibility=…)` as a validated
        # LABEL (it no longer sets the claim's strength). `_apply_source_
        # inheritance` reads it to treat the authored assertion as ONE weak
        # evidence body and to keep recomputing an authored baseline as real
        # sources arrive. Declared so the replay open-set passthrough does not
        # log a FALSE `"Point prop %r is not declared"` drift warning on every
        # rebuild.
        "credibility",
        # #2350 (E-prec): the per-end validity precision declaration. It rides
        # the PointAdded snapshot as a plain string prop and the open-set
        # passthrough restores it, so declaring it is warning-suppression only
        # — but without it a rebuild logs a FALSE `"Point prop %r is not
        # declared"` drift warning for a documented part of the window
        # contract. ``validToPrecision='unknown'`` is the 3-state end's
        # closed-unknown marker and must survive rebuild verbatim.
        "validFromPrecision", "validToPrecision",
        # #6151 defect 2: the #2199 system-default provision, recorded on a
        # decide part BORN live (no explicit status). `_apply_source_
        # inheritance` re-injects the default ONLY where this marker says one
        # was granted, so a decide part created with an EXPLICIT status never
        # gets an unearned default. Declared so the replay open-set passthrough
        # does not log a FALSE `"Point prop %r is not declared"` drift warning
        # on every rebuild.
        "baseline_system_default",
    })
    # #2795 (D2 mechanic 1): list-valued props are persisted ONLY when their
    # key is declared here. Arrays are not fulltext-indexed and the canonical
    # form is the declared `flatten=list` STRING (`search_keys` -> space-joined
    # by `flatten_search_keys_prop`, applied on the SHARED Point writer so all
    # three doors agree — #8006); `tags` (the one such prop in practice) is
    # reconciled by its own clause plus the pass-2 edge sync, which reads the
    # NODE's final value rather than the payload (#2897), so it never reaches
    # the generic filter. Empty
    # pre-D1: no other list prop passes through the generic Point filter today.
    # Replaced by contract.py's declared set in D1.
    # ⛔ NOT the fix for `search_keys`: populating this set stores an ARRAY,
    # which the FTS index cannot match — it re-mints #5482. `search_keys` is
    # populated as a flattened STRING instead (module-level
    # `_POINT_FLATTEN_LIST_PROPS`, #8006).
    _POINT_LIST_PROPS: frozenset = frozenset()

    def _persist_extra_props(self, match_clause: str, match_params: dict,
                              ev: dict, handled_keys: frozenset,
                              list_props: frozenset | None = None,
                              allow_keys: frozenset | None = None) -> dict:
        """Persist arbitrary caller-supplied props not explicitly handled.

        Computes the set difference between event dict keys and the union of
        _META_KEYS + handled_keys, then applies SET n += $extra on the
        matched node.  Skips the query entirely when there are no extra props.

        None values are excluded — Cypher null semantics in SET maps are
        unreliable (coalesce-based updates use explicit per-field clauses).
        #2894: non-persistable values (maps/dicts at ANY depth, bytes, sets)
        are filtered by `_is_persistable_prop_value` — the engine rejects them
        on a SET, so dropping is the only non-crashing option. Nested scalar
        ARRAYS (lists/tuples) ARE accepted by the engine and pass the filter
        (#2958 review — the earlier flat-list-only rule silently dropped them).
        #2795 (D2 mechanic 1): when `list_props` is supplied, a flat LIST is
        # persisted only when its key is declared there; an undeclared list is
        # denied, never written raw. `None` keeps the pre-existing permissive
        # behaviour for the non-Point layers.
        #3998 (D30): when `allow_keys` is supplied the passthrough becomes a
        CLOSED set — a key not in it is denied, whatever its name. This is the
        only form that can carry a "no raw payload" guarantee, because the
        payload-bearing name space is unbounded. Used by the `:Source` layer.

        Returns the dict of props actually persisted (empty when none) so the
        caller can report unrecognised keys (#2795 drift warning).
        """
        skip = self._META_KEYS | handled_keys
        extra = {}
        for k, v in ev.items():
            if k in skip or v is None or not _is_persistable_prop_value(v):
                continue
            if allow_keys is not None and k not in allow_keys:
                continue
            # #2958 review: a TUPLE is persisted by the engine as an array
            # exactly like a list, so the list policy must cover both —
            # otherwise a tuple-valued key bypasses the undeclared-list denial.
            if isinstance(v, (list, tuple)) and list_props is not None \
                    and k not in list_props:
                continue
            extra[k] = v
        if extra:
            self.g.query(
                match_clause + " SET n += $extra",
                params={**match_params, "extra": extra},
            )
        return extra

    def _upsert_point_props(
            self, p: dict, *,
            from_prewipe_snapshot: bool = False,
    ) -> tuple[bool, bool, frozenset]:
        """Write all Point node properties (no edges).

        Single source of truth for Point property parity between apply() and
        rebuild_all() (#330): rebuild pass 1a calls this so a rebuilt graph can
        never drift from the incrementally-applied graph on node properties.

        Returns ``(embedding_written, content_hash_written,
        persisted_extra_keys)``. The first two are the CONDITIONAL derived
        writes. The fixed SET list writes them as
        ``n.embedding = CASE WHEN $embedding IS NOT NULL … ELSE n.embedding
        END`` and ``n.content_hash = coalesce($ch, …)``, and computes neither
        for an operator, falsy content, an unavailable embedder, or a raising
        ``_content_hash`` — so in those cases the existing value is PRESERVED.
        #4042's pass-1b content boundary needs that outcome exactly, never a
        ``bool(content)`` guess.

        ``from_prewipe_snapshot`` says WHERE this payload came from, because
        the deny-drop report below has to name the right cause: ``False`` (the
        default, and every journal arm — live ``apply``/``_upsert``, rebuild
        pass 1a's journal records, the promote folds) means the payload IS the
        journal record, so the key it carries was journaled and only the policy
        refuses it. ``True`` means the payload is a SYNTHETIC event built from
        the #548 pre-wipe GRAPH snapshot rather than from a journal record, so
        the snapshot — not a journal record — is what carried the key into this
        pass (#8018 review round 2: the flag proves the payload's source, NOT
        that the journal lacks the key for that id; an id whose only record is a
        ``PointPromoted`` snapshot or a ``ConfidenceChanged`` belief fold
        carries EP state in the journal too, so the report must not assert the
        stronger claim).

        ``persisted_extra_keys`` is the key set ``_persist_extra_props``
        actually handed to the engine (a ``None``, a non-persistable value, and
        an undeclared list/tuple — ``_POINT_LIST_PROPS`` is EMPTY — are all
        dropped). #5048's open-set boundary needs it for the same reason as the
        two flags above: whether a creation WROTE ``mitigation_strength`` is
        the writer's own outcome, and a second hand-spelled copy of the
        writer's condition drifts from it. Every other caller ignores the
        return.
        #8006: the declared flatten-list prop (``search_keys``) is normalized
        to its canonical GRAPH form at the top of this function, so the
        ``EventAPI``/extractor seam — which never passes through an SDK write
        door — reaches the node with the SAME value the SDK doors write, live
        and on replay alike. A blank list is popped there, so it is a
        non-persisted key, never a write.

        #4457: when a NEW embedding is written, the ``REMOVE n.embedding``
        clause rides in the SAME query ahead of the SET list. On the embedded
        engine (falkordblite/redislite) a ``vecf32`` overwrite of an existing
        vector property can be SILENTLY DISCARDED — measured there as landing
        only once some component moves by ~1.0, which real embedder output
        never does (the server lane lands the same write, which is why only
        the embedded lane reddened). So the node kept its OLD vector and a
        rebuilt Point's ``embedding`` no longer derived from its ``content``.
        ``REMOVE``-first is the workaround this repo's own test helper
        documents (``tests/test_precision_leak_4028.py``); the sibling entity
        seams in THIS file (``Subject``/``Object``/``Document``/plain
        ``Event``) carried the same overwrite shape and are cleared the same
        way by #4524, while the vector writers in OTHER files still carry it
        (tracked in #4520). On the server lane the final state is
        unchanged, and because the clause is emitted ONLY when a new vector is
        being written, the preserve-on-None semantics above are untouched.
        See the query below.

        The parity tests exercise this with REAL embedder output, whose
        per-component deltas are well below the threshold. A probe whose
        per-component delta reaches ~1.0 — a plain 0/1 one-hot swap, exactly
        1.0 — LANDS and therefore MISSES the discard; the engine defect is
        tracked separately (#4520).
        """
        # #8006: the declared flatten-list prop (``search_keys``) is normalized
        # to its canonical GRAPH form HERE — on the SHARED Point writer, which
        # is the door every write reaches — rather than on the SDK write doors
        # alone. ``EventAPI.add_point`` (the E3 producer seam, plus every
        # extractor/mining/CLI caller that mirrors its signature) never passes
        # through ``create_point``/``update_point``, so with the rule on the
        # doors only, its list-valued ``search_keys`` was dropped outright by
        # the ``_persist_extra_props`` list policy and the node held NOTHING
        # (``_POINT_LIST_PROPS`` stays EMPTY on purpose: an ARRAY is not
        # indexed by FTS — that is the #5482/#5444 shape, not a fix). Applying
        # it on the payload here also fixes the REPLAY "for free": rebuild
        # pass 1a calls this same function (#330 parity), so the live write and
        # the rebuilt node hold the same value.
        #
        # A conditional SHALLOW COPY, never an in-place mutation: on the live
        # path ``p`` IS the journal record, and the journal must keep E3's
        # list spelling — the graph value is DERIVED from it, not recorded, so
        # a rebuild re-derives it. The copy is taken only when a list/tuple is
        # actually present (the SDK doors already flattened theirs), and the
        # blank-list pop is visible to the drop-report loop below, which would
        # otherwise log a false "list prop dropped" for a key that was
        # deliberately normalized away.
        if any(isinstance(p.get(k), (list, tuple))
               for k in _POINT_FLATTEN_LIST_PROPS):
            p = dict(p)
            flatten_search_keys_prop(p)
        op = p.get("operator")
        if not isinstance(op, dict):
            # #331 (review r5): parity with _create_edges' r4 guard — a
            # truthy non-dict operator (e.g. a bare string) must degrade
            # to no-operator, not AttributeError in op.get("op_type").
            op = None
        prov = p.get("provenance")
        if not isinstance(prov, dict):
            # #331 (review r3): explicit null / string provenance must not
            # crash the Falkor path (parity with _apply_one's guard).
            prov = {}

        # Compute OR RESTORE the embedding for non-operator Points (#7778).
        # #5004: the journal is PRIMARY for this field. When the payload carries
        # a vector — the journal recorded it with its model identity — restore
        # it VERBATIM and do NOT re-encode. R1 (`docs/durability-posture.md`
        # → *Derived properties that are STORED*; design source
        # `docs/architecture/STORAGE-ARCHITECTURE.md` §3/§14.1
        # O1, landed via #5016): the embedding STORES, it is not regenerated, because a re-embed
        # is a RE-RUN, not a replay. Re-encoding here was the defect: a replay
        # under a changed embedder silently produced a different graph from the
        # same journal.
        embedding = None
        embedding_clear = False
        # PRESENCE IS OWNERSHIP (#5004 round-3). A producer that owns this field
        # ALWAYS writes the key — the vector, or an explicit None when it
        # genuinely has none (`stamp_journal_embedding` guarantees it). So the
        # key being present means "the journal has spoken about this field; do
        # NOT recompute". The absent case is a pre-#5004 strip-era record,
        # where recomputation is the only behaviour available.
        owns_embedding = "embedding" in p
        journalled = _writable_journalled_vector(p.get("embedding"))
        if not op and journalled is not None:
            # Guard the width exactly as `encode_for_store` does for the
            # recompute path: a vector of the wrong width is not a near-miss,
            # it is a broken leg the HNSW index cannot hold (#4194/#4280).
            dim = self.required_embedding_dim
            if dim is not None and len(journalled) != dim:
                # DELIBERATE refusal, not an oversight (#5004 review). Writing
                # the journalled vector is impossible at this width, and
                # RECOMPUTING it would store a vector whose model the journal
                # never recorded — which is exactly how `derived =
                # replay(journal)` goes false again. Refusing keeps the store a
                # pure function of (journal, config); the operator re-embeds
                # deliberately. Recorded once per (width, store-dim) pair.
                warned = _warned_set(self)
                key = ("width", len(journalled), dim)
                if key not in warned:
                    warned.add(key)
                    logger.warning(
                        "replay: a journaled embedding has width %d but this "
                        "store holds %d — leaving it unset rather than writing "
                        "a vector the index cannot hold, or recomputing one "
                        "the journal never recorded (#5004)",
                        len(journalled), dim)
            else:
                embedding = journalled
                _record_embedding_identity(p, _warned_set(self))
        elif not op and owns_embedding:
            # The journal owns the field and carries NO usable vector — either
            # an explicit None (the live write had no embedder) or a value the
            # guard refused. Never recompute: re-encoding here is the round-3
            # defect (on a re-capture made while the embedder was down, the
            # live write PRESERVED-or-CLEARED the node's vector, and re-encoding
            # would produce a vector the live graph does not have).
            #
            # #5004 round-6 — an explicit None is a CLEAR, not just a refusal
            # to write. Live's turn write has an `ELSE NULL` arm for exactly
            # this case (`_TURN_WRITE_CYPHER`: "a preserved vector for changed
            # text would rank the turn by text no longer on the node"). An
            # owned None that merely left `n.embedding` alone therefore
            # RESURRECTED the vector an EARLIER record had set — on the
            # highest-volume producer, under the same embedder. `pass` cannot
            # express a clear; only an explicit arm can (see `$embedding_clear`
            # in the SET clause).
            if p.get("embedding") is None:
                embedding_clear = True
        # No journalled vector (a pre-#5004 strip-era log): there is nothing to
        # restore, so recompute — the previous behaviour, kept deliberately so
        # an old journal still replays and still yields vectors.
        # #331 (review r4): only embed real content — an empty string
        # produced a junk vector in the HNSW index.
        elif not op and p.get("content"):
            try:
                from tortoise.embeddings import encode_for_store
                embedding = encode_for_store(
                    p.get("content", ""), self.required_embedding_dim)
            except Exception:
                pass

        # #2795 (D2): content_hash is DERIVED, not payload — recompute it from
        # the content being written (mirrors create_point's `_content_hash`,
        # #80). The old closed writer never wrote it, so every replayed node
        # had content_hash=NULL and the indexed dedup MATCH degraded. Operators
        # store no content (#548) — the writer synthesizes a fallback for them,
        # so a hash would match nothing (noise): skip, mirroring the embedding
        # carve-out above.
        point_content_hash = None
        if not op and p.get("content"):
            try:
                point_content_hash = _content_hash(p["content"])
            except Exception:
                # #2958 review: truthiness is not a type check — a truthy
                # non-str content (int/list/dict) from a malformed or
                # hand-edited JSONL line would raise inside
                # sha256(text.encode). Rebuild is the RECOVERY path: leave the
                # hash unset (the coalesce preserves any existing value)
                # rather than crash the whole pass.
                point_content_hash = None

        # Build SET clauses + params; context is optional (Phase 1 stop-writes, #49)
        set_clauses = [
            "n.content=$content",
            "n.is_operator=$isop",
            "n.op_type=$opt",
            "n.pointKind=coalesce($pk, n.pointKind)",
            "n.status=coalesce($st, n.status, 'live')",
            "n.authoredBy=coalesce($ab, n.authoredBy)",
            "n.embedding=CASE WHEN $embedding_clear THEN NULL "
            "    WHEN $embedding IS NULL THEN n.embedding "
            "    WHEN $embedding_verbatim THEN $embedding "
            "    ELSE vecf32($embedding) END",
            # #5004 round-3: a CALLER-owned vector's storage FORM, as its own
            # clause (mirroring `is_episodic`). `CASE … ELSE n.embedding_verbatim`
            # keeps the property ABSENT for a server-vectored point: writing an
            # explicit `false` there would make live (absent) and replay
            # (false) disagree. Set only when truthy, so it survives the later
            # re-emits that read the point back through `get_point`.
            "n.embedding_verbatim=CASE WHEN $evb THEN true "
            "    ELSE n.embedding_verbatim END",
            "n.content_hash=coalesce($ch, n.content_hash)",
            "n.confidence=coalesce($cf, n.confidence)",
            "n.createdAt=coalesce($ca, n.createdAt, $now)",
            "n.validFrom=coalesce($vf, n.validFrom)",
            "n.validTo=coalesce($vt, n.validTo)",
            "n.updatedAt=$now",
        ]
        # #5048: the payload's OWN ``is_operator`` wins when it explicitly says
        # FALSE. A mitigation Point is a NON-operator that carries an
        # ``operator`` EDGE descriptor (`#4937`), so deriving identity from
        # ``bool(op)`` re-typed it as an operator on replay — live
        # ``is_operator=false/op_type=NULL``, rebuild ``true/'IMPL'`` on every
        # mitigation: a systematic ``derived = replay(journal)`` divergence, and
        # the identity half of the record this PR's edge fold repairs. Scoped to
        # the explicit-False case, so every operator record (and every legacy
        # record that omits the flag) is byte-for-byte unchanged. The
        # IDENTITY rule is shared with ``edges._create_edges`` AND with
        # ``consistency._canonical_point_fields`` (``is_non_operator_payload``)
        # so the node's identity cannot disagree with the checker's view of it.
        # Whether the record ALSO authorises the reverse edge is the separate,
        # stricter ``has_usable_mitigation_strength`` — identity is not gated on
        # the strength, because re-typing a non-operator as an operator is a
        # divergence whether or not that record can rebuild an edge.
        _mitigation_record = is_non_operator_payload(p) and bool(op)
        params = {
            "id": p["id"], "content": p.get("content", ""),
            "isop": bool(op) and not _mitigation_record,
            "opt": None if _mitigation_record else (op.get("op_type") if op else None),
            "pk": p.get("pointKind"),
            "st": p.get("status"),
            "ab": p.get("authoredBy"),
            "embedding": embedding,
            # #5004 round-3: a caller-supplied vector is restored RAW — the
            # same form `create_point` stored it in (`$embedding`, not
            # `vecf32`, per the recorded PR #3018 decision). Casting it here
            # would narrow an explicitly caller-owned float64 list to float32
            # and the rebuild would disagree with live by ~1e-8 on every
            # component (`0.1` -> `0.10000000149011612`).
            "embedding_verbatim": bool(p.get("embedding_verbatim")),
            "evb": bool(p.get("embedding_verbatim")),
            # #5004 round-6: an owned None CLEARS — see `embedding_clear` above.
            "embedding_clear": embedding_clear,
            "ch": point_content_hash,
            "cf": p.get("confidence"),
            "ca": p.get("createdAt") or p.get("created_at"),
            "vf": p.get("validFrom"), "vt": p.get("validTo"),
            "now": _now_iso(),
        }
        # #5256: the extractedFrom read-version transit. Written ONLY when the
        # Point OWNS an `extractedFrom` AND the payload carries a well-formed
        # non-empty list of [raw_ref, contentHash] pairs whose two members are
        # non-blank strings, kept to that Point's own refs — the SAME shape
        # check + own-ref filter (the SHARED `_point_source_transit` selector)
        # `_upsert_point_edges` uses.
        # An un-sourced Point, or one whose Source has no recorded hash, carries
        # NO property (never '' and never []: honest-absent, because '' compares
        # equal to a Source's '' and reads as a false CURRENT). A payload that
        # carries a shape-valid carrier but NO `extractedFrom` ALSO carries
        # nothing: the carrier is a transit for an edge, and without the edge it
        # is a stray record (the node would otherwise equal its own journal
        # payload and `check_consistency` would stay green). `coalesce` is
        # deliberately NOT used: the value is a recorded FACT of the reading,
        # not a derived value to preserve across re-emits.
        # The shape guard is the SHARED `_valid_transit_pairs` predicate (via
        # `_point_source_transit`), the same one the edge fold uses — so a
        # corrupt/foreign journal line
        # contributes NO anchor on EITHER writer rather than one of them. This
        # clause is the only writer of a value that came off a journal line, and
        # a malformed one (a dict/set/bytes, or a list containing one) would
        # otherwise reach `SET n.sourceVersionTransit=$sv` and raise a Falkor
        # ResponseError — which, mid-`rebuild_all`, leaves the graph WIPED. A
        # corrupt/foreign journal must contribute NO anchor, not abort the
        # recovery path.
        _sv_transit = _point_source_transit(p)
        if _sv_transit is not None:
            set_clauses.append("n.sourceVersionTransit=$sv")
            params["sv"] = _sv_transit
        # A10 operator-scoped replay extension (cycle-22/23): the OperatorAdded
        # point snapshot carries `direction` (stored ALWAYS) + `label` (stored
        # when truthy) on the node — the fixed SET list above drops them,
        # which post-rebuild (a) leaves direction=NULL (a direction-omitting
        # bundle's resubmission MISSES its run-1 operator → duplicate +
        # exactly-once violated), (b) re-opens the label cross-absorption
        # class (a label-absent retry matches rebuilt label-NULL operators),
        # and (c) flips every unidirectional operator to bidirectional in EP
        # (ep.py coalesce default). Write them from the payload (ZERO new
        # record fields — the carrier already exists).
        if op:
            set_clauses.append("n.direction=$dir")
            params["dir"] = p.get("direction")
            # #7852 W1: `direction_basis` rides the same operator-scoped
            # extension — written only when the payload carries it, so a legacy
            # or foreign record leaves the property absent (never a fabricated
            # 'default').
            if p.get("direction_basis") is not None:
                set_clauses.append("n.direction_basis=$dbasis")
                params["dbasis"] = p["direction_basis"]
            if p.get("label") is not None:
                set_clauses.append("n.label=$label")
                params["label"] = p["label"]
        # #3947: `is_episodic` is a SERVER-MANAGED node property — the
        # points-quota discriminator (#1486, quota.py counts only
        # `is_episodic IS NULL OR = false` Points). `create_point` writes it
        # from its explicit kwarg on the LIVE path, but it sits in
        # `_POINT_HANDLED`, so the open-set passthrough never carried it and
        # every REPLAY silently dropped it: a rebuilt turn Point came back as
        # a plain Point — counted against quota, invisible to the episodic
        # reads. Live/replay parity gap, fixed here as an explicit clause
        # gated on the payload's own value (the `provenanceSource` shape).
        # The property stays unreachable from the generic passthrough, and
        # the payload is a server-authored journal snapshot: a
        # tenant-supplied `is_episodic` is rejected at the SDK
        # (`_sanitize_props`) and MCP (`_SERVER_MANAGED_PROPS`) boundaries.
        if p.get("is_episodic") is not None:
            set_clauses.append("n.is_episodic=$episodic")
            params["episodic"] = bool(p["is_episodic"])
        # #2897 (OD-4 disposition #3856 — ADOPT): re-apply the `tags` PROPERTY
        # half from the payload. The `PointAdded` snapshot already carries it
        # (`get_point` returns raw `properties(n)`); the fold simply never wrote
        # it, so a rebuilt graph answered no tag query. The `:Tag`/`TAGGED` half
        # is reconciled in pass 2 (`_upsert_point_edges`), and `tags` is in
        # `_POINT_HANDLED`, so the open-set passthrough does not also write it.
        # PRESENCE-based (`_stated_tags`) — the live writer's own rule, so a
        # scalar value is restored rather than silently dropped (#8317 P1).
        wrote_tags, tags_value = _stated_tags(p)
        if wrote_tags:
            set_clauses.append("n.tags=$tags")
            params["tags"] = tags_value
        # Phase 2 #49: context removed — never written
        # #4457/#4520: the shared clear-then-set seam (`tortoise/vector_write.py`)
        # clears the property FIRST, in the same atomic query, so the
        # conditional `vecf32` write below actually lands on the embedded
        # engine (the overwrite there can be silently discarded). The Cypher
        # guard fires only when a real vector is written, so the CASE's
        # preserve-the-existing-value branch is unaffected and a `MERGE`-created
        # node simply has nothing to remove.
        self.g.query(
            "MERGE (n:Point {id:$id}) " + clear_then_set_prefix("n")
            + "SET " + ", ".join(set_clauses),
            params=params,
        )
        # Ontology v2.1: also store extractedFrom as property for query convenience.
        # #3263: many-to-many — the EDGES are authoritative (see
        # _upsert_point_edges). This scalar/array prop is a query convenience
        # ONLY, and its rule is: a STRING when a single string was passed
        # (including the inference path), and an ARRAY whenever a SEQUENCE was
        # passed — even a one-element one, which is NOT collapsed. Arrays are
        # not equality-matchable (`WHERE n.extractedFrom = '<url>'` will not hit
        # them), so exact-match callers must pass the scalar or traverse the
        # edge. (Wording aligned with sdk.py `list_drafts` and ONTOLOGY v3.11;
        # an earlier version of this comment said "the single-source case",
        # which wrongly implied one-element sequences collapse.)
        source_ref = p.get("extractedFrom")
        if source_ref:
            self.g.query("MATCH (n:Point {id:$id}) SET n.extractedFrom = $ref", params={"id": p["id"], "ref": source_ref})
        # P1-2: Temporal — also store provenance source_id
        if prov.get("source_id"):
            self.g.query(
                "MATCH (n:Point {id:$id}) SET n.provenanceSource=$sid",
                params={"id": p["id"], "sid": prov["source_id"]},
            )
        # #2795 (D2): open-set passthrough — parity with create_point's
        # `SET n += $props` and with every other layer's _persist_extra_props.
        # This is the shared live+replay writer, so door 3's live drop and
        # rebuild's replay drop are fixed together. Handled + deny-listed keys
        # are excluded (precedence: the fixed clauses above already wrote
        # updatedAt/embedding/content_hash), and only persistable values
        # survive the shared type filter (#2894).
        extras = self._persist_extra_props(
            "MATCH (n:Point {id:$id})", {"id": p["id"]}, p,
            self._POINT_HANDLED | self._POINT_DENY,
            list_props=self._POINT_LIST_PROPS,
        )
        for key in extras:
            if key not in self._POINT_DECLARED_PROPS:
                logger.warning(
                    "Point prop %r is not declared — persisted via open-set "
                    "passthrough (#2795); declare it in POINT_PROPS for parity",
                    key)
        # #2795 indicator 4 / D4: a prop that genuinely cannot be restored
        # from the payload must be REPORTED, not dropped silently. The
        # recompute keys (`embedding`/`updatedAt`/`_nid`/`_graph_id`) are
        # fixed-clause/handled and excluded here to avoid noise; the genuinely
        # payload-hostile set is `_POINT_DENY - _POINT_HANDLED`.
        # #2958 review: warn ONCE per (source, key) per rebuild pass (the
        # `_deny_drop_warned` set, reset by `rebuild_all`) — the #548 synthetic
        # snapshot carries EP-owned state for every dreamed graph-only point, so
        # a per-row warning emitted O(N) lines and buried genuine violations. On
        # the live path the set persists for the process, which is the right
        # cadence for a policy-level signal.
        # #8018 review: the de-dup key carries the SOURCE and the text is chosen
        # by it, because two sources feed this report and they are NOT the same
        # event. On the journal arm the payload IS a journal record that carries
        # the key, so only the writer's policy refuses it. On the #548 arm the
        # payload is the pre-wipe GRAPH snapshot rather than a journal record,
        # so the snapshot carried the value into this pass.
        # Keying the de-dup on the bare key let the arm that happened to run
        # first (synthetic events sort first) speak for the other, which
        # re-minted the very misattribution #8018 removed.
        # ⛔ Neither arm may claim what this writer cannot see. "The journal never
        # carries EP state" is FALSE (`consistency.py::_JOURNAL_RESTORABLE_DENY`:
        # `posterior_alpha`/`posterior_beta`/`lastDreamedAt` fold from
        # `ConfidenceChanged`, `outdated`/`expiredAt` from the
        # PointSuperseded/PointInvalidated folds), and "this id has NO journal
        # record" is FALSE whenever the only record is a promote snapshot or a
        # revise (#6239's promote-only shape), and a promote-only id gets BOTH
        # a synthetic event and a promoting fold in the same pass (#8018
        # review round 3). Both claims are therefore made about the PAYLOAD's
        # source only — the snapshot arm names the snapshot as the carrier and
        # says NOTHING about the id's journal coverage.
        for key in self._POINT_DENY - self._POINT_HANDLED:
            if p.get(key) is None:
                continue
            warned = getattr(self, "_deny_drop_warned", None)
            if warned is None:
                warned = self._deny_drop_warned = set()
            if (from_prewipe_snapshot, key) in warned:
                continue
            warned.add((from_prewipe_snapshot, key))
            if from_prewipe_snapshot:
                # #8018 review round 3: this says what the flag PROVES — the
                # payload's source — and nothing about the id's journal
                # coverage. "(the id has no journaled creation)" was false in
                # exactly the shapes this branch must support: a promote-only
                # id (`_JOURNAL_CREATING_EVENT_TYPES` counts `PointPromoted`
                # as a creation) gets a synthetic event AND a promoting fold in
                # one pass, and the persisted-sidecar union (`synthetic_events`
                # in `rebuild_all`) is not re-checked against `log_point_ids`,
                # so an id can be BOTH synthetic and journal-created. In both
                # the sibling message below says the journal record carries the
                # key, so an id-level denial would be contradicted beside it.
                logger.warning(
                    "Point prop %r dropped — deny-listed (recompute/EP-owned, "
                    "#2795): this payload is the #548 pre-wipe graph SNAPSHOT, "
                    "not a journal record, so the snapshot is what carried the "
                    "key into this pass and the writer refuses it by policy — "
                    "a different case from the journal arm, where a record "
                    "does carry it (#8018)", key)
                continue
            logger.warning(
                "Point prop %r dropped — deny-listed (recompute/EP-owned, "
                "#2795): this payload IS the journal record and it carries "
                "the key, so only the writer's policy refuses it — a policy "
                "drop, not a journal gap. The #548 snapshot arm reports the "
                "other case, where the payload is the pre-wipe graph snapshot "
                "rather than a journal record (#8018)", key)
        # (b) a policy-denied list is also reported: the live SDK writer stores
        # raw lists, but replay refuses an UNDECLARED one, so the drop MUST be
        # visible (#2795 indicator 4 / D7). `tags` was this branch's exemplar
        # until #2897 landed — it is `_POINT_HANDLED` now and replayed by its
        # own clause, so a `custom_list`-shaped prop is what the branch
        # actually covers. The message below names the POLICY as the cause, because
        # the journal DOES carry such a key and the drop is the replay writer's
        # refusal — not missing data (#2897 states the mechanism; #8018 fixed the
        # attribution here, which had misled a real reader of #3817).
        # #2958 review: tuples count as arrays here too (engine parity).
        for key, val in p.items():
            if not (isinstance(val, (list, tuple)) and val):
                continue
            if key in self._POINT_LIST_PROPS or key in self._POINT_HANDLED \
                    or key in self._POINT_DENY or key in self._META_KEYS:
                continue
            logger.warning(
                "Point list prop %r dropped — undeclared list props are never "
                "written raw (#2795): the payload DOES carry it and replay "
                "refuses it by policy (`_POINT_LIST_PROPS` is empty), so this "
                "is a policy drop, not a journal gap (#2897, #8018)", key)
        # #4042: report which conditional derived writes actually landed (see
        # the docstring). `embedding`/`point_content_hash` are exactly the
        # values the `CASE`/`coalesce` clauses above gate on, plus the open-set
        # key set the passthrough ACTUALLY persisted (#5048).
        # #2897: `tags` rides the same report even though its own clause — not
        # the passthrough — wrote it. The pass-1b tag boundary reads the
        # WRITER'S OUTCOME rather than re-spelling its condition (the same rule
        # `mitigation_strength` follows, #5048); the drift warning above has
        # already run, so adding it here cannot produce a false "not declared".
        if wrote_tags:
            extras["tags"] = tags_value
        return (embedding is not None, point_content_hash is not None,
                frozenset(extras))

    def _upsert_point_edges(self, p: dict, contains_session: str | None = None) -> None:
        """Wire all Point edges (provenance + about + operator + session).

        Single source of truth for Point edge parity between apply() and
        rebuild_all() pass 2 (#330) — same role as _upsert_point_props for
        node properties.

        ``contains_session`` (#3947) is the CONTAINS-container link of an
        episodic turn Point, and arrives on the EVENT envelope rather than in
        the point payload: it is a capture-write structural fact, not a
        Point property. The replay reads it from the **raw** envelope BEFORE
        `_norm` (`{**ev, **ev["point"]}`) can splice the payload over it, so
        a point key of the same name cannot shadow it; `_META_KEYS` is the
        writer-side backstop that keeps the key out of the node, and the
        SDK/MCP boundary rejects are the fail-closed backstop for tenants.
        """
        # Ontology v2.1: link Point → Source via extractedFrom edge.
        # #3263: many-to-many — one edge per source. _link_source fans a list
        # out to N edges (ontology §3.3 amended to many→many).
        # #5256: the read-version anchor travels in the SAME payload as the
        # ref, on the node's own `sourceVersionTransit` carrier list. We hand it
        # to the writer as a {raw_ref: hash} map (the journal-stable key — see
        # `resolve_source_versions`) — this fold NEVER reads
        # `s.contentHash` (the Source may have advanced since live time). A
        # malformed/falsy payload contributes NO anchor rather than crashing
        # `dict(...)` or stamping a non-str value.
        source_ref = p.get("extractedFrom")
        if source_ref:
            # The SHARED `_point_source_transit` selector: the all-or-nothing
            # `_valid_transit_pairs` shape check — a non-empty list of
            # 2-element `[str, str]` pairs whose members are BOTH non-blank —
            # PLUS the Point's own-ref filter, so a
            # partially-malformed carrier cannot stamp an edge while the node
            # clause writes no record, and a pair for a source this Point does
            # not reference is dropped by BOTH writers alike.
            _svlist = _point_source_transit(p)
            source_versions = (
                {pair[0]: pair[1] for pair in _svlist}
                if _svlist is not None else None)
            self._link_source(p["id"], source_ref,
                              source_versions=source_versions)
        # #3947: the episodic turn stream is `(:Session)-[:CONTAINS]->(:Point)`
        # (ONTOLOGY §4.5, the session container's one structural edge). NOT a
        # member of the generic direct-edge replay (#1048; the journaled
        # `DirectEdgeCreated`/`DirectEdgeRepoint` descriptors ARE replayed since
        # #8499, but this CONTAINS link is carried by the capture TURN LOOP's
        # PointAdded, not by a descriptor): it carries no caller attrs, and it
        # is the capture TURN LOOP ALONE that journals the link, on the turn's
        # own PointAdded (the only two `contains_session=` emission sites are
        # the SDK and hosted turn loops). The extractor-minted points are wired into the Session by
        # OTHER, raw CONTAINS writes scattered through the capture/extraction
        # path — every one of them unjournaled — so a journal-only `rebuild()`
        # still drops those edges; only `rebuild_all`'s `:Session` snapshot
        # restores them. That gap is #3664/#3722's scope. Do NOT read this fold
        # as covering it: the list of unjournaled writers is deliberately not
        # enumerated here, because line-number inventories rot (an earlier
        # draft of this comment cited three and missed two).
        if isinstance(contains_session, str) and contains_session:
            self._link_session(contains_session, p["id"])
        # aboutEntities → per-type about edges (Ontology v2.1 Phase 1)
        about = p.get("aboutEntities")
        if about and isinstance(about, list):
            for entity_name in about:
                self._create_about_edges(p["id"], str(entity_name))
        # #2897 (OD-4 disposition #3856 — ADOPT): the `:Tag`/`TAGGED` half — a
        # SNAPSHOT-DERIVABLE edge of exactly the kind #2489 established for
        # `extractedFrom`/`about*`: the point property determines the edge, so
        # it is never journaled separately and live/replay share ONE
        # reconciliation routine (the projection's `_sync_tags`; the SDK live
        # path delegates to it).
        # The value is read from the NODE, not from `p`: pass 1 has already
        # folded every `PointRevised` (including a tag EDIT and its explicit
        # clear), so the node holds the journal's FINAL tag value — while the
        # `PointAdded` payload this pass iterates holds only the point's
        # CREATION list. Syncing from the payload would resurrect a tag the
        # journal removed (the #3299/#2488 class;
        # `test_a_removed_tag_is_not_resurrected`). One indexed read per point,
        # and idempotent when the same id is added more than once.
        # The `isinstance(..., list)` test is `_sync_tags`' own list-only rule
        # (the live rule too), so a payload that stated a scalar `tags` restores
        # the PROPERTY without inventing edges on either path.
        _tag_row = self.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.tags", params={"id": p["id"]},
        ).result_set
        if _tag_row and isinstance(_tag_row[0][0], list):
            self._sync_tags(p["id"], _tag_row[0][0])
        if p.get("operator"):
            self._create_edges(p)

    # #3664: the `EntityLinked` record's validated vocabulary. The record is
    # replayed from a journal FILE, so its label / relationship-type strings
    # must never be interpolated into Cypher unvalidated. Mirrors
    # ``session_link.ENTITY_LINKED_*`` EXACTLY (kept local so the projection
    # stays self-contained and import-free) — pinned by
    # tests/test_capture_entity_attachment_3664.py::test_entity_linked_vocabulary_drift,
    # because a silent divergence (writer accepts a predicate the fold
    # rejects, or vice versa) loses the edge with no error.
    _ENTITY_LINKED_RELS: frozenset = frozenset({
        "aboutSubject", "aboutObject", "aboutEvent", "aboutPoint",
        "aboutDocument", "aboutAction", "aboutSource",
    })
    _ENTITY_LINKED_LABELS: frozenset = frozenset({
        "Session", "Point", "Event", "Object", "Subject",
        "Source",
    })
    # ONTOLOGY §3.2 triples — the field sets above are their projections, but
    # membership in each set does NOT imply the COMBINATION is legal
    # (``(Session)-[:aboutSubject]->(Subject)`` is in all three sets and in no
    # triple). Mirrored EXACTLY from ``session_link.ENTITY_LINKED_TRIPLES`` and
    # pinned by the drift test.
    _ENTITY_LINKED_TRIPLES: frozenset = frozenset({
        ("aboutSubject", "Point", "Subject"),
        ("aboutSubject", "Event", "Subject"),
        ("aboutObject", "Point", "Object"),
        ("aboutObject", "Event", "Object"),
        ("aboutObject", "Session", "Object"),
        ("aboutEvent", "Point", "Event"),
        ("aboutPoint", "Event", "Point"),
        # D10: aboutDocument targets a :Source; the Document-source triples are
        # dropped (§3.2 does not permit a Source as an aboutSubject/Object/
        # Event source). Mirrored EXACTLY from session_link.ENTITY_LINKED_TRIPLES.
        ("aboutDocument", "Event", "Source"),
        ("aboutSource", "Point", "Source"),
        ("aboutSource", "Event", "Source"),
        ("aboutAction", "Point", "Point"),
    })

    def _fold_entity_linked(self, ev: dict) -> int:
        """#3664: fold an ``EntityLinked`` record into its live edge.

        The capture entity-linking pass (``session_link.link_entity``) writes
        ``(Session)-[:aboutObject]->(Object)`` / ``(Point)-[:aboutObject]->
        (Object)`` edges LIVE and journals the flat logical identities. This
        fold is the replay consumer: an idempotent MERGE keyed on the two
        logical ids, so a JSONL wipe+rebuild reproduces the attachment
        (live == rebuild). Returns 1 when the edge exists after the fold, 0
        when the record is malformed or EITHER endpoint is absent (honest —
        neither was re-created by any journaled event).

        It MATCHes BOTH endpoints, so a 0-row fold is NOT target-specific: a
        missing Session/Point SOURCE 0-rows exactly like a missing target.

        Back-compat wrapper over :meth:`_fold_entity_linked_reason`, which
        additionally distinguishes a MALFORMED record from an ABSENT endpoint
        so the trailing sweeps can make the malformed case observable without
        letting an honestly absent endpoint flood the log (review P2, #3722).
        """
        matched, _reason = self._fold_entity_linked_reason(ev)
        return matched

    def _fold_entity_linked_reason(self, ev: dict) -> tuple[int, str]:
        """Fold one ``EntityLinked`` record; return ``(edge_present, reason)``.

        ``reason`` is ``"ok"`` (the edge exists after the fold), ``"absent"``
        (a well-formed link whose endpoint was not re-created — silent: an
        absent entity is normal, not a defect), or ``"malformed"`` (unknown
        ``edge_type``/label, a non-string field, or an unwritable id — the
        record is a journal DEFECT). The malformed case WARNs here, and only
        here, so EVERY consumer (``apply()``, ``rebuild_all``'s sweep, and
        ``fold_deferred_entity_links``) gets the signal from one place, while
        the absent case stays quiet.

        The two ids come from a journal FILE and ride as Cypher parameters,
        so they get the SAME ``_writable_id`` gate the sibling folds use
        (``_revise_point`` / ``_apply_annotator`` / PointRetracted): a NUL or
        lone-surrogate id raises at parameter parse, and ``rebuild_all``'s
        sweep has no try/except — that raise would abort the rebuild AFTER
        the wipe. A malformed id is a NO-OP here.
        """
        from tortoise.projection import _writable_id

        def _malformed(why: str) -> tuple[int, str]:
            logger.warning(
                "EntityLinked fold dropped a MALFORMED record (%s): id=%r "
                "source_id=%r source_label=%r target_id=%r target_label=%r "
                "edge_type=%r — the journal line is not a replayable link",
                why, ev.get("id") if isinstance(ev, dict) else None,
                ev.get("source_id") if isinstance(ev, dict) else None,
                ev.get("source_label") if isinstance(ev, dict) else None,
                ev.get("target_id") if isinstance(ev, dict) else None,
                ev.get("target_label") if isinstance(ev, dict) else None,
                ev.get("edge_type") if isinstance(ev, dict) else None,
            )
            return 0, "malformed"

        if not isinstance(ev, dict):
            return 0, "malformed"
        rel = ev.get("edge_type", "aboutObject")
        # Type-check BEFORE the membership test: ``edge_type`` /
        # ``source_label`` / ``target_label`` come from a journal FILE and a
        # list/dict value raises ``TypeError: unhashable type`` on the frozen
        # -set lookup. ``rebuild_all``'s sweep has no try/except, so that
        # raise would abort the whole rebuild AFTER the wipe — a malformed
        # line must be a NO-OP, exactly as this fold's docstring promises.
        if not isinstance(rel, str) or rel not in self._ENTITY_LINKED_RELS:
            return _malformed("unknown/non-string edge_type")
        src_label = ev.get("source_label")
        if not isinstance(src_label, str) \
                or src_label not in self._ENTITY_LINKED_LABELS:
            return _malformed("unknown/non-string source_label")
        tgt_label = ev.get("target_label", "Object")
        if not isinstance(tgt_label, str) \
                or tgt_label not in self._ENTITY_LINKED_LABELS:
            return _malformed("unknown/non-string target_label")
        sid = ev.get("source_id") or ev.get("id")
        tid = ev.get("target_id")
        if not _writable_id(sid) or not _writable_id(tid):
            return _malformed("unwritable/NUL-surrogate endpoint id")
        # The COMBINATION must be a permitted ONTOLOGY §3.2 triple. Checking
        # each field alone admits (Session)-[:aboutSubject]->(Subject) and
        # (Point)-[:aboutPoint]->(Point), which the table forbids — and the
        # fold would faithfully replay an edge the ontology does not have.
        if (rel, src_label, tgt_label) not in self._ENTITY_LINKED_TRIPLES:
            return _malformed("not a permitted ONTOLOGY §3.2 triple")
        # #1370: the binding confidence is OPTIONAL on the record. It is SET
        # only when present, mirroring the live writer's conditional SET — so
        # a later no-confidence EntityLinked for the same edge cannot clear a
        # confident one on replay (the live pre-probe short-circuits, and this
        # fold must agree). The value is coerced fail-closed by the SHARED
        # helper: non-numeric, bool, NaN/±inf, overflow (10**400) and
        # out-of-[0,1] values all become None — no SET, no FalkorDB parameter
        # rejection (a raise here would abort rebuild_all AFTER the wipe).
        from tortoise.session_link import coerce_confidence
        conf = coerce_confidence(ev.get("confidence"))
        if conf is None:
            r = self.g.query(
                f"MATCH (s:{src_label} {{id:$sid}}), (t:{tgt_label} {{id:$tid}}) "
                f"MERGE (s)-[:{rel}]->(t) RETURN count(s)",
                params={"sid": sid, "tid": tid},
            )
        else:
            r = self.g.query(
                f"MATCH (s:{src_label} {{id:$sid}}), (t:{tgt_label} {{id:$tid}}) "
                f"MERGE (s)-[e:{rel}]->(t) SET e.confidence=$conf "
                "RETURN count(s)",
                params={"sid": sid, "tid": tid, "conf": float(conf)},
            )
        n = int(r.result_set[0][0]) if r.result_set else 0
        return (1, "ok") if n else (0, "absent")

    def fold_deferred_entity_links(self, events,
                                   hard_delete_seqs=None) -> int:
        """Fold a trailing batch of ``EntityLinked`` records (#3664).

        ``apply()`` is a ONE-record API, so it folds the type inline — correct
        on the live path, where both endpoints already exist. The whole-journal
        apply()-based replay engines (``rebuild()``, ``recover_from_log``,
        ``backup.restore``'s JSONL fallback) buffer the records and call this
        AFTER every creation event has applied, so a link whose endpoint is
        created LATER in the journal still folds — the same forward-reference
        treatment ``rebuild_all`` gives the type. Without this the engines
        disagree on a forward-reference journal. The fold is an idempotent
        MERGE, so deferral changes nothing else.

        ``events`` is a sequence of ``(journal_seq, record)`` pairs — the seq
        is what makes the HARD-DELETE staleness rule below possible. A bare
        record is also accepted (its list index is its seq) so a caller with
        no envelope still works.

        ``hard_delete_seqs`` (``{id: {label: max_hard_delete_seq}}``, built
        by ``projection.journal_hard_delete_seqs``) makes the fold HARD-DELETE
        aware. ``_fold_entity_linked`` is an unconditional MATCH…MERGE, and
        ids are reused routinely (name-deterministic for Object/Subject,
        content-addressed for Points), so without this boundary a link whose
        endpoint was deleted and later re-created under the SAME id came back
        on replay while live had no such edge — the deleted link RESURRECTED.
        A link at seq L whose source OR target has a hard delete at a seq
        AFTER L **that can remove that endpoint's LABEL** is therefore
        skipped. The label test is load-bearing (#3722 review cycle 5 P2):
        ``EntityMutated`` replays ``_delete_entity_by_id`` (the six canonical
        labels only) and ``PointsMerged`` removes Points only, so a journaled
        hard delete can NEVER remove a ``:Session`` node — an id-only test
        wrongly suppressed a live ``(Session)-[:aboutObject]->(Object)`` edge
        whenever any other-label entity with the same id was deleted later.
        The boundary is EXACT per ``(id, label)`` (#3722 review cycle 6 P2):
        each label carries the max seq of a delete that can remove IT, so a
        later ``PointsMerged`` never suppresses an ``Object``-side link whose
        same-id ``Object`` was re-created after an earlier delete.
        A later re-link (its own seq > the delete) is judged on its own seq
        and survives, so the rule needs no extra state.

        Returns the number of links APPLIED (the edge exists after the fold) —
        the honest count: a MALFORMED record, an ABSENT endpoint, and a STALE
        link are all DROPPED and never counted as applied (review P2, #3722).
        """
        # #3722 review (cycle 5): the label-aware staleness helper lives next
        # to the reader that now publishes per-delete label sets (lazy import
        # mirrors ``_writable_id`` — avoids the import cycle).
        from tortoise.projection import _hard_delete_suppresses

        hard_delete_seqs = hard_delete_seqs or {}
        applied = 0
        for seq, raw in _seq_events(events):
            ev = self._norm(raw) if isinstance(raw, dict) else raw
            if isinstance(ev, dict):
                sid = ev.get("source_id") or ev.get("id")
                tid = ev.get("target_id")
                if (_hard_delete_suppresses(
                        hard_delete_seqs, sid, ev.get("source_label"), seq)
                        or _hard_delete_suppresses(
                            hard_delete_seqs, tid,
                            ev.get("target_label", "Object"), seq)):
                    # Stale: the live endpoint was hard-deleted AFTER this
                    # link, and its re-creation does not bring the edge back.
                    continue
            matched, _reason = self._fold_entity_linked_reason(ev)
            applied += matched
        return applied

    def _fold_session_recorded(self, ev: dict) -> int:
        """#3664: fold a ``SessionRecorded`` record into the :Session node.

        The capture path MERGEs the Session with a raw graph write; this
        record is its journal carrier, so the node (and any ``EntityLinked``
        edge from it) replays. Idempotent MERGE keyed on ``id``; ``created_at``,
        ``source``, ``actor_user_id`` and ``capture_lane`` are coalesce-preserved
        (first writer wins, mirroring the live merge), ``turn_count`` tracks the
        latest journaled capture. Returns 1 when the node exists after the fold,
        0 on a malformed record.

        BOTH the id and every journal-derived property value are gated for
        WRITABILITY, not just type (review P1): a NUL / lone-surrogate id and
        a map-valued ``created_at`` / ``turn_count`` / ``harness`` /
        ``actor_user_id`` / ``capture_lane`` payload are each REJECTED by
        ``_annotator_value_ok`` BEFORE the bind — a malformed id is a NO-OP
        (return 0), a malformed field is OMITTED, never bound, so neither can
        reach parameter parse. That matters because ``rebuild_all`` folds this
        record INLINE (no try/except) AFTER the wipe.

        ``entity_links_attempted`` / ``entity_links_created`` are carried by a
        ``SessionRecorded`` the capture emits after the link pass
        (``sdk.capture_session``) — the THIRD of the four, after the turn
        write's own trailing record (#4911) — so the counters the live raw SET
        writes are durable too — ``recover_from_log`` / a journal-only
        ``rebuild()`` otherwise came back with them null (review P2, #3722).

        ``capture_ok`` / ``capture_extractor`` ride the last, TRAILING
        ``SessionRecorded`` the capture emits right after the live
        ``SET s.capture_ok / s.capture_extractor`` — the FOURTH record. Without it those two came
        back null on a journal-only rebuild, and null is CONSUMED by the
        #2335 WI-2b TRUE-retry gate as the legacy "presumed captured" case — a
        session whose capture FAILED stopped retrying (review P2, #3722).
        Same overwrite semantics as the live SET (these are not
        coalesce-preserved); a NUL-laden string is OMITTED by the shared value
        gate, never bound.

        ``capture_redactions`` (#4911) rides the trailing record
        ``sdk._write_capture_turns`` emits after its live
        ``SET s.capture_redactions`` — the SECOND of the four, emitted right
        after the capture's opening record and BEFORE the link pass, same
        reason as the pair below: a
        journal-only rebuild must not restore the Session as though nothing
        was ever redacted. Overwrite semantics, like ``turn_count``.
        """
        from tortoise.projection import _annotator_value_ok, _writable_id

        if not isinstance(ev, dict):
            return 0
        sid = ev.get("id")
        if not _writable_id(sid) or not sid:
            return 0
        sets: list[str] = []
        params: dict = {"sid": sid}
        created_at = ev.get("created_at")
        if created_at is not None and _annotator_value_ok(created_at):
            sets.append("s.created_at=coalesce(s.created_at, $created_at)")
            params["created_at"] = created_at
        # #3516 §B: the producer lane. Folded with coalesce (first writer wins)
        # to MATCH the live capture write, which is also first-writer-wins. No
        # producer journals this field YET: no caller passes `capture_lane` to a
        # journaling writer (the hosted lane passes no `on_session_merged` at
        # all; the SDK lane passes one but never a lane), so this is a GUARD for
        # the first journaling lane (the store-sync CLI), not a parity property
        # that holds today.
        lane = ev.get("capture_lane")
        if lane is not None and _annotator_value_ok(lane):
            sets.append("s.capture_lane=coalesce(s.capture_lane, $v_capture_lane)")
            params["v_capture_lane"] = lane
        # #3516 §B: the client-timestamp floor's inputs, with the SAME guard
        # status as `capture_lane` above — no producer journals these yet, so
        # this is a GUARD for the first journaling lane rather than a parity
        # property that holds today. Without it a journal-only rebuild restores
        # a Session whose floor reads DISABLED where live it was evaluable, and
        # the two legs would disagree about the same session.
        _cap_at = ev.get("client_captured_at")
        if _cap_at is not None and _annotator_value_ok(_cap_at):
            # #3516 §B: the instant and its clock are ONE PAIR. The `CASE` — not
            # the clause order — is what makes it correct: MEASURED on FalkorDB,
            # every `SET` right-hand side is evaluated against the row as it was
            # at the START of the `SET` clause, so `s.client_captured_at` still
            # reads the PRE-update value either way. Independent coalescing would
            # let a replayed event hang a later event's clock on an earlier
            # event's instant, flipping the floor from PASSED to DISABLED.
            #
            # The guard is TRUTHINESS, matching the sink it mirrors
            # (`sdk._write_session_and_turns`) — `""`/`0`/`False` must be absent
            # on replay exactly as they are live, or a rebuild disagrees with the
            # thing it is rebuilding.
            _cap_src = ev.get("client_captured_at_source")
            if _cap_src and _annotator_value_ok(_cap_src):
                sets.append(
                    "s.client_captured_at_source=CASE WHEN s.client_captured_at IS NULL "
                    "THEN $v_client_captured_at_source ELSE s.client_captured_at_source END")
                params["v_client_captured_at_source"] = _cap_src
            sets.append(
                "s.client_captured_at=coalesce(s.client_captured_at, $v_client_captured_at)")
            params["v_client_captured_at"] = _cap_at
        # #3516 §D: the transcript stem the capture came from. Set-if-absent
        # (coalesce), the SAME semantics as the live raw SET
        # (`sdk._write_session_and_turns`): the store-sync backstop ships the
        # SAME session after the hook, and an absent/different stem from the
        # later writer must never relabel what the hook observed. The
        # truthiness guard matches the sink too — `""`/`0`/`False` stay absent
        # on replay exactly as they are live. Without this arm a journal-only
        # rebuild loses `source` and live/replay diverge (review P2).
        _src = ev.get("source")
        if _src and _annotator_value_ok(_src):
            sets.append("s.source=coalesce(s.source, $v_source)")
            params["v_source"] = _src
        for prop in ("turn_count", "harness", "entity_links_attempted",
                     "entity_links_created", "capture_ok",
                     "capture_extractor", "capture_redactions"):
            val = ev.get(prop)
            if val is not None and _annotator_value_ok(val):
                sets.append(f"s.{prop}=$v_{prop}")
                params[f"v_{prop}"] = val
        sets.append("s.is_episodic=true")
        if ev.get("actor_user_id") is not None:
            uid = ev["actor_user_id"]
            if _annotator_value_ok(uid):
                sets.append("s.actor_user_id=coalesce(s.actor_user_id, $uid)")
                params["uid"] = uid
        r = self.g.query(
            f"MERGE (s:Session {{id:$sid}}) SET {', '.join(sets)} "
            "RETURN count(s)",
            params=params,
        )
        return int(r.result_set[0][0]) if r.result_set else 0

    def _link_session(self, session_id: str, point_id: str) -> None:
        """Recreate the capture Session + its CONTAINS edge to one turn (#3947).

        The `:Session` node is itself part of the unjournaled capture write
        (the live loop MERGEs it raw, right before the turn loop), so a
        replay has to recreate it here — carrying `is_episodic: true`, the
        flag the ontology pins on the session container (ONTOLOGY §4.5: "The
        capture graph's :Session node (session container, CONTAINS → turn
        Points) also carries is_episodic: true").

        Ordering mirrors the live loop: the node MERGE runs BEFORE the edge
        MERGE — a full-path MERGE with a missing edge makes FalkorDB create
        the whole path from scratch, duplicating the Point node (#490 review
        P2-2).

        FALLBACK (originally review F6): this recreates a MINIMAL Session —
        `id` + `is_episodic` only. It is the path for a journal that carries
        NO `SessionRecorded` for the session: a pre-#3664 journal, or a
        journal-less/hosted lane whose live capture never journaled the node.
        For such a journal a journal-only `rebuild()` has nothing to replay,
        so `capture_ok`, `turn_count` and `created_at` come back absent, and a
        later capture may read `capture_ok=None` as the legacy "presumed
        captured" case (#2335) instead of retrying.

        The durable JOURNAL carrier now exists (#3664, this change): the SDK
        capture emits `SessionRecorded` (node props + the entity-link outcome
        counters, and a trailing record carrying `capture_ok` /
        `capture_extractor`), folded by `_fold_session_recorded` — so for any
        post-#3664 journal `rebuild()` restores the full container and this
        method's `SET s.is_episodic=true` is a harmless re-assertion. For
        `rebuild_all` the pre-wipe `:Session` snapshot restore loop runs
        BEFORE pass 2 (this method's only call site, reached via
        `_upsert_point_edges`), so the full container is written FIRST and
        this minimal MERGE is a no-op on properties — the stub is never
        written, hence never overwritten. So the durability split is now:
        `rebuild_all` carries the container (and its CONTAINS links) via the
        pre-wipe sidecar; a journaled capture carries it via `SessionRecorded`;
        only a journal with no `SessionRecorded` still yields the stub.
        """
        # #7369: `session_id` is the Session MERGE key (`MERGE (s:Session
        # {id:$sid})`), reached from `PointAdded.contains_session` off the raw
        # journal envelope — an unwritable one aborts after the wipe.
        from tortoise.projection import _log_identity_skip, _writable_id
        if not _writable_id(session_id):
            _log_identity_skip("Session", session_id, "id (MERGE key)")
            return
        # #7951 R2-P2: mirror `_link_source`'s pre-delete gate. A `CONTAINS`
        # edge derived from a `PointAdded` that precedes a later
        # `SessionDeleted` was severed live by the delete's `DETACH DELETE`;
        # a re-capture's fresh Session must not inherit the old incarnation's
        # edge. Only active during `rebuild_all` pass 2.
        _replay_del = self._replay_session_deleted_seq
        if (_replay_del is not None
                and _replay_del.get(session_id, -1) > self._replay_seq):
            return
        self.g.query(
            "MERGE (s:Session {id:$sid}) SET s.is_episodic=true",
            params={"sid": session_id},
        )
        self.g.query(
            "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
            "MERGE (s)-[:CONTAINS]->(t)",
            params={"sid": session_id, "tid": point_id},
        )

    def _fold_session_deleted(self, ev: dict, *, remove_session: bool = True,
                              remove_source: bool = True) -> int:
        """#7951: the ``:Session`` node's hard-delete carrier.

        ``hosted_api._delete_session_sync`` removes the ``:Session`` with a raw
        ``DETACH DELETE``. The node is NOT a canonical entity label
        (``_CANONICAL_ENTITY_ID_PROPS`` has no ``Session``), so no
        ``EntityMutated`` record can delete it — that fold would fall back to
        the id-wide canonical delete and leave the Session standing while
        ``SessionRecorded`` re-materialised it on replay. Without a carrier the
        whole session deletion is invisible to ``derived = replay(journal)`` and
        the next rebuild RESURRECTS it (with its ``CONTAINS`` edges re-wired).

        The fold removes the ``:Source`` the session delete removes in the same
        step — its agentSession stub, ``url = session:{id}`` — because that
        node is re-materialised by ``rebuild_all``'s pass-2 ``_link_source``
        from the deleted Points' own payloads, so it must be re-asserted
        together with the container. Both nodes are keyed off the SAME
        re-creation anchor (a later ``SessionRecorded`` for the id), so one
        carrier cannot drift from the other.

        Idempotent: a 0-row delete is the record's END STATE (a retried delete,
        or a replay onto a graph the record already cleared), so it is NEVER a
        fold-miss and must not be refused. ``DETACH`` drops the ``CONTAINS``
        edges; the owned Points and Events themselves are removed by their own
        ``EntityMutated`` op=delete records, which the same delete site emits.

        ``remove_session`` / ``remove_source`` (#7951 P1-a) let the caller
        suppress ONE of the two removals. They exist for ``rebuild_all``'s
        DEFERRED re-apply: the Source has a SECOND journal re-creation path
        the container anchor cannot see — ``_link_source`` mints
        ``session:{id}`` from ANY journaled ``PointAdded`` carrying
        ``extractedFrom=session:{id}`` — so a deferred re-apply carried out
        after pass 2 must not delete a Source a LATER record re-materialised
        while still deleting a container no later record re-materialised.
        Both default ``True``: the inline folds (``apply`` / ``rebuild`` /
        ``rebuild_all`` pass 1b) are chronology-ordered, so a later record's
        own fold re-creates whatever the delete removed and no suppression is
        needed.

        Returns the number of Session nodes deleted (0 when ``remove_session``
        is False, whatever the Source removal did).
        """
        from tortoise.projection import _writable_id

        if not isinstance(ev, dict):
            return 0
        sid = ev.get("id")
        if not _writable_id(sid):
            return 0
        if remove_source:
            self.g.query(
                "MATCH (src:Source {url:$url}) DETACH DELETE src",
                params={"url": f"session:{sid}"},
            )
        if not remove_session:
            return 0
        r = self.g.query(
            "MATCH (s:Session {id:$sid}) DETACH DELETE s RETURN count(s)",
            params={"sid": sid},
        )
        return int(r.result_set[0][0]) if r.result_set else 0

    def _upsert(self, p: dict, contains_session: str | None = None) -> None:
        """Upsert a Point: node properties via _upsert_point_props, then edges."""
        self._upsert_point_props(p)
        self._upsert_point_edges(p, contains_session=contains_session)

    def _point_status(self, pid: str):
        """The replayed ``status`` of a Point, or ``None`` when the node does
        not exist or carries no ``status``.

        #6239: the ONE reader for the promote gate. Every replay engine asks the
        same question — "was this Point ``draft``/status-less at the record's
        position?" — of its own graph state, so the gate cannot drift between
        them. A missing node and a status-less node are deliberately the SAME
        answer: the capture path promotes an unset status, and the #2256
        promote-only journal materializes the node from the snapshot itself, so
        both must read as "promote legal" (see ``promote_status_applies``).
        """
        row = self.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.status",
            params={"id": pid}).result_set
        return row[0][0] if row else None

    def _delete(self, pid: str) -> None:
        self.g.query("MATCH (n:Point {id:$id}) DETACH DELETE n", params={"id": pid})

    def _retract(self, pid: str, now: str | None = None) -> int:
        """Mark a Point as retracted instead of hard-deleting (#689).

        Retracted points are hidden from normal reads (get_point, query,
        paginated_query all filter status='retracted') but remain queryable via
        raw Cypher. This preserves data integrity — retraction is reversible.

        Historical note: prior to #689, retraction hard-deleted points via
        DETACH DELETE. Points retracted before this change are irrecoverably
        lost (the content existed only in the projection, and the projection
        deleted it). Future retractions leave this tombstone.
        Returns the number of Points matched (#3585): a 0-row retract is a
        fold-miss the caller records as a non-folded event, since the
        mutation is otherwise lost on replay.

        ``now`` (#5048, recorded from #4666) is the record's own ``ts`` — the
        instant the producer minted and wrote to the node. The fold must
        REPLAY it, not read its own clock: ``updatedAt`` is RECORDED —
        docs/durability-posture.md declares that for ``:Source.updatedAt``
        ("minted once by the producer and carried on the record"), and this is
        the same field under the same rule, not a Point row the doc already
        carries. A rebuild that called ``_now_iso()`` here stamped the
        rebuild's wall-clock onto every retracted point and agreed with
        neither the live node nor the producer's record.

        ``now`` is gated by ``_usable_instant`` — the SAME gate the id below
        and the sibling folds' ``updated_at`` use. Without it a corrupt journal
        line (a dict/list/int ``ts``) rides a Cypher parameter and aborts
        ``rebuild_all`` AFTER the wipe, leaving the claim LIVE — the pre-fix
        code never read ``ts`` at all, so this hole only appears with the
        recorded instant. A record that states no USABLE instant writes NO
        column, rather than inventing one.

        RESIDUAL, measured and NOT fixed by the no-column choice: it does not
        make the two ``#330`` engines agree for a ts-less record.
        ``_apply_one`` keeps the value the ``PointAdded`` snapshot carried,
        while this arm leaves whatever pass-1a's ``_upsert_point_props`` just
        wrote — and that is the REPLAY's clock (``n.updatedAt=$now`` with
        ``now=_now_iso()``). Closing that needs pass-1a to stop re-clocking
        ``updatedAt``, which is the wider #5048 residual for EVERY
        never-retracted Point, not this arm's to invent.
        """
        stamp = ", n.updatedAt = $now" if _usable_instant(now) else ""
        params: dict = {"id": pid}
        if stamp:
            params["now"] = now
        r = self.g.query(
            f"MATCH (n:Point {{id:$id}}) SET n.status = 'retracted'{stamp}, "
            f"{decay_clause('n')} RETURN count(n)",
            params=params,
        )
        return (r.result_set[0][0] if r.result_set else 0) or 0

    def _fold_point_restamp(self, ev: dict, *,
                            skip_updated_at: bool = False,
                            decay: bool = True,
                            stamp: bool = True,
                            edge: bool = True) -> int:
        """#3305: ONE home for the Point-lifecycle terminalizer fold body.

        Two replay engines must reach the same graph fold for a journaled
        Point terminalizer, and before this method the fold SELECTION lived in
        only one of them:

          * ``rebuild_all``'s pass-1b trailing sweep (``point_re_stamp_folds``)
            — the path ``tortoise rebuild`` uses, and ``recover_from_log``'s
            pre-wipe-sidecar route; and
          * ``apply()`` — the ONE-RECORD engine behind ``rebuild()``,
            ``recover_from_log()`` and the backup JSONL restore. It had NO
            branch for either type: the record fell through to the
            ``unrecognized event type`` warning, so an apply()-based replay
            re-materialized a superseded Point as ``status='live'`` (and an
            invalidated Point with no ``outdated`` flag) — a dead claim served
            as current, with the CORRECTS edge and the belief decay gone too.

        The SELECTION (survivor rule + supersede canonicalization + both
        belief anchors) is shared through ``plan_point_restamp_folds``; this
        method is the shared BODY it drives.

        ``stamp`` folds the status/flag/validity/CORRECTS half.
        ``PointSuperseded`` → ``_fold_point_superseded`` (status='superseded'
        + outdated + validTo/expiredAt + successor CORRECTS).
        ``PointInvalidated`` → ``_fold_point_invalidated`` (outdated +
        validTo/expiredAt + corrector CORRECTS; status deliberately stays
        live). The DIVERGENCE between the two is deliberate — see each fold.

        ``decay``: the terminalizers' BELIEF half (``_decay_point_belief``)
        folds at the event's OWN journal position (#2884 A3). ``apply()``
        replays chronologically, so it folds here (the default). The
        ``rebuild_all`` sweep passes ``decay=False``: pass-1b already applied
        the decay inline at the surviving event's seq, and re-applying it in
        the trailing sweep would clobber every later same-id belief writer.
        A record the plan rejects for the STAMP half can still decay (a bare
        same-id re-emit advances the recreate boundary but not the
        delete/recreate one), which is why the two halves are separate flags.

        ``skip_updated_at`` is the BOTH families' seq gate (see
        ``_fold_point_superseded`` / ``_fold_point_invalidated``): it suppresses
        only the ``updatedAt`` column when a later same-id inline writer exists,
        so a trailing sweep cannot clobber a newer revision's stamp. A
        chronological replay never needs it — a later same-id revision simply
        folds later.

        Returns the fold's matched-row count (the #2164/#2423 fold-miss
        signal). 0 for an inapplicable record: an empty id, an id the driver
        cannot take as a query parameter, a ``PointSuperseded`` with no
        ``new_id``, or a ``stamp=False`` call (the decay-only half). The id
        gate is ``_writable_id`` — the gate ``_decay_point_belief`` /
        ``_fold_confidence_changed`` already use — so a corrupt line degrades
        to a dropped fold instead of aborting a post-wipe recovery at
        parameter encode.
        """
        from tortoise.projection import _writable_id
        rid = ev.get("id")
        if not rid or not _writable_id(rid):
            return 0
        t = ev.get("type")
        if t == "PointSuperseded" and not ev.get("new_id"):
            # The fold's OWN applicability guard (``_fold_point_superseded``
            # returns 0 without ``new_id``) — and the belief decay must not
            # fire for an event the fold ignores: live never decayed for one.
            # Checked BEFORE the decay so the one-record default (``decay=True``)
            # cannot clobber the target's belief for an ignored event; the
            # plan already yields ``(decay=False, stamp=False)`` for it.
            return 0
        matched = 0
        if stamp:
            if t == "PointSuperseded":
                matched = self._fold_point_superseded(
                    ev, skip_updated_at=skip_updated_at, edge=edge)
            elif t == "PointInvalidated":
                matched = self._fold_point_invalidated(
                    ev, skip_updated_at=skip_updated_at, edge=edge)
            else:
                # Unreachable while ``_POINT_RESTAMP_EVENT_TYPES`` and these
                # arms agree (pinned by tests). Warn rather than no-op: a
                # vocabulary member with no arm must be audible, not a fresh
                # silent drop.
                logger.warning(
                    "_fold_point_restamp: no fold arm for %r — skipped", t)
                return 0
        if decay:
            self._decay_point_belief(rid)
        return matched

    def apply_journal_point_restamp(
            self, ev: dict, seq: int,
            plan: dict[int, tuple[bool, bool]], *,
            hard_deleted: dict) -> tuple[int, str, str] | None:
        """#3305: apply the shared whole-journal plan to ONE terminalizer.

        The apply()-based whole-journal engines (``rebuild(EventLog)``,
        ``consistency.recover_from_log``, ``backup.restore``) replay a journal
        record at a time, so they cannot call ``apply()``'s inline branch for
        these two types — that branch folds EVERY terminalizer, which is not
        ``rebuild_all``'s selection (see ``plan_point_restamp_folds``). They
        call this instead, with the record's journal ``seq`` and the plan, so
        both engines obey one selection.

        ``plan`` is keyed by the SAME ``enumerate`` seq the caller passes. The
        plan NEVER omits a terminalizer seq: an id it cannot write maps to
        ``(False, False)`` (both halves skipped, and the plan already logged the
        warning). A seq ABSENT from the plan therefore means the record is not a
        planned terminalizer at all — a non-terminalizer type, or an ``events``
        list the plan was not built from — and both halves are skipped here.
        The belief half folds here, at the record's own
        position — chronological, so it cannot clobber a later writer.

        A fold that matches NO Point is now RECORDED as a ``NonFoldedEvent``
        carrying the SHARED classifier's shape (``classify_terminalizer_miss``),
        so a WHOLE-JOURNAL caller running inside a ``collect_non_folded()``
        boundary (``rebuild``, ``recover_from_log``, ``backup.restore``) fails
        closed — ``NonFoldedEventsError`` — on exactly the journal
        ``rebuild_all`` refuses. R8 makes the fail-closed set ONE set, and #7719
        closed the gap where this arm was merely loud (#3299). The
        ``logger.warning`` is KEPT: a caller reached OUTSIDE that boundary —
        ``apply()``'s one-record live path, or a bare/test call — has no run to
        fail and still needs the audible line. Before #7719 the whole-journal
        apply() arm was quieter than both, so a ``rebuild(EventLog)`` replay of
        a terminalizer whose target was never created said nothing while
        ``rebuild_all`` refused.

        The record is NORMALIZED here (``self._norm``) because the fold body
        reads the flat ``id``/``new_id``, and ``_norm`` tolerates a nested
        payload (``{"type": ..., "point": {...}}``) — the plan normalizes when
        it decides and ``rebuild_all`` normalizes before folding, so an
        unnormalized call here would make the engines disagree on a
        supported-by-``_norm`` record (#325/#3722's raw-vs-normalized class).

        Returns the ``(seq, old_id, successor_id)`` CORRECTS endpoints when a
        STAMP was applied and the record names a NON-EMPTY writable successor
        for ITS OWN type — ``PointSuperseded``'s ``new_id`` /
        ``PointInvalidated``'s ``corrected_by``, each read ALONE, matching the
        fold dispatch and ``rebuild_all``'s sweep — else ``None``. So every
        returned element is a writable ``str`` and the callers'
        ``list[tuple[int, str, str]]`` buffers are accurate.
        The caller BUFFERS the triple and re-applies it after every creation —
        this pass is chronological, so the inline MERGE no-ops when the
        successor is created LATER in the journal, and only the trailing
        ``fold_deferred_corrects_edges`` sweep can resolve it (the same
        forward-reference treatment ``EntityLinked`` gets; ``rebuild_all``'s
        sweep already runs after its pass-1a hoist, which is why the two
        engines disagreed). The ``seq`` rides along because the sweep needs it
        for the hard-delete staleness rule.

        ``hard_deleted`` is the anchor-gated ``hard_deleted_pairs`` map, and it
        is keyword-only and REQUIRED: it is the only way this consumer can tell
        a genuine terminalizer miss from the named
        ``supersede-target-deleted`` exemption, and a defaulted value would
        re-open the fail-OPEN it closes.
        """
        if hard_deleted is None:
            raise TypeError(
                "apply_journal_point_restamp requires hard_deleted=...")
        ev = self._norm(ev)
        apply_decay, apply_stamp = plan.get(seq, (False, False))
        if not apply_decay and not apply_stamp:
            # Ineligible (a non-non-empty-writable id): the plan already warned.
            return None
        matched = self._fold_point_restamp(
            ev, decay=apply_decay, stamp=apply_stamp)
        rid = ev.get("id")
        if apply_stamp and matched == 0:
            # #7719: the fold could not resolve the target. The SHAPE is the
            # SHARED classifier's — the same value `rebuild_all`'s deferred
            # sweep and `check_consistency`'s reference fold record for this
            # journal — and it is now RECORDED, so a whole-journal run through
            # this consumer fails closed (R8) instead of passing on a merely
            # loud warning. KEEP the warning: the #3299 contract is that a
            # dropped fold must be AUDIBLE, and a caller reached outside a
            # `collect_non_folded` boundary (a bare or test call) still needs
            # the log line — the record fails the RUN, the warning names it.
            # `target_deleted` reads the anchor-gated map; `has_successor`
            # (`new_id`, NEVER `corrected_by`) is REQUIRED at this
            # PointSuperseded site — the classifier's False default would
            # mislabel a target-miss as the EXEMPT
            # `point-superseded-no-new-id`.
            # Function-local on purpose: `projection/__init__` imports this
            # module, so a module-top import of `_hard_deleted_any` would cycle.
            from tortoise.projection import _hard_deleted_any
            record_non_folded(
                classify_terminalizer_miss(
                    ev.get("type"),
                    has_successor=bool(ev.get("new_id")),
                    target_deleted=_hard_deleted_any(
                        hard_deleted, "Point", rid)),
                event_id=ev.get("event_id"), event_type=ev.get("type"),
                seq=seq, id=rid if isinstance(rid, str) else None,
                candidates=(ev.get("new_id"), ev.get("corrected_by")),
                detail="terminalizer fold matched no Point",
            )
            logger.warning(
                "apply_journal_point_restamp: %s fold matched no Point "
                "(event_id=%s id=%r new_id=%r) — the target was never "
                "created, or the record carries no successor",
                ev.get("type"), ev.get("event_id"), ev.get("id"),
                ev.get("new_id"))
        if not apply_stamp:
            return None
        from tortoise.projection import _writable_id
        # Scope the successor field to the NORMALIZED type, exactly as the fold
        # dispatch and ``rebuild_all``'s sweep do: ``_fold_point_superseded``
        # reads ``new_id`` ONLY and ``_fold_point_invalidated`` reads
        # ``corrected_by`` ONLY, so taking ``new_id or corrected_by`` would let
        # a record mint an edge its own fold arm never creates — a phantom
        # CORRECTS on the apply() engines that ``rebuild_all`` does not
        # produce (e.g. a ``PointSuperseded`` carrying only ``corrected_by``).
        successor = (ev.get("corrected_by")
                     if ev.get("type") == "PointInvalidated"
                     else ev.get("new_id"))
        # Gate the BUFFERED triple to non-empty writable strs so the declared
        # ``tuple[int, str, str]`` is the value actually handed back: the
        # successor is read raw from the record, and a truthy non-str / NUL /
        # lone-surrogate one would otherwise ride into the callers' buffers
        # typed as ``str``. Nothing is lost by skipping it — the inline fold
        # already refused the edge through ``_merge_corrects_edge`` with a
        # warning, and the sweep would refuse it again.
        if (not rid or not successor or not _writable_id(rid)
                or not _writable_id(successor)):
            return None
        return (seq, rid, successor)

    def _merge_corrects_edge(self, old_id: str, new_id: str) -> int:
        """#3305: the ONE home for a terminalizer fold's CORRECTS edge MERGE.

        Both lifecycle folds (``_fold_point_superseded``,
        ``_fold_point_invalidated``) and the deferred re-apply
        (``fold_deferred_corrects_edges``) merge the SAME edge, so the query
        lives here rather than in three copies.

        Best-effort by construction: the MERGE only binds when BOTH endpoints
        exist, so a missing successor no-ops the edge without failing the fold
        (the fold's status/validity stamp needs only the target). Returns 1
        when the edge was merged, 0 when an endpoint was absent.

        An endpoint that is empty or that ``_writable_id`` rejects (NUL / lone
        surrogate) is REFUSED here, with a warning, before it reaches the
        query: the plan's gate covers the terminalizer's target, not this
        successor, so a corrupt successor would otherwise reach parameter
        encode and raise — aborting a post-wipe replay at the trailing sweep
        (the same class the target gate exists to prevent). Returns 0 for it;
        nothing is merged.

        The edge names the SUCCESSOR, which a chronological replay may not
        have materialized yet — the caller is responsible for the trailing
        re-apply in that case (see ``fold_deferred_corrects_edges``).
        """
        from tortoise.projection import _writable_id
        if (not old_id or not new_id or not _writable_id(old_id)
                or not _writable_id(new_id)):
            logger.warning(
                "_merge_corrects_edge: refusing an unwritable endpoint "
                "(old_id=%r new_id=%r) — CORRECTS edge not merged",
                old_id, new_id)
            return 0
        # ``RETURN a.id`` is load-bearing: without it FalkorDB yields an empty
        # ``result_set`` even when the MERGE CREATES the edge, so the merged-1
        # contract below would always report 0 (a swallowed signal for any
        # caller that reports a dropped edge).
        result = self.g.query(
            "MATCH (a:Point {id:$new_id}), (b:Point {id:$old_id}) "
            "MERGE (a)-[:CORRECTS]->(b) RETURN a.id LIMIT 1",
            params={"new_id": new_id, "old_id": old_id},
        )
        return 1 if result.result_set else 0

    def fold_deferred_corrects_edges(self, edges, hard_delete_seqs=None) -> int:
        """#3305: re-apply a replayed terminalizer's CORRECTS edge AFTER every
        creation has applied.

        The SIBLING arm of the target-existence gate. ``plan_point_restamp_folds``
        gates each fold on the id it TERMINALIZES, but the fold's CORRECTS edge
        names the SUCCESSOR (``PointSuperseded.new_id`` /
        ``PointInvalidated.corrected_by``) — a second endpoint the record may
        name before it exists. On a chronological replay of
        ``[PointAdded a, PointSuperseded a→s2, PointAdded s2]`` the inline
        MERGE finds no ``s2`` and no-ops, so the apply()-based engines lost the
        edge while ``rebuild_all``'s trailing sweep (which runs after pass-1a
        hoists every creation) created it — the mirror of #3305's own symptom.
        The edge MERGE is idempotent and order-free, so the fix is the SAME
        forward-reference treatment ``fold_deferred_entity_links`` gives
        ``EntityLinked``: buffer the endpoints during the pass and re-apply
        them here. Deferring the flags instead would drag the invalidate
        family's ``updatedAt`` seq-gate along and risk a NEW divergence.

        ``edges`` is a sequence of ``(journal_seq, old_id, new_id)`` triples.

        ``hard_delete_seqs`` (``projection.journal_hard_delete_seqs``) is the
        SAME staleness rule ``fold_deferred_entity_links`` applies, and it is
        REQUIRED for parity rather than a nicety: a sweep that ran purely on
        node existence would re-merge an edge onto a successor that was
        hard-deleted (``EntityMutated op=delete`` / ``PointsMerged``) AFTER the
        terminalizer and later re-created under the same id — an edge live does
        not have, and one ``rebuild_all`` does not produce. Ids are reused
        routinely, so without the rule the deferred sweep RESURRECTS a deleted
        edge and puts the engines back into disagreement. An edge is skipped
        when EITHER endpoint was hard-deleted after this record's seq.

        Returns the number of edges actually merged.
        """
        from tortoise.projection import _hard_delete_suppresses

        hard_delete_seqs = hard_delete_seqs or {}
        applied = 0
        for seq, old_id, new_id in edges:
            if not old_id or not new_id:
                continue
            if (_hard_delete_suppresses(
                    hard_delete_seqs, old_id, "Point", seq)
                    or _hard_delete_suppresses(
                        hard_delete_seqs, new_id, "Point", seq)):
                # Stale: the endpoint was hard-deleted after this terminalizer
                # and its re-creation does not bring the edge back (live parity).
                continue
            applied += self._merge_corrects_edge(old_id, new_id)
        return applied

    def _fold_point_superseded(self, ev: dict,
                               skip_updated_at: bool = False,
                               edge: bool = True) -> int:
        """#2423: fold a PointSuperseded event into Point.status/validity +
        CORRECTS edge.

        Projection-owned cache of the event stream (same §11 role as the
        Object-side ``_fold_object_superseded``): a superseded Point is
        re-stamped status='superseded' + outdated=true + validTo/expiredAt
        (the E6 bi-temporal window END, replayed verbatim from the journaled
        payload — a JSONL wipe+rebuild must reproduce the ORIGINAL stamps,
        not rebuild time), and the CORRECTS edge (successor → superseded old)
        is re-merged.

        Idempotent (a replayed/duplicate event re-applies the same SET/MERGE);
        a chain A→B→C leaves A superseded-by-B and B superseded-by-C (each
        event folds its own target — live semantics, mirror Object).

        #2884 A3: the BELIEF-decay half of the live ``supersede_point`` SET is
        NOT here — it folds INLINE in pass-1b via ``_decay_point_belief`` (at
        the surviving event's own journal seq; see that method's docstring and
        the ``supersede_decay_seq`` pre-pass). This fold owns the status flag,
        the validity stamps and the CORRECTS edge only. Keeping ``decay_clause``
        here was the write≠read defect: this fold runs in the TRAILING sweep,
        after the whole pass-1b loop, so it clobbered every later inline belief
        writer (replay ended at 0.5 while live ended at the later writer's
        value). Mirrors ``_fold_point_invalidated``, whose decay also moved
        inline.

        Returns the MATCHED-ROW count (#2164 Task 2 additive fold-miss
        signal): 1 = the target Point was found and folded, 0 = no Point
        matched (missing Point / stale id) or the event lacks id+new_id. The
        SET…RETURN form yields a row only when the MATCH bound a node, so a
        fold-miss is distinguishable from a successful fold without a separate
        existence pre-query. The CORRECTS MERGE is best-effort (a missing
        successor no-ops the edge arm without failing the fold).
        """
        oid = ev.get("id")
        new_id = ev.get("new_id")
        if not oid or not new_id:
            return 0
        # The journaled payload carries the ORIGINAL supersede-time stamps
        # (valid_to = the successor's validFrom — contiguous window END;
        # expired_at = transaction-time expiry). Replay them verbatim — the
        # #2164-P4 drift class (rebuild-time vs original-time) applies here
        # too. updatedAt mirrors the live stamp block (supersede-time now,
        # journaled on the line via the ts fallback).
        valid_to = ev.get("valid_to")
        # #2350 (E-prec): the journaled per-end precision rides the SAME record.
        # ``None`` when the record predates the field — writing it clears the
        # property, which is the legacy node's own state, so a pre-#2350
        # journal replays byte-identically.
        valid_to_precision = ev.get("valid_to_precision")
        expired_at = _journal_instant(ev, "expired_at")
        updated_at = _journal_instant(ev)
        # ``skip_updated_at`` is the SAME seq-gate ``_fold_point_invalidated``
        # applies. Without it the trailing sweep is the id's LAST writer and
        # writes the journaled supersede ts, while the chronological apply()
        # arm leaves the LATER PointRevised's replay-now stamp (measured) — the
        # engines would disagree. status/outdated/validTo/expiredAt/CORRECTS
        # always fold; the gate suppresses ONLY the updatedAt column. (Live
        # ``update_point`` on a plain Point does not itself advance updatedAt;
        # the replay-now value is the separate, pre-existing replay-vs-live
        # class, not claimed here.) ``edge=False`` suppresses the CORRECTS arm
        # for a successor the journal hard-deleted after this record (see
        # ``fold_deferred_corrects_edges`` / the pass-1b sweep).
        if skip_updated_at:
            set_clause = ("SET n.status='superseded', n.outdated=true, "
                          "n.validTo=$vt, n.validToPrecision=$vtp, "
                          "n.expiredAt=$ea ")
            params = {"id": oid, "vt": valid_to, "vtp": valid_to_precision,
                      "ea": expired_at}
        else:
            set_clause = ("SET n.status='superseded', n.outdated=true, "
                          "n.validTo=$vt, n.validToPrecision=$vtp, "
                          "n.expiredAt=$ea, n.updatedAt=$ua ")
            params = {"id": oid, "vt": valid_to, "vtp": valid_to_precision,
                      "ea": expired_at,
                      "ua": updated_at}
        result = self.g.query(
            "MATCH (n:Point {id:$id}) " + set_clause + "RETURN n.id LIMIT 1",
            params=params,
        )
        if result.result_set and edge:
            self._merge_corrects_edge(oid, new_id)
        return len(result.result_set)

    def _decay_point_belief(self, oid) -> int:
        """#2884 A3: the BELIEF half of a PointInvalidated fold, applied INLINE.

        ``invalidate_point`` decays the claim to vacuity in the SAME live SET
        that raises the outdated flag (``decay_clause`` — crash-atomic). The
        replay fold must reproduce that decay at the EVENT'S OWN journal
        position, NOT in the trailing sweep: the sweep runs after the whole
        pass-1b loop, so applying a journaled decay there clobbers every
        LATER inline belief writer for the id — replay ended at
        ``confidence=0.5`` while live ended at the later writer's value
        (write != read). Inline application lets chronological order decide,
        exactly as it does live; the deferred ``_fold_point_invalidated``
        keeps only the outdated/stamp/CORRECTS half, which no belief writer
        touches.

        Returns the MATCHED-ROW count (0 = the id was never re-created, so
        live's decay hit no node either). The invalidate fold's own 0-row
        warning still fires from the sweep, so a miss is audible exactly once.
        """
        from tortoise.projection import _writable_id
        if not _writable_id(oid):
            return 0
        result = self.g.query(
            f"MATCH (n:Point {{id:$id}}) SET {decay_clause('n')} "
            "RETURN n.id LIMIT 1",
            params={"id": oid},
        )
        return len(result.result_set)

    def _fold_point_invalidated(self, ev: dict, skip_updated_at: bool = False,
                                edge: bool = True) -> int:
        """#2488: fold a PointInvalidated event into the outdated flag +
        validity stamps + CORRECTS edge (NO status write).

        Rebuild-time mirror of ``_fold_point_superseded`` (the sweep replays
        both families over the journal); the DIVERGENCE is deliberate — do
        NOT blind-copy the superseded fold:
          - superseded: status='superseded' (terminal) + successor ``new_id``
          - invalidated: outdated=true flag ONLY (status stays live — the
            point remains a normal live node, just excluded from EP/reads via
            the outdated flag), ``corrected_by`` is the corrector (not a
            status successor), and neither status nor validFrom is written.
        Live ``invalidate_point`` (sdk.py) writes exactly this SET + CORRECTS
        MERGE, so replaying the journaled stamps verbatim reproduces the live
        node (same #2164-P4 drift class as supersede: stamps come from the
        journaled payload ts — the ORIGINAL invalidate time — never rebuild
        time).

        #2884 A3: the BELIEF-decay half of the live SET is NOT here — it folds
        INLINE in pass-1b via ``_decay_point_belief`` (see its docstring for
        why the sweep would clobber a later belief writer). This fold owns the
        outdated flag, the validity stamps and the CORRECTS edge only.

        updatedAt is seq-gated, NOT clock-conditional: pass-1a's
        ``_upsert_point_props`` stamps every replayed node with rebuild-time
        updatedAt BEFORE the trailing sweep runs, and rebuild-now always
        postdates the journaled invalidate ts — a ``$ts >= n.updatedAt`` CASE
        could never fire (the ELSE arm would win every time, pinning rebuilt
        updatedAt to rebuild-now). The sweep fold is the LAST writer on the
        id, so it writes the journaled ts UNCONDITIONALLY (exact live parity,
        the superseded fold's precedent) UNLESS ``skip_updated_at=True`` — a
        LATER same-id PointRevised/PointPromoted (inline event, pass-1b) is a
        legitimate newer writer whose rebuild-now stamp must not be clobbered
        by the older invalidate ts. outdated/validTo/expiredAt/CORRECTS fold
        ALWAYS — the gate suppresses ONLY the updatedAt column.

        Idempotent (replayed/duplicate events re-apply the same SET/MERGE).
        Returns the MATCHED-ROW count (the #2164/#2423 additive fold-miss
        signal): 1 = the target Point was found and folded, 0 = no Point
        matched (missing Point / stale id). A missing/deleted ``corrected_by``
        endpoint no-ops ONLY the CORRECTS MERGE arm (best-effort, like the
        superseded fold's missing successor) without failing the fold or
        warning — parity with #2423. A raw producer omitting ``corrected_by``
        entirely still gets the outdated flag + stamps (needs only oid); only
        the edge arm is gated (code-review P2-2).
        """
        oid = ev.get("id")
        if not oid:
            return 0
        corrected_by = ev.get("corrected_by")
        # Journaled payload stamps (invalidate-time ts = the live SET clock,
        # replayed verbatim). validTo/expiredAt fall back like supersede;
        # updatedAt reads the ts key (the emit passes ts=now).
        valid_to = ev.get("valid_to")
        # #2350 (E-prec): the journaled per-end precision, replayed verbatim
        # (None on a pre-#2350 record — clearing a property the legacy node
        # never had, so the replay stays exact).
        valid_to_precision = ev.get("valid_to_precision")
        expired_at = _journal_instant(ev, "expired_at")
        updated_at = _journal_instant(ev)
        if skip_updated_at:
            # A later same-id PointRevised/PointPromoted already stamped
            # updatedAt (inline, pass-1b) — omit the column so this fold
            # cannot clobber the newer stamp with the older invalidate ts.
            set_clause = ("SET n.outdated=true, n.validTo=$vt, "
                          "n.validToPrecision=$vtp, n.expiredAt=$ea ")
            params = {"id": oid, "vt": valid_to, "vtp": valid_to_precision,
                      "ea": expired_at}
        else:
            # Unconditional updatedAt write: this sweep fold is the id's last
            # journal writer → exact live parity (supersede's precedent).
            set_clause = ("SET n.outdated=true, n.validTo=$vt, "
                          "n.validToPrecision=$vtp, "
                          "n.expiredAt=$ea, n.updatedAt=$ua ")
            params = {"id": oid, "vt": valid_to, "vtp": valid_to_precision,
                      "ea": expired_at,
                      "ua": updated_at}
        result = self.g.query(
            "MATCH (n:Point {id:$id}) " + set_clause + "RETURN n.id LIMIT 1",
            params=params,
        )
        if result.result_set and corrected_by and edge:
            # Best-effort edge arm: a missing/deleted corrected_by point
            # (never re-created, hard-deleted) silently no-ops the MERGE.
            # A RAW producer omitting corrected_by entirely still gets the
            # outdated flag + stamps (the #2488 fix needs only oid) — only
            # the CORRECTS arm is gated on it (P2-2, code-review). A
            # corrected_by created LATER in the journal is re-applied by the
            # trailing ``fold_deferred_corrects_edges`` sweep (#3305).
            self._merge_corrects_edge(oid, corrected_by)
        return len(result.result_set)

    def _fold_confidence_changed(self, ev: dict) -> int:
        """#2884 D3: fold a journaled EP/dream belief-state write-back.

        ``ConfidenceChanged`` is the durability record for the four properties
        the EP/dream write-backs mutate directly (``confidence``,
        ``posterior_alpha``, ``posterior_beta``, ``lastDreamedAt``). Before
        this fold it was listed in ``_NO_PROJECTION_FOLD`` as an "audit-only,
        no graph effect" no-op, so a JSONL wipe+rebuild silently discarded
        every posterior and reset the dream schedule.

        PRESENCE-CONDITIONAL: only the keys the record actually carries are
        applied. An EP flush journals ``confidence`` + both posteriors; a
        dream stamp journals ``confidence`` (+ ``lastDreamedAt`` when the run
        converged); the run-evidence pre-write journals the posteriors AS
        JSON null (the clear). A key absent from the record is never written —
        so a dream record cannot clobber the posteriors the flush recorded,
        and a record cannot invent a value its writer never set. A key present
        with null writes null (removes the property), mirroring the live SET.

        Returns the MATCHED-ROW count (the #2164/#2423 additive fold-miss
        signal): 0 = no such node matched (hard-deleted / never re-created) or
        the record carries no foldable value; 1 = the target epistemic node
        (:Point, :Subject, :Object or :Event — #7853) was found and folded on
        its single label; >1 when one id is carried by a node under several
        epistemic labels (each label's query returns at most one row), which is
        the live writers' multi-label sweep. Idempotent — a replayed/duplicate
        event re-applies the same SET.

        #7853: the target is NOT always a Point. The EP write-back journals a
        ``ConfidenceChanged`` for every node it writes, and the widening makes
        that any of the four epistemic labels — so a ``:Point``-only MATCH left
        a non-Point belief write permanently unreplayable (``rebuild`` /
        ``recover_from_log`` refused the journal with ``point-belief-miss``).
        The id is index-anchored by keeping ONE label per query and sweeping
        the four labels (``epistemic_label_queries``) — the same shape
        ``tortoise/ep.py`` uses for its id-anchored reads, and the rule stated
        at ``tortoise/live.py``: a label predicate over a bare ``MATCH (n)``
        has no per-(label, property) index anchor, so it full-scans EVERY
        ``ConfidenceChanged`` fold (and regresses the common ``:Point`` case
        that ``origin/main`` served from the ``Point(id)`` index).
        """
        oid = ev.get("id")
        # #2884 review P1: the SAME id gate the sibling folds use. A bare
        # ``isinstance(oid, str)`` admits a NUL / lone-surrogate id that
        # ``_writable_id`` rejects; such an id reaches ``params={"id": oid}``
        # and the driver raises at encode — during pass-1b recovery, AFTER
        # the graph was wiped. Lazy import mirrors the sibling folds
        # (``_fold_entity_linked_reason`` / ``_apply_annotator``) and avoids
        # the module cycle (``tortoise.projection`` imports this module).
        from tortoise.projection import _writable_id
        if not _writable_id(oid):
            return 0
        set_parts: list[str] = []
        params: dict = {"id": oid}
        for key in BELIEF_PROPS:
            if key not in ev:
                continue
            value = ev[key]
            # ONE value gate shared with the pure fold (``_apply_one``) so
            # the two dispatchers cannot disagree on a corrupt line (#330
            # parity). A non-finite / bool / string "posterior" persists
            # verbatim through the param parse and brick the next EP run's
            # ``float(...)`` read — drop it. ``None`` is valid: the clear.
            if not _belief_prop_value_ok(key, value):
                continue
            set_parts.append(f"n.{key} = ${key}")
            params[key] = value
        # #2884 A5: the boolean flag carried by the same record
        # (`assess_source`'s `outdated=true`). Its OWN strict gate — the
        # numeric gate would admit `1`/`0` for an int and re-derive a flag
        # type no live writer produces.
        for key in BELIEF_BOOL_PROPS:
            if key not in ev:
                continue
            value = ev[key]
            if not _belief_bool_value_ok(value):
                continue
            set_parts.append(f"n.{key} = ${key}")
            params[key] = value
        if not set_parts:
            return 0
        # #7853 P2 divergence: the LIVE writers (``TortoiseEP._write_node`` /
        # ``_flush_cache``, ``TortoiseSDK.compute_confidence``,
        # ``Dreamer._ep_run_batch``) all sweep ALL FOUR labels and never stop
        # at the first match, so TWO SEPARATE NODES sharing one ``id`` — a
        # ``:Point`` and a distinct ``:Object``, the shape the regression test
        # builds — are both written. The replay fold used to ``break`` after the
        # first non-empty result, so replay updated only the first label and
        # silently disagreed with live on exactly that pair. (A SINGLE node
        # carrying both labels is matched by each label's ``MATCH`` and written
        # identically either way, so it is not the shape that diverges.) The SET
        # is idempotent, so sweep them all and ACCUMULATE the count (never
        # overwrite with a later empty label's 0, which would manufacture a
        # false ``point-belief-miss``).
        matched = 0
        for _q in epistemic_label_queries(
                "MATCH (n:{label} {id:$id}) SET " + ", ".join(set_parts) +
                " RETURN n.id LIMIT 1"):
            r = self.g.query(_q, params=params)
            if r.result_set:
                matched += len(r.result_set)
        return matched

    def _fold_edge_message_changed(self, ev: dict) -> int:
        """#5380: fold a journaled EP edge-message write-back or invalidation.

        The EDGE half of ``_fold_confidence_changed``. Before this, the four
        edge-resident message slots (``msg_alpha``/``msg_beta``,
        ``back_msg_alpha``/``back_msg_beta``) were written by every EP run and
        journaled NOWHERE — so a JSONL wipe+rebuild discarded the entire
        edge-level belief state while the #2884 node half was restored, leaving
        a graph that was only half restored and a next EP run warm-starting
        from a different state than the graph it replaced.

        ``op`` discriminates the two legs (the ``EntityMutated`` precedent):

        * ``set`` — ONE edge's committed slot pair. ``src``/``tgt``/``rel``
          identify the edge (an edge has no node id of its own; the flat
          composite is the ``DirectEdgeCreated`` convention). Presence-
          conditional, like the node fold: only the keys the record carries
          are written, and ``None`` is the journaled CLEAR.

        * ``clear`` — the committed effect of one live
          ``invalidate_messages`` / ``invalidate_factor_messages`` call, as a
          BULK record. ``scope`` names the live query replayed here verbatim
          (``all`` / ``touching`` / ``operator-neighbourhood``), so a replay
          re-derives the same edge set from the graph the journal just built
          rather than from an enumerated id list the record would have to
          carry. Without this leg the ``set`` leg would RESURRECT seeds the
          live graph dropped on a prior/topology change (#1242's stale-seed
          class), i.e. the fix would trade a loss for a divergence.

        Returns the matched-edge count for ``set`` (0 = no such edge) and the
        dropped-edge count for ``clear``. A ``set`` miss is NOT a refusal: the
        edge was hard-deleted live (the ``set`` fold runs after every
        edge-creating pass), or — for the one-record ``apply()`` engine, which
        has no pass-2b and still does not fold ``DirectEdgeCreated`` — the
        plain direct edge was never re-created at all. Since #8499
        ``rebuild_all`` DOES replay ``DirectEdgeCreated``, so on that leg a
        miss is no longer explained by a missing creation path.

        #5380 R8: a miss is still a non-folded event and is RECORDED, never
        silently dropped. The ``set`` miss is recorded by its two callers
        (they own ``seq``); a ``clear`` the fold cannot map to a live query is
        recorded here, at the fall-through. Both shapes are EXEMPT
        (``edge-message-miss`` / ``edge-message-unreplayed``) with their
        degraded guarantees written down in ``nonfolded.EXEMPT_SHAPES``.
        """
        from tortoise.projection import _writable_id
        op = ev.get("op")
        if op == "set":
            src, tgt, rel = ev.get("src"), ev.get("tgt"), ev.get("rel")
            # The relation type is INTERPOLATED into the query (Cypher cannot
            # parameterize a relationship type), so it is gated to the closed
            # set the live writers use — a hand-edited record must not inject.
            if rel not in ("IMPL", "NAND"):
                return 0
            if not (_writable_id(src) and _writable_id(tgt)):
                return 0
            set_parts: list[str] = []
            params: dict = {"src": src, "tgt": tgt}
            for key in EDGE_MESSAGE_PROPS:
                if key not in ev:
                    continue
                value = ev[key]
                if not _edge_message_value_ok(value):
                    continue
                set_parts.append(f"r.{key} = ${key}")
                params[key] = value
            if not set_parts:
                return 0
            # Source label SWEEP, target widened — the same index-preserving
            # shape ``ep._write_back_message`` and ``_flush_cache`` use
            # (#7853/#7902): the source keeps its ``(label, id)`` anchor and
            # the target position takes the disjunction, so a non-Point edge
            # endpoint still folds.
            matched = 0
            for _q in epistemic_label_queries(
                    "MATCH (o:{label} {id:$src})-[r:" + rel + "]->(c) "
                    f"WHERE {epistemic_disjunction('c')} AND c.id = $tgt "
                    "SET " + ", ".join(set_parts) + " RETURN c.id"):
                r = self.g.query(_q, params=params)
                if r.result_set:
                    matched += len(r.result_set)
            return matched

        if op == "clear":
            scope = ev.get("scope")
            if scope == "all":
                rows = self.g.query(
                    "MATCH ()-[r:IMPL|NAND]->() "
                    "REMOVE r.msg_alpha, r.msg_beta, "
                    "       r.back_msg_alpha, r.back_msg_beta "
                    "RETURN count(r)").result_set
                return int(rows[0][0]) if rows else 0
            # #5380 (R8): an unrecognised ``scope``, or a known scope with no
            # usable ``point_ids``, falls through every arm below and would
            # otherwise return 0 INDISTINGUISHABLY from a legitimate empty
            # drop — a silent no-op that resurrects the seeds the live graph
            # dropped (#1242/#330). Recorded (and exempt) rather than refused:
            # a future producer's fourth scope must not make a recovery run
            # un-runnable. The `set` arm's callers record their own miss.
            if scope not in ("touching", "operator-neighbourhood"):
                record_non_folded(
                    SHAPE_EDGE_MESSAGE_UNREPLAYED,
                    event_id=ev.get("event_id"),
                    event_type="EdgeMessageChanged",
                    op=op, detail=f"unrecognised clear scope {scope!r}",
                )
                logger.warning(
                    "EdgeMessageChanged: unrecognised clear scope %r — the "
                    "invalidation was NOT replayed (#5380)", scope)
                return 0
            point_ids = ev.get("point_ids")
            ids = ([p for p in point_ids if _writable_id(p)]
                   if isinstance(point_ids, list) else [])
            if not ids:
                record_non_folded(
                    SHAPE_EDGE_MESSAGE_UNREPLAYED,
                    event_id=ev.get("event_id"),
                    event_type="EdgeMessageChanged",
                    op=op,
                    detail=(f"clear scope {scope!r} carries no usable "
                            "point_ids"),
                )
                logger.warning(
                    "EdgeMessageChanged: clear scope %r carries no usable "
                    "point_ids — the invalidation was NOT replayed (#5380)",
                    scope)
                return 0
            if scope == "touching":
                rows = self.g.query(
                    "MATCH (p)-[r:IMPL|NAND]-() "
                    f"WHERE {epistemic_disjunction('p')} AND p.id IN $ids "
                    "REMOVE r.msg_alpha, r.msg_beta, "
                    "       r.back_msg_alpha, r.back_msg_beta "
                    "RETURN count(DISTINCT r)",
                    params={"ids": ids}).result_set
                return int(rows[0][0]) if rows else 0
            if scope == "operator-neighbourhood":
                rows = self.g.query(
                    "MATCH (op:Point)-[conn:IMPL|NAND]->(p) "
                    f"WHERE {epistemic_disjunction('p')} AND p.id IN $ids "
                    "AND (op.is_operator = true OR op.op_type IS NOT NULL) "
                    "WITH DISTINCT op "
                    "MATCH (op)-[r:IMPL|NAND]->(x) "
                    f"WHERE {epistemic_disjunction('x')} "
                    "REMOVE r.msg_alpha, r.msg_beta, "
                    "       r.back_msg_alpha, r.back_msg_beta "
                    "RETURN count(DISTINCT r)",
                    params={"ids": ids}).result_set
                return int(rows[0][0]) if rows else 0
        return 0

    # ── Entity nodes ───────────────────────────────────────────────

    def _upsert_subject(self, ev: dict) -> None:
        """MERGE Subject by name (content-hash dedup), or by id once the name is shared."""
        sid = ev.get("id")
        name = ev.get("name", "")
        # #7369: `name` is the MERGE KEY (`MERGE (s:Subject {name:$name})`), and
        # a null/refused key aborts the replay. `sid` is only a property value
        # on the SINGLE-carrier path, so there it is left to the parameter
        # boundary; on the shared-name path it becomes the MERGE key and is
        # gated below (#8864 review P2). Function-local import — the
        # module-level cycle-avoidance pattern this file already uses.
        from tortoise.projection import _log_identity_skip, _writable_id
        # Split so the reporter names the identity that ACTUALLY failed: a
        # missing `sid` is an absent PROPERTY (pre-change silent, and
        # `_log_identity_skip` deliberately does not warn for an absent
        # identity), so logging `name` here would have claimed a writable name
        # was unwritable.
        if not sid:
            return
        if not _writable_id(name):
            _log_identity_skip("Subject", name, "name (MERGE key)")
            return
        # #8864: WHICH key identifies this record — see the long note at the
        # MERGE below. Computed here, before the side effects, because the
        # branch decides whether `sid` is a MERGE KEY (a merge key is never
        # nulled by the parameter boundary, so it must be gated by the FOLD).
        carriers = _name_holder_count(self.g, "Subject", name)
        name_is_shared = carriers > 1
        if name_is_shared and not _writable_id(sid):
            # ⛔ The boundary deliberately FORWARDS a MERGE key untouched (a
            # null key is its own abort: `Cannot merge node using null property
            # value`), so an unwritable `sid` would reach the engine and raise
            # `Failed to parse query parameter 'key' value` out of `apply()` /
            # a replay pass that carries no per-event `try/except` — i.e. AFTER
            # ``_wipe_all_nodes``. Pre-#8864 `$id` was only ever a property
            # VALUE there (nulled by the boundary, so `coalesce` kept the
            # stored id); promoting it to the key is what re-opens that class,
            # so the FOLD must skip-and-report exactly as it does for `name`.
            _log_identity_skip("Subject", sid, "id (MERGE key)")
            return
        # Compute embedding for Subject name (#7845)
        embedding = None
        try:
            from tortoise.embeddings import encode_for_store
            embedding = encode_for_store(
                name, self.required_embedding_dim)
        except Exception:
            pass
        # #1918: canonical id must win on MATCH too — parity with the #1155
        # Object fix (_upsert_object ON MATCH o.id=coalesce($id, o.id)). The
        # webhook name-stub path (_event_plain_merge) can mint a Subject stub
        # before this entity path's SubjectAdded lands (since #7936 under the
        # canonical `sub-<sha256>` id — a no-op here; a pre-fix or foreign
        # graph may still carry a random ulid);
        # without the ON MATCH id write, the MERGE below adopts the stub by
        # NAME and the canonical id never lands on any node — MATCH
        # (s:Subject {id:$sid}) wiring (participatesIn, aboutSubject) silently
        # matches nothing. coalesce($id, s.id) makes the incoming id win on
        # MATCH; this function early-returns when the caller sends no id, so
        # entities without ids never fire the clause. Accepted trade-off
        # (same as #1155 for Objects): a LATE random-ulid SubjectAdded from a
        # producer without a deterministic id (api.add_subject, which unlike
        # add_object has no id= override) can re-id a canonical node — blast
        # radius is id-based wiring only (about* edges match by name).
        # #4524: the embedded engine's ``vecf32`` overwrite hazard, same as
        # the Point seam above (#4457): writing a vector onto a property that
        # already holds one can be SILENTLY DISCARDED by falkordblite, leaving
        # the stale vector in place with no error. This write sits inside the
        # MERGE's ON CREATE/ON MATCH clauses, where the dialect will not accept
        # an interleaved REMOVE — so clear the property in a SEPARATE query
        # first (the shape tests/test_precision_leak_4028.py documents;
        # atomicity is not required). Emitted ONLY when a new embedding is being
        # written, so the ``ELSE s.embedding`` preserve branch is untouched and
        # a fresh MERGE has nothing to remove. The pattern carries the SAME key
        # the MERGE below uses (``merge_key``), or this misses its own node.
        #
        # #8864: WHICH key that is — `name` while it identifies exactly ONE
        # carrier (the #452 content-hash dedup and the stub adoption below both
        # depend on name-keying), and `id` once two or more carriers hold it.
        # A name held by N nodes makes `MERGE (s:Subject {name:$name})` match ALL
        # of them and the `ON MATCH` id write stamp ONE id onto EVERY one of
        # them: duplicate-id corruption, after which every id-keyed read folds
        # two distinct entities into one result set. The record's id IS the
        # identity (#3590 R1), so an ambiguous name never has to be resolved to
        # fold the record — it just stops being the key. (`carriers` is read
        # above, before the embedding work, because the branch also decides
        # whether `sid` needs the `_writable_id` gate.)
        #
        # ⚠️ KNOWN GAP, recorded deliberately (#8902 review P3, filed as #8919):
        # in the `id`-keyed branch the `ON MATCH` below writes the id and the
        # adoption markers but NOT `s.name` (`name` is an ON CREATE write), so a
        # registration can fold onto the node that already carries `sid` while
        # that node is named something else — `add_subject(name)` then reports an
        # id whose node is NOT named `name`. Narrower than the clobber this
        # replaces (no id is corrupted), and closing it means writing the name on
        # the id-keyed `ON MATCH` AND teaching the two read-side carrier models
        # the same name move (`journal_epistemic_existence`,
        # `journal_object_surviving_keys`; `_fold_journal_entities` already moves
        # the id with its merge key), or they diverge in the fail-CLOSED
        # direction — so it is its own change, not a rider on #8864.
        if name_is_shared:
            logger.warning(
                "projection: %d Subject carriers already hold the name %r — "
                "a shared name cannot serve as the MERGE key; folding by id "
                "%r instead, so one id is never stamped onto every same-name "
                "carrier (#8864)", carriers, name, sid)
        merge_key = "id" if name_is_shared else "name"
        merge_value = sid if name_is_shared else name
        clear_embedding(self.g, f"(s:Subject {{{merge_key}:$key}})", "s",
                        {"key": merge_value}, embedding)
        _set_emb = set_embedding_clause("s")
        self.g.query(
            f"MERGE (s:Subject {{{merge_key}:$key}}) "
            "ON CREATE SET s.id=$id, s.name=$name, s.subjectKind=$sk, s.createdAt=coalesce($ca, $now), "
            # #1423: status ON CREATE only — the Subject twin of the #1350
            # Object clause. Before this, `status` rode the extra-props lane
            # (`_SUBJECT_HANDLED` lacked it, #2295), which applies on MATCH as
            # well — so ANY SubjectAdded replayed over a superseded node reset
            # it to 'live' (measured), and a re-mention clobbered the live node
            # while replay kept the supersession: the #2193 live≠replay class.
            # With status projection-owned, this clause SEEDS a fresh/
            # re-created Subject and ON MATCH never touches it.
            "            s.status=coalesce($st, 'live'), "
            "            " + _set_emb + " "
            "ON MATCH SET s.id=coalesce($id, s.id), "
            # #7936: adoption CLEARS the event-stub discriminator the
            # first-registration probe excludes. `SET ... = NULL` REMOVES the
            # property (not `false`), so an adopted live node's property set
            # stays byte-identical to the one replay's ON CREATE builds (which
            # never writes `_stub`). A legacy random-ulid stub carries no
            # `_stub`, so this is a no-op there.
            "            s._stub=NULL, "
            "            s.subjectKind=coalesce($sk, s.subjectKind), "
            # #2295: ON MATCH createdAt ADOPTS only when absent
            # (coalesce(s.createdAt, $ca) — existing value wins): mirrors the
            # #2194 Object clause (_upsert_object) for the Subject stub-
            # adoption path. A name-stub minted by a raw/connector producer
            # (no createdAt) adopted by a journaled SDK create (or any
            # EventAPI add_subject mention) must carry the journaled/mention
            # createdAt so live == journal == replay (the #2164-P4 drift
            # class). Byte-identity is guaranteed only for createdAt-LESS
            # stubs — a createdAt-CARRYING EventAPI-first node keeps its own
            # value under existing-wins (accepted divergence, #2295 plan).
            "            s.createdAt=coalesce(s.createdAt, $ca), "
            "            " + _set_emb,
            params={"key": merge_value, "id": sid, "name": name,
                    "sk": ev.get("subject_kind", "other"),
                    "ca": ev.get("createdAt"), "now": _now_iso(),
                    "st": ev.get("status"),  # #1423: status ON CREATE only
                    "embedding": embedding},
        )
        # #228: persist arbitrary caller-supplied props. The anchor follows the
        # MERGE key (#8864): a name-keyed MATCH would write the caller's props
        # onto EVERY same-name carrier, which is the same over-reach as the id
        # clobber.
        self._persist_extra_props(
            "MATCH (n:Subject {id: $id})" if name_is_shared
            else "MATCH (n:Subject {name: $name})",
            {"id": sid} if name_is_shared else {"name": name},
            ev, self._SUBJECT_HANDLED,
        )

    def _upsert_object(self, ev: dict) -> None:
        """MERGE Object by name (content-hash dedup), or by id once the name is shared.

        Objects are encoded via the Source→references→Object chain —
        embedding from name provides direct vector search capability
        while the provenance chain traces back to source content (#7845).
        """
        oid = ev.get("id")
        name = ev.get("name", "")
        # #7369: `name` is the MERGE KEY (`MERGE (o:Object {name:$name})`).
        from tortoise.projection import _log_identity_skip, _writable_id
        # Split so the reporter names the identity that ACTUALLY failed (see
        # the sibling note in `_upsert_subject`).
        if not oid:
            return
        if not _writable_id(name):
            _log_identity_skip("Object", name, "name (MERGE key)")
            return
        # #8864: which key identifies this record — see the note at the MERGE
        # below. Computed here, before the side effects, because the branch
        # decides whether `oid` is a MERGE KEY (never nulled by the parameter
        # boundary, so the FOLD must gate it — see `_upsert_subject`).
        carriers = _name_holder_count(self.g, "Object", name)
        name_is_shared = carriers > 1
        if name_is_shared and not _writable_id(oid):
            _log_identity_skip("Object", oid, "id (MERGE key)")
            return
        title = ev.get("title")  # None default — coalesce needs NULL, not ""
        ok = ev.get("object_kind")  # None default — same issue
        # Compute embedding from name (#7845)
        embedding = None
        try:
            from tortoise.embeddings import encode_for_store
            embedding = encode_for_store(
                name, self.required_embedding_dim)
        except Exception:
            pass
        # #1155-P1: canonical id must win on MATCH too. The produces-edge
        # wiring in _event_plain_merge can mint a name-stub Object when a
        # poll/webhook event lands BEFORE this entity path's first
        # ObjectRegistered — since #7936 under the canonical `obj-<sha256>` id
        # (making this clause a no-op for the generic path), while a pre-fix or
        # foreign graph may still carry a random ulid. Without the ON MATCH id
        # write, the MERGE
        # below adopts the stub by NAME and the canonical id never lands on
        # any node — aboutSubject wiring (MATCH by o.id) silently matches
        # nothing. `coalesce($id, o.id)` is idempotent: $id is always the
        # same deterministic id for a given name across producers (this
        # function early-returns when the caller sends no id, so entities
        # without ids never fire the clause).
        # #4524: as for Subject above — the embedded engine can silently
        # discard a ``vecf32`` overwrite, and this embedding sits inside
        # ON CREATE/ON MATCH, so PRE-REMOVE it in a separate query. Emitted
        # only when a new vector is being written; key ``merge_key`` below — the
        # SAME key the MERGE uses, or this misses its own node.
        #
        # #8864: WHICH key that is — `name` while exactly ONE carrier holds it
        # (the #452 dedup and the stub adoption above depend on name-keying),
        # `id` once two or more do. A shared name makes
        # `MERGE (o:Object {name:$name})` match ALL the carriers and the
        # `ON MATCH` id write stamp ONE id onto EVERY one of them, i.e.
        # duplicate-id corruption: two distinct entities then answer every
        # id-keyed read as one. See `_upsert_subject` for the shared rationale.
        # (`carriers` is computed with the gates above so the branch can also
        # decide whether `oid` needs the `_writable_id` gate.)
        #
        # ⚠️ KNOWN GAP, recorded deliberately (#8902 review P3, filed as #8919):
        # in the `id`-keyed branch the `ON MATCH` below writes the id and the
        # adoption markers but NOT `o.name` (`name` is an ON CREATE write), so a
        # registration can fold onto the node that already carries `oid` while
        # that node is named something else — `add_object(name)` then reports an
        # id whose node is NOT named `name`, and a later name-keyed read resolves
        # the OTHER carriers. Same disposition and same reason as `_upsert_subject`
        # above: closing it means writing the name on the id-keyed `ON MATCH` AND
        # teaching the two read-side carrier models the same name move, which is
        # its own change (#8919), not a rider on #8864.
        if name_is_shared:
            logger.warning(
                "projection: %d Object carriers already hold the name %r — "
                "a shared name cannot serve as the MERGE key; folding by id "
                "%r instead, so one id is never stamped onto every same-name "
                "carrier (#8864)", carriers, name, oid)
        merge_key = "id" if name_is_shared else "name"
        merge_value = oid if name_is_shared else name
        clear_embedding(self.g, f"(o:Object {{{merge_key}:$key}})", "o",
                        {"key": merge_value}, embedding)
        _set_emb = set_embedding_clause("o")
        self.g.query(
            f"MERGE (o:Object {{{merge_key}:$key}}) "
            "ON CREATE SET o.id=$id, o.name=$name, o.objectKind=coalesce($ok, 'other'), o.createdAt=coalesce($ca, $now), o.title=coalesce($title, ''), "
            "            o.status=coalesce($st, 'live'), "
            "            " + _set_emb + " "
            "ON MATCH SET o.id=coalesce($id, o.id), "
            # #7936: adoption CLEARS the event-stub discriminator the
            # first-registration probe excludes (see _upsert_subject).
            "            o._stub=NULL, "
            "            o.objectKind=coalesce($ok, o.objectKind), "
            "            o.createdAt=coalesce(o.createdAt, $ca), "
            "            o.title=coalesce($title, o.title), "
            "            " + _set_emb,
            params={"key": merge_value, "id": oid, "name": name,
                    "ok": ok,
                    "st": ev.get("status"),  # #1350: status ON CREATE only —
                    # ON MATCH never touches it (the clobber guard: a
                    # re-mention cannot reset superseded→live).
                    # #2194: ON MATCH createdAt ADOPTS only when absent
                    # (coalesce(o.createdAt, $ca) — existing value wins, so a
                    # canonical re-mention's fresh $ca is a no-op; the clause
                    # exists for the stub-adoption path, where the SDK
                    # journals a synthesized createdAt that must land on the
                    # adopted live node so live == journal == replay — the
                    # #2164-P4 drift class). Shared with the EventAPI lane:
                    # api.add_object always sends a fresh createdAt →
                    # canonical re-mentions keep their existing value
                    # (idempotent); a createdAt-less stub adopted by an
                    # EventAPI mention now gets stamped (previously stayed
                    # absent) — benign, more consistent.
                    "ca": ev.get("createdAt"), "now": _now_iso(),
                    "title": title,
                    "embedding": embedding},
        )
        # #228: persist arbitrary caller-supplied props. The anchor follows the
        # MERGE key (#8864) — a name-keyed MATCH would write the caller's props
        # onto EVERY same-name carrier.
        self._persist_extra_props(
            "MATCH (n:Object {id: $id})" if name_is_shared
            else "MATCH (n:Object {name: $name})",
            {"id": oid} if name_is_shared else {"name": name},
            ev, self._OBJECT_HANDLED,
        )

    def _fold_object_superseded(self, ev: dict, *,
                               cas: bool = False) -> tuple:
        """#1350: fold an ObjectSuperseded event into Object.status.

        Projection-owned cache of the event stream (§11 'derived values may
        be CACHED'): a superseded Object is marked status='superseded' with
        the successor name + timestamp. A chain A→B→C leaves A superseded by
        B and B superseded by C (each event folds its own target).

        #2242 (CAS): the fold is CONDITIONAL when ``cas=True`` — the LIVE
        path (apply_supersessions, which passes cas=True explicitly) — via a
        single atomic statement computing ``live = (o.status IS NULL OR NOT
        (o.status IN $excluded))`` before a ``CASE WHEN live`` SET. The
        atomicity guarantee is SERVER-mode (single-statement serialization
        on FalkorDB server — metering.py doctrine); embedded self-host
        threads share one process and can still race the read-modify-write
        (documented out-of-scope in the plan). Returns (folded, matched):
        folded = rows actually flipped (were live), matched = rows bound —
        (0, 0) = no node (fold-miss), (0, N) = node exists but ALREADY
        terminal: under the live path that is the keep-first loser (a
        concurrent commit folded it between this record's gate probe and the
        fold); direct fold callers can also produce it as an idempotent
        re-fold. folded > 0 = claimed. First-fold stamps are kept on a
        terminal re-fold (never re-SET). The id→name fallback (#2164 ISSUE B
        legacy) fires ONLY on matched == 0 (genuine absence) — never when
        the id node exists but is terminal (a fallback could fold a dup-name
        carrier or re-claim a terminal target).

        ``cas=False`` (the DEFAULT — replay/rebuild surfaces: the pass-1b
        sweep, the apply dispatch, backup restore, consistency, migrate,
        CLI) is the legacy UNCONDITIONAL SET, byte-identical to pre-CAS —
        returning (matched, matched) so the sweep's folded == 0 warn
        condition ≡ today's matched == 0 (the legacy query keeps its
        ``RETURN o.id LIMIT 1`` — matched ≤ 1 per fold, exactly the pre-CAS
        count; a multi-node dup-name match folds every bound node but the
        LIMIT caps the reported row). Replay MUST stay blind last-wins:
        incarnation-reuse shapes (delete→recreate→re-supersede — two fold
        lines for one name belonging to DIFFERENT incarnations) resolve
        LAST-wins (the #2423 point-sweep precedent); a first-wins replay
        would regress currently-correct rebuilds (plan-review P1). The live
        path is the ONLY caller that opts in.

        #2164 (Task 2): the return is the tuple (folded, matched) — (0, 0)
        = no Object matched (missing Object / stale id / no id+name
        supplied), distinct from a successful fold without a separate
        existence pre-query.
        """
        oid = ev.get("id")
        name = ev.get("name")
        if not oid and not name:
            return (0, 0)
        # #5370: store the successor name VERBATIM — no 200-char cap. A cap
        # here is LOSSY: a successor named >200 chars is stored on its
        # Object in full (identity is the NAME — `_upsert_object` MERGEs on
        # it — and `create_entity` has never capped), but the fold would
        # record only its 200-char prefix — a value that names NO Object.
        # The ask path's name-keyed successor probe
        # (assembly._probe_visible_successors — MATCH (o:Object) WHERE
        # o.name IN $names) then matches nothing and the renderer reports
        # "no successor record found" for a successor that exists and is
        # live. The old comment claimed this cap MIRRORED a writer cap in
        # sdk.py `_connect_issue_objects`; that writer-side surface is a
        # separate, session-indexing-only concern (still capped on main;
        # #3574/#5314 removes it) and the fold must not truncate to it.
        supersedes_by = str(ev.get("supersedes_by") or "")
        # #2164 final-review P4: prefer the journaled event's ORIGINAL ts —
        # rebuild pass-1b replays the raw journaled event (sdk._emit_event
        # stamps ts on the JSONL line) — without this a JSONL wipe+rebuild
        # drifted supersededAt to rebuild time. Live callers (apply_super-
        # sessions' fold_ev carries no ts) fall back to now.
        superseded_at = _journal_instant(ev)
        # #2242: the exclusion tuple is imported from commit_ops at function
        # level (commit_ops has no module-level tortoise imports → no cycle;
        # single source of truth — the keep-first gate tuple).
        from tortoise.commit_ops import _RECALL_OBJECT_EXCLUDED_STATUS
        excluded = list(_RECALL_OBJECT_EXCLUDED_STATUS)

        def _classify(result) -> tuple:
            rows = result.result_set or []
            if cas:
                # RETURN live per row: folded = rows actually flipped
                folded = sum(1 for r in rows if r and r[0])
                return (folded, len(rows))
            # cas=False (blind legacy SET): every matched row folded
            return (len(rows), len(rows))

        if cas:
            # Engine-verified form (#2242 round-1 probe): FalkorDB rejects a
            # bare `NOT IN $param`; `NOT (o.status IN $excluded)` parses and
            # executes. `live` computed in a WITH BEFORE the CASE WHEN SET;
            # RETURN the per-row live flag.
            live = ("WITH o, (o.status IS NULL OR NOT "
                    "(o.status IN $excluded)) AS live "
                    "SET o.status = CASE WHEN live THEN 'superseded' "
                    "                   ELSE o.status END, "
                    "    o.supersededBy = CASE WHEN live THEN $sb "
                    "                          ELSE o.supersededBy END, "
                    "    o.supersededAt = CASE WHEN live THEN $sa "
                    "                          ELSE o.supersededAt END "
                    "RETURN live")
        else:
            live = ("SET o.status='superseded', o.supersededBy=$sb, "
                    "    o.supersededAt=$sa RETURN o.id LIMIT 1")
        common_params = {"sb": supersedes_by, "sa": superseded_at}
        if cas:
            common_params["excluded"] = excluded
        if oid:
            result = self.g.query(
                "MATCH (o:Object {id:$id}) " + live,
                params={"id": oid, **common_params})
            folded, matched = _classify(result)
            if folded == 0 and matched == 0 and name:
                # #2164 review (P2, ISSUE B): the id branch matched NOTHING
                # — the event's id is not the key the node carries (a legacy
                # id-less Object journaled with its SYNTHESIZED canonical
                # id). Fall back to the name branch — the same name-keyed
                # fold the live path uses. #2242: fallback ONLY on genuine
                # absence (matched == 0) — a present-but-terminal id node
                # (0, 1) is a CAS loss and must NOT fall back (a fallback
                # could fold a dup-name carrier or re-claim a terminal
                # target under a different name spelling).
                result = self.g.query(
                    "MATCH (o:Object {name:$name}) " + live,
                    params={"name": name, **common_params})
                folded, matched = _classify(result)
            return (folded, matched)
        result = self.g.query(
            "MATCH (o:Object {name:$name}) " + live,
            params={"name": name, **common_params})
        return _classify(result)

    def _upsert_document(self, ev: dict) -> None:
        """MERGE the document node as a ``:Source`` (D10, ONTOLOGY §4.4).

        A document is a ``:Source`` keyed ``url = <document id>``; there is no
        ``:Document`` graph label. Node TOPOLOGY is preserved: the document
        node stays distinct from the corpus ``#205`` Source (``url =
        source_url``), which keeps its ``references`` edge — the
        index-completeness gate's ``edge`` clause depends on it, so collapsing
        the two into one node would make the edge a dropped self-loop and every
        doc unit permanently incomplete. `content`, `doc_status` and
        `objectKind` are RETIRED and are never written (nor re-admissible via
        the open passthrough).

        #5082: ``sourceKind`` is written ``'document'`` on CREATE ONLY
        (``coalesce``), completing §4.6's REQUIRED field set for this node and
        agreeing with the sibling ``_mint_source_stub`` default. It is the
        TYPE axis; ``documentKind`` stays the GENRE axis (§4.4).

        #5422: ``contentHash`` is the node's **version anchor** on the
        extraction path (ONTOLOGY §4.6 — the hash identifies a version;
        identity is ``url``). It is written by the fixed clause above, gated
        so a hash-less write preserves it, and stays out of the open
        passthrough because BOTH spellings (``contentHash`` via
        ``_SOURCE_HANDLED``, ``content_hash`` via
        ``_DOC_PASSTHROUGH_HANDLED``) are denied. ``version`` is advanced in
        the same clause, per §4.6.
        """
        did = ev.get("id")
        # #7369: `did` is the Source MERGE key (`MERGE (s:Source {url:$id})`),
        # so an unwritable one aborts the replay AFTER the wipe.
        from tortoise.projection import _log_identity_skip, _writable_id
        if not _writable_id(did):
            _log_identity_skip("Document", did, "id (MERGE key)")
            return
        # Compute embedding from title+content for semantic search (#7845).
        # Epic #900 T3 cycle-19: the NEW index path carries a suppress flag in
        # the ev dict (suppress_embedding) — the doc path computes the call
        # UNCONDITIONALLY today (title is always present on new-path docs), an
        # undeclared prop + unbounded network call on every new-path write,
        # repair, and DocumentCreated REPLAY. The legacy branch is unchanged
        # (flag absent → compute as today, SC4).
        embedding = None
        if not ev.get("suppress_embedding"):
            doc_content = " ".join(filter(None, [
                ev.get("title", ""),
                ev.get("content", ""),
            ]))
            if doc_content.strip():
                try:
                    from tortoise.embeddings import encode_for_store
                    embedding = encode_for_store(
                        doc_content, self.required_embedding_dim)
                except Exception:
                    pass
        # #125 capture fields — use ev.get(field) with NO default so None →
        # Cypher null, letting coalesce fall through to existing on partial
        # updates. CRITICAL: "" is non-null in Cypher — coalesce("", d.f, ...)
        # returns "" and WIPES existing. Never use "" defaults in SET clauses.
        topics = ev.get("topics")
        summary = ev.get("summary")
        sid = ev.get("session_id")
        eid = ev.get("event_id")
        # #133: needs_extraction — explicit signal for --upgrade-all discovery.
        # coalesce-null sentinel: None default so partial updates preserve.
        nx = ev.get("needs_extraction")
        # #5422: the document's contentHash — the version anchor on the
        # extraction path. None (absent key, or a metadata-only re-emit) is
        # the preserve sentinel: the clause below must never clear an anchor
        # a prior write established, so a hash-less DocumentCreated is a
        # no-op on it (the same back-compat shape #5256's anchor reader and
        # the corpus index path already depend on). BOTH spellings are read:
        # the journal contract is snake_case (§4.3, matching every sibling
        # field on this event) while hand-written JSONL and older producers
        # may carry camelCase, and either must land on the node rather than
        # one of them silently no-opping the anchor.
        _ch_snake = ev.get("content_hash")
        ch = _ch_snake if _ch_snake is not None else ev.get("contentHash")
        # _searchText computed only when the event carries meaningful text
        has_text = bool(ev.get("title") or summary or topics)
        st = (_build_search_text(ev.get("title", ""), summary, topics)
              if has_text else None)
        # #167: sourcePath — coalesce-null sentinel (no "" default) so
        # partial updates preserve existing value
        sp = ev.get("source_path")
        # #4524: the embedded engine's ``vecf32`` overwrite hazard (#4457).
        # This one is a plain MERGE + SET list (no ON CREATE/ON MATCH), so the
        # conditional REMOVE rides in the SAME atomic query exactly as
        # _upsert_point_props does — emitted only when a new embedding is being
        # written, so the ``ELSE s.embedding`` preserve branch is untouched.
        # #5026/D10: the node is the document ``:Source`` (retired ``:Document``),
        # so the REMOVE and the SET list both bind ``s``.
        self.g.query(
            "MERGE (s:Source {url:$id}) " + clear_then_set_prefix("s") +
            "SET s.id=coalesce(s.id, $id), "
            # ⛔ `_searchText` is written BEFORE `s.title` (#8870): its
            # last-resort fallback READS `s.title`, and on the server engine a
            # same-clause read observes the value an earlier item just wrote.
            # With this item below the title write, a text-less re-emit fell
            # back to the title THAT SAME WRITE had just installed
            # (`'https://example.test/doc-title2'`) on the server engine while
            # the embedded engine fell back to the stored `'stub title'` — one
            # clause, two answers, decided by the backend. Above the title
            # write, both engines use the stored title.
            "    s._searchText=coalesce($st, s._searchText, s.title), "
            "    s.title=coalesce($title, s.title), "
            # #5082 (ONTOLOGY §4.6 + #5013 Q2): a document IS a `:Source`, and
            # §4.6 marks `sourceKind` REQUIRED (✅) on every `:Source` — it is
            # the TYPE axis, distinct from `documentKind`'s genre axis (§4.4:
            # "`documentKind` is not `sourceKind` … neither absorbs the
            # other"). This writer wrote none, so a document node carried
            # `sourceKind = NULL` — a declared-but-unwritten REQUIRED field —
            # while the sibling mint path `_mint_source_stub`
            # (`projection/edges.py`) already defaults any non-`session:` url
            # to exactly `'document'`: two writers of one node, disagreeing on
            # a REQUIRED field. `'document'` is the registered §5 vocabulary
            # term and `SOURCE_KIND_DEFAULTS` registers it NEUTRAL, so no
            # credibility inheritance moves (#398).
            # SET-ON-CREATE-ONLY via `coalesce`: #398's never-overwrite
            # contract — a node that already carries an authoritative
            # `sourceKind` (a stub, a connector Source, a session Source)
            # keeps it, and a partial re-write cannot change it.
            "    s.sourceKind=coalesce(s.sourceKind, 'document'), "
            # #5422: the document :Source's version anchor (ONTOLOGY §4.6 —
            # the hash names a version, identity is `url`). Gated so a write
            # that CARRIES no hash ($ch IS NULL) or an EMPTY one ($ch = '' —
            # the spelling `_mint_source_stub`/`_materialize_session_source`/
            # `hosted_api` use for "no hash") PRESERVES the stored value: a
            # metadata-only re-emit and the #900 index path's `_doc_write`
            # cannot un-anchor a document. An empty STORED hash (a
            # `_mint_source_stub` stub, or a pre-#5422 document node) is
            # COMPLETED by a real hash; a DIFFERING hash REPLACES it on the
            # SAME node (never a second `:Source`).
            # `version` advances with the hash exactly as §4.6 specifies
            # ("1 at creation, +1 on each content-hash change") and exactly
            # as `_upsert_source` does — so the pair is never half-written,
            # and because the gate can only move the hash to a value it does
            # not already hold, running BOTH folds over one url cannot
            # double-bump (the second sees stored == $ch).
            # ⛔ ORDER IS LOAD-BEARING (#8870, #7362 class). TWO ENGINES READ A
            # `SET` LIST DIFFERENTLY and only ONE order is correct on BOTH:
            #   * a SERVER engine (falkordb/falkordb:6.0.1 — the production
            #     `TORTOISE_DB_URI` backend) applies the items LEFT TO RIGHT,
            #     each item seeing what the EARLIER ones just wrote. Writing
            #     `contentHash` first made this clause's own
            #     `s.contentHash <> $ch` guard read the hash the clause had
            #     just installed — always false — so the ordinal froze at its
            #     first value (`['sha-v2', 1]`, measured).
            #   * the EMBEDDED engine (falkordblite, the carve-out lane) and
            #     falkordb-server v4.20.6 (the CI docker lane) evaluate every
            #     right-hand side against the PRE-write record, so the order is
            #     a no-op there — which is WHY a green CI lane was never
            #     evidence about production. MEASURED 2026-10-18, same
            #     statement + node: 6.0.1 gives `['sha2', 1]` (hash first) vs
            #     `['sha2', 2]` (version first); v4.20.6 and embedded give 2
            #     either way.
            # `version` therefore goes FIRST — that one order is correct on
            # both engines — and, generally, every SET item whose expression
            # READS `s.contentHash` must precede the item that WRITES it
            # (pinned order-only and engine-independently by
            # `tests/test_source_version_set_order_7362.py`). Pinned
            # behaviourally here by
            # `test_document_version_counter_advances_with_the_hash`.
            "    s.version=CASE "
            "        WHEN $ch IS NULL OR $ch = '' THEN coalesce(s.version, 1) "
            "        WHEN s.contentHash IS NULL OR s.contentHash = '' "
            "             OR s.contentHash <> $ch "
            "             THEN coalesce(s.version, 0) + 1 "
            "        ELSE coalesce(s.version, 1) END, "
            "    s.contentHash=CASE "
            "        WHEN $ch IS NULL OR $ch = '' THEN s.contentHash "
            "        WHEN s.contentHash IS NULL OR s.contentHash = '' "
            "             OR s.contentHash <> $ch THEN $ch "
            "        ELSE s.contentHash END, "
            # D10 B3 (adversarial): the three-argument coalesce gives a
            # document node a NON-NULL kind on CREATE. `$dk` is Cypher null
            # for an explicit `document_kind: null` (ingest's YAML `type:`
            # decodes to None; `EventAPI.add_document(document_kind=None)`; a
            # null field in a replayed JSONL line) AND for an omitted key, so
            # the old two-argument form left `documentKind` NULL on CREATE —
            # and the documents meter (`documentKind IS NOT NULL`) then read
            # 0, letting the document escape the cap. The trailing `''` is the
            # SAME non-null CREATE default the sibling clauses already use
            # (topics/summary/sessionId/eventId/needs_extraction); on a
            # re-write that OMITS the kind the middle term preserves the
            # stored value (#125 coalesce semantics).
            "    s.documentKind=coalesce($dk, s.documentKind, ''), "
            "    s.format=coalesce($fmt, s.format), "
            "    s.topics=coalesce($topics, s.topics, []), "
            "    s.summary=coalesce($summary, s.summary, ''), "
            "    s.sessionId=coalesce($sid, s.sessionId, ''), "
            "    s.eventId=coalesce($eid, s.eventId, ''), "
            "    s.needs_extraction=coalesce($nx, s.needs_extraction, false), "
            "    s.sourcePath=coalesce($sp, s.sourcePath), "
            "    s.ingestedAt=coalesce(s.ingestedAt, $now), "
            "    " + set_embedding_clause("s") + ", "
            "    s.updatedAt=$now "
            # D10 B6 (fourth door, review round 3): the retired fields must be
            # SCRUBBED on promotion, not merely refused on write. A PRE-RULING
            # node can still carry `content`/`objectKind` — the write paths no
            # longer produce one (ruling B on #3998 refuses them on EVERY
            # `:Source`), but legacy and raw-Cypher nodes do — and a document
            # creator MERGEs onto that SAME node by `url`, inheriting the
            # retired values live AND on replay. The deny-sets above (and in
            # `_upsert_source`) stop NEW writes; this REMOVE clears an
            # INHERITED one.
            # ⛔ GENERATED from the ONE registry (#5135) rather than spelled
            # again: the previous hand-written literal was a fourth copy of
            # the retirement, so a member added to `_DOC_RETIRED_KEYS` would
            # not have been scrubbed here.
            "REMOVE " + ", ".join(
                f"s.{k}" for k in sorted(self._DOC_RETIRED_KEYS)),
            params={"id": did, "title": ev.get("title", did),
                    # NO "" default here: a present null and an omitted key
                    # both stay Cypher null and fall to the third coalesce
                    # term ("") only on CREATE, so a partial re-write cannot
                    # wipe the stored kind (see the clause comment above).
                    "dk": ev.get("document_kind"),
                    "fmt": ev.get("format", "markdown"),
                    "topics": topics, "summary": summary, "sid": sid,
                    "eid": eid, "nx": nx, "st": st, "sp": sp,
                    "ch": ch,
                    "embedding": embedding,
                    "now": _now_iso()},
        )
        # #228: persist arbitrary caller-supplied props (before edge wiring
        # so that extra props land on the Source node regardless of edge
        # success). D10 retired keys (content/doc_status/objectKind) are denied
        # here — the fixed clause above already covers every other doc key.
        #
        # This call passes NO `allow_keys` DELIBERATELY: `test_create_document_
        # persists_arbitrary_props` (#228) pins the document route as a caller
        # passthrough. It is therefore NOT the same contract as `_upsert_source`
        # (which passes `_SOURCE_EXTRA_PROPS`), and the disagreement between the
        # two is resolved on the READ side, where #3998/D30 is authoritative:
        # every read path filters through `_SOURCE_NODE_PROP_NAMES` — including
        # the ROOT bag, which used to bypass it (#5196, P1).
        self._persist_extra_props(
            "MATCH (n:Source {url: $id})", {"id": did},
            ev,
            self._SOURCE_HANDLED
            | self._DOC_PASSTHROUGH_HANDLED
            # The retirement is the ONE registry, unioned HERE at call time
            # (#5135): `_DOC_PASSTHROUGH_HANDLED` deliberately carries no
            # copy, so a member added to the registry is denied without a
            # second edit.
            | self._DOC_RETIRED_KEYS,
        )
        # #205 — wire references edge (Source → document Source) for provenance
        # chain. D10: only when the corpus Source is DISTINCT from the document
        # node; the legacy fallback (no source_url) would otherwise be a
        # degenerate self-loop. The index path always passes the corpus
        # `source_url`, so the index-completeness gate's `edge` clause holds.
        # The legacy no-`source_url` path (every `tortoise/ingest.py` site)
        # therefore mints NO `references` hop — the document node IS the
        # Source — and `get_provenance_chain` serves that path from the
        # `extractedFrom` target itself (ONTOLOGY §3.4 layering truncated at
        # its first hop), so the chain still resolves.
        ref = ev.get("source_url")
        if ref and ref != did:
            # "Document" is the RELATION's spelling, not a node label: D10 retired
            # `:Document` and the writer remaps it onto `:Source` for identity, but it is
            # what marks this link a DERIVATION — and only a derivation link takes the
            # `sourceVersion` anchor (#5199, STORAGE-ARCHITECTURE.md 9.6). Switching this
            # to "Source" keeps the edge and silently drops the anchor.
            self.link_source_to_entity(ref, did, "Document")
        # #125 — aboutSubject edges when about_entities present (Task 1
        # self-contained: label-agnostic generalization lives in edges.py)
        about = ev.get("about_entities") or []
        if about:
            for ent in about:
                self._create_about_edges(did, ent)

    def _upsert_event(self, event: dict, *, guard: bool = False,
                      guard_source_file: str | None = None) -> "tuple[str, bool] | None":  # noqa: UP037
        """MERGE Event node with all ONTOLOGY §3.1 properties.

        Handles both nested ({type:EventRecorded, event:{eventId:...}}) and
        flat (eventId at top level) formats transparently.

        Auto-creates structural edges:
          - (Subject)-[:performs]->(Event) from event.subject
          - (Event)-[:produces]->(Object) from event.object
          - (Event)-[:uses]->(Object) from event.uses (list or single)
          - (Subject)-[:participatesIn]->(Event) from event.participants,
            falling back to event.subject when no explicit participants (#212)

        Epic #900 T3 extension (§4.2 meeting collision rule, cycle-12/13/
        17/18): when ``guard=True`` (the NEW meeting branch's call), the
        eventId write implements the dialect-verified THREE-STATEMENT
        construction — (1) MERGE candidate ON CREATE SET (creates if absent,
        no-op if present); (2) guarded classification ``MATCH ... WHERE
        e.eventKind='meeting' AND e.source_file = $sf`` — HIT = the eventId
        is OURS → in-place update; MISS = taken by a DIFFERENT-source
        meeting → GUARD-REJECTED → (3) suffix follow-up MERGE on
        ``<candidate>-<sha256(source_file)[:8]>`` (per-file-deterministic ⇒
        concurrent writers converge; [:12]/[:16] capped escalation on a
        suffixed-id collision — never a silent clobber). Returns
        ``(resolved_event_id, guard_rejected)``; the caller's wiring, journal
        emission and counter attribution bind to the RESOLVED id.

        Unparameterized calls (legacy ingest_corpus, rebuild replay) execute
        the plain MERGE path byte-identically (SC4) and return None.
        """
        inner = event.get("event", event)  # unwrap nested format
        eid = inner.get("id") or inner.get("eventId")
        # #7369: `eid` is the MERGE KEY (`MERGE (e:Event {eventId:$eid})`).
        from tortoise.projection import _log_identity_skip, _writable_id
        if not _writable_id(eid):
            _log_identity_skip("Event", eid, "eventId (MERGE key)")
            return
        # Embedding: the journaled EventRecorded payload carries the live
        # value (epic #900 cycle-18/19 — the sanctioned replay carrier for the
        # session heal); consume inner["embedding"] when present. Otherwise
        # compute from event content (legacy path, #7845) — EXCEPT meetings
        # (cycle-16: the meeting branch suppresses the unconditional
        # computation — no subject/object ⇒ a junk vector + an undeclared
        # network call on every meeting write/repair/replay; e.embedding stays
        # NULL).
        embedding = inner.get("embedding")
        if embedding is None and inner.get("eventKind") != "meeting":
            # Stored vecf32 (#244): the QUERY param must be cast (a plain-list
            # param is rejected there), and the STORED side goes through the
            # typed write seam so every server-owned vector has the shape every
            # other writer produces. Event has NO vector index — the only vector
            # index is on Point (#8429 is Point-scoped) — so the Event dense leg
            # is always the brute-force scan, where #8316 normalises a
            # right-width stored List at the READ boundary (a wrong width or a
            # non-numeric value still aborts the MATCH — #8430). Align with the
            # Point pattern.
            event_content = " ".join(filter(None, [
                inner.get("subject", ""),
                inner.get("eventKind", ""),
                inner.get("object", ""),
            ]))
            if event_content.strip():
                try:
                    from tortoise.embeddings import encode_for_store
                    embedding = encode_for_store(
                        event_content, self.required_embedding_dim)
                except Exception:
                    pass
        props = {
            "id": eid,  # ensure Event node has id for edge matching (#122)
            "eventKind": inner.get("eventKind", ""),
            "subject": inner.get("subject", ""),
            "object": inner.get("object", ""),
            "startedAt": inner.get("startedAt", ""),
            "endedAt": inner.get("endedAt"),
            "parentEvent": inner.get("parentEvent"),
            "participants": inner.get("participants", []),
            "classificationLevel": inner.get("classificationLevel", "internal"),
            "format": inner.get("format", "jsonl"),
        }
        if not guard:
            # ── PLAIN path — legacy/replay calls, byte-identical (SC4) ──
            self._event_plain_merge(eid, props, embedding, inner)
            # #388: connector Source materialization at the choke point (all
            # connector events flow here via proj.apply; the guarded meeting
            # path never carries connector metadata, so the gate is plain-path
            # scoped). Fire only on a registered connector sourceKind or an
            # explicit sourceUrl — never on bare `source`.
            self._materialize_connector_source(inner, eid)
            return None
        # ── MEETING GUARD path (cycle-12/13/17/18; the ONLY guard consumer) ──
        return self._event_guarded_merge(inner, eid, props, guard_source_file)

    def _fold_object_status(self, name: str | None, event_kind: str, *,
                            about_names=()) -> None:
        """#2729 — THE ONE Object-status fold dispatcher.

        Two halves, applied in this order:

        1. **Pack-declared folds** for this event kind. Each is guarded to the
           Object whose ``objectKind`` is the DECLARING pack's namespaced kind
           (``venture:tranche``) — dispatching on the namespaced kind is what
           stops two packs that share a bare kind name from colliding at the
           fold, and it is why the fold needs no per-event ``if``. The lookup
           and the guard are case-folded and form-widened (``venture:tranche``
           OR bare ``tranche``) because the write gate admits both spellings
           and the extraction commit strips the event namespace.
        2. **The built-in connector compatibility fold**
           (``status_folds.BUILTIN_CONNECTOR_FOLDS``), guarded to Objects whose
           kind declares NO lifecycle. A kind that declares ``statuses`` OWNS
           its state: ``github.issue.closed`` must not silently re-label a
           venture Object with the connector's ``completed`` (#2725 conflict
           check).

        ``name`` is the event's explicit ``object`` property — the event's
        declared target, whose absence is reported. ``about_names`` are the
        event's ``about_entities``: a LOOSE list of entities the event
        concerns, so a declared fold is applied to any of them that is an
        Object of the declaring kind, but a non-match is NOT a violation (the
        extraction pipeline emits events with ``about_entities`` and no
        ``object``; reporting per-non-match would spam every unrelated
        entity).

        The terminal-status guard is unchanged in both halves (#2164/#3309):
        a superseded / deprecated / archived / retracted Object is never
        resurrected into ``recall_state``'s default view.

        A declared fold whose explicit ``object`` target is absent is a real
        signal (a mis-named object in the event, or a kind that drifted) — it
        is emitted at the kind's #1934 enforcement level rather than
        swallowed.
        """
        index = _status_fold_index()
        from tortoise.status_folds import builtin_fold_status

        excluded = _terminal_object_statuses()
        declared = index.folds_for_event(event_kind)
        if declared:
            # The explicit target is REPORTED on a miss; an `about_entities`
            # target is folded silently (see the docstring). `name` wins when
            # it is also present in the list — one write, one report.
            targets: list[tuple[str, bool]] = []
            if name:
                targets.append((name, True))
            for about in about_names or ():
                if isinstance(about, str) and about and about != name:
                    targets.append((about, False))
            # #2729 review P2: the event-kind key is form-widened (namespaced
            # AND bare), so two packs that declare the same bare event name
            # share it. A declared target that MISSES because its kind has no
            # such Object is only a real signal when NO declared fold for this
            # event folded anything — otherwise it is a false
            # `status_fold_unapplied` for a correctly-folding write (the other
            # pack's kind simply is not this Object's kind).
            matched_any = False
            missed: list[tuple[str, str]] = []
            for target_name, report in targets:
                for target_kind, status in declared:
                    kinds = index.kind_forms_for(target_kind)
                    rows = self.g.query(
                        "MATCH (o:Object {name:$n}) "
                        "WHERE toLower(toStringOrNull(o.objectKind)) IN $kinds "
                        "  AND (o.status IS NULL OR NOT (o.status IN $excluded)) "
                        "SET o.status = $status "
                        "RETURN count(o) AS n",
                        params={"n": target_name, "kinds": kinds,
                                "status": status, "excluded": excluded},
                    ).result_set
                    if rows and rows[0][0]:
                        matched_any = True
                        continue
                    if not report:
                        continue
                    # No write: either the terminal guard refused (correct,
                    # silent) or no Object of the declaring kind exists (a
                    # fold violation).
                    present = self.g.query(
                        "MATCH (o:Object {name:$n}) "
                        "WHERE toLower(toStringOrNull(o.objectKind)) IN $kinds "
                        "RETURN count(o) AS n",
                        params={"n": target_name, "kinds": kinds},
                    ).result_set
                    if not (present and present[0][0]):
                        missed.append((target_kind, target_name))
            if not matched_any:
                for target_kind, target_name in missed:
                    _emit_fold_violation(target_kind, event_kind, target_name)
        builtin_status = builtin_fold_status(event_kind)
        if builtin_status is not None and name:
            # `toStringOrNull` (not `toLower` on the raw property) so a
            # payload-supplied non-string `objectKind` (an int, an array —
            # `create_entity(objectKind=...)`, `uses[].kind`) cannot raise
            # `Type mismatch` and abort the whole event write. Pre-#2729 the
            # built-in fold never read `objectKind` at all, so it tolerated
            # any value; the ownership guard must not regress that. A
            # non-string can never be a declared lifecycle kind, so the
            # coercion is exact: null/absent -> apply; non-string/null-able
            # -> `IS NULL` -> apply; a string -> the case-folded set test.
            self.g.query(
                "MATCH (o:Object {name:$n}) "
                "WHERE (o.objectKind IS NULL "
                "       OR toStringOrNull(o.objectKind) IS NULL "
                "       OR NOT (toLower(toStringOrNull(o.objectKind)) IN $declared)) "
                "  AND (o.status IS NULL OR NOT (o.status IN $excluded)) "
                "SET o.status=$status",
                params={"n": name, "status": builtin_status,
                        "declared": sorted(index.kind_forms),
                        "excluded": excluded},
            )

    def _event_plain_merge(self, eid: str, props: dict, embedding,
                           inner: dict) -> None:
        """The legacy single-statement Event MERGE (+ edges + extra props)."""
        # #4524: the ON MATCH embedding write below can be a SILENT no-op on
        # the embedded engine — a ``vecf32`` overwrite of an existing vector is
        # discarded (#4457). It sits inside ON CREATE/ON MATCH, so clear the
        # property in a separate query first. Emitted ONLY when a new embedding
        # is being written, leaving the ``ELSE e.embedding`` preserve branch
        # untouched.
        clear_embedding(self.g, "(e:Event {eventId: $eid})", "e", {"eid": eid}, embedding)
        self.g.query(
            "MERGE (e:Event {eventId: $eid}) "
            "ON CREATE SET e += $props, " + set_embedding_clause("e", preserve_on_null=False) + " "
            "ON MATCH SET e += $props, " + set_embedding_clause("e"),
            params={"eid": eid, "props": props, "embedding": embedding},
        )
        # ── Auto-create structural edges (#122) ──
        # Subject -[:performs]-> Event
        subj = inner.get("subject", "")
        # #7369: `subj`/`obj`/`use_name` are MERGE keys; function-local import
        # (no module-level binding — the cycle-avoidance pattern this file uses).
        from tortoise.projection import _log_identity_skip, _writable_id
        if not _writable_id(subj):
            _log_identity_skip("Event subject", subj, "subject (MERGE key)")
        else:
            # #7936: DETERMINISTIC id (the canonical one for this name), not a
            # random ulid — the graph must be a pure function of the journal.
            # ON CREATE ONLY is unchanged (#1155-P1): a late stub still cannot
            # clobber a canonical id, and a generic registration for the name
            # is now a no-op (same id) rather than a re-key.
            stub_id = _entity_name_id("Subject", subj)
            self.g.query(
                "MERGE (s:Subject {name:$name}) "
                "ON CREATE SET s.id=$id, s.subjectKind='other', s._stub=true",
                params={"name": subj, "id": stub_id},
            )
            self.g.query(
                "MATCH (s:Subject {name:$name}), (e:Event {eventId:$eid}) "
                "MERGE (s)-[:performs]->(e)",
                params={"name": subj, "eid": eid},
            )
        # Event -[:produces]-> Object (or Document when objectType='document', #125)
        obj = inner.get("object", "")
        object_type = inner.get("objectType", "")  # 'Document' | 'Object' | '' (legacy)
        # #7369: `obj` becomes a Source/Object MERGE key below.
        if not _writable_id(obj):
            _log_identity_skip("Event object", obj, "object (MERGE key)")
        elif object_type == "Document":
            # #329: the minted document id is tenant-influenced (event
            # props passthrough) — validate it so it can never be a host
            # path (the read side also fails closed via resolve_under_base).
            from tortoise.security import validate_document_id
            validate_document_id(str(obj))
            # D10 (ONTOLOGY §4.4): a document is a :Source keyed by url.
            # #5082 (second writer): the SAME document `:Source` shape the
            # `DocumentCreated` fold writes — §4.6 marks `sourceKind` REQUIRED
            # on every `:Source`, and #5013 Q2 keeps the TYPE axis here while
            # the genre rides `documentKind` (the two are different questions,
            # §4.4). Written on BOTH arms: create stamps 'document', match
            # FILLS a NULL — which is exactly the effect of
            # `_upsert_document`/`_execute_commit_writes`' plain-`SET` +
            # `coalesce` (#398's never-overwrite contract). An authoritative
            # kind (a `_mint_source_stub` stub, a connector/session Source) is
            # kept, and a document node minted by THIS writer before the fix
            # (or replayed from an old journal line) is repaired by the next
            # fold instead of staying NULL forever.
            self.g.query(
                "MERGE (s:Source {url:$id}) "
                "ON CREATE SET s.id=$id, s.title=$id, "
                "              s.sourceKind=coalesce(s.sourceKind, 'document'), "
                "              s.documentKind='transcript' "
                "ON MATCH SET s.sourceKind=coalesce(s.sourceKind, 'document')",
                params={"id": obj},
            )
            self.g.query(
                "MATCH (s:Source {url:$id}), (e:Event {eventId:$eid}) "
                "MERGE (e)-[:produces]->(s)",
                params={"id": obj, "eid": eid},
            )
        else:
            # #7936: DETERMINISTIC id (the canonical one for this name), not a
            # random ulid — a random stub id moved on every replay, so the
            # graph was not a pure function of the journal.
            stub_id = _entity_name_id("Object", obj)
            # #7936: `_stub=true` is the discriminator the first-registration
            # probe (sdk._create_entity) excludes — a marked stub is NOT a
            # registered node, so a later `create_entity` for this name still
            # journals its ObjectRegistered. Cleared on adoption by
            # `_upsert_object`'s ON MATCH.
            # #1155-P1: id is written ON CREATE ONLY, and THIS is the clause the
            # ruling protects. A poll/webhook event may arrive before the
            # entity path's ObjectRegistered; if this MERGE also wrote id on
            # MATCH, a late stub would CLOBBER the canonical id
            # (github-issue-{repo}-{n}) that _upsert_object set. The canonical
            # id wins via _upsert_object's ON MATCH `o.id=coalesce($id, o.id)`
            # — do not add an id write here. The ruling is about the CLAUSE, not
            # about the stub value: a deterministic stub IS the canonical id
            # for the generic path (a no-op there) and is still replaced by a
            # foreign/connector canonical id.
            self.g.query(
                "MERGE (o:Object {name:$name}) "
                "ON CREATE SET o.id=$id, o.objectKind='other', o._stub=true",
                params={"name": obj, "id": stub_id},
            )
            self.g.query(
                "MATCH (o:Object {name:$name}), (e:Event {eventId:$eid}) "
                "MERGE (e)-[:produces]->(o)",
                params={"name": obj, "eid": eid},
            )
        # #1350: work-item status fold — GitHub/Linear lifecycle events derive
        # the Object's status (decision 2a: in_progress/completed, completed
        # stays NON-terminal — history recallable, not presented as current).
        # Both producer vocabularies map (#1155 divergence).
        _wk = (inner.get("eventKind") or "")
        _obj_name = inner.get("object")
        # #1725: `github.issue.reopened` folds back to in_progress — a reopen
        # is a lifecycle Event whose ONLY projection is Object.status (the
        # indexer's decision table: lifecycle never mutates statement points).
        # M3-P1 guard (#2164): the fold MATCHes only NON-TERMINAL Objects —
        # a dual-tracked Object (connector work item conversationally
        # superseded via the capture fold) must NOT be silently resurrected
        # into recall_state's default view by a later connector lifecycle
        # event. Aligns with the #1350 clobber doctrine (a re-mention cannot
        # reset superseded→live). Objects NOT in a terminal status still fold
        # normally — that is `status IS NULL` (a bare stub created by this
        # MERGE), the canonical `'live'` a writer sets on create, or
        # `in_progress`/`completed`.
        #
        # #3309: the exclusion set is the CANONICAL Object terminal vocabulary
        # (``commit_ops.OBJECT_TERMINAL_STATUSES`` — superseded, deprecated,
        # archived, retracted), not the single hand-typed ``'superseded'``
        # literal this guard carried. The narrow literal contradicted the
        # guard's own stated purpose: it protects recall_state's DEFAULT VIEW,
        # and that view excludes all four (``tests/test_status_projection.py``
        # says so in `..._does_not_clobber_superseded`'s docstring). It was
        # also wrong on the merits — #2977 makes a ``retracted`` Object a
        # REMOVED Object (``assembly.py``: unresolvable, nothing to report), so
        # a connector event that resurrected it to in_progress/completed made a
        # removed Object visible again, while the same event correctly skipped
        # only ``superseded``.
        #
        # Imported at function level for the same reason the supersede fold
        # does it (#2242): no module-level cycle, and ONE source of truth —
        # never a restated literal.
        #
        # #2729: the fold vocabulary is no longer a hand-typed ``if/elif``.
        # Both halves — the built-in connector compatibility folds AND every
        # pack-declared per-kind fold — dispatch through `_fold_object_status`,
        # which compiles its index from `status_folds.py` (the ONE home). The
        # built-in behaviour is preserved VERBATIM for any Object whose kind
        # declares no lifecycle; a kind that declares one OWNS its state and
        # the connector fold no longer applies to it.
        #
        # The event's `object` prop is its declared target; `about_entities`
        # is the loose list the extraction pipeline emits instead (`sdk.py`
        # passes the payload's `about_entities` through, and the projection
        # folds a declared rule onto any of them that is an Object of the
        # declaring kind — without reporting a miss, which is scoped to the
        # explicit `object`).
        _about = inner.get("about_entities") or inner.get("aboutEntities") or []
        if isinstance(_about, str):
            _about = [_about]
        if _wk and (_obj_name or _about):
            self._fold_object_status(_obj_name, _wk, about_names=_about)
        # Event -[:uses]-> Object (input entities, #122; #125 structured dicts)
        uses = inner.get("uses")
        if uses:
            if isinstance(uses, str):
                uses = [uses]
            elif isinstance(uses, dict):
                uses = [uses]  # bare dict → normalize to list
            for use_item in uses:
                if isinstance(use_item, dict):
                    # #125 structured uses: {name, kind} → objectKind from kind
                    use_name = use_item.get("name", "")
                    use_kind = use_item.get("kind", "other")
                else:
                    # legacy string uses → default objectKind='other'
                    use_name = str(use_item)
                    use_kind = "other"
                # #7369: `use_name` is the Object MERGE key.
                if not _writable_id(use_name):
                    _log_identity_skip(
                        "Event use", use_name, "name (MERGE key)")
                else:
                    # #7936: deterministic canonical id, as for the subject and
                    # `object` stubs above — never a random ulid.
                    stub_id = _entity_name_id("Object", use_name)
                    # #7936: mark the `uses[]` stub with the same discriminator
                    # as the subject/object stubs — it too can precede a
                    # canonical `ObjectRegistered` for the name, and the probe
                    # must still journal that registration.
                    self.g.query(
                        "MERGE (o:Object {name:$name}) "
                        "ON CREATE SET o.id=$id, o.objectKind=$kind, o._stub=true "
                        "ON MATCH SET o.objectKind=$kind",
                        params={"name": use_name, "kind": use_kind, "id": stub_id},
                    )
                    self.g.query(
                        "MATCH (o:Object {name:$name}), (e:Event {eventId:$eid}) "
                        "MERGE (e)-[:uses]->(o)",
                        params={"name": use_name, "eid": eid},
                    )
        # ── Auto-create participatesIn edges (#212) ──
        # (Subject)-[:participatesIn]->(Event) for each participant id,
        # falling back to the event.subject field when no explicit participants.
        participants = inner.get("participants") or []
        if isinstance(participants, str):
            participants = [participants]
        if participants:
            for pid in participants:
                self.g.query(
                    "MATCH (s:Subject {id: $sid}), (e:Event {eventId: $eid}) "
                    "MERGE (s)-[:participatesIn]->(e)",
                    params={"sid": pid, "eid": eid},
                )
        elif subj:
            # Fallback: performer is an implicit participant
            self.g.query(
                "MATCH (s:Subject {name: $name}), (e:Event {eventId: $eid}) "
                "MERGE (s)-[:participatesIn]->(e)",
                params={"name": subj, "eid": eid},
            )

        # #228: persist arbitrary caller-supplied props (iterate inner dict
        # so nested {event:{...}} and flat formats both work)
        self._persist_extra_props(
            "MATCH (n:Event {eventId: $eid})", {"eid": eid},
            inner, self._EVENT_HANDLED,
        )

    def _materialize_connector_source(self, inner: dict, eid: str) -> None:
        """#388: materialize a Source node from connector event metadata.

        The single choke point through which 100% of connector events flow
        (github/linear/slack poll + webhook + entity paths all converge on
        ``proj.apply`` → ``_upsert_event``). Connectors already emit
        ``source``/``sourceKind`` (and now ``sourceUrl``) on every EventRecorded;
        this materializes ``(Source)-[:references]->(Event)`` so the
        ``(Point)-[:extractedFrom]->(Source)-[:references]->(Entity)``
        provenance chain (ONTOLOGY §3.4) resolves for connector entities.

        Gate: fires ONLY on a registered connector ``sourceKind`` or an
        explicit ``sourceUrl`` field — never on bare ``source``. The second
        choke-point producer ``mining.py`` emits EventRecorded with ``source``
        but NO sourceKind/sourceUrl (mining.py:417-440) and must stay excluded
        (spurious non-URL Source nodes otherwise).

        Merge semantics (idempotent, no churn on re-poll):
          - ``sourceKind`` is set ONLY on CREATE — a pre-existing Source's kind
            is authoritative (#398 never-overwrite contract; ON MATCH uses
            coalesce so an existing kind is never re-stamped, and a stub
            without kind still gets one);
          - no version bump on re-materialization (unlike ``_upsert_source``,
            which bumps on hash-diff — TypeGraph churn pitfall) — EXCEPT the
            #5214 content version below, which is gated on a REAL change and
            writes nothing when the item is unchanged.

        #5214 — the connector content version. Before this, the Source was
        created with ``contentHash = ''`` and never had one written, so the
        #5199 derivation anchor on ``(Source)-[:references]->(Event)`` was
        ``NULL`` for 100% of connector-derived Events: *"are these entities
        about the content we currently hold?"* was unanswerable on this path,
        and §9.6 recorded it as an open boundary. This is the SOURCE half of
        the pair §9.6's known limitation already assumes exists ("a connector
        re-poll that bumps the Source with no target rebuild"): the version
        advances on a content-changing re-fetch, the ON CREATE-only edge anchor
        deliberately does NOT, and the earlier derivation therefore reads
        ``stale`` — the conservative direction the model prefers (``stale !=
        wrong``, §4.6).

        The digest is `_connector_content_hash(inner)` — sha256 over the
        materialized connector payload, i.e. the content we actually read
        (see that function for why the payload, and not a per-kind registry, is
        the honest input).

        Gate — TWO conditions, both derived from the writer's own contracts:

        1. **A REAL per-item URL** (``_is_real_source_url``, the same
           discriminator the conf-60 direction guard uses). A container-level
           fallback key (``slack:{channel}``, ``linear:{team_key}``) names no
           item — every message in the channel shares that one Source — so
           stamping it with one message's digest would advance the version on
           every poll and mark every sibling derivation stale. Those Sources
           keep ``contentHash = ''`` and stay honestly unanchored, as before.
        2. **The node's OWN ``sourceKind`` is a registered connector kind** —
           i.e. this choke point materialized it. The choke point already
           treats an existing ``sourceKind`` as authoritative (#398: "an
           EXISTING Source's kind is authoritative", never re-stamped), so the
           writer that OWNS the kind owns this node — and it is the same
           ownership question for the version. Without the gate, a connector
           event whose URL names a node another writer owns (a document
           ``:Source``, a ``_link_source`` stub) would CLOBBER that writer's
           ``contentHash`` — silently re-versioning a document against a
           connector poll, and contradicting the sweep below ("the survivor's
           own [metadata] wins — inheritance only fills a gap").

        ⚠️ **The consequence of gate 2 is deliberate: a node whose kind belongs
        to another writer stays UNVERSIONED.** That node reads ``unknown`` (no
        recorded version), never ``current`` — the honest state, and the same
        one it had before #5214. The alternative — stamping it anyway — buys an
        anchor at the price of a false current on a node whose content this
        path does not own, which is the one direction §4.6 forbids. It also
        heals in place: a connector ``:Source`` that a pre-#5214 writer left at
        ``contentHash = ''`` is completed on the next poll, with no backfill.

        The write is gated in the query (``WHERE`` the stored hash differs), so
        an unchanged re-poll performs NO write at all — no property moves, no
        version bumps, no churn. On a real transition the pair moves together
        (``contentHash`` + the §4.6 ordinal ``version``), using the clause
        ``_upsert_document`` already uses for the document path: half-writing
        the pair is the #7362 bug class (*the hash advances, the counter does
        not*), so the two columns share one predicate.

        ``(Source)-[:references]->(Object {id})`` is wired ONLY when the event
        carries an explicit ``sourceObjectId`` (set exclusively by the github
        entity path). ``event.object`` is NEVER used as an Object key — on
        poll/webhook paths it is the entity TITLE string and
        ``_event_plain_merge`` already stubs ``Object {name: title}`` under its
        own name-keyed id; using it would wire references to the wrong stub.
        """
        sk = inner.get("sourceKind")
        source_url = inner.get("sourceUrl")
        if sk not in _CONNECTOR_SOURCE_KINDS and not source_url:
            return
        url = source_url or inner.get("source", "")
        if not url:
            return
        # S0b (#5012): connector events are 100% of this choke point — resolve
        # the inbound spelling to the ONE node its canonical identity names, so
        # the connector registration path cannot mint a second :Source either.
        key = resolve_source_key(self.g, url)
        canonical = normalize_source_url(key)
        # #7369: `key` is the Source MERGE key (`MERGE (s:Source {url: $url})`).
        from tortoise.projection import _log_identity_skip, _writable_id
        if not _writable_id(key):
            _log_identity_skip("Connector source", key, "url (MERGE key)")
            return
        # #388 conf-60 direction guard: NEVER let a fallback key displace a
        # real URL. chat_getPermalink returns None on ANY exception (rate
        # limits, transient outages), so a failed permalink poll emits the
        # container-level fallback (`slack:{channel}`) for an event that
        # already carries a real-permalink Source. Without this guard the
        # sweep below would DELETE the real-URL Source (and its accumulated
        # credibilityTier / sourcePath / title) + references edge — churning
        # provenance, and the next successful poll re-creating it →
        # oscillation. When the incoming url is a fallback key and the event
        # already has a real-URL Source, skip materialization entirely (the
        # fallback adds nothing; the real URL stays authoritative).
        if not _is_real_source_url(url):
            existing = self.g.query(
                "MATCH (s:Source)-[:references]->(e:Event {eventId: $eid}) "
                "RETURN s.url",
                params={"eid": eid},
            ).result_set
            if any(_is_real_source_url(row[0]) for row in existing):
                return
        self.g.query(
            "MERGE (s:Source {url: $url}) "
            "ON CREATE SET s.sourceKind = $sk, s.title = $url, "
            "    s.canonicalUrl = $cu, s.urlAliases = [$raw_url], "
            # #5214: the placeholder stays — the real content version, when one
            # exists, is written by the gated statement below (ONE home for the
            # hash+ordinal pair, so the create and the transition cannot
            # disagree).
            "    s.contentHash = '', s.ingestedAt = $now "
            "ON MATCH SET s.sourceKind = coalesce(s.sourceKind, $sk), "
            "    s.canonicalUrl = coalesce(s.canonicalUrl, $cu), "
            "    s.urlAliases = CASE WHEN $raw_url IN coalesce(s.urlAliases, []) "
            "        THEN s.urlAliases "
            "        ELSE coalesce(s.urlAliases, []) + [$raw_url] END",
            params={"url": key, "raw_url": url, "cu": canonical,
                    "sk": sk or "document", "now": _now_iso()},
        )
        # #5214: the connector content version — see the docstring. `ch` is
        # `''` for a container-level fallback key (no per-item identity → no
        # version), so those Sources never reach the statement at all.
        ch = _connector_content_hash(inner) if _is_real_source_url(url) else ""
        if ch:
            # Gate 2 of the docstring: the node must be one THIS writer
            # materialized (`sourceKind` a registered connector kind — the
            # choke point's own ownership contract, #398). `IN $ck` is the
            # NODE's kind, never the incoming `$sk`: a node another writer owns
            # keeps its own `contentHash` untouched.
            #
            # The rest of the predicate is repeated in the `WHERE` and mirrored
            # in the `SET`'s CASE pair: the WHERE is what makes an unchanged
            # re-poll a true no-op (nothing is written, nothing is dirtied), and
            # the CASE pair is what keeps the two columns moving together if the
            # query is ever run without it. The two readers do NOT agree on
            # WHEN they read, and that asymmetry is the whole defect:
            #   * the `WHERE` is evaluated on the MATCH — always the PRE-write
            #     `s.contentHash`, on every engine;
            #   * the `SET`'s CASE pair is NOT, on a server engine. falkordb 6.x
            #     (the production `TORTOISE_DB_URI` backend) applies the items
            #     LEFT TO RIGHT and each sees the earlier writes, so a
            #     `contentHash`-first list made this clause's own
            #     `s.contentHash <> $ch` guard read the hash it had just
            #     installed. The embedded engine (falkordblite) and
            #     falkordb-server v4.20.6 (the CI docker lane) read PRE-write
            #     values and are order-insensitive — which is why three green
            #     pins sat on a broken production path (#8870).
            #
            # ⛔ ORDER IS LOAD-BEARING: `s.version` is computed BEFORE
            # `s.contentHash` is written, so its guard still sees the STORED
            # hash — the one order that is correct on BOTH engines. MEASURED
            # 2026-10-18 on falkordb/falkordb:6.0.1: the `contentHash`-first
            # order left the ordinal at 1 through EVERY content change
            # (`['sha2', 1]`, expected 2); this order gives 2. Pinned
            # behaviourally by
            # `test_a_content_changing_repoll_advances_the_hash_and_the_ordinal`
            # (on the live engine, and `tests/test_8870_version_anchor_write_
            # order.py` pins it there explicitly) and order-only,
            # engine-independently, by
            # `tests/test_source_version_set_order_7362.py`.
            self.g.query(
                "MATCH (s:Source {url: $url}) "
                "WHERE s.sourceKind IN $ck "
                "  AND (s.contentHash IS NULL OR s.contentHash = '' "
                "       OR s.contentHash <> $ch) "
                "SET s.version=CASE "
                "        WHEN s.contentHash IS NULL OR s.contentHash = '' "
                "             OR s.contentHash <> $ch "
                "             THEN coalesce(s.version, 0) + 1 "
                "        ELSE coalesce(s.version, 1) END, "
                "    s.contentHash=CASE "
                "        WHEN s.contentHash IS NULL OR s.contentHash = '' "
                "             OR s.contentHash <> $ch THEN $ch "
                "        ELSE s.contentHash END",
                params={"url": key, "ch": ch,
                        "ck": list(_CONNECTOR_SOURCE_KINDS)},
            )
        # (Source)-[:references]->(Event) — always, when the event exists, and
        # anchored ON CREATE by the shared derivation writer (#5199) from the
        # Source's current `contentHash`. Since #5214 that hash EXISTS on this
        # path for a real per-item URL (written just above), so the anchor
        # lands here instead of reading NULL — and because the edge anchors ON
        # CREATE only while the Source's version may advance, a re-fetch whose
        # content differs leaves this edge reading `stale`. That is the
        # intended pair (§9.6), not a bug.
        self.link_source_to_event(key, eid)
        # #388 conf-62/conf-60: a fallback-key materialization (`slack:{channel}` /
        # `linear:{team_key}` / bare `source`) can predate the real URL (a
        # permalink becomes available later, or a later poll resolves the
        # container key). The new materialization wires a SECOND Source to the
        # same event → duplicated provenance entries. Clean up: drop every
        # `(Source)-[:references]->(Event)` edge whose url differs from the
        # now-authoritative url, and delete the Source node outright if that
        # edge was its ONLY relationship (deg=1 → orphaned). Shared Sources
        # (still referencing other events / extractedFrom by Points) keep the
        # node — only the superseded edge goes. Direction-guarded by conf-60
        # above: this sweep runs only when the incoming url is a real URL (or
        # no real-URL Source references the event), so a fallback key can
        # never supersede a real permalink Source. EP-neutral for an untiered
        # pair; a deleted node's accumulated tier is inherited (never
        # overwritten — the survivor's own value wins).
        #
        # The survivor is chosen by canonical identity, not by which node was
        # materialized first, so a node that is ABOUT to be deleted (deg = 1)
        # hands its accumulated tier/hash/title to the survivor rather than
        # losing it. The copy is scoped to the deletion branch: a superseded
        # node that survives (deg > 1, still shared) must not graft its
        # metadata onto an unrelated live node.
        self.g.query(
            "MATCH (old:Source)-[r:references]->(e:Event {eventId: $eid}) "
            "WHERE old.url <> $url "
            "MATCH (s:Source {url: $url}) "
            "WITH old, r, s, size([(old)-[x]-(y) | x]) AS deg "
            "DELETE r "
            "WITH old, s, deg "
            "WHERE deg = 1 "
            "SET s.credibilityTier = coalesce(s.credibilityTier, old.credibilityTier), "
            "    s.title = coalesce(s.title, old.title), "
            "    s.sourcePath = coalesce(s.sourcePath, old.sourcePath), "
            "    s.ingestedAt = coalesce(s.ingestedAt, old.ingestedAt), "
            "    s.contentHash = CASE WHEN coalesce(s.contentHash, '') = '' "
            "        THEN coalesce(old.contentHash, '') ELSE s.contentHash END "
            "DELETE old",
            params={"url": key, "eid": eid},
        )
        # (Source)-[:references]->(Object {id}) — only on explicit
        # sourceObjectId (github entity path; event.object is never an Object
        # key — see docstring).
        obj_id = inner.get("sourceObjectId")
        if obj_id:
            self.g.query(
                "MATCH (s:Source {url: $url}), (o:Object {id: $oid}) "
                "MERGE (s)-[:references]->(o)",
                params={"url": key, "oid": obj_id},
            )

    def _event_guarded_merge(self, inner: dict, candidate: str, props: dict,
                             sf: str | None) -> "tuple[str, bool]":  # noqa: UP037
        """The meeting-scoped source_file-aware guard (§4.2 cycle-12/13/17/18).

        THREE-statement construction (dialect-supported — no ``ON MATCH
        WHERE`` in the FalkorDB dialect): (1) MERGE candidate ON CREATE SET;
        (2) guarded classification WHERE eventKind='meeting' AND
        source_file=$sf — HIT ⇒ in-place update, MISS ⇒ guard-rejected;
        (3) suffix follow-up MERGE on the deterministic suffixed id
        (``sha256(source_file)[:8]``) with the same classification guard and a
        capped [:12]/[:16] escalation on suffixed-id collisions (E2E-12 grind).

        Statement 1 carries the FULL prop set — including ``source_file``, the
        guard's comparison property (cycle-18 threading pin): a concurrent
        same-file writer must HIT its own classification (source_file present)
        instead of suffix-forking on a mid-write window.
        """
        import hashlib
        # FULL prop set: the fixed keys + the extras (title/topics/
        # content_metadata/file_hash/source_file/eventStatus/…) — the same
        # surface the plain path persists via _persist_extra_props.
        full = dict(props)
        skip = self._META_KEYS | self._EVENT_HANDLED
        for k, v in inner.items():
            # #2962: this inline site builds its own extras, so it must apply
            # the SAME `_is_persistable_prop_value` filter `_persist_extra_props`
            # uses — a dict/nested-valued unknown prop would otherwise reach
            # the engine and crash `SET e += $props` (no crash, dropped).
            if k not in skip and v is not None and k not in full \
                    and _is_persistable_prop_value(v):
                full[k] = v
        # (1) MERGE candidate — creates if absent, no-op if present (ON CREATE
        # SET is the supported directive; the props NEVER land on a colliding
        # node).
        self.g.query(
            "MERGE (e:Event {eventId: $eid}) ON CREATE SET e += $props",
            params={"eid": candidate, "props": full},
        )
        # (2) guarded classification — is the candidate OURS?
        hit = self.g.query(
            "MATCH (e:Event {eventId: $eid}) "
            "WHERE e.eventKind = 'meeting' AND e.source_file = $sf "
            "RETURN e.id",
            params={"eid": candidate, "sf": sf},
        ).result_set
        if hit:
            self.g.query(
                "MATCH (e:Event {eventId: $eid}) SET e += $props",
                params={"eid": candidate, "props": full},
            )
            return (candidate, False)
        # (3) GUARD-REJECTED → suffix follow-up (per-file-deterministic ⇒
        # concurrent writers converge on the same id; first creates, second
        # no-ops).
        for width in (8, 12, 16):
            suffixed = f"{candidate}-{hashlib.sha256((sf or '').encode('utf-8')).hexdigest()[:width]}"
            held = self.g.query(
                "MATCH (e:Event {eventId: $eid}) RETURN e.source_file",
                params={"eid": suffixed},
            ).result_set
            if held and held[0][0] != sf:
                continue  # suffixed id taken by a DIFFERENT source — escalate
            hit2 = self.g.query(
                "MATCH (e:Event {eventId: $eid}) "
                "WHERE e.eventKind = 'meeting' AND e.source_file = $sf "
                "RETURN e.id",
                params={"eid": suffixed, "sf": sf},
            ).result_set
            if hit2:
                self.g.query(
                    "MATCH (e:Event {eventId: $eid}) SET e += $props",
                    params={"eid": suffixed, "props": full},
                )
            else:
                self.g.query(
                    "MERGE (e:Event {eventId: $eid}) ON CREATE SET e += $props",
                    params={"eid": suffixed, "props": full},
                )
            # extra-props tail re-targets to the RESOLVED id (never the
            # candidate when it exists as the colliding file's Event).
            self._persist_extra_props(
                "MATCH (n:Event {eventId: $eid})", {"eid": suffixed},
                inner, self._EVENT_HANDLED,
            )
            return (suffixed, True)
        # [:16] also taken — never a silent clobber; return the candidate with
        # the rejected flag so the caller records an errors[] warning (the
        # MERGE above is a no-op and no props were written anywhere).
        return (candidate, True)

    def _upsert_source(self, ev: dict, *, merge_run_id: str | None = None) -> "QueryResult | None":  # noqa: F821, UP037
        """MERGE Source node for layered provenance (Ontology v2.1).

        Source properties: url (permalink), sourceType, contentHash, title,
        ingestedAt, version, externalId. Creates stub if missing.

        Epic #900 T3 extensions (all inside the single statement — pin d,
        write-path containment):
          - the ON MATCH version bump is CONDITIONAL on a stored-hash
            difference (§5.1 pin b — ON MATCH bumps version/updatedAt/
            contentHash/title/_searchText ONLY when ``s.contentHash`` differs
            from the incoming ``$hash``; a stub Source with NULL contentHash
            is completed — the JOINT-E2E sweep's stub-handling);
          - ``s.sourcePath = coalesce($sp, s.sourcePath)`` (§4.1 — the
            sanctioned source_path route maps to camelCase on the node);
          - ``s.rawState``/``s.rawStateAt`` (#3998, D30): the ABSENT-RAW state
            — the third value on the source record, after identity (url) and
            version (contentHash). Written ON CREATE only when the caller
            carries one, and PRESERVED ON MATCH whenever the incoming
            ``$rawState`` is NULL — the contentHash anchor rule applied to
            availability, so a re-ingest cannot resurrect a raw that was
            deleted between the two writes. Deliberately INDEPENDENT of the
            hash-diff gate above: a state change with an unchanged body must
            still land;
          - ``s._searchText`` — coalesce ON CREATE, and on a hash-diff MERGE
            overwrite only when the incoming text is present (``coalesce($st,
            s._searchText)``, #3518: a text-less write must never NULL the
            value a prior capture/index write established); E2E-5 retitle
            refresh still overwrites.
          - ``s.__runId = $rid`` on the ON CREATE branch ONLY when
            ``merge_run_id`` is given — the creator's per-run token. The
            embedded FalkorDBLite reports ``Nodes created: 1`` for BOTH of two
            concurrent same-key MERGEs (server-side parallel-executor quirk),
            so ``nodes_created`` is NOT a race-safe creator discriminator on
            the embedded backend: the index path detects the CREATE by
            re-reading ``__runId`` (== its own token ⇒ IT created) and removes
            it immediately. Non-index callers (legacy create_source paths)
            never pass a run id → the clause is omitted entirely (no prop).

        Returns the QueryResult (the caller uses ``nodes_created`` for the
        counter-authority outcome); ``proj.apply`` threads it for
        ``create_source``'s index-path consumers. #5024 P1-A: the result's
        sole row carries ``previousProps`` — the node's properties as the
        statement found them (``null`` for a create), captured inside the
        write so the SDK's record decision cannot use a stale snapshot.
        """
        sid = ev.get("id")
        url = ev.get("url", "")
        # #7369: the Source MERGE key is a RESOLVED key, but a truthy yet
        # unwritable `url`/`sid` would still become it (`$id = sid or key`),
        # and the engine refuses such a key. Require at least one WRITABLE
        # identity, then make sure the truthy winner is not the unwritable one.
        from tortoise.projection import _log_identity_skip, _writable_id
        if not _writable_id(url) and not _writable_id(sid):
            _log_identity_skip("Source", url or sid, "url/id")
            return None
        raw_key = url or sid
        if not _writable_id(raw_key):
            raw_key = sid if _writable_id(sid) else url
        # ── S0a/S0b (#5012): canonical source identity ──
        # S0a runs FIRST and is mechanical (no model, no graph).  S0b then
        # resolves the inbound spelling to the ONE node its canonical identity
        # names (adopting a pre-canonical node on the way).  The MERGE key
        # STAYS ``url`` so every existing by-url read/mutation site keeps
        # working; ``canonicalUrl``/``urlAliases`` are the new identity props.
        # Identity is a canonicalised URL — NEVER an embedding (STORAGE §9.4).
        # The resolver is shared with the stub/link writers (edges.py) so a
        # URL variant cannot mint a second ``:Source`` on ANY write path.
        key = resolve_source_key(self.g, raw_key)
        # #7369 review r8: `resolve_source_key` returns the STORED ``s.url``
        # read back from the graph, and ``url`` is this statement's MERGE key
        # (``MERGE (s:Source {url: $url})``), so ``_journal_safe_params``
        # deliberately forwards it — an unwritable resolved key would abort the
        # replay AFTER the wipe. The three sibling source writers guard the
        # resolved key exactly here (``edges.py:129`` ``_mint_source_stub``,
        # ``edges.py:777`` ``link_source_to_entity``, ``entities.py``
        # ``_materialize_connector_source``); this site is the fourth.
        if not _writable_id(key):
            _log_identity_skip("Source", key, "url (resolved MERGE key)")
            return None
        canonical = normalize_source_url(raw_key)
        search_text = ev.get("_searchText") or ev.get("title")
        run_clause = ", s.__runId = $rid" if merge_run_id is not None else ""
        # ── #5024 P1-A: the pre-write state, captured ATOMICALLY ───────────
        # `create_source`'s record DECISION needs the state the write found,
        # not a snapshot read before it: the ON MATCH bump below is gated on
        # the STORED hash, so a concurrent writer between a separate pre-read
        # and this MERGE made the decision compare against a hash the write
        # never saw (measured: a h-1->h-0 transition suppressed as a no-op
        # while the live node moved to h-0 — fail-OPEN toward suppression).
        # `OPTIONAL MATCH` + `WITH` run BEFORE the `MERGE` in the SAME
        # statement, so `_prev_props` IS the committed pre-write state and no
        # other statement can interleave (null when the node did not exist),
        # and the `RETURN` hands it back on the write's own QueryResult. The
        # written node is untouched by this prefix — the ON CREATE/ON MATCH
        # clauses are byte-identical to before.
        r = self.g.query(
            "OPTIONAL MATCH (_prev:Source {url: $url}) "
            "WITH _prev, properties(_prev) AS _prev_props "
            "MERGE (s:Source {url: $url}) "
            # Lane L5 (#5097): the three CREATOR-ONLY defaults below (`id`,
            # `version`, `externalId`) are suppressed for a record that
            # COMPLETES a stub instead of creating a Source (`$stub_completion`,
            # set only by `sdk._materialize_session_source`). Its live node was
            # created by the M2 `_link_source` stub, which writes NONE of the
            # three — so an ON CREATE arm that wrote them would make a replay
            # GAIN `id`/`version=1`/`externalId=''` the live node never had, and
            # (worse) restart a version ordinal for a writer that does not
            # advance one. `NULL` is the removal/no-op value, so the property
            # simply stays absent, exactly as live has it. Every other record
            # is byte-identical to before (`$stub_completion` is false).
            "ON CREATE SET s.id = CASE WHEN $stub_completion THEN NULL "
            "                        ELSE coalesce($id, $url) END, "
            "              s.canonicalUrl = $cu, "
            "              s.urlAliases = [$raw_url], "
            "              s.sourceKind = $sk, "
            "              s.contentHash = coalesce($hash, ''), "
            # `coalesce($title, $url)`, not `$title`: a record that carries no
            # title (every producer but the capture's — `create_source` defaults
            # it to the key, and the capture passes an explicit null) creates the
            # node with the SAME title the `_link_source` stub writes (`title=url`)
            # rather than none. Without it a `rebuild_all` — which folds this
            # record BEFORE pass 2 mints the stub — left the session `:Source`
            # with no title at all, i.e. a property live HAD and the recovery
            # path lost (#5097 review P1).
            "              s.title = coalesce($title, $url), "
            # #5024: the RECORDED ingest instant, not the replay's clock. The
            # payload has carried `ingestedAt` since #398, but the fold set it
            # from `_now_iso()` at REPLAY time, so `derived = replay(journal)`
            # was FALSE for the field (measured: live ...56.751580 vs rebuilt
            # ...57.047168 on the same 12-instance run). `coalesce` keeps a
            # legacy/foreign record without the key working exactly as before.
            "              s.ingestedAt = coalesce($ingestedAt, $now), "
            # Lane L5 (#5097): ON CREATE wrote NO `updatedAt` while the ON MATCH
            # arm gates it on the hash — so a record that created the node (the
            # `rebuild_all` order) rebuilt the session `:Source` with no update
            # instant where live had one. `coalesce($updatedAt, s.updatedAt)` is
            # a no-op for every record that carries none (so no producer gains a
            # replay-clock stamp it never wrote) and reproduces the recorded one
            # when it is there.
            "              s.updatedAt = coalesce($updatedAt, s.updatedAt), "
            # ── #3644 (§4.6 window half) ────────────────────────────────
            # The CURRENT version's valid-time start. Written from the
            # RECORDED instant (`$vf`, minted once by `create_source` and
            # carried on the record) — never `coalesce($vf, $now)`, which would
            # stamp the REPLAY clock on a record that carries no window (every
            # legacy journal line, and every stub-targeted write). A legacy
            # record therefore replays to a source with NO `validFrom`: an
            # ABSENT start is an open/unbounded start (§4.1/#3654), which is
            # both the honest value and byte-identical to what the pre-change
            # live write produced. There is no ON CREATE `validTo`: the current
            # version's window is OPEN while it is current, and Cypher cannot
            # write a NULL property anyway — absent IS the open end.
            "              s.validFrom = $vf, "
            "              s.version = CASE WHEN $stub_completion THEN NULL "
            "                               ELSE 1 END, "
            "              s.externalId = CASE WHEN $stub_completion THEN NULL "
            "                                  ELSE $ext END, "
            "              s.format = coalesce($fmt, s.format), "
            "              s.sourcePath = coalesce($sp, s.sourcePath), "
            # #3998: the absent-raw state.  Written only when the caller
            # supplies one — a null leaves the property UNWRITTEN, so "nothing
            # recorded" stays the read-side default (present) and no source
            # gains a noise property it never needed.
            "              s.rawState = $rawState, "
            "              s.rawStateAt = $rawStateAt, "
            "              s._searchText = $st" + run_clause + " "
            # JOINT-E2E (epic #900 #1032): when the caller carries NO
            # contentHash ($hash IS NULL — the bundle ingest source-item
            # path; the INDEX path owns contentHash), the ON MATCH preserves
            # the stored hash/version/title — the ingest must never clobber
            # an index-created Source's contentHash to '' or bump its version
            # (the joint convergence contract: ONE Source, original
            # contentHash/references survive, version unchanged). Stubs
            # (NULL stored hash) are still COMPLETED by a real $hash, and the
            # hash-diff bump is unchanged for callers that DO carry a hash.
            # ⛔ THE GUARDED COLUMN IS WRITTEN LAST (#8870/#7362). On a SERVER
            # engine (falkordb 6.x — the production `TORTOISE_DB_URI` backend)
            # the `SET` items of one clause apply LEFT TO RIGHT and each item
            # observes the values the earlier items just wrote; the EMBEDDED
            # engine (falkordblite) and falkordb-server v4.20.6 (the CI docker
            # lane) read PRE-write values instead. Every item below tests
            # `s.contentHash` against `$hash` to decide "is this a NEW
            # version?", so the hash must not be installed until ALL of them
            # have read it — otherwise each comparison reads the hash this same
            # clause just wrote, sees "unchanged", and `title`, `version`,
            # `updatedAt`, `validFrom` and `_searchText` are silently skipped on
            # the only engine that can see it: the #7362 class ("the hash
            # advances, the counter does not") on the very writer §4.6's
            # `stale`/`current` anchor reads. MEASURED 2026-10-18 on
            # falkordb/falkordb:6.0.1, h1→h2: `contentHash`-first left
            # `version=1, title='t1'`; last gives `version=2, title='T2'` — a
            # content-changing repoll with the hash moving `bf64f552…` →
            # `01c54ed4…` and `version` stuck at 1. Placed LAST rather than
            # swapped forward so the next guarded field added here cannot
            # reintroduce the defect. Pinned order-only, engine-independently,
            # by `tests/test_source_version_set_order_7362.py`; behaviourally by
            # `tests/test_8870_version_anchor_write_order.py` and
            # `test_a_rebuild_reproduces_the_source_field_for_field`
            # (`version == 6` on the server engine).
            "ON MATCH SET s.title = CASE WHEN $hash IS NULL THEN s.title "
            "                   WHEN s.contentHash IS NULL OR s.contentHash <> $hash "
            "                   THEN coalesce($title, s.title) ELSE s.title END, "
            # Lane L5 (#5097 review P1): the bump is GATED ON THE SAME
            # DISCRIMINATOR as the ON CREATE suppression above. The capture
            # writer's live raw MERGE never touches `s.version`, so a replayed
            # capture record must not advance the ordinal either — on EITHER
            # arm. Ungated, a session `:Source` that already carried a version
            # (e.g. one a caller created with `create_source` before capturing)
            # sat at `version = 1` live while `rebuild()`/`rebuild_all()` folded
            # this record's differing `contentHash` through the hash-diff arm to
            # `version = 2` — the same ordinal this PR's ON CREATE suppression
            # exists to avoid restarting, just one arm over. With the ordinary
            # M2 stub the bump is invisible (`null + 1` stays absent), which is
            # why only a whole-bag comparison over a versioned node sees it.
            "           s.version = CASE WHEN $stub_completion THEN s.version "
            "                    WHEN $hash IS NULL THEN s.version "
            "                    WHEN s.contentHash IS NULL OR s.contentHash <> $hash "
            "                    THEN s.version + 1 ELSE s.version END, "
            # #5024: same rule as `ingestedAt` above — the recorded instant,
            # never the replay clock (measured divergence: ...57.016262 live vs
            # ...57.079810 rebuilt). A re-materialisation now emits
            # `SourceVersioned` (which carries its own `updatedAt`), so this
            # branch is the LEGACY path for journals written before that record
            # existed; keeping `coalesce($updatedAt, $now)` makes both records
            # replay to the same node.
            #
            # Lane L5 (#5097 review, head f16638e6b): the SAME-HASH (last) arm
            # must REPRODUCE the instant the record carries when the node has
            # none — `_materialize_session_source`'s live clause is
            # `ELSE coalesce(s.updatedAt, $now)`, i.e. it STAMPS a node that
            # lacked one, while `ELSE s.updatedAt` kept the absence. The two
            # part company on the one state where both conditions hold: the M2
            # stub (`contentHash=''`, no `updatedAt`) receiving a capture whose
            # derived transcript is empty (`content_hash` is `''` then) —
            # measured live `updatedAt='2099-…'` / rebuilt `None`, a recovery
            # path LOSING a value the write stored. `coalesce(s.updatedAt,
            # $updatedAt)` is a no-op for a record that carries none (legacy /
            # foreign journals) and for a node already stamped (the first stamp
            # still wins), so no other producer moves.
            "           s.updatedAt = CASE WHEN $hash IS NULL THEN s.updatedAt "
            "                     WHEN s.contentHash IS NULL OR s.contentHash <> $hash "
            "                     THEN coalesce($updatedAt, $now) "
            "                     ELSE coalesce(s.updatedAt, $updatedAt) END, "
            "           s.sourcePath = coalesce($sp, s.sourcePath), "
            # #3644: the window start moves ONLY on the same hash-diff gate as
            # `version`/`updatedAt` — a create/stub-completion (stored hash
            # NULL) or a real version transition. A same-hash write leaves it
            # untouched (there is no new version to open a window for), and a
            # hash-less write (`$hash IS NULL`, the preserve-everything arm)
            # never moves it either. On the gate, the value is `$vf` ITSELF —
            # deliberately NOT `coalesce($vf, s.validFrom)`: a transition whose
            # record carries no window start (a foreign or pre-#3644 journal
            # line) leaves the new version's start UNKNOWN, and `$vf = null`
            # clears the property (verified: the ON CREATE arm relies on the
            # same `SET … = $vf` removal, pinned by the legacy-replay test).
            # Inheriting the PRIOR version's start would be a false claim about
            # the current version — the old window's open is not the new one's,
            # and the prior close is exactly what the record could not state.
            # #3644 review P1: `$vf` is STRICTLY monotone by construction —
            # `create_source` mints it through `_mint_source_window_instant`,
            # which clamps above the prior mint, so this unconditional SET
            # cannot move the window backward or leave it zero-width under a
            # stalled/stepped wall clock.
            "           s.validFrom = CASE WHEN $hash IS NULL THEN s.validFrom "
            "                       WHEN s.contentHash IS NULL OR s.contentHash <> $hash "
            "                       THEN $vf "
            "                       ELSE s.validFrom END, "
            "           s.canonicalUrl = coalesce(s.canonicalUrl, $cu), "
            "           s.urlAliases = CASE WHEN $raw_url IN coalesce(s.urlAliases, []) "
            "               THEN coalesce(s.urlAliases, []) "
            "               ELSE coalesce(s.urlAliases, []) + [$raw_url] END, "
            # #3998: availability is PRESERVED by an upsert that carries no
            # state — the same rule the contentHash anchor follows above.  The
            # gate is the incoming STATE, not the hash diff: re-fetching the
            # same version must not resurrect a raw that was deleted between
            # the two writes.  A caller that DOES carry a state moves it
            # (``raw_state='present'`` is the "the raw came back" write).
            "           s.rawState = CASE WHEN $rawState IS NULL THEN s.rawState "
            "                        ELSE $rawState END, "
            # #5196 review round 3, P2: a SAME-state write must not move the
            # stamp. `_source_payload_is_noop` ignores `rawStateAt` (it is
            # minted per call), so bumping it here mutated the live node while
            # journalling nothing — `rebuild_all` then reverted the stamp and
            # live != replay, the divergence this lane exists to remove.
            # `_prev_props` is the PRE-WRITE state captured atomically above,
            # so this comparison cannot be perturbed by the `s.rawState`
            # assignment in the previous line.
            "           s.rawStateAt = CASE WHEN $rawState IS NULL THEN s.rawStateAt "
            "                         WHEN $rawState = _prev_props.rawState "
            "                              THEN s.rawStateAt "
            "                         ELSE coalesce($rawStateAt, $now) END, "
            "           s._searchText = CASE WHEN $hash IS NULL THEN s._searchText "
            "                        WHEN s.contentHash IS NULL OR s.contentHash <> $hash "
            # #3518: coalesce — a hash-diff write that carries NO searchable
            # text ($st IS NULL, the commit path's session Source) must not
            # ERASE the text a prior capture/index write established. A write
            # that does carry text (the indexer's retitle) still overwrites,
            # so the #900 T3 cycle-4 retitle-refresh semantics are unchanged.
            "                        THEN coalesce($st, s._searchText) "
            "                        ELSE s._searchText END, "
            # D10 (§4.4/§9.5 Q3): `format` is a Source property now; a caller
            # supplying it must land on the node, overwriting an existing value
            # (parity with the old open-passthrough write it replaces).
            "           s.format = coalesce($fmt, s.format), "
            # ⛔ LAST, and it must stay last — see the ORDER note at the head of
            # this ON MATCH clause (#8870).
            "           s.contentHash = CASE WHEN $hash IS NULL THEN s.contentHash "
            "                             WHEN s.contentHash IS NULL OR s.contentHash <> $hash "
            "                             THEN $hash ELSE s.contentHash END "
            # #5024 P1-A: the atomic pre-write capture above rides the write's
            # own result. `null` (a create) is a REAL value here, not a
            # swallowed failure — the caller no longer has a pre-read that can
            # fail and be mistaken for "no node".
            "RETURN _prev_props AS previousProps",
            params={
                "url": key, "id": sid if _writable_id(sid) else key,
                "cu": canonical,
                "raw_url": raw_key,
                "sk": ev.get("sourceKind", "document"),
                "hash": ev.get("contentHash"),
                "title": ev.get("title", key),
                "now": _now_iso(),
                # #5024: the recorded instants (None for a legacy record —
                # `coalesce` falls back to `$now`, the pre-#5024 behaviour).
                "ingestedAt": ev.get("ingestedAt"),
                # #3644: the record's own window start (None for a legacy
                # record — the clause above then leaves the node's window
                # alone rather than synthesizing one from `$now`).
                "vf": ev.get("validFrom"),
                "updatedAt": ev.get("updatedAt"),
                # Lane L5 (#5097): the capture COMPLETION discriminator (see
                # `_SOURCE_CAPTURE_REPLAY_PROPS`). Read as a parameter, never
                # persisted — `_META_KEYS` keeps it off the node.
                "stub_completion": bool(ev.get("_stub_completion")),
                "ext": ev.get("externalId", ""),
                "fmt": ev.get("format"),
                "sp": ev.get("source_path"),
                "st": search_text,
                # #3998: None (not a default string) so the ON CREATE clause
                # LEAVES THE PROPERTY UNWRITTEN and the ON MATCH clause
                # PRESERVES — see both clauses above.
                "rawState": ev.get("rawState"),
                "rawStateAt": ev.get("rawStateAt"),
                **({"rid": merge_run_id} if merge_run_id is not None else {}),
            },
        )
        # #228: persist arbitrary caller-supplied props — CLOSED to the
        # declared :Source surface since #3998 (see _SOURCE_EXTRA_PROPS). An
        # undeclared key is DENIED rather than written, so no spelling of a raw
        # payload can reach the node and no journal replay can restore one.
        # The DENIAL is logged (#2795's drift warning, applied to this layer):
        # silently dropping a prop a caller sent is how a declaration rots into
        # a mystery, and the return value exists precisely to feed this.
        # Lane L5 (#5097): a record that declares itself a CAPTURE COMPLETION
        # also admits `_SOURCE_CAPTURE_REPLAY_PROPS` — the capture writer's own
        # spellings, which are NOT caller-settable (see that set). Every other
        # record keeps the plain #3998 allowlist, so `create_source(**props)`
        # still DENIES them.
        _allow = self._SOURCE_EXTRA_PROPS
        if ev.get("_stub_completion"):
            _allow = _allow | self._SOURCE_CAPTURE_REPLAY_PROPS
        _persisted = self._persist_extra_props(
            "MATCH (n:Source {url: $url})", {"url": key},
            ev, self._SOURCE_HANDLED, allow_keys=_allow,
        )
        _denied = {
            k for k in ev
            if k not in _persisted and k not in self._META_KEYS
            and k not in self._SOURCE_HANDLED and k not in _allow
            and ev.get(k) is not None
        }
        if _denied:
            logger.warning(
                "#3998: denied %d undeclared :Source prop(s) on %s: %s — the "
                "graph INDEXES the raw and is not the raw store (D30). Add the "
                "key to _SOURCE_EXTRA_PROPS if it is genuine metadata.",
                len(_denied), key, sorted(_denied),
            )
        return r

    def _fold_source_versioned(self, ev: dict) -> None:
        """#5024 — replay a `:Source` VERSION TRANSITION (T6).

        THE DEFECT THIS FOLDS. `_upsert_source` bumps `updatedAt` / `version` /
        `contentHash` on a hash-differing `ON MATCH`, and `create_source`
        journaled a plain `SourceCreated` for **every** write. Two consequences,
        both measured on 12 real re-materialisations before this record existed::

            DIFF updatedAt: live='...57.016262' rebuilt='...57.079810'
            DIFF ingestedAt: live='...56.751580' rebuilt='...57.047168'

        (the replay's clock, not the recorded instant), and a NO-OP re-check
        (identical hash) still appended a journal line — 12 -> 13 — which is
        the unbounded-growth cost §9.6 requires the re-check to avoid.

        The ontology's own model (`ONTOLOGY.md` v3.15 §4.6 *Versioning*, and
        `STORAGE-ARCHITECTURE.md` §9.6) is: identity is `url`, `contentHash`
        identifies a VERSION, **one node per url**, and *"a version transition
        appends a journal record ... the prior version's window is a journal
        fact, recoverable by replay"*. So the record carries
        `previousContentHash` — which version this one superseded, the fact that
        makes the prior version addressable once the single node has moved on.
        The transition instant is NOT a separate ``at`` key: the record carries
        it as its own ``updatedAt`` (minted by the producer with the same single
        clock the live write used), and the fold reads exactly that.
        In-place by design: a second `:Source` per version is explicitly ruled
        out, so the history lives in the record and the node holds the CURRENT
        version.

        THE FOLD IS THE LIVE WRITER, DELIBERATELY. The first cut of this method
        re-stated `_upsert_source`'s SET clauses by hand and drifted from it in
        three fields — `urlAliases`, `sourcePath`, `canonicalUrl` — every one a
        live != replay divergence, found by the review gate rather than by the
        tests. Delegating makes apply/replay parity true **by construction**:
        there is no second clause list to fall out of sync. It is safe because
        `_upsert_source`'s ON MATCH is already hash-diff-gated and already reads
        the RECORDED instants (`coalesce($updatedAt, $now)`,
        `coalesce($ingestedAt, $now)`), and its `version` ordinal is
        `s.version + 1` on that same gate — exactly how the live path produced
        it — so replaying create -> transition reproduces the ordinal.
        """
        self._upsert_source(ev)


#: #3998 (D30): the declared `:Source` node-property surface, exposed at module
#: level so the OTHER write path that reaches `:Source` — the generic
#: `sdk._update_entity` tenant surface — and the provenance READ can enforce
#: and filter against the SAME declaration. Two copies of this set would be two
#: contracts, and the one that drifts is the one that stops being true.
_SOURCE_NODE_PROP_NAMES: frozenset = _EntityHandlers._SOURCE_NODE_PROPS
# The SERVER-MANAGED subset of the declaration: written only by `_upsert_source`'s
# fixed clauses (and validated by `raw_state.validate_raw_state`), never by a
# caller-supplied property map. `_update_entity` refuses these on a `:Source` —
# its allowlist is the whole declaration (which MUST admit `rawState`, or the
# state could not be written at all), so without this second set the documented
# tenant route could CLEAR a recorded absence by passing `rawState=None`, or
# persist an unvalidated `rawState='banana'` (#3998 review round 4).
#
# Put this IN FRONT of the declaration, never instead of it: the declaration is
# the read filter's contract (what may appear), this is the write filter's
# (what a caller may set).
_SOURCE_SERVER_MANAGED_PROPS: frozenset = frozenset({
    "rawState", "rawStateAt",
    # #3644: a version's valid-time window is minted by the writer (one clock,
    # shared with `updatedAt`) and journalled — it is never a caller-asserted
    # property. Declaring it on the node makes it readable and makes the read
    # filter keep it; listing it HERE is what stops
    # `tortoise_update_entity(url, validFrom=...)` from writing a window no
    # transition produced (the same "declared ⇒ writable" trap #5196 round 3
    # found for `__runId`). The transition facts (`previousValidTo`,
    # `previousContentHash`) and the remove-side spelling (`validTo`,
    # `valid_to`) are the same class — PR #8016 code-review ISSUE 1: a caller
    # `previousValidTo` collided with the explicit keyword on the journal emit.
    "validFrom", "validTo", "valid_to", "previousValidTo",
    "previousContentHash",
    # `__runId` is DECLARED (it is written to the node by `_upsert_source`'s
    # `run_clause`) but must stay CALLER-UNSETTABLE. `_update_entity` uses the
    # declaration as its WRITE allowlist, so declaring it without listing it here
    # made it writable through the MCP tool `tortoise_update_entity`: measured,
    # `update_entity(url, __runId="TENANT_FORGED")` was accepted, journalled into
    # the EntityMutated record and SURVIVED `rebuild_all` (#5196 round 3, P2).
    # This is the "put this IN FRONT of the declaration, never instead of it"
    # rule the comment below states.
    "__runId",
    # Lane L5 (#5097): the session-capture writer's provenance spellings. They are
    # DECLARED (read surface, #1352) and REPLAYED (only for a record that declares
    # itself a capture completion — `_SOURCE_CAPTURE_REPLAY_PROPS`), but they are
    # minted by the writer, never asserted by a caller: without this entry the
    # generic tenant route would persist a forged `eventId`/`sessionId` on the
    # node AND into the `EntityMutated` record, where a rebuild restores it — the
    # same `__runId` hole (#5196 round 3, P2) one line up.
    "sessionId", "capturedAt", "eventId",
})

# The IDENTITY keys of a `:Source` — NOT refused to a caller-supplied map.
# `create_source` treats these as server-managed (it MERGEs on `url` and mints
# `canonicalUrl`/`urlAliases`), and an earlier revision of the `_update_entity`
# `:Source` guard refused them there. That refusal was WRONG and left this branch
# red: #5438 (`58cd62ed3`, merged, an ancestor of this branch's base) made a
# url-keyed `:Source` WRITABLE through the generic entity surface, and main pins
# it (`tests/test_url_keyed_source_write_4649.py`, both
# `TestUrlKeyedSourceIsWritable` and `TestTheStateReadBackIsTheSameStatement`), so
# `url` MUST stay writable through that route. The measured consequence the
# refusal was aimed at is real and PRE-EXISTING to #3998: `update_entity(url,
# url=<2 KB body>)` rewrites the MERGE key and, after a rebuild, yields DUPLICATE
# `:Source` nodes and an EMPTY provenance chain for the Point. That is #4649's
# structural issue (the outer label loop takes the FIRST matching label), not a
# licence to refuse a write main requires; it is recorded there. (`id` is
# separately refused by `_sanitize_props(reject_id=True)` at the SDK boundary.)
_SOURCE_IDENTITY_PROPS: frozenset = frozenset({
    "url", "canonicalUrl", "urlAliases",
})


def filter_source_props(props: dict) -> tuple[dict, list[str]]:
    """Split a property map for a `:Source` into (declared, denied). #3998.

    ONE filter for the READ paths and for a REPLAYED property map. The live write
    paths enforce the same declaration through two deliberately DIFFERENT sets,
    because a caller may set less than may appear on the node:

      * `_upsert_source` (the caller passthrough) uses `_SOURCE_EXTRA_PROPS` —
        the narrow set a caller is allowed to ADD via `create_source(...)`.
      * `_update_entity` uses `_SOURCE_NODE_PROP_NAMES`, minus the server-managed
        subset it refuses outright. It does NOT refuse the identity keys: main
        requires a url-keyed `:Source` to stay writable through that route
        (#5438 / `tests/test_url_keyed_source_write_4649.py`).
      * the replay fold and the read paths (this function) use
        `_SOURCE_NODE_PROP_NAMES` — a replayed map is already-persisted state, so
        the question is what may APPEAR, not what a caller may set.

    Stated plainly because an earlier revision of this docstring claimed a single
    uniform enforcer, which the code does not do — and the two-set shape is the
    design, not a slip.
    """
    denied = sorted(k for k in props if k not in _SOURCE_NODE_PROP_NAMES)
    kept = {k: v for k, v in props.items() if k in _SOURCE_NODE_PROP_NAMES}
    return kept, denied
