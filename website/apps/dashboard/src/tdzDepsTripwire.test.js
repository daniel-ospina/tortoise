// tdzDepsTripwire.test.js — effect-deps TDZ static tripwire.
//
// WHY THIS EXISTS: the dashboard white-screened for EVERY signed-in user three
// separate times from the same mistake — an effect/hook deps array listing an
// identifier that is a `const` declared LATER in the same component:
//
//   #2426 → #2621 → #2709 (`}, [wizardStep, wizardHarness, harnessKey])`, where
//   `harnessKey` is declared ~4,500 lines below the effect).
//
// A deps array is evaluated EAGERLY during render, so a later-declared const
// throws `ReferenceError: Cannot access 'X' before initialization` on EVERY
// render. Unauthenticated loads redirect before the component mounts, so the
// deploy gate, bundle greps, and unauthenticated smoke tests all miss it.
//
// The analyzer is a pure function over source text, and it is SELF-TESTED with
// synthetic good/bad snippets so the tripwire cannot silently rot into a no-op.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')

const FN = /^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)/
const ARROW = /^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:\(([^)]*)\)|([A-Za-z_$][\w$]*))\s*=>/

/** Split a param list into bare identifier names. */
function paramNames(raw) {
  return raw
    .replace(/[{}[\]]/g, ' ')
    .split(',')
    .map((s) => s.split(':').pop().split('=')[0].trim())
    .filter((s) => /^[A-Za-z_$][\w$]*$/.test(s))
}

/**
 * Names bound by a single declaration line, including destructuring:
 *   const x = 1            -> ['x']
 *   const [a, setA] = ...  -> ['a', 'setA']
 *   const { p, q } = ...   -> ['p', 'q']
 *   const { a: z } = ...   -> ['z']
 * Returns null when the line is not a declaration.
 */
export function declNames(line) {
  const m = line.match(/^\s*(?:export\s+)?(?:const|let|var)\s+(.+)$/)
  if (!m) return null
  const rest = m[1]
  // first '=' at bracket depth 0 (so `{ a = 1 }` does not fool us)
  let depth = 0
  let eq = -1
  for (let i = 0; i < rest.length; i++) {
    const ch = rest[i]
    if (ch === '{' || ch === '[') depth++
    else if (ch === '}' || ch === ']') depth--
    else if (ch === '=' && depth === 0) { eq = i; break }
  }
  if (eq === -1) return null
  const target = rest.slice(0, eq).trim()
  if (/^[A-Za-z_$][\w$]*$/.test(target)) return [target]
  const names = paramNames(target)
  return names.length ? names : null
}

/**
 * Analyze source text for the #2426/#2621/#2709 bug class.
 *
 * A deps-array identifier is a violation when it is neither:
 *   (a) declared with const/let/var/function BEFORE the deps array, nor
 *   (b) a parameter of the nearest preceding function signature (a component
 *       prop or a hook param — those are bound before the deps array runs).
 * Anything else must be declared below → TDZ at render time.
 *
 * @returns {Array<{line:number, name:string, declaredAt:number|null}>}
 */
export function findTdzDeps(source) {
  const lines = source.split('\n')
  const violations = []

  // First declaration line per identifier (file order).
  const declaredAt = new Map()
  for (let i = 0; i < lines.length; i++) {
    const fn = lines[i].match(FN)
    if (fn && !declaredAt.has(fn[1])) declaredAt.set(fn[1], i + 1)
    const names = declNames(lines[i])
    if (names) {
      for (const n of names) if (!declaredAt.has(n)) declaredAt.set(n, i + 1)
    }
  }

  // Deps arrays: `}, [ ... ])` / `], [ ... ])` — the trailing `)` scopes the
  // match to a hook call closing, not an arbitrary object literal.
  const re = /[}\]]\s*,\s*\[([^\]]*)\]\s*\)/g
  let m
  while ((m = re.exec(source)) !== null) {
    const lineNo = source.slice(0, m.index).split('\n').length
    const names = m[1]
      .split(',')
      .map((s) => s.trim())
      .filter((s) => /^[A-Za-z_$][\w$]*$/.test(s))
    if (names.length === 0) continue

    for (const name of names) {
      const decl = declaredAt.get(name)
      if (decl !== undefined && decl < lineNo) continue // (a) declared above

      // (b) nearest preceding function/hook signature params
      let isParam = false
      for (let i = lineNo - 1; i >= 0; i--) {
        const fn = lines[i].match(FN)
        if (fn) {
          if (paramNames(fn[2]).includes(name)) isParam = true
          break
        }
        const arrow = lines[i].match(ARROW)
        if (arrow) {
          const params = arrow[2] !== undefined ? arrow[2] : arrow[3]
          if (paramNames(params).includes(name)) isParam = true
          break
        }
      }
      if (isParam) continue

      violations.push({ line: lineNo, name, declaredAt: decl ?? null })
    }
  }
  return violations
}

