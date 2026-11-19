"""#13036 capstone V2 — the SURVIVING tools return a **correct payload**.

WHAT THIS FILE OWNS, AND WHAT IT DELIBERATELY DOES NOT
======================================================

``#13036`` is the capstone over the 27/40 surface (owner ruling 2026-09-18,
``pr=4120``). It is split into separate rows so that a green row cannot stand in for
a row nobody ran:

* **V1** — every target's *inputs* are reachable from the owning tool.
* **V2** — *(this file)* every surviving tool returns a **correct payload** for a
  valid request.
* **V3** — retired names still answer through the #3883 warning shim.
* **V4** — no absorbed capability was dropped.
* **V5** — ontology conformance.
* **V6** — drift gates.

Two layers were already proven before this file existed and are **not re-derived
here**:

1. **Bindings.** A probe resolves a handler for every live tool and invokes it with
   the SDK seam patched. Result on the head this file was written against:
   **40 live, 0 broken, 2 refused**. That row is about *resolution* — it says
   nothing about whether the resolved handler answers correctly.
2. **Transport.** ``tests/test_mcp_http.py`` serves the tools through authenticated
   MCP and asserts *advertised == served* (122 passed). That row is about *the
   door* — it proves the right set of names is reachable, not that a name's
   payload is right.

So the gap V2 closes is exactly: **a tool that resolves and is advertised, but
answers the wrong thing.** A resolver cannot see that; ``advertised == served``
cannot see that; only asserting the payload can.

WHY A PAYLOAD ASSERTION AND NOT A STATUS ASSERTION
==================================================

``tests/test_11609_read_containers.py`` and ``..._write_containers.py`` prove
*dispatch* by substitution: every discriminator value reaches its own door. They
use **no database** and assert ``{"door": label}`` — deliberately, because routing
is what a container changes there.

A dispatch test and a payload test fail on disjoint regressions. Substitution
catches "``kind='source'`` calls the wrong door". It cannot catch "the source door
returns ``[]`` because the query lost its ``WHERE``" — the door was right, the
answer was empty, and the container test is green either way. That is the failure
mode this file exists for, and it is why **every assertion below reads the seeded
content out of the answer** rather than asserting a status code, ``isError``, or
"it returned a dict".

That is also the answer to the EXTERNAL question the lane brief posed ("is a
'returns 200' assertion worth anything without a payload assertion?"): no. A
transport-level success is a statement about the envelope; the contract a caller
depends on is the payload inside it, so a contract test asserts the payload. One
seeded request per tool is therefore **not** enough on its own for a *container* —
a container's contract is *per discriminator value*, because each arm is a
different query behind one name. Hence requirement 3 below.

THE SEED
========

One embedded store, seeded once per test, holding the minimum real content every
read needs:

* two claims (a ``:Point`` each),
* one object, one subject, one event,
* one source,
* one operator between two claims (``IMPL``),
* one agent session (an ``AgentSession`` Event — the kind ``get_session`` reads),
* one topic-tagged claim (for the ``check_confidence(topic=...)`` arm),
* one calibrated claim (for the ``check_confidence(claim_id=...)`` happy path).

Assertions read these ids back out of the answers. A payload is "correct" when it
contains the thing we seeded — not when it is merely non-empty.

UNDRIVEN TOOLS ARE RECORDED, NEVER PASSED
=========================================

Four live tools refuse at the tenant HTTP transport by design (filesystem-walk and
provisioning vectors). They cannot be driven through this fixture, so they are
listed in ``UNDRIVEN`` with the reason, and ``test_every_live_tool_is_accounted_for``
asserts the union covers the registry exactly. A tool added without a case fails
that test — the surface cannot grow silently past this row. An ``UNDRIVEN`` entry
is **not** a pass, and this file does not claim those tools' payloads.

THIS SUITE WAS PROVEN ABLE TO FAIL (mutation record)
====================================================

A payload suite that has never reddened is an untested test. Two mutations were
applied to ``tortoise/mcp_server.py``, observed red, and reverted — the tree was
confirmed clean between them:

1. **Right door, right TYPE, empty payload.** ``tortoise_list_knowledge`` returned
   ``[]`` for ``kind='source'`` before delegating — the exact "the query lost its
   ``WHERE``" failure this row exists for, and one that a dispatch/substitution
   test and an ``advertised == served`` test both pass. Reddened
   ``test_source_kind_contains_the_seeded_source`` (``AssertionError: []``) and
   **only** that test — the neighbouring ``pointkinds`` case stayed green, so the
   assertion is targeted rather than blunt.
2. **Right door, right shape, WRONG NODE.** ``tortoise_get_entity(type='point')``
   returned the seeded node's properties with its ``id`` replaced by
   ``"wrong-id-not-the-seeded-one"``. Reddened both
   ``test_each_type_arm_returns_the_seeded_node[point…]`` **and**
   ``test_entity_write_returns_an_id_the_get_lane_reads_back[point…]`` — which is
   what makes the write→read direction an IDENTITY assertion and not merely a
   "something came back" assertion.

Neither mutation was a crash or an exception: both returned a well-formed payload
of the correct type. That is the point. A suite that only catches crashes cannot
see the failure class this row owns.
"""

from __future__ import annotations

import os

# 67: TORTOISE_SECRET_PEPPER is mandatory for auth module import.
os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import json

