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

# Deliberately FAR from wall-clock: `now` is an injected parameter, and a clock
# near the real one lets a `now`-ignoring mutant pass during a coincidence
# window. Decades away makes the injection load-bearing.
NOW = datetime(2020, 1, 1, 12, 0, 0, tzinfo=UTC)

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

    with pytest.raises(RuntimeError) as excinfo:
        sweep_oauth_retention(cp, now=NOW)
    # The error must NAME the failing table AND carry the injected reason —
    # asserting only `!=` a legacy string would pass for a message that keeps
    # the shape but drops the reason (or reverts to `sorted(...)[0]`).
    assert "oauth_access_tokens" in str(excinfo.value)
    assert "Supabase unreachable (simulated)" in str(excinfo.value)
    assert cp.unfired_faults() == [], "the injected fault must have fired"

    # The two healthy tables were swept despite the access-table fault.
    assert {row["id"] for row in cp.tables["oauth_refresh_tokens"]} == {
        "oauth_refresh_tokens-inwindow", "oauth_refresh_tokens-live"}
    assert {row["id"] for row in cp.tables["oauth_codes"]} == {
        "oauth_codes-inwindow", "oauth_codes-live"}


def test_a_delete_leg_fault_is_also_fail_closed():
    """Fail-closed must hold on the WRITE leg too. Both other fault tests
    inject on GET, so wrapping only the DELETE in `try/except: pass` — silently
    stopping GC with no error — would pass the whole suite."""
    cp = FakeControlPlane()
    for table, retention_s in _TABLES:
        _seed_three(cp, table, retention_s)
    cp.fail_query(table="oauth_access_tokens", method="DELETE")

    with pytest.raises(RuntimeError) as excinfo:
        sweep_oauth_retention(cp, now=NOW)

    assert "oauth_access_tokens" in str(excinfo.value)
    assert cp.unfired_faults() == []
    # Nothing was deleted for the failed table (its rows survive), and the
    # healthy tables were still swept.
    assert {row["id"] for row in cp.tables["oauth_access_tokens"]} == {
        "oauth_access_tokens-past", "oauth_access_tokens-inwindow",
        "oauth_access_tokens-live"}
    assert {row["id"] for row in cp.tables["oauth_refresh_tokens"]} == {
        "oauth_refresh_tokens-inwindow", "oauth_refresh_tokens-live"}


def test_retention_windows_are_positive():
    for value in (OAUTH_ACCESS_RETENTION_S, OAUTH_REFRESH_RETENTION_S,
                  OAUTH_CODE_RETENTION_S):
        assert isinstance(value, int) and value > 0


def test_a_row_exactly_at_the_cutoff_is_kept():
    """The predicate is STRICT ``<``: a row whose age exactly equals the window
    is still inside the grace. No other test seeds a boundary row, so an
    ``lt``→``lte`` mutation (which reaps one second early) passed the suite."""
    cp = FakeControlPlane().seed("oauth_access_tokens", [
        {"id": "at-cutoff",
         "expires_at": _iso(NOW - timedelta(seconds=OAUTH_ACCESS_RETENTION_S))},
        {"id": "just-inside",
         "expires_at": _iso(NOW - timedelta(seconds=OAUTH_ACCESS_RETENTION_S - 1))},
        {"id": "just-past",
         "expires_at": _iso(NOW - timedelta(seconds=OAUTH_ACCESS_RETENTION_S + 1))},
    ])

    assert sweep_oauth_retention(cp, now=NOW)["oauth_access_tokens"] == 1
    assert {r["id"] for r in cp.tables["oauth_access_tokens"]} == {
        "at-cutoff", "just-inside"}


