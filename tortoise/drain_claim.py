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
2. **Expired** — ``now >= expires_at`` (``expires_at`` is MILLISECONDS on disk,
   ``now`` is seconds — the predicate does the conversion). This is the ONLY rule that applies to a
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
import stat
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

#: The claim file, in the spool ROOT — never ``entries/``, so it is invisible to
#: the spool's own accounting (`_entry_bytes`, `prune_spool`, `_scan_spool`),
#: exactly like ``status.json``. A file inside ``entries/`` would be counted as
#: spool occupancy forever and could push a box over a ceiling.
CLAIM_NAME = "drain.claim"

#: Bumped only if a field's MEANING changes. A record whose version is unknown
#: is unattributable (staleness rule 1), never assumed compatible.
#:
#: ⛔ v2 = the timestamps are MILLISECONDS (see `TIMESTAMP_UNIT_MS`). v1 wrote
#: SECONDS here while the Pi leg wrote milliseconds, so each leg read the other's
#: live claim as long expired and STOLE it — two drainers, which is the defect
#: this whole module removes (found in review, measured across processes: a live
#: Python claim read `stale` to the real TypeScript predicate). Bumping the
#: version is what makes a surviving v1 record SAFE: rule 1 treats it as
#: unattributable, so it is reclaimed by lease rather than misevaluated by 1000x.
#: Generation 33 has not shipped, so both legs move together.
CLAIM_VERSION = 2

#: The claim's on-disk timestamps are milliseconds since the epoch. Both legs must
#: agree — `Date.now()` is ms in the Pi leg, `time.time()` is SECONDS in Python,
#: and the mismatch is silent — so the unit lives in ONE named constant that both
#: write through and a cross-leg test pins.
TIMESTAMP_UNIT_MS = 1000.0

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
    # Rule 1 also covers a record whose VERSION this build does not know: a
    # future format must never be interpreted by today's semantics (the trait a
    # version field exists for) — it is re-verifiable by expiry instead.
    if record.get("version") != CLAIM_VERSION:
        return True
    expires = _finite(record.get("expires_at"))
    if expires is None:
        return True  # rule 1: nothing says when it ends
    if now_s * TIMESTAMP_UNIT_MS >= expires:
        return True  # rule 2: expired (the only rule for a foreign host)
    if host is None:
        return False
    if record.get("host") != host:
        return False  # rule 2 only — this box cannot check that host's pids
    recorded_boot = record.get("boot_id")
    if recorded_boot and boot_id and recorded_boot != boot_id:
        return True  # rule 3, rebooted: the recorded pid is not the holder
    pid = record.get("pid")
    # rule 3: the holder is gone (an unusable `pid` is not a holder fact — fall through)
    return (
        isinstance(pid, int)
        and not isinstance(pid, bool)
        and pid > 0
        and not alive(pid)
    )


def read_claim(root: Path) -> dict | None:
    """The parsed claim, or ``None``. The SURFACE (`session drain`, health, tests).

    ⛔ READS GO THROUGH :func:`_read_raw`, which is ``lstat``-gated, and that is the
    same gate the Pi leg's ``readDrainClaim`` carries. This function used to call
    ``Path.read_text()`` directly — so it BLOCKED FOREVER on a FIFO or a socket at
    the claim path (measured: killed at 8 s, no return) while the TypeScript twin
    answered in ~0 s, and it is what ``renew``, :func:`_claim_still_ours` and
    :func:`holder` actually call, i.e. the drain, the health line and every
    ``session drain`` on the box (#14423 review, P1: the earlier FIFO fix landed
    only on `acquire`'s path).
    """
    raw = _read_raw(claim_path(root))
    if raw is None:
        return None
    try:
        record = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return record if isinstance(record, dict) else None


