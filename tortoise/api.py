"""EventAPI — the shared append surface both producers write through.

Attribution (`initiated_by`, `agent_id`) is bound once at construction, so the
extractor gets initiated_by="extractor" and a user-agent gets "user" without
per-call bookkeeping (see README Interaction model).

Every mutation appends exactly one event to the log AND (if a projection is
attached) applies it incrementally, so the store never drifts from the log.
"""
from __future__ import annotations

import logging

from . import record_writer
from .idempotency import IngestKey, IngestResult
from .ids import now_iso, ulid

# #9572 review P2: "nothing was named" (absent OR blank) has ONE predicate,
# shared with the SDK doors and the hosted surfaces — see the leaf module.
from .kind_fallbacks import named_kind

# #7951 §17.1: the live-write scope the journal-first doors open around the fold.
from .projection import _live_write_scope, fold

# #5140: the `documents` METER KEY's defender for THIS producer. `quota` is a
# leaf module (stdlib-only at import time), so this adds no cycle.
from .quota import refuse_document_writer_demotion

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


def _refuse_invisible_point_kinds_in_payload(payload: dict) -> None:
    """#9287: this seam persists ``pointKind`` verbatim — refuse padding.

    ``EventAPI`` is the write seam every pipeline uses (the v1 extractor,
    ``ingest``, ``docs_miner``, ``mining``, the CLI), and ``add_point`` splats
    the caller's ``fields`` into the journal record, which
    ``projection._upsert_point`` folds with
    ``n.pointKind = coalesce($pk, n.pointKind)``. It already hosts the #5475
    kind gate for exactly this reason, and it had no invisible-kind arm —
    measured at #9591's head: ``api.add_point(..., pointKind='statement
    \\u2003')`` stored the padded kind, the gate answered ``[]``, and a
    ``require_calibration=True`` run stamped it.

    The input here is not always a human: ``extractor.py`` builds the kind from
    the model's JSON and passes it straight to ``add_point``, so a stray
    em-space/NBSP in model output mints the fail-open shape with no caller
    intent — a refusal that names the record is the only thing that surfaces it.

    ONE predicate with the SDK doors (``_kind_invisible_char``), never a
    re-spelling of the categories. The payload walk is ``pack_state``'s
    (``kind_spellings_in_payload``) — the same miner the approval gate reads —
    filtered to the canonical ``pointKind``: entity kinds are a different gate
    with its own blast radius, and this issue is the calibration gate's.

    A MODULE-LEVEL function, deliberately not a method: its one caller
    (``EventAPI._refuse_unapproved_kinds``) is invoked UNBOUND by duck-typed
    API doubles (``mining.py``'s ``ConversationMiner``, the #7951 ordering
    test), so nothing here may touch ``self`` — the first cut did, and raised
    ``AttributeError`` on a double whose ``projection`` has no ``.g``
    (#9591 review cycle 2, P1).
    """
    from .pack_state import kind_spellings_in_payload
    from .sdk import _refuse_invisible_kind
    for kind_prop, value in kind_spellings_in_payload(payload):
        if kind_prop == "pointKind":
            _refuse_invisible_kind("pointKind", value,
                                   door="EventAPI.add_point")


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
    def _refuse_unapproved_kinds(self, payload: dict) -> None:
        """#5475 (residual): a journal-first write may not install a pack.

        ``EventAPI`` is the write seam every pipeline uses (the v1 extractor,
        ``ingest``, ``docs_miner``, ``mining``, the CLI): it appends to the
        journal and folds through ``projection._upsert_*``, which persists the
        kind verbatim — so ``graph_kind_namespaces`` mines the value straight
        back into the graph's APPROVAL set. Measured at HEAD: a graph recorded
        as ``['dev']`` became ``['dev','marketing']`` from one
        ``add_object(..., 'marketing:campaign')``. The create-side gate does not
        see this seam (it never touches the SDK), so the closure property #5475
        states — *a write may not widen the approved set* — was still false one
        transport over.

        ⛔ THE BOUND, stated so this is not read as "the write path is closed":
        this gates a write that HAS a graph handle. ``rebuild_all`` folds a
        journal directly and stays ungated BY DESIGN — the journal is the
        RECONSTRUCTION SOURCE (never a durability authority; that lives only in
        ``docs/durability-posture.md``), so a record already in it must
        reproduce faithfully; refusing at replay would make a committed record
        unfoldable, which is strictly worse than the widening. Two consequences
        follow, both recorded rather than papered over: a record already in a
        legacy journal still widens the graph when rebuilt, and a
        ``projection``-less lane
        (``EventAPI(log, projection=None)`` — no PRODUCTION caller; the only two
        sites that construct one are the ``__main__`` demo subcommand and
        ``m0.py``'s fold demo) journals a kind for that replay to fold. Neither
        is a caller-controlled escalation from a live tenant; both are why
        #8982's replay half is pinned as a STATEMENT by
        ``TestReplayIsDeliberatelyUngated5475`` rather than as a gate.

        It runs BEFORE ``self.log.append`` in ``_emit``, and that ordering is
        load-bearing: the append is this seam's COMMIT POINT (the journal IS the
        reconstruction source), so a refusal raised after it would leave a
        record that is
        faithfully replayed on the next ``rebuild_all`` — refused live, widened
        anyway on replay. Fail closed means fail BEFORE the commit, not after.

        One approval-set read per CALL (not per kind property): the read is a
        whole-graph scan (``graph_installed_namespaces``' own COST note), and a
        capture that names several kinds must not pay it once per spelling. It
        is not paid at all for a payload the cheap ``kind_named_packs``
        pre-check clears — every ``core:`` kind, and a bare kind no catalog pack
        declares (``eventKind='AgentSession'`` is one of those: it clears, so the
        extractor's default capture pays NO read). ⚠️ A bare kind a pack DOES
        declare (``eventKind='ruleRevised'``, agent-ops) does not clear it — that
        leg may attribute the bare kind to the declaring pack, so it pays the
        read. See #8977.

        A graph handle that is bound but UNREADABLE raises (an outage must never
        read as "no packs" and silently widen the gate) — so this adds a graph
        READ to writes that previously did none. That is fail-closed and the
        fold would fail on that outage anyway, but it is a new dependency, not
        the note above's "no read".

        A ``projection``-less lane (``EventAPI(log, projection=None)``) has NO
        graph in scope: this gate abstains there rather than reading an approval
        set from a graph it does not hold (see THE BOUND above — the record is
        gated by nothing on that lane, and the replay that folds it is
        deliberately ungated too).
        """
        # ⛔ #9287: the INVISIBLE-KIND refusal is NOT conditional on a graph
        # handle — it is a pure string predicate, and a ``projection``-less lane
        # folds the record through the same ``_upsert_point`` later (or replays
        # it), so it must be refused here too. Only the APPROVAL-set gate
        # abstains (it needs a read). It runs BEFORE the ``log.append`` commit
        # point in ``_emit``, like the approval gate below, so a refused payload
        # leaves nothing to replay.
        #
        # Called as a BARE MODULE FUNCTION, never ``self.…``: this method is
        # exercised UNBOUND by duck-typed API doubles (``mining.py``'s
        # ``EventAPI._refuse_unapproved_kinds(api, record)``, the #7951 ordering
        # test's bare ``_API``), and ``self`` there is not an ``EventAPI`` at all
        # — a ``self.``-routed helper raised ``AttributeError`` on that double
        # (#9591 review cycle 2, P1, measured: test_7951_write_doors.py red).
        _refuse_invisible_point_kinds_in_payload(payload)
        g = getattr(getattr(self, "projection", None), "g", None)
        if g is None:
            return
        from .pack_state import (
            graph_approval_namespaces,
            payload_names_pack,
            refuse_unapproved_kinds_in_payload,
        )
        if not (spells := payload_names_pack(payload)):
            return
        refuse_unapproved_kinds_in_payload(
            payload, graph_approval_namespaces(g), spells=spells)

    def _emit(self, type_: str, *, corrects=None, actor: str | None = None,
              **payload) -> dict:
        # #7951 — THE JOURNAL ORDERING CONTRACT: **journal first, then
        # project.** The full contract (and the failure-mode argument) lives at
        # the top of `tortoise/sdk.py`; read it before adding a writer. It is
        # `docs/architecture/STORAGE-ARCHITECTURE.md` §3's write protocol —
        # *"Writes go to the truth first, then project"* — and the live doors
        # that state it explicitly are this method and
        # `TortoiseSDK._apply_and_journal`. The append below is the COMMIT
        # POINT: it must precede the fold, and it is deliberately not
        # best-effort (a failed append must not let the fold run).
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
        # #5475 (residual): the approved-pack gate, BEFORE the commit point.
        # Every EventAPI mutation funnels through here, so gating here is what
        # closes the seam for the doors it has today AND for a door added
        # tomorrow — the argument the SDK makes for its own funnel, one
        # transport over. See `_refuse_unapproved_kinds` for why it must run
        # before the append rather than after.
        self._refuse_unapproved_kinds(event)
        # #9190: the append REPORTS the record's journal position, and that
        # number is the record's `journal_event.seq`. Dropping it here is what
        # kept layer 1 empty on live writes (the row was deferred with a
        # `position=False` that attributed the gap to #9190, which has landed).
        seq = self.log.append(event)
        # #9188: the Postgres record, AFTER the append (the commit point, §17.1)
        # and before the fold. This door is a LIVE journal writer — the v1
        # extractor, `ingest`, `docs_miner`, `mining` and the CLI all append
        # through it — so without this call every one of their records would
        # reach the journal and never the record. Best-effort by construction
        # (`write_live_record` counts and logs, never raises): the append above
        # already committed.
        #
        # ⛔ but only when the log IS a journal. `durable` is the log's own
        # answer, defaulting True for every duck-typed double and for
        # `EventLog`; `TortoiseSDK._extract_session_llm` hands this door an
        # `_InMemoryEventLog` (no file I/O, by design) whose records no journal
        # ever carries, so a row from it could never be reproduced or healed by
        # a replay — the record is `replay(journal)`, and a scratch buffer is
        # not a journal. The seam DECLINES it (no row) and counts the decline on
        # `monitoring.metrics()["record_non_journal_skips"]`, NOT on
        # `record_write_failures`: a lane with no journal by design lost nothing a
        # replay could restore (#9231 review P2).
        record_writer.write_live_record(
            event, durable=getattr(self.log, "durable", True), seq=seq)
        if self.projection is not None:
            # #7951 §17.1: journal-first door — the append above is the COMMIT
            # POINT, so the fold runs inside the live-write scope.
            with _live_write_scope("event_api._emit"):
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
            # #432/#1088: EventAPI DELIBERATELY writes status="draft" — the
            # legacy journal-composer posture, NOT SDK parity (SDK default is
            # live). An explicit draft is never auto-promoted; it stays draft
            # until `promote_point` / `update_point(status="live")` — the #131
            # clause now fills only a never-set status.
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
                from .embeddings import encode_for_row, stamp_journal_embedding
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
                    vec = encode_for_row(
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
                          "embedding_text_hash",
                          # #5256: the `extractedFrom` read-version anchor is
                          # server-derived (resolved from the :Source below).
                          # Accepting it would let this non-SDK seam forge the
                          # provenance record, and the same reason the SDK and
                          # MCP boundaries reject it applies here.
                          # #7704: `extractedFromAll` joins them — it is the
                          # FOLD's record of the ref set, and
                          # `_upsert_point_edges`/`_point_all_source_refs`
                          # re-derive the `extractedFrom` edges from it on
                          # replay, so a caller-supplied value puts the
                          # payload's edges AHEAD of the :Source resolution
                          # this seam performs (live wires the resolved refs,
                          # the replay wires the caller's array too). This
                          # seam is the one producer that never reaches
                          # `_sanitize_props`, so the entry is the ONLY guard
                          # on the key here — the sibling keys are registered
                          # at every home for the same reason.
                          "sourceVersion", "sourceVersions",
                          "sourceVersionTransit", "extractedFromAll",
                          # #10010: the FOLD's record FLAG — `_fold_source_evidence`
                          # mints it on the snapshot it re-emits, and the replay's
                          # recreate anchor reads it to tell a fold from a
                          # creation. Server-minted like the keys above, so a
                          # caller value is refused at this seam too.
                          "evidence_fold"})
        if _forged:
            raise ValueError(
                f"{_forged} are server-managed journal fields (embedding "
                "identity / provenance) and cannot be set via "
                "add_point(fields=...)")
        # #5256: resolve the extractedFrom read-version from the :Source on the
        # LIVE path and put it in the payload, so the Point's OWN journaled
        # snapshot carries it and the replay never re-reads the Source (whose
        # in-place contentHash bump is unjournalled, #5024). `getattr` — not
        # `self.projection.g` — is load-bearing: this producer runs in
        # projection-less extraction lanes (`projection=None`) and against the
        # in-memory double (no `.g`), and neither has a Source to read. There
        # the field simply stays absent; the point itself still journals.
        if p.get("extractedFrom"):
            _g = getattr(self.projection, "g", None)
            if _g is not None:
                from .projection.edges import _source_version_transit, resolve_source_versions
                _sv = _source_version_transit(
                    resolve_source_versions(_g, p["extractedFrom"]))
                if _sv is not None:
                    p["sourceVersionTransit"] = _sv
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

    def _registration_id(self, label: str, name: str,
                         explicit_id: str | None = None) -> str:
        """The id a REGISTRATION emitter writes: the live holder's, or a MINTED one.

        #3590 (D1/R1/R2, and the review's finding 1): ``ObjectRegistered`` /
        ``SubjectAdded`` ARE the explicit registration doors — D1's own text
        (``identity-decision.md``) says the entity set grows only from them —
        but a registration still has to be IDEMPOTENT, the way every other
        create door is (``#452``: a re-mention of a live name resolves to the
        live holder).

        ⛔ What changed and why this is not optional: the fold's MERGE key is now
        ``{id}`` (``projection.entities._upsert_subject`` / ``_upsert_object``),
        so the *by-construction* duplicate suppression the old name-keyed MERGE
        provided is GONE — Cypher's ``MERGE`` creates whenever the exact pattern
        is absent (primary source: `neo4j.com/docs/cypher-manual/current/
        clauses/merge/`, "if the exact pattern is not found, MERGE acts like
        CREATE"), and this repo declares range indexes, not a uniqueness
        constraint on ``Object.id``/``Subject.id``. So the anti-duplicate
        property has to be carried by the CALLER, and a door that minted a
        fresh ULID per call would leave a routine re-run of the v1 extractor
        with TWO live carriers of one name — after which every by-name read
        refuses (``AmbiguousEntityName``), ``create_entity`` raises, and
        ``update_entity(name=…)`` raises ``NameAlreadyHeld``, for a name the
        producer registered legitimately.

        ``explicit_id`` wins outright (``mining``/``docs_miner`` pass their own
        canonical id): an explicit-id channel supplies the identity, which is
        exactly what an opaque-id model allows and what ``add_object(id=…)``
        promises. It is never a name derivation.

        ``AmbiguousEntityName`` is propagated, NOT swallowed into a third
        carrier: same-name coexistence is legal (D2) but a name-keyed door may
        not pick a carrier for the caller — route-then-refuse (R8), the same
        disposition ``create_entity`` takes.

        ⛔ The caller MUST hold :func:`~tortoise.entity_identity.entity_create_lock`
        across this resolve AND the ``_emit`` that makes the holder visible —
        otherwise the empty-holder check is a TOCTOU window rather than a guard
        (the same finding round 5 fixed for ``create_entity``).
        """
        if explicit_id:
            return str(explicit_id)
        # `getattr` — not `self.projection.g` — because projection-less lanes
        # (the `__main__` demo, `m0.py`) and in-memory doubles have no graph to
        # resolve against; they journal only, so there is no second carrier to
        # avoid and the plain mint is right.
        g = getattr(self.projection, "g", None)
        if g is None:
            return ulid()
        from .entity_identity import resolve_or_mint
        return resolve_or_mint(g, label, name)[0]

    def add_subject(self, name: str, subject_kind: str | None = None) -> str:
        """Emit SubjectAdded event. Returns the subject's id.

        ``subject_kind`` defaults to ``None`` (**absent**, not ``"other"**) since
        #9572: the fabricated default reached the fold's ON MATCH
        ``coalesce($sk, s.subjectKind)`` as a real value, so a bare re-mention
        reset a user-authored kind. The ``'other'`` default is the fold's ON
        CREATE arm.

        The id is resolved-or-minted (``_registration_id``), never a fresh ULID
        per call: the fold MERGEs Subject by ``id``, so re-minting would leave
        the name with two LIVE holders and poison it for every later by-name
        read (#3590 review, finding 1).
        """
        from .entity_identity import entity_create_lock
        with entity_create_lock("Subject", name):
            sid = self._registration_id("Subject", name)
            ev: dict = {"id": sid, "name": name, "createdAt": now_iso()}
            subject_kind = named_kind(subject_kind)
            if subject_kind is not None:
                ev["subject_kind"] = subject_kind
            self._emit("SubjectAdded", **ev)
        return sid

    def add_object(self, name: str, object_kind: str | None = None, *,
                   id: str | None = None, **props) -> str:
        """Emit ObjectRegistered event. Returns the object's id.

        ``id``: explicit canonical id override (epic #264 plan §4.1 —
        obj_sha256 scheme), which wins outright. WITHOUT it the id is
        resolved-or-minted through ``entity_identity.resolve_or_mint`` — the
        live holder's id for an existing single live carrier, a fresh ULID
        otherwise (``_registration_id``).

        ⛔ The projection MERGEs Object by ``id`` since #3590, so this door's
        idempotency is carried HERE, by the caller, and no longer by the MERGE
        key: a re-run with the same name resolves to the same node instead of
        minting a duplicate (DE2E-1/DE2E-8), and a second live carrier of one
        name is what the resolve REFUSES rather than creates. The previous
        text claimed the MERGE did it — false at this head, which is exactly the
        poisoning this door now prevents (#3590 review, finding 1).

        Extra props (e.g. canonical_name, title) are persisted by the
        projection's extra-props handler.

        ``object_kind`` defaults to ``None`` (**absent**) since #9572 — the
        Object twin of ``add_subject``'s fabricated default.
        """
        from .entity_identity import entity_create_lock
        with entity_create_lock("Object", name):
            oid = self._registration_id("Object", name, explicit_id=id)
            ev: dict = {"id": oid, "name": name, "createdAt": now_iso(), **props}
            object_kind = named_kind(object_kind)
            if object_kind is not None:
                ev["object_kind"] = object_kind
            self._emit("ObjectRegistered", **ev)
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
        (intentionally NOT in ``_DOC_PASSTHROUGH_HANDLED`` — the persistence IS the
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
        # #5140 P1 (review cycle 3): the doc id IS the caller's `:Source` MERGE
        # key, so this producer could rewrite an existing document's genre to
        # `transcript` and drop it out of the metered `documents` set (MEASURED,
        # 3 seeded documents: meter 3 -> 2 -> 1, one call each). The fold's CASE
        # clause drops that value to keep REPLAY total (a rebuild must not abort
        # on a pre-guard record), but this is a PRODUCER, not replay: a silent
        # no-op would return the doc id as if the genre had been written. Runs
        # BEFORE `_emit` (the commit point), so a refusal leaves no record.
        # `getattr` — not `self.projection.g` — because projection-less lanes
        # (the `__main__` demo, `m0.py`) and in-memory doubles have no graph to
        # demote; there the record still journals (`_refuse_unapproved_kinds`
        # states the same bound).
        _g = getattr(self.projection, "g", None)
        if _g is not None:
            refuse_document_writer_demotion(
                _g, doc_id, document_kind, surface="add_document")
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
