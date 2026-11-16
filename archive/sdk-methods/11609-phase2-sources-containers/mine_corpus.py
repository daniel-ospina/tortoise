# Archived sdk-method `mine_corpus` — REMOVED from the product surface (#11609).
#
# batch:   11609-phase2-sources-containers
# reason:  name retired into mine_knowledge_from_directory (#4282 Phase 2, #11609): row 17 keeps this miner under its own name, frozen here as the record of what the retired name did
# source:  tortoise/sdk.py @ 32011c4dad4b42364868cb0810ad8e7317a7dfb6
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'sdk-method'
ARCHIVED_SYMBOL = 'mine_corpus'
ARCHIVED_BATCH = '11609-phase2-sources-containers'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '32011c4dad4b42364868cb0810ad8e7317a7dfb6'

# ── verbatim from tortoise/sdk.py ──

    def mine_corpus(self, directory: str, *, extract_entities: bool = True,
                    progress_file: str | None = None, model=None,
                    event_log_path: str | None = None) -> dict:
        """Batch-mine a session corpus (J-1, plan §6.1) into this graph.

        COMPOSES :meth:`ingest_corpus` (security, resume, file_hash — R17):
        each file is first indexed as an AgentSession Event by the shared
        machinery, then mined via ConversationMiner (Points/Operators/Events +
        Phase-2 entity Objects with aboutObject/aboutEvent wiring, DE2E-1).
        ``event_log_path`` routes mining events to the given JSONL log
        (default: this SDK's configured event log, else a fallback next to
        the DB path).

        Returns: {sessions, ingested, updated, skipped, failed, entities,
        objects, dedup_hits, drafts, errors:[{file, error, retryable}]}.
        Unchanged re-runs report ``skipped`` via file_hash and add no new
        entities/objects (DE2E-N8).
        """
        from tortoise.mining import mine_corpus_with_sdk
        return mine_corpus_with_sdk(
            self, directory, extract_entities=extract_entities,
            progress_file=progress_file, model=model,
            event_log_path=event_log_path,
        )
