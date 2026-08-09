"""CLI serve --http + local key bootstrap tests (#702).

Covers the self-hosted authenticated-MCP story end-to-end:
  1. stdio-refusal message names the real alternatives (no health-server)
  2. auth.py import-crash message tells users to UNSET TORTOISE_API_KEY locally
  3. `serve --http` CLI wiring — real main() dispatch (parser + routing to
     _cmd_serve_http) for tenant/static/none auth modes, including the #719
     allowed_hosts wiring that fixes the 421 Misdirected Request on
     non-loopback binds, the --allowed-hosts flag, and the static-mode
     missing-key error path
  4. local HTTP roundtrip: key create → tenant app → 401 no-auth → tools/list
     with key → write lands in the canonical team_{team_id} graph → Origin
     header accepted
  5. the bootstrap key actually authenticates
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tortoise.sdk import TortoiseSDK


def _parse_sse_json(r):
    """Parse a response body that may be SSE-framed (event: message\\ndata: {...})."""
    text = r.text
    if text.startswith("event:") or "\ndata: " in text:
        for line in text.splitlines():
            if line.startswith("data: "):
                return json.loads(line[len("data: "):])
        return None
    return r.json()


def _boot_tenant_app(db_path):
    """Build the exact app `serve --http --auth tenant` builds (via the CLI
    helper path) with a registry SDK rooted at the same canonical DB."""
    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    from tortoise.mcp_server import create_http_app

    reg = TortoiseSDK(namespace="registry")
    app = create_http_app(
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
        _registry_sdk=reg,
        auth_mode="tenant",
    )

    @asynccontextmanager
    async def _lifespan(_a):
        async with app.lifespan(app):
            yield

    wrapper = FastAPI(lifespan=_lifespan)
    wrapper.mount("/mcp", app)
    return wrapper


@pytest.fixture()
def local_db(tmp_path, monkeypatch):
    """A canonical embedded DB + a bootstrap key via the real CLI."""
    # Pin to the tmp embedded DB — a TORTOISE_DB_URI in the host env would
    # otherwise steer the CLI and the roundtrip tests at a live DB.
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    db = tmp_path / "t.db"
    env = {**os.environ, "TORTOISE_DB_PATH": str(db)}
    proc = subprocess.run(
        [sys.executable, "-m", "tortoise", "key", "create", "--name", "test"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    match = [l for l in proc.stdout.splitlines() if "Created API key:" in l]
    assert match, proc.stdout
    key = match[0].split(":", 1)[1].strip()
    yield db, key, env


# ── 1. stdio-refusal message content ──────────────────────────────────────

def test_stdio_refusal_names_real_alternatives_not_health_server(monkeypatch):
    """The #702 dead end, behaviorally: with the stdio transport active and
    an API key configured (non-dev), _safe() rejects with a message that
    names serve --http and the hosted URL — never the health-server (which
    has no MCP surface). Exercises the real ContextVar branch + live
    is_dev_mode() env check instead of pinning source text."""
    import tortoise.mcp_server as m
    from tortoise.mcp_auth import _transport_mode

    monkeypatch.setenv("TORTOISE_API_KEY", "tt_test_key")
    token = _transport_mode.set("stdio")
    try:
        result = m._safe(lambda: "ok")
    finally:
        _transport_mode.reset(token)

    assert isinstance(result, dict) and "error" in result, \
        "stdio + non-dev must reject, not call through"
    msg = result["error"]
    assert "serve --http" in msg, "stdio refusal must recommend serve --http"
    assert "api.premiselabs.co/mcp" in msg, "stdio refusal must name hosted URL"
    assert "health-server" not in msg, "health-server is not an MCP endpoint — must not be recommended"


# ── 2. auth.py import-crash message content ────────────────────────────────

def test_auth_import_crash_message_guides_unset():
    """The import-crash contract, behaviorally: with TORTOISE_API_KEY set
    but no pepper, importing tortoise.auth must FAIL with a RuntimeError
    whose message tells the user to UNSET the key for local stdio and points
    at serve --http (never the health-server). Simulates the real user
    failure (the exact import the mcp server does at startup) in a fresh
    process instead of pinning source text."""
    code = (
        "import os\n"
        "os.environ['TORTOISE_API_KEY'] = 'tt_x'\n"
        "os.environ.pop('TORTOISE_SECRET_PEPPER', None)\n"
        "import tortoise.auth\n"
    )
    env = {k: v for k, v in os.environ.items()
           if k not in ("TORTOISE_API_KEY", "TORTOISE_SECRET_PEPPER")}
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, env=env, timeout=60)
    assert proc.returncode != 0, "auth import must crash without a pepper when the API key is set"
    msg = proc.stderr
    assert "RuntimeError" in msg, "must raise RuntimeError, not silently pass"
    assert "UNSET" in msg and "TORTOISE_API_KEY" in msg
    assert "serve --http" in msg
    assert "health-server" not in msg


# ── 3. CLI wiring — real main() dispatch (no fake Namespace) ──────────────

def _patch_serve_runtime(monkeypatch, tmp_path):
    """Patch uvicorn.run + create_http_app so `serve --http` can run through
    the REAL main() → _cmd_serve_http path all the way to the uvicorn call
    without binding a socket or starting a server. Returns a calls dict that
    captures the create_http_app kwargs and the uvicorn.run kwargs."""
    import tortoise.mcp_server as mcp_mod
    import uvicorn

    calls = {"create_http_app": None, "uvicorn": None}

    def fake_create_http_app(**kw):
        calls["create_http_app"] = kw
        return object()

    def fake_uvicorn_run(*a, **kw):
        calls["uvicorn"] = kw

    monkeypatch.setattr(mcp_mod, "create_http_app", fake_create_http_app)
    monkeypatch.setattr(uvicorn, "run", fake_uvicorn_run)
    # deterministic embedded DB target (env auto-restored by monkeypatch)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    return calls


def test_serve_http_main_dispatch_static_nonloopback_bind(monkeypatch, tmp_path, capsys):
    """Real main() dispatch: `serve --http --auth static --bind <LAN-IP>`
    reaches uvicorn with that bind AND passes the bind through to the fastmcp
    Host guard via allowed_hosts (fixes the 421 Misdirected Request — #719 P1:
    a non-loopback bind is unreachable for LAN clients otherwise). The
    non-loopback warning (auth != none branch) must also fire."""
    from tortoise.__main__ import main

    calls = _patch_serve_runtime(monkeypatch, tmp_path)
    rc = main(["serve", "--http", "--auth", "static", "--api-key", "tt_x",
               "--bind", "192.168.1.50", "--port", "8123"])
    assert rc == 0
    assert calls["uvicorn"] is not None, "uvicorn.run was not invoked"
    assert calls["uvicorn"]["host"] == "192.168.1.50"
    assert calls["uvicorn"]["port"] == 8123
    app_kw = calls["create_http_app"]
    assert app_kw["auth_mode"] == "static"
    out = capsys.readouterr().out
    assert "reachable on your network" in out, \
        "non-loopback static bind must still warn"
    assert "ensure auth is enforced" in out
    assert app_kw["api_key"] == "tt_x"
    assert "192.168.1.50" in app_kw["allowed_hosts"], \
        "non-loopback --bind must be passed as an allowed host"


def test_serve_http_main_dispatch_none_loopback(monkeypatch, tmp_path):
    """Real main() dispatch: auth=none (localhost eval) reaches uvicorn with
    the default loopback bind and passes auth_mode=none (no registry SDK)."""
    from tortoise.__main__ import main

    calls = _patch_serve_runtime(monkeypatch, tmp_path)
    rc = main(["serve", "--http", "--auth", "none", "--port", "9000"])
    assert rc == 0
    assert calls["uvicorn"]["host"] == "127.0.0.1"
    assert calls["uvicorn"]["port"] == 9000
    app_kw = calls["create_http_app"]
    assert app_kw["auth_mode"] == "none"
    # loopback needs no extra host entries — the guard's DEFAULT_HOSTS covers it
    assert app_kw["allowed_hosts"] == [], \
        "loopback needs no extra host entries — the guard's DEFAULT_HOSTS covers it"


def test_serve_http_main_dispatch_tenant(monkeypatch, tmp_path):
    """Real main() dispatch: default tenant mode reaches uvicorn with
    auth_mode=tenant and a registry SDK built from the same canonical DB."""
    from tortoise.__main__ import main

    calls = _patch_serve_runtime(monkeypatch, tmp_path)
    rc = main(["serve", "--http"])
    assert rc == 0
    app_kw = calls["create_http_app"]
    assert app_kw["auth_mode"] == "tenant"
    assert app_kw["_registry_sdk"] is not None, \
        "tenant mode must build the registry SDK from the canonical DB"
    assert app_kw["_registry_sdk"]._namespace == "registry", \
        "tenant mode registry SDK must target the registry namespace (#702)"
    assert app_kw["_registry_sdk"]._db_path == str(tmp_path / "t.db"), \
        "tenant mode registry SDK must resolve the canonical TORTOISE_DB_PATH (#702)"


def test_serve_http_allowed_hosts_flag(monkeypatch, tmp_path):
    """--allowed-hosts feeds the Host guard verbatim (arbitrary hostnames /
    DNS names clients use that aren't the bind address)."""
    from tortoise.__main__ import main

    calls = _patch_serve_runtime(monkeypatch, tmp_path)
    rc = main(["serve", "--http", "--auth", "none",
               "--allowed-hosts", "mybox.local,10.0.0.7"])
    assert rc == 0
    hosts = calls["create_http_app"]["allowed_hosts"]
    assert "mybox.local" in hosts and "10.0.0.7" in hosts


@pytest.mark.parametrize("flag", ["MyBox.LOCAL.,mybox.local, 10.0.0.7,", " mybox.local ,mybox.local, 10.0.0.7 "])
def test_serve_http_allowed_hosts_normalizes_and_dedups(monkeypatch, tmp_path, flag):
    """--allowed-hosts values are normalized (strip / lowercase / trailing
    dot removed) and deduped — 'MyBox.LOCAL.' and 'mybox.local' collapse to
    one entry (#719)."""
    from tortoise.__main__ import main

    calls = _patch_serve_runtime(monkeypatch, tmp_path)
    rc = main(["serve", "--http", "--auth", "static", "--api-key", "tt_x",
               "--allowed-hosts", flag])
    assert rc == 0
    hosts = calls["create_http_app"]["allowed_hosts"]
    assert set(hosts) == {"mybox.local", "10.0.0.7"}, \
        "allowed-hosts must be normalized (strip/lowercase/trailing-dot) and deduped"
    assert "MyBox.LOCAL." not in hosts and not any(h.endswith(".") for h in hosts), \
        "normalization must lowercase and strip trailing dots"


def test_serve_http_wildcard_bind_merges_allowed_hosts(monkeypatch, tmp_path):
    """#719 P1 scenario: --bind 0.0.0.0 + --allowed-hosts — the guard gets
    BOTH the derived machine hostname AND the extra hostnames, so LAN clients
    using a DNS name aren't 421'd."""
    import socket

    from tortoise.__main__ import main

    calls = _patch_serve_runtime(monkeypatch, tmp_path)
    rc = main(["serve", "--http", "--auth", "static", "--api-key", "tt_x",
               "--bind", "0.0.0.0", "--allowed-hosts", "mybox.local"])
    assert rc == 0
    hosts = calls["create_http_app"]["allowed_hosts"]
    assert "mybox.local" in hosts
    assert socket.gethostname().lower().rstrip(".") in hosts, \
        "wildcard bind must still auto-derive the machine hostname"


@pytest.mark.parametrize("bind", ["0.0.0.0", "::"])
def test_serve_http_any_interface_bind_derives_machine_hosts(monkeypatch, tmp_path, bind, capsys):
    """any-interface bind (0.0.0.0 / ::): clients reach us by the machine's
    hostname/LAN address, not the wildcard — the guard must be seeded with the
    machine's own identity so the 'reachable on your network' warning is
    actually TRUE. hostname/getaddrinfo are patched so the LAN-address seeding
    branch is exercised deterministically (a real hostname may resolve only to
    loopback on CI)."""
    import socket

    from tortoise.__main__ import main

    # Deterministic machine identity: hostname 'mybox' + one non-loopback LAN
    # address, so the getaddrinfo seeding branch always runs.
    monkeypatch.setattr(socket, "gethostname", lambda: "mybox")
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                           ("192.168.50.7", 0))],
    )

    calls = _patch_serve_runtime(monkeypatch, tmp_path)
    # auth=none + wildcard bind is refused by default (P2 security) — the
    # override opts in so this test exercises the host-derivation logic.
    rc = main(["serve", "--http", "--auth", "none", "--bind", bind,
               "--allow-insecure-no-auth"])
    assert rc == 0
    assert calls["uvicorn"] is not None, "uvicorn.run was not invoked"
    hosts = calls["create_http_app"]["allowed_hosts"]
    assert "mybox" in hosts, "any-interface bind must auto-derive the machine hostname"
    assert "192.168.50.7" in hosts, \
        "any-interface bind must seed non-loopback LAN addresses into the guard"
    # The user-visible promise: the warning's 'Host guard allows: ...' line
    # names the machine's own identity.
    out = capsys.readouterr().out
    assert "reachable on your network" in out, \
        "wildcard binds are genuinely network-reachable — the warning MUST fire"
    guard_line = next((ln for ln in out.splitlines() if "Host guard allows:" in ln), None)
    assert guard_line is not None, \
        "wildcard-bind warning must print a 'Host guard allows:' line"
    assert "mybox" in guard_line and "192.168.50.7" in guard_line, \
        "guard line must name the machine hostname and its LAN address"


