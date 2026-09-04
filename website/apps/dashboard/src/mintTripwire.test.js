// mintTripwire.test.js — #2167/#2246 static tripwires (CI-run via
// dashboard-js-tests). Converts the phase-4 one-time grep backstops into
// PERMANENT guards. #2167: the hosted dashboard has ZERO automatic
// bootstrap-mint paths (mount / switchTeam / 401-re-mint / revokeKey re-mint
// were deleted in #2167) — the POST /v1/session/key endpoint + the recovery
// purpose STAY for non-dashboard consumers, and the dashboard's only
// recovery-purpose POSTer (recoverKey) is deleted in #2246, so a bare
// '/v1/session/key' grep is safe. #2246 (ADR-010): the session-only decouple
// deleted the held-key machinery — main.jsx must not reference the deleted
// helpers, must never read/write the KEY_STORAGE slot, and must keep zero
// key-lane probe machinery.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const mainJsx = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')

// #2246: the static guards below must catch CODE references to deleted
// machinery, but explanatory PROSE may still name the deleted helpers (e.g.
// "the held-key machinery ... was deleted"). Strip // and /* */ comments so
// prose stays legal while any code reference still fails the grep.
// #2246 review (F): the strip is QUOTE-AWARE — a naive regex strip turned
// `//` INSIDE string/template literals into comment text (e.g.
// 'https://…' → 'https:'), so a banned token embedded in a URL string became
// invisible to the code greps (evadable). The state machine below strips
// comments only when NOT inside a single/double-quoted string or backtick
// template (minimal escape handling) — strings stay intact, so a code-form
// usage (string literal included) still fails the grep while comment-only
// mentions still pass.
// #2246 review (R2): known evadable shapes (documented seams — this machine
// is a quote-aware comment stripper, NOT a JS tokenizer; pinned by the unit
// tests below):
//   - REGEX LITERALS whose body ends in escaped slashes — `/https?:\/\//`
//     exposes a `//` pair at the regex close, so from that pair to end-of-
//     line is stripped as a comment (accepted limitation: guards must not
//     rely on code whose regex literals end `\/\/`).
//   - TEMPLATE LITERALS with NESTED backticks inside `${...}` expressions
//     (`` `a ${`b`} // …` ``) — the machine closes at the first unescaped
//     backtick, so a nested backtick ends the template early and the tail is
//     re-scanned as plain code (accepted limitation).
// main.jsx currently triggers neither shape (all five guards green); if a
// future guard needs either, upgrade the machine deliberately against the
// pinned tests.
function stripComments(src) {
  let out = ''
  let i = 0
  const n = src.length
  while (i < n) {
    const c = src[i]
    const next = src[i + 1]
    if (c === '/' && next === '/') {
      // line comment — run to (not incl.) the newline
      while (i < n && src[i] !== '\n') i++
    } else if (c === '/' && next === '*') {
      // block comment — run past the closing */
      i += 2
      while (i < n && !(src[i] === '*' && src[i + 1] === '/')) i++
      i += 2
    } else if (c === '"' || c === "'" || c === '`') {
      // string / template literal — copy verbatim to its unescaped close so
      // a `//` inside the literal is never misread as a comment start
      const quote = c
      out += c
      i++
      while (i < n) {
        const sc = src[i]
        if (sc === '\\') { out += sc + (src[i + 1] || ''); i += 2; continue }
        out += sc
        i++
        if (sc === quote) break
      }
    } else {
      out += c
      i++
    }
  }
  return out
}
const mainJsxCode = stripComments(mainJsx)

// #2246 review (R2): stripComments unit tests — pin the quote-aware machine's
// behavior so a future stripper upgrade cannot silently regress what the
// static guards above depend on (strings survive; real comments die).
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
  assert.ok(out.includes('const survivor = 1'), 'following lines still survive the limitation')
  assert.ok(!out.includes('; // keep-me?'), 'the closing `//;` of the regex line is consumed (pinned)')
})

test('#2167: zero mintSessionKey( call sites remain in main.jsx', () => {
  const calls = mainJsx.match(/mintSessionKey\s*\(/g) || []
  assert.deepEqual(calls, [], `mintSessionKey( call sites must be deleted: ${calls}`)
})

test('#2167: no bootstrap-purpose /v1/session/key POST string remains (recovery inline is exempt)', () => {
  // #2246: recoverKey (the dashboard's only POST /v1/session/key consumer)
  // is DELETED — the recovery purpose + endpoint remain for NON-dashboard
  // consumers only (SDK/CLI/selfhost), so match ONLY the bootstrap purpose
  // (any quoting style).
  const boot = mainJsx.match(/purpose\s*:\s*['"]bootstrap['"]/g) || []
  assert.deepEqual(boot, [], `bootstrap-purpose mints must be deleted: ${boot}`)
})

// ── #2246 (ADR-010): session-only decouple static guards ──────────────────
test('#2246: zero held-key machinery references remain in main.jsx', () => {
  // The session-only decouple deleted probe/adopt/drop/classify/install usage.
  // Comments are stripped so explanatory prose may name the deleted helpers;
  // any surviving CODE reference fails.
  for (const name of ['probeClassifyStoredKey', 'classifyHeldKey', 'heldKeyClearState',
                      'nextRegenInstallState', 'isActiveKey', 'recoverKey']) {
    const hits = mainJsxCode.match(new RegExp(name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g')) || []
    assert.deepEqual(hits, [], `${name} code references must be deleted from main.jsx: ${hits}`)
  }
})

test('#2246: KEY_STORAGE is never read or written in main.jsx (residue is only ever purged/kept)', () => {
  // The browser never acts on the slot in session mode: no getItem (the apiKey
  // initializer was zeroed) and no setItem (every install/persist writer was
  // deleted). removeItem stays — the one-shot mount purge + logout wipe.
  const getters = mainJsxCode.match(/localStorage\.getItem\s*\(\s*KEY_STORAGE\s*\)/g) || []
  assert.deepEqual(getters, [], `localStorage.getItem(KEY_STORAGE) must be deleted: ${getters}`)
  const setters = mainJsxCode.match(/localStorage\.setItem\s*\(\s*KEY_STORAGE/g) || []
  assert.deepEqual(setters, [], `localStorage.setItem(KEY_STORAGE must be deleted: ${setters}`)
})

test('#2246: zero key-lane probe fetch remains (stored-key Authorization bearer)', () => {
  // The mount stored-key probe was the last key-authed browser fetch; session
  // mode authenticates with the session JWT only.
  const probes = mainJsxCode.match(/Bearer \$\{(storedKey|stored_key)\}/g) || []
  assert.deepEqual(probes, [], `stored-key probe fetches must be deleted: ${probes}`)
  // recoverKey deletion also makes the bare endpoint string safe (it was the
  // only dashboard consumer of POST /v1/session/key).
  const endpoint = mainJsxCode.match(/v1\/session\/key/g) || []
  assert.deepEqual(endpoint, [], `POST /v1/session/key must have no dashboard consumer: ${endpoint}`)
})
