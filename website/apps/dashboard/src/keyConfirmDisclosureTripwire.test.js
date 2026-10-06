// keyConfirmDisclosureTripwire.test.js — #2246 (PM-1) static tripwire
// (CI-run via dashboard-js-tests). main.jsx has no React runtime harness, so
// the destructive-key confirm COPY is pinned here as source text, mirroring
// graphRenameDeleteTripwire / keyExpiryTripwire. The #2246 contract: every
// one-click revoke/rotate confirm names its target as "name · prefix ·
// created" so a user can identify the key that stops working (rows are
// hash-only; names may be unset). A future edit that drops the prefix — the
// exact #2735 regression, where the dialogs emitted the name only — fails
// loudly here instead of silently shipping an unidentifiable destructive
// action.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const mainJsx = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'main.jsx'), 'utf8')

function fnBody(name) {
  const start = mainJsx.indexOf(`async function ${name}(`)
  assert.notEqual(start, -1, `could not locate async function ${name} in main.jsx`)
  const next = mainJsx.indexOf('\n  async function ', start + 1)
  const end = next === -1 ? mainJsx.length : next
  return mainJsx.slice(start, end)
}

test('#2246 (PM-1): keyRowDisclosure composes name · prefix · created', () => {
  const start = mainJsx.indexOf('function keyRowDisclosure(row, fallbackName)')
  assert.notEqual(start, -1, 'the shared keyRowDisclosure derivation must exist')
  const body = mainJsx.slice(start, mainJsx.indexOf('\n}', start) + 2)
  assert.match(body, /row\.name/, 'the disclosure must read the row name')
  assert.match(body, /row\.key_prefix/, 'the disclosure must read the row key_prefix')
  assert.match(body, /created_at \|\| row\.createdAt/, 'the disclosure must read the created timestamp')
  assert.match(body, /\.join\(' · '\)/, 'the disclosure must join name · prefix · created')
})

test('#2246 (PM-1): all three destructive-key confirms use keyRowDisclosure', () => {
  for (const fn of ['revokeKey', 'regenerateKey', 'revokePanelKey']) {
    assert.match(fnBody(fn), /confirm\(`(?:Revoke|Rotate) \$\{keyRowDisclosure\(/,
      `${fn}'s confirm must name the row via keyRowDisclosure (never the name only)`)
  }
})

test('#2246 (PM-1): the confirms keep the destructive warning copy', () => {
  assert.match(fnBody('revokeKey'), /Applications using it will stop working\./,
    'revoke must state that applications stop working')
  assert.match(fnBody('revokePanelKey'), /Applications using it will stop working\./,
    'panel revoke must state that applications stop working')
  assert.match(fnBody('regenerateKey'), /applications using the old key will stop working\./,
    'rotate must state that applications using the old key stop working')
})

test('#2735/#4342: the rotate copy clears only after the clipboard write succeeds, and never writes a non-string', () => {
  // The old key is already revoked when the reveal mounts, so a failed
  // clipboard write that still cleared the reveal would destroy the only copy
  // of the live replacement (#2392 class). Mirrors revealKey's fallback.
  // #4342: the inline handler was extracted to `copyRotatedKey` and gated on a
  // non-empty string — the old inline `writeText(rotatedKey.plaintext)` could
  // run with '' (a silent no-op copy that then cleared the reveal).
  const body = fnBody('copyRotatedKey')
  assert.match(body, /const plaintext = rotatedKey \? revealablePlaintext\(rotatedKey\.plaintext\) : ''/,
    'the copy must read the plaintext through the non-blank-string predicate')
  assert.match(body, /if \(!plaintext\) return/, 'a falsy plaintext must return before the clipboard')
  assert.match(body, /await navigator\.clipboard\.writeText\(plaintext\)/,
    'the rotate copy must await the clipboard write with the guarded string')
  assert.match(body, /catch \{[\s\S]{0,400}selectNodeContents/,
    'a failed write must keep the plaintext visible and select it for manual copy')
  const start = mainJsx.indexOf('className="new-key"')
  assert.notEqual(start, -1, 'the rotate reveal block must exist')
  const block = mainJsx.slice(start, mainJsx.indexOf('</div>', start) + 6)
  assert.match(block, /onClick=\{copyRotatedKey\}/, 'the reveal Copy must call copyRotatedKey')
  assert.equal((block.match(/writeText\(/g) || []).length, 0,
    'no raw writeText may remain inline in the reveal — the guarded function owns it')
})

test('#2735/#4342: rotate renders the one-time replacement reveal (regression guard)', () => {
  // #2667 deleted the standalone `.new-key` block while regenerateKey kept
  // setting newKey — rotate minted the replacement and never showed it. The
  // reveal now renders from its OWN `rotatedKey` state, so the create modal's
  // dismiss paths (which clear newKey) can never destroy it.
  // #4342: the gate is the DERIVED non-empty string, not the raw object — a
  // falsy plaintext can no longer render an empty `.key-value` box.
  assert.match(mainJsx, /const rotatedKeyReveal = \(rotatedKey && revealablePlaintext\(rotatedKey\.plaintext\)\) \|\| ''/,
    'the rotate reveal must be gated on a non-empty, non-blank plaintext string (#4342)')
  assert.match(mainJsx, /\{rotatedKeyReveal && \(/,
    'the rotate reveal must render from the derived non-empty reveal')
  assert.match(mainJsx, /setRotatedKey\(\{ plaintext: plaintext,/,
    'regenerateKey must set the rotate reveal from the mint response')
  assert.match(mainJsx, /Your new key \(shown once\)/,
    'the reveal must label the key as shown once')
})
