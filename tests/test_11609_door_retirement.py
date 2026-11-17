"""#11609 lane E — the doors retire FORWARD, into a container that is live on main.

WHAT THIS FILE GATES, AND WHY IT IS NOT A RESTATEMENT OF THE REGISTRY
--------------------------------------------------------------------
`tortoise/tool_registry.py` can record that a name is retired and which name a caller
should use instead. It cannot record the two things that make the retirement SAFE, and
those are what this file measures:

  1. **The pointer resolves in ONE hop to a LIVE tool.** GitHub's own
     `DeprecatedToolAliases` states the limit (aliases are not transitive), and a
     pointer at another retired name is the churn loop the Tier-1 note in the registry
     forbids. Derived from the registry, not typed: every retired name's pointer must
     begin with a name that is in `TOOL_REGISTRY`.
  2. **The destination actually performs the absorbed operation.** Measured by
     substitution — the container's own door is replaced by a recorder and the call must
     reach it — not by reading a docstring. A container that answers with something else
     is the failure mode this wave exists to avoid: it would delete the door and keep the
     name, with no capability behind either.
     ⛔ ALL SEVEN, and the two shapes differ on purpose:
       · the three READ doors (`get_session`, `list_batch`, `get_cross_lens_candidates`)
         are substituted at their container's own METHOD — the pointer's `type=`/`kind=`
         is what selects the arm, so the read is measured through `tortoise_get_entity` /
         `tortoise_list_knowledge` / `tortoise_review_link_candidates`;
       · the four `create_*` doors share ONE container method (`create_entity`) whose
         `type=` selects a per-label WRITE, so substitution goes one level deeper: the
         test drives `create_entity(type=..., props={"<kind>": ...})` and records the
         `_create_entity` call it lands on. That is the only seam that can catch an arm
         routing `type="subject"` to an OBJECT write, or one that drops the
         `props`-carried kind the pointer advertises — a check against the
         `_ENTITY_ITEM_LABELS` constant alone leaves both green (#13052 review F3).

⛔ AND THE HALF THAT IS A RECORDED DECISION, NOT A CHOICE THIS FILE MAKES
-----------------------------------------------------------------------
Twenty-five live names are absent from the approved 27-target and are STILL LIVE after
this wave, each for a reason already recorded elsewhere. `TestProtectedDoorsStayLive`
asserts the reasons hold in the code — if one stops holding, that door becomes retirable
and this test should be updated IN THE BATCH THAT UNBLOCKS IT, not silently:

  · six READ doors + `check_structure` + `calibrate_summary` declare `-> list[dict]`,
    the array envelope that carries the #8482 `_safe` failure repair
    (`tests/test_11609_read_containers.py::TestWrapEnvelopeDoorsStayLive`, #10914);
  · `issue_insight`, `session_context`, `search` carry a `rest_spec`, so retiring the
    registry entry deletes a public REST route with it;
  · `query` is the PAGED destination two ALREADY-retired pointers name
    (`paginated_query`, `query_points_by_tag`), so retiring it is a re-pointing decision,
    not a door removal;
  · `create_point` / `diary_write` / `compute_confidence` / `recall` have no arm on the
    container the map records: `create_entity` routes subject|object|event|document only,
    `check_confidence` does not recompute a posterior, and it takes no `mode`/`query`
    selector at all, so `recall`'s four-intent dispatch (confidence gate, centrality
    weighting, NAND/mitigation surfacing) is unreachable through it.

  8 (envelope) + 3 (rest_spec) + 1 (paged destination) + 4 (no arm) + 9 (destination not
  an MCP door yet) = 25, and `TestProtectedDoorsStayLive` reads EVERY one of the first
  sixteen back from the code — a name that lands in this list without an assertion behind
  it is the shape the #13052 review caught.

NO ARCHIVE ROW IS EXPECTED HERE. `archive/mcp-tools/` holds doors with NO destination
(owner ruling 2026-11-12, `archive/README.md`); a forwarded name is STILL SERVED, so the
two mechanisms must not hold the same name — `tools/archive_surface.py` enforces that.
"""
from __future__ import annotations

import inspect
import re
from typing import ClassVar

import pytest

from tortoise import mcp_server as ms
from tortoise.sdk import TortoiseSDK
from tortoise.tool_registry import (
    RETIRED_TOOL_REGISTRY,
    RETIRED_USE_INSTEAD,
    TOOL_REGISTRY,
)

