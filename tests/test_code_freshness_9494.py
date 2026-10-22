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
import time
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
    os.utime(path, (_OLD, _OLD))


def _subprocess_tracked(code: str) -> set[str]:
    """Run ``code`` in a FRESH interpreter and return its printed tokens.

    Fresh process on purpose: in-process, a test has already imported half the
    package, so it cannot distinguish "observed at import" from "observed by the
    first ``check()``". The child is also required to have imported THIS
    checkout — in an environment where ``tortoise`` resolves to a wheel or
    another lane's tree, every membership assertion below would be about code
    that is not this diff.
    """
    probe = (
        "import tortoise, sys\n"
        "print('TREE', tortoise.__file__)\n"
    ) + code
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    out = subprocess.run([sys.executable, "-c", probe], cwd=str(_ROOT), env=env,
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-2000:]
    tokens = set(out.stdout.split())
    tree = Path(out.stdout.split("TREE", 1)[1].split()[0])
    assert Path(tree).resolve().is_relative_to(_ROOT), (
        f"the child imported {tree}, not this checkout ({_ROOT})")
    return tokens


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
    tracked = _subprocess_tracked(code)

    assert "tortoise.sdk" in tracked
    assert "tortoise.tool_registry" in tracked
    assert "tortoise.mcp_auth" in tracked


def test_the_boot_deep_observe_hashes_what_the_import_already_baselined():
    """``observe(deep=True)`` must upgrade entries the shallow import-time call
    already recorded — most importantly ``tortoise.sdk``, loaded BEFORE
    ``monitoring`` pulls ``code_freshness`` in.

    Without the upgrade the boot hash covers only the modules that arrive after
    the import, so a byte-identical rewrite of ``sdk.py`` before the first health
    call would read as unprovable drift for the process's whole life.
    """
    tracked = _subprocess_tracked(
        "import tortoise.mcp_server\n"
        "from tortoise import code_freshness as cf\n"
        "missing = [n for n, e in cf._baseline.items() if e[3] is None]\n"
        "print('UNHASHED=' + str(len(missing)))\n"
        "print('SDK_HASHED=' + str(cf._baseline['tortoise.sdk'][3] is not None))\n"
    )

    assert "UNHASHED=0" in tracked, tracked
    assert "SDK_HASHED=True" in tracked


def test_the_module_import_itself_observes_its_caller():
    """``monitoring`` imports this module at import time and MUST get a baseline
    from that import alone.

    Pinned separately from the ``mcp_server`` case above because the two calls
    cover different entrypoints: ``monitoring.serve_health`` (and any process
    that only imports ``monitoring``) has no later ``observe()``, so deleting the
    module-level call would leave its baseline to the first health call — the
    #9494 window, with a docstring claiming otherwise.

    The probe must NOT import ``code_freshness`` itself: that import would run the
    module-level ``observe()`` on the probe's own line, with everything the import
    already loaded, so every assertion below would hold even if ``monitoring`` had
    dropped its import — the mutation this test exists for would survive. Reading
    the module out of ``sys.modules`` instead makes a missing (or lazy) import in
    ``monitoring`` fail loudly here (``KeyError``) rather than one health call late.
    """
    tracked = _subprocess_tracked(
        "import tortoise.monitoring, sys\n"
        "cf = sys.modules['tortoise.code_freshness']"
        "  # KeyError unless monitoring imported it at module level\n"
        "print(chr(10).join(cf._tracked_names()))\n"
        "print('BASELINE_AT=' + str(cf._baseline_at is not None))\n"
    )

    assert "tortoise.monitoring" in tracked
    assert "tortoise.code_freshness" in tracked
    assert "tortoise.env_truthy" in tracked
    assert "BASELINE_AT=True" in tracked


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
    code_freshness.check()  # baseline the real, loaded graph
    _pretend_sdk_drifted()

    report = code_freshness.check()

    assert report["stale"] is True, report
    row = next(r for r in report["changed"] if r["module"] == "tortoise.sdk")
    assert row["file"] == "tortoise/sdk.py"  # repo-relative: never the host path
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


