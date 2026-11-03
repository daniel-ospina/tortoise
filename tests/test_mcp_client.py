"""Tortoise MCP client tests (#338 T2.1).

Real HTTP against an in-process uvicorn daemon (auth_mode="none", embedded
FalkorDBLite). Verifies status/available/list_tools/call_tool + graceful
degradation when the daemon is down, and (#10605) that the Bearer credential
is resolved PER REQUEST and that an unresolvable credential is a NAMED refusal
rather than an empty header.
"""
from __future__ import annotations

import json
import os
import threading
import time

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import httpx
import pytest


class _RecordAuthorization:
    """ASGI middleware recording each request's Authorization header.

    The header a long-lived client actually puts ON THE WIRE is the only
    observation that distinguishes "resolved per request" from "expanded once
    at construction" (#10605). Recorded at the SERVER, never by inspecting
    client internals — a client-side spy cannot prove what was transmitted.
    """

    def __init__(self, app, record: list):
        self.app = app
        self.record = record

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            self.record.append(next(
                (v.decode() for k, v in scope.get("headers") or () if k == b"authorization"),
                None,
            ))
        await self.app(scope, receive, send)


def _serve(app) -> str:
    """Run `app` under uvicorn on an ephemeral port; yield its /mcp URL."""
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 20
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "daemon did not start"
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        t.join(timeout=10)


def _mcp_app(record: list | None = None):
    """The MCP daemon app (auth none, embedded DB), optionally recording auth.

    Forces embedded DB env (conftest sets TORTOISE_DB_URI to a test container
    that isn't running — the daemon's SDK would retry-connect and hang).
    """
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
    return _RecordAuthorization(app, record) if record is not None else app


@pytest.fixture
def daemon_url(monkeypatch, tmp_path):
    """Start the MCP daemon (auth none, embedded DB) on an ephemeral port."""
    monkeypatch.setenv("TORTOISE_DB_URI", "")
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "daemon.db"))
    yield from _serve(_mcp_app())


@pytest.fixture
def recorded_daemon(monkeypatch, tmp_path):
    """(url, record) — the MCP daemon plus every Authorization header it saw."""
    monkeypatch.setenv("TORTOISE_DB_URI", "")
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "daemon.db"))
    record: list = []
    for url in _serve(_mcp_app(record)):
        yield url, record


_REFUSAL = {
    "jsonrpc": "2.0",
    "id": None,
    "error": {
        "code": -32001,
        "message": (
            "Unauthorized: invalid or missing Bearer token. Expected format: "
            "Authorization: Bearer tt_<key>/tk_<key> (or an OAuth access token "
            "for #524 OAuth clients)"
        ),
    },
}


async def _refuses_every_request(scope, receive, send):
    """An MCP endpoint that 401s exactly as the hosted one does (auth required)."""
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    body = json.dumps(_REFUSAL).encode()
    await send({"type": "http.response.start", "status": 401, "headers": [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ]})
    await send({"type": "http.response.body", "body": body})


@pytest.fixture
def refusing_daemon(monkeypatch, tmp_path):
    """An MCP endpoint that rejects every request as unauthenticated."""
    monkeypatch.setenv("TORTOISE_DB_URI", "")
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "daemon.db"))
    yield from _serve(_refuses_every_request)


@pytest.fixture(autouse=True)
def _no_ambient_store(monkeypatch, tmp_path):
    """No test in this module may read the REAL `~/.tortoise/credentials.json`.

    `resolve_connection` falls back to that store (#1708 D5), so on a box that has
    run `tortoise init` a stored key wins over the fixture's env AND takes the
    endpoint with it (#2369 D1.1): the suite would pass on a machine with no
    store and quietly call a live host on one that has it. Isolating HOME makes
    the module's result independent of who ran it.
    """
    home = tmp_path / "ambient-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


