"""The per-node USAGE signal — *when was this node last served?* (#14011).

The owner's eviction criterion (`#13989`, 2026-11-20, verbatim):

> *"2 criteria should be recency of usage (searched), not recency of creation."*

Nothing in the tree could answer it. This module is that answer: a per-graph
roll-up of ``(label, key) -> (last served ts, serve count)``, derived from an
append-only serve stream, with a **bounded incremental** fold so a query never
replays the whole history.

⛔ WHY THIS IS NOT `tortoise/read_ledger.py` — READ THIS BEFORE "SIMPLIFYING" IT
================================================================================

The read ledger already records reads and already folds them to a per-key
``read_count`` / ``last_read_at``. Reusing it is the obvious move and it is
**forbidden by a recorded decision**, in two places:

`docs/architecture/SEARCH-ARCHITECTURE.md` §7, policy row *"window-local touch
counters + admission control"*::

    ⚠️ The counter is WINDOW-LOCAL cache state — a different artifact from
    `tortoise/read_ledger.py`. That ledger is TENANT-WIDE and is explicitly not a
    product input: `STORAGE-ARCHITECTURE.md` §17.4.6 states that reading it for
    any product decision "would be a violation" (it exists to measure `f`).
    Wiring it as the eviction signal is exactly the violation it names.

`docs/architecture/STORAGE-ARCHITECTURE.md` §17.4.6, closing the §3-deviation
paragraph: *"it would be a violation if any product decision read it."*

**The decision is right, and the reason is a failure mode, not ceremony.** The
ledger's size bound is tuned for a MEASUREMENT window: at
``TORTOISE_READ_LEDGER_MAX_BYTES`` it writes one ``capped`` line and **stops
recording**. If an eviction pass scored off it, the moment the ledger capped the
counter would freeze, every recently-served node would age into "cold", and the
pass would evict **the hottest nodes in the cache** — a measurement bound
silently promoted into a correctness parameter. So this module owns its own
artifact, with its own bound, whose ONE purpose is this policy input. §7 asks
for exactly that: *window-local cache state*.

**What the ledger IS used for here: as the instrument that validates this
counter.** :mod:`tests.test_usage_signal` replays a scripted search pattern
through both and requires the orderings to agree
(:func:`tests.test_usage_signal.test_ordering_matches_the_ledgers_own_record`).
That is ``#14011``'s "matches the ledger's own record of that pattern" — the
instrument checking the counter, which is what an instrument is for. Neither the
writer nor the reader of this module ever opens a ledger file.

WHAT A "SERVE" IS
=================

One line per returned row, written in ONE ``O_APPEND`` batch per read (the same
discipline as the ledger, and for the same measured reason: a per-event
open/write/close is ~610 µs, i.e. 5 ms on a ten-row read — an instrument that
slows the path it measures). A serve is recorded at the **funnel that hands rows
to a caller**, so what it counts is *"this node was in an answer somebody saw"*,
which is the question the policy asks. ``get_point`` is deliberately NOT a
recording site, for the ledger's own reason: the write paths read through it, so
counting it would mark written-but-never-read nodes as served (the flattering
direction for a residency score — a node looks hot because it was created).

⛔ NO WRITE TOUCHES A NODE. The signal lives in a file beside the store, never
in the graph: a ``last_searched_at`` property written on every search is write
amplification into the very store the window exists to bound. This module
imports no graph client and opens no connection — ``test_no_graph_dependency``
pins that.

DERIVED, NEVER AUTHORITATIVE
============================

The stream is the record of what was served; the checkpoint is a **cache of a
fold**, and it may be deleted at any time. ``refresh`` then replays the stream
from zero and produces the same reading — that is the invariant
``test_incremental_equals_cold_replay`` and
``test_restart_survives_on_the_checkpoint_alone`` hold it to. Nothing downstream
may treat a reading as a fact about the store; it is a fact about *this file*,
carrying its own window, its own offset and its own truncation flag.

⚠️ **AN EMPTY READING IS NOT "NOTHING IS HOT".** A freshly armed signal reports
``served_total == 0`` because nothing has been *observed* yet, not because
nothing was used. Everything here that can be empty says so with its window and
its counters rather than reporting a confident zero — the same rule
``read_ledger`` states for the first report of a new ledger.

FAIL DIRECTION
==============

A read never fails because the signal did. Every write failure is swallowed and
warned once; ``refresh`` returns the last good checkpoint (marked ``stale``) or
an empty reading rather than raising into the caller that wanted a search
result. A checkpoint that cannot be read is a CACHE MISS, not an error: the
stream is replayed from cold.

OFF BY DEFAULT, AND OFF MEANS NOT IMPORTED
==========================================
The funnel reads ``TORTOISE_USAGE_SIGNAL`` first (the ``#4097`` truthy contract)
and imports this module only when it is truthy, so a default deployment pays one
env lookup and its response is byte-identical. That is deliberate: the window
that consumes this signal is not armed yet (``#13103``), so the writer ships
disarmed and is armed by the same measurement that arms the window.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import stat
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from .env_truthy import is_truthy

logger = logging.getLogger(__name__)

#: The env flag that makes the serve funnel RECORD (the #4097 truthy contract).
USAGE_FLAG_ENV = "TORTOISE_USAGE_SIGNAL"

#: The serve stream. Default: beside the embedded store, keyed by STORE TARGET
#: so two graphs on one host do not pool into one counter.
USAGE_PATH_ENV = "TORTOISE_USAGE_SIGNAL_PATH"

#: The stream's size bound. On the bound it writes one `capped` line and stops.
#: ⚠️ Deliberately its OWN variable, not the ledger's: this bound is a policy
#: input's freshness horizon, and sharing the measurement's would couple two
#: artifacts whose bounds answer different questions (§7).
USAGE_MAX_BYTES_ENV = "TORTOISE_USAGE_SIGNAL_MAX_BYTES"
DEFAULT_MAX_BYTES = 16 * 1024 * 1024

#: Optional trailing window for the ROLL-UP (not for the stream). ``None`` —
#: the default — keeps every entry, so ``serve_count`` is exact. Set it and
#: an entry whose last serve is older than the window is dropped and reported
#: only as ``pruned_before``: its exact count is gone, its recency is bounded.
#: That trade is opt-in because the scorer it feeds scores RECENCY, and a
#: bounded "not served since <watermark>" answers that; an exact count does not
#: need to survive beyond the window for this policy.
USAGE_WINDOW_DAYS_ENV = "TORTOISE_USAGE_SIGNAL_WINDOW_DAYS"

EVENT_SERVE = "serve"
EVENT_CAPPED = "capped"

#: Checkpoint format version. A checkpoint written by another version is IGNORED
#: (replayed from cold), never migrated: it is a cache, and a cache that cannot
#: be read is a miss.
CHECKPOINT_VERSION = 1

#: A single event line longer than this is dropped, for the ledger's reason: the
#: bound is not what prevents interleaving (one `O_APPEND` write per batch is) —
#: it keeps one record inside a page, so a crash tears at most one line.
MAX_LINE_BYTES = 4096

#: Bytes of the stream PREFIX fingerprinted in the checkpoint. This is what
#: catches a REPLACED stream: `rm` + recreate can recycle an inode, and a new
#: file that happens to be longer than the old offset would otherwise be read
#: incrementally over somebody else's history.
#:
#: ⛔ The fingerprint length is FIXED, not the current offset (or every refresh
#: would compare a longer prefix against a shorter stored digest and replay from
#: cold for ever). A stream shorter than this fingerprints its whole self and
#: stores the length it used (`_State.head_len`), which stays comparable because
#: the stream is APPEND-ONLY.
HEAD_FINGERPRINT_BYTES = 8192

_warned: set[str] = set()

#: Re-entrancy guard for one process; the cross-process lock is `_file_lock`.
_REFRESH_LOCK = threading.Lock()


# ════════════════════════════════════════════════════════════════════════════
# Identity, paths, configuration
# ════════════════════════════════════════════════════════════════════════════


def enabled() -> bool:
    """Is the serve funnel recording? (default off)

    ⚠️ NOT the funnel's own gate: the funnel reads ``TORTOISE_USAGE_SIGNAL``
    itself FIRST so this module is never even imported when the flag is off.
    This is the same predicate for callers that already imported it.
    """
    return is_truthy(os.environ.get(USAGE_FLAG_ENV))


def _store_tag() -> str:
    """A short tag naming the STORE TARGET this signal belongs to.

    The same derivation ``read_ledger.ledger_path`` uses, and for the same
    reason: ``resolve_db_path()`` cannot see the SDK's ``graph_name=`` argument,
    so a server URI alone would put every graph on a host into one counter and
    the residency score of one graph would age out on another's traffic. A
    caller selecting a graph by constructor argument on a shared server must set
    ``TORTOISE_USAGE_SIGNAL_PATH`` explicitly — no default can see that choice.
    """
    from .config import resolve_db_path

    store = "|".join(
        (
            os.environ.get("TORTOISE_DB_URI") or "",
            os.environ.get("TORTOISE_GRAPH_NAME") or "",
            str(resolve_db_path()),
        )
    )
    return hashlib.sha256(store.encode()).hexdigest()[:12]


def usage_path() -> Path:
    """The serve stream's path: the env override, else one file per store target."""
    explicit = os.environ.get(USAGE_PATH_ENV)
    if explicit:
        return Path(explicit).expanduser()
    from .config import resolve_db_path

    return (
        Path(resolve_db_path()).expanduser().parent
        / f"usage-signal-{_store_tag()}.jsonl"
    )


