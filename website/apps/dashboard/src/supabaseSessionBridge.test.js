// supabaseSessionBridge.test.js — #3485 behavioural execution tests for the
// retained cross-subdomain session bridge (website/assets/supabase-session.js).
//
// WHY THIS FILE EXECUTES THE REAL SCRIPT. The pins it replaces were TEXT
// extractors — brace counting plus substring counts. Three review cycles each
// escaped them without being wrong: a `}` inside a string literal truncated a
// brace-counted body; a `//` inside a URL string corrupted comment stripping;
// `window['local'+'Storage']` dodged a `"localStorage" not in body` check;
// `legacyOk=true` dodged `count("legacyOk =")`; `!(stored.expires_at && …)`
// kept the `&&` count at 4 while inverting the polarity; and a bare
// `return null;` above the migration kept every ordering/if check green. Text
// is the wrong tool. This file loads the REAL bridge into a `node:vm` context
// with a minimal browser stub and asserts only OBSERVABLE behaviour — return
// values, cookie bytes, localStorage contents, history calls. None of the
// obfuscations above change behaviour, so none of them can save a mutant.
//
// The sandbox stubs exactly what the bridge touches: `window`/`window.location`,
// `window.localStorage`, `document.cookie` (a real name-addressed jar with the
// RFC 6265 4096-byte per-cookie cap, so an oversized write is DROPPED like a
// browser drops it) and `history.replaceState`. `URL`/`URLSearchParams` are
// passed in because a fresh vm context has neither.
//
// #4054 — WHY THIS SUITE IS RESTORED, AND WHY THE SUBJECT IS THE SHARED FILE.
// This file was deleted with the dashboard's COPY of the bridge
// (website/apps/dashboard/public/assets/supabase-session.js), but
// website/assets/supabase-session.js itself is RETAINED (and still served from
// premiselabs.co). Deleting the only executing test of a retained artifact is
// how it silently rots, so the suite is restored against the shared file.
//
// The dashboard no longer loads the bridge — it is BFF-migrated — and NEITHER
// DOES blog-admin: blog-admin ships its own adapter
// (website/apps/blog-admin/src/lib/supabase.ts) which only shares the COOKIE
// NAME; it has no <script src="/assets/supabase-session.js"> and no import of
// the bridge. The reasons the shared bridge is retained are different and
// concrete: tortoise/oauth.py's live consent-page client is a faithful inline
// PORT of its adapter (parity pinned by tests/test_cross_subdomain_cookie_sync.py),
// the dashboard's live `tt_claim_pending` marker helpers mirror its
// host-conditional helpers (same test), and this file is the subject of the #3503
// fragment-retention gate (tests/test_session_bridge_fragment_retention.py).
// It lives in the dashboard suite because that is the repo's node test runner —
// `.github/workflows/ci.yml` predicates the `dashboard-js-tests` job on a change
// to website/assets/supabase-session.js precisely so this suite runs for it.
// The path below resolves from here to website/assets/supabase-session.js.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'

const here = dirname(fileURLToPath(import.meta.url))
// website/apps/dashboard/src → ../../../assets/supabase-session.js
const BRIDGE_PATH = join(here, '..', '..', '..', 'assets', 'supabase-session.js')
const BRIDGE_SRC = readFileSync(BRIDGE_PATH, 'utf8')

const COOKIE_NAME = 'sb-tortoise-auth-token'
const PROD_LEGACY_KEY = 'sb-ybetwichurajbfswfeqa-auth-token'
const LOCAL_LEGACY_KEY = 'sb-127-auth-token'
const PROD_URL = 'https://ybetwichurajbfswfeqa.supabase.co'
const LOCAL_URL = 'http://127.0.0.1:54321'
const DAY_MS = 24 * 3600 * 1000
const COOKIE_BYTE_CAP = 4096 // RFC 6265 §6.1 minimum per-cookie size

// ── session fixtures ────────────────────────────────────────────────────────
let tokenSeq = 0
function session(overrides = {}) {
  tokenSeq += 1
  return {
    access_token: `access-${tokenSeq}`,
    refresh_token: `refresh-${tokenSeq}`,
    expires_at: Math.floor((Date.now() + DAY_MS) / 1000),
    token_type: 'bearer',
    ...overrides,
  }
}
const pastExpiry = () => Math.floor((Date.now() - 60_000) / 1000)
const futureExpiry = (ms = DAY_MS) => Math.floor((Date.now() + ms) / 1000)
// A valid JSON session that no browser will accept as a cookie: its encoded
// value exceeds the per-cookie cap, so the write is DROPPED (this is the real
// "oversized session silently refused" case the round-trip check exists for).
function oversizedSession() {
  return session({ access_token: 'A'.repeat(COOKIE_BYTE_CAP + 200) })
}
// #3951: a session over SIZE_GUARD (3800 encoded bytes) whose size-guard
// transform strips provider tokens / identities / metadata bloat and then LANDS
// inside the cap. This is the shape the old `readCookie(COOKIE_NAME) !== legacy`
// check misread as "not stored": the cookie holds a DIFFERENT (transformed)
// string, so the equality was false and the early return skipped the trailing
// stale-secret cleanup.
function lossySession(overrides = {}) {
  return session({
    provider_token: 'PROVIDER-TOKEN-SECRET-VALUE',
    provider_refresh_token: 'PROVIDER-REFRESH-TOKEN-SECRET-VALUE',
    user: {
      id: 'u1',
      identities: [{ id: 'identity-1' }],
      user_metadata: { display_name: 'D', blob: 'x'.repeat(6000) },
    },
    ...overrides,
  })
}