// ── self-test: the analyzer must actually detect the pattern ────────────────
test('tdz tripwire: analyzer flags a later-declared const in a deps array', () => {
  const bad = [
    'function App() {',
    '  const ready = false',
    '  React.useEffect(() => {',
    '    if (ready) { go() }',
    '  }, [ready, lateConst])',
    '  const lateConst = 1',
    '  return null',
    '}',
  ].join('\n')
  const found = findTdzDeps(bad)
  assert.deepEqual(
    found.map((v) => v.name),
    ['lateConst'],
    'only the later-declared const is a violation (ready is declared above)',
  )
})

test('tdz tripwire: analyzer reads destructuring + useState declarations', () => {
  const bad = [
    'function App() {',
    '  const [step, setStep] = React.useState(0)',
    '  React.useEffect(() => { setStep(1) }, [step, keyFromLater])',
    '  const keyFromLater = useKey()',
    '  return null',
    '}',
  ].join('\n')
  assert.deepEqual(
    findTdzDeps(bad).map((v) => v.name),
    ['keyFromLater'],
    'a `const [a, b] = useState()` declaration must count as declared-above',
  )
})

test('tdz tripwire: analyzer clears props, params, and earlier consts', () => {
  const good = [
    'function Card({ status }) {',
    '  React.useEffect(() => { setStale(false) }, [status])',
    '  const [stale, setStale] = React.useState(false)',
    '  React.useCallback(() => { use(status, stale) }, [stale, status])',
    '  return null',
    '}',
  ].join('\n')
  assert.deepEqual(findTdzDeps(good), [], 'no violations expected')
})

// ── the real file ───────────────────────────────────────────────────────────
test('#2709: main.jsx has no effect-deps referencing a later-declared const', () => {
  const violations = findTdzDeps(mainJsx)
  const detail = violations
    .map((v) => `L${v.line} deps=[..., ${v.name}] declared@L${v.declaredAt}`)
    .join('; ')
  assert.deepEqual(
    violations,
    [],
    `TDZ-in-deps would white-screen every signed-in render (#2709): ${detail}`,
  )
})

test('#2710: the connect-step auto-open effect is GONE (no queued-modal leak)', () => {
  // #2709's effect was the #2710 root cause: it set `keyModalOpen(true)` on
  // arrival at the connect step, but the shared modal's JSX renders only in
  // the post-welcome dashboard tree — so the flag sat queued during the
  // wizard and popped a stray "Create new API key" modal (30-day default) on
  // exit. #2710 deletes the effect; the connect step owns an inline
  // mint/paste affordance instead. This pins the removal so a future edit
  // cannot quietly reinstate the TDZ-bearing queue-while-invisible effect.
  //
  // Comment-stripped (code-review P2, PR #2771 round 1): a raw regex over the
  // 8k-line file can be satisfied or defeated by a comment, and it only matches
  // one exact single-line formatting. Both negatives run on the stripped source
  // so only LIVE code can fail them.
  const live = mainJsx
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .filter((line) => !line.trim().startsWith('//'))
    .join('\n')
  assert.doesNotMatch(
    live,
    /wizardStep === 2 && isOwnerAdmin && !harnessKey && !capNotice/,
    'the connect-step auto-open modal effect must not return (#2710 stray-modal leak)',
  )
  assert.doesNotMatch(
    live,
    /\[wizardStep, wizardHarness, harnessKey\]/,
    'the #2709 deps array must not return',
  )
  // Structural companion: the whole wizard tree must contain no queue call at
  // all (the only live one is the keys tab's "+ New key", outside this slice).
  const connect = live.slice(
    live.indexOf('const wizardPasteRow = ('),
    live.indexOf('{wizardStep === 3 && ('),
  )
  assert.ok(connect.length > 1000, 'the connect-step slice must resolve (markers moved?)')
  assert.doesNotMatch(connect, /setKeyModalOpen\(true\)/,
    'no wizard path may queue the shared key-create modal')
})