def checkpoint_path(stream: Path | str | None = None) -> Path:
    """The roll-up checkpoint beside a stream (``<stream>.rollup.json``)."""
    target = Path(stream) if stream is not None else usage_path()
    return target.with_suffix(target.suffix + ".rollup.json")


def _max_bytes() -> int:
    raw = os.environ.get(USAGE_MAX_BYTES_ENV)
    try:
        return int(raw) if raw else DEFAULT_MAX_BYTES
    except ValueError:
        return DEFAULT_MAX_BYTES


def _window_days() -> float | None:
    raw = os.environ.get(USAGE_WINDOW_DAYS_ENV)
    if raw is None or not raw.strip():
        return None
    try:
        days = float(raw)
    except ValueError:
        # ⛔ A MISTYPED WINDOW IS NOT "NO WINDOW". A typo'd pruning bound that
        # degrades to "keep everything" silently un-bounds the roll-up, which is
        # the quiet direction; the reading reports the refusal instead.
        _warn_once(
            "window",
            "usage signal: %s=%r is not a day count — the roll-up is running "
            "UNBOUNDED (every entry kept); fix the value to bound it",
            USAGE_WINDOW_DAYS_ENV,
            raw,
        )
        return None
    return days if days > 0 else None


def _warn_once(key: str, msg: str, *args: object) -> None:
    if key in _warned:
        return
    _warned.add(key)
    logger.warning(msg, *args)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _stamp(value: Any) -> datetime | None:
    """Parse an event's ``at``; ``None`` when it is not a time.

    Reuses the ledger's own parse and comparison rather than restating them: a
    lexical compare is chronological only when every stamp carries the SAME UTC
    offset, and ``2026-10-11T01:00:00+09:00`` sorts AFTER
    ``2026-10-11T18:00:00+00:00`` as a string while being seven hours EARLIER.
    One implementation of that rule in the package, not two that can drift.
    """
    from . import read_ledger

    return read_ledger._stamp(value)


