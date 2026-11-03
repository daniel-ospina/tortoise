"""The stdio ↔ Streamable-HTTP bridge (#10605).

The bridge is the durable half of #10605: a harness config that carries
`Authorization: Bearer ${VAR}` expands `${VAR}` ONCE, when the harness starts, so
a long-lived session replays an empty-or-stale header forever. The bridge is a
local stdio command instead, and resolves the credential on EVERY forwarded
request.

Every assertion here is end-to-end through a REAL subprocess: the tests write a
JSON-RPC line to the bridge's stdin and read what it wrote to stdout, while the
upstream stub records the headers it actually received. Nothing is asserted by
inspecting bridge internals — the header on the wire is the observable that
distinguishes "resolved per request" from "captured at start".
"""
from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")


# ── A spec-shaped Streamable-HTTP upstream that records what it receives ──────


class _Upstream:
    """Minimal MCP endpoint: `initialize` + `tools/list`, and an auth-refusal mode.

    It is a stub rather than the real daemon on purpose — these tests are about
    what the BRIDGE transmits (headers, session id, one message per line), not
    about the server's tool surface. The real-daemon interop has its own test
    below.
    """

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.refuse = False
        #: 404 every request that carries a session id (an expired session).
        self.expire = False
        #: Answer with an SSE stream that carries only a NOTIFICATION — a reply
        #: with no JSON-RPC message for the id that asked.
        self.no_reply = False
        #: Quote the REJECTED Authorization header back in the 401 body — the shape
        #: that would carry a credential into whatever the bridge relays.
        self.echo_credential = False
        #: Echo the credential in a 200 body that answers NO id (the no-reply guard
        #: quotes that body back, so it is a second place the value can surface).
        self.echo_in_body = False

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers") or ()}
        payload = json.loads(body) if body else None
        self.requests.append({"headers": headers, "payload": payload})

        if self.expire and "mcp-session-id" in headers:
            await _respond(send, 404, {"error": "session not found"})
            return

        if self.no_reply:
            await _respond(send, 200, None, content_type="text/event-stream", raw=(
                b'data: {"jsonrpc": "2.0", "method": "notifications/message", '
                b'"params": {"level": "info", "data": "no reply for you"}}\n\n'
            ))
            return

        if self.refuse:
            echoed = headers.get("authorization", "")
            if self.echo_credential and echoed:
                # A server is FREE to quote what it rejected. The bridge quotes
                # server text into its refusal, so this is the shape that would
                # carry a credential into a transcript or a CI log.
                message = f"Unauthorized: the key {echoed} is invalid"
            else:
                message = ("Unauthorized: invalid or missing Bearer token. "
                           "Expected format: Authorization: Bearer tt_<key>/tk_<key>")
            await _respond(send, 401, {
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32001, "message": message},
            })
            return

        if self.echo_in_body:
            # 200, echoed credential, and NOTHING for the id that asked.
            await _respond(send, 200, {
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32001, "message": (
                    f"the key {headers.get('authorization', '')} is invalid")},
            })
            return

        method = payload.get("method") if isinstance(payload, dict) else None
        if method == "initialize":
            await _respond(send, 200, {
                "jsonrpc": "2.0", "id": payload["id"],
                "result": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "serverInfo": {"name": "stub", "version": "0"}},
            }, extra_headers=[(b"mcp-session-id", b"sess-1")])
            return
        if method == "tools/list":
            await _respond(send, 200, {
                "jsonrpc": "2.0", "id": payload["id"],
                "result": {"tools": [{"name": "tortoise_search"}]},
            })
            return
        if isinstance(payload, dict) and "id" not in payload:
            await _respond(send, 202, None)
            return
        await _respond(send, 200, {
            "jsonrpc": "2.0", "id": payload.get("id") if isinstance(payload, dict) else None,
            "result": {},
        })


