#!/usr/bin/env python3
"""Assert the RUNNING Fly machine's env honours every ``fly-toml-env`` name (#5656).

The second half of #4568. ``check-fly-secret-drift.py`` checks the *manifest*
against the Fly **secret list** and ``fly.toml``'s ``[env]`` table — all
build-time surfaces. The machine's own ``config.env`` is never read, so a
declared ``[env]`` value that the running machine does not carry passes every
gate there is.

The incident this exists for (#4568, 2026-09): an out-of-band ``fly.toml
[env]`` deploy was silently reverted by the next ``deploy-hosted`` run, and
``TORTOISE_MANUAL_LINKING_ENABLED`` (declared, live at ``1``) was absent on the
machine for ~5 h. ``is_truthy(None)`` made ``_linking_available()`` return
False, so a product flag was off — and the operator had no way to learn that the
value they applied was not the value the machine ran. Until this script existed,
the documented route's outcome rested on an operator running ``flyctl ssh
console … printenv`` by hand; the residual is now recorded as closed in
``docs/infra-runbook.md`` §8.1, under "The machine side (the second half of
#4568)".

Division of labour with ``check-fly-secret-drift.py`` — this is not a duplicate:

  * the drift gate answers "does every name have a declared MANAGING SOURCE,
    and does a Fly SECRET shadow a declared ``[env]`` name?" — a *manifest* check;
  * this gate answers "does the RUNNING MACHINE actually carry the declared
    value?" — a *deployed-state* check.

Both are needed: a manifest can be perfect while the machine runs something else.

Contract
--------
For every name the manifest declares ``fly-toml-env``, and for every **active**
machine of the app, the machine's ``config.env`` must carry that name with the
value ``fly.toml``'s ``[env]`` declares. A name that is absent, or whose value
differs, FAILS. ``fly machines list -a <app> --json`` returns ``config.env``
directly, so no ``ssh`` is needed.

``fly-toml-env`` names are non-secret **by construction** — the manifest's whole
point is that their value is versioned in ``fly.toml`` — so printing them (and
the value the machine runs) is safe and is what makes the failure actionable.

Exit codes (fail-closed; mirrors check-migration-drift / check-fly-machines-guard):
  0  every declared name is present on every active machine with the declared value
  1  at least one declaration is absent or divergent on a running machine
  2  the state could NOT be determined (API error, malformed shape, missing token,
     unreadable fly.toml / manifest, **no `fly-toml-env` names declared**, or
     **zero active machines**) — never reported as 0

Both of the last two are the same rule: **"nothing was compared" is not a pass**.
An empty declaration set, and a fleet with no active machine, each leave this gate
with no comparison to make; certifying that as green is how a guard rots into a
warning nobody reads. Exit 2 is not bypassable in the workflow.

Environment seams (the same names the sibling guards use, so CI wiring and tests
are uniform):
  FLY_TOML                  fly.toml path                     (default: repo fly.toml)
  FLY_APP                   app name        (default: fly.toml [app])
  FLY_MANAGED_SECRETS_FILE  manifest path   (default: .github/scripts/fly-managed-secrets.txt)
  FLY_MACHINES_FILE         machines-list JSON instead of the API (test seam)
  FLY_API_TOKEN             Fly API token (required unless FLY_MACHINES_FILE is set)
  FLY_API_URL               API base        (default: https://api.machines.dev/v1)
  FLY_GUARD_MAX_ATTEMPTS    retry budget    (default: 5)
"""
from __future__ import annotations

import http.client
import json
import os
import re
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_MANIFEST = REPO_ROOT / ".github" / "scripts" / "fly-managed-secrets.txt"
DEFAULT_API_URL = "https://api.machines.dev/v1"

# Mirrors check-fly-machines-guard.py: a transient Fly API race must ride
# through, not block the deploy (#1346 class). 4s/8s/16s/32s backoff.
MAX_ATTEMPTS = 5

#: The manifest source token this gate acts on.
FLY_TOML_ENV = "fly-toml-env"

#: `flyctl IsActive()`: already gone, so no remediation is possible and a
#: destroyed machine's stale config is not a live divergence.
INACTIVE_STATES = frozenset({"destroyed", "destroying"})

#: A `#` starting a line or preceded by whitespace begins a comment. NOT a bare
#: `split("#", 1)`: a `fly-only:#661` issue reference is data, and the sibling
#: drift gate records the bug that a naive split caused.
_COMMENT_RE = re.compile(r"(?:^|\s)#")

