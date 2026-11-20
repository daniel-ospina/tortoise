# Archived sdk-method `session_index_health` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_session_index_health`; recorded destination `index_sources_from_directory` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'session_index_health'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def session_index_health(self, directory: str | None = None) -> dict:
        """Compare session .md files against indexed AgentSession Events.

        #280 item 2 — the ``tortoise doctor`` health surface. Scans the
        canonical corpus (``~/.tortoise/docs/conversations/`` by default,
        ``TORTOISE_SESSION_CORPUS`` override) and matches each file to its
        expected Event by session_id + file_hash.

        Returns ``{directory, file_count, indexed_events, matched, unindexed,
        stale, up_to_date, duplicates}`` — ``stale`` = Event exists but hash
        differs (re-index needed); ``unindexed`` = no Event at all.

        ``indexed_events`` is CORPUS-SCOPED (#280 review P3): the count of
        AgentSession Events whose eventId matches a corpus file — not all
        AgentSession Events in the graph (other sessions would make the
        doctor arithmetic misleading). ``duplicates`` surfaces sessionIds
        claimed by more than one corpus file (rglob copies / duplicated
        frontmatter): those copies made the sweep permanently non-convergent
        (MERGE is last-writer-wins) — they are surfaced here and skipped in
        ingest_corpus, never silently re-indexed (#280 review P2).
        """
        from pathlib import Path  # noqa: I001
        from .file_indexer import compute_file_hash
        from .session_indexer import (
            corpus_files, extract_session_id, session_corpus_dir,
        )

        dir_path = Path(directory or session_corpus_dir())
        if not dir_path.is_dir():
            return {"directory": str(dir_path), "file_count": 0,
                    "indexed_events": 0, "matched": 0,
                    "unindexed": [], "stale": [], "up_to_date": [],
                    "duplicates": []}

        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (e:Event {eventKind:'AgentSession'}) "
            "RETURN e.eventId, e.file_hash"
        ).result_set
        by_event = {r[0]: r[1] for r in rows}

        files = corpus_files(dir_path)
        # Group by session id: two files may share a sessionId (rglob picking
        # up copies, or duplicated frontmatter). Classify only the PRIMARY
        # file (first in sorted order) so the delta drives the sweep to
        # convergence; non-primary copies surface in the `duplicates` bucket.
        by_sid: dict[str, list[str]] = {}
        for f in files:
            # str() coercion keeps the value hashable as a dict key (a YAML
            # list/dict frontmatter sessionId would raise TypeError) while
            # preserving the base event_id derivation (str() == repr() for
            # str/int/float/bool/list/dict), so health stays consistent with
            # ingest_corpus's frontmatter coercion. Only a read failure
            # (extract returns None) falls back to the file-stem id.
            _sid_raw = extract_session_id(str(f))
            sid = str(_sid_raw) if _sid_raw is not None else f"file_{f.stem}"
            by_sid.setdefault(sid, []).append(str(f))
        unindexed: list[str] = []
        stale: list[str] = []
        up_to_date: list[str] = []
        duplicates: list[dict] = []
        for sid, flist in by_sid.items():
            event_id = f"session_{sid}"
            if len(flist) > 1:
                duplicates.append({"session_id": sid, "event_id": event_id,
                                   "files": flist})
            primary = flist[0]
            file_hash = compute_file_hash(primary)
            existing = by_event.get(event_id)
            if existing is None:
                unindexed.append(primary)
            elif existing == file_hash:
                up_to_date.append(primary)
            else:
                stale.append(primary)
        corpus_event_ids = {f"session_{sid}" for sid in by_sid}
        return {"directory": str(dir_path),
                "file_count": len(files),
                "indexed_events": sum(1 for eid in corpus_event_ids
                                       if eid in by_event),
                "matched": len(up_to_date),
                "unindexed": unindexed,
                "stale": stale,
                "up_to_date": up_to_date,
                "duplicates": duplicates}
