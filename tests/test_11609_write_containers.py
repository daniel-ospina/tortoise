"""#11609 lane B — the WRITE/MUTATE containers, pinned in BOTH directions.

WHAT THIS SUITE IS FOR (#4282 Phase 2)
--------------------------------------
Phase 2 BUILDS approved target methods and RETIRES the doors they absorb. Both halves
are easy to get silently wrong, in opposite ways:

  * a container that "covers" a member by RE-IMPLEMENTING it drifts from the door it
    replaced, and the drift is invisible until a caller notices the two answers differ;
  * a retirement that DROPS the old name breaks every client mid-migration, which is
    what the #3883 warning shim exists to prevent.

So every container is checked on the same two facts, derived from the registry rather
than typed:

  1. DISPATCH — each documented discriminator value reaches the handler it absorbs, and
     an undocumented value is REFUSED naming the values that exist (AIP-193). The
     assertion observes the DELEGATION (the member handler is called, with the caller's
     arguments) instead of comparing behaviour, because "the two answers agree" cannot
     distinguish a delegation from a copy that happens to agree today.
  2. SHIM — the retired name still resolves through the #3883 shim, its answer is the
     ORIGINAL handler's, and the warning names a replacement that is a LIVE tool. Every
     redirect must resolve in ONE hop: GitHub's own `DeprecatedToolAliases` limit.

Nothing here re-implements the members. A container whose body stopped delegating fails
the first half; a retirement whose forward entry dangles, or whose shim stopped serving,
fails the second.
"""

from __future__ import annotations

import pytest

import tortoise.mcp_server as ms
from tortoise.tool_registry import RETIRED_TOOL_REGISTRY, TOOL_REGISTRY

#: container tool name -> (member handler it must delegate to, kwargs that select it).
#: The DISCRIMINATOR VALUES are the ones `docs/product/bridge-table.md` Part A records;
#: a container that grows a new arm must grow a row here, which is the point.
DISPATCH_CASES = [
    # ── tortoise_update_knowledge: dispatch is WHICH FIELDS are set ─────────────
    ("tortoise_update_knowledge", "tortoise_update",
     {"id": "p1", "props": {"content": "x"}}),
    ("tortoise_update_knowledge", "tortoise_annotate_operator",
     {"id": "op1", "annotation": {"bias": 0.1, "precision": 0.2,
                                  "consistency": 0.3, "directness": 0.4}}),
    # The temporal arm calls the SDK method its door FRONTS (`sdk.retract_point` /
    # `sdk.invalidate_point`) rather than the door's handler, because that handler's
    # signature carries no window end — and the window end is the whole point of
    # retraction ("a temporal field, not a separate verb"). Marked `sdk:` so the
    # assertion below knows which seam to observe.
    ("tortoise_update_knowledge", "sdk:retract_point",
     {"id": "p1", "invalid_at": "2026-01-01T00:00:00+00:00"}),
    ("tortoise_update_knowledge", "sdk:invalidate_point",
     {"id": "p1", "invalid_at": "2026-01-01T00:00:00+00:00",
      "corrected_by_id": "p2"}),
    # ── tortoise_supersede_knowledge: the LINK POLICY ──────────────────────────
    ("tortoise_supersede_knowledge", "tortoise_supersede",
     {"old_id": "a", "new_id": "b", "transfer_edges": True}),
    ("tortoise_supersede_knowledge", "tortoise_supersede",
     {"old_id": "a", "new_id": "b", "transfer_edges": False}),
    # ── tortoise_adjust_relationship: the ADJUSTMENT ──────────────────────────
    ("tortoise_adjust_relationship", "tortoise_mitigate_operator",
     {"id": "op1", "reason": "weak evidence", "strength": 0.3}),
    ("tortoise_adjust_relationship", "tortoise_annotate_operator",
     {"id": "op1", "bias": 0.1, "precision": 0.2, "consistency": 0.3,
      "directness": 0.4}),
    ("tortoise_adjust_relationship", "tortoise_operator_action",
     {"id": "op1", "action": "mitigate", "reason": "weak evidence"}),
    ("tortoise_adjust_relationship", "tortoise_operator_action",
     {"id": "op1", "action": "annotate", "bias": 0.1, "precision": 0.2,
      "consistency": 0.3, "directness": 0.4}),
    # ── tortoise_refresh_confidence: scope= ───────────────────────────────────
    ("tortoise_refresh_confidence", "tortoise_dream", {"scope": "dirty"}),
    ("tortoise_refresh_confidence", "tortoise_dream", {"scope": "full"}),
    ("tortoise_refresh_confidence", "tortoise_promote_point",
     {"scope": "promote", "point_id": "p1"}),
    ("tortoise_refresh_confidence", "tortoise_set_point_baseline",
     {"scope": "baseline", "point_id": "p1", "alpha": 2.0, "beta": 1.0}),
    # ── the single-member containers ──────────────────────────────────────────
    ("tortoise_write_question", "tortoise_file_decision",
     {"options": ["a", "b"], "evidence": ["e"], "choice": 0}),
    ("tortoise_record_decision", "tortoise_file_human_approval",
     {"approver_id": "s1", "artifact_id": "o1", "point_ids": ["p1"]}),
]