#: This wave's seven. name -> the container whose method must be REACHED, and the
#: discriminator the pointer names. The discriminator is the container's OWN dispatch
#: value (`docs/product/bridge-table.md` Part A), never invented here.
RETIRED_THIS_WAVE: dict[str, tuple[str, str]] = {
    "tortoise_get_session": ("get_session", 'type="session"'),
    "tortoise_find_cross_lens_candidates": (
        "get_cross_lens_candidates", 'kind="cross_lens"'),
    "tortoise_list_batch": ("list_batch", 'kind="batch"'),
    "tortoise_create_subject": ("create_entity", 'type="subject"'),
    "tortoise_create_object": ("create_entity", 'type="object"'),
    "tortoise_create_event": ("create_entity", 'type="event"'),
    "tortoise_create_document": ("create_entity", 'type="document"'),
}


def _require_non_empty(what: str, kinds: tuple[str, ...]) -> tuple[str, ...]:
    """A derivation that produced NOTHING reds here rather than passing vacuously.

    Every assertion in this file is parametrized over one of these tuples, and an empty
    tuple makes a parametrization collect zero cases — a green file with nothing behind
    it. So a restructured pointer table or a restructured `create_entity` dispatch is a
    named failure at import, not a silent no-op.
    """
    if not kinds:
        raise RuntimeError(
            f"test_11609_door_retirement: {what} yielded no kinds, so every assertion "
            "built on it would pass vacuously instead of reding — re-derive this pin "
            "against whatever replaced the shape it reads")
    return kinds


#: The kinds the `create_*` doors this wave retires forward `type=` to, read out of the
#: pointer table itself rather than restated, so a door added to `RETIRED_THIS_WAVE` is
#: covered here without a second edit to a hand-kept list. Scoped by CONTAINER, not by the
#: pointer's shape: `tortoise_get_session` also forwards a `type=` (`"session"`), and that
#: one selects a READ arm rather than a write.
RETIRED_CREATE_TYPES: tuple[str, ...] = _require_non_empty(
    "the `create_*` doors' `type=` pointers",
    tuple(match.group(1)
          for container, pointer in RETIRED_THIS_WAVE.values()
          if container == "create_entity"
          and (match := re.fullmatch(r'type="([a-z_]+)"', pointer))),
)


def _create_entity_dispatch_arms() -> tuple[str, ...]:
    """The kinds `TortoiseSDK.create_entity` DISPATCHES on, read from its own arms.

    ⛔ READ FROM THE DISPATCH, NEVER FROM `_ENTITY_ITEM_LABELS` (#13052 review at
    `2c3c9acc` — the second site of the class the round before it closed 100 lines above).
    That map is interpolated into the `unknown type` message and decides NOTHING, which
    was measured both ways: adding a real `elif t == "point":` arm to the dispatch leaves
    it untouched (a false GREEN on exactly the drift these rows exist to catch), and
    adding `"point"` to it alone changes no behaviour (a false RED). The arms are the
    `if t == "<kind>":` / `elif t == "<kind>":` branches at the method's own body indent.
    """
    source = inspect.getsource(TortoiseSDK.create_entity)
    return tuple(
        re.findall(r'^        (?:el)?if t == "([a-z_]+)":$', source, re.MULTILINE))


_CREATE_ENTITY_TYPES: tuple[str, ...] = _require_non_empty(
    "`TortoiseSDK.create_entity`'s dispatch arms", _create_entity_dispatch_arms())

#: Doors this wave deliberately does NOT retire, each with the artefact that says why.
PROTECTED = {
    # `-> list[dict]` + reaches a container: the #8482 envelope exception.
    "tortoise_search_sessions": "list envelope (#8482)",
    "tortoise_suggest_entry_points": "list envelope (#8482)",
    "tortoise_list_batches": "list envelope (#8482)",
    "tortoise_list_namespaces": "list envelope (#8482)",
    "tortoise_diary_read": "list envelope (#8482)",
    "tortoise_expand_relationships": "list envelope (#8482)",
    "tortoise_check_structure": "list envelope (#8482, #10914)",
    "tortoise_calibrate_summary": "list envelope (#8482, #12951)",
    # `rest_spec`: retiring the registry entry deletes the REST route with it.
    "tortoise_issue_insight": "rest_spec GET /v1/issue-insight",
    "tortoise_session_context": "rest_spec GET /v1/context",
    "tortoise_search": "list envelope + rest_spec GET /v1/search",
    # The paged destination of two already-retired pointers.
    "tortoise_query": "paged destination of paginated_query/query_points_by_tag",
    # No arm on the recorded container.
    "tortoise_create_point": "create_entity has no point arm",
    "tortoise_diary_write": "create_entity has no diary arm",
    "tortoise_compute_confidence": "check_confidence does not recompute",
    "tortoise_recall": "check_confidence has no recall arm",
}

