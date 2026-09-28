// jsxSourceProbe.js — evaluate the call sites that live in main.jsx.
//
// Not a test file: `node --test src/*.test.js` does not run it. It is imported
// by the wiring guards in the note/action tests.
//
// WHY THIS EXISTS (#4637): main.jsx cannot be imported by a node test — it is
// the whole application (browser globals, live session, CSS). Five review cycles
// pinned its wiring with source-text regexes and five times the guard passed
// while the RENDERED card said something else:
//
//   <Tag buildFork={x} {...{ buildFork: false }} />   ← contains the pinned text,
//                                                      renders `false`
//   const keyIsLive = a\s+\n  || b                    ← not the single line a
//                                                      `(.+)$` match reads
//   {(!snippetKey || snippetKey) && (<button …>…</button>)}  ← the flipped
//     condition is satisfied by a quoted decoy of the pinned text elsewhere
//
// Text is not semantics. These probes COMPILE the extracted call site with the
// app's own JSX transform (esbuild — a direct vite dependency, so it is present
// wherever the dashboard is installed) and read the value React produces: the
// EFFECTIVE props after spreads and aliases, or the evaluated expression. A
// probe can only pass by producing the right value.
//
// The extraction itself is necessarily textual (main.jsx is text). Three rules
// keep it honest:
//   1. probes run on the SHARED quote-aware comment-stripped source
//      (`testSupport.js`), so a commented-out site cannot satisfy one;
//   2. `maskLiterals` blanks the CONTENTS of string and template literals (same
//      quote rule as the shared stripper: `'`/`"` cannot span a raw newline,
//      backticks can), and every extractor DROPS a match that starts inside one.
//      Without this, `const s = '<Tag buildFork={false} />'` is extracted as a
//      call site — and being first in file order, a guard reading the first
//      match would read fabricated props (found by an independent verifier on
//      the previous revision, which had claimed the opposite in a docstring);
//   3. `extractOne` requires the anchor to occur EXACTLY ONCE, so a second site
//      (or a code-position decoy) fails the guard rather than giving the real
//      site a second chance to match.
//
// RESIDUAL, stated plainly because the opposite has been claimed before and was
// false: a source-based wiring guard cannot model every construct an author
// could write. An anchor planted inside a REGEX LITERAL body is not masked (regex
// and division are indistinguishable without a parser), and an author who
// replaces an anchor with hand-written logic of a different shape can still
// satisfy a text pin. The guards here therefore catch every ordinary mutation (a
// spread, an alias, an inverted or narrowed expression, a wrapped statement, a
// re-typed literal, a quoted decoy of any ordinary quoting form) and the
// SEMANTIC guard for the facts themselves is the executed module test suite (the
// note/action render tests, which render the real components across the whole
// fork x gate cross-product). No comment or claim in this repo asserts more.
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { pathToFileURL, fileURLToPath } from 'node:url'
import { transformSync } from 'esbuild'
import { stripComments } from './testSupport.js'

const require = createRequire(import.meta.url)
// Absolute specifiers, because the probe module is written to a temp directory:
// a bare `react` would be resolved by looking for node_modules BESIDE THE FILE
// (i.e. in the temp dir), which would fail.
const REACT = JSON.stringify(require.resolve('react'))
const REACT_DOM_SERVER = JSON.stringify(require.resolve('react-dom/server'))
// A RELATIVE specifier ('./onboardingEmptyStateKeyNote.js') is resolved from
// THIS file's directory — the dashboard's src/ — because the probe module itself
// is written to a temp dir, where a relative import would point at nothing.
const HERE = dirname(fileURLToPath(import.meta.url))
const resolveSpec = (spec) => (spec.startsWith('.')
  ? pathToFileURL(join(HERE, spec)).href
  : spec)

const MASKED = '\u0000'

/**
 * Replace the CONTENTS of string and template literals with a sentinel,
 * preserving every offset. Quote rule matches the shared comment stripper:
 * `'` and `"` end at the closing quote or at a raw newline (JSX prose is full of
 * apostrophes), backticks run to the closing backtick. A template's `${…}`
 * interpolation bodies are masked WITH the surrounding text BY DESIGN: a call
 * site written inside an interpolation is invisible to these probes (a guard
 * that fails closed, and one more shape in the declared residual — not a claim
 * that such a site cannot occur).
 */
export function maskLiterals(src) {
  const out = src.split('')
  const blank = (from, to) => { for (let j = from; j < to; j += 1) out[j] = MASKED }
  let i = 0
  while (i < src.length) {
    const quote = src[i]
    if (quote !== "'" && quote !== '"' && quote !== '`') { i += 1; continue }
    let j = i + 1
    while (j < src.length) {
      if (src[j] === '\\') { j += 2; continue }
      if (src[j] === quote) break
      if (quote !== '`' && src[j] === '\n') break
      j += 1
    }
    if (j < src.length && src[j] === quote) {
      blank(i + 1, j)
      i = j + 1
    } else {
      i = j + 1
    }
  }
  return out.join('')
}

