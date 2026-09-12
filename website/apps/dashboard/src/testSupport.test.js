// testSupport.test.js — pins the shared stripComments contract (#3012).
//
// The quote-aware stripper is what every main.jsx vocabulary/tripwire guard
// depends on, so its behaviour is pinned here, co-located with the helper
// rather than privately inside one consumer. The four divergences that a
// future stripper upgrade must not silently regress: (i) URL-in-string
// survival, (ii) a // comment containing quotes/URLs, (iii) the escaped-slash
// regex limitation, (iv) /* */ block comments — plus the whole-file tail
// sentinel that catches an unterminated-/* truncation to EOF.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { stripComments } from './testSupport.js'

test('stripComments: (i) a URL string literal survives stripping intact', () => {
  const s = stripComments("const base = 'https://premiselabs.co/v1/team/keys'; // host comment\nconst x = 1")
  assert.ok(s.includes("'https://premiselabs.co/v1/team/keys'"), `URL literal must survive: ${JSON.stringify(s)}`)
  assert.ok(!s.includes('host comment'), 'the // comment after it must be stripped')
  assert.ok(s.includes('const x = 1'), 'code after the comment line survives')
})

test('stripComments: (ii) a // comment containing quotes/URLs is stripped', () => {
  // A comment body may itself hold quotes + a // — the comment branch runs
  // raw to the newline; the string branch must not have been entered.
  const s = stripComments('const a = 1; // she said "https://x.example/a//b" and // more\nconst b = 2')
  assert.ok(!s.includes('she said'), 'comment body with quotes/URLs must be stripped')
  assert.ok(!s.includes('and // more'), 'a second // inside the comment is still comment text')
  assert.ok(s.includes('const b = 2'))
})

test('stripComments: (iii) regex literals ending in escaped slashes — DOCUMENTED limitation', () => {
  // Accepted limitation (see the stripComments doc comment): the machine is
  // not a tokenizer, so `/https?:\/\//` exposes a `//` pair at the escaped-
  // slash/regex-close junction and the rest of THAT line is stripped as a
  // comment. This pins CURRENT behavior — only the same line is affected
  // (following lines survive), and a future tokenizer upgrade must update
  // this expectation deliberately.
  const out = stripComments('const re = /https?:\\/\\//; // keep-me?\nconst survivor = 1\n')
  assert.ok(!out.includes('keep-me?'), 'the regex close swallows the rest of its line (pinned limitation)')
  assert.ok(out.includes('const survivor = 1'), 'following lines survive')
})

test('stripComments: (iv) block comments die, inline // dies, whole-file scan is not truncated', () => {
  // Axis the three tests above do not cover: /* */ block comments, including
  // INLINE ones, and a trailing // on a line of code.
  assert.ok(!stripComments('/* header banner */\nconst a = 1').includes('header banner'),
    'a whole-line block comment is stripped')
  assert.ok(!stripComments('const a = 1; /* inline note */ const b = 2').includes('inline note'),
    'an INLINE block comment is stripped (the naive whole-line filter would keep it)')
  assert.ok(stripComments('const a = 1; /* inline note */ const b = 2').includes('const b = 2'),
    'code after an inline block comment survives')
  assert.ok(!stripComments('const a = 1 // trailing note').includes('trailing note'),
    'a TRAILING // is stripped (the naive whole-line filter would keep it)')

  // A `//` preceded by `:` is a URL scheme separator, NOT a comment start. This
  // is not a corner case: main.jsx renders URLs in JSX TEXT, so without this
  // the rest of such a line is silently deleted from the scanned view and live
  // copy on it becomes invisible to the source guards.
  const jsxUrl = stripComments('<li>Server URL: <code>https://api.premiselabs.co/mcp/</code></li>')
  assert.ok(jsxUrl.includes('https://api.premiselabs.co/mcp/'),
    'the URL body itself survives intact')
  assert.ok(jsxUrl.includes('</code></li>'),
    `a bare URL in JSX text must not be read as a comment: ${JSON.stringify(jsxUrl)}`)

  // SCAN-COVERAGE SENTINELS. An exposed `/*` swallows text up to the NEXT `*/`
  // — usually MID-FILE, not EOF. A single tail sentinel therefore misses it, so
  // pin six live-code tokens spread across the whole file (head, 20%, 40%,
  // 60%, 80%, tail). Any one going missing means the scanned region silently
  // shrank. RESIDUAL: a swallow bounded entirely within one of those bands can
  // still evade — closing that properly needs regex-literal awareness in the
  // stripper, which is issue #3102.
  const mainJsx = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')
  const stripped = stripComments(mainJsx)
  const SENTINELS = [
    // head: main.jsx no longer inlines the connector URL (it is CANONICAL_MCP_URL
    // in ./harnesses.js), so the old 'premiselabs.co/mcp/' pin is gone. Pin the
    // first LIVE (non-comment) line instead — comments are stripped, so a
    // comment token can never serve as a scan-coverage sentinel.
    ['head', "import React from 'react'"],
    ['20%', "detail.code === '"],
    ['40%', 'Your session ended — sign in again.'],
    ['60%', 'this API key'],
    ['80%', 'No organization'],
    ['tail', "createRoot(document.getElementById('root')).render(<App />)"],
  ]
  for (const [where, token] of SENTINELS) {
    assert.ok(stripped.includes(token),
      `${where} sentinel went missing — the scanned region silently shrank (mid-file /* swallow)`)
  }

  // ACCEPTED, UNPINNED limitation: a template literal with a NESTED backtick in
  // a `${...}` expression — the machine closes at the first unescaped backtick
  // and the tail is re-scanned as plain code, so a `//` there leaks into a
  // comment strip. Documented in testSupport.js; main.jsx does not use the
  // shape today. (Not asserted: pinning it would freeze a bug we intend to
  // remove when the stripper is upgraded — tracked by issue #3102.)
})
