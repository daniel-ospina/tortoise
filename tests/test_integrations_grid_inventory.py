"""#3513 (epic #1714 R1/R6) — the integration grid's tile inventory is ONE
declaration, asserted across the surfaces that must agree.

WHAT THIS PROVES. The dashboard's grid tiles carry the canonical integration
keys. Clicking an unbuilt tile records demand (#3514), and the record is only
written when the key is in ``tortoise.demand.demand_tile_inventory()`` —
``DEMAND_DECLARED_TILES`` plus every ``config/connector_manifest.yaml``
connector. A tile key that is not in that inventory is a SILENT DROP: the user
clicks, the UI looks fine, and nothing is recorded. That is the exact shape the
epic's cross-surface assertion exists to catch, so it is asserted in both
directions here:

  * every JS tile key is accepted by the backend inventory, and
  * every backend inventory key is presented by the grid (or mapped onto the
    tiles that present it, e.g. the ``github`` connector → the issues +
    documents tiles).

The JS side is PARSED from ``website/apps/dashboard/src/integrationsGrid.js``
(text, no Node needed) — the checker reads the real declaration rather than a
re-listed copy, so a tile added only to the JS file fails here.

WHY A SEEDED-VIOLATION CONTROL. An assertion that cannot fail is not a check.
``test_the_assertion_discriminates`` mutates the parsed tile list to the failing
shape and shows the same predicate rejects it.

DB-FREE, NETWORK-FREE. Pure text + the demand module's cached inventory read.
"""
from __future__ import annotations

import re
from pathlib import Path

from tortoise import demand

REPO_ROOT = Path(__file__).resolve().parent.parent
GRID_JS = REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "integrationsGrid.js"


def _scan_balanced(src: str, start: int, opener: str, closer: str) -> str:
    """Return ``src[start:end+1]`` for the balanced ``opener``/``closer`` run.

    Comment- and string-aware (both quote styles): the JS declaration's copy and
    comments contain apostrophes (\"partner's brand kit\", \"repos'\") and
    brackets, so a naive depth count over raw text either never closes or closes
    on a bracket inside a comment.
    """
    assert src[start] == opener
    i = start
    depth = 0
    while i < len(src):
        ch = src[i]
        if ch == "/" and src[i + 1:i + 2] == "/":
            nl = src.find("\n", i)
            i = len(src) if nl < 0 else nl + 1
            continue
        if ch == "/" and src[i + 1:i + 2] == "*":
            end = src.find("*/", i + 2)
            assert end >= 0, "unterminated block comment"
            i = end + 2
            continue
        if ch in ("'", '"'):
            j = i + 1
            while j < len(src):
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == ch:
                    break
                j += 1
            i = j + 1
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError(f"unbalanced {opener}{closer} at {start}")


def _frozen_array_text(src: str, name: str) -> str:
    """The text of ``export const <name> = Object.freeze([ ... ])``.

    Bracket-matched (not a regex to the first ``]``), so a nested array inside a
    future tile cannot truncate the slice and silently hide entries.
    """
    marker = f"export const {name} = Object.freeze(["
    start = src.index(marker)
    open_bracket = src.index("[", start)
    return _scan_balanced(src, open_bracket, "[", "]")


def _tiles_from_source(src: str) -> list[dict[str, str]]:
    """The tile objects declared in ``INTEGRATION_TILES`` (key + state)."""
    text = _frozen_array_text(src, "INTEGRATION_TILES")
    tiles: list[dict[str, str]] = []
    for block in re.finditer(r"\{([^{}]*)\}", text):
        body = block.group(1)
        key = re.search(r"key:\s*'([^']+)'", body)
        if not key:
            continue
        state = re.search(r"state:\s*INTEGRATION_STATE\.(\w+)", body)
        tiles.append({
            "key": key.group(1),
            "state": state.group(1) if state else "",
        })
    return tiles


