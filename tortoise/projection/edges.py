"""Edge creation and linking methods for FalkorProjection."""
from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from tortoise.entity_identity import display_holder_ids, record_non_folded
from tortoise.live import epistemic_disjunction

# #5048: the ONE home for the two mitigation predicates, shared with
# `_upsert_point_props` in this package (imported before this module by
# `projection/__init__.py`): the identity rule, and the separate requirement
# that the record carries a strength a live writer could have written. See
# their docstrings for why the polarity is ``is False`` and why presence of the
# strength is not enough.
from tortoise.projection.entities import (
    has_usable_mitigation_strength,
    is_non_operator_payload,
)
from tortoise.source_identity import normalize_source_url, resolve_source_key

logger = logging.getLogger(__name__)

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


# ── #2489 structural-edge transfer parity: shared rel→(label,key) resolver ──
# ONE home for the key map supersede_point 2b EMISSION (sdk.py), the pass-2b
# DirectEdgeRepoint structural REPLAY branch (projection/__init__.py), and the
# resurrection-DELETE leg. Two divergent copies would drift: _create_about_edges'
# auto-detect (Subject-first order) cannot mint a specific aboutObject/
# aboutEvent/aboutDocument edge for an absent node (it would attach the
# descriptor's rel to a wrong-label node), so replay resolution is LABEL-SCOPED
# and never auto-detects.

# Snapshot-derivable structural rel set — the rels supersede's 2b transfer
# journals as DirectEdgeRepoint descriptors because rebuild pass-2 re-creates
# them at the OLD point from its immutable snapshot (extractedFrom prop +
# aboutEntities list -> _upsert_point_edges / entities.py). They are the edges
# whose resurrection the replay delete-leg must clean. aboutSource,
# aboutAction (Action dissolved in Ontology v3.0) and wasDerivedFrom are NOT
# derivable — never snapshot-recreated, never resurrect at old. ⛔ Membership is
# NOT the same question as "does 2b journal a descriptor for it": `aboutAction`
# and `wasDerivedFrom` DO carry one since #9836, as the ID-ADDRESSED family
# (`ID_ADDRESSED_STRUCTURAL_RELS` below — the target is keyed by its own
# IDENTITY property, `ID_ADDRESSED_TARGET_ID_PROPS`: `id`, or `eventId` for an
# `Event` — never by a natural key). A rel added here is one pass-2 RE-CREATES at
# old; the
# two properties are independent, and conflating them is what left that family
# moved-but-unjournalled.
DERIVABLE_STRUCTURAL_RELS = frozenset({
    'aboutSubject', 'aboutObject', 'aboutEvent', 'aboutPoint',
    'aboutDocument', 'extractedFrom',
})

#: The per-edge attributes a supersede TRANSFER carries — one definition, read
#: by every writer and replay leg that moves an edge, so they cannot drift.
#:
#: The consumers, all reading THIS tuple (#10139 named the structural half;
#: before it the same five keys were also spelled out as literal tuples at each
#: emission and replay site, which is the drift class this constant removes):
#:
#:   * ``sdk.supersede_point`` 2a-DIRECT descriptor emission (live read-back);
#:   * ``sdk.supersede_point`` 2a-DIRECT live carry (``_keep``);
#:   * ``sdk.supersede_point`` structural 2b descriptor emission;
#:   * ``projection._rebuild_all_replay`` — the 2a-DIRECT fold and the structural
#:     (pass-2b ``DirectEdgeRepoint``) fold, i.e. the ONE place the values are
#:     re-applied post-rebuild.
#:
#: A whitelist spelled separately on each side is how a carried value becomes a
#: dropped one: the structural replay re-created the edge BARE while the live
#: transfer carried `confidence` (#10139), and nothing in the code said the two
#: lists were meant to be the same list. Named `DIRECT_EDGE_ATTR_KEYS` rather
#: than `STRUCTURAL_…` because the 2a-DIRECT legs carry it too — the tuple is
#: the graph's edge-attribute vocabulary, not a structural-only one.
#:
#: ⛔ ``source_version`` is deliberately NOT a member, on either side. It is the
#: RECORD column name (``RelationUpsert.attrs``), while the graph writes this
#: anchor as ``sourceVersion`` (``_anchor_on_create`` below) — nothing writes
#: ``r.source_version``, so the entry could never match and would only imply a
#: carry that never happens. It is also the one key the record contract forbids
#: re-applying on a re-link ("a re-link must not ADVANCE a recorded version").
#: ⚠️ CONSEQUENCE, stated rather than implied: ``extractedFrom``'s
#: ``sourceVersion`` anchor is dropped by a supersede transfer AND by its
#: replay. That is a separate, pre-existing loss; this vocabulary does not own
#: it and must not appear to.
DIRECT_EDGE_ATTR_KEYS: tuple[str, ...] = (
    'direction', 'confidence', 'weight', 'label', 'batch_id',
)

# rel -> entity label of the structural TARGET (label-scoped resolution). The
# source side of a supersede transfer is always a Point. Public — sdk.py's 2b
# emission and the projection replay both import it (no duplicate key map).
STRUCTURAL_REL_LABELS = {
    'aboutSubject': 'Subject',
    'aboutObject': 'Object',
    'aboutEvent': 'Event',
    'aboutPoint': 'Point',
    # D10 (ONTOLOGY v3.15 §4.4): a document is a :Source, not a graph node.
    # The label and the replay key MUST move together (below) — a retargeted
    # label with the old key resolves to nothing and silently mis-points the
    # rebuilt edge. aboutDocument is NOT collapsed into aboutSource: it stays
    # in DERIVABLE_STRUCTURAL_RELS (above) and aboutSource stays out.
    'aboutDocument': 'Source',
    'extractedFrom': 'Source',
}

#: The about* rels an ``EventRecorded`` record may carry replay links for
#: (#8398). A subset of ``STRUCTURAL_REL_LABELS`` — ``extractedFrom`` is not an
#: about* prop a caller can set on ``create_event``.
_ABOUT_REPLAY_RELS = frozenset({
    'aboutSubject', 'aboutObject', 'aboutEvent', 'aboutPoint', 'aboutDocument',
})

#: The labels a structural target may carry — the values of
#: ``STRUCTURAL_REL_LABELS`` as a SET. A journaled ``target_label`` is checked
#: against it before it reaches ``resolve_structural_target``, which raises
#: (deliberately: the label is interpolated into query text) and would abort a
#: post-wipe replay.
_STRUCTURAL_LABEL_SET = frozenset(STRUCTURAL_REL_LABELS.values())


def stub_key(rel: str, target: dict):
    """Extract the replayable (label, key) for a structural edge's target node.

    ``target`` is the target node's property dict (the 2b SELECT returns
    ``properties(target)``). Key map: aboutSubject/aboutObject/aboutEvent/
    aboutPoint -> ``name``; aboutDocument/extractedFrom -> ``url`` (D10: a
    document is a :Source, so it resolves by ``url`` — the SAME key the label
    moved with. A title-keyed aboutDocument descriptor is no longer
    resolvable and must not be emitted). NEVER ``target.id`` — Subjects MERGE by
    name (id may be a webhook stub ulid, #1918), Sources by url. Returns None
    for a rel outside the derivable set or an unresolvable key (name-less Point
    from an id-targeted create_about_edge; a Source target lacking url) — such
    descriptors are un-replayable and the caller must SKIP emission (those
    edges die at rebuild today anyway).
    """
    label = STRUCTURAL_REL_LABELS.get(rel)
    if label is None:
        return None
    key = (target.get('url') if rel in ('aboutDocument', 'extractedFrom')
           else target.get('name'))
    if not isinstance(key, str) or not key:
        return None
    return (label, key)


def id_addressed_target(labels, target: dict):
    """The ``(label, identity)`` a repoint descriptor addresses an EPISTEMIC node by.

    The id-addressed twin of :func:`stub_key` (#9836). ``labels`` is the target
    node's label list (the 2b SELECT's ``labels(target)``) and ``target`` its
    property map; the result is the label the node is filed under on BOTH replay
    routes and the value they match it by.

    ⛔ ONE identity rule for both routes, and it is the tree's existing one, not
    this helper's invention: the label is chosen in
    :data:`EPISTEMIC_LABEL_ORDER` (Point-first, the fold's own sweep order, so a
    multi-labelled node resolves the same way the 2a-DIRECT transfer resolves it)
    and the key is that label's OWN identity property
    (:data:`ID_ADDRESSED_TARGET_ID_PROPS`) — ``id`` for Point/Subject/Object and
    ``eventId`` for **Event**, which is the property the record already files an
    Event under (``public.event.node_key = event_id``
    ``chk_event_node_key_is_contract_key``, matched by
    ``record_projection._ENTITY_MATCH_COLUMNS['event'] = ('node_key',)``) and the
    property the fold declares as an Event's PRIMARY identity
    (``projection._CANONICAL_ENTITY_ID_PROPS``). The label and the key move
    TOGETHER, exactly as :func:`stub_key`'s docstring warns for the name-keyed
    family.

    ⛔ ``eventId`` RATHER THAN ``id`` FOR EVENT, and this is not a detail. The
    meeting guard (``entities._event_guarded_merge``) MERGEs its SUFFIX branch on
    ``eventId`` while the props it writes keep ``id = <candidate>`` — so two
    distinct Events of one candidate BOTH carry the candidate in ``id``
    (``tortoise/live.py::EPISTEMIC_ID_KEYS`` states the same and forbids using
    the coalesced value as an identity). Keying an Event descriptor on ``id``
    therefore names no identity: the graph's replay finds 2 nodes and refuses,
    and the record's ``node_key`` lookup names the other one. ``eventId`` is what
    makes the two routes single-valued again, and what lets each row of the 2b
    transfer keep its own target.

    Returns ``None`` — the caller must SKIP emission — when the target carries no
    epistemic label (a `:Source` is url-keyed and outside the record's sweep) or
    no usable identity (a node the record cannot address; filing a descriptor for
    it would make the record RICHER than the graph, the one direction Interface A
    forbids). Such an edge still dies at rebuild, as it does today; it is
    NOT half-journaled.
    """
    held = {str(label) for label in (labels or ())}
    props = target or {}
    for label in EPISTEMIC_LABEL_ORDER:
        if label in held:
            prop = ID_ADDRESSED_TARGET_ID_PROPS.get(label)
            ident = props.get(prop) if prop is not None else None
            if not isinstance(ident, str) or not ident:
                return None
            return (label, ident)
    return None


def _mint_subject_stub(g, name: str) -> None:
    """MERGE the Subject stub the about-edge Name-CASCADE falls back to.

    ⚠️ **#3590 retired this construction and the retirement was WITHDRAWN —
    this mint stays, and both halves of why are recorded here (#8398).** The
    branch briefly made this helper ``resolve_fold_name(..., mention=True)``,
    i.e. resolve-only, on the argument that "the projection never mints an id"
    (D9). That argument is true for the mint D9 forbids — an OPAQUE id minted
    during a fold, which replay re-mints differently, so ``rebuild != live`` by
    construction. It is FALSE for THIS construction, and applying it here broke
    a shipped contract:

    * ``MERGE (s:Subject {name:$name}) ON CREATE SET s.id=$name`` is
      NAME-DETERMINISTIC: the id is a pure function of the MERGE key, so live
      and replay produce the byte-identical node. It is the construction
      ``#8398``'s replay contract is written against.
    * It is the ONLY construction that gives a name no producer registered a
      node at all, and the journal is append-only, so a resolve-only helper
      makes every ``how:"name"`` record already in a journal
      UN-REPLAYABLE (measured: ``replay_about_links`` → ``(0, 1)``, no node;
      ``tests/test_8398_aboutobject_replay.py
      ::test_a_LEGACY_name_link_record_still_replays``). It also contradicts
      ``_count_about_out``'s own documented premise ("``_create_about_edges``
      MINTS a Subject stub when nothing in the graph matches, so for any
      writable value a name link leaves an outgoing edge") and the shipped pins
      that read the stub: ``tests/test_projection.py
      ::test_falkor_subject_stub_resolves_canonical_id`` and
      ``::test_5026_b1_live_aboutdocument_ignores_non_document_source``,
      ``tests/test_pointsuperseded_rebuild.py
      ::test_resolver_stub_mint_equivalence_for_absent_entities``,
      ``tests/test_9079_edge_lane_declared.py`` (this helper IS a declared
      writer) and ``tests/test_cache_projection.py`` (the ``subjectKind`
      literal below).

    What D9 asks of the LIVE path is NOT here, and #3590 D1 (RULED 2026-10-26)
    says it must not be: the SDK's Event door no longer registers the names its
    record mentions (`subject` / `object` / `uses[]` / an unresolved `about*`
    name), because "an unresolvable mention drops its link, records the miss, and
    mints nothing". So for a name nothing registered, this helper IS the mint —
    the fold-side creation #8914 filed as a bug — and it is kept only because
    every `how:"name"` record already in a journal needs it to replay (the
    shipped pins above). Moving it is #8914's work, on both passes at once; it is
    not a producer duty the SDK door can discharge.

    #7369: `name` IS the MERGE key, so an unwritable one aborts the replay
    after the wipe. Skip rather than raise.
    """
    from tortoise.entity_identity import entity_create_lock
    from tortoise.projection import _log_identity_skip, _writable_id
    if not _writable_id(name):
        _log_identity_skip("Subject stub", name, "name (MERGE key)")
        return
    # ⛔ THE SAME ``(label, name)`` LOCK THE CREATE DOOR TAKES (#9876 review).
    # This is the last name-keyed WRITE left in the tree, and the create door is
    # id-keyed (`entity_identity.resolve_or_mint` mints an opaque id and MERGEs
    # on it), so the two constructions no longer converge on one node by
    # themselves — the name-keyed MERGE and the id-keyed MERGE simply address
    # different keys. Interleaved (stub mint between the create's resolve and
    # its write) they leave a name with TWO live carriers: one ``:Subject
    # {name:"X"}`` with ``id="X"`` (this stub) and one with the minted id.
    # Every later by-name create then refuses `ambiguous-entity-name`, which is
    # NON-exempt, so `rebuild_all` stops over a name this door split.
    #
    # Under the shared lock the two orderings both converge: stub first -> the
    # create's resolve finds the stub as the single live holder and ADOPTS it;
    # create first -> this MERGE matches that node by name and creates nothing.
    # Reentrant, and the replay callers (`replay_about_links`, create_event's
    # fold) take it harmlessly.
    with entity_create_lock("Subject", name):
        g.query(
            "MERGE (s:Subject {name:$name}) "
            "ON CREATE SET s.id=$name, s.subjectKind='other'",
            params={"name": name},
        )


def _mint_source_stub(
    g, url: str, source_kind: str | None = None, now: str | None = None,
) -> str | None:
    """MERGE the Source stub _link_source creates — single create path for live
    + replay (mirror _link_source's ON CREATE exactly: title=url, empty
    contentHash, ingestedAt now; session: refs carry is_episodic=true so the
    #1486 one-time episodic backfill does not re-match a replay-minted Source).

    Returns the resolved ``url`` key of the node the stub/registration
    addresses (S0b, #5012): a URL variant resolves to the node its canonical
    identity already names — via the SHARED ``resolve_source_key`` — so no
    write path can mint a second ``:Source``.  Callers MUST use the returned
    key for their subsequent MATCH/MERGE (the raw spelling may not be the
    node's stored ``url``).

    ``source_kind`` defaults to the ref-appropriate value. Ontology §4.6 +
    #909 §4.3 #6 register **agentSession** as the source-kind VALUE for session
    Sources (the four-node capture model's provenance bridge); everything else
    defaults to ``document``. Minting `session:` refs as ``document`` was a
    documented wart that `_materialize_session_source` had to upgrade IN PLACE
    (sdk.py) — resolving the default here means a replay-minted stub is already
    correct, and the eval ingest lane (which never materializes session sources)
    stops mislabelling every session Source as a document. Callers that know
    better still pass it explicitly.

    ``now`` (#8501) is the capture's OWN instant. The capture path calls this
    stub mint FIRST now (before extraction), so for a capture that creates no
    Point this mint is the ONLY writer of ``ingestedAt`` — and its twin
    ``SourceCreated`` record carries the capture's instant. Minting from
    ``_now_iso()`` there would put a LATER clock on the live node than the
    replay folds in, a live≠replay divergence on a path (a keyless capture) that
    previously had none. ``None`` keeps the historical clock read for every
    other caller.
    """
    if source_kind is None:
        source_kind = "agentSession" if str(url).startswith("session:") else "document"
    # S0b (#5012): resolve a URL variant to the node its canonical identity
    # already names, so the stub path cannot mint a second :Source either.
    key = resolve_source_key(g, url)
    # #7369: `key` is the Source MERGE key (`MERGE (s:Source {url:$url})`).
    from tortoise.projection import _log_identity_skip, _writable_id
    if not _writable_id(key):
        _log_identity_skip("Source stub", key, "url (MERGE key)")
        return None
    canonical = normalize_source_url(key)
    params = {"url": key, "raw_url": url, "cu": canonical,
              "sk": source_kind, "now": now or _now_iso()}
    ep_clause = ""
    if str(key).startswith("session:"):
        params["ep"] = True
        ep_clause = ", s.is_episodic=$ep"
    g.query(
        "MERGE (s:Source {url:$url}) "
        "ON CREATE SET s.sourceKind=$sk, s.title=$url, "
        "    s.canonicalUrl=$cu, s.urlAliases=[$raw_url], "
        f"    s.contentHash='', s.ingestedAt=$now{ep_clause} "
        "ON MATCH SET s.canonicalUrl = coalesce(s.canonicalUrl, $cu), "
        "    s.urlAliases = CASE WHEN $raw_url IN coalesce(s.urlAliases, []) "
        "        THEN s.urlAliases "
        "        ELSE coalesce(s.urlAliases, []) + [$raw_url] END",
        params=params,
    )
    return key


