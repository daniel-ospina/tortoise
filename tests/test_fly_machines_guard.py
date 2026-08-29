"""Hermetic tests for .github/scripts/check-fly-machines-guard.py (#1896).

The script reads FLY_MACHINES_FILE / FLY_TOML / FLY_APP / FLY_API_URL /
FLY_API_TOKEN / FLY_GUARD_MAX_ATTEMPTS env seams so tests run with zero
network: FLY_MACHINES_FILE points at a fixture machines-list JSON, FLY_TOML
at a fixture fly.toml, and FLY_API_URL at a local stub HTTP server for the
live-API path.

Fixture provenance: the machine shape mirrors the verbatim live response
captured 2026-08-28 (``flyctl machines list -a tortoise-y4mjjq --json``):
``id`` / ``name`` / ``state`` / ``config.metadata.fly_process_group`` /
``events[].type`` / ``events[].request.exit_event.{exit_code,requested_stop,
restarting}`` / ``events[].request.restart_count``. The crash-loop conditions
match flyctl source (internal/machine/leasable_machine.go:293-311 —
first 'exit' event, restart_count > 1, exit_code != 0, !requested_stop) with
the documented #1896 deviation: the ``restarting`` condition is dropped so
the stopped-after-crash-loop terminal state is flagged.

Exit contract (fail-closed, mirrors check-migration-drift): 0 clean,
1 guard failed (orphan or traffic-group crash-loop), 2 could-not-determine.
"""
from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".github" / "scripts" / "check-fly-machines-guard.py"

_FIXTURE_TMP = None


def _tmp() -> Path:
    """One temp dir per test-run (cleaned by the OS)."""
    global _FIXTURE_TMP
    if _FIXTURE_TMP is None:
        _FIXTURE_TMP = Path(tempfile.mkdtemp(prefix="fly-guard-"))
    return _FIXTURE_TMP


FIXTURES = _tmp()

# Ambient seams a developer might have exported — popped for full hermeticity.
_AMBIENT = ("FLY_API_TOKEN", "FLY_MACHINES_FILE", "FLY_TOML", "FLY_APP",
            "FLY_API_URL", "FLY_GUARD_MAX_ATTEMPTS")


def _machine(mid: str, group: str | None, events: list[dict] | None = None,
             state: str | None = None, incomplete: dict | None = None,
             metadata: dict | None = None, omit_events: bool = False) -> dict:
    """Build a fixture machine dict shaped like the live Fly Machines API.

    group=None → no fly_process_group metadata (empty group = orphan).
    metadata overrides the auto-built metadata dict (e.g. legacy
    ``process_group`` fallback key, or empty {}). NOTE: when ``incomplete``
    is given, ``config`` is popped (the API's host-unreachable shape) and
    ``metadata`` is ignored — ``incomplete_config`` drives the verdict.
    ``host_status`` is set for fixture realism only; the script keys off
    config presence, not host_status.

    Healthy machines carry an explicit ``events: []`` (the API marshals the
    array with omitempty — an absent key means an empty array); pass
    ``omit_events=True`` to construct the absent-key drift shape.
    """
    md = {"fly_process_group": group} if group is not None else {}
    if metadata is not None:
        md = metadata
    m: dict = {"id": mid, "name": f"name-{mid}", "config": {"metadata": md}}
    if events is not None:
        m["events"] = events
    elif not omit_events:
        m["events"] = []
    if state is not None:
        m["state"] = state
    if incomplete is not None:
        m["incomplete_config"] = incomplete
        m["host_status"] = "unreachable"
        m.pop("config", None)
    return m


def _exit_event(restart_count: int = 1, exit_code: int = 0,
                requested_stop: bool = False, restarting: bool = True) -> dict:
    """An 'exit' event carrying the flyctl-read crash signature fields."""
    return {
        "type": "exit",
        "request": {
            "restart_count": restart_count,
            "exit_event": {
                "exit_code": exit_code,
                "requested_stop": requested_stop,
                "restarting": restarting,
            },
        },
    }


def _write_fly_toml(processes: list[str] | None, app: str = "tortoise-y4mjjq") -> Path:
    d = FIXTURES / "conf"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "fly.toml"
    body = f'app = "{app}"\nprimary_region = "iad"\n'
    if processes is not None:
        body += "\n[processes]\n"
        for p in processes:
            body += f'{p} = "{p}-cmd"\n'
    path.write_text(body)
    return path