def _stamp_cmp(a: str, b: str) -> int:
    """Chronological comparison of two stamps (see :func:`_stamp`)."""
    from . import read_ledger

    return read_ledger._stamp_cmp(a, b)


def row_key(label: str, row: Mapping[str, Any]) -> str | None:
    """The ``(label, key)`` coordinate of one returned row, or ``None``.

    ⛔ **Delegated to the ledger's own resolver, deliberately.** The identity
    contract (``Point``/``Subject``/``Object`` -> ``id`` since ``#9881``,
    ``Event -> eventId``, ``Source -> url``) is the record's, and a second
    implementation of it here would drift — the ledger's own docstring names a
    pair of readers that "could drift and nothing here would notice". Sharing
    it is also what makes the equivalence test meaningful: both artifacts key
    rows the same way, so the only thing compared is the ORDERING.

    A ``None`` is a row the contract cannot address. It is NOT a serve of some
    fallback key — inventing one would score a node that was never returned.
    """
    from . import read_ledger

    return read_ledger.row_key(label, row)


# ════════════════════════════════════════════════════════════════════════════
# The write path — one O_APPEND batch per returned row-set
# ════════════════════════════════════════════════════════════════════════════


def _tail_ends_with_newline(target: Path) -> bool:
    """Does the file end with a newline? (a crash can leave a torn last line)"""
    try:
        with open(target, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                return True
            fh.seek(-1, os.SEEK_END)
            return fh.read(1) == b"\n"
    except OSError:  # pragma: no cover — a stat/read race, or an unreadable file
        return True


def _tail_is_capped(target: Path) -> bool:
    """Is the LAST non-empty line of the file our `capped` marker?"""
    try:
        size = target.stat().st_size
        if size == 0:
            return False
        with open(target, "rb") as fh:
            back = min(size, MAX_LINE_BYTES + 64)
            fh.seek(size - back)
            chunk = fh.read()
    except OSError:  # pragma: no cover
        return False
    for raw in reversed(chunk.split(b"\n")):
        line = raw.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            return False
        return isinstance(parsed, dict) and parsed.get("event") == EVENT_CAPPED
    return False


def _append_many(records: Iterable[Mapping[str, Any]], *, path: Path) -> int:
    """Append a BATCH of serve events in ONE write. Returns how many were written.

    The failure direction is the ledger's, for the same reason: a signal write
    never raises into the read path (an instrument may not break the thing it
    measures). Every failure is warned about ONCE — a silently dead stream is
    indistinguishable from a store nobody reads.
    """
    lines: list[str] = []
    n_events = 0
    for record in records:
        try:
            line = json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
        except (TypeError, ValueError):
            _warn_once(
                "encode",
                "usage signal: dropping an unencodable serve event",
            )
            continue
        if len(line.encode()) > MAX_LINE_BYTES:
            _warn_once(
                "long-line",
                "usage signal: dropping a serve line over %d B (a record that "
                "large can be torn by a crash, so it is not written)",
                MAX_LINE_BYTES,
            )
            continue
        lines.append(line)
        n_events += 1
    if not lines:
        return 0
    target: Path | None = None
    try:
        target = path
        target.parent.mkdir(parents=True, exist_ok=True)
        cap = _max_bytes()
        size = target.stat().st_size if target.exists() else 0
        blob = "".join(lines).encode()
        # ⛔ THE BATCH ITSELF COUNTS against the bound (the ledger's own lesson:
        # checking only the pre-write size lets one batch cross the bound with no
        # marker at all). The bound is a ceiling on EVENTS, so the marker line
        # may sit a few dozen bytes over it — the marker is what makes the
        # truncation audible, and an inaudible one is the failure this module
        # refuses.
        if size >= cap or size + len(blob) > cap:
            # The decision reads the FILE's tail, never a process latch: a
            # rotated stream that caps AGAIN must say so again, or the second
            # stop is silent and `refresh` reports `capped: false` over a file it
            # truncated.
            if not _tail_is_capped(target):
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "ab") as fh:
                    # A torn final line with no newline would make the marker
                    # unparseable AND corrupt it — the guarantee failing exactly
                    # in the case it exists for.
                    if size and not _tail_ends_with_newline(target):
                        fh.write(b"\n")
                    fh.write(
                        (
                            json.dumps(
                                {
                                    "at": _now(),
                                    "event": EVENT_CAPPED,
                                    "max_bytes": cap,
                                },
                                separators=(",", ":"),
                                sort_keys=True,
                            )
                            + "\n"
                        ).encode()
                    )
            _warn_once(
                "capped",
                "usage signal %s: reached its %d B bound — no further serves "
                "are recorded (rotate it or raise %s); the scorer must read the "
                "reading's `capped` flag, not a shrinking counter",
                target,
                cap,
                USAGE_MAX_BYTES_ENV,
            )
            return 0
        # 0600 on CREATE: the stream carries (label, key) coordinates, and a
        # `Subject`/`Object` key or a `Source.url` identifies a tenant's content.
        # `os.open` is the only portable way to pass a creation mode through
        # `O_APPEND`; it cannot TIGHTEN an existing inode, so the mode is
        # re-checked below from the `stat` this function already takes.
        st = None
        with contextlib.suppress(OSError):
            st = target.stat()
        if st is not None and stat.S_ISREG(st.st_mode) and (st.st_mode & 0o077):
            with contextlib.suppress(OSError):  # not ours to fail the read path
                os.chmod(target, 0o600)
        # A crash (or a short write) can leave a TORN final line. Appending a
        # batch onto it CONCATENATES the batch's first line with the fragment,
        # losing that record while the return value still counts it.
        prefix = b""
        if size and not _tail_ends_with_newline(target):
            prefix = b"\n"
            blob = prefix + blob
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            # `os.write` may write FEWER bytes than asked (a full disk). Looping
            # is the only way the return value can be trusted.
            written = 0
            while written < len(blob):
                n = os.write(fd, blob[written:])
                if n <= 0:  # pragma: no cover — a closed/full descriptor
                    break
                written += n
        finally:
            os.close(fd)
        if written < len(blob):
            _warn_once(
                "short",
                "usage signal %s: wrote %d of %d B — the tail is torn, so some "
                "serves are LOST and this counter is INCOMPLETE",
                target,
                written,
                len(blob),
            )
            return blob[len(prefix) : written].count(b"\n")
        return n_events
    except Exception as e:  # pragma: no cover — a broken filesystem or bad path
        # ANY failure is swallowed, not just `OSError`: `RuntimeError` (an
        # unresolvable `~user`), `ValueError` (a NUL byte in a path) and
        # `TypeError` (a non-path) are all reachable from configuration.
        _warn_once(
            "oserror",
            "usage signal: append to %s failed (%s) — the read path is "
            "unaffected, but the counter is now incomplete",
            target if target is not None else "(unresolved path)",
            e,
        )
        return 0