def test_default_window_applies_when_the_env_is_unset(monkeypatch):
    """Pin the exact shipped 24h DEFAULT independently of the constant the
    other tests seed from (they derive their rows from `retention_s`, so a
    wrong constant is invisible). Rows are seeded at 86400±1s so ONLY the exact
    default satisfies both, and on ALL THREE tables — the defaults are three
    separate constants, so pinning only the access one let a drifted
    refresh/codes default (anything > 2h) pass the whole suite.
    """
    for name in ("TORTOISE_OAUTH_ACCESS_RETENTION_S",
                 "TORTOISE_OAUTH_REFRESH_RETENTION_S",
                 "TORTOISE_OAUTH_CODE_RETENTION_S"):
        monkeypatch.delenv(name, raising=False)
    cp = FakeControlPlane()
    for table in ("oauth_access_tokens", "oauth_refresh_tokens", "oauth_codes"):
        cp.seed(table, [
            {"id": f"{table}-inside",
             "expires_at": _iso(NOW - timedelta(seconds=86400 - 1))},
            {"id": f"{table}-past",
             "expires_at": _iso(NOW - timedelta(seconds=86400 + 1))},
        ])

    observed = sweep_oauth_retention(cp, now=NOW)

    assert observed == {"oauth_access_tokens": 1, "oauth_refresh_tokens": 1,
                        "oauth_codes": 1}
    for table in ("oauth_access_tokens", "oauth_refresh_tokens", "oauth_codes"):
        assert [r["id"] for r in cp.tables[table]] == [f"{table}-inside"], table


def test_ceiling_boundary_and_magnitude_are_pinned(monkeypatch):
    """`>` vs `>=` at the ceiling is otherwise unpinned (a 9-digit in-range
    value would be clamped upward), and importing `_MAX_RETENTION_S` in the
    other tests neutralizes its magnitude — so assert the LITERAL here."""
    from tortoise.oauth import _MAX_RETENTION_S, _retention_seconds

    assert _MAX_RETENTION_S == 315_360_000, "the 10-year ceiling is a contract"
    env = "TORTOISE_OAUTH_ACCESS_RETENTION_S"
    # In-range values (including a 9-digit one) pass through UNCHANGED.
    for raw in ("200000000", "315359999", "315360000"):
        monkeypatch.setenv(env, raw)
        assert _retention_seconds(env, OAUTH_ACCESS_RETENTION_S) == int(raw)
    # One second over clamps.
    monkeypatch.setenv(env, "315360001")
    assert _retention_seconds(env, OAUTH_ACCESS_RETENTION_S) == _MAX_RETENTION_S


def test_misconfigured_overrides_are_reported(monkeypatch, caplog):
    """A misconfigured retention knob must be VISIBLE to the operator, not a
    silent fallback/clamp — the docstring promises a warning on every branch.
    Nothing else asserts these log calls, so deleting them was invisible."""
    import logging

    from tortoise.oauth import _MAX_RETENTION_S, _retention_seconds

    cases = (
        ("not-a-number", OAUTH_ACCESS_RETENTION_S, "not a positive integer"),
        ("-5", OAUTH_ACCESS_RETENTION_S, "not a positive integer"),  # '-' is not a digit
        ("0", OAUTH_ACCESS_RETENTION_S, "must be positive"),
        ("999999999999", _MAX_RETENTION_S, "clamping"),
        ("9" * 4301, _MAX_RETENTION_S, "clamping"),
    )
    for raw, expected, needle in cases:
        monkeypatch.setenv("TORTOISE_OAUTH_ACCESS_RETENTION_S", raw)
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="tortoise.oauth"):
            assert _retention_seconds("TORTOISE_OAUTH_ACCESS_RETENTION_S",
                                      OAUTH_ACCESS_RETENTION_S) == expected
        assert any(needle in r.getMessage() for r in caplog.records), (
            f"no warning containing {needle!r} for {raw!r}")


