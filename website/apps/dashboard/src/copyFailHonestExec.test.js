// #2935: a copy control must not report success when the clipboard write fails.
//
// The defect was not "no error handling" — three of the sites HAD a
// `try { writeText() } catch {}`. It was that `writeText` rejects
// ASYNCHRONOUSLY, so a synchronous try/catch never observes the rejection and
// every site announced a copy the user could not paste. These tests therefore
// drive a clipboard that REJECTS, and assert the absence of the success signal
// rather than the presence of an error branch.
//
// Follows the established exec idiom (keyMintFailureExec.test.js): the real
// declarations are extracted from main.jsx and compiled with `new Function`,
// so this exercises the shipped implementation rather than a copy of it.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')

// Skip strings/template literals (tracking ${…} nesting) so a brace inside copy
// cannot truncate the extracted body.
function skipString(src, i) {
  const q = src[i]
  if (q === '`') {
    let depth = 0
    for (let j = i + 1; j < src.length; j++) {
      if (src[j] === '\\') { j++; continue }
      if (src[j] === '$' && src[j + 1] === '{') { depth++; j++; continue }
      if (src[j] === '}' && depth > 0) { depth--; continue }
      if (src[j] === '`' && depth === 0) return j
    }
    return -1
  }
  for (let j = i + 1; j < src.length; j++) {
    if (src[j] === '\\') { j++; continue }
    if (src[j] === q) return j
    if (q !== '`' && src[j] === '\n') return -1
  }
  return -1
}

function matchBrace(src, open) {
  let depth = 0
  for (let i = open; i < src.length; i++) {
    const c = src[i]
    if (c === '/' && src[i + 1] === '/') { i = src.indexOf('\n', i); if (i < 0) return -1; continue }
    if (c === '/' && src[i + 1] === '*') { i = src.indexOf('*/', i); if (i < 0) return -1; i++; continue }
    if (c === '"' || c === "'" || c === '`') { i = skipString(src, i); if (i < 0) return -1; continue }
    if (c === '{') depth++
    else if (c === '}') { depth--; if (depth === 0) return i }
  }
  return -1
}

function extractDeclaration(src, decl) {
  const start = src.indexOf(`${decl}(`)
  assert.ok(start > -1, `main.jsx must declare ${decl}()`)
  const open = src.indexOf('{', start)
  assert.ok(open > -1, `${decl}: the body must open with {`)
  const end = matchBrace(src, open)
  assert.ok(end > -1, `${decl}: the body braces must balance`)
  return src.slice(start, end + 1)
}

function build(deps, decls) {
  const body = `${decls.map((d) => extractDeclaration(mainJsx, d)).join('\n')}\n`
    + `return { ${decls.map((d) => d.replace(/^async /, '').replace(/^function /, '')).join(', ')} }`
  const params = Object.keys(deps)
  return new Function(...params, body)(...params.map((n) => deps[n]))
}

const refuse = { writeText: async () => { throw new Error('Write permission denied') } }
const accept = (sink) => ({ writeText: async (v) => { sink.push(v) } })

test('#2935: copyText reports the REAL outcome of the write, both ways', async () => {
  const refused = await build({ navigator: { clipboard: refuse } }, ['async function copyText']).copyText('payload')
  assert.equal(refused, false, 'a rejected write must not report success')

  const wrote = []
  const accepted = await build({ navigator: { clipboard: accept(wrote) } }, ['async function copyText']).copyText('payload')
  assert.equal(accepted, true, 'a resolved write is a success')
  assert.deepEqual(wrote, ['payload'], 'the payload actually reached the clipboard')
})

test('#2935: copyText survives a MISSING clipboard (the case the old try/catch covered)', async () => {
  const ok = await build({ navigator: {} }, ['async function copyText']).copyText('payload')
  assert.equal(ok, false, 'no clipboard is not a successful copy')
})

test('#2935: the inline buttons say "Copy failed", never "Copied", on a refused write', async () => {
  const { copyInline } = build(
    { navigator: { clipboard: refuse }, setTimeout: (fn) => { fn(); return 0 } },
    ['async function copyText', 'async function copyInline'],
  )
  const btn = { textContent: 'Copy URL' }
  await copyInline({ currentTarget: btn }, 'https://api.premiselabs.co/mcp', 'Copy URL')
  assert.equal(btn.textContent, 'Copy URL', 'the resting label is restored after the flash')
  // The flash itself is what the user sees; capture it by freezing the timer.
  const seen = []
  const { copyInline: inlineNoTimer } = build(
    { navigator: { clipboard: refuse }, setTimeout: (fn) => { seen.push(fn) } },
    ['async function copyText', 'async function copyInline'],
  )
  const btn2 = { textContent: 'Copy' }
  await inlineNoTimer({ currentTarget: btn2 }, 'secret', 'Copy')
  assert.equal(btn2.textContent, 'Copy failed', 'a refused write must read as a failure, not "Copied"')
  assert.notEqual(btn2.textContent, 'Copied')
})

