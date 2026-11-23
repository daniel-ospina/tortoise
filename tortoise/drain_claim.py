"""#14350 (D1 of #14342) — ONE drainer per spool: the drain's cross-process claim.

The defect this closes: **every `SessionStart` backgrounds a drain**, `SessionEnd`
takes a final flush, `agent_end` takes another, `store_sync`'s sweep drains the
spool as one of its legs, and `tortoise session drain` can be run by hand — so on
a busy box N drains run at once, and each one POSTs into the SAME server-side
capture ceiling (``hosted_api._CAPTURE_MAX_IN_FLIGHT`` = 8). The measured shape
(#14342, handoff §7.3): a 908-entry spool, retry ``attempts`` clustering 10–17,
and a live refusal ``{"code":"capture_capacity_saturated","in_flight":8,
"limit":8,"retry_after":30}``. Every entry retries against one ceiling, so the
backlog feeds itself.

Raising the ceiling is explicitly NOT the fix (it moves the contention). The fix
is that **only one process drains a given spool at a time**, which is what this
module provides: an exclusive claim on ``<spool>/drain.claim``.

## Why a lease, and not a bare lockfile

Both alternatives were rejected on their known failure modes — the same two the
issue names ("a stale claim vs an operator running two"):

* **flock(2)** (``fcntl.flock``) is the strongest primitive on POSIX and the
  kernel releases it when the holder dies — but the spool is shared by TWO legs
  and one of them is **TypeScript**, which has no ``flock`` in its stdlib. A lock
  the Pi leg cannot take is a lock the Pi leg ignores, and a shared lock one leg
  ignores is not a lock. (It would also need an extra open fd per pass, and
  ``flock`` is advisory across NFS in ways this spool does not need to reason
  about.)
* **a configuration promise** ("run exactly one drainer service") has no
  mechanical enforcement at all, and the hook that fires the drain is installed
  per harness by ``hook_install`` — there is no single place to configure.

So the claim is a **lease**: an atomically-created record carrying the holder's
identity, refreshed by the holder, and **stale the moment its holder is provably
gone OR its lease expires** — which is precisely the issue's requirement that "a
stale claim is re-verifiable, not a permanent block".

## The three staleness rules (all fail-closed toward NOT stealing)

1. **Unattributable** — unreadable, not an object, no usable ``expires_at``: the
   claim cannot be honoured (nothing says when it ends) and cannot be trusted
   either. Treated as stale. A claim file is written complete before it is
   visible (see below), so this only ever describes corruption or a foreign file.
2. **Expired** — ``now >= expires_at``. This is the ONLY rule that applies to a
   claim written on another host (a copied spool): this box cannot check that
   host's pids.
3. **Dead holder** — same ``host`` AND same ``boot_id`` (a rebooted box can
   reuse a pid, and a reused pid is not the holder): ``os.kill(pid, 0)`` says the
   process is gone. ``EPERM`` means "exists, not ours" — alive. Any other
   ``OSError`` is *unprovable*, which is read as ALIVE: the cost of honouring a
   dead claim is one skipped pass (the next one takes it), while the cost of
   stealing a live one is two drainers — the exact defect this module exists to
   remove.

## Atomicity (why ``os.link`` and not ``rename``)

``rename`` REPLACES an existing target, so two processes both "create" the claim
and both think they own it. ``link`` fails with ``EEXIST`` when the target is
there and is atomic when it is not, and — unlike plain ``O_CREAT|O_EXCL`` — the
target becomes visible **with its full content**, because the content is written
to a temp file first. That closes the window where a reader sees a zero-length
claim and reads it as corrupt (rule 1) while the holder is mid-write.

## Renewal, release, and ownership

The holder refreshes ``expires_at`` before each attempt (a cheap write, amortised
by ``renew_if_due``) so a live-but-slow pass is never mistaken for a dead one.
``release`` and the staleness takeover both compare-and-delete: a claim whose
content no longer matches what was judged stale (or no longer carries OUR
``token``) is left alone, so a slow holder can never delete its SUCCESSOR's
claim — only the release of a claim it still owns is a release.

The TTL is derived, not chosen for comfort (see :data:`DEFAULT_TTL_S`).

One spool, two legs, ONE protocol: ``tortoise/pi-hooks/tortoise-capture.ts``
implements the same file with the same fields and the same three rules. Anything
that changes here must change there — a divergence between the legs on this file
would be invisible (each leg would take its own claim) and would restore the
defect silently.
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import secrets
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

#: The claim file, in the spool ROOT — never ``entries/``, so it is invisible to
#: the spool's own accounting (`_entry_bytes`, `prune_spool`, `_scan_spool`),
#: exactly like ``status.json``. A file inside ``entries/`` would be counted as
#: spool occupancy forever and could push a box over a ceiling.
CLAIM_NAME = "drain.claim"

#: Bumped only if a field's MEANING changes. A record whose version is unknown
#: is unattributable (staleness rule 1), never assumed compatible.
CLAIM_VERSION = 1

#: How long a claim stays valid without a refresh — DERIVED from the transport,
#: not chosen for comfort. The longest single step a live drain can spend between
#: refreshes is ONE POST, whose transport bound is 30 s
#: (``__main__._session_post`` → ``urlopen(req, timeout=30)``), plus the local
#: read/digest/CAS work on the entry being filed (sub-millisecond typical, ~150 ms
#: at the 16 MB ``SPOOL_MAX_ENTRY_BYTES`` bound). 4x that bound (120 s) leaves a
#: full order of magnitude of slack for a pre-empted process, while still making a
#: SIGKILLed drainer's claim reclaimable inside two minutes — the failure mode a
#: lease exists to bound.
DEFAULT_TTL_S = 120.0

#: Refresh when this fraction of the TTL has elapsed. A third: two refresh
#: opportunities remain before expiry if one write is lost, so a single failed
#: write cannot cost the claim.
RENEW_FRACTION = 3.0

#: A second acquisition attempt is allowed after a successful takedown, because
#: the takedown-and-create pair is not atomic (it is a compare-and-delete
#: followed by a link). Two ticks, then give up: the caller's pass is
#: best-effort, and the next hook fire retries.
ACQUIRE_ATTEMPTS = 2


def claim_path(root: Path) -> Path:
    """``<spool>/drain.claim`` — the ONE definition of the path."""
    return Path(root) / CLAIM_NAME


def _boot_id() -> str | None:
    """This boot's identifier, or ``None`` where the platform has none.

    ``/proc/sys/kernel/random/boot_id`` is Linux-only. Absent (macOS, a
    container without /proc), the pid-liveness rule is skipped and only the
    lease governs — which is the SAFE degradation: rule 2 still bounds a dead
    holder's claim, and rule 3 can never fire on a reused pid it cannot
    disambiguate.
    """
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="utf-8").strip() or None
    except (OSError, UnicodeDecodeError):
        return None


def _host() -> str:
    try:
        return socket.gethostname() or "unknown"
    except OSError:  # pragma: no cover — a host with no hostname
        return "unknown"


def pid_alive(pid: object) -> bool:
    """Is ``pid`` a live process on THIS box? Unprovable reads as alive.

    ``PermissionError`` (EPERM) means the process EXISTS and belongs to someone
    else. Every other ``OSError`` leaves the question unanswered, and the answer
    that cannot create a second drainer is "alive" (module docstring, rule 3).
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:  # pragma: no cover — unprovable
        return True
    return True