def test_negative_retention_override_never_deletes_live_rows(monkeypatch):
    """A negative window would move the cutoff INTO THE FUTURE and delete live
    credentials. The resolver must fall back to the safe DEFAULT instead — and
    the test must prove the FALLBACK, not merely survival: a row expired 30
    minutes ago is KEPT (a `max(0, abs(-3600))` reading would delete it)."""
    monkeypatch.setenv("TORTOISE_OAUTH_ACCESS_RETENTION_S", "-3600")
    monkeypatch.setenv("TORTOISE_OAUTH_REFRESH_RETENTION_S", "not-a-number")
    monkeypatch.setenv("TORTOISE_OAUTH_CODE_RETENTION_S", "0")
    cp = FakeControlPlane()
    for table in ("oauth_access_tokens", "oauth_refresh_tokens", "oauth_codes"):
        cp.seed(table, [
            {"id": f"{table}-live",
             "expires_at": _iso(NOW + timedelta(hours=1))},
            # Expired 2h ago — inside the 86400s default but OUTSIDE every bad
            # override below (`abs(-3600)` = 3600s, `0`, `+5`), so only a true
            # fallback keeps it. (30 min would NOT discriminate: it is inside a
            # bogus 3600s window, and an `abs()` mutant would pass.)
            {"id": f"{table}-grace",
             "expires_at": _iso(NOW - timedelta(hours=2))},
        ])

    observed = sweep_oauth_retention(cp, now=NOW)

    assert observed == {"oauth_access_tokens": 0, "oauth_refresh_tokens": 0,
                        "oauth_codes": 0}
    for table in ("oauth_access_tokens", "oauth_refresh_tokens", "oauth_codes"):
        assert {row["id"] for row in cp.tables[table]} == {
            f"{table}-live", f"{table}-grace"}


def test_a_valid_positive_override_is_applied(monkeypatch):
    """The mirror of the fallback test: a well-formed override really is used
    (otherwise `_retention_seconds` could ignore the env entirely)."""
    monkeypatch.setenv("TORTOISE_OAUTH_ACCESS_RETENTION_S", "600")
    cp = FakeControlPlane().seed("oauth_access_tokens", [
        {"id": "access-past",
         "expires_at": _iso(NOW - timedelta(minutes=30))},   # outside 600s
        {"id": "access-inwindow",
         "expires_at": _iso(NOW - timedelta(minutes=5))},    # inside 600s
    ])

    assert sweep_oauth_retention(cp, now=NOW)["oauth_access_tokens"] == 1
    assert [r["id"] for r in cp.tables["oauth_access_tokens"]] == [
        "access-inwindow"]


def test_each_table_reads_its_own_retention_env(monkeypatch):
    """A copy-paste swap of the env_name column in the sweep's plan tuple would
    silently wire one table's window to another's key. Three DISTINCT windows
    and two row ages make each cutoff observable and non-interchangeable.

    650s is outside access's 600s but inside refresh's 700s and codes' 800s;
    750s is outside refresh's 700s too. No single shared key reproduces the
    resulting pattern.
    """
    monkeypatch.setenv("TORTOISE_OAUTH_ACCESS_RETENTION_S", "600")
    monkeypatch.setenv("TORTOISE_OAUTH_REFRESH_RETENTION_S", "700")
    monkeypatch.setenv("TORTOISE_OAUTH_CODE_RETENTION_S", "800")
    cp = FakeControlPlane()
    for table in ("oauth_access_tokens", "oauth_refresh_tokens", "oauth_codes"):
        cp.seed(table, [
            {"id": f"{table}-650",
             "expires_at": _iso(NOW - timedelta(seconds=650))},
            {"id": f"{table}-750",
             "expires_at": _iso(NOW - timedelta(seconds=750))},
        ])

    observed = sweep_oauth_retention(cp, now=NOW)

    assert observed == {"oauth_access_tokens": 2, "oauth_refresh_tokens": 1,
                        "oauth_codes": 0}
    assert cp.tables["oauth_access_tokens"] == []
    assert [r["id"] for r in cp.tables["oauth_refresh_tokens"]] == [
        "oauth_refresh_tokens-650"]
    assert {r["id"] for r in cp.tables["oauth_codes"]} == {
        "oauth_codes-650", "oauth_codes-750"}


