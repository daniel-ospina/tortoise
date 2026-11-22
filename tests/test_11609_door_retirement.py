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
     ⛔ ALL THIRTEEN, and the shapes differ on purpose:
       · the three READ doors (`get_session`, `list_batch`, `get_cross_lens_candidates`)
         are substituted at their container's own METHOD — the pointer's `type=`/`kind=`
         is what selects the arm, so the read is measured through `tortoise_get_entity` /
         `tortoise_list_knowledge` / `tortoise_review_link_candidates`;
       · the six doors on `create_entity` (five `create_*` — subject, object, event,
         document, point — plus `diary_write`) share ONE container method whose
         `type=` selects a per-label WRITE, so substitution goes one level deeper: the
         test drives `create_entity(type=..., props={"<kind>": ...})` and records the
         `_create_entity` call it lands on. That is the only seam that can catch an arm
         routing `type="subject"` to an OBJECT write, or one that drops the
         `props`-carried kind the pointer advertises — a check against the
         `_ENTITY_ITEM_LABELS` constant alone leaves both green (#13052 review F3);
       · the four doors lane P6 retired are TWO shapes, and neither is either of those.
         The `rest_spec` pair (`issue_insight`, `session_context`) is substituted at
         their container's own METHOD (`issue_insight` / `session_context`), reached
         through the kept door's keyword discriminator —
         `tortoise_search_knowledge(mode="issue_insight")` /
         `tortoise_check_confidence(session=True)`; the DELETE pair is a different seam
         again — `tortoise_delete` is ONE kept entry whose LABEL dispatch must route a
         Point to `delete_point` and anything else to `delete_entity`, so the recorder
         sits on the per-label METHOD, and the pointer's `dry_run=True` is the
         preview-then-confirm SELECTOR (a bare `tortoise_delete(id)` is a PREVIEW, not a
         delete) — measuring the entry alone would read green for a routing that deletes
         the wrong kind.

     THREE + SIX + FOUR = THIRTEEN, and each group is a different seam:
     `test_the_table_holds_the_thirteen_doors_the_prose_names` reads BOTH the count and the
     per-CONTAINER histogram off the table, so this paragraph is a claim the table is
     checked against, not a second tally kept in prose.

⛔ AND THE HALF THAT IS A RECORDED DECISION, NOT A CHOICE THIS FILE MAKES
-----------------------------------------------------------------------
Seventeen live names are absent from the approved 27-target and are STILL LIVE
after this wave, each for a reason already recorded elsewhere.
`TestProtectedDoorsStayLive` asserts the reasons hold in the code — if one stops
holding, that door becomes retirable and this test should be updated IN THE BATCH
THAT UNBLOCKS IT, not silently:

  · six READ doors + `check_structure` + `calibrate_summary` declare `-> list[dict]`,
    the array envelope that carries the #8482 `_safe` failure repair
    (`tests/test_11609_read_containers.py::TestWrapEnvelopeDoorsStayLive`, #10914);
  · `search` carries a `rest_spec` (`GET /v1/search`) AND a list envelope — but lane P4
    MEASURED the coupling FALSE, for `search` as much as for the pairs (no product module
    derives a route from a registry row; the route is a hand-written decorator in
    `hosted_api.py`, and `docs/architecture/11609-STRUCTURAL-BLOCKERS.md` §1.5 records the
    COUPLING as "aspirational, not real" — the CELL is a recorded fact about a route that
    exists independently). ⛔ So what ACTUALLY holds `search` is the
    `list[dict]` envelope, not the route — do not restore "retiring the entry deletes a REST
    route" as a live reason. `issue_insight` and `session_context` carried a `rest_spec` too
    and left in lane P6, the batch that acted on that measurement;
  · `query` is the PAGED destination two ALREADY-retired pointers name
    (`paginated_query`, `query_points_by_tag`), so retiring it is a re-pointing decision,
    not a door removal;
  · `create_point` / `diary_write` — RETIRED by #11609 lane P1, the batch that gave
    `create_entity` its `type="point"` / `type="diary"` arms; and `compute_confidence` /
    `recall` — RETIRED by #11609 lane P2, the batch that gave `check_confidence` its
    `recompute=True` / `recall=True` arms. Both pairs were held by the SAME kind of
    reason, an ABSENCE at the recorded container, and each left the table in the change
    that made that absence FALSE — which is the contract this file states: a door may
    leave the held list only in the batch that makes its stated reason untrue, with a
    test behind it, not silently and not in two steps.

  9 (envelope: the six READ doors, `check_structure`, `calibrate_summary`, and `search` — whose
  `rest_spec` cell is a RECORD, not a retention reason, and whose own annotation is
  `-> list[dict]`) + 0 (rest_spec as a retention reason: measured FALSE, §1.5) + 1 (paged
  destination) + 0 (no arm) + 2 (destination not an MCP door — `org_create`, `ingest`) = 12,
  and
  `TestProtectedDoorsStayLive` reads EVERY protected name back from the code — a name that
  lands in this list without an assertion behind it is the shape the #13052 review caught.

⛔ THE COUNT WAS 25 UNTIL #11609 LANE P1, 23 UNTIL LANE P2, 21 UNTIL LANE P6, 19 AFTER THAT
LANE'S `rest_spec` PAIR, 17 AFTER ITS DELETE PAIR, AND **12 AT THIS HEAD** — #11609 lane P7
retired FIVE more forward in the batch that REGISTERED their destinations: `tortoise_delete`
(→ `tortoise_delete_knowledge`), `tortoise_create_edge` / `tortoise_create_operator`
(→ `tortoise_link_entities`), `tortoise_events_poll` (→ `tortoise_poll_events`), and
`tortoise_list_graphs` (→ `tortoise_graph_overview(section="graphs")`, whose handler had
dispatched to that very body since #4282). `create_point` and
`diary_write` were two of those twenty-five, held by ONE reason each — "create_entity has
no point arm" / "…no diary arm". That is the reason this file's own contract is written
around: a door may leave `PROTECTED` only in the batch that makes its stated reason FALSE,
and lane P1 built the two arms (`create_entity` now dispatches `type="point"` and
`type="diary"`) and retired both doors in the same change. `compute_confidence` and
`recall` were two of the remaining twenty-three on the same terms, and lane P2 built
their arms (`check_confidence(recompute=True)` / `check_confidence(recall=True)`) and
retired both. `issue_insight` and `session_context` were two of the remaining twenty-one
and left on the same terms in lane P6: their recorded reason (`rest_spec` — *the row IS the
REST route*) was MEASURED FALSE by lane P4, and P6 is the batch that acts on that
measurement. `delete_point` and `delete_entity` were two of the remaining nineteen and left
in the same lane, on the owner's #9660 ruling: the canonical row is executed by KEEPING ONE
`tortoise_delete` entry and widening what it deletes, so `delete_knowledge` — the
destination both were held against — was never going to arrive as a separate row. **P7 is
the batch that gave that kept entry the target's own spelling** (`tortoise_delete_knowledge`)
and retired `tortoise_delete` onto it, so the ruling's shape and the target's name are now
the same thing. The
arithmetic moved with each wave: 4 (no arm) became 2 became 0, and the population 25 became
23 became 21 became 19 became 17 became 12 — a count left at 19 would have been a table claiming two
names that are no longer live, which is the rot the population check exists to red. ⛔ THIS
PROSE SAID 19/`NINE` WHILE THE ASSERTIONS BELOW READ 17/`SEVEN` after #13626 merged — the
assertions were right and the prose was pre-DELETE-pair, which is the shape a lane
"restores" to a red (#13626 review ISSUE 4).

NO ARCHIVE ROW IS EXPECTED HERE. `archive/mcp-tools/` holds doors with NO destination
(owner ruling 2026-11-12, `archive/README.md`); a forwarded name is STILL SERVED, so the
two mechanisms must not hold the same name — `tools/archive_surface.py` enforces that.
"""
from __future__ import annotations

import inspect
import re
from collections import Counter
from typing import ClassVar

import pytest

from tortoise import mcp_server as ms
from tortoise.sdk import TortoiseSDK
from tortoise.tool_registry import (
    RETIRED_TOOL_REGISTRY,
    RETIRED_USE_INSTEAD,
    TOOL_REGISTRY,
)

#: This wave's doors. name -> the container whose method must be REACHED, and the
#: discriminator the pointer names. The discriminator is the container's OWN dispatch
#: value (`docs/product/bridge-table.md` Part A), never invented here.
#: ⛔ THREE WAVES SHARE THIS TABLE. `tests/test_11609_door_retirement.py` was written for
#: lane E's seven; #11609 lane P1 added `tortoise_create_point` and
#: `tortoise_diary_write` to it in the change that BUILT the arms they were waiting for
#: (`create_entity`'s `type="point"`/`type="diary"`), and lane P6 added four more in two
#: units (the `rest_spec` pair, then the DELETE pair). Every assertion here is
#: parametrized over this table, so a door added here is covered without a second edit.
RETIRED_THIS_WAVE: dict[str, tuple[str, str]] = {
    "tortoise_get_session": ("get_session", 'type="session"'),
    "tortoise_find_cross_lens_candidates": (
        "get_cross_lens_candidates", 'kind="cross_lens"'),
    "tortoise_list_batch": ("list_batch", 'kind="batch"'),
    "tortoise_create_subject": ("create_entity", 'type="subject"'),
    "tortoise_create_object": ("create_entity", 'type="object"'),
    "tortoise_create_event": ("create_entity", 'type="event"'),
    "tortoise_create_document": ("create_entity", 'type="document"'),
    # #11609 lane P1 — the two POINT-shaped doors, retired by the batch that gave
    # `create_entity` its arms. `tortoise_create_point` forwards its KIND, which the
    # arm defaults to `statement` when unsent; `tortoise_diary_write` pins `diary`.
    "tortoise_create_point": ("create_entity", 'type="point"'),
    "tortoise_diary_write": ("create_entity", 'type="diary"'),
    # #11609 lane P6 — the `rest_spec` pair, retired by the batch that ACTED on lane P4's
    # measurement that the reason was false (`docs/architecture/11609-STRUCTURAL-BLOCKERS.md`
    # §1.5 / §2.2): no route is derived from a registry row, so the row going deletes no
    # route. Each pointer names the container ARM that dispatches to the very method the
    # door ran, so a migrating caller keeps the whole call.
    "tortoise_issue_insight": ("search_knowledge", 'mode="issue_insight"'),
    "tortoise_session_context": ("check_confidence", 'session=True'),
    # #11609 lane P6 — the two DELETE doors. Their `NO_CONTAINER_YET` reason was
    # "`delete_knowledge` not live", and the owner's #9660 ruling executes that row by
    # KEEPING the single `tortoise_delete` entry and widening it: `TortoiseSDK.delete`
    # dispatches by LABEL, so the kept entry carries both doors' capability plus the
    # preview-then-confirm protocol. The discriminator asserted here is the protocol
    # selector — a bare `tortoise_delete(id)` is a PREVIEW, not a delete.
    "tortoise_delete_point": ("delete", 'dry_run=True'),
    "tortoise_delete_entity": ("delete", 'dry_run=True'),
}

#: The `3 + 6 + 4` the module docstring states, in the table's OWN vocabulary: each key is a
#: `container` value and each value is how many doors retire through it.
#: ⛔ A HISTOGRAM, NOT ONLY A TOTAL (#13651 review F2). The size pin alone left the
#: `container` field with no reader for ten of the thirteen rows — the only other reader is
#: `RETIRED_CREATE_TYPES`, which filters `create_entity` — so a mutant that reassigns one
#: non-`create_entity` container (e.g. `tortoise_session_context` → `get_session`) kept the
#: total at 13, kept that door's pointer and discriminator intact, and left EVERY row in this
#: file green while the docstring's "THREE + SIX + FOUR" became 4 + 6 + 3. The container IS
#: the seam each group is defined by: the READ trio (one container each), the SIX doors that
#: share `create_entity`'s single method, and the lane-P6 quartet (the `rest_spec` pair and
#: the DELETE pair).
RETIRED_THIS_WAVE_BY_CONTAINER: dict[str, int] = {
    "get_session": 1,
    "get_cross_lens_candidates": 1,
    "list_batch": 1,
    "create_entity": 6,
    "search_knowledge": 1,
    "check_confidence": 1,
    "delete": 2,
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
    That map is ingest's PHASE-1 `entities[]` TYPE ALLOW-LIST (and the label map the
    endpoint gate reads), so it is NOT the dispatch: adding a real arm to
    `create_entity` leaves it untouched (a false GREEN on exactly the drift these rows
    exist to catch), and adding a key to it alone opens a validation gate rather than a
    writer branch (a false RED — and, at #13556 review N1, a real partial-mutation bug
    when `point`/`diary` were added there for a MESSAGE; the message now reads
    `_CREATE_ENTITY_TYPE_ARMS`). The arms are the `if t == "<kind>":` /
    `elif t == "<kind>":` branches at the method's own body indent.

    ⛔ BOTH ACCEPTED SHAPES ARE READ (added by #11609 lane P1): a single arm
    `if t == "point":`, and a COMBINED arm `elif t == "point" or t == "diary":` — two
    kinds that share one body because the bodies would be identical, which is what ruff's
    SIM114 requires of a dispatch that has nothing to decide between them. Reading only
    the single-arm shape would silently drop `point` and `diary` from the derivation, and
    the set-equality pin below would then red the whole wave for a syntax it merely did
    not parse (measured: the combined form derived `['document', 'event', 'object',
    'subject']`).

    ⛔ STATED LIMIT (not a gap a future reviewer should re-derive): the arms must stay on
    ONE line at the body indent. A reflow that splits `if t == "subject":` across two
    lines drops that arm from the derivation — which the set-equality pin below reds, so
    the failure is a named FALSE RED on an unrelated refactor, never a false green. The
    durable alternative is for `create_entity` to carry its arms as DATA; that is a change
    to a hot write path for the sake of a test, and it is deliberately not made here.
    """
    source = inspect.getsource(TortoiseSDK.create_entity)
    arms: list[str] = []
    for line in source.splitlines():
        match = _DISPATCH_ARM_LINE.match(line)
        if match:
            arms.extend(_DISPATCH_ARM_KIND.findall(match.group(1)))
    return tuple(arms)


#: One dispatch arm at `create_entity`'s body indent, in either accepted shape — see the
#: function docstring's "BOTH ACCEPTED SHAPES ARE READ".
_DISPATCH_ARM_LINE = re.compile(r'^ {8}(?:el)?if ((?:t == "[a-z_]+"(?: or )?)+):$')

#: Every kind named inside a matched arm, so an `or` chain contributes each of its kinds.
_DISPATCH_ARM_KIND = re.compile(r't == "([a-z_]+)"')


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
    # `rest_spec`: the CELL is still recorded, but the coupling it used to stand for —
    # "retiring the registry entry deletes the REST route with it" — is MEASURED FALSE for
    # every door that ever claimed it, `search` included (§1.5 of
    # `docs/architecture/11609-STRUCTURAL-BLOCKERS.md`): no product module derives a route
    # from a registry row, and the routes are hand-written decorators that reach the SDK
    # directly, so the row going deletes nothing on the wire. ⛔ So `search` is held by its
    # `list[dict]` ENVELOPE, not by the route; its `rest_spec` cell is a recorded fact about
    # a route that exists independently, never a retention reason. `issue_insight` /
    # `tortoise_session_context` sat here on the false reason and left in #11609 lane P6.
    "tortoise_search": "list envelope (#8482); its rest_spec cell (GET /v1/search) is a RECORD, not a reason",
    # The paged destination of two already-retired pointers.
    "tortoise_query": "paged destination of paginated_query/query_points_by_tag",
    # No arm on the recorded container.
    # ⛔ `tortoise_create_point` / `tortoise_diary_write` USED to sit here (held by two
    # ABSENCES at `create_entity`: "has no point arm" / "has no diary arm") and left in
    # #11609 lane P1; `tortoise_compute_confidence` / `tortoise_recall` used to sit here
    # (held by "`check_confidence` does not recompute" / "has no recall arm") and left in
    # #11609 lane P2. Each pair retire FORWARD in `RETIRED_USE_INSTEAD` in the batch that
    # grew the arms. Do not restore them without first removing those arms.
}

#: The other TWO of the registry block's 12: live, off the 27-target, and held for a
#: reason with no PROPERTY to assert — their recorded destination is not a live MCP door,
#: so there is no container to answer through and nothing that can be measured beyond
#: "still live, still not retired" (which `TestProtectedDoorsStayLive` pins for every name
#: in this table too). ⛔ Kept as a separate table rather than folded into `PROTECTED`,
#: because `PROTECTED`'s contract is a retaining artefact this file READS BACK from the
#: code; a name here has none, and pretending otherwise would make the table's own
#: docstring false. What the split buys:
#: `len(PROTECTED) + len(NO_CONTAINER_YET) == 12` turns the registry's prose enumeration
#: into a checked POPULATION, so a live door dropped from either table reds here instead of
#: surviving as a comment.
NO_CONTAINER_YET = {
    # ⛔ `tortoise_delete_point` / `tortoise_delete_entity` USED to sit here (held by
    # ``delete_knowledge not live``) and left in #11609 lane P6: the owner's #9660 ruling
    # executes that row by KEEPING one delete entry and widening it, so the destination is
    # live under the kept entry's own name and both doors retire FORWARD.
    #
    # ⛔ FIVE MORE LEFT IN #11609 LANE P7 — the lane that REGISTERED their destinations:
    # `tortoise_delete` (→ `tortoise_delete_knowledge`), `tortoise_create_edge` and
    # `tortoise_create_operator` (→ `tortoise_link_entities`), `tortoise_events_poll`
    # (→ `tortoise_poll_events`), and `tortoise_list_graphs` (→
    # `tortoise_graph_overview(section="graphs")`, which had dispatched to that very body
    # since #4282 — so "no container row yet" was already false). Each left in the change
    # that made its reason false, which is the whole contract of `PROTECTED`.
    #
    # The two below are the residue, and neither is retirable by this lane:
    #   · `tortoise_org_create` — `tenancy:create_memory_graph` is an SDK/REST operation and
    #     the 2026-09-22 settlement keeps tenancy off the MCP, so there is NO MCP
    #     destination to forward to. Removing it is an ARCHIVE batch (no destination), and
    #     it is not this lane's: `tests/test_13036_capstone_v2.py` records it in
    #     `UNDRIVEN` as "excluded from tenant HTTP by design" (its own doctrine: an
    #     UNDRIVEN row is recorded, never passed), and `tortoise/supabase_control.py`
    #     documents it as the MCP entry point.
    #   · `tortoise_ingest` — `write_knowledge_batch` exists only on the SDK (so no MCP
    #     pointer can name it), and the MCP door has a LIVE product caller:
    #     `tortoise/curation.py`'s `IngestWriter` (`TOOL = "tortoise_ingest"`) writes through
    #     it against a hosted deployment. Archiving the door breaks that writer, so it is a
    #     separate batch that converts the caller first. ⛔ Not a "blocked forever": the
    #     owner's 2026-11-12 ruling removes everything outside the target, and this is the
    #     named precondition for doing it.
    "tortoise_org_create": "account tenancy, not on the MCP (#11609 settled 2026-09-22)",
    "tortoise_ingest": "write_knowledge_batch is SDK-only; a live hosted caller (curation.IngestWriter) must move first",
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


class TestTheWaveIsRetiredForward:
    def test_the_table_holds_the_thirteen_doors_the_prose_names(self):
        """The count AND its decomposition are PINNED, so neither can rot silently.

        ⛔ WHY A COUNT PIN AND NOT JUST THE PROSE (#13651 review F2). Every other
        assertion in this file is parametrized OVER the table, so the table's SIZE was
        read by nothing: the module docstring says "ALL THIRTEEN" and
        `TestProtectedDoorsStayLive` subtracts four pairs from 25, and either sentence
        would have stayed green through a door being added or dropped. That is the same
        defect class as a count chain naming the wrong set — the prose reads verified
        while nothing checks it. The shapes in the docstring are 3 + 6 + 4, so a door
        joining or leaving the wave moves this pin in the change that does it.

        ⛔ AND WHY THE HISTOGRAM, NOT ONLY THE TOTAL (same review, one level down): the size
        pin closes "thirteen doors" but says nothing about WHICH seam each one retires
        through, so the decomposition in the sentence above was a tally kept in prose and
        checked by nothing. The container is that seam, and pinning its histogram is what
        makes the claim true — see `RETIRED_THIS_WAVE_BY_CONTAINER`.
        """
        assert len(RETIRED_THIS_WAVE) == 13, (
            f"RETIRED_THIS_WAVE holds {len(RETIRED_THIS_WAVE)} doors, not the THIRTEEN "
            "this wave's prose enumerates (3 read + 6 on `create_entity` + 4 in lane "
            "P6) — a door added or dropped moves the count AND its shapes paragraph in "
            "the same change, not silently")
        histogram = Counter(
            container for container, _ in RETIRED_THIS_WAVE.values())
        assert dict(histogram) == RETIRED_THIS_WAVE_BY_CONTAINER, (
            f"the wave's per-container histogram is "
            f"{dict(sorted(histogram.items()))}, not "
            f"{dict(sorted(RETIRED_THIS_WAVE_BY_CONTAINER.items()))}: the docstring's "
            "THREE + SIX + FOUR is read off these containers (one READ container each, "
            "SIX doors on `create_entity`, the lane-P6 quartet), so a row whose container "
            "is reassigned moves the group counts even while the total stays at THIRTEEN")

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

    ⛔ Scoped to THIS FILE's thirteen — and NOT because a global scan finds a defect, because it
    does not. The reason is mechanical: this assertion attributes every `name=` kwarg to ONE
    target (`_POINTER_TARGET.match`), and a pointer may be COMPOUND. There is exactly one such
    pointer — `tortoise_mine_conversations` names BOTH arms (*"…from_directory(directory=…,
    extract_entities=…) for a corpus — the single-transcript arm is …from_session(conversation=
    <transcript>, source_id=…)"*) — and every named argument is accepted by ITS OWN target
    (`from_directory(directory, extract_entities=True)`, `from_session(conversation=None, …,
    source_id=None, …)`), so the pointer is correct and was adjudicated so at #12984's P2-1.
    Scoping to the thirteen keeps this assertion's reading of the pointer unambiguous instead of
    teaching it to parse prose; a one-target scan over all 68 pointers is a different change.
    """

    #: The kind each retired `create_*` door REQUIRED, and the container takes inside
    #: `props`. Absent from the pointer, the caller loses the discriminator silently.
    #: ⛔ `tortoise_create_point` is here for the SAME reason the four entity doors
    #: are, and its silent loss is quieter than theirs: an unsent `objectKind` files the
    #: entity as `'other'`, while an unsent `pointKind` files the claim as `statement` — a
    #: REAL kind, indistinguishable from one the caller chose. `tortoise_diary_write` is
    #: deliberately ABSENT: its kind is `diary` by construction (its arm REFUSES a
    #: contradicting `pointKind`), so there is no discriminator for a caller to lose.
    REQUIRED_KIND: ClassVar[dict[str, str]] = {
        "tortoise_create_subject": "subjectKind",
        "tortoise_create_object": "objectKind",
        "tortoise_create_event": "eventKind",
        "tortoise_create_document": "documentKind",
        "tortoise_create_point": "pointKind",
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
        """The property, over the WHOLE population — not only this file's thirteen."""
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
        """#11609's arithmetic: 55 → 48 → 46 → 44 → 40 advertised, no container row arriving.

        Each retiring wave removes doors into arms its container had to grow for them
        (#11609 P1: `create_entity`'s `point`/`diary`; P2: `check_confidence`'s
        `recompute=True` / `recall=True`), so the count can only fall and no container is
        added. The chain is the sequence of pins the merged trees actually carried, one
        step per wave that landed on its own — 48 minus 2 = 46 (P1, #13556), 46 minus 2 = 44
        (P2, #13550) — and then 44 minus 4 = 40, because P6's two pairs (`rest_spec`,
        then DELETE) merged TOGETHER in one commit (`73c26022db`). No
        `len(TOOL_REGISTRY) == 42` pin ever existed, so 42 is not a surface this chain may
        claim as advertised.
        """
        assert len(TOOL_REGISTRY) == 39, (
            f"live surface is {len(TOOL_REGISTRY)}, not 39 — every wave removes doors "
            "into arms on a container that already existed, and #11609 lane P7 registered "
            "the FOUR approved target names that had no entry, so the chain is "
            "44 − 4 (P6's two pairs) = 40, then 40 + 4 (P7's registrations) − 5 "
            "(P7's forward retirements: create_edge, create_operator, events_poll, "
            "delete, list_graphs) = 39")


class TestTheDestinationPerformsTheAbsorbedOperation:
    """Measured by substitution: the container's door is replaced by a recorder.

    ⛔ THE NAME TO SUBSTITUTE IS THE **PRIVATE** SEAM. #12901's final batch privatised the
    whole retired set (`list_batch` → `_list_batch`, `issue_insight` → `_issue_insight`,
    `session_context` → `_session_context`, `delete_point` → `_delete_point`, …), and the
    container arms now dispatch to those `_`-prefixed `def`s. `monkeypatch.setattr(...,
    raising=True)` is the point of these rows: it reds when the name the arm reaches stops
    existing, which is exactly what the privatisation did to the public spelling.
    """

    @pytest.mark.parametrize("kind,door", [
        ("batch", "_list_batch"),
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
            TortoiseSDK, "_get_cross_lens_candidates",
            record("get_cross_lens_candidates"), raising=True)
        sdk.review_link_candidates("cross_lens", limit=3)
        assert [r[0] for r in reached] == ["get_cross_lens_candidates"], reached

    @pytest.mark.parametrize("tool,arm_kwargs,door", [
        ("tortoise_search_knowledge", {"title": "t", "mode": "issue_insight"},
         "_issue_insight"),
        ("tortoise_check_confidence", {"session": True}, "_session_context"),
    ])
    def test_a_rest_spec_door_lands_on_the_method_it_retired_into(
            self, tool, arm_kwargs, door, monkeypatch):
        """Lane P6's pair: the container ARM reaches the very method the door ran.

        ⛔ WHY THIS IS THE ROW THE BATCH OWES. The two doors left `PROTECTED` on a
        measured finding (`docs/architecture/11609-STRUCTURAL-BLOCKERS.md` §1.5: no route
        is derived from a registry row). That finding only makes the retirement SAFE if
        the container arm answers what the door answered — otherwise the door's removal
        deletes a capability and keeps a pointer to something a migrating caller cannot
        use. Substituting the SDK METHOD is the seam: `search_knowledge(mode="issue_insight")`
        dispatches to `issue_insight`, `check_confidence(session=True)` to
        `session_context` — so a container that rerouted either arm reds HERE instead of
        shipping a pointer that resolves to a different operation.

        The MCP door is driven (not the SDK method directly) because the MCP door is what
        a migrating caller's client actually calls, and `_safe`'s auth gate is stubbed to
        dev mode for the same reason `test_get_entity_session_reaches_get_session` does:
        this measures DISPATCH, not auth.
        """
        reached: list[tuple] = []
        sdk = TortoiseSDK.__new__(TortoiseSDK)

        def _fn(_self, *args, **kwargs):
            reached.append((args, kwargs))
            return {"door": door}

        monkeypatch.setattr(TortoiseSDK, door, _fn, raising=True)
        monkeypatch.setattr(ms, "_get_org_sdk", lambda: sdk)
        monkeypatch.setattr(ms, "_is_dev_mode", lambda: True)
        token = ms._transport_mode.set("stdio")
        try:
            assert getattr(ms, tool)(**arm_kwargs) == {"door": door}
        finally:
            ms._transport_mode.reset(token)
        assert len(reached) == 1, f"one call produced {len(reached)} reads: {reached}"

    def test_the_kept_delete_entry_covers_both_absorbed_kinds(self, monkeypatch):
        """Lane P6's delete pair: `TortoiseSDK.delete` IS the destination for both doors.

        ⛔ WHY THIS ROW EXISTS. `tortoise_delete_point` / `tortoise_delete_entity` left
        `NO_CONTAINER_YET` on the owner's #9660 ruling, which executes the `delete_knowledge`
        row by KEEPING `tortoise_delete` and widening it. That is only truthful if the kept
        entry really covers BOTH kinds — so the LABEL dispatch is measured here: a Point
        resolves to `delete_point`, anything else to `delete_entity`. A kept entry that
        routed every id to one of them would delete the wrong thing (or nothing) for the
        other door's callers, while the pointer, the registry and the ruling all read fine.

        The MCP door is driven for the token path too, so the pointer's
        `confirmation=<token>` is proven to reach the same `sdk.delete`.
        """
        reached: list[tuple[str, str]] = []
        sdk = TortoiseSDK.__new__(TortoiseSDK)

        for label, method in (("Point", "_delete_point"), ("Object", "_delete_entity")):
            monkeypatch.setattr(
                TortoiseSDK, "_get_proj",
                lambda _self, _label=label: type(
                    "P", (),
                    {"_resolve_entity": staticmethod(
                        lambda *_a, _l=_label, **_k: [{"label": _l}])})(),
                raising=True)
            monkeypatch.setattr(
                TortoiseSDK, method,
                lambda _self, _id, _m=method: reached.append((_m, _id)) or True,
                raising=True)
            assert TortoiseSDK._delete(sdk, f"id-{label}") is True, label

        assert reached == [("_delete_point", "id-Point"),
                           ("_delete_entity", "id-Object")], reached

        # …and the MCP door's confirmation path lands on that same `sdk.delete`.
        monkeypatch.setattr(ms, "_get_org_sdk", lambda: sdk)
        monkeypatch.setattr(
            TortoiseSDK, "_delete",
            lambda _self, _id: reached.append(("delete", _id)) or True, raising=True)
        monkeypatch.setattr(ms, "_is_dev_mode", lambda: True)
        token = ms._deletion.issue("id-x")
        monkeypatch.setattr(ms._deletion, "verify",
                            lambda *_a, **_k: {"t": "id-x"})
        # `_safe`'s auth gate refuses when the transport mode is unset (#236) — this
        # measures DISPATCH, not auth, so name the mode as the sibling rows do.
        mode = ms._transport_mode.set("stdio")
        try:
            assert ms.tortoise_delete("id-x", confirmation=token) == {
                "deleted": True, "id": "id-x"}
        finally:
            ms._transport_mode.reset(mode)
        assert reached[-1] == ("delete", "id-x"), reached

    def test_get_entity_session_reaches_get_session(self, monkeypatch):
        """The MCP door dispatches `type=` through `tortoise_get`, so measure THERE."""
        reached: list = []
        sdk = TortoiseSDK.__new__(TortoiseSDK)

        def _fn(self_, *args, **kwargs):
            reached.append((args, kwargs))
            return {"session": args[0] if args else None}

        monkeypatch.setattr(TortoiseSDK, "_get_session", _fn, raising=True)
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
        # ⛔ The `object` arm resolves its target BEFORE the write (the #3590 adopt
        # path), so `resolve_or_mint` is a SECOND identity-minting leg — the
        # docstring's "identity minting" is two entry points, not one. It takes the
        # live graph, so leaving it unstubbed made this case reach
        # `entity_identity.py`'s `g.query` with the `g=None` below and raise
        # `AttributeError: 'NoneType' object has no attribute 'query'` instead of
        # measuring dispatch. `(None, True)` is the "no carrier, create it" answer,
        # which is what a door creating a NEW entity is asking; `_create_entity`
        # stays the measured seam.
        from tortoise import entity_identity as _entity_identity
        monkeypatch.setattr(_entity_identity, "resolve_or_mint",
                            lambda _g, _label, _name, explicit_id=None: (None, True),
                            raising=True)
        monkeypatch.setattr(TortoiseSDK, "_get_proj",
                            lambda _self: type("P", (), {"g": None})(), raising=True)
        monkeypatch.setattr(TortoiseSDK, "_nudge_candidates",
                            lambda _self, _name, exclude_ids=None: [], raising=True)
        monkeypatch.setattr(TortoiseSDK, "_provenance_advisory",
                            lambda _self, _node, _props, entity_type=None: None,
                            raising=True)
        # ⛔ THE OBJECT ARM'S IDENTITY LEG IS `resolve_or_mint`, NOT
        # `_entity_identity_for`, and stubbing only the latter left this row RED on
        # `main` (#11609 lane P1 found it there and reproduced it at
        # `501270a4bf`): the arm reached `entity_identity._name_holder_ids` with the
        # `g=None` this test hands `_get_proj`, and raised
        # `AttributeError: 'NoneType' object has no attribute 'query'` — a stub set
        # that went stale when #12869 gave the arm its `resolve_or_mint` leg. It is a
        # FALSE RED (the arm dispatches correctly), which is the worst kind: it hides
        # the real failure this row exists to catch. Patched at the MODULE attribute
        # because the arm imports the name inside its own body, at call time.
        from tortoise import entity_identity

        monkeypatch.setattr(entity_identity, "resolve_or_mint",
                            lambda _g, _label, _name, explicit_id=None: ("fixed-id", True))

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
        a new arm cannot pass this file by hand. ⛔ `point` used to be asserted ABSENT
        here, as the tripwire that said "an arm has landed, `tortoise_create_point` is now
        retirable". It FIRED: #11609 lane P1 built `type="point"` and `type="diary"` and
        retired both doors in the same change, so the assertion is gone and the kinds it
        guards for are in `RETIRED_THIS_WAVE` instead — where the set-equality test below
        holds the two derivations together.

        ⛔ `_ENTITY_ITEM_LABELS` is deliberately NOT read here (#13052 review at
        `2c3c9acc`, corrected at `32c041df`): it decides nothing, so an assertion against
        it is blind to a real dispatch arm AND reds on a map-only edit that changes no
        behaviour. ⛔ THE ARMS ARE NOT DECLARED AS DATA, and this derivation's stated
        limit still holds: each arm must stay on ONE line at the method's body indent. The
        two POINT-shaped arms satisfy that by keeping their shared body in
        `_absorbed_point_write` — a combined `elif t in (...)` would have been invisible
        here, i.e. a false RED on a live arm.
        """
        arms = _create_entity_dispatch_arms()
        assert entity_type in arms, (
            f"create_entity no longer dispatches type={entity_type!r} "
            f"(arms {sorted(arms)}), so the door it absorbs had no container to answer "
            "through")

    def test_an_unknown_create_type_still_refuses(self):
        """The container is a closed set — the door it replaced was one too."""
        sdk = TortoiseSDK.__new__(TortoiseSDK)
        with pytest.raises(ValueError) as exc:
            sdk.create_entity("nonsense", "x")
        assert "nonsense" in str(exc.value)


class TestProtectedDoorsStayLive:
    """A recorded decision, asserted against the code that makes it true.

    ⛔ WHAT IS PINNED, AND WHAT IS ONLY ENUMERATED. The registry's `#11609 lane E` block
    named 25 live doors off the 27-target and four PAIRS have left it since — the chain
    `25 -> 23 -> 21 -> 19 -> 17` the block itself states. TWO (`create_point`, `diary_write`)
    were retired by #11609 lane P1 in the change that gave `create_entity` their arms, TWO
    (`compute_confidence`, `recall`) by lane P2 (#13550) in the change that gave
    `check_confidence` its `recompute=True` / `recall=True` arms, and TWO more
    (`issue_insight`, `session_context`) plus the LAST TWO (`delete_point`, `delete_entity`)
    by lane P6 — the first pair on lane P4's measurement that the `rest_spec` reason was
    false, the second on the #9660 ruling that keeps `tortoise_delete` and widens it — so
    25 − 2 − 2 − 2 − 2 = 17, and the population is 17 and TEN
    of them carry a retaining PROPERTY, and EVERY ONE is read back from the code below —
    none is a name that only appears in the `PROTECTED` dict,
    which is the weaker treatment this class reserves for `NO_CONTAINER_YET` (#13052
    review F3):

      · 9 envelope doors — the `-> list[dict]` annotation, from `inspect.signature` (this is
        also `search`'s real reason: its `rest_spec` cell is a record, never a gate — §1.5);
      · 0 `rest_spec` doors — the category is EMPTY on this tree, because the coupling is
        measured FALSE for every door that ever claimed it;
      · 1 paged destination (`tortoise_query`) — the two retired pointers that name it,
        and the paging parameters those pointers and the live door agree on;
      · 0 "no arm on the recorded container" doors — the category is EMPTY, and that is
        the interesting state, not an omission. It held FOUR absences, two per container,
        and each pair left in the batch that made its absence false: #11609 P1 built
        `create_entity`'s `type="point"` / `type="diary"` arms (the doors were CALLED
        with their kind and had to still refuse it — that is `create_entity`'s dispatch
        branch, not its `_ENTITY_ITEM_LABELS` map, which is ingest's Phase-1 allow-list
        and carries no `point`/`diary` key at all: #13556 review N1), and #11609 P2 built
        `check_confidence`'s `recompute=True` / `recall=True` arms. Both departures are
        asserted the other way round, by
        `test_the_door_retired_only_because_the_container_grew_the_arm` (P2's pair) and
        by `test_a_create_door_lands_on_its_own_arm_and_carries_its_kind` (P1's pair),
        which prove the reason false AT the container instead of trusting the table's
        silence.

    The other TWO (`NO_CONTAINER_YET`) have no live container at all, so the only thing
    to measure is "still live, still not retired" — pinned below for them too. The two
    tables together are what make the enumeration checkable as a POPULATION; neither
    alone is 12 of 12.
    """

    def test_the_two_tables_cover_the_enumerated_population_and_do_not_overlap(self):
        live = _live()
        assert len(PROTECTED) == 10, (
            f"PROTECTED holds {len(PROTECTED)}: the registry block names 10 doors with a "
            "retaining property — if a door gained or lost one, move it in the batch that "
            "changed the code, not to wherever a green run lands")
        assert len(NO_CONTAINER_YET) == 2, (
            f"NO_CONTAINER_YET holds {len(NO_CONTAINER_YET)}: two names are held because "
            "their destination is not a live MCP door — `tortoise_org_create` (tenancy) "
            "and `tortoise_ingest` (SDK-only destination, live hosted caller). A "
            "container landing, or an archive batch moving its caller, moves one")
        both = set(PROTECTED) & set(NO_CONTAINER_YET)
        assert not both, f"a door is in both tables, which is a contradiction: {both}"
        population = set(PROTECTED) | set(NO_CONTAINER_YET)
        assert len(population) == 12, (
            f"the two tables cover {len(population)} names, not the 12 live names off the "
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
        "tortoise_calibrate_summary", "tortoise_search",
    ])
    def test_the_list_envelope_is_still_what_keeps_it_live(self, name):
        annotation = inspect.signature(
            getattr(ms, name), eval_str=True).return_annotation
        assert annotation == list[dict], (
            f"{name} declares {annotation!r}: the array schema is the #8482 repair, so a "
            "non-list envelope means the reason it stayed live is gone — update the "
            "protected table in the batch that gives its container an envelope")

    @pytest.mark.parametrize("name", [
        "tortoise_search",
    ])
    def test_the_rest_spec_cell_is_still_recorded_though_the_route_holds_nothing(self, name):
        """The CELL persists; the ROUTE is not what retains the door (§1.5).

        The assertion is unchanged — `search` still carries `rest_spec` — but the name and
        the message must not teach the measured-false mechanism. ⛔ The coupling is FALSE
        (the route is a hand-written decorator; no product module derives one from a row),
        so removing this cell is NOT a retirement gate: what keeps `search` live is its
        `list[dict]` envelope, pinned by `test_the_list_envelope_is_still_what_keeps_it_live`
        (this file, which now covers `search`) and by
        `tests/test_11609_structural_blockers.py::test_the_search_door_is_held_by_the_envelope_not_the_route`.
        """
        entry = _live()[name]
        assert getattr(entry, "rest_spec", None) is not None, (
            f"{name} no longer carries a `rest_spec` cell. That is a RECORD change, not a "
            "retirement gate — the route does not hold the door; its `list[dict]` envelope "
            "does (see §1.5). Re-derive why the cell moved before retiring anything")

    # ⛔ THE `no arm` CATEGORY IS EMPTY, AND BOTH HALVES OF ITS DEPARTURE ARE ASSERTED
    # FROM THE CODE ABOVE, not from this table's silence:
    #   · #11609 P1 retired `create_point` / `diary_write` when `create_entity` grew their
    #     arms — covered by `test_a_create_door_lands_on_its_own_arm_and_carries_its_kind`
    #     and `test_create_entity_routes_every_retired_create_type`, which drive
    #     `create_entity(type="point"|"diary")` and record the `_create_entity` write it
    #     reaches. ⛔ NOT read from `_ENTITY_ITEM_LABELS` (#13052 review, round 6 / #13556
    #     review N1): that constant is interpolated into the `unknown type` message and
    #     decides nothing, so adding `"point"` to it alone leaves `create_entity` refusing
    #     the type (a false RED) and adding a real arm leaves the map untouched (a false
    #     GREEN, on the exact drift the row existed to catch).
    #   · #11609 P2 retired `compute_confidence` / `recall` when `check_confidence` grew
    #     `recompute=True` / `recall=True` — the row below.
    @pytest.mark.parametrize("name,arm,selector", [
        ("tortoise_compute_confidence", "recompute", ("factors", "evidence", "anchors")),
        ("tortoise_recall", "recall", ("query", "mode")),
    ])
    def test_the_door_retired_only_because_the_container_grew_the_arm(
            self, name, arm, selector):
        """The retired half of the ``no arm`` category: reason FALSE, measured.

        These two doors were held by an ABSENCE ("`check_confidence` does not recompute" /
        "has no recall arm"). #11609 P2 added the arms, so the doors retire FORWARD — and
        the obligation that comes with it is the one this row discharges: the reason must
        be provably FALSE at the container, not merely re-worded in the table. The arm is
        read from the door's own SIGNATURE (so an arm deleted later reddens here), from
        its ``RETIRED_USE_INSTEAD`` pointer (so a name retired without its arm is caught),
        and from its ABSORBED ARGUMENTS (so an arm whose parameters were dropped does not
        pass by name alone — the migrating caller would lose the arguments the door took).
        """
        assert name not in PROTECTED, (
            f"{name} is still in PROTECTED, whose reason is now false — a door leaves that "
            "table in the SAME batch that makes its stated reason untrue")
        assert name not in _live(), f"{name} is still advertised, so it did not retire"
        pointer = RETIRED_USE_INSTEAD[name]
        assert f"{arm}=True" in pointer, (
            f"{name} is pointed at {pointer!r}, which does not select the {arm} arm: the "
            "container's DEFAULT is a different operation, so the pointer would return "
            "something else with no error")
        params = set(inspect.signature(ms.tortoise_check_confidence).parameters)
        assert arm in params, (
            f"`tortoise_check_confidence` no longer accepts {arm}=, so {name}'s "
            "retirement points at an arm that does not exist")
        missing = sorted(set(selector) - params)
        assert not missing, (
            f"`tortoise_check_confidence` no longer accepts {missing} — the {arm} arm "
            f"cannot carry the arguments {name} took, so a migrating caller loses them")
        SDK_PARAMS = set(inspect.signature(TortoiseSDK.check_confidence).parameters)
        assert {arm, *selector} <= SDK_PARAMS, (
            f"the SDK container does not carry {{{', '.join(sorted({arm, *selector} - SDK_PARAMS))}}} "
            f"— {name}'s absorption stopped at the MCP door, so the two layers disagree")

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


class TestEveryReaderNamesTheLiveListingDoor:
    """A retirement is not done while a READER still instructs the retired spelling.

    #11609 lane P7 retired `tortoise_list_graphs` FORWARD into
    `tortoise_graph_overview(section="graphs")`. The registry, the shim and the bridge
    table all say so — but a doc is a reader too, and it is the reader a person ACTS on.
    Measured at the reviewed head (P7 review, P2): the provisioning how-to still told the
    reader to call the retired name in two places, one of them a troubleshooting row whose
    entire purpose is to name the call that fixes the error.

    ⛔ This is NOT a "grep the docs for every retired name" guard, and it must not become
    one: `docs/product/bridge-table.md` deliberately lists all 73, and a global scan would
    have to be taught the difference between a MIGRATION TABLE and an INSTRUCTION. It is
    scoped to the documents whose every `tortoise_*` mention is a call, which is what makes
    the assertion exact instead of aspirational.
    """

    #: Documents whose every `tortoise_*` mention is an instruction to CALL it.
    CALL_INSTRUCTION_DOCS: ClassVar[tuple[str, ...]] = (
        "docs/how-to/provision-a-graph-per-end-user.md",
    )

    #: ⛔ WHAT THIS GUARD READS: MARKDOWN STRUCTURE, NOT ENGLISH (#14237, rounds 2-5).
    #:
    #: The first version of this guard asked "is this mention labelled as a retirement?"
    #: and answered it with a sentence splitter, a label word, and three widening patches.
    #: Every round found a new way to defeat that question, because no punctuation table
    #: answers a question about English:
    #:
    #:   r2  `…retired forward into it.)`        `(?=\s)` missed terminator + `)`
    #:   r3  `….**` / `.*` / `` ` `` / `>`       the closer set was hand-picked
    #:   r4  `is not retired` / `(now retired)`  ANY "retired" in the segment counted
    #:   r5  a fenced ``` call under a note      a fence was not a boundary at all
    #:
    #: Measured at 27187627ea6f, every r4 form was still ACCEPTED (false green) and the
    #: same correct sentence scored differently on `;` vs `—`. Each patch was correct and
    #: the class still leaked, so the question is replaced by two a document answers about
    #: itself:
    #:
    #:   R1  a retired name written as a CALL — the name immediately followed by `(` — is
    #:       a copyable command, so it may not appear at all. No prose talks it back in.
    #:   R2  every other mention is a NOTE, and a note must name the recorded REPLACEMENT
    #:       (`RETIRED_USE_INSTEAD[name]`) inside the same markdown BLOCK. A note that
    #:       gives the reader only the old name is a dead end; an instruction does not
    #:       name the successor, so it reds here without reading a word of English.
    #:
    #: **OVERRIDES:** sentence segmentation's industry default does NOT split at `;` (NLTK
    #: Punkt's `sentbreak` is `.`/`?`/`!` only) — and this guard now does not split at `;`
    #: either, because it does not read SENTENCES at all. The unit is the markdown BLOCK (and
    #: within a table, the CELL) that a reader acts on: five review rounds measured that every
    #: sentence-level unit — the `SENTENCE_BREAK` table, the closer run, the label word — could
    #: be defeated by a form of English the table had not enumerated. ⛔ THE EARLIER `;`-CLAUSE
    #: OVERRIDE ON THIS SAME GUARD IS SUPERSEDED, not narrowed: with no splitter there is no
    #: `;` boundary left to defend, and the property it protected (a note in one clause must
    #: not clear an instruction in the next) is met structurally by the block/cell unit. The
    #: departure from the standard that REMAINS is this one — a structural unit wins over the
    #: linguistic one — and it is recorded here rather than in the deleted comment, so the next
    #: reader holding a sentence-segmentation library's documentation finds the ruling instead
    #: of an accident.
    #:
    #: ⛔ AND A SECOND, NARROWER DEPARTURE: A TABLE **CELL** IS TREATED AS A BLOCK. The GFM
    #: tables extension makes the table a leaf block and states that block-level elements
    #: cannot occur inside a cell (read from the GFM spec's block model), so a spec-faithful
    #: unit would be the ROW — and a ROW is what `blocks()` emitted first. Measured: with the
    #: row as the unit, a migration note in one cell cleared a BARE instruction in the next
    #: (the round-2 fail-open, re-entered through the new rule). Splitting on `|` can only
    #: SHORTEN a unit, so it errs exclusively toward a false red on a correct row — the
    #: fail-closed direction this guard has declared since round 1 — and it is pinned by
    #: `test_a_table_cell_is_its_own_block` (both halves: the instruction cell reds, the note
    #: cell passes). A pipe inside an inline code span is a known limitation of any pipe split
    #: and would only ever SPLIT further, i.e. still the same direction.
    #:
    #: ⛔ R1 ALONE WOULD BE VACUOUS, AND THAT IS MEASURED, NOT FEARED. The defect this
    #: guard exists for — commit b4da1f5c01: "or from the MCP tool
    #: **`tortoise_list_graphs`**, which answers a metadata summary", and the table row
    #: "the MCP tool `tortoise_list_graphs` lists graph **names**" — was written **BARE**,
    #: with no `(`, so a call-form-only rule would have passed both. R2 catches the shape
    #: that was actually shipped; R1 catches the copyable spelling R2 cannot see.
    #:
    #: A BLOCK is a markdown unit and it is deterministic: a fenced code block, a table
    #: row's CELLS, a heading (including a `---`/`===` setext underline), a blockquote
    #: line, a list item with its indented continuation, or a blank-line-delimited
    #: paragraph. Three consequences are deliberate and pinned below — a fence is its OWN
    #: block (a code sample cannot borrow the replacement named in the note above it); a
    #: hard-wrapped prose line is NOT a boundary (this doc wraps at ~80 columns, so a bare
    #: newline inside a paragraph must not split it); and a table row is scoped to its
    #: CELLS, not the row, because a row has no sentence terminator and a note in one cell
    #: would otherwise clear an instruction in the next.
    #:
    #: ⚠️ DECLARED RESIDUALS — the bounds of the rule, named so the next reader learns them
    #: from here rather than from a regex (#14237 r7 review P2: this comment used to promise
    #: "a table row" while the code split cells, and to name two residuals where there are
    #: three). The nested-sub-item over-allow that round 6 declared here is NO LONGER one:
    #: an indented line carrying a list marker is its own block
    #: (`test_a_nested_list_item_is_its_own_block`), so only a marker-less indented line
    #: continues the item:
    #:   * a retired name followed by an inline delimiter run and a bracket IS a call, so a
    #:     NOTE spelled `name (retired)` also reds R1 — the safe (fail-closed) direction, and
    #:     the price of catching `` `name`(args) ``, which RENDERS as a call;
    #:   * a PROSE line containing a `|` is read as a table row and split into cells;
    #:   * a retired name followed by a NEWLINE then `(` is not a call (horizontal rule);
    #:     a call split across a line break is therefore accepted.
    #:
    #: ⚠️ SCOPE: the documents whose every `tortoise_*` mention is a call, and ONE of
    #: them today. The rule is doc-agnostic, but applying it repo-wide would red on docs
    #: that carry retired call forms right now, which is a repair of its own — **#14291**
    #: (filed from the round-5 review's P2, with the six measured lines: `tortoise_recall(`
    #: / `tortoise_update_point(` / `tortoise_create_point(` in `docs/INGEST_CONTRACT.md`,
    #: `tortoise_mine_conversations(` in `docs/quickstart-selfhosted.md`), not this guard.
    #: Naming the issue rather than saying "a follow-up" is deliberate: an unnamed
    #: follow-up is how a class gets declared closed while it is still open.
    REPLACEMENT_NAME = re.compile(r"tortoise_[a-z0-9_]+")

    #: A retired name in call form: the name followed by an OPENING BRACKET, optionally
    #: after HORIZONTAL space and/or an inline-markdown delimiter RUN (backtick, `*`, `_`,
    #: `~`). `name(args)`, `name (args)` and `` `name`(args) `` all RENDER as a call a
    #: reader can copy, and the last one defeated an earlier form of this rule outright
    #: (#14237 r7 review P1, measured: `` `tortoise_list_graphs`(id=x) ``,
    #: `**`tortoise_list_graphs`**(id=x)` and `_`tortoise_list_graphs`_(id=x)` were all
    #: ACCEPTED while the same call with the paren inside the code span was caught).
    #:
    #: HORIZONTAL only — a newline is never a call — and the cost is named in the class
    #: comment: a NOTE spelled `name (retired)` now also reds R1.
    CALL_FORM = re.compile(r"(?:[ \t]*[`*_~]+)*[ \t]*\(")

    @classmethod
    def is_call_form(cls, text: str, at: int) -> bool:
        """True when the retired name ENDING at ``at`` is written as a CALL."""
        return cls.CALL_FORM.match(text, at) is not None

    @staticmethod
    def blocks(text: str) -> list[tuple[int, int, str]]:
        """Split markdown into ``(start, end, block)`` units — structure, not punctuation.

        A fence (``` or ~~~) is one block including its markers, so a code sample is
        never joined to the prose around it. A table row is split into its CELLS (a row
        has no sentence terminator, so a note in one cell would otherwise clear an
        instruction in the next), and a row is recognised by the PIPE it must CONTAIN
        rather than by a LEADING pipe — GFM does not require the leading pipe, and with
        the leading form a pipe-less table collapsed into ONE paragraph, so the cells
        were never separated and the whole table became a single block (#14237 r6
        review P1, measured: pipe-less -> 1 block / ACCEPTED, leading pipes -> cells /
        REJECTED, for identical content). A HEADING is its own block; a LIST ITEM is its
        own block and KEEPS its indented continuation lines, which markdown puts inside
        the same item. Everything else accumulates into a blank-line-delimited
        paragraph, and a hard-wrapped line stays inside it.

        ⚠️ DECLARED RESIDUALS of this model, stated not tuned (#14237 r6 review): a PROSE
        line containing a `|` is read as a table row and split into cells (over-block).
        A nested list item is deliberately NOT a continuation (it is its own block), because
        letting it join its parent let the parent's replacement clear a bare instruction in
        the sub-item — a fail-open; `test_a_nested_list_item_is_its_own_block` pins that.
        """
        blocks: list[tuple[int, int, str]] = []
        offset = 0
        lines = text.splitlines(keepends=True)

        def flush(parts: list[str], begin: int, end: int) -> None:
            if parts:
                blocks.append((begin, end, "".join(parts)))

        paragraph: list[str] = []
        paragraph_start = 0
        item: list[str] = []
        item_start = 0
        fence: list[str] = []
        fence_start = 0
        in_fence = False

        def flush_item() -> None:
            nonlocal item
            flush(item, item_start, offset)
            item = []

        for line in lines:
            stripped = line.strip()
            ends = offset + len(line)
            opens_fence = stripped.startswith("```") or stripped.startswith("~~~")
            if in_fence:
                fence.append(line)
                if opens_fence:
                    blocks.append((fence_start, ends, "".join(fence)))
                    fence, in_fence = [], False
                offset = ends
                continue
            if opens_fence:
                flush(paragraph, paragraph_start, offset)
                paragraph = []
                flush_item()
                fence, fence_start, in_fence = [line], offset, True
                offset = ends
                continue
            if not stripped:
                flush(paragraph, paragraph_start, offset)
                paragraph = []
                flush_item()
                offset = ends
                continue
            is_item = re.match(r"[-*+] |\d+\. ", stripped) is not None
            has_pipe = "|" in stripped.replace("\\|", "")
            if has_pipe:
                # ⛔ A TABLE CELL IS ITS OWN BLOCK, not the row (#14237 round-2 review's
                # property, kept when the rule moved off sentences). A markdown row has no
                # sentence terminator, so with the ROW as the unit a migration note in one
                # cell clears an instruction in the next — the exact fail-open the cell
                # scoping was added for. `|` is the cell separator and not a character this
                # doc uses in prose.
                # ⛔ AND A ROW IS RECOGNISED BY THE PIPE IT CONTAINS, not by a LEADING one:
                # GFM does not require the leading pipe, so keying on it collapsed a
                # pipe-less table into ONE paragraph and the cells were never separated
                # (#14237 r6 review P1).
                flush(paragraph, paragraph_start, offset)
                paragraph = []
                flush_item()
                cursor = offset
                cells = line.split("|")
                for index, cell in enumerate(cells):
                    cell_start = cursor
                    cursor += len(cell)
                    if index < len(cells) - 1:
                        cursor += 1  # the `|` separator itself
                    blocks.append((cell_start, min(cursor, ends), cell))
                offset = ends
                continue
            # A BLOCKQUOTE and a SETEXT UNDERLINE are markdown block boundaries too, and
            # without them the unit merged across one: an instruction inside a `>` line, or
            # under a `---`/`===` heading underline, inherited the note above it and both
            # rules accepted it (#14237 r7 review P2, measured with markdown-it: two blocks
            # rendered, one block returned, ACCEPTED).
            if stripped.startswith(">") or re.fullmatch(r"[=-]{2,}", stripped):
                flush(paragraph, paragraph_start, offset)
                paragraph = []
                flush_item()
                blocks.append((offset, ends, line))
                offset = ends
                continue
            # An INDENTED line after a list item is that item's CONTINUATION: markdown
            # keeps it inside the item, so splitting it out refused correct, hard-wrapped
            # migration notes (#14237 r6 review P2, three measured in `docs/`).
            # ⛔ A NESTED LIST ITEM IS NOT A CONTINUATION (measured, #14237 r6 review's
            # residual closed rather than declared): markdown puts `  - sub` in the parent
            # `<li>` as its own list, and the round-6 model let it JOIN the parent block —
            # so a note naming the replacement in the parent cleared a BARE instruction in
            # the sub-item, which is a fail-OPEN, the one direction this guard may not err
            # in. It is a marker line, so it falls through to the item branch below.
            if item and line[:1] in (" ", "\t") \
                    and re.match(r"[-*+] |\d+\. ", stripped) is None:
                item.append(line)
                offset = ends
                continue
            if stripped.startswith("#") or is_item:
                flush(paragraph, paragraph_start, offset)
                paragraph = []
                flush_item()
                if is_item:
                    item, item_start = [line], offset
                else:
                    blocks.append((offset, ends, line))
                offset = ends
                continue
            if not paragraph:
                paragraph_start = offset
            paragraph.append(line)
            offset = ends
        flush(paragraph, paragraph_start, offset)
        flush_item()
        if in_fence:
            blocks.append((fence_start, offset, "".join(fence)))
        return blocks

    @classmethod
    def replacements(cls, name: str) -> set[str]:
        """The live doors `RETIRED_USE_INSTEAD[name]` tells the reader to use instead.

        A pointer is prose: it can name one live door, two (`tortoise_mine_conversations`),
        or the same one twice (`tortoise_delete` preview + confirm).
        """
        return set(cls.REPLACEMENT_NAME.findall(RETIRED_USE_INSTEAD[name]))

    @classmethod
    def retired_call_forms(cls, text: str) -> list[str]:
        """R1 — every retired name written as a call, as ``name:line`` (empty is clean)."""
        found: list[str] = []
        for name in sorted(RETIRED_USE_INSTEAD):
            for match in re.finditer(rf"\b{re.escape(name)}\b", text):
                if not cls.is_call_form(text, match.end()):
                    continue
                line = text.count("\n", 0, match.start()) + 1
                found.append(f"{name}:{line}")
        return found

    @classmethod
    def notes_missing_replacement(cls, text: str) -> list[str]:
        """R2 — every non-call mention whose block never names the replacement."""
        found: list[str] = []
        blocks = cls.blocks(text)
        for name in sorted(RETIRED_USE_INSTEAD):
            for match in re.finditer(rf"\b{re.escape(name)}\b", text):
                if cls.is_call_form(text, match.end()):
                    continue
                block = next(
                    (body for start, end, body in blocks if start <= match.start() < end),
                    "")
                if not (cls.replacements(name) & set(cls.REPLACEMENT_NAME.findall(block))):
                    line = text.count("\n", 0, match.start()) + 1
                    found.append(f"{name}:{line} -> {' '.join(block.split())[:120]}")
        return found

    def test_the_guard_names_the_doc_it_guards(self):
        """An empty parametrize source SKIPS, it does not fail (#14237 r5 review, P2).

        `@pytest.mark.parametrize` over an empty tuple collects nothing, so deleting the
        entry during a doc rename would leave every gate green while the guard measured
        nothing. This row is the tripwire.
        """
        assert "docs/how-to/provision-a-graph-per-end-user.md" in self.CALL_INSTRUCTION_DOCS, (
            "the guard's only parametrize source no longer names the doc — an empty "
            "parametrize SKIPS instead of failing, so the guard would silently measure "
            "nothing (measured: `parametrize` over `()` gives `1 skipped`)")

    def test_the_block_splitter_is_structure_not_punctuation(self):
        """A fence, a table row, a heading and a list item are blocks; a wrapped line is not.

        ⛔ THE FENCE ROW IS DISCRIMINATING IN BOTH DIRECTIONS, measured (#14237 r5 review,
        #3442 rule 3 — a test must be able to fail under the revert it names). The first
        draft of this row put the fence after STRUCTURAL lines, so dropping the fence
        branch from `blocks` still produced five blocks (the fence merged into the
        paragraph that followed a list item) and the row stayed GREEN — it named a
        property it could not see. The second half is the revert's own case: prose with
        NO blank line before a fence. There, losing the fence branch merges prose+code
        into one block, so both the count and `blocks[5]`/`blocks[6]` red.
        """
        text = (
            "Wrapped prose line one\n"
            "and the same paragraph.\n"
            "\n"
            "| a | b |\n"
            "# a heading\n"
            "- a list item\n"
            "```\n"
            "call()\n"
            "```\n"
            "Prose directly under the fence, with no blank line.\n"
            "~~~\n"
            "code()\n"
            "~~~\n"
        )
        blocks = [body for _start, _end, body in self.blocks(text)]
        # The table row is now FOUR blocks (three cells plus the empty leading cell), and
        # that is the point: `| a | b |` splits to ['', ' a ', ' b ', '\n'], so a note in one
        # cell can never clear an instruction in the next (`test_a_table_cell_is_its_own_block`).
        assert len(blocks) == 10, blocks
        assert blocks[0] == "Wrapped prose line one\nand the same paragraph.\n"
        assert blocks[1:5] == ["", " a ", " b ", "\n"], blocks[1:5]
        assert blocks[5] == "# a heading\n"
        assert blocks[6] == "- a list item\n"
        assert blocks[7] == "```\ncall()\n```\n"
        assert blocks[8] == "Prose directly under the fence, with no blank line.\n", (
            "prose and the fence below it were merged into one block — a code sample then "
            "borrows the replacement a note in the prose names")
        assert blocks[9] == "~~~\ncode()\n~~~\n"

    @pytest.mark.parametrize("kind,text", [
        ("heading",
         "# the MCP tool `tortoise_list_graphs` lists names\n"
         "## `tortoise_graph_overview(section=\"graphs\")` lists them\n"),
        ("list-item",
         "- the MCP tool `tortoise_list_graphs` lists names\n"
         "- `tortoise_graph_overview(section=\"graphs\")` lists them\n"),
    ], ids=["heading", "list-item"])
    def test_each_structural_block_kind_is_its_own_block(self, kind, text):
        """The heading and list-item branches, pinned by a verdict that FLIPS.

        ⛔ MEASURED, and this is the #14237 r6 review P2: the splitter row ABOVE counts blocks
        in a fixture whose structural lines each sit on their own line, so dropping the
        heading branch (or the list-item branch) from `blocks` leaves it GREEN — a count that
        cannot fail is not coverage, and the structural model IS the rule's replacement
        thesis. Each kind is pinned here where losing it changes the ANSWER: the mention in one
        unit, the replacement in the NEXT unit of the same kind, no blank line between. Merge
        the two units into one paragraph and the mention inherits its neighbour's replacement
        — a false green.
        """
        assert self.notes_missing_replacement(text), (
            f"a mention in one {kind} was cleared by the replacement in the next {kind} — "
            "that block kind is not separated, so R2 reads the run as one block")

    @pytest.mark.parametrize("label,text", [
        ("pipe-less",
         "GRAPH | the MCP tool `tortoise_list_graphs` lists names\n"
         "--- | ---\n"
         "GRAPH | `tortoise_graph_overview(section=\"graphs\")` lists them\n"),
        ("leading-pipe",
         "| GRAPH | the MCP tool `tortoise_list_graphs` lists names |\n"
         "| --- | --- |\n"
         "| GRAPH | `tortoise_graph_overview(section=\"graphs\")` lists them |\n"),
    ], ids=["pipe-less", "leading-pipe"])
    def test_a_table_row_is_a_row_with_or_without_leading_pipes(self, label, text):
        """#14237 r6 review P1 — GFM does not require the leading pipe, and that mattered.

        ⛔ MEASURED BEFORE THE FIX: the pipe-less table collapsed into ONE paragraph (1 block),
        so its cells were never separated and the mention in row 1 was cleared by the
        replacement in row 3 — ACCEPTED. The identical content written with leading pipes gave
        3 rows / their cells and was REJECTED. Recognising a row only by a LEADING `|`
        therefore re-opened, for pipe-less tables, the exact cell-scoping fail-open that
        `test_a_table_cell_is_its_own_block` exists to close — reachable by deleting one
        character per line. Both spellings are one table to markdown
        (`MarkdownIt("commonmark").enable("table")` renders each as ONE <table> with
        separate <tr>), so both must be rows here.
        """
        assert self.notes_missing_replacement(text), (
            f"the {label} table was read as one block, so a mention in one cell was cleared "
            "by the replacement in another row")

    def test_a_list_item_keeps_its_indented_continuation(self):
        """#14237 r6 review P2 — markdown puts an indented line INSIDE the item.

        ⛔ MEASURED BEFORE THE FIX: the continuation was a separate block (2 blocks), so this
        correct, hard-wrapped migration note was REFUSED and told its author to fix a document
        that was already right. It is not hypothetical: three real notes in `docs/` had this
        shape (`product/canonical-mcp-tools.md:505`,
        `plans/2026-11-13-issue-12378-event-layer-read.md:50`,
        `scoping/2026-09-17-3863-scope-output.md:874`). Markdown agrees with the note —
        `MarkdownIt` renders both lines as ONE <li>.
        """
        text = (
            "- The door `tortoise_list_graphs` retired forward into\n"
            "  `tortoise_graph_overview(section=\"graphs\")`, use that.\n"
        )
        assert len(self.blocks(text)) == 1, self.blocks(text)
        assert not self.notes_missing_replacement(text), (
            "a correctly wrapped list-item note was refused because its continuation was "
            "treated as a separate block")

    def test_a_hard_wrapped_line_is_NOT_a_block_boundary(self):
        """The negative control: making a bare newline a boundary must red this.

        Without it, the cheapest way to pass every block-kind row is to split on EVERY line —
        which refuses the notes the rule exists to accept, including this repo's own ~80-column
        wrapped migration notes. A guard whose rows can all be satisfied by over-blocking is not
        measuring the rule.
        """
        text = (
            "The live door is `tortoise_graph_overview(section=\"graphs\")`; the MCP\n"
            "tool `tortoise_list_graphs` retired forward into it.\n"
        )
        assert not self.notes_missing_replacement(text), (
            "a hard-wrapped note was refused — a bare newline inside a paragraph is not a "
            "block boundary")

    def test_a_blank_line_ends_a_list_item(self):
        """The blank-line boundary, pinned — deleting it stayed GREEN before this row.

        ⛔ MEASURED (#14237 r7 review P2): dropping the `flush_item()` on the blank-line
        branch left all 27 rows green while turning this shape from REJECTED (2 blocks) into
        ACCEPTED (1 block) — an indented code sample under a parent item's note would then
        borrow that note's replacement. This is the boundary that keeps the r5 fence
        fail-open closed, so it gets a row that names it.
        """
        text = (
            "- Parent note: the live door is `tortoise_graph_overview(section=\"graphs\")`.\n"
            "\n"
            "    tortoise_list_graphs\n"
        )
        assert len(self.blocks(text)) == 2, self.blocks(text)
        assert self.notes_missing_replacement(text), (
            "an indented line after a BLANK line was absorbed into the item above it, so "
            "it inherited that note's replacement — a blank line ends the item")

    def test_a_newline_is_never_a_call_form(self):
        """`CALL_FORM`'s "horizontal only" claim, pinned (#14237 r7 review P2).

        A `\\s*` widening of `CALL_FORM` stayed GREEN before this row, so the comment's
        claim was a promise no test could see. A name broken across a line break is a
        WRAPPED PROSE mention, not a call — declaring that keeps R1 from silently swallowing
        the wrapped-note shape the rest of this class exists to accept.
        """
        text = "the MCP tool `tortoise_list_graphs`\n(graph_id=x selects one graph)\n"
        assert not self.retired_call_forms(text), (
            "a name followed by a newline was read as a call — CALL_FORM must allow "
            "horizontal space and inline delimiters only")

    @pytest.mark.parametrize("kind,text", [
        ("setext-underline",
         "note: the MCP tool `tortoise_list_graphs` retired forward into `tortoise_graph_overview(section=\"graphs\")`\n"
         "---\n"
         "Call `tortoise_list_graphs` for names.\n"),
        ("blockquote",
         "The live door is `tortoise_graph_overview(section=\"graphs\")`.\n"
         "> Call `tortoise_list_graphs` for names.\n"),
    ], ids=["setext-underline", "blockquote"])
    def test_a_setext_underline_and_a_blockquote_are_block_boundaries(self, kind, text):
        """#14237 r7 review P2 — measured with markdown-it: rendered as 2, returned as 1.

        A `---`/`===` underline and a `>` line both START a new markdown block, so an
        instruction in that block is not cleared by the note above it.
        """
        assert len(self.blocks(text)) >= 2, self.blocks(text)
        assert self.notes_missing_replacement(text), (
            f"the {kind} did not start a block, so an instruction inside it inherited the "
            "replacement from the note above")

    def test_a_table_cell_is_its_own_block(self):
        """A row has no terminator, so cell scoping keeps a note from clearing an instruction.

        ⛔ THE PROPERTY IS KEPT FROM THE SENTENCE-ERA TEST, RE-EXPRESSED AGAINST THE RULE THAT
        SHIPPED (#14237 round-2 review, measured; merged from the peer's
        `test_a_table_cell_is_its_own_sentence`). It is the row-unit fail-open: a migration
        note in one cell and an INSTRUCTION in the next share a markdown row, so a ROW-unit R2
        would clear the instruction on its neighbour's replacement. Measured below on both
        halves — the instruction cell alone reds, the note cell alone passes.
        """
        row = ("|| `GRAPH_LISTING` | call `tortoise_list_graphs` for names "
               "| (`tortoise_list_graphs` retired forward into "
               "`tortoise_graph_overview(section=\"graphs\")`) |\n")
        assert not self.retired_call_forms(row), (
            "the fixture must be a BARE mention, not a call form — otherwise R1, not the "
            "cell split, is what reds it and this row grades nothing")
        instruction = row.index("call `tortoise_list_graphs")
        cells = [body for start, end, body in self.blocks(row) if start <= instruction < end]
        assert len(cells) == 1, cells
        assert "tortoise_graph_overview" not in cells[0], (
            "the instruction inherited the NEXT cell's replacement, so a row-unit block "
            "clears an instruction written beside a note — the round-2 fail-open")
        note = row.rindex("`tortoise_list_graphs`")
        note_cell = [body for start, end, body in self.blocks(row) if start <= note < end]
        assert len(note_cell) == 1 and "tortoise_graph_overview" in note_cell[0], (
            "the migration note's own cell must name the replacement and must be its own "
            "block")

    def test_a_nested_list_item_is_its_own_block(self):
        """A SUB-item is a unit of its own, so the parent's replacement cannot clear it.

        Measured fail-OPEN in the round-6 model (which joined every indented line to the
        parent item): the parent names `tortoise_graph_overview`, the nested line is a BARE
        instruction to call `tortoise_list_graphs`, and R2 accepted it on the parent's
        word. A hard-wrapped CONTINUATION (no marker) must still join its item — that half
        is pinned by the three real notes the round-6 review measured and by
        `test_the_block_splitter_is_structure_not_punctuation`.
        """
        text = (
            "- The listing door is `tortoise_graph_overview(section=\"graphs\")`; use it.\n"
            "  - Call `tortoise_list_graphs` for names.\n"
        )
        assert not self.retired_call_forms(text), (
            "the fixture must stay a BARE mention — otherwise R1, not the block split, is "
            "what reds it")
        assert self.notes_missing_replacement(text), (
            "the nested sub-item borrowed its parent item's replacement — a fail-open: the "
            "reader is told to call a retired door and R2 accepts it")
        continuation = (
            "- The listing door is `tortoise_list_graphs`, retired forward into\n"
            "  `tortoise_graph_overview(section=\"graphs\")`, which lists names.\n"
        )
        assert not self.notes_missing_replacement(continuation), (
            "a hard-wrapped CONTINUATION was split off its item, so a correct note reds — "
            "the over-block the round-6 review measured in three `docs/` notes")

    def test_the_PRE_FIX_text_is_a_violation(self):
        """R2 catches the defect this guard exists for — measured from commit b4da1f5c01.

        ⛔ Both lines were written BARE, so a call-form-only rule (R1) would pass them:
        this is why R1 alone is not the guard.
        """
        pre_fix = (
            "`keys:manage`) or from the MCP tool **`tortoise_list_graphs`**, which answers a\n"
            "metadata summary you already have.\n"
            "\n"
            "| `GRAPH_LISTING_VIA_SESSION` | called `GET /v1/graphs` with an API key | keep "
            "`graph.id` from the 201; the MCP tool `tortoise_list_graphs` lists graph "
            "**names**, not ids |\n"
        )
        assert not self.retired_call_forms(pre_fix), (
            "the pre-fix text carried no `(`, so R1 alone cannot see it — that is the "
            "premise of this row")
        assert self.notes_missing_replacement(pre_fix), (
            "the pre-fix text named a retired door as the tool to use and never named "
            "`tortoise_graph_overview`, so R2 must reject it")
        fixed = (
            "(`keys:manage`) or the MCP tool **`tortoise_graph_overview(section=\"graphs\")`**\n"
            "(the door `tortoise_list_graphs` retired forward into it).\n"
            "\n"
            "| `GRAPH_LISTING_VIA_SESSION` | keep `graph.id` from the 201 | "
            "`tortoise_graph_overview(section=\"graphs\")` lists names "
            "(`tortoise_list_graphs` retired forward into it) |\n"
        )
        assert not self.notes_missing_replacement(fixed), (
            "the shipped repair names the live door in the same block as the note, so it "
            "must pass — a guard that reds its own repair is an over-block")

    @pytest.mark.parametrize("text,arm", [
        ("Call `tortoise_list_graphs` for the names.\n", "R2"),
        ("Call `tortoise_list_graphs` — it is not retired.\n", "R2"),
        # R1, not R2: the name is followed by a delimiter run and a bracket, so it RENDERS
        # as a call (#14237 r7 review P1) and R2 treats that shape as a call form.
        ("Call `tortoise_list_graphs` (now retired).\n", "R1"),
        ("Call `tortoise_list_graphs` the way `tortoise_delete` did before it retired.\n", "R2"),
        ("Call `tortoise_list_graphs` for names, even though OTHER doors retired.\n", "R2"),
        ("Call `tortoise_list_graphs` for now — the door retires in the next SDK wave.\n", "R2"),
        ("Do not call `tortoise_list_graphs`; it retired forward into it.\n", "R2"),
        ("Do not call `tortoise_list_graphs` — it retired forward into it.\n", "R2"),
    ], ids=["bare", "negated", "parenthetical", "past-form", "other-doors", "promise",
            "semicolon", "em-dash"])
    def test_an_instruction_cannot_be_talked_back_in(self, text, arm):
        """Every shape rounds 2-7 recorded as a fail-open, at once (#14237 r4-r7 reviews).

        The label word is gone, so none of these has anything to hide behind: an
        instruction does not name the replacement, and the negated/parenthetical/promise
        forms ("not retired", "(now retired)", "retires in the next wave") are irrelevant
        to a rule that reads structure.

        ⛔ EACH ROW NAMES THE ARM THAT MUST CATCH IT — not the union of the two. Most of
        these are instructions that name no replacement, so R2 is the arm (an instruction is
        a note with a dead end). The parenthetical one is R1: a retired name followed by an
        inline delimiter run and a bracket RENDERS as a call (#14237 r7 review P1), and R2
        deliberately skips call forms. Naming the arm keeps every row able to FAIL on its own
        arm's revert; a union would stay green if either arm alone regressed, which is how a
        row stops being coverage.
        """
        if arm == "R1":
            assert self.retired_call_forms(text), (
                f"the instruction was not caught as a copyable CALL: {text!r}")
        else:
            assert self.notes_missing_replacement(text), (
                f"an instruction was accepted as a note: {text!r}")

    @pytest.mark.parametrize("text", [
        "Call `tortoise_list_graphs(graph_id=x)` to read one graph.\n",
        "Call `tortoise_list_graphs(section=...)` and then `tortoise_graph_overview`.\n",
        "Call `tortoise_list_graphs (graph_id=x)` to read one graph.\n",
        "Call `tortoise_list_graphs  (graph_id=x)` to read one graph.\n",
        # #14237 r7 review P1: one markdown delimiter between name and bracket restored
        # the defect on the PR's OWN guarded document, with the whole suite green.
        "Call `tortoise_list_graphs`(graph_id=x) to read one graph.\n",
        "Call **`tortoise_list_graphs`**(graph_id=x) to read one graph.\n",
        "Call _`tortoise_list_graphs`_(graph_id=x) to read one graph.\n",
        "Call *`tortoise_list_graphs`*(graph_id=x) to read one graph.\n",
        "Call `tortoise_list_graphs` (graph_id=x) to read one graph.\n",
        "```python\ngraph = tortoise_list_graphs(graph_id='x')\n```\n",
    ])
    def test_a_copyable_call_is_rejected_even_beside_the_replacement(self, text):
        """R1 — a call form is a spelling to type, so naming the successor does not save it.

        The two SPACED rows are the #14237 r6 review P2: Python accepts `name (args)`, so a
        rule testing ONE character of adjacency called the spaced spelling clean — while the
        declared residual claimed R1 rejected the copyable spelling.
        """
        assert self.retired_call_forms(text), (
            f"a copyable call of a retired door was accepted: {text!r}")

    def test_a_fenced_mention_cannot_borrow_the_note_above_it(self):
        """The r5 shape: a fence was not a boundary, so the note's "retired" cleared it.

        ⛔ The fence DIRECTLY follows the note — no blank line — because that is the shape
        a blank-line-only splitter gets wrong (measured: with the fence branch removed
        from `blocks`, this fixture reds, which is what makes the row discriminating).
        """
        text = (
            "(the door `tortoise_list_graphs` retired forward into "
            "`tortoise_graph_overview(section=\"graphs\")`.)\n"
            "```python\n"
            "graph = tortoise_list_graphs(graph_id='x')\n"
            "```\n"
        )
        assert self.retired_call_forms(text), (
            "a fenced call under a migration note was accepted — a fence is its own block")
        bare = (
            "(the door `tortoise_list_graphs` retired forward into "
            "`tortoise_graph_overview(section=\"graphs\")`.)\n"
            "```\n"
            "tortoise_list_graphs\n"
            "```\n"
        )
        assert self.notes_missing_replacement(bare), (
            "a bare retired name inside its own fence borrowed the note's replacement — "
            "without the fence branch in `blocks`, the note and the code share a paragraph")

    def test_declared_residual_a_block_may_name_both_and_still_instruct(self):
        """⚠️ DECLARED RESIDUAL — stated, not tuned (#14237 r5 review).

        R2 asks that the mention's block names the replacement. A block that names BOTH
        and still tells the reader to call the retired door therefore passes, e.g.
        "Call `tortoise_list_graphs` for names; `tortoise_graph_overview` exists too".
        That is the honest bound of a structural rule: it is strictly narrower than the
        label word it replaces (which accepted a NEGATED label and any neighbouring
        clause's "retired"), and R1 still rejects the copyable spelling. It is pinned
        here so the next reader of the rule learns the bound from a test, not from a
        regex.
        """
        text = (
            "Call `tortoise_list_graphs` for names; "
            "`tortoise_graph_overview(section=\"graphs\")` exists too.\n"
        )
        assert not self.notes_missing_replacement(text)
        assert not self.retired_call_forms(text)

    @pytest.mark.parametrize("relpath", CALL_INSTRUCTION_DOCS)
    def test_no_retired_name_is_offered_as_a_copyable_call(self, relpath):
        """R1 on the real document: no retired name is written as a call (#14237 r5, P1)."""
        from pathlib import Path

        doc = Path(__file__).resolve().parent.parent / relpath
        text = doc.read_text(encoding="utf-8")
        assert not self.retired_call_forms(text), (
            f"{relpath} writes a retired door as a call the reader can copy "
            f"(name:line = {self.retired_call_forms(text)}). The name is shim-only on "
            "`get_tool` and absent from `tools/list`, so a tool-grant client cannot "
            "follow it — re-point the call at the live door")

    @pytest.mark.parametrize("relpath", CALL_INSTRUCTION_DOCS)
    def test_every_mention_is_a_note_that_names_its_replacement(self, relpath):
        """R2 on the real document: each retired mention names its successor (#14237 r5).

        A DEAD-END mention is the defect class: the reader is given the old name and no
        way forward. The doc's own two mentions are migration notes that name
        `tortoise_graph_overview`, so they pass — and `mentions` is asserted non-empty,
        because a scan with an empty discovery set proves nothing.
        """
        from pathlib import Path

        doc = Path(__file__).resolve().parent.parent / relpath
        text = doc.read_text(encoding="utf-8")
        mentions = sum(
            len(re.findall(rf"\b{re.escape(name)}\b", text)) for name in RETIRED_USE_INSTEAD
        )
        assert mentions, (
            f"{relpath} names no retired door at all, so this guard measured nothing — "
            "a scan with an empty discovery set cannot pass")
        dead_ends = self.notes_missing_replacement(text)
        assert not dead_ends, (
            f"{relpath} mentions a retired door without naming its replacement in the "
            f"same block, so the reader is told the old spelling and nothing else: "
            f"{dead_ends}. Name the live door beside the note, or re-point the sentence")
        assert "tortoise_graph_overview" in text, (
            f"{relpath} no longer names the live listing door at all — the repair for "
            "this guard is a RE-POINT, never a deletion")
