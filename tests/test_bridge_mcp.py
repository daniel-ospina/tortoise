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

# Imported at COLLECTION, on purpose. `integrations.crm.twenty.bridge` captures
# `Path.home()` / `expanduser("~")` into module constants at IMPORT time
# (`CAL_STATE_FILE`, `CONFIG["review_queue_path"]`) — the capture-at-start class
# `tortoise/mcp_client.py` deliberately avoids (#10605). `_no_ambient_store` below
# redirects HOME per test, so a body-level import would freeze those constants into
# a ONE-TEST pytest tmp dir that pytest then deletes, for every later test in the
# same process. Collection-time import under the ambient HOME is the construction
# `tests/test_8491_cal_attendee_grounding.py` already uses; the bridge's own
# capture-at-start bug is filed as #12968 rather than carried here.
import integrations.crm.twenty.bridge as bridge

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

        result = bridge.push_to_tortoise(SAMPLE_FRONTMATTER)
        assert result["status"] == "ok"
        types = [p["type"] for p in result["points"]]
        assert types == ["meeting", "decision", "commitment"]
        # ids resolved from the daemon (not None)
        assert all(p.get("id") for p in result["points"])

    def test_daemon_down_graceful_skip(self, monkeypatch):
        monkeypatch.setenv("TORTOISE_MCP_URL", "http://127.0.0.1:1/mcp")
        monkeypatch.setenv("TORTOISE_API_KEY", "")

        result = bridge.push_to_tortoise(SAMPLE_FRONTMATTER)
        assert result == {"status": "skipped", "reason": "tortoise_unavailable"}


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
        result = self._result(
            '{"id": "pt_1"}',
            "Graph: this write targeted the workspace default graph (default).",
        )
        assert bridge._point_id(result) == ("pt_1", None)

    def test_a_non_dict_payload_stays_a_failure_and_never_raises(self):
        """A non-dict payload is `unparseable_result`. The trailing prose block
        must not turn it into an id, and must not raise out of the extractor."""
        result = self._result(
            "[1, 2]", "Graph: this write targeted the workspace default graph."
        )
        assert bridge._point_id(result) == (None, "unparseable_result")


@pytest.fixture(autouse=True)
def _no_ambient_store(monkeypatch, tmp_path):
    """No test in this module may read a REAL credential store.

    `tortoise.mcp_client.resolve_connection` falls back to the stores
    `tortoise init` writes — `./.tortoise` (project-local) then
    `~/.tortoise/credentials.json` (#1708 D5) — when no env KEY names one. These
    tests declare the endpoint with `TORTOISE_MCP_URL` and set
    `TORTOISE_API_KEY=""` (a blank value is deliberately ABSENT), so on a box
    that has run `tortoise init` the STORE supplies the key — and by the #2369
    D1.1 CO-SOURCE rule the store then supplies the HOST too, overriding the URL
    the fixture just declared here. The push then probes the stored host instead
    of this fixture's ephemeral daemon, the hermetic egress guard (#4387) blocks
    the request, and `push_to_tortoise` returns `{"status": "skipped"}` — the
    exact `assert 'skipped' == 'ok'` that reded `test (a)` on main at
    bdda17dd22d0, from the HOST's state rather than from any diff.

    Isolating HOME and CWD makes this module's result independent of who ran it
    — the same fixture, for the same reason, as
    `tests/test_mcp_client.py::_no_ambient_store`.
    """
    home = tmp_path / "ambient-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # …and the PROJECT-LOCAL store `tortoise init --api-key` writes (`./.tortoise`),
    # which is the other candidate in the same chain (#1708 D5).
    (tmp_path / "cwd").mkdir()
    monkeypatch.chdir(tmp_path / "cwd")
    # …AND the ENV half, which isolating the stores does NOT cover. The tests below
    # blank `TORTOISE_API_KEY` only; `resolve_connection()` reads BOTH names, in
    # precedence order (`mcp_client._ENV_KEY_NAMES`), and `TORTOISE_MCP_API_KEY` is
    # the fleet alias (#5200) that a CI cell exports. Left set, it ANSWERS — so the
    # store branch is never reached and this module sends a REAL fleet key to the
    # test daemon (measured on a cell: `sources == ('TORTOISE_API_KEY',
    # 'TORTOISE_MCP_API_KEY')`, no store read). The store pin below still names a
    # leaked store in that state — it reads `_credential_paths()` and stats each
    # candidate directly, so it never consults env precedence — but the endpoint
    # pin beside it measures the RESOLVED identity, so with the alias ambient it
    # reds on the alias rather than on the store the module is meant to isolate.
    # This is the `delenv` half of the recorded idiom —
    # `docs/plans/1708-key-mint-idempotency/plan.md` step 5 prescribes setenv(HOME)
    # + delenv(TORTOISE_API_KEY) + chdir; the sibling fixtures copied the other two.
    #
    # ⛔ The NAMES are ASKED OF THE CODE, for the same reason the store pin below
    # asks `_credential_paths()` instead of restating today's two paths: a literal
    # list is exactly the fail-open that grew once already (#5200 added the
    # `TORTOISE_MCP_API_KEY` alias). A third name would answer ambiently, the store
    # branch would never be reached, and this module would send a REAL fleet key to
    # the test daemon. The STORE pin would stay green in that state — it checks
    # `path.is_file()` and never whether a store was READ — while the ENDPOINT pin
    # below is what reds, on the third name exactly as it does on the alias today
    # (see the paragraph above). That asymmetry is why the NAMES must come from the
    # code rather than from a list here.
    from tortoise.mcp_client import _ENV_KEY_NAMES

    for name in _ENV_KEY_NAMES:
        monkeypatch.delenv(name, raising=False)