def resolve_structural_target(g, label: str, key: str, rel: str, *,
                              mint: bool = True, by_id: bool = False):
    """Resolve the label-scoped structural target node by its replay key.

    ``by_id=True`` (#9836) switches the match from the label's NATURAL key
    (`name`/`url`) to the node's OWN identity property
    (:data:`ID_ADDRESSED_TARGET_ID_PROPS` — ``id``, or ``eventId`` for Event) — the
    addressing the id-addressed repoint family uses, and the only one that
    resolves for a target whose rel fixes no label (`wasDerivedFrom`) or whose
    label has no natural key at all (a Point, the `aboutAction`/`aboutPoint`
    shape). Callers pass ``by_id=True`` for exactly
    :data:`ID_ADDRESSED_STRUCTURAL_RELS` — the flag is a per-REL property of the
    descriptor, never a descriptor-supplied choice, so a forged journal cannot
    ask for a different resolution; which PROPERTY the flag then matches is a
    per-LABEL property, so a forged ``target_label`` cannot choose it either.

    Create-if-missing semantics survive for ONE arm — ``extractedFrom``
    (Source): it MERGEs the provenance stub by ``url`` exactly as the live
    ``_link_source`` writes it, so replay reproduces a node the live graph
    really has, and a Source's identity IS its ``url`` (ONTOLOGY §3.2 — nothing
    is derived from a name). Every other arm resolves only:

    * **``aboutSubject`` (Subject) mints its NAME-KEYED stub** when the bare
      name has no holder — ``_mint_subject_stub``'s ``ON CREATE SET s.id=$name``
      construction, which is name-DETERMINISTIC (so replay reproduces the same
      node) and is what ``#8398``'s replay contract and the shipped pins are
      written against. ⚠️ #3590 briefly made this arm resolve-only; that was
      withdrawn — see ``_mint_subject_stub``'s docstring, which records why the
      D9 rule against fold-time mints does not apply to a name-keyed stub.
      ⛔ It is NOT discharged upstream: since #3590 D1 (RULED 2026-10-26) the
      SDK's Event door registers NOTHING for a mention, so a NEW journal carries
      no ``SubjectAdded`` for this name and this replay-time mint is the LIVE
      mint too — a node whose `id` IS its `name`, the R1 shape, open as #8914.
      (An earlier revision of this sentence claimed the door registered the
      Subject; D1 deleted that door behaviour and the claim was not deleted
      with it — #9876 review P2.)
    * **``aboutDocument`` (Source) is RESOLVE-ONLY**, and for a sharper reason: a
      miss is logged and skipped, never minted, because a phantom Source would
      silently mis-point the resurrected edge (adversarial class B1). D10 §4.4
      makes the document node a ``:Source``, so this is the ``rel`` that decides
      the target-KIND contract — this resolver therefore does NOT
      create-if-missing for every Source; read the branch, not the label.
    * **every other label is RESOLVE-ONLY.** NEVER
      auto-detect labels (Subject-first auto-detect would attach an aboutObject
      descriptor to a same-name Subject node) and NEVER mint Point stubs by name
      (an absent Point target => skip — absent Documents/Objects/Events return
      None too; those entities are journaled by their own creation events, and an
      absent one is an unjournaled-producer edge that dies at rebuild).

    ``mint=False`` (#8398 review P1-2) forbids the stub mint — the
    ``extractedFrom`` Source one and the ``aboutSubject`` one — and leaves pure
    resolution (``resolve_source_key`` instead, which only canonicalises the
    key). A #8398 link whose live
    cascade RESOLVED an existing node must not mint a replacement when that node
    is missing at replay: live created nothing, so a minted stub would be a node
    the live graph never had. The missing endpoint is then reported as a failed
    link instead — the honest outcome for an unjournaled producer.

    Returns {"internal": <FalkorDB internal node id>, "logical": <node.id>}
    or None. ``g`` is the projection graph handle (``self.g`` on
    FalkorProjection / the guarded proxy). """
    if label not in STRUCTURAL_REL_LABELS.values():
        # label is interpolated into the query STRUCTURE — fail loudly on
        # anything outside the fixed constant set (parity with
        # _resolve_entity's runtime defense).
        raise RuntimeError(
            f"resolve_structural_target: unsafe label {label!r} "
            "(contract: STRUCTURAL_REL_LABELS values only)")
    if by_id:
        # #9836 — the id-addressed arm. RESOLVE-ONLY, exactly like every
        # non-stubbable label below: the node's identity property is one its OWN
        # creation record registered, so a miss means the journal never created
        # it (or hard-deleted it), and minting one here would fabricate a node
        # the live graph does not have. `LIMIT 2` + the same #5509 discipline as
        # the name arm: a value held by two nodes of one label names no identity,
        # and picking one would land the edge on a different node than live's
        # transfer did.
        #
        # ⛔ #9836 review (P2): the match property is the LABEL's OWN
        # (`ID_ADDRESSED_TARGET_ID_PROPS`) — ``eventId`` for Event, ``id`` for the
        # rest — not ``id`` for every label. The earlier blanket ``{id:$id}`` was
        # justified as "a live producer cannot create that shape for these rels",
        # and that premise is FALSE in this tree for Event:
        # ``entities._event_guarded_merge``'s SUFFIX branch MERGEs on the suffixed
        # ``eventId`` while ``id`` keeps the candidate, so two distinct meeting
        # Events legitimately share one ``id`` (``live.py::EPISTEMIC_ID_KEYS``
        # states it). Under the blanket match this arm hit the ambiguity refusal
        # and DROPPED the descriptor while live had already moved every edge, and
        # the record — which files an Event under ``node_key`` = ``event_id`` —
        # resolved a different Event than the graph had. Keying on the label's own
        # identity makes the refusal genuinely defensive again (the graph's MERGE
        # key IS ``eventId``) and puts both routes on the same node.
        prop = ID_ADDRESSED_TARGET_ID_PROPS.get(label)
        if prop is None:
            # Not an addressable label (`Source`). The caller's allowlist already
            # refuses it; return None rather than guess an identity for it.
            return None
        rows = g.query(
            f"MATCH (x:{label} {{{prop}:$id}}) RETURN ID(x), x.{prop} LIMIT 2",
            params={"id": key}).result_set
        if len(rows) > 1:
            return _refuse_ambiguous_target(label, key, rows, rel)
        if not rows:
            return None
        return {"internal": rows[0][0], "logical": rows[0][1]}
    if label == "Subject":
        if mint:
            _mint_subject_stub(g, key)
        rows = g.query(
            "MATCH (s:Subject {name:$name}) RETURN ID(s), s.id LIMIT 2",
            params={"name": key}).result_set
        if len(rows) > 1:
            return _refuse_ambiguous_target("Subject", key, rows, rel)
        if not rows:
            return None
        return {"internal": rows[0][0], "logical": rows[0][1]}
    if label == "Source":
        # D10 (ONTOLOGY §4.4): aboutDocument's target is a document :Source
        # keyed by ``url``. RESOLVE-ONLY — never mint: a miss must be logged
        # and skipped, never turned into a phantom Source that silently
        # mis-points the resurrected edge (adversarial class B1). By contrast,
        # extractedFrom keeps its create-if-missing stub (live _link_source
        # parity).
        if rel == "aboutDocument":
            # D10 B1: aboutDocument's target is a DOCUMENT — a :Source carrying
            # `documentKind` and addressable by `url` (ONTOLOGY §4.4). This
            # replay match carries that target-KIND contract and it is
            # load-bearing rather than defensive on the DESCRIPTOR route: a
            # supersede descriptor is keyed on `url` ALONE (`stub_key` cannot
            # tell a document from a provenance :Source), and supersede 2b
            # enumerates `(old:Point)-[rel]->` only — so a descriptor written by
            # a pre-#5206 build (when `create_edge` refused nothing) would be
            # RE-ATTACHED to a provenance :Source at rebuild unless the kind
            # test runs in this match. Refusing SKIPS the descriptor (the edge is
            # absent from the rebuild) instead of minting a mis-pointed one —
            # the #2489 "one create path / byte-identical stubs" invariant.
            # ⚠️ The descriptor route is not the only caller: the name-path arm
            # of `replay_about_links` below calls this same match — with
            # `rel='aboutDocument'`, `target_label='Source'` and the journaled
            # `resolve_key` — whenever a pinned endpoint value does not resolve
            # by id (its `resolve_key` fallback; the key `resolve_about_link`
            # pins there is a DOCUMENT's `url`, guarded by that arm's own
            # `documentKind IS NOT NULL`). So the predicate is load-bearing on
            # the descriptor route only; on that arm the key already came from a
            # document and the kind test is a defence, not the witness.
            # Pinned by tests/test_pointsuperseded_rebuild.py::
            # test_pre_5206_aboutdocument_descriptor_never_repoints_to_a_provenance_source
            # (delete the predicate below and it reds), and the
            # `replay_about_links` route below by
            # tests/test_8398_aboutobject_replay.py::
            # test_the_name_path_aboutdocument_arm_reaches_the_kind_guarded_resolver.
            # ⚠️ This replay arm is STRICTER than two live writers, which apply
            # NO kind test at all: `create_about_edge` — the about* name/handle
            # seam `create_event(aboutDocument=…)` reaches, label filter only —
            # and `session_link.link_entity`, which validates the ONTOLOGY §3.2
            # triple only. `about_document_target_reason` is called from
            # `create_edge` alone. The two live paths that DO carry the
            # contract are the auto-detect (`_resolve_about_edge_target`:
            # `e.documentKind IS NOT NULL`) and `create_edge` (#5206). Closing
            # those two writers is #9679, deliberately not done here — never
            # loosen this guard on the strength of that gap.
            rows = g.query(
                "MATCH (s:Source {url:$url}) WHERE s.documentKind IS NOT NULL "
                "RETURN ID(s), s.id LIMIT 1",
                params={"url": key}).result_set
            if not rows:
                logger.warning(
                    "aboutDocument replay: no Source at url=%r — descriptor "
                    "skipped (never minted, never mis-pointed)", key)
                return None
            return {"internal": rows[0][0], "logical": rows[0][1]}
        # S0b (#5012): the stub helper returns the CANONICAL key — the raw
        # spelling may not be the node's stored ``url``, and the MATCH below
        # must address the node the stub actually merged onto.
        key = _mint_source_stub(g, key) if mint else resolve_source_key(g, key)
        rows = g.query(
            "MATCH (s:Source {url:$url}) RETURN ID(s), s.id LIMIT 1",
            params={"url": key}).result_set
        if not rows:
            return None
        return {"internal": rows[0][0], "logical": rows[0][1]}
    # Non-stubbable labels — resolve-only (never mint). Event nodes match by
    # name (set by create_event).
    #
    # #5509: a NAME is a mutable natural key (D2: two live carriers are legal),
    # so "the" target is not the backend's arbitrary first row. `LIMIT 1`
    # silently picked one — a rebuilt edge could land on a different same-name
    # carrier than the one live pointed at, i.e. replay was order-dependent —
    # and the Object arm is exactly the identity read #5509 owns. Resolve
    # EXACTLY ONE holder or refuse: two or more records the non-folded entry and
    # skips the descriptor (the honest outcome for a name that does not name one
    # identity). NO status filter here, deliberately: this is REPLAY resolution
    # of an already-written edge, which must reach the node live addressed even
    # when it has since gone terminal — routing it through the live resolver
    # would make rebuild disagree with the graph it is rebuilding.
    q = (f"MATCH (x:{label} {{name:$key}}) "
         f"RETURN ID(x), x.id LIMIT 2")
    rows = g.query(q, params={"key": key}).result_set
    if len(rows) > 1:
        return _refuse_ambiguous_target(label, key, rows, rel)
    if not rows:
        return None
    return {"internal": rows[0][0], "logical": rows[0][1]}


def _refuse_ambiguous_target(label: str, key: str, rows, rel: str):
    """Record + report a name held by 2+ carriers; never pick one.

    ``rows`` are the `(internal, logical)` pairs of every holder. Returns None
    so the caller skips the structural edge: a name that does not name ONE
    identity has no target, and picking an arbitrary holder is what #5509
    removes. The entry (not the log line) is the record.
    """
    record_non_folded(label, key, display_holder_ids([r[1] for r in rows]))
    logger.warning(
        "structural edge %s: target %s %r has %d carriers %s — descriptor "
        "skipped (never guess a name-keyed identity, #5509)",
        rel, label, key, len(rows),
        display_holder_ids([r[1] for r in rows]))
    return None


# Canonical structural-edge predicate vocabulary (ONTOLOGY §3.2/§3.3 + #391).
# Hoisted from create_edge so SDK surfaces (e.g. TortoiseSDK.ingest, epic #888
# W4) can validate relation names WITHOUT string-duplicating the set.
_VALID_EDGE_PREDICATES = frozenset({
    'performs', 'produces', 'uses', 'authoredBy', 'ownedBy', 'managedBy',
    'hasMember', 'holdsRole', 'memberOf', 'reportsTo',
    'participatesIn', 'hasPart', 'related', 'dependsOn', 'references',
    'wasDerivedFrom',
    # #391: about* edges (ONTOLOGY §3.2/§3.3) were only creatable via
    # create_about_edge — the generic create_edge set missed them.
    'aboutSubject', 'aboutObject', 'aboutEvent', 'aboutDocument',
    'aboutSource', 'aboutAction',
})


#: The predicates `create_about_edge` accepts — the about* family, hoisted out
#: of the verb so the §17.1 lane's write set (#9195) can NAME it instead of
#: re-spelling the literal in the suite. Deliberately NOT the same constant as
#: `_ABOUT_REPLAY_RELS` above: that one describes what an `EventRecorded`
#: record may carry, and coupling the two would let a replay-set edit change
#: which edge types the verb accepts.
_VALID_ABOUT_EDGE_PREDICATES = frozenset({
    'aboutSubject', 'aboutObject', 'aboutEvent', 'aboutPoint', 'aboutDocument',
})


# Every structural predicate a WRITER accepts — the union of the three
# structural write surfaces this module owns:
#   * create_edge            -> `_VALID_EDGE_PREDICATES`
#   * create_about_edge      -> the about* set, whose keys are
#                               `STRUCTURAL_REL_LABELS` (it accepts `aboutPoint`,
#                               which the generic create_edge set misses #391)
#   * TortoiseSDK.ingest     -> `_VALID_EDGE_PREDICATES`, plus an explicit
#                               `extractedFrom` exemption (sdk.py)
# Consumers that ask "can the runtime write this predicate?" (the #2766
# write-path verdict) read THIS set, not one of the three subsets, so a
# predicate added to any single writer moves the answer with it.
STRUCTURAL_WRITE_PREDICATES = frozenset(
    _VALID_EDGE_PREDICATES | set(STRUCTURAL_REL_LABELS)
)


#: Every predicate the §17.1 STRUCTURAL-EDGE lane (#9195) can WRITE — the union
#: of the two declared mints' allow-lists (`create_edge` →
#: `_VALID_EDGE_PREDICATES`, `create_about_edge` → `_VALID_ABOUT_EDGE_PREDICATES`).
#: The suite partitions THIS set against `structural_edge_carrier`, so a
#: predicate accepted by either mint and classified by neither half is a red
#: test rather than a silent widening (`aboutPoint` is the one member the
#: generic verb does not accept — #9195 review P2-1).
#: ⚠️ Neither verb reads this constant to VALIDATE input: it is the lane's
#: surface for auditors, not a shared allow-list (widening it widens nothing).
STRUCTURAL_EDGE_LANE_PREDICATES = frozenset(
    _VALID_EDGE_PREDICATES | _VALID_ABOUT_EDGE_PREDICATES
)


def about_document_target_reason(label: str | None,
                                 properties: dict | None) -> str | None:
    """Why this node cannot be an ``aboutDocument`` target, or ``None`` when
    it can.

    ``aboutDocument``'s target is a DOCUMENT — a ``:Source`` carrying
    ``documentKind`` and addressable by ``url`` (ONTOLOGY §4.4). The replay path
    enforces all three, but in two places: the resolver matches
    ``(:Source {url:$url}) WHERE documentKind IS NOT NULL``, and the supersede
    descriptor is keyed by ``stub_key``, which emits nothing for an empty or
    non-string key (the ``isinstance(x, str) and x`` rule ``_writable_id``
    applies). Before #5206 the live auto-detect refused a non-document Source
    and so did replay, but the live producer (``create_edge``) refused nothing —
    an edge created through it was built, transferred at supersede and then
    dropped by ``rebuild_all``, ending on NEITHER node (#5206; the two writers
    that still refuse nothing are #9679). ``url`` is part
    of the contract because the supersede descriptor is keyed on it
    (``stub_key``), so a source with no usable ``url`` is lost the same way.
    """
    props = properties or {}
    if label != "Source":
        return f"a {label or 'unknown'}-labelled node, not a :Source"
    if props.get("documentKind") is None:
        return "a :Source without documentKind"
    url = props.get("url")
    if not (isinstance(url, str) and url):
        # Parity with `stub_key` and `_writable_id`: an empty (or non-string)
        # identity is not an identity — `stub_key` emits no DirectEdgeRepoint
        # descriptor for it, so the edge is un-replayable and dies the same way.
        return ("a :Source with no usable url — an aboutDocument target is "
                "addressed by url, and an empty one emits no descriptor")
    return None


# `references` targets whose node is BUILT FROM the source's content, and therefore
# carry the version anchor `sourceVersion` (owner-approved option A, 2026-09-25, #5199;
# the operative record is `STORAGE-ARCHITECTURE.md` §9.6). The anchor is set at LINK
# TIME from the source's current `contentHash`, which is why the public SDK signature
# does not change.
#
# ⚠️ Source of authority: the approved scope is "the `contentHash` of the source version
# the target was built from", and it is the CODE's writers that the set is pinned to —
# NOT a §3.4 enumeration. ONTOLOGY §3.4 declares the `references` target list as
# `Event|Object|Source` (noting a document is itself a `:Source`) and §4.6 still scopes
# the anchor to `extractedFrom` (Point-level); that wording is deliberately frozen on
# this branch, in owner review on #5199.
#
# `Object` is deliberately ABSENT. Every in-repo writer of a Source→Object
# `references` link is identity/mention — a connector artifact whose Source `url`
# IS the artifact — so a version there would be a non-answer, not a stale mark.
# Referential-containment links (Source→Source) carry no `sourceVersion`: a "Source"
# caller lands in the un-anchored branch below, and the in-repo containment
# writers (the session→external MERGE in `hosted_api.py`) write them outside this
# method. `references` is in NEITHER `STRUCTURAL_REL_LABELS` nor
# `DERIVABLE_STRUCTURAL_RELS`, so this anchor adds nothing to the replay surface.
#
# The discriminator is the caller-supplied RELATION spelling (`entity_label`), not the
# target's node label — a proxy for "derived", and the only signature-preserving signal
# available. `Document` is a member because in-repo writers
# do mint a DOCUMENT-DERIVATION link (`projection/entities.py::_upsert_document`, the
# session->document link in `hosted_api.py`, the doc classifier of the ingest path);
# excluding it would leave that derivation half unanchored. If the ontology rules that
# the connector `Event` is identity rather than derivation, only this set moves.
#
# !! POST-D10 the `:Document` LABEL is retired (a document is a `:Source`), so this set
# member is reachable ONLY through the retained deprecated alias: those writers keep
# passing `"Document"` as the RELATION's spelling, and `link_source_to_entity` reads it
# for exactly this decision BEFORE remapping the identity onto `:Source`. Those are two
# separate facts — the stored edge is `(Source)-[:references]->(Source)` either way,
# while the alias is what still marks it a DERIVATION rather than containment. A
# "tidy-up" that switches those call sites to `"Source"` therefore simplifies nothing:
# it silently drops the anchor from every document link. That is not hypothetical — it
# is the merge regression pinned by `test_document_derivation_through_the_production_path_anchors`.
_DERIVATION_REFERENCES_LABELS = frozenset({"Event", "Document"})

# CQL suffix stamping a version anchor on a link MERGE, `ON CREATE` only.
#
# `ON CREATE` is load-bearing: connectors re-poll and the `link_source_to_*` writers are
# idempotent MERGEs, so advancing a recorded version on a re-link would erase exactly the
# staleness the anchor exists to expose. And `''` must NOT be stamped: it is the
# auto-created-Source placeholder (and the "no recorded hash" value on an Event), and
# `'' = ''` compares equal to the source's current hash — a FALSE current.
#
# `version_expr` names the version the edge was READ at. Three expressions are passed:
# ``s.contentHash`` on the `references` writers (which re-read the Source, including on the
# rebuild path — ``_upsert_document`` and ``_materialize_connector_source`` in pass 1b),
# ``e.file_hash`` on the repair path — see
# :meth:`_EdgeHandlers.link_source_to_legacy_event` — and the caller-bound ``$v`` on the
# `extractedFrom` writer (:meth:`_EdgeHandlers._link_source`), which LIVE fills from
# :func:`resolve_source_versions` and REPLAY passes from the Point's own journaled snapshot
# (``sourceVersionTransit``), so that anchor needs no graph read at replay.
#
# ⚠️ KNOWN LIMITATION — the anchor does not advance when the TARGET is rewritten in place
# (#5199, under owner review). ``ON CREATE`` means a target rebuilt from a NEWER version
# through the same node id (the agent-session re-index path reuses
# ``event_id = f"session_{session_id}"``) keeps the ORIGINAL anchor and so reads STALE
# while the target is in fact current — the converse of the false-current guarded below,
# and the conservative direction the model prefers (``stale != wrong``, §4.6). Advancing
# it safely requires distinguishing a target rebuild from a source-only re-poll, which
# the ON CREATE/ON MATCH pair alone cannot; see the question recorded on #5199.
def _anchor_on_create(version_expr: str) -> str:
    return (
        "ON CREATE SET r.sourceVersion = "
        f"CASE WHEN {version_expr} IS NULL OR {version_expr} = '' "
        f"THEN NULL ELSE {version_expr} END"
    )


