"""#10084 — the `graph` description states the argument is OPTIONAL, with no omit imperative.

The measurement behind this file: on the ChatGPT venue a `tortoise_create_point`
carrying a `graph` argument is refused client-side —

    This tool call was blocked by OpenAI's safety checks. Please double check
    what you are sending.

— while the SAME call with the argument omitted creates the point. Two forms were
blocked, and one of them was `graph="default"`, which our own schema described as
"the same no-op" as omitting: we were telling the model that a redundant routing
argument was an equivalent way to say nothing.

The model builds the call payload from this description, so the description is
the one lever that acts BEFORE the venue sees the call. It now states what
`graph` being OPTIONAL means — omission is legal and, for a workspace
credential, lands on the default graph — and it no longer INSTRUCTS omission as
the form to send: `tortoise_list_graphs()` returns flat names with no
`is_default`, so in a workspace with two or more graphs an "omit it" rule makes
the model guess, and the guess such a rule rewards is the default graph
(#10504 re-brief, `NOT-A-DECISION(10504, …optional-graph form)` — compliance
with #9340's recorded "optional `graph` argument, defaulting to the workspace's
default graph", not a reopen of it). ⛔ This changes what the model is TOLD TO
SEND, never what the surface ACCEPTS: `graph` is still advertised, still
optional, still `string`, and still honoured on every tool that had it (#9340
stands — the argument is what makes the target legible at the call site, and a
caller who needs a second graph still has to send it).

What this file does NOT claim: that the venue will now pass a write that carries
`graph` anyway. The venue's filter is a policy decision on their side.

Two reports recorded on #10084 (comment `2026-11-01T05:58:54Z`) say the SAME
error also hits write calls with **no** routing argument, and the same tool
definitions served from two different domains (`community.openai.com` threads
1384381 — prod `.com` blocked, QA `.net` succeeded — and 1407832). ⛔ Those are
OTHER servers' reports, not this repo's measurement: OUR graph-free control write
SUCCEEDED (point `1a17966af61-e7dfb7eeea90`), so a graph-free write path provably
exists on this venue. That is why this change is a lever on what the model is TOLD
to send and never a demonstrated bypass — if graph-free writes were blocked here
too, the re-brief would buy nothing.
"""
from __future__ import annotations

import asyncio
import re

import pytest

from tortoise import mcp_server
from tortoise.tool_registry import GRAPH_ARGUMENT_DESCRIPTION, GRAPH_ARGUMENT_EXCLUDED


def _advertised() -> dict[str, dict]:
    return {t.name: t.parameters for t in asyncio.run(mcp_server.mcp.list_tools())}


def _props(name: str) -> dict:
    return (_advertised()[name].get("properties") or {})


# ── what the model is told to send ─────────────────────────────────────────


def test_the_description_states_the_parameter_is_optional_not_a_drop_tactic():
    """The 2026-11-02 re-brief: the text states the FACT #9340 decided (`graph` is
    optional, omission lands on the default) and never instructs omission as the
    form to send. The imperative it replaces made a model in a ≥2-graph workspace
    GUESS — `tortoise_list_graphs()` carries no `is_default` — and the guess it
    rewarded landed the write on the default graph.

    ⛔ The HEAD is pinned by its SENTENCE SET and by a HEDGE BLOCKLIST, not by a
    phrase blocklist. A blocklist of historical wordings only catches what we
    thought of; a count of sentences only catches an ADDED sentence — and the most
    natural way to soften a rule is to append a hedge INSIDE an existing one (“…;
    when in doubt, leave it out”), which neither reds. So all three are pinned:
    sentence count, the fact-bearing sentences, and any drop-the-argument hedge."""
    head = GRAPH_ARGUMENT_DESCRIPTION.split("Accepts")[0]
    sentences = [s for s in re.split(r"(?<=\.)\s+", head.strip()) if s]
    assert len(sentences) == 6, sentences
    assert sentences[0] == "Optional graph to act on: this parameter MAY be omitted.", sentences[0]
    assert "that is what omission MEANS, not a form to prefer" in sentences[1], sentences[1]
    assert "never permission to drop the argument" in sentences[2], sentences[2]
    for hedge in ("leave it out", "when in doubt", "drop it", "don't send",
                  "do not send", "omit it", "should omit", "must omit"):
        assert hedge not in head.lower(), (hedge, head)
    assert "OMIT IT" not in head, head
    assert "that is the form to send" not in head, head


