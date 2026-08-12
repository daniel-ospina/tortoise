"""Self-host daemon tests (#338 T1.4).

ASGI-level (TestClient, no socket) for /health, /health/ready, MCP handshake
+ grep gate: selfhost module must not import hosted/supabase machinery.
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest


def _client_for_env(monkeypatch, tmp_path, **env):
    """Build a TestClient over the selfhost app with the given env.

    conftest.py sets only TORTOISE_SECRET_PEPPER (no URI); clear
    TORTOISE_DB_URI for embedded-mode tests so selfhost's URI check falls
    through to TORTOISE_DB_PATH.
    """
    from starlette.testclient import TestClient

    from tortoise import selfhost

    # Re-point env BEFORE importing selfhost (reads env at import time).
    if "TORTOISE_DB_URI" not in env:
        monkeypatch.setenv("TORTOISE_DB_URI", "")  # force embedded mode
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    if "TORTOISE_DB_PATH" not in env:
        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "selfhost.db"))

    import importlib

    importlib.reload(selfhost)
    return TestClient(selfhost.app)


def test_embedded_banner_stderr(monkeypatch, tmp_path, capsys):
    """#942: the selfhost daemon prints the loud SINGLE-WRITER / EVAL-ONLY
    banner to stderr when started in embedded mode (no TORTOISE_DB_URI)."""
    tc = _client_for_env(monkeypatch, tmp_path)
    with tc:
        pass
    err = capsys.readouterr().err
    assert "SINGLE-WRITER" in err and "EVAL ONLY" in err


def test_uri_mode_no_banner(monkeypatch, tmp_path, capsys):
    """#942 negative pin: no banner when TORTOISE_DB_URI is set."""
    tc = _client_for_env(
        monkeypatch, tmp_path,
        TORTOISE_DB_URI="docker://:falkordb@localhost:6379/tortoise",
    )
    with tc:
        pass
    err = capsys.readouterr().err
    assert "SINGLE-WRITER" not in err


class TestHealth:
    def test_health_liveness(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path, TORTOISE_API_KEY="k")
        with tc:
            r = tc.get("/health")
            assert r.status_code == 200
            assert r.json()["status"] == "ok"

    def test_health_ready_embedded(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path, TORTOISE_API_KEY="k")
        with tc:
            r = tc.get("/health/ready")
            assert r.status_code == 200
            assert r.json()["status"] == "ready"


class TestMCPHandshake:
    def test_initialize_handshake_no_auth_mode(self, monkeypatch, tmp_path):
        # auth_mode="none" (no key) — initialize passes through to MCP server
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            r = tc.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "selfhost-test", "version": "0"},
                    },
                },
                headers={"Accept": "application/json, text/event-stream",
                         "Content-Type": "application/json"},
            )
            assert r.status_code in (200, 202)

    def test_static_mode_requires_key(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path, TORTOISE_API_KEY="s3cret")
        with tc:
            r = tc.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-03-26",
                                 "capabilities": {},
                                 "clientInfo": {"name": "t", "version": "0"}}},
                headers={"Accept": "application/json, text/event-stream",
                         "Content-Type": "application/json"},
            )
            assert r.status_code == 401
            r2 = tc.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-03-26",
                                 "capabilities": {},
                                 "clientInfo": {"name": "t", "version": "0"}}},
                headers={"Accept": "application/json, text/event-stream",
                         "Content-Type": "application/json",
                         "Authorization": "Bearer s3cret"},
            )
            assert r2.status_code in (200, 202)


class TestGrepGate:
    def test_no_hosted_or_supabase_machinery(self):
        """selfhost must not import hosted_api / supabase / TeamResolution."""
        import ast
        from pathlib import Path

        src = Path("tortoise/selfhost.py").read_text()
        tree = ast.parse(src)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imports.add(node.module or "")
            elif isinstance(node, ast.Import):
                for n in node.names:
                    imports.add(n.name.split(".")[0])
        banned = {"hosted_api", "supabase", "mcp_auth"}
        assert not (imports & banned), f"banned imports: {imports & banned}"


class TestToolsList:
    def test_tools_list_returns_tools(self, monkeypatch, tmp_path):
        """tools/list works directly (stateless_http) and returns tools (T1.4)."""
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            r = tc.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                      "params": {}},
                headers={"Accept": "application/json, text/event-stream",
                         "Content-Type": "application/json"},
            )
            assert r.status_code in (200, 202)
            assert "result" in r.text or "tools" in r.text