#: The other NINE of the registry block's 25: live, off the 27-target, and held for a
#: reason with no PROPERTY to assert — their recorded destination is not live on `main`
#: yet, so there is no container to answer through and nothing that can be measured
#: beyond "still live, still not retired" (which `TestProtectedDoorsStayLive` pins for
#: every name in this table too). ⛔ Kept as a separate table rather than folded into
#: `PROTECTED`, because `PROTECTED`'s contract is a retaining artefact this file READS
#: BACK from the code; a name here has none, and pretending otherwise would make the
#: table's own docstring false. What the split buys: `len(PROTECTED) + len(NO_CONTAINER_YET)
#: == 25` turns the registry's prose enumeration into a checked POPULATION, so a live
#: door dropped from either table reds here instead of surviving as a comment.
NO_CONTAINER_YET = {
    "tortoise_delete": "delete_knowledge not live",
    "tortoise_delete_point": "delete_knowledge not live",
    "tortoise_delete_entity": "delete_knowledge not live",
    "tortoise_create_edge": "link_entities not live",
    "tortoise_create_operator": "link_entities not live",
    "tortoise_events_poll": "poll_events not live",
    "tortoise_org_create": "account tenancy, not on the MCP (#11609 settled 2026-09-22)",
    "tortoise_ingest": "write_knowledge_batch not live",
    "tortoise_list_graphs": "inside `overview`; no container row yet",
}


def _live() -> dict:
    return {t.name: t for t in TOOL_REGISTRY}


def _destination(name: str) -> str:
    """The container `tools/bridge_table.py`'s Part A map records for `name`.

    Read from the generator's own map rather than restated here: every "no arm" reason in
    `PROTECTED` names a container, and that map is where the container is decided — so the
    assertion follows the map instead of a copy of it.
    """
    from tools.bridge_table import DESTINATION

    destination = DESTINATION.get(name)
    assert destination, (
        f"{name} has no recorded destination in `tools/bridge_table.py`'s map, so the "
        "reason this table records for it cannot be asserted against a container — "
        "re-derive it before retiring the door")
    return destination


#: The same first-token read the generators use (`tools/bridge_table.py`'s
#: `_REPLACEMENT_RE`), so this file cannot disagree with the table about what a pointer
#: names.
_POINTER_TARGET = re.compile(r"^([A-Za-z0-9_]+)")


class TestTheSevenAreRetiredForward:
    @pytest.mark.parametrize("name", sorted(RETIRED_THIS_WAVE))
    def test_the_door_left_the_advertised_surface(self, name):
        live = _live()
        assert name not in live, (
            f"{name} is still ADVERTISED — this wave's whole effect is that it is not")
        retired = {t.name: t for t in RETIRED_TOOL_REGISTRY}
        assert name in retired, (
            f"{name} left TOOL_REGISTRY without a RETIRED_USE_INSTEAD row: a caller "
            "would then get 'unknown tool' instead of a warning naming the container")
        assert RETIRED_USE_INSTEAD[name] == retired[name].retired_use_instead

    @pytest.mark.parametrize("name", sorted(RETIRED_THIS_WAVE))
    def test_the_pointer_names_its_container_and_discriminator(self, name):
        pointer = RETIRED_USE_INSTEAD[name]
        _method, discriminator = RETIRED_THIS_WAVE[name]
        assert pointer.startswith("tortoise_"), pointer
        assert discriminator in pointer, (
            f"{name} is pointed at {pointer!r}, which does not name {discriminator!r}: "
            "the container's default arm is a DIFFERENT operation, not a smaller one")

    def test_no_retired_name_is_also_archived(self):
        """Two mechanisms, one name — `tools/archive_surface.py` enforces this too."""
        import json
        from pathlib import Path

        manifest = json.loads(
            (Path(__file__).resolve().parent.parent / "archive" / "MANIFEST.json")
            .read_text(encoding="utf-8"))
        archived = {e["symbol"] for e in manifest["entries"] if e.get("kind") == "mcp-tool"}
        assert not (archived & set(RETIRED_USE_INSTEAD)), (
            "a name is both archived (not served) and retired-forward (still served)")


