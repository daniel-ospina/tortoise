"""EventAPI — the shared append surface both producers write through.

Attribution (`initiated_by`, `agent_id`) is bound once at construction, so the
extractor gets initiated_by="extractor" and a user-agent gets "user" without
per-call bookkeeping (see README Interaction model).

Every mutation appends exactly one event to the log AND (if a projection is
attached) applies it incrementally, so the store never drifts from the log.
"""
from __future__ import annotations

import logging

from .idempotency import IngestKey, IngestResult
from .ids import now_iso, ulid
from .projection import fold

logger = logging.getLogger(__name__)


def provenance(source_id, span, quote, *, speaker=None, extracted_by="mock@0"):
    return {
        "source_id": source_id,
        "speaker": speaker,
        "span": span,
        "quote": quote,
        "extracted_by": extracted_by,
        "extracted_at": now_iso(),
    }


class EventAPI:
    def __init__(self, log, *, initiated_by: str, agent_id: str | None = None,
                 projection=None):
        assert initiated_by in ("extractor", "user")
        self.log = log
        self.initiated_by = initiated_by
        self.agent_id = agent_id
        self.projection = projection
        self.current_run: str | None = None
        self._ingest_cache: dict[tuple, str] | None = None
        # Gate 4: SVBP instance (lazy-init on first confidence query)
        self._svbp = None

    # -- internals -----------------------------------------------------------
    def _emit(self, type_: str, *, corrects=None, actor: str | None = None,
              **payload) -> dict:
        event = {
            "event_id": ulid(),
            "ts": now_iso(),
            "type": type_,
            "initiated_by": self.initiated_by,
            "agent_id": self.agent_id,
            "corrects": corrects,
            **payload,
        }
        # #2600: optional human actor (default None = extraction lanes
        # byte-identical). `actor` is a keyword parameter on `_emit` — the
        # SDK emitter provides it; EventAPI callers never touch it. No
        # caller change required for existing lanes.
        if actor is not None:
            event["actor_user_id"] = actor
        self.log.append(event)
        if self.projection is not None:
            self.projection.apply(event)
        return event

    def emit_belief(self, type_: str, **payload) -> None:
        """#2884 A6: best-effort journal append for the EP/dream belief write-back.

        The ingest CLI's lazy EP propagation passes its ``EventAPI`` here
        instead of hand-building a JSONL envelope, so this lane's belief
        records go through the SAME ``_emit`` shape as every other ingest
        event (one envelope, not three). ``_emit`` is guarded here because the
        graph write has already committed by the time EP returns: an
        ``OSError``/``ENOSPC`` must never propagate out of ``ep.run()`` and
        crash the public ingest CLI. Mirrors
        ``TortoiseSDK._emit_event``'s best-effort JSONL branch.
        """
        try:
            self._emit(type_, **payload)
        except Exception as exc:  # noqa: BLE001, RUF100
            logger.warning(
                "failed to append %s event to ingest log: %s", type_, exc)

    def _point(self, content, provenance, operator=None) -> dict:
        prov = dict(provenance)
        prov.setdefault("run_id", self.current_run)
        # #49 Phase 2: context param removed — context is deprecated.
        # Context/domain is now tracked via pointKind + provenance extractedFrom.
        p = {
            "id": ulid(),
            "content": content,
            "operator": operator,
            "provenance": prov,
            # #432: parity with the SDK default — points enter as draft and go
            # live when first operator edge is created (#131).
            "status": "draft",
            "createdAt": now_iso(),
        }
        # #5004: journal the vector + its identity. `EventAPI` is the SECOND
        # journal producer (ingest / mining / CLI write through it), and the
        # invariant `derived = replay(journal)` is producer-agnostic: without
        # this, an ingest-created Point's replay re-encoded under whatever
        # embedder was configured and silently produced a DIFFERENT graph from
        # the same journal. The vector is computed with the SAME store-declared
        # width the projection will use, and stamped through the SAME shared
        # seam the SDK writer uses (`stamp_journal_embedding`) so the two
        # producers cannot drift. `apply()` then RESTORES it verbatim, so the
        # live node and the journal agree by construction (one compute, not
        # two).
        if operator is None and isinstance(content, str) and content:
            vec = None
            try:
                from .embeddings import encode_for_store, stamp_journal_embedding
            except Exception:  # noqa: BLE001, RUF100
                # #5148: the seam MODULE is unimportable AT THIS MOMENT. Note
                # what this does NOT claim: `tortoise.embeddings` is imported
                # transitively at package-import time (sdk -> cross_lens), and
                # `numpy` is a core dependency, so a plain missing-dependency
                # install never reaches here. What it covers is an import hook
                # or an import environment that fails at the call site — and
                # the principle that a WRITE must not depend on an import
                # succeeding, which is the rule this block already follows for
                # an unavailable EMBEDDER. PRESENCE IS OWNERSHIP holds
                # regardless, and NOTHING is lost by skipping the stamp: with
                # no vector, `stamp_journal_embedding` only re-sets `embedding`
                # to the `None` written below, and its attestation block
                # requires a vector to fire.
                stamp_journal_embedding = None
            if stamp_journal_embedding is not None:
                try:
                    vec = encode_for_store(
                        content,
                        getattr(self.projection, "required_embedding_dim", None))
                except Exception:  # noqa: BLE001, RUF100
                    vec = None
            # PRESENCE IS OWNERSHIP (#5004 round-3): the key is set EVEN when
            # the encode failed or the embedder is unavailable, so the replay
            # is told the journal owns this field for this id and must not
            # invent a vector the live node does not have.
            p["embedding"] = vec
            if stamp_journal_embedding is not None:
                stamp_journal_embedding(p, creating=True)
        return p

    # -- ingest lifecycle / idempotency gate --------------------------------
    def begin_ingest(self, source_id, extractor_version, key: IngestKey, *,
                     force: bool = False) -> IngestResult:
        # Lazy-cache IngestStarted events: scan the log once, then O(1) lookups.
        if self._ingest_cache is None:
            self._ingest_cache = {}
            for e in self.log.read_all():
                if e["type"] == "IngestStarted":
                    k = e["key"]
                    self._ingest_cache[
                        (k["kind"], k["value"], e["extractor_version"])
                    ] = e["run_id"]

        cache_key = (key.kind, key.value, extractor_version)
        if cache_key in self._ingest_cache and not force:
            self.current_run = None
            return IngestResult(run_id=self._ingest_cache[cache_key], skip=True,
                                reason="already processed (same key + extractor_version)")

        # Reprocess (new version or --force): supersede every prior run for this
        # key, whatever its version, so the latest run wins.
        superseded = [
            rid for ck, rid in self._ingest_cache.items()
            if ck[:2] == (key.kind, key.value)
        ]
        run_id = ulid()
        if superseded:  # reprocess: retract the superseded runs' points first
            points = fold(self.log.read_all())
            for p in list(points.values()):
                if p["provenance"].get("run_id") in superseded:
                    self._emit("PointRetracted", id=p["id"])
        self._emit("IngestStarted", run_id=run_id, source_id=source_id,
                   extractor_version=extractor_version, key=key.as_dict(),
                   supersedes=superseded or None)
        # Update cache with the new run.
        self._ingest_cache[cache_key] = run_id
        self.current_run = run_id
        return IngestResult(run_id=run_id, skip=False)

    # -- mutations ----------------------------------------------------------
    def add_point(self, content, provenance, *, corrects=None, **fields) -> str:
        p = self._point(content, provenance)
        p.update(fields)
        # #5004 review: `_point` stamped a SERVER-computed vector. A
        # caller-supplied `embedding` in `fields` replaces that vector while the
        # identity/text-hash still describe the server one — a false
        # attestation that the replay would then trust. Re-normalise the FINAL
        # payload: a foreign vector is carried (so it is restored verbatim), but
        # with NO attestation, because its origin is not ours to vouch for.
        #
        # (The SDK surface refuses caller-supplied `embedding` outright — see
        # `TortoiseSDK._sanitize_props` — so this path is reachable only from a
        # direct `EventAPI` caller, which never gets an MCP/SDK guarantee.)
        if "embedding" in fields:
            # The key stays PRESENT in both branches: an explicit None is the
            # journal saying "this id has no vector", which the replay must
            # honour by leaving it unset rather than re-encoding. Set it FIRST,
            # so presence holds even when the seam below is unreachable.
            p["embedding"] = fields["embedding"]
            try:
                from .embeddings import stamp_journal_embedding
            except Exception:  # noqa: BLE001, RUF100
                # #5148 review: the same import-at-the-call-site lane as
                # `_point` — an unimportable seam must not fail a WRITE. Only
                # the stamp's normalisation safety net is lost; the key is
                # already present, so PRESENCE IS OWNERSHIP is intact.
                stamp_journal_embedding = None
            if stamp_journal_embedding is not None:
                stamp_journal_embedding(p, creating=False)
        # #5004 round-7: the journal MARKERS are server-minted, and this is the
        # one producer that does not pass through `_sanitize_props` (an
        # internal ingest/mining/CLI seam). Allowing them through let a caller
        # (a) claim a server vector was "caller-owned, stored verbatim" —
        # flipping the replay's storage form — and (b) set
        # `embedding_preserved`, which makes `stamp_journal_embedding` skip the
        # model/text-hash attestation that R1 exists to keep. Reject here for
        # the same reason the SDK and MCP boundaries do.
        _forged = sorted(set(fields) &
                         {"embedding_verbatim", "embedding_preserved",
                          # #5004 round-8: the IDENTITY keys too. They never
                          # become node properties (`_POINT_HANDLED`), so this
                          # is not a parity break — but the attestation itself
                          # is forgeable: a caller-written `embedding_model`
                          # rides the journal and makes the replay report a
                          # model change that never happened.
                          "embedding_model", "embedding_revision",
                          "embedding_text_hash"})
        if _forged:
            raise ValueError(
                f"{_forged} are server-managed embedding journal fields and "
                "cannot be set via add_point(fields=...)")
        # P1 #49: mark events with projection_version=2 so the projection gate
        # (Task 1.6) knows to strip context from v2 events.
        self._emit("PointAdded", point=p, corrects=corrects, projection_version=2)
        # Auto-compute grounding when a resolution event seeds the a-vector (#6704).
        # P1 #49: check pointKind, not context (context is deprecated).
        # ponytail: duck-typed; add a proper protocol if more backends gain grounding.
        pk = p.get("pointKind") or fields.get("pointKind")
        if pk == "resolution-event" and self.projection is not None:  # noqa: SIM102
            if hasattr(self.projection, "compute_grounding"):
                self.projection.compute_grounding()
        return p["id"]

    def add_operator(self, op_type, inputs, provenance, *,
                     content=None, corrects=None) -> str:
        # #331: explicit validation — a bare assert vanishes under python -O,
        # and non-string inputs crashed the label join below.
        if not isinstance(op_type, str) or op_type not in ("NAND", "IMPL"):
            raise ValueError(f"unknown gate {op_type!r}")
        if inputs is None:
            raise TypeError("add_operator: inputs must be a list of point ids")
        if isinstance(inputs, str):
            # #331 (review r2): a bare string is a single id, never char-split
            # (parity with merge_points).
            inputs = [inputs]
        _inputs = []
        for x in inputs:
            if isinstance(x, dict):
                if not isinstance(x.get("id"), str):
                    raise TypeError(
                        f"add_operator: input dict missing string 'id': {x!r}")
                _inputs.append(x["id"])
            elif hasattr(x, "id"):
                if not isinstance(x.id, str):
                    raise TypeError(
                        f"add_operator: input {x!r} has non-string .id "
                        f"{x.id!r}")
                _inputs.append(x.id)
            elif isinstance(x, str):
                _inputs.append(x)
            else:
                raise TypeError(
                    "add_operator: input must be a point id string, a dict "
                    f"with 'id', or an object with .id — got {type(x).__name__}: {x!r}")
        label = content or f"{op_type}({', '.join(_inputs)})"
        p = self._point(label, provenance,
                        operator={"op_type": op_type, "inputs": _inputs})
        self._emit("OperatorAdded", point=p, corrects=corrects, projection_version=2)

        # Gate 4: incrementally update SVBP on the new operator
        # ponytail: only FalkorProjection supports SVBP; InMemoryProjection doesn't.
        if self.projection is not None and hasattr(self.projection, 'get_svbp'):
            # Lazy-init SVBP if not yet created (handles operators added
            # before any get_confidence() call — Bug 5 fix).
            if self._svbp is None:
                svbp = self.projection.get_svbp(
                    n_particles=25, n_svgd_steps=15, svgd_lr=0.01,
                    damping=0.5, max_iter=40, tol=5e-3, seed=42,
                )
                if svbp is not None:
                    self._svbp = svbp
            if self._svbp is not None:
                # #1162: scope the warm-start to THIS operator — the old
                # global extract_svbp_factors() ran 2 batch graph queries
                # (O(graph)) on every operator write when the quadrature
                # extra (jax) is installed. The new operator's factor is
                # fully known here: same weight rule (3.0 NAND / 1.0 IMPL)
                # and >=2-input degenerate exclusion as extract_svbp_factors
                # (the EP local path's weights differ — compute_operator_weight
                # applies NAND_BASE_WEIGHT=8.0), so per-write cost drops to
                # O(1) with zero graph queries.
                if len(_inputs) >= 2:
                    weight = 3.0 if op_type == "NAND" else 1.0
                    factors = [(p["id"], op_type, list(_inputs), weight)]
                else:
                    factors = []
                if factors:
                    old_max = self._svbp.max_iter
                    self._svbp.max_iter = 5
                    try:
                        self._svbp.run(factors, warm_start=True)
                    finally:
                        self._svbp.max_iter = old_max  # Bug 4 fix

        return p["id"]

    # ── Gate 4: Confidence API ────────────────────────────────────

    def get_confidence(self, point_id: str) -> dict | None:
        """Compute confidence for a point via SVBP.

        Lazy-inits SVBP on first call. Returns {mean, variance, alpha, beta}
        or None if the projection doesn't support SVBP.
        """
        if self._svbp is None and self.projection is not None and hasattr(self.projection, 'get_svbp'):
            svbp = self.projection.get_svbp(
                n_particles=25, n_svgd_steps=15, svgd_lr=0.01,
                damping=0.5, max_iter=40, tol=5e-3, seed=42,
            )
            if svbp is not None:
                self._svbp = svbp
        if self._svbp is None:
            return None
        return self._svbp.compute_confidence(point_id)

    def revise_point(self, id, *, new_content=None, corrects) -> str:
        ev = self._emit("PointRevised", id=id, new_content=new_content,
                        corrects=corrects)
        return ev["event_id"]

    def retract_point(self, id, *, corrects) -> str:
        ev = self._emit("PointRetracted", id=id, corrects=corrects)
        return ev["event_id"]

    def merge_points(self, keep_id, merge_ids, *, corrects=None) -> str:
        # #331: None/empty merge input must not crash (list(None) raises
        # TypeError); a bare string is a single id, never char-split.
        if merge_ids is None:
            merge_ids = []
        elif isinstance(merge_ids, str):
            merge_ids = [merge_ids]
        ev = self._emit("PointsMerged", keep_id=keep_id, merge_ids=list(merge_ids),
                        corrects=corrects)
        return ev["event_id"]

    def add_subject(self, name: str, subject_kind: str = "other") -> str:
        """Emit SubjectAdded event. Returns the subject's id."""
        sid = ulid()
        self._emit("SubjectAdded", id=sid, name=name,
                   subject_kind=subject_kind,
                   createdAt=now_iso())
        return sid

    def add_object(self, name: str, object_kind: str = "other", *,
                   id: str | None = None, **props) -> str:
        """Emit ObjectRegistered event. Returns the object's id.

        ``id``: deterministic canonical id override (epic #264 plan §4.1 —
        obj_sha256 scheme); default ulid() unchanged (back-compat). Extra props
        (e.g. canonical_name, title) are persisted by the projection's
        extra-props handler. The projection MERGEs Object by name — a re-run
        with the same name never creates a duplicate node (idempotent
        reification, DE2E-1/DE2E-8).
        """
        oid = id or ulid()
        self._emit("ObjectRegistered", id=oid, name=name,
                   object_kind=object_kind, createdAt=now_iso(), **props)
        return oid

    def add_document(self, doc_id: str, title: str, *,
                     document_kind: str = "",
                     document_knowledge_domain: str = "",
                     about_entities: list[str] | None = None,
                     authored_by: str = "",
                     owned_by: str = "",
                     managed_by: str = "",
                     governing_agreement: str = "",
                     format: str = "markdown",
                     version: str = "",
                     content_hash: str | None = None,
                     createdAt: str | None = None,
                     updatedAt: str | None = None,
                     corrects: str | None = None,
                     topics: list[str] | None = None,
                     summary: str | None = None,
                     session_id: str | None = None,
                     event_id: str | None = None,
                     source_path: str | None = None,
                     needs_extraction: bool = False,
                     # epic #900 T3 (§4.1 route pin):
                     source_url: str | None = None,
                     domain: str | None = None,
                     suppress_embedding: bool = False) -> str:
        """Emit DocumentCreated event. Returns the document id (same as input).

        JSONL fields are snake_case per plan §4.3 convention.
        Projection normalizes to camelCase for the graph.
        #125: topics/summary/session_id/event_id capture metadata.
        #167: source_path → d.sourcePath for file resolution.
        #133: needs_extraction → d.needs_extraction for --upgrade-all discovery.
        D10 (ONTOLOGY v3.15 §4.4): ``doc_status`` is RETIRED — liveness is a
        read of the extracted entities, not a stored field. It is no longer a
        parameter and is never emitted.
        Epic #900 T3: ``source_url`` overrides the #205 auto-wire target (the
        indexer passes the real ``corpus://`` Source url so no phantom Source
        is merged — the override rides the JOURNALED event, so replay honors
        it); ``domain`` is persisted as ``d.domain`` via ``_persist_extra_props``
        (intentionally NOT in ``_DOC_RETIRED`` — the persistence IS the
        intent); ``suppress_embedding`` skips the unconditional embedding call
        (new-path docs; the legacy branch computes as today — SC4).
        #5422: ``content_hash`` is the document's **version anchor** on the
        extraction path — the SHA-256 of the ingested text (callers pass
        ``tortoise.ids.content_hash(text)``). It rides the JOURNALED event so
        the projection fold writes it replayably, and it is what gives a
        document-derived Point a version to anchor on (ONTOLOGY §4.6: the
        hash identifies a version; identity is ``url``). The JSONL field is
        snake_case (``content_hash``) like every sibling on this event — the
        projection normalizes it to ``contentHash`` on the node. Absent/None
        is the back-compat shape — the fold PRESERVES the stored hash rather
        than clearing it, so a metadata-only re-emit cannot wipe an anchor.
        """
        self._emit("DocumentCreated",
                   corrects=corrects,
                   id=doc_id,
                   title=title,
                   document_kind=document_kind,
                   document_knowledge_domain=document_knowledge_domain,
                   about_entities=about_entities or [],
                   authored_by=authored_by,
                   owned_by=owned_by,
                   managed_by=managed_by,
                   governing_agreement=governing_agreement,
                   format=format,
                   version=version,
                   content_hash=content_hash,
                   createdAt=createdAt or now_iso(),
                   updatedAt=updatedAt or now_iso(),
                   topics=topics or [],
                   summary=summary,
                   session_id=session_id,
                   event_id=event_id,
                   source_path=source_path,
                   needs_extraction=needs_extraction,
                   source_url=source_url,
                   domain=domain,
                   suppress_embedding=suppress_embedding)
        return doc_id

    def add_event(self, event_id: str, event_kind: str, *,
                  subject: str = "",
                  object_name: str = "",
                  object_type: str = "",
                  uses: list | None = None,
                  participants: list[str] | None = None,
                  started_at: str = "",
                  ended_at: str = "",
                  classification_level: str = "internal",
                  format: str = "jsonl",
                  **extra) -> str:
        """Emit an EventRecorded event (#125). Returns the event id.

        The event id is passed as ``id`` (not ``eventId``) to match the
        projection's ``_upsert_event`` lookup (``inner.get("id") or
        inner.get("eventId")``).
        """
        self._emit("EventRecorded",
                   id=event_id,
                   eventKind=event_kind,
                   subject=subject,
                   object=object_name,
                   objectType=object_type,
                   uses=uses or [],
                   participants=participants or [],
                   startedAt=started_at,
                   endedAt=ended_at,
                   classificationLevel=classification_level,
                   format=format,
                   **extra)
        return event_id

    # ── Cleanup ───────────────────────────────────────────────────

    def close(self):
        """Release SVBP particles and close projection."""
        if self._svbp is not None:
            self._svbp._particles.clear()
            self._svbp._summaries.clear()
            self._svbp = None
        if self.projection is not None and hasattr(self.projection, 'close'):
            self.projection.close()
