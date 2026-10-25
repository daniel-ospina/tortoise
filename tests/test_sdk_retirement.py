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
SDK_SRC = ROOT / "tortoise" / "sdk.py"

#: The owner-approved target table — `| 30 | `update_memory_graph` | … |`. Row
#: number → target name. Read from the doc, never typed: a rename's replacement must be
#: one of the rows the approving region points at.
_TARGET_ROW = re.compile(r"^\| (\d{1,2}) \| `([A-Za-z_][A-Za-z0-9_]*)")

#: `row 30` / `rows 30–33` — the range of target rows an approving region points at.
#: Accepts the en dash the doc uses and the ASCII hyphen, so a re-typeset row does not
#: read as "no approval".
_ROW_RANGE = re.compile(r"rows? (\d{1,2})(?:\s*[–-]\s*(\d{1,2}))?")


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
    assert m, (
        f"{name}: the recorded approval points at no target row, so it authorises no "
        f"replacement. Recorded: {quote!r}"
    )
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
