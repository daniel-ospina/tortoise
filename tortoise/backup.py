"""P1-10 #6982: Backup/restore — JSONL archiver + FalkorDB BGSAVE.

Backup: copies events.jsonl to timestamped dir, triggers BGSAVE on FalkorDB.
Restore: replays backup JSONL into a fresh projection.
"""
from __future__ import annotations

import inspect
import logging
import os
import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from tortoise.config import is_db_uri
from tortoise.cypher_guard import tolerates_altered_numbers

logger = logging.getLogger(__name__)


def _count_journal_events(events_file: Path) -> int:
    """Count the journal's events the way the READER counts them, unparsed.

    ``len(read_all()) + torn_trailing_count`` — records PLUS the torn
    fragments the reader tolerates, minus the seal annotations ``append``
    writes, complete or TORN (#5917) — without json-parsing every record.
    Parsing to produce a REPORTED number measured 20 s on a 300,000-record
    journal against 0.07 s for this pass; both are pinned to agree in
    ``tests/test_5917_torn_tail_seal.py``.

    Byte-safe by construction (``errors="replace"``) and universal-newline
    aware (the text mode's default), so a multi-byte tear or a bare-CR journal
    counts without raising.
    """
    from tortoise.log import SEAL_SENTINEL, is_seal_annotation
    count = 0
    with open(events_file, encoding="utf-8", errors="replace") as f:
        for line in f:
            text = line.strip()
            if not text or is_seal_annotation(text):
                continue
            if SEAL_SENTINEL.startswith(text):
                continue          # a TORN annotation is not an event
            count += 1
    return count


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: UP017


