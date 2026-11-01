"""#6388 — the DRAIN for the ``session-events`` substrate (the OTHER half of #10479).

The measured problem (#6388, re-measured in the PR):

    ~/.tortoise/session-events/   day-addressed JSONL, one record per session
                                  (``{session_id, conversation, metadata,
                                  harness, machine_id, model}``), 61,997 records
                                  / 516 MB on the box that filed the issue.
                                  No rotation, no consumer, and no cursor.

``reflect-hook`` appends a record on shutdown, and NOTHING ever read it back for
filing: the recovery sweep (:func:`tortoise.store_sync.run_sweep`) drains the
capture spool's ``entries/`` and Pi's own session log, and its module docstring
*claims* this substrate as item 1 while ``capture_spool.flush_spool`` only ever
walks ``<spool>/entries/*.meta.json``. That is the named absence this module
closes: the substrate had a WRITER and no DRAIN.

Four constraints shaped the shape of it, and each one is a rule, not a style:

**One queue, one drainer (#1178).** This is a LEG of the existing recovery
sweep, called from ``run_sweep`` beside the spool leg and the log leg — not a
scheduler, not a daemon, and not a second cursor store. It reuses, rather than
re-implements, everything that makes filing safe: the transport callable
(``__main__._session_post``), the wire normalisers and the failure classifier
from :mod:`tortoise.capture_spool`, and the backstop's ONE dead-letter
substrate (``<store-sync>/dead-letter.jsonl``), which the capture health line
already counts.

**Bounded, never one unbounded pass.** ``MAX_RECORDS_PER_RUN`` caps a pass, and
a TRANSIENT refusal STOPS it on the spot: 61,997 records against a rate-limited
API is the hammering the issue forbids, and once a 402/429/5xx is seen every
further POST in the pass is guaranteed to fail. What the pass did not reach
stays exactly where it was, un-consumed, and is reported as ``queued``.

**No record may occupy the head forever.** A per-file cursor (a consumed-record
count) is the queue's own memory, so a record is only crossed once it is FILED
or DEAD-LETTERED — never dropped to make a count fall (#3870: the spool is
type-2 user data kept until the user deletes it, so this module deletes
nothing, ever). Two escape hatches keep that rule from becoming a trap:

* a record that can never be filed (a torn line in a settled file, no session
  id, no turns, a permanent 4xx) is DEAD-LETTERED and crossed, so a poison
  record cannot block the ones behind it — the discriminating failure mode a
  naive in-order drain has;
* a record that keeps drawing a TRANSIENT refusal is charged a STRIKE
  (``MAX_STRIKES``). Until then it ends the pass (the rate-limit rule above);
  after that it is SKIPPED for the remainder of each pass — un-consumed, never
  dropped, reported as ``stuck`` — so one record the server will not take in
  this billing period cannot gate the other 61,996 forever.

**A verdict that did not persist is not a verdict.** The dead-letter write is
checked: a record whose dead letter could not be written is a DEFERRAL, never a
"handled" — crossing a cursor on a row that does not exist is how a record
becomes permanently invisible. The same for the cursor itself: a cursor write
that fails is reported (``cursor_error``) by every caller, because the
alternative is an unreported re-POST storm.

Idempotency: the server upserts on ``session_id``, and the cursor is written
after the pass, so a kill mid-pass re-POSTs a record and converges to one
Session — the same property ``capture_spool`` relies on.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
import zlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from tortoise.capture_spool import (
    PostOutcome,
    _atomic_write,
    _normalise_harness,
    _normalise_machine_id,
    _normalise_model,
    _normalise_session_id,
    classify_failure,
)

__all__ = [
    "CURSOR_FILE",
    "DEAD_LETTER_FILE",
    "DEAD_LETTER_MAX_ROWS",
    "MAX_RECORDS_PER_RUN",
    "MAX_STRIKES",
    "SETTLE_GRACE_SECONDS",
    "STUCK_RETRIES_PER_PASS",
    "cursor_dir",
    "default_session_events_dir",
    "drain_session_events",
    "format_progress",
    "read_dead_letter_count",
]

#: How many records ONE pass may POST. The measured rate that made the backlog
#: (222–263 records/day on the box in #6388) is well inside this, so a healthy
#: box drains in ONE pass; a blocked box that comes back online works the
#: backlog down over passes instead of firing 61,997 POSTs at once.
MAX_RECORDS_PER_RUN = 200
#: Consecutive TRANSIENT refusals a single record may END A PASS with. Past it,
#: the record is skipped (un-consumed) for the rest of each pass and reported as
#: ``stuck``: a 402 whose estimated cost never fits the tier, or a payload the
#: transport always throws on, must not gate the queue behind it forever.
MAX_STRIKES = 3
#: How many known-stuck records one pass may still PROBE. Zero would never let a
#: record recover when the refusal lifts (a quota top-up); more than one would
#: spend the POST budget on records that are not draining.
STUCK_RETRIES_PER_PASS = 1
#: A day file whose mtime is older than this has no writer on it any more, so an
#: unparseable LAST line is a torn record rather than one being appended RIGHT
#: NOW. The same settle rule ``tools/seal_session_events.py`` uses before it
#: seals a day ("sealing a live append is how a record is torn").
SETTLE_GRACE_SECONDS = 3600
#: The per-day consumed-record count. BESIDE the backstop's other bookkeeping
#: (``<store-sync>/``), so every reader that already looks there sees it.
CURSOR_FILE = "session-events-cursor.json"
#: The backstop's ONE dead-letter substrate (``cursor-store.ts::DEAD_LETTER_FILE``
#: names the same file). Not a second surface: the capture health line already
#: counts the rows in this file, so a record that can never be filed appears on
#: the line an operator already reads, with no new reader.
DEAD_LETTER_FILE = "dead-letter.jsonl"
DEAD_LETTER_MAX_ROWS = 500
#: ``TORTOISE_SESSION_EVENTS_DIR`` overrides the substrate root, so a box whose
#: substrate is not beside its spool is still drainable.
SESSION_EVENTS_DIR_ENV = "TORTOISE_SESSION_EVENTS_DIR"
CURSOR_VERSION = 2


def default_session_events_dir(env: Mapping[str, str] | None = None) -> Path:
    """The substrate ``reflect-hook`` appends to: ``<spool-parent>/session-events``.

    Derived from the SPOOL root's parent — the same derivation
    ``capture_spool._store_sync_dir`` uses — rather than from ``HOME`` directly.
    Two reasons, and the second is not cosmetic:

    1. The substrate and the spool are siblings under ``~/.tortoise/``, so one
       input (the spool root) names both; a caller that redirects the spool must
       not keep filing against the old substrate.
    2. It inherits ``capture_spool.spool_dir``'s PYTEST guard: under pytest the
       spool root is derived from the test id, so a test can never drain the
       developer machine's REAL day files (#3721). Reading ``$HOME`` directly
       did exactly that — a suite run posted this machine's own 1,900-record
       backlog through a stubbed transport, and five unrelated tests failed on
       the extra POSTs.

       ⚠️ What the guard gives here is ONE SHARED root, not one per test:
       ``spool_dir()`` is ``<tmp>/tortoise-capture-spool-tests/<test-digest>``
       and this derivation takes ``.parent``, which STRIPS the digest — so every
       test in a run shares ``<tmp>/tortoise-capture-spool-tests/session-events``,
       and ``cursor_dir()`` likewise shares ``…/store-sync`` (which is also where
       the capture-spool tests' health and dead-letter assertions look). An
       earlier version of this docstring claimed per-test isolation, which the
       derivation does not provide (P3, found in review). The conclusion that
       still holds is the one that matters for #3721 — a test never touches the
       REAL substrate — and the sharing is latent rather than an active flake
       only because nothing writes day files there today. A test that needs its
       own substrate must pin ``TORTOISE_SESSION_EVENTS_DIR`` (which is exactly
       what this suite's own cases do).

    ``TORTOISE_SESSION_EVENTS_DIR`` overrides everything, for a box whose
    substrate is not beside its spool.
    """
    from tortoise.capture_spool import spool_dir

    source: Mapping[str, str] = os.environ if env is None else env
    override = source.get(SESSION_EVENTS_DIR_ENV)
    if override:
        return Path(override)
    return spool_dir(env).parent / "session-events"


def cursor_dir(env: Mapping[str, str] | None = None) -> Path:
    """Where the cursor and the dead letters live — the backstop's own dir."""
    from tortoise.capture_spool import _store_sync_dir, spool_dir

    return _store_sync_dir(spool_dir(env))


# ── Files and records ──────────────────────────────────────────────────────


def _day_key(path: Path) -> str:
    """The QUEUE identity of a day file: its day, not its path.

    ``2026-10-01.jsonl`` and ``archive/2026-10-01.jsonl.gz`` are the SAME queue
    entry at two moments in the seal lifecycle (``tools/seal_session_events.py``
    gzips a settled day and removes the plain file). Keying by path re-filed a
    whole day the moment it was sealed and left two cursor rows for one day.
    """
    name = path.name
    for suffix in (".jsonl.gz", ".jsonl", ".gz"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _shape_of(path: Path) -> str:
    """Which of the day's two files this is — its index space, not its name.

    ``archive/X.jsonl.gz`` (``"gz"``) and ``X.jsonl`` (``"plain"``) are the same
    QUEUE entry across a seal, but two different FILES; see
    :meth:`_Cursor.note_shape` for why the cursor has to know which one it is
    looking at.
    """
    return "gz" if path.name.endswith(".gz") else "plain"


def _candidate_files(root: Path) -> list[Path]:
    """Every day file the substrate holds, OLDEST first.

    Both shapes count, and that is load-bearing: a drain that read only
    ``*.jsonl`` would SILENTLY SKIP every day sealed before it was drained —
    records neither filed nor dead-lettered, which is precisely the state this
    module exists to make unreachable. A day present in BOTH shapes (a seal that
    landed between our listing and its unlink) is read from the plain file only:
    the same records twice would be two POSTs of one session.
    """
    found: dict[str, list[Path]] = {}
    for directory in (root / "archive", root):
        try:
            names = sorted(p.name for p in directory.iterdir() if p.is_file())
        except OSError:
            continue
        for name in names:
            if not (name.endswith(".jsonl") or name.endswith(".jsonl.gz")):
                continue
            path = directory / name
            found.setdefault(_day_key(path), []).append(path)
    # PLAIN WINS when a day is present in both shapes (a seal that landed between
    # our listing and its unlink, or an in-place gzip): the plain file is the one
    # a writer is appending to, and reading the same records twice would be two
    # POSTs of one session. Chosen explicitly rather than by scan order.
    picked = [next((p for p in paths if not p.name.endswith(".gz")), paths[-1])
              for paths in found.values()]
    return sorted(picked, key=_day_key)


def _read_lines(path: Path) -> list[str] | None:
    """A day file's non-empty lines, or ``None`` when the file cannot be read.

    ``None`` is NOT "no records": a corrupt gzip or an EACCES day file is a
    whole day that is neither filed nor dead-lettered, so the pass must report
    it (``unreadable``) instead of counting it as an empty queue — silently
    yielding ``[]`` made a lost day read as ``queued 0`` (found in review).
    """
    try:
        opener = gzip.open if path.name.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as fh:  # type: ignore[operator]
            return [line for line in fh.read().split("\n") if line.strip()]
    except (OSError, EOFError, gzip.BadGzipFile, zlib.error):
        # `zlib.error` is the DEFLATE-corrupt case (a flipped byte inside a valid
        # gzip header): neither an OSError nor a `BadGzipFile`, so it escaped
        # `drain_session_events` entirely — the pass aborted before the cursor
        # write, and a corrupt EARLIEST day meant no later day was ever read
        # (every record stranded, no `unreadable` report); a corrupt LATEST day
        # meant the healthy days were re-POSTed on every pass. `--dry-run` had
        # the same hole. A read failure MUST be a reported day, not a crash.
        return None
    except Exception:  # a decompressor bug is a DAY, never a crashed pass
        return None


def _count_records(path: Path, only_session_id: str | None = None) -> int | None:
    """How many records a day file holds. No JSON parse — this is QUEUE DEPTH.

    With ``only_session_id`` the count IS a parse, because the question changes
    from "how many records are in this day" to "how many of them is this run
    asked to file" — and a plan that answered the first while the pass answered
    the second would be two answers to one question (the defect `--dry-run` had
    for `cursor_dir`). Unparseable lines are counted as NOT matching a named
    session: `--file X` must not promise to file a line it cannot attribute.
    """
    lines = _read_lines(path)
    if lines is None:
        return None
    if only_session_id is None:
        return len(lines)
    return sum(1 for line in lines if _sid_of(line) == only_session_id)


def _sid_of(line: str) -> str | None:
    """The usable ``session_id`` of one raw line, or ``None``.

    One definition, used by the `--file` filter in BOTH the plan and the pass, so
    the two cannot disagree about which records are in scope.
    """
    try:
        record = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(record, dict):
        return None
    raw = record.get("session_id")
    return raw if isinstance(raw, str) and raw.strip() else None


def _window(conversation: Any) -> list[dict]:
    """The turns to POST — the last ``MAX_SESSION_TURNS``, each clipped and MARKED.

    Keeping the MOST RECENT turns matches every other capture leg
    (``capture_spool`` snapshots, ``extractTurns`` in the Pi leg): recent context
    is what memory wants, and the server's cap is what makes an over-long record
    a permanent 4xx instead of a legal filing.

    ⛔ NEITHER BOUND IS DECLARED HERE, and neither cut is silent (#4897). Both
    come from the modules that own them, the same way the transcript leg
    (``__main__.py``, the ``window_turns`` block) imports them rather than
    restating them:

    * ``tortoise.quota.MAX_SESSION_TURNS`` is the SERVER's bound —
      ``hosted_api`` raises a hard 400 above it, and ``classify_failure`` reads
      every 4xx outside ``{402, 408, 425, 429, 409}`` as ``permanent``, so this
      record would be dead-lettered AND the cursor crossed. A local copy that
      drifts LOWER therefore dead-letters every record in the band on its first
      pass — the exact outcome the window exists to prevent.
    * ``tortoise.sdk._clip_capture_turn_content`` is the ONE definition of the
      stored window. A bare ``content[:5000]`` slice both re-declared the cap and
      cut SILENTLY: a turn sliced to EXACTLY the cap is not *over* it, so the
      server's own re-windowing adds no marker either, and a reader could not
      tell a complete turn from a cut one. The clip reserves its marker INSIDE
      the cap, so the cut survives the server's re-application of the window.

    Applied to the text exactly as the record carries it and exactly once — this
    leg never re-windows an already-scrubbed body (the false-marker defect of
    #4897's P1). A turn that already sits at the cap is returned verbatim, so a
    second application is a no-op rather than a fresh marker.
    """
    from tortoise.quota import MAX_SESSION_TURNS
    from tortoise.sdk import _clip_capture_turn_content

    turns: list[dict] = []
    if not isinstance(conversation, list):
        return turns
    for item in conversation:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if not isinstance(role, str) or not role or not isinstance(content, str):
            continue
        if not content.strip():
            continue
        turns.append({"role": role,
                      "content": _clip_capture_turn_content(content)})
        if len(turns) > MAX_SESSION_TURNS:
            turns.pop(0)
    return turns


# ── Cursor + dead letters ──────────────────────────────────────────────────


class _Cursor:
    """The per-day queue state: ``{consumed, strikes}`` per day.

    ``strikes`` is a *consecutive* count of transient refusals, per record index.
    It is the one thing that makes "stop the pass on a refusal" (correct for a
    rate-limited API) not also mean "one unfilable record gates the queue for
    ever".
    """

    def __init__(self, root: str, days: dict[str, dict]) -> None:
        self.root = root
        self.days = days

    @classmethod
    def load(cls, store: Path, root: Path) -> _Cursor:
        try:
            parsed = json.loads((store / CURSOR_FILE).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return cls(str(root), {})
        if not isinstance(parsed, dict):
            return cls(str(root), {})
        # A cursor from a DIFFERENT substrate root must never be applied to this
        # one: the keys are day names, so two roots with a `2026-10-01.jsonl`
        # would cross each other's records — silently skipping records that were
        # never filed (found in review).
        if str(parsed.get("root") or "") != str(root):
            return cls(str(root), {})
        days: dict[str, dict] = {}
        raw = parsed.get("days")
        if isinstance(raw, dict):
            for key, value in raw.items():
                if not isinstance(value, dict):
                    continue
                consumed = value.get("consumed")
                strikes = value.get("strikes")
                crossed = value.get("crossed")
                total = value.get("total")
                days[str(key)] = {
                    # A hand-edited / corrupt count is read as "nothing
                    # consumed": the safe direction is a re-POST (idempotent
                    # upsert), never a silently skipped record.
                    "consumed": consumed if isinstance(consumed, int) and consumed >= 0 else 0,
                    "crossed": sorted({int(i) for i in crossed
                                       if isinstance(i, int) and i >= 0})
                    if isinstance(crossed, list) else [],
                    "total": total if isinstance(total, int) and total >= 0 else -1,
                    "probes": value.get("probes") if isinstance(value.get("probes"), int)
                    and value.get("probes") >= 0 else 0,
                    "strikes": {
                        str(k): v for k, v in (strikes or {}).items()
                        if isinstance(v, int) and v > 0
                    } if isinstance(strikes, dict) else {},
                    # WHICH SHAPE this index space belongs to (see
                    # `note_shape`). Absent on a cursor written before the
                    # dual-shape fix, which is treated as "unknown", not as a
                    # transition.
                    "shape": value.get("shape") if value.get("shape") in ("plain", "gz")
                    else None,
                }
        return cls(str(root), days)

    def day(self, key: str) -> dict:
        return self.days.setdefault(key, {"consumed": 0, "crossed": [], "strikes": {},
                                          "total": -1, "probes": 0, "shape": None})

    def note_shape(self, key: str, shape: str) -> str | None:
        """Bind this day's cursor to the SHAPE the index space belongs to.

        The cursor is keyed by DAY and ``consumed`` is an index into that day's
        file — but a day has TWO possible files and they are not one index
        space. ``archive/X.jsonl.gz`` and ``X.jsonl`` are the same queue entry
        only while the SEAL is what separates them: the seal moves the file, so
        the archive holds exactly the records the plain file held. A plain file
        that appears AFTER an archive was drained is a DIFFERENT file — the
        substrate's writer re-created the day for a session that ended after the
        seal — and its records start at index 0 again. Applying the archive's
        ``consumed`` to it skips them all, silently (reproduced: 4 records
        neither filed nor dead-lettered, ``queued 0``, stable across passes).

        So an ``gz → plain`` transition RESETS this day's index space. Every
        other transition keeps it:

        * ``plain → gz`` is the seal, the archive a gzip of the plain file we
          just drained — same records, same indices, nothing re-filed (the
          idempotency `_day_key` exists for).
        * ``plain → plain`` is an append to the same file — a valid prefix.
        * ``gz → gz`` is a re-seal of the same day — a valid prefix too.

        The unsafe direction is the one comparable systems resolve the same way:
        a consumer position is per FILE IDENTITY, and when identity cannot be
        proven the read is repeated rather than skipped (Logstash's ``sincedb``
        keys on inode + major + minor for exactly this, and documents that on
        inode reuse "a previously seen file will be read again"). A re-read is
        idempotent — the server upserts on ``session_id`` — whereas a skip is a
        lost capture (#3870).

        Returns a human-readable detail when it reset, else ``None``.
        """
        day = self.day(key)
        previous = day.get("shape")
        day["shape"] = shape
        if previous != "gz" or shape != "plain":
            return None
        day["consumed"] = 0
        day["crossed"] = []
        day["strikes"] = {}
        day["probes"] = 0
        return (f"{key}: a plain file replaced the drained archive — "
                f"re-filing the day (a new index space)")

    def consumed(self, key: str) -> int:
        return int(self.day(key).get("consumed") or 0)

    def crossed(self, key: str) -> set[int]:
        return {int(i) for i in (self.day(key).get("crossed") or [])}

    def is_done(self, key: str, index: int) -> bool:
        """Already FILED or DEAD-LETTERED in an earlier pass — never re-POSTed.

        ``consumed`` is the contiguous prefix; ``crossed`` holds the indices
        crossed OUT of order (a record that drew a strike and was skipped while
        the ones behind it filed). Without the second half, ``consume(index+1)``
        on the record after a skipped one silently crosses the SKIPPED record —
        leaving it in neither state, reported green (found in re-review).
        """
        return index < self.consumed(key) or index in self.crossed(key)

    def strikes(self, key: str, index: int) -> int:
        return int((self.day(key).get("strikes") or {}).get(str(index), 0))

    def strike(self, key: str, index: int) -> int:
        strikes = self.day(key)["strikes"]
        strikes[str(index)] = int(strikes.get(str(index), 0)) + 1
        return strikes[str(index)]

    def clear_strike(self, key: str, index: int) -> None:
        self.day(key)["strikes"].pop(str(index), None)

    def consume(self, key: str, index: int) -> None:
        """Mark one record FILED or DEAD-LETTERED.

        Never advances the prefix past a record that is not crossed: the prefix
        absorbs only the contiguous run, and anything ahead of it waits in
        ``crossed`` until the gap fills.
        """
        day = self.day(key)
        self.clear_strike(key, index)
        consumed = int(day.get("consumed") or 0)
        if index < consumed:
            return
        crossed = {int(i) for i in (day.get("crossed") or [])}
        if index == consumed:
            consumed += 1
            while consumed in crossed:
                crossed.discard(consumed)
                consumed += 1
            day["consumed"] = consumed
        else:
            crossed.add(index)
        day["crossed"] = sorted(crossed)

    def next_probe(self, key: str) -> int:
        """This day's rotating probe ordinal, advanced once per pass."""
        day = self.day(key)
        ordinal = int(day.get("probes") or 0)
        day["probes"] = ordinal + 1
        return ordinal

    def set_total(self, key: str, total: int) -> None:
        self.day(key)["total"] = int(total)

    def queued(self, key: str, total: int) -> int:
        """Records of this day that are NEITHER filed nor dead-lettered.

        Computed from the cursor, not by subtracting a strike count: a struck
        record that was skipped is still queued, and a crossed one is not, so
        subtracting `stuck` (as a first cut did) both under- and over-counted.
        """
        consumed = self.consumed(key)
        crossed = self.crossed(key)
        done = consumed + sum(1 for i in crossed if i >= consumed)
        return max(0, total - done)

    def serialise(self) -> str:
        return json.dumps(
            {"version": CURSOR_VERSION, "root": self.root,
             "days": dict(sorted(self.days.items()))},
            indent=1, sort_keys=True) + "\n"


def _write_cursor(store: Path, cursor: _Cursor) -> None:
    # The directory may not exist yet (a fresh box has no `<store-sync>/` at all
    # — that absence is #6388's own evidence), and `_atomic_write` opens its temp
    # INSIDE it: without this the write raised ENOENT and every pass re-filed the
    # same records from index 0.
    store.mkdir(parents=True, exist_ok=True, mode=0o700)
    _atomic_write(store / CURSOR_FILE, cursor.serialise())


def _append_dead_letter(store: Path, row: dict) -> bool:
    """Append ONE dead-letter row. Returns whether the row LANDED.

    ``O_APPEND``, single line, no read-modify-rewrite: the log half's worker
    (``cursor-store.ts::recordDeadLetter``) writes the same file, and a
    truncate-then-write here would erase rows it committed between our read and
    our write (found in review).

    ⚠️ STATED RESIDUAL: the LOG half's writer (``cursor-store.ts:270-293``) folds
    and prunes this shared file (``rows.slice(-500)`` + truncate+rewrite), so a
    row this drain appended can be erased by it before a reader looks — and rows
    older than its 500-row window age out. This drain crosses a record once its
    row LANDED, which is the strongest guarantee available without owning that
    writer; the rows are therefore evidence-at-write-time, not a permanent
    archive. Giving this leg its own append-only file would close it, at the cost
    of a second dead-letter surface (#1178's spirit) — a decision left to the
    review of that trade, not taken silently here.
    """
    # `ensure_ascii=True`: a lone surrogate is legal JSON (the TS writer's
    # `JSON.stringify` emits exactly that form for an unpaired code unit) and
    # `.encode("utf-8")` raises `UnicodeEncodeError` on it — a ValueError, not an
    # OSError — which escaped this function and aborted the WHOLE drain before
    # the cursor write, so that day never drained and every pass re-POSTed the
    # prefix. ASCII escaping makes the row writable and keeps the failure list
    # short of one more class.
    data = (json.dumps(row, ensure_ascii=True) + "\n").encode("utf-8")
    try:
        store.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(store / DEAD_LETTER_FILE,
                     os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            written = 0
            while written < len(data):
                written += os.write(fd, data[written:])
            os.fsync(fd)
        finally:
            os.close(fd)
    except (OSError, ValueError):
        return False
    return True


def read_dead_letter_count(store: Path | None = None) -> int:
    """Rows in the shared dead-letter substrate (absent/unreadable ⇒ 0)."""
    directory = store if store is not None else cursor_dir()
    try:
        text = (directory / DEAD_LETTER_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0
    return sum(1 for line in text.split("\n") if line.strip())


# ── The drain ──────────────────────────────────────────────────────────────

PostFn = Callable[[dict], PostOutcome]


def drain_session_events(
    post: PostFn,
    *,
    root: Path | str | None = None,
    store: Path | str | None = None,
    max_records: int = MAX_RECORDS_PER_RUN,
    exclude_session_id: str | None = None,
    only_session_id: str | None = None,
    dry_run: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """One BOUNDED pass over the ``session-events`` substrate.

    ``post`` is the recovery sweep's own transport (``_session_post(api_key,
    api_url)``): a ``payload -> PostOutcome`` callable, so this leg files through
    the same wire path as every other capture and never grows its own.

    ``only_session_id`` scopes the pass to ONE session — the ``--file`` half of
    the sweep CLI, derived from the single file it was given. It must be
    honoured here or `store-sync --file <session>` drains the WHOLE
    session-events backlog, which is the "flag half-applied" class `--file` was
    already fixed for on the spool and log legs (#3515 review). An unselected
    record is skipped WITHOUT a POST and stays queued (never crossed), so the
    scoped run is non-destructive for every other session, and it is counted as
    ``unselected`` — ``queued`` then answers only what THIS run still owes, so a
    scoped run reports ``filed 1, unselected 2, queued 0`` rather than claiming
    either that the backlog is empty or that it owes work it was never given.

    Returns the run's progress record — ``filed``, ``deferred``,
    ``dead_lettered``, ``held``, ``unselected``, ``consumed``, ``queued``
    (records still undelivered), ``unreadable`` (whole days this pass could not
    read at all) and ``stopped`` (``"batch"`` = the bound, ``"refused"`` = a
    transient refusal ended the pass). The absence of exactly this record is why
    tens of thousands of records accumulated invisibly (#6388).
    """
    root_path = Path(root) if root is not None else default_session_events_dir()
    store_path = Path(store) if store is not None else cursor_dir()
    cap = max(0, int(max_records))
    clock = now if now is not None else time.time()

    summary: dict[str, Any] = {
        "status": "drained",
        "root": str(root_path),
        "files": 0,
        "filed": 0,
        "deferred": 0,
        "dead_lettered": 0,
        "held": 0,
        "unselected": 0,
        # 2xx responses that STORED the turns and extracted nothing (#4197's
        # shape, observed here rather than only on the spool leg).
        "degraded": 0,
        "consumed": 0,
        "queued": 0,
        "stuck": 0,
        "stuck_retries": 0,
        "posts": 0,
        "verdicts": 0,
        "unreadable": [],
        "stopped": None,
        "dead_letters_total": 0,
        "cursor_error": None,
        "detail": "",
    }

    files = _candidate_files(root_path)
    summary["files"] = len(files)
    cursor = _Cursor.load(store_path, root_path)

    if dry_run:
        # A plan answers "how big is the backlog" — so it counts, and moves
        # nothing. Reported by both callers (`--dry-run` on the sweep CLI).
        summary["status"] = "dry_run"
        queued = 0
        for path in files:
            total = _count_records(path)
            if total is None:
                summary["unreadable"].append(str(path))
                continue
            # The plan must answer the SAME question the real pass does, so it
            # applies the same index-space binding — in memory only, since a
            # dry run writes no cursor.
            key = _day_key(path)
            cursor.note_shape(key, _shape_of(path))
            if only_session_id is None:
                queued += cursor.queued(key, total)
            else:
                # `cursor.queued` subtracts a DAY's consumed prefix from a DAY's
                # total; under a `--file` filter both of those are the unfiltered
                # quantities, so the plan has to ask per record instead — the
                # same predicate the pass uses, or the two answers to
                # "how much is left" diverge.
                lines = _read_lines(path) or []
                queued += sum(1 for i, line in enumerate(lines)
                              if not cursor.is_done(key, i)
                              and _sid_of(line) == only_session_id)
        summary["queued"] = queued
        summary["detail"] = "plan only — nothing was posted and no cursor moved"
        summary["dead_letters_total"] = read_dead_letter_count(store_path)
        return summary

    if not files:
        summary["dead_letters_total"] = read_dead_letter_count(store_path)
        return summary

    stopped: str | None = None

    for path in files:
        key = _day_key(path)
        lines = _read_lines(path)
        if lines is None:
            # A whole day that cannot be read is REPORTED, never counted as an
            # empty queue: its records are neither filed nor dead-lettered, and
            # an operator has to know which day that is.
            summary["unreadable"].append(str(path))
            continue
        total = len(lines)
        # BEFORE any index is read: bind this day's cursor to the shape it was
        # built from. A plain file that replaced a drained archive is a NEW
        # index space, and applying the old `consumed` to it skipped every new
        # record (see `_Cursor.note_shape`).
        rebound = cursor.note_shape(key, _shape_of(path))
        cursor.set_total(key, total)
        if rebound:
            summary["detail"] = rebound
        if cursor.consumed(key) > total:
            # The file is SHORTER than the cursor claims (a restored or rewritten
            # day). The safe reading is "nothing is known about these records":
            # re-file the day rather than cross records that were never filed.
            cursor.day(key)["consumed"] = 0
            cursor.day(key)["crossed"] = []
            # The STRIKES/`probes` state belongs to the index space being
            # discarded, exactly as `note_shape` already treats it there: stale
            # strikes from the previous space made unrelated records in the
            # rewritten day read as `stuck` sooner (found in review). Nothing was
            # dropped, but the fairness was wrong.
            cursor.day(key)["strikes"] = {}
            cursor.day(key)["probes"] = 0
            summary["detail"] = f"{key}: cursor exceeded the file, re-filing"
        settled = _file_age(path, clock) > SETTLE_GRACE_SECONDS
        # The probe slots: `STUCK_RETRIES_PER_PASS` struck records per day file,
        # chosen by a rotating ordinal so every struck record is eventually
        # probed again. The constant was documented and exported but never READ —
        # one hard-coded slot meant raising it changed nothing (found in review).
        struck = [i for i in range(cursor.consumed(key), total)
                  if cursor.strikes(key, i) >= MAX_STRIKES]
        probe_indices: set[int] = set()
        if struck:
            ordinal = cursor.next_probe(key)
            for slot in range(min(max(0, STUCK_RETRIES_PER_PASS), len(struck))):
                probe_indices.add(struck[(ordinal + slot) % len(struck)])
        index = -1
        for line in lines:
            index += 1
            if cursor.is_done(key, index):
                continue
            if summary["posts"] >= cap or summary["verdicts"] >= cap:
                # TWO counters, because a dead letter is not always a POST.
                # `no_session_id` / `no_conversation` / `unparseable_record` are
                # dead-lettered WITHOUT touching the wire, so a day of 62,000
                # malformed lines did 62,000 `fsync`s into the shared
                # `dead-letter.jsonl` in ONE pass while `posts` stayed 0 — the
                # bound was on the wrong quantity (found in review). `verdicts`
                # counts records given a TERMINAL verdict, POSTed or not.
                stopped = stopped or "batch"
                break
            if cursor.strikes(key, index) >= MAX_STRIKES and index not in probe_indices:
                # A known-stuck record would spend the whole POST budget on
                # itself every pass, so the cap could never reach a not-yet-
                # struck record — the escape hatch defeated exactly when it is
                # needed (found in re-review). It is skipped WITHOUT a POST and
                # stays queued.
                #
                # The ONE probe per pass is ROTATED (`probe_index`), not given to
                # the first struck record in scan order: a single global slot
                # meant only that record was ever probed, so a record that would
                # recover (a quota top-up) stayed unfiled for ever while the pass
                # reported a clean line (found in the next re-review).
                summary["stuck"] += 1
                continue
            if cursor.strikes(key, index) >= MAX_STRIKES:
                summary["stuck_retries"] += 1
            verdict, _session_id = _file_one(
                post, store_path, key, index, line, summary,
                is_last=index == total - 1, settled=settled,
                exclude_session_id=exclude_session_id,
                only_session_id=only_session_id,
            )
            if verdict == "held":
                summary["held"] += 1
                continue
            if verdict == "unselected":
                # Another session's record under a `--file` scope: no POST, no
                # strike, not crossed — it stays exactly as it was.
                summary["unselected"] += 1
                continue
            if verdict == "local_error":
                # The DEAD-LETTER SUBSTRATE could not be written. That is a local
                # fault, not a server verdict, so it is NOT charged a strike (a
                # strike would let the record be skipped later without a row ever
                # landing — the limbo this module forbids) and the pass stops so
                # the operator fixes it.
                summary["deferred"] += 1
                stopped = "dead_letter_unwritable"
                break
            if verdict == "retry":
                summary["deferred"] += 1
                strikes = cursor.strike(key, index)
                if strikes >= MAX_STRIKES:
                    # This record has ended MAX_STRIKES passes. Skipping it for
                    # the rest of THIS pass is the only way a record the server
                    # will not take (a 402 the tier never satisfies, a payload
                    # the transport always throws on) cannot gate the queue —
                    # and because it stays OUT of `crossed`/`consumed`, it is
                    # still queued and still retried on later passes.
                    summary["stuck"] += 1
                    continue
                # TRANSIENT: leave the record un-consumed (retried next pass) and
                # END the pass — a rate-limited or overloaded API is not hammered
                # by the remaining 61,997.
                stopped = "refused"
                break
            # filed / dead-lettered: both are terminal for this record, so the
            # cursor crosses it. A dead letter is a FILING verdict, never a drop
            # — the record itself stays in its day file, untouched.
            cursor.consume(key, index)
            summary["consumed"] += 1
        if stopped in ("refused", "batch", "dead_letter_unwritable"):
            break

    # The queue depth, EVERY pass — a green line over a stuck record hiding an
    # unfiled backlog is the failure #6388 exists to end. Totals are cached in
    # the cursor for days this pass did not read (they can only have GROWN, so a
    # stale total under-counts, and the next pass corrects it).
    queued = 0
    for path in files:
        key = _day_key(path)
        total = cursor.day(key).get("total", -1)
        if not isinstance(total, int) or total < 0:
            counted = _count_records(path)
            if counted is None:
                continue
            total = counted
            cursor.set_total(key, total)
        if only_session_id is None:
            queued += cursor.queued(key, total)
        else:
            # Same reason as the plan above: a `--file` run reports what it was
            # asked to file, not the whole day's backlog — otherwise the run
            # says `queued 40` after filing the one session it was scoped to.
            lines = _read_lines(path) or []
            queued += sum(1 for i, line in enumerate(lines)
                          if not cursor.is_done(key, i)
                          and _sid_of(line) == only_session_id)
    summary["queued"] = queued
    # The pass-ending reason is INDEPENDENT of the unreadable-day list: a pass
    # that stopped on the bound while some earlier day was unreadable used to
    # report `stopped unreadable`, which names the wrong cause.
    summary["stopped"] = stopped or ("unreadable" if summary["unreadable"] else None)
    try:
        _write_cursor(store_path, cursor)
    except OSError as exc:
        # A cursor that could not be persisted is REPORTED by every caller: the
        # next pass then re-files (idempotent, but a re-POST storm) and an
        # operator has to be able to see why. Stated residual: strikes live only
        # in the cursor, so on a permanently unwritable store a record drawing a
        # transient refusal keeps ending each pass — reported here, on the
        # health line and as `ok: false`, never silent.
        summary["cursor_error"] = f"{type(exc).__name__}: {exc}"
    summary["dead_letters_total"] = read_dead_letter_count(store_path)
    return summary


def _file_age(path: Path, now: float | None = None) -> float:
    """Seconds since a day file was last written (0 when it cannot be stat'd)."""
    try:
        return max(0.0, (time.time() if now is None else now) - path.stat().st_mtime)
    except OSError:
        return 0.0


def _file_one(post: PostFn, store: Path, key: str, index: int, line: str,
              summary: dict[str, Any], *, is_last: bool, settled: bool,
              exclude_session_id: str | None = None,
              only_session_id: str | None = None,
              ) -> tuple[str, str | None]:
    """File ONE record. Returns ``(verdict, session_id)``.

    ``verdict`` is ``"filed"``, ``"dead"``, ``"retry"`` (a deferral: the cursor
    stays before the record) or ``"held"`` (live-session exclusion). Every
    non-deferral is therefore either a filed capture or a dead-letter row that
    PROVABLY landed — no record can end a pass in a state that is neither.
    """
    def dead(reason: str, detail: str = "", session_id: str | None = None,
             status: int | None = None) -> tuple[str, str | None]:
        row = {
            "path": f"{key}#{index}",
            "sessionId": session_id,
            "offset": index,
            "reason": reason,
            "at": int(time.time()),
        }
        if status is not None:
            row["status"] = status
        if detail:
            row["detail"] = detail[:500]
        if not _append_dead_letter(store, row):
            # The dead letter did NOT land. Crossing the cursor here would leave
            # the record in neither state — the exact limbo this module forbids
            # (found in review: an unwritable substrate reported every record as
            # dead-lettered, crossed them, and lost all of them silently).
            summary["detail"] = "dead-letter substrate is not writable"
            return "local_error", session_id
        summary["dead_lettered"] += 1
        summary["verdicts"] += 1
        return "dead", session_id

    scoped = only_session_id is not None

    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        if scoped:
            # The SCOPE decision comes BEFORE every verdict, because a line this
            # pass cannot attribute is not ours to judge (#6388 review). It used
            # to be parsed first, so a `--file X` run dead-lettered and CROSSED an
            # unparseable line (a row into the shared dead-letter file, the index
            # consumed) that may belong to any other session — and a torn tail in
            # ANY day file charged a strike and ended the scoped pass with
            # `stopped="refused"` before it ever reached the session it was
            # scoped to. `_count_records` already states the reading the plan
            # uses ("unparseable lines are counted as NOT matching a named
            # session"); this makes the pass agree with it, and `unselected`
            # never consumes, so the unscoped pass still picks it up.
            return "unselected", None
        if is_last and not settled:
            # A writer may be mid-append on this file RIGHT NOW: the tail line is
            # torn, not corrupt. Defer, so the cursor stays before it and the
            # completed record is filed next pass (`SETTLE_GRACE_SECONDS` is the
            # same guard the seal tool uses before it touches a day).
            summary["detail"] = f"{key}: tail line mid-append, deferred"
            return "retry", None
        return dead("unparseable_record", "line is not valid JSON")
    if not isinstance(record, dict):
        if scoped:
            # Same rule for a JSON line that is not an object: it carries no
            # session_id to compare, so it cannot be attributed to the scope.
            return "unselected", None
        return dead("unparseable_record", "record is not an object")

    raw_sid = record.get("session_id")
    sid = raw_sid if isinstance(raw_sid, str) and raw_sid.strip() else None
    if sid is not None and exclude_session_id and sid == exclude_session_id:
        # The session that is LIVE right now: filing it makes the server replay
        # it and skip extraction for every later turn (`__main__`'s
        # `_cmd_session_drain` documents the same rule for the spool). Left
        # un-consumed, so it is filed once it is no longer live.
        return "held", sid
    if only_session_id is not None and sid != only_session_id:
        # `--file <session>`: this pass was scoped to one session, so another
        # session's record is not ours to file — and NOT ours to dead-letter
        # either (a null sid included: it cannot be attributed to the scope).
        # Left un-consumed, so an unscoped pass picks it up later.
        return "unselected", sid
    turns = _window(record.get("conversation"))
    if sid is None:
        # No session id ⇒ the server cannot upsert it and the record names no
        # session to report; a permanent 422 that would otherwise retry forever.
        return dead("no_session_id", "record carries no usable session_id")
    if not turns:
        return dead("no_conversation", "record carries no usable turns",
                    session_id=sid)

    payload = {
        # The SAME normalisers the spool leg puts on the wire: an illegal
        # harness/machine_id/model draws a 422, and a 422 is PERMANENT.
        "harness": _normalise_harness(record.get("harness")),
        "session_id": _normalise_session_id(sid),
        "conversation": turns,
        "machine_id": _normalise_machine_id(record.get("machine_id")),
    }
    metadata = record.get("metadata")
    source = metadata.get("source") if isinstance(metadata, dict) else None
    if isinstance(source, str) and source.strip():
        payload["source"] = source.strip()[:256]
    model = _normalise_model(record.get("model"))
    if model:
        payload["model"] = model

    summary["posts"] += 1
    try:
        outcome = post(payload)
    except Exception as exc:  # a transport bug is a DEFERRAL, never a loss
        summary["detail"] = f"{type(exc).__name__}: {exc}"[:300]
        return "retry", sid
    if outcome.ok:
        summary["filed"] += 1
        summary["verdicts"] += 1
        # A 2xx is not one thing. The spool leg reads the body and records the
        # store-only / degraded shapes as DEFERRALS (#4197), because the turns
        # were stored and nothing was extracted. This leg counted every 2xx as
        # `filed` and never looked — two answers to one wire event (found in
        # review). The REASON comes from the single shared definition
        # (`__main__._deferral_reason`), never a second copy of the rule.
        #
        # The FILING policy is deliberately unchanged: the record was accepted,
        # re-POSTing it every pass would hammer the API for a condition this leg
        # cannot fix, and the server upserts on `session_id` if it later
        # extracts. What changes is that the condition is now COUNTED and
        # printed, so `filed` no longer silently claims extraction happened.
        reason = _deferral_reason_of(outcome)
        if reason:
            summary["degraded"] += 1
            summary["detail"] = f"stored without extraction ({reason})"[:300]
        return "filed", sid
    verdict = classify_failure(outcome.status, outcome.detail)
    if verdict == "retry":
        summary["detail"] = str(outcome.detail or outcome.status or "deferred")[:300]
        return "retry", sid
    return dead("http_rejected", str(outcome.detail or ""), session_id=sid,
                status=outcome.status)


def _deferral_reason_of(outcome: PostOutcome) -> str | None:
    """Why a 2xx stored the turns without extracting — or ``None``.

    Delegates to ``__main__._deferral_reason``, the ONE definition every other
    capture writer uses, so this leg cannot drift from the spool's reading of
    the same response body (#4197). Imported at call time: ``__main__`` is the
    caller of this module, and it is a large import to pull in at module load.

    Never raises: a body shape this cannot interpret is ``None`` ("nothing known
    to be wrong"), because a probe that cannot look must not fabricate a
    deferral — and must never cost the filing that already succeeded.
    """
    body = getattr(outcome, "body", None)
    if not isinstance(body, dict):
        return None
    try:
        from tortoise.__main__ import _deferral_reason

        return _deferral_reason(body)
    except Exception:
        return None


def format_progress(summary: dict[str, Any]) -> str:
    """The one operator line: filed / deferred / dead-lettered / still-queued.

    ``session-events drain: filed 200, dead-lettered 1, deferred 0, queued 1797
    (stopped batch)`` — the rate an operator needs, on the surface they read.
    ``cursor_error`` and ``unreadable`` days are named here for the same reason:
    a substrate nobody can move must not report a clean line.
    """
    line = (
        "session-events drain: "
        f"filed {summary.get('filed', 0)}, "
        f"dead-lettered {summary.get('dead_lettered', 0)}, "
        f"deferred {summary.get('deferred', 0)}, "
        f"queued {summary.get('queued', 0)}"
    )
    if summary.get("stuck"):
        line += f", stuck {summary['stuck']} (over {MAX_STRIKES} strikes, skipped)"
    if summary.get("held"):
        line += f", held {summary['held']} (live session)"
    if summary.get("unselected"):
        line += f", unselected {summary['unselected']} (not this --file scope)"
    if summary.get("degraded"):
        line += (f", stored-not-extracted {summary['degraded']} "
                 f"(filed; #4197 shape)")
    if summary.get("stopped"):
        line += f" (stopped {summary['stopped']})"
    if summary.get("posts"):
        line += f", posted {summary['posts']}"
    if summary.get("unreadable"):
        line += f" [unreadable days: {len(summary['unreadable'])}]"
    if summary.get("cursor_error"):
        line += f" [CURSOR NOT WRITTEN: {summary['cursor_error']}]"
    if summary.get("dead_letters_total"):
        line += f" [dead-letter rows: {summary['dead_letters_total']}]"
    return line


def cursor_digest(store: Path | str | None = None) -> str:
    """A digest of the cursor file — the falsifiable "did the backlog move" probe."""
    directory = Path(store) if store is not None else cursor_dir()
    try:
        data = (directory / CURSOR_FILE).read_bytes()
    except OSError:
        return ""
    return hashlib.sha256(data).hexdigest()[:16]
