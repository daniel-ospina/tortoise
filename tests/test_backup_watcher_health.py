"""#2877: a dead backup watcher must be visible to /health, not just a boot log.

#2851/#2922 fixed the ``UnboundLocalError`` that stopped the backup watcher from
ever starting. The *class* of failure survived: ``_lifespan`` wraps the watcher
start in a log-and-continue ``except``, so the hosted durability monitor could
be completely dead while ``/health`` reported ``{"status": "ok"}``.

These tests are deliberately DB-free and app-boot-free: ``tortoise.hosted_api``
is imported with the pepper env set and the DB probe is stubbed, so they run in
the normal lane. Nothing here touches FalkorDB, R2, or the network.

Structure:
  * (a) functional + structural guards that the watcher boot path cannot
        silently shadow ``os`` / drop the failure marker again (#2851).
  * (b) ``/health`` reports ``degraded`` for a failed (never-started) or
        stopped (dead-thread) watcher, and stays ``ok`` when the sweep is
        legitimately disabled (#2877 target).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import os
from pathlib import Path

import pytest

# Auth module import requires the pepper; set it before importing the app.
os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import tortoise.hosted_api as hosted_api

REPO_ROOT = Path(__file__).resolve().parent.parent
HOSTED_API_SRC = REPO_ROOT / "tortoise" / "hosted_api.py"


# ── helpers ──────────────────────────────────────────────────────────────────


class _Thread:
    def __init__(self, alive: bool) -> None:
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


class _FakeWatcher:
    def __init__(self, alive: bool) -> None:
        self._thread = _Thread(alive)


@pytest.fixture(autouse=True)
def _restore_watcher_state():
    """Never leak the module globals this suite mutates into another test."""
    prev_watcher = hosted_api._WATCHER
    prev_error = hosted_api._WATCHER_START_ERROR
    yield
    hosted_api._WATCHER = prev_watcher
    hosted_api._WATCHER_START_ERROR = prev_error


def _stub_db_ok(monkeypatch) -> dict:
    """Make ``/health``'s DB verdict a clean ``ok``.

    This tree is post-#2850: ``health()`` reads ``_HEALTH_PROBE.snapshot()``, one
    in-memory value from a background refresher, so patching the module-level
    ``_probe_db`` alone is not enough — it is reached only from
    ``HealthProbe._run``, and a cold probe reports
    ``{"ok": False, "error": "probe has not produced a result"}``, failing every
    ``ok``-state assertion for the wrong reason. The snapshot is stubbed, and
    ``_probe_db`` as well so the helper also holds for a handler that awaits it
    directly.

    Returns the verdict dict for callers that assert on ``body["db"]``.
    """
    verdict = {"ok": True, "latency_ms": 0.1, "error": None}
    monkeypatch.setattr(hosted_api, "_probe_db", lambda: dict(verdict))
    health_probe = getattr(hosted_api, "_HEALTH_PROBE", None)
    if health_probe is not None:  # main-only seam (#2850)
        monkeypatch.setattr(health_probe, "snapshot", lambda: dict(verdict))
    return verdict


def _health_with_db_ok(monkeypatch) -> dict:
    _stub_db_ok(monkeypatch)
    return asyncio.run(hosted_api.health())


def _lifespan_node() -> ast.AsyncFunctionDef:
    tree = ast.parse(HOSTED_API_SRC.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_lifespan":
            return node
    raise AssertionError("tortoise/hosted_api.py has no `_lifespan` — relocate this pin")


# ── (a) the watcher boot path cannot regress into the #2851 shadow ───────────


def test_lifespan_does_not_bind_os_as_a_local():
    """#2851 functional guard: `os` must stay a module global in ``_lifespan``.

    A function-local ``import os`` (or ``os = ...``) binds ``os`` for the WHOLE
    function body, so the earlier ``os.environ.get(...)`` read raises
    ``UnboundLocalError`` — the exact crash that aborted the watcher-start block
    on every hosted boot for ~31 days.

    Two tables are checked, not one, because where the binding lands depends on
    whether anything nested captures it: a plain local lands in ``co_varnames``,
    and CPython promotes it to a **cell** (so it appears only in
    ``co_cellvars``) when a nested function inside ``_lifespan`` also reads the
    name. On the current tree ``co_varnames`` is the half that bites — no nested
    def in ``_lifespan`` reads ``os`` (``_sweep_events`` lives at module scope),
    and re-introducing ``import os`` puts ``os`` in ``co_varnames`` (verified by
    compiling the mutated function: ``co_cellvars`` stays clean). The
    ``co_cellvars`` half is kept against a future nested reader of ``os``.
    """
    code = inspect.unwrap(hosted_api._lifespan).__code__
    for shadowed in ("os", "asyncio", "threading", "logging"):
        assert (
            shadowed not in code.co_varnames and shadowed not in code.co_cellvars
        ), (
            f"`{shadowed}` is bound locally inside _lifespan — a function-local "
            "import/assignment shadows the module global for the whole function "
            "and re-introduces the #2851 UnboundLocalError"
        )


def test_watcher_start_failure_sets_the_health_marker():
    """#2877 structural guard: loudness alone does not reach /health.

    The start-failure ``except`` must record ``_WATCHER_START_ERROR`` — without
    it a failed watcher is indistinguishable from the legitimate disabled state
    and /health keeps reporting `ok` (the reported bug).

    The assignment must be located in an ``except`` handler AND carry a
    non-``None`` value. An unqualified "is the name assigned anywhere in
    ``_lifespan``" search is satisfied by the per-instance ``= None`` reset at
    the top of the function, so it stayed green with the failure-site
    assignment deleted (verified).
    """
    marker_in_handler = [
        node
        for handler in ast.walk(_lifespan_node())
        if isinstance(handler, ast.ExceptHandler)
        for node in ast.walk(handler)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        and target.id == "_WATCHER_START_ERROR"
        and not (isinstance(node.value, ast.Constant) and node.value.value is None)
    ]
    assert marker_in_handler, (
        "the start-failure `except` in _lifespan must assign "
        "`_WATCHER_START_ERROR` a non-None value — the `= None` reset at the "
        "top of the function does not count, or /health cannot tell 'wanted "
        "but dead' from 'disabled' (#2877)"
    )


# ── (b) /health surfaces a dead/absent watcher ───────────────────────────────


def test_health_ok_when_watcher_running(monkeypatch):
    hosted_api._WATCHER = _FakeWatcher(alive=True)
    hosted_api._WATCHER_START_ERROR = None

    body = _health_with_db_ok(monkeypatch)

    assert body["status"] == "ok"
    assert body["backup_watcher"] == {"state": "running", "ok": True, "error": None}


def test_health_degraded_when_watcher_never_started(monkeypatch):
    """The #2877 regression: DB fine, backups dead → not `ok`."""
    hosted_api._WATCHER = None
    hosted_api._WATCHER_START_ERROR = (
        "cannot access local variable 'os' where it is not associated with a value"
    )

    body = _health_with_db_ok(monkeypatch)

    assert body["status"] == "degraded", (
        "a failed backup watcher must degrade /health — silently-absent backups "
        "were exactly the failure #2877 reported"
    )
    assert body["db"]["ok"] is True  # the DB is not the problem
    assert body["backup_watcher"]["state"] == "failed"
    assert body["backup_watcher"]["ok"] is False
    assert "os" in body["backup_watcher"]["error"]