class TestOriginProtection:
    def test_origin_bearing_request_ok_for_allowed_origin(self, monkeypatch, tmp_path):
        """allowed_origins pin: Origin http://localhost:8000 passes (T1.2)."""
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            r = tc.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-03-26",
                                 "capabilities": {},
                                 "clientInfo": {"name": "t", "version": "0"}}},
                headers={"Accept": "application/json, text/event-stream",
                         "Content-Type": "application/json",
                         "Origin": "http://localhost:8000"},
            )
            assert r.status_code in (200, 202)

    def test_disallowed_origin_rejected(self, monkeypatch, tmp_path):
        """Origin outside the allowlist → 403 (host_origin_protection)."""
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            r = tc.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-03-26",
                                 "capabilities": {},
                                 "clientInfo": {"name": "t", "version": "0"}}},
                headers={"Accept": "application/json, text/event-stream",
                         "Content-Type": "application/json",
                         "Origin": "https://evil.example"},
            )
            assert r.status_code == 403



class TestStartupGuard:
    def test_refuses_none_mode_on_non_loopback(self, monkeypatch, tmp_path):
        """Fail-closed (code-review P1): no key + non-loopback bind → SystemExit."""
        import importlib

        monkeypatch.setenv("TORTOISE_DB_URI", "")
        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "guard.db"))
        monkeypatch.setenv("TORTOISE_API_KEY", "")
        monkeypatch.setenv("TORTOISE_HOST", "0.0.0.0")

        with pytest.raises(SystemExit) as exc:
            importlib.reload(__import__("tortoise.selfhost", fromlist=["app"]))
        assert "REFUSING TO START" in str(exc.value)

    def test_allows_static_mode_on_non_loopback(self, monkeypatch, tmp_path):
        """Key set → auth_mode=static → non-loopback bind is fine."""
        import importlib

        monkeypatch.setenv("TORTOISE_DB_URI", "")
        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "guard2.db"))
        monkeypatch.setenv("TORTOISE_API_KEY", "k")
        monkeypatch.setenv("TORTOISE_HOST", "0.0.0.0")
        importlib.reload(__import__("tortoise.selfhost", fromlist=["app"]))
        # No exception = guard passed (static mode ok on non-loopback)


class TestSubprocessSmoke:
    def test_python_m_selfhost_real_http(self, tmp_path):
        """T1.4 subprocess smoke: `python -m tortoise.selfhost` on an
        ephemeral port serves /health + MCP initialize over real HTTP."""
        import socket
        import subprocess
        import sys
        import time
        import urllib.request
        import urllib.error

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        env = dict(os.environ)
        env["TORTOISE_DB_URI"] = ""
        env["TORTOISE_DB_PATH"] = str(tmp_path / "smoke.db")
        env["TORTOISE_PORT"] = str(port)
        env["TORTOISE_HOST"] = "127.0.0.1"
        env["TORTOISE_API_KEY"] = "smoke-key"
        env["PYTHONPATH"] = os.getcwd()

        proc = subprocess.Popen(
            [sys.executable, "-m", "tortoise.selfhost"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            # Wait for /health (up to 30s)
            healthy = False
            for _ in range(60):
                if proc.poll() is not None:
                    break
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                        if r.status == 200:
                            healthy = True
                            break
                except (urllib.error.URLError, ConnectionError, OSError):
                    time.sleep(0.5)
            assert healthy, "selfhost did not become healthy in 30s"

            # Real MCP initialize with the static key (follow 307 redirects
            # — fastmcp streamable-http may redirect POST /mcp to /mcp/).
            import json as _json

            def _post_with_redirect(url, data, headers, redirects=5):
                while redirects > 0:
                    try:
                        req = urllib.request.Request(url, data=data, headers=headers)
                        return urllib.request.urlopen(req, timeout=10)
                    except urllib.error.HTTPError as e:
                        if e.code in (307, 308) and e.headers.get("Location"):
                            url = e.headers["Location"]
                            redirects -= 1
                            continue
                        raise
                raise RuntimeError("too many redirects")

            body = _json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-03-26",
                           "capabilities": {},
                           "clientInfo": {"name": "smoke", "version": "0"}},
            }).encode()
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": "Bearer smoke-key",
            }
            with _post_with_redirect(f"http://127.0.0.1:{port}/mcp", body, headers) as r:
                assert r.status in (200, 202), f"initialize status {r.status}"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
