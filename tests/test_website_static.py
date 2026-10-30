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
5. Copy pin for the `04 · Automate` beat (#3432, #8466): the paragraph is
   pinned so a paraphrase cannot slip past. The availability premise that used
   to sit over it is retired — outbound webhook push shipped (#2081, PR #8104)
   — see `TestAutomateBeatCopy`.
6. `/investors` Progress cell order + the owner's sanctioned demand headline
   (PR #9283): the section must OPEN on what exists (`Product & distribution`
   left of `Early demand`), the headline bullet is pinned verbatim — curly
   quotation marks included — and the retired `20+ prospective beta testers`
   line must stay gone. The verbatim quotation is an ACCEPTED RISK recorded by
   the owner, not a defect to "fix" — see `TestInvestorsProgressCopy`.

7. Product-root marker contract (PR #9652 review, P1): the string that proves
   the tortoise host serves product.html at `/` is pinned in TWO surfaces that
   drift independently — `PRODUCT_ROOT_MARKER` in
   `tests/e2e/test_legal_pages.py` (exercised by the pre-merge `legal-e2e` job
   and by post-deploy `verify-legal`) and the `grep -qF` in the post-deploy DNS
   preflight (`.github/workflows/deploy-pages.yml`), which NO pre-merge leg
   reads. Both must be structural (not hero prose) and must agree; see
   `TestProductRootMarkerContract`.

8. Company-root marker contract (PR #9813 review, P1): the SAME contract one
   page over. `premiselabs.co/` is pinned by `COMPANY_ROOT_MARKER`
   (`data-page="company"` on index.html's `<html>`), because the pin it
   replaced was the company hero's prose — which PR #9813's hero rewrite
   deleted, reddening the served-root suite on a correct page; see
   `TestCompanyRootMarkerContract`.

9. Document outline (PR #9813 review, P2): no page may skip a heading level
   (h1 -> h3), and the company front page must keep a real `h2` for the
   Tortoise section; see `TestPageHeadingOutline`.

10. Short-viewport beats (PR #9813 review, P2): the beats whose content is
    taller than a landscape phone (`#beat-cta`, `#beat-tortoise` on index.html,
    `#beat-hero` on product.html) are fixed full-height overlays with centred
    content, so each must be covered by an `@media (max-height: ...)` rule that
    lets the stack scroll instead of clipping — AND must be able to RECEIVE the
    gesture, which `pointer-events: none` on `.beat` otherwise makes
    impossible; see `TestShortViewportBeatsScrollInsteadOfClipping`.

Run:  TORTOISE_TEST_CARVE_OUT=1 python -m pytest tests/test_website_static.py -v
      (a URI-less run fails at session setup by construction — epic #1647 P4;
      this file is static, offline and stdlib-only, so the carve-out lane fits.)
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
from html import unescape
from itertools import pairwise
from pathlib import Path

# `pytest` is deliberately not imported (#8066): this file's only use was the
# `pytest.skip` that made the tortoise-decide gate green-while-blind.

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # tests/ and tools/ are not installed packages
    sys.path.insert(0, str(REPO_ROOT))

PRODUCT_HTML = REPO_ROOT / "website" / "product.html"
PRICING_JSON = REPO_ROOT / "product" / "pricing.json"
# The investor one-pager (PR #9283): its Progress section carries owner copy and
# a left/right cell order that is pure DOM order — see TestInvestorsProgressCopy.
INVESTORS_HTML = REPO_ROOT / "website" / "investors.html"
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


# ═══════════════════════════════════════════════════════════════════════════════
# 4b. Static pricing fallback — the section must not be EMPTY without JS
# ═══════════════════════════════════════════════════════════════════════════════


def _served(markup: str) -> str:
    """Markup a READER actually gets: HTML comments, `<script>` and `<style>`
    bodies removed — including an UNTERMINATED comment opener, which swallows
    the rest of the document in a browser.

    Without this the gate is satisfied by markup nobody receives — wrapping the
    static cards in `<!-- … -->` (the natural way to disable the fallback while
    debugging) would leave every assertion below green while the no-JS reader is
    back to an empty grid, which is the failure this section exists to catch.
    Same lesson as `tests/_html_links.py` (#3436).
    """
    markup = re.sub(r"<!--.*?-->", " ", markup, flags=re.S)
    markup = re.sub(r"<script\b.*?</script>", " ", markup, flags=re.S | re.I)
    markup = re.sub(r"<style\b.*?</style>", " ", markup, flags=re.S | re.I)
    return re.sub(r"<!--.*", " ", markup, flags=re.S)


def _pricing_section() -> str:
    """The `#pricing-section` region as a reader receives it.

    Sliced on the STRIPPED document (not on a sentinel comment), so a comment
    that disables part of the section removes it from this gate too. Stops at
    the first `</section>`, so the nested-section guard below keeps a claim in a
    nested section from being invisible here.
    """
    src = _served(PRODUCT_HTML.read_text(encoding="utf-8"))
    marker = 'id="pricing-section"'
    assert marker in src, (
        "website/product.html lost #pricing-section — this gate's subject moved")
    start = src.index(marker)
    section = src[start:src.index("</section>", start)]
    assert "<section" not in section[section.index(">") + 1:], (
        "a <section> is nested inside #pricing-section — this slice stops at the "
        "first </section>, so anything after it would be invisible to every "
        "assertion here. Re-scope this gate.")
    return section


def _static_pricing_grid() -> str:
    """The static cards: between `#pricing-grid` and the usage line."""
    section = _pricing_section()
    start = section.index('id="pricing-grid"')
    end = section.index('id="pricing-usage-line"')
    assert end > start, "#pricing-usage-line no longer follows #pricing-grid"
    return section[start:end]


def _render_facts(fn: str) -> dict:
    """The render's OWN literals, read out of `renderPricing()`.

    The point is that the static markup is compared against the render — not
    against a second hand-typed copy of the render in this file. Anything the
    static cards share with the render (destination, price suffix, CTA class,
    exclusion marker, badge) is extracted here, so a funnel change on the
    render side fails here instead of silently desynchronizing the no-JS copy.
    """
    cta = re.search(
        r"""<a href="([^"]+)" class="btn\$\{name === '([a-z]+)' \? '([^']+)' : ''\}">\$\{cta\}</a>""",
        fn)
    assert cta, "renderPricing()'s CTA template changed shape — re-point this gate"
    suffix = re.search(r'<div class="price">\$\{fmtPrice\(t\.price\)\.big\}<span>([^<]*)</span></div>', fn)
    assert suffix, "renderPricing()'s price template changed shape — re-point this gate"
    excl = re.search(
        r'<div class="feature"><span class="([a-z]+)">([^<]*)</span> <span class="muted">\$\{f\}</span></div>',
        fn)
    assert excl, "renderPricing()'s exclusion template changed shape — re-point this gate"
    tag = re.search(r"'<span class=\"popular-tag\">([^<]*)</span>'", fn)
    assert tag, "renderPricing()'s 'Most popular' badge literal moved"
    cta_expr = re.search(r"const cta = name === '([a-z]+)' \? '([^']*)' : '([^']*)';", fn)
    assert cta_expr, "renderPricing()'s CTA copy changed shape"
    sep = re.search(
        r"\$\{PRICING\.display\.overage_line\}([^$]*)\$\{overageTiers\.join\(", fn)
    assert sep, "renderPricing()'s overage sentence changed shape — re-point this gate"
    return {
        "cta_href": cta.group(1),
        "free_key": cta_expr.group(1),
        "free_cta": cta_expr.group(2),
        "paid_cta": cta_expr.group(3),
        "cta_secondary_class": cta.group(3).strip(),   # the `btn <x>` suffix
        "cta_class_key": cta.group(2),
        "price_suffix": suffix.group(1),
        "excluded_class": excl.group(1),
        "excluded_glyph": excl.group(2),
        "popular_tag": _beat_text(tag.group(1)),
        "overage_separator": sep.group(1).strip(),
    }


def _static_cards() -> list[dict]:
    """One dict per static `.pricing-card`, in DOM order.

    Every field the JS render writes is captured, including the CTA, the
    "Most popular" badge, the price suffix and the INCLUDED/EXCLUDED marker —
    a gate that read only the prices would stay green while the revenue button
    drifted to `href="/signup"`, or while "Overage" was re-rendered with a green
    check on the one tier that does not include it (both measured).
    """
    grid = _static_pricing_grid()
    cards = []
    for chunk in grid.split('<div class="pricing-card')[1:]:
        features_block = re.search(r'<div class="features">(.*?)</div>\s*<a ', chunk, re.S)
        assert features_block, "a static pricing card has no features block"
        rows = re.findall(
            r'<div class="feature"><span class="(ck|x)">([^<]*)</span>\s*'
            r'<span( class="muted")?>(.*?)</span>',
            features_block.group(1))
        assert rows, "a static pricing card has no feature rows"
        tag = re.search(r'<span class="popular-tag">(.*?)</span>', chunk, re.S)
        price = re.search(r'<div class="price">(.*?)<span>([^<]*)</span>', chunk, re.S)
        assert price, "a static pricing card has no price block"
        cta = re.search(r'<a href="([^"]*)" class="(btn[^"]*)">(.*?)</a>', chunk, re.S)
        assert cta, "a static pricing card has no CTA anchor"
        cards.append({
            "popular": chunk.lstrip().startswith('popular'),
            "popular_tag": _beat_text(tag.group(1)) if tag else None,
            "label": _beat_text(re.search(r"<h3>(.*?)</h3>", chunk, re.S).group(1)),
            "big": _beat_text(price.group(1)),
            "suffix": price.group(2),
            "billing": _beat_text(re.search(r'<p class="billing">(.*?)</p>', chunk, re.S).group(1)),
            "included": [_beat_text(t) for m, _g, _mu, t in rows if m == "ck"],
            "excluded": [_beat_text(t) for m, _g, _mu, t in rows if m == "x"],
            # (marker class, glyph, dimmed?) — the render writes an exclusion as
            # `<span class="x">✕</span> <span class="muted">…</span>`, so a lost
            # `muted` is an exclusion rendered as body copy.
            "excluded_markers": [(m, g, mu.strip())
                                 for m, g, mu, _t in rows if m != "ck"],
            "cta_href": cta.group(1),
            "cta_class": cta.group(2),
            "cta_label": _beat_text(cta.group(3)),
        })
    return cards


def _static_usage_line() -> str:
    m = re.search(r'<p class="pricing-usage" id="pricing-usage-line">(.*?)</p>',
                  _pricing_section(), re.S)
    assert m, "#pricing-usage-line lost its static content"
    return _beat_text(m.group(1))


def _static_selfhost_block() -> str:
    """The `#selfhost-section` element, closed by its OWN `</div>`.

    Sliced by depth rather than "to the end of the section": a link added later
    inside `#pricing-section` (a "Talk to sales" line, say) would otherwise fail
    this gate although the block it tests did not change.
    """
    section = _pricing_section()
    marker = section.index('id="selfhost-section"')
    # Start at the `<div` that OPENS the element, not at the id inside its
    # tag — otherwise the first balanced child (`.actions`) closes the slice.
    start = section.rindex("<div", 0, marker)
    depth = 0
    for m in re.finditer(r"<div\b|</div>", section[start:]):
        depth += 1 if m.group(0) != "</div>" else -1
        if depth == 0:
            return section[start:start + m.end()]
    raise AssertionError("#selfhost-section has no balanced closing </div>")


class TestStaticPricingFallback:
    """The pricing section is the revenue surface, and it used to be EMPTY
    without JavaScript.

    `#pricing-grid`, `#pricing-usage-line` and `#selfhost-section` were rendered
    entirely by the page's inline script, so a reader that does not execute JS —
    a crawler, or an AI evaluator reading the page — saw the Monthly/Annual
    toggle above a blank grid — before this change `#pricing-section` rendered
    `Pricing · <sub> · Monthly · Annual -20% · Use of the service subject to our
    Terms` with `document.querySelectorAll('.pricing-card').length === 0`.

    The static markup is a THIRD copy of the tier facts (the inline script's
    `PRICING` mirror is the second, product/pricing.json the first), so it is
    the copy most likely to rot. This class keeps it honest by deriving every
    expectation — prices, labels, features, CTA copy/href/class, the price
    suffix, the exclusion marker, the badge, the overage sentence, the
    self-host licence and links — from the mirror and from `renderPricing()`/
    `renderSelfHost()` themselves, never by re-typing the value here. Slices are
    read through `_served()`, so markup that no reader receives (comment or
    `<script>`-wrapped) cannot satisfy the gate.
    """

    def _expected(self) -> tuple[list[str], str, dict]:
        """(tier order, the mode the static cards must carry, PRICING mirror)."""
        pricing = _extract_pricing_object()
        fn = _render_pricing_fn(PRODUCT_HTML.read_text(encoding="utf-8"))
        names = re.search(r"const names = \[(.*?)\]", fn, re.S)
        assert names, "renderPricing() must keep its `names` tier order"
        order = re.findall(r"'([a-z]+)'", names.group(1))
        assert order, "could not read renderPricing()'s tier order"
        mode = "annual" if pricing["display"]["annual_default"] else "monthly"
        return order, mode, pricing

    def _labels(self) -> dict:
        fn = _render_pricing_fn(PRODUCT_HTML.read_text(encoding="utf-8"))
        labels_match = re.search(r"const labels = \{([^}]*)\}", fn)
        assert labels_match, "renderPricing() must define a `labels` display-name map"
        return dict(re.findall(r"([A-Za-z_$][\w$]*)\s*:\s*'([^']*)'", labels_match.group(1)))

    def test_render_keys_the_free_cta_from_one_tier(self):
        """The render decides the free CTA's COPY and its CLASS with two separate
        `name === '<tier>'` conditionals. If they disagree, the JS grid paints one
        tier's button as secondary and the static cards the other, with nothing
        red — so pin them to the same key."""
        fn = _render_pricing_fn(PRODUCT_HTML.read_text(encoding="utf-8"))
        facts = _render_facts(fn)
        assert facts["cta_class_key"] == facts["free_key"], (
            "renderPricing() keys the free CTA's class on "
            f"{facts['cta_class_key']!r} but its copy on {facts['free_key']!r}")

    def test_a_reader_can_see_the_grid(self):
        """A hidden grid satisfies every content assertion while the no-JS reader
        is back to an empty-looking section, so the container must stay
        renderable: live region present, no inline hiding."""
        grid = _static_pricing_grid()
        opening = grid[:grid.index(">") + 1]
        assert 'aria-live="polite"' in opening, (
            "#pricing-grid lost its aria-live region (setBilling re-renders it)")
        assert "style=" not in opening, (
            "#pricing-grid carries an inline style — a `display:none` there "
            "hides the static fallback from the reader this gate protects")

    def test_every_public_tier_renders_in_the_source(self):
        order, _, _ = self._expected()
        cards = _static_cards()
        assert len(cards) == len(order), (
            f"#pricing-grid carries {len(cards)} static cards for {len(order)} "
            f"tiers ({', '.join(order)}) — a missing card is a blank slot for a "
            f"no-JS reader")

    def test_static_cards_carry_the_mirror_facts(self):
        order, mode, pricing = self._expected()
        fn = _render_pricing_fn(PRODUCT_HTML.read_text(encoding="utf-8"))
        labels = self._labels()
        facts = _render_facts(fn)
        disc = pricing["display"]["annual_discount_pct"]

        for name, card in zip(order, _static_cards(), strict=True):
            tier = pricing["tiers"][name]
            expected_label = labels.get(name, name.capitalize())
            big, total = _fmt_price(tier["price"], mode, disc)
            assert card["label"] == expected_label, (
                f"static card {name}: label {card['label']!r} != {expected_label!r}")
            assert card["big"] == big, (
                f"static card {name}: price {card['big']!r} != {big!r} ({mode})")
            assert card["suffix"] == facts["price_suffix"], (
                f"static card {name}: price suffix {card['suffix']!r} != the "
                f"render's {facts['price_suffix']!r}")
            assert card["billing"] == total, (
                f"static card {name}: billing line {card['billing']!r} != {total!r}")
            assert card["included"] == tier["features"], (
                f"static card {name}: features drifted from the PRICING mirror "
                f"{card['included']!r} != {tier['features']!r}")
            assert card["excluded"] == tier["excluded"], (
                f"static card {name}: exclusions drifted "
                f"{card['excluded']!r} != {tier['excluded']!r}")
            # An exclusion must LOOK like an exclusion: the render marks it with
            # its own class+glyph, so a green check on a non-included line is a
            # silent claim that the tier includes it.
            assert card["excluded_markers"] == [
                (facts["excluded_class"], facts["excluded_glyph"], 'class="muted"')
            ] * len(tier["excluded"]), (
                f"static card {name}: excluded rows are marked "
                f"{card['excluded_markers']!r}, but the render marks them "
                f"({facts['excluded_class']!r}, {facts['excluded_glyph']!r}, "
                f"muted)")
            assert card["popular"] is tier["popular"], (
                f"static card {name}: popular flag drifted")
            # The CTA is what actually converts.
            assert card["cta_label"] == (
                facts["free_cta"] if name == facts["free_key"] else facts["paid_cta"]), (
                f"static card {name}: CTA label {card['cta_label']!r} != the "
                f"render's copy")
            assert card["cta_href"] == facts["cta_href"], (
                f"static card {name}: CTA points at {card['cta_href']!r}, the "
                f"render at {facts['cta_href']!r}")
            expected_class = ("btn " + facts["cta_secondary_class"]
                              if name == facts["free_key"] else "btn")
            assert card["cta_class"] == expected_class, (
                f"static card {name}: CTA class {card['cta_class']!r} != "
                f"{expected_class!r} — the free CTA loses its secondary styling")
            # The badge is the most conspicuous element on the section; a card
            # marked `popular` without its visible tag is a silent demotion.
            assert card["popular_tag"] == (
                facts["popular_tag"] if tier["popular"] else None), (
                f"static card {name}: 'Most popular' badge is "
                f"{card['popular_tag']!r} but the tier's popular flag is "
                f"{tier['popular']} (the render writes {facts['popular_tag']!r})")

    def test_static_overage_line_matches_the_render(self):
        order, _, pricing = self._expected()
        fn = _render_pricing_fn(PRODUCT_HTML.read_text(encoding="utf-8"))
        labels = self._labels()
        facts = _render_facts(fn)
        metered = [labels.get(n, n.capitalize()) for n in order
                   if pricing["tiers"][n]["overage"]]
        expected = (f"{pricing['display']['overage_line']} "
                    f"{facts['overage_separator']} {', '.join(metered)}") if metered \
            else pricing["display"]["overage_line"]
        assert _static_usage_line() == expected, (
            f"#pricing-usage-line's static text drifted from the render "
            f"({_static_usage_line()!r} != {expected!r})")

    def test_static_self_hosted_block_is_not_empty(self):
        """Same failure mode one section down: `#selfhost-section` was JS-only,
        so a no-JS reader lost the licence summary and the self-host links."""
        fn = _function_refs(PRODUCT_HTML.read_text(encoding="utf-8"), "renderSelfHost")
        block = _static_selfhost_block()
        pricing, _ = _mirror_data()
        assert _beat_text(pricing["display"]["license_self_hosted"]) in _beat_text(block), (
            "the static self-host block must carry pricing.json's licence line")
        heading = re.search(r"<h4>([^<]*)</h4>", fn)
        assert heading, "renderSelfHost()'s heading moved"
        static_heading = re.search(r"<h4>([^<]*)</h4>", block)
        assert static_heading, "the static self-host block lost its <h4> heading"
        # Compared as the block's OWN heading, not as a substring: the actions
        # row carries a `/self-hosted` link, so a substring check would pass on
        # the link text even after the heading itself was rewritten.
        assert _beat_text(static_heading.group(1)) == _beat_text(heading.group(1)), (
            f"static self-host heading {_beat_text(static_heading.group(1))!r} != "
            f"the render's {_beat_text(heading.group(1))!r}")
        # Every link the render writes, in the same order — a missing or
        # retargeted one is invisible to a reader that cannot run the script.
        def links(src: str) -> list[tuple[str, str]]:
            return [(_beat_text(h), _beat_text(t)) for h, t in
                    re.findall(r'href="([^"]+)"[^>]*>(.*?)</a>', src, re.S)]

        assert links(block) == links(fn), (
            "the static self-host links (href + label) no longer match "
            f"renderSelfHost()'s: {links(block)!r} != {links(fn)!r}")
        # The render's own wrappers, so the block is not merely present but
        # styled the same (an unstyled `<p>` in place of `.license` reads as
        # body copy, and a bare `<div>` in place of `.actions` unstacks the row).
        for wrapper in ("class=\"license\"", 'class="actions"'):
            assert wrapper in fn, f"renderSelfHost() lost {wrapper}"
            assert wrapper in block, f"the static self-host block lost {wrapper}"

    def test_static_toggle_highlights_the_same_period_the_cards_price(self):
        """The static cards carry `display.annual_default` figures, so the pill
        highlighted in the SOURCE must be that same period — otherwise a no-JS
        reader sees Annual prices under a lit "Monthly" pill."""
        # `_served()`, for the same reason every other slice uses it: a
        # commented-out pill is markup no reader receives.
        html = _served(PRODUCT_HTML.read_text(encoding="utf-8"))
        _, _, pricing = self._expected()
        want = "annual" if pricing["display"]["annual_default"] else "monthly"
        for period in ("monthly", "annual"):
            m = re.search(rf'<button id="btn-{period}"([^>]*)>', html)
            assert m, f"#btn-{period} markup moved"
            attrs = m.group(1)
            is_active = 'class="active"' in attrs
            assert is_active is (period == want), (
                f"#btn-{period} is {'active' if is_active else 'inactive'} in the "
                f"source but display.annual_default makes {want!r} the default "
                f"— a no-JS reader gets the toggle and the cards disagreeing")
            assert 'aria-pressed="' + ("true" if is_active else "false") + '"' in attrs, (
                f"#btn-{period}'s static aria-pressed disagrees with its own class")


# ── #1566: welcome.html's provisioning pipeline must stay dead ──

# #8066/#8027: the subject is the SERVED copy, NOT the machine-local relay.
# This gate used to read `skills/tortoise-decide/SKILL.md` through the tracked
# `skills` symlink, whose committed blob is an ABSOLUTE path in one developer's
# home (`/Users/danielospina/Documents/GitHub/agent-infra/skills`). The link
# dangles everywhere else, so the test `pytest.skip`ped: the gate was green
# exactly where it could not see the file (measured 2026-10-16 on the CI-style
# checkout — `SKIPPED [1] ... skills/ symlink not present`), and on a machine
# where it did resolve it graded whichever unrelated agent-infra checkout
# happened to be linked. The skip's own explanation was wrong on BOTH kinds of
# machine: `/Users/home/agent-infra/skills` (a live agent-infra, 95/98 of this
# repo's `operations/skills/*` targets present) does NOT carry `tortoise-decide`
# at all — it ships `how-to-use-tortoise`, `tortoise-file-finding` and
# `tortoise-verify-chain`. The product skills are tracked in THIS repo, under
# `website/apps/dashboard/public/skills/`, and those are the bytes the
# onboarding installer serves (`https://app.premiselabs.co/skills/<name>/SKILL.md`).
# Reading the tracked served copy is deterministic and is the pattern
# `tests/test_ingest_conformance.py` adopted for the same relay in #8499 and
# that `tests/test_served_skills.py` already uses. The dead relay itself is
# #8066/#8027 (owner-gated remedy — see #8027's "the choice is the owner's");
# the source↔served parity gap is #4358/#4643.
DECIDE_SKILL = (REPO_ROOT / "website" / "apps" / "dashboard" / "public"
                / "skills" / "tortoise-decide" / "SKILL.md")


def test_tortoise_decide_skill_ships_the_workflow():
    """#1643 (Task 3): the SERVED tortoise-decide skill exists in the tracked
    tree and AUTHORS the decision workflow (options → criteria → findings →
    edges → mitigations → EP ranking) — the onboarding skills primer links a
    REAL invokable skill.

    ⛔ A missing subject is a FAIL, not a skip (#8066): a gate whose subject is
    resolved through a machine-local path has no result on the CI path, and its
    green is an artifact of absence."""
    assert DECIDE_SKILL.exists(), (
        f"the tracked served skill is missing: {DECIDE_SKILL} — the onboarding "
        "primer links it, so this gate has no subject (#8066)")
    src = DECIDE_SKILL.read_text()
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


class TestAutomateBeatCopy:
    """#3432 / #8466: the `04 · Automate` beat's copy, and the facts behind it.

    #3432 pinned this paragraph while the beat advertised webhook delivery in
    the present tense and no outbound webhook existed anywhere in `tortoise/`
    (every hit was an INBOUND receiver — `/webhooks/stripe`, the connector
    servers). That is resolved: push delivery SHIPPED in PR #8104 (epic #2080
    W4 phase 2) — `tortoise/webhook_delivery.py` (subscription registry,
    dispatcher, retry/backoff, delivery receipts, HMAC signing) plus four
    tenant REST routes declared in `config/rest-surface-inventory.yml`, drained
    at the end of a tenant's dream cycle with `TORTOISE_WEBHOOK_DISPATCH` as
    the kill switch. The `planned, not shipped — see #3909` hedge then became
    the stale claim, so #8466 replaced the paragraph and retired the two
    assertions whose subject the shipped capability removed:

    * the `pricing.json` premise test — its own docstring named the failure mode
      that happened ("the premise reads a `pricing.json` flag, and such flags
      LAG"). `product/pricing.json` still reads `features.webhooks: "planned"`
      on pro/team while the delivery layer is on main, so that assertion was
      green while reality moved. Flipping the flag is deliberately NOT part of
      #8466: the file's own `_comment` records that these feature strings lag
      by design pending a pure-doc follow-up, and `per_graph_keys` is in the
      same state.
    * the tracker-link test — an earlier revision of this copy carried a
      roadmap claim that needed a link; the owner's final copy does not link a
      tracker either.

    ⚠️ THE COPY IS THE OWNER'S, VERBATIM, AND ONE OF ITS TWO CLAUSES IS ROADMAP.
    The paragraph reads "Define a confidence threshold or lifecycle events to fire
    webhooks." The LIFECYCLE half ships: `webhook_delivery._validated_types`
    filters the durable `:GraphEvent` types → the 12 `CLAIM_EVENT_TYPES` in
    `tortoise/shared_state/events.py`. The CONFIDENCE-THRESHOLD half does NOT:
    no subscription carries a threshold/condition field, and nothing fires on
    an EP belief crossing a threshold — that trigger is #3909 (OPEN,
    `deferred`, `not-launch-relevant`), and §5.3 of
    `docs/plans/2026-10-11-2081-webhook-delivery.md` says #3909 reuses this
    layer. The owner ruled on 2026-10-13 that the beat carries this copy
    anyway (see the OVERRIDES line on #8466). ⛔ DO NOT "fix" the paragraph by
    deleting the clause as an unshipped-availability claim: that edit was made
    in PR #8470 and reverted by the owner's instruction.

    What is left is a paraphrase guard, and it is deliberately weak: the pin is
    a hand-maintained SECOND COPY of a prose claim (the durable binding is
    #5063), and its scope is this beat's static markup in this one file — a
    claim added to another beat, to another page, or injected at runtime by the
    page's own GSAP block is green here. It is kept because this paragraph has
    already carried a wrong availability claim in each direction: unshipped-but-
    advertised before #3432, shipped-but-disclaimed after.
    """

    # The paragraph's rendered text — the owner's copy, verbatim. Amended by the
    # owner on 2026-10-18 (#8466 pin): "Define confidence threshold" → "Define a
    # confidence threshold" (a missing article), and the second sentence dropped
    # to "Automate decisions and agent actions." (the "investment decisions … and
    # more" tail overclaimed). Both edits are the owner's, made on the same
    # sentence the docstring above calls theirs — not a paraphrase.
    EXPECTED_PARAGRAPH_TEXT = (
        "Define a confidence threshold or lifecycle events to fire webhooks. "
        "Automate decisions and agent actions."
    )

    def test_automate_beat_paragraph_is_pinned(self):
        """The paragraph is pinned so a paraphrase cannot slip past.

        A token check ("does 'webhook' appear, and is 'planned' somewhere in the
        beat?") passed on `Use webhooks to trigger workflows… planned`, which is
        still an availability claim. Equality does not.
        """
        assert _beat_text(_automate_paragraph_markup()) == self.EXPECTED_PARAGRAPH_TEXT, (
            "the 04 · Automate paragraph changed. Before updating this pin, "
            "answer: is the paragraph still the owner's copy VERBATIM? It is an "
            "owner-authored sentence, not a derivable availability statement — "
            "the confidence-threshold clause is roadmap (#3909) and is here by "
            "the owner's 2026-10-13 ruling (OVERRIDES on #8466). Removing that "
            "clause as an unshipped-availability claim was done in PR #8470 and "
            "reverted; do not repeat it. LIFECYCLE-event push delivery does "
            "ship: tortoise/webhook_delivery.py + /v1/context/subscriptions."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 6. /investors Progress: cell order + the owner's sanctioned headline (PR #9283)
# ═══════════════════════════════════════════════════════════════════════════════


def _investors_progress_section() -> str:
    """The `<section id="progress">…</section>` slice, from the SECTION tag.

    Same construction (and the same nested-section guard) as
    `_automate_beat_markup`: the slice must start at `<section`, and this slice
    stops at the FIRST `</section>`, so a claim inside a nested section would be
    invisible to every assertion below.
    """
    src = INVESTORS_HTML.read_text(encoding="utf-8")
    marker = 'id="progress"'
    assert marker in src, (
        "#progress moved or was renamed on website/investors.html — this gate's "
        "subject is gone; re-point it at wherever the Progress section lives "
        "(PR #9283)")
    i = src.index(marker)
    start = src.rindex("<section", 0, i)
    section = src[start:src.index("</section>", start)]
    assert "<section" not in section[section.index(">") + 1:], (
        "a <section> is nested inside #progress — this slice stops at the first "
        "</section>, so a claim in the nested section would be invisible to "
        "every assertion in this class (PR #9283)")
    return section


def _progress_cells() -> list[tuple[str, list[str]]]:
    """`(h3 heading, rendered bullets)` per cell, in DOM order.

    Rendered text (entities decoded) on purpose: the bullet's quotation marks
    are the `&ldquo;`/`&rdquo;` entities, and a pin on the raw markup would pass
    on a rewrite that swapped them for plain ASCII quotes.
    """
    section = _investors_progress_section()
    body = section[section.index(">") + 1:]
    cells = re.findall(r"<h3>(.*?)</h3>\s*<ul[^>]*>(.*?)</ul>", body, re.S)
    return [
        (_beat_text(heading), [_beat_text(li) for li in re.findall(r"<li>.*?</li>", bullets, re.S)])
        for heading, bullets in cells
    ]


class TestInvestorsProgressCopy:
    """PR #9283: the Progress section's cell order and its sanctioned headline.

    TWO failure modes, both measured on that PR.

    1. **The cell order silently reverts.** `.profile` is
       `display: grid; grid-template-columns: 1fr 1fr` with no `direction`
       override, and the only positional rule on the page
       (`#gtm .profile > div:last-child`) is scoped to `#gtm`. So DOM order IS
       the rendered left/right order, and the section must OPEN on what exists
       (`Product & distribution`) rather than on the demand claim. Rendering
       collapses the difference: there is no diff marker, no test, and no CSS
       selector that notices a swap.

    2. **The headline bullet is the owner's copy and must not be paraphrased.**
       A reviewer P2 — "the quotation asserts a verbatim utterance nobody
       recorded" — was actioned by an agent in `fbd706a` as a rewrite of the
       owner's line into indirect speech (`asked when they could use it`). The
       owner reverted the rewrite in `cd864dc` and recorded the P2 as an
       ACCEPTED RISK: the copy is the owner's, and a reviewer finding does not
       authorise an agent to rewrite it. This pin makes the next such rewrite
       fail here instead of shipping, which is the outcome that cost a
       round trip and a wrong audit record (`tortoise#9283`).

    Scope, stated plainly: this is a hand-maintained SECOND COPY of a prose
    claim over one static page's markup. A claim injected at runtime, or moved
    to another page, is green here. It is kept because this specific bullet has
    already been "corrected" once against the owner's copy.
    """

    # Rendered left/right order of the two cells: what exists, then the demand.
    EXPECTED_CELL_ORDER = ("Product & distribution", "Early demand")
    # The owner's sanctioned line, verbatim — entities decoded, so the curly
    # quotation marks are pinned too (ASCII quotes are NOT this copy).
    EXPECTED_DEMAND_HEADLINE = "32 of 34 calls: \u201cwhen can I use it\u201d"
    # What this PR replaced. It must not come back: "prospective" disclaims the
    # noun it modifies and a bare count with no denominator is a headcount.
    RETIRED_LINE = "20+ prospective beta testers"

    def test_progress_opens_on_what_exists(self):
        """Left cell is `Product & distribution` — DOM order is render order."""
        cells = _progress_cells()
        assert len(cells) == 2, (
            f"#progress must hold exactly two cells, found {len(cells)} — the "
            "side-by-side read (what exists | what is claimed) is the point of "
            "the section (PR #9283)")
        headings = tuple(heading for heading, _ in cells)
        assert headings == self.EXPECTED_CELL_ORDER, (
            f"#progress cell order changed: {headings!r} != "
            f"{self.EXPECTED_CELL_ORDER!r}. The section must open on what "
            "EXISTS ('Product & distribution'); the demand claim belongs in the "
            "right cell (PR #9283). DOM order is the rendered order — the grid "
            "is `1fr 1fr` with no `direction` override and no positional rule "
            "reaching #progress — so swapping the two <div>s inverts the page.")

    def test_early_demand_headline_is_the_sanctioned_copy_verbatim(self):
        """The first `Early demand` bullet, character-for-character."""
        cells = dict(_progress_cells())
        assert "Early demand" in cells, (
            f"the 'Early demand' cell is gone (cells: {sorted(cells)}) — this "
            "gate's subject moved (PR #9283)")
        first_bullet = cells["Early demand"][0]
        assert first_bullet == self.EXPECTED_DEMAND_HEADLINE, (
            f"the Early-demand headline is not the owner's copy verbatim: "
            f"{first_bullet!r} != {self.EXPECTED_DEMAND_HEADLINE!r}. ⛔ DO NOT "
            "'fix' this by paraphrasing it to indirect speech: that edit was "
            "made in fbd706a (a reviewer P2 about the quotation asserting a "
            "verbatim utterance nobody recorded), reverted by the owner in "
            "cd864dc, and the P2 recorded as an ACCEPTED RISK. The copy is the "
            "owner's call. If the copy genuinely changed, get the owner's "
            "wording and update this pin with it.")

    def test_the_retired_prospective_beta_line_is_gone(self):
        """`20+ prospective beta testers` must not return anywhere in the section."""
        section_text = _beat_text(_investors_progress_section())
        assert self.RETIRED_LINE not in section_text, (
            f"{self.RETIRED_LINE!r} is back in #progress. It was replaced by a "
            "pass rate over the calls that actually happened: 'prospective' "
            "disclaims the noun it modifies, a trailing '+' reads as a floor, "
            "and a count with no denominator invites the reader to discount it "
            "(PR #9283)")


#: Directories under the website scan roots that hold BUILD OUTPUT or vendored code,
#: never a declaration or a page of ours. Shared by both mono scans so they cannot
#: drift: `dist/` is the dashboard's and blog-admin's bundle, `node_modules/` the
#: installed tree, and `.wrangler/` (`.gitignore:7`) is what `wrangler dev` emits —
#: a bundled copy of `website/functions/**`, so a stale bundle carries the PRE-FIX
#: blog sheet verbatim. Scanning it made both mono guards RED over a correct tree on
#: any machine that had run the site's own dev server, and told the developer to add
#: `font-family` to generated code (#10254 review). The repo's other scans already
#: skip it for the same reason (`tests/test_cross_subdomain_cookie_sync.py:237`,
#: `tests/test_no_legacy_token_path.py:44`).
BUILD_OUTPUT_DIRS = ("node_modules", "dist", ".wrangler")


class TestMonospaceStackRendersAsAMonoFace:
    """Every mono font declaration must resolve to a real mono face on macOS Chrome (#6870).

    The stack this repo used everywhere was
    `'SF Mono', 'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace`. None of
    those named faces is installed on a stock macOS Chrome, so the declaration fell
    through to the bare `monospace` keyword — and Chrome's macOS default for that
    keyword is **Courier**.

    ⚠️ **`ui-monospace` does NOT fix this on Chrome.** It is a Safari 13.1+ generic;
    Chrome never shipped it (crbug.com/40194142) and, measured with CDP
    `CSS.getPlatformFontsForNode` on this repo's Chromium: `ui-monospace` alone →
    Times (the declaration is dropped), `ui-monospace, Arial` → Arial, `'SF Mono'` →
    Times, `SFMono-Regular` → Times, `Menlo` → **Menlo**, old stack → **Courier**.
    So the thing that makes Chrome render a mono face is **`Menlo`**; `ui-monospace`
    is carried first only so Safari resolves the platform UI mono. Both are required
    below, and `Menlo` is required *because* it is the one face Chrome resolves —
    "tidy up the redundant fallbacks" would resurrect the bug, which is why the
    guard asks for it by name instead of trusting the generic.

    ⚠️ **Scope:** it checks DECLARATIONS, not usage. A page that replaces
    `font-family: var(--mono)` with a serif renders a serif and this stays green —
    that is a layout decision, not the Courier defect, and it stays out of scope.

    Pinned rather than remembered because the stack is hand-copied into ~28 places
    across the marketing pages, the dashboard, the blog renderer, the blog-admin app,
    the cookie-consent banner and the served MCP consent page (`tortoise/oauth.py`):
    re-copying the old one from a neighbouring file is a one-keystroke regression and
    it is SILENT — the page still renders, just in the wrong face. Six such copies
    were missed on the first pass, and a 28th (the consent page every MCP client
    approves a grant on) was found by review.

    The scan keys on **the declaration value**, not on the `SF Mono` name: a stack
    listing none of the old names (`'Cascadia Code', monospace`) renders in Courier
    too, and an earlier version that keyed on the name PASSED exactly that mutation.
    It strips CSS/JS comments (so prose saying "not monospace" is not a stack) and
    blanks `@font-face` blocks, whose `font-family` is the name being *defined* rather
    than a fallback list — without that, `font-family:'monospace'` in an inline style
    was skipped as if it were a webfont name, and two real regressions (a JSX
    `fontFamily:'monospace'` and a canvas `ctx.font='… monospace'`) went green.
    """

    SUFFIXES = (".html", ".css", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".py")
    # `--mono:`, `font-family:`, camelCase `fontFamily:` and JS `ctx.font=` (both
    # appear in the React surfaces), the `font:` shorthand (which smuggles a mono
    # family past a font-family-only scan), and tailwind's `mono: [...]`.
    DECL = re.compile(
        r"(?:--mono|font-family|fontFamily|font|mono)\s*[:=]\s*"
        # a JS template literal puts `${…}` in the middle of the value, so allow it
        r"((?:\$\{[^}]*\}|[^;{}\n]|\n[ \t])*)",
        re.IGNORECASE)
    #: `/* */`, `<!-- -->` and `//`-to-end-of-line (but not the `//` in a URL)
    COMMENT = re.compile(r"/\*.*?\*/|<!--.*?-->|(?<!:)//[^\n]*", re.DOTALL)
    #: `@font-face { font-family: <name> }` declares a NAME, not a fallback stack.
    FONT_FACE = re.compile(r"@font-face\s*\{[^}]*\}", re.IGNORECASE)

    #: directories and served python modules that emit CSS for a browser.
    #: `render.py` carries no mono declaration today; it is listed because it IS
    #: an HTML emitter, so a declaration added there is scanned rather than found
    #: by review (the `oauth.py` history) — and
    #: `test_no_other_python_module_emits_a_mono_declaration` is the ratchet that
    #: keeps this list honest in both directions.
    ROOTS = ("website", "apps")
    MODULES = ("tortoise/oauth.py", "tortoise/render.py")
    #: build output and vendored trees that no declaration of ours lives in
    SKIP_DIRS = BUILD_OUTPUT_DIRS
    #: everything under here that emits a mono declaration must be in `MODULES`
    PY_ROOT = "tortoise"

    def _sources(self):
        """Every file this guard reads — FAIL CLOSED on a declared subject that is gone.

        A `continue`/`is_file()` skip here is a fail-open: renaming or moving
        `apps/` or `tortoise/oauth.py` left the scan GREEN over the 8 canvas/JSX
        declarations and the MCP consent page it exists to protect (`#6870`
        review P2). The floor in `test_no_mono_declaration_omits_a_face_...`
        does not cover it either — losing `apps/` leaves 28 declarations, still
        above the 20 floor. So a declared subject that is not there is a
        FAILURE that names the subject to update, never a silent narrowing.
        """
        repo = Path(__file__).resolve().parent.parent
        for root_name in self.ROOTS:
            root = repo / root_name
            assert root.is_dir(), (
                f"{root_name}/ is gone — update ROOTS in {type(self).__name__}. "
                "The scan silently narrowed instead, so every declaration under "
                "it is now unguarded (#6870 review P2).")
            # os.walk + a pruned `dirnames`, NOT `sorted(root.rglob("*"))`: rglob
            # materialises the WHOLE tree — node_modules and dist included —
            # before the filters run, turning a ~0.4 s test into a multi-second
            # walk on any machine that has built the dashboard or blog-admin.
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(d for d in dirnames if d not in self.SKIP_DIRS)
                for name in sorted(filenames):
                    path = Path(dirpath) / name
                    if path.suffix in self.SUFFIXES:
                        yield path
        for mod in self.MODULES:
            path = repo / mod
            assert path.is_file(), (
                f"{mod} is gone — update MODULES in {type(self).__name__}. Its "
                "emitted HTML would otherwise leave the scan GREEN over a served "
                "surface (#6870 review P2).")
            yield path

    @classmethod
    def _blank(cls, m):
        """Replace a comment/`@font-face` block with spaces, KEEPING newlines.

        Offsets and line numbers therefore stay the ORIGINAL file's: an earlier
        version collapsed comments to one space, so every failure message below
        a multi-line `/* … */` comment pointed at the wrong line.
        """
        return re.sub(r"[^\n]", " ", m.group(0))

    @classmethod
    def _strip(cls, text):
        """Blank comments and `@font-face` blocks, preserving length + newlines."""
        return cls.FONT_FACE.sub(cls._blank, cls.COMMENT.sub(cls._blank, text))

    @classmethod
    def _mono_values(cls, text):
        """Yield `(offset, value)` for every mono declaration value in `text`."""
        for m in cls.DECL.finditer(text):
            value = m.group(1)
            if "monospace" not in value.lower() or "var(--mono" in value:
                continue
            yield m.start(), value

    def _declarations(self):
        """Yield `(path, line, value)` for every mono declaration the guard reads."""
        for path in self._sources():
            text = self._strip(path.read_text(errors="replace"))
            for offset, value in self._mono_values(text):
                yield path, text.count("\n", 0, offset) + 1, value

    def _mono_declarations(self):
        repo = Path(__file__).resolve().parent.parent
        offenders, checked = [], 0
        for path, line, value in self._declarations():
            checked += 1
            low = value.lower()
            why = []
            if "menlo" not in low:
                why.append("no `Menlo` (the only face macOS Chromium resolves)")
            if "ui-monospace" not in low:
                why.append("no `ui-monospace` (Safari)")
            elif "menlo" in low and low.index("ui-monospace") > low.index("menlo"):
                why.append("`ui-monospace` must come before the named faces")
            if why:
                offenders.append(f"{path.relative_to(repo)}:{line}: {value.strip()[:100]} -> {'; '.join(why)}")
        return checked, offenders

    def test_no_mono_declaration_omits_a_face_chrome_actually_resolves(self):
        checked, offenders = self._mono_declarations()
        assert checked >= 20, (
            f"only {checked} mono font declaration(s) found under {'/'.join(self.ROOTS)} "
            f"and {', '.join(self.MODULES)}. If the stack was consolidated into one "
            "place, delete this test rather than letting it pass on nothing — a scan "
            "that finds nothing proves nothing.")
        assert not offenders, (
            "these mono declarations cannot render as a mono face on macOS Chromium "
            "(issue #6870), so they fall back to Courier:\n  " + "\n  ".join(offenders) +
            "\nUse: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, 'Cascadia Code', "
            "'Fira Code', 'JetBrains Mono', monospace")

    def test_every_file_it_scans_selects_this_guard(self):
        """The REVERSE direction: a file the guard READS must RUN it when changed.

        Registration in `config/ci-surfaces.yml` only makes this file
        CLASSIFIED; selection is what makes it RUN. Before the `onboarding`
        `SOURCE_PATTERNS` entries for `apps/graph-viz/src/` and
        `tortoise/oauth.py`, a diff touching ONLY one of those files selected no
        surface at all (`apps/` is in `NON_PYTHON_PREFIXES` and matched no
        pattern → `surfaces=[]`, tier-1 smoke) or selected `api`+`core` with the
        guard absent — so a re-copy of the old stack into a graph-viz canvas
        label or into the served MCP consent page merged GREEN.

        Derived from what the scan actually reads, so it cannot rot: a new
        declaration in an unwired path fails here instead of shipping unguarded
        (the `#6870` review P1 class, and `#1349/#3332/#3616` before it).
        """
        from tools.ci_selection import load_manifest, select

        repo = Path(__file__).resolve().parent.parent
        manifest = load_manifest()
        guard = Path(__file__).name
        missing = []
        for path in sorted({p for p, _, _ in self._declarations()}):
            rel = path.relative_to(repo).as_posix()
            selection = select([rel], "pull_request", manifest)
            if guard not in selection["test_files"]:
                missing.append(f"{rel} (selected surfaces: {selection['surfaces'] or 'none'})")
        assert not missing, (
            "these files carry a mono declaration this guard checks, but editing "
            "one selects no leg that runs this guard — so the Courier regression "
            "(#6870) merges green on exactly the diff that causes it. Add the "
            "path (or its directory) to SOURCE_PATTERNS['onboarding'] in "
            "tools/ci_selection.py, naming CONCRETE paths — select() matches with "
            "startswith and has no glob support:\n  " + "\n  ".join(missing))

    def test_no_other_python_module_emits_a_mono_declaration(self):
        """A NEW HTML emitter must be declared in `MODULES`, not discovered late.

        `tortoise/oauth.py` — the MCP consent page every client approves a grant
        on — was found by REVIEW, not by the scan, and its declaration lives in
        a module no `website/`-rooted pass could see (`#6870` review P2). This is
        the reverse ratchet for that population: a second emitter (or a move of
        the consent page's CSS into a new module) fails here, where adding it to
        `MODULES` is a one-line registration instead of a silent blind spot.
        """
        repo = Path(__file__).resolve().parent.parent
        undeclared = []
        for path in sorted((repo / self.PY_ROOT).rglob("*.py")):
            rel = path.relative_to(repo).as_posix()
            if rel in self.MODULES:
                continue
            raw = path.read_bytes()
            # Cheap byte pre-filter: 244 modules / ~16 MB, and no `.py` file can
            # carry a declaration without this substring (the value filter below
            # requires it), so the decode+regex pass only runs on candidates.
            if b"monospace" not in raw:
                continue
            text = self._strip(raw.decode("utf-8", "replace"))
            if next(self._mono_values(text), None) is not None:
                undeclared.append(rel)
        assert not undeclared, (
            "these modules emit a mono declaration but are NOT in "
            f"{type(self).__name__}.MODULES, so the scan never reads them and a "
            "re-copied stack there ships unguarded (#6870):\n  "
            + "\n  ".join(undeclared))

# ═══════════════════════════════════════════════════════════════════════════
# Product-root marker contract (PR #9652 review, P1)
# ═══════════════════════════════════════════════════════════════════════════
# The tortoise host serves `product.html` at `/` (the
# `website/functions/_middleware.ts` rewrite). TWO surfaces pin a string to
# prove that rewrite happened, and neither can catch the other:
#
#   1. `tests/e2e/test_legal_pages.py` — `PRODUCT_ROOT_MARKER`, read by the
#      local-emulation canary and by the served-root assertions inside
#      `_assert_serves_product` (raw body: an attribute does not survive
#      `_clean()`'s tag stripping). No count is stated on purpose — every add
#      or retirement would re-stale the number (#3950); the shape is pinned by
#      `test_every_marker_read_goes_through_the_raw_body_helper` below.
#   2. `.github/workflows/deploy-pages.yml` — the post-deploy DNS preflight
#      greps the served root and only then exports `TORTISE_HOST_CHECK=1`.
#
# That preflight's miss branch is a `::warning::`, so a stale marker does not
# red the job — it turns the tortoise-host half of the suite OFF, green. The
# pre-merge `legal-e2e` job (`.github/workflows/ci.yml`) DOES run
# `tests/e2e/test_legal_pages.py`, against a local `wrangler pages dev` with
# `TORTISE_HOST_CHECK=1` — but it reads the e2e constant only, never the
# workflow's grep, so it cannot see the preflight half drift; and until this PR
# fixed the constant's ordering, its local-emulation canary was dead and the
# tortoise-host tests skipped there too. Post-deploy, the preflight's marker is
# read by no pre-merge leg at all. In BOTH legs a drifted marker SKIPS the
# tortoise-host half green, so the change that breaks the marker is the one
# whose verification quietly stops running (PR #8597 named this; PR #9652 hit it
# again by replacing the hero `<h1>`, which WAS the marker) — which is why the
# pin belongs here, graded at PR time on the committed files.
#
# This file is selected whenever `website/product.html` changes (the
# `onboarding` surface in config/ci-surfaces.yml), AND also whenever
# `tests/e2e/test_legal_pages.py` changes (a `SOURCE_PATTERNS["onboarding"]`
# row in tools/ci_selection.py) — the file that holds the constant this gate
# parses had to be reachable, or the marker could be re-cut there with the gate
# asleep. Both paths are pinned by
# tests/test_ci_selection.py::test_product_root_marker_guard_runs_on_a_legal_pages_only_diff.
LEGAL_PAGES_E2E = REPO_ROOT / "tests" / "e2e" / "test_legal_pages.py"
DEPLOY_PAGES_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-pages.yml"
INDEX_HTML = REPO_ROOT / "website" / "index.html"


def _root_marker(name: str) -> str:
    """The literal of a module-level `NAME = "..."` in the e2e module.

    Parsed with `ast` rather than imported: that module needs playwright and a
    live host, and it calls `_tortoise_emulated_locally()` at import time. `ast`
    reads the literal exactly (quote style and escapes included) without
    executing anything.
    """
    src = LEGAL_PAGES_E2E.read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(target, "id", None) == name
                   for target in node.targets):
            continue
        value = ast.literal_eval(node.value)
        assert isinstance(value, str) and value, (
            f"{name} must be a non-empty string literal")
        return value
    raise AssertionError(
        "tests/e2e/test_legal_pages.py no longer defines a module-level "
        f"{name} assignment — this gate's subject moved")


def _product_root_marker() -> str:
    """The marker `tests/e2e/test_legal_pages.py` asserts on the served root."""
    return _root_marker("PRODUCT_ROOT_MARKER")


def _company_root_marker() -> str:
    """The marker `tests/e2e/test_legal_pages.py` asserts on the company root.

    Added by PR #9813: the company root was pinned by its hero PROSE
    ("the primary function of memory is not recall, but learning"), so the
    first hero rewrite deleted the pin — the exact defect PR #8597 and PR #9652
    each hit on the product root before the marker there became structural.
    """
    return _root_marker("COMPANY_ROOT_MARKER")


def _deploy_preflight_marker() -> str:
    """The literal the `verify-legal` DNS preflight greps the served root for."""
    src = DEPLOY_PAGES_WORKFLOW.read_text(encoding="utf-8")
    line = next(
        (ln for ln in src.splitlines()
         if "ROOT_MARKER" in ln and "grep" in ln and "-qF" in ln),
        None)
    assert line is not None, (
        ".github/workflows/deploy-pages.yml has no `grep -qF` on $ROOT_MARKER — "
        "the tortoise-host preflight this gate pairs with moved or was removed")
    match = re.search(r"grep\s+-qF\s+(['\"])(.+?)\1", line)
    assert match, f"cannot extract the preflight's root marker literal from: {line.strip()}"
    return match.group(2)


def _root_element(html: str, where: str = "website/product.html") -> str:
    """The page's `<html ...>` open tag."""
    match = re.search(r"<html\b[^>]*>", html, re.I)
    assert match, f"{where} has no <html> open tag"
    return match.group(0)


RAW_BODY_HELPER = "_assert_serves_product"
RAW_BODY_HELPER_COMPANY = "_assert_serves_company"
EMULATION_CANARY = "_tortoise_emulated_locally"

#: Every served-root marker the e2e module defines, and the helpers allowed to
#: read it. Both markers are structural ATTRIBUTES on the page's `<html>`, so
#: every read must be on the RAW body — `_clean()` strips tags, attributes
#: included, and an assertion written against cleaned text can never match.
SERVED_ROOT_MARKERS = ("PRODUCT_ROOT_MARKER", "COMPANY_ROOT_MARKER")
RAW_BODY_HELPERS = (RAW_BODY_HELPER, RAW_BODY_HELPER_COMPANY, EMULATION_CANARY)


def _marker_read_sites(src: str) -> list[tuple[str, int, str]]:
    """Every READ of a served-root marker, as `(enclosing function, line, name)`.

    `ast` rather than a regex: the constant's name also appears in the error
    message below itself and in comments, which a text scan cannot tell from a
    read.
    """

    class _Reads(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []
            self.reads: list[tuple[str, int, str]] = []

        def _visit_function(self, node: ast.AST) -> None:
            self.stack.append(getattr(node, "name", "<lambda>"))
            self.generic_visit(node)
            self.stack.pop()

        visit_FunctionDef = _visit_function  # type: ignore[assignment]
        visit_AsyncFunctionDef = _visit_function  # type: ignore[assignment]

        def visit_Name(self, node: ast.Name) -> None:
            if node.id in SERVED_ROOT_MARKERS and isinstance(node.ctx, ast.Load):
                self.reads.append(
                    (self.stack[-1] if self.stack else "<module>", node.lineno, node.id))

    visitor = _Reads()
    visitor.visit(ast.parse(src))
    return visitor.reads


def _asserted_expressions(src: str, function: str) -> list[str]:
    """The unparsed test expression of every `assert` inside `function`."""
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            return [
                ast.unparse(child.test)
                for child in ast.walk(node)
                if isinstance(child, ast.Assert)
            ]
    return []


def _called_names(src: str, function: str) -> list[str]:
    """Every function CALLED inside `function`, by simple name.

    `ast`, not a text scan: the whole point is to tell what a test actually
    invokes from what its comments, docstring or messages mention.
    """
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            names: list[str] = []
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                func = child.func
                if isinstance(func, ast.Name):
                    names.append(func.id)
                elif isinstance(func, ast.Attribute):
                    names.append(func.attr)
            return names
    return []


def _asserted_string_literals(src: str) -> list[tuple[int, str]]:
    """Every string LITERAL that could carry a served-root prose pin.

    Deliberately BROAD: narrowing this is how the first version of this check
    missed the shapes below. It reads every string constant in the module
    except two — DOCSTRINGS and assert MESSAGES — because both are
    documentation, and both legitimately quote the sentence they explain.
    Everything else counts:

    * an `assert` test (`assert "<sentence>" in r2.text()`);
    * a module-level constant, which is this module's own convention for a
      pin (`COMPANY_HERO_PIN = "<sentence>"` … `assert COMPANY_HERO_PIN in …`);
    * a comparison inside a helper (`if "<sentence>" not in body: raise …`);
    * comments — invisible to `ast`, so a note that QUOTES the hero (however it
      is wrapped) can never red the caller.
    """
    tree = ast.parse(src)
    documentation: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert) and node.msg is not None:
            documentation.update(id(child) for child in ast.walk(node.msg))
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                documentation.update(id(child) for child in ast.walk(first.value))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in documentation
    ]


def _shares_a_run_of(a: str, b: str, length: int) -> str | None:
    """A shared contiguous `length`-character run between `a` and `b`, if any.

    Contiguity is what a source-text scan gets WRONG when the text wraps: two
    lines joined are never adjacent in the file. Comparing normalised LITERALS
    makes the check independent of how the source happens to be wrapped, in
    either direction (the literal can be longer or shorter than the subject).

    A run must span a space to count: a `length`-character match inside a
    single word ("ction") is not a phrase anyone pins copy by.
    """
    for short, long in ((a, b), (b, a)):
        if len(short) < length:
            continue
        for start in range(len(short) - length + 1):
            window = short[start:start + length]
            if " " in window and window in long:
                return window
    return None


class TestProductRootMarkerContract:
    """The served-root marker is ONE structural string, agreed by both pins.

    Scope, stated plainly: this asserts the marker exists in the COMMITTED page
    and matches what the workflow greps. It cannot prove what production serves
    (that is `tests/e2e/test_legal_pages.py`, post-deploy); its job is to make a
    copy edit that orphans either pin red at PR time instead of silently
    disarming the tortoise-host suite after deploy.
    """

    def test_the_marker_identifies_the_committed_product_page(self):
        """The pinned marker must exist in `website/product.html`."""
        marker = _product_root_marker()
        html = PRODUCT_HTML.read_text(encoding="utf-8")
        assert marker in html, (
            f"PRODUCT_ROOT_MARKER {marker!r} does not occur in "
            "website/product.html. The tortoise host serves that file at `/`, "
            "so both consumers of the marker — tests/e2e/test_legal_pages.py "
            "and the deploy-pages.yml DNS preflight — can no longer prove the "
            "middleware rewrite. The preflight's miss branch is a ::warning::, "
            "so the tortoise-host tests would SKIP (green) instead of failing. "
            "If you edited the page's identity, move the marker in BOTH places.")

    def test_the_marker_does_not_also_match_the_company_root(self):
        """The marker's job is to discriminate product.html from index.html."""
        marker = _product_root_marker()
        index = INDEX_HTML.read_text(encoding="utf-8")
        assert marker not in index, (
            f"PRODUCT_ROOT_MARKER {marker!r} also occurs in website/index.html, "
            "which the company root (`premiselabs.co/`) serves. It no longer "
            "distinguishes the two roots, so `test_middleware_root_rewrites` "
            "and the deploy preflight would pass on the wrong page.")

    def test_the_deploy_preflight_greps_the_same_marker(self):
        """One marker, one source: the workflow must grep the test's string."""
        assert _deploy_preflight_marker() == _product_root_marker(), (
            "the deploy-pages.yml DNS preflight and PRODUCT_ROOT_MARKER in "
            "tests/e2e/test_legal_pages.py disagree. Moving one without the "
            "other either reds the post-deploy suite on a correct page or "
            "skips it green: the preflight only exports TORTISE_HOST_CHECK=1 "
            "when its grep matches, and the miss branch is a ::warning::.")

    def test_the_marker_is_an_attribute_on_the_root_element(self):
        """Structural, not prose: a copy edit must not be able to move it."""
        marker = _product_root_marker()
        root = _root_element(PRODUCT_HTML.read_text(encoding="utf-8"))
        assert marker in root, (
            f"PRODUCT_ROOT_MARKER ({marker!r}) is not part of the page's <html> "
            f"element ({root!r}). A marker that is a sentence of marketing copy "
            "(the `#beat-hero` h1 was one until PR #9652) is re-broken by every "
            "hero rewrite — PR #8597 and PR #9652 both had to move it. Keep the "
            "pin on page identity; keep the hero owner-editable.")

    def test_the_marker_is_defined_before_the_emulation_canary_runs(self):
        """Ordering pin: the canary reads the marker, so it must be bound first.

        `TORTISE_EMULATED = _tortoise_emulated_locally()` is a module-level call
        whose body reads `PRODUCT_ROOT_MARKER`. Bound after it, the read raises
        NameError, which the canary's own `except Exception: return False`
        swallows — so the canary reads False unconditionally and the whole
        tortoise-host half skips on the local (`127.0.0.1`) CI leg even when
        emulation works.
        """
        lines = LEGAL_PAGES_E2E.read_text(encoding="utf-8").splitlines()
        defined = next(
            (i for i, ln in enumerate(lines) if ln.startswith("PRODUCT_ROOT_MARKER = ")),
            None)
        canary = next(
            (i for i, ln in enumerate(lines)
             if ln.startswith("TORTISE_EMULATED = _tortoise_emulated_locally()")),
            None)
        assert defined is not None, "PRODUCT_ROOT_MARKER assignment not found"
        assert canary is not None, "the TORTISE_EMULATED canary call was moved or renamed"
        assert defined < canary, (
            f"PRODUCT_ROOT_MARKER is bound on line {defined + 1}, AFTER the "
            f"`_tortoise_emulated_locally()` call on line {canary + 1} that reads "
            "it. The NameError is swallowed by the canary's bare except, so "
            "TORTISE_EMULATED is False on every local run and the tortoise-host "
            "tests skip regardless of the marker. Define the marker first.")

    def test_every_marker_read_goes_through_the_raw_body_helper(self):
        """One shape for every served-root read: the RAW body, via one helper.

        The marker is a structural ATTRIBUTE, and `_clean()` strips tags — so
        an assertion written the old way (``PRODUCT_ROOT_MARKER in
        _clean(body)``, which is what PR #9652 had to rewrite at two sites) can
        never pass, and a copy of it added later would silently red the
        post-deploy suite on a correct page. This pins the SHAPE rather than a
        count, so adding or retiring a served-root assertion does not re-stale
        it (#3950); only reverting to a `_clean()`d or ad-hoc read does.
        """
        src = LEGAL_PAGES_E2E.read_text(encoding="utf-8")
        reads = _marker_read_sites(src)
        assert reads, (
            "no read of PRODUCT_ROOT_MARKER/COMPANY_ROOT_MARKER found in "
            "tests/e2e/test_legal_pages.py — this gate's subject moved")
        offenders = [
            (fn, line, name) for fn, line, name in reads
            if fn not in RAW_BODY_HELPERS
        ]
        assert not offenders, (
            "a served-root marker is read outside "
            f"{'/'.join(h + '()' for h in RAW_BODY_HELPERS)} at "
            + ", ".join(f"line {line} ({name} in {fn}())"
                        for fn, line, name in offenders)
            + ". These markers are structural attributes on <html>, and "
            "`_clean()` strips tags, so an assertion that cleans the body first "
            "can never match — it reds the served-root suite on a correct page "
            "and needs a rewrite the next time it lands (PR #9652 rewrote two). "
            "Read the raw document through `_assert_serves_product(body, "
            "where)` / `_assert_serves_company(body, where)`.")

        asserted = _asserted_expressions(src, RAW_BODY_HELPER)
        assert asserted, (
            f"`{RAW_BODY_HELPER}` in tests/e2e/test_legal_pages.py contains no "
            "assert — the served-root contract has no subject left")
        assert "PRODUCT_ROOT_MARKER in body" in asserted, (
            f"`{RAW_BODY_HELPER}` no longer asserts `PRODUCT_ROOT_MARKER in "
            f"body` (found: {asserted!r}). It must read the RAW document — "
            "`_clean(body)` strips the attribute off the <html> element, so the "
            "assertion could never match.")

        company_asserted = _asserted_expressions(src, RAW_BODY_HELPER_COMPANY)
        assert company_asserted, (
            f"`{RAW_BODY_HELPER_COMPANY}` in tests/e2e/test_legal_pages.py "
            "contains no assert — the company-root half of the middleware "
            "regression (#6) has no subject left")
        assert "COMPANY_ROOT_MARKER in body" in company_asserted, (
            f"`{RAW_BODY_HELPER_COMPANY}` no longer asserts "
            f"`COMPANY_ROOT_MARKER in body` (found: {company_asserted!r}). It "
            "must read the RAW document — `_clean(body)` strips the attribute "
            "off the <html> element, so the assertion could never match. This "
            "is precisely how the company root came to be pinned by hero prose "
            "(PR #9813).")


# ═══════════════════════════════════════════════════════════════════════════
# 8. Company-root marker contract (PR #9813 review, P1)
# ═══════════════════════════════════════════════════════════════════════════
# The company host (`premiselabs.co/`) serves `website/index.html` and the
# middleware regression test proves it by asserting a marker on the served
# document. That marker used to be the hero SENTENCE
# ("the primary function of memory is not recall, but learning"), so the first
# company-hero rewrite deleted it and the served-root assertion went red on a
# correct page — the same defect PR #8597 and PR #9652 each hit on the product
# root before it moved to `data-page="product"` (see
# TestProductRootMarkerContract above). This class is the PR-time half of the
# remedy: the marker must be page IDENTITY, it must live on the `<html>`
# element of the committed page, and it must not double as the product marker.

HEADING_TAG_RE = re.compile(r"<h([1-6])\b[^>]*>", re.I)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
STYLE_OR_SCRIPT_RE = re.compile(r"<(style|script)\b.*?</\1>", re.S | re.I)
MEDIA_PRELUDE_RE = re.compile(r"@media\s*\(\s*max-height:\s*(\d+)\s*px\s*\)\s*\{")


def _heading_levels(html: str) -> list[int]:
    """Heading levels in document order, ignoring comments and style/script."""
    stripped = HTML_COMMENT_RE.sub("", html)
    stripped = STYLE_OR_SCRIPT_RE.sub("", stripped)
    return [int(m.group(1)) for m in HEADING_TAG_RE.finditer(stripped)]


def _brace_block(text: str, open_index: int) -> str:
    """The `{...}` block that opens at `open_index`, brace-counted to its end."""
    depth, i = 0, open_index
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_index + 1:i]
        i += 1
    return text[open_index + 1:]


def _max_height_media_blocks(html: str) -> list[tuple[int, str]]:
    """Every `@media (max-height: Npx) { ... }` block as `(N, body)`.

    Brace-counted, NOT `.*?}` up to the first close: one declaration block per
    selector inside the media query is semantically identical to a shared one,
    and a regex that stops at the first `}` would read that refactor as "no
    rule names this selector" — a false red on a correct page.
    """
    blocks: list[tuple[int, str]] = []
    for match in MEDIA_PRELUDE_RE.finditer(html):
        blocks.append((int(match.group(1)),
                       _brace_block(html, match.end() - 1)))
    return blocks


#: The e2e test that proves the company host's served root.
COMPANY_ROOT_CALL_SITE = "test_middleware_root_rewrites"

#: How many contiguous characters a string literal must share with the company
#: hero sentence before it counts as a PROSE PIN, plus a word break (a run
#: inside one word is not copy). This is a NOISE TRADE-OFF, not the shortest
#: possible pin: `"not recall"` (10 characters) would work as a prose pin, and
#: so would `"recall, but"` (11) — they are tolerated because shorter runs need
#: no hero copy at all (the longest run ANY current assertion in the e2e module
#: shares with the hero is 5 characters: `"ction"`, `" not "`), and a predicate
#: that fires on those is a predicate everyone deletes.
HERO_PROSE_RUN = 15


#: The synthetic subject the non-vacuity fixtures are built against. It is NOT
#: the live hero on purpose: a fixture quoting the copy under review reds the
#: moment the owner rewrites the hero — the exact coupling this PR removes at
#: deploy time — and its failure reads as "the predicate is broken", which is
#: how a guard like this gets weakened.
FIXTURE_SENTENCE = "alpha beta gamma delta epsilon zeta eta theta"


def _company_hero_sentence() -> str:
    """`website/index.html`'s `<h1>` as one normalised (lowercase) sentence.

    Entities are unescaped so the comparison is against the sentence as it
    READS (`&`), not as it is spelled in markup (`&amp;`), and the space that
    tag-stripping leaves before sentence-final punctuation is removed —
    otherwise the compared string is not the sentence anyone quotes.
    """
    html = INDEX_HTML.read_text(encoding="utf-8")
    h1 = re.search(r"<h1\b[^>]*>(.*?)</h1>", html, re.S | re.I)
    assert h1, "website/index.html has no <h1> — the hero moved"
    text = unescape(re.sub(r"<[^>]+>", " ", h1.group(1))).replace("\u00a0", " ")
    sentence = re.sub(r"\s+([.,;:!?])", r"\1", " ".join(text.split())).lower()
    assert sentence, "the company hero <h1> is empty"
    return sentence


class TestCompanyRootMarkerContract:
    """The served company-root marker is ONE structural string (PR #9813).

    Scope, stated plainly: this asserts the marker in the COMMITTED page and
    the SHAPE of the e2e read. It cannot prove what production serves (that is
    `tests/e2e/test_legal_pages.py`, post-deploy). Its job is to make a hero
    rewrite that orphans the pin visibly fail at PR time — the pin it replaced
    (hero prose) was invisible to every pre-merge leg that runs on a website
    diff, because the whole e2e module self-skips without `RUN_LEGAL_E2E=1`.
    """

    def test_the_marker_identifies_the_committed_company_page(self):
        """The pinned marker must exist in `website/index.html`."""
        marker = _company_root_marker()
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert marker in html, (
            f"COMPANY_ROOT_MARKER {marker!r} does not occur in "
            "website/index.html. The company host serves that file at `/`, so "
            "`test_middleware_root_rewrites` can no longer prove the company "
            "root is index.html — and, because the whole e2e module skips "
            "without RUN_LEGAL_E2E=1, no pre-merge website leg would see it. "
            "If you edited the page's identity, move the marker in BOTH places.")

    def test_the_marker_does_not_also_match_the_product_root(self):
        """The company marker must not double as the product root marker."""
        marker = _company_root_marker()
        product = PRODUCT_HTML.read_text(encoding="utf-8")
        assert marker not in product, (
            f"COMPANY_ROOT_MARKER {marker!r} also occurs in "
            "website/product.html, which the tortoise host serves at `/`. The "
            "two served roots then become indistinguishable, so the middleware "
            "rewrite assertions would pass on the wrong page.")

    def test_the_marker_is_an_attribute_on_the_root_element(self):
        """Structural, not prose: a copy edit must not be able to move it."""
        marker = _company_root_marker()
        root = _root_element(INDEX_HTML.read_text(encoding="utf-8"),
                             "website/index.html")
        assert marker in root, (
            f"COMPANY_ROOT_MARKER ({marker!r}) is not part of the company "
            f"page's <html> element ({root!r}). A marker that is a sentence of "
            "marketing copy is re-broken by every hero rewrite — PR #9813's "
            "hero rewrite is exactly what deleted the prose pin this replaced. "
            "Keep the pin on page identity; keep the hero owner-editable.")

    def test_the_served_root_is_proven_structurally_not_by_prose(self):
        """The served-root test proves the root with the marker, never by text.

        This pins the SHAPE of the call site — the one thing that stops the
        historical defect returning: `test_middleware_root_rewrites` must call
        `_assert_serves_company` (which reads the RAW body and asserts
        `COMPANY_ROOT_MARKER`) and must make NO `_clean()` call at all. A prose
        pin cannot be added without text to compare, so "no `_clean()` here"
        is the mechanical statement of "the root is not proven by reading the
        page's words".
        """
        src = LEGAL_PAGES_E2E.read_text(encoding="utf-8")
        calls = _called_names(src, COMPANY_ROOT_CALL_SITE)
        assert calls, (
            f"tests/e2e/test_legal_pages.py no longer defines "
            f"`{COMPANY_ROOT_CALL_SITE}` — this gate's subject moved, so the "
            "company root has no served-document proof at all")
        assert RAW_BODY_HELPER_COMPANY in calls, (
            f"`{COMPANY_ROOT_CALL_SITE}` does not call "
            f"`{RAW_BODY_HELPER_COMPANY}()` (calls: {sorted(set(calls))!r}). "
            "The company root must be proven by `COMPANY_ROOT_MARKER` on the "
            "raw document — a prose assertion is what PR #9813 had to rewrite.")
        assert "_clean" not in calls, (
            f"`{COMPANY_ROOT_CALL_SITE}` calls `_clean()` (calls: "
            f"{sorted(set(calls))!r}). `_clean()` strips tags and therefore "
            "the `data-page` attribute off `<html>`, so any assertion built on "
            "it either can never match or is a prose pin on the page's words "
            "— the PR #8597/#9652/#9813 defect. Read the raw body through "
            f"`{RAW_BODY_HELPER_COMPANY}(body, where)`.")

    def test_no_literal_pins_the_company_root_by_hero_copy(self):
        """No string literal in the e2e module may carry the company hero copy.

        Read from the `ast` string LITERALS, not from the source text, so the
        check is independent of how the file is wrapped and of what its
        comments/docstrings quote — the previous 40-character prefix scan
        reddened a CORRECT page when a comment two files away was reflowed, and
        stayed green for a pin of the hero's tail.
        """
        sentence = _company_hero_sentence()
        offenders = []
        for line, literal in _asserted_string_literals(
                LEGAL_PAGES_E2E.read_text(encoding="utf-8")):
            shared = _shares_a_run_of(
                " ".join(literal.split()).lower(), sentence, HERO_PROSE_RUN)
            if shared:
                offenders.append((line, literal, shared))
        assert not offenders, (
            "tests/e2e/test_legal_pages.py carries the company HERO COPY in a "
            "string literal at "
            + ", ".join(f"line {line} ({literal[:60]!r})"
                        for line, literal, _ in offenders)
            + f". It shares {HERO_PROSE_RUN}+ characters with the hero "
            f"({offenders[0][2]!r}), so the next hero rewrite reds the "
            "served-root suite on a CORRECT page — the PR #9813 defect. Pin "
            "`COMPANY_ROOT_MARKER` on the `<html>` element instead, and read "
            "it through `_assert_serves_company()`.")

    def test_the_prose_pin_checks_reject_the_old_shapes(self):
        """Both checks above must FIRE on the shapes they exist to stop.

        Without this, the two tests could pass vacuously (a predicate that
        matches nothing is green on every tree). Each fixture is the defect in
        miniature, checked through the SAME helpers the tests use, against a
        SYNTHETIC subject — never the live hero, or the fixture would red the
        day the owner rewrites the copy this PR sets out to keep editable.
        """
        def fires(source: str) -> bool:
            return any(
                _shares_a_run_of(" ".join(literal.split()).lower(),
                                 FIXTURE_SENTENCE, HERO_PROSE_RUN)
                for _, literal in _asserted_string_literals(source))

        prose_pins = (
            # the shape PR #8597/#9652/#9813 shipped: the root pinned by a
            # sentence of the hero, asserted on cleaned text
            'def test_middleware_root_rewrites():\n'
            f'    assert "{FIXTURE_SENTENCE}" in _clean(r2.text())\n',
            # the same pin WRAPPED across source lines — this is what reddened
            # a correct tree under the old source-text scan
            'def test_middleware_root_rewrites():\n'
            '    assert (\n'
            '        "alpha beta gamma"\n'
            '        " delta epsilon zeta"\n'
            '    ) in r2.text()\n',
            # a FRAGMENT of the hero, the class the old 40-character prefix
            # could never see
            'def test_middleware_root_rewrites():\n'
            '    assert "delta epsilon zeta" in r2.text()\n',
            # …and the file's OWN pin convention: a module-level constant
            'HERO_PIN = "alpha beta gamma delta epsilon"\n'
            'def test_middleware_root_rewrites():\n'
            '    assert HERO_PIN in r2.text()\n',
            # …and a prose check written as a helper guard, not an assert
            'def _assert_serves_company(body, where):\n'
            '    if "alpha beta gamma delta epsilon" not in body:\n'
            '        raise AssertionError("company root prose drifted")\n',
        )
        for fixture in prose_pins:
            assert fires(fixture), f"the prose-pin check does NOT fire on: {fixture!r}"

        clean_read = (
            'def test_middleware_root_rewrites():\n'
            '    body = _clean(r2.text())\n'
            '    assert COMPANY_ROOT_MARKER in body\n')
        calls = _called_names(clean_read, COMPANY_ROOT_CALL_SITE)
        assert "_clean" in calls, (
            "the call-site check does NOT fire when the served-root test reads "
            f"`_clean()` (calls: {calls!r})")
        assert RAW_BODY_HELPER_COMPANY not in calls, (
            "the call-site check does not notice a missing "
            f"`{RAW_BODY_HELPER_COMPANY}()` call (calls: {calls!r})")

        # …and neither check may fire on a CORRECT site, however it is wrapped
        # or documented: comments, docstrings and assert messages that quote the
        # sentence are documentation, not pins.
        documented = (
            '"""Quotes the hero: "' + FIXTURE_SENTENCE + '"."""\n'
            '# a comment quoting the hero, wrapped mid-sentence:\n'
            '#   alpha beta gamma\n'
            '#   delta epsilon zeta eta theta\n'
            'def test_middleware_root_rewrites():\n'
            '    """Helper docstring: ' + FIXTURE_SENTENCE + '."""\n'
            '    assert COMPANY_ROOT_MARKER in r2.text(), (\n'
            '        "missing marker — pin data-page, not "\n'
            '        "' + FIXTURE_SENTENCE + '")\n')
        assert not fires(documented), (
            "the literal scan reads comments, docstrings or assert messages — "
            "a note quoting the hero would red a correct tree: "
            f"{_asserted_string_literals(documented)!r}")
        documented_calls = _called_names(documented, COMPANY_ROOT_CALL_SITE)
        assert RAW_BODY_HELPER_COMPANY not in documented_calls
        assert "_clean" not in documented_calls


# ═══════════════════════════════════════════════════════════════════════════
# 9. Document outline: no skipped heading level (PR #9813 review, P2)
# ═══════════════════════════════════════════════════════════════════════════
# Replacing the company page's `<h2>` with a `<p>` left the outline stepping
# h1 -> h3, which is an AT-navigation / WCAG H42 regression: screen-reader
# heading navigation and Lighthouse's `heading-order` audit both read the
# committed markup, so the pin belongs here, on the files.
class TestPageHeadingOutline:
    def test_the_company_front_page_does_not_skip_a_heading_level(self):
        levels = _heading_levels(INDEX_HTML.read_text(encoding="utf-8"))
        assert levels, "website/index.html has no headings"
        assert levels[0] == 1, (
            f"website/index.html's first heading is h{levels[0]}, not h1 "
            f"(levels: {levels})")
        for prev, cur in pairwise(levels):
            assert cur <= prev + 1, (
                f"website/index.html skips a heading level: h{prev} -> h{cur} "
                f"(levels: {levels}). Do not replace a section heading with a "
                "paragraph — a level skip is an accessibility regression, and "
                "the fix costs one tag (`<h2 class=\"tortoise-pitch\">`).")

    def test_the_company_front_page_has_a_level_two_heading(self):
        """The product section must keep a heading, not just body prose."""
        levels = _heading_levels(INDEX_HTML.read_text(encoding="utf-8"))
        assert 2 in levels, (
            f"website/index.html has no h2 at all (levels: {levels}). The "
            "Tortoise section on the company front page is the product's only "
            "heading there; demoting it to a paragraph leaves the page's main "
            "section unnamed for AT users.")

    def test_the_product_front_page_does_not_skip_a_heading_level(self):
        levels = _heading_levels(PRODUCT_HTML.read_text(encoding="utf-8"))
        assert levels and levels[0] == 1, (
            f"website/product.html's first heading is not h1 (levels: {levels})")
        for prev, cur in pairwise(levels):
            assert cur <= prev + 1, (
                f"website/product.html skips a heading level: h{prev} -> h{cur} "
                f"(levels: {levels})")


# ═══════════════════════════════════════════════════════════════════════════
# 10. Short-viewport beats must scroll, not clip (PR #9813 review, P2)
# ═══════════════════════════════════════════════════════════════════════════
# `.beat` is `position: fixed; height: 100%; justify-content: center` with no
# overflow rule, so content taller than the viewport is cut at BOTH edges and
# is unreachable (document scroll is consumed by ScrollTrigger). Both fronts
# carry beats whose text is now taller than a landscape phone's viewport:
# `#beat-tortoise` (the closing pitch, PR #9813) and `#beat-cta` (the waitlist
# form + its legal line) on index.html, and `#beat-hero` (the subtitle PR #9813
# enlarged) on product.html. Each needs the existing `@media (max-height: ...)`
# escape hatch — AND a way to RECEIVE the gesture: `.beat` is
# `pointer-events: none`, so without the visibility-gated `.beat.pe-on` rule a
# gesture over the pitch text passes through to the document and drives the
# ScrollTrigger scrub instead of the beat's overflow box. Declaring
# `overflow-y: auto` alone is decoration (PR #9813 review, P2).
SHORT_VIEWPORT_BEATS = (
    (INDEX_HTML, "#beat-cta", "the company waitlist form + its legal line"),
    (INDEX_HTML, "#beat-tortoise", "the company closing pitch + its CTA"),
    (PRODUCT_HTML, "#beat-hero", "the product hero + its CTA"),
)

#: A landscape phone is ~390-430 px tall, so a hatch that only fires below this
#: leaves the viewport it exists for with no escape hatch at all.
MIN_SHORT_VIEWPORT_PX = 480

#: A rule that lets a VISIBLE beat take pointer events (the animation script
#: toggles the class from the scrubbed opacity, so invisible beats stay
#: click-through — #744).
PE_ON_RULE_RE = re.compile(
    r"\.beat\.pe-on\b[^{}]*\{[^{}]*pointer-events\s*:\s*auto", re.S)
PE_ON_TOGGLE_RE = re.compile(r"classList\.toggle\(\s*['\"]pe-on['\"]")
PE_ON_SEED_RE = re.compile(r"classList\.add\(\s*['\"]pe-on['\"]")
BEAT_LIST_RE = re.compile(r"const\s+beats\s*=\s*\[(.*?)\]", re.S)


def _call_objects(text: str, call_start: int) -> list[str]:
    """Every top-level `{...}` object inside the call whose `(` follows `call_start`."""
    i = text.find("(", call_start)
    if i < 0:
        return []
    depth, objects = 0, []
    while i < len(text):
        char = text[i]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return objects
        elif char == "{" and depth == 1:
            block = _brace_block(text, i)
            objects.append(block)
            i += len(block) + 2   # '{' + block + '}' — resume after the close
            continue
        i += 1
    return objects


def _beat_tween_var_objects(html: str) -> list[list[str]]:
    """The vars objects of every `gsap.to(el, …)` / `gsap.fromTo(el, …)` call."""
    return [_call_objects(html, match.start())
            for match in re.finditer(r"gsap\.(?:to|fromTo)\(\s*el\s*,", html)]


class TestShortViewportBeatsScrollInsteadOfClipping:
    """The escape hatch is DECLARED (here) and REACHABLE (next test).

    Scope, stated plainly: this reads the committed markup and script. It does
    not open a browser, so it cannot observe clipping pixel-for-pixel — what it
    can do is pin the conditions without which the hatch provably cannot work:
    the `@media (max-height: ...)` rule with both declarations at a breakpoint
    that fires on a landscape phone, the visibility-gated pointer-events rule,
    and the per-tween wiring that keeps that class in step with the opacity. A
    change that drops any of them stops being a silent regression.
    """

    def test_each_tall_beat_declares_the_css_escape_hatch(self):
        for path, selector, what in SHORT_VIEWPORT_BEATS:
            blocks = _max_height_media_blocks(path.read_text(encoding="utf-8"))
            assert blocks, (
                f"{path.name} declares no `@media (max-height: ...)` rule. "
                f"{what} is a fixed full-height beat with centred content, so "
                "a short viewport clips it at both edges.")
            covering = [
                (height, body) for height, body in blocks
                if selector in body
            ]
            assert covering, (
                f"{path.name}: no `@media (max-height: ...)` rule names "
                f"{selector} (blocks found: "
                f"{[(h, b[:60]) for h, b in blocks]!r}). {what} is a fixed "
                "full-height beat whose content is taller than a landscape "
                "phone's viewport, and centred overflow is clipped at both "
                f"ends — the CTA becomes unreachable. Extend the "
                f"short-viewport rule to {selector}.")
            escaping = [
                height for height, body in covering
                if "overflow-y: auto" in body
                and "justify-content: flex-start" in body
            ]
            assert escaping, (
                f"{path.name}: the `@media (max-height: ...)` rule naming "
                f"{selector} does not both switch to `justify-content: "
                "flex-start` and allow `overflow-y: auto` "
                f"(found: {[b for _, b in covering]!r}). Centred overflow is "
                "cut at BOTH edges, so the first line goes above the viewport "
                "and the CTA below it.")
            assert max(escaping) >= MIN_SHORT_VIEWPORT_PX, (
                f"{path.name}: the escape hatch for {selector} only fires at "
                f"`max-height: {max(escaping)}px`, which a landscape phone "
                f"(~390-430 px tall) never reaches — {what} stays clipped on "
                f"the very viewport this rule exists for. Keep the breakpoint "
                f"at {MIN_SHORT_VIEWPORT_PX}px or wider.")

    def test_the_beat_can_receive_the_scroll_gesture_while_visible(self):
        """`overflow-y: auto` on a `pointer-events: none` box does nothing.

        A gesture over the pitch text would pass THROUGH the beat to the
        document, where ScrollTrigger consumes it — so the clipped content
        never moves and the beat's own overflow box is decoration. The
        mechanism is visibility-gated hit-testing: the beat must be in the
        script's beat list, the script must seed and then toggle `.pe-on` from
        the scrubbed opacity, EVERY opacity tween on it must keep that class in
        step, and the beat's own `pointer-events: none` default (if it has one)
        must come with a `.pe-on` companion that out-ranks it.
        """
        for path, selector, what in SHORT_VIEWPORT_BEATS:
            html = path.read_text(encoding="utf-8")
            beat_id = selector.lstrip("#")
            assert PE_ON_RULE_RE.search(html), (
                f"{path.name} declares no `.beat.pe-on {{ pointer-events: auto "
                "}` rule. `.beat` is `pointer-events: none`, so the overflow "
                f"hatch on {selector} can never receive a wheel/touch gesture "
                f"— {what} stays clipped where it is cut, and the gesture "
                "scrolls the page instead. Gate hit-testing on visibility "
                "(the `.beat.pe-on` pattern) rather than dropping the overlay "
                "rule, which would let invisible beats swallow clicks (#744).")
            assert PE_ON_TOGGLE_RE.search(html), (
                f"{path.name} never toggles `.pe-on`: the class must follow the "
                "scrubbed opacity (`classList.toggle('pe-on', opacity > "
                "0.05)`), or the beat takes pointer events at the wrong time "
                "— always (swallowing clicks, #744) or never (the overflow "
                "hatch stays inert).")
            assert PE_ON_SEED_RE.search(html), (
                f"{path.name} never seeds `.pe-on` for the beat that is visible "
                "at load (`i === 0`). No fade tween runs before the first "
                "scroll, so the first beat keeps `pointer-events: none` — its "
                "own overflow box stays unreachable until it has already "
                "started to fade.")
            beat_list = BEAT_LIST_RE.search(html)
            assert beat_list and beat_id in beat_list.group(1), (
                f"{path.name}: `{beat_id}` is not in the script's beat list "
                f"({beat_list.group(0) if beat_list else 'not found'!r}), so no "
                "fade tween ever updates its `.pe-on` state and the overflow "
                "hatch is never reachable.")

            # The mechanism has to hold EVERY frame the beat is on screen, not
            # just exist somewhere: each scrubbed beat tween must carry the sync
            # callback. Deleting one leaves that beat permanently click-through
            # (fade-in) or permanently hit-testable while invisible (fade-out,
            # the #744 direction).
            synced, unsynced = 0, []
            for objects in _beat_tween_var_objects(html):
                for config in (obj for obj in objects if "scrollTrigger" in obj):
                    synced += 1
                    if "onUpdate" not in config:
                        unsynced.append(config[:80])
            assert synced, (
                f"{path.name}: no scrolled `gsap` beat tween (a `gsap.to(el, …)` "
                "/ `gsap.fromTo(el, …)` with a `scrollTrigger`) was found — the "
                "beat sequence this guard reads moved.")
            assert not unsynced, (
                f"{path.name}: {len(unsynced)} of {synced} beat tweens do not "
                f"call the `.pe-on` sync ({unsynced!r}). Without it the beat's "
                "hit-testing drifts out of step with its visibility — a "
                "faded-out beat keeps swallowing clicks (#744) or a visible "
                "one never gains the gesture and its overflow hatch is dead.")

            # A `pointer-events: none` DEFAULT on the beat itself is fine — it
            # is what an invisible overlay needs — but an id selector out-ranks
            # `.beat.pe-on`, so it must come with a `.pe-on` companion.
            if re.search(r"#" + re.escape(beat_id)
                         + r"\s*\{[^{}]*pointer-events\s*:\s*none", html):
                assert re.search(r"#" + re.escape(beat_id)
                                 + r"\.pe-on\s*\{[^{}]*pointer-events\s*:\s*auto",
                                 html), (
                    f"{path.name} declares `pointer-events: none` on "
                    f"`{selector}` and no `{selector}.pe-on {{ pointer-events: "
                    "auto }}` companion. The id selector out-ranks "
                    "`.beat.pe-on`, so the visibility-gated rule is defeated "
                    "and the beat can never receive the scroll gesture.")


class TestARenderedCodeElementGetsAMonoFace:
    """A page that renders `<code>`/`<pre>` must give the ELEMENT a mono family (#6870).

    `TestMonospaceStackRendersAsAMonoFace` proves every *declaration* is right, and
    that is not enough: a `<code>` or `<pre>` element with NO matching rule inherits
    nothing — the user-agent's `font-family: monospace` beats inheritance — so it
    renders in Courier even when `--mono` is perfect everywhere in the file.

    That is exactly how the blog renderer and the 404 page survived the first pass:
    measured with CDP `CSS.getPlatformFontsForNode`, the blog's `<code>`/`<pre>` and
    `website/404.html`'s `<code>` were **Courier** while every sibling page was
    **Menlo**, because those two files rendered the element and never styled it.

    Scope is the self-contained page: a file under `ROOTS` carrying its own `<style>`
    block, i.e. the marketing pages, the 404, and the blog renderer's inline sheet. A
    React component styled by a separate stylesheet is out of scope — this test cannot
    see across files, and pretending otherwise would pin nothing. Two more emitters are
    self-contained with their own sheet and are deliberately OUTSIDE `ROOTS` because
    they are not pages this tree serves for reading: `tortoise/oauth.py:2145` (the MCP
    consent page) and `tortoise/render.py:94` (the renderer's own document), both of
    which the sibling declaration guard scans. Neither renders `<code>`/`<pre>` today,
    so no live element is missed — and
    `test_the_out_of_root_emitters_render_no_code` pins that precondition, so growing
    one there reds with the repair named instead of slipping past this scan.

    WHAT THIS PINS: every self-contained page that renders `<code>`/`<pre>` carries a
    rule naming that element whose family resolves to a mono face. That is the shape
    the defect had — the element existed and nothing styled it.

    WHAT IT DOES NOT PIN, stated because a guard is trusted for what it claims: CSS is
    not modelled, it is pattern-matched. It does not evaluate `@media`/`@supports`
    conditions or a `<style media=…>` attribute, does not model `<noscript>`, does not
    parse CSS strings (so a `/*` inside `content:"…"` is treated as a comment), and
    reads `font-family` plus the `font:` shorthand rather than resolving the cascade.
    A page could therefore satisfy it and still render Courier under some condition.
    Each of those is latent — no page in this tree carries such a shape — and closing
    them properly means parsing CSS, not adding a fifth regex.

    One thing it DOES model, because it is the defect's own shape: the family list is
    **ordered**. The browser takes the first family it has installed (MDN
    `font-family`; the generics are always installed), so a mono face named *after* a
    family Chrome certainly reaches is not the face the element gets —
    `code{font-family:'SF Mono', monospace, Menlo}` renders **Courier**, which is
    #6870 with `Menlo` one keystroke too late. The list is walked in priority order and
    refused when a Courier-attracting token (`monospace`, `courier`, `serif`,
    `sans-serif`) is reached before any `Menlo`; `ui-monospace` is not one of them,
    because Chrome drops it and the shipped stacks therefore carry it first. What is
    still not modelled: whether a *named* face is installed (a guard cannot know), and
    per-character fallback — so a named serif chosen in plain sight stays GREEN, which
    is the layout-decision boundary the sibling class records.

    Four ways an earlier version of this guard went GREEN over a page that renders a
    non-mono face, each closed here:

    1. **Inert text.** It read the file as text, so a `/* … */` comment inside the
       sheet, a `<style>` inside an HTML comment, a `<style>` inside a `<script>`
       string, and a `<style>` inside `<template>` each counted as styling. The
       document is blanked for comments, script bodies and template bodies before any
       sheet is read.
    2. **`var(--mono)` with nothing to resolve to.** The token was accepted without
       asking whether the page defines `--mono` at all, or whether the value it
       defines is a mono face (`--mono: Georgia, serif` passed). The declaration is
       now resolved through the page's own definitions, two hops, and must land on a
       mono face — and a `var()` **fallback** is read only when the token is missing,
       so `--mono: Georgia, serif` with `font-family: var(--mono, Menlo)` is REFUSED
       instead of being credited through fallback text CSS never substitutes
       (`var(--mono, …Menlo…)` ships 4x in
       `website/apps/dashboard/src/index.css:583,753,919,996`). A reference that
       resolves to nothing invalidates the **whole** declaration at computed-value
       time, and that signal is propagated out of the resolver's recursion instead of
       being dropped as empty text, so `--mono: var(--nope)` plus
       `font-family: Menlo, var(--mono)` is refused rather than credited through the
       trailing face the browser never applies. The accepted face is `Menlo` alone,
       because the sibling class measured `'SF Mono'` and `SFMono-Regular` as Times on
       this repo's Chromium (`TestMonospaceStackRendersAsAMonoFace`, above) — crediting
       them re-blessed the Courier defect on the one route the sibling guard cannot see.
    3. **A rule that styles the element only in context.** `.art-body pre code` — and
       `pre.anything code` — says nothing about an inline `<code>` in a paragraph, so
       that pairing is skipped; everything else is the cascade.
    4. **A custom property NAME read case-blind.** The resolver folded custom property
       names, so `:root{--Mono:Menlo} code{font-family:var(--MONO)}` resolved to
       `Menlo` and went GREEN. css-variables-1 §2 is explicit that custom property
       names are **not** ASCII case-insensitive and are equal only when identical —
       `--foo` and `--FOO` are distinct properties — so CSS computes the
       guaranteed-invalid value, invalidates `font-family` at computed-value time, and
       the element renders the UA `monospace`: Courier, the #6870 face. Names now match
       exactly; only the family *value* is folded, which css-fonts makes
       case-insensitive (#10254 review, round 13).

    Three ways it wrongly REFUSED a correct page, all closed: a rule wrapped in
    `@media`, a `font-family: var(--code-font)` indirection, and the element with a
    class or attribute (`code.inline{…}`). The rule match is innermost-only, the
    indirection resolves, a `font:` shorthand naming a mono face is accepted, and the
    last compound may carry a class/id/attribute/pseudo.

    One residual does fail GREEN, and it is named here rather than implied: a custom
    property is collected from **every** rule that declares it, regardless of the
    selector, and resolved for every element — but custom properties inherit
    (css-variables-1, the `--*` property table: `Inherited: yes`), so a token declared
    on a scoped rule does not reach a `<code>` outside that scope.
    `.art-body{--mono:Menlo} code{font-family:var(--mono)}` is therefore credited here,
    while a browser computes the guaranteed-invalid value for a `code` outside
    `.art-body`, invalidates the whole declaration, and renders the inherited family.
    Latent, not live — eight of the nine subjects define `--mono` in `:root`
    (`404.html:17`, `faq.html:24`, … `_lib.ts:1098`) and the ninth,
    `website/apps/dashboard/public/404.html`, resolves no `var()` at all: its `code{}`
    rule names `ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace` directly
    (`:41-42`), so there is no token for this limit to mis-resolve. Eight, not nine, at
    this head (#10254 review, round 11). And keying `defined` by declaring selector
    would mean modelling which ancestors an element has, i.e. the CSS parse this guard
    deliberately does not do. Pinned by
    `test_a_scoped_token_is_credited_and_that_is_a_documented_limit`, so closing it is a
    deliberate edit and never a silent change (#10254 review, round 9).

    Known residual, deliberately not modelled: a page rendering `<code>` **only**
    inside `<pre>` is refused, because the `pre code` pairing is skipped
    unconditionally. It fails RED, never green, and no page in this tree is shaped
    that way. A `var()` nested inside a fallback (`var(--a, var(--b, Menlo))`) is *not*
    a residual — the reference is matched paren by paren and substituted recursively,
    which is what css-variables-1 §"replace a var()" step 3 does (the fallback is
    itself substituted), so the family that is credited is the one the browser would
    compute. Truncating at the first `)` instead credited `--brand: Georgia, serif` +
    `font-family: var(--mono, var(--brand, Menlo))` as mono (#10254 review, round 6).
    """

    #: the trees this guard scans. `apps/` is NOT an empty declaration: it contributes
    #: 2 of the `MIN_DECIDABLE` subjects today — `apps/graph-viz/index.html` (a page
    #: that carries its own `<style>` and renders no `<code>`/`<pre>` YET) and
    #: `apps/graph-viz/src/components/ontology/HierarchyChart.tsx` (own `<style>`, not a
    #: page) — while the only `apps/` file that DOES render the element,
    #: `apps/graph-viz/src/components/ontology/OntologyView.tsx`, carries no own sheet,
    #: so it is not decidable here. MEASURED 2026-10-29 (#10254 review, round 12): the
    #: earlier wording ("`apps/` yields no self-contained page today") was stale — the
    #: top-level `apps/` root already carries sheets, and dropping it takes the ratchet
    #: to 19 < `MIN_DECIDABLE`. So removing it must be a deliberate edit that moves the
    #: floor too, never a silent narrowing (see `_sources`); each root's contribution is
    #: pinned by `test_every_page_it_can_decide_on_selects_this_guard`.
    ROOTS = ("website", "apps")
    #: measured 2026-10-28 at this tree: 9 self-contained pages carry `<code>`/`<pre>`
    #: (faq, privacy, 404, self-hosted, security, docs, license,
    #: apps/dashboard/public/404.html, and the blog renderer `_lib.ts`). The floor sits
    #: ON the measurement on purpose: a gap between the floor and the count is what lets
    #: the scan narrow without a red, so removing a page must move this number.
    MIN_PAGES = 9
    #: measured 2026-10-28 at this tree: 21 files under `ROOTS` carry their own `<style>`
    #: block — the set `test_every_page_it_can_decide_on_selects_this_guard` ratchets. A
    #: SEPARATE constant because it is a separate population: reusing `MIN_PAGES` as that
    #: test's floor left a 12-page gap, which is the narrowing this file forbids. Equal to
    #: the measurement on purpose, for the same reason.
    MIN_DECIDABLE = 21

    SUFFIXES = (".html", ".css", ".js", ".ts", ".jsx", ".tsx")
    RENDERS_CODE = re.compile(r"<(code|pre)\b", re.IGNORECASE)
    #: only the page's own sheet is author CSS for this guard. Inert text is stripped
    #: first: `<script>` strings, `<template>` bodies (not rendered), HTML comments.
    STYLE = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL | re.IGNORECASE)
    INERT = re.compile(r"<script\b[^>]*>.*?</script>|<template\b[^>]*>.*?</template>",
                       re.DOTALL | re.IGNORECASE)
    COMMENT = re.compile(r"/\*.*?\*/|<!--.*?-->", re.DOTALL)
    #: INNERMOST rules only: a body containing `{` means the match is an at-rule
    #: prelude wrapping another rule, and the inner one is the rule that styles it
    RULE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.DOTALL)
    #: `font-family` and the `font:` shorthand — the shorthand smuggles a family past
    #: a font-family-only scan, the lesson the sibling class already learned
    FAMILY = re.compile(r"(?:font-family|font)\s*:\s*([^;}]*)", re.IGNORECASE)
    VAR_DEF = re.compile(r"(--[\w-]+)\s*:\s*([^;}]*)")
    #: the tokens a family VALUE asks for — the population the scoped-token limit below
    #: applies to. Deliberately not `--mono`-only: the guard credits `var(--anything)`,
    #: so `--code-font` declared on a scoped rule is the same hole under another name.
    VAR_REF = re.compile(r"var\(\s*(--[\w-]+)", re.IGNORECASE)
    #: the selector's LAST compound must be the element itself — `.code{}` is the big
    #: "404" glyph, and `.art-body pre{font-family:inherit}` inherits rather than sets.
    #: The element may carry a class, id, attribute or pseudo — `code.inline{…}` styles
    #: `<code>`, so only a following TAG character disqualifies the compound
    ELEMENT_LAST = re.compile(r"(?:code|pre|kbd|samp)(?![a-zA-Z0-9_-])", re.IGNORECASE)
    TAG = re.compile(r"([a-zA-Z][\w-]*)")
    #: a real face, NOT the `monospace` keyword: on macOS Chrome that keyword IS
    #: Courier, which is the defect. The sibling guard requires Menlo by name for the
    #: same reason, and accepting the keyword let `var(--mono, monospace)` pass with
    #: `--mono` undefined or set to a serif.
    #: `Menlo` is the ONLY name here, and that is measured rather than assumed: the
    #: sibling class (`TestMonospaceStackRendersAsAMonoFace`, above) records CDP
    #: `CSS.getPlatformFontsForNode` on this repo's Chromium that `'SF Mono'` → Times
    #: and `SFMono-Regular` → Times while
    #: `Menlo` → Menlo, and #6870's availability probe records `'SF Mono'` as not
    #: exposed to web content. Crediting those two names made this guard GREEN over
    #: `font-family: var(--mono, 'SF Mono', monospace)` — Courier-class, the exact
    #: defect #6870 is about — on the `var()`-fallback route the sibling guard skips
    #: (it ignores any value containing `var(--mono`), so nothing else caught it
    #: (#10254 review, round 8).
    MONO = ("menlo",)
    #: The mirror of `MONO`: a family that makes Chrome reach a NON-mono face before
    #: any mono face named after it can apply. `font-family` is an ORDERED list and
    #: the browser uses "the first font in the list that is installed" (MDN
    #: `font-family`; the generics are always installed), so `Menlo` appended AFTER
    #: `monospace` is not the face the element gets — `monospace` IS Courier on macOS
    #: Chrome, the #6870 face. Matching `menlo` anywhere in the resolved value, with
    #: position ignored, made this guard GREEN over
    #: `code{font-family:'SF Mono', monospace, Menlo}` — the defect one keystroke too
    #: late (#10254 review).
    #: `ui-monospace` is deliberately NOT an attractor: it is a hyphenated generic
    #: Chrome drops (the sibling's measured table above), which is exactly why the
    #: shipped stacks carry it first. The boundaries are word-ish, not a substring test,
    #: so `ui-monospace` cannot match `monospace` and `sans-serif` cannot be read as the
    #: bare `serif` generic. A NAMED face such as `Georgia` is not here either: naming a
    #: serif face in plain sight is a layout decision, which is the boundary the sibling
    #: class records — only the generics/Courier are the Courier defect.
    COURIER_ATTRACTING = re.compile(
        r"(?<![\w-])(?:sans-serif|monospace|courier|serif)(?![\w-])")

    @staticmethod
    def _families(value):
        """The family tokens of `value`, in the order the browser tries them.

        `font-family` is a comma-separated list, and a `font:` shorthand prefixes the
        same list with size/weight/line-height — so `font:12px Menlo` is one token that
        must still be read as Menlo. Split on top-level commas only, so a comma inside a
        quoted family name (`'Foo, Bar'`) does not split it.
        """
        tokens, buf, quote = [], [], None
        for ch in value:
            if quote:
                buf.append(ch)
                if ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
                buf.append(ch)
            elif ch == ",":
                tokens.append("".join(buf))
                buf = []
            else:
                buf.append(ch)
        tokens.append("".join(buf))
        return [t.strip() for t in tokens if t.strip()]

    def _is_mono(self, value, defined, depth=0):
        """Does `value` give the element a mono face, once the page's tokens are substituted?"""
        # substitute BEFORE reading any face: a `var()` reference names no family of
        # its own, so a keyword found in the un-substituted text is not a decision
        # about what the element computes to. Reading the raw string first is how
        # `var(--mono, Menlo)` was credited while `--mono: Georgia, serif` — the face
        # Chrome actually used — went unread (PR #10254 review, round 3).
        substituted = self._substitute(value, defined, depth)
        if substituted is None:
            # invalid at computed-value time (css-variables-1 §2.2: a guaranteed-invalid
            # value in a property value makes the property invalid at computed-value
            # time). The declaration does not apply, the element inherits, and the UA
            # `monospace` — Courier — wins. Never a face, whichever names it carried.
            return False
        # the FAMILY VALUE is folded here and only here: font family names are ASCII
        # case-insensitive (css-fonts-4), unlike the custom property NAMES the resolver
        # matches, which keep their casing.
        v = substituted.strip().lower()
        if not v or v in ("inherit", "unset", "initial", "revert"):
            # inheriting is the failing case for an element with no author rule: the
            # UA `monospace` sheet would win. A parent that IS mono is out of scope.
            return False
        # Walk the list in PRIORITY order, not `any(name in value)`: the browser takes
        # the FIRST family it has installed, so a mono face named after a family the
        # browser certainly reaches is not the face the element gets. The first token
        # that names a mono face is a pass; a Courier-attracting token reached first is
        # a refusal; anything else (an unavailable named face, `ui-monospace`, a serif
        # face chosen in plain sight) is passed over, exactly as the browser passes it.
        for family in self._families(v):
            if any(m in family for m in self.MONO):
                return True
            if self.COURIER_ATTRACTING.search(family):
                return False
        return False

    @staticmethod
    def _var_reference(text, start):
        """`(name, fallback, end)` for the `var(` at `text[start]`, matched paren by paren.

        `end` is the index of its closing `)`. The fallback is split at the first
        *top-level* comma, so `var(--mono, var(--brand, Menlo))` yields the fallback
        `var(--brand, Menlo)` — whole, not truncated at the inner `)`.

        There is deliberately no `var(...)` regex here: parens cannot be counted by one,
        and truncating at the first `)` is how an earlier version credited a serif named
        only inside an unresolved fallback (#10254 review, round 6).

        `None` for an unbalanced reference: that is a parse error in CSS, so the whole
        declaration is dropped and no family in it applies — the caller refuses it
        rather than reading the residue as text.

        The returned NAME keeps the author's casing. Custom property names are the one
        identifier family CSS does NOT fold: css-variables-1 §2, "Unlike other CSS
        properties, custom property names are not ASCII case-insensitive. Instead,
        custom property names are only equal to each other if they are identical to
        each other" — `--foo` and `--FOO` are two distinct properties. Lowercasing the
        name here made this guard disagree with the browser in the fail-GREEN
        direction: `:root{--Mono:Menlo} code{font-family:var(--MONO)}` is the
        guaranteed-invalid value with no fallback, so `font-family` is invalid at
        computed-value time and the element renders the UA `monospace` — Courier, i.e.
        #6870 itself — while the collapsed key resolved it to `Menlo` (#10254 review,
        round 13). Only the FAMILY VALUE is case-insensitive (css-fonts), and `_is_mono`
        is where that fold belongs.
        """
        depth, i = 0, start + 3
        while i < len(text):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        else:
            return None
        inner = text[start + 4:i]
        depth, comma = 0, None
        for j, ch in enumerate(inner):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                comma = j
                break
        if comma is None:
            return inner.strip(), None, i
        return inner[:comma].strip(), inner[comma + 1:], i

    def _substitute(self, value, defined, depth=0):
        """`value` with its `var()` references replaced the way CSS replaces them.

        A **defined** custom property is substituted and its fallback is *dead text*:
        css-variables-1 §"Using Cascading Variables" makes the second argument the
        substitution value only "when the value of the referenced custom property is
        the guaranteed-invalid value", i.e. missing or invalid. Crediting a fallback
        regardless is a false green and not a hypothetical one: the live idiom
        `font-family: var(--mono, ui-monospace, …Menlo…)` ships 4x in
        `website/apps/dashboard/src/index.css` (583, 753, 919, 996), so any page that
        also defined `--mono` as a serif would pass this guard while macOS Chrome
        rendered the serif.

        Reference resolution is recursive and paren-balanced, because the fallback is
        itself substituted (css-variables-1 "replace a var()" step 3): `var(--a,
        var(--b, Menlo))` computes to whatever `--b` is, so the family this guard may
        credit is the one the browser would actually use — reading the fallback as
        flat text credited a serif sitting inside it (#10254 review, round 6).

        A missing token falls back. A reference that resolves to nothing — missing with
        no fallback, defined as empty, or a chain past the depth cap — makes the whole
        declaration invalid at computed-value time (CSS does not fall through to the
        next family in the list), so the element inherits and the guard goes RED. That
        is the safe direction: over-detection, never a false green.

        Returns `None` for that invalid case rather than `""`, so the flag survives the
        recursion. Returning empty *text* lost it one hop down — `--mono: var(--nope)`
        with `font-family: Menlo, var(--mono)` came back as `Menlo,`, which reads as a
        mono face although CSS invalidates the whole declaration and the element renders
        Courier (#10254 review, round 8).
        """
        out, i, invalid = [], 0, False
        while i < len(value):
            if value[i:i + 4].lower() != "var(":
                out.append(value[i])
                i += 1
                continue
            ref = self._var_reference(value, i)
            if ref is None:
                # unbalanced `var(`: a parse error, so CSS drops the WHOLE declaration
                # and no family written in it applies. Refuse rather than credit the
                # residue's text (`Menlo, var(--mono` would otherwise read as mono).
                invalid = True
                break
            name, fallback, end = ref
            resolved = defined.get(name)
            chosen = resolved if resolved is not None else fallback
            if chosen is None or not chosen.strip() or depth >= 2:
                invalid = True
                i = end + 1
                continue
            substituted = self._substitute(chosen, defined, depth + 1)
            # `None` is the recursion's invalid signal — propagate it, do not append it
            # as text, which is how the flag was lost one hop down. A leftover `var(`
            # is the same refusal (an unresolved reference is not a face).
            if substituted is None or "var(" in substituted.lower():
                invalid = True
            else:
                out.append(substituted)
            i = end + 1
        return None if invalid else "".join(out)

    def _elements_styled_mono(self, css):
        """The element names (`code`, `pre`) that some rule gives a mono family."""
        # keyed by the author's exact name: custom property names are case-SENSITIVE
        # (css-variables-1 §2 — `--foo` and `--FOO` are distinct), so folding case
        # here resolves a reference the browser invalidates (#10254 review, round 13).
        # `value` is left raw too; family-value folding is `_is_mono`'s job.
        defined = {name.strip(): value for name, value in self.VAR_DEF.findall(css)}
        # the LAST matching rule wins the cascade, so a later
        # `code{font-family:inherit}` must un-style it, not be ignored
        last_family = {}
        for selectors, body in self.RULE.findall(css):
            families = self.FAMILY.findall(body)
            if not families:
                continue
            family = families[-1]
            for sel in selectors.split(","):
                parts = re.split(r"[\s>+~]+", sel.strip())
                last = parts[-1]
                match = self.ELEMENT_LAST.match(last)
                if not match:
                    continue
                # the compound may carry a class/id/attr/pseudo — the ELEMENT is the tag
                element = match.group(0).lower()
                # `pre code` (and `pre.anything code`) styles code *inside* pre; it is
                # not the generic style for an inline <code>, so it must not decide it
                if element == "code" and len(parts) > 1:
                    tag = self.TAG.match(parts[-2])
                    if tag and tag.group(1).lower() == "pre":
                        continue
                last_family[element] = family
        return {e for e, fam in last_family.items() if self._is_mono(fam, defined)}

    def test_no_self_contained_page_renders_code_or_pre_unstyled(self):
        repo = Path(__file__).resolve().parent.parent
        offenders, checked = [], 0
        for path in self._sources(repo):
            text = path.read_text(errors="replace")
            if not self.RENDERS_CODE.search(text):
                continue
            # blank the whole DOCUMENT first: a `<style>` inside a `<script>` string, a
            # `<template>` body, or an HTML comment is inert, and blanking only the
            # extracted sheets missed the last two
            css = "\n".join(self.STYLE.findall(
                self.COMMENT.sub(" ", self.INERT.sub(" ", text))))
            if not css.strip():
                continue
            checked += 1
            # per element, not "some rule somewhere": the blog styled `pre` and left
            # inline `code` to the UA sheet, which passed a set-level check
            rendered = {e.lower() for e in self.RENDERS_CODE.findall(text)}
            missing = sorted(rendered - self._elements_styled_mono(css))
            if missing:
                offenders.append(f"{path.relative_to(repo)} ({', '.join('<' + m + '>' for m in missing)})")
        # the floor is the MEASURED count (9 self-contained pages today), not a guess: it
        # has no slack, so deleting or moving a page is a deliberate act that moves this
        # number with it, instead of narrowing the scan silently (PR #10254 review).
        assert checked >= self.MIN_PAGES, (
            f"only {checked} self-contained page(s) found under "
            f"{'/'.join(self.ROOTS)} — the scan is not looking at the right tree")
        assert not offenders, (
            "these pages render these elements but no rule in their own <style> gives the ELEMENT "
            "a mono family, so the user-agent's `monospace` applies and macOS Chrome renders Courier "
            "(issue #6870):\n  " + "\n  ".join(offenders) +
            "\nAdd e.g. `code{font-family:var(--mono)}` AND make sure that page defines `--mono` as a "
            "mono face (an undefined var() invalidates the declaration and the element inherits).")

    @staticmethod
    def _sources(repo):
        # os.walk with a pruned `dirnames`, NOT `rglob`: rglob materialises the whole
        # tree, `node_modules` included, before any filter can run (see the sibling
        # class's note — it is the same walk, and the same cost).
        # `.wrangler` is in this set for the reason the shared constant records: it is
        # a gitignored COPY of `website/functions/**`, so a stale bundle contains the
        # pre-fix blog sheet and both tests in this class failed on generated code
        # (#10254 review P3).
        skip = BUILD_OUTPUT_DIRS
        for root_name in TestARenderedCodeElementGetsAMonoFace.ROOTS:
            root = repo / root_name
            # fail closed: a missing root would narrow the scan silently
            assert root.is_dir(), (
                f"{root_name}/ is gone — update ROOTS in "
                f"{TestARenderedCodeElementGetsAMonoFace.__name__}")
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in skip]
                for name in filenames:
                    if Path(name).suffix not in TestARenderedCodeElementGetsAMonoFace.SUFFIXES:
                        continue
                    yield Path(dirpath) / name

    def test_every_page_it_can_decide_on_selects_this_guard(self):
        """The REVERSE direction: a page this guard can DECIDE on must RUN it when changed.

        Registration in `config/ci-surfaces.yml` only makes this file CLASSIFIED;
        selection is what makes it RUN. Declaring `apps/` in `ROOTS` and stopping
        there is exactly the silent-drop class `#1349/#3332/#3616` name: the guard
        grows a subject whose diff selects no leg that runs it, so the regression it
        exists for merges GREEN and main goes red only on the next trunk run.

        MEASURED at the head this landed (#10254 review P2), over the files this scan
        actually reads (`_sources`, node_modules/dist pruned): 21 of the files under
        `ROOTS` carry their own `<style>` block — the set this guard can decide on —
        and 20 of those already selected this surface. `apps/graph-viz/index.html` was
        the only one that did not (`select([…]) -> surfaces=[], full=False`, guard ABSENT;
        `apps/` is in `NON_PYTHON_PREFIXES` and matched no `SOURCE_PATTERNS` row, so
        the diff filtered to `changed == []` and took the docs-only tier-1 early
        return). That page renders no `<code>`/`<pre>` today, which is why it had to
        be derived rather than noticed.

        The subject set is the own-`<style>` set, NOT the `<code>`-rendering set: a
        page becomes decidable the moment it renders the element, so ratcheting only
        the pages that render it today would leave the next one free to arrive
        unguarded. Derived from what the scan actually reads, so it cannot rot.
        """
        from tools.ci_selection import load_manifest, select

        repo = Path(__file__).resolve().parent.parent
        manifest = load_manifest()
        guard = Path(__file__).name
        missing = []
        decidable = 0
        # per-root census: a root that contributes ZERO is the shape the `ROOTS`
        # comment used to (wrongly) describe for `apps/`, and it is the shape that
        # makes a declared root decorative rather than a subject set (#10254 review,
        # round 12).
        by_root = {root: 0 for root in self.ROOTS}
        for path in sorted(self._sources(repo)):
            text = path.read_text(errors="replace")
            # same blanking order the guard uses: inert text is not a sheet
            if not self.STYLE.search(self.COMMENT.sub(" ", self.INERT.sub(" ", text))):
                continue
            decidable += 1
            rel = path.relative_to(repo).as_posix()
            by_root[Path(rel).parts[0]] += 1
            selection = select([rel], "pull_request", manifest)
            if guard not in selection["test_files"]:
                missing.append(f"{rel} (selected surfaces: {selection['surfaces'] or 'none'})")
        assert decidable >= self.MIN_DECIDABLE, (
            f"only {decidable} page(s) carry their own <style> under "
            f"{'/'.join(self.ROOTS)} — the scan is not looking at the right tree")
        # Every DECLARED root must actually contribute subjects. `apps/` carries 2 of
        # the 21 today (`apps/graph-viz/index.html`, `HierarchyChart.tsx`), which is the
        # fact the `ROOTS` comment states; if that ever drops to 0 the root is
        # decorative and the comment is stale again — move the floor and the comment
        # together instead of letting this assert be the only record (#10254 round 12).
        assert all(count for count in by_root.values()), (
            "a declared root carries no own-<style> subject: "
            f"{by_root!r} over ROOTS={'/'.join(self.ROOTS)} — dropping or emptying a "
            "root narrows the scan; if the narrowing is intended, move MIN_DECIDABLE "
            "and the ROOTS comment in the same edit")
        assert not missing, (
            "these pages carry their own <style> block, so this guard would DECIDE on "
            "them if they rendered `<code>`/`<pre>` — but editing one selects no leg "
            "that runs this guard, so the Courier regression (#6870) would merge green "
            "on exactly the diff that causes it. Add the path to "
            "SOURCE_PATTERNS['onboarding'] in tools/ci_selection.py, naming a CONCRETE "
            "path — select() matches with startswith and has no glob support:\n  "
            + "\n  ".join(missing))

    def test_a_var_fallback_is_dead_text_when_the_token_is_defined(self):
        """The resolver's own cases, pinned so the guard cannot silently un-fix (#10254 review).

        Probes, not pages: each `css` string is a whole sheet handed to the resolver, and the
        expectation is what CSS would compute for a `<code>` under it. The third and fourth rows
        are the false greens the review measured — a mono face named ONLY in a fallback that the
        browser never substitutes, because `--mono` is defined to a serif.
        """
        cases = [
            # (why, css, is `code` given a mono face?)
            ("defined serif, mono fallback — fallback is dead text (css-variables §3)",
             ":root{--mono:Georgia,serif} code{font-family:var(--mono, Menlo)}", False),
            ("same, quoted face)",
             ":root{--mono:Georgia,serif} code{font-family:var(--mono, 'SF Mono')}", False),
            ("same, the live dashboard idiom",
             ":root{--mono:Georgia,serif} "
             "code{font-family:var(--mono, ui-monospace, SFMono-Regular, Menlo, monospace)}", False),
            ("defined mono, fallback unused",
             ":root{--mono:Menlo,monospace} code{font-family:var(--mono, Georgia)}", True),
            # Custom property NAMES are case-sensitive (css-variables-1 §2: "custom
            # property names are not ASCII case-insensitive … only equal to each other
            # if they are identical to each other"), so a reference differing in case is
            # the guaranteed-invalid value, not the token. These three were GREEN while
            # the resolver folded the name — and CSS computes exactly the #6870 face
            # (no fallback → invalid at computed-value time → inherit → UA `monospace`,
            # which is Courier on macOS Chrome). Pinned in BOTH directions, and only the
            # family VALUE is folded (`menlo` matches `Menlo`).
            ("a case-differing reference is the guaranteed-invalid value, NOT the token",
             ":root{--Mono:Menlo} code{font-family:var(--MONO)}", False),
            ("…the other way round: `--mono` declared, `--MONO` asked for",
             ":root{--mono:Menlo} code{font-family:var(--MONO)}", False),
            ("…and it is not rescued by an unrelated `--mono` elsewhere",
             ":root{--MONO:Menlo;--mono:Georgia,serif} code{font-family:var(--Mono)}", False),
            ("a case-differing reference falls back like any other missing token",
             ":root{--Mono:Menlo} code{font-family:var(--MONO, Menlo)}", True),
            ("the IDENTICAL name resolves — case is preserved, never folded away",
             ":root{--Mono:Menlo} code{font-family:var(--Mono)}", True),
            ("missing token — the fallback IS substituted",
             "code{font-family:var(--mono, Menlo)}", True),
            ("defined serif, no fallback to rescue it",
             ":root{--mono:Georgia,serif} code{font-family:var(--mono)}", False),
            ("token defined as an unresolvable var() → RED, never green",
             ":root{--mono:var(--nope)} code{font-family:var(--mono, Menlo)}", False),
            ("a later `font:` shorthand resets the family",
             ":root{--mono:Menlo} code{font-family:var(--mono)} code{font:12px Georgia}", False),
            ("`inherit` is the failing case, not a pass",
             "code{font-family:inherit}", False),
            ("bare `monospace` IS Courier on macOS Chrome — not a mono face here",
             "code{font-family:var(--mono, monospace)}", False),
            # `'SF Mono'` and `SFMono-Regular` are measured as Times on macOS Chrome in
            # this very file (`TestMonospaceStackRendersAsAMonoFace`, CDP on this repo's
            # Chromium) and #6870
            # records `'SF Mono'` as not exposed to web content. This route — a mono
            # name in a `var()` fallback — is the one the sibling declaration guard
            # cannot see, because it skips values containing `var(--mono`.
            ("`'SF Mono'` in a fallback is Times, not mono — #6870's own measurement",
             "code{font-family:var(--mono, 'SF Mono', 'Cascadia Code', monospace)}", False),
            ("`SFMono-Regular` on its own is Times too",
             "code{font-family:SFMono-Regular}", False),
            # A family list is ORDERED: the browser takes the first family it has
            # installed, so a mono face appended AFTER a family Chrome always reaches
            # is not the face the element gets. `monospace` IS Courier here, so these
            # are #6870 itself — and they were GREEN while `menlo` was matched
            # anywhere in the value (#10254 review).
            ("a mono face appended after the generic IS the #6870 face again",
             "code{font-family:monospace, Menlo}", False),
            ("the generic via the live dashboard's `var()` fallback shape",
             "code{font-family:var(--mono, ui-monospace, monospace, Menlo)}", False),
            ("`'SF Mono'`, the generic, then `Menlo` — the defect one keystroke late",
             "code{font-family:'SF Mono', monospace, Menlo}", False),
            ("a serif generic reached before the mono face",
             "code{font-family:serif, Menlo}", False),
            # …and the other direction, so the ordered rule cannot false-red what
            # ships: `ui-monospace` is a generic Chrome DROPS
            # (`TestMonospaceStackRendersAsAMonoFace`), so the
            # real stack — which carries it first — still resolves to `Menlo`.
            ("the shipped stack: `ui-monospace` first is not a Courier attractor",
             "code{font-family:ui-monospace, SFMono-Regular, 'SF Mono', Menlo, monospace}",
             True),
            ("`Menlo` before the generic still wins — order, not presence",
             "code{font-family:Menlo, monospace}", True),
            ("a `font:` shorthand naming the mono face is still accepted",
             "code{font:italic bold 12px/1.4 Menlo}", True),
        ]
        for why, css, expect in cases:
            got = "code" in self._elements_styled_mono(css)
            assert got == expect, (
                f"{'false green' if got else 'false red'}: {why}\n  css: {css}\n"
                f"  guard says mono={got}, CSS computes mono={expect}")

    def test_a_scoped_token_is_credited_and_that_is_a_documented_limit(self):
        """The one fail-GREEN shape this guard documents instead of fixing (#10254 r9).

        `VAR_DEF` collects `--x: …` from every rule whatever selector declares it, and
        `_elements_styled_mono` resolves any `var(--x)` against that flat map. A custom
        property INHERITS (css-variables-1, the `--*` property table: `Inherited: yes`),
        so a token declared on `.art-body` does not reach a `<code>` outside it: the
        reference there is the guaranteed-invalid value, the declaration is invalid at
        computed-value time, and the element renders its PARENT's family — not the mono
        face this guard credits.

        This is the opposite direction from the two residuals the class docstring
        states, which both fail RED. It stays a documented LIMIT because keying
        `defined` by declaring selector means modelling which ancestors an element has;
        and it is latent today — eight of the nine subjects define `--mono` in `:root`,
        and the ninth (`website/apps/dashboard/public/404.html`) names the family list
        directly in its `code{}` rule (`:41-42`) and resolves no `var()` at all, so this
        limit has no token there to mis-resolve. Eight, not nine, at this head (#10254
        review, round 11). The assertion is written to RED the moment the limit is
        closed, so closing it is a deliberate edit and this probe is deleted with the
        docstring bullet, rather than the shape being re-derived as a finding for the
        fourth time.
        """
        probes = [
            # (why, css) — each is GREEN here; the CSS computes no mono face for `code`
            ("token declared on a scoped ancestor rule",
             ".art-body{--mono:Menlo} code{font-family:var(--mono)}"),
            ("token declared inside a conditional group",
             "@media print{:root{--mono:Menlo}} code{font-family:var(--mono)}"),
        ]
        for why, css in probes:
            assert "code" in self._elements_styled_mono(css), (
                f"the documented declaring-scope limit CHANGED ({why}): the guard now "
                f"REFUSES a token whose declaring selector it cannot prove reaches the "
                f"element. That is the fail-safe direction, so update the "
                f"`WHAT IT DOES NOT PIN` bullet and delete this probe.\n  css: {css}")

    #: selectors that reach EVERY element, so a token declared on one is not scoped:
    #: `:root` is the html element (all elements inherit from it), `html` is itself, and
    #: `*` matches everything. Only the third has real cascade caveats, and none of them
    #: can leave an element without the token, which is what this ratchet asks.
    DOCUMENT_WIDE_SELECTORS = frozenset({":root", "html", "*"})

    @classmethod
    def _scoped_family_tokens(cls, css):
        """Tokens a family value asks for that are declared only on a SCOPED rule.

        The documented fail-GREEN limit is `VAR_DEF` collecting a custom property from
        any rule whatever its selector. It is a limit and not a bug because a token on
        `:root` reaches every element (custom properties inherit, css-variables-1; MDN
        `--*`: "custom properties are scoped to the element(s) they are declared on",
        `Inherited: yes`), so the flat map and a browser agree on every page in this
        tree. This helper is the mechanical form of that sentence: it returns
        `[(token, declaring_selectors)]` for every token a `font-family`/`font` value
        references whose declaring rules are NOT document-wide. Non-empty = the limit is
        LIVE on that sheet, i.e. the class docstring's "latent, not live" no longer
        holds and the flat map can credit a face the element never computes.
        """
        declared = {}
        for selectors, body in cls.RULE.findall(css):
            for name, _value in cls.VAR_DEF.findall(body):
                declared.setdefault(name.strip(), set()).update(
                    s.strip() for s in selectors.split(",") if s.strip())
        referenced = set()
        for _selectors, body in cls.RULE.findall(css):
            for family in cls.FAMILY.findall(body):
                referenced.update(t.strip() for t in cls.VAR_REF.findall(family))
        return [
            (token, sorted(declared[token])) for token in sorted(referenced)
            # a token declared nowhere is a different defect (the guard refuses it, fail
            # closed), and it is not this one: there is no scope to be wrong about.
            if token in declared and not declared[token] <= cls.DOCUMENT_WIDE_SELECTORS
        ]

    def test_the_scoped_token_limit_is_latent_on_every_page_it_decides_on(self):
        """Every `var()` family on a page it DECIDES on resolves a document-wide token.

        The class docstring justifies leaving the declaring-scope limit unmodelled with a
        MEASURED claim — "latent, not live" — and that claim was wrong in the tree twice
        while it was hand-written prose: round 10 and round 11 both re-measured "all nine
        subjects define `--mono` in `:root`" as EIGHT of nine
        (`website/apps/dashboard/public/404.html` names the family list directly and
        resolves no `var()`, so the limit cannot be tripped there). A count in a comment
        is not a guard; this is. It REDs the moment a page hands an element a family
        through a token declared on a scoped selector, which is the day the limit stops
        being latent — and the repair is then either moving the token to `:root` or
        stating the limit as LIVE.
        """
        repo = Path(__file__).resolve().parent.parent
        offenders, checked = [], 0
        for path in self._sources(repo):
            text = path.read_text(errors="replace")
            if not self.RENDERS_CODE.search(text):
                continue
            css = "\n".join(self.STYLE.findall(
                self.COMMENT.sub(" ", self.INERT.sub(" ", text))))
            if not css.strip():
                continue
            checked += 1
            for token, scopes in self._scoped_family_tokens(css):
                offenders.append(
                    f"{path.relative_to(repo)}: {token} declared on "
                    f"{', '.join(scopes)} — a family value asks for it")
        assert checked >= self.MIN_PAGES, (
            f"only {checked} self-contained page(s) found under "
            f"{'/'.join(self.ROOTS)} — the scan is not looking at the right tree")
        assert not offenders, (
            "the documented declaring-scope limit is no longer LATENT — these pages "
            "feed a `code`/`pre` family through a token declared only on a scoped rule, "
            "so the guard credits a face the browser never computes for the element "
            "(custom properties inherit: a token on `.art-body` does not reach a "
            "`<code>` outside it, so the reference is the guaranteed-invalid value and "
            "the whole declaration is invalid at computed-value time):\n  "
            + "\n  ".join(offenders)
            + "\nMove the token to `:root` (or another document-wide selector), or "
              "state the limit as LIVE in the class docstring and extend "
              "`test_a_scoped_token_is_credited_and_that_is_a_documented_limit` — "
              "closing it silently is the one thing this file forbids.")

    def test_the_scoped_token_predicate_separates_the_three_shapes(self):
        """Non-vacuity for the ratchet above: it must RED the scoped shape and only it.

        Without this, `_scoped_family_tokens` returning `[]` on the real tree is
        indistinguishable from a predicate that returns `[]` always — the failure mode
        the sibling class records for a guard whose subject went missing.
        """
        assert self._scoped_family_tokens(
            ":root{--mono:Menlo} code{font-family:var(--mono)}") == []
        assert self._scoped_family_tokens(
            "code{font-family:Menlo}") == []          # the ninth subject's shape
        scoped = self._scoped_family_tokens(
            ".art-body{--mono:Menlo} code{font-family:var(--mono)}")
        assert scoped == [("--mono", [".art-body"])], (
            "the ratchet above no longer recognises the documented limit's own shape — "
            f"its clean verdict on the real tree means nothing. Got {scoped!r}")
        # and the `var()`-under-another-name route, which the flat map covers equally
        assert self._scoped_family_tokens(
            "main{--code-font:Menlo} code{font-family:var(--code-font)}"
        ) == [("--code-font", ["main"])]

    def test_a_nested_var_is_resolved_the_way_the_browser_resolves_it(self):
        """A `var()` inside a fallback, and an unresolvable reference in a list (#10254 r6).

        The fallback of a `var()` is *itself substituted* by css-variables-1 §"replace a
        var()" step 3, so `var(--mono, var(--brand, Menlo))` computes to whatever
        `--brand` is. Reading the fallback as flat text — the reference regex stopped at
        the first `)`, so it never saw `Menlo` as living inside another `var()` — credited
        a **serif** as a mono face: the round-6 review's false green, reproduced against
        this class's own logic before it was fixed.

        The last two rows are the other direction of the same mistake: a reference that
        resolves to nothing invalidates the **whole** declaration at computed-value time,
        and CSS does not then fall through to the next family in the list — so a trailing
        `Menlo` written after an unresolvable `var()` is not a face the element gets.
        """
        cases = [
            # (why, css, is `code` given a mono face?)
            ("a serif inside the fallback's own var() — the round-6 false green",
             ":root{--brand:Georgia,serif} "
             "code{font-family:var(--mono, var(--brand, Menlo))}", False),
            ("the same shape, inner token missing → the browser uses its fallback",
             "code{font-family:var(--mono, var(--brand, Menlo))}", True),
            ("the same shape, `--mono` defined → the outer fallback is dead text",
             ":root{--mono:Menlo} code{font-family:var(--mono, var(--brand, Menlo))}", True),
            ("the same shape, `--mono` defined as a serif",
             ":root{--mono:Georgia,serif} "
             "code{font-family:var(--mono, var(--brand, Menlo))}", False),
            ("an unresolvable `var()` invalidates the whole list, not just its slot",
             "code{font-family:var(--mono), Menlo}", False),
            ("a token whose own value carries an unresolvable var() is guaranteed-invalid",
             ":root{--mono:Menlo,var(--nope)} code{font-family:var(--mono)}", False),
            # the invalid signal has to survive the recursion: returning empty *text*
            # appended `Menlo,` for the next family, so a declaration CSS invalidates
            # whole read as a mono face (round 8's false green)
            ("an invalid token voids the declaration around it — the trailing face is dead",
             ":root{--mono:var(--nope)} code{font-family:Menlo, var(--mono)}", False),
            ("the same, leading face first",
             ":root{--mono:var(--nope)} code{font-family:var(--mono), Menlo}", False),
            ("an unbalanced var( is a parse error, so no family in the value applies",
             "code{font-family:Menlo, var(--mono}", False),
        ]
        for why, css, expect in cases:
            got = "code" in self._elements_styled_mono(css)
            assert got == expect, (
                f"{'false green' if got else 'false red'}: {why}\n  css: {css}\n"
                f"  guard says mono={got}, CSS computes mono={expect}")

    #: the self-contained emitters this guard's `ROOTS` does NOT reach, each carrying the
    #: `<style>` block that makes it a page the sibling declaration guard scans.
    OUT_OF_ROOT_EMITTERS = ("tortoise/oauth.py", "tortoise/render.py")

    def test_the_out_of_root_emitters_render_no_code(self):
        """The scope limit is stated, so its precondition is pinned instead of assumed.

        `Scope` above names two self-contained emitters that carry their own sheet but
        sit outside `ROOTS` (`tortoise/`, not `website/` + `apps/`), and that sentence is
        only true while neither renders a `<code>`/`<pre>`: an element there is one this
        scan cannot see, so the Courier defect (#6870) would have no guard on that
        surface at all. A narrow, deliberate exclusion is exactly the kind of claim that
        rots silently, so the precondition is measured here rather than trusted.

        Non-vacuity: the same predicate is applied first to the pages INSIDE `ROOTS`, and
        must find at least `MIN_PAGES` of them — a predicate that had stopped seeing
        elements would otherwise pass this test by finding nothing anywhere.

        The exclusion itself is pinned by the two properties that actually make these
        emitters invisible to this scan, each with the mutation that reds it. Asserted
        over `inside` instead — the shape this test shipped with — it could never fire:
        `inside` is derived from `_sources`, i.e. from `ROOTS`, so no member of it can be
        a path outside `ROOTS`, and an assertion no edit can falsify pins nothing
        (#10254 review P3). Being outside `ROOTS` is one half; the other is that `.py` is
        not in `SUFFIXES`, so the walk would not read them even under a widened root.
        """
        repo = Path(__file__).resolve().parent.parent

        def _visible(rel):
            text = (repo / rel).read_text(errors="replace")
            return self.COMMENT.sub(" ", self.INERT.sub(" ", text))

        scanned = {p.relative_to(repo).as_posix() for p in self._sources(repo)}
        inside = [rel for rel in sorted(scanned) if self.RENDERS_CODE.search(_visible(rel))]
        assert len(inside) >= self.MIN_PAGES, (
            "this predicate finds no element even on the pages this guard scans "
            f"({inside}) — it is not measuring what it claims")

        under_root = [rel for rel in self.OUT_OF_ROOT_EMITTERS
                      if rel.split("/")[0] in self.ROOTS]
        assert not under_root, (
            f"{under_root} now sit under a `ROOTS` entry ({self.ROOTS}) — the scope note "
            "in this class says they are outside it, so either narrow `ROOTS` back or "
            "adopt the emitter the way the message below describes")
        readable = [rel for rel in self.OUT_OF_ROOT_EMITTERS
                    if Path(rel).suffix in self.SUFFIXES]
        assert not readable, (
            f"{readable} carry a suffix this scan reads ({self.SUFFIXES}) — the suffix "
            "set is half of why these emitters are invisible here, so adopting `.py` "
            "makes the scope note's justification false even while `ROOTS` is unchanged")
        reachable = [rel for rel in self.OUT_OF_ROOT_EMITTERS if rel in scanned]
        assert not reachable, (
            f"{reachable} are yielded by `_sources` — this scan reads a page the scope "
            "note claims it cannot, so the exclusion is no longer deliberate")

        grew = []
        for rel in self.OUT_OF_ROOT_EMITTERS:
            text = _visible(rel)
            assert self.STYLE.search(text), (
                f"{rel} no longer carries its own `<style>` block — the scope note in "
                "this class names it as a self-contained page, so either the note or "
                "this list is now stale")
            if self.RENDERS_CODE.search(text):
                grew.append(rel)
        assert not grew, (
            "these self-contained emitters sit OUTSIDE this guard's ROOTS and have grown "
            "a `<code>`/`<pre>` element — that element has no mono rule this scan can "
            "see, which is the #6870 defect on a surface with no guard. Either give the "
            "element a mono family inside the emitter, or declare the file in `ROOTS` "
            "and extend the ratchet, so the exclusion stays deliberate:\n  "
            + "\n  ".join(grew))
