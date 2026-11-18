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
    monkeypatch.setattr(TortoiseSDK, "_create_point", recorder, raising=True)
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
        door = sdk._create_point("hypothesis", "the door's claim about widgets")
        arm = sdk.create_entity(
            "point", "the arm's claim about widgets",
            props={"pointKind": "hypothesis"})["node"]

        _assert_is_a_point(sdk, door["id"])
        _assert_is_a_point(sdk, arm["id"])

        stored_door = sdk._get_point(door["id"])
        stored_arm = sdk._get_point(arm["id"])
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
        door = sdk._diary_write("agent-1", "the door's entry", topic="t", wing="w")
        arm = sdk.create_entity("diary", "the arm's entry", props={
            "authoredBy": "agent-1", "topic": "t", "wing": "w"})["node"]

        _assert_is_a_point(sdk, door["id"])
        _assert_is_a_point(sdk, arm["id"])

        stored_door = sdk._get_point(door["id"])
        stored_arm = sdk._get_point(arm["id"])
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
        # Both arm shapes, for the reason `test_11609_door_retirement.py`'s
        # `_create_entity_dispatch_arms` states: a COMBINED arm
        # (`elif t == "point" or t == "diary":`) names both kinds, and this test would
        # otherwise call the container's own arm "not dispatched" (measured: the
        # single-shape regex derived `['document', 'event', 'object', 'subject']`).
        arms: list[str] = []
        for line in source.splitlines():
            match = re.match(r'^ {8}(?:el)?if ((?:t == "[a-z_]+"(?: or )?)+):$', line)
            if match:
                arms.extend(re.findall(r't == "([a-z_]+)"', match.group(1)))
        for name, kind in (("tortoise_create_point", "point"),
                           ("tortoise_diary_write", "diary")):
            pointer = RETIRED_USE_INSTEAD[name]
            assert f'type="{kind}"' in pointer, (name, pointer)
            assert kind in arms, (
                f"{name} is pointed at {pointer!r} but `create_entity` does not "
                f"dispatch type={kind!r} (arms {sorted(arms)})")

    def test_the_point_arm_still_carries_the_goal_state_field(self, sdk_factory):
        """#13110 gave the point DOOR a `goalState` PARAMETER; the arm must keep it.

        The retirement removes a schema field an agent could see, so the one thing
        that must not change is that the value still REACHES the write: the
        container takes `goalState` at the top level and relays it into `props`,
        and the point arm forwards `props` to `create_point`, which stores it (with
        the #13110 "a create never DELETES" guard, which is why the value is read
        back from the graph rather than off the reply).
        """
        sdk = sdk_factory()
        node = sdk.create_entity(
            "point", "the ship date holds",
            props={"pointKind": "goal", "goalState": "unmet"})["node"]
        assert sdk._get_point(node["id"])["goalState"] == "unmet", (
            "`goalState` did not survive the container: `tortoise_create_point` "
            "declared it as a parameter (#13110), so a migrating caller's goal "
            "state would be silently dropped by the arm")


def _point_count(sdk) -> int:
    """How many `:Point` nodes the graph holds — the ZERO-MUTATION witness."""
    return sdk._get_proj().g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0]


