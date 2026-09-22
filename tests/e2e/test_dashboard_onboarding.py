"""#1997 (W1) onboarding wizard e2e (RUN_DASHBOARD_E2E opt-in, two-origin harness).

Journey coverage: the 4 HUMAN wizard steps (org-create →
fork card → connect-consent → done), the fork checkpoint (self + build;
the build pick records NO catalog-presented step — #3913), the connect step's
key affordances, and the re-entry card.
The done step exits WITHOUT patching onboarding_complete (accept-and-drop).

#2710 / #2711 / #2755 / #2756 (2026-09-09 connect-step fixes) brought this spec
to the SHIPPED post-#2698 UI and added the owner/admin connect branch:
  - the connect step has 4 harness FAMILIES (Claude / Codex / Cursor / Pi)
    with a second-level SURFACE row (#2912): Claude → Code / Desktop / Web,
    Codex → CLI / Desktop. ChatGPT is key-less OAuth and is filtered OUT of
    the wizard chooser (#2698);
  - the dead pre-#2698 "Copy setup" button is gone — setup content is a
    WizardPromptCard with its own Copy control;
  - an owner/admin who lands on the connect step with no in-memory key gets an
    in-flow mint CTA (never-expiring) + a paste escape, and leaving the wizard
    must NOT pop the shared key-create modal (either exit path); #3783: when the
    org ALREADY holds a usable durable key (its plaintext is shown once and is
    not in memory), the step offers that existing key instead of the mint CTA —
    minting a second there spent the free tier's whole key allowance;
  - #2865: the connect chooser is no longer owner/admin-only — a member with
    no key reaches the key-less OAuth Claude Desktop/Web leaves (asserted in
    `test_member_without_key_reaches_keyless_claude_connectors`); on a KEYED
    leaf a member still gets the paste escape only (never the mint CTA, which
    would 403 server-side);
  - the shown-once key row must not overflow at any phone width;
  - WizardPromptCard must not nest interactive elements, and a drag-select
    inside it must not overwrite the clipboard;
  - Codex has a CLI/Desktop surface toggle and Desktop reaches
    ~/.codex/config.toml.

Test-review hardening (2026-09-10): mock `role`/`mint_status`/`key_rows` cover
the paste REFUSAL paths and the 402 mint cap (a rejected paste must never
advance, and a capped mint must keep the paste escape), the mint-free exit is
asserted for BOTH wizard exits, the connect step's own advance is exercised and
now proves it writes NO `harness-connected` checkpoint (#3428/#2937 — the
assertion is the opposite of the one this line used to index), the drag-select
check proves a selection was
actually made, the clipboard assert compares against the card's own text, the
390px check is bracketed across widths with an over-long token and covers the
build fork's FOURTH key row, and the catalog check is backed by a registry-only
mock name (not the offline fallback).

**CI lane:** this spec is opt-in (`RUN_DASHBOARD_E2E`) and, since #4221, IS
wired into `.github/workflows/ci.yml` — the `dashboard-e2e` job's step runs it
alongside `test_keys_table_mixed.py` / `test_graphs_management.py` /
`test_ship_test_onboarding.py`, against the same two-origin harness. Run it
locally against a fresh `dist/` whenever the connect step changes.
"""
from __future__ import annotations

import json
import os
import re
import time

import pytest
from playwright.sync_api import Page, expect

if not os.environ.get("RUN_DASHBOARD_E2E"):
    pytest.skip("dashboard e2e: opt-in via RUN_DASHBOARD_E2E=1", allow_module_level=True)

from tests.e2e.test_session_login_flow import (
    APP_HOST,
    AUTH_HOST,
    DASHBOARD_URL,
    _bff_path,
    _goto_local_dashboard,
    _is_bff_api,
    _preflight_local_servers,
    _proxy_body,
    _seed_local_session_cookie,
)


@pytest.fixture(scope="module", autouse=True)
def _local_preview_servers() -> None:
    """#2744: fail fast (one clear error) when :8788/:8790 are not serving."""
    _preflight_local_servers()

# #2698: ChatGPT is key-less OAuth — deliberately NOT a wizard harness choice.
# #2912: level 1 = the harness family, level 2 = the surface (only Claude and
# Codex have one).
WIZARD_HARNESS_FAMILIES = ["Claude", "Codex", "Cursor", "Pi"]
WIZARD_CLAUDE_SURFACES = ["Claude Code", "Claude Desktop", "Claude Web"]
WIZARD_CODEX_SURFACES = ["Codex CLI", "Codex Desktop"]

MINTED_KEY = "tt_minted_0123456789abcdef0123456789abcdef"
# #2711: a realistic worst case for the unbreakable token (72 chars, no
# separators) — the pre-fix row pushed Copy off-screen at 390px.
LONG_MINTED_KEY = "tt_" + "0123456789abcdef" * 4 + "beef"
PASTED_KEY = "tt_connect_abcdef0123456789"
PASTED_KEY_2 = "tt_connect_fedcba9876543210"
# A never-expiring durable row matching PASTED_KEY (durableConnectKey resolves
# the pasted plaintext against the org's key rows — an unknown key is refused).
DURABLE_ROW = {"id": "k-paste", "name": "agent key", "key_prefix": PASTED_KEY[:10],
               "created_via": "dashboard", "enabled": True, "expires_at": None,
               "revoked_at": None}


def _row(prefix: str, **over) -> dict:
    """A key row matching `prefix`, overridable for the refusal cases."""
    row = {"id": f"k-{prefix}", "name": "agent key", "key_prefix": prefix[:10],
           "created_via": "dashboard", "enabled": True, "expires_at": None,
           "revoked_at": None}
    row.update(over)
    return row


def _session(user_id: str) -> dict:
    return {"access_token": "fake.eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.sig",
            "refresh_token": "rt", "expires_in": 3600,
            "expires_at": int(time.time()) + 3600, "token_type": "bearer",
            "user": {"id": user_id, "email": f"{user_id}@premise-labs.dev",
                     "user_metadata": {"display_name": "Onboarding Test"}}}


def _seed_cookie(page: Page, user_id: str) -> None:
    # #2744: seed BOTH the loopback (what the local preview reads) and the
    # prod parent-domain cookie (session-coherent intercepted redirects).
    _seed_local_session_cookie(page, user_id, _session(user_id))


