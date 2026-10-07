// wizardRenderedCopy.js — observe the wizard copy that is WRITTEN IN main.jsx.
//
// Not a test file: `node --test src/*.test.js` does not run it. It is imported by
// `wizardRenderedCopy.test.js` (the gate) and by
// `scripts/gen-wizard-rendered-copy-snapshot.mjs` (the pin regenerator).
//
// WHY THIS EXISTS (#4885). #4880 moved the wizard's DATA-DRIVEN copy out of
// main.jsx into `wizardPrompts.js` / `harnesses.js`, where the rendered-value
// snapshot pins it. One class was left behind: the prose HAND-WRITTEN IN JSX. An
// independent reviewer proved the gap by appending a sentence inside the live
// OAuth connector note (`<p className="wizard-note">`, main.jsx) — both suites
// stayed green (560 JS, 15 Python).
//
// Extracting those fragments into `WIZARD_CAPTIONS` would NOT close it: the
// COMPOSITION — fragment order, the inline <code>/<strong>/<em> elements, and the
// `{web ? 'claude.ai' : 'Claude Desktop'}` ternaries — would still be hand-written
// JSX and still unobserved, and that ternary is exactly what the note must get
// right. So this module observes the class where it lives: it enumerates every
// agent-facing prose element in main.jsx and RENDERS each through the repo's JSX
// probe (`jsxSourceProbe.js`: esbuild + react-dom/server), so the assertion is on
// the string a user sees, not on the source that produced it.
//
// THE ENUMERATION IS AN AST, NOT A TEXT SCAN. main.jsx is parsed with Babel
// (`@babel/parser`, the parser `@vitejs/plugin-react` already runs on this file)
// and the elements are selected by TAG + STATIC `className`. A previous revision
// searched for the literal string `<p className="wizard-note">`, which a
// semantically identical spelling (`<p className="wizard-note" >`, a newline
// between attributes, an added attribute, `className={'wizard-note'}`) evaded
// entirely — a new paragraph would have shipped with both tests green. The AST is
// insensitive to all of that, and it also gives exact source spans, so no
// hand-rolled lexer is needed: comments and string literals cannot confuse it,
// and a JSX apostrophe cannot swallow a line.
//
// Three further rules keep the observation honest:
//   * every free identifier a node reads is bound from a declared deterministic
//     WORLD plus module-scope literals extracted VERBATIM from their declarator —
//     anything else makes the probe THROW, so an unmodelled node fails LOUD
//     rather than rendering `undefined`;
//   * a module-scope literal must have EXACTLY ONE declarator, so a `let`/`var`
//     shadow inside the component cannot make the probe certify the module value
//     while the application renders the shadow (#4637's failure);
//   * the test pins both what each node renders and a census of the population.
//
// RESIDUAL, stated plainly because the opposite has been claimed in this repo
// before and was false. A `<p>`/`<pre>` whose class name is not STATICALLY
// present on the element (a spread, a computed template, a `cn()` helper) is not
// enumerated — the element is then not an anchored prose node, and the census
// fails LOUD if a previously-enumerated one changes shape rather than silently
// dropping it. A same-named binding introduced by a construct that is not a
// variable declarator (`function KEY_VISIBILITY_NOTE(){}`, a parameter) is not
// detected as a shadow. The SEMANTIC guard remains the executed render suite;
// this module adds observation of the surfaces that suite could not reach.
import { createRequire } from 'node:module'
import { evalExpressions, importsFromMain } from './jsxSourceProbe.js'
import { HARNESS_ORDER } from './harnesses.js'

const require = createRequire(import.meta.url)
// Babel's parser. `@vitejs/plugin-react` already parses main.jsx with it, and it
// is declared in the dashboard's devDependencies (and vendored under
// node_modules/, like esbuild), so it is present wherever the dashboard is.
const { parse, parseExpression } = require('@babel/parser')

const jsSource = (v) => JSON.stringify(v)

