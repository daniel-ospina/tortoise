# Archived sdk-method `get_point` — REMOVED from the product surface (#11609).
#
# batch:   12901-final
# reason:  #12901 FINAL batch: get_point left the public SDK surface — privatised (body survives as `_get_point`), no single owner-approved replacement. Frozen here so a mistake is inspected against the code that shipped it (#11609 ruling).
# source:  tortoise/sdk.py @ 424368c05d8ee9b26aa2d77e4c5a93a4b207b58f
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'sdk-method'
ARCHIVED_SYMBOL = 'get_point'
ARCHIVED_BATCH = '12901-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '424368c05d8ee9b26aa2d77e4c5a93a4b207b58f'

# ── verbatim from tortoise/sdk.py ──

    def get_point(self, id: str, *, served_out: dict | None = None) -> dict:
        """Get a Point by ID. Returns dict of all properties, or ``{}`` if absent.

        Two absent SHAPES, which are not two verdicts: ``{}`` — returned BOTH
        when the absence IS ESTABLISHED (the record was asked and holds no such
        row) and when an ``off`` deployment with the marker unarmed can
        establish nothing at all (the pre-#12941 shape, which says nothing
        about whether the node exists) — and ``{residency: "not-hot"}`` when
        nobody who could see the node was consulted and the marker is armed
        (#7952 — a cache miss is never reported as an empty answer).
        ⛔ ONLY THE FIRST IS FALSY, and that is the whole of what ``if props:``
        can tell a caller: the marker shape is a TRUTHY dict carrying no node
        properties, and BOTH marker arms return it — the `off` +
        ``TORTOISE_RESIDENCY`` partial-cache arm below, and the armed
        ``not_hot`` arm. So `if props:` separates an ESTABLISHED absence from a
        found node and nothing more; a write-verification must ALSO read
        ``props.get("id")`` (a found node always carries one) or refuse the
        ``residency`` marker, or it reports a node this store could not see as a
        successful write. ⚠️ This paragraph used to say the opposite — that
        ``if props:`` was a correct write-verification — and that sentence, not
        the behaviour, was the defect (#12947 review, P2).

        #432: retracted points (status='retracted') ARE returned by get_point
        — they are tombstoned, not deleted. Query surfaces exclude them by
        default (opt-in via include_retracted).

        #12941 — THE READ-MY-WRITES SEAM, opt-in through ``served_out``.
        ``TORTOISE_RECORD_SERVE`` selects the store exactly as it does on the
        hosted route (``tortoise/record_serve.py``, one parser, one registry):
        ``off`` answers from the cache as before, ``record`` answers from the
        record or RAISES, and ``prefer`` asks the record first with a NAMED,
        COUNTED fallback. A write lands in the record, so with the cache cold
        an ``off`` deployment answers ``{}`` for a node that EXISTS — which is
        the defect this seam removes.

        ⛔ WHY OPT-IN AND NOT UNCONDITIONAL. This method is the SDK's INTERNAL
        accessor: write paths call it MID-WRITE to build journal events and to
        check dedupe/approve/promote state (``_emit_event("PointAdded",
        point=self.get_point(mid))`` and friends — see the #7983 note below), so
        a record answer would make a writer read a store the same write has not
        finished projecting, and an armed deployment would change what a WRITE
        journals (#12941's own exclusion, ``record_serve``'s
        ``internal-accessor``). ``served_out=None`` — the default, and what
        every write path passes — is byte-identical to the pre-#12941 call: one
        cache query, no record consult, no added key.

        ``served_out``  a dict the CALLER owns; on return it carries the
                        attribution (``servedFrom``, ``fallbackReason``, and
                        ``residency: "not-hot"`` when nobody who could see the
                        node was consulted). ``off`` fills only ``residency`` —
                        there is no fallback to explain, and an unarmed read
                        keeps the pre-#12941 response shape. The field is the
                        same caller-owned-sink instrument ``tortoise_fts_query``
                        already uses (``read_status_out`` / ``leg_trace``,
                        #3862/#8447), so
                        this adds NO public SDK method and changes no existing
                        caller's behaviour.

        ⛔ A cache-only empty answer is never returned as a bare ``{}`` when the
        record could not be asked: the return carries ``residency: "not-hot"``
        (#7952 — "a cache miss must say not-hot, never return empty"), so a
        verifier can tell *"we cannot see it from here"* from *"it does not
        exist"*. ``TORTOISE_RESIDENCY`` gates that marker in ``off`` mode, the
        same as everywhere else.
        """
        # ⛔ THE PROJECTION IS LAZY (#12941 review P1-1). Opening it is a COLD
        # OPEN (connect, version probe, `_ensure_indexes()`, and on a restored
        # graph an index BUILD budgeted by `probe_setup_timeout()`), and in
        # `record` mode this read must answer with the graph engine STOPPED —
        # that is the property the mode exists for (`record_serve`'s header) and
        # the one `hosted_api.get_point` already honours by constructing no
        # handle at all. So the handle is built on FIRST USE: never in `record`
        # mode, and on the MCP lane (which builds a fresh SDK per call,
        # `mcp_auth._get_org_sdk`) that is the difference between paying a cold
        # open for every armed read and paying it only when the cache is really
        # needed.
        _proj: list = []

        def _cache_handle():
            if not _proj:
                _proj.append(self._get_proj())
            return _proj[0]

        def _cache_answer() -> dict | None:
            """The cache's answer: ``None`` for a miss (the seam's vocabulary)."""
            rows = _cache_handle().g.query(
                "MATCH (n:Point {id:$id}) RETURN properties(n)",
                params={"id": id},
            ).result_set
            return dict(rows[0][0]) if rows else None

        if served_out is None:
            props = _cache_answer()
            # #7983: NOT recorded, deliberately. `get_point` is the SDK's INTERNAL
            # accessor: write paths call it to build events (`_emit_event("PointAdded",
            # point=self.get_point(mid))`) and to check dedupe/promote/approval state,
            # so recording it makes a node written and never read look READ — biasing
            # `f` HIGH, i.e. AGAINST the split, and destroying the one-directional
            # caveat §17.4.6 relies on. A caller-facing read-by-id path is a coverage
            # hole (#8877); the right fix is a seam that separates the two, not a hook
            # that counts every internal check as a caller read.
            return props or {}

        # ── the CALLER-FACING read (#12941) — opt-in, so an unarmed caller and
        # every mid-write caller above keep the pre-#12941 shape exactly.
        from .record_serve import (
            FALLBACK_RECORD_BOUND_MISS,
            FALLBACK_RECORD_TENANT_UNRESOLVED,
            MODE_OFF,
            MODE_PREFER,
            MODE_RECORD,
            READ_POINT_BY_ID,
            CacheAnswerRequired,
            RecordReadBoundMiss,
            RecordServeError,
            RecordUnavailable,
            answered_by_cache,
            point_residency,
            serve_mode,
            serve_point_bounded,
        )
        from .residency import RESIDENCY_FIELD, RESIDENCY_NOT_HOT

        mode = serve_mode()
        coordinate = f"Point:{id}"
        if mode == MODE_OFF:
            # OFF consults no record, so an empty answer can only be marked, never
            # repaired: the marker says "this store cannot see it", which is all a
            # partial cache (or an armed deployment) can honestly say.
            props = _cache_answer() or {}
            marker = point_residency(_cache_handle().g, found=bool(props), key=id)
            # ⛔ OFF emits NO attribution — that is the pre-#12941 shape, and the
            # `servedFrom`/`fallbackReason` pair is the ARMED read's statement
            # about a fallback, of which `off` performs none (`hosted_api.get_point`
            # states the same rule for the same reason). The marker is the one
            # thing that CAN be said here, and it is carried on the answer itself
            # as well as in the sink, so a caller reading only props still sees it.
            if marker:
                served_out[RESIDENCY_FIELD] = marker
                return {**props, RESIDENCY_FIELD: marker}
            return props

        # The tenant is the SDK's OWN carried pair first and the ambient
        # scope/env second — the same resolution the record WRITE seam uses
        # (`record_tenant_effective`), so the read cannot be scoped to a tenant
        # the write was not filed under.
        from .record_writer import record_tenant_effective

        tenant = record_tenant_effective(getattr(self, "_record_tenant", None))
        if tenant is None:
            if mode == MODE_RECORD:
                # Structural, and it has its own class: retrying never resolves a
                # tenant, so this must not read as the retryable outage that
                # earns a 503 on the hosted door.
                raise RecordServeError(
                    "this SDK carries no record tenant, so the read cannot be "
                    "served from the record — absence is NOT established")
            answer = answered_by_cache(
                read=READ_POINT_BY_ID, coordinate=coordinate,
                props=_cache_answer(),
                reason=FALLBACK_RECORD_TENANT_UNRESOLVED)
        else:
            # ⛔ ONE BOUND FOR ALL THREE DOORS (#12947 finding 1, P1). The hosted
            # route already submits this same call to
            # `_read_offload(..., timeout=record_read_timeout_s())`; the SDK and
            # the MCP door called it on the caller's thread and nothing bounded
            # it, so a slow record produced the transport's `-32009` ("the work
            # may still complete") instead of this seam's NAMED fallback, and
            # `FALLBACK_RECORD_BOUND_MISS` was unreachable from those doors.
            # `record_read_timeout_s()` is resolved HERE rather than imported at
            # module scope so the bound a lane refuses on is the one a test can
            # shrink to prove the refusal (`monitoring.py` pins it below the
            # transport's wait bound).
            from .monitoring import record_read_timeout_s

            bound_s = record_read_timeout_s()

            def _cache_answered(reason: str):
                """The caller's OWN cache read — the deferred shape's fallback.

                It runs HERE, on the caller's thread, so the bounded worker
                touches the RECORD and never a graph handle: the same split
                `hosted_api.get_point` makes, and the reason `cache_answer=None`
                is passed below for `prefer`.
                """
                return answered_by_cache(
                    read=READ_POINT_BY_ID, coordinate=coordinate,
                    props=_cache_answer(), reason=reason)

            try:
                answer = serve_point_bounded(
                    bound_s=bound_s,
                    tenant=tenant, point_id=id,
                    # ⛔ `prefer` takes the DEFERRED shape: the seam still
                    # decides and NAMES the fallback, but the cache read then
                    # happens on this lane instead of inside the worker.
                    # `record` never calls the callback and must pass one at all
                    # (`serve_point` refuses `None` outside `prefer`).
                    cache_answer=(None if mode == MODE_PREFER else _cache_answer),
                    mode=mode)
            except CacheAnswerRequired as needed:
                # `prefer` only: the record missed or could not be asked, so the
                # reason is the SEAM's and the answer is the cache's.
                answer = _cache_answered(needed.reason)
            except RecordReadBoundMiss as bound_miss:
                if mode == MODE_RECORD:
                    # No cache branch exists, so "too slow" must RAISE: turning a
                    # capacity condition into an absence is the §17.2 inversion
                    # this whole module exists to prevent. Chained from the seam's
                    # own miss so the cause survives the translation.
                    raise RecordUnavailable(
                        f"the record did not answer {coordinate} inside "
                        f"{bound_s:.1f}s — in 'record' mode there is no cache "
                        f"branch, so an absence is NOT established") from bound_miss
                answer = _cache_answered(FALLBACK_RECORD_BOUND_MISS)
        served_out.update(answer.as_wire())
        # ⛔ THE SEAM DECIDES HERE, NOT THE DECLARATION. In an armed mode the
        # record was (or could have been) asked, so `answer.not_hot` already
        # encodes the ONE distinction that matters (§17.2) and the cache's
        # declaration must NOT be consulted on top of it: a record-mode miss on a
        # cache that declares itself PARTIAL is still a genuine ABSENCE — the
        # truth store was asked and held no such row — and marking it `not-hot`
        # would invent an impairment out of a correctness guarantee.
        # `point_residency` is for the OFF arm above, where no store that could
        # see the node was consulted at all.
        if answer.not_hot:
            served_out[RESIDENCY_FIELD] = RESIDENCY_NOT_HOT
            return {**(answer.props or {}), RESIDENCY_FIELD: RESIDENCY_NOT_HOT}
        return dict(answer.props) if answer.props else {}
