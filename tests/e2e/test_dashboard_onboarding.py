"""#1997 (W1) onboarding wizard e2e (RUN_DASHBOARD_E2E opt-in, two-origin harness).

Journey coverage: the 4 HUMAN wizard steps (org-create →
fork card → connect-consent → done), the fork checkpoint (self + build /
catalog-presented), the connect step's key affordances, and the re-entry card.
The done step exits WITHOUT patching onboarding_complete (accept-and-drop).

#2710 / #2711 / #2755 / #2756 (2026-09-09 connect-step fixes) brought this spec
to the SHIPPED post-#2698 UI and added the owner/admin connect branch:
  - the connect step has 6 harness tabs (ChatGPT is key-less OAuth and is
    filtered OUT of the wizard tab row), NOT the pre-#2698 7;
  - the dead pre-#2698 "Copy setup" button is gone — setup content is a
    WizardPromptCard with its own Copy control;
  - an owner/admin who lands on the connect step with no in-memory key gets an
    in-flow mint CTA (never-expiring) + a paste escape, and leaving the wizard
    must NOT pop the shared key-create modal (either exit path);
  - members still get ONLY the paste escape (the known, separately-owned UX
    gap — asserted here so a future fix is a deliberate change);
  - the shown-once key row must not overflow at any phone width;
  - WizardPromptCard must not nest interactive elements, and a drag-select
    inside it must not overwrite the clipboard;
  - Codex has a CLI/Desktop surface toggle and Desktop reaches
    ~/.codex/config.toml.

Test-review hardening (2026-09-10): mock `role`/`mint_status`/`key_rows` cover
the paste REFUSAL paths and the 402 mint cap (a rejected paste must never
advance, and a capped mint must keep the paste escape), the mint-free exit is
asserted for BOTH wizard exits, the connect step's own advance is exercised
(harness-connected checkpoint), the drag-select check proves a selection was
actually made, the clipboard assert compares against the card's own text, the
390px check is bracketed across widths with an over-long token and covers the
build fork's FOURTH key row, and the catalog check is backed by a registry-only
mock name (not the offline fallback).

**CI lane (known gap — issue filed):** this spec is opt-in (`RUN_DASHBOARD_E2E`)
and is NOT wired into `.github/workflows/ci.yml` — its `dashboard-e2e` step runs
only `test_keys_table_mixed.py` + `test_graphs_management.py`. On CI the only
automated guard for these four fixes is the static source-scan tripwire
(`website/apps/dashboard/src/wizardConnectTripwire.test.js`), which cannot
observe a runtime stray modal or a clipboard overwrite. Run this spec locally
against a fresh `dist/` whenever the connect step changes.
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
    _goto_local_dashboard,
    _preflight_local_servers,
    _proxy_body,
    _seed_local_session_cookie,
)


@pytest.fixture(scope="module", autouse=True)
def _local_preview_servers() -> None:
    """#2744: fail fast (one clear error) when :8788/:8790 are not serving."""
    _preflight_local_servers()

