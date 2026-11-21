"""#11609 lane P7 — the four unregistered targets, and the five doors they retire.

WHAT THIS FILE MEASURES, AND WHY IT IS NOT A RESTATEMENT OF THE REGISTRY
-----------------------------------------------------------------------
`tests/test_11609_door_retirement.py` holds the `PROTECTED` contract and the population
pins; `tests/test_11609_structural_blockers.py` holds the `NO_CONTAINER_YET` destinations.
Neither can answer the questions THIS batch created, because both are written about doors
that were held, and this batch's subject is the four APPROVED TARGET NAMES that had no
registry entry at all:

  `get_historical_knowledge`, `link_entities`, `poll_events`, `delete_knowledge`
  (issue #13391 — the `40 − 17 + 4 = 27` arithmetic's four `+1`s)

A `ToolDefinition` alone is not a tool. `mcp_server.py` registers a name by
`globals()[t.name]` (#2210), so an entry with no module-level function is ADVERTISED and
NOT SERVED — it appears in `tools/list` and every `tools/call` fails. So each new name is
measured three ways, and no two of them imply the third:

  1. **it is advertised** — in `TOOL_REGISTRY`, and the handler the adapter builds exists;
  2. **it is served** — `mcp.get_tool(name)` resolves it AND the resolved tool has a
     `fn`, then CALLING that `fn` reaches the SDK method the tool exists for
     (substituted, so an empty call log means the wire never reached the capability);
  3. **it answers the absorbed door's call** — for the three that absorb doors, the same
     arguments the retired door took must land on the same writer.

The five retirements are the other half: a door may only leave the advertised surface in
the batch that makes its stated reason false, and the reason here was literally
"`<destination>` not live" — so the destination being registered IS the proof, and each
pointer must resolve in ONE hop (`TestEveryPointerResolvesInOneHop` covers the whole
map; this file pins that the five names are in it at all).
"""
from __future__ import annotations

import asyncio

import pytest

from tools.bridge_table import TARGET_MCP
from tortoise import mcp_server as ms
from tortoise.sdk import TortoiseSDK
from tortoise.tool_registry import (
    RETIRED_TOOL_REGISTRY,
    RETIRED_USE_INSTEAD,
    TOOL_REGISTRY,
)

RETIRED_BY_NAME = {t.name: t for t in RETIRED_TOOL_REGISTRY}

#: The four names this batch registered, with the SDK method each must reach.
NEW_TARGETS: dict[str, str] = {
    "tortoise_link_entities": "link_entities",
    "tortoise_poll_events": "poll_events",
    "tortoise_delete_knowledge": "_delete",
    "tortoise_get_historical_knowledge": "get_historical_knowledge",
}

#: The five doors this batch retired FORWARD, and the live tool each now names.
RETIRED_BY_P7: dict[str, str] = {
    "tortoise_create_edge": "tortoise_link_entities",
    "tortoise_create_operator": "tortoise_link_entities",
    "tortoise_events_poll": "tortoise_poll_events",
    "tortoise_delete": "tortoise_delete_knowledge",
    "tortoise_list_graphs": "tortoise_graph_overview",
}


@pytest.fixture
def served(monkeypatch):
    """Install a recording SDK and the transport mode `_safe`'s auth gate needs.

    `_safe` refuses when the transport mode is unset (fail-closed, #236), and these
    cases measure DISPATCH, not auth — the posture every sibling container suite takes.
    Returns `install(method, payload) -> (sdk, calls)`.
    """
    token = ms._transport_mode.set("stdio")
    monkeypatch.setattr(ms, "_is_dev_mode", lambda: True)
    monkeypatch.setattr(ms, "_get_org_sdk", lambda: object())
    try:
        def install(method: str, payload=None):
            calls: list[tuple] = []

            def _fn(_self, *args, **kwargs):
                calls.append((args, kwargs))
                return payload

            monkeypatch.setattr(TortoiseSDK, method, _fn, raising=True)
            sdk = TortoiseSDK.__new__(TortoiseSDK)
            monkeypatch.setattr(ms, "_get_org_sdk", lambda: sdk)
            return sdk, calls
        yield install
    finally:
        ms._transport_mode.reset(token)


