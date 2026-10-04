"""Hermetic tests for .github/scripts/check-fly-machine-env.py (#5656).

The script reads FLY_MACHINES_FILE / FLY_TOML / FLY_APP / FLY_MANAGED_SECRETS_FILE
/ FLY_API_URL / FLY_API_TOKEN / FLY_GUARD_MAX_ATTEMPTS env seams so tests run with
zero network: FLY_MACHINES_FILE points at a fixture machines-list JSON, FLY_TOML at
a fixture fly.toml, FLY_MANAGED_SECRETS_FILE at a fixture manifest, and FLY_API_URL
at a local stub HTTP server for the one live-API path.

Fixture provenance: the machine shape is the same one tests/test_fly_machines_guard.py
uses, captured verbatim from ``flyctl machines list -a tortoise-y4mjjq --json``
(2026-08-28): ``id`` / ``name`` / ``state`` / ``config.env``. The declaration shape
is the live manifest's (``.github/scripts/fly-managed-secrets.txt``), where
``fly-toml-env`` names are applied from ``fly.toml``'s ``[env]``.

Exit contract (fail-closed, mirrors check-migration-drift / check-fly-machines-guard):
  0 every declared name is present on every active machine with the declared value
  1 at least one declaration is absent or divergent on a running machine
  2 could-not-determine (API error, malformed shape, missing token, unreadable input)

The #4568 incident this gate closes: ``TORTOISE_MANUAL_LINKING_ENABLED`` was
declared and live at ``1``, but absent on the running machine for ~5 h — a product
flag silently off, with no gate able to see it.
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
from typing import ClassVar

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".github" / "scripts" / "check-fly-machine-env.py"

_FIXTURE_TMP = None


def _tmp() -> Path:
    """One temp dir per test-run (cleaned by the OS)."""
    global _FIXTURE_TMP
    if _FIXTURE_TMP is None:
        _FIXTURE_TMP = Path(tempfile.mkdtemp(prefix="fly-env-guard-"))
    return _FIXTURE_TMP


FIXTURES = _tmp()

# Ambient seams a developer might have exported — popped for full hermeticity.
_AMBIENT = ("FLY_API_TOKEN", "FLY_MACHINES_FILE", "FLY_TOML", "FLY_APP",
            "FLY_MANAGED_SECRETS_FILE", "FLY_API_URL", "FLY_GUARD_MAX_ATTEMPTS")

#: The three names the LIVE manifest declares fly-toml-env (pinned, so a manifest
#: edit that changes the audited set is a deliberate test change, not a silent one).
LIVE_NAMES = ("TORTOISE_CONTROL_PLANE", "TORTOISE_REAUTH_WINDOW_SECONDS",
              "TORTOISE_MANUAL_LINKING_ENABLED")

_counter = {"n": 0}


def _write_fly_toml(env: dict[str, str] | None, app: str = "tortoise-y4mjjq",
                   toml_raw: str | None = None) -> Path:
    _counter["n"] += 1
    d = FIXTURES / f"toml-{_counter['n']}"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "fly.toml"
    if toml_raw is not None:
        path.write_text(toml_raw)
        return path
    body = f'app = "{app}"\nprimary_region = "iad"\n'
    if env is not None:
        body += "\n[env]\n"
        for k, v in env.items():
            body += f'  {k} = "{v}"\n'
    path.write_text(body)
    return path


def _write_manifest(entries: list[tuple[str, str]] | None, raw: str | None = None) -> Path:
    """entries → `<NAME> <source>` lines; `raw` writes the file verbatim."""
    _counter["n"] += 1
    d = FIXTURES / f"manifest-{_counter['n']}"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "fly-managed-secrets.txt"
    if raw is not None:
        path.write_text(raw)
    else:
        lines = ["# fixture manifest"]
        for name, source in (entries or []):
            lines.append(f"{name} {source}")
        path.write_text("\n".join(lines) + "\n")
    return path


def _machine(mid: str, env: dict[str, str] | None, state: str | None = None) -> dict:
    m: dict = {"id": mid, "name": f"name-{mid}", "config": {}}
    if env is not None:
        m["config"]["env"] = env
    if state is not None:
        m["state"] = state
    return m


def _write_machines(machines: list[dict]) -> Path:
    _counter["n"] += 1
    d = FIXTURES / f"mach-{_counter['n']}"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "machines.json"
    path.write_text(json.dumps(machines))
    return path


def _run(machines: list[dict], *, env: dict[str, str] | None = None,
         manifest: list[tuple[str, str]] | None = None,
         manifest_raw: str | None = None,
         toml_env: dict[str, str] | None = None,
         toml_raw: str | None = None,
         env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run the script with every surface pointed at a fixture (zero network)."""
    toml_env = {"TORTOISE_CONTROL_PLANE": "supabase",
                "TORTOISE_REAUTH_WINDOW_SECONDS": "900",
                "TORTOISE_MANUAL_LINKING_ENABLED": "1"} if toml_env is None else toml_env
    manifest = [(n, "fly-toml-env") for n in LIVE_NAMES] if manifest is None else manifest
    run_env = dict(os.environ)
    for k in _AMBIENT:
        run_env.pop(k, None)
    run_env.update({
        "FLY_MACHINES_FILE": str(_write_machines(machines)),
        "FLY_TOML": str(_write_fly_toml(toml_env, toml_raw=toml_raw)),
        "FLY_MANAGED_SECRETS_FILE": str(_write_manifest(manifest, raw=manifest_raw)),
    })
    run_env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, env=run_env, cwd=REPO_ROOT,
    )


