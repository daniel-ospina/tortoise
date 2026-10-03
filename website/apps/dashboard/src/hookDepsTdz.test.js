// hookDepsTdz.test.js — run with node --test (#2709 regression guard).
//
// Source scan (wizardArchived.test.js style — reads main.jsx as TEXT, no React
// runtime, zero deps: the dashboard-js-tests lane runs bare `node --test` and
// does NOT npm-install, so a real JSX parser is unavailable here by design).
//
// Why: React evaluates a hook's deps array eagerly during render. A binding
// declared later in the same function scope is still in its temporal dead
// zone at that point, so listing it throws
//   ReferenceError: Cannot access '<name>' before initialization
// on EVERY render. That is the white-screen class that has now recurred:
// #2449 / PR #2448 (welcomeHasOrg/wizardShowPaste) and #2709 (harnessKey).
//
// Detection strategy (scope-light but robust, mutation-proven below):
//   1. Blank comments AND string/template contents while PRESERVING newlines
//      (so reported line numbers are true).
//   2. Find every hook call by name, bracket-match its argument list, and take
//      the LAST top-level argument if it is an array literal — this handles
//      one-liners, multi-line arrays, and trailing `//` comments alike.
//   3. Collect every binding in the file (const/let/class, var, function,
//      params, imports). A deps identifier is a TDZ hazard only when its ONLY
//      declarations are `const/let/class` positioned AFTER the hook call.
//      Hoisted (var/function) and earlier bindings — including shadowing
//      params in nested scopes — suppress the flag, which is what keeps this
//      heuristic free of cross-scope false positives.
//
// The runtime net is tests/e2e/test_dashboard_mount_smoke.py (real authed
// render, zero console errors). This is the fast always-on unit net.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))

const HOOKS = ['useEffect', 'useMemo', 'useCallback', 'useLayoutEffect', 'useInsertionEffect']

// Blank comment + string/template contents, preserving every newline so line
// numbers stay exact. (Regex literals containing `//`/`/*` are not modelled;
// none appear in main.jsx, and a miss here can only suppress a match, never
// invent one.)
export function blankCommentsAndStrings(src) {
  let out = ''
  let mode = 'code' // code | line | block | single | double | template
  for (let i = 0; i < src.length; i++) {
    const c = src[i]
    const c2 = src[i + 1]
    if (mode === 'code') {
      if (c === '/' && c2 === '/') { out += '  '; i++; mode = 'line'; continue }
      if (c === '/' && c2 === '*') { out += '  '; i++; mode = 'block'; continue }
      if (c === "'") { out += ' '; mode = 'single'; continue }
      if (c === '"') { out += ' '; mode = 'double'; continue }
      if (c === '`') { out += ' '; mode = 'template'; continue }
      out += c
      continue
    }
    if (c === '\n') { out += '\n'; if (mode === 'line') mode = 'code'; continue }
    if (mode === 'line') { out += ' '; continue }
    if (mode === 'block') {
      if (c === '*' && c2 === '/') { out += '  '; i++; mode = 'code'; continue }
      out += ' '
      continue
    }
    // single | double | template
    if (c === '\\') { out += '  '; i++; continue }
    if ((mode === 'single' && c === "'") || (mode === 'double' && c === '"') ||
        (mode === 'template' && c === '`')) { out += ' '; mode = 'code'; continue }
    out += ' '
  }
  return out
}

function matchBracket(code, open) {
  let depth = 0
  for (let i = open; i < code.length; i++) {
    const c = code[i]
    if (c === '(' || c === '[' || c === '{') depth++
    else if (c === ')' || c === ']' || c === '}') {
      depth--
      if (depth === 0) return i
    }
  }
  return -1
}

function splitTopLevel(text) {
  const parts = []
  let depth = 0
  let start = 0
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (c === '(' || c === '[' || c === '{') depth++
    else if (c === ')' || c === ']' || c === '}') depth--
    else if (c === ',' && depth === 0) { parts.push(text.slice(start, i)); start = i + 1 }
  }
  parts.push(text.slice(start))
  return parts
}

/** Return [{hook, pos, deps:[...]}] for every hook call with an array last arg. */
export function findHookDeps(code) {
  const found = []
  for (let i = 0; i < code.length; i++) {
    for (const hook of HOOKS) {
      if (!code.startsWith(hook, i)) continue
      const before = code[i - 1]
      const after = code[i + hook.length]
      if (before && /[\w$]/.test(before)) continue
      if (after && /[\w$]/.test(after)) continue
      let j = i + hook.length
      while (j < code.length && /\s/.test(code[j])) j++
      if (code[j] !== '(') continue
      const close = matchBracket(code, j)
      if (close < 0) continue
      const args = splitTopLevel(code.slice(j + 1, close))
      if (args.length < 2) continue
      const last = args[args.length - 1].trim()
      if (!last.startsWith('[') || !last.endsWith(']')) continue
      const deps = splitTopLevel(last.slice(1, -1)).map((s) => s.trim())
        .filter((s) => /^[A-Za-z_$][\w$]*$/.test(s))
      if (deps.length) found.push({ hook, pos: i, deps })
    }
  }
  return found
}

const IDENT = /[A-Za-z_$][\w$]*/g