#: The longest value echoed into a log line before truncation. Values here are
#: non-secret by construction; the truncation is for log hygiene, not secrecy.
_MAX_VALUE_ECHO = 64


class GuardError(Exception):
    """Fail-closed: the machine env cannot be determined."""


def _clean(value: str) -> str:
    """Sanitize machine-controlled strings before embedding in log lines."""
    return " ".join(value.split())


def _err(message: str) -> None:
    # GitHub Actions annotation (parsed from the log by the runner); harmless
    # plain text when run locally. Every could-not-determine diagnostic routes
    # through here so a blocked step's root cause surfaces in the UI.
    print(f"::error::{message}", file=sys.stderr)


def _short(value: object) -> str:
    text = _clean(str(value))
    return text if len(text) <= _MAX_VALUE_ECHO else text[:_MAX_VALUE_ECHO] + "…"


def load_fly_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def declared_toml_env(toml: dict) -> dict[str, str]:
    """Return ``fly.toml``'s ``[env]`` table as ``{name: value}``.

    Fail-closed: a non-table ``[env]``, or a value that is not a string, is
    unreadable state (exit 2) rather than an empty declaration set — an empty
    declaration set would make every name "not declared" and pass vacuously.
    """
    env = toml.get("env")
    if env is None:
        return {}
    if not isinstance(env, dict):
        raise GuardError("'env' in fly.toml is not a table")
    out: dict[str, str] = {}
    for name, value in env.items():
        if not isinstance(name, str) or not name:
            raise GuardError("fly.toml [env] has a non-string or empty key")
        if not isinstance(value, str):
            raise GuardError(
                f"fly.toml [env].{_clean(name)} is not a string "
                "(Fly env values are strings; a non-string cannot be compared)"
            )
        out[name] = value
    return out


def read_fly_toml_env_names(path: Path) -> list[str]:
    """Names the manifest declares ``fly-toml-env``, in file order.

    The manifest is the DECLARATION of what is applied from ``[env]``, so a name
    it declares but ``fly.toml`` does not carry is itself a divergence (the
    declaration cannot be honoured) — reported by ``main`` as a violation, not
    as an unreadable state.

    Malformed entries RAISE (exit 2, never the bypassable 1): a manifest this
    gate cannot read is not a manifest that declares nothing.
    """
    names: list[str] = []
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        marker = _COMMENT_RE.search(raw)
        line = (raw[: marker.start()] if marker else raw).strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2:
            raise ValueError(
                f"{path.name}:{lineno}: expected '<NAME> <source>', got {raw!r}"
            )
        name, source = parts
        if source == FLY_TOML_ENV:
            names.append(name)
    return names


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
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError,
                OSError, json.JSONDecodeError, ValueError) as e:
            # http.client.HTTPException covers IncompleteRead / BadStatusLine —
            # truncated-body races that must retry + exit 2, never traceback.
            last_err = f"machines API error: {e}"
        if attempt < max_attempts - 1:
            time.sleep(2 ** (attempt + 2))  # 4s, 8s, 16s, 32s, ...
    raise RuntimeError(last_err or "machines API error")


def machine_env(machine: object) -> tuple[str, str | None, dict[str, str]]:
    """Return ``(machine_id, state, env)`` for one machines-list entry.

    ``config.env`` may legitimately be absent on a machine with no ``[env]``, so
    that reads as an EMPTY env — every declared name is then "absent", which is
    the divergence this gate exists to report. A ``config.env`` that is present
    but not an object is malformed (exit 2): it cannot be read as "no env".
    """
    if not isinstance(machine, dict):
        raise GuardError("machines list entry is not a JSON object")
    mid = _clean(str(machine.get("id") or machine.get("name") or "?"))
    state = machine.get("state")
    if state is not None and not isinstance(state, str):
        raise GuardError(f"machine {mid}: 'state' is not a string")
    config = machine.get("config")
    if config is not None and not isinstance(config, dict):
        raise GuardError(f"machine {mid}: 'config' is not a JSON object")
    env = (config or {}).get("env")
    if env is None:
        return mid, state, {}
    if not isinstance(env, dict):
        raise GuardError(f"machine {mid}: 'config.env' is not a JSON object")
    out: dict[str, str] = {}
    for name, value in env.items():
        if not isinstance(name, str) or not name:
            raise GuardError(f"machine {mid}: 'config.env' has a non-string or empty key")
        if not isinstance(value, str):
            # Fly env values are strings; a non-string is a shape this gate does
            # not understand, and guessing at it could manufacture a pass.
            raise GuardError(
                f"machine {mid}: 'config.env.{_clean(name)}' is not a string"
            )
        out[name] = value
    return mid, state, out


