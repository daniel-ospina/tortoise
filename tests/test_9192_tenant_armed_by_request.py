"""#9192 — the record's tenant is established by PRODUCTION, per request.

THE DEFECT THIS PINS. `tortoise/record_writer.py` carries the record's tenant in
a `ContextVar` (`_RECORD_TENANT`, set by `use_record_tenant`) with a
process-global fallback (`TORTOISE_RECORD_ORG_ID`/`_GRAPH_ID`). At `f80c522a1`
the ContextVar had **zero production callers** — all 10 `use_record_tenant(...)`
call sites (`git grep -c` at that ref) were in
`tests/test_9078_record_written_by_production_path.py`, and
`tests/test_record_projection.py` names the symbol in prose only — so the only
reachable arming path on a real deployment was the env pair. On a process that
serves several tenant graphs the pair is not a weaker tenant, it is a WRONG
one: env is process-global, so tenant
A's rows land under tenant B's `(org_id, graph_id)`, well-formed, with the FK
satisfied. Nothing raises. That is a silent cross-tenant write, which is why
this issue is a P0-blocker rather than a tidiness note.

WHAT PROVES IT NOW — three legs, deliberately different in kind:

1. **Production installs the scope** (`TestTheProductionCaller`). The MCP lane's
   `OrgResolutionMiddleware` resolves `(org_id, graph_id)` for every
   authenticated request; the tenant scope is entered there, so a write that
   runs anywhere under `call_next` — on the loop or on a context-copying pool
   worker — is filed under the REQUEST's tenant. The leg is BEHAVIOURAL: two
   tenants, two graph-bound credentials, one process, and the assertion is on
   what `write_records` is called with (the observable consequence on the
   record/write path), not on "the ContextVar was set".

2. **The env pair cannot name a request's tenant** — same two requests with the
   pair planted as a THIRD tenant's, and with the pair naming one of the two.
   Both must stay their own: a scope always wins, and a planted value never
   appears in the rows either request files.

3. **A process that cannot name its tenant REFUSES** (`TestTheArmingModes`).
   The pair is honoured only on a deployment that declared itself single-tenant
   (`TORTOISE_RECORD_SINGLE_TENANT`); undeclared (the default) it is ignored and
   the armed write is refused and counted `no_tenant` — never filed under it.
   That is the half that makes the wrong answer impossible rather than merely
   unlikely, and #9078's decision 3 (a single-tenant deployment may arm by env)
   is preserved *behind* the declaration, pinned here so it cannot be lost
   while closing the hazard.

WHAT IS NOT HERE, NAMED SO THE ABSENCE IS VISIBLE.

* The REST/hosted lane's installer is `hosted_api._data_sdk` →
  `_bind_record_tenant` (#9188 / #10025), not this file. It cannot be a
  one-line wrap on THIS lane: an org-wide/session-authenticated request carries
  no `graph_id` — it names the org's DEFAULT tenant through `default_graph_id`
  (see the next sentence), not through a per-graph id.
  `hosted_api._session_user_org` carries the org's `default_graph_id` through
  the same `supabase_control.default_graph_id_for_credential` home the key lane
  uses: #10025 named it for an org-wide `tt_`/`tk_` **API KEY**, #10046 for a
  **SESSION/JWT** credential and an OAuth `oat_` token. The org-wide **MCP**
  credential IS named on this lane since #10025 —
  `resolve_api_key` puts the org's active `kind='default'` row id on the
  credential as `default_graph_id`, and the pinned legs below assert BOTH
  directions: named when the control plane resolved the row
  (`test_an_org_wide_control_plane_key_is_named_by_its_default_graph`), and
  REFUSED when it did not. What genuinely remains #10047's is
  `mcp_auth`'s DEFAULT-plan branch
  (`record_tenant_for_graph({"kind": "default"})`) — the SEED/INDEX runner that
  builds its own SDK is wired since #10047
  (`hosted_api._bind_default_graph_record_tenant`); the default-graph question
  cannot be closed by guessing a tenant id anywhere.
* The cross-tenant pin at the FOLD (`tests/test_record_projection.py`) is
  #9214's and stays; this file adds the caller-level leg above it.
"""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import pytest

from tortoise import monitoring
from tortoise.record_writer import (
    RECORD_GRAPH_ENV,
    RECORD_ORG_ENV,
    RECORD_SINGLE_TENANT_ENV,
    RecordWrite,
    RecordWriteOutcome,
    current_record_tenant,
    record_tenant,
    use_record_tenant,
    write_live_record,
)
from tortoise.sdk import TortoiseSDK

