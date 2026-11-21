"""#11609 lane B — the WRITE/MUTATE containers dispatch, and DELEGATE.

The body of PR #12915 claims *both* directions are pinned here:

  1. every discriminator value dispatches to the member's OWN handler — asserted
     by observing the delegation, not by re-implementing it;
  2. a retired name is not silently gone: on the MCP surface it still RESOLVES
     and WARNS (#3883); on the SDK surface it raises naming its replacement
     (#3836(c)).

⛔ WHY THIS FILE EXISTS AT ALL (#12915 review F2). The body cited this path and
the file did not exist, so the 14-doors-into-6-tools consolidation shipped with
no assertion on the delegation at all — which is exactly the gap that let two
P0s through: `goal_judgement` still calling a RETIRED name, and
`adjust_relationship(action='mitigate')` silently dropping `credibility`. Both
now have a pin below.

⛔ AND ONE GATE, not just examples: `test_no_production_module_reaches_a_retired
_name` walks `tortoise/` and refuses an attribute access to any name in
`sdk_retired.RETIRED_SDK_METHODS`. Examples pin the cases we thought of; the
walk pins the ones we did not.
"""
from __future__ import annotations

import ast
import inspect
import json
import pathlib
import sys
import textwrap
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

from tortoise import mcp_server
from tortoise.mcp_auth import (
    _current_org_id,
    _current_org_limits,
    _transport_mode,
)
from tortoise.sdk_retired import RETIRED_SDK_METHODS

REPO = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _transport_context():
    """Every handler goes through `_safe`, which needs an initialized mode (#236)."""
    _transport_mode.set("stdio")
    _current_org_id.set(None)
    _current_org_limits.set(None)
    yield
    _transport_mode.set(None)
    _current_org_id.set(None)
    _current_org_limits.set(None)


# ── a recorder that stands in for a member handler ──────────────────

class _Member:
    """Records how a container called the member it is supposed to delegate to."""

    SENTINEL = object()

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return _Member.SENTINEL


def _stub_sdk(**methods):
    """A stand-in for the org SDK: only the named methods exist.

    Deliberately NOT a Mock: a `Mock` answers ANY attribute, so a container
    calling a name that does not exist would look like a successful delegation.
    Every name the container reaches must be one this stub was given.
    """
    class _Stub:
        pass

    stub = _Stub()
    for name, value in methods.items():
        setattr(stub, name, value)
    return stub


# ── 1. the containers delegate to the member's own handler ──────────

def test_update_knowledge_props_arm_delegates_to_tortoise_update(monkeypatch):
    member = _Member()
    monkeypatch.setattr(mcp_server, "tortoise_update", member)
    got = mcp_server.tortoise_update_knowledge("n1", props={"status": "live"})
    assert got is _Member.SENTINEL
    assert member.calls == [(("n1",), {"props": {"status": "live"}})]


def test_the_props_arm_member_routes_at_update_knowledge_not_update(monkeypatch):
    """#12915 review F1 — the ONE routing decision the container's `props` arm
    inherits from `tortoise_update`.

    `sdk.update` sends a Point to `update_point`: no `restatus` journal record
    (the `derived = replay(journal)` class) and a fixed-sentence refusal that
    names neither the target status nor the transition. `sdk.update_knowledge`
    is the relay the absorbed `tortoise_update_point` / `tortoise_update_entity`
    handlers already call, so the container must land there too.

    The stub carries ONLY `update_knowledge`: the retired routing reddens here
    with an `AttributeError` rather than silently reaching a second writer.
    """
    member = _Member()
    monkeypatch.setattr(mcp_server, "_get_org_sdk",
                        lambda: _stub_sdk(update_knowledge=member))
    got = mcp_server.tortoise_update("n1", props={"status": "superseded"})
    assert got is _Member.SENTINEL
    assert member.calls == [(("n1",), {"status": "superseded"})], member.calls


def test_update_knowledge_annotation_arm_delegates_to_annotate_operator(monkeypatch):
    member = _Member()
    monkeypatch.setattr(mcp_server, "tortoise_annotate_operator", member)
    dims = {"bias": 0.1, "precision": 0.2, "consistency": 0.3, "directness": 0.4}
    got = mcp_server.tortoise_update_knowledge("n1", annotation=dims)
    assert got is _Member.SENTINEL
    assert member.calls == [(("n1",), dims)]


def test_update_knowledge_retract_arm_delegates_to_the_sdk_retract_point(monkeypatch):
    retract = _Member()
    monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: _stub_sdk(_retract_point=retract))
    got = mcp_server.tortoise_update_knowledge("n1", invalid_at="2030-01-01T00:00:00Z")
    assert got is _Member.SENTINEL
    assert retract.calls == [(("n1",), {"valid_to": "2030-01-01T00:00:00Z"})]


def test_update_knowledge_invalidate_arm_delegates_to_the_sdk_invalidate_point(monkeypatch):
    invalidate = _Member()
    monkeypatch.setattr(mcp_server, "_get_org_sdk",
                        lambda: _stub_sdk(_invalidate_point=invalidate))
    got = mcp_server.tortoise_update_knowledge(
        "n1", invalid_at="2030-01-01T00:00:00Z", corrected_by_id="n2")
    assert got is _Member.SENTINEL
    assert invalidate.calls == [
        (("n1", "n2"), {"valid_to": "2030-01-01T00:00:00Z"})]


def test_supersede_knowledge_delegates_to_tortoise_supersede(monkeypatch):
    member = _Member()
    monkeypatch.setattr(mcp_server, "tortoise_supersede", member)
    got = mcp_server.tortoise_supersede_knowledge("a", "b", transfer_edges=False)
    assert got is _Member.SENTINEL
    assert member.calls == [(("a", "b"), {"transfer_edges": False, "dry_run": False})]


def test_adjust_relationship_fields_arms_delegate_to_their_member(monkeypatch):
    annotate, mitigate = _Member(), _Member()
    monkeypatch.setattr(mcp_server, "tortoise_annotate_operator", annotate)
    monkeypatch.setattr(mcp_server, "tortoise_mitigate_operator", mitigate)

    assert mcp_server.tortoise_adjust_relationship(
        "op1", bias=0.1, precision=0.2, consistency=0.3, directness=0.4,
    ) is _Member.SENTINEL
    assert annotate.calls == [(("op1", 0.1, 0.2, 0.3, 0.4), {})]

    assert mcp_server.tortoise_adjust_relationship(
        "op1", reason="weak", strength=0.3) is _Member.SENTINEL
    assert mitigate.calls == [(("op1", "weak", 0.3, None), {})]


def test_adjust_relationship_action_spellings_delegate_to_operator_action(monkeypatch):
    member = _Member()
    monkeypatch.setattr(mcp_server, "tortoise_operator_action", member)
    assert mcp_server.tortoise_adjust_relationship(
        "op1", reason="weak", action="mitigate") is _Member.SENTINEL
    assert member.calls[0][0][:2] == ("mitigate", "op1")


def test_refresh_confidence_arms_delegate_to_their_member(monkeypatch):
    dream, promote, baseline = _Member(), _Member(), _Member()
    monkeypatch.setattr(mcp_server, "tortoise_dream", dream)
    monkeypatch.setattr(mcp_server, "tortoise_promote_point", promote)
    monkeypatch.setattr(mcp_server, "tortoise_set_point_baseline", baseline)

    assert mcp_server.tortoise_refresh_confidence() is _Member.SENTINEL
    assert mcp_server.tortoise_refresh_confidence(scope="full") is _Member.SENTINEL
    assert mcp_server.tortoise_refresh_confidence(
        scope="promote", point_id="p1") is _Member.SENTINEL
    assert mcp_server.tortoise_refresh_confidence(
        scope="baseline", point_id="p1", alpha=2.0, beta=1.0) is _Member.SENTINEL

    assert dream.calls[0][1]["full"] is False
    assert dream.calls[1][1]["full"] is True
    assert promote.calls == [(("p1",), {})]
    assert baseline.calls == [(('p1', 2.0, 1.0), {})]


@pytest.mark.parametrize("knob", [
    {"full": True},
    {"dirty_only": False},
    {"max_hops": 9},
    {"require_calibration": False},
    {"mode": "full"},
    {"budget": 7},
])
@pytest.mark.parametrize("scope", ["promote", "baseline"])
def test_a_point_id_scope_REFUSES_the_dream_knobs(scope, knob, monkeypatch):
    """#12915 final review F2-b: a keyword the chosen arm cannot record is
    REFUSED, never dropped.

    Measured before this fix, at `bebb2332`:
    `refresh_confidence(scope='promote', point_id='p1', mode='full', budget=7,
    max_hops=9, dirty_only=False, require_calibration=False)` → the promotion ran
    (`tortoise_promote_point('p1')` was reached, returning the SDK's own
    `{id, status, promoted, blocked, …}`) and every one of those five knobs was
    silently discarded. A caller who asked for a whole-graph pass got a single
    promotion and NO signal.

    One parametrised row per knob, on BOTH point_id scopes, because the refusal
    is one code path but a future arm that splits the scopes must not be able to
    keep one and lose the other. Each row also asserts the member was NOT called:
    a refusal that still writes is the defect it refuses.
    """
    promote, baseline = _Member(), _Member()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mcp_server, "tortoise_promote_point", promote)
        patch.setattr(mcp_server, "tortoise_set_point_baseline", baseline)
        extra = {} if scope == "promote" else {"alpha": 2.0, "beta": 1.0}
        got = mcp_server.tortoise_refresh_confidence(
            scope=scope, point_id="p1", **extra, **knob)
    message = str(got.get("error", ""))
    assert next(iter(knob)) in message, (knob, got)
    assert "dream arms" in message, (knob, got)
    assert promote.calls == [] and baseline.calls == [], (knob, promote.calls, baseline.calls)


def test_an_explicit_dirty_scope_REFUSES_full_True(monkeypatch):
    """The same one-arm rule, ONE ARM OVER (#12915 review of this fix, P2).

    `scope` and `full=` both name the mechanism, and the dream arm derives
    `full` FROM the scope — so `scope='dirty', full=True` used to run a DIRTY
    dream while the caller asked for a whole-graph pass: the identical
    silently-dropped-keyword defect the point_id rows above fix, on the arm
    those rows do not reach.

    `scope=None, full=True` is the supported spelling and still resolves to
    'full' (pinned by the delegation row); this refuses only the contradiction.
    """
    dream = _Member()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mcp_server, "tortoise_dream", dream)
        got = mcp_server.tortoise_refresh_confidence(scope="dirty", full=True)
    message = str(got.get("error", ""))
    assert "two mechanisms" in message, got
    assert dream.calls == [], f"a contradictory call must not dream: {dream.calls}"


