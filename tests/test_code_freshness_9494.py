"""#9494 — a long-lived server must report that it is serving an old module graph.

Measured 2026-10-21: four live ``tortoise.mcp_server`` processes, the oldest
started **2d 4h before** ``_ledger_record`` was added to ``tortoise/sdk.py``.
Every ``mcp__tortoise__*`` call then failed with

    cannot import name '_ledger_record' from 'tortoise.sdk'

which names a symbol that EXISTS in the repo (it was introduced by
``c975bd2be``) — so the first evidence an agent sees points at correct code and
never at the process. Nothing measured staleness: not ``tortoise_health``, not
``/health``, not a log line.

These tests pin the three halves of the fix:

1. the measurement itself (``code_freshness.check``) — a loaded module whose
   file changes on disk is reported, and the report names the restart;
2. the health surface (``monitoring.metrics`` → ``tortoise_health``) carries
   the verdict, so the first health call can say "stale" before the first
   failing call does;
3. the boundary (``mcp_server._safe``) answers a symbol-shaped failure on a
   MEASURED-stale process with the drift and the fix — and leaves an
   ImportError on a clean process reading as the real code defect it is.
"""
from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock

import pytest

from tortoise import code_freshness


@pytest.fixture(autouse=True)
def _fresh_baseline():
    """Start every test from an empty baseline so drift cannot leak between
    tests (the module-global baseline is process-wide by design — that IS the
    measurement — so isolation is the test's job)."""
    code_freshness._reset_for_tests()
    yield
    code_freshness._reset_for_tests()


def _probe_module(tmp_path, name="tortoise._freshness_probe_9494"):
    """A ``tortoise.*`` module backed by a REAL file, so ``check()`` stats it.

    A real path (not a mock) is the point: the measurement is
    "the file this process loaded no longer matches the file on disk", so the
    test must give it a file to change.
    """
    path = tmp_path / "probe_9494.py"
    path.write_text("VALUE = 1\n")
    module = types.ModuleType(name)
    module.__file__ = str(path)
    return module, path


def _rewrite(path, body="VALUE = 1\nEXTRA_SYMBOL = 2\n"):
    """The edit shape of a mid-flight symbol addition: content AND size change."""
    st = os.stat(path)
    path.write_text(body)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))


# ── 1. the measurement ───────────────────────────────────────────────

def test_a_module_edited_after_load_is_reported_stale(tmp_path, monkeypatch):
    """The #9494 mechanism, at its smallest: load → edit → check says stale."""
    module, path = _probe_module(tmp_path)
    monkeypatch.setitem(sys.modules, module.__name__, module)

    first = code_freshness.check()
    assert first["stale"] is False, first
    assert module.__name__ in code_freshness.tracked()

    _rewrite(path)  # the mid-flight symbol addition
    second = code_freshness.check()

    assert second["stale"] is True, second
    assert [row["module"] for row in second["changed"]] == [module.__name__]
    assert second["changed"][0]["removed"] is False
    assert second["changed"][0]["disk_mtime"] > second["changed"][0]["loaded_mtime"]


def test_the_stale_report_names_the_fix_not_the_symbol(tmp_path, monkeypatch):
    """Acceptance (a)+(b): naming a symbol sends the reader into correct code.

    A stale report must name the PROCESS and the restart — that is the whole
    cost the issue measured (time spent proving ``sdk.py`` was fine).
    """
    module, path = _probe_module(tmp_path)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    code_freshness.check()
    _rewrite(path)

    report = code_freshness.check()

    assert report["hint"] and "Restart the MCP server" in report["hint"]
    assert module.__name__ in report["hint"]  # names WHAT drifted
    assert report["process_started_at"]  # and the age it has been serving


def test_a_removed_file_is_drift_too(tmp_path, monkeypatch):
    """A deleted module file is the same class of drift, never a silent pass."""
    module, path = _probe_module(tmp_path)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    code_freshness.check()
    path.unlink()

    report = code_freshness.check()

    assert report["stale"] is True
    assert report["changed"][0]["removed"] is True
    assert report["changed"][0]["disk_mtime"] is None


def test_the_snapshot_covers_the_sdk_the_issue_was_about():
    """The graph measured is the SERVING graph — including ``tortoise.sdk``,
    the file whose symbol the fleet's ImportError named."""
    import tortoise.mcp_server  # noqa: F401 — the serving entrypoint

    assert "tortoise.sdk" in code_freshness.tracked()


