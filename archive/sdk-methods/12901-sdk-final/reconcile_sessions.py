# Archived sdk-method `reconcile_sessions` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_reconcile_sessions`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'reconcile_sessions'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def reconcile_sessions(self, directory: str | None = None,
                           extract_metadata: bool = False,
                           llm_model: str | None = "gpt-5-mini") -> dict:
        """Reconciliation sweep (#280 item 3) — scan for unindexed session
        files and re-index them.

        Scan-then-replay: ``session_index_health()`` computes the delta
        (unindexed + hash-stale files), then ``ingest_corpus()`` replays the
        directory — its dedup skips everything up-to-date and the per-session
        flock (#280 item 1) serializes against concurrent hook writers. No
        cron infra needed: the sweep triggers from the same hook/CLI surface
        (align decision) — run it manually via ``tortoise index sessions`` or
        from session-end.sh.

        ``extract_metadata`` defaults to False so sweeps use the cheap
        keyword fallback — never burn LLM tokens on bulk retry.
        """
        from pathlib import Path  # noqa: I001
        from .session_indexer import session_corpus_dir

        directory = str(Path(directory or session_corpus_dir()).resolve())
        health = self.session_index_health(directory)
        result: dict = {}
        if health["unindexed"] or health["stale"]:
            result = self.ingest_corpus(
                directory, eventKind="AgentSession",
                extract_metadata=extract_metadata, llm_model=llm_model,
            )
        return {**health, "reindex": result}
