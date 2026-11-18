"""#11609 lane P1 — `create_entity`'s POINT and DIARY arms, and the doors they freed.

WHAT THIS FILE GATES
--------------------
`tests/test_11609_door_retirement.py` holds the `PROTECTED` contract: a live door may
only leave that table in the batch that makes its stated reason FALSE. For
`tortoise_create_point` and `tortoise_diary_write` that reason was
*"create_entity has no point arm"* / *"…no diary arm"*, so the arms and the two
retirements are ONE change and this file is what proves the first half:

  1. **The arm dispatches** — measured by substitution at `create_point`, the method the
     arm relays to, so a future re-implementation inside `create_entity` reds here
     instead of drifting silently from the door it claims to absorb.
  2. **The arm stores what the door stored** — measured on a REAL graph, by writing the
     same claim through the door and through the arm and comparing the stored node.
     This is the assertion the retirement actually needs: a container that answers
     `type="point"` with a `:Subject` (or that drops the caller's `pointKind`, or that
     mints duplicates where the door folded) is caught by none of the registry, the
     pointer, nor the dispatch pin.
  3. **The retired name still answers** — through the #3883 shim (#3883/#3836(b)), which
     must serve the door's OWN handler rather than a re-implementation.

⛔ THE ARGUMENTS ARE THE DOORS', NOT THE CONTAINER'S, and two of them are silent if they
are lost, which is why each has its own pin below:

  · `pointKind` rides INSIDE `props` (the container has no per-kind parameter), and an
    UNSENT kind DEFAULTS to `statement` — a REAL kind, so a caller who asked for
    `hypothesis` and lost it gets a well-formed point of the wrong kind, with no error;
  · `dedup` differs between the two doors and the container must not flatten the
    difference: `tortoise_create_point` declared `dedup=True` (a re-write FOLDS), while
    `tortoise_diary_write` passed no dedup at all (create_point's own default, so every
    entry mints a new Point).
"""
from __future__ import annotations

import pytest

from tortoise import mcp_server
from tortoise.sdk import TortoiseSDK

#: Fields that are per-WRITE rather than per-DOOR: they differ between any two writes
#: (including two through the same door), so they cannot distinguish the two paths.
_PER_WRITE = frozenset({
    "id", "content", "content_hash", "createdAt", "updatedAt", "name",
})


