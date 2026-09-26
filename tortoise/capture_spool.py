"""#3963 — the durable capture spool (Python leg: Claude Code hooks + CLI).

The defect this closes: capture happened ONLY on `SessionEnd`, a hook Claude
Code cancels at its ~1.5 s default timeout (a measured run took 9.26 s — #3754)
and which does not fire at all on a user interrupt. An interrupted or
laptop-closed session therefore filed NOTHING, invisibly. The transcript is
already durable on disk; what is lost is the *filing*.

Cadence — cheap capture FREQUENTLY, costly extraction DEFERRED
(``~/.pi/agent/state/research-capture-cadence-precedent.md`` §c):

* **cheap**: every capture appends only the NEW turns to a local write-ahead log
  (``<spool>/entries/<key>.turns.jsonl``). The append is O(new turns) — the
  session-sized read belongs to the caller's snapshot, not to a rewrite here.
* **deferred**: the network POST (which triggers server-side LLM extraction) is
  attempted by ``tortoise session capture`` and, at the next opportunity, by
  ``tortoise session drain`` — wired into the SessionStart hook. Session end
  stays a final flush, not the mechanism of record.

There is deliberately NO "every N turns" constant: the field converges on
per-turn capture with deferred extraction, and no product ships a
product-defined N (research §c.2).

Bounds are COUNT and BYTES — never time. #3870 (owner) ruled that the local
capture spool is type-2 user data kept "until the user deletes it", and that a
TTL / max-age / "prune after N days" CONTRADICTS that ruling. A count or byte
bound is a size bound, not a clock: it evicts the OLDEST entries past the
ceiling and records every eviction.

Idempotency: ``session_id`` remains the SERVER's upsert key. On top of it the
client derives a content-addressed capture key
``sha256(session_id \\0 sha256(turns))`` — stable across replays, distinct when
the conversation actually changed. A snapshot whose capture key was already
filed short-circuits before the network, so replaying a spool twice files ONE
session and issues ONE POST. The same key is what makes #3713's in-flight 409
benign: a 409 is retried, and the retry replays rather than losing the write.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "DEFAULT_BOUNDS",
    "DISCARD_BYTES_EXCEEDED",
    "DISCARD_CORRUPT",
    "DISCARD_COUNT_EXCEEDED",
    "DISCARD_ENTRY_TOO_LARGE",
    "DISCARD_TRANSCRIPT_EMPTY",
    "PROBE_SESSION_ID_PREFIX",
    "Bounds",
    "FlushSummary",
    "PostOutcome",
    "Snapshot",
    "backoff_delay",
    "capture_key",
    "classify_failure",
    "content_digest",
    "entry_key",
    "flush_spool",
    "is_probe_session_id",
    "is_spooled",
    "list_spool_metas",
    "read_discards",
    "read_spool_meta",
    "read_spool_turns",
    "remove_spool_entry",
    "spool_dir",
    "write_spool_entry",
]

SPOOL_MAX_ENTRIES = 1000
SPOOL_MAX_TOTAL_BYTES = 256 * 1024 * 1024
# Must exceed the SERVER's legal maximum, or a legal capture is discarded as
# oversized: 500 turns x 5000 chars x up to 4 UTF-8 bytes/char ~= 10 MB, plus
# the envelope. (A 4 MiB ceiling silently discarded legal CJK sessions.)
SPOOL_MAX_ENTRY_BYTES = 16 * 1024 * 1024
RETRY_BASE_SECONDS = 30
RETRY_MAX_SECONDS = 6 * 60 * 60
# `attempts` is clamped to this before `backoff_delay` computes 2**(n-1): the
# backoff saturates (30 * 2**10 > 6 h) long before, and a corrupt stored value
# of 10**400 would otherwise try to materialise an astronomically large int.
_MAX_ATTEMPTS = 64

DISCARD_ENTRY_TOO_LARGE = "entry_too_large"
DISCARD_COUNT_EXCEEDED = "spool_count_exceeded"
DISCARD_BYTES_EXCEEDED = "spool_total_bytes_exceeded"
DISCARD_TRANSCRIPT_EMPTY = "transcript_empty"
#: A transcript that cannot be read at all (EACCES, EISDIR, non-UTF-8 bytes).
DISCARD_TRANSCRIPT_UNREADABLE = "transcript_unreadable"
#: An UNEXPECTED failure while filing one entry (a bug, not a classification):
#: recorded and backed off, but the entry is KEPT — an internal bug must never
#: delete user data.
DISCARD_ENTRY_FAILED = "entry_failed"
#: The spool's own `entries/` listing cannot be read — every capture is invisible.
DISCARD_SPOOL_UNREADABLE = "spool_unreadable"
DISCARD_CORRUPT = "corrupt_entry"
#: A filesystem error while writing the spool — recorded, never silent.
DISCARD_WRITE_FAILED = "spool_write_failed"

#: Discard reasons where the entry is NOT removed — the capture is still on the
#: spool and WILL be retried, so it is not a loss.
#:
#: Everything else in the ledger reached it through ``_discard_entry``, which
#: records and then UNLINKS the turn log + meta: a real loss.
#:
#: The distinction exists because a caller's exit code must answer "was anything
#: LOST?" — never "is the ledger non-empty?". ``DISCARD_ENTRY_FAILED`` is an
#: internal bug (an unexpected raise while filing), not a data loss; the entry is
#: kept with a backoff, so treating it as a failure would report a capture as
#: lost while it is still on disk and still queued (#3971).
RETAINED_DISCARD_REASONS = frozenset({DISCARD_ENTRY_FAILED})


def is_lost_discard(record: dict) -> bool:
    """True when a ledger record means the entry was REMOVED (a real loss).

    A retained discard (``RETAINED_DISCARD_REASONS``) is recorded only, so the
    capture survives to retry. Use this — not ``summary.discarded`` — to decide
    whether a capture attempt actually lost anything.
    """
    return record.get("reason") not in RETAINED_DISCARD_REASONS


@dataclass(frozen=True)
class Bounds:
    """The spool's COUNT and BYTE ceilings (never a TTL — see the module doc)."""

    max_entries: int = SPOOL_MAX_ENTRIES
    max_total_bytes: int = SPOOL_MAX_TOTAL_BYTES
    max_entry_bytes: int = SPOOL_MAX_ENTRY_BYTES