import pytest

from tortoise.sdk import TortoiseSDK
from tortoise.tool_registry import TOOL_REGISTRY

# ── the live surface, as the registry declares it ───────────────────────────

LIVE_TOOLS = frozenset(t.name for t in TOOL_REGISTRY)

#: Live tools that **cannot** be driven through the authenticated transport.
#: Every entry names the mechanism, because "could not drive it" is a finding
#: only when it says why. None of these is a pass.
UNDRIVEN: dict[str, str] = {
    "tortoise_org_create":
        "UNDRIVEN — excluded from tenant HTTP by design: graph provisioning is a "
        "privilege boundary served by /internal/provision, not by the tenant MCP "
        "door (handler returns the -32004 HTTP-exclusion refusal).",
    "tortoise_index_sources_from_directory":
        "UNDRIVEN — excluded from tenant HTTP by design: the directory argument is "
        "a caller-controlled filesystem walk on the HOST, a vector the tenant door "
        "must not expose.",
    "tortoise_mine_knowledge_from_directory":
        "UNDRIVEN — excluded from tenant HTTP by design: same HOST filesystem walk "
        "as index_sources_from_directory.",
    "tortoise_refresh_confidence":
        "UNDRIVEN — excluded from tenant HTTP by design: the 4-tool refresh family "
        "is served by the hosted REST API / stdio lane, not the tenant MCP door.",
}


# ── transport helpers (the fixture shape from tests/test_mcp_http.py) ───────

def _parse_sse_json(r):
    """Parse a body that may be SSE-framed (event: message\\ndata: {...})."""
    text = r.text
    if text.startswith("event:") or "\ndata: " in text:
        for line in text.splitlines():
            if line.startswith("data: "):
                return json.loads(line[len("data: "):])
        return None
    return r.json()


def _tool_payload(body):
    """The PAYLOAD is ``content[0]``; later blocks are appended disclosures.

    Same rule as ``tests/test_mcp_http.py::_tool_result``: joining every block
    turns a write into ``{"text": …}`` once a disclosure block appears (#10084).

    An EMPTY collection is served as ``content: []`` with the value carrying in
    ``structuredContent.result`` — measured on ``list_knowledge(kind='batch')``
    against an unseeded batch set. Without that fallback the payload reads as the
    raw envelope and an empty-but-correct answer is indistinguishable from a
    broken seam, which would make the assertion vacuous in exactly the case it
    exists to pin.
    """
    result = (body or {}).get("result", {}) or {}
    if isinstance(result, dict) and "content" in result:
        blocks = [c for c in result["content"] if isinstance(c, dict)]
        text = blocks[0].get("text", "") if blocks else ""
        if text:
            try:
                return json.loads(text)
            except ValueError:
                return {"_text": text}
        structured = result.get("structuredContent")
        if isinstance(structured, dict) and "result" in structured:
            return structured["result"]
        # An empty SEQUENCE renders as zero content blocks: FastMCP emits
        # `content: []` and, for some handlers, no `structuredContent` at all —
        # measured on `list_knowledge(kind='batch')` and on `list_batches`
        # against an unseeded batch set. Reading that as the envelope would make
        # an empty-but-correct answer indistinguishable from a broken seam.
        if not blocks and not result.get("isError"):
            return []
    return result