/** Every binding in the file with its position + kind (for suppression). */
export function collectBindings(code) {
  const bindings = []
  const add = (name, pos, kind) => bindings.push({ name, pos, kind })

  let m
  const declRe = /\b(const|let|var|class)\s+/g
  while ((m = declRe.exec(code))) {
    const kind = m[1]
    const start = declRe.lastIndex
    let depth = 0
    let text = ''
    let j = start
    while (j < code.length) {
      const c = code[j]
      if (c === '(' || c === '[' || c === '{') depth++
      else if (c === ')' || c === ']' || c === '}') depth--
      if (depth === 0 && (c === '=' || c === ';' || c === '\n')) break
      text += c
      j++
    }
    for (const name of text.match(IDENT) || []) add(name, start, kind)
  }

  const fnRe = /\bfunction\s+([A-Za-z_$][\w$]*)/g
  while ((m = fnRe.exec(code))) add(m[1], m.index, 'function')

  const importRe = /\bimport\s+([^;]+?)\s+from\b/g
  while ((m = importRe.exec(code))) {
    for (const name of m[1].match(IDENT) || []) add(name, m.index, 'import')
  }

  // Parameters — function/method decls, parenthesised arrows, single-param arrows.
  const paramRes = [/function\s*[A-Za-z_$]*\s*\(([^)]*)\)/g, /\(([^)]*)\)\s*=>/g]
  for (const re of paramRes) {
    while ((m = re.exec(code))) {
      for (const name of m[1].match(IDENT) || []) add(name, m.index, 'param')
    }
  }
  const singleArrow = /(?:^|[^\w$.])([A-Za-z_$][\w$]*)\s*=>/g
  while ((m = singleArrow.exec(code))) add(m[1], m.index, 'param')

  return bindings
}

/** Offenders: deps identifiers whose only declarations are later const/let/class. */
export function scanTdzDeps(source) {
  const code = blankCommentsAndStrings(source)
  const bindings = collectBindings(code)
  const offenders = []
  for (const { hook, pos, deps } of findHookDeps(code)) {
    for (const dep of deps) {
      const hits = bindings.filter((b) => b.name === dep)
      if (!hits.length) continue // unknown/global — not this bug class
      // Hoisted bindings never TDZ (var → undefined; function/import → initialized).
      if (hits.some((b) => b.kind === 'import' || b.kind === 'function' || b.kind === 'var')) continue
      // Any binding declared BEFORE the hook call resolves the reference
      // (covers earlier consts and enclosing-scope parameters).
      if (hits.some((b) => b.pos < pos)) continue
      // Only const/let/class bindings are TDZ hazards. A later-only parameter
      // is NOT in scope at the call site (it would be an unresolved global),
      // so it must not suppress a later const's TDZ flag.
      const laterTdz = hits.some((b) =>
        (b.kind === 'const' || b.kind === 'let' || b.kind === 'class') && b.pos > pos)
      if (!laterTdz) continue
      const line = code.slice(0, pos).split('\n').length
      offenders.push({ line, hook, dep, declLine: code.slice(0, hits[0].pos).split('\n').length })
    }
  }
  return offenders
}

test('#2709: no hook deps array references a const declared later in main.jsx', () => {
  const src = readFileSync(join(__dirname, 'main.jsx'), 'utf8')
  const offenders = scanTdzDeps(src)
  assert.deepEqual(offenders, [],
    'hook deps reference later-declared consts (TDZ render crash — #2449/#2709):\n'
    + offenders.map((o) => `L${o.line}: ${o.hook} deps[${o.dep}] -> const declared L${o.declLine}`).join('\n'))
})

// ── Mutation controls ─────────────────────────────────────────────────
// A guard that cannot be shown to fail is not a guard. These fixtures prove
// the scanner flags the known-bad shapes and clears the known-safe ones, so a
// future regex/scanner regression fails loudly here instead of silently
// green-lighting a recurrence.
test('#2709 guard self-check: known-bad deps shapes are flagged', () => {
  const bad = [
    ['single-line deps', 'function App() {\n  React.useEffect(() => { void harnessKey }, [harnessKey])\n  const harnessKey = \'k\'\n}'],
    ['multi-line deps', 'function App() {\n  React.useEffect(() => { void harnessKey }, [\n    wizardStep,\n    harnessKey,\n  ])\n  const harnessKey = \'k\'\n}'],
    ['trailing-line-comment', 'function App() {\n  React.useEffect(() => { void harnessKey }, [harnessKey]) // eslint-disable-line\n  const harnessKey = \'k\'\n}'],
    ['useCallback', 'function App() {\n  const go = React.useCallback(() => harnessKey, [harnessKey])\n  const harnessKey = \'k\'\n}'],
    ['later param must not suppress a const TDZ', 'function App() {\n  React.useEffect(() => {}, [harnessKey])\n  const harnessKey = \'k\'\n  const f = (harnessKey) => harnessKey\n}'],
  ]
  for (const [name, fixture] of bad) {
    const found = scanTdzDeps(fixture)
    assert.equal(found.length, 1, `guard must flag ${name}; got ${JSON.stringify(found)}`)
    assert.equal(found[0].dep, 'harnessKey', `guard flagged wrong dep for ${name}`)
  }
})

test('#2709 guard self-check: known-safe shapes are not flagged', () => {
  const good = [
    ['earlier const', 'function App() {\n  const harnessKey = \'k\'\n  React.useEffect(() => {}, [harnessKey])\n}'],
    ['hoisted function', 'function App() {\n  React.useEffect(() => later(), [later])\n  function later() {}\n}'],
    ['shadowing param (nested scope)', 'function Card({ status }) {\n  React.useEffect(() => {}, [status])\n}\nfunction App() {\n  const status = \'x\'\n}'],
    ['state destructuring', 'function App() {\n  const [step, setStep] = React.useState(0)\n  React.useEffect(() => {}, [step, setStep])\n}'],
  ]
  for (const [name, fixture] of good) {
    assert.deepEqual(scanTdzDeps(fixture), [], `guard must NOT flag ${name}`)
  }
})
