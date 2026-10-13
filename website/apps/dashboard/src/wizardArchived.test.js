// wizardArchived.test.js — run with node --test. Source-scan assertions for
// the #1997 (W1) archived-not-deleted contract (DE2E-1): the legacy #1643
// wizard is NEVER rendered by the live surface, but its JSX + labels remain
// in source (A0 rollback path, epic §8). Reads main.jsx as TEXT — no React
// runtime needed.
//
// #3802 adds the REACHABILITY half: the same flag that makes the legacy JSX
// dead also decides whether a copy constant main.jsx imports can reach a user
// at all. That census is derived from the AST (not from a text scan — see the
// `classifyCopyReads` docstring) and pinned, so a constant that silently loses
// its last LIVE read site fails here.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
// The ONE Babel parse config for main.jsx (#4885 shares it) — a second literal
// here would drift from the probe's `plugins`.
import { parseModule } from './wizardRenderedCopy.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(join(__dirname, 'main.jsx'), 'utf8')

test('legacy #1643 wizard render is gated behind the ARCHIVED flag (never rendered)', () => {
  // The legacy wizard's gate must be the archived flag, not the live path.
  assert.ok(/LEGACY_WIZARD_ARCHIVED\s*&&\s*welcomeOriented\s*&&/.test(src),
    'legacy wizard gated by LEGACY_WIZARD_ARCHIVED && welcomeOriented')
  assert.ok(/const LEGACY_WIZARD_ARCHIVED\s*=\s*false/.test(src),
    'LEGACY_WIZARD_ARCHIVED is false (never rendered)')
})

test('archived marker comment present (rollback drill reference)', () => {
  assert.ok(src.includes('⛔ ARCHIVED — #1997 (W1)'), 'ARCHIVED marker header')
})

test('legacy wizard labels still exist in source (archived-not-deleted)', () => {
  // the legacy labels array uses JS-escaped apostrophes (\')
  for (const label of ["Connect your tool", "Memory sources",
                       "Your agent\\'s toolkit", "Seed your graph", "You\\'re set"]) {
    assert.ok(src.includes(label), `legacy label archived: ${label}`)
  }
})

test('live wizard renders WIZARD_STEPS (the 5 human steps), not legacy labels', () => {
  assert.ok(src.includes('WIZARD_STEPS.map'), 'live wizard maps WIZARD_STEPS')
  // #2912: the stage label moved from an in-card `.wizard-title` to the page
  // <h1> (the header now names the STAGE, not the org's set-up status). The
  // label is now the exported pure helper `wizardStageLabel` (unit-tested in
  // wizardFlow.test.js — a source grep cannot prove the override ORDER), so all
  // this tripwire has to pin is that the <h1> uses it. #2487's in-card
  // `wizard-title` branch was merged AWAY by #2912 (it reintroduced the duplicate
  // stage label defect 5 reported); its org-holder invariant lives on through the
  // helper's `hasOrg` arm and the e2e's "never 'Create your Organization'" pin.
  const h1Open = src.indexOf('<h1 className="welcome-title">')
  const h1 = src.slice(h1Open, src.indexOf('</h1>', h1Open))
  assert.ok(h1.includes('wizardStageLabel(wizardStep, { hasOrg: welcomeHasOrg, paused: effectivelyPaused, connected: serverHarnessConnected, buildFork: isBuildFork })'),
    'the live wizard <h1> names the stage through wizardStageLabel (the org-holding, paused, #3428 not-connected and cycle-4 build-fork overrides are pinned by the unit tests)')
  assert.ok(!src.slice(src.indexOf('<div className="welcome-head">'), src.indexOf('LEGACY_WIZARD_ARCHIVED &&')).includes('className="wizard-title"'),
    'no in-card stage label may return in the LIVE wizard (the archived rollback block keeps its own)')
  // the archived block's title still reads wizardSteps (kept for rollback)
  assert.ok(/wizard-title">\{wizardSteps\[wizardStep\]\}/.test(src),
    'archived legacy title retained (wizardSteps)')
})

