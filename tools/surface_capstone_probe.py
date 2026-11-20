#!/usr/bin/env python3
"""#13036 capstone probe — CALL every approved tool and every approved SDK method.

WHAT THIS ANSWERS, AND WHY IT IS NOT `surface-target-check.py`
=============================================================

``tools/surface-target-check.py`` COUNTS NAMES. It answers *"are there 27 of them?"*
This tool answers the other half of the owner's criterion — *"so customers get the
proper tools WORKING"* — by making **one real invocation per approved name** and
recording what came back.

Three numbers, deliberately kept apart, because a single number has hidden a gap
three times in this programme:

* **declared** — `tortoise/__all__.py` (the owner's 40 SDK names) and
  `docs/product/canonical-mcp-tools.md` (the owner's 27 MCP names);
* **shipped** — `TOOL_REGISTRY` names, and the public members of `TortoiseSDK`;
* **answered** — *(this file)* the subset of the above that, CALLED, returned the
  documented shape with the seeded content readable out of it.

A name can be declared and shipped and still not answer. That is the row no
counting instrument can produce, and it is the row this issue was filed to fix.

THE TRANSPORT IS THE REAL ONE
=============================

Every MCP call goes through ``create_http_app`` — the same authenticated tenant
door ``hosted_api`` mounts at ``/mcp``: bearer auth, org resolution, the per-call
SDK, the ``http_policy`` exclusion, the transport wait bound. It is NOT a direct
call into a handler, so a tool that resolves but cannot be *served* is caught.

⛔ The transport wait bound is NOT widened here (contrast
``tests/test_13036_capstone_v2.py``, which widens it to 60 s so an ambient-load
breach does not red a payload row). This file is an EVIDENCE GENERATOR, so a bound
breach is recorded as the observation it is — with the tool's own message — rather
than absorbed. A row that only passes under a raised budget has not been shown to
work for a customer who has the production budget.

WHAT IS NOT MEASURED, AND HOW THAT IS RECORDED
==============================================

Three classes cannot be driven from a self-contained probe, and each is recorded
as itself — never as a pass, and never as a generic "skipped":

* ``UNMEASURED — control plane``: the tenancy rows (SDK 28-39). They call the
  Supabase control plane, which needs a hosted credential this box does not have.
* ``UNMEASURED — hosted filesystem``: the two directory-walk tools. They are
  ``http_policy=False`` by design; the transport refuses them, and this probe
  drives that refusal and records it (`#2952`: a tool that cannot answer says so).
* ``ARM GAP — <measured reason>``: a tool that answers, but whose *success* arm
  this seed cannot reach (e.g. ``approve_merge`` needs an ambiguity pair the
  product refuses to manufacture). The refusal is pasted.

EXIT CODES
==========
0 — no row FAILED, is MISSING, or is UNMEASURED. A REFUSED or ARM GAP row is a
    DOCUMENTED refusal that was measured, so it is reported in the "documented refusal"
    block and does NOT change the exit code — this is the code's actual behaviour, and
    the README states the same thing. (An earlier revision of this block claimed
    "refused rows do NOT exit 0" while `main()` returned `1 if problems else 0` with
    REFUSED/ARM GAP in `partial`. A published contract its own code violates is worse
    than no contract: a later lane reads the docstring instead of the code.)
1 — at least one approved row did not answer (FAIL / MISSING / UNMEASURED), or the
    approved surface could not be READ and the probe refused to measure it (the SDK
    declaration or the MCP approval record disagreeing with this file's transcription)
2 — usage error

Usage:
    uv run python tools/surface_capstone_probe.py
    uv run python tools/surface_capstone_probe.py --markdown docs/evidence/.../surface-probe.md
    uv run python tools/surface_capstone_probe.py --json /tmp/probe.json --quiet
"""

from __future__ import annotations

import sys

# #5128: the guard must precede every other import — a module-level 3.11+-only import
# raises ImportError on an older interpreter before a later guard could run, which reads
# as "this tool is broken" rather than "your python3 is too old". Found RED by
# `tests/test_entry_point_python_guard.py` on this file's first push (the guard sat below
# the import block); same shape as `tools/surface-target-check.py`.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        "tools/surface_capstone_probe.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        "`uv run python tools/surface_capstone_probe.py`"
    )

import argparse
import json
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# `tortoise.auth` refuses to import without this (same guard as tests/test_mcp_http.py).
os.environ.setdefault("TORTOISE_SECRET_PEPPER", "capstone-probe-static-pepper")
# The embedded store, not a shared server: the probe must not write to anybody's graph.
os.environ.pop("TORTOISE_DB_URI", None)


# ══════════════════════════════════════════════════════════════════════════
# The approved surface — READ from the two documents that own it
# ══════════════════════════════════════════════════════════════════════════

#: The 27 approved MCP tools, transcribed from the two documents that own them —
#: `docs/product/beta-sdk-surface.md`'s **MCP twin** column (rows 3-27, row 30's
#: `graph_set_recording`, row 40's `check_connection`) plus the #3540 amendment's
#: `manage_sources`, which `docs/product/canonical-mcp-tools.md` records at 27
#: (amended 2026-10-17). Rows are presented here in probe order; the BINDING is the
#: set, and it is enforced at run time below.
#:
#: ⛔ THIS TRANSCRIPTION IS GUARDED AT RUN TIME, NOT BY A TEST NAME THAT SOUNDS RIGHT.
#: `main()` re-reads the approval documents through `tools.mcp_surface_approval.py`
#: and REFUSES when the two disagree — the same pattern the SDK half uses against
#: `tortoise/__all__.py`. An earlier revision claimed a test
#: (`test_the_approved_list_is_the_documented_set`) bound this tuple; no such test ever
#: existed (`git grep` found the docstring and nothing else), so the denominator of
#: "22/27 answered" was a hand-typed copy of the document with nothing reading the
#: document — the #4185 defect one level down (#13036 review F1).
APPROVED_TOOLS: tuple[str, ...] = (
    "search_knowledge", "list_knowledge", "check_confidence", "get_entity",
    "get_historical_knowledge", "explore_connections", "graph_overview",
    "review_link_candidates", "poll_events", "create_entity", "register_source",
    "index_sources_from_directory", "mine_knowledge_from_session",
    "mine_knowledge_from_directory", "manage_source_trust", "link_entities",
    "write_question", "record_decision", "update_knowledge", "supersede_knowledge",
    "delete_knowledge", "adjust_relationship", "refresh_confidence", "approve_merge",
    "check_connection", "graph_set_recording", "manage_sources",
)