// The deterministic world every node is rendered in. Each entry maps an
// identifier to the arms of its values, as JS SOURCE TEXT. A node's renders are
// the cross-product of the arms of the identifiers it ACTUALLY reads, so both
// live branches of a ternary are pinned and a snapshot diff names the branch.
export const WORLD = Object.freeze({
  // main.jsx derives `web` from `wizardHarness === 'claude-web'`; BOTH arms are
  // live, and the OAuth note's `claude.ai` / `Claude Desktop` choice rides it.
  web: ['false', 'true'],
  displayName: ["'Pi'"],
  // `shownOrgName || 'your Organization'` — both arms are live for a new user.
  shownOrgName: ['null', "'Acme'"],
  // `existingKeyNoteFrom(team, keys)` — below-cap AND at-cap. At-cap needs a
  // finite limit with every slot used (`{ max_api_keys: 1 }` + one live row).
  team: ['null', '{ max_api_keys: 1 }'],
  keys: ['[]', "[{ id: 'a' }]"],
  // The capture row's loop variable and the wizard's live harness selection.
  // Every harness the app offers is an arm: the copy is harness-specific, so a
  // single harness would leave the others unobserved (the wizard's default is
  // 'claude', not 'pi').
  h: HARNESS_ORDER.map(jsSource),
  wizardHarness: HARNESS_ORDER.map(jsSource),
  harnessKey: [jsSource('tk_snapshot')],
  welcomeKey: [jsSource('tk_snapshot')],
  snippetKey: [jsSource('tk_snapshot')],
})

// The element families this gate enumerates, selected by tag + static class. A
// `<p>`/`<pre>` whose class list contains the class is a node of that family.
export const FAMILIES = Object.freeze([
  { id: 'wizard-caption', tag: 'p', className: 'wizard-caption' },
  { id: 'wizard-note', tag: 'p', className: 'wizard-note' },
  { id: 'snippet', tag: 'pre', className: 'snippet' },
])

// Names main.jsx imports. Resolved through `importsFromMain` (which fails closed
// on a local shadow), so a probe can only bind the module the app renders.
export const IMPORTED_NAMES = Object.freeze([
  'WIZARD_CAPTIONS',
  'HARNESS_INTRO',
  'existingKeyNoteFrom',
  'HARNESS_INSTALL',
  'HARNESS_SKILLS',
  'HARNESS_SKILLLESS',
  'HARNESS_SKILLS_IN_PROMPT',
  'HARNESS_SKILLS_IN_STEPS',
  'HARNESS_PERSIST',
  'HARNESS_CAPTURE_INSTALL',
])

// Module-scope literals main.jsx declares. Not imported, so the probe cannot
// resolve them — but they ARE copy, so they are bound from the DECLARATOR's own
// initializer source (never re-typed), and a change to it moves the pin.
export const LOCAL_LITERAL_NAMES = Object.freeze(['KEY_VISIBILITY_NOTE', 'firstDataSnippet'])

/** Parse main.jsx. Exported so callers can parse once and share the AST. */
export function parseModule(source) {
  // `attachComment: false` — this gate reads ELEMENTS, never comments, and
  // attaching them costs ~3× the parse on a file main.jsx's size.
  return parse(source, { sourceType: 'module', plugins: ['jsx'], attachComment: false })
}

// Babel AST nodes carry `type`; `loc`/`start`/`end` do not.
const isNode = (v) => v && typeof v === 'object' && typeof v.type === 'string'

/** Visit every AST node reachable from `root`. */
function walk(root, visit) {
  const stack = [root]
  while (stack.length) {
    const node = stack.pop()
    if (Array.isArray(node)) { stack.push(...node); continue }
    if (!isNode(node)) continue
    visit(node)
    for (const key of Object.keys(node)) {
      if (key === 'loc' || key === 'leadingComments' || key === 'trailingComments' || key === 'innerComments') continue
      const value = node[key]
      if (value && typeof value === 'object') stack.push(value)
    }
  }
}

/**
 * The identifiers an expression READS, as a set. A non-computed member property
 * and an object key are not reads, so `a.keys` does not select the `keys` arm.
 */
export function readIdentifiers(node) {
  const names = new Set()
  const visit = (current) => {
    if (Array.isArray(current)) { for (const c of current) visit(c); return }
    if (!isNode(current)) return
    if (current.type === 'Identifier') names.add(current.name)
    for (const key of Object.keys(current)) {
      if (key === 'loc' || key === 'leadingComments' || key === 'trailingComments' || key === 'innerComments') continue
      if (current.type === 'MemberExpression' && key === 'property' && !current.computed) continue
      if (current.type === 'ObjectProperty' && key === 'key' && !current.computed) continue
      const value = current[key]
      if (value && typeof value === 'object') visit(value)
    }
  }
  visit(node)
  return names
}