def _clean_machine(mid: str = "8654509b634758") -> dict:
    return _machine(mid, {"TORTOISE_CONTROL_PLANE": "supabase",
                          "TORTOISE_REAUTH_WINDOW_SECONDS": "900",
                          "TORTOISE_MANUAL_LINKING_ENABLED": "1"})


# ── clean ──────────────────────────────────────────────────────────────────


def test_clean_machine_exit_zero():
    r = _run([_clean_machine()])
    assert r.returncode == 0, r.stderr
    assert "3 fly-toml-env name(s)" in r.stdout, r.stdout
    assert "1 active machine(s)" in r.stdout, r.stdout


def test_clean_multiple_machines_exit_zero():
    r = _run([_clean_machine("m1"), _clean_machine("m2")])
    assert r.returncode == 0, r.stderr
    assert "2 active machine(s)" in r.stdout, r.stdout


# ── the #4568 defect: declared but absent on the running machine ───────────


def test_declared_but_absent_fails_and_names_the_variable():
    # The exact #4568 shape: the machine lacks TORTOISE_MANUAL_LINKING_ENABLED.
    m = _machine("m1", {"TORTOISE_CONTROL_PLANE": "supabase",
                        "TORTOISE_REAUTH_WINDOW_SECONDS": "900"})
    r = _run([m])
    assert r.returncode == 1, r.stderr
    assert "TORTOISE_MANUAL_LINKING_ENABLED" in r.stderr, r.stderr
    assert "ABSENT" in r.stderr, r.stderr
    # The two names that ARE present must NOT be reported — a gate that flags
    # everything is one nobody reads.
    assert "TORTOISE_CONTROL_PLANE` is declared" not in r.stderr, r.stderr


def test_divergent_value_fails_and_shows_both_values():
    m = _machine("m1", {"TORTOISE_CONTROL_PLANE": "supabase",
                        "TORTOISE_REAUTH_WINDOW_SECONDS": "900",
                        "TORTOISE_MANUAL_LINKING_ENABLED": "0"})
    r = _run([m])
    assert r.returncode == 1, r.stderr
    assert "TORTOISE_MANUAL_LINKING_ENABLED" in r.stderr, r.stderr
    assert "'0'" in r.stderr and "'1'" in r.stderr, r.stderr


def test_one_stale_machine_among_many_fails_and_is_named():
    # A partially-rolled deploy: the second machine still runs the old env.
    stale = _machine("stale-1", {"TORTOISE_CONTROL_PLANE": "supabase",
                                 "TORTOISE_REAUTH_WINDOW_SECONDS": "900"})
    r = _run([_clean_machine("fresh-1"), stale])
    assert r.returncode == 1, r.stderr
    assert "stale-1" in r.stderr, r.stderr
    assert "fresh-1" not in r.stderr, r.stderr


def test_machine_with_no_env_key_at_all_lists_every_name():
    # config.env absent is a READABLE state (a machine with no [env]) — every
    # declared name is then absent, which is exactly the divergence to report.
    r = _run([_machine("m1", None)])
    assert r.returncode == 1, r.stderr
    for name in LIVE_NAMES:
        assert name in r.stderr, r.stderr


# ── nothing-compared is not a pass ─────────────────────────────────────────


def test_empty_machine_list_is_could_not_determine():
    r = _run([])
    assert r.returncode == 2, r.stdout
    assert "not a pass" in r.stderr, r.stderr