// ── the browser stub ────────────────────────────────────────────────────────
function makeCookieJar({ cap = COOKIE_BYTE_CAP } = {}) {
  const jar = new Map()
  const writes = []
  const document = {
    get cookie() {
      return [...jar.entries()].map(([k, v]) => `${k}=${v}`).join('; ')
    },
    set cookie(header) {
      const text = String(header)
      const semi = text.indexOf(';')
      const pair = semi === -1 ? text : text.slice(0, semi)
      const attrs = semi === -1 ? '' : text.slice(semi)
      const eq = pair.indexOf('=')
      const name = pair.slice(0, eq)
      const value = pair.slice(eq + 1)
      if (/;\s*Max-Age=0/i.test(attrs)) {
        jar.delete(name)
        writes.push({ header: text, dropped: false, removed: true })
        return
      }
      // Browser parity: a serialized cookie above the per-cookie cap is dropped
      // silently — exactly the refusal `storeSession` reads back to detect.
      const dropped = name.length + value.length > cap
      writes.push({ header: text, dropped, removed: false })
      if (dropped) return
      jar.set(name, value)
    },
  }
  return { jar, document, writes }
}

function makeLocalStorage() {
  const map = new Map()
  return {
    map,
    api: {
      getItem: (k) => (map.has(String(k)) ? map.get(String(k)) : null),
      setItem: (k, v) => { map.set(String(k), String(v)) },
      removeItem: (k) => { map.delete(String(k)) },
      clear: () => map.clear(),
      key: (i) => [...map.keys()][i] ?? null,
      get length() { return map.size },
    },
  }
}

function makeSandbox({
  hostname = 'tortoise.premiselabs.co',
  hash = '',
  pathname = '/',
  search = '',
  origin,
  cap = COOKIE_BYTE_CAP,
  supabase,
} = {}) {
  const { jar, document, writes } = makeCookieJar({ cap })
  const ls = makeLocalStorage()
  const replaceStateCalls = []
  const locationReplaceCalls = []
  const location = {
    hostname,
    hash,
    pathname,
    search,
    origin: origin ?? `https://${hostname}`,
    replace: (u) => { locationReplaceCalls.push(String(u)) },
  }
  const window = { location, localStorage: ls.api }
  if (supabase !== undefined) window.supabase = supabase
  const history = { replaceState: (a, b, url) => { replaceStateCalls.push(String(url)) } }

  const sandbox = {
    window,
    document,
    history,
    URL,
    URLSearchParams,
    console,
  }
  vm.createContext(sandbox)
  vm.runInContext(BRIDGE_SRC, sandbox, { filename: BRIDGE_PATH })

  return {
    sandbox, window, document, jar, writes, ls, location, history,
    replaceStateCalls, locationReplaceCalls,
    bridge: window.__tortoiseSessionBridge,
  }
}

// Read a cookie the way the bridge does (name → URL-decoded value).
function cookieValue(sb, name = COOKIE_NAME) {
  const m = sb.document.cookie.match(
    new RegExp('(?:^|; )' + name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '=([^;]*)')
  )
  return m ? decodeURIComponent(m[1]) : null
}
// Seed a cookie without going through the bridge (the "something already
// stored" precondition).
function seedCookie(sb, value, name = COOKIE_NAME) {
  sb.document.cookie = `${name}=${encodeURIComponent(value)}; Path=/`
}

// ── positive control / sandbox reachability ─────────────────────────────────

test('#3485: a valid, unexpired parent-domain cookie session IS returned', () => {
  const sb = makeSandbox()
  const s = session()
  seedCookie(sb, JSON.stringify(s))
  const got = sb.window.readValidSession()
  assert.ok(got, 'a valid future-dated cookie session must be returned (not null)')
  assert.equal(got.access_token, s.access_token, 'the cookie access token is returned')
  assert.equal(got.refresh_token, s.refresh_token, 'the cookie refresh token is returned')
})

test('#3485: the bridge exposes the gate helpers to window', () => {
  const sb = makeSandbox()
  for (const name of ['readValidSession', 'storeSession', 'clearStoredSession', 'bounceToAuth']) {
    assert.equal(typeof sb.window[name], 'function', `window.${name} must be callable by the gate`)
  }
  assert.equal(sb.bridge.COOKIE_NAME, COOKIE_NAME, 'the shared cookie name is exposed')
})

// ── (1) a legacy-only localStorage session is never returned unshared ───────

test('#3485: a legacy-only session that CANNOT be shared is never returned', () => {
  const sb = makeSandbox()
  const legacy = JSON.stringify(oversizedSession())
  sb.ls.api.setItem(PROD_LEGACY_KEY, legacy)
  const got = sb.window.readValidSession()
  assert.equal(got, null,
    'the session lives only in origin-scoped localStorage and the cookie write did ' +
    'not land — reporting it IS the tortoise → app → tortoise loop')
  assert.equal(cookieValue(sb, COOKIE_NAME), null, 'the oversized cookie write must not have landed')
  assert.equal(sb.ls.api.getItem(PROD_LEGACY_KEY), legacy,
    'the only surviving copy of the credential must not be destroyed')
})

test('#3485: a legacy-only session that CAN be shared IS shared, then returned', () => {
  const sb = makeSandbox()
  const s = session()
  sb.ls.api.setItem(PROD_LEGACY_KEY, JSON.stringify(s))
  const got = sb.window.readValidSession()
  assert.ok(got, 'a shareable legacy session must be migrated into the cookie and returned')
  assert.equal(got.access_token, s.access_token)
  assert.equal(JSON.parse(cookieValue(sb, COOKIE_NAME)).access_token, s.access_token,
    'the parent-domain cookie must carry the legacy session')
  assert.equal(sb.ls.api.getItem(PROD_LEGACY_KEY), null,
    'the legacy copy is dropped once it is shared')
})

test('#3485: a non-session legacy value never poisons the shared cookie (gate path)', () => {
  const sb = makeSandbox()
  // Valid JSON, object-shaped, future-dated — but no access_token, so it is not
  // a session. It must never reach the parent-domain cookie; a cookie carrying
  // it would outrank a real session under the second legacy key.
  const junk = JSON.stringify({ expires_at: futureExpiry() })
  sb.ls.api.setItem(PROD_LEGACY_KEY, junk)
  const got = sb.window.readValidSession()
  assert.equal(got, null, 'a non-session must never be reported as a session')
  assert.equal(cookieValue(sb, COOKIE_NAME), null,
    'a value that is not a session must never be written to the parent-domain cookie')
  assert.equal(sb.ls.api.getItem(PROD_LEGACY_KEY), null, 'the junk is dropped')
})