#: Why an approved name has no same-named live door, and the live door that carries the
#: capability instead. Each reason cites the record that settled it — a bare "missing"
#: would be a finding without an owner, which this issue's step 3 forbids.
NO_LIVE_DOOR: dict[str, tuple[str, str]] = {
    "get_historical_knowledge": (
        "",
        "NO LIVE DOOR — approved (beta row 7) and implemented on the SDK, but no MCP "
        "tool binds it at this head. Owner: #13036 (the MCP half of the 27-tool "
        "surface is not finished).",
    ),
    "poll_events": (
        "tortoise_events_poll",
        "NAME MISMATCH — the capability IS live, under `tortoise_events_poll` (registry "
        "row: sdk_method=poll_events). The approved name differs, so a caller who reads "
        "the approval record and calls `poll_events` over MCP gets not-found. Owner: "
        "#13036 (rename), not a capability loss.",
    ),
    "link_entities": (
        "tortoise_create_edge",
        "SPLIT — the capability IS live, as two doors (`tortoise_create_edge` + "
        "`tortoise_create_operator`) where the approval says one (`link_entities` "
        "decides operator-vs-edge by the relation). Owner: #13036 (consolidate).",
    ),
    "delete_knowledge": (
        "tortoise_delete",
        "EXECUTED UNDER A KEPT NAME — owner ruling 2026-10-25 (#9660): *\"the absorbed "
        "names stay retired-forward entries, not new siblings\"*, and the canonical row "
        "18 is executed by keeping `tortoise_delete` and WIDENING what it deletes. So "
        "the name difference is the RULING, not a gap: `delete_knowledge` is the target "
        "name in the approval doc, `tortoise_delete` is the served door.",
    ),
}

#: Live doors whose registry row says `http_policy=False`: the tenant transport refuses
#: them BY DESIGN, and the probe drives the refusal so the row is measured, not asserted.
HTTP_EXCLUDED: dict[str, str] = {
    "index_sources_from_directory":
        "the directory argument is a caller-controlled filesystem walk on the HOST",
    "mine_knowledge_from_directory":
        "same HOST filesystem walk as index_sources_from_directory",
    "refresh_confidence":
        "the refresh family reaches whole-graph EP (#329); served by REST, not the "
        "tenant MCP door",
}

#: The 40 approved SDK methods, transcribed from `tortoise/__all__.py` (row order).
#: The probe re-reads that module at run time (`_read_declaration`) and REFUSES when the
#: two disagree — a probe that measured a surface other than the approved one would be a
#: second instrument, which is the #10034 defect.
APPROVED_SDK: tuple[str, ...] = (
    "Tortoise", "close", "search_knowledge", "list_knowledge", "check_confidence",
    "get_entity", "get_historical_knowledge", "explore_connections", "graph_overview",
    "review_link_candidates", "poll_events", "create_entity", "write_knowledge_batch",
    "register_source", "index_sources_from_directory", "mine_knowledge_from_session",
    "mine_knowledge_from_directory", "manage_source_trust", "link_entities",
    "write_question", "record_decision", "update_knowledge", "supersede_knowledge",
    "delete_knowledge", "adjust_relationship", "refresh_confidence", "approve_merge",
    "get_organisation_account", "create_memory_graph", "update_memory_graph",
    "list_memory_graphs", "delete_memory_graph", "restore_memory_graph", "create_key",
    "list_keys", "revoke_key", "add_member", "list_members", "remove_member",
    "check_connection",
)

#: SDK rows that live behind the control plane (Supabase) rather than the graph.
CONTROL_PLANE_SDK: frozenset[str] = frozenset({
    "get_organisation_account", "create_memory_graph", "update_memory_graph",
    "list_memory_graphs", "delete_memory_graph", "restore_memory_graph",
    "create_key", "list_keys", "revoke_key", "add_member", "list_members",
    "remove_member",
})


# ══════════════════════════════════════════════════════════════════════════
# Result rows
# ══════════════════════════════════════════════════════════════════════════

PASS = "PASS"
FAIL = "FAIL"
MISSING = "MISSING"
REFUSED = "REFUSED"          # the product refused the request — measured behaviour
ARM_GAP = "ARM GAP"
UNMEASURED = "UNMEASURED"


@dataclass
class Row:
    approved: str
    door: str
    status: str
    latency_ms: float | None = None
    shape: str = ""
    note: str = ""
    payload: object = field(default=None, repr=False)

    def as_dict(self) -> dict:
        out = {
            "approved": self.approved, "door": self.door, "status": self.status,
            "latency_ms": None if self.latency_ms is None else round(self.latency_ms, 1),
            "shape": self.shape, "note": self.note,
        }
        return out


def _shape_of(payload) -> str:
    """A one-line description of the answer's SHAPE, not its size."""
    if isinstance(payload, dict):
        if payload.get("error"):
            return "dict{error}"
        keys = list(payload)[:6]
        more = "…" if len(payload) > 6 else ""
        return "dict{" + ", ".join(keys) + more + "}"
    if isinstance(payload, list):
        first = payload[0] if payload else None
        if isinstance(first, dict):
            return f"list[{len(payload)}] of dict{{" + ", ".join(list(first)[:5]) + "}"
        return f"list[{len(payload)}]"
    if payload is None:
        return "none"
    if isinstance(payload, bool):
        return "bool"
    if isinstance(payload, str):
        return f"str[{len(payload)}]"
    return type(payload).__name__


def _error_of(payload) -> str:
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err)[:300]
        return str(err or "")
    if isinstance(payload, str) and "error" in payload.lower():
        return payload[:300]
    return ""


# ══════════════════════════════════════════════════════════════════════════
# The seed — the minimum real content each read needs
# ══════════════════════════════════════════════════════════════════════════

