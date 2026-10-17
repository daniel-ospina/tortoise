"""The read ledger — the instrument `f` (the hot fraction) has never had (#7983).

`docs/architecture/STORAGE-ARCHITECTURE.md` §17.4 closes the two-store
arithmetic and leaves **two magnitudes unmeasured**, `f` and `rho`. This module
is the instrument for the FIRST of them, and it exists because a census of the
tree found nothing that could answer the question:

* `GRAPH.MEMORY USAGE` is a graph-level sampling estimate; FalkorDB exposes **no
  per-node access statistics** and we persist none;
* the *residency declaration* (`tortoise/residency.py`) is a fact about the
  **projection's own write work** — `declare_residency` is called only by
  `cache_projection` — so it can say *"did we materialize this node"* and can
  never say *"was it read"*;
* `read_status.hit_count` is **in flight and per request**: it is discarded when
  the response is built;
* the per-org retrieval meter (`metering_records.retrieval_*`, #4489) counts
  **how many** reads an org made, never **which bytes** they touched, and is a
  no-op unless a tally is armed (the hosted path).

So `f` is a **policy** variable today — `§2.0`'s *"keep the limit in falkor
high"* sets it to 1 — rather than a measured one. The ledger is the smallest
persisted fact that turns it into a division:

    f = bytes read at least once in the window ÷ stock bytes

**THE ZERO-READ MASS IS THE CLAIM.** The split's entire saving is
`(1 − f) × cache`, i.e. *"bytes that are never read again stop renting RAM"*.
The ledger's other half — every registered key with `read_count == 0` — IS
"never read again", in bytes.

⚠️ **WHAT THIS DOES NOT DO, AND MUST NEVER APPEAR TO DO.** It measures from the
moment it is switched on. A freshly-enabled ledger on a long-lived graph reports
`f = 0` because nothing has been *observed* yet, not because nothing was read —
that is a **measurement window**, not a finding, and the report says so
(`window_started` / `observed_reads`). Do not quote the first report of a new
ledger as `f`; quote it with its window, its sample and its date, which is what
§17.4.6 requires of every figure in that table.

**The coordinate is the record's own `(label, key)` contract**, never a bare id:
`Point → id`, `Event → eventId`, `Subject`/`Object → name`, `Source → url`
(`public.node_identity.key_property`, generated from the label; the same set
`cache_projection.KEY_PROPERTY` derives from the placement doc). ⚠️ The map is
pinned against the MIGRATION only — **not** against `KEY_PROPERTY`, so that pair
can drift without a red test (see `KEY_FIELDS` below). The label is part of the coordinate
because `Subject` and `Object` share `name`: a key-only ledger would collapse
`(Object, "acme")` and `(Subject, "acme")` into one row.

**Fail direction.** A measurement must never break the product. A ledger write
that raises is swallowed and logged once — a read never fails because the ledger
did. But a ledger that has stopped recording must not read as complete: the
append is size-bounded (`TORTOISE_READ_LEDGER_MAX_BYTES`, default 64 MiB) and on
the bound it writes ONE `capped` line and stops, which `report` surfaces as
`capped: true` for the rest of that file's life.

**Off by default, and off means NOT IMPORTED.** The funnel reads the flag first
(`TORTOISE_READ_LEDGER`, the #4097 truthy contract) and imports this module only
when it is truthy — so a default deployment pays one env lookup, never loads the
module, and its response is byte-identical. `TORTOISE_READ_LEDGER_PATH` chooses the
file; the default sits next to the embedded store (`resolve_db_path()`'s
directory) so one instance's ledger cannot be confused with another's.

Format: JSONL, one line per event, appended with a single `O_APPEND` write **per
BATCH** (the funnel hands over a whole returned row-set — one write, not one per
event: per-event open/write/close/mkdir/stat measured ~610 µs, i.e. 5 ms on a
10-row read, which is the instrument slowing the path it measures). `O_APPEND`
gives each write an atomic end-of-file, so concurrent appenders add whole batches
and never seek into each other's bytes. Individual LINES are kept under 4 KiB so
a torn write (a crash mid-`write`) damages at most one record — and a damaged
line is COUNTED (`lines_skipped`) and reported, never silently dropped. Created
`0600`: the ledger carries tenant identifiers (`Subject.name`, `Source.url`), so
it must not be world-readable just because the umask says so.
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
from typing import Any, Iterable, Mapping  # noqa: UP035

from .env_truthy import is_truthy

logger = logging.getLogger(__name__)

#: The env flag that makes the read funnel RECORD (the #4097 truthy contract).
LEDGER_FLAG_ENV = "TORTOISE_READ_LEDGER"

#: The ledger file. Default: beside the embedded store.
LEDGER_PATH_ENV = "TORTOISE_READ_LEDGER_PATH"

#: The tenancy partition a row belongs to. Unset = the ledger's own single
#: namespace (`None` in the fold), which is what a self-hosted single-tenant
#: instance has.
LEDGER_TENANT_ENV = "TORTOISE_READ_LEDGER_TENANT"

#: The size bound. On the bound the ledger writes one `capped` line and stops.
LEDGER_MAX_BYTES_ENV = "TORTOISE_READ_LEDGER_MAX_BYTES"
DEFAULT_MAX_BYTES = 64 * 1024 * 1024

EVENT_READ = "read"
EVENT_STOCK = "stock"
EVENT_CAPPED = "capped"
#: A returned row the ledger could not ADDRESS — it carries no value for its
#: label's contract field (an FTS `Subject`/`Object`/`Source` row carries `id`,
#: never `name`/`url`). Written as its
#: own event because a read the ledger cannot key is a HOLE in both the
#: numerator and the denominator — dropping it silently would make the report
#: claim a completeness it never had.
EVENT_UNRESOLVED = "unresolved"

#: Caller-facing entity types that are NOT graph labels: a document IS a
#: `:Source` (D10, ONTOLOGY v3.15 §4.4) and an OPERATOR IS A `:Point`
#: (`is_operator: true`), so recording `("Operator", id)` or `("Document", id)`
#: would be a coordinate the stock (registered under the real label) can never
#: join — the read then sits in `unregistered` and its node stays in the
#: never-read mass, which is the flattering direction.
ENTITY_TYPE_TO_LABEL: dict[str, str] = {"document": "Source", "operator": "Point"}


def label_for_entity_type(entity_type: str) -> str:
    """The graph LABEL a caller-facing `entity_type` reads from.

    `point→Point`, `event→Event`, `subject→Subject`, `object→Object`, plus the
    two aliases above. Kept here, next to `KEY_FIELDS`, so a reader can check the
    two together — and pinned by a test that walks every alias the SDK can
    hand it.
    """
    return ENTITY_TYPE_TO_LABEL.get(entity_type, entity_type.capitalize())


#: The record's key columns, per label — `public.node_identity.key_property`
#: generated from the label (#7955's identity contract). The RECORD is the
#: authority; `tests/test_read_ledger.py` pins this map against the MIGRATION (`public.node_identity`).
#: ⚠️ It is NOT pinned against `cache_projection.KEY_PROPERTY`, which derives from
#: the same placement doc by a different route — that is the pair that can drift,
#: and nothing here would notice.
KEY_FIELDS: dict[str, str] = {
    "Point": "id",
    "Event": "eventId",
    "Subject": "name",
    "Object": "name",
    "Source": "url",
    "Session": "id",
}

#: A line longer than this is dropped. The bound is NOT what prevents interleaving
#: — one `O_APPEND` write per batch is — it keeps a single record's line inside a
#: page, so a write torn by a crash damages one record at most, and that record is
#: COUNTED (`lines_skipped`) rather than lost silently.
MAX_LINE_BYTES = 4096

_warned: set[str] = set()


#: The events the LAST `_append_many` kept (i.e. not dropped for size or
#: encoding). `record_rows` needs them to report how many of ITS callers' reads
#: provably reached the file — the batch's line count alone cannot say that.
_LAST_KEPT = threading.local()


def _tail_ends_with_newline(target: Path) -> bool:
    """Does the file end with a newline? (a crash can leave a torn last line)"""
    try:
        with open(target, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                return True
            fh.seek(-1, os.SEEK_END)
            return fh.read(1) == b"\n"
    except OSError:  # pragma: no cover
        return True


def _tail_is_capped(target: Path) -> bool:
    """Does the file already END with a `capped` marker? (another process's)"""
    try:
        with open(target, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 256))
            tail = fh.read().decode("utf-8", "replace").strip().splitlines()
        if not tail:
            return False
        last = json.loads(tail[-1])
        # A non-object tail (a torn merge) must read as "no marker" — NOT raise:
        # this runs inside an append, where the contract is that nothing escapes
        # into the read path.
        return isinstance(last, dict) and last.get("event") == EVENT_CAPPED
    except (OSError, ValueError):
        return False