// ── (2) expired / unparseable cookie → null ─────────────────────────────────

test('#3485: an expired, expiry-less, unparseable, or token-less cookie session yields null', () => {
  const cases = [
    ['expired', JSON.stringify(session({ expires_at: pastExpiry() }))],
    ['no expires_at', JSON.stringify({ access_token: 'a', refresh_token: 'r' })],
    ['not JSON', 'this-is-not-json'],
    ['no access_token', JSON.stringify({ expires_at: futureExpiry() })],
    ['no refresh_token', JSON.stringify({ access_token: 'a', expires_at: futureExpiry() })],
  ]
  for (const [label, raw] of cases) {
    const sb = makeSandbox()
    seedCookie(sb, raw)
    assert.equal(sb.window.readValidSession(), null, `${label}: must yield null`)
  }
})

// The cycle-5 P1 (found by a fresh reviewer): supabase-js's own _isValidSession
// additionally requires a `refresh_token` KEY, and its load path _removeSession()s
// the stored session when it is missing. The dashboard mounts supabase-js, so a
// cookie this bridge ACCEPTS but that consumer DELETES is a live loop: the head
// gate passes, the mount gate wipes the cookie, the app bounces to /auth, and the
// migration re-creates the same cookie from the legacy key kept because the
// unconfirmable write never dropped it. The gate must accept only what the
// consumer accepts.
test('#3485: a cookie without a refresh_token is NOT a session (the consumer would delete it)', () => {
  const sb = makeSandbox()
  seedCookie(sb, JSON.stringify({ access_token: 'access-1', expires_at: futureExpiry() }))
  assert.equal(sb.window.readValidSession(), null,
    'a cookie supabase-js would delete must never be reported as a session')
  // The same shape in localStorage is junk: it must not reach the shared cookie,
  // and it must not survive to be re-migrated on the next bounce.
  const sb2 = makeSandbox()
  sb2.ls.api.setItem(PROD_LEGACY_KEY,
    JSON.stringify({ access_token: 'access-2', expires_at: futureExpiry() }))
  assert.equal(sb2.window.readValidSession(), null, 'no refresh_token is still no session')
  assert.equal(cookieValue(sb2, COOKIE_NAME), null,
    'an unusable legacy value must never reach the parent-domain cookie')
  assert.equal(sb2.ls.api.getItem(PROD_LEGACY_KEY), null,
    'the unusable legacy key is dropped rather than re-migrated forever')
})

// ── (3) storeSession round-trip ─────────────────────────────────────────────

test('#3485: storeSession returns true only when the cookie now carries THAT session', () => {
  const sb = makeSandbox()
  const s = session()
  assert.equal(sb.window.storeSession(s), true, 'a durable write must report success')
  const stored = JSON.parse(cookieValue(sb, COOKIE_NAME))
  assert.equal(stored.access_token, s.access_token, 'the cookie carries the exact access token')
  assert.equal(stored.refresh_token, s.refresh_token, 'the cookie carries the exact refresh token')
})

test('#3485: storeSession is false for a token-less, expired, or non-round-tripping write', () => {
  // token-less
  const sb1 = makeSandbox()
  assert.equal(sb1.window.storeSession({ access_token: 'a', expires_at: futureExpiry() }), false,
    'a session without a refresh_token is not stored')
  // expired
  const sb2 = makeSandbox()
  assert.equal(sb2.window.storeSession(session({ expires_at: pastExpiry() })), false,
    'an expired session must not be reported as stored')
  // refused write (oversized), nothing pre-existing
  const sb3 = makeSandbox()
  assert.equal(sb3.window.storeSession(oversizedSession()), false,
    'a write the browser refused must not report success')
  assert.equal(cookieValue(sb3, COOKIE_NAME), null, 'nothing landed')
})

test('#3485: a refused write must not read back a PRE-EXISTING cookie as success', () => {
  const sb = makeSandbox()
  const existing = session()
  seedCookie(sb, JSON.stringify(existing))
  const oversized = oversizedSession()
  assert.equal(sb.window.storeSession(oversized), false,
    'reading back whatever cookie happened to be there is not a round-trip')
  const stored = JSON.parse(cookieValue(sb, COOKIE_NAME))
  assert.equal(stored.access_token, existing.access_token,
    'the pre-existing session is untouched')
  assert.notEqual(stored.access_token, oversized.access_token,
    'the new session was never stored, so it must not be claimed as stored')
})

// ── (4) an expired legacy session never displaces / invalidates a cookie ─────

test('#3485: a valid cookie survives an EXPIRED legacy session in localStorage', () => {
  const sb = makeSandbox()
  const cookie = session({ access_token: 'cookie-live' })
  seedCookie(sb, JSON.stringify(cookie))
  sb.ls.api.setItem(PROD_LEGACY_KEY,
    JSON.stringify(session({ access_token: 'legacy-dead', expires_at: pastExpiry() })))
  const got = sb.window.readValidSession()
  assert.ok(got, 'the valid parent-domain cookie must still be read')
  assert.equal(got.access_token, 'cookie-live', 'the expired legacy must not displace it')
  assert.equal(JSON.parse(cookieValue(sb, COOKIE_NAME)).access_token, 'cookie-live',
    'the cookie bytes must be unchanged')
})

test('#3485: an expired legacy never overwrites a cookie merely for expiring later', () => {
  const sb = makeSandbox()
  const now = Date.now()
  // The cookie is present and carries an access_token (so it is "usable"), but
  // it is itself expired. The legacy session is ALSO expired, yet its
  // expires_at is later — without the "legacy must still be usable" guard the
  // later value would overwrite the cookie.
  seedCookie(sb, JSON.stringify(session({ access_token: 'cookie-older', expires_at: Math.floor((now - 7200_000) / 1000) })))
  sb.ls.api.setItem(PROD_LEGACY_KEY,
    JSON.stringify(session({ access_token: 'legacy-newer', expires_at: Math.floor((now - 60_000) / 1000) })))
  sb.window.readValidSession()
  assert.equal(JSON.parse(cookieValue(sb, COOKIE_NAME)).access_token, 'cookie-older',
    'an expired legacy session must not displace the cookie just because it expires later')
})