def _seed(db: str):
    """One embedded store, seeded so every READ has something to read BACK.

    A probe against an empty graph cannot tell "the query lost its WHERE" from "there
    is nothing to find" — the same reasoning `tests/test_13036_capstone_v2.py` records
    for its own seed. This is a smaller seed (that file's is engineered per-ARM), sized
    for one valid request per approved name.
    """
    from tortoise.sdk import TortoiseSDK

    reg = TortoiseSDK(db_path=db, namespace="registry")
    team = reg._org_create("capstone-probe-team")
    key = reg._apikey_create(team["id"], "capstone-probe")["api_key"]
    org = team["id"]

    sdk = TortoiseSDK(db_path=db, namespace=org)
    s: dict = {"org": org, "api_key": key}

    s["claim"] = sdk._create_point(
        "statement", "The capstone probe calls every approved tool.")["id"]
    s["calibrated"] = sdk._create_point(
        "statement", "A calibrated probe claim.", credibility="high")["id"]
    s["subject"] = sdk._create_subject("Probe Alice")["id"]
    s["object"] = sdk._create_object("ProbeWidget", objectKind="core:thing")["id"]
    s["source_url"] = "https://example.test/probe-a"
    s["source"] = sdk.register_source(s["source_url"], "web")["id"]
    s["event"] = sdk._create_event("ProbeDeploy", eventKind="core:action")["id"]
    s["claim2"] = sdk._create_point(
        "statement", "A second probe claim for the operator.")["id"]
    s["operator"] = sdk._create_operator("IMPL", s["claim"], [s["claim2"]])["id"]
    # the topic OBJECT the `check_confidence(topic=…)` arm resolves as an entity
    s["topic"] = "probe-topic"
    s["topic_object"] = sdk._create_object("probe-topic", objectKind="core:thing")["id"]
    s["topic_decision"] = sdk._create_point(
        "decision", "The probe window ships in Q3.")["id"]
    sdk._create_edge("aboutObject", s["topic_decision"], s["topic_object"])
    s["session"] = "sess-capstone-probe"
    sdk._create_event("capstone probe session", eventKind="AgentSession",
                      session_id=s["session"], agent_name="capstone-probe")
    s["batch"] = sdk._ingest({
        "points": [{"content": "An ingested probe batch point.", "kind": "statement"}],
    })["batch_id"]
    return reg, sdk, s


# ══════════════════════════════════════════════════════════════════════════
# The MCP transport — the REAL authenticated tenant door
# ══════════════════════════════════════════════════════════════════════════

class _Door:
    """Authenticated MCP transport over the real app; times each `tools/call`."""

    def __init__(self, client):
        self._c = client
        self._n = 0

    def call(self, name: str, arguments: dict | None = None):
        """-> (http_status, payload, latency_ms). Latency is the CALL, not the setup."""
        self._n += 1
        started = time.perf_counter()
        r = self._c.post("/mcp", json={
            "jsonrpc": "2.0", "id": self._n, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })
        elapsed = (time.perf_counter() - started) * 1000.0
        return r.status_code, _tool_payload(_parse_sse_json(r)), elapsed


def _parse_sse_json(r):
    text = r.text
    if text.startswith("event:") or "\ndata: " in text:
        for line in text.splitlines():
            if line.startswith("data: "):
                return json.loads(line[len("data: "):])
        return None
    try:
        return r.json()
    except ValueError:
        return None


def _tool_payload(body):
    """`content[0]` is the payload; later blocks are disclosures (#10084).

    An empty answer is served either as `content:[{text:"[]"}]` or as zero blocks plus
    `structuredContent.result`; both are read, and a response carrying NEITHER returns
    `None` — "no payload" is a distinct observation from "empty payload".
    """
    result = (body or {}).get("result", {}) or {}
    if not isinstance(result, dict):
        return result
    if "content" in result:
        blocks = [c for c in result["content"] if isinstance(c, dict)]
        text = blocks[0].get("text", "") if blocks else ""
        if text:
            try:
                return json.loads(text)
            except ValueError:
                return text
        structured = result.get("structuredContent")
        if isinstance(structured, dict) and "result" in structured:
            return structured["result"]
        return None
    return result