def record_serves(
    label: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    at: str | None = None,
    path: Path | None = None,
) -> int:
    """Record a returned row-set as SERVED, in ONE append. Returns keys recorded.

    Called from the serve funnel only when the flag is on. ``rows`` are the
    dicts the funnel hands a caller, so what is recorded is exactly what was
    surfaced — not a candidate pool, and not the stock.

    A row the identity contract cannot address is SKIPPED (it is not served
    under a fallback key). That is a hole in the counter, and the reading names
    it (``unresolved_skips``) rather than absorbing it.
    """
    stamp = at or _now()
    events: list[dict[str, Any]] = []
    unresolved = 0
    for row in rows or ():
        if not isinstance(row, Mapping):
            unresolved += 1
            continue
        key = row_key(label, row)
        if key is None:
            unresolved += 1
            continue
        events.append(
            {
                "at": stamp,
                "event": EVENT_SERVE,
                "label": label,
                "key": key,
            }
        )
    if unresolved:
        # File-level, not per-row: a count is all this counter keeps, and the
        # count is what makes the hole visible in `refresh`.
        events.append({"at": stamp, "event": "unresolved", "n": unresolved})
    if not events:
        return 0
    _append_many(events, path=path or usage_path())
    return sum(1 for e in events if e.get("event") == EVENT_SERVE)


# ════════════════════════════════════════════════════════════════════════════
# The roll-up — bounded, incremental, replayable
# ════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class UsageEntry:
    """One ``(label, key)`` coordinate and what the stream observed about it."""

    label: str
    key: str
    last_served_at: str | None
    first_served_at: str | None
    serve_count: int

    @property
    def served(self) -> bool:
        return self.serve_count > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "key": self.key,
            "last_served_at": self.last_served_at,
            "first_served_at": self.first_served_at,
            "serve_count": self.serve_count,
        }


@dataclass
class _State:
    """The checkpoint's payload — a CACHE of a fold, never the record."""

    offset: int = 0
    source_size: int = 0
    source_dev: int | None = None
    source_ino: int | None = None
    head_sha: str | None = None
    head_len: int = 0
    lines_read: int = 0
    lines_skipped: int = 0
    served_total: int = 0
    undated_serves: int = 0
    unresolved_skips: int = 0
    capped: bool = False
    pruned: int = 0
    pruned_before: str | None = None
    #: ``"label\tkey"`` -> [last, first, count]. On DISK this is a list of
    #: arrays (see `_serialise`), so a key containing any byte round-trips.
    entries: dict[str, list[Any]] = field(default_factory=dict)

    @staticmethod
    def ident(label: str, key: str) -> str:
        return f"{label}\t{key}"

    def apply(self, label: str, key: str, at: str | None) -> None:
        ident = self.ident(label, key)
        slot = self.entries.get(ident)
        parsed = _stamp(at) is not None
        if slot is None:
            self.entries[ident] = [at if parsed else None, at if parsed else None, 1]
            return
        slot[2] += 1
        if parsed:
            if slot[0] is None or _stamp_cmp(at, slot[0]) > 0:
                slot[0] = at
            if slot[1] is None or _stamp_cmp(at, slot[1]) < 0:
                slot[1] = at

    def prune(self, before: datetime) -> None:
        """Drop entries whose last serve is older than `before` (bounded memory).

        ⛔ A PRUNED ENTRY LOSES ITS COUNT, and the reading says so: it is
        reported only as "not served since ``pruned_before``". That is enough
        for a RECENCY score (which is what consumes this) and it is honest
        about what was dropped — silently keeping a decayed count would be a
        number the stream no longer supports.
        """
        dropped: list[str] = []
        watermark: str | None = None
        for ident, slot in self.entries.items():
            last = slot[0]
            last_dt = _stamp(last) if isinstance(last, str) else None
            if last_dt is not None and last_dt < before:
                dropped.append(ident)
                if watermark is None or _stamp_cmp(last, watermark) > 0:
                    watermark = last
        for ident in dropped:
            del self.entries[ident]
        if dropped:
            self.pruned += len(dropped)
            if watermark is not None and (
                self.pruned_before is None
                or _stamp_cmp(watermark, self.pruned_before) > 0
            ):
                self.pruned_before = watermark


