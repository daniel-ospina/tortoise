"""Issue #3036 — OAuth retention/GC sweep.

The FK half of #3036 is a PostgreSQL constraint and lives in the PGlite schema
suite ``supabase/tests/20260925000001_oauth_referential_integrity.sql`` (the
real schema lane). This module covers the SWEEP half: it is Python and runs on
the hosted Supabase control plane, so it is exercised here against the
in-memory FakeControlPlane (zero network).

Windows are credential hygiene, a different axis from the user-content
deletion promise (``docs/retention-and-deletion.md``); the sweep deletes a row
once its own ``expires_at`` is past by more than the configured grace.
"""
from __future__ import annotations

import ast
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.fake_control_plane import ErrorControlPlane, FakeControlPlane
from tortoise import hosted_api as ha_mod
from tortoise.oauth import (
    OAUTH_ACCESS_RETENTION_S,
    OAUTH_CODE_RETENTION_S,
    OAUTH_REFRESH_RETENTION_S,
    sweep_oauth_retention,
)

NOW = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)

_TABLES = (
    ("oauth_access_tokens", OAUTH_ACCESS_RETENTION_S),
    ("oauth_refresh_tokens", OAUTH_REFRESH_RETENTION_S),
    ("oauth_codes", OAUTH_CODE_RETENTION_S),
)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _seed_three(cp: FakeControlPlane, table: str, retention_s: int) -> None:
    """One row past the window, one expired but inside it, one live."""
    cp.seed(table, [
        {"id": f"{table}-past",
         "expires_at": _iso(NOW - timedelta(seconds=retention_s + 3600))},
        {"id": f"{table}-inwindow",
         "expires_at": _iso(NOW - timedelta(seconds=retention_s // 2))},
        {"id": f"{table}-live",
         "expires_at": _iso(NOW + timedelta(hours=1))},
    ])


# ── the sweep itself ────────────────────────────────────────────────────────

def test_removes_rows_past_their_window_and_keeps_rows_inside_it():
    """Both directions, for all three tables: a row past its window is DELETED,
    an expired-but-in-window row and a live row are KEPT."""
    cp = FakeControlPlane()
    for table, retention_s in _TABLES:
        _seed_three(cp, table, retention_s)

    observed = sweep_oauth_retention(cp, now=NOW)

    assert observed == {
        "oauth_access_tokens": 1,
        "oauth_refresh_tokens": 1,
        "oauth_codes": 1,
    }
    for table, _ in _TABLES:
        remaining = {row["id"] for row in cp.tables[table]}
        assert remaining == {f"{table}-inwindow", f"{table}-live"}, table


def test_revoked_but_unexpired_rows_are_kept():
    """A revoked row is only reaped once its own expiry has passed — the #2863
    residue lives out its TTL, it is not deleted at revocation."""
    cp = FakeControlPlane().seed("oauth_refresh_tokens", [
        {"id": "r-revoked",
         "expires_at": _iso(NOW + timedelta(days=1)),
         "revoked_at": _iso(NOW)},
    ])

    assert sweep_oauth_retention(cp, now=NOW)["oauth_refresh_tokens"] == 0
    assert len(cp.tables["oauth_refresh_tokens"]) == 1


def test_sweep_is_idempotent_and_reports_zero_on_a_second_run():
    cp = FakeControlPlane()
    for table, retention_s in _TABLES:
        _seed_three(cp, table, retention_s)

    first = sweep_oauth_retention(cp, now=NOW)
    second = sweep_oauth_retention(cp, now=NOW)

    assert sum(first.values()) == 3
    assert second == {
        "oauth_access_tokens": 0,
        "oauth_refresh_tokens": 0,
        "oauth_codes": 0,
    }


def test_sweep_fails_closed_on_a_control_plane_error():
    """The sweep under test must not swallow a failure; the CALLER does."""
    with pytest.raises(RuntimeError):
        sweep_oauth_retention(ErrorControlPlane(), now=NOW)


def test_one_table_failure_does_not_starve_the_others():
    """Per-table isolation: a persistent fault on one table must not deny GC to
    the other two. The failed table is recorded and the function still raises
    after attempting all three (fail-closed, retried next cycle)."""
    cp = FakeControlPlane()
    for table, retention_s in _TABLES:
        _seed_three(cp, table, retention_s)
    cp.fail_query(table="oauth_access_tokens", method="GET")

    with pytest.raises(RuntimeError):
        sweep_oauth_retention(cp, now=NOW)

    # The two healthy tables were swept despite the access-table fault.
    assert {row["id"] for row in cp.tables["oauth_refresh_tokens"]} == {
        "oauth_refresh_tokens-inwindow", "oauth_refresh_tokens-live"}
    assert {row["id"] for row in cp.tables["oauth_codes"]} == {
        "oauth_codes-inwindow", "oauth_codes-live"}


def test_retention_windows_are_positive():
    for value in (OAUTH_ACCESS_RETENTION_S, OAUTH_REFRESH_RETENTION_S,
                  OAUTH_CODE_RETENTION_S):
        assert isinstance(value, int) and value > 0


def test_negative_retention_override_never_deletes_live_rows(monkeypatch):
    """A negative window would move the cutoff INTO THE FUTURE and delete live
    credentials. The resolver must fall back to the safe default instead."""
    monkeypatch.setenv("TORTOISE_OAUTH_ACCESS_RETENTION_S", "-3600")
    monkeypatch.setenv("TORTOISE_OAUTH_REFRESH_RETENTION_S", "not-a-number")
    monkeypatch.setenv("TORTOISE_OAUTH_CODE_RETENTION_S", "0")
    cp = FakeControlPlane()
    for table in ("oauth_access_tokens", "oauth_refresh_tokens", "oauth_codes"):
        cp.seed(table, [{"id": f"{table}-live",
                         "expires_at": _iso(NOW + timedelta(hours=1))}])

    observed = sweep_oauth_retention(cp, now=NOW)

    assert observed == {"oauth_access_tokens": 0, "oauth_refresh_tokens": 0,
                        "oauth_codes": 0}
    for table in ("oauth_access_tokens", "oauth_refresh_tokens", "oauth_codes"):
        assert [row["id"] for row in cp.tables[table]] == [f"{table}-live"]


# ── the caller / scheduling wiring ──────────────────────────────────────────

def test_caller_is_a_noop_when_supabase_is_disabled(monkeypatch):
    """OAuth is hosted-only (D3): registry/embedded mode must not touch it.

    Assert the NEGATIVE (no control-plane call), not the absence of a raise —
    the caller swallows every Exception, so a raise-based sentinel is vacuous.
    """
    from tortoise import supabase_control as sc

    calls: list[str] = []
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: False)
    monkeypatch.setattr(sc, "get_control_plane",
                        lambda: calls.append("called") or FakeControlPlane())

    ha_mod._sweep_oauth_retention()  # must not raise, must not query

    assert calls == [], "registry mode must not fetch the control plane"


def test_caller_sweeps_when_supabase_is_enabled(monkeypatch):
    from tortoise import supabase_control as sc

    cp = FakeControlPlane()
    # The caller uses the real clock (no injected `now`), so seed against it:
    # one row long dead, one live.
    real_now = datetime.now(UTC)
    cp.seed("oauth_codes", [
        {"id": "oauth_codes-past",
         "expires_at": _iso(real_now - timedelta(days=400))},
        {"id": "oauth_codes-live",
         "expires_at": _iso(real_now + timedelta(hours=1))},
    ])
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(sc, "get_control_plane", lambda: cp)

    ha_mod._sweep_oauth_retention()

    assert {row["id"] for row in cp.tables["oauth_codes"]} == {"oauth_codes-live"}


def test_caller_swallows_a_sweep_failure(monkeypatch):
    """Best-effort: a failed sweep logs and never kills the loop."""
    from tortoise import supabase_control as sc

    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(sc, "get_control_plane", lambda: ErrorControlPlane())

    ha_mod._sweep_oauth_retention()  # must not raise


def test_boot_sweeps_include_the_oauth_sweep(monkeypatch):
    called: list[str] = []

    async def _fake_worker(fn, *, name=None, **kwargs):
        called.append(getattr(fn, "__name__", str(fn)))
        return None

    monkeypatch.setattr(ha_mod, "run_on_daemon_worker", _fake_worker)
    asyncio.run(ha_mod._run_boot_sweeps())

    assert "_sweep_oauth_retention" in called


def test_hourly_retention_loop_schedules_the_oauth_sweep():
    """The periodic runner must re-arm the OAuth sweep INSIDE its ``while True``
    body, not merely anywhere in the function — a call placed before the loop
    fires once per process and would keep this test green.

    ``_event_retention_loop`` is a closure inside ``_lifespan``, so it is
    pinned statically (the established pattern in
    ``tests/test_boot_regressions.py``) rather than executed.
    """
    tree = ast.parse(Path(ha_mod.__file__).read_text(encoding="utf-8"))
    loop = next(
        (node for node in ast.walk(tree)
         if isinstance(node, ast.AsyncFunctionDef)
         and node.name == "_event_retention_loop"),
        None,
    )
    assert loop is not None, "_event_retention_loop not found in hosted_api.py"
    while_loop = next(
        (node for node in ast.walk(loop)
         if isinstance(node, ast.While) and isinstance(node.test, ast.Constant)
         and node.test.value is True),
        None,
    )
    assert while_loop is not None, "_event_retention_loop has no `while True:`"
    names = {node.id for node in ast.walk(while_loop)
             if isinstance(node, ast.Name)}
    assert "_sweep_oauth_retention" in names, (
        "_sweep_oauth_retention must be scheduled INSIDE the hourly while-loop"
    )
