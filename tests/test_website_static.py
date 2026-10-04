"""Static parity tests for the pricing surface — issue #749.

The pricing section is the biggest revenue surface and had NO functional
test. product.html claims its `PRICING` object is a "data mirror of
product/pricing.json — single source", but a broken shape (missing key, NaN
price, undefined popular) throws mid-map, the grid renders blank, and CI stays
green (anti-#728).

Repo-local, zero network, stdlib only (the test_waitlist_form.py pattern):

1. Parse the PRICING object out of website/product.html (lightweight JS-object
   → JSON normalizer; no JS engine).
2. Assert every tier in product/pricing.json exists in the HTML mirror with
   price/popular/features/excluded and matching numeric limits
   (None → "∞" mapping).
3. Reimplement fmtPrice() math in Python → annual contract
   (per-month × 12 == billed total — the rounding-once invariant),
   price 0 → 'Free forever', 20% discount.
4. Element inventory (anti-#728): renderPricing/renderSelfHost/setBilling
   reference #pricing-grid / #pricing-usage-line / #selfhost-section /
   #btn-monthly / #btn-annual, and those element ids exist in the page; init
   wiring honors display.annual_default.
5. Unshipped-capability claim pin (#3432): the `04 · Automate` beat's copy is
   pinned (the beat advertised webhook delivery as available while
   product/pricing.json marks it "planned" and no outbound webhook ships), and
   the beat must carry a real anchor to the issue tracking the capability.

Run:  TORTOISE_TEST_CARVE_OUT=1 python -m pytest tests/test_website_static.py -v
      (a URI-less run fails at session setup by construction — epic #1647 P4;
      this file is static, offline and stdlib-only, so the carve-out lane fits.)
"""
from __future__ import annotations

import json
import re
import sys
from html import unescape
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # tests/ and tools/ are not installed packages
    sys.path.insert(0, str(REPO_ROOT))

from tests._html_links import extract_anchor_hrefs  # noqa: E402

PRODUCT_HTML = REPO_ROOT / "website" / "product.html"
PRICING_JSON = REPO_ROOT / "product" / "pricing.json"
# #4336: the dashboard's display-name map — the second hand-maintained copy of
# the tier names (product.html renderPricing() carries the first).
DASHBOARD_PRICING_JS = (REPO_ROOT / "website" / "apps" / "dashboard"
                         / "src" / "pricing.js")

# Internal quota tiers that are NOT public offerings — they must not leak
# onto the pricing page (anon = unclaimed zero-email teams, raised to free
# on claim, #1082). Mirror/parity assertions operate on the public set.
INTERNAL_TIERS = frozenset({"anon"})
PUBLIC_TIERS = frozenset({"free", "solo", "pro", "team"})


# ── JS-object extraction (no JS engine, no network) ─────────────────────────


def _extract_pricing_object() -> dict:
    """Parse the `const PRICING = {...}` object from product.html.

    Raises AssertionError (with the source context) when the block cannot be
    located — a renamed/moved constant must fail loudly, not skip.
    """
    src = PRODUCT_HTML.read_text(encoding="utf-8")
    start = src.index("const PRICING = {")
    obj_start = src.index("{", start)
    end_marker = "let currentBilling"
    obj_end = src.index(end_marker, obj_start)
    block = src[obj_start:obj_end].rstrip()
    # Strip the trailing `};` (block ends with the object's closing brace)
    if block.endswith("};"):
        block = block[:-1]
    return json.loads(_normalize_js_object(block))


def _normalize_js_object(src: str) -> str:
    """Quote bare identifier keys + drop trailing commas so json.loads works.

    Handles nested objects/arrays and string literals (single or double
    quoted, with backslash escapes). Only the object's OWN keys are quoted —
    values pass through verbatim.
    """
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in ('"', "'"):
            quote = c
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == quote:
                    j += 1
                    break
                j += 1
            out.append(src[i:j])
            i = j
        elif c.isalpha() or c in "_$":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            ident = src[i:j]
            k = j
            while k < n and src[k] in " \t\n":
                k += 1
            out.append(f'"{ident}"' if k < n and src[k] == ":" else ident)
            i = j
        elif c == ",":
            k = i + 1
            while k < n and src[k] in " \t\n":
                k += 1
            if k < n and src[k] in "}]":
                i = k  # trailing comma before } or ] — drop it
            else:
                out.append(c)
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