@pytest.mark.parametrize("bind", ["localhost", "LOCALHOST", "::1", "127.0.0.1", "127.0.0.2", "::ffff:127.0.0.1"])
def test_serve_http_loopback_aliases_no_network_warning(monkeypatch, tmp_path, capsys, bind):
    """Loopback binds — the 'reachable on your network' warning must NOT fire
    (only genuinely non-loopback binds warn; the old `bind != "127.0.0.1"`
    check false-alarmed on loopback aliases like localhost / ::1 / 127.0.0.2 /
    the IPv4-mapped ::ffff:127.0.0.1, #719 P2)."""
    from tortoise.__main__ import main

    calls = _patch_serve_runtime(monkeypatch, tmp_path)
    rc = main(["serve", "--http", "--auth", "none", "--bind", bind])
    assert rc == 0
    assert calls["uvicorn"] is not None, "uvicorn.run was not invoked"
    assert calls["uvicorn"]["host"] == bind
    # Loopback aliases need no extra guard entries (DEFAULT_HOSTS covers them).
    assert calls["create_http_app"]["allowed_hosts"] == [], \
        f"loopback alias {bind} must not seed extra Host-guard entries"
    captured = capsys.readouterr()
    assert "reachable on your network" not in captured.out, \
        f"loopback alias {bind} must not trigger the network warning"
    assert "reachable on your network" not in captured.err, \
        f"loopback alias {bind} must not trigger the network warning (stderr)"


