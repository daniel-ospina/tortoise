"""#9664 capstone — the CONNECTOR journey reaches a real record, and its id reads back.

WHAT THIS PINS, AND WHY IT IS NOT THE WHOLE CAPSTONE.

#9664 asks for one manual run of the ten-step ChatGPT onboarding journey, with a
per-step verdict, and rules itself *"not a script. Not a CI job."* The decisive
half of that run (steps 4, 6-9 *through ChatGPT*) needs a Plus/Pro session and a
human for the sign-in, so it stays `BLOCKED-ON-EXTERNAL` and this file does not
claim it. What an agent lane CAN prove — and what no test in this tree proved
before — is the part that decides whether the run would have found a working
record at the end of it:

    a request that arrives over the CONNECTOR (authenticated `POST /mcp`,
    `tools/call`) makes a Point exist in the Postgres RECORD, under the
    request's own tenant, and the identifier the client is handed reads the
    same Point back.

Each leg below is the measurable form of one of the capstone's four questions,
and the ABSENCE that motivated each is named on it. What is proven elsewhere and
is therefore CITED rather than duplicated:

* **step 2 — the served skill equals the repo.** `tests/test_onboarding_variants.py`
  pins the dashboard deploy mirror byte-for-byte (`test_m8_deploy_mirror_matches_canonical`)
  and the `.skill` archive payload against its served `SKILL.md`
  (`test_9660_skill_archive_holds_exactly_its_own_skill`). Measured at
  `a999be41e`: `diff tortoise/onboarding/SKILL.md
  website/apps/dashboard/public/skills/tortoise-onboarding/SKILL.md` → identical.
* **step 2's copy is not merely stored but ADVERTISED-name-clean.**
  `test_9660_chatgpt_first_job_names_only_ADVERTISED_tools` asserts the rendered
  ChatGPT first job names no retired tool. The same rule applied to the tool
  DESCRIPTIONS is `test_a_live_tool_description_never_recommends_a_retired_tool`
  below, which is the one place that rule did not reach.
* **#9078 / #9192** already prove the fold writes four ontology layers from the
  SDK's capture path, and that the MCP lane installs the REQUEST's tenant on the
  write. Neither drives a tool call through the HTTP transport into Postgres:
  `git grep -l TEST_RECORD_DB_URI -- tests/` returned only
  `test_9078_...` (SDK path) and `test_ci_selection.py` (the workflow pin), so
  before this file the sentence "an MCP call reaches the record" had no test.

#9557 (backup + restore) and #9555 (S1 narrative / raw turn text) are NOT
depended on here: a row that lands and reads back is provable without either, and
a capstone that blocked on them would prove nothing tonight.

RUNNING IT. Needs a Postgres carrying the record migrations (`TEST_RECORD_DB_URI`
— the armed `test-record-postgres` CI job, #9551) and a graph backend: the
graph-bound key writes the Point into a graph FIRST and the record row follows
the journal append, so the embedded carve-out (`TORTOISE_TEST_CARVE_OUT=1`, no
`TORTOISE_DB_URI`) and a live FalkorDB both exercise it. A run with no
`TEST_RECORD_DB_URI` SKIPS by name — it must never pass by asserting nothing.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

#: The record's database — the same variable `test_9078` uses, and the one the
#: `test-record-postgres` job exports (`.github/actions/postgres-provision`).
RECORD_DB_ENV = "TEST_RECORD_DB_URI"

#: The tools this file drives, in order: verify, file the first memory, then read
#: it back by id — plus the two listing tools the capstone's step 3 exercises.
#: The served ChatGPT first job names only the first two (verify, then file), so
#: this tuple is a SUPERSET of that job rather than a transcription of it; the
#: read-back and the listing pair come from the capstone's own journey.
#: `tortoise_health` is deliberately NOT here — it is retired (#3883), so it is
#: absent from `tools/list` and a connector's tool grant cannot contain it. The
#: served skill already says so (`tortoise/onboarding/SKILL.md`, the
#: ADVERTISED-form sentence).
JOURNEY_TOOLS = ("tortoise_overview", "tortoise_create_point",
                 "tortoise_query", "tortoise_list_graphs", "tortoise_get_entity")


# ── the transport, mounted the way production mounts it ────────────────────


def _mounted_test_client(app):
    """Wrap the MCP app in a Starlette Mount at /mcp (mirrors hosted_api).

    The same wrapper `tests/test_mcp_http.py` and `tests/test_9192_...` use: the
    MCP sub-app's routes live at `/`, production mounts it under `/mcp`, and the
    router strips the prefix. A direct ASGI call would bypass the mount and could
    hide a prefix-dependent bug in the very path this file pins.
    """
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


def _sse_json(response):
    """Parse an MCP response body that may be SSE-framed (`event: message`)."""
    text = response.text
    if text.startswith("event:") or "\ndata: " in text:
        for line in text.splitlines():
            if line.startswith("data: "):
                return json.loads(line[len("data: "):])
        return None
    return response.json()


def _call(client, name, arguments, *, request_id=1):
    response = client.post("/mcp", json={
        "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    body = _sse_json(response)
    assert body is not None, f"no JSON-RPC body for {name}: {response.text[:600]}"
    return response, body


def _tool_payload(body):
    """The structured result of a `tools/call`, whatever envelope carried it."""
    result = body.get("result") or {}
    if "structuredContent" in result:
        return result["structuredContent"]
    for block in result.get("content") or []:
        if block.get("type") == "text":
            try:
                return json.loads(block["text"])
            except json.JSONDecodeError:
                continue
    return result


def _assert_call_succeeded(body, what):
    """A `tools/call` DID what it was asked, or this reds naming the envelope.

    ⛔ NOT `assert "error" not in _tool_payload(body)`: that spelling is true
    for both failure envelopes this transport produces, so it is not evidence.

    * a **JSON-RPC protocol error** is `{"jsonrpc", "id", "error": {...}}` — no
      `result` at all, so `_tool_payload` returns `{}` and `"error" not in {}`
      passes;
    * a **tool error** is `result: {"content": [...prose...], "isError": true}` —
      produced when a `ToolError`/exception is RAISED inside the tool and FastMCP
      converts it to `CallToolResult(isError=True)`. The flag is spelled
      `isError`, never `error`, and a non-JSON text block falls through
      `_tool_payload` to the raw result.

    ⚠️ `mcp_server._safe` deliberately produces the OTHER shape — an ORDINARY
    successful result carrying `{"error": …}` (`_SafeError`), `isError` false.
    Pairing the two producers is the mistake `_SafeError`'s own module docstring
    spends its length keeping apart ("two separate questions, two separate
    gates"), and it is why the verify step's payload check below cannot rely on
    the absence of `error`: only POSITIVE evidence separates the two.

    So both checks are made on the RAW body, where the envelopes actually are.
    """
    assert "error" not in body, (
        f"{what} failed at the JSON-RPC layer: {json.dumps(body)[:600]}")
    result = body.get("result") or {}
    assert result.get("isError") is not True, (
        f"{what} returned a tool ERROR: {json.dumps(result)[:800]}")


def _assert_healthy_verify_payload(payload):
    """The journey's verify step really measured a healthy graph.

    ⛔ NOT `"error" not in payload` ALONE: `_SafeError` — a
    dict subclass whose only key is `error`, returned as an ORDINARY successful
    `tools/call` result and never as `isError` (`mcp_server._safe`: "every tool
    returns the SDK result on success or `{"error": message}` on failure") — is
    the shape a RAISED exception in `tortoise_health()` produces. Neither
    `isError` nor a JSON-RPC `error` key accompanies it, and an empty/absent
    result carries no `error` either, so the check has to demand POSITIVE evidence.

    The positive term is `monitoring.metrics()`'s own success verdict —
    `status == "ok"` — set only when the db probe observed success
    (`monitoring.py`, the `db["ok"] is True` branch). It is absent from the
    `_SafeError`/empty shapes entirely, and it is `degraded`/`unknown` for a probe
    failure, so one assertion closes every failure shape.

    ⚠️ TWO checks were considered and are deliberately NOT here:

    * `db["ok"] is not False` — `status` is DERIVED from `db["ok"]`
      (`monitoring.py`), so it can never fail once `status == "ok"` is asserted.
      A check with no failure behind it.
    * `graph_size_error is None` — real (a count overrun leaves
      `status == "ok"` with the marker set), but it reads evidence this leg never
      consumes: the file asserts no `graph_size`, and the marker fires on a
      HEALTHY graph whose taxonomy count overran `GRAPH_SIZE_TIMEOUT` — the
      #3253/#3143 false-degrade class. Demanding it would make a required job red
      on a slow count that has nothing to do with whether the connector reached
      the record, which is this leg's subject; the count has its own coverage.
    """
    assert payload.get("status") == "ok", (
        "the journey's verify step did not report a measured-healthy graph — "
        "`status` is `ok` only when the probe observed success, so a degraded, "
        "unknown, empty or `_SafeError` payload cannot pass here: "
        f"{json.dumps(payload)[:600]}")


# ── the record's database, provisioned the way the CI job provisions it ────


def _database(monkeypatch):
    """ARM the record, and hand back a connection that can assert on it.

    ⛔ ARMING IS A SEPARATE ACT FROM HAVING A DATABASE, and this file exists partly
    because the two variables are independent — `TEST_RECORD_DB_URI` set and
    `TORTOISE_RECORD_DSN` unset is a reachable state: an UNARMED deployment is
    byte-identical by design — `write_live_record`
    returns `None` with no row, no counter and no warning — so the journey completed
    and every record assertion simply found nothing. That is the same silent shape
    the seam's own "armed but unreachable FAILS" decision exists to keep apart, one
    level up: the test has to PROVE it is measuring an armed record, not assume it.
    The caller asserts the seam's own view (`RecordWriter().armed`) before calling,
    so an unarmed lane is a diagnosis, not a mystery.

    The schema application and the tenancy seed are IMPORTED from
    `tests/test_9078_record_written_by_production_path.py` rather than copied: a
    second `_ensure_record_schema` would drift, and the copy that drifts passes
    on a stale schema while the real one reds — the exact silent-allowance class
    that file exists to delete.
    """
    dsn = os.environ.get(RECORD_DB_ENV)
    if not dsn:
        pytest.skip(f"{RECORD_DB_ENV} not set — no Postgres, so no record to prove")
    psycopg2 = pytest.importorskip(
        "psycopg2", reason="psycopg2-binary is the record's driver ([postgres] extra)")
    from tests.test_9078_record_written_by_production_path import (
        _app_role_dsn,
        _ensure_record_schema,
        _seed_tenant,
    )
    from tortoise import record_writer
    # The WRITER connects as the record's app role (R6: NOBYPASSRLS), exactly as
    # `test_9078` arms it; the provisioning superuser stays on this connection for
    # seeding and assertions.
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, _app_role_dsn(dsn))
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    _ensure_record_schema(conn)
    return conn, _seed_tenant


# ── one tenant, one connector, one process ─────────────────────────────────


@pytest.fixture
def connector(tmp_path, monkeypatch):
    """A real MCP app whose graph-bound credential resolves to a RECORD tenant.

    Every piece is the production object: the HTTP app (`create_http_app`), the
    tenancy middleware's own resolver (`record_tenant_for`), the record seam
    (`write_live_record`). The two stubs stand in only for the CONTROL PLANE
    (`resolve_api_key`), because the record's layer tables FK to
    `public.organizations`/`public.graphs` and a registry-shaped `g_<hex>` id is
    a different id space (#9192: "a registry graph id is never installed").
    """
    from tortoise import supabase_control as sc
    from tortoise.mcp_server import create_http_app
    from tortoise.sdk import TortoiseSDK

    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "registry.db"))
    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", str(tmp_path / "events"))

    org_id = f"org-{uuid.uuid4().hex[:10]}"
    graph_id = str(uuid.uuid4())
    api_key = "tk_capstone_connector"
    # The ORGS ROW is the limits dict in supabase mode (`mcp_auth`:
    # `limits = org`), and a MISSING limit key is fail-closed (#310 GAP-B), so
    # the row carries every `_RESOURCE_LIMIT_KEYS` value. `max_sessions: None` is
    # the recorded "unlimited" spelling, not an omission.
    org_row = {
        "org_id": org_id, "graph_id": graph_id,
        "graph_namespace": f"org_{org_id}_{graph_id}",
        "delegation_depth": None,
        "scopes": ["graphs:read", "graphs:write"],
        "legacy_full_access": True, "created_by": None, "suspended_at": None,
        "plan": "pro",
        "max_points": 10_000, "max_api_keys": 10, "max_sessions": None,
        "max_users": 10, "max_graphs": 10,
    }
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(sc, "get_control_plane", lambda: None)
    monkeypatch.setattr(sc, "resolve_api_key",
                        lambda _cp, token: org_row if token == api_key else None)

    registry = TortoiseSDK(db_path=str(tmp_path / "registry.db"),
                           namespace="registry")
    app = create_http_app(allowed_origins=["https://app.premiselabs.co"],
                          _registry_sdk=registry)
    client = _mounted_test_client(app)
    client.__enter__()  # the MCP lifespan (session manager) lives in the context
    try:
        yield client, api_key, (org_id, graph_id)
    finally:
        client.__exit__(None, None, None)
        registry.close()


def _authenticated(client, api_key):
    client.headers["Authorization"] = f"Bearer {api_key}"


def _rows(conn, tenant, table, *, columns="count(*)"):
    org_id, graph_id = tenant
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {columns} FROM public.{table} WHERE org_id=%s AND graph_id=%s",
            (org_id, graph_id))
        return cur.fetchone()


def _tenant_parents(conn, tenant) -> tuple[int, int]:
    """The two PARENT rows every recorded layer write foreign-keys to.

    `(organizations, graphs)`, as counts for this exact pair. Without them a
    zero-row assertion is **true by construction**, not evidence: `node_identity`
    FK-references `public.graphs (org_id, id)`
    (`20261012000001_record_event_stream.sql:70`) and `public.point`
    FK-references `node_identity` (`20261012000003_record_epistemic.sql:119`), so
    an unseeded tenant cannot hold a row no matter what the code under test does.
    Measured on a throwaway `postgres:16`: inserting a `node_identity` row for an
    UNSEEDED pair raises `ForeignKeyViolation` on `node_identity_org_id_fkey`;
    the same insert after `_seed_tenant` succeeds. Asserting the parents exist is
    what separates "the refusal held" from "nothing could have been written".
    """
    org_id, graph_id = tenant
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM public.organizations WHERE id=%s",
                    (org_id,))
        orgs = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM public.graphs WHERE id=%s AND org_id=%s",
            (graph_id, org_id))
        graphs = cur.fetchone()[0]
    return orgs, graphs


# ── 1. the surface the client is CONSTRUCTED from ──────────────────────────


def test_the_connector_advertises_the_journey_tools_and_nothing_retired(
        connector):
    """`tools/list` is the grant: what is absent here, the model cannot call.

    A tool-grant client builds its allowlist from `tools/list` alone
    (`tests/test_onboarding_variants.py::test_9660_chatgpt_first_job_names_only_ADVERTISED_tools`
    is the same rule applied to the copy). Two properties are asserted, and the
    second is the one that keeps this leg from decaying:

    * every tool the served journey instructs the model to call is advertised;
    * the advertised set is EXACTLY the registry's HTTP surface — so a retired
      entry cannot be advertised by accident (it would hand the model a name the
      shim only answers on `get_tool`), and the surface cannot quietly SHRINK
      below what the registry declares.
    """
    from tortoise import tool_registry as tr

    client, api_key, _tenant = connector
    _authenticated(client, api_key)

    response = client.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "capstone", "version": "1"}},
    })
    assert response.status_code == 200, response.text[:600]

    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2,
                                         "method": "tools/list"})
    body = _sse_json(response)
    advertised = [tool["name"] for tool in body["result"]["tools"]]

    expected = {t.name for t in tr.TOOL_REGISTRY if t.http_policy}
    retired = {t.name for t in tr.RETIRED_TOOL_REGISTRY}
    assert set(advertised) == expected, (
        "the advertised surface is not the registry's HTTP surface — missing "
        f"{sorted(expected - set(advertised))}, extra "
        f"{sorted(set(advertised) - expected)}")
    assert not (set(advertised) & retired), (
        f"a RETIRED tool is advertised: {sorted(set(advertised) & retired)}")
    missing = [name for name in JOURNEY_TOOLS if name not in advertised]
    assert not missing, (
        f"the served onboarding journey calls {missing}, which this connector "
        "does not advertise — a tool-grant client cannot make that call")


# ── 2. the failure path, before the happy one ──────────────────────────────

#: The payload this leg sends, kept as ONE literal because it is also the
#: tenant-agnostic probe below: a row carrying this exact content was written by
#: an unauthenticated call, wherever it landed.
_UNAUTHENTICATED_CONTENT = "unauthenticated must not land"


def test_an_unauthenticated_connector_gets_a_legible_refusal(connector, monkeypatch):
    """No credential ⇒ a refusal that names the fix, and NOTHING recorded.

    A capstone that only proves the happy path is the defect class this repo has
    paid for (#2982 empty payload, #2581 host-dependent verdict). The assertions
    are on the three things that separate a refusal from a silent success: the
    status, a machine-readable body a client can surface, and the record
    itself — an unauthenticated call must not leave a row behind.

    ⛔ THE RECORD HALF IS ONLY EVIDENCE IF A WRITE COULD HAVE LANDED, so the two
    preconditions for that are asserted, not assumed:

    * the tenant is SEEDED. Without `public.organizations`/`public.graphs` the
      layer tables FK-refuse every insert, so `count(*) == 0` would be true by
      construction — it could not tell "the refusal held" from "nothing could
      ever be written". `_tenant_parents` states that the parents are there;
    * the seam is ARMED (`RecordWriter().armed`), the same precondition the
      capstone leg asserts. An unarmed seam writes nothing, counts nothing and
      warns about nothing, so it too makes a zero indistinguishable from a
      correct refusal — see `_database`'s docstring.

    All three layer tables the fold touches are then asserted empty, because
    `point` alone would miss a partial write that stopped at the identity row.

    ⛔ THE TENANT-SCOPED COUNTS ARE NOT THE WHOLE RECORD HALF (P3, PR #9852
    review): they only see a row under the tenant THIS fixture resolved, so a
    regressed tenancy resolver that installed a different (default/static)
    tenant would write happily and leave all three at 0. Two further assertions
    close that: a GLOBAL count on this leg's own unique payload, and an unmoved
    `record_write_failure_counts()` delta — which is also what separates "auth
    refused before the tool ran" from "the tool ran and the seam refused it for
    want of a tenant (`no_tenant`)".
    """
    from tortoise import monitoring, record_writer

    client, _api_key, tenant = connector
    conn, seed_tenant = _database(monkeypatch)
    seed_tenant(conn, tenant)
    assert record_writer.RecordWriter().armed, (
        "the record seam is NOT armed, so an empty table below would be about an "
        "unarmed deployment rather than about the refusal: check "
        f"{record_writer.RECORD_DSN_ENV}")
    before = dict(monitoring.record_write_failure_counts())
    try:
        response = client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "tortoise_create_point",
                       "arguments": {"kind": "statement",
                                     "content": _UNAUTHENTICATED_CONTENT}},
        })
        after = dict(monitoring.record_write_failure_counts())
        assert response.status_code == 401, (
            f"an unauthenticated connector call returned {response.status_code}, "
            f"not a refusal: {response.text[:400]}")
        body = response.json()
        message = (body.get("error") or {}).get("message") or ""
        assert "Unauthorized" in message, (
            f"the refusal does not say what is wrong: {response.text[:400]}")
        assert "Bearer" in message, (
            "the refusal does not name the credential shape, so a client cannot "
            f"act on it: {message!r}")
        assert "Traceback" not in response.text, (
            "an unauthenticated client received a stack trace, not a refusal")
        assert "tortoise_create_point" not in response.text, (
            "the refusal leaked the tool inventory to an unauthenticated caller")
        parents = _tenant_parents(conn, tenant)
        assert parents == (1, 1), (
            "the tenant's parent rows are not in place, so the zero counts below "
            "are TRUE BY CONSTRUCTION (the layer tables FK to "
            f"organizations/graphs) rather than evidence: {parents!r}")
        for table in ("point", "node_identity", "journal_event"):
            assert _rows(conn, tenant, table)[0] == 0, (
                f"an unauthenticated call wrote a {table} row into the record")
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM public.point WHERE content=%s",
                        (_UNAUTHENTICATED_CONTENT,))
            elsewhere = cur.fetchone()[0]
        assert elsewhere == 0, (
            "an unauthenticated call filed a Point into the record under SOME "
            "tenant — possibly not this one, e.g. if a regressed resolver "
            "installed a default tenant, which the tenant-scoped counts above "
            "cannot see")
        moved = {key: after.get(key, 0) - before.get(key, 0)
                 for key in set(after) | set(before)}
        moved = {key: value for key, value in moved.items() if value}
        assert not moved, (
            "the record seam recorded a write FAILURE, so the unauthorised call "
            f"reached the write path and was refused there instead of at the "
            f"credential: counters moved {moved}")
    finally:
        conn.close()


# ── 3. THE CAPSTONE: one onboarding-shaped call reaches the record ─────────


def test_the_verify_step_assertions_reject_the_failure_envelopes_they_name():
    """Non-vacuity for the checks that replaced `assert "error" not in payload`.

    A failed `tools/call` arrives in one of two envelopes and NEITHER carries an
    `error` key where the old assertion looked: a JSON-RPC protocol error has no
    `result` (so `_tool_payload` yields `{}`), and a tool error puts `isError`
    inside `result`. A degraded or unmeasured health report likewise has no
    `error` key at all. Each is fed to the real check here and must be REFUSED;
    the healthy report must pass, so this cannot be satisfied by a check that
    always raises.

    Mutation check: restore `assert "error" not in _tool_payload(body)` for the
    call check → the FIRST TWO `pytest.raises` fail (the two call envelopes);
    the payload-check raises that follow are governed by
    `_assert_healthy_verify_payload` and are unaffected by that mutation — they
    are proven by the `_SafeError`/empty/absent cases below. The POSITIVE cases
    matter as much as the raises: a check that always fails, or one that rejects
    the `_SafeError`/empty shapes only by accident, would otherwise satisfy this
    test.
    """
    protocol_error = {"jsonrpc": "2.0", "id": 1,
                      "error": {"code": -32603, "message": "boom"}}
    assert _tool_payload(protocol_error) == {}, (
        "the payload helper must see nothing in a protocol error — that is why "
        "the check has to read the body")
    with pytest.raises(AssertionError):
        _assert_call_succeeded(protocol_error, "the journey's verify step")

    tool_error = {"jsonrpc": "2.0", "id": 1,
                  "result": {"content": [{"type": "text",
                                            "text": "graph unreachable"}],
                             "isError": True}}
    assert _tool_payload(tool_error) == {
        "content": [{"type": "text", "text": "graph unreachable"}],
        "isError": True}, "a tool error's flag must not be reachable as `error`"
    with pytest.raises(AssertionError):
        _assert_call_succeeded(tool_error, "the journey's verify step")

    # the POSITIVE half, so "the check always raises" cannot satisfy this test
    _assert_call_succeeded({"jsonrpc": "2.0", "id": 1,
                            "result": {"content": [], "isError": False}},
                           "a healthy call")

    # ⛔ the failure shapes that carry neither an `error` key nor `isError`:
    # `_SafeError` is a dict with only `error`, returned as a SUCCESSFUL result;
    # an empty result and a missing result carry no `error` key at all.
    safe_error = {"error": "Authentication required. MCP transport mode not "
                           "initialized."}
    with pytest.raises(AssertionError):
        _assert_healthy_verify_payload(safe_error)
    with pytest.raises(AssertionError):
        _assert_healthy_verify_payload({})
    with pytest.raises(AssertionError):
        _assert_healthy_verify_payload({"content": []})

    degraded = {"status": "degraded", "db": {"ok": False}, "graph_size": 0,
                "graph_size_error": "graph_size_unavailable: db probe failed"}
    with pytest.raises(AssertionError):
        _assert_healthy_verify_payload(degraded)

    unmeasured = {"status": "unknown", "db": {"ok": None}, "graph_size": 0,
                  "graph_size_error": "graph_size_unavailable: timed out"}
    with pytest.raises(AssertionError):
        _assert_healthy_verify_payload(unmeasured)

    # `status: degraded` with a MEASURED size is still a failure (the db probe
    # failed for a reason that is not the count) — the status term is load-bearing.
    measured_but_degraded = {"status": "degraded", "db": {"ok": False},
                             "graph_size": 7, "graph_size_error": None}
    with pytest.raises(AssertionError):
        _assert_healthy_verify_payload(measured_but_degraded)

    healthy = {"status": "ok", "db": {"ok": True}, "graph_size": 0,
               "graph_size_error": None}
    _assert_healthy_verify_payload(healthy)


def test_one_onboarding_call_reaches_the_record_and_its_id_reads_back(
        connector, monkeypatch):
    """The journey's first memory, end to end: connector → graph → RECORD → id.

    Driven exactly as the served first job instructs: verify with the ADVERTISED
    health section, file the first memory with `tortoise_create_point`, then read
    it back by the id the client was handed. The record half is asserted on ROWS
    under the REQUEST's tenant, because the acceptance sentence for the whole
    epic is *"a user's Points … exist in Postgres"* and nothing else answers it.

    Why each assertion is here rather than "the call returned 200":

    * the **rows** make a silent record loss visible. `write_live_record` counts
      and WARNS instead of raising (the journal already holds the record), so a
      broken seam still returns a perfectly good Point to the client — a 200 is
      not evidence that the record happened;
    * the **failure counter** is asserted unchanged for the same reason: it is
      the only surface on which "configured but the write did not land" is
      distinguishable from "landed";
    * the **identifier** is asserted to be the record's own `node_key`, and then
      used again through a second call — an id that reads nothing back is not an
      identifier a user can reuse, which is what step 3 of the capstone asks for;
    * the **content** is asserted, so a row that exists with the wrong payload
      cannot pass as a record.
    """
    from tortoise import monitoring, record_writer

    client, api_key, tenant = connector
    conn, seed_tenant = _database(monkeypatch)
    seed_tenant(conn, tenant)
    assert record_writer.RecordWriter().armed, (
        "the record seam is NOT armed, so every assertion below would be about an "
        "empty database rather than about the journey: check "
        f"{record_writer.RECORD_DSN_ENV}")
    try:
        _authenticated(client, api_key)

        # The served first job: verify through the ADVERTISED health section.
        _status, health = _call(client, "tortoise_overview", {"section": "health"})
        _assert_call_succeeded(health, "the journey's verify step")
        _assert_healthy_verify_payload(_tool_payload(health))

        before = dict(monitoring.record_write_failure_counts())
        content = (f"#9664 capstone: this Point came through the connector "
                   f"({uuid.uuid4().hex[:8]})")
        response, body = _call(client, "tortoise_create_point",
                               {"kind": "statement", "content": content})
        after = dict(monitoring.record_write_failure_counts())
        assert response.status_code == 200, response.text[:600]
        _assert_call_succeeded(body, "filing the first memory")

        payload = _tool_payload(body)
        point_id = payload.get("id")
        assert point_id, f"the tool returned no reusable identifier: {payload!r}"

        lost = {key: after.get(key, 0) - before.get(key, 0)
                for key in set(after) | set(before)}
        lost = {key: value for key, value in lost.items() if value}
        assert not lost, (
            "the record write did not land even though the tool reported success "
            f"— counters moved {lost}. This is the silent-loss shape: the client "
            "got a Point, the record got nothing, and only this counter says so")

        # ── the record: layer 1 (position), and the Point the user filed ──
        assert _rows(conn, tenant, "journal_event")[0] >= 1, (
            "no journal_event row — layer 1 (the position the append reported) "
            "did not reach the record, so a replay would re-number this write")
        assert _rows(conn, tenant, "node_identity")[0] == 1, (
            "no node_identity row for the filed Point")
        with conn.cursor() as cur:
            cur.execute(
                "SELECT node_key, content FROM public.point "
                "WHERE org_id=%s AND graph_id=%s", tenant)
            rows = cur.fetchall()
        assert len(rows) == 1, (
            f"expected exactly the one Point this call filed, found {rows!r} — a "
            "row count above 1 means this request filed more than one Point")
        recorded_key, recorded_content = rows[0]
        assert recorded_key == point_id, (
            f"the identifier handed to the client ({point_id!r}) is not the "
            f"record's key ({recorded_key!r}) — the id cannot be used to read "
            "the record back")
        assert recorded_content == content, (
            f"the record holds different content ({recorded_content!r}) than the "
            f"user filed ({content!r})")

        # ── the identifier is REUSABLE, through the advertised read tool ──
        response, body = _call(client, "tortoise_get_entity",
                               {"id": point_id, "type": "point"}, request_id=4)
        assert response.status_code == 200, response.text[:600]
        _assert_call_succeeded(body, f"reading {point_id!r} back")
        read_back = _tool_payload(body)
        assert read_back.get("id") == point_id, (
            "the read-back payload does not carry the identifier the client was "
            f"handed ({point_id!r}) — got {read_back.get('id')!r}. No `.get` "
            "default here: with one, an absent `id` would satisfy the equality "
            "by comparing the fallback to itself")
        assert read_back.get("content") == content, (
            f"reading {point_id!r} back returned {read_back.get('content')!r}, "
            f"not what was filed ({content!r})")
    finally:
        conn.close()


# ── the same rule, applied to the DESCRIPTIONS a model reads ───────────────

#: LIVE registry tools whose `description` recommends a RETIRED name.
#:
#: Measured at `dced2b29b`, BY WHOLE TOKEN: **2 tools, 3 names**. "Live" is
#: `TOOL_REGISTRY` membership — NOT `http_policy`, and the two are not the same
#: surface. `tortoise_index_files` carries
#: `http_policy=False` (`tortoise/tool_registry.py`, "EXCLUDED from tenant HTTP"
#: in its own description), so it is absent from a connector's `tools/list` and
#: the CONNECTOR-facing half of this defect is `tortoise_query` alone
#: — `tortoise_get_point` is the RETIRED spelling of the read the journey's
#: "read it back by id" step performs, which the journey makes with
#: `tortoise_get_entity(id, type="point")` (the registry's own
#: `RETIRED_USE_INSTEAD` entry says so).
#:
#: ⛔ ONE NAME, NOT TWO, FOR `tortoise_query`. A dict that pins
#: `("tortoise_get", "tortoise_get_point")` because the matcher was
#: `name in description` is wrong — and `tortoise_get` is a strict PREFIX
#: of `tortoise_get_point`, so it was counted as "named" without being present.
#: That is a measurement artifact, not a fact, and it had a second consequence:
#: `tortoise_get` is ALSO a prefix of `tortoise_get_entity`, the live canonical
#: read tool (`RETIRED_USE_INSTEAD`'s own value, and explicitly NOT retired at
#: `tool_registry.py:1854-1861`), so the matcher reported the CORRECT copy as
#: stale and the equality pins below would have told the fixer "do not widen
#: this pin" while the xfail stayed armed forever — the fix could not clear the
#: flag that was supposed to delete itself. The matcher now reads WHOLE tool
#: tokens (`_retired_names_in_text`), and
#: `test_the_prescribed_fix_clears_the_stale_description_flag` proves that
#: applying `RETIRED_USE_INSTEAD` empties this measurement.
#:
#: The stdio-only half is still measured, because the rule is about the text a
#: MODEL reads: a stdio client receives the same `description` and the retired
#: names are absent from ITS `tools/list` too, so the property holds for both
#: surfaces. What must never drift is the CLAIM — hence
#: `test_the_stale_description_set_says_which_half_is_the_connectors`, which pins
#: which side of `http_policy` each measured tool sits on.
_RETIRED_NAMES_NAMED_BY_LIVE_DESCRIPTIONS = {
    "tortoise_query": ("tortoise_get_point",),
    "tortoise_index_files": ("tortoise_index_sessions", "tortoise_ingest_corpus"),
}

#: A tool name as it is spelled in prose: `tortoise_` + lowercase/underscore/digit.
#: Greedy on purpose — `tortoise_get_point` and `tortoise_get_entity` are two
#: DIFFERENT tokens, and a prefix of either must never be read as a name of its
#: own (`tortoise_get` is the retired spelling AND the prefix of the live one).
_TOOL_NAME_TOKEN = re.compile(r"tortoise_[a-z0-9_]+")


def _retired_names_in_text(text, retired_names):
    """The RETIRED tool names this text NAMES — whole tokens, never substrings.

    ⛔ A substring test is the wrong instrument for "this description recommends
    tool X": `"tortoise_get" in "...tortoise_get_point"`
    is `True`, so every prefix relationship between a retired name and a longer
    name (live or retired) manufactures a hit. Tokenising the prose and
    intersecting with the retired set answers the question that was asked.

    Every `tortoise_*` token in the text is a candidate, so a retired name is
    still found inside prose, punctuation, or a `name(...)` call form.
    """
    return tuple(sorted(set(_TOOL_NAME_TOKEN.findall(text or ""))
                        & set(retired_names)))


def _retired_names_named_by_live_descriptions() -> dict[str, tuple[str, ...]]:
    """Measured, not asserted from a date: which LIVE descriptions are stale.

    Every `TOOL_REGISTRY` entry — advertised on HTTP (`http_policy`) **or**
    stdio-only, because the defect is a description that names a tool its own
    reader cannot call, and a stdio client's `tools/list` omits the retired name
    just as a connector's does. Names are matched as WHOLE TOKENS
    (`_retired_names_in_text`), not as substrings.
    """
    from tortoise import tool_registry as tr

    retired = {t.name for t in tr.RETIRED_TOOL_REGISTRY}
    stale: dict[str, tuple[str, ...]] = {}
    for tool in tr.TOOL_REGISTRY:
        named = _retired_names_in_text(tool.description, retired)
        if named:
            stale[tool.name] = named
    return stale


def test_the_retired_name_matcher_reads_whole_tool_names_not_substrings():
    """`tortoise_get` is a PREFIX of two different tools, one of them LIVE.

    The measurement this file publishes is "these descriptions NAME a retired
    tool", and a substring test answers a different question. `tortoise_get` is
    retired, `tortoise_get_point` is retired, and `tortoise_get_entity` is the
    LIVE canonical read tool (`RETIRED_USE_INSTEAD`'s own value, and the owner
    ruling at `tool_registry.py:1854-1861` says it must NOT be retired). A
    substring matcher reports all three as hits on any one of them.

    Mutation check: restore `name in (tool.description or "")` in
    `_retired_names_in_text` → the first assertion reds (the live
    `tortoise_get_entity` is reported as naming retired `tortoise_get`) and the
    second reports two names where one is written. Measured, not assumed.

    The last assertion pins the INSTRUMENT: a name whose spelling falls outside
    `tortoise_[a-z0-9_]+` would be invisible to the matcher, i.e. a stale
    description naming it would measure CLEAN without having been looked at.
    Every name in both registries matches today; this reds
    on the first one that does not, so the matcher is re-examined instead of
    silently under-reporting.
    """
    from tortoise import tool_registry as tr

    retired = {t.name for t in tr.RETIRED_TOOL_REGISTRY}
    assert all(_TOOL_NAME_TOKEN.fullmatch(t.name)
               for t in (*tr.TOOL_REGISTRY, *tr.RETIRED_TOOL_REGISTRY)), (
        "a registry tool name is not spellable as a `tortoise_*` token, so "
        "`_retired_names_in_text` cannot see it and a stale description naming "
        "it would measure clean: fix the token pattern before trusting the "
        f"measurement. Unmatched: "
        f"{sorted(t.name for t in (*tr.TOOL_REGISTRY, *tr.RETIRED_TOOL_REGISTRY) if not _TOOL_NAME_TOKEN.fullmatch(t.name))!r}")
    assert _retired_names_in_text('use tortoise_get_entity(id, type="point")',
                                  retired) == (), (
        "the LIVE canonical read tool is reported as a retired name — the "
        "matcher is reading prefixes, not names")
    assert _retired_names_in_text(
        "for a single known ID use tortoise_get_point.", retired) == (
            "tortoise_get_point",), (
        "the retired spelling must be reported EXACTLY once, as itself — a "
        "`tortoise_get` entry here is the prefix artifact")
    assert _retired_names_in_text(
        'tortoise_get_point → tortoise_get_entity(id, type="point")',
        retired) == ("tortoise_get_point",), (
        "with a retired name and its live replacement side by side, only the "
        "retired one is a hit")
    assert _retired_names_in_text(None, retired) == (), (
        "a tool with no description cannot be stale")


def test_the_prescribed_fix_clears_the_stale_description_flag():
    """The xfail above claims it DELETE-ITSELF when the copy is fixed. Prove it.

    `RETIRED_USE_INSTEAD` prescribes the replacement for every retired spelling,
    so applying it to each measured description must empty the measurement. A
    substring matcher cannot survive that check: the prescribed replacement for
    `tortoise_get_point` —
    `tortoise_get_entity(id, type="point")` — still CONTAINS `tortoise_get`, so
    the corrected copy measured as stale, the strict xfail stayed armed over a
    fixed defect, and the equality pins told the fixer "do not widen this pin".

    Mutation check: restore the substring matcher → RED on `tortoise_query`.
    """
    from tortoise import tool_registry as tr

    retired = {t.name for t in tr.RETIRED_TOOL_REGISTRY}
    measured = _retired_names_named_by_live_descriptions()
    assert measured, (
        "no measurement to prove against — remove this test together with the "
        "xfail it guards")
    for tool_name, names in measured.items():
        tool = next(t for t in tr.TOOL_REGISTRY if t.name == tool_name)
        fixed = tool.description or ""
        for name in sorted(names, key=len, reverse=True):
            assert name in tr.RETIRED_USE_INSTEAD, (
                f"`RETIRED_USE_INSTEAD` has no replacement for {name!r}, so there "
                "is no prescribed fix for this finding")
            fixed = re.sub(rf"(?<![a-z0-9_]){re.escape(name)}(?![a-z0-9_])",
                           lambda _m, _to=tr.RETIRED_USE_INSTEAD[name]: _to,
                           fixed)
        assert _retired_names_in_text(fixed, retired) == (), (
            f"applying `RETIRED_USE_INSTEAD` to {tool_name!r}'s description still "
            f"measures stale ({_retired_names_in_text(fixed, retired)!r}) — the "
            "prescribed fix cannot clear the marker that exists to clear itself, "
            "so this row would have to be edited by hand forever")


@pytest.mark.xfail(
    condition=_retired_names_named_by_live_descriptions() != {},
    strict=True,
    reason=(
        "MEASURED defect: a tool's `description` is part of the tools/list "
        "payload the model reads, and TWO live tools name THREE RETIRED tools "
        "that are absent from that same payload. `tortoise_query` → "
        "`tortoise_get_point` is the CONNECTOR-facing half "
        "(`tortoise_query` itself IS advertised) and names the RETIRED spelling "
        "of the read that step performs (`tortoise_get_entity(id, "
        "type=\"point\")`), so a tool-grant client is told to "
        "call a tool it was never granted; `tortoise_index_files` → "
        "`tortoise_index_sessions`/`tortoise_ingest_corpus` is stdio-only "
        "(`http_policy=False`), so it mis-instructs a stdio client rather than a "
        "connector. This marker DELETE-ITSELF when the copy is fixed (the "
        "condition is the measured set, not a date); the equality pins below are "
        "what stay loud in the meantime."
    ),
)
def test_a_live_tool_description_never_recommends_a_retired_tool():
    """A description that names a retired tool is a call the client cannot make.

    `tests/test_onboarding_variants.py` already forbids a retired name in the
    ChatGPT *copy*; the registry's own `description` strings are the other half of
    what a model reads, and nothing checked them. This asserts the PROPERTY (no
    stale description anywhere), so it turns green on its own the moment the copy
    is fixed — the strict xfail reds on that day, which is what re-measures the
    marker instead of leaving a permanent exclusion behind.

    "The moment the copy is fixed" means EVERY stale description: while one
    remains the property is still false, so the marker stays armed — correctly.
    What must hold is that the prescribed fix CLEARS a row, which
    `test_the_prescribed_fix_clears_the_stale_description_flag` measures.
    """
    measured = _retired_names_named_by_live_descriptions()
    assert measured == {}, (
        "these live tools recommend a RETIRED tool name in their `description`, "
        f"which a tool-grant client cannot call: {measured!r}")


def test_the_stale_description_set_is_exactly_what_was_measured():
    """The pin that keeps the xfail above from hiding a NEW stale description.

    An allowance keyed on "is the set non-empty" would silently absorb a fourth
    entry. Pinned by EQUALITY instead: the measured set may only change when this
    line changes, and fixing one entry reds it too — so neither a new defect nor a
    fix can land unnoticed. (The #9200 idiom, from `test_9078`.)

    ⚠️ The pin spans `TOOL_REGISTRY`, not the HTTP surface, so an edit to a
    stdio-only tool's `description` reds this too (deliberate: the pin's job is
    to force a re-MEASURE of a stale description, and a description no model can
    follow is a defect on either surface). What it must not do is overstate the
    impact — see the surface-split pin below.
    """
    measured = _retired_names_named_by_live_descriptions()
    assert measured == _RETIRED_NAMES_NAMED_BY_LIVE_DESCRIPTIONS, (
        "the set of live descriptions naming RETIRED tools changed: "
        f"{measured!r} (pinned: {_RETIRED_NAMES_NAMED_BY_LIVE_DESCRIPTIONS!r}). "
        "Fix the description — do not widen this pin silently")


def test_the_stale_description_set_says_which_half_is_the_connectors():
    """The measured set is 2 tools / 3 names, and only ONE tool is advertised.

    The constant and the `xfail` reason above could read as though four
    *advertised* descriptions were stale — a count that would itself be an
    artifact of the substring matcher (`tortoise_get` counted as "named" because
    it prefixes `tortoise_get_point`). It is 2 tools / 3 names, and a ChatGPT-style
    tool-grant client is mis-instructed once. `tortoise_index_files`
    carries `http_policy=False` (`tortoise/tool_registry.py`), and
    `HTTP_ALLOWED` is derived from exactly that flag (`get_http_allowed` in
    `tortoise/tool_registry.py`, bound as `HTTP_ALLOWED` in
    `tortoise/mcp_auth.py`), so by this file's OWN definition of advertised
    (`test_the_connector_advertises_the_journey_tools_and_nothing_retired`:
    `{t.name for t in TOOL_REGISTRY if t.http_policy}`) only `tortoise_query`
    reaches the connector. The impact claim is therefore pinned mechanically
    rather than left in prose that the next lane would read as measurement.

    Both halves are asserted by equality, so a tool changing `http_policy` (or a
    third stale description appearing on either side) forces a re-measure instead
    of silently re-wording the claim.
    """
    from tortoise import tool_registry as tr

    advertised = {t.name for t in tr.TOOL_REGISTRY if t.http_policy}
    measured = _retired_names_named_by_live_descriptions()
    http_facing = {name: names for name, names in measured.items()
                   if name in advertised}
    stdio_only = sorted(set(measured) - advertised)
    assert http_facing == {"tortoise_query": ("tortoise_get_point",)}, (
        "the CONNECTOR-facing half of the stale-description defect changed — "
        f"advertised stale tools: {http_facing!r}. Only an HTTP-advertised tool's "
        "description can mis-instruct a tool-grant client; re-measure before "
        "re-wording the xfail reason")
    assert stdio_only == ["tortoise_index_files"], (
        "which stale descriptions belong to stdio-only tools changed: "
        f"{stdio_only!r}. Those are real defects for a stdio client, but they "
        "cannot reach a connector — keep both facts in the claim")


# ── the wiring: this file must RUN where the record is armed ───────────────


def test_ci_runs_this_file_in_the_armed_record_job():
    """A capstone that no job runs is prose with extra steps.

    The record legs only arm in `test-record-postgres` (#9551 provisions the
    database there and asserts zero skips); everywhere else this file SKIPS by
    name. So the job's `run:` must name this file — removing it must not leave a
    green job, which is precisely the #9551 defect one level up. Pinned
    structurally, the same way `test_9078` pins its own job, because a `run:`
    string cannot be executed.
    """
    import yaml

    workflow = (Path(__file__).resolve().parent.parent
                / ".github" / "workflows" / "python-ci.yml")
    jobs = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]
    job = jobs.get("test-record-postgres")
    assert job is not None, (
        "`test-record-postgres` is gone — that job is the only place the record "
        "is armed, so this file would never run armed anywhere (#9551)")
    runs = "\n".join(str(step.get("run", "")) for step in job.get("steps") or [])
    assert Path(__file__).name in runs, (
        f"`test-record-postgres` does not run {Path(__file__).name}: the capstone "
        "legs would skip in every other job and the job would stay green")