# ── fmtPrice() mirror (website/product.html, verbatim semantics) ────────────


def _fmt_price(price, mode: str, discount_pct: int) -> tuple[str, str]:
    """Python mirror of the page's fmtPrice(): (big, total) display strings.

    Annual: effective PER-MONTH price big, total billed small; round ONCE
    (per-month) then derive the total so the two never disagree
    (e.g. $7/mo × 12 must equal $84, not $86).
    """
    if mode == "annual":
        if price == 0:
            return "$0", "Free forever"
        disc = discount_pct / 100
        per_month = max(1, round(price * 12 * (1 - disc) / 12))
        total = per_month * 12
        return f"${per_month}", f"${total} billed annually"
    return f"${price}", "Billed monthly"


def _render_pricing_fn(html: str) -> str:
    """Extract the renderPricing() function body from product.html."""
    m = re.search(r"function renderPricing\(\) \{.*?\n    \}", html, re.S)
    assert m, "renderPricing() not found in product.html"
    return m.group(0)


def _function_refs(html: str, fn_name: str) -> str:
    m = re.search(rf"function {fn_name}\(.*?\n    \}}", html, re.S)
    assert m, f"{fn_name}() not found in product.html"
    return m.group(0)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. PRICING object parses + tier inventory
# ═══════════════════════════════════════════════════════════════════════════════


class TestPricingObjectShape:
    def test_pricing_json_parses_with_required_structure(self):
        data = json.loads(PRICING_JSON.read_text(encoding="utf-8"))
        for key in ("$schema", "status", "billing", "tiers", "display"):
            assert key in data, f"pricing.json missing top-level key: {key}"
        assert data["status"] == "current"
        assert data["billing"]["model"] == "per-team"
        for tier in ("free", "solo", "pro", "team"):
            assert tier in data["tiers"], f"pricing.json missing tier: {tier}"

    def test_pricing_object_extracts_from_product_html(self):
        pricing = _extract_pricing_object()
        assert set(pricing.keys()) == {"display", "tiers"}
        # Only public tiers render on the pricing page; internal quota tiers
        # (anon — unclaimed zero-email teams, #1082) must not leak.
        assert set(pricing["tiers"].keys()) == PUBLIC_TIERS
        assert not (INTERNAL_TIERS & set(pricing["tiers"].keys()))

    def test_tier_sets_mirror_bidirectionally(self):
        html_tiers = set(_extract_pricing_object()["tiers"].keys())
        json_tiers = set(json.loads(
            PRICING_JSON.read_text(encoding="utf-8"))["tiers"].keys())
        assert html_tiers == json_tiers - INTERNAL_TIERS == PUBLIC_TIERS
        assert not (INTERNAL_TIERS & html_tiers)

    def test_every_tier_has_complete_render_inventory(self):
        """A missing key (price/popular/features/excluded) throws mid-map and
        the grid renders blank — every tier must carry the full inventory."""
        pricing = _extract_pricing_object()
        for name, tier in pricing["tiers"].items():
            for key in ("price", "popular", "features", "excluded",
                        "graphs", "users", "ops", "nodes", "keys", "overage"):
                assert key in tier, f"PRICING.tiers[{name}] missing '{key}'"
            assert isinstance(tier["features"], list) and tier["features"]
            assert isinstance(tier["excluded"], list)
            assert isinstance(tier["popular"], bool)

    def test_exactly_one_popular_tier(self):
        pricing = _extract_pricing_object()
        popular = [n for n, t in pricing["tiers"].items() if t["popular"]]
        assert popular == ["pro"], f"expected exactly pro as popular, got {popular}"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Mirror parity: HTML PRICING ↔ product/pricing.json
# ═══════════════════════════════════════════════════════════════════════════════