async def _respond(send, status: int, body: dict | None, extra_headers=(),
                   content_type: str = "application/json", raw: bytes | None = None) -> None:
    if raw is None:
        raw = json.dumps(body).encode() if body is not None else b""
    headers = [
        (b"content-type", content_type.encode()),
        (b"content-length", str(len(raw)).encode()),
        *extra_headers,
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": raw})


def _serve(app) -> str:
    """Run `app` under uvicorn on an ephemeral port; yield its /mcp URL."""
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "upstream did not start"
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def upstream():
    """(url, stub)."""
    stub = _Upstream()
    for url in _serve(stub):
        yield url, stub


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated HOME with no credential, and no ambient key in the env."""
    directory = tmp_path / "home"
    directory.mkdir()
    monkeypatch.setenv("HOME", str(directory))
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    monkeypatch.delenv("TORTOISE_MCP_API_KEY", raising=False)
    return directory


def _write_credential(home: Path, key: str, api_url: str | None = None) -> Path:
    """Write the store the onboarding CLI writes.

    `api_url` is set wherever the store is the credential source, because #2369
    D1.1 makes a STORE-sourced key resolve its endpoint from that same file (or
    the built-in default) — never from the harness's `TORTOISE_MCP_URL`.
    """
    path = home / ".tortoise" / "credentials.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {"api_key": key}
    if api_url:
        payload["api_url"] = api_url
    path.write_text(json.dumps(payload))
    return path


# ── The bridge subprocess ────────────────────────────────────────────────────


class _Bridge:
    def __init__(self, url: str, home: Path, extra_env: dict[str, str] | None = None):
        env = {k: v for k, v in os.environ.items()
               if k not in ("TORTOISE_API_KEY", "TORTOISE_MCP_API_KEY")}
        env.update({"HOME": str(home), "TORTOISE_MCP_URL": url})
        env.update(extra_env or {})
        self.stderr_lines: list[str] = []
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "tortoise.mcp_bridge"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, env=env, cwd=str(REPO_ROOT),
        )
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        for line in self.proc.stderr:  # type: ignore[union-attr]
            self.stderr_lines.append(line.rstrip())

    def send(self, message: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def read(self, timeout: float = 60.0) -> dict:
        """The next JSON-RPC line the bridge wrote — fails loudly on silence."""
        assert self.proc.stdout is not None
        ready, _, _ = select.select([self.proc.stdout], [], [], timeout)
        assert ready, f"the bridge wrote nothing within {timeout}s: {self.stderr_lines}"
        line = self.proc.stdout.readline()
        assert line.strip(), f"empty line on stdout; stderr: {self.stderr_lines}"
        return json.loads(line)

    def read_nothing(self, timeout: float = 2.0) -> None:
        assert self.proc.stdout is not None
        ready, _, _ = select.select([self.proc.stdout], [], [], timeout)
        assert not ready, f"expected no stdout message, got: {self.proc.stdout.readline()!r}"

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=15)
        finally:
            if self.proc.poll() is None:  # pragma: no cover - defensive
                self.proc.kill()


@pytest.fixture
def bridge_factory(upstream, home):
    bridges: list[_Bridge] = []

    def _make(extra_env: dict[str, str] | None = None) -> _Bridge:
        bridge = _Bridge(upstream[0], home, extra_env)
        bridges.append(bridge)
        return bridge

    yield _make
    for bridge in bridges:
        bridge.close()


_INITIALIZE = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "test", "version": "0"}},
}


def _auth_headers(stub: _Upstream) -> list[str | None]:
    return [r["headers"].get("authorization") for r in stub.requests]


# ── The defect: a captured credential never rotates ─────────────────────────


class TestPerRequestCredential:
    def test_a_rotated_stored_credential_is_used_by_the_next_request(self, upstream, home, bridge_factory):
        """The whole point: rotate the store, the NEXT call carries the new key.

        A bridge that read the credential at start-up would replay the first key
        for the life of the process — which is exactly the defect the harness's
        `${VAR}` expansion has, one layer down.
        """
        url, stub = upstream
        base = url[: -len("/mcp")]
        credential = _write_credential(home, "tt_first", base)
        bridge = bridge_factory()

        bridge.send(_INITIALIZE)
        assert bridge.read()["id"] == 1
        credential.write_text(json.dumps({"api_key": "tt_second", "api_url": base}))
        bridge.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert bridge.read()["id"] == 2

        assert _auth_headers(stub) == ["Bearer tt_first", "Bearer tt_second"]

    def test_the_env_key_wins_over_the_store(self, upstream, home, bridge_factory):
        """Env precedence over the store (#1708 D1).

        Deliberately NOT named "re-read": a bridge is a child process, so its
        environment is a snapshot of the harness's at spawn. What is proven here
        is the ORDER, not liveness — liveness of the store is proven by the
        rotation test above.
        """
        url, stub = upstream
        _write_credential(home, "tt_stored", url[: -len("/mcp")])
        bridge = bridge_factory({"TORTOISE_API_KEY": "tt_env"})

        bridge.send(_INITIALIZE)
        bridge.read()
        bridge.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        bridge.read()

        assert _auth_headers(stub) == ["Bearer tt_env", "Bearer tt_env"]

    def test_the_fleet_alias_is_honoured(self, upstream, home, bridge_factory):
        """`TORTOISE_MCP_API_KEY` (#5200): the sibling empty-bearer path.

        #5200 recorded a key present under the ALIAS while the config expanded
        the canonical name to an empty bearer. The bridge reads both, so the
        same box that produced that 401 authenticates here.
        """
        _, stub = upstream
        bridge = bridge_factory({"TORTOISE_MCP_API_KEY": "tt_alias"})

        bridge.send(_INITIALIZE)
        bridge.read()

        assert _auth_headers(stub) == ["Bearer tt_alias"]

    def test_the_session_id_is_carried_forward(self, upstream, home, bridge_factory):
        """The bridge is a session, not a series of independent POSTs."""
        url, stub = upstream
        _write_credential(home, "tt_one", url[: -len("/mcp")])
        bridge = bridge_factory()

        bridge.send(_INITIALIZE)
        bridge.read()
        bridge.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        bridge.read()

        assert stub.requests[0]["headers"].get("mcp-session-id") is None
        assert stub.requests[1]["headers"].get("mcp-session-id") == "sess-1"
        # The protocol version negotiated by `initialize` travels on later calls.
        assert stub.requests[1]["headers"].get("mcp-protocol-version") == "2025-06-18"


# ── The refusal: never an empty Bearer, always a named cause ────────────────


class TestRefusal:
    def test_a_blank_credential_never_becomes_an_empty_bearer(self, upstream, home, bridge_factory):
        """`"   "` must be treated as ABSENT, not forwarded as `Bearer   `."""
        url, stub = upstream
        _write_credential(home, "   ", url[: -len("/mcp")])
        bridge = bridge_factory()

        bridge.send(_INITIALIZE)
        assert bridge.read()["id"] == 1

        assert _auth_headers(stub) == [None], "a blank credential was sent as a header"

    def test_auth_rejection_with_no_credential_is_the_named_refusal(self, upstream, home, bridge_factory):
        """No credential + 401 ⇒ `MCP-KEY-EMPTY`, naming the sources checked."""
        _, stub = upstream
        stub.refuse = True
        bridge = bridge_factory()

        bridge.send(_INITIALIZE)
        message = bridge.read()

        text = message["error"]["message"]
        assert text.startswith("MCP-KEY-EMPTY"), text
        assert "TORTOISE_API_KEY" in text and "credentials.json" in text
        # The client's own absence is named BEFORE the server's wording, which
        # still travels as evidence — the message is the fix, not a replacement.
        assert "invalid or missing Bearer" in text
        assert _auth_headers(stub) == [None]

    def test_auth_rejection_with_a_credential_names_the_value_fault(self, upstream, home, bridge_factory):
        """A credential WAS sent: the message must not blame a missing key.

        The upstream here ECHOES the rejected header, which is what makes the
        assertion below a property rather than a coincidence: a server that
        quotes the key back must not get it relayed into the harness's error.
        """
        _, stub = upstream
        stub.refuse = True
        stub.echo_credential = True
        bridge = bridge_factory({"TORTOISE_API_KEY": "tt_expired"})

        bridge.send(_INITIALIZE)
        text = bridge.read()["error"]["message"]

        assert not text.startswith("MCP-KEY-EMPTY"), text
        assert "TORTOISE_API_KEY" in text
        assert "tt_expired" not in text, f"the bridge relayed the credential value: {text}"
        assert "<redacted>" in text, f"the echoed evidence was dropped instead of redacted: {text}"

    def test_a_notification_gets_no_response_line(self, upstream, home, bridge_factory):
        """A JSON-RPC notification expects no reply; emitting one breaks the peer."""
        url, stub = upstream
        _write_credential(home, "tt_one", url[: -len("/mcp")])
        bridge = bridge_factory()

        bridge.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        bridge.read_nothing()
        assert len(stub.requests) == 1

    def test_a_transport_failure_is_an_error_not_a_crash(self, home, bridge_factory):
        """An unreachable endpoint answers with a JSON-RPC error, never a hang.

        The key comes from the ENVIRONMENT here so the environment legitimately
        chooses the endpoint (a store-sourced key would ignore it, #2369 D1.1).
        """
        bridge = bridge_factory({
            "TORTOISE_API_KEY": "tt_one",
            "TORTOISE_MCP_URL": "http://127.0.0.1:1/mcp",
        })

        bridge.send(_INITIALIZE)
        message = bridge.read()

        assert message["error"]["code"] == -32603
        assert "MCP transport failure" in message["error"]["message"]


class TestFailureModes:
    def test_a_reply_with_no_matching_id_is_answered_not_silent(self, upstream, home, bridge_factory):
        """A 200 whose stream carries no reply for the id must not become silence.

        Measured shape: an SSE stream that delivers only a notification. Writing
        nothing leaves the harness waiting forever — the failure this guard
        exists for — and re-reading the consumed body would raise
        `httpx.StreamConsumed` and kill the bridge instead.
        """
        url, stub = upstream
        _write_credential(home, "tt_one", url[: -len("/mcp")])
        stub.no_reply = True
        bridge = bridge_factory()

        bridge.send({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
        message = bridge.read()

        assert message["id"] == 7
        assert message["error"]["code"] == -32603
        assert "no JSON-RPC reply" in message["error"]["message"], message
        # The process survived: a second request is still answered.
        stub.no_reply = False
        bridge.send({"jsonrpc": "2.0", "id": 8, "method": "tools/list"})
        assert bridge.read()["id"] == 8

    def test_an_echoed_credential_is_redacted_in_the_no_reply_snippet(
        self, upstream, home, bridge_factory,
    ):
        """A 200 body can quote the credential too, and this path quotes it back.

        Redaction on the refusals alone left this hole: a `200` whose body carried
        the rejected key and no reply for the id put the VALUE into the no-reply
        error the harness logs. Redaction belongs on every server-derived string
        that reaches output, not only on the named refusals.
        """
        url, stub = upstream
        _write_credential(home, "tt_secret", url[: -len("/mcp")])
        stub.echo_in_body = True
        bridge = bridge_factory()

        bridge.send({"jsonrpc": "2.0", "id": 9, "method": "tools/list"})
        message = bridge.read()

        text = message["error"]["message"]
        assert message["id"] == 9 and message["error"]["code"] == -32603, message
        assert "tt_secret" not in text, f"the snippet relayed the credential value: {text}"
        assert "<redacted>" in text, f"the evidence was dropped instead of redacted: {text}"

    def test_redaction_survives_a_TRUNCATED_body(self):
        """Redact FIRST, truncate second — the order is the property.

        Every site used to slice the body and redact the slice, so a credential
        straddling the cut was emitted in part (up to `len(key) - 1` characters):
        `str.replace` cannot match a value that is no longer whole. Measured here
        at each boundary the bridge actually uses (200 / 300 / 400).
        """
        from tortoise.mcp_bridge import _refusal_message
        from tortoise.mcp_client import Connection

        key = "tt_Q7Zq9Xv3Lp0Rt5Wm2Kd8Nb4Yc6Hs1Jf"
        sent = Connection("http://h/mcp", key, "TORTOISE_API_KEY", ("TORTOISE_API_KEY",))
        windows = [key[i:i + 8] for i in range(len(key) - 7)]

        # Straddle every boundary the bridge slices at (400 here, 200/300/400 in
        # `forward`): the cut lands INSIDE the credential for most of these pads.
        for pad in (200, 300, 350, 380, 393, 399, 400, 450):
            text = _refusal_message(401, "x" * pad + key, sent)
            assert not any(w in text for w in windows), (
                f"pad={pad}: a FRAGMENT of the value was emitted: {text[-80:]}")
            if pad <= 380:  # the marker fits inside the 400-char budget
                assert "<redacted>" in text, f"pad={pad}: no evidence left: {text[-80:]}"

    def test_an_expired_session_can_be_re_initialized(self, upstream, home, bridge_factory):
        """The recovery the 404 message promises must actually be possible.

        A dead `Mcp-Session-Id` re-sent on the harness's own `initialize` makes
        every recovery attempt 404 forever, so the id must be dropped when it is
        found to be dead — and never attached to an `initialize` at all.
        """
        url, stub = upstream
        _write_credential(home, "tt_one", url[: -len("/mcp")])
        bridge = bridge_factory()

        bridge.send(_INITIALIZE)
        assert bridge.read()["id"] == 1
        stub.expire = True
        bridge.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        expired = bridge.read()
        assert expired["error"]["code"] == -32002, expired

        stub.expire = False
        bridge.send({**_INITIALIZE, "id": 3})
        assert bridge.read()["id"] == 3
        sessions = [r["headers"].get("mcp-session-id") for r in stub.requests]
        assert sessions == [None, "sess-1", None], sessions

    def test_a_stored_key_ignores_an_env_endpoint(self, home, bridge_factory, upstream):
        """#2369 D1.1 co-source, at the bridge: the config cannot aim the key.

        A harness config is a committable file, so a `TORTOISE_MCP_URL` it carries
        must never be able to redirect a credential taken from the user's store.
        """
        url, stub = upstream
        _write_credential(home, "tt_stored", url[: -len("/mcp")])
        # The environment points at a dead port; the STORE's host must win.
        bridge = bridge_factory({"TORTOISE_MCP_URL": "http://127.0.0.1:1/mcp"})

        bridge.send(_INITIALIZE)
        assert bridge.read()["id"] == 1
        assert _auth_headers(stub) == ["Bearer tt_stored"]


# ── Interop: the real MCP daemon, through the bridge ────────────────────────


def test_against_the_real_mcp_daemon(tmp_path, home, monkeypatch):
    """The bridge speaks to the real `tortoise.mcp_server`, not just the stub.

    Hermetic (embedded FalkorDBLite), so it costs a few seconds and proves the
    handshake/session handling works against the server we actually ship.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "")
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "daemon.db"))
    from contextlib import asynccontextmanager

    from starlette.applications import Starlette
    from starlette.routing import Mount

    from tortoise.mcp_server import create_http_app

    mcp_app = create_http_app(allowed_origins=["http://localhost:8000"], auth_mode="none")

    @asynccontextmanager
    async def _lifespan(parent_app):
        async with mcp_app.lifespan(mcp_app):
            yield

    app = Starlette(lifespan=_lifespan, routes=[Mount("/mcp", app=mcp_app)])
    for url in _serve(app):
        _write_credential(home, "tt_real", url[: -len("/mcp")])
        bridge = _Bridge(url, home)
        try:
            bridge.send(_INITIALIZE)
            assert "serverInfo" in bridge.read()["result"]
            bridge.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            names = [tool["name"] for tool in bridge.read()["result"]["tools"]]
            assert "tortoise_create_point" in names, names[:5]
        finally:
            bridge.close()
