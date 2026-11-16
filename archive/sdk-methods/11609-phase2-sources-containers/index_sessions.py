# Archived sdk-method `index_sessions` — REMOVED from the product surface (#11609).
#
# batch:   11609-phase2-sources-containers
# reason:  name retired into index_sources_from_directory(file_type='agent_session') (#4282 Phase 2, #11609); it was a single delegation, frozen as the record of what the retired name did
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
ARCHIVED_SYMBOL = 'index_sessions'
ARCHIVED_BATCH = '11609-phase2-sources-containers'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '32011c4dad4b42364868cb0810ad8e7317a7dfb6'

# ── verbatim from tortoise/sdk.py ──

    def index_sessions(self, directory: str, extract_metadata: bool = True,
                       llm_model: str | None = "gpt-5-mini",
                       progress_file: str | None = None) -> dict:
        """Index session files as AgentSession Events.

        DEPRECATED — use ``index_directory`` (epic #900). Thin wrapper
        around the frozen legacy ``ingest_corpus`` (SC4 — behavior
        unchanged); see its docstring for the ``extract_metadata``
        flag-semantics divergence (W3, §6.1).
        """
        return self.ingest_corpus(directory, eventKind="AgentSession",
                                  extract_metadata=extract_metadata,
                                  llm_model=llm_model,
                                  progress_file=progress_file)