test('#3485: a NEWER, still-usable legacy session DOES take over the cookie', () => {
  // The converse of the test above: a live legacy session with a later expiry
  // is a genuine refresh of a stale cookie and must win (this proves the guard
  // above refuses expired values, not every value).
  const sb = makeSandbox()
  seedCookie(sb, JSON.stringify(session({ access_token: 'cookie-stale', expires_at: futureExpiry(3600_000) })))
  sb.ls.api.setItem(PROD_LEGACY_KEY,
    JSON.stringify(session({ access_token: 'legacy-fresh', expires_at: futureExpiry(2 * DAY_MS) })))
  const got = sb.window.readValidSession()
  assert.equal(got.access_token, 'legacy-fresh', 'a newer, unexpired legacy session must be adopted')
  assert.equal(JSON.parse(cookieValue(sb, COOKIE_NAME)).access_token, 'legacy-fresh')
})

// ── (5) a corrupt legacy value must not stop the cookie being read ──────────

test('#3485: a valid-JSON legacy with an unpaired surrogate does not stop the cookie read', () => {
  const sb = makeSandbox()
  const cookie = session()
  seedCookie(sb, JSON.stringify(cookie))
  // JSON.parse accepts the lone high surrogate, but encodeURIComponent(value)
  // throws URIError on it — the corrupt-value path that must stay contained.
  const legacy =
    '{"access_token":"legacy-live","refresh_token":"r","expires_at":' +
    futureExpiry(2 * DAY_MS) + ',"note":"\uD800"}'
  assert.ok(legacy.includes('\uD800'), 'fixture must carry a real unpaired surrogate unit')
  assert.doesNotThrow(() => JSON.parse(legacy), 'the fixture is valid JSON (parses)')
  assert.throws(() => encodeURIComponent(legacy), /URIError|malformed/,
    'the fixture must trigger the URIError the bridge has to absorb')
  sb.ls.api.setItem(PROD_LEGACY_KEY, legacy)

  let got
  assert.doesNotThrow(() => { got = sb.window.readValidSession() },
    'a corrupt legacy value must never escape the bridge (never break the page)')
  assert.ok(got, 'the valid parent-domain cookie must still be read')
  assert.equal(got.access_token, cookie.access_token,
    'the corrupt value was never written; the cookie still holds the real session')
  assert.ok(!String(cookieValue(sb, COOKIE_NAME)).includes('legacy-live'),
    'a value that cannot be encoded must never reach the shared cookie')
})

// ── (6) the implicit-flow fragment is stripped only when the write landed ───

test('#3485: a landed fragment write strips the fragment exactly once', () => {
  const hash =
    '#access_token=' + encodeURIComponent('frag-access') +
    '&refresh_token=' + encodeURIComponent('frag-refresh') +
    '&expires_at=' + futureExpiry() + '&expires_in=3600&token_type=bearer'
  const sb = makeSandbox({ hash })
  assert.equal(sb.replaceStateCalls.length, 1, 'a session that landed in the cookie strips the fragment')
  assert.equal(JSON.parse(cookieValue(sb, COOKIE_NAME)).access_token, 'frag-access')
})

test('#3485: a refused fragment write leaves the fragment untouched (the only copy survives)', () => {
  const hash =
    '#access_token=' + 'A'.repeat(COOKIE_BYTE_CAP + 200) +
    '&refresh_token=r&expires_at=' + futureExpiry()
  const sb = makeSandbox({ hash })
  assert.equal(sb.replaceStateCalls.length, 0,
    'storeSession returned false, so erasing the fragment would strand the visitor with nothing stored')
  assert.equal(cookieValue(sb, COOKIE_NAME), null, 'the oversized write did not land')
})

test('#3485: an EXPIRED fragment is not stripped (it was never stored)', () => {
  const hash = '#access_token=a&refresh_token=r&expires_at=' + pastExpiry()
  const sb = makeSandbox({ hash })
  assert.equal(sb.replaceStateCalls.length, 0,
    'an expired fragment must survive rather than be silently discarded')
})

// ── migrateLegacySession (the createTortoiseSupabaseClient / body path) ─────

test('#3485 (body path): a valid legacy session migrates to the cookie and is dropped', () => {
  const sb = makeSandbox({ supabase: { createClient: () => ({ ok: true }) } })
  const s = session()
  sb.ls.api.setItem(PROD_LEGACY_KEY, JSON.stringify(s))
  const client = sb.window.createTortoiseSupabaseClient(PROD_URL, 'anon-key')
  assert.ok(client, 'the factory returns a client when window.supabase is loaded')
  assert.equal(JSON.parse(cookieValue(sb, COOKIE_NAME)).access_token, s.access_token,
    'the legacy session must reach the shared cookie')
  assert.equal(sb.ls.api.getItem(PROD_LEGACY_KEY), null, 'the legacy copy is dropped once shared')
})

test('#3485 (body path): the local-host legacy key is also migrated', () => {
  const sb = makeSandbox({ supabase: { createClient: () => ({}) } })
  const s = session()
  sb.ls.api.setItem(LOCAL_LEGACY_KEY, JSON.stringify(s))
  sb.window.createTortoiseSupabaseClient(LOCAL_URL, 'anon-key')
  assert.equal(JSON.parse(cookieValue(sb, COOKIE_NAME)).access_token, s.access_token,
    'the supabase-js derived local key (sb-127-…) must migrate too')
})

test('#3485 (body path): a non-session legacy value never poisons the shared cookie', () => {
  const sb = makeSandbox({ supabase: { createClient: () => ({}) } })
  const junk = JSON.stringify({ expires_at: futureExpiry() }) // object, no access_token
  sb.ls.api.setItem(PROD_LEGACY_KEY, junk)
  sb.window.createTortoiseSupabaseClient(PROD_URL, 'anon-key')
  assert.equal(cookieValue(sb, COOKIE_NAME), null,
    'a value that is not a session must never be written to the parent-domain cookie')
  assert.equal(sb.ls.api.getItem(PROD_LEGACY_KEY), null, 'the junk is dropped')
})

