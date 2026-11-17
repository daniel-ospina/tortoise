#!/usr/bin/env python3
"""#12855 — rewrite the stored `outdated` / `retracted` Object status words to
`deprecated`, ONCE, through the SDK's own journaled update path.

    uv run python tools/rewrite_object_status.py                       # preview (default)
    uv run python tools/rewrite_object_status.py --execute             # do it
    uv run python tools/rewrite_object_status.py --execute --json

Owner decision, on #12855 (2026-11-16): *"Rewrite the stored rows once."* That
authorises dropping `outdated` from `assembly._RECALL_OBJECT_EXCLUDED_STATUSES`
(the symbol, not a line number — the pointer to it had already drifted from
`4383` to `4422` on this head, and a stale pointer is worse than none) — and the
drop is only safe AFTER this tool has run,
because the member is load-bearing for rows that already carry the word: the
successor-existence probe treats an excluded successor as invisible
(NAME-ONLY annotation), so removing the member flips such a row to a **verified
successor link**. The member stays until no stored row needs it; this tool is
what makes "no stored row needs it" true on a given graph.

WHY THIS IS A MIGRATION AND NOT A TIDYING
========================================
`rebuild_all` reconstructs the graph from the event stream, so a bare
`SET n.status = 'deprecated'` is **reverted by the next rebuild** — the creation
record still carries `outdated`, and the fold re-derives it. The durable path
already exists and is the one this tool uses:

    sdk.update_knowledge(id, status='deprecated')
      → `_update_entity_any` → `_update_entity`
      → non-Point arm journals `EntityMutated{op: restatus, state: {status}}

and the replay fold applies that `state` with the same `SET n += $s` the live
write ran. So a rewrite executed **row by row through the SDK** is a durable
rewrite: the journal holds it, a rebuild reproduces it, and nothing new has to
be invented. This tool therefore never issues a raw `SET` for the status word —
a raw write here would look green on the live graph and be undone silently.

⚠️ **The journal is a precondition, not an option.** If no journal is
configured for the target, every write would be live-only, and this tool REFUSES
before the first write rather than produce that. `--event-log-path` names it;
when omitted it is resolved by `tortoise.config._resolve_event_log_path` — the
ONE derivation the deployed lane uses.

⛔ AND A CONFIGURED JOURNAL IS NOT ENOUGH: the record must actually LAND. The
SDK's `_journal_entity_mutation` is best-effort (it returns `False` when the
append raises) and `_update_entity` does not inspect that return — so a failed
append leaves a **live-only** write that the next `rebuild_all` reverts, with no
exception anywhere. This tool therefore takes an end-of-log CURSOR before the
run and, after it, reads the records appended since — requiring THIS row's own
`EntityMutated{label: Object, id: rid, state.status: deprecated}` among them — and
FAILS that row when it is absent; the row is not counted as rewritten and the
run exits 1. ⛔ The proof is IDENTITY-based on purpose: an earlier version used a
`EventLog.position()` delta, and `position()` is diagnostics, not evidence
(``tortoise/log.py``: "under concurrency the number it names belongs to a peer's
record") — a peer's record satisfied the delta over our failed append, and the
row rebuilt back to `outdated` while the run reported success. A count, a delta
or "any `EntityMutated`" cannot attribute a missing record to a row.

WHY PREVIEW IS THE DEFAULT
==========================
The absence of `--execute` is the preview; there is no `--dry-run`. Same shape as
`tools/purge_legacy_turns.py`: a destructive arm that cannot run by accident.
The preview prints the population **split by word** (`outdated` and `retracted`
are different questions and are counted separately), the ids, and the rows this
tool deliberately does NOT touch. A preview that reports **0 / 0** is a real
result: it means no stored row needs the `assembly` member, and the member is
dead weight on that graph (measured per graph — it is not a claim about any
other). ⛔ UNLESS a marker names rows that are still not proven durable (a
previous run may have left them LIVE-ONLY) or the read was truncated: those are
printed, the 0/0 sentence is withheld, and the preview exits 1 — a read it could
not complete is not a measurement. The proof is the journal's LAST status record
per row, so a marker left by a killed run whose rows all landed is discharged by
a preview too, and the 0/0 reading becomes a real measurement.

WHAT IT REWRITES, AND WHAT IT DELIBERATELY DOES NOT
==================================================
* **Yes:** `:Object` rows whose `status` string is `outdated` or `retracted` →
  `status='deprecated'`. `deprecated` is a declared §4.3 Object terminal word, and
  it is what the owner's decision names. `retracted` → `deprecated` is a
  dead→dead move, which `_update_entity`'s non-Point arm admits by design;
  `outdated` is NOT in the family's dead set, so that move is live→dead.
  ⚠️ THE READ SIDE MOVES TOO, and that is the point: `outdated` is not a member
  of `OBJECT_TERMINAL_STATUSES`, so a rewritten row leaves the recall/search
  pool it was being served from and is excluded by the terminal vocabulary
  instead. That is the intended alignment — one state, not a state half the
  system believes — but an operator comparing recall output before and after
  should expect the row to disappear rather than to change label.
* **No:** the `outdated` **boolean flag** (`n.outdated = true`). It is a different
  axis (ONTOLOGY §5's Point lifecycle flag, read by `live.is_terminal_status`),
  the owner's decision excludes it, and this tool would corrupt Points if it
  touched it. ⚠️ The count+sample the preview prints is `:Object` rows only —
  the query is `MATCH (n:Object)`, and the flag is NOT counted on `:Point`
  rows, which is where it is meaningful. Read the number as "Object-side
  `outdated` flags", never as "the flag population".
* **No:** `:Point` / `:Subject` / `:Event` / `:Source` rows. This is the Object
  family only, and "only" is enforced by RESOLVING THE ID, not merely by the
  row's own labels: `update_knowledge` binds an addressed row by every identity
  key the write door declares (`id` per label, plus the declared secondary — a
  url-only `:Source` stub is reached by an id it does not carry).
* **No:** a row's supersession carriers (`supersededBy` / `supersededAt`). A row
  that carries both a legacy status word and a carrier is **counted separately**
  in the preview, because rewriting the status leaves the carrier in place — the
  operator sees that shape rather than this tool silently deciding what it means.

⛔ A ROW LABELLED **BOTH** `:Point` AND `:Object` IS REFUSED, AND THAT IS
DELIBERATE. `update_knowledge` dispatches on the label, so such a row takes the
Point arm, where a terminal Point declares NO outgoing transition (#2498: *a
dead claim is answered by minting a successor, never by re-stamping it*). This
tool does NOT work around that: overriding it is a REOPEN of #2498, not a
migration step, and nothing here is authorised to reverse a recorded decision.

The row is reported as a FAILURE by id and the run exits 1 — never silently
skipped, and never counted as rewritten. ⚠️ THE REFUSAL IS MADE **HERE**, from
the row's own labels and from the ids bound to it, and NOT by catching an
exception — because only ONE of the two arms raises. Measured on this head
(2026-11-17), same fixture, both words on a `:Object`+`:Point` row:

    stored `retracted` -> NO RAISE; returns status 'retracted'; journal +1  (ECHO)
    stored `outdated`  -> RAISES ValueError "... refused ['status'] on a dead
                          claim" (#2498)

`retracted` already MEANS `deprecated` through #11824 S0's meaning mapping, so
the door writes the word the row already holds and leaves the row untouched
(`sdk.py`, "AN ECHO WRITES THE WORD THE ROW ALREADY HOLDS"); `outdated` is a
dead claim, which no door re-stamps. Either way the row does not reach
:data:`REWRITE_TO`.

⛔ AND THE LABELS OF *THIS* ROW ARE NOT THE WHOLE GUARD. `update_knowledge`
binds on `id`, not on the row: a `:Point` node that merely SHARES the id is
invisible in `labels(n)` of the `:Object`, and the Point arm would rewrite it
(#12855 forbids touching Points; measured: a sibling Point went `live ->
deprecated` while the Object stayed `outdated`, and the call still returned
`deprecated`). So every node the WRITE could bind is resolved first — not just
`m.id`: the door binds by each label's primary key plus its SECONDARY key on a
miss (`:Source`'s `url`), so a url-only `:Source` stub whose `url` equals the
`:`Object`'s id is bound by the same call while carrying no `id` at all. The
binding query is derived from the door's own key tables for that reason. A row is
refused unless it is the ONLY node bound and carries no `:Point`/`:Subject`
label; ⚠️ a further label that selects another arm (e.g. `[Object, Source]`) is
ADMITTED — it takes the Source arm as well and journals two records for one set
of values, which is why the batch gate's `delta < len(rewritten)` is a LOWER
bound, never an equality. The post-write check re-reads THAT node, never the value
`update_knowledge` returned — which resolves by label precedence and would
report the Point. Measured on the canonical instance (2026-11-17):
exactly one such row exists (`org_probe8884b_5d1a9fd5`, a probe graph), and it
is the ONLY `:Object` row in the whole instance carrying a legacy word — so this
refusal, not a rewrite, is what an operator sees there. Read that as the tool
answering honestly, not as the rewrite having failed.

PAGING AND THE CROSS-CHECK (#5062 / #4515)
==========================================
FalkorDB applies a server-global `RESULTSET_SIZE` (default 10 000) silently, so
an unpaged population read can certify the half it saw and this tool would then
leave rows behind while reporting success. The read is keyset-paged on `id(n)`
and compared against the store's OWN aggregate count; a page set that falls
short is reported as `truncated` and REFUSES the execute arm. The post-condition
re-read is the second guard: `--execute` exits non-zero unless the population is
**0 after the run**, whatever the per-row results said.

EXIT CODES
==========
    0   the run completed — a preview, or an execute whose population is now 0
    1   the run did not reach its post-condition: rows remain, a row failed, a
        record did not land, a row's `id` is bound by more than one node, a
        durability marker is PENDING (a previous run may have left rows
        live-only — the reading is held back until `rebuild_all` + a re-run), or
        the read was truncated / a row is UNADDRESSABLE (no usable `id`). A
        PREVIEW over such a read exits 1 too — the preview is the measurement
        that unblocks the sequenced drop, so it must not read as complete.
    2   refused before touching anything — on BOTH arms, since a refusal is the
        same verdict whether or not `--execute` was asked for: no journal, an
        empty or AMBIGUOUS target flag (`--uri` together with `--db-path`), a
        durability marker this run could not READ (permission, or a directory in
        its place: its ids are unknown, so neither the report nor the 0-rows
        reading is trustworthy), and on the WRITE arm another run holding the
        journal's run lock (`another-run-in-progress`) or a lock file that cannot
        be created (`run-lock-unavailable`) — a preview takes the lock best-effort:
        it writes nothing but that lock, so a busy or read-only lock must not
        block a measurement; and on `--execute` a TRUNCATED read or an
        UNADDRESSABLE row (no usable `id`) — both exit 1 on
        a preview (an incomplete measurement) and 2 on the write arm (nothing is
        written). ⚠️ A FAN-OUT / dual-labelled row is NOT in this set: it is a
        per-ROW refusal (that row is reported by id, the safe rows are still
        rewritten, exit 1), because refusing the whole run would mean the tool
        could never make progress on a graph holding one such row — which is
        exactly the canonical instance.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import would fail first with an UNATTRIBUTED error.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/rewrite_object_status.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/rewrite_object_status.py`"
    )

import argparse
import contextlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

if __package__ in (None, ""):  # run as a script / imported by a test
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: The legacy words this tool retires, each rewritten to :data:`REWRITE_TO`.
#: ONE declaration: the Cypher predicate, the preview's split counts and the
#: docstring's table all read it.
LEGACY_WORDS: tuple[str, ...] = ("outdated", "retracted")

#: The declared §4.3 Object terminal word the owner's decision names.
REWRITE_TO = "deprecated"

#: Keyset page size — well under FalkorDB's `RESULTSET_SIZE` (default 10 000)
#: so a page can never be truncated; the aggregate count below is the guard.
_PAGE = 1000

REFUSE_NO_JOURNAL = "no-journal"
REFUSE_EMPTY_FLAG = "empty-target-flag"
REFUSE_UNADDRESSABLE = "unaddressable-row"
REFUSE_TRUNCATED = "truncated-read"
REFUSE_FANOUT = "id-fanout-or-foreign-label"
REFUSE_AMBIGUOUS_TARGET = "ambiguous-target-flag"
REFUSE_PENDING_UNWRITABLE = "durability-marker-unwritable"
REFUSE_PENDING_UNREADABLE = "durability-marker-unreadable"
REFUSE_BUSY = "another-run-in-progress"
REFUSE_LOCK_UNAVAILABLE = "run-lock-unavailable"

#: Knowledge labels a ``:Object`` must NOT also carry. ``update_knowledge``
#: dispatches on the label, so a dual-labelled row takes the other family's arm
#: and the Object tier's `status` write never runs. Both answers that arm can
#: give mean "not rewritten": a stored SYNONYM (`retracted`/`archived`) ECHOES
#: (returns the row unchanged, #11824 S0 review P2) while a stored `outdated`
#: RAISES the #2498 dead-claim refusal. Neither leaves the row at
#: :data:`REWRITE_TO`, so the refusal is made here, from the labels, BEFORE the
#: write — not by catching an exception, which only one of the two raises.
_FOREIGN_KNOWLEDGE_LABELS = frozenset({"Point", "Subject"})


def _journal_cursor(log):
    """An opaque end-of-log cursor, or ``None`` when it cannot be taken.

    ⛔ The cursor is what makes the durability check IDENTITY-based. A
    `position()` DELTA proves only that the journal GREW, and `position()` is
    documented as diagnostics, not evidence (``tortoise/log.py``: "under
    concurrency the number it names belongs to a peer's record"). A peer writing
    this graph's journal during our failed append therefore satisfies a delta gate
    and leaves a LIVE-ONLY row certified as recorded — measured. A cursor lets us
    read back the records appended in the window and look for OUR row's record.

    ⛔ ONE cursor per RUN, not per row: `read_after` re-parses the whole journal
    (`EventLog.read_all`, no cache) — measured ≈30 ms per call at a 535 KiB /
    5,000-record journal, so a per-row probe costs minutes on a real one. The
    window only has to bound "after the run's first write", and matching the
    wider window per row is not weaker: a record for THIS row with the target
    status means the journal replays the target for it, which is the property
    being certified.
    """
    try:
        return log.cursor_at_end()
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _journal_records_since(log, cursor):
    """The records appended after ``cursor``; ``None`` when unreadable."""
    if cursor is None:
        return None
    try:
        return log.read_after(cursor)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _row_recorded(records, rid) -> bool:
    """True only when this row's LAST status record among ``records`` says rewritten.

    Matching on `(type, label, id)` — not on a count — is what makes the gate
    independent of a concurrent writer: a peer's record for another row cannot
    stand in for ours.

    ⛔ LAST RECORD WINS, not "any record exists". The journal replays as a FOLD,
    so what makes a row durable is its final status, not some earlier one. A row
    whose history is `deprecated` -> `outdated` (an un-deprecation, or a rewrite
    that later got re-stated) HAS a `deprecated` record; an existence test
    discharges it, and a preview then prints "0 rows ⇒ dead weight" over a row
    whose live state is `outdated` — the false PASS this gate exists to prevent
    (measured: journal ['deprecated','outdated'], live `deprecated`, exit 0).
    """
    last = None
    for rec in records or ():
        if not isinstance(rec, dict):
            continue
        state = rec.get("state")
        if not isinstance(state, dict) or "status" not in state:
            continue
        if (rec.get("type") == "EntityMutated"
                and rec.get("label") == "Object"
                and str(rec.get("id")) == str(rid)):
            last = state.get("status")
    return last == REWRITE_TO


def _row_gone(records, rid) -> bool:
    """True when the journal's LAST record for this row is its DELETE.

    ⛔ WITHOUT THIS THE MARKER CAN BE PERMANENTLY UNCLEARABLE. `_row_recorded`
    reads status-bearing records only, so a row a killed run marked and a later
    run DELETED has its last status record still `outdated` — under a
    record-existence rule it was kept forever, and the prescribed repair
    (`rebuild_all`, then re-run) provably could not clear it: the row is gone, so
    it never re-enters the population and no rewrite ever happens. Measured: mint
    a legacy row, `_set_pending(journal, [oid])`, delete it through the SDK, then
    `--execute` twice around a `rebuild_all` — exit 1 each time, marker unchanged,
    and the "0 rows ⇒ the `assembly` member is dead weight" measurement could
    never be produced on that graph.

    A deleted row cannot be live-only: there is nothing left for `rebuild_all` to
    restore. Terminal, so it is discharged.
    """
    last = None
    for rec in records or ():
        if not isinstance(rec, dict):
            continue
        if (rec.get("type") == "EntityMutated"
                and rec.get("label") == "Object"
                and str(rec.get("id")) == str(rid)):
            last = rec
    return last is not None and str(last.get("op") or "") == "delete"


def _pending_path(journal: str | None):
    """The durable marker beside the journal, or ``None`` when there is no path.

    ⛔ WHY A FILE. A failed (or SIGTERM'd) run leaves rows written LIVE-ONLY: the
    node says `deprecated`, the journal still replays `outdated`, and nothing on
    disk records that. The next PREVIEW then sees 0 rows and prints the sentence
    that authorises dropping the `assembly` member — a false measurement produced
    by the tool itself, and the one thing this migration must not do. The marker
    is written BEFORE the first write and removed only by a run that discharges
    it, so a killed run is covered too.

    ⛔ NORMALISED: `/x/j.jsonl`, `/x/./j.jsonl` and a symlinked base dir name ONE
    journal but would otherwise produce three different marker files — an
    `--execute` that failed via one spelling would leave a marker a preview via
    another spelling cannot see, restoring the false reading with no re-run.
    """
    if not journal:
        return None
    # ⛔ BOTH halves from the RESOLVED path. `resolve()` follows a symlink, but
    # naming the marker after the UNRESOLVED basename put it beside the TARGET
    # under the LINK's name: one journal, two markers, and an `--execute` that
    # failed through one spelling leaves a marker a preview through the other
    # cannot see — the false reading this function exists to prevent (measured
    # with `b/alias.jsonl -> a/events.jsonl`).
    resolved = Path(journal).expanduser().resolve()
    return resolved.with_name(resolved.name + ".rewrite-pending")


def _atomic_write(path: Path, text: str) -> None:
    """Replace ``path`` atomically AND durably (tmp + fsync + ``os.replace``).

    ⛔ NOT ``write_text``: that is truncate-then-write, so a kill, OOM or ENOSPC
    in the window leaves an EMPTY marker — which `_read_pending` reads as "no
    marker", and the next preview then prints the sentence that authorises
    dropping the `assembly` member. The guard's own update could produce the
    false PASS it exists to prevent (measured: a torn write ⇒ `pending=[]` ⇒
    "dead weight": True, exit 0).

    ⛔ ``fsync`` BEFORE the replace, and again on the DIRECTORY after it: a
    rename is atomic against a process kill, not against a machine crash, and
    the crash leaves a zero-length marker that `_read_pending` refuses on both
    arms — a dead end the operator cannot clear from the message alone.
    ``write_text`` stays the write primitive so the torn-write seam a test
    monkeypatches remains the real one.
    """
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        with contextlib.suppress(OSError):
            fd = os.open(str(tmp), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        os.replace(tmp, path)
        with contextlib.suppress(OSError):
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    finally:
        with contextlib.suppress(OSError):
            if tmp.exists():
                tmp.unlink()


def _write_pending(journal: str | None, ids: list) -> bool:
    """MERGE ``ids`` into the marker; ``False`` when it cannot be written.

    ⛔ MERGE, never replace. A row is named here BEFORE its own write, and the
    rows a previous run left live-only now read `deprecated` LIVE, so they are
    absent from the current population: a replace would drop them, and the clean
    run that follows would clear the marker — restoring the false "0 rows"
    reading it exists to prevent (measured: one `--execute` after a failed run
    was enough). Shrinking is :func:`_set_pending`'s job, at the end of a run.
    """
    path = _pending_path(journal)
    if path is None:
        return True
    with _marker_lock(journal):
        return _write_pending_locked(journal, ids)


def _write_pending_locked(journal: str | None, ids: list) -> bool:
    path = _pending_path(journal)
    if path is None:
        return True
    current = _read_pending(journal)
    if current is None:
        # ⛔ ``None`` is NOT "no marker": merging onto an unreadable one would
        # OVERWRITE ids nobody could read. Fail, so the row is refused rather
        # than written without a durable trace.
        return False
    pending = set(current)
    pending.update(str(i) for i in ids if i is not None)
    if not pending:
        # Nothing to record, and an EMPTY marker file is residue a later reader
        # would have to reason about — do not create it.
        return True
    try:
        _atomic_write(path, "\n".join(sorted(pending)) + "\n")
    except OSError:
        return False
    return True


def _set_pending(journal: str | None, ids: list) -> bool:
    """Write EXACTLY ``ids``, removing the marker when the set is empty.

    ⛔ The per-row writes MERGE (a row is named before its own write), so they
    can never shrink the set. The end of a run must be able to DISCHARGE: an id
    durably rewritten in a run that was not entirely clean reads `deprecated`
    LIVE from then on, so it never re-enters a later run's population and could
    never be discharged again — the marker would be poisoned forever and the
    graph permanently unmeasurable (measured: 3 runs, ids stuck on disk).
    """
    with _marker_lock(journal):
        return _set_pending_locked(journal, ids)


def _set_pending_locked(journal: str | None, ids: list) -> bool:
    path = _pending_path(journal)
    if path is None:
        return True
    ids = sorted({str(i) for i in ids if i is not None})
    try:
        if not ids:
            _clear_pending_locked(journal)
            return True
        _atomic_write(path, "\n".join(ids) + "\n")
    except OSError:
        return False
    return True


#: ``{"<lock path>": [handle, refcount]}`` — the lock is held per process, so a
#: nested acquisition (the CLI holds it for the whole run, the marker operations
#: take it again) must not deadlock against itself: a second `flock` on a second
#: fd of the SAME process fails with EWOULDBLOCK, which would refuse our own run.
_RUN_LOCKS: dict[str, list] = {}


def _acquire_run_lock(journal: str | None, *, required: bool = True):
    """An EXCLUSIVE, non-blocking lock next to the marker; ``Refused`` if busy.

    ⛔ The marker protocol is a read-modify-write shared by every process pointed
    at this journal: a per-row MERGE reads the file and writes it back, and the
    end-of-run discharge writes an EXACT set. Without serialization they are
    last-writer-wins, so the discharge silently DELETES an id a concurrent run
    just merged — the peer's live-only row loses its only durable trace and the
    next preview prints the `0 rows ⇒ dead weight` sentence with exit 0. That is
    the exact false PASS this marker exists to prevent (measured: a peer merge
    fired inside the discharge's journal read, and the peer id was gone).

    ``required=False`` is the PREVIEW arm: a preview writes nothing but the lock
    file, and a lock it cannot create (read-only journal directory) or cannot
    take (a peer mid-run) must not block a legitimate measurement — it degrades
    to no lock instead of refusing. The write arm is serialized for real.

    The lock file is never unlinked: removing a file under a held `flock` lets a
    third process lock a NEW inode and run concurrently with the old holder.
    """
    path = _pending_path(journal)
    if path is None:
        return None
    lock_path = path.with_name(path.name + ".lock")
    key = str(lock_path)
    held = _RUN_LOCKS.get(key)
    if held is not None:
        held[1] += 1
        return (key, held[0])
    try:
        # A long-lived handle is the POINT (the flock lives as long as the run),
        # so the context-manager rule does not apply here.
        handle = open(lock_path, "a+", encoding="utf-8")  # noqa: SIM115
    except OSError as exc:
        if not required:
            return None
        raise Refused(REFUSE_LOCK_UNAVAILABLE, (
            f"the run lock {lock_path} could not be created ({exc}), so a "
            "concurrent run could erase this run's durability marker. Nothing "
            "was written.")) from exc
    try:
        import fcntl
    except ImportError:                     # pragma: no cover - non-POSIX
        _RUN_LOCKS[key] = [handle, 1]
        return (key, handle)
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        if not required:
            return None
        raise Refused(REFUSE_BUSY, (
            f"another run holds {lock_path} ({exc}). Two runs on one journal can "
            "erase each other's durability markers, so this one refuses rather "
            "than interleave. Wait for it, then re-run. Nothing was written."
        )) from exc
    _RUN_LOCKS[key] = [handle, 1]
    return (key, handle)


def _release_run_lock(token) -> None:
    if token is None:
        return
    key, handle = token
    held = _RUN_LOCKS.get(key)
    if held is None:
        return
    held[1] -= 1
    if held[1] > 0:
        return
    _RUN_LOCKS.pop(key, None)
    with contextlib.suppress(ImportError, OSError, AttributeError, ValueError):
        # `ImportError` matters: `_acquire_run_lock` deliberately supports a
        # platform without `fcntl` and returns an unlocked token there, so a
        # release that re-imported it unguarded would crash AFTER the migration
        # ran — a traceback and a non-zero exit over completed work.
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    with contextlib.suppress(OSError, AttributeError):
        handle.close()


@contextlib.contextmanager
def _marker_lock(journal: str | None):
    """Serialise one marker read-modify-write (the write arm's granularity)."""
    token = _acquire_run_lock(journal, required=True)
    try:
        yield
    finally:
        _release_run_lock(token)


def _population_ids(rep) -> set[str]:
    return {str(r.get("id")) for r in rep.rows if r.get("id") is not None}


def _kept_pending(pending, population, log) -> list[str]:
    """The ids that must STAY marked: this run's, plus any not proven durable.

    ⛔ Applied on the PREVIEW arm too. A marker left by a killed run can name
    only ids whose own record already landed; they are discharged by the execute
    arm, but a preview that showed them verbatim withheld the measurement and
    prescribed `rebuild_all` — a destructive journal replay — on a healthy graph.
    Read-only: no write is involved on the preview path.

    The WHOLE journal is read (these ids were named by runs that may be long
    finished), and only when there is something to judge: the steady state (no
    marker, nothing rewritten) parses nothing.
    """
    ids = sorted({str(i) for i in pending or () if i is not None})
    if not ids:
        return []
    try:
        records = log.read_all()
    except (AttributeError, OSError, TypeError, ValueError):
        records = None                       # fail closed: keep every id
    kept = []
    for pid in ids:
        # ⛔ An id still carrying a legacy word LIVE is NOT dischargeable,
        # whatever an older record says about an earlier life of the row.
        if pid in population:
            kept.append(pid)
            continue
        # Not in the population: dischargeable only on PROOF — its own record
        # says rewritten, or the journal says the row is GONE (`_row_gone`).
        if _row_recorded(records, pid) or _row_gone(records, pid):
            continue
        kept.append(pid)
    return kept


def _clear_pending(journal: str | None) -> None:
    with _marker_lock(journal):
        _clear_pending_locked(journal)


def _clear_pending_locked(journal: str | None) -> None:
    path = _pending_path(journal)
    if path is None:
        return
    with contextlib.suppress(OSError, AttributeError):
        path.unlink()


def _read_pending(journal: str | None) -> list[str] | None:
    """The ids a previous run may have left live-only; ``None`` when unreadable.

    ⛔ ``None`` and ``[]`` are DIFFERENT answers: an unreadable marker is not
    "no marker", and collapsing them lets a permission error read as a clean
    graph.
    """
    path = _pending_path(journal)
    if path is None:
        return []
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, AttributeError):
        return None
    ids = [line.strip() for line in text.splitlines() if line.strip()]
    if not ids:
        # ⛔ AN EMPTY MARKER IS NOT "NO MARKER". A kill inside `write_text`'s
        # truncate-then-write window leaves exactly this, and reading it as
        # "clean" restores the false `0 rows ⇒ dead weight` conclusion the marker
        # exists to prevent (measured). `_write_pending` is now atomic, so an
        # empty file can only mean a marker a previous version half-wrote — still
        # a state whose content is unknown, so fail closed.
        return None
    return ids


def _nodes_sharing_id(g, rid: str) -> list[list[str]]:
    """Every node the WRITE DOOR would bind for ``rid``, as its label list.

    `update_knowledge` binds on identity keys, not on the row: `id` for every
    canonical label, plus each label's DECLARED secondary key — `Source.url` is
    one, because `_link_source`/`_mint_source_stub` mint url-only stubs with no
    `id` (`tortoise/projection/__init__.py`, ``_CANONICAL_ENTITY_SECONDARY_ID_
    PROPS``). So a `:Point`/`:Subject`/`:Event`/`:Source` node that merely shares
    an `:Object`'s address is invisible in that row's `labels(n)` while the write
    still reaches it (#12855: Points keep their words).

    ⛔ The arms are DERIVED from the door's own tables, never hand-written: a
    hand-written subset is the #2901/#4653 failure shape (a value silently
    omitted from the set that decides). Measured 2026-11-17: a sibling Point went
    `live -> deprecated` while its Object stayed `outdated`, and an id-addressed
    run over a url-only `:Source` stub wrote the Source and exited 0.
    """
    # ⛔ TWO COLUMNS, AND THE NODE ID IS LOAD-BEARING. `labels(m)` alone is NOT
    # node-unique, and `UNION` is a set operation over whole ROWS: two DISTINCT
    # `:Object` nodes with the same label set collapse to ONE result row, so the
    # `len(nodes) != 1` fan-out refusal never fires and the write restamps the
    # sibling — measured: `(a:Object {id:'dup1', status:'outdated'})` beside
    # `(b:Object {id:'dup1', status:'live'})` yielded `[['Object']]`, the run
    # reported `rewritten: ['dup1']`, and the never-populated `live` sibling came
    # back `deprecated` at exit 0 (the journal names `(Object, dup1)`, so a replay
    # applies it to both). Returning `id(m)` keeps two nodes distinct while ONE
    # node matched by two of its own arms still collapses — the de-dup holds.
    return [list(labs or []) for (_nid, labs) in g.query(
        _binding_cypher(), params={"rid": rid}).result_set]


_BINDING_CYPHER: str | None = None


def _binding_cypher() -> str:
    """The identity resolution used by :func:`_nodes_sharing_id`, built from the
    write door's canonical identity tables (so it cannot drift from them).

    ⛔ LABEL-SCOPED ARMS, UNIONed. A `MATCH (m) WHERE (m:Point AND m.id=$rid) OR
    …` is an unlabelled full-node scan — measured ≈1.5 µs/node, i.e. ≈60 ms at
    40 k nodes, and it runs several times per legacy row on a tool meant for
    production. Each arm here is an indexed label+property lookup, and the repo's
    own write door resolves its ids the same way (``sdk.py``'s ``_lifecycle_ids``).
    ``UNION`` (not ``UNION ALL``) so a node matching two of its own arms — a
    `:Source` carrying both the id and the url, a dual-labelled node — is counted
    ONCE.

    ⛔ THE PROJECTION IS `id(m), labels(m)`, NEVER `labels(m)` ALONE. `UNION`
    de-duplicates ROWS, so a labels-only projection also merges two DIFFERENT
    nodes that happen to carry the same label set — the fan-out gate then sees
    one binding and the write restamps a sibling it never saw (measured; see
    `_nodes_sharing_id`).
    """
    global _BINDING_CYPHER
    if _BINDING_CYPHER is None:
        from tortoise.projection import (
            _CANONICAL_ENTITY_ID_PROPS,
            _CANONICAL_ENTITY_SECONDARY_ID_PROPS,
        )
        pairs = (list(_CANONICAL_ENTITY_ID_PROPS)
                 + list(_CANONICAL_ENTITY_SECONDARY_ID_PROPS))
        arms = [f"MATCH (m:{label} {{{prop}: $rid}}) RETURN id(m), labels(m)"
                for label, prop in pairs]
        _BINDING_CYPHER = "\nUNION\n".join(arms)
    return _BINDING_CYPHER


def _binding_refusal(rid: str, nodes: list[list[str]]) -> str:
    """Why a row whose id is not bound by this `:Object` alone is refused.

    Two shapes, and they need different words: a SECOND binding (the write would
    restamp a sibling), or a single binding whose labels put the write on another
    family's arm — where the two knowledge families behave differently, and the
    message must say which: on the POINT arm the rewrite never lands (a stored
    synonym ECHOES, a stored `outdated` RAISES the #2498 refusal), while on the
    SUBJECT arm the word IS written and stored (measured 2026-11-17), so the
    refusal there is this tool's SCOPE.
    """
    if len(nodes) > 1:
        return (
            "REFUSED (id-fanout): this address is bound by "
            f"{len(nodes)} nodes -> {nodes!r}. update_knowledge binds on the "
            "identity keys, not on the row, so writing here could restamp a "
            "sibling :Point/:Subject/:Event/:Source (#12855 keeps their words) "
            "and could leave THIS row untouched while the call reports the "
            "sibling. Nothing was written for this row. Resolve the duplicate "
            "address by hand, then re-run.")
    labels = list(nodes[0]) if nodes else []
    if not labels:
        return (
            "REFUSED (unbound): no node is bound by this address, so the write "
            "would match nothing and this row would be a silent no-op. Nothing "
            "was written.")
    foreign = sorted(lbl for lbl in labels
                     if lbl in _FOREIGN_KNOWLEDGE_LABELS)
    if foreign:
        return (
            "REFUSED (dual-labelled): this :Object also carries "
            f"{', '.join(':' + lbl for lbl in foreign)} — update_knowledge "
            "dispatches on the label, so the write takes that family's arm. "
            "On the POINT arm the rewrite never lands: that arm either ECHOES a "
            "stored synonym back (the row keeps its word, #11824 S0) or REFUSES "
            "a dead claim (a claim is answered by minting a successor, never by "
            "re-stamping it — #2498). On the SUBJECT arm the caller's word IS "
            "written and stored (measured 2026-11-17) — the refusal there is "
            "this tool's SCOPE, not a limitation: it refuses a row that carries "
            "a `:Point`/`:Subject` label, because such a node carries a "
            "lifecycle another family owns (#12855 keeps their words). Either "
            "way the row is reported here instead of being "
            "counted as rewritten. Mint a successor, or correct the label, and "
            "re-run.")
    return (
        "REFUSED (not an :Object binding): the node at this address is labelled "
        f"{labels!r}, so the write would not restamp the :Object this tool read. "
        "Nothing was written.")


def _is_clean_object_row(nodes: list[list[str]]) -> bool:
    """True only when the write can bind EXACTLY ONE node, and that node is an
    `:Object` carrying no other `:Point`/`:Subject` label.

    A second binding is what lets `update_knowledge` write a row this tool never
    read; a `Point`/`Subject` label on the single binding is the dual-label case
    (that family's arm gets the write). Either is refused.

    ⚠️ A further label that selects ANOTHER arm is tolerated, not filtered —
    measured 2026-11-17: a `[Object, Source]` row takes the Source arm as well
    and journals TWO records for one set of values, so the batch gate's
    `delta < len(rewritten)` is a LOWER bound on records, never an equality. The
    write itself is still correct (same node, same value); the count is simply
    not `len(rewritten)` on such a row.
    """
    if len(nodes) != 1:
        return False
    labels = list(nodes[0])
    if "Object" not in labels:
        return False
    return not any(lbl in _FOREIGN_KNOWLEDGE_LABELS for lbl in labels)


def _object_status(proj, rid: str):
    """The status stored on the `:Object` node bound by ``rid`` — nothing else.

    Takes the PROJECTION and reaches its graph handle here, because a
    projection's own ``query`` wraps its arguments differently from the graph's
    (the nested-``params`` bug measured in #12855; see :func:`collect`'s note).

    ⛔ Never `update_knowledge`'s return value: that resolves by label
    PRECEDENCE, so on a duplicate id it answers with the `:Point`/`:Subject`
    sibling, and a guard reading it would certify a rewrite of a row that was
    never written.
    """
    rows = proj.g.query(
        "MATCH (n:Object {id:$rid}) RETURN n.status",
        params={"rid": rid}).result_set
    return rows[0][0] if rows else None


def _word_predicate() -> str:
    """The status predicate, built from :data:`LEGACY_WORDS` (never retyped).

    Fixed module constants — no caller input reaches this string, so there is
    nothing to inject and no need for a parameter.
    """
    return " OR ".join(f"n.status = '{w}'" for w in LEGACY_WORDS)


def _rows_cypher() -> str:
    return (
        "MATCH (n:Object) WHERE id(n) >= $cursor AND (" + _word_predicate() + ") "
        "RETURN id(n), n.id, n.status, n.name, n.supersededBy, n.supersededAt, "
        "labels(n) "
        "ORDER BY id(n) LIMIT $batch"
    )


def _count_cypher() -> str:
    return "MATCH (n:Object) WHERE " + _word_predicate() + " RETURN count(n)"


#: The legacy FLAG — a different axis, reported and never written. `coalesce`
#: so a NULL/absent flag is not read as terminal (the read surfaces' own
#: predicate, `live.is_terminal_status`'s Cypher half).
_FLAG_COUNT_CYPHER = (
    "MATCH (n:Object) WHERE coalesce(n.outdated, false) <> false RETURN count(n)"
)
_FLAG_SAMPLE_CYPHER = (
    "MATCH (n:Object) WHERE coalesce(n.outdated, false) <> false "
    "RETURN n.id, n.outdated ORDER BY n.id LIMIT $batch"
)


@dataclass
class Report:
    """Everything one run learned, and everything it did.

    Deliberately a flat record of FACTS (counts, ids, journal deltas) — the
    renderer is :func:`render`, so a `--json` consumer and the `--execute` arm
    cannot disagree about what was measured.
    """

    graph: str | None = None
    journal: str | None = None
    #: The population, split by word: ``{"outdated": n, "retracted": n}``.
    by_word: dict[str, int] = field(default_factory=dict)
    total: int = 0
    #: The store's OWN ``count(n)`` for the predicate — the number the read is
    #: cross-checked against. Kept on the report because a reader comparing
    #: ``total`` (the length of the read) with it is the only way to SEE a
    #: partial read; the truncation flag alone cannot say "0 of 3".
    store_count: int | None = None
    #: The FULL population, `{"id", "status", "name", "graph_id"}` per row —
    #: the execute arm acts on this, never on the render sample.
    rows: list[dict] = field(default_factory=list)
    sample: list[dict] = field(default_factory=list)
    #: Rows carrying a supersession carrier as well as a legacy status word.
    carriers: list[dict] = field(default_factory=list)
    #: `:Object` rows with no `id` — the SDK cannot address them.
    unaddressable: list[str] = field(default_factory=list)
    #: The legacy boolean FLAG, on `:Object` rows (the query is `MATCH
    #: (n:Object)`; Points own the flag's meaningful population and are NOT
    #: counted here — see the note rendered beside it), reported, never written.
    flag_count: int = 0
    #: Ids a PREVIOUS run may have left LIVE-ONLY (read from the marker beside
    #: the journal). While non-empty the graph's "0 rows" state is not a
    #: measurement, so the sequenced drop must not proceed on it.
    pending: list[str] = field(default_factory=list)
    #: Rows whose `id` is bound by more than one node, or by a node whose label
    #: set is not `:Object`-only. `update_knowledge` binds on the ID, so writing
    #: such a row can restamp a `:Point`/`:Subject` sibling (#12855 forbids it)
    #: and can leave THIS row untouched while the call reports the sibling. They
    #: are refused before any write.
    fanout: list[dict] = field(default_factory=list)
    flag_sample: list[dict] = field(default_factory=list)
    #: The paged read fell short of the store's own count.
    truncated: bool = False
    executed: bool = False
    rewritten: list[str] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    journal_records_before: int | None = None
    journal_records_after: int | None = None
    remaining: dict[str, int] = field(default_factory=dict)

    @property
    def journal_delta(self) -> int | None:
        if self.journal_records_before is None or self.journal_records_after is None:
            return None
        return self.journal_records_after - self.journal_records_before

    def to_dict(self) -> dict:
        # `rows` is the whole population; `sample` is what a reader needs to
        # recognise it. Keeping the full list out of the artefact keeps a
        # 10 000-row preview readable, and `rewritten` still names every id that
        # was actually written.
        out = {k: v for k, v in self.__dict__.items() if k != "rows"}
        out["journal_records_appended"] = self.journal_delta
        out["legacy_words"] = list(LEGACY_WORDS)
        out["rewrite_to"] = REWRITE_TO
        return out

# ── the read ─────────────────────────────────────────────────────────────


def collect(proj, *, sample: int = 20) -> Report:
    """Read the population. READ-ONLY, keyset-paged, cross-checked.

    Takes the PROJECTION (``sdk._get_proj()``), never a bare graph handle: a
    projection's own ``query`` wraps its arguments differently from the graph's,
    and a caller that mixed the two got a nested-``params`` statement back
    (#12855, measured) — so there is one accepted type and the graph handle is
    taken here, once.

    `sample` bounds only what is RENDERED; the full population is enumerated,
    because the execute arm rewrites every row and the preview's counts must be
    the counts it will act on.
    """
    g = proj.g
    rep = Report()
    rows: list = []
    cursor = 0
    while True:
        page = g.query(_rows_cypher(),
                       params={"cursor": cursor, "batch": _PAGE}).result_set
        if not page:
            break
        rows.extend(page)
        cursor = int(page[-1][0]) + 1
        if len(page) < _PAGE:
            break

    store_count = g.query(_count_cypher()).result_set
    total = int(store_count[0][0] or 0) if store_count else 0
    rep.store_count = total
    rep.truncated = len(rows) < total
    rep.total = len(rows)

    by_word = {w: 0 for w in LEGACY_WORDS}
    for graph_id, rid, status, name, sup_by, sup_at, labels in rows:
        word = status if isinstance(status, str) else repr(status)
        by_word[word] = by_word.get(word, 0) + 1
        row = {"id": rid, "graph_id": graph_id, "status": status, "name": name,
               "labels": list(labels or [])}
        rep.rows.append(row)
        if len(rep.sample) < sample:
            rep.sample.append(row)
        if sup_by or sup_at:
            rep.carriers.append(row)
        if rid is None or (isinstance(rid, str) and not rid):
            rep.unaddressable.append(str(graph_id))
        else:
            # ⛔ Read the ID, not just this row's labels. `update_knowledge`
            # binds on `id`, so a `:Point`/`:Subject` that merely SHARES it
            # takes that family's arm and is written while this row's own
            # labels look clean (#12855 forbids touching Points). The preview
            # must SHOW that shape rather than let `--execute` discover it.
            row["nodes"] = _nodes_sharing_id(g, rid)
            if not _is_clean_object_row(row["nodes"]):
                rep.fanout.append(row)
    rep.by_word = by_word

    flag_count = g.query(_FLAG_COUNT_CYPHER).result_set
    rep.flag_count = int(flag_count[0][0] or 0) if flag_count else 0
    rep.flag_sample = [
        {"id": r[0], "outdated": r[1]}
        for r in g.query(_FLAG_SAMPLE_CYPHER,
                         params={"batch": sample}).result_set
    ]
    return rep


# ── the write ────────────────────────────────────────────────────────────


def rewrite(sdk, rep: Report) -> Report:
    """Rewrite every collected row through the SDK's journaled update path.

    One `sdk.update_knowledge(id, status=REWRITE_TO)` per row — the public
    write that `_update_entity` journals as `EntityMutated{state: {status}}`.
    A raw `SET` is never issued here: it would not reach the journal, and the
    next `rebuild_all` would revert it (see the module docstring).

    ⛔ The pre-write refusals are re-applied HERE, not only in :func:`run`: this
    is a public writer, and a caller that reaches it with a report from an
    incomplete read would write the visible share — the partial rewrite the
    refusals exist to prevent.
    """
    rep.executed = True
    refusal = _refusal(rep)
    if refusal is not None:
        rep.executed = False
        raise refusal
    # `_refusal` covers the population gates; the MARKER gate is separate (it is
    # `run`'s, because it needs the journal path). Re-applied here as well: a
    # caller that built its own Report skipped that read, and an unknown marker
    # state is exactly what must stop a write.
    if getattr(rep, "pending", None) is None:
        rep.executed = False
        raise Refused(REFUSE_PENDING_UNREADABLE, (
            f"the durability marker {_pending_path(rep.journal)} could not be "
            "read, so it is unknown whether a previous run left rows LIVE-ONLY. "
            "Nothing was written."))
    log = sdk._get_event_log()
    # ⛔ THE JOURNAL PRECONDITION BELONGS HERE TOO (review finding, 2026-11-17).
    # `run` answers it, but `rewrite` is the public writer the docstring above
    # calls out — and measured, a direct call on a journal-less target WROTE the
    # row (`outdated` → `deprecated`, live-only, revertable by the next
    # `rebuild_all`) and then died on `None.position()` instead of refusing.
    if log is None:
        rep.executed = False
        raise Refused(REFUSE_NO_JOURNAL, (
            "the SDK reports no journal for this target, so every write would "
            "be LIVE-ONLY and the next `rebuild_all` would revert it."))
    try:
        rep.journal_records_before = log.position()
    except (OSError, AttributeError) as exc:          # pragma: no cover
        rep.failures.append({"phase": "journal-position", "error": str(exc)})

    # ⛔ WRITE THE MARKER BEFORE THE FIRST WRITE, not after a failure: a run
    # killed mid-loop leaves live-only rows and no stdout. It is MERGED with any
    # marker a previous failed run left (those rows now read clean LIVE, so they
    # are no longer in this population — replacing would drop them), and ONLY a
    # run that discharges it clears it (end of this function). An unwritable
    # marker refuses: it is the tool's own measurement guard, and "best effort"
    # would make it inert exactly when a write is about to happen.
    # ⛔ THE MARKER IS WRITTEN PER WRITE, INCREMENTALLY. Writing it once for the
    # whole planned population would include rows refused BEFORE any write (a
    # dual-labelled row), which no `--execute` can ever discharge — the marker
    # would then never clear and the graph could never be measured, while the
    # report blamed those rows for a live-only write they never had. Each id is
    # added immediately before its OWN write and merged into any marker a
    # previous failed run left (those rows read clean LIVE now, so they are not
    # in this population and must not be dropped).
    _marker_ids = set(rep.pending or ())
    # ⛔ ONE journal cursor for the whole run (see `_journal_cursor`).
    _run_cursor = _journal_cursor(log)
    _written_ok: list = []

    for row in rep.rows:
        rid = row["id"]
        if rid is None or (isinstance(rid, str) and not rid):
            rep.failures.append({
                "id": None, "graph_id": row.get("graph_id"),
                "error": ("UNADDRESSABLE: this :Object carries a legacy status "
                          "word and no usable `id` (None, or an empty string), so "
                          "the SDK cannot rewrite it"),
            })
            continue
        proj = sdk._get_proj()
        # ⛔ RE-RESOLVE AT WRITE TIME. `row["nodes"]` is a SNAPSHOT taken by
        # `collect()` — and on `--execute` it is the PREVIEW's snapshot, since
        # the write acts on the Report its refusals were checked against. A
        # sibling created between the read and this write is invisible in that
        # snapshot and the call would restamp it with no undo. One indexed query
        # per row, beside the two `position()` probes already taken.
        nodes_now = _nodes_sharing_id(proj.g, rid)
        if not _is_clean_object_row(nodes_now):
            rep.failures.append({
                "id": rid, "graph_id": row.get("graph_id"),
                "error": _binding_refusal(rid, nodes_now),
            })
            continue
        # ⛔ The marker names THIS row before its own write, so a SIGTERM between
        # here and the journal append still leaves a trace. An unwritable marker
        # refuses: it is the tool's own measurement guard, and "best effort"
        # would make it inert exactly when a write is about to happen. The id is
        # added to the report's set only once that write SUCCEEDS, so the report
        # never accuses a row that was refused of being live-only.
        if not _write_pending(rep.journal, set(_marker_ids) | {str(rid)}):
            # ⛔ Two different verdicts: an UNREADABLE marker (a read failure) is
            # not an unwritable one, and the module names them separately for a
            # reason — collapsing them mislabelled the cause.
            _token = (REFUSE_PENDING_UNREADABLE
                      if _read_pending(rep.journal) is None
                      else REFUSE_PENDING_UNWRITABLE)
            rep.failures.append({
                "id": rid,
                "error": (f"{_token}: the durability marker "
                          f"{_pending_path(rep.journal)} could not be "
                          f"{'read' if _token == REFUSE_PENDING_UNREADABLE else 'written'}"
                          ", so this row is NOT being written — a failed or "
                          "killed run would leave it LIVE-ONLY with no trace."),
            })
            continue
        _marker_ids.add(str(rid))
        try:
            landed = sdk.update_knowledge(rid, status=REWRITE_TO)
        except Exception as exc:
            rep.failures.append({"id": rid, "error": f"{type(exc).__name__}: {exc}"})
            continue
        # ⛔ Read the `:Object` ROW, never `landed["status"]`: the return value
        # resolves by label precedence, so on a duplicate id it answers with the
        # sibling and would certify a write that never reached this row.
        actual = _object_status(sdk._get_proj(), rid)
        if not isinstance(landed, dict) or actual != REWRITE_TO:
            rep.failures.append({
                "id": rid,
                "error": (f"write left the :Object at status {actual!r} "
                          f"(sdk returned {landed.get('status') if isinstance(landed, dict) else landed!r})"),
            })
            continue
        # ⛔ The binding set can change UNDER the write. Report that by name
        # rather than as "the Object was not rewritten": the sibling may have
        # been restamped by this very call, and there is no undo.
        nodes_after = _nodes_sharing_id(proj.g, rid)
        if nodes_after != nodes_now:
            rep.failures.append({
                "id": rid,
                "error": ("RACED (id-fanout): the node(s) addressed by this id "
                          f"CHANGED during the write — {nodes_now!r} -> "
                          f"{nodes_after!r} — so a sibling may have been "
                          "restamped by the same call. Not counted as "
                          "rewritten; inspect this id by hand (#12855 keeps "
                          ":Point/:Subject words)."),
            })
            continue
        # ⛔ A JOURNAL RECORD IS PART OF THE WRITE. The SDK's append is
        # best-effort and `_update_entity` ignores its result (#4240/#7951), so
        # a failed append leaves a LIVE-ONLY row that the next `rebuild_all`
        # reverts — with no exception anywhere.
        #
        # ⛔ AND THE PROOF MUST NAME THE ROW. A `position()` DELTA only proves the
        # journal GREW: `position()` is diagnostics, not evidence ("under
        # concurrency the number it names belongs to a peer's record"), so a peer
        # writing this graph's journal in the same window satisfies a delta gate
        # over OUR failed append — measured, and the row then rebuilt back to
        # `outdated` while the run reported success. So the records appended in
        # the window are matched to THIS row's identity by `_row_recorded` below.
        # ⛔ The durability verdict is taken AFTER the loop, from ONE journal
        # read (see `_journal_cursor`): per-row `read_after` calls re-parse the
        # whole journal and cost ≈30 ms each at a 535 KiB one.
        _written_ok.append(rid)

    _records = _journal_records_since(log, _run_cursor)
    for rid in _written_ok:
        if _records is None:
            # FAIL CLOSED on an unreadable cursor: "could not measure" is not
            # "recorded". The batch gate fails on a `None` delta for the same
            # reason, and a row must not be certified on a probe that never ran.
            rep.failures.append({
                "id": rid,
                "error": ("the journal's record position could not be read "
                          "around this write, so its durability is UNPROVEN: "
                          "the row may be LIVE-ONLY and reverted by the next "
                          "`rebuild_all` (#4240). Not counted as rewritten."),
            })
            continue
        if not _row_recorded(_records, rid):
            rep.failures.append({
                "id": rid,
                "error": ("the write landed but its OWN journal record is not "
                          "there: the row is LIVE-ONLY and the next "
                          "`rebuild_all` will revert it (#4240). Not counted as "
                          "rewritten."),
            })
            continue
        rep.rewritten.append(rid)

    try:
        rep.journal_records_after = log.position()
    except (OSError, AttributeError) as exc:          # pragma: no cover
        rep.failures.append({"phase": "journal-position", "error": str(exc)})

    after = collect(sdk._get_proj())
    rep.remaining = dict(after.by_word)
    # ⛔ The POST-condition must be judged on the POST read. `rep.truncated`
    # describes the PRE-write read; a truncated or unaddressable post-read can
    # show `leftover == 0` while rows remain, which is a false PASS.
    if after.truncated:
        rep.truncated = True
    if after.unaddressable:
        rep.unaddressable.extend(after.unaddressable)
    # ⛔ RECONCILE `fanout` WITH WHAT WAS ACTUALLY WRITTEN. `rep.fanout` is the
    # PRE-READ snapshot: a sibling can be deleted (or created) before the write,
    # and the write re-resolves the address. Left unreconciled, the report tells
    # an operator a row was "refused, never written" that the run just rewrote —
    # the one line an operator uses to decide whether to look closer.
    written = set(rep.rewritten)
    rep.fanout = [r for r in rep.fanout if r.get("id") not in written]
    _listed = {r.get("id") for r in rep.fanout}
    for row in after.fanout:
        if row.get("id") not in written and row.get("id") not in _listed:
            rep.fanout.append(row)
    # ⛔ DISCHARGE, which the per-row MERGE cannot do. See `_set_pending`.
    #
    # ⛔ AND THE DISCHARGE IS NOT `rep.rewritten` ALONE. A run KILLED mid-loop
    # (SIGTERM/OOM/KeyboardInterrupt) never reaches this line, so every id named
    # so far — INCLUDING ids whose own record already landed — stays in the
    # marker. Those rows read `deprecated` live AND replay `deprecated`, so they
    # never re-enter a population and only a hand-edit could clear them: the
    # documented repair (`rebuild_all`, then re-run) restores nothing for them.
    # So the same identity proof is applied to the marker's OWN ids: an id
    # carrying a durable rewrite record is discharged, one without it is kept.
    _discharged = {str(i) for i in rep.rewritten}
    # ⛔ ONE CRITICAL SECTION — read, filter AND write under the same lock. Taking
    # the lock for the read and again for the write, with nothing held in between,
    # leaves the exact-set write unprotected: a peer's merge landing in that gap is
    # silently erased, so its live-only row loses the only durable trace and the
    # next preview certifies the graph (measured: a real peer `_write_pending`
    # succeeded mid-discharge and its id was gone afterwards).
    with _marker_lock(rep.journal):
        return _discharge_locked(rep, log, _discharged, _marker_ids)


def _discharge_locked(rep, log, _discharged, _marker_ids) -> Report:
    _on_disk = _read_pending(rep.journal)
    if _on_disk is None:
        # An unreadable marker is not an empty one (see `_read_pending`); do not
        # shrink a set nobody could read, and say so.
        _on_disk = set(_marker_ids)
        rep.failures.append({
            "id": None,
            "error": (f"{REFUSE_PENDING_UNREADABLE}: the durability marker "
                      f"{_pending_path(rep.journal)} became unreadable during "
                      "the run, so its ids could not be discharged."),
        })
    _still = [pid for pid in _kept_pending(
        sorted(set(_on_disk) | _marker_ids), _population_ids(rep), log)
        if pid not in _discharged]
    _write_failed = None
    if (rep.failures or rep.truncated or rep.unaddressable or rep.fanout
            or _still
            or any(rep.remaining.get(w, 0) for w in LEGACY_WORDS)):
        rep.pending = _still
        if not _set_pending_locked(rep.journal, _still):
            _write_failed = _still or list(_on_disk)
    else:
        _clear_pending_locked(rep.journal)
        rep.pending = []
    if _write_failed is not None:
        # A failed discharge is not a silent success: the marker on disk still
        # names ids this run did not discharge, so the graph is unmeasured.
        rep.pending = _write_failed
        rep.failures.append({
            "id": None,
            "error": (f"{REFUSE_PENDING_UNWRITABLE}: the durability marker "
                      f"{_pending_path(rep.journal)} could not be updated with "
                      f"the discharged set, so it still names {_write_failed!r}."),
        })
    return rep


# ── rendering ────────────────────────────────────────────────────────────


def render(rep: Report) -> str:
    lines: list[str] = []
    head = "execute" if rep.executed else "preview"
    lines.append(f"rewrite_object_status: {head} — graph {rep.graph!r}")
    lines.append(f"  journal: {rep.journal}")
    for word in LEGACY_WORDS:
        lines.append(f"  :Object rows with status={word!r}: {rep.by_word.get(word, 0)}")
    other = {k: v for k, v in rep.by_word.items() if k not in LEGACY_WORDS}
    for word, n in sorted(other.items()):
        lines.append(f"  (unexpected) :Object rows with status={word}: {n}")
    pop = f"  population: {rep.total}"
    if (rep.store_count is not None
            and rep.store_count != rep.total):
        pop += f"  (store count {rep.store_count})"
    lines.append(pop)
    if rep.truncated:
        lines.append(
            "  ⛔ TRUNCATED: the paged read fell short of the store's own count — "
            f"{REFUSE_TRUNCATED}")
    if rep.sample:
        lines.append(f"  sample ({len(rep.sample)} of {rep.total}):")
        for row in rep.sample:
            lines.append(f"    {row['id']}  status={row['status']!r}  name={row['name']!r}")
    if rep.carriers:
        lines.append(
            f"  ⚠ {len(rep.carriers)} row(s) ALSO carry a supersession carrier "
            "(supersededBy/supersededAt) — the carrier is NOT touched:")
        for row in rep.carriers[:5]:
            lines.append(f"    {row['id']}")
    if rep.unaddressable:
        lines.append(
            f"  ⛔ {len(rep.unaddressable)} :Object row(s) have no `id` — the SDK "
            f"cannot address them ({REFUSE_UNADDRESSABLE}): "
            + ", ".join(rep.unaddressable[:5]))
    if rep.fanout:
        lines.append(
            f"  ⛔ {len(rep.fanout)} row(s) whose `id` is bound by more than one "
            f"node, or by a node carrying a :Point/:Subject label "
            f"({REFUSE_FANOUT}) — refused, never written (update_knowledge binds "
            "on the id, so the write would reach that sibling):")
        for row in rep.fanout[:5]:
            lines.append(f"    {row['id']}  nodes={row.get('nodes')!r}")
    if rep.pending:
        lines.append(
            f"  ⛔ {len(rep.pending)} row(s) may be LIVE-ONLY from a previous "
            "run that did not finish cleanly "
            f"({', '.join(rep.pending[:5])}{'…' if len(rep.pending) > 5 else ''}): "
            # ⛔ THE MARKER PROVES INTENT, NOT PROGRESS. An id is written to it
            # BEFORE the write door is called, so a killed run leaves ids whose
            # live write never happened at all — and the marker cannot tell those
            # apart from one whose live write landed with no durable record. Say
            # BOTH, or the report accuses a row of state it may not hold.
            "each of these was about to be rewritten when the previous run "
            "stopped, and this marker cannot tell how far it got: its live "
            "status may be the legacy word still, or the new one with no journal "
            "record behind it — in the latter case `rebuild_all` restores the "
            "legacy word. "
            "The `0 rows` reading on this graph is NOT a measurement until they "
            "are repaired — re-run `--execute` after `rebuild_all`. An id whose "
            "row no longer exists is discharged automatically once its journal "
            "record says rewritten or deleted; for any id that survives that, "
            "read the marker, confirm each one by hand, and delete the file — "
            "no replay can clear a row that is gone.")
    if rep.flag_count:
        lines.append(
            f"  note: {rep.flag_count} :Object row(s) carry the legacy `outdated` "
            "FLAG (n.outdated) — a different axis, NOT rewritten here:")
        for row in rep.flag_sample[:5]:
            lines.append(f"    {row['id']}  outdated={row['outdated']!r}")

    if rep.executed:
        lines.append("")
        lines.append(f"  rewritten: {len(rep.rewritten)} row(s) -> status={REWRITE_TO!r}")
        if rep.journal_delta is not None:
            lines.append(f"  journal records appended: {rep.journal_delta}")
        for fail in rep.failures:
            lines.append(f"  ⛔ FAILED {fail.get('id')}: {fail.get('error')}")
        lines.append("  after: " + ", ".join(
            f"{w}={rep.remaining.get(w, 0)}" for w in LEGACY_WORDS))
    else:
        lines.append("")
        # ⛔ Do not advise an arm that will REFUSE. `--execute` proves the READ
        # complete before its first write, so telling an operator to re-run on a
        # graph that fails those checks sends them to an exit-2 refusal. A
        # fan-out / dual-labelled row is NOT one of those (it is a per-row
        # refusal — the safe rows still get rewritten), so it does not gate this
        # advice; it is named beside them instead.
        blockers = bool(rep.truncated or rep.unaddressable)
        if rep.total == 0 and not rep.truncated and not rep.pending:
            lines.append(
                "  ⇒ 0 rows carry 'outdated' or 'retracted' on this graph. No rewrite "
                "is needed, and `assembly._RECALL_OBJECT_EXCLUDED_STATUSES`'s "
                "`outdated` member is dead weight HERE — the drop is a separate, "
                "sequenced change (#12855 step 3).")
        elif rep.pending and not blockers:
            # ⛔ NOT in `blockers`: a marker is not a refusal (`_refusal` does not
            # refuse it), so the refusal advice below would claim `--execute`
            # REFUSES this target when it runs, and would hide the one remedy
            # that works.
            lines.append(
                f"  ⇒ preview only; nothing was written. ⛔ {len(rep.pending)} row(s) "
                "may be LIVE-ONLY from a previous run (written without a durable "
                "journal record), so the reading above is NOT a measurement: run "
                "`rebuild_all` to restore the legacy word, then re-run `--execute`.")
        elif blockers:
            lines.append(
                "  ⇒ preview only; nothing was written. ⛔ `--execute` would REFUSE "
                "this target (see the refusals above): resolve them first — an "
                "incomplete read by a larger page/batch, a missing `id` at the "
                "writer — then re-run.")
        else:
            note = ("  ⚠ the row(s) flagged above are refused PER ROW: `--execute` "
                    "rewrites the clean rows and reports those by id (exit 1)."
                    if rep.fanout else "")
            lines.append(
                "  ⇒ preview only; nothing was written. Re-run with --execute "
                "to rewrite these rows through the journaled update path.")
            if note:
                lines.append(note)
    return "\n".join(lines)


# ── entry point ──────────────────────────────────────────────────────────


def _empty_flag(args) -> tuple[str, str] | None:
    """A target flag that selects a different store than the operator named.

    The same refusal `turn_parity_census.empty_graph_target_flag` makes, restated
    here in one line because this tool opens ONE store through the SDK and a
    set-but-empty flag would fall through to the embedded default while the
    operator believes they addressed a container.
    ⛔ AND `--uri` + `--db-path` TOGETHER IS THE SAME DEFECT IN A WORSE FORM: the
    SDK lets an explicit `db_path` win over the URI, so the run writes the
    EMBEDDED store while the operator believes it addressed the container — and
    the journal it appends to is whichever `--event-log-path` names, so a
    production journal can receive a record for a write that never touched the
    production graph. Refused rather than resolved, under its OWN reason token
    (the defect is ambiguity, not emptiness).

    Returns ``(reason_token, message)`` or ``None``.
    """
    if getattr(args, "uri", None) is not None and \
            getattr(args, "db_path", None) is not None:
        return (REFUSE_AMBIGUOUS_TARGET,
                "--uri and --db-path were both passed, and they name two stores")
    for name in ("uri", "db_path", "graph_name", "event_log_path"):
        value = getattr(args, name, None)
        if value is not None and not str(value).strip():
            return (REFUSE_EMPTY_FLAG,
                    f"--{name.replace('_', '-')} was passed EMPTY — an empty "
                    "target flag is not 'no flag', it selects a different store")
    return None


class Refused(Exception):
    """A pre-write refusal: nothing was written, and `main` exits 2.

    Raised from :func:`run` — the layer a suite drives — as well as answered by
    `main`, because a refusal reachable ONLY from the CLI is a refusal any other
    caller walks straight past, half-writing the population the refusal exists
    to protect.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason
        self.message = message


def _refusal(rep: Report) -> Refused | None:
    """The fail-closed pre-write gates, judged on the population to be written.

    Both mean "this run cannot reach the population it claims", and a partial
    rewrite of such a graph is the shape that leaves the `assembly` member
    load-bearing for HIDDEN rows — so both refuse BEFORE the first write.

    ⛔ `fanout` is deliberately NOT one of them. A row whose `id` is bound by a
    `:Point`/`:Subject` sibling is refused PER ROW (it is reported by id, and the
    safe rows are still rewritten), because promoting it to a whole-run refusal
    would leave the tool unable to rewrite anything on a graph holding one such
    row — the canonical instance is exactly that shape.
    """
    if rep.truncated:
        return Refused(
            REFUSE_TRUNCATED,
            "the paged read fell short of the store's own count, so this run does "
            "not know the population.")
    if rep.unaddressable:
        return Refused(
            REFUSE_UNADDRESSABLE,
            f"{len(rep.unaddressable)} :Object row(s) carry a legacy status word "
            "but no `id`, so the SDK cannot rewrite them; a partial rewrite would "
            "leave the filter member load-bearing.")
    return None


def run(sdk, *, execute: bool = False, sample: int = 20,
        report: Report | None = None) -> Report:
    """Collect, and (only when `execute`) rewrite. The testable core.

    `main` is argparse + connection + exit codes; every decision that matters is
    here, so a suite can drive the real graph and the real journal without
    re-implementing this tool's logic.

    `report` lets `--execute` act on the population its REFUSALS were checked
    against. Re-collecting here reopened a TOCTOU window: `main` proved the
    preview read complete, then wrote from a different read that was never
    checked.
    """
    proj = sdk._get_proj()
    rep = report if report is not None else collect(proj, sample=sample)
    rep.graph = getattr(proj, "graph_name", None)
    log = sdk._get_event_log()
    rep.journal = str(getattr(log, "path", None)) if log is not None else None
    # A previous run may have left rows LIVE-ONLY; that makes this graph's "0
    # rows" state unmeasurable, so it must travel with the report.
    rep.pending = _read_pending(rep.journal)
    if rep.pending is None:
        # An UNREADABLE marker is not "no marker" — collapsing the two lets a
        # permission error read as a clean graph, which is the false measurement
        # the marker exists to prevent. Fail closed, before any write.
        raise Refused(REFUSE_PENDING_UNREADABLE, (
            f"the durability marker {_pending_path(rep.journal)} exists but "
            "could not be read, so it is unknown whether a previous run left "
            "rows LIVE-ONLY. Nothing was written. Repair: both arms refuse until "
            "this file is readable, so it cannot be discharged by running the "
            "tool — fix what blocks the read (permissions, or a replacement file "
            "with one row id per line), and delete it only once every id in it "
            "either reads `deprecated` live or has a `deprecated` record as the "
            "LAST record for it in the journal. This tool will not guess."))
    if rep.pending and log is not None:
        # ⛔ The PREVIEW gets the same discharge test the write arm applies, or a
        # marker left by a killed run (naming only ids whose records landed)
        # withholds the measurement and prescribes `rebuild_all` — a destructive
        # journal replay — on a healthy graph. Read-only.
        rep.pending = _kept_pending(
            rep.pending, _population_ids(rep), log)
    if not execute:
        return rep
    # ⛔ THE REFUSALS ARE MADE HERE, not only in `main`. `main` answers them with
    # exit 2; a caller that reaches `run(execute=True)` directly gets the SAME
    # gate as an exception, so there is no second, unguarded write path — the
    # shape that half-wrote a graph in review (2026-11-17).
    if sdk._get_event_log() is None:
        raise Refused(
            REFUSE_NO_JOURNAL,
            "no journal is configured for this target, so every write would be "
            "live-only and the next `rebuild_all` would revert it.")
    refusal = _refusal(rep)
    if refusal is not None:
        raise refusal
    return rewrite(sdk, rep)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rewrite_object_status",
        description=("#12855: rewrite stored `outdated`/`retracted` Object status "
                     "words to `deprecated`, through the journal. Preview by "
                     "default; --execute writes."),
    )
    parser.add_argument("--uri", help="FalkorDB URI (default: $TORTOISE_DB_URI, else embedded)")
    parser.add_argument("--db-path", help="embedded DB path (default: resolved)")
    parser.add_argument("--graph-name", help="graph name to address (verbatim)")
    parser.add_argument("--event-log-path",
                        help=("the JSONL journal this target's rebuild replays "
                              "(default: $TORTOISE_EVENT_LOG_BASE_DIR derivation)"))
    parser.add_argument("--sample", type=int, default=20,
                        help="how many ids to print (default 20)")
    parser.add_argument("--execute", action="store_true",
                        help="actually rewrite the rows (default: preview only)")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    bad = _empty_flag(args)
    if bad is not None:
        reason, message = bad
        sys.stderr.write(
            f"rewrite_object_status: REFUSED {reason}: {message}. "
            "Nothing was read and nothing was written.\n")
        return 2

    import os

    if args.uri:
        # The SDK resolves its target from the environment, so --uri must land
        # there or the read and the write could name two different stores.
        os.environ["TORTOISE_DB_URI"] = args.uri

    from tortoise.config import _resolve_event_log_path
    from tortoise.sdk import TortoiseSDK

    journal = args.event_log_path
    if journal is None:
        journal = _resolve_event_log_path(namespace=None, graph_name=args.graph_name)
    if journal is None:
        # ⛔ Name a repair that WORKS. `_resolve_event_log_path` returns None
        # unconditionally when `graph_name is None` (``config.py``: the base dir
        # alone derives no path), so "set TORTOISE_EVENT_LOG_BASE_DIR" on its own
        # sends the operator back to the identical refusal — and this is the
        # safety refusal standing between them and a raw `SET`.
        base = os.environ.get("TORTOISE_EVENT_LOG_BASE_DIR")
        if base and args.graph_name is None:
            repair = (f"TORTOISE_EVENT_LOG_BASE_DIR is set ({base}) but a graph "
                      "key is required to derive the path: pass "
                      "--graph-name <graph> (or name the file with "
                      "--event-log-path).")
        else:
            repair = ("Pass --event-log-path <path>, or pass --graph-name "
                      "<graph> together with TORTOISE_EVENT_LOG_BASE_DIR.")
        sys.stderr.write(
            f"rewrite_object_status: REFUSED {REFUSE_NO_JOURNAL}: no journal is "
            "configured for this target, so every write would be live-only and "
            f"the next rebuild would revert it. {repair} Nothing was written.\n")
        return 2

    # ⛔ ONE run at a time on this journal (see `_acquire_run_lock`). Held across
    # both arms, so the preview cannot be interleaved with another run's writes.
    try:
        _lock = _acquire_run_lock(journal, required=args.execute)
    except Refused as refused:
        sys.stderr.write(
            f"rewrite_object_status: REFUSED {refused.reason}: "
            f"{refused.message}\n")
        return 2

    sdk = TortoiseSDK(db_path=args.db_path, graph_name=args.graph_name,
                      event_log_path=journal)
    try:
        if sdk._get_event_log() is None:              # pragma: no cover - parity
            sys.stderr.write(
                f"rewrite_object_status: REFUSED {REFUSE_NO_JOURNAL}: the SDK "
                "reports no journal for this target. Nothing was written.\n")
            return 2

        # ⛔ ONE handler for BOTH arms. `run` raises `Refused` on the PREVIEW path
        # too (an unreadable durability marker is judged before the read is
        # returned), and an uncaught one is a traceback with exit 1 and no
        # `REFUSED` line — for the same target `--execute` refuses with exit 2.
        try:
            preview = run(sdk, execute=False, sample=args.sample)
            rep = preview
            if args.execute:
                # Fail closed BEFORE the first write. The gates themselves live in
                # `_refusal` (and are re-applied inside `run`), so a non-CLI caller
                # cannot take a second, unguarded write path.
                rep = run(sdk, execute=True, sample=args.sample, report=preview)
        except Refused as refused:
            sys.stderr.write(
                f"rewrite_object_status: REFUSED {refused.reason}: "
                f"{refused.message} Nothing was written.\n")
            return 2
    finally:
        sdk.close()
        _release_run_lock(_lock)

    if args.json:
        print(json.dumps(rep.to_dict(), indent=2, default=str))
    else:
        print(render(rep))

    if not rep.executed:
        # A preview is the MEASUREMENT that unblocks dropping the `assembly`
        # member, so it must not exit 0 over a read it could not complete —
        # otherwise the `0 rows` sentence below reads as a complete answer.
        return 1 if (rep.truncated or rep.unaddressable or rep.fanout
                      or rep.pending) else 0
    leftover = sum(rep.remaining.get(w, 0) for w in LEGACY_WORDS)
    # ⛔ The record is part of the write: a run whose journal grew by fewer
    # records than rows it rewrote has live-only rows that the next
    # `rebuild_all` reverts, whatever the per-row probes managed to see.
    record_short = (rep.journal_delta is None
                    or rep.journal_delta < len(rep.rewritten))
    if (rep.failures or rep.truncated or leftover or rep.unaddressable
            or record_short or rep.pending):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