def test_the_rationale_lives_once_in_the_server_instructions_not_on_every_tool():
    """WHY `graph` is optional belongs in the server `instructions` field (sent
    once per session), not in the parameter description, which is COPIED onto
    every advertising tool (#10084 review P2: the description had grown 631 →
    1671 chars, ~+73 KB of tool payload per client turn on the one venue where
    the payload is what gets refused).

    The fact is not dropped, only de-duplicated — so the model still learns the
    reason, and a future editor cannot quietly re-inline it."""
    rationale = (
        "It is optional because a graph is working context, not an identity, so "
        "the target is stated per call rather than bound into the connection (#9340).")
    assert rationale in mcp_server._MCP_INSTRUCTIONS, mcp_server._MCP_INSTRUCTIONS
    assert "working context, not an identity" not in GRAPH_ARGUMENT_DESCRIPTION, (
        "the rationale is back in the per-tool string — it is paid once per "
        "advertising tool in every client payload")


def test_the_description_stays_within_its_measured_budget():
    """The parameter description is served ONCE PER ADVERTISING TOOL — measured
    at >60 tools — so its length is a payload cost, not a style preference, and
    it is paid on the venue #10084 is about (a client-side refusal of the JSON
    tool schema / call payload).

    The budget is the measured 1484-character text plus headroom for one more
    operative rule; anything larger needs a deliberate edit here, with its
    delta stated, rather than one more explanatory paragraph."""
    assert len(GRAPH_ARGUMENT_DESCRIPTION) <= 1500, (
        f"{len(GRAPH_ARGUMENT_DESCRIPTION)} chars exceeds the 1500 budget — keep "
        "operative rules here and put rationale in `_MCP_INSTRUCTIONS`")


def test_the_description_sends_a_graph_that_is_not_the_default():
    """The rule is TARGET-based, not ask-based: a target established by a system
    prompt or an earlier turn still has to be sent."""
    head = GRAPH_ARGUMENT_DESCRIPTION.split("Accepts")[0]
    assert "NOT this workspace's default, SEND it" in head, head


def test_the_description_forbids_dropping_the_argument_to_get_a_write_through():
    """The anti-bypass clause. Without it the text TEACHES that a host refuses a
    routing argument, and the obvious retry after a block — drop `graph` — lands
    the write on the DEFAULT graph: #9340's silent-wrong-graph failure class."""
    head = GRAPH_ARGUMENT_DESCRIPTION.split("Accepts")[0]
    assert "Never drop `graph`" in head, head
    assert "including when a host has already refused an earlier call" in head, head


def test_the_description_does_not_claim_a_non_redundant_argument_is_safe():
    """The measured block was value-independent (a NON-EXISTENT graph id was
    refused the same way as the no-op), so a qualifier like "redundant" would
    read as permission for a routing argument the host refuses anyway."""
    head = GRAPH_ARGUMENT_DESCRIPTION.split("Accepts")[0]
    assert "redundant routing argument" not in head, head
    assert "whatever its value" in head, head


def test_the_host_observation_names_its_object_and_is_not_a_blanket_rule():
    """`carries a routing argument at all` was a BLANKET rule whose nearest-noun
    reading is "never send a routing argument" — the exact drop-the-argument
    bypass the anti-bypass sentence forbids. The claim is an observation about a
    WRITE carrying `graph`, so it must name the object and stay value-independent
    (#10084 review P2; the issue itself records the trigger as NOT established)."""
    head = GRAPH_ARGUMENT_DESCRIPTION.split("Accepts")[0]
    assert "a host may refuse a write call that carries `graph` whatever its value" in head, head
    assert "carries a routing argument at all" not in head, head
    # and it is an OBSERVATION, never an instruction to drop the argument
    assert "never permission to drop the argument" in head, head
    assert "do not send the redundant" not in head, head


def test_the_anti_bypass_clause_has_a_stop_condition():
    """The resend instruction is right (the venue's filter is intermittent), an
    UNBOUNDED one is not: without a terminal action a model facing a host that
    keeps refusing has no exit except the one the clause forbids — dropping the
    argument, which writes to the default graph instead (#10084 review P3)."""
    head = GRAPH_ARGUMENT_DESCRIPTION.split("Accepts")[0]
    assert "If the refusal repeats with the graph you were asked for" in head, head
    assert "never retry with `graph` dropped" in head, head


def test_the_omit_rule_is_qualified_for_a_graph_bound_credential():
    """OMISSION is "the default" only for an UNBOUND credential: on the
    `plan_graph_target(None) -> None` path `mcp_auth._get_org_sdk` opens a
    graph-bound key's OWN graph (`gid and ns`), so an unqualified "omission
    targets the default" tells a bound key's caller to omit its way into the
    WRONG graph — and the qualification is what the sibling `graph="default"`
    sentence already carries (#10084 review P3)."""
    head = GRAPH_ARGUMENT_DESCRIPTION.split("Accepts")[0]
    assert "for a workspace credential, omission targets the default" in head, head
    assert "graph-bound credential's omission stays on its own graph" in head, head


