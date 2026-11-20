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
* one topic OBJECT plus a ``decision`` Point ``aboutObject``-linked to it (for the
  ``check_confidence(topic=...)`` arm, which resolves the topic as an ENTITY and
  walks that edge — a Point carrying a ``topic=`` PROPERTY is not a topic),
* one calibrated claim (for the ``check_confidence(claim_id=...)`` happy path),
* one orphan ``useCase`` (a live chain point with no parent JTBD, so the
  ``structure_check`` validator has a violation to REPORT — without it that arm's
  correct answer is ``[]`` and it pins nothing),
* one ingested bundle (so ``list_knowledge(kind='batch')`` and ``list_batches``
  read a real batch id back; an unseeded batch set carries no payload at all over
  this transport, which is indistinguishable from a broken seam).

Assertions read these ids back out of the answers. A payload is "correct" when it
contains the thing we seeded — not when it is merely non-empty.

UNDRIVEN TOOLS ARE RECORDED, NEVER PASSED
=========================================

Four live tools refuse at the tenant HTTP transport by design (filesystem-walk and
provisioning vectors). They cannot be driven for a PAYLOAD through this fixture, so
they are listed in ``UNDRIVEN`` with the reason, and
``test_every_live_tool_is_accounted_for`` asserts the union covers the registry
exactly. A tool added without a case fails that test — the surface cannot grow
silently past this row. An ``UNDRIVEN`` entry is **not** a pass, and this file does
not claim those tools' payloads.

⛔ "Cannot be driven" is a claim about the TRANSPORT, so it is MEASURED, not
asserted from a registry row (`#13886` r6, F1). Each entry carries a valid probe in
``_UNDRIVEN_PROBE_ARGS`` and ``test_every_undriven_tool_is_REFUSED_by_the_tenant_
door`` drives it, expecting the ``-32004`` HTTP-exclusion refusal — the same shape
the ``dream_health`` section gap already had. The accounting test binds the set in
BOTH directions (``UNDRIVEN ⊆ http_policy=False``), because the pre-r6 bind ran one
way only: a tool whose row flipped to ``http_policy=True`` stayed in ``UNDRIVEN``,
became servable over HTTP, and every assertion in this file stayed green.

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
see the failure class this row owns. Mutation 1 was re-run after the review
round below and still reddened exactly one test.

WHAT A REVIEW ROUND CHANGED (and why it is recorded here)
=======================================================

A fresh-context review found that the first draft contained assertions a broken
implementation could satisfy. They are fixed, and the fixes are the reason several
tests assert COUNTS and IDS rather than "no error":

* ``mine_knowledge_from_session`` sent ``items`` as a list of STRINGS. The item
  contract is ``{wing, room, content}`` dicts, so the call was REFUSED (-32008) and
  the only assertion ("not the stdio-only error") passed on that refusal — a tool
  counted as covered while its write arm was never exercised. It now sends a dict,
  asserts ``filed == 1``, and asserts the re-send is a DUPLICATE.
* ``calibrate_summary``, five of six ``search_knowledge`` ``mode=`` arms, and the
  ``session``/``topic``/``recompute`` confidence arms asserted ``out is not None``
  or ``isinstance(out, (list, dict))`` — satisfied by every branch the payload
  helper can return. Each now asserts its own shape and seeded content. ⛔ That
  sentence was FALSE of ``topic`` and ``recompute`` when it was written: the next
  review round found both still unable to fail (an unreachable ``None`` guard on
  ``recompute``, and a ``topic`` arm whose empty answer was a SEED-shape mismatch
  recorded as a measurement). Both are fixed in the round whose record sits at
  ``ARM_GAPS``, and only now is this claim true of the code.
  ⛔ It was ALSO false of ``issue_insight``, which asserted two key NAMES until
  the r6 round: ``{"has_prior": False, "data_points": []}`` passed it. It now
  asserts the seeded claim inside ``data_points`` — so read the sentence as "every
  arm here reads seeded content out of its own shape", which is what the code now
  does, rather than as a claim about when it became true.
* ``source_reliability`` used ``out.get("url", <seed>)``, whose default made a
  payload MISSING the field pass. It now has no default.
* ``create_edge``, ``write_question``, ``record_decision`` and ``ingest`` asserted
  only "no error". ``ingest({})`` in particular returns a CONSTANT batch id with
  all-zero counts, so an empty bundle proved nothing. All four now assert counts
  and ids, and read the write back.
* The ``graph_overview`` section table used ``isinstance(out, dict)``, which
  ``{"error": …}`` also satisfies — for ``taxonomy``, ``tags``, ``stale``,
  ``graphs`` and ``namespaces`` that was the ONLY assertion. Error envelopes are
  now excluded explicitly and those arms assert content.
* ``create_entity``'s 6th arm (``type='diary'``) was undriven — diary points were
  seeded through the SDK, bypassing the transport. It now round-trips through
  ``list_knowledge(kind='diary')``.

Two things the review surfaced that are NOT fixed here, because they are not this
row's to decide, and are recorded instead of absorbed:

* ``tortoise_expand_relationships`` declares ``http_policy=False`` yet answers over
  the tenant HTTP surface — tracked in **#13170**, which predates this row; this
  suite is independent confirmation and deliberately does not file a peer issue
  (see ``HTTP_POLICY_DISAGREEMENTS``). A new
  ``test_every_http_excluded_tool_is_accounted_for`` binds the file to
  ``http_policy`` so the registry is the authority and this cannot drift silently.
* ``approve_merge``'s success arm and ``manage_sources(enable)`` cannot be driven
  on this lane — see ``ARM_GAPS``. They are recorded as gaps, never as passes.
  (``check_confidence(topic=)`` was in that list and is NOT a gap: the arm resolves
  the topic as an ENTITY and reads the ``decision`` Points ``aboutObject``-linked to
  it, so the seed now carries that shape and the payload is asserted as that chain
  — the earlier entry measured a seed defect, not the arm.)

A SECOND REVIEW ROUND (r6) — what it changed
============================================

Six more of the same class, each fixed here and each MEASURED before the fix:

* ``_tool_payload`` manufactured the answer it was supposed to distinguish. Zero
  content blocks + no ``structuredContent`` + ``isError: false`` became ``[]``, and
  a bare-string refusal became a ``{"_text": …}`` dict — so a broken seam could
  satisfy an empty-collection arm, and a TEXT refusal could satisfy a dict arm.
  The transport helper now returns an explicit ``_NoPayload`` sentinel (neither a
  list nor a dict) and a ``_PlainText`` ``str`` subclass, and the one arm that
  really answers nothing (``get_entity(type='governance')``) asserts that wire
  shape instead of the helper's fallback.
* ``UNDRIVEN`` was prose: nothing drove the four excluded tools, and the
  ``http_policy`` bind ran one way. Both directions are asserted now, and each
  entry is PROBED for its ``-32004`` refusal.
* Four arms asserted a shape and stopped: ``check_structure`` (an empty list),
  ``review_link_candidates`` (two key names), ``check_connection`` (`{}` is a
  dict) and ``graph_set_recording`` (``graph_id == graph_id``, i.e. `None ==
  None`). Each now reads seeded content out of its own payload — the seed grew an
  orphan ``useCase`` for the chain validator to report by rule.
* The ``mode=`` tables answered only to themselves. Both are now bound to the
  product's own tuples (``TortoiseSDK.SEARCH_KNOWLEDGE_MODES`` and
  ``mcp_server._RECALL_MODES``), the same bind the ``section=`` table already had,
  so a mode the product adds reds this file instead of going unmeasured.
* The documented ambient flake was mitigated for ONE tool while every arm was
  exposed to it. The transport wait bound is now widened once, in the fixture, for
  every arm; the ``sources_state`` projection stub stays for what it actually is —
  that tool's own control-plane dependency.

A THIRD REVIEW ROUND (r8) — what it changed
===========================================

Two findings, both *claim vs assertion* rather than product defects, both fixed by
making the fixture and the assertion measure the same thing:

* ``test_review_link_candidates_answers_both_directions`` asserted that the
  operator's TARGET was proposed — but the seed CONNECTS that pair, and
  ``sdk._review_add`` skips connected pairs, so the row's verdict came from an
  unengineered pair clearing the 0.72 similarity band. Measured consequence: the
  same test code red on a box without the embeddings extra and green on one with
  it (the TF-IDF fallback scores a template-sharing pair far below 0.72). The
  seed now carries an engineered pair — verbatim wording that scores 1.000 under
  BOTH the embedding cosine and the TF-IDF fallback — and the row asserts both
  halves: that pair IS proposed, and the above-band pair joined by the seeded
  IMPL operator is NOT (which is what makes the ``connected`` skip falsifiable).
* ``_PlainText`` was a guard with no reddening row: ``_text_of`` renders a dict
  too, so reverting the sentinel to the pre-r6 ``{"_text": …}`` left the file
  green. Both ``deletion`` token refusals now assert the WIRE SHAPE
  (``isinstance(out, str) and not isinstance(out, dict)``), which reds on exactly
  that revert.
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

#: The arguments each `UNDRIVEN` tool is PROBED with, so "it cannot be driven
#: here" is MEASURED per tool rather than asserted from the registry row (#13886
#: r6, F1). Each probe is a VALID request for its tool, so the only thing that
#: can refuse it is the transport guard this file credits with the refusal — a
#: malformed probe would "refuse" for the wrong reason and prove nothing.
_UNDRIVEN_PROBE_ARGS: dict[str, dict] = {
    "tortoise_org_create": {"name": "capstone-undriven-team"},
    "tortoise_index_sources_from_directory": {"directory": "/tmp"},
    "tortoise_mine_knowledge_from_directory": {"directory": "/tmp"},
    "tortoise_refresh_confidence": {},
}

#: Per-ARM gaps inside a tool that is otherwise driven. A tool can be DRIVEN and
#: still have an arm this lane cannot reach; saying so is the difference between
#: a measured gap and a silent claim. These are NOT passes either.
ARM_GAPS: dict[str, str] = {
    "tortoise_approve_merge(data)":
        "ARM GAP — the merge SUCCESS arm is unreachable here: `create_object` "
        "deduplicates on `canonical_name`, so seeding two identical names returns "
        "the SAME id and no ambiguity pair exists (measured: 'has no ambiguity "
        "pair on the current graph'). The refusal arm IS driven.",
    "tortoise_manage_sources(enable)":
        "ARM GAP — the `enable` arm requires a connected GitHub account and refuses "
        "by naming that (`GitHub not connected`). The `list` and `disable` arms are "
        "driven, and `disable` is read back through `list`.",
}