_DERIVATION_ANCHOR_SET = _anchor_on_create("s.contentHash")
_BACKFILL_ANCHOR_SET = _anchor_on_create("e.file_hash")


def resolve_source_versions(g, source_ref) -> dict[str, str]:
    """LIVE-only: map each source ref to its :Source's non-blank ``contentHash``.

    #5256 — the create-path ``extractedFrom`` read-version anchor. This is the
    ONLY place the ``extractedFrom`` read version is read from a Source in
    Python (the separate ``references`` anchors read ``s.contentHash`` inside
    their own MERGE — see ``_DERIVATION_ANCHOR_SET``); it runs on the LIVE write
    path and its result is carried in the Point's own journaled snapshot, so the
    REPLAY never re-reads the Source for the ``extractedFrom`` anchor (the separate
    ``references`` anchors DO re-read it on the rebuild path). That distinction is
    load-bearing:
    ``_upsert_source``'s in-place ``contentHash`` bump is unjournalled (#5024),
    so a Source read at replay time can have advanced since the Point was read —
    a FALSE current.

    Keys are the **raw ``extractedFrom`` ref** — the journal-stable spelling —
    NOT ``resolve_source_key(g, ref)``'s live-time resolution. ``_link_source``
    at replay looks the raw ref up directly, and a value keyed by a
    *resolution* is unstable: the Source node's stored ``url`` can differ
    between live and replay (an unjournaled stub minted by an earlier Point is
    absent when pass-1 builds the Source, so the same ref resolves to a
    different key), and the lookup then misses and leaves the edge bare with no
    error. The raw ref is the one spelling both lanes share — it is the Point's
    own payload key. ``resolve_source_key`` is still called for every ref, but
    ONLY to FIND the Source node whose ``contentHash`` to read; it is never
    this dict's key. Empty / missing / ``''`` / blank
    hashes are OMITTED: ``''`` compares equal to a Source's ``''`` and reads as
    a false CURRENT, so absent is the honest value (ONTOLOGY §4.6). The same
    rule is applied on the replay side by ``_valid_transit_pairs`` — this
    function must agree with it or a blank hash is live-only (a live≠replay
    divergence).

    ``source_ref`` is normalized exactly as ``_link_source`` does — a bare
    ``str`` is ONE ref, never iterated character-wise.

    Side effect: ``resolve_source_key``'s idempotent adopt-on-touch (it stamps
    ``canonicalUrl``/``urlAliases`` on an EXISTING pre-canonical node) — the
    same side effect ``_link_source``/``_mint_source_stub`` already has. It
    never mints a Source.
    """
    refs = [source_ref] if isinstance(source_ref, str) else list(source_ref)
    out: dict[str, str] = {}
    for ref in refs:
        if not ref:
            continue
        key = resolve_source_key(g, ref)
        rows = g.query(
            "MATCH (s:Source {url:$url}) RETURN s.contentHash",
            params={"url": key}).result_set
        if not rows:
            continue
        h = rows[0][0]
        # #5256: blank is ABSENT, exactly as the replay-side
        # `_valid_transit_pairs` treats it. A truthiness test would admit a
        # whitespace-only `contentHash` ("   " is truthy): the LIVE writers
        # would then stamp `r.sourceVersion="   "` (`_anchor_on_create` only
        # nulls `''`) and carry `[['url','   ']]` on the node, while the
        # REPLAY predicate drops the pair — a live≠replay divergence reachable
        # from `create_source(url, kind, contentHash="   ")`.
        if isinstance(h, str) and h.strip():
            # Keyed by the RAW ref, not `key`: see the docstring — the
            # resolution is a live-time fact, the raw ref is journal-stable.
            out[ref] = h
    return out


def _source_version_transit(versions: dict[str, str] | None):
    """The Point-node transit for the ``extractedFrom`` read version (#5256).

    A list of ``[raw_ref, contentHash]`` pairs, keyed by the Point's own
    ``extractedFrom`` spelling (journal-stable — see ``resolve_source_versions``)
    — the EDGE carries the
    per-link scalar ``r.sourceVersion``; this list is the prop-as-transit that
    puts the value into the Point's own journaled snapshot (``get_point`` →
    ``ev["point"]``) so pass-2 can re-stamp the edge without reading the
    Source. Nested arrays of scalars are persistable (#2894), so this is a
    legal node property.

    Returns ``None`` when there is nothing to record, so the key is OMITTED
    entirely — never ``[]`` and never ``''``: an absent anchor must be ABSENT,
    not an empty value the replay/gate would compare as present.
    """
    if not versions:
        return None
    return [[ref, h] for ref, h in versions.items()]


def source_change_notices(g, source_refs, current_versions: dict[str, str] | None):
    """#5516 — the advisory write-time notice: was this Source read at a version
    that is no longer current?

    For each ref, compares the Source's CURRENT ``contentHash`` (read by the
    caller into ``current_versions`` via :func:`resolve_source_versions`) against
    the versions recorded on the Source's ``extractedFrom`` links (#5256's
    ``r.sourceVersion``). The Source has changed for the writer iff its current
    version has **never been recorded as read** — so the decision is
    order-independent and cannot re-fire once the current version has been read.

    Advisory by construction: it writes nothing, and the caller treats any
    failure as "no notice" rather than as a write failure. Returns a list of
    ``{"source", "previousVersion", "currentVersion"}`` dicts — empty when
    every Source is unchanged, or when nothing comparable was recorded.

    ⚠ It does carry a read's own side effect: :func:`resolve_source_key` performs
    its documented idempotent adopt-on-touch (it stamps ``canonicalUrl`` /
    ``urlAliases`` on an existing PRE-canonical node). At the ``create_point``
    call site the identical resolution has already run inside
    :func:`resolve_source_versions`, so no NEW write occurs there — but a direct
    caller on a pre-canonical Source does mutate it, and this docstring will not
    claim otherwise.

    Blank/absent on EITHER side is honest-absent and produces NO notice: ``''``
    compares equal to a Source's ``''`` and reads as a false *current* (#5256),
    and a Source read for the first time has no prior version to compare
    against. The comparison is exact string equality, exactly as
    :func:`tortoise.search_engine.currency_status` compares one link's pair —
    content hashes carry no ordering, so there is no "newer" to infer.

    The fire/silence decision deliberately does NOT order by ``p.createdAt``:
    that is a CALLER-owned logical date (the ingest path passes document
    frontmatter dates), so a read recorded later can carry an earlier
    ``createdAt``. Ordering the decision by it would fire a notice for an
    UNCHANGED Source whenever a newer read happens to carry an older date.
    ``createdAt`` orders only which prior version the notice NAMES.

    ⚠ The recorded side is the **Source's** recorded reads, not a per-writer
    one: the create seam records no writer identity on the ``extractedFrom``
    link, so a per-writer scoping is not derivable here. On a multi-writer graph
    it answers "has this Source changed since its last recorded read" — the
    stronger, still-true statement.

    ``source_refs`` is normalized exactly as :func:`resolve_source_versions`
    does — a bare ``str`` is ONE ref, never iterated character-wise — and
    ``current_versions`` is keyed by the RAW ``extractedFrom`` ref (that
    function's journal-stable key).
    """
    refs = [source_refs] if isinstance(source_refs, str) else list(source_refs)
    versions = current_versions or {}
    notices: list[dict] = []
    seen: set[str] = set()
    for raw_ref in refs:
        if not raw_ref:
            continue
        current = versions.get(raw_ref)
        if not isinstance(current, str) or not current.strip():
            continue
        # Resolve to the ONE Source node identity `resolve_source_versions`
        # addressed (see the adopt-on-touch note above). Dedup on the RESOLVED
        # key, not the raw ref: two refs that alias one Source describe ONE
        # change, and reporting it twice would inflate the notice list.
        key = resolve_source_key(g, raw_ref)
        if key in seen:
            continue
        seen.add(key)
        # (1) DECISION — order-independent. Has this Source already been read at
        # its CURRENT version? If so it has not changed since that read and a
        # notice would be a FALSE change. `LIMIT 1` exits on the first edge that
        # proves it.
        hit = g.query(
            "MATCH (p:Point)-[r:extractedFrom]->(s:Source {url:$url}) "
            "WHERE r.sourceVersion = $current "
            "RETURN 1 LIMIT 1",
            params={"url": key, "current": current}).result_set
        if hit:
            continue
        # (2) LABEL — `previousVersion` names a prior recorded read. When
        # several exist, `createdAt` picks which (BEST-EFFORT: it is a
        # caller-owned logical date); the decision above is already made, so a
        # caller-owned date cannot flip fire/silence.
        rows = g.query(
            "MATCH (p:Point)-[r:extractedFrom]->(s:Source {url:$url}) "
            "WHERE r.sourceVersion IS NOT NULL AND r.sourceVersion <> '' "
            "AND r.sourceVersion <> $current "
            "RETURN r.sourceVersion "
            "ORDER BY p.createdAt DESC, p.id DESC LIMIT 1",
            params={"url": key, "current": current}).result_set
        if not rows:
            continue
        recorded = rows[0][0]
        if isinstance(recorded, str) and recorded.strip():
            notices.append({
                "source": raw_ref,
                "previousVersion": recorded,
                "currentVersion": current,
            })
    return notices


# ═══════════════════════════════════════════════════════════════════════════
# §17.1 (#9195) — the STRUCTURAL-EDGE write lane is DECLARED.
#
# `docs/architecture/STORAGE-ARCHITECTURE.md` §17.1 (from the §2.0 owner
# ruling: Postgres is the record, FalkorDB a derived cache) forbids a SECOND
# writer of the cache. `#7951` enforced that at the fold (`FalkorProjection.
# apply`) and at the public raw-query lane (`FalkorProjection.query`); `#9079`
# decomposes closing the third door — the guarded write handle the SDK itself
# uses, `proj.g.query` — into the writers that legitimately reach it FIRST, and
# `#9124` is the guard that lands last. This is the FOURTH and last declaration
# child (`#9123` the `:EpMeta` epoch, `#9121` onboarding metadata, `#9122` the
# raw `:Event` writers) — ⚠️ "last declaration child" is NOT "#9124 may arm":
# the about NAME lane and the provenance lane listed under WHAT THIS LANE DOES
# NOT COVER are still unscoped, so `#9124` must census (or scope) them first.
#
# WHAT THE WRITES ARE. `create_edge` and `create_about_edge` each issue exactly
# ONE mutating statement,
#
#   MATCH (s:L {k:$sv}) MATCH (t:M {k:$tv}) MERGE (s)-[:<predicate>]->(t)
#
# and `create_edge`'s three typed wrappers (`create_owned_by`,
# `create_managed_by`, `create_authored_by`) are `return self.create_edge(...)` —
# so the lane is two mints over one statement shape. The write mints NO node
# (`MATCH` on both endpoints, never `MERGE`: an absent endpoint returns `False`
# and writes nothing), NO property (no `SET` on the relationship, no value
# parameter — the verbs take none, see
# `docs/architecture/STORAGE-ARCHITECTURE.md` §9.6) and NO identity. Its whole
# content is `(source, predicate, target)` between two entities that already
# exist, so the write adds no state of its own: it is a DERIVATION.
#
# WHY DECLARED AND NOT JOURNALLED, PRECISELY. Two shapes would have made this
# write honest without a declaration, and the evidence rejects BOTH:
#   * `_live_write_scope` — its contract is "journal appended first, then the
#     fold". ✅ That is now TRUE of this lane (`#9553` class 4): the mints journal
#     a flat `DirectEdgeCreated` next to the edge, so the lane COULD declare it.
#     It keeps the narrower `_structural_edge_write_scope` because the write is
#     still not gated on the append (the append is best-effort, and a refused
#     append must not refuse a product write) — the declaration names the write,
#     not the ordering.
#   * journalling — ✅ LANDED (`#9553` class 4, `_record_structural_edges`).
#     `DirectEdgeCreated` is a flat `{src, tgt, edge_type, source_label,
#     target_label}` record whose pass-2b fold re-creates exactly this edge shape,
#     and `record_projection`'s `RelationUpsert` materialises the same descriptor
#     as a `relation` row — so ONE carrier serves both rebuild routes. The
#     emission lives at the mint and reaches the journal through the recorder the
#     journal-owning door binds; a REPLAY never emits (`replay_window_open`),
#     because a recovery write that appended to the truth store would make the
#     log a function of itself. The record-store epic that owns this half is
#     `#7879` (R5), and the write-class row this lane's LIVE side already carried
#     is on `#9079`'s census.
# And the ORDERING is settled the other way round regardless: the declaration
# lands BEFORE the guard, because a guard that refuses the product's own edge
# mints is not a guard, it is a regression (measured on `#9079`'s lane: arming it
# refused these writers; the seed path's REST caller answers 500 — see
# `selfhost_api.py::POST /onboarding/seed` and
# `hosted_api.py::POST /v1/onboarding/seed`).
#
# ⛔ WHAT THIS LANE CLAIMS, PER RELATION — not one global claim, because one
# global claim would be false. ``structural_edge_carrier`` answers it from the
# ONE set the replay engines read, and both halves are MEASURED (an embedded
# journal-backed SDK, two endpoints, `rebuild_all` from the journal):
#   * **snapshot-carried** — `DERIVABLE_STRUCTURAL_RELS` (`aboutSubject`,
#     `aboutObject`, `aboutEvent`, `aboutPoint`, `aboutDocument`,
#     `extractedFrom`): the endpoint Point's immutable snapshot
#     (`aboutEntities` / the `extractedFrom` prop, resurrected by
#     `entities._upsert_point_edges` at replay) is a carrier, so an edge minted
#     HERE is resurrected when that snapshot already carries the same link —
#     which is how the sanctioned writers write it (`create_point(aboutSubject=
#     …)`, `extractedFrom=…`). ⚠️ The snapshot half is NOT the only carrier for a
#     bare `about*` mint any more: since #9553 the record half carries it too
#     (`RECORD_CARRIED_STRUCTURAL_RELS` below), so "a bare assertion on a node
#     whose record never held it" is carried by the record rather than lost.
#   * **record-carried** — every predicate in `RECORD_CARRIED_STRUCTURAL_RELS`,
#     via `structural_edge_record_carrier`: the mint journals a flat
#     `DirectEdgeCreated` (`related`, `dependsOn`, `ownedBy`, `managedBy`,
#     `authoredBy`, `memberOf`, `hasMember`, `holdsRole`, `reportsTo`,
#     `participatesIn`, `hasPart`, `performs`, `produces`, `uses`,
#     `wasDerivedFrom`, `references`, plus the epistemic-targeted `about*`:
#     `aboutSubject`, `aboutObject`, `aboutEvent`, `aboutPoint`, `aboutAction`).
#     ⚠️ Of those five, the FIRST FOUR are snapshot-carried as well; `aboutAction`
#     is NOT (`DERIVABLE_STRUCTURAL_RELS` omits it, and the snapshot cascade has no
#     `aboutAction` entry) — the RECORD half is its only carrier, which is exactly
#     why it is named in the supersede residual below. MEASURED after this landed: mint them live
#     and `rebuild_all` restores each one (they used to report `edges: 0`), and
#     the record's `relation` table gains one row each — the same family, one
#     carrier, both routes.
#   * **Carried per write, and one shape DECLINED** —
#     `SOURCE_ENDPOINT_CANONICAL_RELS` (`aboutDocument`, `aboutSource`): the
#     producer journals both when their endpoints are epistemic (measured), and
#     declines their CANONICAL shape — a `:Source` endpoint, which the pass-2b
#     sweep (`EPISTEMIC_LABELS` on `p.id`) cannot address — warning once. Making
#     that shape addressable needs a FOLD change (a url-keyed Source branch), not
#     a producer: `#9834`, declared in `04-projection.md` §3 and doc 08 §R5.
#
#     ⚠️ Per-WRITE caveats, not per-relation ones: a `references` whose SOURCE is
#     a `:Source` (the `link_source_to_entity` shape) is declined the same way
#     and REPORTED through `projection._log_identity_skip` (`drops_record=True`,
#     which also RECORDS it so a run boundary can count what a rebuild will not
#     restore); a SELF-EDGE is skipped (the fold's `E2E-11.6` no-self contract)
#     and warned once per relation by `TortoiseSDK._record_structural_edge`.
#     Before `#9553`, EVERY one of these was uncarried and silent; the point of
#     the ledger is that the remaining gaps are named.
#
# ⛔ WHAT THIS LANE DOES NOT COVER — stated, so a reader does not infer it from
# a green suite. `_EdgeHandlers` holds OTHER edge-minting verbs; they are not
# this lane and deliberately keep their own shape (each reason below is
# MEASURED at this head, not inferred from a name):
#   * `_create_about_edges` / `wire_about_link` — the NAME-resolved `about*`
#     path: the caller resolves a name through the Subject→Object→Event→Source→
#     Point cascade and the resolved decision is JOURNALLED (`resolve_about_link`,
#     `EventRecorded`'s replay links, #8398), so their write belongs to their
#     journalling door's scope — the live-scoped shape, not this one.
#     `wire_about_link` also MINTS a `:Subject` stub (a node), so an edge lane
#     must not authorize it. ⚠️ They are NOT refusal-safe today either: the
#     about links are written AFTER `_live_write_scope("create_entity")` closes
#     (`sdk.py`'s about path), so `#9124` would refuse them — measured. That is a
#     residual this lane discloses and does NOT absorb; `#9124` must census it.
#   * `_link_source` / `link_source_to_entity` / `link_source_to_event` /
#     `link_source_to_legacy_event` — the PROVENANCE lane: they `MERGE` a
#     `:Source` NODE and stamp `r.sourceVersion` (a `:Source` mint under an edge
#     lane would be the `#9068` shape). ⚠️ They are NOT refusal-safe today
#     either, exactly like the about lane above: the write funnels through
#     `self.g.query` in `_link_source` with no scope in flight, and its live
#     callers — `create_entity`'s `extractedFrom` leg and
#     `link_source_to_entity` (reached from the REST surface) — issue it AFTER
#     their enclosing scope closes, so `#9124` would refuse them. Stated as a
#     residual this lane does NOT absorb: `#9124` must census it.
#   * `_sync_tags` — `TAGGED` edges plus a `:Tag` MERGE, written from
#     `create_point`'s own declared scope.
#   * `_create_edges` / `replay_about_links` — the FOLD: they run inside
#     `_live_write_scope` / `_replay_write_scope` already.
# `tests/test_9079_edge_lane_declared.py::test_every_edge_minting_verb_is_
# declared_or_stated` enumerates those verbs from this module's own AST, so a
# NEW one fails the suite instead of appearing undeclared.
#
# ⛔ AND `references` KEEPS ITS OWN SHAPE. The generic verb writes that edge
# BARE: no `sourceVersion` anchor, and a later `link_source_to_*` `MERGE`
# cannot repair it because its stamp is `ON CREATE`-only (`#5213` option 3;
# §9.6; pinned by `tests/test_references_boundary_5213.py`). This lane does not
# route it through the anchor and does not claim the anchor for it.
#
# ⚠️ THE DECLARATION IS NOT A BYPASS. It is a per-call `with` block — no
# instance attribute, constructor argument or env var can set it — and it wraps
# the MUTATING statement only, so a read on this path is not swept into a lane
# it does not belong to, and a write that does not go through `create_edge`
# still fails closed.
# ═══════════════════════════════════════════════════════════════════════════