test('DE2E-2 copy sweep: org-create dialog + wizard copy say Organization', () => {
  assert.ok(src.includes('Create a new organization'), 'create-team dialog header')
  assert.ok(src.includes('Organization name required'), 'validation error copy')
  // #2912: the welcome header no longer prints '<org> is set up' (the stage is
  // the h1) — the ready copy is the org-create summary line, which must still
  // name the Organization. #2487/#2547's lowercase fallback ('Your organization
  // is set up') lived in the <h1> this PR removed; the org name now renders in
  // the eyebrow, and the no-name case is covered by the stage label itself.
  assert.ok(/You're set up in <strong>\{shownOrgName \|\| 'your organization'\}<\/strong>/.test(src),
    'welcome ready copy names the user\'s Organization')
  assert.ok(src.includes('Creating your Organization and API key'), 'provisioning copy')
  assert.ok(src.includes('Your Organization and API key are live'), 're-entry + first-data cards')
})

test('wizardComplete no longer writes onboarding_complete (accept-and-drop, plan T7)', () => {
  assert.ok(!/body:\s*JSON\.stringify\(\{\s*onboarding_complete:\s*true\s*\}\)/.test(src),
    'wizardComplete dropped the PATCH onboarding_complete write')
})

test('review P1 / #3913: the build fork handler writes NO checkpoint step (the catalog card still renders)', () => {
  // #1997 (review P1) pinned the handler's catalog-presented write; #3913
  // (owner ruling 2026-09-20) removed it — the build-fork gate completes on the
  // two acts the server OBSERVES (harness-connected + first-points-filed), so a
  // fork pick must record no step. The build branch still exists: a build pick
  // stays on the fork step so the catalog CARD renders, and self picks advance.
  assert.ok(src.includes("if (forkId !== 'build')"),
    'handleWizardFork still branches on the build pick (non-build picks advance, build stays for the card)')
  assert.ok(src.includes('Build catalog'),
    'the build catalog CARD still renders (only the write was removed)')
  assert.ok(!/step: 'catalog-presented'/.test(src),
    'no catalog-presented checkpoint anywhere in main.jsx — #3913 removed the writer')
})

// ── #3802: which imported copy constants can a USER actually receive ────────
//
// WHY THIS EXISTS. `LEGACY_WIZARD_ARCHIVED = false` makes the #1643 render dead
// — and that render is the ONLY reader of a whole family of copy constants, so
// an edit to `HARNESS_INSTALL` (or one of its siblings) reaches no user while an
// assertion on it in `harnesses.test.js` READS as a shipped-behavior claim.
// #3754 shipped that way: its `timeout` fix had to be applied to a live copy AND
// to the reference-only one, and a contributor patching only the reference copy
// would have seen the suite stay green. #3802's target is "0 shipped-behavior
// claims pinned against non-rendered copy without a linked assertion on the
// rendered copy" — the census below is the machine half of that: it NAMES which
// constants render and which do not.
//
// WHAT IS PINNED. Every constant main.jsx imports from the two COPY modules
// (`./harnesses.js` — the HARNESS_* vocabulary; `./wizardPrompts.js` — the
// wizard's prompt builders) is classified by its READ SITES: `live` = the read
// can render; `archived` = the read sits behind a gate that is provably false at
// that point. The map is hand-pinned, so a constant that silently loses its last
// live read site — the defect class — reds here with a named diff, and so does
// one that GAINS a live read site (a shipped change, not a default).
//
// HOW THE GATE IS DERIVED, not hardcoded. A DEAD FLAG is a `const NAME = false`
// with EXACTLY ONE declarator of NAME in the file (a second declarator would be
// a shadow this scan cannot follow — it fails LOUD instead of guessing which
// binding a read means). A read is ARCHIVED when it sits behind a gate on such a
// flag with the POLARITY the flag's value makes dead:
//
//   `FLAG && X` / `if (FLAG) S` / `FLAG ? S : B`  → dead, because FLAG is false
//   `!FLAG && X` / `if (!FLAG) S` / `!FLAG ? S : B` → LIVE, because !FLAG is true
//   `if (!FLAG) return` → the statement tail after it is dead (it never runs)
//   `if (FLAG) return`  → the tail is LIVE (the return never fires)
//
// That last pair is not hypothetical: main.jsx uses `if (FLAG) return` for the
// step-change announcement effect and `if (!FLAG) return` for the seed-prefill
// effect, so both polarities are exercised on this file.
//
// RESIDUAL, stated because the opposite has been claimed in this repo before and
// was false. A flag composed into another expression (`const dead = FLAG || x`),
// a flag read through an alias, or a gate that depends on a function ARGUMENT is
// not followed — such a read is classified `live`. The guard stays fail-loud for
// the class it covers: changing a gate this scan DOES see moves the census, and
// the falsifiability tests below mutate its input (both polarities) to prove the
// classification can red rather than pass vacuously.

