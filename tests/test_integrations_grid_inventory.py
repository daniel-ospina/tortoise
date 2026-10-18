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

#3514 (the CLICK's client half) extends the same declaration one level out. The
grid's tiles are the keys a click NAMES; ``demandCapture.js`` is the code that
names one — the route, the body's field names, the action it sends and the
``org_key`` it must never send. Every one of those is a place where the client
and the server can disagree while both look right, and two of them are SILENT on
the server side: an unregistered prop is dropped by ``_ALLOWED_ANALYTICS_PROPS``
(no error, no row field, demand under-counted) and a body field renamed only in
the client records nothing at all. So they are asserted here, against the real
server objects (``hosted_api.app.routes``, ``_ALLOWED_ANALYTICS_PROPS``,
``demand.*``), not against a re-listed copy.

WHY A SEEDED-VIOLATION CONTROL. An assertion that cannot fail is not a check.
``test_the_assertion_discriminates`` mutates the parsed tile list to the failing
shape and shows the same predicate rejects it.

DB-FREE, NETWORK-FREE. Pure text + the demand module's cached inventory read.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.routing import APIRoute

from tortoise import demand

REPO_ROOT = Path(__file__).resolve().parent.parent
GRID_JS = REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "integrationsGrid.js"
#: #3514: the click's CLIENT half — the module that owns the route, the body and
#: the action the dashboard sends.
DEMAND_JS = REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "demandCapture.js"
MAIN_JS = REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "main.jsx"


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


# ── #3514: the client half of the same declaration ──────────────────────

def _js_string_const(src: str, name: str) -> str:
    """The value of ``export const <name> = '<literal>'`` in a dashboard module."""
    m = re.search(rf"export const {name} = '([^']*)'", src)
    assert m, f"{name} must be a plain string literal in the client module"
    return m.group(1)


def _js_body_keys(src: str) -> set[str]:
    """The keys of the body ``demandPayload`` returns.

    Read from the returned object literal itself (not from a comment): the
    function is the ONE place the wire shape is built, so its literal IS the
    contract.
    """
    start = src.index("export function demandPayload(")
    end = src.index("export function newDemandKey(", start)
    body = src[start:end]
    ret = body.index("return {")
    open_brace = body.index("{", ret)
    literal = _scan_balanced(body, open_brace, "{", "}")
    # Split the literal's top level on commas and take each entry's KEY — the
    # name before `:`, or the bare identifier of a shorthand property (`action`,
    # which a colon-only regex read past, silently accepting a body with no
    # action at all).
    keys: set[str] = set()
    depth = 0
    piece = ""
    for ch in literal[1:-1] + ",":
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            name = piece.split(":")[0].strip()
            assert re.fullmatch(r"\w+", name), f"unreadable body entry: {piece!r}"
            keys.add(name)
            piece = ""
            continue
        piece += ch
    return keys


def test_the_client_posts_the_route_the_app_actually_mounts() -> None:
    """The client's path must be a route the served table answers with POST.

    A drift here is a 404 the UI reports as "Your request was not confirmed" —
    true, and indistinguishable from a broken sink.
    """
    from tortoise.hosted_api import app

    client_path = _js_string_const(DEMAND_JS.read_text(), "DEMAND_PATH")
    served = {
        route.path
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == client_path
        and "POST" in (route.methods or set())
    }
    assert served == {client_path}, (
        f"the dashboard posts {client_path!r}, which the route table does not "
        f"answer with POST — the click would 404 while looking recorded"
    )


def test_the_client_body_keys_are_the_registered_props() -> None:
    """Every field the client sends is a prop the funnel store KEEPS.

    The silent-drop trap: ``_track_analytics_event`` drops an unregistered prop
    with no error, so a renamed client field records a row that is missing the
    fact the operator ranks on. ``org_key`` is deliberately NOT in the body —
    the server derives it from the authenticated team.
    """
    from tortoise.hosted_api import _ALLOWED_ANALYTICS_PROPS

    keys = _js_body_keys(DEMAND_JS.read_text())
    assert keys == {"integration_key", "action", "surface"}, keys
    assert "org_key" not in keys, "org_key is SERVER-derived; a client copy would corrupt the ranking"
    unregistered = keys - set(_ALLOWED_ANALYTICS_PROPS)
    assert unregistered == set(), (
        f"the client sends {sorted(unregistered)} but the analytics allowlist "
        f"drops them silently — the record would be written without them"
    )


def test_the_client_sends_the_server_action_vocabulary() -> None:
    """One action, and it is the server's own name for it."""
    client_action = _js_string_const(DEMAND_JS.read_text(), "DEMAND_ACTION_CLICK")
    assert client_action == demand.DEMAND_ACTION_CLICK
    assert client_action in demand.DEMAND_ACTIONS
    # The client sends clicks only: a dismissal is not demand (the read half
    # counts clicks, so a client that posted dismissals would inflate #3541's
    # trigger). Asserted as an ABSENCE in the module, with the pinned exception
    # that the payload builder forwards what it is handed.
    main = MAIN_JS.read_text()
    assert "action: 'go_back'" not in main, "the dashboard must not record dismissals as demand"
    assert "DEMAND_ACTION_CLICK" in DEMAND_JS.read_text()


def test_the_client_records_which_surface_the_click_came_from() -> None:
    """Both mounts declare a surface, and it is a value the record can carry."""
    src = DEMAND_JS.read_text()
    for name in ("DEMAND_SURFACE_WIZARD", "DEMAND_SURFACE_SETTINGS"):
        value = _js_string_const(src, name)
        assert value and len(value) <= demand.DEMAND_MAX_SURFACE_LEN, name
    main = MAIN_JS.read_text()
    assert "surface: DEMAND_SURFACE_SETTINGS" in main
    assert "surface={DEMAND_SURFACE_WIZARD}" in main


def test_the_client_half_is_wired_not_merely_declared() -> None:
    """Seeded-violation control for the three assertions above.

    The module existing proves nothing: the dialog must actually post through
    it. Read as a wiring fact, not a copy — the call site names the module's
    own function and the App's session seam.
    """
    main = MAIN_JS.read_text()
    assert "from './demandCapture.js'" in main
    assert "recordDemand({ integrationKey, surface, idempotencyKey, request: api })" in main
    assert "X-Idempotency-Key" in DEMAND_JS.read_text()