#: Live tools whose DECLARED `http_policy` and OBSERVED tenant-HTTP behaviour
#: disagree on this head. Recorded so the disagreement is visible at the row that
#: would otherwise absorb it as a pass (#13886 review, finding 11). The registry
#: row is the product's declaration and this file does not edit it.
HTTP_POLICY_DISAGREEMENTS: dict[str, str] = {
    "tortoise_expand_relationships":
        "Declared `http_policy=False` (not on the tenant HTTP surface) yet its "
        "handler carries NO first-statement transport guard, so it ANSWERS over "
        "HTTP with a real payload. Tracked in #13170 (opened 2026-11-17, before "
        "this row; this suite is independent confirmation and adds no peer issue). "
        "The case below drives it and asserts the payload, because the behaviour "
        "is real whichever way the registry row is settled.",
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


class _NoPayload:
    """An answer that carried no payload at all: no content block, no
    ``structuredContent``, and ``isError`` false.

    ⛔ It is deliberately NEITHER a ``list`` NOR a ``dict``. The pre-r6 helper
    returned ``[]`` for this case, which made the *broken seam* answer identical
    to the *empty-but-correct collection* the arms assert — the helper created
    the collision its own docstring said it prevented (`#13886` r6, F2). A
    sentinel that satisfies no collection assertion cannot do that.

    ``result`` is kept so an arm can assert the WIRE shape it observed
    (``isError is False``) instead of asserting the helper's opinion of it.

    The spec is why NO wire reading can do better: ``CallToolResult.content`` is a
    required ``ContentBlock[]``, ``structuredContent`` is OPTIONAL, and a
    successful empty result may legitimately use an empty ``content`` array
    (MCP schema, ``tools/call``). So "empty payload" and "no payload" are the same
    wire shape — the arms that care are driven against SEEDED content, and the
    two that genuinely answer nothing assert this sentinel.
    """

    __slots__ = ("result",)

    def __init__(self, result):
        self.result = result

    def __repr__(self):
        return f"<_NoPayload isError={self.result.get('isError')!r}>"


class _PlainText(str):
    """A NON-JSON tool answer — the handler returned a bare string, so FastMCP
    serves it as ``content[0].text`` verbatim (a refusal's wording, e.g.
    ``deletion``'s token errors).

    A ``str`` SUBCLASS on purpose: the pre-r6 helper returned
    ``{"_text": text}``, and that dict satisfied ``isinstance(out, dict)`` plus
    ``"error" not in out`` — so a *text* refusal could pass a *structured-payload*
    arm (`#13886` r6, F2). A string can satisfy neither.

    ⛔ The guard is only guard-shaped if a revert REDS something. The two
    ``deletion`` token arms assert ``isinstance(out, str) and not
    isinstance(out, dict)``, because ``_text_of`` renders both forms and would
    otherwise pass on the dict this subclass exists to prevent (`#13886` r8, F2).
    """


def _text_of(out):
    """The refusal WORDING of a non-JSON answer, or the JSON rendering of a
    structured one — what the two `deletion` token cases read.

    ⛔ The two renderings are NOT interchangeable, and this helper must never be
    used to tell them apart: it renders a dict as well as a str, so a text
    refusal that regressed to ``{"_text": …}`` still "contains" its wording.
    The two token cases therefore ALSO assert the wire shape
    (``isinstance(out, str) and not isinstance(out, dict)``) — that is the half
    that reds on the regression (`#13886` r8, F2).
    """
    if isinstance(out, str):
        return str(out)
    return json.dumps(out, default=str)


def _tool_payload(body):
    """The PAYLOAD is ``content[0]``; later blocks are appended disclosures.

    Same rule as ``tests/test_mcp_http.py::_tool_result``: joining every block
    turns a write into ``{"text": …}`` once a disclosure block appears (#10084).

    An EMPTY collection is served EITHER as ``content: [{"text": "[]"}]`` or as
    ``content: []`` with the value carrying in ``structuredContent.result`` —
    BOTH measured on this surface (``graph_overview(section='tags')`` takes the
    first shape, ``check_structure`` the second). A response that carries NEITHER
    (measured: ``get_entity(type='governance')`` over a Point that owns nothing,
    and ``list_knowledge(kind='batch')`` against an unseeded batch set) is
    ``_NoPayload``, NOT ``[]`` — see the sentinel. Those two arms are driven
    against SEEDED content instead, so neither has to tell an empty answer from
    a missing one.
    """
    result = (body or {}).get("result", {}) or {}
    if isinstance(result, dict) and "content" in result:
        blocks = [c for c in result["content"] if isinstance(c, dict)]
        text = blocks[0].get("text", "") if blocks else ""
        if text:
            try:
                return json.loads(text)
            except ValueError:
                return _PlainText(text)
        structured = result.get("structuredContent")
        if isinstance(structured, dict) and "result" in structured:
            return structured["result"]
        # Zero content blocks and no structuredContent: the answer carried no
        # payload. Say THAT (see `_NoPayload`) rather than manufacturing the
        # empty collection an empty-value arm wants to see.
        if not blocks and not result.get("isError"):
            return _NoPayload(result)
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
    from contextlib import asynccontextmanager

    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.testclient import TestClient

    from tortoise import mcp_auth as _mcp_auth
    from tortoise.mcp_server import create_http_app

    # ── the ambient red this row must not inherit (#13886 r6, F6) ───────────
    # ``mcp_server._await_under_mcp_wait_bound`` spends
    # ``mcp_auth._TRANSPORT_WAIT_BOUND_S`` (10 s — a MODULE CONSTANT with no env
    # knob ON PURPOSE, ``mcp_auth.py:792``: once a caller codes to the number a
    # silent env change is a client-visible contract change). This fixture
    # provisions an org, mints a key and makes a dozen transport calls PER TEST,
    # so on a contended box a dispatch can breach the bound, and the refusal then
    # describes the ENVIRONMENT rather than the payload this row exists to assert.
    # MEASURED as a red in a full-file run of this module:
    #
    #     transport wait bound (10s) exceeded: MCP tools/call tortoise_manage_sources
    #
    # A payload row that reds on ambient load pins nothing, so the bound is
    # widened FOR THIS MODULE's tests only. Both seams read the constant at CALL
    # time (``mcp_server.py:1680``/``:2994``), so a scoped attribute patch reaches
    # them, and every clamp derived from it only ever REDUCES. ⛔ This is the
    # UNIFORM half of the mitigation — it covers every arm, which is what the r6
    # review found missing when the only mitigation was the one tool-specific
    # projection stub below.
    monkeypatch.setattr(_mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 60.0)

    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    db = str(tmp_path / "capstone.db")
    monkeypatch.setenv("TORTOISE_DB_PATH", db)

    reg = TortoiseSDK(db_path=db, namespace="registry")
    team = reg._org_create("capstone-v2-team")
    key = reg._apikey_create(team["id"], "capstone")["api_key"]
    org = team["id"]

    sdk = TortoiseSDK(db_path=db, namespace=org)
    s: dict[str, str] = {}
    s["org"] = org

    # ── the seed ────────────────────────────────────────────────────────────
    s["claim"] = sdk._create_point(
        "statement", "The capstone V2 lane proves payloads, not statuses.")["id"]
    # A CALIBRATED claim: the `check_confidence(claim_id=…)` happy path refuses
    # an uncalibrated evidence point by name, so the happy path needs one that
    # carries a credibility prior.
    s["calibrated"] = sdk._create_point(
        "statement", "A calibrated seeded claim for the confidence arm.",
        credibility="high")["id"]
    # ⛔ The operator's TARGET restates the SOURCE verbatim ON PURPOSE.
    # `review_link_candidates(mode='add')` skips pairs that already share a
    # connection (`sdk._review_add`: `if frozenset((a, b)) in connected:
    # continue`), so a connected pair can only make that skip FALSIFIABLE if
    # its similarity clears the 0.72 band — otherwise "not proposed" is
    # indistinguishable from "scored too low" and deleting the skip reds
    # nothing (`#13886` r8, F1). Verbatim wording scores 1.000 under BOTH
    # scorers (bge-small cosine, and the TF-IDF fallback `_encode` degrades to
    # when the embeddings extra is absent — #399), so no box can change the
    # verdict.
    s["claim2"] = sdk._create_point(
        "statement", "The capstone V2 lane proves payloads, not statuses.")["id"]
    # ── the `check_confidence(topic=…)` seed ───────────────────────────────
    # That arm dispatches to `belief_timeline`, which RESOLVES the topic as an
    # ENTITY and returns the `decision` Points `aboutObject`-linked to it. A Point
    # carrying a `topic=` PROPERTY is a different shape altogether: it is not an
    # Object and owns no edge, so that seed answers `[]` STRUCTURALLY. The earlier
    # draft seeded the property and recorded the empty answer as a measured
    # "arm gap", i.e. it read as "this tool is broken" when it was the seed.
    s["topic"] = "capstone-topic"
    s["topic_object"] = sdk._create_object(
        "capstone-topic", objectKind="core:thing")["id"]
    s["topic_decision"] = sdk._create_point(
        "decision", "The capstone retrieval window ships in Q3.")["id"]
    sdk._create_edge("aboutObject", s["topic_decision"], s["topic_object"])
    s["object"] = sdk._create_object("Widget", objectKind="core:thing")["id"]
    s["subject"] = sdk._create_subject("Alice")["id"]
    s["source"] = sdk.register_source("https://example.test/a", "web")["id"]
    s["event"] = sdk._create_event("Deploy", eventKind="core:action")["id"]
    s["operator"] = sdk._create_operator("IMPL", s["claim"], [s["claim2"]])["id"]
    # ── the `review_link_candidates` seed ─────────────────────────────────
    # A related-but-UNCONNECTED pair the add arm must PROPOSE. The pre-r8 row
    # asserted `claim2` — the operator's target — so the pair it named is
    # excluded BY CONSTRUCTION and its pass rested on some other pair clearing
    # 0.72 incidentally, i.e. on the box having the embedding model (measured:
    # the same test code, `1 failed, 122 passed` on one box and `123 passed` on
    # another at a docstring-only head — `#13886` r7 ISSUE 2 / r8 F1). This
    # pair is engineered instead: verbatim wording ⇒ similarity 1.000 under
    # the embedding cosine AND under the TF-IDF fallback, and no edge between
    # the two, so "proposed" is the product's answer on every box.
    s["review_pair"] = [
        sdk._create_point(
            "statement",
            "A related-pair statement seeded for the link-candidate arm.",
        )["id"]
        for _ in range(2)
    ]
    s["session"] = "sess-capstone-1"
    sdk._create_event("capstone session", eventKind="AgentSession",
                     session_id=s["session"], agent_name="capstone")

    # ── the `structure_check` seed ────────────────────────────────────────
    # `check_structure` runs the product-strategy chain validators, and against
    # a graph holding no chain kinds its CORRECT report is `[]` — so the arm was
    # proven only to answer a list (`#13886` r6, F3). One live `useCase` with no
    # parent JTBD is exactly the shape the `orphan_use_case` rule reports, which
    # is what lets the assertion below read a VIOLATION back out instead of
    # asserting an empty answer.
    s["use_case"] = sdk._create_point(
        "useCase",
        "An orphaned capstone useCase, seeded so the chain validator has "
        "something to report.")["id"]

    # ── the `batch` seed ──────────────────────────────────────────────────
    # `list_knowledge(kind='batch')` and `list_batches` against an UNSEEDED
    # batch set carry no payload at all over this transport (measured), which is
    # the same wire shape as a broken seam. A real ingest gives both arms an id
    # to read back, so neither assertion has to accept an empty answer.
    s["batch"] = sdk._ingest({
        "points": [{"content": "An ingested capstone batch point.",
                     "kind": "statement"}],
    })["batch_id"]

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


@pytest.fixture
def sources_state(monkeypatch):
    """The onboarding projection the two ``manage_sources`` arms read, INJECTED.

    Same seam, and the same reason, as ``tests/test_mcp_http.py``'s
    ``test_gate_cache_no_refetch_within_ttl``: the real projection's graph leg
    reports ``unavailable`` when the embedded store is contended, and under a
    loaded box the dispatch can breach the 10 s transport wait bound — MEASURED
    on this row, one red in a full-file run of this module:

        transport wait bound (10s) exceeded: MCP tools/call tortoise_manage_sources

    That refusal is produced by the ENVIRONMENT, so it says nothing about the
    payload this row exists to assert — and it reds a file every PR inherits.

    The WRITER is stubbed onto the SAME dict rather than the read alone, so the
    ``disable`` -> ``list`` round trip below stays a real round trip through the
    tool's own write and the tool's own projection. What is removed is the
    ambient dependency (the registry probe's latency), not the assertion.

    ⛔ This is NOT the ambient-flake mitigation any more (`#13886` r6, F6): it is
    specific to ``manage_sources``'s own control-plane read, and it left every
    other graph-reading arm exposed to the transport wait bound. That red is now
    handled once, for every arm, by the `seeded` fixture — see the bound patch
    there. Keeping the two separate is the point: this stub is about the tool's
    dependency, the fixture patch is about the ENVIRONMENT.
    """
    from tortoise import hosted_api

    state: dict[str, object] = {}
    writes: list[dict[str, object]] = []

    def _projection(_org_id):
        return state

    def _write(_org_id, _echo=True, **fields):
        writes.append(dict(fields))
        state.update(fields)
        return state

    monkeypatch.setattr(hosted_api, "_get_onboarding_projection", _projection)
    monkeypatch.setattr(hosted_api, "_update_onboarding_state", _write)
    return state, writes


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


def test_the_declared_coverage_matches_the_tools_this_file_ACTUALLY_CALLS():
    """`_DRIVEN_BY_TEST` must equal the set of tools passed to ``door.call(...)``.

    Without this, the accounting gate above is only as honest as a hand-typed
    tuple: delete a test (or gut it) and the tuple keeps claiming coverage
    forever. That is the same silent-drift class the file exists to catch, so it
    is read off the AST rather than trusted.
    """
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(__file__).read_text())
    called = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "call"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            called.add(node.args[0].value)

    declared = set(_DRIVEN_BY_TEST)
    assert not declared - called, (
        "declared as driven but NEVER called by any test (the list has drifted): "
        f"{sorted(declared - called)}")
    assert not called - declared - set(UNDRIVEN), (
        "called by a test but not declared in _DRIVEN_BY_TEST: "
        f"{sorted(called - declared - set(UNDRIVEN))}")