#: retired tool name -> the live container it must forward to (one hop).
RETIREMENTS = {
    "tortoise_update": "tortoise_update_knowledge",
    "tortoise_update_point": "tortoise_update_knowledge",
    "tortoise_update_entity": "tortoise_update_knowledge",
    "tortoise_retract_point": "tortoise_update_knowledge",
    "tortoise_supersede": "tortoise_supersede_knowledge",
    "tortoise_invalidate": "tortoise_supersede_knowledge",
    "tortoise_annotate_operator": "tortoise_adjust_relationship",
    "tortoise_mitigate_operator": "tortoise_adjust_relationship",
    "tortoise_operator_action": "tortoise_adjust_relationship",
    "tortoise_dream": "tortoise_refresh_confidence",
    "tortoise_promote_point": "tortoise_refresh_confidence",
    "tortoise_set_point_baseline": "tortoise_refresh_confidence",
    "tortoise_file_decision": "tortoise_write_question",
    "tortoise_file_human_approval": "tortoise_record_decision",
}


@pytest.fixture()
def observed(monkeypatch):
    """Record delegation instead of performing it.

    The container resolves `tortoise_<member>` from module globals at CALL time, so
    replacing the module attribute is exactly the seam a delegation crosses — a
    container that re-implemented the arm would never reach this recorder.
    """
    calls: list[tuple[str, tuple, dict]] = []

    def _recorder(name):
        def _fn(*args, **kwargs):
            calls.append((name, args, kwargs))
            return {"delegated_to": name}
        return _fn

    for _container, member, _kwargs in DISPATCH_CASES:
        if member.startswith("sdk:"):
            continue  # the SDK seam is patched below
        monkeypatch.setattr(ms, member, _recorder(member), raising=False)

    class _FakeSDK:
        """Only the two temporal writers: everything else goes through handlers."""
        retract_point = staticmethod(_recorder("sdk:retract_point"))
        invalidate_point = staticmethod(_recorder("sdk:invalidate_point"))

    monkeypatch.setattr(ms, "_get_org_sdk", lambda: _FakeSDK(), raising=False)
    # The quota gate resolves the CALLER's tenant and refuses without one, which is a
    # property of the MCP transport rather than of dispatch. Passing the callable
    # through keeps the measurement on the seam this test is about.
    monkeypatch.setattr(ms, "_quota_gated",
                        lambda fn, *a, **k: fn, raising=False)
    # `_safe` fail-closes on an unset transport mode (#236), and the stdio arm passes
    # while no API key is set — which is the lane's own environment. `http` is NOT
    # usable here: `tortoise_refresh_confidence` refuses it by design (#329), and that
    # refusal has its own test below.
    from tortoise import mcp_auth
    token = mcp_auth._transport_mode.set("stdio")
    try:
        yield calls
    finally:
        mcp_auth._transport_mode.reset(token)


def test_every_container_dispatches_to_the_handler_it_absorbs(observed):
    """Each documented discriminator value reaches the member's OWN handler."""
    for container, member, kwargs in DISPATCH_CASES:
        before = len(observed)
        ms.__dict__[container](**kwargs)
        assert len(observed) == before + 1, (
            f"{container}({kwargs}) did not delegate to exactly one member handler "
            f"(observed {observed[before:]!r})")
        name, _args, _inner = observed[-1]
        assert name == member, (
            f"{container}({kwargs}) delegated to {name}, not {member} — the two "
            f"doors now behave differently")


