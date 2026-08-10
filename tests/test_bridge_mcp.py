"""Bridge MCP conversion tests (#338 T2.2).

1. push_to_tortoise pushes points through the daemon over MCP (no engine imports)
2. Daemon down → graceful skip (tortoise_unavailable)
3. Static AST gate: zero `from tortoise.sdk import` in integrations/
"""
from __future__ import annotations

import os
import sys
import threading
import time

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

# Repo root on sys.path so `import integrations` works under any pytest
# invocation (console script does not add CWD; `python -m pytest` does).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

SAMPLE_FRONTMATTER = {
    "title": "Test Meeting",
    "date": "2026-08-07",
    "decisions": [{"text": "Ship the service model."}],
    "commitments": [{"text": "Write the license."}],
}


@pytest.fixture
def daemon_url(monkeypatch, tmp_path):
    """Start the MCP daemon (auth none, embedded DB) on an ephemeral port.

    Forces embedded DB env (conftest sets TORTOISE_DB_URI to a test container
    that isn't running — the daemon's SDK would retry-connect and hang).
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "")
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "daemon.db"))
    import uvicorn
    from contextlib import asynccontextmanager
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from tortoise.mcp_server import create_http_app

    mcp_app = create_http_app(allowed_origins=["http://localhost:8000"], auth_mode="none")

    @asynccontextmanager
    async def _lifespan(parent_app):
        # Starlette Mount does NOT propagate sub-app lifespan — compose it so
        # the StreamableHTTPSessionManager task group initializes (same fix as
        # hosted_api._lifespan / selfhost._lifespan).
        async with mcp_app.lifespan(mcp_app):
            yield

    app = Starlette(lifespan=_lifespan, routes=[Mount("/mcp", app=mcp_app)])

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 20
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "daemon did not start"
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    t.join(timeout=10)


class TestBridgePush:
    def test_push_creates_points_over_mcp(self, monkeypatch, daemon_url):
        monkeypatch.setenv("TORTOISE_MCP_URL", daemon_url)
        monkeypatch.setenv("TORTOISE_API_KEY", "")

        from integrations.crm.twenty.bridge import push_to_tortoise

        result = push_to_tortoise(SAMPLE_FRONTMATTER)
        assert result["status"] == "ok"
        types = [p["type"] for p in result["points"]]
        assert types == ["meeting", "decision", "commitment"]
        # ids resolved from the daemon (not None)
        assert all(p.get("id") for p in result["points"])

    def test_daemon_down_graceful_skip(self, monkeypatch):
        monkeypatch.setenv("TORTOISE_MCP_URL", "http://127.0.0.1:1/mcp")
        monkeypatch.setenv("TORTOISE_API_KEY", "")

        from integrations.crm.twenty.bridge import push_to_tortoise

        result = push_to_tortoise(SAMPLE_FRONTMATTER)
        assert result == {"status": "skipped", "reason": "tortoise_unavailable"}


class TestNoEngineImports:
    def test_integrations_have_zero_sdk_imports(self):
        """AST gate (AC4): integrations connect via MCP — no engine imports."""
        import ast
        from pathlib import Path

        root = Path("integrations")
        offenders = []
        for py in root.rglob("*.py"):
            tree = ast.parse(py.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "tortoise.sdk":
                    offenders.append(f"{py}:{node.lineno}")
                if isinstance(node, ast.Import):
                    for n in node.names:
                        if n.name == "tortoise" or n.name.startswith("tortoise.sdk"):
                            offenders.append(f"{py}:{node.lineno}")
        assert not offenders, f"engine imports in integrations/: {offenders}"

    def test_bridge_imports_mcp_client(self):
        import ast
        from pathlib import Path

        src = Path("integrations/crm/twenty/bridge.py").read_text()
        tree = ast.parse(src)
        assert any(
            isinstance(n, ast.ImportFrom) and n.module == "tortoise.mcp_client"
            for n in ast.walk(tree)
        ), "bridge must import tortoise.mcp_client"