def test_a_deferred_digest_is_reported_stale_but_marked_unverified(tmp_path, monkeypatch, caplog):
    """When the process never confirmed which bytes it loaded, a stat mismatch
    is still STALE — the failure directions are not symmetric.

    A conservative false alarm costs a restart; a false pass is the #9494 lie.
    The row says which one it is (`content_verified: False`), and the payload
    counts them, so an operator can tell "measured" from "cannot prove".
    """
    module, path = _probe_module(tmp_path)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    code_freshness.observe()  # SHALLOW: no digest yet
    _rewrite(path)

    report = code_freshness.check()

    assert report["stale"] is True
    assert report["changed"][0]["content_verified"] is False
    assert report["unverified"] == 1


def test_observe_never_reads_source_files(tmp_path, monkeypatch):
    """Importing this module must cost ``stat`` calls only.

    The digest is deferred to the first ``check()`` precisely because the
    observation runs on every serving entrypoint's import (this repo's startup
    graph is ~6 MB).
    """
    code_freshness._reset_for_tests()

    def _boom(_path):
        raise AssertionError("observe() read a source file")

    monkeypatch.setattr(code_freshness, "_digest", _boom)
    assert code_freshness.observe() > 0
    monkeypatch.undo()

    report = code_freshness.check()  # the digest IS established here
    assert report["stale"] is False
    with code_freshness._lock:
        assert code_freshness._baseline["tortoise.sdk"][3] is not None


def test_a_persistent_drift_is_not_re_read_on_every_check(tmp_path, monkeypatch):
    """``check()`` runs on every health probe AND on every failing tool call.

    A drifted module must cost one read, then a ``stat`` — never one multi-MB
    read per probe for the rest of the process's life.
    """
    module, path = _probe_module(tmp_path)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    code_freshness.check()  # establish digests for the clean graph
    _rewrite(path)

    reads = []
    real_digest = code_freshness._digest
    monkeypatch.setattr(code_freshness, "_digest",
                        lambda p: reads.append(p) or real_digest(p))

    assert code_freshness.check()["stale"] is True
    assert code_freshness.check()["stale"] is True
    assert code_freshness.check()["stale"] is True

    assert reads.count(str(path)) == 1, reads


def test_one_warning_per_drift_not_one_per_health_call(tmp_path, monkeypatch, caplog):
    """A six-day-old stale server is probed continuously: the log must say it
    once per drift, not once per probe."""
    module, path = _probe_module(tmp_path)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    code_freshness.check()
    _rewrite(path)

    with caplog.at_level("WARNING", logger="tortoise.code_freshness"):
        code_freshness.check()
        code_freshness.check()
        code_freshness.check()

    records = [r for r in caplog.records if r.name == "tortoise.code_freshness"]
    assert len(records) == 1, [r.getMessage() for r in records]
    assert module.__name__ in records[0].getMessage()


def test_baseline_at_records_the_FIRST_observation_not_the_latest(tmp_path, monkeypatch):
    """``baseline_at`` is the process's baseline age; a later ``observe()`` that
    picks up a lazily imported module must not move it."""
    code_freshness.check()
    first = code_freshness._baseline_at
    assert first is not None

    late, _path = _probe_module(tmp_path, name="tortoise._probe_late_9494")
    monkeypatch.setitem(sys.modules, late.__name__, late)
    code_freshness.check()

    assert late.__name__ in code_freshness.tracked()  # the late module IS added
    assert code_freshness._baseline_at == first        # but the age does not move


def test_the_warned_set_is_bounded_and_never_gates_the_verdict(tmp_path, monkeypatch):
    """The WARNING channel is capped; the VERDICT is not.

    A process that drifts hundreds of times must not grow the set without limit —
    and, more important, hitting the cap may only suppress the log line, never
    the ``stale`` answer.
    """
    monkeypatch.setattr(code_freshness, "_WARNED_CAP", 1)
    probes = []
    for i in range(3):
        module, path = _probe_module(tmp_path, name=f"tortoise._probe_cap2_{i}")
        monkeypatch.setitem(sys.modules, module.__name__, module)
        probes.append((module, path))
    code_freshness.check()
    for _module, path in probes:
        _rewrite(path)

    report = code_freshness.check()

    assert report["changed_count"] == 3   # the verdict is complete
    assert len(code_freshness._warned) == 1  # the log channel is capped