def test_the_container_passes_the_callers_arguments_through(observed):
    """A delegation that reshapes its arguments is a re-implementation by stealth.

    Only the arguments the container is DOCUMENTED to translate are excluded, and
    there is exactly one such translation in this family: `refresh_confidence`'s
    `scope` -> the dream arm's `full=` flag (the scope vocabulary is the container's
    own, the flag is the member's).
    """
    ms.tortoise_refresh_confidence(scope="full")
    name, _a, kwargs = observed[-1]
    assert name == "tortoise_dream"
    assert kwargs["full"] is True, (
        "scope='full' must reach the dream arm as full=True — otherwise the "
        "container answers the DIRTY pass and calls it a whole-graph one")

    ms.tortoise_refresh_confidence(scope="dirty")
    _n, _a, kwargs = observed[-1]
    assert kwargs["full"] is False

    # The temporal arm keeps the caller's instant rather than minting its own: the
    # window end reaches `sdk.retract_point(valid_to=...)`, which is the writer that
    # stamps `validTo` and journals it, so a rebuild replays the caller's instant.
    ms.tortoise_update_knowledge(id="p1", invalid_at="2026-01-01T00:00:00+00:00")
    name, args, kwargs = observed[-1]
    assert name == "sdk:retract_point", (name, args, kwargs)
    assert "2026-01-01" in str(kwargs), (
        "the retraction dropped the caller's window end — the door stamps the "
        "point's validTo from it, so dropping it silently retracts at `now`"
    )

    # A property update reaches the Point/entity dispatcher with the props intact.
    ms.tortoise_update_knowledge(id="p1", props={"content": "x"})
    _n, _a, kwargs = observed[-1]
    assert kwargs["props"] == {"content": "x"}


@pytest.mark.parametrize(("container", "bad", "must_name"), [
    ("tortoise_refresh_confidence", {"scope": "everything"},
     ["dirty", "full", "promote", "baseline"]),
    ("tortoise_adjust_relationship", {"id": "op1", "action": "frobnicate"},
     ["mitigate", "annotate"]),
])
def test_an_unknown_discriminator_value_is_refused_naming_the_real_ones(
        container, bad, must_name):
    """AIP-193: the error must let a caller resolve it, so it names the values.

    Never a fall-through to "some arm" — that is how `operator_action` used to accept
    `credibility` and silently drop it (the defect beta's own note records).
    """
    result = ms.__dict__[container](**bad)
    assert isinstance(result, dict) and result.get("error"), (
        f"{container}({bad}) was ACCEPTED — an unknown discriminator value must be "
        f"refused, not defaulted: {result!r}")
    message = result["error"]
    for value in must_name:
        assert value in message, (
            f"{container}'s refusal does not name {value!r}, so a caller cannot "
            f"correct the call: {message!r}")


def test_one_arm_per_call_is_refused_rather_than_half_recorded():
    """Two arms in one call cannot both be recorded, so the call is refused.

    This is the SDK verb's own contract (`update_knowledge`) carried to the door: a
    caller cannot tell a partial write from a full one.
    """
    mixed = ms.tortoise_update_knowledge(
        id="p1", props={"content": "x"}, invalid_at="2026-01-01T00:00:00+00:00")
    assert mixed.get("error"), mixed

    both_dims = ms.tortoise_adjust_relationship(
        id="op1", reason="why", strength=0.2, bias=0.1, precision=0.2,
        consistency=0.3, directness=0.4)
    assert both_dims.get("error"), both_dims

    partial = ms.tortoise_adjust_relationship(id="op1", bias=0.1)
    assert partial.get("error"), partial


def test_two_different_window_ends_are_a_conflict_not_a_precedence():
    """`valid_until` and `invalid_at` are two spellings of ONE field."""
    result = ms.tortoise_update_knowledge(
        id="p1", valid_until="2026-01-01T00:00:00+00:00",
        invalid_at="2027-01-01T00:00:00+00:00")
    assert result.get("error"), result


def test_corrected_by_id_needs_a_temporal_field():
    """Without one it would be a silent no-op — so it is refused."""
    result = ms.tortoise_update_knowledge(id="p1", corrected_by_id="p2")
    assert result.get("error"), result


