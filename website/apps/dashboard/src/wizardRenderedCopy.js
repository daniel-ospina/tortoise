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
// The extraction is necessarily textual (main.jsx cannot be imported — it is the
// whole application: browser globals, live session, CSS). Three rules keep it
// honest, mirroring the doctrine `jsxSourceProbe.js` already states:
//   1. the scan runs on a comment- and literal-masked copy of the source
//      (offset-preserving), so an anchor inside a comment or a quoted decoy is
//      not a node;
//   2. every free identifier a node reads is bound from the declared deterministic
//      WORLD plus the module-scope literals extracted verbatim — anything else
//      makes the probe THROW, so an unmodelled node fails LOUD rather than
//      rendering `undefined`;
//   3. the test pins BOTH what each node renders and a census of the enumerated
//      population, so a NEW prose paragraph fails instead of silently dropping out
//      and a data-backed node quietly rewritten as literal prose fails too.
//
// RESIDUAL, stated plainly because the opposite has been claimed in this repo
// before and was false. The masker is not a parser: an anchor inside a REGEX
// LITERAL body is not masked (regex and division are indistinguishable without
// one), and a node opened with a different-but-equivalent spelling (extra
// whitespace between attributes, a spread class name) is not enumerated — though
// the test's non-vacuity floor then fails LOUD rather than passing empty. The
// SEMANTIC guard remains the executed render suite; this module adds observation
// of the surfaces that suite could not reach.
import { evalExpressions, importsFromMain } from './jsxSourceProbe.js'

// The deterministic world every node is rendered in. Each entry maps an
// identifier to the arms of its values, as JS SOURCE TEXT. A node's renders are
// the cross-product of the arms of the identifiers it ACTUALLY reads, so every
// live branch is pinned and a snapshot diff names the branch that moved.
export const WORLD = Object.freeze({
  // main.jsx derives `web` from `wizardHarness === 'claude-web'`; BOTH arms are
  // live, and the OAuth note's `claude.ai` / `Claude Desktop` choice rides it.
  web: ['false', 'true'],
  displayName: ["'Pi'"],
  // `shownOrgName || 'your Organization'` — both arms are live for a new user.
  shownOrgName: ['null', "'Acme'"],
  // `existingKeyNoteFrom(team, keys)` — below-cap is the deterministic arm; the
  // at-cap arm is pinned by keyAllowance.test.js.
  team: ['null'],
  keys: ['[]'],
  // The capture row's loop variable, the wizard's live harness, its key state,
  // and the key the pre-#3501 `firstDataSnippet` concatenation interpolates.
  h: ["'pi'"],
  wizardHarness: ["'pi'"],
  harnessKey: [JSON.stringify('tk_snapshot')],
  welcomeKey: [JSON.stringify('tk_snapshot')],
  snippetKey: [JSON.stringify('tk_snapshot')],
})