def _wire(page: Page, *, seed_objects: list = None,  # noqa: RUF013
          role: str | None = None, key_rows: list | None = None,
          minted_key: str = MINTED_KEY, mint_status: int = 200,
          mint_error_detail: str = "API key cap reached",
          onboarding_projection: dict | None = None) -> dict:
    """Route harness: the API mocks for the wizard journey. Returns the
    capture dict ({objects, points, state_patches, org_create, checkpoint,
    mint}).

    `role` mirrors the /v1/organizations membership role — the connect step branches on
    it (owner/admin → harness tabs + prompt cards; member → paste escape only).
    Omitting it (the pre-#2710 shape) is the MEMBER path.
    `key_rows` is the org's key table — durableConnectKey resolves a pasted
    plaintext against it (so a refusal case is just a differently-shaped row).
    `mint_status` fails POST /v1/team/keys (402 = the tier cap, the only
    reachable non-transport mint failure).
    `onboarding_projection` serves `GET /v1/onboarding/state` (default: none —
    the pre-existing 401 fall-through, so `serverHarnessConnected` stays false).
    #3428/#2937 (lane B3, review cycle 2 P2-2): the wizard reads `st.onboarding`,
    so this is the runtime seam that makes the Connected screen reachable — it
    turns the capture-tense gating from source-pinned into runtime-proven.
    """
    cap = {"objects": [], "points": [], "state": [], "org_create": [],
           "checkpoint": [], "mint": [], "capabilities": 0}
    seed_objects = seed_objects or [{"id": "obj-1", "name": "Onboarding Test", "objectKind": "project", "status": "in_progress"}]
    rows = key_rows if key_rows is not None else []

    org_row = {"org_id": "team_o", "name": "Onboarding Test"}
    if role is not None:
        org_row["role"] = role

    def handle(route):
        url = route.request.url
        method = route.request.method
        # #1828: loadAll pins ?org_id= on overview reads — match on the
        # query-stripped path so /v1/team/keys?org_id=… still resolves.
        path = _bff_path(url)
        if _is_bff_api(url):
            if path.endswith("/v1/organizations") and method == "GET":
                # #2494: the mock's org row must carry the name in the field the
                # dashboard reads (`org_name`) — otherwise the account menu's
                # organization row renders "No organization".
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([{**org_row, "org_name": "Onboarding Test"}]))
                return
            if path.endswith("/v1/team/keys") and method == "POST":
                # #2710: the wizard's mint CTA rides this endpoint. The body is
                # captured so the journey can assert it carries NO expires_in
                # (Never-only embed contract, #2426 decision 2).
                cap["mint"].append(json.loads(route.request.post_data or "{}"))
                if mint_status != 200:
                    route.fulfill(status=mint_status, content_type="application/json",
                                  body=json.dumps({"detail": mint_error_detail}))
                    return
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"id": "k-mint", "key": minted_key,
                                               "api_key": minted_key,
                                               "expires_at": None,
                                               "created_via": "dashboard"}))
                return
            if path.endswith("/v1/session/key") and method == "POST":
                # #2167: the mount NEVER mints a bootstrap key (the old
                # tt_onb_key mint mock is gone) — loud 500 + counter so a
                # regression mint fails the journey instead of silently
                # passing the wizard walk.
                cap["session_key_posts"] = cap.get("session_key_posts", 0) + 1
                route.fulfill(status=500, content_type="application/json",
                              body=json.dumps({"detail": "#2167 zero-mint tripwire"}))
                return
            if path.endswith("/v1/team") or path.endswith("/v1/team/"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({**org_row, "tier": "free", "graph_ready": True,
                                               "point_count": 0,
                                               "subscription_status": "active",
                                               "checkout_price_ids": {"solo": "price_solo", "pro": "price_pro", "team": "price_team"},
                                               "write_ops_limit": 1000, "write_ops_used": 0}))
                return
            if path.endswith("/v1/capabilities") and method == "GET":
                # #2004 (W8): the registry-backed build catalog. The mock
                # returns a module name that exists ONLY here, so the fork test
                # can tell a registry-backed render from the static offline
                # fallback (wizardFlow.js BUILD_CATALOG_PLACEHOLDER).
                # #2763: the payload currently CANNOT reach the card (fetch is
                # gated on wizardStep 2, the catalog renders on wizardStep 1),
                # so the test pins the shipped placeholder render + the fetch
                # count and will flip when #2763 is fixed.
                cap["capabilities"] += 1
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"modules": [
                                  {"name": "Registry-only module", "kind": "extractor",
                                   "description": "present only in the mocked registry"}]}))
                return
            if path.endswith("/v1/sessions") or path.endswith("/v1/team/keys") or path.endswith("/backups"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"sessions": [], "keys": rows, "backups": []}))
                return
            if path.endswith("/v1/objects") and method == "POST":
                body = json.loads(route.request.post_data or "{}")
                cap["objects"].append(body)
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(seed_objects[0]))
                return
            if path.endswith("/v1/points") and method == "POST":
                body = json.loads(route.request.post_data or "{}")
                cap["points"].append(body)
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"id": "point-1", "content": body.get("content", "")}))
                return
            if path.endswith("/v1/onboarding/state") and method == "GET":
                # #3428/#2937 (lane B3, review cycle 2 P2-2): the runtime seam
                # for the Connected screen. The app reads `st.onboarding`, so
                # the projection must be WRAPPED — a bare projection would be
                # silently ignored and the branch would stay unreachable.
                if onboarding_projection is None:
                    route.fulfill(status=401, content_type="application/json", body="{}")
                    return
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"onboarding": onboarding_projection}))
                return
            if path.endswith("/v1/onboarding/state") and method == "PATCH":
                cap["state"].append(json.loads(route.request.post_data or "{}"))
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"onboarding_complete": True}))
                return
            # #1997 (W1): a RETURNING user's org-create step used to post to
            # /v1/onboarding/team (one-shot team_created → 409 → advance).
            # #2323 (Option B): org-holding accounts now see a read-only
            # step — this stub stays as a loud tripwire (cap['org_create']
            # must stay EMPTY on the journey) rather than a silent 409.
            if path.endswith("/v1/onboarding/team") and method == "POST":
                cap["org_create"].append(json.loads(route.request.post_data or "{}"))
                route.fulfill(status=409, content_type="application/json",
                              body=json.dumps({"detail": "Sub-team already created"}))
                return
            # #1997 (W1), revised by #3913: the fork checkpoint is the ONLY
            # client write — `catalog-presented` must never appear here (the
            # assertions below pin exactly that).
            if path.endswith("/v1/onboarding/state/checkpoint") and method == "POST":
                body = json.loads(route.request.post_data or "{}")
                cap["checkpoint"].append(body)
                fork = body.get("fork")
                onboarding = {"fork": fork, "status": "active",
                              "onboarding_complete": False, "completed_steps": []}
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"created_steps": [], "noop_steps": [],
                                                "onboarding": onboarding}))
                return
            if path.endswith("/v1/onboarding/github/connect") and method == "POST":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"auth_url": "https://github.com/login/oauth/authorize?fake"}))
                return
            if path.endswith("/v1/onboarding/github/status"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"connected": False, "repos_count": None}))
                return
            route.fulfill(status=401, content_type="application/json", body="{}")
            return
        if url.startswith(AUTH_HOST):
            from tests.e2e.test_session_login_flow import AUTH_ORIGIN
            _proxy_body(route, AUTH_ORIGIN + url[len(AUTH_HOST):], page)
            return
        if url.startswith(APP_HOST):
            local = DASHBOARD_URL.rstrip("/") + url[len(APP_HOST):]
            ctype = "application/javascript" if local.endswith(".js") else ("text/css" if local.endswith(".css") else "text/html")
            resp = page.request.get(local)
            route.fulfill(status=resp.status, content_type=ctype, body=resp.body())
            return
        route.continue_()

    page.route("**/*", handle)
    return cap


def _walk_to_fork(page: Page) -> None:
    """Re-entry → step 0 (read-only org summary) → the fork card."""
    # #2744: the DOCUMENT always loads from the local built-dist preview.
    _goto_local_dashboard(page)
    expect(page.locator("body")).to_contain_text("Continue setup", timeout=20_000)
    page.get_by_role("button", name="Continue setup").click()
    # STEP 0: create/join org — an account that already holds an org sees a
    # read-only summary (never a second mint, #2323) and advances. #2912: the
    # header h1 names the stage — 'Your Organization' for this read-only state.
    # #2364 round-1 (kept through the merge): the org-holder step must never
    # re-read the org-create title, wherever it renders.
    expect(page.locator(".welcome-title")).to_have_text("Your Organization", timeout=10_000)
    expect(page.locator("body")).not_to_contain_text("Create your Organization", timeout=10_000)
    expect(page.locator("body")).to_contain_text("You're set up in", timeout=5_000)
    page.get_by_role("button", name="Continue →").click()
    # STEP 1: fork card (was step 2 before orientation removal).
    expect(page.locator("body")).to_contain_text("Choose how you'll use Tortoise", timeout=10_000)


def _walk_to_connect(page: Page) -> None:
    """The shared prefix of every journey: re-entry → step 0 → fork SELF →
    connect. The account already holds an org (_wire's team_row)."""
    _walk_to_fork(page)
    # #3218: the self-fork option is first-person now.
    page.get_by_role("button", name="For my internal setup").click()
    expect(page.locator("body")).to_contain_text("Connect your agent", timeout=10_000)


def _measure_key_row(page: Page) -> dict:
    """Measure the rendered shown-once key row: document overflow, the row's
    own overflow, and whether the Copy button is inside the viewport."""
    return page.evaluate("""() => {
      const code = [...document.querySelectorAll('code')].find(c => (c.textContent||'').trim().startsWith('tt_'));
      const row = code ? code.parentElement : null;
      const btn = row ? row.querySelector('button') : null;
      const b = btn ? btn.getBoundingClientRect() : null;
      return { scrollWidth: document.documentElement.scrollWidth,
               clientWidth: document.documentElement.clientWidth,
               rowOverflow: row ? row.scrollWidth - row.clientWidth : null,
               codeWidth: code ? Math.round(code.getBoundingClientRect().width) : null,
               viewport: window.innerWidth,
               copyBtnRight: b ? b.right : null };
    }""")


def _wait_until(fn, *, timeout_ms: int = 5_000, message: str = "") -> None:
    """Bounded poll (the sync Playwright API's expect.poll is unavailable in
    this env) — replaces instantaneous reads that can race a React effect."""
    deadline = time.time() + timeout_ms / 1000
    last = None
    while time.time() < deadline:
        last = fn()
        if last:
            return
        time.sleep(0.1)
    raise AssertionError(f"timed out after {timeout_ms}ms waiting for {message}: {last!r}")


def _mint_from_connect(page: Page) -> None:
    """#2710: the documented connect-step path — mint from inside the wizard."""
    page.get_by_role("button", name="Create an API key").click()
    expect(page.locator(".wizard-prompt-card")).to_be_visible(timeout=10_000)


def _expect_left_wizard(page: Page) -> None:
    """Wait for the wizard TREE to be gone AND the dashboard to actually mount
    (a [data-tab=keys] nav button) — a stray queued modal is opened by a
    dashboard effect one tick after the wizard unmounts, so the absence check
    must not be an instantaneous snapshot."""
    expect(page.locator(".wizard-prompt-card")).to_have_count(0, timeout=15_000)
    expect(page.get_by_text("You're all set")).to_have_count(0, timeout=15_000)
    expect(page.locator(".harness-family")).to_have_count(0, timeout=15_000)
    expect(page.locator("[data-tab=keys]")).to_be_visible(timeout=15_000)
    page.wait_for_timeout(400)  # bounded: one settle tick after the mount


