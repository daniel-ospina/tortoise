"""#11609 P2 — `check_confidence`'s RECOMPUTE and RECALL arms, and the two doors they retire.

WHAT THIS FILE MEASURES, AND WHY IT IS NOT A RESTATEMENT OF THE SIGNATURE
------------------------------------------------------------------------
`tests/test_11609_door_retirement.py` holds the `PROTECTED` contract: a door may leave
that table only in the batch that makes its stated reason TRUE-FALSE, proven by a test.
`tortoise_compute_confidence` was held because *"check_confidence does not recompute"*
and `tortoise_recall` because *"check_confidence has no recall arm"* — both ABSENCES.
A signature check on `recompute=`/`recall=` would be satisfied by an arm that accepts
the flag and answers something else, which is the failure the whole wave exists to
avoid (delete the door, keep the name, no capability behind either). So this file
measures the three things a parameter presence cannot:

  1. **The arm DISPATCHES** — measured by substitution at the SDK method each retired
     door called (`compute_confidence` / `recall_state`), the same seam
     `test_11609_write_containers.py` uses at `_create_entity`. The container answers,
     not a copy of it.
  2. **The answer is the SAME** as the retired door's — the arm's payload is compared
     for equality against the door's own, per recall intent (`state`, `gaps`,
     `subgraph`, `custom`), with the SDK stubbed on both sides so this is a statement
     about dispatch and shape, never about storage.
  3. **The retired NAME still answers** through the #3883 warning shim, with the
     retirement marker and the pointer in `_meta` — the half that makes a retirement a
     consolidation rather than a removal.

⛔ AND THE ONE THING THE ABSORBED DOOR'S CALLERS WOULD LOSE SILENTLY: `limit=`.
`check_confidence`'s `topic` arm defaults it to 50 and `recall_state` defaults it to
10, so a single shared default would have widened every migrating recall caller's
answer 5x with no error. That is pinned explicitly, both directions.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

# The declared public-method set is ONE implementation (#4282): "did not grow" is
# measured against it, never against a count typed here.
from tools.sdk_surface import load_declaration, reflected_from_runtime
from tortoise import mcp_server as ms
from tortoise.sdk import TortoiseSDK
from tortoise.tool_registry import (
    RETIRED_TOOL_REGISTRY,
    RETIRED_USE_INSTEAD,
    TOOL_REGISTRY,
)

RETIRED_BY_NAME = {t.name: t for t in RETIRED_TOOL_REGISTRY}


def _sdk_stub(monkeypatch, method: str, payload):
    """Replace ONE SDK method with a recorder; return the call log.

    `TortoiseSDK.__new__` is the container with no constructor run, so this asserts
    DISPATCH and the forwarded arguments, never persistence — the same posture the
    READ/WRITE container suites take.
    """
    calls: list[tuple[tuple, dict]] = []

    def _fn(_self, *args, **kwargs):
        calls.append((args, kwargs))
        return payload

    monkeypatch.setattr(TortoiseSDK, method, _fn, raising=True)
    return TortoiseSDK.__new__(TortoiseSDK), calls


@pytest.fixture
def door(monkeypatch):
    """`_safe`'s auth gate refuses when the transport mode is unset (fail-closed,
    #236) — every case here measures DISPATCH, not auth, so name the mode.

    Returns a callable that installs a recording SDK as the org SDK and hands back
    its call log, so a door test reads `door("compute_confidence", payload)[0]`.
    """
    token = ms._transport_mode.set("stdio")
    monkeypatch.setattr(ms, "_is_dev_mode", lambda: True)
    monkeypatch.setattr(ms, "_get_org_sdk", lambda: object())
    try:
        def install(method: str, payload):
            sdk, calls = _sdk_stub(monkeypatch, method, payload)
            monkeypatch.setattr(ms, "_get_org_sdk", lambda: sdk)
            return calls
        yield install
    finally:
        ms._transport_mode.reset(token)


class TestTheRecomputeArm:
    """`recompute=True` — the arm `tortoise_compute_confidence` retires into."""

    def test_it_reaches_compute_confidence_with_the_arguments_the_door_took(
            self, monkeypatch):
        sdk, calls = _sdk_stub(monkeypatch, "compute_confidence", {"confidences": {}})
        sdk.check_confidence(
            recompute=True, factors=[{"id": "f1"}], evidence={"e": 1},
            anchors=["p1"], max_hops=2, rel_filter="IMPL", direction="outgoing",
            require_calibration=False)
        assert len(calls) == 1, f"one recompute produced {len(calls)} calls: {calls}"
        args, kwargs = calls[0]
        assert args == ([{"id": "f1"}], {"e": 1}), args
        assert kwargs == {
            "anchors": ["p1"], "max_hops": 2, "rel_filter": "IMPL",
            "direction": "outgoing", "require_calibration": False,
            "recency_decay": None,
        }, kwargs

    def test_it_returns_the_arm_s_answer_unwrapped(self, monkeypatch):
        payload = {"iterations": 3, "converged": True, "confidences": {"p1": 0.7}}
        sdk, _ = _sdk_stub(monkeypatch, "compute_confidence", payload)
        assert sdk.check_confidence(recompute=True) is payload, (
            "the recompute arm wrapped its answer — a migrating caller parsing the "
            "retired door's payload would then read a nesting that is not there")

    def test_the_no_argument_form_is_still_the_dirty_subgraph_pass(self, monkeypatch):
        """`tortoise_compute_confidence()` with no arguments is the LOCAL EP path —
        the arm must forward that shape, not invent factors (#395 delta C)."""
        sdk, calls = _sdk_stub(monkeypatch, "compute_confidence", {})
        sdk.check_confidence(recompute=True)
        args, kwargs = calls[0]
        assert args == (None, None), args
        assert kwargs["anchors"] is None and kwargs["max_hops"] == 1, kwargs


class TestTheRecallArm:
    """`recall=True` — the arm `tortoise_recall` retires into."""

    def test_it_reaches_recall_state(self, monkeypatch):
        sdk, calls = _sdk_stub(monkeypatch, "recall_state", [{"id": "p1"}])
        assert sdk.check_confidence(recall=True, query="what is true") == [{"id": "p1"}]
        args, kwargs = calls[0]
        assert args == ("what is true",), args
        assert kwargs["kind"] is None and kwargs["include_superseded"] is False

    @pytest.mark.parametrize("supplied,expected", [
        (None, 10), (3, 3),
    ])
    def test_the_default_limit_is_recall_s_own_not_topic_s(
            self, supplied, expected, monkeypatch):
        """⛔ THE SILENT-WIDENING GUARD. `recall_state` bounds at 10; `check_confidence`'s
        `topic` arm bounds at 50. One shared default would have answered a migrating
        `tortoise_recall(query=...)` with five times the rows and no error."""
        sdk, calls = _sdk_stub(monkeypatch, "recall_state", [])
        sdk.check_confidence(recall=True, query="q", limit=supplied)
        assert calls[0][1]["limit"] == expected, calls[0][1]

    def test_the_topic_arm_keeps_its_own_fifty(self, monkeypatch):
        sdk, calls = _sdk_stub(monkeypatch, "belief_timeline", [])
        sdk.check_confidence(topic="t")
        assert calls[0][1]["limit"] == 50, (
            "the topic arm's historical limit moved when the recall arm's arrived")

    def test_the_preset_defaults_are_forwarded_resolved(self, monkeypatch):
        """`state` applies the preset (1.0 / 1.0 / 0.10) and `custom` passes through —
        at the SDK layer `recall_state`'s own signature defaults ARE that preset, so
        the resolved value is the whole difference and it must be sent, not omitted."""
        sdk, calls = _sdk_stub(monkeypatch, "recall_state", [])
        sdk.check_confidence(recall=True, query="q")
        assert calls[0][1]["relevance_exp"] == 1.0
        assert calls[0][1]["confidence_exp"] == 1.0
        assert calls[0][1]["centrality_weight"] == 0.10

    def test_a_structure_intent_is_refused_and_names_its_direct_route(self, monkeypatch):
        """`gaps`/`subgraph` are graph-structure reads, not confidence reads: they are
        reachable on the SDK as their own methods, so the refusal NAMES them rather
        than silently answering the state arm."""
        sdk, calls = _sdk_stub(monkeypatch, "recall_state", [])
        with pytest.raises(ValueError) as exc:
            sdk.check_confidence(recall=True, mode="gaps", query="q")
        assert "recall_gaps" in str(exc.value), str(exc.value)
        assert calls == [], "a refused arm still called the SDK"


class TestOneArmPerCall:
    """A combined call would answer one arm and DROP the other with no error."""

    @pytest.mark.parametrize("kwargs", [
        {"recompute": True, "recall": True},
        {"recompute": True, "claim_id": "c1"},
        {"recompute": True, "topic": "t"},
        {"recompute": True, "session": True},
        {"recall": True, "claim_id": "c1"},
        {"recall": True, "topic": "t"},
        {"recall": True, "calibrate": True},
    ])
    def test_the_sdk_refuses_two_arms(self, kwargs):
        with pytest.raises(ValueError) as exc:
            TortoiseSDK.__new__(TortoiseSDK).check_confidence(**kwargs)
        named = "recompute" if kwargs.get("recompute") else "recall"
        assert named in str(exc.value), str(exc.value)

    @pytest.mark.parametrize("kwargs", [
        {"recompute": True, "claim_id": "c1"},
        {"recall": True, "topic": "t"},
        {"recompute": True, "recall": True},
    ])
    def test_the_door_refuses_two_arms_as_a_structured_error(self, kwargs):
        out = ms.tortoise_check_confidence(**kwargs)
        assert out["code"] == ms.ERR_INVALID, out
        if kwargs.get("recompute") and kwargs.get("recall"):
            assert "ONE arm per call" in out["error"], out
            return
        arm = "recompute" if kwargs.get("recompute") else "recall"
        assert f"{arm}=True cannot be combined" in out["error"], out

    def test_the_legacy_precedence_is_untouched(self, monkeypatch):
        """`claim_id` still beats `topic`, and `session` still beats `calibrate`.

        The refusal above guards the NEW arms only: a caller relying on the historical
        precedence keeps its exact behaviour, which is why that pair is not in the
        refusal table."""
        sdk, calls = _sdk_stub(monkeypatch, "get_confidence", {"mean": 0.5})
        assert sdk.check_confidence("c1", topic="ignored") == {"mean": 0.5}
        assert calls[0][0] == ("c1",), calls

        sdk2, calls2 = _sdk_stub(monkeypatch, "session_context", {"session": "s"})
        assert sdk2.check_confidence(session=True, calibrate=True) == {"session": "s"}
        assert len(calls2) == 1, calls2


class TestTheArmArgumentsNameTheirArm:
    """⛔ AN ARM-ONLY ARGUMENT NAMES ITS ARM, AND IS REFUSED WITHOUT IT.

    Measured on the pre-fix head: `check_confidence(query="what is true now")`,
    `check_confidence(factors=[{"id": "f1"}])` and
    `check_confidence(min_confidence=0.9)` each answered `calibrate_summary()` with the
    argument dropped and NO error. The retired pointer is
    `check_confidence(recall=True, query=..., min_confidence=...)`, so a caller that
    copies the arguments and loses the flag receives the graph-wide calibration audit
    and reads it as a successful migration — the exact answers-one-drops-the-other
    shape `TestOneArmPerCall` refuses everywhere else. Each case below is a silent
    success on the pre-fix code and a refusal here.
    """

    @pytest.mark.parametrize("kwargs,arm,arg", [
        ({"query": "what is true now"}, "recall", "query"),
        ({"min_confidence": 0.9}, "recall", "min_confidence"),
        ({"mode": "gaps"}, "recall", "mode"),
        ({"include_superseded": True}, "recall", "include_superseded"),
        ({"factors": [{"id": "f1"}]}, "recompute", "factors"),
        ({"evidence": [{"id": "e1"}]}, "recompute", "evidence"),
        ({"anchors": ["p1"]}, "recompute", "anchors"),
        ({"max_hops": 3}, "recompute", "max_hops"),
        ({"rel_filter": "IMPL"}, "recompute", "rel_filter"),
        ({"direction": "incoming"}, "recompute", "direction"),
    ])
    def test_the_sdk_refuses_an_arm_argument_without_the_arm(
            self, kwargs, arm, arg, monkeypatch):
        sdk, calls = _sdk_stub(monkeypatch, "calibrate_summary", {"calibration": 1})
        with pytest.raises(ValueError) as exc:
            sdk.check_confidence(**kwargs)
        assert arg in str(exc.value), str(exc.value)
        assert f"{arm}=True" in str(exc.value), str(exc.value)
        assert calls == [], (
            "a call that names an arm's argument but not the arm still ran the "
            "default arm — the argument was dropped with no error")

    @pytest.mark.parametrize("kwargs,arm,arg", [
        ({"query": "what is true now"}, "recall", "query"),
        ({"min_confidence": 0.9}, "recall", "min_confidence"),
        ({"seed": "s1"}, "recall", "seed"),
        ({"max_nodes": 5}, "recall", "max_nodes"),
        ({"factors": [{"id": "f1"}]}, "recompute", "factors"),
        ({"anchors": ["p1"]}, "recompute", "anchors"),
        ({"max_hops": 3}, "recompute", "max_hops"),
    ])
    def test_the_door_refuses_an_arm_argument_without_the_arm(
            self, kwargs, arm, arg, door):
        calls = door("calibrate_summary", {"calibration": 1})
        out = ms.tortoise_check_confidence(**kwargs)
        assert out["code"] == ms.ERR_INVALID, out
        assert arg in out["error"], out
        assert f"{arm}=True" in out["error"], out
        assert calls == [], out

    def test_a_call_naming_arguments_from_BOTH_arms_names_both_in_one_refusal(
            self, door):
        """One round trip, not two: the refusal lists both sets."""
        calls = door("calibrate_summary", {"calibration": 1})
        out = ms.tortoise_check_confidence(query="q", factors=[])
        assert out["code"] == ms.ERR_INVALID, out
        assert "recompute arm: " in out["error"], out
        assert "recall arm: " in out["error"], out
        assert calls == [], out

    def test_a_shared_parameter_names_no_arm(self, monkeypatch):
        """`limit` and `require_calibration` belong to a LEGACY arm too, so supplying
        them must NOT be read as naming one (the `topic` arm's 50 depends on it)."""
        sdk, calls = _sdk_stub(monkeypatch, "belief_timeline", [])
        sdk.check_confidence(topic="t", limit=7, require_calibration=False)
        assert calls[0][1]["limit"] == 7, calls
        sdk2, calls2 = _sdk_stub(monkeypatch, "calibrate_summary", {})
        sdk2.check_confidence(limit=7)
        assert len(calls2) == 1, calls2

    def test_the_door_forwards_no_limit_to_the_recall_arm_so_the_preset_holds(
            self, monkeypatch):
        """The door must forward `limit=None`, not a resolved 10 or 50: `tortoise_recall`
        owns a PER-MODE default (10 for `state`, 20 for `gaps`), so resolving it at the
        door would flatten the modes to one number. `mode` IS resolved (None -> the
        door's documented `state`), because the arm-name refusal needs `None` to mean
        "not supplied"."""
        calls: list[tuple] = []

        def _fn(query=None, **kwargs):
            calls.append((query, kwargs))
            return {"mode": "state", "results": []}

        monkeypatch.setattr(ms, "tortoise_recall", _fn, raising=True)
        ms.tortoise_check_confidence(recall=True, query="q")
        assert calls[0][1]["limit"] is None, calls[0][1]
        assert calls[0][1]["mode"] == "state", calls[0][1]


class TestTheAbsorbedDoorStillAnswers:
    """The #3883 half: the retired NAME resolves, warns, and reaches its handler."""

    @pytest.mark.parametrize("name", [
        "tortoise_compute_confidence", "tortoise_recall",
    ])
    def test_the_name_is_off_the_advertised_surface_but_still_served(self, name):
        assert name not in {t.name for t in TOOL_REGISTRY}, (
            f"{name} is still advertised, so it did not retire")
        assert name in RETIRED_USE_INSTEAD, (
            f"{name} left the registry without a forward pointer: a caller gets "
            "'unknown tool' instead of a warning naming the arm")
        assert name in RETIRED_BY_NAME

        tool = asyncio.run(ms.mcp.get_tool(name))
        marker = ((tool.meta or {}).get("tortoise") or {}).get("retired")
        assert marker and marker["retired"] is True, (
            f"{name} resolved without its retirement marker — the shim would answer "
            "silently as if it were still advertised")
        assert marker["use_instead"] == RETIRED_USE_INSTEAD[name]
        assert "check_confidence(" in marker["message"]

    @pytest.mark.parametrize("name, method, payload", [
        ("tortoise_compute_confidence", "compute_confidence", {"confidences": {}}),
        ("tortoise_recall", "recall_state", [{"id": "p1"}]),
    ])
    def test_the_retired_name_runs_its_handler(self, name, method, payload, door):
        """Substituted at the SDK method the handler dispatches to: the shim must CALL
        the capability, not merely resolve to a description.

        `callable(tool.fn)` is not this claim — a shim that returned `None`, or one
        wired to a copy of the answer, is equally callable. The recorder goes where the
        shim's handler ends up, so an empty call log means the shim never reached it.
        """
        calls = door(method, payload)
        tool = asyncio.run(ms.mcp.get_tool(name))
        assert callable(tool.fn), f"{name} resolved to a tool with no handler"

        tool.fn()

        why = ("the retired name resolved but never reached the handler" if not calls
               else "the shim called the capability more than once")
        assert len(calls) == 1, (
            f"{name} dispatched to the capability it shim's {len(calls)} times: "
            f"{calls} — {why}")

    @pytest.mark.parametrize("name", [
        "tortoise_compute_confidence", "tortoise_recall",
    ])
    def test_the_pointer_resolves_in_one_hop_to_a_live_tool(self, name):
        target = RETIRED_USE_INSTEAD[name].split("(", 1)[0].strip()
        live = {t.name for t in TOOL_REGISTRY}
        assert target in live, (
            f"{name} points at {target!r}, which is not live — aliases are not "
            "transitive, so a migrating caller would land on another shim")


class TestTheDoorAndTheDoorItReplacedAgree:
    """Equal PAYLOADS, per intent — measured with the SDK stubbed on both sides.

    This is scope item 4's "the same node the retired door did", applied to a
    container that returns a payload rather than creating a node: a recall intent must
    answer exactly what `tortoise_recall` answered for it, or the retirement changed
    the operation rather than the name.
    """

    @pytest.mark.parametrize("mode", ["state", "gaps", "subgraph", "custom"])
    def test_every_recall_intent_answers_identically(self, mode, monkeypatch, door):
        payload = ({"nodes": [], "edges": [], "stats": {"n": 0}}
                   if mode == "subgraph" else [{"id": "p1"}])
        stub_name = {"gaps": "recall_gaps", "subgraph": "recall_subgraph"}.get(
            mode, "recall_state")
        door(stub_name, payload)

        kwargs = dict(mode=mode, query="q", limit=5,
                      min_confidence=0.2, include_superseded=True)
        if mode == "subgraph":
            kwargs["seed"] = "p1"
        if mode == "gaps":
            kwargs.update(min_load=2, max_support=3)

        through_the_door = ms.tortoise_recall(**kwargs)
        through_the_container = ms.tortoise_check_confidence(recall=True, **kwargs)
        assert through_the_container == through_the_door, (
            f"mode={mode!r} answered {through_the_container!r} through the container and "
            f"{through_the_door!r} through the door it absorbed — the retirement changed "
            "the operation, not the name")

    def test_the_recompute_arm_answers_identically(self, door):
        payload = {"iterations": 2, "converged": True, "confidences": {"p1": 0.4}}
        door("compute_confidence", payload)
        assert (ms.tortoise_check_confidence(recompute=True, anchors=["p1"])
                == ms.tortoise_compute_confidence(anchors=["p1"]) == payload)

    def test_the_recompute_arm_keeps_the_door_s_fail_closed_calibration_default(
            self, door):
        """`tortoise_compute_confidence` requires calibration by DEFAULT (#344) —
        `require_calibration=None` at the container means NOT SUPPLIED, so it must
        resolve to that door's `True`, never to the SDK read's `None`."""
        calls = door("compute_confidence", {})
        ms.tortoise_check_confidence(recompute=True)
        assert calls[0][1]["require_calibration"] is True, calls[0][1]


class TestTheSignaturesCarryBothArms:
    """The arms exist at BOTH layers, with the retired doors' whole argument sets."""

    #: `tortoise_compute_confidence`'s own parameters, verbatim.
    COMPUTE_ARGS = ("factors", "evidence", "anchors", "max_hops", "rel_filter",
                    "direction", "require_calibration")
    #: `tortoise_recall`'s own parameters, verbatim.
    RECALL_ARGS = ("query", "mode", "kind", "limit", "include_superseded",
                   "min_confidence", "relevance_exp", "confidence_exp",
                   "centrality_weight", "seed", "depth", "completeness",
                   "min_load", "max_support", "max_nodes")

    @pytest.mark.parametrize("layer", ["door", "sdk"])
    def test_the_recompute_arm_carries_every_argument_the_door_took(self, layer):
        fn = (ms.tortoise_check_confidence if layer == "door"
              else TortoiseSDK.check_confidence)
        params = set(inspect.signature(fn).parameters)
        missing = sorted({"recompute", *self.COMPUTE_ARGS} - params)
        assert not missing, (
            f"{layer}: check_confidence no longer accepts {missing}, so a caller of the "
            "retired tortoise_compute_confidence cannot migrate without losing them")

    @pytest.mark.parametrize("layer", ["door", "sdk"])
    def test_the_recall_arm_carries_every_argument_the_door_took(self, layer):
        fn = (ms.tortoise_check_confidence if layer == "door"
              else TortoiseSDK.check_confidence)
        params = set(inspect.signature(fn).parameters)
        # The DOOR absorbs all four intents, so it carries the structure-read
        # parameters too (`seed`/`depth`/`completeness`/`min_load`/`max_support`/
        # `max_nodes`). The SDK arm absorbs `state`/`custom` only, and those six are
        # the other two intents' — the SDK already exposes them as
        # `recall_gaps`/`recall_subgraph`, which the refusal names. Neither layer may
        # drop a parameter of the intents it DOES carry.
        required = {"recall", *self.RECALL_ARGS}
        if layer == "sdk":
            required -= {"seed", "depth", "completeness", "min_load",
                         "max_support", "max_nodes"}
        missing = sorted(required - params)
        assert not missing, (
            f"{layer}: check_confidence no longer accepts {missing}, so a caller of the "
            "retired tortoise_recall cannot migrate without losing them")

    def test_the_sdk_surface_matches_its_declaration(self):
        """#11609's target is a FALLING surface: the arms are PARAMETERS on an existing
        method, so no public SDK method may have been added to carry them.

        "Did not grow" is a statement about the SET, so it is measured against the
        declared surface (`config/sdk-surface.json`) and not by five membership checks —
        those pass just as happily for a surface that gained
        `check_confidence_recompute` alongside the routes it meant to keep.

        This test is deliberately the DERIVED view's reconciliation, NOT the approval: the
        declaration is regenerable, so an intended addition can be admitted by regenerating
        it, and this test then passes by design. The authority on whether a method may
        exist is the owner's approval (#4282 — the declared target in `tortoise/__all__.py`,
        never a snapshot of what the code currently does), and what reds an UNAPPROVED
        addition in required CI is the `high_water_counts` growth pin in
        `config/surface-manifest.yml`, enforced independently by the BASE-REF-pinned
        `tools/surface-guard.py` (its step 7) — raising that pin is a deliberate edit in the
        approving PR, so a re-cut over a grown surface cannot be green by construction.
        `tools/sdk_surface.py --check` is the third thing: it keeps this declaration and the
        frozen `sdk:` rows from drifting apart. This test only keeps a surface change from
        arriving unaccompanied by its declaration.
        """
        # The REACHABLE view is `reflected_from_runtime()`'s — the same one the frozen
        # baseline was cut from. Re-implementing `dir()` + `callable` here would be a
        # second opinion about what the public surface IS (#4282).
        public = reflected_from_runtime()
        declared, _ = load_declaration()

        added = sorted(public - declared)
        assert not added, (
            f"the SDK surface gained {added} — #11609 carries both arms as PARAMETERS of "
            "`check_confidence`, so a new public method for one is the growth this wave "
            "exists to prevent. If the addition is intended it needs the owner's approval "
            "(#4282): regenerate config/sdk-surface.json, add the `sdk:` row and raise "
            "`high_water_counts.sdk_public_methods` in config/surface-manifest.yml — that "
            "pin, enforced by the base-ref-pinned tools/surface-guard.py, is what reds an "
            "unapproved addition")
        removed = sorted(declared - public)
        assert not removed, (
            f"the SDK surface lost {removed} while the declaration still names it")
        for needed in ("compute_confidence", "recall_state", "recall_gaps",
                       "recall_subgraph", "check_confidence"):
            assert needed in public, (
                f"{needed} is not on the SDK surface — the arm names a route that is "
                "not there")
