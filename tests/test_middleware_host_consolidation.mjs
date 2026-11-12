// The ONE marketing host (#11762) — behavioral tests for the middleware's
// redirect map, driven OFFLINE against the real `onRequest`.
//
// Run (Node >= 22.6):
//   node --experimental-strip-types tests/test_middleware_host_consolidation.mjs
//
// ⛔ WHY THIS EXISTS, and why it is not the Playwright suite.
// `tests/e2e/test_legal_pages.py` asserts the same routing through a browser
// against a dev server, but it is gated (RUN_LEGAL_E2E=1 + a DNS preflight for
// the product host), so on most lanes it SKIPS — and a skipped assertion cannot
// fail. This file constructs the Pages Function's context directly, so every
// row of the map is asserted on every run, with no server, no network and no
// gate. The two are complementary: the E2E suite proves the deployed host is
// correct, this proves the RULES are.
//
// The mock is deliberately minimal — `request.url`, the `host` header,
// `env.ASSETS.fetch` and `next()`. That is exactly the surface `onRequest`
// reads. ⛔ `env.ASSETS.fetch` RE-ENTERS the asset router, where the committed
// `website/_redirects` DOES apply (measured — `MEMORY.md` `[deploy]`), so this
// mock APPLIES the `.html` rules from the real file. That is not pedantry: an
// earlier cut of the middleware fetched `<page>.html`, five product pages are
// `_redirects` sources, and the rewrite answered 404 for every one of them —
// i.e. five browser-persistent 301s pointing at a 404. A mock that skipped
// `_redirects` was green through all of it. It cannot be now.
//
// ⛔ THE PROPERTY THIS FILE EXISTS FOR: one hop, never a chain.
// A 301 is browser-persistent and cannot be reclaimed by a later deploy, so a
// redirect may only ship when its target is a 200 in the SAME deploy. Every
// source URL below is therefore walked TWICE: the first response must be a
// single 3xx, and re-driving the middleware at the Location must answer 200.
// The asset server is mocked from the REAL committed `website/*.html` files, so
// "the target is a 200" is a claim about files that exist, not about a string.

import { onRequest } from "../website/functions/_middleware.ts";
import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const WEBSITE = join(HERE, "..", "website");

// The product set, as the map below drives it. Kept in one place so a page added
// to the middleware without a row here is a visible omission rather than a silent gap.
const PRODUCT_PAGES = [
  "/docs", "/faq", "/security", "/self-hosted", "/license",
  "/design-partners-intro", "/investors",
];

const COMPANY = "premiselabs.co";
const PRODUCT = "tortoise.premiselabs.co";
const APP = "app.premiselabs.co";

let failures = 0;

function assert(cond, msg) {
  if (!cond) {
    failures += 1;
    console.error(`  ✗ ${msg}`);
  }
}

function assertEq(actual, expected, msg) {
  assert(
    actual === expected,
    `${msg} (expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)})`,
  );
}

// ── The asset server, mocked from the REAL committed files ────────────────
// `website/*.html` is what Pages serves from the project root. Deriving the set
// from disk (rather than listing it here) is what makes "the target is a 200"
// falsifiable: delete docs.html and the map's /tortoise/docs row reds.
const ROOT_HTML = new Set(
  readdirSync(WEBSITE)
    .filter((f) => f.endsWith(".html"))
    .map((f) => `/${f}`),
);

// The two layers BELOW the middleware that a bare asset mock would get wrong,
// modelled explicitly so the pass-through assertions are about reality:
//   • Pages Functions — `/blog` and `/blog/*` are served by
//     `website/functions/blog/[[path]].ts`, so they are 200s, not files.
//   • `worksite/_redirects` — the retired Spanish privacy alias answers 301 on
//     ANY host, and the `.html` duplicate rules apply to an ASSET FETCH too. Both
//     are read from the REAL committed file, so adding or removing a rule changes
//     this harness rather than silently diverging from it.
const isBlogFunction = (pathname) => pathname === "/blog" || pathname.startsWith("/blog/");

// Parse the committed `_redirects` for single-hop 301 rules (the only shape this
// project uses). Anything this harness cannot model is a hard failure, not a skip.
const COMMITTED_REDIRECTS = (() => {
  const text = readFileSync(join(WEBSITE, "_redirects"), "utf8");
  const rules = new Map();
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const parts = line.split(/\s+/);
    if (parts.length !== 3 || parts[2] !== "301") {
      throw new Error("_redirects rule this harness cannot model: " + raw);
    }
    rules.set(parts[0], parts[1]);
  }
  return rules;
})();