def test_undriven_entries_name_a_mechanism():
    """`UNDRIVEN` is a finding only when it says why — never a bare label."""
    for name, reason in UNDRIVEN.items():
        assert reason.startswith("UNDRIVEN — "), name
        assert len(reason) > 60, f"{name}: reason must name the mechanism"
    # …and every entry must carry a probe, or its refusal is never measured
    # (`#13886` r6, F1): a new UNDRIVEN entry with no probe would otherwise be
    # the pre-r6 state again — prose, with nothing driving it.
    assert set(_UNDRIVEN_PROBE_ARGS) == set(UNDRIVEN), (
        "UNDRIVEN entries and probe arguments disagree — every entry needs a "
        f"probe: {sorted(set(UNDRIVEN) ^ set(_UNDRIVEN_PROBE_ARGS))}")


def test_every_http_excluded_tool_is_accounted_for():
    """A tool declared OFF the tenant HTTP surface must be either UNDRIVEN or a
    recorded disagreement — so a new `http_policy=False` row cannot appear and be
    silently exercised (or silently skipped) here.

    This is the registry-to-file binding: the reviewer's finding was that
    `_DRIVEN_BY_TEST` is hand-typed prose. Binding it to `http_policy` makes the
    registry itself the authority for which tools the transport may serve.

    ⛔ BOTH DIRECTIONS (`#13886` r6, F1). The pre-r6 test only asserted
    `declared_excluded − UNDRIVEN − DISAGREEMENTS == ∅` and
    `driven_excluded ⊆ DISAGREEMENTS`, so an `UNDRIVEN` tool whose row flipped to
    `http_policy=True` stayed green: it was servable over HTTP and still read as
    "cannot be driven here". The subset below is that missing direction.
    """
    declared_excluded = {t.name for t in TOOL_REGISTRY if not t.http_policy}
    unrecorded = declared_excluded - set(UNDRIVEN) - set(HTTP_POLICY_DISAGREEMENTS)
    assert not unrecorded, (
        "http_policy=False tool(s) with neither an UNDRIVEN entry nor a recorded "
        f"HTTP_POLICY_DISAGREEMENT: {sorted(unrecorded)}")
    # an exclusion we DID drive must be declared as a disagreement, not left
    # looking like an ordinary drivable tool
    driven_excluded = declared_excluded & set(_DRIVEN_BY_TEST)
    assert driven_excluded <= set(HTTP_POLICY_DISAGREEMENTS), (
        "tool(s) driven over HTTP while the registry declares http_policy=False, "
        f"with no recorded disagreement: {sorted(driven_excluded)}")
    # …and the OTHER direction: every UNDRIVEN tool must actually BE declared
    # off the tenant HTTP surface. Without this, flipping one row to
    # `http_policy=True` leaves the tool servable AND unmeasured.
    assert set(UNDRIVEN) <= declared_excluded, (
        "UNDRIVEN tool(s) the registry declares SERVABLE over tenant HTTP — the "
        "entry is stale: either drive it (and drop the entry) or fix the "
        f"http_policy row: {sorted(set(UNDRIVEN) - declared_excluded)}")


@pytest.mark.parametrize("name", sorted(_UNDRIVEN_PROBE_ARGS))
def test_every_undriven_tool_is_REFUSED_by_the_tenant_door(seeded, name):
    """Each `UNDRIVEN` entry is DRIVEN once, expecting the transport refusal.

    This is the half the file already applied to the `dream_health` section gap
    (`test_the_dream_health_gap_is_the_measured_refusal`): "could not drive it"
    is a claim about the TRANSPORT, and a claim is measured, not asserted from a
    registry row. Without it, dropping a handler's `_http_excluded_error()`
    guard makes the tool answer a real (and unasserted) payload over HTTP while
    every accounting test above stays green — the silent un-coverage these rows
    exist to prevent (`#13886` r6, F1).

    The refusal is asserted by CODE (`-32004`) and by its wording, not by
    "an error came back": a different refusal would mean the tool refused for a
    reason this entry does not name."""
    door, _s, _ = seeded
    status, out = door.call(name, _UNDRIVEN_PROBE_ARGS[name])
    assert status == 200
    text = json.dumps(out, default=str)
    assert "-32004" in text, (
        f"{name} is recorded UNDRIVEN but the tenant door did not answer the "
        f"HTTP-exclusion refusal: {out!r}")
    assert "not available over HTTP" in text, out


