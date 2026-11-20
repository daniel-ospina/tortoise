# Archived sdk-method `get_session` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_get_session`; recorded destination `get_entity` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'get_session'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def get_session(self, session_id: str) -> dict | None:
        """Get a single agent session by session_id.

        TWO lanes share this read, and they write DIFFERENT node kinds:

        * an indexed agent-session ``Event`` (``eventKind='AgentSession'`` —
          the ``index_sessions`` / session-commit lane), returned as its raw
          properties (the historical contract, positionally unchanged);
        * a captured durable ``:Session`` record (``capture_session``, and the
          hosted ``POST /v1/sessions``), returned in the SAME projection the
          hosted ``GET /v1/sessions/{id}`` serves.

        #3557: the second arm did not exist. The capture lane writes a
        ``:Session`` node plus a ``sessionCaptured`` Event — never an
        ``AgentSession`` Event — so ``get_session`` returned ``None`` for
        EVERY session the SDK itself had just captured, and a self-hosted
        consumer (including the MCP ``tortoise_get_session`` tool, which
        delegates here) could not read back its own captures at all. The
        durable arm below is the read half of the capture lane.
        """
        if not session_id:
            return None
        # #8290: the demo/sample namespace answers ``None`` HERE, before either
        # lane runs. The guard is on the ENTRY, not in the captured arm below,
        # because this read has TWO lanes and the FIRST serves a raw
        # ``AgentSession`` ``Event`` by ``session_id``/``sessionId``; guarding
        # only the captured arm left that one answering for a demo-prefixed id —
        # the SDK serving sample content the hosted detail door 404s, which is
        # this issue's own defect one arm over. An indexed graph can hold such an
        # Event (it predates the namespace's reservation at the capture doors,
        # ``ids.session_id_refusal``). The predicate is the shared
        # ``session_schema.is_demo_session_id``, never a local prefix test, so the
        # SDK cannot exclude a different set than the hosted doors.
        from tortoise.session_schema import is_demo_session_id

        if is_demo_session_id(session_id):
            return None
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (e:Event {eventKind: 'AgentSession'}) "
            "WHERE e.session_id = $sid OR e.sessionId = $sid RETURN properties(e)",
            params={"sid": session_id}
        ).result_set
        if rows:
            return rows[0][0]
        return self._read_captured_session(proj, session_id)