def _mirror_data() -> tuple[dict, dict]:
    """Load both sides of the mirror: the HTML PRICING object + pricing.json."""
    html = _extract_pricing_object()
    js = json.loads(PRICING_JSON.read_text(encoding="utf-8"))
    return html, js


class TestMirrorNumericParity:
    def test_price_mirrors_price_usd_monthly(self):
        html, js = _mirror_data()
        for name, tier in js["tiers"].items():
            if name in INTERNAL_TIERS:
                continue  # internal quota tier — not mirrored on the public page
            assert html["tiers"][name]["price"] == tier["price_usd_monthly"], (
                f"tier {name}: HTML price {html['tiers'][name]['price']} != "
                f"pricing.json {tier['price_usd_monthly']}")

    def test_limit_fields_mirror(self):
        """graphs/users/ops/nodes/keys map to pricing.json limits with
        None (unlimited) rendered as '∞'."""
        html, js = _mirror_data()
        field_map = {
            "graphs": "max_graphs_per_team",
            "users": "max_users_per_team",
            "ops": "included_write_ops_per_month",
            "nodes": "max_graph_nodes",
            "keys": "max_api_keys",
        }
        for name, tier in js["tiers"].items():
            if name in INTERNAL_TIERS:
                continue  # internal quota tier — not mirrored on the public page
            for html_key, json_key in field_map.items():
                expected = tier[json_key]
                if expected is None:
                    expected = "∞"
                assert html["tiers"][name][html_key] == expected, (
                    f"tier {name}: HTML {html_key}={html['tiers'][name][html_key]} "
                    f"!= pricing.json {json_key}={tier[json_key]}")

    def test_overage_flag_mirrors(self):
        html, js = _mirror_data()
        for name, tier in js["tiers"].items():
            if name in INTERNAL_TIERS:
                continue  # internal quota tier — not mirrored on the public page
            assert html["tiers"][name]["overage"] is tier["overage"], (
                f"tier {name}: HTML overage {html['tiers'][name]['overage']} "
                f"!= pricing.json {tier['overage']}")

    def test_excluded_mirrors_overage(self):
        """Overage tiers carry no exclusions; non-overage tiers exclude the
        paid overage line."""
        html, js = _mirror_data()
        for name, tier in js["tiers"].items():
            if name in INTERNAL_TIERS:
                continue  # internal quota tier — not mirrored on the public page
            excluded = html["tiers"][name]["excluded"]
            if tier["overage"]:
                assert excluded == [], f"tier {name}: overage tier must not exclude anything"
            else:
                assert excluded == ["Overage"], f"tier {name}: expected Overage exclusion"

    def test_display_mirrors(self):
        html, js = _mirror_data()
        html_disp, js_disp = html["display"], js["display"]
        for key in ("annual_discount_pct", "annual_default",
                    "license_self_hosted", "overage_line"):
            assert html_disp[key] == js_disp[key], (
                f"display.{key} drift: HTML {html_disp[key]!r} != "
                f"pricing.json {js_disp[key]!r}")

    def test_feature_copy_pins_tier_facts(self):
        """Spot-check that the displayed feature strings encode the JSON
        facts (catches a mirror that parses but renders stale copy)."""
        html, js = _mirror_data()
        free = html["tiers"]["free"]
        assert any("1 graph" in f for f in free["features"])
        assert any("2 API keys" in f for f in free["features"])
        assert any("10,000 write ops/mo" in f for f in free["features"])
        pro = html["tiers"]["pro"]
        assert any("Unlimited graphs" in f for f in pro["features"])
        assert any("Usage-based overage" in f for f in pro["features"])
        team = html["tiers"]["team"]
        assert any("invites + RBAC" in f for f in team["features"])
        # JSON-side facts agree (belt and braces)
        assert js["tiers"]["free"]["max_api_keys"] == 2
        assert js["tiers"]["pro"]["max_graphs_per_team"] is None
        assert js["tiers"]["team"]["max_users_per_team"] is None


