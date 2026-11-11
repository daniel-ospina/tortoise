"""tortoise migrate-db — data-safe migration of legacy embedded.db to the
canonical path (plan Task 11, issue #176).

Safety properties:
  1. Backup-first: embedded.db is copied to .bak-<timestamp> before any
     rebuild; backup failure aborts with source intact.
  2. Advisory lock (~/.tortoise/.migrate.lock): concurrent runs serialize;
     exactly one wins, others skip with message.
  3. 3-way discriminator for tortoise.db present + no marker:
       - 0 nodes / corrupt            -> interrupted -> delete + rebuild
       - 0 < count < source events    -> partial -> delete + rebuild (no --force)
       - count >= source AND valid    -> genuine conflict -> error / --force
  4. Marker (.migrated-v2) written AFTER successful rebuild + integrity
     verification (never before — marker-before-rebuild blocks recovery).
  5. Never binary-copies the .db (JSONL rebuild via rebuild_all; a binary
     copy could silently corrupt across versions).
"""
from __future__ import annotations

import fcntl
import logging
import os
import shutil
import sys
import time

logger = logging.getLogger(__name__)

MARKER = ".migrated-v2"
LOCK = ".migrate.lock"
BACKUP_PREFIX = "embedded.db.bak-"
DEFAULT_EMBEDDED = os.path.join(os.path.expanduser("~"), ".tortoise", "embedded.db")


class MigrateLock:
    """fcntl advisory lock; auto-released on process exit (incl. SIGKILL)."""

    def __init__(self, path: str):
        self.path = path
        self._fh = None

    def acquire(self) -> bool:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._fh = open(self.path, "a")  # noqa: SIM115
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fh.seek(0)
            self._fh.truncate()
            self._fh.write(str(os.getpid()))
            self._fh.flush()
            return True
        except OSError:
            self._fh.close()
            self._fh = None
            return False

    def release(self) -> None:
        if self._fh:
            try:  # noqa: SIM105
                fcntl.flock(self._fh, fcntl.LOCK_UN)
            except OSError:
                pass
            self._fh.close()
            self._fh = None


def _tortoise_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".tortoise")


def _count_nodes(db_path: str) -> int | None:
    """Open a FalkorDB db and count nodes; None on failure (corrupt/absent)."""
    try:
        from tortoise.projection import FalkorProjection
        proj = FalkorProjection(db_path, allow_nonstandard_path=True)
        try:
            rows = proj.g.query("MATCH (n) RETURN count(n)").result_set
            return int(rows[0][0]) if rows and rows[0][0] else 0
        finally:
            proj.close()
    except Exception:
        return None


def _count_source_events(source_db: str) -> int:
    """Count events derivable from the source embedded.db (via its JSONL if
    present, else its node count). Returns node count as the discriminator."""
    count = _count_nodes(source_db)
    return count if count is not None else 0


def _pack_selection_shape(counts: dict) -> tuple[str, str]:
    """Delegate to the ONE shape answer (`tortoise.projection`).

    THREE of the four reporters route through it: the two projection recovery
    callers and this migration route (both its warning sites). The fourth — the
    `rebuild` CLI — keeps its own branch chain deliberately, because it prints a
    count line and per-shape prose whose exact wording is pinned by
    `test_cmd_rebuild_reports_the_pack_selection_marker`; a second copy of the
    RULE here is what let the reporters drift apart across review rounds 3-4,
    whereas the CLI's copy is behaviour-pinned and states no rule the helper
    contradicts.
    """
    from tortoise.projection import pack_selection_shape

    return pack_selection_shape(counts or {})