// The asset router: `_redirects` first (YES, even for an ASSETS.fetch), then
// Pages' pretty-URL resolution of `/x` to `/x.html`, then the file itself.
async function assetsFetch(assetUrl) {
  const { pathname } = new URL(assetUrl);
  if (COMMITTED_REDIRECTS.has(pathname)) {
    // A 3xx is NOT an asset — the caller must treat this as a miss, exactly as
    // `website/apps/dashboard/functions/admin/[[path]].ts` does.
    return new Response(null, { status: 301, headers: { Location: COMMITTED_REDIRECTS.get(pathname) } });
  }
  if (ROOT_HTML.has(pathname)) {
    return new Response(`<html data-asset="${pathname}"></html>`, { status: 200 });
  }
  if (ROOT_HTML.has(`${pathname}.html`)) {
    return new Response(`<html data-asset="${pathname}.html"></html>`, { status: 200 });
  }
  return new Response("Not Found", { status: 404 });
}

function ctx(urlStr) {
  const u = new URL(urlStr);
  return {
    request: {
      url: urlStr,
      method: "GET",
      headers: new Headers({ host: u.host }),
    },
    env: { ASSETS: { fetch: assetsFetch } },
    // The platform's fallthrough after the middleware calls next():
    // _redirects first, then Functions, then a `.html`-less path serving
    // `path + ".html"`, then the root's index.html, then 404.
    next: async () => {
      if (COMMITTED_REDIRECTS.has(u.pathname)) {
        return new Response(null, {
          status: 301,
          headers: { Location: `${u.origin}${COMMITTED_REDIRECTS.get(u.pathname)}` },
        });
      }
      if (isBlogFunction(u.pathname)) {
        return new Response(`<html data-function="blog"></html>`, { status: 200 });
      }
      if (u.pathname === "/") {
        return new Response(`<html data-asset="/index.html"></html>`, { status: 200 });
      }
      if (ROOT_HTML.has(u.pathname) || ROOT_HTML.has(`${u.pathname}.html`)) {
        return new Response(`<html data-asset="${u.pathname}"></html>`, { status: 200 });
      }
      return new Response("Not Found", { status: 404 });
    },
  };
}

async function hit(urlStr) {
  const res = await onRequest(ctx(urlStr));
  return { status: res.status, location: res.headers.get("location") };
}

// Drive a source URL and assert it is a SINGLE 3xx onto a 200 (or onto an
// explicitly-allowed external origin, for the app-origin handoffs).
async function assertOneHop(src, { expectStatus = 301, expectLocation = null, external = false } = {}) {
  const first = await hit(src);
  assertEq(first.status, expectStatus, `${src} answers ${expectStatus}`);
  if (expectLocation !== null) {
    assertEq(first.location, expectLocation, `${src} redirects to the declared target`);
  }
  if (!first.location) {
    assert(true && false, `${src} answered a 3xx with NO Location`);
    return;
  }
  const locHost = new URL(first.location).host;
  if (external) {
    // The app origin is a SEPARATE Pages project (`tortoise-dashboard`); this
    // mock cannot answer for it, so the assertion is that we hand off to it in
    // exactly ONE hop rather than chaining through our own host.
    assertEq(locHost, APP, `${src} hands off to the app origin in one hop`);
    return;
  }
  assertEq(locHost, COMPANY, `${src} lands on the company host (the one marketing host)`);
  const second = await hit(first.location);
  assert(
    second.status !== 301 && second.status !== 302,
    `${src} → ${first.location} is a CHAIN (second hop ${second.status} ${second.location ?? ""})`,
  );
  assertEq(second.status, 200, `${src} → ${first.location} lands on a 200`);
}