def test_mutating_the_real_sdk_mtime_is_observed():
    """The acceptance case with the real file: mutate ``sdk.py`` on disk after
    the process loaded it and assert the staleness is observable.

    Only the mtime is moved (10^9 ns forward) and it is restored in ``finally``
    — content is never touched, so a crash mid-test leaves at worst one
    byte-compilation, never a modified repository source."""
    import tortoise.sdk as sdk_module

    code_freshness.check()  # baseline the real, loaded graph
    st = os.stat(sdk_module.__file__)
    try:
        os.utime(sdk_module.__file__, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
        report = code_freshness.check()
    finally:
        os.utime(sdk_module.__file__, ns=(st.st_atime_ns, st.st_mtime_ns))

    assert report["stale"] is True, report
    assert "tortoise.sdk" in [row["module"] for row in report["changed"]]


def test_the_report_never_leaks_an_absolute_host_path(tmp_path, monkeypatch):
    """``metrics()``/``tortoise_health`` is served to TENANTS over HTTP: the
    drift row must identify the file (``tortoise/sdk.py``) without publishing
    the host's directory layout or user name."""
    module, path = _probe_module(tmp_path)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    code_freshness.check()
    _rewrite(path)

    row = code_freshness.check()["changed"][0]

    assert row["file"] == "probe_9494.py" or not row["file"].startswith("/")
    assert str(tmp_path) not in row["file"]


def test_a_clean_process_reports_clean():
    """No drift, no alarm: the flag must not be a constant."""
    report = code_freshness.check()

    assert report["stale"] is False
    assert report["changed"] == []
    assert report["hint"] is None
    assert report["tracked"] > 0


# ── 2. the health surface ────────────────────────────────────────────

class _FakeSDK:
    """Minimal SDK stub for a reachability probe with no FalkorDB (the shape
    ``tests/test_monitoring.py`` uses for the same reason)."""

    def _get_proj(self):
        proj = MagicMock()
        proj.g.query.return_value = MagicMock(result_set=[[1]])
        return proj

    def taxonomy(self):
        return {"Point": 0}


def test_health_payload_carries_the_server_code_verdict(monkeypatch):
    """``tortoise_health`` — the first call an agent makes — can answer the
    question the fleet could not ask: is this process serving the repo?"""
    from tortoise import monitoring

    verdict = {"stale": False, "changed": [], "tracked": 3, "hint": None}
    monkeypatch.setattr(code_freshness, "check", lambda: verdict)

    payload = monitoring.metrics(sdk=_FakeSDK())

    assert payload["server_code"] == verdict


def test_health_reports_stale_without_degrading_liveness(monkeypatch):
    """A stale process is still SERVING: ``status`` is a liveness verdict about
    the graph, so it must not flip to degraded (that would turn an
    observability gap into a load-balancer outage) — the staleness rides its
    own field."""
    from tortoise import monitoring

    stale = {"stale": True, "changed": [{"module": "tortoise.sdk"}],
             "changed_count": 1, "tracked": 3, "hint": code_freshness.HINT}
    monkeypatch.setattr(code_freshness, "check", lambda: stale)

    payload = monitoring.metrics(sdk=_FakeSDK())

    assert payload["server_code"]["stale"] is True
    assert payload["status"] == "ok"


def test_tortoise_health_end_to_end_carries_the_verdict(tmp_path, monkeypatch):
    """Through the tool the fleet actually calls, drift is visible."""
    from tortoise import mcp_server
    from tortoise.mcp_auth import _transport_mode

    module, path = _probe_module(tmp_path)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    code_freshness.check()
    _rewrite(path)

    monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: _FakeSDK())
    token = _transport_mode.set("http")
    try:
        result = mcp_server.tortoise_health()
    finally:
        _transport_mode.reset(token)

    assert result["server_code"]["stale"] is True, result
    assert module.__name__ in result["server_code"]["hint"]


# ── 3. the boundary: the error names the right thing ─────────────────

_SYMBOL_ERROR = "cannot import name '_ledger_record' from 'tortoise.sdk'"


def _call_safe(fn, monkeypatch):
    from tortoise import mcp_server
    from tortoise.mcp_auth import _transport_mode

    monkeypatch.setattr(mcp_server, "_is_dev_mode", lambda: True)
    token = _transport_mode.set("stdio")
    try:
        return mcp_server._safe(fn)
    finally:
        _transport_mode.reset(token)


def test_a_stale_process_answers_a_symbol_failure_with_the_restart(tmp_path, monkeypatch):
    """The exact reported failure, on a measured-stale process: the answer must
    carry the drift and the fix instead of only the symbol name."""
    module, path = _probe_module(tmp_path)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    code_freshness.check()
    _rewrite(path)

    def boom():
        raise ImportError(_SYMBOL_ERROR)

    payload = _call_safe(boom, monkeypatch)

    assert payload["stale_server"] is True, payload
    assert _SYMBOL_ERROR in payload["error"]  # the original cause is kept
    assert "Restart the MCP server" in payload["error"]
    assert module.__name__ in payload["error"]


def test_a_clean_process_keeps_the_import_error_reading_as_a_code_defect(monkeypatch):
    """The other direction, and it matters more: an ``ImportError`` on a FRESH
    process is a real code defect. Blaming it on staleness would be the same
    class of lie in reverse — so nothing is added."""
    def boom():
        raise ImportError(_SYMBOL_ERROR)

    payload = _call_safe(boom, monkeypatch)

    assert "stale_server" not in payload
    assert payload["error"] == _SYMBOL_ERROR


def test_staleness_hint_ignores_non_symbol_failures(tmp_path, monkeypatch):
    """A stale process must not relabel every error as staleness — only the
    symbol-shaped ones the issue measured."""
    module, path = _probe_module(tmp_path)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    code_freshness.check()
    _rewrite(path)

    assert code_freshness.staleness_hint(RuntimeError(_SYMBOL_ERROR)) is None
    assert code_freshness.staleness_hint(ImportError(_SYMBOL_ERROR)) is not None
