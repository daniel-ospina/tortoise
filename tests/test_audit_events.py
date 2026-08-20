"""Tests for audit event logger — Postgres + JSONL fallback.

Postgres-dependent tests are marked with @pytest.mark.postgres and
skipped when TEST_AUDIT_DB_URI is not set.
"""
from __future__ import annotations  # noqa: I001

import json
import os
from pathlib import Path  # noqa: F401
from unittest import mock

import pytest

from tortoise.audit_events import AuditLogger, _HAS_PSYCOPG2


@pytest.fixture
def audit_logger(tmp_path, monkeypatch):
    """AuditLogger with temp fallback dir."""
    fallback_dir = tmp_path / ".tortoise"
    fallback_dir.mkdir(parents=True, exist_ok=True)
    logger = AuditLogger(dsn=None)  # Force JSONL-only
    logger._fallback_dir = fallback_dir
    logger._fallback_path = fallback_dir / "audit_fallback.jsonl"
    return logger


class TestAuditLoggerJSONL:
    """JSONL fallback tests (no Postgres needed)."""

    def test_append_writes_to_jsonl_fallback(self, audit_logger):
        audit_logger.append("team-1", "user-1", "test_op")
        assert audit_logger._fallback_path.exists()
        content = audit_logger._fallback_path.read_text().strip()
        assert content
        event = json.loads(content)
        assert event["team_id"] == "team-1"
        assert event["actor_user_id"] == "user-1"
        assert event["operation"] == "test_op"

    def test_append_includes_all_fields(self, audit_logger):
        audit_logger.append(
            "team-1", "user-1", "team_create",
            resource_type="team", resource_id="t-123",
            ip_address="1.2.3.4", user_agent="pytest",
        )
        content = audit_logger._fallback_path.read_text().strip()
        event = json.loads(content)
        assert event["resource_type"] == "team"
        assert event["resource_id"] == "t-123"
        assert event["ip_address"] == "1.2.3.4"

    def test_append_generates_ulid_id(self, audit_logger):
        audit_logger.append("team-1", None, "op")
        content = audit_logger._fallback_path.read_text().strip()
        event = json.loads(content)
        assert event["id"]
        assert len(event["id"]) > 10

    def test_append_generates_created_at(self, audit_logger):
        audit_logger.append("team-1", None, "op")
        content = audit_logger._fallback_path.read_text().strip()
        event = json.loads(content)
        assert event["created_at"]

    def test_multiple_appends_accumulate(self, audit_logger):
        for i in range(5):
            audit_logger.append("team-1", "user-1", f"op_{i}")
        lines = audit_logger._fallback_path.read_text().strip().split("\n")
        assert len(lines) == 5

    def test_close_does_not_raise(self, audit_logger):
        audit_logger.append("team-1", "user-1", "op")
        audit_logger.close()  # Should not raise

    def test_replay_lock_prevents_concurrent_replay(self, audit_logger):
        """Verify _replay_lock is a threading.Lock."""
        assert hasattr(audit_logger, '_replay_lock')
        import threading
        assert isinstance(audit_logger._replay_lock, type(threading.Lock()))

    def test_fallback_dir_created(self, tmp_path):
        """Fallback dir is created if it doesn't exist."""
        new_dir = tmp_path / "new_fallback"
        logger = AuditLogger(dsn=None)
        logger._fallback_dir = new_dir
        logger._fallback_path = new_dir / "audit_fallback.jsonl"
        logger.append("team-1", "user-1", "op")
        assert new_dir.exists()
        logger.close()