def test_is_loopback_bind_ipv4_mapped_loopback_guarded(monkeypatch):
    """The ipv4_mapped normalization in _is_loopback_bind must classify
    ::ffff:127.0.0.1 as loopback and ::ffff:<LAN-IP> as non-loopback even on
    CPython builds where IPv6Address.is_loopback reports False for IPv4-mapped
    loopbacks (pre-gh-117566 — <3.12.7 / <3.11.13). Patching is_loopback to
    False makes the normalization the only path to True, so this test fails if
    that normalization regresses (#719)."""
    import ipaddress

    from tortoise.__main__ import _is_loopback_bind

    monkeypatch.setattr(ipaddress.IPv6Address, "is_loopback", property(lambda self: False))
    assert _is_loopback_bind("::ffff:127.0.0.1") is True, \
        "IPv4-mapped loopback must stay loopback regardless of CPython patch level"
    assert _is_loopback_bind("::ffff:192.168.1.50") is False, \
        "IPv4-mapped LAN address must never classify as loopback"


@pytest.mark.parametrize("uri,remote", [
    # redis:// / rediss:// — always remote by convention (managed/shared
    # instances), even when the host string is loopback.
    ("redis://:pass@db.example.com:6379/0", True),
    ("rediss://:pass@localhost:6379/0", True),
    # docker:// loopback hosts — local instance, no warning.
    ("docker://:pass@localhost:6379/0", False),
    ("docker://:pass@127.0.0.1:6379/0", False),
    ("docker://:pass@127.0.0.2:6379/0", False),
    ("docker://:pass@[::1]:6379/0", False),
    ("docker://:pass@[::ffff:127.0.0.1]:6379/0", False),
    # docker:// non-loopback hosts — valid remote FalkorDB targets, warn.
    ("docker://:pass@db.example.com:6379/0", True),
    ("docker://user:pass@10.0.0.5:6379/tortoise", True),
    ("docker://:pass@192.168.1.50:6379/0", True),
    ("docker://:pass@[::ffff:192.168.1.50]:6379/0", True),
    # Not a URI — never remote.
    ("", False),
    ("/tmp/tortoise.db", False),
])
def test_db_uri_remote_classifies_remote_docker_hosts(uri, remote):
    """The remote-target warnings must fire for every genuinely remote DB
    target — including docker://user:pass@remote-host:6379/... (a valid
    remote FalkorDB instance) — and stay silent for loopback docker://
    instances (localhost / 127.0.0.0/8 / ::1 / IPv4-mapped). redis:// /
    rediss:// are always remote by convention (#719 P2, round 7)."""
    from tortoise.__main__ import _db_uri_remote

    assert _db_uri_remote(uri) is remote, f"{uri!r}: expected remote={remote}"