class TestTheFourTargetsAreRegistered:
    def test_the_target_set_is_now_complete(self):
        """All 27 approved names resolve as doors — the arithmetic's own claim.

        Derived from `TARGET_MCP`, so a target the docs drop cannot silently stop
        being checked: the list IS the approval (`docs/product/canonical-mcp-tools.md`,
        pr 4120) as the bridge table carries it.
        """
        live = {t.name for t in TOOL_REGISTRY}
        missing = [n for n in TARGET_MCP if f"tortoise_{n}" not in live]
        assert missing == [], (
            f"approved target(s) {missing} are not live MCP doors — the 27/40 ruling "
            "names them, so a missing one is the gap #13391 reports")

    @pytest.mark.parametrize("name", sorted(NEW_TARGETS))
    def test_it_is_advertised_and_has_a_module_level_handler(self, name):
        """#2210: an entry without a module-level function is advertised-not-served."""
        assert name in {t.name for t in TOOL_REGISTRY}, f"{name} is not in TOOL_REGISTRY"
        assert name not in RETIRED_USE_INSTEAD, f"{name} is a LIVE target, not a retired one"
        assert hasattr(ms, name), (
            f"{name} has no module-level handler in `mcp_server.py`, so the adapter's "
            "`globals()[t.name]` cannot serve it — it would be ADVERTISED and not served "
            "(#2210)")

    @pytest.mark.parametrize("name", sorted(NEW_TARGETS))
    def test_the_wire_resolves_it_to_a_callable_tool(self, name):
        tool = asyncio.run(ms.mcp.get_tool(name))
        assert tool is not None, f"{name} does not resolve through `mcp.get_tool`"
        assert callable(tool.fn), f"{name} resolved to a tool with no handler"

    @pytest.mark.parametrize("name,entry", sorted(NEW_TARGETS.items()))
    def test_its_declared_sdk_method_exists(self, name, entry):
        """The registry's own binding, resolved — the #3838 drift class, one level up."""
        declared = {t.name: t for t in TOOL_REGISTRY}[name].sdk_method
        assert declared, f"{name} declares no sdk_method"
        assert hasattr(TortoiseSDK, declared), (
            f"{name} declares sdk_method={declared!r}, which does not exist on "
            "TortoiseSDK — the manifest's binding column would be a false declaration")