def test_health_degraded_when_watcher_thread_died(monkeypatch):
    hosted_api._WATCHER = _FakeWatcher(alive=False)
    hosted_api._WATCHER_START_ERROR = None

    body = _health_with_db_ok(monkeypatch)

    assert body["status"] == "degraded"
    assert body["backup_watcher"]["state"] == "stopped"
    assert body["backup_watcher"]["ok"] is False


def test_health_stays_ok_when_watcher_is_legitimately_disabled(monkeypatch):
    """Fail-closed default (no config) / kill switch must NOT degrade /health.

    The #2877 target pins this: false-positive degraded on every
    TestClient/embedded/self-host boot would train operators to ignore the
    signal, which is how the original blindness returns.
    """
    hosted_api._WATCHER = None
    hosted_api._WATCHER_START_ERROR = None

    body = _health_with_db_ok(monkeypatch)

    assert body["status"] == "ok"
    assert body["backup_watcher"] == {"state": "disabled", "ok": True, "error": None}


def test_health_never_raises_on_watcher_failure(monkeypatch):
    """Liveness must answer, not 5xx — a dead durability monitor is not a
    process death (#338 / the /health contract)."""
    _stub_db_ok(monkeypatch)

    class _ExplodingWatcher:
        @property
        def _thread(self):
            raise RuntimeError("watcher metadata unreadable")

    hosted_api._WATCHER = _ExplodingWatcher()

    body = asyncio.run(hosted_api.health())

    assert body["status"] == "degraded"
    assert body["backup_watcher"]["state"] == "unknown"
    assert body["backup_watcher"]["ok"] is False