def _write_machines(machines: list[dict]) -> Path:
    d = FIXTURES / "mach"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "machines.json"
    path.write_text(json.dumps(machines))
    return path


def _run(env_extra: dict[str, str], machines: list[dict],
         processes: list[str] | None = None) -> subprocess.CompletedProcess:
    """Run the script with FLY_MACHINES_FILE + FLY_TOML pointed at fixtures."""
    machines_file = _write_machines(machines)
    toml = _write_fly_toml(processes)
    env = dict(os.environ)
    for k in _AMBIENT:
        env.pop(k, None)
    env.update({
        "FLY_MACHINES_FILE": str(machines_file),
        "FLY_TOML": str(toml),
    })
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, env=env, cwd=REPO_ROOT,
    )


# ── clean fleet ────────────────────────────────────────────────────────────


def test_clean_single_machine_exit_zero():
    # Today's fleet: one Launch-managed 'app' machine, no exit events. Pin the
    # count so a fleet-scoping regression (machine silently skipped) fails.
    r = _run({}, [_machine("8654509b634758", "app")])
    assert r.returncode == 0, r.stderr
    assert "1 active machine" in r.stdout, r.stdout
    assert "all Launch-managed and not crash-looping" in r.stdout, r.stdout


def test_empty_fleet_clean():
    # Zero machines → nothing to guard (the deploy creates machines).
    r = _run({}, [])
    assert r.returncode == 0, r.stderr
    assert "0 active machines" in r.stdout


# ── orphan detection (flyctl ProcessGroup(): fly_process_group → process_group → '') ──


def test_orphan_empty_process_group_blocks():
    # The incident class: empty/absent process group = NOT part of Fly Launch.
    r = _run({}, [_machine("080d6e1a0d2928", None)])
    assert r.returncode == 1, r.stderr
    assert "080d6e1a0d2928" in r.stdout
    assert "destroy" in r.stdout.lower()


def test_orphan_metadata_absent_blocks():
    # config present but no metadata key at all → empty group → orphan.
    r = _run({}, [{"id": "no-meta", "name": "x", "config": {}}])
    assert r.returncode == 1, r.stderr
    assert "no-meta" in r.stdout


def test_orphan_unknown_process_group_blocks():
    # A group outside the allowed set is fail-closed ORPHAN.
    r = _run({}, [_machine("abc123", "worker")])
    assert r.returncode == 1, r.stderr
    assert "worker" in r.stdout


def test_processes_section_expands_allowed():
    # fly.toml [processes] keys join the allowed set.
    r = _run({}, [_machine("abc123", "worker")], processes=["web", "worker"])
    assert r.returncode == 0, r.stderr


def test_processes_section_plus_internal_groups_allowed():
    # Allowed set = [processes] keys UNION internal groups — a release-command
    # machine stays legit even when [processes] is present.
    r = _run({}, [_machine("rel123", "fly_app_release_command"),
                  _machine("con123", "fly_app_console")],
             processes=["web", "worker"])
    assert r.returncode == 0, r.stderr


def test_process_group_legacy_key_fallback():
    # flyctl ProcessGroup(): fly_process_group → process_group → '' — the
    # legacy key resolves the group (not an orphan).
    r = _run({}, [_machine("legacy1", None,
                           metadata={"process_group": "app"})])
    assert r.returncode == 0, r.stderr


def test_process_group_legacy_key_rogue_blocks():
    r = _run({}, [_machine("legacy2", None,
                           metadata={"process_group": "rogue"})])
    assert r.returncode == 1, r.stderr
    assert "rogue" in r.stdout


def test_release_command_machine_allowed():
    # Deploy-time release-command machine — legit transient, must not flag.
    r = _run({}, [_machine("rel123", "fly_app_release_command")])
    assert r.returncode == 0, r.stderr


# ── crash-loop detection (flyctl heuristic + documented deviation) ─────────


def test_crash_loop_blocks():
    r = _run({}, [_machine("crash1", "app", [_exit_event(restart_count=3, exit_code=127)])])
    assert r.returncode == 1, r.stderr
    assert "CRASH-LOOP" in r.stdout


def test_requested_stop_not_crash_loop():
    # Operator-stopped (fly machines stop) — requested_stop=true, not a loop.
    r = _run({}, [_machine("stop1", "app",
                           [_exit_event(restart_count=3, exit_code=127, requested_stop=True)])])
    assert r.returncode == 0, r.stderr