def _connector_key_map(src: str) -> dict[str, list[str]]:
    """``CONNECTOR_KEY_TO_TILES`` — connector keys presented as other tiles.

    The literal is ``Object.freeze({...})`` (braces, not brackets), so it is
    brace-matched to its close rather than reusing the array helper.
    """
    marker = "export const CONNECTOR_KEY_TO_TILES = Object.freeze({"
    start = src.index(marker)
    open_brace = src.index("{", start + len("export const CONNECTOR_KEY_TO_TILES = "))
    text = _scan_balanced(src, open_brace, "{", "}")
    out: dict[str, list[str]] = {}
    for m in re.finditer(r"(\w+):\s*Object\.freeze\(\[([^\]]*)\]\)", text):
        out[m.group(1)] = re.findall(r"'([^']+)'", m.group(2))
    return out


def _unknown_keys(keys, inventory) -> list[str]:
    return sorted(k for k in keys if k not in inventory)


def test_grid_source_exists_and_declares_tiles() -> None:
    assert GRID_JS.exists(), "the grid module must exist (this test reads its real declaration)"
    tiles = _tiles_from_source(GRID_JS.read_text())
    assert len(tiles) >= len(demand.DEMAND_DECLARED_TILES), "the grid must present at least the declared tiles"


def test_every_grid_tile_key_is_accepted_by_the_demand_inventory() -> None:
    tiles = _tiles_from_source(GRID_JS.read_text())
    inventory = demand.demand_tile_inventory()
    unknown = _unknown_keys({t["key"] for t in tiles}, inventory)
    assert unknown == [], (
        "these grid tile keys would record NOTHING when clicked (not in "
        f"tortoise/demand.py's inventory): {unknown}. Add the connector/tile "
        "key to config/connector_manifest.yaml or DEMAND_DECLARED_TILES."
    )


def test_every_declared_tile_is_presented_by_the_grid() -> None:
    tiles = {t["key"] for t in _tiles_from_source(GRID_JS.read_text())}
    missing = sorted(set(demand.DEMAND_DECLARED_TILES) - tiles)
    assert missing == [], f"declared demand tiles with no grid presentation: {missing}"


def test_every_manifest_connector_is_presented_or_mapped() -> None:
    src = GRID_JS.read_text()
    tiles = {t["key"] for t in _tiles_from_source(src)}
    mapping = _connector_key_map(src)
    # The manifest's connector keys are exactly the inventory minus the declared
    # non-connector tiles.
    connector_keys = sorted(demand.demand_tile_inventory() - set(demand.DEMAND_DECLARED_TILES))
    for key in connector_keys:
        if key in tiles:
            continue
        mapped = mapping.get(key)
        assert mapped, f"connector key {key!r} has neither a tile nor a CONNECTOR_KEY_TO_TILES mapping"
        assert all(m in tiles for m in mapped), f"connector {key!r} maps to tiles that do not exist: {mapped}"


def test_tile_keys_are_unique() -> None:
    keys = [t["key"] for t in _tiles_from_source(GRID_JS.read_text())]
    assert len(keys) == len(set(keys)), f"duplicate tile keys: {keys}"


def test_shipped_tiles_are_exactly_the_declared_tiles() -> None:
    tiles = _tiles_from_source(GRID_JS.read_text())
    shipped = {t["key"] for t in tiles if t["state"] == "SHIPPED"}
    assert shipped == set(demand.DEMAND_DECLARED_TILES)


def test_the_assertion_discriminates() -> None:
    """Seeded violation: a bogus tile key is REJECTED by the same predicate."""
    inventory = demand.demand_tile_inventory()
    assert _unknown_keys({"agent_sessions", "not_a_real_integration"}, inventory) == ["not_a_real_integration"]
    # and the parsed list really is the shipped one (the control cannot pass by
    # parsing nothing)
    keys = {t["key"] for t in _tiles_from_source(GRID_JS.read_text())}
    assert "agent_sessions" in keys
    assert "slack" in keys
