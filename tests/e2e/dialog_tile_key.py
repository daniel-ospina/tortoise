"""Which tile a coming-soon dialog ACTUALLY opened — the browser-free half.

WHY THIS MODULE EXISTS. `tests/e2e/test_dashboard_onboarding.py` drives the
coming-soon dialog by focusing a tile and pressing Enter, then hands the caller
back a `(dialog, key)` pair so a spec can assert `opened_key == key`. The
original helper returned `key or <the locator's own data attribute>` — i.e. the
REQUEST echoed back to itself — so that assertion could never red, whatever the
app did.

It mattered. `dashboard-e2e` redded at `6962fe46f` (#11057) inside
`test_every_coming_soon_tile_captures_its_click` with
`Locator.click: Timeout 30000ms exceeded` waiting for `Register interest`: the
previous tile's deferred focus restore (one `requestAnimationFrame`, the
deliberate deferral in `main.jsx::closeComingSoon`) ran after the harness's
`focus()`, Enter re-opened an ALREADY RECORDED tile, the echoed-mismatch passed
silently, and the spec then waited 30 s for a primary a recorded tile never
renders.

So the observation is split out here, browser-free, the way #10305 split the
readiness gate into `preview_preflight.py` — but the SPLIT ALONE did not make it
cheap, and this docstring used to claim it did. A module with no `test_*`
function collects zero items, and the only test of the mapping sat in
`tests/e2e/test_dashboard_onboarding.py`, which `pytest.skip`s itself at import
unless `RUN_DASHBOARD_E2E=1` — so "exercisable without the opt-in" was false,
and the case only ever ran inside the dashboard-e2e job it was extracted to
avoid.

Its browser-free arm now lives in `tests/test_dialog_tile_key.py`, which the
`core` surface selects and every default python lane runs (#11057 review
ISSUE-1); this module stays the single home of the mapping itself.
"""
from __future__ import annotations

#: The tile aria-label is `<name> <em dash> <state word>` (`tileAriaLabel` in
#: `website/apps/dashboard/src/integrationsGrid.js`): "Slack — coming soon".
#: Named once here so the split cannot drift into a bare hyphen.
ARIA_LABEL_SEPARATOR = "\u2014"


def tile_key_for_dialog_name(name: str, rendered: list) -> str:
    """Map the tile NAME a dialog renders to its integration key.

    `rendered` is `[(key, aria_label), …]` read from the coming-soon tiles ON
    SCREEN — the same DOM the user sees. The dialog renders
    `#integration-modal-body` as `<strong>{tile.name}.</strong> …` (`main.jsx`),
    so its `<strong>` is the one fact that says WHICH tile the app decided to
    open; mapping it back through the rendered tiles is what makes the caller's
    `assert opened_key == key` an observation instead of a tautology.

    A name that matches no rendered tile RAISES. There is deliberately no
    fallback to the caller's request: silence about a dialog nobody can name is
    the exact fail-open this module replaced.
    """
    wanted = (name or "").strip().rstrip(".").strip()
    for tile_key, label in rendered:
        if (label or "").split(ARIA_LABEL_SEPARATOR)[0].strip() == wanted:
            return tile_key
    raise AssertionError(
        f"the open dialog names {wanted!r}, which is no rendered coming-soon tile: {rendered}")
