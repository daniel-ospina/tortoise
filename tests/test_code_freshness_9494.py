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

1. the measurement (``code_freshness.check``) — a loaded module whose file
   changes on disk is reported, a content-identical rewrite is not, the startup
   graph is baselined AT STARTUP, and the report names the restart;
2. the health surface (``monitoring.metrics`` → ``tortoise_health``) carries the
   verdict, so the first health call can say "stale" before the first failing
   call does;
3. the boundary (``mcp_server._safe``) answers a symbol-shaped failure on a
   MEASURED-stale process with the drift and the fix — but only when the failure
   NAMES a module that actually drifted, so a real code defect, a missing extra,
   or an unrelated ``AttributeError`` keeps reading as itself.
"""
from __future__ import annotations

import os
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tortoise import code_freshness

_ROOT = Path(__file__).resolve().parent.parent
_OLD = 1_600_000_000  # 2020-09-13, safely older than this process's start


@pytest.fixture(autouse=True)
def _fresh_baseline():
    """Start every test from an empty baseline so drift cannot leak between
    tests (the baseline is process-global by design — that IS the measurement —
    so isolation is the test's job)."""
    code_freshness._reset_for_tests()
    yield
    code_freshness._reset_for_tests()


def _probe_module(tmp_path, name="tortoise._freshness_probe_9494"):
    """A ``tortoise.*`` module backed by a REAL file, so ``check()`` stats it.

    A real path (not a mock) is the point: the measurement is "the file this
    process loaded no longer matches the file on disk", so the test must give it
    a file to change.
    """
    path = tmp_path / f"{name.rsplit('.', 1)[-1]}.py"
    path.write_text("VALUE = 1\n")
    module = types.ModuleType(name)
    module.__file__ = str(path)
    return module, path


def _rewrite(path, body="VALUE = 1\nEXTRA_SYMBOL = 2\n"):
    """The edit shape of a mid-flight symbol addition: content AND size change."""
    st = os.stat(path)
    path.write_text(body)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))