// The static class list of a JSX opening element, or null. Accepts a string
// attribute or a string inside an expression container — the two spellings that
// are the same JSX. Anything non-static is not an anchored prose node.
export function staticClassName(openingElement) {
  for (const attr of openingElement.attributes) {
    if (attr.type !== 'JSXAttribute') continue
    if (!attr.name || attr.name.name !== 'className') continue
    const value = attr.value
    if (!value) return null
    if (value.type === 'StringLiteral') return value.value
    if (value.type === 'JSXExpressionContainer' && value.expression.type === 'StringLiteral') {
      return value.expression.value
    }
    return null
  }
  return null
}

/**
 * Every agent-facing prose node in `source`, in FILE ORDER, each carrying the
 * exact source span it came from and its parsed element.
 */
export function extractNodes(source, ast = parseModule(source)) {
  const nodes = []
  walk(ast, (node) => {
    if (node.type !== 'JSXElement') return
    const opening = node.openingElement
    const tag = opening.name.type === 'JSXIdentifier' ? opening.name.name : null
    if (!tag) return
    const classes = (staticClassName(opening) || '').split(/\s+/).filter(Boolean)
    for (const family of FAMILIES) {
      if (family.tag !== tag || !classes.includes(family.className)) continue
      nodes.push({
        family: family.id,
        tag,
        source: source.slice(node.start, node.end),
        children: source.slice(opening.end, node.closingElement.start),
        line: node.loc.start.line,
        node,
      })
    }
  })
  nodes.sort((a, b) => a.node.start - b.node.start)
  return nodes
}

// A node's children take one of four shapes:
//   data        — one `{WIZARD_CAPTIONS.x}` / `{HARNESS_INTRO.x}`, however spelled
//   local-const — one `{KEY_VISIBILITY_NOTE}` / `{firstDataSnippet}`
//   expression  — one other JSX expression, sourced from a tested module
//   inline      — the rest: hand-written text and/or inline elements, with zero
//                 or more expressions. This is the class #4885 is about — copy
//                 that no data module owns and no earlier guard could see.
export function classifyChildren(children) {
  const meaningful = children.filter((c) => !(c.type === 'JSXText' && c.value.trim() === ''))
  if (meaningful.length !== 1 || meaningful[0].type !== 'JSXExpressionContainer') return 'inline'
  const expr = meaningful[0].expression
  if (expr.type === 'MemberExpression' && expr.object.type === 'Identifier'
    && ['WIZARD_CAPTIONS', 'HARNESS_INTRO'].includes(expr.object.name)) return 'data'
  if (expr.type === 'Identifier' && LOCAL_LITERAL_NAMES.includes(expr.name)) return 'local-const'
  return 'expression'
}

/**
 * The enumerated population WITHOUT rendering — `{family, line, kind, children}`
 * per node, in file order. The census is a property of the SOURCE (which nodes
 * exist and what shape they are), so it needs no probe run; only the VALUE pin
 * does. Split out so a mutation test can assert the population cheaply.
 */
export function describeNodes(source, ast = parseModule(source)) {
  return extractNodes(source, ast).map((node) => ({
    family: node.family,
    line: node.line,
    kind: classifyChildren(node.node.children),
    children: node.children.replace(/\s+/g, ' ').trim(),
  }))
}

/**
 * The arms a node is rendered in: the cross-product of the WORLD entries for the
 * identifiers its JSX EXPRESSIONS read — plus the identifiers read by any
 * module-scope literal the node interpolates (`firstDataSnippet` interpolates
 * `snippetKey`). A node that reads none of the world renders once.
 */
export function armsFor(read) {
  const dims = Object.entries(WORLD).filter(([name]) => read.has(name))
  let arms = [{ label: '', bindings: {} }]
  for (const [name, values] of dims) {
    const next = []
    for (const arm of arms) {
      for (const value of values) {
        next.push({
          label: arm.label ? `${arm.label},${name}=${value}` : `${name}=${value}`,
          bindings: { ...arm.bindings, [name]: value },
        })
      }
    }
    arms = next
  }
  return arms
}

// Kept private: the string form is only used by the exported convenience wrapper
// above, which callers pass an AST-derived identifier set instead.

/**
 * The initializer source of a module-scope `const NAME = …`, verbatim. Throws
 * unless there is EXACTLY ONE declarator of that name anywhere in the module —
 * so a `let`/`var` shadow inside a component (which the application would render
 * while the probe certified the module value) fails LOUD, exactly as
 * `importsFromMain` already does for an imported name.
 */