def test_first_timer_wizard_human_steps(page: Page) -> None:
    """#1997 (W1) + #2323 (Option B): a returning-style session (team
    exists, empty graph) walks the NEW 4 HUMAN steps (epic plan P1):
    org-create/join → fork card → connect-consent → done.
    The org-create step is a READ-ONLY summary for accounts that already
    hold an org — it NEVER mints a second org (cap['org_create'] stays
    empty). The done step exits WITHOUT patching onboarding_complete
    (accept-and-drop: the node's fork-aware gate owns completion).

    #2710/#2865: this journey is the MEMBER row (no role). #2865 removed the
    owner/admin-only gate on the connect chooser, so a member now REACHES the
    harness families; the member's key-LESS OAuth journey is asserted in
    `test_member_without_key_reaches_keyless_claude_connectors`. This journey
    lands on the DEFAULT leaf (Claude Code, a KEYED leaf), where a member still
    gets the paste escape ONLY — never the owner/admin mint CTA, which would
    403 server-side."""
    _seed_cookie(page, "u-onb")
    cap = _wire(page, key_rows=[DURABLE_ROW])
    _walk_to_connect(page)
    # #2865: the chooser is no longer owner/admin-only — a member reaches the 4
    # harness families (the OAuth leaves need no mint).
    assert page.locator(".harness-family").count() == 4, \
        "members must reach the harness chooser (#2865 removed the role gate)"
    # #2912 (test-review P2): the member LEDE, measured rather than inferred.
    # The default Claude Code leaf is KEYED, so the member paste lede stays.
    # "Pick which harness to connect." would be a lie here, and the string the
    # issue reported as vague must not come back.
    expect(page.locator(".welcome-lede")).to_have_text(
        "Paste an API key to connect your agent.", timeout=10_000)
    assert "Pick which harness to connect" not in page.locator(".welcome-lede").inner_text()
    assert "Connect Tortoise to your Organization" not in page.locator(".welcome-head").inner_text()
    # The mint CTA is owner/admin render-gated (POST /v1/team/keys is
    # _require_owner_admin server-side) — a member must never see a 403 button,
    # and on a KEYED leaf gets the paste row directly instead.
    assert page.get_by_role("button", name="Create an API key").count() == 0, \
        "members must not see the owner/admin mint CTA"
    expect(page.get_by_role("button", name="Use this key")).to_be_visible(timeout=5_000)
    page.get_by_label("Paste an API key").fill(PASTED_KEY)
    page.get_by_role("button", name="Use this key").click()
    # The pasted durable key is accepted (row-truth match) → the keyed leaf now
    # renders its setup prompt (procedure step 2) and the wizard can advance.
    expect(page.locator(".wizard-prompt-card").first).to_be_visible(timeout=5_000)
    expect(page.get_by_role("button", name="I've set it up — Continue →")).to_be_visible(timeout=5_000)
    page.get_by_role("button", name="Skip for now").click()
    # STEP 3: done — the wizard REPORTS what the server observed; NO
    # `harness-connected` write and NO onboarding_complete PATCH (the node's
    # gate owns completion; accept-and-drop).
    # #2912 (PR-gate UX): skipping = the PAUSED state, so the <h1> names that
    # state. It used to read "You're all set" — the opposite — directly above
    # the not-connected body. review cycle 6 (item 2): the paused wording is the
    # OBSERVATION phrasing on both forks — the categorical "your agent is not
    # connected yet" is false for a captured session.
    expect(page.locator(".welcome-title")).to_have_text(
        "Setup paused — no connection observed yet", timeout=10_000)
    # #3428/#2937 (lane B3, review cycle 1 P1-2): the old paused body
    # ("…your agent isn't connected yet") was deleted with the human writer;
    # retarget the surviving copy on the VISIBLE final screen.
    # #3428 (lane B3, review cycle 2 P0-1): scope to `div.done` — the wizard
    # progress crumbs render `<span class="wizard-step done">` per completed
    # step, so the bare `.done` selector is ambiguous (strict-mode violation).
    expect(page.locator("div.done")).to_contain_text("We haven't seen your agent's first write through its Tortoise tools yet", timeout=10_000)
    # the done step's exit (wizardComplete) — scoped: the header carries its own
    # exit ("Open my dashboard →"). No longer a same-named twin — review cycle 1
    # (P2-6): the done button was renamed to "Go to dashboard".
    page.locator(".wizard-actions").get_by_role("button", name="Go to dashboard").click()

    # #2494: account menu expansion — after the dashboard loads ("Overview loaded"
    # status), click the account menu trigger and verify both labeled sections
    # (Personal Account with Log out, Organization with org name).
    expect(page.locator("body")).to_contain_text("Overview loaded", timeout=10_000)
    page.get_by_role("button", name=re.compile(r"^Account menu — .*")).click()
    account_menu = page.locator('.account-menu')
    expect(account_menu).to_be_visible(timeout=5_000)
    # Personal Account section: identity, Profile button, Log out button.
    expect(account_menu).to_contain_text("Personal Account")
    expect(account_menu.get_by_role("button", name="Profile")).to_be_visible()
    expect(account_menu.locator('.account-menu-logout')).to_be_visible()
    expect(account_menu.locator('.account-menu-logout')).to_contain_text("Log out")
    # Divider between sections.
    expect(account_menu.locator('.account-menu-divider')).to_be_visible()
    # Organization section: org name, create button.
    expect(account_menu).to_contain_text("Organization")
    expect(account_menu).to_contain_text("Onboarding Test")
    expect(account_menu.get_by_role("button", name=re.compile(r"Create new organization"))).to_be_visible()
    # Close the menu (Escape key).
    page.locator('[aria-label^="Account menu —"]').press("Escape")
    expect(account_menu).not_to_be_visible()

    assert not any("onboarding_complete" in p for p in cap["state"]), \
        f"done step must NOT patch onboarding_complete: {cap['state']}"
    # #2323: an org-holding journey NEVER mints a second org through the
    # wizard org-create step.
    assert cap["org_create"] == [], f"#2323 violated: org_create fired: {cap['org_create']}"
    # #2167: the whole wizard journey issues ZERO POST /v1/session/key (the
    # mount mint is deleted; connect-step durable sourcing is #2211-owned and
    # rides POST /v1/team/keys)
    assert cap.get("session_key_posts", 0) == 0, f"zero-mint violated: {cap.get('session_key_posts')} session-key POSTs"


def test_owner_connect_step_mints_never_expiring_key_in_flow(page: Page) -> None:
    """#2710: an owner/admin with NO in-memory key must be able to complete the
    connect step from inside the wizard.

    Pre-fix: the step showed the dead sentence "Create an API key to see the
    setup prompt." with no control, pill clicks were no-ops, and the queued
    auto-open modal then popped a stray "Create new API key" (30-day default)
    on the dashboard after exit. Now: the shipped two-level harness chooser
    renders, the mint CTA mints a NEVER-expiring key (the connect step's own
    Never-only contract), the prompt card renders, the step's OWN advance writes
    NO harness-connected checkpoint (the human writer is deleted — #3428/#2937;
    the step now reports the server projection), and the HEADER exit leaks no
    modal."""
    _seed_cookie(page, "u-owner")
    cap = _wire(page, role="owner")
    _walk_to_connect(page)

    # #2698/#2912 shipped chooser: 4 families at the top level, ChatGPT
    # (key-less OAuth) filtered out.
    family_names = page.locator(".harness-family").all_inner_texts()
    assert family_names == WIZARD_HARNESS_FAMILIES, f"connect-step family row drifted: {family_names}"
    # Level 2: Claude expands to its three surfaces; the default is Claude Code.
    surface_names = page.locator(".harness-surface").all_inner_texts()
    assert [s.split("\n")[0] for s in surface_names] == WIZARD_CLAUDE_SURFACES, \
        f"the Claude family must offer its three surfaces: {surface_names}"

    # #2912: KEY FIRST — the key block is step 1 and the procedure is step 2,
    # so the setup prompt is never offered before a key exists. With no key the
    # procedure block is NOT rendered at all (a "2 Copy the setup prompt"
    # heading promised a prompt that did not exist — the reported defect 2).
    expect(page.locator(".wizard-block-title")).to_have_count(1)
    assert page.locator(".wizard-block-title").all_inner_texts()[0].endswith("Get your API key")
    assert "Copy the setup prompt" not in page.locator(".wizard-block-title").all_inner_texts()[0]
    assert page.locator(".wizard-prompt-card").count() == 0, \
        "no prompt card may render before a key exists"

    # The promised path exists: the sentence AND its affordance.
    expect(page.locator("body")).to_contain_text("Create an API key to see the setup prompt.")
    expect(page.get_by_role("button", name="Create an API key")).to_be_visible()

    _mint_from_connect(page)
    # #2912: the key-mode pills live in the KEY block, so they appear only once
    # a key exists (there is nothing to include/separate before that). Pill
    # clicks are display-mode toggles — assert the CLICK DID ITS JOB (the mode
    # actually switched) rather than a dialog count that cannot change inside
    # the wizard tree (test-review P2: that assertion was vacuous).
    separate_pill = page.get_by_role("button", name="Key separate from prompt")
    included_pill = page.get_by_role("button", name="Key included in prompt")
    expect(separate_pill).to_be_visible(timeout=5_000)
    separate_pill.click()
    assert "active" in (separate_pill.get_attribute("class") or ""), \
        "the 'separate' pill must become the selected mode (pure toggle, no modal)"
    assert "active" not in (included_pill.get_attribute("class") or "")
    # back to the default mode so the prompt embeds the key (the documented
    # "easiest" path); both pill states are pure toggles.
    included_pill.click()
    assert "active" in (included_pill.get_attribute("class") or "")
    # The wizard mint is Never-only: no expires_in on the wire.
    assert len(cap["mint"]) == 1, f"expected ONE wizard mint, got {cap['mint']}"
    body = cap["mint"][0]
    assert "expires_in" not in body, \
        f"the wizard connect mint must never expire (no expires_in): {body}"
    assert body.get("name"), "the wizard mint names the key row (distinguishable rows, #2325)"
    # The prompt card carries the minted plaintext (shown once, in-memory only).
    card = page.locator(".wizard-prompt-card").first
    expect(card).to_be_visible()
    assert MINTED_KEY in card.inner_text(), "the minted key must be embedded in the prompt"

    # Leaving the wizard via the HEADER exit must NOT surface the shared
    # key-create modal (#2710). Pre-fix this popped a 30-day-default modal.
    # #3428/#2937 (lane B3, EXIT EVIDENCE — the negative control): take the
    # connect step's OWN advance (not Skip) with NOTHING connected and assert
    # the wizard cannot reach Connected. The deleted human writer is exactly
    # what used to make this pass as a false positive, so this control is the
    # direct test of the fix.
    page.get_by_role("button", name="I've set it up — Continue →").click()
    # #3428 (lane B3, review cycle 1 P1-3): scope the exit evidence to the
    # VISIBLE final screen. review cycle 6 (item 2): "No connection observed
    # yet" is also text inside the sr-only step announcement (clipped, not
    # display:none), so an unscoped `body` assertion would still pass if only the
    # `.done` body reverted to the deleted "Your agent is connected — it files
    # your decisions and findings…" claim.
    expect(page.locator(".welcome-title")).to_have_text("No connection observed yet", timeout=10_000)
    expect(page.locator("div.done")).to_contain_text("We haven't seen your agent's first write through its Tortoise tools yet")
    assert "Your agent is connected" not in page.locator("div.done").inner_text(), \
        "#3428: the final screen rendered the connection claim with nothing connected"
    assert not any(c.get("step") == "harness-connected" for c in cap["checkpoint"]), \
        f"#3428: the connect step's advance must NOT write the harness-connected checkpoint: {cap['checkpoint']}"
    # review cycle 1 (P2-5), corrected in cycle 4 (item 5): this file never
    # reports `harness-connected` — the route serves GET /v1/onboarding/state
    # from `onboarding_projection`, which DEFAULTS to None and so fulfils the
    # pre-existing 401 fall-through (an explicit handler, not an unmocked call),
    # and the checkpoint mock always answers completed_steps: []. The Connected
    # branch is therefore unreachable here — this line can only catch the
    # present-tense sentence leaking OUTSIDE it. The branch-gated claim is
    # pinned structurally in wizardConnectTripwire.test.js
    # (doneCaptureClaim === 'present').
    assert "is capturing your agent" not in page.locator("div.done").inner_text(), \
        "#3428: the present-tense capture sentence must not leak outside the Connected branch"
    page.locator("header").get_by_role("button", name="Open my dashboard →").click(timeout=15_000)
    _expect_left_wizard(page)
    assert page.locator("[role=dialog]").count() == 0, \
        "#2710: a stray key-create modal leaked onto the dashboard after the header exit"
    assert page.get_by_text("Create new API key").count() == 0, \
        "#2710: the queued create-key modal (30-day default) must never surface post-exit"