def _older(path):
    os.utime(path, (  _OLD, _OLD))


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

    A stale report must name the PROCESS, which module drifted, and the restart —
    that is the whole cost the issue measured (time spent proving ``sdk.py`` was
    fine).
    """
    module, path = _probe_module(tmp_path)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    code_freshness.check()
    _rewrite(path)

    report = code_freshness.check()

    assert report["hint"] and "Restart the MCP server" in report["hint"]
    assert module.__name__ in report["hint"]  # names WHAT drifted
    assert report["process_started_at"].endswith("Z")  # and the age it served
    assert report["baseline_at"] is not None


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


def test_a_content_identical_rewrite_is_not_drift_and_re_arms(tmp_path, monkeypatch):
    """``git checkout`` / ``lane_stash.py pop`` / ``git rebase`` rewrite files
    byte-identically and bump the mtime — routine in this fleet. That is NOT
    drift (the process serves exactly the repo's bytes), so it must neither
    raise the alarm nor reappear on the next call."""
    module, path = _probe_module(tmp_path)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    code_freshness.check()

    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))  # mtime only

    report = code_freshness.check()
    assert report["stale"] is False, report
    assert report["mtime_only"] == 1

    again = code_freshness.check()  # re-armed: not re-reported, not re-hashed
    assert again["stale"] is False
    assert again["mtime_only"] == 0

    _rewrite(path)  # a REAL edit on top of the re-armed baseline
    assert code_freshness.check()["stale"] is True


def test_the_startup_graph_is_baselined_at_import_not_at_first_health_call():
    """The design rests on the baseline being taken when the process is young.

    Asserted in a SUBPROCESS, on the module map only: in-process, a test has
    already imported half the package, so it cannot distinguish "observed at
    import" from "observed by the first ``check()``". Measured before the fix:
    25 of the 58 modules a server loads were baselined only at first use — so an
    edit to ``tool_registry.py`` while the server ran read as clean, which is
    precisely the #9494 window.
    """
    code = (
        "import tortoise.mcp_server\n"
        "from tortoise import code_freshness as cf\n"
        # `_tracked_names` (not `tracked`) so no observe() runs here: the point
        # is what the module body recorded by itself.
        "print(chr(10).join(cf._tracked_names()))\n"
    )
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    out = subprocess.run([sys.executable, "-c", code], cwd=str(_ROOT), env=env,
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-2000:]
    tracked = set(out.stdout.split())

    assert "tortoise.sdk" in tracked
    assert "tortoise.tool_registry" in tracked
    assert "tortoise.mcp_auth" in tracked


def test_an_edit_dated_before_process_start_is_still_drift(tmp_path, monkeypatch):
    """Pins the per-module baseline against the naive alternative.

    A file whose mtime is OLDER than process start that changes after the
    baseline was taken must still be reported. "is the file newer than process
    start" cannot see it; "does this file differ from what I observed" can. The
    naive version would also false-alarm on a lazily imported module edited
    before its import — which is why the design is per-module.
    """
    module, path = _probe_module(tmp_path)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    _older(path)
    assert code_freshness.check()["stale"] is False

    path.write_text("VALUE = 1\nEXTRA_SYMBOL = 2\n")  # size changes, mtime stays old
    _older(path)
    assert os.stat(path).st_mtime < code_freshness._STARTED_AT

    report = code_freshness.check()

    assert report["stale"] is True, report
    assert report["changed"][0]["module"] == module.__name__


def test_the_real_sdk_is_tracked_and_its_row_is_repo_relative(monkeypatch):
    """The acceptance case, without touching the repository.

    Pretending ``tortoise.sdk`` was loaded a second earlier (a baseline entry,
    not an ``os.utime`` on the real file — a concurrent lane or a hard kill must
    never be able to leave the checked-out tree's mtime shifted) drives exactly
    the comparison a six-day-old server hits, and pins the row's spelling: the
    payload is served to tenants, so it must be ``tortoise/sdk.py``, never the
    host's absolute path.
    """
    import tortoise.sdk as sdk_module

    code_freshness.check()  # baseline the real, loaded graph
    _pretend_sdk_drifted()

    report = code_freshness.check()

    assert report["stale"] is True, report
    row = next(r for r in report["changed"] if r["module"] == "tortoise.sdk")
    assert row["file"] == "tortoise/sdk.py"
    assert not os.path.isabs(row["file"])
    assert "tortoise.sdk" in report["hint"]


def test_the_report_caps_the_row_list_and_the_hint_but_not_the_count(tmp_path, monkeypatch):
    """``changed`` AND the hint are bounded (both are served per health call and
    on every ``_safe`` error) while ``changed_count`` stays truthful.

    Capping ``changed`` alone bounded nothing: the one event that raises
    ``stale`` — a ``git checkout``/rebase moving dozens of ``tortoise/*.py`` —
    is also the event that makes the list long, so the string field grew with
    it while the structured field stayed at 20 (review P2).
    """
    probes = []
    for i in range(21):
        module, path = _probe_module(tmp_path, name=f"tortoise._probe_cap_{i}")
        monkeypatch.setitem(sys.modules, module.__name__, module)
        probes.append((module, path))
    code_freshness.check()
    for _module, path in probes:
        _rewrite(path)

    report = code_freshness.check()

    assert report["changed_count"] == 21
    assert len(report["changed"]) == 20
    # The hint names the SAME capped list and says how many it left out — never
    # the full 21 names, and never silently short of the truth.
    assert report["hint"].count("_probe_cap_") == 20
    assert "and 1 more" in report["hint"]
    assert code_freshness._MAX_LISTED == 20


def test_a_clean_process_reports_clean():
    """No drift, no alarm: the flag must not be a constant."""
    report = code_freshness.check()

    assert report["stale"] is False
    assert report["changed"] == []
    assert report["hint"] is None
    assert report["mtime_only"] == 0
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
    observability gap into a load-balancer outage) — the staleness rides its own
    field."""
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


def _pretend_sdk_drifted():
    """Make the baseline claim ``tortoise.sdk`` was loaded from OTHER bytes.

    A baseline entry, never an ``os.utime`` on the real file: shifting the
    checked-out tree's mtime is visible to every concurrent lane for the length
    of the test and can survive a hard kill, and it would be a lie anyway — the
    content would be identical, which the measurement correctly calls NOT drift.
    A different size and digest is exactly what a symbol added six days ago
    looks like to a process that loaded the file before it.
    """
    code_freshness.check()
    with code_freshness._lock:
        path, mtime_ns, size, _blake = code_freshness._baseline["tortoise.sdk"]
        code_freshness._baseline["tortoise.sdk"] = (
            path, mtime_ns - 10**9, size + 1, "0" * 32)


def test_a_stale_process_answers_a_symbol_failure_with_the_restart(monkeypatch):
    """The exact reported failure, on a measured-stale process: the answer must
    carry the drift and the fix instead of only the symbol name."""
    _pretend_sdk_drifted()

    def boom():
        raise ImportError(_SYMBOL_ERROR, name="tortoise.sdk")

    payload = _call_safe(boom, monkeypatch)

    assert payload["stale_server"] is True, payload
    assert _SYMBOL_ERROR in payload["error"]  # the original cause is kept
    assert "Restart the MCP server" in payload["error"]
    assert "tortoise.sdk" in payload["error"]


def test_the_restart_sentence_stands_alone_after_an_error_message(monkeypatch):
    """``HINT`` is served as a SUFFIX to an error string by ``_safe``.

    It used to say "the files above changed on disk since" — a referent that
    exists only in the health payload's ``changed`` list. After an
    ``ImportError`` there is nothing above it, so the one sentence the reader
    needs points at something they cannot see (review P2).
    """
    _pretend_sdk_drifted()

    def boom():
        raise ImportError(_SYMBOL_ERROR, name="tortoise.sdk")

    payload = _call_safe(boom, monkeypatch)

    assert " above" not in payload["error"]
    assert " above" not in code_freshness.HINT
    assert "Restart the MCP server" in payload["error"]


def test_a_clean_process_keeps_the_import_error_reading_as_a_code_defect(monkeypatch):
    """The other direction, and it matters more: a symbol miss on a FRESH process
    is a real code defect. Blaming it on staleness would be the same class of lie
    in reverse — so nothing is added."""
    def boom():
        raise ImportError(_SYMBOL_ERROR, name="tortoise.sdk")

    payload = _call_safe(boom, monkeypatch)

    assert "stale_server" not in payload
    assert payload["error"] == _SYMBOL_ERROR


def test_a_failure_naming_an_undrifted_module_is_not_relabelled(monkeypatch):
    """A stale process must not become a blanket explanation.

    With ``tortoise.sdk`` drifted, a symbol miss in a DIFFERENT module is still
    that module's own problem: attributing it to staleness would send the reader
    to a restart that cannot fix it.
    """
    _pretend_sdk_drifted()

    assert code_freshness.staleness_hint(
        ImportError("cannot import name 'X' from 'tortoise.quota'", name="tortoise.quota")
    ) is None


def test_a_missing_module_is_never_staleness(monkeypatch):
    """``ModuleNotFoundError`` is an ``ImportError``, and a stale process must not
    absorb it: a missing extra (``uv sync`` without it) is not fixed by a
    restart."""
    _pretend_sdk_drifted()

    exc = ModuleNotFoundError("No module named 'sentence_transformers'",
                              name="sentence_transformers")
    assert code_freshness.staleness_hint(exc) is None


def test_an_unrelated_attribute_error_is_never_staleness(monkeypatch):
    """``AttributeError`` has no provenance unless it names a module — and a
    ``NoneType`` failure answered with "restart the server" would steer the
    reader away from a real bug."""
    _pretend_sdk_drifted()

    assert code_freshness.staleness_hint(
        AttributeError("'NoneType' object has no attribute 'get'")) is None
    assert code_freshness.staleness_hint(
        AttributeError("module 'tortoise.sdk' has no attribute '_ledger_record'")
    ) is not None


def test_staleness_hint_ignores_non_symbol_failures(monkeypatch):
    """Only the symbol-shaped failures the issue measured."""
    _pretend_sdk_drifted()

    assert code_freshness.staleness_hint(RuntimeError(_SYMBOL_ERROR)) is None
    assert code_freshness.staleness_hint(ImportError(_SYMBOL_ERROR, name="tortoise.sdk")) is not None