test('#3485 (body path): a refused write never destroys the only copy', () => {
  const sb = makeSandbox({ supabase: { createClient: () => ({}) } })
  const legacy = JSON.stringify(oversizedSession())
  sb.ls.api.setItem(PROD_LEGACY_KEY, legacy)
  sb.window.createTortoiseSupabaseClient(PROD_URL, 'anon-key')
  assert.equal(cookieValue(sb, COOKIE_NAME), null, 'the oversized write did not land')
  assert.equal(sb.ls.api.getItem(PROD_LEGACY_KEY), legacy,
    'the only surviving copy of the credential must not be destroyed')
})

test('#3485 (body path): an expired legacy never displaces a valid cookie', () => {
  const sb = makeSandbox({ supabase: { createClient: () => ({}) } })
  const cookie = session({ access_token: 'cookie-live' })
  seedCookie(sb, JSON.stringify(cookie))
  sb.ls.api.setItem(PROD_LEGACY_KEY,
    JSON.stringify(session({ access_token: 'legacy-dead', expires_at: pastExpiry() })))
  sb.window.createTortoiseSupabaseClient(PROD_URL, 'anon-key')
  assert.equal(JSON.parse(cookieValue(sb, COOKIE_NAME)).access_token, 'cookie-live',
    'the body path must apply the same expiry guard as the gate path')
})

// ── #3951: a LOSSY-BUT-LANDED write must still clean up ─────────────────────
//
// migrateLegacySession()/migrateLegacyKeysToCookie() documented a hygiene step
// ("drop [the legacy key] whether or not a cookie was already present") that
// its own control flow contradicted: the equality check
// `readCookie(COOKIE_NAME) !== legacy` is FALSE for every session the size
// guard transforms (the strip makes the stored value a different string), so
// the early return fired and the trailing removeItem never ran. The legacy key
// — the UNTRANSFORMED copy, carrying provider_token / provider_refresh_token /
// identities — stayed readable in localStorage indefinitely.
//
// The check must key on whether the write LANDED, not on whether it
// round-tripped byte-identically. The safety direction is unchanged and is what
// the two cases below separate: a landed write (lossy or not) means the new
// credential is durably stored, so the stale-secret original goes; a REFUSED
// write means the original is still the only copy, so it stays (#3503) — but
// its stale provider tokens are stripped in place when that scrub can be
// performed, so no post-migration path this code controls leaves a provider
// leaves a provider token readable.

test('#3951 (body path): a lossy-but-landed write drops the legacy key and its provider tokens', () => {
  const sb = makeSandbox({ supabase: { createClient: () => ({}) } })
  const s = lossySession({ access_token: 'lossy-access', refresh_token: 'lossy-refresh' })
  sb.ls.api.setItem(PROD_LEGACY_KEY, JSON.stringify(s))
  sb.window.createTortoiseSupabaseClient(PROD_URL, 'anon-key')

  const stored = cookieValue(sb, COOKIE_NAME)
  assert.ok(stored, 'the transformed session must still land in the cookie')
  assert.notEqual(stored, JSON.stringify(s),
    'precondition: the size guard transformed the value, so it is NOT byte-identical to the legacy string')
  const parsed = JSON.parse(stored)
  assert.equal(parsed.access_token, 'lossy-access',
    'the size guard keeps the session credential (access_token)')
  assert.equal(parsed.refresh_token, 'lossy-refresh',
    'the size guard keeps the session credential (refresh_token)')
  assert.equal(parsed.provider_token, undefined, 'the size guard strips the provider token')

  assert.equal(sb.ls.api.getItem(PROD_LEGACY_KEY), null,
    'a write that LANDED (even lossily) must drop the legacy key — it is the only copy of the ' +
    'stale provider token the hygiene step exists to remove')
})

test('#3951 (gate path): a lossy-but-landed write drops the legacy key too', () => {
  const sb = makeSandbox()
  const s = lossySession({ access_token: 'gate-lossy-access', refresh_token: 'gate-lossy-refresh' })
  sb.ls.api.setItem(PROD_LEGACY_KEY, JSON.stringify(s))

  const got = sb.window.readValidSession()
  assert.ok(got, 'the landed session must be readable from the cookie')
  assert.equal(got.access_token, 'gate-lossy-access', 'the gate reads the transformed session')
  assert.equal(sb.ls.api.getItem(PROD_LEGACY_KEY), null,
    'the gate path (migrateLegacyKeysToCookie) has the same defect and must drop the legacy key')
})

test('#3951 (body path): a REFUSED write keeps the only copy but strips its provider tokens', () => {
  const sb = makeSandbox({ supabase: { createClient: () => ({}) } })
  const s = session({
    access_token: 'S'.repeat(COOKIE_BYTE_CAP + 200), // still over cap after the size guard
    refresh_token: 'refused-refresh',
    provider_token: 'REFUSED-PROVIDER-SECRET',
    provider_refresh_token: 'REFUSED-PROVIDER-REFRESH-SECRET',
  })
  sb.ls.api.setItem(PROD_LEGACY_KEY, JSON.stringify(s))
  sb.window.createTortoiseSupabaseClient(PROD_URL, 'anon-key')

  assert.equal(cookieValue(sb, COOKIE_NAME), null, 'the refused write must not have landed')
  const kept = sb.ls.api.getItem(PROD_LEGACY_KEY)
  assert.ok(kept, 'the only surviving copy of the credential must not be destroyed (#3503)')
  const parsed = JSON.parse(kept)
  assert.equal(parsed.refresh_token, 'refused-refresh',
    'the session credential survives, so the visitor is recoverable rather than stranded')
  assert.equal(parsed.provider_token, undefined,
    'the stale provider token must not be left readable in localStorage')
  assert.equal(parsed.provider_refresh_token, undefined,
    'the stale provider refresh token must not be left readable either')
})