def test_owner_wizard_complete_exit_after_mint_is_modal_free(page: Page) -> None:
    """#2710 (both exit paths): the SECOND wizard exit (`wizardComplete`, the
    done step's ".wizard-actions" button — the path an owner actually takes
    after minting) must also clear the shared modal. The header escape is
    covered above; a leak on this path would otherwise ship untested."""
    _seed_cookie(page, "u-owner-exit")
    _wire(page, role="owner")
    _walk_to_connect(page)
    _mint_from_connect(page)
    page.get_by_role("button", name="Skip for now").click()
    # #2912 (PR-gate UX): skipped → paused, and the heading says so (cycle 6
    # item 2: the observation phrasing, both forks).
    expect(page.locator(".welcome-title")).to_have_text(
        "Setup paused — no connection observed yet", timeout=10_000)
    page.locator(".wizard-actions").get_by_role("button", name="Go to dashboard").click(timeout=15_000)
    _expect_left_wizard(page)
    assert page.locator("[role=dialog]").count() == 0, \
        "#2710: the wizardComplete exit leaked the shared key-create modal"
    assert page.get_by_text("Create new API key").count() == 0, \
        "#2710: the wizardComplete exit must not surface the queued create-key modal"


def test_owner_connect_step_paste_toggle_reaches_prompt_card(page: Page) -> None:
    """#2710: the owner/admin paste escape — an owner who holds a durable key's
    plaintext can paste it without leaving the wizard. #3783: an org that
    ALREADY has a usable durable row must be offered that key, not a mint CTA
    that would spend the free tier's last key slot."""
    _seed_cookie(page, "u-owner2")
    cap = _wire(page, role="admin", key_rows=[DURABLE_ROW])
    _walk_to_connect(page)
    # No in-memory key + a usable durable row → the EXISTING-key affordance.
    # (Pre-#3783 this rendered the mint CTA, which is the reported defect.)
    assert page.get_by_role("button", name="Create an API key").count() == 0, \
        "#3783: an existing usable key must not present the mint CTA"
    expect(page.get_by_role("button", name="Use an existing key")).to_be_visible()
    assert "already has an API key" in page.locator("body").inner_text(), \
        "#3783: the step must name the key that already exists"
    page.get_by_role("button", name="I already have a key — paste it instead").click()
    expect(page.locator("#wizard-paste-row")).to_be_visible(timeout=5_000)
    page.get_by_label("Paste an API key").fill(PASTED_KEY)
    page.get_by_role("button", name="Use this key").click()
    card = page.locator(".wizard-prompt-card").first
    expect(card).to_be_visible(timeout=10_000)
    assert PASTED_KEY in card.inner_text(), "the pasted key must be embedded in the prompt"
    assert cap["mint"] == [], f"pasting must not mint a second key: {cap['mint']}"


@pytest.mark.parametrize("refusal,row,alert", [
    ("unknown", None, "does not match any key"),
    ("expiring", _row(PASTED_KEY_2, expires_at="2027-01-01T00:00:00Z"), "It expires"),
    ("revoked", _row(PASTED_KEY_2, revoked_at="2026-01-01T00:00:00Z"), "revoked or disabled"),
    ("disabled", _row(PASTED_KEY_2, enabled=False), "revoked or disabled"),
    ("bootstrap-row", _row(PASTED_KEY_2, created_via="bootstrap"), "login session"),
])
def test_owner_connect_paste_refusals_never_advance(page: Page, refusal, row, alert) -> None:
    """#2710 (test-review P1): the paste escape's REFUSAL paths. A key that is
    unknown / expiring / revoked / disabled / session-scoped must surface the
    documented alert and keep the user on the connect step — no advance, no
    mint, no prompt card. Pre-fix (and for a regression that treats the paste
    as truth) the wizard would silently advance with an unusable key."""
    _seed_cookie(page, f"u-refuse-{refusal}")
    cap = _wire(page, role="owner", key_rows=[row] if row else [])
    _walk_to_connect(page)
    page.get_by_role("button", name="I already have a key — paste it instead").click()
    page.get_by_label("Paste an API key").fill(PASTED_KEY_2)
    page.get_by_role("button", name="Use this key").click()
    alert_el = page.locator("[role=alert]")
    expect(alert_el).to_be_visible(timeout=5_000)
    assert alert in alert_el.inner_text(), \
        f"the {refusal} refusal must explain itself: {alert_el.inner_text()!r}"
    assert page.locator(".wizard-prompt-card").count() == 0, \
        f"a {refusal} key must not render a setup prompt"
    assert cap["mint"] == [], f"a refused paste must not mint: {cap['mint']}"


def test_owner_connect_paste_rejects_malformed_input(page: Page) -> None:
    """#2710: the pre-`tt_` guard — a value that is not a Tortoise key shape is
    refused with its own message (and empty input cannot be submitted at all)."""
    _seed_cookie(page, "u-malformed")
    _wire(page, role="owner")
    _walk_to_connect(page)
    page.get_by_role("button", name="I already have a key — paste it instead").click()
    use = page.get_by_role("button", name="Use this key")
    assert use.is_disabled(), "an empty paste box must not be submittable"
    page.get_by_label("Paste an API key").fill("not-a-tortoise-key")
    use.click()
    alert_el = page.locator("[role=alert]")
    expect(alert_el).to_be_visible(timeout=5_000)
    assert "does not look like a Tortoise API key" in alert_el.inner_text()
    assert page.locator(".wizard-prompt-card").count() == 0


def test_owner_connect_mint_cap_keeps_the_paste_escape(page: Page) -> None:
    """#2710 (test-review P1): the 402 tier cap is the only reachable mint
    failure. It must surface the limit + remedy and KEEP the paste escape open
    (the remedy ends at the paste box) — never dead-end the user again, and
    never advance with no key."""
    _seed_cookie(page, "u-capped")
    cap = _wire(page, role="owner", mint_status=402)
    _walk_to_connect(page)
    page.get_by_role("button", name="Create an API key").click()
    alert_el = page.locator("[role=alert]")
    expect(alert_el).to_be_visible(timeout=10_000)
    assert "limit of API keys" in alert_el.inner_text(), \
        f"the cap must be explained as a limit, not an error: {alert_el.inner_text()!r}"
    assert len(cap["mint"]) == 1, f"the cap still means exactly one attempted mint: {cap['mint']}"
    expect(page.locator("#wizard-paste-row")).to_be_visible(timeout=5_000)
    assert page.locator(".wizard-prompt-card").count() == 0, \
        "a capped mint must not render a setup prompt"


@pytest.mark.parametrize("width", [320, 360, 390, 414])
def test_owner_connect_key_row_has_no_horizontal_overflow(page: Page, width: int) -> None:
    """#2711: the shown-once key row (and its Copy button) must fit the
    viewport at phone widths. Measured, not eyeballed: the pre-fix row at
    390px measured scrollWidth 531 vs clientWidth 390 with the Copy button at
    x=481..531 (clipped off-screen). Bracketed across widths (test-review
    P2) with an over-long 72-char token — the worst case for an unbreakable
    `tt_…` string."""
    _seed_cookie(page, f"u-mobile-{width}")
    _wire(page, role="owner", minted_key=LONG_MINTED_KEY)
    page.set_viewport_size({"width": width, "height": 844})
    _walk_to_connect(page)
    _mint_from_connect(page)
    page.get_by_role("button", name="Key separate from prompt").click()
    expect(page.locator("code", has_text="tt_")).to_be_visible(timeout=5_000)
    m = _measure_key_row(page)
    assert m["scrollWidth"] == m["clientWidth"], \
        f"#2711: horizontal overflow at {width}px — {m}"
    assert m["rowOverflow"] is not None and m["rowOverflow"] <= 0, \
        f"#2711: the key ROW itself overflows at {width}px — {m}"
    assert m["copyBtnRight"] is not None and m["copyBtnRight"] <= m["viewport"], \
        f"#2711: the Copy button is clipped off-screen at {width}px — {m}"


def test_build_fork_key_row_has_no_horizontal_overflow(page: Page) -> None:
    """#2711: the BUILD fork's connect step renders a FOURTH shown-once key row
    (its own inline style, which already carries `wordBreak: 'break-all'` and
    no `minWidth`). This is a deliberate boundary PIN, not a regression guard:
    it passes against the pre-fix dist because that row never overflowed — its
    job is to keep the fourth row honest if the three agent-driven rows' shared
    style is ever refactored. Measured at 390px with a 72-char token: the row
    and its Copy button must stay inside the viewport."""
    _seed_cookie(page, "u-buildfork-mobile")
    _wire(page, role="owner", minted_key=LONG_MINTED_KEY)
    page.set_viewport_size({"width": 390, "height": 844})
    _walk_to_fork(page)
    page.get_by_role("button", name=re.compile("Build an application on top")).click()
    page.get_by_role("button", name="Continue →").click()
    expect(page.locator("body")).to_contain_text("Connect your agent", timeout=10_000)
    page.get_by_role("button", name="Create an API key", exact=True).click()
    expect(page.locator("code", has_text="tt_")).to_be_visible(timeout=10_000)
    m = _measure_key_row(page)
    assert m["scrollWidth"] == m["clientWidth"], \
        f"#2711 (build fork): horizontal overflow at 390px — {m}"
    assert m["rowOverflow"] is not None and m["rowOverflow"] <= 0, \
        f"#2711 (build fork): the key ROW overflows at 390px — {m}"
    assert m["copyBtnRight"] is not None and m["copyBtnRight"] <= m["viewport"], \
        f"#2711 (build fork): the Copy button is clipped off-screen — {m}"


