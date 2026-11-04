"""The stdio ↔ Streamable-HTTP bridge (#10605).

The bridge is the durable half of #10605. A harness config that carries
`Authorization: Bearer ${VAR}` is a CAPTURE in two ways, and it is worth being
precise because the difference decides the fix: the `${VAR}` EXPANSION re-runs at
every connect (measured on the reporting harness, #10605), but it expands a
`process.env` frozen when the harness started — and an UNSET variable expands
silently to `""`, so a credential exported under a name the client never reads
sends an empty Bearer on every connect. That name is the reported defect, and a
name-level fix is what the bridge gives: a local stdio command that resolves the
credential from its SOURCES on EVERY forwarded request, so a rotation or a
late-added key takes effect without a restart.

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


def test_no_artifact_restates_the_refuted_capture_mechanism():
    """#10605 review (F4): the STATED mechanism must be the MEASURED one.

    The PR first said `${TORTOISE_API_KEY}` "is expanded once, when the harness
    process starts, and the resulting string is replayed for the life of that
    process". The tracker refuted that twice from the harness source (2026-11-02,
    2026-11-03) and once on the wire (3/3 `initialize` POSTs): `expandExpr()`
    RE-RUNS at every connect. What is frozen is `process.env` — captured at
    process start — plus the per-connection resolution; an unset `${VAR}` expands
    silently to `""`, which is why the NAME, not a cache, was the defect.

    The three artifacts that carried the false version are the module docstring,
    this file's docstring, and `docs/client-server-split.md` §8a. A wrong
    mechanism is not cosmetic: it points the next reader at cache expiry (a
    re-expansion fix) instead of at the config NAME, which is the fix that
    actually landed — every one of the four is pinned here so the plausible-but-
    wrong sentence cannot come back in a later tidy-up.

    `client/README.md` is the FOURTH, added after review caught it: the row it
    carries ships inside the wheel as the `tortoise-client` description, so it is
    the one place a `pip install`-only user reads the mechanism, and it still said
    the header "is expanded once at process start" while this PR's own §8a
    measured the opposite. A guard that pins three artifacts and misses the one
    that ships to users pins the copies, not the claim.
    """
    import tortoise.mcp_bridge as bridge_module

    artifacts = {
        "tortoise/mcp_bridge.py": bridge_module.__doc__ or "",
        "tests/test_mcp_bridge.py": __doc__ or "",
        "client/README.md": (REPO_ROOT / "client" / "README.md").read_text(
            encoding="utf-8"
        ),
        "docs/client-server-split.md": (
            REPO_ROOT / "docs" / "client-server-split.md"
        ).read_text(encoding="utf-8"),
    }
    refuted = (
        "expanded once, when the harness",
        "expanded once at process start",
        "replayed for the life of the process",
        "replays an empty-or-stale header forever",
    )
    for name, text in artifacts.items():
        for phrase in refuted:
            assert phrase not in text, (
                f"{name} restates the REFUTED capture mechanism ({phrase!r}) — "
                "the expansion re-runs at every connect; the frozen thing is "
                "process.env and the per-connection resolution (#10605)")
    for name in ("tortoise/mcp_bridge.py", "client/README.md",
                 "docs/client-server-split.md"):
        assert "re-runs at every connect" in artifacts[name].lower(), (
            f"{name} must state the MEASURED mechanism (the expansion re-runs at "
            "every connect) rather than leaving the reader to infer it")
        assert "process.env" in artifacts[name], (
            f"{name} must name what is actually frozen: process.env")


def test_the_timeout_knob_is_declared_and_matches_the_code_default():
    """`TORTOISE_MCP_BRIDGE_TIMEOUT` is read by the bridge and declared NOWHERE else.

    #10727 review (residual): the knob was read at `main()` and named only inside a
    comment in this module — not in `.env.example`, which this repo treats as its
    env CONTRACT (`AGENTS.md`), and not in §8a, the one recipe a harness operator
    follows. So the operator who hits a legitimately slow call had a knob they
    could not find, and the only documented reading of a timeout was "outage".

    The value is pinned too, not just the name: a declared default that disagrees
    with `_DEFAULT_TIMEOUT` is the same drift one layer down, and it is the copy an
    operator will trust.
    """
    import re

    import tortoise.mcp_bridge as bridge_module

    env_text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "TORTOISE_MCP_BRIDGE_TIMEOUT" in env_text, (
        ".env.example is this repo's env contract and does not declare the "
        "bridge's only knob")
    declared = re.search(r"^#\s*TORTOISE_MCP_BRIDGE_TIMEOUT=(\S+)\s*$", env_text, re.M)
    assert declared, (
        ".env.example must show the knob's real default on its declaration line")
    assert float(declared.group(1)) == bridge_module._DEFAULT_TIMEOUT, (
        f".env.example declares {declared.group(1)}, the code defaults to "
        f"{bridge_module._DEFAULT_TIMEOUT}")

    split_doc = (REPO_ROOT / "docs" / "client-server-split.md").read_text(
        encoding="utf-8")
    assert "TORTOISE_MCP_BRIDGE_TIMEOUT" in split_doc, (
        "§8a is the operator's recipe for the bridge; it must name the knob that "
        "answers a slow call")


# ── A spec-shaped Streamable-HTTP upstream that records what it receives ──────


def test_the_hosted_recipe_residual_has_a_home_this_fold_does_not_close():
    """#10790 review (P2): the residual's declared home must OUTLIVE the fold.

    `docs/client-server-split.md` §8a records the half of #10605 that the bridge
    does NOT retire: the onboarding generator (`_harness_mcp_config`) and the
    committed `.mcp.json` still emit the `Authorization: Bearer ${TORTOISE_API_KEY}`
    capture for the hosted shape. It used to name #10605 as that record — and
    #10605 is CLOSED, so the residual was reachable only by someone who already
    knew to read a closed issue's comments plus a code docstring. Its open home is
    now #10885, filed with the residual's own `file:line` scope.

    The assertion is a BICONDITIONAL on purpose, so the guard does not rot into a
    tautology in either direction:

    * the capture is still emitted and the doc has lost its open home -> RED
      (the residual survives with no reachable record: the defect this pins);
    * the capture is RETIRED and the doc still declares it -> RED (a stale claim
      sending the next reader to a closed issue). Retiring the capture means
      deleting the residual paragraph, and this test says so when it happens.
    """
    main_py = (REPO_ROOT / "tortoise" / "__main__.py").read_text(encoding="utf-8")
    doc = (REPO_ROOT / "docs" / "client-server-split.md").read_text(encoding="utf-8")

    # The residual is REAL while the onboarding generator still hands a user a
    # config whose credential is captured at process start.
    emitted = '"auth_header": "Bearer ${TORTOISE_API_KEY}"' in main_py

    # Read the residual PARAGRAPH, not the whole file: a passing mention of the
    # number anywhere else would let the declaration itself lose its home.
    marker = "What this does NOT do"
    assert marker in doc, (
        "docs/client-server-split.md §8a no longer declares the hosted-recipe "
        "residual (#10605 / #10885) — if the capture was retired, delete this "
        "test with the paragraph rather than leaving it dangling")
    start = doc.index(marker)
    end = doc.find("\n\n", start)
    paragraph = doc[start:] if end == -1 else doc[start:end]
    declared = "#10885" in paragraph

    assert emitted == declared, (
        "the residual and its declared home disagree (#10605 is CLOSED; its open "
        f"home is #10885): capture_still_emitted={emitted}, "
        f"doc_names_the_open_home={declared}. If emitted, add #10885 to the §8a "
        "paragraph; if retired, DELETE the paragraph instead of re-pointing it.")

    # Naming #10605 as the ORIGIN is fine; the sentence that says where the
    # residual is TRACKED must name an issue that is still OPEN.
    assert "tracked OPEN on #10885" in paragraph, (
        "the §8a residual paragraph must say where the residual is TRACKED "
        "(#10885, OPEN), not only where it was recorded — #10605 is CLOSED")

    # ⛔ THE RELAY NAMES WHICH PIN HOLDS WHICH HALF (#10790 review, P2).
    #
    # §8a relays TWO residuals — the hosted-recipe capture (#10885) and the
    # `503 consent_store_unavailable` arm + keyless-re-entry hole (#10884) — and the
    # paragraph used to say "Both sentences are held in place by a biconditional
    # pin (`test_the_hosted_recipe_residual_...`)". It does not: `paragraph` above
    # stops at the first blank line, and the #10884 sentence lives in the PARAGRAPH
    # AFTER it, so deleting `#10884` from the page left the suite green — a false
    # coverage claim, which is worse than an admitted gap because it stops the next
    # reader from checking. The #10884 half is really held by the two pins in
    # `tests/test_oauth_consent_bff_routes.py` (they read `start.ts` and
    # `tortoise/oauth.py`, which is where those residuals live). Both halves are
    # checked here so the credited guards must exist, not merely be named.
    assert "Both sentences are held" not in doc, (
        "§8a still claims a single biconditional pin covers BOTH relayed residuals — "
        "the pin it names reads only the `What this does NOT do` paragraph, which "
        "cannot see the #10884 sentence in the next one")
    relay_start = doc.index("#10605 is CLOSED")
    relay_end = doc.find("\n\nBoth modules are staged", relay_start)
    relay = doc[relay_start:] if relay_end == -1 else doc[relay_start:relay_end]
    bff_routes = (REPO_ROOT / "tests" / "test_oauth_consent_bff_routes.py").read_text(
        encoding="utf-8")
    holders = (
        "test_the_store_fault_residual_names_a_home_this_fold_does_not_close",
        "test_the_whitespace_attempt_key_residual_names_a_home_this_fold_does_not_close",
    )
    for holder in holders:
        assert holder in relay, (
            f"§8a relays the #10884 residual but does not name the guard that holds "
            f"it ({holder}) — a coverage claim has to say WHICH pin covers which "
            "sentence, or the half nobody reads rots silently")
        assert f"def {holder}(" in bff_routes, (
            f"§8a credits `{holder}` for the #10884 half, but no such test exists in "
            "tests/test_oauth_consent_bff_routes.py — a named guard that does not "
            "exist is a false coverage claim, not a pointer")
    assert "#10884" in relay, (
        "§8a's relay no longer names #10884 for the store-fault / keyless-re-entry "
        "residual, yet both code pins still declare that open home")


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
        #: Answer the request's OWN id with a body that echoes the credential — the
        #: reply the bridge relays to stdout VERBATIM, i.e. the one that reaches
        #: the harness's transcript.
        self.echo_answered = False
        #: Answer the request as a JSON-RPC BATCH (a list) over SSE — the shape the
        #: module docstring promises to emit element-by-element.
        self.batch_reply = False
        #: 307 every request to this URL — a CROSS-ORIGIN hop, which is what makes
        #: httpx drop the Authorization header (#10727 review, P2).
        self.redirect_to: str | None = None
        #: The status the redirect answers with: 307/308 PRESERVE the request (and
        #: so may be followed), 301/302/303 do not.
        self.redirect_status = 307
        #: Redirect only the FIRST request, so a FOLLOWED hop can be observed on
        #: the second (a persistent redirect would loop).
        self.redirect_once = False

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
        self.requests.append({
            "path": scope.get("path"),
            "headers": headers,
            "payload": payload,
            "body": body,
        })

        if self.redirect_to and not (self.redirect_once and len(self.requests) > 1):
            await _respond(send, self.redirect_status, None,
                           extra_headers=[(b"location", self.redirect_to.encode())])
            return

        if self.batch_reply:
            reply = {
                "jsonrpc": "2.0",
                "id": payload.get("id") if isinstance(payload, dict) else None,
                "result": {"tools": [{"name": "tortoise_search"}]},
            }
            await _respond(send, 200, None, content_type="text/event-stream", raw=(
                b"data: " + json.dumps([reply]).encode() + b"\n\n"
            ))
            return

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

        if self.echo_answered:
            await _respond(send, 200, {
                "jsonrpc": "2.0",
                "id": payload.get("id") if isinstance(payload, dict) else None,
                "error": {"code": -32001, "message": (
                    f"rejected credential {headers.get('authorization', '')}")},
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


@pytest.fixture(autouse=True)
def _no_ambient_store(tmp_path, monkeypatch):
    """No test in this module may read the CHECKOUT's own `./.tortoise`.

    `factory` isolates the SUBPROCESS's cwd, but `forward()` is also driven
    IN-PROCESS by several tests, and those call `resolve_connection()` in the
    pytest process — whose cwd is the repository. The project-local store is read
    first (#1708 D5) and the module under test is gitignored there by design
    (`.gitignore`), so on a box that has run the documented
    `tortoise init --api-key` the ambient store supplies the key and the endpoint
    (#2369 D1.1) and every in-process assertion measures the wrong credential:
    the redaction pins then go RED with no leak, on exactly the machines that hold
    a real key. The sibling `tests/test_mcp_client.py` isolates for the same
    reason; this is the same mechanism, for the half that runs in-process.
    """
    cwd = tmp_path / "pytest-cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    return cwd


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
    def __init__(self, url: str, home: Path, cwd: Path,
                 extra_env: dict[str, str] | None = None):
        env = {k: v for k, v in os.environ.items()
               if k not in ("TORTOISE_API_KEY", "TORTOISE_MCP_API_KEY")}
        env.update({"HOME": str(home), "TORTOISE_MCP_URL": url})
        # The repo on `sys.path` so `-m tortoise.mcp_bridge` resolves from the
        # tmp cwd below (the child's PROJECT-LOCAL store leg is `cwd/.tortoise`).
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            f"{REPO_ROOT}{os.pathsep}{existing}" if existing else str(REPO_ROOT))
        env.update(extra_env or {})
        self.stderr_lines: list[str] = []
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "tortoise.mcp_bridge"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, env=env, cwd=str(cwd),
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

    @staticmethod
    def await_requests(stub, count: int, timeout: float = 15.0) -> None:
        """Block until the stub has received `count` requests (bounded).

        #10727 review nit: asserting `len(stub.requests) == count` immediately
        after a fixed silence window measured the RUNNER's scheduling, not the
        bridge — under load the forward had not landed yet and the test redded as
        if the bridge had not forwarded at all. The silence proof and the
        forward-landed proof are different claims, so they get different
        determinism: silence is a bounded window (the peer emits nothing, so
        there is nothing to wait FOR), while a forward is an EVENT that will
        arrive, so it is polled to a deadline. This can only turn a correct
        bridge green; it cannot mask an extra reply line, which `read_nothing`
        already reds on.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(stub.requests) >= count:
                return
            time.sleep(0.02)
        raise AssertionError(
            f"the bridge forwarded {len(stub.requests)} request(s), expected "
            f"{count} within {timeout}s")

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=15)
        finally:
            if self.proc.poll() is None:  # pragma: no cover - defensive
                self.proc.kill()


@pytest.fixture
def bridge_factory(upstream, home, tmp_path):
    bridges: list[_Bridge] = []

    def _make(extra_env: dict[str, str] | None = None,
              cwd: Path | None = None) -> _Bridge:
        # ⛔ THE CHILD'S cwd IS A TMP DIRECTORY, never REPO_ROOT. The module under
        # test reads the PROJECT-LOCAL store first (`Path.cwd()/.tortoise`, #1708
        # D5), so spawning in the checkout let a developer's `tortoise init
        # --api-key` store — the documented hosted flow, gitignored so its
        # presence is normal — outrank every fixture: the per-test key was
        # ignored, `TORTOISE_MCP_URL` was ignored too once the store supplied the
        # credential (co-source, #2369 D1.1), and the suite sent the host's live
        # key to the live hosted endpoint (#10727 review, P2).
        if cwd is None:
            cwd = tmp_path / f"bridge-cwd-{len(bridges)}"
            cwd.mkdir()
        bridge = _Bridge(upstream[0], home, cwd, extra_env)
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


def test_the_child_resolves_the_project_local_store_from_its_OWN_cwd(
    upstream, home, bridge_factory, tmp_path,
):
    """The child's PROJECT-LOCAL store must be the test's, never the checkout's.

    `resolve_connection` reads `Path.cwd()/.tortoise` FIRST (#1708 D5), so a bridge
    spawned with `cwd=REPO_ROOT` let a developer's `tortoise init --api-key` store
    — the documented hosted flow, gitignored so its presence is normal — outrank
    every fixture: the per-test key was ignored, `TORTOISE_MCP_URL` was ignored
    once the store supplied the credential (co-source, #2369 D1.1), and the suite
    sent the host's live key to the live hosted endpoint. Each bridge now spawns
    in its own tmp cwd, and this pin reds if that ever regresses: on a worktree
    with no `REPO_ROOT/.tortoise` the child would resolve NOTHING (no header at
    all), and on a checkout that has one it would resolve the HOST's key.
    """
    url, stub = upstream
    project = tmp_path / "child-cwd"
    project.mkdir()
    (project / ".tortoise").write_text(
        json.dumps({"api_key": "tt_from_child_cwd", "api_url": url[: -len("/mcp")]}))
    # `home` carries no store: the project-local leg is the only source here.
    bridge = bridge_factory(cwd=project)

    bridge.send(_INITIALIZE)
    assert bridge.read()["id"] == 1
    assert _auth_headers(stub) == ["Bearer tt_from_child_cwd"], (
        "the child did not resolve the store in ITS OWN cwd — it is reading the "
        f"checkout's `./.tortoise` instead ({_auth_headers(stub)})")


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
        bridge.await_requests(stub, 1)
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
    def test_a_non_UTF8_stdout_still_gets_the_frame_it_asked_for(self):
        """The protocol writer must not depend on the child's stdout encoding.

        `ensure_ascii=False` raises `UnicodeEncodeError` when stdout is ASCII (a
        container locale with no `C.UTF-8`, `PYTHONIOENCODING`, Windows ANSI) and
        the reply carries any non-ASCII character — ordinary Tortoise content.
        `main()`'s emit loop is outside every `try`, so the raise escapes and the
        bridge writes NOTHING for that frame: the harness waits forever.
        """
        import io

        from tortoise import mcp_bridge

        raw = io.BytesIO()
        ascii_out = io.TextIOWrapper(raw, encoding="ascii", write_through=True)
        original = mcp_bridge.sys.stdout
        mcp_bridge.sys.stdout = ascii_out
        try:
            mcp_bridge._emit({
                "jsonrpc": "2.0", "id": 1,
                "result": {"serverInfo": {"name": "Peña Blanca"}},
            })
            ascii_out.flush()
        finally:
            mcp_bridge.sys.stdout = original

        line = raw.getvalue().decode("ascii")
        assert json.loads(line)["result"]["serverInfo"]["name"] == "Peña Blanca"
        assert "\\u00f1" in line, (
            "the frame must be ASCII-escaped on the wire, not written as raw UTF-8")

    def test_a_faulted_stderr_cannot_kill_the_frame_it_is_logging_about(self, monkeypatch):
        """`_log` is called FROM `main()`'s fault handler, so it must not fault.

        ENOSPC under `2>/dev/full`, or a collector pipe that goes away
        mid-session, makes the write raise. If `_log` propagates, `main()`'s
        `except Exception` re-enters it, the second raise escapes `main()`, and
        the next request id in the batch gets NO reply — the hang the handler
        exists to prevent.
        """
        import io

        from tortoise import mcp_bridge

        class _Raising(io.StringIO):
            def write(self, _data):
                raise OSError(28, "No space left on device")

        monkeypatch.setattr(mcp_bridge.sys, "stderr", _Raising())
        mcp_bridge._log("internal failure forwarding a frame")  # must NOT raise

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

    def test_a_relayed_reply_never_carries_the_credential(self, upstream, home, bridge_factory):
        """The reply that ANSWERS the id is written to stdout verbatim.

        That is the one path that reaches the harness's transcript, and it is the
        one path redaction had not reached: a server that quotes the rejected key
        inside `error.message` handed the VALUE to the harness — the exact
        client the bridge exists to keep the key away from.
        """
        url, stub = upstream
        _write_credential(home, "tt_relayed_secret", url[: -len("/mcp")])
        stub.echo_answered = True
        bridge = bridge_factory()

        bridge.send(_INITIALIZE)
        message = bridge.read()

        text = json.dumps(message)
        assert "tt_relayed_secret" not in text, f"the relayed reply carried the value: {text}"
        assert "<redacted>" in text, f"the evidence was dropped instead of redacted: {text}"

    def test_an_internal_failure_never_prints_the_credential(
        self, monkeypatch, home, capsys,
    ):
        """An exception can carry the header verbatim (UnicodeEncodeError does).

        The generic failure handler emitted `{exc!r}` to BOTH stderr and a
        `-32603` on stdout, so a non-ASCII key exposed the whole credential to a
        bridge that had merely failed to send it. Measured here by injecting a
        fault whose text contains the key.
        """
        import io

        from tortoise import mcp_bridge

        _write_credential(home, "tt_internal_secret", "http://127.0.0.1:9")
        monkeypatch.setenv("TORTOISE_MCP_URL", "http://127.0.0.1:9/mcp")
        monkeypatch.setattr(
            mcp_bridge, "forward",
            lambda *_a, **_k: (_ for _ in ()).throw(
                UnicodeEncodeError("ascii", "Bearer tt_internal_secret", 7, 9,
                                   "ordinal not in range(128)")))

        class _Out(io.StringIO):
            pass

        out = _Out()
        monkeypatch.setattr(sys, "stdin", io.StringIO('{"jsonrpc":"2.0","id":1,"method":"initialize"}\n'))
        monkeypatch.setattr(sys, "stdout", out)
        assert mcp_bridge.main([]) == 0

        emitted = out.getvalue()
        assert "tt_internal_secret" not in emitted, f"the value reached stdout: {emitted}"
        assert "<redacted>" in emitted, f"no redaction marker: {emitted}"
        assert "tt_internal_secret" not in capsys.readouterr().err

        # The LINE the handler quotes is a SECOND place the value can straddle a
        # cut: it was sliced to 120 chars BEFORE redaction, so a credential that
        # crossed that boundary came out in part. The short line above cannot see
        # it — the key is not in that line at all — so the boundary needs its own
        # line, with the key placed to straddle char 120.
        key = "tt" + "_internal_secret"

        def _line(pad: int) -> str:
            return json.dumps({
                "jsonrpc": "2.0", "id": 2, "method": "initialize",
                "params": {"pad": "x" * pad, key: "y" * 50},
            }) + "\n"

        # COMPUTE the pad: the key's column moves with the serialised wrapper, so a
        # hand-picked pad silently stops straddling the boundary when the payload
        # shape changes and the test passes for the wrong reason.
        # Land the key so the cut leaves >= 8 of its characters: a shorter remnant
        # is below the fragment window and the test would pass on a leak it cannot
        # see (the earlier fixture left 5 and did exactly that).
        want = 120 - 12
        pad = want - _line(0).index(key)
        assert pad > 0, f"the payload already exceeds char {want} (pad={pad})"
        long_line = _line(pad)
        start = long_line.index(key)
        assert start < 120 and 120 - start >= 8, (
            f"the cut must land INSIDE the key and leave >= 8 characters: "
            f"start={start}, remnant={120 - start}")

        # Keep the scenario-1 stdout capture in place: re-monkeypatching
        # `sys.stdout` a second time leaves a closed StringIO behind for the
        # session-scoped redislite-hygiene teardown to write into. This scenario
        # only reads stderr.
        monkeypatch.setattr(sys, "stdin", io.StringIO(long_line))
        capsys.readouterr()  # drop the first scenario's stderr
        assert mcp_bridge.main([]) == 0

        err = capsys.readouterr().err
        # Scope the check to the QUOTED segment: the key ends in "internal", which
        # is also a word in the log prefix ("internal failure forwarding"), so a
        # whole-stream substring test reports a leak that is only the prefix.
        marker = "internal failure forwarding "
        quoted = err.split(marker, 1)[1] if marker in err else ""
        assert quoted, f"the failure log line was not found in: {err[-300:]}"
        fragments = [key[i:i + 8] for i in range(len(key) - 7)]
        assert not any(f in quoted for f in fragments), (
            f"a FRAGMENT of the value reached stderr: {quoted[:300]}")
        assert "<redacted>" in quoted, f"no redaction marker on the quoted line: {quoted[:300]}"

    def test_redaction_survives_a_TRUNCATED_body_on_the_REDIRECT_refusal(self):
        """The redirect refusal is a SECOND builder, added after the repair.

        `_redirect_refusal` was written while every sibling site already redacted
        first and truncated second, and it sliced the body before redacting — the
        one reintroduced truncate-before-redact site. A credential straddling the
        400-char cut therefore emitted `len(key) - 1` characters into the message
        the harness writes to a transcript and a CI lane to a log. Pinned
        separately from the `_refusal_message` case so a future edit to either
        builder cannot quietly leave the other uncovered.
        """
        import httpx as _httpx

        from tortoise.mcp_bridge import _redirect_refusal
        from tortoise.mcp_client import Connection

        key = "tt" + "_boundary_secret_value"
        sent = Connection("http://h/mcp", key, "TORTOISE_API_KEY", ("TORTOISE_API_KEY",))
        windows = [key[i:i + 8] for i in range(len(key) - 7)]
        response = _httpx.Response(
            307, request=_httpx.Request("POST", "http://h/mcp"))

        for pad in (200, 300, 350, 380, 393, 399, 400, 450):
            text = _redirect_refusal(
                response, "x" * pad + key, sent, "http://other-origin/mcp")
            assert not any(w in text for w in windows), (
                f"pad={pad}: a FRAGMENT of the value was emitted: {text[-80:]}")
            if pad <= 380:
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

    def test_a_batched_reply_is_delivered_not_declared_unanswered(
        self, upstream, home, bridge_factory,
    ):
        """A JSON-RPC BATCH is a reply, and the module promises to emit it.

        RED before the fix: `_response_messages` appended the decoded event as-is,
        so a batch closed `pending` (its unwrap loop reads a list) and was then
        rejected by the no-reply guard, which only recognises a dict. The bridge
        answered `-32603 "no JSON-RPC reply for id(s) [7]"` while holding the real
        result, and the harness was told its request went unanswered.
        """
        url, stub = upstream
        _write_credential(home, "tt_one", url[: -len("/mcp")])
        stub.batch_reply = True
        bridge = bridge_factory()

        bridge.send({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
        message = bridge.read()

        assert "error" not in message, (
            "a batch reply was declared unanswered while it was in hand: "
            f"{message}"
        )
        assert message["id"] == 7, message
        assert message["result"]["tools"] == [{"name": "tortoise_search"}], message

    def test_a_cross_origin_redirect_is_REFUSED_before_the_body_is_sent(
        self, upstream, home, bridge_factory,
    ):
        """A CROSS-ORIGIN redirect must not deliver the JSON-RPC body anywhere.

        httpx re-issues a 307/308 with the SAME body and strips only
        `Authorization`, so `follow_redirects=True` delivered the tool arguments
        (and `Mcp-Session-Id` / `MCP-Protocol-Version`) to whatever origin the
        `Location` named, and only then reported a (correct) refusal about a
        credential that never left — the exfiltration shape GHSA-5h93-6whr-6q8j
        was filed for, and the reason the MCP SDKs follow same-origin redirects
        only. The refusal must therefore be raised WITHOUT a second request: the
        foreign origin sees ZERO requests.

        RED before the fix: the target recorded one request carrying the body.
        """
        _url, stub = upstream
        target = _Upstream()
        target.refuse = True
        served = _serve(target)
        target_url = next(served)
        try:
            stub.redirect_to = target_url
            bridge = bridge_factory({"TORTOISE_API_KEY": "tt_secret"})

            bridge.send(_INITIALIZE)
            text = bridge.read()["error"]["message"]

            assert target.requests == [], (
                "the JSON-RPC body was REPLAYED to a foreign origin before the "
                f"refusal was reported ({target.requests})")
            assert _auth_headers(stub) == ["Bearer tt_secret"]
            assert "MCP-CREDENTIAL-NOT-CARRIED" in text, text
            assert "DID NOT follow" in text, text
            assert "DID send a credential" not in text, (
                f"a credential that never left was blamed for the rejection: {text}")
            assert target_url.rstrip("/") in text, text
            assert "different origin" in text.lower(), text
            assert "tt_secret" not in text, f"the credential value was relayed: {text}"
        finally:
            served.close()

    def test_a_same_origin_redirect_is_still_followed(
        self, upstream, home, bridge_factory,
    ):
        """Tightening to same-origin must keep the hop the endpoint NEEDS.

        The server answers `/mcp` with `/mcp/` (#2864), which is a SAME-ORIGIN 307.
        A bridge that refused every redirect would trade the leak for an endpoint
        that cannot be reached at its documented URL, so the followed hop is
        pinned on the wire: two requests, the second at the new path, carrying the
        credential AND the same JSON-RPC body (a 307 preserves the request).
        """
        url, stub = upstream
        assert url.endswith("/mcp"), url
        # The ENV key leg keeps the harness URL (`.../mcp`), which is the endpoint
        # the server answers with a `/mcp/` redirect (#2864). A STORE credential
        # would already resolve to `api_url + "/mcp/"`, so it could not show the hop.
        stub.redirect_to = "/mcp/"
        stub.redirect_once = True
        bridge = bridge_factory({"TORTOISE_API_KEY": "tt_one"})

        bridge.send(_INITIALIZE)
        message = bridge.read()

        assert message.get("id") == 1, message
        assert [r["path"] for r in stub.requests] == ["/mcp", "/mcp/"], (
            f"the same-origin hop was not followed: {[r['path'] for r in stub.requests]}")
        assert _auth_headers(stub) == ["Bearer tt_one", "Bearer tt_one"]
        assert stub.requests[1]["body"] == stub.requests[0]["body"]

    def test_a_method_changing_redirect_is_not_followed(
        self, upstream, home, bridge_factory,
    ):
        """301/302/303 do not preserve a JSON-RPC POST, so they are refused.

        Only 307/308 mean "the same request, at another URL". Re-sending the body
        on a `302 Found` would follow a policy the endpoint did not state (and
        turning the POST into a GET cannot carry a request at all), so the hop is
        answered by name and nothing is re-sent.
        """
        url, stub = upstream
        assert url.endswith("/mcp"), url
        stub.redirect_to = "/mcp/"
        stub.redirect_status = 302
        bridge = bridge_factory({"TORTOISE_API_KEY": "tt_one"})

        bridge.send(_INITIALIZE)
        text = bridge.read()["error"]["message"]

        assert len(stub.requests) == 1, (
            f"a method-changing redirect was followed: {[r['path'] for r in stub.requests]}")
        assert "307/308" in text, text
        assert "Nothing was re-sent" in text, text

    def test_the_redirect_remedy_is_actionable_for_a_STORED_credential(
        self, upstream, home, bridge_factory,
    ):
        """The advice must be one that can work for the source that produced it.

        A FILE-sourced credential decides its own endpoint (co-source, #2369
        D1.1), so "point TORTOISE_MCP_URL at the target" changes nothing: the
        store's `api_url` wins, the operator repeats the failure, and the message
        is accurate and useless — the #10605 defect in the branch added for it.
        """
        url, stub = upstream
        _write_credential(home, "tt_stored_redirect", url[: -len("/mcp")])
        target = _Upstream()
        target.refuse = True
        served = _serve(target)
        target_url = next(served)
        try:
            stub.redirect_to = target_url
            bridge = bridge_factory()

            bridge.send(_INITIALIZE)
            text = bridge.read()["error"]["message"]

            assert "MCP-CREDENTIAL-NOT-CARRIED" in text, text
            assert target.requests == [], (
                "the stored credential's request was replayed to the redirect "
                f"target ({target.requests})")
            assert "api_url" in text and "credentials.json" in text, (
                f"a stored credential was told to use an env URL it ignores: {text}")
            assert "point TORTOISE_MCP_URL" not in text, text
        finally:
            served.close()


    def test_a_partially_answered_batch_reports_the_MISSING_ids(self):
        """One answered id is not a complete answer to a batch.

        The guard used `not any(id in expected_ids)`, so a batch of two where the
        server answered id 1 and never answered id 2 passed the guard and wrote
        NOTHING for id 2 — the harness then waits on that id forever, which is the
        silence the guard exists to prevent.

        Driven through `forward` with a stubbed transport rather than through the
        bridge subprocess: a PARTIAL answer is by definition one the stream does
        not end on, so the end-to-end shape waits for the read timeout and the
        test would measure the clock instead of the guard. Nothing about the
        decision is mocked — the real guard, `_response_messages` and
        `_errors_for_ids` all run.
        """
        import httpx

        from tortoise.mcp_bridge import forward

        body = json.dumps({"jsonrpc": "2.0", "id": 11,
                           "result": {"tools": [{"name": "tortoise_search"}]}})
        response = httpx.Response(
            200, content=body.encode(),
            headers={"content-type": "application/json"},
        )

        class _Client:
            def stream(self, *_a, **_k):
                class _Ctx:
                    def __enter__(self_inner):
                        return response

                    def __exit__(self_inner, *_exc):
                        return False

                return _Ctx()

        messages = forward(_Client(), {}, [
            {"jsonrpc": "2.0", "id": 11, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 12, "method": "tools/call"},
        ])

        ids = [m["id"] for m in messages]
        assert ids == [11, 12], f"expected the answer AND an error, got ids {ids}"
        assert messages[0]["result"]["tools"] == [{"name": "tortoise_search"}], messages[0]
        assert messages[1]["error"]["code"] == -32603, messages[1]
        named = messages[1]["error"]["message"]
        # Compare the id LIST, not a bare substring: the message also quotes the
        # server body as a snippet, and that body legitimately contains `"id": 11`.
        assert "id(s) [12]" in named, named
        assert "id(s) [11" not in named, (
            f"an answered id was reported as unanswered: {named}")


    def test_a_batch_reply_with_an_unhashable_id_does_not_break_the_guard(self):
        """A JSON-RPC `id` may be an array or an object, and those are unhashable.

        Keying the answered-set on such an id raises `TypeError`, which `main()`
        turns into a fabricated "internal failure" for EVERY id in the batch —
        discarding an answer the server did give. The guard follows the same
        `(str, int)` filter `_response_messages` already uses for `pending`.
        """
        import httpx

        from tortoise.mcp_bridge import forward

        body = json.dumps([
            {"jsonrpc": "2.0", "id": ["not", "hashable"], "result": {}},
            {"jsonrpc": "2.0", "id": 11, "result": {"tools": [{"name": "tortoise_search"}]}},
        ])
        response = httpx.Response(
            200, content=body.encode(),
            headers={"content-type": "application/json"},
        )

        class _Client:
            def stream(self, *_a, **_k):
                class _Ctx:
                    def __enter__(self_inner):
                        return response

                    def __exit__(self_inner, *_exc):
                        return False

                return _Ctx()

        messages = forward(_Client(), {}, [
            {"jsonrpc": "2.0", "id": 11, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 12, "method": "tools/call"},
        ])

        ids = [m.get("id") for m in messages if isinstance(m, dict)]
        assert 11 in ids, f"the answered id was discarded: {messages}"
        assert 12 in ids, f"the unanswered id got no error: {messages}"
        answered = next(m for m in messages if isinstance(m, dict) and m.get("id") == 11)
        assert answered.get("result") == {"tools": [{"name": "tortoise_search"}]}, answered

    def test_an_unhashable_REQUEST_id_is_answered_or_refused_never_silent(self):
        """The silence guarantee must survive an id a set cannot key.

        `id` may be an array or an object, and filtering those out of the
        comparison entirely left the request with NO reply and NO error — the one
        outcome `forward()` promises never to produce ("a bridge that writes
        nothing leaves the harness waiting forever").
        """
        import httpx

        from tortoise.mcp_bridge import forward

        def _forward(body: bytes, payload):
            response = httpx.Response(
                200, content=body, headers={"content-type": "application/json"})

            class _Client:
                def stream(self, *_a, **_k):
                    class _Ctx:
                        def __enter__(self_inner):
                            return response

                        def __exit__(self_inner, *_exc):
                            return False

                    return _Ctx()

            return forward(_Client(), {}, payload)

        request = [{"jsonrpc": "2.0", "id": [1, 2], "method": "tools/call"}]

        # (a) The server answered NOTHING: the request must still be answered.
        silent = _forward(json.dumps([]).encode(), request)
        assert [m.get("id") for m in silent] == [[1, 2]], (
            f"a request with an unhashable id was left unanswered: {silent}")
        assert silent[0]["error"]["code"] == -32603, silent

        # (b) The server DID answer it: relay the result, fabricate nothing.
        answered = _forward(
            json.dumps([{"jsonrpc": "2.0", "id": [1, 2], "result": {"ok": True}}]).encode(),
            request,
        )
        assert len(answered) == 1 and answered[0].get("result") == {"ok": True}, answered

    def test_a_partially_answered_batch_still_captures_the_protocol_version(self):
        """The early return must not drop the tail's `initialize` side effect.

        `forward()` records the negotiated protocol version from any relayed
        `initialize` result. The partial-batch guard returns EARLY, so without an
        explicit capture a batched `initialize` whose sibling id went unanswered
        would negotiate a version and then never send `MCP-Protocol-Version` again
        for the life of the session.
        """
        import httpx

        from tortoise.mcp_bridge import forward

        body = json.dumps([{"jsonrpc": "2.0", "id": 1, "result": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "serverInfo": {"name": "stub", "version": "0"}}}])
        response = httpx.Response(
            200, content=body.encode(),
            headers={"content-type": "application/json"},
        )

        class _Client:
            def stream(self, *_a, **_k):
                class _Ctx:
                    def __enter__(self_inner):
                        return response

                    def __exit__(self_inner, *_exc):
                        return False

                return _Ctx()

        state: dict = {"session_id": None, "protocol_version": None}
        messages = forward(_Client(), state, [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ])

        assert state["protocol_version"] == "2025-06-18", (
            "the negotiated protocol version was dropped on the partial-batch path: "
            f"state={state}")
        # ...and the silence for id 2 is still answered.
        assert [m["id"] for m in messages] == [1, 2], messages

    def test_a_resolution_failure_on_the_FAULT_path_still_answers_the_frame(
        self, monkeypatch, home, capsys,
    ):
        """The fault handler must not be able to fault (#10605 review).

        RED before this fix, measured: the `except Exception` handler computed its
        redaction keys by calling `resolve_connection()` — twice — so when
        RESOLUTION was what failed, the handler raised and the exception escaped
        `main()`. A bridge that dies with a traceback writes no JSON-RPC error, so
        the harness waits forever for an answer that is never coming, and the exit
        is indistinguishable from a crash loop. The handler now reads the live key
        through `_live_key()`, which cannot raise.
        """
        import io

        from tortoise import mcp_bridge

        _write_credential(home, "tt_start_secret", "http://127.0.0.1:9")
        monkeypatch.setenv("TORTOISE_MCP_URL", "http://127.0.0.1:9/mcp")

        real = mcp_bridge.resolve_connection
        calls = {"n": 0}

        def _flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                return real()   # main()'s own start-up resolution succeeds
            raise OSError("the credential store stopped being readable")

        monkeypatch.setattr(mcp_bridge, "resolve_connection", _flaky)
        monkeypatch.setattr(
            mcp_bridge, "forward",
            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))

        out = io.StringIO()
        monkeypatch.setattr(
            sys, "stdin",
            io.StringIO('{"jsonrpc":"2.0","id":1,"method":"initialize"}\n'))
        monkeypatch.setattr(sys, "stdout", out)

        assert mcp_bridge.main([]) == 0, "main() propagated the handler's own fault"

        emitted = out.getvalue()
        assert "-32603" in emitted, f"the frame was not answered: {emitted}"
        assert "tt_start_secret" not in emitted, emitted
        assert calls["n"] >= 2, (
            "the live-key lookup on the fault path was never attempted, so this "
            "test is not exercising the hazard it names")
        assert "tt_start_secret" not in capsys.readouterr().err

    def test_a_partially_answered_batch_does_not_leak_the_credential(self, home):
        """The answered message on the partial-batch path must be redacted too.

        `forward()` ends with a `_redact_message` sweep precisely because a relayed
        reply may quote the credential the server rejected. The partial-batch guard
        returns EARLY, so it bypasses that sweep — and before the guard existed this
        path relayed nothing at all, so re-instating the relay without redaction
        traded a silence for a leak on the busiest relay path.
        """
        import httpx

        from tortoise.mcp_bridge import forward

        # The credential must RESOLVE for the redaction sweep to have something to
        # remove: with no key there is nothing to leak, and the test would pass
        # vacuously.
        key = "tt" + "_batch_leak_guard"
        _write_credential(home, key, "http://127.0.0.1:9")
        # The server quotes the key inside the ANSWERED message for id 11.
        body = json.dumps({"jsonrpc": "2.0", "id": 11,
                           "error": {"code": -32000,
                                     "message": f"bad request, key {key} rejected"}})
        response = httpx.Response(
            200, content=body.encode(),
            headers={"content-type": "application/json"},
        )

        class _Client:
            def stream(self, *_a, **_k):
                class _Ctx:
                    def __enter__(self_inner):
                        return response

                    def __exit__(self_inner, *_exc):
                        return False

                return _Ctx()

        messages = forward(_Client(), {}, [
            {"jsonrpc": "2.0", "id": 11, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 12, "method": "tools/call"},
        ])

        relayed = json.dumps(messages)
        assert key not in relayed, f"the credential value was relayed verbatim: {relayed}"
        assert "<redacted>" in relayed, f"no redaction marker on the answered message: {relayed}"
        assert [m["id"] for m in messages] == [11, 12], messages

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
        # Its own tmp cwd, for the same reason as `bridge_factory`: the child reads
        # the PROJECT-LOCAL store first, so a checkout's `./.tortoise` must not be
        # in scope (#10727 review, P2).
        cwd = tmp_path / "daemon-cwd"
        cwd.mkdir(exist_ok=True)
        bridge = _Bridge(url, home, cwd)
        try:
            bridge.send(_INITIALIZE)
            assert "serverInfo" in bridge.read()["result"]
            bridge.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            names = [tool["name"] for tool in bridge.read()["result"]["tools"]]
            assert "tortoise_create_point" in names, names[:5]
        finally:
            bridge.close()