def enabled() -> bool:
    """Is the read funnel recording? (default off)

    ⚠️ NOT the funnel's own gate: the funnel reads `TORTOISE_READ_LEDGER` itself
    FIRST, so that this module is never even imported when the flag is off (one
    env lookup, no import). This is the same predicate for callers that already
    imported it — a CLI, a test, a report — and `tests/test_read_ledger.py` pins
    the two against the same truthy contract so they cannot drift.
    """
    return is_truthy(os.environ.get(LEDGER_FLAG_ENV))


def tenant() -> str | None:
    """The tenant partition: the env override, else the CURRENT ORG, else `None`.

    ⛔ THE PER-TENANT COORDINATE MUST BE REACHABLE, or `f` is a pooled number on
    exactly the deployment it matters for. Nothing sets the env var per request
    and a hosted process serves MANY tenants from one process, so defaulting to
    `None` folded every tenant's stock and reads together — a union that biases
    `f` DOWN (flattering the split), with no flag, because there was no second
    tenant value to notice. The MCP layer already knows the org per request, so
    the ledger asks it (fail-soft: no auth context, no request, or an import
    cycle all mean "no partition", never an exception into the read path).
    """
    who = os.environ.get(LEDGER_TENANT_ENV)
    if who:
        return who
    try:
        # local import: the auth module imports a lot, and a cycle here must not
        # take a read down.
        from .mcp_auth import _current_org_id  # a ContextVar, NOT a callable

        return _current_org_id.get() or None
    except Exception:  # pragma: no cover — a measurement never breaks a read
        return None


def ledger_path() -> Path:
    """The ledger file: the env override, else one file per STORE TARGET.

    `resolve_db_path()` falls through to the embedded default for a SERVER URI
    (`docker://`, `redis://`) — it has no local file to point at — so using its
    directory alone would put EVERY graph on the host in one file, with
    `tenant = None`. `fold` would then union several stocks and read sets into one
    ratio, biasing `f` DOWN (the flattering direction) with nothing flagged. The
    store's own identity is therefore part of the filename, and `TORTOISE_GRAPH_NAME`
    joins it when set — a graph chosen only by the SDK's `graph_name=` argument is
    invisible here and needs an explicit path.
    """
    explicit = os.environ.get(LEDGER_PATH_ENV)
    if explicit:
        return Path(explicit).expanduser()
    from .config import resolve_db_path

    # A server URI alone still holds MANY graphs (the SDK's `graph_name` chooses
    # one and `resolve_db_path()` never sees it), so the graph name is part of the
    # identity whenever the environment states it. A caller that selects a graph
    # by constructor argument on a SHARED server must set an explicit
    # `TORTOISE_READ_LEDGER_PATH`: no default can see that choice.
    store = "|".join(
        (
            os.environ.get("TORTOISE_DB_URI") or "",
            os.environ.get("TORTOISE_GRAPH_NAME") or "",
            str(resolve_db_path()),
        )
    )
    tag = hashlib.sha256(store.encode()).hexdigest()[:12]
    return Path(resolve_db_path()).expanduser().parent / f"read_ledger-{tag}.jsonl"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _max_bytes() -> int:
    raw = os.environ.get(LEDGER_MAX_BYTES_ENV)
    try:
        return int(raw) if raw else DEFAULT_MAX_BYTES
    except ValueError:
        return DEFAULT_MAX_BYTES


