// apiPathResolvesToARoute.test.js — #4144.
//
// WHY THIS FILE EXISTS
// --------------------
// The dashboard talks to its OWN origin and nothing else: every call goes through
// the BFF, whose proxy route is `functions/api/v1/[[path]].ts` and which rebuilds
// the upstream URL as `${API_ORIGIN}/v1/${rest}`. A call site that forgets the
// `/v1/` segment therefore asks for a path with no Pages Function and gets a 404
// from Pages — which is exactly how the Backups card came to read as empty while
// backups existed:
//
//     GET https://app.premiselabs.co/api/backups?org_id=… 404 (Not Found)
//
// The `loadBackups` call site predated the #3501/#4054 migration to the proxy and
// was the ONE literal left without the prefix. The e2e/render harness could not
// catch it: it answers whatever the client asks for under `/api/`, so it is blind
// to whether a real route exists (that blindness is noted in issue #4144 — "the
// harness asserting it is not the same as a route existing").
//
// WHAT IS PINNED
// --------------
// Every LITERAL first argument of an `api(...)` call in the dashboard source must
// be servable: either it is on the `/v1/` proxy path (whose wildcard route exists
// by construction), or there is a real `functions/api/<path>.ts` file for it. This
// checks the filesystem rather than a prefix convention, so it catches BOTH
// failure modes — a dropped `/v1/` segment, and a typo'd or deleted BFF route.
//
// Mutation: change `api(\`/v1/backups${q}\`)` back to `/backups` and the first test
// fails naming the path; delete `functions/api/profile.ts` and the second fails.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const APP_DIR = join(HERE, '..')
const API_DIR = join(APP_DIR, 'functions', 'api')

// Every literal first argument of an `api(...)` call. The template-literal form is
// the common one (`api(\`/v1/team/keys${q}\`)`); the scan takes the whole quoted
// body and then truncates at the first `$`, so what is compared is the static
// prefix — the part that must resolve to a route. Calls written as
// `api(someVariable)` carry no literal and are out of scope.
function literalApiPaths(src) {
  const out = []
  const re = /\bapi\(\s*(?:`([^`]*)`|'([^']*)'|"([^"]*)")/g
  let m
  while ((m = re.exec(src)) !== null) {
    const body = m[1] ?? m[2] ?? m[3]
    out.push(body.split('$')[0])
  }
  return out
}

function candidates() {
  // main.jsx holds the dashboard's data calls. Kept explicit rather than globbing
  // every file: a new file with call sites should be added here deliberately.
  return ['main.jsx'].map((f) => ({ file: f, paths: literalApiPaths(readFileSync(join(HERE, f), 'utf8')) }))
}

test('every literal api() path is servable by the BFF (a real route exists)', () => {
  const bad = []
  let total = 0
  for (const { file, paths } of candidates()) {
    total += paths.length
    for (const raw of paths) {
      const path = raw.split('?')[0]
      if (path.startsWith('/v1/')) continue // the proxy's wildcard route: functions/api/v1/[[path]].ts
      // Otherwise a dedicated BFF function must exist at functions/api/<path>.ts.
      const rel = path.replace(/^\//, '').replace(/\/$/, '')
      if (existsSync(join(API_DIR, `${rel}.ts`))) continue
      bad.push(`${file}: api('${raw}') → /api${path} has no route (no functions/api/${rel}.ts and not on /v1/)`)
    }
  }
  assert.ok(total >= 20, `expected to find the dashboard's api() call sites, found ${total}`)
  assert.deepEqual(bad, [], `api() paths that cannot be served:\n  ${bad.join('\n  ')}`)
})

test('the backups call site specifically asks for the /v1 proxy (regression: #4144)', () => {
  const src = readFileSync(join(HERE, 'main.jsx'), 'utf8')
  assert.ok(
    /api\(\s*`\/v1\/backups\$\{q\}`/.test(src),
    'loadBackups must call the /v1 proxy; a bare `/backups` has no Pages Function and 404s',
  )
  assert.ok(
    !/api\(\s*`\/backups\$\{q\}`/.test(src),
    'the bare `/backups` call shape is back — that is the #4144 bug',
  )
})