class TestLinkEntitiesAnswersTheTwoDoorsItAbsorbs:
    def test_single_target_reaches_the_sdk_dispatch(self, served):
        """`tortoise_create_edge`'s call lands on `link_entities`, unchanged."""
        _sdk, calls = served("link_entities", {"edge": 1})
        assert ms.tortoise_link_entities("performs", "a", "b") == {"edge": 1}
        assert len(calls) == 1, calls
        args, kwargs = calls[0]
        assert args == ("performs", "a", "b"), args
        # The door's own default was "no operator, decide from the relation"; every
        # other keyword must arrive as "not asked" (None/True), never as a value the
        # caller did not send — the fail-silent class the SDK's own docstring names.
        assert kwargs == {
            "operator": None, "direction": None, "confidence": None,
            "weight": None, "label": None, "batch_id": None,
            "entity_label": None, "source_kind": None, "promote_source": True,
        }, kwargs

    def test_the_operator_door_s_spelling_of_the_target_is_accepted(self, served):
        """`target_ids` — the retired door's own argument name — is accepted, and it
        selects the OPERATOR arm (see the dedicated row below): the spelling carries the
        door's semantics, so neither a JSON-string list nor a bare id is a TypeError."""
        _sdk, calls = served("_create_operator", {"operator": 1})
        ms.tortoise_link_entities("supports", "a", target_ids='["b"]')
        assert len(calls) == 1, calls
        assert calls[0][0] == ("supports", "a", ["b"]), calls[0]

    def test_the_plural_spelling_selects_the_operator_arm_even_for_ONE_target(self, served):
        """`target_ids=[x]` is the retired operator door's call — an OPERATOR, not a
        plain direct edge.

        ⛔ MEASURED FAILURE this pins (capstone V2, P7): with only the multi-target
        count selecting the operator arm, `link_entities("NAND", a, [b])` fell through to
        the relation-decided arm, where IMPL/NAND without `operator=True` writes the
        operator-LESS direct edge — a DIFFERENT write for the same arguments, with no
        error (the #8447 shape). The spelling selects the writer.
        """
        _sdk, calls = served("_create_operator", {"operator": 1})
        ms.tortoise_link_entities("NAND", "a", target_ids=["b"])
        assert len(calls) == 1, calls
        args, kwargs = calls[0]
        assert args == ("NAND", "a", ["b"]), args
        assert kwargs["semantics"] is None, kwargs

    @pytest.mark.parametrize("targets", [["b"], ["b", "c"]], ids=["one", "two"])
    def test_the_list_SHAPE_selects_the_operator_arm_not_the_COUNT(self, served, targets):
        """The POSITIONAL list spelling, at ONE target and at TWO — the same writer.

        ⛔ MEASURED FAILURE this pins, and it is the half the FIRST fix left open: with
        `len(targets) > 1` as the operator arm's count term, `link_entities("NAND", a, [b])`
        took the relation-decided arm (→ the operator-LESS direct edge) while
        `link_entities("NAND", a, [b, c])` took the operator arm — the SAME expression
        shape writing two different things by count ALONE. That is precisely what the
        retired door's own positional signature hands a mechanically-renamed caller
        (`tortoise_create_operator(op_type, source_id, target_ids, ...)`), and the
        registered description advertises the list form on `to_id`.

        Parametrised over one and two targets ON PURPOSE: a count-based rule reds the
        `one` row and greens the `two` row, so a fix that only widens the count term
        (`> 0`) cannot pass this test while still deciding by arity somewhere else.
        """
        _sdk, calls = served("_create_operator", {"operator": 1})
        ms.tortoise_link_entities("NAND", "a", targets)
        assert len(calls) == 1, (
            f"the positional list {targets!r} produced {len(calls)} operator writes: "
            f"{calls} — a LIST of targets is the operator arm at ANY count, and this one "
            "fell through to the relation-decided arm")
        assert calls[0][0] == ("NAND", "a", targets), calls[0]

    def test_a_json_string_list_in_to_id_is_the_same_shape(self, served):
        """A raw transport sends the list as JSON text; `_parse` yields the LIST, so the
        SHAPE (not the transport encoding) is what selects the arm."""
        _sdk, calls = served("_create_operator", {"operator": 1})
        ms.tortoise_link_entities("NAND", "a", '["b"]')
        assert len(calls) == 1 and calls[0][0] == ("NAND", "a", ["b"]), calls

    def test_a_SCALAR_to_id_still_follows_the_relation(self, served):
        """The other half of the contract, pinned so the shape rule cannot over-reach:
        a scalar target keeps the relation-decided arm (`link_entities`' own contract),
        including for IMPL/NAND — where the caller asked for the bare edge."""
        _sdk, calls = served("link_entities", {"edge": 1})
        ms.tortoise_link_entities("NAND", "a", "b")
        assert len(calls) == 1, calls
        assert calls[0][0] == ("NAND", "a", "b"), calls[0]
        assert calls[0][1]["operator"] is None, calls[0]

    def test_an_explicit_operator_true_selects_the_same_arm(self, served):
        _sdk, calls = served("_create_operator", {"operator": 1})
        ms.tortoise_link_entities("supports", "a", "b", operator=True)
        assert len(calls) == 1 and calls[0][0] == ("supports", "a", ["b"]), calls

    def test_a_multi_target_call_is_ONE_operator_over_N_targets(self, served):
        """The part/whole fan-out `tortoise_create_operator` had, preserved exactly.

        A per-target loop would create N separate operators — a different graph, not a
        smaller one. So the assertion is on the writer: ONE call, carrying the LIST.
        """
        _sdk, calls = served("_create_operator", {"operator": 1})
        ms.tortoise_link_entities(
            "hasPart", "parent", ["c1", "c2"], semantics="hasPart")
        assert len(calls) == 1, (
            f"a 2-target call produced {len(calls)} operator writes: {calls} — the "
            "fan-out must be ONE operator over N targets, as the retired door wrote it")
        args, kwargs = calls[0]
        assert args == ("hasPart", "parent", ["c1", "c2"]), args
        assert kwargs["semantics"] == "hasPart", kwargs

    def test_semantics_on_a_single_target_is_refused_not_dropped(self, served):
        """`link_entities` has no `semantics` slot on the single-target arm.

        Dropping it would return success for a write that stored a subset of the call —
        the fail-silent class this surface exists to refuse.
        """
        _sdk, calls = served("link_entities", {"edge": 1})
        out = ms.tortoise_link_entities("performs", "a", "b", semantics="addresses")
        assert isinstance(out, dict) and "error" in out, out
        assert "semantics" in out["error"], out
        assert calls == [], "a refused call must not reach the writer"

    def test_a_multi_target_call_refuses_what_that_arm_cannot_record(self, served):
        _sdk, calls = served("_create_operator", {"operator": 1})
        out = ms.tortoise_link_entities("hasPart", "a", ["b", "c"], confidence=0.9)
        assert isinstance(out, dict) and "error" in out, out
        assert "confidence" in out["error"], out
        assert calls == [], calls


