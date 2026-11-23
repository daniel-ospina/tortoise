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
import re

import pytest

from tools.bridge_table import TARGET_MCP
from tortoise import mcp_server as ms
from tortoise.pack_registry import SEMANTICS_MECHANISMS
from tortoise.sdk import TortoiseSDK
from tortoise.tool_registry import (
    RETIRED_TOOL_REGISTRY,
    RETIRED_USE_INSTEAD,
    TOOL_REGISTRY,
)

RETIRED_BY_NAME = {t.name: t for t in RETIRED_TOOL_REGISTRY}

#: The writer's own, UNSUBSTITUTED entry point, captured at import: the `served` fixture
#: replaces `TortoiseSDK._create_operator` with a recorder, so a case that wants to run the
#: REAL `op_type` gate has to hold the real function from before the substitution.
REAL_CREATE_OPERATOR = TortoiseSDK._create_operator

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
        door's semantics, so neither a JSON-string list nor a bare id is a TypeError.

        ⛔ `supports` is a MEANING, not a mechanism (#11609 P7 review, P1): the writer's
        first argument is `op_type`, whose allow-list is `IMPL`/`NAND`/`coherence` +
        the part/whole types, so the meaning goes in the mechanism slot of
        `SEMANTICS_MECHANISMS` and the meaning itself rides in `semantics=`. This row
        used to assert `("supports", …)` — it PINNED the defect, because a write through
        the real `_create_operator` answers that with `ValueError` on every call.
        """
        _sdk, calls = served("_create_operator", {"operator": 1})
        ms.tortoise_link_entities("supports", "a", target_ids='["b"]')
        assert len(calls) == 1, calls
        assert calls[0][0] == ("IMPL", "a", ["b"]), calls[0]
        assert calls[0][1]["semantics"] == "supports", calls[0]

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
        assert len(calls) == 1 and calls[0][0] == ("IMPL", "a", ["b"]), calls
        assert calls[0][1]["semantics"] == "supports", calls

    def test_a_multi_target_call_is_ONE_operator_over_N_targets(self, served):
        """The part/whole fan-out `tortoise_create_operator` had, preserved exactly.

        A per-target loop would create N separate operators — a different graph, not a
        smaller one. So the assertion is on the writer: ONE call, carrying the LIST.

        ⛔ `hasPart` is a MEANING (`SEMANTICS_MECHANISMS` -> `IMPL`), so the mechanism
        travels as `op_type` and the meaning as `semantics=` (#11609 P7 review, P1).
        """
        _sdk, calls = served("_create_operator", {"operator": 1})
        ms.tortoise_link_entities(
            "hasPart", "parent", ["c1", "c2"], semantics="hasPart")
        assert len(calls) == 1, (
            f"a 2-target call produced {len(calls)} operator writes: {calls} — the "
            "fan-out must be ONE operator over N targets, as the retired door wrote it")
        args, kwargs = calls[0]
        assert args == ("IMPL", "parent", ["c1", "c2"]), args
        assert kwargs["semantics"] == "hasPart", kwargs

    @pytest.mark.parametrize("relation,mechanism", [
        ("supports", "IMPL"),
        ("mutuallySupportive", "IMPL"),
        ("refutes", "NAND"),
        ("mutuallyExclusive", "NAND"),
        ("hasPart", "IMPL"),
        ("dependsOn", "IMPL"),
        ("addresses", "IMPL"),
        ("causes", "IMPL"),
        ("produces", "IMPL"),
    ])
    def test_a_MEANING_reaches_the_writer_as_its_MECHANISM(self, served, relation,
                                                          mechanism):
        """Every meaning this arm advertises resolves to an `op_type` the writer accepts.

        ⛔ DISCRIMINATING BY CONSTRUCTION: the mutant is `relation` in the `op_type` slot
        (the shipped defect, #11609 P7 review P1) and it reds EVERY row here, because the
        expected value is the MECHANISM and the buggy write sends the MEANING. It would
        also raise `ValueError` from the real `_create_operator`, whose allow-list is
        `IMPL`/`NAND`/`coherence` + the part/whole types — which is why the defect was
        invisible: the capstone only drove allow-listed NAND/IMPL relations.
        """
        _sdk, calls = served("_create_operator", {"operator": 1})
        ms.tortoise_link_entities(relation, "a", ["b"])
        assert len(calls) == 1, calls
        args, kwargs = calls[0]
        assert args == (mechanism, "a", ["b"]), (
            f"relation {relation!r} reached the writer as op_type {args[0]!r}; the "
            f"meaning belongs in `semantics` and the MECHANISM ({mechanism!r}) in "
            "op_type — `_create_operator` refuses a meaning there on every call")
        assert kwargs["semantics"] == relation, kwargs

    def test_every_dispatched_op_type_passes_the_writer_s_OWN_gate(self, served):
        """The writer's real gate, RUN — because every case above substitutes it.

        ⛔ The review named this as the hole in this file's own coverage: "every case
        substitutes `TortoiseSDK._create_operator`, so it proves *dispatch shape* and
        cannot see that the dispatched arguments are invalid for the real writer" — which
        is exactly how the meaning-in-`op_type` defect shipped green. This runs the
        writer's FIRST statement (the `op_type` allow-list, which fires before any graph
        access), so the pre-fix value is proven refused and every value this door
        actually dispatches is proven accepted — with no database involved.

        Derived from `pack_registry.SEMANTICS_MECHANISMS` rather than a transcription, so
        a vocabulary move cannot leave this assertion checking a stale nine-name list.
        """
        with pytest.raises(ValueError, match="op_type must be"):
            REAL_CREATE_OPERATOR(object(), "supports", "a", ["b"])
        for meaning in sorted(SEMANTICS_MECHANISMS):
            _sdk, calls = served("_create_operator", {"operator": 1})
            ms.tortoise_link_entities(meaning, "a", ["b"])
            assert len(calls) == 1, (meaning, calls)
            op_type = calls[0][0][0]
            try:
                REAL_CREATE_OPERATOR(object(), op_type, "a", ["b"])
            except ValueError as exc:  # pragma: no cover - the regression this pins
                pytest.fail(
                    f"relation {meaning!r} dispatched op_type={op_type!r}, which the "
                    f"writer's own gate refuses: {exc}")
            except Exception:
                pass

    def test_a_MECHANISM_spelling_passes_through_untouched(self, served):
        """The retired door's positional signature still works: an `op_type` that IS a
        mechanism carries no meaning, so `semantics` stays exactly what the caller sent.

        This is the other half of the mapping's contract, pinned so it cannot over-reach
        and rewrite a call that was already correct.
        """
        _sdk, calls = served("_create_operator", {"operator": 1})
        ms.tortoise_link_entities("NAND", "a", ["b"])
        assert len(calls) == 1, calls
        args, kwargs = calls[0]
        assert args == ("NAND", "a", ["b"]), args
        assert kwargs["semantics"] is None, kwargs

    def test_a_relation_that_is_itself_the_meaning_refuses_a_second_one(self, served):
        """One keyword, one meaning: `relation='supports'` + `semantics='refutes'` is a
        contradiction, not a request to pick one. It is refused BEFORE the writer, which
        is the same rule the SDK states for every dropped keyword."""
        _sdk, calls = served("_create_operator", {"operator": 1})
        out = ms.tortoise_link_entities("supports", "a", ["b"], semantics="refutes")
        assert isinstance(out, dict) and "error" in out, out
        assert "semantics" in out["error"], out
        assert calls == [], "a refused call must not reach the writer"

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


class TestBothLinkArmsAreQuotaGated:
    """#11609 P7 review (P1): the linking verb absorbed TWO metered write paths.

    `tortoise_create_edge` and `tortoise_create_operator` both wrapped their writer
    in `_quota_gated`; the single door that replaces them must meter both of its
    arms, or the batch ships the surface's only unmetered edge-write path. The
    inventory half is what `tests/test_mcp_http.py::TestIntrospectiveQuotaCompleteness`
    cross-checks (it scans a handler body for `.create_operator`), so this pair pins
    the OTHER arm too — the one no scan pattern reaches, because `.link_entities` is
    the SDK's public name and no pattern names it.
    """

    def test_the_inventory_carries_it_because_BOTH_arms_write(self):
        import inspect
        import re

        assert "tortoise_link_entities" in ms._QUOTA_GATED, (
            "the #329 inventory omits the one linking verb — its LIST arm reaches "
            "`_create_operator` (operator Points carry EP weight) and its SCALAR arm "
            "reaches the edge writer, so it is the exact class the inventory exists for")
        src = inspect.getsource(ms.tortoise_link_entities)
        for writer in ("._create_operator", ".link_entities"):
            assert re.search(rf"\._?{re.escape(writer[1:])}", src), (
                f"the handler no longer reaches {writer}: if that arm was removed, the "
                "quota inventory and this pin must move with it")

    def test_both_arms_route_through_the_quota_wrapper(self, served, monkeypatch):
        """A spy on `_quota_gated` proves the WRAP, not just the inventory row.

        Membership alone is satisfied by a handler that names the tool and then calls
        the writer bare — which is the state this finding described.
        """
        seen: list = []
        real = ms._quota_gated

        def spy(fn, resource="points", **kwargs):
            seen.append(fn)
            return real(fn, resource, **kwargs)

        monkeypatch.setattr(ms, "_quota_gated", spy)
        # the LIST arm — one operator over N targets
        served("_create_operator", {"operator": 1})
        assert ms.tortoise_link_entities("NAND", "a", ["b"]) == {"operator": 1}
        # the SCALAR arm — the SDK's own relation-decided dispatch
        served("link_entities", {"edge": 1})
        assert ms.tortoise_link_entities("performs", "a", "b") == {"edge": 1}
        # Identity, not a name: the `served` fixture substitutes each writer with its
        # own recorder, so the NAME of the wrapped callable proves nothing — the
        # object does. Zero entries would mean the wrap is gone entirely.
        assert len(seen) == 2, (
            f"the quota wrapper saw {len(seen)} of the handler's two arms — both must "
            "pass through it")
        assert getattr(seen[0], "__func__", seen[0]) is TortoiseSDK._create_operator, (
            "the LIST arm reached the operator writer without the quota wrapper")
        assert getattr(seen[1], "__func__", seen[1]) is TortoiseSDK.link_entities, (
            "the SCALAR arm reached the SDK's edge dispatch without the quota wrapper")


class TestTheRelationDecidedArmCountsItsPointCreates:
    """R1 metering: the wrapper checks the cap, but the WEIGHT is a separate claim.

    `_quota_gated(sdk.link_entities, "points")` records no `point_create` at all, and
    the SCALAR arm can create a Point — `link_entities("supports", a, b)` reaches the
    SDK's `create_operator` (the meaning's mechanism). That is the whole live rule
    (#14237 N2): an explicit `operator=true`, like any LIST target, never reaches this
    arm — the handler routes it to its OPERATOR writer, which weighs `1` directly. So
    the arm that shipped counted a plain edge correctly and lost the operator half of
    its own dispatch. A flat `1` would be the opposite error: an
    ordinary `performs` link would be billed as a Point create, tripping an abuse
    signal on the most common call this door serves.

    The rule must also not be a SECOND copy of the SDK's dispatch: it is read from the
    SDK's own `_EPISTEMIC_RELATION_MEANINGS`, or the day a meaning is added the door
    and the SDK disagree with nothing red.
    """

    @pytest.mark.parametrize("relation,expected", [
        ("supports", 1),        # meaning -> SEMANTICS_MECHANISMS -> create_operator
        ("refutes", 1),
        ("mutuallySupportive", 1),
        ("IMPL", 0),            # plain edge on this arm
        ("performs", 0),
        ("ownedBy", 0),
        ("references", 0),
    ])
    def test_the_weight_follows_the_SDK_s_own_dispatch_table(self, relation, expected):
        weight = ms._link_entities_scalar_abuse_weight(
            {"edge": 1}, (relation, "a", "b"), {})
        assert weight == expected, (
            f"{relation!r} was weighted {weight}: an operator Point and a plain edge "
            "cannot share a weight, or the abuse signal means nothing")

    def test_an_explicit_operator_true_is_routed_to_the_OPERATOR_arm(
            self, served, monkeypatch):
        """The routing fact the helper's SCOPE note rests on (#14237 N2).

        The helper keeps an `operator is True` term as belt-and-braces and its SCOPE
        note says production cannot reach it, because the handler sends an explicit
        `operator=true` to the OPERATOR writer, which passes `abuse_weight=1`
        directly. That claim is only worth what its measurement is: asserted here on
        the OBJECT the handler wraps, and on the WEIGHT it receives.
        """
        seen: list = []
        real = ms._quota_gated

        def spy(fn, resource="points", **kwargs):
            seen.append((fn, kwargs.get("abuse_weight")))
            return real(fn, resource, **kwargs)

        monkeypatch.setattr(ms, "_quota_gated", spy)
        served("_create_operator", {"operator": 1})
        served("link_entities", {"edge": 1})
        assert ms.tortoise_link_entities(
            "supports", "a", "b", operator=True) == {"operator": 1}, (
            "an explicit operator=true did not write through the operator arm")
        assert len(seen) == 1, (
            f"the handler wrapped {len(seen)} writers for one call — an explicit "
            "operator=true must reach exactly one")
        writer, weight = seen[0]
        assert getattr(writer, "__func__", writer) is TortoiseSDK._create_operator, (
            "an explicit operator=true reached the SDK's relation-decided dispatch — "
            "if that routing ever moves, this test AND the weight helper's "
            "'unreachable' SCOPE note must move with it")
        assert weight == 1, (
            "the OPERATOR arm weighs its Point create directly; a callable here would "
            "mean the scalar helper is on a path its own SCOPE note declares "
            "unreachable")

    def test_an_explicit_operator_true_is_weighted(self):
        assert ms._link_entities_scalar_abuse_weight(
            {"": 1}, ("ownedBy", "a", "b"), {"operator": True}) == 1

    @pytest.mark.parametrize("padded", [" supports ", "supports\n", "\tsupports"])
    def test_a_PADDED_spelling_is_still_an_operator(self, padded):
        """The helper applies the SDK's OWN normalisation — MEASURED, not assumed.

        `TortoiseSDK.link_entities` opens with `rel = (relation or "").strip()` and
        decides the arm from `_EPISTEMIC_RELATION_MEANINGS.get(rel.upper())`, so a padded
        spelling builds a carrying operator exactly as the bare one does. Measured on the
        REAL dispatch at this head (`TortoiseSDK.__new__` + recorder doors, no graph):
        `" supports "`, `"supports\n"` and `"\tsupports"` all reach `_create_operator`.

        ⛔ THIS ROW IS THE FAIL-OPEN MUTANT'S ONLY WITNESS. Every param row above spells
        the meanings EXACTLY, so dropping `.strip()` from the helper — a one-character
        "simplification" of a line that now looks redundant — leaves all of them green
        while the padded spellings that DO mint an operator get weighted 0, i.e. the
        point_create goes unrecorded. That is the same class §1 of this PR exists about,
        one layer in, so the padding is pinned rather than left to the exact spellings.
        """
        assert ms._link_entities_scalar_abuse_weight(
            {"": 1}, (padded, "a", "b"), {}) == 1, (
            f"the padded spelling {padded!r} was weighted 0 — the SDK strips before its "
            "lookup, so this call DOES mint a Point and a 0 here records nothing")

    def test_a_meaning_ADDED_to_the_SDK_is_weighted_without_editing_the_door(
            self, monkeypatch):
        """The "read from the SDK, not copied" claim, MEASURED (#14237 r2 review, P2).

        The param rows above are the meanings the SDK declares TODAY, so a hand-copied
        tuple of those four spellings passes every one of them — which is exactly the
        parallel-list failure the helper's docstring says it cannot have, left
        unasserted. This row adds a FIFTH meaning to the SDK's own mapping and requires
        the door to weight it with no edit here: the copied-list mutant answers 0.
        """
        meanings = dict(TortoiseSDK._EPISTEMIC_RELATION_MEANINGS)
        assert "entails" not in {str(k).lower() for k in meanings}, (
            "the fixture meaning collides with a live SDK meaning — pick another")
        meanings["ENTAILS"] = "entails"
        monkeypatch.setattr(TortoiseSDK, "_EPISTEMIC_RELATION_MEANINGS", meanings)
        weight = ms._link_entities_scalar_abuse_weight({}, ("entails", "a", "b"), {})
        assert weight == 1, (
            "a meaning added to the SDK's own dispatch table was not weighted by the "
            f"door (got {weight}): the door is reading a COPY of that table, not the "
            "table itself, so the two can drift with every row here green")

    def test_the_scalar_arm_PASSES_a_weight_to_the_wrapper(self, served, monkeypatch):
        """Wiring, measured on the OBJECT: the wrapper must RECEIVE the callable.

        ⛔ `inspect.getsource` was the previous form and it is not a gate (#14237 review
        N1): it includes comments, so `abuse_weight=_link_entities_scalar_abuse_weight`
        surviving in a COMMENT kept the row green while the call itself was mutated back
        to passing no weight. `_quota_gated` is spied and the weight's IDENTITY asserted,
        which is falsifiable by that one edit.
        """
        received: list[dict] = []
        real = ms._quota_gated

        def spy(fn, resource="points", **kwargs):
            received.append(kwargs)
            return real(fn, resource, **kwargs)

        monkeypatch.setattr(ms, "_quota_gated", spy)
        served("link_entities", {"edge": 1})
        assert ms.tortoise_link_entities("performs", "a", "b") == {"edge": 1}
        assert len(received) == 1, received
        assert received[0].get("abuse_weight") is ms._link_entities_scalar_abuse_weight, (
            "the relation-decided arm is wrapped WITHOUT the derived weight, so the "
            "operator it can create is never recorded as a point_create — and a "
            "source-text match cannot see that, so this asserts the object instead")


class TestThePreviewReceiptIsNotWiderThanTheDefect:
    """The review's F5 premise, measured — and the half that is actually true.

    The review reported that a caller of the ADVERTISED `tortoise_delete_knowledge`
    "gets a receipt naming the retired door". Verifying it against primary source: the
    preview's `tool` field said `tortoise_delete`, but `tortoise_delete_knowledge`
    REBUILDS the reply through `_deletion.receipt(...)`, which has no `tool` key and
    copies only `target`/`found`/`nodes_removed`/`edges_removed`/`nodes`/`edges` from
    it. So the retired literal never reached a caller — it was a DEAD assignment.

    Both halves are pinned here, because the fix is only honest if the second one stays
    true: if a future consumer starts forwarding `preview["tool"]` into the receipt, the
    receipt must be naming the LIVE door, not the retired spelling.
    """

    def _stub(self, monkeypatch, served, *, resolved=False):
        """Install the SDK recorder and a projection whose resolution we choose.

        ⛔ `resolved=False` exercises the NOT-FOUND early return (`_preview_result(
        "tortoise_delete_knowledge", …)`); `resolved=True` exercises the FOUND branch —
        `out["tool"] = "tortoise_delete_knowledge"` — which is the assignment the P2 fix
        is actually about and which the not-found stub never reaches (#14237 review F2).
        """
        sdk, calls = served("_delete", True)

        class _Proj:
            @staticmethod
            def _resolve_entity(*_a, **_k):
                return [{"label": "Point", "id": "p1"}] if resolved else []

        monkeypatch.setattr(sdk, "_get_proj", lambda: _Proj(), raising=False)
        if resolved:
            # The leaf previews walk the graph; this class measures the DISPATCHER's
            # `tool` field, so the leaves are substituted rather than queried.
            monkeypatch.setattr(
                ms, "_preview_delete_point",
                lambda _sdk, _id: ms._preview_result(
                    "tortoise_delete_point", "delete", found=True,
                    target={"id": _id, "label": "Point"},
                    nodes_removed=1, edges_removed=0, nodes=[_id], edges=[]))
        return sdk, calls

    @pytest.mark.parametrize("resolved", [False, True], ids=["not-found", "resolved"])
    def test_the_preview_field_names_the_ADVERTISED_door(self, monkeypatch, served,
                                                         resolved):
        """BOTH literals, because they are different assignments.

        The not-found path returns `_preview_result("tortoise_delete_knowledge", …)`;
        the found path returns a leaf preview and then stamps `out["tool"]`. A stub
        that only ever answers "not found" pins the first and leaves the second free to
        revert to the retired spelling with every row green — the false PASS #3442
        exists to refuse.
        """
        sdk, _calls = self._stub(monkeypatch, served, resolved=resolved)
        preview = ms._preview_delete(sdk, "p1")
        assert preview["tool"] == "tortoise_delete_knowledge", preview
        assert preview["tool"] in {t.name for t in TOOL_REGISTRY}, (
            "the preview names a door the surface does not advertise, so a caller "
            "following it calls a tool it was never offered")

    @pytest.mark.parametrize("resolved", [False, True], ids=["not-found", "resolved"])
    def test_the_value_a_caller_can_get_back_names_no_retired_door(self, monkeypatch,
                                                                   served, resolved):
        """The fail-open half, made falsifiable.

        ⛔ The previous form asserted `RETIRED_USE_INSTEAD` names never appear in the
        receipt. That assertion could not fail under ANY revert: `_deletion.receipt`
        carries no `tool` key, so no preview literal reaches it (measured, #14237 review
        F2) — dead weight that reads as coverage (#3442 rule 3). And its plain `in`
        check was wrong in the other direction: `tortoise_delete` is a SUBSTRING of the
        live `tortoise_delete_knowledge`, so the CORRECT value would have tripped it.

        So it measures the value a future consumer would forward, on the path that
        produces it: the dispatcher's own `tool` (which reds when either literal is
        reverted) and the receipt, with a WORD-BOUNDED match so the live door — and any
        name the surface still advertises — cannot be misread as a retirement.

        ⛔ WHICH HALF IS COVERAGE AND WHICH IS A FORWARD GUARD, MEASURED (#14237 round-5
        review, P2). The review said the `("receipt", receipt)` row "never reaches a
        receipt". **Half of that is falsified by primary source**: calling
        `tortoise_delete_knowledge("p1")` with no `confirmation` takes the preview branch
        (`mcp_server.py:7144`), that branch DOES build the payload with
        `_deletion.receipt(...)` (`:7155`), and returns that very dict after overlaying
        the preview's fields (`:7161-7167`) — so the payload IS the receipt body
        (`preview is True`, `deleted is False`, a `confirmation` token). What IS true is
        the sharper half: `:7161-7167` stamps `dry_run` and copies `found`/`target`/
        `nodes_removed`/`edges_removed`/`nodes`/`edges`, and **never `tool`**, so no
        preview literal can reach this row — an assertion here could not fail under the
        revert it names.
        That is a FORWARD GUARD (the day a consumer starts forwarding `tool`, the receipt
        must name the live door), not coverage of this diff, and it is written as such
        below rather than left to read as a second pin. Driven on the preview path on
        purpose: the confirm path needs a token and destroys a node to inspect a dict
        whose `tool` key is absent either way.
        """
        sdk, calls = self._stub(monkeypatch, served, resolved=resolved)
        preview = ms._preview_delete(sdk, "p1")
        receipt = ms.tortoise_delete_knowledge("p1")
        assert calls == [], "the preview reached the writer"
        live = {t.name for t in TOOL_REGISTRY}
        for surface, payload in (("preview", preview), ("receipt", receipt)):
            named = [
                name for name in sorted(RETIRED_USE_INSTEAD)
                if name not in live
                and re.search(rf"\b{re.escape(name)}\b", str(payload))
            ]
            assert not named, (
                f"the {surface} a caller receives names the retired {named} — the value "
                "a caller re-calls must be one `tools/list` advertises")
        # The receipt row above is the forward guard, and THIS is its falsifier: the
        # overlay stamps `dry_run` and copies six preview fields, never `tool`, which is why
        # no retired literal can reach a caller (`mcp_server.py:7161-7167`). The day that
        # changes, this reds and the loop entry becomes real coverage of what it forwards.
        assert receipt.get("preview") is True and receipt.get("deleted") is False, (
            f"the `receipt` row no longer inspects a receipt body: {receipt}")
        assert "tool" not in receipt, (
            "`tortoise_delete_knowledge`'s preview reply now carries a `tool` field — the "
            "receipt row above stops being a forward guard and becomes live coverage, so "
            "re-measure it (and its revert) instead of trusting this note")

