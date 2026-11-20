# Archived sdk-method `annotate_ask_hits` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_annotate_ask_hits`; recorded destination `search_knowledge` (#12901 final batch, #4282 Phase 2).
# source:  tortoise/sdk.py @ 1231060fd2be9205b9b3bba1fd315e9b19b3602e
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'sdk-method'
ARCHIVED_SYMBOL = 'annotate_ask_hits'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def annotate_ask_hits(self, hits: list[dict]) -> list[dict]:
        """Ask-path-local hit annotation: close the ``SearchResult.to_dict()``
        gap (sessionId present, ``session_date``/``speaker`` absent) so
        ``render_context``'s date markers + speaker decoration and the
        temporal/KU fragments function on the ask path (#1987 Task 4).

        One BATCH Cypher over the returned hits' ids (never N+1): a join to
        the point's OWN ``:Event`` node (``eventId`` → ``startedAt``), a join
        to the session that CONTAINS the point for the session's RECORDED
        time, and a join to the source turn ``:Point`` (``source_turn_id``)
        for ``speaker``. Produces ADDITIVE keys only — undated hits render
        byte-identical:

          * ``session_date`` (#4106) — the point's own capture Event
            (``startedAt[:10]``) when the ``eventId`` join yields one, ELSE
            the CONTAINS session's recorded ``created_at[:10]``. When several
            sessions CONTAIN the point the EARLIEST recorded time is taken —
            a deterministic pick that never depends on the engine's
            unspecified row order, and one of the point's own recorded
            session times rather than an inference. A value that is not a
            well-formed ``YYYY-MM-DD`` prefix is treated as ABSENT. NO other
            source is consulted: a date that is not recorded renders as NO
            date, never as a default (a wrong date is worse than none).
            Episodic turn Points carry no ``eventId`` — the capture turn
            store stamps provenance on EXTRACTED points only (see the retry
            branch below) — so before #4106 the ``eventId`` join was the sole
            source and every captured turn rendered undated, leaving the
            reader's temporal/KU fragments to compute elapsed time from
            nothing. The ``CONTAINS`` edge is the SAME provenance mechanism
            the point fetch already resolves session IDENTITY from.
          * ``speaker`` — the hit's own ``speaker`` prop, else the source
            turn's ``speaker``, else "" (the ``_render_block`` role-bracket
            guard suppresses double-attribution);
          * ``session_id`` — the Event's ``sessionId`` when the ``eventId``
            join yields one (hits lacking ``sessionId`` but sharing an Event
            join group together for the per-session dedup — P2-20), else the
            hit's own value unchanged. ⛔ The ``created_at`` leg added by
            #4106 attaches NO ``session_id`` — it is read for the DATE only.

        ⛔ POOL SAFETY (D3 #1540, #4106): this method must NOT introduce a NEW
        session source. Every ``session_id`` it attaches becomes the ask
        lane's ``dedup_pool`` bucket key, so widening the identity join (e.g.
        taking the ``:Session`` ``CONTAINS`` edge id as a ``session_id`` — the
        eval ingest writes those with INTERNAL ``lme:{qid}:s{si}`` ids)
        re-buckets the pool and changes which hits fit the resolved ask-lane
        reader window (200/400/200/16000/128000 since #4105/#4235; 8k/32KiB
        before it).
        #4106 adds ONE source, the session's own recorded
        ``created_at``, and it is read for ``session_date`` ONLY — the
        attached ``session_id`` set is byte-identical with and without it
        (pinned by ``tests/test_ask_sdk.py``).

        The ``session_date`` field is ITSELF a bucket-key fallback (the ask
        lane's ``session_id or session_date or idx:`` chain and
        ``retrieval._pkg_session``), so its effect on bucketing is measured,
        not assumed (``tests/test_ask_sdk.py`` seeds ``session-transcript``
        raw chunks across two sessions): the key such a hit used to get was
        ``idx:-1`` — deliberately, because the point fetch emits neither the
        snake ``session_id`` nor ``lme_session_index``, so EVERY such hit
        shared one global bucket. Populating the date can therefore never
        drop a hit the ``idx:-1`` collapse kept, and on sessions with
        distinct dates it restores hits that collapse was discarding. D3's
        session identity is derived downstream by ``retrieval.hit_session_id``
        from the hit's own ``sessionId`` (populated by the point fetch from
        the Point prop / ``:Session`` edge) / ``session_id``. That derivation
        does NOT re-bucket the pool — but it does widen rendered blocks, so it
        changes byte-cap admission under the resolved ceiling (see the
        ``retrieved_session_ids`` row in ``docs/product/answer-surface.md``),
        unlike widening THIS join, which is what re-buckets the pool.

        D8 supersession/validity keys are ALREADY attached to point hits by
        ``tortoise_fts_query`` via ``fetch_point_epistemic_state`` — this
        method MUST NOT re-fetch them; they ride through untouched (no drift
        risk, no duplicate join). ``has_answer`` and every other passthrough
        key survive unchanged. The annotation covers the full returned set
        (the ask lane retrieves with ``limit=resolve_ask_retrieval_caps()
        ["limit"]``, ``DEFAULT_ASK_RETRIEVAL_LIMIT=200`` since #4105 — NOT
        ``DEFAULT_CONTEXT_ITEM_CAP``, which is still 40).
        """
        if not hits:
            return hits
        proj = self._get_proj()
        ids = [str(h.get("id")) for h in hits if h.get("id")]
        if not ids:
            return hits
        try:
            # #4106: the ``CONTAINS`` session leg + ``collect(DISTINCT
            # s.created_at)`` collapse a point contained by several sessions
            # into ONE row per id (a scalar ``s.created_at`` return would
            # multiply rows, and the engine's row order is unspecified).
            rows = proj.g.query(
                "MATCH (n:Point) WHERE n.id IN $ids "
                "OPTIONAL MATCH (ev:Event) WHERE ev.eventId = n.eventId "
                "OPTIONAL MATCH (n)<-[:CONTAINS]-(s:Session) "
                "OPTIONAL MATCH (t:Point) WHERE t.id = n.source_turn_id "
                "RETURN n.id, ev.startedAt, n.speaker, t.speaker, "
                "ev.sessionId, n.sessionId, collect(DISTINCT s.created_at)",
                params={"ids": ids},
            ).result_set
        except Exception:
            # A whole-batch annotation raise maps to 502 retrieval_unavailable
            # upstream (the ask lane); the hits are returned untouched so the
            # caller's error map is the single authority.
            raise
        joined: dict[str, dict] = {}
        for row in rows:
            pid = row[0]
            ev_started = (row[1] or "") if len(row) > 1 else ""
            own_speaker = (row[2] or "") if len(row) > 2 else ""
            turn_speaker = (row[3] or "") if len(row) > 3 else ""
            ev_session = (row[4] or "") if len(row) > 4 else ""
            n_session = (row[5] or "") if len(row) > 5 else ""
            # #4106: session's RECORDED time, earliest first (deterministic —
            # never plan order). Empty list = no recorded date ⇒ unknown.
            session_times = [
                str(v) for v in ((row[6] if len(row) > 6 else None) or [])
                if v]
            # #4106: only a well-formed YYYY-MM-DD prefix is a date. A
            # malformed/sentinel recorded time renders as UNKNOWN — never as
            # a truncated garbage "date". Earliest wins on a multi-session
            # point (deterministic, never plan order).
            ev_date = _iso_date10(ev_started)
            recorded = sorted({d for d in map(_iso_date10, session_times) if d})
            if ev_date:
                sdate = ev_date
            elif recorded:
                sdate = recorded[0]
            else:
                sdate = ""
            joined[pid] = {
                "session_date": sdate,
                "speaker": own_speaker or turn_speaker or "",
                # UNCHANGED by #4106 — the date leg attaches no session_id.
                "session_id": ev_session or n_session,
            }
        out = []
        for h in hits:
            ann = joined.get(str(h.get("id")))
            if ann is None:
                # null-join hit (no Event node / no source turn): additive
                # keys stay absent — byte-identical rendering, no crash.
                out.append(h)
                continue
            enriched = dict(h)
            # additive keys only — never overwrite existing values
            enriched.setdefault("session_date", ann["session_date"])
            enriched.setdefault("speaker", ann["speaker"])
            if ann["session_id"]:
                enriched.setdefault("session_id", ann["session_id"])
            out.append(enriched)
        return out