def migrate(force: bool = False) -> dict:
    """Run the migration; returns result dict. Raises on abort conditions."""
    tortoise_dir = _tortoise_dir()
    os.makedirs(tortoise_dir, exist_ok=True)
    source = os.path.join(tortoise_dir, "embedded.db")
    target = os.path.join(tortoise_dir, "tortoise.db")
    marker = os.path.join(tortoise_dir, MARKER)
    lock_path = os.path.join(tortoise_dir, LOCK)

    # No source -> no-op (FileNotFoundError)
    if not os.path.exists(source):
        return {"status": "noop", "reason": "no embedded.db"}

    # Marker present -> already migrated; verify DB not corrupt first
    if os.path.exists(marker) and not force:
        node_count = _count_nodes(target)
        if node_count is None:
            logger.warning("marker present but tortoise.db corrupt — re-migrating")
            os.remove(marker)
            if os.path.exists(target):
                os.remove(target)
        else:
            return {"status": "already-migrated", "nodes": node_count}

    # Backup first (P0): never proceed without the restore point
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_path = os.path.join(tortoise_dir, f"{BACKUP_PREFIX}{timestamp}")
    try:
        shutil.copy2(source, backup_path)
    except OSError as e:
        raise RuntimeError(
            f"backup failed: {e}. Migration aborted. Source DB intact."
        ) from e

    # Advisory lock under which the conflict check runs (P0: concurrent
    # --force migrators must not both rename tortoise.db)
    lock = MigrateLock(lock_path)
    if not lock.acquire():
        return {"status": "skipped", "reason": "another migration in progress"}
    try:
        if os.path.exists(target):
            target_nodes = _count_nodes(target)
            source_events = _count_source_events(source)
            if target_nodes is not None and target_nodes >= source_events:
                # Genuine conflict: valid target with >= source events
                if not force:
                    raise RuntimeError(
                        f"Both embedded.db and tortoise.db exist without "
                        f"migration marker (target has {target_nodes} nodes, "
                        f"source {source_events}). Resolve manually or use --force."
                    )
                # --force: rename existing target to .bak-conflict-<ts>
                conflict_bak = os.path.join(
                    tortoise_dir, f"tortoise.db.bak-conflict-{timestamp}")
                try:
                    os.rename(target, conflict_bak)
                except OSError as e:
                    raise RuntimeError(
                        f"rename failed: {e}. Migration aborted. Resolve manually."
                    ) from e
                logger.warning("moved existing tortoise.db to %s", conflict_bak)
            else:
                # 0-node/corrupt OR partial (< source): interrupted migration
                # -> delete partial + rebuild cleanly (no --force needed)
                logger.warning(
                    "tortoise.db appears interrupted (%s nodes, source %s) — "
                    "rebuilding cleanly", target_nodes, source_events)
                if os.path.exists(target):
                    os.remove(target)

        # Rebuild from JSONL — but we don't have the source's event log dir
        # here (embedded.db is a binary RDB). Per plan: rebuild via
        # rebuild_all from the canonical JSONL log directory. Locate it:
        events_dir = os.path.join(os.path.dirname(source), "events")
        if not os.path.isdir(events_dir):
            events_dir = _find_events_dir()
        # #5612: bound only on the JSONL rebuild route below (the RDB-copy
        # fallback replays nothing, so it has no salvage tally). Bound here so
        # the returned counts can carry it on EVERY route without a NameError.
        rebuild_counts: dict | None = None
        if not events_dir:
            # No JSONL available: copy the RDB snapshot (documented fallback —
            # NOT the binary-copy path for the primary flow; this is the
            # recovery path when no event log exists).
            logger.warning(
                "no JSONL event log found — falling back to RDB snapshot copy "
                "(best-effort recovery)")
            # #915 — remove any stale AOF at the target: with AOF enabled,
            # Redis loads the AOF in preference to the RDB, so a stale
            # appendonlydir/ would shadow the copied snapshot.
            from tortoise.projection import remove_stale_aof
            remove_stale_aof(target)
            shutil.copy2(backup_path, target)
        else:
            from tortoise.projection import remove_stale_aof, FalkorProjection  # noqa: I001
            remove_stale_aof(target)
            proj = FalkorProjection(target, allow_nonstandard_path=True)
            try:
                # #2944 L1: migration intentionally wipes the (fresh/partial)
                # embedded target and replays from the source's JSONL — an
                # explicit, deliberate destructive step, so the token is
                # passed at this entry point.
                rebuild_counts = proj.rebuild_all(events_dir, confirm_destructive=True)
            finally:
                proj.close()
            # ── #9752: this route's preservation report describes an EMPTY graph ──
            # `rebuild_all` captures each preservation section (config rows,
            # `:TeamMeta`, `:PackSelection`) from the graph it is about to wipe —
            # i.e. from the FRESH `target` created nine lines above, NOT from
            # `backup_path`, the legacy graph the journal came from. On this route
            # the capture therefore sees an empty graph BY CONSTRUCTION: no marker
            # is captured, nothing is restored, the post-restore comparison never
            # runs (`pack_selection_marker is None` skips it), and the rebuild
            # returns `pack_selection_gap: 0` with `pack_selection_verified:
            # True`. Those are facts about the empty target, not about the graph a
            # user migrates.
            #
            # Reporting them as a successful verification is the false PASS this
            # class exists to stop — measured on a private lane: a source graph
            # carrying `configured=True revision=2` with an EMPTY approved set
            # migrates to `configured=False revision=0` (the catalog union, so the
            # pack an admin REMOVED is re-admitted) while this route reported
            # `pack_selection_gap: 0, pack_selection_verified: True` and the CLI
            # printed nothing. Also measured on the same run: `:PackInstall`
            # returns `[]` — the same capture-source defect, on the same route,
            # for the classes `_config_classes()` owns (tracked as #11866).
            #
            # So this route reports UNKNOWN: the marker is not confirmed intact,
            # and NOT observed gone. Named rather than silent, the disposition the
            # RECORD-sourced `rebuild_from_record` already takes for this class
            # (`tortoise/cache_projection.py`, `06-cache-projection.md` §0.5).
            # CARRYING it needs a capture source that is not `self.g`; that is
            # #11866, not this fix.
            #
            # The condition keeps a STRONGER report from the rebuild itself: a
            # leftover pre-wipe sidecar beside the SOURCE's event log (a
            # previous partial migration from the same log dir) can say
            # `state_unknown` on its own, and that answer is not overridden —
            # it is the same verdict. The sidecar is keyed to `log_dir`, never
            # to the DB's directory: `prewipe_snapshot_path(log_dir)`
            # (`projection/__init__.py:2656`, read at `:14524`) is handed the
            # SOURCE's `events_dir` below (`:211`), and the rebuild warns
            # explicitly when the two directories differ (`:14514-14519`).
            #
            # `not pack_selection_expected` makes this the EXACT empty-capture
            # signature, so the override retires itself: the day #11866 gives
            # this route a capture source that is not `self.g`, `expected`
            # becomes non-zero and a genuinely verified carry is reported as one,
            # instead of being relabelled UNKNOWN by a rule that only ever
            # described an empty target.
            #
            # The `verified = False` is NOT conditional, because an empty capture
            # means nothing was verified — whatever flag the rebuild returned for
            # it. The one shape where that matters on its own is a pre-v7 leftover
            # beside the SOURCE's event log: the rebuild then already reports
            # `state_unknown: True, gap: 1`, so the block below is skipped, but
            # leaving its `verified: True` in place would still tell a caller that
            # reads that field (rather than `gap`) that the migrated graph's marker
            # was confirmed — the same false PASS, one field over.
            #
            # `pack_selection_state_unknown` has TWO producers, and the warning
            # below must not name one cause for both. (a) THIS route's own
            # override, a few lines down, when the fresh `target` captured
            # nothing — `rebuild_all` logs no ERROR for that shape. (b) a
            # leftover rescue file beside the SOURCE's event log that cannot say
            # (pre-v7, or no `pack_selection` section) — `rebuild_all` DOES log
            # an ERROR naming that file. Snapshot the flag BEFORE the override so
            # the warning can still tell the two apart.
            _pack_unknown_inherited = bool(
                rebuild_counts.get("pack_selection_state_unknown"))
            if not rebuild_counts.get("pack_selection_expected"):
                rebuild_counts = dict(rebuild_counts)
                rebuild_counts["pack_selection_verified"] = False
                if not (
                    rebuild_counts.get("pack_selection_gap")
                    or rebuild_counts.get("pack_selection_state_unknown")
                    or rebuild_counts.get("pack_selection_conflict")
                ):
                    rebuild_counts["pack_selection_state_unknown"] = True
                    rebuild_counts["pack_selection_gap"] = 1
            # #4641: the JSONL rebuild path is one of the three routes that
            # performs the unconditional wipe, so a completed rebuild can
            # still leave the migrated graph's onboarding state NOT confirmed
            # intact — an onboarding restore gap (those writes ride no journal
            # record), an unverified restore, or a state-UNKNOWN rescue file.
            # Reporting `migrated` while swallowing it is the silent partial
            # loss the class exists to stop, so name it here as the other
            # callers (`consistency.recover_from_log`, the CLI) now do. This
            # route carries no `onboarding_gap`/`team_meta_gap` field of its own
            # (its result dict reports only the pack-selection class and
            # `corrupt_lines`), so the warning can only POINT at the rebuild
            # ERROR lines, which carry the shape; it must not describe all three
            # as a loss the replay could not close.
            if rebuild_counts.get("onboarding_gap"):
                logger.warning(
                    "rebuild: the migrated graph's onboarding state is NOT "
                    "confirmed intact — re-run onboarding for the affected "
                    "org(s) and see the rebuild ERROR log (#4641)")
            # #5353: the sibling of the line above, and it is here for the same
            # reason — a `:TeamMeta` marker that is not confirmed intact makes
            # the `#2789` first-org guard read `count(m) == 0`, so a later
            # provisioning call can re-mint it with the control plane's name.
            # The collapse count alone would not distinguish that from a graph
            # that never held a marker; only the gap/UNKNOWN pair does.
            if rebuild_counts.get("team_meta_gap"):
                logger.warning(
                    "rebuild: the migrated graph's :TeamMeta first-org marker "
                    "is NOT confirmed intact (%s) — the #2789 first-org guard "
                    "may read 0 and a later provisioning call can re-mint it "
                    "with the control plane's name; see the rebuild ERROR log "
                    "(#5353)",
                    "pre-preservation/UNKNOWN"
                    if rebuild_counts.get("team_meta_state_unknown")
                    else "restore gap")
            # #9752: the third sibling of the two lines above, and it is here
            # for the same reason — `:PackSelection` is written with a bare
            # `MERGE` and rides no journal record, so a rebuild only confirms
            # it by restoring it from the pre-wipe sidecar. When that does not
            # happen the migrated graph reads as UNCONFIGURED —
            # `resolved_pack_set` falls back to the records/union arms — so a
            # pack an admin REMOVED is re-admitted with its `revision` reset,
            # the harm #9752 exists to stop. The collapse count alone would not
            # distinguish that from a graph that was never configured; only
            # the gap/UNKNOWN pair does.
            if rebuild_counts.get("pack_selection_gap"):
                _label, _consequence = _pack_selection_shape(rebuild_counts)
                if (rebuild_counts.get("pack_selection_state_unknown")
                        and not _pack_unknown_inherited):
                    # #11866 — shape (a) ONLY: the cause is the CAPTURE SOURCE,
                    # not a stale sidecar. `rebuild_all` captures each
                    # preservation section from the graph it is about to wipe,
                    # which here is the fresh `target`, so the capture is empty
                    # by construction. Nothing was expected, nothing was
                    # restored and no verification failed — so the rebuild
                    # logged no ERROR at all, and the tail this route used to
                    # print ("see the rebuild ERROR log") sent the operator to
                    # an empty line while naming the sidecar as the suspect. An
                    # operator who goes looking for a stale rescue file beside
                    # the target finds nothing, because that is not the failure
                    # and not the fix.
                    logger.warning(
                        "rebuild: the migrated graph's :PackSelection pack-set "
                        "marker is NOT confirmed intact (%s) — %s. This route "
                        "captures from the graph it is about to wipe (the fresh "
                        "`target`), so the marker was NOT OBSERVED, not "
                        "observed gone (#9752/#11866)", _label, _consequence)
                else:
                    logger.warning(
                        "rebuild: the migrated graph's :PackSelection pack-set "
                        "marker is NOT confirmed intact (%s) — %s; see the "
                        "rebuild ERROR log (#9752)", _label, _consequence)
            # #5612: the journal read is a SALVAGE read, so a damaged mid-file
            # line no longer raises out of the rebuild — it is skipped and
            # counted. This route then writes the migration MARKER, i.e. it is
            # the most terminal "looks fine" surface there is: without this
            # line a lossy migration charges itself as complete and no later
            # open re-tries it. Before #5612 the strict read raised here.
            if rebuild_counts.get("corrupt_lines"):
                logger.warning(
                    "rebuild: the migrated graph is MISSING records — %s "
                    "corrupt mid-file journal line(s) were SKIPPED, so the "
                    "journal needs repair and this migration marker is "
                    "already written (#5612)",
                    rebuild_counts["corrupt_lines"])

        # Integrity verification (node-ID-set equality vs source) before marker
        target_nodes = _count_nodes(target)
        if target_nodes is None:
            raise RuntimeError("rebuild produced corrupt/unreadable tortoise.db")
        source_nodes = _count_nodes(backup_path) or 0
        if source_nodes and target_nodes < source_nodes:
            logger.warning(
                "integrity: target has %s nodes, source %s — mismatch",
                target_nodes, source_nodes)

        # Marker AFTER successful rebuild + verify
        with open(marker, "w") as fh:
            fh.write(time.strftime("%Y-%m-%dT%H:%M:%SZ"))
        return {"status": "migrated", "nodes": target_nodes,
                "backup": backup_path,
                # #5612: additive — 0 on a clean journal; a non-zero
                # count means the migrated graph is missing records the
                # journal held, which a caller must not read as a lossless
                # migration (the rebuild warns; this carries the number).
                "corrupt_lines": int((rebuild_counts or {}).get(
                    "corrupt_lines") or 0),
                # #9752: additive, same shape as `corrupt_lines` — a non-zero
                # count means the migrated graph's pack-set marker is NOT
                # confirmed intact, which a caller must not read as a clean
                # migration (the rebuild warns; this carries the number).
                "pack_selection_gap": int((rebuild_counts or {}).get(
                    "pack_selection_gap") or 0),
                # The shape discriminator, so a surface printing the count can
                # say UNKNOWN instead of asserting a loss nobody observed
                # (a pre-v7 rescue file cannot say whether the marker was
                # destroyed).
                "pack_selection_state_unknown": bool(
                    (rebuild_counts or {}).get(
                        "pack_selection_state_unknown")),
                # The other two shape discriminators, carried for the same
                # reason: without them a caller cannot tell a CONFLICT (the
                # graph IS configured, possibly with a newer set) or an
                # UNVERIFIED read from an observed loss — it would print the
                # absence remedy over a graph that holds a decision.
                "pack_selection_verified": (
                    (rebuild_counts or {}).get("pack_selection_verified")),
                "pack_selection_conflict": bool(
                    (rebuild_counts or {}).get("pack_selection_conflict"))}
    finally:
        lock.release()


