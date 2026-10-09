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

test('#2935: wizardCopyStep says "Copy failed" when the clipboard refuses', async () => {
  const copied = []
  const { wizardCopyStep } = build(
    {
      navigator: { clipboard: refuse },
      setCopiedStep: (v) => copied.push(v),
      mountedRef: { current: true },
      setTimeout: () => 0,
      COPY_FAILED: '__copy_failed__',
    },
    ['async function copyText', 'async function wizardCopyStep'],
  )
  await wizardCopyStep('step payload')
  assert.deepEqual(copied, ['__copy_failed__'], 'the false-success defect was setCopiedStep(text) here — it must not be set')
  assert.ok(!copied.includes('step payload'), 'a refused write must never mark that step as copied')
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

// #2935 (review P2-1): the ratchet is a FUNCTION so it can be tested against
// sources that are not main.jsx. The first version keyed on the qualified
// literal `navigator.clipboard?.writeText(` on one line, which a destructured
// alias, a bracket access, or a multi-line call all evaded — a guard that only
// catches the shape it was written against is theatre. Flag the `writeText`
// IDENTIFIER (plus the other copy primitives), unless the write is awaited or
// its rejection is handled.
export function clipboardWriteOffences(src) {
  const offences = []
  src.split('\n').forEach((line, i) => {
    if (/^\s*(\/\/|\*|\/\*)/.test(line)) return
    const code = line.replace(/\/\/.*$/, '')
    const isCopy = /\bwriteText\s*\(/.test(code)
      || /\bexecCommand\s*\(\s*['"]copy['"]/.test(code)
      || /\bClipboardItem\b/.test(code)
    if (!isCopy) return
    // `await` must be on the same line; `.then(`/`.catch(` observe the promise.
    if (/await\s+\.?\s*[A-Za-z_$]/.test(code)) return
    if (/\.then\s*\(|\.catch\s*\(/.test(code)) return
    offences.push(`main.jsx:${i + 1}: ${line.trim()}`)
  })
  return offences
}

test('#2935 TRIPWIRE: no clipboard site is left un-awaited (the defect cannot come back)', () => {
  const offences = clipboardWriteOffences(mainJsx)
  assert.deepEqual(offences, [], `un-awaited clipboard writes reintroduce #2935:\n${offences.join('\n')}`)
})

test('#2935 TRIPWIRE is real: it catches the shapes that evaded the first version', () => {
  // Mutation-tested against the forms a reviewer used to defeat the literal
  // matcher. Each of these reproduces the defect and MUST be flagged.
  const evading = [
    'navigator.clipboard.writeText(x)',
    'const { writeText } = navigator.clipboard; writeText(x)',
    'navigator["clipboard"].writeText(x)',
    'const cb = navigator.clipboard; cb.writeText(x)',
    'document.execCommand("copy")',
    'navigator.clipboard.write([new ClipboardItem({ "text/plain": blob })])',
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
  ]
  handled.forEach((line) => {
    assert.deepEqual(clipboardWriteOffences(line), [], `must NOT flag: ${line}`)
  })
})
