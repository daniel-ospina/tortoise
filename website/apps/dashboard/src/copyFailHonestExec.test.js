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

test('#2935: wizardCopyStep claims NOTHING when the clipboard refuses', async () => {
  const copied = []
  const { wizardCopyStep } = build(
    {
      navigator: { clipboard: refuse },
      setCopiedStep: (v) => copied.push(v),
      mountedRef: { current: true },
      setTimeout: () => 0,
    },
    ['async function copyText', 'async function wizardCopyStep'],
  )
  await wizardCopyStep('step payload')
  assert.deepEqual(copied, [''], 'the false-success defect was setCopiedStep(text) here — it must not be set')
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
    },
    ['async function copyText', 'async function wizardCopy'],
  )
  await wizardCopy('payload', 'harness')
  assert.deepEqual(marked, [''], 'the button must not flip to Copied ✓ over an unchanged clipboard')
  assert.ok(!marked.includes('harness'), 'a refused write must never set the copied label')
})

test('#2935 TRIPWIRE: no clipboard site is left un-awaited (the defect cannot come back)', () => {
  // Every `navigator.clipboard` write must be awaited, so its rejection is
  // observable. A bare `writeText(...)` — with or without a synchronous
  // try/catch — silently reproduces #2935. This is the ratchet: it fails on a
  // NEW site, not just on the ones fixed here.
  const lines = mainJsx.split('\n')
  const offences = []
  lines.forEach((line, i) => {
    const code = line.replace(/\/\/.*$/, '')
    if (!/navigator\.clipboard\??\.writeText\s*\(/.test(code)) return
    if (/await\s+navigator\.clipboard/.test(code)) return
    // Comments describing the pattern are not code.
    if (/^\s*\*/.test(line)) return
    offences.push(`main.jsx:${i + 1}: ${line.trim()}`)
  })
  assert.deepEqual(offences, [], `un-awaited clipboard writes reintroduce #2935:\n${offences.join('\n')}`)
})