test('#3951 (gate path): a REFUSED write keeps the session but strips its provider tokens', () => {
  const sb = makeSandbox()
  const s = session({
    access_token: 'G'.repeat(COOKIE_BYTE_CAP + 200),
    refresh_token: 'gate-refused-refresh',
    provider_token: 'GATE-REFUSED-PROVIDER-SECRET',
  })
  sb.ls.api.setItem(PROD_LEGACY_KEY, JSON.stringify(s))

  assert.equal(sb.window.readValidSession(), null,
    'a session the cookie cannot hold is not reported as shared')
  const kept = sb.ls.api.getItem(PROD_LEGACY_KEY)
  assert.ok(kept, 'the gate path must not destroy the only copy')
  const parsed = JSON.parse(kept)
  assert.equal(parsed.refresh_token, 'gate-refused-refresh', 'the credential survives')
  assert.equal(parsed.provider_token, undefined, 'the stale provider token is stripped')
})

// ── #3951 review round 1: the `else` branches, and "did THIS write land" ────
//
// Round-1 reviewers found two gaps the first four tests did not cover:
//  (a) both `else` branches (cookie PRESENT, legacy NEWER) carry the same
//      defect and were untested — reverting only those two sites left the suite
//      green; and
//  (b) a read-back-only confirmation cannot tell "the write I just issued
//      landed" from "an equivalent session was already in the cookie", so a
//      REFUSED or browser-DROPPED write over a matching cookie would destroy the
//      only copy of the newer credential — a #3503 regression. The confirmation
//      now requires the cookie to have CHANGED, which is what distinguishes
//      them (`writeLanded`'s token comparison corroborates but is not decisive).
//
// `padding` below is a NON-STRIPPABLE top-level field: the size guard removes
// provider tokens / identities / metadata, so only bulk it does not know about
// can still exceed the cap and force a refusal while the cookie holds the same
// access_token/refresh_token pair.

test('#3951 (body path, cookie present + newer legacy): a lossy-but-landed write drops the legacy key', () => {
  const sb = makeSandbox({ supabase: { createClient: () => ({}) } })
  seedCookie(sb, JSON.stringify(session({ access_token: 'cookie-stale', expires_at: futureExpiry(3600_000) })))
  const s = lossySession({ access_token: 'lossy-newer', refresh_token: 'lossy-newer-r', expires_at: futureExpiry(2 * DAY_MS) })
  sb.ls.api.setItem(PROD_LEGACY_KEY, JSON.stringify(s))
  sb.window.createTortoiseSupabaseClient(PROD_URL, 'anon-key')

  assert.equal(JSON.parse(cookieValue(sb, COOKIE_NAME)).access_token, 'lossy-newer',
    'the newer legacy session must take over the cookie')
  assert.equal(sb.ls.api.getItem(PROD_LEGACY_KEY), null,
    'the else branch (cookie present, legacy newer) has the same defect and must drop the legacy key')
})

test('#3951 (gate path, cookie present + newer legacy): a lossy-but-landed write drops the legacy key', () => {
  const sb = makeSandbox()
  seedCookie(sb, JSON.stringify(session({ access_token: 'cookie-stale', expires_at: futureExpiry(3600_000) })))
  const s = lossySession({ access_token: 'gate-lossy-newer', refresh_token: 'gate-lossy-newer-r', expires_at: futureExpiry(2 * DAY_MS) })
  sb.ls.api.setItem(PROD_LEGACY_KEY, JSON.stringify(s))

  assert.equal(sb.window.readValidSession().access_token, 'gate-lossy-newer',
    'the newer legacy session must take over the cookie')
  assert.equal(sb.ls.api.getItem(PROD_LEGACY_KEY), null,
    'the gate else branch must drop the legacy key too')
})

test('#3951: a REFUSED write over an equivalent pre-existing session must NOT destroy the legacy copy (#3503)', () => {
  const sb = makeSandbox({ supabase: { createClient: () => ({}) } })
  // The cookie already holds THIS session (same access_token + refresh_token)
  // but an older expires_at, so a read-back-only confirmation would see the
  // matching cookie and call the write "landed", then destroy the only copy of
  // the newer expiry. The write is REFUSED (non-strippable padding keeps it over
  // the cap), so the copy must survive — scrubbed.
  seedCookie(sb, JSON.stringify(session({
    access_token: 'same-access', refresh_token: 'same-refresh', expires_at: futureExpiry(3600_000),
  })))
  const newerExp = futureExpiry(2 * DAY_MS)
  sb.ls.api.setItem(PROD_LEGACY_KEY, JSON.stringify(session({
    access_token: 'same-access', refresh_token: 'same-refresh', expires_at: newerExp,
    padding: 'x'.repeat(6000), provider_token: 'SAME-TOKENS-PROVIDER-SECRET',
  })))
  sb.window.createTortoiseSupabaseClient(PROD_URL, 'anon-key')

  assert.equal(JSON.parse(cookieValue(sb, COOKIE_NAME)).access_token, 'same-access',
    'precondition: the cookie is byte-unchanged by the refused write')
  const kept = sb.ls.api.getItem(PROD_LEGACY_KEY)
  assert.ok(kept, 'a REFUSED write must not destroy the only copy of the newer credential (#3503)')
  const parsed = JSON.parse(kept)
  assert.equal(parsed.expires_at, newerExp, 'the newer credential is the copy that survives')
  assert.equal(parsed.provider_token, undefined,
    'and its stale provider token is still scrubbed — the copy is not the leak')
})

