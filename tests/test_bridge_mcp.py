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

#: Warm-up attempts, and the pause between them. Both are BOUNDED and their
#: failure is not a verdict — see `_warm_daemon`.
_WARM_ATTEMPTS = 3
_WARM_PAUSE_S = 2.0


def _warm_daemon() -> None:
    """Boot the daemon's embedded DB BEFORE anything this file asserts on.

    #5049's doctrine — *a verdict from ambient machine speed is not a verdict* —
    applied to the one thing the `daemon_url` fixture left cold. `server.started`
    means the socket accepts connections; it does NOT mean the embedded store is
    up, so the FIRST dispatched tool call pays the whole redislite boot plus
    graph start, and it must fit inside the 10s transport wait bound
    (`mcp_auth._TRANSPORT_WAIT_BOUND_S`, enforced in
    `mcp_server._await_under_mcp_wait_bound`).

    Measured on shard `test (d)`, job 126325424780 (head `9fe385d09c69`): the
    first `tortoise_create_point` — the call `test_push_creates_points_over_mcp`
    asserted on — returned at **10.27 s** with
    `transport wait bound (10s) exceeded: MCP tools/call tortoise_create_point`,
    so a bridge that WORKS read as `tortoise_push_failed`. Nothing about the
    push was wrong: the shard had nine sibling jobs on the same host.

    The refusal is the product's documented behaviour, not a fault in it ("the
    work may still complete on the server … retry only if repeating the
    operation is safe"), and `dedup=True` makes the retry safe here — so this
    absorbs it up to a bounded count and NEVER asserts on the outcome. What it
    buys is a condition: by the time the test's own assertions run, the store is
    warm and the bridge is judged on creating points, which is what this
    module exists to pin, rather than on cold-start latency.

    Deliberately NOT pinned by a test: a warm-up has no pre-fix code to redden
    on — the shape it removes is `10.27s > 10s` on a loaded host, which no
    assertion in this file can produce deliberately. The evidence is the CI
    log above.
    """
    from tortoise.mcp_client import call_tool

    for attempt in range(_WARM_ATTEMPTS):
        try:
            call_tool("tortoise_create_point", {
                "kind": "event",
                "content": "daemon warm-up (test fixture)",
                "authoredBy": "test-warmup",
                "dedup": True,
            })
            return
        except Exception:  # the legible refusal is what this absorbs
            if attempt + 1 < _WARM_ATTEMPTS:
                time.sleep(_WARM_PAUSE_S)


@pytest.fixture
def daemon_url(monkeypatch, tmp_path):
    """Start the MCP daemon (auth none, embedded DB) on an ephemeral port.

    Forces embedded DB env (conftest sets TORTOISE_DB_URI to a test container
    that isn't running — the daemon's SDK would retry-connect and hang).
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "")
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "daemon.db"))
    import uvicorn  # noqa: I001
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
    url = f"http://127.0.0.1:{port}/mcp"
    # Point the client at THIS daemon and warm its store before the test's own
    # assertions (see `_warm_daemon`). The test re-sets both below; setting them
    # here is what lets the warm-up reach the daemon at all.
    monkeypatch.setenv("TORTOISE_MCP_URL", url)
    monkeypatch.setenv("TORTOISE_API_KEY", "")
    _warm_daemon()
    yield url
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


def test_a_refused_write_keeps_the_ids_that_already_landed(monkeypatch):
    """#11153's audit item, for THIS caller: the surface now flags a refusal with
    `isError: true`, and `tortoise.mcp_client` runs the fastmcp client with
    `raise_on_error=True` — so a refusal arrives as a raised `ToolError`.

    Left uncaught it aborted the loop into `tortoise_push_failed`, DISCARDING the
    ids of the points that had already landed: a partial minutes push that cannot
    be reconciled, produced by a DESIGNED refusal (review round 4, measured).
    """
    from fastmcp.exceptions import ToolError

    import tortoise.mcp_client as mcp_client
    from integrations.crm.twenty import bridge

    monkeypatch.setattr(bridge, "tortoise_available", lambda: True)

    calls = {"n": 0}

    def _refusing_call_tool(_name, _args):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"id": "p1"}
        raise ToolError('{"error": "Team points limit reached (25000)", '
                        '"code": -32006}')

    monkeypatch.setattr(mcp_client, "call_tool", _refusing_call_tool)

    result = bridge.push_to_tortoise(SAMPLE_FRONTMATTER)

    assert result["status"] == "error"
    assert result["reason"] == "tortoise_push_partial", (
        "a refused write reported the whole push as failed, discarding what landed")
    assert result["points"][0]["id"] == "p1", result["points"]
    assert any("points limit" in f["error"] for f in result["failed"]), result


def test_a_push_where_nothing_landed_is_not_called_partial(monkeypatch):
    """The TERM has to follow what actually landed (review round 5).

    `tortoise_push_partial` means "some rows are on the graph, reconcile the ids";
    a push where every write failed — including a server CRASH, which is also a
    `ToolError` — must keep the `tortoise_push_failed` alarm rather than read as a
    partial reconciliation case.
    """
    from fastmcp.exceptions import ToolError

    import tortoise.mcp_client as mcp_client
    from integrations.crm.twenty import bridge

    monkeypatch.setattr(bridge, "tortoise_available", lambda: True)

    def _always_refuse(_name, _args):
        raise ToolError("Error calling tool: TypeError: 'NoneType' object is not "
                        "subscriptable")

    monkeypatch.setattr(mcp_client, "call_tool", _always_refuse)

    result = bridge.push_to_tortoise(SAMPLE_FRONTMATTER)

    assert result["status"] == "error"
    assert result["reason"] == "tortoise_push_failed", result
    assert result["failed"], "the per-write detail must survive the report"


class TestPointIdExtraction:
    """`_point_id` reads `content[0]`, the block the dispatch seam guarantees is the
    payload (#10084). Later blocks are PROSE — the graph disclosure, a retired-name
    warning — and must never be parsed as the answer."""

    @staticmethod
    def _result(*texts):
        import types

        return types.SimpleNamespace(
            content=[types.SimpleNamespace(text=t) for t in texts]
        )

    def test_the_id_comes_from_the_payload_block_not_a_later_disclosure(self):
        from integrations.crm.twenty.bridge import _point_id

        result = self._result(
            '{"id": "pt_1"}',
            "Graph: this write targeted the workspace default graph (default).",
        )
        assert _point_id(result) == ("pt_1", None)

    def test_a_non_dict_payload_stays_a_failure_and_never_raises(self):
        """A non-dict payload is `unparseable_result`. The trailing prose block
        must not turn it into an id, and must not raise out of the extractor."""
        from integrations.crm.twenty.bridge import _point_id

        result = self._result(
            "[1, 2]", "Graph: this write targeted the workspace default graph."
        )
        assert _point_id(result) == (None, "unparseable_result")


class TestNoEngineImports:
    def test_integrations_have_zero_sdk_imports(self):
        """AST gate (AC4): integrations connect via MCP — no engine imports.

        Scope limit (#9929 review advisory, disclosed): this gate sees IMPORT
        NODES only. An integration that runs IN the Tortoise container and
        reaches the engine through a module-level string (the writer script in
        ``integrations/cli-extraction/extract_turns.py``) is invisible to
        ``ast.parse``, and the running query lives in a test file this gate
        does not scan — so a green run here is NOT proof that no integration
        names the engine. The in-container access is deliberate and documented
        in ``integrations/cli-extraction/README.md`` ("Its writes go through
        public SDK doors").
        """
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