def test_restart_count_one_not_crash_loop():
    # First crash is assumed mid-loop; the deploy replaces the machine.
    r = _run({}, [_machine("r1", "app", [_exit_event(restart_count=1, exit_code=1)])])
    assert r.returncode == 0, r.stderr


def test_first_crash_not_flagged():
    # Boundary pinned: restart_count=1 with a 'restart' event in the history
    # (mid-loop) is NOT flagged — the boundary at >1 is intentional; a
    # machine on its first crash is deploy-replaced (self-healing).
    events = [
        {"type": "restart", "request": {}, "source": "flyd", "timestamp": 1},
        _exit_event(restart_count=1, exit_code=1),
    ]
    r = _run({}, [_machine("r2", "app", events)])
    assert r.returncode == 0, r.stderr
    assert "CRASH-LOOP" not in r.stdout


def test_missing_restart_count_go_zero():
    # Go zero-value semantics: absent restart_count → 0 → no flag.
    ev = _exit_event(restart_count=3, exit_code=127)
    del ev["request"]["restart_count"]
    r = _run({}, [_machine("r3", "app", [ev])])
    assert r.returncode == 0, r.stderr


def test_missing_exit_code_go_zero():
    # Go zero-value semantics: absent exit_code → 0 → no flag.
    ev = _exit_event(restart_count=3, exit_code=127)
    del ev["request"]["exit_event"]["exit_code"]
    r = _run({}, [_machine("c1", "app", [ev])])
    assert r.returncode == 0, r.stderr


def test_exit_code_zero_not_crash_loop():
    r = _run({}, [_machine("c0", "app", [_exit_event(restart_count=3, exit_code=0)])])
    assert r.returncode == 0, r.stderr


def test_stopped_after_crash_loop_blocks():
    # DEVIATION: flyctl requires restarting=true; the guard DROPs it so the
    # terminal stopped-after-crash-loop state (max restart count reached →
    # stopped) is flagged.
    r = _run({}, [_machine("term1", "app",
                           [_exit_event(restart_count=3, exit_code=127, restarting=False)])])
    assert r.returncode == 1, r.stderr
    assert "CRASH-LOOP" in r.stdout


def test_stopped_crash_loop_still_flagged():
    # Boundary: state 'stopped' is NOT skipped (only destroyed/destroying are).
    r = _run({}, [_machine("stop2", "app",
                           [_exit_event(restart_count=3, exit_code=127, restarting=False)],
                           state="stopped")])
    assert r.returncode == 1, r.stderr
    assert "CRASH-LOOP" in r.stdout


def test_destroyed_machine_ignored():
    # flyctl IsActive(): destroyed/destroying machines are skipped — a
    # retained destroyed machine with crash history must not flag deploys.
    for state in ("destroyed", "destroying"):
        r = _run({}, [_machine("dead1", "app",
                               [_exit_event(restart_count=3, exit_code=127)],
                               state=state)])
        assert r.returncode == 0, r.stderr


def test_release_command_crash_not_crash_flagged():
    # Internal groups are orphan-checked only — a crashed release-command
    # machine is a deploy-surface problem (the deploy reports it), not a
    # traffic-serving loop; crash-flagging it would deadlock the fix deploy.
    r = _run({}, [_machine("rel9", "fly_app_release_command",
                           [_exit_event(restart_count=3, exit_code=127)])])
    assert r.returncode == 0, r.stderr


def test_most_recent_exit_wins():
    # Events are newest-first; the FIRST 'exit' event is the most recent exit.
    recent_clean = _exit_event(restart_count=1, exit_code=0)
    older_crash = _exit_event(restart_count=3, exit_code=127)
    r = _run({}, [_machine("multi1", "app", [recent_clean, older_crash])])
    assert r.returncode == 0, r.stderr


def test_events_absent_warns_not_fails():
    # fly-go marshals events with omitempty — an absent key means an empty
    # array (legitimately event-less machine). The guard warns (drift tripwire)
    # but does NOT fail the deploy (false-positive risk).
    r = _run({}, [_machine("noev1", "app", omit_events=True)])
    assert r.returncode == 0, r.stderr
    assert "no 'events' array" in r.stdout
    assert "1 active machine" in r.stdout