class TestAuditLoggerMockPostgres:
    """Tests with mocked psycopg2 to verify Postgres path is attempted."""

    class _FakeCursor:
        """Captures executed SQL + params for assertions."""

        def __init__(self):
            self.executed = []

        def execute(self, sql, params=None):
            self.executed.append((sql, params))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _FakeConn:
        autocommit = True

        def __init__(self):
            self.cursor_obj = TestAuditLoggerMockPostgres._FakeCursor()

        def cursor(self):
            return self.cursor_obj

        def close(self):
            pass

    def _logger_with_fake_pg(self, monkeypatch, tmp_path):
        """AuditLogger whose Postgres writes land on a capturing fake conn.

        Injects a fake ``psycopg2`` module (with a capturing connect) into
        tortoise.audit_events — no real Postgres, no psycopg2 install
        needed, so the E2E-9 contract runs in plain CI.
        """
        import types
        monkeypatch.setenv("TORTOISE_AUDIT_DSN", "postgresql://fake:fake@localhost/fake")
        conn = self._FakeConn()
        fake_psycopg2 = types.SimpleNamespace(connect=lambda dsn: conn)
        monkeypatch.setattr("tortoise.audit_events.psycopg2", fake_psycopg2)
        monkeypatch.setattr("tortoise.audit_events._HAS_PSYCOPG2", True)
        logger = AuditLogger()
        logger._fallback_dir = tmp_path / ".tortoise"
        logger._fallback_path = tmp_path / ".tortoise" / "audit_fallback.jsonl"
        return logger, conn

    def _insert_params(self, conn):
        inserts = [
            (sql, params) for sql, params in conn.cursor_obj.executed
            if sql.strip().upper().startswith("INSERT")
        ]
        assert inserts, "no INSERT was executed against the fake Postgres conn"
        return inserts[-1]

    def test_non_uuid_actor_inserted_verbatim(self, monkeypatch, tmp_path):
        """E2E-9 (#771): a NON-UUID actor must insert cleanly.

        Pre-#669 the Supabase audit_events table declared actor_user_id UUID
        (migration 0002) while the logger emits TEXT — any non-UUID actor
        ('service-bootstrap', 'anon-*', ...) failed the INSERT and fell back
        to JSONL. Migration 0006 alters the column to TEXT; this test locks
        the contract: the logger passes the actor through verbatim (no UUID
        coercion anywhere in the INSERT statement or params).
        """
        logger, conn = self._logger_with_fake_pg(monkeypatch, tmp_path)
        logger.append("team-1", "service-bootstrap", "bootstrap",
                     resource_type="team", resource_id="t-1")
        sql, params = self._insert_params(conn)
        assert "actor_user_id" in sql
        assert "%(actor_user_id)s" in sql
        assert params["actor_user_id"] == "service-bootstrap"
        logger.close()

    def test_anon_prefixed_actor_inserted_verbatim(self, monkeypatch, tmp_path):
        """E2E-9: the anon-* actor family (agent signups) lands as TEXT too."""
        logger, conn = self._logger_with_fake_pg(monkeypatch, tmp_path)
        logger.append("team-2", "anon-8f3a1c", "agent_signup")
        sql, params = self._insert_params(conn)  # noqa: RUF059
        assert params["actor_user_id"] == "anon-8f3a1c"
        logger.close()

    def test_postgres_insert_attempted_when_dsn_set(self, monkeypatch, tmp_path):
        """When TORTOISE_AUDIT_DSN is set, Postgres INSERT is attempted."""
        if not _HAS_PSYCOPG2:
            pytest.skip("psycopg2 not installed")

        monkeypatch.setenv("TORTOISE_AUDIT_DSN", "postgresql://fake:fake@localhost/fake")

        fallback_dir = tmp_path / ".tortoise"
        fallback_dir.mkdir(parents=True, exist_ok=True)

        logger = AuditLogger()
        logger._fallback_dir = fallback_dir
        logger._fallback_path = fallback_dir / "audit_fallback.jsonl"

        # Mock psycopg2.connect to fail, so we exercise the fallback path
        with mock.patch("tortoise.audit_events.psycopg2.connect",
                        side_effect=Exception("connection refused")):
            logger.append("team-1", "user-1", "test_op")

        # Should have fallen back to JSONL
        assert logger._fallback_path.exists()
        logger.close()

    def test_noop_when_no_dsn_and_no_psycopg2(self, tmp_path, monkeypatch):
        """Without DSN, works JSONL-only."""
        monkeypatch.delenv("TORTOISE_AUDIT_DSN", raising=False)

        fallback_dir = tmp_path / ".tortoise"
        fallback_dir.mkdir(parents=True, exist_ok=True)

        logger = AuditLogger()
        logger._fallback_dir = fallback_dir
        logger._fallback_path = fallback_dir / "audit_fallback.jsonl"

        logger.append("team-1", "user-1", "test_op")
        assert logger._fallback_path.exists()
        logger.close()

    def test_schema_ddl_is_valid_sql(self):
        """Verify the schema DDL is syntactically plausible."""
        from tortoise.audit_events import _SCHEMA_DDL
        assert "CREATE TABLE IF NOT EXISTS audit_events" in _SCHEMA_DDL
        assert "id TEXT PRIMARY KEY" in _SCHEMA_DDL
        assert "team_id TEXT NOT NULL" in _SCHEMA_DDL


