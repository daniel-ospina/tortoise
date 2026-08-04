"""Audit event logger — three-tier persistence for control-plane operations.

Tier 1: Postgres INSERT via psycopg2 (sync, optional)
Tier 2: Local JSONL fallback file (~/.tortoise/audit_fallback.jsonl)
Tier 3: Replay — on next successful connection, replay fallback into Postgres

Postgres is optional. When TORTOISE_AUDIT_DSN is unset or psycopg2 is not
installed, audit operates in JSONL-only mode (Tier 2).
"""
from __future__ import annotations

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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    actor_user_id TEXT,
    operation TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    ip_address TEXT,
    user_agent TEXT,
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
    file (~/.tortoise/audit_fallback.jsonl).
    """

    def __init__(self, dsn: str | None = None):
        self._dsn = dsn or os.environ.get("TORTOISE_AUDIT_DSN")
        self._conn = None
        self._fallback_dir = Path.home() / ".tortoise"
        self._fallback_dir.mkdir(parents=True, exist_ok=True)
        self._fallback_path = self._fallback_dir / "audit_fallback.jsonl"
        self._replay_lock = threading.Lock()
        self._replay_backoff = 1.0

    # ── Public API ──────────────────────────────────────────────────

    def append(
        self,
        team_id: str,
        actor_user_id: str | None,
        operation: str,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Append an audit event.

        Tries Postgres first. On failure, writes to JSONL fallback.
        On any successful Postgres write, replays accumulated fallback entries.
        """
        from tortoise.ids import ulid

        event = {
            "id": ulid(),
            "team_id": team_id,
            "actor_user_id": actor_user_id,
            "operation": operation,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "ip_address": ip_address,
            "user_agent": user_agent,
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
            try:
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
            raise ValueError(
                f"TORTOISE_AUDIT_DSN must start with postgresql:// or postgres://, got: {self._dsn[:20]}..."
            )
        if not _HAS_PSYCOPG2:
            _logger.debug("psycopg2 not installed — audit in JSONL-only mode")
            return False

        try:
            if self._conn is not None:
                try:
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
        if self._conn is None:
            if not self._connect():
                return False

        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO audit_events
                       (id, team_id, actor_user_id, operation,
                        resource_type, resource_id, ip_address,
                        user_agent, created_at)
                       VALUES (%(id)s, %(team_id)s, %(actor_user_id)s,
                               %(operation)s, %(resource_type)s,
                               %(resource_id)s, %(ip_address)s,
                               %(user_agent)s, %(created_at)s)""",
                    event,
                )
            return True
        except Exception as e:
            _logger.warning("AuditLogger: Postgres INSERT failed: %s", e)
            # Connection may be dead — reset for reconnect on next append
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
            # Exponential backoff before allowing reconnect
            time.sleep(min(self._replay_backoff, 30))
            self._replay_backoff = min(self._replay_backoff * 2, 30)
            return False

    def _write_fallback(self, event: dict) -> None:
        """Append event to local JSONL fallback file."""
        try:
            with open(self._fallback_path, "a") as f:
                f.write(json.dumps(event, default=str) + "\n")
        except Exception as e:
            _logger.error("AuditLogger: fallback write failed: %s", e)

    def _replay_fallback(self) -> None:
        """Replay accumulated fallback entries into Postgres.

        Uses a lock to prevent concurrent replay. Reads the fallback file,
        replays each line, and truncates on success.
        """
        if not self._fallback_path.exists():
            return

        acquired = self._replay_lock.acquire(blocking=False)
        if not acquired:
            return  # Another thread is replaying

        try:
            if not self._fallback_path.exists():
                return

            # Read all fallback lines
            lines = self._fallback_path.read_text().strip().split("\n")
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

            # Insert all events
            success_count = 0
            for event in events:
                try:
                    with self._conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO audit_events
                               (id, team_id, actor_user_id, operation,
                                resource_type, resource_id, ip_address,
                                user_agent, created_at)
                               VALUES (%(id)s, %(team_id)s, %(actor_user_id)s,
                                       %(operation)s, %(resource_type)s,
                                       %(resource_id)s, %(ip_address)s,
                                       %(user_agent)s, %(created_at)s)""",
                            event,
                        )
                    success_count += 1
                except Exception as e:
                    _logger.warning("AuditLogger: replay insert failed: %s", e)
                    # Write failed events back to fallback
                    with open(self._fallback_path, "a") as f:
                        f.write(json.dumps(event, default=str) + "\n")

            # Truncate fallback file on full success
            if success_count == len(events):
                self._fallback_path.write_text("")
            elif success_count > 0:
                # Partial success — only keep failed events
                remaining = events[success_count:]
                self._fallback_path.write_text(
                    "\n".join(json.dumps(e, default=str) for e in remaining) + "\n"
                )
        except Exception as e:
            _logger.error("AuditLogger: replay failed: %s", e)
        finally:
            self._replay_lock.release()