def _find_events_dir() -> str | None:
    """Locate the canonical JSONL event-log directory (heuristic)."""
    home = os.path.expanduser("~")
    for candidate in (
        os.path.join(home, "events"),
        os.path.join(home, ".tortoise", "events"),
        os.path.join(home, ".tortoise", "logs"),
        os.path.join(home, ".tortoise", "memory", "events"),
    ):
        if os.path.isdir(candidate) and any(
            f.endswith(".jsonl") for f in os.listdir(candidate)):
            return candidate
    return None


def main(argv: list[str] | None = None) -> int:
    import argparse
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="tortoise migrate-db")
    p.add_argument("--force", action="store_true",
                   help="Bypass marker / overwrite conflicting tortoise.db")
    args = p.parse_args(argv)
    try:
        result = migrate(force=args.force)
        if result.get("status") == "migrated":
            logger.warning("migrated embedded.db -> tortoise.db (%s nodes)",
                           result.get("nodes"))
            # #9752: without this line a migration whose pack-set marker was
            # not carried back prints a clean "migrated" and says nothing,
            # while the graph now reads as UNCONFIGURED and a pack an admin
            # REMOVED can be re-admitted with its `revision` reset. Printed
            # with the UNKNOWN/gap discrimination for the same reason the
            # `rebuild` CLI prints the shapes separately: "could not confirm"
            # must never be reported as an observed loss.
            if result.get("pack_selection_gap"):
                _label, _consequence = _pack_selection_shape(result)
                logger.warning(
                    "migrated graph's :PackSelection pack-set marker is NOT "
                    "confirmed intact (%s) — %s (#9752).",
                    _label, _consequence)
        elif result.get("status") == "already-migrated":
            logger.warning("already migrated (marker present, %s nodes) — skip",
                           result.get("nodes"))
        elif result.get("status") == "noop":
            logger.warning("no embedded.db found — nothing to migrate")
        elif result.get("status") == "skipped":
            logger.warning("skipped: %s", result.get("reason"))
        return 0
    except RuntimeError as e:
        logger.error("%s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