class TestThePointerIsCallableAtTheContainer:
    """A pointer is a MIGRATION INSTRUCTION, so its arguments must be accepted.

    `test_the_pointer_names_its_container_and_discriminator` asserts the container and
    the discriminator are NAMED; that is satisfiable by a pointer the container would
    reject with `TypeError`, which is the same dead end as no pointer at all. Measured
    failure this class exists for: the four `create_*` pointers named
    `eventKind=`/`documentKind=` as TOP-LEVEL kwargs, and `tortoise_create_entity`
    takes `(type, name, props=None)` — the kinds ride INSIDE `props` (the SDK pops them
    and refuses the write without one). `tortoise_create_subject`/`_object` named the
    kind NOWHERE, which is quieter and worse: `entities.py` defaults it to `'other'`.

    ⛔ Scoped to THIS WAVE's seven — and NOT because a global scan finds a defect, because it
    does not. The reason is mechanical: this assertion attributes every `name=` kwarg to ONE
    target (`_POINTER_TARGET.match`), and a pointer may be COMPOUND. There is exactly one such
    pointer — `tortoise_mine_conversations` names BOTH arms (*"…from_directory(directory=…,
    extract_entities=…) for a corpus — the single-transcript arm is …from_session(conversation=
    <transcript>, source_id=…)"*) — and every named argument is accepted by ITS OWN target
    (`from_directory(directory, extract_entities=True)`, `from_session(conversation=None, …,
    source_id=None, …)`), so the pointer is correct and was adjudicated so at #12984's P2-1.
    Scoping to the seven keeps this assertion's reading of the pointer unambiguous instead of
    teaching it to parse prose; a one-target scan over all 60 pointers is a different change.
    """

    #: The kind each retired `create_*` door REQUIRED, and the container takes inside
    #: `props`. Absent from the pointer, the caller loses the discriminator silently.
    REQUIRED_KIND: ClassVar[dict[str, str]] = {
        "tortoise_create_subject": "subjectKind",
        "tortoise_create_object": "objectKind",
        "tortoise_create_event": "eventKind",
        "tortoise_create_document": "documentKind",
    }

    @pytest.mark.parametrize("name", sorted(RETIRED_THIS_WAVE))
    def test_every_named_argument_is_one_the_container_accepts(self, name):
        """BIND the pointer's own call against the live tool's signature."""
        pointer = RETIRED_USE_INSTEAD[name]
        target = _live()[_POINTER_TARGET.match(pointer).group(1)]
        fn = getattr(ms, target.name, None)
        assert fn is not None, f"{target.name} is advertised but not defined on the server"
        sig = inspect.signature(fn)
        accepts_kwargs = any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        named = re.findall(r"(?:^|[(,]\s*)([A-Za-z_][A-Za-z0-9_]*)=(?!=)", pointer)
        assert named, f"{name}'s pointer names no argument at all: {pointer!r}"
        rejected = [kw for kw in named if kw not in sig.parameters and not accepts_kwargs]
        assert not rejected, (
            f"{name} is pointed at {pointer!r}, which passes {rejected} — "
            f"{target.name}{sig} does not take them, so a migrating caller gets a "
            "TypeError from the pointer that is supposed to migrate them")

    @pytest.mark.parametrize("name,kind", sorted(REQUIRED_KIND.items()))
    def test_the_required_kind_is_carried_where_the_container_reads_it(self, name, kind):
        pointer = RETIRED_USE_INSTEAD[name]
        assert f'"{kind}"' in pointer, (
            f"{name} is pointed at {pointer!r} without {kind!r}: the retired door could "
            "not be called without a kind, and the container's create path DEFAULTS an "
            f"absent {kind} to 'other' (entities.py), so the caller loses the "
            "discriminator with no error")
        assert f'props={{"{kind}": ' in pointer, (
            f"{name}'s {kind} must be INSIDE props — `tortoise_create_entity(type, name, "
            f"props=None)` has no {kind} parameter: {pointer!r}")


class TestEveryPointerResolvesInOneHop:
    def test_every_retired_name_points_at_a_live_tool(self):
        """The property, over the WHOLE population — not only this wave's seven."""
        live = set(_live())
        strays = []
        for name, pointer in RETIRED_USE_INSTEAD.items():
            target = _POINTER_TARGET.match(pointer or "")
            if target is None or target.group(1) not in live:
                strays.append((name, pointer))
        assert not strays, (
            "these pointers name something that is not a LIVE tool, so a migrating caller "
            f"lands on another shim (aliases are not transitive): {strays}")

    def test_the_live_count_moved_strictly_lower(self):
        """#11609's arithmetic: 55 → 48 advertised, with no container row arriving."""
        assert len(TOOL_REGISTRY) == 48, (
            f"live surface is {len(TOOL_REGISTRY)}, not 48 — this wave removes doors only "
            "(no container ships here), so the number can only fall")