/** Is `index` inside a string/template literal body? */
const insideLiteral = (masked, index) => masked[index] === MASKED

/** The one and only match of `pattern` in `source` (or throw with the count). */
export function extractOne(source, pattern, label) {
  const hits = extractAll(source, pattern, label)
  if (hits.length !== 1) {
    throw new Error(`${label}: expected exactly one match of ${pattern} — found ${hits.length}`)
  }
  return hits[0]
}

/**
 * All matches of `pattern`, or throw when there are none. A match that STARTS
 * inside a string/template literal body is not a code site and is dropped: a
 * quoted decoy must never satisfy — or inflate the count of — an anchor.
 */
export function extractAll(source, pattern, label) {
  const flags = pattern.flags.includes('g') ? pattern.flags : `${pattern.flags}g`
  const masked = maskLiterals(source)
  const hits = []
  for (const m of source.matchAll(new RegExp(pattern.source, flags))) {
    if (!insideLiteral(masked, m.index)) hits.push(m[0])
  }
  if (hits.length === 0) throw new Error(`${label}: no match of ${pattern}`)
  return hits
}

/**
 * How `name` is declared as a LOCAL binding in `source`, or null. `importsFromMain`
 * refuses a probed name that is locally bound anywhere: a local
 * `const ownerCardProps = …` inside App() SHADOWS the import, so the application
 * renders the shadowing helper while a probe bound to the module would certify
 * the imported one (a reviewer's green-with-the-defect mutation).
 *
 * The patterns below cover, and are each pinned by a case in
 * `onboardingEmptyStateKeyNote.test.js`: a declaration keyword followed by the
 * name (`const`/`let`/`var`/`function`/`async function`/`function*`/`class`),
 * including one that follows another statement on the same line; a later
 * declarator of a multi-declarator statement; object and array destructuring
 * (where the name is the bound one, not a property key); a parameter list; and a
 * bare arrow parameter. NOT covered (declared residual, fail-closed direction
 * unknown — an exotic spelling not listed here would not be detected by this
 * helper; the semantic guard remains the executed render/probe suite): bindings
 * introduced by `for`/`catch` heads of unusual shape, `eval`, or a `with` scope.
 */
export function localBindingHits(source, name) {
  const src = stripComments(source)
  const n = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const patterns = [
    // a declaration keyword then the name: `const NAME`, `let NAME`, `var NAME`,
    // `function NAME`, `async function NAME`, `function* NAME`, `class NAME`
    new RegExp(`(?:^|[;{}])\\s*(?:async\\s+)?(?:const|let|var|function|class)\\s*\\*?\\s*${n}\\b`, 'm'),
    // a LATER declarator of a const/let/var statement: `const a = 1, NAME = …`
    new RegExp(`(?:^|[;{}])\\s*(?:const|let|var)\\s+[^;\\n]*?\\b${n}\\b\\s*(?==)`, 'm'),
    // ARRAY destructuring: `const [NAME] = […]`
    new RegExp(`(?:^|[;{}]|\\()\\s*(?:const|let|var)\\s*\\[[^\\]]*\\b${n}\\b`, 'm'),
    // OBJECT destructuring, only where the name is BOUND — `const { NAME } = …` or
    // `const { a: NAME } = …`. A property key (`{ NAME: alias }`) binds `alias`,
    // not `NAME`, and must not be reported.
    new RegExp(`(?:^|[;{}]|\\()\\s*(?:const|let|var)\\s*\\{[^}]*\\b${n}\\b\\s*(?!:)`, 'm'),
    // a PARAMETER: `(… NAME …) =>` / `function f(… NAME …) {`
    new RegExp(`\\([^()]*\\b${n}\\b[^()]*\\)\\s*(?:=>|\\{)`, 'm'),
    // a bare arrow parameter: `NAME => …`
    new RegExp(`(?:^|[;(,{])\\s*${n}\\s*=>`, 'm'),
  ]
  for (const re of patterns) {
    const hit = src.match(re)
    if (hit) return hit[0].trim()
  }
  return null
}

/**
 * The import map main.jsx ITSELF declares, for the names a probe needs.
 *
 * A probe must bind the module the application actually uses. Injecting the
 * module a TEST chooses leaves a hole: a local `const ownerCardProps = () => ({…})
 * inside App() shadows the import, the app renders the shadowing helper, and the
 * probe happily asserts the props of the imported one — the #4637 defect with
 * every guard green (found by an independent reviewer). So `probeTags`'s
 * `imports` should come from here, and the tests additionally pin that main.jsx
 * declares no local binding of these names.
 *
 * Throws when a name is not imported at all (a locally-declared helper then
 * cannot be probed — the caller must fail rather than assert against a module the
 * app does not use).
 */
