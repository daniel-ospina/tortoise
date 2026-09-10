"""TEMPORARY screenshot harness for the onboarding connect step (local only)."""
from __future__ import annotations

import os

import pytest
from playwright.sync_api import Page

if not os.environ.get("RUN_DASHBOARD_E2E"):
    pytest.skip("screenshot harness: opt-in via RUN_DASHBOARD_E2E=1", allow_module_level=True)

from tests.e2e.test_dashboard_onboarding import (  # noqa: E402
    _seed_cookie, _wire, _walk_to_connect,
)

OUT = "/tmp/wizard-shots"


@pytest.fixture(scope="module", autouse=True)
def _preview():
    from tests.e2e.test_session_login_flow import _preflight_local_servers
    _preflight_local_servers()


def _shots(page: Page, tag: str, width: int) -> None:
    page.set_viewport_size({"width": width, "height": 1400})
    page.wait_for_timeout(500)
    names = page.locator(".harness-tab").all_inner_texts()
    page.screenshot(path=f"{OUT}/{tag}-00-initial-{width}.png", full_page=True)
    for i, name in enumerate(names):
        page.get_by_role("button", name=name, exact=True).first.click()
        page.wait_for_timeout(350)
        safe = name.replace(" ", "-").lower()
        page.screenshot(path=f"{OUT}/{tag}-{i + 1:02d}-{safe}-{width}.png", full_page=True)
    # codex desktop surface
    page.get_by_role("button", name="Codex", exact=True).first.click()
    page.wait_for_timeout(250)
    page.get_by_role("button", name="Desktop (no terminal)").click()
    page.wait_for_timeout(350)
    page.screenshot(path=f"{OUT}/{tag}-99-codex-desktop-{width}.png", full_page=True)


def test_shot_owner_no_key(page: Page) -> None:
    os.makedirs(OUT, exist_ok=True)
    _seed_cookie(page, "u-shot-nokey")
    _wire(page, role="owner")
    _walk_to_connect(page)
    page.screenshot(path=f"{OUT}/nokey-initial-1200.png", full_page=True)


def test_shot_owner_with_key(page: Page) -> None:
    os.makedirs(OUT, exist_ok=True)
    _seed_cookie(page, "u-shot-key")
    cap = _wire(page, role="owner")
    _walk_to_connect(page)
    page.get_by_role("button", name="Create an API key").click()
    page.wait_for_timeout(600)
    _shots(page, "withkey", 1200)
    _shots(page, "withkey", 390)
    assert cap is not None
