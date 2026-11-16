# Archived sdk-method `index_directory` — REMOVED from the product surface (#11609).
#
# batch:   11609-phase2-sources-containers
# reason:  name retired into index_sources_from_directory (#4282 Phase 2, #11609): row 15 keeps this walker under its own name, frozen here as the record of what the retired name did
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
ARCHIVED_SYMBOL = 'index_directory'
ARCHIVED_BATCH = '11609-phase2-sources-containers'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '32011c4dad4b42364868cb0810ad8e7317a7dfb6'

# ── verbatim from tortoise/sdk.py ──

    def index_directory(self, directory: str,
                        *, file_type: str = "auto",
                        corpus_name: str | None = None,
                        extract_metadata: bool = True,
                        llm_model: str | None = "gpt-5-mini",
                        embedding_repair_backoff: float | None = None,
                        progress_file: str | None = None,
                        ) -> dict:
        """Batch walk (custom bounded walker, W1 cycle-4 pin). Returns:
        {"directory", "corpus_name", "file_count", "indexed", "updated",
         "skipped", "failed", "aborted", "ignored", "errors": [...],
         "by_kind": {kind: count}, "aborted_reason"?}
        Invariant: indexed + updated + skipped + failed + aborted == file_count.
        file_count counts `*.md` files ONLY (non-md walk entries land in
        `ignored`, never in file_count or the four buckets); aborted = files
        never reached because the run aborted (bounded-abort disposition,
        E2E-19); aborted_reason names the DB-failure class when aborted > 0.
        errors[] entries carry the cause-class token (decode/size/escape/
        structural/filename/db/lock) + the rel-path + limit values where
        applicable (§6.4 cycle-21).
        Raises: ValueError — #329 boundary violations (directory +
        progress_file, realpath-resolved; symlink roots resolving outside the
        base, E2E-7(v1)); TORTOISE_MAX_FILE_MB garbage/<=0.
        """
        import os as _os  # noqa: I001
        from pathlib import Path
        from .security import resolve_under_base as _rub
        from .index_walk import (walk_markdown, compute_dispositions,  # noqa: F401
                                 DISP_ESCAPE, DISP_UNRECONCILED,
                                 DISP_STRUCTURAL)
        raw_base = _os.environ.get("TORTOISE_INGEST_BASE_DIR")
        ingest_base = _os.path.realpath(_os.path.expanduser(raw_base)) if raw_base else None

        # ── directory-argument resolution (cycle-7): the DIRECTORY argument
        # gets the SAME realpath-resolved treatment as progress_file — an
        # in-base symlink root whose target resolves OUTSIDE the base raises
        # ValueError BEFORE any walk (E2E-7(v1)); inside → indexes normally
        # with realpath-derived urls (E2E-7(v2)).
        if not isinstance(directory, str) or not directory:
            raise ValueError("index_directory: directory must be a non-empty string")
        if not _os.path.isabs(directory) or ".." in Path(directory).parts:
            raise ValueError(
                f"Unsafe directory {directory!r} — must be absolute with no "
                f"'..' components (#329)."
            )
        dir_path = Path(directory)
        if not dir_path.is_dir():
            # cycle-12 disposition: nonexistent walk root = zero-count no-op
            # (legacy session_index_health is_dir() parity — the backgrounded
            # hook must never see an unhandled FileNotFoundError traceback).
            return {"directory": str(dir_path), "corpus_name": corpus_name or "",
                    "file_count": 0, "indexed": 0, "updated": 0,
                    "skipped": 0, "failed": 0, "aborted": 0, "ignored": 0,
                    "errors": [], "by_kind": {}}
        resolved_dir = _os.path.realpath(str(dir_path))
        if ingest_base is not None and _rub(resolved_dir, ingest_base) is None:
            raise ValueError(
                f"Unsafe directory {directory!r} — its resolved target "
                f"{resolved_dir!r} is outside TORTOISE_INGEST_BASE_DIR "
                f"({ingest_base})."
            )

        # ── progress_file bounds (cycle-6): REALPATH-RESOLVED through
        # resolve_under_base BEFORE any walk/write; nothing may materialize at
        # a resolved target outside the base (E2E-10(g3)).
        if progress_file is not None:
            if not isinstance(progress_file, str) or not progress_file:
                raise ValueError("progress_file must be a non-empty string.")
            if not _os.path.isabs(progress_file):
                raise ValueError(f"progress_file must be absolute: {progress_file!r}")
            if ".." in Path(progress_file).parts:
                raise ValueError(f"progress_file contains '..': {progress_file!r}")
            if ingest_base is not None and _rub(progress_file, ingest_base) is None:
                raise ValueError(
                    f"progress_file {progress_file!r} not under "
                    f"TORTOISE_INGEST_BASE_DIR (resolved-target discipline, "
                    f"§6.4/E2E-10(g3))."
                )

        corpus_root = Path(resolved_dir)
        corpus_name = corpus_name or corpus_root.name
        max_bytes = self._index_max_bytes()  # noqa: F841
        repair_backoff = self._index_repair_backoff(embedding_repair_backoff)
        # TORTOISE_INDEX_NO_NETWORK (test-only, cycle-10): FORCES
        # extract_metadata=False-equivalent omission at the NEW-PATH call
        # boundary regardless of the flag (NEVER inside the shared
        # `_session_embedding` — the legacy path must still embed; SC4).
        if self._index_no_network():
            extract_metadata = False

        result = {
            "directory": str(corpus_root),
            "corpus_name": corpus_name,
            "file_count": 0, "indexed": 0, "updated": 0, "skipped": 0,
            "failed": 0, "aborted": 0, "ignored": 0,
            "errors": [], "by_kind": {}, "aborted_reason": None,
        }

        # ── bounded walk + pre-write dispositions ─────────────────────
        # Per-corpus run lock: serializes concurrent index_directory runs on
        # the SAME corpus in this process (the threads leg; the embedded
        # engine's cross-connection MERGE semantics cannot provide a race-safe
        # counter outcome otherwise). Different corpora parallelize.
        with _index_run_lock_for("dir:" + resolved_dir):
            return self._index_directory_locked(
                corpus_root, corpus_name, file_type, extract_metadata,
                llm_model, repair_backoff, progress_file, ingest_base, result,
                keys={} if progress_file is None else self._index_load_progress(
                    progress_file, str(corpus_root)))
