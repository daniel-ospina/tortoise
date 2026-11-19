/* The ONE pricing source for every marketing page that quotes a price.
 *
 * #13645 added `/tortoise-for-product-teams` alongside `/tortoise`, and both
 * pages render prices. Copying the `PRICING` object into the second page — the
 * shape the scoping named as the defect — would have produced two prices that
 * can drift with nothing red: a niche campaign page quoting a stale price is
 * worse than no campaign page. So the tier table + the formatting contract live
 * HERE, both pages load this file, and
 * `tests/test_website_static.py::TestPricingSourceIsShared` holds the two pages
 * to it (the pages' static cards are a no-JS fallback and are pinned to this
 * source by `TestStaticPricingFallback`).
 *
 * ⛔ DO NOT add a second copy of this object to a page. Both pages load
 * `/assets/tortoise-pricing.js`; a page that inlines its own tier table reds
 * the shared-source test, which exists for exactly that.
 *
 * The table is a MIRROR of `product/pricing.json` (the first source), pinned
 * against it by `tests/test_website_static.py::TestMirrorNumericParity` and
 * `tests/test_tier_naming.py`. Edit this file and `product/pricing.json`
 * together, never one of them alone.
 *
 * Serving: `website/assets/` is `public` in
 * `config/pages-upload-classification.txt`, and `script-src 'self'` in
 * `website/_headers` (and in RELAXED_CSP for Function-served pages) allows it.
 */
window.TORTOISE_PRICING = {
  display: {
    annual_discount_pct: 20,
    annual_default: true,
    license_self_hosted: "BSL 1.1 + $5M AUG (free <$5M trailing-12mo revenue); Mozilla Public License 2.0 (MPL-2.0) conversion after 4 years; hosted excluded from grant",
    overage_line: "$5 per additional 10k write ops",
  },
  tiers: {
    free: { price: 0, graphs: 1, users: 1, ops: 10000, nodes: 10000, keys: 2, overage: false, popular: false, features: ["1 graph", "1 collaborator", "10,000 write ops/mo", "10,000-node graph", "2 API keys", "Unlimited integrations"], excluded: ["Overage"] },
    solo: { price: 19, graphs: 2, users: 1, ops: 10000, nodes: 25000, keys: 5, overage: true, popular: false, features: ["2 graphs", "1 collaborator", "10,000 write ops/mo", "25,000-node graph", "5 API keys", "Unlimited integrations", "Usage-based overage"], excluded: [] },
    pro: { price: 25, graphs: "∞", users: 2, ops: 50000, nodes: 100000, keys: 10, overage: true, popular: true, features: ["Unlimited graphs", "2 collaborators", "50,000 write ops/mo", "100,000-node graph", "10 API keys", "Unlimited integrations", "Usage-based overage"], excluded: [] },
    team: { price: 149, graphs: "∞", users: "∞", ops: 200000, nodes: 600000, keys: 20, overage: true, popular: false, features: ["Unlimited graphs", "Unlimited collaborators (invites + RBAC)", "200,000 write ops/mo", "600,000-node graph", "20 API keys", "Unlimited integrations", "Usage-based overage", "Priority support"], excluded: [] }
  }
};

/* The display contract: (price, mode, discountPct) → {big, total}.
 *
 * Annual: effective PER-MONTH price big, total billed small (common practice).
 * Round ONCE (per-month), then derive the total so the two never disagree
 * (e.g. $7/mo × 12 must equal $84, not $86). ONE implementation, because two
 * pages formatting one price two ways is the same drift as two price tables —
 * `tests/test_website_static.py::_fmt_price` is the Python mirror of this. */
window.tortoiseFmtPrice = function (price, mode, discountPct) {
  if (mode === 'annual') {
    if (price === 0) return { big: '$0', total: 'Free forever' };
    var disc = discountPct / 100;
    var perMonth = Math.max(1, Math.round(price * 12 * (1 - disc) / 12));
    var total = perMonth * 12;
    return { big: '$' + perMonth, total: '$' + total + ' billed annually' };
  }
  return { big: '$' + price, total: 'Billed monthly' };
};