const COPY_MODULES = ['./harnesses.js', './wizardPrompts.js']

/** Pre-order walk; `visit(node, parent)` — the parent is what makes a gate
 *  analysable (an `&&` operand is not identifiable from the node alone). */
function walk(root, visit, parent = null) {
  if (Array.isArray(root)) { for (const child of root) walk(child, visit, parent); return }
  if (!root || typeof root.type !== 'string') return
  visit(root, parent)
  for (const key of Object.keys(root)) {
    if (key === 'loc' || key.endsWith('Comments')) continue
    const value = root[key]
    if (value && typeof value === 'object') walk(value, visit, root)
  }
}

/**
 * How `test` gates on a dead flag, or `null`:
 * `'is'` for a bare `FLAG` (FALSE at runtime) and `'isNot'` for `!FLAG` (TRUE).
 * The polarity is the whole point — `FLAG && X` kills X, `!FLAG && X` runs it.
 */
function flagGate(test, flags) {
  if (test.type === 'Identifier' && flags.includes(test.name)) return 'is'
  if (test.type === 'UnaryExpression' && test.operator === '!'
    && test.argument.type === 'Identifier' && flags.includes(test.argument.name)) return 'isNot'
  return null
}

/** `const NAME = false` declarators — the flags whose guarded operands are dead. */
function deadFlags(ast) {
  const counts = new Map()
  walk(ast.program, (node) => {
    if (node.type === 'VariableDeclarator' && node.id.type === 'Identifier') {
      counts.set(node.id.name, (counts.get(node.id.name) ?? 0) + 1)
    }
  })
  const flags = []
  walk(ast.program, (node, parent) => {
    if (node.type !== 'VariableDeclarator' || node.id.type !== 'Identifier') return
    if (!(node.init && node.init.type === 'BooleanLiteral' && node.init.value === false)) return
    let declaration = parent
    while (declaration && declaration.type !== 'VariableDeclaration') declaration = parent
    if (!declaration || declaration.kind !== 'const') return
    assert.equal(counts.get(node.id.name), 1,
      `${node.id.name} has ${counts.get(node.id.name)} declarators — the reachability scan follows ONE binding, `
      + 'so a second (a shadow) would be mis-attributed; refusing to guess which one a read means')
    flags.push(node.id.name)
  })
  return flags
}

/** Spans a user can never receive, derived from the dead flags. */
function deadSpans(ast, flags) {
  const spans = []
  const dead = (node) => spans.push({ start: node.start, end: node.end })

  walk(ast.program, (node, parent) => {
    if (node.type === 'LogicalExpression' && node.operator === '&&') {
      const operands = []
      const flatten = (n) => {
        if (n.type === 'LogicalExpression' && n.operator === '&&') { flatten(n.left); flatten(n.right) } else operands.push(n)
      }
      flatten(node)
      // Only the operands BEFORE the last GUARD the last one: a flag in final
      // position (`x && FLAG`) guards nothing, and marking the flag identifier
      // itself dead would move the census for free.
      const guards = operands.slice(0, -1).map((o) => flagGate(o, flags))
      if (guards.includes('is')) dead(operands[operands.length - 1])
    }
    if (node.type === 'ConditionalExpression') {
      const gate = flagGate(node.test, flags)
      if (gate === 'is') dead(node.consequent)
      if (gate === 'isNot') dead(node.alternate)
    }
    if (node.type === 'IfStatement') {
      const gate = flagGate(node.test, flags)
      if (!gate) return
      // The consequent is dead only when the test is FALSE at runtime (`FLAG`);
      // under `!FLAG` it is the arm that always runs.
      if (gate === 'is') dead(node.consequent)
      // An early `return` takes everything after it in the block with it — but
      // only when that return can actually fire. `if (!FLAG) return` always
      // fires (so the tail is dead); `if (FLAG) return` never does (tail LIVE).
      if (gate !== 'isNot' || !parent || parent.type !== 'BlockStatement') return
      const tail = node.consequent.type === 'BlockStatement'
        ? node.consequent.body[node.consequent.body.length - 1]
        : node.consequent
      if (!tail || tail.type !== 'ReturnStatement') return
      const index = parent.body.indexOf(node)
      if (index >= 0 && index + 1 < parent.body.length) {
        spans.push({ start: parent.body[index + 1].start, end: parent.body[parent.body.length - 1].end })
      }
    }
  })
  return spans
}

