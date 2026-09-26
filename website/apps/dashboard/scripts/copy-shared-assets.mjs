// copy-shared-assets.mjs — #4143.
//
// WHY THIS EXISTS
// ---------------
// `website/consent.js` is the consent manager gating PostHog and the GTM
// container. It is origin-agnostic (no imports, no relative URLs) and was
// historically served from the marketing project root, which deploys
// `website/` verbatim — so `/consent.js` resolved on tortoise.premiselabs.co.
//
// #4054 moved the funnel pages (`signup.html` → `/auth`, `welcome.html`) onto
// the app origin, which deploys THIS directory's `dist/`. Both moved pages
// still load `<script src="/consent.js" defer></script>`, so on the app origin
// the request fell through to the SPA shell — a 200 with `text/html`, which
// Chromium refuses ("strict MIME type checking is enabled") and CI's
// zero-console-error assertion correctly fails. The consent banner never
// initialised, so `x_signup` and PostHog were silently skipped on the app
// origin.
//
// The fix keeps ONE tracked source of truth (`website/consent.js`, still served
// by the marketing project) and copies it into `dist/` at build time. A tracked
// duplicate under `public/` would drift; this cannot.
//
// Runs as part of `npm run build`, so `dist/` is correct for CI e2e, which
// boots `dist/` — not the source tree.
import { copyFileSync, existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const dashboardRoot = join(dirname(fileURLToPath(import.meta.url)), '..')
const source = join(dashboardRoot, '..', '..', 'consent.js')
const target = join(dashboardRoot, 'dist', 'consent.js')

// Fail loud. A silent skip would reproduce exactly the defect this fixes: the
// page 200s with HTML, and only a browser console check would notice.
if (!existsSync(source)) {
  console.error(`copy-shared-assets: missing source ${source}`)
  process.exit(1)
}
if (!existsSync(join(dashboardRoot, 'dist'))) {
  console.error('copy-shared-assets: dist/ does not exist — run vite build first')
  process.exit(1)
}

copyFileSync(source, target)
console.log(`copy-shared-assets: consent.js -> ${target}`)