def test_the_dream_knobs_still_reach_the_dream_arm(monkeypatch):
    """The mirror: the refusal must not swallow the knobs on their OWN arm.

    ⚠️ NOT a guard against the `stray` check firing unconditionally — the dream
    scopes RETURN before that block is reached (measured: mutants `if stray:` →
    `if True:` leaves this row GREEN, and reds only the delegation row above).
    What it pins is the dream arm's own kwarg MAPPING: all six knobs reach
    `tortoise_dream` with the values the caller gave, so a future edit that
    forwards, say, `budget=None` or swaps `dirty_only` for `full` reds here
    rather than shipping a silently different dream. Same shape as the
    `credibility`-dropped-on-a-write defect this batch exists to remove.
    """
    dream = _Member()
    monkeypatch.setattr(mcp_server, "tortoise_dream", dream)
    assert mcp_server.tortoise_refresh_confidence(
        scope="full", mode="full", budget=7, max_hops=9,
        dirty_only=False, require_calibration=False) is _Member.SENTINEL
    kwargs = dream.calls[0][1]
    assert kwargs == {"full": True, "dirty_only": False, "max_hops": 9,
                      "require_calibration": False, "mode": "full",
                      "budget": 7}, kwargs


def test_write_question_and_record_decision_delegate_to_their_member(monkeypatch):
    question, approval = _Member(), _Member()
    monkeypatch.setattr(mcp_server, "tortoise_file_decision", question)
    monkeypatch.setattr(mcp_server, "tortoise_file_human_approval", approval)

    assert mcp_server.tortoise_write_question(
        ["a", "b"], ["e1"], 0) is _Member.SENTINEL
    assert question.calls == [((["a", "b"], ["e1"], 0), {})], question.calls

    assert mcp_server.tortoise_record_decision(
        "s1", "o1", ["p1"], "yes") is _Member.SENTINEL
    assert approval.calls == [(("s1", "o1", ["p1"], "yes"), {})], approval.calls


# ── 2. a keyword the chosen arm cannot record is REFUSED ────────────

def test_action_mitigate_CARRIES_credibility(monkeypatch):
    """#12915 review F4, the P1 this file was missing.

    `tortoise_adjust_relationship`'s own docstring advertises
    `reason=/strength=/credibility= → MITIGATE`, and the fields-only spelling
    DOES pass credibility through — but the `action='mitigate'` spelling used to
    hand the call to a handler with no `credibility` parameter and return
    success. A silent drop on a write verb is indistinguishable from "recorded".
    """
    mitigate = _Member()
    monkeypatch.setattr(mcp_server, "tortoise_mitigate_operator", mitigate)
    stub = _stub_sdk(_mitigate_operator=mitigate)
    monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: stub)

    got = mcp_server.tortoise_adjust_relationship(
        "op1", reason="weak", credibility="high", action="mitigate")

    assert got is _Member.SENTINEL
    assert mitigate.calls == [(("op1", "weak", 0.5, "high"), {})], mitigate.calls


def test_action_annotate_REFUSES_the_mitigate_keywords(monkeypatch):
    """The same rule, other direction: `strength`/`reason`/`credibility` are
    not fields of the annotation arm, so they are refused rather than dropped."""
    for keyword in ({"strength": 0.3}, {"reason": "weak"}, {"credibility": "high"}):
        got = mcp_server.tortoise_adjust_relationship(
            "op1", bias=0.1, precision=0.2, consistency=0.3, directness=0.4,
            action="annotate", **keyword)
        message = str(got.get("error", ""))
        assert "ONE arm per call" in message, (keyword, got)
        assert "could not record" in message or "cannot record" in message, (keyword, got)


def test_action_mitigate_REFUSES_the_annotation_dimensions(monkeypatch):
    got = mcp_server.tortoise_adjust_relationship(
        "op1", reason="weak", bias=0.1, action="mitigate")
    assert "cannot record bias" in str(got.get("error", "")), got


def test_the_RETIRED_door_itself_refuses_dimensions_on_its_mitigate_arm():
    """The same one-arm rule, ONE LAYER DOWN — driven on `tortoise_operator_action`.

    ⛔ WHY THIS IS A SEPARATE CASE (#12915 final review, F2-a). The container's
    refusal above is reached by `tortoise_adjust_relationship`, which checks the
    stray dimensions ITSELF before the door is ever called — so deleting the
    door's own `stray` block reddened nothing (measured: the mutant survives,
    `1 passed`). That is the defect shape this batch keeps meeting: a fix the
    body claims, whose removal no row notices (#3442).

    It is not cosmetic, because this door is RETIRED BUT SERVED: the #3883 shim
    calls THIS function, not the container, so a caller arriving with the old
    name and `bias=` got success with the dimension silently discarded.

    Two halves, because a refusal that STILL writes is the defect it refuses:
    the message must name the stray keyword, and the writer must not be reached.
    The stub has `mitigate_operator` ONLY, so a call that fell through would be
    recorded here (and any other name would `AttributeError` rather than pass).
    """
    mitigate = _Member()
    stub = _stub_sdk(_mitigate_operator=mitigate)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mcp_server, "_get_org_sdk", lambda: stub)
        got = mcp_server.tortoise_operator_action(
            "mitigate", id="op1", reason="weak", bias=0.1)
    message = str(got.get("error", ""))
    assert "cannot record bias" in message, got
    assert "ONE arm per call" in message, got
    assert mitigate.calls == [], f"nothing may be written on a refusal: {mitigate.calls}"


# ── 2b. `dry_run` is honoured or REFUSED on EVERY arm — never ignored ──
#
# ⛔ #12915 review F2. `dry_run` on this tool is an assertion about the write:
# `_dry_run_tool_names()` derives its set from the handler's declared `dry_run`
# parameter (this container declares one), and `_arguments_say_preview()` uses
# that membership to record `dry_run=True` as "writes nothing" (#10084). The
# annotation arm used to neither preview nor refuse — it WROTE — so an agent
# asking for a preview got a real graph write plus a write-ack reporting a
# preview. One case per arm, because the arms are separate code paths.

def test_dry_run_is_REFUSED_on_the_annotation_arm(monkeypatch):
    member = _Member()
    monkeypatch.setattr(mcp_server, "tortoise_annotate_operator", member)
    dims = {"bias": 0.1, "precision": 0.2, "consistency": 0.3, "directness": 0.4}

    got = mcp_server.tortoise_update_knowledge("n1", annotation=dims, dry_run=True)

    assert member.calls == [], (
        "a dry_run=True call WROTE the annotation — the harness records that "
        "call as a preview that wrote nothing, so this is a false PASS")
    message = str(got.get("error", ""))
    assert "dry_run" in message and "preview" in message, got


def test_dry_run_is_REFUSED_on_the_property_arm(monkeypatch):
    member = _Member()
    monkeypatch.setattr(mcp_server, "tortoise_update", member)

    got = mcp_server.tortoise_update_knowledge("n1", props={"tier": "gold"},
                                              dry_run=True)

    assert member.calls == [], member.calls
    assert "dry_run" in str(got.get("error", "")), got


def test_dry_run_PREVIEWS_the_temporal_arms_and_writes_nothing(monkeypatch):
    """The other half: the two arms that DO have a preview must reach it."""
    writes, previews = _Member(), _Member()
    monkeypatch.setattr(mcp_server, "_preview_retract_point", previews)
    monkeypatch.setattr(mcp_server, "_preview_invalidate", previews)
    monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: _stub_sdk(
        _retract_point=writes, _invalidate_point=writes))

    for kwargs in ({"invalid_at": "2030-01-01T00:00:00Z"},
                   {"invalid_at": "2030-01-01T00:00:00Z",
                    "corrected_by_id": "n2"}):
        got = mcp_server.tortoise_update_knowledge("n1", dry_run=True, **kwargs)
        assert got is _Member.SENTINEL, got
    assert writes.calls == [], (
        f"a dry_run=True temporal call reached the WRITER: {writes.calls}")
    assert len(previews.calls) == 2, previews.calls


# ── 3. the goalState cache writes through a LIVE name (F1) ──────────

def test_the_goal_state_cache_writes_through_a_live_sdk_method():
    """#12915 review F1: `_write_goal_state_cache` called `sdk.update_entity`,
    which #4282 RETIRED — `TortoiseSDK.__getattr__` raises, so every `met`/
    `unmet` judgement died inside the cache write.

    The stub carries `update_knowledge`, which is the approved replacement, plus
    the `get_entity` read the retirement guard added (round 13) legitimately
    needs. Any call to a name that is not on this object raises, so the
    assertion reddens the moment the retired spelling comes back.

    ⛔ `get_entity` answers a LIVE goal: this test is about the WRITE path, and a
    live goal is what reaches it. The unreadable-goal half (the guard is
    fail-closed there) is pinned by
    `test_13035_goal_ending_consistency.py::
    test_a_goal_whose_status_CANNOT_BE_READ_is_treated_as_RETIRED`.
    """
    from tortoise.goal_judgement import _write_goal_state_cache

    written = []
    stub = _stub_sdk(update_knowledge=lambda node_id, **props: written.append(
        (node_id, props)),
        # `**_kw` because #13093 gave `get_entity` its caller-facing `cache_only`
        # opt-out, and every internal reader passes it; a stub with a fixed
        # positional signature would redden on the call, not on the behaviour.
        get_entity=lambda _id, **_kw: {"id": _id, "status": "live",
                                       "goalState": "active"})

    assert _write_goal_state_cache(stub, "goal-1", "met") == "met"
    assert written == [("goal-1", {"goalState": "met"})], written
    # `unknown` writes NOTHING (:data:`GOAL_STATE_CACHE`) — pinned so the stub
    # cannot pass by ignoring the state word entirely.
    written.clear()
    assert _write_goal_state_cache(stub, "goal-1", "unknown") is None
    assert written == [], written


# ── 4. neither direction is silent ─────────────────────────────────