test('#2935: the inline buttons still say "Copied" when the write really lands', async () => {
  const wrote = []
  const { copyInline } = build(
    { navigator: { clipboard: accept(wrote) }, setTimeout: () => 0 },
    ['async function copyText', 'async function copyInline'],
  )
  const btn = { textContent: 'Copy' }
  await copyInline({ currentTarget: btn }, 'secret', 'Copy')
  assert.equal(btn.textContent, 'Copied')
  assert.deepEqual(wrote, ['secret'])
})

test('#2935: wizardCopyStep names the FAILING row only, and says so', async () => {
  const copied = []
  const { wizardCopyStep } = build(
    {
      navigator: { clipboard: refuse },
      setCopiedStep: (v) => copied.push(v),
      mountedRef: { current: true },
      setTimeout: () => 0,
      copyFailedKey: (t) => `__copy_failed__|${t}`,
    },
    ['async function copyText', 'async function wizardCopyStep'],
  )
  await wizardCopyStep('step payload')
  assert.deepEqual(copied, ['__copy_failed__|step payload'],
    'the false-success defect was setCopiedStep(text) here — it must not be set')
  assert.ok(!copied.includes('step payload'), 'a refused write must never mark that step as copied')
  // The key is payload-scoped, so ONE refused row cannot relabel its siblings.
  assert.notEqual(copied[0], '__copy_failed__', 'a bare sentinel would mark every sibling row as failed')
})

test('#2935 (review P1): a stale failure timer must not wipe a later STICKY success', async () => {
  // The 'harness' success path deliberately arms NO timer (it is sticky), so an
  // unconditional 4s clear from an earlier failure erased it — and with it the
  // Continue affordance, which is gated on wizardCopied === 'harness'.
  const timers = []
  const state = []
  const { wizardCopy } = build(
    {
      navigator: { clipboard: refuse },
      setWizardCopied: (v) => state.push(typeof v === 'function' ? v(state[state.length - 1]) : v),
      mountedRef: { current: true },
      setTimeout: (fn) => { timers.push(fn); return 0 },
      wizardHarness: 'pi',
      onboardingTeamQ: () => '',
      api: () => ({ catch: () => {} }),
      COPY_FAILED: '__copy_failed__',
    },
    ['async function copyText', 'async function wizardCopy'],
  )
  await wizardCopy('payload', 'harness')       // refused -> sentinel + a 4s clear
  state.push('harness')                        // a later successful copy (sticky)
  timers.forEach((fn) => fn())                 // the stale failure timer fires
  assert.equal(state[state.length - 1], 'harness',
    'the failure timer must clear only its own sentinel, never a later success')
})

test('#2935: wizardCopy claims NOTHING when the clipboard refuses', async () => {
  const marked = []
  const { wizardCopy } = build(
    {
      navigator: { clipboard: refuse },
      setWizardCopied: (v) => marked.push(v),
      mountedRef: { current: true },
      setTimeout: () => 0,
      wizardHarness: 'pi',
      onboardingTeamQ: () => '',
      api: () => ({ catch: () => {} }),
      COPY_FAILED: '__copy_failed__',
    },
    ['async function copyText', 'async function wizardCopy'],
  )
  await wizardCopy('payload', 'harness')
  assert.deepEqual(marked, ['__copy_failed__'], 'the button must not flip to Copied ✓ over an unchanged clipboard')
  assert.ok(!marked.includes('harness'), 'a refused write must never set the copied label')
})
// #2935 (review P2-1): strip comments and string BODIES while preserving line
// structure, so the scan sees code only. The first version keyed on the
// qualified literal `navigator.clipboard?.writeText(` on one line, which a
// destructured alias, a bracket access, a multi-line call, or an unrelated
// earlier `await` all defeated — and which a legitimately handled
// `writeText(x).catch(...)` wrongly flagged.
function stripNonCode(src) {
  const out = src.split('')
  let i = 0
  while (i < src.length) {
    const c = src[i]
    if (c === '/' && src[i + 1] === '/') {
      const nl = src.indexOf('\n', i)
      // A trailing line comment has no newline — blank to the end, do not bail
      // (bailing left the comment body scannable, a false red).
      const stop = nl < 0 ? src.length : nl
      for (let j = i; j < stop; j++) out[j] = ' '
      if (nl < 0) break
      i = nl
      continue
    }
    if (c === '/' && src[i + 1] === '*') {
      const end = src.indexOf('*/', i)
      if (end < 0) break
      for (let j = i; j < end + 2; j++) if (out[j] !== '\n') out[j] = ' '
      i = end + 2
      continue
    }
    if (c === '"' || c === "'" || c === '`') {
      const end = skipString(src, i)
      if (end < 0) { i++; continue }
      for (let j = i + 1; j < end; j++) if (out[j] !== '\n') out[j] = ' '
      i = end + 1
      continue
    }
    i++
  }
  return out.join('')
}