@pytest.mark.parametrize("uri,warns", [
    ("docker://:pass@db.example.com:6379/0", True),
    ("docker://user:pass@10.0.0.5:6379/tortoise", True),
    ("docker://:pass@localhost:6379/0", False),
    ("docker://:pass@127.0.0.1:6379/0", False),
    ("redis://:pass@db.example.com:6379/0", True),
])
def test_serve_http_remote_db_target_warning_fires_for_remote_docker(monkeypatch, tmp_path, capsys, uri, warns):
    """serve --http must print the 'Remote/cloud DB target' warning for
    docker:// targets on non-loopback hosts exactly as it does for redis:// /
    rediss://, and stay silent for loopback docker:// instances — operators
    bootstrapping keys / serving against a remote docker:// instance get the
    same remote-graph awareness as cloud targets (#719 P2, round 7)."""
    from tortoise.__main__ import main

    calls = _patch_serve_runtime(monkeypatch, tmp_path)
    monkeypatch.setenv("TORTOISE_DB_URI", uri)
    rc = main(["serve", "--http", "--auth", "static", "--api-key", "tt_x"])
    assert rc == 0
    assert calls["uvicorn"] is not None, "uvicorn.run was not invoked"
    out = capsys.readouterr().out
    assert "DB target" in out, "DB target diagnostic line must still print"
    if warns:
        assert "Remote/cloud DB target" in out, f"{uri}: remote-target warning must fire"
    else:
        assert "Remote/cloud DB target" not in out, f"{uri}: loopback target must not warn"



