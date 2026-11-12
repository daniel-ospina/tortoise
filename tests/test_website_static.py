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
6. `/investors` Progress cell order + pinned bullet copy (PR #9283): the section
   must OPEN on what exists (`Product & distribution` left of `Early demand`),
   every bullet in both cells is pinned verbatim — curly quotation marks
   included — and the retired `20+ prospective beta testers` line must stay
   gone. The verbatim quotation is an ACCEPTED RISK recorded by the owner, not
   a defect to "fix" — see `TestInvestorsProgressCopy`.

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
import hashlib
import json
import os
import re
import struct
import sys
import tempfile
import zlib
from html import unescape
from html.parser import HTMLParser
from itertools import pairwise
from pathlib import Path
from typing import ClassVar
from urllib.parse import urljoin, urlsplit

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


def _ends_inside_attribute_value(section: str) -> bool:
    """True when the slice stops inside a tag, i.e. inside an attribute value.

    A `</section>` carried in a quoted OR unquoted attribute value is not an end
    tag to a browser, but it ends this slice, so whatever follows it renders
    while no assertion below can see it.

    The verdict comes from the tokenizer, not from a delimiter scan: an
    incomplete tag is exactly what `HTMLParser` leaves in `rawdata` after
    `feed()`, and a value may itself contain `<` or `>` (both are parse errors the
    tokenizer folds into the value), which defeats any `rfind("<")` or regex.

    Trailing TEXT such as `<p>Ratio = 2` or `<div class="x">Ratio = 2` is a
    complete document fragment, so it is not reported here — the `stray`
    assertion names it accurately, where an attribute report would send a
    maintainer hunting for an attribute that does not exist.
    """
    probe = HTMLParser(convert_charrefs=True)
    probe.feed(section)
    return bool(probe.rawdata)


def _served_without_comments(markup: str) -> str:
    """`_served`, except a comment is DELETED rather than replaced by a space.

    A comment contributes neither text nor whitespace, so a space here
    synthesises a word boundary no reader sees: `<span>2M+</span><!--x-->
    <span>views</span>` renders `2M+views` but would pin as `2M+ views`. This is
    the variant `#progress`'s text path slices with; `_served` is unchanged for
    its existing callers, which take structure (and, in the pricing path, text)
    from markup where a comment never sits between two words.
    """
    markup = re.sub(r"<!--.*?-->", "", markup, flags=re.S)
    markup = re.sub(r"<script\b.*?</script>", " ", markup, flags=re.S | re.I)
    markup = re.sub(r"<style\b.*?</style>", " ", markup, flags=re.S | re.I)
    return re.sub(r"<!--.*", "", markup, flags=re.S)


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


def _norm(parts: list[str]) -> str:
    """Rendered text from collected chunks: concatenated, whitespace collapsed.

    Concatenated, NOT joined with a separator: `<span>2M+</span><span>views</span>`
    renders `2M+views`, and a synthesised space would let that change pass the
    pins while a reader sees different text. Spacing therefore has to come from
    the source — `handle_data` keeps whitespace-only chunks for exactly that.
    """
    return re.sub(r"\s+", " ", "".join(parts)).strip()


#: Declarations that stop text reaching a reader, checked on an inline
#: `style=`. There is no stylesheet path on purpose (see the class docstring).
#: The zero values are anchored at a property boundary and accept every SIGNED
#: and decimal spelling of zero (`0`, `.0`, `00`, `+0`, `-0`, `0.0px`) — but not
#: a computed one (`calc(0)`), which no regular expression can see. Two pieces
#: each earn their place: the `(?![.\d])` lookahead is what keeps an honest
#: `opacity: 0.35` or `font-size:0.9em` from reading as a zero, while
#: `(?<![-\w])` keeps a DIFFERENT property whose name ends in `color`
#: (`background-color: transparent`) from reading as `color: transparent`.
_HIDING_DECL = re.compile(
    r"(?<![-\w])(?:"
    r"display\s*:\s*none"
    r"|visibility\s*:\s*hidden"
    r"|opacity\s*:\s*[+-]?(?:0+(?:\.0*)?|\.0+)(?:[eE][+-]?\d+)?(?![.\d])"
    r"|color\s*:\s*transparent"
    r"|font-size\s*:\s*[+-]?(?:0+(?:\.0*)?|\.0+)(?:[eE][+-]?\d+)?(?![.\d])"
    r"|content-visibility\s*:\s*hidden"
    r")",
    re.I)


class _ProgressTextParser(HTMLParser):
    """Reads the served `#progress` markup STRUCTURALLY instead of by regex.

    A regex over markup cannot tell a tag from a `<` in copy, mis-splits a tag
    whose attribute value contains `>`, and never sees copy carried in `alt=` or
    `title=`. Walking the document gets those right by construction, so what
    this parser reports is exactly what a TEXT pin cannot cover:

    - `attribute_copy` — copy-bearing attributes with a non-empty value: they
      render, and a pin on text cannot see them. `srcdoc` is included because an
      `<iframe>`'s document renders inside the section.
    - `hidden` — text that does not reach every reader, or reaches none: an
      element marked `hidden`, one whose inline `style` hides it
      (`_HIDING_DECL`), or text inside `aria-hidden="true"`. The last one is an ACCESSIBILITY defect, not a
      visual one — a sighted reader still sees that text — and it is text-only
      so the page's own decorative `<svg aria-hidden="true">` still passes.
    - `stray` — text outside the title, the headings and the bullets: a claim
      added anywhere else in the section, including between a heading and its
      list, and in a list that precedes every heading.

    Scope, stated plainly: this is a TEXT gate. It pins the words the section
    renders and refuses text this walk cannot attribute to a heading or a
    bullet. It is NOT a defence against CSS: `clip`, `text-indent`, an
    off-screen `position`, a `transform`, or any rule in the page stylesheet can
    make text invisible without the walk noticing, and an attempted check for
    those was removed rather than kept — it was unsound both ways (it matched
    the page's own `html.gated section { display: none }` and missed indirection
    through a class). A hidden-claim audit is a different instrument.

    Markup that moves without changing a word (a `class`/`id`, a `<ul>` wrapped
    in a `<div>`, an `<hr>`) carries no text and therefore passes. Text is
    collected per element, and `convert_charrefs` decodes `&ldquo;` to the curly
    quotation mark the page renders, so the tuples compare against what a reader
    reads.
    """

    COPY_ATTRS = ("alt", "title", "placeholder", "aria-label", "value", "srcdoc")
    #: HTML void elements never close, so `handle_starttag` does not push them.
    #: Their synthetic end tag therefore has nothing to match and closes nothing
    #: (`handle_endtag` only pops on a match); the set is here so the self-closing
    #: override never treats one as ordinary HTML and tries to close it.
    VOID = frozenset((
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    ))
    HIDING_STYLE = _HIDING_DECL

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        # (tag, aria_hidden) — the flag rides the stack so `aria-hidden` only
        # counts when it wraps TEXT.
        self.stack: list[tuple[str, bool]] = []
        self.cells: list[tuple[str, list[str]]] = []
        self.title: list[str] = []
        self.attribute_copy: list[str] = []
        self.hidden: list[str] = []
        self.stray: list[str] = []
        self._heading: list[str] = []
        #: Whether a heading OPENED this cell — separate from its text being
        #: non-empty, so `<h3></h3>` still owns the bullets that follow it.
        self._heading_seen = False
        self._bullets: list[str] = []
        self._li: list[list[str]] = []
        #: list nesting depth when each `_li` buffer opened — `</li>` is
        #: optional before the next sibling `<li>`, but a nested list inside an
        #: `<li>` must NOT be read as a sibling.
        self._li_depth: list[int] = []
        self._list_depth = 0

    def _flush_open_li(self) -> None:
        """Close any `<li>` still open (the browser auto-closes it), so its text
        reaches the pins instead of being carried into the next cell.

        Closing is inner-first, mirroring `handle_endtag`: a nested list inside
        an `<li>` merges into the bullet that contains it, or the inner items
        would surface as siblings in the wrong order.
        """
        while len(self._li) > 1:
            inner = _norm(self._li.pop())
            self._li_depth.pop()
            self._li[-1].append(inner)
        while self._li:
            self._bullets.append(_norm(self._li.pop()))
            self._li_depth.pop()

    def _close_cell(self) -> None:
        """Finish the cell whose bullets have all been seen (a cell is complete
        when the NEXT heading opens, or the document ends).

        Bullets collected before any heading exists have no cell to belong to,
        so they are reported as stray. Bullets collected AFTER the last heading
        belong to that (still open) cell and reach its tuple — either way they
        are pinned or they fail; neither path drops text.

        "A heading was seen" is a FLAG, not `self._heading` being non-empty: an
        `<h3></h3>` (or one holding only a comment or an icon) carries no text
        yet still opens a cell, and reading emptiness as "no heading" reported
        that cell's pinned bullets as stray.
        """
        self._flush_open_li()
        if self._heading_seen:
            self.cells.append((_norm(self._heading), list(self._bullets)))
        else:
            self.stray.extend(b for b in self._bullets if b)
        self._heading, self._bullets = [], []
        self._heading_seen = False

    def handle_starttag(self, tag: str, attrs) -> None:
        aria_hidden = False
        for name, value in attrs:
            low = name.lower()
            if low in self.COPY_ATTRS:
                if value:
                    self.attribute_copy.append(f"<{tag} {name}={value!r}>")
                continue
            if low == "hidden" or (low == "aria-hidden" and (value or "").lower() == "true"):
                if low == "hidden":
                    self.hidden.append(f"<{tag} {name}>")
                else:
                    aria_hidden = True
            elif low == "style" and value and self.HIDING_STYLE.search(value):
                self.hidden.append(f"<{tag} style={value!r}>")
        if tag not in self.VOID:
            self.stack.append((tag, aria_hidden))
        if tag in ("ul", "ol"):
            self._list_depth += 1
        elif tag == "h3":
            self._close_cell()
            self._heading_seen = True
        elif tag == "li":
            if self._li and self._li_depth[-1] == self._list_depth:
                # Implicit close: `</li>` is optional before the next sibling.
                # It merges into an enclosing bullet exactly as an explicit
                # `</li>` does, so ordering cannot depend on the omission.
                self._li_depth.pop()
                text = _norm(self._li.pop())
                if self._li:
                    self._li[-1].append(text)
                else:
                    self._bullets.append(text)
            self._li.append([])
            self._li_depth.append(self._list_depth)

    def handle_startendtag(self, tag: str, attrs) -> None:
        """A self-closing tag is closed only where a browser closes it.

        The default fires BOTH halves, which is wrong on an ordinary HTML element
        (`<li/>`, `<p/>`): the slash is a parse error there, so the element stays
        OPEN and closing it would merge or drop the text that follows. Only
        foreign content (SVG/MathML) honours the slash. Void elements are left to
        `handle_starttag`, which never pushes them.
        """
        self.handle_starttag(tag, attrs)
        foreign = tag in ("svg", "math") or any(
            t in ("svg", "math") for t, _ in self.stack)
        if foreign:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        # Only a MATCHING end tag closes anything. An end tag with nothing to
        # match — `</br>` on a void element, a stray or mistyped close — is
        # ignored by a browser, and popping for it would drop every open
        # ancestor: an enclosing `aria-hidden` would stop applying, and text
        # after `<h3>Early<br></br>demand</h3>` would be misrouted to stray.
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if tag in ("ul", "ol"):
            # A closing list closes the items it opened: `</li>` is optional
            # before `</ul>` too, and leaving a buffer open would nest the next
            # SIBLING item into the item before it.
            while self._li and self._li_depth[-1] >= self._list_depth:
                self._li_depth.pop()
                text = _norm(self._li.pop())
                if self._li:
                    self._li[-1].append(text)
                else:
                    self._bullets.append(text)
            self._list_depth = max(0, self._list_depth - 1)
        if tag == "li" and self._li:
            self._li_depth.pop()
            text = _norm(self._li.pop())
            if self._li:
                # a nested list inside an <li> merges into the outer bullet:
                # still pinned, and a mismatch still fails.
                self._li[-1].append(text)
            else:
                self._bullets.append(text)

    def handle_data(self, data: str) -> None:
        if not data:
            return
        if data.strip() and any(aria for _, aria in self.stack):
            self.hidden.append(f"aria-hidden text {data.strip()!r}")
        if self._li:
            self._li[-1].append(data)
        elif any(tag == "h3" for tag, _ in self.stack):
            self._heading.append(data)
        elif any(tag == "h2" for tag, _ in self.stack):
            self.title.append(data)
        elif data.strip():
            # Indentation between elements is not a claim; real text is.
            self.stray.append(data.strip())

    def close(self) -> None:
        super().close()
        self._close_cell()


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
# 6. /investors Progress: cell order + both cells' pinned bullets (PR #9283)
# ═══════════════════════════════════════════════════════════════════════════════


def _investors_progress_section() -> str:
    """The `<section id="progress">…</section>` slice, from the SECTION tag, as
    a READER receives it — `_served_without_comments` strips comments (deleting
    them, not spacing them) before the slice is taken.

    Serving first is load-bearing, not tidiness: slicing the raw file and
    filtering afterwards cannot see a comment that opens BEFORE the section, so
    `<!-- <section id="progress"> … </section> -->` left every assertion in this
    class green while no reader receives the section at all — the exact failure
    `_served` exists to prevent. `_pricing_section` slices the stripped document
    for the same reason.

    Same construction (and the same nested-section guard) as
    `_automate_beat_markup`: the slice must start at `<section`, and this slice
    stops at the FIRST `</section>`, so a claim inside a nested section would be
    invisible to every assertion below.
    """
    src = _served(INVESTORS_HTML.read_text(encoding="utf-8"))
    raw = INVESTORS_HTML.read_text(encoding="utf-8")
    marker = 'id="progress"'
    # Strip comments BEFORE locating anything in the raw file: a commented
    # `</section>` would end the raw region early, and a comment that merely
    # NAMES the marker would make the raw and served slices describe different
    # sections.
    raw_wo_comments = re.sub(r"<!--.*?-->", " ", raw, flags=re.S)
    assert marker in src, (
        "#progress is absent from website/investors.html as served — moved, "
        "renamed, or commented out. This gate's subject is gone; re-point it at "
        "wherever the Progress section lives (PR #9283)")
    assert src.count(marker) == 1 and raw_wo_comments.count(marker) == 1, (
        f"website/investors.html mentions {marker} "
        f"{src.count(marker)}x as served / {raw_wo_comments.count(marker)}x raw. "
        "This gate slices raw and served separately and they must describe the "
        "same section (PR #9283)")
    # `_served` deletes <style>/<script> bodies, so copy they inject (a
    # `content:` string appended to every heading, say) renders on the page and
    # is invisible to every assertion below. Only the RAW region can show them.
    raw_start = raw_wo_comments.rindex("<section", 0, raw_wo_comments.index(marker))
    raw_section = raw_wo_comments[raw_start:raw_wo_comments.index("</section>", raw_start)]
    assert not re.search(r"<(style|script)\b", raw_section, re.I), (
        "#progress carries a <style> or <script>. `_served` removes its body "
        "before this gate slices the section, so text it injects renders while "
        "no assertion can see it. Move the rule into the page stylesheet and "
        "delete it here (PR #9283).")
    text_src = _served_without_comments(INVESTORS_HTML.read_text(encoding="utf-8"))
    i = text_src.index(marker)
    start = text_src.rindex("<section", 0, i)
    section = text_src[start:text_src.index("</section>", start)]
    assert "<section" not in section[section.index(">") + 1:], (
        "a <section> is nested inside #progress — this slice stops at the first "
        "</section>, so a claim in the nested section would be invisible to "
        "every assertion in this class (PR #9283)")
    # Testing for a `</section>` literal inside `section` cannot work — the
    # slice stops BEFORE it. The truncation itself is what shows up: the slice
    # ends inside an attribute value.
    assert not _ends_inside_attribute_value(section), (
        "#progress's slice ends inside an attribute value — a `</section>` "
        "carried in one truncated it early, so whatever follows renders "
        "unchecked (PR #9283)")
    return section


class _ProfileCellCounter(HTMLParser):
    """Counts the DIRECT `<div>` children of the section's ONE `.profile` block.

    "Direct" is structural: a `<div>` counts only when no other element is open
    inside `.profile`, so wrapping the cells in a `<span>` (which leaves the grid
    with one child) counts nothing, while wrapping them in a `<div>` counts one.

    Two children is a NECESSARY condition for the side-by-side read, not a
    sufficient one: the columns themselves come from the `.profile` declaration,
    which this counter does not read, so a `grid-template-columns: 1fr` edit
    stacks them and stays green. What it removes is the specific bypass that
    merged both cells into one child while keeping two `<h3>`s.

    `count` is summed across every `.profile` block, so it says nothing on its
    own about how many blocks there are: splitting the section into two
    `.profile` divs (one cell each) also yields 2 while the columns stack.
    `profiles` is what rules that out.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.count = 0
        self.profiles = 0
        self._inside = False
        #: Elements open INSIDE the `.profile` div. Empty means a `<div>`
        #: starting now is a direct child; a `<span>`/`<a>` wrapper puts its
        #: contents one level down, where they are not grid children.
        self._open: list[str] = []

    def handle_startendtag(self, tag: str, attrs) -> None:
        """HTML ignores the self-closing slash, so the element stays OPEN.

        The default start-then-end pair would close a `<div/>` the browser keeps
        open, and every child after it would fall outside the profile.
        """
        self.handle_starttag(tag, attrs)

    def handle_starttag(self, tag: str, attrs) -> None:
        if not self._inside:
            if (tag == "div"
                    and "profile" in (dict(attrs).get("class") or "").split()):
                self._inside = True
                self.profiles += 1
                self._open = []
            return
        if tag == "div" and not self._open:
            self.count += 1
        if tag not in _ProgressTextParser.VOID:
            self._open.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self._inside:
            return
        if tag == "div" and not self._open:
            self._inside = False
            return
        # An unmatched end tag closes nothing (a browser ignores it); popping
        # for it would close the profile on a stray `</span>`.
        for i in range(len(self._open) - 1, -1, -1):
            if self._open[i] == tag:
                del self._open[i:]
                break


def _walk_progress_section(markup: str) -> _ProgressTextParser:
    """Run the walk over `markup` placed inside a minimal `#progress` shell.

    Synthetic markup, not the live page: it reaches parser branches the served
    section does not exercise today, so a branch cannot regress to silently
    allowing a claim while the pins stay green.
    """
    parser = _ProgressTextParser()
    parser.feed(
        '<section id="progress"><div class="wrap"><h2>Progress</h2>'
        f'<div class="profile">{markup}</div></div></section>')
    parser.close()
    return parser


def _progress_cells() -> list[tuple[str, list[str]]]:
    """`(h3 heading, rendered bullets)` per cell, in DOM order.

    The walk is `_ProgressTextParser`, over the SERVED section
    (`_investors_progress_section`), so a comment is not a claim a reader
    receives. The parser refuses — rather than silently ignoring — the three
    things a text pin cannot cover: copy in an attribute, a hidden element, and
    any text outside the title, the headings and the bullets. Markup that moves
    without changing a word does not fail.
    """
    parser = _ProgressTextParser()
    parser.feed(_investors_progress_section())
    parser.close()
    assert not parser.attribute_copy, (
        "#progress carries copy in an attribute: "
        f"{parser.attribute_copy!r}. It renders, but these pins compare TEXT and "
        "cannot see it — put the claim in a <li> and pin it, or drop the value "
        "(PR #9283).")
    assert not parser.hidden, (
        f"#progress hides an element: {parser.hidden!r}. A claim that reaches no "
        "reader while its pin stays green is the failure this gate exists for: "
        "for a `hidden`/inline-`style` hit delete the bullet and its tuple "
        "instead of hiding it, and for an `aria-hidden` hit remove the attribute "
        "— a sighted reader still sees that text, so deleting the copy would "
        "hide a real claim from everyone (PR #9283).")
    assert _norm(parser.title) == "Progress", (
        f"#progress's own title is {_norm(parser.title)!r}, not 'Progress' — a "
        "claim can hide there and no pinned tuple covers it (PR #9283)")
    assert not parser.stray, (
        "#progress carries text outside the pinned structure: "
        f"{parser.stray!r}. No EXPECTED_*_BULLETS tuple can see it — put the "
        "claim in a <li> and pin it, or remove it (PR #9283).")
    return parser.cells


class TestInvestorsProgressCopy:
    """PR #9283: the Progress section's cell order and both cells' pinned bullets.

    THREE failure modes.

    1. **The cell order silently reverts.** `.profile` is
       `display: grid; grid-template-columns: 1fr 1fr` with no `direction`
       override. Two rules reach `.profile`'s COLUMNS (typography does not
       affect order): `#gtm .profile > div:last-child` (scoped to `#gtm`) and
       the 620px breakpoint
       (`.outcomes, .profile { grid-template-columns: 1fr }`). Neither REORDERS
       a cell — one spans the last GTM cell, the other stacks the columns — so
       DOM order remains the rendered order at every width, and the section must
       OPEN on what exists (`Product & distribution`) rather than on the demand
       claim. Rendering collapses the difference: there is no diff marker, no
       test, and no CSS selector that notices a swap.

    2. **A traction figure moves without the owner.** These bullets are the
       claims this page makes to investors, so each one is pinned verbatim, in
       DOM order. A figure that changes is a claim the page cannot back — the
       provenance is recorded next to the tuples.

    3. **The quotation is paraphrased.** A reviewer P2 — "the quotation asserts
       a verbatim utterance nobody recorded" — was actioned by an agent in
       `fbd706a` as a rewrite of the owner's line into indirect speech (`asked
       when they could use it`). The owner reverted the rewrite in `cd864dc`
       and recorded the P2 as an ACCEPTED RISK: the copy is the owner's, and a
       reviewer finding does not authorise an agent to rewrite it. This pin
       makes the next such rewrite fail here instead of shipping
       (`tortoise#9283`).

    Scope, stated plainly: this is a hand-maintained SECOND COPY of prose over
    one static page's markup. Within `#progress` the extraction is fail-closed
    (`_progress_cells`): copy in an attribute, text hidden by one of the inline
    declarations in `_HIDING_DECL` or by a `hidden` attribute, an injected
    `<style>`/`<script>`, and any text outside the pinned `<h3>`/`<li>` text all
    fail rather than hiding.

    NOT covered, and deliberately: a claim injected at runtime; a claim on
    another page; and ANY hiding done from the page stylesheet, whole section or
    one bullet. A stylesheet check was written and then removed — it matched the
    page's own `html.gated section { display: none }` and missed indirection
    through a class, so it bought false reds in exchange for coverage it could
    not deliver. A hidden-claim audit is a different instrument.
    """

    # Rendered left/right order of the two cells: what exists, then the demand.
    EXPECTED_CELL_ORDER = ("Product & distribution", "Early demand")
    # Both cells' bullets, verbatim and in DOM order — entities decoded, so the
    # curly quotation marks are pinned too (ASCII quotes are NOT this copy).
    #
    # Both cells are pinned because these are the traction claims this page
    # makes to investors: a figure that changes without the owner saying so is a
    # claim the page cannot back. Provenance for the current figures: the
    # submitted a16z Speedrun application video (2026-11-01) — "more than two
    # million views", "over 40 calls", "more than 35 people now waiting". The
    # page renders that last one as `35+ waiting to try it`.
    EXPECTED_PRODUCT_BULLETS = (
        "Working alpha, beta in development",
        "2M+ views on founder-led content, generating inbound leads",
    )
    EXPECTED_DEMAND_BULLETS = (
        "40+ calls: \u201cwhen can I use it\u201d",
        "35+ waiting to try it",
        "1 team design partner providing data & feedback",
        "Enterprise discussions underway",
    )
    # What an earlier revision carried. It must not come back: "prospective"
    # disclaims the noun it modifies.
    RETIRED_LINE = "20+ prospective beta testers"

    def test_progress_opens_on_what_exists(self):
        """Left cell is `Product & distribution` — DOM order is render order."""
        counter = _ProfileCellCounter()
        counter.feed(_investors_progress_section())
        counter.close()
        assert counter.profiles == 1, (
            f"#progress must hold ONE `.profile` block, found {counter.profiles} "
            "— two blocks (one cell each) stack the columns instead of placing "
            "them side by side, which is the point of the section (PR #9283)")
        assert counter.count == 2, (
            f"#progress must hold exactly two `.profile` cells, found "
            f"{counter.count} — the side-by-side read (what exists | what is "
            "claimed) is the point of the section, and merging them into one "
            "child leaves the right column empty (PR #9283)")
        cells = _progress_cells()
        assert len(cells) == 2, (
            f"#progress must carry exactly two cells, found {len(cells)} "
            "headings (PR #9283)")
        headings = tuple(heading for heading, _ in cells)
        assert headings == self.EXPECTED_CELL_ORDER, (
            f"#progress cell order changed: {headings!r} != "
            f"{self.EXPECTED_CELL_ORDER!r}. The section must open on what "
            "EXISTS ('Product & distribution'); the demand claim belongs in the "
            "right cell (PR #9283). DOM order is the rendered order — the grid "
            "is `1fr 1fr` with no `direction` override and no positional rule "
            "reaching #progress — so swapping the two <div>s inverts the page.")

    def test_product_bullets_are_the_sanctioned_copy_verbatim(self):
        """The `Product & distribution` cell, character-for-character."""
        cells = dict(_progress_cells())
        assert "Product & distribution" in cells, (
            f"the 'Product & distribution' cell is gone (cells: {sorted(cells)}) "
            "— this gate's subject moved (PR #9283)")
        actual = tuple(cells["Product & distribution"])
        assert actual == self.EXPECTED_PRODUCT_BULLETS, (
            f"the Product & distribution bullets changed: {actual!r} != "
            f"{self.EXPECTED_PRODUCT_BULLETS!r}. These are traction claims made "
            "to investors — the submitted application video is the source for "
            "the `2M+ views` figure, so take it from there, never paraphrase it "
            "from a nearby draft.")

    def test_early_demand_bullets_are_the_sanctioned_copy_verbatim(self):
        """The whole `Early demand` cell, character-for-character."""
        cells = dict(_progress_cells())
        assert "Early demand" in cells, (
            f"the 'Early demand' cell is gone (cells: {sorted(cells)}) — this "
            "gate's subject moved (PR #9283)")
        actual = tuple(cells["Early demand"])
        assert actual == self.EXPECTED_DEMAND_BULLETS, (
            f"the Early-demand bullets are not the owner's copy verbatim: "
            f"{actual!r} != {self.EXPECTED_DEMAND_BULLETS!r}. The first bullet "
            "is the owner's quotation — ⛔ DO NOT 'fix' it by paraphrasing it "
            "to indirect speech: that edit was made in fbd706a (a reviewer P2 "
            "about the quotation asserting a verbatim utterance nobody "
            "recorded), reverted by the owner in cd864dc, and the P2 recorded "
            "as an ACCEPTED RISK. The copy is the owner's call. If the copy "
            "genuinely changed, get the owner's wording and update this pin "
            "with it.")

    def test_the_hiding_declarations_and_their_zero_spellings(self):
        hiding = (
            "display:none", "visibility:hidden", "color:transparent",
            "content-visibility:hidden",
            # A zero value is the same declaration however it is spelled, sign
            # included: `.0`, `00` and `+0` hide the text just as `0` does, so a
            # parser anchored only on the bare form leaves a live bypass. A
            # computed zero (`calc(0)`) is out of reach by construction.
            "opacity:0", "opacity:0.0", "opacity:.0", "opacity:00",
            "opacity:+0", "opacity:-0",
            "font-size:0", "font-size:0.0px", "font-size:00", "font-size:+0",
        )
        for css in hiding:
            assert _HIDING_DECL.search(f"margin:0;{css}"), css

    def test_honest_styles_are_not_mistaken_for_hiding(self):
        for css in (
            "opacity:0.35", "opacity:.9", "opacity:1", "opacity:+0.5",
            "font-size:0.9em", "font-size:+.5em",
            "background-color:transparent", "transition:opacity 0.2s",
        ):
            assert not _HIDING_DECL.search(css), css

    def test_an_unterminated_attribute_value_is_refused(self):
        # A quoted value is the common shape, but an unquoted one truncates the
        # slice just as effectively: the tokenizer appends the `<` to the value,
        # so the browser never sees an end tag while this slice does.
        for truncated in ('<span title="a', "<span title='a", "<span title=a",
                          "</ul><span title=a",
                          # a value may contain `>` or `<`
                          '<span title="a>b', '<span data-x="a < b ',
                          "<span data-x=a<b "):
            assert _ends_inside_attribute_value(truncated), truncated
        for whole in ('<span title="a</section>b">', "<li><span>x</span></li>",
                      '<div class="profile">', "<ul>",
                      # A trailing text node is not an attribute value: the
                      # `stray` assertion is what should report it, and a closed
                      # tag before the `=` proves the text is outside the tag.
                      "</div><p>Ratio = 2", "</div><p>Revenue=2M",
                      '<div class="x">Ratio = 2', '<p class="note">Total ='):
            assert not _ends_inside_attribute_value(whole), whole

    def test_an_empty_heading_still_owns_its_bullets(self):
        # A heading that renders no text (`<h3></h3>`, or one holding only a
        # comment) still opens a cell. Reading its emptiness as "no heading"
        # reported that cell's pinned bullets as stray text.
        assert _walk_progress_section(
            "<h3></h3><ul><li>a</li><li>b</li></ul>").cells == [("", ["a", "b"])]
        assert _walk_progress_section(
            "<h3><!-- x --></h3><ul><li>a</li></ul>").cells == [("", ["a"])]
        # Bullets with NO heading at all still have no cell to belong to.
        orphan = _walk_progress_section("<ul><li>a</li></ul>")
        assert orphan.cells == [] and orphan.stray == ["a"]

    def test_a_comment_between_words_does_not_synthesise_a_space(self):
        # The served text a reader gets has no space there, so the pin must not
        # either; `_served` would turn the comment into one.
        assert _served_without_comments(
            "<span>2M+</span><!--x--><span>views</span>"
        ) == "<span>2M+</span><span>views</span>"
        assert _served_without_comments("a<!--x-->b") == "ab"
        # The space a reader DOES get is preserved.
        assert _served_without_comments("a <!--x--> b") == "a  b"

    def test_a_comment_inside_the_section_does_not_synthesise_a_space(self) -> None:
        # No comment sits inside the real section, so this reads the slice from a
        # temporary page: with `_served` (comment -> space) the bullet would pin
        # as `2M+ views` while a reader gets `2M+views`.
        global INVESTORS_HTML
        page = Path(tempfile.mkdtemp()) / "investors.html"
        page.write_text(
            '<section id="progress"><div class="wrap"><h2>Progress</h2>'
            '<div class="profile"><div><h3>Product</h3><ul class="bullets">'
            '<li><span>2M+</span><!--x--><span>views</span></li></ul></div>'
            '<div><h3>Demand</h3><ul class="bullets"><li><span>a</span></li>'
            '</ul></div></div></div></section>',
            encoding="utf-8")
        original, INVESTORS_HTML = INVESTORS_HTML, page
        try:
            assert dict(_progress_cells())["Product"] == ["2M+views"]
        finally:
            INVESTORS_HTML = original

    def test_a_nested_list_never_reorders_a_bullet(self):
        # `</li>` is optional before the next sibling, so the same markup is
        # read the same way whether it is written or omitted. Closing inner
        # first is what keeps the order; closing outer first emitted the inner
        # items as siblings ahead of their own parent.
        for markup, expected in (
            ("<li>a<ul><li>b<li>c</ul></li>", ["abc"]),
            ("<li>a<ul><li>b</li><li>c</li></ul></li>", ["abc"]),
            # `</li>` is optional before `</ul>` too: the item that contains the
            # nested list must close there, or the next SIBLING nests into it.
            ("<li>a<ul><li>b</ul><li>c</li><li>d</li>", ["ab", "c", "d"]),
            ("<li>a<ul><li>b</li></ul></li><li>c</li><li>d</li>", ["ab", "c", "d"]),
        ):
            assert _walk_progress_section(f"<h3>H</h3><ul>{markup}</ul>").cells == [("H", expected)]
        assert _walk_progress_section("<h3>H</h3><ul><li>a</li><li>b</li></ul>").cells == [
            ("H", ["a", "b"])]
        # An `<li>` left open at the END of the document, or at a cell boundary,
        # is still rendered by the browser: `_flush_open_li` is what carries it
        # into the pins. Without these three the flush body could be deleted and
        # every remaining case would still pass.
        unclosed = _walk_progress_section("<h3>H</h3><ul><li>claim</ul>")
        assert unclosed.cells == [("H", ["claim"])]
        assert not unclosed._li and not unclosed._li_depth
        assert _walk_progress_section(
            "<h3>H</h3><ul><li>claim").cells == [("H", ["claim"])]
        assert _walk_progress_section(
            "<h3>A</h3><ul><li>ax<h3>B</h3><ul><li>bx").cells == [
                ("A", ["ax"]), ("B", ["bx"])]
        assert _walk_progress_section(
            "<h3>H</h3><ul><li>a<ul><li>b").cells == [("H", ["ab"])]

    def test_the_walks_survive_self_closing_tags(self):
        # The counter runs the start half alone for every self-closing tag, so a
        # `<br/>` or `<img/>` inside a cell can neither crash it nor unbalance
        # its depth; the `<div class="profile"/>` case below is the one that
        # would fail if it closed elements the browser keeps open.
        counter = _ProfileCellCounter()
        counter.feed('<div class="profile"><div><h3>a</h3><ul><li>x<br/></li>'
                     '</ul></div><div><h3>b</h3><ul><li><img src="y"/></li>'
                     '</ul></div></div>')
        assert counter.count == 2
        # "Direct" is structural: a non-div wrapper leaves the grid with ONE
        # child, so it must not read as two cells.
        for wrapper in ("span", "a href='#'", "p"):
            wrapped = _ProfileCellCounter()
            wrapped.feed('<div class="profile"><' + wrapper + '><div>a</div>'
                         '<div>b</div></' + wrapper.split()[0] + '></div>')
            assert wrapped.count == 0, wrapper
        # A div wrapper merges the cells into one grid child.
        merged = _ProfileCellCounter()
        merged.feed('<div class="profile"><div><div>a</div><div>b</div></div></div>')
        assert merged.count == 1
        # Two `.profile` blocks stack the columns even though `count` is 2.
        split = _ProfileCellCounter()
        split.feed('<div class="profile"><div>a</div></div>'
                   '<div class="profile"><div>b</div></div>')
        assert (split.profiles, split.count) == (2, 2)
        # HTML ignores the slash: the div stays open, so both children count.
        counter = _ProfileCellCounter()
        counter.feed('<div class="profile"/><div>a</div><div>b</div>')
        assert counter.count == 2
        # An end tag with nothing to match (`</br>` on a void element) is ignored
        # by a browser, so it must not close an ancestor: doing that reddened an
        # honest heading and switched off an enclosing `aria-hidden`.
        assert _walk_progress_section(
            "<h3>H<br></br>2</h3><ul><li>a</li></ul>").cells == [("H2", ["a"])]
        assert not _walk_progress_section(
            "<h3>H<br></br>2</h3><ul><li>a</li></ul>").stray
        assert len(_walk_progress_section(
            '<h3>H</h3><ul><li><div aria-hidden="true">a<br></br>b</div>'
            '</li></ul>').hidden) == 2
        # `<li/>` stays open in HTML, so the text after it is that bullet's.
        assert _walk_progress_section(
            "<h3>H</h3><ul><li/>a<li>b</li></ul>").cells == [("H", ["a", "b"])]

    def test_the_walk_refuses_what_a_text_pin_cannot_see(self):
        """The parser's branches, on synthetic markup rather than the live page.

        Every refusal below exists because a claim escaped the pins in an
        earlier revision of this gate, and the live page exercises none of them.
        Without these cases a branch could regress to "silently allow" and the
        suite would stay green — the failure mode the gate exists to prevent.
        """

        walk = _walk_progress_section

        # Refusals — each is a claim a reader could be shown.
        assert walk('<h3>H</h3><ul><li>a</li></ul><img alt="claim">').attribute_copy
        assert walk('<h3>H</h3><ul><li>a</li></ul><iframe srcdoc="claim">').attribute_copy
        assert walk('<h3>H</h3><ul><li hidden>a</li></ul>').hidden
        assert walk('<h3>H</h3><ul><li style="display:none">a</li></ul>').hidden
        assert walk('<h3>H</h3><ul><li style="color:transparent">a</li></ul>').hidden
        assert walk('<h3>H</h3><ul><li style="opacity:0">a</li></ul>').hidden
        assert walk('<h3>H</h3><ul><li><span aria-hidden="true">a</span></li></ul>').hidden
        assert walk('<h3>H</h3><p>claim</p>').stray == ["claim"]
        assert walk('<p>claim</p><h3>H</h3><ul><li>a</li></ul>').stray == ["claim"]
        assert walk('<h3>H</h3>lead-in<ul><li>a</li></ul>').stray == ["lead-in"]

        # Honest markup — none of these is a claim, so none may refuse.
        assert not walk('<h3>H</h3><ul><li>a</li></ul><svg aria-hidden="true"></svg>').hidden
        assert not walk('<h3>H</h3><ul><li>a</li></ul><img alt="">').attribute_copy
        assert not walk('<h3>H</h3><ul><li style="opacity:0.35">a</li></ul>').hidden
        assert not walk('<h3>H</h3><hr><ul><li>a</li></ul>').stray
        assert not walk('<div><h3 class="x">H</h3></div><ul><li>a</li></ul>').stray

        # Pinning still works through the shapes the page may legitimately take.
        assert walk('<h3>H</h3><ul><li>a</li></ul>').cells == [("H", ["a"])]
        # No space is synthesised: `out` and `in` touch in the markup, so they
        # touch in the pin — a change a reader sees cannot pass.
        assert walk('<h3>H</h3><ul><li>out<ul><li>in</li></ul></li></ul>').cells == [
            ("H", ["outin"])]
        assert walk('<h3>H</h3><ul><li><span>a</span><span>b</span></li></ul>').cells == [
            ("H", ["ab"])]
        assert walk('<h3>H</h3><ul><li><span>a</span> <span>b</span></li></ul>').cells == [
            ("H", ["a b"])]
        # Self-closing tags must not drain the stack: the synthetic end tag has
        # nothing to match on a void element and would drop every ancestor.
        assert walk('<h3>H</h3><ul><li><div aria-hidden="true">'
                    '<img src="x"/>secret</div>a</li></ul>').hidden
        assert walk('<h3>H<br/>x</h3><ul><li>a</li></ul>').cells == [("Hx", ["a"])]
        assert not walk('<h3>H</h3><ul><li>a</li></ul><svg aria-hidden="true"/>').hidden
        assert walk('<h3>H</h3><ul><li>claim</ul>').cells == [("H", ["claim"])]
        assert walk('<h3>H</h3><div><ul><li>a</li></ul></div>').cells == [("H", ["a"])]
        # `</li>` is optional before the next <li>: the browser closes it, so
        # these are two bullets and neither may leak into the next cell.
        assert walk('<h3>H</h3><ul><li>a<li>b</li></ul>').cells == [("H", ["a", "b"])]
        assert walk('<h3>A</h3><ul><li>ax</ul><h3>B</h3><ul><li>bx</li></ul>').cells == [
            ("A", ["ax"]), ("B", ["bx"])]

    def test_the_retired_prospective_beta_line_is_gone(self):
        """`20+ prospective beta testers` must not return anywhere in the section."""
        section_text = _beat_text(_investors_progress_section())
        assert self.RETIRED_LINE not in section_text, (
            f"{self.RETIRED_LINE!r} is back in #progress. 'prospective' "
            "disclaims the noun it modifies; it was replaced by the owner's own "
            "figures (PR #9283)")


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


class TestSiteIconAndSocialPreviewContract:
    """Every page must carry a real icon and a real preview image (PR #11092 era).

    THE DEFECT THIS PINS. Both live origins answered `/favicon.ico` with **404**,
    and the two pages that matter most (`index.html` for premiselabs.co,
    `product.html` for tortoise.premiselabs.co — the host that serves it at `/`)
    declared **no icon at all and no `og:image`**: a browser showed a blank tab,
    and a link pasted into Slack/X/WhatsApp rendered as a bare title with no
    card. Five other pages did declare an icon — `/logo.png`, a 400x244 WORDMARK
    — which a tab renders as a squashed bar, so "there is a `rel=icon`" was
    never the property that mattered.

    So the contract is stated in the three parts a human actually sees, and each
    part is checked against the file on disk rather than against the tag:

    1. **The icon resolves.** `/favicon.ico`, `/favicon.png` and
       `/apple-touch-icon.png` exist in the served tree, the PNG is SQUARE and
       at least 180px (the apple-touch size), and the ICO is a real icon
       container holding an embedded PNG — not a renamed image.
    2. **The preview resolves and is the size it claims.** `og:image` maps to a
       file in the tree, and `og:image:width`/`height` match the JPEG's actual
       SOF header. A card whose declared size disagrees with the bytes is
       cropped by the scraper that trusts the tag.
    3. **No page re-uses the wordmark as an icon.** `/logo.png` is a wordmark;
       it may appear in page content, never in a `rel=icon`.

    The dimensions are read with `struct`/`re` over the SVG-free formats we
    ship (PNG IHDR, JPEG SOF, ICO directory) — stdlib only, no imaging library,
    so the guard runs in the fast lane.

    ⛔ WHAT THIS CAN SEE ABOUT CONTENT, AND WHAT IT STILL CANNOT. Every icon is
    DECODED (`_png_palette`) — `favicon.png`, `apple-touch-icon.png`, and the PNG
    payloads inside `favicon.ico` (the container Windows and Edge actually draw) — and
    must hold more than one colour with no colour above 95% of the canvas, so a flat
    rectangle in place of a mark reds. Two colours is legitimate art and passes; the
    measured placeholder this PR deletes reds at 99.2% one colour. The card is
    a JPEG, which this file cannot decode without dragging a wheel into the fast
    lane, so it is bounded by ENCODED SIZE (>= 40 KB) — a proxy, with its
    measurements written beside it, and it does reject a solid 1200x630 JPEG (22,016
    bytes measured).
    What remains unseen is whether the card DEPICTS the right thing: a 60 KB
    photograph of something unrelated passes. The blog's own head is emitted by
    `website/functions/blog/_lib.ts` and is guarded by `tests/test_blog_seo_meta.mjs`,
    which executes the real renderer.
    """

    WEB = REPO_ROOT / "website"
    ICON_ICO = "favicon.ico"
    ICON_PNG = "favicon.png"
    ICON_APPLE = "apple-touch-icon.png"
    # Declared once: every page's preview image is 1200x630 (the 1.91:1 the
    # scrapers crop to), and the two bespoke pages below ship their own.
    OG_SIZE = (1200, 630)
    # The two hosts this site is served from. Declared once because every page's
    # preview URL is checked against it — see the og:image assertions.
    SITE_ORIGINS = frozenset({"premiselabs.co", "tortoise.premiselabs.co"})
    # The base an icon href is resolved against before it is compared with a file's
    # path. Since epic #11762 there is ONE marketing host: `tortoise.premiselabs.co`
    # is a permanent 301 to it, so a page is never served FROM that host and no
    # document-relative href can mean it. This must be a member of `SITE_ORIGINS`
    # (every shipped icon href is host-relative, so membership is what passes).
    SITE_ROOT = "https://premiselabs.co/"
    # The retired product host. It is KEPT as a permanent 301 — the hostname is
    # never deleted — so `SITE_ORIGINS` above deliberately still holds it: the
    # asset tree (`/assets/*.jpg`, `/skills/*.skill`, the icons) really is served
    # from both, and narrowing the set would red honest asset URLs. What may NOT
    # name it is a PAGE: a canonical, an `og:url`, an `og:image` or a link into a
    # page that moved. That is asserted positively, per page, by
    # `TestProductSetIsServedUnderTheOneHost`, rather than inferred from a
    # membership set that also has to describe assets.
    RETIRED_PRODUCT_HOST = "tortoise.premiselabs.co"
    # The encoded-size floor a link-preview card must clear. MEASURED, not guessed:
    # a FLAT 1200x630 JPEG at maximum quality is 22,016 B (`sips -s formatOptions
    # best`), so the first cut's 20 KB floor passed the very image it was written to
    # reject. The floor is enforced on EVERY page's declared card, and the three cards
    # at this head are 94,066 B (og-tortoise.jpg), 49,555 B (investors-og.jpg) and
    # 50,393 B (design-partners-intro-og.jpg) — i.e. 2.35x, 1.24x and 1.26x the floor,
    # not 2.35x each. Re-encoding the SAME pixels is what eats the margin: with
    # `ffmpeg -q:v 4` the two smaller cards measure 37,024 B and 39,818 B, both under
    # the floor. That is the one false red this bound can produce, which is why
    # `_card_floor_violation`'s message tells the reader to LOOK at the card first.
    # `test_the_content_guards_reject_a_blank_image_and_keep_their_measured_floors`
    # holds this floor ABOVE the flat measurement, so lowering it to 20 KB again is red.
    CARD_MIN_BYTES = 40_000
    # What a flat 1200x630 JPEG measures — the image CARD_MIN_BYTES exists to reject.
    # Recorded as a number so the relationship is ASSERTED (`FLAT_CARD_BYTES <
    # CARD_MIN_BYTES`) rather than left implicit in a comment.
    FLAT_CARD_BYTES = 22_016
    # A decoded icon must hold at least this many distinct colours — TWO, because a
    # hard-edged two-colour mark is legitimate art (pixel art, a `crispEdges` SVG
    # export, anything without antialiasing) and a guard that reds honest artwork is a
    # guard the next person weakens instead of fixes. This was 8, and the comment
    # beside the check claimed "a two-colour logo would still pass at 8" — it could
    # not, 2 < 8, so the sentence asserted the opposite of what the code did. What
    # rejects the degenerate case is the PAIR: one flat colour trips this floor AND
    # the dominant-colour floor below. Neither alone is enough to describe a mark.
    ICON_MIN_COLOURS = 2
    # How much of the canvas the mark must span. The artwork's own padding put it at
    # 68% (#11318 shipped that straight downscale) and in a tab strip it read as a
    # small glyph on a slab; `tools/build_site_icons.py` crops it to 84%. 80% is the
    # floor, not the target: it is the point at which the mark stops reading as small
    # next to a neighbour whose glyph fills its tile, and it sits above 68% so a
    # re-export of the unpadded artwork reds instead of quietly undoing this.
    MARK_WIDTH_FLOOR = 0.80
    # The tile's corner pixels. 0 = the tab background shows through the rounded corner;
    # apple-touch-icon is the exception and must stay opaque (iOS masks it itself).
    ROUNDED_CORNER_ALPHA = 0
    # Transparent corners alone do not make a rounded tile — four punched pixels would
    # pass. This is how far along each edge the first fully-opaque pixel must sit, as a
    # fraction of the side: an arc of radius r reaches an edge at exactly r, so with the
    # builder's 21% this measures 21%, while a nibbled square measures under 1%.
    CORNER_INSET_FLOOR = 0.10
    # ...and no single colour may cover more than this fraction of its canvas.
    ICON_MAX_DOMINANT = 0.95

    # --- format readers -------------------------------------------------

    @staticmethod
    def _png_size(data: bytes) -> tuple[int, int]:
        assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
        return struct.unpack(">II", data[16:24])

    @staticmethod
    def _png_pixels(data: bytes) -> tuple[int, int, list[list[tuple[int, ...]]]]:
        """Decode an 8-bit PNG to (width, height, rows of pixel tuples) — stdlib only.

        ⛔ WHY A DECODER LIVES IN A TEST. `/blog/og-image.png` was a 4.6 KB
        placeholder: two colours, 99.2% of the canvas flat background, a bare accent
        rule, no mark and no text. Every dimension assertion in this file passed on
        it, because a 1200x630 rectangle IS 1200x630. A card (or an icon) that renders
        as an empty block is the failure these guards exist to prevent, and only the
        pixels can tell it apart from art. No imaging library is in the dev env, and
        adding one for a guard would make the fast lane depend on a wheel — so this
        decodes 8-bit PNGs (the only depth this repo writes) directly.

        ⛔ ONE DECODER, TWO CONSUMERS. `_png_palette` counts colours and the rounded-tile
        guard reads GEOMETRY and ALPHA out of the same rows. A second decoder for the
        second question is how the two drift, and this file already carries the cost of
        one reader being right in one place and wrong in another (the source-order meta
        readers). Alpha stays in the tuple, so a transparent pixel is its own colour: a
        decoder that silently dropped the alpha channel reds the colour floor rather
        than passing a blank tile.
        """
        assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
        pos = 8
        width = height = 0
        depth = colour = 0
        idat = b""
        palette: list[tuple[int, ...]] = []
        while pos < len(data):
            (length,) = struct.unpack(">I", data[pos:pos + 4])
            kind = data[pos + 4:pos + 8]
            body = data[pos + 8:pos + 8 + length]
            pos += 12 + length
            if kind == b"IHDR":
                width, height, depth, colour = struct.unpack(">IIBB", body[:10])
                assert body[12] == 0, "interlaced PNG — not the shape this repo writes"
            elif kind == b"PLTE":
                palette = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
            elif kind == b"IDAT":
                idat += body
            elif kind == b"IEND":
                break
        assert depth == 8, f"bit depth {depth} — this decoder reads 8-bit PNGs"
        channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour]

        def paeth(a: int, b: int, c: int) -> int:
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            if pa <= pb and pa <= pc:
                return a
            return b if pb <= pc else c

        stride = width * channels
        # ⛔ BOUNDED, because an unbounded `zlib.decompress` inflates whatever a
        # checked-in IDAT expands to (zlib reaches ~1000:1), so a crafted icon could
        # allocate without limit in CI. The IHDR already states the exact size — one
        # filter byte per row plus `width * channels` — so the bound is known before a
        # byte is inflated, and a stream that overruns it is a malformed PNG rather
        # than something to keep decoding.
        expected = height * (stride + 1)
        stream = zlib.decompressobj()
        raw = stream.decompress(idat, expected + 1)
        assert len(raw) == expected and not stream.unconsumed_tail, (
            f"PNG image data does not expand to the {expected} B its IHDR geometry "
            f"({width}x{height}, {channels} channel(s)) declares — refusing to decode it")
        prev = bytearray(stride)
        rows: list[list[tuple[int, ...]]] = []
        off = 0
        for _ in range(height):
            ftype = raw[off]
            off += 1
            line = bytearray(raw[off:off + stride])
            off += stride
            if ftype:
                for i in range(stride):
                    a = line[i - channels] if i >= channels else 0
                    b = prev[i]
                    c = prev[i - channels] if i >= channels else 0
                    if ftype == 1:
                        line[i] = (line[i] + a) & 0xFF
                    elif ftype == 2:
                        line[i] = (line[i] + b) & 0xFF
                    elif ftype == 3:
                        line[i] = (line[i] + (a + b) // 2) & 0xFF
                    elif ftype == 4:
                        line[i] = (line[i] + paeth(a, b, c)) & 0xFF
                    else:
                        raise AssertionError(f"bad PNG filter {ftype}")
            prev = line
            row: list[tuple[int, ...]] = []
            for x in range(0, stride, channels):
                px = tuple(line[x:x + channels])
                row.append(palette[px[0]] if colour == 3 else px)
            rows.append(row)
        return width, height, rows

    @classmethod
    def _png_palette(cls, data: bytes) -> dict[tuple[int, ...], int]:
        """How many pixels of each colour a PNG really holds."""
        _w, _h, rows = cls._png_pixels(data)
        counts: dict[tuple[int, ...], int] = {}
        for row in rows:
            for px in row:
                counts[px] = counts.get(px, 0) + 1
        return counts

    @staticmethod
    def _flat_png(size: int = 256,
                  rgba: tuple[int, int, int, int] = (0x06, 0x0B, 0x14, 0xFF)) -> bytes:
        """A one-colour 8-bit RGBA PNG — the degenerate icon, built here.

        ⛔ WHY A SYNTHETIC IMAGE IS IN THIS FILE. The pixel floor below is worth
        something only if it REJECTS, and the file it was written for
        (`/blog/og-image.png`, the deleted two-colour placeholder) is gone. This is
        that shape rebuilt in memory, so the guard's FAILURE path runs on every CI
        run instead of surviving as a spot check recorded in a PR body.
        """
        ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
        raw = (b"\x00" + bytes(rgba) * size) * size      # filter byte + one pixel/column

        def chunk(kind: bytes, body: bytes) -> bytes:
            return (struct.pack(">I", len(body)) + kind + body
                    + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

    @staticmethod
    def _two_colour_png(size: int = 256, band: int = 20) -> bytes:
        """A two-colour, hard-edged mark — the artwork the colour floor must NOT red.

        ⛔ WHY THIS FIXTURE EXISTS. The floor beside it started at 8 distinct colours,
        and its own comment claimed "a two-colour logo would still pass at 8" — it
        could not, 2 < 8, so a `crispEdges`/pixel-art mark (no antialiasing, exactly
        the art a designer might choose) reded with "that is a flat rectangle, not a
        mark". Lowering the floor to 2 fixed that, but nothing MEASURED the fix: the
        only icons in the tree decode to ~200+ colours, so pushing the floor back to
        8 left every assertion green. With this fixture it reds.

        20 rows of accent over 256 is 7.8% of the canvas, i.e. dominant 92.2% — under
        `ICON_MAX_DOMINANT` on purpose, so the fixture isolates the COLOUR-COUNT floor
        from the dominant one.
        """
        ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
        accent, background = (0x06, 0xB6, 0xD4, 0xFF), (0x06, 0x0B, 0x14, 0xFF)
        raw = b"".join(
            b"\x00" + (bytes(accent) if y < band else bytes(background)) * size
            for y in range(size))

        def chunk(kind: bytes, body: bytes) -> bytes:
            return (struct.pack(">I", len(body)) + kind + body
                    + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

    @classmethod
    def _icon_mark_violations(cls, name: str, data: bytes, *,
                              in_tree: bool = True) -> list[str]:
        """Every reason a decoded icon is not a mark; empty means it carries art.

        Split out of the guard so the SAME predicate can be run against a blank
        fixture (see `test_the_content_guards_reject_a_blank_image_and_keep_their_
        measured_floors`). A check whose failure path is never executed cannot be
        told apart from a no-op.

        `name` is a BARE name of a file in the served tree: the `website/` prefix is
        added here, so a caller that passes one produces `website/website/…` in the
        only message that matters. A SYNTHETIC fixture is not in the tree, so it
        passes `in_tree=False` — otherwise its message names a path that does not
        exist, and the reader is sent looking for a file that was never there.
        """
        counts = cls._png_palette(data)
        pixels = sum(counts.values())
        where = f"website/{name}" if in_tree else name
        problems: list[str] = []
        if len(counts) < cls.ICON_MIN_COLOURS:
            problems.append(
                f"{where} holds {len(counts)} distinct colour(s) over "
                f"{pixels} pixels — that is a flat rectangle, not a mark; every size "
                "assertion would pass on it")
        dominant = max(counts.values()) / pixels
        if dominant >= cls.ICON_MAX_DOMINANT:
            problems.append(
                f"{where} is {dominant:.1%} a single colour — a mark needs to "
                "carry something the background does not")
        return problems

    @classmethod
    def _og_asset_path(cls, image: str) -> Path:
        """The file a declared `og:image` URL resolves to in the served tree.

        The QUERY is stripped before the URL becomes a path: `…/og-tortoise.jpg?v=2`
        is a legitimate cache-bust that changes no file, and a guard that keeps it
        reports "not in the served tree" about a page that is correct. The ORIGIN is
        deliberately dropped rather than matched here — the same tree is served from
        two hosts, and that is a separate assertion (`SITE_ORIGINS`).
        """
        return cls.WEB / urlsplit(image).path.lstrip("/")

    @classmethod
    def _og_card_bytes(cls, image: str) -> tuple[Path | None, bytes]:
        """The file a declared card URL resolves to, and its bytes; `(None, b"")` if absent.

        The default `load` for `_card_violations`: existence and the bytes come from the
        served tree, through `_og_asset_path` (so a `?v=2` cache-bust still resolves).
        """
        path = cls._og_asset_path(image)
        return (path, path.read_bytes()) if path.is_file() else (None, b"")

    @staticmethod
    def _filtered_png() -> tuple[bytes, dict[tuple[int, ...], int], list[int]]:
        """A 5-row RGB PNG whose rows use filter types 0–4, and the histogram it encodes.

        ⛔ WHY THIS FIXTURE EXISTS. Both fixtures around it emit filter type 0
        (`b"\x00" + row`), so Sub/Up/Average/Paeth reconstruction — the branch every
        REAL icon in this tree takes (measured: favicon.png rows use filters 1,2,3,4) —
        was executed by no test. The failure direction is why that matters: a wrong
        predictor invents extra colours AND lowers the dominant share, i.e. it satisfies
        both content floors, so a broken decoder would read noise as art while every
        assertion stayed green.

        The rows are a gradient, not a flat fill, because a flat row makes Sub, Up,
        Average and Paeth degenerate to the same output and the fixture would prove
        nothing about any of them.

        Returns `(png, expected histogram, filter types the stream uses)` — the filter
        list is returned so a future edit cannot quietly collapse it back to type 0 and
        leave this test passing on the untested path again.
        """
        width, height = 8, 5
        pixels = [
            [((x * 37 + y * 13) & 0xFF, (x * 53 + y * 29) & 0xFF, (x * 71 + y * 17) & 0xFF)
             for x in range(width)]
            for y in range(height)
        ]

        def paeth(a: int, b: int, c: int) -> int:
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            if pa <= pb and pa <= pc:
                return a
            return b if pb <= pc else c

        body = bytearray()
        prev = [0] * (width * 3)
        filters: list[int] = []
        for y, row in enumerate(pixels):
            flat = [c for px in row for c in px]
            ftype = y % 5
            filters.append(ftype)
            encoded = bytearray()
            for i, value in enumerate(flat):
                a = flat[i - 3] if i >= 3 else 0
                b = prev[i]
                c = prev[i - 3] if i >= 3 else 0
                predictor = (0, a, b, (a + b) // 2, paeth(a, b, c))[ftype]
                encoded.append((value - predictor) & 0xFF)
            body += bytes([ftype]) + bytes(encoded)
            prev = flat

        ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB

        def chunk(kind: bytes, payload: bytes) -> bytes:
            return (struct.pack(">I", len(payload)) + kind + payload
                    + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

        expected: dict[tuple[int, ...], int] = {}
        for row in pixels:
            for px in row:
                expected[px] = expected.get(px, 0) + 1
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", zlib.compress(bytes(body))) + chunk(b"IEND", b""),
                expected, filters)

    @staticmethod
    def _jpeg_stream_length(data: bytes) -> int:
        """The bytes up to the EOI that ENDS THE SCAN — not the last `FFD9` in the file.

        ⛔ A PADDED FILE IS NOT A CARD, and file length cannot tell: a flat 1200x630
        JPEG is 4,723 B, and the SAME image padded with ~36 KB of trailing zeros cleared
        a 40 KB floor with every other assertion — dimensions, type, magic bytes — still
        green.

        ⛔ AND `rfind(b"\\xff\\xd9")` CANNOT TELL EITHER. Byte-stuffing makes FFD9
        unambiguous only INSIDE an entropy-coded scan (`FF` is always followed by `00` or
        a restart marker there); bytes appended AFTER the image are inside no stream, so
        padding closed with a two-byte trailer measured as if it had been painted on —
        measured: 4,723 B + zeros + `FF D9` = 41,500 B and the floor cleared. So the walk
        follows the segment chain from SOI to SOS and then the scan itself, and stops at
        the FIRST EOI it meets. A real card's stream ends there; anything after it is
        padding and does not count.

        Residual, accepted and named: an in-band metadata block (an EXIF thumbnail, a
        large ICC packet) sits BEFORE the EOI and still counts toward the floor. That is
        the residual the floor has always had — it bounds encoded bulk, never whether the
        card DEPICTS the right thing — and closing it would mean decoding the JPEG, which
        this fast-lane guard deliberately does not do.
        """
        n = len(data)
        i = 2
        while i + 4 <= n:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker == 0xFF:
                i += 1
                continue
            if marker == 0xD9:  # EOI with no scan (header-only or empty image)
                return i + 2
            if marker == 0xDA:  # SOS: entropy-coded data follows
                j = i + 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
                while j + 1 < n:
                    if data[j] == 0xFF:
                        nxt = data[j + 1]
                        if nxt == 0xD9:
                            return j + 2
                        if nxt == 0x00 or 0xD0 <= nxt <= 0xD7:  # stuffing / restart
                            j += 2
                            continue
                    j += 1
                return n
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
        return n

    @staticmethod
    def _png_stream_length(data: bytes) -> int:
        """The bytes up to the end of the IEND chunk — a PNG's image IS its chunks.

        The JPEG walk above must never be applied to a PNG: `FF D9` inside IDAT is
        ordinary compressed data, so a card would be measured by a random interior byte
        pair. Both directions were measured on real bytes: a flat PNG padded past 40 KB
        measured 41,000 under a JPEG-only measure (a blank card clearing the floor), and
        `website/logo.png` — 35,670 B, whose IDAT does contain `FF D9` — measured 29,390
        under the superseded `rfind` measure, i.e. 6,280 B short of a legitimate card.
        The padded-PNG fixture in `test_the_content_guards_reject_a_blank_image_and_keep_
        their_measured_floors` is what catches a dispatch regression; the `logo.png` pin
        beside it asserts the PNG path still measures a real PNG at its full length.
        """
        n = len(data)
        pos = 8
        while pos + 8 <= n:
            (length,) = struct.unpack(">I", data[pos:pos + 4])
            kind = data[pos + 4:pos + 8]
            if kind == b"IEND":
                return min(pos + 12 + length, n)
            if length > n:  # malformed length: do not walk off the end
                return n
            pos += 12 + length
        return n

    @classmethod
    def _encoded_length(cls, data: bytes) -> int:
        """How many bytes of this card are actually IMAGE, dispatched on its magic.

        A format with no reader here is measured as its file length — the only honest
        statement the guard can make without parsing it, and the floor is a bulk check,
        not a format check (`_card_size` owns the declared-type dispatch).
        """
        if data[:2] == b"\xff\xd8":
            return cls._jpeg_stream_length(data)
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return cls._png_stream_length(data)
        return len(data)

    @classmethod
    def _card_floor_violation(cls, page: str, path: Path, data: bytes) -> str | None:
        """Why an encoded card is too small to BE a card — None when it clears."""
        encoded = cls._encoded_length(data)
        if encoded >= cls.CARD_MIN_BYTES:
            return None
        return (f"{page}'s card {path.name} holds only {encoded} bytes of encoded image "
                f"({len(data)} B on disk), under the "
                f"{cls.CARD_MIN_BYTES // 1000} KB floor — a flat 1200x630 card of one "
                f"colour measures {cls.FLAT_CARD_BYTES // 1000} KB, so a card this "
                "small is usually near-uniform and a link preview of it is an empty "
                "block. If this card is REAL, open it: a legitimate re-encode can land "
                "here (the smallest real card is 49,555 B; re-saving it with "
                "`ffmpeg -q:v 4` measures 37,024 B). Then lower CARD_MIN_BYTES in "
                "tests/test_website_static.py and record what you measured.")

    @classmethod
    def _card_size(cls, declared_type: str, raw: bytes) -> tuple[int, int]:
        """The card's real pixel size, read with the reader the page's TYPE names.

        Split out of the guard so BOTH dispatch branches run against real bytes (see
        `test_the_card_type_dispatch_reads_the_format_the_page_names`): every page in
        this tree declares `og:image:type: image/jpeg`, and the only PNG card the repo
        ever had is deleted by this PR, so without that fixture the `image/png` branch
        is executed by nothing and a JPEG-only dispatch reads green.

        The declared type is the page's own statement about its card, so it is taken at
        its word and the BYTES are held to it — a page that legitimately ships a PNG
        card is read by the PNG reader instead of being reported as "not a JPEG", and
        a page that lies about the type still reds.
        """
        readers = {"image/jpeg": cls._jpeg_size, "image/png": cls._png_size}
        assert declared_type in readers, (
            f"og:image:type {declared_type!r} has no reader here — this guard reads "
            f"{sorted(readers)}; add a reader rather than dropping the assertion")
        return readers[declared_type](raw)

    @classmethod
    def _card_violations(cls, page_name: str, head: str, load=None) -> list[str]:
        """Every way this page's link-preview card fails the contract; empty means it passes.

        ⛔ SPLIT OUT SO THE PAGE-LEVEL APPLICATION IS DRIVEN BY A TEST, not only by this
        tree's own pages — the same reason `_icon_trio_violations` is. MEASURED on the
        code before this split, all 13 tests in this class GREEN under each of three
        mutants: the encoded-size floor application deleted, `_card_size` reverted to
        `_jpeg_size` (the JPEG-only dispatch this PR replaced), and the image resolved
        with the raw `image.split("//", 1)[-1]` form (the `?v=2` false red this PR
        fixed). The three behaviours this PR exists to add on the card path were pinned
        only at the helpers they call, never at the loop that calls them — so the loop's
        half could be `assert True` and nothing noticed.

        `load` is the test seam: it maps an `og:image` URL to `(path | None, bytes)`. The
        default reads the served tree (which is what the page loop uses), so the
        query-stripping path is exercised by the real pages rather than bypassed.

        Every check is collected rather than asserted, so a card that is BOTH the wrong
        format and too small reports both — a guard that stops at the first defect is how
        the second one stays hidden.
        """
        load = load or cls._og_card_bytes
        image = cls._og(head, "image")
        if not image:
            return [f"{page_name} declares no og:image — a shared link renders no card"]
        path, raw = load(image)
        if path is None:
            return [f"{page_name} points og:image at {image}, which is not in the served "
                    f"tree (looked for {cls._og_asset_path(image)}) — the scraper fetches "
                    "a 404 and shows no card"]

        violations: list[str] = []
        declared_type = cls._og(head, "image:type")
        actual: tuple[int, int] | None = None
        if not declared_type:
            violations.append(
                f"{page_name} declares no og:image:type — a scraper that has to sniff the "
                "format is a scraper that can guess wrong")
        else:
            try:
                actual = cls._card_size(declared_type, raw)
            except AssertionError as exc:
                violations.append(
                    f"{page_name} declares og:image:type {declared_type!r} but "
                    f"{path.name}'s bytes are not one: {exc}")

        declared = (cls._og(head, "image:width"), cls._og(head, "image:height"))
        if not all(value and value.strip().isdigit() for value in declared):
            violations.append(
                f"{page_name} declares og:image:width/height {declared[0]!r}x{declared[1]!r}, "
                "which is not a pixel count — a scraper then reserves a box nobody promised")
        elif actual is not None and (int(declared[0]), int(declared[1])) != actual:
            violations.append(
                f"{page_name} declares {int(declared[0])}x{int(declared[1])} but "
                f"{path.name} is {actual[0]}x{actual[1]}")

        floor = cls._card_floor_violation(page_name, path, raw)
        if floor:
            violations.append(floor)
        return violations

    @staticmethod
    def _jpeg_size(data: bytes) -> tuple[int, int]:
        assert data[:2] == b"\xff\xd8", "not a JPEG"
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):  # SOF0/SOF1/SOF2 carry the frame size
                height, width = struct.unpack(">HH", data[i + 5:i + 9])
                return width, height
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            length = struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + length
        raise AssertionError("no SOF marker in JPEG")

    @classmethod
    def _ico_entries(cls, data: bytes) -> list[dict]:
        """One dict per icon: the DIRECTORY's claim and what its PAYLOAD really is.

        The directory and the payload are two independent statements about the
        same image, and only the payload is what a renderer draws. A file whose
        second entry declares 48x48 while pointing at the 32x32 payload renders
        blank in Edge and fine in Chrome — so both are read here and compared.
        """
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        assert (reserved, kind) == (0, 1), "not an icon container"
        assert count >= 1, "an .ico with no entries"
        out = []
        for n in range(count):
            base = 6 + 16 * n
            w, h, _cols, _res, planes, bpp, length, offset = struct.unpack(
                "<BBBBHHII", data[base:base + 16])
            assert offset + length <= len(data), (
                f"favicon.ico entry {n} runs past the end of the file "
                f"(offset {offset} + length {length} > {len(data)} bytes)")
            assert length > 0, f"favicon.ico entry {n} declares an empty payload"
            payload = data[offset:offset + length]
            is_png = payload[:8] == b"\x89PNG\r\n\x1a\n"
            out.append({
                "declared": (w or 256, h or 256),
                "planes": planes,
                "bpp": bpp,
                "is_png": is_png,
                "actual": cls._png_size(payload) if is_png else None,
                # Kept so the CONTENT check can decode what the directory merely
                # describes: every assertion above reads the container's claims and
                # the payload's geometry, and a solid-colour payload satisfies both.
                "payload": payload,
            })
        return out

    # --- helpers --------------------------------------------------------

    # The population every assertion from here down iterates. Pinned, because a
    # MOVE narrows it silently: `glob("*.html")` is non-recursive, so filing six
    # pages into a subdirectory left these guards covering 8 of 14 pages and green
    # (epic #11762 rejected that shape for exactly this reason). Adding or removing
    # a page is a deliberate act; a page that leaves coverage by moving is not.
    PAGES_AT_11762 = (
        "404.html", "contact.html", "design-partners-intro.html", "docs.html",
        "dpa.html", "faq.html", "index.html", "investors.html", "license.html",
        "privacy.html", "product.html", "security.html", "self-hosted.html",
        "tos.html",
    )

    def _pages(self):
        pages = sorted(self.WEB.glob("*.html"))
        assert pages, "no pages found — the guard would pass vacuously"
        return pages

    def test_the_guard_population_is_pinned(self):
        """The population the icon/card guards iterate must not shrink by accident."""
        assert tuple(p.name for p in self._pages()) == self.PAGES_AT_11762, (
            "the marketing-page population changed. That is legitimate only when a "
            "page is deliberately added or retired — a page that MOVED out of "
            "`website/*.html` silently leaves every guard below and stays green")

    @classmethod
    def _head(cls, html: str) -> str:
        """The head's MARKUP: comments and inline `<style>`/`<script>` bodies removed.

        ⛔ This is not tidying — it is the fix for a fail-open the first cut had. The
        value readers below are regexes over this text, and an HTML comment is
        invisible to a browser: wrapping `og:image` in `<!-- -->` shipped a page with
        NO preview image at all while all eight assertions passed, because the regex
        happily matched the commented-out tag. A guard that reads what a browser
        ignores reports green on a page a scraper sees as empty. Stripping first makes
        the value readers and `test_the_head_is_well_formed_markup` answer about the
        SAME text, which is the text a scraper parses.

        Comments are also where this repo QUOTES tags (the noindex rationale on
        `design-partners-intro.html` quotes a `<meta name="robots">`), and
        `404.html` carries its whole stylesheet in the head — counting either as
        markup is the opposite error.
        """
        start = html.index("<head")
        return cls._head_markup(html[start:html.index("</head>", start)])

    # The readers below go through the PARSER, not a regex, and that is not
    # fastidiousness: `<meta content="…" property="og:image">` is identical to a
    # browser, to every scraper and to this file's own structural test, but a
    # source-order regex reports "declares no og:image — a shared link renders no
    # card" about a page that is correct. A guard that cries wolf on a legitimate
    # edit teaches the next person to weaken it, which is how a guard dies.
    @classmethod
    def _meta_content(cls, head: str, **attrs: str) -> str | None:
        metas, _ = cls._head_metas(head)
        for meta in metas:
            if all(meta.get(k) == v for k, v in attrs.items()):
                return meta.get("content")
        return None

    @classmethod
    def _og(cls, head: str, prop: str) -> str | None:
        return cls._meta_content(head, property=f"og:{prop}")

    @classmethod
    def _twitter(cls, head: str, name: str) -> str | None:
        return cls._meta_content(head, name=f"twitter:{name}")

    @classmethod
    def _links(cls, head: str) -> list[dict[str, str]]:
        """Every `<link>` element in the head, as its attribute dict."""

        class Collector(HTMLParser):
            def __init__(self) -> None:
                super().__init__(convert_charrefs=True)
                self.links: list[dict[str, str]] = []
                self.in_head = False

            def handle_starttag(self, tag: str, attrs) -> None:
                if tag == "head":
                    self.in_head = True
                elif tag == "link" and self.in_head:
                    self.links.append({k: (v or "") for k, v in attrs})

            def handle_endtag(self, tag: str) -> None:
                if tag == "head":
                    self.in_head = False

        collector = Collector()
        collector.feed(head)
        collector.close()
        return collector.links

    # --- 1. the icon resolves ------------------------------------------

    def test_every_page_declares_the_icon_trio(self):
        """Two `rel=icon` rows (ICO + PNG) plus the apple-touch icon, every page.

        The `rel` is asserted, not just the `href`: `<link rel="preload"
        href="/favicon.ico">` mentions the file and gives the tab no icon at all, and
        an href-only check cannot tell the two apart.

        The `sizes` attribute is held to the BYTES rather than to a literal, because a
        declared size is a promise a renderer reserves space for: a 512px re-export of
        a 256px file is square, is >= 180, passes every size assertion — and makes all
        14 pages lie about it. So the tag is checked against the IHDR (PNG) and against
        the directory (ICO), which is what the tab will actually draw.
        """
        png_w, png_h = self._png_size((self.WEB / self.ICON_PNG).read_bytes())
        apple_w, _apple_h = self._png_size((self.WEB / self.ICON_APPLE).read_bytes())
        ico_sizes = {
            i["declared"][0]
            for i in self._ico_entries((self.WEB / self.ICON_ICO).read_bytes())
        }

        png = (png_w, png_h)
        apple = (apple_w, _apple_h)
        for page in self._pages():
            violations = self._icon_trio_violations(
                page.name, page.read_text(), png, ico_sizes, apple)
            assert not violations, "\n".join(violations)

    def _icon_trio_violations(self, page_name: str, head: str,
                              png: tuple[int, int], ico_sizes: set[int],
                              apple: tuple[int, int], *,
                              origins: frozenset[str] | None = None) -> list[str]:
        """Every way this page's `<head>` fails the icon contract; empty means it passes.

        ⛔ SPLIT OUT SO THE PAGE-LEVEL APPLICATION IS DRIVEN BY A TEST, not only by this
        tree's own pages. The commit that added these assertions proved the off-host and
        wrong-`sizes` paths with a hand-run probe against `website/404.html` and never with
        a suite assertion — so replacing either one with `assert True` left 87 tests green,
        and a real page regression would have shipped green. That is precisely the class of
        defect this guard exists to end, one level ABOVE the helpers it calls.
        """
        links = self._links(self._head(head))
        # `rel` is split CASE-INSENSITIVELY: HTML lowercases attribute NAMES, never values,
        # so `rel="ICON"` is the same relation — comparing the raw value reded a correct
        # page with a message that blamed it for having no icon at all.
        violations = [
            f"{page_name} does not declare {href} with the rel that makes it an icon — a "
            "page with no icon shows a blank tab and, on a share, no avatar; iOS draws a "
            "screenshot instead of the mark"
            for href in self._missing_icon_links(links, origins=origins)]
        # ⛔ EVERY link is audited, and the apple-touch branch is dispatched BY NAME:
        # `"apple-touch-icon".split()` is `["apple-touch-icon"]`, so a `"icon" in rel`
        # filter reaches neither of its branches and the apple link's `sizes` went
        # unchecked while this guard reported the page correct.
        violations += [
            v for v in (self._declared_sizes_violation(
                page_name, link, png, ico_sizes, apple, origins=origins)
                for link in links) if v]
        return violations

    @classmethod
    def _missing_icon_links(cls, links: list[dict], *,
                            origins: frozenset[str] | None = None) -> list[str]:
        """The icon files this `<link>` set fails to declare with the rel that makes it one.

        Split out of the trio guard so the SAME predicate can be run against a synthetic
        link set (`test_the_declared_sizes_guard_refuses_a_wrong_apple_slot`): every page
        in this tree spells `rel` in lower case, so the case-INSENSITIVE read is
        unobservable on the real population — a regression to `.split()` alone leaves
        every page-level assertion green while `rel="ICON"` (valid HTML) reds a correct
        page. A check whose failure path never runs cannot be told apart from a no-op.
        """
        declared = {(cls._icon_ref(ln.get("href", ""), origins=origins), token)
                    for ln in links
                    for token in ln.get("rel", "").lower().split()}
        missing: list[str] = []
        for href in (f"/{cls.ICON_ICO}", f"/{cls.ICON_PNG}"):
            if (href, "icon") not in declared:
                missing.append(href)
        if (f"/{cls.ICON_APPLE}", "apple-touch-icon") not in declared:
            missing.append(f"/{cls.ICON_APPLE}")
        return missing


    @classmethod
    def _icon_ref(cls, href: str, *,
                  origins: frozenset[str] | None = None,
                  base: str | None = None) -> str:
        """The PATH an icon href names when it is THIS site's; "" when it is not.

        The query is not part of what is served: `?v=2` on a favicon href is standard
        cache-busting, changes no file and is honoured by every browser, so comparing
        raw hrefs reports "declares no rel=icon" about a page that is correct — the
        same false red `_og_asset_path` avoids for `og:image`.

        ⛔ AND THE ORIGIN IS CHECKED, NOT DROPPED. `urlsplit("//evil.example/
        favicon.ico").path` is `/favicon.ico` — the netloc is discarded — so a
        protocol-relative href "declared" the icon by path while the browser fetched
        it from ANOTHER HOST, and the guard reported green on a tab that has no icon
        (or someone else's). Only an href that resolves to one of `SITE_ORIGINS`
        yields a path; anything else yields "" and matches no icon assertion.

        `origins` is the SURFACE's own origin set. It exists because the guard now grades
        the app tree too, and `app.premiselabs.co` is a real origin of ours that
        `SITE_ORIGINS` deliberately does not carry (that set is what the marketing
        `og:image` guard holds a card to — widening IT would loosen a different check).
        Default `None` keeps every existing caller on `SITE_ORIGINS`.

        ⛔ `base` IS THE PAGE'S OWN URL, NOT THE SITE ROOT (review round 3). A href is
        resolved by the BROWSER against the page that carries it, so a page in a
        subdirectory declaring `href="favicon.ico?v=…"` requests `<dir>/favicon.ico`. Every
        href was resolved against `SITE_ROOT`, so such a page read GREEN for a file that does
        not exist — measured on a new `apps/dashboard/public/settings/index.html` mutant,
        reachable only because the app surface now `rglob`s for nested pages.
        """
        url = urlsplit(urljoin(base or cls.SITE_ROOT, href))
        if url.hostname not in (origins if origins is not None else cls.SITE_ORIGINS):
            return ""
        return url.path

    @staticmethod
    def _parse_declared_sizes(token: str) -> set[int] | None:
        """The square, single-slot sizes a `sizes="32x32 48x48"` token promises.

        `None` means "this token is not that list" — the CALLER turns it into a
        violation, quoting the token. ⛔ IT MUST NOT RAISE. Its only caller is
        `_declared_sizes_violation`, which is a VIOLATION COLLECTOR: `_icon_trio_
        violations` documents itself as returning *every* way a page fails the contract,
        and a raise here instead escapes the collector with ONE raw AssertionError,
        losing every other violation on the page. Measured on the code that raised: a
        spec-valid head whose `rel="icon"` links omit the OPTIONAL `sizes=` raised, and
        a head that ALSO omitted two of the three icon links reported only the sizes
        assertion — the two missing-link violations were never returned.
        """
        pairs = re.findall(r"(\d+)x(\d+)", token)
        if not pairs or not all(a == b for a, b in pairs):
            return None
        return {int(a) for a, _ in pairs}

    @classmethod
    def _declared_sizes_violation(cls, page_name: str, link: dict,
                                  png: tuple[int, int], ico_sizes: set[int],
                                  apple: tuple[int, int], *,
                                  origins: frozenset[str] | None = None) -> str | None:
        """Why a `<link>`'s declared `sizes=` disagrees with the file's bytes; None = agrees.

        ⛔ SPLIT OUT OF THE TRIO GUARD for the reason `_icon_mark_violations` is: the
        apple-touch branch fires only when a page declares `sizes=` on that link, and
        NO page does — measured at this head, 24 surfaced documents carry 24 apple links
        and 0 of them declare one. Inline it was therefore unreachable, and a check whose
        failure path never runs cannot be told apart from a no-op.
        `test_the_declared_sizes_guard_refuses_a_wrong_apple_slot` now feeds it the
        link the branch is written for, in both directions.

        `sizes` is OPTIONAL on the apple link — the href alone is what iOS needs — so
        absent is not a defect. When it IS declared it is a promise like the others
        (MDN's own example is `rel="apple-touch-icon" sizes="114x114"`), and this
        guard knows the file's real geometry, so a wrong one must not pass silently.

        ⛔ ON THE `rel="icon"` LINKS IT IS REQUIRED, AND THAT IS A HOUSE RULE, not the
        spec. The spec makes `sizes` a hint that is optional everywhere; this site pins
        both tab-icon slots so a renderer reserves the right box instead of guessing, so
        a page that omits one is reported. Stated here because the alternative is that
        the requirement looks like an accident of the parser. Every omission and every
        token this guard cannot read is RETURNED as a violation — never raised — so one
        defect can never hide the rest.
        """
        rels = link.get("rel", "").lower().split()
        path = cls._icon_ref(link.get("href", ""), origins=origins)
        declared = link.get("sizes")
        if "icon" in rels and path == f"/{cls.ICON_PNG}":
            return cls._sizes_violation(page_name, declared, {png[0]}, cls.ICON_PNG,
                                       f"IHDR is {png[0]}x{png[1]}", "box")
        if "icon" in rels and path == f"/{cls.ICON_ICO}":
            return cls._sizes_violation(page_name, declared, ico_sizes, cls.ICON_ICO,
                                       f"directory holds {sorted(ico_sizes)}", "container")
        if ("apple-touch-icon" in rels and path == f"/{cls.ICON_APPLE}"
                and declared):
            return cls._sizes_violation(page_name, declared, {apple[0]}, cls.ICON_APPLE,
                                       f"IHDR is {apple[0]}x{apple[1]}", "box")
        return None

    @classmethod
    def _sizes_violation(cls, page_name: str, declared: str | None, expected: set[int],
                         target: str, truth: str, unit: str) -> str | None:
        """Why a `<link>`'s declared `sizes=` disagrees with what the bytes hold.

        Returns a violation string — never raises — because every branch below is a way
        a page can fail a contract, not a programming error. `truth` states what the file
        actually holds (`"IHDR is 256x256"` / `"directory holds [32, 48]"`) and `unit`
        names the thing that ends up wrong (`box` for a PNG, `container` for the ICO).
        """
        if not declared:
            return (f"{page_name} declares no sizes= for {target} — this site pins that "
                    f"slot ({truth}) so a renderer reserves the right {unit} instead of "
                    "guessing. `sizes` is OPTIONAL in the spec, so this is a house rule "
                    "(see `_declared_sizes_violation`); declare it, or change the rule "
                    "deliberately")
        parsed = cls._parse_declared_sizes(declared)
        if parsed is None:
            return (f"{page_name} declares sizes={declared!r} for {target}, which is not "
                    "a square '<w>x<h>' slot list — a tab icon is square, and a token "
                    "this guard cannot read is a promise it cannot check")
        if parsed == expected:
            return None
        return (f"{page_name} declares sizes={declared!r} for {target}, whose {truth} — "
                f"the tag and the {unit} must agree, or the tab reserves the wrong {unit}")

    def test_the_declared_sizes_guard_refuses_a_wrong_apple_slot(self):
        """NON-VACUITY: the branches no page in this tree drives must still be able to RED.

        ⛔ WHY. The apple-touch `sizes` branch fires only when a page declares `sizes=` on
        that link, and none does (24 apple links, 0 declaring one, measured at this
        head), so inline it was dead code. The trio's `icon`/rel membership is the same
        class of hole from the other side: every page here spells `rel` in lower case,
        so the case-insensitive read is unobservable on the real population.

        Each edge is pinned in BOTH directions — the wrong slot reds, the right one does
        not — so none of these assertions is either dead or a constant.
        """
        png = self._png_size((self.WEB / self.ICON_PNG).read_bytes())
        apple = self._png_size((self.WEB / self.ICON_APPLE).read_bytes())
        ico_sizes = {i["declared"][0]
                     for i in self._ico_entries((self.WEB / self.ICON_ICO).read_bytes())}
        apple_link = {"rel": "apple-touch-icon", "href": f"/{self.ICON_APPLE}"}

        wrong = dict(apple_link, sizes="114x114")
        assert apple[0] != 114, (
            "the fixture's wrong size (114) is the file's real width, so it isolates "
            "nothing")
        assert self._declared_sizes_violation("page.html", wrong, png, ico_sizes, apple), (
            f"declaring sizes=114x114 for a {apple[0]}x{apple[1]} apple-touch icon was not "
            "reded — the branch that exists to catch it is dead")

        right = dict(apple_link, sizes=f"{apple[0]}x{apple[1]}")
        assert self._declared_sizes_violation(
            "page.html", right, png, ico_sizes, apple) is None, (
            "the correct apple-touch size was reded, so the branch is not a check")

        assert self._declared_sizes_violation(
            "page.html", apple_link, png, ico_sizes, apple) is None, (
            "omitting the OPTIONAL sizes= on the apple link was reded — the href alone "
            "is what iOS needs")

        # The ICO branch: the directory holds [32, 48], so a single-slot declaration
        # must red. Killing this branch outright left the suite green, which is the
        # only reason it needs an edge of its own here.
        ico_link = {"rel": "icon", "href": f"/{self.ICON_ICO}",
                    "sizes": " ".join(f"{n}x{n}" for n in sorted(ico_sizes))}
        assert self._declared_sizes_violation(
            "page.html", ico_link, png, ico_sizes, apple) is None, (
            "the ICO's own directory sizes were reded")
        ico_wrong = dict(ico_link, sizes=f"{sorted(ico_sizes)[0]}x{sorted(ico_sizes)[0]}")
        assert self._declared_sizes_violation(
            "page.html", ico_wrong, png, ico_sizes, apple), (
            f"declaring sizes={ico_wrong['sizes']!r} for an ICO whose directory holds "
            f"{sorted(ico_sizes)} was not reded — the ICO branch is dead")

        upper = {"rel": "ICON", "href": f"/{self.ICON_PNG}", "sizes": "1x1"}
        assert self._declared_sizes_violation(
            "page.html", upper, png, ico_sizes, apple), (
            'rel="ICON" with a wrong sizes= was not reded — a case-sensitive read '
            "skips the link entirely, which is how a correct page reds and a wrong "
            "one passes)")

        off_host = {"rel": "icon", "href": "//evil.example/favicon.ico",
                    "sizes": f"{png[0]}x{png[1]}"}
        assert self._icon_ref(off_host["href"]) == "", (
            "a protocol-relative href was resolved to this site's path — the netloc was "
            "dropped, so an off-host icon reads as declared")

        # ...and the trio's own membership predicate, driven with a synthetic link set:
        # the real pages all spell `rel` in lower case, so this is the only place the
        # case-insensitive read is observable.
        complete = [{"rel": "icon", "href": f"/{self.ICON_ICO}"},
                    {"rel": "icon", "href": f"/{self.ICON_PNG}"},
                    dict(apple_link)]
        assert self._missing_icon_links(complete) == [], (
            "a complete, correctly-spelled icon trio was reported as missing links")
        assert self._missing_icon_links(
            [dict(ln, rel=ln["rel"].upper()) for ln in complete]) == [], (
            "an upper-case rel was reported as missing — HTML lowercases attribute "
            "NAMES, never values, so rel=\"ICON\" is the same relation")
        assert self._missing_icon_links(complete[:2]) == [f"/{self.ICON_APPLE}"], (
            "dropping the apple-touch link was not reported as a missing icon")
        assert self._missing_icon_links([complete[0], complete[2]]) == [f"/{self.ICON_PNG}"], (
            "dropping the PNG rel=icon was not reported as a missing icon")
        # ⛔ THE FILE KEY OF THE ICO AND APPLE BRANCHES. On this tree the PNG conjunct
        # shadows them (every page's PNG link is graded first and the ICO/apple hrefs are
        # the only other ones), so dropping either lets a SECOND, correctly-declared
        # variant (`/favicon-32.png`, `/apple-touch-icon-120x120.png`) be graded against
        # the default file and red — a false red nothing in the suite noticed.
        assert self._declared_sizes_violation(
            "page.html", {"rel": "icon", "href": "/favicon-32.png", "sizes": "32x32"},
            png, ico_sizes, apple) is None, (
            "a second, correctly-declared PNG icon was graded against the ICO")
        assert self._declared_sizes_violation(
            "page.html", {"rel": "apple-touch-icon",
                          "href": "/apple-touch-icon-120x120.png", "sizes": "120x120"},
            png, ico_sizes, apple) is None, (
            "a correctly-declared apple-touch variant was graded against apple-touch-icon.png")
        # ...and the PAGE-LEVEL application itself, driven by a synthetic head. Without
        # this, either assertion in the loop could be `assert True` and the suite stayed
        # green — measured by the round-6 reviewer against a genuinely broken 404.html.
        broken = ("<head>"
                  '<link rel="icon" href="/favicon.ico" sizes="32x32">'
                  f'<link rel="icon" href="/favicon.png" sizes="{png[0]}x{png[1]}">'
                  "</head>")  # no apple-touch link, and the wrong ICO slot
        trio = self._icon_trio_violations("synthetic.html", broken, png, ico_sizes, apple)
        assert any(self.ICON_APPLE in v for v in trio), (
            f"a head with no apple-touch link produced no page-level violation: {trio}")
        assert any(self.ICON_ICO in v and "sizes" in v for v in trio), (
            f"a head declaring the wrong ICO slot produced no page-level violation: {trio}")
        good = ("<head>"
                f'<link rel="icon" href="/favicon.ico" '
                f'sizes="{" ".join(f"{n}x{n}" for n in sorted(ico_sizes))}">'
                f'<link rel="icon" href="/favicon.png" sizes="{png[0]}x{png[1]}">'
                f'<link rel="apple-touch-icon" href="/{self.ICON_APPLE}" '
                f'sizes="{apple[0]}x{apple[1]}">'
                "</head>")
        assert self._icon_trio_violations(
            "synthetic.html", good, png, ico_sizes, apple) == [], (
            "a complete, correctly-declared head was reported as violating the contract")
        # ⛔ THE REL TOKEN, NOT JUST THE HREF. `<link rel="preload" href="/favicon.ico">`
        # mentions the file and gives the tab no icon at all — an href-only reduction of
        # this predicate reports all three as declared, which is the exact earlier cut's
        # defect, and it is silent unless this case drives it.
        assert self._missing_icon_links(
            [dict(ln, rel="preload") for ln in complete]
        ) == [f"/{self.ICON_ICO}", f"/{self.ICON_PNG}", f"/{self.ICON_APPLE}"], (
            "an icon file mentioned by a non-icon relation satisfied the trio — the rel "
            "token, not just the href, is what makes the link an icon")
        # ...and a NON-SQUARE `sizes=` token is rejected, not merely parsed: `32x32 48x16`
        # parses to {32, 48}, which EQUALS the ICO's directory, so without this the same
        # declaration is ACCEPTED. Rejection is `None` (not a raise — see
        # `_parse_declared_sizes`: the parser feeds a collector, which must return a
        # violation rather than escape the whole page over one unreadable token).
        assert self._parse_declared_sizes("32x32 48x16") is None, (
            'a non-square sizes= token was accepted — "48x16" is not a slot a tab can '
            "draw, and it parses to the same set as the ICO's directory")
        # ...and the parser still PARSES what it should, so `None` is not the only answer
        # it can give (a guard whose every input returns None passes the row above
        # vacuously).
        assert self._parse_declared_sizes("32x32 48x48") == {32, 48}, (
            "a square multi-slot sizes= token did not parse — the ICO's own declaration "
            "then rededs as unreadable")
        assert self._parse_declared_sizes("any") is None, (
            'sizes="any" parsed as a pixel slot — it is SVG-only syntax that names no '
            "size at all, so it cannot be checked against a raster icon's IHDR")
        assert self._missing_icon_links(
            [dict(ln, href="//evil.example/favicon.ico")
             if ln["href"] == f"/{self.ICON_ICO}" else ln for ln in complete]
        ) == [f"/{self.ICON_ICO}"], (
            "an off-host href was accepted as this site's ICO — the origin was dropped")

    def test_the_trio_collector_returns_every_violation_instead_of_raising(self):
        """A violation COLLECTOR must return them all; one defect may not hide another.

        ⛔ WHY THIS EXISTS. `_icon_trio_violations` is documented as returning *every* way
        a page's `<head>` fails the icon contract, and `sizes` is OPTIONAL on
        `rel="icon"` in the spec — so a minimal, universally-supported icon declaration
        is honest markup. Measured on the code that ASSERTED instead of returning: that
        head raised `AssertionError: the tag declares no sizes= — a renderer then
        guesses` straight out of the collector, and a head that also omitted two of the
        three icon links reported only the sizes assertion — the missing-link violations
        were computed and then thrown away with the exception. On the `rel="icon"`
        links the requirement is a HOUSE RULE (see `_declared_sizes_violation`) and it is
        reported as a violation; the apple link's stays OPTIONAL.
        """
        png = self._png_size((self.WEB / self.ICON_PNG).read_bytes())
        apple = self._png_size((self.WEB / self.ICON_APPLE).read_bytes())
        ico_sizes = {i["declared"][0]
                     for i in self._ico_entries((self.WEB / self.ICON_ICO).read_bytes())}

        minimal = ("<head>"
                   f'<link rel="icon" href="/{self.ICON_ICO}">'
                   f'<link rel="icon" href="/{self.ICON_PNG}">'
                   f'<link rel="apple-touch-icon" href="/{self.ICON_APPLE}">'
                   "</head>")
        trio = self._icon_trio_violations("synthetic.html", minimal, png, ico_sizes, apple)
        assert len(trio) == 2, (
            "a spec-valid head whose two rel=icon links omit the OPTIONAL sizes= must "
            f"report exactly the two house-rule violations; got {trio}")
        assert any(self.ICON_ICO in v and "sizes" in v for v in trio), (
            f"omitting sizes= on the ICO link was not reported: {trio}")
        assert any(self.ICON_PNG in v and "sizes" in v for v in trio), (
            f"omitting sizes= on the PNG link was not reported: {trio}")
        assert not any(self.ICON_APPLE in v for v in trio), (
            "omitting the OPTIONAL sizes= on the apple link was reported as a violation — "
            "the href alone is what iOS needs, and that exemption is deliberate")

        # An unreadable token is a VIOLATION with the token quoted, not a raise: `any`
        # is SVG-only syntax naming no size, and a non-square list cannot be a tab slot.
        cases = {
            "any": "SVG-only token",
            f"{png[0]}x{apple[0]}": "non-square slot list",
            "big": "not a <w>x<h> list at all",
        }
        for token, why in cases.items():
            link = {"rel": "icon", "href": f"/{self.ICON_PNG}", "sizes": token}
            violation = self._declared_sizes_violation(
                "page.html", link, png, ico_sizes, apple)
            assert violation and token in violation, (
                f"sizes={token!r} ({why}) was not returned as a violation quoting the "
                f"token; got {violation!r}")

        # ...and ONE DEFECT MAY NOT HIDE ANOTHER. The head below omits two icon links
        # AND the sizes= on the one link it does declare; both must come back.
        partial = ("<head>"
                   f'<link rel="icon" href="/{self.ICON_ICO}">'
                   "</head>")
        both = self._icon_trio_violations("synthetic.html", partial, png, ico_sizes, apple)
        assert any("does not declare" in v for v in both), (
            f"the missing icon links were not reported: {both}")
        assert any("sizes" in v for v in both), (
            f"the omitted sizes= was not reported alongside the missing links: {both}")

    def test_the_icon_files_exist_and_are_usable(self):
        """Every icon a page points at exists, and has the shape the tag implies."""
        for name in (self.ICON_ICO, self.ICON_PNG, self.ICON_APPLE):
            assert (self.WEB / name).is_file(), f"website/{name} is missing — every page links it"

        png_w, png_h = self._png_size((self.WEB / self.ICON_PNG).read_bytes())
        assert png_w == png_h, f"favicon.png is {png_w}x{png_h} — a tab icon must be square"
        assert png_w >= 180, (
            f"favicon.png is {png_w}px; the apple-touch slot needs >=180 and a "
            "smaller source is upscaled blurry by the OS")

        apple_w, apple_h = self._png_size((self.WEB / self.ICON_APPLE).read_bytes())
        assert (apple_w, apple_h) == (180, 180), (
            f"apple-touch-icon.png is {apple_w}x{apple_h}; iOS reads 180x180")

        icons = self._ico_entries((self.WEB / self.ICON_ICO).read_bytes())
        assert len(icons) >= 2, f"favicon.ico holds {len(icons)} image(s) — ship 32 and 48"
        assert all(i["is_png"] for i in icons), (
            "favicon.ico carries a raw DIB image — this repo's builder writes PNG "
            "payloads, and a DIB rewrite means the container was replaced by hand")
        assert {i["declared"][0] for i in icons} >= {32, 48}, (
            f"favicon.ico sizes are {sorted(i['declared'][0] for i in icons)}; Windows uses 32 and 48")
        for i in icons:
            assert i["declared"] == i["actual"], (
                f"favicon.ico declares {i['declared']} but the payload is {i['actual']} — "
                "the directory's size is what a renderer reserves, so a mismatch draws "
                "a blank or clipped icon in some browsers and looks fine in others")
            assert (i["planes"], i["bpp"]) == (1, 32), (
                f"favicon.ico entry declares planes={i['planes']} bpp={i['bpp']}; "
                "a PNG payload is 1 plane / 32bpp and anything else is a hand-edited directory")

        # ...and every one of them has something DRAWN on it. A 256x256 flat
        # rectangle is a valid PNG at a valid size and shows a blank tab: the
        # measured placeholder this PR deletes from the blog was exactly that shape.
        # ⛔ THE ICO'S PAYLOADS ARE INCLUDED. `favicon.ico` is the file Windows and Edge
        # draw, and every assertion above reads the container's CLAIMS plus the
        # payload's geometry — a two-entry ICO declaring 32 and 48 over solid
        # `#060b14` payloads satisfies the entry count, the `{32,48}` set, the
        # `declared == actual` pairing AND `(1 plane, 32 bpp)`, i.e. all of them, while
        # rendering a blank tab. Measured on a synthesized file.
        # ⛔ WHAT THIS DOES NOT ASSERT. The pair below answers "is anything drawn on
        # it", not "is this the right art": one flat colour reds (dominant == 1.0) and
        # the deleted placeholder reds at 99.2%, but a 60 KB photograph of something
        # unrelated passes both. The distinct-colour floor is **2**, not 8: a
        # hard-edged two-colour mark (pixel art, a `crispEdges` SVG export, any mark
        # without antialiasing) is legitimate art and must not red — and the earlier
        # floor of 8 did red it while this very comment claimed a two-colour logo
        # would pass at 8. It could not, and a guard that cries wolf at honest
        # artwork is a guard the next person weakens instead of fixes.
        drawn: list[tuple[str, bytes]] = [
            (n, (self.WEB / n).read_bytes())
            for n in (self.ICON_PNG, self.ICON_APPLE)
        ]
        drawn += [
            (f"{self.ICON_ICO} entry {n} ({i['declared'][0]}x{i['declared'][1]})",
             i["payload"])
            for n, i in enumerate(icons) if i["is_png"]
        ]
        for name, data in drawn:
            problems = self._icon_mark_violations(name, data)
            assert not problems, "\n".join(problems)

    def test_the_icon_is_a_rounded_tile_and_the_mark_fills_it(self):
        """The tab icon is a ROUNDED tile whose MARK fills it — the shape, not the size.

        Both claims are the owner's report in #11486, and both were invisible to every
        assertion above: the shipped icon was a 256x256 opaque SQUARE whose triangle
        spanned 68% of the canvas, so it passed the size check, the decode, the pixel
        floors, the ICO directory checks and the wordmark ban, and still looked like a
        hard slab with a small glyph next to neighbours whose art fills their tile.

        Measured on the bytes, not on the markup: a page that declares a 256x256 icon
        says nothing about what is drawn inside it.

        `apple-touch-icon.png` is asserted the OTHER way round on purpose — iOS applies
        its own rounded mask to that file, so a pre-rounded image would be rounded twice
        and show dark corners on a light home screen.
        """
        def alpha_of(px: tuple[int, ...]) -> int:
            """A pixel's alpha; a PNG with no alpha channel is opaque by definition.

            ⛔ MEASURED, because the first version REQUIRED four channels here and the
            apple-touch assertion went through the same helper — so an apple-touch that
            was opaque and square (exactly what its message demands) but saved as colour
            type 2 redded with "an icon with no alpha channel cannot have a rounded
            corner", which is the opposite of that file's contract. The old asset set was
            colour type 2, so an honest re-export tripped it.
            """
            return px[3] if len(px) == 4 else 255

        def check_tile(label: str, data: bytes) -> None:
            w, h, rows = self._png_pixels(data)
            # The TAB icons are the ones that must be rounded, and a 3-channel PNG cannot
            # be: there is no alpha to carry the corner. (apple-touch is checked apart.)
            assert all(len(px) == 4 for row in rows for px in row), (
                f"{label} has no alpha channel — a tab icon must be a rounded tile, and a "
                "PNG without transparency can only be a square. Rebuild it with "
                "tools/build_site_icons.py")
            corners = ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))
            for x, y in corners:
                alpha = alpha_of(rows[y][x])
                assert alpha == self.ROUNDED_CORNER_ALPHA, (
                    f"{label} is opaque at its ({x},{y}) corner (alpha {alpha}) — a "
                    "hard square edge, which is the shape #11486 reports as a slab. "
                    "Rebuild it with tools/build_site_icons.py, which masks the corners")

            # ...and the tile is ROUNDED, not merely nibbled. "Corner is transparent" is
            # satisfied by four punched pixels on a square slab (measured green), so the
            # arc is measured as an inset along each edge, in fractions of the side: a
            # radius-r arc reaches the edge at exactly r.
            floor = int(self.CORNER_INSET_FLOOR * w)
            edges = {
                "top": [(x, 0) for x in range(w)],
                "bottom": [(x, h - 1) for x in range(w)],
                "left": [(0, y) for y in range(h)],
                "right": [(w - 1, y) for y in range(h)],
            }
            for edge, line in edges.items():
                for start, step in ((0, 1), (len(line) - 1, -1)):
                    inset = 0
                    i = start
                    while 0 <= i < len(line) and alpha_of(rows[line[i][1]][line[i][0]]) < 255:
                        inset += 1
                        i += step
                    assert inset >= floor, (
                        f"{label} is fully opaque {inset}px in from the {edge} corner — "
                        f"under the {floor}px ({self.CORNER_INSET_FLOOR:.0%} of {w}) a "
                        "rounded tile needs, so the corner is nibbled rather than rounded "
                        "and the tab still reads as a square slab (#11486)")

            # The ground is whatever opaque colour covers the most pixels; the mark is
            # everything far from it. Both are measured, so no colour is hardcoded.
            opaque = [px for row in rows for px in row if px[3] == 255]
            assert opaque, f"{label} has no fully opaque pixel at all"
            counts: dict[tuple[int, ...], int] = {}
            for px in opaque:
                counts[px[:3]] = counts.get(px[:3], 0) + 1
            ground = max(counts, key=lambda c: counts[c])
            xs = [x for y in range(h) for x in range(w)
                  if rows[y][x][3] == 255
                  and sum(abs(rows[y][x][i] - ground[i]) for i in range(3)) > 60]
            assert xs, f"{label} shows nothing that is not its ground colour"
            span = (max(xs) - min(xs) + 1) / w
            assert span >= self.MARK_WIDTH_FLOOR, (
                f"{label}'s mark spans {span:.0%} of the canvas width ({(1 - span) / 2:.0%} "
                f"padding each side) — under the {self.MARK_WIDTH_FLOOR:.0%} floor, so in a "
                "tab strip it reads as a small glyph on a slab (#11486). The artwork "
                "carries its own padding; tools/build_site_icons.py crops it out rather "
                "than shrinking the mark to fit it")

        check_tile(self.ICON_PNG, (self.WEB / self.ICON_PNG).read_bytes())
        for i, entry in enumerate(self._ico_entries((self.WEB / self.ICON_ICO).read_bytes())):
            if not entry["is_png"]:
                continue
            w, h = entry["declared"]
            check_tile(f"{self.ICON_ICO} entry {i} ({w}x{h})", entry["payload"])

        # ...and apple-touch is the exception, asserted rather than skipped.
        apple = (self.WEB / self.ICON_APPLE).read_bytes()
        w, h, rows = self._png_pixels(apple)
        for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
            assert alpha_of(rows[y][x]) == 255, (
                f"{self.ICON_APPLE} is transparent at ({x},{y}) — iOS masks this file "
                "itself, so pre-rounded corners render as dark notches on a light home "
                "screen. It is the one icon that must stay a full-bleed square")

    def test_the_wordmark_is_never_used_as_an_icon(self):
        """`/logo.png` is a 400x244 wordmark — a tab renders it as a squashed bar.

        Reads the same parsed `<link>` elements the trio assertion reads, and splits
        `rel` the same way: a regex over the raw markup is case-sensitive,
        double-quote-only and order-sensitive, so `rel="shortcut icon"` — which the
        trio assertion deliberately accepts — walked straight past it.

        The href is resolved against a root URL before it is compared, because every
        page here is served at a root-level path: on `/faq`, `href="logo.png"` IS
        `/logo.png`, and a substring test on the attribute sees neither. Matching on
        `"icon" in token` rather than on two exact tokens is what covers
        `apple-touch-icon-precomposed`, which iOS still honours.
        """
        for page in self._pages():
            head = self._head(page.read_text())
            for ln in self._links(head):
                rel = ln.get("rel", "").lower().split()
                if not any("icon" in token for token in rel):
                    continue
                violation = self._icon_target_violation(page.name, ln)
                assert violation is None, violation

    @classmethod
    def _icon_target_violation(cls, page_name: str, link: dict) -> str | None:
        """Why an ICON link's TARGET is not a usable tab icon; None means it is.

        Split out of the guard above for the reason `_declared_sizes_violation` is: every
        page in this tree links three valid icons, so inlined the whole check was executed
        only by CORRECT input. Measured: restoring the existence check to AFTER the `.svg`
        exemption (the pre-fix order) kept every test green while `href="/assets/nope.svg"`
        silently passed — the same "proved with a hand-run probe, pinned by nothing"
        defect this class exists to end. `test_the_icon_target_guard_reads_existence_and_
        shape` drives each branch with a synthetic link set.

        ⛔ THE BLACKLIST IS ONE FILENAME WIDE. The property it is about is "a tab icon
        must be square" — a browser squashes a wide image into the square slot — and a
        filename test cannot see `assets/premiselabs-logo.png`, which is 160x98 and is the
        same wordmark by another name (measured: it passed while `logo.png` was refused).
        So the TARGET IS READ.
        ⛔ AND THE BYTES ARE READ FOR EVERY FORMAT, NOT JUST `.png`: gating on one
        extension is the same one-rule-wide hole one level down — measured,
        `rel="icon" href="/assets/og-tortoise.jpg"` (1200x630) passed every icon
        assertion, and so did a href naming a file that does not exist.
        ⛔ AND EXISTENCE COMES FIRST, BEFORE ANY FORMAT EXEMPTION. The `.svg` exemption is
        about having no intrinsic pixel box to squash — it is NOT a licence to skip the
        file check, and an early `continue` that did so let a page link a nonexistent
        vector icon while this guard reported green (measured: the only format that did).
        """
        # ⛔ THE ORIGIN IS CHECKED, NOT DROPPED — via `_icon_ref`, the one helper that
        # already does it. `urlsplit("//evil.example/favicon.png").path` is
        # `/favicon.png`, so a netloc-discarding read grades an off-host icon as this
        # site's own file and reports green on a tab that draws someone else's image.
        # Measured before this fix: `_icon_target_violation("page.html", {"rel": "icon",
        # "href": "//evil.example/favicon.png"})` returned None.
        path_part = cls._icon_ref(link.get("href", ""))
        if not path_part:
            # `_icon_ref` also returns "" for an href that names NO host at all — an
            # inline `data:` icon is legitimate HTML, so "another host" would be a
            # false statement about it. The branch says what is true of every case.
            return (f"{page_name} links an icon that is not a file this site serves "
                    f"(rel={link.get('rel')!r} href={link.get('href')!r}) — this guard "
                    "cannot verify it")
        assert "/logo.png" not in path_part, (
            f"{page_name} uses the wordmark as an icon: rel={link.get('rel')!r} "
            f"href={link.get('href')!r}")
        target = cls.WEB / path_part.lstrip("/")
        if not target.is_file():
            return (f"{page_name} links an icon this site does not serve ({path_part}) — "
                    "the tab draws nothing")
        if path_part.endswith(".svg"):
            # A vector icon has no intrinsic pixel box to squash — the tab sizes it from
            # the viewBox — so it is EXEMPT BY NAME from the shape check, above.
            return None
        raw = target.read_bytes()
        if raw[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = cls._png_size(raw)
        elif raw[:2] == b"\xff\xd8":
            w, h = cls._jpeg_size(raw)
        elif raw[:4] == b"\x00\x00\x01\x00":
            # The ICO's directory entries are the slots a renderer reserves, so each of
            # them must be square too.
            for entry in cls._ico_entries(raw):
                if entry["declared"][0] != entry["declared"][1]:
                    return (f"{page_name} uses {path_part} as a tab icon and its "
                            f"{entry['declared'][0]}x{entry['declared'][1]} directory "
                            "entry is not square — that icon is drawn squashed")
            return None
        else:
            raise AssertionError(
                f"{page_name} uses {path_part} as a tab icon; this guard reads PNG, JPEG "
                "and ICO, and exempts only .svg — add a reader rather than leaving the "
                "tab unchecked")
        if w != h:
            return (f"{page_name} uses a {w}x{h} non-square image as a tab icon "
                    f"({path_part}) — the tab squashes it into a square")
        return None

    def test_the_icon_target_guard_reads_existence_and_shape(self):
        """NON-VACUITY for `_icon_target_violation`, driven by a synthetic link set.

        Every page links three valid icons, so on the real population this predicate is
        only ever run against input that passes — a regression in any branch is
        unobservable. MEASURED before this test existed: moving the existence check back
        below the `.svg` exemption left all 13 icon/card tests green while
        `href="/assets/nope.svg"` was accepted. Each edge is pinned in BOTH directions.
        """
        for link in ({"rel": "icon", "href": "/favicon.png"},
                     {"rel": "icon", "href": "/favicon.png?v=2"},
                     {"rel": "icon", "href": "/apple-touch-icon.png"},
                     {"rel": "icon", "href": "/favicon.ico"}):
            assert self._icon_target_violation("page.html", link) is None, (
                f"a valid icon link was reded: {link}")

        for missing in ("/assets/nope.png", "/assets/nope.svg"):
            violation = self._icon_target_violation(
                "page.html", {"rel": "icon", "href": missing}) or ""
            assert "does not serve" in violation, (
                f"an icon link to a missing file ({missing}) was accepted — the format "
                "exemption must come AFTER the existence check, measured: an early "
                f"`.svg` continue re-opened exactly this. Got: {violation!r}")

        wide = self._icon_target_violation(
            "page.html", {"rel": "icon", "href": "/assets/og-tortoise.jpg"}) or ""
        assert "non-square" in wide, (
            "a 1200x630 JPEG used as a tab icon was accepted — the shape check is "
            f"`.png`-only again. Got: {wide!r}")

        wordmark = self._icon_target_violation(
            "page.html", {"rel": "icon", "href": "/assets/premiselabs-logo.png"}) or ""
        assert "non-square" in wordmark, (
            "a 160x98 wordmark was accepted as a tab icon — the filename blacklist is the "
            f"only rule again. Got: {wordmark!r}")

        # ⛔ THE ORIGIN EDGE, AND IT MUST BE PINNED HERE OR THE FIX IS UNWATCHED. This
        # helper is graded on hrefs; every other fixture in this test is ON-SITE, so a
        # revert to `urlsplit(urljoin(...)).path` (which discards the netloc) leaves
        # every other assertion in this test behaving identically and the suite GREEN.
        # MEASURED with that revert restored: both off-host forms below return None.
        # `data:` is included because `_icon_ref` rejects it too, for a different
        # reason (it names no host at all), so the branch must not claim "another host".
        for off_host in ("//evil.example/favicon.png",
                         "https://evil.example/favicon.png",
                         "data:image/png;base64,AAAA"):
            refused = self._icon_target_violation(
                "page.html", {"rel": "icon", "href": off_host}) or ""
            assert "not a file this site serves" in refused, (
                f"{off_host!r} was accepted as a tab icon — the origin is being "
                f"discarded again (`urlsplit(...).path` drops the netloc). "
                f"Got: {refused!r}")

        try:
            self._icon_target_violation("page.html", {"rel": "icon", "href": "/logo.png"})
        except AssertionError:
            pass
        else:
            raise AssertionError("the wordmark was accepted as a tab icon")

    # --- 2. the preview resolves and is the size it claims ---------------

    @staticmethod
    def _head_markup(head: str) -> str:
        """The head with its two non-markup regions removed: comments and CSS/JS bodies.

        Both legitimately CONTAIN angle brackets — this repo's own noindex rationale
        quotes `<meta name="robots" content="noindex">` inside an HTML comment, and
        404.html inlines its whole stylesheet — so counting raw `<meta` occurrences
        in the document would flag prose as a broken tag.

        `<noscript>` and `<template>` are stripped for the same reason `<script>` is:
        their contents are not live head metadata. A scraper does not read an
        `og:image` that only exists inside a `<noscript>` fallback, so a guard that
        did would report green on a page with no preview — measured, before this
        list was complete.
        """
        text = re.sub(r"<!--.*?-->", "", head, flags=re.S)
        stripped = re.sub(r"<(style|script|noscript|template)\b.*?</\1>", "", text,
                          flags=re.S | re.I)
        # An UNCLOSED wrapper defeats the strip above, and CPython's parser treats
        # `noscript`/`template` as ordinary elements, so the metas inside are still
        # counted and still found — while a browser puts them in the inert fragment
        # and renders no card. Measured: an unclosed `<template>` before the metas read
        # green. `style`/`script` are CDATA to that parser and already fail closed, so
        # they are listed here only to keep one rule rather than two.
        leftover = re.findall(r"<(style|script|noscript|template)\b", stripped, flags=re.I)
        assert not leftover, (
            f"an unclosed <{leftover[0]}> in the head — the strip cannot remove a "
            "wrapper with no end tag, so the tags after it read as live markup here "
            "while a browser treats them as inert content (and a scraper sees no card)")
        return stripped

    @staticmethod
    def _head_metas(head: str) -> tuple[list[dict[str, str]], list[str]]:
        """Parse the head: one dict per `<meta>` element, plus any text that leaked.

        Used instead of the regexes above wherever STRUCTURE is the question. A
        regex answers "does this substring appear", which a malformed tag also
        satisfies; only a parser tells you the substring became an element.
        """

        # Elements whose TEXT is legitimate head content: a `title`, an inlined
        # `style` (404.html carries all of its CSS in the head), a `script`.
        # Their data is not the remnant of an unclosed tag, so it is not stray.
        TEXT_ELEMENTS = frozenset({"title", "style", "script", "noscript"})

        class Collector(HTMLParser):
            def __init__(self) -> None:
                super().__init__(convert_charrefs=True)
                self.metas: list[dict[str, str]] = []
                self.stray: list[str] = []
                self.in_head = False
                self.text_depth = 0

            def handle_starttag(self, tag: str, attrs) -> None:
                if tag == "head":
                    self.in_head = True
                elif not self.in_head:
                    return
                elif tag == "meta":
                    self.metas.append({k: (v or "") for k, v in attrs})
                elif tag in TEXT_ELEMENTS:
                    self.text_depth += 1

            def handle_endtag(self, tag: str) -> None:
                if tag == "head":
                    self.in_head = False
                elif tag in TEXT_ELEMENTS and self.text_depth:
                    self.text_depth -= 1

            def handle_data(self, data: str) -> None:
                if self.in_head and not self.text_depth and data.strip():
                    self.stray.append(data.strip())

        collector = Collector()
        collector.feed(head)
        collector.close()
        return collector.metas, collector.stray

    def test_the_head_is_well_formed_markup(self):
        """A meta tag that lost its `>` swallows the next one — and the regexes still pass.

        MEASURED ON THIS VERY CHANGE, which is why this test exists. Two pages were
        edited to add `twitter:image:alt`; the edit dropped the closing `>` of the
        `og:image:alt` line, so the file read

            <meta property="og:image:alt" content="…"
            <meta name="twitter:image:alt" content="…">>

        Every value assertion in `test_every_page_declares_a_social_preview` passed on
        both pages (`_og`/`_twitter` match `name="twitter:…"` anywhere in the head,
        including inside text another tag swallowed). A browser instead parsed ONE
        element, found no `twitter:image:alt` at all, and leaked a bare `>` into the
        head as visible text. A guard whose verdict is unchanged by a broken tag is
        not measuring the tag.

        So the structure is asserted from the parsed tree: every `<meta` the source
        spells must arrive as its OWN element (a swallowed one loses the count), and
        no non-whitespace text may appear in the head at all.
        """
        for page in self._pages():
            text = page.read_text()
            assert "<head" in text and "</head>" in text, f"{page.name} has no head"
            markup = self._head(text)

            metas, stray = self._head_metas(markup)
            assert not stray, (
                f"{page.name} emits text into <head>: {stray[:3]} — almost always the "
                "remnant of a tag that was not closed, which a browser renders as a "
                "stray character and a scraper ignores")
            assert markup.count("<meta") == len(metas), (
                f"{page.name} spells {markup.count('<meta')} '<meta' occurrences in the head "
                f"but only {len(metas)} parse as elements — a tag lost its '>' and "
                "swallowed the next one")

            # A swallowed tag, a stray quote or a doubled `"` all show up as an
            # attribute that has no business being there — a bare `"` parses as an
            # attribute whose KEY is a quote character. An all-or-nothing key check
            # is what catches the shape; no value assertion can, because the value
            # the regex reads is the one before the damage.
            allowed = {"property", "name", "content", "charset", "http-equiv"}
            for meta in metas:
                assert meta, f"{page.name} has a <meta> element with no attributes"
                odd = set(meta) - allowed
                assert not odd, (
                    f"{page.name} parsed a <meta> with attribute key(s) {sorted(odd)!r} "
                    f"({meta}) — a stray or doubled quote leaves a quote character "
                    "where an attribute name should be, and a swallowed tag leaves tag "
                    "text there")
                assert "<" not in " ".join(meta.values()) and ">" not in " ".join(meta.values()), (
                    f"{page.name} parsed a <meta> whose attributes contain tag text "
                    f"{meta} — the following tag was swallowed by an unclosed one")

    def test_every_page_declares_a_social_preview(self):
        """`og:image` + its size + alt, and the Twitter mirror, on every page."""
        for page in self._pages():
            head = self._head(page.read_text())
            image = self._og(head, "image")
            assert image, f"{page.name} declares no og:image — a shared link renders no card"
            assert image.startswith("https://"), (
                f"{page.name} og:image is {image!r}; a link-preview scraper does NOT "
                "resolve a relative URL, so a same-origin path silently renders no card "
                "at all while the page looks configured (the failure mode the bespoke "
                "preview pages already document)")
            assert image.split("/")[2] in self.SITE_ORIGINS, (
                f"{page.name} og:image points at {image.split('/')[2]}, not one of "
                f"{sorted(self.SITE_ORIGINS)} — the card must be served by us or the "
                "scraper fetches it from a host we do not control")
            assert self._og(head, "image:width") and self._og(head, "image:height"), (
                f"{page.name} declares og:image without its dimensions; scrapers that "
                "trust the tag then request an assumed size")
            alt = (self._og(head, "image:alt") or "").strip()
            assert alt, (
                f"{page.name} declares no usable og:image:alt — the card is unreadable to "
                "a screen reader and to any client that suppresses remote images (a "
                "whitespace-only value is not alt text)")
            assert (self._twitter(head, "image:alt") or "").strip() == alt, (
                f"{page.name} twitter:image:alt ({self._twitter(head, 'image:alt')!r}) "
                f"disagrees with og:image:alt ({alt!r}) — one platform then describes a "
                "different picture than the other")
            assert self._twitter(head, "card") == "summary_large_image", (
                f"{page.name} twitter:card is {self._twitter(head, 'card')!r}; without "
                "summary_large_image the 1200x630 image is cropped to a square thumbnail")
            assert self._twitter(head, "image") == image, (
                f"{page.name} twitter:image ({self._twitter(head, 'image')}) disagrees "
                f"with og:image ({image}) — one of the two platforms gets the wrong picture")

    def test_the_declared_preview_size_matches_the_file(self):
        """The tag is a promise about bytes; check it against the bytes.

        The per-page checks live in `_card_violations` so the PAGE-LEVEL application can
        be driven by a test (`test_the_page_card_predicate_applies_every_check_it_
        declares`); this loop applies them to every shipped page, which is what makes the
        tree's own cards guarded rather than only their helpers.
        """
        for page in self._pages():
            head = self._head(page.read_text())
            violations = self._card_violations(page.name, head)
            assert not violations, "\n".join(violations)

    def test_the_page_card_predicate_applies_every_check_it_declares(self):
        """NON-VACUITY for `_card_violations`: each branch REDS through the predicate.

        ⛔ WHY. MEASURED on the code before this predicate existed, all 13 tests in this
        class GREEN under each of three mutants — the floor application deleted,
        `_card_size` reverted to `_jpeg_size`, and the image resolved with the raw
        `image.split("//", 1)[-1]` form. The helpers were pinned and nothing pinned the
        loop that called them, so the loop's half could be `assert True`. That is the
        identical defect the icon half of this class was already fixed for.
        """
        good = self._head((self.WEB / "index.html").read_text())
        image = self._og(good, "image")
        assert image, "the fixture page declares no og:image"
        assert self._card_violations("index.html", good) == [], (
            "the shipped index.html was reported as violating its own card contract: "
            f"{self._card_violations('index.html', good)}")
        card_path, card_bytes = self._og_card_bytes(image)
        assert card_path is not None and len(card_bytes) > self.CARD_MIN_BYTES, (
            "the fixture card does not resolve in the served tree")

        def violations(head: str, load) -> list[str]:
            return self._card_violations("synthetic.html", head, load=load)

        # 1. THE ENCODED-SIZE FLOOR, applied by the PREDICATE and not merely by the
        #    helper: a flat 1200x630 JPEG whose pixels are nothing.
        flat = b"\xff\xd8" + b"\x00" * (self.FLAT_CARD_BYTES - 2)
        assert self._card_floor_violation("synthetic.html", card_path, flat), (
            "the fixture is not the flat card this case needs — FLAT_CARD_BYTES no "
            "longer trips the floor")
        flat_v = violations(good, lambda _url: (card_path, flat))
        assert any("floor" in v and "flat" in v for v in flat_v), (
            "a flat card fed through the page predicate produced no floor violation, so "
            f"the loop is not applying it; got {flat_v}")

        # 2. THE FORMAT DISPATCH reads the type the PAGE declares, and holds the bytes to
        #    it. Reverting `_card_size` to a JPEG-only reader reds the first case.
        png = self._two_colour_png(180)
        png_typed = good.replace('content="image/jpeg"', 'content="image/png"')
        assert png_typed != good, "the fixture head does not declare og:image:type"
        png_ok = violations(png_typed, lambda _url: (card_path, png))
        assert not any("og:image:type" in v for v in png_ok), (
            "a page that honestly declares a PNG card was reported as failing the "
            f"format check — the predicate is not dispatching on the declared type: {png_ok}")
        jpeg_typed_over_png = violations(good, lambda _url: (card_path, png))
        assert any("og:image:type" in v for v in jpeg_typed_over_png), (
            "a card whose bytes contradict the declared og:image:type passed the "
            f"predicate; got {jpeg_typed_over_png}")

        # 3. THE QUERY-STRIPPING PATH, with the REAL loader: a `?v=2` cache-bust on a
        #    correct tag resolves to the served file. Restoring the raw split form reds
        #    this — the helper test alone cannot, because it calls `_og_asset_path`
        #    directly rather than through the predicate.
        busted = good.replace(image, image + "?v=2")
        assert busted != good, "the fixture head does not carry the card URL"
        bust_v = self._card_violations("synthetic.html", busted)
        assert not any("not in the served tree" in v for v in bust_v), (
            "a legitimate ?v=2 cache-bust turned the card into a 404 in the predicate — "
            f"the query is not being stripped on this path: {bust_v}")

        # 4. AN ABSENT CARD and a page with no og:image at all, both reported.
        missing = good.replace(image, image.rsplit("/", 1)[0] + "/nope.jpg")
        assert any("not in the served tree" in v
                   for v in self._card_violations("synthetic.html", missing)), (
            "a card URL that resolves to nothing was accepted")
        headless = self._head("<head></head>")
        assert any("no og:image" in v
                   for v in self._card_violations("synthetic.html", headless)), (
            "a head with no og:image produced no violation")

        # 5. A USER-ENTERED size that is not a pixel count is a violation, not a crash:
        #    `int()` on a non-numeric attribute raised out of the loop before this split.
        junk = good.replace('content="1200"', 'content="wide"')
        assert junk != good, "the fixture head does not declare og:image:width"
        junk_v = self._card_violations("synthetic.html", junk)
        assert any("not a pixel count" in v for v in junk_v), (
            f"og:image:width='wide' did not produce a dimension violation: {junk_v}")

        # 6. A DECLARED size that disagrees with the bytes, so the dimension comparison
        #    itself is pinned (deleting it otherwise leaves every case above green).
        wrong = good.replace('content="1201"', 'content="1202"')
        if wrong == good:
            wrong = good.replace('content="1200"', 'content="1201"')
        wrong_v = self._card_violations("synthetic.html", wrong)
        assert any("1201x630" in v or "1x630" in v for v in wrong_v), (
            f"a declared width one pixel off the file was not reported: {wrong_v}")

    def test_the_card_type_dispatch_reads_the_format_the_page_names(self):
        """NON-VACUITY for `_card_size`: BOTH reader branches, on real bytes.

        Every page in this tree declares `og:image:type: image/jpeg`, and the only PNG
        card the repo ever had is deleted by this PR — so the `image/png` branch of the
        dispatch was executed by NOTHING. A branch that returned the other format's
        numbers, or a `readers` dict reduced to JPEG alone, read green.

        Each branch is run here against bytes it must accept AND against bytes of the
        other format, so the dispatch cannot be weakened by deleting a reader; and an
        unknown type must be REFUSED rather than sniffed, because a sniffing guard
        accepts a lie about the type it was written to catch.
        """
        card = (self.WEB / "assets" / "og-tortoise.jpg").read_bytes()
        assert self._card_size("image/jpeg", card) == (1200, 630), (
            "the shipped card, declared as what it is, does not read its own SOF frame")

        png = self._two_colour_png(180)
        assert self._card_size("image/png", png) == (180, 180), (
            "a PNG card must be read by the PNG reader and report its own IHDR")

        for declared, raw, expected in (("image/jpeg", png, "not a JPEG"),
                                        ("image/png", card, "not a PNG")):
            try:
                size = self._card_size(declared, raw)
            except AssertionError as exc:
                assert expected in str(exc), (
                    f"declaring {declared!r} over the OTHER format must red with "
                    f"{expected!r}, got: {exc}")
            else:
                raise AssertionError(
                    f"declaring {declared!r} over bytes of the other format returned "
                    f"{size} — a lying og:image:type passed the guard")

        try:
            self._card_size("image/webp", card)
        except AssertionError as exc:
            assert "no reader" in str(exc), exc
        else:
            raise AssertionError("an unknown og:image:type was accepted, not refused")

    def test_the_content_guards_reject_a_blank_image_and_keep_their_measured_floors(self):
        """NON-VACUITY: each content guard must FAIL on the image it was written for.

        Every floor in this class was argued from measurements taken outside CI, and
        a guard's failure path is exactly what a PR body forgets. Asserted here: the
        decoder reads real pixels, a blank image really reds BOTH floors, and the
        card floor really sits above the flat JPEG that defeated its first value.

        ⛔ The two card numbers are a PAIR, on purpose. #11318's first card floor was
        20 KB while a flat 1200x630 JPEG at maximum quality measures 22,016 B — so
        the check passed the exact image it existed to reject. Raising the floor
        without re-measuring the flat case repeats the mistake; this pin makes that
        red instead of green.
        """
        blank = self._flat_png()
        counts = self._png_palette(blank)
        assert sum(counts.values()) == 256 * 256, (
            "the decoder did not count every pixel of a 256x256 PNG, so a dominant-colour "
            "ratio computed from it is meaningless")
        assert len(counts) == 1, f"a flat PNG decoded to {len(counts)} colours, not 1"

        problems = self._icon_mark_violations(self.ICON_PNG, blank)
        assert len(problems) == 2, (
            "a blank icon must trip BOTH content floors (distinct colours and dominant "
            f"colour); it tripped {len(problems)}: {problems}")

        # A SYNTHETIC fixture is not in the served tree, so its message must not name a
        # `website/…` path — that sends the reader looking for a file that never existed.
        synthetic = self._icon_mark_violations("a synthetic fixture", blank, in_tree=False)
        assert synthetic and not any(p.startswith("website/") for p in synthetic), (
            "a synthetic fixture's message claims a `website/…` path: "
            f"{synthetic}")

        # ...and the OTHER direction, which nothing measured: a legitimate TWO-COLOUR
        # mark must not red. Without this fixture `ICON_MIN_COLOURS` can go back to 8 —
        # the review-1 defect, red on a `crispEdges`/pixel-art mark — and every test in
        # this class stays green, because the only icons in the tree decode to 200+
        # colours. A floor is only pinned when both of its edges are.
        mark = self._two_colour_png()
        mark_counts = self._png_palette(mark)
        assert len(mark_counts) == 2, (
            f"the two-colour fixture decoded to {len(mark_counts)} colours, so it isolates "
            "nothing")
        assert max(mark_counts.values()) / sum(mark_counts.values()) < self.ICON_MAX_DOMINANT, (
            "the fixture's accent covers too little of the canvas to isolate the "
            "colour-count floor from the dominant-colour one")
        assert self._icon_mark_violations(
            "the two-colour fixture", mark, in_tree=False) == [], (
            "a hard-edged two-colour mark reded "
            f"({self._icon_mark_violations('the two-colour fixture', mark, in_tree=False)}) "
            f"— ICON_MIN_COLOURS "
            f"is {self.ICON_MIN_COLOURS}, and a mark without antialiasing is art the "
            "next person might choose, not a blank rectangle")

        assert self.FLAT_CARD_BYTES < self.CARD_MIN_BYTES, (
            f"the card floor ({self.CARD_MIN_BYTES} B) is not above the flat 1200x630 "
            f"JPEG it exists to reject ({self.FLAT_CARD_BYTES} B), so a flat card passes")
        flat_card = b"\xff\xd8" + b"\x00" * (self.FLAT_CARD_BYTES - 2)
        # ⛔ AND PADDING IS NOT PAINT. Measuring FILE length let a flat image clear the
        # floor by appending zeros after the EOI — measured: +36 KB of trailing zeros
        # took a 4,723 B flat card past 40,000 B green. The floor reads the ENCODED
        # image, so the same bytes padded are still rejected.
        padded = flat_card[:2] + b"\x00" * (self.FLAT_CARD_BYTES - 4) + b"\xff\xd9"
        padded += b"\x00" * (self.CARD_MIN_BYTES + 1_000 - len(padded))
        assert len(padded) > self.CARD_MIN_BYTES, "the padding fixture is not past the floor"
        assert self._card_floor_violation("page.html", Path("card.jpg"), padded), (
            "a flat card PADDED past the floor cleared it — the floor is measuring the "
            "file, not the encoded image, and trailing bytes are not paint")

        # ...and the DECODER itself, on the path every real icon takes. Both fixtures
        # above emit filter type 0 only, so Sub/Up/Average/Paeth reconstruction was
        # executed by no test — and a wrong predictor there invents MORE colours and a
        # LOWER dominant share, i.e. it satisfies both floors while the guard reads
        # noise as art. The failure direction is a false PASS, so nothing else in this
        # file would notice; this fixture is patterned row by row so each filter has a
        # different, non-degenerate predictor.
        filtered, expected, filters = self._filtered_png()
        assert filters == [0, 1, 2, 3, 4], (
            f"the fixture no longer exercises every PNG filter type (got {filters}) — "
            "then the reconstruction branch is untested again")
        assert self._png_palette(filtered) == expected, (
            "the decoder did not reproduce the pixels a filter-type-1..4 PNG encodes — "
            "every real icon in the tree uses those filters")
        assert self._card_floor_violation("page.html", Path("card.jpg"), flat_card), (
            "a card the size of a measured flat JPEG cleared the floor")
        # ...and the card that ships is what clears it, so the floor is not just
        # "always red" and not "any file at all".
        shipped = (self.WEB / "assets" / "og-tortoise.jpg").read_bytes()
        assert self._card_floor_violation("page.html", Path("og-tortoise.jpg"),
                                          shipped) is None, (
            f"the shipped card ({len(shipped)} B) no longer clears the floor — look at "
            "it, then re-measure before touching CARD_MIN_BYTES")

        # ⛔ AND A TRAILING `FF D9` IN THE PADDING RE-OPENED THE HOLE. `rfind` finds the
        # last EOI in the FILE, and the two bytes that close padding are indistinguishable
        # from an image's end once you are outside any scan — measured: the same 4,723 B
        # flat card + zeros + `FF D9` = 41,500 B cleared the floor green. The walk now
        # stops at the EOI that ends the SCAN, so the trailer is not paint.
        trailer = (flat_card[:2] + b"\x00" * (self.FLAT_CARD_BYTES - 4) + b"\xff\xd9"
                   + b"\x00" * (self.CARD_MIN_BYTES + 1_000 - self.FLAT_CARD_BYTES)
                   + b"\xff\xd9")
        assert len(trailer) > self.CARD_MIN_BYTES, "the trailer fixture is not past the floor"
        assert self._card_floor_violation("page.html", Path("card.jpg"), trailer), (
            "a flat card padded and CLOSED WITH FF D9 cleared the floor — the measure is "
            "taking the last EOI in the file instead of the one that ends the image")

        # ⛔ AND THE MEASURE IS FORMAT-AWARE. A PNG's image ends at its IEND chunk; `FF D9`
        # inside IDAT is ordinary compressed data, so a measure that hunts `FF D9` both
        # FALSE-PASSES a padded PNG card and FALSE-FAILS a real one. `website/logo.png`
        # is the real witness: 35,670 B, and its IDAT happens to contain `FF D9`.
        # ⛔ MEASURED AT THIS HEAD, because the number is not folklore: the 6,280-short
        # (29,390 B) reading belongs to the SUPERSEDED `rfind`-for-the-last-`FF D9`
        # measure. The current segment/scan walk reads `logo.png` at its full 35,670 B
        # even when handed the JPEG branch, so it does NOT stop at an interior `FF D9`.
        # What the pin below therefore witnesses is narrower than the bug story: that
        # whichever branch ran ended at the file's own end. HEAD-MEASURED, it can NOT
        # tell the PNG branch from the JPEG one on `logo.png` — deleting the PNG branch
        # leaves it green, because the current JPEG walk also reads this PNG in full.
        # The padded-PNG fixture above is what catches a dispatch regression; this
        # assertion only catches a walk that ends anywhere but the file's own end.
        padded_png = self._flat_png(64)
        padded_png += b"\x00" * (self.CARD_MIN_BYTES + 1_000 - len(padded_png))
        assert self._card_floor_violation("page.html", Path("card.png"), padded_png), (
            "a flat PNG padded past the floor cleared it — the floor measured the file, "
            "not the PNG's chunks")
        in_tree_png = (self.WEB / "logo.png").read_bytes()
        assert b"\xff\xd9" in in_tree_png, (
            "website/logo.png no longer contains an FF D9 byte pair, so it no longer "
            "isolates the JPEG-delimiter bug it was chosen for — pick another PNG (or "
            "state that this witness is gone)")
        assert self._encoded_length(in_tree_png) == len(in_tree_png), (
            f"website/logo.png ({len(in_tree_png)} B) measures "
            f"{self._encoded_length(in_tree_png)} B — the PNG chunk walk did not end at "
            "the file's own end (a mis-parsed chunk length, or trailing bytes after "
            "IEND)")
        assert self._encoded_length(shipped) == len(shipped), (
            f"the shipped JPEG ({len(shipped)} B) measures "
            f"{self._encoded_length(shipped)} B — its stream must end at its own EOF")

        # The VALUE readers accept honest markup. A reordered attribute and a
        # cache-busting query are identical to a browser and to every scraper; the
        # source-order regexes they replaced called both broken (#11318 review 2).
        tag = ('<head><meta content="https://tortoise.premiselabs.co/assets/'
               'og-tortoise.jpg?v=2" property="og:image">')
        image = self._og(tag, "image")
        assert image is not None and image.endswith("og-tortoise.jpg?v=2"), (
            f"a reordered og:image tag read as {image!r} — that is the false red the "
            "parser replaced the regex for")
        assert self._og_asset_path(image).is_file(), (
            "a legitimate `?v=2` cache-bust on a correct og:image resolves to no file")

    def test_the_shared_preview_is_the_agreed_size(self):
        """1200x630 is the size the scrapers crop to; the shared image must be it."""
        shared = self.WEB / "assets" / "og-tortoise.jpg"
        assert shared.is_file(), "the shared preview image is missing"
        assert self._jpeg_size(shared.read_bytes()) == self.OG_SIZE, (
            f"assets/og-tortoise.jpg is not {self.OG_SIZE[0]}x{self.OG_SIZE[1]}")

    def test_the_two_pasted_urls_carry_the_preview_from_the_one_host(self):
        """The two URLs that get pasted into a chat: `premiselabs.co` and its `product root`.

        ⛔ THIS USED TO BE A TWO-ORIGIN TEST, and the property it guarded was "the
        card comes from the host the page is on" — `index.html` from
        premiselabs.co, `product.html` from tortoise.premiselabs.co. Epic #11762
        removed the second host (it is now a permanent 301), so that pair can no
        longer be the assertion: the surviving property is simply "both pasted
        URLs resolve to one origin, and both cards name it". Asserting the old
        pair would have pinned the very defect the epic removes.
        """
        origin = "https://premiselabs.co/"
        for name in ("index.html", "product.html"):
            head = self._head((self.WEB / name).read_text())
            image = self._og(head, "image")
            assert image and image.startswith(origin), (
                f"{name} is served from {origin} but points its preview at {image} — "
                "the card must come from the host that serves the page")

    # --- the icon, on EVERY surface ------------------------------------

    DASHBOARD_ROOT = WEB / "apps" / "dashboard" / "public"
    ICON_FILES = ("favicon.ico", "favicon.png", "apple-touch-icon.png")

    # ⛔ THE TWO RELATIONS THE TRIO USES, BY NAME — never a substring of the value.
    # `mask-icon` and `apple-touch-icon-precomposed` are DISTINCT relation tokens
    # (WebKit's own parser and Google's favicon guidance both separate them from `icon`),
    # so `"icon" in token` simultaneously refused a correct page that adds a legitimate
    # `mask-icon` and could not tell `rel="icon"` from `rel="apple-touch-icon"`.
    ICON_RELS = ("icon", "apple-touch-icon")

    # The app SURFACE is its own Pages deployment on its own origin, so its pages may
    # name `https://app.premiselabs.co/…` absolutely. `SITE_ORIGINS` stays the marketing
    # pair on purpose: it is what the `og:image` guard holds a card to, and widening THAT
    # would loosen a different check.
    ICON_ORIGINS = frozenset({*SITE_ORIGINS, "app.premiselabs.co"})

    @classmethod
    def _icon_hrefs(cls, html: str) -> list[str]:
        """The `href` of every tab-icon / touch-icon link in a page's head, in order.

        ⛔ THE RELATION IS NAMED, NOT SUBSTRING-MATCHED. `mask-icon` and
        `apple-touch-icon-precomposed` are DISTINCT relation tokens, so the previous
        `any("icon" in token for token in rel)` counted a legitimate `mask-icon` as a
        fourth tab icon (a FALSE RED on a correct page) and matched every one of the
        three kinds identically (the fail-open the rel assertion below closes).
        """
        out = []
        for link in cls._links(cls._head(html)):
            rel = link.get("rel", "").lower().split()
            if any(token in cls.ICON_RELS for token in rel):
                out.append(link.get("href", ""))
        return out

    @staticmethod
    def _href_version(href: str) -> str | None:
        query = href.split("?", 1)[1] if "?" in href else ""
        for part in query.split("&"):
            if part.startswith("v="):
                return part[2:]
        return None

    @staticmethod
    def _head_element_count(rendered: str) -> int:
        r"""How many `<head>` ELEMENTS a source renders — `<header>` is not one.

        ⛔ SPLIT OUT SO THE COUNT IS DRIVEN BY AN ASSERTION, not only by the surface test.
        The first cut was `rendered.count("<head")`, which counts a `<header>` element as a
        second `<head>`: `<header>` starts with the substring `<head`, and every one of the
        three self-rendered sources could legitimately gain one. It refused an honest page
        with a diagnosis ("a SECOND document … grade each one") describing a document that
        does not exist — a false red, and the class this file's `_icon_trio_violations`
        docstring says must not ship.

        ⛔ A WORD BOUNDARY ALONE IS NOT ENOUGH, IN EITHER DIRECTION (review rounds 7-8).
        `<head\b` is CASE-SENSITIVE and HTML tag names are not: `<HEAD>` counted 0, so a
        genuine second document spelled in uppercase passed `heads == 1` and the guard
        sliced the first while reporting green for both — the fail-open this count exists to
        prevent. And the boundary still matched `<head` inside a JS STRING: these sources
        build HTML with `.replace()` / concatenation, so `html.replace("<head", "<head>")`
        counted 2 and refused an honest edit. `(?=[\s/>])` requires the tag name to END there
        (so `<header>`, `<heading>`, `<head-x>` and `"<head"` are not matches) and `re.I`
        makes the name case-insensitive — the same flag `HEADING_TAG_RE` (`:2577`) carries.
        `<head>`, `<head >`, `<head\t>`, `<head\n>` and `<head/>` each count one, in any case.
        """
        return len(re.findall(r"<head(?=[\s/>])", rendered, re.I))

    def test_every_surface_declares_the_same_versioned_icon_set(self):
        """ONE mark on every origin — and a URL that changes when the bytes do.

        Two defects, both reported visibly as "this page has a different tab icon":

        1. **Two deployments, three marks.** `app.premiselabs.co` is a SEPARATE Pages
           project (root `website/apps/dashboard/public`), and its entry points declared
           `data:,` (a blank icon, so the browser drew its generic glyph) or `/logo.png`
           (a 400x244 wordmark, squashed into the square slot). The marketing pages had
           the real trio, so the same brand showed three different tabs (#11407).
        2. **The URL never moved when the artwork did.** `/favicon.ico` carries
           `max-age=14400` and a browser keeps a favicon far longer than any response
           header, so re-exporting the mark left old tabs — and old pages whose HTML was
           cached — drawing the superseded slab indefinitely. The header is the measurable
           part, and it is measured: `curl -sI https://tortoise.premiselabs.co/favicon.ico`
           → `cache-control: public, max-age=14400, must-revalidate`. Every href therefore
           carries `?v=<sha256[:12]>`, and the version is asserted
           against the file's OWN bytes below, so a re-export without a bump reds here.

        The `sizes` attributes are asserted for the marketing tree by
        `test_every_page_declares_the_icon_trio`, and for BOTH trees by
        `_surface_icon_violations` (which runs the same name-dispatching trio guard);
        this test is about the SET being identical across trees and self-invalidating,
        which is what harmonising means.
        """
        expected = {}
        for name in self.ICON_FILES:
            expected[f"/{name}"] = hashlib.sha256((self.WEB / name).read_bytes()).hexdigest()[:12]
        assert len(set(expected.values())) == len(expected), (
            f"two icon files are byte-identical, so one version no longer identifies one "
            f"asset: {expected}")

        # The dashboard project serves the SAME bytes: that is what stops the two
        # deployments drifting into two marks, and a re-export that forgets one tree reds.
        for name in self.ICON_FILES:
            local = self.DASHBOARD_ROOT / name
            assert local.is_file(), (
                f"the dashboard project has no {name} — app.premiselabs.co would fall back to "
                "a generic tab icon (or 404) while the marketing host shows the real one")
            assert local.read_bytes() == (self.WEB / name).read_bytes(), (
                f"website/apps/dashboard/public/{name} differs from website/{name} — the app "
                "origin and the marketing origin would show different marks")

        # ⛔ THE /admin CONSOLE IS A SURFACE TOO. `website/apps/blog-admin/index.html` is
        # staged to `dist/admin/` and served on the APP origin, so a browser there asks for
        # `/favicon.ico` — the UNVERSIONED path — unless the page declares a versioned link.
        # It declared none, so a stale console tab after a re-export was still possible while
        # every on-disk surface was covered (review round 1). Its links resolve against the app
        # root, and it may name that origin absolutely, so it grades with the app surface.
        surfaces = {
            "marketing": (self._pages(), self.WEB, self.SITE_ORIGINS),
            # `rglob`, not `glob`: a page in a SUBDIRECTORY of `public/` is served on the
            # same origin by the same project, so `glob("*.html")` would leave it silently
            # ungraded — the "a page the guard never discovers" shape. There is no nested
            # HTML today, so this changes no current result; it closes the door on one.
            "app": ([self.WEB / "apps" / "dashboard" / "index.html",
                     *sorted(self.DASHBOARD_ROOT.rglob("*.html"))],
                    self.DASHBOARD_ROOT, self.ICON_ORIGINS),
            "app-admin": ([self.WEB / "apps" / "blog-admin" / "index.html",
                           # The COMMITTED built shell. It is a TRACKED, served artifact and
                           # the repo's source-of-record (#7749: a source change without a
                           # rebuild leaves it behind for a whole release), so grading only
                           # the vite SOURCE let the deployed `/admin` document keep the
                           # pre-fix head — no icon link, hence the UNVERSIONED fallback.
                           self.WEB / "apps" / "blog-admin" / "dist" / "index.html"],
                          self.DASHBOARD_ROOT, self.ICON_ORIGINS),
        }
        # ⛔ `assert pages` ALONE IS NEARLY VACUOUS HERE: two of the three surfaces build
        # their list from LITERALS, so truthiness could only ever fail for `marketing`, and
        # the app surface's `glob("*.html")` half could shrink to nothing without a word.
        # Names are asserted instead — a page that stops being found is the failure this
        # guard exists to notice, and it must not be quieter than a page that breaks.
        expected_pages = {
            "marketing": 14,
            "app": 5,
            # 2, not 1: the vite SOURCE and the committed `dist/` shell are both graded —
            # `dist/index.html` disappearing from this list is exactly the drift #7749
            # describes, and a minimum of 1 would let it happen in silence.
            "app-admin": 2,
        }
        for label, (pages, root, origins) in surfaces.items():
            assert pages, f"no {label} pages found — the guard would pass vacuously"
            assert len(pages) >= expected_pages[label], (
                f"the {label} surface found only {len(pages)} page(s), not the "
                f"{expected_pages[label]} it declares: "
                f"{sorted(p.name for p in pages)} — a page that stopped being discovered "
                "passes every assertion below by not being graded")
            found = self._grade_surface(label, pages, root, origins, expected)
            assert not found, "\n".join(found)

        # ⛔ NO GLOB REACHES THESE — they are STRINGS in TypeScript, not pages on disk, so
        # each is graded through `_surface_icon_violations` on its own text, against the
        # root that actually SERVES it. The blog head is the surface a shared link opens.
        # The other two are on the APP origin and rendered a full `<head>` with NO icon
        # link at all, so both fell back to the UNVERSIONED `/favicon.ico` — the defect
        # this test exists to stop, one file over (review round 2).
        # ⛔ GRADED, NOT SUBSTRING-MATCHED (review round 3). The first cut asserted only
        # `href="/favicon.ico?v=<sha>" in source`. That is rel-BLIND, so `rel="preload"`
        # ("mentions the file and gives the tab no icon at all" — this file's own words),
        # every `rel="icon"` demoted to `apple-touch-icon`, and the whole tag commented
        # out all passed. Reusing the page predicate closes it with no new code: it
        # dispatches the rel by NAME, requires each of the trio, refuses `data:` and the
        # wordmark, resolves the origin, checks the file exists under the serving root, and
        # compares the `?v=` to the digest. Each source gets ITS OWN root because a self-rendered
        # document is served by a project, and asserting existence against the right one
        # is the point.
        # ⚠️ `website/functions/_middleware.ts`'s refusal page is a deliberate EXCEPTION and
        # stays unlinked: its own contract forbids an outbound request while a credential is
        # in the URL — so it is excluded by DECISION, not by oversight.
        # ⛔ EACH SOURCE CARRIES THE URL IT IS SERVED AT (review round 8 — the same defect the
        # round-3 `base` fix closed for disk pages): `_icon_ref` resolves a href the way a
        # BROWSER does, against the page that carries it, so grading these three against
        # `SITE_ROOT` alone read a document-relative `favicon.ico?v=…` in `confirm.ts` as
        # GREEN while the browser requests `/auth/favicon.ico` and 404s. All three sources use
        # root-relative hrefs today (verified), so this changes no current answer.
        self_rendered = {
            "website/functions/blog/_lib.ts": (
                self.WEB / "functions" / "blog" / "_lib.ts", self.WEB, self.SITE_ORIGINS,
                self.SITE_ROOT + "blog/"),
            "website/apps/dashboard/functions/auth/confirm.ts": (
                self.WEB / "apps" / "dashboard" / "functions" / "auth" / "confirm.ts",
                self.DASHBOARD_ROOT, self.ICON_ORIGINS, self.SITE_ROOT + "auth/"),
            "website/apps/dashboard/functions/admin/[[path]].ts": (
                self.WEB / "apps" / "dashboard" / "functions" / "admin" / "[[path]].ts",
                self.DASHBOARD_ROOT, self.ICON_ORIGINS, self.SITE_ROOT + "admin/"),
        }
        for source_label, (source, root, origins, page_url) in self_rendered.items():
            rendered = source.read_text()
            # `self._head_element_count`, not `count("<head")`: a `<header>` element would
            # have counted as a second `<head>` and refused an honest page with a diagnosis
            # ("grade each one, or list the new document") that describes a document that
            # does not exist. The helper's docstring carries the two failure directions it
            # closes; what DRIVES it is `test_a_header_element_is_not_a_second_head_document`.
            heads = self._head_element_count(rendered)
            assert heads == 1, (
                f"{source_label} renders {heads} <head> element(s), but this guard grades ONE "
                "document per source (it slices the first `<head>…</head>`). A SECOND "
                "document added to this file would ship with no icon and no `?v=` while every "
                "assertion here stayed green — grade each one, or list the new document "
                "separately, rather than letting the guard quietly cover the first only")
            violations = self._surface_icon_violations(
                "self-rendered", source_label, rendered, root, expected, origins,
                page_url=page_url)
            assert not violations, "\n".join(violations)

    def _grade_surface(self, label: str, pages, root: Path,
                       origins: frozenset[str], expected: dict[str, str]) -> list[str]:
        """Every violation on one SURFACE, driven through the real page-URL threading.

        Split out of `test_every_surface_declares_the_same_versioned_icon_set` so a test can
        drive a NESTED page through `_page_url` rather than passing `page_url=` itself: with
        the loop inlined, replacing `page_url=self._page_url(page, root)` with `page_url=None`
        — or making `_page_url` return a constant — left every assertion green (review
        round 4). A guard whose threading no test executes cannot be told from a no-op.
        """
        violations: list[str] = []
        for page in pages:
            violations += self._surface_icon_violations(
                label, page.name, page.read_text(), root, expected, origins,
                page_url=self._page_url(page, root))
        return violations

    def _page_url(self, page: Path, root: Path) -> str | None:
        """The URL a page is SERVED at — or `None` when this guard cannot know it.

        The marketing site is served from `website/`, so `website/faq.html` is `/faq.html`.
        (`website/pricing/index.html` would be `/pricing/`, and this resolves it correctly;
        the MARKETING surface does not discover nested pages at all — that is #4111, not a
        base-resolution bug.)

        ⛔ `None` IS NOT THE SITE ROOT (review round 4). A project whose staged root is not
        the page's ancestor — the dashboard's `index.html` above `public/`, the blog-admin
        console served at `/admin`, the `/auth/confirm` Function — cannot have its served
        path derived here, and returning `SITE_ROOT` for those pages resolved a DOCUMENT-
        RELATIVE href against the MARKETING root and graded it green, while the browser
        requests `/admin/favicon.ico` (answered by the admin gate Function) or
        `/auth/favicon.ico` (a 404). Measured: `href="favicon.ico?v=…"` on
        `apps/blog-admin/index.html`, on its committed `dist/index.html`, and in
        `functions/auth/confirm.ts` were all green. `None` makes the collector REFUSE a
        document-relative href instead of guessing, which is the only honest answer.
        """
        try:
            rel = page.parent.relative_to(root)
        except ValueError:
            return None
        if rel == Path("."):
            return self.SITE_ROOT
        return self.SITE_ROOT + rel.as_posix() + "/"

    def _surface_icon_violations(self, label: str, page_name: str, head: str,
                                 root: Path, expected: dict[str, str],
                                 origins: frozenset[str], *,
                                 page_url: str | None = None) -> list[str]:
        """Every way ONE page on ONE origin breaks the icon contract; empty = it passes.

        Split out of the test above so the page-level application is DRIVEN BY AN
        ASSERTION on both trees, the way `_icon_trio_violations` was split out for the
        marketing one.

        ⛔ THE REL IS GRADED HERE, BY NAME, ON BOTH TREES. The first cut folded the app
        pages through `_icon_hrefs` alone — a count, an href set, an existence check and
        a `?v=` compare — none of which reads `rel`. So replacing
        `rel="apple-touch-icon"` with `rel="icon"` on an app page left EVERY assertion
        green while `app.premiselabs.co` stopped declaring the icon iOS saves to the
        home screen (it draws a page screenshot instead). That is the href-only blind
        spot this class already names for the marketing tree — "`<link rel="preload"
        href="/favicon.ico">` mentions the file and gives the tab no icon at all" — one
        origin over, and the app origin is the tree #11407 was REPORTED against. The
        existing trio guard dispatches `apple-touch-icon` by name and checks `sizes`,
        so running it here closes both gaps with no new predicate.
        """
        png = self._png_size((self.WEB / self.ICON_PNG).read_bytes())
        apple = self._png_size((self.WEB / self.ICON_APPLE).read_bytes())
        ico_sizes = {i["declared"][0]
                     for i in self._ico_entries((self.WEB / self.ICON_ICO).read_bytes())}

        violations = self._icon_trio_violations(
            page_name, head, png, ico_sizes, apple, origins=origins)

        # ⛔ NO `len(hrefs) != 3` HERE. It refused an HONEST page that adds a fourth icon
        # link (an SVG variant, a second `sizes=192x192` slot) with a message claiming the
        # trio was missing, which is the opposite of what happened — and it added no
        # detection power over `_icon_trio_violations`, which already reds a page that omits
        # any one of the three. A guard that reds honest artwork is the guard the next person
        # weakens instead of fixes (review round 2, measured on a `/tmp` copy of signup.html).
        hrefs = self._icon_hrefs(head)
        for href in hrefs:
            if href.startswith("data:"):
                violations.append(
                    f"{page_name} blanks its icon with {href!r} — a blank icon is not a "
                    "harmonised icon, it is the browser's generic glyph")
                continue
            # ⛔ RESOLVED THROUGH `_icon_ref`, WHICH IS WHAT MAKES `origins` REAL.
            # `href.split("?", 1)[0]` DROPPED the origin instead of checking it: an
            # absolute `https://app.premiselabs.co/favicon.ico?v=…` — the exact form
            # `ICON_ORIGINS`'s own comment says the app pages may use — kept its scheme
            # and host, matched no key in `expected`, and reded a CORRECT page with "is
            # not one of the three icon files" (the file it names IS one of the three).
            # That false red was the only place `origins` could have changed an answer,
            # so before this the parameter was unobservable too: every graded page uses
            # a root-relative href, which `_icon_ref` resolves through `SITE_ORIGINS`
            # whether or not `origins` is threaded — replacing
            # `ICON_ORIGINS` with `SITE_ORIGINS`, or deleting `origins` outright, left
            # this whole file green. Resolving here grades the origin that actually
            # serves the file: another host yields "" (so a protocol-relative
            # `//evil.example/favicon.ico` can no longer pass as `/favicon.ico`), and
            # this surface's own origin is accepted in both the absolute and
            # root-relative spellings. `test_the_app_tree_accepts_an_absolute_href_on_
            # its_own_origin` drives all four directions through a real app page.
            # ⛔ A DOCUMENT-RELATIVE HREF ON A PAGE WHOSE SERVED URL IS UNKNOWN IS
            # UNGRADEABLE, NOT RESOLVABLE (review round 4). Guessing `SITE_ROOT` graded
            # `/admin/favicon.ico` and `/auth/favicon.ico` green while the browser asked the
            # admin gate Function and a 404 respectively. Say so instead.
            if page_url is None and not href.startswith("/") and "://" not in href:
                violations.append(
                    f"{page_name} declares the document-relative icon href {href!r}, but this "
                    "surface's served URL cannot be derived from the tree — use a "
                    "root-relative href (`/favicon.png?v=…`), which is what every page here "
                    "already does, or the tab request goes to a path that serves something "
                    "else (or nothing)")
                continue
            resolved = self._icon_ref(
                href, origins=origins, base=page_url or self.SITE_ROOT)
            if not resolved:
                violations.append(
                    f"{page_name} declares {href!r}, which resolves to no icon file on "
                    f"this surface's origins {sorted(origins)} — the browser fetches "
                    "that tab icon from another host (or the href is not a URL), so the "
                    "mark on the tab is not ours")
                continue
            if "/logo.png" in resolved:
                violations.append(
                    f"{page_name} uses the wordmark as a tab icon ({href!r}) — 400x244 "
                    "squashed into a square slot (#11407)")
                continue
            # ⛔ A FOURTH icon link is ALLOWED. An honest page may add an SVG variant or a
            # second `sizes=` slot, and refusing it — with a message claiming the trio was
            # MISSING, which is the opposite of what happened — was the false red the
            # count's removal was meant to end. Dropping the count alone did not end it:
            # the membership rejection below the wordmark check still refused every extra,
            # which is why the comment said "a fourth is GREEN" and the code disagreed.
            # `_icon_trio_violations` above already reds a page that OMITS any one of the
            # three, and that is the property the contract needs; an extra adds a mark, it
            # does not remove one. What an extra IS still held to is the two rules that keep
            # a tab correct: the file must EXIST under this surface's own root (or the tab
            # request 404s), and it must be versioned with ITS OWN digest (or a re-export
            # keeps the old artwork drawing — the staleness bug this file exists for).
            local = root / resolved.lstrip("/")
            if not local.is_file():
                violations.append(
                    f"{page_name} links {href!r}, which does not exist under "
                    f"{root.name}/ — the tab request 404s")
                continue
            version = self._href_version(href)
            want = expected.get(resolved)
            if want is None:
                # A non-trio icon: its digest comes from its OWN bytes, so the same
                # self-invalidating rule applies to artwork the contract did not name.
                want = hashlib.sha256(local.read_bytes()).hexdigest()[:12]
            if version is None:
                violations.append(
                    f"{page_name} links {href!r} with NO `?v=` — declare "
                    f"`?v={want}` (this file's sha256 prefix). Without it a "
                    "browser or edge holding the old icon keeps drawing it after a "
                    "re-export, which is the stale-icon bug this contract exists for")
            elif version != want:
                violations.append(
                    f"{page_name} links {href!r} but the file's sha256 starts "
                    f"{want!r} — bump the `?v=` with the bytes, or a browser "
                    "holding the old icon keeps drawing it (that is the stale-icon bug)")

        # ⛔ AN EXTRA MAY NOT RE-DECLARE A TRIO FILE UNDER THE OTHER TRIO RELATION.
        # Allowing extras is right (round 4: refusing an honest SVG variant / second
        # `sizes=` was a false red), but `_icon_hrefs` above STRIPS the rel — it returns a
        # flat list of hrefs — so that loop cannot tell WHICH relation an extra carries.
        # Measured, before this rule: adding
        #   `<link rel="apple-touch-icon" href="/favicon.png?v=<its own digest>">`
        # to an otherwise-correct page PASSED every assertion — the file exists, it is one
        # of the trio, and its `?v=` is its real digest. But iOS resolves competing
        # `apple-touch-icon` links to ONE of them and this site declares no `sizes=` on
        # that link, so the saved home-screen mark could become the ROUNDED, TRANSPARENT
        # 256 px `favicon.png` — the double-rounding §6.7 says the opaque 180 px asset
        # exists to prevent. `rel="icon"` naming `apple-touch-icon.png` is the same hole
        # and is refused for the same reason: a relation names an ASSET here, not a size
        # preference, so a cross-relation extra silently swaps a mark instead of adding
        # one. Non-trio extras (an SVG variant, a 192 px slot, a repeat of the same file
        # under the same rel) are untouched, so round 4's false red does not come back.
        # Residual, stated rather than implied: a `sizes=` declared on a non-trio PNG/ICO
        # extra is not compared with that file's real pixels — closing that needs a byte
        # probe per extra, and no extra exists today.
        apple_asset = f"/{self.ICON_APPLE}"
        for link in self._links(self._head(head)):
            tokens = link.get("rel", "").lower().split()
            if not any(token in self.ICON_RELS for token in tokens):
                continue
            href = link.get("href", "")
            # Same base as the href loop above: resolving this one against the site root
            # false-RED a nested page whose LOCAL `apple-touch-icon.png` was named under
            # `rel="icon"` — a cross-relation swap that had not happened (review round 4).
            resolved = self._icon_ref(
                href, origins=origins, base=page_url or self.SITE_ROOT)
            if not resolved:
                continue  # data:/foreign/not-a-URL is already reported above
            # ⛔ THE ASSET IS NAMED BY ITS FILE, NOT BY ITS ROOT PATH (review round 4). A
            # nested page's own `apple-touch-icon.png` resolves to `/settings/…`, which is
            # not `/apple-touch-icon.png`, so a path comparison false-RED a page that had
            # swapped nothing. The relation names an ASSET, so the file name is the test.
            if "apple-touch-icon" in tokens and Path(resolved).name != self.ICON_APPLE:
                violations.append(
                    f"{page_name} declares {href!r} under rel=\"apple-touch-icon\", but "
                    f"that relation names {apple_asset} — iOS keys the saved home-screen "
                    "mark off this relation and resolves competing links to one, so a "
                    "cross-relation extra can replace the opaque 180 px tile with a "
                    "rounded/transparent one (#11407)")
            elif "icon" in tokens and Path(resolved).name == self.ICON_APPLE:
                violations.append(
                    f"{page_name} declares {href!r} under rel=\"icon\", but that is the "
                    "apple-touch-icon asset — a relation names an ASSET here, so a "
                    "cross-relation extra swaps a mark rather than adding one (#11407)")
        return [f"{label}: {v}" for v in violations]

    def test_a_document_relative_icon_href_needs_a_known_page_url(self, tmp_path):
        """A page whose served URL cannot be derived must REFUSE a relative href.

        Three real surfaces are in that position — the blog-admin console (served at `/admin`
        and `/admin/`, and `website/apps/blog-admin/vite.config.ts` documents the RFC 3986
        merge), the dashboard's `index.html` (above `public/`), and the `/auth/confirm`
        Function (rendered at `/auth/confirm`). For all three the guard cannot know the base,
        and returning the SITE ROOT for them resolved `favicon.ico?v=…` against the MARKETING
        root and graded it green while the browser requested `/admin/favicon.ico` (answered by
        the admin gate Function) or `/auth/favicon.ico` (a 404).

        This also pins the THREADING: `_page_url` is exercised here (not bypassed with an
        explicit `page_url=`), and `_grade_surface` is the same path the real surface loop
        takes — so making `_page_url` return a constant, or dropping the argument in the loop,
        reds here.
        """
        expected = {f"/{name}": hashlib.sha256((self.WEB / name).read_bytes()).hexdigest()[:12]
                    for name in self.ICON_FILES}
        for name in self.ICON_FILES:
            (tmp_path / name).write_bytes((self.WEB / name).read_bytes())

        # ---- `_page_url` itself, both branches -------------------------------------
        assert self._page_url(self.WEB / "faq.html", self.WEB) == self.SITE_ROOT
        assert self._page_url(self.WEB / "pricing" / "index.html", self.WEB) == (
            self.SITE_ROOT + "pricing/")
        assert self._page_url(
            self.WEB / "apps" / "dashboard" / "index.html", tmp_path) is None, (
            "a page whose staged root is NOT its ancestor has no derivable served URL — "
            "returning the site root here is what graded /admin and /auth/favicon.ico green")

        # ---- a nested page, through the REAL surface loop --------------------------
        nested = tmp_path / "settings"
        nested.mkdir()
        for name in self.ICON_FILES:
            (nested / name).write_bytes((self.WEB / name).read_bytes())
        relative = (self.WEB / "apps" / "dashboard" / "public" / "signup.html").read_text()
        for name, digest in expected.items():
            relative = relative.replace(f'href="{name}?v={digest}"', f'href="{name.lstrip("/")}?v={digest}"')
        nested_page = nested / "index.html"
        nested_page.write_text(relative)

        assert self._grade_surface(
            "sandbox", [nested_page], tmp_path, self.ICON_ORIGINS, expected) == [], (
            "a nested page's document-relative hrefs resolve against ITS OWN directory, where "
            "the same three files are present — this is the case that proves the threading: "
            "with `page_url=None` (or a constant base) it resolves against the site root")

        # ---- and a relative href with NO derivable base is reported ---------------
        orphan = tmp_path / "orphan"
        orphan.mkdir()
        (orphan / "index.html").write_text(relative)
        found = self._grade_surface(
            "sandbox", [orphan / "index.html"], nested, self.ICON_ORIGINS, expected)
        assert found, (
            "a document-relative href on a page whose base cannot be derived must be REFUSED, "
            "not silently resolved against the site root")
        assert "document-relative icon href" in found[0], found[0]

        # ---- and a root-relative href on the same kind of page is still fine ------
        rooted = relative
        for name, digest in expected.items():
            rooted = rooted.replace(f'href="{name.lstrip("/")}?v={digest}"', f'href="{name}?v={digest}"')
        (orphan / "index.html").write_text(rooted)
        assert self._grade_surface(
            "sandbox", [orphan / "index.html"], nested, self.ICON_ORIGINS, expected) == [], (
            "an absolute (root-relative) href needs no base, so the fallback must not refuse "
            "it — that would be a false red on every real page")

    def test_the_app_tree_grades_the_icon_rel_by_name(self):
        """NON-VACUITY for the app-tree half: a swapped `rel` must red, both directions.

        Measured on the code before this: swapping `rel="apple-touch-icon"` for
        `rel="icon"` on `website/apps/dashboard/public/signup.html` left every assertion
        in `test_every_surface_declares_the_same_versioned_icon_set` green — 3 hrefs, all
        three in `expected`, all three files present, all three `?v=` matching. The
        mutated page is CORRECT by every count and still loses the iOS home-screen mark,
        which is why the assertion has to reach the relation and not the href.
        """
        expected = {f"/{name}": hashlib.sha256((self.WEB / name).read_bytes()).hexdigest()[:12]
                    for name in self.ICON_FILES}
        page = self.DASHBOARD_ROOT / "signup.html"
        head = page.read_text()

        def grade(html: str) -> list[str]:
            return self._surface_icon_violations(
                "app", page.name, html, self.DASHBOARD_ROOT, expected, self.ICON_ORIGINS)

        assert grade(head) == [], (
            "the real app page must pass, or the mutants below prove nothing:\n"
            + "\n".join(grade(head)))

        # Direction 1 — the touch icon declared as a tab icon (the review's surviving mutant).
        demoted = head.replace('rel="apple-touch-icon"', 'rel="icon"', 1)
        assert demoted != head, "fixture drift: signup.html no longer declares the apple rel"
        caught = grade(demoted)
        assert any(self.ICON_APPLE in v and "apple-touch-icon" in v for v in caught), (
            "a page that declares /apple-touch-icon.png under rel=\"icon\" loses the iOS "
            "home-screen mark and every count/href/version assertion still passes — the "
            f"rel must be graded. Got: {caught}")

        # Direction 2 — every tab icon declared as a touch icon: count is still 3.
        promoted = head.replace('rel="icon"', 'rel="apple-touch-icon"')
        caught = grade(promoted)
        assert any(self.ICON_ICO in v and "icon" in v for v in caught), (
            f"a page whose tab icons all claim apple-touch-icon must red. Got: {caught}")

        # Direction 3 — a legitimate `mask-icon` is a DIFFERENT relation: it must not be
        # counted as a fourth tab icon (the false red the substring filter produced).
        png_version = expected[f"/{self.ICON_PNG}"]
        mask_link = (f'  <link rel="mask-icon" href="/{self.ICON_PNG}?v={png_version}" '
                     'color="#000000">\n')
        masked = head.replace("</head>", mask_link + "</head>", 1)
        assert masked != head
        assert grade(masked) == [], (
            "a `mask-icon` is not one of the trio and must not be graded as one; the "
            "substring filter refused this correct page:\n" + "\n".join(grade(masked)))

        # The blog's head is a STRING in the SSR function, not a page on disk, so no glob
        # reaches it — it is graded in `test_every_surface_declares_the_same_versioned_icon_set`
        # alongside the other two self-rendered documents, where the whole surface is graded.

    def test_an_extra_icon_link_is_allowed_and_a_stale_one_is_not(self, tmp_path):
        """NON-VACUITY for the fourth-link arm — the OTHER half of round 2's P2.

        Round 2 removed `len(hrefs) != 3`, which refused an honest fourth link. Round 3
        measured that the arm behind it — "is not one of the three icon files" — still
        refused BOTH forms the removal's own comment called honest: an SVG variant and a
        second `sizes=` tile. That is the difference between deleting a count and closing
        the case, and this test is what makes the difference observable.

        Graded in a sandbox root so nothing here can be mistaken for a claim about the real
        tree: the trio is copied in byte-for-byte (so `expected` still matches), an extra
        192px-style tile and an SVG are created, and the four mutants below are driven
        through the same predicate the guard uses on real pages.
        """
        expected = {f"/{name}": hashlib.sha256((self.WEB / name).read_bytes()).hexdigest()[:12]
                    for name in self.ICON_FILES}
        for name in self.ICON_FILES:
            (tmp_path / name).write_bytes((self.WEB / name).read_bytes())
        # a second raster tile and an SVG variant: neither is one of the trio's names
        (tmp_path / "icon-256.png").write_bytes((self.WEB / self.ICON_PNG).read_bytes())
        (tmp_path / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
        tile_v = hashlib.sha256((tmp_path / "icon-256.png").read_bytes()).hexdigest()[:12]
        svg_v = hashlib.sha256((tmp_path / "favicon.svg").read_bytes()).hexdigest()[:12]
        page = (self.WEB / "apps" / "dashboard" / "public" / "signup.html").read_text()

        def grade(html: str, *, page_url: str | None = None) -> list[str]:
            return self._surface_icon_violations(
                "sandbox", "signup.html", html, tmp_path, expected, self.ICON_ORIGINS,
                page_url=page_url)

        assert grade(page) == [], (
            "the unmutated page must grade clean against a byte-identical sandbox root:\n"
            + "\n".join(grade(page)))

        def with_extra(href: str) -> str:
            return page.replace(
                "</head>", f'  <link rel="icon" href="{href}">\n</head>', 1)

        # THE HONEST CASES — each was RED before this contract allowed an extra at all.
        for href, label in (
            (f"/icon-256.png?v={tile_v}", "a second raster tile, versioned with its own bytes"),
            (f"/favicon.svg?v={svg_v}", "an SVG variant, versioned with its own bytes"),
        ):
            assert grade(with_extra(href)) == [], (
                f"{label} ({href}) is an honest extra icon and must not be refused:\n"
                + "\n".join(grade(with_extra(href))))

        # THE FAILURE PATH — an extra is ALLOWED, not EXEMPT: it is still an icon on a tab,
        # so the two rules that keep a tab correct bind it exactly as they bind the trio.
        unversioned = with_extra("/favicon.svg")
        red = grade(unversioned)
        assert red, "an extra icon with no version must red — it goes stale the same way"
        assert "with NO `?v=`" in red[0], red[0]

        stale = with_extra("/icon-256.png?v=ffffffffffff")
        red = grade(stale)
        assert red, "an extra icon with a stale `?v=` must red"
        assert "bump the `?v=` with the bytes" in red[0] or "bump its `?v=`" in red[0], red[0]

        missing = with_extra("/icon-512.png?v=ffffffffffff")
        red = grade(missing)
        assert red, "an extra icon pointing at a file that does not exist must red"
        assert "does not exist under" in red[0], red[0]

        # AND THE DOCUMENT-RELATIVE ARM (`page_url`): a page in a SUBDIRECTORY resolves a
        # bare `favicon.ico` against its OWN directory, which is where the browser asks.
        nested = page.replace('href="/favicon.ico?v=' + expected["/favicon.ico"] + '"',
                              'href="favicon.ico?v=' + expected["/favicon.ico"] + '"', 1)
        assert nested != page
        assert grade(nested, page_url=self.SITE_ROOT + "settings/"), (
            "a document-relative href on a page served from /settings/ requests "
            "/settings/favicon.ico, which does not exist — resolving it against the SITE "
            "ROOT reads green for a tab request that 404s")
        (tmp_path / "settings").mkdir()
        for name in self.ICON_FILES:
            (tmp_path / "settings" / name).write_bytes((self.WEB / name).read_bytes())
        assert grade(nested, page_url=self.SITE_ROOT + "settings/") == [], (
            "with the files present in the page's own directory the same href is correct")

    def test_the_app_tree_accepts_an_absolute_href_on_its_own_origin(self):
        """NON-VACUITY for `origins` — it must CHANGE AN ANSWER, in both directions.

        `ICON_ORIGINS` exists for one documented reason (its own comment): the app surface
        is a separate Pages deployment, so its pages "may name
        `https://app.premiselabs.co/...` absolutely". On the code before this test the
        href loop compared `href.split("?", 1)[0]` against `expected`, so that blessed
        form kept its scheme and host, matched no key, and a page correct by every count,
        file and version was reported as linking "which is not one of the three icon
        files" — a FALSE RED on a correct page, with a message that misnames the cause
        (the file IS one of the three).

        The same read made `origins` UNOBSERVABLE: all 21 graded pages use root-relative
        hrefs, which `_icon_ref` resolves through `SITE_ORIGINS` whether `origins` is
        passed or not, so replacing `ICON_ORIGINS` with `SITE_ORIGINS` — or deleting the
        parameter and its four call sites — left every assertion green. A check whose
        failure path cannot run is indistinguishable from a no-op, which is the case this
        file's own `_missing_icon_links` docstring says must not ship.

        All four directions are driven through a REAL app page, not a fixture, so this
        cannot drift into testing a string nobody deploys.
        """
        expected = {f"/{name}": hashlib.sha256((self.WEB / name).read_bytes()).hexdigest()[:12]
                    for name in self.ICON_FILES}
        page = self.DASHBOARD_ROOT / "signup.html"
        head = page.read_text()

        def grade(html: str, origins: frozenset[str]) -> list[str]:
            return self._surface_icon_violations(
                "app", page.name, html, self.DASHBOARD_ROOT, expected, origins)

        absolute = head
        for path, version in expected.items():
            absolute = absolute.replace(
                f'href="{path}?v={version}"',
                f'href="https://app.premiselabs.co{path}?v={version}"')
        for path, version in expected.items():
            assert f'href="https://app.premiselabs.co{path}?v={version}"' in absolute, (
                "fixture drift: signup.html no longer declares the versioned "
                f"{path} href, so the absolute form below tests fewer links than the "
                "page has")

        # Direction 1 — the form the comment blesses must PASS. This is the false red.
        assert grade(absolute, self.ICON_ORIGINS) == [], (
            "a page may name its own origin's icons ABSOLUTELY (`ICON_ORIGINS`'s comment "
            "says so), and grading it root-relative-only reds a correct page:\n"
            + "\n".join(grade(absolute, self.ICON_ORIGINS)))

        # Direction 2 — `origins` is OBSERVABLE: the app origin is deliberately not in
        # `SITE_ORIGINS`, so the same page graded against the marketing set must red. A
        # mutant that passes `SITE_ORIGINS` (or drops the parameter) fails here.
        assert grade(absolute, self.SITE_ORIGINS), (
            "grading the app surface against `SITE_ORIGINS` must red an absolute "
            "app-origin href — otherwise `ICON_ORIGINS` changes no answer and the "
            "parameter is dead weight")

        # Direction 3 — the protocol-relative spelling is the same origin, not another.
        protocol_relative = head
        for path, version in expected.items():
            protocol_relative = protocol_relative.replace(
                f'href="{path}?v={version}"',
                f'href="//app.premiselabs.co{path}?v={version}"')
        assert protocol_relative.count('href="//app.premiselabs.co') == 3
        assert grade(protocol_relative, self.ICON_ORIGINS) == [], (
            "`//app.premiselabs.co/favicon.ico?...` is this surface's own origin and must "
            "pass:\n" + "\n".join(grade(protocol_relative, self.ICON_ORIGINS)))

        # Direction 4 — the widening is by ORIGIN, not "any host". A foreign host still
        # reds, both as a missing declaration and as an href that names no file of ours.
        foreign = absolute.replace("https://app.premiselabs.co", "https://evil.example")
        caught = grade(foreign, self.ICON_ORIGINS)
        assert len([v for v in caught if "evil.example" in v]) == 3, (
            "every one of the three foreign hrefs must be reported, not just the first — "
            "an icon served from another host is not ours and one defect must not hide "
            f"the rest. Got: {caught}")

    def test_a_header_element_is_not_a_second_head_document(self):
        """NON-VACUITY for `_head_element_count`: `<header>` is not a second `<head>`.

        The count exists to fail closed when one file renders TWO documents — the guard
        slices the first `<head>…</head>`, so a second document would ship with no icon
        while every assertion stayed green. The first cut was `rendered.count("<head")`,
        and `<header>` begins with that substring: an honest page that gained a `<header>`
        (a nav bar — nothing to do with `<head>`) was refused with "a SECOND document …
        grade each one", a diagnosis that describes a document nobody wrote. That is a
        FALSE RED, i.e. a guard the next person weakens instead of fixes.

        Both directions, on the three REAL self-rendered sources (none of which carries a
        `<header>` today, so the fixture is the only thing that exercises it):
        """
        sources = {
            "website/functions/blog/_lib.ts":
                self.WEB / "functions" / "blog" / "_lib.ts",
            "website/apps/dashboard/functions/auth/confirm.ts":
                self.DASHBOARD_ROOT.parent / "functions" / "auth" / "confirm.ts",
            "website/apps/dashboard/functions/admin/[[path]].ts":
                self.DASHBOARD_ROOT.parent / "functions" / "admin" / "[[path]].ts",
        }
        for label, source in sources.items():
            # ⛔ `is_file()` BEFORE `read_text()`: read-first raised `FileNotFoundError` on a
            # renamed fixture, so the drift diagnosis below was unreachable in every case it
            # named — "a check whose failure path never runs cannot be told apart from a
            # no-op", which is this file's own rule (review round 8, F3).
            assert source.is_file(), f"fixture drift: {label} is gone"
            text = source.read_text()
            assert self._head_element_count(text) == 1, (
                f"{label} renders {self._head_element_count(text)} <head> element(s); this "
                "test assumes exactly one, or the directions below grade nothing")
            assert "<header" not in text, (
                f"fixture drift: {label} already has a <header>, so the mutation below is "
                "not the only one and cannot be attributed")

            # Direction 1 — the FALSE RED. A `<header>` element must not be counted.
            with_header = text.replace("</head>", "</head>\n<header>site</header>", 1)
            assert with_header != text, f"fixture drift: {label} no longer closes </head>"
            assert self._head_element_count(with_header) == 1, (
                f"{label} gained a <header> (a nav bar — not a head) and the count says it "
                "renders a second DOCUMENT, refusing an honest page: "
                f"count={self._head_element_count(with_header)}, "
                "substring hits=" + repr(with_header.count("<head")))

            # Direction 2 — a genuine SECOND document is still counted, so the loosening is
            # by tag boundary and not "stop counting": this is the red the guard exists for.
            assert self._head_element_count(text + "<head>") == 2, (
                f"{label} plus a real second <head> must count 2 — otherwise the guard "
                "covers the first document and reports green for both")

            # Direction 3 — EVERY opening spelling of the ONE element still counts exactly
            # one, in any case: `<head>`, `<head `, `<head\t>`, `<head\n`, `<head/>` are the
            # same element, and an UPPERCASE name is not a different one (HTML lowercases
            # tag names; the case-sensitive boundary counted `<HEAD>` as 0 and so let a real
            # second document through).
            for spelling in ("<head>", "<head >", "<head\t>", "<head\n>", "<head/>",
                             "<HEAD>", "<Head>"):
                assert self._head_element_count(spelling) == 1, (
                    f"{spelling!r} must count as one <head> element")

            # Direction 4 — a `<head` inside a STRING BUILDER is not an element either.
            # These three sources assemble HTML with `.replace()`/concatenation, so an honest
            # edit like the one below counted 2 and refused the page with "a SECOND
            # document" that nobody wrote.
            builder = 'x = html.replace("<head", "<head>")'
            assert self._head_element_count(builder) == 1, (
                f"a `\"<head\"` inside a string builder is not an element: {builder!r} "
                f"counted {self._head_element_count(builder)}")
            assert self._head_element_count("<head-x>") == 0, (
                "a tag name that merely STARTS with `head` is not <head> ")


# ═══════════════════════════════════════════════════════════════════════════
# #11764 — the product set is SERVED from the one marketing host
# ═══════════════════════════════════════════════════════════════════════════


class TestProductSetIsServedUnderTheOneHost:
    """Every marketing page names the URL that actually serves it (#11764, epic #11762).

    WHY THIS EXISTS. Epic #11762 moved the product surface to `premiselabs.co/tortoise`
    and turned `tortoise.premiselabs.co` into a permanent 301. The pages were not moved
    on disk (the middleware rewrites the prefix), so NOTHING about the files changed —
    which is exactly why the head could keep naming the retired host and every existing
    guard stayed green. A canonical naming a URL that 301s to the document serving it is
    a hard SEO error, and it is invisible to any check that only reads the card file.

    THE ASSERTION IS A DESTINATION, NOT A COUNT. The count of retired-host occurrences is
    a number that both wrong shapes satisfy: `== 0` reds the day a legitimate asset URL
    names it, and `<= N` stops noticing when a link is dropped. So each page is asked
    the only question that cannot be gamed — "does your canonical name the URL that
    serves YOU?" — and the retired host is refused where it would actually be wrong.
    """

    WEB = REPO_ROOT / "website"
    CONTRACT = TestSiteIconAndSocialPreviewContract
    ONE_HOST = "https://premiselabs.co"

    # page -> the URL that serves it after #11762. The product set sits UNDER the
    # prefix; the business's legal pages stay at the ROOT (the owner's fork in the
    # epic: legal governs the business, not the product).
    CANONICAL: ClassVar[dict[str, str]] = {
        "product.html": f"{ONE_HOST}/tortoise",
        "docs.html": f"{ONE_HOST}/tortoise/docs",
        "faq.html": f"{ONE_HOST}/tortoise/faq",
        "self-hosted.html": f"{ONE_HOST}/tortoise/self-hosted",
        "security.html": f"{ONE_HOST}/tortoise/security",
        "license.html": f"{ONE_HOST}/tortoise/license",
        "investors.html": f"{ONE_HOST}/tortoise/investors",
        "design-partners-intro.html": f"{ONE_HOST}/tortoise/design-partners-intro",
        "privacy.html": f"{ONE_HOST}/privacy",
        "tos.html": f"{ONE_HOST}/tos",
        "dpa.html": f"{ONE_HOST}/dpa",
        "index.html": f"{ONE_HOST}/",
        "contact.html": f"{ONE_HOST}/contact",
    }

    # The pages that MOVED region: they are served under the prefix, so a root-relative
    # link to a sibling's old path is a hop to a 301 (or, for `/`, the COMPANY homepage
    # — a different document entirely).
    MOVED = ("product.html", "docs.html", "faq.html", "self-hosted.html",
             "security.html", "license.html", "investors.html",
             "design-partners-intro.html")

    # The pages that STAYED at the root (`index.html`, `contact.html`). Their `/` link is
    # CORRECT — it is the company homepage — so the prefix rule does not apply to them,
    # but a root-relative link to a product path does not become correct by staying put:
    # `/docs` on `premiselabs.co/contact` is the retired path and now takes a hop. Leaving
    # these out of the scan is what let `contact.html` keep `href="/self-hosted"` while
    # every sibling it sits beside was rewritten (caught by the VGATE review, #11764).
    # Every page the company host serves at `/` + its own path. The legal three belong
    # here: they stayed at the root, so `/` on them is the company homepage (not the
    # product) and their links must use the prefix. A reviewer measured that they were in
    # NEITHER link population, so a future `href="/"` labelled "Back to product" on
    # `privacy.html` would have shipped green — the same miss measured on `contact.html`.
    ROOT_SERVED = ("index.html", "contact.html", "privacy.html", "tos.html", "dpa.html")
    # The app origin's page population, pinned for the same reason `_pages` is: the guard
    # below walks a non-recursive glob, so a page added to a subdirectory (or served from
    # `apps/dashboard/index.html`, which lives OUTSIDE `public/`) would leave coverage with
    # no red anywhere.
    APP_ORIGIN_PAGES = ("404.html", "invite-accept.html", "signup.html", "welcome.html")
    # The RETIRED path -> the path that serves it now. BOTH spellings are keys: the
    # middleware's `PRODUCT_PAGES` carries `/docs` AND `/docs.html`, so the `.html` form
    # is routed in one hop rather than 404ing — and a guard that only knew the
    # extensionless spelling would stay green on a link that still costs the visitor a
    # redirect, which is the property these guards advertise.
    OLD_PRODUCT_PATHS: ClassVar[dict[str, str]] = {
        "/product": "/tortoise", "/product.html": "/tortoise",
        "/docs": "/tortoise/docs", "/docs.html": "/tortoise/docs",
        "/faq": "/tortoise/faq", "/faq.html": "/tortoise/faq",
        "/self-hosted": "/tortoise/self-hosted", "/self-hosted.html": "/tortoise/self-hosted",
        "/security": "/tortoise/security", "/security.html": "/tortoise/security",
        "/license": "/tortoise/license", "/license.html": "/tortoise/license",
        "/investors": "/tortoise/investors", "/investors.html": "/tortoise/investors",
        "/design-partners-intro": "/tortoise/design-partners-intro",
        "/design-partners-intro.html": "/tortoise/design-partners-intro",
    }

    def _canonical(self, name: str) -> str | None:
        head = self.CONTRACT._head((self.WEB / name).read_text())
        for link in self.CONTRACT._links(head):
            if "canonical" in link.get("rel", "").lower().split():
                return link.get("href")
        return None

    def test_every_page_canonical_names_the_url_that_serves_it(self):
        """The destination, per page — a canonical must not 301 to its own document."""
        for name, expected in self.CANONICAL.items():
            assert self._canonical(name) == expected, (
                f"{name} declares canonical {self._canonical(name)!r}; it is served at "
                f"{expected!r}. A canonical naming a URL that 301s back to this document "
                "is the defect #11762 exists to remove")

    def test_404_declares_no_canonical(self):
        """An error page has no canonical URL — one would claim a document that isn't there."""
        assert self._canonical("404.html") is None, (
            "404.html declares a canonical; it is served on every host for every missing "
            "path, so any value would be a lie")

    def test_no_marketing_page_names_the_retired_product_host(self):
        """⛔ THE PROPERTY THE MOVE ACTUALLY CREATES: one host, no page naming the other.

        `tortoise.premiselabs.co` stays alive as a permanent 301 (the hostname is never
        deleted), so a page CAN name it and the link still works — through an extra hop,
        onto a canonical that disagrees with the URL the visitor is on. That is why this
        is asserted on the page text rather than left to a membership set: `SITE_ORIGINS`
        has to keep describing the ASSET tree, which really is served from both.
        """
        retired = TestSiteIconAndSocialPreviewContract.RETIRED_PRODUCT_HOST
        offenders = []
        for page in sorted(self.WEB.glob("*.html")):
            for lineno, line in enumerate(page.read_text().splitlines(), 1):
                if retired in line:
                    offenders.append(f"{page.name}:{lineno}: {line.strip()[:100]}")
        assert not offenders, (
            f"these pages still name the retired product host {retired!r}:\n  "
            + "\n  ".join(offenders)
            + "\n\nEvery one of them is a hop onto a 301, or a label that is now false. "
              "Point it at the URL that serves it (product set: /tortoise/…, legal: the root)")

    def test_a_moved_page_links_its_siblings_by_their_new_path(self):
        """A root-relative href on a moved page means the RETIRED path, not the sibling.

        This is the half a URL-rewriting sweep misses: the absolute links are obvious,
        but `href="/docs"` was correct only because the page was served FROM the product
        host's root. On `premiselabs.co/tortoise/faq` the same href resolves to
        `premiselabs.co/docs` (a 301), and `href="/"` resolves to the COMPANY homepage —
        a different document. Nothing about the file changed when the URL did.
        """
        old_paths = {"/": "/tortoise", **self.OLD_PRODUCT_PATHS}
        offenders = []
        for name in self.MOVED:
            text = (self.WEB / name).read_text()
            for href in re.findall(r'href="([^"]+)"', text):
                path = href.split("#", 1)[0].split("?", 1)[0]
                if path in old_paths:
                    offenders.append(f"{name}: href={href!r} → should be "
                                     f"{old_paths[path]!r}")
        assert not offenders, (
            "these links on moved pages still point at the RETIRED path — from under "
            "/tortoise they reach a different document (`/` is the company page) or take "
            "a hop:\n  " + "\n  ".join(offenders))

    def test_no_page_links_a_product_path_from_the_root(self):
        """A root-served page (`index`, `contact`) must use the prefix too.

        Its `/` link is right on `index.html` — that IS the company homepage — and wrong on
        `contact.html`, where the label says "product". The two cases look alike in a URL
        sweep and only one of them moved, which is exactly how `contact.html` kept
        `href="/self-hosted"` through a migration that rewrote every sibling beside it, and
        kept two `href="/"` links labelled "product" after the product left the root.
        """
        offenders = []
        for name in self.ROOT_SERVED:
            text = (self.WEB / name).read_text()
            for href in re.findall(r'href="([^"]+)"', text):
                path = href.split("#", 1)[0].split("?", 1)[0]
                if path in self.OLD_PRODUCT_PATHS:
                    offenders.append(f"{name}: href={href!r} → should be "
                                     f"{self.OLD_PRODUCT_PATHS[path]!r}")
                elif path == "/" and name != "index.html":
                    # `/` is the COMPANY page on this host. On `index.html` that is the
                    # document itself; on any other root-served page a `/` link labelled
                    # "product" sends the visitor to the company homepage.
                    offenders.append(f"{name}: href={href!r} → `/` is the company page "
                                     "here; the product is '/tortoise'")
        assert not offenders, (
            "these links on a ROOT-served page still name a pre-migration product URL "
            "(one hop onto a 301, or onto a different document entirely):\n  "
            + "\n  ".join(offenders))

    def test_an_app_origin_page_never_links_a_marketing_path_root_relative(self):
        """On `app.premiselabs.co`, `/docs` is not the docs page — it is a 404.

        ⛔ MEASURED, not assumed: the app is its OWN Pages project (`tortoise-dashboard`),
        so a root-relative href there resolves inside it, and `/docs`, `/faq`, `/license`,
        `/security`, `/privacy`, `/tos`, `/dpa`, `/blog` all answered **404** (measured
        `curl` before this change). The marketing pages therefore have to be named
        ABSOLUTELY on these four files, and `SITE_ROOT`'s own tree cannot catch it —
        nothing under `website/*.html` links into the app origin.

        `/` is deliberately NOT a marketing path here: `https://app.premiselabs.co/` serves
        the dashboard (200), so a logo pointing at `/` is correct on this origin.
        """
        marketing = {"/docs", "/faq", "/self-hosted", "/security", "/license",
                     "/privacy", "/tos", "/dpa", "/blog", "/product", "/contact",
                     "/investors", "/design-partners-intro"}
        app_pages = sorted(self.CONTRACT.DASHBOARD_ROOT.glob("*.html"))
        assert app_pages, "no app-origin pages found — the guard would pass vacuously"
        assert tuple(p.name for p in app_pages) == self.APP_ORIGIN_PAGES, (
            "the app-origin page population changed — a page that MOVED into a "
            f"subdirectory leaves this guard: {[p.name for p in app_pages]}")
        offenders = []
        for page in app_pages:
            for href in re.findall(r'href="([^"]+)"', page.read_text()):
                path = href.split("#", 1)[0].split("?", 1)[0]
                # The prefix IS a marketing URL on this origin, and so is the `.html`
                # spelling of any marketing path: `app.premiselabs.co/tortoise/docs`
                # answered 404 when measured, exactly like `/docs` did.
                under_prefix = path == "/tortoise" or path.startswith("/tortoise/")
                if path in marketing or under_prefix or path.removesuffix(".html") in marketing:
                    offenders.append(f"{page.name}: href={href!r}")
        assert not offenders, (
            "these root-relative links on an APP-ORIGIN page resolve inside the dashboard "
            "project, where they are 404s — name the marketing host absolutely:\n  "
            + "\n  ".join(offenders))

    def test_the_pages_that_moved_are_still_the_files_that_serve_them(self):
        """The decision this child took: a Function rewrites the prefix, the files stay.

        Pinned because the other shape is a silent coverage loss: filing the six pages
        into `website/tortoise/` would leave `glob("*.html")` guarding 8 of 14 pages with
        no red anywhere (see `_pages`'s pin).
        """
        for name in self.MOVED:
            assert (self.WEB / name).is_file(), (
                f"{name} is no longer at website/{name} — the prefix is served by the "
                "middleware rewrite, so moving the file changes which pages the marketing "
                "guards can see")