class TestDisplayLabelParity:
    """#4336: the user-facing tier name lives in TWO hand-maintained maps —
    product.html renderPricing()'s `labels` and the dashboard's
    `TIER_LABELS` (pricing.js). Nothing else ties them together, so a rename
    applied to one and not the other renders two names for one tier.
    (The internal tier KEYS stay `pro` — only the display value changes.)"""

    def _html_labels(self) -> dict:
        fn = _render_pricing_fn(PRODUCT_HTML.read_text(encoding="utf-8"))
        m = re.search(r"const labels = \{([^}]*)\}", fn)
        assert m, "renderPricing() must define a `labels` display-name map"
        return dict(re.findall(r"([A-Za-z_$][\w$]*)\s*:\s*'([^']*)'", m.group(1)))

    def _dashboard_labels(self) -> dict:
        js = DASHBOARD_PRICING_JS.read_text(encoding="utf-8")
        m = re.search(r"export const TIER_LABELS = \{([^}]*)\}", js, re.S)
        assert m, "pricing.js must export TIER_LABELS"
        return dict(re.findall(r"([A-Za-z_$][\w$]*)\s*:\s*'([^']*)'", m.group(1)))

    def test_labels_maps_agree_for_public_tiers(self):
        html_labels = self._html_labels()
        js_labels = self._dashboard_labels()
        for tier in PUBLIC_TIERS:
            assert html_labels.get(tier) == js_labels.get(tier), (
                f"display-label drift for tier {tier}: product.html "
                f"{html_labels.get(tier)!r} != pricing.js {js_labels.get(tier)!r}")

    def test_pro_displays_as_builder_and_key_is_unchanged(self):
        assert self._dashboard_labels()["pro"] == "Builder"
        assert self._html_labels()["pro"] == "Builder"
        # The tier KEY stays `pro` (parity, Stripe ids, pricing.json).
        assert "pro" in _extract_pricing_object()["tiers"]
        assert "pro" in json.loads(
            PRICING_JSON.read_text(encoding="utf-8"))["tiers"]


# ═══════════════════════════════════════════════════════════════════════════════
# 3. fmtPrice() math
# ═══════════════════════════════════════════════════════════════════════════════