class _SlowSet(set):
    """A set that HESITATES inside ``__contains__``.

    The real race window between "is this drift already warned about" and "mark
    it warned" is a few bytecodes, and eight threads are staggered long before
    they reach it by the stat/hash loop — measured: 150 trials never produced a
    duplicate, i.e. a plain barrier test passes with the lock REMOVED. The delay
    makes every thread sit inside the window simultaneously, so the locked
    version still emits one record while the unlocked one emits eight.
    """

    def __contains__(self, item):
        found = super().__contains__(item)
        time.sleep(0.05)
        return found


def test_concurrent_checks_emit_one_warning(tmp_path, monkeypatch, caplog):
    """A burst of health probes must not duplicate the STALE PROCESS line — the
    invariant the guarded membership test exists to hold (``check()`` is served
    by a threaded health server and by concurrent tool calls)."""
    import threading

    module, path = _probe_module(tmp_path)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    code_freshness.check()
    _rewrite(path)
    monkeypatch.setattr(code_freshness, "_warned", _SlowSet())

    barrier = threading.Barrier(8)
    errors = []

    def probe():
        try:
            barrier.wait(timeout=10)
            code_freshness.check()
        except Exception as exc:  # surfaced by the errors assertion
            errors.append(exc)

    with caplog.at_level("WARNING", logger="tortoise.code_freshness"):
        threads = [threading.Thread(target=probe, daemon=True) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

    assert errors == []
    assert not any(t.is_alive() for t in threads)  # a hung thread cannot pass
    records = [r for r in caplog.records if r.name == "tortoise.code_freshness"]
    assert len(records) == 1, [r.getMessage() for r in records]


def test_a_rewrite_inside_the_hash_window_is_never_reported_clean(tmp_path, monkeypatch):
    """The boot digest must be sampled only if the stat held ACROSS the read.

    A stat check and a separate read leave a TOCTOU window, and the writers that
    land in it are the rewrites this digest exists to recognise (``git
    checkout``, ``lane_stash.py pop``, ``git rebase``). The dangerous pair is
    "stat of A, digest of B": it *matches* on the next ``check()``, so the file
    takes the content-identical branch and the health payload answers
    ``stale: false`` while the process is still running A — the #9494 failure,
    produced by its own fix.
    """
    module, path = _probe_module(tmp_path)          # the process "loaded" A
    monkeypatch.setitem(sys.modules, module.__name__, module)
    real_digest = code_freshness._digest
    raced = []

    def racing_digest(target):
        # A rewrite lands between the caller's stat and this read.
        if Path(target) == path and not raced:
            raced.append(True)
            path.write_text("VALUE = 1\nEXTRA_SYMBOL = 9\n")
            st = os.stat(path)
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
        return real_digest(target)

    monkeypatch.setattr(code_freshness, "_digest", racing_digest)
    code_freshness.observe(deep=True)               # boot: the raced sample
    assert raced, "the race window was not entered — the test proves nothing"
    monkeypatch.undo()

    report = code_freshness.check()
    assert report["stale"] is True, report           # never a clean answer

    # And it stays stale: a bad sample must not be re-armed into a clean one.
    assert code_freshness.check()["stale"] is True


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
    cause, _, hint = payload["error"].partition("\n\n")
    assert cause == _SYMBOL_ERROR
    # The HINT half must name the drifted module. Asserting the whole string
    # would pass on the raw exception text alone (it contains 'tortoise.sdk'),
    # pinning nothing about what `_safe` adds.
    assert "Restart the MCP server" in hint
    assert "imported tortoise.sdk at start" in hint


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

    # Names the DRIFTED module, so the generic ImportError path WOULD return a
    # hint: only the explicit exclusion keeps this None.
    exc = ModuleNotFoundError("No module named 'tortoise.sdk'", name="tortoise.sdk")
    assert code_freshness.staleness_hint(exc) is None
    assert code_freshness.staleness_hint(
        ModuleNotFoundError("No module named 'sentence_transformers'",
                            name="sentence_transformers")) is None


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
    # No usable `.name` (a hand-raised ImportError): the MESSAGE is parsed for
    # the module, so the hint still has provenance.
    assert code_freshness.staleness_hint(ImportError(_SYMBOL_ERROR)) is not None