class TestTheDestinationPerformsTheAbsorbedOperation:
    """Measured by substitution: the container's door is replaced by a recorder."""

    @pytest.mark.parametrize("kind,door", [
        ("batch", "list_batch"),
    ])
    def test_list_knowledge_batch_reaches_list_batch(self, kind, door, monkeypatch):
        reached: list = []
        sdk = TortoiseSDK.__new__(TortoiseSDK)

        def record(label):
            def _fn(*args, **kwargs):
                reached.append((label, args, kwargs))
                return {"door": label}
            return _fn

        monkeypatch.setattr(TortoiseSDK, door, record(door), raising=True)
        sdk.list_knowledge(kind, id="batch-1")
        assert [r[0] for r in reached] == [door], reached

    def test_review_link_candidates_cross_lens_reaches_its_door(self, monkeypatch):
        reached: list = []
        sdk = TortoiseSDK.__new__(TortoiseSDK)

        def record(label):
            def _fn(*args, **kwargs):
                reached.append((label, args, kwargs))
                return {"door": label}
            return _fn

        monkeypatch.setattr(
            TortoiseSDK, "get_cross_lens_candidates",
            record("get_cross_lens_candidates"), raising=True)
        sdk.review_link_candidates("cross_lens", limit=3)
        assert [r[0] for r in reached] == ["get_cross_lens_candidates"], reached

    def test_get_entity_session_reaches_get_session(self, monkeypatch):
        """The MCP door dispatches `type=` through `tortoise_get`, so measure THERE."""
        reached: list = []
        sdk = TortoiseSDK.__new__(TortoiseSDK)

        def _fn(self_, *args, **kwargs):
            reached.append((args, kwargs))
            return {"session": args[0] if args else None}

        monkeypatch.setattr(TortoiseSDK, "get_session", _fn, raising=True)
        monkeypatch.setattr(ms, "_get_org_sdk", lambda: sdk)
        # `_safe`'s auth gate refuses when the transport mode is unset (fail-closed,
        # #236) — this case measures DISPATCH, not auth, so name the mode.
        monkeypatch.setattr(ms, "_is_dev_mode", lambda: True)
        token = ms._transport_mode.set("stdio")
        try:
            assert ms.tortoise_get_entity("s-1", type="session") == {"session": "s-1"}
        finally:
            ms._transport_mode.reset(token)
        assert reached == [(("s-1",), {})], reached

    @pytest.mark.parametrize("entity_type,label,event_type,kind_key", [
        ("subject", "Subject", "SubjectAdded", "subjectKind"),
        ("object", "Object", "ObjectRegistered", "objectKind"),
        ("event", "Event", "EventRecorded", "eventKind"),
        ("document", "Document", "DocumentCreated", "documentKind"),
    ])
    def test_a_create_door_lands_on_its_own_arm_and_carries_its_kind(
            self, entity_type, label, event_type, kind_key, monkeypatch):
        """Substitute the write the container performs — for all four `create_*` doors.

        ⛔ THE ASSERTION PAIR A `_ENTITY_ITEM_LABELS` MEMBERSHIP CHECK CANNOT MAKE. The
        pointer advertises `tortoise_create_entity(type="subject", name=...,
        props={"subjectKind": ...})`; two ways that advertisement can be a lie while the
        registry, the pointer and the label constant all stay green:

          1. the `type="subject"` arm writes an OBJECT (wrong label, wrong journal);
          2. the kind never reaches the write — `subjectKind` is dropped from the props
             bag, which is the silent `'other'` default `_entity_door_props`' docstring
             was written to stop (and for `event`/`document` a hard refusal instead).

        Measured at the ONE seam every arm funnels through (`_create_entity`) rather than
        at the graph: the container is `TortoiseSDK.__new__` with its persistence and
        advisory legs stubbed, so this asserts DISPATCH and the prop bag, never storage.
        """
        reached: list[tuple] = []
        sdk = TortoiseSDK.__new__(TortoiseSDK)

        def record(_self, _label, id_val, props, _event_type, **_kwargs):
            reached.append((_label, dict(props), _event_type))
            return {"id": id_val, "label": _label}

        # Every stub below is a leg this test does NOT measure, and each needs a live
        # graph: identity minting, the nudge query, the provenance advisory, the
        # projection object the event branch builds before it writes.
        monkeypatch.setattr(TortoiseSDK, "_create_entity", record, raising=True)
        monkeypatch.setattr(TortoiseSDK, "_entity_identity_for",
                            lambda _self, _l, _n, explicit_id=None: "fixed-id",
                            raising=True)
        monkeypatch.setattr(TortoiseSDK, "_get_proj",
                            lambda _self: type("P", (), {"g": None})(), raising=True)
        monkeypatch.setattr(TortoiseSDK, "_nudge_candidates",
                            lambda _self, _name, exclude_ids=None: [], raising=True)
        monkeypatch.setattr(TortoiseSDK, "_provenance_advisory",
                            lambda _self, _node, _props, entity_type=None: None,
                            raising=True)

        sdk.create_entity(entity_type, "Ada", props={kind_key: "person"})

        assert len(reached) == 1, f"one create produced {len(reached)} writes: {reached}"
        got_label, got_props, got_event = reached[0]
        assert (got_label, got_event) == (label, event_type), (
            f"create_entity(type={entity_type!r}) wrote a {got_label!r} ("
            f"{got_event!r}), not a {label!r} ({event_type!r}) — the retired door is "
            "pointed at an arm that creates a DIFFERENT entity")
        assert got_props.get(kind_key) == "person", (
            f"create_entity(type={entity_type!r}, props={{{kind_key!r}: 'person'}}) "
            f"reached the write as {got_props!r}: the kind is not in the bag, so the "
            "pointer's discriminator is dropped (subject/object fold it to 'other'; "
            "event/document refuse) and a migrating caller loses the kind silently")

    def test_the_derived_kinds_are_the_ones_the_doors_forward(self):
        """Both derivations agree, so neither is a hand-kept list that can rot alone.

        `RETIRED_CREATE_TYPES` comes from the pointer table and `_CREATE_ENTITY_TYPES`
        from `create_entity`'s dispatch arms; an arm added for a kind no door forwards,
        or a door forwarding a kind the container no longer routes, is the difference.
        """
        assert set(_CREATE_ENTITY_TYPES) == set(RETIRED_CREATE_TYPES), (
            f"`create_entity` dispatches {sorted(_CREATE_ENTITY_TYPES)} while the doors "
            f"this wave retires forward {sorted(RETIRED_CREATE_TYPES)} — one of the two "
            "moved, and the retirement is only safe while they are the same set")

    @pytest.mark.parametrize("entity_type", RETIRED_CREATE_TYPES)
    def test_create_entity_routes_every_retired_create_type(self, entity_type):
        """The arm exists for every `create_*` door this wave retires.

        Both halves are read from `create_entity`'s OWN DISPATCH ARMS — the branch that
        actually decides the write — so a hand-copied tuple here cannot drift from it and
        a new arm cannot pass this file by hand. ⛔ `point` is asserted ABSENT on purpose:
        when an arm for it lands, `tortoise_create_point` becomes retirable and this
        assertion is the tripwire that says so.

        ⛔ `_ENTITY_ITEM_LABELS` is deliberately NOT read here (#13052 review at
        `2c3c9acc`, corrected at `32c041df`): it decides nothing, so an assertion against
        it is blind to a real dispatch arm AND reds on a map-only edit that changes no
        behaviour. The door itself is driven for `point` by its sibling below.
        """
        arms = _create_entity_dispatch_arms()
        assert entity_type in arms, (
            f"create_entity no longer dispatches type={entity_type!r} "
            f"(arms {sorted(arms)}), so the door it absorbs had no container to answer "
            "through")
        assert "point" not in arms, (
            "create_entity now has a `point` arm — `tortoise_create_point` can retire in "
            "that batch (see RETIRED_USE_INSTEAD's lane-E note)")

    def test_an_unknown_create_type_still_refuses(self):
        """The container is a closed set — the door it replaced was one too."""
        sdk = TortoiseSDK.__new__(TortoiseSDK)
        with pytest.raises(ValueError) as exc:
            sdk.create_entity("nonsense", "x")
        assert "nonsense" in str(exc.value)