@pytest.fixture
def isolated_home(monkeypatch, tmp_path):
    """A HOME with no stored credential, and no ambient key in the env."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    monkeypatch.delenv("TORTOISE_MCP_API_KEY", raising=False)
    return home


@pytest.fixture
def client_env(monkeypatch, daemon_url):
    """Point mcp_client at the daemon (no auth)."""
    monkeypatch.setenv("TORTOISE_MCP_URL", daemon_url)
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    monkeypatch.delenv("TORTOISE_MCP_API_KEY", raising=False)


class TestStatus:
    def test_status_ok_with_tools(self, client_env):
        from tortoise.mcp_client import status

        s = status()
        assert s["status"] == "ok"
        assert s["tools"] > 0

    def test_available_true(self, client_env):
        from tortoise.mcp_client import available

        assert available() is True

    def test_status_unavailable_when_down(self, monkeypatch):
        from tortoise.mcp_client import status

        monkeypatch.setenv("TORTOISE_MCP_URL", "http://127.0.0.1:1/mcp")
        monkeypatch.setenv("TORTOISE_API_KEY", "")
        s = status()
        assert s["status"] == "tortoise_unavailable"


class TestTools:
    def test_list_tools_contains_create_point(self, client_env):
        from tortoise.mcp_client import list_tools

        tools = list_tools()
        assert "tortoise_create_point" in tools
        assert "tortoise_query" in tools

    def test_call_create_point(self, client_env):
        from tortoise.mcp_client import call_tool

        result = call_tool("tortoise_create_point", {
            "kind": "statement",
            "content": "mcp-client test point",
            "authoredBy": "test-client",
            "dedup": True,
        })
        # CallToolResult — parse the JSON text and assert a real Point id came
        # back (fail-closed: a rejected engine call also yields is_error=False
        # with an error dict — only a parsed "id" proves the write succeeded).
        import json as _json

        assert result.is_error in (False, None)
        text = "".join(
            getattr(b, "text", "") for b in (result.content or [])
        )
        parsed = _json.loads(text)
        assert isinstance(parsed, dict)
        assert parsed.get("id"), f"expected point id in result, got: {parsed}"


class TestCredentialResolution:
    """#10605: the Bearer credential is resolved PER REQUEST, never once.

    The defect class: a client (or a harness config's ``${VAR}``) that captures
    the credential at construction/start-up keeps sending the captured value —
    empty or stale — for the life of the process. Every assertion here reads
    the header the SERVER received, so "resolved per request" is proven on the
    wire rather than inferred from client internals.
    """

    def test_a_rotated_env_key_reaches_the_wire_on_the_next_request(
        self, recorded_daemon, monkeypatch,
    ):
        """RED before the fix: a live client resent the key captured at build time."""
        import asyncio

        from tortoise import mcp_client

        url, seen = recorded_daemon
        monkeypatch.setenv("TORTOISE_MCP_URL", url)
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_first")
        client = mcp_client.get_client()

        async def _two_round_trips():
            # ONE client across both requests — the long-lived-session shape.
            async with client:
                await client.list_tools()
                monkeypatch.setenv("TORTOISE_API_KEY", "tt_second")
                await client.list_tools()

        asyncio.run(_two_round_trips())

        sent = [h for h in seen if h]
        assert sent, "no Authorization header reached the server at all"
        assert sent[0] == "Bearer tt_first", sent
        assert sent[-1] == "Bearer tt_second", (
            "the rotated key did not reach the wire — the client is still sending "
            f"the value it captured at construction: {sent}"
        )

    def test_a_rotated_stored_credential_reaches_the_wire(
        self, recorded_daemon, isolated_home, monkeypatch,
    ):
        """The other documented store: ~/.tortoise/credentials.json, rewritten live.

        This is the shape the onboarding CLI writes (`_key_resolution_expr`), so
        a CI lane or bridge process that holds one client for hours must pick a
        rotated file up without a restart.
        """
        import asyncio

        from tortoise import mcp_client

        url, seen = recorded_daemon
        cred = isolated_home / ".tortoise" / "credentials.json"
        cred.parent.mkdir()
        # #2369 D1.1 co-source: a STORE-sourced key takes its endpoint from the
        # same file, so the store records the origin the daemon serves (the code
        # appends `/mcp/`, which is the canonical route).
        base = url[: -len("/mcp")]
        cred.write_text(json.dumps({"api_key": "tt_stored_one", "api_url": base}))
        # isolated_home removed the env key, so the FILE is the only source — and
        # the env URL is deliberately NOT how this test points at the daemon.
        client = mcp_client.get_client()

        async def _two_round_trips():
            async with client:
                await client.list_tools()
                cred.write_text(json.dumps({"api_key": "tt_stored_two", "api_url": base}))
                await client.list_tools()

        asyncio.run(_two_round_trips())

        sent = [h for h in seen if h]
        assert sent[0] == "Bearer tt_stored_one", sent
        assert sent[-1] == "Bearer tt_stored_two", (
            "the rotated stored credential did not reach the wire: "
            f"{sent} — the client re-reads the store only at construction"
        )

    def test_a_blank_key_is_never_sent_as_an_empty_bearer(
        self, recorded_daemon, monkeypatch,
    ):
        """A whitespace credential must not become `Bearer   ` on the wire.

        `"   "` is truthy, so the pre-fix client forwarded `Authorization:
        Bearer    ` — an empty bearer the server can only answer with its own
        "invalid or missing Bearer token" message, which misdirects the reader
        onto the server's auth policy instead of the client's empty value.
        """
        from tortoise import mcp_client

        url, seen = recorded_daemon
        monkeypatch.setenv("TORTOISE_MCP_URL", url)
        monkeypatch.setenv("TORTOISE_API_KEY", "   ")

        mcp_client.list_tools()

        assert not any(h and not h.strip().removeprefix("Bearer").strip() for h in seen), (
            f"an EMPTY Bearer reached the server: {seen!r}"
        )

    def test_an_unresolvable_credential_against_an_auth_endpoint_is_a_named_refusal(
        self, refusing_daemon, monkeypatch, isolated_home,
    ):
        """The refusal names the CLIENT cause, not the server's auth policy.

        No credential resolves and the endpoint rejects the call, so the error
        must be the named `MCP-KEY-EMPTY` refusal listing the sources checked —
        not a forwarded "invalid or missing Bearer token", which is the message
        the issue measured as "accurate and useless".
        """
        from tortoise import mcp_client

        monkeypatch.setenv("TORTOISE_MCP_URL", refusing_daemon)

        with pytest.raises(mcp_client.MCPKeyEmptyError) as caught:
            mcp_client.list_tools()

        message = str(caught.value)
        assert caught.value.code == "MCP-KEY-EMPTY"
        assert "TORTOISE_API_KEY" in message
        assert "credentials.json" in message
        # The server's rejection still travels WITH the refusal (evidence, not
        # replacement). fastmcp wraps the body in its own RuntimeError, so the
        # status line — not the body text — is what survives here; the bridge
        # (`tortoise/mcp_bridge.py`), which owns its HTTP calls, carries the body.
        assert "401" in message, message

    def test_status_reports_the_refusal_as_unconfigured(
        self, refusing_daemon, monkeypatch, isolated_home,
    ):
        """`status()` never raises: it reports the named refusal in its payload."""
        from tortoise import mcp_client

        monkeypatch.setenv("TORTOISE_MCP_URL", refusing_daemon)
        payload = mcp_client.status()

        assert payload["status"] == mcp_client.STATUS_UNCONFIGURED, payload
        assert "MCP-KEY-EMPTY" in payload["error"], payload

    def test_a_sent_credential_does_not_become_the_refusal(
        self, refusing_daemon, monkeypatch, isolated_home,
    ):
        """A key WAS sent: the refusal must never claim there was none.

        This is the branch that keeps `MCP-KEY-EMPTY` honest — without it, any
        regression that raised on every auth rejection would still pass the rest
        of this class, and a user with a merely expired key would be told to set
        the variable they already set.
        """
        from tortoise import mcp_client

        monkeypatch.setenv("TORTOISE_MCP_URL", refusing_daemon)
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_expired")

        with pytest.raises(Exception) as caught:
            mcp_client.list_tools()

        assert not isinstance(caught.value, mcp_client.MCPKeyEmptyError), caught.value
        assert "401" in str(caught.value), caught.value
        # …and the probe reports an outage, not a set-up gap.
        payload = mcp_client.status()
        assert payload["status"] == "tortoise_unavailable", payload

    def test_a_corrupt_credential_store_is_named_not_erased(
        self, refusing_daemon, monkeypatch, isolated_home,
    ):
        """A store that exists but will not parse is NOT "no credential".

        #1708 D6 makes a corrupt store loud in the engine path; this reader has
        the same store, so it must name the failure instead of telling the
        operator to set a key that is already on disk.
        """
        from tortoise import mcp_client

        store = isolated_home / ".tortoise" / "credentials.json"
        store.parent.mkdir()
        store.write_text("{ this is not json")
        monkeypatch.setenv("TORTOISE_MCP_URL", refusing_daemon)

        with pytest.raises(mcp_client.MCPKeyEmptyError) as caught:
            mcp_client.list_tools()

        message = str(caught.value)
        assert str(store) in message
        assert "JSONDecodeError" in message, message

    def test_a_stored_key_keeps_its_own_host(self, recorded_daemon, monkeypatch, isolated_home):
        """#2369 D1.1 co-source: an env URL must not redirect a stored key.

        A committed harness config can set `TORTOISE_MCP_URL`; if the stored key
        honoured it, that config could aim the user's credential at any host.
        """
        from tortoise import mcp_client

        url, seen = recorded_daemon
        store = isolated_home / ".tortoise" / "credentials.json"
        store.parent.mkdir()
        base = url[: -len("/mcp")]
        store.write_text(json.dumps({"api_key": "tt_stored", "api_url": base}))
        # A dead endpoint in the environment, which the store-sourced key ignores.
        monkeypatch.setenv("TORTOISE_MCP_URL", "http://127.0.0.1:1/mcp")

        assert "tortoise_create_point" in mcp_client.list_tools()
        sent = [h for h in seen if h]
        assert sent and set(sent) == {"Bearer tt_stored"}, sent

    def test_a_credential_for_another_host_is_never_sent(self, recorded_daemon, isolated_home, monkeypatch):
        """The SOURCE can rotate too: env key → store key on another host.

        The client's endpoint is fixed at construction, so once the credential
        resolves to a different host it is not this request's credential. Sending
        it anyway is the exfiltration shape #2369 D1.1 forbids — measured here at
        the server, which must never see the second key.
        """
        import asyncio

        from tortoise import mcp_client

        url, seen = recorded_daemon
        monkeypatch.setenv("TORTOISE_MCP_URL", url)
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_env")
        client = mcp_client.get_client()
        store = isolated_home / ".tortoise" / "credentials.json"
        store.parent.mkdir()

        async def _rotate_the_source():
            async with client:
                await client.list_tools()
                monkeypatch.delenv("TORTOISE_API_KEY")
                store.write_text(json.dumps(
                    {"api_key": "tt_store", "api_url": "https://other.example"}))
                await client.list_tools()

        asyncio.run(_rotate_the_source())

        sent = [h for h in seen if h]
        assert sent[0] == "Bearer tt_env", sent
        assert "Bearer tt_store" not in sent, (
            f"a credential belonging to another host was sent to this one: {sent}")

    def test_a_credential_for_another_host_fails_by_name(self, isolated_home, monkeypatch):
        """…and the failure names the conflict instead of a generic 401."""
        import httpx

        from tortoise import mcp_client

        store = isolated_home / ".tortoise" / "credentials.json"
        store.parent.mkdir()
        store.write_text(json.dumps(
            {"api_key": "tt_store", "api_url": "https://other.example"}))
        provider = mcp_client._BearerProvider("http://127.0.0.1:9/mcp")
        request = httpx.Request("POST", "http://127.0.0.1:9/mcp")

        flow = provider.auth_flow(request)
        next(flow)
        assert "authorization" not in request.headers, "withheld credential was sent"

        with pytest.raises(mcp_client.MCPCredentialHostMismatchError) as caught:
            mcp_client._classify_failure(RuntimeError("401 Unauthorized"), provider)
        message = str(caught.value)
        assert caught.value.code == "MCP-CREDENTIAL-HOST-MISMATCH"
        assert "other.example" in message and "127.0.0.1:9" in message

    def test_the_same_endpoint_spelled_with_a_trailing_slash_is_not_another_host(
        self, recorded_daemon, isolated_home, monkeypatch,
    ):
        """Co-source equality is about the ENDPOINT, never the spelling.

        `resolve_connection` builds a store endpoint as `api_url + "/mcp/"` while
        an env URL (and the built-in default) carries `/mcp`. Raw string equality
        therefore called ONE endpoint two hosts, withheld the credential, and
        blamed the operator for a conflict that did not exist — silently losing
        the very property #10605 is about.
        """
        import asyncio

        from tortoise import mcp_client

        url, seen = recorded_daemon
        monkeypatch.setenv("TORTOISE_MCP_URL", url)  # this form carries "/mcp"
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_env")
        client = mcp_client.get_client()  # endpoint fixed while the env key is live
        store = isolated_home / ".tortoise" / "credentials.json"
        store.parent.mkdir()

        async def _swap_to_the_store():
            async with client:
                await client.list_tools()
                monkeypatch.delenv("TORTOISE_API_KEY")
                # Same host, base form — resolve_connection appends "/mcp/".
                store.write_text(json.dumps(
                    {"api_key": "tt_stored", "api_url": url[: -len("/mcp")]}))
                await client.list_tools()

        asyncio.run(_swap_to_the_store())

        sent = [h for h in seen if h]
        assert sent[0] == "Bearer tt_env", sent
        assert sent[-1] == "Bearer tt_stored", (
            f"the SAME endpoint's stored credential was withheld: {sent}")

    def test_the_mismatch_message_names_the_value_the_store_must_record(
        self, isolated_home, monkeypatch,
    ):
        """The prescribed remedy must actually work (#10727 review).

        The store builds its endpoint as `api_url + "/mcp/"`, so telling the
        operator to record the ENDPOINT produces `/mcp/mcp/` and breaks it. The
        message has to name the BASE.
        """
        from tortoise import mcp_client

        store = isolated_home / ".tortoise" / "credentials.json"
        store.parent.mkdir()
        store.write_text(json.dumps(
            {"api_key": "tt_store", "api_url": "https://other.example"}))
        provider = mcp_client._BearerProvider("http://127.0.0.1:9/mcp")
        next(provider.auth_flow(httpx.Request("POST", "http://127.0.0.1:9/mcp")))

        with pytest.raises(mcp_client.MCPCredentialHostMismatchError) as caught:
            mcp_client._classify_failure(RuntimeError("401 Unauthorized"), provider)

        message = str(caught.value)
        assert "record http://127.0.0.1:9 as `api_url`" in message, message
        assert "record http://127.0.0.1:9/mcp" not in message, message
        assert mcp_client._store_base("https://h.example/mcp/") == "https://h.example"
        assert mcp_client._store_base("https://h.example/") == "https://h.example"
        assert mcp_client._store_base("https://h.example/mcp") == "https://h.example"

    def test_a_transport_fault_is_not_reported_as_a_credential_mismatch(
        self, isolated_home, monkeypatch,
    ):
        """A down daemon is an OUTAGE even while a credential is withheld.

        Reporting a refused connection as `MCP-CREDENTIAL-HOST-MISMATCH` moved an
        outage into the set-up-gap bucket (#3805) — the same misdirection as the
        server's `invalid or missing Bearer token`, in the other direction.
        """
        from tortoise import mcp_client

        store = isolated_home / ".tortoise" / "credentials.json"
        store.parent.mkdir()
        store.write_text(json.dumps(
            {"api_key": "tt_store", "api_url": "https://other.example"}))
        provider = mcp_client._BearerProvider("http://127.0.0.1:1/mcp")

        async def _probe():
            async with mcp_client._client(provider) as client:
                await client.list_tools()

        with pytest.raises(Exception) as caught:
            mcp_client._run_guarded(_probe(), provider)

        assert provider.withheld is not None, "this test needs the withheld path"
        assert not isinstance(caught.value, mcp_client.MCPCredentialHostMismatchError), (
            "a transport fault was reported as a credential conflict: "
            f"{type(caught.value).__name__}: {caught.value}")

    def test_a_withheld_credential_is_announced_when_the_call_succeeds(
        self, recorded_daemon, isolated_home, monkeypatch, capsys,
    ):
        """Withholding must not be SILENT when the call still succeeds.

        A keyless endpoint accepts the unauthenticated request, so there is no
        failure to classify — but the operator is running on a misconfiguration
        that will bite the moment auth is required. The warning names the sources
        and never the credential VALUE.
        """
        from tortoise import mcp_client

        url, seen = recorded_daemon
        store = isolated_home / ".tortoise" / "credentials.json"
        store.parent.mkdir()
        store.write_text(json.dumps(
            {"api_key": "tt_store", "api_url": "https://other.example"}))
        mcp_client._WARNED_TOPICS.clear()
        provider = mcp_client._BearerProvider(url)

        async def _list():
            async with mcp_client._client(provider) as client:
                return await client.list_tools()

        assert mcp_client._run_guarded(_list(), provider), "the call must succeed"
        assert [h for h in seen if h] == [], "a withheld credential reached the server"

        err = capsys.readouterr().err
        assert "was NOT sent" in err, err
        assert "other.example" in err and url in err, err
        assert "tt_store" not in err, f"the credential VALUE leaked to stderr: {err}"

    def test_a_tool_error_mentioning_401_is_not_a_credential_refusal(self, client_env):
        """A TOOL's 401 is the tool's, not the client's — never rewritten.

        fastmcp raises `ToolError` carrying the tool's own text; a connector that
        reports `401 Unauthorized` for an upstream provider must keep that on the
        keyless self-host path instead of being told its client has no key.
        """
        from fastmcp.exceptions import ToolError

        from tortoise import mcp_client

        sent = mcp_client.resolve_connection()
        assert sent.key is None, "this test needs the keyless path"
        # No raise: the classification must leave a ToolError alone.
        mcp_client._raise_if_credential_was_missing(
            ToolError("upstream provider returned 401 Unauthorized"), sent)

    def test_a_keyless_endpoint_still_needs_no_credential(
        self, recorded_daemon, monkeypatch, isolated_home,
    ):
        """A self-host daemon with auth off is NOT unconfigured — no refusal.

        Pins the boundary the refusal must not cross: absent credential + no
        rejection = a working keyless endpoint, not an error (#1708 / the
        documented self-host path).
        """
        from tortoise import mcp_client

        url, seen = recorded_daemon
        monkeypatch.setenv("TORTOISE_MCP_URL", url)

        assert "tortoise_create_point" in mcp_client.list_tools()
        assert all(h is None for h in seen), seen

