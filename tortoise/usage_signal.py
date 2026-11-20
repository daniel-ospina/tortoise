"""The per-node USAGE signal — *when was this node last served?* (#14011).

The owner's eviction criterion (`#13989`, 2026-11-20, verbatim):

> *"2 criteria should be recency of usage (searched), not recency of creation."*

Nothing in the tree could answer it. This module is that answer: a per-graph
roll-up of ``(tenant, label, key) -> (last served ts, serve count)``, derived
from an append-only serve stream, with a **bounded incremental** fold so a query
never replays the whole history.

⛔ WHY THIS IS NOT `tortoise/read_ledger.py` — READ THIS BEFORE "SIMPLIFYING" IT
================================================================================

The read ledger already records reads and already folds them to a per-key
``read_count`` / ``last_read_at``. Reusing it is the obvious move and it is
**forbidden by a recorded decision**, in two places.

`docs/architecture/SEARCH-ARCHITECTURE.md` §7, policy row *"window-local touch
counters + admission control"* (verified at this head, line 580)::

    ⚠️ The counter is WINDOW-LOCAL cache state — a different artifact from
    `tortoise/read_ledger.py`. That ledger is TENANT-WIDE and is explicitly not a
    product input: `STORAGE-ARCHITECTURE.md` §17.4.6 states that reading it for
    any product decision "would be a violation" (it exists to measure `f`).
    Wiring it as the eviction signal is exactly the violation it names.

`docs/architecture/STORAGE-ARCHITECTURE.md` §17.4.6, closing the §3-deviation
paragraph: *"it would be a violation if any product decision read it."*

So this module owns its own artifact, with its own bound, whose ONE purpose is
this policy input. §7 asks for exactly that: *window-local cache state*. Neither
the writer nor the reader here ever opens a ledger file.

⚠️ **A bounded stream can freeze, here as in the ledger — and that is why the
DOOR fails closed.** ``USAGE_MAX_BYTES`` stops recording exactly the way the
ledger's bound does. The difference is what happens to a consumer: this module's
ordering door (:meth:`UsageReading.coldest_first`) **refuses to order a capped
or pooled counter at all**, so a frozen counter evicts *nothing* — the safe
direction — instead of ranking live nodes as cold and evicting the hottest ones.
A fold that merely reported a number would reproduce the hazard; refusing at the
door is what removes it.

WHAT THE LEDGER *IS* USED FOR HERE: as the instrument that validates this
counter. ``tests/test_usage_signal.py::test_ordering_matches_the_ledgers_own_record``
replays a scripted search pattern through both and requires the orderings to
agree — ``#14011``'s own "matches the ledger's own record of that pattern". That
is an instrument checking a counter, which is what an instrument is for; §7
forbids the reverse direction (the product reading the ledger).

WHAT A "SERVE" IS
=================

One line per returned row, written in ONE ``O_APPEND`` batch per read (the
ledger's own measured discipline, and for the same reason: a per-event
open/write/close is ~610 µs, i.e. 5 ms on a ten-row read — an instrument that
slows the path it measures). A serve is recorded at the **funnel that hands rows
to a caller**, so what it counts is *"this node was in an answer somebody saw"*,
which is the question the policy asks. ``get_point`` is deliberately NOT a
recording site, for the ledger's own reason: the write paths read through it, so
counting it would mark written-but-never-read nodes as served — a node would
look hot because it was created.

⛔ NO WRITE TOUCHES A NODE. The signal lives in a file beside the store, never in
the graph: a ``last_searched_at`` property written on every search is write
amplification into the very store the window exists to bound. This module
imports no graph client and opens no connection —
``test_no_graph_dependency`` pins that, and pins it against the import spellings
this package actually uses.

TENANCY — the coordinate has THREE parts, not two
=================================================
``(tenant, label, key)``. The ledger carries a tenant for a measured reason
(§17.4.6: *"a hosted process serving many tenants would otherwise write every
event under ``tenant = None``"*), and it matters MORE here, because this artifact
is the product input: on one shared graph, two tenants' ``(Point, id)`` pairs
would otherwise pool, and one tenant's eviction would be scored on a stranger's
traffic — a node looks hot because somebody else read it, which is the flattering
direction. So the tenant is recorded on the event, and a reading that sees more
than one partition **refuses to order** (``pooled``) rather than pooling.

DERIVED, NEVER AUTHORITATIVE
============================
The stream is the record of what was served; the checkpoint is a **cache of a
fold**, and it may be deleted at any time. ``refresh`` then replays the stream
from zero and produces the same reading — the invariant
``test_incremental_equals_cold_replay`` and
``test_restart_survives_on_the_checkpoint_alone`` hold it to.

⚠️ **ONE RESIDUAL, NAMED RATHER THAN IMPLIED.** A rotation that rewrites the
stream's already-folded bytes IN PLACE at the SAME SIZE (a `copytruncate` whose new
content is exactly as long as the old) is undetectable to any incremental fold: the
inode, the size and the consumed prefix all still match, so there is nothing to
notice. The stream's contract is APPEND-ONLY (`O_APPEND`); a `copytruncate`
rotation must delete the checkpoint, and the alternative — re-hashing the whole
consumed prefix every call — is the full replay this module exists to avoid. A
SHRINK is detected (`st_size < offset` ⇒ cold replay), and so is a replacement
(`dev`/`ino`), including an inode recycled by `rm` + recreate (the prefix
fingerprint, which is now taken from the descriptor that folded the bytes rather
than from a fresh `open`).

⚠️ **AN EMPTY READING IS NOT "NOTHING IS HOT".** A freshly armed signal reports
``served_total == 0`` because nothing has been *observed* yet, not because
nothing was used. A missing stream, an EMPTY stream (a `copytruncate` rotation
mid-window) and an unpartitioned read all say so on the reading's face — with its
window (``observed_from``) and its counters — rather than reporting a confident
zero. ``test_an_empty_stream_is_not_a_confident_zero`` is that guard: without it,
after a rotation every node is "unserved", the caller falls back to creation
recency, and the reading still looks healthy.

FAIL DIRECTION
==============
A read never fails because the signal did. Every write failure is swallowed and
warned once, and ``refresh`` NEVER raises into the caller that wanted a search
result: a failure returns an **EMPTY, ``stale`` reading whose** ``error`` **names
the failure** — it does not return a remembered earlier fold, because there is no
cached in-process reading (deliberately: the stream is appended by a different
process). A checkpoint that cannot be read is a CACHE MISS, not an error: the
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
import math
import os
import stat
import threading
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .env_truthy import is_truthy

logger = logging.getLogger(__name__)

#: The env flag that makes the serve funnel RECORD (the #4097 truthy contract).
USAGE_FLAG_ENV = "TORTOISE_USAGE_SIGNAL"

#: The serve stream. Default: beside the embedded store, keyed by STORE TARGET
#: so two graphs on one host do not pool into one counter.
USAGE_PATH_ENV = "TORTOISE_USAGE_SIGNAL_PATH"

#: The stream's size bound. On the bound it writes one `capped` line and stops —
#: and the ordering door then REFUSES (see the module docstring): a frozen counter
#: must evict nothing, never rank live nodes as cold.
USAGE_MAX_BYTES_ENV = "TORTOISE_USAGE_SIGNAL_MAX_BYTES"
DEFAULT_MAX_BYTES = 16 * 1024 * 1024

#: Optional trailing window for the ROLL-UP (not for the stream). ``None`` —
#: the default — keeps every entry, so ``serve_count`` is exact. Set it and an
#: entry whose last serve is older than the window is dropped and reported only
#: as ``pruned_before``: its exact count is gone, its recency is bounded. Opt-in,
#: because the scorer consumes RECENCY and a bounded "not served since
#: <watermark>" answers that.
USAGE_WINDOW_DAYS_ENV = "TORTOISE_USAGE_SIGNAL_WINDOW_DAYS"

EVENT_SERVE = "serve"
EVENT_CAPPED = "capped"
EVENT_UNRESOLVED = "unresolved"

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
#: stores the length it used (``_State.head_len``), which stays comparable because
#: the stream is APPEND-ONLY.
HEAD_FINGERPRINT_BYTES = 8192

#: The tenant filter's "every partition" sentinel. Distinct from ``None``, which
#: names the ledger's own single namespace (a self-hosted instance).
ALL_TENANTS = "\x00all-tenants"

#: The CLI's "no --tenant given" sentinel — distinct from an empty string, which
#: the door turns into the unpartitioned ``None`` namespace.
_CLI_ALL = "\x00cli-absent"

_warned: set[str] = set()

#: Re-entrancy guard for one process; the cross-process lock is `_file_lock`.
_REFRESH_LOCK = threading.Lock()

#: The events the LAST `_append_many` kept (i.e. not dropped for size or
#: encoding). `record_serves` needs them to report how many of ITS caller's rows
#: provably reached the file — the intended count is not the delivered one.
_LAST_KEPT = threading.local()


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


def tenant() -> str | None:
    """The tenancy partition an event belongs to (the request's org, else none).

    ⛔ **Reuses the ledger's own resolver on purpose.** It is a pure function of
    the env override and the auth contextvar — not a read of that artifact — and
    sharing it means the two writers partition a hosted process the SAME way. A
    second implementation is how ``tenant = None`` came to be written for every
    event of a multi-tenant deployment (§17.4.6), which pools tenants and biases
    a score in the flattering direction.
    """
    from . import read_ledger

    return read_ledger.tenant()


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


def checkpoint_path(
    stream: Path | str | None = None, *, tenant: str | None = ALL_TENANTS
) -> Path:
    """The roll-up checkpoint beside a stream, KEYED BY PARTITION.

    ⛔ One checkpoint per ``(stream, partition)``, not one per stream. A partition
    filter changes which events a fold contains, so a single checkpoint had to be
    thrown away whenever the filter changed — and the default is ``ALL_TENANTS``
    while :meth:`UsageReading.refusal_reason` tells a pooled caller to "order one
    tenant at a time", i.e. exactly the alternation that would then re-fold the
    whole stream on every call (measured: 7.4 KB replay per call on a 4-serve
    stream, bounded only by ``USAGE_MAX_BYTES``).

    ⚠️ A partition tag, not the whole tenant id: the file sits beside the store,
    and a tenant id in a filename is a tenant identifier on disk.
    """
    target = Path(stream) if stream is not None else usage_path()
    tag = hashlib.sha256(
        (ALL_TENANTS if tenant == ALL_TENANTS else f"t:{tenant!r}").encode()
    ).hexdigest()[:8]
    return target.with_suffix(target.suffix + f".{tag}.rollup.json")


def _max_bytes() -> int:
    raw = os.environ.get(USAGE_MAX_BYTES_ENV)
    try:
        return int(raw) if raw else DEFAULT_MAX_BYTES
    except ValueError:
        return DEFAULT_MAX_BYTES


def _window_days(explicit: float | None = None) -> float | None:
    """The pruning window in days, or ``None`` for "keep every entry".

    ⛔ A VALUE THE OPERATOR MEANT AS A BOUND IS NEVER SILENTLY "NO BOUND". Every
    arm that cannot honour the stated bound WARNS and then degrades to "keep
    everything" in the open — the typo arm, the ``0`` arm and the negative arm
    alike. The numeric arm used to fall through silently, so
    ``TORTOISE_USAGE_SIGNAL_WINDOW_DAYS=0`` was byte-identical to *unconfigured*
    (``pruned=0``, ``pruned_before=None``, ``window_started=None``) while the
    roll-up ran unbounded — the same quiet direction the comment below refuses,
    on the one parameter whose whole job is bounding memory.

    A NON-FINITE value is refused for a second reason: ``timedelta(days=nan)``
    raises, and ``refresh`` promises it never does.
    """
    if explicit is not None:
        return _checked_window(explicit, repr(explicit), "the `window_days` argument")
    raw = os.environ.get(USAGE_WINDOW_DAYS_ENV)
    if raw is None or not raw.strip():
        return None
    try:
        days = float(raw)
    except ValueError:
        # ⛔ A MISTYPED WINDOW IS NOT "NO WINDOW". A typo'd pruning bound that
        # degrades to "keep everything" silently un-bounds the roll-up, which is
        # the quiet direction; the warning names the refusal.
        _warn_once(
            "window",
            "usage signal: %s=%r is not a day count — the roll-up is running "
            "UNBOUNDED (every entry kept); fix the value to bound it",
            USAGE_WINDOW_DAYS_ENV,
            raw,
        )
        return None
    return _checked_window(days, raw, USAGE_WINDOW_DAYS_ENV)


def _checked_window(days: float, raw: str, origin: str) -> float | None:
    """A stated window that cannot bound anything is refused LOUDLY.

    ``0`` and the negatives prune nothing (an entry is never older than
    everything), so they are the "unbounded while the operator believes
    otherwise" shape the typo arm above already refuses.
    """
    if days <= 0:
        # ⛔ AND NEITHER IS A NON-POSITIVE ONE (#14011 review finding 3). This arm
        # used to `return None` without a word, so `WINDOW_DAYS=0` (or `-1`) was
        # byte-identical to NOT CONFIGURED — `pruned=0`, `pruned_before=None` —
        # while the roll-up ran unbounded. That is the same quiet direction the
        # typo arm above exists to refuse, on the one parameter whose job is
        # bounding memory. Prune-everything at `0` would be a different reading
        # (`pruned_before` would have to be "now"), so this refuses instead.
        _warn_once(
            "window",
            "usage signal: %s=%r is not a positive day count — the roll-up is "
            "running UNBOUNDED (every entry kept); fix the value to bound it",
            origin,
            raw,
        )
        return None
    if not math.isfinite(days):
        # ⛔ AND NEITHER IS A NON-FINITE ONE. `nan`/`inf` do not prune anything
        # either, and `timedelta(days=nan)` RAISES — out of `refresh`, whose own
        # docstring promises it never raises. A separate key from the arm above so
        # an operator who fixes `0` still sees the `nan`.
        _warn_once(
            "window-nonfinite",
            "usage signal: %s=%r is not a FINITE day count — the roll-up is "
            "running UNBOUNDED (every entry kept); fix the value to bound it",
            origin,
            raw,
        )
        return None
    return days


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
    pair of readers that "could drift and nothing here would notice". Sharing it
    is also what makes the equivalence test meaningful: both artifacts key rows
    the same way, so the only thing compared is the ORDERING.

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
    kept: list[Mapping[str, Any]] = []
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
        kept.append(record)
        n_events += 1
    if not lines:
        _LAST_KEPT.kept = []
        return 0
    _LAST_KEPT.kept = kept
    target: Path | None = None
    try:
        target = path
        target.parent.mkdir(parents=True, exist_ok=True)
        cap = _max_bytes()
        size = target.stat().st_size if target.exists() else 0
        blob = "".join(lines).encode()
        # ⛔ THE BATCH ITSELF COUNTS against the bound (the ledger's own lesson:
        # checking only the pre-write size lets one batch cross the bound with no
        # marker at all). The bound is a ceiling on EVENTS, so the marker line may
        # sit a few dozen bytes over it — the marker is what makes the truncation
        # audible, and an inaudible one is the failure this module refuses.
        if size >= cap or size + len(blob) > cap:
            # The decision reads the FILE's tail, never a process latch: a
            # rotated stream that caps AGAIN must say so again, or the second stop
            # is silent and `refresh` reports `capped: false` over a file it
            # truncated.
            if not _tail_is_capped(target):
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "ab") as fh:
                    # A torn final line with no newline would make the marker
                    # unparseable AND corrupt it — the guarantee failing exactly in
                    # the case it exists for.
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
                "usage signal %s: reached its %d B bound — no further serves are "
                "recorded (rotate it or raise %s). The ordering door REFUSES a "
                "capped counter, so eviction stops rather than ranking live nodes "
                "as cold.",
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
    tenant_id: str | None = None,
) -> int:
    """Record a returned row-set as SERVED, in ONE append. Returns keys recorded.

    Called from the serve funnel only when the flag is on. ``rows`` are the dicts
    the funnel hands a caller, so what is recorded is exactly what was surfaced —
    not a candidate pool, and not the stock.

    A row the identity contract cannot address is SKIPPED (it is not served under
    a fallback key), and the skip is COUNTED so ``refresh`` can report the hole.
    """
    stamp = at or _now()
    # Same rule the ledger's own writers use: `tenant_id=None` means "resolve it
    # from the request context", and an unauthenticated context resolves to None.
    who = tenant_id if tenant_id is not None else tenant()
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
                "tenant": who,
            }
        )
    if unresolved:
        # File-level, not per-row: a count is all this counter keeps, and the
        # count is what makes the hole visible in `refresh`.
        events.append(
            {"at": stamp, "event": EVENT_UNRESOLVED, "n": unresolved, "tenant": who}
        )
    if not events:
        return 0
    written = _append_many(events, path=path or usage_path())
    if not written:
        return 0
    # ⛔ WHAT LANDED, NOT WHAT WAS INTENDED. `_append_many` may drop an over-long
    # line and a short write lands only a prefix, so counting the serve events we
    # CONSTRUCTED would report a serve the file never received — and the caller
    # that logs this number is the only evidence a deployment has.
    landed = (getattr(_LAST_KEPT, "kept", None) or [])[:written]
    return sum(1 for e in landed if e.get("event") == EVENT_SERVE)


# ════════════════════════════════════════════════════════════════════════════
# The roll-up — bounded, incremental, replayable
# ════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class UsageEntry:
    """One ``(tenant, label, key)`` coordinate and what the stream observed."""

    tenant: str | None
    label: str
    key: str
    last_served_at: str | None
    first_served_at: str | None
    serve_count: int

    @property
    def served(self) -> bool:
        return self.serve_count > 0

    @property
    def dated(self) -> bool:
        """Is this entry's recency KNOWN? (an undated serve cannot place itself)"""
        return self.last_served_at is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenant": self.tenant,
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
    source_dev: int | None = None
    source_ino: int | None = None
    head_sha: str | None = None
    head_len: int = 0
    #: The tenant filter this fold was built under. A checkpoint built for one
    #: partition may not be incrementally extended under another.
    partition: str | None = None
    #: The PRUNING WINDOW this fold was built under — the same kind of fold
    #: parameter as `partition`, and it needs the same binding. Pruning DESTROYS
    #: entries, so a windowed fold written into the shared cache was inherited by
    #: the next UNBOUNDED caller with no replay and no signal: `serve_count`
    #: returned 0 for a coordinate whose serve was still in the stream and
    #: `coldest_first` omitted it — so the node read as "no opinion" and the
    #: caller's creation-recency tie-break took over. That is the failure class
    #: this module exists to remove, delivered by a tuning knob.
    window_days: float | None = None
    lines_read: int = 0
    lines_skipped: int = 0
    served_total: int = 0
    undated_serves: int = 0
    unresolved_skips: int = 0
    capped: bool = False
    pruned: int = 0
    pruned_before: str | None = None
    #: Entries dropped by the window for having NO parseable time at all. They
    #: carry no watermark (nothing places them), so they are counted apart from
    #: `pruned` rather than letting `pruned_before` claim to describe them.
    pruned_undated: int = 0
    #: Every partition the stream has been seen to carry, bounded (all we need
    #: is "more than one"). Serialised as a sorted list.
    tenants: set[str | None] = field(default_factory=set)
    #: The earliest event `at` the STREAM contains — the fact that says a
    #: rotation restarted the window while the history is gone.
    observed_from: str | None = None
    #: ``(tenant, label, key)`` -> ``[last, first, count]``.
    entries: dict[Any, list[Any]] = field(default_factory=dict)

    def apply(self, coord: Any, at: str | None) -> None:
        slot = self.entries.get(coord)
        parsed = _stamp(at) is not None
        if slot is None:
            self.entries[coord] = [at if parsed else None, at if parsed else None, 1]
            return
        slot[2] += 1
        if parsed:
            if slot[0] is None or _stamp_cmp(at, slot[0]) > 0:
                slot[0] = at
            if slot[1] is None or _stamp_cmp(at, slot[1]) < 0:
                slot[1] = at

    def prune(self, before: datetime) -> bool:
        """Drop entries last served before `before`. Returns whether any went.

        ⛔ A PRUNED ENTRY LOSES ITS COUNT, and the reading says so: it is reported
        only as "not served since ``pruned_before``". That is enough for a
        RECENCY score (which is what consumes this) and it is honest about what
        was dropped — silently keeping a decayed count would be a number the
        stream no longer supports.

        ⛔ **EVERY entry class ages, or "bounded" is a claim about one of them.**
        The first cut skipped an entry whose stored ``last`` is not a parseable
        stamp (``apply(..., None)`` stores ``None``; a torn-then-repaired line can
        store junk), so a roll-up of undated serves grew without bound at EVERY
        window while still printing ``pruned``/``undated_serves`` as counters —
        the bound failing OPEN and SILENTLY, on the one property it advertises.
        Those entries are now dropped too, counted separately in
        ``pruned_undated`` because they have no watermark to name: nothing says
        WHEN they were served, only that nothing places them inside the window.

        ⚠️ This is NOT a reversal of "an undated serve sorts WARMEST". That rule
        governs the ORDERING door, where evicting a node on unknown recency is the
        dangerous direction. Prune governs MEMORY, an entry with no time at all
        cannot be shown to belong to the window, and the survivors are still
        ordered warmest-first.
        """
        dropped: list[Any] = []
        undated: list[Any] = []
        watermark: str | None = None
        for coord, slot in self.entries.items():
            last = slot[0]
            last_dt = _stamp(last) if isinstance(last, str) else None
            if last_dt is None:
                undated.append(coord)
            elif last_dt < before:
                dropped.append(coord)
                if watermark is None or _stamp_cmp(last, watermark) > 0:
                    watermark = last
        for coord in dropped + undated:
            del self.entries[coord]
        if undated:
            self.pruned_undated += len(undated)
        if dropped:
            self.pruned += len(dropped)
            if watermark is not None and (
                self.pruned_before is None
                or _stamp_cmp(watermark, self.pruned_before) > 0
            ):
                self.pruned_before = watermark
        return bool(dropped or undated)


@dataclass(frozen=True)
class UsageReading:
    """The usage signal as of one fold — DERIVED, never authoritative.

    Every field is here so a consumer cannot quote a bare ordering: a score
    computed over a capped stream, over a pooled set of tenants, or over a window
    that started five minutes ago must be quotable-with-its-caveat or not quoted
    at all.
    """

    source: str
    as_of: str
    entries: Mapping[Any, UsageEntry]
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
    #: This refresh's own work — the BOUND, observable. A refresh that replayed
    #: the whole stream would report ``bytes_folded`` equal to the file size, so a
    #: future "simplify by replaying" refactor cannot pass as unchanged.
    bytes_folded: int = 0
    lines_folded: int = 0
    serves_folded: int = 0
    observed_from: str | None = None
    window_started: str | None = None
    #: Entries the window dropped because nothing places them in time at all — the
    #: class that used to age by NO path (`prune` skipped it, so the roll-up grew
    #: unbounded at every window). Counted apart from :attr:`pruned`, whose
    #: `pruned_before` watermark cannot describe an entry that carries no time.
    pruned_undated: int = 0
    tenants: int = 0
    pooled: bool = False
    error: str | None = None

    # ── honesty ────────────────────────────────────────────────────────────

    @property
    def orderable(self) -> bool:
        """May a scorer order this reading at all? (fail-closed when it may not)

        ``False`` for a CAPPED counter (frozen — ordering it would rank live
        nodes as cold and evict the hottest), for a POOLED one (more than one
        tenant's traffic in one roll-up — ordering it scores one tenant on
        another's reads), and for a STALE one (no stream).
        """
        return not (
            self.capped
            or self.pooled
            or self.stale
            or self.served_total == 0
            or self.lines_skipped > 0
        )

    def refusal_reason(self) -> str | None:
        """Why :attr:`orderable` is False, in words a caller can log."""
        if self.stale:
            return f"no usable counter ({self.error or 'stale'})"
        if self.capped:
            return (
                "the stream reached its size bound and STOPPED recording — a frozen "
                "counter ages every served node into 'cold', so ordering it would "
                "evict the hottest nodes in the cache"
            )
        if self.pooled:
            return (
                f"{self.tenants} tenant partition(s) are folded together — order one "
                f"tenant at a time (`refresh(tenant=...)`), or a node looks hot "
                f"because a different tenant read it"
            )
        if self.lines_skipped > 0:
            # ⛔ A LOST LINE IS THE ONLY TRACE OF A LOST `capped` MARKER. A torn
            # marker that no later append heals re-opens the frozen-counter door
            # with nothing else to notice it, so an incomplete counter refuses for
            # the same reason a capped one does.
            return (
                f"{self.lines_skipped} unreadable stream line(s) — the counter is "
                f"INCOMPLETE, and a lost `capped` marker is indistinguishable from a "
                f"stream that never capped"
            )
        if self.served_total == 0:
            return (
                "no serves observed yet — an armed-but-quiet counter has no opinion, "
                "and treating every candidate as unserved is the creation-recency "
                "fallback this signal exists to remove"
            )
        return None

    def staleness_seconds(self, now: datetime | None = None) -> float | None:
        """How far behind the stream this reading is, or ``None`` if unknown."""
        ref = now or datetime.now(UTC)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=UTC)
        parsed = _stamp(self.as_of)
        return (ref - parsed).total_seconds() if parsed is not None else None

    # ── the door the scorer uses ───────────────────────────────────────────

    def entry(
        self, label: str, key: str, *, tenant: str | None = None
    ) -> UsageEntry | None:
        """The entry for one coordinate in ONE partition.

        ``tenant`` defaults to ``None`` — the unpartitioned namespace a self-hosted
        instance writes, and what a single-org deployment resolves to when there is
        no request context.

        ⛔ **EVERY READ GOES THROUGH THE DOOR, not just :meth:`coldest_first`.** The
        accessors are what the residency policy is documented to compose with
        (``recency_key`` plus the ``#13989`` class booster), so a frozen or pooled
        counter returning a healthy-looking number HERE is the hazard the door
        exists to remove — and this is the door it recommends. A refused reading
        answers ``None``/``0`` for every coordinate, which composes to "no signal"
        rather than to a ranking.
        """
        if not self.orderable:
            return None
        return self.entries.get((tenant, label, key))

    def last_served(
        self, label: str, key: str, *, tenant: str | None = None
    ) -> str | None:
        got = self.entry(label, key, tenant=tenant)
        return got.last_served_at if got is not None else None

    def serve_count(
        self, label: str, key: str, *, tenant: str | None = None
    ) -> int:
        got = self.entry(label, key, tenant=tenant)
        return got.serve_count if got is not None else 0

    def recency_key(
        self, label: str, key: str, *, tenant: str | None = None
    ) -> tuple[float, int, str] | None:
        """``(last_served_epoch, serve_count, key)`` — smallest = COLDEST.

        Returns ``None`` when this counter has **no opinion** about the
        coordinate (never served since the signal was armed). ⛔ ``None`` is not
        the same as "cold": a node written a minute ago has no signal, and
        ranking it coldest would re-impose creation recency — the policy the
        owner's criterion replaces.

        An entry with a COUNT but no parseable time (an undated serve) sorts at
        ``+inf``, i.e. WARMEST: it is known to have been served and its recency
        is unknown, so the safe reading is "do not evict this". Ranking it
        coldest is the module's stated worst case reachable from one bad ``at``.

        This is an ORDERING, not a score. The residency policy (``#13989``: usage
        recency plus a class booster for ``Object``/``Subject``/``Source``)
        composes it with its own terms; the module deliberately embeds no
        formula, because the formula is policy and its home is not here.
        """
        got = self.entry(label, key, tenant=tenant)
        if got is None:
            return None
        parsed = _stamp(got.last_served_at) if got.last_served_at else None
        if parsed is None:
            return (float("inf"), got.serve_count, key)
        return (parsed.timestamp(), got.serve_count, key)

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
        stable: ties on the last served time fall to the lower ``serve_count``,
        then to the key string, so two runs over the same stream agree exactly.

        ``label`` narrows to one population (the evictor sweeps ONE licensed
        population per pass). ``keys`` narrows to a candidate set.

        ⛔ **REFUSES A READING IT MAY NOT ORDER, and refuses by returning
        NOTHING.** :attr:`orderable` False (capped, pooled, stale) yields ``[]``
        plus a :meth:`refusal_reason` — an empty pass evicts nothing, which is
        the safe direction; the alternative is ranking a frozen counter and
        deleting the hottest nodes. A caller that ignores the refusal loses
        throughput, never data.

        ⛔ **UNSERVED KEYS ARE NOT RETURNED.** A coordinate the counter has never
        seen is not "the coldest thing" — it is "this signal has no opinion", and
        a newly-written node is the commonest case. Folding them in would make
        this module evict brand-new data, i.e. exactly the creation-recency policy
        the owner's criterion replaces. They are available as
        :meth:`unserved_keys`, so the caller applies its own tie-break rather than
        inheriting one from here.
        """
        if not self.orderable:
            return []
        wanted = set(keys) if keys is not None else None
        rows: list[tuple[tuple[float, int, str], tuple[str, str]]] = []
        for who, lbl, key in self.entries:
            if label is not None and lbl != label:
                continue
            if wanted is not None and key not in wanted:
                continue
            order = self.recency_key(lbl, key, tenant=who)
            if order is None:  # pragma: no cover — the key came from `entries`
                continue
            rows.append((order, (lbl, key)))
        rows.sort(key=lambda pair: pair[0])
        return [coord for _order, coord in rows]

    def unserved_keys(
        self,
        candidates: Iterable[str],
        *,
        label: str | None = None,
    ) -> list[str]:
        """Which of `candidates` this signal has no opinion about (any label).

        ⛔ ``[]`` on a refused reading. On a STALE one this would otherwise declare
        **every** candidate unserved — precisely the creation-recency fallback this
        module exists to remove, delivered with no marker at all.
        """
        if not self.orderable:
            return []
        if label is None:
            seen = {key for (_t, _lbl, key) in self.entries}
        else:
            seen = {key for (_t, lbl, key) in self.entries if lbl == label}
        return [key for key in candidates if key not in seen]

    def undated_entries(self) -> list[UsageEntry]:
        """Entries known to be served but not placeable in time (sort WARMEST)."""
        if not self.orderable:
            return []
        return [e for e in self.entries.values() if not e.dated]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "as_of": self.as_of,
            "entries": len(self.entries),
            "served_total": self.served_total,
            "pruned": self.pruned,
            "pruned_before": self.pruned_before,
        "pruned_undated": self.pruned_undated,
            "capped": self.capped,
            "lines_read": self.lines_read,
            "lines_skipped": self.lines_skipped,
            "undated_serves": self.undated_serves,
            "unresolved_skips": self.unresolved_skips,
            "offset": self.offset,
            "bytes_folded": self.bytes_folded,
            "lines_folded": self.lines_folded,
            "serves_folded": self.serves_folded,
            "replayed": self.replayed,
            "stale": self.stale,
            "observed_from": self.observed_from,
            "window_started": self.window_started,
            "tenants": self.tenants,
            "pooled": self.pooled,
            "orderable": self.orderable,
            "refusal": self.refusal_reason(),
            "error": self.error,
        }


# ── checkpoint (a CACHE of a fold) ─────────────────────────────────────────


def _serialise(state: _State) -> dict[str, Any]:
    """The checkpoint's on-disk form. An ARRAY per entry, so keys round-trip."""
    entries: list[list[Any]] = []
    for (tenant, label, key), slot in state.entries.items():
        entries.append([tenant, label, key, slot[0], slot[1], slot[2]])
    return {
        "version": CHECKPOINT_VERSION,
        "offset": state.offset,
        "window_days": state.window_days,
        "source_dev": state.source_dev,
        "source_ino": state.source_ino,
        "head_sha": state.head_sha,
        "head_len": state.head_len,
        "partition": state.partition,
        "lines_read": state.lines_read,
        "lines_skipped": state.lines_skipped,
        "served_total": state.served_total,
        "undated_serves": state.undated_serves,
        "unresolved_skips": state.unresolved_skips,
        "capped": state.capped,
        "pruned": state.pruned,
        "pruned_before": state.pruned_before,
        "pruned_undated": state.pruned_undated,
        "tenants": sorted(t for t in state.tenants if t is not None)
        + ([None] if None in state.tenants else []),
        "observed_from": state.observed_from,
        "entries": entries,
    }