def test_connect_prompt_card_has_no_nested_interactive_and_dragselect_is_safe(page: Page) -> None:
    """#2755: WizardPromptCard is a plain region with ONE explicit copy
    control. (1) No interactive element nested in an interactive element
    (WCAG 4.1.2 / 1.3.1). (2) A drag-select inside the card must NOT write to
    the clipboard (pre-fix the whole card was a `role=button` click target, so
    selecting a line copied the entire prompt over the user's clipboard)."""
    # #2744: the DOCUMENT is now the LOCAL preview, so the clipboard grant must
    # target that origin (a grant for APP_HOST no longer matches the document).
    page.context.grant_permissions(["clipboard-read", "clipboard-write"],
                                   origin=DASHBOARD_URL.rstrip("/"))
    _seed_cookie(page, "u-a11y")
    _wire(page, role="owner")
    _walk_to_connect(page)
    _mint_from_connect(page)
    card = page.locator(".wizard-prompt-card").first
    nested = page.evaluate("""() => {
      const sel = 'a[href],button,input,select,textarea,[role=button],[role=link],[tabindex]:not([tabindex="-1"])';
      const out = [];
      document.querySelectorAll('[role=button],a[href],button').forEach((el) => {
        const inner = el.querySelectorAll(sel);
        if (inner.length) out.push({ host: el.tagName, label: el.getAttribute('aria-label') || '',
                                     inner: inner.length });
      });
      return out;
    }""")
    assert nested == [], f"#2755: nested interactive elements on the connect step: {nested}"

    sentinel = "SENTINEL-not-overwritten"
    page.evaluate(f"() => navigator.clipboard.writeText({sentinel!r})")
    card.scroll_into_view_if_needed()
    box = card.bounding_box()
    assert box is not None, "#2755: the prompt card has no bounding box"
    # test-review P2: prove the drag actually landed on the card (a drag that
    # misses would make the clipboard assertion vacuous).
    assert box["y"] >= 0 and box["y"] + 40 <= page.viewport_size["height"], \
        f"#2755: the card is not fully in the viewport — the drag would miss: {box}"
    page.mouse.move(box["x"] + 10, box["y"] + 10)
    page.mouse.down()
    page.mouse.move(box["x"] + 150, box["y"] + 40, steps=10)
    page.mouse.up()
    selected = page.evaluate("() => window.getSelection().toString()")
    assert selected, "#2755: the drag-select produced no selection — the check would be vacuous"
    assert page.evaluate("() => navigator.clipboard.readText()") == sentinel, \
        "#2755: a drag-select inside the prompt card overwrote the clipboard"
    # The one explicit control still copies the card's own prompt text.
    card_text = card.inner_text()
    card.locator("button").last.click()
    _wait_until(lambda: page.evaluate("() => navigator.clipboard.readText()") != sentinel,
                timeout_ms=5_000, message="the explicit copy to resolve")
    copied = page.evaluate("() => navigator.clipboard.readText()")
    assert card_text.startswith(copied), \
        ("#2755: the copy control must copy the FULL prompt (the card's text "
         f"starts with it) — got {len(copied)} chars: {copied[:80]!r}")
    assert len(copied) > 200, "#2755: the copied prompt looks truncated"


def test_owner_connect_mint_failure_is_visible(page: Page) -> None:
    """#2710 (code-review P1): a NON-402 mint failure must be visible. The error
    is rendered inside the paste disclosure, but only the 402 cap opens it — so a
    suspension 403 / transport failure / the #2326 team-switch guard would have
    silently reverted the button with no feedback and no way forward."""
    _seed_cookie(page, "u-mint-500")
    # api() surfaces `detail` as the Error message, so this sentinel proves the
    # visible alert IS the mint's failure and not some unrelated banner.
    cap = _wire(page, role="owner", mint_status=500,
                mint_error_detail="MINT-FAILURE-SENTINEL")
    _walk_to_connect(page)
    page.get_by_role("button", name="Create an API key").click()
    alert_el = page.locator("[role=alert]")
    expect(alert_el).to_contain_text("MINT-FAILURE-SENTINEL", timeout=10_000)
    assert len(cap["mint"]) == 1, f"exactly one attempted mint: {cap['mint']}"
    # the disclosure stays CLOSED (the 402 path is the only one that opens it) …
    assert page.locator("#wizard-paste-row").count() == 0
    # … so the alert must be visible OUTSIDE it, and the user can retry or paste.
    expect(page.get_by_role("button", name="Create an API key")).to_be_enabled()
    expect(page.get_by_role("button", name="I already have a key — paste it instead")).to_be_visible()
    assert page.locator(".wizard-prompt-card").count() == 0


@pytest.mark.parametrize("surface,sync_text", [
    ("Claude Desktop", "Open Claude Desktop → Settings → Connectors"),
    ("Claude Web", "Open claude.ai → Settings → Connectors"),
])
def test_manual_harness_surfaces_are_keyless_oauth(page: Page, surface: str, sync_text: str) -> None:
    """#2865: the two MANUAL Claude surfaces are KEY-LESS OAuth, for every role.

    This test replaces `test_owner_no_key_affordance_on_manual_harness_surfaces`,
    whose `#2710`/`#2912` contract required the opposite: that these surfaces
    show a visible `Create an API key` mint CTA and render ONLY the key block
    until a key lands. #2864 made the hosted `/mcp` endpoint advertise OAuth
    discovery (RFC 9728), so these surfaces connect by sign-in and never ask for
    a credential — the old contract pinned the behaviour this change removes.

    The guarantee that replaces it: no key row, no `Bearer` recipe, no beta
    caveat, and the Continue affordance reachable WITHOUT a key.
    """
    _seed_cookie(page, "u-manual-" + surface.split()[-1].lower())
    _wire(page, role="owner", key_rows=[DURABLE_ROW])
    _walk_to_connect(page)
    # Level 1 → level 2: Claude is already the active family; pick the surface.
    page.get_by_role("button", name="Claude", exact=True).click()
    surface_btn = page.get_by_role("button", name=re.compile(re.escape(surface)))
    surface_btn.click()
    expect(surface_btn).to_have_class(re.compile(r"\bactive\b"), timeout=10_000)

    body = page.locator("body").inner_text()
    # (a) No mint CTA and no key row on a key-less surface.
    assert page.get_by_role("button", name="Create an API key").count() == 0, \
        f"{surface}: a key-less OAuth surface must not offer a mint CTA"
    assert "YOUR_API_KEY" not in body, \
        f"{surface}: the placeholder key must not render"
    assert page.locator("code", has_text="…").count() == 0, \
        f"{surface}: no fake '…' key row"
    # (b) No bearer recipe and no beta 'Request headers' caveat.
    assert "Bearer" not in body, \
        f"{surface}: the OAuth surface must not show an Authorization: Bearer recipe"
    assert "Request headers" not in body or "Leave Request headers empty" in body, \
        f"{surface}: the only 'Request headers' mention must be the OAuth 'leave it empty' step"
    # (c) The sign-in / Authorize step is present.
    assert "sign-in" in body or "sign in" in body, \
        f"{surface}: the OAuth recipe must state the sign-in step"
    assert "Authorize" in body or "Add custom connector" in body, \
        f"{surface}: the OAuth recipe must state the connector/Authorize step"
    # (d) Continue is reachable WITHOUT a key — the old no-dead-end guarantee.
    continue_btn = page.get_by_role(
        "button", name=re.compile(r"I've connected it — Continue"))
    expect(continue_btn).to_be_visible(timeout=5_000)
    expect(continue_btn).to_be_enabled()


@pytest.mark.parametrize("surface,connector_lead", [
    ("Claude Desktop", "Open Claude Desktop → Settings → Connectors"),
    ("Claude Web", "Open claude.ai → Settings → Connectors"),
])
def test_member_without_key_reaches_keyless_claude_connectors(
        page: Page, surface: str, connector_lead: str) -> None:
    """#2865 Indicator: a NON-owner, NON-admin member HOLDING NO API KEY selects
    Claude Desktop / Claude Web in the live wizard and is shown a sign-in /
    Authorize path with no key row and no beta caveat, and can Continue — no
    key, no mint.

    This is the row the issue's Indicator actually names. The sibling
    `test_manual_harness_surfaces_are_keyless_oauth` covers an OWNER/admin, so
    on its own it proves the recipe renders but NOT that the `isOwnerAdmin`
    gate stopped hiding these tabs from members — pre-#2865 a member was sent
    past the harness chooser to a paste-a-key row, so an OAuth connect (which
    needs no mint) was unreachable. `role="member"` + no `key_rows` is the
    non-admin, key-less subject.
    """
    _seed_cookie(page, "u-member-" + surface.split()[-1].lower())
    # role="member" (NOT owner/admin) AND no key rows: a member holds no key
    # and cannot mint one (POST /v1/team/keys is _require_owner_admin).
    cap = _wire(page, role="member")
    _walk_to_connect(page)

    # (a) The chooser itself is reachable for a member — the #2865 gate change.
    expect(page.locator(".harness-family")).to_have_count(4, timeout=10_000)
    expect(page.get_by_role("button", name="Claude", exact=True)).to_be_visible(timeout=5_000)

    # Level 1 → level 2: Claude is already the active family; pick the surface.
    surface_btn = page.get_by_role("button", name=re.compile(re.escape(surface)))
    surface_btn.click()
    expect(surface_btn).to_have_class(re.compile(r"\bactive\b"), timeout=10_000)
    body = page.locator("body").inner_text()

    # (b) NO key affordance of any kind on the key-less OAuth leaf.
    assert page.get_by_role("button", name="Create an API key").count() == 0, \
        f"{surface}: a member must not see the owner/admin mint CTA"
    assert "Use this key" not in body, \
        f"{surface}: a key-less OAuth leaf must not offer the paste escape"
    assert "Your API key" not in body, f"{surface}: no key row on a key-less leaf"
    # (c) No credential recipe and no beta 'Request headers' caveat survive.
    assert "Bearer" not in body, \
        f"{surface}: must not show an Authorization: Bearer recipe"
    assert "rolling out in Anthropic" not in body, \
        f"{surface}: the beta Request-headers caveat must be gone"
    assert "use the Claude Code surface instead" not in body, \
        f"{surface}: the divert-to-Claude-Code escape hatch must be gone"
    # (d) The OAuth recipe: connector URL → sign-in → Authorize → org.
    assert connector_lead in body, f"{surface}: the connector lead-in must render"
    assert "https://api.premiselabs.co/mcp" in body, \
        f"{surface}: the canonical connector URL must render"
    assert "sign-in" in body or "sign in" in body, \
        f"{surface}: the OAuth recipe must state the sign-in step"
    assert "Authorize" in body, f"{surface}: the OAuth recipe must state the Authorize step"
    assert "Pick the Organization" in body, \
        f"{surface}: the OAuth recipe must state the org chooser step"
    # (e) Continue is reachable and ENABLED without a key — the guarantee that
    # replaces the pre-#2865 owner-only no-dead-end tripwire.
    continue_btn = page.get_by_role(
        "button", name=re.compile(r"I've connected it — Continue"))
    expect(continue_btn).to_be_visible(timeout=5_000)
    expect(continue_btn).to_be_enabled()
    continue_btn.click()
    # … and a key-less advance must NOT write the harness-connected checkpoint
    # (#2937 is this same handler's KEYLESS leaf — one defect, two variants).
    # The advance now reports the server projection, so with nothing connected
    # it lands on the honest not-connected done step and the exit is still
    # reachable (the user is never trapped).
    expect(page.locator(".harness-families")).to_have_count(0, timeout=10_000)
    # #3428 (review cycle 1 P1-3): assert the VISIBLE final screen, not the
    # unscoped body — the step label also lives in the sr-only step
    # announcement, so a body-wide assertion cannot falsify a `.done`-body
    # regression. (cycle 6 item 2: the label is now "No connection observed yet".)
    expect(page.locator(".welcome-title")).to_have_text("No connection observed yet", timeout=10_000)
    expect(page.locator("div.done")).to_contain_text("We haven't seen your agent's first write through its Tortoise tools yet")
    assert not any(c.get("step") == "harness-connected" for c in cap["checkpoint"]), \
        f"#2937/#3428: a key-less advance must NOT write the checkpoint: {cap['checkpoint']}"
    expect(page.locator(".wizard-actions").get_by_role(
        "button", name="Go to dashboard")).to_be_visible(timeout=10_000)