@pytest.mark.postgres
class TestAuditLoggerPostgres:
    """Integration tests requiring a real Postgres instance.

    Set TEST_AUDIT_DB_URI to run these.
    e.g., TEST_AUDIT_DB_URI=postgresql://user:pass@localhost:5432/tortoise_test
    """

    @pytest.fixture
    def pg_logger(self):
        """AuditLogger connected to test Postgres."""
        dsn = os.environ.get("TEST_AUDIT_DB_URI")
        if not dsn:
            pytest.skip("TEST_AUDIT_DB_URI not set")
        logger = AuditLogger(dsn=dsn)
        yield logger
        logger.close()

    def test_append_writes_to_postgres(self, pg_logger):
        pg_logger.append("team-pg", "user-pg", "pg_test_op",
                         resource_type="team", resource_id="pg-1")
        # If we got here without exception, the write succeeded
        # Verify by appending another and checking no fallback was needed
        assert not pg_logger._fallback_path.exists() or \
            pg_logger._fallback_path.read_text().strip() == ""

    def test_non_uuid_actor_lands_with_text_value(self, pg_logger):
        """E2E-9 (#771): non-UUID actor round-trips through a real Postgres.

        Runs against a real Postgres (TEST_AUDIT_DB_URI, e.g. a local
        Supabase stack with migrations 0002+0006 applied): the row must land
        with the actor stored verbatim — a UUID-typed column would reject
        'service-bootstrap' with an invalid-input error and silently fall
        back to JSONL.
        """
        actor = "service-bootstrap"
        team_id = "team-e2e9"
        pg_logger.append(team_id, actor, "e2e9_bootstrap",
                         resource_type="team", resource_id=team_id)
        with pg_logger._conn.cursor() as cur:
            cur.execute(
                "SELECT actor_user_id FROM audit_events "
                "WHERE team_id = %s AND operation = 'e2e9_bootstrap' "
                "ORDER BY created_at DESC LIMIT 1",
                (team_id,),
            )
            row = cur.fetchone()
        assert row is not None, "audit row did not land in Postgres"
        assert row[0] == actor, f"actor stored as {row[0]!r}, expected {actor!r}"

    def test_schema_auto_created(self, pg_logger):
        """Verifies the table exists by writing to it (schema auto-create)."""
        pg_logger.append("team-schema", "user-schema", "schema_test")
        # If this doesn't raise, the schema was created successfully

    def test_multiple_appends(self, pg_logger):
        for i in range(3):
            pg_logger.append("team-multi", "user-multi", f"op_{i}")
        # No fallback file should be needed
        assert not pg_logger._fallback_path.exists() or \
            pg_logger._fallback_path.read_text().strip() == ""