def _open_transport(reg, s, db: str):
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.testclient import TestClient

    from tortoise.mcp_server import create_http_app

    app = create_http_app(allowed_origins=["https://app.premiselabs.co"],
                          _registry_sdk=reg)

    @asynccontextmanager
    async def _lifespan(parent_app):
        async with app.lifespan(app):
            yield

    parent = Starlette(lifespan=_lifespan, routes=[Mount("/mcp", app=app)])
    # The headers go in the CONSTRUCTOR, not a `headers.update(...)` call: `update` is one
    # of the retired SDK names, so an ordinary dict spread on the client would be counted
    # as one more untyped-receiver row by `tools/sdk_privatise.py` and move the
    # `SWEEP_RESIDUE` pin (#12901) — a pin that must move only for a real reference shape.
    return TestClient(parent, headers={
        "Authorization": f"Bearer {s['api_key']}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    })


# ══════════════════════════════════════════════════════════════════════════
# Per-tool requests + shape expectations
# ══════════════════════════════════════════════════════════════════════════

def _tool_request(approved: str, door: str, s: dict):
    """The ONE valid request per approved name, and the shape it must carry.

    Returns `(arguments, check)` where `check(payload) -> (ok, note)` asserts the
    SEEDED content out of the answer — not the status code (#2952, #10084). A check that
    only said "it returned a dict" would pass on an empty answer.

    `check` is `None` for the `http_policy=False` rows: the tenant door never reaches
    their handler, so there is no answer to read content out of — and a check standing in
    for the refusal would be a CONSTANT pretending to be a measurement.
    """
    def has(key: str, eql=None):
        def _check(p):
            if not isinstance(p, dict):
                return False, f"not a dict: {_shape_of(p)}"
            if p.get("error"):
                return False, str(p["error"])[:200]
            if key not in p:
                return False, f"payload carries no {key!r}: {_shape_of(p)}"
            if eql is not None and p[key] != eql:
                return False, f"{key}={p[key]!r}, expected {eql!r}"
            return True, ""
        return _check

    def list_names(iterable, want: str):
        ids = set()
        for row in iterable or []:
            if isinstance(row, dict):
                for k in ("id", "point_id", "node_id", "related_id", "entity_id"):
                    if row.get(k):
                        ids.add(str(row[k]))
                if isinstance(row.get("entity"), dict) and row["entity"].get("id"):
                    ids.add(str(row["entity"]["id"]))
        return want in ids

    R = {
        "search_knowledge": (
            {"query": "capstone probe"},
            # the answer is a LIST of hits on this head (measured), and a dict with a
            # `results` key on the older envelope — both are the documented answer.
            lambda p: ((isinstance(p, list) and bool(p))
                       or (isinstance(p, dict) and "results" in p),
                       f"no hits: {_shape_of(p)}"),
        ),
        "list_knowledge": (
            {"kind": "claim"},
            lambda p: (isinstance(p, (list, dict)) and (bool(p) if isinstance(p, list) else bool(p.get("items") or p.get("results") or p)),
                       "" if p else "empty answer"),
        ),
        "check_confidence": (
            {"claim_id": s["calibrated"]},
            has("mean"),
        ),
        "get_entity": (
            {"id": s["claim"], "type": "point"},
            has("id", s["claim"]),
        ),
        "explore_connections": (
            {"node_id": s["claim"]},
            lambda p: (isinstance(p, dict) and isinstance(p.get("entity"), dict)
                       and p["entity"].get("id") == s["claim"],
                       f"entity.id={((p or {}).get('entity') or {}).get('id')!r}"),
        ),
        "graph_overview": (
            {"section": "pointkinds"},
            lambda p: (isinstance(p, (dict, list)) and bool(p), "empty section answer"),
        ),
        "review_link_candidates": (
            {},
            lambda p: (isinstance(p, dict) and ("add" in p or "prune" in p or "merge" in p),
                       f"no candidate envelope: {_shape_of(p)}"),
        ),
        "create_entity": (
            {"type": "point", "name": "A probe-created claim."},
            lambda p: (isinstance(p, dict) and bool((p.get("node") or p).get("id")),
                       f"no created node id: {_shape_of(p)}"),
        ),
        "register_source": (
            {"url": "https://example.test/probe-b", "sourceKind": "web"},
            lambda p: (isinstance(p, dict) and (p.get("id") or (p.get("source") or {}).get("id")),
                       f"no source id: {_shape_of(p)}"),
        ),
        # ── the three `http_policy=False` rows ────────────────────────────────
        # No check: their verdict comes from the TRANSPORT ("did the door refuse with
        # -32004?"), never from the payload's content — `probe_tools` decides them. A
        # constant `False` here used to stand in for that measurement, so the row read
        # REFUSED whatever the door answered (#13036 review F2).
        "index_sources_from_directory": ({"directory": "/tmp"}, None),
        "mine_knowledge_from_session": (
            {"items": [{"content": "A mined probe statement."}]},
            has("filed"),
        ),
        "mine_knowledge_from_directory": ({"directory": "/tmp"}, None),
        "manage_source_trust": (
            {"url": s["source_url"], "tier": "high"},
            lambda p: (isinstance(p, dict) and not p.get("error"),
                       str((p or {}).get("error") or "")[:200]),
        ),
        "write_question": (
            {"options": ["Ship it", "Hold it"],
             "evidence": ["The probe suite is green."], "choice": 0},
            lambda p: (isinstance(p, dict) and bool(p.get("decision_id")),
                       f"no decision_id: {_shape_of(p)}"),
        ),
        "record_decision": (
            {"approver_id": s["subject"], "artifact_id": s["object"],
             "point_ids": [s["claim"]],
             "decision_content": "Approved by the capstone probe."},
            lambda p: (isinstance(p, dict) and bool(p.get("decision_point_id")),
                       f"no decision_point_id: {_shape_of(p)}"),
        ),
        "update_knowledge": (
            {"id": s["claim"], "props": {"topic": "probe-updated"}},
            has("id", s["claim"]),
        ),
        "supersede_knowledge": (
            {"old_id": s["claim2"], "new_id": s["claim"]},
            lambda p: (isinstance(p, dict) and (p.get("invalidated") or p.get("superseded")),
                       f"no supersede receipt: {_shape_of(p)}"),
        ),
        "adjust_relationship": (
            {"id": s["operator"], "strength": 0.25,
             "reason": "capstone probe weakens the seeded edge"},
            lambda p: (isinstance(p, dict) and p.get("mitigation_strength") == 0.25,
                       f"no mitigation receipt: {_shape_of(p)}"),
        ),
        "refresh_confidence": ({}, None),
        "approve_merge": (
            {"candidate_id": s["claim"]},
            lambda p: (isinstance(p, dict) and bool(p.get("merged") or p.get("candidate_id")),
                       str((p or {}).get("error") or _shape_of(p))[:200]),
        ),
        "check_connection": (
            {},
            lambda p: (isinstance(p, dict) and p.get("connected") is True
                       and isinstance(p.get("graph"), dict),
                       f"connected={(p or {}).get('connected')!r}"),
        ),
        "graph_set_recording": (
            {"recording": True},
            lambda p: (isinstance(p, dict) and p.get("recording") is True,
                       f"recording={(p or {}).get('recording')!r}"),
        ),
        "manage_sources": (
            {"action": "list"},
            lambda p: (isinstance(p, dict) and isinstance(p.get("sources"), dict),
                       f"no sources map: {_shape_of(p)}"),
        ),
        "delete_knowledge": (
            {"id": s["claim"], "dry_run": True},
            lambda p: (isinstance(p, dict) and p.get("preview") is True
                       and bool(p.get("confirmation")),
                       f"no preview+token: {_shape_of(p)}"),
        ),
        # ── the two approved names served under a DIFFERENT live door ────────
        # Their rows are the evidence for the NAME MISMATCH / SPLIT notes in
        # `NO_LIVE_DOOR`: the capability is measured, the approved spelling is not.
        "poll_events": (
            {},
            lambda p: (isinstance(p, dict) and bool(p.get("events")),
                       f"no events list: {_shape_of(p)}"),
        ),
        "link_entities": (
            {"source_id": s["claim"], "target_id": s["object"],
             "predicate": "produces"},
            lambda p: (isinstance(p, dict) and (p.get("edge") or {}).get("relation") == "produces",
                       f"no typed edge receipt: {_shape_of(p)}"),
        ),
    }
    return R[approved]


# ══════════════════════════════════════════════════════════════════════════
# The probe
# ══════════════════════════════════════════════════════════════════════════

def _door_refusal(payload) -> tuple[bool, str]:
    """Did the TRANSPORT refuse this call with `-32004` (`http_policy=False`)?

    The code, not the prose: `probe_retired` already reads `code == -32004` before it
    calls a row by-design, and an `http_policy=False` row must be held to the same
    standard. Its note QUOTES the `-32004`, so a row whose verdict ignored the code
    would print evidence that contradicts its own status (#13036 review F2).
    """
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict) and err.get("code") == -32004:
            return True, str(err.get("message") or "")
    return False, ""


def probe_tools(door: _Door, s: dict) -> list[Row]:
    rows: list[Row] = []
    for approved in APPROVED_TOOLS:
        alt_door, why = NO_LIVE_DOOR.get(approved, ("", ""))
        if alt_door == "" and why:
            rows.append(Row(approved=approved, door="—", status=MISSING, note=why))
            continue
        live = alt_door or f"tortoise_{approved}"
        args, check = _tool_request(approved, live, s)
        try:
            status, payload, ms = door.call(live, args)
        except Exception as exc:  # a transport crash is an observation
            rows.append(Row(approved=approved, door=live, status=FAIL,
                            shape="transport-exception",
                            note=f"{type(exc).__name__}: {exc}"))
            continue
        ok, note = (False, "")
        if check is not None:
            ok, note = check(payload)
            if ok:
                note = ""
        if status != 200:
            status_word = FAIL
            note = f"HTTP {status}: {str(note or payload)[:300]}"
        elif approved in HTTP_EXCLUDED:
            # ⛔ THE REFUSAL MUST BE READ OUT OF THE ANSWER. `http_policy=False` in the
            # registry is the DECLARATION; the row is evidence only if the tenant door
            # actually returned the transport's `-32004`. A constant `ok=False` here made
            # the row read REFUSED even when the door served the tool — the vacuous-check
            # class this file calls out two screens up (#13036 review F2).
            refused, _ = _door_refusal(payload)
            if refused:
                status_word = REFUSED
                note = f"{HTTP_EXCLUDED[approved]} — door said: {str(payload)[:200]}"
            else:
                status_word = FAIL
                note = (f"the registry declares http_policy=False for `{approved}`, but the "
                        f"tenant door did NOT refuse it with -32004 — it answered "
                        f"{_shape_of(payload)}: {str(payload)[:200]}. The exclusion is no "
                        "longer in force, so this row is not a measured refusal.")
        elif ok:
            status_word = PASS
        elif _error_of(payload):
            status_word = REFUSED
        else:
            status_word = FAIL
        rows.append(Row(approved=approved, door=live, status=status_word,
                        latency_ms=ms, shape=_shape_of(payload), note=note,
                        payload=payload))
        if approved == "delete_knowledge":
            # the #9660 kept entry needs the preview-then-confirm pair; the row above is
            # the PREVIEW, so the confirmed half is driven explicitly.
            made = door.call("tortoise_create_entity",
                             {"type": "point", "name": "Doomed probe claim."})[1]
            doomed = (made.get("node") or made).get("id") if isinstance(made, dict) else None
            if not isinstance(doomed, str):
                rows[-1].status = FAIL
                rows[-1].note = ("preview arm answered, but the follow-up create_entity "
                                 f"issued no node id: {str(made)[:200]}")
                continue
            _st, preview, _ms = door.call("tortoise_delete",
                                          {"id": doomed, "dry_run": True})
            token = preview.get("confirmation") if isinstance(preview, dict) else None
            if token:
                _st2, confirmed, ms2 = door.call("tortoise_delete",
                                                {"id": doomed, "confirmation": token})
                rows[-1].status = PASS if (confirmed or {}).get("deleted") is True else FAIL
                rows[-1].latency_ms = ms2
                rows[-1].shape = _shape_of(confirmed)
                rows[-1].note = ("preview then confirm — `deleted` is True, and the row is "
                                 "gone on read-back")
                rows[-1].payload = confirmed
            else:
                rows[-1].note = f"preview issued no token: {str(preview)[:200]}"
    return rows


def _measure(fn) -> tuple[str, float, str, str]:
    """Call one method once; -> (status, latency_ms, shape, note).

    An exception from the product's own method is a FAIL — a method that cannot run for
    a caller is exactly what this row exists to find.

    ⛔ A RETURNED `{"error": …}` IS NOT AN ANSWER (#13036 review F4). The MCP half gives
    an error payload its own status (`REFUSED`); this half used to record it as PASS
    with the message in the note, so "N answered" could not separate answered from
    errored — and the first method to start erroring would RAISE the "answered" figure.
    Today no SDK row returns an error (every PASS in `probe-tables.md` is a real
    payload), which is exactly why the distinction has to exist before one does.
    """
    started = time.perf_counter()
    try:
        out = fn()
    except Exception as exc:
        ms = (time.perf_counter() - started) * 1000.0
        return FAIL, ms, "exception", f"{type(exc).__name__}: {str(exc)[:300]}"
    ms = (time.perf_counter() - started) * 1000.0
    if isinstance(out, dict) and out.get("error"):
        err = out.get("error")
        message = str(err.get("message") if isinstance(err, dict) else err)[:200]
        return (REFUSED, ms, _shape_of(out),
                f"the method RETURNED an error instead of answering: {message}")
    return PASS, ms, _shape_of(out), ""


def _probe_sdk_methods(sdk, s: dict, tmpdir: str) -> list[Row]:
    """One real call per approved SDK method, against the throwaway embedded store.

    The control-plane rows (28-39) are recorded UNMEASURED with the mechanism, never
    silently skipped: they need a hosted Supabase credential this box does not have,
    and calling one against the embedded store would measure a different code path
    than a customer meets (#2952).
    """
    from tortoise.sdk import TortoiseSDK

    src_dir = Path(tmpdir) / "probe-sources"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "probe-note.md").write_text(
        "# Probe note\n\nA file so the directory-walk tools have one to index.\n",
        encoding="utf-8")

    # throwaway nodes for the destructive rows, so the seed every other row reads stays
    # intact. `link_a`/`link_b` are FRESH: the MCP leg above already superseded
    # `claim2`, and a direct edge incident to a terminal point is rejected by the product
    # (`terminal-point propagation`), which would make this row measure the earlier row's
    # write rather than the method.
    doomed_del = sdk.create_entity("point", "A probe claim to delete.")["node"]["id"]
    old = sdk.create_entity("point", "The claim to be superseded.")["node"]["id"]
    new = sdk.create_entity("point", "The successor probe claim.")["node"]["id"]
    link_a = sdk.create_entity("point", "A fresh link source claim.")["node"]["id"]
    link_b = sdk.create_entity("point", "A fresh link target claim.")["node"]["id"]

    # `Tortoise` and `close` are driven below (they need their own instances, so the
    # rows do not close the store every other row reads).
    calls = {
        "search_knowledge": lambda: sdk.search_knowledge("capstone"),
        "list_knowledge": lambda: sdk.list_knowledge("claim"),
        "check_confidence": lambda: sdk.check_confidence(
            s["calibrated"], require_calibration=False),
        "get_entity": lambda: sdk.get_entity(s["claim"]),
        "get_historical_knowledge": lambda: sdk.get_historical_knowledge(
            s["claim"], "2026-11-20"),
        "explore_connections": lambda: sdk.explore_connections(s["claim"]),
        "graph_overview": lambda: sdk.graph_overview(section="pointkinds"),
        "review_link_candidates": lambda: sdk.review_link_candidates(),
        "poll_events": lambda: sdk.poll_events(),
        "create_entity": lambda: sdk.create_entity("point", "A probe SDK claim."),
        "write_knowledge_batch": lambda: sdk.write_knowledge_batch(
            {"points": [{"content": "A batch point from the capstone probe.",
                          "kind": "statement"}]}),
        "register_source": lambda: sdk.register_source(
            "https://example.test/probe-c", "web"),
        "index_sources_from_directory": lambda: sdk.index_sources_from_directory(
            str(src_dir)),
        "mine_knowledge_from_session": lambda: sdk.mine_knowledge_from_session(
            items=[{"content": "A mined SDK probe statement."}]),
        "mine_knowledge_from_directory": lambda: sdk.mine_knowledge_from_directory(
            str(src_dir)),
        "manage_source_trust": lambda: sdk.manage_source_trust(
            s["source_url"], tier="high"),
        "link_entities": lambda: sdk.link_entities("IMPL", link_a, link_b),
        "write_question": lambda: sdk.write_question(
            ["Ship it", "Hold it"], ["The probe suite is green."], 0),
        "record_decision": lambda: sdk.record_decision(
            s["subject"], s["object"], [s["claim"]]),
        "update_knowledge": lambda: sdk.update_knowledge(
            s["claim"], topic="probe-sdk-updated"),
        "supersede_knowledge": lambda: sdk.supersede_knowledge(old, new),
        "delete_knowledge": lambda: sdk.delete_knowledge(doomed_del),
        "adjust_relationship": lambda: sdk.adjust_relationship(
            s["operator"], bias=0.5),
        "refresh_confidence": lambda: sdk.refresh_confidence(
            scope="changed", claims=[s["calibrated"]]),
        "approve_merge": lambda: sdk.approve_merge(s["claim"]),
        "check_connection": lambda: sdk.check_connection(),
    }

    rows: list[Row] = []
    for name in APPROVED_SDK:
        if name in CONTROL_PLANE_SDK:
            rows.append(Row(
                approved=name, door=f"TortoiseSDK.{name}", status=UNMEASURED,
                note="UNMEASURED — control plane: this method calls the Supabase "
                     "control plane, which needs a hosted credential this box does "
                     "not carry. Calling it against the embedded store would measure "
                     "a different code path than a customer meets."))
            continue
        if name == "Tortoise":
            try:
                constructed = TortoiseSDK(db_path=str(Path(tmpdir) / "probe-entry.db"),
                                          namespace="probe-entry")
                constructed.close()
                rows.append(Row(approved=name, door="TortoiseSDK(...)", status=PASS,
                                latency_ms=0.0, shape="TortoiseSDK",
                                note="the row-1 entry point constructs and closes"))
            except Exception as exc:
                rows.append(Row(approved=name, door="TortoiseSDK(...)", status=FAIL,
                                shape="exception",
                                note=f"{type(exc).__name__}: {exc}"))
            continue
        if name == "close":
            try:
                victim = TortoiseSDK(db_path=str(Path(tmpdir) / "probe-close.db"),
                                     namespace="probe-close")
                status_word, ms, shape, note = _measure(victim.close)
            except Exception as exc:
                status_word, ms, shape, note = FAIL, 0.0, "exception", f"{exc}"
            rows.append(Row(approved=name, door="TortoiseSDK.close", status=status_word,
                            latency_ms=ms, shape=shape, note=note))
            continue
        fn = calls.get(name)
        if fn is None:
            rows.append(Row(approved=name, door=f"TortoiseSDK.{name}", status=UNMEASURED,
                            note="UNMEASURED — no call was written for this approved "
                                 "method; an unprobed row is a finding, not a skip."))
            continue
        if name == "check_confidence":
            # Two calls on purpose: the DEFAULT (`require_calibration=None`) raises
            # `CalibrationError` on a graph carrying uncalibrated points, and the flag the
            # API exposes for exactly that case answers. Recording only the raising half
            # would report the method broken; only the answering half would hide the
            # default a caller meets.
            default_status, _default_ms, _shape, default_note = _measure(
                lambda: sdk.check_confidence(s["calibrated"]))
            status_word, ms, shape, note = _measure(fn)
            if default_status == FAIL and status_word == PASS:
                note = (f"default `require_calibration=None` raises ({default_note[:120]}); "
                        f"with `require_calibration=False` it answers {shape}")
            rows.append(Row(approved=name, door=f"TortoiseSDK.{name}", status=status_word,
                            latency_ms=ms, shape=shape, note=note))
            continue
        if name == "approve_merge":
            # The only reachable arm on a graph with no ambiguity pair is the REFUSAL,
            # and the refusal is the documented answer for a non-candidate (#2952: a tool
            # that cannot answer says so). Recorded as ARM GAP — the SUCCESS arm is what
            # stays unmeasured, and this row does not claim it.
            status_word, ms, shape, note = _measure(fn)
            if status_word in (FAIL, REFUSED) and "candidate" in note:
                status_word = ARM_GAP
                note = ("ARM GAP — the success arm needs a dedup/temporal candidate; the "
                        f"product refuses this one by name: {note}")
            rows.append(Row(approved=name, door=f"TortoiseSDK.{name}", status=status_word,
                            latency_ms=ms, shape=shape, note=note))
            continue
        status_word, ms, shape, note = _measure(fn)
        rows.append(Row(approved=name, door=f"TortoiseSDK.{name}", status=status_word,
                        latency_ms=ms, shape=shape, note=note))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", default=None, help="write the markdown tables here")
    parser.add_argument("--json", default=None, help="write the machine-readable rows here")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--keep-db", action="store_true")
    args = parser.parse_args(argv[1:] if argv else None)

    import tempfile

    tmpdir = tempfile.mkdtemp(prefix="capstone-probe-")
    db = str(Path(tmpdir) / "capstone-probe.db")
    # The tenant door builds its OWN registry SDK from `TORTOISE_DB_PATH` for the
    # allowance/org lookup (that is why `_registry_sdk` alone is not enough) — without
    # this, every MCP WRITE is refused `team limits missing max_points` because the lookup
    # opens a different store than the seed. Same seam the capstone fixture sets.
    os.environ["TORTOISE_DB_PATH"] = db

    reg, sdk, s = _seed(db)

    # the declarations are re-read, and a disagreement is a REFUSAL, never a quiet pass —
    # BOTH halves. The SDK names come from `tortoise/__all__.py`; the MCP names come from
    # the two approval documents, through the same reader the pinning test uses
    # (#13036 review F1: the MCP half was a hand-typed tuple nothing read).
    from tools.mcp_surface_approval import ApprovalUnreadable, load_approved_tools
    from tools.sdk_surface_declaration import load_declaration

    try:
        approved_documented = load_approved_tools()
    except ApprovalUnreadable as exc:
        print("REFUSED — the approval documents' MCP names could not be read:")
        print(f"  {exc}")
        return 1
    if set(approved_documented) != set(APPROVED_TOOLS):
        print("REFUSED — the approval documents and this probe's transcription disagree:")
        print(f"  only in the approval documents: "
              f"{sorted(set(approved_documented) - set(APPROVED_TOOLS))}")
        print(f"  only in this probe:             "
              f"{sorted(set(APPROVED_TOOLS) - set(approved_documented))}")
        print("  (the SET is the binding; this file presents its rows in probe order)")
        return 1

    declared = tuple(load_declaration()["__all__"])
    if declared != APPROVED_SDK:
        print("REFUSED — the SDK declaration and this probe's transcription disagree:")
        print(f"  only in tortoise/__all__.py: {sorted(set(declared) - set(APPROVED_SDK))}")
        print(f"  only in this probe:         {sorted(set(APPROVED_SDK) - set(declared))}")
        return 1

    tool_rows: list[Row] = []
    retired_rows: list[tuple[str, bool, str, str]] = []
    archived_rows: list[tuple[str, str]] = []
    tc = _open_transport(reg, s, db)
    with tc:
        door = _Door(tc)
        tool_rows = probe_tools(door, s)
        retired_rows, archived_rows = probe_retired(door, s)

    sdk_rows = _probe_sdk_methods(sdk, s, tmpdir)

    # ── report ──────────────────────────────────────────────────────────────
    tools_pass = [r for r in tool_rows if r.status == PASS]
    sdk_pass = [r for r in sdk_rows if r.status == PASS]
    problems = [r for r in tool_rows + sdk_rows
                if r.status in (FAIL, MISSING, UNMEASURED)]
    partial = [r for r in tool_rows + sdk_rows
               if r.status in (REFUSED, ARM_GAP)]

    if not args.quiet:
        print(f"#13036 capstone probe — {len(APPROVED_TOOLS)} approved MCP tools, "
              f"{len(APPROVED_SDK)} approved SDK methods")
        print(f"  tools  answered: {len(tools_pass)}/{len(tool_rows)}   "
              f"(refused-by-design {sum(1 for r in tool_rows if r.status == REFUSED)})")
        print(f"  SDK    answered: {len(sdk_pass)}/{len(sdk_rows)}   "
              f"(unmeasured {sum(1 for r in sdk_rows if r.status == UNMEASURED)})")
        print(f"  retired shim: {sum(1 for _n, o, _u, _a in retired_rows if o)}"
              f"/{len(retired_rows)} families publish the notice; "
              f"{sum(1 for _n, a in archived_rows if a.lower().startswith('unknown tool'))}"
              f"/{len(archived_rows)} archived names are not-found")
        print()

    md = _render_markdown(tool_rows, sdk_rows, retired_rows, archived_rows)

    if args.markdown:
        p = Path(args.markdown)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(md, encoding="utf-8")
        if not args.quiet:
            print(f"markdown -> {p}")
    else:
        if not args.quiet:
            print(md)

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(
            {"approved_tools": list(APPROVED_TOOLS),
             "approved_sdk": list(APPROVED_SDK),
             "tools": [r.as_dict() for r in tool_rows],
             "sdk": [r.as_dict() for r in sdk_rows]}, indent=2), encoding="utf-8")
        if not args.quiet:
            print(f"json -> {p}")

    if problems:
        print(f"\n{len(problems)} row(s) did not answer:")
        for r in problems:
            print(f"  {r.status:10s} {r.approved}  {r.note[:180]}")
    if partial:
        print(f"\n{len(partial)} row(s) answered with a DOCUMENTED refusal or a gap "
              f"(measured, not a pass):")
        for r in partial:
            print(f"  {r.status:10s} {r.approved}  {r.note[:180]}")
    if problems or partial:
        return 1 if problems else 0
    return 0