@contextmanager
def _destination_rollback(events_path: str, db_path: str | None, *,
                          replace_db: bool):
    """#7767 — a refused restore must not leave the destination destroyed.

    ``restore`` REPLACES the destination journal, and — when the backup
    carries a snapshot — the destination DB *and* its AOF dir, BEFORE the
    replay loop that can refuse (``assert_no_non_folded``) or fail (a torn
    backend, a malformed mid-file journal line, an ``OSError`` mid-copy). The
    torn-tail verdict is taken earlier still (#3316), but every OTHER refusal
    shape is only decidable by replaying, so a refusal used to land on a store
    that had already been destroyed, with no rollback: the caller got the
    exception AND a mutated destination.

    Each destination the copy is about to overwrite is renamed ASIDE — an
    O(1) same-filesystem rename, NOT a copy of the whole DB, so the guarantee
    costs ~nothing on a large store — and moved back verbatim when the block
    raises. Any partial artifact left at the destination is discarded first,
    so a destination that did not exist before is not left behind either; on
    success the aside copies are dropped.

    ``db_path`` is overwritten by the copy ONLY when the backup carries a
    snapshot, so it is staged aside only then. When it does NOT, the
    destination DB is deliberately left IN PLACE — the JSONL fallback folds
    ON TOP of it, so moving it aside would silently replay onto an empty
    graph. It is still TRACKED, so a destination DB the replay newly created
    is removed rather than left half-populated.

    Deliberately NOT a pre-flight classification of the journal (the rejected
    option (a) on #7767). The non-folded set depends on the graph the replay
    LANDS on — the JSONL fallback folds into the existing destination graph
    when the backup carries no snapshot — so a journal-only pre-flight could
    not decide it, and every future refusal shape would re-open the window.
    Renaming aside is correct for every failure mode instead, including ones
    the code cannot attribute to a known refusal.

    BOUND (named, not claimed away): an in-place fold into a destination graph
    that already had nodes is mutated before the refusal and a rename cannot
    un-apply it. That case only arises when the backup carries NO snapshot (a
    snapshot is copied over, so the replay starts from the backup's own DB),
    and closing it needs option (b)'s full temp copy of the DB rather than an
    O(1) rename — tracked by #7928, with the measurement. A destination DB the
    replay newly CREATED is removed, so the common shape is covered here.
    The adjacent `_recover_or_raise` leg of the issue is #7929.

    CONCURRENCY (stated, not fixed): the aside-then-restore protocol assumes
    exclusive ownership of the destination, and `restore` takes no lock of its
    own. There is no store-wide WRITER lock to lean on: the flock taken when a
    projection is constructed is the per-RDB CONSTRUCTION lock
    (tortoise/embedded_lifecycle.py), held only for the constructor and released
    when it returns, and the only lifetime lock is the SHARED owner/reaper lock
    — two restores can hold it together. Two restores into the SAME destination
    can therefore interleave across the WHOLE restore (staging, copy, replay,
    and the success-path aside-drop), not merely the staging renames; one
    rollback can then discard another's committed result. That is outside the
    supported posture rather than a new contract: an embedded store is a
    single-writer store (docs/durability-posture.md), so callers must not run
    two restores against one destination concurrently. Closing it needs a
    destination-wide lock (tracked with the #7928 residual, not widened here).
    """
    token = uuid.uuid4().hex[:12]
    staged: list[tuple[Path, Path]] = []
    created: list[Path] = []

    def _stage(original: Path, *, overwrite: bool) -> None:
        if not original.exists():
            created.append(original)
            return
        if not overwrite:
            return          # stays in place; the replay folds INTO it
        side = original.with_name(f"{original.name}.restore-aside-{token}")
        os.replace(original, side)
        staged.append((original, side))

    def _discard(path: Path) -> None:
        """Best-effort removal that never raises, but never hides a failure.

        Cleanup is the last thing standing between a failed restore and a lost
        destination, so a removal that does not happen must be VISIBLE: the
        caller's original exception says nothing about it, and the bytes it
        failed to remove can shadow the restored destination. `FileNotFoundError`
        is the expected no-op; every other `OSError` is logged.
        """
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                os.remove(path)
        except FileNotFoundError:
            return
        except OSError:
            logger.warning(
                "restore: could not remove %s — what remains there may shadow "
                "the restored destination", path, exc_info=True)

    # The staging renames live INSIDE the guarded region. `os.replace` is not
    # infallible (a read-only parent dir, ENOSPC, or the exists()->replace
    # race), and a raise from a LATER stage used to escape before any handler
    # ran: the journal was already renamed aside and was never put back, so a
    # failed restore destroyed the destination — the exact #7767 shape, in the
    # staging phase. events_path and db_path are frequently different
    # directories, so a failure on one must not strand the other.
    try:
        _stage(Path(events_path), overwrite=True)
        if (db_path is not None and str(db_path) != ":memory:"
                and not is_db_uri(str(db_path))):
            from tortoise.projection import stale_aof_dirs
            _stage(Path(db_path), overwrite=replace_db)
            # The AOF dirs go with the DB: `remove_stale_aof` deletes them, so
            # a refused restore must not take the destination's unflushed data
            # too.
            for aof in stale_aof_dirs(db_path):
                _stage(aof, overwrite=replace_db)
        yield
    except BaseException:
        for original, _side in staged:
            _discard(original)
        for path in created:
            _discard(path)
        for original, side in reversed(staged):
            # The unwind must not itself raise: an un-restorable entry would
            # otherwise abandon every remaining one AND replace the caller's
            # original exception with the rollback's. Each restore is attempted
            # independently — but a failure is LOGGED, never silent, because the
            # caller's exception does not say the destination was left at `side`.
            try:
                os.replace(side, original)
            except OSError:
                logger.error(
                    "restore rollback FAILED to put %s back from %s — the "
                    "destination was NOT restored; the original bytes survive "
                    "at that aside path", original, side, exc_info=True)
        raise
    else:
        for _original, side in staged:
            _discard(side)


def backup(db_path: str, events_path: str = "events.jsonl",
           target_dir: str | None = None) -> Path:
    """Copy database + event log to a timestamped backup directory.

    Returns the backup directory path.
    """
    if target_dir is None:
        target_dir = f"backups/{_timestamp()}"

    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)

    # Copy FalkorDB
    db = Path(db_path)
    if db.exists():
        shutil.copy2(db, target / db.name)

    # Copy event log
    ev = Path(events_path)
    if ev.exists():
        shutil.copy2(ev, target / ev.name)

    # Trigger FalkorDB BGSAVE if available. Best-effort — a snapshot failure
    # never aborts the file backup — but the outcome is logged AND recorded in
    # the manifest so a silent no-op is impossible (#2974). If the CLI target
    # is itself a URI, snapshot THAT instance; otherwise the configured
    # TORTOISE_DB_URI is used (never a hardcoded localhost).
    bgsave = _bgsave(db_path if is_db_uri(db_path) else None)

    # Write manifest
    manifest = target / "manifest.json"
    import json
    manifest.write_text(json.dumps({
        "backed_up_at": _timestamp(),
        "db": db.name,
        "events": ev.name,
        "bgsave": bgsave,
    }, indent=2))

    return target