class TestPollEventsAnswersTheDoorItAbsorbs:
    def test_the_new_name_reaches_poll_events(self, served):
        _sdk, calls = served("poll_events", {"events": [], "next_cursor": "c"})
        assert ms.tortoise_poll_events(after="a", types=["PointAdded"], limit=5) == {
            "events": [], "next_cursor": "c"}
        assert len(calls) == 1, calls
        assert calls[0][1] == {"after": "a", "types": ["PointAdded"], "limit": 5}, calls[0]

    def test_a_json_string_type_list_is_parsed_as_the_door_did(self, served):
        _sdk, calls = served("poll_events", {"events": [], "next_cursor": "c"})
        ms.tortoise_poll_events(types='["PointAdded", "PointRevised"]')
        assert calls[0][1]["types"] == ["PointAdded", "PointRevised"], calls[0]

    def test_the_retired_name_answers_through_the_same_body(self, served):
        _sdk, calls = served("poll_events", {"events": [], "next_cursor": "c"})
        assert ms.tortoise_events_poll(limit=7) == {"events": [], "next_cursor": "c"}
        assert len(calls) == 1 and calls[0][1]["limit"] == 7, calls


class TestDeleteKnowledgeKeepsThePreviewProtocol:
    """The kept entry moved onto the target's spelling — the PROTOCOL must not move.

    #9660's protocol is the whole reason this is one entry: the first call changes
    nothing and returns a token. A rename that quietly dropped it would turn an
    accidental call into a destruction, so it is measured on the NEW name.
    """

    def _stub_preview(self, monkeypatch, served):
        sdk, calls = served("_delete", True)

        class _Proj:
            @staticmethod
            def _resolve_entity(*_a, **_k):
                return []

        monkeypatch.setattr(sdk, "_get_proj", lambda: _Proj(), raising=False)
        return sdk, calls

    def test_the_first_call_deletes_nothing_and_returns_a_token(self, monkeypatch, served):
        _sdk, calls = self._stub_preview(monkeypatch, served)
        receipt = ms.tortoise_delete_knowledge("nope")
        assert calls == [], (
            "the preview reached the writer — a token-less call must change NOTHING")
        assert receipt["dry_run"] is True, receipt
        assert receipt["confirmation"], receipt
        assert receipt["found"] is False, receipt

    def test_the_confirmation_call_deletes_exactly_once(self, monkeypatch, served):
        _sdk, calls = self._stub_preview(monkeypatch, served)
        token = ms._deletion.issue("id-x")
        monkeypatch.setattr(ms._deletion, "verify", lambda *_a, **_k: {"t": "id-x"})
        assert ms.tortoise_delete_knowledge("id-x", confirmation=token) == {
            "deleted": True, "id": "id-x"}
        assert len(calls) == 1, calls

    def test_the_retired_name_forwards_the_same_protocol(self, monkeypatch, served):
        _sdk, calls = self._stub_preview(monkeypatch, served)
        receipt = ms.tortoise_delete("nope")
        assert calls == [], "`tortoise_delete` must forward the preview, not delete"
        assert receipt["dry_run"] is True and receipt["confirmation"], receipt


