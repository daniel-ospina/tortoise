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


