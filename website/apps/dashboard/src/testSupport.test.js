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
  // pin live-code tokens spread across the whole file. Any one going missing
  // means the scanned region silently shrank. Every token must be LIVE CODE
  // (comments are stripped, so a comment token can never work) and UNIQUE in
  // the stripped view — a token with N copies only trips if the swallow eats
  // every one of them. RESIDUAL: a swallow bounded entirely within one of the
  // bands can still evade — closing that properly needs regex-literal
  // awareness in the stripper, which is issue #3102.
  //
  // A sentinel at stripped offset 0 (e.g. the very first import) is VACUOUS: an
  // exposed `/*` starts at p > 0, and [p, q) can never remove offset 0. The
  // head pin therefore has to sit a little way in, not on line 1.
  const mainJsx = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')
  const stripped = stripComments(mainJsx)
  const SENTINELS = [
    // ~2.4% — the first named function declaration (unique).
    ['head', 'function OverviewElementSkeleton({ label }) {'],
    ['20%', "detail.code === '"],
    // ~40% — unique. (Was 'Your session ended — sign in again.', which occurs
    // TWICE — ~32.6% and ~33.2% — so it had to vanish from both to trip.)
    ['40%', 'async function logout() {'],
    // ~55% — a real mid-band pin. (The token that used to sit here, 'this API
    // key', first occurs at 1.4% and has 4 copies, so it pinned nothing near
    // 60% and could only trip if all 4 copies vanished. It was then
    // 'const sub = WIZARD_STEPS[wizardStep].sub' until #3725 moved that lede
    // decision into wizardFlow.js's `wizardStepSub` and the local binding was
    // renamed `headSub` — the sentinel moved with it.)
    ['55%', 'const headSub = wizardStepSub(wizardStep, { hasOrg: welcomeHasOrg, connected: serverHarnessConnected, buildFork: isBuildFork })'],
    // ~80% — unique. (Was 'No organization', which occurs FOUR times, at
    // 73.1/73.2/74.8/75.2%.)
    ['80%', '<button className="account-menu-create" onClick={openCreateTeamDialog}>'],
    ['tail', "createRoot(document.getElementById('root')).render(<App />)"],
  ]
  for (const [where, token] of SENTINELS) {
    assert.ok(mainJsx.includes(token),
      `${where} sentinel is not live code in main.jsx — a guard token that does not exist cannot pin coverage`)
    // UNIQUENESS is the property that makes a sentinel a sentinel: a token with
    // N copies only trips if a swallow happens to eat every one of them, so a
    // multi-copy entry silently pins a fraction of the band it claims. Two
    // entries above (40%, 80%) shipped with 2 and 4 copies respectively until a
    // confirmation review caught it — hence this assertion, which is what makes
    // the invariant above enforceable rather than aspirational.
    assert.equal(stripped.split(token).length - 1, 1,
      `${where} sentinel is not UNIQUE in the stripped view (${stripped.split(token).length - 1} copies) — a multi-copy token only trips if every copy is eaten; replace it with a unique live token`)
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

test('stripComments: (v) a JSX-text apostrophe does not open a phantom string', () => {
  // The bug this pins (found in re-review of #3012, measured on the real file):
  // treating every ' as a string delimiter meant an apostrophe in JSX TEXT
  // (`what's left`) opened a PHANTOM string that ran to the NEXT apostrophe —
  // spans of up to ~1.6 KB — with everything inside copied verbatim, so
  // comments in that span were never stripped. 56 of main.jsx's 134 block
  // comments survived, which inverted the caller's contract in BOTH
  // directions: a commented-out anchor could satisfy a presence ratchet, and a
  // JSX comment could spuriously trip a residual scan.
  //
  // A ' or " literal cannot span an unescaped newline, so the string branch now
  // ends at one; backticks still may (pinned by the template case below).
  const jsxText = "<div>what's left</div>\n{/* file your first memory */}\n<span>this Organization's graph</span>\n"
  const out = stripComments(jsxText)
  assert.ok(!out.includes('file your first memory'),
    'a comment on a line following a JSX-text apostrophe must still be stripped')
  assert.ok(out.includes('what\u2019s left') || out.includes("what's left"),
    'the JSX text itself survives the strip')
  assert.ok(out.includes("this Organization's graph"),
    'text after the second apostrophe is not swallowed')

  // Direction 2 of the same bug: an apostrophe must not license a phantom
  // string that hides a real anchor, i.e. the ratchet must see live copy and
  // NOT see commented copy.
  const live = stripComments("<p>Don't forget to file your first memory</p>\n")
  assert.ok(live.includes('file your first memory'), 'live copy stays visible to a presence ratchet')
  const commentOnly = stripComments("<p>Don't</p>\n{/* file your first memory */}\n")
  assert.ok(!commentOnly.includes('file your first memory'),
    'comment-only copy must NOT satisfy a presence ratchet')

  // A newline abort must NOT break legitimately multi-line template literals.
  const tpl = stripComments('const t = `line one // not a comment\nline two /* not a comment */\n`\nconst after = 1\n')
  assert.ok(tpl.includes('line one // not a comment'), 'a // inside a multi-line template survives')
  assert.ok(tpl.includes('line two /* not a comment */'), 'a /* inside a multi-line template survives')
  assert.ok(tpl.includes('const after = 1'), 'code after the template survives')

  // An escaped newline inside a ' literal (line continuation) must not abort
  // the string early — the escape branch copies the escaped newline.
  const cont = stripComments("const s = 'a\\\nb'; // trailing\nconst c = 2\n")
  assert.ok(!cont.includes('trailing'), 'a // after a line-continuation literal is still stripped')
  assert.ok(cont.includes('const c = 2'))
})