def _warn_once(key: str, msg: str, *args: object) -> None:
    if key in _warned:
        return
    _warned.add(key)
    logger.warning(msg, *args)


#: The funnel passes ONE path, so this holds one entry in production; the bound
#: exists because the tests pass many, and a module-level set may not grow with
#: the number of distinct paths a process ever sees.
_dirs_made: set[str] = set()


def _append_many(records: list[dict[str, Any]], *, path: Path | None = None) -> int:
    """Append a BATCH of events in ONE write. Returns how many were written.

    One write per batch, not per event: the read funnel calls this once per
    returned row-count, and a per-event `mkdir` + two `stat`s + open/write/close
    was measured at ~610 µs per event — 5 ms on a 10-row read and seconds on a
    large one, i.e. the instrument slowing the path it measures.

    The failure direction is deliberate: a ledger write never raises into the
    read path (a measurement may not break the thing it measures). Every failure
    is warned about ONCE — a silently dead ledger is indistinguishable from a
    store nobody reads, which is the reading this instrument exists to prevent.
    """
    if not records:
        return 0
    lines: list[str] = []
    kept: list[dict[str, Any]] = []
    for record in records:
        try:
            line = json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
        except (TypeError, ValueError):
            # A non-serialisable field (`nbytes` from an odd caller) is a lost
            # EVENT, warned once — never an exception in the read path, which is
            # what the broad handler downstream exists for. It has to be HERE
            # because the handler starts at the file I/O.
            _warn_once(
                "encode",
                "read ledger: dropping an unencodable %s event",
                record.get("event") if isinstance(record, dict) else type(record).__name__,
            )
            continue
        if len(line.encode()) > MAX_LINE_BYTES:
            _warn_once(
                "long-line",
                "read ledger: dropping an over-long %s events line "
                "(over %d B: a record that large can be torn by a crash, so it "
                "is not written)",
                record.get("event"),
                MAX_LINE_BYTES,
            )
            continue
        lines.append(line)
        kept.append(record)
    if not lines:
        return 0
    _LAST_KEPT.kept = kept
    target: Path | None = None
    try:
        # RESOLUTION is inside the guard too: `ledger_path()` expands `~user` and
        # raises `RuntimeError` when it cannot — a config typo on the one
        # deployment that enables the instrument must not take the READ PATH down.
        target = path or ledger_path()
        key = str(target)
        if key not in _dirs_made:
            target.parent.mkdir(parents=True, exist_ok=True)
            if len(_dirs_made) < 64:
                _dirs_made.add(key)
        cap = _max_bytes()
        # `st_size`, not `fh.tell()`: `tell()` reports THIS handle's position at
        # open and cannot see another appender's bytes — the case a multi-process
        # ledger exists for.
        size = target.stat().st_size if target.exists() else 0
        # The cap is measured EVERY append, from the FILE, and there is no
        # process latch: a rotated (truncated or replaced) file must start
        # recording again, or an operator who follows the warning's own
        # instruction ("rotate it") would get a silent, read-free ledger whose
        # first report says f = 0 — the measurement-window trap this module
        # exists to refuse.
        blob = "".join(lines).encode()
        # ⛔ THE BATCH ITSELF COUNTS. Checking only the pre-write size lets one
        # batch cross the bound by its own length and land with NO `capped`
        # marker until a later append happens at all (measured: 37,090 B written
        # under a 200 B cap, `capped: false`) — "bounded in bytes" and "a
        # truncated window is never reported as quiet" both fail for it.
        # The marker below costs a few dozen bytes; the bound is a ceiling on
        # EVENTS, not a hard file-size limit, and the marker is what makes the
        # truncation audible.
        if size >= cap or size + len(blob) > cap:
            # The marker decision reads the FILE's tail, never a process latch: a
            # rotated ledger that caps AGAIN must say so again, or the second stop
            # is silent and `fold()` reports `capped: false` over a file it
            # truncated — the "truncated window read as a quiet one" failure.
            if not _tail_is_capped(target):
                # ⛔ 0600 ON THIS PATH TOO: `open(mode="ab")` creates the file at
                # the umask's mode, and this branch runs on the FIRST append when
                # one batch already exceeds a small cap — the tightest permission
                # the module documents would not be the one the file has.
                _fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(_fd, "ab") as fh:
                    # ⛔ A crash can leave a TORN final line with no newline.
                    # Appending the marker onto it makes the marker unparseable
                    # (so `fold` reports `capped: false` over a truncated file)
                    # AND corrupts the marker itself — the module's headline
                    # guarantee would fail exactly in the case it exists for.
                    try:
                        needs_nl = target.stat().st_size > 0 and not _tail_ends_with_newline(target)
                    except OSError:  # pragma: no cover
                        needs_nl = False
                    if needs_nl:
                        fh.write(b"\n")
                    fh.write(
                        (
                            json.dumps(
                                {"at": _now(), "event": EVENT_CAPPED, "max_bytes": cap},
                                separators=(",", ":"),
                                sort_keys=True,
                            )
                            + "\n"
                        ).encode()
                    )
            _warn_once(
                "capped",
                "read ledger %s: reached its %d B bound — no "
                "further events are recorded (rotate it or raise "
                "%s)",
                target,
                cap,
                LEDGER_MAX_BYTES_ENV,
            )
            return 0
        # 0600 on CREATE: the ledger holds tenant identifiers (`Subject.name`,
        # `Object.name`, `Source.url`), so it must not be world-readable just
        # because the umask says so. `os.open` is the only portable way to pass
        # a creation mode through `O_APPEND` — and it cannot TIGHTEN an existing
        # inode, so a file someone pre-created (a restored backup, an earlier run
        # under a looser umask) is chmod'ed the first time this process writes to
        # it. Trusting the create mode alone would leave tenant identifiers
        # world-readable on exactly the files it was added for.
        #
        # ⛔ THERE IS NO LATCH, on purpose. A per-path latch survives a ROTATION,
        # and a per-(dev, inode) latch fails on inode RECYCLING — `rm` +
        # recreate can land the same inode with a looser mode and the check never
        # runs. The mode comes from the `stat` this function already takes, so
        # the check costs one comparison and a chmod only when the file is
        # actually too open.
        st = None
        with contextlib.suppress(OSError):
            st = target.stat()
        if st is not None and stat.S_ISREG(st.st_mode) and (st.st_mode & 0o077):
            # ⛔ REGULAR FILES ONLY: pointing the env var at a DIRECTORY (or a
            # device) must not have its mode rewritten as a side effect of a
            # measurement — the tightest permission is not automatically the
            # right one for something that is not our file.
            with contextlib.suppress(OSError):  # not ours to fail the read path
                os.chmod(target, 0o600)
        # ⛔ A crash (or the short-write path below) can leave a TORN final line
        # with no newline. Appending a batch onto it CONCATENATES the batch's
        # first line with the fragment, so that record is unparseable AND lost —
        # while the return value still counted it, because the newline it wrote
        # is the batch's own. The `capped` marker path already reasons about
        # this; the normal path must do the same, or "a torn write damages at
        # most one record" is false in exactly the recovery case it exists for
        # and the lost read biases `f` LOW.
        n_events = len(lines)
        prefix = b""
        if size and not _tail_ends_with_newline(target):
            prefix = b"\n"
            blob = prefix + blob
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            # `os.write` may write FEWER bytes than asked (a full disk, a pipe).
            # Looping is the only way the return value can be trusted, and it is
            # what `lines_written` counts below — the caller must never hear "N
            # events recorded" for a batch whose tail never reached the file.
            written = 0
            while written < len(blob):
                n = os.write(fd, blob[written:])
                if n <= 0:  # pragma: no cover — a closed/full descriptor
                    break
                written += n
        finally:
            os.close(fd)
        if written < len(blob):
            # A SHORT write leaves a torn tail: count only the events whose line
            # is provably whole, and say the window is incomplete.
            _warn_once(
                "short",
                "read ledger %s: wrote %d of %d B — the tail is torn, so some "
                "events are LOST and this window is INCOMPLETE",
                target,
                written,
                len(blob),
            )
            # The repaired prefix is not an event: count only lines that start
            # after it.
            return blob[len(prefix) : written].count(b"\n")
        return n_events
    except Exception as e:  # pragma: no cover — a broken filesystem or a bad path
        # ANY failure is swallowed, not just `OSError`: the module's contract is
        # that a measurement may never break the thing it measures, and
        # `RuntimeError` (an unresolvable `~user`), `ValueError` (a NUL byte in a
        # path) and `TypeError` (a non-path) are all reachable from configuration.
        _warn_once(
            "oserror",
            "read ledger: append to %s failed (%s) — the read path "
            "is unaffected, but the measurement is now incomplete",
            target if target is not None else "(unresolved path)",
            e,
        )
        return 0