def test_a_retired_sdk_name_RAISES_naming_its_replacement():
    """#3836(c): retiring a name must not read as "does not exist".

    `__getattr__` is on the INSTANCE, so the hint is reached through an
    uninitialized instance (`object.__new__` skips `__init__`, which would need a
    graph). Called on the CLASS the interpreter answers instead, with a bare
    "has no attribute" — which is the failure this pins against.
    """
    from tortoise.sdk import TortoiseSDK
    from tortoise.sdk_retired import RETIRED_SDK_METHODS as _MAP

    assert _MAP, "no retired SDK names — the pin is vacuous"
    sdk = object.__new__(TortoiseSDK)
    for name, replacement in sorted(_MAP.items()):
        with pytest.raises(AttributeError) as excinfo:
            getattr(sdk, name)
        message = str(excinfo.value)
        assert "retired" in message, (name, message)
        assert f"use TortoiseSDK.{replacement}" in message, (name, message)


def test_a_retired_MCP_tool_still_RESOLVES_and_warns():
    """#3883, the other direction: an MCP caller gets a warning naming the
    replacement, never a bare "tool not found". A container that re-implemented
    its member instead of delegating would still be a behaviour change for this
    caller, which is why the two directions are pinned together.

    Read off the BUILT shim table (`_RETIRED_SHIMS`), not off a tool call: the
    shim is a plain object, so this needs no graph. `tools/surface-guard.py` §5
    executes the `get_tool` resolution path on top of the same declarations.
    """
    from tortoise.tool_registry import RETIRED_TOOL_REGISTRY

    assert RETIRED_TOOL_REGISTRY, "no retired tools declared — the pin is vacuous"
    for entry in RETIRED_TOOL_REGISTRY:
        assert entry.retired_use_instead, entry.name
        assert entry.retired_use_instead != entry.name, entry.name

        shim = mcp_server._RETIRED_SHIMS[entry.name]
        # The warning is the contract, so the served function must BE the shim ...
        assert (shim.fn.__doc__ or "").startswith("RETIRED"), entry.name
        # ... and it must name where the caller should go instead.
        marker = (shim.meta or {}).get("tortoise", {}).get("retired", {})
        assert marker.get("retired") is True, entry.name
        assert marker.get("use_instead") == entry.retired_use_instead, entry.name


# ── 5. the repo-wide gate: no production module reaches a retired name ──

PRODUCTION = REPO / "tortoise"

#: (module, RECEIVER name, retired name) → why that call reaches a NON-SDK object.
#:
#: ⛔ DECLARED, never inferred. The census below is receiver-BLIND by
#: construction: it matches the CALL SHAPE ``X.<retired>(…)`` and it has no way
#: to know what ``X`` is. When a wave retires a name that another class also
#: uses as a METHOD, every legitimate call to that other class reds the gate —
#: measured on `test (g)` at main `09f5c99afd` (run 44593727658):
#: ``tortoise/hosted_api.py:8432: .status``, which is
#: ``EmbeddingModel.status()`` — the EMBEDDER's own classmethod (#13674
#: publishes its load bound on ``/health``), not ``TortoiseSDK.status`` (retired
#: by #11609 in favour of ``graph_overview``). A retired SDK reader is not
#: evidence about a different class's method.
#:
#: Inferring the receiver's class inside the scanner was the alternative and it
#: is the wrong trade: resolution in the scanner is logic a reviewer has to
#: reason about, and every inference rule is a bypass shape. A table is DATA a
#: reviewer can read, and `test_every_declared_non_sdk_receiver_is_a_real_one`
#: below refuses a row whose receiver is not provably (a) imported by
#: `from … import` at every binding of that name — function-local imports are
#: fine, `_embedder_health` imports lazily so the path collects no I/O (#2850) —
#: with every binding resolving to the SAME class and the name never re-bound,
#: and (b) a class that is NOT a TortoiseSDK subclass with the named attribute
#: callable. Re-point that import at the SDK and the pin reds; that is the
#: bypass this table cannot hide.
NON_SDK_RECEIVERS: dict[tuple[str, str, str], str] = {
    ("tortoise/hosted_api.py", "EmbeddingModel", "status"): (
        "#13674 publishes the embedder's load bound on /health; "
        "`EmbeddingModel.status()` (tortoise/embeddings.py) is a CLASSMETHOD on "
        "the embedder. It is not `TortoiseSDK.status`, which #11609 retired in "
        "favour of `graph_overview`."
    ),
}

#: Retired names that COLLIDE with an ordinary method name on the objects this
#: code base holds (the Cypher client). `query` is the whole class today: it is a
#: retired ``TortoiseSDK`` name AND the universal ``<graph>.query(cypher)`` method, so
#: a receiver-blind match reports every Cypher call in the package. MEASURED on
#: `main` at a296e6741e — after the CALL-only narrowing, the census still reported
#: HUNDREDS of offenders across `abuse.py`, `assembly.py`, `backup_sweep.py`,
#: `analyze.py` … and zero SDK call sites, which is the same failure the CALL-only
#: narrowing fixed once (`status`/`provenance`) recurring on a different name.
#:
#: For an ambiguous name the receiver must LOOK like the SDK (`sdk`, `_sdk`). The
#: residual is recorded rather than hidden: a ``TortoiseSDK`` bound to some other
#: variable name calling ``.query()`` is missed by this census — and
#: `TortoiseSDK.__getattr__` still RAISES for it at runtime, which is the pin the
#: census exists to back up, not a substitute for it.
#:
#: This branch is ALSO what exempts the FalkorDB graph handle, and that is measured,
#: not assumed (#13036 review F3a): a ``.g.query(...)`` call names `query`, which is
#: ambiguous, so every one of them is exempted HERE — a separate
#: `GRAPH_HANDLE_RECEIVERS` table keyed on `(path, receiver)` changed no outcome
#: (0 offenders with it, 0 without, repo-wide) and its recorded justification was wrong
#: on both counts: it cited FIVE offenders where the three files it named carry 16
#: (MEASURED at a3655b739b: `why.py` 5, `why_snapshot.py` 3, `__main__.py` 8 — the
#: package holds 927 such calls in all), and it described `tortoise/__main__.py`'s `g`
#: as `FalkorProjection.g` when there `g` is a loop variable over `groups`.
#: ``test_the_census_still_catches_an_ambiguous_name_on_an_sdk_receiver`` pins BOTH
#: directions of this branch so removing the receiver test cannot pass silently.
#:
#: `create_subject` is the second kind of collision, and it is a SURFACE DOOR rather
#: than a Cypher one: `TortoiseSDK.create_subject` is retired (#11609, in favour of
#: `create_entity`), and the SAME name is the published write door on the two seed
#: surfaces — `_OrgSeedSurface.create_subject` (tortoise/hosted_api.py) and
#: `_SdkSeedSurface.create_subject` (tortoise/onboarding/selfhost.py) — each of which
#: delegates to `self._sdk._create_subject`. `tortoise/onboarding/seed.py` reaches it
#: through a `surface` PARAMETER, which is why the path-and-receiver table above
#: cannot carry the row: its pin (`_receiver_binding_violations`) refuses a
#: parameter binding, and rightly so — a parameter can hold anything. The exemption
#: is therefore expressed as the receiver test this branch already is, and the fact
#: that justifies it is MEASURED by
#: `test_the_two_seed_surfaces_publish_a_create_subject_door_that_delegates_privately`,
#: so a surface that stops delegating (or loses the door) reds instead of silently
#: widening the exemption. Residual, recorded not hidden: a `TortoiseSDK` bound to a
#: name other than `sdk`/`_sdk` calling `.create_subject()` is missed here — exactly
#: as `query` is — and `TortoiseSDK.__getattr__` still RAISES for it at runtime.
AMBIGUOUS_RETIRED_NAMES: frozenset[str] = frozenset(
    {"query", "list_graphs", "link_source_to_entity", "create_subject"})
SDK_RECEIVER_NAMES: frozenset[str] = frozenset({"sdk", "_sdk"})


def _receiver_name(receiver: ast.expr) -> str | None:
    """The receiver's terminal NAME, for a bare `g` or an attribute chain `proj.g`."""
    if isinstance(receiver, ast.Name):
        return receiver.id
    if isinstance(receiver, ast.Attribute):
        return receiver.attr
    return None


def _attribute_names(tree: ast.AST,
                     rel_path: str | None = None) -> list[tuple[int, str]]:
    """Every CALL of a retired SDK name: ``<receiver>.<retired>(…)``.

    ⛔ CALL, not every attribute read (#12951 CI red). This collected every
    ``ast.Attribute`` whose NAME was retired, which is receiver-blind: the
    moment a wave retires a name that is also an ordinary field name, the gate
    floods with data-model reads that reach no SDK at all. Measured when #11609
    Phase 2 retired ``status`` and ``provenance`` — 86 offenders, 86 of them
    plain reads on a row / record / exception object (``_held.status``,
    ``r.provenance``, ``outcome.status`` …) and ZERO SDK call sites, so the red
    said nothing about the defect this gate exists to catch while blocking
    every merge until someone narrowed or deleted it.

    The defect it exists to catch IS a call: ``goal_judgement`` kept
    ``sdk.update_entity(…)`` and took every goal judgement down with it. A call
    site is an ``ast.Call`` whose ``func`` IS the retired attribute, so this
    keeps detection exact in the direction that matters —
    ``test_the_census_still_catches_an_sdk_call_site`` below proves both
    directions on synthetic trees rather than leaving the narrowing as an
    unverified claim.

    Two exemptions exist, and they are NOT the same shape (#14045 review, finding 5c):

    1. ``NON_SDK_RECEIVERS`` — a non-SDK class that shares a retired name as a method. Its
       rows are skipped only for that module, only for that receiver NAME, and passing no
       path exempts nothing, so the synthetic-tree pins below keep their full strength.
    2. ``AMBIGUOUS_RETIRED_NAMES`` — the retired names that also name an ordinary method on
       another class this code base holds (the Cypher client's `query`/`list_graphs`/
       `link_source_to_entity`, and the two seed surfaces' `create_subject`). This skip is
       PATH-INDEPENDENT on purpose (it keys on the RECEIVER alone: an
       ambiguous name counts only on an ``sdk``/``_sdk`` receiver), so it DOES apply when
       ``rel_path`` is None. An earlier version of this paragraph claimed "passing no path
       exempts nothing", which was false from the moment this branch existed:
       ``_attribute_names(ast.parse("def f(x):\n return x.query(1)\n"))`` answers ``[]``, so
       the no-path pins below cannot reach it. Its pin is the parameterised
       ``test_the_census_still_catches_an_ambiguous_name_on_an_sdk_receiver`` — which asserts
       both directions with NO path passed, which is exactly how it pins the path-independence.
    """
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in RETIRED_SDK_METHODS):
            continue
        receiver = node.func.value
        if rel_path is not None and isinstance(receiver, ast.Name) \
                and (rel_path, receiver.id, node.func.attr) in NON_SDK_RECEIVERS:
            continue
        if node.func.attr in AMBIGUOUS_RETIRED_NAMES:
            name = _receiver_name(receiver)
            if name not in SDK_RECEIVER_NAMES:
                continue
        out.append((node.func.lineno, node.func.attr))
    return out