def _deserialise(payload: Any) -> _State | None:
    """Rebuild a checkpoint, or ``None`` when it cannot be trusted.

    ⛔ EVERY FIELD IS VALIDATED, INCLUDING THE ONE CROSS-FIELD INVARIANT the
    prefix check depends on: ``head_len == min(offset, HEAD_FINGERPRINT_BYTES)``.
    A version-1 checkpoint whose fields are each individually valid but which
    says ``head_len = 0`` makes ``_state_matches_stream`` short-circuit and
    disable prefix verification entirely — the only check that catches an
    in-place, same-size replacement. Verified: without that invariant, editing
    only the checkpoint's ``head_len`` merged a replaced stream's history onto
    the old fold and reported it as current.

    Returning a partial state would make the incremental fold start from an
    offset the entries do not describe — the one way this module could produce a
    WRONG ordering instead of an empty one.
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("version") != CHECKPOINT_VERSION:
        return None
    try:
        offset = int(payload["offset"])
        head_len = int(payload.get("head_len") or 0)
        if offset < 0 or head_len < 0:
            return None
        if offset and head_len != min(offset, HEAD_FINGERPRINT_BYTES):
            return None
        if not offset and head_len:
            return None
        partition = payload.get("partition")
        if partition is not None and not isinstance(partition, str):
            return None
        raw_window = payload.get("window_days")
        if raw_window is not None:
            try:
                raw_window = float(raw_window)
            except (TypeError, ValueError):
                return None
            if not raw_window > 0:
                return None
        state = _State(
            offset=offset,
            window_days=raw_window,
            source_dev=(
                int(payload["source_dev"]) if payload.get("source_dev") is not None else None
            ),
            source_ino=(
                int(payload["source_ino"]) if payload.get("source_ino") is not None else None
            ),
            head_sha=(
                str(payload["head_sha"]) if payload.get("head_sha") is not None else None
            ),
            head_len=head_len,
            partition=partition,
            lines_read=int(payload.get("lines_read") or 0),
            lines_skipped=int(payload.get("lines_skipped") or 0),
            served_total=int(payload.get("served_total") or 0),
            undated_serves=int(payload.get("undated_serves") or 0),
            unresolved_skips=int(payload.get("unresolved_skips") or 0),
            capped=bool(payload.get("capped")),
            pruned=int(payload.get("pruned") or 0),
            pruned_undated=int(payload.get("pruned_undated") or 0),
            pruned_before=(
                str(payload["pruned_before"])
                if payload.get("pruned_before") is not None
                else None
            ),
            observed_from=(
                str(payload["observed_from"])
                if payload.get("observed_from") is not None
                else None
            ),
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if state.offset and state.head_sha is None:
        return None
    raw_tenants = payload.get("tenants")
    if not isinstance(raw_tenants, list):
        return None
    for who in raw_tenants:
        if who is not None and not isinstance(who, str):
            return None
        state.tenants.add(who)
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return None
    for row in entries:
        if not (isinstance(row, list) and len(row) == 6):
            return None
        who, label, key, last, first, count = row
        if who is not None and not isinstance(who, str):
            return None
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
        state.entries[(who, label, key)] = [last, first, n]
    # ⛔ THE CROSS-FIELD INVARIANT, in the shape the `head_len` one has. The bind
    # is empty when `tenants` is, so a checkpoint claiming no partitions while its
    # ENTRIES span two would open the ordering door on exactly the reading it
    # exists to refuse — one tenant's node scored on another's reads. A checkpoint
    # that disagrees with itself is a cache MISS, never a half-trusted state.
    if {who for (who, _lbl, _key) in state.entries} - state.tenants:
        return None
    return state


def _load_checkpoint(target: Path) -> _State | None:
    """Read a checkpoint, or ``None`` (a CACHE MISS) for every way it can fail.

    ⛔ ``read_text`` raises **``UnicodeDecodeError``** on bytes that are not
    UTF-8, and that is a ``ValueError`` — so an ``except OSError`` alone let it
    escape, even though ``refresh`` promises twice in its own docstring that it
    NEVER raises and that a checkpoint it cannot read is a cache miss. It is
    raised BEFORE the ``json.loads`` try below, so that handler could not catch
    it either; a corrupt or foreign-written checkpoint took `refresh` out at the
    one function that promises it cannot. ``RecursionError`` (deeply nested JSON)
    is the same class one step further in.
    """
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        # OSError: unreadable. ValueError: NOT UTF-8 — decode fails before any
        # parse, which is why this arm has to be here and not at `json.loads`.
        return None
    try:
        return _deserialise(json.loads(raw))
    except (ValueError, RecursionError):
        return None


def _save_checkpoint(target: Path, state: _State) -> bool:
    """Write the checkpoint ATOMICALLY. Returns False when it could not be kept.

    ⛔ `os.replace` on a sibling temp file: a partial write that replaced a GOOD
    checkpoint would throw away fold progress on every crash. The rename is the
    atomic step; a lost write degrades to a cache miss, never to a partial state.
    """
    tmp = target.with_suffix(target.suffix + f".tmp{os.getpid()}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # ⛔ 0600, THE SAME MODE THE STREAM IS CREATED WITH. `write_text` uses the
        # umask's mode (0644 on a default box), and this file holds the SAME
        # coordinates the 0600 exists to protect — a `Source.url` or a
        # `Subject`/`Object` key identifies a tenant's content. On a 0755 store
        # directory that put every tenant's keys world-readable beside a
        # 0600 stream.
        blob = json.dumps(_serialise(state), separators=(",", ":"), sort_keys=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fd = -1  # fdopen owns it now
                fh.write(blob)
        finally:
            if fd != -1:  # pragma: no cover — fdopen itself failed
                with contextlib.suppress(OSError):
                    os.close(fd)
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
    stream prefix, so the loser of a race simply re-does work — but two processes
    writing the checkpoint concurrently WOULD interleave offsets. Best-effort: a
    filesystem without `flock` falls through rather than failing the read.
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


def _stream_exists(target: Path) -> bool:
    """Does the stream exist? An unreadable `stat` is a NO, never a raise."""
    try:
        return target.exists()
    except OSError:  # pragma: no cover — a vanished mount / permission change
        return False


def _empty_reading(
    target: Path, *, replayed: bool, stale: bool, error: str | None
) -> UsageReading:
    return UsageReading(
        source=str(target),
        as_of=_now(),
        entries={},
        served_total=0,
        pruned=0,
        pruned_before=None,
        pruned_undated=0,
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
    bytes_folded: int = 0,
    lines_folded: int = 0,
    serves_folded: int = 0,
    error: str | None = None,
) -> UsageReading:
    entries: dict[Any, UsageEntry] = {}
    for (who, label, key), slot in state.entries.items():
        entries[(who, label, key)] = UsageEntry(
            tenant=who,
            label=label,
            key=key,
            last_served_at=slot[0],
            first_served_at=slot[1],
            serve_count=int(slot[2]),
        )
    # ⛔ DERIVED FROM THE ENTRIES, which is what `coldest_first` actually iterates —
    # not from `state.tenants`. `state.tenants` also collects a partition that
    # contributed only an UNRESOLVABLE row (no entry), so a bind on it latches a
    # permanent `pooled` refusal over a roll-up whose entries are entirely one
    # tenant's. The two sets are held in agreement by `_deserialise`.
    entry_tenants = {who for (who, _lbl, _key) in state.entries}
    named = {t for t in entry_tenants if t is not None}
    return UsageReading(
        source=str(target),
        as_of=_now(),
        entries=entries,
        served_total=state.served_total,
        pruned=state.pruned,
        pruned_before=state.pruned_before,
        pruned_undated=state.pruned_undated,
        capped=state.capped,
        lines_read=state.lines_read,
        lines_skipped=state.lines_skipped,
        undated_serves=state.undated_serves,
        unresolved_skips=state.unresolved_skips,
        offset=state.offset,
        replayed=replayed,
        stale=stale,
        bytes_folded=bytes_folded,
        lines_folded=lines_folded,
        serves_folded=serves_folded,
        observed_from=state.observed_from,
        window_started=window_started,
        # One namespace is not "pooled": a self-hosted instance writes
        # `tenant = None` and a single-org deployment writes one id. It is the
        # SECOND distinct partition that makes an ordering score one tenant on
        # another's traffic.
        tenants=len(named) + (1 if None in entry_tenants else 0),
        pooled=len(entry_tenants) > 1,
        error=error,
    )


def _consume(state: _State, chunk: bytes, *, partition: str | None) -> tuple[int, int]:
    """Fold a byte chunk of NEW stream bytes into `state`, in FILE ORDER.

    Returns ``(lines_folded, serves_folded)`` for THIS chunk — the bound, made
    observable so "incremental" is a measurement rather than a claim.

    Only COMPLETE lines are consumed (the caller cuts at the last newline), so a
    torn tail is re-read next time rather than lost.
    """
    folded = 0
    serves = 0
    for raw in chunk.split(b"\n"):
        line = raw.strip()
        if not line:
            continue
        folded += 1
        # A line that is not whole (the caller's cut should prevent it) or not JSON
        # is COUNTED, never silently dropped: a torn line is a LOST serve, and a
        # counter that hides its losses reads as complete.
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
            # FILE-level, exactly like the ledger's marker, and handled BEFORE the
            # partition filter: the marker carries no tenant (it is the FILE that
            # stopped), so filtering first would report a capped stream as quiet to
            # anyone reading one partition — the truncation this module exists to
            # refuse.
            state.capped = True
            continue
        at_any = ev.get("at")
        # ⛔ A PARSEABLE stamp only. `_stamp_cmp` falls back to a LEXICAL compare
        # for unparseable values (the ledger needs that fallback elsewhere), so one
        # hand-edited line with `at: "!!"` would become the permanent window start
        # the ops door prints as "a rotation restarts this".
        if _stamp(at_any) is not None and (
            state.observed_from is None or _stamp_cmp(at_any, state.observed_from) < 0
        ):
            state.observed_from = at_any
        who = ev.get("tenant")
        if who is not None and not isinstance(who, str):
            who = None
        if partition != ALL_TENANTS and who != partition:
            continue
        state.tenants.add(who)
        if kind == EVENT_UNRESOLVED:
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
        coord = (who, label, key)
        # `served_total` counts EVERY serve event folded (dated and undated) — it
        # is the "has anything been observed" guard, so it must not understate
        # usage. `undated_serves` is its NAMED SUBSET: a serve that happened but
        # cannot be placed in time. Such a serve is still counted (the counter must
        # not lose it) but it may NOT move `last_served_at` to an unknown instant —
        # a fabricated time is worse than an old one, because the scorer acts on
        # it. It sorts WARMEST for the same reason (`recency_key`).
        state.served_total += 1
        serves += 1
        if _stamp(at_any) is None:
            state.undated_serves += 1
            state.apply(coord, None)
            continue
        state.apply(coord, at_any)
    return folded, serves


def _checkpoint_matches(
    state: _State,
    head_bytes: bytes,
    st: os.stat_result,
    *,
    partition: str | None,
    window: float | None,
) -> bool:
    """Is this checkpoint a fold of THIS stream's consumed prefix? (see refresh)

    Six independent checks, each of which alone would miss a real rotation or a
    real fold-parameter change: the tenant partition and the PRUNING WINDOW the
    fold was built under (both select or destroy entries, so neither may be
    incrementally extended under a different value), the device/inode pair (a
    replaced file), the size (a truncated one), and the head fingerprint over a
    FIXED prefix length (an inode RECYCLED by `rm` + recreate — the size check
    would pass if the new file happened to be longer).

    ⛔ **THE PREFIX IS THE BYTES THE CALLER READ *BEFORE* THE BODY**, never a
    re-read from the descriptor after folding. Taking it afterwards let the
    fingerprint certify bytes the fold never saw: a `copytruncate` (SAME inode)
    landing in the read→save window was invisible to the device/inode check, the
    re-read prefix was hashed as the NEW content, the checkpoint persisted a fold
    describing a file that no longer existed, and nothing ever re-checked —
    inode, size and the re-read prefix all agreed with themselves. Measured: four
    coordinates the stream did not contain reported as current, `orderable=True`,
    permanently, while a cold replay of the same file showed a different set.
    Capturing BEFORE the body read means such a rewrite leaves the OLD digest in
    the checkpoint, the next refresh mismatches, and the fold replays from cold —
    fail-closed and self-healing.
    """
    if state.partition != partition:
        return False
    if state.window_days != window:
        return False
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
    if state.head_sha is None or len(head_bytes) < state.head_len:
        return False
    return hashlib.sha256(head_bytes[: state.head_len]).hexdigest() == state.head_sha


#: ``refresh(window_days=…)``'s "resolve it from the environment" sentinel. A
#: plain ``None`` default could not distinguish "not given" from "no window", so
#: an env-set window could not be turned OFF per call — the documented knob in one
#: direction only.
ENV_WINDOW = "env"


def refresh(
    path: Path | None = None,
    *,
    tenant: str | None = ALL_TENANTS,
    now: datetime | None = None,
    window_days: float | str | None = ENV_WINDOW,
    force: bool = False,
) -> UsageReading:
    """Bring the roll-up up to date and return the reading. NEVER raises.

    **Bounded, incrementally.** One call reads only the bytes appended since the
    last checkpoint (``state.offset`` → EOF) and never the whole stream; the work
    it did is reported as :attr:`UsageReading.bytes_folded` /
    ``lines_folded`` / ``serves_folded``, so "incremental" is a measurement and a
    refactor that reintroduced a full replay would be visible. When nothing was
    appended the fold does nothing and the checkpoint is not rewritten.

    **Recomputed from cold when it must be.** The checkpoint is a cache. It is
    discarded and the stream replayed from zero when it is missing, unreadable,
    written by another :data:`CHECKPOINT_VERSION`, built under a different
    ``tenant`` filter or ``window_days``, or when the stream is not the one it was
    built from — a
    different device/inode, a shorter file (truncated or rotated), or a
    fingerprint mismatch on the consumed prefix (an inode recycled by ``rm`` +
    recreate). Replay must produce the SAME reading as the incremental path; that
    is the invariant the tests hold it to, and it is what makes the signal derived
    rather than authoritative.

    ⚠️ **The invariant is over the SURVIVING SET, not over every cumulative
    counter.** With ``window_days`` pruning ON, a cold replay sees events an
    incremental fold already pruned away, so ``pruned`` and a re-served key's
    ``serve_count`` legitimately differ between the two paths: pruning is
    history-dependent by design, and claiming otherwise would be a claim the code
    does not make. ``test_replay_matches_the_surviving_set_under_a_window`` pins
    what IS guaranteed; with pruning off (the default) the whole reading is
    identical, which is what the other replay tests assert.

    ``tenant`` defaults to :data:`ALL_TENANTS`, which folds every partition and
    then reports ``pooled`` when it saw more than one — so the ordering door
    refuses rather than scoring one tenant on another's traffic. Pass a specific
    id (or ``None`` for the unpartitioned namespace) to order one tenant.

    ``window_days`` prunes entries whose last serve is older than the window,
    bounding the roll-up's memory. The default ``"env"`` reads
    ``TORTOISE_USAGE_SIGNAL_WINDOW_DAYS``; ``None`` means NO window and, unlike the
    env var's absence, can turn an env-set window off per call. Pruning is a
    separate, opt-in bound and it is reported (:attr:`UsageReading.pruned`,
    :attr:`UsageReading.pruned_before`).
    ⛔ It bounds EVERY entry class, not only the dated one: an entry whose serves
    all carried an unparseable ``at`` is dropped too and counted in
    :attr:`UsageReading.pruned_undated`, because nothing places it inside or
    outside the window — before that arm existed, that class grew without bound at
    any window while the reading still advertised a bound.
    """
    target = Path(path) if path is not None else usage_path()
    ref = now or datetime.now(UTC)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=UTC)
    if window_days == ENV_WINDOW:
        window = _window_days()
    elif window_days is None:
        # EXPLICITLY unbounded — the direction the env var cannot express.
        window = None
    else:
        window = _window_days(float(window_days))
    window_started: str | None = None
    if window is not None:
        window_started = (ref - timedelta(days=window)).isoformat(timespec="seconds")

    if not _stream_exists(target):
        # ⛔ A MISSING STREAM IS NOT "NOTHING IS HOT". The reading carries
        # `served_total == 0` AND `stale` so a consumer cannot read an unarmed
        # signal as a confident cold corpus.
        return _empty_reading(target, replayed=True, stale=True, error="no stream")

    # ⛔ A PATH THAT CANNOT NAME A CHECKPOINT MUST NOT RAISE OUT OF HERE. This
    # function is documented "NEVER raises" and it is on the armed consumer's
    # path (a per-pass scorer), not the unarmed writer's. `with_suffix` raises
    # `ValueError` for an empty name — `TORTOISE_USAGE_SIGNAL_PATH=.` reaches it
    # from configuration alone — and `checkpoint_path` was computed outside every
    # guard, so one env typo took down the reader while the writer degraded
    # correctly and said nothing.
    try:
        ckpt = checkpoint_path(target, tenant=tenant)
    except (ValueError, TypeError, OSError) as e:
        return _empty_reading(target, replayed=True, stale=True, error=f"bad path: {e}")

    with _REFRESH_LOCK, _file_lock(ckpt):
        # ⛔ ONE DESCRIPTOR FOR THE WHOLE REFRESH — stat, prefix, and body. Every
        # check below and the fingerprint that CERTIFIES the fold must describe the
        # same bytes; reading any of them by path reopens the window a
        # same-inode replacement slips through.
        try:
            fh = open(target, "rb")  # noqa: SIM115 — closed below
        except OSError as e:
            return _empty_reading(target, replayed=True, stale=True, error=str(e))
        try:
            try:
                st = os.fstat(fh.fileno())
            except OSError as e:  # pragma: no cover — a vanished mount
                return _empty_reading(target, replayed=True, stale=True, error=str(e))
            if st.st_size == 0:
                # ⛔ A PRESENT-BUT-EMPTY STREAM IS ALSO NOT A READING. This is the
                # `copytruncate` shape: the file exists, records nothing, and every
                # candidate is "unserved" — so the caller falls back to creation
                # recency while the reading looks healthy. It is indistinguishable
                # from "just armed" unless it says so, and it says so here.
                return _empty_reading(
                    target, replayed=True, stale=True, error="empty stream"
                )

            state: _State | None = None if force else _load_checkpoint(ckpt)
            replayed = False
            # ⛔ THE PREFIX IS READ FIRST, BEFORE THE BODY, FROM THIS DESCRIPTOR.
            # See `_checkpoint_matches`: a fingerprint taken after the fold
            # certifies bytes the fold never saw.
            try:
                head_bytes = fh.read(HEAD_FINGERPRINT_BYTES)
            except OSError as e:  # pragma: no cover
                return _empty_reading(target, replayed=True, stale=True, error=str(e))

            if state is not None and not _checkpoint_matches(
                state, head_bytes, st, partition=tenant, window=window
            ):
                # The stream is not the one this checkpoint describes. A cache
                # built over a different prefix is worse than no cache: it would
                # fold new bytes onto another file's history.
                state = None
            if state is None:
                state = _State(partition=tenant, window_days=window)
                replayed = True
            start = state.offset

            try:
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

            # A `copytruncate` DURING the read leaves the descriptor on the same
            # inode with a shorter body: the offset we folded from describes a file
            # that no longer exists. Re-fstat and, if it moved, fold from zero on
            # what is actually there. ⛔ `st_size < start + len(whole)` — not
            # `< start`, which let a truncation INTO the freshly-read window
            # through and folded vanished bytes as current (measured: size 348 -> 300
            # against an offset of 348, reported `stale=False`).
            try:
                after = os.fstat(fh.fileno())
            except OSError as e:  # pragma: no cover
                return _empty_reading(target, replayed=replayed, stale=True, error=str(e))
            if (after.st_dev, after.st_ino) != (st.st_dev, st.st_ino) or (
                after.st_size < start + len(whole)
            ):
                state = _State(partition=tenant, window_days=window)
                replayed = True
                start = 0
                try:
                    fh.seek(0)
                    head_bytes = fh.read(HEAD_FINGERPRINT_BYTES)
                    fh.seek(0)
                    chunk = fh.read()
                except OSError as e:  # pragma: no cover
                    return _empty_reading(target, replayed=True, stale=True, error=str(e))
                cut = chunk.rfind(b"\n")
                whole = chunk[: cut + 1] if cut >= 0 else b""
                try:
                    after = os.fstat(fh.fileno())
                except OSError as e:  # pragma: no cover
                    return _empty_reading(target, replayed=True, stale=True, error=str(e))
                st = after

            lines_folded = 0
            serves_folded = 0
            if whole:
                lines_folded, serves_folded = _consume(state, whole, partition=tenant)
                state.offset = start + len(whole)
            pruned_now = False
            if window is not None:
                pruned_now = state.prune(ref - timedelta(days=window))
            # ⛔ `capped` IS RE-DERIVED FROM THE FILE'S TAIL, never latched. The
            # remedy the warning names ("rotate it or raise the bound") demonstrably
            # works — recording resumes — and a latched flag then refused for ever
            # with a message that had become FALSE (measured: entries 1 -> 2 after
            # raising the cap, `orderable` still False, "STOPPED recording" still
            # printed). The writer re-decides its marker from the tail for exactly
            # this reason.
            if whole:
                state.capped = _tail_is_capped(target)
            # An idle refresh — no new bytes and nothing pruned — leaves the
            # checkpoint alone: rewriting the whole entry map on a no-op made a
            # per-pass scorer pay O(entries) for nothing.
            if whole or pruned_now or replayed:
                state.source_dev = st.st_dev
                state.source_ino = st.st_ino
                # A FIXED-length prefix (see HEAD_FINGERPRINT_BYTES): the stream is
                # append-only, so the consumed prefix can never change without the
                # file being replaced. Hashed from the bytes captured BEFORE the
                # body read, so the fingerprint describes exactly what was folded.
                state.head_len = min(state.offset, HEAD_FINGERPRINT_BYTES)
                state.head_sha = (
                    hashlib.sha256(head_bytes[: state.head_len]).hexdigest()
                    if state.head_len and len(head_bytes) >= state.head_len
                    else None
                )
                _save_checkpoint(ckpt, state)
            return _to_reading(
                state,
                target,
                replayed=replayed,
                stale=False,
                window_started=window_started,
                bytes_folded=len(whole),
                lines_folded=lines_folded,
                serves_folded=serves_folded,
            )
        finally:
            with contextlib.suppress(OSError):
                fh.close()


def reading(*, path: Path | None = None, now: datetime | None = None) -> UsageReading:
    """The current signal. A convenience alias for :func:`refresh`.

    There is deliberately no cached in-process reading: the stream is appended by
    a DIFFERENT process on the hosted path, so an in-process cache would age
    silently.

    ⚠️ **Cost, stated honestly.** The FOLD is incremental — ``bytes_folded`` is
    zero on an idle call and equal to the new bytes otherwise, and an idle call
    writes no checkpoint. The READING is not: building it materialises one
    ``UsageEntry`` per coordinate, so each call is O(entries) beside the fold —
    measured ~61 ms on a 20k-entry roll-up. A caller polling this per eviction pass
    should size the window (``USAGE_WINDOW_DAYS``) against that, or build one
    ``UsageReading`` and reuse it for the whole pass.
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
    ap.add_argument(
        "--tenant",
        default=_CLI_ALL,
        help="order ONE tenant partition (required when the stream holds several); "
        "pass '' for the unpartitioned namespace (a self-hosted instance)",
    )
    ap.add_argument("--label", default=None, help="only this label")
    ap.add_argument("--top", type=int, default=20, help="rows to print (default 20)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    target = Path(a.stream) if a.stream else usage_path()
    # ⛔ THREE distinct tenants, not two: absent (ALL_TENANTS), a named id, and the
    # UNPARTITIONED namespace. With `default=None` the last was unreachable from
    # this door, so an operator whose stream went `pooled` had no way to inspect
    # the `None` partition at all.
    if a.tenant == _CLI_ALL:
        got = refresh(target)
    elif a.tenant == "":
        got = refresh(target, tenant=None)
    else:
        got = refresh(target, tenant=a.tenant)
    if a.json:
        print(json.dumps(got.as_dict(), indent=2))
        return 0 if got.orderable and got.served_total else 2
    print(f"stream           : {got.source}")
    print(f"as of            : {got.as_of}")
    if got.error:
        print(f"⚠️ status        : {got.error}")
    print(
        f"serves folded    : {got.served_total:,} over {len(got.entries):,} "
        f"coordinate(s)"
    )
    if got.observed_from:
        print(f"stream observes  : from {got.observed_from}  (a rotation restarts this)")
    if got.window_started:
        print(f"pruning window   : {got.window_started} → {got.as_of}")
    if got.pruned:
        print(
            f"pruned           : {got.pruned:,} entr(ies) last served at or before "
            f"{got.pruned_before} (dropped by {USAGE_WINDOW_DAYS_ENV}) — their exact "
            f"counts are gone, their recency is bounded"
        )
    if got.pruned_undated:
        print(
            f"pruned (undated) : {got.pruned_undated:,} entr(ies) had NO parseable "
            f"`at` at all, so nothing places them inside the window — dropped for "
            f"the same bound (they are the class that used to age by no path)"
        )
    if got.undated_serves:
        print(
            f"⚠️ undated       : {got.undated_serves:,} serve(s) carry no parseable "
            f"`at` — counted, sorted WARMEST (unknown time is not 'oldest'), and they "
            f"cannot move a last-served time"
        )
    if got.unresolved_skips:
        print(
            f"⚠️ unresolved    : {got.unresolved_skips:,} returned row(s) carry no "
            f"value for their label's contract key — NOT counted as serves"
        )
    if got.lines_skipped:
        print(
            f"⚠️ {got.lines_skipped:,} unreadable line(s) skipped — the counter is "
            f"INCOMPLETE"
        )
    if got.refusal_reason():
        # ⛔ NOT A SUCCESS EXIT. A cron/check reading only the exit code saw 0 on a
        # capped or pooled counter in the human path while `--json` returned 2 —
        # and `served_total > 0` short-circuited the guard below, so the two doors
        # disagreed about the same reading.
        print(f"⛔ NOT ORDERABLE : {got.refusal_reason()}")
        return 2
    if not got.served_total:
        print(
            "⚠️ no serves folded yet — an armed-but-quiet signal measures nothing. This "
            "is a MEASUREMENT WINDOW, not a finding about the corpus."
        )
        return 2
    order = got.coldest_first(a.label)
    shown = order[: max(0, a.top)]
    print(f"\ncoldest first ({len(shown)} of {len(order):,}):")
    by_coord: dict[tuple[str, str], UsageEntry] = {}
    for candidate in got.entries.values():
        by_coord.setdefault((candidate.label, candidate.key), candidate)
    for label, key in shown:
        got_entry = by_coord.get((label, key))
        if got_entry is None:  # pragma: no cover — coldest_first only yields entries
            continue
        print(
            f"  {label:<10} {key[:44]:<44} "
            f"last={got_entry.last_served_at} n={got_entry.serve_count}"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
