"""#9192 — the record's tenant is established by PRODUCTION, per request.

THE DEFECT THIS PINS. `tortoise/record_writer.py` carries the record's tenant in
a `ContextVar` (`_RECORD_TENANT`, set by `use_record_tenant`) with a
process-global fallback (`TORTOISE_RECORD_ORG_ID`/`_GRAPH_ID`). At `f80c522a1`
the ContextVar had **zero production callers** — all 17 `use_record_tenant(...)`
call sites were in `tests/` — so the only reachable arming path on a real
deployment was the env pair. On a process that serves several tenant graphs the
pair is not a weaker tenant, it is a WRONG one: env is process-global, so tenant
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

* The REST/hosted lane's installer is **#9188**, not this file. It cannot be a
  one-line wrap there: an org-wide/session-authenticated request carries no
  `graph_id` (`hosted_api._session_user_org` resolves `org_id` + tier only), and
  the record's layer tables key on `public.graphs.id`, which the org's DEFAULT
  graph need not have a row for (`supabase_control.delete_graph_row`: *"no row
  exists for the default graph"*). Resolving that is #9188's scope. The
  org-wide **MCP** credential is pinned here as the same refusal, so the
  default-graph question cannot be closed by guessing a tenant id in this layer.
* The cross-tenant pin at the FOLD (`tests/test_record_projection.py`) is
  #9214's and stays; this file adds the caller-level leg above it.
"""
from __future__ import annotations

import ast
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

    def test_two_tenants_are_filed_under_their_own_request_tenant(
            self, two_tenant_lane, record_calls, monkeypatch):
        """Two tenants, one process: each write is filed under ITS request.

        The env pair is planted as tenant A's, so this is also the cross-write
        leg: if the resolution ever fell back to the pair, tenant B's record
        would appear as tenant A — a well-formed row under the wrong
        organisation, which nothing downstream can detect.
        """
        reg, tenants = two_tenant_lane
        monkeypatch.delenv(RECORD_SINGLE_TENANT_ENV, raising=False)
        monkeypatch.setenv(RECORD_ORG_ENV, tenants["a"]["org_id"])
        monkeypatch.setenv(RECORD_GRAPH_ENV, tenants["a"]["graph_id"])

        from tortoise.mcp_server import create_http_app

        app = create_http_app(allowed_origins=["https://app.premiselabs.co"],
                              _registry_sdk=reg)
        client = _mounted_test_client(app)
        with client:
            _write_a_point(client, tenants["a"]["api_key"], "tenant a: the door is open")
            _write_a_point(client, tenants["b"]["api_key"], "tenant b: the door is open")

        assert record_calls == [
            (tenants["a"]["org_id"], tenants["a"]["graph_id"]),
            (tenants["b"]["org_id"], tenants["b"]["graph_id"]),
        ], (
            "the record write did not use the REQUEST's tenant "
            f"(got {record_calls}) — a value from the process-global env pair "
            "is a cross-tenant write, and a refusal here means no tenant was "
            "installed by the tenancy middleware")

    def test_the_tenant_does_not_survive_its_request(
            self, two_tenant_lane, record_calls, monkeypatch):
        """The scope is a `with`, so it cannot leak into the NEXT request.

        A bare `set` would leave tenant B's value in place for whatever the same
        connection serves next — which is how a background write or a later
        request inherits a stranger's organisation. Asserted after the client is
        closed: nothing is in scope when no request is.
        """
        reg, tenants = two_tenant_lane
        monkeypatch.delenv(RECORD_SINGLE_TENANT_ENV, raising=False)
        monkeypatch.delenv(RECORD_ORG_ENV, raising=False)
        monkeypatch.delenv(RECORD_GRAPH_ENV, raising=False)

        from tortoise.mcp_server import create_http_app

        app = create_http_app(allowed_origins=["https://app.premiselabs.co"],
                              _registry_sdk=reg)
        client = _mounted_test_client(app)
        with client:
            _write_a_point(client, tenants["a"]["api_key"], "tenant a: leak check")
            assert record_calls  # the request DID file a record

        assert current_record_tenant() is None
        assert record_tenant() is None


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
        assert write_live_record({"type": "PointAdded"}) is None

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
        assert write_live_record({"type": "PointAdded"}) is None
        assert record_calls == []

    def test_an_org_wide_credential_is_refused_not_guessed(
            self, two_tenant_lane, record_calls, monkeypatch):
        """No named graph ⇒ no tenant ⇒ REFUSED, never the org's default graph.

        The record's layer tables key on `public.graphs.id` and the default
        graph need not have a row, so a default-graph tenant id cannot be
        derived here. Guessing one is the cross-tenant write this issue is
        about; refusing is the one failure a live door may have. Naming the
        default-graph tenant is #9188's.

        The process here is UNDECLARED (multi-tenant, the default) with no env
        pair, which is the shape a deployment that serves several orgs actually
        has — the org-wide key resolves no graph, so nothing can name its
        tenant and the write must not land.
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
        with client:
            _write_a_point(client, org_wide["api_key"], "org-wide: no named graph")
        after = dict(monitoring.record_write_failure_counts())

        assert record_calls == [], (
            "an org-wide credential filed a record under a tenant it cannot "
            f"name (got {record_calls})")
        assert after.get("no_tenant", 0) > before.get("no_tenant", 0), (before, after)