def test_the_census_still_catches_an_sdk_call_site():
    """The narrowing above is name-blind, so prove it is not KIND-blind.

    A call of a retired name is still caught; an ordinary attribute READ of the
    same name is not. Without this pin the narrowing is an assertion about
    behaviour that no test measures — the exact shape of change that turns a
    gate into decoration.
    """
    retired = sorted(RETIRED_SDK_METHODS)
    assert retired, "no retired names declared — the pin is vacuous"
    name = retired[0]
    caught = ast.parse(f"def f(sdk):\n    return sdk.{name}(1)\n")
    missed = ast.parse(f"def f(row):\n    return row.{name}\n")
    assert _attribute_names(caught) == [(2, name)], (
        "a CALL of a retired name must still be caught — this is the "
        "`goal_judgement`/`sdk.update_entity` defect the gate exists for")
    assert _attribute_names(missed) == [], (
        "a plain attribute READ of a name that happens to be retired must NOT "
        "be flagged — it reaches no SDK")

    # ── RECORDED RESIDUAL (#12951 review Q4) ───────────────────────────────
    # The census is CALL-SHAPE scoped, so an ALIASED call — ``fn = sdk.<retired>;
    # fn(1)`` — carries no ``ast.Attribute`` at the call site and is NOT flagged.
    # That is SAFE rather than merely measured, and the reason is a different pin:
    # ``TortoiseSDK.__getattr__`` (sdk.py) raises ``AttributeError(retirement_hint
    # (name))`` on the ATTRIBUTE ACCESS itself, so the alias is never formed — the
    # first statement below dies before a callee exists. Both shapes are asserted
    # here so the residual is recorded where the narrowing is, instead of being
    # claimed away above. (The raise itself is pinned for EVERY retired name by
    # `test_a_retired_sdk_name_RAISES_naming_its_replacement`; the single-name
    # check here ties that pin to the alias claim.)
    from tortoise.sdk import TortoiseSDK
    aliased = ast.parse(f"def f(sdk):\n    fn = sdk.{name}\n    return fn(1)\n")
    assert _attribute_names(aliased) == [], (
        "the census is call-shape scoped: an aliased call is not visible to it — "
        "if this ever starts matching, update the recorded residual above")
    alias_builtin = ast.parse(
        f"def f(sdk):\n    return getattr(sdk, {name!r})(1)\n")
    assert _attribute_names(alias_builtin) == [], (
        "a getattr() dynamic call is not visible to the census either")

    bare = object.__new__(TortoiseSDK)
    with pytest.raises(AttributeError):
        getattr(bare, name)


@pytest.mark.parametrize("name", sorted(AMBIGUOUS_RETIRED_NAMES))
def test_the_census_still_catches_an_ambiguous_name_on_an_sdk_receiver(name):
    """The ambiguous branch's CATCH direction is a pin, not a claim (#13036 review F3b).

    The retired names that also name an ordinary method on another class this code base
    holds — the Cypher client's `query`/`list_graphs`/`link_source_to_entity` and the
    two seed surfaces' `create_subject` — are exempted on every non-SDK receiver. That
    branch carries **1561 of 1561** live hits in `tortoise/` (MEASURED at `e0accb8fe` by
    counting, over every `*.py` under `tortoise/` — `Path("tortoise").rglob("*.py")` —
    each `ast.Call` whose `func` is an `ast.Attribute` with an attr in the set and whose
    receiver's terminal name is not `sdk`/`_sdk`: 1543 `query`, 11 `list_graphs`,
    5 `link_source_to_entity`, 2 `create_subject`; an earlier cut of this sentence said
    "repo-wide" over 1558, which was this same `tortoise/`-only count at an older head —
    the figures ROT, so re-run that walk rather than quoting them) — so a mutant that
    forgot the receiver test
    (`if node.func.attr in AMBIGUOUS_RETIRED_NAMES: continue`) would forgive EVERY ONE
    of them and leave the whole file green: the only catch-direction pin that existed
    seeded `sorted(RETIRED_SDK_METHODS)[0]` = a NON-ambiguous name, so nothing measured
    this.

    Both directions are asserted here, plus the graph-handle shape the census must keep
    exempting through this branch (the branch that used to be labelled with a separate
    `GRAPH_HANDLE_RECEIVERS` table).
    """
    for receiver in sorted(SDK_RECEIVER_NAMES):
        assert _attribute_names(
            ast.parse(f"def f({receiver}):\n    return {receiver}.{name}(1)\n")) \
            == [(2, name)], (
                f"`{receiver}.{name}()` is an SDK call site of a retired name and MUST be "
                "caught — dropping the receiver test would forgive it silently")
    # a non-SDK receiver is exempt — that is the point of the branch, not a hole
    for receiver in ("row", "graph", "self"):
        assert _attribute_names(
            ast.parse(f"def f({receiver}):\n    return {receiver}.{name}(1)\n")) == [], (
                f"`{receiver}.{name}()` on a non-SDK receiver must stay exempt, or the "
                "census floods with the Cypher client's own methods (0 measured SDK hits)")
    # ... and the ATTRIBUTE spelling of an SDK receiver is caught too: `_receiver_name`
    # reads the terminal name of `self.sdk`, which is the shape the package actually uses.
    assert _attribute_names(
        ast.parse(f"def f(self):\n    return self.sdk.{name}(1)\n")) == [(2, name)], (
            "`self.sdk.<retired>()` is an SDK call site — the terminal receiver name is "
            "what the branch tests, so this shape must not be exempted with `self`")
    # the graph-handle shape: `g` / `proj.g` are the 16 measured `.g.query(...)` sites
    for source in (f"def f(g):\n    return g.{name}(1)\n",
                   f"def f(proj):\n    return proj.g.{name}(1)\n"):
        assert _attribute_names(ast.parse(source)) == [], (
            "a graph-handle receiver must stay exempt THROUGH THE AMBIGUOUS BRANCH — "
            "the path-scoped `GRAPH_HANDLE_RECEIVERS` table that exempted it separately "
            "changed no outcome and was removed (#13036 review F3a)")


#: Statements that OPEN A NEW SCOPE. A node inside one of these does not execute
#: as the enclosing function's own body, so `ast.walk(door)` — which answers "does
#: this text MENTION the call" — is not the question the door's delegation pin asks.
_NESTED_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _own_scope_nodes(root: ast.AST) -> list[ast.AST]:
    """Every node EXECUTED IN `root`'s own scope — nested scopes are not entered.

    `ast.walk` descends into a `def`/`class`/`lambda` body, which is a different
    question from "does this scope run it": a helper defined inside the door, or the
    same call inside a lambda, never executes as the door's delegation. MEASURED at
    `386e8e3524`: a door rewritten to

        def _never_runs():
            return self._sdk._create_subject(name, **props)
        raise RuntimeError('door broken')

    left the pin GREEN (`2 passed`), because the only delegation it found was inside a
    function nobody calls. That is a FALSE PASS on a gate whose whole justification is
    that the fact behind the exemption is measured rather than asserted.
    """
    out: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(root))
    while stack:
        node = stack.pop()
        if isinstance(node, _NESTED_SCOPE_NODES):
            continue
        out.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return out


def _contains_own_scope(root: ast.AST, wanted: list[ast.AST]) -> bool:
    """Identity test: is one of `wanted` a node in `root`'s OWN scope?

    `root` ITSELF counts — the delegated call is normally `root` (`return
    self._sdk._create_subject(...)`), and `_own_scope_nodes` starts at its children.
    """
    wanted_ids = {id(node) for node in wanted}
    if id(root) in wanted_ids:
        return True
    return any(id(node) in wanted_ids for node in _own_scope_nodes(root))


def _door_binding_problems(cls: type, name: str = "create_subject") -> list[str]:
    """Every reason `cls`'s RUNTIME `name` is not a door this pin can read.

    ⛔ THE RUNTIME BINDING, NOT AN AST READING OF THE CLASS BODY. Six rounds of review
    each found a way for a syntactic scan of `cls.body` to stay green over a door that is
    not the one the class ends up with, and the last round named why it can never be
    repaired syntactically: a class body is EXECUTED. Measured bypasses of the AST
    readers that were deleted at this head — pin GREEN each time, on the real class:

      * a shadowing `def` — under `if True:`, a `match` case, `try`/`finally`, `except*`;
      * a binder that is not a `def` — `except E as create_subject` (PEP 3110 deletes the
        target at handler exit), `del (x, create_subject)`, `case (create_subject,):`,
        `case [*create_subject]:`, `case {"k": _, **create_subject}:`, `create_subject =
        ...`, a walrus in a default or decorator, a class named `create_subject`;
      * a MASKING DECORATOR — one `@` line replaces the function the class binds while
        the scanned `def` body still reads `self._sdk._create_subject(...)`;
      * DEAD CODE — `if False:` holding a delegating `def` after the live door.

    `cls.__dict__[name]` is what `surface.create_subject(...)` resolves to, so reading it
    is not a proxy for the question — it IS the question, and no class-body form can
    reach it. The three refusals below are deliberately narrow so the pin cannot be
    satisfied by something the seed cannot use.
    """
    door = cls.__dict__.get(name)
    if door is None:
        return [f"`{name}` is not bound on the class — the seed's own call would raise "
                "`AttributeError`, so the exemption has outlived its reason"]
    if type(door) is not types.FunctionType:
        return [f"`{name}` is a {type(door).__name__}, not a plain function — a "
                "decorator or an assignment produced the runtime door, so what the seed "
                "calls is not the `def` this pin can read, and the exemption is "
                "unjustified until that wrapper is reviewed as a change to the write "
                "path"]
    if door.__qualname__ != f"{cls.__name__}.{name}":
        return [f"`{name}` is {door.__qualname__!r} — a DECORATOR replaced the door "
                "(the binding is a closure or a function from elsewhere), so the body "
                "this pin would inspect is not the body that runs"]
    if hasattr(door, "__wrapped__"):
        return [f"`{name}` carries `__wrapped__` — a decorator built on "
                "`functools.wraps` wrapped the door. It is refused rather than "
                "unwrapped on purpose: `inspect.getsource` follows `__wrapped__` "
                "(CPython's `getsourcelines` unwraps before locating the file), so a "
                "`functools.wraps` mask would otherwise read the PRE-decorator body and "
                "stay green while the seed calls the wrapper"]
    return []


