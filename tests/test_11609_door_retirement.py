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

import html
import inspect
import re
import string
import unicodedata
from collections import Counter
from typing import ClassVar

import pytest
import regex
from markdown_it import MarkdownIt

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
    #:       The name is judged in the block's DECODED text as well as its source, because an
    #:       entity-spelled name (`&#116;ortoise_list_graphs`) renders as the name and is the
    #:       same door to the reader (measured fail-open, closed in the same round).
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
    #: ⛔ A SECOND, NARROWER DEPARTURE, and it carries the marker for the same reason.
    #: **OVERRIDES:** the GFM spec's leaf block for a table is the ROW, and its own block model
    #: forbids block-level elements inside a cell — this guard's unit is the CELL. A ROW is what
    #: `blocks()` emitted first, and it is unmarked prose no longer: measured, the ROW re-entered
    #: the round-2 fail-open (see below), so a reader holding the GFM spec who "tidies" the unit
    #: back to the spec's ROW would silently restore it. A TABLE **CELL** IS TREATED AS A BLOCK.
    #: The GFM tables extension makes the table a leaf block and states that block-level elements
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
    #: ⚠️ DECLARED BOUNDS — stated so the next reader learns them here rather than by
    #: experiment. Section (a) are the prices of choices; (b) is the fail-closed direction.
    #: NOTE: two earlier entries are GONE because the model changed, not because they were
    #: worded away — the "a prose line containing `|` is read as a table row" over-block ended
    #: when the CommonMark parser became the model (a lone pipe line is a paragraph), and the
    #: nested-sub-item over-allow ended when a marker line became its own block.
    #:   (a) * a retired name followed by an inline delimiter run and a bracket IS a call, so a
    #:         NOTE spelled `name (retired)` also reds R1 — the safe (fail-closed) direction,
    #:         and the price of catching `` `name`(args) ``, which RENDERS as a call;
    #:       * a retired name followed by a NEWLINE then `(` is not a CALL (horizontal rule),
    #:         so it is judged as a NOTE, i.e. rejected unless its block also names the
    #:         replacement (#14237 r8 review P2: an earlier wording said such a call "is
    #:         therefore accepted", which is FALSE for a bare instruction);
    #:       * ANY block whose inline source contains a `<` is OPAQUE (visible text empty), in
    #:         the paragraph path, the table-CELL path and the HTML-block path alike: ONE rule,
    #:         applied where all FIVE read it — `retired_call_forms` (R1, its raw arm and its
    #:         rendered arm), `notes_missing_replacement` (R2, ditto), and
    #:         `_entity_only_mentions`, the spelling-variant reader, which judges a mention on
    #:         the block's decoded SOURCE and on the block's RENDERED text precisely because an
    #:         opaque block has no visible text at all. So every mention in such a block reds —
    #:         R2 has no text to match and R1 reds a call form as it does anywhere else
    #:         (measured: an earlier wording offered ``an R1 call form`` as an escape; nothing
    #:         survives). ⛔ THE FOURTH READER WAS THE ESCAPE (#14237 r13 review P2, measured):
    #:         it bailed on `not body`, and `body` is `""` for EVERY opaque block, so
    #:         `Call &#116;ortoise_list_graphs(x=1) for names. <br>` and four sibling shapes
    #:         returned `R1 [] R2 []` — ACCEPTED — while this sentence promised otherwise. An
    #:         entity spelling in a NON-opaque block reds (r12) and a PLAIN spelling in an
    #:         opaque block reds (r10), so the intersection of the two fixes was the one gap.
    #:         The rows below pin it per shape, because the sentence is the contract a reader
    #:         trusts and it may not describe itself more widely than it behaves.
    #:         ⛔ AND THE COUNT ABOVE WAS THE NEXT ESCAPE (#14237 r16, measured): "all FOUR" was
    #:         itself a blast radius wider than the behaviour, because R1 judges CALL FORM in
    #:         the raw source only — so `Call tortoise_list_graphs\\(x=1\\) for names. Use
    #:         tortoise_graph_overview(...)` returned `R1 [] R2 []` (ACCEPTED) while the reader
    #:         sees a copyable call, and `tortoise\\_list\\_graphs` — markdown's backslash
    #:         escape, the OTHER way to spell a name — was invisible to every reader: a dead
    #:         end in that spelling was ACCEPTED where the plain one reds. R1 now judges call
    #:         form in the RENDERED text too, and discovery reads the rendered text as well as
    #:         the decoded source, so the verdict depends on neither spelling mechanism.
    #:         Approximating "what a browser displays" from the source failed OPEN FOUR rounds
    #:         running — the tag strip (r10), an inline `<script>`/`<style>`/`display:none`
    #:         body (r11), and raw-slice segmentation that re-admitted image alt text, link
    #:         titles and reference labels (r12) — which is why the rule is opacity, not
    #:         parsing. The declared price is the over-block, and it is stated as the rule
    #:         actually is — the trigger is the SOURCE, not the render, so a NOTE loses its
    #:         replacement credit if its block contains a `<` at all. ⛔ MEASURED and pinned
    #:         per spelling: an inline tag/comment (`<br>`), an AUTOLINK (`<https://…>`,
    #:         `<ops@…>`) — markdown, not a tag — a code span carrying `<` (`` `<graph.id>` ``),
    #:         and a BACKSLASH-escaped `<`. The last three are text a reader genuinely sees, so
    #:         this is a FALSE RED on correct prose; that is the deliberate fail-closed trade,
    #:         and `test_the_declared_over_block_is_the_price_of_the_opacity_rule` pins each
    #:         spelling so the bound cannot be described more narrowly than it behaves
    #:         (**#14237 r12 review P2** — the class comment is the contract a reader trusts).
    #:       * an ENTITY spelling is decoded by its block's own LANGUAGE, and it is reported
    #:         exactly where the PLAIN spelling is: a well-formed (`;`-terminated) reference
    #:         reds even in a link title, a link destination or image alt text, and a
    #:         semicolon-less numeric reference is NOT a reference in markdown text (the
    #:         renderer shows `&#116ortoise…` literally, so it is no mention and does not red)
    #:         while inside a raw `html_block` it IS consumed, as a browser consumes it. ⛔ ONE
    #:         decode for both was measured wrong in BOTH directions (#14237 r14): the
    #:         permissive (HTML5) one false-red a literal sentence as soon as an unrelated
    #:         `<br>` made its block opaque, and the visible-body one could not see a real
    #:         entity spelling in the very containers the plain spelling reds in, so the
    #:         verdict depended on the SPELLING. Discovery reads the block's decoded SOURCE;
    #:         `body` still governs R2's replacement credit, so a hidden replacement still
    #:         cannot clear a visible mention. Pinned by
    #:         `test_the_verdict_does_not_depend_on_the_SPELLING`,
    #:         `test_a_semicolon_less_reference_is_LITERAL_in_markdown_text` and
    #:         `test_raw_HTML_is_decoded_the_way_a_browser_decodes_it`.
    #:       * ⚠️ *THREE* SPELLING MECHANISMS, NOT TWO (#14237 r17, measured fail-open). The
    #:         gate above the discovery walk said `&` and `\\` are "the only ways the source can
    #:         differ from the text the reader sees while a retired name is on screen". THAT WAS
    #:         FALSE — rendering also REMOVES MARKUP, so markup INSIDE the name splits it into
    #:         raw tokens that are not retired names at all, and the whole unit was skipped
    #:         before the rendered arm could read it. Measured at the r16 head: `Call
    #:         tortoise_list_graph*s*(x=1) for names. Use tortoise_graph_overview(...)`,
    #:         `…grap**hs**…` and two-code-span spellings all returned `R1 [] R2 []` while the
    #:         reader sees a copyable call — and the dead-end spelling (`The tool
    #:         tortoise_list_grap*hs* lists names.`) was ACCEPTED where the plain one is an R2
    #:         dead end. The rendered arm now runs for EVERY unit and only `shown` stays behind
    #:         the `&` gate, which is exact rather than approximate (`_decoded_source` resolves
    #:         references and nothing else, so with no `&` it returns the source unchanged). A
    #:         delimiter strip is NOT the fix: `tortoise*_*list_graphs` renders with its
    #:         delimiters intact, so it is not the name —
    #:         `test_a_delimiter_that_does_NOT_render_as_the_name_is_accepted` pins that
    #:         control. Same round, a second spelling gap in `CALL_FORM`: its class was `[ \t]`,
    #:         so `Call tortoise_list_graphs&nbsp;(x=1)` — the very `name (args)` shape a plain
    #:         space already reds — was ACCEPTED, because the renderer emits U+00A0. The class
    #:         is now `[^\\S\\n\\r]` ("horizontal whitespace": NBSP, EN SPACE, THIN SPACE …) and
    #:         never a line terminator.
    #:       * ⚠️ *FOUR* SPELLING MECHANISMS: the INVISIBLE CHARACTER (#14237 r18, measured
    #:         fail-open, and it was a DECLARED residual until it was closed here). A ZWSP /
    #:         ZWNJ / ZWJ / word-joiner / soft hyphen renders as NOTHING, so it hides the door
    #:         in two places and each needed its own repair: INSIDE the name
    #:         (`Call tortoise&#8203;_list_graphs(x=1)`) the tokenizer split it into pieces that
    #:         are not retired names at all, and BETWEEN the name and its bracket
    #:         (`Call tortoise_list_graphs&#8203;(x=1)`) the character is not horizontal
    #:         WHITESPACE — `[^\\S\\n\\r]` does not match U+200B — so the separator run broke and
    #:         a rendered `name(x=1)` was read as a dead-end note rather than a call. Both were
    #:         measured ACCEPTED at `63ef683d18`; the residual row that had declared the first
    #:         one accepted is now the row that pins it RED (its own message asked for exactly
    #:         that). ONE mechanism, in ONE place: `_strip_invisible` over the JUDGED texts,
    #:         which closes both placements by leaving the ordinary `name(` behind — the
    #:         separator class is deliberately NOT widened (see `CALL_FORM`'s note, which
    #:         measured that widening as dead weight).
    #:         ⛔ AND THE CLASS WAS DECLARED CLOSED TWICE WHILE ITS MEMBERS WERE OPEN (#14237
    #:         r19 and r21, both measured fail-open — the same "stated bound wider than the
    #:         behaviour" the bullet above is about). The r18 repair gave `_strip_invisible` a
    #:         hand list of SIX codepoints. Measured at the r18 head `c75dc3603e66` against
    #:         Unicode's Cf category: **164 of its 170 members were ACCEPTED** between the name
    #:         and its bracket. That count is read from primary source
    #:         (`DerivedGeneralCategory.txt`; this interpreter's `unicodedata` — Unicode 15.0.0 —
    #:         agrees, 170), because BOTH earlier wordings were the size of the round's own
    #:         PROBE ("26 members; the other 20" in r20's comment, "of Unicode's 23 Cf" in r19's
    #:         commit message). r19 then asked `unicodedata.category` and the prose said the class
    #:         was closed a second time — and it was not, because **Cf is not the property that
    #:         means "a reader sees nothing"**: measured at the r20 head, variation selectors
    #:         (U+FE0F), the combining grapheme joiner (U+034F), the Hangul fillers (U+115F,
    #:         U+1160, U+3164) and the Mongolian free variation selectors (U+180B..180D) all
    #:         render as NOTHING and were ACCEPTED in BOTH placements, because their category is
    #:         Mn/Lo and the strip removed only Cf. r21 then asked **Default_Ignorable_Code_Point**
    #:         (the one property DEFINED as "this character is invisible", 4174 codepoints under
    #:         `regex`'s table) and the prose called the class closed a THIRD time — and it was not
    #:         closed either, in two directions that were measured rather than inferred:
    #:         (a) the Cf families Unicode excludes from that property for LAYOUT reasons (not
    #:         because they render) — U+FFF9..FFFB and U+13430..1343F — render NOTHING and were
    #:         ACCEPTED in both placements at r21's head; (b) the control family (a literal U+0007 /
    #:         U+001B / U+009F between the name and its bracket) renders nothing and was ACCEPTED
    #:         in both placements at every head before r22. The class is therefore DERIVED from
    #:         Unicode properties — `(Default_Ignorable ∪ Cf ∪ Cc) − Prepended_Concatenation_Mark
    #:         − White_Space` — never a second list that disagrees with Unicode the day a character
    #:         is minted (#4170). It also KEEPS r21's removal of the over-block the Cf category
    #:         bought: the THIRTEEN of the 32 Cf-not-Default_Ignorable that ARE
    #:         Prepended_Concatenation_Mark (U+0600..0605, U+06DD, U+070F, U+0890..0891, U+08E2,
    #:         U+110BD, U+110CD) render a sign or mark, so they survive the strip and a NOTE
    #:         written beside one keeps its replacement credit. ⛔ r21's prose said ALL 32 render a
    #:         mark; the other NINETEEN do not, which is why they are stripped now.
    #:         `test_the_invisible_class_is_DERIVED_from_UNICODE_PROPERTIES_not_a_hand_list` pins the
    #:         mechanism, the placement rows below pin it per family (Cf, the non-Cf families r19
    #:         left open, the layout-only Cf families and the controls r21/r22 left open), and
    #:         `test_a_Cf_member_that_DESIGNS_a_visible_mark_is_NOT_stripped` pins the over-block's
    #:         removal (see `INVISIBLE_PROPERTY`).
    #:       * the judged UNITS are every leaf block **and the source between them**, so a
    #:         retired name in a link-reference DEFINITION is reached in EVERY spelling. That
    #:         text is in NO block (the parser consumes the definition), which is why R2 already
    #:         reported a plain mention there as `NO BLOCK` (r9 review P1) — while the ENTITY
    #:         spelling of it fell through every reader, because the entity reader walked the
    #:         blocks (#14237 r15 review, measured: `[foo]: &#116;ortoise_list_graphs(x=1)`
    #:         ACCEPTED, the plain spelling R1 on line 1). A gap has NO visible text, so it can
    #:         never clear a mention — the plain spelling's verdict, not a new one. Pinned by
    #:         the `ref-def-*` rows of `test_the_verdict_does_not_depend_on_the_SPELLING` and by
    #:         `test_a_reference_definition_is_judged_OUTSIDE_the_block_model`.
    #:   (b) * a replacement that appears ONLY where a reader cannot see it — an HTML comment, a
    #:         link destination, a link-reference definition — does NOT count (#14237 r8/r9
    #:         reviews), because the body R2 reads is the block's VISIBLE text;
    #:       * a mention that falls in NO block at all (a link-reference definition, a
    #:         preamble) is REPORTED, not ignored — `test_a_mention_outside_every_block_is_reported`
    #:         pins it, and that row exists because "no block means clean" was measured as a
    #:         mutant that left the whole suite green. ⚠️ That row pins the PLAIN spelling only;
    #:         the ENTITY spelling of the same mention needed a judged unit that is not a block,
    #:         and is pinned by the bullet above (#14237 r15).
    #:
    #: ⚠️ SCOPE: the documents whose every `tortoise_*` mention is a call, and ONE of
    #: them today. The rule is doc-agnostic, but applying it repo-wide would red on docs
    #: that carry retired call forms right now, which is a repair of its own — **#14291**
    #: (filed from the round-5 review's P2, and re-measured at round 8: SEVEN lines, not six —
    #: `tortoise_recall:`358 / `tortoise_update_point:`400,:670,:689 /
    #: `tortoise_create_point:`665 / `tortoise_create_operator:`666 in
    #: `docs/INGEST_CONTRACT.md`, and `tortoise_mine_conversations:`714 in
    #: `docs/quickstart-selfhosted.md`), not this guard.
    #: Naming the issue rather than saying "a follow-up" is deliberate: an unnamed
    #: follow-up is how a class gets declared closed while it is still open.
    REPLACEMENT_NAME = re.compile(r"tortoise_[a-z0-9_]+")

    #: A retired name is matched as a WHOLE TOKEN — a maximal run of `[A-Za-z0-9_]`.
    #: ⛔ NOT `\b`, and not a lookaround pair either: Python treats `_` as a word character, so
    #: `\b` silently missed `_tortoise_list_graphs_` (underscore emphasis), which markdown
    #: renders as an italicised name and which is just as copyable as the backticked form
    #: (#14237 r9 review P1, measured: the emphasis spelling was invisible to BOTH rules).
    #: And a lookbehind that allows `_` on the left must also allow it on the right, which then
    #: matches `tortoise_delete` inside `tortoise_delete_entity` — the two directions cannot be
    #: satisfied by one boundary, so the token is compared whole instead.
    NAME_TOKEN = re.compile(r"[A-Za-z0-9_]+")

    @classmethod
    def name_hits(cls, text: str):
        """Yield ``(name, start, end)`` for every retired name written as a whole token.

        A leading/trailing run of `_` is MARKDOWN EMPHASIS, not part of the name, so it is
        stripped before the registry comparison and the yielded span covers the name only —
        which is what lets R1 see `_name_(` as a call (`_` is one of `CALL_FORM`'s delimiters).
        Internal underscores are NOT stripped, so `tortoise_delete_entity` cannot match
        `tortoise_delete` (#14237 r9 review P1, both measured).
        """
        for match in cls.NAME_TOKEN.finditer(text):
            token = match.group(0)
            core = token.strip("_")
            if core in RETIRED_USE_INSTEAD:
                lead = len(token) - len(token.lstrip("_"))
                yield core, match.start() + lead, match.start() + lead + len(core)

    #: Unicode's "a reader sees NOTHING here" set — the question this class actually asks —
    #: DERIVED from Unicode PROPERTIES, never enumerated and never one property alone:
    #:
    #:     ( \p{Default_Ignorable_Code_Point} ∪ \p{Cf} ∪ \p{Cc} )
    #:       − \p{Prepended_Concatenation_Mark} − \p{White_Space}
    #:
    #: Placed inside a retired name or between the name and its bracket, each such character
    #: leaves the reader looking at the door (or at the call) while the SOURCE no longer
    #: tokenizes as either (#14237 r18, both shapes measured ACCEPTED at `63ef683d18`). Declared
    #: ONCE and applied in ONE place — `_strip_invisible`, over the JUDGED texts and before
    #: tokenizing — which closes BOTH placements: inside the name the token splits without it,
    #: and between the name and its bracket removing it leaves the ordinary `name(` that
    #: `CALL_FORM` already matches.
    #: ⛔ IT IS A DERIVATION OF UNICODE PROPERTIES, NOT A LIST AND NOT ONE CATEGORY, AND ALL
    #: FOUR WERE MEASURED (#14237 r19/r21/r22). r18 shipped six codepoints and called the class
    #: closed. r19 replaced them with `unicodedata.category == "Cf"` and the class comment called
    #: it closed AGAIN — 164 of Cf's 170 members were open at r18, and then a WHOLE INVISIBLE
    #: FAMILY OUTSIDE Cf was open: Mn variation selectors and the combining grapheme joiner, Lo
    #: Hangul fillers, Mn Mongolian free variation selectors. "Cf" is the FORMAT category, not the
    #: "invisible" property, and a hand list is a second list that disagrees with Unicode the day
    #: a character is minted (#4170). So the question is asked of the PROPERTIES, through
    #: `regex`'s Unicode table, and never of a string of escapes.
    #: ⛔ EVERY TERM IS A MEASURED REPAIR, AND THE THREE ROUNDS BEFORE THIS ONE ARE THE REASON
    #: (#14237 r19/r21/r22):
    #:   · r19 shipped `Cf` alone. 164 of its 170 members had been open under r18's hand list,
    #:     and it MISSED a whole invisible family OUTSIDE Cf — Mn variation selectors, the
    #:     combining grapheme joiner, Lo Hangul fillers, Mn Mongolian free variation selectors.
    #:   · r21 replaced it with `Default_Ignorable_Code_Point` alone (4174 members), and the class
    #:     comment called the class closed a third time — while TWO Cf families that Unicode
    #:     excludes from that property for LAYOUT reasons (not because they render a glyph) were
    #:     still open: U+FFF9..FFFB (interlinear annotation anchor / separator / terminator) and
    #:     U+13430..1343F (Egyptian hieroglyph format controls) render NOTHING, and were measured
    #:     ACCEPTED in BOTH placements at r21's head `0c25ea83ba`. `\p{Cf}` puts them back.
    #:   · `\p{Cc}` − `\p{White_Space}` is the control family: a literal U+0007, U+001B or U+009F
    #:     between the name and its bracket renders as nothing and was ACCEPTED in both placements
    #:     at every head before this one, the Default_Ignorable-only class included (measured).
    #:   · THE SUBTRACTIONS are Unicode's own answer to "which of those the reader DOES see", not
    #:     this file's opinion: `Prepended_Concatenation_Mark` (U+0600..0605, U+06DD, U+070F,
    #:     U+0890..0891, U+08E2, U+110BD, U+110CD) renders a sign or mark in combination with
    #:     adjacent text, so a NOTE written beside one keeps its replacement credit — this is
    #:     r21's over-block removal, KEPT, and `test_a_Cf_member_that_DESIGNS_a_visible_mark_is_
    #:     NOT_stripped` pins it. `White_Space` keeps `\t`/`\n`/`\r` and NBSP out of the strip, so
    #:     `CALL_FORM`'s "a newline is never a call" and its visible-space bound still hold.
    #: ⛔ r21's "the 32 Cf-not-Default_Ignorable are EXACTLY the ones Unicode excludes because
    #: they render a sign or mark" was the defect this PR's §2 exists about — a stated bound
    #: wider than the behaviour. THIRTEEN of the 32 are Prepended_Concatenation_Mark (they do
    #: render a mark); NINETEEN are not, they render nothing, and r21's class left them ACCEPTED.
    #: Measured, one row per family: U+FFF9..FFFB and U+13430..1343F.
    #: ⛔ `(?V1)` IS LOAD-BEARING — in V0 syntax `regex` reads `--` as literal characters instead
    #: of set subtraction, and the class then silently measures something else (measured: a flat V0
    #: form STRIPPED the Prepended_Concatenation_Marks and the visible spaces, and a nested
    #: `[[…]--[…]]` form matched ZERO codepoints).
    #: ⛔ NOT a general "strip all Unicode" rule: NBSP and the other spaces a reader SEES are
    #: horizontal whitespace, already covered, and are deliberately left visible here.
    INVISIBLE_PROPERTY = (
        "(Default_Ignorable_Code_Point ∪ Cf ∪ Cc) "
        "− Prepended_Concatenation_Mark − White_Space")
    INVISIBLE_RE = regex.compile(
        r"(?V1)[\p{Default_Ignorable_Code_Point}\p{Cf}\p{Cc}"
        r"--\p{Prepended_Concatenation_Mark}--\p{White_Space}]+")

    #: ⚠️ THE DECLARED RESIDUALS, stated so they cannot be rediscovered as findings:
    #:   · U+2800 BRAILLE PATTERN BLANK renders an EMPTY CELL, not nothing, so no property lists
    #:     it as invisible and this class leaves it visible: a reader sees a blank between the name
    #:     and the bracket, and a line copied out of the doc carries that cell, so it is not the
    #:     cleanly copyable `name(` R1 forbids. "Blank glyph" is an UNBOUNDED class (any one-cell
    #:     space-like glyph) and enumerating it is the failure this class has repaired three times.
    #:     Pinned by `test_declared_residual_a_BLANK_GLYPH_that_is_NOT_the_property_is_accepted`.
    #:   · U+0000 is ACCEPTED at this head — and NOT because the class misses it: HTML5 replaces a
    #:     NUL with U+FFFD before the guard ever sees it (measured: the literal-NUL shape is
    #:     accepted while U+0007/U+001B/U+009F in the same shape are RED), so the reader is looking
    #:     at a VISIBLE replacement character. No strip can recover a character the parser rewrote.

    #: The CommonMark parser IS the block model. A hand-written punctuation/construct table
    #: was tried for four review rounds and every round found another construct it did not
    #: know — a pipe-less table, a list continuation, a nested item, a setext underline, `1)`,
    #: a thematic break, a TAB after a list marker, an HTML block, a link-reference
    #: definition. Reimplementing Markdown is the defect; `markdown-it-py` is already a
    #: declared test dependency of this repo (the `test` extra in pyproject, imported by
    #: tests/test_blog_authoring_doc.py), so the guard asks it instead of guessing.
    MD = MarkdownIt("commonmark").enable("table")

    #: A retired name in call form: the name followed by an OPENING BRACKET, optionally
    #: after HORIZONTAL space, an inline-markdown delimiter RUN (backtick, `*`, `_`, `~`),
    #: and/or an inline HTML TAG (`<code>`, `</code>`, `<kbd>`, `<br>`). `name(args)`,
    #: `name (args)`, `` `name`(args) `` and `<code>name</code>(args)` all RENDER as a call a
    #: reader can copy (#14237 r10 review P2 — the tag form was ACCEPTED before it was added).
    #: HORIZONTAL only — a newline is never a call.
    #:
    #: ⛔ HORIZONTAL SPACE IS A CLASS, NOT TWO CHARACTERS (#14237 r17, measured fail-open). The
    #: class was `[ \t]`, so `Call tortoise_list_graphs&nbsp;(x=1)` — which renders as
    #: `name (x=1)`, the SAME shape a plain space already reds — was ACCEPTED, because the
    #: renderer emits U+00A0 and the class did not hold it. `[^\S\n\r]` is exactly "horizontal
    #: whitespace": every separator a reader sees as a space (NBSP, EN SPACE, THIN SPACE …),
    #: and neither line terminator, so a newline stays not-a-call by construction.
    #:
    #: ⛔ AN INVISIBLE SEPARATOR IS NOT ADDED TO THIS CLASS, ON PURPOSE (#14237 r18). The
    #: `tortoise_list_graphs&#8203;(x=1)` shape is NOT closed here: widening the separator run
    #: was MEASURED dead weight — with the invisible character removed from the judged texts
    #: (`_strip_invisible`) the same occurrence is already RED, because the strip leaves the
    #: ordinary `name(` this class has always matched. One mechanism, in the one place that
    #: decides the reader's text, so the two cannot disagree about the same shape.
    CALL_FORM = re.compile(r"(?:[^\S\n\r]*(?:[`*_~]+|</?[A-Za-z][^>]*>))*[^\S\n\r]*\(")

    @classmethod
    def is_call_form(cls, text: str, at: int) -> bool:
        """True when the retired name ENDING at ``at`` is written as a CALL."""
        return cls.CALL_FORM.match(text, at) is not None

    @classmethod
    def _strip_invisible(cls, text: str) -> str:
        """The text with every `INVISIBLE_PROPERTY` member REMOVED — what a reader reads.

        Applied to the JUDGED texts only (`shown` and `rendered`), never to `body`: `body`
        decides R2's replacement credit and widening it would credit a replacement that
        carries a hidden character, which is the over-block direction. This normalisation is
        for DISCOVERY — a retired name split by a character the reader cannot see is the name
        to that reader (#14237 r18). Offsets are unaffected in any way that matters: every
        caller uses the return value's own coordinates only for the SIGN of `call_end`, and
        reports the block's START line, which comes from the original text.

        ⛔ THE CLASS COMES FROM UNICODE'S PROPERTY, NOT FROM A LIST AND NOT FROM A CATEGORY
        (#14237 r19/r21, both measured fail-open). r18's hand list held 6 of Cf's 170
        (`DerivedGeneralCategory.txt`; `unicodedata` 15.0.0 agrees) and the class comment said
        the class was closed, so the other 164 — a bidi mark or an invisible math operator
        between the name and its bracket, or inside the name — still returned `R1 [] R2 []` on
        text a reader reads as `tortoise_list_graphs(x=1)`. r19 asked `unicodedata.category`,
        which fixed those and still MISSED every invisible character outside Cf: a variation
        selector, the combining grapheme joiner, a Hangul filler, a Mongolian free variation
        selector — measured ACCEPTED at the r20 head in both placements. `Default_Ignorable_Code_Point`
        is the property that means "invisible", so it is the only correct answer to this
        question, and it is asked of `regex` because `unicodedata` does not expose it.
        """
        return cls.INVISIBLE_RE.sub("", text)

    @staticmethod
    def _inline_text(token, *, breaks: str = " ") -> str:
        """What the reader SEES of an inline token: text and inline code only.

        Deliberately excludes `html_inline` (a comment is not displayed) and link/image
        TARGETS and ATTRIBUTES (an `href` is not a door the reader can call). That is the
        whole of R2's "a replacement the reader can see": it closes the HTML-comment case and
        the link-target case, and — because link-reference definitions produce no visible
        text at all — the r9 review's reference-link P1 (#14237 r8/r9 reviews).

        ⛔ `breaks` IS A PARAMETER BECAUSE THE TWO RULES NEED OPPOSITE ANSWERS (#14237 r16).
        R2 asks "does this BLOCK name the replacement", and this doc hard-wraps at ~80
        columns, so a hard-wrapped line must JOIN and a break there is a space. R1 asks "is
        the name immediately followed by `(`", where a break before the bracket is declared
        NOT to be a call — with a space it would become one, and the guard would red a
        wrapped sentence it accepts today. So R1 passes `"\\n"` and R2 keeps the default.
        """
        parts: list[str] = []
        for child in token.children or []:
            if child.type in ("text", "code_inline", "text_special"):
                parts.append(child.content)
            elif child.type in ("softbreak", "hardbreak"):
                parts.append(breaks)
        return "".join(parts)

    @classmethod
    def blocks(cls, text: str) -> list[tuple[int, int, str]]:
        """Split markdown into ``(start, end, visible_text)`` LEAF blocks.

        The units come from the CommonMark parser's own token stream, so every construct
        markdown defines is a boundary BY CONSTRUCTION — a fence, a table CELL, a heading
        (ATX or setext), a list item (its marker-less continuation joins it; a nested item is
        its own), a blockquote line, a thematic break, an HTML block, and a
        blank-line-delimited paragraph. No punctuation table, so there is nothing to keep
        widening and no construct a future review can find missing.

        Table cells need their own offsets because the parser gives every cell in a row the
        ROW's line map; each cell's own source (`token.content`) is therefore located in the
        row text, in order, which is exact because the parser has already removed the pipes.

        This is the THREE-field projection of `_unit_records`, which is the one parse walk:
        the guard asks three questions of a unit (what is VISIBLE, what the SOURCE decodes to,
        and what the RENDERER emits) and they are answered in one place so they cannot
        disagree — the failure mode of every earlier round that answered one of them twice.
        """
        return [(start, end, body) for start, end, body, _shown, _rendered, _html
                in cls._unit_records(text)]

    @classmethod
    def _unit_records(cls, text: str) -> list[tuple[int, int, str, str, str, bool]]:
        """``(start, end, body, shown, rendered, is_html)`` for every judged unit.

        `body` — the VISIBLE text. The only text R2's replacement credit may read, so a
        replacement hidden in an HTML comment, a link destination or a link title cannot
        clear a mention. Empty for an opaque block.

        `shown` — the unit's DECODED SOURCE (`_decoded_source`). What DISCOVERY reads, because
        it still holds the containers `body` excludes: a mention in image alt text, a link
        destination or a title reds for the PLAIN spelling (the raw scan sees it), so the
        ENTITY spelling of the same mention has to be discoverable in the same text (#14237
        r14, measured).

        `rendered` — what the RENDERER emits: `&`-references decoded by the parser, BACKSLASH
        ESCAPES resolved by the parser, code spans left LITERAL, and a line break preserved as
        a newline. This is the text a reader copies a command out of, so R1 judges call form
        here as well as in the raw source (#14237 r16, measured fail-open: `name\\(x=1\\)`
        renders as a call and no reader saw it).

        `is_html` — whether the unit is an `html_block`, whose source is HTML rather than
        markdown and therefore needs the other decode.
        """
        starts = cls._line_starts(text)

        def off(line_no: int) -> int:
            return starts[line_no] if line_no < len(starts) else len(text)

        found: list[tuple[int, int, str, str, str, bool]] = []

        def record(begin: int, finish: int, body: str, rendered: str, is_html: bool) -> None:
            found.append((begin, finish, body,
                          cls._decoded_source(text[begin:finish], permissive_html=is_html),
                          rendered, is_html))

        row_cursor: int | None = None
        row_spans: list[tuple[int, int]] = []
        for token in cls.MD.parse(text):
            if token.type == "tr_open" and token.map:
                row_cursor = off(token.map[0])
                row_stop = off(token.map[1]) if token.map[1] is not None else len(text)
                row_spans = cls._row_cell_spans(text, row_cursor, row_stop)
                continue
            if token.type == "tr_close":
                row_cursor = None
                row_spans = []
                continue
            if token.type in ("fence", "code_block", "html_block") and token.map:
                begin, finish = off(token.map[0]), off(token.map[1])
                # ⛔ A FENCE'S INFO STRING IS NOT DISPLAYED (#14237 r11 review P2): it becomes a
                # `class` attribute. `token.content` is the code a reader sees, so a fence
                # ````tortoise_graph_overview` cannot satisfy R2 for its own body.
                body = token.content if token.type == "fence" else text[begin:finish]
                # A fence's content and a code block's source are shown LITERALLY: no escape
                # resolution, because a backslash inside code is a character the reader sees.
                rendered = token.content if token.type == "fence" else text[begin:finish]
                if token.type == "html_block":
                    # ⛔ AN HTML BLOCK IS OPAQUE, never tag-stripped (#14237 r10 review P1).
                    # A tag strip is a hand-written HTML guess and it is WRONG in the fail-OPEN
                    # direction: a `<script>`/`<style>` body, and a comment after a `>`, are
                    # never displayed, yet they stayed in the "visible" text and satisfied R2
                    # — measured: `<div>…<script>const door =
                    # "tortoise_graph_overview";</script></div>` ACCEPTED a dead-end mention.
                    # Nothing in the block counts as shown, so every mention inside it must be
                    # in R1 call form or it reds. Fail-CLOSED, and declared as a bound.
                    body = ""
                    # An HTML block is passed through RAW, so the browser's HTML5 decode is its
                    # rendering — that is `shown`, and `\\(` is not an escape in HTML at all.
                    rendered = cls._decoded_source(text[begin:finish], permissive_html=True)
                record(begin, finish, body, rendered, token.type == "html_block")
                continue
            if token.type != "inline":
                continue
            # ⛔ ONE RULE FOR "VISIBLE", APPLIED IN ONE PLACE (#14237 r10, r11 and r12 reviews).
            # A block whose inline source contains a `<` is OPAQUE: its visible text is EMPTY.
            # Deciding which inline container renders is HTML semantics, and every attempt to
            # approximate it from the source failed OPEN somewhere new — the tag strip (r10),
            # the inline `<script>`/`<style>`/`display:none` body (r11), and the raw-slice
            # segmentation (r11) that re-admitted image ALT text, link TITLES and reference
            # LABELS. Opaque is the fail-CLOSED direction: a mention in such a block cannot be
            # cleared by anything, so it reds unless the block itself is a bare note with no
            # markup at all. The over-block (a note containing any inline tag) is declared.
            # Otherwise `_inline_text` reads the RENDERED text from the token's children, which
            # already excludes image alt text (an `image` token's own children are not walked),
            # link destinations and `html_inline`.
            body = "" if "<" in token.content else cls._inline_text(token)
            rendered = cls._inline_text(token, breaks="\n")
            if row_cursor is not None:
                cell = token.content
                span = row_spans.pop(0) if row_spans else None
                at = text.find(cell, row_cursor)
                if at >= 0:
                    end = at + len(cell)
                elif span is not None:
                    # `find` cannot locate a cell whose source carries an escape the parser
                    # removed (`\|` -> `|`), so fall back to the row's own source span rather
                    # than a ZERO-LENGTH block (#14237 r10 review P2, measured false red).
                    at, end = span
                else:
                    at, end = row_cursor, row_cursor
                row_cursor = max(end, row_cursor)
                record(at, end, body, rendered, False)
                continue
            if token.map:
                record(off(token.map[0]), off(token.map[1]), body, rendered, False)
        return found

    @staticmethod
    def _line_starts(text: str) -> list[int]:
        """Character offset of every line start, plus the end of the document."""
        starts = [0]
        for line in text.splitlines(keepends=True):
            starts.append(starts[-1] + len(line))
        return starts

    @classmethod
    def _raw_html_block_spans(cls, text: str) -> set[tuple[int, int]]:
        """`(start, end)` of every `html_block` — the blocks the renderer passes through RAW.

        It is the only block kind whose source is HTML rather than markdown, so it is the only
        one that needs a DIFFERENT decode (see `_decoded_source`). `blocks()` already
        distinguishes it — it reports an opaque empty body for it — but the reason is not
        recoverable from the body alone: an inline `<br>` also makes a block opaque.

        Derived from `_unit_records`'s own `is_html` field (#14237 r16), so there is ONE parse
        walk and the two readings of the tree cannot drift apart. `_unblocked_spans` uses it
        to ask which gap units the parser refused to place at all.
        """
        return {(start, end)
                for start, end, _body, _shown, _rendered, is_html in cls._unit_records(text)
                if is_html and end > start}

    #: CommonMark's BACKSLASH ESCAPE: a `\` before ASCII punctuation renders as the punctuation
    #: itself. Used ONLY for the GAP units (`_unblocked_spans`), which have no parser token to
    #: read — every unit that does have one takes its rendered text from the parser, which
    #: applies this rule everywhere EXCEPT inside code spans, where a backslash is literal.
    ESCAPE = re.compile(r"\\([" + re.escape(string.punctuation) + r"])")

    @classmethod
    def _escape_resolution(cls, source: str) -> str:
        """The source with markdown's backslash escapes resolved (see `ESCAPE`)."""
        return cls.ESCAPE.sub(r"\1", source)

    #: The CommonMark character-reference rule, and ONLY it: `&`, an optional `#`/`#x`, and a
    #: TERMINATING `;`. ⛔ SEMICOLON-STRICT ON PURPOSE. `html.unescape` implements HTML5, which
    #: also consumes a semicolon-less numeric reference — decoding MARKDOWN text with it
    #: invents a mention the reader never sees: markdown-it renders
    #: `&#116ortoise_list_graphs(x=1)` LITERALLY (`<p>Call &amp;#116ortoise_list_graphs…`), which
    #: is why the guard accepts that shape. Measured (#14237 r14): the permissive decode made
    #: the SAME literal sentence red as soon as an unrelated `<br>` made its block opaque.
    CHARACTER_REFERENCE = re.compile(
        r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});")

    @classmethod
    def _decoded_source(cls, raw: str, *, permissive_html: bool) -> str:
        """The block's SOURCE as the renderer its OWN language belongs to decodes it.

        One question with two answers, because `blocks()` already separates the two
        languages: markdown text is decoded by CommonMark's rule (`CHARACTER_REFERENCE`, `;`
        required), while an `html_block` is passed through as RAW HTML and decoded by the
        browser's HTML5 algorithm, which consumes a numeric reference with or without the
        terminator. Using ONE decode for both is what produced both r14 findings — the
        permissive one invented a mention in markdown text, and the visible-body one could
        not see a real mention in a container the body excludes.

        ⛔ THIS IS THE SOURCE DECODE, AND IT DELIBERATELY RESOLVES ONLY REFERENCES. Markdown's
        OTHER spelling mechanism is the backslash escape (`\\_`, `\\(`), which the INLINE
        PARSER resolves — and only outside code spans, where a backslash is literal. So the
        escape spelling is read from the parser's own output (`_unit_records`'s `rendered`),
        never approximated here: a source-level escape strip would red
        `` `name\\(x=1\\)` `` inside a code span, where the reader sees the backslashes.
        ``_escape_resolution`` exists only for the GAP units, which have no parser output at all.
        """
        if permissive_html:
            return html.unescape(raw)
        return cls.CHARACTER_REFERENCE.sub(lambda m: html.unescape(m.group(0)), raw)

    @classmethod
    def _row_cell_spans(cls, text: str, row_start: int,
                        row_stop: int) -> list[tuple[int, int]]:
        """The SOURCE span of every cell in one table row, in order.

        Derived by splitting the row's own source on **unescaped** pipes, not by
        `text.find(token.content)`. ⛔ MEASURED (#14237 r10 review P2): markdown-it UNESCAPES
        `\\|` inside a cell's `content`, so `find` misses a cell that contains an escaped
        pipe — and the old fallback produced a ZERO-LENGTH block, which reported a CORRECT
        note (mention and replacement in the same cell) as `NO BLOCK`, a false red. The
        parser emits one `inline` token per cell including an empty one (measured), so these
        spans line up with the cell tokens one-to-one.
        """
        raw = text[row_start:row_stop]
        spans: list[tuple[int, int]] = []
        at = row_start
        for chunk in re.split(r"(?<!\\)\|", raw):
            spans.append((at, at + len(chunk)))
            at += len(chunk) + 1
        # GFM's outer pipes are optional, and each one contributes an empty span.
        if raw.lstrip().startswith("|"):
            spans = spans[1:]
        if raw.rstrip().endswith("|") and not raw.rstrip().endswith("\\|"):
            spans = spans[:-1]
        return spans

    @classmethod
    def replacements(cls, name: str) -> set[str]:
        """The live doors `RETIRED_USE_INSTEAD[name]` tells the reader to use instead.

        A pointer is prose: it can name one live door, two (`tortoise_mine_conversations`),
        or the same one twice (`tortoise_delete` preview + confirm).
        """
        return set(cls.REPLACEMENT_NAME.findall(RETIRED_USE_INSTEAD[name]))

    @classmethod
    def _entity_only_mentions(cls, text: str) -> list[tuple[str, int, str, int]]:
        """Mentions a reader SEES that the raw scan cannot: the name is ENTITY-SPELLED.

        ⛔ MEASURED (this fix round, same class as the r10/r11/r12 findings):
        ``Call &#116;ortoise_list_graphs(x=1) for names.`` renders — to the reader, and to
        a browser — as a call to the retired door, and BOTH rules passed it, because
        `name_hits` scans the RAW source where the token reads `#116;ortoise_list_graphs`.
        That is a false PASS of the guard's own promise, not a cosmetic gap.

        A name whose decoded occurrence count EXCEEDS its whole-token count in the block's
        raw source is (partly) entity-spelled — and only those are returned, so a plainly
        written mention stays the raw scan's to judge and no existing verdict changes
        direction.

        ⛔ THE JUDGED TEXT IS THE BLOCK'S DECODED SOURCE, IN EVERY BLOCK (#14237 r14 review,
        measured fail-open). The first cut judged `body` whenever the block had visible text,
        and `body` is exactly what `blocks()` strips the invisible containers out of — image
        ALT text, a link's DESTINATION and TITLE, a link-reference label. A retired name
        ENTITY-spelled in any of those places was therefore invisible to ALL FOUR readers and
        ACCEPTED, while the PLAIN spelling in the very same place reds (the raw scan sees it):
        `[docs](https://x.invalid "&#116;ortoise_list_graphs")` came back CLEAN and
        `[docs](https://x.invalid "tortoise_list_graphs")` came back a dead end. The verdict
        may not depend on the SPELLING — that is this reader's whole premise — so discovery
        reads the decoded SOURCE, the exact counterpart of what `name_hits` already scans.
        What `body` still governs is R2's REPLACEMENT credit, and that is what keeps the
        r11/r12 exclusions (a hidden replacement does not count) intact: this is not their
        reopening.

        ⛔ WHY A COUNT AND NOT `name in raw`, which was the first cut and was MEASURED
        insufficient: a block may carry a plain mention AND an entity-spelled CALL of the
        same door, e.g. ``The door tortoise_list_graphs is retired; use
        tortoise_graph_overview. Also call &#116;ortoise_list_graphs(x=1).`` — `name in raw`
        is True there, so the call was skipped, and R2 passes because the block does name the
        successor. The excess count is order-independent, so neither a preceding nor a
        following plain mention can hide the entity occurrence.

        ⛔ AN OPAQUE BLOCK IS NOT SKIPPED (#14237 r13 review P2, measured fail-open). The
        first cut bailed on `not body`, and `blocks()` sets `body = ""` for EVERY opaque
        block — an `html_block`, and any block whose inline source contains a `<` — because
        nothing in such a block may clear R2. So the ENTITY spelling of a name was invisible
        to all four readers whenever the same paragraph carried an autolink or a `<br>`:
        the raw scan cannot see `#116;ortoise_list_graphs`, and this reader skipped the block
        whose visible body is empty by construction. `Call &#116;ortoise_list_graphs(x=1)
        for names. <br>` was ACCEPTED. It is the intersection of the r10/r12 fixes that was
        left open, and the class comment already promised the opposite, so it is closed here
        rather than declared.

        ⛔ THE SOURCE BETWEEN BLOCKS IS A UNIT TOO (#14237 r15, measured fail-open). Walking
        `blocks()` cannot reach text the parser places in NO leaf — and a link-REFERENCE
        DEFINITION is exactly that: `[foo]: &#116;ortoise_list_graphs` produces no block, so
        the ENTITY spelling of the mention had no reader that could see it and was ACCEPTED,
        while the PLAIN spelling in the same definition is reported as "NO BLOCK" by R2 and
        is an R1 call when it carries arguments. The verdict depended on the SPELLING again,
        which is this reader's whole premise, and the class comment already promised that a
        mention outside every block is REPORTED. The units are therefore every BLOCK and the
        GAPS between them, decoded in source order; a gap carries no visible text (body ""),
        so a mention there can never be cleared by anything — the same fail-closed verdict
        the plain spelling already gets.

        Returns ``(name, block_start, body, call_end)`` where `call_end` is the end offset of
        a CALL-FORM occurrence in one of the two judged texts, or -1. Only its SIGN is the
        contract every caller uses (`>= 0` = a call form was seen); the offset itself is
        meaningful only within the text that produced it. Reporting the block's START line is
        deliberate — the source offset of a decoded character does not exist.

        ⛔ MARKDOWN'S OTHER SPELLING MECHANISM (#14237 r16, measured fail-open, same class as
        r10-r15). A character reference is not the only way the source can say one thing and
        the reader see another: a BACKSLASH ESCAPE does the same (`tortoise\\_list\\_graphs`
        renders as the retired name, `name\\(x=1\\)` renders as a copyable call). Measured at
        the r15 head: `The tool tortoise\\_list\\_graphs lists names.` returned BOTH rules empty
        — ACCEPTED — while the plain spelling of the very same sentence is a dead end, and
        `Call tortoise_list_graphs\\(x=1\\) for names. Use tortoise_graph_overview(...)`
        returned both empty too, while `Call tortoise_list_graphs(x=1) …` reds R1. The escape
        is resolved by the PARSER, so the text a reader copies from is now read from the
        parser's own output (`_unit_records`'s `rendered`) instead of being approximated on
        the source — which is why an escaped bracket inside a CODE SPAN stays literal and is
        correctly not a call. This reader is therefore no longer "entity only"; the name is
        kept because every review round in this file's prose refers to it by that name, and
        the docstring, not the identifier, is the contract.
        """
        found: list[tuple[str, int, str, int]] = []
        # ONE ordered unit list: every leaf block, plus the source the parser placed in none
        # of them. `sort` keeps the reports in SOURCE order, which is what makes this
        # reader's output comparable, line for line, with the raw scan's.
        units = [(start, end, body,
                  cls._strip_invisible(shown), cls._strip_invisible(rendered))
                 for start, end, body, shown, rendered, _is_html in cls._unit_records(text)]
        for start, stop in cls._unblocked_spans(text):
            raw = text[start:stop]
            # A GAP renders NOTHING, so there is no parser output to read; its `rendered` is
            # its source with BOTH spelling mechanisms resolved, which is the closest
            # counterpart of what the renderer would make of that source.
            units.append((start, stop, "",
                          cls._strip_invisible(
                              cls._decoded_source(raw, permissive_html=False)),
                          cls._strip_invisible(
                              cls._decoded_source(cls._escape_resolution(raw),
                                                  permissive_html=False))))
        units.sort(key=lambda unit: (unit[0], unit[1]))
        for begin, finish, body, shown, rendered in units:
            raw = text[begin:finish]
            # ⛔ ONE SPELLING MECHANISM CHANGES THE DECODED SOURCE, AND IT IS `&` (#14237 r17,
            # measured fail-open). This gate used to be `if "&" not in raw and "\\" not in
            # raw: continue`, on the stated premise that `&` and `\` are "the only ways the
            # source can differ from the text the reader sees while a retired name is on
            # screen". THAT PREMISE WAS FALSE and the gate was the bug: rendering also
            # REMOVES MARKUP, so markup INSIDE the name splits it into raw tokens that are
            # not retired names at all — `Call tortoise_list_graph*s*(x=1) for names. Use
            # tortoise_graph_overview(...)` and `…tortoise_list_graph``s``(x=1)…` both render
            # as a copyable call to the retired door and both returned `R1 [] R2 []`. So the
            # RENDERED arm now runs for EVERY unit, and only `shown` stays behind the `&`
            # gate — which is exact rather than approximate, because `_decoded_source`
            # resolves character references and NOTHING else: with no `&` in the source it
            # returns the source unchanged.
            judged_texts = (shown, rendered) if "&" in raw else (rendered,)
            # ONE entry per (name, unit): every arm below can discover the same door, and two
            # entries for one mention would report it twice.
            calls: dict[str, int] = {}
            # WHAT THIS UNIT'S MENTIONS ARE JUDGED ON, and it takes TWO texts (#14237 r14, r16).
            # `shown` (the DECODED SOURCE) is where a spelling-variant name is DISCOVERED,
            # because it still holds the containers `body` excludes — image alt text, a link's
            # destination and title — where the PLAIN spelling reds (the raw scan sees it) so
            # the other spelling must not clear it. `rendered` (the parser's own output) is
            # where a spelling-variant CALL is JUDGED, because only the renderer resolves an
            # escape and knows a code span keeps it literal.
            for judged in judged_texts:
                for name, call_end in cls._excess_mentions(raw, judged):
                    calls[name] = call_end if call_end >= 0 else calls.get(name, -1)
            # ⛔ A *PLAIN* NAME WHOSE CALL FORM IS SPELLED WITH AN ESCAPE OR A REFERENCE
            # (#14237 r16, measured fail-open): `Call tortoise_list_graphs\\(x=1\\) for
            # names. Use tortoise_graph_overview(...)` returned R1 empty — the NAME is plain,
            # so the excess count above sees nothing — while a reader sees a copyable call to
            # the retired door. R1 is "a name in call form may not appear at all", so the
            # judgement is taken from the rendered text for EVERY name, not only the ones
            # whose own spelling is unusual. A name the raw scan ALREADY reds as a call inside
            # this unit is skipped: R1 reports it at its own line and a second entry would
            # report one mention twice.
            raw_calls = {name for name, _start, end in cls.name_hits(raw)
                         if cls.is_call_form(raw, end)}
            for name, call_end in cls._rendered_call_forms(rendered):
                if name in raw_calls:
                    continue
                if calls.get(name, -1) < 0:
                    calls[name] = call_end
            for name, call_end in calls.items():
                # `found` still carries `body`, never `shown`: R2's replacement credit reads the
                # VISIBLE text, so a replacement hidden in an HTML comment, a link destination
                # or a link title cannot clear a mention (the r11/r12 rule this preserves).
                found.append((name, begin, body, call_end))
        return found

    @classmethod
    def _rendered_call_forms(cls, rendered: str) -> list[tuple[str, int]]:
        """Every retired name the RENDERED text shows in CALL form, as ``(name, end)``."""
        return [(name, end) for name, _start, end in cls.name_hits(rendered)
                if cls.is_call_form(rendered, end)]

    @classmethod
    def _excess_mentions(cls, raw: str, judged: str) -> list[tuple[str, int]]:
        """Names the JUDGED text carries MORE often than the raw one — i.e. spelled differently.

        A count, not `name in raw`: a unit may carry a plain mention AND a spelling-variant
        occurrence of the same door, and the excess is order-independent. Whole-token on both
        sides, so neither a preceding nor a following plain mention can hide the other
        spelling. Called twice per unit — once with the decoded SOURCE (discovery) and once
        with the RENDERED text (escapes) — because a name can be spelled unusually in either.
        Returns ``(name, call_end)``; `call_end` is the end offset of the first occurrence
        written in CALL form within `judged`, or -1.
        """
        raw_counts: dict[str, int] = {}
        for name, _start, _end in cls.name_hits(raw):
            raw_counts[name] = raw_counts.get(name, 0) + 1
        ends_by_name: dict[str, list[int]] = {}
        for name, _start, end in cls.name_hits(judged):
            ends_by_name.setdefault(name, []).append(end)
        excess: list[tuple[str, int]] = []
        for name, ends in ends_by_name.items():
            if len(ends) <= raw_counts.get(name, 0):
                continue
            excess.append((name, next((e for e in ends if cls.is_call_form(judged, e)), -1)))
        return excess

    @classmethod
    def _unblocked_spans(cls, text: str) -> list[tuple[int, int]]:
        """`(start, end)` of the source OUTSIDE every leaf block, in source order.

        A link-reference DEFINITION lives here: the CommonMark parser consumes it and emits no
        leaf for it, which is why `notes_missing_replacement` reports a PLAIN mention there as
        "NO BLOCK" (#14237 r9 review P1). The ENTITY spelling had no reader reaching it at
        all, because the entity reader walked the blocks (#14237 r15, measured fail-open).
        """
        spans = sorted((start, stop) for start, stop, _body in cls.blocks(text) if stop > start)
        gaps: list[tuple[int, int]] = []
        cursor = 0
        for start, stop in spans:
            if start > cursor:
                gaps.append((cursor, start))
            cursor = max(cursor, stop)
        if cursor < len(text):
            gaps.append((cursor, len(text)))
        return gaps

    @classmethod
    def retired_call_forms(cls, text: str) -> list[str]:
        """R1 — every retired name written as a call, as ``name:line`` (empty is clean)."""
        found: list[str] = []
        for name, _start, end in cls.name_hits(text):
            if not cls.is_call_form(text, end):
                continue
            line = text.count("\n", 0, _start) + 1
            found.append(f"{name}:{line}")
        for name, begin, _body, call_end in cls._entity_only_mentions(text):
            if call_end < 0:
                continue
            found.append(f"{name}:{text.count(chr(10), 0, begin) + 1}")
        return found

    @classmethod
    def notes_missing_replacement(cls, text: str) -> list[str]:
        """R2 — every non-call mention whose BLOCK never names the replacement.

        The block comes from the CommonMark parser, and its `body` is already the block's
        VISIBLE text (see `blocks`), so a replacement in an HTML comment, a link target or a
        link-reference definition cannot satisfy R2. A mention that falls in NO block at all —
        a link-reference definition, a document preamble — is reported too: a mention the
        reader can see but the model cannot place is a dead end by default, which is the
        fail-closed direction.
        """
        found: list[str] = []
        blocks = cls.blocks(text)
        for name, start, end in cls.name_hits(text):
            if cls.is_call_form(text, end):
                continue
            body = next(
                (body for b0, b1, body in blocks if b0 <= start < b1),
                None)
            if body is None or not (
                    cls.replacements(name) & set(cls.REPLACEMENT_NAME.findall(body))):
                line = text.count("\n", 0, start) + 1
                where = "NO BLOCK" if body is None else " ".join(body.split())[:120]
                found.append(f"{name}:{line} -> {where}")
        for name, begin, body, call_end in cls._entity_only_mentions(text):
            if call_end >= 0:
                continue
            if cls.replacements(name) & set(cls.REPLACEMENT_NAME.findall(body)):
                continue
            line = text.count("\n", 0, begin) + 1
            where = " ".join(body.split())[:120]
            if not where and not any(b0 <= begin < b1 for b0, b1, _b in blocks):
                # SAY WHICH KIND of unplaceable this is, in the one case where the plain
                # spelling says the same thing: a mention in NO BLOCK at all (a link-reference
                # definition) is outside the block model, exactly as R2 reports the plain
                # spelling above. An OPAQUE block has its own empty rendering, unchanged, so
                # the entity report matches the plain one line for line either way.
                where = "NO BLOCK"
            found.append(f"{name}:{line} -> {where}")
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

    def test_the_block_model_is_the_commonmark_parser(self):
        """The block units are the PARSER's, pinned EXACTLY so a hand-written model reds.

        ⛔ WHY THIS ROW EXISTS (#14237 r9 review, and the four rounds before it). The previous
        model was a regex table of constructs, and every review round found another construct it
        did not know — a pipe-less table (r6), a setext underline and a blockquote (r7), `1)`, a
        lone `-` and `***` (r8), a TAB after a list marker, an HTML block and a link-reference
        definition (r9). Reimplementing Markdown is the defect, so the model is now
        `MarkdownIt("commonmark").enable("table")`, a DECLARED test dependency of this repo (the
        `test` extra in pyproject), and every construct markdown defines is a boundary by
        construction.

        ⛔ THE ASSERTION IS THE EXACT BLOCK LIST (#14237 r10 review P2): an earlier form asserted
        `len(blocks) >= 4` plus two `any(...)` checks, and a hand-written model kept alongside
        the parser satisfied all of them, so the row could not fail under the revert it names.
        The bodies below are only reachable through the parser: the table is split into its
        CELLS, the HTML block contributes an OPAQUE empty body, the fence carries its own body,
        and the list item carries its continuation line.
        """
        active = self.MD.get_active_rules()["block"]
        for rule in ("heading", "lheading", "list", "blockquote", "hr", "fence",
                     "html_block", "table"):
            assert rule in active, (rule, active)
        text = ("note one\n\n| a | b |\n| --- | --- |\n| c | d |\n\n<details>\nitem\n"
                "</details>\n\n```py\ncode()\n```\n\n- item note\n  continuation\n")
        assert [body for _start, _end, body in self.blocks(text)] == [
            "note one", "a", "b", "c", "d", "", "code()\n",
            "item note continuation",
        ], self.blocks(text)

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

        ⛔ MEASURED (#14237 r7 review P2, re-measured under the parser model at r10): where the
        blank line did not end the item, this shape went from REJECTED to ACCEPTED and an
        indented code sample under a parent item's note borrowed that note's replacement. This
        is the boundary that keeps the r5 fence
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

    @pytest.mark.parametrize("mid", [
        "\n",                     # a blank line (a bare newline is NOT one)
        "---\n",                  # setext underline (level 2, three dashes)
        "-\n",                    # setext underline (level 2, ONE dash)
        "===\n",                  # setext underline level 1
        "***\n",                  # thematic break
        "___\n",                  # thematic break
        "# Heading\n",            # ATX heading
        "- a list item\n",        # unordered item
        "1. a list item\n",       # ordered item, dot
        "1) a list item\n",       # ordered item, PAREN (missed until r8)
        "-\ta list item\n",       # marker followed by a TAB (missed until r9)
        "<details>\n",            # an HTML block (missed until r9)
        "> a quote\n",            # blockquote
        "| a | b |\n| --- | --- |\n",   # a REAL table (a lone pipe line is a paragraph)
        "```\nx\n```\n",          # fence
    ], ids=["blank", "setext-3", "setext-1", "setext-1-eq", "hr-star", "hr-underscore",
            "heading", "ul-item", "ol-dot", "ol-paren", "tab-item", "html-block",
            "blockquote", "table", "fence"])
    def test_a_paragraph_is_CLOSED_by_every_block_start(self, mid):
        """Every close of the open paragraph, pinned by a verdict that FLIPS.

        ⛔ WHAT THIS ROW PINS, and the history it inherits. It asserts that a block start
        CLOSES the open paragraph, so the instruction after it cannot inherit the note above:
        every kind of block start is a case below. The failure it defends against was measured
        under the RETIRED hand-written model (#14237 r8 review P1), where five of that
        splitter's closing flushes could be deleted with every row still green and the note
        stayed open and swallowed the instruction; that splitter is GONE (replaced by the
        CommonMark parser, see `blocks`), so those flushes no longer exist and are not what
        this row can fail on today. What it can fail on is a block start that the model stops
        treating as one — measured at r9/r10, that is still the shape that fails open.
        """
        note = "Note: `tortoise_list_graphs` retired into `tortoise_graph_overview`.\n"
        text = note + mid + "Call tortoise_list_graphs for names.\n"
        assert self.notes_missing_replacement(text), (
            f"the open paragraph was NOT closed by {mid!r}, so the instruction below it "
            "inherited the note's replacement — that block start does not end a paragraph")

    @pytest.mark.parametrize("mid", ["1) ", "-\n", "***\n", "___\n"],
                             ids=["ol-paren", "setext-1", "hr-star", "hr-underscore"])
    def test_the_leaf_blocks_markdown_defines_all_start_a_block(self, mid):
        """#14237 r8 review P1 — three markdown leaf kinds the r7 forms missed.

        MEASURED before the fix: `1)` (an ordered-list marker), a LONE `-` (a legal setext
        underline), and a `***`/`___` thematic break each merged the note and the instruction
        into ONE block, so a bare retired call was ACCEPTED — the exact fail-open the
        structural rule replaced the English heuristic to close. markdown-it renders each as
        its own block (an `<ol>`, an `<h2>`, an `<hr />`), which is the oracle the class
        docstrings cite.
        """
        note = "Note: `tortoise_list_graphs` retired into `tortoise_graph_overview`.\n"
        text = note + mid + "Call tortoise_list_graphs for names.\n"
        assert len(self.blocks(text)) >= 2, self.blocks(text)
        assert self.notes_missing_replacement(text), (
            f"{mid!r} did not start a block, so an instruction under it inherited the note")

    @pytest.mark.parametrize("text,arm", [
        ("Call _tortoise_list_graphs_(graph_id=x) to read one graph.\n", "R1"),
        ("Call __tortoise_list_graphs__(graph_id=x) to read one graph.\n", "R1"),
        ("The MCP tool _tortoise_list_graphs_ lists graph names.\n", "R2"),
    ], ids=["underscore-em", "underscore-strong", "underscore-bare"])
    def test_the_underscore_emphasis_spelling_is_still_the_name(self, text, arm):
        """`_` is a word character, so `\b` saw `_name_` as ONE token and missed it entirely.

        ⛔ MEASURED (#14237 r9 review P1): both the copyable call `_name_(x)` and the bare
        dead-end `_name_` were ACCEPTED, and markdown renders both with the retired name
        visible as italic/strong text. The name is now matched as a whole TOKEN with
        leading/trailing emphasis underscores stripped, so this spelling is caught while
        `tortoise_delete_entity` still cannot match `tortoise_delete` (internal underscores are
        part of the name).
        """
        if arm == "R1":
            assert self.retired_call_forms(text), (
                f"the emphasis spelling of a CALL was missed: {text!r}")
        else:
            assert self.notes_missing_replacement(text), (
                f"the emphasis spelling of a dead-end NOTE was missed: {text!r}")

    @pytest.mark.parametrize("text", [
        # #14237 r8 review P2: the replacement existed only where a reader cannot see it.
        "<!-- the live door is `tortoise_graph_overview` -->\nCall `tortoise_list_graphs` for names.\n",
        "[`tortoise_list_graphs`](https://example.invalid/tortoise_graph_overview)\n",
        "Call `tortoise_list_graphs` for names. <span title=\"tortoise_graph_overview\">i</span>\n",
        # #14237 r9 review P1: a link-REFERENCE definition is not displayed at all.
        "[1]: https://example.invalid/tortoise_graph_overview\nCall `tortoise_list_graphs` for names.\n",
        "See [tortoise_list_graphs][1] for names.\n\n[1]: https://example.invalid/tortoise_graph_overview\n",
        "<!-- see > the live door is `tortoise_graph_overview` -->\nCall `tortoise_list_graphs` for names.\n",
    ], ids=["html-comment", "link-target", "html-attribute", "ref-definition",
            "ref-link-text", "comment-with-gt"])
    def test_a_replacement_a_reader_cannot_SEE_is_not_a_replacement(self, text):
        """R2 intersects the block's VISIBLE text, not its markdown source.

        ⛔ MEASURED before the fix: all three of these were ACCEPTED, and markdown-it renders
        each with the retired name still the only door shown to the reader (the comment and
        the `href` are not displayed text). A replacement in a URL fragment is not a door the
        reader can call.
        """
        assert self.notes_missing_replacement(text), (
            "an INVISIBLE replacement satisfied R2 — the rendered block still gives the "
            "reader the retired name and no live door")

    # ⛔ A TABLE CANNOT INTERRUPT A LIST ITEM in CommonMark (measured: the "table" stays
    # inside the item's paragraph, so there is no second block to close it — the old
    # hand-written model called that a row and this param, which the r9 round caught,
    # graded nothing). Heading, fence and thematic break DO interrupt, so they carry it.

    def test_a_mention_outside_every_block_is_reported(self):
        """A mention the model cannot PLACE is a dead end by default (fail-closed).

        ⛔ MEASURED (#14237 r9 review P1): a retired name inside a link-REFERENCE definition
        is invisible to the reader AND outside every leaf block, so the only safe verdict is
        "reported". Without this row the code could treat `body is None` as CLEAN — measured:
        that mutant leaves all 159 rows green — and a mention the parser cannot place would
        silently pass.
        """
        text = ('[1]: https://example.invalid/`tortoise_list_graphs`\n\n'
                'The live door is `tortoise_graph_overview(section="graphs")`.\n')
        assert self.notes_missing_replacement(text), (
            "a mention outside every block was treated as clean — an unplaceable mention "
            "must fail closed")
        assert "NO BLOCK" in self.notes_missing_replacement(text)[0], (
            self.notes_missing_replacement(text))

    def test_an_escaped_pipe_in_a_cell_does_not_lose_the_cell(self):
        """A cell containing `\\|` is still ONE block, at its own SOURCE span.

        ⛔ MEASURED (#14237 r10 review P2): markdown-it UNESCAPES `\\|` inside a cell's
        `token.content`, so `text.find(cell, row_cursor)` missed the cell and the fallback
        produced a ZERO-LENGTH block — which reported a CORRECT note (the mention and its
        replacement in the SAME cell) as `NO BLOCK`. That is a false red on a document that
        obeys the rule; the direction is fail-closed, but the cost is a correct doc refused.
        """
        row = ("| door | note |\n"
               "| --- | --- |\n"
               "| x | `tortoise_list_graphs` retired forward into a \\| b "
               "`tortoise_graph_overview` |\n")
        assert not self.notes_missing_replacement(row), (
            "a correct note in a cell containing an escaped pipe was refused: "
            f"{self.notes_missing_replacement(row)}")
        held = [b for b in self.blocks(row) if "tortoise_list_graphs" in b[2]]
        assert held, f"the mention's cell was not located at all: {self.blocks(row)}"
        assert all(b1 > b0 for b0, b1, _ in held), (
            f"the cell holding the mention is a ZERO-LENGTH block: {held}")

    @pytest.mark.parametrize("mid", ["# Heading\n", "```\nx\n```\n", "***\n"],
                             ids=["heading", "fence", "thematic-break"])
    def test_a_list_item_is_CLOSED_by_the_next_structural_block(self, mid):
        """A structural block CLOSES the open list item, pinned.

        ⛔ WHY THE PARAMS ARE WHAT THEY ARE (#14237 r8 P1, corrected at r10 P2). The property is
        "the block that starts here CLOSES the open list item", so an indented instruction under
        it cannot inherit the item's note. The params are exactly the constructs that INTERRUPT a
        list item in CommonMark — a heading, a fence and a thematic break. A TABLE ROW cannot:
        measured, markdown-it keeps `| a | b |` + its delimiter row INSIDE the item's paragraph,
        so the old `table-row` param graded nothing (it was removed, not fixed, and the property
        is unchanged).
        """
        item_note = "- note: the live door is `tortoise_graph_overview(section=\"graphs\")`.\n"
        instruction = "  Call tortoise_list_graphs for names.\n"
        text = item_note + mid + instruction
        assert self.notes_missing_replacement(text), (
            f"the open list item was NOT closed by {mid!r}, so an instruction indented under "
            "it inherited the note's replacement")

    def test_a_table_cell_is_its_own_block(self):
        """A row has no terminator, so cell scoping keeps a note from clearing an instruction.

        ⛔ THE PROPERTY IS KEPT FROM THE SENTENCE-ERA TEST, RE-EXPRESSED AGAINST THE RULE THAT
        SHIPPED (#14237 round-2 review, measured; the peer's sentence-era row
        `test_a_table_cell_is_its_own_sentence` was merged into THIS test and no longer exists).
        It is the row-unit fail-open: a migration
        note in one cell and an INSTRUCTION in the next share a markdown row, so a ROW-unit R2
        would clear the instruction on its neighbour's replacement. Measured below on both
        halves — the instruction cell alone reds, the note cell alone passes.
        """
        # A REAL GFM table: header row, delimiter row, then the graded row. A lone pipe
        # line is a PARAGRAPH to CommonMark — the old hand-written model called it a row,
        # which is why this fixture is spelled out (#14237 r9 review).
        row = ("| door | note | live |\n"
               "| --- | --- | --- |\n"
               "| `GRAPH_LISTING` | call `tortoise_list_graphs` for names "
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
        `test_a_list_item_keeps_its_indented_continuation`.
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
        # #14237 r10 review P2: a call wrapped in inline HTML renders as the same call.
        "Call <code>tortoise_list_graphs</code>(graph_id=x). Door: tortoise_graph_overview.\n",
        "Call <kbd>tortoise_list_graphs</kbd>(x). Door: tortoise_graph_overview.\n",
        "Call `tortoise_list_graphs`<br>(x). Door: tortoise_graph_overview.\n",
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

    def test_an_HTML_block_is_opaque(self):
        """#14237 r10 review P1 — the named fixture for the fail-open an HTML block caused.

        ⛔ MEASURED BEFORE THE FIX: the block's "visible" text came from a tag strip, so a
        `<script>` body, a `<style>` body or a comment after a `>` — none of which a reader
        sees — satisfied R2 for a dead-end mention below it and the document was ACCEPTED.
        An HTML block's text is now EMPTY, so every mention inside one must be a CALL or red.
        """
        for body in (
            '<script>const d = "tortoise_graph_overview";</script>',
            '<style>/* tortoise_graph_overview */</style>',
            '<!-- a > b: tortoise_graph_overview -->',
            '<img alt="a > b" src="tortoise_graph_overview.png">',
        ):
            text = f'<div>\nCall tortoise_list_graphs for names.\n{body}\n</div>\n'
            assert self.blocks(text) and all(b == "" for _s, _e, b in self.blocks(text)), (
                f"an HTML block's body is not opaque: {self.blocks(text)}")
            assert self.notes_missing_replacement(text), (
                f"an invisible HTML construct satisfied R2: {body}")

    @pytest.mark.parametrize("hidden", [
        '<script>const d = "tortoise_graph_overview";</script>',
        '<style>/* tortoise_graph_overview */</style>',
        '<span style="display:none">tortoise_graph_overview</span>',
    ], ids=["script", "style", "display-none"])
    def test_an_inline_HTML_span_is_a_block_boundary(self, hidden):
        """#14237 r11 review P1 — inline HTML is opaque, like an HTML block.

        ⛔ MEASURED BEFORE THE FIX: keeping the paragraph as one block and reading its text
        children let the BODY of an inline `<script>`/`<style>` — or a `display:none` span —
        satisfy R2, so a dead-end mention was ACCEPTED while a browser shows the reader the
        retired door and nothing else. Deciding which inline containers render is HTML
        semantics again (the guess that failed at r8, r10 and here), so the rule is opacity: a
        block whose inline source contains a `<` has NO visible text, and nothing in it can
        clear a mention (#14237 r12 review — the r11 wording here still described the
        tag-splitting RAW-SLICE segmentation, a model that commit replaced because it
        re-admitted image alt text, link titles and reference labels).
        """
        text = f"Call `tortoise_list_graphs` for names. {hidden}\n"
        assert self.notes_missing_replacement(text), (
            f"an inline HTML construct satisfied R2 invisibly: {hidden}")

    @pytest.mark.parametrize("hidden", [
        # Measured ACCEPTED under the deleted r11 tag-splitter: a `>` INSIDE the comment ended
        # the span, so the rest of the comment was emitted as a VISIBLE block holding BOTH the
        # mention and the replacement. Now rejected because a block containing `<` is opaque.
        "See below. <!-- a > Call tortoise_list_graphs for names. Use "
        "tortoise_graph_overview. -->",
        # Same fail-open through a quoted ATTRIBUTE value rather than a comment.
        '<a title="x > Call tortoise_list_graphs for names. Use '
        'tortoise_graph_overview.">link</a>',
        # Control: the `>` is in the tag and the instruction is genuinely visible text that
        # names no successor. It reds on BOTH the old and the new code — it pins that the
        # opacity rule did not become an over-accept in the other direction.
        '<a title="x > y">Call tortoise_list_graphs for names.</a>',
    ], ids=["comment-with-gt", "attribute-with-gt", "attribute-with-gt-then-mention"])
    def test_an_inline_html_span_cannot_hide_a_replacement_behind_a_gt(self, hidden):
        """A `>` inside an inline HTML span cannot buy a hidden replacement credit.

        ⛔ MEASURED (#14237 r10 review P1, re-found at r12): while paragraphs were segmented
        on tags with `<[^>]*>`, that pattern ended the span at the FIRST `>` — so a `>` inside
        a quoted attribute value or inside a comment left the REMAINDER as its own visible
        block, and the first two shapes below were ACCEPTED: a bare instruction whose only
        "replacement" is text a browser never displays. That was the same fail-OPEN the
        block-level opacity closed, re-entered through the paragraph path.

        The tag splitter is GONE; a block whose inline source contains `<` is opaque, so all
        three shapes red for the same reason and the paragraph path cannot drift from the
        block and cell paths again. This row still earns its place: the HTML-BLOCK path is
        covered by `test_an_HTML_block_is_opaque` and the cell path by
        `test_a_TABLE_CELL_is_opaque_too`, while these are PARAGRAPHS carrying a `>` — the
        exact shape that survived two earlier fixes.
        """
        assert self.notes_missing_replacement(hidden + "\n"), (
            f"an inline HTML span held a hidden replacement behind a `>`: {hidden}")

    def test_a_fences_INFO_STRING_is_not_a_replacement(self):
        """#14237 r11 review P2 — a fence body is the CODE, not its language tag.

        ⛔ MEASURED: a fence body used to be the raw source slice, so
        ```tortoise_graph_overview satisfied R2 for a dead-end mention inside the fence —
        an info string becomes a `class` attribute and is never displayed. The body is the
        fence's CONTENT, which is what a reader sees.
        """
        text = '```tortoise_graph_overview\nCall tortoise_list_graphs for names\n```\n'
        assert self.notes_missing_replacement(text), (
            "a fence's language tag was counted as a shown replacement")

    @pytest.mark.parametrize("cell", [
        '<script>var d = "tortoise_graph_overview";</script>',
        '<style>/* tortoise_graph_overview */</style>',
        '<span style="display:none">tortoise_graph_overview</span>',
        '<span title="tortoise_graph_overview">i</span>',
    ], ids=["script", "style", "display-none", "attribute"])
    def test_a_TABLE_CELL_is_opaque_too(self, cell):
        """#14237 r12 review P1 — the cell path missed the rule the paragraph path had.

        ⛔ MEASURED: the same three strings the inline-HTML row pins as invisible in a
        PARAGRAPH were ACCEPTED in a table cell, because the cell branch read the token's text
        children before the HTML question was asked — so a script body, a style body, a
        `display:none` span or a tag ATTRIBUTE in a cell cleared a dead-end mention. There is
        now ONE rule ("a `<` in the block's inline source makes it opaque"), applied where both
        paths read it, so the cell cannot drift from the paragraph again.
        """
        text = f"| a | Call tortoise_list_graphs for names {cell} |\n| --- | --- |\n"
        assert self.notes_missing_replacement(text), (
            f"a table cell's invisible {cell!r} satisfied R2")

    @pytest.mark.parametrize("carries", [
        "<br>",                                           # an inline HTML tag
        "See <https://app.premiselabs.co> for details.",   # an AUTOLINK — markdown, not a tag
        "Mail <ops@premiselabs.co> instead.",              # the email autolink spelling
        "The id is in a code span like `<graph.id>`.",     # a code span carrying `<`
        r"An escaped \< is plain text.",                    # an ESCAPED `<` — not markup at all
    ], ids=["inline-tag", "autolink-url", "autolink-email", "code-span-with-lt", "escaped-lt"])
    def test_the_declared_over_block_is_the_price_of_the_opacity_rule(self, carries):
        """The DECLARED price of the r12 rule, pinned as a verdict rather than as prose.

        ⛔ A bound that is only DESCRIBED is indistinguishable from an accident, and this PR
        has already had one declared residual worded away (r5 review P2). So the price is
        measured here, once per SPELLING of it: a NOTE that would otherwise clear the mention
        loses its credit purely because its block contains a `<`. That is the FAIL-CLOSED
        direction — every note below is correct prose and it reds anyway — and it is the
        deliberate trade for closing an invisible replacement in every path at once.

        ⛔ WHY FIVE SPELLINGS AND NOT ONE (#14237 r12 review P2). The rule's trigger is the
        block's SOURCE, not what a browser renders, so the price is wider than "carries an
        inline tag": an AUTOLINK (`<https://…>`, `<ops@…>`) is markdown syntax rather than
        HTML, a code span carrying `<` renders as visible text, and a BACKSLASH-escaped `<` is
        not markup at all — yet EACH of them costs the note its credit. MEASURED at the r12
        head: the comment named only the tag and the code span, so the class comment described
        a narrower bound than the rule enforced, which is the same false-declaration class
        the r12 review round was convened on. Each spelling is now pinned, so narrowing the rule
        to the render — if a reviewer ever asks for it — has to change these rows on purpose.

        ⚠️ NON-VACUITY, stated honestly: this fix corrects the DECLARATION and completes the
        pin, it does not change the rule, so these rows pass on the pre-fix tree BY
        CONSTRUCTION. Their discriminating power is proven by mutation instead — removing the
        opacity rule (`shown = cls._inline_text(token)`) reds all five, which is why they are
        a pin on behaviour rather than a re-statement of it.
        """
        note = "The door `tortoise_list_graphs` retired into `tortoise_graph_overview`."
        assert self.notes_missing_replacement(note + "\n") == [], (
            "CONTROL: the same note with no markup must stay accepted, or this row measures "
            "nothing but the block model")
        over_blocked = self.notes_missing_replacement(f"{note} {carries}\n")
        assert over_blocked, (
            f"the declared over-block is not real for {carries!r}: this spelling no longer "
            "costs a NOTE its replacement credit, so the rule quietly became something else "
            "than the bound the class comment states")
        assert "NO BLOCK" not in over_blocked[0], over_blocked

    @pytest.mark.parametrize("hidden", [
        "![tortoise_graph_overview](x.png) <br>",
        "[x][tortoise_graph_overview] <br>\n\n[tortoise_graph_overview]: https://x.invalid",
        '[x](https://a.invalid/(y) "tortoise_graph_overview") <b>x</b>',
        "`<graph.id>` here. ![tortoise_graph_overview](x.png)",
    ], ids=["image-alt", "ref-label", "link-title", "fake-tag-in-code-span"])
    def test_a_markup_hidden_replacement_does_not_count(self, hidden):
        """#14237 r12 review P1 — a raw-slice segmentation re-admitted hidden text.

        ⛔ MEASURED at the r11 head: once a paragraph contained ANY `<` it was split into RAW
        source slices with only `](...)` stripped, so an image ALT, a link TITLE and a reference
        LABEL — none displayed — satisfied R2, and even a `<graph.id>` inside a code span
        flipped the paragraph onto that path. One rule replaced it: a `<` makes the block
        OPAQUE, so nothing in it can clear a mention.
        """
        text = f"Call tortoise_list_graphs for names. {hidden}\n"
        assert self.notes_missing_replacement(text), (
            f"a hidden replacement satisfied R2: {hidden!r}")

    def test_a_fenced_mention_cannot_borrow_the_note_above_it(self):
        """The r5 shape: a fence is its OWN block, and that is pinned STRUCTURALLY.

        ⛔ The verdict half of this row cannot see the fence branch (#14237 r10 review P2,
        measured: deleting `fence` from the branch leaves the whole class GREEN — the mention
        then lands in NO BLOCK and is reported for that reason instead). So the row asserts the
        BODY of the fence's own block, which only exists if the branch ran: the parser hands the
        fence its content as one unit, and an indented code block gets the same treatment.
        """
        text = (
            "(the door `tortoise_list_graphs` retired forward into "
            "`tortoise_graph_overview(section=\"graphs\")`.)\n"
            "```python\n"
            "graph = tortoise_list_graphs(graph_id='x')\n"
            "```\n"
        )
        bodies = [body for _start, _end, body in self.blocks(text)]
        assert "graph = tortoise_list_graphs(graph_id='x')\n" in bodies, (
            f"the fence is not its own block, so a code sample can borrow a note above it: "
            f"{bodies}")
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
            "a bare retired name inside its own fence borrowed the note's replacement")
        indented = (
            "the MCP tool `tortoise_graph_overview(section=\"graphs\")` lists names\n"
            "\n"
            "    tortoise_list_graphs\n"
        )
        bodies = [body for _start, _end, body in self.blocks(indented)]
        assert "    tortoise_list_graphs\n" in bodies, (
            f"an indented code block is not its own block: {bodies}")
        assert self.notes_missing_replacement(indented), (
            "an indented code sample borrowed the note's replacement")

    @pytest.mark.parametrize("spelling", [
        "Call &#116;ortoise_list_graphs(x=1) for names.",
        "Call tortoise&#95;list&#95;graphs(x=1) for names.",
    ], ids=["whole-name-entity", "underscore-entities"])
    def test_an_ENTITY_spelled_call_is_still_a_call(self, spelling):
        """MEASURED fail-open of the same class as r10/r11/r12 — the name is DECODED.

        An entity-spelled name renders as the name: a reader sees the retired door and can
        copy the call. Both rules read the RAW source, where the token is `#116;ortoise_…`,
        so before this row the guard returned a clean verdict on:

            Call &#116;ortoise_list_graphs(x=1) for names.
            -> R1 []  R2 []      ACCEPTED (a browser shows the retired door)

        This is the mirror of the r10-r12 findings: those hid a REPLACEMENT the reader never
        sees, this hid the MENTION the reader does see. Both are a false PASS of the guard's
        own promise, so both are closed — via the block's decoded body, which `blocks()`
        already computes for R2.

        ⛔ NOT a code span, and that is measured rather than assumed: `blocks()` reports
        ``Call `&#116;ortoise_list_graphs`(x=1).`` with its body still holding the ENTITY,
        because markdown-it gives `code_inline` the literal source and the renderer escapes
        it — a reader sees the entity text there, so there is nothing to decode and no row.
        """
        assert self.retired_call_forms(spelling + "\n"), (
            f"an entity-spelled call was accepted as a note: {self.blocks(spelling + chr(10))}")

    @pytest.mark.parametrize("spelling", [
        "The MCP tool &#116;ortoise_list_graphs lists graph names.",
        "The MCP tool tortoise&#95;list_graphs lists graph names.",
    ], ids=["bare-entity-mention", "bare-underscore-entity"])
    def test_an_ENTITY_spelled_dead_end_is_reported(self, spelling):
        """The R2 half: a bare entity-spelled mention with NO successor is a dead end.

        Measured before this row: ``R1 []  R2 []`` — ACCEPTED, although the paragraph the
        reader sees names only the retired door. The decoded body is what R2 already matches
        replacements against, so the same decode answers whether the mention is there.
        """
        assert self.notes_missing_replacement(spelling + "\n"), (
            f"an entity-spelled dead end was accepted: {self.blocks(spelling + chr(10))}")

    def test_an_ENTITY_spelling_is_judged_ONCE_and_a_named_successor_still_clears_it(self):
        """The two controls: no double-report, and no false red on a correct note.

        `_entity_only_mentions` returns a name only when it is ABSENT from the block's own
        raw source, so a plainly written mention is still the raw scan's to judge — otherwise
        every mention in a block containing an unrelated `&` (an `&` in prose, an `&amp;`)
        would be reported twice. And an entity-spelled mention whose block DOES name the live
        door is a correct note, so it must stay accepted: the fix may not become an
        over-block that reds the repair it asks for.
        """
        plain = "The door `tortoise_list_graphs` retired; use `tortoise_graph_overview`.\n"
        assert self.retired_call_forms(plain) == [], self.retired_call_forms(plain)
        assert self.notes_missing_replacement(plain) == [], self.notes_missing_replacement(plain)
        amp = "Tom &amp; Jerry: the door `tortoise_list_graphs` retired; use `tortoise_graph_overview`.\n"
        assert self.retired_call_forms(amp) == [], self.retired_call_forms(amp)
        assert self.notes_missing_replacement(amp) == [], self.notes_missing_replacement(amp)
        repaired = "The door &#116;ortoise_list_graphs retired; use `tortoise_graph_overview`.\n"
        assert self.retired_call_forms(repaired) == [], self.retired_call_forms(repaired)
        assert self.notes_missing_replacement(repaired) == [], self.notes_missing_replacement(repaired)
        calls = self.retired_call_forms("Call &#116;ortoise_list_graphs(x=1).\n")
        assert len(calls) == 1, f"an entity-spelled call is reported once, not twice: {calls}"
        mixed = (
            "The door `tortoise_list_graphs` retired; use `tortoise_graph_overview`."
            " Also call &#116;ortoise_list_graphs(x=1).\n"
        )
        assert self.retired_call_forms(mixed), (
            "a PLAIN mention in the same block hid an entity-spelled call — the first cut"
            f" of this fix gated on `name in raw` and measured exactly this miss: {mixed!r}")

    @pytest.mark.parametrize("text", [
        "Call &#116;ortoise_list_graphs(x=1) for names. <br>",
        "Call &#116;ortoise_list_graphs(x=1) for names. See <https://x.invalid>.",
        "<div>\nCall &#116;ortoise_list_graphs(x=1) for names.\n</div>",
        "| a | Call &#116;ortoise_list_graphs(x=1) <br> |",
    ], ids=["paragraph-with-br", "paragraph-with-autolink", "html-block", "table-cell"])
    def test_an_ENTITY_call_in_an_OPAQUE_block_is_still_a_call(self, text):
        """R1 at the INTERSECTION of the r10 and r12 fixes — the gap both left open.

        ⛔ MEASURED at the reviewed head (#14237 r13 review P2), with the class's own
        methods: every row below returned `R1 [] R2 []`, i.e. ACCEPTED, while the class
        comment promised "every mention in such a block reds". The two halves had each
        been fixed in isolation — an entity spelling in a NON-opaque block reds (r12), a
        PLAIN spelling in an opaque block reds (r10) — and their intersection red neither,
        because `_entity_only_mentions` bailed on `not body` and `body` is `""` for every
        opaque block by construction.

        The shapes are ordinary prose: one autolink or one `<br>` in the same ~80-column
        paragraph as an entity-spelled mention is the whole pre-condition, and the entity
        spelling is in scope because the reader SEES the door and can copy the call.
        """
        assert self.retired_call_forms(text + "\n"), (
            "an entity-spelled call inside an OPAQUE block was accepted — the block's "
            f"visible body is empty, so only its decoded source can judge it: {text!r}")

    @pytest.mark.parametrize("text", [
        "The MCP tool &#116;ortoise_list_graphs lists names. <br>",
        "The MCP tool &#116;ortoise_list_graphs lists names. See <https://x.invalid>.",
        "<div>\nThe MCP tool &#116;ortoise_list_graphs lists names.\n</div>",
        "| a | The MCP tool &#116;ortoise_list_graphs lists names. <br> |",
    ], ids=["paragraph-with-br", "paragraph-with-autolink", "html-block", "table-cell"])
    def test_an_ENTITY_dead_end_in_an_OPAQUE_block_is_reported(self, text):
        """R2 half of the same intersection: the dead end nobody could see.

        A dead end is worse than a call — a reader who copies it gets nothing — and it was
        ACCEPTED in every opaque shape. R2 reports it because an opaque block's `body` is
        deliberately `""`, so no replacement can clear it; that is the fail-closed direction
        the opacity rule declares, applied to the mention instead of to the replacement.
        """
        assert self.notes_missing_replacement(text + "\n"), (
            "an entity-spelled dead end inside an OPAQUE block was accepted: "
            f"{text!r}")

    @pytest.mark.parametrize("plain,entity", [
        ('[docs](https://x.invalid "tortoise_list_graphs")',
         '[docs](https://x.invalid "&#116;ortoise_list_graphs")'),
        ('[docs](tortoise_list_graphs)', '[docs](&#116;ortoise_list_graphs)'),
        ('![tortoise_list_graphs](x.png)', '![&#116;ortoise_list_graphs](x.png)'),
        # ⛔ THE SHAPES WITH NO LEAF BLOCK AT ALL (#14237 r15 review, measured fail-open).
        # A link-reference DEFINITION is consumed by the parser, so it is in NO block: the raw
        # scan reports its PLAIN mention (`NO BLOCK` for a dead end, an R1 call when it carries
        # arguments) and the entity reader never reached it, because it walked `blocks()`.
        ('[foo]: tortoise_list_graphs\n\nSee [foo].',
         '[foo]: &#116;ortoise_list_graphs\n\nSee [foo].'),
        ('[tortoise_list_graphs]: https://x.invalid\n\nSee [tortoise_list_graphs].',
         '[&#116;ortoise_list_graphs]: https://x.invalid\n\nSee [&#116;ortoise_list_graphs].'),
        ('[foo]: tortoise_list_graphs(x=1)\n\nSee [foo].',
         '[foo]: &#116;ortoise_list_graphs(x=1)\n\nSee [foo].'),
    ], ids=["link-title", "link-destination", "image-alt",
            "ref-def-destination", "ref-def-label", "ref-def-call"])
    def test_the_verdict_does_not_depend_on_the_SPELLING(self, plain, entity):
        """⛔ MEASURED FAIL-OPEN (#14237 r14 and r15): the same mention, two spellings, two verdicts.

        `name_hits` scans the WHOLE source, so a PLAIN retired name in a link's title, a link's
        destination, an image's alt text — or a link-reference DEFINITION, which is in no leaf
        block at all — is reported. The entity reader judged `body`, and `body` is exactly what
        `blocks()` strips those containers out of, so the ENTITY spelling of the very same
        mention was invisible to all four readers and ACCEPTED. A guard whose verdict is a
        function of the spelling is the defect this reader exists to close, so both spellings
        must agree — here, on REPORT, the fail-closed direction the class comment declares.

        ⛔ EXACT EQUALITY, not "both truthy": the reports' text is what a reader is handed, so
        a disagreement in the PLACEMENT (`-> NO BLOCK` vs an empty body) is the same defect one
        layer in. That is also what keeps the r15 rows honest: the ref-def rows cannot pass by
        the definition happening to sit inside some block.
        """
        rules = (self.retired_call_forms, self.notes_missing_replacement)
        plain_hits = {rule.__name__: rule(plain + "\n") for rule in rules}
        entity_hits = {rule.__name__: rule(entity + "\n") for rule in rules}
        assert any(plain_hits.values()), (
            "NEITHER rule reports the PLAIN spelling, so this row would prove nothing: "
            f"{plain!r}")
        assert entity_hits == plain_hits, (
            f"the verdict depends on the SPELLING of the same mention: {entity!r} gave "
            f"{entity_hits}, {plain!r} gave {plain_hits}")

    def test_a_fenced_entity_is_reported_like_the_PLAIN_spelling(self):
        """The r14 comment's "a fenced entity is ACCEPTED, and correctly so" is SUPERSEDED here.

        ⛔ WHY THIS ROW EXISTS AND WHY IT IS NOT IN THE PARITY TABLE. The r14 fix decodes every
        unit's SOURCE, so an entity-spelled call inside a FENCE now reds — the earlier comment
        recorded that shape as accepted, on the reasoning that a fence's `&` is escaped and a
        code block is not an instruction. That reasoning does not survive the guard's own
        pinned behaviour: a PLAIN call inside a fence already reds R1, and
        `test_a_fenced_mention_cannot_borrow_the_note_above_it` PINS that ("a fenced call under
        a migration note was accepted — a fence is its own block"). So accepting only the
        ENTITY spelling would be the spelling-dependence this reader exists to close; the r14
        change is the consistency fix, and this row records the reversal so the next round does
        not file it as drift.

        The two reports cannot be compared line for line, and that is declared: the entity
        report names the UNIT's start line (a decoded character has no source offset — see
        `_entity_only_mentions`), the raw scan names the mention's own line.

        ⚠️ NON-VACUITY, stated honestly: this shape already reds at `722ed8f0a` (the r14 fix
        landed there), so the row is a PIN on that behaviour rather than a new fix. Its
        discriminating power is measured by mutation instead — reverting `shown` to the visible
        `body` reds it (19 failed / 190 passed over this class, the mutant applied and reverted
        with the tree sha-verified).
        """
        plain = "```\ntortoise_list_graphs(x=1)\n```\n"
        entity = "```\n&#116;ortoise_list_graphs(x=1)\n```\n"
        assert self.retired_call_forms(plain) == ["tortoise_list_graphs:2"], (
            f"the PLAIN fenced call is no longer reported, so this row measures nothing: "
            f"{self.retired_call_forms(plain)}")
        assert self.retired_call_forms(entity) == ["tortoise_list_graphs:1"], (
            "an entity-spelled fenced call was accepted — the one spelling a fence would "
            f"excuse: {self.retired_call_forms(entity)}")
        assert not self.notes_missing_replacement(entity), (
            "a call form was graded a dead end as well: "
            f"{self.notes_missing_replacement(entity)}")

    def test_a_reference_definition_is_judged_OUTSIDE_the_block_model(self):
        """The gap unit is STRUCTURAL, and this row pins it as structure (#14237 r15, measured).

        ⛔ MEASURED FAIL-OPEN at `722ed8f0a`: `[foo]: &#116;ortoise_list_graphs(x=1)` was
        ACCEPTED while `[foo]: tortoise_list_graphs(x=1)` red R1 on line 1 — the same
        definition, the same reader-visible door, and a verdict that flipped on the spelling,
        because `_entity_only_mentions` iterated `blocks()` and a link-reference definition
        produces NO leaf. The parser consumes it, which is exactly why R2 already reports a
        plain mention there as `NO BLOCK` (r9 review P1).

        ⛔ WHY THE STRUCTURE IS ASSERTED AS WELL AS THE VERDICT (#14237 r10 review P2's rule):
        the verdict half alone can be satisfied from another direction — a definition that
        also sits in a paragraph would be reported by the ordinary block path — so this row
        asserts the property the gap unit exists for: `blocks()` does NOT cover the definition
        and `_unblocked_spans()` DOES. Deleting the gap unit reds the parametrized ref-def
        rows above; moving the definition into a block reds this one.
        """
        text = "[foo]: &#116;ortoise_list_graphs(x=1)\n\nSee [foo].\n"
        definition_at = text.index("[foo]:")
        assert not any(b0 <= definition_at < b1 for b0, b1, _body in self.blocks(text)), (
            f"the definition is inside a leaf block, so this row measures the block path: "
            f"{self.blocks(text)}")
        gaps = self._unblocked_spans(text)
        assert any(b0 <= definition_at < b1 for b0, b1 in gaps), (
            f"the source outside every block is not a judged unit: {gaps}")
        assert self.retired_call_forms(text) == [
            "tortoise_list_graphs:1"], (
            "an entity-spelled CALL in a link-reference definition was accepted: "
            f"{self.retired_call_forms(text)}")
        assert not self.notes_missing_replacement(text), (
            "a call form was graded a dead end as well: "
            f"{self.notes_missing_replacement(text)}")

    @pytest.mark.parametrize("name,bracket_open,bracket_close", [
        # The NAME is plain and only the BRACKET is spelled unusually: the excess count above
        # sees nothing (both counts are 1), so this is the arm that needs the RENDERED text.
        ("tortoise_list_graphs", "&#40;", "&#41;"),
        ("tortoise_list_graphs", r"\(", r"\)"),
        # …and both mechanisms at once, plus markdown's other name spelling.
        ("&#116;ortoise_list_graphs", r"\(", r"\)"),
        (r"tortoise\_list\_graphs", "(", ")"),
    ], ids=["entity-bracket", "escaped-bracket",
            "entity-name-and-bracket", "escaped-name"])
    def test_a_CALL_spelled_with_an_escape_or_a_reference_is_still_a_call(
            self, name, bracket_open, bracket_close):
        """⛔ MEASURED FAIL-OPEN (#14237 r16): R1 read call form in the RAW source only.

        The reader sees `tortoise_list_graphs(x=1)` in every row below — markdown's inline
        parser resolves `&#40;` and `\\(` to `(` — so R1's rule ("a retired name written as a
        CALL may not appear at all; no prose talks it back in") applies. It did not fire: at
        the r15 head `Call tortoise_list_graphs\\(x=1\\) for names. Use
        tortoise_graph_overview(section="graphs") instead.` returned `R1 [] R2 []` and was
        ACCEPTED, while the same sentence with plain brackets reds R1. The block NAMES the
        replacement in every row on purpose: that is the exact condition under which the
        escape spelling was accepted (R2 cleared it as a note).

        ⛔ THE NAME HALF IS NOT A CASUAL EXTRA. `tortoise\\_list\\_graphs` is how a doc author
        writes that name when they want literal underscores, and it RENDERS as the door, so
        the name spelling and the bracket spelling are two halves of one property.
        """
        replacement = 'Use tortoise_graph_overview(section="graphs") instead.'
        text = f"Call {name}{bracket_open}x=1{bracket_close} for names. {replacement}\n"
        assert self.retired_call_forms(text) == ["tortoise_list_graphs:1"], (
            f"a copyable call to the retired door was accepted: {text!r} -> "
            f"{self.retired_call_forms(text)}")
        assert self.notes_missing_replacement(text) == [], (
            "a call form was graded a dead end as well: "
            f"{self.notes_missing_replacement(text)}")

    @pytest.mark.parametrize("plain,variant", [
        ("The tool tortoise_list_graphs lists names.",
         "The tool tortoise\\_list\\_graphs lists names."),
        ("Call tortoise_list_graphs(x=1) for names.",
         "Call tortoise\\_list\\_graphs(x=1) for names."),
    ], ids=["dead-end", "call"])
    def test_the_ESCAPE_spelling_is_judged_like_the_plain_one(self, plain, variant):
        """⛔ MEASURED FAIL-OPEN (#14237 r16): a backslash-escaped NAME was invisible.

        `name_hits` scans the raw source, where `tortoise\\_list\\_graphs` is not a whole token
        of the retired name at all, and the entity reader decoded references only — so BOTH
        rules returned empty: a dead end in that spelling was ACCEPTED where the plain one is
        reported, and a call in that spelling was ACCEPTED where the plain one reds R1. The
        escape is resolved by the parser, so the rendered text is read instead.

        ⛔ EXACT EQUALITY of the reports, not "both non-empty": the reports' text is what a
        reader is handed, so a disagreement in placement is the same defect one layer in (the
        r14/r15 rule, applied to the second spelling mechanism).
        """
        rules = (self.retired_call_forms, self.notes_missing_replacement)
        plain_hits = {rule.__name__: rule(plain + "\n") for rule in rules}
        variant_hits = {rule.__name__: rule(variant + "\n") for rule in rules}
        assert any(plain_hits.values()), (
            "NEITHER rule reports the PLAIN spelling, so this row would prove nothing: "
            f"{plain!r}")
        assert variant_hits == plain_hits, (
            f"the verdict depends on the SPELLING of the same mention: {variant!r} gave "
            f"{variant_hits}, {plain!r} gave {plain_hits}")

    def test_a_wrapped_bracket_is_still_not_a_call(self):
        """The declared bound, pinned where the r16 fix could have broken it.

        A retired name followed by a NEWLINE then `(` is NOT a call, and the guard accepts it
        when its block names the replacement. Reading the RENDERED text would have silently
        reversed that, because `_inline_text`'s DEFAULT renders a softbreak as a SPACE — the
        wrapped bracket would have become a call and this correct paragraph would have gone
        red. That is why `_inline_text` takes `breaks` and R1 passes `"\\n"`. Reverting it to
        the default reds exactly this row.

        ⛔ THE STRAY `&` WAS THE POINT UNTIL ROUND 17, AND IT IS NOW GONE (#14237 r17,
        measured). `_entity_only_mentions` used to walk only the units whose source carried
        `&` or `\\`, so without an `&` this paragraph never reached the rendered arm and the
        row passed for the wrong reason — measured then: with the `&` removed, the
        `breaks=" "` mutant survived the whole class. The r17 fix runs the rendered arm for
        EVERY unit (the gate was the bug: markup inside a name splits it into raw tokens that
        are not retired names at all), so the arm is reached without any `&`. RE-MEASURED at
        this head with the `&` removed AND `breaks=" "` restored: this row and
        `test_a_newline_is_never_a_call_form` both red. Keeping the `&` would have left this
        docstring asserting a condition that no longer decides anything.
        """
        text = ("Call tortoise_list_graphs\n(x=1) for names. "
                'Use tortoise_graph_overview(section="graphs") instead.\n')
        assert self.retired_call_forms(text) == [], (
            "a HARD-WRAPPED bracket was read as a call: "
            f"{self.retired_call_forms(text)}")
        assert self.notes_missing_replacement(text) == [], (
            f"a note naming its replacement was reported: {self.notes_missing_replacement(text)}")

    def test_a_code_span_keeps_its_backslash_so_it_is_not_a_call(self):
        """Why the escape is read from the PARSER and not stripped from the source.

        Inside a code span markdown does NOT resolve escapes, so a reader sees
        `tortoise\\_list\\_graphs\\(x=1\\)` WITH its backslashes: it is not a copyable call, and
        the guard accepts it when the block names the replacement. A source-level escape strip
        (the cheap way to close r16) would red this correct paragraph — which is why the
        rendered text comes from the parser's own children and `_escape_resolution` is used
        only for the gap units, which have no parser output.
        """
        text = (r"Use `tortoise_list_graphs\(x=1\)` verbatim: "
                'tortoise_graph_overview(section="graphs").\n')
        assert self.retired_call_forms(text) == [], (
            "a backslash inside a CODE SPAN was resolved, inventing a call: "
            f"{self.retired_call_forms(text)}")
        assert self.notes_missing_replacement(text) == [], (
            f"a note naming its replacement was reported: {self.notes_missing_replacement(text)}")

    def test_a_semicolon_less_reference_is_LITERAL_in_markdown_text(self):
        """The other direction: the permissive decode INVENTED a mention in markdown text.

        CommonMark requires the terminating `;`, and the renderer this repo pins agrees —
        markdown-it emits `Call &amp;#116ortoise_list_graphs(x=1)`, i.e. the reader sees the
        literal `&#116ortoise_list_graphs`, NOT the door. So `&#116ortoise…` is not a mention.
        ⛔ MEASURED (#14237 r14): the opaque branch decoded with `html.unescape` (HTML5, which
        consumes a semicolon-less numeric reference), so the SAME literal sentence red as soon
        as an unrelated `<br>` made its block opaque — one reader-visible text, two answers, on
        a difference the reader cannot see.
        """
        literal = "Call &#116ortoise_list_graphs(x=1) for names."
        assert self.retired_call_forms(literal + "\n") == [], (
            f"a literal `&#116ortoise…` was decoded into a mention: {literal!r}")
        assert self.retired_call_forms(literal + " <br>\n") == [], (
            "an unrelated `<br>` turned a literal `&#116ortoise…` into a mention — HTML5's "
            "semicolon-optional decode applied to MARKDOWN text")
        # …and the TERMINATED spelling in the identical position still reds, so the row above
        # is not a guard that stopped looking.
        assert self.retired_call_forms(
            "Call &#116;ortoise_list_graphs(x=1) for names. <br>\n")

    def test_raw_HTML_is_decoded_the_way_a_browser_decodes_it(self):
        """An `html_block` is passed through as RAW HTML, so HTML5's rule is the reader's rule.

        `_decoded_source` is one question with two answers because `blocks()` already splits the
        two languages — and this row pins the half that must NOT become strict: a browser
        consumes a semicolon-less numeric reference inside raw HTML, so the name IS shown there
        and reds. Pinning it matters both ways: a later "simplify to one decode" would either
        re-introduce the r14 false red above (permissive everywhere) or open this shape
        (strict everywhere).
        """
        assert self.retired_call_forms(
            '<div>Call &#116ortoise_list_graphs(x=1) for names.</div>\n'), (
            "a semicolon-less reference inside RAW HTML is consumed by a browser, so the name "
            "is displayed and must be reported")
        assert self.retired_call_forms(
            '<div>Call &#116;ortoise_list_graphs(x=1) for names.</div>\n')

    def test_closing_the_opaque_entity_gap_does_not_widen_the_entity_rule(self):
        """The controls: the fix reports the intersection and NOTHING ELSE.

        Discovery reads the block's decoded SOURCE in every block (#14237 r14), which is what
        makes the verdict spelling-independent — but REPLACEMENT credit still reads `body`, so
        the r11/r12 exclusions hold where they were aimed: a replacement hidden in image alt
        text, a link destination or title, a reference label or `html_inline` still cannot
        clear a visible mention, and an unrelated `&amp;` in the same block still cannot
        manufacture one.

        ⚠️ The last row is the DECLARED price, stated here as a bound rather than tuned
        away: in an opaque block the replacement credit is unreachable, so a note that DOES
        name the successor is still reported (`R1`) — the same verdict a plainly-spelled
        note in an opaque block already gets, and the trade the opacity rule has declared
        since round 1. It is pinned so the next reader learns it from a test.
        """
        repaired = "The door &#116;ortoise_list_graphs retired; use `tortoise_graph_overview`.\n"
        assert self.retired_call_forms(repaired) == [], self.retired_call_forms(repaired)
        assert self.notes_missing_replacement(repaired) == [], (
            self.notes_missing_replacement(repaired))
        amp = (
            "Tom &amp; Jerry: the door `tortoise_list_graphs` retired; "
            "use `tortoise_graph_overview`.\n"
        )
        assert self.retired_call_forms(amp) == [], self.retired_call_forms(amp)
        assert self.notes_missing_replacement(amp) == [], self.notes_missing_replacement(amp)
        # A mention a reader never sees is still a mention: an entity spelling inside a
        # `<script>` body is reported, which is the fail-CLOSED direction (r11's rule).
        hidden = '<div><script>var d = "&#116;ortoise_list_graphs(x=1)";</script></div>\n'
        assert self.retired_call_forms(hidden), (
            "an entity-spelled call in a script body was accepted, so the opaque branch "
            f"narrowed the r11 rule instead of widening the r12 one: {hidden!r}")
        # DECLARED PRICE: opaque block + a named successor is still reported.
        priced = "Call &#116;ortoise_list_graphs(x=1). Use `tortoise_graph_overview`. <br>\n"
        assert self.retired_call_forms(priced), (
            "an opaque block now lets a named successor clear a written call — that would "
            "be the r11 fail-open returning through the entity path")

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

    @pytest.mark.parametrize("split", [
        "tortoise_list_graph*s*",
        "tortoise_list_grap**hs**",
        "tortoise_list_graph``s``",
    ], ids=["emphasis-inside", "strong-inside", "code-span-split"])
    @pytest.mark.parametrize("arm", ["call", "dead-end"])
    def test_a_name_SPLIT_by_inline_markup_is_still_the_name(self, split, arm):
        """⛔ MEASURED FAIL-OPEN (#14237 r17): markup INSIDE the name defeated the guard.

        The discovery arm skipped every unit whose raw source carried neither `&` nor `\\`,
        on the stated premise that those are "the only ways the source can differ from the
        text the reader sees while a retired name is on screen". Rendering also REMOVES
        markup, so markup inside the name splits it into raw tokens that are not retired
        names at all — and the unit was then skipped before the rendered arm could read it.
        Measured at the r16 head: `Call tortoise_list_graph*s*(x=1) for names. Use
        tortoise_graph_overview(...)` and the `` ``s`` `` spelling both returned `R1 [] R2 []`
        while the reader sees a copyable call, and the same sentence with the plain name reds
        R1. The dead-end spelling (`The tool tortoise_list_grap*hs* lists names.`) was
        ACCEPTED too, where the plain one is an R2 dead end.

        ⛔ WHY THE RENDERED ARM AND NOT A DELIMITER STRIP: a strip is a hand-written guess at
        what markup removes, and the parser already knows. `tortoise*_*list_graphs` renders
        with its delimiters intact (they are literal there), so it is NOT the name — pinned
        by `test_a_delimiter_that_does_NOT_render_as_the_name_is_accepted`, which a strip
        would red.
        """
        replacement = 'Use tortoise_graph_overview(section="graphs") instead.'
        # TWO ROWS PER SPELLING, one per rule (#14237 r17): the verdict that changes is R1's
        # for a call and R2's for a dead end, and a single row asserting both would abort on
        # the first and leave the second with no mutant that can reach it.
        if arm == "call":
            call = f"Call {split}(x=1) for names. {replacement}\n"
            assert self.retired_call_forms(call) == ["tortoise_list_graphs:1"], (
                f"a copyable call whose NAME is split by markup was accepted: {call!r} -> "
                f"{self.retired_call_forms(call)}")
            assert self.notes_missing_replacement(call) == [], (
                "a call form was graded a dead end as well: "
                f"{self.notes_missing_replacement(call)}")
            return
        dead_end = f"The tool {split} lists names.\n"
        assert self.retired_call_forms(dead_end) == [], (
            f"the dead-end arm is not a call, so this row measures nothing: {dead_end!r}")
        assert self.notes_missing_replacement(dead_end), (
            f"an INSTRUCTION naming only the retired door was accepted when the name was "
            f"split by markup: {dead_end!r}")

    def test_a_delimiter_that_does_NOT_render_as_the_name_is_accepted(self):
        """The control for the row above: only text the RENDERER produces is the name.

        `tortoise*_*list_graphs` and `tortoise_list_~~g~~raphs` keep their delimiters in the
        rendered text (they are literal there, not markup), so the reader does not see the
        retired door and there is nothing to report. This is what separates reading the
        parser's output from stripping `*`/`_`/`~` out of the source: the strip is the cheap
        approximation, and it would red both of these correct sentences.
        """
        replacement = 'Use tortoise_graph_overview(section="graphs") instead.'
        for literal in ("tortoise*_*list_graphs", "tortoise_list_~~g~~raphs"):
            text = f"Call {literal}(x=1) for names. {replacement}\n"
            assert self.retired_call_forms(text) == [], (
                f"a delimiter the renderer kept LITERAL was read as the retired name in "
                f"{literal!r}: {self.retired_call_forms(text)}")
            assert self.notes_missing_replacement(text) == [], (
                f"{literal!r} -> {self.notes_missing_replacement(text)}")

    @pytest.mark.parametrize("space,spelling", [
        (" ", "a plain ASCII space"),
        ("&nbsp;", "U+00A0"),
        ("&ensp;", "U+2002"),
        ("&thinsp;", "U+2009"),
    ], ids=["ascii-space", "nbsp", "en-space", "thin-space"])
    def test_every_HORIZONTAL_space_before_the_bracket_is_still_a_call(
            self, space, spelling):
        """⛔ MEASURED FAIL-OPEN (#14237 r17): the class was `[ \\t]`, so NBSP was accepted.

        `CALL_FORM` already treats `name (args)` — a name, a SPACE, a bracket — as a call, so
        the shape is in scope by the rule's own wording. The rendered text of
        `Call tortoise_list_graphs&nbsp;(x=1)` carries U+00A0, not a space, and the class did
        not hold it: measured at the r16 head that sentence returned `R1 [] R2 []` while its
        ASCII-space twin reds. The class is now `[^\\S\\n\\r]` — "horizontal whitespace" — so
        the parametrize covers the members a writer can reach through an entity, and the
        ASCII row is the control that keeps the others from passing for the wrong reason.
        """
        replacement = 'Use tortoise_graph_overview(section="graphs") instead.'
        text = f"Call tortoise_list_graphs{space}(x=1) for names. {replacement}\n"
        assert self.retired_call_forms(text) == ["tortoise_list_graphs:1"], (
            f"a call separated by {spelling} was accepted: {text!r} -> "
            f"{self.retired_call_forms(text)}")
        assert self.notes_missing_replacement(text) == [], (
            "a call form was graded a dead end as well: "
            f"{self.notes_missing_replacement(text)}")

    @pytest.mark.parametrize("placement", ("inside-the-name", "before-the-bracket"))
    @pytest.mark.parametrize("codepoint", (
        # the six r18 shipped…
        0x00AD, 0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF,
        # …and one member of EVERY Cf family the hand list left ACCEPTED (#14237 r19,
        # measured): a bidi MARK, a bidi EMBEDDING, a bidi ISOLATE, the Arabic letter MARK,
        # an invisible MATH operator, and the Mongolian vowel separator. One per family, so
        # a future narrowing of the property reds a NAME here rather than only a count.
        0x200E, 0x202A, 0x2066, 0x061C, 0x2062, 0x180E,
        # …and one member of EVERY family OUTSIDE Cf that r19 left ACCEPTED (#14237 r21,
        # measured at the r20 head): variation selector-16 (Mn), the combining grapheme
        # joiner (Mn), the Hangul fillers (Lo, both the choseong and the compatibility one),
        # a Mongolian free variation selector (Mn), and U+17B4 (Mn, a Khmer inherent vowel).
        0xFE0F, 0x034F, 0x115F, 0x3164, 0x180B, 0x17B4,
    ))
    def test_an_INVISIBLE_character_hides_neither_the_name_nor_its_bracket(
            self, codepoint, placement):
        """The invisible-character class, CLOSED in BOTH places it hid the door (#14237 r18).

        ⛔ THIS ROW REPLACED A DECLARED RESIDUAL, at that row's own instruction ("the
        invisible-character residual is CLOSED — delete this row and say so in the class
        comment instead of leaving a stale bound"). It was measured ACCEPTED in TWO
        placements, and each needed a different repair, so pinning one would have been the
        exact defect this class comment keeps naming — a stated bound narrower than the
        behaviour:

        * INSIDE the name (`Call tortoise&#8203;_list_graphs(x=1)`) — `name_hits` tokenises on
          `[A-Za-z0-9_]`, so the split halves are not retired names at all. Closed by
          `_strip_invisible` over the JUDGED texts (discovery), which is what the old
          residual named as the repair.
        * BETWEEN the name and its bracket (`Call tortoise_list_graphs&#8203;(x=1)`) — the
          name IS discovered here (the plain scan sees it); what failed is that the
          character is not horizontal WHITESPACE, so `CALL_FORM`'s separator run stopped at it
          and the occurrence was graded a dead-end note instead of a call. Closed by the SAME
          `_strip_invisible`, which leaves the ordinary `name(` that `CALL_FORM` already
          matches — the separator class is deliberately NOT widened, because that widening was
          measured dead weight (see `CALL_FORM`'s comment).

        A reader sees `tortoise_list_graphs(x=1)` in both cases — the invisible character
        renders as nothing — so both are copyable calls to the retired door, which R1 forbids
        absolutely. `placement` is a SEPARATE parameter rather than a loop inside one row so
        each half reds on its OWN id: a loop would stop at the first failing placement and
        report one green for the other, which is how a half-covered fix reads as covered.
        The plain control below is what keeps a green from meaning the row is measuring
        nothing.
        """
        reference = f"&#{codepoint};"
        replacement = 'Use tortoise_graph_overview(section="graphs") instead.'
        plain = f"Call tortoise_list_graphs(x=1) for names. {replacement}\n"
        hidden = {
            "inside-the-name":
                f"Call tortoise{reference}_list_graphs(x=1) for names. {replacement}\n",
            "before-the-bracket":
                f"Call tortoise_list_graphs{reference}(x=1) for names. {replacement}\n",
        }[placement]
        assert self.retired_call_forms(plain) == ["tortoise_list_graphs:1"], (
            f"the plain control no longer reds, so this row measures nothing: {plain!r}")
        assert self.retired_call_forms(hidden) == ["tortoise_list_graphs:1"], (
            f"U+{codepoint:04X} {placement} hid a copyable call to the retired door — the "
            f"reader sees `tortoise_list_graphs(x=1)` in {hidden!r}, so this is a false PASS "
            f"of R1: {self.retired_call_forms(hidden)}")

    def test_the_invisible_class_is_DERIVED_from_UNICODE_PROPERTIES_not_a_hand_list(self):
        """The strip asks Unicode's properties, never a list and never one property alone.

        ⛔ THREE ROUNDS IN A ROW DECLARED THIS CLASS CLOSED WHILE MEMBERS OF IT WERE OPEN
        (#14237 r19, r21, r22 — the same shape as the r13/r16 findings: a stated blast radius
        wider than the behaviour). r18 repaired the class with SIX enumerated codepoints. r19
        showed 164 of Cf's 170 members were ACCEPTED in both placements and asked
        `unicodedata.category` instead — prose said "closed" again, while a WHOLE FAMILY
        OUTSIDE Cf was open (variation selectors, the combining grapheme joiner, the Hangul
        fillers, Mongolian free variation selectors: Mn/Lo, so `Cf` missed them). r21 asked
        `Default_Ignorable_Code_Point` instead, and called the class closed a THIRD time while
        TWO families were still open, both measured ACCEPTED in both placements: the Cf members
        Unicode excludes from that property for LAYOUT reasons (U+FFF9..FFFB, U+13430..1343F —
        they render nothing) and the control family (U+0007/U+001B/U+009F render nothing).

        So the assertion is about the MECHANISM, not about a longer list: every codepoint in the
        probe must be a member of the DERIVED class, and that membership must be one the
        `Default_Ignorable_Code_Point`-only class WOULD NOT give — the "no single property"
        assertion below — and the strip must remove ALL of them. A future narrowing back to an
        enumeration, to the Cf category, or to the single Default_Ignorable property reds HERE
        (the probe's non-Cf and layout-only members survive the strip) as well as in the
        placement rows, and a widening that starts eating code points a reader SEES reds here
        too (the visible control below) — the direction the placement rows alone cannot catch.
        """
        invisibles = (
                 # one member of every Cf family: soft hyphen, ZWSP/ZWNJ/ZWJ, LRM, RLM,
                 # Arabic letter mark, Mongolian vowel separator, bidi embeddings and
                 # isolates, invisible math operators, word joiner, BOM.
                 "\u00ad\u200b\u200c\u200d\u200e\u200f\u061c\u180e"
                 "\u202a\u202b\u202c\u202d\u202e\u2060\u2061\u2062\u2063\u2064"
                 "\u2066\u2067\u2068\u2069\ufeff"
                 # …and one member of every family OUTSIDE Cf that renders as nothing:
                 # variation selectors (Mn), the combining grapheme joiner (Mn), the Hangul
                 # fillers (Lo), and Mongolian free variation selectors (Mn).
                 "\ufe0f\ufe0e\U000e0100\u034f\u115f\u1160\u3164\u180b\u180d\u17b4"
                 # …and one member of every Cf family Unicode EXCLUDES from
                 # Default_Ignorable for LAYOUT reasons, which r21 therefore left open
                 # (#14237 r22, measured ACCEPTED in both placements at `0c25ea83ba`).
                 "\ufff9\ufffa\ufffb\U00013430\U0001343f"
                 # …and one member of the control family, which NO property above covers
                 # (#14237 r22, measured ACCEPTED at every earlier head).
                 "\x07\x1b\x9f")
        probe = "tortoise_list_graphs" + invisibles
        outside_the_class = [ch for ch in invisibles
                             if not self.INVISIBLE_RE.fullmatch(ch)]
        assert not outside_the_class, (
            "the probe no longer spans the class's families — a green here would measure "
            f"nothing: {[(hex(ord(c)), unicodedata.category(c)) for c in outside_the_class]}")
        # ⛔ THE CLASS IS NOT ONE PROPERTY, and this is the assertion that says so: every one of
        # these families is OUTSIDE the Default_Ignorable property, so a class that asked only
        # that property would already have gone green above while leaving them open.
        one_property_would_miss = [ch for ch in "\ufff9\ufffb\U00013430\U0001343f\x07\x1b\x9f"
                                   if regex.fullmatch(r"\p{Default_Ignorable_Code_Point}", ch)]
        assert not one_property_would_miss, (
            "these probe members ARE Default_Ignorable, so they no longer prove the class is "
            f"wider than that one property: {[hex(ord(c)) for c in one_property_would_miss]}")
        assert self._strip_invisible(probe) == "tortoise_list_graphs", (
            "the strip is not the derived invisible set: every character in the probe renders "
            "as nothing, so a reader reads only the name, while the strip left "
            f"{self._strip_invisible(probe)!r}")
        # The bound in the other direction: characters a reader SEES must survive, or the class
        # has been widened into "strip all Unicode" (an over-block that would red correct prose
        # and, worse, could shorten a token into a name).
        visible = " tortoise\u00a0list\u2003graphs\u2014x\t\n"
        assert self._strip_invisible(visible) == visible, (
            "the strip removed characters a reader SEES, which is the over-block direction: "
            f"{self._strip_invisible(visible)!r}")
        # …and the Prepended_Concatenation_Mark subtraction still holds: those render a mark.
        pcm = "\u0600\u06dd\u070f\u08e2\U000110bd"
        assert all(self.INVISIBLE_RE.fullmatch(ch) is None for ch in pcm), (
            "a Prepended_Concatenation_Mark is being stripped again — that is r19's over-block "
            f"returning: {[(hex(ord(c)), self.INVISIBLE_RE.fullmatch(c)) for c in pcm]}")

    @pytest.mark.parametrize("codepoint", (
        0x0600, 0x0601, 0x06DD, 0x070F, 0x0890, 0x08E2, 0x110BD, 0x110CD))
    def test_a_Cf_member_that_DESIGNS_a_visible_mark_is_NOT_stripped(self, codepoint):
        """THE OVER-BLOCK IS GONE, and it is Unicode that says so: these are
        `Prepended_Concatenation_Mark`, the Cf members Unicode itself keeps OUT of
        Default_Ignorable because they render a sign or mark with adjacent text.

        r19 declared sixteen Cf members a deliberate over-block because stripping them made a
        NOTE written beside one lose its replacement credit. r21 removed the price by asking
        `Default_Ignorable_Code_Point` — but its prose then claimed ALL 32 Cf-not-Default_Ignorable
        members render a mark, and half of that claim was false (U+FFF9..FFFB and U+13430..1343F
        render nothing; #14237 r22). This row is now scoped to the members that DO render, and
        the reason is the PROPERTY rather than "not Default_Ignorable", so a class that later
        strips them reds on the premise it actually violates. Both halves are pinned: the
        character SURVIVES the strip, and the mention is still REPORTED as a dead end — keeping
        it visible is only correct while R2 still catches the note, otherwise this row would be
        trading an over-block for a fail-open.
        """
        ch = chr(codepoint)
        assert unicodedata.category(ch) == "Cf", (
            f"U+{codepoint:04X} is no longer Cf, so this row no longer measures what it names")
        assert regex.fullmatch(r"\p{Prepended_Concatenation_Mark}", ch), (
            f"U+{codepoint:04X} ({unicodedata.name(ch, '?')}) is no longer a "
            "Prepended_Concatenation_Mark in this regex table — the row's premise is gone, so "
            "it now measures nothing")
        assert not self.INVISIBLE_RE.fullmatch(ch), (
            f"U+{codepoint:04X} ({unicodedata.name(ch, '?')}) was stripped: it renders a sign "
            "or mark, so the Prepended_Concatenation_Mark subtraction is gone and the "
            f"over-blocked marks are back: {self._strip_invisible(ch)!r}")
        hidden = f"Call tortoise_list_graphs{ch}(x=1) for names."
        assert self._strip_invisible(hidden) == hidden, (
            f"U+{codepoint:04X} ({unicodedata.name(ch, '?')}) was stripped: it renders a sign "
            "or mark, so the Prepended_Concatenation_Mark subtraction is gone and the "
            f"over-blocked marks are back: {self._strip_invisible(hidden)!r}")
        assert self.retired_call_forms(hidden) == [], (
            f"U+{codepoint:04X} was read as a plain call, but a reader SEES the mark between "
            f"the name and its bracket: {self.retired_call_forms(hidden)}")
        assert self.notes_missing_replacement(hidden), (
            "a mention carrying a visible mark was graded CLEAN — keeping the character is only "
            "correct while R2 still reports the dead end: "
            f"{self.notes_missing_replacement(hidden)}")

    @pytest.mark.parametrize("placement", ("inside-the-name", "before-the-bracket"))
    @pytest.mark.parametrize("codepoint", (
        # the interlinear annotation anchor / separator / terminator…
        0xFFF9, 0xFFFA, 0xFFFB,
        # …and the Egyptian hieroglyph format controls. Unicode keeps BOTH families out of
        # Default_Ignorable for LAYOUT reasons (ignoring them would change how adjacent text is
        # rendered), NOT because they render a glyph of their own — so a reader sees nothing here,
        # and r21's class left all 19 ACCEPTED in both placements (#14237 r22, measured).
        0x13430, 0x13431, 0x13432, 0x13433, 0x13434, 0x13435, 0x13436, 0x13437,
        0x13438, 0x13439, 0x1343A, 0x1343B, 0x1343C, 0x1343D, 0x1343E, 0x1343F,
    ))
    def test_a_Cf_member_Unicode_excludes_for_LAYOUT_is_STILL_invisible(
            self, codepoint, placement):
        """The gap r21 opened: excluded from Default_Ignorable ≠ renders a glyph (#14237 r22).

        r21 deleted r19's over-block by asking `Default_Ignorable_Code_Point`, and its prose then
        said "the Cf codepoints that are NOT Default_Ignorable are EXACTLY the ones Unicode
        excludes BECAUSE they render a sign or mark". Measured against the property itself: only
        THIRTEEN of the 32 are `Prepended_Concatenation_Mark`. The other NINETEEN are the
        interlinear annotation controls and the Egyptian hieroglyph format controls, which
        Unicode excludes because IGNORING them would change the layout of adjacent text — they
        render nothing of their own. A reader therefore reads `tortoise_list_graphs(x=1)`, which
        is a copyable call to a retired door, so leaving them unstripped is a false PASS of R1:
        measured ACCEPTED in BOTH placements at `0c25ea83ba` (r21's head).

        The premise assertions keep this row honest in both directions: the codepoint must still
        be Cf, must still be OUTSIDE Default_Ignorable (otherwise it proves nothing about the
        gap), and must still be outside Prepended_Concatenation_Mark (otherwise it belongs to
        the row above, where keeping it visible is correct).
        """
        ch = chr(codepoint)
        assert unicodedata.category(ch) == "Cf", (
            f"U+{codepoint:04X} is no longer Cf, so this row no longer measures what it names")
        assert not regex.fullmatch(r"\p{Default_Ignorable_Code_Point}", ch), (
            f"U+{codepoint:04X} became Default_Ignorable — this row's premise (a Cf member "
            "OUTSIDE that property) is gone, so it now measures nothing")
        assert not regex.fullmatch(r"\p{Prepended_Concatenation_Mark}", ch), (
            f"U+{codepoint:04X} is a Prepended_Concatenation_Mark, which renders a mark and is "
            "pinned visible by the row above — this row would contradict it")
        reference = f"&#{codepoint};"
        replacement = 'Use tortoise_graph_overview(section="graphs") instead.'
        plain = f"Call tortoise_list_graphs(x=1) for names. {replacement}\n"
        hidden = {
            "inside-the-name":
                f"Call tortoise{reference}_list_graphs(x=1) for names. {replacement}\n",
            "before-the-bracket":
                f"Call tortoise_list_graphs{reference}(x=1) for names. {replacement}\n",
        }[placement]
        assert self.retired_call_forms(plain) == ["tortoise_list_graphs:1"], (
            f"the plain control no longer reds, so this row measures nothing: {plain!r}")
        assert self.retired_call_forms(hidden) == ["tortoise_list_graphs:1"], (
            f"U+{codepoint:04X} {placement} hid a copyable call to the retired door — the "
            f"reader sees `tortoise_list_graphs(x=1)` in {hidden!r}, so this is a false PASS "
            f"of R1: {self.retired_call_forms(hidden)}")

    @pytest.mark.parametrize("placement", ("inside-the-name", "before-the-bracket"))
    @pytest.mark.parametrize("codepoint", (
        # C0 and C1 controls: no Unicode "invisible" property covers them, and they render as
        # nothing, so they hide the door exactly like a ZWSP does. Measured ACCEPTED in both
        # placements at every head before r22 (#14237 r22).
        0x0007, 0x001B, 0x009F,
    ))
    def test_a_CONTROL_character_is_stripped_too(self, codepoint, placement):
        r"""The family NO property above reaches: a C0/C1 control renders as nothing (#14237 r22).

        `Default_Ignorable_Code_Point` is Unicode's answer to "may a renderer drop this?" and it
        deliberately lists only format/variation characters — a control like U+0007 (BEL) or
        U+009F is Cc and answers FALSE, so a class built on that property alone left every one of
        them ACCEPTED in both placements (measured at r21's head). A browser renders them as
        nothing, so the reader reads a copyable `tortoise_list_graphs(x=1)`.

        `\p{Cc}` − `\p{White_Space}` is the term that closes it, and the White_Space subtraction
        is what keeps `\t`/`\n`/`\r` OUT — `CALL_FORM` says a newline is never a call, and a tab
        between the name and its bracket is already allowed as horizontal whitespace.

        ⚠️ U+0000 is NOT in this row on purpose: HTML5 maps a NUL to U+FFFD before the guard
        sees it, so the reader is looking at a visible replacement character. That arm is a
        declared residual in `INVISIBLE_RE`'s comment rather than a strip failure.
        """
        ch = chr(codepoint)
        assert unicodedata.category(ch) == "Cc", (
            f"U+{codepoint:04X} is no longer Cc, so this row no longer measures what it names")
        assert not regex.fullmatch(r"\p{Default_Ignorable_Code_Point}", ch), (
            f"U+{codepoint:04X} became Default_Ignorable — the row's premise (a control no "
            "invisible property covers) is gone")
        replacement = 'Use tortoise_graph_overview(section="graphs") instead.'
        # A LITERAL control, not an entity: markdown-it's own entity handling rewrites some
        # control references, so the entity spelling would measure the parser, not the guard.
        hidden = {
            "inside-the-name":
                f"Call tortoise_list{ch}_graphs(x=1) for names. {replacement}\n",
            "before-the-bracket":
                f"Call tortoise_list_graphs{ch}(x=1) for names. {replacement}\n",
        }[placement]
        assert self.retired_call_forms(hidden) == ["tortoise_list_graphs:1"], (
            f"U+{codepoint:04X} {placement} hid a copyable call to the retired door — a browser "
            f"renders that control as nothing, so this is a false PASS of R1: "
            f"{self.retired_call_forms(hidden)}")

    def test_a_VISIBLE_space_inside_the_name_is_still_a_plain_mention(self):
        """The bound beside the row above, stated so the codepoint class cannot widen silently.

        `tortoise&#160;_list_graphs` is NOT the retired name to a reader — an NBSP renders as
        a space, so the sentence shows `tortoise _list_graphs`, which nobody copies as a call
        to this door. It stays a NON-call mention (R2's question), which is why the strip holds
        Unicode's `Default_Ignorable_Code_Point` property (render-as-nothing) and not NBSP or
        the other visible spaces: widening it to "all Unicode spaces" would red correct prose
        for a shape that is not the door.
        """
        replacement = 'Use tortoise_graph_overview(section="graphs") instead.'
        text = f"Call tortoise&#160;_list_graphs(x=1) for names. {replacement}\n"
        assert self.retired_call_forms(text) == [], (
            "a VISIBLE space inside the name was read as the door, which is the over-block "
            f"direction: {self.retired_call_forms(text)}")

    def test_declared_residual_a_BLANK_GLYPH_that_is_NOT_the_property_is_accepted(self):
        """⚠️ DECLARED RESIDUAL — `tortoise_list_graphs\u2800(x=1)` is ACCEPTED (#14237 r21).

        U+2800 BRAILLE PATTERN BLANK renders as an EMPTY CELL, not as nothing, so Unicode does
        not list it as Default_Ignorable (measured: the property is false for it, and for the
        other blank-RENDERING glyphs, while it is true for all 4174 members the tests above
        span). A reader sees a blank between the name and its bracket and a line copied out of
        the doc carries the cell, so this is not the cleanly copyable `name(` the R1 rule
        forbids — which is why it is declared rather than chased.

        ⛔ The bound is stated here so it cannot be rediscovered as a finding, and so the next
        reader can tell it apart from a stale claim: "blank glyph" is an UNBOUNDED class (any
        one-cell space-like glyph), and enumerating it is exactly the failure this class has
        repaired twice. Pinned with the row style the line-break residual below uses.
        """
        ch = chr(0x2800)
        assert not self.INVISIBLE_RE.fullmatch(ch), (
            "U+2800 became Default_Ignorable — this residual is CLOSED: delete this row and say "
            "so in `INVISIBLE_RE`'s comment instead of leaving a stale bound")
        replacement = 'Use tortoise_graph_overview(section="graphs") instead.'
        text = f"Call tortoise_list_graphs{ch}(x=1) for names. {replacement}\n"
        assert self.retired_call_forms(text) == [], (
            "the blank-glyph residual is CLOSED — delete this row and say so in "
            f"`INVISIBLE_RE`'s comment: {self.retired_call_forms(text)}")

    def test_declared_residual_a_LINE_BREAK_inside_the_name_is_accepted(self):
        """⚠️ DECLARED RESIDUAL — `tortoise_list_\ngraphs(x=1)` is ACCEPTED, and it should be.

        A hard-wrapped name renders as two lines with a space between them
        (`tortoise_list_ graphs(x=1)`), so the reader is looking at a broken token, not at a
        command they can copy — the same reason `CALL_FORM` declares a newline never a call.
        Pinned here so this bound is recorded rather than discovered later: the invisible
        characters above are invisible to the READER, whereas a line break moves the second
        half of the name onto another line.
        """
        replacement = 'Use tortoise_graph_overview(section="graphs") instead.'
        text = f"Call tortoise_list_\ngraphs(x=1) for names. {replacement}\n"
        assert self.retired_call_forms(text) == [], (
            "the wrapped-name residual is CLOSED — delete this row and say so in the class "
            f"comment instead of leaving a stale bound: {self.retired_call_forms(text)}")

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
            sum(1 for hit in self.name_hits(text) if hit[0] == name)
            for name in RETIRED_USE_INSTEAD
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
