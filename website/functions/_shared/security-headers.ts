/**
 * Content-Security-Policy for the premise-labs Pages project
 * (`premiselabs.co`, `tortoise.premiselabs.co`) — #3525.
 *
 * There is only one trust level on this origin: every page here is a pre-#3501
 * static page (or the blog SSR, which also renders inline style + JSON-LD), so
 * all of them need `RELAXED_CSP`. See the dashboard's
 * `functions/_shared/security-headers.ts` for the full rationale, including why
 * each origin below is present and why `'unsafe-inline'` is a declared,
 * temporary concession that #3501 removes.
 *
 * WHY A SECOND COPY
 * -----------------
 * This is a separate Cloudflare Pages project and `functions/` cannot import
 * across project roots, so the value is duplicated. `website/_headers` carries
 * the same string for static assets — `_headers` does NOT apply to Function
 * responses, so the two must stay in step. `src/securityHeaders.test.js` in the
 * dashboard asserts this constant, the dashboard's `RELAXED_CSP`, and the two
 * `_headers` values are byte-identical, so the duplication cannot drift.
 *
 * `https://static.cloudflareinsights.com` is not ours: Cloudflare Web Analytics
 * is on for this zone, so the edge injects its SRI-pinned beacon into HTML
 * responses whose REQUEST carries `Accept: text/html` (not a User-Agent test —
 * `curl -H 'Accept: text/html' https://premiselabs.co/` shows the tag, while a
 * plain `curl` and a local preview do not). The dashboard copy of this file
 * documents the SRI caveats in full; the guard fails if any policy drops the
 * beacon's script origin, or re-ADDS the third-party RUM origin — under automatic
 * injection the beacon POSTs to this origin's own `/cdn-cgi/rum`, so that host is
 * the manual-embed endpoint only and must not be allowlisted (#4706).
 */

const csp = (...directives: string[]): string => directives.join("; ");

/** Static assets (`website/_headers` `/*`) and every Function-rendered HTML page. */
export const RELAXED_CSP = csp(
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' https://static.cloudflareinsights.com https://cdnjs.cloudflare.com https://us-assets.i.posthog.com https://www.googletagmanager.com https://connect.facebook.net https://challenges.cloudflare.com https://static.ads-twitter.com",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: https:",
  "font-src 'self' data:",
  "connect-src 'self' https://*.supabase.co https://api.premiselabs.co https://us.i.posthog.com https://us-assets.i.posthog.com https://www.googletagmanager.com https://www.google-analytics.com https://region1.google-analytics.com https://connect.facebook.net https://www.facebook.com https://challenges.cloudflare.com",
  // #7751: the blog renders video embeds (`blog/_lib.ts` -> `_shared/video.ts`).
  // `frame-src` is present and restrictive, so it — NOT `default-src` — governs
  // the nested browsing context: without these two origins every embed is
  // CSP-blocked while the article (and its `VideoObject`) renders. The origins
  // are the renderer's own constructed embed hosts, asserted against the
  // renderer's provider list by `tests/test_blog_markdown.mjs`.
  "frame-src https://challenges.cloudflare.com https://www.youtube-nocookie.com https://player.vimeo.com",
  "object-src 'none'",
  "base-uri 'none'",
  "frame-ancestors 'none'",
  "form-action 'self'",
);