test('#3951: a REFUSED write over a cookie sharing access_token but NOT refresh_token keeps the legacy', () => {
  const sb = makeSandbox({ supabase: { createClient: () => ({}) } })
  // #3485 discipline: a prior cookie sharing the access_token but carrying a
  // DIFFERENT refresh_token is NOT this write — the only copy of the NEW
  // refresh_token must survive. This fixture reaches the refusal via the size
  // guard, so it pins the REFUSED path. `writeLanded`'s token comparison is
  // corroborating only: a changed cookie carrying different tokens needs a
  // concurrent writer, which the single-threaded harness cannot stage.
  seedCookie(sb, JSON.stringify(session({
    access_token: 'same-access', refresh_token: 'OLD-refresh', expires_at: futureExpiry(3600_000),
  })))
  sb.ls.api.setItem(PROD_LEGACY_KEY, JSON.stringify(session({
    access_token: 'same-access', refresh_token: 'NEW-refresh', expires_at: futureExpiry(2 * DAY_MS),
    padding: 'x'.repeat(6000), provider_token: 'REFRESH-MISMATCH-PROVIDER-SECRET',
  })))
  sb.window.createTortoiseSupabaseClient(PROD_URL, 'anon-key')

  assert.equal(JSON.parse(cookieValue(sb, COOKIE_NAME)).refresh_token, 'OLD-refresh',
    'the refused write left the pre-existing cookie in place')
  const kept = sb.ls.api.getItem(PROD_LEGACY_KEY)
  assert.ok(kept, 'the only copy of the NEW refresh_token must survive a refused write')
  assert.equal(JSON.parse(kept).refresh_token, 'NEW-refresh', 'the new refresh token is preserved')
  assert.equal(JSON.parse(kept).provider_token, undefined, 'and the stale provider token is scrubbed')
})

test('#3951: an unencodable legacy value (setItem throws) still gets its provider token scrubbed', () => {
  const sb = makeSandbox()
  // JSON.parse accepts the lone high surrogate, so the value IS a session — but
  // encodeURIComponent inside the write throws URIError. That is a write that
  // did not land, so the legacy copy is kept... and its provider token must not
  // be left readable just because the write path threw past the scrub.
  const legacy =
    '{"access_token":"throw-access","refresh_token":"r","expires_at":' +
    futureExpiry(2 * DAY_MS) + ',"provider_token":"THROW-PATH-PROVIDER-SECRET","note":"\uD800"}'
  assert.doesNotThrow(() => JSON.parse(legacy), 'the fixture is valid JSON (parses)')
  assert.throws(() => encodeURIComponent(legacy), /URIError|malformed/,
    'the fixture must trigger the URIError the write path has to absorb')
  sb.ls.api.setItem(PROD_LEGACY_KEY, legacy)

  assert.doesNotThrow(() => sb.window.readValidSession(),
    'an unencodable legacy value must never escape the bridge')
  const kept = sb.ls.api.getItem(PROD_LEGACY_KEY)
  assert.ok(kept, 'nothing landed, so the copy is kept')
  assert.equal(JSON.parse(kept).provider_token, undefined,
    'the stale provider token is scrubbed even on the throw path — it is never left readable')
})

test('#3951 (gate path, cookie present + newer legacy): a REFUSED write keeps the newer credential', () => {
  const sb = makeSandbox()
  // The gate `else` branch is the one site whose REFUSED outcome the landed
  // tests above do not reach. Dropping the `continue` there would fall through
  // to removeItem() and destroy the only copy of the newer credential — the
  // exact #3503 class — while the cookie already holds an equivalent session.
  seedCookie(sb, JSON.stringify(session({
    access_token: 'same-access', refresh_token: 'same-refresh', expires_at: futureExpiry(3600_000),
  })))
  const newerExp = futureExpiry(2 * DAY_MS)
  sb.ls.api.setItem(PROD_LEGACY_KEY, JSON.stringify(session({
    access_token: 'same-access', refresh_token: 'same-refresh', expires_at: newerExp,
    padding: 'x'.repeat(6000), provider_token: 'GATE-ELSE-REFUSED-PROVIDER-SECRET',
  })))
  sb.window.readValidSession()

  const kept = sb.ls.api.getItem(PROD_LEGACY_KEY)
  assert.ok(kept, 'the gate else branch must retain the only copy of the newer credential on a refused write')
  const parsed = JSON.parse(kept)
  assert.equal(parsed.expires_at, newerExp, 'the newer credential is the copy that survives')
  assert.equal(parsed.provider_token, undefined, 'and its stale provider token is scrubbed')
})

test('#3951: a write the browser DROPS (cap below the derived limit) is not a landed write', () => {
  // The stub's setItem exposes no write-issued signal, so the drop is detected
  // solely by the cookie being left UNCHANGED. A browser enforcing a smaller
  // per-cookie cap than the 4096 the code derives SIZE_CAP from can drop an
  // issued write; the confirmation must catch that: this is not the write, and
  // the legacy copy (the only place the NEW refresh_token lives) must survive —
  // scrubbed.
  const sb = makeSandbox({ cap: 1000, supabase: { createClient: () => ({}) } })
  seedCookie(sb, JSON.stringify(session({
    access_token: 'same-access', refresh_token: 'OLD-refresh', expires_at: futureExpiry(3600_000),
  })))
  sb.ls.api.setItem(PROD_LEGACY_KEY, JSON.stringify(session({
    access_token: 'same-access', refresh_token: 'NEW-refresh', expires_at: futureExpiry(2 * DAY_MS),
    filler: 'x'.repeat(1200), provider_token: 'DROPPED-WRITE-PROVIDER-SECRET',
  })))
  sb.window.createTortoiseSupabaseClient(PROD_URL, 'anon-key')

  assert.equal(JSON.parse(cookieValue(sb, COOKIE_NAME)).refresh_token, 'OLD-refresh',
    'precondition: the browser dropped the issued write, so the cookie is unchanged')
  const kept = sb.ls.api.getItem(PROD_LEGACY_KEY)
  assert.ok(kept, 'a dropped write is not a landed write — the only copy of the NEW refresh_token must survive')
  assert.equal(JSON.parse(kept).refresh_token, 'NEW-refresh', 'the new refresh token is preserved')
  assert.equal(JSON.parse(kept).provider_token, undefined, 'and the stale provider token is scrubbed')
})