class TestHistoricalKnowledgeAnswers:
    def test_it_reaches_the_sdk_method_with_the_door_s_arguments(self, served):
        payload = {"status": "absent", "found": False}
        _sdk, calls = served("get_historical_knowledge", payload)
        assert ms.tortoise_get_historical_knowledge("p1", "2024-01-01") == payload
        assert calls == [(("p1", "2024-01-01"), {})], calls

    def test_the_three_state_contract_survives_the_wire(self, served):
        """`ambiguous` carries found=True and NO valid_point — the tool must not reshape it.

        The failure mode this guards is a wrapper that "helpfully" picks a candidate:
        two candidates reported as one is how a caller reads a lie as an answer.
        """
        payload = {"status": "ambiguous", "found": True, "ambiguous": True,
                   "candidates": [{"id": "a"}, {"id": "b"}]}
        _sdk, _calls = served("get_historical_knowledge", payload)
        out = ms.tortoise_get_historical_knowledge("p1", "2024-01-01")
        assert out == payload and "valid_point" not in out, out


class TestTheFiveDoorsRetiredForward:
    @pytest.mark.parametrize("name", sorted(RETIRED_BY_P7))
    def test_the_name_left_the_advertised_surface_and_is_still_served(self, name):
        assert name not in {t.name for t in TOOL_REGISTRY}, (
            f"{name} is still advertised, so it did not retire")
        assert name in RETIRED_USE_INSTEAD, (
            f"{name} left the advertised surface with no forward pointer — a caller "
            "would get 'unknown tool' instead of a warning naming the destination")
        assert name in RETIRED_BY_NAME, (
            f"{name} has no declaration in `_ENTRY_DECLARATIONS`, so "
            "`RETIRED_TOOL_REGISTRY` cannot build a shim for it and the retired name "
            "does NOT resolve (#3883)")

        tool = asyncio.run(ms.mcp.get_tool(name))
        marker = ((tool.meta or {}).get("tortoise") or {}).get("retired")
        assert marker and marker["retired"] is True, (
            f"{name} resolved without its retirement marker")
        assert marker["use_instead"] == RETIRED_USE_INSTEAD[name]

    @pytest.mark.parametrize("name,target", sorted(RETIRED_BY_P7.items()))
    def test_the_pointer_names_a_live_tool_in_one_hop(self, name, target):
        live = {t.name for t in TOOL_REGISTRY}
        pointer = RETIRED_USE_INSTEAD[name]
        assert pointer.split("(", 1)[0].strip() == target, (
            f"{name} is pointed at {pointer!r}, not at {target!r} — aliases are not "
            "transitive, so a pointer at a retired or missing name dead-ends")
        assert target in live, f"{name}'s replacement {target} is not a live tool"

    def test_the_delete_pointer_keeps_the_preview_then_confirm_shape(self):
        """A migrating caller who drops the protocol deletes on the first call."""
        pointer = RETIRED_USE_INSTEAD["tortoise_delete"]
        assert "dry_run=True" in pointer and "confirmation=" in pointer, pointer

    def test_the_pointer_is_callable_at_the_container(self):
        """Every `name=`/`key=` a pointer advertises must be a real parameter.

        Not covered by the door-retirement suite (it binds only its own thirteen), and
        these pointers are the ones a caller follows after this batch.
        """
        import inspect
        import re

        for name in sorted(RETIRED_BY_P7):
            pointer = RETIRED_USE_INSTEAD[name]
            target = pointer.split("(", 1)[0].strip()
            fn = getattr(ms, target)
            sig = inspect.signature(fn)
            accepts_kwargs = any(
                p.kind is inspect.Parameter.VAR_KEYWORD
                for p in sig.parameters.values())
            named = re.findall(r"(\w+)=", pointer)
            rejected = [
                kw for kw in named
                if kw not in sig.parameters and not accepts_kwargs]
            assert not rejected, (
                f"{name} is pointed at {pointer!r}, which passes {rejected} — "
                f"{target}{sig} does not take them, so the migration instruction is a "
                "TypeError for the caller who follows it")