/**
 * `{ rows, flags, shadowed }` for every constant main.jsx imports from a copy
 * module, in the import's own order. Takes a SOURCE STRING (not the file) so the
 * falsifiability tests can feed a synthetic module through the same path.
 */
function classifyCopyReads(source) {
  const ast = parseModule(source)
  const parents = new Map()
  walk(ast.program, (node, parent) => { if (parent) parents.set(node, parent) })

  const targets = new Set()
  for (const statement of ast.program.body) {
    if (statement.type !== 'ImportDeclaration') continue
    if (!COPY_MODULES.includes(statement.source.value)) continue
    for (const specifier of statement.specifiers) {
      if (specifier.local) targets.add(specifier.local.name)
    }
  }
  const flags = deadFlags(ast)
  const spans = deadSpans(ast, flags)

  const reads = new Map()
  const visit = (node) => {
    if (Array.isArray(node)) { node.forEach(visit); return }
    if (!node || typeof node.type !== 'string') return
    if (node.type === 'Identifier' && targets.has(node.name)) {
      if (!reads.has(node.name)) reads.set(node.name, [])
      reads.get(node.name).push(node.start)
      return
    }
    for (const key of Object.keys(node)) {
      if (key === 'loc' || key.endsWith('Comments')) continue
      // A non-computed `.prop` and an object literal's key are not READS.
      if (node.type === 'MemberExpression' && key === 'property' && !node.computed) continue
      if (node.type === 'ObjectProperty' && key === 'key' && !node.computed) continue
      // An import binding is not a read (the import IS the binding site).
      if (node.type === 'ImportDeclaration') continue
      const value = node[key]
      if (value && typeof value === 'object') visit(value)
    }
  }
  visit(ast.program)

  // A local declarator of an imported name is a SHADOW: this scan would count its
  // reads as reads of the module's constant. Fail LOUD (the same contract as
  // `importsFromMain` in wizardRenderedCopy.js) rather than certify a value the
  // application never renders.
  const shadowed = []
  walk(ast.program, (node) => {
    if (node.type === 'VariableDeclarator' && node.id.type === 'Identifier' && targets.has(node.id.name)) {
      shadowed.push(node.id.name)
    }
  })

  const rows = [...targets].map((name) => {
    const sites = reads.get(name) ?? []
    const archived = sites.filter((at) => spans.some((s) => at >= s.start && at < s.end)).length
    return { name, live: sites.length - archived, archived, shadowed: shadowed.includes(name) }
  })
  return { rows, flags, shadowed }
}

const classified = classifyCopyReads(src)

