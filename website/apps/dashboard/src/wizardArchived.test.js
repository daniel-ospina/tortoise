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
// #3726 cycle 5: main.jsx (12k lines) is parsed ONCE for every AST pin below, and
// the live welcome card's window is derived once and shared. A pin that re-parsed
// per assertion also re-DERIVED its window per assertion, which is how two pins end
// up disagreeing about which region they watch.
/** The ready copy's own text — what identifies the element that CARRIES it. */
const READY_COPY = "You're set up in"
const SRC_AST = parseModule(src)
const SRC_JSX = jsxElements(SRC_AST)
/** `jsxElements` over a SOURCE string — the entry point for the synthetics below. */
const jsxOf = (source) => jsxElements(parseModule(source))

/** className of a JSX opening element in ANY spelling (`"x"`, `'x'`, `{"x"}`, `` {`x`} ``). */
function classNameOf(opening) {
  for (const attr of opening.attributes) {
    if (attr.type !== 'JSXAttribute' || attr.name?.name !== 'className') continue
    const v = attr.value
    if (!v) return null
    if (v.type === 'StringLiteral') return v.value
    if (v.type === 'JSXExpressionContainer') {
      const e = v.expression
      if (e.type === 'StringLiteral') return e.value
      if (e.type === 'TemplateLiteral' && e.expressions.length === 0) return e.quasis[0].value.cooked
    }
    return null
  }
  return null
}

/** Does the static className list contain `name` as one of its classes? */
function hasClass(cls, name) {
  return cls.split(/\s+/).includes(name)
}

/**
 * Every JSX ELEMENT whose `className` resolves STATICALLY, with its source span,
 * in file order.
 *
 * #3726 cycle 4: an element is SELECTED by tag + static class from the AST rather
 * than LOCATED by the text of its tag. That is what makes these pins insensitive
 * to attribute order, an added attribute, whitespace before `>` and every
 * `className` spelling at once — and it removes the `indexOf` sentinel, which is
 * the mechanism that turns a moved anchor into an empty window (`slice(-1, p)` is
 * `''`) or into the rest of the file.
 *
 * RESIDUAL, stated rather than hidden: a `className` that is NOT statically
 * resolvable (a template with EXPRESSIONS, a spread, a `cn()` helper) is not
 * enumerated, so a label whose class is dynamic AND whose TEXT is a static string —
 * `<p className={`wizard-title ${wizardStep}`}>Memory sources</p>` — evades BOTH
 * arms of the negative pin (measured green). The `{wizardSteps[…]}` spelling of the
 * same label IS caught, by the read-anchored arm rather than by this one.
 *
 * #3726 cycle 4 (P2): the previous wording claimed the mitigation lived "there", in
 * `wizardRenderedCopy.js::staticClassName`. It does not — that census enumerates
 * only the `PROSE` families (`p.wizard-caption`, `p.wizard-note`, `pre.snippet`);
 * `wizard-title` is in no family, so the RENDERED case is pinned NOWHERE. Tracked
 * as a follow-up (#8917): the fix is a render-level assertion on the live welcome
 * card, not another source scan.
 */
function jsxElements(ast) {
  const out = []
  walkNodes(ast, (node) => {
    if (node.type !== 'JSXElement') return
    const cls = classNameOf(node.openingElement)
    if (cls === null) return
    const name = node.openingElement.name
    out.push({ tag: name?.type === 'JSXIdentifier' ? name.name : null, cls, start: node.start, end: node.end, node })
  })
  return out.sort((a, b) => a.start - b.start)
}

/**
 * The legacy #1643 `wizardSteps` array read from the AST: `{ declarators, labels }`,
 * `labels` being the element strings (any quoting) or `null`.
 *
 * #3726 cycle 4 (P1): the label pin used to be a SUBSTRING match over the whole
 * file, which is wrong in BOTH directions:
 *
 *  - FALSE RED (the direction main actually had): the needle was the ESCAPED
 *    spelling (`You\\'re set`), so a pure re-quote of `wizardSteps` redded a pin
 *    about copy that had not changed — measured in the cycle-5 falsifiability
 *    test below, against the real string shapes.
 *  - FALSE GREEN (the direction an obvious "fix" introduces): normalising the
 *    escape before matching (`src.replace(/\\'/g, "'")`) makes the needle match
 *    the LIVE ready copy (`You're set up in <strong>…</strong>`) and a comment, so
 *    DELETING the label from the legacy array — precisely the archived-not-deleted
 *    break this test exists for — leaves the suite green.
 *
 * The array's own elements are quote-agnostic AND cannot be satisfied by copy
 * rendered somewhere else; the pin's `declarators` count makes a shadow loud.
 */
function legacyWizardLabels(ast) {
  let declarators = 0
  let labels = null
  walkNodes(ast, (node) => {
    if (node.type !== 'VariableDeclarator' || node.id.type !== 'Identifier' || node.id.name !== 'wizardSteps') return
    declarators += 1
    if (!node.init || node.init.type !== 'ArrayExpression') return
    const values = node.init.elements.map(literalString)
    if (values.every((v) => v !== null)) labels = values
  })
  return { declarators, labels }
}

/**
 * The `wizardStageLabel(...)` call inside a JSX element, read from the AST:
 * `{ step, overrides }` (the first argument's identifier, and the override
 * object's key → value identifier), or `null` when the element does not call it.
 */
function stageLabelCall(element) {
  let found = null
  walkNodes(element, (node) => {
    if (found || node.type !== 'CallExpression') return
    if (node.callee?.type !== 'Identifier' || node.callee.name !== 'wizardStageLabel') return
    const [step, overrides] = node.arguments
    const entries = overrides?.type === 'ObjectExpression' ? overrides.properties.map((p) => [keyName(p.key), p.value?.name ?? null]) : []
    found = { step: step?.type === 'Identifier' ? step.name : null, overrides: Object.fromEntries(entries) }
  })
  return found
}

/** Does this element's subtree READ `wizardSteps[...]` (the legacy label lookup)? */
function hasWizardStepsRead(element) {
  let found = false
  walkNodes(element, (node) => {
    if (found || !isWizardStepsRead(node)) return
    found = true
  })
  return found
}

/**
 * An ELEMENT read of the legacy array: `wizardSteps[i]`, `wizardSteps?.[i]`,
 * `wizardSteps.at(i)`, `wizardSteps?.at(i)` — the shape that produces a stage label.
 *
 * #3726 cycle 5 (P2): the predicate used to test `node.type === 'MemberExpression'`
 * only. Optional chaining is a DIFFERENT node type (`OptionalMemberExpression`), so
 * one keystroke moved the read out of both pins in both directions (measured): an
 * in-card `{wizardSteps?.[wizardStep]}` rendered the duplicate stage label with the
 * negative pin GREEN, and re-spelling the ARCHIVED title as `wizardSteps?.[…]` — a
 * no-op — redded the positive pin.
 *
 * #3726 cycle 6 (P3): it then matched ANY member access, so `{wizardSteps.length}` or
 * `wizardSteps.map(…)` in the live card redded the negative pin with a message naming
 * a stage label that was not there (measured) — a false red with a wrong diagnosis.
 * Only element access counts; a non-element member read is not a label.
 */
function isWizardStepsRead(node) {
  if (!(node.type === 'MemberExpression' || node.type === 'OptionalMemberExpression')) return false
  if (!(node.object?.type === 'Identifier' && node.object.name === 'wizardSteps')) return false
  if (node.computed) return true
  return node.property?.type === 'Identifier' && node.property.name === 'at'
}

// #3726 cycle 2: the two #3913 pins below were still written as source regexes,
// so each covered two of the three JS string delimiters (`"`, `'` — not `` ` ``)
// and the negative one silenty false-GREENed on a template literal or a quoted
// key. Spelling every combination is an arms race; the AST settles it. Both
// helpers read the one parse config the module already imports.
function walkNodes(root, visit) {
  const stack = [root]
  while (stack.length) {
    const node = stack.pop()
    if (Array.isArray(node)) { stack.push(...node); continue }
    if (!node || typeof node !== 'object' || typeof node.type !== 'string') continue
    visit(node)
    for (const key of Object.keys(node)) {
      if (key === 'loc' || key === 'leadingComments' || key === 'trailingComments' || key === 'innerComments') continue
      const value = node[key]
      if (value && typeof value === 'object') stack.push(value)
    }
  }
}

/** The static string value of a literal node in ANY spelling, else null. */
function literalString(node) {
  if (!node) return null
  if (node.type === 'StringLiteral') return node.value
  if (node.type === 'TemplateLiteral' && node.expressions.length === 0) return node.quasis[0].value.cooked
  return null
}

/** A property KEY's name: `step`, `'step'`, `"step"` and `` `step` `` all read `step`. */
function keyName(node) {
  if (!node) return null
  if (node.type === 'Identifier') return node.name
  return literalString(node)
}

