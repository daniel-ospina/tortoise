// hookDepsTdz.test.js — run with node --test (#2709 regression guard).
//
// Source scan (wizardArchived.test.js style — reads main.jsx as TEXT, no React
// runtime): within a single component/function scope, no hook deps array may
// reference a const/let whose ONLY declaration in that scope sits LATER in
// the file.
//
// Why: React evaluates a hook's deps array eagerly during render. A binding
// declared later in the same function scope is still in its temporal dead
// zone at that point, so listing it throws
//   ReferenceError: Cannot access '<name>' before initialization
// on EVERY render. That is the white-screen class that shipped three times:
// #2426, #2621, and #2709 (`harnessKey` in the wizard auto-open-modal effect
// deps — App() crashed for every signed-in user; unauthenticated loads
// redirect before App mounts, so the login gate never saw it).
//
// The runtime net is tests/e2e/test_dashboard_mount_smoke.py (real authed
// render, zero console errors). This scan is the cheap always-on unit net: it
// fails in the zero-dep `node --test` lane in milliseconds, before any build.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const raw = readFileSync(join(__dirname, 'main.jsx'), 'utf8')
// Strip block comments so a documentation example can never masquerade as
// code. Full-line `//` comments cannot match either pattern below.
const src = raw.replace(/\/\*[\s\S]*?\*\//g, '')
const lines = src.split('\n')

// Partition the file by top-level (column-0) function declarations. Deps
// identifiers resolve in their own component scope, so a same-named const in
// a DIFFERENT component must never be treated as the binding (that cross-scope
// contamination is what would flag a prop like `status` against a later
// `const status` inside App()).
const scopeStarts = []
lines.forEach((line, i) => {
  if (/^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+[A-Za-z_$][\w$]*\s*\(/.test(line)) {
    scopeStarts.push(i)
  }
})
const scopes = []
scopeStarts.forEach((start, n) => {
  const end = n + 1 < scopeStarts.length ? scopeStarts[n + 1] : lines.length
  scopes.push([start, end])
})

// `var` hoists (no TDZ) and function declarations hoist, so neither is a
// hazard — only const/let at the scope's statement depth (2-space indent,
// which is where component-body declarations live in main.jsx).
function declaredLater(scopeLines, offset, dep, depsIdx) {
  const hits = []
  scopeLines.forEach((line, j) => {
    const m = /^ {2}(?:const|let)\s+(.+?)\s*=(?:[^=]|$)/.exec(line)
    if (!m) return
    for (const name of m[1].match(/[A-Za-z_$][\w$]*/g) || []) {
      if (name === dep) hits.push(offset + j)
    }
  })
  return hits.length > 0 && hits.every((n) => n > depsIdx)
}

test('#2709: no hook deps array references a const declared later in its scope', () => {
  const offenders = []
  for (const [start, end] of scopes) {
    const scopeLines = lines.slice(start, end)
    scopeLines.forEach((line, j) => {
      const i = start + j
      // a deps-array close: `}, [a, b])` or `], [a])` on a line tail.
      const m = /^\s*[\]}]\s*,\s*\[([^\]]*)\]\s*\)\s*;?\s*$/.exec(line)
      if (!m) return
      for (const dep of m[1].split(',').map((s) => s.trim()).filter(Boolean)) {
        // only plain identifiers can TDZ — skip member exprs / calls / literals
        if (!/^[A-Za-z_$][\w$]*$/.test(dep)) continue
        if (declaredLater(scopeLines, start, dep, i)) {
          offenders.push(`L${i + 1}: deps[${dep}] declared only later in the same scope`)
        }
      }
    })
  }
  assert.deepEqual(offenders, [],
    'hook deps reference later-declared consts (TDZ render crash — #2426/#2621/#2709):\n'
    + offenders.join('\n'))
})