class _Recorder:
    """Records the call the arm makes, and answers like `create_point` would."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, kind, content, *, is_episodic=None, **props):
        self.calls.append((kind, content, dict(props)))
        return {"id": "pt-fixed", "content": content, "pointKind": kind}

    @property
    def last(self) -> tuple:
        assert self.calls, "the arm never reached create_point"
        return self.calls[-1]


@pytest.fixture
def bare_sdk(monkeypatch):
    """An SDK with only the legs the ARM uses — no graph, no persistence."""
    recorder = _Recorder()
    sdk = TortoiseSDK.__new__(TortoiseSDK)
    monkeypatch.setattr(TortoiseSDK, "create_point", recorder, raising=True)
    monkeypatch.setattr(TortoiseSDK, "_nudge_candidates",
                        lambda _self, _text, exclude_ids=None: [], raising=True)
    return sdk, recorder


# ── 1. the arm dispatches to the method it absorbed ────────────────────────

class TestTheArmDispatches:
    def test_the_point_arm_relays_to_create_point_with_the_statement_default(
            self, bare_sdk):
        sdk, recorder = bare_sdk
        sdk.create_entity("point", "widgets are load-bearing")
        kind, content, props = recorder.last
        assert (kind, content) == ("statement", "widgets are load-bearing"), (
            "the point arm must relay the CLAIM to `create_point` — `name` is the "
            f"content there and an unsent kind is `statement`; got {kind!r}/{content!r}")
        assert props["dedup"] is True, (
            "`tortoise_create_point` declared `dedup=True`, so a migrating caller that "
            "omits it still FOLDS; `create_point`'s own SDK default is False, and "
            f"carrying it here would mint duplicates — props were {props!r}")

    def test_the_point_kind_rides_in_props_and_reaches_the_write(self, bare_sdk):
        sdk, recorder = bare_sdk
        sdk.create_entity("point", "a guess", props={"pointKind": "hypothesis",
                                                    "tags": ["t"]})
        kind, _content, props = recorder.last
        assert kind == "hypothesis", (
            "the caller's `pointKind` did not reach the write — the pointer advertises "
            "it INSIDE props, so losing it files a silent `statement`")
        assert props["tags"] == ["t"], "the caller's props were dropped on the way"
        assert "pointKind" not in props, (
            "pointKind must not ALSO ride as a caller prop: `create_point` lets a "
            "props-`pointKind` WIN over its positional kind (#9287), so a stale copy "
            "is a second spelling of one decision")

    def test_an_explicit_dedup_is_the_callers(self, bare_sdk):
        sdk, recorder = bare_sdk
        sdk.create_entity("point", "x", props={"dedup": False})
        _kind, _content, props = recorder.last
        assert props["dedup"] is False, (
            "the arm defaulted over an explicit `dedup=False`, so `dedup=False` "
            "(force-create, the door's documented escape from the fold) would be "
            "unreachable through the container")

    def test_the_diary_arm_pins_the_kind_and_carries_the_author(self, bare_sdk):
        sdk, recorder = bare_sdk
        sdk.create_entity("diary", "dear diary", props={
            "authoredBy": "agent-1", "topic": "t", "wing": "w"})
        kind, content, props = recorder.last
        assert (kind, content) == ("diary", "dear diary")
        assert props == {"authoredBy": "agent-1", "topic": "t", "wing": "w"}, (
            "the diary arm must carry the door's own three props and invent nothing — "
            f"got {props!r}")

    def test_the_diary_arm_does_not_acquire_the_point_doors_dedup(self, bare_sdk):
        sdk, recorder = bare_sdk
        sdk.create_entity("diary", "dear diary", props={"authoredBy": "agent-1"})
        _kind, _content, props = recorder.last
        assert "dedup" not in props, (
            "`tortoise_diary_write` passed no dedup, so every entry minted a fresh "
            "Point; defaulting it True here would make a repeated entry FOLD onto the "
            "earlier one and silently lose the second writing")

    @pytest.mark.parametrize("type_,props,match", [
        ("diary", {}, "requires authoredBy"),
        ("diary", {"authoredBy": "a", "pointKind": "statement"}, "pins the kind"),
    ])
    def test_the_diary_arm_refuses_rather_than_inventing(self, bare_sdk, type_, props,
                                                        match):
        """Both refusals protect a fact the retired door could not express.

        `tortoise_diary_write(agent_name, entry, …)` could not be called without its
        author, and it took NO kind argument at all — so a container that accepts
        `type="diary"` without `authoredBy`, or that lets a `pointKind` override the
        discriminator, answers with something the door never could.
        """
        sdk, recorder = bare_sdk
        with pytest.raises(ValueError, match=match):
            sdk.create_entity(type_, "x", props=props)
        assert not recorder.calls, "a refused call still wrote"

    def test_the_arms_answer_in_the_containers_shape(self, bare_sdk):
        """`{node, nudges}` — the same reply every other arm gives."""
        sdk, _recorder = bare_sdk
        for reply in (sdk.create_entity("point", "a claim"),
                      sdk.create_entity("diary", "e", props={"authoredBy": "a"})):
            assert set(reply) == {"node", "nudges"}, reply
            assert reply["node"]["id"] == "pt-fixed"


# ── 2. the arm stores what the door stored (REAL graph) ───────────────────

class TestTheArmStoresWhatTheDoorStored:
    def test_a_point_through_the_arm_is_the_point_the_door_makes(self, sdk_factory):
        sdk = sdk_factory()
        door = sdk.create_point("hypothesis", "the door's claim about widgets")
        arm = sdk.create_entity(
            "point", "the arm's claim about widgets",
            props={"pointKind": "hypothesis"})["node"]

        _assert_is_a_point(sdk, door["id"])
        _assert_is_a_point(sdk, arm["id"])

        stored_door = sdk.get_point(door["id"])
        stored_arm = sdk.get_point(arm["id"])
        assert stored_door["pointKind"] == stored_arm["pointKind"] == "hypothesis", (
            "the two paths stored different kinds — the arm did not absorb the door")
        assert stored_door["content"] == "the door's claim about widgets"
        assert stored_arm["content"] == "the arm's claim about widgets"
        assert (set(stored_door) - _PER_WRITE) == (set(stored_arm) - _PER_WRITE), (
            "the arm and the door stored DIFFERENT property sets, so the container is "
            "a lookalike rather than the door: "
            f"door={sorted(set(stored_door) - _PER_WRITE)} "
            f"arm={sorted(set(stored_arm) - _PER_WRITE)}")

    def test_a_diary_entry_through_the_arm_is_the_entry_the_door_makes(
            self, sdk_factory):
        sdk = sdk_factory()
        door = sdk.diary_write("agent-1", "the door's entry", topic="t", wing="w")
        arm = sdk.create_entity("diary", "the arm's entry", props={
            "authoredBy": "agent-1", "topic": "t", "wing": "w"})["node"]

        _assert_is_a_point(sdk, door["id"])
        _assert_is_a_point(sdk, arm["id"])

        stored_door = sdk.get_point(door["id"])
        stored_arm = sdk.get_point(arm["id"])
        for key, want in (("pointKind", "diary"), ("authoredBy", "agent-1"),
                          ("topic", "t"), ("wing", "w")):
            assert stored_door.get(key) == stored_arm.get(key) == want, (
                f"{key!r} differs between the door and the arm: "
                f"door={stored_door.get(key)!r} arm={stored_arm.get(key)!r}")
        assert (set(stored_door) - _PER_WRITE) == (set(stored_arm) - _PER_WRITE), (
            "the diary arm stored a different property set than the door: "
            f"door={sorted(set(stored_door) - _PER_WRITE)} "
            f"arm={sorted(set(stored_arm) - _PER_WRITE)}")

    def test_the_point_arm_folds_a_repeat_as_the_door_did(self, sdk_factory):
        """The door's `dedup=True` default is a BEHAVIOUR, not a hint."""
        sdk = sdk_factory()
        first = sdk.create_entity("point", "a repeated claim")["node"]
        again = sdk.create_entity("point", "a repeated claim")["node"]
        assert again["id"] == first["id"], (
            "a repeated `create_entity(type='point')` minted a SECOND Point — the "
            "retired door folded by default (dedup=True), so a migrated re-run would "
            "now duplicate every claim it re-asserts")
        forced = sdk.create_entity("point", "a repeated claim",
                                  props={"dedup": False})["node"]
        assert forced["id"] != first["id"], "dedup=False stopped force-creating"

    def test_the_diary_arm_mints_a_new_entry_each_time_as_the_door_did(
            self, sdk_factory):
        sdk = sdk_factory()
        first = sdk.create_entity("diary", "same words",
                                  props={"authoredBy": "a"})["node"]
        again = sdk.create_entity("diary", "same words",
                                  props={"authoredBy": "a"})["node"]
        assert again["id"] != first["id"], (
            "two diary writes folded into one Point — `tortoise_diary_write` passed no "
            "dedup, and a diary is a JOURNAL: collapsing two entries loses one")