// The element families this gate enumerates. `openTag` is the anchor a node must
// carry; `tag` closes it. Adding a family without a pin is a hard failure, so a
// new prose class cannot appear silently.
export const FAMILIES = Object.freeze([
  { id: 'wizard-caption', tag: 'p', openTag: '<p className="wizard-caption">' },
  { id: 'wizard-note', tag: 'p', openTag: '<p className="wizard-note">' },
  { id: 'snippet', tag: 'pre', openTag: '<pre className="snippet"' },
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
// resolve them — but they ARE copy, so they are extracted VERBATIM from the same
// source text the application uses (never re-typed), and a change to the
// declaration therefore moves the rendered pin.
export const LOCAL_LITERAL_NAMES = Object.freeze(['KEY_VISIBILITY_NOTE', 'firstDataSnippet'])

const MASKED = '\u0000'

/**
 * Offset-preserving masker: blanks the CONTENTS of `//` and `/* *​/` comments and
 * of string/template literals, leaving every offset intact so a structural scan
 * runs on the masked copy while the RAW source is sliced by the same indices.
 * Comment-aware and quote-aware in ONE left-to-right pass, so an apostrophe in a
 * comment cannot open a phantom string and JSX prose (full of apostrophes) cannot
 * swallow the rest of the file. Quote rule matches `jsxSourceProbe.js`: `'`/`"`
 * end at the closing quote or at a raw newline; backticks run to their close.
 */
export function maskForScan(src) {
  const out = src.split('')
  let i = 0
  while (i < src.length) {
    const c = src[i]
    const n = src[i + 1]
    if (c === '/' && n === '/' && src[i - 1] !== ':') {
      while (i < src.length && src[i] !== '\n') { out[i] = MASKED; i += 1 }
      continue
    }
    if (c === '/' && n === '*') {
      out[i] = MASKED; out[i + 1] = MASKED; i += 2
      while (i < src.length && !(src[i] === '*' && src[i + 1] === '/')) { out[i] = MASKED; i += 1 }
      if (i < src.length) { out[i] = MASKED; out[i + 1] = MASKED; i += 2 }
      continue
    }
    if (c === "'" || c === '"' || c === '`') {
      const quote = c
      i += 1
      while (i < src.length) {
        if (src[i] === '\\') { out[i] = MASKED; out[i + 1] = MASKED; i += 2; continue }
        if (src[i] === '\n' && quote !== '`') break
        if (src[i] === quote) { i += 1; break }
        out[i] = MASKED
        i += 1
      }
      continue
    }
    i += 1
  }
  return out.join('')
}

/** The index of the `>` closing the open tag it starts inside (bracket-aware). */
function openTagEnd(masked, from) {
  let depth = 0
  for (let i = from; i < masked.length; i += 1) {
    const c = masked[i]
    if (c === '{' || c === '(' || c === '[') depth += 1
    else if (c === '}' || c === ')' || c === ']') depth -= 1
    else if (c === '>' && depth === 0) return i
  }
  return -1
}

// `<p>`/`<pre>` do not nest inside themselves, so the first `</tag>` after the
// open tag closes the node.
function extractFamily(source, masked, family) {
  const hits = []
  let at = 0
  while (true) {
    const index = source.indexOf(family.openTag, at)
    if (index === -1) break
    at = index + family.openTag.length
    // A hit starting inside a masked region is a decoy (a string or a comment).
    if (masked[index] === MASKED) continue
    const tagEnd = openTagEnd(masked, index + family.openTag.length - 1)
    if (tagEnd === -1) throw new Error(`${family.id}: anchor at offset ${index} has no closing '>'`)
    const close = masked.indexOf(`</${family.tag}>`, tagEnd)
    if (close === -1) throw new Error(`${family.id}: node at offset ${index} has no </${family.tag}>`)
    hits.push({
      family: family.id,
      source: source.slice(index, close + `</${family.tag}>`.length),
      children: source.slice(tagEnd + 1, close),
      line: source.slice(0, index).split('\n').length,
    })
  }
  return hits
}

/**
 * Every agent-facing prose node in `source`, in file order, each carrying the
 * exact source span it came from and the raw children between its tags.
 */
export function extractNodes(source) {
  const masked = maskForScan(source)
  const nodes = []
  for (const family of FAMILIES) nodes.push(...extractFamily(source, masked, family))
  return nodes
}

// A node's children take one of four shapes:
//   data        — `{WIZARD_CAPTIONS.connect}` / `{HARNESS_INTRO.codexDesktop}`
//   local-const — `{KEY_VISIBILITY_NOTE}` / `{firstDataSnippet}`
//   expression  — a single JSX expression sourced from a tested module
//   inline      — the rest: hand-written text and/or inline elements, with zero
//                 or more expressions. This is the class #4885 is about — copy
//                 that no data module owns and no earlier guard could see.
export function classifyChildren(children) {
  // Normalize the whitespace just inside the braces, so `{ WIZARD_CAPTIONS.x }`
  // classifies exactly as `{WIZARD_CAPTIONS.x}` — a pure reformat must not
  // change a node's kind (that would red the census for a non-change).
  const t = children.trim().replace(/^\{\s*/, '{').replace(/\s*\}$/, '}')
  if (/^\{WIZARD_CAPTIONS\.[A-Za-z0-9_$]+\}$/.test(t)) return 'data'
  if (/^\{HARNESS_INTRO\.[A-Za-z0-9_$]+\}$/.test(t)) return 'data'
  if (t.length > 2 && t.startsWith('{') && t.endsWith('}')
    && LOCAL_LITERAL_NAMES.includes(t.slice(1, -1))) return 'local-const'
  if (t.length > 2 && t.startsWith('{') && t.endsWith('}')
    && !t.slice(1, -1).includes('{') && !t.slice(1, -1).includes('<')) return 'expression'
  return 'inline'
}

/** Does the (comment/literal-free) `masked` read the identifier `name`? */
const reads = (masked, name) => new RegExp(`\\b${name.replace(/[$]/g, '\\$&')}\\b`).test(masked)

/**
 * The source of every `{…}` JSX expression inside `children`, joined. JSX text is
 * TEXT, not code — so the arm detection must only see the expressions, or the
 * prose word "keys" (in "the keys your organization already has") would be read
 * as the `keys` binding. Brace-aware, and quote/comment-blind via `maskForScan`.
 */
export function expressionSpans(children) {
  const masked = maskForScan(children)
  const spans = []
  let i = 0
  while (i < masked.length) {
    if (masked[i] !== '{') { i += 1; continue }
    let depth = 0
    let end = -1
    for (let j = i; j < masked.length; j += 1) {
      if (masked[j] === '{') depth += 1
      else if (masked[j] === '}') { depth -= 1; if (depth === 0) { end = j; break } }
    }
    if (end === -1) throw new Error(`unbalanced {…} in a prose node: ${children.slice(i, i + 60)}`)
    spans.push(children.slice(i + 1, end))
    i = end + 1
  }
  return spans.join('\n')
}

/**
 * The arms a node is rendered in: the cross-product of the WORLD entries for the
 * identifiers its JSX EXPRESSIONS read — plus the identifiers read by any
 * module-scope literal the node interpolates (`firstDataSnippet` interpolates
 * `snippetKey`). A node that reads none of the world renders once.
 */
export function armsFor(code) {
  const dims = Object.entries(WORLD).filter(([name]) => reads(code, name))
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

/**
 * The source of a module-scope literal main.jsx declares (`const NAME = …`),
 * verbatim. Only single literals and multi-line string concatenations are
 * supported; any other shape throws rather than binding the wrong source.
 */
export function localLiteralSource(source, name) {
  const masked = maskForScan(source)
  const anchor = `const ${name} = `
  const hits = []
  let at = 0
  while (true) {
    const index = source.indexOf(anchor, at)
    if (index === -1) break
    at = index + anchor.length
    if (masked[index] !== MASKED) hits.push(index)
  }
  if (hits.length !== 1) {
    throw new Error(`main.jsx must declare ${name} exactly once at module scope (found ${hits.length})`)
  }
  const from = hits[0] + anchor.length
  let depth = 0
  for (let i = from; i < masked.length; i += 1) {
    const c = masked[i]
    if (c === '(' || c === '[' || c === '{') depth += 1
    else if (c === ')' || c === ']' || c === '}') depth -= 1
    else if (c === '\n' && depth === 0) {
      const before = masked.slice(from, i).replace(/\s+$/, '')
      // A statement continues while it trails a binary/continuation operator…
      if (/[+\-*/%&|?=,]$/.test(before)) continue
      // …or when the NEXT line OPENS with one (a multi-line string
      // concatenation leads with `+`).
      const after = masked.slice(i + 1).match(/^[^\S\n]*(.)/)
      if (after && /[+\-*/%&|?:.,]/.test(after[1])) continue
      return source.slice(from, i).trim()
    }
  }
  throw new Error(`could not slice the ${name} declaration`)
}

// `renderToStaticMarkup` escapes text; decode what the probe's renderer emits.
export function htmlToText(html) {
  return html
    .replace(/<[^>]*>/g, '')
    .replace(/&#x27;/g, "'").replace(/&#39;/g, "'")
    .replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&')
}

/**
 * The comparable projection of a rendered node — what the committed pin holds.
 *
 * It is deliberately the RENDERED value only. The source `children` and the line
 * number are NOT pinned: a cosmetic reformat or an edit above a node must not red
 * the gate, and a source change that MATTERS shows up in `renders` (the copy
 * moved) or in `kind` (a data-backed node became hand-written, or vice versa).
 */
export function pinnable(rendered) {
  return {
    family: rendered.family,
    kind: rendered.kind,
    renders: rendered.renders,
  }
}

/**
 * Render every node. Imports come from main.jsx's OWN import map (so a shadowing
 * local fails instead of being certified); module-scope literals are extracted
 * verbatim; each node × arm is compiled and RUN in ONE probe module, with its
 * bindings declared inside its own IIFE so no two arms share state.
 *
 * Returns `[{ family, line, kind, children, renders: [{ label, text }] }]`.
 */
export async function renderNodes(source, nodes = extractNodes(source), { probe } = {}) {
  const run = probe ?? evalExpressions
  const imports = importsFromMain(source, IMPORTED_NAMES)
  const localSources = {}
  for (const name of LOCAL_LITERAL_NAMES) localSources[name] = localLiteralSource(source, name)

  const expressions = []
  const plan = []
  for (const node of nodes) {
    const code = expressionSpans(node.children)
    const localNames = LOCAL_LITERAL_NAMES.filter((name) => reads(code, name))
    // A local literal's own dependencies (e.g. `snippetKey` inside
    // `firstDataSnippet`) are part of what the node renders, so they select arms
    // too.
    const allCode = code + '\n' + localNames.map((name) => localSources[name]).join('\n')
    const arms = armsFor(allCode)
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
    kind: classifyChildren(node.children),
    children: node.children.replace(/\s+/g, ' ').trim(),
    renders: arms.map((arm, k) => ({ label: arm.label, text: htmlToText(probes[start + k].html) })),
  }))
}