// ── The map, row by row (#11762) ───────────────────────────────────────────
//   tortoise.premiselabs.co/                      → premiselabs.co/tortoise
//   tortoise.premiselabs.co/{docs,faq,self-hosted,security}
//                                                 → premiselabs.co/tortoise/…
//   tortoise.premiselabs.co/license               → premiselabs.co/tortoise/license
//   tortoise.premiselabs.co/{privacy,dpa,tos}     → premiselabs.co/…
//   tortoise.premiselabs.co/blog/*                → premiselabs.co/blog/*
{
  const productRows = [
    ["/", "/tortoise"],
    ["/product", "/tortoise"],
    ["/product.html", "/tortoise"],
    ["/index.html", "/tortoise"],
    ["/docs", "/tortoise/docs"],
    ["/docs.html", "/tortoise/docs"],
    ["/faq", "/tortoise/faq"],
    ["/self-hosted", "/tortoise/self-hosted"],
    ["/security", "/tortoise/security"],
    ["/license", "/tortoise/license"],
    ["/license.html", "/tortoise/license"],
    ["/design-partners-intro", "/tortoise/design-partners-intro"],
    ["/investors", "/tortoise/investors"],
  ];
  for (const [from, to] of productRows) {
    await assertOneHop(`https://${PRODUCT}${from}`, {
      expectLocation: `https://${COMPANY}${to}`,
    });
  }

  // The legal pages land at the company ROOT — they govern the business, not
  // the product. This split is the whole reason #11762's child 1 precedes the
  // children that move files.
  for (const [from, to] of [
    ["/privacy", "/privacy"],
    ["/privacy.html", "/privacy"],
    ["/tos", "/tos"],
    ["/dpa", "/dpa"],
  ]) {
    await assertOneHop(`https://${PRODUCT}${from}`, {
      expectLocation: `https://${COMPANY}${to}`,
    });
  }

  // The blog prefix keeps its exact-prefix semantics: /blogpost and /blog-extra
  // must NOT match (E2E-7), so they stay un-redirected on the product host.
  for (const from of ["/blog", "/blog/", "/blog/types-of-agent-memory"]) {
    const { status, location } = await hit(`https://${PRODUCT}${from}`);
    assertEq(status, 301, `${from} on the product host is redirected`);
    assert(location?.startsWith(`https://${COMPANY}/blog`), `${from} lands on the company blog`);
  }
  for (const from of ["/blogpost", "/blog-extra"]) {
    const { status } = await hit(`https://${PRODUCT}${from}`);
    assert(
      status !== 301 && status !== 302,
      `${from} is NOT caught by the blog prefix rule (exact prefix only) — got ${status}`,
    );
  }
}

// ── The company host IS the site now ──────────────────────────────────────
{
  for (const p of ["/", "/blog", "/blog/types-of-agent-memory", "/privacy", "/tos", "/dpa"]) {
    const { status } = await hit(`https://${COMPANY}${p}`);
    assertEq(status, 200, `company ${p} is SERVED here, not redirected off-host`);
  }
  // The retired route is answered by the committed `_redirects` (→ /privacy),
  // which this middleware must not pre-empt with a cross-host hop: the 301 the
  // client sees must be SAME-HOST, so the canonical host is named once and the
  // terminal URL is /privacy on this host.
  const retired = await hit(`https://${COMPANY}/aviso-privacidad`);
  assertEq(retired.status, 301, "company /aviso-privacidad passes through to _redirects");
  assertEq(
    new URL(retired.location).host,
    COMPANY,
    "the retired alias consolidates SAME-HOST through _redirects (no cross-host hop)",
  );
  assertEq(new URL(retired.location).pathname, "/privacy", "…onto /privacy");
}

// ── The product URL is SERVED, so every 301 above lands on a 200 ──────────
{
  const rows = [
    ["/tortoise", "/product.html"],
    ["/tortoise/", "/product.html"],
    ["/tortoise/docs", "/docs.html"],
    ["/tortoise/docs.html", "/docs.html"],
    ["/tortoise/faq", "/faq.html"],
    ["/tortoise/self-hosted", "/self-hosted.html"],
    ["/tortoise/security", "/security.html"],
    ["/tortoise/license", "/license.html"],
    ["/tortoise/investors", "/investors.html"],
    ["/tortoise/design-partners-intro", "/design-partners-intro.html"],
  ];
  for (const [from, asset] of rows) {
    const res = await onRequest(ctx(`https://${COMPANY}${from}`));
    assertEq(res.status, 200, `company ${from} is SERVED (rewrite), not redirected`);
    const body = await res.text();
    assert(
      body.includes(`data-asset="${asset}"`),
      `company ${from} serves ${asset} (got ${body.slice(0, 40)})`,
    );
  }
  // A prefix match must not turn into a wildcard 200: an unknown product page
  // is a 404, never a redirect onto a 404.
  const missing = await onRequest(ctx(`https://${COMPANY}/tortoise/nope`));
  assertEq(missing.status, 404, "an unknown /tortoise/* path is a 404, not a 200 or a redirect");

  // ⛔ THE PREFIX IS NOT A WILDCARD. Only the product set is servable under it —
  // otherwise `/tortoise/index` answers the COMPANY homepage at a product URL and
  // `/tortoise/product` is a second URL for the product page, both 200. That is
  // the duplicate-canonical condition the whole change exists to remove, and the
  // `/tortoise/nope` row above does NOT discriminate it (`nope` is not a file).
  for (const from of ["/tortoise/index", "/tortoise/contact", "/tortoise/product", "/tortoise/privacy"]) {
    const res = await onRequest(ctx(`https://${COMPANY}${from}`));
    assertEq(
      res.status,
      404,
      `${from} must NOT resolve — only the product set (${[...PRODUCT_PAGES].join(", ")}) is servable under the prefix`,
    );
  }
}