def _read_raw(path: Path) -> bytes | None:
    """The claim file's bytes, or ``None`` when they cannot be had.

    ⛔ ``lstat`` FIRST: reading a FIFO or a socket BLOCKS until a writer appears, so
    a corrupt object at the claim path used to hang the drain, ``renew``, ``holder``
    and every health surface FOREVER (measured: a FIFO never returned; review
    finding). Only a REGULAR file is ever read, and a non-regular object answers
    ``None`` immediately — which the caller then handles as "unreadable", never as
    "read these bytes".
    """
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        return path.read_bytes()
    except OSError:
        return None


def _takedown(path: Path, token: str) -> tuple[Path, bytes] | None:
    """Atomically MOVE the claim aside, so the comparison can be trusted.

    ⛔ THE READ-COMPARE-UNLINK SEQUENCE THIS REPLACES WAS RACY, and the race
    restored the exact defect this module exists to remove (found in review).
    Two processes that both judged the same claim stale both pass a *compare*,
    then one `link`s its own claim while the other is between its `read` and its
    `unlink` — and the second `unlink` removes the NEW, LIVE claim. Both then
    hold it, and the loser only notices at its next refresh (up to TTL/3 later).
    Reproduced deterministically by delaying one contender between the compare
    and the unlink.

    ``rename`` is the arbiter instead: exactly one contender can move the file
    away, and the CONTENT THAT WAS THERE is what lands in the quarantine — so the
    caller compares bytes it provably owns the moment of, not bytes that could
    have been replaced underneath it.

    Returns ``(quarantine_path, bytes)``, or ``None`` when the claim was already
    gone (a peer moved it first, which is not a failure). The caller MUST dispose
    of the quarantine: :func:`_dispose_quarantine`.
    """
    # ⚠️ The `.tmp-` INFIX is load-bearing, not cosmetic: `capture_spool`'s
    # scratch sweeper deletes abandoned `*.tmp-*` files under the spool root by
    # mtime, and a taker killed between the rename and the disposal would
    # otherwise leave a small file in the root that NOTHING ever collects (the
    # root holds only `drain.claim` and `status.json`, so no spool scan walks it).
    # The lifetime here is microseconds, so the sweeper can never race a live
    # quarantine: its bound is minutes.
    quarantine = path.with_name(f"{path.name}.tmp-take-{os.getpid()}-{token}")
    try:
        os.rename(path, quarantine)
    except (FileNotFoundError, OSError):
        return None
    data = _read_raw(quarantine)
    if data is None:  # pragma: no cover — a rename we cannot read back
        with contextlib.suppress(OSError):
            os.unlink(quarantine)
        return None
    return quarantine, data


def _dispose_quarantine(quarantine: Path, data: bytes,
                        path: Path, expected: bytes) -> bool:
    """Drop the quarantined claim, or PUT IT BACK if it was not the one we judged.

    ``True`` means the bytes really were ``expected`` (ours / the stale record) and
    the claim is gone. ``False`` means we displaced somebody else's NEW claim — a
    third process installed it between our rename and our write — so the bytes are
    restored by ``link``, which cannot replace a claim a FOURTH process has since
    installed.

    ⛔ WHAT HAPPENS WHEN EVEN THE RESTORE LOST, stated accurately because the earlier
    version of this sentence was FALSE and a false sentence is worse than no
    sentence: the displaced holder does NOT "stop" — its claim file is gone, so it
    keeps draining until its next `renew`/`renew_if_due` tells it the claim is not
    ours, up to ``TTL/3`` (≈40 s) away. Two drainers can coexist for that window, and
    it is BOUNDED BY THE LEASE rather than unbounded, which is the only claim this
    code makes. The restore attempt is therefore LOGGED rather than swallowed: a
    silently deleted live claim is exactly the class this module's rules exist to
    make visible (#14423 cycle-7 review, P2).
    """
    if data != expected:
        try:
            os.link(quarantine, path)
        except FileExistsError:
            # A fourth process holds the path now. The displaced record's bytes are
            # gone, and the only honest thing left is to say so.
            print(
                "tortoise.drain_claim: a displaced claim could not be restored "
                f"({path} is held by a newer claim); that holder keeps draining "
                f"until its next refresh (up to {_ttl_third_hint()})",
                file=sys.stderr)
        except OSError as exc:
            print(
                f"tortoise.drain_claim: a displaced claim could not be restored "
                f"({exc}); it will be re-verified at the holder's next refresh",
                file=sys.stderr)
        with contextlib.suppress(OSError):
            os.unlink(quarantine)
        return False
    with contextlib.suppress(OSError):
        os.unlink(quarantine)
    return True