export function clipboardWriteOffences(src) {
  const code = stripNonCode(src)
  const lines = code.split('\n')
  const raw = src.split('\n')
  const offences = []
  lines.forEach((line, i) => {
    // `writeText` as an IDENTIFIER — not the qualified call shape. Round 2
    // matched `writeText(`, which `writeText?.(x)` and the value-alias form
    // evaded. Bracket access on the METHOD (`["writeText"]`) cannot be seen in
    // the stripped projection, because its key IS a string body, so it is
    // matched on the raw line — narrowly, so a string that merely MENTIONS the
    // call is still not flagged. Pure comment lines are skipped.
    const rawLine = raw[i] || ''
    // A line that was ENTIRELY comment/string blanks to nothing in the stripped
    // projection. (A leading-comment regex was wrong: a block comment followed
    // by real code on the same line is not a comment line.)
    if (line.trim() === '') return
    const isCopy = /\bwriteText\b/.test(line)
      || /\[\s*['"]writeText['"]\s*\]/.test(rawLine)
      || /\bexecCommand\s*\(/.test(line)
      || /\bClipboardItem\b/.test(line)
    if (!isCopy) return
    const at = line.search(/\bwriteText\b|\bexecCommand\s*\(|\bClipboardItem\b/)
    // The bracket form's key is a blanked string body, so on the stripped line
    // the match is at -1 — take `before` from the RAW line there, or an
    // awaited bracket call reads as un-awaited (a false red).
    const bracketAt = at < 0 ? rawLine.search(/\[\s*['"]writeText['"]\s*\]/) : -1
    const before = at < 0 ? rawLine.slice(0, bracketAt < 0 ? 0 : bracketAt) : line.slice(0, at)
    // `await` must govern THIS call: since the last statement boundary, or as
    // the trailing token of the previous line. An unrelated earlier
    // `await foo();` does not count.
    const awaitedHere = /\bawait\b/.test(before.slice(before.lastIndexOf(';') + 1))
    const prevLine = i > 0 ? lines[i - 1] : ''
    // An earlier `await` on the previous line governs this call as long as that
    // statement was not already terminated — `await foo();` followed by the
    // write does NOT make the write awaited (the round-3 evasion).
    const awaitedPrev = /\bawait\b/.test(prevLine) && !/;\s*$/.test(prevLine)
    if (awaitedHere || awaitedPrev) return
    // `.then`/`.catch` must be attached to THIS write's own call — `[^;]*?`
    // cannot cross a statement boundary, so a `.catch` belonging to an unrelated
    // call later on the line no longer makes the write look observed.
    if (/\bwriteText\b[^;]*?\)\s*\.(then|catch)\s*\(/.test(line)) return
    // A handler chained on the NEXT line is equally observant.
    const nextLine = i + 1 < lines.length ? lines[i + 1] : ''
    if (/^\s*\.\s*(then|catch)\s*\(/.test(nextLine)) return
    offences.push(`main.jsx:${i + 1}: ${(raw[i] || '').trim()}`)
  })
  return offences
}

test('#2935 TRIPWIRE: no clipboard site is left un-awaited (the defect cannot come back)', () => {
  const offences = clipboardWriteOffences(mainJsx)
  assert.deepEqual(offences, [], `un-awaited clipboard writes reintroduce #2935:\n${offences.join('\n')}`)
})

test('#2935 TRIPWIRE is real: it catches the shapes that evaded the first version', () => {
  const evading = [
    'navigator.clipboard.writeText(x)',
    'const { writeText } = navigator.clipboard; writeText(x)',
    'navigator["clipboard"].writeText(x)',
    'const cb = navigator.clipboard; cb.writeText(x)',
    'document.execCommand("copy")',
    'navigator.clipboard.write([new ClipboardItem({ "text/plain": blob })])',
    // The three the PREVIOUS guard let through:
    'await foo(); navigator.clipboard.writeText(x)',
    '/* not a comment anymore */ navigator.clipboard.writeText(x)',
    'await copyText("https://a"); navigator.clipboard.writeText(x)',
    // The four round 3 named:
    'navigator.clipboard?.writeText?.(x)',
    'navigator.clipboard["writeText"](x)',
    'const w = navigator.clipboard.writeText; w(x)',
    'navigator.clipboard.writeText(x); foo().catch(console.error)',
  ]
  evading.forEach((line) => {
    assert.equal(clipboardWriteOffences(line).length, 1, `the ratchet must flag: ${line}`)
  })
})

test('#2935 TRIPWIRE does not cry wolf on an observed rejection', () => {
  const handled = [
    'await navigator.clipboard.writeText(x)',
    'await copyText(x)',
    'navigator.clipboard.writeText(x).catch(() => {})',
    '// navigator.clipboard.writeText(x) -- not code',
    // The four the PREVIOUS guard wrongly flagged:
    'await (navigator.clipboard.writeText(x))',
    'await\nnavigator.clipboard.writeText(x)',
    'const s = `see navigator.clipboard.writeText(x)`',
    'const s = "navigator.clipboard.writeText(x)"',
    // The four round 4 named:
    'await navigator.clipboard["writeText"](x)',
    'await navigator.clipboard?.["writeText"]?.(x)',
    'navigator.clipboard.writeText(x)\n  .catch(() => {})',
    'await navigator.clipboard\n  .writeText(x)',
  ]
  handled.forEach((line) => {
    assert.deepEqual(clipboardWriteOffences(line), [], `must NOT flag: ${line}`)
  })
})