export function importsFromMain(source, names) {
  const stripped = stripComments(source)
  const masked = maskLiterals(stripped)
  const importRe = /import\s*\{([^}]*)\}\s*from\s*(['"])([^'"]+)\2/g
  const found = new Map()
  for (const m of stripped.matchAll(importRe)) {
    // A match that STARTS inside a string/template literal is a quoted decoy, not
    // an import statement — without this, a later decoy re-binds the name (a
    // reviewer's green-with-the-defect mutation: the real import points at a
    // lying module and a string supplies the real-looking one).
    if (masked[m.index] === MASKED) continue
    for (const raw of m[1].split(',')) {
      const parts = raw.trim().split(/\s+as\s+/)
      const name = parts.pop().trim()
      if (!name) continue
      const spec = m[3]
      if (found.has(name) && found.get(name) !== spec) {
        throw new Error(`main.jsx imports ${name} from both ${found.get(name)} and ${spec}`)
      }
      found.set(name, spec)
    }
  }
  const out = {}
  for (const name of names) {
    if (!found.has(name)) {
      throw new Error(`main.jsx does not import ${name} — a probe may not bind a module the application does not use`)
    }
    // Fail closed on a local binding of the same name: it shadows the import, so
    // the app renders the shadow while the probe asserts the import.
    const shadow = localBindingHits(stripped, name)
    if (shadow) {
      throw new Error(`main.jsx declares ${name} locally (${shadow}) — it would shadow the import a probe binds`)
    }
    out[name] = found.get(name)
  }
  return out
}

async function compileAndRun({ imports, bindings, expressions }) {
  const importLines = Object.entries(imports)
    .map(([name, spec]) => `import { ${name} } from ${JSON.stringify(resolveSpec(spec))};`).join('\n')
  // Bindings are JS SOURCE TEXT, not values: a test can hand the probe a real
  // expression extracted from main.jsx (e.g. a derivation) and have it EXECUTED,
  // which is the difference between asserting a statement's text and asserting
  // what it computes.
  const declarations = Object.entries(bindings)
    .map(([name, src]) => `const ${name} = ${src};`).join('\n')
  const probes = expressions
    .map((expr) => `(() => { const v = (${expr}); return { value: v, html: isElement(v) ? renderToStaticMarkup(v) : null } })()`)
    .join(',\n  ')
  const moduleSource = `
import React from ${REACT};
import { renderToStaticMarkup } from ${REACT_DOM_SERVER};
${importLines}
${declarations}
const isElement = (v) => v !== null && typeof v === 'object' && 'type' in v && 'props' in v;
export const probes = [
  ${probes}
];
`
  const dir = mkdtempSync(join(tmpdir(), 'pi-jsx-probe-'))
  const file = join(dir, `probe-${Date.now()}.mjs`)
  try {
    const { code } = transformSync(moduleSource, {
      loader: 'jsx',
      format: 'esm',
      jsx: 'transform',
      jsxFactory: 'React.createElement',
      jsxFragment: 'React.Fragment',
    })
    writeFileSync(file, code)
    const mod = await import(`${pathToFileURL(file).href}?t=${Date.now()}`)
    return mod.probes
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
}

/**
 * Every `<Tag … />` call site in `source`, evaluated. Returns one
 * `{ source, props, html }` per occurrence, in file order — the props are the
 * EFFECTIVE props (a JSX spread after a pinned attribute wins, exactly as
 * React resolves it), and `html` is the static render of that call site with the
 * probe's bindings. A tag planted inside a string or template literal is NOT a
 * call site and is excluded (`maskLiterals`), so it can neither satisfy a guard
 * nor be mistaken for the first arm.
 */
export async function probeTags(source, { tag, bindings = {}, imports = {} }) {
  const tags = extractAll(stripComments(source), new RegExp(`<${tag}(?=[\\s/>])[\\s\\S]*?/>`), `probeTags(${tag})`)
  const probes = await compileAndRun({ imports, bindings, expressions: tags })
  return probes.map((p, i) => {
    if (p.value === null || typeof p.value !== 'object' || !('props' in p.value)) {
      throw new Error(`probeTags(${tag}) [#${i}]: the extracted site is not a React element: ${tags[i]}`)
    }
    return { source: tags[i], props: p.value.props, html: p.html }
  })
}

/**
 * Evaluate JS expressions against the probe's bindings. `expressions` are
 * source text (usually extracted from main.jsx by `extractOne`). Returns the
 * values (and a static render for element values).
 */
export async function evalExpressions(expressions, { bindings = {}, imports = {} } = {}) {
  const list = Array.isArray(expressions) ? expressions : [expressions]
  return compileAndRun({ imports, bindings, expressions: list })
}