def _append(record: Mapping[str, Any], *, path: Path | None = None) -> bool:
    """Append ONE event. Returns False when nothing was written."""
    return _append_many([dict(record)], path=path) == 1


def record_stock(
    label: str,
    key: Any,
    *,
    nbytes: int | None = None,
    at: str | None = None,
    path: Path | None = None,
    tenant_id: str | None = None,
) -> bool:
    """Register ONE key as part of the tenant's STOCK (i.e. it exists to read).

    `nbytes` is the bytes the key costs in the store being measured — the
    cache's share of RAM, or the record's row width. `None` is allowed and is
    NOT zero: a stock whose bytes are unknown still has a *key* denominator, so
    `f` falls back to the key fraction and the report says the byte fraction is
    unavailable rather than computing it over a pretend zero.
    """
    if not key:
        return False
    return _append(
        {
            "at": at or _now(),
            "event": EVENT_STOCK,
            "label": label,
            # STR() — `row_key` coerces too, and a numeric key written raw would
            # be rejected by `fold` (it requires a str), silently emptying the
            # DENOMINATOR while every read of that key resolved to "unregistered".
            "key": str(key),
            "bytes": nbytes,
            "tenant": tenant_id if tenant_id is not None else tenant(),
        },
        path=path,
    )


def record_read(
    label: str,
    key: str,
    *,
    at: str | None = None,
    path: Path | None = None,
    tenant_id: str | None = None,
) -> bool:
    """Record ONE key as READ. One line per key per read — reads are the point."""
    if not key:
        return False
    return _append(
        {
            "at": at or _now(),
            "event": EVENT_READ,
            "label": label,
            "key": str(key),
            "tenant": tenant_id if tenant_id is not None else tenant(),
        },
        path=path,
    )


def row_key(label: str, row: Mapping[str, Any]) -> str | None:
    """The `(label, key)` coordinate of ONE returned row, or `None`.

    `label` is already half the coordinate, so a name-shaped fallback cannot
    collide across labels: `Subject.name` and `Object.name` stay distinct rows.
    `None` means the row carries no value for the label's CONTRACT field (an FTS
    `Object` row carries `id`, not `name`; it is not keyless, it is unaddressable
    in this coordinate) — counted as an UNRESOLVED read
    by the report rather than dropped silently, because a read the ledger cannot
    address is a hole in the denominator as well as in the numerator.
    """
    field_name = KEY_FIELDS.get(label)
    if field_name is not None:
        # For a RECORD label the contract field is REQUIRED. Falling back to any
        # other id-shaped field would record a coordinate the STOCK can never
        # join on: `Subject`/`Object` carry BOTH `id` and `name`, and the
        # contract key is `name`, so an `id`-keyed read row would silently sit
        # outside the denominator and be reported as "never read" — the flattering
        # direction. `None` here is counted, not dropped.
        value = row.get(field_name)
        return str(value) if value else None
    for alt in ("id", "eventId", "name", "url"):
        if row.get(alt):
            return str(row[alt])
    return None


