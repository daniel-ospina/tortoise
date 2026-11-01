"""#10084 — the `graph` DESCRIPTION sends the omitted form first.

The measurement behind this file: on the ChatGPT venue a `tortoise_create_point`
carrying a `graph` argument is refused client-side —

    This tool call was blocked by OpenAI's safety checks. Please double check
    what you are sending.

— while the SAME call with the argument omitted creates the point. Two forms were
blocked, and one of them was `graph="default"`, which our own schema described as
"the same no-op" as omitting: we were telling the model that a redundant routing
argument was an equivalent way to say nothing.

The model builds the call payload from this description, so the description is
the one lever that acts BEFORE the venue sees the call. It now states
omit-first. ⛔ This changes what the model is TOLD TO SEND, never what the
surface ACCEPTS: `graph` is still advertised, still optional, still `string`, and
still honoured on every tool that had it (#9340 stands — the argument is what
makes the target legible at the call site, and a caller who needs a second graph
still has to send it).

What this file does NOT claim: that the venue will now pass a write that carries
`graph` anyway. The venue's filter is a policy decision on their side, and the
same error is reported for write calls with no routing argument at all and for
the same tool definitions served from two different domains. See #10084.
"""
from __future__ import annotations

import asyncio

import pytest

from tortoise import mcp_server
from tortoise.tool_registry import GRAPH_ARGUMENT_DESCRIPTION, GRAPH_ARGUMENT_EXCLUDED


def _advertised() -> dict[str, dict]:
    return {t.name: t.parameters for t in asyncio.run(mcp_server.mcp.list_tools())}


def _props(name: str) -> dict:
    return (_advertised()[name].get("properties") or {})


# ── what the model is told to send ─────────────────────────────────────────


def test_the_description_states_omit_first():
    """The instruction must come before the accepted forms, in the words a model
    reads as a rule, not as an option."""
    head = GRAPH_ARGUMENT_DESCRIPTION.split("Accepts")[0]
    assert "OMIT IT" in head, head
    assert "that is the form to send" in head, head


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
    assert "carries a routing argument at all" in head, head


def test_the_no_op_equivalence_is_qualified_for_a_graph_bound_credential():
    """Omission and `graph="default"` are NOT the same call for a graph-bound
    credential: `mcp_auth.plan_graph_target` refuses it rather than re-pointing
    (pinned by `test_9340_graph_tool_argument.py::
    test_a_bound_credential_cannot_ask_for_the_default_graph`)."""
    head = GRAPH_ARGUMENT_DESCRIPTION.split("Accepts")[0]
    no_op_sentence = head[head.index('graph="default"') :]
    assert "graph-bound credential is refused rather than" in no_op_sentence, no_op_sentence
    assert "do not send it" in no_op_sentence, no_op_sentence


def test_the_description_still_carries_the_forms_that_are_accepted():
    """Omit-first is not the same as omit-only: the accepted forms and the
    transport limits #9340 pins must survive the re-wording."""
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
    assert len(advertising) > 60, "the argument stopped being injected wholesale"
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
def test_an_explicit_graph_is_still_dispatched_value_independently(value):
    """The description forbids the redundant NO-OP form; it must not make the
    dispatch refuse a value. `_take_graph_argument` is the single dispatch seam
    both the advertised and the honoured set go through."""
    arguments = {"kind": "Observation", "content": "x", "graph": value}
    taken = mcp_server._take_graph_argument("tortoise_create_point", arguments)
    assert taken == value, taken
    assert "graph" not in arguments, arguments
