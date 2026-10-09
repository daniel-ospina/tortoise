// userTokenRejection.test.js — #3559 review round 2 (P2-1, P2-4).
//
// `_shared/auth/supabase.ts::isUserTokenRejection` decides whether a 401/403
// from the `is_admin()` RPC is the USER's dead credential (a sign-out) or OUR
// infrastructure fault (503). The round-2 reviewer proved the real function
// lied in TWO ways, both of which this file pins:
//
//   P2-1 — it regex-tested the RAW body when `JSON.parse` failed, so a NON-JSON
//          config/WAF HTML error page containing `jwt` (word-bounded) or
//          `invalid token` was read as a session verdict. That is the exact
//          #3485 class: a deployment fault becomes "/admin bounces to sign-in"
//          and `/api/sb` clears a live session cookie.
//   P2-4 — its vocabulary was narrower than the provider's. `\bjwt\b` cannot
//          match GoTrue's `INVALID_JWT` (`_` is a word character, so there is
//          no word boundary before `jwt`), and `PGRST302` /
//          `UNUSABLE_CREDENTIAL` were not recognised at all — so a genuinely
//          rejected bearer was never signed out and the operator sat on a
//          permanent 503.
//
// This drives the REAL bundled TypeScript (esbuild is already a vite
// dependency), so a regression in the classifier itself fails HERE. The e2e
// suites cover the route wiring, but only a directly-loaded function can pin a
// non-JSON body, which the e2e mock cannot express.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { Buffer } from 'node:buffer'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { buildSync } from 'esbuild'

const here = dirname(fileURLToPath(import.meta.url))
const dashboardRoot = join(here, '..')

/** Bundle a TypeScript entry to an importable ES module (data: URL). */
async function loadTs(entry) {
  const out = buildSync({
    entryPoints: [join(dashboardRoot, entry)],
    bundle: true,
    format: 'esm',
    platform: 'neutral',
    target: 'es2022',
    write: false,
  })
  const code = out.outputFiles[0].text
  return import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`)
}

const classifier = await loadTs('functions/_shared/auth/supabase.ts')

// ── P2-1: an unreadable body must fail SAFE (a 503), never toward sign-out ──
test('P2-1: a non-JSON or unreadable 401/403 body is never a credential verdict', () => {
  const faults = [
    [401, ''],
    [401, '   '],
    [401, '<html><body>502 Bad Gateway</body></html>'],
    [401, '<html><body>Your jwt was rejected</body></html>'],
    [403, '<html>…invalid token…</html>'],
    [401, 'invalid token'],
    [401, 'jwt expired'],
    [403, 'invalid claim'],
    [401, '{"code":"PGRST30'], // truncated JSON
    [401, 'null'],
    [401, '"invalid token"'], // valid JSON, but a bare string — not an envelope
    [401, '[{"code":"PGRST301"}]'], // an array is not an error envelope
  ]
  for (const [status, body] of faults) {
    assert.equal(
      classifier.isUserTokenRejection(status, body),
      false,
      `status=${status} body=${JSON.stringify(body)} must be a FAULT (503), not a sign-out`,
    )
  }
})

test('P2-1: a JSON envelope that names a JWT failure is still a sign-out', () => {
  const rejections = [
    [401, JSON.stringify({ code: 'PGRST301', message: 'JWT expired' })],
    [403, JSON.stringify({ code: 'PGRST303', message: 'JWT claim validation failed' })],
    [401, JSON.stringify({ error: 'invalid token' })],
    [401, JSON.stringify({ message: 'JWT expired' })],
    [403, JSON.stringify({ error_description: 'invalid claim: missing sub claim' })],
  ]
  for (const [status, body] of rejections) {
    assert.equal(
      classifier.isUserTokenRejection(status, body),
      true,
      `status=${status} body=${body} must be a sign-out`,
    )
  }
})

// ── P2-4: the provider codes the free-text regex could not reach ────────────
test('P2-4: the provider codes the regex missed are recognised', () => {
  const codes = ['INVALID_JWT', 'PGRST302', 'UNUSABLE_CREDENTIAL', 'invalid_jwt', 'bad_jwt']
  for (const code of codes) {
    assert.equal(
      classifier.isUserTokenRejection(401, JSON.stringify({ code })),
      true,
      `code=${code} must be recognised as a rejected USER credential`,
    )
  }
})

// ── P2-4's other half: an UNRECOGNISED body still fails safe ────────────────
test('P2-4: an unrecognised code or a service-key fault stays a fault (503)', () => {
  const faults = [
    [401, JSON.stringify({ message: 'Invalid API key' })],
    [401, JSON.stringify({ message: 'Missing or invalid credentials' })],
    [403, JSON.stringify({ code: '42501', message: 'insufficient privileges' })],
    [401, JSON.stringify({ code: 'some_new_provider_code' })],
    [500, JSON.stringify({ code: 'PGRST301' })],
    [429, JSON.stringify({ code: 'PGRST301' })],
    [404, JSON.stringify({ code: 'PGRST301' })],
  ]
  for (const [status, body] of faults) {
    assert.equal(
      classifier.isUserTokenRejection(status, body),
      false,
      `status=${status} body=${body} must be a FAULT (503), never a sign-out`,
    )
  }
})

// ── Round 3 (#7749 residual): a bare `jwt` in a BENIGN envelope is not a verdict ──
//
// The message fallback matched `\bjwt\b` on its own. PostgREST answers a
// REQUEST-level parse error with `PGRST100`, and these three bodies name the
// term while saying nothing about a rejected credential: matching them clears a
// live session over a benign envelope — the #3485 direction (a fault read as a
// credential verdict). The term must co-occur with a rejection/expiry verb.
test('round 3: a benign `jwt` mention in a PGRST100 envelope stays a fault (503)', () => {
  const benign = [
    '{"code":"PGRST100","message":"Your jwt configuration was rotated by an administrator"}',
    '{"code":"PGRST100","message":"Contact support about your JWT settings"}',
    '{"code":"PGRST100","message":"unexpected \\"jwt\\" in query selector"}',
  ]
  for (const body of benign) {
    assert.equal(
      classifier.isUserTokenRejection(401, body),
      false,
      `body=${body} names \`jwt\` but reports no rejected credential — must be a FAULT (503)`,
    )
  }
})

// Positive control for the narrowing: the co-occurrence requirement must not
// kill the genuine signal. Each body carries the `jwt` term WITH a
// rejection/expiry verb, and each must still sign the user out.
test('round 3: a `jwt` term WITH a rejection/expiry verb is still a sign-out', () => {
  const rejections = [
    [401, JSON.stringify({ message: 'JWT expired' })],
    [401, JSON.stringify({ message: 'invalid JWT: unable to parse or verify signature' })],
    [401, JSON.stringify({ msg: 'JWT is malformed' })],
    [403, JSON.stringify({ error: 'jwt revoked' })],
    [401, JSON.stringify({ message: 'jwt missing' })],
  ]
  for (const [status, body] of rejections) {
    assert.equal(
      classifier.isUserTokenRejection(status, body),
      true,
      `status=${status} body=${body} must be a sign-out`,
    )
  }
})