class TestTheIngestGateStaysClosed:
    """N1 (#13556 review round 2): a MESSAGE vocabulary is not an allow-list.

    `_ENTITY_ITEM_LABELS` is ingest's Phase-1 `entities[]` type ALLOW-LIST, and
    step 3's entity writer has no `point`/`diary` branch. Naming the two new arms
    in that map therefore ACCEPTED a bundle at Phase 1 that step 3 refused with
    `Phase2Error` — after step 2 had already committed the bundle's points, i.e.
    the #2422/#7925 partial-mutation class this module closes in half a dozen
    other places. The arms are named in `create_entity`'s `unknown type` message
    from `_CREATE_ENTITY_TYPE_ARMS` instead, which no gate reads.
    """

    @pytest.mark.parametrize("declared", ["point", "diary"])
    def test_a_point_shaped_entity_item_is_refused_before_any_write(
            self, sdk_factory, declared):
        from tortoise.exceptions import Phase2Error

        sdk = sdk_factory()
        bundle = {"points": [{"content": "a claim in the same bundle"}],
                  "entities": [{"ref": "e1", "type": declared, "name": "y"}]}
        with pytest.raises(ValueError) as exc:
            sdk._ingest(bundle)
        assert not isinstance(exc.value, Phase2Error), (
            f"an entities[] item typed {declared!r} reached step 3, so the points it "
            f"was bundled with were already committed: {exc.value} — Phase 1 must "
            "refuse the type, with zero mutation")
        assert _point_count(sdk) == 0, (
            f"the bundle's points were WRITTEN before the {declared!r} entity item "
            "was refused — the partial-mutation shape #2422/#7925 exists to stop")

    def test_the_container_message_still_names_its_own_arms(self, sdk_factory):
        """…and the fix must not shrink the message the map was widened for."""
        sdk = sdk_factory()
        with pytest.raises(ValueError) as exc:
            sdk.create_entity("nonsense", "x")
        message = str(exc.value)
        for arm in ("point", "diary"):
            assert arm in message, (
                f"`create_entity`'s unknown-type message no longer names type={arm!r}: "
                f"{message!r} — a caller is left without the call that would work")