@dataclass(frozen=True)
class UsageReading:
    """The usage signal as of one fold — DERIVED, never authoritative.

    Every field is here so a consumer cannot quote a bare ordering: a score
    computed over a capped stream, or over a window that started five minutes
    ago, must be quotable-with-its-caveat or not quoted at all.
    """

    source: str
    as_of: str
    entries: Mapping[tuple[str, str], UsageEntry]
    served_total: int
    pruned: int
    pruned_before: str | None
    capped: bool
    lines_read: int
    lines_skipped: int
    undated_serves: int
    unresolved_skips: int
    offset: int
    replayed: bool
    stale: bool
    window_started: str | None = None
    error: str | None = None

    # ── the door the scorer uses ───────────────────────────────────────────

    def entry(self, label: str, key: str) -> UsageEntry | None:
        return self.entries.get((label, key))

    def last_served(self, label: str, key: str) -> str | None:
        got = self.entry(label, key)
        return got.last_served_at if got is not None else None

    def serve_count(self, label: str, key: str) -> int:
        got = self.entry(label, key)
        return got.serve_count if got is not None else 0

    def recency_key(self, label: str, key: str) -> tuple[float, int, str]:
        """``(last_served_epoch, serve_count, key)`` — COLDEST FIRST when sorted.

        This is an ORDERING, not a score. The residency policy (``#13989``:
        usage recency plus a class booster for ``Object``/``Subject``/``Source``)
        composes this with its own terms; the module deliberately does not embed
        a formula, because the formula is policy and its home is not here.

        A key with no observed serve sorts BEFORE every served key (``-inf``):
        "never served since the signal was armed" is the coldest reading there
        is. ⚠️ That includes a node created a minute ago, which is why
        :meth:`coldest_first` keeps unserved keys in their own bucket and lets
        the caller decide — see its docstring.
        """
        got = self.entry(label, key)
        if got is None or got.last_served_at is None:
            return (float("-inf"), 0, key)
        parsed = _stamp(got.last_served_at)
        epoch = parsed.timestamp() if parsed is not None else float("-inf")
        return (epoch, got.serve_count, key)

    def coldest_first(
        self,
        label: str | None = None,
        *,
        keys: Iterable[str] | None = None,
    ) -> list[tuple[str, str]]:
        """The served coordinates, COLDEST FIRST — the order an eviction pass wants.

        ``cache_evictor.evict`` takes a ``Selector`` whose job is to return
        candidates "ordered cold-first by whatever policy the caller owns"
        (``#10976``). This is the usage-recency term of that ordering, and it is
        stable: ties on ``last_served_at`` fall to the lower ``serve_count``,
        then to the key string, so two runs over the same stream agree exactly.

        ``label`` narrows to one population (the evictor sweeps ONE licensed
        population per pass). ``keys`` narrows to a candidate set.

        ⛔ **UNSERVED KEYS ARE NOT RETURNED.** A coordinate the counter has
        never seen is not "the coldest thing" — it is "this signal has no
        opinion", and a newly-written node is the commonest case. Folding them
        in would make this module evict brand-new data, i.e. exactly the
        creation-recency policy the owner's criterion replaces. The unmapped
        candidates are available as :meth:`unserved_keys`, so the caller applies
        its own tie-break (the present one is creation recency) rather than
        inheriting one from here.
        """
        wanted = set(keys) if keys is not None else None
        rows: list[tuple[tuple[float, int, str], tuple[str, str]]] = []
        for (lbl, key), got in self.entries.items():
            if label is not None and lbl != label:
                continue
            if wanted is not None and key not in wanted:
                continue
            rows.append((self.recency_key(lbl, key), (lbl, key)))
        rows.sort(key=lambda pair: pair[0])
        return [coord for _order, coord in rows]

    def unserved_keys(
        self,
        candidates: Iterable[str],
        *,
        label: str | None = None,
    ) -> list[str]:
        """Which of `candidates` this signal has no opinion about (any label)."""
        if label is None:
            seen = {key for (_lbl, key) in self.entries}
        else:
            seen = {key for (lbl, key) in self.entries if lbl == label}
        return [key for key in candidates if key not in seen]

    # ── honesty ────────────────────────────────────────────────────────────

    def staleness_seconds(self, now: datetime | None = None) -> float | None:
        """How far behind the stream this reading is, or ``None`` if unknown."""
        ref = now or datetime.now(UTC)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=UTC)
        parsed = _stamp(self.as_of)
        return (ref - parsed).total_seconds() if parsed is not None else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "as_of": self.as_of,
            "entries": len(self.entries),
            "served_total": self.served_total,
            "pruned": self.pruned,
            "pruned_before": self.pruned_before,
            "capped": self.capped,
            "lines_read": self.lines_read,
            "lines_skipped": self.lines_skipped,
            "undated_serves": self.undated_serves,
            "unresolved_skips": self.unresolved_skips,
            "offset": self.offset,
            "replayed": self.replayed,
            "stale": self.stale,
            "window_started": self.window_started,
            "error": self.error,
        }