/** Every `step: <literal>` write in the source, whatever the key/value quoting. */
function stepWrites(ast) {
  const out = []
  walkNodes(ast, (node) => {
    if (node.type === 'ObjectProperty' && keyName(node.key) === 'step') {
      const value = literalString(node.value)
      if (value !== null) out.push(value)
    }
    if (node.type === 'AssignmentExpression' && node.left.type === 'MemberExpression') {
      if (keyName(node.left.property) === 'step') {
        const value = literalString(node.right)
        if (value !== null) out.push(value)
      }
    }
  })
  return out
}

/** Every `forkId !== <literal>` comparison, whatever the literal's quoting. */
function forkIdComparisons(ast) {
  const out = []
  walkNodes(ast, (node) => {
    if (node.type !== 'BinaryExpression') return
    if (!['!==', '!='].includes(node.operator)) return
    if (node.left.type !== 'Identifier' || node.left.name !== 'forkId') return
    const value = literalString(node.right)
    if (value !== null) out.push(value)
  })
  return out
}

/**
 * Every `x || '<static>'` fallback in `ast`, with its span and its LEFT OPERAND,
 * in file order. `left` is the left side's identifier name when it is a bare
 * identifier, else `null` — which is what lets the ready-copy pin require the
 * FALLBACK VARIABLE and not merely the fallback string.
 */
function orFallbacks(ast) {
  const out = []
  walkNodes(ast, (node) => {
    if (node.type !== 'LogicalExpression' || node.operator !== '||') return
    const value = literalString(node.right)
    if (value === null) return
    out.push({ value, start: node.start, left: node.left.type === 'Identifier' ? node.left.name : null, node })
  })
  return out.sort((a, b) => a.start - b.start)
}

/**
 * The live welcome card's span, read from the AST: from the `div.welcome-card`
 * element to the archived gate that follows its head block. BOTH bounds are asserted
 * here, once — an unresolved END is what turns a window into the rest of the file, an
 * unresolved START what turns it into an empty slice, and an END inside the card what
 * turns a negative pin into an inspection of the wrong region; either way every pin
 * that uses the window certifies a region it does not name.
 *
 * #3726 cycle 6: existence is not CONTAINMENT, and the END used to be a TEXT
 * sentinel searched on the RAW source — comments included. `main.jsx` already
 * carries a note INSIDE the card that names the flag (L9262, "NEVER rendered while
 * LEGACY_WIZARD_ARCHIVED is false"), so widening that note by two characters
 * (` && …`) would resolve the END EARLY, shrink the window past the copy it
 * watches, and leave `deepEqual(inCardTitles, [])` passing over the shrunken
 * region — the same false-green shape as the `indexOf`-returns-`-1` defect this PR
 * closes. The boundary is therefore read from the AST as the archived GATE
 * (`archivedGates`), its UNIQUENESS is asserted, and its ORDER relative to the card
 * is asserted.
 */
function welcomeCardWindow(ast) {
  // #3726 cycle 4 (P1): the window's START is the CARD, not the head. Anchoring it
  // to `div.welcome-head` (8370) left the card's own first 33 lines (8337-8369)
  // outside every consumer, so a `wizard-title` placed there — above the
  // provisioning/error fork, and therefore rendered in ALL THREE card states — redded
  // nothing (measured). The head is now required to sit INSIDE the card it belongs to.
  const card = welcomeCard(ast)
  const heads = jsxElements(ast).filter((e) => e.tag === 'div' && hasClass(e.cls, 'welcome-head'))
  assert.equal(heads.length, 1,
    'the welcome-head block must resolve exactly once (a pin over an empty or unbounded window is a false green)')
  assert.ok(heads[0].start > card.start && heads[0].end < card.end,
    'the welcome-head block must sit INSIDE the welcome card — otherwise the window starts wherever a class happens to be')
  const start = card.start
  const end = archivedGateStart(ast)
  assert.notEqual(end, -1,
    'the archived-block boundary must resolve (an unresolved end makes the window the rest of the file)')
  assert.ok(end > heads[0].end,
    'the archived block must come AFTER the live card\'s `div.welcome-head` content — a gate that resolves inside it ends the window inside the region it certifies, and a label after it is invisible')
  return { start, end }
}

/** The single `div.welcome-card` — the region every in-card pin certifies. */
function welcomeCard(ast) {
  const cards = jsxElements(ast).filter((e) => e.tag === 'div' && hasClass(e.cls, 'welcome-card'))
  assert.equal(cards.length, 1,
    'the welcome CARD (div.welcome-card) must resolve exactly once — it is the region every in-card pin certifies')
  return cards[0]
}

/**
 * The JSX subtree the archived gate RENDERS — `{start, end}` of the `&&` expression's
 * right side — or `null` when the gate is not the `&& (<jsx>)` form. The live card
 * legitimately contains this block (it is kept for the A0 rollback), so the
 * content-anchored in-card pin has to exclude exactly this span and nothing else.
 */
function archivedSubtree(ast) {
  const gate = archivedGates(ast)[0]
  if (!gate) return null
  const jsxish = (n) => n && (n.type === 'JSXElement' || n.type === 'JSXFragment')
  let span = null
  walk(ast.program, (node, parent) => {
    if (span || node !== gate || !parent) return
    if (parent.type === 'LogicalExpression' && parent.operator === '&&'
      && parent.left === gate && jsxish(parent.right)) {
      span = { start: parent.right.start, end: parent.right.end }
    }
    // #3726 cycle 6 (P3): `gateRendersArchivedBlock` documents the ternary spelling as
    // a supported shape, so the EXCLUSION has to recognise it too — otherwise the
    // behavior-preserving `{(A && B) ? (<jsx/>) : null}` rewrite made the archived
    // block's own label look like an in-card one (measured, with a wrong message).
    if (parent.type === 'ConditionalExpression' && parent.test === gate && jsxish(parent.consequent)) {
      span = { start: parent.consequent.start, end: parent.consequent.end }
    }
  })
  return span
}

/**
 * Every `wizardSteps[…]` READ in `ast` whose offset is not inside `span` (the
 * archived subtree), as offsets in file order. A read inside the LIVE card is the
 * #2912 duplicate stage label: the live heading must name the stage through
 * `wizardStageLabel`, not by indexing the legacy array.
 */
function wizardStepsReadsOutside(ast, span) {
  const out = []
  walkNodes(ast, (node) => {
    if (!isWizardStepsRead(node)) return
    if (span && node.start >= span.start && node.end <= span.end) return
    out.push(node.start)
  })
  return out.sort((a, b) => a - b)
}

/**
 * `name -> [init, …]` for every `VariableDeclarator` with a bare identifier id.
 *
 * #3726 cycle 5 (P2): both copy pins anchored on WHERE an expression is WRITTEN, so
 * both were one binding hop from the truth — a hoist
 * (`const legacyStageLabel = wizardSteps[wizardStep]` then `<p>{legacyStageLabel}</p>`)
 * rendered the duplicate stage label with the pin green, and a hoisted
 * `const orgLabel = shownOrgName || '…'` made a pure refactor of the ready copy a
 * FALSE RED (both measured). A resolved VALUE is not a position; this map is the one
 * hop both pins take.
 */
function bindingsByName(ast) {
  const map = new Map()
  walkNodes(ast, (node) => {
    if (node.type !== 'VariableDeclarator' || node.id.type !== 'Identifier') return
    const list = map.get(node.id.name) ?? []
    list.push(node.init)
    map.set(node.id.name, list)
  })
  return map
}

/**
 * The init of `name`'s SINGLE declarator, or `null` — not a local binding (a prop, a
 * parameter, a hook result) or declared more than once, in which case the reader
 * refuses to guess which one a render means.
 */
function uniqueInit(bindings, name) {
  const inits = bindings.get(name)
  return inits && inits.length === 1 ? inits[0] : null
}

/**
 * Every stage label READ that reaches the live card: direct `wizardSteps[…]` reads in
 * the card outside the archived subtree, plus a rendered identifier whose single
 * declarator IS such a read (the hoist one hop away).
 */
function inCardStageReads(ast, card, archivedSpan) {
  const inArchived = (n) => archivedSpan && n.start >= archivedSpan.start && n.end <= archivedSpan.end
  const direct = wizardStepsReadsOutside(ast, archivedSpan)
    .filter((offset) => offset > card.start && offset < card.end)
  const bindings = bindingsByName(ast)
  const hoisted = []
  walkNodes(card.node, (node) => {
    if (node.type !== 'JSXExpressionContainer' || node.expression.type !== 'Identifier') return
    if (inArchived(node)) return
    const init = uniqueInit(bindings, node.expression.name)
    if (init && hasWizardStepsRead(init)) hoisted.push(node.start)
  })
  return [...direct, ...hoisted].sort((a, b) => a - b)
}