class TestTheCompletionSignalMovedWithTheDoor:
    """`tortoise_create_point` carried TWO things, and only one was the node.

    The door's handler also fired `_maybe_onboarding_auto_complete(...)` — the
    server-side signal that flips the dashboard's connect step to *Connected* on a
    first write (`tortoise/onboarding/SKILL.md` says so, and the wizard copy's
    "file my first memory" step relies on it). `tortoise_create_entity` did not
    have it, so retiring the door without moving this block would have absorbed
    the write and DROPPED the completion — a silent capability loss of exactly the
    shape #394 recorded when `create_document`'s `_linkSource` half was left
    behind in the move.

    ⛔ These are HANDLER pins, not SDK pins: the observation lives in
    `tortoise/mcp_server.py`, one level above everything `TestTheArmStoresWhatTheDoorStored`
    can see, which is precisely why it was invisible to the arm's own tests.
    """

    @pytest.fixture()
    def observed(self, monkeypatch):
        """Record every observation, and stub the SDK the handler builds.

        `_transport_mode` is set to `http` so `_safe`'s transport gate passes, and
        no org context is set so the quota pre-write short-circuits — the pin is
        about the OBSERVATION, and a quota fixture would hide it behind infra.
        """
        from tortoise.mcp_auth import _transport_mode

        calls: list[dict] = []
        monkeypatch.setattr(mcp_server, "_maybe_onboarding_auto_complete",
                            lambda **kw: calls.append(kw))

        class _FakeSDK:
            def __init__(self, payload):
                self.payload = payload

            def create_entity(self, *_a, **_k):
                if isinstance(self.payload, BaseException):
                    raise self.payload
                return self.payload

        def _install(payload):
            monkeypatch.setattr(mcp_server, "_get_org_sdk",
                                lambda: _FakeSDK(payload))

        token = _transport_mode.set("http")
        try:
            yield calls, _install
        finally:
            _transport_mode.reset(token)

    def test_the_point_arm_observes_a_completed_first_write(self, observed):
        calls, install = observed
        install({"node": {"id": "p1", "pointKind": "statement"}, "nudges": []})
        result = mcp_server.tortoise_create_entity(
            type="point", name="a first memory")
        assert result["node"]["id"] == "p1"
        assert calls == [{"decision_observed": False}], (
            "the point arm did not record the onboarding observation, so a user "
            "who files their first memory through the ADVERTISED call never sees "
            "the dashboard flip to Connected")

    def test_the_diary_arm_does_not_observe(self, observed):
        """⛔ THE FIFTH NEVER-OBSERVED DOOR, and the one a pin USED to call intended.

        `tortoise_diary_write` (`tortoise/mcp_server.py:6258-6265`) is a two-line
        passthrough — no `_maybe_onboarding_auto_complete` anywhere in it — so the
        `type="diary"` arm that absorbs it must not fire the signal either. A
        retirement that OBSERVES where the door did not flips the dashboard to
        *Connected* on a diary entry, which is a product behaviour change, not a
        retirement (#13556 review F2; the block's own invariant, three lines above
        the guard, says the observation may not widen as a side effect).
        """
        calls, install = observed
        install({"node": {"id": "d1", "pointKind": "diary"}, "nudges": []})
        result = mcp_server.tortoise_create_entity(
            type="diary", name="a first entry",
            props={"authoredBy": "agent-1"})
        assert result["node"]["id"] == "d1", result
        assert calls == [], (
            "the diary arm recorded an onboarding observation its retired door "
            f"never made: {calls} — `tortoise_diary_write` has no "
            "`_maybe_onboarding_auto_complete` call at all")

    @pytest.mark.parametrize("kind", sorted(mcp_server.DECISION_SHAPED_POINT_KINDS))
    def test_the_decision_branch_observes_a_persisted_decision(self, observed, kind):
        """The POSITIVE control for the `decision_observed` branch (#13556 F3).

        Every other pin in this class asserts `decision_observed is False`, so a
        mutant that hard-codes `decision_observed=False` reds none of them: the one
        behaviour the block exists to preserve for a migrating decision-writer —
        `decide-completed` on a `decision`/`option` persisted through the ADVERTISED
        door — was unpinned. Both members of `DECISION_SHAPED_POINT_KINDS` are
        exercised, because the branch's whole value is choosing BETWEEN them and
        every other kind.
        """
        calls, install = observed
        install({"node": {"id": "p3", "pointKind": kind}, "nudges": []})
        mcp_server.tortoise_create_entity(type="point", name="we chose A")
        assert calls == [{"decision_observed": True}], (
            f"a persisted {kind!r} did not observe the decision step: {calls} — the "
            "decide-completed signal a migrating decision-writer relied on is lost")

    def test_the_persisted_kind_is_what_observes(self, observed):
        """Asked `option`, stored `statement` — the STORED kind decides (#9287).

        The kind guard can re-route and rewrite a kind, so a handler that observed
        what it ASKED for would record a decision the graph does not hold.
        """
        calls, install = observed
        install({"node": {"id": "p2", "pointKind": "statement"}, "nudges": []})
        mcp_server.tortoise_create_entity(
            type="point", name="not really a decision",
            props={"pointKind": "option"})
        assert calls == [{"decision_observed": False}], (
            "the observation read the REQUESTED kind instead of the persisted one")

    def test_a_non_point_arm_never_observes(self, observed):
        """A retirement may not widen the observation as a side effect."""
        calls, install = observed
        install({"node": {"id": "s1", "subjectKind": "core:product"},
                 "nudges": []})
        for t in ("subject", "object", "event", "document"):
            mcp_server.tortoise_create_entity(type=t, name=f"a {t}")
        assert calls == [], (
            f"a non-point arm recorded an onboarding observation: {calls} — "
            "`tortoise_create_point` observed, `create_subject` never did, and the "
            "container must not flatten that difference")

    def test_the_container_gates_the_retired_doors_tag_quota(self, monkeypatch):
        """#329's cap + value rules followed the door into the container (N2a).

        Both refusals used to live only in `tortoise_create_point`'s handler; a
        container without them accepts an unbounded tag list and non-string tags on
        the ONLY advertised point-write path, i.e. a quota limit failing OPEN.
        Driven through the handler so the assertion covers the wiring, not just the
        helper — and the recording stub proves the refusal happens BEFORE the write
        rather than filtering its result.
        """
        from tortoise.mcp_auth import _transport_mode
        from tortoise.quota import MAX_TAGS_PER_POINT

        asked: list[dict] = []

        class _SDK:
            def create_entity(self, *a, **k):
                asked.append({"args": a, "kwargs": k})
                return {"node": {"id": "p1", "pointKind": "statement"}, "nudges": []}

        monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: _SDK())
        token = _transport_mode.set("http")
        try:
            over = mcp_server.tortoise_create_entity(
                type="point", name="too many tags",
                props={"tags": [f"t{i}" for i in range(MAX_TAGS_PER_POINT + 1)]})
            assert isinstance(over, mcp_server._SafeError), over
            assert over["code"] == mcp_server.ERR_QUOTA, over

            bad = mcp_server.tortoise_create_entity(
                type="point", name="non-string tag", props={"tags": [7]})
            assert isinstance(bad, mcp_server._SafeError), bad
            assert "invalid tag value" in bad["error"], bad

            # the SAME gate on the diary arm: both relay to `create_point`
            diary = mcp_server.tortoise_create_entity(
                type="diary", name="e", props={"authoredBy": "a", "tags": [""]})
            assert isinstance(diary, mcp_server._SafeError), diary

            ok = mcp_server.tortoise_create_entity(
                type="point", name="within the cap", props={"tags": ["fine"]})
            assert not isinstance(ok, mcp_server._SafeError), ok
        finally:
            _transport_mode.reset(token)
        assert len(asked) == 1, (
            f"a refused tag list reached the writer: {asked} — the guard must run "
            "BEFORE the write, not filter its result")

    def test_the_container_records_the_doors_r1_abuse_weight(self, observed):
        """N2b: `record_point_create` must still fire where the write now lives."""
        for t, want in (("point", 1), ("diary", 1), ("subject", 0),
                        ("object", 0), ("event", 0), ("document", 0)):
            got = mcp_server._container_abuse_weight(
                {"node": {}}, (t, "a name"), {})
            assert got == want, (
                f"type={t!r} recorded R1 weight {got}, not {want} — the retired "
                "doors recorded weight 1 for a point and 0 for everything else")
        # a NON-STR type must not crash the metering hook (`_quota_gated` swallows
        # exceptions, so a TypeError here would be a SILENT zero weight)
        assert mcp_server._container_abuse_weight({}, (None,), {}) == 0
        # …and no positional arg at all (a kwargs-only future call) is 0, not 1
        assert mcp_server._container_abuse_weight({}, (), {"type": "point"}) == 0

    def test_the_container_passes_that_weight_to_the_quota_gate(self, monkeypatch):
        """N2b's WIRING, not just the callable: the gate must receive it.

        A weight function nobody passes records nothing — the pre-fix container
        called `_quota_gated(..., "points")` and left `abuse_weight=None`, which is
        exactly the silent loss this pin reds.
        """
        from tortoise.mcp_auth import _transport_mode

        seen: dict = {}

        def _gate(fn, resource="points", abuse_weight=None):
            seen["resource"] = resource
            seen["abuse_weight"] = abuse_weight
            return lambda *a, **k: {"node": {"id": "p1"}, "nudges": []}

        class _SDK:
            def create_entity(self, *a, **k):  # never called: `_gate` is the stub
                raise AssertionError("the stubbed gate was bypassed")

        monkeypatch.setattr(mcp_server, "_quota_gated", _gate)
        monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: _SDK())
        token = _transport_mode.set("http")
        try:
            mcp_server.tortoise_create_entity(type="point", name="n")
        finally:
            _transport_mode.reset(token)
        assert seen.get("abuse_weight") is mcp_server._container_abuse_weight, (
            f"the container did not hand R1 a weight to record: {seen} — "
            "`record_point_create` stops firing for point creation here")

    def test_a_failed_write_never_observes(self, observed):
        calls, install = observed
        install(RuntimeError("the graph refused the write"))
        result = mcp_server.tortoise_create_entity(type="point", name="doomed")
        assert isinstance(result, mcp_server._SafeError), result
        assert calls == [], "a failed write must never record an observation (#3926)"