class TestFmtPriceMath:
    def _discount(self) -> int:
        html, _ = _mirror_data()
        return html["display"]["annual_discount_pct"]

    def test_monthly_billing(self):
        for price in (0, 9, 25, 149):
            big, total = _fmt_price(price, "monthly", 20)
            assert big == f"${price}"
            assert total == "Billed monthly"

    def test_free_is_forever_free(self):
        big, total = _fmt_price(0, "annual", 20)
        assert (big, total) == ("$0", "Free forever")

    def test_annual_contract_rounding_once_invariant(self):
        """The page's own invariant: round per-month ONCE, derive the total —
        $7/mo × 12 must equal $84, not $86."""
        disc = self._discount()
        for price in (9, 25, 149):
            _, total = _fmt_price(price, "annual", disc)
            billed = int(total.split(" ")[0].lstrip("$"))
            assert billed == max(1, round(price * 12 * (1 - disc / 100) / 12)) * 12

    def test_annual_prices_across_tiers(self):
        disc = self._discount()
        assert disc == 20  # the -20% badge on the page
        cases = {
            0: ("$0", "Free forever"),
            9: ("$7", "$84 billed annually"),    # 9×12×0.8/12 = 7.2 → 7
            25: ("$20", "$240 billed annually"),  # 25×0.8 = 20 exactly
            149: ("$119", "$1428 billed annually"),  # 119.2 → 119
        }
        for price, expected in cases.items():
            assert _fmt_price(price, "annual", disc) == expected

    def test_per_month_never_drops_below_1(self):
        assert _fmt_price(1, "annual", 20)[0] == "$1"
        # Even a 99% discount floor keeps $1/mo (max(1, ...))
        assert max(1, round(1 * 12 * 0.01 / 12)) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 4. renderPricing / setBilling / renderSelfHost element inventory (anti-#728)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRenderElementInventory:
    def test_render_pricing_references_grid_and_usage_line(self):
        html = PRODUCT_HTML.read_text(encoding="utf-8")
        fn = _render_pricing_fn(html)
        assert "getElementById('pricing-grid')" in fn
        assert "getElementById('pricing-usage-line')" in fn
        # Every tier is mapped — a missing tier renders a blank card slot
        for name in ("free", "solo", "pro", "team"):
            assert f"'{name}'" in fn or f'"{name}"' in fn

    def test_render_pricing_has_no_undefined_name_path(self):
        """#4815: `names` and `labels` are separate hand-maintained lists, so a
        tier in one but not the other used to print the literal string
        "undefined" in customer copy — and an empty metered set dangled the
        " — applies to " separator."""
        fn = _render_pricing_fn(PRODUCT_HTML.read_text(encoding="utf-8"))

        # Exactly one name resolver, and it carries the fallback...
        assert "const displayName = (n) => labels[n] ||" in fn, (
            "renderPricing() must resolve display names through one helper "
            "with a fallback")
        # ...so no render site indexes `labels` bare (no fallback → undefined).
        resolver = "(n) => labels[n] ||"
        assert "labels[" not in fn.replace(resolver, "", 1), (
            "a bare `labels[...]` lookup renders `undefined` when the tier is "
            "missing from the map — every use must go through displayName()")
        # The sentence must not render its separator with nothing after it.
        assert "overageTiers.length" in fn, (
            "the overage line must be skipped (not left dangling) when no "
            "tier is metered")

    def test_dashboard_plan_grid_discloses_overage(self):
        """#4815: `planOptions()` computed an `overage` flag and the plan
        grids rendered nothing, so a metered tier was invisible on the card
        the user upgrades from — the card Solo is bought on. The string must
        come from pricing.json's own `display.overage_line`, never a second
        hardcoded copy of the price.

        The assertions are scoped to the LIVE surfaces: `main.jsx` carries a
        SECOND `planOptions()` grid inside the archived
        `LEGACY_WIZARD_ARCHIVED` block (dead code, never rendered), so a
        whole-file substring would pass a disclosure that only ever rendered
        there."""
        js = DASHBOARD_PRICING_JS.read_text(encoding="utf-8")
        main = (REPO_ROOT / "website" / "apps" / "dashboard" / "src"
                / "main.jsx").read_text(encoding="utf-8")

        assert "overageLine: t.overage" in js, (
            "planOptions() must expose the disclosure only for a metered tier")
        assert "pricing.display?.overage_line" in js, (
            "the disclosure must be pricing.json's own display.overage_line")

        # Excise the archived wizard block (same anchor/marker pair
        # overview.test.js derives its A0 slice with) so the render assertions
        # below can only be satisfied by code that actually runs.
        archived_anchor = "LEGACY_WIZARD_ARCHIVED && welcomeOriented && ("
        archived_start = main.index(archived_anchor)
        archived_end = main.index("\n                )}\n", archived_start)
        live = main[:archived_start] + main[archived_end:]

        assert "{p.overageLine && (" in live, (
            "the LIVE Billing plan grid must render the disclosure when the "
            "flag is set — a line rendered only in the archived (dead) grid "
            "is not a disclosure")

        # The paid-new-org purchase dialog commits a metered subscription, so
        # it must show the selected plan's overage line too.
        dialog_start = live.index('id="create-org-title-purchase"')
        dialog_end = live.index("Continue to checkout", dialog_start)
        dialog = live[dialog_start:dialog_end]
        assert "newOrgSelectedPlan?.overageLine && (" in dialog, (
            "the paid-new-org purchase dialog must render the SELECTED "
            "plan's overage line — it commits checkout for a metered "
            "subscription")
        # A bare `overageLine` substring is not enough: the same silent no-op
        # this test exists to prevent is reachable by breaking the SELECTION,
        # which leaves the render textually intact while `newOrgSelectedPlan`
        # is undefined and nothing renders. Pin the predicate and the render
        # variable so a predicate regression reds instead of passing.
        assert ("team?.checkout_price_ids?.[p.tier] === createTeamPlan" in live), (
            "the dialog's selected plan must be resolved by PRICE ID against "
            "checkout_price_ids (the state holds a price id, not a tier key) "
            "— matching on p.tier directly would resolve nothing and render "
            "no disclosure")
        assert "newOrgSelectedPlan.overageLine" in dialog, (
            "the dialog must render the resolved plan's own overageLine")

        assert "per additional 10k" not in main, (
            "the overage price string belongs in pricing.json, not re-typed "
            "in the dashboard")

    def test_set_billing_references_toggle_buttons(self):
        html = PRODUCT_HTML.read_text(encoding="utf-8")
        fn = _function_refs(html, "setBilling")
        for el in ("btn-monthly", "btn-annual"):
            assert f"getElementById('{el}')" in fn

    def test_render_self_host_references_section(self):
        html = PRODUCT_HTML.read_text(encoding="utf-8")
        fn = _function_refs(html, "renderSelfHost")
        assert "getElementById('selfhost-section')" in fn

    def test_all_inventoried_element_ids_exist_in_page(self):
        html = PRODUCT_HTML.read_text(encoding="utf-8")
        for el in ("pricing-grid", "pricing-usage-line", "selfhost-section",
                   "btn-monthly", "btn-annual", "annual-badge"):
            assert f'id="{el}"' in html, f"missing element id: {el}"

    def test_init_wiring_honors_annual_default(self):
        """Init runs setBilling('annual') when display.annual_default is
        true — a flipped default silently prices everyone monthly."""
        html = PRODUCT_HTML.read_text(encoding="utf-8")
        pricing, _ = _mirror_data()
        assert "if (PRICING.display.annual_default) setBilling('annual')" in html
        assert "renderSelfHost();" in html
        # Annual is the default (matches the -20% badge init)
        assert pricing["display"]["annual_default"] is True

    def test_annual_badge_shows_discount(self):
        html = PRODUCT_HTML.read_text(encoding="utf-8")
        pricing, _ = _mirror_data()
        # The badge markup is <span class="badge" id="annual-badge">-20%</span>
        assert f"-{pricing['display']['annual_discount_pct']}%" in html


# ── #1566: welcome.html's provisioning pipeline must stay dead ──


def test_tortoise_decide_skill_ships_the_workflow():
    """#1643 (Task 3): skills/tortoise-decide/SKILL.md exists and AUTHORS the
    decision workflow (options → criteria → findings → edges → mitigations →
    EP ranking) — the onboarding skills primer links a REAL invokable skill."""
    p = Path("skills/tortoise-decide/SKILL.md")
    if not p.exists():
        # The repo's skills/ is a symlink to a local agent-infra checkout
        # (broken on CI) — the skill is committed there; skip with an
        # annotation rather than fail the CI checkout (TORTISE_HOST_CHECK
        # pattern).
        pytest.skip("skills/ symlink not present (agent-infra not checked out)")
    assert p.exists(), "skills/tortoise-decide/SKILL.md missing"
    src = p.read_text()
    for marker in ("tortoise-decide", "options", "criteria", "findings",
                   "IMPL", "NAND", "mitigation", "confidence"):
        assert marker in src, f"skill missing the {marker!r} step"
    # Tool-based (MCP): the skill must reference the graph-write tools, not
    # require a local FalkorDB.
    assert "create_point" in src or "tortoise_create_point" in src, \
        "the skill must be tool-based (MCP write tools)"


def test_welcome_provisioning_pipeline_is_dead_since_1566():
    """#1566 (review P2): welcome.html's provisioning pipeline must STAY dead
    — restoring it would recreate the double-provision surface #1082/#1566
    guard against.

    #3501 removed the session bridge itself, so the pipeline is now dead by
    CONSTRUCTION rather than by having had its symbols stripped out of a
    still-present bridge. The assertion is therefore inverted and widened: the
    bridge must no longer exist at all, and neither may the provisioning
    symbols it used to host.

    Pinning the stronger property matters here — the old form
    (``assert "runSessionBridge" in src``) would now FAIL on the correct
    implementation, and the tempting "fix" of deleting that line would leave
    the provisioning symbols themselves unpinned.
    """
    src = Path("website/apps/dashboard/public/welcome.html").read_text()
    # Comments in welcome.html name the removed markers to explain #3501, so
    # the absence checks must run against comment-stripped source.
    src_code = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    src_code = re.sub(r"/\*.*?\*/", "", src_code, flags=re.S)
    src_code = re.sub(r"^[ \t]*//.*$", "", src_code, flags=re.M)
    for dead in ("provisionViaEdgeFunction", "waitForProvisioning",
                 "revealKeyOnce", "claimStatusGuard",
                 "runSessionBridge"):
        assert dead not in src_code, (
            f"{dead} must not exist — the provisioning surface is dead "
            "(#1566) and its host bridge was removed in #3501"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Unshipped-capability claims on the marketing page (#3432)
# ═══════════════════════════════════════════════════════════════════════════════


def _automate_beat_markup() -> str:
    """The `<section id="beat-automate">…</section>` slice, from the SECTION tag.

    The slice must start at `<section`, not at the `id=` attribute: starting at
    the attribute leaves `id="beat-automate" class="beat workflow">` outside any
    tag for `_beat_text`'s strip, and the pinned text then carries that fragment
    (caught in plan review as a P0). The nested-section assert below is the same
    guard `tests/test_website_docs_consistency.py` carries for `#beat-hero`: this
    slice stops at the FIRST `</section>`, so a claim inside a nested section
    would be invisible to every assertion in this class.
    """
    src = PRODUCT_HTML.read_text(encoding="utf-8")
    marker = 'id="beat-automate"'
    assert marker in src, (
        "the #beat-automate section moved or was renamed — this gate's subject is "
        "gone; re-point it at wherever the Automate claim now lives (#3432)")
    i = src.index(marker)
    start = src.rindex("<section", 0, i)
    section = src[start:src.index("</section>", start)]
    assert "<section" not in section[section.index(">") + 1:], (
        "a <section> is nested inside #beat-automate — this slice stops at the "
        "first </section>, so a claim in the nested section would be invisible "
        "to every assertion in this class (#3432)")
    return section


def _beat_text(markup: str) -> str:
    """Rendered text of a markup slice: tags stripped, entities decoded, spaces collapsed."""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", markup))).strip()


def _automate_paragraph_markup() -> str:
    section = _automate_beat_markup()
    match = re.search(r"<p>.*?</p>", section, re.S)
    assert match, (
        "#beat-automate has no <p>…</p> — the beat was restructured and this "
        "gate's subject changed shape (#3432)")
    return match.group(0)


class TestUnshippedCapabilityClaims:
    """#3432: the `04 · Automate` beat advertised webhook delivery as available.

    It read "Use webhooks to trigger workflows, update systems, or coordinate
    agents…" — present tense — while every source of truth said otherwise:
    `product/pricing.json` marks `features.webhooks` `"planned"` on pro/team and
    `false` elsewhere, no outbound webhook exists in `tortoise/` (every hit is an
    INBOUND receiver — `/webhooks/stripe`, the GitHub/Slack connector servers),
    and `docs/plans/2026-08-08-432-subscriptions-plan.md` lists "webhook
    delivery" as out of scope. #3909 tracks the capability and is explicitly
    deferred, post-beta. Introduced by 8918a7fae (#665), so it was live for ~7
    weeks with nothing able to catch it.

    A TRIPWIRE, NOT A BINDING — six things it does NOT promise, named rather
    than hidden:

    1. The premise reads a `pricing.json` flag, and such flags LAG.
       `features.export` still reads `"planned"` while
       `tortoise/hosted_api.py` ships the export endpoint, so a release that
       never moves the flag leaves this file green and the page stale.
    2. It can therefore also RED for the wrong reason: a pure-doc realignment of
       these schema strings (the follow-up `pricing.json`'s own `_comment`
       describes), or a new unshipped sentinel, trips the premise although
       nothing shipped. A new sentinel is a deliberate allow-list edit, not a
       silence.
    3. The copy pin is a hand-maintained SECOND COPY of a prose claim. An
       availability sentence is not derivable today, so it is pinned rather
       than left unguarded; the durable binding is #5063.
    4. The scope is this beat's **static** markup in this one FILE. A claim added
       to another beat, to another page, to whatever `_redirects` may serve at
       `/product`, or injected at runtime by the page's own GSAP block is all
       green here. Phrase search cannot establish a site-wide property — the
       first sentence of this very beat was found by reading, not searching.
    5. The route test proves a link EXISTS, not that it is reachable: the beat is
       a `pointer-events: none` overlay until GSAP adds `.pe-on`, and it is
       `opacity: 0` without JS. Pre-existing page architecture.
    6. The heading `04 · Automate` is deliberately NOT pinned: it is a narrative
       label, not an availability claim, and freezing it would make a
       renumbering red a test whose message is about shipping status. What is
       pinned is the paragraph.

    The premise is an unshipped ALLOW-LIST rather than a boolean negation
    because `pricing.json`'s own `_comment` describes the shipped transition as
    `"planned"` → `"shipped"`, and `"shipped" is not True`.
    """

    # The paragraph's rendered text, exactly as #3432 leaves it (168 chars).
    EXPECTED_PARAGRAPH_TEXT = (
        "Poll claim events after a cursor to drive your own workflows, systems, "
        "and agents as the graph changes. Webhook push delivery is planned, not "
        "shipped — see issue #3909."
    )
    TRACKER_URL = "https://github.com/daniel-ospina/tortoise/issues/3909"
    UNSHIPPED_VALUES = (False, "planned")

    def test_pricing_json_still_marks_webhooks_unshipped(self):
        """Premise. Reds when webhooks leave the unshipped set, and en route.

        Three branches when it fires: webhooks shipped (fix the page and this
        class), the flag was realigned/newly sentineled while nothing shipped
        (fix the flag, or widen the allow-list deliberately and say which
        sentinel), or a tier lost/renamed the key.
        """
        tiers = json.loads(PRICING_JSON.read_text(encoding="utf-8"))["tiers"]
        observed = {}
        for tier, spec in tiers.items():
            features = spec.get("features")
            observed[tier] = (features.get("webhooks")
                              if isinstance(features, dict) else features)
        shipped = sorted(tier for tier, value in observed.items()
                         if value not in self.UNSHIPPED_VALUES)
        assert not shipped, (
            f"product/pricing.json no longer marks webhooks unshipped for "
            f"{ {t: observed[t] for t in shipped} }. If webhooks SHIPPED, the "
            f"#3432 pin below is now WRONG — state availability in the beat and "
            f"update EXPECTED_PARAGRAPH_TEXT. If the flag moved only to realign "
            f"the pricing-schema strings, or a new unshipped sentinel was "
            f"adopted, fix the flag — or widen the allow-list deliberately and "
            f"name the sentinel. Do not widen it to silence this."
        )

    def test_automate_beat_paragraph_is_pinned(self):
        """The paragraph is pinned so a paraphrase cannot slip past.

        A token check ("does 'webhook' appear, and is 'planned' somewhere in the
        beat?") passes on `Use webhooks to trigger workflows… planned`, which is
        still an availability claim. Equality does not.
        """
        assert _beat_text(_automate_paragraph_markup()) == self.EXPECTED_PARAGRAPH_TEXT, (
            "the 04 · Automate paragraph changed. Before updating this pin, "
            "answer: does the new copy state anything in the present tense that "
            "the product does not do? The shipped surface is the PULL path "
            "(GET /v1/events, sdk.events_poll, tortoise_events_poll); push "
            "delivery is #3909 and is not shipped."
        )

    def test_automate_beat_links_the_capability_tracker(self):
        """The roadmap claim must carry its route — text alone does not link.

        Asserted on PARSED anchor hrefs, not a substring of the markup: a
        substring check stays true when the URL is parked in an HTML comment, in
        `data-href=`, or in `title="…"`. `extract_anchor_hrefs`
        (tests/_html_links.py) returns only rendered anchors, and its own tests
        pin all three of those rejections.
        """
        assert self.TRACKER_URL in extract_anchor_hrefs(_automate_beat_markup()), (
            "the 04 · Automate beat no longer links the issue that tracks the "
            "capability (#3909). The roadmap claim must be reachable — that is "
            "the point of making it a claim a reader can check (#3432)."
        )


