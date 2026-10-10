"""Audit event logger — three-tier persistence for control-plane operations.

Tier 1: Postgres INSERT via psycopg2 (sync, optional)
Tier 2: Local JSONL fallback file ($TORTOISE_AUDIT_FALLBACK_DIR, else
        ~/.tortoise/audit_fallback.jsonl) — resolved lazily, at write time
Tier 3: Replay — on next successful connection, replay fallback into Postgres

Postgres is optional. When TORTOISE_AUDIT_DSN is unset or psycopg2 is not
installed, audit operates in JSONL-only mode (Tier 2).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

_logger = logging.getLogger(__name__)

# Lazy import — psycopg2 is an optional dependency
try:
    import psycopg2
    import psycopg2.extras
    _HAS_PSYCOPG2 = True
except ImportError:
    psycopg2 = None  # type: ignore
    _HAS_PSYCOPG2 = False

# Post-flip guard (#669): if TORTOISE_AUDIT_DSN is configured but psycopg2 is
# NOT installed, the logger would silently operate in JSONL-only mode — the
# exact failure seen when the audit DSN came online and the hosted image
# lacked the postgres extra. Be LOUD at import so a misconfiguration cannot
# hide (the fallback still works; this is a boot-time warning, not a crash).
if os.environ.get("TORTOISE_AUDIT_DSN") and not _HAS_PSYCOPG2:
    _logger.warning(
        "TORTOISE_AUDIT_DSN is set but psycopg2 is not installed — audit "
        "will fall back to JSONL. Install the 'postgres' extra "
        "(pip install -e '.[postgres]')."
    )


# #7924 review P2: a fallback event that reached NO durable location
# used to be indistinguishable in-process from a persisted one (the ERROR log
# was the only evidence). Count the DROPs by cause in the shared monitoring
# substrate, so the loss is a COUNTER and not only a log line — the same shape
# as that substrate's journal-write-failure counter
# (monitoring.JOURNAL_WRITE_FAILURE_COUNT, #4240: "a log line nothing
# watches"), which is carried by ``monitoring.metrics()`` as
# ``journal_write_failures``; this family likewise rides it as
# ``audit_fallback_drops``.
#
# SCOPE, stated honestly (#7924 review round 3): ``metrics()`` reaches an
# operator through ``monitoring.serve_health`` and the MCP ``tortoise_health``
# tool. It is NOT the deployed hosted ``/health`` (whose handler returns its own
# dict) nor the ``/healthz`` listener — neither carries ``audit_fallback_drops``
# — so on the hosted app a drop is COUNTED here without being surfaced by
# either endpoint. That gap is the reason this comment does not claim otherwise.
# Still NON-FATAL by design: audit failure must never break the serving flow
# (see hosted_api._audit_auth_failure), so neither may this counter.
def _note_fallback_drop(reason: str) -> None:
    """Count a dropped audit event; NEVER raises (#3820 counter doctrine).

    Read it back with ``tortoise.monitoring.audit_fallback_drop_counts()`` or
    ``tortoise.monitoring.metrics()["audit_fallback_drops"]``.
    """
    try:
        from tortoise.monitoring import record_audit_fallback_drop
        record_audit_fallback_drop(reason)
    except Exception as e:  # pragma: no cover - defensive
        _logger.warning("AuditLogger: drop counter failed (%s): %s", reason, e)


def _warned_whitespace_override() -> bool:
    """Return True if the whitespace-only-knob warning was ALREADY emitted.

    #7924 review P2: ``_fallback_file()`` runs on the audit hot path,
    so an unconditional warning would emit one line per audit event for the
    whole duration of a Postgres outage. Warn once per process, mirroring
    ``pack_registry``'s warn-once treatment of ``TORTOISE_PACKS_DIR``.
    """
    global _WHITESPACE_OVERRIDE_WARNED
    if _WHITESPACE_OVERRIDE_WARNED:
        return True
    _WHITESPACE_OVERRIDE_WARNED = True
    return False


_WHITESPACE_OVERRIDE_WARNED = False


def _warned_replay_resolution_failure() -> bool:
    """Return True if the read-leg resolution failure was ALREADY reported.

    #7924 review round 3: ``_replay_fallback()`` runs on every SUCCESSFUL
    Postgres write (``append``'s else-branch), so an unconditional ERROR
    there emits one line per audit event whenever the fallback path cannot be
    resolved — even though every one of those events WAS durably persisted,
    which makes the message read as a loss that did not happen. Warn once per
    process, exactly as the whitespace-only-knob warning above does, so a
    malformed ``$HOME`` cannot flood the log from the healthy path.
    """
    global _REPLAY_RESOLUTION_WARNED
    if _REPLAY_RESOLUTION_WARNED:
        return True
    _REPLAY_RESOLUTION_WARNED = True
    return False


_REPLAY_RESOLUTION_WARNED = False


def _refuse_unusable_home() -> None:
    """Refuse a home directory that cannot anchor the fallback.

    Three shapes reach the SAME root artefact (``/.tortoise/audit_fallback.jsonl``),
    where a root-writable container (the hosted shape) lets the mkdir+append
    SUCCEED — so no drop is counted and the loss is silent:

    * SET-but-EMPTY ``$HOME`` (``""``): ``Path.home()`` returns ``/``, which IS
      absolute, so the absolute-path check cannot see it.

    * A ``$HOME`` whose RESOLVED location IS the filesystem root — ``"/"``,
      ``"/.."``, ``"//"``, ``"/tmp/../.."``, or a symlink to ``/``. Keying the
      refusal on the literal string MISSED these (#7924 review round 4): they
      are non-empty, so the empty check passed, and the resulting
      ``$HOME/.tortoise`` is not itself the root, so the artifact-location
      guard could not see it either.

    * An UNSET ``$HOME`` whose PASSWORD-DATABASE entry is the root (#7924
      review round 5): ``Path.home()`` then returns ``/`` from ``pwd``, so
      treating "unset" as "fine" reopened the hole for a minimal container.
      The pwd-derived home is resolved and checked here too.

    A fourth shape, SET-but-WHITESPACE ``$HOME`` (``"   "``), is a DIFFERENT
    failure mode rather than a root escape: ``Path.home()`` is then the
    RELATIVE ``PosixPath('   ')``, which the absolute-path check already
    refuses (and which would otherwise mkdir a CWD-relative directory). The
    ``home.strip()`` gate below refuses it here first, with the same message,
    so that hazard is reported as the misconfiguration it is.
    """
    home = os.environ.get("HOME")
    if home is None:
        # `$HOME` absent: `Path.home()` falls back to the PASSWD entry, and
        # that entry can itself be the root. Resolve and check THAT — "unset"
        # is not a licence to skip the invariant. (`Path.home()` may itself
        # raise when there is no passwd entry; that propagates and the caller
        # counts it as a drop, the same outcome as any unusable home.)
        resolved_home = Path(os.path.realpath(Path.home()))
        if resolved_home == Path(resolved_home.anchor):
            raise RuntimeError(
                "the passwd-derived home resolves to the filesystem root "
                f"({resolved_home!r}) with $HOME unset — refusing to write the "
                "audit fallback at the root; set HOME or "
                "TORTOISE_AUDIT_FALLBACK_DIR to an absolute path")
        return
    if not home.strip():
        raise RuntimeError(
            "$HOME is set but empty/whitespace — refusing to "
            "resolve the audit fallback against the filesystem "
            "root; set TORTOISE_AUDIT_FALLBACK_DIR to an absolute "
            "path or fix $HOME")
    resolved_home = Path(os.path.realpath(home))
    if resolved_home == Path(resolved_home.anchor):
        raise RuntimeError(
            f"$HOME resolves to the filesystem root ({home!r} → "
            f"{resolved_home!r}) — refusing to write the audit fallback at "
            "the root; set TORTOISE_AUDIT_FALLBACK_DIR to an absolute path "
            "or fix $HOME")


def _refuse_a_root_location(path: Path) -> None:
    """Refuse a fallback artefact whose resolved DIRECTORY is the root.

    A LEXICAL comparison is not enough (#7924 review round 3):
    ``Path('/..') != Path('/')``, yet the kernel resolves both to the
    filesystem root, so a check that compared the two strings saw a NON-root
    path while ``mkdir``/``open`` wrote ``/audit_fallback.jsonl`` — the
    round-2 silent-loss shape, one spelling over. ``os.path.realpath``
    collapses ``..`` AND symlinks, which is where the write actually lands,
    and it does not require the leaf to exist.
    """
    resolved_dir = Path(os.path.realpath(path.parent))
    if resolved_dir == Path(resolved_dir.anchor):
        raise RuntimeError(
            "audit fallback directory resolves to the filesystem root: "
            f"{resolved_dir!r} — refusing (the fallback must live under $HOME "
            "or an explicit absolute directory)")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    actor_user_id TEXT,
    operation TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    ip_address TEXT,
    user_agent TEXT,
    detail JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""


class AuditLogger:
    """Append-only audit event logger with optional Postgres backend.

    Args:
        dsn: Postgres connection string. If None, reads TORTOISE_AUDIT_DSN
             from environment. If that is also unset, operates in JSONL-only
             mode (no Postgres writes, no replay).

    Thread-safe for single-process use. JSONL fallback uses a per-process
    file (``$HOME/.tortoise/audit_fallback.jsonl``, relocatable with
    ``$TORTOISE_AUDIT_FALLBACK_DIR``).
    """

    def __init__(self, dsn: str | None = None):
        self._dsn = dsn or os.environ.get("TORTOISE_AUDIT_DSN")
        self._conn = None
        # #7816: the fallback location is resolved LAZILY, at write/replay
        # time, NEVER here. `hosted_api` builds a module-level AuditLogger at
        # import, so binding ``Path.home()`` (and mkdir-ing it) in __init__
        # made an IMPORT write into the developer's real ``~/.tortoise`` —
        # before any per-test HOME fixture — and froze that path, so a later
        # test's fallback append still landed there. ``_fallback_dir`` /
        # ``_fallback_path`` remain settable OVERRIDES (tests pin them); when
        # unset, ``_fallback_file()`` reads the environment in effect at CALL
        # time.
        self._fallback_dir: Path | None = None
        self._fallback_path: Path | None = None
        self._replay_lock = threading.Lock()
        self._replay_backoff = 1.0

    def _fallback_file(self) -> Path:
        """Resolve the JSONL fallback path for the CURRENT environment.

        #7816: resolution is deferred to call time so the path honours the
        ``$HOME`` in effect when the fallback is actually used (e.g. a
        per-test tmp tree) instead of the ``$HOME`` captured at import.
        Precedence:

        1. an explicit ``_fallback_path`` (tests pin this),
        2. an explicit ``_fallback_dir`` (tests pin this),
        3. ``$TORTOISE_AUDIT_FALLBACK_DIR`` (a relocation knob),
        4. ``$HOME/.tortoise`` (the documented default).

        A whitespace-only override is treated as UNSET (#7924 review P2):
        ``Path(" ")`` would otherwise be a RELATIVE path, so ``_write_fallback``
        would mkdir a literal ``" "`` directory in the CWD and drop audit
        events there. The peer knobs (``TORTOISE_PACKS_DIR``,
        ``TORTOISE_DB_PATH``) normalize the same way.

        ``~`` is expanded before the absolute check, exactly as those peer
        knobs do (``tortoise/pack_registry.py``, ``tortoise/config.py``): a
        literal ``~/.audit`` is otherwise a RELATIVE path and would raise
        below, dropping EVERY fallback event for the whole duration of a
        Postgres outage — the opposite of what a relocation knob is for
        (#7924 review P1/P2).

        A set-but-empty/whitespace ``$HOME`` — or one that RESOLVES to the
        filesystem root (``"/"``, ``"/.."``, ``"//"``, a symlink) — is refused
        UP FRONT (#7924 review rounds 2 and 4): for ``HOME=""``
        ``Path.home()`` returns ``/`` — which IS absolute — so the absolute
        check below would PASS and the fallback would resolve to
        ``/.tortoise/audit_fallback.jsonl``, i.e. OUTSIDE ``$HOME``, in the
        filesystem root. In a root-writable container (the hosted shape)
        ``_write_fallback`` then mkdirs ``/.tortoise`` and the append SUCCEEDS,
        so the drop counter is never incremented and the loss is silent.
        ``HOME="   "`` and ``HOME="/.."`` are the same misconfiguration one
        spelling over. All are refused here — counted as an
        ``unresolvable_path`` drop, never as ``write_failed`` — while an UNSET
        ``$HOME`` keeps the documented ``Path.home()`` fallback. The refusal
        gates EVERY leg that consults ``$HOME``, the ``~``-override leg
        included (#7924 review round 3): ``expanduser()`` reads ``$HOME`` for a
        bare leading ``~``, so ``HOME=""`` +
        ``TORTOISE_AUDIT_FALLBACK_DIR='~/.audit'`` reached the same root
        through the knob.

        The RESOLVED path is then required to be ABSOLUTE (#7924 review
        P2) as defense in depth: the two override legs are held to the same
        invariant — returning them unchecked would make a post-construction
        relative ``_fallback_dir`` (inert in the eager version, where only
        ``_fallback_path`` was read) the winning path and mkdir it in the CWD.
        A resolved base that IS the filesystem root is refused outright
        (#7924 review round 3), so no leg can reach ``/audit_fallback.jsonl``.
        The check RESOLVES the directory (``os.path.realpath``), because a
        lexical comparison is defeated by ``..``: ``Path('/..')`` is not equal
        to ``Path('/')`` yet the kernel resolves both to the root.

        Raises whatever ``Path.home()`` raises (``RuntimeError`` for a
        malformed ``$HOME``), and ``RuntimeError`` for a path that is not
        absolute; callers that must not raise wrap the call.
        """
        if self._fallback_path is not None:
            path = self._fallback_path
            if not path.is_absolute():
                raise RuntimeError(
                    f"audit fallback path is not absolute: {path!r}")
            _refuse_a_root_location(path)
            return path
        if self._fallback_dir is not None:
            base = self._fallback_dir
        else:
            override = (os.environ.get("TORTOISE_AUDIT_FALLBACK_DIR") or "").strip()
            if (not override
                    and os.environ.get("TORTOISE_AUDIT_FALLBACK_DIR")
                    and not _warned_whitespace_override()):
                _logger.warning(
                    "AuditLogger: TORTOISE_AUDIT_FALLBACK_DIR is whitespace-only — "
                    "treating it as unset and using the $HOME default")
            if override:
                if override == "~" or override.startswith("~/"):
                    # #7924 review round 3: a BARE leading `~` is expanded from
                    # `$HOME` by `expanduser()`, so with `HOME=""` it expands
                    # to `/` — the same root escape the default leg refuses,
                    # reached through the relocation knob. (`~user` expands
                    # from the password database and never consults `$HOME`,
                    # so it is deliberately left alone.)
                    _refuse_unusable_home()
                base = Path(override).expanduser()
            else:
                _refuse_unusable_home()
                base = Path.home() / ".tortoise"
        if not base.is_absolute():
            raise RuntimeError(
                f"audit fallback base is not an absolute path: {base!r} "
                "(set TORTOISE_AUDIT_FALLBACK_DIR to an absolute path, or fix "
                "$HOME)")
        # The artifact-location guard (#7924 review round 3): `base ==
        # Path(base.anchor)` is a LEXICAL comparison, and `Path('/..')` is not
        # equal to `Path('/')` even though the kernel resolves both to the
        # root — so `TORTOISE_AUDIT_FALLBACK_DIR=/..` used to write
        # `/audit_fallback.jsonl` while the guard saw a non-root path.
        #
        # This IS load-bearing on the override and explicit-path legs, and it
        # is the SECOND line of defence on the $HOME legs: it fires when
        # `<home>/.tortoise` itself resolves to the root (e.g. that path is a
        # symlink to `/`). The ordinary root-resolving $HOME spellings are
        # refused earlier, by `_refuse_unusable_home()`.
        _refuse_a_root_location(base / "audit_fallback.jsonl")
        return base / "audit_fallback.jsonl"

    # ── Public API ──────────────────────────────────────────────────

    def append(
        self,
        org_id: str,
        actor_user_id: str | None,
        operation: str,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        detail: dict | None = None,
    ) -> None:
        """Append an audit event.

        ``detail`` is a free-form JSONB payload (20260813000004 added the
        column; org_claim stores provider/email/user_id — 0002 has no
        provider/email columns).

        Tries Postgres first. On failure, writes to JSONL fallback.
        On any successful Postgres write, replays accumulated fallback entries.
        """
        from tortoise.ids import ulid

        event = {
            "id": ulid(),
            "org_id": org_id,
            "actor_user_id": actor_user_id,
            "operation": operation,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "detail": json.dumps(detail) if detail is not None else None,
            "created_at": _now_iso(),
        }

        pg_ok = self._try_pg_insert(event)
        if not pg_ok:
            self._write_fallback(event)
        else:
            # On successful Postgres write, attempt replay of any
            # accumulated fallback entries from prior failures.
            self._replay_fallback()

    def close(self) -> None:
        """Close the Postgres connection if open."""
        if self._conn is not None:
            try:  # noqa: SIM105
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    # ── Internals ───────────────────────────────────────────────────

    def _connect(self) -> bool:
        """Establish (or re-establish) Postgres connection.

        Returns True on success, False on failure.
        """
        if not self._dsn:
            return False
        if not self._dsn.startswith(("postgresql://", "postgres://")):
            # #2903 (same leak class as #2796): a malformed DSN is still secret
            # material — this error is rendered into logs and telemetry (the
            # unhandled-exception handler, the purge sweep's exc_info warning),
            # and a raw prefix can carry a password head. Report a
            # non-reversible 8-hex identity instead (secret_store contract:
            # fingerprints are the ONLY key identity that may reach logs).
            got = hashlib.sha256(self._dsn.strip().encode()).hexdigest()[:8]
            raise ValueError(
                "TORTOISE_AUDIT_DSN must start with postgresql:// or "
                f"postgres:// (got <{got}>...)"
            )
        if not _HAS_PSYCOPG2:
            _logger.debug("psycopg2 not installed — audit in JSONL-only mode")
            return False

        try:
            if self._conn is not None:
                try:  # noqa: SIM105
                    self._conn.close()
                except Exception:
                    pass
            self._conn = psycopg2.connect(self._dsn)
            self._conn.autocommit = True
            self._ensure_schema()
            self._replay_backoff = 1.0  # reset backoff on success
            return True
        except Exception as e:
            _logger.warning("AuditLogger: Postgres connection failed: %s", e)
            self._conn = None
            return False

    def _ensure_schema(self) -> None:
        """Create audit_events table if it doesn't exist."""
        if self._conn is None:
            return
        try:
            with self._conn.cursor() as cur:
                cur.execute(_SCHEMA_DDL)
        except Exception as e:
            _logger.warning("AuditLogger: schema creation failed: %s", e)

    def _try_pg_insert(self, event: dict) -> bool:
        """Attempt Postgres INSERT. Returns True on success."""
        if self._conn is None:  # noqa: SIM102
            if not self._connect():
                return False

        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO audit_events
                       (id, org_id, actor_user_id, operation,
                        resource_type, resource_id, ip_address,
                        user_agent, detail, created_at)
                       VALUES (%(id)s, %(org_id)s, %(actor_user_id)s,
                               %(operation)s, %(resource_type)s,
                               %(resource_id)s, %(ip_address)s,
                               %(user_agent)s, %(detail)s::jsonb, %(created_at)s)
                       ON CONFLICT (id) DO NOTHING""",
                    event,
                )
            return True
        except Exception as e:
            _logger.warning("AuditLogger: Postgres INSERT failed: %s", e)
            # Connection may be dead — reset for reconnect on next append
            try:  # noqa: SIM105
                self._conn.close()
            except Exception:
                pass
            self._conn = None
            # Exponential backoff before allowing reconnect
            time.sleep(min(self._replay_backoff, 30))
            self._replay_backoff = min(self._replay_backoff * 2, 30)
            return False

    def _write_fallback(self, event: dict) -> None:
        """Append event to local JSONL fallback file.

        Resolves the path OUTSIDE the best-effort write guard (#7924 review
        P2): a location we cannot RESOLVE is a DROP, not a write error, and it
        is reported and COUNTED as one — never conflated with a successful
        write. Ensures the parent directory exists so a reassigned fallback
        path (or a deleted dir) never silently drops audit events.

        Never raises — audit failure must not break the serving flow; a drop is
        surfaced through the ERROR log and the monitoring counter instead.
        """
        try:
            path = self._fallback_file()
        except Exception as e:
            _note_fallback_drop("unresolvable_path")
            _logger.error(
                "AuditLogger: fallback path unresolvable — audit event "
                "DROPPED (not persisted anywhere): %s", e)
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a") as f:
                f.write(json.dumps(event, default=str) + "\n")
        except Exception as e:
            _note_fallback_drop("write_failed")
            _logger.error(
                "AuditLogger: fallback write failed — audit event DROPPED "
                "(not persisted anywhere): %s", e)

    def _replay_fallback(self) -> None:
        """Replay accumulated fallback entries into Postgres.

        Uses a lock to prevent concurrent replay. Reads the fallback file,
        replays each line, and truncates on success.
        """
        try:
            path = self._fallback_file()
        except Exception as e:
            # #7924 review P2 — this deliberately does NOT count.
            # The counter's contract is per EVENT that reached no durable sink
            # (``monitoring.record_audit_fallback_drop``); a resolution failure
            # on the READ leg does not establish that. With Postgres healthy
            # there may be nothing stranded at all, so counting here reports
            # phantom drops on a fully-persisted path. The failure is still
            # surfaced loudly — an ERROR is the right signal for "could not
            # look", and the write leg still counts the drops that ARE events.
            #
            # #7924 review round 3 — and it is WARN-ONCE: this method runs on
            # every SUCCESSFUL Postgres write, so an unconditional ERROR here
            # emitted one line per audit event while `$HOME` was malformed,
            # reading as a loss even though every one of those events was
            # durably persisted. The loss signal is the drop counter on the
            # WRITE leg; this message only says "could not look".
            if not _warned_replay_resolution_failure():
                _logger.error(
                    "AuditLogger: could not inspect the audit fallback for "
                    "replay (%s) — no fallback was read this call; nothing is "
                    "reported lost (the write leg counts drops)", e)
            return
        if not path.exists():
            return

        acquired = self._replay_lock.acquire(blocking=False)
        if not acquired:
            return  # Another thread is replaying

        try:
            if not path.exists():
                return

            # Read all fallback lines
            lines = path.read_text().strip().split("\n")
            if not lines or lines == [""]:
                return

            events = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    _logger.warning("AuditLogger: corrupt fallback line, skipping")

            if not events:
                return

            # Re-ensure connection before replay
            if self._conn is None and not self._connect():
                return

            # Insert all events — IDEMPOTENT (post-flip fix, #669): ON CONFLICT
            # (id) DO NOTHING means already-replayed events are SKIPPED, not
            # re-queued. Without this, a replay after a partial success (or a
            # restart where some rows landed) re-appended the same events to
            # the fallback forever — the duplicate-key flood seen when
            # TORTOISE_AUDIT_DSN came online.
            failed: list[dict] = []
            for event in events:
                try:
                    with self._conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO audit_events
                               (id, org_id, actor_user_id, operation,
                                resource_type, resource_id, ip_address,
                                user_agent, created_at)
                               VALUES (%(id)s, %(org_id)s, %(actor_user_id)s,
                                       %(operation)s, %(resource_type)s,
                                       %(resource_id)s, %(ip_address)s,
                                       %(user_agent)s, %(created_at)s)
                               ON CONFLICT (id) DO NOTHING""",
                            event,
                        )
                except Exception as e:
                    _logger.warning("AuditLogger: replay insert failed: %s", e)
                    failed.append(event)

            # Rewrite the fallback with ONLY the genuinely-failed events
            # (review P2, PR #919 — the old contiguous-tail truncation could
            # drop a mid-list failure when a later event succeeded).
            if not failed:
                path.write_text("")
            else:
                path.write_text(
                    "\n".join(json.dumps(e, default=str) for e in failed) + "\n"
                )
        except Exception as e:
            _logger.error("AuditLogger: replay failed: %s", e)
        finally:
            self._replay_lock.release()