# #2698: ChatGPT is key-less OAuth — deliberately NOT a wizard harness tab.
WIZARD_HARNESS_TABS = ["Claude Code", "Claude Desktop", "Claude Web",
                       "Codex", "Cursor", "Pi"]

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
          mint_error_detail: str = "API key cap reached") -> dict:
    """Route harness: the API mocks for the wizard journey. Returns the
    capture dict ({objects, points, state_patches, org_create, checkpoint,
    mint}).

    `role` mirrors the /v1/teams membership role — the connect step branches on
    it (owner/admin → harness tabs + prompt cards; member → paste escape only).
    Omitting it (the pre-#2710 shape) is the MEMBER path.
    `key_rows` is the org's key table — durableConnectKey resolves a pasted
    plaintext against it (so a refusal case is just a differently-shaped row).
    `mint_status` fails POST /v1/team/keys (402 = the tier cap, the only
    reachable non-transport mint failure).
    """
    cap = {"objects": [], "points": [], "state": [], "org_create": [],
           "checkpoint": [], "mint": [], "capabilities": 0}
    seed_objects = seed_objects or [{"id": "obj-1", "name": "Onboarding Test", "objectKind": "project", "status": "in_progress"}]
    rows = key_rows if key_rows is not None else []

    team_row = {"team_id": "team_o", "name": "Onboarding Test"}
    if role is not None:
        team_row["role"] = role

    def handle(route):
        url = route.request.url
        method = route.request.method
        # #1828: loadAll pins ?team_id= on overview reads — match on the
        # query-stripped path so /v1/team/keys?team_id=… still resolves.
        path = url.split("?", 1)[0]
        if "api.premiselabs.co" in url:
            if path.endswith("/v1/teams") and method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps([team_row]))
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
                              body=json.dumps({**team_row, "tier": "free", "graph_ready": True,
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
            # #1997 (W1): fork set-once + catalog-presented checkpoint writes.
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
    # #2744: the DOCUMENT always loads from the local committed-dist preview.
    _goto_local_dashboard(page)
    expect(page.locator("body")).to_contain_text("Continue setup", timeout=20_000)
    page.get_by_role("button", name="Continue setup").click()
    # STEP 0: create/join org — an account that already holds an org sees a
    # read-only summary (never a second mint, #2323) and advances.
    expect(page.locator("body")).to_contain_text("Create your Organization", timeout=10_000)
    expect(page.locator("body")).to_contain_text("You're set up in", timeout=5_000)
    page.get_by_role("button", name="Continue →").click()
    # STEP 1: fork card (was step 2 before orientation removal).
    expect(page.locator("body")).to_contain_text("Choose how you'll use Tortoise", timeout=10_000)


def _walk_to_connect(page: Page) -> None:
    """The shared prefix of every journey: re-entry → step 0 → fork SELF →
    connect. The account already holds an org (_wire's team_row)."""
    _walk_to_fork(page)
    page.get_by_role("button", name="Use it for your own agents").click()
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
    expect(page.locator(".harness-tab")).to_have_count(0, timeout=15_000)
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

    #2710: this journey is the MEMBER row (no role) — members get the paste
    escape ONLY (no harness tabs, no prompt cards, and NOT the owner/admin
    mint CTA, which would 403 server-side). The owner/admin connect branch is
    covered separately below."""
    _seed_cookie(page, "u-onb")
    cap = _wire(page, key_rows=[DURABLE_ROW])
    _walk_to_connect(page)
    # MEMBER: no harness tabs (the known, separately-owned "members get no
    # setup instructions" gap) — only the paste escape.
    assert page.locator(".harness-tab").count() == 0, \
        "members must not see harness tabs (owner/admin-only surface)"
    # The mint CTA is owner/admin render-gated (POST /v1/team/keys is
    # _require_owner_admin server-side) — a member must never see a 403 button.
    assert page.get_by_role("button", name="Create an API key").count() == 0, \
        "members must not see the owner/admin mint CTA"
    expect(page.get_by_role("button", name="Use this key")).to_be_visible(timeout=5_000)
    page.get_by_label("Paste an API key").fill(PASTED_KEY)
    page.get_by_role("button", name="Use this key").click()
    # The pasted durable key is accepted (row-truth match) → the wizard can
    # advance. No prompt card renders for a member (the documented gap: the
    # member path has no setup instructions at all).
    expect(page.get_by_role("button", name="Continue to dashboard")).to_be_visible(timeout=5_000)
    assert page.locator(".wizard-prompt-card").count() == 0, \
        "a member must get NO setup prompt card (no harness instructions)"
    page.get_by_role("button", name="Skip for now").click()
    # STEP 3: done — agent takes over; NO onboarding_complete PATCH (the
    # node's gate owns completion; accept-and-drop).
    expect(page.locator("body")).to_contain_text("You're all set", timeout=10_000)
    # the done step's exit (wizardComplete) — scoped: the header carries a
    # same-named 'Open my dashboard →' escape.
    page.locator(".wizard-actions").get_by_role("button", name="Open my dashboard →").click()
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
    on the dashboard after exit. Now: the shipped 6 harness tabs render, the
    mint CTA mints a NEVER-expiring key (the connect step's own Never-only
    contract), the prompt card renders, the step's OWN advance writes the
    harness-connected checkpoint, and the HEADER exit leaks no modal."""
    _seed_cookie(page, "u-owner")
    cap = _wire(page, role="owner")
    _walk_to_connect(page)

    # #2698 shipped tab row: 6 tabs, ChatGPT (key-less OAuth) filtered out.
    tab_names = page.locator(".harness-tab").all_inner_texts()
    assert tab_names == WIZARD_HARNESS_TABS, f"connect-step tab row drifted: {tab_names}"
    assert "ChatGPT" not in tab_names, "ChatGPT is not a wizard harness tab (#2698)"

    # The promised path exists: the sentence AND its affordance.
    expect(page.locator("body")).to_contain_text("Create an API key to see the setup prompt.")
    expect(page.get_by_role("button", name="Create an API key")).to_be_visible()
    # Pill clicks are display-mode toggles — assert the CLICK DID ITS JOB (the
    # mode actually switched) rather than a dialog count that cannot change
    # inside the wizard tree (test-review P2: that assertion was vacuous).
    separate_pill = page.get_by_role("button", name="Key separate from prompt")
    included_pill = page.get_by_role("button", name="Key included in prompt")
    separate_pill.click()
    assert "active" in (separate_pill.get_attribute("class") or ""), \
        "the 'separate' pill must become the selected mode (pure toggle, no modal)"
    assert "active" not in (included_pill.get_attribute("class") or "")
    # back to the default mode so the prompt embeds the key (the documented
    # "easiest" path); both pill states are pure toggles.
    included_pill.click()
    assert "active" in (included_pill.get_attribute("class") or "")

    _mint_from_connect(page)
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
    # #2710's objective is COMPLETING the step in flow, so take the connect
    # step's OWN advance (not Skip) and prove it writes the checkpoint.
    page.get_by_role("button", name="I've set it up — Continue →").click()
    expect(page.locator("body")).to_contain_text("You're all set", timeout=10_000)
    assert any(c.get("step") == "harness-connected" for c in cap["checkpoint"]), \
        f"the connect step's advance must write the harness-connected checkpoint: {cap['checkpoint']}"
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
    expect(page.locator("body")).to_contain_text("You're all set", timeout=10_000)
    page.locator(".wizard-actions").get_by_role("button", name="Open my dashboard →").click(timeout=15_000)
    _expect_left_wizard(page)
    assert page.locator("[role=dialog]").count() == 0, \
        "#2710: the wizardComplete exit leaked the shared key-create modal"
    assert page.get_by_text("Create new API key").count() == 0, \
        "#2710: the wizardComplete exit must not surface the queued create-key modal"


def test_owner_connect_step_paste_toggle_reaches_prompt_card(page: Page) -> None:
    """#2710: the owner/admin paste escape — an owner who holds a durable key's
    plaintext can paste it without leaving the wizard."""
    _seed_cookie(page, "u-owner2")
    cap = _wire(page, role="admin", key_rows=[DURABLE_ROW])
    _walk_to_connect(page)
    # No in-memory key → the mint CTA + the paste toggle are both offered.
    expect(page.get_by_role("button", name="Create an API key")).to_be_visible()
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
    page.get_by_role("button", name=re.compile("Create an API key for")).click()
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


@pytest.mark.parametrize("tab,sync_text", [
    ("Claude Desktop", "Open Claude Desktop → Settings → Developer → Edit Config"),
    ("Claude Web", "Go to claude.ai → Settings → Connectors → Add custom connector"),
])
def test_owner_no_key_affordance_on_manual_harness_tabs(page: Page, tab: str, sync_text: str) -> None:
    """#2710 (code-review P1): the two MANUAL harness tabs must not dead-end.

    Pre-fix they kept a `YOUR_API_KEY` placeholder in the config block plus a
    Copy button whose handler wrote `harnessKey` — an empty string with no key —
    so an owner/admin on those tabs had no mint CTA, no paste escape, and a Copy
    control that silently clobbered the clipboard.

    The tab click is synchronised on a MANUAL-TAB-UNIQUE instruction string
    first: the default tab (claude) already renders an identical affordance, so
    an unsynchronised read would pass against the previous tab's DOM."""
    _seed_cookie(page, "u-manual-" + tab.split()[-1].lower())
    _wire(page, role="owner", key_rows=[DURABLE_ROW])
    _walk_to_connect(page)
    page.get_by_role("button", name=tab, exact=True).click()
    expect(page.locator("body")).to_contain_text(sync_text, timeout=10_000)
    # The no-key state offers the SAME in-flow path as the agent-driven tabs.
    expect(page.get_by_role("button", name="Create an API key")).to_be_visible(timeout=5_000)
    harness = page.locator("body")
    assert "YOUR_API_KEY" not in harness.inner_text(), \
        f"{tab}: the placeholder key must not render"
    assert page.locator("code", has_text="…").count() == 0, \
        f"{tab}: no fake '…' key row"
    assert page.locator(".wizard-prompt-card").count() == 0, \
        f"{tab}: the config block must not render before a key exists"
    # The paste escape lands a real key, and the config block then renders it.
    page.get_by_role("button", name="I already have a key — paste it instead").click()
    page.get_by_label("Paste an API key").fill(PASTED_KEY)
    page.get_by_role("button", name="Use this key").click()
    expect(page.locator("code", has_text=PASTED_KEY)).to_be_visible(timeout=5_000)
    assert "YOUR_API_KEY" not in page.locator("body").inner_text(), \
        f"{tab}: the placeholder must be replaced by the real key"


def test_codex_desktop_hides_the_key_mode_pills(page: Page) -> None:
    """#2756 (code-review P1): the Codex Desktop surface embeds the key in the
    config block by construction, so the "Key separate from prompt" promise
    cannot be honored there — the pills AND the separate key row must be gone on
    that surface, and both must come back on the CLI surface."""
    _seed_cookie(page, "u-codex-mode")
    _wire(page, role="owner")
    _walk_to_connect(page)
    _mint_from_connect(page)
    pills = page.get_by_role("button", name=re.compile("Key (included in|separate from) prompt"))
    group = page.get_by_role("group", name="Codex setup surface")
    page.get_by_role("button", name="Codex", exact=True).click()
    expect(group).to_be_visible(timeout=10_000)  # sync: the Codex surface rendered
    expect(pills).to_have_count(2, timeout=5_000)
    # "separate" on the CLI surface shows the separate key row and keeps the key
    # OUT of the prompt card.
    page.get_by_role("button", name="Key separate from prompt").click()
    expect(page.locator("code", has_text=MINTED_KEY)).to_be_visible(timeout=5_000)
    # Desktop: the pills and the separate row go away; the key lives in the
    # config block instead.
    page.get_by_role("button", name="Desktop (no terminal)").click()
    expect(group.get_by_role("button", name="Desktop (no terminal)")).to_have_attribute("aria-pressed", "true")
    expect(pills).to_have_count(0, timeout=5_000)
    assert page.locator("code", has_text=MINTED_KEY).count() == 0, \
        "the separate key row must not sit beside a key-embedding Desktop block"
    expect(page.locator(".wizard-prompt-card").first).to_contain_text(MINTED_KEY, timeout=5_000)
    # … and back to CLI.
    page.get_by_role("button", name="CLI (terminal)").click()
    expect(pills).to_have_count(2, timeout=5_000)


def test_codex_desktop_toggle_reaches_config_toml_instructions(page: Page) -> None:
    """#2756: Codex has two surfaces (CLI needs a terminal; Desktop does not).
    Selecting the Codex tab must offer the surface toggle, and Desktop must
    reach the ~/.codex/config.toml instructions — pre-fix the toggle's state
    and the `codexDesktop` copy in harnesses.js were dead, so a Desktop user
    got the CLI command.

    The assertion pins UNIVERSAL_COMMAND.codexDesktop's distinctive stanza
    (`[mcp_servers.tortoise]`), not just the intro sentence that shares the
    branch (test-review P1)."""
    _seed_cookie(page, "u-codex")
    _wire(page, role="owner")
    _walk_to_connect(page)
    _mint_from_connect(page)
    page.get_by_role("button", name="Codex", exact=True).click()
    group = page.get_by_role("group", name="Codex setup surface")
    expect(group).to_be_visible(timeout=5_000)
    expect(page.get_by_role("button", name="Desktop (no terminal)")).to_be_visible()
    # CLI (default) is the terminal path — no config.toml, no TOML stanza.
    cli_text = page.locator(".wizard-prompt-card").first.inner_text()
    assert "config.toml" not in cli_text, \
        "the Codex CLI variant must not claim the Desktop config file"
    assert "[mcp_servers.tortoise]" not in cli_text, \
        "the Codex CLI variant must not render the Desktop TOML block"
    page.get_by_role("button", name="Desktop (no terminal)").click()
    card = page.locator(".wizard-prompt-card").first
    expect(card).to_contain_text("~/.codex/config.toml", timeout=5_000)
    assert "[mcp_servers.tortoise]" in card.inner_text(), \
        "the Desktop variant must render UNIVERSAL_COMMAND.codexDesktop (the TOML block)"
    # The Desktop copy control uses the harness's own label (HARNESS_COPY_LABEL).
    expect(card.get_by_role("button", name="Copy instructions")).to_be_visible()
    # The toggle is reversible and scoped to the Codex tab.
    page.get_by_role("button", name="CLI (terminal)").click()
    assert "config.toml" not in page.locator(".wizard-prompt-card").first.inner_text(), \
        "switching back to CLI must restore the terminal instructions"
    page.get_by_role("button", name="Pi", exact=True).click()
    assert page.get_by_role("group", name="Codex setup surface").count() == 0, \
        "the Codex surface toggle must not leak onto other harness tabs"


def test_first_timer_wizard_build_fork_marks_catalog(page: Page) -> None:
    """#1997 (W1, review P1 regression): picking the BUILD fork on the fork
    card must mark catalog-presented via the checkpoint (the render-time
    effect cannot observe the fresh pick — React batches the fork-chosen +
    advance states — so the handler fires it directly). The build-fork gate
    (harness-connected + first-points-filed + catalog-presented) must be
    evaluable. #2323: the org-holding journey never mints a second org.

    The catalog pin is deliberately two-sided (#2763): the fork card renders the
    STATIC placeholder because the registry fetch is gated on the connect step
    while the catalog renders one step earlier — the fetch is counted so the
    mock is genuinely exercised, and the assertion flips to the registry-only
    name when #2763 lands."""
    _seed_cookie(page, "u-bld")
    cap = _wire(page, role="owner")
    # #2744: the DOCUMENT always loads from the local committed-dist preview.
    _goto_local_dashboard(page)
    expect(page.locator("body")).to_contain_text("Continue setup", timeout=20_000)
    page.get_by_role("button", name="Continue setup").click()
    # STEP 0: create/join org (orientation removed per epic #2534).
    expect(page.locator("body")).to_contain_text("Create your Organization", timeout=10_000)
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
    assert any(c.get("fork") == "build" for c in cap["checkpoint"]), \
        f"build fork not checkpointed: {cap['checkpoint']}"
    assert any(c.get("step") == "catalog-presented" for c in cap["checkpoint"]), \
        f"catalog-presented not marked: {cap['checkpoint']}"
    assert cap["org_create"] == [], f"#2323 violated: org_create fired: {cap['org_create']}"