def test_bind_allowed_hosts_ipv4_mapped_normalized(monkeypatch):
    """_bind_allowed_hosts must apply the same ipv4_mapped normalization as
    _is_loopback_bind: ::ffff:127.0.0.1 stays loopback (no Host-guard
    entries) and ::ffff:<LAN-IP> seeds the plain embedded IPv4 — the address
    clients actually send in the Host header — even on CPython builds where
    IPv6Address.is_loopback reports False for mapped addresses
    (pre-gh-117566 — <3.12.7 / <3.11.13). Without normalization the guard
    would hold '::ffff:192.168.1.50', which never matches a client's
    '192.168.1.50' Host header → 421 despite the #719 Host-guard fix."""
    import ipaddress

    from tortoise.__main__ import _bind_allowed_hosts

    monkeypatch.setattr(ipaddress.IPv6Address, "is_loopback", property(lambda self: False))
    assert _bind_allowed_hosts("::ffff:127.0.0.1", None) == [], \
        "mapped loopback must not seed Host-guard entries"
    assert _bind_allowed_hosts("::ffff:192.168.1.50", None) == ["192.168.1.50"], \
        "mapped LAN bind must seed the embedded IPv4, not the ::ffff: form"


@pytest.mark.parametrize("bind", ["0.0.0.0", "192.168.1.50", "::", "mybox.local", "::ffff:192.168.1.50"])
def test_serve_http_none_nonloopback_refused_without_override(monkeypatch, tmp_path, capsys, bind):
    """--auth none + non-loopback/wildcard bind is REFUSED (exit 1) unless
    --allow-insecure-no-auth — there is no auth to enforce, so the old
    warning-only path could expose full MCP access (P2 security). mybox.local
    exercises the unresolvable-hostname branch of _is_loopback_bind;
    ::ffff:192.168.1.50 the IPv4-mapped non-loopback half of the mapping."""
    import tortoise.mcp_server as mcp_mod
    import uvicorn

    from tortoise.__main__ import main

    def _boom(**kw):
        raise AssertionError("create_http_app must not run for refused insecure serve")

    monkeypatch.setattr(mcp_mod, "create_http_app", _boom)
    # uvicorn is patched too so a refusal regression fails loudly instead of
    # falling through to a real socket bind / CI hang.
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)

    rc = main(["serve", "--http", "--auth", "none", "--bind", bind])
    assert rc == 1, f"auth=none + {bind} must be refused (exit 1) without the override"
    err = capsys.readouterr().err
    assert "allow-insecure-no-auth" in err
    assert bind in err