/**
 * Every A0 archived GATE in `ast`, in file order: a `&&` LogicalExpression whose
 * LEFT is `LEGACY_WIZARD_ARCHIVED` and whose RIGHT is `welcomeOriented`.
 *
 * #3726 cycle 6: the boundary must be anchored to the gate's SHAPE, not to the
 * flag's NAME. It was `source.indexOf('LEGACY_WIZARD_ARCHIVED &&', start)` —
 * prose-forgeable, because `main.jsx`'s own card carries a note that names the flag
 * (L9262). Reading "the first identifier named LEGACY_WIZARD_ARCHIVED" traded that
 * for a CODE-forgeable sentinel, which is WORSE: a
 * `{LEGACY_WIZARD_ARCHIVED ? <p className="wizard-title">…</p> : null}` inside the
 * card — the most plausible way the forbidden in-card label comes back, guarded by
 * the flag itself — resolved the boundary early and left the in-card pin GREEN with
 * the label present (measured).
 *
 * Shape alone is not enough either (cycle 2 measured it): the forbidden label is
 * most plausibly reintroduced by COPYING the archived block's own gated line into
 * the card, which reproduces the gate's shape exactly — a SECOND candidate, first in
 * the file, resolving the boundary 750 lines early while both window consumers
 * stayed green. `archivedGateStart` therefore requires EXACTLY ONE gate: a second
 * one fails LOUD instead of silently deciding which region the pins watch. This is
 * also why the reader returns the whole file's gates rather than the first after
 * the card — the count is the invariant, and a gate that moves ABOVE the card then
 * reddens on the ordering check instead of quietly becoming the window's start.
 */
function archivedGates(ast) {
  const gates = []
  walkNodes(ast, (node) => {
    if (node.type !== 'LogicalExpression' || node.operator !== '&&') return
    if (node.left.type !== 'Identifier' || node.left.name !== 'LEGACY_WIZARD_ARCHIVED') return
    if (node.right.type !== 'Identifier' || node.right.name !== 'welcomeOriented') return
    gates.push(node)
  })
  return gates.sort((a, b) => a.start - b.start)
}

/** Offset of the ONE archived gate; `-1` when there is none. A second gate throws. */
function archivedGateStart(ast) {
  const gates = archivedGates(ast)
  assert.ok(gates.length <= 1,
    `exactly one archived gate may exist — found ${gates.length} \`LEGACY_WIZARD_ARCHIVED && welcomeOriented\` expressions, ` +
    'so the card-window boundary would resolve at whichever comes FIRST and certify a region it does not name')
  return gates.length ? gates[0].start : -1
}

/**
 * Does `gate` RENDER the archived block — is it the condition that guards a JSX
 * subtree (`GATE && (<jsx/>)`, or `GATE ? (<jsx/>) : …`)?
 *
 * #3726 cycle 4 (P3): this used to be a text slice over the gate's own span
 * (`/^[\s)]*&&\s*\(/`), so a COMMENT inside the expression
 * (`{A && B /* A0 rollback gate *\/ && (`) redded the pin — prose affecting a pin,
 * which is the defect class this PR removes. Read from the AST, comments and
 * parentheses cannot matter.
 */
function gateRendersArchivedBlock(ast, gate) {
  const jsxish = (n) => !!n && (n.type === 'JSXElement' || n.type === 'JSXFragment')
  let renders = false
  walk(ast.program, (node, parent) => {
    if (renders || node !== gate || !parent) return
    if (parent.type === 'LogicalExpression' && parent.operator === '&&'
      && parent.left === gate && jsxish(parent.right)) renders = true
    if (parent.type === 'ConditionalExpression' && parent.test === gate
      && jsxish(parent.consequent)) renders = true
  })
  return renders
}

/**
 * Every JSX element in `ast` with its source span, INCLUDING elements that carry no
 * `className` — the copy-carrying element is found by its TEXT, not by a class.
 */
function jsxAll(ast) {
  const out = []
  walkNodes(ast, (node) => {
    if (node.type !== 'JSXElement') return
    const name = node.openingElement.name
    out.push({ tag: name?.type === 'JSXIdentifier' ? name.name : null, start: node.start, end: node.end, node })
  })
  return out.sort((a, b) => a.start - b.start)
}

/**
 * The static text held by a JSX subtree — element text, string literals and template
 * quasis (no expressions). Used to find the element that CARRIES a piece of copy.
 */
function subtreeText(node) {
  const parts = []
  walk(node, (n) => {
    if (n.type === 'JSXText') parts.push(n.value)
    if (n.type === 'StringLiteral') parts.push(n.value)
    if (n.type === 'TemplateElement') parts.push(n.value.cooked ?? n.value.raw)
  })
  // #3726 cycle 4 (P2): collapsing whitespace is what the RENDERER does, so a reflow
  // of the copy across source lines — byte-identical HTML, verified with the repo's
  // own render probe — is a no-op here instead of a false red on a pin about a
  // missing fallback.
  return parts.join('').replace(/\s+/g, ' ')
}

/**
 * The fallback values of the ready-copy `shownOrgName || '<fallback>'` sites that
 * sit inside the element CARRYING the ready copy, in file order.
 *
 * #3726 cycle 5 (P1): the pin used to collect every `x || <string>` in the 12k-line
 * module and assert that ONE of them was `'your organization'`. main.jsx has three
 * such sites — the welcome ready copy this test exists for, plus two
 * "Continue setting up" headings in a DIFFERENT card — so deleting the ready-copy
 * fallback stayed GREEN while the assertion message still claimed the ready copy
 * was pinned.
 *
 * #3726 cycle 4 (P2): anchoring on the card WINDOW was still an existential — any
 * in-card `shownOrgName || '<static>'` satisfied it, rendered or not, so deleting
 * the real fallback and planting `{false && <span>{shownOrgName || 'your
 * organization'}</span>}` inside the card stayed green (measured; the live ready
 * copy then rendered `You're set up in . Next, choose…`). The pin is now anchored to
 * the copy itself: the `||` must sit inside the element whose own subtree holds the
 * `You're set up in` text, which a decoy in the same card cannot reach.
 */
function readyCopyOrgFallbacks(ast) {
  const { start, end } = welcomeCardWindow(ast)
  const carriers = jsxAll(ast).filter((e) => e.start > start && e.start < end
    && subtreeText(e.node).includes(READY_COPY))
  // The MINIMAL carriers: an ancestor of the copy's element also holds the text, and
  // accepting ancestors would let a decoy anywhere in the card through.
  const minimal = carriers.filter((e) => !carriers.some((o) => o !== e && o.start > e.start && o.end < e.end))
  // #3726 cycle 4 (P3): carrying the text is not RENDERING the fallback. A decoy in
  // the same paragraph (`You're set up in <strong>{shownOrgName}</strong>. {false &&
  // <span>{shownOrgName || 'your organization'}</span>}`) satisfied the pin while the
  // live copy rendered `You're set up in .` (measured). The `||` must therefore be
  // render-REACHABLE from its carrier: no short-circuit or ternary on the way up.
  const parents = new Map()
  walk(ast.program, (node, parent) => { if (parent) parents.set(node, parent) })
  // Render-REACHABLE and in TEXT position: `{false && …}` never renders, and a
  // `title={shownOrgName || '…'}` is a tooltip, not the copy the pin names (measured:
  // the attribute form satisfied the pin while the visible copy rendered
  // "You're set up in .").
  const reachableFrom = (node, carrier) => {
    let n = parents.get(node)
    while (n && n !== carrier.node) {
      if (n.type === 'LogicalExpression' || n.type === 'ConditionalExpression') return false
      if (n.type === 'JSXAttribute') return false
      n = parents.get(n)
    }
    return n === carrier.node
  }
  const inline = orFallbacks(ast)
    .filter((f) => f.left === 'shownOrgName'
      && minimal.some((e) => f.start > e.start && f.start < e.end && reachableFrom(f.node, e)))
    .map((f) => f.value)
  // #3726 cycle 5 (P2): a HOIST is a value, not a position — extracting the fallback
  // to `const orgLabel = shownOrgName || '…'` and rendering `{orgLabel}` changes no
  // rendered character, so requiring the inline shape made that refactor a FALSE RED
  // (measured). One hop is resolved, the same way `inCardStageReads` resolves the
  // legacy-array read.
  //
  // #3726 cycle 6 (P2): the hop resolves the VALUE, so the render site it resolved
  // through has to pass the SAME reachability test as the inline form — otherwise
  // `{false && <span>{orgLabel}</span>}` or `<span title={orgLabel} />` satisfied the
  // pin while the visible copy rendered "You're set up in ." (measured).
  const bindings = bindingsByName(ast)
  const hoisted = []
  for (const e of minimal) {
    walkNodes(e.node, (node) => {
      if (node.type !== 'JSXExpressionContainer' || node.expression.type !== 'Identifier') return
      if (!reachableFrom(node, e)) return
      const init = uniqueInit(bindings, node.expression.name)
      if (!init || init.type !== 'LogicalExpression' || init.operator !== '||') return
      if (init.left.type !== 'Identifier' || init.left.name !== 'shownOrgName') return
      const value = literalString(init.right)
      if (value !== null) hoisted.push(value)
    })
  }
  return [...inline, ...hoisted]
}

