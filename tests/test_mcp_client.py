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
import shutil
import subprocess
import threading
import time
from pathlib import Path

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


def _git_repo_with_store(tmp_path, payload: dict, *, commit: bool = False) -> Path:
    """A real checkout carrying a project-local `./.tortoise` credential store.

    `commit=True` reproduces the #10796 shape exactly: the store ARRIVED with the
    checkout (`git add -f`, or a hostile repository), so it is repo-controlled.
    `commit=False` is what `tortoise init --api-key` leaves behind — the store is
    gitignored, so no repository can have committed it.
    """
    if shutil.which("git") is None:  # pragma: no cover - git is a CI dependency
        pytest.skip("git is not installed, so a TRACKED store cannot be built")
    repo = tmp_path / "checkout"
    repo.mkdir()
    (repo / ".tortoise").write_text(json.dumps(payload))
    _git(repo, "init", "-q")
    if commit:
        _git(repo, "add", "-f", ".tortoise")
    return repo


def _git(repo: Path, *args: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr


def _fake_git(monkeypatch, tmp_path, exit_code: int) -> None:
    """Put a `git` on PATH that exits with `exit_code` and prints nothing.

    Lets a test reach the arms of `_git_tracks` that a real checkout cannot
    produce on demand: the FATAL that means "git could not answer" (an
    unreadable index, dubious ownership on a bind-mounted clone, a corrupt
    `.git`) as distinct from `1`, which means "answered: untracked".
    """
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    script = bindir / "git"
    script.write_text(f"#!/bin/sh\nexit {exit_code}\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")


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


async def _fails_500_echoing_the_key(scope, receive, send):
    """A 500 whose body echoes the Authorization header it received."""
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers") or ()}
    body = json.dumps({
        "jsonrpc": "2.0", "id": None,
        "error": {"code": -32603,
                  "message": f"boom for {headers.get('authorization', '')}"},
    }).encode()
    await send({"type": "http.response.start", "status": 500, "headers": [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ]})
    await send({"type": "http.response.body", "body": body})


@pytest.fixture
def echoing_failure_daemon(monkeypatch, tmp_path):
    """A broken endpoint that quotes the credential it received in its 500."""
    monkeypatch.setenv("TORTOISE_DB_URI", "")
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "daemon.db"))
    yield from _serve(_fails_500_echoing_the_key)


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
    # …and for the PROJECT-LOCAL store `tortoise init --api-key` writes
    # (`./.tortoise`), which is a candidate too (#1708 D5).
    (tmp_path / "cwd").mkdir()
    monkeypatch.chdir(tmp_path / "cwd")


def _redirect_app(target: str):
    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        await send({"type": "http.response.start", "status": 307, "headers": [
            (b"location", target.encode()),
            (b"content-length", b"0"),
        ]})
        await send({"type": "http.response.body", "body": b""})

    return app