REPO_ROOT = Path(__file__).resolve().parent.parent


# ── fixtures ─────────────────────────────────────────────────────────────────


def _stub_write() -> RecordWrite:
    """The real outcome type, with NO connection and a consumed position.

    The real `RecordWrite` (not a double) because `write_live_record` reports
    the position through `dataclasses.replace` on it (#9190's watermark path) —
    a hand-rolled stub would be pinned to today's attributes and break on the
    next one.

    `connection=None` keeps it from committing/closing anything: the record DB
    is not part of this proof (the tenant RESOLUTION is), and
    `tests/test_record_projection` owns the statement-level cross-tenant pin.
    """
    return RecordWrite(outcome=RecordWriteOutcome.APPLIED, events=1, statements=1)


@pytest.fixture
def record_calls(monkeypatch):
    """Armed record + a spy on the seam, returning the tenants it was called with.

    Patching `record_writer.write_records` (not `write_live_record`) is the
    point: the tenant resolution happens INSIDE `write_live_record`, so a spy
    one frame higher would prove nothing about which tenant was used. The DSN is
    a value that is never dialled, because the seam never runs.
    """
    import tortoise.record_writer as rw

    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://u:p@127.0.0.1:1/record")
    calls: list[tuple[str, str]] = []

    def _spy(records, *, org_id, graph_id, **kwargs):
        calls.append((org_id, graph_id))
        return _stub_write()

    monkeypatch.setattr(rw, "write_records", _spy)
    return calls


@pytest.fixture
def two_tenant_lane(tmp_path, monkeypatch):
    """A real MCP app over an embedded registry with TWO graph-bound tenants.

    Every tenant gets its own org, its own `public.graphs`-shaped registry graph
    (`_graph_create` → `graph_id`), and a graph-bound `tk_` key, because the
    tenant scope must come from what the AUTH layer resolves — an org-wide key
    (the last leg of this file) resolves no graph and is a different case.
    The JSONL journal is armed, because an unjournalled lane declines the record
    by design (`no_durable_journal`) and would make every leg here vacuous.
    """
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    db_path = str(tmp_path / "registry.db")
    monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", str(tmp_path / "events"))

    reg = TortoiseSDK(db_path=db_path, namespace="registry")
    tenants: dict[str, dict] = {}
    for name in ("a", "b"):
        org = reg.org_create(f"org-{name}")
        reg.org_update(org["id"], max_points=10_000, max_graphs=10, max_sessions=10_000)
        graph = reg._graph_create(org["id"], f"graph-{name}", kind="custom")
        key = reg.apikey_create(
            org["id"], f"probe-{name}", graph_id=graph["graph_id"],
            scopes=["graphs:read", "graphs:write"], prefix="tk_",
        )
        tenants[name] = {
            "org_id": org["id"], "graph_id": graph["graph_id"],
            "api_key": key["api_key"],
        }
    yield reg, tenants
    reg.close()


def _post_request(api_key: str, *, lane: str = "loop"):
    """A POST /mcp request whose bearer resolves through the auth middleware."""
    from starlette.requests import Request

    return Request({
        "type": "http", "http_version": "1.1", "method": "POST",
        "scheme": "https", "path": "/mcp", "raw_path": b"/mcp",
        "query_string": b"", "root_path": "",
        "headers": [(b"authorization", f"Bearer {api_key}".encode()),
                    (b"x-lane", lane.encode()),
                    (b"content-type", b"application/json")],
        "client": ("127.0.0.1", 54321),
        "server": ("testserver", 443),
        "state": {},
    })


@pytest.fixture
def control_plane_lane(monkeypatch):
    """A CONTROL-PLANE-keyed resolution: the id space the record can key on.

    The tenant scope is installed only where the ids come from `public.*`
    (`is_supabase_enabled()`, the predicate that also chooses the resolver),
    because the layer tables FK to `public.graphs` — so the multi-tenant leg
    has to model that lane. These stubs stand in for `resolve_api_key` (which
    returns the orgs row + `api_keys.graph_id`, `REFERENCES public.graphs(id)`),
    not for anything this PR changed; the middleware, the scope, the record
    resolver and the seam are all the real objects.
    """
    from tortoise import supabase_control as sc

    tenants: dict[str, dict] = {}
    by_key: dict[str, dict] = {}
    for leaf in ("a", "b"):
        org_id = f"org-{leaf}-cp"
        graph_id = f"3f9a0c1e-0b2d-4c8a-9e77-00000000000{leaf}"  # a uuid, not g_<hex>
        api_key = f"tk_{leaf}_control_plane"
        tenant = {"org_id": org_id, "graph_id": graph_id, "api_key": api_key}
        tenants[leaf] = tenant
        by_key[api_key] = {
            "org_id": org_id,
            "graph_id": graph_id,
            "graph_namespace": f"org_{org_id}_{graph_id}",
            "delegation_depth": None,
            "scopes": ["graphs:read", "graphs:write"],
            "legacy_full_access": True,
            "created_by": None,
            "suspended_at": None,
            "plan": "pro",
        }
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(sc, "get_control_plane", lambda: None)
    monkeypatch.setattr(sc, "resolve_api_key",
                        lambda _cp, token: by_key.get(token))
    return tenants