@tolerates_altered_numbers
def restore(backup_dir: str, db_path: str,
            events_path: str = "events.jsonl", into_falkor: bool = False) -> dict:
    """Restore from backup directory. Replays events into a fresh projection.

    Returns {events, status}.

    #7174/#5011: this is a whole-journal REPLAY engine (the fourth, beside
    ``rebuild``/``rebuild_all``/``recover_from_log``), and its JSONL fallback
    calls ``proj.apply_journal_point_restamp`` — which writes the journaled
    ``valid_to`` VERBATIM as a param. A record whose number the store cannot hold
    must DIVERGE for ``check_consistency``, never crash the restore, so the
    numeric-domain refusal is suspended here like the other three engines.

    Event-sourcing contract (#114): when the backup contains a FalkorDB
    snapshot (tortoise.db — BGSAVE RDB), into_falkor mode opens that
    snapshot directly. The RDB is the complete graph state INCLUDING
    SDK-created points (which never appear in events.jsonl, since the SDK
    writes via Cypher). Replaying only events.jsonl would silently drop
    every SDK-created point. RDB-first restore preserves the full graph;
    JSONL replay is the fallback when no snapshot exists.
    """
    source = Path(backup_dir)
    if not source.exists():
        return {"events": 0, "status": "error: backup dir not found"}

    manifest_file = source / "manifest.json"
    events_file = source / "events.jsonl"
    # Read DB filename from manifest (issue #176, plan Task 11): pre-migration
    # backups may have embedded.db in their manifest — never hardcode.
    import json as _json
    _db_name = "tortoise.db"
    if manifest_file.exists():
        try:  # noqa: SIM105
            _db_name = _json.loads(manifest_file.read_text()).get("db", "tortoise.db")
        except Exception:
            pass
    db_file = source / _db_name
    # Graceful fallback for pre-migration backups that lack the manifest 'db'
    # key (embedded.db was the legacy filename). Also try scanning for any
    # *.db file if the manifest-derived name doesn't exist.
    if not db_file.exists():
        fallback = source / "embedded.db"
        if fallback.exists():
            logger.warning(
                "manifest db %r not found — falling back to embedded.db in %s",
                _db_name, source)
            db_file = fallback
            _db_name = "embedded.db"
        else:
            # Last resort: scan for any *.db file in the backup dir
            db_candidates = sorted(source.glob("*.db"))
            if db_candidates:
                logger.warning(
                    "manifest db %r not found, embedded.db not found — "
                    "falling back to %s", _db_name, db_candidates[0].name)
                db_file = db_candidates[0]
                _db_name = db_file.name

    if not events_file.exists():
        return {"events": 0, "status": "error: no events.jsonl in backup"}

    # #3316: the refusal guards the REPLAY, so it applies ONLY to an
    # invocation that can replay (``into_falkor=True``). The default
    # ``into_falkor=False`` path replays NOTHING — it only copies the backup
    # files — so it cannot resurrect removed state and must still copy a
    # crash-backup. (The CLI's ``tortoise restore`` uses that default; see the
    # matching note at ``tortoise/__main__.py``.)
    #
    # It is taken HERE rather than inside the JSONL fallback below because the
    # fallback runs AFTER the block that REPLACES the destination store: a
    # refusal there would leave the caller's DB emptied and its AOF removed
    # (``remove_stale_aof`` rmtree's it) while claiming the graph was left
    # alone. So the verdict is bound to the replay-capable invocation, before
    # the destructive copy. The RDB path is deliberately NOT consulted first —
    # consulting it needs the snapshot copied over the destination, which is
    # the very mutation being guarded. The cost is bounded: a torn DESTRUCTIVE
    # tail also refuses a ``into_falkor=True`` restore whose snapshot could
    # have carried the graph, and the remedy (repair or truncate the journal in
    # the backup) is the one every other engine names.
    _source_records: list[dict] | None = None
    if into_falkor:
        from tortoise.log import EventLog, refuse_torn_tail_revival
        _source_log = EventLog(events_file)
        try:
            _source_records = _source_log.read_all()
            refuse_torn_tail_revival(_source_log.torn_tail_revival_records())
        except ValueError:
            # Mid-file corruption is NOT this refusal (it is a parse error, not
            # a torn tail) and the RDB path does not read the journal at all:
            # let the JSONL fallback below raise it, as it did before #3316.
            _source_records = None

    # #7767: everything below REPLACES the destination — the journal always,
    # and (when the backup carries a snapshot) the DB and its AOF dir — while
    # the replay that can REFUSE runs LAST, so a refusal used to land on a
    # store that had already been destroyed, with no rollback. Each
    # destination is renamed aside first and put back verbatim if anything
    # below raises; see `_destination_rollback`.
    with _destination_rollback(
            events_path, db_path, replace_db=db_file.exists()):
        # Copy files to target
        shutil.copy2(events_file, events_path)
        if db_file.exists():
            # #915 — with AOF enabled, Redis loads the AOF in preference to the
            # RDB. A stale appendonlydir/ at the target path would make this
            # restore silently serve the OLD live graph instead of the snapshot.
            # Restore semantics = "the restored snapshot wins". (The stale dirs
            # were already staged aside by `_destination_rollback`, so this is
            # a no-op today — kept because it is the #915 wiring, and because a
            # caller of `remove_stale_aof` must never be able to see the old
            # AOF survive a completed restore.)
            from tortoise.projection import remove_stale_aof
            remove_stale_aof(db_path)
            shutil.copy2(db_file, db_path)

        # Count events: records AND torn fragments, which is the number this
        # surface has always reported (`test_backup_restore_default_copies_a_torn_
        # tail_backup` pins a torn journal at 2) — minus the seal annotations
        # `append` writes, which annotate a fragment rather than being one (#5917).
        # Counted WITHOUT parsing: the reader-derived count parsed every record a
        # second time (twice on the ``into_falkor`` path, which already parsed the
        # journal above) to produce a number this surface only reports — measured
        # 20.4 s vs 0.07 s on a 300,000-record journal.
        count = _count_journal_events(events_file)

        # Restore into FalkorDB if requested
        if into_falkor:
            from tortoise.projection import (  # noqa: I001
                FalkorProjection,
                _object_hard_deleted_ids,
                hard_deleted_pairs,
                journal_first_materialization,
                journal_hard_delete_seqs,
                journal_object_surviving_keys,
                plan_point_restamp_folds,
            )
            from tortoise.projection.nonfolded import (
                assert_no_non_folded,
                collect_non_folded,
            )
            from tortoise.log import EventLog
            # RDB-first: open the snapshot directly — it holds the full graph
            # incl. SDK-created points that never made it into events.jsonl.
            if db_file.exists():
                proj = FalkorProjection(db_path)
                try:
                    # Verify the snapshot actually has data; if the RDB is a
                    # stub/empty, fall through to JSONL replay below.
                    rows = proj.g.query("MATCH (n) RETURN count(n)").result_set
                    if rows and rows[0][0]:
                        return {"events": count, "status": "ok", "restored_via": "rdb"}
                finally:
                    proj.close()
            # JSONL replay fallback (no RDB, or RDB was empty)
            proj = FalkorProjection(db_path)
            try:
                # #3664: ``apply()`` is a one-record API, so an ``EntityLinked``
                # whose endpoint is created LATER in the journal folds to nothing
                # inline. This engine buffers the records and folds them AFTER the
                # pass, the same trailing sweep ``rebuild`` / ``rebuild_all`` /
                # ``recover_from_log`` give the type. The records carry their
                # journal seq so the sweep can suppress a link whose endpoint was
                # HARD-DELETED afterwards (#3722 review P2). A fold failure is
                # logged, never raised: restore must not abort on one unreplayable
                # link.
                # #3316: the torn-tail verdict was already taken on the SOURCE
                # journal above, BEFORE this engine replaced the destination store
                # — the refusal cannot be re-taken here, after the mutation, and
                # the copy made above is byte-identical to the source. ``None``
                # means the source parse hit mid-file corruption; re-reading the
                # copy raises the same actionable error from the same reader.
                log = EventLog(events_path)
                records = (_source_records if _source_records is not None
                           else log.read_all())
                hard_delete_seqs = journal_hard_delete_seqs(records)
                # #7719: the anchor-gated hard-delete MEMBERSHIP map, hoisted ONCE
                # over the SAME ``records`` iterable the replay loop walks.
                hard_deleted = hard_deleted_pairs(records)
                deferred_links: list[tuple[int, dict]] = []
                # #3305: the Point lifecycle terminalizers fold through the SHARED
                # whole-journal plan (the same selection ``rebuild_all`` uses),
                # not through ``apply()``'s inline branch — that branch folds every
                # terminalizer, including the pre-recreation ones ``rebuild_all``
                # drops and the non-canonical supersedes it collapses.
                restamp_plan, _ = plan_point_restamp_folds(records)
                # #3305: their CORRECTS edges name a SUCCESSOR this pass may create
                # later, so defer the edges and re-apply them after the pass (the
                # inline MERGE no-ops for a forward reference, while
                # ``rebuild_all``'s after-creations sweep resolves it).
                deferred_corrects: list[tuple[int, str, str]] = []
                # #5285 cycle-3 (FIX 3): this is the FOURTH whole-journal replay
                # engine, and it replayed one record at a time with NO journal
                # context — so `apply()`'s ObjectSuperseded refusal gate (keyed on
                # `journal_object_surviving is not None`) was SKIPPED and an
                # id-only absent-target supersede restored silently, while
                # `rebuild_all` refuses the identical journal. Supply the same
                # whole-journal keys `rebuild`/`recover_from_log` supply, and
                # feature-detect the kwargs (an `apply(ev)`-only injected backend
                # must not be handed a kwarg it does not accept).
                journal_object_surviving = journal_object_surviving_keys(records)
                # #7719: derived from the GATED map via `_hard_deleted_any`, so an
                # anchor-suppressed delete no longer exempts the supersede.
                journal_object_deleted = _object_hard_deleted_ids(hard_deleted)
                # #3585 (P1-1): the whole-journal EXISTENCE map, so a
                # retract/state-op that precedes its own creation (folded by
                # `rebuild_all`'s hoist) is not refused on this chronological path.
                first_materialized = journal_first_materialization(records)
                apply_kwargs: dict = {}
                _pass_seq = False
                try:
                    _apply_params = inspect.signature(proj.apply).parameters
                except (TypeError, ValueError):
                    _apply_params = {}
                if "journal_object_surviving" in _apply_params:
                    apply_kwargs["journal_object_surviving"] = journal_object_surviving
                    apply_kwargs["journal_object_deleted"] = journal_object_deleted
                if "journal_first_materialized" in _apply_params:
                    apply_kwargs["journal_first_materialized"] = first_materialized
                    _pass_seq = "journal_seq" in _apply_params
                # The refusal is a RUN BOUNDARY, exactly as on the other three
                # engines: without the collector `record_non_folded` is a no-op
                # and the context would change nothing. `assert_no_non_folded`
                # raises `NonFoldedEventsError` AFTER the close, so a miss fails
                # the restore loudly instead of returning `{"status": "ok"}`.
                with collect_non_folded() as _nf_entries:
                    for seq, ev in enumerate(records):
                        if isinstance(ev, dict) and ev.get("type") == "EntityLinked":
                            deferred_links.append((seq, ev))
                            continue
                        # Keyed on the PLAN, not the raw envelope type — the plan
                        # selects by the NORMALIZED type (``_norm`` splices a nested
                        # payload), so a raw-type guard would let a ``type``-in-``point``
                        # terminalizer fall through to ``apply()``'s inline branch and
                        # its unshared selection (#325/#3722's raw-vs-normalized class).
                        if seq in restamp_plan:
                            edge = proj.apply_journal_point_restamp(
                                ev, seq, restamp_plan, hard_deleted=hard_deleted)
                            if edge is not None:
                                deferred_corrects.append(edge)
                            continue
                        if _pass_seq:
                            proj.apply(ev, journal_seq=seq, **apply_kwargs)
                        else:
                            proj.apply(ev, **apply_kwargs)
                    if deferred_corrects:
                        try:
                            proj.fold_deferred_corrects_edges(
                                deferred_corrects, hard_delete_seqs)
                        except Exception:
                            logger.exception(
                                "restore: deferred CORRECTS fold failed; %d "
                                "edge(s) not replayed", len(deferred_corrects))
                    if deferred_links:
                        try:
                            proj.fold_deferred_entity_links(
                                deferred_links, hard_delete_seqs)
                        except Exception:
                            logger.exception(
                                "restore: deferred EntityLinked fold failed; %d "
                                "link(s) not replayed", len(deferred_links))
                assert_no_non_folded(_nf_entries, engine="restore")
            finally:
                proj.close()

        return {"events": count, "status": "ok"}


