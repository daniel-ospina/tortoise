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
// #3726: the AST seam already exists in this repo (and says why: no hand-rolled
// lexer). Reusing it keeps deps arrays out of the reach of comments and strings.
import { parseModule } from './wizardRenderedCopy.js'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')

/** `source` with comments removed, so a negative pin can be neither satisfied
 *  nor defeated by prose. Block comments and full-line `//` comments (the shape
 *  this file has always used). */
function stripComments(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .filter((line) => !line.trim().startsWith('//'))
    .join('\n')
}

/**
 * The connect-step region of `source`: from the inline mint/paste affordance's
 * declaration to the `{wizardStep === 3 && (` block that follows it, comment-
 * stripped. BOTH markers are `-1`-checked.
 *
 * #3726 cycle 5: the END marker was the last unguarded `indexOf` in this file. An
 * unresolved end makes `slice(start, -1)` the whole rest of the file, which still
 * satisfies a `length > 1000` guard — so the pin silently watches a much larger
 * region: it can red on an unrelated change far below, and it is no longer the
 * connect step it names. The marker is a whitespace-tolerant pattern for the same
 * reason the rest of this PR is quote-agnostic: an exact-text sentinel is a
 * formatting dependency.
 */
function connectStepSlice(source) {
  const live = stripComments(source)
  const start = live.indexOf('const wizardPasteRow = (')
  assert.notEqual(start, -1, 'the connect-step start marker must resolve (without it the slice is nothing)')
  const end = /\{\s*wizardStep\s*===\s*3\s*&&\s*\(/.exec(live.slice(start))
  assert.ok(end, 'the connect-step end marker must resolve (an unresolved end makes the slice the rest of the file)')
  return live.slice(start, start + end.index)
}

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
 * #3726 defect 1: a deps array is the LAST argument of a HOOK call, located by
 * ARITY rather than by the punctuation that happens to precede it.
 *
 * The old scan anchored on `}` or `]` before the comma, so an expression-bodied
 * arrow whose deps array follows a parenthesised call was never scanned at all
 * — measured on main.jsx: `useMemo(() => first(backupInfo && ...), [backupInfo])`
 * at L7652 was skipped. Widening the anchor to `)` would also match an ordinary
 * call taking an array literal (`foo(x, [a, b])`), which is not a deps array and
 * would raise a false violation, so the anchor is the hook call itself.
 *
 * It is read from the AST, not from text: `wizardRenderedCopy.js::parseModule`
 * already exists for exactly this reason and states the rationale — a
 * hand-rolled lexer has to re-implement strings, template interpolation, regex
 * literals, comments and JSX text, and gets them wrong. A first revision here
 * did exactly that and was measured losing deps arrays the OLD anchor caught
 * (a `/\[/` regex inside a hook body desynchronised the paren walk) while
 * inventing violations for a hook call written in a comment or a string.
 *
 * @returns {Array<{start:number, line:number, inner:string, expr:string, names:string[]}>}
 */
export function depsArrays(source) {
  const out = []
  const stack = [parseModule(source)]
  while (stack.length) {
    const node = stack.pop()
    if (Array.isArray(node)) { stack.push(...node); continue }
    if (!node || typeof node !== 'object' || typeof node.type !== 'string') continue
    for (const key of Object.keys(node)) {
      if (key === 'loc' || key === 'leadingComments' || key === 'trailingComments' || key === 'innerComments') continue
      const value = node[key]
      if (value && typeof value === 'object') stack.push(value)
    }
    // Babel emits OptionalCallExpression for `useMemo?.(…)`, so an
    // equality test here silently MISSED those hooks — the fail-open
    // direction, in the one guard that exists for a silent white-screen.
    // No live instance today (no optional hook call in main.jsx).
    if (!/CallExpression$/.test(node.type)) continue
    const name = calleeName(node.callee)
    const last = node.arguments[node.arguments.length - 1]
    if (!name || !last || last.type !== 'ArrayExpression') continue
    if (!isDepsHook(name, node.arguments)) continue
    // The NAMES come from the elements, not from splitting the inner text: a
    // comment inside the array (`[a, // why\n lateB]`) swallowed the identifier
    // that followed it — the same hand-lexing hazard the AST seam removes.
    // The deps EXPRESSION is the elements' own spans with whitespace collapsed,
    // so `a.b` stays distinct from `a` (a named pin below keys on it) while a
    // prettier reflow, a trailing comma or a comment BETWEEN elements is not a
    // change — formatting is not a behaviour, which is this issue's own thesis.
    const names = []
    const exprs = []
    for (const el of last.elements) {
      let n = el
      while (n && /^(Optional)?MemberExpression$/.test(n.type)) n = n.object
      if (n?.type === 'Identifier') names.push(n.name)
      exprs.push(el ? source.slice(el.start, el.end).replace(/\s+/g, ' ').trim() : '')
    }
    out.push({
      start: last.start,
      line: last.loc.start.line,
      inner: source.slice(last.start + 1, last.end - 1),
      expr: exprs.join(', '),
      names,
    })
  }
  return out.sort((a, b) => a.start - b.start)
}

/**
 * The hook's NAME from its callee, in any spelling that is statically known:
 * `useMemo`, `React.useMemo`, `React?.useMemo` (OptionalMemberExpression) and
 * `React['useMemo']` (a COMPUTED member with a static string key).
 *
 * #3726 cycle 4: the computed form and the custom-hook population were the last
 * two shapes origin/main's punctuation anchor caught and the first AST revision
 * did not — a narrowing that the identity pin structurally cannot see, because
 * `found` is produced by the same function (`useThing(() => …, [lateX])` added
 * tomorrow would be invisible to the #2426/#2621/#2709 guard). A DYNAMIC key
 * (`React[hookName]`) is unresolvable statically and stays out of scope.
 */
function calleeName(callee) {
  if (!callee) return null
  if (callee.type === 'Identifier') return callee.name
  if (!/^(Optional)?MemberExpression$/.test(callee.type)) return null
  const property = callee.property
  if (!callee.computed) return property?.type === 'Identifier' ? property.name : null
  return property?.type === 'StringLiteral' ? property.value : null
}

/** The four hooks React defines that take `(callback, deps)`. */
const DEPS_HOOKS = new Set(['useMemo', 'useEffect', 'useLayoutEffect', 'useCallback'])

/**
 * Is this call a hook carrying a deps array?
 *
 * The four `DEPS_HOOKS` are the `(callback, deps)` built-ins. EVERY other hook is
 * scanned too — built-in (`useSyncExternalStore`) or CUSTOM (`useThing(fn, deps)`)
 * — because the arrow/deps shape is a React-wide convention and an allow-list is
 * a silent population narrowing. The guard that keeps the widening honest is
 * ARITY: the deps array must be the LAST of at least two arguments. That is what
 * excludes `useState([a, b])` — an array INITIAL VALUE, its only argument, which
 * a bare `/^use/` prefix rule would have misread as deps (7 such sites in
 * main.jsx today). Residual, stated: a hook that takes the deps array as its ONLY
 * argument is not scanned; no such hook exists here.
 */
function isDepsHook(name, args) {
  if (!/^use[A-Z]/.test(name)) return false
  return DEPS_HOOKS.has(name) || args.length >= 2
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

  for (const deps of depsArrays(source)) {
    const lineNo = deps.line
    const names = deps.names
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

// ── #3726 defect 1: the shape the old punctuation anchor skipped ────────────
test('#3726: analyzer scans a deps array that follows a parenthesised call', () => {
  // Expression-bodied arrow: the last argument is `[lateMemo]` but it is
  // preceded by `)` (the call that produced the value), not by `}` or `]`. The
  // old anchor `/[}\]]\s*,\s*\[/` therefore skipped the array entirely and a
  // later-declared const in it was invisible — one deps array in main.jsx
  // (L7652) was missed by exactly this shape.
  const bad = [
    'function App() {',
    '  const memo = React.useMemo(() => pick(ready, lateMemo), [lateMemo])',
    '  const lateMemo = 1',
    '  return memo',
    '}',
  ].join('\n')
  assert.deepEqual(
    findTdzDeps(bad).map((v) => v.name),
    ['lateMemo'],
    'a deps array after a parenthesised call must still be analysed',
  )
})

test('#3726: a NON-hook call taking an array literal is not a deps array', () => {
  // Guards the REJECTED design, not the shipped one: origin/main's `[}\]]`
  // anchor does not match this either, so this test is green on both. It exists
  // to stop a future fix from "solving" the missing shape by widening the
  // character class to `)`, which would report `lateArr` here as a TDZ
  // violation — a false red. Anchoring on the hook call cannot.
  const source = [
    'function App() {',
    '  describe(pick(ready), [lateArr])',
    '  const lateArr = 1',
    '  return null',
    '}',
  ].join('\n')
  assert.deepEqual(findTdzDeps(source), [],
    'a plain call with an array-literal argument is not a hook deps array')
})

test('#3726: the analyzer sees EXACTLY these hook deps arrays in main.jsx', () => {
  // An identity pin, not a count. A `>= N` pin is satisfiable by non-code (a
  // comment or a string that looks like a hook call) and cannot see an
  // invention masking a loss — measured: with `>= 41` the suite stayed green
  // after the real L7652 `[backupInfo]` array was DELETED and two comment-only
  // hook calls were added in its place. This list fails on a loss AND on an
  // invention, so a change to the scan or to main.jsx must be deliberate.
  const found = depsArrays(mainJsx).map((d) => d.expr)
  // The deps EXPRESSIONS, not their line numbers: a line number reddens this
  // pin on any unrelated inserted line, which trains people to re-baseline it.
  // They are the ELEMENTS' spans with whitespace collapsed (`expr`), not the
  // array's raw inner text: `inner.trim()` reddened on a prettier reflow of an
  // array or a trailing comma — formatting changes that alter no behaviour,
  // which is this issue's own thesis — while still keying on content, so a
  // deleted, changed or added deps ENTRY reddens (falsifiability test below).
  assert.deepEqual(found, [
  "active, ms",
  "text",
  "text, onCopied",
  "githubDisconnect.open, githubDisconnect.busy",
  "deleteAccountOpen, deleteAccountBusy",
  "apiKey",
  "keysLoaded, keysLoadNonce",
  "",
  "",
  "welcomeMode, authed",
  "wizardStep, welcomeMode, authed",
  "wizardStep, welcomeMode, authed, serverHarnessConnected",
  "wizardStep",
  "wizardStep, welcomeMode, authed, wizardPaused, effectivelyPaused, serverHarnessConnected, isBuildFork, welcomeHasOrg",
  "",
  "tab",
  "",
  "tab, team, checking, authed, welcomeMode, deepLinkTick",
  "reposLoaded, onboarding, reposList, currentOrgId, reposLoadFailed",
  "accountMenuOpen",
  // #2728: the dialog's background-inert effect, keyed on the graph it holds.
  "packsGraphId",
  "createTeamOpen",
  "revealKey",
  "authMode, sessionBooted",
  "sessionBooted",
  "team?.subscription_status",
  "",
  "welcomeMode, welcomeKey, wizardDurableKey",
  "wizardStep, wizardForkChosen, onboarding && onboarding.fork",
  "branchLists, docsScope",
  "githubConnected, reposLoaded, reposLoadFailed, currentOrgId",
  "onboarding, currentOrgId, reposLoadFailed",
  "wizardStep",
  "",
  "",
  "currentOrgId",
  // #2728: the Configured-packs home's summary read (Settings tab + the
  // selected graph + the graph list that supplies the initial default).
  "tab, packsHomeGraphId, graphs, currentOrgId",
  "overviewDataComplete, overviewAnnounced",
  "tab, welcomeMode, team",
  "overviewSkeletonLive, frameStale",
  "keys",
  "backupInfo",
  "tab",
  ])
})

test('#3726 falsifiability: the identity pin compares deps EXPRESSIONS, not their formatting', () => {
  // The pin keys on CONTENT, so a reflow of an array (and a comment BETWEEN its
  // elements) must be the same entry — a prettier pass is not a behaviour change,
  // which is this issue's own thesis — while a changed, dropped or added entry
  // must be a different one. The old key (the array's raw inner text) failed the
  // first half: it reddened on formatting.
  const reflowed = [
    'const a = useMemo(',
    '  () => pick(ready),',
    '  [',
    '    ready, // the ready flag',
    '    lateB,',
    '  ],',
    ')',
  ].join('\n')
  assert.deepEqual(depsArrays(reflowed).map((d) => d.expr), ['ready, lateB'],
    'a reflowed array with a comment between elements is the same deps expression')
  assert.notEqual(depsArrays(reflowed)[0].inner.trim(), 'ready, lateB',
    'the raw inner text IS formatting-sensitive — keying on it reddened on a prettier pass')
  // Content moves the pin in all three directions.
  assert.deepEqual(depsArrays('const a = useMemo(() => 1, [ready])').map((d) => d.expr), ['ready'],
    'a dropped entry must redden the pin')
  assert.deepEqual(depsArrays('const a = useMemo(() => 1, [ready, lateC])').map((d) => d.expr), ['ready, lateC'],
    'a changed entry must redden the pin')
  assert.deepEqual(depsArrays('const a = useMemo(() => 1, [ready, lateB, lateC])').map((d) => d.expr), ['ready, lateB, lateC'],
    'an added entry must redden the pin')
  // A member expression stays DISTINCT from its root, so `team?.status` cannot be
  // mistaken for `team` (or vice versa) by the pin.
  assert.deepEqual(depsArrays('const a = useMemo(() => 1, [team?.status])').map((d) => d.expr), ['team?.status'])
  assert.deepEqual(depsArrays('const a = useMemo(() => 1, [team])').map((d) => d.expr), ['team'])
})

test('#3726: a regex literal inside a hook body does not hide its deps array', () => {
  // origin/main's anchor already caught these, so this is not evidence for the
  // AST seam — it is a REGRESSION GUARD against returning to a character
  // scanner: a hand-rolled paren walk desynchronised on `/\[/` and `/\)/`, so
  // the hook was dropped entirely and a TDZ violation in its deps array shipped
  // silently (measured on the branch's first commit).
  for (const body of ['const m = /\\[/g; go(m)', 'const m = /\\)/g; go(m)', 'go(/[)]/)']) {
    const source = [
      'function App() {',
      `  React.useEffect(() => { ${body} }, [lateVal])`,
      '  const lateVal = 1',
      '  return null',
      '}',
    ].join('\n')
    assert.deepEqual(findTdzDeps(source).map((v) => v.name), ['lateVal'],
      `a regex literal (${body}) must not hide the deps array`)
  }
})

test('#3726: a hook call written in a comment, a string or JSX text is not code', () => {
  // Also a regression guard rather than evidence for the AST seam (origin/main's
  // anchor does not match these either): a text scan reported them as
  // violations, which would red the suite for documenting the rule it enforces.
  const source = [
    'function App() {',
    '  // do NOT write useMemo(() => x, [lateComment]) here',
    '  const msg = "call useMemo(fn, [lateStr]) first"',
    '  return <p>Use useMemo(fn, [lateJsx]) for that</p>',
    '}',
  ].join('\n')
  assert.deepEqual(findTdzDeps(source), [],
    'comments, string literals and JSX text are not hook calls')
})

test('#3726: an OPTIONAL hook call is still scanned', () => {
  // Babel emits `OptionalCallExpression` for `useMemo?.(…)`. An equality test
  // against 'CallExpression' silently dropped the hook — a fail-open miss in the
  // guard whose whole job is to catch a silent white-screen. No live instance in
  // main.jsx today; pinned so the fix cannot be removed quietly.
  const source = [
    'function App() {',
    '  React.useMemo?.(() => 1, [lateOpt])',
    '  const lateOpt = 1',
    '  return null',
    '}',
  ].join('\n')
  assert.deepEqual(findTdzDeps(source).map((v) => v.name), ['lateOpt'],
    'a hook called with `?.` still has a deps array')
})

test('#3726: `React?.useMemo(…)` (optional MEMBER) is scanned too', () => {
  // The call side was hardened for `useMemo?.(…)` but the CALLEE side still
  // required `MemberExpression`; `React?.useMemo(…)` parses as CallExpression
  // with an OptionalMemberExpression callee, so it was dropped — fail-open, in
  // the guard whose job is to catch a silent white-screen. `x?.y(…)` is the
  // idiom main.jsx already uses (7 optional calls today, none a hook).
  for (const call of ['React?.useMemo(() => 1, [lateOM])', 'obj?.useCallback(() => 1, [lateOM])']) {
    const source = [
      'function App() {',
      `  ${call}`,
      '  const lateOM = 1',
      '  return null',
      '}',
    ].join('\n')
    assert.deepEqual(findTdzDeps(source).map((v) => v.name), ['lateOM'],
      `an optional member callee must not hide the deps array: ${call}`)
  }
})

test('#3726: a COMPUTED-MEMBER hook callee (`React[\'useMemo\']`) is scanned too', () => {
  // The callee had to be non-computed, so a computed member with a STATIC key —
  // which origin/main's `}, […]` anchor caught — was skipped: a population
  // narrowing the identity pin cannot see (the same function produces `found`).
  for (const call of ["React['useMemo'](() => 1, [lateCM])", 'React["useEffect"](fn, [lateCM])']) {
    const source = [
      'function App() {',
      `  ${call}`,
      '  const lateCM = 1',
      '  return null',
      '}',
    ].join('\n')
    assert.deepEqual(findTdzDeps(source).map((v) => v.name), ['lateCM'],
      `a computed member callee with a static key must not hide the deps array: ${call}`)
  }
})

test('#3726: a CUSTOM hook carrying a deps array is scanned (no allow-list narrowing)', () => {
  // The allow-list of four names meant `useThing(() => …, [lateCustom])` — added
  // tomorrow — was invisible to the #2426/#2621/#2709 guard, and the identity pin
  // could not notice, because it is produced by the same scanner. Every `use[A-Z]`
  // hook is scanned now; `useSyncExternalStore` (a real one in main.jsx) is here
  // too even though it takes no deps array, so the test is about the population.
  for (const call of ['useThing(() => 1, [lateCustom])', 'useSyncExternalStore(subscribe, getSnapshot, [lateCustom])']) {
    const source = [
      'function App() {',
      `  ${call}`,
      '  const lateCustom = 1',
      '  return null',
      '}',
    ].join('\n')
    assert.deepEqual(findTdzDeps(source).map((v) => v.name), ['lateCustom'],
      `a hook outside the four built-ins must not hide its deps array: ${call}`)
  }
})

test('#3726: an array INITIAL STATE is not a deps array (the widening must not invent one)', () => {
  // The guard on the widening. `/^use/` on its own would read `useState([a, b])`
  // as a deps array — 7 such sites exist in main.jsx — and report the array's
  // ELEMENTS as TDZ violations (a false red). A deps array is the last of at least
  // two arguments; an initial state is the only argument.
  const source = [
    'function App() {',
    '  const [keys, setKeys] = React.useState([lateA, lateB])',
    '  const [text, setText] = React.useState([])',
    '  const lateA = 1',
    '  const lateB = 2',
    '  return null',
    '}',
  ].join('\n')
  assert.deepEqual(findTdzDeps(source), [],
    'a useState initial VALUE is not deps — inventing a violation here would red the suite on correct code')
})

test('#3726: a comment inside a deps array does not swallow the next name', () => {
  // The names came from splitting the array's INNER TEXT on commas, so a line
  // comment ate the identifier after it — the same hand-lexing hazard the AST
  // seam removes. Elements give the names directly.
  const source = [
    'function App() {',
    '  const ready = 1',
    '  React.useEffect(fn, [',
    '    ready, // the ready flag',
    '    lateAfterComment,',
    '  ])',
    '  const lateAfterComment = 2',
    '  return null',
    '}',
  ].join('\n')
  assert.deepEqual(findTdzDeps(source).map((v) => v.name), ['lateAfterComment'],
    'the identifier after a comment in the deps array must still be checked')
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
  const live = stripComments(mainJsx)
  assert.doesNotMatch(
    live,
    /wizardStep === 2 && isOwnerAdmin && !harnessKey && !capNotice/,
    'the connect-step auto-open modal effect must not return (#2710 stray-modal leak)',
  )
  assert.doesNotMatch(
    live,
    /\[\s*wizardStep\s*,\s*wizardHarness\s*,\s*harnessKey\s*\]/,
    'the #2709 deps array must not return',
  )
  // Structural companion: the whole wizard tree must contain no queue call at
  // all (the only live one is the keys tab's "+ New key", outside this slice).
  // #3726 cycle 5: the slice's END is guarded inside the helper — an unresolved
  // end used to be silently `slice(start, -1)` = everything to EOF.
  const connect = connectStepSlice(mainJsx)
  assert.ok(connect.length > 1000, 'the connect-step slice must resolve (markers moved?)')
  assert.doesNotMatch(connect, /setKeyModalOpen\(true\)/,
    'no wizard path may queue the shared key-create modal')
})

test('#2710 falsifiability: the connect-step slice is bounded at BOTH ends', () => {
  // Reproduces the pre-fix reader's failure mode and pins the guarded one.
  const build = (marker) => [
    '  const wizardPasteRow = (',
    '    <div id="wizard-paste-row">paste</div>',
    '  )',
    ...(marker ? [marker] : []),
    '  go(setKeyModalOpen(true))',
  ].join('\n')
  // A reflowed end marker is the same marker, and the slice still CLOSES there.
  const reflowed = build('  { wizardStep === 3 && (')
  assert.ok(!connectStepSlice(reflowed).includes('setKeyModalOpen'),
    'the slice must END at the marker — the code after it belongs to a different step')
  // With the marker gone, the pre-fix `indexOf` returns -1 and `slice(start, -1)`
  // is everything to EOF: it still satisfies `length > 1000` and silently watches
  // `setKeyModalOpen(true)`, which is not the region the pin claims to watch.
  const unmarked = [build(null), ...Array.from({ length: 40 }, (_, i) => `  const pad${i} = ${'0'.repeat(40)}`)].join('\n')
  const preFixSlice = unmarked.slice(
    unmarked.indexOf('const wizardPasteRow = ('),
    unmarked.indexOf('{wizardStep === 3 && ('),
  )
  assert.ok(preFixSlice.length > 1000 && preFixSlice.includes('setKeyModalOpen'),
    'the unguarded end watched the rest of the file — `length > 1000` passed while the window was not the connect step')
  assert.throws(() => connectStepSlice(unmarked), /end marker must resolve/,
    'an unresolved end must fail LOUD, not widen the window to EOF')
})
