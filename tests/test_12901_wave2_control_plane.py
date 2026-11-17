"""#12901 wave 2 — the control plane left the public SDK surface, capability intact.

Wave 2 retired the canonical inventory's control-plane partition (`N1`–`N6`): the
sixteen console/hosted doors that are off the owner's approved 40 and bound by no MCP
tool. Unlike the #4282 surplus block (no callers at all) and wave 1 (one production
caller), **every** wave-2 name is called from `tortoise/hosted_api.py`,
`tortoise/__main__.py`, `tortoise/mcp_auth.py` or a `graph-scripts/` entry point. Each
one therefore took the `capture_session` → `_capture_session` shape: the body survives
under a leading underscore, its callers were re-pointed, and the public door closes.

`tests/test_sdk_retirement.py` already pins the RECORD (each name left the surface, is
accounted for in the inventory, and states its recorded destination). It cannot pin the
two properties that decide whether this wave is SAFE, and those are what this file is:

  1. **The capability survived** — every retired name has a live private seam.
  2. **No caller was left broken** — no production code still reaches the public name.
     This is the failure #3836 (c) forbids (a retirement may not leave a caller broken);
     it is invisible to a test that only reads `sdk_retired.py`, because a re-introduced
     `sdk.apikey_create(...)` would raise only on the code path that runs it.

Hermetic: names and seams are read off the class, and the caller scan is an AST walk.
No graph, no database.
"""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SDK_SRC = ROOT / "tortoise" / "sdk.py"

#: The wave's membership, stated ONCE here so the pin cannot silently shrink.
#:
#: The rule that produced it is re-derivable and is written in
#: `tortoise/sdk_retired.py::REMOVED_SDK_METHODS` above the wave-2 block: a member of the
#: canonical control-plane partition (`N1`–`N6`) that is off `tortoise/__all__.py` and
#: bound by no `tool_registry` entry. The five N-members that stayed, and why, are in
#: `docs/product/canonical-sdk-methods.md` (the wave-2 removals section).
WAVE2 = (
    "apikey_create",
    "apikey_revoke",
    "apikey_verify",
    "graph_active_key_count",
    "graph_delete",
    "graph_key_ids",
    "invitation_get_by_id",
    "invitation_list",
    "invitation_revoke",
    "membership_create",
    "org_get",
    "signup_token_lookup",
    "signup_token_recover",
    "signup_token_revoke",
    "sweep_invite_ghost_memberships",
    "trash_graphs",
)

#: Where a caller could be. Production code only: a suite is allowed to reach a seam, and
#: `archive/` is the code that already left.
CALLER_ROOTS = ("tortoise", "tools", "battery", "graph-scripts", "services", "apps")


def _public_methods() -> set[str]:
    from tortoise.sdk import TortoiseSDK

    return {
        n for n in dir(TortoiseSDK)
        if not n.startswith("_") and callable(getattr(TortoiseSDK, n, None))
    }


@pytest.mark.parametrize("name", WAVE2)
def test_the_wave_2_name_is_no_longer_public(name: str) -> None:
    assert name not in _public_methods(), (
        f"`{name}` is on the public surface again. Wave 2 closed this door; re-opening "
        "one is a surface change and needs the owner's approval FIRST (AGENTS.md → "
        "\"MCP/SDK Surface Approval\"), plus its record in `REMOVED_SDK_METHODS` removed."
    )


@pytest.mark.parametrize("name", WAVE2)
def test_the_wave_2_name_survives_as_a_private_seam(name: str) -> None:
    """Closing the door must not delete the capability.

    Every wave-2 name has a caller inside our own code (`hosted_api`, the CLI,
    `mcp_auth`, one `graph-scripts` entry point), so the departure shape is the private
    seam — not a deletion. A missing seam means the retirement broke a live route.
    """
    from tortoise.sdk import TortoiseSDK

    seam = f"_{name}"
    assert callable(getattr(TortoiseSDK, seam, None)), (
        f"`{name}` left the public surface with no `{seam}` seam. Wave 2's whole "
        "premise is that the BODY stays and only the DOOR closes — a deletion here "
        "breaks every hosted route that called it."
    )