@pytest.fixture
def org_wide_control_plane_lane(monkeypatch):
    """A CONTROL-PLANE **org-wide** key whose default graph IS resolvable.

    #10025 materializes the org's default graph as an active `kind='default'`
    `public.graphs` row, and `resolve_api_key` carries its id on the credential
    as `default_graph_id`. That is what makes an org-wide credential nameable on
    this lane — and nothing pinned it: the pre-existing org-wide leg below runs
    the REGISTRY lane, whose refusal comes from `control_plane_ids=False`, so a
    refactor that dropped `default_graph_id` would silently return this lane to
    refusing every write while `/health` still read green (its counters are a
    DECLINE channel only).
    """
    from tortoise import supabase_control as sc

    org_id = "org-wide-cp"
    default_graph_id = "g_0123456789abcdef"
    api_key = "tk_org_wide_control_plane"
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(sc, "get_control_plane", lambda: None)
    monkeypatch.setattr(sc, "resolve_api_key",
                        lambda _cp, token: ({
                            "org_id": org_id,
                            "graph_id": None,
                            "default_graph_id": default_graph_id,
                            "graph_namespace": f"org_{org_id}",
                            "delegation_depth": None,
                            "scopes": ["graphs:read", "graphs:write"],
                            "legacy_full_access": True,
                            "created_by": None,
                            "suspended_at": None,
                            "plan": "pro",
                        } if token == api_key else None))
    return {"org_id": org_id, "default_graph_id": default_graph_id,
            "api_key": api_key}


def _mounted_test_client(app):
    """Wrap the MCP app in a Starlette Mount at /mcp (mirrors hosted_api).

    The MCP sub-app routes live at `/`; production mounts it under `/mcp`, and
    the router strips the prefix. Same wrapper `tests/test_mcp_http.py` uses, so
    the middleware under test runs in the shape it runs in production — a direct
    ASGI call would bypass the mount and could hide a prefix-dependent bug.
    """
    from contextlib import asynccontextmanager

    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.testclient import TestClient

    @asynccontextmanager
    async def _lifespan(parent_app):
        async with app.lifespan(app):
            yield

    parent = Starlette(lifespan=_lifespan, routes=[Mount("/mcp", app=app)])
    client = TestClient(parent)
    client.headers.update({
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    })
    return client


def _parse_sse_json(response):
    """Parse an MCP response body that may be SSE-framed (`event: message`)."""
    text = response.text
    if text.startswith("event:") or "\ndata: " in text:
        for line in text.splitlines():
            if line.startswith("data: "):
                return json.loads(line[len("data: "):])
        return None
    return response.json()


def _write_a_point(client, api_key: str, content: str) -> dict:
    """One MCP tool call that appends a journal record through a live door."""
    client.headers["Authorization"] = f"Bearer {api_key}"
    response = client.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "tortoise_create_point",
                   "arguments": {"kind": "statement", "content": content}},
    })
    assert response.status_code == 200, response.text
    body = _parse_sse_json(response)
    assert body is not None, response.text
    return body


# ── 1. the production caller ────────────────────────────────────────────────


