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
def local_db(tmp_path):
    """A canonical embedded DB + a bootstrap key via the real CLI."""
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

def test_stdio_refusal_names_real_alternatives_not_health_server():
    """The #702 dead end: stdio + TORTOISE_API_KEY must name serve --http /
    hosted URL / dev-mode — never the health-server (which has no MCP surface)."""
    import tortoise.mcp_server as m
    import inspect
    src = inspect.getsource(m._safe)
    assert "serve --http" in src, "stdio refusal must recommend serve --http"
    assert "api.premiselabs.co/mcp" in src, "stdio refusal must name hosted URL"
    assert "health-server" not in src, "health-server is not an MCP endpoint — must not be recommended"


# ── 2. auth.py import-crash message content ────────────────────────────────

def test_auth_import_crash_message_guides_unset():
    """auth.py: with TORTOISE_API_KEY but no pepper, the error must tell the
    user to UNSET the key for local stdio (and point at serve --http)."""
    import tortoise.auth as a
    import inspect
    src = inspect.getsource(a)
    assert "UNSET" in src and "TORTOISE_API_KEY" in src
    assert "serve --http" in src
    assert "health-server" not in src


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


def test_serve_http_main_dispatch_static_nonloopback_bind(monkeypatch, tmp_path):
    """Real main() dispatch: `serve --http --auth static --bind <LAN-IP>`
    reaches uvicorn with that bind AND passes the bind through to the fastmcp
    Host guard via allowed_hosts (fixes the 421 Misdirected Request — #719 P1:
    a non-loopback bind is unreachable for LAN clients otherwise)."""
    from tortoise.__main__ import main

    calls = _patch_serve_runtime(monkeypatch, tmp_path)
    rc = main(["serve", "--http", "--auth", "static", "--api-key", "tt_x",
               "--bind", "192.168.1.50", "--port", "8123"])
    assert rc == 0
    assert calls["uvicorn"]["host"] == "192.168.1.50"
    assert calls["uvicorn"]["port"] == 8123
    app_kw = calls["create_http_app"]
    assert app_kw["auth_mode"] == "static"
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
    assert not app_kw["allowed_hosts"]


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


def test_serve_http_any_interface_bind_derives_machine_hosts(monkeypatch, tmp_path):
    """0.0.0.0 bind: clients reach us by the machine's hostname/LAN address,
    not the wildcard — the guard must be seeded with the machine's own
    identity so the 'reachable on your network' warning is actually TRUE."""
    import socket

    from tortoise.__main__ import main

    calls = _patch_serve_runtime(monkeypatch, tmp_path)
    rc = main(["serve", "--http", "--auth", "none", "--bind", "0.0.0.0"])
    assert rc == 0
    hosts = calls["create_http_app"]["allowed_hosts"]
    assert socket.gethostname().lower() in hosts, \
        "any-interface bind must auto-derive the machine hostname"


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