def test_incident_replay():
    # Exact 2026-08-28 incident: orphan (empty group, crash-looped to stop)
    # + healthy app machine → exit 1 naming the orphan.
    orphan = _machine("080d6e1a0d2928", None,
                      [_exit_event(restart_count=3, exit_code=127, restarting=False)],
                      state="stopped")
    healthy = _machine("8654509b634758", "app")
    r = _run({}, [healthy, orphan])
    assert r.returncode == 1, r.stderr
    assert "080d6e1a0d2928" in r.stdout


def test_multiple_violations_aggregated():
    # One orphan + one crash-looping traffic machine → both named, count summed.
    orphan = _machine("orphan-1", None)
    crash = _machine("crash-1", "app", [_exit_event(restart_count=3, exit_code=127)])
    r = _run({}, [orphan, crash])
    assert r.returncode == 1, r.stderr
    assert "orphan-1" in r.stdout and "crash-1" in r.stdout
    assert "2 violation(s)" in r.stdout
    assert "fix before deploy" in r.stdout


# ── host-unreachable shape (fly-go GetConfig() incomplete_config fallback) ──


def test_incomplete_config_fallback_ok():
    # host_status != ok → API omits config, returns incomplete_config (fly-go
    # GetConfig() semantics). Orphan check via incomplete_config metadata;
    # crash-loop check skipped for that machine (warn) — exit 0 when clean.
    r = _run({}, [_machine("host1", "app", incomplete={"metadata": {"fly_process_group": "app"}})])
    assert r.returncode == 0, r.stderr
    assert "host unreachable" in r.stdout


def test_incomplete_config_skips_crash_check():
    # The critical guard: stale crash events on a host-unreachable machine
    # must NOT flag it — crash-loop evaluation is skipped (warn), exit 0.
    r = _run({}, [_machine("host3", "app",
                           [_exit_event(restart_count=3, exit_code=127)],
                           incomplete={"metadata": {"fly_process_group": "app"}})])
    assert r.returncode == 0, r.stderr
    assert "host unreachable" in r.stdout
    assert "CRASH-LOOP" not in r.stdout


def test_incomplete_config_empty_group_blocks():
    # Incomplete config with empty metadata → empty group → orphan.
    r = _run({}, [_machine("host2", None, incomplete={"metadata": {}})])
    assert r.returncode == 1, r.stderr
    assert "host2" in r.stdout


def test_config_wins_when_both_present():
    # Both config AND incomplete_config → config wins (no host warning, crash
    # loop evaluated normally).
    m = _machine("both1", "app")
    m["incomplete_config"] = {"metadata": {}}
    r = _run({}, [m])
    assert r.returncode == 0, r.stderr
    assert "host unreachable" not in r.stdout


# ── fail-closed shape validation (API drift must exit 2, never silently pass) ──


def _assert_exit2(r: subprocess.CompletedProcess) -> None:
    assert r.returncode == 2, r.stderr
    assert "cannot determine" in r.stderr, r.stderr


def test_machine_missing_config_exit_2():
    # Neither config nor incomplete_config → cannot determine → exit 2.
    r = _run({}, [{"id": "ghost1", "name": "x"}])
    _assert_exit2(r)


def test_machine_entry_not_dict_exit_2():
    r = _run({}, ["not-a-machine"])
    _assert_exit2(r)


def test_config_not_dict_exit_2():
    r = _run({}, [{"id": "c1", "config": "nope"}])
    _assert_exit2(r)


def test_incomplete_config_not_dict_exit_2():
    r = _run({}, [{"id": "i1", "incomplete_config": "nope"}])
    _assert_exit2(r)


def test_events_not_list_exit_2():
    r = _run({}, [{"id": "e1", "config": {"metadata": {"fly_process_group": "app"}},
                   "events": {"type": "start"}}])
    _assert_exit2(r)


def test_events_entry_not_dict_exit_2():
    r = _run({}, [_machine("e2", "app", ["not-an-event"])])
    _assert_exit2(r)


def test_metadata_not_dict_exit_2():
    r = _run({}, [{"id": "m1", "config": {"metadata": "nope"}}])
    _assert_exit2(r)


def test_machine_bad_exit_event_exit_2():
    # First 'exit' event lacks request.exit_event → cannot determine → exit 2.
    r = _run({}, [_machine("bad1", "app", [{"type": "exit", "request": {}}])])
    _assert_exit2(r)