def test_the_no_op_equivalence_is_qualified_for_a_graph_bound_credential():
    """Omission and `graph="default"` are NOT the same call for a graph-bound
    credential: `mcp_auth.plan_graph_target` refuses it rather than re-pointing
    (pinned by `test_9340_graph_tool_argument.py::
    test_a_bound_credential_cannot_ask_for_the_default_graph`)."""
    head = GRAPH_ARGUMENT_DESCRIPTION.split("Accepts")[0]
    no_op_sentence = head[head.index('graph="default"') :]
    assert "graph-bound credential is refused rather than" in no_op_sentence, no_op_sentence
    assert "do not send the redundant" not in no_op_sentence, no_op_sentence


def test_the_description_still_carries_the_forms_that_are_accepted():
    """Optional is not omit-first: the accepted forms and the transport limits
    #9340 pins must survive the re-wording."""
    assert "org_<workspace>_<graph_id>" in GRAPH_ARGUMENT_DESCRIPTION
    assert "HOSTED" in GRAPH_ARGUMENT_DESCRIPTION
    assert "namespace" in GRAPH_ARGUMENT_DESCRIPTION


# ── what the surface ACCEPTS is unchanged (no surface change) ──────────────


def test_the_blocked_write_tool_still_advertises_an_optional_string_graph():
    props = _props("tortoise_create_point")
    assert "graph" in props, "removing it would be a surface change and would strand #9340"
    assert props["graph"] == {"type": "string", "description": GRAPH_ARGUMENT_DESCRIPTION}
    assert "graph" not in _advertised()["tortoise_create_point"].get("required", [])


def test_every_advertising_tool_keeps_graph_optional_and_a_string():
    """The explicit-graph case is the one this fix must not break. A single tool
    that started REQUIRING the argument, or changed its type, would be a change
    to what the surface accepts."""
    advertised = _advertised()
    advertising = sorted(set(advertised) - GRAPH_ARGUMENT_EXCLUDED)
    # This is a FLOOR, not a count: it exists so the loop below cannot pass by
    # advertising `graph` on a handful of tools. It moved 60 → 55 with #11609 batch 1,
    # which ARCHIVED four no-destination tools (`tortoise_analyze`,
    # `tortoise_backfill_v25`, `tortoise_packs_list`, `tortoise_pack_install`) — the
    # injection is still wholesale (60 tools at this head); what changed is the surface
    # the floor is measured against.
    assert len(advertising) > 55, (
        f"the argument stopped being injected wholesale — only {len(advertising)} "
        "tool(s) advertise `graph`")
    for name in advertising:
        schema = advertised[name]
        props = schema.get("properties") or {}
        assert "graph" in props, (
            f"{name} stopped advertising `graph` — a caller that needs a second "
            "graph could no longer name it at the call site (#9340)")
        assert props["graph"]["type"] == "string", name
        assert props["graph"]["description"] == GRAPH_ARGUMENT_DESCRIPTION, name
        assert "graph" not in (schema.get("required") or []), (
            f"{name} started requiring `graph` — that is a surface change")


def test_the_excluded_tools_still_do_not_advertise_it():
    advertised = _advertised()
    for name in sorted(GRAPH_ARGUMENT_EXCLUDED & set(advertised)):
        assert "graph" not in (_props(name)), (
            f"{name} names its graph explicitly and must not advertise `graph`")


@pytest.mark.parametrize("value", ["default", "org_ws_gid", "a-unique-name"])
def test_an_explicit_graph_survives_the_pop_and_is_not_swallowed(value):
    """`_take_graph_argument` POPS the key (and refuses it on a
    `GRAPH_ARGUMENT_EXCLUDED` tool) — it neither resolves nor authorises: that is
    `_resolve_graph_argument` → `mcp_auth.plan_graph_target`, pinned in
    `tests/test_9340_graph_tool_argument.py`. What this pins is narrower and real:
    the re-briefed description changes what the model is TOLD to send, so the
    pop must still hand the advertised value on instead of swallowing it."""
    arguments = {"kind": "Observation", "content": "x", "graph": value}
    taken = mcp_server._take_graph_argument("tortoise_create_point", arguments)
    assert taken == value, taken
    assert "graph" not in arguments, arguments