def _bgsave(uri: str | None = None) -> str:
    """Trigger a FalkorDB BGSAVE on the CONFIGURED database (#2974).

    The endpoint is resolved through the same canonical resolver the product
    uses — ``TORTOISE_DB_URI`` parsed by ``tortoise.projection.
    resolve_db_endpoint`` (the derivation ``FalkorProjection.from_uri`` also
    uses). The previous implementation dialed a hardcoded
    ``localhost:16379`` from the embedded-mode ``FALKORDB_HOST``/``FALKORDB_
    PORT`` defaults, which hosted production never sets, so the BGSAVE never
    reached the real instance and the swallowed exception hid it.

    Best-effort semantics are preserved — a snapshot failure must NOT abort
    the file backup — but it is never silent (#2820): every failure is logged
    at ERROR and returned so ``backup()`` records it in the manifest.

    Args:
        uri: explicit database URI (the CLI ``--db`` target when it is a
            ``docker://``/``redis://``/``rediss://`` URI). When None, the
            configured ``TORTOISE_DB_URI`` is used.

    Returns:
        ``"ok"``, ``"skipped: <why>"`` (embedded — no server endpoint to
        snapshot), or ``"failed: <why>"``.
    """
    target = (uri or os.environ.get("TORTOISE_DB_URI", "") or "").strip()
    if not target:
        # Embedded mode: redislite owns the on-disk RDB and the file copy in
        # backup() is the durable artifact — nothing to BGSAVE. Not an error.
        logger.info(
            "BGSAVE skipped — no server URI configured (embedded mode); "
            "the copied DB file is the durable artifact")
        return "skipped: no server URI configured (embedded mode)"
    if "://" in target and not is_db_uri(target):
        # A URI was configured but its scheme is unsupported — the endpoint is
        # UNRESOLVABLE. This is a misconfiguration, never a silent skip.
        scheme = target.split("://", 1)[0]
        reason = (
            f"unsupported DB URI scheme {scheme!r} (expected docker://, "
            f"redis://, or rediss://) — snapshot endpoint unresolvable")
        logger.error("BGSAVE failed — %s", reason)
        return f"failed: {reason}"
    if not is_db_uri(target):
        # TORTOISE_DB_URI is a file path (backward-compat embedded mode).
        logger.info("BGSAVE skipped — configured DB is an embedded file path")
        return "skipped: embedded DB path"
    try:
        from tortoise.projection import resolve_db_endpoint
        endpoint = resolve_db_endpoint(target)
    except Exception as e:
        reason = f"could not resolve the DB endpoint: {e}"
        logger.error("BGSAVE failed — %s", reason, exc_info=True)
        return f"failed: {reason}"
    try:
        from falkordb import FalkorDB

        from tortoise.cypher_guard import guarded_client  # #3595: guard seam
        db = guarded_client(FalkorDB, host=endpoint.host, port=endpoint.port,
                            username=endpoint.username, password=endpoint.password,
                            ssl=endpoint.ssl,
                            socket_connect_timeout=5, socket_timeout=10)
        db.connection.execute_command("BGSAVE")
    except Exception as e:
        reason = f"BGSAVE against {endpoint.host}:{endpoint.port} failed: {e}"
        logger.error("%s", reason, exc_info=True)
        return f"failed: {reason}"
    logger.info("BGSAVE triggered at %s:%s", endpoint.host, endpoint.port)
    return "ok"