def test_codex_desktop_hides_the_key_mode_pills(page: Page) -> None:
    """#2756/#2912: the Codex Desktop surface embeds the key in the config block
    by construction, so the "Key separate from prompt" promise cannot be honored
    there — the pills AND the separate key row must be gone on that surface, and
    both must come back on the CLI surface."""
    _seed_cookie(page, "u-codex-mode")
    _wire(page, role="owner")
    _walk_to_connect(page)
    _mint_from_connect(page)
    pills = page.get_by_role("button", name=re.compile("Key (included in|separate from) prompt"))
    group = page.get_by_role("group", name="Codex surface")
    page.get_by_role("button", name="Codex", exact=True).click()
    expect(group).to_be_visible(timeout=10_000)  # sync: the Codex surface row rendered
    expect(pills).to_have_count(2, timeout=5_000)
    # "separate" on the CLI surface shows the separate key row and keeps the key
    # OUT of the prompt card.
    page.get_by_role("button", name="Key separate from prompt").click()
    expect(page.locator("code", has_text=MINTED_KEY)).to_be_visible(timeout=5_000)
    # Desktop: the pills and the separate row go away; the key lives in the
    # config block instead.
    group.get_by_role("button", name="Codex Desktop").click()
    expect(group.get_by_role("button", name="Codex Desktop")).to_have_attribute("aria-pressed", "true")
    expect(pills).to_have_count(0, timeout=5_000)
    assert page.locator("code", has_text=MINTED_KEY).count() == 0, \
        "the separate key row must not sit beside a key-embedding Desktop block"
    expect(page.locator(".wizard-prompt-card").first).to_contain_text(MINTED_KEY, timeout=5_000)
    # … and back to CLI.
    group.get_by_role("button", name="Codex CLI").click()
    expect(pills).to_have_count(2, timeout=5_000)


def test_codex_desktop_surface_reaches_config_toml_instructions(page: Page) -> None:
    """#2756/#2912: Codex has two surfaces (CLI needs a terminal; Desktop does
    not). The Codex family must offer them as a level-2 surface row, and Desktop
    must reach the ~/.codex/config.toml instructions — pre-fix the toggle's
    state and the `codexDesktop` copy in harnesses.js were dead, so a Desktop
    user got the CLI command.

    The assertion pins UNIVERSAL_COMMAND.codexDesktop's distinctive stanza
    (`[mcp_servers.tortoise]`), not just the intro sentence that shares the
    branch (test-review P1)."""
    _seed_cookie(page, "u-codex")
    _wire(page, role="owner")
    _walk_to_connect(page)
    _mint_from_connect(page)
    page.get_by_role("button", name="Codex", exact=True).click()
    group = page.get_by_role("group", name="Codex surface")
    expect(group).to_be_visible(timeout=5_000)
    assert group.locator(".harness-surface").all_inner_texts()[0].startswith("Codex CLI"), \
        "Codex CLI is the default surface (terminal path)"
    # CLI (default) is the terminal path — no config.toml, no TOML stanza.
    cli_text = page.locator(".wizard-prompt-card").first.inner_text()
    assert "config.toml" not in cli_text, \
        "the Codex CLI variant must not claim the Desktop config file"
    assert "[mcp_servers.tortoise]" not in cli_text, \
        "the Codex CLI variant must not render the Desktop TOML block"
    group.get_by_role("button", name="Codex Desktop").click()
    card = page.locator(".wizard-prompt-card").first
    expect(card).to_contain_text("~/.codex/config.toml", timeout=5_000)
    assert "[mcp_servers.tortoise]" in card.inner_text(), \
        "the Desktop variant must render UNIVERSAL_COMMAND.codexDesktop (the TOML block)"
    # The Desktop copy control uses the harness's own label (HARNESS_COPY_LABEL).
    expect(card.get_by_role("button", name="Copy instructions")).to_be_visible()
    # The surface choice is reversible and scoped to the Codex family.
    group.get_by_role("button", name="Codex CLI").click()
    assert "config.toml" not in page.locator(".wizard-prompt-card").first.inner_text(), \
        "switching back to CLI must restore the terminal instructions"
    page.get_by_role("button", name="Pi", exact=True).click()
    assert page.locator(".harness-surfaces").count() == 0, \
        "the surface row must not leak onto single-choice families (Pi)"


def test_3218_multi_part_procedures_render_a_numbered_step_three(page: Page) -> None:
    """#3218: Pi's connect procedure has two user-visible parts (set up, then
    restart + verify) and Claude Web/Desktop's has two (add the connector,
    then hand Claude the workflows). Each part is its own numbered block — the
    second one used to be a bare caption inside block 2, so the circles said
    (1, 2) while the user had three things to do.

    #2865 interaction: Claude Web/Desktop are now key-less OAuth, so their
    KEY block is gone — the two user-visible parts are (1) add the connector
    and (2) the workflows prompt. #3218's invariant is preserved (the prompt
    hand-off is its own numbered block, not a caption buried in block 1); only
    the count drops from #3218's keyed three to the key-less two."""
    _seed_cookie(page, "u-3218")
    _wire(page, role="owner")
    _walk_to_connect(page)
    _mint_from_connect(page)

    # Pi: 1 key → 2 Set up Pi → 3 Restart Pi and verify
    page.get_by_role("button", name="Pi", exact=True).click()
    titles = page.locator(".wizard-block-title").all_inner_texts()
    assert len(titles) == 3, f"Pi must render three numbered blocks, got {titles}"
    assert titles[0].endswith("Get your API key"), titles
    assert titles[1].endswith("Set up Pi"), titles
    assert titles[2].endswith("Restart Pi and verify"), titles

    # Claude Web: #2865 made this leaf key-less OAuth, so there is no key
    # block — 1 Add the Claude Web connector → 2 the workflows prompt.
    page.get_by_role("button", name="Claude", exact=True).click()
    page.get_by_role("button", name="Claude Web").click()
    titles = page.locator(".wizard-block-title").all_inner_texts()
    assert len(titles) == 2, \
        f"key-less Claude Web must render two numbered blocks, got {titles}"
    assert titles[0].endswith("Add the Claude Web connector"), titles
    assert titles[1].endswith("Give Claude the Tortoise workflows"), titles

    # Claude Code is a single-prompt flow — the circles stay (1, 2).
    page.get_by_role("button", name="Claude Code").click()
    titles = page.locator(".wizard-block-title").all_inner_texts()
    assert len(titles) == 2, f"Claude Code stays at two numbered blocks, got {titles}"
    assert titles[1].endswith("Set up Claude Code"), titles


def test_3218_key_row_states_the_visibility_window(page: Page) -> None:
    """#3218: the key surfaces no longer say "(shown once)" — they state the real
    window (visible while on this step) and the recovery path (create one from
    the API Keys page; rotating replaces it).

    The note must render in the DEFAULT 'included' mode too: that mode hides the
    separate key row, and a note gated on the row would leave the commonest
    connect path with no cue at all (review cycle 1, P1)."""
    _seed_cookie(page, "u-3218-key")
    _wire(page, role="owner")
    _walk_to_connect(page)
    _mint_from_connect(page)
    # DEFAULT mode: no separate key row, but the note must still be on screen.
    assert page.locator(".key-row").count() == 0, \
        "the default 'included' mode hides the separate key row"
    note = page.locator(".wizard-note", has_text="Visible while you're on this step")
    expect(note).to_be_visible(timeout=5_000)
    note_text = note.inner_text()
    assert "API Keys page" in note_text, note_text
    assert "rotating replaces this key" in note_text, note_text
    # Switching to 'separate' shows the row; the note must NOT be duplicated.
    page.get_by_role("button", name="Key separate from prompt").click()
    row = page.locator(".key-row")
    expect(row).to_contain_text("Your API key:", timeout=5_000)
    assert "shown once" not in row.inner_text(), \
        "the row must not claim 'shown once'"
    assert page.locator(".wizard-note", has_text="Visible while you're on this step").count() == 1, \
        "exactly ONE visibility note may render (not one per key surface)"


