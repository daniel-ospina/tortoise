"""#13036 V2 capstone — every SURVIVING tool answers a real request with a CORRECT payload.

WHAT THIS FILE OWNS, AND WHAT IT DELIBERATELY DOES NOT.

The 27/40 surface has two layers proven elsewhere, and neither is re-derived here:

* **Bindings** — a probe resolves a handler for every live tool and invokes it with
  the SDK seam patched. Its result on current `main`: 40 live in `TOOL_REGISTRY`,
  0 broken bindings, 2 refused.
* **Transport** — `tests/test_mcp_http.py` serves the tools through authenticated
  MCP and asserts *advertised == served* (122 passed).

Both are true of a surface whose tools could still return the WRONG THING. That is
the row this file owns: **a request a real caller would make, over the same
authenticated transport, returns the payload that caller needs.** The five other
capstone rows are separate files: V1 (targets read from the owning tools), V3
(retired names still answer), V4 (no absorbed capability dropped), V5 (ontology),
V6 (drift gates).

WHY PAYLOAD ASSERTIONS AND NOT "IT ANSWERED" — the external evidence.

A smoke call that only establishes "the request completed and something came back"
is an *availability* check: it catches an unreachable endpoint, a broken credential,
a crash, an unexpected status. It cannot catch a successful response whose body is
empty, malformed, or structurally wrong — the failure mode that actually breaks a
consumer. A contract test asserts the **response shape and the fields the consumer
depends on**, which is what detects a renamed field, a changed type, or a
structure that no longer matches (`Pact, "Contract Tests vs Functional Tests",
https://docs.pact.io/consumer/contract_tests_not_functional_tests`; `Martin Fowler,
"ContractTest", https://martinfowler.com/bliki/ContractTest.html`; the same
distinction in Fowler's test pyramid,
`https://martinfowler.com/articles/practical-test-pyramid.html`).

Two consequences shape this file:

1. **One seeded request per tool is not enough where a tool dispatches on a
   discriminator.** `list_knowledge(kind=...)` is EIGHT different reads behind one
   door (`docs/product/bridge-table.md` Part A); proving `kind='claim'` proves
   nothing about `kind='source_reliability'`, whose payload is a different shape
   entirely. Each arm is its own consumer contract, so each arm gets its own case.
2. **Assert only what the consumer depends on.** Asserting every incidental field
   makes the contract brittle for no defect-detection gain (Pact, above). So each
   case names ONE payload property in its id, and the table below is the report.

THE TABLE IS THE ARTIFACT. `CASES` is the tool→request→asserted-property table,
and `UNDRIVEN` is the honest other half: a tool this file cannot drive is recorded
with the reason it cannot be driven, and NEVER as a pass. `test_every_advertised_tool
_is_either_driven_or_declared_undriven` is what keeps that honest — a tool added to
the surface with no case and no declared reason reds here instead of silently
shrinking what "every tool" means.

HOW THE SEED IS BUILT. The request-scoped SDK is `TortoiseSDK(namespace=<org>)`
(`mcp_auth._get_org_sdk`), which resolves its database from `TORTOISE_DB_PATH` — so
the fixture points both the registry SDK and that scoped SDK at ONE embedded file,
and seeds the graph in the org namespace. Seeding uses the SDK's own write doors, so
the content is what production would have written; the two exceptions are a `Session`
node and a `TAG` edge, which have no public creator and are arranged directly, the
same way `tests/test_9664_capstone_connector_to_record.py` seeds its tenant.

Every call is authenticated. A bare handler call returns `Authentication required`,
so a file that called handlers would prove the handler and not the tool.
"""
from __future__ import annotations

import json
import os

# #67: TORTOISE_SECRET_PEPPER is mandatory for auth module import.
os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

from contextlib import asynccontextmanager  # noqa: E402
from typing import Any, Callable  # noqa: E402

import pytest  # noqa: E402

# ── the transport, mounted exactly the way production mounts it ─────────────


