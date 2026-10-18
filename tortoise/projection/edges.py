"""Edge creation and linking methods for FalkorProjection."""
from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timezone

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
# whose resurrection the replay delete-leg must clean. aboutAction
# (Action dissolved in Ontology v3.0), aboutSource, and wasDerivedFrom are NOT
# derivable — never snapshot-recreated, never resurrect at old — so they get no
# descriptor (emitting one would half-own the A10 raw-edge family by accident).
DERIVABLE_STRUCTURAL_RELS = frozenset({
    'aboutSubject', 'aboutObject', 'aboutEvent', 'aboutPoint',
    'aboutDocument', 'extractedFrom',
})

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


def _mint_subject_stub(g, name: str) -> None:
    """MERGE the Subject stub live wiring's auto-detect fallback creates
    (edges.py _create_about_edges) — single create path for live + replay so a
    replayed descriptor mints a byte-identical stub to live wiring.

    #7369: `name` IS the MERGE key, so an unwritable one aborts the replay
    after the wipe. Skip rather than raise.
    """
    from tortoise.projection import _log_identity_skip, _writable_id
    if not _writable_id(name):
        _log_identity_skip("Subject stub", name, "name (MERGE key)")
        return
    g.query(
        "MERGE (s:Subject {name:$name}) "
        "ON CREATE SET s.id=$name, s.subjectKind='other'",
        params={"name": name},
    )


def _mint_source_stub(g, url: str, source_kind: str | None = None) -> str | None:
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
              "sk": source_kind, "now": _now_iso()}
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
                              mint: bool = True):
    """Resolve the label-scoped structural target node by its replay key.

    Create-if-missing semantics: Subject / Source stubs MERGE by key (the live
    wiring precedent — _create_about_edges' fallback / _link_source). NEVER
    auto-detect labels (Subject-first auto-detect would attach an aboutObject
    descriptor to a same-name Subject node) and NEVER mint Point stubs by name
    (an absent Point target => skip — absent Documents/Objects/Events return
    None too; those entities are journaled by their own creation events, and an
    absent one is an unjournaled-producer edge that dies at rebuild).

    ``mint=False`` (#8398 review P1-2) forbids the Subject/Source stub mint and
    leaves pure resolution. A #8398 link whose live cascade RESOLVED an
    existing node must not mint a replacement when that node is missing at
    replay: live created nothing, so a minted stub would be a node the live
    graph never had. The missing endpoint is then reported as a failed link
    instead — the honest outcome for an unjournaled producer.

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
            # D10 B1 live-parity (adversarial): the LIVE auto-detect
            # (`_resolve_about_edge_target`) resolves an aboutDocument target
            # only when
            # `e.documentKind IS NOT NULL`, so a session/connector/provenance
            # Source can never become an aboutDocument target. This replay
            # match MUST carry the same guard — without it a producer-created
            # edge (reachable through the public `sdk.create_edge`) to a
            # provenance Source is re-attached to that non-document Source on
            # rebuild, i.e. live and replay disagree (the #2489 "one create
            # path / byte-identical stubs" invariant). The guard is
            # DELIBERATE live-parity, not an accident: a wrong target must be
            # refused here, exactly as live refuses it.
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
    applies). Live auto-detect refused a non-document Source
    and so did replay, but the live producer (``create_edge``) refused nothing —
    an edge created through it was built, transferred at supersede and then
    dropped by ``rebuild_all``, ending on NEITHER node (#5206). ``url`` is part
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
# Referential-containment links (Source→Source) stay property-free: a `"Source"`
# caller lands in the property-free branch below, and the in-repo containment
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


class _EdgeHandlers:
    """Mixin: edge creation, about edges, source linking, edge stats."""

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
        # source). #2489: the mint is routed through the SHARED helper
        # (_mint_subject_stub) so live wiring and rebuild replay mint
        # byte-identical stubs (one create path). The stub's id IS its name
        # (`ON CREATE SET s.id=$name`), which is why the name is a valid
        # ``targets`` value here.
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
        live path must mint the ``:Subject`` stub first (the cascade's
        fallback). Writing by the RESOLVED internal id — rather than
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
        valid = {'aboutSubject', 'aboutObject', 'aboutEvent', 'aboutPoint', 'aboutDocument'}
        if edge_type not in valid:
            raise ValueError(f"Invalid about edge type: {edge_type}. Must be one of {valid}")
        
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
        entity label — Point (default) or Document (create_document provenance,
        #394).

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
        signature is unchanged. Identity/mention links (``Object``) stay
        property-free. Written ``ON CREATE`` only: a re-link must NOT advance the
        recorded version, or staleness would silently read as current.

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
        # referential-containment link, which the model keeps property-free.
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
        ``projection/entities.py`` — which are deliberately left property-free; see
        ``STORAGE-ARCHITECTURE.md`` §9.6).

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
        Matches target by id OR eventId (Event nodes use eventId as key)."""
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