def test_first_timer_wizard_build_fork_records_no_catalog_presented(page: Page) -> None:
    """#3913 (owner ruling 2026-09-20): picking the BUILD fork records the fork
    and NO checkpoint step. The build-fork gate is the two acts the server
    OBSERVES — harness-connected + first-points-filed — so the dashboard must
    not write `catalog-presented` (the render-time effect is gone and the pick
    handler's optional mark is gone with it). This is the runtime half of that
    removal: the ONLY client checkpoint on the journey is the set-once fork.
    #2323: the org-holding journey never mints a second org.

    The catalog pin is deliberately two-sided (#2763): the fork card renders the
    STATIC placeholder because the registry fetch is gated on the connect step
    while the catalog renders one step earlier — the fetch is counted so the
    mock is genuinely exercised, and the assertion flips to the registry-only
    name when #2763 lands."""
    _seed_cookie(page, "u-bld")
    cap = _wire(page, role="owner")
    # #2744: the DOCUMENT always loads from the local built-dist preview.
    _goto_local_dashboard(page)
    expect(page.locator("body")).to_contain_text("Continue setup", timeout=20_000)
    page.get_by_role("button", name="Continue setup").click()
    # STEP 0: create/join org (orientation removed per epic #2534). #2912: the
    # org-holding read-only summary shows the stage h1 'Your Organization'.
    # #2364 round-1 (kept through the merge): never 'Create your Organization'
    # on resume/re-entry (#2323 read-only).
    expect(page.locator(".welcome-title")).to_have_text("Your Organization", timeout=10_000)
    expect(page.locator("body")).not_to_contain_text("Create your Organization", timeout=10_000)
    expect(page.locator("body")).to_contain_text("You're set up in", timeout=5_000)
    page.get_by_role("button", name="Continue →").click()
    expect(page.locator("body")).to_contain_text("Choose how you'll use Tortoise", timeout=10_000)
    # STEP 1: fork card — pick the BUILD fork. #2763: the catalog renders from
    # the static placeholder here (the registry fetch fires one step later than
    # the only call site that renders it), so assert the SHIPPED names and then
    # prove the registry request is genuinely issued on the connect step.
    page.get_by_role("button", name=re.compile("Build an application on top")).click()
    expect(page.locator("body")).to_contain_text("Build catalog", timeout=10_000)
    expect(page.locator("body")).to_contain_text("Session recorder", timeout=5_000)
    assert cap["capabilities"] == 0, \
        "the registry catalog must not be fetched from the fork card (it renders one step earlier — #2763)"
    page.get_by_role("button", name="Continue →").click()
    expect(page.locator("body")).to_contain_text("Connect your agent", timeout=10_000)
    # The fetch is issued by a passive effect when the connect step mounts —
    # poll instead of snapshotting immediately after the text appears.
    _wait_until(lambda: cap["capabilities"] == 1, timeout_ms=5_000,
                message="the registry catalog fetch (exactly once)")
    assert cap["capabilities"] == 1, \
        f"#2004 (W8): the registry catalog must be fetched exactly once: {cap['capabilities']}"
    # #3913: the fork is the ONLY checkpoint the dashboard writes. The old
    # assertion here was the opposite — it required the catalog-presented mark;
    # that write is deleted, so the meaningful pin is now its ABSENCE, and the
    # exact capture proves no OTHER step write was smuggled in beside the fork.
    assert cap["checkpoint"] == [{"fork": "build"}], \
        f"#3913: the build pick must record the fork and NO step: {cap['checkpoint']}"
    assert [c for c in cap["checkpoint"] if c.get("step")] == [], \
        f"#3913: no checkpoint step may be written by the dashboard: {cap['checkpoint']}"
    # the PATCH surface is the other way a catalog mark could return.
    assert [b for b in cap["state"] if b.get("catalog_presented")] == [], \
        f"#3913: no PATCH may record catalog_presented: {cap['state']}"
    assert cap["org_create"] == [], f"#2323 violated: org_create fired: {cap['org_create']}"


def test_build_fork_connected_on_the_two_observed_acts(page: Page) -> None:
    """#3913: the wizard's build path reaches the connected done screen on a
    projection carrying the two OBSERVED acts (harness-connected +
    first-points-filed) and NOT `catalog-presented`, with no catalog row on the
    way. This pins the UI path only — the wizard cursor advances client-side, so
    it would walk the same way before the gate change. The assertions that
    actually pin #3913 live elsewhere: the exact checkpoint capture in
    `test_first_timer_wizard_build_fork_records_no_catalog_presented` above
    (the fork is the ONLY thing written), the per-fork counted rows in the
    dashboard JS unit tests, and the server-side gate in
    test_capabilities_endpoint.py / test_onboarding_auto_complete.py."""
    _seed_cookie(page, "u-bld-2acts")
    proj = {"org_id": "team_o", "fork": "build", "status": "active",
            "onboarding_complete": False,
            "completed_steps": ["team-named", "harness-connected", "first-points-filed"],
            "session_recording": True}
    # The projection handed to the app carries ONLY the two observed acts. That is
    # a fixture premise, not an assertion — the app-derived pin is the done screen
    # below, which must render without a catalog step anywhere in it.
    _wire(page, role="owner", onboarding_projection=proj)
    _walk_to_fork(page)  # fork already chosen server-side → Continue is present
    page.get_by_role("button", name="Continue →").click()
    expect(page.locator("body")).to_contain_text("Connect your agent", timeout=10_000)
    _advance_to_done(page)
    expect(page.locator(".welcome-title")).to_have_text("You're all set", timeout=10_000)
    done = page.locator("div.done")
    expect(done).to_contain_text("Connected", timeout=10_000)
    # No "catalog" text assert here: the done body contains none on any branch,
    # so it would pass unchanged on origin/main and pin nothing (cycle-3 finding).


# ── #3428/#2937 (lane B3, review cycle 2 P2-2): runtime coverage for the ──
# Connected branch and its derived capture tense. Before this seam the GET
# /v1/onboarding/state call was unmocked and 401'd, so `serverHarnessConnected`
# was always false and the whole success screen was only pinned by source text.
def _connected_projection(*, receipt: str | None = None, probe: str | None = None) -> dict:
    """A server-observed connection for GET /v1/onboarding/state.

    `completed_steps` carries `harness-connected`; `session_recording` is ON so
    "no sentence" on a key-less leaf is a TRUTH decision (no capture install
    path), never a missing-capability accident. `receipt` names the harness
    whose capture receipt was observed; `probe` names the harness whose install
    PROBE was observed (install confirmed server-side, capture not fired yet).
    With NEITHER, the projection is the #3782 live state — recording on with
    nothing observed for the harness — which must read "not installed yet",
    never a future promise. The projection must be WRAPPED by `_wire`
    (`{"onboarding": …}`) — the app reads `st.onboarding`.

    `fork` is deliberately ABSENT: a projection carrying it disables the fork
    card's option buttons (set-once), so `_walk_to_connect`'s own pick would be
    a click on a disabled control. The wizard's local fork choice does not need
    it, and `serverHarnessConnected` reads `completed_steps` only.
    """
    proj = {"org_id": "team_o", "status": "active",
            "onboarding_complete": False,
            "completed_steps": ["team-named", "harness-connected"],
            "session_recording": True}
    if receipt:
        proj[f"session_capture_receipt_{receipt}"] = True
    if probe:
        proj[f"install_probe_{probe}"] = True
    return proj


def _advance_to_done(page: Page) -> None:
    """Land on step 3 from the connect step's skip escape (the done step's
    body is gated on the SERVER projection, so how we advanced does not
    matter — this keeps the capture-tense tests focused on the projection)."""
    page.get_by_role("button", name="Skip for now").click()
    expect(page.locator("div.done")).to_be_visible(timeout=10_000)


def test_connected_screen_states_capture_in_present_tense_on_an_observed_receipt(page: Page) -> None:
    """#3428/#2937 (lane B3, review cycle 2 P2-2a): a per-harness capture
    RECEIPT is the only state in which the owner-approved present-tense sentence
    is truthful. Runtime-proven here (it was source-pinned before)."""
    _seed_cookie(page, "u-b3-receipt")
    _wire(page, role="owner",
          onboarding_projection=_connected_projection(receipt="claude"))
    _walk_to_connect(page)
    _advance_to_done(page)
    expect(page.locator(".welcome-title")).to_have_text("You're all set", timeout=10_000)
    done = page.locator("div.done")
    expect(done).to_contain_text("Connected", timeout=10_000)
    expect(done).to_contain_text("Tortoise is capturing your agent's sessions.")
    assert "will capture your agent's sessions" not in done.inner_text(), \
        "a receipt must yield the PRESENT tense, never the future one"


def test_connected_screen_states_capture_in_future_tense_after_an_install_probe(page: Page) -> None:
    """#3428/#2937 (lane B3, review cycle 2 P2-2b), corrected by #3782: capture
    available, NO receipt, but an install PROBE was observed server-side. The
    probe is exactly what makes the future tense honest — the screen states what
    WILL happen, must NOT print the present-tense sentence, and must NOT claim
    the honest "not installed yet" state (the install was observed)."""
    _seed_cookie(page, "u-b3-no-receipt")
    _wire(page, role="owner", onboarding_projection=_connected_projection(probe="claude"))
    _walk_to_connect(page)
    _advance_to_done(page)
    expect(page.locator(".welcome-title")).to_have_text("You're all set", timeout=10_000)
    done = page.locator("div.done")
    expect(done).to_contain_text("Connected", timeout=10_000)
    expect(done).to_contain_text("Tortoise will capture your agent's sessions.")
    assert "Tortoise is capturing your agent's sessions." not in done.inner_text(), \
        "no receipt means the present-tense claim is false"
    assert "not installed yet" not in done.inner_text(), \
        "#3782: an observed install probe means the install IS installed — not installed yet is false"