#: The declared lane name. A refusal never prints it (a refusal fires when NO
#: reason is in flight), so this value is what a test reads back AT THE WRITE
#: and what the projection's declaration ledger cites — stable on purpose, so a
#: reader can grep it.
STRUCTURAL_EDGE_WRITE_LANE = "structural-edge.derived"


def structural_edge_carrier(rel: str) -> str | None:
    """The SNAPSHOT carrier an edge of relation ``rel`` has, if any.

    ``"endpoint-snapshot"`` — the endpoint Point's own record is the carrier
    (`DERIVABLE_STRUCTURAL_RELS`, resurrected by
    `entities._upsert_point_edges`), so an edge the lane mints survives a
    rebuild when that record already carries the same link.

    ``None`` — the endpoint snapshot carries no such link. ⛔ THIS IS NOT "no
    carrier at all" any more (#9553 class 4): :func:`structural_edge_record_carrier`
    answers the RECORD half. Read the two together; a `None` here reads "no
    snapshot carrier", never "nothing carried it".

    ⚠️ And do NOT read the sibling as "every write is carried": it answers
    `"record"` for the whole lane write set (`RECORD_CARRIED_STRUCTURAL_RELS`),
    while the EMISSION is per write and declines a `:Source` endpoint —
    `SOURCE_ENDPOINT_CANONICAL_RELS` names the two predicates whose canonical
    shape that hits (`aboutDocument`, `aboutSource`). The function answers per
    CARRIER; the decline is `_record_addressable`'s, reported through
    `projection._log_identity_skip`.

    DERIVED from ``DERIVABLE_STRUCTURAL_RELS`` — the one set the replay engines
    read — rather than a second hand-maintained table, so adding a rel to that
    set moves this answer with it and the lane's per-relation claim cannot
    drift from replay's actual behaviour.
    """
    return "endpoint-snapshot" if rel in DERIVABLE_STRUCTURAL_RELS else None


#: The node labels a `DirectEdgeCreated` descriptor's endpoints can address on
#: REPLAY, restated from the fold rather than guessed: pass-2b's
#: `DirectEdgeCreated` leg sweeps exactly `EPISTEMIC_LABELS` (`Point`, `Subject`,
#: `Object`, `Event`) and probes `p.id IN $ids` (`projection/__init__.py`, "fold
#: `DirectEdgeCreated`"). A `:Source` endpoint is therefore NOT addressable — it
#: is url-keyed and outside the sweep — which is why the lane declines to journal
#: a write whose endpoint resolves to one, instead of filing a record the
#: journal-sourced rebuild cannot consume (a record RICHER than the graph, the
#: one direction Interface A forbids).
RECORD_ADDRESSABLE_EDGE_LABELS = frozenset(
    {"Point", "Subject", "Object", "Event"})

#: The same four labels as an ORDERED tuple. `EPISTEMIC_LABELS`
#: (`tortoise/live.py`) is the fold's sweep order — Point-first — and the two
#: must agree, because a node carrying two labels must resolve to the SAME one
#: on the write, the transfer and both replay routes (the first-wins rule
#: `_epistemic_label` applies in `sdk.supersede_point`'s 2a leg). Pinned to the
#: fold's own tuple by `tests/test_9836_id_addressed_repoint.py`.
EPISTEMIC_LABEL_ORDER = ("Point", "Subject", "Object", "Event")

#: label → the property an ID-ADDRESSED repoint target is keyed by, per label
#: (#9836). The pair the descriptor carries is ``(label, target[prop])``, and the
#: SAME pair decides the graph's replay match
#: (`resolve_structural_target(..., by_id=True)`) and the key the record resolves
#: (`record_projection._endpoint_resolution` → the label's canonical column), so a
#: label listed here cannot be addressed one way on the graph and another way in
#: the record.
#:
#: ⛔ ``Event`` → ``eventId``, EVERYTHING ELSE → ``id``, and the split is the
#: tree's own, not this table's opinion:
#:
#:   * ``projection._CANONICAL_ENTITY_ID_PROPS`` declares ``eventId`` as the
#:     Event family's PRIMARY identity and ``id`` for Point/Subject/Object;
#:   * the record files an Event under ``node_key``, and
#:     ``public.event.node_key = event_id`` is a NOT NULL CHECK
#:     (``chk_event_node_key_is_contract_key``), while ``node_key`` IS the id for
#:     Point/Session and the ``id`` column is the key for Object/Subject (#9881);
#:   * ``live.EPISTEMIC_ID_KEYS`` says the two Event keys are NOT
#:     interchangeable and that the coalesced value is not an identity.
#:
#: The Event row is the one that changes behaviour, and it changes it from broken
#: to correct: `entities._event_guarded_merge`'s SUFFIX branch MERGEs on the
#: suffixed ``eventId`` while the props keep ``id = <candidate>`` — so two distinct
#: Events share one ``id``. Keying by ``id`` made the graph's replay see 2 nodes
#: (refusal → the descriptor and its edge were dropped) and pointed the record at
#: the OTHER Event's row; keying by ``eventId`` names exactly one node on both
#: routes. `Source` is deliberately ABSENT: it is url-keyed and outside the
#: record's id sweep (`RECORD_ADDRESSABLE_EDGE_LABELS`), so a descriptor for one
#: is never emitted.
ID_ADDRESSED_TARGET_ID_PROPS: dict[str, str] = {
    "Point": "id",
    "Subject": "id",
    "Object": "id",
    "Event": "eventId",
}

#: The structural predicates this lane now JOURNALS a `DirectEdgeCreated` for
#: (#9553 class 4) — the whole lane write set, as a LITERAL: derived from
#: `STRUCTURAL_EDGE_LANE_PREDICATES`, every assertion about it would be a
#: tautology, so the drift test compares the two and adding a predicate to either
#: mint must classify it here, by hand.
#:
#: ⛔ "CARRIED" IS A PRODUCER STATEMENT, NOT A PROMISE ABOUT EVERY WRITE. The
#: emission is per WRITE and declines an endpoint the replay fold cannot address
#: (`:data:`RECORD_ADDRESSABLE_EDGE_LABELS` — a `:Source`, or a node whose value
#: is not its `id`), warning once per relation. Which predicates that hits most
#: is named in :data:`SOURCE_ENDPOINT_CANONICAL_RELS`.
RECORD_CARRIED_STRUCTURAL_RELS = frozenset({
    "performs", "produces", "uses", "authoredBy", "ownedBy", "managedBy",
    "hasMember", "holdsRole", "memberOf", "reportsTo", "participatesIn",
    "hasPart", "related", "dependsOn", "references", "wasDerivedFrom",
    "aboutSubject", "aboutObject", "aboutEvent", "aboutPoint", "aboutAction",
    "aboutDocument", "aboutSource",
})

#: The lane predicates whose CANONICAL target is a `:Source`, and which are
#: therefore usually declined at the write — NAMED, and deliberately NOT excluded
#: from :data:`RECORD_CARRIED_STRUCTURAL_RELS`, because the exclusion was
#: measurably wrong in both directions and a peer review caught it:
#:
#:   * `create_edge` type-checks ONLY `aboutDocument`'s target
#:     (`about_document_target_reason`), so an `aboutSource` between two
#:     epistemic endpoints IS journaled and record-carried (measured);
#:   * `create_about_edge` type-checks none, so `aboutDocument` between two
#:     Points is journaled too (measured).
#:
#: So these two are carried exactly like every other predicate, and their
#: CANONICAL shape (`(Point)-[:aboutDocument]->(a document :Source)`) is declined
#: per write with the reason. Making that shape addressable is a FOLD change (a
#: url-keyed `:Source` branch in pass-2b) — `#9834`.
#:
#: `TAGGED` is the ONE family with no carrier at all: no record `tag` table for
#: `relation.tgt_key`'s composite FK, and `_sync_tags` reaches neither this lane
#: nor this producer (nor a warning) — declared in `04-projection.md` §3 and
#: doc 08 §R5.
SOURCE_ENDPOINT_CANONICAL_RELS = frozenset({"aboutDocument", "aboutSource"})

assert STRUCTURAL_EDGE_LANE_PREDICATES == RECORD_CARRIED_STRUCTURAL_RELS, (
    "every predicate this lane can write must be carried by the producer — a new "
    "one needs the emission classified, not silently left out")
assert SOURCE_ENDPOINT_CANONICAL_RELS <= RECORD_CARRIED_STRUCTURAL_RELS


def structural_edge_record_carrier(rel: str) -> str | None:
    """The RECORD carrier this lane emits for an edge of relation ``rel``.

    ``"record"`` — the mint journals a flat `DirectEdgeCreated` descriptor
    (`{src, tgt, edge_type, source_label, target_label}`) next to the edge, which
    pass-2b re-creates on a journal-sourced rebuild and which
    `record_projection` materialises as a `relation` row (the descriptor is
    §4.4's canonical shape, shared with `DirectEdgeRepoint`).

    ``None`` — not a predicate this lane writes.

    ⚠️ Per RELATION, and the emission is per WRITE: a write whose endpoint is not
    :data:`RECORD_ADDRESSABLE_EDGE_LABELS` (a `:Source`; a node whose value is not
    its `id`) is DECLINED at the mint and reported through
    `projection._log_identity_skip` (`absent_is_ordinary=False`,
    `drops_record=True`), which also RECORDS it. `"record"` therefore means
    "the producer carries this relation for the endpoints the record can
    address", not "every instance of it is carried" — and
    :data:`SOURCE_ENDPOINT_CANONICAL_RELS` names the two whose canonical shape is
    always declined.
    """
    return "record" if rel in RECORD_CARRIED_STRUCTURAL_RELS else None


#: The structural rels `TortoiseSDK.supersede_point`'s 2b leg MOVES from the old
#: point to the successor live. Mirrored from the writer's own
#: `sdk.SUPERSEDE_STRUCTURAL_RELS` (the single source of truth the MCP dry-run
#: preview also imports) and pinned equal by
#: `tests/test_9553_class4_structural_edge_records.py::test_the_supersede_transfer_set_matches_the_writer`.
#:
#: ⛔ WHY IT LIVES HERE. `rebuild_all`'s `DirectEdgeCreated` fold must not skip a
#: creation whose endpoint was superseded UNLESS a supersede actually MOVED that
#: edge — and leg 2b moves only the OUTGOING side. The fold cannot import
#: `tortoise.sdk` (cycle), so the set is mirrored and drift-pinned.
SUPERSEDE_TRANSFERRED_RELS = frozenset({
    "aboutSubject", "aboutObject", "aboutAction", "aboutEvent",
    "aboutPoint", "aboutDocument", "extractedFrom", "wasDerivedFrom",
})

#: The structural rels a supersede TRANSFERS whose target is addressed by the
#: node's OWN `id`, not by a name/url natural key (#9836).
#:
#: ⛔ WHY THIS IS A SEPARATE FAMILY, and not two more rows in
#: :data:`STRUCTURAL_REL_LABELS`. That map is `rel -> ONE label` and
#: :func:`stub_key` reads the label's natural key off the target's properties
#: (`name` for Subject/Object/Event/Point, `url` for Source). Both facts hold for
#: the snapshot-derivable family and hold for NEITHER of these two:
#:
#:   * `aboutAction` is `Point -> Point` (ONTOLOGY §3.2, the legacy pre-v3.0
#:     Action edge) and a Point has no `name` — `stub_key` cannot mint a key for
#:     it, and the graph's `MATCH (x:Point {name:$key})` arm would never resolve
#:     one, which is the `aboutPoint` defect #10313 owns;
#:   * `wasDerivedFrom` is `Object -> Object` per ONTOLOGY §3.2, and its writers
#:     do NOT type-check (`create_derivation` and `create_edge` accept any
#:     resolvable pair), so its target label is not a function of the rel at all.
#:
#: Both are nevertheless MOVED by `supersede_point`'s 2b leg (they are in
#: :data:`SUPERSEDE_TRANSFERRED_RELS`) and, because they are not snapshot-
#: recreatable, neither `rebuild_all` pass-2 nor the `DirectEdgeCreated` fold puts
#: them back at the old point. So the whole 2b contract — journal the move,
#: replay the move — can be extended to them by addressing the target the way the
#: rest of the tree addresses an EPISTEMIC NODE: by its `id`, label-agnostically
#: (the same sweep the 2a-DIRECT leg runs, `EPISTEMIC_LABELS` on `p.id`). The
#: descriptor therefore carries the label it OBSERVED LIVE in `target_label`,
#: which is the field's own meaning on the structural route.
#:
#: ⛔ These are NOT in :data:`DERIVABLE_STRUCTURAL_RELS` and must never be added
#: there: that set is the snapshot-resurrection set, and a member is an edge
#: pass-2 re-creates at OLD from the point's immutable snapshot. Neither of these
#: is ever snapshot-recreated, so claiming membership would make the replay's
#: delete-leg guard reason about a resurrection that does not exist.
ID_ADDRESSED_STRUCTURAL_RELS = frozenset({"aboutAction", "wasDerivedFrom"})

#: Every structural rel whose 2b transfer carries a `DirectEdgeRepoint`
#: descriptor — the SNAPSHOT-DERIVABLE family (key-addressed) plus the
#: ID-ADDRESSED family (#9836). This is the REPLAY leg's discriminator: the branch
#: that reads a descriptor's `tgt` as a target key rather than as a bare node id.
STRUCTURAL_REPOINT_RELS = (DERIVABLE_STRUCTURAL_RELS
                           | ID_ADDRESSED_STRUCTURAL_RELS)


#: Logged once per relation, not once per write: the decline is a DECLARED gap
#: ("this endpoint class has no record carrier"), and a per-write line on the
#: `references`/`about*` fan-out would bury every other log in the process. The
#: reason is stated at the code site and in `04-projection.md` §3 — never left to
#: be found. Also used by `TortoiseSDK._record_structural_edge` for the self-edge
#: skip, so the two per-write declines share ONE once-per-relation register.
#:
#: ⚠️ NAMED DECLINES LIKE THE ENDPOINT ONE GO THROUGH `_log_identity_skip`, not
#: through this register: that reporter is the package's ONE seam for "a record
#: was not written because an endpoint could not be addressed", it also RECORDS
#: the skip (`record_identity_skip`) so a run boundary can count it, and a second
#: register would make the same event visible in one place and invisible in the
#: other (#9827's review asked for exactly this seam). This set is left for the
#: SELF-EDGE skip alone, which is not an identity defect — both ids are fine, the
#: fold's `E2E-11.6` contract refuses the shape.
_RECORD_GAP_WARNED: set[str] = set()


def _record_addressable(end: Mapping[str, Any]) -> bool:
    """Can a `DirectEdgeCreated` descriptor address this resolved endpoint?

    TWO conditions, both the fold's rather than this module's invention:

    * the LABEL is one pass-2b sweeps (:data:`RECORD_ADDRESSABLE_EDGE_LABELS`);
    * the value the MERGE anchored on IS that node's `id` property — the fold's
      `WHERE p.id IN $ids` probe, exact. An `Event` resolved through its
      `eventId` branch is addressable only when the node really carries `id`
      (`_upsert_event` sets both; an eventId-only legacy node does not), and a
      `:Source` never is.

    Reading `id` back from the resolved PROPERTIES is deliberate: the invariant
    "Event.id == eventId" is what `_upsert_event` writes, not what every node in
    every graph carries, and filing a descriptor for a node the fold will not
    find is exactly the record-richer-than-graph defect this gate exists to
    prevent.
    """
    if end.get("label") not in RECORD_ADDRESSABLE_EDGE_LABELS:
        return False
    props = end.get("properties") or {}
    return props.get("id") is not None and props.get("id") == end.get("value")


def _structural_edge_write_scope():
    """The §17.1 declaration for the structural-edge lane's ONE statement.

    Lazy import on purpose: `tortoise.projection` imports THIS module, so a
    module-level import would be circular (the same reason `state.py`'s
    ``_onboarding_write_scope`` imports late, #9121).
    """
    from tortoise.projection import _cache_write_authorized

    return _cache_write_authorized(STRUCTURAL_EDGE_WRITE_LANE)