# ── direction 2: the retired name still answers, and says where to go ─────────


def test_every_retirement_forwards_to_a_live_tool_in_one_hop():
    """A redirect must RESOLVE — aliases are not transitive (GitHub's own limit)."""
    from tortoise.tool_registry import RETIRED_USE_INSTEAD

    live = {t.name for t in TOOL_REGISTRY}
    for retired, container in RETIREMENTS.items():
        assert retired not in live, f"{retired} is retired but still advertised"
        pointer = RETIRED_USE_INSTEAD[retired]
        assert pointer.startswith(container), (
            f"{retired} forwards to {pointer!r}, not to {container}")
        assert container in live, (
            f"{retired} forwards to {container}, which is not a live tool — the "
            f"caller would land on a name that does not exist")
        # ONE HOP: the container itself must not be a retired name.
        assert container not in RETIRED_USE_INSTEAD, (
            f"{retired} -> {container} -> ... is a chain, and a chain is a churn loop")


def test_the_retired_name_is_served_by_its_original_handler():
    """The shim reuses the ORIGINAL function, so the answer cannot drift.

    A shim that resolved to the CONTAINER would be a second dispatch of the same
    name, and a shim that resolved to nothing would be a silent "tool not found"
    instead of a migration hint (#3836 (b)).
    """
    from tortoise.mcp_server import build_retired_tools

    handlers = {t.name: getattr(ms, t.name) for t in RETIRED_TOOL_REGISTRY
                if hasattr(ms, t.name)}
    shims = build_retired_tools(RETIRED_TOOL_REGISTRY, handlers)
    for retired in RETIREMENTS:
        assert retired in shims, (
            f"{retired} is retired with no shim — a caller gets 'tool not found' "
            f"instead of the migration warning #3883 requires")
        # `functools.wraps` carries the original's identity, so the shim IS the
        # function the live surface used to serve.
        assert shims[retired].fn.__wrapped__ is handlers[retired], (
            f"{retired}'s shim does not wrap its original handler")


def test_no_retired_name_is_missing_from_the_warning_path():
    """Every retired entry the registry declares is actually shim-able.

    A `retired_use_instead` whose handler is gone is the `#2210` shape: the name is
    hidden from `tools/list` AND unresolvable on `tools/call`, i.e. deleted while
    still being advertised as retired.
    """
    declared = {t.name for t in RETIRED_TOOL_REGISTRY}
    missing = sorted(n for n in declared if not hasattr(ms, n))
    assert not missing, (
        "retired names with no handler — the shim cannot be built, so a caller of "
        f"one of these gets nothing at all: {missing}")


def test_refresh_confidence_refuses_http():
    """#329 carried onto the container: whole-graph EP is not tenant-HTTP work.

    `tortoise_dream` was `http_policy=False` precisely so a tenant could not ask for a
    whole-graph EP pass over the MCP transport. The container ABSORBS that door and is
    itself `http_policy=False`, and an unadvertised tool is still callable BY NAME —
    so the handler must refuse, as its FIRST statement (`privileged_exposure_violations`
    reads exactly that shape). Losing the guard here would hand a tenant the CPU work
    the original policy exists to keep off that surface.
    """
    from tortoise import mcp_auth

    token = mcp_auth._transport_mode.set("http")
    try:
        for scope in ("dirty", "full"):
            result = ms.tortoise_refresh_confidence(scope=scope)
            assert result.get("error"), (
                f"refresh_confidence(scope={scope!r}) answered over the HTTP "
                f"transport — whole-graph EP must not be reachable there (#329): "
                f"{result!r}")
    finally:
        mcp_auth._transport_mode.reset(token)


def test_the_rest_door_to_whole_graph_ep_survived_the_container():
    """/v1/dream is the BUDGETED HTTP path, so retiring the tool row must not drop it."""
    from tortoise.tool_registry import TOOL_REGISTRY

    row = next(t for t in TOOL_REGISTRY if t.name == "tortoise_refresh_confidence")
    assert row.http_policy is False, (
        "the container reaches whole-graph EP, so it must not be HTTP-exposed")
    assert row.rest_spec is not None and row.rest_spec.path == "/v1/dream", (
        "the POST /v1/dream route is the hosted, separately-budgeted door to "
        "whole-graph EP; retiring `tortoise_dream` must not take it with it")