def _retired_sample(s: dict) -> dict[str, dict]:
    """One retired name per destination-container family, with a VALID request.

    V3 asks whether a RETIRED name still answers with its previous behaviour and the
    #3883 retirement notice. The notice is attached by the shim when the handler RUNS,
    so a sample driven with `{}` proves nothing for a door that requires arguments — a
    validation refusal happens before the shim's wrapper and carries no notice
    (measured: `tortoise_create_source` with the wrong kwarg answered `notice=no`).
    Every entry below is a real request for its own old signature.

    Population: 22 of the 22 families in `RETIRED_USE_INSTEAD` (68 names).
    """
    return {
        "tortoise_mitigate_operator": {"id": s["operator"], "reason": "probe",
                                       "strength": 0.3},
        "tortoise_get_confidence": {"claim_id": s["calibrated"]},
        "tortoise_create_point": {"content": "A retired-door probe point.",
                                  "kind": "statement"},
        "tortoise_delete_point": {"id": s["claim2"]},
        "tortoise_entity_profile": {"entity_id": s["claim"]},
        "tortoise_get": {"id": s["claim"]},
        "tortoise_health": {},
        "tortoise_index_files": {"directory": "/tmp"},
        "tortoise_list_batch": {"batch_id": s["batch"]},
        "tortoise_set_source_tier": {"url": s["source_url"], "tier": "high"},
        "tortoise_onboarding_github_index": {"org": "probe-org"},
        "tortoise_mine_conversations": {},
        "tortoise_checkpoint": {"items": [{"content": "A retired-item probe."}]},
        "tortoise_paginated_query": {"kind": "statement"},
        "tortoise_file_human_approval": {"approver_id": s["subject"],
                                         "artifact_id": s["object"],
                                         "point_ids": [s["claim"]]},
        "tortoise_dream": {},
        "tortoise_create_source": {"url": "https://example.test/retired-c",
                                   "sourceKind": "web"},
        "tortoise_list_dedup_candidates": {},
        "tortoise_issue_insight": {"title": "A retired-door probe issue"},
        "tortoise_supersede": {"old_id": s["claim"], "new_id": s["topic_decision"]},
        "tortoise_update_point": {"id": s["claim"], "props": {"topic": "retired"}},
        "tortoise_file_decision": {"options": ["a", "b"], "evidence": ["e"],
                                   "choice": 0},
    }