test('#3951: a DROPPED write over an EXPIRED equivalent cookie keeps the only gate-usable copy', () => {
  // The cookie already holds this session's token pair but is EXPIRED, and the
  // issued write is DROPPED (browser cap below the code's derived limit), so the
  // cookie is unchanged. A read-back that only checked the tokens would call it
  // landed and delete the legacy — the only copy the gate (which rejects an
  // expired session) would ever accept. The write must have CHANGED the cookie.
  const sb = makeSandbox({ cap: 1000, supabase: { createClient: () => ({}) } })
  seedCookie(sb, JSON.stringify(session({
    access_token: 'same-access', refresh_token: 'same-refresh', expires_at: pastExpiry(),
  })))
  const newerExp = futureExpiry(2 * DAY_MS)
  sb.ls.api.setItem(PROD_LEGACY_KEY, JSON.stringify(session({
    access_token: 'same-access', refresh_token: 'same-refresh', expires_at: newerExp,
    filler: 'x'.repeat(1200), provider_token: 'EXPIRED-EQUIVALENT-PROVIDER-SECRET',
  })))
  sb.window.createTortoiseSupabaseClient(PROD_URL, 'anon-key')

  const cookie = JSON.parse(cookieValue(sb, COOKIE_NAME))
  assert.ok(cookie.expires_at * 1000 <= Date.now(),
    'precondition: the surviving cookie is expired, so the gate rejects it')
  const kept2 = sb.ls.api.getItem(PROD_LEGACY_KEY)
  assert.ok(kept2, 'the only gate-usable copy of the credential must survive a dropped write')
  assert.equal(JSON.parse(kept2).expires_at, newerExp, 'the unexpired copy is preserved')
  assert.equal(JSON.parse(kept2).provider_token, undefined, 'and the stale provider token is scrubbed')
})

test('#3951: a DROPPED write over a cookie sharing refresh_token but not access_token keeps the legacy', () => {
  // The mirror of the test above on the access_token dimension: the surviving
  // cookie shares only the refresh_token, so the changed-cookie check must
  // still refuse to call the dropped write a landing.
  const sb = makeSandbox({ cap: 1000, supabase: { createClient: () => ({}) } })
  seedCookie(sb, JSON.stringify(session({
    access_token: 'OLD-access', refresh_token: 'same-refresh', expires_at: futureExpiry(3600_000),
  })))
  sb.ls.api.setItem(PROD_LEGACY_KEY, JSON.stringify(session({
    access_token: 'NEW-access', refresh_token: 'same-refresh', expires_at: futureExpiry(2 * DAY_MS),
    filler: 'x'.repeat(1200), provider_token: 'ACCESS-MISMATCH-PROVIDER-SECRET',
  })))
  sb.window.createTortoiseSupabaseClient(PROD_URL, 'anon-key')

  assert.equal(JSON.parse(cookieValue(sb, COOKIE_NAME)).access_token, 'OLD-access',
    'precondition: the dropped write left the cookie unchanged')
  const kept = sb.ls.api.getItem(PROD_LEGACY_KEY)
  assert.ok(kept, 'the only copy of NEW-access must survive')
  assert.equal(JSON.parse(kept).access_token, 'NEW-access', 'the new access token is preserved')
  assert.equal(JSON.parse(kept).provider_token, undefined, 'and the stale provider token is scrubbed')
})

test('#3951: the scrub must not clobber a concurrent tab\u2019s newer legacy session', () => {
  // localStorage is shared across tabs, and this migration already assumes a
  // stale cached tab can write a NEWER legacy session. The scrub's write-back
  // must act only on the value it actually inspected: if a concurrent tab has
  // replaced it, the newer session must survive untouched rather than be
  // reverted to a scrubbed snapshot (which would destroy its only copy).
  const sb = makeSandbox({ supabase: { createClient: () => ({}) } })
  const oldLegacy = JSON.stringify(session({
    access_token: 'H'.repeat(COOKIE_BYTE_CAP + 200), // over cap → refused, so the scrub runs
    refresh_token: 'old-refresh', expires_at: futureExpiry(2 * DAY_MS),
    provider_token: 'OLD-TAB-PROVIDER-SECRET',
  }))
  const newerLegacy = JSON.stringify(session({
    access_token: 'CONCURRENT-new-access', refresh_token: 'concurrent-refresh',
    expires_at: futureExpiry(2 * DAY_MS),
  }))
  sb.ls.api.setItem(PROD_LEGACY_KEY, oldLegacy)
  // Simulate the concurrent tab: the migration's FIRST read sees the old value,
  // and the replace happens right after it — so the scrub's re-read sees the
  // newer session and must decline to write.
  const api = sb.ls.api
  const realGetItem = api.getItem // capture the fn — window.localStorage IS api
  let firstRead = true
  sb.window.localStorage.getItem = (k) => {
    if (k === PROD_LEGACY_KEY && firstRead) {
      firstRead = false
      api.setItem(PROD_LEGACY_KEY, newerLegacy)
      return oldLegacy
    }
    return realGetItem.call(api, k)
  }
  sb.window.createTortoiseSupabaseClient(PROD_URL, 'anon-key')

  assert.equal(sb.ls.api.getItem(PROD_LEGACY_KEY), newerLegacy,
    'a concurrent tab\u2019s newer session must not be reverted to the scrubbed snapshot')
  assert.equal(JSON.parse(sb.ls.api.getItem(PROD_LEGACY_KEY)).access_token, 'CONCURRENT-new-access',
    'the newer credential survives — the only copy is not destroyed')
})

test('#3951: a refused write with nothing to scrub leaves the legacy bytes untouched', () => {
  // Non-canonical (spaced) JSON with no provider token: there is nothing to
  // scrub, so the retained copy is left byte-identical rather than needlessly
  // re-serialized (and its non-canonical bytes normalized).
  const sb = makeSandbox({ supabase: { createClient: () => ({}) } })
  const raw = '{ "access_token":"' + 'H'.repeat(COOKIE_BYTE_CAP + 200) +
    '", "refresh_token":"r", "expires_at":' + futureExpiry(2 * DAY_MS) + ' }'
  sb.ls.api.setItem(PROD_LEGACY_KEY, raw)
  sb.window.createTortoiseSupabaseClient(PROD_URL, 'anon-key')

  assert.equal(cookieValue(sb, COOKIE_NAME), null, 'the oversized write did not land')
  assert.equal(sb.ls.api.getItem(PROD_LEGACY_KEY), raw,
    'nothing to strip → the retained bytes are left exactly as they were')
})