def test_all_machines_inactive_is_could_not_determine():
    # Every machine destroyed → the env was not compared against anything, so
    # this must NOT read as green.
    r = _run([_machine("m1", None, state="destroyed"),
              _machine("m2", None, state="destroying")])
    assert r.returncode == 2, r.stderr
    assert "not a pass" in r.stderr, r.stderr


def test_inactive_machine_is_skipped_but_active_one_still_checked():
    r = _run([_machine("dead", None, state="destroyed"), _clean_machine("live")])
    assert r.returncode == 0, r.stderr
    assert "1 active machine(s)" in r.stdout, r.stdout


# ── declaration integrity ──────────────────────────────────────────────────


def test_declared_name_absent_from_fly_toml_fails():
    # The manifest says `fly-toml-env` but fly.toml does not carry it: the
    # declaration cannot be honoured.
    r = _run([_clean_machine()], toml_env={"TORTOISE_CONTROL_PLANE": "supabase"})
    assert r.returncode == 1, r.stderr
    assert "cannot be honoured" in r.stderr, r.stderr


def test_non_fly_toml_env_sources_are_not_asserted():
    # A `workflow` / `gh-secret:` declaration is another gate's business; this one
    # must not demand it on the machine.
    r = _run([_clean_machine()],
             manifest=[("TORTOISE_CONTROL_PLANE", "fly-toml-env"),
                       ("TORTOISE_REAUTH_WINDOW_SECONDS", "fly-toml-env"),
                       ("TORTOISE_MANUAL_LINKING_ENABLED", "fly-toml-env"),
                       ("FLY_ONLY_NAME", "workflow"),
                       ("SOME_GH_SECRET", "gh-secret:SOME_GH_SECRET")])
    assert r.returncode == 0, r.stderr
    assert "FLY_ONLY_NAME" not in r.stderr, r.stderr


def test_no_fly_toml_env_declared_is_could_not_determine():
    # An EMPTY declaration set cannot certify anything: with nothing declared,
    # "every declared name is present" is vacuously true, so the machine could be
    # missing every fly.toml [env] value while this exits 0 — the #4568 outcome.
    # Same rule as zero active machines: nothing compared is not a pass.
    r = _run([_clean_machine()], manifest=[("SOME_GH_SECRET", "gh-secret:X")])
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "empty declaration set" in r.stderr, r.stderr


def test_comment_only_manifest_is_could_not_determine():
    # A comment-only manifest declares nothing — exit 2, same as above.
    r = _run([_clean_machine()], manifest=None,
             manifest_raw="# only comments\n\n   \n")
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "empty declaration set" in r.stderr, r.stderr


# ── fail-closed on unreadable input (exit 2, never the bypassable 1) ───────


def test_malformed_manifest_is_could_not_determine():
    r = _run([_clean_machine()], manifest=None,
             manifest_raw="TORTOISE_CONTROL_PLANE\n")  # one field, not two
    assert r.returncode == 2, r.stdout
    assert "cannot determine declared" in r.stderr, r.stderr


def test_missing_manifest_is_could_not_determine():
    missing = FIXTURES / "does-not-exist.txt"
    r2 = _run([_clean_machine()], manifest=None, manifest_raw="x y\n",
              env_extra={"FLY_MANAGED_SECRETS_FILE": str(missing)})
    assert r2.returncode == 2, r2.stdout
    assert "cannot determine declared" in r2.stderr, r2.stderr


def test_missing_fly_toml_is_could_not_determine():
    r = _run([_clean_machine()],
             env_extra={"FLY_TOML": str(FIXTURES / "nope.toml")})
    assert r.returncode == 2, r.stdout
    assert "fly.toml not found" in r.stderr, r.stderr


def test_toml_env_non_string_value_is_could_not_determine():
    # `TORTOISE_MANUAL_LINKING_ENABLED = 1` is a TOML INTEGER, not a string. It
    # cannot be compared against a Fly env value, so it is unreadable state —
    # coercing it would manufacture a comparison that never happened.
    raw = ('app = "tortoise-y4mjjq"\n\n[env]\n'
           '  TORTOISE_CONTROL_PLANE = "supabase"\n'
           '  TORTOISE_REAUTH_WINDOW_SECONDS = "900"\n'
           '  TORTOISE_MANUAL_LINKING_ENABLED = 1\n')
    r = _run([_clean_machine()], toml_raw=raw)
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "is not a string" in r.stderr, r.stderr


