"""A retired SDK name FAILS and NAMES its replacement (#4282 phase 2).

The owner ruled (#3836, option (c), 2026-09-23): a retired SDK name stops resolving and
raises with the migration hint. There is deliberately **no alias layer and no warning
shim**. `tortoise/sdk_retired.py` holds the names that have ALREADY left the public
surface; `TortoiseSDK.__getattr__` turns each into that failure.

This file is the gate on that map, in both directions:

  * **Every entry is an APPROVED rename.** The old→new pair must appear in the
    owner-approved `docs/product/beta-sdk-surface.md` "Renamed …" table. Without this a
    lane could retire a LIVE method and point every caller at a name the owner never
    approved — the #4282 mandate (an addition, or a rename not in the approved tables,
    needs Daniel first).
  * **The rename LANDED, and the replacement EXISTS.** The owner's Option B is *"retire
    each legacy name in the same release that lands its replacement"*, so a retired name
    whose replacement is not on the surface is the "into a vacuum" case, refused here.

The test is hermetic: it reads the source and the doc, and it exercises `__getattr__` on
an instance built with `object.__new__` (no `__init__`, so no graph and no DB).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BETA_DOC = ROOT / "docs" / "product" / "beta-sdk-surface.md"
INVENTORY_DOC = ROOT / "docs" / "product" / "canonical-sdk-methods.md"
SDK_SRC = ROOT / "tortoise" / "sdk.py"

#: The floor of the RECORDED universe: every name this phase has accounted for, once.
#:
#: 155 = the 149-method surface the #4282 phase-2 ledger started from (the "149 today"
#: in `beta-sdk-surface.md`) + the 6 names retired before it (they left at an earlier
#: phase, so they are not part of that 149). Each name is counted exactly once: it is
#: public today, or it is in `RETIRED_SDK_METHODS`, or it is in `REMOVED_SDK_METHODS`.
#:
#: It is a FLOOR, not an equality, on purpose: a name ADDS to the surface legitimately
#: (through the approved path and its own gate) and must not red this pin, while a name
#: that LEAVES without landing in one of the two records drops the total by one.
ACCOUNTED_SURFACE_FLOOR = 155

#: The owner-approved target table — `| 30 | `update_memory_graph` | … |`. Row
#: number → target name. Read from the doc, never typed: a rename's replacement must be
#: one of the rows the approving region points at.
_TARGET_ROW = re.compile(r"^\| (\d{1,2}) \| `([A-Za-z_][A-Za-z0-9_]*)")

#: `row 30` / `rows 30–33` — the range of target rows an approving region points at.
#: Accepts the en dash the doc uses and the ASCII hyphen, so a re-typeset row does not
#: read as "no approval".
_ROW_RANGE = re.compile(r"rows? (\d{1,2})(?:\s*[–-]\s*(\d{1,2}))?")


def _mentions(quote: str, name: str) -> bool:
    """Does `quote` name `name` as a WHOLE identifier (not as a substring of another)?"""
    return bool(
        re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", quote)
    )


#: `Recorded destination `x`` / `Recorded destinations `x`, `y`` — the ONE claim shape a
#: surplus removal's prose may use to state where a caller should go instead.
_DESTINATION_CLAIM = re.compile(
    r"Recorded destinations? ((?:`[A-Za-z_][A-Za-z0-9_]*`(?:, )?)+)"
)


def _inventory_destinations() -> dict[str, tuple[str, ...]]:
    """The destination the NON-OWNER inventory records, per name it lists.

    Its third column is the recorded disposition (`tools/sdk_rename_table.py` cites it and
    the owner ledger agrees name-for-name), so it is READ here, never re-typed: a cell of
    `—` means no destination was recorded, and each backticked identifier in it is one.
    """
    out: dict[str, tuple[str, ...]] = {}
    for line in INVENTORY_DOC.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 3:
            continue
        m = re.fullmatch(r"`([A-Za-z_][A-Za-z0-9_]*)`", cells[0])
        if not m:
            continue
        cell = cells[2]
        out[m.group(1)] = (
            () if cell.startswith("—")
            else tuple(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", cell))
        )
    return out


def _target_rows() -> dict[int, str]:
    """The numbered owner-approved target rows of `beta-sdk-surface.md`."""
    out: dict[int, str] = {}
    for line in BETA_DOC.read_text(encoding="utf-8").splitlines():
        m = _TARGET_ROW.match(line)
        if m:
            out[int(m.group(1))] = m.group(2)
    assert len(out) == 40, (
        f"the beta doc's target table no longer parses as 40 numbered rows (got {len(out)}); "
        "every retirement's approval check is derived from it, so it cannot be guessed"
    )
    return out


def _norm(text: str) -> str:
    """Whitespace-normalised text, so a re-wrap cannot fake (or hide) a citation."""
    return " ".join(text.split())


def _approved_replacements(name: str) -> set[str]:
    """The target names the owner's ledger authorises `name` to become.

    Three shapes of approval appear in the beta doc, and all three are read here — each
    entry of `RETIRED_SDK_APPROVALS` quotes the region that authorises it:

      1. a single-name rename row — ``| `events_poll` | → row 11 `poll_events`. |``
      2. a GROUP rename row — ``| `graph_delete`, `graph_restore`, `graph_list`,
         `graph_set_name` | → rows 30–33 `*_memory_graph*`. |``
      3. a discarded-row entry that names its destination inline —
         ``~~`graph_set_recording`~~ … folds into **`update_memory_graph`** (row 30)``

    A parser that accepted only shape 1 could never authorise shapes 2 and 3 — which is
    112 of the 114 renames the ledger records — so it would block phase 2 permanently.
    The quote is what keeps this checkable: it must be present VERBATIM in the doc, it
    must name the retired name, and the replacement must be one of the numbered target
    rows it points at. WHICH row of a range a name collapses into is the author's
    decision, settled by review — see `tortoise/sdk_retired.py`'s residual note.
    """
    from tortoise.sdk_retired import RETIRED_SDK_APPROVALS

    quote = _norm(RETIRED_SDK_APPROVALS[name])
    assert quote in _norm(BETA_DOC.read_text(encoding="utf-8")), (
        f"{name}: the recorded approval is NOT in {BETA_DOC.name} — an approval cannot "
        f"be invented. Recorded: {quote!r}"
    )
    assert name in quote, (
        f"{name}: the recorded approval does not name the retired name. Recorded: {quote!r}"
    )
    m = _ROW_RANGE.search(quote)
    if not m:
        # A region that names a destination WITHOUT numbering it — the owner ledger's
        # "Removed" table does exactly this for a group at a time (`narrow readers (…) →
        # `get_entity``). There is no ordinal in the text, so the approved set is the
        # targets the region NAMES — and only when it names exactly ONE, because two or
        # more is a CHOICE, and a choice belongs to the author, not to a parser.
        named = sorted({t for t in _target_rows().values() if _mentions(quote, t)})
        assert len(named) == 1, (
            f"{name}: the recorded approval points at no target row and names "
            f"{len(named)} approved targets ({named}), so it authorises no single "
            f"replacement. Recorded: {quote!r}"
        )
        return set(named)
    lo = int(m.group(1))
    hi = int(m.group(2) or lo)
    rows = _target_rows()
    assert hi >= lo, f"{name}: inverted row range rows {lo}–{hi}"
    missing = [n for n in range(lo, hi + 1) if n not in rows]
    assert not missing, f"{name}: the approval names target rows that do not exist: {missing}"
    return {rows[n] for n in range(lo, hi + 1)}


def _public_methods() -> set[str]:
    """The live public `TortoiseSDK` surface, from a fresh AST walk of the class body."""
    tree = ast.parse(SDK_SRC.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "TortoiseSDK":
            return {
                n.name
                for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not n.name.startswith("_")
            }
    raise AssertionError("no top-level `TortoiseSDK` class in tortoise/sdk.py")


def test_the_map_is_not_empty() -> None:
    """A silently-empty map would turn every assertion below vacuously true."""
    from tortoise.sdk_retired import RETIRED_SDK_METHODS

    assert RETIRED_SDK_METHODS, (
        "no retired SDK name is declared. If phase 2 has not renamed anything yet, this "
        "file's other assertions are vacuous — state that explicitly rather than pass."
    )


def test_every_retired_name_is_an_approved_rename() -> None:
    """Each entry's replacement is a target row the owner's approving region names."""
    from tortoise.sdk_retired import RETIRED_SDK_METHODS

    wrong = {}
    for old, new in RETIRED_SDK_METHODS.items():
        approved = _approved_replacements(old)
        if new not in approved:
            wrong[old] = (new, sorted(approved))
    assert not wrong, (
        "a retired name's replacement is not one the owner-approved region names (the "
        f"#4282 mandate: an unapproved rename needs Daniel first): {wrong}"
    )


def test_every_retired_name_carries_its_own_approval() -> None:
    """The two maps are the same key set — an entry cannot skip its citation."""
    from tortoise.sdk_retired import RETIRED_SDK_APPROVALS, RETIRED_SDK_METHODS

    assert set(RETIRED_SDK_APPROVALS) == set(RETIRED_SDK_METHODS), (
        "a retirement with no recorded approval region, or an approval with no "
        "retirement: "
        f"{sorted(set(RETIRED_SDK_APPROVALS) ^ set(RETIRED_SDK_METHODS))}"
    )


def test_each_retirement_records_the_row_it_chose() -> None:
    """A RANGE approval does not pin WHICH row — the recorded ordinal does.

    The group row approves rows 30–33, so `"graph_list": "delete_memory_graph"` would
    satisfy every other assertion in this file and its hint would send every caller to
    the eraser. This test is the pin that makes the author's choice an artifact instead
    of a runtime re-decision: the recorded ordinal must resolve to the DECLARED
    replacement, in a row the approving region actually names.
    """
    from tortoise.sdk_retired import (
        RETIRED_SDK_APPROVAL_ROWS,
        RETIRED_SDK_METHODS,
    )

    assert set(RETIRED_SDK_APPROVAL_ROWS) == set(RETIRED_SDK_METHODS), (
        "every retirement needs its chosen target row, and only retirements may have "
        f"one: {sorted(set(RETIRED_SDK_APPROVAL_ROWS) ^ set(RETIRED_SDK_METHODS))}"
    )
    rows = _target_rows()
    wrong: dict[str, tuple] = {}
    for old, new in RETIRED_SDK_METHODS.items():
        row = RETIRED_SDK_APPROVAL_ROWS[old]
        if rows.get(row) != new:
            wrong[old] = (row, rows.get(row), new)
    assert not wrong, (
        "the recorded target row does not name the declared replacement "
        "(row, the name on that row, the declared replacement): "
        f"{wrong}"
    )
    outside = {
        old: (RETIRED_SDK_APPROVAL_ROWS[old], sorted(_approved_replacements(old)))
        for old in RETIRED_SDK_METHODS
        if rows[RETIRED_SDK_APPROVAL_ROWS[old]] not in _approved_replacements(old)
    }
    assert not outside, (
        "the recorded target row lies OUTSIDE the region the owner approved — the "
        "ordinal cannot be used to smuggle in a row the approval never named: "
        f"{outside}"
    )


def test_each_retired_name_left_the_public_surface() -> None:
    """`old` must no longer be a public method — a map entry for a LIVE name is a lie."""
    from tortoise.sdk_retired import RETIRED_SDK_METHODS

    public = _public_methods()
    still_live = sorted(set(RETIRED_SDK_METHODS) & public)
    assert not still_live, (
        f"declared retired but still defined on `TortoiseSDK`: {still_live}. The map is a "
        "record of what LEFT the surface; a name still present must not be in it."
    )


def test_every_removal_is_recorded_and_documented() -> None:
    """A name that left with NO replacement must still be ACCOUNTED FOR.

    This is the complement the map never had (#4282 phase 2.3):
    `test_each_retired_name_left_the_public_surface` pins map → surface, so it catches a
    record for a LIVE name. Nothing pinned surface → record, so `graph_count` and
    `org_list` left the surface with no entry anywhere and the departure was silent.

    Three directions here: the record may not name a live method, the two records may
    not overlap (a name is renamed OR removed, never both), and each removed name must
    also be accounted for in the non-owner inventory — so a junk entry cannot be used
    to satisfy the count pin below.

    Non-vacuity: on the pre-#4282-2.3 tree this test fails twice over — `REMOVED_SDK_METHODS`
    does not exist, and `graph_count` / `org_list` are still public methods.
    """
    from tortoise.sdk_retired import REMOVED_SDK_METHODS, RETIRED_SDK_METHODS, retirement_hint

    public = _public_methods()
    still_live = sorted(set(REMOVED_SDK_METHODS) & public)
    assert not still_live, (
        f"declared removed but still defined on `TortoiseSDK`: {still_live}. The record "
        "is of what LEFT the surface; a name still present must not be in it."
    )
    both = sorted(set(REMOVED_SDK_METHODS) & set(RETIRED_SDK_METHODS))
    assert not both, (
        f"a name is recorded as BOTH renamed and removed: {both}. A departure has one "
        "shape — a replacement to name, or none."
    )
    inventory = INVENTORY_DOC.read_text(encoding="utf-8")
    unaccounted = sorted(n for n in REMOVED_SDK_METHODS if f"`{n}`" not in inventory)
    assert not unaccounted, (
        f"removed from the surface with no trace in {INVENTORY_DOC.name}: {unaccounted}. "
        "A removal with no replacement owes at least a RECORD; the inventory is the "
        "non-owner document a reader reaches for what exists."
    )
    hinted = sorted(n for n in REMOVED_SDK_METHODS if retirement_hint(n) is not None)
    assert not hinted, (
        f"a REMOVED name emits a retirement hint: {hinted}. These have no approved "
        "replacement, so a hint would name a destination the owner never approved."
    )


def test_the_accounted_surface_never_shrinks() -> None:
    """Every name this phase has accounted for stays accounted for, exactly once.

    The direction that matters is the decrement: a method deleted from `TortoiseSDK`
    without a record drops this total by one, which is the silent departure the two
    records exist to prevent. Surface GROWTH cannot red this pin (it is a floor), so it
    does not duplicate the approved-addition gate.
    """
    from tortoise.sdk_retired import REMOVED_SDK_METHODS, RETIRED_SDK_METHODS

    accounted = (
        len(_public_methods()) + len(RETIRED_SDK_METHODS) + len(REMOVED_SDK_METHODS)
    )
    assert accounted >= ACCOUNTED_SURFACE_FLOOR, (
        f"the recorded surface has SHRUNK to {accounted} (floor "
        f"{ACCOUNTED_SURFACE_FLOOR}): a public method disappeared without landing in "
        "`RETIRED_SDK_METHODS` (a rename — it must name its replacement) or "
        "`REMOVED_SDK_METHODS` (internalised or deleted — it must say why)."
    )


def test_every_replacement_exists() -> None:
    """Option B: the replacement lands in the same release the retiree leaves."""
    from tortoise.sdk_retired import RETIRED_SDK_METHODS

    public = _public_methods()
    missing = sorted(set(RETIRED_SDK_METHODS.values()) - public)
    assert not missing, (
        f"a retired name points at a replacement that is not on the surface: {missing}. "
        "Retiring into a vacuum breaks callers with no migration target (owner's Option B)."
    )


def test_a_retired_name_fails_and_names_its_replacement() -> None:
    """The behaviour the ruling is about, exercised through `__getattr__`."""
    from tortoise.sdk import TortoiseSDK
    from tortoise.sdk_retired import RETIRED_SDK_METHODS

    # `object.__new__` skips `__init__`: this test needs no graph, no DB and no fixture.
    sdk = object.__new__(TortoiseSDK)
    for old, new in RETIRED_SDK_METHODS.items():
        try:
            getattr(sdk, old)
        except AttributeError as exc:
            assert new in str(exc), (
                f"`TortoiseSDK.{old}` failed without naming `{new}` — the whole point of "
                f"option (c) is the migration hint. Got: {exc}"
            )
        else:
            raise AssertionError(
                f"`TortoiseSDK.{old}` still RESOLVES — there is no alias layer under the "
                "owner's #3836 (c) ruling, so it must raise"
            )


def test_a_name_that_was_never_public_gets_no_retirement_claim() -> None:
    """The hint must not be attached to names that were never retired."""
    from tortoise.sdk import TortoiseSDK
    from tortoise.sdk_retired import retirement_hint

    assert retirement_hint("definitely_not_a_real_method") is None
    sdk = object.__new__(TortoiseSDK)
    with pytest.raises(AttributeError) as exc:
        _ = sdk.definitely_not_a_real_method
    assert "retired" not in str(exc.value).lower(), (
        f"a name that was never public claimed to be a retirement: {exc.value}"
    )


# ══════════════════════════════════════════════════════════════════════════
# #4282 surplus block (#10226 review, P2) — the RECORDED DESTINATION is a claim
# ══════════════════════════════════════════════════════════════════════════
#
# The 26-name surplus block used to carry each departure's destination in free prose
# only, and it drifted off the record it claimed to quote: measured 2026-11-10, five
# entries named a target the record never recorded (or named one where it records
# NONE) and three claimed "no destination" for names the record does give one, while
# both counters above the block were wrong. `tortoise/sdk_retired.py` now holds the
# destinations in ONE machine-readable cell; these tests hold that cell, and every
# reason written from it, to the inventory — in both directions.

def test_every_surplus_destination_is_the_one_the_record_records() -> None:
    """The cell IS the inventory's column — re-typed values red here, not in production.

    Non-vacuity: on the pre-fix tree this fails on eight names — `batch_create_points`
    (`create_entity` vs `write_knowledge_batch`), `get_provenance_chain`
    (`check_confidence` vs `get_entity`), `list_drafts` (`update_knowledge` vs
    `list_knowledge`), `backfill_about_entities`/`backfill_sources`/`complete_source`
    (a target where the record has `—`), and `apikey_list`/`membership_list`/
    `membership_delete` (`—` where the record has a target).
    """
    from tortoise.sdk_retired import SURPLUS_DELETION_DESTINATIONS

    inventory = _inventory_destinations()
    missing = sorted(n for n in SURPLUS_DELETION_DESTINATIONS if n not in inventory)
    assert not missing, (
        f"declared in the surplus destination cell but absent from "
        f"{INVENTORY_DOC.name}: {missing}. The inventory is the record this cell "
        "transcribes; a name it does not list has no recorded disposition."
    )
    wrong = {
        n: (dests, inventory[n])
        for n, dests in SURPLUS_DELETION_DESTINATIONS.items()
        if dests != inventory[n]
    }
    assert not wrong, (
        "a surplus name's recorded destination is not the one "
        f"{INVENTORY_DOC.name} records (cell, inventory): {wrong}"
    )


def test_a_surplus_reason_states_only_the_recorded_destination() -> None:
    """The prose may neither miss the recorded destination nor invent a different one.

    The claim sentence is the only shape allowed to state a destination
    (`Recorded destination[s] `x``), so it is parsed and compared as a SET: a reason
    that names `create_entity` while the record says `write_knowledge_batch` reds even
    though it also mentions the right one somewhere else.
    """
    from tortoise.sdk_retired import REMOVED_SDK_METHODS, SURPLUS_DELETION_DESTINATIONS

    wrong: dict[str, tuple] = {}
    for name, dests in SURPLUS_DELETION_DESTINATIONS.items():
        if name not in REMOVED_SDK_METHODS:
            continue  # a RETIREMENT: pinned by test_every_retired_name_is_an_approved_rename
        reason = REMOVED_SDK_METHODS[name]
        claim = _DESTINATION_CLAIM.search(reason)
        claimed = (
            tuple(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", claim.group(1)))
            if claim else ()
        )
        if claimed != dests:
            wrong[name] = (claimed, dests, reason)
    assert not wrong, (
        "a surplus removal's stated destination is not its recorded one "
        "(claimed, recorded, reason): "
        f"{ {k: v[:2] for k, v in wrong.items()} }"
    )


def test_a_live_recorded_destination_is_hinted_or_declared() -> None:
    """A destination a caller can actually call must be NAMED on failure (#3836 (c)).

    Measured at this head two surplus names have a LIVE recorded destination. The hint is
    free for `get_provenance_chain` (`get_entity` has a `def`), and it carries one;
    `create_or_update_point` cannot, because two records disagree about its destination —
    so that divergence is DECLARED in `UNHINTED_LIVE_DESTINATIONS` with its reason rather
    than silently skipped. Both directions are pinned: an undeclared live miss reds, and
    so does a declaration that has stopped being live (a stale excuse).
    """
    from tortoise.sdk_retired import (
        RETIRED_SDK_METHODS,
        SURPLUS_DELETION_DESTINATIONS,
        UNHINTED_LIVE_DESTINATIONS,
    )

    public = _public_methods()
    live_unhinted = {
        name: dest
        for name, dests in SURPLUS_DELETION_DESTINATIONS.items()
        for dest in dests
        if dest in public and RETIRED_SDK_METHODS.get(name) != dest
    }
    assert set(live_unhinted) == set(UNHINTED_LIVE_DESTINATIONS), (
        "a surplus name whose recorded destination is LIVE on `TortoiseSDK` today is "
        "neither hinted (RETIRED_SDK_METHODS -> that destination) nor declared as a "
        f"recorded divergence (live-and-unhinted, declared): {sorted(live_unhinted)}, "
        f"{sorted(UNHINTED_LIVE_DESTINATIONS)}"
    )
    silent = sorted(n for n, why in UNHINTED_LIVE_DESTINATIONS.items() if not why.strip())
    assert not silent, f"a declared divergence carries no reason: {silent}"
    unstated = sorted(
        n for n, dest in live_unhinted.items() if f"`{dest}`" not in UNHINTED_LIVE_DESTINATIONS[n]
    )
    assert not unstated, (
        "a declared divergence does not NAME the live destination it excuses, so the "
        f"reader cannot tell which record wins: {unstated}"
    )


def test_the_surplus_counters_state_the_cell() -> None:
    """The block's own header counts the cell — and both of its numbers were once wrong."""
    from tortoise.sdk_retired import SURPLUS_DELETION_DESTINATIONS

    with_dest = sum(1 for d in SURPLUS_DELETION_DESTINATIONS.values() if d)
    assert (len(SURPLUS_DELETION_DESTINATIONS), with_dest) == (26, 14), (
        "the #4282 surplus block header states 26 names: 14 with a recorded destination "
        f"and 12 without. The cell now holds {len(SURPLUS_DELETION_DESTINATIONS)} names, "
        f"{with_dest} with one — restate the header (and this pin) with the record."
    )