def test_connected_screen_without_probe_or_receipt_reports_not_installed(page: Page) -> None:
    """#3782: the live defect. `harness-connected` (a real server-observed
    connection) with recording ON but NEITHER an install probe NOR a capture
    receipt must not promise a capture the server never observed. The screen
    states the honest "not installed yet" — the identical string Settings
    renders for the same state — and neither the present- nor the future-tense
    sentence."""
    _seed_cookie(page, "u-b3-no-probe-no-receipt")
    _wire(page, role="owner", onboarding_projection=_connected_projection())
    _walk_to_connect(page)
    _advance_to_done(page)
    expect(page.locator(".welcome-title")).to_have_text("You're all set", timeout=10_000)
    done = page.locator("div.done")
    expect(done).to_contain_text("Connected", timeout=10_000)
    expect(done).to_contain_text("not installed yet")
    text = done.inner_text()
    assert "Tortoise is capturing your agent's sessions." not in text, \
        "#3782: no receipt — the present-tense claim is false"
    assert "Tortoise will capture your agent's sessions." not in text, \
        "#3782: no probe — the future-tense promise is not server-observed"


def test_keyless_no_capability_leaf_prints_no_capture_sentence(page: Page) -> None:
    """#3428/#2937 (lane B3, review cycle 2 P2-2c): Claude Web has
    `HARNESS_CAPTURE_SUPPORT === false` (no live install path), so even a
    server-observed connection prints NO capture sentence — present or future."""
    _seed_cookie(page, "u-b3-web")
    _wire(page, role="member",
          onboarding_projection=_connected_projection(receipt="claude-web"))
    _walk_to_connect(page)
    page.get_by_role("button", name=re.compile("Claude Web")).click()
    page.get_by_role(
        "button", name=re.compile(r"I've connected it — Continue")).click()
    expect(page.locator("div.done")).to_be_visible(timeout=10_000)
    expect(page.locator(".welcome-title")).to_have_text("You're all set", timeout=10_000)
    done = page.locator("div.done")
    expect(done).to_contain_text("Connected", timeout=10_000)
    text = done.inner_text()
    # review cycle 3 (P2-1): assert the STEM shared by BOTH tenses — the
    # present-tense sentence carries "capturing your agent's sessions" and the
    # future-tense one "capture your agent's sessions", so a future-tense leak
    # used to stay green under the present-only pin.
    assert "your agent's sessions" not in text, \
        f"#3428: a leaf with no capture install path must print no claim, present or future: {text}"


def test_build_fork_done_step_never_claims_a_harness_or_filing(page: Page) -> None:
    """#3428/#2937 (lane B3, review cycle 2 P1-2): the build fork's step 2 is
    the SDK call (POST /v1/points), which files NO onboarding step — so the
    self-fork body ("hasn't filed anything … head back to Claude Code") is
    false the moment the user runs the wizard's own curl, and names a harness
    this branch never offered. The build leaf must say neither.

    (The server-side gap — a REST-first org has no server-observed completion
    signal — is a separate defect, filed by the lane orchestrator.)"""
    _seed_cookie(page, "u-b3-build")
    _wire(page, role="owner")  # GET unmocked → nothing connected
    _walk_to_fork(page)
    page.get_by_role("button", name=re.compile("Build an application on top")).click()
    page.get_by_role("button", name="Continue →").click()
    expect(page.locator("body")).to_contain_text("Connect your agent", timeout=10_000)
    _advance_to_done(page)
    # review cycle 5 (item 5): the build fork's SKIP path must not fall through
    # to a categorical paused arm above a body that refuses to claim it — this
    # <h1> assertion is what keeps the ordering from silently regressing.
    # review cycle 6 (item 2): the observation phrasing is now the ONLY paused
    # string (the self fork prints it too), so this pins the shared wording.
    expect(page.locator(".welcome-title")).to_have_text(
        "Setup paused — no connection observed yet", timeout=10_000)
    text = page.locator("div.done").inner_text()
    assert "we can't tell it's connected yet" in text, \
        f"the build body must state the honest not-observed case: {text!r}"
    assert "hasn't filed anything" not in text, \
        f"#3428 P1-2: the build fork's REST call files a point — this is false: {text!r}"
    assert "Claude Code" not in text, \
        f"#3428 P1-2: the build branch never offers a harness: {text!r}"
    # review cycle 6 (item 10/T3): the `harness-connected` checkpoint assertion
    # that used to sit here was VACUOUS — `_advance_to_done()` clicks "Skip for
    # now", which is state-only and never POSTs a checkpoint, so it could not
    # fail by construction. The deleted writer is covered by the real
    # advance-path controls (the owner connect-step Continue test and the
    # key-less member-advance test) and structurally by
    # wizardConnectTripwire.test.js + distBundle.test.js.
    # review cycle 3 (P1-E + P1-F): the not-connected body may not point at a
    # REST call this branch never rendered (the curl is key-gated) nor at the
    # fork-aware Setup guide, whose only affordance re-enters the wizard at step
    # 0 — always the SDK branch for a build-fork org.
    assert "/v1/points" in text, \
        f"#3428 P1-E: the endpoint must be named, not left as a deictic: {text!r}"
    assert "REST call above" not in text, \
        f"#3428 P1-E: the no-key branch renders no REST call above: {text!r}"
    assert "Setup Guide" not in text, \
        f"#3428 P1-F/P2-10: the unreachable location claim must be gone (and the canonical spelling is 'Setup guide'): {text!r}"


def test_unsure_fork_done_step_names_no_harness(page: Page) -> None:
    """#3428/#2937 (lane B3, review cycle 3 P1-A): the 'unsure' fork answer
    advances straight to step 3, so step 2's harness chooser NEVER renders — yet
    `wizardHarness` keeps its untouched 'claude' default. Naming Claude there is
    the build-fork defect ("names a harness the branch never offered")."""
    _seed_cookie(page, "u-b3-unsure")
    _wire(page, role="owner")  # GET unmocked → nothing connected
    _walk_to_fork(page)
    page.get_by_role("button", name="Not sure yet — decide later").click()
    expect(page.locator("div.done")).to_be_visible(timeout=10_000)
    text = page.locator("div.done").inner_text()
    # review cycle 6 (item 9): assert the REDIRECT clause, not the substring
    # "your agent" — the static body already contains "your agent's first write
    # through its Tortoise tools yet", so the old assertion could not fail. MUTATION: if `doneHarnessName`
    # leaked a real harness on the no-picker fork (e.g. 'Cursor'), this clause
    # disappears while the adjacent 'Claude' check would still pass.
    assert "head back to your agent" in text, \
        f"#3428 P1-A: with no picker the harness degrades to the neutral phrase: {text!r}"
    assert "Claude" not in text, \
        f"#3428 P1-A: the unsure fork never offered Claude: {text!r}"
    assert "Settings → Setup guide" in text, \
        f"#3428 P2-10: the canonical surface name is 'Setup guide': {text!r}"


def test_member_done_step_remedy_asks_for_an_api_key(page: Page) -> None:
    """#3428/#2937 (lane B3, review cycle 3 P1-D): a member cannot mint, and the
    connect step's procedure block is key-gated — so a member on a KEYED leaf
    sees only the paste escape. The not-connected remedy must name the one action
    they have, not a Setup-guide command they can never see."""
    _seed_cookie(page, "u-b3-member")
    _wire(page, role="member")  # GET unmocked → nothing connected
    _walk_to_connect(page)
    _advance_to_done(page)
    text = page.locator("div.done").inner_text()
    assert "ask an owner or admin for an API key" in text, \
        f"#3428 P1-D: a member is told the action they can actually take: {text!r}"
    assert "(running it creates a fresh key)" not in text, \
        f"#3428 P1-D: a member cannot mint, so this clause is false: {text!r}"


def test_member_unsure_done_step_uses_the_neutral_remedy(page: Page) -> None:
    """#3428/#2937 (lane B3, review cycle 4 item 12): a MEMBER who answers
    'unsure' never establishes a harness pick (the fork stays None), so
    `wizardHarness` keeps its untouched 'claude' default. Deriving the remedy
    from it told the member to "ask an owner or admin for an API key" for a
    surface this branch never offered; the neutral arm must print instead."""
    _seed_cookie(page, "u-b3-member-unsure")
    _wire(page, role="member")  # GET unmocked → nothing connected
    _walk_to_fork(page)
    page.get_by_role("button", name="Not sure yet — decide later").click()
    expect(page.locator("div.done")).to_be_visible(timeout=10_000)
    text = page.locator("div.done").inner_text()
    assert "ask an owner or admin for an API key" not in text, \
        f"#3428 item 12: no pick was established, so the member must not be sent for a key: {text!r}"
    assert "(running it creates a fresh key)" not in text, \
        f"#3428 item 12: no pick was established, so no fresh key may be promised: {text!r}"
    assert "Settings → Setup guide" in text, \
        f"#3428 item 12: the neutral arm names the surface every branch reaches: {text!r}"
    assert "Claude" not in text, \
        f"#3428 item 12: the unsure fork never offered Claude: {text!r}"


def test_wizard_mint_cap_done_step_drops_the_fresh_key_clause(page: Page) -> None:
    """#3428/#2937 (lane B3, review cycle 3 P2-7): the wizard's OWN mint 402 sets
    `wizardDurableError`, not the Keys-tab `capNotice` — so an owner who hit the
    cap inside the wizard must not still be told a fresh key will be created."""
    _seed_cookie(page, "u-b3-cap")
    _wire(page, role="owner", mint_status=402,
          mint_error_detail="You've reached your plan's limit of 2 API keys.")
    _walk_to_connect(page)
    page.get_by_role("button", name="Create an API key").click()
    expect(page.locator('[role="alert"]').first).to_be_visible(timeout=10_000)
    _advance_to_done(page)
    text = page.locator("div.done").inner_text()
    assert "(running it creates a fresh key)" not in text, \
        f"#3428 P2-7: the wizard's cap 402 means no fresh key can be created: {text!r}"
    assert "Settings → Setup guide" in text, \
        f"#3428 P2-7: the cap arm still names the setup guide: {text!r}"