class TestNoStoredCredentialDecidesTheEndpoint:
    """Precondition pins for `_no_ambient_store` (#2369 D1.1).

    These are PRECONDITIONS, not behaviour: they fail LOUD and name the cause
    instead of letting the module pass on a clean box and quietly probe a live
    host on a dirty one.
    """

    def test_no_credential_store_is_reachable_from_this_test(self):
        """Ask the CODE where the stores are — a re-spelled list can hide a third.

        The candidate chain is `mcp_client._credential_paths()`'s to own. A pin
        that hard-codes today's two paths stays green the day a third candidate
        is added, while this module reads that new real store and silently
        measures a live host again — a fail-open inside the fail-loud guard.

        `assert candidates` is safe here BECAUSE `_no_ambient_store` sets HOME to
        a tmp dir: the two ways `_credential_paths()` legitimately loses a
        candidate (an unresolvable `Path.home()`, e.g. a UID with no passwd
        entry; a REMOVED `Path.cwd()`, e.g. a reaped worktree — both named in
        its own docstring) cannot happen under this fixture, so an empty chain
        means this pin measured nothing and must say so.
        """
        from tortoise.mcp_client import _credential_paths

        candidates = list(_credential_paths())
        assert candidates, (
            "the credential-store chain resolved to NO candidates, so this pin "
            "measured nothing — it is not a PASS"
        )
        found = [str(path) for path, _repo_controlled in candidates if path.is_file()]
        assert not found, (
            "an ambient credential store reached this test: "
            f"{found}. It carries a KEY, and #2369 D1.1 makes the store that "
            "supplies the key supply the HOST as well — so TORTOISE_MCP_URL below "
            "is overridden and this module measures whatever host the store names. "
            "THIS IS NOT YOUR DIFF: `_no_ambient_store` is supposed to isolate every "
            "store in that chain; if one is visible here, the fixture is gone or bypassed."
        )

    def test_the_declared_daemon_url_is_the_one_the_client_resolves(self, monkeypatch):
        """`TORTOISE_MCP_URL` must be what the client actually talks to.

        No `daemon_url` here ON PURPOSE: resolution needs no DAEMON — it reads the
        env, and for the credential it may stat/read the stores (`_credential_paths`,
        `_git_tracks`) — so a literal URL pins the identical property. Booting a
        server only to produce a string would add a way for this precondition to
        red as `daemon did not start`: an infrastructure failure wearing the name
        of a credential-isolation one.
        """
        from tortoise.mcp_client import resolve_connection

        declared = "http://127.0.0.1:9/mcp"
        monkeypatch.setenv("TORTOISE_MCP_URL", declared)
        monkeypatch.setenv("TORTOISE_API_KEY", "")

        connection = resolve_connection()
        assert connection.url == declared, (
            f"the client resolved {connection.url!r} instead of the declared "
            f"{declared!r} (sources read: {connection.sources})"
        )
        # ⛔ `source`, not `declared`: `declared` is `bool(env_url)` on the env arm
        # AND on the no-key arm, and the store arm sets it from the store's own
        # `api_url` — so it is `True` in every arm that can satisfy the assertion
        # above, which makes it entailed and unable to tell an env-resolved
        # identity from a store-resolved one (#2369 D1.1's whole question).
        # `source` is the discriminating field: the env arm names the env KEY, the
        # store arm names the store path, and "no credential anywhere" is `None` —
        # which is the only state `_no_ambient_store` permits.
        assert connection.source is None, (
            f"this test resolved its identity from {connection.source!r} instead of "
            f"from nothing at all (sources read: {connection.sources}). THIS IS NOT "
            "YOUR DIFF: `_no_ambient_store` deletes both env key names and isolates "
            "both stores, so no credential source may answer here."
        )


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

        root = Path(__file__).resolve().parents[1] / "integrations"
        # ⛔ An ABSOLUTE root, and the gate must MEASURE something. `_no_ambient_store`
        # changes CWD, and `rglob` on a path that is not there yields an empty list —
        # so `Path("integrations")` would report zero offenders and pass VACUOUSLY
        # (a false PASS in the exact shape this gate exists to catch).
        scanned = list(root.rglob("*.py"))
        assert scanned, f"the AST gate read NO files under {root} — this is not a PASS"
        offenders = []
        for py in scanned:
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

        src = (Path(__file__).resolve().parents[1]
               / "integrations/crm/twenty/bridge.py").read_text()
        # The same non-vacuity guard as the gate above: an EMPTY source parses to a
        # tree with no nodes, so `any(...)` would report "no mcp_client import" and
        # fail for a reason that has nothing to do with the bridge.
        assert src.strip(), "the bridge source read EMPTY — this is not a PASS"
        tree = ast.parse(src)
        assert any(
            isinstance(n, ast.ImportFrom) and n.module == "tortoise.mcp_client"
            for n in ast.walk(tree)
        ), "bridge must import tortoise.mcp_client"