class TestProtectedDoorsStayLive:
    """A recorded decision, asserted against the code that makes it true.

    ⛔ WHAT IS PINNED, AND WHAT IS ONLY ENUMERATED. The registry's `#11609 lane E` block
    names 25 live doors off the 27-target. SIXTEEN of them carry a retaining PROPERTY,
    and EVERY ONE is read back from the code below — none is a name that only appears in
    the `PROTECTED` dict, which is the weaker treatment this class reserves for
    `NO_CONTAINER_YET` (#13052 review F3):

      · 8 envelope doors — the `-> list[dict]` annotation, from `inspect.signature`;
      · 3 `rest_spec` doors — the registry entry's own `rest_spec`;
      · 1 paged destination (`tortoise_query`) — the two retired pointers that name it,
        and the paging parameters those pointers and the live door agree on;
      · 4 "no arm on the recorded container" doors — the container itself: the two
        write doors are CALLED with the kind and must still refuse it (that is
        `create_entity`'s dispatch branch; its `_ENTITY_ITEM_LABELS` map only
        interpolates into the `unknown type` message and decides nothing, so an edit to
        it adds no arm and a check on it was green with an arm present); the two
        `check_confidence` doors are measured from its parameter SET.

    The other NINE (`NO_CONTAINER_YET`) have no live container at all, so the only thing
    to measure is "still live, still not retired" — pinned below for them too. The two
    tables together are what make the enumeration checkable as a POPULATION; neither
    alone is 25 of 25.
    """

    def test_the_two_tables_cover_the_enumerated_population_and_do_not_overlap(self):
        live = _live()
        assert len(PROTECTED) == 16, (
            f"PROTECTED holds {len(PROTECTED)}: the registry block names 16 doors with a "
            "retaining property — if a door gained or lost one, move it in the batch that "
            "changed the code, not to wherever a green run lands")
        assert len(NO_CONTAINER_YET) == 9, (
            f"NO_CONTAINER_YET holds {len(NO_CONTAINER_YET)}: nine names are held because "
            "their recorded destination is not live — a container landing moves one")
        both = set(PROTECTED) & set(NO_CONTAINER_YET)
        assert not both, f"a door is in both tables, which is a contradiction: {both}"
        population = set(PROTECTED) | set(NO_CONTAINER_YET)
        assert len(population) == 25, (
            f"the two tables cover {len(population)} names, not the 25 live names off the "
            "27-target that the registry block enumerates — a live door dropped from both "
            "is a door with NO recorded reason to stay live")
        missing = sorted(n for n in population if n not in live)
        assert not missing, (
            f"these are named as protected but are no longer live: {missing} — if they "
            "were retired, a recorded decision was reversed; reopen it instead")
        retired = sorted(n for n in population if n in RETIRED_USE_INSTEAD)
        assert not retired, f"protected doors must not carry a retirement pointer: {retired}"

    @pytest.mark.parametrize("name", sorted(PROTECTED))
    def test_the_door_is_live_and_not_retired(self, name):
        assert name in _live(), (
            f"{name} is not advertised. If you retired it, you reversed a recorded "
            f"decision ({PROTECTED[name]}) — reopen that decision instead; the batch that "
            "unblocks it updates this table in the same change")
        assert name not in RETIRED_USE_INSTEAD, name

    @pytest.mark.parametrize("name", [
        "tortoise_search_sessions", "tortoise_suggest_entry_points",
        "tortoise_list_batches", "tortoise_list_namespaces", "tortoise_diary_read",
        "tortoise_expand_relationships", "tortoise_check_structure",
        "tortoise_calibrate_summary",
    ])
    def test_the_list_envelope_is_still_what_keeps_it_live(self, name):
        annotation = inspect.signature(
            getattr(ms, name), eval_str=True).return_annotation
        assert annotation == list[dict], (
            f"{name} declares {annotation!r}: the array schema is the #8482 repair, so a "
            "non-list envelope means the reason it stayed live is gone — update the "
            "protected table in the batch that gives its container an envelope")

    @pytest.mark.parametrize("name", [
        "tortoise_issue_insight", "tortoise_session_context", "tortoise_search",
    ])
    def test_the_rest_route_is_still_what_keeps_it_live(self, name):
        entry = _live()[name]
        assert getattr(entry, "rest_spec", None) is not None, (
            f"{name} no longer carries a `rest_spec`, so retiring the entry no longer "
            "deletes a REST route — it may be retirable in that batch")

    @pytest.mark.parametrize("name,kind", [
        ("tortoise_create_point", "point"),
        ("tortoise_diary_write", "diary"),
    ])
    def test_create_entity_still_has_no_arm_for_the_write(self, name, kind):
        """`create_entity`'s "arm" is a DISPATCH branch, so its absence is DRIVEN.

        ⛔ NOT read from `_ENTITY_ITEM_LABELS` (#13052 review, round 6): that constant is
        interpolated into the `unknown type` message and decides nothing, which was
        measured both ways — adding `"point"` to it alone leaves `create_entity` still
        refusing the type (a false RED), and adding a real `elif t == "point":` arm to
        the dispatch leaves the map untouched (a false GREEN, on the exact drift this row
        exists to catch). So the DOOR is called with the kind and must still refuse it:
        an arm landing is precisely the call that stops raising.
        """
        destination = _destination(name)
        assert destination == "create_entity", (
            f"{name} is recorded as landing on {destination!r}, not `create_entity` — the "
            "reason this table records for it cannot be asserted against that container")
        # DID NOT RAISE here IS the drift: `create_entity` accepted the kind, so the door
        # has an arm and its recorded reason no longer holds — re-check whether it is
        # retirable in the batch that added the arm, and move it out of this table there.
        with pytest.raises(ValueError, match="unknown type") as refused:
            TortoiseSDK.__new__(TortoiseSDK).create_entity(kind, "x")
        assert kind in str(refused.value), (
            f"`create_entity` refused {kind!r} with {str(refused.value)!r}, which does not "
            "name the kind: the refusal this row reads is no longer the arm check")

    @pytest.mark.parametrize("name,forbidden", [
        ("tortoise_compute_confidence", ("recompute", "posterior")),
        ("tortoise_recall", ("mode", "query")),
    ])
    def test_the_recorded_container_still_has_no_arm_for_it(self, name, forbidden):
        """The "no arm" reason, asserted against the CONTAINER rather than as prose.

        This is the one category whose retaining property is an ABSENCE, and an absence
        stated only in a comment is exactly what an arm landing later leaves behind —
        `recall` would then look protected while its container could serve it. `forbidden`
        names the parameters whose ARRIVAL on the container would open an arm; measured
        from the door's own signature, so a `mode=`/`recompute=` selector cannot be added
        without reddening this row.
        """
        destination = _destination(name)
        door = destination if destination.startswith("tortoise_") else f"tortoise_{destination}"
        container = getattr(ms, door, None)
        assert container is not None, (
            f"{name}'s recorded container {door} is no longer a door, so the reason this "
            "table records for it cannot be asserted — re-derive it before retiring")
        params = set(inspect.signature(container).parameters)
        arrived = sorted(set(forbidden) & params)
        assert not arrived, (
            f"{door} now accepts {arrived}, so {name!r}'s recorded reason "
            f"({PROTECTED[name]}) no longer holds — it may serve the operation now. "
            "Re-check whether it is retirable in the batch that added the arm")

    def test_a_dropped_destination_is_a_named_assertion(self, monkeypatch):
        """`_destination()`'s assert is only reachable on the DRIFT path, so pin it here.

        Every name in both tables has a map row, so on the green path that assert never
        runs: reverting it to a bare `DESTINATION[name]` would leave the suite green and
        hand the next lane a `KeyError` naming neither the map nor the reason — the
        failure #13052 review round 6 filed (P3). `_destination` imports `DESTINATION` by
        identity, so deleting from the module's own dict reaches it.
        """
        from tools import bridge_table

        monkeypatch.delitem(bridge_table.DESTINATION, "tortoise_recall")
        with pytest.raises(AssertionError, match="has no recorded destination"):
            _destination("tortoise_recall")

    def test_query_is_still_the_paged_destination_of_two_retired_pointers(self):
        """`tortoise_query`'s reason is a ROLE, so it is read back from the pointers.

        The recorded reason is not a property of the door alone: it is that two ALREADY
        retired names point at it with paging arguments. Measured both ways — the pointers
        still name `tortoise_query` in one hop, and the live door still accepts the paging
        parameters the pointers advertise, so re-pointing the shim or dropping paging
        reddens here instead of silently orphaning two retired names.
        """
        pointers = {
            retired_name: RETIRED_USE_INSTEAD[retired_name]
            for retired_name in ("tortoise_paginated_query", "tortoise_query_points_by_tag")
        }
        for retired_name, pointer in pointers.items():
            target = pointer.split("(", 1)[0].strip()
            assert target == "tortoise_query", (
                f"{retired_name} points at {pointer!r}, not at `tortoise_query` — the paged "
                "destination this table protects has moved, so tortoise_query's recorded "
                f"reason ({PROTECTED['tortoise_query']}) no longer holds")
            assert "offset=" in pointer and "limit=" in pointer, (
                f"{retired_name}'s pointer {pointer!r} no longer names the paging "
                "arguments it was migrated onto")
        params = set(inspect.signature(ms.tortoise_query).parameters)
        missing = sorted({"offset", "limit"} - params)
        assert not missing, (
            f"`tortoise_query` no longer accepts {missing}: it is protected as the PAGED "
            f"destination ({PROTECTED['tortoise_query']}) and both retired pointers name "
            "that paging — re-point them in the batch that removes it")