/**
 * Every write that sets `onboarding_complete` to **true** — object properties and
 * member assignments, whatever the key's quoting.
 *
 * #3726 cycle 5 (P2): the reader pushed the literal's value REGARDLESS of polarity,
 * so a legitimate reset (`onboarding_complete: false`) landed as `[false]` and
 * failed a `deepEqual(…, [])` with a message naming a write that is not the defect.
 * Only `true` is the forbidden opt-in (the sibling `stepWrites` pin has the same
 * polarity discipline, and a positive control proving the reader can see the
 * write at all).
 */
function onboardingCompleteWrites(ast) {
  const out = []
  walkNodes(ast, (node) => {
    const isProp = node.type === 'ObjectProperty' && keyName(node.key) === 'onboarding_complete'
    const isAssign = node.type === 'AssignmentExpression' && node.left.type === 'MemberExpression'
      && keyName(node.left.property) === 'onboarding_complete'
    if (!isProp && !isAssign) return
    const v = isProp ? node.value : node.right
    if (v.type === 'BooleanLiteral' && v.value === true) out.push(true)
  })
  return out
}

test('legacy #1643 wizard render is gated behind the ARCHIVED flag (never rendered)', () => {
  // The legacy wizard's gate must be the archived flag, not the live path. Read from
  // the AST (#3726 cycle 6): this used to be a raw-source regex
  // (`/LEGACY_WIZARD_ARCHIVED\s*&&\s*welcomeOriented\s*&&/`), which is a SECOND,
  // disagreeing derivation of the same fact — it false-redded on a pure reformat
  // (`{(LEGACY_WIZARD_ARCHIVED) && (welcomeOriented) && (`) while staying green when
  // a third conjunct was pushed in front. One derivation, one answer.
  const gates = archivedGates(SRC_AST)
  assert.equal(gates.length, 1,
    'the legacy wizard is gated by exactly ONE `LEGACY_WIZARD_ARCHIVED && welcomeOriented` expression (a second makes the card-window boundary ambiguous)')
  assert.ok(gateRendersArchivedBlock(SRC_AST, gates[0]),
    'that gate RENDERS the archived block — it is not a bare comparison, and a comment inside it cannot affect this')
  assert.ok(/const LEGACY_WIZARD_ARCHIVED\s*=\s*false/.test(src),
    'LEGACY_WIZARD_ARCHIVED is false (never rendered)')
})

test('archived marker comment present (rollback drill reference)', () => {
  assert.ok(src.includes('⛔ ARCHIVED — #1997 (W1)'), 'ARCHIVED marker header')
})

test('legacy wizard labels still exist in source (archived-not-deleted)', () => {
  // #3726 cycle 4 (P1): read the ARRAY, not the file text. main's needle was the
  // ESCAPED spelling (`You\'re set`), so it pinned the array correctly but
  // FALSE-REDDED on a pure re-quote of it; the obvious fix — normalise the escape
  // and match the file — makes `You're set` unfalsifiable instead, because the live
  // ready copy and a comment both contain it. Both directions are measured in the
  // cycle-5 falsifiability test below. The array's own elements are quote-agnostic
  // AND cannot be satisfied by copy that renders somewhere else.
  const { declarators, labels } = legacyWizardLabels(SRC_AST)
  assert.equal(declarators, 1, 'exactly one `wizardSteps` binding — a second (a shadow) would make this pin ambiguous')
  assert.deepEqual(labels, ["Connect your tool", "Memory sources",
                            "Your agent's toolkit", "Seed your graph", "You're set"],
    'the legacy #1643 wizardSteps labels are the archived-not-deleted contract (A0 rollback copy)')
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
  // #3726 cycle 4: the element is SELECTED (one `h1` whose static className list
  // contains `welcome-title`), so no `indexOf` sentinel is left to misfire. The
  // pre-fix form located it by the text `<h1 className="welcome-title">` and
  // sliced to `indexOf('</h1>', open)`: with the anchor unresolved that `indexOf`
  // restarts at 0 and finds the FIRST `</h1>` in the file, which is EARLIER than
  // the window, so `slice(-1, p)` was `''` and `includes(...)` was false — a LOUD
  // but MISLEADING red ("the <h1> does not name the stage") on a pure re-quote. On
  // the negative pin below the same empty window PASSED, which is the false GREEN
  // this branch exists to close. Selecting the element removes the window entirely.
  const titles = SRC_JSX.filter((e) => e.tag === 'h1' && hasClass(e.cls, 'welcome-title'))
  assert.equal(titles.length, 1, 'the live wizard <h1> (.welcome-title) must resolve exactly once')
  const stage = stageLabelCall(titles[0].node)
  assert.ok(stage, 'the live wizard <h1> names the stage through wizardStageLabel (a source grep cannot prove the override ORDER, which wizardFlow.test.js pins)')
  assert.equal(stage.step, 'wizardStep', 'the stage label is computed from the current step')
  // The overrides are the #2912/#3428/cycle-4 contract: read as key -> identifier
  // from the AST, so a reformat (spacing, key quoting, line breaks) is a no-op and
  // a REMOVED override reddens with the key named.
  assert.deepEqual(stage.overrides, {
    hasOrg: 'welcomeHasOrg',
    paused: 'effectivelyPaused',
    connected: 'serverHarnessConnected',
    buildFork: 'isBuildFork',
  }, 'the org-holding, paused, #3428 not-connected and cycle-4 build-fork overrides are all passed to wizardStageLabel')
  // The NEGATIVE pin's region: the live welcome card, from the card element to the
  // archived block that legitimately keeps the in-card label. The bounds are derived
  // ONCE, by a helper that asserts both ends, and shared with the ready-copy pin
  // below: an unresolved END makes a negative pin watch the rest of the file, an
  // unresolved START makes it empty, and `!includes(…)` passes over both.
  const { start: cardStart, end: gateStart } = welcomeCardWindow(SRC_AST)
  // #3726 cycle 3: the assertion itself was still a two-delimiter regex, so the
  // third delimiter — and the JSX container form — kept the pin GREEN while the
  // label was genuinely back in the card. Class names from the AST instead: one
  // shape covers every spelling (`"wizard-title"`, `{'wizard-title'}`, `` {`wizard-title`} ``).
  const inCardTitles = SRC_JSX.filter((e) => e.start > cardStart && e.start < gateStart && hasClass(e.cls, 'wizard-title'))
  assert.deepEqual(inCardTitles, [],
    'no in-card stage label may return in the LIVE wizard (the archived rollback block keeps its own)')
  // #3726 cycle 4 (P2): the pin above is anchored to the class NAME, so four
  // spellings of the same defect rendered with it GREEN (measured): a classless
  // `<p>{wizardSteps[wizardStep]}</p>` inside the head, the `class=` alias (not
  // `className`), the same inside the ready-copy paragraph, and any copy placed BELOW
  // the archived block — the window ends at the gate, so nothing under the rollback
  // block was watched at all. Anchor the negative pin to the DEFECT instead: the
  // stage label must not INDEX THE LEGACY ARRAY anywhere in the live card outside the
  // archived subtree.
  const card = welcomeCard(SRC_AST)
  const archivedSpan = archivedSubtree(SRC_AST)
  const inCardStepReads = inCardStageReads(SRC_AST, card, archivedSpan)
  assert.deepEqual(inCardStepReads, [],
    'no in-card stage label may return — anchored to an ELEMENT READ of the legacy `wizardSteps` array, so a classless copy, the `class=` alias, optional chaining or a read below the archived block cannot evade it')
  // The archived block's title still READS wizardSteps (kept for rollback): the
  // element's own subtree must contain the `wizardSteps[...]` member read, which a
  // nearby comment or an unrelated 400-character slice cannot satisfy.
  const archivedTitles = SRC_JSX.filter((e) => hasClass(e.cls, 'wizard-title'))
  assert.ok(archivedTitles.some((e) => hasWizardStepsRead(e.node)),
    'archived legacy title retained (it still renders wizardSteps[wizardStep])')
})

// ── #3726 falsifiability: each pin above must be able to go RED ──────────────

