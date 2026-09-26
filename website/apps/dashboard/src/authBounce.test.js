// authBounce.test.js — #3930. The regression guard for the app-origin auth bounce.
//
// THE PROPERTY UNDER TEST
// -----------------------
// A signed-out visit to a real app pathname must carry that pathname (and its
// query) across the bounce to /auth, so the visitor is returned to the page they
// asked for — and the value must be a same-origin PATH, so the bounce cannot
// become an open redirect.
//
// WHY IT IS A PURE-MODULE TEST PLUS TWO PINS
// ------------------------------------------
// The dashboard has no React test runtime, so — like sessionGate.test.js — the
// behaviour is proven by executing the real extracted module, and the wiring is
// pinned by reading main.jsx as TEXT. A pure test would pass even if main.jsx
// never called the module; the wiring pin would pass even if the module were
// wrong. The third pin is the producer↔consumer MIRROR: the /auth page's inline
// allowlist cannot import this module (inline HTML in the ASSETS project, vs a
// Vite bundle), so the two route lists are copies — and a silent drift between
// them is exactly the #3930 symptom (a return-to emitted but dropped).
//
// NON-VACUITY: the mutation control at the end rebuilds the pre-#3930 bounce
// (`'/auth' + search + hash`) and shows the core assertion reds on it.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { stripComments } from './testSupport.js'
import {
  APP_RETURN_ROUTES,
  APP_SUBTREE_ROUTES,
  authBounceTarget,
  isAppReturnPath,
} from './authBounce.js'

const here = dirname(fileURLToPath(import.meta.url))
const dashboardRoot = join(here, '..')
const signupHtml = readFileSync(join(dashboardRoot, 'public', 'signup.html'), 'utf8')
const mainJsx = stripComments(readFileSync(join(here, 'main.jsx'), 'utf8'))

/** The `next` the bounce offers, or null when it offers none. */
function nextOf(target) {
  return new URLSearchParams(target.split('#')[0].replace(/^\/auth/, '')).get('next')
}

// ── the allowlist ──────────────────────────────────────────────────────────

test('accepts the app origin real pathname routes', () => {
  // Single pages: exact form and the trailing-slash form. Both are routed —
  // `_redirects` 200-rewrites `/team/`, and main.jsx's welcome mode accepts
  // `/welcome/`.
  for (const path of ['/welcome', '/welcome/', '/team', '/team/']) {
    assert.equal(isAppReturnPath(path), true, `${path} is a real app route`)
  }
  // The console is a SUBTREE: functions/admin/[[path]].ts serves the shell at
  // any depth, so its sub-paths are real destinations.
  for (const path of ['/admin', '/admin/', '/admin/blog', '/admin/blog/edit', '/admin/assets/index-DS3aDc5i.js']) {
    assert.equal(isAppReturnPath(path), true, `${path} is a real console route`)
  }
})

test('rejects everything that is not an app route', () => {
  for (const path of [
    // single pages do NOT own sub-paths — /team/x and /welcome/x 404
    '/team/x',
    '/welcome/x',
    // prefix confusion
    '/welcomex',
    '/welcomex/y',
    '/administrator',
    '/administer',
    // the app root: the default landing needs no return-to
    '/',
    // the auth page itself: carrying it as a return-to would loop
    '/auth',
    '/auth/callback',
    // off the allowlist entirely
    '/blog',
    '/signin',
    '/nonsense-probe-xyz',
    // not a pathname at all
    '',
    null,
    undefined,
    42,
  ]) {
    assert.equal(isAppReturnPath(path), false, `${String(path)} must not be a return-to`)
  }
})

// ── the bounce target ──────────────────────────────────────────────────────

test('carries the requested path AND its query as next', () => {
  // The two deep links #3930 exists for: the Stripe return handoff and the
  // recovery panel.
  const stripe = authBounceTarget({ pathname: '/team', search: '?session_id=abc' })
  assert.equal(nextOf(stripe), '/team?session_id=abc', `stripe return lost: ${stripe}`)
  assert.equal(stripe.split('#')[0], '/auth?session_id=abc&next=%2Fteam%3Fsession_id%3Dabc')

  const reset = authBounceTarget({ pathname: '/welcome', search: '?reset=1' })
  assert.equal(nextOf(reset), '/welcome?reset=1', `recovery panel lost: ${reset}`)

  assert.equal(nextOf(authBounceTarget({ pathname: '/welcome', search: '' })), '/welcome')
  assert.equal(nextOf(authBounceTarget({ pathname: '/team/', search: '' })), '/team/')
  assert.equal(nextOf(authBounceTarget({ pathname: '/admin/blog', search: '' })), '/admin/blog')
})

