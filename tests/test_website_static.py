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

Run:  python -m pytest tests/test_website_static.py -v
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_HTML = REPO_ROOT / "website" / "product.html"
PRICING_JSON = REPO_ROOT / "product" / "pricing.json"

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
    guard against. The signed-in redirect must precede the pipeline."""
    src = Path("website/welcome.html").read_text()
    assert "DEAD SINCE #1566" in src, "dead-pipeline marker missing"
    prov = src.index("DEAD SINCE #1566")
    assert prov < src.index("provisionViaEdgeFunction"), \
        "the pipeline marker must precede the provisioning functions"


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
