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

# The ONE section that states a rename, per the beta doc's own heading. Scoped so a
# backticked pair elsewhere in the doc (a target row, a discarded row) cannot be read as
# an approved rename.
_RENAME_HEADING = "### Renamed, relocated, or re-homed — or deleted as a duplicate alias"
# `| \`old\` | → row 11 \`new\`. |` — a single-name row whose disposition names the
# destination. Rows naming a group/wildcard are deliberately NOT read: the map is a
# single name → single name lookup and a group row would need a choice this test must
# not make.
_RENAME_ROW = re.compile(
    r"^\| `([a-z_][a-z0-9_]*)` \| → rows? \d+(?:[–-]\d+)? .*?`([a-z_][a-z0-9_]*)`"
)


def _approved_renames() -> dict[str, str]:
    """The owner-approved `old -> new` pairs, read from the beta doc."""
    text = BETA_DOC.read_text(encoding="utf-8")
    start = text.index(_RENAME_HEADING)
    out: dict[str, str] = {}
    for line in text[start:].splitlines()[1:]:
        if line.startswith("## "):
            break
        m = _RENAME_ROW.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


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
    """Each entry's exact `old -> new` pair is in the owner-approved beta rename table."""
    from tortoise.sdk_retired import RETIRED_SDK_METHODS

    approved = _approved_renames()
    wrong = {
        old: (new, approved.get(old))
        for old, new in RETIRED_SDK_METHODS.items()
        if approved.get(old) != new
    }
    assert not wrong, (
        "a retired name's replacement is not the owner-approved one (the #4282 mandate: "
        f"an unapproved rename needs Daniel first): {wrong}"
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