@pytest.mark.parametrize("raw", ["+5", "1_0", "\u0663", "0", "", " 5", "5 ", "1e9"])
def test_malformed_or_nonpositive_override_falls_back_to_default(raw, monkeypatch):
    """Strict parse: `int()` alone accepts ``+5``, ``1_0`` and non-ASCII digits
    (``\u0663`` = 3), each of which silently yields a window far shorter than
    intended. A malformed or non-positive value falls back to the DEFAULT."""
    from tortoise.oauth import _retention_seconds

    monkeypatch.setenv("TORTOISE_OAUTH_ACCESS_RETENTION_S", raw)
    assert _retention_seconds("TORTOISE_OAUTH_ACCESS_RETENTION_S",
                              OAUTH_ACCESS_RETENTION_S) == OAUTH_ACCESS_RETENTION_S


@pytest.mark.parametrize("raw", [
    str(315_360_000 + 1),       # one second over the ceiling
    "999999999999",             # 12 digits, above the ceiling
    "9" * 4301,                 # beyond CPython's int() digit limit
    "000000" + str(315_360_000 + 1),  # leading zeros must not hide the value
])
def test_out_of_range_override_clamps_to_the_ceiling(raw, monkeypatch):
    """Directional: an operator asking for MORE than the ceiling gets the
    ceiling, not the 1-day default — falling back would delete EARLIER than
    requested. The 4301-digit case is the one bare `int()` cannot even parse
    (CPython's 4300-digit limit), so a width check must come before it, and
    that check must compare SIGNIFICANT digits (leading zeros pad the string
    without raising the value)."""
    from tortoise.oauth import _MAX_RETENTION_S, _retention_seconds

    monkeypatch.setenv("TORTOISE_OAUTH_ACCESS_RETENTION_S", raw)
    assert _retention_seconds("TORTOISE_OAUTH_ACCESS_RETENTION_S",
                              OAUTH_ACCESS_RETENTION_S) == _MAX_RETENTION_S


@pytest.mark.parametrize("raw", [
    "00000086400",   # leading zeros: a VALID in-range window, not a clamp
    "00000600",
])
def test_leading_zeros_do_not_inflate_the_width_check(raw, monkeypatch):
    """A width check on `len(raw)` would read `00000086400` (11 chars) as
    above the ceiling and clamp a legitimate 86400s window to 10 years —
    silently disabling GC. The check must strip leading zeros first."""
    from tortoise.oauth import _retention_seconds

    monkeypatch.setenv("TORTOISE_OAUTH_ACCESS_RETENTION_S", raw)
    assert _retention_seconds("TORTOISE_OAUTH_ACCESS_RETENTION_S",
                              OAUTH_ACCESS_RETENTION_S) == int(raw.lstrip("0"))


class _RecordingFake(FakeControlPlane):
    """Records (table, method) for every query so CALL ORDER is observable."""

    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, str]] = []

    def query(self, table, **kwargs):
        self.calls.append((table, kwargs.get("method") or "GET"))
        return super().query(table, **kwargs)


def test_delete_order_is_access_then_refresh_then_codes():
    """The documented delete order is load-bearing — it is why the FK's
    ON DELETE SET NULL is a safety net rather than the routine path (an access
    row is reaped before any refresh row it points at). FakeControlPlane models
    no FK, so the order is otherwise unobservable, and a reordered plan passed
    the whole suite."""
    cp = _RecordingFake()
    for table, retention_s in _TABLES:
        _seed_three(cp, table, retention_s)

    sweep_oauth_retention(cp, now=NOW)

    deletes = [table for table, method in cp.calls if method == "DELETE"]
    assert deletes == ["oauth_access_tokens", "oauth_refresh_tokens",
                       "oauth_codes"]


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


def test_caller_swallows_a_sweep_failure(monkeypatch, caplog):
    """Best-effort: a failed sweep logs and never kills the loop.

    The LOG is half the contract — without it a GC failure is invisible — so
    assert it, not just the absence of a raise.
    """
    import logging

    from tortoise import supabase_control as sc

    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(sc, "get_control_plane", lambda: ErrorControlPlane())

    with caplog.at_level(logging.WARNING, logger="tortoise.hosted_api"):
        ha_mod._sweep_oauth_retention()  # must not raise

    assert any("oauth retention sweep failed" in r.getMessage()
               for r in caplog.records), caplog.records


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