def record_rows(
    label: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    path: Path | None = None,
    tenant_id: str | None = None,
    at: str | None = None,
) -> int:
    """Record a whole returned batch in ONE append. Returns keys recorded.

    Called by the read funnel only when the flag is on — the funnel reads
    `TORTOISE_READ_LEDGER` itself first, so a default deployment neither imports
    this module nor calls in (`enabled()` is the same predicate, for callers that
    already hold it).
    """
    stamp = at or _now()
    who = tenant_id if tenant_id is not None else tenant()
    events: list[dict[str, Any]] = []
    recorded = 0
    for row in rows:
        key = row_key(label, row) if isinstance(row, Mapping) else None
        if key is None:
            events.append({"at": stamp, "event": EVENT_UNRESOLVED, "label": label, "tenant": who})
            continue
        events.append({"at": stamp, "event": EVENT_READ, "label": label, "key": key, "tenant": who})
        recorded += 1
    # Report what reached the file, not what was intended: `_append_many` returns
    # the whole LINES it wrote, and a torn batch may land fewer than it held. A
    # torn batch cannot be attributed per key, so the honest answer is "none
    # provably recorded" — `_append_many` has already warned that the window is
    # INCOMPLETE, which is the fact a caller must not miss.
    written = _append_many(events, path=path)
    if not written:
        return 0
    # Exact, not "all or nothing": a batch may lose ONE event to the size bound
    # and still land the rest, and a torn tail lands a PREFIX of the kept lines.
    # Both are readable from the kept list, so the count is what provably landed
    # rather than what was intended (which is what a caller logs).
    landed = (getattr(_LAST_KEPT, "kept", None) or [])[:written]
    return sum(1 for e in landed if e.get("event") == EVENT_READ and e.get("key"))


# ════════════════════════════════════════════════════════════════════════════
# The fold — the ledger is EVENTS, `f` is a reading of them
# ════════════════════════════════════════════════════════════════════════════


def _stamp_cmp(a: str, b: str) -> int:
    """Chronological comparison of two stamps (string fallback if unparseable)."""
    da, db = _stamp(a), _stamp(b)
    if da is not None and db is not None:
        return (da > db) - (da < db)
    return (a > b) - (a < b)


