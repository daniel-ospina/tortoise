"""#11057 — the BROWSER-FREE arm of "which tile did that dialog actually open".

``tests/e2e/dialog_tile_key.py`` extracts the coming-soon dialog observation out
of the heavyweight dashboard-e2e spec so it can be exercised cheaply. Its
docstring said that split let the mapping be tested *without*
``RUN_DASHBOARD_E2E=1``, the two wrangler previews and a browser — and until
this file existed that claim was FALSE (review ISSUE-1, P2): the only test of the
mapping lived in ``tests/e2e/test_dashboard_onboarding.py``, which executes

    pytest.skip("dashboard e2e: opt-in via RUN_DASHBOARD_E2E=1", allow_module_level=True)

at import time, so ``pytest tests/e2e/test_dashboard_onboarding.py -k
dialog_tile_key`` reported ``1 skipped`` and never executed a single assertion,
while ``tests/e2e/dialog_tile_key.py`` itself defines no ``test_*`` and collects
zero items. The "cheap arm" only ever ran inside the expensive dashboard-e2e job.

So the cases MOVED here, where ``core`` selects them and a default python lane
runs them, and the helper module's docstring now points at this file.

What they pin (the defect): the original helper returned
``key or <the locator's own data attribute>`` — the REQUEST echoed back to
itself — so a spec's ``assert opened_key == key`` could never red, whatever the
app did. ``dashboard-e2e`` redded at ``6962fe46f`` because of exactly that: the
previous tile's deferred focus restore ran after the harness's ``focus()``,
Enter re-opened an ALREADY RECORDED tile, the echoed-mismatch passed silently,
and the spec then waited 30s for a primary a recorded tile never renders.

Run with (stub-only, imports no browser and no database):
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
    tests/test_dialog_tile_key.py -q -p no:cacheprovider
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tests.e2e.dialog_tile_key import ARIA_LABEL_SEPARATOR, tile_key_for_dialog_name

#: The tiles on screen for every case below, as the spec reads them from the
#: DOM: ``[(data-integration-key, aria-label), …]``. `tileAriaLabel` in
#: `website/apps/dashboard/src/integrationsGrid.js` renders
#: ``<name> <em dash> <state word>``.
RENDERED = [
    ("supabase_org", "Supabase \u2014 coming soon"),
    ("slack", "Slack \u2014 coming soon"),
    ("linear", "Linear \u2014 coming soon"),
]


def _pre_fix_echo(requested_key):
    """The helper this module replaces — kept ONLY to prove the cases are not vacuous.

    Pre-fix (``tests/e2e/test_dashboard_onboarding.py`` before 8f3b27e82)::

        tile_key = key or tile.get_attribute("data-integration-key")

    It never looked at the dialog, so it returns whatever it was ASKED for: the
    request compared with itself. `test_every_case_reds_against_the_pre_fix_echo_helper`
    runs each assertion below through this function and requires it to FAIL —
    which is what makes the suite non-vacuous rather than merely green.
    """
    return requested_key


def test_the_dialog_decides_the_key_not_the_request() -> None:
    """THE CASE THE OLD HELPER COULD NOT SEE: caller asked `linear`, dialog says Slack."""
    # the dialog's `<strong>{tile.name}.</strong>` — sentence punctuation included
    assert tile_key_for_dialog_name("Slack.", RENDERED) == "slack"
    # Observed, that is a mismatch; echoed, it was an equality.
    assert tile_key_for_dialog_name("Slack.", RENDERED) != "linear"


def test_an_unnameable_dialog_raises_instead_of_echoing_the_request() -> None:
    """A name that is no rendered tile is a RED, never a fall-through to the request."""
    with pytest.raises(AssertionError):
        tile_key_for_dialog_name("Notion.", RENDERED)


@pytest.mark.parametrize("name", ["Slack.", "Slack", "  Slack.  ", "Slack ."])
def test_the_name_a_dialog_renders_maps_to_its_own_tile(name: str) -> None:
    """Whitespace and the sentence full stop are normalised; the KEY is the tile's."""
    assert tile_key_for_dialog_name(name, RENDERED) == "slack"


def test_a_bare_hyphen_is_not_the_label_separator() -> None:
    """The separator is the em dash `tileAriaLabel` renders — a hyphen must not match.

    Named once (`ARIA_LABEL_SEPARATOR`) so the split cannot drift into a bare
    hyphen; a hyphenated label leaves the whole string, which is no tile NAME,
    and therefore raises rather than silently mapping to the first tile.
    """
    assert ARIA_LABEL_SEPARATOR == "\u2014"
    with pytest.raises(AssertionError):
        tile_key_for_dialog_name("Slack", [("slack", "Slack - coming soon")])


def test_the_mapping_module_is_browser_free_and_opt_in_free() -> None:
    """The "cheap arm" claim, pinned: no opt-in guard, no browser in the module.

    Re-adding `RUN_DASHBOARD_E2E` (or a module-level `pytest.skip`, or a
    playwright import) to `tests/e2e/dialog_tile_key.py` is how this defect
    comes back — this file would keep passing while running nothing, which is
    the inversion ISSUE-1 named.
    """
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tests", "e2e", "dialog_tile_key.py")
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    assert "allow_module_level" not in source
    assert "playwright" not in source
    # no environment read at all: the mapping takes its facts as arguments, so
    # an opt-in guard has nothing to read (prose mentions of the dashboard
    # spec's flag are fine — a `os.environ` gate is not).
    assert "os.environ" not in source
    # …and it really is the function, not a copy renamed on the way out.
    assert tile_key_for_dialog_name("Linear.", RENDERED) == "linear"


def test_every_case_reds_against_the_pre_fix_echo_helper() -> None:
    """NON-VACUITY, executed rather than asserted: the old shape fails these cases.

    Each lambda is the same assertion the case above makes, re-expressed against
    `_pre_fix_echo`. If any of them PASSES here, the corresponding case would
    have passed on the pre-fix code and would be pinning nothing.
    """
    pre_fix_cases = {
        "the dialog decides the key, not the request":
            lambda: _pre_fix_echo("linear") == "slack",
        "an unnameable dialog raises":
            lambda: _raises_assertion(lambda: _pre_fix_echo("Notion.")),
        "the rendered name maps to its own tile":
            lambda: _pre_fix_echo("Slack.") == "slack",
        "a bare hyphen is not the separator":
            lambda: _raises_assertion(lambda: _pre_fix_echo("Slack")),
    }
    vacuous = []
    for label, case in pre_fix_cases.items():
        try:
            passed = case()
        except AssertionError:
            passed = False
        if passed:
            vacuous.append(label)
    assert not vacuous, (
        "these cases would ALSO pass on the pre-fix echo helper, so they pin "
        f"nothing: {vacuous}")


def _raises_assertion(call) -> bool:
    """True when `call` raised AssertionError — i.e. the case behaved as required."""
    try:
        call()
    except AssertionError:
        return True
    return False
