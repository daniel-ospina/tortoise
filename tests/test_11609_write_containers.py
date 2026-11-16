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
import json
import pathlib
import sys

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


def test_update_knowledge_annotation_arm_delegates_to_annotate_operator(monkeypatch):
    member = _Member()
    monkeypatch.setattr(mcp_server, "tortoise_annotate_operator", member)
    dims = {"bias": 0.1, "precision": 0.2, "consistency": 0.3, "directness": 0.4}
    got = mcp_server.tortoise_update_knowledge("n1", annotation=dims)
    assert got is _Member.SENTINEL
    assert member.calls == [(("n1",), dims)]


def test_update_knowledge_retract_arm_delegates_to_the_sdk_retract_point(monkeypatch):
    retract = _Member()
    monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: _stub_sdk(retract_point=retract))
    got = mcp_server.tortoise_update_knowledge("n1", invalid_at="2030-01-01T00:00:00Z")
    assert got is _Member.SENTINEL
    assert retract.calls == [(("n1",), {"valid_to": "2030-01-01T00:00:00Z"})]


def test_update_knowledge_invalidate_arm_delegates_to_the_sdk_invalidate_point(monkeypatch):
    invalidate = _Member()
    monkeypatch.setattr(mcp_server, "_get_org_sdk",
                        lambda: _stub_sdk(invalidate_point=invalidate))
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
    assert baseline.calls == [(("p1", 2.0, 1.0), {})]


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
    stub = _stub_sdk(mitigate_operator=mitigate)
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


# ── 3. the goalState cache writes through a LIVE name (F1) ──────────

def test_the_goal_state_cache_writes_through_a_live_sdk_method():
    """#12915 review F1: `_write_goal_state_cache` called `sdk.update_entity`,
    which #4282 RETIRED — `TortoiseSDK.__getattr__` raises, so every `met`/
    `unmet` judgement died inside the cache write.

    The stub carries ONLY `update_knowledge`, which is the approved replacement.
    Any call to a name that is not on this object raises, so the assertion
    reddens the moment the retired spelling comes back.
    """
    from tortoise.goal_judgement import _write_goal_state_cache

    written = []
    stub = _stub_sdk(update_knowledge=lambda node_id, **props: written.append(
        (node_id, props)))

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


def _attribute_names(tree: ast.AST) -> list[tuple[int, str]]:
    return [(node.lineno, node.attr) for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in RETIRED_SDK_METHODS]


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
        for lineno, attr in _attribute_names(tree):
            offenders.append(f"{path.relative_to(REPO)}:{lineno}: .{attr}")
    assert not offenders, (
        "production code reaches RETIRED SDK names — a retired name RAISES at "
        "call time (#4282/#3836(c)), so each of these is a live failure:\n  "
        + "\n  ".join(offenders))


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
        pid = sdk.create_point("statement", "a claim", dedup=False)["id"]
        before = len(_entity_mutations(events, pid))

        sdk.update_knowledge(pid, status="superseded")

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
    """
    sdk, events = _sdk_with_journal(tmp_path)
    try:
        pid = sdk.create_point("statement", "a claim", dedup=False)["id"]
        before = len(_entity_mutations(events, pid))

        with pytest.raises(ValueError) as excinfo:
            sdk.update_knowledge(pid, status="archived")

        message = str(excinfo.value)
        assert "archived" in message, (
            "the refusal does not name the refused target, so a caller cannot "
            f"tell a typo from a closed door: {message!r}")
        assert "live" in message, (
            f"the refusal does not name the transition it refused: {message!r}")
        assert len(_entity_mutations(events, pid)) == before, (
            "a refused transition appended a journal record")
        assert sdk.get_point(pid)["status"] == "live", (
            "a refused transition moved the row")
    finally:
        sdk.close()