// ── The app origin's pinned decisions survive the host move ────────────────
// #4054 / #4171 / #4409: these are HOST-INDEPENDENT branches and the product
// host's 301 must not pre-empt them.
{
  for (const host of [PRODUCT, COMPANY]) {
    await assertOneHop(`https://${host}/signin`, { external: true });
    await assertOneHop(`https://${host}/signin.html`, { external: true });
  }
  const auth = await hit(`https://${PRODUCT}/auth`);
  assertEq(auth.status, 301, "product /auth keeps its grandfathered 301");
  assertEq(auth.location, `https://${APP}/auth`, "product /auth still goes to the APP origin");

  const admin = await hit(`https://${PRODUCT}/admin`);
  assertEq(admin.status, 302, "product /admin keeps its 302 (#4409 — a NEW branch is 302)");
  assertEq(admin.location, `https://${APP}/admin`, "product /admin still goes to the APP origin");
}

// ── /blog/api/* is NEVER redirected (a POST must not become a GET) ────────
{
  for (const host of [PRODUCT, COMPANY]) {
    const { status } = await hit(`https://${host}/blog/api/session`);
    assertEq(status, 200, `${host}/blog/api/* is not redirected on ${host}`);
  }
}

// ── The retired privacy alias is ONE hop onto a canonical-host 200 ─────────
{
  await assertOneHop(`https://${PRODUCT}/aviso-privacidad`, {
    expectLocation: `https://${COMPANY}/privacy`,
  });
}

// ── Previews and local dev keep the pass-through ──────────────────────────
// The legal E2E suite runs against `wrangler pages dev` (host 127.0.0.1) and
// depends on the tortoise-only pages answering 200 there; previews stay
// navigable and are never indexed.
{
  for (const host of ["127.0.0.1:8788", "feat-x.premise-labs.pages.dev"]) {
    for (const p of ["/docs", "/privacy", "/blog"]) {
      const { status } = await hit(`http://${host}${p}`);
      assertEq(status, 200, `${host}${p} passes through (dev/preview)`);
    }
    // …and the product URL is exercisable there too, which is what lets the
    // E2E suite cover the rewrite without a production DNS preflight.
    const prod = await onRequest(ctx(`http://${host}/tortoise/docs`));
    assertEq(prod.status, 200, `${host}/tortoise/docs is served on dev/preview too`);
  }
}

// ── A stray OAuth code is still refused, and still BEFORE any branch ──────
// #10088: the refusal sits above the host blocks because those branches carry
// the query string into a `Location`. The host move must not change that.
{
  const code = "?code=50605f24-15a1-40f0-8e86-f3722cccce13";
  for (const host of [PRODUCT, COMPANY]) {
    const { status, location } = await hit(`https://${host}/docs${code}`);
    assertEq(status, 400, `${host}/docs with a stray code is REFUSED, not redirected`);
    assertEq(location, null, `${host}/docs with a stray code emits no Location (code cannot travel)`);
  }
  // /tortoise is not an exempt surface either — it renders a page.
  const under = await hit(`https://${COMPANY}/tortoise/docs${code}`);
  assertEq(under.status, 400, "the new product prefix is not an exempt surface for a stray code");
}

console.log("");
if (failures > 0) {
  console.error(`${failures} host-consolidation assertion(s) failed`);
  process.exitCode = 1;
} else {
  console.log("all host-consolidation assertions passed");
}