export function localLiteralSource(source, name, ast = parseModule(source)) {
  const declarators = []
  walk(ast, (node) => {
    if (node.type === 'VariableDeclarator' && node.id.type === 'Identifier' && node.id.name === name) {
      declarators.push(node)
    }
  })
  if (declarators.length !== 1) {
    throw new Error(`${name} must have exactly ONE declarator in main.jsx (found ${declarators.length}) — `
      + 'a second binding would shadow the one a probe certifies')
  }
  const init = declarators[0].init
  if (!init) throw new Error(`${name} must be initialized to a literal`)
  return source.slice(init.start, init.end)
}

// `renderToStaticMarkup` escapes text; decode what the probe's renderer emits.
export function htmlToText(html) {
  return html
    .replace(/<[^>]*>/g, '')
    .replace(/&#x27;/g, "'").replace(/&#39;/g, "'")
    .replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&')
}

// The ordered names of the elements INSIDE the root element, so a change to the
// inline structure (<code> → <strong>, a dropped <em>) is observed even when the
// text is identical. `htmlToText` deliberately deletes tags, so this is the half
// that pins them.
export function inlineTags(html, tag) {
  const inner = html.replace(new RegExp(`^<${tag}\\b[^>]*>`), '').replace(new RegExp(`</${tag}>$`), '')
  return [...inner.matchAll(/<([a-zA-Z][a-zA-Z0-9-]*)\b/g)].map((m) => m[1])
}

/**
 * The comparable projection of a rendered node — what the committed pin holds.
 * It is deliberately the RENDERED value only: NO line number (an edit above a
 * node must not red the gate) and no source text (a cosmetic reformat must not
 * either). A source change that MATTERS shows up in `renders` (the copy moved)
 * or in `kind` (a data-backed node became hand-written, or vice versa).
 */
export function pinnable(rendered) {
  return { family: rendered.family, kind: rendered.kind, renders: rendered.renders }
}

/**
 * Render every node. Imports come from main.jsx's OWN import map (so a shadowing
 * local fails instead of being certified); module-scope literals are extracted
 * from their declarator; each node × arm is compiled and RUN in ONE probe module,
 * with its bindings declared inside its own IIFE so no two arms share state.
 */
export async function renderNodes(source, nodes, { probe, ast = parseModule(source) } = {}) {
  const run = probe ?? evalExpressions
  const list = nodes ?? extractNodes(source, ast)
  const imports = importsFromMain(source, IMPORTED_NAMES)
  const localSources = {}
  for (const name of LOCAL_LITERAL_NAMES) localSources[name] = localLiteralSource(source, name, ast)

  const expressions = []
  const plan = []
  for (const node of list) {
    const codes = node.node.children
      .filter((c) => c.type === 'JSXExpressionContainer')
      .map((c) => c.expression)
    const read = new Set()
    for (const code of codes) for (const name of readIdentifiers(code)) read.add(name)
    const localNames = LOCAL_LITERAL_NAMES.filter((name) => read.has(name))
    // A local literal's own dependencies (`snippetKey` inside `firstDataSnippet`)
    // are part of what the node renders, so they select arms too.
    for (const name of localNames) {
      for (const dep of readIdentifiers(parseExpression(localSources[name]))) read.add(dep)
    }
    const arms = armsFor(read)
    const locals = localNames.map((name) => `const ${name} = ${localSources[name]};`)
    const start = expressions.length
    for (const arm of arms) {
      // Arm bindings first: a module-scope literal may READ one of them
      // (`firstDataSnippet` interpolates `snippetKey`), and a local may not be
      // read before it is declared.
      const decls = Object.entries(arm.bindings).map(([n, v]) => `const ${n} = ${v};`).concat(locals)
      expressions.push(`(() => { ${decls.join(' ')} return (${node.source}) })()`)
    }
    plan.push({ node, start, arms })
  }
  const probes = await run(expressions, { imports })
  return plan.map(({ node, start, arms }) => ({
    family: node.family,
    line: node.line,
    kind: classifyChildren(node.node.children),
    children: node.children.replace(/\s+/g, ' ').trim(),
    renders: arms.map((arm, k) => ({
      label: arm.label,
      text: htmlToText(probes[start + k].html),
      tags: inlineTags(probes[start + k].html, node.tag),
    })),
  }))
}
