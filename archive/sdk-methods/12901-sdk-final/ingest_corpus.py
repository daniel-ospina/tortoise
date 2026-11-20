# Archived sdk-method `ingest_corpus` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_ingest_corpus`; recorded destination `index_sources_from_directory` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'ingest_corpus'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def ingest_corpus(self, directory: str, eventKind: str = "DocumentCreated",
                      extract_metadata: bool = False, llm_model: str | None = "gpt-5-mini",
                      progress_file: str | None = None) -> dict:
        """Batch ingestion — walk directory, parse YAML frontmatter,
        create/update Event nodes. Returns {ingested, updated, skipped, failed, errors}.

        DEPRECATED — use ``index_sources_from_directory`` (epic #900). During the W3
        deprecation window this FROZEN legacy branch keeps its behavior
        byte-identical (SC4). FLAG-SEMANTICS DIVERGENCE (W3, §6.1):
        ``extract_metadata=False`` STILL computes session embeddings on the
        legacy path (``_session_embedding`` unconditional in both AgentSession
        branches) while the new path short-circuits the embedding to None —
        the SAME flag, different Event semantics on two live paths.
        """
        # ── #5475 (residual): ``eventKind`` is CALLER-SUPPLIED here ──
        # This FROZEN legacy branch writes it with a raw ``MERGE … SET e +=
        # $props`` further down, bypassing every gated seam — so the approval
        # gate is applied HERE, before the walk. Measured by the review of the
        # #5475 change: ``ingest_corpus(dir, eventKind='marketing:meeting')`` on
        # a ``dev``-only graph left ``['dev','marketing']``. Behaviour is
        # otherwise unchanged (SC4): a bare / ``core:`` / approved spelling
        # still writes, and the default ``DocumentCreated`` names no pack.
        self._refuse_unapproved_kind("eventKind", eventKind)
        import os as _os  # noqa: I001
        import json as _json
        from pathlib import Path
        from datetime import datetime, timezone

        # #329: ingest path validation — absolute, no `..`, and under the
        # optional TORTOISE_INGEST_BASE_DIR base. The stdio/CLI surface is
        # operator-trusted, but the directory walk reads host files: bound it.
        from .security import ingest_dir_is_safe, resolve_under_base
        ingest_base = None
        raw_base = _os.environ.get("TORTOISE_INGEST_BASE_DIR")
        if raw_base:
            ingest_base = _os.path.realpath(_os.path.expanduser(raw_base))
        if not ingest_dir_is_safe(directory, ingest_base):
            raise ValueError(
                f"Unsafe ingest directory: {directory!r}. Directory must be "
                f"absolute, contain no '..' components, and resolve under "
                f"TORTOISE_INGEST_BASE_DIR when set ({ingest_base or '<unset>'})."
            )
        if progress_file is not None:
            if not isinstance(progress_file, str) or not progress_file:
                raise ValueError("progress_file must be a non-empty string.")
            if not _os.path.isabs(progress_file):
                raise ValueError(f"progress_file must be absolute: {progress_file!r}")
            if ".." in Path(progress_file).parts:
                raise ValueError(f"progress_file contains '..': {progress_file!r}")
            if ingest_base is not None and resolve_under_base(progress_file, ingest_base) is None:
                raise ValueError(
                    f"progress_file {progress_file!r} not under TORTOISE_INGEST_BASE_DIR."
                )

        # Canonical boundary regex lives in file_indexer (#280 review round 6):
        # hoisted so extract/health/ingest can never drift apart again (the round-5
        # bug was exactly two copies diverging → permanent sweep non-convergence).
        from .file_indexer import _FM_RE
        ingested, updated, skipped, failed = 0, 0, 0, 0
        errors: list[dict] = []
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        proj = self._get_proj()

        def _record_failure(_rel: str, _reason: str, *,
                            retryable: bool) -> None:
            """Count ONE file as failed without aborting the sweep (#9122).

            A journal-append failure is retryable; a #5475 kind refusal is not
            (it will refuse identically on every retry, so a `retryable` flag
            would make the sweep non-convergent on that file). Neither may take
            the whole corpus down with it: `ingest_corpus` is a batch door.
            """
            nonlocal failed
            failed += 1
            errors.append({"file": _rel, "error": _reason,
                           "retryable": retryable})

        files = sorted(Path(directory).rglob("*.md"))

        # Resume from progress file
        completed_files: list[str] = []
        if progress_file:
            try:
                with open(progress_file) as pf:
                    progress = _json.load(pf)
                    completed_files = progress.get("completed_files", [])
            except Exception:
                pass
        processed_set = set(completed_files)

        # #280 review P2: two corpus files sharing a sessionId (duplicated
        # frontmatter, or rglob picking up copies) used to make the sweep
        # permanently non-convergent — MERGE is last-writer-wins, so the
        # losing copy stays hash-stale and every run re-merges both. Dedupe
        # the scan to ONE primary file per sessionId (first in sorted order);
        # non-primary copies are surfaced by session_index_health()'s
        # `duplicates` bucket instead of being re-indexed every run.
        _primary_sessions: dict[str, str] = {}

        for i, filepath in enumerate(files):
            rel_path = str(filepath)
            if rel_path in processed_set:
                skipped += 1
                continue

            if eventKind == "AgentSession" and filepath.is_symlink():
                # R17 parity with mining.py's pre-scan: a symlinked *.md is
                # never read (host-file read + LLM exfiltration when
                # extract_metadata/llm_model is set) and never participates in
                # primary-session selection — otherwise a symlink sorting
                # before a real file sharing its sessionId becomes ingest's
                # primary (target content read+indexed) while mining picks the
                # real file → hash never matches → re-mined every run (point
                # stacking; round-2 review).
                skipped += 1
                errors.append({"file": rel_path,
                               "error": "symlinked file skipped (R17: the corpus "
                                         "walk must not follow symlinks)",
                               "retryable": False})
                continue

            try:
                text = filepath.read_text(encoding="utf-8")
            except Exception as e:
                failed += 1
                errors.append({"file": rel_path, "error": str(e), "retryable": False})
                continue

            m = _FM_RE.match(text)
            frontmatter: dict = {}
            if m:
                try:
                    import yaml as _yaml
                    parsed = _yaml.safe_load(m.group(1))
                    if isinstance(parsed, dict):
                        frontmatter = parsed
                        # YAML types (bool/int) must not leak into string fields
                        # (regression vs old line-by-line parser). Coerce known
                        # string fields to str.
                        for _k in ("format", "version", "title",
                                   "sessionId", "session_id", "agent"):
                            if _k in frontmatter and frontmatter[_k] is not None:
                                frontmatter[_k] = str(frontmatter[_k])
                except Exception:
                    pass  # fallback to empty dict

            # Optional frontmatter-metadata validation (#1362) — warn-only,
            # gated by TORTOISE_VALIDATE_FRONTMATTER=1 (default OFF). The
            # tolerant parse above is UNCHANGED ({} on malformed still
            # degrades); this only reports missing/malformed required fields.
            from .frontmatter_validator import validate_and_warn
            validate_and_warn(
                frontmatter,
                kind="session" if eventKind == "AgentSession" else "document",
                context=f"ingest_corpus:{rel_path}",
            )

            # #330: content identity hash shared by both modes (hashlib is
            # module-imported). byte-identical re-ingest -> skipped.
            file_hash = hashlib.sha256(text.encode()).hexdigest()

            if eventKind == "AgentSession":
                # AgentSession branch — session indexing with metadata extraction
                session_id = frontmatter.get("sessionId") or frontmatter.get("session_id") or f"file_{filepath.stem}"
                # #280 review P2: non-primary copy of a duplicated sessionId —
                # skip deterministically (retryable:False — re-running changes
                # nothing); the duplicate is surfaced, not silently re-indexed.
                _primary = _primary_sessions.get(session_id)
                if _primary is not None and _primary != rel_path:
                    skipped += 1
                    errors.append({"file": rel_path,
                                   "error": f"duplicate sessionId '{session_id}' "
                                            f"(primary file: {_primary}) — non-primary "
                                            f"copy skipped (see session_index_health "
                                            f"'duplicates')",
                                   "retryable": False})
                    continue
                _primary_sessions.setdefault(session_id, rel_path)
                event_id = f"session_{session_id}"
                name = frontmatter.get("title", filepath.stem)
                # #280: per-session flock — serialize against the session-end hook's
                # single-file writer and concurrent sweeps (MATCH->SET is not MERGE-atomic).
                # A live holder -> skip WITHOUT marking the file complete (retried later).
                from .index_lock import SessionIndexLock
                _lock = SessionIndexLock(session_id)
                try:
                    _lock_status = _lock.acquire()
                except (OSError, AttributeError, ImportError) as _lock_err:
                    # #280 review P2 (robustness): an unusable lock path must
                    # never abort the batch sweep — unwritable/blocked lock dir
                    # (EACCES/EROFS/ENOSPC/EMFILE) or a planted symlink (ELOOP
                    # from O_NOFOLLOW) is recorded as a retryable error and the
                    # sweep continues (same as the held path).
                    skipped += 1
                    errors.append({"file": rel_path,
                                   "error": f"session lock unavailable: {_lock_err}",
                                   "retryable": True})
                    continue
                if _lock_status == "held":
                    skipped += 1
                    errors.append({"file": rel_path,
                                   "error": f"session lock held: {_lock.detail}",
                                   "retryable": True})
                    continue
                try:

                    # Check dedup
                    exists_rows = proj.g.query(
                        "MATCH (e:Event {eventId:$eid}) RETURN properties(e)",
                        params={"eid": event_id},
                    ).result_set
    
                    if exists_rows:
                        existing_props = exists_rows[0][0]
                        # #330: skip unchanged + complete events. has_keywords is the
                        # sole completeness signal (an eventStatus disjunct would skip
                        # incomplete sessions that still need enrichment).
                        has_keywords = bool(existing_props.get("keywords"))
                        if existing_props.get("file_hash") == file_hash and has_keywords:
                            skipped += 1
                            completed_files.append(rel_path)
                            continue
                        # Always extract keywords (even without LLM)
                        from .session_indexer import extract_keywords_from_frontmatter as _kw_fallback  # noqa: I001
                        if extract_metadata:
                            from .session_indexer import extract_metadata as _extract
                            try:
                                metadata = _extract(text, llm_model)
                            except Exception:
                                metadata = _kw_fallback(text)
                        else:
                            metadata = _kw_fallback(text)
    
                        merged_keywords = list(dict.fromkeys(
                            existing_props.get("keywords", []) + metadata.get("keywords", [])
                        ))[:20]
                        existing_arc = existing_props.get("content_metadata", "{}")
                        try:
                            existing_arc = _json.loads(existing_arc) if isinstance(existing_arc, str) else existing_arc
                        except Exception:
                            existing_arc = {}
                        # #330: dedup + cap the narrative arc so repeated enrichment
                        # of unchanged files never grows it unboundedly (and the
                        # state-change comparison below stays deterministic). Arc
                        # entries may be dicts (phase/topic/decisions), so dedup via
                        # a canonical JSON key (str-sorted to tolerate mixed-type
                        # keys), not dict.fromkeys.
                        _arc_seen = {}
                        for _phase in (existing_arc.get("narrative_arc", [])
                                       + metadata.get("narrative_arc", [])):
                            _key = _json.dumps(_phase, sort_keys=True, default=str)
                            _arc_seen.setdefault(_key, _phase)
                        _merged_phases = list(_arc_seen.values())
                        # Cap while preferring genuinely-new phases: keep existing
                        # ones first (already-merged), then append new ones up to
                        # the cap so a full arc never starves fresh phases.
                        _existing_phases = existing_arc.get("narrative_arc", [])
                        _new_only = [p for p in _merged_phases
                                     if _json.dumps(p, sort_keys=True, default=str)
                                     not in {_json.dumps(e, sort_keys=True, default=str)
                                             for e in _existing_phases}]
                        new_phases = (_merged_phases[:len(_existing_phases)]
                                      + _new_only)[:50]
    
                        # Normalize topics to a comparable, hashable form (#330):
                        # LLM output is unvalidated — a list-of-dicts would crash
                        # set() and abort the whole run.
                        def _norm_topics(t) -> list:
                            t = t or []
                            if not isinstance(t, list):
                                t = [t]
                            return [str(x) for x in t]
                        _new_topics = _norm_topics(metadata.get("topics",
                                                                existing_props.get("topics", [])))
                        _stored_topics = _norm_topics(existing_props.get("topics"))
                        _stored_keywords = _norm_topics(existing_props.get("keywords"))
                        _new_name = metadata.get("summary", existing_props.get("name", name))
    
                        update_props = {
                            "name": _new_name,
                            "keywords": merged_keywords,
                            "topics": _new_topics,
                            "file_hash": file_hash,
                            "content_metadata": _json.dumps({
                                "schema_version": 1,
                                "summary": metadata.get("summary", ""),
                                "narrative_arc": new_phases,
                                "issues": metadata.get("issues", []),
                                "prs": metadata.get("prs", []),
                                "critical_decisions": metadata.get("critical_decisions", []),
                            }),
                            "message_count": frontmatter.get("message_count", 0),
                        }
                        # #4647: this writer hands $props straight to the store
                        # (raw Cypher parameters), so it bypasses
                        # _sanitize_props and must apply the numeric-domain
                        # guard itself — a frontmatter int is an arbitrary-
                        # precision Python int the store would SILENTLY clamp.
                        for _k, _v in update_props.items():
                            _reject_unrepresentable_number(_k, _v)
                        # #330: unchanged content whose enrichment produced nothing
                        # new counts as skipped, not updated (counter honesty).
                        # Compare the FULL payload that would be written (keywords,
                        # normalized topics, name, narrative_arc, issues/prs/
                        # critical_decisions — the latter feed _connect_issue_objects)
                        # so a real change in any persisted field is never
                        # miscounted as a skip.
                        if existing_props.get("file_hash") == file_hash:
                            _old_meta = existing_arc
                            changed = (
                                set(_norm_topics(merged_keywords)) != set(_stored_keywords)
                                or set(_new_topics) != set(_stored_topics)
                                or _new_name != existing_props.get("name", name)
                                or new_phases != list(existing_arc.get("narrative_arc", []))
                                or _norm_topics(metadata.get("issues", [])) != _norm_topics(_old_meta.get("issues", []))
                                or _norm_topics(metadata.get("prs", [])) != _norm_topics(_old_meta.get("prs", []))
                                or _norm_topics(metadata.get("critical_decisions", [])) != _norm_topics(_old_meta.get("critical_decisions", []))
                            )
                            if not changed:
                                skipped += 1
                                completed_files.append(rel_path)
                                continue
    
                        # #244: (re)compute the session embedding from the merged
                        # surface and store as vecf32 — None when model unavailable.
                        embedding = self._session_embedding(
                            update_props["name"], metadata.get("summary", ""),
                            merged_keywords, update_props["topics"],
                        )
                        # #9122 (§17.1): this writer reached the cache with no
                        # journal record. Journal-first, then the handle inside
                        # the sanctioned scope (see `_event_raw_write_door`).
                        #
                        # ⛔ The RECORD carries the merged node view, not
                        # `update_props` alone. This arm is a partial patch
                        # (`e += $props`), but its replay is not partial: the
                        # fold writes its OWN fixed props dict (`startedAt`,
                        # `endedAt`, `format`, `subject`, `object`, …) with
                        # defaults for anything the record omits
                        # (`_upsert_event`), so a record carrying only this
                        # patch would REPLACE a good `startedAt` with `''` and
                        # a `format` of `markdown` with `jsonl` on every
                        # rebuild — silently dropping the key `search_sessions`
                        # orders by. Measuring it is what caught this; the
                        # established precedent is `_session_event_write`, which
                        # journals the FULL props dict on its ON MATCH arm too.
                        # `embedding` is excluded from the merge: the stored
                        # value is a `Vectorf32` (not JSON) and the live clause's
                        # preserve-on-null already governs it, so the computed
                        # vector below is the only one that may ride the record.
                        _record_props = {k: v for k, v in existing_props.items()
                                         if k != "embedding"}
                        _record_props.update(update_props)
                        # The record must carry the vector the NODE will end up
                        # with. The live clause is preserve-on-null, so a None
                        # here keeps the stored vector — but the FOLD does not
                        # preserve: with no `embedding` on the record it falls
                        # back to encoding `subject + eventKind + object`, i.e.
                        # the single token "AgentSession", and writes that OVER
                        # the good stored vector. Carrying the stored value
                        # forward is what keeps live == replay for it.
                        _record_embedding = (
                            embedding if embedding is not None
                            else _journal_vector(existing_props.get("embedding")))
                        try:
                            _written = self._event_raw_write_door(
                                proj,
                                "MATCH (e:Event {eventId:$eid}) "
                                + clear_then_set_prefix("e")
                                + "SET e += $props, " + set_embedding_clause("e"),
                                event_id=event_id, props=_record_props,
                                event_kind=eventKind,
                                embedding=_record_embedding,
                                lane="ingest_corpus",
                            )
                        except UnapprovedKindNamespace as _refused:
                            _record_failure(rel_path, f"refused: {_refused}",
                                            retryable=False)
                            continue
                        if not _written:
                            # Append is the commit point: nothing was written, so
                            # do NOT count it updated or mark the file complete —
                            # the next run retries it.
                            _record_failure(
                                rel_path,
                                "journal append failed — the :Event write was "
                                "not committed", retryable=True)
                            continue
                        updated += 1
                        completed_files.append(rel_path)
                        # Connect issue/PR references to Objects
                        self._connect_issue_objects(event_id, metadata)
                    else:
                        # New session Event — always extract keywords
                        from .session_indexer import extract_keywords_from_frontmatter as _kw_fallback  # noqa: I001
                        if extract_metadata:
                            from .session_indexer import extract_metadata as _extract
                            try:
                                metadata = _extract(text, llm_model)
                            except Exception:
                                metadata = _kw_fallback(text)
                        else:
                            metadata = _kw_fallback(text)
    
                        props = {
                            "name": metadata.get("summary", name),
                            "eventKind": eventKind,
                            "session_id": session_id,
                            "agent": frontmatter.get("agent", "pi"),
                            "source_file": rel_path,
                            "file_hash": file_hash,
                            "keywords": metadata.get("keywords", []),
                            "topics": metadata.get("topics", []),
                            "message_count": frontmatter.get("message_count", 0),
                            "startedAt": now,
                            "content_metadata": _json.dumps({
                                "schema_version": 1,
                                "summary": metadata.get("summary", ""),
                                "narrative_arc": metadata.get("narrative_arc", []),
                                "issues": metadata.get("issues", []),
                                "prs": metadata.get("prs", []),
                                "critical_decisions": metadata.get("critical_decisions", []),
                            }),
                            "eventStatus": "indexed",
                            "classificationLevel": "internal",
                            "format": "markdown",
                        }
                        # #4647: same direct-$props writer as the update branch
                        # above — guard before the write.
                        for _k, _v in props.items():
                            _reject_unrepresentable_number(_k, _v)
                        # #244: compute the session embedding (name + summary +
                        # keywords + topics) and store as vecf32 — None when the
                        # model is unavailable (indexing never depends on it).
                        embedding = self._session_embedding(
                            props["name"], metadata.get("summary", ""),
                            props["keywords"], props["topics"],
                        )
                        # #9122 (§17.1): journal the new session Event before
                        # the handle write (see `_event_raw_write_door`).
                        try:
                            _written = self._event_raw_write_door(
                                proj,
                                "MERGE (e:Event {eventId:$eid}) "
                                + clear_then_set_prefix("e")
                                + "SET e += $props, " + set_embedding_clause("e"),
                                event_id=event_id, props=props,
                                event_kind=eventKind, embedding=embedding,
                                lane="ingest_corpus",
                            )
                        except UnapprovedKindNamespace as _refused:
                            _record_failure(rel_path, f"refused: {_refused}",
                                            retryable=False)
                            continue
                        if not _written:
                            _record_failure(
                                rel_path,
                                "journal append failed — the :Event write was "
                                "not committed", retryable=True)
                            continue
                        ingested += 1
                        completed_files.append(rel_path)
                        # Connect issue/PR references to Objects
                        self._connect_issue_objects(event_id, metadata)
                finally:
                    _lock.release()
            else:
                # Original DocumentCreated logic
                doc_id = str(filepath.relative_to(directory))
                title = frontmatter.get("title", filepath.stem)
                doc_kind = frontmatter.get("type", frontmatter.get("document_kind", ""))
                domain = frontmatter.get("domain", frontmatter.get("documentKnowledgeDomain", ""))

                # #330: three-way on ROW PRESENCE (new doc -> zero rows;
                # legacy event -> row with file_hash=None). RETURN e.file_hash
                # so byte-identical re-ingest is counted as skipped.
                exists_rows = proj.g.query(
                    "MATCH (e:Event {eventId:$eid}) RETURN e.file_hash",
                    params={"eid": doc_id},
                ).result_set

                props = {
                    "title": title,
                    "document_kind": doc_kind,
                    "document_knowledge_domain": domain,
                    "authored_by": frontmatter.get("authoredBy", ""),
                    "owned_by": frontmatter.get("ownedBy", ""),
                    "managed_by": frontmatter.get("managedBy", ""),
                    "governing_agreement": frontmatter.get("governedBy", frontmatter.get("governingAgreement", "")),
                    "format": "markdown",
                    "version": frontmatter.get("version", ""),
                    "createdAt": frontmatter.get("created", now),
                    "updatedAt": frontmatter.get("updated", now),
                    "eventKind": "DocumentCreated",
                    "classificationLevel": "internal",
                    "file_hash": file_hash,
                }
                # #4647: the DEFAULT ingest branch (and the path the MCP
                # ``tortoise_ingest_corpus`` tool takes) hands $props straight
                # to the store; frontmatter values are arbitrary-precision
                # Python ints from ``yaml.safe_load``, so they must be guarded
                # here too — the AgentSession branches above already are.
                for _k, _v in props.items():
                    _reject_unrepresentable_number(_k, _v)

                if not exists_rows:
                    # New document
                    # #9122 (§17.1): journal-first, then the handle inside the
                    # sanctioned scope (see `_event_raw_write_door`).
                    try:
                        _written = self._event_raw_write_door(
                            proj,
                            "CREATE (e:Event {eventId:$eid}) SET e += $props",
                            event_id=doc_id, props=props,
                            lane="ingest_corpus",
                        )
                    except UnapprovedKindNamespace as _refused:
                        _record_failure(rel_path, f"refused: {_refused}",
                                        retryable=False)
                        continue
                    if not _written:
                        _record_failure(
                            rel_path,
                            "journal append failed — the :Event write was not "
                            "committed", retryable=True)
                        continue
                    ingested += 1
                elif exists_rows[0][0] == file_hash:
                    # Byte-identical re-ingest — nothing to do (#330)
                    skipped += 1
                else:
                    # Changed content, or legacy event without a stored hash
                    # (None) — update and backfill the hash.
                    # #9122 (§17.1): the update arm is a write too, so it
                    # carries its own record.
                    try:
                        _written = self._event_raw_write_door(
                            proj,
                            "MATCH (e:Event {eventId:$eid}) SET e += $props",
                            event_id=doc_id, props=props,
                            lane="ingest_corpus",
                        )
                    except UnapprovedKindNamespace as _refused:
                        _record_failure(rel_path, f"refused: {_refused}",
                                        retryable=False)
                        continue
                    if not _written:
                        _record_failure(
                            rel_path,
                            "journal append failed — the :Event write was not "
                            "committed", retryable=True)
                        continue
                    updated += 1

            # Progress checkpoint every 100 files
            if progress_file and (i + 1) % 100 == 0:
                _save_progress(progress_file, str(directory), len(files),
                              ingested + updated + skipped + failed,
                              ingested, updated, skipped, failed, errors,
                              completed_files=completed_files)

        monitoring.record_ingest()

        if progress_file:
            _save_progress(progress_file, str(directory), len(files),
                          ingested + updated + skipped + failed,
                          ingested, updated, skipped, failed, errors,
                          completed_files=completed_files)

        return {"ingested": ingested, "updated": updated, "skipped": skipped,
                "failed": failed, "errors": errors}