def _serialise(state: _State) -> dict[str, Any]:
    """The checkpoint's on-disk form. An ARRAY per entry, so keys round-trip."""
    entries: list[list[Any]] = []
    for ident, slot in state.entries.items():
        label, _, key = ident.partition("\t")
        entries.append([label, key, slot[0], slot[1], slot[2]])
    return {
        "version": CHECKPOINT_VERSION,
        "offset": state.offset,
        "source_size": state.source_size,
        "source_dev": state.source_dev,
        "source_ino": state.source_ino,
        "head_sha": state.head_sha,
        "head_len": state.head_len,
        "lines_read": state.lines_read,
        "lines_skipped": state.lines_skipped,
        "served_total": state.served_total,
        "undated_serves": state.undated_serves,
        "unresolved_skips": state.unresolved_skips,
        "capped": state.capped,
        "pruned": state.pruned,
        "pruned_before": state.pruned_before,
        "entries": entries,
    }


def _deserialise(payload: Any) -> _State | None:
    """Rebuild a checkpoint, or ``None`` when it cannot be trusted.

    ⛔ EVERY FIELD IS VALIDATED. A hand-edited or truncated checkpoint is a
    CACHE MISS, not a crash and not a half-applied state: returning a partial
    state would make the incremental fold start from an offset the entries do
    not describe, which is the one way this module could produce a WRONG
    ordering instead of an empty one.
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("version") != CHECKPOINT_VERSION:
        return None
    try:
        state = _State(
            offset=int(payload["offset"]),
            source_size=int(payload.get("source_size") or 0),
            source_dev=(
                int(payload["source_dev"]) if payload.get("source_dev") is not None else None
            ),
            source_ino=(
                int(payload["source_ino"]) if payload.get("source_ino") is not None else None
            ),
            head_sha=(
                str(payload["head_sha"]) if payload.get("head_sha") is not None else None
            ),
            head_len=int(payload.get("head_len") or 0),
            lines_read=int(payload.get("lines_read") or 0),
            lines_skipped=int(payload.get("lines_skipped") or 0),
            served_total=int(payload.get("served_total") or 0),
            undated_serves=int(payload.get("undated_serves") or 0),
            unresolved_skips=int(payload.get("unresolved_skips") or 0),
            capped=bool(payload.get("capped")),
            pruned=int(payload.get("pruned") or 0),
            pruned_before=(
                str(payload["pruned_before"])
                if payload.get("pruned_before") is not None
                else None
            ),
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if state.offset < 0 or state.head_len < 0:
        return None
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return None
    for row in entries:
        if not (isinstance(row, list) and len(row) == 5):
            return None
        label, key, last, first, count = row
        if not isinstance(label, str) or not isinstance(key, str):
            return None
        if last is not None and not isinstance(last, str):
            return None
        if first is not None and not isinstance(first, str):
            return None
        try:
            n = int(count)
        except (TypeError, ValueError):
            return None
        state.entries[_State.ident(label, key)] = [last, first, n]
    return state


def _load_checkpoint(target: Path) -> _State | None:
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        return _deserialise(json.loads(raw))
    except ValueError:
        return None


def _save_checkpoint(target: Path, state: _State) -> bool:
    """Write the checkpoint ATOMICALLY. Returns False when it could not be kept.

    ⛔ `os.replace` on a sibling temp file: a partial checkpoint read back by
    the next `refresh` would be deserialised as a MISS (correct) but a partial
    write that replaced a GOOD checkpoint would throw away fold progress on
    every crash. The rename is the atomic step.
    """
    tmp = target.with_suffix(target.suffix + f".tmp{os.getpid()}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            json.dumps(_serialise(state), separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, target)
        return True
    except Exception as e:  # pragma: no cover — a full disk, an odd path
        _warn_once("checkpoint", "usage signal: checkpoint not written (%s)", e)
        with contextlib.suppress(OSError):
            tmp.unlink()
        return False


@contextlib.contextmanager
def _file_lock(target: Path) -> Iterator[None]:
    """Serialise concurrent roll-ups across PROCESSES (a no-op if unavailable).

    A missed lock does not corrupt anything — the fold is a pure function of a
    stream prefix, so the loser of a race simply re-does work — but two
    processes writing the checkpoint concurrently WOULD interleave offsets. The
    lock is best-effort: a filesystem without `flock` falls through rather than
    failing the read.
    """
    handle = None
    try:
        lock = target.with_suffix(target.suffix + ".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock, "a+b")  # noqa: SIM115 — closed in the finally
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):  # pragma: no cover — non-POSIX / no flock
            pass
    except OSError:  # pragma: no cover
        handle = None
    try:
        yield
    finally:
        if handle is not None:
            with contextlib.suppress(Exception):
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            with contextlib.suppress(Exception):
                handle.close()


def _head_sha(target: Path, nbytes: int) -> str | None:
    """sha256 of the stream's first `nbytes` bytes (``None`` for a zero-length prefix)."""
    if nbytes <= 0:
        return None
    try:
        with open(target, "rb") as fh:
            head = fh.read(nbytes)
    except OSError:  # pragma: no cover
        return None
    if len(head) < nbytes:
        # The stream is shorter than the prefix it was fingerprinted over — it
        # was truncated. A short read is not a partial match.
        return None
    return hashlib.sha256(head).hexdigest()


def _empty_reading(target: Path, *, replayed: bool, stale: bool, error: str | None) -> UsageReading:
    return UsageReading(
        source=str(target),
        as_of=_now(),
        entries={},
        served_total=0,
        pruned=0,
        pruned_before=None,
        capped=False,
        lines_read=0,
        lines_skipped=0,
        undated_serves=0,
        unresolved_skips=0,
        offset=0,
        replayed=replayed,
        stale=stale,
        error=error,
    )