def test_recorded_disagreements_explain_themselves():
    for name, reason in HTTP_POLICY_DISAGREEMENTS.items():
        assert len(reason) > 80, f"{name}: disagreement must name the mechanism"
    for name, reason in ARM_GAPS.items():
        assert reason.startswith("ARM GAP — "), name
        assert len(reason) > 80, f"{name}: arm gap must name the mechanism"


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

    def test_governance_arm_answers_no_payload_for_a_point(self, seeded):
        """`type='governance'` answers the entities a Subject OWNS. The seed's
        claim is a Point owned by nobody, so the arm answers NOTHING — and over
        the wire that is `content: []` with NO `structuredContent` and
        `isError: false` (MEASURED here), a DIFFERENT wire shape from the empty
        COLLECTIONS below (`list_batches` / `check_structure` carry
        `structuredContent.result: []`).

        ⛔ Asserting `out == []` here asserted the transport HELPER's fallback,
        not the tool (`#13886` r6, F2): the fallback returned `[]` for that exact
        shape, so a broken seam read as a correct empty answer. The assertion is
        now the observed wire shape — and it still reds if the arm starts
        answering rows (the payload becomes a real list)."""
        door, s, _ = seeded
        status, out = door.call("tortoise_get_entity",
                                {"id": s["claim"], "type": "governance"})
        assert status == 200
        assert isinstance(out, _NoPayload), (
            "a Point owns no entities, so the governance arm must answer no "
            f"payload; got {out!r}")
        assert out.result.get("isError") is False, out


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
        """`kind='source_reliability'` is a PER-SOURCE read: it requires `url=`.

        The URL assertion has no default — a payload MISSING the field must
        fail, which is what `out.get("url", <seed>)` got wrong.
        """
        door, s, _ = seeded
        door.call("tortoise_manage_source_trust",
                  {"url": s["source"], "tier": "high"})
        status, out = door.call("tortoise_list_knowledge",
                                {"kind": "source_reliability",
                                 "url": s["source"]})
        assert status == 200
        assert isinstance(out, dict), out
        assert out.get("url") == s["source"], out
        # the tier write must have moved it off "untiered"/None
        assert isinstance(out.get("reliability"), (int, float)), out
        assert 0.0 <= float(out["reliability"]) <= 1.0, out
        assert out.get("cache") in {"fresh", "recomputed", "miss"}, out

    def test_batch_kind_reads_the_seeded_batch(self, seeded):
        """`kind='batch'` is a LIST arm. An UNSEEDED batch set renders as
        ``content: []`` with NO ``structuredContent`` (MEASURED), which is the
        same wire shape as a broken seam — so the arm is driven against a real
        seeded batch instead, and the assertion is the batch's own id. Asserting
        "a collection came back" could not tell the two apart (`#13886` r6, F2)."""
        door, s, _ = seeded
        status, out = door.call("tortoise_list_knowledge", {"kind": "batch"})
        assert status == 200
        rows = out.get("results") if isinstance(out, dict) else out
        assert isinstance(rows, (list, tuple)) and rows, out
        assert s["batch"] in {row.get("batch_id") for row in rows
                              if isinstance(row, dict)}, out

    def test_namespace_kind_answers_the_pack_namespaces(self, seeded):
        door, _s, _ = seeded
        status, out = door.call("tortoise_list_knowledge",
                                {"kind": "namespace"})
        assert status == 200
        assert isinstance(out, list) and out, out
        assert all(isinstance(r, dict) for r in out), out
        names = {row.get("namespace") for row in out}
        assert names and all(isinstance(n, str) and n for n in names), out

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
        door, _s, sdk = seeded
        sdk._create_point("diary", "Seeded diary entry for the read arm.",
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

#: The `mode=` table: every value the container dispatches on, with the SHAPE
#: its own arm answers. It is the parametrize TABLE itself, and it answers to the
#: PRODUCT's own tuple (`TortoiseSDK.SEARCH_KNOWLEDGE_MODES`) via
#: `test_the_search_mode_table_is_the_products_mode_set` — the same anti-silence
#: bind the `section=` table carries. Without that bind the table answers only to
#: itself: drop a row, or let the product add a seventh mode, and nothing reds
#: while the file claims "every advertised `mode=` is driven" (`#13886` r6, F5).
_SEARCH_MODE_ASSERTIONS: dict[str, str] = {
    "hybrid": "claim",
    "full_text": "claim",
    "entry_points": "claim",
    "tag": "tag_envelope",
    "sessions": "session",
    "issue_insight": "insight",
}


class TestSearchReturnsSeededClaim:

    def test_the_search_mode_table_is_the_products_mode_set(self):
        """`driven == TortoiseSDK.SEARCH_KNOWLEDGE_MODES`, exactly.

        The table is hand-typed, so without this bind it can silently disagree
        with the product it claims to cover — a new mode would have no case and
        the file would still say "every advertised `mode=` is driven", and a
        deleted row would keep its `_DRIVEN_BY_TEST` credit."""
        assert set(_SEARCH_MODE_ASSERTIONS) == set(
            TortoiseSDK.SEARCH_KNOWLEDGE_MODES), (
            "the search `mode=` table and the product's own mode set disagree: "
            f"{sorted(set(_SEARCH_MODE_ASSERTIONS) ^ set(TortoiseSDK.SEARCH_KNOWLEDGE_MODES))}")

    def test_search_knowledge_hybrid_finds_the_seeded_claim(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_search_knowledge",
                                {"query": "capstone"})
        assert status == 200
        assert isinstance(out, list) and out, out
        assert s["claim"] in _ids(out), out

    @pytest.mark.parametrize("mode,assertion", sorted(_SEARCH_MODE_ASSERTIONS.items()))
    def test_each_search_mode_returns_ITS_OWN_payload(self, seeded, mode,
                                                     assertion):
        """Every advertised `mode=` is driven AND its payload asserted.

        An earlier draft only proved `hybrid` and let the other five pass on
        `out is not None` — which any error dict satisfies. Each arm returns a
        DIFFERENT shape, so each is asserted against its own.
        """
        door, s, _ = seeded
        args = {"query": "capstone", "mode": mode}
        if mode == "tag":
            # No tag is seeded, so this arm's correct answer is the empty
            # ENVELOPE — asserted by its keys and total, not by non-emptiness.
            args["tag"] = "capstone-no-such-tag"
        if mode == "issue_insight":
            # That arm matches a WOULD-BE issue against the graph, and its
            # relevance gate drops a one-token overlap: measured, `query=
            # 'capstone'` alone answers `has_prior: false` with no rows, because
            # the gate needs a measured belief or >= 2 shared tokens. The title
            # below shares three tokens with the seeded claim, which is what puts
            # the seed INSIDE the payload instead of asserting an empty set.
            args["title"] = "capstone V2 lane proves payloads"
        status, out = door.call("tortoise_search_knowledge", args)
        assert status == 200
        assert not (isinstance(out, dict) and "unknown mode" in str(
            out.get("error", "")).lower()), (
            f"mode={mode!r} was refused as unknown: {out}")

        if assertion == "claim":
            rows = out.get("results") if isinstance(out, dict) else out
            assert s["claim"] in _ids(rows), (mode, out)
        elif assertion == "tag_envelope":
            assert isinstance(out, dict), (mode, out)
            assert set(out) >= {"results", "total", "hasMore"}, (mode, out)
            assert out["total"] == 0 and out["results"] == [], (mode, out)
        elif assertion == "session":
            assert isinstance(out, list), (mode, out)
            # the agent session seeded for this fixture must be findable
            assert s["session"] in {r.get("session_id") for r in out
                                     if isinstance(r, dict)}, (mode, out)
        elif assertion == "insight":
            assert isinstance(out, dict), (mode, out)
            assert out.get("has_prior") is True, (
                "the insight arm answered 'no prior work' over a graph that "
                f"carries the seeded claim: {out}")
            points = out.get("data_points")
            assert isinstance(points, list) and points, (mode, out)
            # ⛔ key PRESENCE was the whole assertion before r6 (`#13886` F4,
            # and the same finding in the previous round): `{"has_prior": False,
            # "data_points": []}` satisfied it. The seeded content has to be IN
            # the rows, or the arm is proven to answer, not to answer correctly.
            assert any("capstone V2 lane" in str(dp.get("content") or "")
                       for dp in points if isinstance(dp, dict)), (
                f"the insight rows do not carry the seeded claim: {out}")

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

    def test_query_text_arm_returns_the_seeded_claim(self, seeded):
        """`text=` routes through ``tortoise_fts_query`` — a DIFFERENT accessor
        from the structural arm (``sdk.paginated_query``) the case above drives,
        so the two are different queries behind one name and neither proves the
        other. Undriven until the r5 review, which had the structural case as
        the only `tortoise_query` arm in the file."""
        door, s, _ = seeded
        status, out = door.call("tortoise_query", {"text": "capstone"})
        assert status == 200
        assert isinstance(out, list) and out, out
        assert s["claim"] in _ids(out), out

    @pytest.mark.parametrize("argument,value", [
        ("min_confidence", 0.5),
        ("order_by", "confidence"),
    ])
    def test_query_REFUSES_a_structural_arm_argument_it_cannot_apply(
            self, seeded, argument, value):
        """#12500: ``paginated_query`` — the accessor both structural arms read
        through — takes neither a confidence floor nor an ordering, so the arm
        REFUSES them instead of dropping them. Before #12500 the value was
        accepted and discarded and the UNFILTERED population was served as if
        it had applied: a wrong answer that looked like a right one.

        Asserting the refusal NAMES the knob and the arm that can apply it is
        what distinguishes this from the dropped-value behaviour — a payload
        carrying `results` means the argument was silently ignored."""
        door, _s, _ = seeded
        status, out = door.call("tortoise_query",
                                {"kind": "statement", argument: value})
        assert status == 200
        assert isinstance(out, dict), out
        error = str(out.get("error") or "")
        assert argument in error, out
        assert "structural arm" in error, out
        assert "text=" in error, (
            f"the refusal must name the arm that CAN apply it: {out}")
        assert "results" not in out, (
            f"the refused argument was dropped and the read served anyway: {out}")

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
        door, _s, _ = seeded
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
        door, _s, _ = seeded
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
        door, _s, _ = seeded
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
        door, _s, _ = seeded
        _status, out = door.call("tortoise_create_entity",
                                {"type": "point",
                                 "name": "One belief kind, asserted."})
        node = out.get("node") or out
        assert node.get("pointKind") == "statement", out

    def test_operator_write_returns_an_id_the_get_lane_reads_back(self, seeded):
        """`create_operator` over a FRESH pair (the seeded pair already carries
        IMPL, and the mechanism set is closed — #12067)."""
        door, _s, _ = seeded
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
        """Asserts the EDGE ITSELF, not merely the absence of an error — a
        no-op or a wrongly-directed edge must fail."""
        door, s, _ = seeded
        status, out = door.call("tortoise_create_edge",
                                {"source_id": s["claim"],
                                 "target_id": s["object"],
                                 "predicate": "produces"})
        assert status == 200
        assert not (isinstance(out, dict) and out.get("error")), out
        edge = out.get("edge") or {}
        assert edge.get("relation") == "produces", out
        assert edge.get("from") == s["claim"], out
        assert edge.get("to") == s["object"], out
        assert out.get("created") is True, out

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

    def test_supersede_object_arm_reports_the_family_supersede(self, seeded):
        """`old_id` dispatches on the OLD node's label (`mcp_server.py:6111`):
        a Point gets the CORRECTS arm, an Object/Subject gets the FAMILY arm,
        whose payload is a different one
        (``{superseded, id, label, superseded_by, superseded_at}``). The Point
        case above never touches it, so the family arm was unasserted.

        The successor is created over the wire, and the write is read back —
        the payload alone would pass on a supersede that reported the
        transition without applying it."""
        door, s, _ = seeded
        made = door.call("tortoise_create_entity",
                         {"type": "object", "name": "Widget v2"})[1]
        successor = (made.get("node") or made)["id"]
        status, out = door.call("tortoise_supersede_knowledge",
                                {"old_id": s["object"], "new_id": successor})
        assert status == 200
        assert out.get("superseded") is True, out
        assert out.get("id") == s["object"], out
        assert out.get("label") == "Object", out
        assert out.get("superseded_by") == "Widget v2", out
        assert out.get("superseded_at"), out
        assert "invalidated" not in out, (
            f"the POINT arm answered an Object supersede: {out}")
        status, got = door.call("tortoise_get_entity",
                                {"id": s["object"], "type": "entity"})
        assert got.get("status") == "superseded", got
        assert got.get("supersededBy") == "Widget v2", got

    def test_supersede_dry_run_previews_the_family_arm_and_writes_nothing(
            self, seeded):
        """`dry_run=True` previews the SAME arm: it must name which arm it would
        run and change nothing. A preview that answered generically (or that
        applied the write) would pass a status-only check and fail a person
        reading it before deciding."""
        door, s, _ = seeded
        made = door.call("tortoise_create_entity",
                         {"type": "object", "name": "Gadget v2"})[1]
        successor = (made.get("node") or made)["id"]
        status, out = door.call("tortoise_supersede_knowledge",
                                {"old_id": s["object"], "new_id": successor,
                                 "dry_run": True})
        assert status == 200
        assert out.get("dry_run") is True, out
        assert out.get("found") is True, out
        assert "family supersede" in str(out.get("would") or ""), out
        assert (out.get("target") or {}).get("superseded_by") == "Gadget v2", out
        assert "No changes were made" in str(out.get("note") or ""), out
        status, got = door.call("tortoise_get_entity",
                                {"id": s["object"], "type": "entity"})
        assert got.get("status") != "superseded", (
            f"a dry-run supersede mutated the node: {got}")

    def test_delete_dry_run_previews_the_seeded_node(self, seeded):
        """`dry_run` must not delete — the payload names what WOULD go, and a
        confirmation token proves the preview was computed against the node."""
        door, s, _sdk = seeded
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

    def test_delete_confirm_arm_deletes_the_node_and_the_read_agrees(self, seeded):
        """The CONFIRMED half of the one destructive write on the surface — the
        only arm this file drove was `dry_run`, the one that changes nothing
        (``mcp_server.py:7037-7041``). Two behaviours V2 owns live only there:
        `_deletion.verify` must accept the token the PREVIEW issued, and
        `deleted` is computed from ``bool(result)``, so a falsy-but-correct
        delete reports ``deleted: False`` — the destructive write claiming it
        changed nothing while the row is gone.

        A throwaway node, so the seed every other arm reads stays intact."""
        door, _s, _ = seeded
        made = door.call("tortoise_create_entity",
                         {"type": "point", "name": "Doomed capstone claim."})[1]
        doomed = (made.get("node") or made)["id"]
        status, preview = door.call("tortoise_delete",
                                    {"id": doomed, "dry_run": True})
        assert status == 200
        assert preview.get("found") is True, preview
        token = preview.get("confirmation")
        assert token, preview
        status, out = door.call("tortoise_delete",
                                {"id": doomed, "confirmation": token})
        assert status == 200
        assert out.get("deleted") is True, out
        assert out.get("id") == doomed, out
        status, got = door.call("tortoise_get_entity",
                                {"id": doomed, "type": "point"})
        assert not (isinstance(got, dict) and got.get("id") == doomed), (
            f"the node the delete reported as deleted is still readable: {got}")

    def test_delete_refuses_a_token_issued_for_a_DIFFERENT_node(self, seeded):
        """``deletion.py:159`` binds the token to ONE target — the module's own
        contract is "it never falls through to a delete". Driven end to end
        because that binding is the whole point of the two-call protocol
        (#9660): a token minted for one claim must not destroy another."""
        door, s, _ = seeded
        _status, preview = door.call("tortoise_delete",
                                     {"id": s["claim"], "dry_run": True})
        token = preview["confirmation"]
        status, out = door.call("tortoise_delete",
                                {"id": s["claim2"], "confirmation": token})
        assert status == 200
        # ⛔ The WIRE SHAPE, not only the wording (`#13886` r8, F2). This
        # refusal is a bare-string answer, and `_text_of` renders a dict too —
        # so before this line, reverting `_tool_payload`'s `_PlainText` to the
        # pre-r6 `{"_text": text}` left the row GREEN (measured: 2 passed, the
        # file's red count unchanged). A `str` refusal is precisely what must
        # not satisfy a structured-payload arm; assert that here.
        assert isinstance(out, str) and not isinstance(out, dict), (
            f"a TEXT refusal must not arrive as a structured payload: {out!r}")
        assert "different item" in _text_of(out), out
        status, still = door.call("tortoise_get_entity",
                                  {"id": s["claim2"], "type": "point"})
        assert still.get("id") == s["claim2"], still

    def test_delete_refuses_a_bogus_token_and_destroys_nothing(self, seeded):
        """A malformed token is a REFUSAL, not a delete — asserted on the node,
        not on the error text alone: a handler that raised after deleting would
        pass a message-only check."""
        door, s, _ = seeded
        status, out = door.call("tortoise_delete",
                                {"id": s["claim"],
                                 "confirmation": "not-a-token"})
        assert status == 200
        assert isinstance(out, str) and not isinstance(out, dict), (
            f"a TEXT refusal must not arrive as a structured payload: {out!r}")
        assert "not a deletion confirmation token" in _text_of(out), out
        status, still = door.call("tortoise_get_entity",
                                  {"id": s["claim"], "type": "point"})
        assert still.get("id") == s["claim"], still

    def test_diary_write_arm_round_trips_through_diary_read(self, seeded):
        """The 6th `create_entity` arm (`type='diary'`) — the absorbed
        `tortoise_diary_write` door. Seeding a diary through the SDK would
        bypass the transport and leave the whole write arm unproven."""
        door, _s, _ = seeded
        status, out = door.call("tortoise_create_entity",
                                {"type": "diary",
                                 "name": "A diary entry written over the wire.",
                                 "props": {"authoredBy": "wire-author"}})
        assert status == 200
        node = out.get("node") or out
        assert node.get("pointKind") == "diary", out
        assert node.get("authoredBy") == "wire-author", out
        status, rows = door.call("tortoise_list_knowledge",
                                 {"kind": "diary", "agent": "wire-author"})
        assert node["id"] in _ids(rows), rows

    def test_register_source_returns_the_seeded_url(self, seeded):
        door, _s, _ = seeded
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
        probe got wrong (it sent a string and was refused by validation).

        Asserts the COUNTS: one decision, one point per option, one per
        evidence statement. A write that filed nothing would pass a
        no-error-only check.
        """
        door, _s, _ = seeded
        status, out = door.call("tortoise_write_question",
                                {"options": ["Ship it", "Hold it"],
                                 "evidence": ["The suite is green."],
                                 "choice": 0})
        assert status == 200
        assert not (isinstance(out, dict) and out.get("error")), out
        assert out.get("decision_id"), out
        assert len(out.get("option_ids") or []) == 2, out
        assert len(out.get("evidence_ids") or []) == 1, out
        # the filed question is a real point
        status, got = door.call("tortoise_get_entity",
                                {"id": out["decision_id"], "type": "point"})
        assert got.get("id") == out["decision_id"], got

    def test_record_decision_records_against_a_real_approver(self, seeded):
        """`artifact_id` must name a real artifact (the seeded Object) — the
        refusal for a non-existent one names the id, which is what the first
        probe hit when it passed a Point id.

        Asserts the RECORD it wrote: an event, a decision point, and the IMPL
        approval edge. "No error" would pass on a write of nothing.
        """
        door, s, _ = seeded
        status, out = door.call("tortoise_record_decision",
                                {"approver_id": s["subject"],
                                 "artifact_id": s["object"],
                                 "point_ids": [s["claim"]],
                                 "decision_content": "Approved by the capstone."})
        assert status == 200
        assert not (isinstance(out, dict) and out.get("error")), out
        assert out.get("event_id"), out
        assert out.get("decision_point_id"), out
        assert len(out.get("impl_operator_ids") or []) == 1, out
        status, got = door.call("tortoise_get_entity",
                                {"id": out["decision_point_id"],
                                 "type": "point"})
        assert got.get("id") == out["decision_point_id"], got

    def test_ingest_returns_a_batch_receipt_for_a_REAL_bundle(self, seeded):
        """`tortoise_ingest({})` returns a CONSTANT batch id and all-zero counts,
        so an empty-bundle case proves nothing. This ingests a real point and
        asserts the receipt counted it AND that it is readable back."""
        door, _s, _ = seeded
        bundle = {"points": [{"content": "An ingested capstone point.",
                               "kind": "statement"}]}
        status, out = door.call("tortoise_ingest", {"bundle": bundle})
        assert status == 200
        assert out.get("batch_id"), out
        assert out["created"]["points"] == 1, out
        new_id = (out.get("ids") or {}).get("points") or []
        assert new_id, out
        status, got = door.call("tortoise_get_entity",
                                {"id": new_id[0], "type": "point"})
        assert got.get("id") == new_id[0], got

    def test_mine_from_session_files_a_real_item(self, seeded):
        """The raw-TRANSCRIPT arm is stdio-only BY DESIGN; the `items=` arm is
        the one served over HTTP.

        `items` entries are `{wing, room, content}` DICTS (sdk
        `_check_checkpoint_items`), not strings — a list of strings is refused
        with code -32008, and an earlier draft's only assertion ("not the
        stdio-only error") passed on that refusal. This asserts the write.
        """
        door, _s, _ = seeded
        status, out = door.call("tortoise_mine_knowledge_from_session",
                                {"items": [{"content":
                                            "A mined statement from the capstone."}]})
        assert status == 200
        assert not (isinstance(out, dict) and out.get("error")), out
        assert out.get("filed") == 1, out
        assert out.get("duplicates") == 0, out

        # the dedup contract: the same item again is a duplicate, not a misfile
        status, again = door.call("tortoise_mine_knowledge_from_session",
                                  {"items": [{"content":
                                              "A mined statement from the capstone."}]})
        assert again.get("filed") == 0 and again.get("duplicates") == 1, again


# ══════════════════════════════════════════════════════════════════════════
# 5 — GRAPH OVERVIEW: one case per `section=` arm
# ══════════════════════════════════════════════════════════════════════════

#: The `section=` table: every absorbed source, with the SHAPE its own door
#: returns. It is the parametrize TABLE itself, not a second copy beside it — the
#: previous cut carried a parallel 14-name tuple that nothing read, so it could
#: not fail under any mutant while reading as if the section set were bound.
_OVERVIEW_SECTIONS: dict[str, type] = {
    "taxonomy": dict,
    "structure": dict,
    "structure_check": list,
    "pointkinds": list,
    "tags": list,
    "sources": list,
    "namespaces": list,
    "graphs": list,
    "topics": dict,
    "health": dict,
    "status": dict,
    "stale": dict,
    "validate_domain": dict,
    "audit": dict,
}

#: Sections the container SERVES that this row cannot drive through the tenant
#: HTTP transport, with the MEASURED mechanism. Recorded rather than asserted as
#: "something came back": over HTTP the refusal IS the correct payload, and its
#: code is what distinguishes it from a break.
_OVERVIEW_SECTION_GAPS: dict[str, str] = {
    "dream_health":
        "ARM GAP — MEASURED: `section='dream_health'` is REFUSED at the tenant "
        "HTTP transport with JSON-RPC -32004 (`This tool is not available over "
        "HTTP. Use the hosted REST API or stdio MCP.`), the same transport "
        "boundary that makes `tortoise_refresh_confidence` UNDRIVEN above. "
        "Nothing about the section is broken; this lane cannot reach it.",
}


class TestGraphOverviewSections:
    """Part A records `section=` as this target's discriminator — 15 absorbed
    sources, so a single-arm proof would be a token gesture.

    Each arm is asserted against the SHAPE ITS OWN DOOR returns, read off the
    live surface — not against a shared "something came back". An arm that
    drifted to another door's shape (a dict where a list belongs) fails.

    The table answers to the PRODUCT's own section set, not to itself:
    ``test_the_section_table_covers_every_source_the_container_serves`` binds it,
    so a section source added to the SDK reds this file until it gets a case or a
    recorded gap — the same anti-silence shape as ``_DRIVEN_BY_TEST``. Without
    that bind the table is 14 of the 15 sections the container serves, and the
    missing one is invisible.
    """

    def test_the_section_table_covers_every_source_the_container_serves(self):
        """`driven ∪ gaps == served`, exactly — and the served set is not
        hand-typed here: it is `TortoiseSDK.GRAPH_OVERVIEW_SECTIONS` PLUS
        `mcp_server._OVERVIEW_TRANSPORT_ONLY_SECTIONS`, the pair
        `tests/test_11609_read_containers.py` already pins in both directions.

        A section source added to the product with no case and no gap fails this,
        which is what stops the table from being a 14-of-15 hand-typed list.
        """
        from tortoise import mcp_server as ms
        served = set(TortoiseSDK.GRAPH_OVERVIEW_SECTIONS) | set(
            ms._OVERVIEW_TRANSPORT_ONLY_SECTIONS)
        driven = set(_OVERVIEW_SECTIONS)
        assert driven | set(_OVERVIEW_SECTION_GAPS) == served, (
            "section source(s) served by the container with no case and no "
            f"recorded gap: {sorted(served - driven - set(_OVERVIEW_SECTION_GAPS))}"
            "; recorded gap(s) the container no longer serves: "
            f"{sorted(set(_OVERVIEW_SECTION_GAPS) - served)}")
        assert not (driven & set(_OVERVIEW_SECTION_GAPS)), (
            "a section is both driven and recorded as a gap: "
            f"{sorted(driven & set(_OVERVIEW_SECTION_GAPS))}")

    def test_the_dream_health_gap_is_the_measured_refusal(self, seeded):
        """The recorded gap must be the OBSERVED answer, not a note: -32004 over
        the tenant transport. If the section ever starts answering here the gap is
        stale, and this arm is what notices."""
        door, _, _ = seeded
        _, out = door.call("tortoise_graph_overview",
                           {"section": "dream_health"})
        text = json.dumps(out)
        assert "-32004" in text or "not available over HTTP" in text, (
            f"the recorded gap is stale — `dream_health` answered: {out!r}")


    @pytest.mark.parametrize("section,expected", sorted(_OVERVIEW_SECTIONS.items()))
    def test_each_section_answers_its_own_type(self, seeded, section, expected):
        door, s, _ = seeded
        args = {"section": section}
        if section == "validate_domain":
            args["domain"] = "dev"
        if section == "topics":
            args["entity_id"] = s["claim"]
        status, out = door.call("tortoise_graph_overview", args)
        assert status == 200
        # An ERROR envelope is a dict, so a bare isinstance check would accept
        # `{"error": …}` for every dict-shaped arm. This parametrized type check
        # is a SHAPE gate, never the whole assertion: every member of the table
        # has a content test below that reads the seeded graph back out —
        # `structure_check` included (`test_structure_check_section_reports_the_
        # seeded_orphan`), which is what the r6 review found missing when this
        # comment admitted the type check was that arm's ONLY assertion
        # (`#13886` F3).
        assert not (isinstance(out, dict) and "error" in out), (
            f"section={section!r} answered an error envelope: {out!r}")
        assert isinstance(out, expected), (
            f"section={section!r} answered {type(out).__name__}, expected "
            f"{expected.__name__}: {out!r}")

    def test_taxonomy_section_counts_every_seeded_kind(self, seeded):
        door, _s, _ = seeded
        _status, out = door.call("tortoise_graph_overview",
                                {"section": "taxonomy"})
        assert out.get("Point", 0) >= 3, out
        assert out.get("Object", 0) >= 1, out
        assert out.get("Subject", 0) >= 1, out
        assert out.get("Source", 0) >= 1, out
        assert out.get("Event", 0) >= 1, out

    def test_stale_section_answers_its_envelope(self, seeded):
        door, _s, _ = seeded
        _status, out = door.call("tortoise_graph_overview",
                                {"section": "stale"})
        assert set(out) >= {"stale", "count", "cutoff", "limit"}, out
        assert isinstance(out["stale"], list), out
        assert out["count"] == 0, out

    def test_graphs_and_namespaces_name_the_served_tenant(self, seeded):
        door, s, _ = seeded
        _status, out = door.call("tortoise_graph_overview",
                                 {"section": "graphs"})
        assert f"org_{s['org']}" in {str(g) for g in out}, out
        _status, ns = door.call("tortoise_graph_overview",
                              {"section": "namespaces"})
        assert all(isinstance(r, dict) for r in ns), ns
        assert all("namespace" in r for r in ns), ns

    def test_tags_section_answers_an_empty_list_when_none_are_seeded(self, seeded):
        """No tag is seeded, so `[]` is correct — asserted as the list value so
        a crash or an envelope still fails."""
        door, _s, _ = seeded
        _status, out = door.call("tortoise_graph_overview",
                                {"section": "tags"})
        assert out == [], out

    def test_structure_check_section_reports_the_seeded_orphan(self, seeded):
        """The container's `section='structure_check'` arm is the same read as
        `tortoise_check_structure`, and it is the one WRAP member of the table —
        so the type gate above proved only that it answers a list (`#13886` r6,
        F3). The seeded orphan `useCase` must be in it."""
        door, s, _ = seeded
        status, out = door.call("tortoise_graph_overview",
                                {"section": "structure_check"})
        assert status == 200
        assert isinstance(out, list), out
        by_id = {r.get("id"): r for r in out if isinstance(r, dict)}
        assert s["use_case"] in by_id, (
            f"the seeded orphan useCase is not in the section report: {out}")
        assert by_id[s["use_case"]].get("type") == "orphan_use_case", out

    def test_structure_section_counts_the_seeded_operator(self, seeded):
        """The seeded IMPL operator must be visible here — that is the arm's
        whole question."""
        door, _s, _ = seeded
        _status, out = door.call("tortoise_graph_overview",
                                {"section": "structure"})
        assert out.get("operators") == 1, out
        assert out.get("total", 0) >= 3, out

    def test_pointkinds_section_counts_the_seeded_statements(self, seeded):
        door, _s, _ = seeded
        _status, out = door.call("tortoise_graph_overview",
                                {"section": "pointkinds"})
        assert isinstance(out, list) and out, out
        by_kind = {row["kind"]: row["count"] for row in out
                   if isinstance(row, dict) and "kind" in row}
        assert by_kind.get("statement", 0) >= 2, out

    def test_status_section_names_the_served_graph(self, seeded):
        door, _s, _ = seeded
        _status, out = door.call("tortoise_graph_overview",
                                {"section": "status"})
        assert out.get("connected") is True, out
        assert isinstance(out.get("counts"), dict), out
        assert out.get("total_entities", 0) > 0, out
        assert out.get("namespace"), out

    def test_topics_section_echoes_the_seeded_entity(self, seeded):
        door, s, _ = seeded
        _status, out = door.call("tortoise_graph_overview",
                                {"section": "topics",
                                 "entity_id": s["claim"]})
        assert out.get("id") == s["claim"], out
        assert "neighbors" in out, out

    def test_validate_domain_accepts_a_real_domain(self, seeded):
        """`validate_domain` takes a DOMAIN NAME from the pack set; `core` is
        not one and is refused by name. Proving the arm needs a real domain."""
        door, _s, _ = seeded
        _status, out = door.call("tortoise_graph_overview",
                                {"section": "validate_domain",
                                 "domain": "dev"})
        assert out.get("domain") == "dev", out
        assert out.get("ok") is True, out
        assert isinstance(out.get("violations"), list), out

    def test_unknown_domain_is_refused_naming_the_valid_set(self, seeded):
        door, _s, _ = seeded
        _status, out = door.call("tortoise_graph_overview",
                                {"section": "validate_domain",
                                 "domain": "core"})
        assert isinstance(out, dict) and "error" in out, out
        assert "known:" in out["error"], out

    def test_audit_section_walks_the_seeded_nodes(self, seeded):
        door, _s, _ = seeded
        _status, out = door.call("tortoise_graph_overview",
                                {"section": "audit"})
        assert out.get("node_count", 0) >= 3, out
        assert out.get("edge_count", 0) >= 2, out
        assert isinstance(out.get("checks"), list), out

    def test_combined_summary_counts_the_seeded_nodes(self, seeded):
        """No `section=` → the combined summary. It must COUNT the seed: a
        Point count of zero over a seeded graph is the wrong-payload failure."""
        door, _s, _ = seeded
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
        door, _s, _ = seeded
        _status, out = door.call("tortoise_graph_overview",
                                {"section": "health"})
        assert out.get("status") == "ok", out
        assert out.get("falkordb") == "connected", out
        assert isinstance(out.get("graph_size"), int) and out["graph_size"] > 0, out

    def test_sources_section_contains_the_seeded_source(self, seeded):
        door, s, _ = seeded
        _status, out = door.call("tortoise_graph_overview",
                                {"section": "sources"})
        assert isinstance(out, list), out
        assert s["source"] in {
            row.get("url") for row in out if isinstance(row, dict)}, out

    def test_dream_health_section_is_http_excluded(self, seeded):
        """The one `section=` value that is NOT servable on this lane. Recorded
        as an explicit refusal assertion, never silently skipped."""
        door, _s, _ = seeded
        _status, out = door.call("tortoise_graph_overview",
                                {"section": "dream_health"})
        assert isinstance(out, dict) and "error" in out, out
        assert "HTTP" in str(out["error"]), out


# ══════════════════════════════════════════════════════════════════════════
# 6 — CONFIDENCE: each argument-arm
# ══════════════════════════════════════════════════════════════════════════

#: The `mode=` values the `recall=True` arm dispatches on, and which assertion
#: shape drives each. Bound to the CONTAINER's own tuple
#: (`mcp_server._RECALL_MODES`) by the bind test below — the table does not
#: answer to itself (`#13886` r6, F5).
_RECALL_MODE_SHAPES: dict[str, str] = {
    "state": "state_family",
    "gaps": "state_family",
    "custom": "state_family",
    "subgraph": "subgraph",
}

#: The three state-family modes share ONE payload assertion; `subgraph` has its
#: own shape. Derived from the table so the two cannot drift apart.
_RECALL_STATE_FAMILY = tuple(
    mode for mode, shape in _RECALL_MODE_SHAPES.items()
    if shape == "state_family")


class TestConfidenceArms:

    def test_the_recall_mode_table_is_the_containers_mode_set(self):
        """`driven == mcp_server._RECALL_MODES`, exactly.

        Same anti-silence bind as the `section=` and search `mode=` tables
        (`#13886` r6, F5): the recall cases below answer only to themselves, so a
        mode the container adds — or one this file drops — was invisible, while
        the refusal case below asserts the served set from the CONTAINER's own
        message. `_RECALL_MODES` is the tuple that message is built from
        (`mcp_server.py:5637`), so binding to it makes the two agree by
        construction rather than by hand.
        """
        from tortoise import mcp_server as ms

        assert set(_RECALL_MODE_SHAPES) == set(ms._RECALL_MODES), (
            "the recall `mode=` table and the container's own mode set disagree: "
            f"{sorted(set(_RECALL_MODE_SHAPES) ^ set(ms._RECALL_MODES))}")

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
        remedy. Per-claim scoping (#7739) is only proven if the refusal names
        THIS claim — the pre-#7739 whole-graph refusal would pass a check that
        merely looked for the word "uncalibrated"."""
        door, s, _ = seeded
        status, out = door.call("tortoise_check_confidence",
                                {"claim_id": s["claim"]})
        assert status == 200
        assert isinstance(out, dict), out
        text = json.dumps(out)
        assert "uncalibrated" in text, out
        assert "calibrate_summary" in text, out
        assert s["claim"] in text, (
            "the refusal must name the claim it was asked about: " f"{out}")
        assert s["calibrated"] not in text, (
            "the refusal named a DIFFERENT claim — scoping is not per-claim: "
            f"{out}")

    def test_session_arm_returns_the_diary_and_prior_state(self, seeded):
        """`no_prior_sessions` must be FALSE (the seed has points) and the
        seeded claim must appear in `recent_points` — an arm blind to the graph
        returning `true` with no rows would pass a key-presence check."""
        door, s, _ = seeded
        status, out = door.call("tortoise_check_confidence", {"session": True})
        assert status == 200
        assert isinstance(out, dict), out
        assert out.get("no_prior_sessions") is False, out
        assert s["claim"] in _ids(out.get("recent_points")), out

    def test_topic_arm_returns_the_decision_chain_for_the_topic(self, seeded):
        """`topic=` dispatches to `belief_timeline`, whose contract is the `decision`
        Points `aboutObject`-linked to the topic ENTITY — so the seed carries an
        Object plus a decision Point wired to it, and the payload is asserted as
        that chain. (A Point carrying a `topic=` property is not an entity and
        owns no edge: asserting its empty answer would have been a seed defect
        recorded as a measurement.)"""
        door, s, _ = seeded
        status, out = door.call("tortoise_check_confidence",
                               {"topic": s["topic"]})
        assert status == 200
        assert isinstance(out, list) and out, out
        assert all(r.get("pointKind") == "decision" for r in out), out
        contents = [r.get("content") or "" for r in out]
        assert any("capstone retrieval window" in c for c in contents), (
            "the chain must carry the seeded decision Point, not just any "
            f"decision Points: {out}")

    def test_recompute_arm_returns_confidence_numbers(self, seeded):
        """`recompute=True` refuses to be combined with `claim_id` — it selects its
        own arm — so it is driven alone, ANCHORED on the calibrated seed.

        The anchor is load-bearing: over HTTP with neither `factors` nor `anchors`
        the retired door short-circuits a clean request-scoped SDK to
        `diagnostic: "no_dirty_state_http"`, so an unanchored call would assert the
        short-circuit instead of the arm. The payload is what the arm EXISTS for:
        a per-point posterior, so a refusal envelope or an empty `confidences` map
        must fail this row."""
        door, s, _ = seeded
        status, out = door.call("tortoise_check_confidence", {
            "recompute": True,
            # anchor the seeded CLAIM: it is the target of the seeded IMPL
            # operator, so the BFS selection from it reaches a real factor set.
            # An isolated anchor (one that reaches no factor) answers the
            # `no_factors` early return, which is a different arm.
            "anchors": [s["claim"]],
            # explicit opt-out: the arm's fail-closed default (True, #344) refuses
            # a subgraph containing an uncalibrated point, which would test the
            # calibration gate rather than the posterior this row is about.
            "require_calibration": False,
        })
        assert status == 200
        assert isinstance(out, dict), out
        assert "error" not in out, out
        assert out.get("diagnostic") != "no_dirty_state_http", out
        assert out.get("diagnostic") != "no_factors", out
        confidences = out.get("confidences")
        assert isinstance(confidences, dict) and confidences, out
        assert s["claim"] in confidences, out
        posterior = confidences[s["claim"]]
        assert isinstance(posterior, dict), posterior
        assert 0.0 <= float(posterior["mean"]) <= 1.0, posterior
        assert float(posterior["variance"]) >= 0.0, posterior

    @pytest.mark.parametrize("mode", _RECALL_STATE_FAMILY)
    def test_recall_arm_answers_the_seeded_claim(self, seeded, mode):
        """`recall=True` is the arm the retired ``tortoise_recall`` door retires
        INTO — the container names it in its own dispatch set
        (``mcp_server.py:8220``) and dispatches it at ``:8358``. It was the ONE
        documented arm this file drove nothing for, so the tool still read as
        covered while all four recall intents (``state | gaps | subgraph |
        custom``) carried no payload assertion anywhere.

        It is reachable over the tenant door: ``tortoise_recall`` carries no
        ``_transport_mode`` guard and no ``_http_excluded_error()``, so this is
        a measured gap in the row, not an environmental one.

        The three state-family modes are parameterised because ``gaps`` reads a
        DIFFERENT accessor (``recall_gaps``) under its own presets while
        ``custom`` skips the preset tuning — three queries behind one mode
        argument, and the ``mode`` the arm ECHOES is asserted alongside the
        seeded claim so a dispatch that clamped every mode to the default cannot
        pass by returning a well-formed payload of another arm."""
        door, s, _ = seeded
        status, out = door.call("tortoise_check_confidence",
                                {"recall": True, "mode": mode,
                                 "query": "capstone"})
        assert status == 200
        assert isinstance(out, dict), out
        assert out.get("mode") == mode, out
        rows = out.get("results")
        assert isinstance(rows, list) and rows, out
        by_id = {r.get("id"): r for r in rows if isinstance(r, dict)}
        assert s["claim"] in by_id, (
            "the state read must serve the seeded claim it matched by name — "
            f"got ids {sorted(str(k) for k in by_id)}")
        assert "capstone V2 lane" in (by_id[s["claim"]].get("content") or ""), \
            by_id[s["claim"]]

    def test_recall_arm_answers_the_subgraph_mode_shape(self, seeded):
        """``mode="subgraph"`` returns ``{nodes, edges, stats}`` — a DIFFERENT
        shape from the state/gaps/custom family, which is what makes it the
        mode that proves the arm CARRIES the caller's ``mode=`` instead of
        serving the default. The walk is anchored on the seeded IMPL: the
        source claim is the seed and the operator's target must be reachable."""
        door, s, _ = seeded
        status, out = door.call("tortoise_check_confidence",
                                {"recall": True, "mode": "subgraph",
                                 "seed": s["claim"]})
        assert status == 200
        assert isinstance(out, dict), out
        assert out.get("mode") == "subgraph", out
        assert "results" not in out, (
            f"the subgraph mode answered the state shape: {out}")
        node_ids = {n.get("id") for n in out.get("nodes") or []
                    if isinstance(n, dict)}
        assert s["claim"] in node_ids, out
        assert s["claim2"] in node_ids, (
            "the walk must reach the operator's target through the seeded "
            f"IMPL: {out}")
        assert out.get("edges"), out
        assert isinstance(out.get("stats"), dict), out

    def test_recall_arm_refuses_an_unknown_mode_naming_the_served_set(self, seeded):
        """The arm's own mode contract: an unknown mode is REFUSED and the
        refusal names the four it serves. An arm that answered the default for
        any mode string would pass a refusal-free check and hide a caller's
        typo."""
        door, _s, _ = seeded
        status, out = door.call("tortoise_check_confidence",
                                {"recall": True, "mode": "bogus"})
        assert status == 200
        assert isinstance(out, dict), out
        assert out.get("mode") == "bogus", out
        error = str(out.get("error") or "")
        assert "not recognized" in error, out
        assert "state|gaps|subgraph|custom" in error, out

    def test_calibrate_arm_answers_the_calibration_view(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_check_confidence", {"calibrate": True})
        assert status == 200
        assert isinstance(out, list), out
        assert s["claim"] in _ids(out), out

    def test_calibrate_summary_lists_the_seeded_claim(self, seeded):
        """Asserts the ROWS — a `not None` / `isinstance` pair is satisfiable by
        every branch `_tool_payload` can return, so it proved nothing."""
        door, s, _ = seeded
        status, out = door.call("tortoise_calibrate_summary", {})
        assert status == 200
        assert isinstance(out, list) and out, out
        assert s["claim"] in _ids(out), out
        assert all(isinstance(r, dict) for r in out), out
        assert all("calibrated" in r for r in out), out


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

    def test_adjust_relationship_requires_reason_then_writes_a_mitigation(
            self, seeded):
        """The mitigation arm refuses `strength=` alone — that refusal IS the
        contract. With a reason it does not mutate the operator: it writes a
        MITIGATION Point carrying `mitigation_strength` and the reason
        (measured on this head). Asserting "the operator changed" was wrong and
        would have reddened a correct implementation; asserting the returned
        mitigation is the payload this arm actually promises."""
        door, s, _ = seeded
        status, out = door.call("tortoise_adjust_relationship",
                                {"id": s["operator"], "strength": 0.5})
        assert status == 200
        assert isinstance(out, dict) and "reason" in str(out.get("error", "")), out

        status, mit = door.call("tortoise_adjust_relationship",
                                {"id": s["operator"], "strength": 0.25,
                                 "reason": "capstone V2 weakens the seeded edge"})
        assert status == 200
        assert not (isinstance(mit, dict) and mit.get("error")), mit
        assert mit.get("mitigation_strength") == 0.25, mit
        assert "capstone V2 weakens the seeded edge" in str(mit.get("content")), mit
        assert mit.get("baseline_set") is True, mit
        # the mitigation is a real, readable point
        status, got = door.call("tortoise_get_entity",
                                {"id": mit["id"], "type": "point"})
        assert got.get("id") == mit["id"], got

    def test_review_link_candidates_answers_both_directions(self, seeded):
        """`add` is a LIST OF CANDIDATES, so a key-presence check proves only
        that the envelope exists (`#13886` r6, F3).

        ⛔ BOTH halves are ENGINEERED, because an incidental pass is not a
        measurement (`#13886` r8, F1 — the pre-r8 version of this row named
        `claim2`, which the seed CONNECTS via the IMPL operator, so
        `_review_add` excludes that pair by construction and the row's verdict
        came from an unengineered pair clearing the 0.72 band: measured RED on a
        box without the embeddings extra and GREEN on one with it, same test
        code):

        * ``review_pair`` — verbatim-identical, NO edge ⇒ the arm must PROPOSE
          it, naming the relation it suggests. Verbatim wording scores 1.000
          under the bge-small cosine AND under the TF-IDF fallback, so the
          assertion holds on a keyword-only box too;
        * ``{claim, claim2}`` — also verbatim-identical, but joined by the seeded
          IMPL ⇒ it must NOT be proposed. That is the ``connected`` skip in
          ``sdk._review_add``; with the pair BELOW the band (as it was before
          r8) removing the skip reddened nothing.

        The row's PAYLOAD invariants are asserted on the proposed pair as well,
        because "a row came back" is the key-presence check this row exists to
        replace: the similarity FLOOR the arm applied is the product's own
        default, the queue is ordered most-similar-first, and the row names the
        relation it suggests and the score it measured. (The endpoints' sorted
        shape is NOT asserted: a mutant that emits `(a, b)` unsorted stays green
        on this seed, so that assertion would be dead weight — rule 3.)

        `prune` stays empty — nothing superseded is seeded — and is asserted as
        the list a crash would break."""
        from tortoise.embeddings import DEFAULT_THRESHOLD

        door, s, _ = seeded
        status, out = door.call("tortoise_review_link_candidates", {})
        assert status == 200
        assert isinstance(out, dict), out
        add = out.get("add")
        assert isinstance(add, list) and add, out

        def _rows_touching(point_id):
            return [row for row in add if isinstance(row, dict)
                    and point_id in (row.get("from"), row.get("to"))]

        left, right = s["review_pair"]
        proposed = [row for row in _rows_touching(left)
                    if right in (row.get("from"), row.get("to"))]
        assert proposed, (
            "the seeded UNCONNECTED pair is not proposed by the link-candidate "
            f"read (left={left} right={right}): {out}")
        assert all(row.get("suggested_relation") for row in proposed), out

        connected_pairs = [row for row in _rows_touching(s["claim"])
                           if s["claim2"] in (row.get("from"),
                                              row.get("to"))]
        assert not connected_pairs, (
            "a pair that ALREADY shares the seeded IMPL operator was proposed "
            f"as a missing connection — the `connected` skip is not holding: "
            f"{connected_pairs}")

        # ── the proposed row's payload, not just its presence ──────────────
        assert len(proposed) == 1, proposed
        row = proposed[0]
        # the floor the arm applied is the PRODUCT's default (`DEFAULT_THRESHOLD`,
        # 0.72) — asserted over the whole queue, so dropping the filter (every
        # pair, including the 0.0-0.2 tail, becomes a "candidate") reds here
        assert all(float(r["similarity"]) >= DEFAULT_THRESHOLD
                   for r in add if isinstance(r, dict)), out
        # most similar first — a caller reading the head of the queue sees the
        # best candidate, which is the arm's documented ordering
        similarities = [float(r["similarity"]) for r in add
                        if isinstance(r, dict)]
        assert similarities == sorted(similarities, reverse=True), out
        # the row names the relation it suggests, and the score it measured
        assert row["suggested_relation"] == "IMPL", out
        assert f"{float(row['similarity']):.2f}" in row["reason"], out

        assert isinstance(out.get("prune"), list), out
        assert out["prune"] == [], out

    def test_events_poll_returns_the_seeded_journal(self, seeded):
        door, s, _ = seeded
        status, out = door.call("tortoise_events_poll", {})
        assert status == 200
        events = out.get("events") if isinstance(out, dict) else out
        assert isinstance(events, list) and events, out
        assert s["claim"] in json.dumps(events), out

    def test_diary_read_returns_the_seeded_entry(self, seeded):
        door, _s, sdk = seeded
        sdk._create_point("diary", "Diary read arm entry.", authoredBy="reader")
        status, out = door.call("tortoise_diary_read",
                                {"agent_name": "reader"})
        assert status == 200
        assert isinstance(out, list) and out, out
        assert any("Diary read arm" in str(r.get("content", ""))
                   for r in out if isinstance(r, dict)), out

    def test_list_graphs_returns_the_seeded_tenant_graph(self, seeded):
        """`list_graphs` reports the STORAGE namespace, which is `org_<org_id>`
        — not the bare org id `graph_overview(section='status')` reports."""
        door, s, _ = seeded
        status, out = door.call("tortoise_list_graphs", {})
        assert status == 200
        assert isinstance(out, list) and out, out
        assert f"org_{s['org']}" in {str(g) for g in out}, out

    def test_list_namespaces_returns_the_pack_namespaces(self, seeded):
        door, _s, _ = seeded
        status, out = door.call("tortoise_list_namespaces", {})
        assert status == 200
        assert isinstance(out, list) and out, out
        assert all(isinstance(r, dict) for r in out), out
        assert all("namespace" in r for r in out), out

    def test_list_batches_answers_the_seeded_batch(self, seeded):
        """Asserting `rows == []` proved only that nothing was seeded. The
        fixture now ingests a real bundle, so the receipt's batch id must be IN
        the listing — an arm that dropped its rows, or served someone else's,
        reds here (`#13886` r6)."""
        door, s, _ = seeded
        status, out = door.call("tortoise_list_batches", {})
        assert status == 200
        rows = out.get("results") if isinstance(out, dict) else out
        assert isinstance(rows, (list, tuple)) and rows, out
        assert s["batch"] in {row.get("batch_id") for row in rows
                              if isinstance(row, dict)}, out

    def test_check_connection_reports_the_seeded_graph(self, seeded):
        """`isinstance(out.get("graph"), dict)` passes on `{}` (`#13886` r6, F3),
        so the resolved graph must NAME what the fixture provisioned: the
        storage graph is `org_<org_id>` and the namespace is the org the seeder
        opened."""
        door, s, _ = seeded
        status, out = door.call("tortoise_check_connection", {})
        assert status == 200
        assert out.get("connected") is True, out
        graph = out.get("graph")
        assert isinstance(graph, dict), out
        assert graph.get("name") == f"org_{s['org']}", out
        assert graph.get("namespace") == s["org"], out

    def test_check_structure_reports_the_seeded_orphan(self, seeded):
        """`check_structure` runs the product-strategy chain validators, so the
        CORRECT report against a graph holding no chain kinds is `[]` — and
        asserting that empty list proved only that the arm answers A LIST
        (`#13886` r6, F3). The seed carries one live `useCase` with no parent
        JTBD, so the report must NAME that rule and that node. A validator that
        stopped running, or one that flagged the wrong node, reds here."""
        door, s, _ = seeded
        status, out = door.call("tortoise_check_structure", {})
        assert status == 200
        assert isinstance(out, list), out
        by_id = {r.get("id"): r for r in out if isinstance(r, dict)}
        assert s["use_case"] in by_id, (
            "the seeded orphan useCase is not in the chain report: " f"{out}")
        assert by_id[s["use_case"]].get("type") == "orphan_use_case", out
        assert "no parent JTBD" in str(
            by_id[s["use_case"]].get("message") or ""), out

    def test_graph_set_recording_round_trips_BOTH_ways(self, seeded):
        """A one-way check passes on a hardcoded `{"recording": True}` payload;
        flipping it is what makes this a round trip.

        ⛔ `graph_id == graph_id` was the whole second assertion before r6
        (`#13886` F3), and `None == None` satisfies it. This key names no bound
        graph, so the DOCUMENTED fallback is the org DEFAULT graph — the value
        `default` — and that is what the field must carry. The unknown-graph arm
        is driven too, so the id is proven to be RESOLVED rather than echoed
        back unchecked."""
        door, _s, _ = seeded
        _status, on = door.call("tortoise_graph_set_recording",
                                {"recording": True})
        assert on.get("recording") is True, on
        assert on.get("graph_id") == "default", on
        _status, off = door.call("tortoise_graph_set_recording",
                                {"recording": False})
        assert off.get("recording") is False, off
        assert off.get("graph_id") == "default", off
        _status, unknown = door.call("tortoise_graph_set_recording",
                                     {"recording": True,
                                      "graph_id": "capstone-no-such-graph"})
        assert unknown.get("status") == 404, unknown
        assert "graph" in str(unknown.get("error") or "").lower(), unknown

    def test_manage_sources_lists_the_configured_sources(self, seeded,
                                                         sources_state):
        """Asserts the VALUES, not the key set.

        Both GitHub sources go through ONE ``_index_state`` helper, so reading
        the wrong state key keeps every key present and only the values move —
        the failure `tests/test_manage_sources_3540.py` records. The injected
        state is therefore deliberately NOT the product default.
        """
        state, _writes = sources_state
        state["github_indexed"] = True
        state["github_docs_indexed"] = False
        state["docs_enabled"] = False
        door, _s, _ = seeded
        status, out = door.call("tortoise_manage_sources", {"action": "list"})
        assert status == 200
        assert isinstance(out, dict), out
        sources = out.get("sources")
        assert isinstance(sources, dict), out
        assert set(sources) == {"github_issues", "github_documents",
                                "agent_sessions"}, sources
        for row in sources.values():
            assert {"label", "enabled"} <= set(row), row
        # each GitHub source from ITS OWN index key and ITS OWN intent flag
        assert sources["github_issues"]["indexed"] is True, sources
        assert sources["github_documents"]["indexed"] is False, sources
        assert sources["github_issues"]["enabled"] is True, sources
        assert sources["github_documents"]["enabled"] is False, sources
        # the raw projection rides along, so nothing is hidden behind the tool's
        # own interpretation of the state
        assert out.get("onboarding") == state, out

    def test_manage_sources_disable_arm_lands_and_is_read_back(self, seeded,
                                                               sources_state):
        """The `action=` discriminator has more than one arm; `disable` writes an
        intent that `list` must then report — a shape-only check on `list`
        would not see a disabled write that went nowhere.

        The intent flag is pinned by NAME: a write that set `github_connected`
        (or the sibling's flag) would also switch the other source off and pass
        any test that only read `enabled` back.

        MEASURED: `enable` cannot be driven on this lane — it requires a
        connected GitHub account — so it is recorded in ARM_GAPS, not faked."""
        _state, writes = sources_state
        door, _s, _ = seeded
        status, off = door.call("tortoise_manage_sources",
                                {"action": "disable",
                                 "source": "github_documents"})
        assert status == 200
        assert off.get("status") == "disabled", off
        assert off.get("source") == "github_documents", off
        assert off.get("connection_untouched") is True, off
        assert writes == [{"docs_enabled": False}], writes
        status, listed = door.call("tortoise_manage_sources", {"action": "list"})
        assert status == 200
        assert listed["sources"]["github_documents"]["enabled"] is False, listed
        # the SIBLING keeps its own intent (#1924: one connection, per-source
        # flags) — the write above must not have touched it
        assert listed["sources"]["github_issues"]["enabled"] is True, listed

    def test_approve_merge_refuses_a_missing_candidate_by_name(self, seeded):
        """Nothing in the seed is a merge CANDIDATE, so the correct payload for an
        unknown id is the refusal naming it — the `candidate_id` arm is driven.

        The SUCCESS arm is not reachable here: `create_object` deduplicates on
        `canonical_name`, so two identical names return the SAME id and no
        ambiguity pair can be produced (measured). Recorded in ARM_GAPS rather
        than presented as a pass."""
        door, _s, _ = seeded
        status, out = door.call("tortoise_approve_merge",
                                {"candidate_id": "no-such-candidate",
                                 "action": "merge"})
        assert status == 200
        assert isinstance(out, dict) and "error" in out, out
        assert "no-such-candidate" in str(out["error"]), out