DEFAULT_BOUNDS = Bounds()


@dataclass
class Snapshot:
    session_id: str
    turns: list[dict]
    source: str
    machine_id: str
    model: str | None = None
    harness: str = "claude"


@dataclass
class PostOutcome:
    """What one upload attempt observed. ``body`` is the parsed 2xx body."""

    ok: bool
    status: int | None = None
    detail: str = ""
    body: dict = field(default_factory=dict)


@dataclass
class FlushSummary:
    attempted: int = 0
    filed: int = 0
    deferred: int = 0
    skipped: int = 0
    # Entries INSIDE their backoff window — deferred by a previous refusal, not
    # by anything wrong now. Counted separately from `skipped` because since
    # #4714 moved 402 from "discard" to "defer", a quota-blocked spool reaches a
    # steady state where EVERY drain skips and nothing is attempted: without
    # this counter the drain prints nothing at all and N captures sit unfiled
    # invisibly (the discard ledger used to be the signal).
    held_by_backoff: int = 0
    # Deliberately not attempted: the session that is LIVE right now, held back
    # for its own final flush (see ``exclude_session_id``). Counted separately
    # from ``skipped`` so the drain reports WHICH it did.
    held_back: int = 0
    # `session verify` probes refused filing (they are synthetic by
    # construction). Separate from `held_back` so a drain can SAY a refusal
    # happened: a silent refusal would leave an operator wondering why a
    # spooled session never lands.
    probe_refusals: list[dict] = field(default_factory=list)
    discarded: list[dict] = field(default_factory=list)
    outcomes: dict[str, PostOutcome] = field(default_factory=dict)

    @property
    def lost(self) -> list[dict]:
        """The ledger records that mean the capture is GONE.

        ``discarded`` is the FULL ledger, retained entries included, because the
        ledger is an audit surface — it must record every anomaly. An exit code
        must key on THIS instead: ``entry_failed`` keeps the entry on the spool
        with a backoff, so it is a deferral, not a loss (#3971).
        """
        return [d for d in self.discarded if is_lost_discard(d)]


# ── Paths ──────────────────────────────────────────────────────────────────


def spool_dir(env: Mapping[str, str] | None = None) -> Path:
    """The local capture spool. ``TORTOISE_CAPTURE_SPOOL_DIR`` overrides it.

    ``env`` is the environment the CALLER means — pass the env a hook was fired
    under, not this process's. Every lookup below honours it, because a caller
    that pins ``HOME`` (as `session verify` callers do) would otherwise have
    the seam write to one spool and the verification look in another, and
    report a false "nothing to clean up" (#4714 review).

    Fail-safe in tests: a test (or a PROCESS a test spawns) must never reach the
    developer machine's real spool (#3721's trap). Under pytest the path is
    derived DETERMINISTICALLY from the test id, so every process of one test
    shares one spool and none of them touches ``~/.tortoise``.
    """
    source: Mapping[str, str] = os.environ if env is None else env
    override = source.get("TORTOISE_CAPTURE_SPOOL_DIR")
    if override:
        return Path(override)
    test_id = source.get("PYTEST_CURRENT_TEST")
    if test_id:
        digest = hashlib.sha256(_utf8(test_id)).hexdigest()[:16]
        return Path(tempfile.gettempdir()) / "tortoise-capture-spool-tests" / digest
    home = source.get("HOME")
    base = Path(home) if home else Path.home()
    return base / ".tortoise" / "capture-spool"


def _entries_dir(root: Path) -> Path:
    return root / "entries"


def _meta_path(root: Path, session_id: str) -> Path:
    return _entries_dir(root) / f"{entry_key(session_id)}.meta.json"


def _log_path(root: Path, session_id: str) -> Path:
    return _entries_dir(root) / f"{entry_key(session_id)}.turns.jsonl"


def _discard_path(root: Path) -> Path:
    return root / "discarded.jsonl"


# ── Keys ───────────────────────────────────────────────────────────────────