@pytest.mark.parametrize("bind", ["192.168.1.50", "mybox.local", "::ffff:192.168.1.50"])
def test_serve_http_none_nonloopback_override_works(monkeypatch, tmp_path, capsys, bind):
    """--allow-insecure-no-auth explicitly opts into auth=none on a
    non-loopback bind (LAN IP, unresolvable hostname, or IPv4-mapped LAN
    address) — starts (rc 0), warns loudly, passes auth_mode=none."""
    from tortoise.__main__ import main

    calls = _patch_serve_runtime(monkeypatch, tmp_path)
    rc = main(["serve", "--http", "--auth", "none", "--bind", bind,
               "--allow-insecure-no-auth", "--port", "8124"])
    assert rc == 0
    assert calls["uvicorn"] is not None, "uvicorn.run was not invoked"
    assert calls["uvicorn"]["host"] == bind
    assert calls["uvicorn"]["port"] == 8124
    app_kw = calls["create_http_app"]
    assert app_kw["auth_mode"] == "none"
    # IPv4-mapped binds normalize to the embedded IPv4 — the address clients
    # actually send in the Host header (#719 review fix).
    expected_host = bind.removeprefix("::ffff:")
    assert expected_host in app_kw["allowed_hosts"]
    out = capsys.readouterr().out
    assert "reachable on your network" in out
    # The none-mode warning is distinct — auth is NOT enforced, so the text
    # must say so (a regression back to the generic 'ensure auth is enforced'
    # wording would be unfulfillable in none mode — #719 P2).
    assert "NOT enforced" in out


def test_serve_http_static_hostname_bind_seeds_guard(monkeypatch, tmp_path, capsys):
    """Hostname bind (mybox.local) auto-seeds the Host guard with the bind
    itself (the `elif bind != "localhost"` branch of _bind_allowed_hosts) —
    the #719 P1 421 fix for clients connecting via a DNS name. auth=static so
    it takes the passing path (auth=none + non-loopback is refused)."""
    from tortoise.__main__ import main

    calls = _patch_serve_runtime(monkeypatch, tmp_path)
    rc = main(["serve", "--http", "--auth", "static", "--api-key", "tt_x",
               "--bind", "mybox.local"])
    assert rc == 0
    assert calls["uvicorn"] is not None, "uvicorn.run was not invoked"
    app_kw = calls["create_http_app"]
    assert app_kw["auth_mode"] == "static"
    assert "mybox.local" in app_kw["allowed_hosts"], \
        "hostname bind must seed the Host guard with the bind itself"
    out = capsys.readouterr().out
    assert "reachable on your network" in out