@pytest.fixture
def redirecting_daemon(monkeypatch, tmp_path):
    """(first-origin url, second-origin url): the first 307s to the second, which 401s.

    `_serve` is a plain generator whose `finally` shuts the server down, so the
    generator has to be KEPT: a `next()` whose generator is then dropped stops the
    server on garbage collection, and the test measures a refused connection
    instead of a redirect.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "")
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "daemon.db"))
    running = []

    def start(app) -> str:
        generator = _serve(app)
        url = next(generator)
        running.append(generator)
        return url

    try:
        second = start(_refuses_every_request)
        first = start(_redirect_app(second))
        yield first, second
    finally:
        for generator in running:
            generator.close()


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
        # back (fail-closed: only a parsed "id" proves the write succeeded).
        # Since #11153 a REFUSED call no longer reaches here at all: it ships
        # `isError: true`, which the client turns into a `ToolError`. This result is
        # therefore an accepted write, and the payload check below is what proves it.
        import json as _json

        assert result.is_error in (False, None)
        # The PAYLOAD is `content[0]`. Extra blocks are appended LAST by the
        # dispatch seam (#10084's graph disclosure, and `_warn_retired_result`
        # before it), so joining every block and `json.loads`-ing the result is
        # not a contract — it fails with "Extra data" the moment a second block
        # appears.
        text = getattr((result.content or [None])[0], "text", "")
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

    def test_the_fleet_alias_reaches_the_wire(
        self, recorded_daemon, isolated_home, monkeypatch,
    ):
        """#5200/#10605: `TORTOISE_MCP_API_KEY` alone must authenticate.

        The alias leg had NO behavioural test on the client. The only thing
        pinning it was a doc assertion that greps `_ENV_KEY_NAMES` for the name
        — and a name in that tuple is not a name the RESOLUTION reads (a
        refactor that kept the tuple for the refusal message while the loop
        iterated something else stayed green). This reads the header the SERVER
        received, so the leg is proven on the wire: the alias alone, with the
        canonical variable absent, must arrive as a Bearer value.
        """
        from tortoise import mcp_client

        url, seen = recorded_daemon
        monkeypatch.setenv("TORTOISE_MCP_URL", url)
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        monkeypatch.setenv("TORTOISE_MCP_API_KEY", "tt_alias_only")

        mcp_client.list_tools()

        sent = [h for h in seen if h]
        assert sent, (
            "no Authorization header reached the server at all when only the "
            "fleet alias was set — a harness that exports "
            "TORTOISE_MCP_API_KEY sends an empty Bearer (#5153). sources "
            f"the client resolved: {seen!r}"
        )
        assert sent[-1] == "Bearer tt_alias_only", sent

    def test_the_canonical_name_outranks_the_alias(
        self, recorded_daemon, isolated_home, monkeypatch,
    ):
        """The alias is a FALLBACK — the documented precedence must hold.

        Both names set to different values is the only case that distinguishes
        a precedence chain from a last-wins scan, and it is the case a harness
        with a stale alias and a rotated canonical key actually hits: an
        inverted order sends the stale one and reports a 401 against a key the
        operator has already replaced.
        """
        from tortoise import mcp_client

        url, seen = recorded_daemon
        monkeypatch.setenv("TORTOISE_MCP_URL", url)
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_canonical")
        monkeypatch.setenv("TORTOISE_MCP_API_KEY", "tt_alias_only")

        mcp_client.list_tools()

        sent = [h for h in seen if h]
        assert sent and sent[-1] == "Bearer tt_canonical", (
            "the alias outranked the canonical name — precedence is "
            f"TORTOISE_API_KEY first, then the alias: {sent!r}"
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

    def test_an_explicit_default_port_is_the_same_endpoint(self):
        """`http://h:80/mcp` and `http://h/mcp` are ONE endpoint, not two.

        Same false-withhold class as the trailing slash, minus the slash: an
        operator who writes the default port explicitly would otherwise lose the
        credential and be told a conflict exists that does not.
        """
        from tortoise import mcp_client

        assert mcp_client._same_endpoint("http://h/mcp", "http://h:80/mcp")
        assert mcp_client._same_endpoint("https://h/mcp", "https://h:443/mcp/")
        assert mcp_client._same_endpoint("http://H.example/mcp", "http://h.example:80/mcp")
        assert mcp_client._same_endpoint("http://[::1]:80/mcp", "http://[::1]/mcp")
        # …and the property it must never loosen: a real difference still differs.
        assert not mcp_client._same_endpoint("http://h/mcp", "http://h:8080/mcp")
        assert not mcp_client._same_endpoint("http://h/mcp", "https://h/mcp")
        assert not mcp_client._same_endpoint("http://h/mcp", "http://other/mcp")
        assert not mcp_client._same_endpoint("http://h/a", "http://h/b")

    def test_get_client_raises_the_named_refusal(
        self, refusing_daemon, isolated_home, monkeypatch,
    ):
        """`get_client()` is PUBLIC, so the contract must hold there too.

        A consumer builds its own client and drives it in an `async with`; with
        no guard it would get fastmcp's raw transport error and lose the named
        refusal this issue is about. Measured through the public constructor,
        not through the module's own wrappers.
        """
        import asyncio

        from tortoise import mcp_client

        monkeypatch.setenv("TORTOISE_MCP_URL", refusing_daemon)
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        client = mcp_client.get_client()

        async def _call():
            async with client:
                await client.list_tools()

        with pytest.raises(mcp_client.MCPKeyEmptyError) as caught:
            asyncio.run(_call())
        assert caught.value.code == "MCP-KEY-EMPTY"
        assert "credentials.json" in str(caught.value)

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

    def test_a_failed_teardown_is_not_reported_as_success(self):
        """A close failure AFTER a good body must not vanish.

        The guard captured the raw client's `__aexit__` exception and returned
        `False` without re-raising, so `status()` reported `ok` for a probe whose
        teardown failed — a false PASS, and the inverse of what a guard is for.
        """
        import asyncio

        from tortoise import mcp_client

        class _Raw:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                raise RuntimeError("transport close failed: lost socket")

            async def list_tools(self):
                return []

        guard = mcp_client._GuardedClient(_Raw(), mcp_client._BearerProvider("http://h/mcp"))

        async def _run():
            async with guard as client:
                await client.list_tools()

        with pytest.raises(RuntimeError, match="transport close failed"):
            asyncio.run(_run())

    def test_status_never_prints_a_credential_the_server_echoed(
        self, echoing_failure_daemon, isolated_home, monkeypatch,
    ):
        """The CLIENT surface redacts too, not just the bridge.

        A server may echo the key it received, and `status()` returns text the CLI
        prints to stdout — so the value must be redacted there as well. Measured on
        a 500 whose body quotes the Authorization header.
        """
        from tortoise import mcp_client

        monkeypatch.setenv("TORTOISE_MCP_URL", echoing_failure_daemon)
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_echoed_secret_value")

        payload = mcp_client.status()
        text = json.dumps(payload)

        assert payload["status"] == "tortoise_unavailable", payload
        assert "tt_echoed_secret_value" not in text, (
            f"the credential value reached the status payload: {text}")

        # …and the printer used by `status()`/the CLI strips an echoed value from
        # text a library error DOES carry (fastmcp passes JSON-RPC `error.message`
        # through on tool errors, which is exactly where an echo arrives).
        provider = mcp_client._BearerProvider("http://h/mcp")
        provider.sent = mcp_client.Connection(
            "http://h/mcp", "tt_echoed_secret_value", "TORTOISE_API_KEY",
            ("TORTOISE_API_KEY",))
        printed = mcp_client.describe_failure(
            RuntimeError("the key tt_echoed_secret_value is invalid"), provider)
        assert "tt_echoed_secret_value" not in printed, printed
        assert "<redacted>" in printed, printed

    def test_describe_failure_redacts_the_key_that_was_SENT_after_a_rotation(
        self, recorded_daemon, isolated_home, monkeypatch,
    ):
        """The printer must strip the SENT key, not merely the live one.

        `list_tools()`/`call_tool()` build their provider internally, so a CLI
        printing an error has no provider at hand. If the rotation happened
        between the send and the print — this issue's headline scenario — redacting
        only the LIVE resolution leaves the value that was actually on the wire.
        """
        from tortoise import mcp_client

        url, _ = recorded_daemon
        monkeypatch.setenv("TORTOISE_MCP_URL", url)
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_sent_key_0123456789abcdef")
        assert mcp_client.list_tools()
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_new_key_fedcba9876543210")

        text = mcp_client.describe_failure(
            RuntimeError("rejected credential Bearer tt_sent_key_0123456789abcdef"))
        assert "tt_sent_key_0123456789abcdef" not in text, text
        assert "<redacted>" in text, text

    def test_a_connection_never_RENDERS_its_credential(self, monkeypatch):
        """`Connection` renders its coordinates, never its `key`.

        The dataclass's generated `__repr__` included every field, so a single
        `logger.info(connection)`, a `print(conn)` or a traceback frame carrying
        the object put a live Bearer credential into a log line, a transcript or
        a CI artifact — on the per-request path this module exists for.

        The assertion is BOTH ways on purpose: the value must still be an
        attribute (the redactor and the bridge's `_headers` read `connection.key`),
        and must not be rendered — so deleting the field would fail here rather
        than pass vacuously.
        """
        from tortoise import mcp_client

        monkeypatch.setenv("TORTOISE_API_KEY", "tt_repr_canary_0123456789abcdef")
        connection = mcp_client.resolve_connection()

        assert connection.key == "tt_repr_canary_0123456789abcdef", (
            "the credential must stay a readable attribute"
        )
        for rendered in (repr(connection), str(connection), f"{connection}", f"{connection!r}"):
            assert "tt_repr_canary_0123456789abcdef" not in rendered, rendered
        # …while the non-secret coordinates stay rendered, so the repr is still
        # the diagnostic it is used as.
        assert connection.url in repr(connection), repr(connection)
        assert connection.sources[0] in repr(connection), repr(connection)

    def test_the_project_local_store_is_read(
        self, recorded_daemon, isolated_home, monkeypatch, tmp_path,
    ):
        """`tortoise init --api-key` saves `./.tortoise` — the client must read it.

        The hosted onboarding writes the PROJECT-LOCAL store, and the recorded
        chain is env → cwd/.tortoise → ~/.tortoise/credentials.json (#1708 D5).
        Reading only the global store made the client blind to the file the user
        was actually given: no credential, the default endpoint, and a refusal
        naming stores they never wrote.
        """
        from tortoise import mcp_client

        url, seen = recorded_daemon
        work = tmp_path / "project"
        work.mkdir()
        monkeypatch.chdir(work)
        (work / ".tortoise").write_text(json.dumps(
            {"api_key": "tt_project_local", "api_url": url[: -len("/mcp")]}))

        connection = mcp_client.resolve_connection()
        assert connection.key == "tt_project_local", connection
        assert connection.source == str(work / ".tortoise"), connection

        assert mcp_client.list_tools()
        assert set(h for h in seen if h) == {"Bearer tt_project_local"}, seen

    def test_a_git_tracked_project_store_is_not_a_credential_source(
        self, isolated_home, monkeypatch, tmp_path, capsys,
    ):
        """A COMMITTED `./.tortoise` must not outrank the user's global store.

        Both halves of this identity come from ONE file (`resolve_connection`'s
        co-source rule), so a repository that commits `./.tortoise` would choose
        the key AND the endpoint — the repo-controlled shape #3660 fails closed
        on. Precedence itself is NOT reversed (#1708 D5): the store `tortoise
        init --api-key` writes is gitignored, so it is still read first — pinned
        by `test_the_project_local_store_is_read` just above.
        """
        from tortoise import mcp_client

        (isolated_home / ".tortoise").mkdir(parents=True, exist_ok=True)
        (isolated_home / ".tortoise" / "credentials.json").write_text(json.dumps(
            {"api_key": "tt_legit_user_key", "api_url": "https://api.premiselabs.co"}))
        repo = _git_repo_with_store(
            tmp_path,
            {"api_key": "tt_attacker", "api_url": "http://collector.evil:9999"},
            commit=True,
        )
        monkeypatch.chdir(repo)

        connection = mcp_client.resolve_connection()
        assert connection.key == "tt_legit_user_key", connection
        assert connection.url == "https://api.premiselabs.co/mcp/", connection
        assert connection.source == str(
            isolated_home / ".tortoise" / "credentials.json"), connection
        # Named as IGNORED in the refusal's source list — not absent, not corrupt.
        assert any("repo-controlled: ignored" in s for s in connection.sources), connection.sources
        assert "tracked by git" in capsys.readouterr().err

    def test_an_untracked_project_store_still_wins_over_the_global_one(
        self, isolated_home, monkeypatch, tmp_path,
    ):
        """Negative of the guard: the #1708 D5 store is still read FIRST.

        Without this, the test above could be satisfied by deleting the
        project-local candidate outright — which is the D5 reversal #10796
        explicitly rules out.
        """
        from tortoise import mcp_client

        (isolated_home / ".tortoise").mkdir(parents=True, exist_ok=True)
        (isolated_home / ".tortoise" / "credentials.json").write_text(json.dumps(
            {"api_key": "tt_global_key"}))
        # A real repo, same store, but NOT committed — what `tortoise init` leaves.
        repo = _git_repo_with_store(tmp_path, {"api_key": "tt_local_key"})
        monkeypatch.chdir(repo)

        connection = mcp_client.resolve_connection()
        assert connection.key == "tt_local_key", connection
        assert connection.source == str(repo / ".tortoise"), connection

    def test_status_never_raises_when_the_working_directory_is_gone(
        self, isolated_home, monkeypatch, tmp_path,
    ):
        """`status()` promises never to raise; a REAPED worktree must not break it.

        Resolution runs PER REQUEST (#10605), so a `Path.cwd()` that raises does
        not fail one probe — it fails every `status`, `available`, `list_tools`
        and `call_tool`. `available()`'s caller (`integrations/crm/twenty/
        bridge.py`) catches only ImportError/ValueError, so the minutes script
        would CRASH where its own contract says it skips.
        """
        from tortoise import mcp_client

        monkeypatch.setenv("TORTOISE_MCP_URL", "http://127.0.0.1:9/mcp")
        doomed = tmp_path / "reaped-worktree"
        doomed.mkdir()
        monkeypatch.chdir(doomed)
        doomed.rmdir()

        payload = mcp_client.status()  # must NOT raise
        assert payload["status"] == "tortoise_unavailable", payload
        assert payload["url"] == "http://127.0.0.1:9/mcp", payload

    def test_a_repository_git_cannot_read_still_fails_CLOSED(
        self, isolated_home, monkeypatch, tmp_path,
    ):
        """A git FATAL is not an answer — the store is IGNORED, not trusted.

        `git ls-files` returns **128** for "not a git repository" AND for
        "dubious ownership" (a bind-mounted checkout owned by another uid — the
        ordinary container case), an unreadable/corrupt index, and a `.git` git
        will not parse. Reading every non-zero as "not tracked" fails OPEN on
        exactly those checkouts, and a committed store supplies BOTH halves of the
        identity again. Only `1` is an answer.
        """
        from tortoise import mcp_client

        (isolated_home / ".tortoise").mkdir(parents=True, exist_ok=True)
        (isolated_home / ".tortoise" / "credentials.json").write_text(
            json.dumps({"api_key": "tt_legit_user_key"}))
        repo = tmp_path / "unreadable-checkout"
        repo.mkdir()
        (repo / ".tortoise").write_text(json.dumps({"api_key": "tt_attacker"}))
        (repo / ".git").mkdir()  # a repository IS here; git just cannot read it
        monkeypatch.chdir(repo)
        _fake_git(monkeypatch, tmp_path, 128)

        connection = mcp_client.resolve_connection()
        assert connection.key == "tt_legit_user_key", connection
        assert any("repo-controlled: ignored" in s for s in connection.sources), connection.sources

    def test_the_same_fatal_outside_any_repository_still_reads_the_store(
        self, isolated_home, monkeypatch, tmp_path,
    ):
        """Negative: with no `.git` above it, nothing can have committed the file.

        This is the arm that keeps the guard from becoming "never trust a
        project-local store", which would reverse #1708 D5.
        """
        from tortoise import mcp_client

        plain = tmp_path / "tarball"
        plain.mkdir()
        (plain / ".tortoise").write_text(json.dumps({"api_key": "tt_local_key"}))
        monkeypatch.chdir(plain)
        _fake_git(monkeypatch, tmp_path, 128)

        connection = mcp_client.resolve_connection()
        assert connection.key == "tt_local_key", connection

    def test_ambient_GIT_env_cannot_make_a_tracked_store_look_untracked(
        self, isolated_home, monkeypatch, tmp_path,
    ):
        """`GIT_INDEX_FILE`/`GIT_DIR` must not turn the guard into a no-op.

        git honours them, so pointing it at another index makes it answer `1`
        about a file the real index TRACKS — and `1` is a positive "this
        repository does not track it". A committed harness `env` block is exactly
        the channel this module already treats as reachable (#2369 D1.1), so the
        query has to be asked of THIS repository, with a scrubbed environment.
        """
        from tortoise import mcp_client

        (isolated_home / ".tortoise").mkdir(parents=True, exist_ok=True)
        (isolated_home / ".tortoise" / "credentials.json").write_text(
            json.dumps({"api_key": "tt_legit_user_key"}))
        repo = _git_repo_with_store(
            tmp_path, {"api_key": "tt_attacker"}, commit=True,
        )
        # A VALID, empty index in another directory — what GIT_INDEX_FILE is for.
        other = tmp_path / "other-index"
        other.mkdir()
        empty_index = other / "index"
        subprocess.run(
            ["git", "-C", str(repo), "read-tree", "--empty"], check=True,
            capture_output=True, env={**os.environ, "GIT_INDEX_FILE": str(empty_index)},
        )
        monkeypatch.setenv("GIT_INDEX_FILE", str(empty_index))
        monkeypatch.chdir(repo)

        assert mcp_client._git_tracks(repo / ".tortoise") is True
        connection = mcp_client.resolve_connection()
        assert connection.key == "tt_legit_user_key", connection

    def test_the_git_query_is_harnessed_so_it_cannot_touch_the_protocol_pipe(
        self, isolated_home, monkeypatch, tmp_path,
    ):
        """Assert the CALL ITSELF, because `ls-files` cannot be made to misbehave.

        `mcp_bridge.forward` -> `resolve_connection` runs in the process whose
        STDIN IS the JSON-RPC protocol pipe, and whose environment may carry
        `GIT_*` that redirect the repository or the index. Asserting on git's
        output cannot prove either protection: `ls-files` never reads stdin and
        never prompts, so a test that only checked the return value stays green
        with BOTH protections removed. The call's own arguments are the property.
        """
        from tortoise import mcp_client

        repo = _git_repo_with_store(tmp_path, {"api_key": "tt_local"}, commit=True)
        monkeypatch.setenv("GIT_INDEX_FILE", "/tmp/elsewhere/index")
        monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
        seen: dict = {}
        real_run = mcp_client.subprocess.run

        def _spy(argv, **kwargs):
            seen["env"] = dict(kwargs.get("env") or {})
            seen["stdin"] = kwargs.get("stdin")
            return real_run(argv, **kwargs)

        monkeypatch.setattr(mcp_client.subprocess, "run", _spy)
        assert mcp_client._git_tracks(repo / ".tortoise") is True

        assert seen["stdin"] is mcp_client.subprocess.DEVNULL, seen
        assert not [k for k in seen["env"] if k.startswith("GIT_") and k != "GIT_TERMINAL_PROMPT"], (
            f"ambient GIT_* leaked into the query: {sorted(seen['env'])}")
        assert seen["env"].get("GIT_TERMINAL_PROMPT") == "0", seen["env"]

    def test_a_tracked_GLOBAL_store_is_ignored_too(
        self, isolated_home, monkeypatch, tmp_path,
    ):
        """The other candidate: a `$HOME` that is itself a checkout.

        A dotfiles repository can track `~/.tortoise/credentials.json`, and then
        one committed file supplies both halves of the identity on the GLOBAL
        candidate — the same #10796 shape. The store is only vetted when a `.git`
        sits above it, so the ordinary host still pays no `git` spawn per request.
        """
        from tortoise import mcp_client

        home = isolated_home
        (home / ".tortoise").mkdir(parents=True, exist_ok=True)
        store = home / ".tortoise" / "credentials.json"
        store.write_text(json.dumps({
            "api_key": "tt_attacker", "api_url": "http://collector.evil:9999",
        }))
        _git(home, "init", "-q")
        _git(home, "add", "-f", ".tortoise/credentials.json")

        connection = mcp_client.resolve_connection()
        assert connection.key is None, connection
        assert connection.url == mcp_client._DEFAULT_URL, connection
        assert any("repo-controlled: ignored" in s for s in connection.sources), connection.sources

    def test_an_ordinary_global_store_is_never_probed_with_git(
        self, isolated_home, monkeypatch,
    ):
        """Negative: no `.git` above HOME means no spawn, and the key is read.

        This is what keeps the per-request path cheap, and what stops a `$HOME`
        that merely SITS inside a work tree from losing a credential no
        repository had a reason to control.
        """
        from tortoise import mcp_client

        store = isolated_home / ".tortoise" / "credentials.json"
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps({"api_key": "tt_global_key"}))

        def _boom(*_a, **_k):
            raise AssertionError("git was spawned for an ordinary global store")

        monkeypatch.setattr(mcp_client.subprocess, "run", _boom)
        assert mcp_client.resolve_connection().key == "tt_global_key"

    def test_a_deeply_nested_store_is_NAMED_not_raised(
        self, isolated_home, monkeypatch, tmp_path,
    ):
        """`json.loads` raises RecursionError — a RuntimeError, not a ValueError.

        A store is attacker-influenced input, so its parse failure must land in
        the #1708 D6 "named, not erased" arm like any other: escaping here broke
        `status()`'s never-raise contract AND killed the bridge with a traceback
        before it could answer its frame — the failure its fault handler exists
        to prevent.
        """
        from tortoise import mcp_client

        monkeypatch.setenv("TORTOISE_MCP_URL", "http://127.0.0.1:9/mcp")
        store = isolated_home / ".tortoise" / "credentials.json"
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text("[" * 200_000 + "]" * 200_000)

        connection = mcp_client.resolve_connection()  # must NOT raise
        assert connection.key is None, connection
        assert any("RecursionError" in s for s in connection.sources), connection.sources
        payload = mcp_client.status()  # must NOT raise either
        assert payload["status"] == "tortoise_unavailable", payload

    def test_the_git_work_tree_probe_fails_CLOSED_when_it_cannot_look(
        self, tmp_path,
    ):
        """`exists()` re-raises EACCES, so the probe must answer "maybe".

        A directory that cannot be searched might hide a `.git`; answering "no"
        would let an unreadable checkout pass the guard. Fail closed, then let
        `_git_tracks`' fatal arm decide.
        """
        import os

        from tortoise import mcp_client

        if os.geteuid() == 0:  # pragma: no cover - root ignores the mode
            pytest.skip("root can search a 0000 directory, so the trigger is inert")

        outer = tmp_path / "outer"
        (outer / "inner").mkdir(parents=True)
        outer.chmod(0o000)
        try:
            assert mcp_client._in_a_git_work_tree(outer / "inner" / "deeper") is True
        finally:
            outer.chmod(0o755)

    def test_status_never_raises_when_the_store_cannot_be_STATTED(
        self, isolated_home, monkeypatch,
    ):
        """An unsearchable store directory raises PermissionError from `is_file()`.

        `Path.is_file()`/`exists()` only swallow ENOENT/ENOTDIR/ELOOP/EINVAL, so
        EACCES — a container whose `HOME` the running uid cannot traverse
        (`docker --user 1000` against `HOME=/root`), or a `chmod 000` store dir —
        re-raises it out of `resolve_connection`. That is the ordinary container
        shape, not a contrived one, and it lands on EVERY request.
        """
        import os

        from tortoise import mcp_client

        if os.geteuid() == 0:  # pragma: no cover - root ignores the mode
            pytest.skip("root can read a 0000 directory, so the trigger is inert")

        monkeypatch.setenv("TORTOISE_MCP_URL", "http://127.0.0.1:9/mcp")
        store = isolated_home / ".tortoise"
        store.mkdir(parents=True, exist_ok=True)
        (store / "credentials.json").write_text(json.dumps({"api_key": "tt_k"}))
        store.chmod(0o000)
        try:
            connection = mcp_client.resolve_connection()  # must NOT raise
            assert connection.key is None, connection
            payload = mcp_client.status()  # must NOT raise
            assert payload["status"] == "tortoise_unavailable", payload
        finally:
            store.chmod(0o755)

    def test_a_closed_stderr_never_writes_a_diagnostic_to_stdout(
        self, isolated_home, monkeypatch, capsys,
    ):
        """`sys.stderr is None` makes `print(file=None)` fall back to STDOUT.

        In the bridge stdout IS the JSON-RPC pipe, so a warning written there is
        a protocol error that kills the session — the exact failure the module
        exists to prevent. Both writers must drop the line instead.
        """
        from tortoise import mcp_bridge, mcp_client

        monkeypatch.setattr(mcp_client.sys, "stderr", None)
        mcp_client._WARNED_TOPICS.clear()
        mcp_client._warn("a track-looking warning")
        mcp_client._warn_once("unit-test-none-stderr", "a withheld credential")
        mcp_bridge._log("forwarding stdio")

        captured = capsys.readouterr()
        assert captured.out == "", (
            f"a diagnostic reached stdout, i.e. the JSON-RPC pipe: {captured.out!r}")

    def test_warn_once_cannot_raise_after_a_successful_run(
        self, monkeypatch,
    ):
        """The third stderr writer: it runs AFTER the result is in hand.

        `_announce_withheld` is called on the success path, so an unguarded write
        there turns a completed request into a `BrokenPipeError`; `status()`'s
        broad `except` would then report a probe that SUCCEEDED as
        `tortoise_unavailable`.
        """
        import io

        from tortoise import mcp_client

        class _Raising(io.StringIO):
            def write(self, _data):
                raise BrokenPipeError(32, "Broken pipe")

        mcp_client._WARNED_TOPICS.clear()
        monkeypatch.setattr(mcp_client.sys, "stderr", _Raising())
        mcp_client._warn_once("unit-test-topic", "a withheld credential")

    def test_status_never_raises_when_stderr_itself_faults(
        self, isolated_home, monkeypatch, tmp_path,
    ):
        """A broken diagnostics stream must not become a crash.

        `status()` documents "never raises", and its callers skip cleanly on that
        basis; `2>&1 | head -1` (BrokenPipe), `2>/dev/full` (ENOSPC) and a closed
        stderr all reach the warning prints, which run from
        `resolve_connection()` — per request (#10605).
        """
        import io

        from tortoise import mcp_client

        monkeypatch.setenv("TORTOISE_MCP_URL", "http://127.0.0.1:9/mcp")
        store = isolated_home / ".tortoise"
        store.mkdir(parents=True, exist_ok=True)
        # 0644 -> the group/other-readable warning fires, i.e. it writes to stderr.
        target = store / "credentials.json"
        target.write_text(json.dumps({"api_key": "tt_store_key"}))
        target.chmod(0o644)
        mcp_client._WARNED_READABLE_STORES.clear()

        class _Raising(io.StringIO):
            def write(self, _data):
                raise BrokenPipeError(32, "Broken pipe")

        monkeypatch.setattr(mcp_client.sys, "stderr", _Raising())
        payload = mcp_client.status()  # must NOT raise
        assert payload["status"] == "tortoise_unavailable", payload

    def test_endpoint_declared_describes_the_endpoint_the_connection_USES(
        self, isolated_home, monkeypatch,
    ):
        """The PRODUCER half of the `degraded` vs `unconfigured` split.

        `client/tortoise_client/cli.py` stubs this in its probe tests, so without
        this pin reverting the producer (`declared=` on the three returns, or
        `endpoint_declared` itself) reddens nothing — the wiring would be pinned
        and the value not.
        """
        from tortoise import mcp_client

        monkeypatch.delenv("TORTOISE_MCP_URL", raising=False)
        assert mcp_client.endpoint_declared() is False  # nothing declared anywhere

        monkeypatch.setenv("TORTOISE_MCP_URL", "http://127.0.0.1:9/mcp")
        assert mcp_client.endpoint_declared() is True  # the env declares one
        monkeypatch.delenv("TORTOISE_MCP_URL", raising=False)

        store = isolated_home / ".tortoise"
        store.mkdir(parents=True, exist_ok=True)
        (store / "credentials.json").write_text(json.dumps(
            {"api_key": "tt_store_key", "api_url": "http://127.0.0.1:9"}))
        assert mcp_client.endpoint_declared() is True  # a store declares one

        (store / "credentials.json").write_text(json.dumps({"api_key": "tt_store_key"}))
        assert mcp_client.endpoint_declared() is False  # a key is not an endpoint

    def test_a_directory_named_tortoise_is_not_a_corrupt_store(
        self, isolated_home, monkeypatch, tmp_path,
    ):
        """A repo's own `./.tortoise` STATE DIR is "no config here", not corrupt.

        Several repos keep `.tortoise` as a directory of state files; reading it
        would raise `IsADirectoryError` and be reported as an unreadable store,
        which is the misdirection this module exists to remove.
        """
        from tortoise import mcp_client

        work = tmp_path / "repo"
        (work / ".tortoise").mkdir(parents=True)
        monkeypatch.chdir(work)

        connection = mcp_client.resolve_connection()
        assert connection.key is None, connection
        assert not any("IsADirectoryError" in s for s in connection.sources), connection.sources

    def test_a_cross_origin_redirect_names_the_credential_it_did_not_carry(
        self, redirecting_daemon, isolated_home, monkeypatch,
    ):
        """A credential that RESOLVED but was never sent is its own state.

        httpx strips `Authorization` across an origin change and fastmcp follows
        redirects, so a 401 from the redirect target answers a request that had
        none. Classifying from the resolution alone called that an OUTAGE
        (`tortoise_unavailable`, exit 3) and named a host the operator never
        configured — while the key was never on the wire at all.
        """
        from tortoise import mcp_client

        first, _second = redirecting_daemon
        monkeypatch.setenv("TORTOISE_MCP_URL", first)
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_live_key")

        payload = mcp_client.status()
        text = json.dumps(payload)

        assert payload["status"] == "unconfigured", payload
        assert "MCP-CREDENTIAL-NOT-CARRIED" in payload["error"], payload
        assert "rebuild the client" in payload["error"], (
            f"the remedy cannot work for a client whose endpoint is fixed at "
            f"construction: {payload['error']}")
        assert "tt_live_key" not in text, f"the credential value was printed: {text}"

    def test_a_live_key_stops_the_empty_refusal_from_claiming_none_resolved(
        self, monkeypatch, isolated_home,
    ):
        """`MCP-KEY-EMPTY` may not be raised when a credential DOES resolve.

        The classification falls back to a LIVE resolution when the failed request
        never attached a credential (`provider.sent is None` — a connect
        failure). It then fell straight through to `_refusal`, whose text is
        hard-coded to "NO credential resolved on the client (checked …)": with a
        key set, that sentence is false AND it names the very source that is set —
        the misdirection class this module exists to remove (#10727 review, P3).

        The honest classification is "the failed request carried none, and WHY is
        not knowable", so nothing is translated and the server's message stands.
        """
        from tortoise import mcp_client

        monkeypatch.setenv("TORTOISE_MCP_URL", "http://127.0.0.1:9/mcp")
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_live_key")
        provider = mcp_client._BearerProvider(mcp_client.resolve_connection().url)
        assert provider.sent is None, "this test needs the no-request-evidence path"

        # No raise: `MCP-KEY-EMPTY` would be a false statement about the state.
        mcp_client._classify_failure(RuntimeError("401 Unauthorized"), provider)

    def test_a_live_key_absent_still_gets_the_named_empty_refusal(
        self, monkeypatch, isolated_home,
    ):
        """…while the source it names is honoured when NOTHING resolves.

        The other half of the same branch: with no key anywhere, the refusal is
        the accurate word, and the message must keep naming every source read.
        """
        from tortoise import mcp_client

        monkeypatch.setenv("TORTOISE_MCP_URL", "http://127.0.0.1:9/mcp")
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        monkeypatch.delenv("TORTOISE_MCP_API_KEY", raising=False)
        provider = mcp_client._BearerProvider(mcp_client.resolve_connection().url)

        with pytest.raises(mcp_client.MCPKeyEmptyError) as caught:
            mcp_client._classify_failure(RuntimeError("401 Unauthorized"), provider)

        assert caught.value.code == "MCP-KEY-EMPTY"
        assert "NO credential resolved" in str(caught.value)

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

    def test_a_status_code_inside_a_URL_is_not_an_auth_rejection(self):
        """`/401/` in an endpoint's PATH is not a 401 from the server.

        httpx renders a non-auth failure as `Client error '404 Not Found' for url
        'http://h:9/401/mcp'`, and the `\b40[13]\b` arm matched the PATH SEGMENT —
        so a request that got a 404 from a wrong URL (or a daemon named in a
        proxy's own message) was reported as the credential-less refusal: "add a
        key" for a request that never reached a credential decision. That is the
        #3805 inversion in the other direction — an outage reported as a set-up
        gap — so it must stay an outage.

        Measured: this exact text RED on the pre-fix classifier
        (`MCPKeyEmptyError` raised) and green after the URL strip. The positive
        control is in the same test on purpose — the strip must not silence a
        REAL rejection, whose code sits in the status phrase, not in the URL.
        """
        import httpx

        from tortoise import mcp_client

        def status_error(code: int, path: str) -> httpx.HTTPStatusError:
            # The message is httpx's OWN (`raise_for_status()`), not a hand-built
            # string: the defect this pins lives in the text the library produces,
            # so the test must fail if that text stops carrying the URL — or if it
            # starts carrying something a hand-written fixture would miss.
            request = httpx.Request("POST", f"http://127.0.0.1:9{path}")
            response = httpx.Response(code, request=request)
            with pytest.raises(httpx.HTTPStatusError) as caught:
                response.raise_for_status()
            return caught.value

        wrong_path = status_error(404, "/401/mcp")
        # The trap has to be PRESENT in the text, or this test is vacuous.
        assert "401" in str(wrong_path), str(wrong_path)
        # An EXPLICIT keyless connection, so the assertion cannot depend on what
        # this box happens to have in `./.tortoise` or the environment: `sent=None`
        # would re-resolve live and take the "a key DOES resolve" branch instead.
        keyless = mcp_client.Connection(
            url="http://127.0.0.1:9/401/mcp", key=None, source=None, sources=(),
        )
        # No raise: a 404 is not a credential decision.
        mcp_client._raise_if_credential_was_missing(wrong_path, keyless)

        real_rejection = status_error(401, "/mcp")
        with pytest.raises(mcp_client.MCPKeyEmptyError):
            mcp_client._raise_if_credential_was_missing(real_rejection, keyless)

    def test_an_sdk_that_refuses_the_redirect_gets_the_same_named_refusal(
        self, monkeypatch, isolated_home,
    ):
        """Both SDK behaviours reach ONE refusal, or a version bump breaks it.

        Measured on this PR's own CI (#10727): the lane's `.venv` holds mcp 1.29,
        which FOLLOWS the cross-origin hop and lands on a 401 with a stripped
        header, while CI installs mcp 1.30, which REFUSES the hop outright with
        `Redirect to <url> not followed…`. Same operator problem, arrived at by
        two routes — and classifying only the first turned the set-up gap back
        into an outage (`tortoise_unavailable`, exit 3) on the SDK CI actually
        runs. Driven from the exception TEXT on purpose: the branch must hold on
        the version this box happens NOT to have.
        """
        from tortoise import mcp_client

        monkeypatch.setenv("TORTOISE_MCP_URL", "http://127.0.0.1:9/mcp")
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_live_key")
        provider = mcp_client._BearerProvider(mcp_client.resolve_connection().url)
        provider.sent = mcp_client.resolve_connection()
        refusal = mcp_client.MCPCredentialNotCarriedError(
            "Redirect to http://127.0.0.1:5/mcp not followed; use that URL as the "
            "endpoint if it is the intended server")

        with pytest.raises(mcp_client.MCPCredentialNotCarriedError) as raised:
            mcp_client._classify_failure(refusal, provider)

        text = str(raised.value)
        assert "MCP-CREDENTIAL-NOT-CARRIED" in text, text
        assert "http://127.0.0.1:5/mcp" in text, (
            f"the refusal must name the endpoint to aim at: {text}")
        assert "rebuild the client" in text, text
        assert "tt_live_key" not in text, f"the credential value was printed: {text}"
        # The declined hop DID carry the credential to the first endpoint; saying
        # "never sent" is a falsehood the operator can check.
        assert "never sent" not in text, text
        assert "http://127.0.0.1:9/mcp" in text, text

    def test_a_redirect_downgrade_refusal_is_not_a_credential_refusal(
        self, monkeypatch, isolated_home,
    ):
        """The SDK's OTHER `not followed` refusal must not be swallowed by this one.

        mcp 1.30 emits two refusals from one function: the cross-origin one this
        branch exists for, and `not followed: it would downgrade this HTTPS
        endpoint to plain HTTP`, whose own remedy is the OPPOSITE — use https, or
        fix the proxy settings. A prefix-only match classified the downgrade as a
        credential/endpoint gap and told the operator to aim the client at the
        `http` URL, i.e. to send the bearer over cleartext.
        """
        from tortoise import mcp_client

        monkeypatch.setenv("TORTOISE_MCP_URL", "https://mcp.example.com/mcp")
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_live_key")
        provider = mcp_client._BearerProvider(mcp_client.resolve_connection().url)
        provider.sent = mcp_client.resolve_connection()
        downgrade = RuntimeError(
            "Redirect to http://mcp.example.com/mcp/ not followed: it would "
            "downgrade this HTTPS endpoint to plain HTTP.\nTry "
            "https://mcp.example.com/mcp/ instead, or fix the proxy settings.")

        # No raise: this is a proxy/scheme fault, and the SDK's own message is the
        # honest one. The credential classification must stay out of it.
        mcp_client._classify_failure(downgrade, provider)

    def test_a_tool_error_quoting_the_redirect_refusal_is_not_a_credential_refusal(
        self, monkeypatch, isolated_home,
    ):
        """A TOOL that relays a nested SDK refusal is the tool's failure.

        The other two classifiers exempt `ToolError` so a tool's own text can
        mention 401/Unauthorized without being rewritten. The redirect branch must
        hold that line too: a Tortoise tool proxying another MCP server can quote
        the refusal verbatim, and rewriting it into `unconfigured` moves a failing
        tool into the credential bucket (the #3805 inversion).
        """
        from fastmcp.exceptions import ToolError

        from tortoise import mcp_client

        monkeypatch.setenv("TORTOISE_MCP_URL", "http://127.0.0.1:9/mcp")
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_live_key")
        provider = mcp_client._BearerProvider(mcp_client.resolve_connection().url)
        provider.sent = mcp_client.resolve_connection()
        relayed = ToolError(
            "fetch failed: Redirect to https://other.example/x not followed; use "
            "that URL as the endpoint if it is the intended server")

        mcp_client._classify_failure(relayed, provider)

    def test_a_refused_hop_with_no_credential_names_the_absence_not_a_value(
        self, monkeypatch, isolated_home,
    ):
        """No credential resolved = no "resolved from <nothing>" in the message.

        The refusal text names the source a credential came from, so on the keyless
        path it has to say the absence out loud rather than interpolate `None`.
        """
        from tortoise import mcp_client

        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        monkeypatch.delenv("TORTOISE_MCP_API_KEY", raising=False)
        monkeypatch.setenv("TORTOISE_MCP_URL", "https://mcp.example.com/mcp")
        provider = mcp_client._BearerProvider(mcp_client.resolve_connection().url)
        provider.sent = mcp_client.resolve_connection()
        assert provider.sent.key is None, "this test needs the keyless path"
        refusal = RuntimeError(
            "Redirect to https://other.example/mcp not followed; use that URL as "
            "the endpoint if it is the intended server")

        with pytest.raises(mcp_client.MCPCredentialNotCarriedError) as raised:
            mcp_client._classify_failure(refusal, provider)

        text = str(raised.value)
        assert "resolved from None" not in text, text
        assert "No credential resolved either" in text, text
        assert "https://other.example/mcp" in text, text

    def test_a_withheld_credential_explains_a_declined_redirect_too(
        self, monkeypatch, isolated_home,
    ):
        """Co-source conflict first: a withheld value must not be credited with a hop.

        The declined-redirect branch runs before the auth check, so without an
        explicit withheld case here a co-source conflict reports as "your endpoint
        redirects" — and the text credits the WITHHELD value with reaching a host
        it was deliberately never sent to, the same class of falsehood the
        "never sent" wording was.
        """
        from tortoise import mcp_client

        monkeypatch.setenv("TORTOISE_API_KEY", "tt_live_key")
        provider = mcp_client._BearerProvider("https://client.example/mcp")
        withheld = mcp_client.Connection(
            url="https://other.example/mcp", key="tt_live_key",
            source="~/.tortoise/credentials.json", sources=("store",),
        )
        provider.sent = withheld
        provider.withheld = withheld
        declined = RuntimeError(
            "Redirect to https://third.example/mcp not followed; use that URL as "
            "the endpoint if it is the intended server")

        with pytest.raises(mcp_client.MCPCredentialHostMismatchError) as raised:
            mcp_client._classify_failure(declined, provider)

        text = str(raised.value)
        assert "MCP-CREDENTIAL-HOST-MISMATCH" in text, text
        assert "https://other.example/mcp" in text, text
        assert "https://client.example/mcp" in text, text
        assert "tt_live_key" not in text, f"the credential value was printed: {text}"

    def test_a_declined_hop_names_the_client_endpoint_not_the_credential_origin(
        self, monkeypatch, isolated_home,
    ):
        """The destination is where the request WENT, not the credential's source.

        `sent` is the credential's PROVENANCE; under co-source the two can be
        different hosts. Naming `sent.url` as the destination credits the value
        with a delivery it never made — the same falsehood as "never sent".
        """
        from tortoise import mcp_client

        monkeypatch.setenv("TORTOISE_API_KEY", "tt_live_key")
        sent = mcp_client.Connection(
            url="https://elsewhere.example/mcp", key="tt_live_key",
            source="TORTOISE_API_KEY", sources=("TORTOISE_API_KEY",),
        )

        text = str(mcp_client._not_carried(
            RuntimeError("boom"), sent, "https://target.example/mcp",
            hop_refused=True, request_url="https://client.example/mcp",
        ))

        assert "https://client.example/mcp" in text, text
        assert "https://elsewhere.example/mcp" not in text, (
            f"the credential's origin was named as the request destination: {text}")

    def test_a_redirect_target_that_echoes_the_credential_is_redacted(
        self, monkeypatch, isolated_home,
    ):
        """A `Location` is SERVER text: quoting it must not quote the value.

        The refusal quotes the redirect target, and a target is whatever the server
        sent — including a URL that carries the credential. Every other quoted
        server string in this module is redacted; the target is one of them.
        """
        from tortoise import mcp_client

        monkeypatch.setenv("TORTOISE_API_KEY", "tt_live_key")
        sent = mcp_client.Connection(
            url="https://client.example/mcp", key="tt_live_key",
            source="TORTOISE_API_KEY", sources=("TORTOISE_API_KEY",),
        )

        text = str(mcp_client._not_carried(
            RuntimeError("boom"), sent,
            "https://target.example/mcp?key=tt_live_key",
            hop_refused=True, request_url="https://client.example/mcp",
        ))

        assert "tt_live_key" not in text, f"the credential value was printed: {text}"
        assert "target.example" in text, f"the target must still be named: {text}"

    def test_a_redirect_target_echoing_a_value_the_bare_request_never_sent(
        self, monkeypatch, isolated_home,
    ):
        """The target is redacted with every known value, not just `sent.key`.

        A `Location` can echo a value this process learned AFTER the request went
        out bare (a rotated-in key, a store the request never read). `sent.key` is
        None on that path, so redacting with it alone printed the value verbatim —
        the same hole as the keyless `_refusal` echo.
        """
        from tortoise import mcp_client

        monkeypatch.setenv("TORTOISE_API_KEY", "tt_known_after_the_fact")
        bare = mcp_client.Connection(
            url="https://client.example/mcp", key=None, source=None, sources=("none",),
        )

        text = str(mcp_client._not_carried(
            RuntimeError("boom"), bare,
            "https://target.example/mcp?key=tt_known_after_the_fact",
            hop_refused=True, request_url="https://client.example/mcp",
        ))

        assert "tt_known_after_the_fact" not in text, (
            f"a value this process knows was printed: {text}")
        assert "target.example" in text, text

    def test_a_declined_hop_through_the_classifier_names_the_client_endpoint(
        self, monkeypatch, isolated_home,
    ):
        """The CALL SITE must pass the client's endpoint, not the credential's.

        `_not_carried` naming the right URL is only half the contract: the remedy
        text names where the request went, so a call site that forwards `sent.url`
        credits a credential with reaching its OWN origin host while the client is
        wired elsewhere — a false delivery claim, and one no helper-level test can
        see. Driven through `_classify_failure`, with `sent.url` deliberately
        different from the client endpoint.
        """
        from tortoise import mcp_client

        monkeypatch.setenv("TORTOISE_API_KEY", "tt_live_key")
        provider = mcp_client._BearerProvider("https://client.example/mcp")
        provider.sent = mcp_client.Connection(
            url="https://elsewhere.example/mcp", key="tt_live_key",
            source="TORTOISE_API_KEY", sources=("TORTOISE_API_KEY",),
        )
        declined = RuntimeError(
            "Redirect to https://target.example/mcp not followed; use that URL as "
            "the endpoint if it is the intended server")

        with pytest.raises(mcp_client.MCPCredentialNotCarriedError) as raised:
            mcp_client._classify_failure(declined, provider)

        text = str(raised.value)
        assert "the request went to https://client.example/mcp" in text, (
            f"the destination must be the client's endpoint: {text}")
        assert "reached https://client.example/mcp" in text, text
        assert "https://elsewhere.example/mcp" not in text, text

    def test_a_keyless_refusal_still_redacts_a_value_this_process_knows(
        self, monkeypatch, isolated_home,
    ):
        """A bare request can still be answered with a leaked-value echo.

        The keyless refusal quotes SERVER text, and the server echoes what it saw —
        which a request that went out bare can still carry if the value came from
        somewhere else in this process (a previous request, the store, the
        environment). Redacting only the credential of the FAILED request left such
        an echo printed verbatim.
        """
        from tortoise import mcp_client

        monkeypatch.setenv("TORTOISE_API_KEY", "tt_live_key")
        bare = mcp_client.Connection(
            url="https://mcp.example.com/mcp", key=None, source=None, sources=("none",),
        )

        text = str(mcp_client._refusal(
            RuntimeError("401 Unauthorized: key tt_live_key is invalid"), bare))

        assert "tt_live_key" not in text, f"the credential value was printed: {text}"
        assert "<redacted>" in text, text
        assert "no session restart" in text, "the remediation text must survive"

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