def content_digest(turns: list[dict]) -> str:
    """sha256 of the canonical turns — the dedup + capture-key source."""
    blob = json.dumps(turns, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(_utf8(blob)).hexdigest()


def capture_key(session_id: str, turns: list[dict]) -> str:
    """The stable, content-addressed client idempotency key."""
    seed = f"{session_id}\u0000{content_digest(turns)}"
    return hashlib.sha256(_utf8(seed)).hexdigest()


def _utf8(text: str) -> bytes:
    """Encode for HASHING, tolerating lone surrogates.

    A `session_id` (or turn text) can carry a lone surrogate — JS `JSON.stringify`
    emits one for an unpaired code unit, and a hand-written meta can contain
    anything. `str.encode("utf-8")` raises `UnicodeEncodeError` on those, which
    escaped `entry_key`/`capture_key`/`content_digest` on EVERY leg (crashing
    `session spool` with a traceback, and wedging the drain by making the entry
    unreadable-then-removable-never). `surrogatepass` is deterministic and
    injective, and for every normal id it produces the SAME bytes as plain
    `utf-8`, so no existing key or digest changes. The digest (never the raw id)
    is what lands in a filename.
    """
    return text.encode("utf-8", "surrogatepass")


def entry_key(session_id: str) -> str:
    """Filesystem-safe, collision-resistant stem for a session id."""
    return hashlib.sha256(_utf8(session_id)).hexdigest()[:32]


# ── Failure classification + backoff ───────────────────────────────────────


def classify_failure(status: int | float | None, detail: str = "") -> str:
    """``"retry"`` (transient) or ``"permanent"``.

    TRANSIENT: no status (network / timeout / an unparseable status), 5xx, 3xx (a
    redirect on a stored api_url must not delete the capture), the retryable 4xx
    family (402/408/425/429), and EVERY 409. On this idempotent upsert a 409 is either
    #3713's in-flight concurrency condition (retry then replays) or a policy
    state (recording disabled) the user can reverse — a capture must not be
    destroyed because recording was briefly off. Matching the server's prose is
    deliberately avoided: the client ships independently.

    402 is TRANSIENT, and calling it permanent destroyed user data (#4714).
    The hosted quota gate refuses a capture whose *estimated* point cost would
    cross the org's cap. Since #4614 that refusal is a STRUCTURED detail, not
    prose:

        {"detail": {"code": "quota_exceeded", "resource": "points",
                     "used": 24956, "limit": 25000, "estimate": 48,
                     "message": "Team points limit reached: 24956 in use + 48
                                  estimated for this capture exceeds 25000."}}

    `est` is computed from the INCOMING capture, so the identical capture
    succeeds the moment a node is freed or the tier changes — exactly the
    "becomes valid by waiting" property that defines transient here. Classified
    permanent, `_flush_one` routed it to `_discard_entry`, which unlinks the
    turn log and meta: the spool's only copy of the user's session was deleted
    by the drain, and the hook's own advice ("run `tortoise session drain` to
    file it") was what triggered the loss. The spool exists to survive a
    transient refusal and file it later; in a quota-bound deployment 402 is the
    transient refusal that actually occurs, so the mechanism was inverted for
    precisely its own use case.

    Not detected by prose: the client ships independently of the server's
    wording, and a capacity/billing refusal is a category, not a string. #4614
    gave the refusal that category (`detail.code`); this classifier still keys
    on the STATUS, deliberately — see the note below.

    ⚠️ The status-keyed verdict is a DATA-SAFETY decision and is NOT narrowed
    by #4614. Now that a category exists, a caller *could* treat a
    `quota_exceeded` 402 as terminal, and that would re-open the exact data
    loss #4714 closed: any entry this drain would otherwise file later would be
    unlinked instead. The category is for REPORTING and for the surfaces that
    can act on it (`website/apps/dashboard`, the `capture-errors` breadcrumb);
    the spool keeps deferring. #5051 holds the question of when a refusal is
    genuinely terminal; until it is answered, retry is the safe direction.

    Any 402 is retried, with the ENTRY's `backoff_delay` capping the cadence —
    carried across turns, so a growing session is retried on the backoff clock
    rather than once per turn. That converts immediate loss into a BOUNDED,
    DEFERRED one: `SPOOL_MAX_ENTRIES` / `SPOOL_MAX_TOTAL_BYTES` still apply, and
    `prune_spool` evicts oldest-first with a recorded reason, so an org that
    stays over quota does eventually lose the oldest captures — visibly, on the
    discard ledger, never silently. Retry is the safe direction here: the
    asymmetry is a bounded, recorded deferral versus irreversible unlink of the
    only copy.

    PERMANENT: every other 4xx — a malformed payload or an out-of-range turn
    count never becomes valid by waiting.
    """
    # Total over "no status", matching the Pi leg's classifyFailure exactly so
    # the two cannot disagree: None (never got one) and a value that is not a
    # usable finite number (an unparseable status). Without the isfinite arm a
    # NaN returned "permanent" while the Pi leg returned "retry" for the same
    # input — a divergence the cross-leg parity test is meant to make
    # impossible.
    #
    # The `math.isfinite` call is WRAPPED, not guarded by an isinstance: an int
    # too large to convert to a float (`math.isfinite(10**400)`) raises
    # OverflowError rather than returning inf, and an escaping error here would
    # DROP the capture — `__main__`'s `_spool_if_retryable` swallows it into
    # "spool write failed", and `_flush_one` reports `entry_failed`. That is
    # strictly worse than either verdict this function can return, and it also
    # made the Python leg diverge from the Pi leg, which pins the same absurd
    # magnitude to Infinity and answers "retry".
    if status is None or isinstance(status, bool):
        # `bool` FIRST, because in Python a bool IS an int: `math.isfinite(True)`
        # is True, so `True`/`False` would fall through every transient arm and
        # land on "permanent" — routing a corrupt status to `_discard_entry` and
        # DELETING the capture. The Pi leg is correct here only by accident
        # (`Number.isFinite(true)` is false because `isFinite` requires
        # `typeof === "number"`), which is exactly the sort of accident the
        # cross-leg parity test cannot see once the two legs stop matching.
        return "retry"
    try:
        if not math.isfinite(status):
            return "retry"
    except (TypeError, ValueError, OverflowError):
        return "retry"
    if 300 <= status < 400:
        return "retry"
    if status >= 500:
        return "retry"
    if status in (402, 408, 425, 429):
        return "retry"
    if status == 409:
        return "retry"
    return "permanent"


def backoff_delay(attempts: int) -> int:
    """Exponential backoff for attempt N (1-based), capped at RETRY_MAX_SECONDS."""
    exponent = max(0, attempts - 1)
    return min(RETRY_BASE_SECONDS * (2 ** exponent), RETRY_MAX_SECONDS)


# ── Read/write ─────────────────────────────────────────────────────────────


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    # 0600: the spool holds full conversation text (the same private-file
    # convention ~/.tortoise uses for credentials).
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            # The whole point of the spool is surviving a kill; a rename without
            # an fsync can leave a zero-length file after a power loss.
            os.fsync(fh.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    os.replace(tmp, path)


def _sweep_stale_temp_files(root: Path, max_age_s: float = 300.0) -> None:
    """Remove our own abandoned `*.tmp-<pid>` scratch files.

    A killed process leaves a temp that matches neither the meta glob nor
    ``_entry_bytes``, so it would never be counted or pruned and could grow the
    spool past its byte ceiling forever. These are scratch artifacts, NOT user
    data (the #3870 no-TTL ruling covers captures): an mtime-based sweep of our
    own temp files is safe. Best-effort.
    """
    now = time.time()
    for directory in (root, _entries_dir(root)):
        for stale in directory.glob("*.tmp-*"):
            with contextlib.suppress(OSError):
                if now - stale.stat().st_mtime > max_age_s:
                    stale.unlink()


def _serialise_turn(turn: dict) -> str:
    return json.dumps({"role": turn["role"], "content": turn["content"]}, ensure_ascii=False) + "\n"


def _log_has_complete_records(path: Path) -> bool:
    """True when the turn log ends at a record boundary.

    A process killed mid-append leaves a partial line. Appending to it would
    FUSE the new turn into the garbage tail, the tail would then be skipped on
    read-back, and the lost turn would still be counted in the meta digest —
    a silent, unrecoverable loss. The caller rewrites the full log instead.
    """
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                return True
            fh.seek(-1, os.SEEK_END)
            return fh.read(1) == b"\n"
    except OSError:
        return False


def read_spool_turns(root: Path, session_id: str) -> list[dict]:
    """Rebuild the conversation from the write-ahead turn log.

    Returns exactly what is stored (any role), because the store is the record
    and the meta's `turns_count`/`content_digest` are computed over it: dropping
    a role here would silently post fewer turns than the digest covers while
    still stamping the entry FILED. Non-conversational roles are filtered at
    CAPTURE time (the CLI notes the drop), never silently at read time.
    """
    path = _log_path(root, session_id)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # An unreadable log (a directory in its place, EACCES, a partial-disk
        # error) must not escape the drain: the entry is reported as
        # `transcript_empty` and recorded, keeping `session drain`'s
        # always-exit-0 contract.
        #
        # `UnicodeDecodeError` is a `ValueError`, NOT an `OSError` — and a
        # KILLED/partial append of a multi-byte character (CJK/emoji, written
        # raw by `_serialise_turn`) leaves exactly that: a torn codepoint in the
        # log. Without it here the per-turn `session spool` crashed with a
        # traceback and the drain wedged with NO ledger line, which is the
        # failure this whole change exists to remove.
        return []
    turns: list[dict] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("role"), str) \
                and isinstance(parsed.get("content"), str):
            turns.append({"role": parsed["role"], "content": parsed["content"]})
    return turns


def read_spool_meta(root: Path, session_id: str) -> dict | None:
    try:
        return json.loads(_meta_path(root, session_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        # `UnicodeDecodeError` is a `ValueError` — see `read_spool_turns`.
        return None


def _entry_bytes(root: Path, session_id: str) -> int:
    total = 0
    for path in (_meta_path(root, session_id), _log_path(root, session_id)):
        with contextlib.suppress(OSError):
            total += path.stat().st_size
    return total


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _now_iso() -> str:
    """Microsecond-resolution UTC timestamp.

    Second resolution (strftime without %f) made `prune_spool`'s "oldest first"
    ordering a tie for entries written in the same second, so which entry got
    evicted depended on readdir order. Microseconds make the write order real;
    the `session_id` tiebreak below keeps it deterministic regardless.
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def record_discard(root: Path, record: dict) -> dict:
    """Append a discard record — the "never a silent drop" contract."""
    full = {"at": _now_iso(), **record}
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with _discard_path(root).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(full, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return full


def read_discards(root: Path) -> list[dict]:
    path = _discard_path(root)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _remove_entry_files(root: Path, session_id: str) -> None:
    for path in (_meta_path(root, session_id), _log_path(root, session_id)):
        with contextlib.suppress(OSError):
            path.unlink()


def is_spooled(root: Path, session_id: str) -> bool:
    """Whether a session currently has a spool entry (meta or turn log)."""
    return _meta_path(root, session_id).exists() or \
        _log_path(root, session_id).exists()


def remove_spool_entry(root: Path, session_id: str) -> None:
    """Unlink a session's spool entry (meta + turn log), best effort.

    Deliberately returns nothing: "did something get removed" and "is anything
    still there" are DIFFERENT questions, and a `bool` that conflates them
    lets a caller report a removal that silently failed — an unlink can fail on
    EACCES or a read-only volume (#4714 review). Callers that must know ask
    `is_spooled` before and after.

    Motivating case: ``session verify`` fires the real seam with a SYNTHETIC
    transcript, so a retryable refusal parks the probe in the durable spool,
    where a drain would POST it into the tenant graph. This is the tidy-up; the
    structural defense is `is_probe_session_id` + the drain's refusal, because
    the codex/cursor seams write from a DETACHED worker that can outlive this
    call.
    """
    _remove_entry_files(root, session_id)


#: A `session verify` probe id, matched STRUCTURALLY — `verify-<harness>-
#: <stamp>-<hex>` per `session_verify._probe_id`.
#:
#: A PREFIX test is not enough, and the difference is destructive: session ids
#: also come from `_local_session_id` (`<transcript-stem>-<digest>`), so a real
#: session file named `verify-my-notes.jsonl` derives the id
#: `verify-my-notes-0e9ebe1a9262`. Under a prefix test that real capture was
#: both refused AND deleted, with a ledger line calling it a probe — silently
#: destroying user data (#4714 review).
PROBE_SESSION_ID_PREFIX = "verify-"
_PROBE_SESSION_ID_RE = re.compile(
    r"verify-[a-z][a-z0-9-]*-\d{8}T\d{6}Z-[0-9a-f]{6}\Z")


def is_probe_session_id(session_id: str) -> bool:
    """Whether ``session_id`` is a ``session verify`` probe (never real data).

    Matches the full probe shape, not the prefix — see `_PROBE_SESSION_ID_RE`.
    This is the only thing preventing synthetic probe content from being
    extracted as memory, so it must stay narrow. A false POSITIVE merely holds
    a real capture back (reversible, and reported); a false NEGATIVE lets a
    probe reach the graph. The prefix match this replaced was worse than either
    — it refused AND deleted real data.

    ⚠️ This regex and `session_verify._probe_id` describe the SAME format in two
    places, and nothing enforces that they agree. `tests/test_session_import_codex.py::
    test_the_probe_match_cannot_drift_from_the_producer` asserts they do, by
    deriving its samples from `_probe_id` — if you change one, that test is what
    tells you to change the other (#4714 review).
    """
    return _PROBE_SESSION_ID_RE.match(session_id) is not None


def _discard_entry(root: Path, meta: dict, reason: str, detail: str = "") -> dict:
    """Record the discard FIRST, then remove the entry files.

    The ledger line is the contract ("never a silent drop"): unlinking first and
    recording second leaves a SIGKILL / ENOSPC window in which an unfiled
    capture is deleted with NO record. Recording first can at worst leave entry
    files behind a record (the next pass records again — a duplicate line, which
    an audit can reconcile; a missing line cannot be).
    """
    record = record_discard(root, {
        "session_id": meta["session_id"],
        "capture_key": meta.get("capture_key"),
        "reason": reason,
        "detail": detail,
    })
    _remove_entry_files(root, meta["session_id"])
    return record


def list_spool_metas(root: Path) -> tuple[list[dict], list[dict]]:
    """All spool metas; malformed ones become ``corrupt_entry`` discards.

    A meta that is valid JSON but has no usable ``session_id`` is ALSO corrupt —
    otherwise the drain crashes on `meta["session_id"]` instead of recording it.
    Both halves (meta + turn log) are removed: an orphaned log is invisible to
    the meta scan and would never be counted or pruned.
    """
    metas: list[dict] = []
    discards: list[dict] = []
    d = _entries_dir(root)
    # `Path.is_dir()` is True for a mode-000 directory and `Path.glob` SWALLOWS
    # the EACCES its `scandir` hits — so the old `is_dir()` + glob returned an
    # empty listing for a spool it could not read at all: the drain reported
    # "empty spool" forever, with no ledger line. `os.scandir` raises.
    #
    # There is deliberately NO `d.exists()` pre-check: `Path.exists()` itself
    # RAISES `PermissionError` when a parent lacks traverse permission (a
    # mode-000 spool ROOT), which escaped `flush_spool` and made `session drain`
    # exit 1 with a traceback. Absent-vs-unreadable is decided by the scandir
    # call instead: ENOENT is a fresh install, any other OSError is recorded.
    try:
        with os.scandir(d) as entries:
            names = sorted(e.name for e in entries if e.name.endswith(".meta.json"))
    except FileNotFoundError:
        # A fresh install: absent is not a failure.
        return metas, discards
    except OSError as exc:
        discards.append(record_discard(root, {
            "session_id": "spool",
            "reason": DISCARD_SPOOL_UNREADABLE,
            "detail": f"cannot list {d}: {exc}",
        }))
        return metas, discards
    for name in names:
        path = d / name
        meta = None
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            # `UnicodeDecodeError` is a `ValueError`: a non-UTF-8 meta is a
            # CORRUPT entry (recorded below), not a crash.
            meta = None
        if not isinstance(meta, dict) or not isinstance(meta.get("session_id"), str) \
                or not meta["session_id"]:
            stem = path.name[: -len(".meta.json")]
            discards.append(record_discard(root, {
                "session_id": f"unreadable:{stem}",
                "reason": DISCARD_CORRUPT,
                "detail": ("meta has no usable session_id" if meta is not None
                           else "meta is not valid JSON") + f": {path}",
            }))
            for orphan in (path, d / f"{stem}.turns.jsonl"):
                with contextlib.suppress(OSError):
                    orphan.unlink()
            continue
        metas.append(meta)
    return metas, discards


def write_spool_entry(
    root: Path,
    snapshot: Snapshot,
    bounds: Bounds = DEFAULT_BOUNDS,
) -> dict:
    """Write/extend the spool entry for one session snapshot.

    Appends only the NEW turns when the incoming list extends the stored log;
    rewrites fully when history changed (a MAX_TURNS window shift, a branch, a
    compaction) so the log can never silently diverge from the conversation.

    Returns ``{"written": bool, "bytes": int, "discards": [...]}``.
    """
    discards: list[dict] = []
    if not snapshot.turns:
        return {"written": False, "bytes": 0, "discards": discards}
    try:
        _entries_dir(root).mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        # A spool directory that cannot be created is a LOSS — record it.
        discards.append(record_discard(root, {
            "session_id": snapshot.session_id,
            "capture_key": capture_key(snapshot.session_id, snapshot.turns),
            "reason": DISCARD_WRITE_FAILED,
            "detail": str(exc),
        }))
        return {"written": False, "bytes": 0, "discards": discards}
    meta_path = _meta_path(root, snapshot.session_id)
    log_path = _log_path(root, snapshot.session_id)

    prior = read_spool_meta(root, snapshot.session_id)
    stored = read_spool_turns(root, snapshot.session_id) if prior else []
    new_digest = content_digest(snapshot.turns)

    # Dedup: an identical snapshot is a no-op (no rewrite, no re-upload).
    if prior and len(stored) == len(snapshot.turns) and prior.get("content_digest") == new_digest:
        return {"written": False, "bytes": _entry_bytes(root, snapshot.session_id), "discards": discards}

    extends = (
        len(stored) > 0
        # The log must end at a record boundary, or a torn append would fuse the
        # new turn into the garbage tail (#3963 review).
        and _log_has_complete_records(log_path)
        and len(snapshot.turns) > len(stored)
        and all(
            t.get("role") == snapshot.turns[i].get("role")
            and t.get("content") == snapshot.turns[i].get("content")
            for i, t in enumerate(stored)
        )
    )

    if extends:
        log_text = "".join(_serialise_turn(t) for t in snapshot.turns[len(stored):])
    else:
        log_text = "".join(_serialise_turn(t) for t in snapshot.turns)
    appended_bytes = len(log_text.encode("utf-8"))
    log_bytes_after = (_file_size(log_path) if extends else 0) + appended_bytes

    now = _now_iso()
    meta = {
        "version": 1,
        "session_id": snapshot.session_id,
        "harness": snapshot.harness,
        "source": snapshot.source,
        "machine_id": snapshot.machine_id,
        "created_at": (prior or {}).get("created_at", now),
        "updated_at": now,
        "turns_count": len(snapshot.turns),
        "content_digest": new_digest,
        "capture_key": capture_key(snapshot.session_id, snapshot.turns),
        # The backoff belongs to the ENTRY, not to one snapshot. A session that
        # keeps growing writes a new meta on EVERY turn; resetting these here
        # re-armed the retry window each time, so a deferred 402 was re-POSTed at
        # turn cadence with no backoff at all (#4714) — the "capped cadence"
        # this module promises held only for a STATIC entry. A new turn is not a
        # new upload attempt, so carry them forward. The filing path resets them
        # (attempts=0) and a genuinely fresh entry starts at zero.
        "attempts": _attempts(prior or {}),
        "next_attempt_at_ms": _carried_window(prior or {}),
    }
    if snapshot.model:
        meta["model"] = snapshot.model
    # A re-snapshot whose content is byte-identical to what was already filed
    # keeps the filing marker (the prior branch was unreachable above when the
    # digests matched and the lengths matched — retained for a window shift that
    # lands on identical content).
    if prior and prior.get("content_digest") == new_digest and prior.get("filed_key"):
        meta["filed_key"] = prior["filed_key"]
        meta["filed_at"] = prior.get("filed_at")

    meta_text = json.dumps(meta, ensure_ascii=False, indent=2) + "\n"
    entry_bytes = len(meta_text.encode("utf-8")) + log_bytes_after

    # Per-entry bound: an entry that can never legally be filed is discarded
    # with a reason rather than written and forgotten.
    if entry_bytes > bounds.max_entry_bytes:
        # `_discard_entry` records the reason BEFORE removing the files. Doing it
        # by hand here (unlink, then record) left the very SIGKILL / ENOSPC window
        # the ordering contract exists to close — and this is the one branch that
        # deletes a PREVIOUSLY SPOOLED entry, so it is the branch that matters.
        discards.append(_discard_entry(
            root,
            {"session_id": snapshot.session_id,
             "capture_key": meta["capture_key"]},
            DISCARD_ENTRY_TOO_LARGE,
            f"entry is {entry_bytes} bytes > max_entry_bytes={bounds.max_entry_bytes}",
        ))
        return {"written": False, "bytes": 0, "discards": discards}

    try:
        if extends:
            fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(log_text)
                fh.flush()
                os.fsync(fh.fileno())
        else:
            _atomic_write(log_path, log_text)
        _atomic_write(meta_path, meta_text)
    except OSError as exc:
        # A filesystem failure (full disk, read-only home) is a LOSS — record
        # it with a reason rather than returning an empty discard list.
        discards.append(record_discard(root, {
            "session_id": snapshot.session_id,
            "capture_key": meta["capture_key"],
            "reason": DISCARD_WRITE_FAILED,
            "detail": str(exc),
        }))
        return {"written": False, "bytes": 0, "discards": discards}

    discards.extend(prune_spool(root, snapshot.session_id, bounds))
    _sweep_stale_temp_files(root)
    return {"written": True, "bytes": _entry_bytes(root, snapshot.session_id), "discards": discards}


def _age_key(meta: dict) -> tuple[str, str, str]:
    """Deterministic oldest-first ordering.

    `updated_at` alone ties for entries written in the same clock tick, which
    made eviction depend on directory order. `session_id` is the final,
    deterministic tiebreak (arbitrary among true ties, but stable).
    """
    # `str(...)` coercion, not a raw tuple: a corrupt-typed `updated_at` (an int
    # from a hand-edited file) raised `TypeError: '<' not supported between
    # instances of 'int' and 'str'` and crashed `session spool`.
    return (
        str(meta.get("updated_at") or ""),
        str(meta.get("created_at") or ""),
        str(meta.get("session_id") or ""),
    )


def _backoff_ms(meta: dict) -> float:
    """`next_attempt_at_ms` as a finite number, however corrupt the stored value is.

    A non-numeric value ("soon", null, a nested dict) raised `ValueError` out of
    `flush_spool` and wedged the drain; 0 means "retry now", which is the safe
    reading.

    A NON-FINITE window is not a window: `inf > now_ms` is true forever, and
    because `write_spool_entry` now carries this field forward, an `inf` would
    have made a growing session permanently un-fileable — it reports "will
    retry" and never can. `float(10**400)` raises `OverflowError` rather than
    returning `inf`, so that is caught too: this helper runs on the WRITE path,
    where an escaping error would break `session spool`'s documented exit 0.
    """
    try:
        raw = meta.get("next_attempt_at_ms") or 0
    except AttributeError:  # a non-dict meta is #4906's class; stay total anyway
        return 0.0
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        # A REAL number only, mirroring the Pi leg's `clampWindow`
        # (`typeof value === "number"`). `float("1790000000000")` would honour a
        # numeric STRING the Pi leg zeroes — and since the two legs share ONE
        # spool dir, a future string window would make the Python drain HOLD an
        # entry the Pi drain retries, while `_carried_window` wrote the
        # string-derived float back to disk.
        return 0.0
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(value) or value <= 0:
        # `<= 0` as well as non-finite: the Pi leg's `clampWindow` returns 0 for
        # both, and the two legs share ONE spool directory. A negative window is
        # inert at every consumer (it is never `> now_ms`), but leaving it to be
        # written back would put a value on disk that only one leg can produce.
        return 0.0
    return value


def _carried_window(prior: dict) -> float:
    """The prior entry's backoff window, bounded to what the write path can produce.

    `_backoff_ms` makes it a finite number; this makes it a PLAUSIBLE one. A
    legitimately written window is at most ``written_at + RETRY_MAX_SECONDS``
    (``backoff_delay`` saturates there), so anything further out is corrupt — and
    because this value is CARRIED forward it would be re-written on every turn
    and strand the entry permanently, with every surface reporting nothing
    wrong.
    """
    window = _backoff_ms(prior)
    ceiling = time.time() * 1000.0 + RETRY_MAX_SECONDS * 1000
    return window if window <= ceiling else 0.0


def _attempts(meta: dict) -> int:
    """`attempts` as a small non-negative int, however corrupt the stored value is.

    Clamped, because `backoff_delay` computes `2 ** (attempts - 1)` from it and
    a stored `10**400` would try to materialise an astronomically large integer.
    The backoff is fully saturated long before this bound.
    """
    try:
        raw = meta.get("attempts") or 0
    except AttributeError:  # a non-dict meta is #4906's class; stay total anyway
        return 0
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        # A REAL number only, matching the Pi leg's `clampAttempts`
        # (`typeof value === "number"`). `int("5")` would accept a numeric
        # STRING and put the entry on attempt 5 — a 16-minute wait — where the
        # Pi leg computes attempt 1, and the two legs share one spool dir.
        # Booleans are ints in Python and are refused for the same reason: no
        # legitimate writer produces one, so it is corrupt input.
        return 0
    try:
        # Through a FLOAT, so the magnitude JS would have parsed to Infinity is
        # read the same way here. `10**400` is a 401-digit integer: Python holds
        # it exactly, but `JSON.parse` yields Infinity and the Pi leg's
        # `Number.isFinite` then yields attempt 0 (a 30 s wait), where a direct
        # `int(raw)` yielded attempt 64 (a SIX-HOUR wait) from the same bytes.
        # Both are retryable, so neither loses the capture — but the two legs
        # must not disagree about a cadence they share one directory for.
        as_float = float(raw)
    except (TypeError, ValueError, OverflowError):
        return 0
    if not math.isfinite(as_float):
        return 0
    return min(max(0, int(as_float)), _MAX_ATTEMPTS)


def prune_spool(root: Path, keep_session_id: str | None, bounds: Bounds = DEFAULT_BOUNDS) -> list[dict]:
    """Enforce COUNT and BYTE ceilings, evicting the OLDEST entries.

    Never evicts ``keep_session_id`` (the entry just written). One discard
    record per eviction, naming the ceiling.
    """
    discards: list[dict] = []
    metas, _ = list_spool_metas(root)
    ranked = sorted(
        (m for m in metas if m.get("session_id") != keep_session_id),
        key=_age_key,
    )
    count = len(metas)
    total = sum(_entry_bytes(root, m["session_id"]) for m in metas)
    for meta in ranked:
        if count <= bounds.max_entries and total <= bounds.max_total_bytes:
            break
        size = _entry_bytes(root, meta["session_id"])
        if total > bounds.max_total_bytes:
            reason = DISCARD_BYTES_EXCEEDED
            detail = f"spool is {total} bytes > max_total_bytes={bounds.max_total_bytes}"
        else:
            reason = DISCARD_COUNT_EXCEEDED
            detail = f"spool has {count} entries > max_entries={bounds.max_entries}"
        discards.append(_discard_entry(root, meta, reason, detail))
        count -= 1
        total -= size
    return discards


# ── Replay ─────────────────────────────────────────────────────────────────

PostFn = Callable[[dict], PostOutcome]


def flush_spool(
    root: Path,
    post: PostFn,
    *,
    now: float | None = None,
    only_session_id: str | None = None,
    exclude_session_id: str | None = None,
    bounds: Bounds = DEFAULT_BOUNDS,
) -> FlushSummary:
    """One replay opportunity.

    For every spooled session whose content has not been filed, attempt one
    POST. Transient failures back off; permanent 4xx are discarded with a
    reason; entries inside a backoff window are skipped (not lost).

    ``only_session_id`` files just that session; ``exclude_session_id`` drains
    OTHER sessions while a live one is still being captured.

    ``now`` is epoch MILLISECONDS (the same unit as the Pi leg — the two legs
    share one spool directory, so a seconds value written by one would read as
    "in backoff until the year 57000" to the other).
    """
    now_ms = time.time() * 1000.0 if now is None else now
    summary = FlushSummary()
    metas, discards = list_spool_metas(root)
    summary.discarded.extend(discards)
    for meta in sorted(metas, key=_age_key):
        sid = meta.get("session_id", "")
        try:
            _flush_one(root, meta, sid, summary, post, now_ms, only_session_id,
                       exclude_session_id, bounds)
        except Exception as exc:
            # A per-entry bug must never WEDGE the drain — one bad entry used to
            # make `flush_spool` raise for every later opportunity, forever.
            #
            # It must also not DELETE: the failure is UNCLASSIFIED (a bug, not a
            # permanent 4xx), so the entry is recorded in the ledger and left on
            # the spool with a backoff. Removing it here would turn an internal
            # bug into permanent data loss.
            summary.discarded.append(record_discard(root, {
                "session_id": sid or "unknown",
                "capture_key": meta.get("capture_key"),
                "reason": DISCARD_ENTRY_FAILED,
                "detail": f"unexpected failure while filing: {exc!r}",
            }))
            with contextlib.suppress(Exception):
                retry_meta = read_spool_meta(root, sid) or meta
                retry_meta["attempts"] = _attempts(meta) + 1
                retry_meta["next_attempt_at_ms"] = (
                    now_ms + backoff_delay(retry_meta["attempts"]) * 1000)
                _write_meta(root, retry_meta)
            summary.deferred += 1
    return summary


def _clear_breadcrumb_for(harness: str | None, session_id: str | None) -> None:
    """Drop the capture-failure breadcrumb once THAT session has landed.

    The record is per-HARNESS, so this must be narrow in three directions or it
    destroys evidence about something else (#4714 review):

    * ``kind`` — the shipped hooks write ``install-inert`` to the SAME path, and
      ``session verify`` reaches INERT only from that record. Unlinking blindly
      let a drain firing while verify was mid-flight erase it and report an
      inert install as PROVEN. Only a ``capture-failure`` record is cleared.
    * ``session_id`` — a failure recorded for a DIFFERENT session must survive.
      This is an IDENTITY check; a timestamp check does NOT work, because the
      spool's ``updated_at`` is frozen by the dedup path and so cannot say when
      this session last failed.
    * a record with NO recorded ``session_id`` holds no identity to match on —
      the shipped codex/cursor seams write that shape today (#4799) — so it is
      KEPT. A stale breadcrumb is a cosmetic wart; wrongly erasing one loses
      evidence of a session that may still be lost.

    Best effort throughout: a breadcrumb is evidence, never a gate on filing.
    """
    if not harness:
        return
    try:
        import json

        from tortoise.hook_install import KIND_CAPTURE_FAILURE, local_state_dir

        # The WRITER's derivation, not a second one: under an empty or absent
        # override both resolve under ``$HOME``, so a breadcrumb the writer
        # placed is the one this clears (``local_state_dir`` owns the rule).
        path = local_state_dir("capture-errors") / f"{harness}.json"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if record.get("kind") != KIND_CAPTURE_FAILURE:
            return
        recorded_id = record.get("session_id")
        if recorded_id and session_id and recorded_id != session_id:
            # A DIFFERENT session's failure — still current, must survive.
            return
        if not recorded_id:
            # The shipped codex/cursor hooks write a `capture-failure` record
            # WITHOUT a session id, and are not a legacy shape — so treating it
            # as clearable lets ANY filing erase a still-current failure for a
            # different session, which is what this function promises not to do.
            # Their records also hold no identity to match on, so the safe
            # direction is to leave them: a stale breadcrumb is a cosmetic
            # wart, a wrongly-erased one is lost evidence.
            return
        with contextlib.suppress(OSError):
            path.unlink()
    except Exception:
        # Never let breadcrumb housekeeping affect a successful filing.
        return


def _flush_one(root: Path, meta: dict, sid: str, summary: FlushSummary, post: PostFn,
               now_ms: float, only_session_id: str | None,
               exclude_session_id: str | None, bounds: Bounds) -> None:
    """Attempt ONE spool entry. Extracted so `flush_spool` can guard every entry."""
    if only_session_id and sid != only_session_id:
        return
    if exclude_session_id and sid == exclude_session_id:
        summary.held_back += 1
        return
    # STRUCTURAL probe guard, not a timing one — but ONLY for an unfiltered
    # flush. `session verify` fires the real seam, and the seam runs
    # `session capture`, which files through THIS function: refusing there would
    # stop the probe landing at all, so `captured` would read FAIL for every
    # harness and verify could never prove the chain it exists to prove (a real
    # regression, caught in CI, not by review).
    #
    # The leak this guards is a probe LINGERING — a detached codex/cursor worker
    # spooling one after verify's cleanup has run — and the next thing that
    # would file it is an automatic `session drain`. So: refuse only when the
    # caller named no session (`only_session_id is None` == a drain), and always
    # allow a targeted filing, where the caller knows which session it means.
    # The refusal is non-destructive: verify's own cleanup removes what it made.
    if only_session_id is None and is_probe_session_id(sid):
        summary.held_back += 1
        summary.probe_refusals.append({
            "session_id": sid,
            "detail": "session verify probe — synthetic content is never filed "
                      "by a drain",
        })
        return
    if meta.get("filed_key") and meta.get("filed_key") == meta.get("capture_key"):
        summary.skipped += 1
        return
    window = _backoff_ms(meta)
    if window > now_ms + RETRY_MAX_SECONDS * 1000:
        # No legitimately-written window is further out than now + RETRY_MAX: the
        # write path can only produce `written_at + backoff_delay(n)`, and
        # `backoff_delay` saturates at RETRY_MAX. A value beyond that is corrupt,
        # and the safe reading of an unusable window is "retry now" — honouring
        # it would strand the entry indefinitely while every surface stayed
        # silent, the same failure the non-finite guard closes.
        window = 0.0
    if window > now_ms:
        summary.held_by_backoff += 1
        summary.skipped += 1
        return
    turns = read_spool_turns(root, sid)
    if not turns:
        summary.discarded.append(
            _discard_entry(root, meta, DISCARD_TRANSCRIPT_EMPTY, "turn log is empty or unreadable")
        )
        return
    # The digest of what will actually be POSTed. The CAS below compares the
    # disk against THIS, never against the stale pre-POST meta: a writer that
    # appended to the log and then died before its meta write leaves the meta
    # digest unchanged, and comparing meta-to-meta would stamp `filed_key`
    # over a turn that was never posted.
    posted_digest = content_digest(turns)
    summary.attempted += 1
    payload = {
        "harness": meta.get("harness", "claude"),
        "session_id": sid,
        "source": meta.get("source"),
        "conversation": turns,
        "machine_id": meta.get("machine_id"),
    }
    if meta.get("model"):
        payload["model"] = meta["model"]
    outcome = post(payload)
    summary.outcomes[sid] = outcome
    if outcome.ok:
        # The deferred session HAS now landed, so the machine-local "this
        # harness lost its last capture" breadcrumb is no longer true. It was
        # only ever cleared on a 2xx inside `sessions import`, which a
        # spooled-then-drained session never takes — so a recovered capture
        # left the breadcrumb standing (and `session verify` reading a failure
        # that had already been resolved) (#4714 review).
        _clear_breadcrumb_for(meta.get("harness"), sid)
        # COMPARE-AND-SWAP, on the POSTED CONTENT. A concurrent capture (a
        # resumed session, or the SessionStart drain racing a live turn) can
        # grow this entry while the POST is in flight. Stamp `filed_key` only
        # when what is on disk NOW is exactly what was posted; otherwise the
        # entry stays unfiled and the next opportunity re-posts the longer
        # conversation. Comparing meta-to-meta was wrong: a writer killed
        # between its log append and its meta write leaves the meta digest
        # stale, so the check passed while a turn went unfiled forever.
        on_disk_turns = read_spool_turns(root, sid)
        on_disk = read_spool_meta(root, sid)
        if (on_disk is not None
                and content_digest(on_disk_turns) == posted_digest):
            on_disk["filed_key"] = on_disk.get("capture_key") or capture_key(sid, on_disk_turns)
            on_disk["filed_at"] = datetime.fromtimestamp(
                now_ms / 1000.0, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            on_disk["attempts"] = 0
            on_disk["next_attempt_at_ms"] = 0
            _write_meta(root, on_disk)
        # else: the posted content WAS filed; the entry keeps the newer turns
        # and stays unfiled, so they are re-posted next time.
        summary.filed += 1
        return
    if classify_failure(outcome.status, outcome.detail) == "permanent":
        summary.discarded.append(_discard_entry(
            root, meta, f"permanent_http_{outcome.status if outcome.status is not None else 'none'}",
            outcome.detail or "permanent client error",
        ))
        return
    # Re-read before the backoff write-back for the same reason as the CAS
    # above: never clobber newer turns written while the POST was in flight.
    pending = read_spool_meta(root, sid) or meta
    if pending.get("filed_key") and pending.get("filed_key") == pending.get("capture_key"):
        # A CONCURRENT flush already filed this exact content while our POST was
        # in flight. Re-arming the backoff here would attach a window to content
        # that was never refused — and since `write_spool_entry` now CARRIES the
        # window, the next turn's NEW content would inherit it and wait up to
        # RETRY_MAX (6 h) with no attempt behind it (#4714 cycle-7 review).
        summary.skipped += 1
        return
    pending["attempts"] = _attempts(meta) + 1
    pending["next_attempt_at_ms"] = now_ms + backoff_delay(pending["attempts"]) * 1000
    _write_meta(root, pending)
    summary.deferred += 1
    return


def _write_meta(root: Path, meta: dict) -> None:
    with contextlib.suppress(OSError):
        _atomic_write(_meta_path(root, meta["session_id"]),
                      json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