test('the bounce is a same-origin PATH — never an origin', () => {
  // Every accepted target must resolve to the app origin and to an app route.
  // This is the open-redirect assertion at the producer: the value is a path,
  // so it cannot name a host.
  const app = 'https://app.premiselabs.co'
  for (const [pathname, search] of [
    ['/team', '?session_id=abc'],
    ['/welcome', '?reset=1'],
    ['/admin/blog', ''],
    ['/team', '?x=//evil.com'],
  ]) {
    const target = authBounceTarget({ pathname, search })
    const next = nextOf(target)
    assert.ok(next, `no return-to emitted for ${pathname}`)
    const resolved = new URL(next, app)
    assert.equal(resolved.origin, app, `next escaped the origin: ${next} → ${resolved.origin}`)
    assert.equal(isAppReturnPath(resolved.pathname), true, `next is not an app route: ${next}`)
    assert.ok(!next.startsWith('//'), `next is protocol-relative: ${next}`)
  }
})

test('ignores an off-allowlist or absolute pathname', () => {
  for (const pathname of ['/blog', '/', '/auth', 'https://evil.com', '//evil.com', '/..//evil.com', 'javascript:alert(1)']) {
    const target = authBounceTarget({ pathname, search: '' })
    assert.equal(nextOf(target), null, `off-allowlist pathname produced a next: ${pathname} → ${target}`)
    assert.equal(target, '/auth')
  }
})

test('preserves the existing bounce behaviour', () => {
  // The OAuth error banner rides the target; a live credential fragment never
  // does (oauthErrorHash() is the caller's guard — see main.jsx).
  const withError = authBounceTarget({ pathname: '/team', search: '?claim=1', errorHash: '#error=denied' })
  assert.equal(withError, '/auth?claim=1&next=%2Fteam%3Fclaim%3D1#error=denied')

  // No return-to: the query still rides through (the claim card depends on it).
  assert.equal(authBounceTarget({ pathname: '/', search: '?claim=1' }), '/auth?claim=1')
  assert.equal(authBounceTarget({}), '/auth')

  // A caller that forgets the leading '?' must not corrupt the path.
  assert.equal(nextOf(authBounceTarget({ pathname: '/team', search: 'session_id=abc' })), '/team?session_id=abc')
})

// ── wiring pin: main.jsx must use the module ───────────────────────────────

test('main.jsx delegates its bounce to this module', () => {
  assert.match(mainJsx, /import\s*\{[^}]*\bauthBounceTarget\b[^}]*\}\s*from\s*'\.\/authBounce\.js'/, 'the module is not imported')
  assert.match(mainJsx, /authBounceTarget\(\{/, 'bounceToAuth does not call authBounceTarget')
  assert.match(mainJsx, /pathname:\s*window\.location\.pathname/, 'the bounce does not read the pathname')
  // The pre-#3930 destination built itself from the origin + search + hash only.
  assert.doesNotMatch(mainJsx, /['"]\/auth['"]\s*\+\s*search/, 'the pathname-dropping bounce is back')
})

// ── producer ↔ consumer mirror pin ─────────────────────────────────────────

test('signup.html mirrors the route allowlist and the subtree set', () => {
  function literal(name) {
    const m = signupHtml.match(new RegExp(`var ${name} = (\\[[^\\]]*\\])`))
    assert.ok(m, `${name} literal not found in signup.html — the mirror cannot be checked`)
    return JSON.parse(m[1])
  }
  assert.deepEqual(literal('APP_RETURN_ROUTES'), APP_RETURN_ROUTES, 'the /auth allowlist drifted from src/authBounce.js')
  assert.deepEqual(literal('APP_SUBTREE_ROUTES'), APP_SUBTREE_ROUTES, 'the subtree set drifted from src/authBounce.js')
  // The pre-#3930 consumer hard-coded /admin; a revert must red this pin.
  assert.doesNotMatch(signupHtml, /path !== "\/admin" && path !== "\/admin\/"/, 'the /admin-only predicate is back')
  // The query must ride the return-to, or /team?session_id= loses the handoff.
  assert.match(signupHtml, /var ret = path \+ u\.search/, 'the return-to no longer carries the query')
})

// ── mutation control ──────────────────────────────────────────────────────

test('mutation control: the pre-#3930 bounce fails these assertions', () => {
  // Rebuild the exact shape #3930 removed and show the core property is absent
  // from it — so this file is not passing over an unchanged implementation.
  const legacyBounce = (search = '', hash = '') => '/auth' + search + hash
  const legacy = legacyBounce('?session_id=abc', '')
  assert.equal(legacy, '/auth?session_id=abc')
  assert.equal(nextOf(legacy), null, 'the legacy bounce must carry no return-to')
  const fixed = authBounceTarget({ pathname: '/team', search: '?session_id=abc' })
  assert.equal(nextOf(fixed), '/team?session_id=abc')
  assert.notEqual(legacy, fixed)
})
