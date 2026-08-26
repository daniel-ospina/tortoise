"""tests/e2e conftest — #1721: module-scoped playwright chain (root-cause fix).

pytest-playwright's `playwright` fixture is SESSION-scoped. Playwright's
sync API (playwright/sync_api/_context_manager.py __enter__) owns a private
asyncio loop and parks its dispatcher greenlet mid-run_until_complete; while
parked, `loop._running` stays True and asyncio._set_running_loop(loop) is
live on the main thread's thread-local. With a session-scoped fixture the
loop stays "running" from the first page use until SESSION end — so in a
full-suite run (`pytest tests/`, which collects tests/e2e/ early) every later
test that calls asyncio.run() (test_abuse TestTurnstile, test_agent_signup,
...) dies with "asyncio.run() cannot be called from a running event loop" and
every @pytest.mark.asyncio test (test_client_ip_middleware, ...) dies with
"Runner.run() cannot be called from a running event loop" — the order-
dependent cascade of #1721.

sync_playwright().stop() (__exit__) closes the loop and clears the
thread-local running loop, so owning playwright per MODULE bounds the parked
loop to the first e2e module that uses it: after that module finishes, the
main thread is clean and the rest of the suite runs without the cascade.
Fixtures defined in a conftest override plugin fixtures with the same name
for that directory (and below) — the hosted / legal / signup-form e2e suites
keep the plugin's session scope only if they never launch the browser; any
module that does launch is bounded to itself.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import uuid
from collections.abc import Callable, Generator
from typing import Any

import pytest
from playwright.sync_api import Browser, BrowserType, Page, Playwright, sync_playwright

# ── #1721: module-scoped playwright chain (root-cause fix) ─────────────
# pytest-playwright's `playwright` fixture is SESSION-scoped. Playwright's
# sync API (playwright/sync_api/_context_manager.py __enter__) owns a private
# asyncio loop and parks its dispatcher greenlet mid-run_until_complete; while
# parked, `loop._running` stays True and asyncio._set_running_loop(loop) is
# live on the main thread's thread-local. With a session-scoped fixture the
# loop stays "running" from this module's first page use until SESSION end —
# so in a full-suite run (`pytest tests/`, which collects tests/e2e/ early)
# every later test that calls asyncio.run() (test_abuse TestTurnstile,
# test_agent_signup, ...) dies with "asyncio.run() cannot be called from a
# running event loop" and every @pytest.mark.asyncio test (test_client_ip_
# middleware, ...) dies with "Runner.run() cannot be called from a running
# event loop" — the order-dependent cascade of #1721.
#
# sync_playwright().stop() (__exit__) closes the loop and clears the
# thread-local running loop, so owning playwright per MODULE bounds the parked
# loop to this file: after the module finishes, the main thread is clean and
# the rest of the suite runs without the cascade. Fixtures defined in a test
# module override plugin fixtures with the same name for that module only —
# the hosted / legal / signup-form e2e suites keep the plugin's session scope.
# The mirrors below match pytest_playwright's definitions 1:1 (scope module).


@pytest.fixture(scope="module")
def playwright() -> Generator[Playwright, None, None]:
    # Guard (review P1, #1721): if an EARLIER opt-in e2e module
    # (legal/signup/dashboard/hosted with RUN_LEGAL_E2E=1 etc.) already
    # parked its SESSION-scoped playwright loop in this thread, the sync
    # API's __enter__ would raise "Playwright Sync API inside the asyncio
    # loop" — skip instead of erroring. In the default suite welcome is the
    # only playwright user and runs first, so the normal path is taken.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pw = sync_playwright().start()
        yield pw
        pw.stop()
        return
    pytest.skip(
        "a session-scoped playwright loop is already parked in this thread "
        "(combined opt-in e2e run) — run tests/e2e/test_welcome_page.py "
        "separately (#1721)"
    )


@pytest.fixture(scope="module")
def browser_type(playwright: Playwright, browser_name: str) -> BrowserType:
    return getattr(playwright, browser_name)


@pytest.fixture(scope="module")
def connect_options() -> dict | None:
    return None


@pytest.fixture(scope="module")
def launch_browser(
    browser_type_launch_args: dict[str, Any],
    browser_type: BrowserType,
    connect_options: dict | None,
) -> Callable[..., Browser]:
    def launch(**kwargs: dict[str, Any]) -> Browser:
        launch_options = {**browser_type_launch_args, **kwargs}
        if connect_options:
            browser = browser_type.connect(
                **(
                    {
                        **connect_options,
                        "headers": {
                            "x-playwright-launch-options": json.dumps(launch_options),
                            **(connect_options.get("headers") or {}),
                        },
                    }
                )
            )
        else:
            browser = browser_type.launch(**launch_options)
        return browser

    return launch


@pytest.fixture(scope="module")
def browser(launch_browser: Callable[..., Browser]) -> Generator[Browser, None, None]:
    browser = launch_browser()
    yield browser
    browser.close()