def _to_reading(
    state: _State,
    target: Path,
    *,
    replayed: bool,
    stale: bool,
    window_started: str | None,
    error: str | None = None,
) -> UsageReading:
    entries: dict[tuple[str, str], UsageEntry] = {}
    for ident, slot in state.entries.items():
        label, _, key = ident.partition("\t")
        entries[(label, key)] = UsageEntry(
            label=label,
            key=key,
            last_served_at=slot[0],
            first_served_at=slot[1],
            serve_count=int(slot[2]),
        )
    return UsageReading(
        source=str(target),
        as_of=_now(),
        entries=entries,
        served_total=state.served_total,
        pruned=state.pruned,
        pruned_before=state.pruned_before,
        capped=state.capped,
        lines_read=state.lines_read,
        lines_skipped=state.lines_skipped,
        undated_serves=state.undated_serves,
        unresolved_skips=state.unresolved_skips,
        offset=state.offset,
        replayed=replayed,
        stale=stale,
        window_started=window_started,
        error=error,
    )


def _consume(state: _State, chunk: bytes) -> None:
    """Fold a byte chunk of NEW stream bytes into `state`, in FILE ORDER.

    Only COMPLETE lines are consumed by the caller (it cuts at the last
    newline), so a torn tail is re-read next time rather than lost.
    """
    for raw in chunk.split(b"\n"):
        line = raw.strip()
        if not line:
            continue
        # A line that is not whole (the caller's cut should prevent it) or not
        # JSON is COUNTED, never silently dropped: a torn line is a LOST serve,
        # and a counter that hides its losses reads as complete.
        try:
            ev = json.loads(line)
        except ValueError:
            state.lines_skipped += 1
            continue
        if not isinstance(ev, dict):
            state.lines_skipped += 1
            continue
        state.lines_read += 1
        kind = ev.get("event")
        if kind == EVENT_CAPPED:
            # FILE-level, exactly like the ledger's marker: this file stopped
            # recording, and the reading must say so for the rest of its life.
            state.capped = True
            continue
        if kind == "unresolved":
            with contextlib.suppress(TypeError, ValueError):
                state.unresolved_skips += int(ev.get("n") or 0)
            continue
        if kind != EVENT_SERVE:
            state.lines_skipped += 1
            continue
        label, key = ev.get("label"), ev.get("key")
        if not isinstance(label, str) or not isinstance(key, str) or not label or not key:
            state.lines_skipped += 1
            continue
        at = ev.get("at")
        # `served_total` counts EVERY serve event folded (dated and undated) —
        # it is the "has anything been observed" guard, so it must not understate
        # usage. `undated_serves` is its NAMED SUBSET: a serve that happened but
        # cannot be placed in time. Such a serve is still counted (the counter
        # must not lose it) but it may NOT move `last_served_at` to an unknown
        # instant — a fabricated time is worse than an old one, because the
        # scorer would act on it.
        state.served_total += 1
        if _stamp(at) is None:
            state.undated_serves += 1
            state.apply(label, key, None)
            continue
        state.apply(label, key, at)


def refresh(
    path: Path | None = None,
    *,
    now: datetime | None = None,
    window_days: float | None = None,
    force: bool = False,
) -> UsageReading:
    """Bring the roll-up up to date and return the reading. NEVER raises.

    **Bounded, incrementally.** One call reads only the bytes appended since the
    last checkpoint (``state.offset`` → EOF) and never the whole stream. A read
    of the signal is therefore O(new events), not O(history) — the constraint
    ``#14011`` states as *"a bounded roll-up, so a read never replays the whole
    ledger"*.

    **Recomputed from cold when it must be.** The checkpoint is a cache. It is
    discarded and the stream replayed from zero when it is missing, unreadable,
    written by another :data:`CHECKPOINT_VERSION`, or when the stream is not the
    one it was built from — a different device/inode, a shorter file (truncated
    or rotated), or a fingerprint mismatch on the consumed prefix (an inode
    recycled by ``rm`` + recreate). Replay must produce the SAME reading as the
    incremental path; that is the invariant the tests hold it to, and it is what
    makes the signal derived rather than authoritative.

    ``window_days`` prunes entries whose last serve is older than the window,
    bounding the roll-up's memory. ``None`` (default) keeps every entry, so
    ``serve_count`` is exact. Pruning is a separate, opt-in bound and it is
    reported (:attr:`UsageReading.pruned`, :attr:`UsageReading.pruned_before`).
    """
    target = Path(path) if path is not None else usage_path()
    ref = now or datetime.now(UTC)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=UTC)
    window = window_days if window_days is not None else _window_days()
    window_started: str | None = None
    if window is not None:
        window_started = (ref - timedelta(days=window)).isoformat(timespec="seconds")

    if not target.exists():
        # ⛔ A MISSING STREAM IS NOT "NOTHING IS HOT". The reading carries
        # `served_total == 0` AND `stale` so a consumer cannot read an unarmed
        # signal as a confident cold corpus.
        return _empty_reading(target, replayed=True, stale=True, error="no stream")

    ckpt = checkpoint_path(target)
    with _REFRESH_LOCK, _file_lock(ckpt):
        try:
            st = target.stat()
        except OSError as e:  # pragma: no cover — a stat race
            return _empty_reading(target, replayed=True, stale=True, error=str(e))

        state: _State | None = None if force else _load_checkpoint(ckpt)
        replayed = False
        if state is not None and not _state_matches_stream(state, target, st):
            # The stream is not the one this checkpoint describes. A cache built
            # over a different prefix is worse than no cache: it would fold new
            # bytes onto another file's history.
            state = None
        if state is None:
            state = _State()
            replayed = True
        start = state.offset

        try:
            with open(target, "rb") as fh:
                fh.seek(start)
                chunk = fh.read()
        except OSError as e:  # pragma: no cover — an unreadable stream
            return _empty_reading(target, replayed=replayed, stale=True, error=str(e))

        # ⛔ CONSUME ONLY COMPLETE LINES. A torn tail (a crash mid-write, or a
        # writer whose batch we raced) is left UNCONSUMED, so the next refresh
        # re-reads it once it is whole — and is folded exactly once, not zero
        # times and not twice.
        cut = chunk.rfind(b"\n")
        whole = chunk[: cut + 1] if cut >= 0 else b""
        if whole:
            _consume(state, whole)
            state.offset = start + len(whole)
        state.source_size = st.st_size
        state.source_dev = st.st_dev
        state.source_ino = st.st_ino
        # A FIXED-length prefix (see HEAD_FINGERPRINT_BYTES): the stream is
        # append-only, so the consumed prefix can never change without the file
        # being replaced.
        state.head_len = min(state.offset, HEAD_FINGERPRINT_BYTES)
        state.head_sha = _head_sha(target, state.head_len)
        if window is not None:
            state.prune(ref - timedelta(days=window))
        saved = _save_checkpoint(ckpt, state)
        return _to_reading(
            state,
            target,
            replayed=replayed,
            # An unsaved checkpoint is not a wrong READING — this one is exact
            # for the bytes consumed. It is only "the next refresh redoes work".
            # `stale` stays False; `checkpoint_kept` would be a separate fact and
            # is not worth a field the scorer could misread.
            stale=False,
            window_started=window_started,
            error=None if saved else "checkpoint not persisted",
        )