// Hand-pinned census of `name: [live, archived]`, in import order. `archived`
// reads are behind the LEGACY_WIZARD_ARCHIVED gate; `live` reads are what a user
// (or a prompt card) actually receives. Reference-only today: the HARNESS_*
// install/skill/persist family, whose LIVE carriers are the agent PROMPT
// (`wizardPromptText`: the dashboard never shows `claude mcp add` — it hands the
// agent "Add Tortoise MCP at <url>") and, for the capture step,
// `HARNESS_CAPTURE_INSTALL` in the Memory-sources row.
const COPY_READ_CENSUS = Object.freeze({
  CANONICAL_MCP_URL: [2, 0],
  CAPTURE_OPT_IN_LINE: [4, 0],
  HARNESS_CAPTURE_INSTALL: [1, 0],
  HARNESS_CAPTURE_REASON: [1, 0],
  HARNESS_CAPTURE_REQUIRES_OPT_IN: [1, 0],
  HARNESS_CAPTURE_SUPPORT: [1, 0],
  HARNESS_CONTINUE_LABEL: [1, 1],
  HARNESS_COPY_LABEL: [1, 1],
  HARNESS_FAMILIES: [2, 0],
  HARNESS_INSTALL: [0, 2],
  HARNESS_INTRO: [1, 2],
  HARNESS_NAMES: [2, 1],
  HARNESS_OAUTH: [3, 1],
  HARNESS_ORDER: [1, 1],
  HARNESS_PERSIST: [0, 2],
  HARNESS_SKILLLESS: [0, 2],
  HARNESS_SKILLS: [0, 2],
  HARNESS_SKILLS_IN_PROMPT: [0, 2],
  HARNESS_SKILLS_IN_STEPS: [0, 2],
  HARNESS_STEPS: [0, 2],
  UNIVERSAL_COMMAND: [1, 0],
  WIZARD_CAPTIONS: [7, 0],
  analyticsHarnessOf: [1, 0],
  harnessDisplayName: [1, 0],
  harnessFamilyOf: [1, 0],
  knownHarnessName: [1, 0],
  preferredSurface: [1, 0],
  wizardPromptText: [2, 0],
  wizardWorkflowsText: [1, 0],
})

test('#3802: every copy constant main.jsx imports is classified live/archived, and the census is pinned', () => {
  assert.deepEqual(classified.shadowed, [],
    'a local declarator shadows an imported copy constant — this scan cannot tell its reads from the module\'s, '
    + 'so the census below would certify a value the application never renders')
  const got = {}
  for (const row of classified.rows) {
    assert.ok(!(row.name in got), `duplicate import of ${row.name}`)
    got[row.name] = [row.live, row.archived]
  }
  assert.deepEqual(Object.keys(got).sort(), Object.keys(COPY_READ_CENSUS).sort(),
    'the imported copy-constant surface changed — a new or removed import must be classified, never skipped')
  assert.deepEqual(got, COPY_READ_CENSUS,
    'the render reachability of main.jsx\'s copy constants changed. A constant that lost its last live read site is '
    + 'now reference-only — an edit to it reaches no user, and any test asserting shipped behavior on it needs the '
    + 'linked assertion on the live copy (#3802). A new live read site is a shipped change. Update the pin deliberately.')
})

test('#3802: every imported copy constant is READ somewhere (no constant that only LOOKS wired)', () => {
  // An import with zero reads is the same defect one step earlier than a
  // reference-only constant: the name is in the module graph, so a reader assumes
  // it is wired, while nothing consumes it. `HARNESS_SELF_INSTALL` was exactly
  // that (imported at main.jsx:17, never read) until #3802 dropped the import.
  const unread = classified.rows.filter((r) => r.live + r.archived === 0).map((r) => r.name)
  assert.deepEqual(unread, [], 'imported but never read — drop the import or render it')
})

test('#3802: the capture step a user receives is the RENDERED copy, not HARNESS_INSTALL', () => {
  // The linked assertion the issue's target asks for: the reference-only constant
  // and the live one must not be confused. `HARNESS_CAPTURE_INSTALL` is rendered
  // in the Memory-sources row (#1728) and is therefore the surface a user
  // receives; `HARNESS_INSTALL` renders only inside the archived block, so it is
  // the A0 ROLLBACK copy — kept, and asserted, for the rollback path alone.
  const byName = Object.fromEntries(classified.rows.map((r) => [r.name, r]))
  assert.equal(byName.HARNESS_INSTALL.archived, 2,
    'HARNESS_INSTALL must stay read by the archived block — the A0 rollback path renders it')
  assert.equal(byName.HARNESS_INSTALL.live, 0,
    'HARNESS_INSTALL is reference-only: do not assert shipped behavior on it without an assertion on the live copy')
  assert.ok(byName.HARNESS_CAPTURE_INSTALL.live > 0,
    'HARNESS_CAPTURE_INSTALL is the LIVE capture copy — the Memory-sources row renders it')
})