def _finite(value: object) -> float | None:
    """``value`` as a finite float, or ``None`` (total over junk/NaN/bool)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def claim_is_stale(
    record: object,
    *,
    now_s: float,
    host: str | None = None,
    boot_id: str | None = None,
    alive: Callable[[object], bool] = pid_alive,
) -> bool:
    """The three staleness rules, as one TOTAL predicate.

    ``record`` is the PARSED claim (``None`` when the file could not be read as
    a JSON object). ``host``/``boot_id`` are THIS box's; passing ``None``
    disables the pid rule (the caller cannot check it). Nothing here mutates the
    spool, so it is the seam every staleness test pins, and the reader the
    takeover path uses to decide whether it may delete.
    """
    if not isinstance(record, dict):
        return True  # rule 1: unattributable
    expires = _finite(record.get("expires_at"))
    if expires is None:
        return True  # rule 1: nothing says when it ends
    if now_s >= expires:
        return True  # rule 2: expired (the only rule for a foreign host)
    if host is None:
        return False
    if record.get("host") != host:
        return False  # rule 2 only — this box cannot check that host's pids
    recorded_boot = record.get("boot_id")
    if recorded_boot and boot_id and recorded_boot != boot_id:
        return True  # rule 3, rebooted: the recorded pid is not the holder
    pid = record.get("pid")
    if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
        if not alive(pid):
            return True  # rule 3: the holder is gone
    return False


def read_claim(root: Path) -> dict | None:
    """The parsed claim, or ``None``. The SURFACE (`session drain`, health, tests)."""
    try:
        text = claim_path(root).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        record = json.loads(text)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def _read_raw(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _remove_if_unchanged(path: Path, expected: bytes) -> bool:
    """Compare-and-delete: unlink ONLY if the file still holds ``expected``.

    The compare is what makes a takedown safe. Two processes may both judge the
    same claim stale; the first one's delete makes the second's ``ENOENT``, which
    is not a failure. And a claim that changed between the read and the unlink
    belongs to a NEW holder, who must keep it.
    """
    if _read_raw(path) != expected:
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        # A peer judged the same claim stale and got there first. Not a failure:
        # the claim is gone either way, and the caller's next attempt decides the
        # winner by `link`'s EEXIST.
        return True
    except OSError:
        return False
    return True


def _write_claim_atomic(path: Path, record: dict) -> None:
    """Write ``record`` in place, atomically (the RENEW path).

    ``renew`` addresses the claim by PATH, so replacing the inode is fine; the
    ``os.replace`` keeps a reader from ever seeing a half-written record (rule 1
    would read that as corrupt and steal a live claim).
    """
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{record['token']}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@dataclass
class DrainClaim:
    """A claim this process HOLDS. Use :func:`acquire`, never construct one.

    ``renew_if_due`` is the hot-path call (once per entry): it re-reads the
    clock, refreshes only when a third of the TTL has gone, and returns ``False``
    when the claim is no longer ours — at which point the holder MUST stop, or it
    becomes the second drainer it exists to prevent.
    """

    path: Path
    token: str
    leg: str
    ttl_s: float = DEFAULT_TTL_S
    clock: Callable[[], float] = time.time
    _issued_at: float = 0.0
    _renewed_at: float = 0.0

    def record(self, now_s: float) -> dict:
        return {
            "version": CLAIM_VERSION,
            "token": self.token,
            "leg": self.leg,
            "pid": os.getpid(),
            "host": _host(),
            "boot_id": _boot_id(),
            "started_at": self._issued_at,
            "expires_at": now_s + self.ttl_s,
        }

    def renew(self, now_s: float | None = None) -> bool:
        """Refresh the lease. ``False`` means the claim is no longer ours."""
        now = self.clock() if now_s is None else now_s
        current = read_claim(self.path.parent)
        if current is None or current.get("token") != self.token:
            return False
        try:
            _write_claim_atomic(self.path, self.record(now))
        except OSError:
            return False
        self._issued_at = now
        self._renewed_at = now
        return True

    def renew_if_due(self, now_s: float | None = None) -> bool:
        """Renew only when a third of the TTL has gone (the per-entry call)."""
        now = self.clock() if now_s is None else now_s
        if now - self._renewed_at < self.ttl_s / RENEW_FRACTION:
            return True
        return self.renew(now)

    def release(self) -> bool:
        """Give the claim up. ``False`` means we no longer held it."""
        raw = _read_raw(self.path)
        if raw is None:
            return False
        try:
            record = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return False
        if not isinstance(record, dict) or record.get("token") != self.token:
            return False
        return _remove_if_unchanged(self.path, raw)


class DrainClaimUnavailable(RuntimeError):
    """The claim could not be created for an ENVIRONMENT reason (not contention).

    Distinct from ``acquire``'s ``None``, which means "another holder has it".
    This one means the claim FILE could not be written at all — an unwritable or
    missing spool root. The caller decides; :func:`flush_spool` proceeds
    UNCLAIMED and says so, because a root that cannot hold a 60-byte claim file
    cannot hold a spool entry either (both go through ``os.open`` with ``0600`` in
    the same directory), so the pass about to run can only report the broken
    spool — and refusing to run would hide that report, which is the false-PASS
    direction this codebase treats as the worse error.
    """


def acquire(
    root: Path,
    *,
    leg: str,
    ttl_s: float = DEFAULT_TTL_S,
    now: float | None = None,
    clock: Callable[[], float] = time.time,
    alive: Callable[[object], bool] = pid_alive,
) -> DrainClaim | None:
    """Take the spool's drain claim, or return ``None`` when another holder has it.

    ``None`` is NOT an error: it is the correct answer for the second of two
    concurrent drains, and the caller must treat it as "do nothing this pass" —
    every entry it would have attempted is still on the spool and the holder is
    working the same list.

    ``leg`` names the caller (``"python"`` / ``"pi"``) for the diagnosis an
    operator reads; it is not part of the ownership test.

    Raises :class:`DrainClaimUnavailable` when the claim cannot be WRITTEN (an
    environment failure, distinct from contention — see that class).
    """
    root = Path(root)
    path = claim_path(root)
    boot_id = _boot_id()
    host = _host()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DrainClaimUnavailable(str(exc)) from exc
    for _ in range(ACQUIRE_ATTEMPTS):
        now_s = clock() if now is None else now
        token = secrets.token_hex(8)
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{token}")
        claim = DrainClaim(path=path, token=token, leg=leg, ttl_s=ttl_s,
                           clock=clock, _issued_at=now_s)
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(claim.record(now_s), ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            # `link` is the atomic create-if-absent (module docstring): the target
            # never exists without its full content.
            os.link(tmp, path)
        except FileExistsError:
            existing = _read_raw(path)
            try:
                parsed = json.loads(existing.decode("utf-8")) if existing else None
            except (ValueError, UnicodeDecodeError):
                parsed = None
            if not claim_is_stale(parsed, now_s=now_s, host=host,
                                boot_id=boot_id, alive=alive):
                return None
            if existing is not None:
                _remove_if_unchanged(path, existing)
            continue
        except OSError as exc:
            raise DrainClaimUnavailable(str(exc)) from exc
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
        claim._renewed_at = now_s
        return claim
    return None


class DrainClaimHeld:
    """Read-only description of somebody ELSE's claim (`session drain`, health)."""

    __slots__ = ("record",)

    def __init__(self, record: dict) -> None:
        self.record = record

    @property
    def leg(self) -> str | None:
        value = self.record.get("leg")
        return value if isinstance(value, str) else None

    @property
    def pid(self) -> int | None:
        value = self.record.get("pid")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @property
    def expires_at(self) -> float | None:
        return _finite(self.record.get("expires_at"))

    def line(self, root: Path) -> str:
        """One operator-readable sentence naming WHO holds it and until when."""
        who = f"pid {self.pid}" if self.pid is not None else "an unnamed process"
        leg = f" ({self.leg} leg)" if self.leg else ""
        expires = self.expires_at
        until = (f", lease to {time.strftime('%H:%M:%SZ', time.gmtime(expires))}"
                 if expires is not None else "")
        return (f"another drainer holds this spool{leg}: {who}{until} — "
                f"nothing attempted ({claim_path(root)})")


def holder(root: Path) -> DrainClaimHeld | None:
    """The current holder, when there is one — for a caller that gave up."""
    record = read_claim(root)
    return DrainClaimHeld(record) if record is not None else None