class TestTheProductionCaller:
    """The tenant reaches the write path from the REQUEST, in production code."""

    def test_a_production_module_calls_the_record_tenant_setter(self):
        """`use_record_tenant` is CALLED from `tortoise/`, not only from tests.

        The defect class this issue names is "a seam with no production caller",
        and no test of the seam can see it — the tests call the seam themselves.
        So this leg is structural on purpose: parse the shipped package and
        require a CALL to the setter. `tests/**` is excluded by construction
        (the scan starts at `tortoise/`), so a probe cannot satisfy it.
        """
        callers: list[str] = []
        for path in sorted((REPO_ROOT / "tortoise").rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "use_record_tenant"):
                    callers.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "use_record_tenant"):
                    callers.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
        assert callers, (
            "no production caller of `record_writer.use_record_tenant` — the "
            "record's tenant is ambient again, and on a multi-tenant process "
            "the only way to arm it is the process-global env pair (#9192)")

    def test_an_org_wide_control_plane_key_is_named_by_its_default_graph(
            self, org_wide_control_plane_lane, record_calls):
        """#10025: the org-wide CONTROL-PLANE credential is NAMED, not refused.

        The tenant is `(org_id, default_graph_id)` where the id is the one the
        credential carries — resolved from the org's active `kind='default'`
        row, never derived from a name and never a constant. Driven at the
        MIDDLEWARE (the production caller), and asserted on what the record
        seam was called with, so this fails if the field stops being carried.
        """
        lane = org_wide_control_plane_lane

        async def _drive() -> list:
            from starlette.responses import JSONResponse

            from tortoise.mcp_auth import OrgResolutionMiddleware

            middleware = OrgResolutionMiddleware(app=None, registry_sdk=None)

            async def _call_next(request):
                write_live_record({"type": "PointAdded"})
                return JSONResponse({"ok": True})

            await middleware.dispatch(_post_request(lane["api_key"]), _call_next)
            return record_calls

        calls = asyncio.run(_drive())
        assert calls == [(lane["org_id"], lane["default_graph_id"])], (
            "an org-wide control-plane credential must file under its default "
            "graph's row id — refusing it is the armed-and-silent deployment "
            "#10025 exists to end, and filing it under anything else is a "
            "cross-tenant write")

    def test_the_env_pair_has_no_reader_outside_the_gated_resolver(self):
        """The gate is only worth anything if a NEW reader cannot bypass it.

        #9192 review C4: placing the declaration gate inside `record_tenant()` is
        a placement, not an invariant. Its sibling primitive — `write_records(records,
        org_id=..., graph_id=...)` — takes the tuple as a required kwarg, and
        `record_dsn()` reads `TORTOISE_RECORD_DSN` five lines above the resolver,
        so a future lane that reads `TORTOISE_RECORD_ORG_ID`/`_GRAPH_ID` itself
        (the obvious copy of its neighbour) reopens the silent cross-tenant write
        with this whole file green. So the READER gets the same structural pin
        the SETTER got above: inside the shipped package the env literals may be
        touched only inside `record_tenant()` / `_refused_tenant_env_note`.

        Deliberately literal-name based: it catches the READ SHAPE, not one
        spelling of it (`RECORD_ORG_ENV`, the raw literal, `os.environ.get(...)`,).
        """
        allowed_functions = {"record_tenant", "_refused_tenant_env_note",
                             # the declaration gate itself
                             "single_tenant_declared"}
        env_literals = {RECORD_ORG_ENV, RECORD_GRAPH_ENV, RECORD_SINGLE_TENANT_ENV}
        const_names = {"RECORD_ORG_ENV", "RECORD_GRAPH_ENV",
                       "RECORD_SINGLE_TENANT_ENV"}
        violations: list[str] = []
        for path in sorted((REPO_ROOT / "tortoise").rglob("*.py")):
            rel = path.relative_to(REPO_ROOT)
            tree = ast.parse(path.read_text(), filename=str(path))
            allowed_ranges: list[tuple[int, int]] = []
            for node in ast.walk(tree):
                if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name in allowed_functions):
                    allowed_ranges.append((node.lineno, node.end_lineno or node.lineno))
                # The module-level DEFINITIONS of the constants are not readers.
                if (isinstance(node, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id in const_names
                                for t in node.targets)):
                    allowed_ranges.append((node.lineno, node.end_lineno or node.lineno))
            for node in ast.walk(tree):
                names: set[str] = set()
                if isinstance(node, ast.Name) and node.id in const_names:
                    names.add(node.id)
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and node.value in env_literals):
                    names.add(node.value)
                if not names:
                    continue
                lineno = getattr(node, "lineno", 0)
                inside = any(lo <= lineno <= hi for lo, hi in allowed_ranges)
                if not inside:
                    violations.append(f"{rel}:{lineno} → {sorted(names)}")
        assert not violations, (
            "a reader of the process-global record tenant lives outside the "
            "gated resolver — it bypasses TORTOISE_RECORD_SINGLE_TENANT, which "
            "is the cross-tenant write #9192 removed. Move it into "
            "record_writer.record_tenant() or go through record_tenant():\n  "
            + "\n  ".join(violations))

    def test_two_tenants_are_filed_under_their_own_request_tenant(
            self, control_plane_lane, record_calls, monkeypatch):
        """Two tenants, one process: each write is filed under ITS request.

        Driven at the MIDDLEWARE, because the installation is conditioned on a
        control-plane id space (`is_supabase_enabled()`) and the registry lane
        used by the HTTP legs cannot produce one — see
        `test_a_registry_graph_id_is_never_installed` for that lane's own
        behaviour. What is under test here is the attribution, and the assertion
        is on what `write_records` was called with (the observable consequence
        on the record/write path), not on "the ContextVar was set".

        A DECLARED single-tenant pair is planted as tenant A's, so this is also
        the precedence leg: if the pair could outrank the scope, tenant B's row
        would appear as tenant A — well-formed, and undetectable downstream.
        Tenant B's write runs on a WORKER THREAD (`asyncio.to_thread`), which is
        where the repo's offloads run: the context copy is the property that
        makes the scope reach them.
        """
        tenants = control_plane_lane
        monkeypatch.setenv(RECORD_SINGLE_TENANT_ENV, "1")
        monkeypatch.setenv(RECORD_ORG_ENV, tenants["a"]["org_id"])
        monkeypatch.setenv(RECORD_GRAPH_ENV, tenants["a"]["graph_id"])

        async def _drive() -> list:
            seen: list = []
            from starlette.responses import JSONResponse

            from tortoise.mcp_auth import OrgResolutionMiddleware

            middleware = OrgResolutionMiddleware(app=None, registry_sdk=None)

            async def _call_next(request):
                # The write is what a tool would do under `call_next`.
                if request.headers["x-lane"] == "thread":
                    await asyncio.to_thread(write_live_record, {"type": "PointAdded"})
                else:
                    write_live_record({"type": "PointAdded"})
                seen.append(current_record_tenant())
                return JSONResponse({"ok": True})

            for name in ("a", "b"):
                await middleware.dispatch(
                    _post_request(tenants[name]["api_key"],
                                  lane="thread" if name == "b" else "loop"),
                    _call_next)
            return seen

        seen = asyncio.run(_drive())
        assert record_calls == [
            (tenants["a"]["org_id"], tenants["a"]["graph_id"]),
            (tenants["b"]["org_id"], tenants["b"]["graph_id"]),
        ], (
            "the record write did not use the REQUEST's tenant "
            f"(got {record_calls}) — a value from the process-global env pair "
            "is a cross-tenant write, and a refusal here means no tenant was "
            "installed by the tenancy middleware")
        assert seen == [
            (tenants["a"]["org_id"], tenants["a"]["graph_id"]),
            (tenants["b"]["org_id"], tenants["b"]["graph_id"]),
        ], f"the scope was not what the request saw (got {seen})"

    def test_the_tenant_does_not_survive_its_request(self, control_plane_lane):
        """The scope is a `with` in the SAME context, so it cannot outlive it.

        ⛔ THIS LEG DELIBERATELY DOES NOT USE THE MOUNTED CLIENT (#9192 review
        B1). A `TestClient` runs the app in the portal thread's loop, so the
        assertions in THIS thread would read `None` whatever the middleware did
        — the leg passed with a bare, never-reset `_RECORD_TENANT.set(...)`
        planted in a probe middleware, which is the vacuous-test class. So the
        middleware is AWAITED DIRECTLY here: `dispatch` runs in this test's own
        context, which makes any `set` without a reset visible to the assertion
        after the `await`, while the `with` restores it.
        """
        tenants = control_plane_lane

        async def _drive() -> list:
            seen: list = []
            from starlette.responses import JSONResponse

            from tortoise.mcp_auth import OrgResolutionMiddleware

            middleware = OrgResolutionMiddleware(app=None, registry_sdk=None)

            async def _call_next(_request):
                seen.append(current_record_tenant())
                return JSONResponse({"ok": True})

            assert current_record_tenant() is None
            await middleware.dispatch(
                _post_request(tenants["a"]["api_key"]), _call_next)
            # SAME context as the dispatch ⇒ a bare `set` is still here.
            assert current_record_tenant() is None, (
                "the record's tenant outlived its request — the middleware set "
                "the ContextVar without restoring it, so the next write in this "
                "context (a background task, a pooled worker, a later request on "
                "a reused context) would be filed under a stranger's tenant")
            assert record_tenant() is None
            return seen

        seen = asyncio.run(_drive())
        org = tenants["a"]
        assert seen == [(org["org_id"], org["graph_id"])], (
            "the middleware did not install the credential's tenant around the "
            f"request (saw {seen})")

    def test_a_registry_graph_id_is_never_installed(
            self, two_tenant_lane, record_calls, monkeypatch):
        """The SELF-HOST lane's graph ids are not record ids — install nothing.

        #9192 review C1. `apikey_verify` returns `api_keys.graph_id` on the
        Supabase lane, which is `REFERENCES public.graphs(id)` — the id the
        record's layer tables key on. On the self-host registry lane the same
        field is the Falkor `:Graph` node id (`g_<hex>`, minted in
        `sdk._graph_create`) and registry graphs are never inserted into
        `public.graphs`, so installing it would displace a correctly declared
        single-tenant pair with a value the FK cannot satisfy — every write
        `write_failed`, cause unattributable.

        So the lane keeps its pre-#9192 arming exactly: UNDECLARED ⇒ refused
        (never misfiled under the planted pair), DECLARED single-tenant ⇒ filed
        under the pair. Both phases drive the real mounted MCP app.
        """
        reg, tenants = two_tenant_lane
        monkeypatch.setenv(RECORD_ORG_ENV, tenants["a"]["org_id"])
        monkeypatch.setenv(RECORD_GRAPH_ENV, tenants["a"]["graph_id"])
        monkeypatch.delenv(RECORD_SINGLE_TENANT_ENV, raising=False)

        from tortoise.mcp_server import create_http_app

        app = create_http_app(allowed_origins=["https://app.premiselabs.co"],
                              _registry_sdk=reg)
        client = _mounted_test_client(app)
        before = dict(monitoring.record_write_failure_counts())
        with client:
            _write_a_point(client, tenants["a"]["api_key"], "a: undeclared")
            _write_a_point(client, tenants["b"]["api_key"], "b: undeclared")
        after = dict(monitoring.record_write_failure_counts())

        assert record_calls == [], (
            "a registry-lane request filed a record under an id the record's "
            "tables cannot key on (or under the env pair it did not declare): "
            f"{record_calls}")
        assert after.get("no_tenant", 0) >= before.get("no_tenant", 0) + 2, (
            before, after)

        # DECLARED: the self-host single-tenant deployment still records, and it
        # records under the pair — the pre-#9192 behaviour, preserved.
        monkeypatch.setenv(RECORD_SINGLE_TENANT_ENV, "1")
        record_calls.clear()
        with client:
            _write_a_point(client, tenants["a"]["api_key"], "a: declared")
        assert record_calls == [(tenants["a"]["org_id"], tenants["a"]["graph_id"])], (
            f"a declared single-tenant self-host deployment lost its pair "
            f"(got {record_calls})")