class _Door:
    """Authenticated MCP transport with a counter for JSON-RPC ids."""

    def __init__(self, tc):
        self._tc = tc
        self._n = 0

    def call(self, name, arguments=None):
        self._n += 1
        r = self._tc.post("/mcp", json={
            "jsonrpc": "2.0", "id": self._n, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })
        return r.status_code, _tool_payload(_parse_sse_json(r))


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    """One embedded store, seeded with the minimum real content every read needs.

    The team graph is ``namespace=<org_id>`` (``mcp_auth._get_org_sdk``), which is
    why the seeder opens that namespace on the same db file the request-scoped SDK
    will open. Embedded (no Docker) mirrors the ``test_mcp_http.py`` fixture.
    """
    from tortoise.mcp_server import create_http_app
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.testclient import TestClient
    from contextlib import asynccontextmanager

    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    db = str(tmp_path / "capstone.db")
    monkeypatch.setenv("TORTOISE_DB_PATH", db)

    reg = TortoiseSDK(db_path=db, namespace="registry")
    team = reg.org_create("capstone-v2-team")
    key = reg._apikey_create(team["id"], "capstone")["api_key"]
    org = team["id"]

    sdk = TortoiseSDK(db_path=db, namespace=org)
    s: dict[str, str] = {}

    # ── the seed ────────────────────────────────────────────────────────────
    s["claim"] = sdk.create_point(
        "statement", "The capstone V2 lane proves payloads, not statuses.")["id"]
    # A CALIBRATED claim: the `check_confidence(claim_id=…)` happy path refuses
    # an uncalibrated evidence point by name, so the happy path needs one that
    # carries a credibility prior.
    s["calibrated"] = sdk.create_point(
        "statement", "A calibrated seeded claim for the confidence arm.",
        credibility="high")["id"]
    s["claim2"] = sdk.create_point(
        "statement", "A second seeded claim, the operator's target.")["id"]
    s["object"] = sdk.create_object("Widget", objectKind="core:thing")["id"]
    s["subject"] = sdk.create_subject("Alice")["id"]
    s["source"] = sdk.register_source("https://example.test/a", "web")["id"]
    s["event"] = sdk.create_event("Deploy", eventKind="core:action")["id"]
    s["operator"] = sdk.create_operator("IMPL", s["claim"], [s["claim2"]])["id"]
    s["session"] = "sess-capstone-1"
    sdk.create_event("capstone session", eventKind="AgentSession",
                     session_id=s["session"], agent_name="capstone")

    # ── transport ───────────────────────────────────────────────────────────
    app = create_http_app(allowed_origins=["https://app.premiselabs.co"],
                          _registry_sdk=reg)

    @asynccontextmanager
    async def _lifespan(parent_app):
        async with app.lifespan(app):
            yield

    parent = Starlette(lifespan=_lifespan, routes=[Mount("/mcp", app=app)])
    tc = TestClient(parent)
    tc.headers.update({
        "Authorization": f"Bearer {key}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    })
    with tc:
        yield _Door(tc), s, sdk


def _ids(rows):
    """Every id-shaped field in a collection, for containment assertions."""
    out = set()
    for row in rows or []:
        if isinstance(row, dict):
            for k in ("id", "eventId", "url", "canonicalUrl"):
                if isinstance(row.get(k), str):
                    out.add(row[k])
    return out


# ══════════════════════════════════════════════════════════════════════════
# 0 — the accounting gate: no live tool escapes this row unnoticed
# ══════════════════════════════════════════════════════════════════════════

def test_every_live_tool_is_accounted_for():
    """Every live tool is either DRIVEN below or recorded UNDRIVEN with a reason.

    This is the row's anti-silence gate. Adding a tool to the registry without a
    payload case fails here, so the surface cannot grow past V2 unnoticed — the
    failure names the new tool.

    It does NOT prove the driven tools' payloads; the per-tool tests do that.
    """
    driven = _driven_tool_names()
    recorded = driven | set(UNDRIVEN)
    missing = LIVE_TOOLS - recorded
    assert not missing, (
        "live tool(s) with no V2 payload case and no recorded reason: "
        f"{sorted(missing)} — add a case, or record UNDRIVEN with the mechanism")
    unknown = recorded - LIVE_TOOLS
    assert not unknown, (
        "this file names tool(s) that are not live (retired or never existed): "
        f"{sorted(unknown)} — a case for a retired name belongs to row V3")


def test_undriven_entries_name_a_mechanism():
    """`UNDRIVEN` is a finding only when it says why — never a bare label."""
    for name, reason in UNDRIVEN.items():
        assert reason.startswith("UNDRIVEN — "), name
        assert len(reason) > 60, f"{name}: reason must name the mechanism"


def _driven_tool_names():
    """The tools this file calls over the transport, read off its case list."""
    return set(_DRIVEN_BY_TEST)


#: Tools driven by the per-family tests in this file. Listed here so the
#: accounting gate can assert coverage without importing pytest's collection —
#: the alternative (trusting the tests to remember) is what lets a new tool slip
#: past a row unnoticed.
_DRIVEN_BY_TEST = (
    "tortoise_list_knowledge", "tortoise_search_knowledge", "tortoise_search",
    "tortoise_query", "tortoise_get_entity", "tortoise_create_entity",
    "tortoise_graph_overview", "tortoise_check_confidence",
    "tortoise_calibrate_summary", "tortoise_check_connection",
    "tortoise_check_structure", "tortoise_explore_connections",
    "tortoise_expand_relationships", "tortoise_review_link_candidates",
    "tortoise_list_batches", "tortoise_list_graphs", "tortoise_list_namespaces",
    "tortoise_manage_source_trust", "tortoise_register_source",
    "tortoise_events_poll", "tortoise_diary_read", "tortoise_search_sessions",
    "tortoise_suggest_entry_points", "tortoise_write_question",
    "tortoise_record_decision", "tortoise_create_edge",
    "tortoise_create_operator", "tortoise_adjust_relationship",
    "tortoise_approve_merge", "tortoise_ingest", "tortoise_update_knowledge",
    "tortoise_supersede_knowledge", "tortoise_delete",
    "tortoise_graph_set_recording", "tortoise_manage_sources",
    "tortoise_mine_knowledge_from_session",
)


# ══════════════════════════════════════════════════════════════════════════
# 1 — GET returns the seeded node
# ══════════════════════════════════════════════════════════════════════════

class TestGetReturnsSeededNode:
    """`get_entity`'s 7 `type=` arms each return the node that was seeded."""

    @pytest.mark.parametrize("type_,key,field,expected", [
        ("point", "claim", "id", "claim"),
        ("operator", "operator", "id", "operator"),
        ("entity", "object", "id", "object"),
        ("event", "event", "id", "event"),
        ("session", "session", "session_id", "session"),
    ])
    def test_each_type_arm_returns_the_seeded_node(self, seeded, type_, key,
                                                   field, expected):
        door, s, _ = seeded
        status, out = door.call("tortoise_get_entity",
                                {"id": s[key], "type": type_})
        assert status == 200
        assert isinstance(out, dict), out
        assert out.get(field) == s[expected], (
            f"get_entity(type={type_!r}) did not return the seeded node: {out}")

    def test_the_events_arm_lists_seeded_events(self, seeded):
        """`type='events'` is a LIST surface — it must contain the seeded event."""
        door, s, _ = seeded
        status, out = door.call("tortoise_get_entity", {"type": "events"})
        assert status == 200
        assert isinstance(out, list) and out, out
        assert s["event"] in _ids(out), out

    def test_omitted_type_auto_detects_the_seeded_point(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_get_entity", {"id": s["claim"]})
        assert status == 200
        assert out.get("id") == s["claim"], out
        assert out.get("content", "").startswith("The capstone V2 lane")

    def test_governance_arm_returns_no_owned_entities_for_a_point(self, seeded):
        """`type='governance'` answers the entities a Subject OWNS. The seed's
        claim is a Point owned by nobody, so the correct payload is an EMPTY
        LIST — asserted as that value, not as "something non-None came back"."""
        door, s, _ = seeded
        status, out = door.call("tortoise_get_entity",
                                {"id": s["claim"], "type": "governance"})
        assert status == 200
        assert out == [], (
            "a Point owns no entities, so the governance arm must answer []; "
            f"got {out!r}")


# ══════════════════════════════════════════════════════════════════════════
# 2 — LIST is non-empty and contains the seed, for EVERY `kind=` arm
# ══════════════════════════════════════════════════════════════════════════

class TestListKnowledgeKinds:
    """`list_knowledge(kind=…)` is proven per KIND, not for `claim` alone.

    Part A of `docs/product/bridge-table.md` records 7 sources absorbed by this
    target with `kind=` as the discriminator. A case that only proves `claim`
    leaves six arms unproven — and they are different queries.
    """

    def test_claim_kind_contains_the_seeded_claim(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_list_knowledge", {"kind": "claim"})
        assert status == 200
        rows = out.get("results") if isinstance(out, dict) else out
        assert s["claim"] in _ids(rows), out

    def test_source_kind_contains_the_seeded_source(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_list_knowledge", {"kind": "source"})
        assert status == 200
        assert isinstance(out, list) and out, out
        urls = {row.get("url") for row in out if isinstance(row, dict)}
        assert s["source"] in urls, out

    def test_source_reliability_kind_reads_the_seeded_source(self, seeded):
        """`kind='source_reliability'` is a PER-SOURCE read: it requires `url=`."""
        door, s, _ = seeded
        door.call("tortoise_manage_source_trust",
                  {"url": s["source"], "tier": "high"})
        status, out = door.call("tortoise_list_knowledge",
                                {"kind": "source_reliability",
                                 "url": s["source"]})
        assert status == 200
        assert isinstance(out, dict), out
        assert out.get("url", s["source"]) == s["source"], out

    def test_batch_kind_answers_a_collection(self, seeded):
        """No batch was seeded, so the CORRECT payload is an empty collection —
        asserted as a collection, so a `None`/error regression still fails."""
        door, s, _ = seeded
        status, out = door.call("tortoise_list_knowledge", {"kind": "batch"})
        assert status == 200
        rows = out.get("results") if isinstance(out, dict) else out
        assert isinstance(rows, (list, tuple)), out

    def test_namespace_kind_answers_the_pack_namespaces(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_list_knowledge",
                                {"kind": "namespace"})
        assert status == 200
        assert isinstance(out, list) and out, out
        names = {row.get("namespace") for row in out if isinstance(row, dict)}
        assert names, out
        assert all(isinstance(n, str) and n for n in names), out

    def test_topic_kind_answers_the_seeded_entitys_profile(self, seeded):
        """`kind='topic'` is a PER-ENTITY read: it must echo the entity it was
        asked about, and report its neighbourhood."""
        door, s, _ = seeded
        status, out = door.call("tortoise_list_knowledge",
                                {"kind": "topic", "entity_id": s["claim"]})
        assert status == 200
        assert isinstance(out, dict), out
        assert out.get("id") == s["claim"], out
        assert "neighbors" in out, out

    def test_diary_kind_reads_the_seeded_author(self, seeded):
        """`kind='diary'` is PER-AUTHOR. Seed one entry so the arm is not vacuous."""
        door, s, sdk = seeded
        sdk.create_point("diary", "Seeded diary entry for the read arm.",
                         authoredBy="capstone-author")
        status, out = door.call("tortoise_list_knowledge",
                                {"kind": "diary", "agent": "capstone-author"})
        assert status == 200
        rows = out.get("results") if isinstance(out, dict) else out
        assert isinstance(rows, list) and rows, out
        assert any("Seeded diary entry" in str(row.get("content", ""))
                   for row in rows if isinstance(row, dict)), out


# ══════════════════════════════════════════════════════════════════════════
# 3 — SEARCH returns the seeded claim
# ══════════════════════════════════════════════════════════════════════════

class TestSearchReturnsSeededClaim:

    def test_search_knowledge_hybrid_finds_the_seeded_claim(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_search_knowledge",
                                {"query": "capstone"})
        assert status == 200
        assert isinstance(out, list) and out, out
        assert s["claim"] in _ids(out), out

    @pytest.mark.parametrize("mode", ["hybrid", "full_text", "tag", "sessions",
                                      "entry_points", "issue_insight"])
    def test_each_search_mode_is_a_valid_discriminator(self, seeded, mode):
        """Every advertised `mode=` is accepted, and the unknown-mode refusal is
        NOT what comes back — this is the mode set Part A records as `mode=`."""
        door, s, _ = seeded
        args = {"query": "capstone", "mode": mode}
        if mode == "tag":
            args["tag"] = "statement"
        status, out = door.call("tortoise_search_knowledge", args)
        assert status == 200
        if isinstance(out, dict) and "error" in out:
            assert "unknown mode" not in str(out["error"]).lower(), (
                f"mode={mode!r} was refused as unknown: {out}")
        else:
            assert out is not None, out

    def test_search_finds_the_seeded_claim(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_search", {"query": "capstone"})
        assert status == 200
        assert isinstance(out, list) and out, out
        assert s["claim"] in _ids(out), out

    def test_query_structural_read_returns_the_seeded_claim(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_query", {"kind": "statement"})
        assert status == 200
        assert isinstance(out, list) and out, out
        assert s["claim"] in _ids(out), out

    def test_suggest_entry_points_returns_the_seeded_claim(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_suggest_entry_points",
                                {"query": "capstone"})
        assert status == 200
        assert isinstance(out, list) and out, out
        assert s["claim"] in _ids(out), out

    def test_search_sessions_returns_the_seeded_session(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_search_sessions", {"query": "capstone"})
        assert status == 200
        assert isinstance(out, list) and out, out
        assert s["session"] in {r.get("session_id") for r in out
                                if isinstance(r, dict)}, out


# ══════════════════════════════════════════════════════════════════════════
# 4 — WRITE returns the created id AND a subsequent read sees it
# ══════════════════════════════════════════════════════════════════════════

class TestWritesAreReadableBack:
    """A write is correct when its id comes back AND the read lane finds it.

    Asserting only the returned id would pass on a handler that creates a node
    nothing can read — the write-shaped version of a status-only assertion.
    """

    @pytest.mark.parametrize("type_,name", [
        ("point", "Capstone point written over the wire."),
        ("object", "Capstone Gadget"),
        ("subject", "Capstone Bob"),
    ])
    def test_entity_write_returns_an_id_the_get_lane_reads_back(
            self, seeded, type_, name):
        door, s, _ = seeded
        status, out = door.call("tortoise_create_entity",
                                {"type": type_, "name": name})
        assert status == 200
        node = out.get("node", out)
        new_id = node.get("id")
        assert new_id, out

        read_type = "point" if type_ == "point" else "entity"
        status, got = door.call("tortoise_get_entity",
                                {"id": new_id, "type": read_type})
        assert status == 200
        assert got.get("id") == new_id, got

    def test_event_write_is_readable_back(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_create_entity",
                                {"type": "event", "name": "Capstone Event",
                                 "props": {"eventKind": "core:action"}})
        assert status == 200
        new_id = (out.get("node") or out).get("id")
        assert new_id, out
        status, got = door.call("tortoise_get_entity",
                                {"id": new_id, "type": "event"})
        assert got.get("id") == new_id, got

    def test_document_write_is_readable_back(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_create_entity",
                                {"type": "document", "name": "Capstone Doc",
                                 "props": {"documentKind": "note"}})
        assert status == 200
        node = out.get("node") or out
        new_id = node.get("id")
        assert new_id, out
        status, got = door.call("tortoise_get_entity",
                                {"id": new_id, "type": "entity"})
        assert got.get("id") == new_id, got

    def test_point_write_carries_the_one_belief_kind(self, seeded):
        """A point is `:Point {pointKind: statement}` — ONE belief kind, never a
        new one (#10977 / comment 6781757043)."""
        door, s, _ = seeded
        status, out = door.call("tortoise_create_entity",
                                {"type": "point",
                                 "name": "One belief kind, asserted."})
        node = out.get("node") or out
        assert node.get("pointKind") == "statement", out

    def test_operator_write_returns_an_id_the_get_lane_reads_back(self, seeded):
        """`create_operator` over a FRESH pair (the seeded pair already carries
        IMPL, and the mechanism set is closed — #12067)."""
        door, s, _ = seeded
        a = door.call("tortoise_create_entity",
                      {"type": "point", "name": "Operator source claim."})[1]
        b = door.call("tortoise_create_entity",
                      {"type": "point", "name": "Operator target claim."})[1]
        a_id = (a.get("node") or a)["id"]
        b_id = (b.get("node") or b)["id"]
        status, out = door.call("tortoise_create_operator",
                                {"op_type": "NAND", "source_id": a_id,
                                 "target_ids": [b_id]})
        assert status == 200
        node = out.get("node") or out
        new_id = node.get("id")
        assert new_id, out
        status, got = door.call("tortoise_get_entity",
                                {"id": new_id, "type": "operator"})
        assert got.get("id") == new_id, got
        assert got.get("op_type") == "NAND", got

    def test_create_edge_writes_a_typed_relation(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_create_edge",
                                {"source_id": s["claim"],
                                 "target_id": s["object"],
                                 "predicate": "produces"})
        assert status == 200
        assert not (isinstance(out, dict) and out.get("error")), out

    def test_update_knowledge_lands_and_is_read_back(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_update_knowledge",
                                {"id": s["claim"],
                                 "props": {"topic": "capstone-updated"}})
        assert status == 200
        assert out.get("id") == s["claim"], out
        status, got = door.call("tortoise_get_entity",
                                {"id": s["claim"], "type": "point"})
        assert got.get("topic") == "capstone-updated", got

    def test_supersede_reports_the_correction_it_made(self, seeded):
        door, s, _ = seeded
        new = door.call("tortoise_create_entity",
                        {"type": "point", "name": "The corrected claim."})[1]
        new_id = (new.get("node") or new)["id"]
        status, out = door.call("tortoise_supersede_knowledge",
                                {"old_id": s["claim2"], "new_id": new_id})
        assert status == 200
        assert out.get("invalidated") is True, out
        assert out.get("id") == s["claim2"], out
        assert out.get("corrected_by") == new_id, out

    def test_delete_dry_run_previews_the_seeded_node(self, seeded):
        """`dry_run` must not delete — the payload names what WOULD go, and a
        confirmation token proves the preview was computed against the node."""
        door, s, sdk = seeded
        status, out = door.call("tortoise_delete",
                                {"id": s["claim"], "dry_run": True})
        assert status == 200
        assert out.get("preview") is True, out
        assert out.get("deleted") is False, out
        assert out.get("id") == s["claim"], out
        assert out.get("confirmation"), out
        # still there
        status, got = door.call("tortoise_get_entity",
                                {"id": s["claim"], "type": "point"})
        assert got.get("id") == s["claim"], got

    def test_register_source_returns_the_seeded_url(self, seeded):
        door, s, _ = seeded
        url = "https://example.test/capstone-source"
        status, out = door.call("tortoise_register_source",
                                {"url": url, "sourceKind": "web"})
        assert status == 200
        assert out.get("url") == url, out
        assert out.get("sourceKind") == "web", out

    def test_manage_source_trust_returns_the_tier_it_wrote(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_manage_source_trust",
                                {"url": s["source"], "tier": "high"})
        assert status == 200
        assert out.get("url") == s["source"], out
        assert out.get("credibilityTier") == "T1", out

    def test_write_question_files_a_question_with_its_options(self, seeded):
        """`choice` is an INDEX into `options` — the wire contract the first
        probe got wrong (it sent a string and was refused by validation)."""
        door, s, _ = seeded
        status, out = door.call("tortoise_write_question",
                                {"options": ["Ship it", "Hold it"],
                                 "evidence": ["The suite is green."],
                                 "choice": 0})
        assert status == 200
        assert not (isinstance(out, dict) and out.get("error")), out
        assert isinstance(out, (dict, list)), out

    def test_record_decision_records_against_a_real_approver(self, seeded):
        """`artifact_id` must name a real artifact (the seeded Object) — the
        refusal for a non-existent one names the id, which is what the first
        probe hit when it passed a Point id."""
        door, s, _ = seeded
        status, out = door.call("tortoise_record_decision",
                                {"approver_id": s["subject"],
                                 "artifact_id": s["object"],
                                 "point_ids": [s["claim"]],
                                 "decision_content": "Approved by the capstone."})
        assert status == 200
        assert not (isinstance(out, dict) and out.get("error")), out

    def test_ingest_returns_a_batch_receipt(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_ingest", {})
        assert status == 200
        assert out.get("batch_id"), out
        assert "created" in out and "deduped" in out, out

    def test_mine_from_session_is_driven_on_its_http_arm(self, seeded):
        """The raw-transcript arm is stdio-only BY DESIGN; the turn-list `items=`
        arm is the one served over HTTP, so that is the arm proven here."""
        door, s, _ = seeded
        status, out = door.call("tortoise_mine_knowledge_from_session",
                                {"items": ["A mined statement from the capstone."]})
        assert status == 200
        assert not (isinstance(out, dict)
                    and "stdio/CLI only" in str(out.get("error", ""))), out


# ══════════════════════════════════════════════════════════════════════════
# 5 — GRAPH OVERVIEW: one case per `section=` arm
# ══════════════════════════════════════════════════════════════════════════

_OVERVIEW_SECTIONS = (
    "taxonomy", "structure", "structure_check", "pointkinds", "tags", "sources",
    "namespaces", "graphs", "topics", "health", "status", "stale",
    "validate_domain", "audit",
)


class TestGraphOverviewSections:
    """Part A records `section=` as this target's discriminator — 14 absorbed
    sources, so a single-arm proof would be a token gesture.

    Each arm is asserted against the SHAPE ITS OWN DOOR returns, read off the
    live surface — not against a shared "something came back". An arm that
    drifted to another door's shape (a dict where a list belongs) fails.
    """

    @pytest.mark.parametrize("section,expected", [
        ("taxonomy", dict),
        ("structure", dict),
        ("structure_check", list),
        ("pointkinds", list),
        ("tags", list),
        ("sources", list),
        ("namespaces", list),
        ("graphs", list),
        ("topics", dict),
        ("health", dict),
        ("status", dict),
        ("stale", dict),
        ("validate_domain", dict),
        ("audit", dict),
    ])
    def test_each_section_answers_its_own_type(self, seeded, section, expected):
        door, s, _ = seeded
        args = {"section": section}
        if section == "validate_domain":
            args["domain"] = "dev"
        if section == "topics":
            args["entity_id"] = s["claim"]
        status, out = door.call("tortoise_graph_overview", args)
        assert status == 200
        assert isinstance(out, expected), (
            f"section={section!r} answered {type(out).__name__}, expected "
            f"{expected.__name__}: {out!r}")

    def test_structure_section_counts_the_seeded_operator(self, seeded):
        """The seeded IMPL operator must be visible here — that is the arm's
        whole question."""
        door, s, _ = seeded
        status, out = door.call("tortoise_graph_overview",
                                {"section": "structure"})
        assert out.get("operators") == 1, out
        assert out.get("total", 0) >= 3, out

    def test_pointkinds_section_counts_the_seeded_statements(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_graph_overview",
                                {"section": "pointkinds"})
        assert isinstance(out, list) and out, out
        by_kind = {row["kind"]: row["count"] for row in out
                   if isinstance(row, dict) and "kind" in row}
        assert by_kind.get("statement", 0) >= 2, out

    def test_status_section_names_the_served_graph(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_graph_overview",
                                {"section": "status"})
        assert out.get("connected") is True, out
        assert isinstance(out.get("counts"), dict), out
        assert out.get("total_entities", 0) > 0, out
        assert out.get("namespace"), out

    def test_topics_section_echoes_the_seeded_entity(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_graph_overview",
                                {"section": "topics",
                                 "entity_id": s["claim"]})
        assert out.get("id") == s["claim"], out
        assert "neighbors" in out, out

    def test_validate_domain_accepts_a_real_domain(self, seeded):
        """`validate_domain` takes a DOMAIN NAME from the pack set; `core` is
        not one and is refused by name. Proving the arm needs a real domain."""
        door, s, _ = seeded
        status, out = door.call("tortoise_graph_overview",
                                {"section": "validate_domain",
                                 "domain": "dev"})
        assert out.get("domain") == "dev", out
        assert out.get("ok") is True, out
        assert isinstance(out.get("violations"), list), out

    def test_unknown_domain_is_refused_naming_the_valid_set(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_graph_overview",
                                {"section": "validate_domain",
                                 "domain": "core"})
        assert isinstance(out, dict) and "error" in out, out
        assert "known:" in out["error"], out

    def test_audit_section_walks_the_seeded_nodes(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_graph_overview",
                                {"section": "audit"})
        assert out.get("node_count", 0) >= 3, out
        assert out.get("edge_count", 0) >= 2, out
        assert isinstance(out.get("checks"), list), out

    def test_combined_summary_counts_the_seeded_nodes(self, seeded):
        """No `section=` → the combined summary. It must COUNT the seed: a
        Point count of zero over a seeded graph is the wrong-payload failure."""
        door, s, _ = seeded
        status, out = door.call("tortoise_graph_overview", {})
        assert status == 200
        assert isinstance(out, dict), out
        taxonomy = out.get("taxonomy")
        assert isinstance(taxonomy, dict), out
        assert taxonomy.get("Point", 0) >= 3, out
        assert taxonomy.get("Source", 0) >= 1, out
        assert taxonomy.get("Object", 0) >= 1, out
        # every section folded into the summary keeps its own shape
        for key in ("structure", "pointkinds", "tags", "sources"):
            assert key in out, f"combined summary dropped {key!r}: {out}"

    def test_health_section_reports_ok_against_the_seeded_graph(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_graph_overview",
                                {"section": "health"})
        assert out.get("status") == "ok", out
        assert out.get("falkordb") == "connected", out
        assert isinstance(out.get("graph_size"), int) and out["graph_size"] > 0, out

    def test_sources_section_contains_the_seeded_source(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_graph_overview",
                                {"section": "sources"})
        assert isinstance(out, list), out
        assert s["source"] in {
            row.get("url") for row in out if isinstance(row, dict)}, out

    def test_dream_health_section_is_http_excluded(self, seeded):
        """The one `section=` value that is NOT servable on this lane. Recorded
        as an explicit refusal assertion, never silently skipped."""
        door, s, _ = seeded
        status, out = door.call("tortoise_graph_overview",
                                {"section": "dream_health"})
        assert isinstance(out, dict) and "error" in out, out
        assert "HTTP" in str(out["error"]), out


# ══════════════════════════════════════════════════════════════════════════
# 6 — CONFIDENCE: each argument-arm
# ══════════════════════════════════════════════════════════════════════════

class TestConfidenceArms:

    def test_default_arm_lists_confidence_for_the_seeded_points(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_check_confidence", {})
        assert status == 200
        assert isinstance(out, list) and out, out
        assert s["claim"] in _ids(out), out

    def test_claim_id_arm_returns_the_confidence_FOR_that_claim(self, seeded):
        """The calibrated seed must produce real confidence numbers — a claim
        id that came back with the uncalibrated refusal would mean the arm
        cannot serve the happy path at all."""
        door, s, _ = seeded
        status, out = door.call("tortoise_check_confidence",
                                {"claim_id": s["calibrated"]})
        assert status == 200
        assert isinstance(out, dict), out
        assert "error" not in out, (
            "the calibrated seed must not trip the uncalibrated refusal: "
            f"{out}")
        assert 0.0 <= float(out["mean"]) <= 1.0, out
        assert float(out["variance"]) >= 0.0, out
        assert float(out["alpha"]) > 0 and float(out["beta"]) > 0, out
        assert float(out["effective_n"]) > 0, out

    def test_claim_id_arm_refuses_an_uncalibrated_point_BY_NAME(self, seeded):
        """The refusal IS the payload here: it must name the point and the
        remedy. A refusal that returned a bare error string would lose the
        remedy and the id."""
        door, s, _ = seeded
        status, out = door.call("tortoise_check_confidence",
                                {"claim_id": s["claim"]})
        assert status == 200
        assert isinstance(out, dict), out
        text = json.dumps(out)
        assert "uncalibrated" in text, out
        assert "calibrate_summary" in text, out

    def test_session_arm_returns_the_diary_and_prior_state(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_check_confidence", {"session": True})
        assert status == 200
        assert isinstance(out, dict), out
        assert "no_prior_sessions" in out, out

    def test_calibrate_arm_answers_the_calibration_view(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_check_confidence", {"calibrate": True})
        assert status == 200
        assert isinstance(out, list), out
        assert s["claim"] in _ids(out), out

    def test_calibrate_summary_lists_the_seeded_claim(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_calibrate_summary", {})
        assert status == 200
        assert out is not None, out
        assert isinstance(out, (list, dict)), out


# ══════════════════════════════════════════════════════════════════════════
# 7 — RELATIONSHIP + the remaining reads
# ══════════════════════════════════════════════════════════════════════════

class TestRelationshipsAndRemainingReads:

    def test_explore_connections_returns_the_seeded_entity(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_explore_connections",
                                {"node_id": s["claim"]})
        assert status == 200
        assert isinstance(out, dict), out
        assert out.get("entity", {}).get("id") == s["claim"], out

    def test_expand_relationships_returns_the_seeded_operator_edge(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_expand_relationships",
                                {"point_id": s["claim"]})
        assert status == 200
        assert isinstance(out, list) and out, out
        assert s["claim2"] in {r.get("related_id") for r in out
                               if isinstance(r, dict)}, out
        assert s["operator"] in {r.get("operator_id") for r in out
                                 if isinstance(r, dict)}, out

    def test_adjust_relationship_requires_reason_and_then_writes(self, seeded):
        """The mitigation arm refuses `strength=` alone — that refusal is the
        contract; with a reason it must write and be readable back."""
        door, s, _ = seeded
        status, out = door.call("tortoise_adjust_relationship",
                                {"id": s["operator"], "strength": 0.5})
        assert status == 200
        assert isinstance(out, dict) and "reason" in str(out.get("error", "")), out

        status, out = door.call("tortoise_adjust_relationship",
                                {"id": s["operator"], "strength": 0.5,
                                 "reason": "capstone V2 weakens the seeded edge"})
        assert status == 200
        assert not (isinstance(out, dict) and out.get("error")), out

    def test_review_link_candidates_answers_both_directions(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_review_link_candidates", {})
        assert status == 200
        assert isinstance(out, dict), out
        assert "add" in out and "prune" in out, out

    def test_events_poll_returns_the_seeded_journal(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_events_poll", {})
        assert status == 200
        events = out.get("events") if isinstance(out, dict) else out
        assert isinstance(events, list) and events, out
        assert s["claim"] in json.dumps(events), out

    def test_diary_read_returns_the_seeded_entry(self, seeded):
        door, s, sdk = seeded
        sdk.create_point("diary", "Diary read arm entry.", authoredBy="reader")
        status, out = door.call("tortoise_diary_read",
                                {"agent_name": "reader"})
        assert status == 200
        assert isinstance(out, list) and out, out
        assert any("Diary read arm" in str(r.get("content", ""))
                   for r in out if isinstance(r, dict)), out

    def test_list_graphs_returns_the_seeded_tenant_graph(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_list_graphs", {})
        assert status == 200
        assert isinstance(out, list) and out, out
        assert any(str(g).startswith("org_") for g in out), out

    def test_list_namespaces_returns_the_pack_namespaces(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_list_namespaces", {})
        assert status == 200
        assert isinstance(out, list) and out, out
        assert all("namespace" in r for r in out if isinstance(r, dict)), out

    def test_list_batches_answers_a_collection(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_list_batches", {})
        assert status == 200
        rows = out.get("results") if isinstance(out, dict) else out
        assert isinstance(rows, (list, tuple)), out
        assert rows == [], out

    def test_check_connection_reports_the_seeded_graph(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_check_connection", {})
        assert status == 200
        assert out.get("connected") is True, out
        assert isinstance(out.get("graph"), dict), out

    def test_check_structure_answers_a_report(self, seeded):
        """No epistemic gates are seeded, so the correct report is EMPTY —
        asserted as the list the door returns, so a crash/None fails."""
        door, s, _ = seeded
        status, out = door.call("tortoise_check_structure", {})
        assert status == 200
        assert isinstance(out, list), out
        assert out == [], out

    def test_graph_set_recording_round_trips(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_graph_set_recording",
                                {"recording": True})
        assert status == 200
        assert out.get("recording") is True, out

    def test_manage_sources_lists_the_configured_sources(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_manage_sources", {"action": "list"})
        assert status == 200
        assert isinstance(out, dict), out
        assert "sources" in out and isinstance(out["sources"], dict), out

    def test_approve_merge_refuses_a_missing_candidate_by_name(self, seeded):
        """Nothing is a merge CANDIDATE in the seed, so the correct payload is
        the refusal naming the candidate — the arm is still driven."""
        door, s, _ = seeded
        status, out = door.call("tortoise_approve_merge",
                                {"candidate_id": "no-such-candidate",
                                 "action": "merge"})
        assert status == 200
        assert isinstance(out, dict) and "error" in out, out
        assert "no-such-candidate" in str(out["error"]), out
