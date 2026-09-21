"""Entity CRUD handlers for FalkorProjection — Point, Subject, Object, Document, Event, Source.

#2490 rebuild-decay note (rides #2488, APPLIED): #2488's
``_fold_point_invalidated`` landed in this module and now appends
``decay_clause('n')`` to ITS terminalizing SET (both the unconditional and
``skip_updated_at`` branches) — otherwise INVALIDATED claims resurrect their
frozen posterior post-rebuild while superseded/retracted decay (the exact
ghost class #2490 eliminates).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

# #2795: cycle-free helper (tortoise/ids.py is stdlib-only). sdk.py imports
# projection at module top, so the sdk-private `_content_hash` is NOT
# importable here.
from tortoise.ids import content_hash as _content_hash
from tortoise.live import decay_clause  # #2490: rebuild folds decay terminal posteriors

logger = logging.getLogger(__name__)

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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


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


class _EntityHandlers:
    """Mixin: entity upsert/delete methods for FalkorProjection."""

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
    })
    _OBJECT_HANDLED: frozenset = frozenset({
        "id", "name", "object_kind", "objectKind", "createdAt", "title",
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
        # name / eventStatus / createdAt are intentionally NOT here —
        # they were historically dropped by the fixed-field MERGE and
        # are now persisted as arbitrary props via _persist_extra_props.
    })
    _SOURCE_HANDLED: frozenset = frozenset({
        "id", "url", "sourceKind", "contentHash",
        "title", "ingestedAt", "version", "externalId", "updatedAt",
        # epic #900 T3 (§4.1): the ev keys `source_path` (→ s.sourcePath via
        # the MERGE clause, never persisted verbatim snake_case) and
        # `_searchText` (set by the write path, coalesce-on-create /
        # overwrite-on-hash-diff — §4.1 cycle-4 merge semantics).
        "source_path",
        "_searchText",
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
        # A10 operator-scoped replay extension
        "direction", "label",
        # structural / edge-carried — never node props via passthrough
        "operator", "provenance", "about_entities", "aboutEntities",
        "extractedFrom", "is_episodic", "_nid", "_graph_id",
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
    # BEFORE they can reach the extras dict the drift warning inspects, so such
    # an entry is documentation-only until `_POINT_LIST_PROPS` is populated
    # from D1's contract.py. `tags` is exactly that case: it is DENIED on
    # replay (its raw-list half-restore is refused — #2897) and the drop is
    # reported by the undeclared-list warning below, NOT suppressed here.
    _POINT_DECLARED_PROPS: frozenset = frozenset({
        "quote", "when", "search_keys", "speaker", "source_turn_id", "tags",
        # #3689 review P2 (A): the four canonical annotator dims are legitimately
        # carried on a PointAdded snapshot by `create_point(annotator_*=…)` /
        # `_update_entity` — declaring them keeps the replay open-set
        # passthrough from logging a FALSE `"Point prop %r is not declared"`
        # drift warning on every rebuild.
        "annotator_bias", "annotator_precision",
        "annotator_consistency", "annotator_directness",
    })
    # #2795 (D2 mechanic 1): list-valued props are persisted ONLY when their
    # key is declared here. Arrays are not fulltext-indexed and the canonical
    # form is the declared `flatten=list` STRING (`search_keys` -> space-joined
    # by `_flatten_search_keys_prop`); `tags` is owned by its own `_sync_tags`
    # path and its TAGGED-edge replay is out of scope (#2897), so a raw-list
    # half-restore is refused. NOTE the live/rebuild boundary this creates:
    # `sdk.create_point` still writes raw lists directly (`SET n += $props`),
    # so a live `n.tags` exists while replay drops it — the pre-existing #2897
    # gap (the drop is now REPORTED, not silent). Empty pre-D1: no list prop
    # passes through the generic Point filter today. Replaced by contract.py's
    # declared set in D1.
    _POINT_LIST_PROPS: frozenset = frozenset()

    def _persist_extra_props(self, match_clause: str, match_params: dict,
                              ev: dict, handled_keys: frozenset,
                              list_props: frozenset | None = None) -> dict:
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

        Returns the dict of props actually persisted (empty when none) so the
        caller can report unrecognised keys (#2795 drift warning).
        """
        skip = self._META_KEYS | handled_keys
        extra = {}
        for k, v in ev.items():
            if k in skip or v is None or not _is_persistable_prop_value(v):
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

    def _upsert_point_props(self, p: dict) -> tuple[bool, bool]:
        """Write all Point node properties (no edges).

        Single source of truth for Point property parity between apply() and
        rebuild_all() (#330): rebuild pass 1a calls this so a rebuilt graph can
        never drift from the incrementally-applied graph on node properties.

        Returns ``(embedding_written, content_hash_written)`` — the two
        CONDITIONAL derived writes. The fixed SET list writes them as
        ``n.embedding = CASE WHEN $embedding IS NOT NULL … ELSE n.embedding
        END`` and ``n.content_hash = coalesce($ch, …)``, and computes neither
        for an operator, falsy content, an unavailable embedder, or a raising
        ``_content_hash`` — so in those cases the existing value is PRESERVED.
        #4042's pass-1b content boundary needs that outcome exactly, never a
        ``bool(content)`` guess. Every other caller ignores the return.
        """
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

        # Compute embedding for non-operator Points (#7778)
        embedding = None
        # #331 (review r4): only embed real content — an empty string
        # produced a junk vector in the HNSW index.
        if not op and p.get("content"):
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
            "n.embedding=CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) ELSE n.embedding END",
            "n.content_hash=coalesce($ch, n.content_hash)",
            "n.confidence=coalesce($cf, n.confidence)",
            "n.createdAt=coalesce($ca, n.createdAt, $now)",
            "n.validFrom=coalesce($vf, n.validFrom)",
            "n.validTo=coalesce($vt, n.validTo)",
            "n.updatedAt=$now",
        ]
        params = {
            "id": p["id"], "content": p.get("content", ""),
            "isop": bool(op), "opt": op.get("op_type") if op else None,
            "pk": p.get("pointKind"),
            "st": p.get("status"),
            "ab": p.get("authoredBy"),
            "embedding": embedding,
            "ch": point_content_hash,
            "cf": p.get("confidence"),
            "ca": p.get("createdAt") or p.get("created_at"),
            "vf": p.get("validFrom"), "vt": p.get("validTo"),
            "now": _now_iso(),
        }
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
        # Phase 2 #49: context removed — never written
        self.g.query(
            "MERGE (n:Point {id:$id}) SET " + ", ".join(set_clauses),
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
        # #2958 review: warn ONCE per key per rebuild pass (the `_deny_drop_warned`
        # set, reset by `rebuild_all`) — the #548 synthetic snapshot carries
        # EP-owned state for every dreamed graph-only point, so a per-row
        # warning emitted O(N) lines and buried genuine violations. On the live
        # path the set persists for the process, which is the right cadence for
        # a policy-level signal.
        for key in self._POINT_DENY - self._POINT_HANDLED:
            if p.get(key) is None:
                continue
            warned = getattr(self, "_deny_drop_warned", None)
            if warned is None:
                warned = self._deny_drop_warned = set()
            if key in warned:
                continue
            warned.add(key)
            logger.warning(
                "Point prop %r dropped — deny-listed (recompute/EP-owned, "
                "#2795); not restorable from the payload", key)
        # (b) a policy-denied list is also reported: the live SDK writer
        # still stores raw lists (e.g. `tags`), but replay refuses them, so the
        # drop MUST be visible (#2795 indicator 4 / D7; `tags` -> #2897).
        # #2958 review: tuples count as arrays here too (engine parity).
        for key, val in p.items():
            if not (isinstance(val, (list, tuple)) and val):
                continue
            if key in self._POINT_LIST_PROPS or key in self._POINT_HANDLED \
                    or key in self._POINT_DENY or key in self._META_KEYS:
                continue
            logger.warning(
                "Point list prop %r dropped — undeclared list props are never "
                "written raw (#2795); not restorable from the payload", key)
        # #4042: report which conditional derived writes actually landed (see
        # the docstring). `embedding`/`point_content_hash` are exactly the
        # values the `CASE`/`coalesce` clauses above gate on.
        return embedding is not None, point_content_hash is not None

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
        source_ref = p.get("extractedFrom")
        if source_ref:
            self._link_source(p["id"], source_ref)
        # #3947: the episodic turn stream is `(:Session)-[:CONTAINS]->(:Point)`
        # (ONTOLOGY §4.5, the session container's one structural edge). NOT a
        # member of the deferred generic direct-edge replay (#1048: caller-
        # authored `create_direct_edge` descriptors stay unaligned on
        # rebuild): it carries no caller attrs, and it is the capture TURN LOOP
        # ALONE that journals the link, on the turn's own PointAdded (the only
        # two `contains_session=` emission sites are the SDK and hosted turn
        # loops). The extractor-minted points are wired into the Session by
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
        "Session", "Point", "Document", "Event", "Object", "Subject",
        "Source",
    })
    # ONTOLOGY §3.2 triples — the field sets above are their projections, but
    # membership in each set does NOT imply the COMBINATION is legal
    # (``(Session)-[:aboutSubject]->(Subject)`` is in all three sets and in no
    # triple). Mirrored EXACTLY from ``session_link.ENTITY_LINKED_TRIPLES`` and
    # pinned by the drift test.
    _ENTITY_LINKED_TRIPLES: frozenset = frozenset({
        ("aboutSubject", "Point", "Subject"),
        ("aboutSubject", "Document", "Subject"),
        ("aboutSubject", "Event", "Subject"),
        ("aboutObject", "Point", "Object"),
        ("aboutObject", "Document", "Object"),
        ("aboutObject", "Event", "Object"),
        ("aboutObject", "Session", "Object"),
        ("aboutEvent", "Point", "Event"),
        ("aboutEvent", "Document", "Event"),
        ("aboutPoint", "Event", "Point"),
        ("aboutDocument", "Event", "Document"),
        ("aboutSource", "Point", "Source"),
        ("aboutSource", "Document", "Source"),
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
        r = self.g.query(
            f"MATCH (s:{src_label} {{id:$sid}}), (t:{tgt_label} {{id:$tid}}) "
            f"MERGE (s)-[:{rel}]->(t) RETURN count(s)",
            params={"sid": sid, "tid": tid},
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
        edge from it) replays. Idempotent MERGE keyed on ``id``; ``created_at``
        and ``actor_user_id`` are coalesce-preserved (first writer wins,
        mirroring the live merge), ``turn_count`` tracks the latest journaled
        capture. Returns 1 when the node exists after the fold, 0 on a
        malformed record.

        BOTH the id and every journal-derived property value are gated for
        WRITABILITY, not just type (review P1): a NUL / lone-surrogate id and
        a map-valued ``created_at`` / ``turn_count`` / ``harness`` /
        ``actor_user_id`` payload each raise at parameter parse, and ``rebuild_all`` folds this
        record INLINE (no try/except) AFTER the wipe. A malformed id is a
        NO-OP (return 0); a malformed field is OMITTED, never bound.

        ``entity_links_attempted`` / ``entity_links_created`` are carried by a
        SECOND ``SessionRecorded`` the capture emits after the link pass
        (``sdk.capture_session``), so the counters the live raw SET writes are
        durable too — ``recover_from_log`` / a journal-only ``rebuild()``
        otherwise came back with them null (review P2, #3722).

        ``capture_ok`` / ``capture_extractor`` ride a THIRD, TRAILING
        ``SessionRecorded`` the capture emits right after the live
        ``SET s.capture_ok / s.capture_extractor``. Without it those two came
        back null on a journal-only rebuild, and null is CONSUMED by the
        #2335 WI-2b TRUE-retry gate as the legacy "presumed captured" case — a
        session whose capture FAILED stopped retrying (review P2, #3722).
        Same overwrite semantics as the live SET (these are not
        coalesce-preserved); a NUL-laden string is OMITTED by the shared value
        gate, never bound.
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
        for prop in ("turn_count", "harness", "entity_links_attempted",
                     "entity_links_created", "capture_ok",
                     "capture_extractor"):
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
        self.g.query(
            "MERGE (s:Session {id:$sid}) SET s.is_episodic=true",
            params={"sid": session_id},
        )
        self.g.query(
            "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
            "MERGE (s)-[:CONTAINS]->(t)",
            params={"sid": session_id, "tid": point_id},
        )

    def _upsert(self, p: dict, contains_session: str | None = None) -> None:
        """Upsert a Point: node properties via _upsert_point_props, then edges."""
        self._upsert_point_props(p)
        self._upsert_point_edges(p, contains_session=contains_session)

    def _delete(self, pid: str) -> None:
        self.g.query("MATCH (n:Point {id:$id}) DETACH DELETE n", params={"id": pid})

    def _retract(self, pid: str) -> None:
        """Mark a Point as retracted instead of hard-deleting (#689).

        Retracted points are hidden from normal reads (get_point, query,
        paginated_query all filter status='retracted') but remain queryable via
        raw Cypher. This preserves data integrity — retraction is reversible.

        Historical note: prior to #689, retraction hard-deleted points via
        DETACH DELETE. Points retracted before this change are irrecoverably
        lost (the content existed only in the projection, and the projection
        deleted it). Future retractions leave this tombstone.
        """
        self.g.query(
            "MATCH (n:Point {id:$id}) SET n.status = 'retracted', n.updatedAt = $now, "
            f"{decay_clause('n')}",
            params={"id": pid, "now": _now_iso()},
        )

    def _fold_point_superseded(self, ev: dict) -> int:
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
        expired_at = ev.get("expired_at") or _now_iso()
        updated_at = ev.get("ts") or _now_iso()
        result = self.g.query(
            "MATCH (n:Point {id:$id}) "
            "SET n.status='superseded', n.outdated=true, "
            "    n.validTo=$vt, n.expiredAt=$ea, n.updatedAt=$ua, "
            f"    {decay_clause('n')} "
            "RETURN n.id LIMIT 1",
            params={"id": oid, "vt": valid_to, "ea": expired_at,
                    "ua": updated_at},
        )
        if result.result_set:
            self.g.query(
                "MATCH (a:Point {id:$new_id}), (b:Point {id:$old_id}) "
                "MERGE (a)-[:CORRECTS]->(b)",
                params={"new_id": new_id, "old_id": oid},
            )
        return len(result.result_set)

    def _fold_point_invalidated(self, ev: dict, skip_updated_at: bool = False) -> int:
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
        expired_at = ev.get("expired_at") or _now_iso()
        updated_at = ev.get("ts") or _now_iso()
        if skip_updated_at:
            # A later same-id PointRevised/PointPromoted already stamped
            # updatedAt (inline, pass-1b) — omit the column so this fold
            # cannot clobber the newer stamp with the older invalidate ts.
            set_clause = ("SET n.outdated=true, n.validTo=$vt, n.expiredAt=$ea, "
                          f"{decay_clause('n')} ")
            params = {"id": oid, "vt": valid_to, "ea": expired_at}
        else:
            # Unconditional updatedAt write: this sweep fold is the id's last
            # journal writer → exact live parity (supersede's precedent).
            set_clause = ("SET n.outdated=true, n.validTo=$vt, "
                          "n.expiredAt=$ea, n.updatedAt=$ua, "
                          f"{decay_clause('n')} ")
            params = {"id": oid, "vt": valid_to, "ea": expired_at,
                      "ua": updated_at}
        result = self.g.query(
            "MATCH (n:Point {id:$id}) " + set_clause + "RETURN n.id LIMIT 1",
            params=params,
        )
        if result.result_set and corrected_by:
            # Best-effort edge arm: a missing/deleted corrected_by point
            # (never re-created, hard-deleted) silently no-ops the MERGE.
            # A RAW producer omitting corrected_by entirely still gets the
            # outdated flag + stamps (the #2488 fix needs only oid) — only
            # the CORRECTS arm is gated on it (P2-2, code-review).
            self.g.query(
                "MATCH (a:Point {id:$new_id}), (b:Point {id:$old_id}) "
                "MERGE (a)-[:CORRECTS]->(b)",
                params={"new_id": corrected_by, "old_id": oid},
            )
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
        signal): 1 = the target Point was found and folded, 0 = no Point
        matched (hard-deleted / never re-created) or the record carries no
        foldable value. Idempotent — a replayed/duplicate event re-applies the
        same SET.
        """
        oid = ev.get("id")
        if not isinstance(oid, str) or not oid:
            return 0
        set_parts: list[str] = []
        params: dict = {"id": oid}
        for key in ("confidence", "posterior_alpha", "posterior_beta",
                    "lastDreamedAt"):
            if key not in ev:
                continue
            value = ev[key]
            # Reject value shapes FalkorDB cannot take as a parameter (a
            # corrupt journal line must not abort rebuild_all AFTER the wipe
            # — the #331/_writable_id precedent). ``None`` is valid: it is
            # the journaled clear.
            if value is not None and not _is_persistable_prop_value(value):
                continue
            set_parts.append(f"n.{key} = ${key}")
            params[key] = value
        if not set_parts:
            return 0
        result = self.g.query(
            "MATCH (n:Point {id:$id}) SET " + ", ".join(set_parts) +
            " RETURN n.id LIMIT 1",
            params=params,
        )
        return len(result.result_set)

    # ── Entity nodes ───────────────────────────────────────────────

    def _upsert_subject(self, ev: dict) -> None:
        """MERGE Subject by name (content-hash dedup)."""
        sid = ev.get("id")
        name = ev.get("name", "")
        if not sid or not name:
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
        # with a RANDOM ulid id before this entity path's SubjectAdded lands;
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
        self.g.query(
            "MERGE (s:Subject {name:$name}) "
            "ON CREATE SET s.id=$id, s.subjectKind=$sk, s.createdAt=coalesce($ca, $now), "
            "            s.embedding=CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) ELSE s.embedding END "
            "ON MATCH SET s.id=coalesce($id, s.id), "
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
            "            s.embedding=CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) ELSE s.embedding END",
            params={"id": sid, "name": name,
                    "sk": ev.get("subject_kind", "other"),
                    "ca": ev.get("createdAt"), "now": _now_iso(),
                    "embedding": embedding},
        )
        # #228: persist arbitrary caller-supplied props
        self._persist_extra_props(
            "MATCH (n:Subject {name: $name})", {"name": name},
            ev, self._SUBJECT_HANDLED,
        )

    def _upsert_object(self, ev: dict) -> None:
        """MERGE Object by name (content-hash dedup).

        Objects are encoded via the Source→references→Object chain —
        embedding from name provides direct vector search capability
        while the provenance chain traces back to source content (#7845).
        """
        oid = ev.get("id")
        name = ev.get("name", "")
        if not oid or not name:
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
        # wiring in _event_plain_merge can mint a name-stub Object (random
        # ulid) when a poll/webhook event lands BEFORE this entity path's
        # first ObjectRegistered. Without the ON MATCH id write, the MERGE
        # below adopts the stub by NAME and the canonical id never lands on
        # any node — aboutSubject wiring (MATCH by o.id) silently matches
        # nothing. `coalesce($id, o.id)` is idempotent: $id is always the
        # same deterministic id for a given name across producers (this
        # function early-returns when the caller sends no id, so entities
        # without ids never fire the clause).
        self.g.query(
            "MERGE (o:Object {name:$name}) "
            "ON CREATE SET o.id=$id, o.objectKind=coalesce($ok, 'other'), o.createdAt=coalesce($ca, $now), o.title=coalesce($title, ''), "
            "            o.status=coalesce($st, 'live'), "
            "            o.embedding=CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) ELSE o.embedding END "
            "ON MATCH SET o.id=coalesce($id, o.id), "
            "            o.objectKind=coalesce($ok, o.objectKind), "
            "            o.createdAt=coalesce(o.createdAt, $ca), "
            "            o.title=coalesce($title, o.title), "
            "            o.embedding=CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) ELSE o.embedding END",
            params={"id": oid, "name": name,
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
        # #228: persist arbitrary caller-supplied props
        self._persist_extra_props(
            "MATCH (n:Object {name: $name})", {"name": name},
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
        supersedes_by = str(ev.get("supersedes_by") or "")[:200]
        # #2164 final-review P4: prefer the journaled event's ORIGINAL ts —
        # rebuild pass-1b replays the raw journaled event (sdk._emit_event
        # stamps ts on the JSONL line) — without this a JSONL wipe+rebuild
        # drifted supersededAt to rebuild time. Live callers (apply_super-
        # sessions' fold_ev carries no ts) fall back to now.
        superseded_at = ev.get("ts") or _now_iso()
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
        """MERGE Document node."""
        did = ev.get("id")
        if not did:
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
        ds = ev.get("doc_status")
        # #133: needs_extraction — explicit signal for --upgrade-all discovery.
        # coalesce-null sentinel: None default so partial updates preserve.
        nx = ev.get("needs_extraction")
        # _searchText computed only when the event carries meaningful text
        has_text = bool(ev.get("title") or summary or topics)
        st = (_build_search_text(ev.get("title", ""), summary, topics)
              if has_text else None)
        # #167: sourcePath — coalesce-null sentinel (no "" default) so
        # partial updates preserve existing value
        sp = ev.get("source_path")
        self.g.query(
            "MERGE (d:Document {id:$id}) "
            "SET d.title=coalesce($title, d.title), "
            "    d.documentKind=coalesce($dk, d.documentKind), "
            "    d.format=coalesce($fmt, d.format), "
            "    d.content=coalesce($content, d.content), "
            "    d.topics=coalesce($topics, d.topics, []), "
            "    d.summary=coalesce($summary, d.summary, ''), "
            "    d.sessionId=coalesce($sid, d.sessionId, ''), "
            "    d.eventId=coalesce($eid, d.eventId, ''), "
            "    d.doc_status=coalesce($ds, d.doc_status, 'draft'), "
            "    d.needs_extraction=coalesce($nx, d.needs_extraction, false), "
            "    d.sourcePath=coalesce($sp, d.sourcePath), "
            "    d._searchText=coalesce($st, d._searchText, d.title), "
            "    d.embedding=CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) ELSE d.embedding END, "
            "    d.updatedAt=$now",
            params={"id": did, "title": ev.get("title", did),
                    "dk": ev.get("document_kind", ""),
                    "fmt": ev.get("format", "markdown"),
                    "content": ev.get("content"),
                    "topics": topics, "summary": summary, "sid": sid,
                    "eid": eid, "ds": ds, "nx": nx, "st": st, "sp": sp,
                    "embedding": embedding,
                    "now": _now_iso()},
        )
        # #228: persist arbitrary caller-supplied props (before edge wiring
        # so that extra props land on the Document node regardless of edge success)
        self._persist_extra_props(
            "MATCH (n:Document {id: $id})", {"id": did},
            ev, self._DOCUMENT_HANDLED,
        )
        # #205 — wire references edge (Source → Document) for provenance chain.
        # Epic #900 T3 (§4.1 route pin): under OQ-6 doc ids are `doc_<rel-path>`
        # ≠ the corpus:// Source url, so the hard-coded did==did auto-wire would
        # MERGE a PHANTOM Source (url=doc_<rel>, empty contentHash). The
        # optional `source_url` ev-key override (default falls back to did —
        # legacy ingest flow byte-identical) routes the #205 link onto the real
        # Source the indexer created first. The override rides the journaled
        # DocumentCreated event, so replay re-creates the edge onto the real
        # Source (S13/T12 split: doc-unit references edges SURVIVE rebuild).
        self.link_source_to_entity(ev.get("source_url") or did, did, "Document")
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
        if not eid:
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
            # Stored vecf32 (#244): vec.euclideanDistance rejects plain-list
            # vectors — a single List-typed embedding poisons brute-force vector
            # search for the whole Event label. Align with the Point pattern.
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

    def _event_plain_merge(self, eid: str, props: dict, embedding,
                           inner: dict) -> None:
        """The legacy single-statement Event MERGE (+ edges + extra props)."""
        self.g.query(
            "MERGE (e:Event {eventId: $eid}) "
            "ON CREATE SET e += $props, e.embedding = CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) END "
            "ON MATCH SET e += $props, e.embedding = CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) ELSE e.embedding END",
            params={"eid": eid, "props": props, "embedding": embedding},
        )
        # ── Auto-create structural edges (#122) ──
        # Subject -[:performs]-> Event
        subj = inner.get("subject", "")
        if subj:
            from tortoise.ids import ulid
            stub_id = ulid()
            self.g.query(
                "MERGE (s:Subject {name:$name}) "
                "ON CREATE SET s.id=$id, s.subjectKind='other'",
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
        if obj:
            if object_type == "Document":
                # #329: the minted Document id is tenant-influenced (event
                # props passthrough) — validate it so it can never be a host
                # path (the read side also fails closed via resolve_under_base).
                from tortoise.security import validate_document_id
                validate_document_id(str(obj))
                self.g.query(
                    "MERGE (d:Document {id:$id}) "
                    "ON CREATE SET d.title=$id, d.documentKind='transcript'",
                    params={"id": obj},
                )
                self.g.query(
                    "MATCH (d:Document {id:$id}), (e:Event {eventId:$eid}) "
                    "MERGE (e)-[:produces]->(d)",
                    params={"id": obj, "eid": eid},
                )
            else:
                from tortoise.ids import ulid
                stub_id = ulid()
                # #1155-P1: id is written ON CREATE ONLY — the stub id is a
                # RANDOM ulid, and a poll/webhook event may arrive before the
                # entity path's ObjectRegistered. If this MERGE also wrote id
                # on MATCH, a late stub would CLOBBER the canonical id
                # (github-issue-{repo}-{n}) that _upsert_object set. The
                # canonical id wins via _upsert_object's ON MATCH
                # `o.id=coalesce($id, o.id)` — do not add an id write here.
                self.g.query(
                    "MERGE (o:Object {name:$name}) "
                    "ON CREATE SET o.id=$id, o.objectKind='other'",
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
        # M3-P1 guard (#2164): the fold MATCHes only non-superseded Objects —
        # a dual-tracked Object (connector work item conversationally
        # superseded via the capture fold) must NOT be silently resurrected
        # into recall_state's default view by a later connector lifecycle
        # event. Aligns with the #1350 clobber doctrine (a re-mention cannot
        # reset superseded→live). Live Objects (status IS NULL or <>'superseded')
        # still fold normally.
        if _obj_name and _wk in ("pm:cardCreated", "github.issue.open",
                                 "github.issue.reopened"):
            self.g.query(
                "MATCH (o:Object {name:$n}) "
                "WHERE (o.status IS NULL OR o.status <> 'superseded') "
                "SET o.status='in_progress'",
                params={"n": _obj_name})
        elif _obj_name and _wk in ("pm:cardCompleted", "github.issue.closed"):
            self.g.query(
                "MATCH (o:Object {name:$n}) "
                "WHERE (o.status IS NULL OR o.status <> 'superseded') "
                "SET o.status='completed'",
                params={"n": _obj_name})
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
                if use_name:
                    from tortoise.ids import ulid
                    stub_id = ulid()
                    self.g.query(
                        "MERGE (o:Object {name:$name}) "
                        "ON CREATE SET o.id=$id, o.objectKind=$kind "
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
            which bumps on hash-diff — TypeGraph churn pitfall).

        ``(Source)-[:references]->(Object {id})`` is wired ONLY when the event
        carries an explicit ``sourceObjectId`` (set exclusively by the github
        entity path). ``event.object`` is NEVER used as an Object key — on
        poll/webhook paths it is the entity TITLE string and
        ``_event_plain_merge`` already stubs ``Object {name: title}`` with a
        random ulid; using it would wire references to the wrong stub.
        """
        sk = inner.get("sourceKind")
        source_url = inner.get("sourceUrl")
        if sk not in _CONNECTOR_SOURCE_KINDS and not source_url:
            return
        url = source_url or inner.get("source", "")
        if not url:
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
            "    s.contentHash = '', s.ingestedAt = $now "
            "ON MATCH SET s.sourceKind = coalesce(s.sourceKind, $sk)",
            params={"url": url, "sk": sk or "document", "now": _now_iso()},
        )
        # (Source)-[:references]->(Event) — always, when the event exists.
        self.g.query(
            "MATCH (s:Source {url: $url}), (e:Event {eventId: $eid}) "
            "MERGE (s)-[:references]->(e)",
            params={"url": url, "eid": eid},
        )
        # #388 conf-62/conf-60: a fallback-key materialization (`slack:{channel}` /
        # `linear:{team_key}` / bare `source`) can predate the real URL (a
        # permalink becomes available later, or a later poll resolves the
        # container key). The new materialization wires a SECOND Source to the
        # same event → duplicated provenance entries. Clean up: drop every
        # `(Source)-[:references]->(Event)` edge whose url differs from the
        # now-authoritative url, and delete the Source node outright if that
        # edge was its ONLY relationship (deg=1 → orphaned). Shared Sources
        # (still referencing other events / extractedFrom by Points) keep the
        # node — only the superseded edge goes. EP-neutral: connector kinds
        # are neutral on both sides of the swap. Direction-guarded by conf-60
        # above: this sweep runs only when the incoming url is a real URL (or
        # no real-URL Source references the event), so a fallback key can
        # never supersede a real permalink Source.
        self.g.query(
            "MATCH (old:Source)-[r:references]->(e:Event {eventId: $eid}) "
            "WHERE old.url <> $url "
            "WITH old, r, size([(old)-[x]-(y) | x]) AS deg "
            "DELETE r "
            "WITH old, deg "
            "WHERE deg = 1 "
            "DELETE old",
            params={"url": url, "eid": eid},
        )
        # (Source)-[:references]->(Object {id}) — only on explicit
        # sourceObjectId (github entity path; event.object is never an Object
        # key — see docstring).
        obj_id = inner.get("sourceObjectId")
        if obj_id:
            self.g.query(
                "MATCH (s:Source {url: $url}), (o:Object {id: $oid}) "
                "MERGE (s)-[:references]->(o)",
                params={"url": url, "oid": obj_id},
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
            if k not in skip and v is not None and k not in full:
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
          - ``s._searchText`` — coalesce ON CREATE, OVERWRITE on hash-diff
            MERGE (§4.1 cycle-4 merge semantics; E2E-5 retitle refresh);
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
        ``create_source``'s index-path consumers.
        """
        sid = ev.get("id")
        url = ev.get("url", "")
        if not sid and not url:
            return None
        key = url or sid
        search_text = ev.get("_searchText") or ev.get("title")
        run_clause = ", s.__runId = $rid" if merge_run_id is not None else ""
        r = self.g.query(
            "MERGE (s:Source {url: $url}) "
            "ON CREATE SET s.id = coalesce($id, $url), "
            "              s.sourceKind = $sk, "
            "              s.contentHash = coalesce($hash, ''), "
            "              s.title = $title, "
            "              s.ingestedAt = $now, "
            "              s.version = 1, "
            "              s.externalId = $ext, "
            "              s.sourcePath = coalesce($sp, s.sourcePath), "
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
            "ON MATCH SET s.contentHash = CASE WHEN $hash IS NULL THEN s.contentHash "
            "                          WHEN s.contentHash IS NULL OR s.contentHash <> $hash "
            "                          THEN $hash ELSE s.contentHash END, "
            "           s.title = CASE WHEN $hash IS NULL THEN s.title "
            "                   WHEN s.contentHash IS NULL OR s.contentHash <> $hash "
            "                   THEN coalesce($title, s.title) ELSE s.title END, "
            "           s.version = CASE WHEN $hash IS NULL THEN s.version "
            "                    WHEN s.contentHash IS NULL OR s.contentHash <> $hash "
            "                    THEN s.version + 1 ELSE s.version END, "
            "           s.updatedAt = CASE WHEN $hash IS NULL THEN s.updatedAt "
            "                     WHEN s.contentHash IS NULL OR s.contentHash <> $hash "
            "                     THEN $now ELSE s.updatedAt END, "
            "           s.sourcePath = coalesce($sp, s.sourcePath), "
            "           s._searchText = CASE WHEN $hash IS NULL THEN s._searchText "
            "                        WHEN s.contentHash IS NULL OR s.contentHash <> $hash "
            "                        THEN $st ELSE s._searchText END",
            params={
                "url": key, "id": sid or key,
                "sk": ev.get("sourceKind", "document"),
                "hash": ev.get("contentHash"),
                "title": ev.get("title", key),
                "now": _now_iso(),
                "ext": ev.get("externalId", ""),
                "sp": ev.get("source_path"),
                "st": search_text,
                **({"rid": merge_run_id} if merge_run_id is not None else {}),
            },
        )
        # #228: persist arbitrary caller-supplied props
        self._persist_extra_props(
            "MATCH (n:Source {url: $url})", {"url": key},
            ev, self._SOURCE_HANDLED,
        )
        return r