# ── 2. the arming modes ─────────────────────────────────────────────────────


def _clear_record_env(monkeypatch):
    for name in (RECORD_ORG_ENV, RECORD_GRAPH_ENV, RECORD_SINGLE_TENANT_ENV):
        monkeypatch.delenv(name, raising=False)


class TestTheArmingModes:
    """Who may name the tenant, and what a process that cannot says."""

    def test_an_undeclared_process_refuses_the_environment_tenant(
            self, record_calls, monkeypatch):
        """The env pair is INERT until the deployment declares single tenancy.

        This is the leg that removes the hazard rather than guarding it: with no
        process-global answer available, a multi-tenant process cannot file one
        tenant's rows under another. It fails CLOSED (refused + counted), and
        the count is `no_tenant` — the term an operator can act on.
        """
        _clear_record_env(monkeypatch)
        monkeypatch.setenv(RECORD_ORG_ENV, "org-from-env")
        monkeypatch.setenv(RECORD_GRAPH_ENV, "graph-from-env")
        before = dict(monitoring.record_write_failure_counts())

        assert record_tenant() is None
        _refused = write_live_record({"type": "PointAdded"})
        # #8447: the refusal is REPORTED — not the `None` an unarmed deployment
        # returns, which is the value this assertion used to accept.
        assert _refused is not None and _refused.applied is False, _refused
        assert _refused.refusal == "no_tenant", _refused

        assert record_calls == [], "an undeclared process filed a record under env"
        after = dict(monitoring.record_write_failure_counts())
        assert after.get("no_tenant", 0) > before.get("no_tenant", 0), (before, after)

    def test_the_refusal_names_the_missing_declaration(
            self, record_calls, monkeypatch, caplog):
        """An operator who SET the pair must be told why it did not arm."""
        _clear_record_env(monkeypatch)
        monkeypatch.setenv(RECORD_ORG_ENV, "org-from-env")
        monkeypatch.setenv(RECORD_GRAPH_ENV, "graph-from-env")
        with caplog.at_level("WARNING"):
            write_live_record({"type": "PointAdded"})
        message = "\n".join(record.message for record in caplog.records)
        assert "no_tenant" in message, message
        assert RECORD_SINGLE_TENANT_ENV in message, (
            "the refusal did not name the declaration an operator must set — "
            f"an unexplained refusal is a support ticket: {message}")

    def test_a_declared_single_tenant_deployment_still_arms_by_env(
            self, record_calls, monkeypatch):
        """#9078 decision 3 is PRESERVED, behind the declaration.

        The declaration exists to make the pair explicit, not to remove it: a
        deployment that serves one graph keeps arming with two environment
        variables and no code change.
        """
        _clear_record_env(monkeypatch)
        monkeypatch.setenv(RECORD_ORG_ENV, "solo-org")
        monkeypatch.setenv(RECORD_GRAPH_ENV, "solo-graph")
        monkeypatch.setenv(RECORD_SINGLE_TENANT_ENV, "1")

        assert record_tenant() == ("solo-org", "solo-graph")
        write = write_live_record({"type": "PointAdded"})
        assert write is not None
        assert record_calls == [("solo-org", "solo-graph")]

    def test_a_scope_still_beats_a_declared_pair(self, record_calls, monkeypatch):
        """A request scope is the per-request truth; the pair never outranks it."""
        _clear_record_env(monkeypatch)
        monkeypatch.setenv(RECORD_ORG_ENV, "solo-org")
        monkeypatch.setenv(RECORD_GRAPH_ENV, "solo-graph")
        monkeypatch.setenv(RECORD_SINGLE_TENANT_ENV, "1")

        with use_record_tenant("request-org", "request-graph"):
            write_live_record({"type": "PointAdded"})
        assert record_calls == [("request-org", "request-graph")]

    def test_a_half_pair_is_never_completed(self, record_calls, monkeypatch):
        """One coordinate is a misconfiguration, not a tenant (even declared)."""
        _clear_record_env(monkeypatch)
        monkeypatch.setenv(RECORD_SINGLE_TENANT_ENV, "1")
        monkeypatch.setenv(RECORD_ORG_ENV, "solo-org")

        assert record_tenant() is None
        _refused = write_live_record({"type": "PointAdded"})
        assert _refused is not None and _refused.refusal == "no_tenant", _refused
        assert record_calls == []

    @pytest.mark.parametrize("declared", ["", "   ", "0", "false", "no",
                                          "off", "yes-please", "ture"])
    def test_any_non_truthy_declaration_is_multi_tenant(
            self, declared, record_calls, monkeypatch):
        """The declaration is READ through `env_flag`, not by presence.

        `.env.example` promises an operator that "a typo reads as ABSENT (refused
        rows, not misfiled rows)". A presence read (`os.environ.get(...) is not
        None`) satisfies every other leg in this class — the key is DELETED in
        one and "1" in another — while `TORTOISE_RECORD_SINGLE_TENANT=0`, the
        operator saying explicitly "I am not single-tenant", would arm the
        process-global pair. That is the silent cross-tenant write this issue
        removes, so the vocabulary is pinned here, at the same seam.
        """
        _clear_record_env(monkeypatch)
        monkeypatch.setenv(RECORD_ORG_ENV, "solo-org")
        monkeypatch.setenv(RECORD_GRAPH_ENV, "solo-graph")
        monkeypatch.setenv(RECORD_SINGLE_TENANT_ENV, declared)

        from tortoise.record_writer import single_tenant_declared

        assert single_tenant_declared() is False, (
            f"{RECORD_SINGLE_TENANT_ENV}={declared!r} declared the deployment "
            "single-tenant — the key is being read for PRESENCE, not truth")
        assert record_tenant() is None
        _refused = write_live_record({"type": "PointAdded"})
        assert _refused is not None and _refused.refusal == "no_tenant", _refused
        assert record_calls == []

    @pytest.mark.parametrize("declared", ["1", "true", "TRUE", "yes", "on"])
    def test_the_truthy_vocabulary_declares(
            self, declared, record_calls, monkeypatch):
        """The other half of the same seam: a real declaration still arms."""
        _clear_record_env(monkeypatch)
        monkeypatch.setenv(RECORD_ORG_ENV, "solo-org")
        monkeypatch.setenv(RECORD_GRAPH_ENV, "solo-graph")
        monkeypatch.setenv(RECORD_SINGLE_TENANT_ENV, declared)

        assert record_tenant() == ("solo-org", "solo-graph")

    def test_an_org_wide_credential_is_refused_not_guessed(
            self, two_tenant_lane, record_calls, monkeypatch, caplog):
        """An org-wide credential on the REGISTRY lane is REFUSED, never guessed.

        ⚠️ This is the REGISTRY lane: the refusal here comes from the
        `control_plane_ids=False` id space, not from an unnameable default graph.
        The control-plane org-wide case is the OPPOSITE direction since #10025
        and is pinned by
        `test_an_org_wide_control_plane_key_is_named_by_its_default_graph` two
        tests up — the two lanes stay one answer each, and neither guesses.

        The process here is UNDECLARED (multi-tenant, the default) with no env
        pair, which is the shape a deployment that serves several orgs actually
        has — and the registry lane's `g_<hex>` Falkor ids have no
        `public.graphs` row, so nothing can name a tenant there and the write
        must not land.
        """
        reg, tenants = two_tenant_lane
        _clear_record_env(monkeypatch)
        org_wide = reg.apikey_create(
            tenants["b"]["org_id"], "probe-org-wide",
            scopes=["graphs:read", "graphs:write"])

        from tortoise.mcp_server import create_http_app

        app = create_http_app(allowed_origins=["https://app.premiselabs.co"],
                              _registry_sdk=reg)
        client = _mounted_test_client(app)
        before = dict(monitoring.record_write_failure_counts())
        with client, caplog.at_level("WARNING"):
            _write_a_point(client, org_wide["api_key"],
                           "org-wide: no named graph")
        after = dict(monitoring.record_write_failure_counts())

        assert record_calls == [], (
            "an org-wide credential filed a record under a tenant it cannot "
            f"name (got {record_calls})")
        assert after.get("no_tenant", 0) > before.get("no_tenant", 0), (before, after)
        # The remediation must be one this operator can ACT on. "wrap the write
        # in use_record_tenant" is not: this lane installs that scope per
        # request, so the actionable sentence names the graph-bound credential
        # (#9192 review A5).
        message = "\n".join(record.message for record in caplog.records)
        assert "no_tenant" in message, message
        assert "tk_" in message and "graph-bound" in message, (
            "the org-wide refusal did not name the credential fix, so its only "
            f"advice is unfollowable on this lane: {message}")


