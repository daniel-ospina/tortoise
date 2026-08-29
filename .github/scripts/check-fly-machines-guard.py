#!/usr/bin/env python3
"""Fly machine fleet guard for the hosted app (#1896).

Fail-closed pre-deploy check: no orphaned (non-Fly-Launch) or crash-looping
machine may serve production traffic. The 2026-08-28 incident: orphan machine
``080d6e1a0d2928`` (empty process group — NOT part of Fly Launch; legacy
buildpack/Procfile ``web: gunicorn app:app``) received production traffic and
crash-looped (``gunicorn: command not found`` → exit 127 → "machine has
reached its max restart count of 3" → stopped), producing 10-15s dashboard
hangs while the Fly proxy retried every request 15x.

Detection (mirrors flyctl's canonical logic, source-verified against flyctl
master + fly-go main):

1. ORPHAN — a machine not part of Fly Launch. flyctl's ``ProcessGroup()``
   (fly-go machine_types.go): ``config.metadata.fly_process_group`` →
   ``config.metadata.process_group`` → ``""`` (empty = "Found machines that
   aren't part of Fly Launch"). The group is validated against the app's
   allowed set = fly.toml ``[processes]`` keys (or the ``{'app'}`` default
   when there is no ``[processes]`` section — the current fly.toml has none)
   unioned with the Fly-internal groups ``fly_app_release_command`` /
   ``fly_app_console`` / ``fly_app_test_machine_command`` (fly-go constants).
   A machine with an empty group or a group outside the allowed set is an
   ORPHAN. The transient deploy-time ``fly_app_release_command`` machine is
   legitimately Launch-created and MUST NOT be flagged.

2. CRASH-LOOP — flyctl's ``isConstantlyRestarting()``
   (internal/machine/leasable_machine.go:293-311): take the FIRST ``exit``
   event in the machines-list ``events`` array (newest-first → most recent
   exit); flag iff ``request.restart_count > 1 AND
   request.exit_event.exit_code != 0 AND NOT request.exit_event.requested_stop``.
   JSON field names (fly-go struct tags): ``exit_event``, ``exit_code``,
   ``requested_stop``, ``restarting``, ``restart_count``.

   DELIBERATE DEVIATION (documented): we DROP flyctl's extra
   ``exit_event.restarting`` condition so the stopped-after-crash-loop
   terminal state (the incident's "max restart count of 3" → stopped) is also
   flagged — flyctl only checks live machines during deploy waits; the guard
   must catch the terminal state too. Consequences, verified reasoning:
   (a) ``restart_count`` is per-restart-episode, not lifetime-cumulative
   (flyctl's own smoke-wait would permanently fail any machine restarted
   >= 2x ever if it were cumulative), so a recovered machine is flagged only
   while its most recent exit still shows the crash signature — the
   conservative direction for a pre-deploy gate; (b) platform/operator stops
   (auto-stop, ``fly machine stop``, deploy teardown) record
   ``requested_stop=true`` AND ``exit_code=0`` (community evidence), so
   keeping ``!requested_stop`` does not false-flag operator stops; the
   crash-give-up stop retains the crash's ``requested_stop=false`` (incident
   evidence: exit 127 crash signature at terminal stop). OPEN VERIFICATION
   ITEM: if a future observation shows a give-up stop carrying
   ``requested_stop=true``, add a ``state == 'stopped'`` + crash-signature
   rule independent of ``requested_stop``.

   Crash-loop detection applies to TRAFFIC groups only (the ``[processes]``
   keys or ``{'app'}`` default) — internal groups are transient/non-traffic
   and crash-flagging them would deadlock the fix deploy after a failed
   release command.

3. FLEET SCOPING — machines with ``state ∈ {destroyed, destroying}`` are
   skipped (fly-go ``IsActive()``); the list endpoint returns them and a
   retained destroyed machine must not flag every deploy (``fly machines
   destroy`` cannot clear it). ``stopped`` machines are NOT skipped — the
   terminal stopped-after-crash-loop state is the deviation's target.

4. HOST-UNREACHABLE SHAPE — when ``host_status != "ok"`` the API omits
   ``config`` and returns ``incomplete_config`` (fly-go ``GetConfig()``:
   "when host_status isn't 'ok', the config can't be fully retrieved"). The
   guard falls back to ``incomplete_config`` for the orphan check (flyctl's
   ``GetConfig()`` semantics) and skips the crash-loop check for that machine
   (events may be stale) with a warning. Exit 2 only when BOTH are absent.

5. FAIL-CLOSED SHAPE VALIDATION — per machine: entry not a dict, ``config``
   present-but-not-a-dict, ``config.metadata`` present-but-not-a-dict,
   ``events`` present-but-not-a-list, or the first ``exit`` event lacking a
   parseable ``request.exit_event`` → exit 2 "cannot determine machines
   state". An API shape rename/nesting drift must fail the deploy, never
   silently pass. Missing ``restart_count``/``exit_code`` follow Go
   zero-value semantics (0 → no flag); present-but-non-int → exit 2.

Exit contract (fail-closed, mirrors verify-cutover / check-migration-drift):
  0 clean (no orphan, no traffic-group crash-loop)
  1 guard failed (orphan and/or crash-loop; remediation messages printed)
  2 could-not-determine (missing token / API error / malformed response /
    malformed machine / missing fly.toml)

Env seams (hermetic tests + operator control):
  FLY_MACHINES_FILE     path to a machines-list JSON file (test seam; skips API)
  FLY_TOML              path to fly.toml (default: <repo>/fly.toml)
  FLY_APP               app name override (default: fly.toml [app])
  FLY_API_URL           Machines API base (default https://api.machines.dev/v1)
  FLY_API_TOKEN         Fly API token (required when FLY_MACHINES_FILE unset)
  FLY_GUARD_MAX_ATTEMPTS  API retry attempts (default 3; test seam keeps the
                          suite fast — production keeps the default)

Read-only: never writes to the Fly API. Usable as a standalone operator
dry-run: FLY_API_TOKEN=... python3 .github/scripts/check-fly-machines-guard.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Fly-internal process groups (fly-go constants) — legitimate Launch machines
# that never serve traffic; orphan-checked but NOT crash-loop-checked.
INTERNAL_PROCESS_GROUPS = frozenset(
    {"fly_app_release_command", "fly_app_console", "fly_app_test_machine_command"}
)

# Default traffic group when fly.toml has no [processes] section.
DEFAULT_TRAFFIC_GROUPS = frozenset({"app"})

DEFAULT_API_URL = "https://api.machines.dev/v1"

# flyctl IsActive(): machines in these states are excluded from the fleet.
INACTIVE_STATES = frozenset({"destroyed", "destroying"})

MAX_ATTEMPTS = 3  # API retry budget (2s/4s backoff) — the deploy step it gates retries 5x45s.


class GuardError(Exception):
    """Fail-closed: the machines state cannot be determined."""


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def load_fly_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def process_groups(toml: dict) -> tuple[frozenset[str], frozenset[str]]:
    """Return (allowed_groups, traffic_groups).

    allowed = traffic groups + internal groups; traffic = [processes] keys or
    the ``{'app'}`` default (single-process apps).
    """
    processes = toml.get("processes")
    if isinstance(processes, dict) and processes:
        traffic = frozenset(processes.keys())
    else:
        traffic = DEFAULT_TRAFFIC_GROUPS
    allowed = frozenset(set(traffic) | set(INTERNAL_PROCESS_GROUPS))
    return allowed, traffic


def fetch_machines(app: str, token: str, api_url: str, max_attempts: int = MAX_ATTEMPTS) -> list:
    """GET /apps/{app}/machines with retries; raises RuntimeError on failure."""
    url = f"{api_url.rstrip('/')}/apps/{app}/machines"
    headers = {"Authorization": f"Bearer {token}"}
    last_err: str | None = None
    for attempt in range(max_attempts):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
            data = json.loads(body)
            if not isinstance(data, list):
                raise ValueError("machines API returned a non-list response")
            return data
        except urllib.error.HTTPError as e:  # must precede URLError (subclass)
            try:
                snippet = e.read(400).decode("utf-8", "replace")
            except Exception:
                snippet = ""
            last_err = f"machines API HTTP {e.code}: {snippet[:400]}"
        except (urllib.error.URLError, TimeoutError, OSError,
                json.JSONDecodeError, ValueError) as e:
            last_err = f"machines API error: {e}"
        if attempt < max_attempts - 1:
            time.sleep(2 ** (attempt + 1))  # 2s, 4s, ...
    raise RuntimeError(last_err or "machines API error")


def _machine_config(machine) -> tuple[dict | None, dict | None]:
    """Validate + return (config, incomplete_config); raises GuardError."""
    if not isinstance(machine, dict):
        raise GuardError("machines list entry is not a JSON object")
    config = machine.get("config")
    incomplete = machine.get("incomplete_config")
    if config is None and incomplete is None:
        raise GuardError(
            f"machine {machine.get('id', '?')}: neither 'config' nor 'incomplete_config' "
            "present (host unreachable without fallback?)"
        )
    if config is not None and not isinstance(config, dict):
        raise GuardError(f"machine {machine.get('id', '?')}: 'config' is not a JSON object")
    if incomplete is not None and not isinstance(incomplete, dict):
        raise GuardError(
            f"machine {machine.get('id', '?')}: 'incomplete_config' is not a JSON object"
        )
    for cfg in (config, incomplete):
        if cfg is None:
            continue
        metadata = cfg.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise GuardError(
                f"machine {machine.get('id', '?')}: 'metadata' is not a JSON object"
            )
    events = machine.get("events")
    if events is not None and not isinstance(events, list):
        raise GuardError(f"machine {machine.get('id', '?')}: 'events' is not a JSON array")
    state = machine.get("state")
    if state is not None and not isinstance(state, str):
        raise GuardError(
            f"machine {machine.get('id', '?')}: 'state' is not a string "
            "(cannot evaluate destroyed/destroying skip)"
        )
    return config, incomplete


def process_group(machine: dict, config: dict) -> str:
    """flyctl ProcessGroup(): fly_process_group → process_group → ''."""
    metadata = config.get("metadata") or {}
    return metadata.get("fly_process_group") or metadata.get("process_group") or ""


def crash_loop_verdict(machine: dict) -> bool | None:
    """flyctl isConstantlyRestarting(), minus the `restarting` condition.

    Returns True (crash-loop), False (not), or None (could-not-determine →
    exit 2: the first 'exit' event lacks a parseable request.exit_event).
    """
    events = machine.get("events") or []
    for ev in events:
        if not isinstance(ev, dict):
            return None
        if ev.get("type") != "exit":
            continue
        request = ev.get("request")
        if not isinstance(request, dict):
            return None
        exit_event = request.get("exit_event")
        if not isinstance(exit_event, dict):
            return None
        # Go zero-value semantics for absent counts; type drift → exit 2.
        restart_count = request.get("restart_count", 0)
        exit_code = exit_event.get("exit_code", 0)
        if isinstance(restart_count, bool) or not isinstance(restart_count, int):
            return None
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            return None
        requested_stop = exit_event.get("requested_stop", False)
        if not isinstance(requested_stop, bool):
            # Fail-closed: a drifted non-bool (e.g. the string "false" →
            # truthy → would suppress the crash signature) must exit 2.
            return None
        return restart_count > 1 and exit_code != 0 and not requested_stop
    return False


def main() -> int:
    toml_path = Path(os.environ.get("FLY_TOML") or (REPO_ROOT / "fly.toml"))
    if not toml_path.exists():
        _err(f"cannot determine Fly app config: fly.toml not found at {toml_path}")
        return 2
    try:
        toml = load_fly_toml(toml_path)
    except (OSError, tomllib.TOMLDecodeError) as e:
        _err(f"cannot determine Fly app config: {e}")
        return 2
    app = os.environ.get("FLY_APP") or toml.get("app")
    if not app:
        _err("cannot determine Fly app name (fly.toml [app] / FLY_APP)")
        return 2
    allowed, traffic = process_groups(toml)

    machines_file = os.environ.get("FLY_MACHINES_FILE")
    if machines_file:
        try:
            with open(machines_file) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            _err(f"cannot determine machines state: {e}")
            return 2
        if not isinstance(data, list):
            _err("cannot determine machines state: FLY_MACHINES_FILE is not a JSON list")
            return 2
        machines = data
    else:
        token = os.environ.get("FLY_API_TOKEN")
        if not token:
            _err("cannot determine machines state: FLY_API_TOKEN not set")
            return 2
        api_url = os.environ.get("FLY_API_URL") or DEFAULT_API_URL
        try:
            max_attempts = int(os.environ.get("FLY_GUARD_MAX_ATTEMPTS") or MAX_ATTEMPTS)
            if max_attempts < 1:
                raise ValueError
        except ValueError:
            _err("cannot determine machines state: FLY_GUARD_MAX_ATTEMPTS must be a positive integer")
            return 2
        try:
            machines = fetch_machines(app, token, api_url, max_attempts)
        except RuntimeError as e:
            _err(f"cannot determine machines state: {e}")
            return 2

    violations: list[str] = []
    warnings: list[str] = []
    active = 0
    for machine in machines:
        if not isinstance(machine, dict):
            _err("cannot determine machines state: machines list entry is not a JSON object")
            return 2
        state = machine.get("state")
        if state is not None and not isinstance(state, str):
            _err(
                f"cannot determine machines state: machine {machine.get('id', '?')}: "
                "'state' is not a string"
            )
            return 2
        if state in INACTIVE_STATES:
            continue  # flyctl IsActive(): already gone, no remediation possible
        try:
            config, incomplete = _machine_config(machine)
        except GuardError as e:
            _err(f"cannot determine machines state: {e}")
            return 2
        active += 1
        mid = machine.get("id") or machine.get("name") or "?"
        using_incomplete = config is None
        cfg = config if config is not None else incomplete
        group = process_group(machine, cfg) if cfg is not None else ""

        if not group:
            violations.append(
                f"ORPHAN machine {mid}: empty process group — NOT part of Fly Launch; "
                f"destroy: fly machines destroy {mid} -a {app}"
            )
        elif group not in allowed:
            violations.append(
                f"ORPHAN machine {mid}: process group '{group}' not in allowed set "
                f"{sorted(allowed)}; destroy: fly machines destroy {mid} -a {app}"
            )

        if group in traffic and using_incomplete:
            warnings.append(
                f"machine {mid}: host unreachable (host_status != ok) — crash-loop "
                "detection not evaluated for this machine; orphan check passed"
            )
        elif group in traffic:
            verdict = crash_loop_verdict(machine)
            if verdict is None:
                _err(
                    f"cannot determine machines state: machine {mid} has an exit event "
                    "without a parseable request.exit_event (API shape drift?)"
                )
                return 2
            if verdict:
                violations.append(
                    f"CRASH-LOOP machine {mid}: restart_count>1 with non-zero exit and no "
                    f"requested stop; investigate: fly logs -a {app}; "
                    f"destroy: fly machines destroy {mid} -a {app}"
                )

    for w in warnings:
        print(f"::warning::{w}")

    if violations:
        print("::error::Fly machine guard FAILED:")
        for v in violations:
            print(f"  - {v}")
        print(
            f"{len(violations)} violation(s) — fix before deploy "
            "(incident-fix bypass: skip-fly-machines-guard input / SKIP_FLY_MACHINES_GUARD var)"
        )
        return 1

    if active == 0:
        print("OK: 0 active machines — nothing to guard (deploy will create machines)")
    else:
        print(f"OK: {active} active machine(s), all Launch-managed and not crash-looping")
    return 0


if __name__ == "__main__":
    sys.exit(main())