test('#3726 falsifiability: the legacy-label pin reads the ARRAY, not copy that happens to contain the label', () => {
  // ── the direction main actually had: a FALSE RED on a pure re-quote ────────
  // main's needle was the ESCAPED spelling, and in main.jsx that sequence occurs
  // only inside the legacy array — so the pre-fix pin was NOT vacuous for
  // `You're set`. What it could not survive is the change this issue is about:
  // re-quoting the array without touching one character of the copy.
  // #3726 cycle 6 (P1): the demonstration below is confined to a SYNTHETIC source.
  // Asserting the escaped sequence against the LIVE `src` was itself the defect
  // this issue exists to remove: `You\'re set` occurs only inside the legacy array
  // (main.jsx:3666), so a PURE RE-QUOTE of that array — dropping the escape and
  // switching the delimiters, not one character of the copy — redded that
  // assertion with a message about a PRE-FIX needle while the real pin above
  // stayed green. The false red simply moved out of the pin and into its own
  // falsifiability test. Every other falsifiability test in this file feeds
  // synthetics only, for the same reason.
  const mainNeedle = "You\\'re set"
  const asShipped = [
    'const wizardSteps = [',
    "  'Connect your tool',",
    "  'Memory sources',",
    "  'Your agent\\'s toolkit',",
    "  'Seed your graph',",
    "  'You\\'re set',",
    ']',
  ].join('\n')
  assert.ok(asShipped.includes(mainNeedle),
    'a source written in the pre-fix spelling contains the pre-fix needle')
  assert.deepEqual(legacyWizardLabels(parseModule(asShipped)), {
    declarators: 1,
    labels: ["Connect your tool", "Memory sources", "Your agent's toolkit", "Seed your graph", "You're set"],
  }, 'the synthetic parses as one array of the five labels')
  // Tie the synthetic to the REAL input BY VALUE: the same five labels the live
  // `wizardSteps` holds. Spelling cannot redden this (a re-quote reads the same
  // labels), so the demo cannot drift into asserting a fiction the repository no
  // longer contains.
  assert.deepEqual(legacyWizardLabels(parseModule(asShipped)).labels,
    legacyWizardLabels(SRC_AST).labels,
    'the synthetic reproduces the LIVE wizardSteps labels — the pre-fix spelling below is a spelling this array once had, not an invented one')
  const requoted = [
    'const wizardSteps = [',
    '  "Connect your tool",',
    "  'Memory sources',",
    '  `Your agent\'s toolkit`,',
    '  "Seed your graph",',
    "  `You're set`,",
    ']',
  ].join('\n')
  assert.ok(!requoted.includes(mainNeedle),
    'a pure re-quote FALSE-REDDED the pre-fix pin (defect 2: a re-quote is not a behaviour change)')
  assert.deepEqual(legacyWizardLabels(parseModule(requoted)), {
    declarators: 1,
    labels: ["Connect your tool", "Memory sources", "Your agent's toolkit", "Seed your graph", "You're set"],
  }, 'a re-quoted array renders the same five labels and must not redden the pin')
  // ── the direction an obvious "fix" introduces: a FALSE GREEN ─────────────
  // Normalising the escape before matching is the natural repair for that false
  // red, and it makes the needle match LIVE copy plus a comment — so the pin stays
  // green with the array's label deleted, which is the archived-not-deleted break
  // this test exists to catch.
  const liveCopyOnly = [
    'export const Card = () => (',
    '  <p>',
    "    You're set up in <strong>{shownOrgName || 'your organization'}</strong>",
    "    {/* the old step said You're set */}",
    '  </p>',
    ')',
  ].join('\n')
  assert.ok(liveCopyOnly.replace(/\\'/g, "'").includes("You're set"),
    'the normalised needle matches live copy and a comment — the false green this pin must not have')
  assert.deepEqual(legacyWizardLabels(parseModule(liveCopyOnly)), { declarators: 0, labels: null },
    'the AST pin finds no legacy array, so the same source reddens instead')
})

test('#3726 falsifiability: the welcome-card window fails LOUD on a missing gate and ignores prose', () => {
  // Every way the ONE guarded span in this file can stop naming the live card.
  const card = (block) => [
    '<div className="welcome-card">',
    '  <div className="welcome-head">',
    ...block,
    '    <h1 className="welcome-title">{wizardStageLabel(wizardStep, { hasOrg: welcomeHasOrg })}</h1>',
    '    <p>You are set up</p>',
    '  </div>',
    '  {LEGACY_WIZARD_ARCHIVED && welcomeOriented && (',
    '    <p className="wizard-title">{wizardSteps[wizardStep]}</p>',
    '  )}',
    '</div>',
  ].join('\n')
  // 1. The sentinel is GONE: `indexOf` is -1 and the pre-fix `slice(start, -1)` was
  //    the rest of the file — a pin watching a region it does not name.
  const unmarked = card([]).replace(
    '{LEGACY_WIZARD_ARCHIVED && welcomeOriented && (', '{someOtherFlag && (')
  assert.throws(() => welcomeCardWindow(parseModule(unmarked)), /must resolve/,
    'an unresolved end must fail LOUD, not widen the window to the rest of the file')
  // 2. The boundary still RESOLVES but a note INSIDE the card names the flag — the
  //    live shape of this hazard: main.jsx:9262 already mentions
  //    `LEGACY_WIZARD_ARCHIVED` in a comment inside the card, two characters short
  //    of forging the text sentinel. The raw-`indexOf` form is fooled; the AST read
  //    is not, because a comment is not a node.
  const notedInCard = card([
    '  {/* NEVER rendered while LEGACY_WIZARD_ARCHIVED && welcomeOriented */}',
    '  <p className="wizard-title">{wizardSteps[wizardStep]}</p>',
  ])
  const notedAst = parseModule(notedInCard)
  const notedHead = jsxElements(notedAst).find((e) => e.tag === 'div' && hasClass(e.cls, 'welcome-head'))
  const forgedEnd = notedInCard.indexOf('LEGACY_WIZARD_ARCHIVED &&', notedHead.start)
  assert.ok(forgedEnd > notedHead.start && forgedEnd < archivedGateStart(notedAst),
    'the comment really is EARLIER than the archived gate — the sentinel the pre-fix reader searched is inside the card')
  assert.deepEqual(
    jsxElements(notedAst).filter((e) => hasClass(e.cls, 'wizard-title') && e.start > notedHead.start && e.start < forgedEnd),
    [],
    'the raw-indexOf window is EMPTY, so `deepEqual(inCardTitles, [])` passed with the forbidden label sitting in the card')
  const noted = welcomeCardWindow(notedAst)
  assert.deepEqual(
    jsxElements(notedAst).filter((e) => hasClass(e.cls, 'wizard-title') && e.start > noted.start && e.start < noted.end).length,
    1,
    'the AST boundary ignores the prose, so the in-card label is still SEEN and the negative pin reddens')
  // 3. The boundary must be anchored to the GATE'S SHAPE, not to the flag's NAME.
  //    This is the shape the forbidden label comes back in when it is guarded by the
  //    flag itself: a TERNARY, not the `&&` gate. The in-card label is genuinely
  //    present, so the window has to reach the real gate and see it.
  const ternaryInCard = card([
    '  {LEGACY_WIZARD_ARCHIVED ? (<p className="wizard-title">{wizardSteps[wizardStep]}</p>) : null}',
  ])
  const ternaryAst = parseModule(ternaryInCard)
  const ternaryWindow = welcomeCardWindow(ternaryAst)
  // Independent of `end > start` (which the helper already asserts): the TERNARY's
  // own flag read must sit BEFORE the boundary, i.e. the boundary is the archived
  // `&& welcomeOriented` gate below the card and not the conditional the label hangs
  // off.
  let ternaryFlag = -1
  walkNodes(ternaryAst, (n) => {
    if (n.type === 'ConditionalExpression' && n.test.type === 'Identifier'
      && n.test.name === 'LEGACY_WIZARD_ARCHIVED') ternaryFlag = n.test.start
  })
  assert.ok(ternaryFlag > -1, 'the synthetic really plants a flag-guarded ternary inside the card')
  // Independent of the helper's own ordering check: the ternary is NOT a gate, so the
  // file still has exactly ONE and the boundary is not the conditional's test.
  assert.equal(archivedGates(ternaryAst).length, 1,
    'a flag-guarded TERNARY is not a gate — the reader must not count it')
  assert.notEqual(archivedGates(ternaryAst)[0].start, ternaryFlag,
    'the boundary is the archived `&&` gate, not the ternary\'s test expression')
  assert.ok(ternaryWindow.start < ternaryFlag,
    'the ternary sits inside the window, so its label would be SEEN by the in-card pin')
  assert.deepEqual(
    jsxElements(ternaryAst).filter((e) => hasClass(e.cls, 'wizard-title') && e.start > ternaryWindow.start && e.start < ternaryWindow.end).length,
    1,
    'a flag-guarded TERNARY in the card must not shrink the window past the label it contains')
  // …and with the gate gone, a ternary does NOT stand in for it: the boundary fails
  // LOUD. Anchoring on the flag's NAME instead let the ternary resolve the boundary
  // early, and the in-card pin then passed with the label in the card (measured).
  const ternaryOnly = ternaryInCard.replace(
    '{LEGACY_WIZARD_ARCHIVED && welcomeOriented && (', '{someOtherFlag && (')
  assert.throws(() => welcomeCardWindow(parseModule(ternaryOnly)), /must resolve/,
    'a ternary, an object key or a member property must not resolve the archived gate')
  // 4. The SHAPE-PRESERVING attack (measured against the shape-only anchor): the
  //    forbidden label comes back by COPYING the archived block's own gated line into
  //    the card. That reproduces the gate's shape exactly, so it is a SECOND candidate
  //    boundary — and if the reader took the first, the window would shrink past the
  //    label and the in-card pin would pass. Uniqueness is the invariant that makes
  //    this loud.
  const duplicateGate = card([
    '  {LEGACY_WIZARD_ARCHIVED && welcomeOriented && (<p className="wizard-title">{wizardSteps[wizardStep]}</p>)}',
  ])
  assert.throws(() => welcomeCardWindow(parseModule(duplicateGate)), /exactly one archived gate/,
    'a SECOND gate inside the card must fail LOUD, not silently become the window end')
  // …and the negative-bang spelling of the same attack (`!(A && B)`) is the same
  // second gate, because the inner `&&` is a node in its own right.
  const bangGate = card([
    '  {!(LEGACY_WIZARD_ARCHIVED && welcomeOriented) && (<p className="wizard-title">{wizardSteps[wizardStep]}</p>)}',
  ])
  assert.throws(() => welcomeCardWindow(parseModule(bangGate)), /exactly one archived gate/,
    'a negated copy of the gate is still a second gate node')
  // 5. A SINGLE gate RELOCATED inside the card (the outer one removed) keeps the count
  //    at one and still resolves — so the count alone is not enough. The gate must sit
  //    BELOW the card element; otherwise the window ends inside the region it certifies
  //    and any label after it is invisible.
  const movedGate = [
    '<div className="welcome-card">',
    '  <div className="welcome-head">',
    '    {LEGACY_WIZARD_ARCHIVED && welcomeOriented && (<p className="wizard-title">{wizardSteps[wizardStep]}</p>)}',
    '  </div>',
    '</div>',
  ].join('\n')
  assert.throws(() => welcomeCardWindow(parseModule(movedGate)), /AFTER the live card/,
    'a gate INSIDE the card must fail LOUD — the window would end inside the region it certifies')
})

test('#3726 falsifiability: the <h1> pin is selected by class and names every override', () => {
  // Selection is by tag + static className, so the spellings that false-redded the
  // text anchor (`{'welcome-title'}`, a backtick class, an ADDED attribute, a space
  // before `>`) all resolve — while a removed override still reddens.
  const element = (className, call) => [
    'export const Card = () => (',
    `  <h1 className={${className}} id="wizard-stage">`,
    `    {${call}}`,
    '  </h1>',
    ')',
  ].join('\n')
  const allOverrides = 'wizardStageLabel(wizardStep, { hasOrg: welcomeHasOrg, paused: effectivelyPaused, connected: serverHarnessConnected, buildFork: isBuildFork })'
  for (const className of ["'welcome-title'", "\"welcome-title\"", '`welcome-title`']) {
    const titles = jsxOf(element(className, allOverrides)).filter((e) => e.tag === 'h1' && hasClass(e.cls, 'welcome-title'))
    assert.equal(titles.length, 1, `the <h1> must resolve for className={${className}}`)
    assert.deepEqual(stageLabelCall(titles[0].node).overrides, {
      hasOrg: 'welcomeHasOrg', paused: 'effectivelyPaused', connected: 'serverHarnessConnected', buildFork: 'isBuildFork',
    }, 'a purely formatting edit (attribute added, class spelling changed) is a no-op')
  }
  const dropped = 'wizardStageLabel(wizardStep, { hasOrg: welcomeHasOrg, paused: effectivelyPaused, connected: serverHarnessConnected })'
  const titles = jsxOf(element("'welcome-title'", dropped)).filter((e) => e.tag === 'h1' && hasClass(e.cls, 'welcome-title'))
  assert.ok(!('buildFork' in stageLabelCall(titles[0].node).overrides),
    'a DROPPED override must be visible to the pin (on the real <h1> the deepEqual on the override map above reddens)')
  // A heading that stops calling the helper at all is a failure, not a skip.
  assert.equal(stageLabelCall(jsxOf(element("'welcome-title'", "'Connect your agent'"))[0].node), null,
    'an <h1> that no longer calls wizardStageLabel must redden the pin')
})

test('#3726 falsifiability: the in-card pin is anchored to the wizardSteps READ, not to a class name', () => {
  // The negative pin's window is the live card EXCLUDING the archived subtree. Anchor
  // it to the read, or four spellings of the same #2912 defect render with it green.
  const gate = '{LEGACY_WIZARD_ARCHIVED && welcomeOriented && (<p className="wizard-title">{wizardSteps[wizardStep]}</p>)}'
  const build = (inner) => [
    '<div className="welcome-card">',
    '  <div className="welcome-head">',
    inner,
    '  </div>',
    `  ${gate}`,
    '</div>',
  ].join('\n')
  const read = (source) => {
    const ast = parseModule(source)
    return inCardStageReads(ast, welcomeCard(ast), archivedSubtree(ast))
  }
  // The archived block's OWN read is legitimately in the card: excluded, so the
  // control below is `[]` and the pin is not vacuous by construction.
  assert.deepEqual(read(build('    <h1 className="welcome-title">ready</h1>')), [],
    'the archived block\'s own wizardSteps read is not an in-card stage label')
  for (const [label, inner] of [
    ['classless', '    <p>{wizardSteps[wizardStep]}</p>'],
    ['the class= alias', '    <p class="wizard-title">{wizardSteps[wizardStep]}</p>'],
    ['a template className', '    <p className={`wizard-title ${wizardStep}`}>{wizardSteps[wizardStep]}</p>'],
    // #3726 cycle 5 (P2): optional chaining is a DIFFERENT node type
    // (`OptionalMemberExpression`), so `?.[…]` and `?.at(…)` were invisible to a
    // `MemberExpression`-only predicate — one keystroke past the pin in both
    // directions (measured: the in-card label rendered with the pin green).
    ['optional chaining', '    <p>{wizardSteps?.[wizardStep]}</p>'],
    ['optional .at()', '    <p>{wizardSteps?.at(wizardStep)}</p>'],
  ]) {
    assert.equal(read(build(inner)).length, 1,
      `an in-card stage label spelled ${label} must be SEEN — the class-name pin alone misses it`)
  }
  // A HOIST is a value, not a position: the legacy array read assigned to a binding
  // and rendered by name is the same defect one hop away (measured green before this
  // reader resolved it).
  const hoisted = [
    'const legacyStageLabel = wizardSteps[wizardStep]',
    'export const Card = () => (',
    '<div className="welcome-card">',
    '  <div className="welcome-head">',
    '    <p>{legacyStageLabel}</p>',
    '  </div>',
    `  ${gate}`,
    '</div>',
    ')',
  ].join('\n')
  assert.equal(read(hoisted).length, 1,
    'a stage label HOISTED through a binding is still an in-card stage label')
  // #3726 cycle 6 (P3): the TERNARY spelling of the gate is a shape the file already
  // recognises (`gateRendersArchivedBlock`), so the EXCLUSION must recognise it too —
  // otherwise a behavior-preserving `(A && B) ? (<jsx/>) : null` rewrite makes the
  // archived block's own label look like an in-card one (measured, with a message
  // naming a label that was not there).
  const ternaryGate = (op) => [
    '<div className="welcome-card">',
    '  <div className="welcome-head"><h1 className="welcome-title">ready</h1></div>',
    `  ${op}`,
    '</div>',
  ].join('\n')
  for (const [shape, op] of [
    ['&&', '{LEGACY_WIZARD_ARCHIVED && welcomeOriented && (<p className="wizard-title">{wizardSteps[wizardStep]}</p>)}'],
    ['ternary', '{(LEGACY_WIZARD_ARCHIVED && welcomeOriented) ? (<p className="wizard-title">{wizardSteps[wizardStep]}</p>) : null}'],
  ]) {
    assert.deepEqual(read(ternaryGate(op)), [],
      `the archived block's own label is excluded in its ${shape} spelling — it is never an in-card label`)
  }
  // …and a copy BELOW the archived block is still inside the card, where the
  // window-to-the-gate pin cannot see it.
  const below = [
    '<div className="welcome-card">',
    '  <div className="welcome-head"><h1 className="welcome-title">ready</h1></div>',
    `  ${gate}`,
    '  <p>{wizardSteps[wizardStep]}</p>',
    '</div>',
  ].join('\n')
  assert.equal(read(below).length, 1,
    'a stage label BELOW the archived block is still in the live card — the window-to-the-gate pin never reaches it')
  // #3726 cycle 6 (P3): the read pin is about the LABEL, so a non-element read of the
  // array — a progress line, a map — is not one. Matching any member access redded
  // this with a message naming a stage label that was not there (measured).
  for (const notALabel of ['    <p>Step {wizardStep + 1} of {wizardSteps.length}</p>',
    '    <p>{wizardSteps.map((s) => s).length}</p>']) {
    assert.deepEqual(read(build(notALabel)), [],
      `a non-element read of the array is not a stage label: ${notALabel.trim()}`)
  }
})

test('#3726 falsifiability: the in-card title pin fires on EVERY className spelling', () => {
  // The negative pin is the one where a false green is worst, so prove it can red:
  // a re-introduced in-card stage label spelled as a JSX expression or a template
  // literal (the exact spellings that defeated the two-delimiter regex) is found.
  for (const className of ["'wizard-title'", '\"wizard-title\"', '`wizard-title`', "'wizard-title extra'"]) {
    const synthetic = [
      'export const Card = () => (',
      '  <div className="welcome-card">',
      '    <div className="welcome-head">',
      '      <h1 className="welcome-title">ready</h1>',
      '    </div>',
      `    <p className={${className}}>{wizardSteps[wizardStep]}</p>`,
      '  </div>',
      ')',
      'export const Legacy = () => LEGACY_WIZARD_ARCHIVED && welcomeOriented && null',
    ].join('\n')
    // The SAME window helper the live pin uses — so the synthetic exercises the
    // boundary checks too, rather than re-deriving a window with its own fallback.
    const { start: cardStart, end: gateStart } = welcomeCardWindow(parseModule(synthetic))
    const inCard = jsxOf(synthetic)
      .filter((e) => e.start > cardStart && e.start < gateStart && hasClass(e.cls, 'wizard-title'))
    assert.equal(inCard.length, 1, `an in-card label spelled className={${className}} must redden the pin`)
  }
})

test('#3726 falsifiability: stepWrites records every write spelling, so its negative pin can red', () => {
  // The live pin is `!stepWrites(SRC_AST).includes('catalog-presented')`. A helper that
  // cannot recognise the write would make that assertion vacuous, so plant the
  // write in the three delimiter spellings (plus a member assignment and a quoted
  // key) and require it to be read back.
  const synthetic = [
    'export const go = (state) => {',
    "  state.post({ step: 'catalog-presented' })",
    '  state.post({ "step": "catalog-presented" })',
    '  state.post({ [`step`]: `catalog-presented` })',
    "  state.step = 'catalog-presented'",
    '  state.post({ step: nextStep })',   // not a literal: not a readable write
    '}',
  ].join('\n')
  assert.deepEqual(stepWrites(parseModule(synthetic)).sort(), [
    'catalog-presented', 'catalog-presented', 'catalog-presented', 'catalog-presented',
  ].sort(), 'every `step` write spelling must be read back')
})

test('DE2E-2 copy sweep: org-create dialog + wizard copy say Organization', () => {
  assert.ok(src.includes('Create a new organization'), 'create-team dialog header')
  assert.ok(src.includes('Organization name required'), 'validation error copy')
  // #2912: the welcome header no longer prints '<org> is set up' (the stage is
  // the h1) — the ready copy is the org-create summary line, which must still
  // name the Organization. #2487/#2547's lowercase fallback ('Your organization
  // is set up') lived in the <h1> this PR removed; the org name now renders in
  // the eyebrow, and the no-name case is covered by the stage label itself.
  // #3726 cycle 3: a pure-formatting edit (`{ shownOrgName || 'your organization' }`)
  // or a template-literal fallback false-redded this. The rendered behaviour is
  // a `||` whose right side is the string; read that, not the punctuation.
  // #3726 cycle 5 (P1): reading EVERY `|| <string>` in the module was a false
  // green — main.jsx has three `shownOrgName || 'your organization'` sites (this
  // ready copy plus two "Continue setting up" headings in a different card), so
  // deleting the ready-copy fallback left the assertion green. The pin is anchored
  // to the LIVE WELCOME CARD and to the `shownOrgName` operand. The card's only
  // other org fallback is the connect-step key line, which spells the string with
  // a capital O (`'your Organization'`) and therefore cannot satisfy this pin.
  assert.ok(readyCopyOrgFallbacks(SRC_AST).includes('your organization'),
    'the live welcome ready copy falls back to the user\'s Organization — a `||` in another card cannot satisfy this')
  assert.ok(src.includes('Creating your Organization and API key'), 'provisioning copy')
  assert.ok(src.includes('Your Organization and API key are live'), 're-entry + first-data cards')
})

test('#3726 falsifiability: the ready-copy pin is anchored to the welcome card, not the module', () => {
  // The mutation the old whole-file scan could not see: drop the ready copy's
  // fallback while an unrelated card keeps the same `|| 'your organization'`.
  const card = (ready) => [
    'export const Card = () => (',
    '  <div className="welcome-card">',
    '    <div className="welcome-head">',
    '      <h1 className="welcome-title">ready</h1>',
    '    </div>',
    `    <p>You're set up in <strong>${ready}</strong></p>`,
    '  </div>',
    ')',
  ].join('\n')
  // The archived block is the window's END sentinel; without it the window has no
  // documented end and `welcomeCardWindow` refuses (fail loud, not rest-of-file).
  const archived = 'export const Legacy = () => LEGACY_WIZARD_ARCHIVED && welcomeOriented && null'
  const otherCard = "export const Other = () => <h2>Continue setting up {shownOrgName || 'your organization'}</h2>"
  const withFallback = `${card("{shownOrgName || 'your organization'}")}\n${archived}\n${otherCard}`
  const withoutFallback = `${card('{shownOrgName}')}\n${archived}\n${otherCard}`
  assert.deepEqual(readyCopyOrgFallbacks(parseModule(withFallback)), ['your organization'],
    'the ready copy resolves when its fallback is present')
  // The pre-fix form: collect every `x || '<string>'` in the module. It stays
  // satisfied by the heading in the OTHER card — the false green.
  assert.ok(orFallbacks(parseModule(withoutFallback)).map((f) => f.value).includes('your organization'),
    'a whole-module scan is still satisfied by the unrelated card — that is the false green this pin must not have')
  assert.deepEqual(readyCopyOrgFallbacks(parseModule(withoutFallback)), [],
    'deleting the ready-copy fallback must REDDEN the anchored pin')
  // …and a formatting-only edit (spaces inside the braces, a template fallback)
  // is still the same input, which is what reading the AST buys.
  const spaced = `${card('{ shownOrgName || `your organization` }')}\n${archived}\n${otherCard}`
  assert.deepEqual(readyCopyOrgFallbacks(parseModule(spaced)), ['your organization'],
    'spacing and a template-literal fallback are formatting, not behaviour')
  // …and so is a REFLOW: the same copy split across source lines renders byte-identical
  // HTML (verified with the repo's render probe), so it must not red a pin that is
  // about a missing fallback.
  const reflowed = card("{shownOrgName || 'your organization'}")
    .replace("You're set up in <strong>", "You're set up\n                            in <strong>")
  assert.ok(reflowed !== card("{shownOrgName || 'your organization'}")
    && !reflowed.includes("You're set up in"), 'the synthetic really reflowed the copy')
  assert.deepEqual(readyCopyOrgFallbacks(parseModule(`${reflowed}\n${archived}`)), ['your organization'],
    'a whitespace-only reflow of the ready copy is a no-op')
  // #3726 cycle 4 (P3): carrying the text is not RENDERING the fallback. A dead decoy
  // in the same paragraph must not satisfy the pin while the live copy renders
  // "You're set up in .".
  const deadDecoy = card('{shownOrgName}')
    .replace("</strong></p>", "</strong>. {false && <span>{shownOrgName || 'your organization'}</span>}Next, choose</p>")
  assert.deepEqual(readyCopyOrgFallbacks(parseModule(`${deadDecoy}\n${archived}`)), [],
    'a non-rendered decoy in the SAME paragraph must not satisfy the ready-copy pin')
  // …nor a fallback in an ATTRIBUTE of the carrier: a `title={…}` is a tooltip, not the
  // copy the pin names (measured green while the visible copy rendered
  // "You're set up in .").
  const attributeDecoy = card('{shownOrgName}')
    .replace("</strong></p>", "</strong>. Next, choose<span title={shownOrgName || 'your organization'} /></p>")
  assert.deepEqual(readyCopyOrgFallbacks(parseModule(`${attributeDecoy}\n${archived}`)), [],
    'a fallback in an ATTRIBUTE of the carrier is not the rendered copy')
  // …and the HOIST, the other direction: extracting the fallback to a binding changes
  // no rendered character, so it must not red (measured as a false red before the
  // reader resolved one hop).
  const hoistedFallback = `const orgLabel = shownOrgName || 'your organization'\n`
    + card('{orgLabel}')
  assert.deepEqual(readyCopyOrgFallbacks(parseModule(`${hoistedFallback}\n${archived}`)), ['your organization'],
    'hoisting the fallback into a binding is a refactor, not a missing fallback')
  // …but resolving the VALUE does not exempt the RENDER SITE: a hoisted binding that
  // is only rendered in a dead branch or in an attribute is not the copy (#3726
  // cycle 6 — both measured green while the visible copy rendered
  // "You're set up in .").
  const hoistedDeadDecoy = `const orgLabel = shownOrgName || 'your organization'\n`
    + card('{shownOrgName}').replace('</strong></p>', '</strong>. {false && <span>{orgLabel}</span>}</p>')
  assert.deepEqual(readyCopyOrgFallbacks(parseModule(`${hoistedDeadDecoy}\n${archived}`)), [],
    'a hoisted fallback rendered only in a DEAD branch is not the rendered copy')
  const hoistedAttributeDecoy = `const orgLabel = shownOrgName || 'your organization'\n`
    + card('{shownOrgName}').replace('</strong></p>', '</strong>. <span title={orgLabel} /></p>')
  assert.deepEqual(readyCopyOrgFallbacks(parseModule(`${hoistedAttributeDecoy}\n${archived}`)), [],
    'a hoisted fallback rendered only in an ATTRIBUTE is not the rendered copy')
})

test('wizardComplete no longer writes onboarding_complete (accept-and-drop, plan T7)', () => {
  // #3726 cycle 3: same mechanism as the catalog-presented pin — a negative
  // source match that hard-codes the key's spelling, so a re-quoted key
  // (`{ "onboarding_complete": true }`) shipped the write it forbids and stayed
  // GREEN. Read the AST: any object property or member assignment that sets it
  // to TRUE. The PATCH body shape is asserted separately below.
  assert.deepEqual(onboardingCompleteWrites(SRC_AST), [],
    'wizardComplete dropped the PATCH onboarding_complete write')
})

test('#3726 falsifiability: the onboarding_complete reader sees a TRUE write and ignores a reset', () => {
  // A negative pin whose reader cannot recognise the write is vacuous, and main.jsx
  // has no live `onboarding_complete: true` write to exercise it — so plant the
  // write in the key spellings a re-quote produces, plus the member assignment.
  const synthetic = [
    'export const go = (state) => {',
    '  state.patch({ onboarding_complete: true })',
    '  state.patch({ "onboarding_complete": true })',
    '  state.patch({ [`onboarding_complete`]: true })',
    '  state.onboarding_complete = true',
    '  state.patch({ onboarding_complete: false })',   // a RESET, not the forbidden opt-in
    '  state.onboarding_complete = false',
    '}',
  ].join('\n')
  assert.equal(onboardingCompleteWrites(parseModule(synthetic)).length, 4,
    'every TRUE write spelling must be read back — otherwise the negative pin cannot red')
  const resetOnly = [
    'export const go = (state) => {',
    '  state.patch({ onboarding_complete: false })',
    '  state.onboarding_complete = false',
    '}',
  ].join('\n')
  assert.deepEqual(onboardingCompleteWrites(parseModule(resetOnly)), [],
    'a `false` write is a reset — collecting it made the pin report a defect that is not there')
})

test('review P1 / #3913: the build fork handler writes NO checkpoint step (the catalog card still renders)', () => {
  // #1997 (review P1) pinned the handler's catalog-presented write; #3913
  // (owner ruling 2026-09-20) removed it — the build-fork gate completes on the
  // two acts the server OBSERVES (harness-connected + first-points-filed), so a
  // fork pick must record no step. The build branch still exists: a build pick
  // stays on the fork step so the catalog CARD renders, and self picks advance.
  // #3726 defect 2: quote-agnostic. Matching the literal with single quotes
  // hard-coded false-redded this test on a pure re-quote ("which quote did the
  // author use" is not a behaviour), while the checkpoint pin beside it stayed
  // GREEN — the same hard-coding in the negative form is a false green, because
  // the pattern simply stops matching the writer it exists to catch.
  assert.deepEqual(forkIdComparisons(SRC_AST), ['build'],
    'handleWizardFork still branches on the build pick (non-build picks advance, build stays for the card)')
  assert.ok(src.includes('Build catalog'),
    'the build catalog CARD still renders (only the write was removed)')
  assert.ok(!stepWrites(SRC_AST).includes('catalog-presented'),
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
    // A VariableDeclarator's parent is ALWAYS a VariableDeclaration in a Babel AST, so
    // this used to be a climb that could not make progress (it reassigned `parent`,
    // spinning forever on any other holder). Asserted instead of looped: a future
    // non-declarator holder fails LOUD rather than hanging the suite.
    assert.equal(parent?.type, 'VariableDeclaration',
      `${node.id.name} is not held by a VariableDeclaration — the reachability scan classifies const vs let/var from that parent`)
    if (parent.kind !== 'const') return
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

/**
 * #4915: the census covers EVERY render module of the application, not just
 * main.jsx. The per-harness status rows — and the five copy constants only they
 * read — moved into `harnessStatusCard.jsx`, so a scan that kept reading main.jsx
 * alone would have LOST coverage of exactly those constants: they would vanish
 * from `rows`, the census key-set assertion would fail once, and a subsequent
 * editor could re-pin a surface that no longer included them. Summing the
 * per-file rows keeps the property ("every imported copy constant is classified
 * and read") over the app's render surface. Counts ADD across files, so a name
 * imported by two modules is one row with the sum of its reads — the
 * duplicate-import check therefore runs per FILE, where "duplicate" means the
 * same module importing one name twice.
 */
function mergeCopyReads(classifieds) {
  const merged = new Map()
  const flags = []
  const shadowed = []
  for (const one of classifieds) {
    flags.push(...one.flags)
    shadowed.push(...one.shadowed)
    for (const row of one.rows) {
      const prev = merged.get(row.name)
      if (!prev) merged.set(row.name, { ...row })
      else {
        prev.live += row.live
        prev.archived += row.archived
        prev.shadowed = prev.shadowed || row.shadowed
      }
    }
  }
  return { rows: [...merged.values()], flags, shadowed }
}

// The app's render modules, in census order.
const COPY_SOURCES = ['main.jsx', 'harnessStatusCard.jsx']
const copyReadsByFile = Object.fromEntries(
  COPY_SOURCES.map((file) => [file, classifyCopyReads(readFileSync(join(__dirname, file), 'utf8'))]))
const classified = mergeCopyReads(Object.values(copyReadsByFile))

// Hand-pinned census of `name: [live, archived]`, summed over the app's render
// modules (#4915 — `main.jsx` + `harnessStatusCard.jsx`), in import order.
// `archived` reads are behind the LEGACY_WIZARD_ARCHIVED gate; `live` reads are
// what a user (or a prompt card) actually receives. Reference-only today: the
// HARNESS_* install/skill/persist family, whose LIVE carriers are the agent
// PROMPT (`wizardPromptText`: the dashboard never shows `claude mcp add` — it
// hands the agent "Add Tortoise MCP at <url>") and, for the capture step,
// `HARNESS_CAPTURE_INSTALL` in the Memory-sources row.
//
// #4915 note: the numbers below did NOT move when the per-harness status rows
// moved out of main.jsx. That is the point of summing across modules — the five
// constants those rows read went on being read (live) from their new module, and
// HARNESS_NAMES / HARNESS_ORDER each traded a main.jsx read for a card read. A
// pin that had stayed main.jsx-only would have gone red here and, once re-pinned,
// would have certified a surface that no longer included the rows.
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
  CODEX_DESKTOP_COMMAND: [1, 0],
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
  for (const [file, one] of Object.entries(copyReadsByFile)) {
    const names = one.rows.map((r) => r.name)
    assert.equal(new Set(names).size, names.length, `${file} imports a copy constant twice`)
  }
  const got = {}
  for (const row of classified.rows) {
    assert.ok(!(row.name in got), `duplicate row for ${row.name} — mergeCopyReads must key by name`)
    got[row.name] = [row.live, row.archived]
  }
  assert.deepEqual(Object.keys(got).sort(), Object.keys(COPY_READ_CENSUS).sort(),
    'the imported copy-constant surface changed — a new or removed import must be classified, never skipped')
  assert.deepEqual(got, COPY_READ_CENSUS,
    'the render reachability of the render modules\' copy constants changed. A constant that lost its last live '
    + 'read site is now reference-only — an edit to it reaches no user, and any test asserting shipped behavior on '
    + 'it needs the linked assertion on the live copy (#3802). A new live read site is a shipped change. Update '
    + 'the pin deliberately.')
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