def main() -> int:
    toml_path = Path(os.environ.get("FLY_TOML") or (REPO_ROOT / "fly.toml"))
    if not toml_path.exists():
        _err(f"cannot determine Fly app config: fly.toml not found at {toml_path}")
        return 2
    try:
        toml = load_fly_toml(toml_path)
        declared = declared_toml_env(toml)
    except (OSError, tomllib.TOMLDecodeError, GuardError) as e:
        _err(f"cannot determine Fly app config: {e}")
        return 2
    app = os.environ.get("FLY_APP") or toml.get("app")
    if not app:
        _err("cannot determine Fly app name (fly.toml [app] / FLY_APP)")
        return 2

    manifest_path = Path(os.environ.get("FLY_MANAGED_SECRETS_FILE") or DEFAULT_MANIFEST)
    try:
        declared_names = read_fly_toml_env_names(manifest_path)
    except (OSError, ValueError) as e:
        _err(f"cannot determine declared fly-toml-env names: {e}")
        return 2
    if not declared_names:
        # An EMPTY declaration set is could-not-determine, not a pass. The
        # declaration set is this gate's UNIVERSE: with nothing declared,
        # "every declared name is present" is vacuously true, and the machine
        # could be missing every `fly.toml [env]` value while the run stays
        # green — the exact #4568 outcome this gate exists to prevent. Removing
        # the `fly-toml-env` lines is also invisible to check-fly-secret-drift.py
        # (it has no reverse [env]-completeness rule), so the hole is real.
        # This is the same rule applied below to zero active machines: nothing
        # compared is never a pass. The sibling drift gate refuses the analogous
        # empty secret list for the same reason.
        _err(
            f"cannot determine what to assert: no `fly-toml-env` names are declared "
            f"in {manifest_path.name} — an empty declaration set cannot certify "
            "anything ('nothing compared' is not a pass)"
        )
        return 2

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
    # A declared name that `fly.toml` does not carry: the declaration cannot be
    # honoured, and the machine side would compare against nothing.
    for name in declared_names:
        if name not in declared:
            violations.append(
                f"{name}: declared `fly-toml-env` in {manifest_path.name} but absent "
                "from fly.toml [env] — the declaration cannot be honoured"
            )

    checked = 0
    for machine in machines:
        try:
            mid, state, env = machine_env(machine)
        except GuardError as e:
            _err(f"cannot determine machines state: {e}")
            return 2
        if state in INACTIVE_STATES:
            continue
        checked += 1
        for name in declared_names:
            if name not in declared:
                continue  # already reported above; not a machine-side finding
            expected = declared[name]
            if name not in env:
                violations.append(
                    f"machine {mid}: `{name}` is declared in fly.toml [env] "
                    f"(value {_short(expected)!r}) but ABSENT from the machine's env"
                )
            elif _clean(env[name]) != _clean(expected):
                violations.append(
                    f"machine {mid}: `{name}` runs {_short(env[name])!r} but fly.toml "
                    f"[env] declares {_short(expected)!r}"
                )

    if checked == 0:
        # Every machine is destroyed/destroying, or the list is empty. That is a
        # fleet with nothing running, NOT a machine that carries the right env —
        # "nothing was compared" must never read as "the comparison passed".
        _err(
            "cannot determine machines state: 0 active machine(s) to compare — "
            "'nothing compared' is not a pass"
        )
        return 2

    if violations:
        print(
            f"Fly machine env drift: {len(violations)} violation(s) across "
            f"{checked} active machine(s):",
            file=sys.stderr,
        )
        for v in violations:
            # NOT `_short(v)`: the message is composed from already-`_clean`ed
            # values, and truncating the whole line would cut off the machine id
            # and the name under test — the two things that make it actionable.
            print(f"  - {v}", file=sys.stderr)
        return 1

    print(
        f"OK: {len(declared_names)} fly-toml-env name(s) present with the declared "
        f"value on {checked} active machine(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
