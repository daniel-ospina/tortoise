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
 * `/assets/tortoise-pricing.js`, and the list of pages that render the table is
 * `PRODUCT_PAGES_SHARING_PRICING` in `tests/test_website_static.py` — a row in
 * that file (`test_the_population_is_every_page_that_renders_the_table`) walks
 * every served page and REFUSES one that renders the table (`TORTOISE_PRICING`,
 * `pricing-grid`, `class="price"`) without being in that list, so a page that
 * inlines its own tier table reds rather than shipping a second copy. A new
 * page that quotes prices joins the list deliberately; it does not inherit
 * coverage by copy-pasting this file's shape.
 *
 * The table is a MIRROR of `product/pricing.json` (the first source), pinned
 * against it by `tests/test_website_static.py::TestMirrorNumericParity`. (The
 * tier NAMES are separately held across `product/pricing.json`, product.html's
 * `labels` map and the dashboard's `TIER_LABELS` by `tests/test_tier_naming.py`;
 * that module does not read this file.) Edit this file and `product/pricing.json`
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

/* ⛔ FROZEN — the table is ONE object every page that quotes a price renders, so
 * a page that WRITES to it changes the price every OTHER page shows.
 * `tests/test_website_static.py::test_neither_page_mutates_the_shared_table`
 * refuses the statically written forms (`x = …`, `delete x.y`, `Object.assign`),
 * and a list of spellings is exactly what a reflective write defeats:
 * `Object.defineProperty(…, 'price', {value: 26})` and `Reflect.set(…)` got past
 * it and left every row green (#13696 round 11, measured). So the freeze is the
 * gate. Measured with node against this file: `defineProperty` / `features.push`
 * throw, and `x.price = 26` / `Reflect.set(…, 'price', 26)` are REFUSED
 * (`Reflect.set` returns false) — the tier still prices at 25 and
 * `tortoiseFmtPrice(25, 'annual', 20)` still returns `$20` / `$240 billed
 * annually`, so no page can re-price the table for the other one. A classic
 * (non-module) script is SLOPPY mode, which is why an assignment or a
 * `Reflect.set` is silently ignored rather than thrown: the write does not take
 * effect either way, which is the invariant. Deep — `Object.keys`, so a tier
 * added above is covered the day it lands — and it is pinned by
 * `test_the_shared_table_is_FROZEN_against_a_reflective_write`. */
(function freezePricingTable(table) {
  Object.keys(table.tiers).forEach(function (key) {
    var tier = table.tiers[key];
    Object.freeze(tier.features);
    Object.freeze(tier.excluded);
    Object.freeze(tier);
  });
  Object.freeze(table.display);
  Object.freeze(table.tiers);
  Object.freeze(table);
}(window.TORTOISE_PRICING));

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
  /* ⛔ The Free tier is free in BOTH modes. Without this line a $0 tier read
   * `$0 /mo — Billed monthly` on the Monthly tab and `Free forever` on Annual:
   * nothing is billed on a free tier, so the Monthly copy is both false and a
   * contradiction of the same card one click away (#13696 review cycle 2, P2).
   * The static card already says `Free forever`, so this also keeps the JS
   * render and the no-JS fallback saying the same thing. */
  if (price === 0) return { big: '$0', total: 'Free forever' };
  return { big: '$' + price, total: 'Billed monthly' };
};