class _EdgeHandlers:
    """Mixin: edge creation, about edges, source linking, edge stats."""

    def _record_structural_edges(self, rel: str, sources: list[dict],
                                 targets: list[dict]) -> None:
        """Hand the edges this mint just wrote to the RECORD producer (#9553).

        ONE producer for the lane's two mints, called with the endpoints
        `_resolve_entity` ALREADY returned — so the descriptor names the node the
        MERGE actually wrote to, not a re-resolution that a concurrent write could
        point elsewhere.

        The RECORDER is bound by the journal-owning door
        (`TortoiseSDK._get_proj` → `_bind_structural_edge_recorder`), because the
        journal handle lives there: a projection constructed on its own (a test,
        a replay engine, `FalkorProjection.from_uri`) has none and emits
        nothing, which is the pre-#9553 behaviour rather than a silent half-write.

        ⛔ AND A REPLAY NEVER EMITS, even on a BOUND projection. `apply()` and
        `rebuild_all`'s pass-2b drive `replay_about_links` → this very mint, on the
        SDK's own live projection — so without the guard a RECOVERY WRITE would
        append to the append-only journal it is reading: one `DirectEdgeCreated`
        per journaled link per rebuild, unbounded, built from the endpoints
        resolved AT REPLAY TIME (so any live↔replay drift becomes new durable
        truth). Measured before the guard: a journal of 4 records re-grew to 5, 6,
        7 over three successive `rebuild_all` runs. The journal is APPEND-ONLY; a
        rebuild reads it and never extends it — its standing is
        `docs/durability-posture.md`'s to state (`projection.replay_window_open`).

        ⛔ THE NEXT GATE IS THE FOLD'S OWN PROBE, not a guess. Pass-2b addresses a
        `DirectEdgeCreated` endpoint by sweeping `EPISTEMIC_LABELS` for `p.id IN
        $ids`; a `:Source` endpoint is outside that sweep, so journaling one would
        write a record the JOURNAL rebuild cannot consume while the RECORD rebuild
        can — the two routes disagreeing, which is the one thing this family is
        supposed to make impossible. Such a write is DECLINED (no partial
        descriptor) and REPORTED through the package's one skip reporter,
        `projection._log_identity_skip` (`absent_is_ordinary=False`,
        `drops_record=True`), rather than dropped in silence — the failure mode
        that let R5 sit. That seam also RECORDS the skip, so a run boundary can
        count what a rebuild will not restore instead of reading a log.
        """
        recorder = getattr(self, "_structural_edge_recorder", None)
        if recorder is None:
            return
        # Lazy import: `tortoise.projection` imports THIS module.
        from tortoise.projection import _log_identity_skip, replay_window_open

        if replay_window_open():
            # Expected and silent: a replay reconstructs a graph from records that
            # ALREADY describe these edges. Nothing is disclosed because nothing
            # is missing — the journal is the source, not a consumer.
            return
        for s in sources:
            for t in targets:
                for end in (s, t):
                    if not _record_addressable(end):
                        # `where` carries the reason, because the reporter's own
                        # message states the refused SHAPE (absent / non-str /
                        # NUL-bearing), and the shape is only half the story
                        # here: this endpoint may be perfectly well-formed and
                        # still unaddressable — a `:Source` is url-keyed and the
                        # fold sweeps `EPISTEMIC_LABELS` on `p.id` only.
                        _log_identity_skip(
                            f"structural edge {rel!r} {end.get('label')} "
                            "endpoint",
                            end.get("value"),
                            "id (record endpoint)",
                            where=(
                                "structural edge (mint): the fold sweeps "
                                f"{sorted(RECORD_ADDRESSABLE_EDGE_LABELS)} on "
                                "`p.id` only, so this endpoint is not "
                                "addressable — the edge is in the graph and no "
                                "`DirectEdgeCreated` is journaled (#9553 class "
                                "4, `04-projection.md` §3)"),
                            absent_is_ordinary=False,
                            drops_record=True,
                        )
                        break
                else:
                    recorder({
                        "edge_type": rel,
                        "src": s["value"],
                        "src_label": s["label"],
                        "tgt": t["value"],
                        "tgt_label": t["label"],
                    })

    def _create_edges(self, p: dict) -> None:
        """Create typed edges for an operator Point. Auto-creates stub nodes
        for missing source endpoints referenced by short IDs (#6713).
        Operator endpoints may carry any of the four epistemic labels
        :Point/:Subject/:Object/:Event (#7902, owner ruling #7813) — the
        existence checks and edge MERGEs match all four
        (#7902 fold-parity: live create_operator writes the same typed +
        INPUT edges this replay does)."""
        op = p.get("operator")
        if not isinstance(op, dict):
            # #331 (review r4): a truthy non-dict operator value must
            # degrade to no edges, not AttributeError in op.get().
            return
        # #331 (review r3): .get() — a malformed operator dict without
        # op_type/inputs must degrade to no typed edges, not KeyError.
        rel_type = {"NAND": "NAND", "IMPL": "IMPL",
                     "composedOf": "hasPart", "decomposesInto": "hasPart",
                     "contains": "hasPart", "wraps": "hasPart"}.get(op.get("op_type"))
        import logging as _logging
        _log = _logging.getLogger(__name__)
        # #7902: the operator endpoint may carry any of :Point/:Subject/
        # :Object/:Event (owner ruling #7813). The fold is the SHARED
        # live+replay writer, so its existence probes and edge MERGEs must
        # admit the same labels the live ``create_operator`` writer does —
        # otherwise a non-Point input is skipped (long id) or phantom-stubbed
        # (short id) on replay, diverging live from rebuilt.
        _epi_s = epistemic_disjunction("s")
        for idx, src in enumerate(op.get("inputs") or []):
            # #331 (review r4): non-string inputs members are malformed —
            # skip (len()/Cypher param would raise).
            if not isinstance(src, str):
                continue
            # ponytail: auto-create stub if source endpoint doesn't exist.
            # Short numeric IDs are orphan refs from cross-file wiring scripts.
            if len(src) < 20:  # short IDs (non-ULID) are suspect
                # #7369 review r5 (P1): the stub CREATE below passes `src` as a
                # plain VALUE position, not a MERGE key, so the parameter
                # boundary DEGRADES an unwritable id to None and mints
                # `CREATE (s:Point {id:null})` — a node with no identity that
                # no later MERGE can ever match, left behind on every replay.
                # An unwritable id is not a stub worth minting, so skip the
                # edge exactly as the per-instance cap path below does.
                from tortoise.projection import _writable_id
                if not _writable_id(src):
                    _log.warning(
                        "input source %r is not a writable identity (empty, "
                        "or NUL/lone-surrogate bearing) — stub not created, "
                        "INPUT edge skipped",
                        src,
                    )
                    continue
                exists = self.g.query(
                    "MATCH (s) WHERE " + _epi_s + " "
                    "AND s.id = $sid RETURN count(s) > 0",
                    params={"sid": src}
                ).result_set[0][0]
                if not exists:
                    # #329: bounded stub auto-creation — at the per-instance cap
                    # we STOP creating stubs and SKIP the edge to the missing
                    # node (fail-safe: no partial edge, no crash, warning logged).
                    if getattr(self, "_autocreated_stubs", 0) >= getattr(
                            self, "_max_autocreated_stubs", 500):
                        _log.warning(
                            "stub auto-creation cap reached (%d) — skipping "
                            "missing source %r (edge not created)",
                            getattr(self, "_max_autocreated_stubs", 500), src,
                        )
                        continue
                    self.g.query(
                        "CREATE (s:Point {id:$sid}) "
                        "SET s.content='[missing]', "
                        "    s.is_operator=false",
                        params={"sid": src}
                    )
                    self._autocreated_stubs = getattr(self, "_autocreated_stubs", 0) + 1
            else:
                # #1917: long IDs are presumed real ULIDs — never stub them.
                # A source that doesn't resolve would silently match nothing
                # in the MERGE below; warn instead of dropping the input.
                exists = self.g.query(
                    "MATCH (s) WHERE " + _epi_s + " "
                    "AND s.id = $sid RETURN count(s) > 0",
                    params={"sid": src},
                ).result_set[0][0]
                if not exists:
                    _log.warning(
                        "input source %r (id length %d >= 20) does not "
                        "resolve to an existing epistemic node — INPUT edge "
                        "skipped",
                        src, len(src),
                    )
                    continue
            if rel_type is not None:
                # Known op_type → typed edge + reverse INPUT
                self.g.query(
                    f"MATCH (o:Point {{id:$oid}}), (s) "
                    f"WHERE {_epi_s} AND s.id = $sid "
                    f"MERGE (o)-[:{rel_type} {{idx:$idx}}]->(s) "
                    f"MERGE (s)-[:INPUT {{idx:$idx}}]->(o)",
                    params={"oid": p["id"], "sid": src, "idx": idx},
                )
                # #5048: a MITIGATION point's edge is BIDIRECTIONAL live —
                # ``mitigate_operator`` writes ``(m)-[:IMPL]->(op)`` AND the
                # canonical reverse ``(op)-[:mitigated_by]->(m)`` — but the
                # ``OperatorAdded`` payload only names the IMPL direction, so
                # the reverse edge rode no record and the fold above rebuilt
                # only one half. ``compute_operator_weight`` reads
                # ``mitigated_by``, so a rebuild silently reverted w_eff to the
                # undecayed base (measured 0.5 -> 1.0, 1 -> 0 edges).
                #
                # The gate is the record's OWN stated non-operator identity
                # AND a strength a live writer could have written — the two
                # shared predicates, so the identity half and the edge half of
                # this record's replay agree by construction. A mitigation is a
                # NON-operator Point that carries an operator EDGE descriptor
                # solely so this fold can rebuild ``(m)-[:IMPL]->(op)``
                # (`#4937` — ``mitigated_by`` is canonical only from a
                # mitigation Point, NEVER a generic operator).
                #
                # Neither half alone is enough. The identity must be EXPLICITLY
                # false: ``not p.get("is_operator")`` is ALSO true when the key
                # is ABSENT, and the EventAPI / extractor / ingest producer
                # (`api.py::_point`) emits an ``OperatorAdded`` with no
                # ``is_operator`` at all — that widened every generic IMPL
                # operator on the main ingest path. And the strength must be
                # USABLE: the descriptor is attachable by any low-level producer
                # (``EventAPI.add_point(**fields)``), so descriptor-alone minted
                # the edge for a payload that was never a mitigation, while a
                # present-but-malformed strength is either dropped by the writer
                # or ignored at read time (#6151: the read degrades an unusable
                # strength to the fallback rather than raising). ``is False`` also excludes the
                # SDK's generic operator (``is_operator: true``) and
                # ``rebuild_all``'s #548 graph-only synthesis, whose
                # ``op_type``-bearing node may carry no flag.
                # ``rel_type == "IMPL"`` pins WHICH relation the record names
                # (the live writer hardcodes IMPL); the identity pins whether
                # its source is a mitigation. `o` is the mitigation Point, `s`
                # the operator it damps — matching the live writer's
                # direction, and ONTOLOGY §3.9's ``(op)-[:mitigated_by]->(m)``.
                #
                # NOT gated on ``s.is_operator = true``, deliberately, though
                # §3.9's hard rule (#2315) wants exactly that and
                # ``mitigate_operator`` enforces it. Measured: this fold is the
                # SHARED live+replay writer, and in `#329`'s stub case (a
                # mitigation whose short-id ``src`` does not resolve yet, so the
                # stub above is created ``is_operator=false``) the two engines
                # reach this MERGE with DIFFERENT origins — ``apply()`` folds the
                # mitigation before the operator exists and never re-attempts
                # the edge when it arrives, while ``rebuild_all`` pass-1a hoists
                # the operator first. The predicate therefore made rebuild mint
                # ``(op)-[:mitigated_by]->(m)`` where apply minted nothing: a
                # live!=replay divergence, and that invariant is the whole point
                # of this fold. The rule it would enforce concerns an edge
                # ONTOLOGY itself calls dead structure (no EP factor addresses a
                # non-operator), so the fold keeps its parity and the rule stays
                # with the SDK writer. Recorded on #5048 with the measurement.
                if (rel_type == "IMPL" and is_non_operator_payload(p)
                        and has_usable_mitigation_strength(p)):
                    self.g.query(
                        "MATCH (o:Point {id:$oid}), (s) "
                        "WHERE " + _epi_s + " AND s.id = $sid "
                        "MERGE (s)-[:mitigated_by]->(o)",
                        params={"oid": p["id"], "sid": src},
                    )
            else:
                # Unknown op_type → INPUT edge only (convention: source → operator)
                self.g.query(
                    "MATCH (o:Point {id:$oid}), (s) "
                    "WHERE " + _epi_s + " AND s.id = $sid "
                    "MERGE (s)-[:INPUT {idx:$idx}]->(o)",
                    params={"oid": p["id"], "sid": src, "idx": idx},
                )

    def _sync_tags(self, point_id: str, tags, *, sweep_orphans: bool = False) -> None:
        """Reconcile `TAGGED` edges for a point against a tags value (#485).

        ONE routine for BOTH writers (#2897): the SDK live path
        (`create_point` / `update_point`) delegates here, and the replay fold
        (`_upsert_point_edges` pass 2, and `_revise_point` for a tag EDIT)
        calls it too — so live and replay can never drift into the raw-list
        half-restore the old replay refused. The `:Tag`/`TAGGED` half is
        SNAPSHOT-DERIVABLE from the Point's own `tags` property, exactly like
        `extractedFrom` → `_link_source` (#2489); it is never journaled
        separately.

        Idempotent: MERGE creates any missing :Tag nodes + TAGGED edges, and
        edges to tags no longer in the list are deleted.

        The normalisation is TOTAL for the shapes the signature allows — a
        `list`, or a `tuple` (the engine stores both as an array, cf.
        `_is_persistable_prop_value`) — and a FALSY value, which both callers
        normalise to `[]` with the same `or []` expression (`props["tags"] or
        []` live, `ev["tags"] or []` in `_revise_point`), so `None`/`""` CLEAR
        the edges. The live create/update gates accept a tuple as well as a
        list (#8317 review R2), so a tuple round-tripping through the journal
        as a list does not make the replay invent edges live never wrote.

        ⚠️ BOUND, stated rather than implied — a TRUTHY NON-ARRAY (`tags` set to
        a number, a string, a dict). The PROPERTY halves AGREE (`_stated_tags`
        writes a persistable scalar verbatim; `_revise_point` clears a value the
        store cannot hold, which is what `#7369`'s null-degradation leaves the
        live write too), but the EDGE halves DIFFER, in this direction:

          * LIVE KEEPS the node's EXISTING edges — this guard returns early.
            That is deliberate and PINNED:
            `tests/test_sdk.py::test_update_non_list_tags_no_crash` ("Non-list
            tag values match create_point: property set, no edge sync") asserts
            the old edge is still there.
          * A `rebuild_all` REMOVES them: the graph is wiped, and the DEFERRED
            derived pass (`_upsert_point_edges`) reads only the node's FINAL
            value, so it mints none and a membership the live graph still
            answers for is gone.
          * `rebuild(EventLog)` KEEPS them: it wipes too, but it replays
            record-by-record through `apply`, so the creation's list-sync runs
            BEFORE the edit turns the property into a non-array and no later
            sweep revisits it.

        So on this input the two replay arms disagree with each other AND with
        live; #8333 owns both halves. The three-arm test in
        `test_3817_tags_rebuild_durability.py` covers the tag-LIST shape only.

        Which half is right is not in doubt. The `tags` PROPERTY is the SOURCE
        of this derived half (that is what "snapshot-derivable" means, #2489's
        shape), so a non-array derives NO edges and the rebuilt graph is the
        self-consistent one — LIVE is the half carrying a stale derived
        artifact. That is the same stale-edge class #485 fixed for a list
        ("update_point previously set the property but left edges stale, so
        query_points_by_tag missed updated points"), left in place for the
        non-list case.

        The tightest fix is HERE and is one line — normalise a non-array to `[]`
        instead of returning early, making the edge half a pure function of the
        NORMALISED property value on every path (live create, live update,
        replay revise, the derived pass) — so `create_point` is unaffected (a
        fresh point has no edges to clear). It is deliberately NOT taken here:
        it alters a LIVE write for an input the public signature excludes
        (`tags: list[str] | None`) and reverses a pinned test, which is not a
        change to bundle into a durability fix. Filed with the measurement as
        **#8333**, which owns it and the pin.

        The drop is REPORTED, not silent: for a value the store cannot hold,
        `check_consistency` names `tags` in `uncarried_journal_fields` (see
        `consistency._uncarried`), so the check stays green on a byte-faithful
        replay without hiding that the property half was not carried.

        ``sweep_orphans`` (#8854 review P3) forces the orphan-`:Tag` GC below to
        run even when THIS invocation deleted no edge. It exists because this
        routine is now retried as a whole (#7405): a first attempt can land the
        `DELETE r` and then be ABORTED on the GC statement, and the retry's
        re-read of `stale` then finds nothing left to delete — so a
        per-invocation `removed` counter would skip the sweep and leave an
        orphan `:Tag` that `list_tags` reports as a `count: 0` entry while a
        rebuilt graph, which replays this routine from a clean state, has none
        (live != replay). A caller that can DELETE edges on a RETRIED path —
        `update_point`'s tag sync — passes ``True`` FROM THE SECOND ATTEMPT ON
        (``sweep_orphans=(_sync_attempts > 1)`` in `sdk.py`), so a first attempt
        that drops NO edge pays no graph-wide `:Tag` scan. `sweep_orphans`
        FORCES the sweep; it does not gate it — the GC still runs whenever this
        call ``removed`` an edge, so `update_point(pid, tags=[])` on a point
        that held a tag DOES scan on its first attempt (its own `DELETE r`
        already left the orphan this call must clear).
        ``create_point`` keeps the default: a fresh point has no stale edges, so
        the scan stays off its hot path.

        ⛔ The limit of that saving, stated so nobody rediscovers it: an orphan
        ``:Tag`` left by an EARLIER interrupted call is collected only by a
        later call that removes an edge or retries — a plain tagged update on a
        point that already held the tag does not scan. `list_tags` can therefore
        show a ``count: 0`` entry that a rebuilt graph (which replays from a
        clean state) does not have, until such a call happens. Pinned both ways
        by ``test_the_orphan_sweep_is_a_retry_only_cost``.
        """
        if not isinstance(tags, (list, tuple)):
            return
        for tag in tags:
            self.g.query(
                "MATCH (p:Point {id:$pid}) "
                "MERGE (t:Tag {name:$tag}) "
                "MERGE (p)-[:TAGGED]->(t)",
                params={"pid": point_id, "tag": tag},
            )
        # Delete edges to tags no longer in the list (diff via graph read to
        # avoid list-parameter IN-clause portability concerns on FalkorDB).
        stale = self.g.query(
            "MATCH (p:Point {id:$pid})-[:TAGGED]->(t:Tag) RETURN t.name",
            params={"pid": point_id},
        ).result_set
        removed = 0
        for row in stale:
            if row[0] not in tags:
                self.g.query(
                    "MATCH (p:Point {id:$pid})-[r:TAGGED]->(t:Tag {name:$tag}) "
                    "DELETE r",
                    params={"pid": point_id, "tag": row[0]},
                )
                removed += 1
        # GC orphaned :Tag nodes when the sync actually removed edges — tag
        # removal can leave a Tag with no TAGGED referrers (#485). Skipped on
        # create_point (no stale edges there), so new-point creation never pays
        # the scan. `sweep_orphans` forces it on a RETRIED reconciler whose
        # earlier attempt already landed the DELETEs (see the docstring).
        if removed or sweep_orphans:
            self.g.query("MATCH (t:Tag) WHERE NOT (t)<-[:TAGGED]-() DELETE t")

    #: The cascading name path's label/rel order — Subject → Object → Event →
    #: Source → Point, then a Subject stub. Declared ONCE so the resolver, the
    #: live write and the replay cannot drift apart: this order IS the live
    #: semantics (first match wins), and it is what ``stub_key``'s replay keys
    #: were chosen for.
    _ABOUT_NAME_CASCADE = (
        ("Subject", "aboutSubject"),
        ("Object", "aboutObject"),
        ("Event", "aboutEvent"),
        ("Source", "aboutDocument"),
        ("Point", "aboutPoint"),
    )

    def resolve_about_link(self, entity_name: str
                           ) -> tuple[dict | None, list[int] | None]:
        """RESOLVE — without writing — the link the cascading name path produces.

        #8398 (review P1-2): ``_create_about_edges`` is the rel-IGNORING
        Subject→Object→Event→Source→Point cascade, so what it wires is not
        necessarily the caller's ``rel`` at all: ``create_event(aboutObject=
        "Acme")`` with no ``Acme`` node in the graph wired **aboutSubject** to a
        fresh Subject stub. Journaling that as ``how:"name"`` and re-running the
        cascade at replay made replay ask a LATER graph — once the caller
        registered the Object, the cascade answered ``aboutObject``. Measured on
        the shipped code: live ``[['aboutSubject', ['Subject'], 'Acme']]`` vs
        replay ``[['aboutObject', ['Object'], 'Acme']]`` — a different rel AND a
        different node, reported as success because ``_count_about_out`` only
        asks "is the source joined to anything".

        So the RESOLVED decision is journaled, pinned to the write that produced
        it. Returns ``(journal_link, target_internal_id)``:

        ``journal_link`` — ``{"rel", "how": "id", "targets": [<live endpoint>],
        "target_label", "resolve_key", "mint_if_absent"}``, the same ``how:"id"``
        vocabulary as an id-resolved link, plus what replay needs to repeat this
        exact decision instead of re-deriving it:

        * ``rel``/``target_label`` — the cascade's answer, PINNED. Replay must
          never re-run the cascade: it asks a different graph.
        * ``resolve_key`` — the natural key the live match used (``name``, or
          ``url`` for a document ``:Source``; ``stub_key``'s map, so live and
          replay agree on WHICH key identifies the label).
        * ``mint_if_absent`` — may replay CREATE the endpoint? True only when
          the endpoint live resolved IS the Subject stub (its id equals its
          name — the ``ON CREATE SET s.id=$name`` construction), because that
          is the one construction that may legitimately mint. A Subject live
          merely MATCHED is not a mint (see ``_resolve_about_edge_target``), and
          every other label is resolve-only live, so minting one at replay would
          fabricate a node live never had.

          ⚠️ **#3590 briefly retired the construction this flag describes, and
          that retirement was WITHDRAWN (#8398).** ``_mint_subject_stub`` was
          made resolve-only at that head, which made this flag inert on the
          Subject arm and left a journaled ``mint_if_absent: true`` replaying as
          a bare resolve — so a name no producer registered lost its link on
          every replay, including the ``how:"name"`` records an append-only
          journal cannot repair. The helper mints again, so the flag means what
          it says on BOTH arms (its own docstring records the reasoning and the
          shipped pins that depend on it). It is journaled ``true`` on the
          Subject-stub fallback below because that is the RECORD shape
          (``tests/test_8398_aboutobject_replay.py`` pins it).

        ``target_internal_ids`` — the FalkorDB internal ids the LIVE write must
        address, so the journal and the write come from ONE resolution rather
        than two reads that could disagree. A LIST because a name — or a
        document ``title`` — may legally have several carriers, and EVERY one of
        them is journaled in ``targets``: BOTH arms of ``_resolve_about_edge_target``
        (the name-keyed labels and the url-or-title-keyed document ``Source``)
        resolve all their carriers, so no arm pins an arbitrary row and live
        writes exactly what the record says. ``None`` for the stub fallback (no
        node yet). ⛔ It MUST NEVER be journaled: internal ids die at rebuild.

        Returns ``(None, None)`` when the cascade wires nothing (an unwritable
        name — refused and logged, #7369).

        ⚠️ **Resolving PRE-fold means the ``Event`` arm can no longer match the
        record being written — DELIBERATELY (#8398, 5th review P2-2).** This call
        happens before the ``Event`` exists, so a name equal to the Event's OWN
        name (``create_event("SelfName", "event", aboutSubject="SelfName")``)
        now resolves to the ``:Subject`` arm — ``(Event)-[:aboutSubject]->
        (:Subject {id:"SelfName"})``, which SURVIVES a rebuild — where
        ``origin/main`` wired a self-``aboutEvent`` edge ``(Event)->(Event)``
        that was never journaled and which ``rebuild_all`` therefore LOSES. The
        change in live wiring is accepted: the retired self-edge could never
        survive a rebuild, and it is traded for a link whose record and edge
        come from ONE resolution (the property this method exists to keep —
        moving the resolution post-fold to restore the self-edge would let the
        record and the write disagree again). Pinned by
        ``test_a_name_equal_to_the_Events_OWN_name_resolves_to_the_SUBJECT_arm``.
        """
        for label, rel in self._ABOUT_NAME_CASCADE:
            link, internals = self._resolve_about_edge_target(
                entity_name, label, rel)
            if link is not None:
                return link, internals
        # Nothing matched — live defaults to a Subject stub (label-agnostic
        # source). #2489 routed that mint through the SHARED helper
        # (_mint_subject_stub) so live wiring and rebuild replay produced
        # byte-identical stubs (one create path). ⚠️ #3590 made the helper
        # resolve-only for a while and that is WITHDRAWN: the stub is
        # name-KEYED, so its id is a pure function of the MERGE key and replay
        # reproduces it exactly — the mint D9 forbids is the OPAQUE one, not
        # this one (see the helper's docstring). ⛔ It is NOT covered upstream:
        # since #3590 D1 (RULED 2026-10-26) the SDK's Event door registers
        # NOTHING for a mention — "an unresolvable mention drops its link,
        # records the miss, and mints nothing" — so a NEW journal carries no
        # ``SubjectAdded`` for this name and DOES depend on this replay-time
        # mint. That mint writes `id` = `name` (the R1 shape): the residual is
        # #8914, not a producer duty the door discharges (#9876 review P2).
        #
        # `mint_if_absent` is True on purpose: it is the record's claim about
        # the construction this fallback names, and BOTH arms now honour it
        # (``resolve_structural_target(mint=…)``).
        from tortoise.projection import _log_identity_skip, _writable_id
        if not _writable_id(entity_name):
            _log_identity_skip("Subject stub", entity_name, "name (MERGE key)")
            return None, None
        return ({
            "rel": "aboutSubject",
            "how": "id",
            "targets": [entity_name],
            "target_label": "Subject",
            "resolve_key": entity_name,
            "mint_if_absent": True,
        }, None)

    def _resolve_about_edge_target(self, target_name: str, label: str,
                                   rel: str
                                   ) -> tuple[dict | None, list[int] | None]:
        """One cascade attempt: resolve a NAMED node of ``label``; never writes.

        D10 (ONTOLOGY v3.15 §4.4/§9.5 Q1): a document is a ``:Source``, so the
        ``aboutDocument`` target is a document-bearing Source matched on ``url``
        (its identity key — the document id) or ``title`` (its display name,
        the old Document convention). The ``documentKind IS NOT NULL`` guard
        keeps a session/connector/provenance Source from ever becoming an
        ``aboutDocument`` target. The journaled ``resolve_key`` for that branch
        is the node's ``url``, so replay's ``resolve_structural_target`` (which
        matches ``:Source {url}``, url-keyed since #2489) reaches the same node
        even when LIVE matched by title.

        Returns ``(journal_link, internal_ids)``: ``internal_ids`` is EVERY
        FalkorDB internal id this attempt resolved (a name with several legal
        carriers resolves to all of them — see the ambiguity note in the
        non-``Source`` arm), or ``None`` when nothing matched. ⛔ Internal ids
        MUST NEVER be journaled: they die at rebuild.

        ⛔ **NEITHER arm may ``LIMIT`` its probe.** A name — and a document's
        ``title``, which is the same kind of mutable natural key (D2) — may be
        held by several carriers, and the retired ``_try_about_edge`` MERGE'd
        the edge against EVERY one of them. Both arms therefore resolve ALL
        carriers and journal all of them in ``targets``; the ambiguity notes in
        each arm are one argument, stated where the removal happened.
        """
        # #7369: `target_name` rides as a READ parameter here, and the engine
        # parses every parameter regardless of clause — an unwritable one
        # aborts the replay after the wipe just as a write would.
        from tortoise.projection import _log_identity_skip, _writable_id
        if not _writable_id(target_name):
            _log_identity_skip(
                "aboutDocument edge", target_name, "name (read parameter)")
            return None, None
        if label == 'Source':
            # ⛔ NEVER limit this probe either (review at b26bf2a2c). The
            # retired ``_try_about_edge`` limited only its EXISTENCE probe and
            # then MERGE'd the edge against EVERY matching document Source
            # (``WHERE (e.url = $name OR e.title = $name) AND e.documentKind IS
            # NOT NULL``), so a ``title`` held by several documents wired all of
            # them. ``LIMIT 1`` here made live wire ONE — and wrote that
            # backend-row-order pick into an append-only journal — so it cost
            # every other carrier an edge ``origin/main`` gave it. That is both
            # an undisclosed behaviour regression and the identical defect the
            # non-``Source`` arm below removes; a title is a mutable natural key
            # exactly as a name is (D2). Measured with three document Sources
            # sharing ``title="Same Title"``: ``LIMIT 1`` wired and journaled
            # one, the retired behaviour wired all three. So resolve ALL of
            # them, journal EVERY url, and write every one.
            rows = self.g.query(
                "MATCH (e:Source) WHERE (e.url = $name OR e.title = $name) "
                "AND e.documentKind IS NOT NULL "
                "RETURN ID(e), e.url",
                params={"name": target_name},
            ).result_set
            if not rows:
                return None, None
            # The journaled replay key for this arm is the node's ``url`` (D10:
            # a document Source is url-keyed since #2489), so every url is a
            # journaled target and the first is the ``resolve_key`` fallback.
            # The internal ids are built from the SAME rows, so the live edge
            # set is exactly the journaled ``targets`` set: a carrier with no
            # ``url`` has nothing to journal, and live-wiring it would be an
            # edge replay could never address. (Unreachable for a document
            # ``:Source`` — ``url`` is its MERGE key — and kept aligned anyway
            # so the invariant is true by construction, not by luck.)
            targets = [url for _i, url in rows if url]
            internals = [row[0] for row in rows if row[1]]
            key = targets[0] if targets else None
            # A ``:Source`` is never a mintable stub here (aboutDocument is
            # resolve-only, D10 B1), so this link makes no mint claim.
            mint = False
        else:
            # ⛔ NEVER limit this probe (review at 2b9626072). A name is a
            # mutable natural key (D2: two live carriers are legal), and the
            # retired ``_try_about_edge`` MERGE'd the edge against EVERY
            # same-name carrier. The probe here was ``LIMIT 1``: live wired one
            # arbitrary carrier, that backend row-order pick was written into an
            # append-only journal, and live and replay then ran OPPOSITE
            # policies (live guessed; replay refused via
            # ``_refuse_ambiguous_target``). It also cost every OTHER carrier an
            # edge it used to get. ``LIMIT 2`` is not a fix either — it tells
            # one from many but still truncates at the third carrier, so live
            # would disagree with the retired behaviour AND with its own
            # journal. So resolve ALL of them: every endpoint is journaled in
            # ``targets`` (already a list, the id path journals several values
            # the same way) and every one of them is written. Nothing is picked,
            # so nothing arbitrary is pinned, and live writes exactly what the
            # record says. The ``Source`` arm above applies the SAME rule to a
            # title — it carried the identical ``LIMIT 1`` until review at
            # b26bf2a2c.
            rows = self.g.query(
                f"MATCH (e:{label} {{name:$name}}) "
                "RETURN ID(e), coalesce(e.id, e.eventId, e.name)",
                params={"name": target_name},
            ).result_set
            if not rows:
                return None, None
            key = target_name
            targets = [ident for _i, ident in rows if ident]
            # ⛔ Mint ONLY when the endpoint live resolved IS the stub (review
            # at 2b9626072): the stub construction is
            # ``MERGE (s:Subject {name:$name}) ON CREATE SET s.id=$name``, so
            # its id equals its name. Keying the claim on the LABEL alone
            # claimed a mint for a Subject live merely MATCHED — measured: a
            # link to the existing real Subject ``sub-6772…`` journaled
            # ``mint_if_absent: true``, and a replay where that Subject was
            # absent minted a duplicate same-name stub (``id == name``) and
            # reported ``wired=1, failed=0``. That is
            # ``derived == replay(journal)`` broken in the FABRICATION
            # direction — the invariant this whole path exists to establish.
            mint = label == "Subject" and any(
                ident == target_name for _i, ident in rows)
            # Same alignment as the ``Source`` arm: the live edge set is exactly
            # the journaled ``targets`` set. (``name`` is the MATCH key on this
            # arm, so ``coalesce`` can never be empty here — the filter is for
            # the invariant, not for a reachable case.)
            internals = [row[0] for row in rows if row[1]]
        return ({
            "rel": rel,
            "how": "id",
            "targets": targets,
            "target_label": label,
            "resolve_key": key,
            "mint_if_absent": mint,
        }, internals)

    def _create_about_edges(self, source_id: str, entity_name: str) -> dict | None:
        """Link entity (Point, Document, Event, or Object) to a named entity.

        Auto-detects entity type from the legacy flat aboutEntities list.
        Tries Subject → Object → Event → Source → Point (``_ABOUT_NAME_CASCADE``),
        creates a Subject stub if none matched.
        ONTOLOGY v2.5 §2.2: Point/Doc/Event → Subject/Object/Action; Point/Doc → Event; Event → Point/Document.
        #125: source MATCH is label-agnostic so Document/Event sources work too.

        Returns the RESOLVED journal link (see ``resolve_about_link``) or
        ``None``: #8398's ``create_event`` seam journals it, so the record
        carries the rel/label this cascade actually produced rather than the
        caller's request and a promise to re-run the cascade later.
        """
        link, internals = self.resolve_about_link(entity_name)
        if link is None:
            return None
        self.wire_about_link(source_id, link, internals)
        return link

    def wire_about_link(self, source_id: str, link: dict,
                        target_internal: int | Sequence[int] | None) -> bool:
        """Write ONE resolved about link — the single construction for live.

        ``target_internal`` is the id (or, for a name with several legal
        carriers, the ids) ``resolve_about_link`` resolved; ``None`` means the
        live path must RESOLVE-OR-MINT the ``:Subject`` stub the cascade's
        fallback named (``_mint_subject_stub`` — the name-keyed construction
        live and replay share; the caller may have registered that name already,
        in which case this resolves that node. ⛔ NOT "the SDK door has normally
        registered it": since D1 (RULED 2026-10-26) the door mints nothing for a
        mention, so nothing is registered for an unregistered reference — the
        stub is what makes the link wire on both passes). Writing by the RESOLVED internal id — rather than
        re-matching the name inside the MERGE — is what makes the journaled link
        and the edge the same decision, with no second read that could answer
        differently. Every resolved id is written, so the live edge set is
        exactly the journaled ``targets`` set — and the return value is that
        write's MEASURED effect (the edge exists after the MERGE), never the
        loop's progress, so a target that is gone reports ``False``.
        """
        rel = link["rel"]
        if rel not in _ABOUT_REPLAY_RELS:
            raise ValueError(
                f"wire_about_link: {rel!r} is not an about* rel "
                f"({sorted(_ABOUT_REPLAY_RELS)})")
        if target_internal is None:
            _mint_subject_stub(self.g, link["resolve_key"])
            rows = self.g.query(
                "MATCH (s:Subject {name:$name}) RETURN ID(s) LIMIT 1",
                params={"name": link["resolve_key"]}).result_set
            if not rows:
                return False
            internals: list[int] = [rows[0][0]]
        elif isinstance(target_internal, int):
            internals = [target_internal]
        else:
            internals = list(target_internal)
        if not internals:
            return False
        wrote = False
        for n in self._resolve_entity(source_id, by_id=True):
            for internal in internals:
                rows = self.g.query(
                    f"MATCH (n:{n['label']} {{{n['key']}:$sid}}), (t) "
                    f"WHERE ID(t)=$tid MERGE (n)-[:{rel}]->(t) "
                    f"RETURN count(*)",
                    params={"sid": n["value"], "tid": internal},
                ).result_set
                # #8398 4th review P2-3: the WRITE's own effect, not the loop's
                # progress. ``wrote = True`` used to be unconditional, so a target
                # the graph no longer holds — the MATCH half of the MERGE finds
                # nothing and the row is dropped — returned True for an edge that
                # was never created. Measured:
                # ``wire_about_link(ev, {"rel":"aboutObject"}, 999999)`` → True
                # while ``count(r) == 0``. This boolean is what feeds
                # ``wired += 1`` in ``replay_about_links``, i.e. it is the
                # difference between "the replay restored the link" and "the
                # replay reported it restored".
                # ⚠️ ``count(*)`` AGGREGATES, so a match-less MERGE still yields
                # ONE row holding 0 — the VALUE must be read, not the row count
                # (``bool(rows)`` alone would stay True).
                if rows and rows[0] and rows[0][0]:
                    wrote = True
        return wrote

    def create_about_edge(self, source_id: str, target_id: str, edge_type: str, *,
                          target_label: str | None = None) -> bool:
        """Create a specific about* edge by source/target IDs. Validates edge type.
        
        Args:
            source_id: ID of source node (Point, Document, or Event)
            target_id: ID of target node
            edge_type: one of aboutSubject, aboutObject, aboutEvent, aboutPoint, aboutDocument
            target_label: restrict the target to this node label (#3586).
                ``None`` (default) keeps the historical label-agnostic id/eventId
                resolution — callers that deliberately wire a structural edge
                whose target label is not the one the rel implies (Point→Point
                ``aboutSubject`` transfer tests) must keep working. The about*
                name/handle seam passes ``STRUCTURAL_REL_LABELS[edge_type]`` so a
                value that collides with ANOTHER label's id/eventId cannot steal
                the rel from its intended target.
        
        Returns True if edge was created.
        """
        valid = _VALID_ABOUT_EDGE_PREDICATES
        if edge_type not in valid:
            raise ValueError(
                f"Invalid about edge type: {edge_type}. Must be one of "
                f"{sorted(valid)}")
        
        # Resolve endpoints via index-backed labeled lookups (issue #327).
        # Source predicate is id-only; target is id OR eventId (legacy OR-set),
        # optionally SCOPED to ``target_label`` (#3586) so the label-agnostic
        # union cannot let a same-spelling node of a different label win and
        # strand the intended target.
        sources = self._resolve_entity(source_id, by_id=True)
        targets = self._resolve_entity(target_id, by_id=True, by_eventId=True)
        if target_label is not None:
            targets = [t for t in targets if t["label"] == target_label]
        if not sources or not targets:
            return False
        created = False
        # §17.1 (#9195): the about-lane's ONE mutating statement, DECLARED. Both
        # endpoints were just resolved by `MATCH` above, so this writes a bare
        # relationship and nothing else — the same shape and the same claim as
        # `create_edge`; read the block above `STRUCTURAL_EDGE_WRITE_LANE`.
        with _structural_edge_write_scope():
            for s in sources:
                for t in targets:
                    r = self.g.query(
                        f"MATCH (s:{s['label']} {{{s['key']}:$sv}}) "
                        f"MATCH (t:{t['label']} {{{t['key']}:$tv}}) "
                        f"MERGE (s)-[:{edge_type}]->(t) "
                        f"RETURN count(*) > 0",
                        params={"sv": s["value"], "tv": t["value"]},
                    )
                    if r.result_set and r.result_set[0][0]:
                        created = True
        # #9553 class 4: the RECORD carrier, emitted after the MERGE (the
        # `create_direct_edge` discipline — a crash between the write and the
        # emission is recovered by the retry's idempotent MERGE + emission, so
        # there is no created-only gate).
        if created:
            self._record_structural_edges(edge_type, sources, targets)
        return created

    def replay_about_links(self, payload: dict, hard_delete_seqs=None,
                           seq: int | None = None) -> tuple[int, int]:
        """Re-wire the ``about*`` edges journaled WITH an ``EventRecorded`` record.

        #8398: ``create_event(aboutObject=…)`` wires its links as GRAPH EDGES,
        so they were never among the writes ``rebuild_all`` replays —
        ``derived == replay(journal)`` was false for exactly this class (a
        rebuild dropped the Event→entity links while ``create_operator``'s IMPL
        edges, which journal an ``OperatorAdded`` record, survived).

        ⚠️ **The CALLER must run this in a TRAILING SWEEP, after every entity
        registration has folded** — not inline in the record loop. The replay
        stream is filename order, not chronology (``sorted(os.listdir(…))``),
        and pass-1a hoists only ``PointAdded``/``OperatorAdded``: invoked inline,
        a rotated journal whose ``ObjectRegistered`` lands in a later file would
        replay this record while the target does not exist yet, and
        ``create_about_edge`` would return False and drop the link silently —
        the very defect this fixes. This mirrors ``edge_message_events``, which
        is buffered for the same reason ("making the target edge exist").

        ``how == "id"`` carries the values RESOLVED at write time, so replay
        repeats an exact endpoint instead of re-running the id-or-name cascade
        against a graph whose answers may differ (the drift this method exists
        to avoid).

        ⛔ **A name-path link is pinned, never re-derived (review P1-2).**
        ``create_event(aboutObject="Acme")`` with nothing named ``Acme`` wired
        ``aboutSubject`` to a fresh Subject stub, because the live name path is
        the rel-IGNORING cascade. Journaling that as ``how:"name"`` made replay
        run the cascade against the END-OF-JOURNAL graph — measured on the
        shipped code: live ``aboutSubject``→Subject vs replay
        ``aboutObject``→Object once the caller registered the Object. Different
        rel, different node, ``failed == 0`` (``_count_about_out`` only asks "is
        the source joined to anything"). So the writer now journals the RESOLVED
        decision — ``rel``, ``target_label``, ``resolve_key`` and
        ``mint_if_absent`` — and replay repeats THAT: the endpoint by id when it
        resolves, else the same label/key live matched on, minting only when the
        live construction minted.

        ``how:"name"`` is DEFENSIVE, not a legacy path with data behind it.
        ``aboutLinks`` is introduced by this unmerged change — it does not exist
        on ``origin/main`` and no released version writes it — so no journal in
        the wild can hold a ``how:"name"`` link. The only records that could
        were written by an earlier commit of THIS branch, which did journal
        ``{"rel", "value", "how": "name"}``; this branch is append-only too, so
        a journal written by such a build still replays. The branch therefore
        defends a form only this branch ever produced — delete it (and
        ``test_a_LEGACY_name_link_record_still_replays``) freely if it becomes
        dead weight. It is kept because dropping it would turn such a record
        into a counted loss rather than the pre-fix behaviour it had.

        Returns ``(wired, failed)`` — the caller reports ``failed`` so a rebuild
        that could not re-wire a journaled link is never silent. Every skip that
        costs a journaled link counts as ``failed``: a mystery ``how``, a ``rel``
        outside the set, a ``targets`` that is not a list, an empty ``targets``,
        a pinned link with an unusable ``target_label``, and a record whose id
        cannot address an Event at all (its links are counted, not dropped).
        "Wired" on the legacy ``name`` path is MEASURED, never assumed — but by
        the LINK's presence, not by ``rel``: a name link's own rel resolved to
        nothing at write time, so live wired whatever the rel-ignoring cascade
        found.

        ⛔ **A link whose TARGET the journal hard-deletes later is STALE, not
        lost (#8398 review).** ``hard_delete_seqs`` + ``seq`` are the replay's
        own hard-delete map (``journal_hard_delete_seqs``) and this record's
        seq; a target endpoint the journal hard-deletes at a later seq is
        SKIPPED — never counted as ``failed``, never re-minted — because live
        has no such edge either (``delete`` DETACH-DELETEs it) and a mint would
        FABRICATE a node the live graph no longer holds. This mirrors
        ``fold_deferred_entity_links`` (``entities.py``), which tests BOTH
        endpoints for exactly this reason, and it REUSES the same
        ``_hard_delete_suppresses`` predicate rather than re-deriving "stale".
        Measured at 208f96eb8 before this: ``create_event(aboutObject="Acme")``
        (Subject-stub fallback) → ``delete("Acme")`` → ``rebuild_all``
        RESURRECTED ``(:Subject {id:"Acme"})`` and its edge and reported
        ``wired=1, failed=0``; for an id-resolved ``Object`` target it instead
        reported one false failure with the warning "the target node was not
        restored by this replay … the live graph had these edges", both halves
        false.

        ``hard_delete_seqs is None`` means "no staleness information" (the
        ``apply``-style callers that see ONE record with no journal-wide view),
        which keeps the pre-existing behaviour exactly.
        """
        # The label-aware staleness helper lives next to its sibling reader; the
        # lazy import mirrors ``entities.py`` and avoids the import cycle.
        # ``_about_link_payload`` is the SHARED resolver for this record's
        # nested-vs-flat shape — imported from the same place so the gate that
        # decides whether this reader runs cannot drift from the rule below
        # (#8398 6th review P2).
        from tortoise.projection import _about_link_payload, _hard_delete_suppresses

        def _target_stale(endpoint_id, endpoint_label) -> bool:
            """True when the journal hard-DELETES this target AFTER ``seq``.

            ONE definition of "stale" (``_hard_delete_suppresses``), label-
            scoped, so a delete that cannot remove THIS label never suppresses
            the edge (#3722 cycle 6). ``seq is None`` disables the check.
            """
            if hard_delete_seqs is None or seq is None:
                return False
            return _hard_delete_suppresses(
                hard_delete_seqs, endpoint_id, endpoint_label, seq)

        # ⛔ Unwrap the NESTED record format ONLY when the inner dict actually
        # ADDRESSES this record (#8398, 5th review P2-1), through the ONE
        # resolver ``_about_link_payload`` — the same function the gate ahead of
        # this reader calls, so the two cannot disagree about which dict is the
        # payload (#8398 6th review P2). ``event`` is not a
        # reserved name on every writer (``EventAPI.add_event`` and the miner
        # journal ``{event: {id, …}}``; a caller PROP may legally be called
        # ``event``), so the two shapes are ambiguous at this key — and an
        # unconditional ``payload.get("event", payload)`` resolved the
        # ambiguity in the ONE direction that silently destroys journal data:
        # MEASURED at 742ff7246,
        # ``create_event("withprop", "event", event={"payload": "x"},
        # aboutObject=oid)`` writes the record
        # ``{"type": "EventRecorded", "id": "1a14…", "event": {"payload":
        # "x"}, "aboutLinks": [{"rel": "aboutObject", "how": "id",
        # "targets": ["obj-…"]}]}`` — the caller prop became ``inner``, so
        # ``source`` was ``None`` and this reader returned ``(0, 0)``: NO
        # count, NO warning, while the record demonstrably claimed a link. A
        # journaled link claim was discarded in the exact silence this method
        # exists to end.
        #
        # So: the nested form is authoritative only when its payload carries
        # the record's address (``id``/``eventId``, the two keys
        # ``_upsert_event`` reads). Otherwise the record IS the payload — its
        # own ``aboutLinks`` are read and the caller prop is inert data. The
        # residual ambiguity is removed at the WRITE end too (the SDK drops a
        # caller ``event`` prop at the Event door), but a journal written by
        # any other build must still be READ correctly: this reader cannot
        # assume the intake strip ran.
        inner = _about_link_payload(payload)
        source = inner.get("id") or inner.get("eventId")
        # The record's OWN link field wins when the nested payload has none: a
        # ``_journal_extra`` field is written at the RECORD's top level even
        # for a nested-shaped record, so reading only ``inner`` would hide it.
        links = inner.get("aboutLinks") or payload.get("aboutLinks") or []
        if not isinstance(source, str):
            # an unaddressable record still CLAIMED links; a (0, 0) here would
            # drop them without a signal — the class this fix exists to end
            return (0, len(links)) if isinstance(links, list) and links else (0, 0)
        if not isinstance(links, list):
            return (0, 1)
        wired = failed = 0
        for link in links:
            if not isinstance(link, dict):
                failed += 1
                continue
            rel = link.get("rel")
            if rel not in _ABOUT_REPLAY_RELS:
                # a journaled link this replay does not understand is a loss,
                # not a non-event — it must be counted, not skipped.
                # ⚠️ TRIPWIRE: if the writer ever gains a rel this set lacks,
                # this reports one failure per record on EVERY rebuild until the
                # set is extended. Fail-loud is the intended direction; the
                # remedy is to add the rel to ``_ABOUT_REPLAY_RELS``.
                failed += 1
                continue
            if isinstance(link.get("resolve_key"), str) and link.get("resolve_key"):
                # #8398 (review P1-2): a NAME-path link, PINNED to the rel/label
                # the live cascade produced. Replay must NEVER re-run the
                # cascade for these: the cascade is rel-ignoring, so it answers
                # whatever a LATER graph happens to hold (measured: live
                # aboutSubject/Subject, replay aboutObject/Object once the
                # caller registered the Object). Two steps, most-faithful
                # first: the endpoint live produced (by id), else the same
                # label/key live matched on — whose presence is a mint claim
                # only when ``mint_if_absent`` set it.
                label = link.get("target_label")
                key = link["resolve_key"]
                if (not isinstance(label, str)
                        or label not in _STRUCTURAL_LABEL_SET):
                    # Do NOT let a malformed record reach
                    # ``resolve_structural_target`` (it raises on an unsafe
                    # label — aborting a post-wipe replay).
                    failed += 1
                    continue
                # P2-8 (review at 2b9626072): a ``targets`` that is not a LIST
                # is malformed, and iterating it as a sequence walks a STRING
                # character by character — ``targets: "abcd"`` could WIRE to a
                # node whose id is one character and then "repair" the rest via
                # the key fallback, instead of counting the loss. Same
                # validation the ``how:"id"`` arm below already applies.
                targets = link.get("targets")
                if not isinstance(targets, list):
                    failed += 1
                    continue
                # #8398 4th review P2-2: an EMPTY ``targets`` journals NO
                # endpoint, so it is a malformed record and it is counted as one
                # loss HERE — before the key fallback below can "repair" it.
                # Measured before this boundary: a
                # ``{"targets": [], "resolve_key": "Ghost-True",
                # "mint_if_absent": true}`` record MINTED the ``:Subject``
                # ``Ghost-True``, wired an edge and returned ``(1, 0)`` — a
                # record that names no endpoint fabricating a node + edge and
                # reporting ``derived == replay(journal)`` clean, contradicting
                # this method's docstring. The sibling ``how:"id"`` arm counts
                # the same record as one loss, so the two arms disagreed about
                # the SAME record, and the fallback's own failure was the only
                # reason the ``else`` arm ever agreed with it.
                if not targets:
                    failed += 1
                    continue
                # #8398 review: drop the endpoints the journal hard-deletes
                # AFTER this record. A stale endpoint is not a loss and must
                # never be re-minted (``resolve_structural_target`` would mint
                # a Subject stub the live graph no longer has).
                key_stale = _target_stale(key, label)
                live_targets = [
                    value for value in targets
                    if not (isinstance(value, str)
                            and _target_stale(value, label))]
                if not live_targets:
                    # EVERY journaled endpoint was hard-deleted after this
                    # record: live has no such edge either, and the key fallback
                    # below must NOT mint one back. (``key_stale`` is not the
                    # test: a link whose endpoints are all stale is stale as a
                    # LINK, and repairing it by key would fabricate the node
                    # this very commit exists to stop fabricating.)
                    continue
                wired_before = wired
                for value in live_targets:
                    if (isinstance(value, str) and value
                            and self.create_about_edge(
                                source, value, rel, target_label=label)):
                        wired += 1
                # P2-2 (review at b26bf2a2c): account PER TARGET, exactly as
                # the ``how:"id"`` arm below does. Collapsing a multi-carrier
                # link into ONE boolean reported full success for a PARTIAL
                # loss — measured: three same-name ``:Event`` carriers in one
                # pinned link with one carrier gone returned ``(1, 0)`` while
                # only two of the three journaled edges existed, so a journaled
                # endpoint was lost with ``failed == 0`` and no warning. The
                # contract above is per journaled ENDPOINT ("every skip that
                # costs a journaled link counts as ``failed``"), not per link.
                # Counted over the LIVE endpoints only: a stale target is not
                # a missing one.
                missing = len(live_targets) - (wired - wired_before)
                if wired > wired_before:
                    failed += missing
                    continue
                # Nothing resolved by VALUE. The legacy repair — ask the ONE key
                # the record pinned — resolves AT MOST ONE node
                # (``resolve_structural_target`` refuses an ambiguous key), so
                # it is attempted once per LINK, never once per missing value:
                # run per value it would re-wire (and re-count) the same node
                # for every carrier that did not resolve, inventing wires. It
                # covers the one-value link whose value is not a resolvable id
                # (a url-keyed document Source), which is what it was added
                # for.
                # ⛔ A hard-deleted key must NOT reach here: the repair MINTS
                # when ``mint_if_absent`` (the Subject-stub fallback journals
                # exactly that), which is how a rebuild FABRICATED a node the
                # live graph had deleted.
                target = None
                if not key_stale:
                    target = resolve_structural_target(
                        self.g, label, key, rel,
                        mint=bool(link.get("mint_if_absent")))
                if bool(target) and self.wire_about_link(
                        source, link, target["internal"]):
                    wired += 1
                    # ... and it repaired exactly ONE of the missing values.
                    failed += max(missing - 1, 0)
                else:
                    # Every LIVE endpoint is a loss. ``missing`` is at least 1
                    # here — the empty-``targets`` record was counted and
                    # skipped above, and an all-stale link never reaches this
                    # arm — so there is no artificial floor left. Before #8398's
                    # 4th review this read ``max(missing, 1)`` (whose floor was
                    # the ONLY thing counting an empty ``targets``, and only
                    # when the fallback failed) beside a ``key_stale`` arm
                    # adding the same ``missing``: two arms, one verdict.
                    failed += missing
            elif link.get("how") == "id":
                targets = link.get("targets")
                if not isinstance(targets, list) or not targets:
                    failed += 1
                    continue
                # #8398 review: same staleness rule on the id-resolved form —
                # the target label is the one the rel implies.
                label = STRUCTURAL_REL_LABELS.get(rel)
                live_targets = [
                    value for value in targets
                    if not (isinstance(value, str)
                            and _target_stale(value, label))]
                if not live_targets:
                    # every journaled endpoint was hard-deleted after this
                    # record: live has no such edge, so this is not a loss
                    continue
                for value in live_targets:
                    ok = bool(value) and isinstance(value, str) and \
                        self.create_about_edge(
                            source, value, rel,
                            target_label=STRUCTURAL_REL_LABELS.get(rel))
                    if ok:
                        wired += 1
                    else:
                        failed += 1
            elif link.get("how") == "name" and isinstance(link.get("value"), str):
                # ⚠️ DEFENSIVE only — see "``how:"name"`` is DEFENSIVE" above:
                # no released version and no ``origin/main`` writes this form,
                # so only a journal written by an EARLIER COMMIT OF THIS BRANCH
                # can carry one. Such a link carries no resolved endpoint, so
                # replay has nothing to repeat but the rel-ignoring cascade.
                # The journal is append-only, so those records cannot be
                # repaired — they keep the pre-fix behaviour (and its drift)
                # rather than being silently counted as losses. New writes carry
                # ``resolve_key`` above.
                #
                # ⚠️ ``_create_about_edges`` is the rel-IGNORING
                # Subject→Object→Event→Source→Point cascade, and a "name" link is
                # one whose OWN rel resolved to nothing at write time — so live
                # did NOT wire ``rel`` either; it wired whatever the cascade
                # found. Measuring ``rel`` here would report failure for the
                # reproduction live just made (and would report failure on every
                # re-run of an already-wired link, because MERGE adds nothing).
                self._create_about_edges(source, link["value"])
                if self._count_about_out(source) > 0:
                    wired += 1
                else:
                    failed += 1
            else:
                failed += 1
        return (wired, failed)

    def _count_about_out(self, source: str) -> int:
        """Count the source's outgoing about edges — by ID only, never by name.

        The ``how: "name"`` success test (DEFENSIVE records only — see
        ``replay_about_links``: no released version writes that form). A name
        link's own rel resolved to nothing at write time, so the cascade is
        rel-ignoring and the honest question is only "did this record leave the
        Event joined to anything".

        ⛔ It is deliberately NOT "is the Event joined to a node carrying this
        VALUE": that is a name-keyed identity read, and #3633's guard refuses one
        from ``tortoise/projection/edges.py`` precisely because it reopens the
        route-then-refuse decision — identity resolves in ONE place
        (``tortoise/entity_identity.py``), not in a diagnostic.

        ⚠️ **What this can and cannot tell you.** ``_create_about_edges`` MINTS a
        Subject stub when nothing in the graph matches, so for any writable value
        a name link leaves an outgoing edge and ``failed`` is effectively
        unreachable — this count therefore does NOT detect a link that landed on
        a stub of the wrong label instead of the real target. On an Event that
        carries NO other about* edge it detects the two cases that are
        reachable: a cascade that produced no edge at all, and a value the
        cascade could not use. (That precondition is load-bearing — the sentence
        was unqualified before #8398's second review, and it is false on an
        Event that already carries any about* edge: measured ``wired=1,
        failed=0`` for a link nothing wired.) That is weaker than "the right edge
        is there", and it is what is available without the refused identity
        read.
        The direction of the error is OVER-counting ``wired``, never a false
        failure: a source that ALREADY carried another about* edge counts as
        wired even if this record's call was a no-op, so a no-op name link reads
        as success. (Stated the other way round before #8398's review P2-3, and
        it was backwards: this predicate can never report a GOOD link as failed —
        every writable value leaves an edge — so its only failure direction is a
        generous count. A false failure would train readers to ignore the
        warning, which is why the count errs toward success.)

        Idempotent: an already-wired source counts, so a re-run reports no
        failure that did not happen.
        """
        rows = self.g.query(
            "MATCH (s:Event) WHERE s.id=$sv OR s.eventId=$sv "
            # #6976: the predicate filters `s` and the next clause RE-BINDS it, so
            # without this `WITH s` the engine drops the predicate and counts
            # OTHER events' edges.
            "WITH s "
            "MATCH (s)-[r:aboutSubject|aboutObject|aboutEvent|aboutPoint|aboutDocument]->() "
            "RETURN count(r)",
            params={"sv": source}).result_set
        return int(rows[0][0]) if rows and rows[0] else 0

    def _link_source(self, point_id: str, source_ref: str | Sequence[str], source_kind: str | None = None, *, label: str = "Point", source_versions: dict[str, str] | None = None) -> None:
        """Link entity → Source via extractedFrom edge (Ontology v3.3).

        Creates stub Source if missing, keyed on url. ``source_kind`` defaults
        per-ref (``agentSession`` for ``session:`` refs, else ``document``) —
        connectors and the Document path pass specific values (github_issue,
        slack_message, linear_card, ...). ``label`` selects the source-side
        entity label — Point (default) or Source (the ``create_document``
        provenance caller, #394). D10 §4.4 makes the document node a
        ``:Source``, so no caller passes the retired ``Document`` label here.

        ``source_ref`` is **many-to-many** (ontology §3.3, amended #3263): a
        claim extracted from several sources carries one edge per source, so a
        list/tuple of refs is fanned out to N edges. Previously a list was not
        rejected but silently MERGEd a SINGLE Source whose ``url`` was an ARRAY
        (or, for the inferred path, stringified the list into a bogus
        ``session:['s1', 's2']`` ref) — a corrupt provenance node with no error.

        Session-provenance refs (`session:<id>`, written by the capture
        extractors) stamp `is_episodic=true` ON CREATE — the backfill's
        condition 4 (graph-scripts/backfill_is_episodic.py) treats
        Session-linked Sources as episodic; a new capture creating a
        flag-less Source would otherwise keep matching the one-time backfill
        (issue #1486). Non-session Sources (documents, connectors) are
        untouched.

        ``source_versions`` (#5256) maps a source ref — keyed by the Point's
        own RAW ``extractedFrom`` spelling, the journal-stable key — to the
        ``contentHash`` the Point was read from. It is **handed** to this writer
        by the caller — the LIVE create path resolves it from the Source; the
        REPLAY passes the value from the Point's own journaled snapshot
        (``sourceVersionTransit``, whose pairs are raw-ref keyed by the same
        contract). This method therefore NEVER
        reads ``s.contentHash`` itself: pass-2 resurrection calls the same
        writer, and the Source's hash may have advanced since live time
        (``_upsert_source``'s in-place bump is unjournalled, #5024), so reading
        it here would record a FALSE current. Absent / empty ⇒ no property at
        all (``ON CREATE SET r.sourceVersion = NULL`` is a no-op), never ``''``.
        """
        # #8171 (review P1): a ref that is neither a string nor a list/tuple of
        # them is NOT a source identity. `list(source_ref)` on a DICT yielded
        # its KEYS, and each key was minted as its own `:Source` — an INVENTED
        # provenance, which "is worse than none" (design contract #3). Measured
        # pre-fix: `{"url": ..., "kind": ...}` minted Sources named `url` and
        # `kind`, each with an `extractedFrom` edge. A per-member type check
        # cannot catch this — the keys ARE strings — so the gate has to be on
        # the CONTAINER. The doors that can validate ATOMICALLY do
        # (`create_entity`, `create_point`) and raise pre-write; this choke
        # point guarantees the remaining callers cannot fabricate whatever
        # they pass.
        if isinstance(source_ref, str):
            refs = [source_ref]
        elif isinstance(source_ref, (list, tuple)):
            refs = list(source_ref)
        else:
            return
        versions = source_versions or {}
        for raw_ref in refs:
            if not raw_ref or not isinstance(raw_ref, str):
                continue
            # #7951 R2-P2: a `session:<id>` edge whose ORIGINATING record
            # precedes a later `SessionDeleted` belongs to the incarnation the
            # delete `DETACH DELETE`d. Live, that delete severed the edge, and
            # a later record re-materialising a FRESH Source does not restore
            # it. Pass 2 re-derives from every `PointAdded`, so the gate lives
            # here: skip both the mint and the edge for a pre-delete ref.
            # `_replay_session_deleted_seq` is set only for the duration of
            # `rebuild_all`'s pass 2, so live/`rebuild()` are untouched.
            _replay_del = self._replay_session_deleted_seq
            if (_replay_del is not None and isinstance(raw_ref, str)
                    and raw_ref.startswith("session:")
                    and _replay_del.get(raw_ref[8:], -1) > self._replay_seq):
                continue
            # #2489: Source stub creation routed through the SHARED resolver helper
            # (_mint_source_stub — mirror query text, incl. the session: is_episodic
            # clause) so live wiring and rebuild replay mint byte-identical stubs
            # (one create path).
            ref = _mint_source_stub(self.g, raw_ref, source_kind)
            # The carrier is keyed by the RAW ref (the Point's journal-stable
            # `extractedFrom` spelling), so the raw lookup is the one that must
            # hit on BOTH lanes; the resolved-key lookup covers a caller that
            # keyed by `_mint_source_stub`'s return instead. (`ref` is that
            # return, and it is NOT stable across a rebuild — see
            # `resolve_source_versions`; a URL variant resolves to the node's
            # stored url, which can differ between live and replay.) `$v` is
            # ALWAYS bound so the bare callers
            # (create_document, the ingest connection leg, direct test calls)
            # never miss a parameter.
            v = versions.get(ref)
            if v is None:
                v = versions.get(raw_ref)
            # #5199's shared guard: `ON CREATE` only, and `''`/NULL ⇒ NULL. A
            # re-link must not ADVANCE a recorded version, and the empty string
            # is never written (it compares equal to a Source's `''` — a false
            # current).
            #
            # D10: a Source source-side entity resolves by ``url`` (its identity
            # key); Point/other labels keep the id key.
            key_clause = "{url:$pid}" if label == "Source" else "{id:$pid}"
            self.g.query(
                f"MATCH (n:{label} {key_clause}), (s:Source {{url:$url}}) "
                "MERGE (n)-[r:extractedFrom]->(s) "
                + _anchor_on_create("$v"),
                params={"pid": point_id, "url": ref, "v": v},
            )

    def link_source_to_entity(self, source_url: str, entity_id: str, entity_label: str, source_kind: str = "document") -> None:
        """Create Source → Entity references edge (Ontology v3.1 §3.4).

        Auto-creates the Source node if it doesn't exist (MERGE + ON CREATE SET)
        so the edge works even when no Point extracted the source yet (#205).

        Args:
            source_url: the Source node's url (auto-created if missing)
            entity_id: the Document/Event/Object node id the source references
            entity_label: the entity label (Source|Event|Object) for the MATCH.
                The retired ``"Document"`` is accepted as a DEPRECATED ALIAS and
                resolved to ``Source`` (D10, ONTOLOGY v3.15 §4.4).
            source_kind: sourceKind to set on auto-created Source (default: "document")

        Anchor (#5199, owner-approved 2026-09-25): a DERIVATION link
        (``entity_label`` in ``_DERIVATION_REFERENCES_LABELS``) records the version
        it was read from as ``sourceVersion`` — the source's current
        ``contentHash``, read HERE so that no caller passes it and the public SDK
        signature is unchanged. Identity/mention links (``Object``) carry no
        ``sourceVersion`` — this writer sets no property on them at all (the
        non-derivation branch is a bare ``MERGE``), which is why nothing here
        needs a version anchor. Written ``ON CREATE`` only:
        a re-link must NOT advance the recorded version, or staleness would
        silently read as current.

        ⛔ NO SELF-LOOP (#5151). A source and a ``Source`` entity are the SAME
        ``:Source`` node when their keys are equal, so the edge would be
        ``(:Source {url:'X'})-[:references]->(:Source {url:'X'})`` — a node
        referencing itself, which states nothing (ONTOLOGY §3.4's ``references``
        is source→entity containment/derivation). Before D10 the two sides were
        different LABELS and equal keys could not degenerate; D10 made a document
        a ``:Source``, which made it reachable. The guard is HERE, not in the
        callers, because a caller-side ``if ref and ref != did`` is defeated by
        two things a caller cannot see:

        * key RESOLUTION — ``resolve_source_key`` maps a URL variant
          (``…/a/?utm_source=x``) onto the node stored as ``…/a``, so the raw
          spellings differ while the RESOLVED key equals the entity id (measured:
          the caller-side guard passes it through and the edge is a self-loop);
        * the next caller — every new ``Source``→``Source`` writer would have to
          re-derive the same rule, and #5140 showed what a misplaced guard costs
          in the other direction: the *legacy* no-``source_url`` path silently
          minted no edge at all, repaired by re-pointing ``get_provenance_chain``'s
          reader.

        SCOPE, precisely: only the ``references`` MERGE is suppressed. The
        ``:Source`` MERGE above still runs (every property it sets, including the
        ``sourceKind``/``canonicalUrl``/``urlAliases`` bookkeeping, is unchanged),
        because collapsing source and entity onto one node leaves the NODE right
        and only the EDGE degenerate — the same suppression shape as
        ``_upsert_document``'s and ``session_source_migration``'s. Nothing is
        dropped that a reader needs: the node IS its own provenance, which is what
        ``get_provenance_chain`` already serves for the collapsed path, and live
        and replay now agree (a journaled ``DocumentCreated`` whose ``source_url``
        equals its document id no longer mints a self-loop on ``rebuild_all``).

        ⚠️ Deliberately NOT narrowed to "``references`` is a Source→Source
        predicate": its target set is legitimately ``Event`` | ``Object`` |
        ``Source`` (docs/ONTOLOGY.md §3.4), so a Source-only declaration would
        reject legal links. The degenerate class is SAME-NODE, not same-label —
        which is why the guard tests identity and the ``Event``/``Object`` labels
        (where an equal ``id`` string addresses a DIFFERENT node) are untouched.

        Raises:
            ValueError: if entity_label is not one of Source, Event, Object
                (Action was dissolved in Ontology v3.0).
        """
        # #5199 × D10 (merge resolution): the DERIVATION question is about the
        # LINK's meaning, so it is answered from the label the CALLER passed,
        # BEFORE D10's identity remap below collapses ``Document`` onto
        # ``Source``. Reading it afterwards would make the ``Document`` member of
        # ``_DERIVATION_REFERENCES_LABELS`` unreachable — a ``Document`` link is a
        # derivation link and would silently stop being anchored (caught by
        # ``test_document_link_records_the_version_read``). Anchoring by the
        # remapped label instead would wrongly stamp every ``Source -> Source``
        # referential-containment link, which the model leaves without a
        # ``sourceVersion`` anchor.
        _is_derivation = entity_label in _DERIVATION_REFERENCES_LABELS
        if entity_label == "Document":
            # D10: :Document is retired — a document is a :Source. Kept as a
            # deprecated alias so existing callers/journal replay converge on
            # the same node instead of creating a second label.
            entity_label = "Source"
        valid = {"Source", "Event", "Object"}
        if entity_label not in valid:
            raise ValueError(
                f"Invalid entity_label: {entity_label}. Must be one of {valid} "
                f"(Action was dissolved in Ontology v3.0; 'Document' is a "
                f"deprecated alias for 'Source')."
            )
        # MERGE Source with auto-create (mirrors _link_source) — #205
        key = resolve_source_key(self.g, source_url)
        # #7369: `key` is the Source MERGE key, and `source_url` is a
        # first-class JOURNALED field of `DocumentCreated` (sdk.py), forwarded
        # verbatim by `_upsert_document` — an unwritable one aborts the replay
        # after the wipe. Same gate as its three siblings (_mint_source_stub,
        # _materialize_connector_source, _upsert_source).
        from tortoise.projection import _log_identity_skip, _writable_id
        if not _writable_id(key):
            _log_identity_skip("Source link", key, "url (MERGE key)")
            return
        canonical = normalize_source_url(key)
        self.g.query(
            "MERGE (s:Source {url:$url}) "
            "ON CREATE SET s.sourceKind=$sk, s.title=$url, "
            "    s.canonicalUrl=$cu, s.urlAliases=[$raw_url], "
            "    s.contentHash='', s.ingestedAt=$now "
            "ON MATCH SET s.canonicalUrl = coalesce(s.canonicalUrl, $cu), "
            "    s.urlAliases = CASE WHEN $raw_url IN coalesce(s.urlAliases, []) "
            "        THEN s.urlAliases "
            "        ELSE coalesce(s.urlAliases, []) + [$raw_url] END",
            params={"url": key, "raw_url": source_url, "cu": canonical,
                    "sk": source_kind, "now": _now_iso()},
        )
        # D10: a document is a :Source, and a Source resolves by ``url`` (not
        # ``id``) — the same identity key the label moved with.
        key_clause = "{url:$eid}" if entity_label == "Source" else "{id:$eid}"
        # #5151: the self-loop guard, at the CHOKE POINT (read this method's
        # docstring paragraph "NO SELF-LOOP" for why not in the caller). Both
        # endpoints of a ``Source`` entity MATCH are ``{url: …}``-keyed, and ``url``
        # is single-valued, so ``key == entity_id`` IS node identity here — no
        # second read, no race. Scoped to ``Source`` because for
        # ``Event``/``Object`` the target is matched on ``{id: …}`` under a
        # DIFFERENT label, so an equal string addresses a distinct node and the
        # edge is legitimate.
        # Deliberately NOT logged: there is no link to lose (source and entity are
        # one node), so this is the ordinary case, not a degraded one — a warning
        # here would fire on every collapsed-provenance write and train readers to
        # ignore it, the direction `_count_about_out`'s ``wired`` count errs away
        # from for the same reason.
        if entity_label == "Source" and key == entity_id:
            return
        if _is_derivation:
            self.g.query(
                f"MATCH (s:Source {{url:$url}}), (e:{entity_label} {key_clause}) "
                f"MERGE (s)-[r:references]->(e) " + _DERIVATION_ANCHOR_SET,
                params={"url": key, "eid": entity_id},
            )
        else:
            self.g.query(
                f"MATCH (s:Source {{url:$url}}), (e:{entity_label} {key_clause}) "
                f"MERGE (s)-[:references]->(e)",
                params={"url": key, "eid": entity_id},
            )

    def link_source_to_event(self, source_key: str, event_id: str) -> None:
        """MERGE ``(Source {url})-[:references]->(Event {eventId})`` — the
        ``eventId``-keyed derivation writer, anchoring the version the SOURCE holds.

        Separate from :meth:`link_source_to_entity` because legacy raw-Cypher Events
        carry no ``id``, so that method's id-keyed MATCH would silently no-op on them.
        Used by the connector choke point and the capture path. Every provenance
        writer of a DERIVATION link stamps, so such an edge cannot be anchored on one
        such path and unanchored on another (the generic ``create_edge`` escape hatch
        and ``graph-scripts/backfill_references.py`` are not provenance writers and are
        not auto-anchored; nor are the CONTAINMENT writers — the session->external
        link in ``hosted_api.py`` and the connector ``Source``->``Object`` link in
        ``projection/entities.py`` — which are deliberately left without a
        ``sourceVersion`` anchor; see ``STORAGE-ARCHITECTURE.md`` §9.6).

        ``source_key`` is passed through AS the Source key — callers pass the same
        value they used for the Source MERGE, so this adds no new resolution step.

        NOTE: on the repair path use :meth:`link_source_to_legacy_event` instead — the
        Source there is deliberately NOT the version the Event was read from.
        """
        self.g.query(
            "MATCH (s:Source {url: $url}), (e:Event {eventId: $eid}) "
            "MERGE (s)-[r:references]->(e) " + _DERIVATION_ANCHOR_SET,
            params={"url": source_key, "eid": event_id},
        )

    def link_source_to_legacy_event(self, source_key: str, event_id: str) -> None:
        """Repair-link a LEGACY ``Event`` to its ``Source``, anchoring the version
        **the Event records it was read from** — never the Source's current hash.

        Why this cannot be :meth:`link_source_to_event`: ``backfill_sources`` moves the
        Source to the file's **CURRENT** hash while the legacy Event keeps the
        ``file_hash`` it was captured with (W2, "file edited since capture"). The
        Source's hash there is therefore NOT the version this Event was built from, and
        anchoring ``s.contentHash`` would report a STALE Event as **current** — the
        precise failure ``sourceVersion`` exists to expose. ``e.file_hash`` *is* that
        version (it equals ``s.contentHash`` whenever the file has not changed), so it
        is the honest anchor; an Event with no recorded hash anchors **nothing** rather
        than guessing.
        """
        self.g.query(
            "MATCH (s:Source {url: $url}), (e:Event {eventId: $eid}) "
            "MERGE (s)-[r:references]->(e) " + _BACKFILL_ANCHOR_SET,
            params={"url": source_key, "eid": event_id},
        )

    # ponytail: SDK compat alias (Phase 1b will rename caller)
    _link_extracted_from = _link_source

    def edge_stats(self) -> dict:
        """Return {operators, impl_edges, nand_edges, input_edges} for diagnostics."""
        ops = self.g.query(
            "MATCH (n:Point) WHERE n.is_operator = true RETURN count(n)"
        ).result_set[0][0]
        impl = self.g.query(
            "MATCH ()-[r:IMPL]->() RETURN count(r)"
        ).result_set[0][0]
        nand = self.g.query(
            "MATCH ()-[r:NAND]->() RETURN count(r)"
        ).result_set[0][0]
        inp = self.g.query(
            "MATCH ()-[r:INPUT]->() RETURN count(r)"
        ).result_set[0][0]
        return {"operators": ops, "impl_edges": impl, "nand_edges": nand,
                "input_edges": inp}

    def create_edge(self, source_id: str, target_id: str, predicate: str) -> bool:
        """Create a named edge between two entities by their IDs.
        Matches target by id OR eventId (Event nodes use eventId as key).

        ⛔ This verb takes NO property parameter and NO ``**kwargs`` — §9.6's
        boundary (a derivation edge minted here carries no ``sourceVersion``
        anchor and nothing can supply one) is pinned by
        ``tests/test_references_boundary_5213.py``. The record CARRIER for the
        edge is emitted one level up, by the SDK door, which resolves the
        endpoints and writes ``DirectEdgeCreated`` (#9553, R5 class 4).
        """
        valid_predicates = _VALID_EDGE_PREDICATES
        if predicate not in valid_predicates:
            raise ValueError(f"Unknown predicate: {predicate}")
        # Resolve endpoints via index-backed labeled lookups (issue #327).
        # Source OR-set: id | eventId | url ; target OR-set: id | eventId —
        # matching the legacy predicates exactly (a url-only stub Source is
        # therefore NOT a valid target, preserving prior behavior).
        sources = self._resolve_entity(source_id, by_id=True, by_eventId=True, by_url=True)
        targets = self._resolve_entity(target_id, by_id=True, by_eventId=True)
        if not sources or not targets:
            return False
        # D10 B1 (#5206): ``aboutDocument``'s target is a DOCUMENT-bearing
        # :Source (ONTOLOGY v3.15 §4.4); a provenance/session/connector Source is
        # an `aboutSource` target instead. The replay resolver enforces this
        # (`resolve_structural_target`) but this live producer path did not, so a
        # producer-created edge to a non-document Source was created live,
        # transferred at supersede, then refused on `rebuild_all` — ending on
        # NEITHER node. Refusing here makes live and replay agree on the
        # TARGET KIND; it is not a durability guarantee — a document-bearing
        # :Source that replay does not re-materialize is dropped regardless,
        # which is the separate write-surface question tracked in #2296.
        if predicate == 'aboutDocument':
            for t in targets:
                why = about_document_target_reason(
                    t['label'], t.get('properties'))
                if why is not None:
                    raise ValueError(
                        f"aboutDocument target {target_id!r} is not a "
                        f"document-bearing :Source — it is {why}. "
                        "`aboutDocument` targets a DOCUMENT (ontology §4.4); "
                        "for a provenance/session/connector Source use "
                        "`aboutSource`. A live edge on a non-document Source "
                        "cannot survive `rebuild_all` (#5206), so accepting "
                        "it loses it silently.")
        # #390: mirror create_owned_by's circular-DAG guard for ownedBy — the
        # generic create_edge path must not bypass it. The new edge is
        # source -[:ownedBy]-> target; a cycle would close iff target already
        # (transitively) owns source. Same varlen 1..10 traversal + all
        # resolved (target, source) pairs as create_owned_by.
        if predicate == 'ownedBy':
            for t in targets:
                for s in sources:
                    cycle = self.g.query(
                        f"MATCH (t:{t['label']} {{{t['key']}:$tid}}) "
                        f"MATCH (s:{s['label']} {{{s['key']}:$sid}}) "
                        f"MATCH path = (t)-[:ownedBy*1..10]->(s) RETURN count(path) > 0",
                        params={"tid": t["value"], "sid": s["value"]},
                    )
                    if cycle.result_set and cycle.result_set[0][0]:
                        raise ValueError(
                            f"Circular ownership: {target_id} already owned by {source_id}")
        created = False
        # §17.1 (#9195): the lane's ONE mutating statement, DECLARED. Both
        # endpoints were just resolved by `MATCH` above, so this writes a bare
        # relationship and nothing else — read the block above
        # `STRUCTURAL_EDGE_WRITE_LANE` for what the lane claims per relation and
        # for the carrier gap it discloses rather than blesses.
        with _structural_edge_write_scope():
            for s in sources:
                for t in targets:
                    r = self.g.query(
                        f"MATCH (s:{s['label']} {{{s['key']}:$sv}}) "
                        f"MATCH (t:{t['label']} {{{t['key']}:$tv}}) "
                        f"MERGE (s)-[:{predicate}]->(t) RETURN count(*) > 0",
                        params={"sv": s["value"], "tv": t["value"]},
                    )
                    if r.result_set and r.result_set[0][0]:
                        created = True
        if created:
            self._record_structural_edges(predicate, sources, targets)
        return created

    def create_owned_by(self, entity_id: str, subject_id: str) -> bool:
        """Create ownedBy edge with circular ownership DAG check."""
        # Resolve endpoints by id (index-backed, issue #327); the varlen
        # traversal itself has no index in FalkorDB and is accepted.
        # All resolved (s, t) pairs are checked (original cartesian semantics).
        sources = self._resolve_entity(subject_id, by_id=True)
        targets = self._resolve_entity(entity_id, by_id=True)
        found_cycle = False
        for s in sources:
            for t in targets:
                cycle = self.g.query(
                    f"MATCH (s:{s['label']} {{{s['key']}:$sid}}) "
                    f"MATCH (t:{t['label']} {{{t['key']}:$tid}}) "
                    f"MATCH path = (s)-[:ownedBy*1..10]->(t) RETURN count(path) > 0",
                    params={"sid": s["value"], "tid": t["value"]},
                )
                if cycle.result_set and cycle.result_set[0][0]:
                    found_cycle = True
                    break
            if found_cycle:
                break
        if found_cycle:
            raise ValueError(f"Circular ownership: {subject_id} already owned by {entity_id}")
        return self.create_edge(entity_id, subject_id, 'ownedBy')

    def create_managed_by(self, entity_id: str, subject_id: str) -> bool:
        return self.create_edge(entity_id, subject_id, 'managedBy')

    def create_authored_by(self, entity_id: str, subject_id: str) -> bool:
        return self.create_edge(entity_id, subject_id, 'authoredBy')