@pytest.mark.parametrize("name", WAVE2)
def test_the_retirement_error_is_the_documented_one(name: str) -> None:
    """A removal with no recorded destination gets NO hint — and says so plainly.

    #3836 (c) retires a RENAME by failing with the replacement named. These names record
    no replacement ON THE SURFACE (`create_key`, `add_member`, … have no `def` yet, and
    `org_get`'s live destination has no approving beta region), so a hint would name
    something the owner never approved — the one thing the map may never invent.
    """
    from tortoise.sdk import TortoiseSDK
    from tortoise.sdk_retired import RETIRED_SDK_METHODS

    assert name not in RETIRED_SDK_METHODS, (
        f"`{name}` is recorded as a RETIRED (hinted) name; wave 2 records it as a "
        "REMOVAL. A hint must name an owner-approved replacement, which does not exist."
    )
    sdk = object.__new__(TortoiseSDK)  # no __init__: no graph, no DB
    with pytest.raises(AttributeError) as excinfo:
        getattr(sdk, name)
    message = str(excinfo.value)
    assert name in message, (
        f"the failure for `{name}` does not name it: {message!r}. A caller has to be "
        "able to tell which name is gone."
    )
    assert "use TortoiseSDK" not in message, (
        f"`{name}` emitted a migration hint: {message!r}. It has no recorded surface "
        "replacement, so a hint would be false."
    )


def test_no_production_caller_still_reaches_a_retired_door() -> None:
    """No production file may reach `.apikey_create` — only `._apikey_create`.

    This is the wave's real risk. `REMOVED_SDK_METHODS` is a record, not an enforcement:
    a caller left pointing at the closed door raises `AttributeError` only when its code
    path RUNS, which is how a retirement ships a broken route. The scan matches on the
    ATTRIBUTE and then judges nothing else — the wave-1 lesson is that a receiver-keyed
    scan (`sdk.<name>`) misses `TortoiseSDK().<name>` and `_make_sdk(...).<name>`, and
    both shapes are live in `hosted_api.py`.
    """
    offenders: list[str] = []
    for root in CALLER_ROOTS:
        base = ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts or path == SDK_SRC:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:  # pragma: no cover - a broken file is another gate's find
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr in WAVE2
                    and not node.attr.startswith("_")
                ):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} .{node.attr}")
    assert not offenders, (
        "production code still reaches a door wave 2 closed (it will raise "
        f"AttributeError when that path runs): {sorted(offenders)}"
    )


def test_the_wave_did_not_raise_the_live_count() -> None:
    """The PR contract: the live SDK count is STRICTLY LOWER than it found the tree.

    Derived, never typed: the 16 names are absent from the frozen surface list, from the
    manifest's SDK rows and from the owner's declaration, and the live class agrees with
    the frozen list. The count itself is read from `config/sdk-surface.json`, so this
    arm cannot drift from the number the gate reads.
    """
    from tortoise.sdk_retired import REMOVED_SDK_METHODS

    frozen = json.loads((ROOT / "config" / "sdk-surface.json").read_text(encoding="utf-8"))
    frozen_names = set(frozen["methods"])
    # ⛔ Loaded BY PATH, never as `tortoise.__all__`: importing the dotted name registers a
    # `tortoise.__all__` attribute on the package and breaks `from tortoise import *` for
    # the rest of the process — the trap
    # `test_sdk_surface_declaration.py::test_loading_the_declaration_by_path_does_not_
    # shadow_star_import` exists to pin.
    spec = importlib.util.spec_from_file_location(
        "_tortoise_all_declaration", ROOT / "tortoise" / "__all__.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    declared = set(module.__all__)
    live = _public_methods()

    assert live == frozen_names, (
        "the live class and the frozen surface list disagree — re-cut "
        "`config/sdk-surface.json` with `uv run python tools/sdk_surface.py`"
    )
    for name in WAVE2:
        assert name not in frozen_names, f"`{name}` is still in the frozen surface list"
        assert name not in declared, (
            f"`{name}` is on the owner's approved list `tortoise/__all__.py` — a wave "
            "may not retire an approved name, and that list is not ours to re-cut."
        )
        assert name in REMOVED_SDK_METHODS, f"`{name}` has no removal record"

    manifest = (ROOT / "config" / "surface-manifest.yml").read_text(encoding="utf-8")
    stale_rows = [f"- name: sdk:{n}" for n in WAVE2 if f"- name: sdk:{n}" in manifest]
    assert not stale_rows, (
        f"the manifest still carries SDK rows for retired names: {stale_rows}. Re-cut it "
        "with `uv run python tools/surface_manifest.py cut --commit <sha>` — never "
        "hand-edit it."
    )