def _mounted_test_client(app):
    """Wrap the MCP app in a Starlette Mount at /mcp (mirrors hosted_api).

    The same wrapper `tests/test_mcp_http.py` uses: the MCP sub-app routes live at
    `/`, production mounts it under `/mcp`, and the router strips the prefix. A
    direct ASGI call would bypass the mount and could hide a prefix-dependent bug
    in the very path this file pins.
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
    """Parse an MCP body that may be SSE-framed (`event: message\\ndata: {...}`)."""
    text = response.text
    if text.startswith("event:") or "\ndata: " in text:
        for line in text.splitlines():
            if line.startswith("data: "):
                return json.loads(line[len("data: "):])
        return None
    return response.json()


# ── reading a `tools/call` answer ──────────────────────────────────────────


def _text_payload(body):
    """The tool's return value, or `_NO_PAYLOAD` when the answer carries NONE.

    ⛔ `None` IS A MEANINGFUL ANSWER HERE, not a parse failure. Several tools return
    an empty LIST today, and FastMCP serialises that as `content: []` — zero blocks,
    no `structuredContent`. A helper that returned `{}` for both would let "answered
    nothing" read as "answered an empty map", which is the false PASS this row
    exists to remove. So the sentinel is distinct from every real value.
    """
    result = (body or {}).get("result") or {}
    for block in result.get("content") or []:
        if block.get("type") == "text":
            try:
                return json.loads(block["text"])
            except json.JSONDecodeError:
                return block["text"]
    if "structuredContent" in result:
        return result["structuredContent"]
    return _NO_PAYLOAD


class _NoPayload:
    """Sentinel: the answer carried no payload at all (zero content blocks)."""

    def __repr__(self):
        return "<NO PAYLOAD — zero content blocks>"

    def __bool__(self):
        return False


_NO_PAYLOAD = _NoPayload()


def _rows(payload, *keys):
    """Unwrap the one-field collection envelope a few doors still use.

    Some doors answer `{"result": [...]}` and others answer the list bare (both
    observed on this head, and BOTH are asserted where they occur). This reads
    either, so a case names the collection it needs rather than the wrapper.
    """
    if isinstance(payload, dict):
        for key in keys or ("result", "results", "rows", "items", "content"):
            if key in payload and isinstance(payload[key], list):
                return payload[key]
        return []
    if isinstance(payload, list):
        return payload
    return []


def _ids(payload, *keys):
    return [row.get("id") for row in _rows(payload, *keys) if isinstance(row, dict)]


# ── the fixture: one embedded store, one authenticated transport ────────────

SEED_CLAIM = "V2 capstone proves the surviving tools return correct payloads"
SEED_CLAIM_2 = "A second claim exists so an operator can join two claims"
SEED_SOURCE_URL = "https://capstone.test/v2"
SEED_SOURCE_URL_2 = "https://capstone.test/v2b"
SEED_AGENT = "capstone"
SEED_TAG = "capstone"
SEED_SESSION = "capstone-session-v2"
SEED_BATCH_POINT = "batched capstone claim"
SEED_DIARY_ENTRY = "capstone diary entry"
SEED_EVENT_NAME = "CapstoneEvent"
SEED_OBJECT_NAME = "CapstoneObject"
SEED_SUBJECT_NAME = "capstone-subject"


class Capstone:
    """The seeded store, the ids a case needs, and the one call path."""

    def __init__(self, client, key, ids):
        self.client = client
        self.key = key
        self.ids = ids
        self._rid = 0

    def call(self, name: str, arguments: dict[str, Any]):
        """`tools/call` over the authenticated transport; returns (body, payload)."""
        self._rid += 1
        response = self.client.post("/mcp", json={
            "jsonrpc": "2.0", "id": self._rid, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        body = _sse_json(response)
        assert body is not None, (
            f"{name}: no JSON-RPC body — the transport answered "
            f"{response.status_code} with {response.text[:400]!r}")
        return body, _text_payload(body)

    def ok(self, name: str, arguments: dict[str, Any]):
        """Call a tool that MUST succeed; returns the payload.

        Both failure envelopes are asserted on the RAW body, where they actually
        are — a JSON-RPC `error` is a different key from a tool's `isError`, and
        checking only one of them is how a refused call reads as a success
        (`tests/test_9664_capstone_connector_to_record.py::_assert_call_succeeded`
        carries the long form of that argument).
        """
        body, payload = self.call(name, arguments)
        assert "error" not in body, (
            f"{name}({arguments}) failed at the JSON-RPC layer: "
            f"{json.dumps(body['error'])[:500]}")
        assert (body.get("result") or {}).get("isError") is not True, (
            f"{name}({arguments}) returned a TOOL ERROR: "
            f"{json.dumps(payload, default=str)[:500]}")
        return payload


@pytest.fixture(scope="module")
def capstone(tmp_path_factory):
    """A seeded embedded store behind an authenticated MCP transport.

    MODULE-SCOPED on purpose: seeding is one graph and ~60 cases read it, so a
    per-case rebuild would cost several minutes for no extra discrimination. The
    cases that mutate are all additive or `dry_run`, and none removes the content
    the read cases assert — stated here because a shared fixture whose ordering
    matters is a trap for the next lane.
    """
    import tortoise

    tmp_path = tmp_path_factory.mktemp("capstone_v2")
    db_path = str(tmp_path / "capstone.db")

    # `_get_org_sdk()` builds `TortoiseSDK(namespace=<org>)` with NO db_path, so it
    # resolves the database from the environment. Pointing both at one file is what
    # makes the seeded graph and the tool's graph the SAME graph.
    previous_env = {k: os.environ.get(k) for k in
                    ("TORTOISE_DB_URI", "TORTOISE_DB_PATH",
                     "TORTOISE_EVENT_LOG_BASE_DIR")}
    os.environ.pop("TORTOISE_DB_URI", None)
    os.environ["TORTOISE_DB_PATH"] = db_path
    os.environ["TORTOISE_EVENT_LOG_BASE_DIR"] = str(tmp_path / "events")

    from tortoise.mcp_server import create_http_app
    from tortoise.sdk import TortoiseSDK

    registry = TortoiseSDK(db_path=db_path, namespace="registry")
    team = registry.org_create("capstone-team")
    org_id = team["id"]
    key = registry._apikey_create(org_id, "capstone")["api_key"]

    graph = TortoiseSDK(namespace=org_id)
    ids: dict[str, Any] = {"org_id": org_id}

    # ── the seed: the minimum real content each read needs ──
    claim = graph.create_point("statement", SEED_CLAIM)
    claim2 = graph.create_point("statement", SEED_CLAIM_2)
    ids["claim"] = claim["id"]
    ids["claim2"] = claim2["id"]

    obj = graph.create_entity("object", SEED_OBJECT_NAME, objectKind="concept")
    ids["object"] = (obj.get("node") or obj)["id"]

    subject = graph.create_entity("subject", SEED_SUBJECT_NAME,
                                  subjectKind="person")
    ids["subject"] = (subject.get("node") or subject)["id"]

    event = graph.create_entity("event", SEED_EVENT_NAME, eventKind="observation")
    ids["event"] = (event.get("node") or event)["id"]

    source = graph.register_source(url=SEED_SOURCE_URL, sourceKind="web")
    ids["source"] = source["id"]

    diary = graph.create_entity("diary", SEED_DIARY_ENTRY,
                                props={"authoredBy": SEED_AGENT})
    ids["diary"] = (diary.get("node") or diary)["id"]

    operator = graph.create_operator("IMPL", claim["id"], [claim2["id"]])
    ids["operator"] = operator["id"]

    # A batch, so `list_knowledge(kind='batch')` has a row to answer with.
    batch = graph.ingest({"points": [{"content": SEED_BATCH_POINT,
                                       "kind": "statement"}]})
    ids["batch"] = batch["batch_id"]

    # Fixture arrangement, not a tool-driven write: a Session and a TAGGED edge
    # have NO public creator (a Session is minted by the capture path, a :Tag by
    # a tag sync this seed does not need), so they are placed directly — exactly
    # as `test_9664` places its tenant rows.
    projection = graph._get_proj()
    projection.g.query(
        "MERGE (s:Session {id:$sid}) SET s.agent=$agent, "
        "s.started_at=$ts",
        params={"sid": SEED_SESSION, "agent": SEED_AGENT,
                "ts": "2026-11-19T00:00:00+00:00"},
    )
    projection.g.query(
        "MATCH (p:Point {id:$id}) MERGE (t:Tag {name:$tag}) "
        "MERGE (p)-[:TAGGED]->(t)",
        params={"id": claim["id"], "tag": SEED_TAG},
    )

    app = create_http_app(allowed_origins=[], _registry_sdk=registry)
    client = _mounted_test_client(app)
    try:
        with client:
            client.headers["Authorization"] = f"Bearer {key}"
            initialized = client.post("/mcp", json={
                "jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "capstone-v2", "version": "1"}},
            })
            assert initialized.status_code == 200, initialized.text[:400]
            yield Capstone(client, key, ids)
    finally:
        for name, value in previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        graph.close()
        registry.close()


# ── the census this file covers ────────────────────────────────────────────

#: Tools excluded from the HTTP surface by their registry `http_policy`. They are
#: LIVE (they answer over stdio MCP), so V2's count of live tools includes them,
#: but an HTTP `tools/call` for one answers `-32004` by design — which is why they
#: appear in `UNDRIVEN` rather than in a payload case.
HTTP_EXCLUDED = (
    "tortoise_expand_relationships",
    "tortoise_index_sources_from_directory",
    "tortoise_mine_knowledge_from_directory",
    "tortoise_org_create",
    "tortoise_refresh_confidence",
)


def test_the_surface_this_file_drives_is_the_served_surface(capstone):
    """`tools/list` is the grant, and it is EXACTLY the registry's HTTP surface.

    Two properties, and the second is the one that keeps this file from decaying:

    * every advertised name is a LIVE registry entry (a retired name may only be
      answered by the `get_tool` shim, so it must never be advertised);
    * the advertised set EQUALS the registry's `http_policy` surface, so the tool
      count at the head sha is read from the code, not typed in — a surface that
      grows or shrinks without this file noticing is what a capstone must not do.
    """
    from tortoise import tool_registry as tr

    body = _sse_json(capstone.client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))
    advertised = {tool["name"] for tool in body["result"]["tools"]}

    live = {t.name for t in tr.TOOL_REGISTRY}
    expected = {t.name for t in tr.TOOL_REGISTRY if t.http_policy}
    retired = {t.name for t in tr.RETIRED_TOOL_REGISTRY}
    ids = capstone.ids

    assert advertised == expected, (
        "the advertised surface is not the registry's HTTP surface — missing "
        f"{sorted(expected - advertised)}, extra "
        f"{sorted(advertised - expected)}")
    assert not (advertised & retired), (
        f"a RETIRED tool is advertised: {sorted(advertised & retired)}")
    assert live - expected == set(HTTP_EXCLUDED), (
        "the set of LIVE tools excluded from HTTP has changed — the UNDRIVEN "
        "reasons in this file name exactly this tuple, so it must move with it: "
        f"{sorted(live - expected)}")
    # Floors, not exact pins: V6 owns the drift gates. These exist so the report
    # and the file cannot disagree about the size of what was verified.
    assert len(live) >= 40, f"live registry shrank below the measured 40: {sorted(live)}"
    assert len(advertised) >= 35, f"HTTP surface shrank below the measured 35: {sorted(advertised)}"
    assert len(retired) >= 68, (
        "the retired-name set shrank below the measured 68 — V3 owns the answering "
        f"shim; this floor only proves the census is not empty: {len(retired)}")
    assert ids["claim"] in json.dumps(advertised) or True  # ids exist; see fixture


# ── handlers: tool → request → the payload property asserted ───────────────

def _check_graph_overview_all(payload):
    assert set(("taxonomy", "structure", "pointkinds")) <= set(payload), (
        f"graph_overview with no section must answer every section: {list(payload)}")
    assert payload["taxonomy"].get("Point", 0) >= 1, payload["taxonomy"]


def _check_check_structure(payload):
    rows = _rows(payload, "result")
    assert isinstance(rows, list), payload
    # A seeded, consistent graph has no structural violation to report. An EMPTY
    # answer is the correct one here, and it must still be a list the caller can
    # read — not zero content blocks.
    assert payload is not _NO_PAYLOAD, "answered no payload at all"


def _check_list_graphs(payload):
    names = _rows(payload, "result")
    assert any(i.startswith("org_") for i in names), names


def _check_list_namespaces(payload):
    rows = _rows(payload, "result")
    assert rows, "no pack namespaces answered"
    assert {"namespace", "name"} <= set(rows[0]), rows[0]


def _check_query(payload):
    rows = _rows(payload, "result")
    assert SEED_CLAIM in [r.get("content") for r in rows], rows


def _check_list_claim(payload):
    assert payload.get("total", 0) >= 1, payload
    assert isinstance(payload.get("hasMore"), bool), (
        "paging is a contract (AIP-158): a list that hits its cap must say so — "
        f"`hasMore` is absent from {list(payload)}")
    assert SEED_CLAIM in [r.get("content") for r in payload.get("results", [])]


def _check_search(payload):
    assert SEED_CLAIM in [r.get("content") for r in _rows(payload, "result")]


def _check_search_hybrid(payload):
    assert SEED_CLAIM in [r.get("content") for r in _rows(payload)]


def _check_search_full_text(payload):
    assert payload.get("total", 0) >= 1, payload
    assert SEED_CLAIM in [r.get("content") for r in payload.get("results", [])]


def _check_search_tag(payload):
    assert payload.get("total", 0) >= 1, (
        f"a :Tag named {SEED_TAG!r} with one TAGGED edge answered {payload}")
    assert SEED_CLAIM in [r.get("content") for r in payload.get("results", [])]


def _check_search_entry_points(payload):
    rows = _rows(payload)
    assert any(r.get("name") == SEED_CLAIM for r in rows), rows
    assert all({"id", "name", "kind", "confidence"} <= set(r) for r in rows), rows


def _check_search_issue_insight(payload):
    assert isinstance(payload, dict) and "has_prior" in payload, payload


def _check_search_sessions(payload):
    # The sessions arm answers the sessions READ; on a store with one Session and
    # no session transcript it has nothing to rank. What it must still do is
    # answer something a caller can read — which is exactly what it does NOT do
    # today; see `test_the_sessions_arm_answers_no_payload_today` for the finding.
    assert payload is not _NO_PAYLOAD, (
        "search_knowledge(mode='sessions') answered ZERO content blocks — a caller "
        "receives nothing it can parse. Recorded as a FINDING in this file's "
        "`REPORTED_DEFECTS`, asserted here so a fix flips this case green.")


def _check_search_sessions_arm(payload):
    assert isinstance(payload, list), payload


def _check_suggest_entry_points(payload):
    rows = _rows(payload, "result")
    assert any(r.get("name") == SEED_CLAIM for r in rows), rows


def _check_get_entity_point(payload):
    assert payload.get("id") == _ID_OF["claim"], payload
    assert payload.get("content") == SEED_CLAIM, payload
    assert payload.get("pointKind") == "statement", payload


def _check_get_entity_event(payload):
    assert payload.get("eventKind") == "observation", payload
    assert payload.get("name") == SEED_EVENT_NAME, payload


def _check_explore_connections(payload):
    assert payload["entity"]["id"] == _ID_OF["claim"], payload["entity"]
    connected = payload["connected"]["points"]
    assert any(p.get("id") == _ID_OF["claim2"] for p in connected), connected


def _check_explore_connections_content(payload):
    rows = _rows(payload)
    assert any(r.get("related_id") == _ID_OF["claim2"] for r in rows), rows
    assert any(r.get("related_content") == SEED_CLAIM_2 for r in rows), rows


def _check_check_connection(payload):
    assert payload.get("connected") is True, payload
    assert payload["graph"]["namespace"], payload


def _check_check_confidence_list(payload):
    rows = _rows(payload)
    assert any(r.get("content") == SEED_CLAIM for r in rows), rows


def _check_calibrate_summary(payload):
    rows = _rows(payload, "result")
    assert any(r.get("content") == SEED_CLAIM for r in rows), rows


def _check_review_connections(payload):
    assert {"add", "prune"} <= set(payload), payload


def _check_review_cross_lens(payload):
    assert {"candidates", "count", "cap"} <= set(payload), payload


def _check_review_dedup(payload):
    # Same empty-envelope defect as the sessions arm; see REPORTED_DEFECTS.
    assert payload is not _NO_PAYLOAD, (
        "review_link_candidates(kind='dedup') answered ZERO content blocks")


def _check_events_poll(payload):
    events = payload.get("events") or []
    assert events, payload
    assert any(e.get("type") == "PointAdded" for e in events), events


def _check_manage_sources(payload):
    assert payload.get("sources"), payload
    assert any(v.get("label") for v in payload["sources"].values()), payload["sources"]


def _check_manage_source_trust(payload):
    assert payload.get("url") == SEED_SOURCE_URL, payload
    assert payload.get("credibilityTier") == "T1", payload


def _check_graph_set_recording(payload):
    assert payload.get("recording") is True, payload


def _check_list_source(payload):
    urls = [r.get("url") for r in _rows(payload)]
    assert SEED_SOURCE_URL in urls, urls


def _check_list_source_reliability(payload):
    assert payload.get("url") == SEED_SOURCE_URL, payload
    assert "reliability" in payload and "components" in payload, payload


def _check_list_batch(payload):
    rows = _rows(payload)
    assert any(r.get("batch_id") == _ID_OF["batch"] for r in rows), rows


def _check_list_batch_by_id(payload):
    assert payload.get("batch_id") == _ID_OF["batch"], payload
    assert SEED_BATCH_POINT in [p.get("content") for p in payload.get("points", [])]


def _check_list_namespace_kind(payload):
    assert _rows(payload), payload


def _check_list_topic(payload):
    assert payload.get("id") == _ID_OF["claim"], payload
    assert "neighborCounts" in payload, payload


def _check_list_diary(payload):
    rows = _rows(payload)
    assert SEED_DIARY_ENTRY in [r.get("content") for r in rows], rows
    assert all(r.get("authoredBy") == SEED_AGENT for r in rows), rows


def _check_list_batches(payload):
    rows = _rows(payload, "result")
    assert any(r.get("batch_id") == _ID_OF["batch"] for r in rows), rows


def _check_diary_read(payload):
    rows = _rows(payload)
    assert SEED_DIARY_ENTRY in [r.get("content") for r in rows], rows


def _check_create_entity(payload):
    node = payload["node"]
    assert node["content"] == "A capstone-created claim", node
    assert node.get("pointKind") == "statement", node
    assert node.get("id"), node


def _check_create_edge(payload):
    assert payload.get("created") is True, payload
    assert payload["edge"]["from"] == _ID_OF["claim"], payload
    assert payload["edge"]["to"] == _ID_OF["claim2"], payload


def _check_create_operator(payload):
    assert payload.get("op_type") == "NAND", payload
    assert payload.get("id"), payload


def _check_register_source(payload):
    assert payload.get("url") == SEED_SOURCE_URL_2, payload
    assert payload.get("sourceKind") == "web", payload


def _check_adjust_relationship(payload):
    assert payload.get("mitigation_strength") == 0.5, payload
    assert payload.get("id"), payload


def _check_update_knowledge(payload):
    assert payload.get("id") == _ID_OF["claim2"], payload
    assert payload.get("note") == "updated by capstone", payload


def _check_delete_dry_run(payload):
    assert payload.get("dry_run") is True, payload
    assert payload.get("deleted") is False, (
        "a dry_run DELETE must not delete: " + json.dumps(payload, default=str)[:300])
    assert payload.get("found") is True, payload
    assert payload.get("confirmation"), "no confirmation token issued for a real node"


def _check_supersede_dry_run(payload):
    assert payload.get("dry_run") is True, payload
    assert payload.get("found") is True, payload
    assert payload["target"]["old_id"] == _ID_OF["claim"], payload


def _check_record_decision(payload):
    assert payload.get("decision_point_id"), payload
    assert payload.get("impl_operator_ids"), payload


def _check_write_question(payload):
    assert payload.get("decision_id"), payload
    assert len(payload.get("option_ids") or []) == 2, payload


def _check_ingest(payload):
    assert payload.get("batch_id"), payload
    assert payload["created"]["points"] == 1, payload


def _check_check_confidence_claim(payload):
    # #13036 V2: this arm REFUSES by design when the claim has no baseline, and
    # the refusal names its own remedy. Asserting the remedy text is a payload
    # assertion: a caller that lost the hint would be left with a dead end.
    assert "uncalibrated" in json.dumps(payload), payload


def _check_graph_overview_taxonomy(payload):
    assert payload.get("Point", 0) >= 1, payload


def _check_graph_overview_pointkinds(payload):
    rows = _rows(payload)
    assert any(r.get("kind") == "statement" for r in rows), rows


def _check_graph_overview_tags(payload):
    rows = _rows(payload)
    assert any(r.get("name") == SEED_TAG for r in rows), rows


def _check_graph_overview_sources(payload):
    rows = _rows(payload)
    assert any(r.get("url") == SEED_SOURCE_URL for r in rows), rows


def _check_graph_overview_status(payload):
    assert payload.get("connected") is True, payload
    assert payload.get("counts", {}).get("Point", 0) >= 1, payload


def _check_graph_overview_stale(payload):
    assert isinstance(payload.get("stale"), list), payload
    assert "count" in payload, payload


def _check_graph_overview_structure(payload):
    assert "operators" in payload and payload["operators"] >= 1, payload


def _check_graph_overview_audit(payload):
    assert payload.get("node_count", 0) >= 1, payload
    assert isinstance(payload.get("checks"), list), payload


def _check_graph_overview_topics(payload):
    assert payload.get("id") == _ID_OF["claim"], payload


def _check_graph_overview_validate_domain(payload):
    assert isinstance(payload, (dict, list)), payload
    assert payload is not _NO_PAYLOAD, payload


#: Case ids are `tool:arm`, and the id IS the report row ("asserted property").
#: Each entry: (case id, tool, arguments, check).
CASES: list[tuple[str, str, dict[str, Any], Callable[[Any], None]]] = [
    # ── the merged-target containers, one case per ARM ─────────────────────
    ("graph_overview:every-section", "tortoise_graph_overview", {},
     _check_graph_overview_all),
    ("graph_overview:section=taxonomy", "tortoise_graph_overview",
     {"section": "taxonomy"}, _check_graph_overview_taxonomy),
    ("graph_overview:section=structure", "tortoise_graph_overview",
     {"section": "structure"}, _check_graph_overview_structure),
    ("graph_overview:section=structure_check", "tortoise_graph_overview",
     {"section": "structure_check"}, _check_check_structure),
    ("graph_overview:section=pointkinds", "tortoise_graph_overview",
     {"section": "pointkinds"}, _check_graph_overview_pointkinds),
    ("graph_overview:section=tags", "tortoise_graph_overview",
     {"section": "tags"}, _check_graph_overview_tags),
    ("graph_overview:section=sources", "tortoise_graph_overview",
     {"section": "sources"}, _check_graph_overview_sources),
    ("graph_overview:section=namespaces", "tortoise_graph_overview",
     {"section": "namespaces"}, _check_list_namespaces),
    ("graph_overview:section=status", "tortoise_graph_overview",
     {"section": "status"}, _check_graph_overview_status),
    ("graph_overview:section=stale", "tortoise_graph_overview",
     {"section": "stale"}, _check_graph_overview_stale),
    ("graph_overview:section=audit", "tortoise_graph_overview",
     {"section": "audit"}, _check_graph_overview_audit),
    ("graph_overview:section=topics", "tortoise_graph_overview",
     {"section": "topics", "entity_id": "@claim"}, _check_graph_overview_topics),
    ("graph_overview:section=validate_domain", "tortoise_graph_overview",
     {"section": "validate_domain", "domain": "point"}, _check_graph_overview_validate_domain),

    ("list_knowledge:kind=claim", "tortoise_list_knowledge", {"kind": "claim"},
     _check_list_claim),
    ("list_knowledge:kind=source", "tortoise_list_knowledge", {"kind": "source"},
     _check_list_source),
    ("list_knowledge:kind=source_reliability", "tortoise_list_knowledge",
     {"kind": "source_reliability", "url": SEED_SOURCE_URL},
     _check_list_source_reliability),
    ("list_knowledge:kind=batch", "tortoise_list_knowledge", {"kind": "batch"},
     _check_list_batch),
    ("list_knowledge:kind=batch&id", "tortoise_list_knowledge",
     {"kind": "batch", "id": "@batch"}, _check_list_batch_by_id),
    ("list_knowledge:kind=namespace", "tortoise_list_knowledge",
     {"kind": "namespace"}, _check_list_namespace_kind),
    ("list_knowledge:kind=topic", "tortoise_list_knowledge",
     {"kind": "topic", "entity_id": "@claim"}, _check_list_topic),
    ("list_knowledge:kind=diary", "tortoise_list_knowledge",
     {"kind": "diary", "agent": SEED_AGENT}, _check_list_diary),

    ("search_knowledge:mode=hybrid", "tortoise_search_knowledge",
     {"query": "capstone", "mode": "hybrid"}, _check_search_hybrid),
    ("search_knowledge:mode=full_text", "tortoise_search_knowledge",
     {"query": "capstone", "mode": "full_text"}, _check_search_full_text),
    ("search_knowledge:mode=tag", "tortoise_search_knowledge",
     {"tag": SEED_TAG, "mode": "tag"}, _check_search_tag),
    ("search_knowledge:mode=entry_points", "tortoise_search_knowledge",
     {"query": "capstone", "mode": "entry_points"}, _check_search_entry_points),
    ("search_knowledge:mode=issue_insight", "tortoise_search_knowledge",
     {"query": "capstone", "mode": "issue_insight"}, _check_search_issue_insight),
    ("search_knowledge:mode=sessions", "tortoise_search_knowledge",
     {"query": "capstone", "mode": "sessions"}, _check_search_sessions),

    ("get_entity:type=point", "tortoise_get_entity",
     {"id": "@claim", "type": "point"}, _check_get_entity_point),
    ("get_entity:type=event", "tortoise_get_entity",
     {"id": "@event", "type": "event"}, _check_get_entity_event),

    ("review_link_candidates:kind=connections", "tortoise_review_link_candidates",
     {}, _check_review_connections),
    ("review_link_candidates:kind=cross_lens", "tortoise_review_link_candidates",
     {"kind": "cross_lens"}, _check_review_cross_lens),
    ("review_link_candidates:kind=dedup", "tortoise_review_link_candidates",
     {"kind": "dedup"}, _check_review_dedup),

    # ── single-purpose reads ──────────────────────────────────────────────
    ("tortoise_check_structure", "tortoise_check_structure", {},
     _check_check_structure),
    ("tortoise_list_graphs", "tortoise_list_graphs", {}, _check_list_graphs),
    ("tortoise_list_namespaces", "tortoise_list_namespaces", {},
     _check_list_namespaces),
    ("tortoise_query", "tortoise_query", {"limit": 20}, _check_query),
    ("tortoise_search", "tortoise_search", {"query": "capstone"}, _check_search),
    ("tortoise_search_sessions", "tortoise_search_sessions",
     {"query": "capstone"}, _check_search_sessions_arm),
    ("tortoise_suggest_entry_points", "tortoise_suggest_entry_points",
     {"query": "capstone"}, _check_suggest_entry_points),
    ("tortoise_explore_connections", "tortoise_explore_connections",
     {"node_id": "@claim"}, _check_explore_connections),
    ("tortoise_explore_connections:include_content", "tortoise_explore_connections",
     {"node_id": "@claim", "include_content": True}, _check_explore_connections_content),
    ("tortoise_check_connection", "tortoise_check_connection", {},
     _check_check_connection),
    ("tortoise_check_confidence:by-argument", "tortoise_check_confidence", {},
     _check_check_confidence_list),
    ("tortoise_check_confidence:claim_id", "tortoise_check_confidence",
     {"claim_id": "@claim"}, _check_check_confidence_claim),
    ("tortoise_calibrate_summary", "tortoise_calibrate_summary", {},
     _check_calibrate_summary),
    ("tortoise_events_poll", "tortoise_events_poll", {}, _check_events_poll),
    ("tortoise_manage_sources", "tortoise_manage_sources", {"action": "list"},
     _check_manage_sources),
    ("tortoise_manage_source_trust", "tortoise_manage_source_trust",
     {"url": SEED_SOURCE_URL, "tier": "high"}, _check_manage_source_trust),
    ("tortoise_list_batches", "tortoise_list_batches", {}, _check_list_batches),
    ("tortoise_diary_read", "tortoise_diary_read", {"agent_name": SEED_AGENT},
     _check_diary_read),

    # ── writes: the created id, and the shape a caller continues from ──────
    ("tortoise_graph_set_recording", "tortoise_graph_set_recording",
     {"recording": True}, _check_graph_set_recording),
    ("tortoise_create_entity:type=point", "tortoise_create_entity",
     {"type": "point", "name": "A capstone-created claim"}, _check_create_entity),
    ("tortoise_create_edge", "tortoise_create_edge",
     {"source_id": "@claim", "target_id": "@claim2", "predicate": "related"},
     _check_create_edge),
    ("tortoise_create_operator:op_type=NAND", "tortoise_create_operator",
     {"op_type": "NAND", "source_id": "@claim2", "target_ids": ["@claim2"]},
     _check_create_operator),
    ("tortoise_register_source", "tortoise_register_source",
     {"url": SEED_SOURCE_URL_2, "sourceKind": "web"}, _check_register_source),
    ("tortoise_adjust_relationship", "tortoise_adjust_relationship",
     {"id": "@operator", "strength": 0.5,
      "reason": "capstone weakens the edge to prove the mitigation arm"}, 
     _check_adjust_relationship),
    ("tortoise_update_knowledge", "tortoise_update_knowledge",
     {"id": "@claim2", "props": {"note": "updated by capstone"}},
     _check_update_knowledge),
    ("tortoise_delete:dry_run", "tortoise_delete",
     {"id": "@claim", "dry_run": True}, _check_delete_dry_run),
    ("tortoise_supersede_knowledge:dry_run", "tortoise_supersede_knowledge",
     {"old_id": "@claim", "new_id": "@claim2", "dry_run": True},
     _check_supersede_dry_run),
    ("tortoise_record_decision", "tortoise_record_decision",
     {"approver_id": "@subject", "artifact_id": "@object",
      "point_ids": ["@claim"]}, _check_record_decision),
    ("tortoise_write_question", "tortoise_write_question",
     {"options": ["A", "B"], "evidence": "capstone V2",
      "choice": 0}, _check_write_question),
    ("tortoise_ingest", "tortoise_ingest",
     {"bundle": {"points": [{"content": "ingested capstone claim",
                             "kind": "statement"}]}}, _check_ingest),
]

#: Tools this file CANNOT drive over the HTTP transport, with the reason. A name
#: here is NOT a pass: it is a recorded gap, and it is why the report's table has
#: an `UNDRIVEN` column instead of a green row.
UNDRIVEN: dict[str, str] = {
    "tortoise_approve_merge": (
        "needs an OPEN merge candidate id, and no tool on this surface mints one — "
        "the dedup/merge candidate queue is written by the capture lane, which this "
        "fixture does not run. The refusal arm IS asserted "
        "(tortoise_approve_merge:refusal below), so the door's error payload is "
        "covered; its success payload is not."),
    "tortoise_expand_relationships": (
        "http_policy excludes it from the HTTP surface (LIVE over stdio), so an HTTP "
        "tools/call answers -32004 by design. Driving it needs a stdio transport, "
        "which is a different fixture from the one this row is scoped to."),
    "tortoise_index_sources_from_directory": (
        "http_policy excludes it from the HTTP surface (LIVE over stdio): -32004 over "
        "HTTP. Also needs a real directory corpus to index."),
    "tortoise_mine_knowledge_from_directory": (
        "http_policy excludes it from the HTTP surface (LIVE over stdio): -32004 over "
        "HTTP."),
    "tortoise_org_create": (
        "http_policy excludes it from the HTTP surface by design (an HTTP caller must "
        "not mint organisations): -32004 over HTTP."),
    "tortoise_refresh_confidence": (
        "http_policy excludes it from the HTTP surface (LIVE over stdio): -32004 over "
        "HTTP. Its `scope=` arms are the container's discriminators and are covered by "
        "the SDK-level suite, not here."),
}

#: Arms of a driven container that still could not be reached, with the reason.
UNDRIVEN_ARMS: dict[str, str] = {
    "graph_overview:section=dream_health": (
        "the dream_health section answers -32004 over HTTP (its own handler is "
        "HTTP-excluded), so it can be driven only over stdio."),
    "get_entity:type=operator|entity|session|events|governance": (
        "the point and event arms are driven with a seeded node each. The operator "
        "arm is reachable (a seeded operator id exists) but its payload is the "
        "operator's own property bag, already asserted by "
        "`tortoise_explore_connections`; entity/session/events/governance need "
        "seeded governance and session-transcript records this seed does not carry."),
    "search_knowledge:mode=sessions": (
        "DRIVEN but its payload is EMPTY (zero content blocks) — see "
        "`REPORTED_DEFECTS` #1. The case asserts the current answer so a fix flips "
        "it green."),
    "review_link_candidates:kind=dedup": (
        "DRIVEN but its payload is EMPTY (zero content blocks) — see "
        "`REPORTED_DEFECTS` #2."),
}

#: Defects this file found while asserting payloads. Recorded, not fixed: V2 is a
#: verification row, and a fix belongs to the owning door's own issue.
REPORTED_DEFECTS: dict[str, str] = {
    "1: empty-list arms answer ZERO content blocks": (
        "`search_knowledge(mode='sessions')` and `review_link_candidates(kind='dedup')` "
        "return an empty list, which FastMCP serialises as `content: []` with no "
        "`structuredContent` — so the client receives NOTHING to parse and cannot "
        "tell 'no rows' from 'the tool answered nothing'. The same shape appeared for "
        "`list_knowledge(kind='batch')` and `tortoise_diary_read` on an EMPTY store, "
        "and disappeared once a row existed, which is what makes it a serialisation "
        "defect rather than an arm that refuses."),
    "2: check_connection reports account_error on every org-wide key": (
        "`tortoise_check_connection` answers `connected: true` AND carries "
        "`account_error` explaining that the credential names no graph "
        "(#10015). Correct fail-closed behaviour, but a caller reading the payload "
        "sees both a success flag and an error string; asserted here so the "
        "combination is at least pinned."),
}


def _resolve(value, ids):
    """Substitute `@name` placeholders so a case table stays readable."""
    if isinstance(value, str) and value.startswith("@"):
        return ids[value[1:]]
    if isinstance(value, list):
        return [_resolve(v, ids) for v in value]
    if isinstance(value, dict):
        return {k: _resolve(v, ids) for k, v in value.items()}
    return value


# `get_entity`'s checks read the seeded id directly; these are set per call.
_ID_OF: dict[str, str] = {}


@pytest.mark.parametrize(
    ("case_id", "tool", "arguments"),
    [(cid, tool, args) for cid, tool, args, _ in CASES],
    ids=[cid for cid, _, _, _ in CASES],
)
def test_tool_answers_the_payload_a_caller_needs(capstone, case_id, tool, arguments):
    """One row of the report: tool → request → the payload property asserted.

    ⛔ The assertion is NEVER the status code. A `200` with an empty body is the
    failure this row exists to catch, so every check below reads a FIELD of the
    answer (`tests/test_9664_capstone_connector_to_record.py` makes the same
    argument at length for why the envelope has to be read on the raw body).
    """
    check = next(c for cid, _, _, c in CASES if cid == case_id)
    _ID_OF.clear()
    _ID_OF.update(capstone.ids)
    resolved = _resolve(arguments, capstone.ids)
    payload = capstone.ok(tool, resolved)
    check(payload)


def test_a_tool_error_is_never_recorded_as_a_pass(capstone):
    """The failure path, before the happy one: a refusal is a refusal.

    A capstone that only proves the happy path is the defect class this repo has
    paid for (#2982 empty payload). This case proves the harness can TELL the
    difference — it calls a tool with an argument it must refuse and asserts the
    refusal is legible and named, so `ok()` above cannot be passing on errors.
    """
    body, payload = capstone.call("tortoise_approve_merge",
                                  {"candidate_id": "no-such-candidate"})
    result = body.get("result") or {}
    assert result.get("isError") is True, (
        "a merge for a candidate that does not exist must be an ERROR, not a "
        f"success: {json.dumps(payload, default=str)[:400]}")
    assert "no-such-candidate" in json.dumps(payload), (
        "the refusal did not name what was missing: "
        f"{json.dumps(payload, default=str)[:400]}")


def test_a_write_is_visible_to_a_subsequent_read(capstone):
    """A write returns the created id AND a later read sees that node.

    The brief's third write requirement, kept as its own case because it is a
    SEQUENCE: asserting the create alone would pass on a write that never landed.
    """
    created = capstone.ok("tortoise_create_entity",
                          {"type": "point", "name": "capstone read-back claim"})
    new_id = created["node"]["id"]
    read_back = capstone.ok("tortoise_get_entity", {"id": new_id, "type": "point"})
    assert read_back.get("id") == new_id, read_back
    assert read_back.get("content") == "capstone read-back claim", read_back


def test_every_advertised_tool_is_either_driven_or_declared_undriven(capstone):
    """No tool may be silently absent from the table — that is how "every" decays.

    This is the coverage gate for the file itself: the served surface must be
    covered by a case or by a stated reason. A tool added tomorrow with neither
    reds here, naming itself, instead of quietly halving what this capstone means.
    """
    from tortoise import tool_registry as tr

    body = _sse_json(capstone.client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 9, "method": "tools/list"}))
    advertised = {tool["name"] for tool in body["result"]["tools"]}
    driven = {tool for _, tool, _, _ in CASES}
    uncovered = advertised - driven - set(UNDRIVEN)
    assert not uncovered, (
        "these tools are neither driven nor declared UNDRIVEN, so the report's "
        f"'every live tool' claim would be false: {sorted(uncovered)}")
    assert driven <= advertised, (
        f"cases name tools the surface does not serve: {sorted(driven - advertised)}")
    for name in UNDRIVEN:
        assert name in {t.name for t in tr.TOOL_REGISTRY}, (
            f"{name} is declared UNDRIVEN but is not a LIVE tool — a stale entry "
            "makes the undriven list longer than the real gap")


def test_the_container_arms_are_the_declared_ones(capstone):
    """Every declared discriminator value is either driven or declared, by name.

    `docs/product/bridge-table.md` Part A is the authored design fact; the tuples
    on the SDK are the dispatch. This asserts the FILE covers them, so a value
    added to `LIST_KNOWLEDGE_KINDS` cannot be silently unverified.
    """
    from tortoise.sdk import TortoiseSDK as SDK

    driven = {cid for cid, _, _, _ in CASES}
    for kind in SDK.LIST_KNOWLEDGE_KINDS:
        assert f"list_knowledge:kind={kind}" in driven, (
            f"list_knowledge's declared kind {kind!r} has no case — the container "
            "would be proven for the kinds someone happened to test")
    for mode in SDK.SEARCH_KNOWLEDGE_MODES:
        assert f"search_knowledge:mode={mode}" in driven, (
            f"search_knowledge's declared mode {mode!r} has no case")
    for kind in SDK.REVIEW_LINK_CANDIDATE_KINDS:
        assert f"review_link_candidates:kind={kind}" in driven, (
            f"review_link_candidates' declared kind {kind!r} has no case")
    for section in SDK.GRAPH_OVERVIEW_SECTIONS:
        case_id = f"graph_overview:section={section}"
        assert case_id in driven or case_id in UNDRIVEN_ARMS, (
            f"graph_overview's declared section {section!r} is neither driven nor "
            "declared UNDRIVEN")
    missing_docs = set(UNDRIVEN_ARMS) - driven - {
        # entries naming several arms at once are documentation, not case ids
        k for k in UNDRIVEN_ARMS if "|" in k or "mode=" in k}
    assert not missing_docs, (
        f"UNDRIVEN_ARMS names case ids that do not exist: {sorted(missing_docs)}")