def _assert_is_a_point(sdk, point_id: str) -> None:
    """ONE belief kind (#10977): the node is a `:Point`, never a new label."""
    count = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN count(n)",
        params={"id": point_id}).result_set[0][0]
    assert count == 1, (
        f"{point_id} is not a `:Point` — a point is `:Point {{pointKind: …}}` and the "
        "arms must never mint a new kind (#10977)")


# ── 3. the retired names still answer through the #3883 shim ──────────────

class TestTheRetiredNamesStillAnswer:
    @pytest.mark.parametrize("name", ["tortoise_create_point", "tortoise_diary_write"])
    def test_the_shim_serves_the_doors_own_handler(self, name):
        """A shim that re-implemented the door would be the same defect as a container
        that did, one surface over — so the assertion is on IDENTITY, not on output."""
        shim = mcp_server._RETIRED_SHIMS[name]
        handler = getattr(mcp_server, name)
        assert shim.fn.__wrapped__ is handler, (
            f"{name}'s #3883 shim does not serve the door's own handler, so a retired "
            "caller is answered by something the live tool never ran")
        assert (shim.fn.__doc__ or "").startswith("RETIRED"), name

    def test_the_pointer_names_the_arm_that_absorbed_it(self):
        """The shim's warning must name a call the container ACCEPTS.

        `test_11609_door_retirement.py` binds the pointer against the tool signature;
        this asserts the other end — that the `type=` it names is a real dispatch arm,
        so the warning cannot send a caller to a `ValueError: unknown type`.
        """
        import inspect
        import re

        from tortoise.tool_registry import RETIRED_USE_INSTEAD

        source = inspect.getsource(TortoiseSDK.create_entity)
        arms = set(re.findall(r'^        (?:el)?if t == "([a-z_]+)":$', source,
                              re.MULTILINE))
        for name, kind in (("tortoise_create_point", "point"),
                           ("tortoise_diary_write", "diary")):
            pointer = RETIRED_USE_INSTEAD[name]
            assert f'type="{kind}"' in pointer, (name, pointer)
            assert kind in arms, (
                f"{name} is pointed at {pointer!r} but `create_entity` does not "
                f"dispatch type={kind!r} (arms {sorted(arms)})")