def test_toml_env_missing_table_is_could_not_determine_when_names_declared():
    # `[env]` absent while the manifest declares fly-toml-env names: every name
    # reads as "not declared", which `main` reports as a violation (exit 1) —
    # the declaration genuinely cannot be honoured. Pin that it is NOT a crash.
    r = _run([_clean_machine()], toml_env={})
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "cannot be honoured" in r.stderr, r.stderr


def test_machine_env_non_object_is_could_not_determine():
    m = {"id": "m1", "name": "n", "config": {"env": "not-an-object"}}
    r = _run([m])
    assert r.returncode == 2, r.stdout
    assert "config.env" in r.stderr, r.stderr


def test_machine_env_non_string_value_is_could_not_determine():
    m = {"id": "m1", "name": "n",
         "config": {"env": {"TORTOISE_CONTROL_PLANE": 7}}}
    r = _run([m])
    assert r.returncode == 2, r.stdout
    assert "is not a string" in r.stderr, r.stderr


def test_machine_entry_not_an_object_is_could_not_determine():
    r = _run(["not-a-machine"])  # type: ignore[list-item]
    assert r.returncode == 2, r.stdout
    assert "not a JSON object" in r.stderr, r.stderr


def test_machines_file_not_a_list_is_could_not_determine():
    p = FIXTURES / "notalist.json"
    p.write_text('{"machines": []}')
    r = _run([_clean_machine()], env_extra={"FLY_MACHINES_FILE": str(p)})
    assert r.returncode == 2, r.stdout
    assert "not a JSON list" in r.stderr, r.stderr


def test_missing_api_token_is_could_not_determine():
    # No FLY_MACHINES_FILE seam + no token → exit 2 (never bypassable).
    r = _run([_clean_machine()],
             env_extra={"FLY_MACHINES_FILE": "", "FLY_API_TOKEN": ""})
    assert r.returncode == 2, r.stdout
    assert "FLY_API_TOKEN not set" in r.stderr, r.stderr


def test_bad_max_attempts_is_could_not_determine():
    r = _run([_clean_machine()],
             env_extra={"FLY_MACHINES_FILE": "", "FLY_API_TOKEN": "t",
                        "FLY_GUARD_MAX_ATTEMPTS": "0"})
    assert r.returncode == 2, r.stdout
    assert "positive integer" in r.stderr, r.stderr


# ── the live-API path (local stub server; no network) ──────────────────────


class _StubHandler(http.server.BaseHTTPRequestHandler):
    payload: bytes = b"[]"
    status = 200
    seen_auth: ClassVar[list[str]] = []

    def do_GET(self):  # BaseHTTPRequestHandler API (N802 is not enabled here)
        _StubHandler.seen_auth.append(self.headers.get("Authorization", ""))
        self.send_response(_StubHandler.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(_StubHandler.payload)))
        self.end_headers()
        self.wfile.write(_StubHandler.payload)

    def log_message(self, *args):  # silence
        pass


def _stub_server(payload: list, status: int = 200, seen: list | None = None):
    _StubHandler.payload = json.dumps(payload).encode()
    _StubHandler.status = status
    _StubHandler.seen_auth = seen if seen is not None else []
    srv = http.server.HTTPServer(("127.0.0.1", 0), _StubHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/v1"


def test_live_api_path_reads_config_env_and_passes():
    payload = [_clean_machine("api-1")]
    srv, url = _stub_server(payload)
    try:
        r = _run([], env_extra={"FLY_MACHINES_FILE": "", "FLY_API_TOKEN": "tok",
                                "FLY_API_URL": url})
    finally:
        srv.shutdown()
    assert r.returncode == 0, r.stderr
    assert "1 active machine(s)" in r.stdout, r.stdout


def test_live_api_path_sends_bearer_token():
    seen: list[str] = []
    srv, url = _stub_server([_clean_machine("api-1")], seen=seen)
    try:
        r = _run([], env_extra={"FLY_MACHINES_FILE": "", "FLY_API_TOKEN": "sekret",
                                "FLY_API_URL": url})
    finally:
        srv.shutdown()
    assert r.returncode == 0, r.stderr
    assert seen == ["Bearer sekret"], seen


def test_live_api_http_error_is_could_not_determine():
    # A failing API is exit 2 — never a silent pass and never the bypassable 1.
    srv, url = _stub_server([], status=500)
    try:
        r = _run([], env_extra={"FLY_MACHINES_FILE": "", "FLY_API_TOKEN": "tok",
                                "FLY_API_URL": url, "FLY_GUARD_MAX_ATTEMPTS": "1"})
    finally:
        srv.shutdown()
    assert r.returncode == 2, r.stdout
    assert "HTTP 500" in r.stderr, r.stderr