def _stamp(value: Any) -> datetime | None:
    """Parse an event's `at` into an aware datetime, or `None`.

    ⛔ STAMPS ARE COMPARED AS TIMES, NOT AS STRINGS. `at=` is a public parameter,
    and lexical order is chronological only when every stamp carries the SAME UTC
    offset: `2026-10-11T01:00:00+09:00` is 16:00Z, so a string compare puts it
    AFTER `2026-10-11T18:00:00+00:00` and a window can count a read from outside
    itself, or pick an earlier read as the latest. A naive stamp is read as UTC
    (the module writes UTC, `Z` or `+00:00`); an unparseable one falls back to
    `None`, which every caller treats as "no time" rather than as a comparison.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass
class StockRow:
    """One `(label, key)` of the stock, with what the ledger observed about it."""

    label: str
    key: str
    nbytes: int | None = None
    first_written_at: str | None = None
    read_count: int = 0
    last_read_at: str | None = None

    @property
    def read(self) -> bool:
        return self.read_count > 0


@dataclass
class LedgerFold:
    """The ledger, folded to one row per stock key."""

    rows: dict[tuple[str | None, str, str], StockRow] = field(default_factory=dict)
    #: Reads whose `(label, key)` was never registered as stock — the ledger saw
    #: a read of a key whose bytes are outside the denominator.
    unregistered_reads: int = 0
    unresolved_reads: int = 0
    #: Reads whose `at` could not be placed in the window (counted, never dropped
    #: silently). A missing stamp is a caller error, not a quiet exclusion.
    reads_undated: int = 0
    window_started: str | None = None
    window_ended: str | None = None
    capped: bool = False
    lines_read: int = 0
    lines_skipped: int = 0
    #: The earliest `at` the FILE contains. A rotated ledger starts a new window,
    #: and this is the only thing in the report that says so.
    first_event_at: str | None = None

    @property
    def stock_keys(self) -> int:
        return len(self.rows)


def fold(
    path: Path | str | None = None,
    *,
    window_days: float | None = None,
    now: datetime | None = None,
    tenant: str | None = None,
) -> LedgerFold:
    """Read the ledger and fold it.

    `window_days` bounds the READS considered (`at >= now - window`); the STOCK is
    always everything the ledger has ever been told exists, because a key written
    before the window is still stock the cache is renting RAM for. A window that
    silently shrank the denominator would report a hot fraction that rises as the
    window shortens — the denominator error §17.4.2 records three times.
    """
    target = Path(path) if path is not None else ledger_path()
    out = LedgerFold()
    #: `f` is a PER-TENANT quantity — `(1 − f) × cache` is one tenant's saving — so
    #: a fold that unions tenants answers a question nobody asked. `tenant=None`
    #: keeps the single-namespace case (a self-hosted instance) and folds
    #: everything a single-tenant deployment ever writes; a multi-tenant ledger
    #: must pass one.
    tenant_filter = tenant
    if not target.exists():
        return out
    pending: dict[tuple[str | None, str, str], list[str | None]] = {}
    cutoff = None
    cutoff_dt: datetime | None = None
    if window_days is not None:
        ref = now or datetime.now(UTC)
        if ref.tzinfo is None:
            # `_stamp` reads a naive event stamp as UTC; `now` follows the same
            # rule, or the comparison raises TypeError out of a read-only report
            # for the first caller who passes the natural naive datetime.
            ref = ref.replace(tzinfo=UTC)
        cutoff_dt = ref - timedelta(days=window_days)
        cutoff = cutoff_dt.isoformat(timespec="seconds")
        out.window_started = cutoff
        out.window_ended = ref.isoformat(timespec="seconds")
    with open(target, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            out.lines_read += 1
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                out.lines_skipped += 1
                continue
            if not isinstance(ev, dict):
                # JSON but not an EVENT (`null`, a number, a list) — a merged or
                # hand-edited file can produce one, and `.get` on it would raise
                # out of a READ-ONLY report.
                out.lines_skipped += 1
                continue
            at_any = ev.get("at")
            if isinstance(at_any, str) and (
                out.first_event_at is None or _stamp_cmp(at_any, out.first_event_at) < 0
            ):
                out.first_event_at = at_any
            kind = ev.get("event")
            if kind == EVENT_CAPPED:
                # FILE-level, like `first_event_at`, and handled BEFORE the tenant
                # filter: the marker carries no tenant (it is the FILE that
                # stopped), so filtering first would report a capped ledger as
                # quiet to anyone who passes `--tenant` — the truncation the
                # module exists to refuse.
                out.capped = True
                continue
            if tenant_filter is not None and ev.get("tenant") != tenant_filter:
                continue
            if kind == EVENT_UNRESOLVED:
                at = ev.get("at")
                _at_dt = _stamp(at)
                if cutoff_dt is not None and _at_dt is None:
                    # Same rule as a read: an event that cannot be placed in the
                    # window is COUNTED, not dropped silently — this is the
                    # coverage-hole counter, and a hole is not allowed to be
                    # invisible.
                    out.unresolved_reads += 1
                    continue
                if cutoff_dt is not None and _at_dt < cutoff_dt:
                    continue
                out.unresolved_reads += 1
                continue
            label, key = ev.get("label"), ev.get("key")
            if not isinstance(label, str) or not isinstance(key, str):
                out.lines_skipped += 1
                continue
            ident = (ev.get("tenant"), label, key)
            if kind == EVENT_STOCK:
                row = out.rows.get(ident)
                if row is None:
                    row = out.rows[ident] = StockRow(label=label, key=key)
                if ev.get("bytes") is not None:
                    try:
                        row.nbytes = int(ev["bytes"])
                    except (TypeError, ValueError, OverflowError):
                        # A hand-edited or half-rewritten `bytes` must not take
                        # the report down, and the EVENT is not lost: the key
                        # exists, its width is simply unknown — which is exactly
                        # what `nbytes is None` means everywhere else (the byte
                        # fraction excludes it and reports its coverage). OverflowError
                        # too: `record_stock(nbytes=float("inf"))` is writable
                        # through this module's OWN public API and emits
                        # `{"bytes": Infinity}`, and `int(inf)` raises — one such
                        # line would make every later fold() die, permanently.
                        row.nbytes = None
                at = ev.get("at")
                # `isinstance` guard: a non-string `at` (an epoch integer, a list)
                # would reach `_stamp_cmp`'s mixed-type fallback and raise a
                # TypeError out of a READ-ONLY report — the pending-READ path
                # already guards this, and the same "damaged line is counted, not
                # fatal" rule applies here.
                if (
                    isinstance(at, str)
                    and at
                    and (row.first_written_at is None or _stamp_cmp(at, row.first_written_at) < 0)
                ):
                    row.first_written_at = at
            elif kind == EVENT_READ:
                at = ev.get("at")
                _at_dt = _stamp(at)
                if cutoff_dt is not None and _at_dt is None:
                    # A read with no parseable stamp cannot be placed in the
                    # window. Dropping it SILENTLY would leave the numerator
                    # short with the report reading as complete — every other
                    # loss in this module is counted, so this one is too.
                    out.reads_undated += 1
                    continue
                if cutoff_dt is not None and _at_dt < cutoff_dt:
                    continue
                row = out.rows.get(ident)
                if row is None:
                    # NOT yet a hole: the stock line may appear LATER in the file
                    # (reads accumulate from the moment the flag is set, while a
                    # stock census is a separate, later step). Applying the join
                    # only in file order would report an observed read as "never
                    # read" — the flattering direction — so pending reads are
                    # held and joined after the whole file is folded.
                    pending.setdefault(ident, []).append(at)
                    continue
                row.read_count += 1
                if at and (row.last_read_at is None or _stamp_cmp(at, row.last_read_at) > 0):
                    row.last_read_at = at
            else:
                out.lines_skipped += 1
    # The second half of the join: a read that preceded its stock registration is
    # the SAME observation, so it is applied now. Reads for keys the ledger was
    # never told about stay unregistered — a real hole, reported as one.
    for ident, stamps in pending.items():
        row = out.rows.get(ident)
        if row is None:
            out.unregistered_reads += len(stamps)
            continue
        row.read_count += len(stamps)
        for stamp in (s for s in stamps if isinstance(s, str)):
            if row.last_read_at is None or _stamp_cmp(stamp, row.last_read_at) > 0:
                row.last_read_at = stamp
    return out


@dataclass
class HotFraction:
    """`f`, with its base, its denominator and its coverage. Never a bare ratio.

    `f_keys`        read keys ÷ stock keys — always available.
    `f_bytes`       read BYTES ÷ stock BYTES — only over keys whose `bytes` the
                    ledger knows, and `None` when no key carries bytes, because
                    a byte fraction over a pretend-zero denominator is not a
                    measurement (`bytes_known_fraction` states the coverage).
    """

    stock_keys: int
    read_keys: int
    stock_bytes: int
    read_bytes: int
    bytes_known_stock_keys: int
    bytes_known_read_keys: int
    unregistered_reads: int
    unresolved_reads: int
    window_started: str | None
    window_ended: str | None
    #: Earliest event IN THE FILE. After a rotation this is recent while the
    #: history is gone — the report says so instead of implying a long window.
    first_event_at: str | None
    capped: bool

    @property
    def f_keys(self) -> float | None:
        if not self.stock_keys:
            return None
        return self.read_keys / self.stock_keys

    @property
    def f_bytes(self) -> float | None:
        if not self.stock_bytes:
            return None
        return self.read_bytes / self.stock_bytes

    @property
    def bytes_known_fraction(self) -> float | None:
        if not self.stock_keys:
            return None
        return self.bytes_known_stock_keys / self.stock_keys

    @property
    def f_bytes_qualified(self) -> bool:
        """Is `f_bytes` a reading of the READS, or only of the sized subset?

        False when any READ key carries no `bytes`. Those keys leave the
        numerator AND the denominator, so the subset ratio is NOT a bound in
        either direction — it is higher than the truth if the unsized keys are
        mostly unread, lower if they are mostly read. The flag exists to stop the
        number being QUOTED as `f`, not to label it conservative.
        """
        return self.bytes_known_read_keys == self.read_keys

    @property
    def zero_read_keys(self) -> int:
        return self.stock_keys - self.read_keys

    @property
    def zero_read_bytes(self) -> int:
        return self.stock_bytes - self.read_bytes

    def as_dict(self) -> dict[str, Any]:
        return {
            "f_keys": self.f_keys,
            "f_bytes": self.f_bytes,
            "stock_keys": self.stock_keys,
            "read_keys": self.read_keys,
            "zero_read_keys": self.zero_read_keys,
            "stock_bytes": self.stock_bytes,
            "read_bytes": self.read_bytes,
            "zero_read_bytes": self.zero_read_bytes,
            "bytes_known_fraction": self.bytes_known_fraction,
            "f_bytes_qualified": self.f_bytes_qualified,
            "bytes_known_stock_keys": self.bytes_known_stock_keys,
            "bytes_known_read_keys": self.bytes_known_read_keys,
            "unregistered_reads": self.unregistered_reads,
            "unresolved_reads": self.unresolved_reads,
            "window_started": self.window_started,
            "window_ended": self.window_ended,
            "first_event_at": self.first_event_at,
            "capped": self.capped,
        }


def hot_fraction(folded: LedgerFold) -> HotFraction:
    """Reduce a fold to `f` — the ONE place the division happens."""
    stock_bytes = read_bytes = 0
    bytes_known = bytes_known_read = 0
    read_keys = 0
    for row in folded.rows.values():
        if row.read:
            read_keys += 1
        if row.nbytes is not None:
            bytes_known += 1
            stock_bytes += row.nbytes
            if row.read:
                bytes_known_read += 1
                read_bytes += row.nbytes
    return HotFraction(
        stock_keys=folded.stock_keys,
        read_keys=read_keys,
        stock_bytes=stock_bytes,
        read_bytes=read_bytes,
        bytes_known_stock_keys=bytes_known,
        bytes_known_read_keys=bytes_known_read,
        unregistered_reads=folded.unregistered_reads,
        unresolved_reads=folded.unresolved_reads,
        window_started=folded.window_started,
        window_ended=folded.window_ended,
        first_event_at=folded.first_event_at,
        capped=folded.capped,
    )


def _tenants_in_file(path: Path | str) -> set[str | None]:
    """Every tenant named by ANY event (stock, read or unresolved)."""
    seen: set[str | None] = set()
    try:
        with open(Path(path), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    ev = json.loads(line.strip() or "{}")
                except ValueError:
                    continue
                if isinstance(ev, dict) and "tenant" in ev:
                    seen.add(ev.get("tenant"))
    except OSError:  # pragma: no cover
        return seen
    return seen


def _events_for_tenant(path: Path | str, tenant: str) -> int:
    """How many events in the FILE name this tenant (reads count, stock or not).

    `fold(tenant=...)` builds ROWS, and a tenant whose reads are all
    `unresolved` — the FTS Subject/Object/Source legs, or a deployment with no
    stock census — has NO rows while its reads are real. Asking the file is the
    only way "a typo" and "reads recorded before the stock" stay distinguishable.
    """
    n = 0
    try:
        with open(Path(path), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    ev = json.loads(line.strip() or "{}")
                except ValueError:
                    continue
                if isinstance(ev, dict) and ev.get("tenant") == tenant:
                    n += 1
    except OSError:  # pragma: no cover
        return 0
    return n


def _main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m tortoise.read_ledger",
        description="Read the read ledger and report f (the hot fraction) with "
        "its base, its denominator and its window.",
    )
    ap.add_argument("--ledger", default=None, help="ledger file (default: the env path)")
    ap.add_argument(
        "--window-days",
        type=float,
        default=None,
        help="count only reads inside this trailing window",
    )
    ap.add_argument(
        "--tenant",
        default=None,
        help="fold ONE tenant (required when the ledger holds several: f is per tenant)",
    )
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    target = Path(a.ledger) if a.ledger else ledger_path()
    if not target.exists():
        # A missing file is not a zero reading: "stock 0 keys" from a path typo
        # reads exactly like "nothing is registered", which is the confusion this
        # instrument exists to remove.
        print(f"no ledger at {target} — nothing has been recorded there")
        return 2
    folded = fold(target, window_days=a.window_days, tenant=a.tenant)
    if (
        a.tenant is not None
        and folded.lines_read
        and not folded.rows
        and (_events_for_tenant(target, a.tenant) == 0)
    ):
        # A typo'd `--tenant` is not an empty tenant: the file holds events for
        # SOMEONE, and reporting "0 keys / f n/a" over them is the same
        # typo-reads-as-a-zero-denominator failure the missing-file branch above
        # refuses. The tenants that ARE in the file are named by count only — the
        # identifiers stay out of a report that may be pasted into an issue.
        seen = fold(target, window_days=a.window_days)
        others = {t for t in _tenants_in_file(target) if t is not None}
        others.discard(a.tenant)
        print(
            f"no events for tenant {a.tenant!r} — the ledger holds "
            f"{len(seen.rows):,} key(s) under {len(others)} other tenant(s)"
        )
        return 2
    # A UNION IS ALSO A WRONG NUMBER, and it is the easy mistake: `f` is per
    # tenant, so forgetting `--tenant` on a file that holds three folds three
    # stocks and three read sets into one ratio — which biases `f` DOWN and so
    # FLATTERS the split. The typo'd tenant is refused above; this is the same
    # number class, so it is flagged the same way (named by COUNT — the
    # identifiers stay out of a report that may be pasted into an issue).
    union_tenants = 0
    pooled_single_namespace = False
    mixed_namespace = False
    all_tenants = _tenants_in_file(target)
    tenant_count_in_file = len({t for t in all_tenants if t is not None}) + (
        1 if None in all_tenants else 0
    )
    if a.tenant is None:
        union_tenants = len({t for t in all_tenants if t is not None})
        # Every event under `tenant=None` is ONE namespace to the ledger — and a
        # hosted process serving many tenants writes exactly that unless the
        # tenant coordinate is known. `tenant()` now asks the auth context, so
        # this shape means "no context available", i.e. a reading that MAY be a
        # union and cannot prove it is not.
        # A file with `tenant = None` events AND one named tenant is a union
        # too — and it is the shape `tenant()` produces in production (context-
        # free writes land on None, request-scoped ones on an org), so counting
        # only the NAMED tenants reported `union_tenants == 1` and flagged
        # nothing while the report still pooled two populations.
        mixed_namespace = None in all_tenants and len(all_tenants) > 1
        pooled_single_namespace = bool(all_tenants) and all_tenants <= {None}
    f = hot_fraction(folded)
    if a.json:
        # `lines_skipped` / `lines_read` are fold-level facts, not `f`'s: a torn
        # line is a LOST event, so they belong beside the reading they qualify.
        print(
            json.dumps(
                {
                    **f.as_dict(),
                    "lines_read": folded.lines_read,
                    "lines_skipped": folded.lines_skipped,
                    "reads_undated": folded.reads_undated,
                    "tenants_in_file": tenant_count_in_file,
                    "pooled_single_namespace": pooled_single_namespace,
                    "mixed_namespace": mixed_namespace,
                },
                indent=2,
            )
        )
        return 0
    print(f"ledger           : {a.ledger or ledger_path()}")
    if a.tenant is not None:
        print(f"tenant           : {a.tenant}")
    if pooled_single_namespace:
        print(
            "⚠️ NOT PARTITIONED: every event carries tenant=None — if this "
            "process serves more than one tenant, this f is a POOLED union "
            "(biased DOWN). Give the ledger a tenant (TORTOISE_READ_LEDGER_TENANT, "
            "or a request context) to keep them apart."
        )
    if mixed_namespace:
        print(
            f"⚠️ MIXED: {union_tenants} named tenant(s) AND unpartitioned "
            f"(tenant=None) events — this file pools them, so f is a UNION "
            f"(biased DOWN). Pass --tenant, and set a tenant on the writers too."
        )
    if union_tenants > 1:
        print(
            f"⚠️ UNION         : this file holds {union_tenants} tenants and NO "
            f"--tenant was given — f is per tenant, so this reading pools them "
            f"and biases f DOWN (flattering the split). Pass --tenant."
        )
    if f.first_event_at is not None:
        print(f"file spans from  : {f.first_event_at}  (a rotation restarts this)")
    print(
        f"window           : {f.window_started or '(all recorded events)'}"
        + (f" → {f.window_ended}" if f.window_ended else "")
    )
    print(
        f"stock            : {f.stock_keys:,} keys · "
        f"{f.stock_bytes:,} B over {f.bytes_known_stock_keys:,} keys with bytes"
    )
    print(f"read at least once: {f.read_keys:,} keys · {f.read_bytes:,} B")
    print(f"NEVER read       : {f.zero_read_keys:,} keys · {f.zero_read_bytes:,} B")
    if folded.reads_undated:
        print(
            f"⚠️ undated reads : {folded.reads_undated:,} read event(s) carry no "
            f"parseable `at` and are OUTSIDE the window — the numerator is short "
            f"by that much"
        )
    fk = f.f_keys
    print(
        f"f (keys)         : {fk:.4f}" if fk is not None else "f (keys)         : n/a — empty stock"
    )
    fb = f.f_bytes
    if fb is not None:
        cov = f.bytes_known_fraction or 0.0
        qualifier = (
            "; ⚠️ NOT all READ keys carry bytes — this is the ratio over the sized"
            " subset only, not a bound in either direction"
            if not f.f_bytes_qualified
            else ""
        )
        print(
            f"f (bytes)        : {fb:.4f}  (over {cov:.1%} of stock keys carrying bytes{qualifier})"
        )
    else:
        print("f (bytes)        : n/a — no key in the ledger carries bytes")
    if f.unregistered_reads or f.unresolved_reads:
        print(
            f"⚠️ reads outside the denominator: {f.unregistered_reads} unregistered, "
            f"{f.unresolved_reads} with no value for their label's contract field "
            f"(an FTS Subject/Object/Source row carries `id`, not `name`/`url`) — "
            f"f undercounts the numerator by these"
        )
    if folded.lines_skipped:
        print(
            f"⚠️ {folded.lines_skipped} unreadable line(s) skipped of {folded.lines_read:,} read "
            "— a torn line is a lost event, so the window is INCOMPLETE"
        )
    if f.capped:
        print(
            "⛔ the ledger reached its size bound and stopped recording — this "
            "window is truncated, not quiet"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