def _seed_door_problems(door_source: str,
                        delegated: str = "_create_subject") -> list[str]:
    """Every reason a door's OWN SOURCE fails to be the private passthrough.

    Pure over source text — the shipped pin feeds it the source of the RUNTIME function
    read off `door.__code__`, and the non-vacuity arms feed it mutants of that same
    text — so the predicate is the same code in both directions and cannot drift from
    what ships. Text alone can still be wrong in two ways, and both are refused:

    1. the delegation must EXECUTE in the door's own scope — a call nested inside a
       helper or a lambda the door never runs is not the door's delegation;
    2. it must be what the CALLER receives — it has to reach a `return`, directly or
       through a name assigned to it in the same scope, so
       `self._sdk._create_subject(...)` followed by `return None` is not a passthrough.

    Both shapes were MEASURED GREEN at `386e8e3524`, on the real `_SdkSeedSurface`
    (`2 passed` / `1 passed`), because the predicate then matched the call anywhere in
    the door's text.
    """
    tree = ast.parse(textwrap.dedent(door_source))
    fn = next((node for node in tree.body
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
    if fn is None:
        return ["the runtime door's own source is not a function definition — there is "
                "nothing here this pin can read for delegation"]

    own = _own_scope_nodes(fn)
    delegations = [
        node for node in own
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == delegated
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "_sdk"]
    if not delegations:
        return [f"does not call `self._sdk.{delegated}()` in its OWN scope — a call "
                "nested inside a function/lambda the door never runs is not the "
                "surface's passthrough, so the exemption that rests on it is "
                "unjustified"]

    # ⛔ ... and the delegation has to be what the CALLER receives. MEASURED at
    # `386e8e3524`: `self._sdk._create_subject(...)` followed by `return None` — the
    # call is in the door's own scope but its result is thrown away — stayed GREEN.
    carried: set[str] = set()
    for node in own:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if node.value is not None and _contains_own_scope(node.value, delegations):
            carried |= {t.id for t in targets if isinstance(t, ast.Name)}

    for node in own:
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        if _contains_own_scope(node.value, delegations):
            return []
        if any(isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Load)
               and inner.id in carried for inner in _own_scope_nodes(node)):
            return []
    return [f"calls `self._sdk.{delegated}()` but never RETURNS it — the door's "
            "caller does not receive the subject, so the door is not the "
            "passthrough the exemption claims"]


def _door_problems(cls: type, name: str = "create_subject",
                   delegated: str = "_create_subject") -> list[str]:
    """The whole per-door measurement: the RUNTIME binding, then the source it runs.

    The source is read from `door.__code__`, never from `door`: the code object has no
    `__wrapped__` for `inspect` to follow, so a wrapper cannot hand the pin the
    pre-decorator body. MEASURED: for a `functools.wraps`-masked door,
    `'_create_subject' in inspect.getsource(door)` is True while
    `inspect.getsource(door.__code__)` does not contain it — so either predicate alone
    refuses the wrapper, and both are present.
    """
    problems = _door_binding_problems(cls, name)
    if problems:
        return problems
    return _seed_door_problems(inspect.getsource(cls.__dict__[name].__code__),
                               delegated)


def test_the_two_seed_surfaces_publish_a_create_subject_door_that_delegates_privately():
    """The FACT behind `create_subject`'s presence in the ambiguous set, MEASURED.

    `create_subject` is a retired `TortoiseSDK` name (#11609, in favour of
    `create_entity`), AND the published write door on the two seed surfaces. That second
    half is the whole justification for exempting a `surface` receiver, so it is checked
    here rather than asserted in prose: `_OrgSeedSurface` (tortoise/hosted_api.py) and
    `_SdkSeedSurface` (tortoise/onboarding/selfhost.py) each have to expose a plain
    `create_subject` FUNCTION that delegates to `self._sdk._create_subject`.

    ⛔ THE RUNTIME BINDING, NOT AN AST READING OF THE CLASS BODY. Three review rounds
    each found a way for a syntactic scan to stay green over a door that is not the one
    the class ends up with, and the third round named the reason it can never be fixed
    syntactically: Python's class body is EXECUTED. Measured bypasses of the AST versions
    (all on the real class, pin GREEN each time):

      * a shadowing `def` — under `if True:`, a `match` case, `try`/`finally`, `except*`;
      * a binder that is not a `def` — `except E as create_subject` (PEP 3110 deletes the
        target at handler exit), `del (x, create_subject)`, `case (create_subject,):`,
        `create_subject = ...`, a walrus in a default or decorator, a class named
        `create_subject`;
      * a MASKING DECORATOR — `@_mask` replaces the tuple the class binds while the
        scanned body still delegates, so one `@` line defeated the whole pin;
      * DEAD CODE — `if False: def create_subject(...)` shadowing a non-delegating live
        door (no reachability analysis can be trusted for this, and none is attempted).

    So the pin reads `cls.__dict__['create_subject']` — exactly what the seed's
    `surface.create_subject(...)` will call — and then requires:

    1. the attribute is a plain `types.FunctionType`;
    2. its `__qualname__` is `"<Class>.create_subject"`, i.e. the class body's own
       function and not a closure a decorator returned;
    3. it carries no `__wrapped__` — a `functools.wraps` mask is refused, not unwrapped;
    4. the RUNTIME function's own source delegates to `self._sdk._create_subject(...)`
       in its OWN scope, and RETURNS it to the caller.

    A wrapper is refused rather than unwrapped on purpose: an `@functools.cache` or an
    instrumentation decorator around a write door is a change to the write path that
    nobody has reviewed as such, and `inspect`ing through it would hide that.

    ⛔ The delegation is measured in the DOOR'S OWN SCOPE and must be RETURNED, not
    merely mentioned somewhere in its text — both shapes were measured GREEN at
    `386e8e3524` and are pinned red by the test below.
    """
    from tortoise.hosted_api import _OrgSeedSurface
    from tortoise.onboarding.selfhost import _SdkSeedSurface

    for cls in (_OrgSeedSurface, _SdkSeedSurface):
        problems = _door_problems(cls)
        assert not problems, (
            f"{cls.__module__}.{cls.__name__}.create_subject no longer satisfies the "
            f"pin the `create_subject` exemption rests on: " + "; ".join(problems))


def test_the_delegation_pin_refuses_a_DISCARDED_or_NESTED_delegation():
    """Non-vacuity of the pin above, measured on mutants of the REAL door's source.

    Both arms were GREEN at `386e8e3524` — the predicate matched the call anywhere in
    the door's text — so both are false PASSes a mutant PR could exploit to keep the
    exemption while the door it rests on is broken:

    1. **nested** — the call lives in a helper the door never runs, or a lambda.
    2. **discarded** — the door calls the private method and returns something else.

    The mutants are cut out of the source the pin ITSELF reads (`inspect.getsource` of
    the door's code object) by that source's own AST line span — not a text anchor — so
    they track the door if it is ever reformatted. The control arm proves the unmutated
    source is what the predicate accepts, so a predicate that refuses everything cannot
    pass this test.
    """
    from tortoise.onboarding.selfhost import _SdkSeedSurface

    delegated = "_create_subject"
    door = _SdkSeedSurface.__dict__["create_subject"]
    source = textwrap.dedent(inspect.getsource(door.__code__))
    assert _seed_door_problems(source, delegated) == [], (
        "the control arm: the door as it stands must satisfy the pin, or the two "
        "mutants below prove nothing")

    fn = next(node for node in ast.parse(source).body
              if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
    body_first, body_end = fn.body[0].lineno - 1, fn.end_lineno
    indent = " " * fn.body[0].col_offset
    lines = source.splitlines(keepends=True)
    head, tail = lines[:body_first], lines[body_end:]

    def mutant(body: str) -> str:
        return "".join(head) + body + "".join(tail)

    nested = mutant(
        f"{indent}def _never_runs():\n"
        f"{indent}    return self._sdk.{delegated}(name, **props)\n"
        f"{indent}raise RuntimeError('door broken')\n")
    problems = _seed_door_problems(nested, delegated)
    assert problems and "OWN scope" in problems[0], (
        "a delegation that lives only inside a helper the door never runs is not the "
        "door's delegation — MEASURED green at 386e8e3524, so this arm is the pin on "
        "the fix", problems)

    discarded = mutant(
        f"{indent}self._sdk.{delegated}(name, **props)\n"
        f"{indent}return None\n")
    problems = _seed_door_problems(discarded, delegated)
    assert problems and "never RETURNS" in problems[0], (
        "a door that calls the private method and throws the result away gives its "
        "caller no subject — MEASURED green at 386e8e3524", problems)

    carried = mutant(
        f"{indent}created = self._sdk.{delegated}(name, **props)\n"
        f"{indent}return created\n")
    assert _seed_door_problems(carried, delegated) == [], (
        "the assign-then-return shape IS a passthrough and must not be over-blocked "
        "by the tightened pin")


#: A delegating `def create_subject` in the class body, followed by a class-body form
#: that changes what `C.__dict__["create_subject"]` ends up being. Every one of these
#: was measured FAIL-OPEN against the AST reader this pin shipped with — the reader
#: answered the `def`, so the pin stayed GREEN while the class no longer bound it. They
#: are the last review round's own table, kept as data so the surface is enumerable
#: rather than whack-a-mole.
_DOOR_CLASS_BODY = (
    "class C:\n"
    "    def create_subject(self, name):\n"
    "        return self._sdk._create_subject(name)\n")


def _class_body_variant(tail: str) -> str:
    return _DOOR_CLASS_BODY + tail


_CLASS_BODY_BINDING_BYPASSES = {
    "del": _class_body_variant("    del create_subject\n"),
    "del-tuple": _class_body_variant("    _x = 1\n    del (_x, create_subject)\n"),
    "del-list": _class_body_variant("    _x = 1\n    del [_x, create_subject]\n"),
    "except-as": _class_body_variant(
        "    try:\n"
        "        raise ValueError()\n"
        "    except ValueError as create_subject:\n"
        "        pass\n"),
    "exceptstar-as": _class_body_variant(
        "    try:\n"
        "        raise ExceptionGroup('g', [ValueError()])\n"
        "    except* ValueError as create_subject:\n"
        "        pass\n"),
    "match-tuple": _class_body_variant(
        "    match (1,):\n"
        "        case (create_subject,):\n"
        "            pass\n"),
    "match-star": _class_body_variant(
        "    match [1, 2]:\n"
        "        case [*create_subject]:\n"
        "            pass\n"),
    "match-mapping": _class_body_variant(
        '    match {"k": 1}:\n'
        '        case {"k": _, **create_subject}:\n'
        "            pass\n"),
    "assign": _class_body_variant("    create_subject = 1\n"),
    "walrus-in-default": _class_body_variant(
        "    def other(self, x=(create_subject := 2)):\n"
        "        pass\n"),
    "walrus-in-decorator": _class_body_variant(
        "    @(create_subject := staticmethod)\n"
        "    def other(self):\n"
        "        pass\n"),
    "class-named": _class_body_variant("    class create_subject:\n        pass\n"),
}


@pytest.mark.parametrize("label", sorted(_CLASS_BODY_BINDING_BYPASSES))
def test_the_pin_reads_the_RUNTIME_binding_so_a_class_body_form_cannot_hide_it(label):
    """The last review round's `_effective_class_binding` fail-open table, closed.

    Twelve class-body forms make CPython end the class with something other than the
    delegating `def` the source still shows: `del` (bare, tuple, list), `except E as` and
    `except* E as` (PEP 3110 DELETES the handler target at exit), the three `match`
    captures (`MatchAs`/`MatchStar`/`MatchMapping.rest`, whose names are plain `str`
    fields a collector keyed on `ast.Name` never sees), a plain assignment, a walrus in
    a nested `def`'s default, a walrus in a decorator expression, and a nested `class` of
    the same name. Any of them kept the `create_subject` exemption justified while the
    seed's own call would raise `AttributeError` or reach a non-function.

    ⛔ TEN of the twelve were measured FAIL-OPEN on the deleted reader (it answered the
    `def`); `assign` and `class-named` were already refused by it, and are kept as arms
    because the runtime pin must refuse them too. Excluded on purpose: a walrus in a
    class BASE binds in the ENCLOSING scope, so CPython keeps the door — asserting a
    refusal there would be false.

    The pin no longer parses the class body at all, so the form cannot reach it: the
    verdict comes from `C.__dict__`, which is CPython's own answer. Three assertions per
    arm — CPython really does end with something that is not the delegating `def` (so the
    fixture is not vacuous), the source DOES still contain that `def` (so a class-body
    reader would have answered it), and the pin REFUSES the class. Reverting to any
    class-body reader reds this test instead of silently widening the exemption.
    """
    source = _CLASS_BODY_BINDING_BYPASSES[label]
    namespace: dict[str, object] = {}
    exec(compile(source, f"<door-fixture:{label}>", "exec"), namespace)
    cls = namespace["C"]
    assert isinstance(cls, type), label

    assert "def create_subject(self, name):" in source, (
        "the fixture must still CONTAIN a delegating `def` — that is what made this a "
        "bypass of a class-body reader rather than a missing door")
    assert not isinstance(cls.__dict__.get("create_subject"), types.FunctionType), (
        f"vacuous arm: `{label}` did NOT change what the class binds, so it never was "
        "a bypass of anything")

    problems = _door_binding_problems(cls)
    assert problems, (
        f"the `{label}` form leaves `C.__dict__['create_subject']` as "
        f"{type(cls.__dict__.get('create_subject')).__name__}"
        f"{'' if 'create_subject' in cls.__dict__ else ' (absent)'}, while the class "
        "body still shows a delegating `def` — the pin MUST refuse this; a GREEN here "
        "means the verdict came from the class body again")


def test_the_door_pin_refuses_a_MASKED_rebound_or_absent_door():
    """Non-vacuity of the BINDING arms, one synthetic class per refusal.

    Each arm keeps a delegating `def create_subject` in the source while what
    `surface.create_subject(...)` resolves to is a different object — the ways a
    decorator or an assignment can defeat a body-only reading. All were GREEN against the
    AST reader deleted at this head, and two were measured against the REAL
    `_SdkSeedSurface` (`@_mask` returning a non-delegating closure, and a
    `functools.wraps` mask that forges the door's `__qualname__`).

    The control arm is a plain door, so a predicate that refuses everything cannot pass.
    """

    def build(source: str) -> type:
        namespace: dict[str, object] = {}
        exec(compile(source, "<door-binding-fixture>", "exec"), namespace)
        return namespace["C"]  # type: ignore[return-value]

    plain = build(
        "class C:\n"
        "    def create_subject(self, name):\n"
        "        return self._sdk._create_subject(name)\n")
    assert _door_binding_problems(plain) == [], (
        "the control arm: a plain delegating door must satisfy the binding checks, or "
        "the refusals below prove nothing")

    wraps_masked = build(
        "import functools\n"
        "def _mask(fn):\n"
        "    @functools.wraps(fn)\n"
        "    def replacement(*args, **kwargs):\n"
        "        return object()\n"
        "    return replacement\n"
        "class C:\n"
        "    @_mask\n"
        "    def create_subject(self, name):\n"
        "        return self._sdk._create_subject(name)\n")
    problems = _door_binding_problems(wraps_masked)
    assert problems and "__wrapped__" in problems[0], (
        "a `functools.wraps` mask forges `__qualname__` and hides the wrapper behind "
        "`__wrapped__`; the pin must refuse it rather than unwrap it", problems)

    closure_masked = build(
        "def _mask(fn):\n"
        "    def replacement(*args, **kwargs):\n"
        "        return object()\n"
        "    return replacement\n"
        "class C:\n"
        "    @_mask\n"
        "    def create_subject(self, name):\n"
        "        return self._sdk._create_subject(name)\n")
    problems = _door_binding_problems(closure_masked)
    assert problems and "DECORATOR" in problems[0], (
        "a decorator returning a closure replaces the class binding — the `def` in the "
        "source is not what runs", problems)

    rebound = build(
        "def _other(self, name):\n"
        "    return 1\n"
        "class C:\n"
        "    def create_subject(self, name):\n"
        "        return self._sdk._create_subject(name)\n"
        "    create_subject = _other\n")
    problems = _door_binding_problems(rebound)
    assert problems and "DECORATOR" in problems[0], (
        "rebinding the name to a function defined outside the class body is the same "
        "defeat as a decorator — the surviving `def` is not the binding", problems)

    cached = build(
        "import functools\n"
        "class C:\n"
        "    @functools.cache\n"
        "    def create_subject(self, name):\n"
        "        return self._sdk._create_subject(name)\n")
    problems = _door_binding_problems(cached)
    assert problems and "not a plain function" in problems[0], (
        "`@functools.cache` around a WRITE door is a change nobody reviewed as such; "
        "it must be refused, not inspected through", problems)

    absent = build("class C:\n    pass\n")
    problems = _door_binding_problems(absent)
    assert problems and "not bound" in problems[0], (
        "a class with no door at all must be refused: the seed's call would raise "
        "`AttributeError`", problems)


def test_the_pin_never_reads_the_class_body_so_dead_code_cannot_satisfy_it():
    """The dead-code arm: a delegating `def` under `if False:` is not the live door.

    MEASURED against the AST reader at `386e8e3524`: with the live door rewritten to
    `return None` and an `if False:` block holding the delegating `def` after it, the
    reader's "last binder wins" rule answered the DEAD def and the pin stayed GREEN.

    The pin now feeds the predicate the RUNTIME function's own source, so the dead block
    is never consulted at all.
    """
    source = (
        "class C:\n"
        "    def create_subject(self, name):\n"
        "        return None\n"
        "    if False:\n"
        "        def create_subject(self, name):\n"
        "            return self._sdk._create_subject(name)\n")
    assert "self._sdk._create_subject(" in source, (
        "the fixture must contain a delegating `def` — that is what made the dead-code "
        "shape a bypass of a class-body reader")

    namespace: dict[str, object] = {}
    exec(compile(source, "<door-fixture:dead-code>", "exec"), namespace)
    live = namespace["C"].__dict__["create_subject"]  # type: ignore[attr-defined]

    assert "_create_subject" not in live.__code__.co_names, (
        "the LIVE door must not reference the private method at all, or the fixture "
        "does not model the measured shape")
    live_body = (
        "def create_subject(self, name):\n"
        "    return None\n")
    problems = _seed_door_problems(live_body, "_create_subject")
    assert problems, (
        "the live door returns `None` and delegates nowhere, so the pin must refuse it "
        "— a GREEN means the dead `if False:` block was consulted", problems)


def test_no_production_module_reaches_a_retired_sdk_name():
    """#12915 review F1/F3. A retired name is only retired if nothing can still
    CALL it: `goal_judgement` kept `sdk.update_entity` and took every goal
    judgement down with it.

    AST, not grep: a docstring that *names* a retired method (explaining the
    rename) is not a call site, and this file's own prose proves a text search
    cannot tell the two apart.
    """
    offenders = []
    for path in sorted(PRODUCTION.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - a broken file is another gate's job
            continue
        for lineno, attr in _attribute_names(tree, str(path.relative_to(REPO))):
            offenders.append(f"{path.relative_to(REPO)}:{lineno}: .{attr}")
    assert not offenders, (
        "production code reaches RETIRED SDK names — a retired name RAISES at "
        "call time (#4282/#3836(c)), so each of these is a live failure:\n  "
        + "\n  ".join(offenders))


def test_the_census_exemption_is_path_and_receiver_scoped():
    """The NON_SDK_RECEIVERS exemption cannot be widened by accident.

    Three properties, each of which is a bypass shape if it stops holding:

    1. **Path-scoped** — the SAME call shape in any other module is still an
       offender. A table keyed only on (receiver, name) would exempt
       ``EmbeddingModel.status()`` everywhere, including in a module where the
       exemption was never justified.
    2. **Receiver-scoped** — the same file calling ``sdk.status()`` is still an
       offender; the row exempts ONE receiver NAME, never the module.
    3. **Off by default** — the census called with no path (the synthetic-tree
       pins above) exempts nothing, so their strength is unchanged.
    """
    (rel, receiver, retired), reason = next(
        (((k[0], k[1], k[2]), v) for k, v in NON_SDK_RECEIVERS.items()),
        (None, None))
    assert rel is not None, (
        "no declared receivers — this pin would measure nothing (vacuity is a "
        "red, not a StopIteration)")
    assert reason and reason.strip(), "a declared exemption needs its reason"
    source = f"def f():\n    return {receiver}.{retired}()\n"

    assert _attribute_names(ast.parse(source), rel) == [], (
        "the declared non-SDK receiver must be exempt in ITS module")
    assert _attribute_names(ast.parse(source), "tortoise/somewhere_else.py") == \
        [(2, retired)], (
            "the exemption must NOT travel to another module")
    assert _attribute_names(ast.parse(source)) == [(2, retired)], (
        "with no path the census must exempt nothing — the synthetic pins above "
        "depend on it")
    assert _attribute_names(
        ast.parse(f"def f(sdk):\n    return sdk.{retired}()\n"), rel) == [(2, retired)], (
        "the exemption is keyed on the RECEIVER NAME — an SDK receiver in the "
        "same file must still be caught")


def _receiver_binding_violations(source: str, receiver: str) -> list[str]:
    """Every binding of ``receiver`` that could carry a NON-imported object.

    ⛔ WHY NOT `ast.Name`-Store ALONE (#13863 review, P1 — a REAL fail-open).
    The first cut collected only ``ast.Name`` nodes in Store context, so a
    **parameter**, a **lambda parameter**, an ``except … as``, an ``import x as``
    or a star import could each bind the exempted NAME to a live ``TortoiseSDK``
    handle while every pin stayed green. Reproduced in a scratch tree:
    ``def _probe(EmbeddingModel): return EmbeddingModel.status()`` called with
    ``_org_sdk(org_id)`` → **5 passed**, i.e. the gate certified a live
    retired-name call site as clean. A row exempts a NAME, so every way that
    name can come to mean something else has to be visible here.

    Returns human-readable violations; empty means every binding of the name is
    a ``from … import`` (checked separately against the class it names).
    """
    out: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.arg) and node.arg == receiver:
            out.append(f"parameter at line {node.lineno}")
        elif isinstance(node, ast.ExceptHandler) and node.name == receiver:
            out.append(f"`except … as {receiver}` at line {node.lineno}")
        elif isinstance(node, ast.Name) and node.id == receiver and \
                isinstance(node.ctx, (ast.Store, ast.Del)):
            # assignment / for target / with-as / comprehension / walrus / del
            out.append(f"assignment or `del` at line {node.lineno}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if (alias.asname or alias.name.split(".")[0]) == receiver:
                    out.append(f"`import … as {receiver}` at line {node.lineno}")
        elif isinstance(node, (ast.Global, ast.Nonlocal)) and receiver in node.names:
            kind = "global" if isinstance(node, ast.Global) else "nonlocal"
            out.append(f"`{kind} {receiver}` at line {node.lineno}")
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == receiver:
            out.append(f"match capture at line {node.lineno}")
        elif isinstance(node, ast.MatchMapping) and node.rest == receiver:
            out.append(f"mapping-pattern `**{receiver}` at line {node.lineno}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and node.name == receiver:
            # `def NAME` / `class NAME` bind the name too, and their `.name` is a
            # plain `str` — no `ast.Name` exists for the AST to walk, so a
            # class named after the exempted receiver shadows the real one and
            # the row would certify ITS `status()` as clean.
            out.append(f"`def`/`class` named {receiver} at line {node.lineno}")
        elif type(node).__name__ in ("TypeVar", "TypeVarTuple", "ParamSpec") \
                and getattr(node, "name", None) == receiver:
            # PEP 695 type parameters bind in their own scope (cannot carry an
            # object, but the enumeration has to be honest about them).
            out.append(f"type parameter named {receiver} at line {node.lineno}")
        elif isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            out.append(f"`from … import *` at line {node.lineno} may bind {receiver}")
    return out


def _import_target_violation(target: ast.ImportFrom, receiver: str) -> str | None:
    """Why an exemption row's ``from … import`` cannot be CHECKED, or ``None``.

    ⛔ IT MUST BE ABSOLUTE AND NAME A MODULE PATH (#13863 review, P2).
    ``test_every_declared_non_sdk_receiver_is_a_real_one`` resolves the row's
    class with ``importlib.import_module(target.module)`` and then compares it
    against ``TortoiseSDK``. A RELATIVE import's ``module`` is resolved against
    the CALLING module's package, which ``importlib.import_module`` does not
    know about: ``from .embeddings import EmbeddingModel`` (``level=1``,
    ``module='embeddings'``) would silently import a TOP-LEVEL ``embeddings``
    module — a different class, or whatever a stray ``embeddings.py`` on
    ``sys.path`` provides — and certify a class it never looked at. ``from .
    import X`` has no module at all. Both are refused BY NAME rather than
    resolved into a guess.
    """
    if not target.module:
        return (f"`from . import {receiver}` names no module path, so the class "
                "cannot be checked — import it by its full module path")
    if target.level != 0:
        return (f"`{receiver}` is imported RELATIVELY (level={target.level}), so "
                "`importlib` would resolve it as a TOP-LEVEL module and this pin "
                "would check the WRONG class (or the wrong one on `sys.path`) — "
                "import it by its full module path")
    return None


def test_the_receiver_import_audit_refuses_relative_and_module_less_imports():
    """A row's import must be ABSOLUTE, or the pin checks a class it never named.

    ⛔ NON-VACUITY for the relative-import refusal (#13863 review, P2): the only
    declared row imports absolutely, so deleting the ``target.level != 0``
    clause reds NOTHING today. This pin makes that clause vary — the mutant is
    caught here (measured: with ``level != 0`` removed from
    ``_import_target_violation``, the two `level` rows FAIL).
    """
    receiver = "EmbeddingModel"
    absolute = ast.parse(f"from tortoise.embeddings import {receiver}\n").body[0]
    assert _import_target_violation(absolute, receiver) is None, (
        "an absolute `from … import` by full module path is the LEGITIMATE shape "
        "and must not be refused")
    for source, why in (
            (f"from .embeddings import {receiver}\n", "level"),
            (f"from ..embeddings import {receiver}\n", "level"),
            (f"from . import {receiver}\n", "no module path")):
        node = ast.parse(source).body[0]
        refusal = _import_target_violation(node, receiver)
        assert refusal is not None, (
            f"`{source.strip()}` was ACCEPTED — a relative import resolves against "
            "the calling module's package, which `importlib.import_module` does "
            "not know, so this pin would check a top-level module instead of the "
            "class the row names")
        assert why in refusal, (why, refusal)


def test_the_binding_audit_catches_every_way_the_name_can_be_rebound():
    """The audit must see EVERY binding form, not just an assignment.

    This is the pin for the P1 the review found on #13863: an exemption row is
    keyed on a NAME, so the only thing standing between it and a live SDK call
    site is that the name cannot be bound to anything but the declared class.
    """
    receiver = "EmbeddingModel"
    must_catch = {
        "parameter": f"def f({receiver}):\n    return {receiver}.status()\n",
        "lambda parameter": f"g = lambda {receiver}: {receiver}.status()\n",
        "except as": (f"try:\n    pass\nexcept Exception as {receiver}:\n"
                      f"    {receiver}.status()\n"),
        "assignment": f"{receiver} = get_sdk()\n{receiver}.status()\n",
        "with-as": f"with open('x') as {receiver}:\n    {receiver}.status()\n",
        "for target": f"for {receiver} in sdk_list:\n    {receiver}.status()\n",
        "import as": f"import tortoise.sdk as {receiver}\n{receiver}.status()\n",
        "del": f"del {receiver}\n",
        "global": f"def f():\n    global {receiver}\n    {receiver}.status()\n",
        "star import": "from tortoise.sdk import *\n",
        "mapping-pattern rest": (
            f"match {{}}:\n    case {{**{receiver}}}:\n        {receiver}.status()\n"),
        "class shadow": f"class {receiver}:\n    pass\n",
        "def shadow": f"def {receiver}():\n    pass\n",
        "async def shadow": f"async def {receiver}():\n    pass\n",
    }
    for label, source in must_catch.items():
        assert _receiver_binding_violations(source, receiver), (
            f"a {label} binding of the exempted name was NOT caught — that is the "
            "#13863 P1 fail-open: an SDK handle can arrive under the exempted "
            "name and the gate certifies a live retired-name call site as clean")

    legit = (f"def f():\n    from tortoise.embeddings import {receiver}\n"
             f"    return {receiver}.status()\n")
    assert _receiver_binding_violations(legit, receiver) == [], (
        "a lazy `from … import` of the declared class is the LEGITIMATE shape "
        "(#2850 imports it inside the handler) and must not be flagged")


def test_every_declared_non_sdk_receiver_is_a_real_one():
    """A table row is a CLAIM about a class, so check the class.

    The census cannot verify a receiver (that is why the table is declared), so
    this pin does it instead, and it is the pin that makes the table
    un-mutable into a bypass: every binding of the receiver name must be a
    ``from … import`` of the SAME class (`_receiver_binding_violations` refuses
    parameters, `except … as`, ``import … as``, assignments, `del`, match
    captures and star imports) — and the class those imports name is provably
    not a ``TortoiseSDK`` subclass and really has the named attribute as a
    callable. Re-point an import at the SDK (or bind the name any other way) and
    this reds instead of the gate silently losing a real call site.

    ⛔ The binding may be **function-local**, and may happen **more than once**:
    `_embedder_health` and its siblings each import ``EmbeddingModel`` inside
    the handler on purpose (#2850 — the health path collects no I/O, so it must
    not pay a module import), so requiring a single module-level import in
    ``tree.body`` rejects a legitimate row. The requirement is about
    AMBIGUITY, not about scope or count: every binding of the name must resolve
    to the SAME class, and the name must never be bound any other way.
    """
    import importlib
    import inspect

    from tortoise.sdk import TortoiseSDK

    assert NON_SDK_RECEIVERS, "no declared receivers — this pin is vacuous"
    for (rel, receiver, retired), reason in NON_SDK_RECEIVERS.items():
        assert reason.strip(), f"{rel}:{receiver}.{retired} has no reason"
        source = (REPO / rel).read_text()
        tree = ast.parse(source)

        # Every OTHER way the name can be bound is a bypass (#13863 P1).
        violations = _receiver_binding_violations(source, receiver)
        assert not violations, (
            f"{rel}: `{receiver}` is bound by something other than `from … "
            f"import` — {violations} — so an SDK handle can arrive under the "
            "exempted name and the row would hide a REAL retired-name call "
            "site (except in #13863)")

        bound = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                 and any(a.asname == receiver or a.name == receiver for a in n.names)]
        assert bound, (
            f"{rel}: `{receiver}` is never imported — the exemption names a "
            "class, so a callable/variable binding is not enough")
        # Several function-local imports of the SAME class are legitimate and
        # common here (`_embedder_health` and its siblings each import lazily so
        # the path collects no I/O, #2850). Ambiguity is the danger, not the
        # count: every binding of the name must name the SAME class.
        targets = {(n.module, n.level, a.name) for n in bound for a in n.names
                   if a.asname == receiver or a.name == receiver}
        assert len(targets) == 1, (
            f"{rel}: `{receiver}` is bound to {sorted(targets)} — the exemption "
            "names ONE class, so every import of that name must be the same one")
        target = bound[0]
        refusal = _import_target_violation(target, receiver)
        assert refusal is None, f"{rel}: {refusal}"
        # The alias that MATCHES the receiver, not names[0]: an import line may
        # carry several names and this row is about exactly one of them.
        alias = next(a for a in target.names
                     if a.asname == receiver or a.name == receiver)

        module = importlib.import_module(target.module)
        cls = getattr(module, alias.name)
        assert inspect.isclass(cls), (
            f"{rel}: `{receiver}` binds {cls!r}, which is not a class — the "
            "retired name would belong to no class and the exemption would be a guess")
        assert not issubclass(cls, TortoiseSDK), (
            f"{rel}: `{receiver}` resolves to {cls.__module__}.{cls.__qualname__}, "
            "which IS a TortoiseSDK subclass — this exemption would hide a REAL "
            "retired-name call site, which is exactly the bypass the table must "
            "not allow")
        assert callable(getattr(cls, retired, None)), (
            f"{cls.__module__}.{cls.__qualname__} has no callable `{retired}` — the "
            "exemption exempts a call that cannot exist, so it is stale")


# ── 6. the SDK container's POINT arm keeps the absorbed door's BEHAVIOUR ──
#
# ⛔ THE GAP THIS CLOSES (#12915 review, the blocking finding that outlived three
# rounds). A container is only an absorption if the member's BEHAVIOUR survives;
# the body's claim that `update_knowledge` "delegates to `update()` which already
# dispatches on the resolved label" hid a difference on exactly one label. For a
# Point, `update()` takes the `update_point` arm, which
#
#   1. appends NO journal record for a status write — the `derived = replay(
#      journal)` class, so a rebuild loses the transition; and
#   2. refuses every non-promote transition with ONE fixed sentence that names
#      neither the target status nor the transition, where the absorbed door
#      named both.
#
# Both are pinned below, in the container, so the routing cannot drift back
# without a red row. Measured at the merged head before the fix: 0 records and
# `update_point only promotes draft→live` (the review's own `tests/
# test_9649_update_entity_point_lifecycle.py` went from 40 failed to 63 passed
# once the routing was corrected).


def _sdk_with_journal(tmp_path):
    """An EMBEDDED engine whose JSONL journal is readable (the #3311 shape)."""
    from tortoise.sdk import TortoiseSDK

    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t11609_containers.db"),
                      event_log_path=str(events / "events.jsonl"))
    return sdk, events


def _entity_mutations(events, node_id):
    """The `EntityMutated` records addressing `node_id` (id is TOP-LEVEL)."""
    path = events / "events.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("type") == "EntityMutated" and rec.get("id") == node_id:
            out.append(rec)
    return out


def test_the_sdk_containers_point_arm_journals_the_status(tmp_path):
    """A status write through the container must leave a REPLAYABLE record.

    `update_entity` journalled a `restatus` against the Point; `update_point`
    (which `update()` routes a Point to) does not. Retiring the door without
    carrying that half would make every Point status write invisible to
    `rebuild_all` — a silent loss, not a rename.
    """
    sdk, events = _sdk_with_journal(tmp_path)
    try:
        pid = sdk._create_point("statement", "a claim", dedup=False)["id"]
        before = len(_entity_mutations(events, pid))

        # ⛔ THE WORD NEEDS ITS PARTNER (#12592): a bare `status="superseded"`
        # is refused at this door now, and this case pins the JOURNAL record a
        # container status write leaves — not the successor rule (that is pinned
        # in `tests/test_12592_supersede_door.py`). Naming a successor keeps the
        # intent and takes the same `restatus` path.
        sdk.update_knowledge(pid, status="superseded", supersededBy="a claim v2")

        written = _entity_mutations(events, pid)[before:]
        assert len(written) == 1, (
            "a status write through the container appended no (or more than "
            f"one) journal record — a rebuild cannot replay it: {written!r}")
        blob = json.dumps(written[0])
        assert "restatus" in blob, (
            f"the record does not describe a restatus: {written[0]!r}")
        assert "superseded" in blob, (
            "the record must carry the word the graph holds: " + blob)
    finally:
        sdk.close()


def test_the_sdk_containers_point_arm_names_the_refused_transition(tmp_path):
    """The refusal must let a caller CORRECT the call (AIP-193).

    Pre-fix the container answered with `update_point`'s fixed sentence
    ("only promotes draft→live"), which names neither the target word nor the
    transition — so a caller could not tell an undeclared transition from a
    closed door. Non-vacuity: the target word below is asserted, and the
    pre-fix message does not contain it.

    ⛔ #11824 S0 moved the ARM, not the claim: `archived` (the former target
    here) is a DECLARED SYNONYM of `deprecated` now, and `live -> deprecated` IS
    a declared transition, so that word LANDS. `draft` is the target whose
    transition from `live` is still undeclared (`_ALLOWED_TRANSITIONS['live']`
    is {deprecated, superseded}), and its refusal names BOTH the target and the
    transition — measured: "refused the status transition 'live' -> 'draft' …
    this door's declared Point transitions from 'live' are …".
    """
    sdk, events = _sdk_with_journal(tmp_path)
    try:
        pid = sdk._create_point("statement", "a claim", dedup=False)["id"]
        before = len(_entity_mutations(events, pid))

        with pytest.raises(ValueError) as excinfo:
            sdk.update_knowledge(pid, status="draft")

        message = str(excinfo.value)
        assert "draft" in message, (
            "the refusal does not name the refused target, so a caller cannot "
            f"tell a typo from a closed door: {message!r}")
        assert "live" in message, (
            f"the refusal does not name the transition it refused: {message!r}")
        assert len(_entity_mutations(events, pid)) == before, (
            "a refused transition appended a journal record")
        assert sdk._get_point(pid)["status"] == "live", (
            "a refused transition moved the row")
    finally:
        sdk.close()


# ── 7. the MCP container's props arm lands on the SAME writer as the SDK verb ──
#
# ⛔ #12915 review F1, fix direction: "add one MCP-level case that drives
# `tortoise_update_knowledge(id, props={'status': ...})` end to end, not a stub".
# §1 pins the DELEGATION (a recorder stands in for `tortoise_update`), which by
# construction cannot see the routing behind it; the two cases below open a real
# embedded engine and drive the advertised door through the whole chain
# (`tortoise_update_knowledge` -> `tortoise_update` -> `sdk.update_knowledge` ->
# `_update_entity_any` -> `_update_entity`), so the divergence the review measured
# — refusal + 0 records on the container, success + 1 record on the door it
# absorbs — cannot come back with the suite green.

def test_the_mcp_containers_props_arm_journals_the_status(tmp_path, monkeypatch):
    """Measured pre-fix at this head: `ValueError: update_point only promotes
    draft→live` and **0** `EntityMutated` records, where the absorbed door wrote
    **1** `restatus`. Post-fix the container and `TortoiseSDK.update_knowledge`
    (§6 above) produce the same record.
    """
    sdk, events = _sdk_with_journal(tmp_path)
    monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: sdk)
    try:
        pid = sdk._create_point("statement", "a claim", dedup=False)["id"]
        before = len(_entity_mutations(events, pid))

        # Same shape as the SDK case above: the pair, not the bare word (#12592).
        got = mcp_server.tortoise_update_knowledge(
            pid, props={"status": "superseded", "supersededBy": "a claim v2"})

        assert not (isinstance(got, dict) and got.get("error")), (
            "the advertised door refused a status write the door it absorbs "
            f"performs: {got}")
        written = _entity_mutations(events, pid)[before:]
        assert len(written) == 1, (
            "the container's props arm appended no (or more than one) journal "
            f"record, so a rebuild cannot replay it: {written!r}")
        blob = json.dumps(written[0])
        assert "restatus" in blob, f"not a restatus record: {written[0]!r}"
        assert "superseded" in blob, (
            "the record must carry the word the graph holds: " + blob)
    finally:
        sdk.close()


def test_the_mcp_containers_props_arm_names_the_refused_transition(
        tmp_path, monkeypatch):
    """The other half of the same routing: the refusal must let a caller
    CORRECT the call (AIP-193). Pre-fix the container answered with
    `update_point`'s fixed sentence, which names neither the target word nor the
    transition.

    ⛔ #11824 S0 moved the ARM, not the claim: `archived` is a DECLARED SYNONYM
    of `deprecated` now, so `live -> archived` LANDS. `draft` is the word whose
    transition from `live` is still undeclared, and its refusal names both the
    target and the transition (see the sibling SDK case above).
    """
    sdk, events = _sdk_with_journal(tmp_path)
    monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: sdk)
    try:
        pid = sdk._create_point("statement", "a claim", dedup=False)["id"]
        before = len(_entity_mutations(events, pid))

        got = mcp_server.tortoise_update_knowledge(
            pid, props={"status": "draft"})

        message = str(got.get("error", ""))
        assert "draft" in message, (
            "the refusal does not name the refused target, so a caller cannot "
            f"tell a typo from a closed door: {message!r}")
        assert "live" in message, (
            f"the refusal does not name the transition it refused: {message!r}")
        assert len(_entity_mutations(events, pid)) == before, (
            "a refused transition appended a journal record")
        assert sdk._get_point(pid)["status"] == "live", (
            "a refused transition moved the row")
    finally:
        sdk.close()