# ── suite isolation ─────────────────────────────────────────────────────────


class TestTheSuiteCannotBeArmedByTheDeveloperMachine:
    """#9192 review P2: the FOURTH arming key must be cleared for every test.

    `tests/conftest.py::_no_ambient_record_arming` exists because a developer
    lane armed exactly the way `.env.example` documents armed every test that
    drives a record door. This PR makes that recipe **four** variables — the
    `TORTOISE_RECORD_SINGLE_TENANT` declaration is now part of it, and it is the
    half that makes the pair *reachable* — so the fixture has to clear it too.

    Structural on purpose, and for the same reason the production-caller leg is:
    the failure mode is a test that plants the pair and expects a REFUSAL without
    clearing the declaration. Such a test is green in CI (no ambient declaration
    there) and arms on the developer's machine — exactly the local-env-dependent
    split the fixture was added to prevent — so no ordinary leg can see it.
    Parsing the fixture is the only instrument that does.
    """

    def test_the_autouse_fixture_clears_the_declaration_too(self):
        src = (REPO_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fixture = next(
            (node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name == "_no_ambient_record_arming"), None)
        assert fixture is not None, (
            "`_no_ambient_record_arming` is gone from tests/conftest.py — the "
            "record's arming keys are ambient for every test again")

        # The clears live in `for name in (...): monkeypatch.delenv(name)`.
        cleared: set[str] = set()
        for node in ast.walk(fixture):
            if not isinstance(node, ast.For) or not isinstance(node.target, ast.Name):
                continue
            if not any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                       and c.func.attr == "delenv" for c in ast.walk(node)):
                continue
            cleared |= {e.value for e in ast.walk(node.iter)
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)}

        for name in ("TORTOISE_RECORD_DSN", "TORTOISE_RECORD_ORG_ID",
                     "TORTOISE_RECORD_GRAPH_ID", RECORD_SINGLE_TENANT_ENV):
            assert name in cleared, (
                f"tests/conftest.py does not clear {name} per test — a machine "
                "armed the way `.env.example` documents leaks it into the whole "
                f"suite (cleared: {sorted(cleared)})")