def _state_matches_stream(state: _State, target: Path, st: os.stat_result) -> bool:
    """Is this checkpoint a fold of THIS stream's consumed prefix? (see refresh)

    Three independent checks, each of which alone would miss a real rotation:
    the device/inode pair catches a replaced file, the size catches a truncated
    one, and the head fingerprint catches an inode RECYCLED by `rm` + recreate
    (the size check would pass if the new file happens to be longer).
    """
    if state.source_dev is not None and st.st_dev != state.source_dev:
        return False
    if state.source_ino is not None and st.st_ino != state.source_ino:
        return False
    if st.st_size < state.offset:
        return False
    if state.head_len == 0:
        # Nothing was consumed, so there is no prefix to compare — the size and
        # inode checks above are the whole test.
        return True
    if state.head_sha is None:
        return False
    return _head_sha(target, state.head_len) == state.head_sha


def reading(*, path: Path | None = None, now: datetime | None = None) -> UsageReading:
    """The current signal. A convenience alias for :func:`refresh` (read-only).

    There is deliberately no cached in-process reading: the stream is appended
    by a DIFFERENT process on the hosted path, so an in-process cache would age
    silently. ``refresh`` costs one ``stat`` plus the bytes since the last
    checkpoint, i.e. nothing when nothing was served.
    """
    return refresh(path, now=now)


# ════════════════════════════════════════════════════════════════════════════
# Ops door — the same shape `python -m tortoise.read_ledger` has
# ════════════════════════════════════════════════════════════════════════════


def _main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m tortoise.usage_signal",
        description="Report the per-node usage signal (last served / serve count) "
        "derived from the serve stream, with its window and its truncation state.",
    )
    ap.add_argument("--stream", default=None, help="serve stream (default: env path)")
    ap.add_argument("--label", default=None, help="only this label")
    ap.add_argument("--top", type=int, default=20, help="rows to print (default 20)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    target = Path(a.stream) if a.stream else usage_path()
    got = refresh(target)
    if a.json:
        print(json.dumps(got.as_dict(), indent=2))
        return 0 if got.served_total or got.entries else 2
    print(f"stream           : {got.source}")
    print(f"as of            : {got.as_of}")
    if got.error:
        print(f"⚠️ status        : {got.error}")
    print(
        f"serves folded    : {got.served_total:,} over {len(got.entries):,} "
        f"coordinate(s)"
    )
    if got.window_started:
        print(f"window           : {got.window_started} → {got.as_of}")
    if got.pruned:
        print(
            f"pruned           : {got.pruned:,} entr(ies) last served at or before "
            f"{got.pruned_before} (dropped by {USAGE_WINDOW_DAYS_ENV}) — their "
            f"exact counts are gone, their recency is bounded"
        )
    if got.undated_serves:
        print(
            f"⚠️ undated       : {got.undated_serves:,} serve(s) carry no parseable "
            f"`at` — counted, but they cannot move a last-served time"
        )
    if got.unresolved_skips:
        print(
            f"⚠️ unresolved    : {got.unresolved_skips:,} returned row(s) carry no "
            f"value for their label's contract key — NOT counted as serves"
        )
    if got.lines_skipped:
        print(
            f"⚠️ {got.lines_skipped:,} unreadable line(s) skipped of "
            f"{got.lines_read + got.lines_skipped:,} — the counter is INCOMPLETE"
        )
    if got.capped:
        print(
            "⛔ the stream reached its size bound and stopped recording — this "
            "counter is TRUNCATED, not quiet. Do not score residency from it: a "
            "frozen counter ages every served node into 'cold'."
        )
    if not got.served_total:
        print(
            "⚠️ no serves folded yet — an armed-but-quiet signal measures nothing. "
            "This is a MEASUREMENT WINDOW, not a finding about the corpus."
        )
        return 2
    order = got.coldest_first(a.label)
    shown = order[: max(0, a.top)]
    print(f"\ncoldest first ({len(shown)} of {len(order):,}):")
    for label, key in shown:
        got_entry = got.entry(label, key)
        if got_entry is None:  # pragma: no cover — coldest_first only yields entries
            continue
        print(
            f"  {label:<10} {key[:48]:<48} "
            f"last={got_entry.last_served_at} n={got_entry.serve_count}"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