def test_exit_event_request_not_dict_exit_2():
    r = _run({}, [_machine("bad2", "app", [{"type": "exit", "request": "x"}])])
    _assert_exit2(r)


def test_restart_count_string_exit_2():
    ev = _exit_event(restart_count=3, exit_code=127)
    ev["request"]["restart_count"] = "3"
    r = _run({}, [_machine("t1", "app", [ev])])
    _assert_exit2(r)


def test_restart_count_bool_exit_2():
    ev = _exit_event(restart_count=3, exit_code=127)
    ev["request"]["restart_count"] = True
    r = _run({}, [_machine("t2", "app", [ev])])
    _assert_exit2(r)


def test_exit_code_string_exit_2():
    ev = _exit_event(restart_count=3, exit_code=127)
    ev["request"]["exit_event"]["exit_code"] = "127"
    r = _run({}, [_machine("t3", "app", [ev])])
    _assert_exit2(r)


def test_exit_code_bool_exit_2():
    ev = _exit_event(restart_count=3, exit_code=127)
    ev["request"]["exit_event"]["exit_code"] = True
    r = _run({}, [_machine("t4", "app", [ev])])
    _assert_exit2(r)


def test_requested_stop_string_exit_2():
    # Fail-closed: a drifted non-bool requested_stop (the string "false" is
    # truthy → would suppress the crash signature) must exit 2, never
    # silently pass as an operator stop.
    ev = _exit_event(restart_count=3, exit_code=127)
    ev["request"]["exit_event"]["requested_stop"] = "false"
    r = _run({}, [_machine("t5", "app", [ev])])
    _assert_exit2(r)


def test_requested_stop_absent_go_zero():
    # Go zero-value: absent requested_stop → False → the crash signature
    # holds (not an operator stop) → flagged.
    ev = _exit_event(restart_count=3, exit_code=127)
    del ev["request"]["exit_event"]["requested_stop"]
    r = _run({}, [_machine("t6", "app", [ev])])
    assert r.returncode == 1, r.stderr
    assert "CRASH-LOOP" in r.stdout


def test_state_not_string_exit_2():
    # A non-string state (e.g. a list) would crash the destroyed/destroying
    # membership check — fail-closed to exit 2 instead of a traceback.
    r = _run({}, [{"id": "s1", "state": ["destroyed"],
                   "config": {"metadata": {"fly_process_group": "app"}}}])
    _assert_exit2(r)


def test_incomplete_config_metadata_not_dict_exit_2():
    r = _run({}, [{"id": "i2", "incomplete_config": {"metadata": "nope"}}])
    _assert_exit2(r)


# ── could-not-determine (exit 2, fail-closed) ──────────────────────────────


def test_missing_token_exit_2():
    # No FLY_MACHINES_FILE + no FLY_API_TOKEN → must hit the API → exit 2.
    env = dict(os.environ)
    for k in _AMBIENT:
        env.pop(k, None)
    env.update({"FLY_TOML": str(_write_fly_toml(None))})
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                       env=env, cwd=REPO_ROOT)
    assert r.returncode == 2, r.stdout
    assert "FLY_API_TOKEN" in r.stderr


def test_missing_fly_toml_exit_2():
    env = dict(os.environ)
    for k in _AMBIENT:
        env.pop(k, None)
    env.update({"FLY_TOML": "/nonexistent/fly.toml"})
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                       env=env, cwd=REPO_ROOT)
    assert r.returncode == 2, r.stdout
    assert "fly.toml not found" in r.stderr


def test_malformed_fly_toml_exit_2():
    d = FIXTURES / "conf"
    d.mkdir(parents=True, exist_ok=True)
    bad = d / "bad.toml"
    bad.write_text("app = [unclosed")
    env = dict(os.environ)
    for k in _AMBIENT:
        env.pop(k, None)
    env.update({"FLY_TOML": str(bad)})
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                       env=env, cwd=REPO_ROOT)
    assert r.returncode == 2, r.stdout
    assert "cannot determine" in r.stderr


def test_machines_file_invalid_json_exit_2():
    d = FIXTURES / "mach"
    d.mkdir(parents=True, exist_ok=True)
    bad = d / "garbage.json"
    bad.write_text("{ not json")
    env = dict(os.environ)
    for k in _AMBIENT:
        env.pop(k, None)
    env.update({"FLY_TOML": str(_write_fly_toml(None)), "FLY_MACHINES_FILE": str(bad)})
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                       env=env, cwd=REPO_ROOT)
    assert r.returncode == 2, r.stdout
    assert "cannot determine" in r.stderr