def test_serve_http_static_missing_key_error_path(monkeypatch, tmp_path, capsys):
    """--auth static without --api-key / TORTOISE_API_KEY → exit 1 with a
    clear message, and create_http_app must never be called."""
    import tortoise.mcp_server as mcp_mod

    from tortoise.__main__ import main

    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)

    def _boom(**kw):
        raise AssertionError("create_http_app must not run without a key")

    monkeypatch.setattr(mcp_mod, "create_http_app", _boom)
    rc = main(["serve", "--http", "--auth", "static"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "--api-key" in err and "TORTOISE_API_KEY" in err


def test_serve_http_api_key_requires_static_auth(monkeypatch, tmp_path, capsys):
    """--api-key with non-static auth (tenant default) → exit 1 with a clean
    error pointing at --auth static — the key must never be silently ignored
    (#719 P2)."""
    import tortoise.mcp_server as mcp_mod

    from tortoise.__main__ import main

    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)

    def _boom(**kw):
        raise AssertionError("create_http_app must not run when --api-key is ignored")

    monkeypatch.setattr(mcp_mod, "create_http_app", _boom)
    # default --auth (tenant) + --api-key: refused, not silently ignored
    rc = main(["serve", "--http", "--api-key", "tt_x"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "--auth static" in err, "error must tell the user to pass --auth static"
    assert "tenant" in err, "error must name the active auth mode"
    # explicit non-static modes are refused too
    rc = main(["serve", "--http", "--auth", "none", "--api-key", "tt_x"])
    assert rc == 1
    assert "--auth static" in capsys.readouterr().err


# ── 4+5. local HTTP roundtrip with the bootstrap key ──────────────────────

def test_local_http_roundtrip_lands_in_team_graph(local_db, monkeypatch):
    """key create → tenant app: no-auth 401; tools/list with the key works;
    a write lands in the canonical team_{team_id} graph (not an empty or
    orphaned namespace); Origin header accepted."""
    from fastapi.testclient import TestClient

    db, key, env = local_db
    # env mutation restored even on failure (monkeypatch = pytest try/finally)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(db))

    # team id from the registry
    sdk = TortoiseSDK(namespace="registry")
    team_id = sdk._get_registry().query(
        "MATCH (k:APIKey) RETURN k.team_id"
    ).result_set[0][0]

    wrapper = _boot_tenant_app(db)
    accept = "application/json, text/event-stream"
    headers = {"Authorization": f"Bearer {key}", "Accept": accept}

    with TestClient(wrapper) as c:
        # no auth → 401 (auth boundary)
        r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                   headers={"Accept": accept})
        assert r.status_code == 401

        # tools/list with the bootstrap key → 200, real tools
        r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                   headers=headers)
        assert r.status_code == 200
        body = _parse_sse_json(r)
        tools = body["result"]["tools"]
        names = {t["name"] for t in tools}
        assert "tortoise_create_point" in names
        assert len(tools) > 5

        # write → lands in team_{team_id}
        r = c.post("/mcp", json={
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "tortoise_create_point",
                       "arguments": {"kind": "observation", "content": "roundtrip"}},
        }, headers=headers)
        body = _parse_sse_json(r)
        assert not body["result"]["isError"], body

        team_sdk = TortoiseSDK(namespace=team_id)
        pts = team_sdk._get_proj().g.query("MATCH (p:Point) RETURN count(p)").result_set
        assert pts and pts[0][0] >= 1, "write must land in the team graph"

        # isolation: default 'tortoise' graph must NOT hold it
        plain = TortoiseSDK()
        pts2 = plain._get_proj().g.query("MATCH (p:Point) RETURN count(p)").result_set
        assert pts2 and pts2[0][0] == 0, "team write must not leak into default graph"

        # Origin header (real MCP clients send it) → accepted
        r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
                   headers={**headers, "Origin": "http://127.0.0.1:8000"})
        assert r.status_code == 200


@pytest.mark.parametrize("bind,port", [("0.0.0.0", 8000), ("::", 8000), ("0.0.0.0", 9000)])
def test_key_create_wildcard_bind_prints_lan_note(local_db, bind, port):
    """#719 P2: `key create --bind 0.0.0.0` (the documented mirror of
    'serve --http --bind 0.0.0.0') must print the LAN-address correction —
    the printed wildcard URL is unusable for clients, and the note used to
    fire only on full defaults (suppressed exactly when needed most).
    Wildcard + non-default port must also show it."""
    db, key, env = local_db
    env["TORTOISE_DB_PATH"] = str(db)
    proc = subprocess.run(
        [sys.executable, "-m", "tortoise", "key", "create", "--name", "test",
         "--bind", bind, "--port", str(port)],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert f"http://{bind}:{port}/mcp" in proc.stdout
    assert "LAN address" in proc.stdout
    assert f"never {bind}" in proc.stdout


def test_key_create_default_bind_still_prints_mirror_hint(local_db):
    """The defaults case keeps the mirror hint (pass --bind/--port to match
    a custom serve setup) — regression guard for the elif branch."""
    db, key, env = local_db
    env["TORTOISE_DB_PATH"] = str(db)
    proc = subprocess.run(
        [sys.executable, "-m", "tortoise", "key", "create", "--name", "test"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "assumes the default serve bind/port" in proc.stdout


def test_bootstrap_key_persists_and_verifies(local_db):
    """The key printed by `tortoise key create` authenticates via apikey_verify
    in a FRESH process (survives restart on the canonical DB)."""
    db, key, env = local_db
    env["TORTOISE_DB_PATH"] = str(db)
    code = (
        "import os\n"
        "from tortoise.sdk import TortoiseSDK\n"
        "sdk = TortoiseSDK(namespace='registry')\n"
        "res = sdk.apikey_verify('" + key + "')\n"
        "assert res and res.get('team_id'), 'key must verify'\n"
        "print('VERIFIED')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "VERIFIED" in proc.stdout
