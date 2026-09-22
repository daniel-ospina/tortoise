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
 */

const csp = (...directives: string[]): string => directives.join("; ");

/** Static assets (`website/_headers` `/*`) and every Function-rendered HTML page. */
export const RELAXED_CSP = csp(
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://us-assets.i.posthog.com https://www.googletagmanager.com https://connect.facebook.net https://challenges.cloudflare.com",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: https:",
  "font-src 'self' data:",
  "connect-src 'self' https://*.supabase.co https://api.premiselabs.co https://us.i.posthog.com https://us-assets.i.posthog.com https://www.googletagmanager.com https://www.google-analytics.com https://region1.google-analytics.com https://connect.facebook.net https://www.facebook.com https://challenges.cloudflare.com",
  "frame-src https://challenges.cloudflare.com",
  "object-src 'none'",
  "base-uri 'none'",
  "frame-ancestors 'none'",
  "form-action 'self'",
);