def test_machines_file_non_list_exit_2():
    d = FIXTURES / "mach"
    d.mkdir(parents=True, exist_ok=True)
    obj = d / "obj.json"
    obj.write_text('{"a": 1}')
    env = dict(os.environ)
    for k in _AMBIENT:
        env.pop(k, None)
    env.update({"FLY_TOML": str(_write_fly_toml(None)), "FLY_MACHINES_FILE": str(obj)})
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                       env=env, cwd=REPO_ROOT)
    assert r.returncode == 2, r.stdout
    assert "not a JSON list" in r.stderr


def test_machines_file_missing_exit_2():
    # OSError arm of the machines-file read → exit 2.
    env = dict(os.environ)
    for k in _AMBIENT:
        env.pop(k, None)
    env.update({"FLY_TOML": str(_write_fly_toml(None)),
                "FLY_MACHINES_FILE": "/nonexistent/machines.json"})
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                       env=env, cwd=REPO_ROOT)
    assert r.returncode == 2, r.stdout
    assert "cannot determine" in r.stderr


def test_fly_toml_is_directory_exit_2():
    # OSError arm of load_fly_toml (e.g. FLY_TOML pointing at a directory).
    env = dict(os.environ)
    for k in _AMBIENT:
        env.pop(k, None)
    env.update({"FLY_TOML": str(FIXTURES / "conf")})  # a directory
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                       env=env, cwd=REPO_ROOT)
    assert r.returncode == 2, r.stdout
    assert "cannot determine" in r.stderr


def test_malformed_processes_exit_2():
    # A top-level 'processes' that isn't a table → fail-closed exit 2 (never
    # silently fall back to the default allowed set).
    d = FIXTURES / "conf"
    d.mkdir(parents=True, exist_ok=True)
    bad = d / "bad-processes.toml"
    bad.write_text('app = "tortoise-y4mjjq"\nprocesses = "not-a-table"\n')
    env = dict(os.environ)
    for k in _AMBIENT:
        env.pop(k, None)
    env.update({"FLY_TOML": str(bad)})
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                       env=env, cwd=REPO_ROOT)
    assert r.returncode == 2, r.stdout
    assert "cannot determine" in r.stderr


def test_blank_app_exit_2():
    env = dict(os.environ)
    for k in _AMBIENT:
        env.pop(k, None)
    env.update({"FLY_TOML": str(_write_fly_toml(None, app="")), "FLY_APP": ""})
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                       env=env, cwd=REPO_ROOT)
    assert r.returncode == 2, r.stdout
    assert "Fly app name" in r.stderr


# ── live-API path via a local stub HTTP server (FLY_API_URL seam) ──────────


class _StubHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server API)
        # Stateful: consume one (status, body) response per request.
        responses = getattr(self.server, "responses", None)
        if responses:
            status, body = responses.pop(0)
        else:
            status = getattr(self.server, "status", 200)
            body = getattr(self.server, "body", "[]")
        self.server.requests.append(
            {"path": self.path, "authorization": self.headers.get("Authorization")}
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):  # silence
        pass


class _StubServer:
    """Tiny threaded HTTP server returning configured status/body responses.

    ``responses`` is a list of (status, body) tuples consumed per request
    (for retry-then-success tests); otherwise fixed status/body. Records
    every request (path + Authorization header) on ``.requests``.
    """

    def __init__(self, status: int = 200, body: str = "[]",
                 responses: list[tuple[int, str]] | None = None):
        self._srv = http.server.HTTPServer(("127.0.0.1", 0), _StubHandler)
        self._srv.status = status
        self._srv.body = body
        self._srv.responses = responses
        self._srv.requests = []
        self.port = self._srv.server_address[1]
        self._t = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._t.start()

    @property
    def requests(self) -> list[dict]:
        return self._srv.requests

    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


def _run_live(env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    for k in _AMBIENT:
        env.pop(k, None)
    env.update({"FLY_TOML": str(_write_fly_toml(None)), "FLY_API_TOKEN": "test-token"})
    env.update(env_extra)
    return subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                          env=env, cwd=REPO_ROOT)