def _ttl_third_hint() -> str:
    """The bound the restore-failure message quotes: a third of the default lease.

    A function rather than prose so the sentence and the constant cannot drift apart:
    the earlier comment hard-coded "≤ TTL/3 ≈ 40 s" beside a ``DEFAULT_TTL_S`` of 120
    — the number was RIGHT and nothing tied the two together, which is how a review
    finds a comment that describes a different constant than the one in the file.
    """
    return f"{DEFAULT_TTL_S / RENEW_FRACTION:.0f} s"


def _claim_still_ours(path: Path, token: str) -> bool:
    """Is the claim at ``path`` still ours? Used when a refresh WRITE failed.

    A failed write is not evidence that a peer took the claim over, and treating
    it as one (a) aborts a pass that still owns the spool and (b) leaves the claim
    parked with a live pid, so every later pass reads ``held_by_claim`` for up to
    the full lease — while the operator is told a peer took it (review finding).
    """
    record = read_claim(path.parent)
    return isinstance(record, dict) and record.get("token") == token


def _write_claim_linked(path: Path, record: dict) -> None:
    """Install ``record`` at ``path`` with ``os.link`` — create-if-absent.

    The RENEW path, and the reason it is ``link`` and not ``os.replace``: renew
    must be a compare-and-swap against the claim it judged stale-aside, so the
    install has to FAIL rather than overwrite when a peer claimed inside the
    window. ``os.replace`` would clobber that peer's fresh record silently — the
    two-drainer defect — while ``link`` raises ``FileExistsError``, which the
    caller reads as "we are no longer the holder". Full content is visible at
    creation (the same property ``acquire`` relies on), so no reader can observe a
    half-written record and mis-judge it as corrupt.

    Raises ``FileExistsError`` when the claim path is occupied, and ``OSError`` on
    any other failure; the caller restores what it moved aside in that case.
    """
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{record['token']}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.link(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


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
            # ON-DISK UNIT IS MILLISECONDS (TIMESTAMP_UNIT_MS): the Pi leg's
            # `Date.now()` is ms, and a unit mismatch between the legs is silent.
            "started_at": self._issued_at * TIMESTAMP_UNIT_MS,
            "expires_at": (now_s + self.ttl_s) * TIMESTAMP_UNIT_MS,
        }

    def renew(self, now_s: float | None = None) -> bool:
        """Refresh the lease. ``False`` means the claim is no longer ours.

        ⛔ THREE GUARDS, all from review findings, all one-directional:

        * a claim that is ALREADY EXPIRED is never resurrected — see the check
          below;
        * a failed WRITE is not "not ours": :func:`_claim_still_ours` says
          whether the record is still ours, and the caller keeps draining on the
          un-refreshed lease rather than aborting a pass it still owns;
        * the refresh is a **COMPARE-AND-SWAP on the inode**, not a read followed
          by an ``os.replace``. This is the one that took a second review cycle,
          and the reason is worth keeping: the earlier version read the record,
          replaced it, and then READ BACK to verify — which reads the bytes this
          process just wrote, so it always matched and the guard was vacuous. The
          race it thought it covered is real (a peer whose takeover lands between
          our read and our replace has its fresh claim clobbered, and both
          processes then hold the spool for up to ``TTL/3``), and the trigger is a
          renewal landing at the deadline — a freeze, or the first write that
          succeeds after a run of failed ones — which is exactly when a stale
          on-disk record exists for the peer to judge. Reusing :func:`_takedown`
          (the same arbiter ``acquire`` uses) and installing the refresh with
          ``os.link`` makes the window a real CAS: ``EEXIST`` means a peer claimed
          in it and THEIR claim stands, which is the safe direction.
        """
        now = self.clock() if now_s is None else now_s
        raw = _read_raw(self.path)
        if raw is None:
            return False
        try:
            current = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return False
        if not isinstance(current, dict) or current.get("token") != self.token:
            return False
        expires = _finite(current.get("expires_at"))
        if expires is not None and now * TIMESTAMP_UNIT_MS >= expires:
            return False
        # ── the CAS ──────────────────────────────────────────────────────────
        # 1. move the claim aside atomically, so no other process can judge the
        #    record we are about to compare against (see `_takedown`);
        moved = _takedown(self.path, self.token)
        if moved is None:
            return False  # a peer moved it first: it is no longer ours to refresh
        quarantine, data = moved
        if data != raw:
            # We displaced a NEWER record than the one we judged — `_takedown`
            # puts it back; we are not the holder any more.
            _dispose_quarantine(quarantine, data, self.path, raw)
            return False
        # 2. install the refreshed record with `link` (create-if-absent), so a peer
        #    that claimed inside the window is never clobbered.
        try:
            _write_claim_linked(self.path, self.record(now))
        except FileExistsError:
            # A peer holds the claim now. Their record STANDS; ours is obsolete.
            _dispose_quarantine(quarantine, data, self.path, raw)
            return False
        except OSError:
            # The write failed. Put the record we moved aside BACK (never lose a
            # lease to a failed refresh) and report honestly from the path.
            with contextlib.suppress(OSError):
                os.link(quarantine, self.path)
            with contextlib.suppress(OSError):
                os.unlink(quarantine)
            return _claim_still_ours(self.path, self.token)
        _dispose_quarantine(quarantine, data, self.path, raw)
        # ⛔ `_issued_at` is NOT advanced: it is when this claim was ACQUIRED, and
        # the Pi leg deliberately preserves it across renewals (`started_at:
        # record.started_at`). Advancing it here made the two legs disagree about a
        # field in a file whose contract is field-level agreement (review finding).
        self._renewed_at = now
        return True

    def renew_if_due(self, now_s: float | None = None) -> bool:
        """Renew only when a third of the TTL has gone (the per-entry call)."""
        now = self.clock() if now_s is None else now_s
        if now - self._renewed_at < self.ttl_s / RENEW_FRACTION:
            return True
        return self.renew(now)

    def reclaim(self, now_s: float | None = None) -> bool:
        """Re-arm a lease that lapsed while WE were still the holder.

        ⛔ THIS EXISTS BECAUSE :meth:`renew` CANNOT DO IT, AND THE DIFFERENCE IS A
        PERMANENT STALL. ``renew`` refuses an already-expired record on purpose (a
        holder that overran its lease must not overwrite the successor that
        legitimately took over). But ONE step of a pass is unbounded and runs
        BETWEEN the acquisition and the first refresh: the whole-spool scan
        (``list_spool_metas`` / ``listSpoolEntries``). A scan longer than the TTL
        therefore expires OUR OWN lease, every later refresh refuses, the pass
        attempts nothing, and the next opportunity repeats it — measured at the
        production TTL: two passes, ``attempted: 0``, ``lost_claim: True``, zero
        POSTs, forever, with a message that blames a peer that does not exist
        (#14423 merge-gate review, runtime finding). The scan is unbounded on a
        high-latency ``$HOME`` and the #14342 incident started at 908 entries.

        The distinction that makes this safe is OWNERSHIP, not the clock: the record
        on disk still carries OUR token, so the lease was ours and lapsed by our own
        work rather than being taken. The re-arm is the SAME compare-and-swap
        ``renew`` uses — takedown-aside, byte compare, ``os.link`` install — so a
        peer that took over in the meantime wins (its record is restored, we return
        ``False``), and the caller stops exactly as before.

        ``True`` means the lease is ours again; ``False`` means it is not (a peer
        holds it, or the spool cannot be written).
        """
        now = self.clock() if now_s is None else now_s
        raw = _read_raw(self.path)
        if raw is None:
            return False
        try:
            current = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return False
        if not isinstance(current, dict) or current.get("token") != self.token:
            return False  # a peer's record: their claim stands
        moved = _takedown(self.path, self.token)
        if moved is None:
            return False  # a peer moved it first
        quarantine, data = moved
        if data != raw:
            _dispose_quarantine(quarantine, data, self.path, raw)
            return False
        try:
            _write_claim_linked(self.path, self.record(now))
        except FileExistsError:
            _dispose_quarantine(quarantine, data, self.path, raw)
            return False
        except OSError:
            with contextlib.suppress(OSError):
                os.link(quarantine, self.path)
            with contextlib.suppress(OSError):
                os.unlink(quarantine)
            return _claim_still_ours(self.path, self.token)
        _dispose_quarantine(quarantine, data, self.path, raw)
        self._issued_at = self._issued_at or now
        self._renewed_at = now
        return True

    def release(self) -> bool:
        """Give the claim up. ``False`` means we no longer held it.

        The takedown is atomic (see :func:`_takedown`): this used to read the
        record, compare and then `unlink` — a sequence in which a successor
        installed between the compare and the unlink was deleted by the previous
        holder.

        ⛔ AND A RELEASE FIRST CHECKS THAT THE CLAIM IS OURS, WITHOUT MOVING IT.
        The takedown-then-compare version made the docstring's promise ("a release
        can only ever dispose of a claim it actually owned") false in a THREE-process
        interleaving: the not-ours release still `rename`d the successor's claim
        aside, and if a third process installed into the now-empty path the restore
        `link` failed `EEXIST` and the successor's bytes were unlinked — reproduced,
        `release()` returned ``False`` and the successor's claim was GONE
        (#14423 review, P2). Reading our token first has no window at all in the
        not-ours direction, which is the direction that destroyed a live claim; the
        ours direction keeps the takedown, whose quarantine compare is what makes
        the disposal safe against a peer that replaces it mid-release.
        """
        current = _read_raw(self.path)
        if current is None:
            return False
        try:
            held = json.loads(current.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return False
        if not isinstance(held, dict) or held.get("token") != self.token:
            return False  # not ours: leave it exactly where it is
        moved = _takedown(self.path, self.token)
        if moved is None:
            return False
        quarantine, data = moved
        if data != current:
            # A peer replaced the record between our read and our rename. Its bytes
            # are restored by the sentinel arm and our release declines.
            _dispose_quarantine(quarantine, data, self.path, b"<not-ours>")
            return False
        with contextlib.suppress(OSError):
            os.unlink(quarantine)
        return True


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


def _clear_unusable_claim_path(path: Path) -> bool:
    """Remove something at the claim path that is not a readable claim.

    A DIRECTORY, a socket or a dangling symlink at `<spool>/drain.claim` is
    corruption, not a holder: `link` fails `EEXIST` at it, nothing can read it as a
    claim, and leaving it there blocks every future drain FOREVER — which is the
    "a stale claim is re-verifiable, not a permanent block" rule #14350 states,
    failing in the worst direction. Measured: a directory there made every pass
    report "another drainer holds this spool" with NO identity, and nothing ever
    removed it.

    A symlink is removed WITHOUT following it (`unlink` on the link), so this can
    never reach through to a target outside the spool. A non-empty directory that
    refuses `rmdir` is left alone and reported: deleting unknown content is the
    worse error, and the caller's answer for that is :class:`DrainClaimUnavailable`
    ("run unclaimed, and SAY so") rather than a silent block.
    """
    try:
        mode = os.lstat(path).st_mode
    except OSError:
        return False
    try:
        if stat.S_ISDIR(mode):
            path.rmdir()  # an EMPTY directory; refuses `ENOTEMPTY` otherwise
            return True
        # A symlink is unlinked AS A LINK (never followed), and a FIFO/socket too: it
        # is an object, not content, so `unlink` is the correct removal.
        os.unlink(path)
        return True
    except OSError:
        return False


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
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
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
            if existing is None:
                # The read failed. ⛔ WHICH KIND OF FAILURE IT WAS DECIDES WHETHER
                # WE MAY DELETE, and conflating them is how this code once deleted a
                # LIVE claim (review P0): a transient local error (EMFILE, EIO, an
                # NFS hiccup, EACCES after a mode drift) on a perfectly good regular
                # file is NOT evidence that the file is junk. The doctrine everywhere
                # else in this module is "unprovable reads as ALIVE"; here it must be
                # the same, so a regular file we could not read is honoured (return
                # `None` = somebody holds it) and NEVER unlinked.
                try:
                    mode = os.lstat(path).st_mode
                except FileNotFoundError:
                    continue  # it vanished (a peer released); retry the link
                except OSError:
                    return None  # cannot even stat it: honour it
                if stat.S_ISREG(mode):
                    return None
                # A directory, symlink, FIFO, socket or device: corruption, not a
                # holder. Rule 1 already says stale, and leaving it would `EEXIST`
                # every future attempt — a permanent block, which #14350 forbids.
                if not _clear_unusable_claim_path(path):
                    # `from None`: the `FileExistsError` we are handling is the
                    # EXPECTED contention signal, not this condition's cause —
                    # chaining it would point a reader at the wrong file (B904).
                    raise DrainClaimUnavailable(
                        f"{path} exists and is not a claim, and cannot be cleared — "
                        "the spool root is not writable by this process") from None
                continue
            # ATOMIC TAKEDOWN, then verify in quarantine: see `_takedown`. The
            # previous read-compare-unlink let two contenders delete each other's
            # fresh claims (review finding).
            moved = _takedown(path, token)
            if moved is None:
                continue  # a peer moved it first; the next attempt decides
            quarantine, data = moved
            if not _dispose_quarantine(quarantine, data, path, existing):
                # We displaced a NEWER claim than the one we judged stale (a
                # third process installed it). It is restored; give up this tick.
                return None
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
        """The lease end, in SECONDS, for human-facing surfaces.

        The record stores milliseconds (TIMESTAMP_UNIT_MS); every consumer that
        formats or compares a time in seconds goes through here, so the unit is
        converted in ONE place.
        """
        value = _finite(self.record.get("expires_at"))
        return None if value is None else value / TIMESTAMP_UNIT_MS

    def line(self, root: Path) -> str:
        """One operator-readable sentence naming WHO holds it and until when."""
        who = f"pid {self.pid}" if self.pid is not None else "an unnamed process"
        leg = f" ({self.leg} leg)" if self.leg else ""
        expires = self.expires_at
        until = ""
        if expires is not None:
            # ⛔ GUARDED: `time.gmtime` raises on an out-of-range instant, and this
            # line is the OPERATOR surface for a face we could not judge. A record
            # with a finite but absurd `expires_at` (1e30) is honoured by rule 2
            # (it is never expired), so `flush_spool` called this on EVERY pass and
            # `OverflowError` escaped OUTSIDE its try/finally — one corrupt claim
            # file blocked the spool permanently, the outcome #14350 forbids
            # (#14423 review, P2, reproduced end-to-end).
            try:
                until = f", lease to {time.strftime('%H:%M:%SZ', time.gmtime(expires))}"
            except (OverflowError, OSError, ValueError):
                until = ""
        return (f"another drainer holds this spool{leg}: {who}{until} — "
                f"nothing attempted ({claim_path(root)})")


def holder(root: Path) -> DrainClaimHeld | None:
    """The current holder, when there is one — for a caller that gave up."""
    record = read_claim(root)
    return DrainClaimHeld(record) if record is not None else None
