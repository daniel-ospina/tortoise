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
from pathlib import Path
from typing import Any, Callable, Mapping

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
#: Server window bounds (`tortoise/quota.py::MAX_SESSION_TURNS`, and the Pi
#: leg's `TURN_MAX_CHARS`). Applied here for the same reason the Pi leg applies
#: them: the server's 400 for an over-long capture is PERMANENT, so an
#: un-windowed record would be dead-lettered as "refused" when it is a legal
#: conversation the product itself caps on every other leg.
MAX_TURNS = 500
TURN_MAX_CHARS = 5000
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


def _candidate_files(root: Path) -> list[Path]:
    """Every day file the substrate holds, OLDEST first.

    Both shapes count, and that is load-bearing: a drain that read only
    ``*.jsonl`` would SILENTLY SKIP every day sealed before it was drained —
    records neither filed nor dead-lettered, which is precisely the state this
    module exists to make unreachable. A day present in BOTH shapes (a seal that
    landed between our listing and its unlink) is read from the plain file only:
    the same records twice would be two POSTs of one session.
    """
    found: dict[str, Path] = {}
    for directory in (root / "archive", root):
        try:
            names = sorted(p.name for p in directory.iterdir() if p.is_file())
        except OSError:
            continue
        for name in names:
            if not (name.endswith(".jsonl") or name.endswith(".jsonl.gz")):
                continue
            path = directory / name
            key = _day_key(path)
            # PLAIN WINS: the root is scanned last, and it holds the live shape.
            if key not in found or directory == root:
                found[key] = path
    return sorted(found.values(), key=_day_key)


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
    except (OSError, EOFError, gzip.BadGzipFile):
        return None


def _count_records(path: Path) -> int | None:
    """How many records a day file holds. No JSON parse — this is QUEUE DEPTH."""
    lines = _read_lines(path)
    return None if lines is None else len(lines)