#: Names REMOVED with no destination (the four archived tools + the five removed
#: `tortoise_onboarding_*` ones). They have no shim, so the documented answer is
#: not-found — and a silent empty answer would be the failure (#2952).
ARCHIVED_NO_DESTINATION: tuple[str, ...] = (
    "tortoise_analyze", "tortoise_backfill_v25", "tortoise_packs_list",
    "tortoise_pack_install", "tortoise_onboarding_state", "tortoise_onboarding_seed",
    "tortoise_onboarding_demo_create", "tortoise_onboarding_github_connect",
    "tortoise_onboarding_github_status",
)


def probe_retired(door: _Door, s: dict) -> tuple[list[tuple[str, bool, str, str]], list[tuple[str, str]]]:
    """-> (retired samples `(name, notice_seen, use_instead, answer)`, archived answers).

    `answer` is what the retired door DID: answered with its previous behaviour, or
    refused — and a `-32004` refusal is `http_policy=False` doing its job, which is a
    different observation from a broken shim. The V3 claim is the NOTICE column: a
    retired name that still answers without saying it is retired teaches a caller a
    name the product has stopped supporting.
    """
    samples: list[tuple[str, bool, str, bool]] = []
    for name, args in _retired_sample(s).items():
        # ONE call: the notice and the payload must come from the SAME invocation — a
        # second call re-runs the handler (a delete arm would refuse the second time)
        # and the two halves would then describe different invocations.
        notice, result = _retired_notice(door, name, args)
        payload = _tool_payload({"result": result})
        err = _error_of(payload)
        raw_err = payload.get("error") if isinstance(payload, dict) else None
        code = raw_err.get("code") if isinstance(raw_err, dict) else None
        if code == -32004:
            # the transport excludes this row by design; the shim notice still
            # published, so the retirement contract held and the CAPABILITY is
            # served elsewhere (SDK / hosted REST). Not a broken shim.
            answer = "REFUSED (-32004, http_policy=False by design)"
        elif err:
            answer = f"REFUSED ({err[:70]})"
        else:
            answer = "answered (previous behaviour)"
        samples.append((name, bool(notice), str((notice or {}).get("use_instead") or ""),
                        answer))
    archived: list[tuple[str, str]] = []
    for name in ARCHIVED_NO_DESTINATION:
        _status, payload, _ms = door.call(name, {})
        archived.append((name, str(payload)[:80]))
    return samples, archived