def test_live_api_clean():
    srv = _StubServer(status=200, body=json.dumps([_machine("8654509b634758", "app")]))
    try:
        r = _run_live({"FLY_API_URL": srv.url()})
    finally:
        srv.close()
    assert r.returncode == 0, r.stderr
    assert "1 active machine" in r.stdout, r.stdout
    # The live seam must prove the right request went out.
    assert len(srv.requests) == 1, srv.requests
    assert srv.requests[0]["path"] == "/v1/apps/tortoise-y4mjjq/machines", srv.requests
    assert srv.requests[0]["authorization"] == "Bearer test-token", srv.requests


def test_live_api_fly_app_override():
    # FLY_APP overrides fly.toml's app in the request path.
    srv = _StubServer(status=200, body=json.dumps([_machine("8654509b634758", "app")]))
    try:
        r = _run_live({"FLY_API_URL": srv.url(), "FLY_APP": "other-app"})
    finally:
        srv.close()
    assert r.returncode == 0, r.stderr
    assert srv.requests[0]["path"] == "/v1/apps/other-app/machines", srv.requests


def test_live_api_retry_then_success():
    # Transient 500 then 200 → retry recovers → exit 0.
    srv = _StubServer(responses=[(500, '{"error":"boom"}'),
                                 (200, json.dumps([_machine("8654509b634758", "app")]))])
    try:
        r = _run_live({"FLY_API_URL": srv.url(), "FLY_GUARD_MAX_ATTEMPTS": "2"})
    finally:
        srv.close()
    assert r.returncode == 0, r.stderr
    assert len(srv.requests) == 2, srv.requests


def test_api_error_exit_2():
    srv = _StubServer(status=500, body='{"error":"boom"}')
    try:
        r = _run_live({"FLY_API_URL": srv.url(), "FLY_GUARD_MAX_ATTEMPTS": "1"})
    finally:
        srv.close()
    assert r.returncode == 2, r.stdout
    assert "cannot determine" in r.stderr
    assert "500" in r.stderr  # diagnostics name the HTTP status


def test_query_error_exit_2():
    srv = _StubServer(status=200, body='{"message":"not a list"}')
    try:
        r = _run_live({"FLY_API_URL": srv.url(), "FLY_GUARD_MAX_ATTEMPTS": "1"})
    finally:
        srv.close()
    assert r.returncode == 2, r.stdout
    assert "cannot determine" in r.stderr


def test_live_api_malformed_json_exit_2():
    # JSONDecodeError arm of fetch_machines → exit 2.
    srv = _StubServer(status=200, body="{ not json")
    try:
        r = _run_live({"FLY_API_URL": srv.url(), "FLY_GUARD_MAX_ATTEMPTS": "1"})
    finally:
        srv.close()
    assert r.returncode == 2, r.stdout
    assert "cannot determine" in r.stderr


def test_api_connection_refused_exit_2():
    # URLError/ConnectionRefused arm of fetch_machines (most likely real-world
    # failure mode) → exit 2, fail-closed.
    env = dict(os.environ)
    for k in _AMBIENT:
        env.pop(k, None)
    env.update({"FLY_TOML": str(_write_fly_toml(None)), "FLY_API_TOKEN": "test-token",
                "FLY_API_URL": "http://127.0.0.1:1/v1", "FLY_GUARD_MAX_ATTEMPTS": "1"})
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                       env=env, cwd=REPO_ROOT)
    assert r.returncode == 2, r.stdout
    assert "cannot determine" in r.stderr


def test_max_attempts_invalid_exit_2():
    # FLY_GUARD_MAX_ATTEMPTS parse branch is fail-closed exit 2, before any
    # API call (0 requests recorded by the stub).
    srv = _StubServer(status=200, body="[]")
    try:
        r = _run_live({"FLY_API_URL": srv.url(), "FLY_GUARD_MAX_ATTEMPTS": "abc"})
    finally:
        srv.close()
    assert r.returncode == 2, r.stdout
    assert "positive integer" in r.stderr
    assert len(srv.requests) == 0, srv.requests


def test_max_attempts_zero_exit_2():
    srv = _StubServer(status=200, body="[]")
    try:
        r = _run_live({"FLY_API_URL": srv.url(), "FLY_GUARD_MAX_ATTEMPTS": "0"})
    finally:
        srv.close()
    assert r.returncode == 2, r.stdout
    assert "positive integer" in r.stderr
    assert len(srv.requests) == 0, srv.requests