def _window(conversation: Any) -> list[dict]:
    """The turns to POST — the last ``MAX_TURNS``, each capped at ``TURN_MAX_CHARS``.

    Keeping the MOST RECENT turns matches every other capture leg
    (``capture_spool`` snapshots, ``extractTurns`` in the Pi leg): recent context
    is what memory wants, and the server's cap is what makes an over-long record
    a permanent 4xx instead of a legal filing.
    """
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
        turns.append({"role": role, "content": content[:TURN_MAX_CHARS]})
        if len(turns) > MAX_TURNS:
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
    def load(cls, store: Path, root: Path) -> "_Cursor":
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
                days[str(key)] = {
                    # A hand-edited / corrupt count is read as "nothing
                    # consumed": the safe direction is a re-POST (idempotent
                    # upsert), never a silently skipped record.
                    "consumed": consumed if isinstance(consumed, int) and consumed >= 0 else 0,
                    "strikes": {
                        str(k): v for k, v in (strikes or {}).items()
                        if isinstance(v, int) and v > 0
                    } if isinstance(strikes, dict) else {},
                }
        return cls(str(root), days)

    def day(self, key: str) -> dict:
        return self.days.setdefault(key, {"consumed": 0, "strikes": {}})

    def consumed(self, key: str) -> int:
        return int(self.day(key).get("consumed") or 0)

    def strike(self, key: str, index: int) -> int:
        strikes = self.day(key)["strikes"]
        strikes[str(index)] = int(strikes.get(str(index), 0)) + 1
        return strikes[str(index)]

    def clear_strike(self, key: str, index: int) -> None:
        self.day(key)["strikes"].pop(str(index), None)

    def consume(self, key: str, index: int) -> None:
        self.day(key)["consumed"] = index + 1
        self.clear_strike(key, index)

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
    our write (found in review). The 500-row cap therefore belongs to the
    READERS — this substrate is a status surface, not a second unbounded log, and
    a record's own row is written once (its path key is unique).
    """
    try:
        store.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(store / DEAD_LETTER_FILE,
                     os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
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
    dry_run: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """One BOUNDED pass over the ``session-events`` substrate.

    ``post`` is the recovery sweep's own transport (``_session_post(api_key,
    api_url)``): a ``payload -> PostOutcome`` callable, so this leg files through
    the same wire path as every other capture and never grows its own.

    Returns the run's progress record — ``filed``, ``deferred``,
    ``dead_lettered``, ``held``, ``consumed``, ``queued`` (records still
    undelivered), ``unreadable`` (whole days this pass could not read at all) and
    ``stopped`` (``"batch"`` = the bound, ``"refused"`` = a transient refusal
    ended the pass). The absence of exactly this record is why tens of thousands
    of records accumulated invisibly (#6388).
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
        "consumed": 0,
        "queued": 0,
        "stuck": 0,
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
            queued += max(0, total - cursor.consumed(_day_key(path)))
        summary["queued"] = queued
        summary["detail"] = "plan only — nothing was posted and no cursor moved"
        summary["dead_letters_total"] = read_dead_letter_count(store_path)
        return summary

    if not files:
        summary["dead_letters_total"] = read_dead_letter_count(store_path)
        return summary

    attempted = 0
    stopped: str | None = None

    for path in files:
        key = _day_key(path)
        lines = _read_lines(path)
        if lines is None:
            # A whole day that cannot be read is REPORTED, never counted as an
            # empty queue: its records are neither filed nor dead-lettered, and
            # an operator has to know which day that is.
            summary["unreadable"].append(str(path))
            stopped = "unreadable"
            continue
        consumed = cursor.consumed(key)
        if consumed > len(lines):
            # The file is SHORTER than the cursor claims (a restored or rewritten
            # day). The safe reading is "nothing is known about these records":
            # re-file the day rather than cross records that were never filed.
            cursor.day(key)["consumed"] = consumed = 0
            summary["detail"] = f"{key}: cursor exceeded the file, re-filing"
        settled = _file_age(path) > SETTLE_GRACE_SECONDS
        index = -1
        for line in lines:
            index += 1
            if index < consumed:
                continue
            if attempted >= cap:
                stopped = stopped or "batch"
                break
            verdict, session_id = _file_one(
                post, store_path, key, index, line, summary,
                is_last=index == len(lines) - 1, settled=settled,
                exclude_session_id=exclude_session_id,
            )
            if verdict == "held":
                summary["held"] += 1
                continue
            if verdict == "retry":
                summary["deferred"] += 1
                strikes = cursor.strike(key, index)
                if strikes >= MAX_STRIKES:
                    # This record has ended MAX_STRIKES passes. Skipping it for
                    # the rest of THIS pass is the only way a record the server
                    # will not take (a 402 the tier never satisfies, a payload
                    # the transport always throws on) cannot gate the queue.
                    summary["stuck"] += 1
                    continue
                # TRANSIENT: leave the record un-consumed (retried next pass) and
                # END the pass — a rate-limited or overloaded API is not hammered
                # by the remaining 61,997.
                stopped = "refused"
                break
            attempted += 1
            # filed / dead-lettered: both are terminal for this record, so the
            # cursor crosses it. A dead letter is a FILING verdict, never a drop
            # — the record itself stays in its day file, untouched.
            cursor.consume(key, index)
            summary["consumed"] += 1
        if stopped in ("refused", "batch"):
            break

    # How much is still undelivered — asked only when a pass stopped early,
    # because it is a full scan of the substrate and a healthy pass already knows
    # the answer is "what the cursor says is left".
    if stopped:
        queued = 0
        for path in files:
            total = _count_records(path)
            if total is None:
                continue
            key = _day_key(path)
            queued += max(0, total - cursor.consumed(key) - _stuck_count(cursor, key, total))
        summary["queued"] = queued
    summary["stopped"] = stopped
    try:
        _write_cursor(store_path, cursor)
    except OSError as exc:
        # A cursor that could not be persisted is REPORTED by every caller: the
        # next pass then re-files (idempotent, but a re-POST storm) and an
        # operator has to be able to see why.
        summary["cursor_error"] = f"{type(exc).__name__}: {exc}"
    summary["dead_letters_total"] = read_dead_letter_count(store_path)
    return summary


def _stuck_count(cursor: _Cursor, key: str, total: int) -> int:
    """Records of a day that are currently over their strike limit."""
    strikes = cursor.day(key).get("strikes") or {}
    return sum(1 for idx, count in strikes.items()
               if int(count) >= MAX_STRIKES and 0 <= int(idx) < total)


def _file_age(path: Path) -> float:
    """Seconds since a day file was last written (0 when it cannot be stat'd)."""
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return 0.0


def _file_one(post: PostFn, store: Path, key: str, index: int, line: str,
              summary: dict[str, Any], *, is_last: bool, settled: bool,
              exclude_session_id: str | None = None,
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
            return "retry", session_id
        summary["dead_lettered"] += 1
        return "dead", session_id

    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        if is_last and not settled:
            # A writer may be mid-append on this file RIGHT NOW: the tail line is
            # torn, not corrupt. Defer, so the cursor stays before it and the
            # completed record is filed next pass (`SETTLE_GRACE_SECONDS` is the
            # same guard the seal tool uses before it touches a day).
            summary["detail"] = f"{key}: tail line mid-append, deferred"
            return "retry", None
        return dead("unparseable_record", "line is not valid JSON")
    if not isinstance(record, dict):
        return dead("unparseable_record", "record is not an object")

    raw_sid = record.get("session_id")
    sid = raw_sid if isinstance(raw_sid, str) and raw_sid.strip() else None
    if sid is not None and exclude_session_id and sid == exclude_session_id:
        # The session that is LIVE right now: filing it makes the server replay
        # it and skip extraction for every later turn (`__main__`'s
        # `_cmd_session_drain` documents the same rule for the spool). Left
        # un-consumed, so it is filed once it is no longer live.
        return "held", sid
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

    try:
        outcome = post(payload)
    except Exception as exc:  # a transport bug is a DEFERRAL, never a loss
        summary["detail"] = f"{type(exc).__name__}: {exc}"[:300]
        return "retry", sid
    if outcome.ok:
        summary["filed"] += 1
        return "filed", sid
    verdict = classify_failure(outcome.status, outcome.detail)
    if verdict == "retry":
        summary["detail"] = str(outcome.detail or outcome.status or "deferred")[:300]
        return "retry", sid
    return dead("http_rejected", str(outcome.detail or ""), session_id=sid,
                status=outcome.status)


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
    if summary.get("stopped"):
        line += f" (stopped {summary['stopped']})"
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