test('#3802 falsifiability: `FLAG &&` kills its operand and `!FLAG &&` does NOT (the polarity is the point)', () => {
  const synthetic = [
    "import { HARNESS_CAPTURE_INSTALL } from './harnesses.js'",
    'export const Note = () => {',
    '  const LEGACY_WIZARD_ARCHIVED = false',
    '  return (<>',
    '    {LEGACY_WIZARD_ARCHIVED && (<pre className="snippet">{HARNESS_CAPTURE_INSTALL}</pre>)}',
    '    {!LEGACY_WIZARD_ARCHIVED && (<pre className="snippet">{HARNESS_CAPTURE_INSTALL}</pre>)}',
    '    <pre className="snippet">{HARNESS_CAPTURE_INSTALL}</pre>',
    '  </>)',
    '}',
  ].join('\n')
  const { rows, flags } = classifyCopyReads(synthetic)
  assert.deepEqual(flags, ['LEGACY_WIZARD_ARCHIVED'], 'the flag was derived, not listed')
  assert.deepEqual(rows.map((r) => [r.name, r.live, r.archived]), [['HARNESS_CAPTURE_INSTALL', 2, 1]],
    'only the `FLAG &&` read is unreachable — `!FLAG &&` runs (FLAG is false), and treating it as dead '
    + 'would certify a gate that is not one')
})

test('#3802 falsifiability: the early-exit polarity decides whether the tail is dead', () => {
  const cases = [
    // `!FLAG` is TRUE, so this return always fires and the tail never runs.
    [['  const LEGACY_WIZARD_ARCHIVED = false', '  if (!LEGACY_WIZARD_ARCHIVED) return'], 0, 2],
    // `FLAG` is FALSE, so this return never fires and the tail always runs.
    [['  const LEGACY_WIZARD_ARCHIVED = false', '  if (LEGACY_WIZARD_ARCHIVED) return'], 2, 0],
  ]
  for (const [head, live, archived] of cases) {
    const synthetic = [
      "import { HARNESS_SKILLS } from './harnesses.js'",
      'export const go = () => {',
      ...head,
      '  void HARNESS_SKILLS',
      '  void HARNESS_SKILLS',
      '}',
    ].join('\n')
    assert.deepEqual(classifyCopyReads(synthetic).rows.map((r) => [r.name, r.live, r.archived]),
      [['HARNESS_SKILLS', live, archived]],
      `an early return governed by \`${head[1].trim()}\` has the opposite effect from its negation`)
  }
})

test('#3802 falsifiability: a ternary\'s dead arm follows the test polarity', () => {
  const synthetic = (test) => [
    "import { HARNESS_STEPS } from './harnesses.js'",
    'export const pick = () => {',
    '  const LEGACY_WIZARD_ARCHIVED = false',
    `  return ${test} ? \`\${HARNESS_STEPS}\` : \`\${HARNESS_STEPS}\``,
    '}',
  ].join('\n')
  assert.deepEqual(classifyCopyReads(synthetic('LEGACY_WIZARD_ARCHIVED')).rows.map((r) => [r.live, r.archived]),
    [[1, 1]], '`FLAG ? A : B` — the consequent is the dead arm')
  assert.deepEqual(classifyCopyReads(synthetic('!LEGACY_WIZARD_ARCHIVED')).rows.map((r) => [r.live, r.archived]),
    [[1, 1]], '`!FLAG ? A : B` — the ALTERNATE is the dead arm')
})

test('#3802 falsifiability: a second declarator of a flag fails LOUD instead of mis-attributing reads', () => {
  const synthetic = [
    "import { HARNESS_INSTALL } from './harnesses.js'",
    'export const a = () => { const LEGACY_WIZARD_ARCHIVED = false }',
    'export const b = () => { const LEGACY_WIZARD_ARCHIVED = false; return HARNESS_INSTALL }',
  ].join('\n')
  assert.throws(() => classifyCopyReads(synthetic), /declarators/,
    'a shadowed flag must refuse to classify — a scan that guesses certifies the wrong binding')
})