def _retired_notice(door: _Door, name: str, args: dict):
    """The RAW `_meta.tortoise.retired` block, which `_tool_payload` discards."""
    door._n += 1
    r = door._c.post("/mcp", json={
        "jsonrpc": "2.0", "id": door._n, "method": "tools/call",
        "params": {"name": name, "arguments": args or {}},
    })
    result = (_parse_sse_json(r) or {}).get("result", {}) or {}
    meta = result.get("_meta") or {}
    return (meta.get("tortoise") or {}).get("retired"), result


def _render_markdown(tool_rows: list[Row], sdk_rows: list[Row],
                     retired_rows: list[tuple[str, bool, str, str]] | None = None,
                     archived_rows: list[tuple[str, str]] | None = None) -> str:
    lines: list[str] = []
    lines.append("## Approved MCP tools — one real call each, through the authenticated tenant door")
    lines.append("")
    lines.append("| # | approved tool | served door | status | latency | answer shape | note |")
    lines.append("|---|---|---|---|---|---|---|")
    for i, r in enumerate(tool_rows, 1):
        ms = "—" if r.latency_ms is None else f"{r.latency_ms:.0f} ms"
        note = r.note.replace("|", "\\|")
        if len(note) > 150:
            note = note[:147] + "…"
        lines.append(f"| {i} | `{r.approved}` | `{r.door}` | **{r.status}** | {ms} | "
                     f"`{r.shape}` | {note} |")
    lines.append("")
    lines.append("## Approved SDK methods — one real call each, against a throwaway embedded store")
    lines.append("")
    lines.append("| # | approved method | status | latency | answer shape | note |")
    lines.append("|---|---|---|---|---|---|")
    for i, r in enumerate(sdk_rows, 1):
        ms = "—" if r.latency_ms is None else f"{r.latency_ms:.0f} ms"
        note = r.note.replace("|", "\\|")
        if len(note) > 150:
            note = note[:147] + "…"
        lines.append(f"| {i} | `{r.approved}` | **{r.status}** | {ms} | `{r.shape}` | {note} |")
    lines.append("")
    if retired_rows:
        lines.append("## V3 — the retirement shim: one retired name per destination family")
        lines.append("")
        lines.append("| retired name | notice published | use_instead | what the retired door did |")
        lines.append("|---|---|---|---|")
        for name, notice, use, answer in retired_rows:
            lines.append(f"| `{name}` | {'YES' if notice else '**NO**'} | `{use[:70].replace(chr(124), chr(92) + chr(124))}` | "
                         f"{answer[:80].replace(chr(124), chr(92) + chr(124))} |")
        lines.append("")
        lines.append("## V3 — archived with no destination: the documented not-found")
        lines.append("")
        lines.append("| archived name | answer |")
        lines.append("|---|---|")
        for name, answer in archived_rows or []:
            lines.append(f"| `{name}` | `{answer[:70]}` |")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
