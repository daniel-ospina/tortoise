// modalInputContrastTripwire.test.js — #2778 static tripwires (CI-run via
// dashboard-js-tests, node --test zero-dep convention, mirroring the other
// main.jsx/index.css tripwires). The create-organization dialog's name
// <input> shipped with NO class and NO inline style; the global
// `input, textarea, select` rule sets only color/caret-color, so the field
// fell back to the UA white background under the dark theme's light text —
// measured 1.48:1 (WCAG AA needs 4.5:1).
//
// The checks pin the fix's SHAPE (not whole-sheet override resistance):
//   1. exactly one shared `.modal input/textarea/select` rule, with correct
//      effective declarations: dark --surface background, a PAINTED border
//      (width > 0, style not none/hidden) whose COMPOSITED contrast clears the
//      WCAG 1.4.11 non-text 3:1 floor (last-wins border/border-* resolution),
//      the --text/--surface pair clearing the WCAG 1.4.3 4.5:1 text floor, and
//      no custom-property declaration inside the rule;
//   2. the create-org name input itself carries no class, no inline style and
//      no own background — i.e. it is exactly the element that MUST be themed
//      by the shared rule (a re-added inline/white background fails here);
//   3. `.delete-confirm-input` stays `.modal`-scoped (0,2,0) so it keeps
//      out-specifying the shared default and keeps its destructive red border.
//
// Deliberately out of scope — verified instead by computed styles in the
// dashboard-e2e DOM lane (#2793):
//   - proving the <input> is *nested* inside the `.modal` JSX element (a
//     structural question; a hand-rolled mask is unreliable on this file —
//     prose apostrophes/quotes desync quote-balancing heuristics — and the
//     zero-dep convention rules out importing the transitive JSX parsers);
//   - proving NO other rule anywhere in the sheet overrides the field's
//     background/border. At-rule nesting, `:is()`/attribute selectors,
//     higher-specificity/`!important` rules and a `--surface` redefinition
//     elsewhere all evade a regex scan, because a regex scan is not a cascade
//     resolver — four review rounds each defeated an attempt at it. The DOM
//     lane asserts `getComputedStyle` directly and is the right home for it.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')
const indexCss = readFileSync(join(here, 'index.css'), 'utf8')

// Comment-stripped + whitespace-collapsed copy so selector/declaration
// matching is formatting-insensitive (a reformat or a nearby comment must not
// silently drop the guard).
const cssFlat = indexCss.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\s+/g, ' ')

// WCAG contrast helpers — the border check computes the real ratio rather than
// trusting a token name, so a future edit cannot restore a 1.41:1 boundary
// while keeping the guard green.
const hexToRgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))
const srgb = (c) => (c / 255 <= 0.04045 ? c / 255 / 12.92 : ((c / 255 + 0.055) / 1.055) ** 2.4)
const luminance = ([r, g, b]) => 0.2126 * srgb(r) + 0.7152 * srgb(g) + 0.0722 * srgb(b)
const contrastRatio = (a, b) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

function declarationsOf(body) {
  return body
    .split(';')
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => {
      const i = s.indexOf(':')
      return i === -1 ? { prop: s, value: '' } : { prop: s.slice(0, i).trim(), value: s.slice(i + 1).trim() }
    })
}

// Effective (last-wins) value of a property, as the browser would resolve it
// within a single rule.
function effectiveValue(decls, props) {
  let value = null
  for (const d of decls) if (props.includes(d.prop)) value = d.value
  return value
}

test('#2778: a single shared .modal input/textarea/select rule themes dialog fields', () => {
  const rule = cssFlat.match(/\.modal input, \.modal textarea, \.modal select \{([^}]*)\}/)
  assert.ok(
    rule,
    'index.css must keep a shared `.modal input, .modal textarea, .modal select` rule — the create-org dialog rendered a bare <input> that relied on it',
  )
  const sharedCount = (cssFlat.match(/\.modal input, \.modal textarea, \.modal select \{/g) || []).length
  assert.equal(sharedCount, 1, 'exactly one shared dialog-field rule may exist — a duplicate override could quietly reset it')

  const decls = declarationsOf(rule[1])
  // A custom-property declaration inside the rule could retarget a token the
  // contrast/background checks trust (e.g. `--surface: #ffffff`).
  const customProps = decls.filter((d) => d.prop.startsWith('--')).map((d) => d.prop)
  assert.deepEqual(customProps, [], `the shared dialog-field rule must not redefine custom properties (found: ${customProps.join(', ')})`)

  // The exact bug: no background was set, so the UA white showed through.
  const background = effectiveValue(decls, ['background', 'background-color'])
  assert.ok(
    background && /^var\(--surface(?:\s*,|\s*\))/.test(background),
    `the dialog-field default must set a dark \`background\` resolving to --surface (not --surface-hover); got: ${background}`,
  )
  assert.doesNotMatch(background, /(#fff|#ffffff|white)\b/i, 'the dialog-field default must never resolve to a white background')
  assert.match(effectiveValue(decls, ['color']) || '', /var\(--text/, 'the dialog-field default must set `color`')

  // Resolve the PAINTED border (last-wins across border / border-* longhands).
  let paintedWidth = null
  let paintedStyle = null
  let paintedColor = null
  for (const d of decls) {
    if (d.prop === 'border') {
      const w = d.value.match(/(^|\s)(\d+(?:\.\d+)?)(px)?(\s|$)/)
      if (w) paintedWidth = Number(w[2])
      else if (/(^|\s)0(\s|$)/.test(d.value)) paintedWidth = 0
      const st = d.value.match(/\b(solid|dashed|dotted|double|none|hidden)\b/)
      if (st) paintedStyle = st[1]
      const c = d.value.match(/(rgba?\([^)]*\)|var\([^)]*\))/)
      if (c) paintedColor = c[1]
    } else if (d.prop === 'border-width') {
      const w = d.value.match(/^(\d+(?:\.\d+)?)(px)?$/)
      paintedWidth = w ? Number(w[1]) : d.value.trim() === '0' ? 0 : paintedWidth
    } else if (d.prop === 'border-style') {
      paintedStyle = d.value.trim()
    } else if (d.prop === 'border-color') {
      paintedColor = d.value.trim()
    }
  }
  assert.ok(paintedStyle !== 'none' && paintedStyle !== 'hidden', `the dialog-field border must be painted (effective style: ${paintedStyle})`)
  assert.ok(paintedWidth !== null && paintedWidth > 0, `the dialog-field border must have a non-zero width (effective: ${paintedWidth})`)

  // WCAG 1.4.11: the field fill is the same --surface as the .modal panel, so
  // the border is the only cue identifying the control and must clear 3:1.
  const panelRule = cssFlat.match(/\.modal \{([^}]*)\}/)
  assert.ok(panelRule, 'the base `.modal` panel rule must exist')
  assert.doesNotMatch(panelRule[1], /--surface\s*:/, '--surface must not be redefined on .modal (the contrast check would use the wrong panel colour)')
  const surfaceHex = indexCss.match(/--surface:\s*(#[0-9a-fA-F]{6})/)
  assert.ok(surfaceHex, 'the --surface token must be defined')
  const surface = hexToRgb(surfaceHex[1])

  // WCAG 1.4.3: the text/background pair is the actual reported defect
  // (1.48:1 → 11.76:1), so compute it rather than only checking token names.
  const textHex = indexCss.match(/--text:\s*(#[0-9a-fA-F]{6})/)
  assert.ok(textHex, 'the --text token must be defined')
  const textRatio = contrastRatio(hexToRgb(textHex[1]), surface)
  assert.ok(
    textRatio >= 4.5,
    `the dialog-field text must clear the WCAG 1.4.3 4.5:1 floor against --surface; measured ${textRatio.toFixed(2)}:1 (--text on --surface)`,
  )

  const whiteAlpha = paintedColor && paintedColor.match(/^rgba\(255,\s*255,\s*255,\s*([0-9.]+)\)$/)
  assert.ok(
    whiteAlpha,
    `the effective dialog-field border must be a white-alpha border (a var(--border) token measures only 1.41:1 against the identical --surface panel); got: ${paintedColor}`,
  )
  const alpha = Number(whiteAlpha[1])
  const composited = surface.map((c) => alpha * 255 + (1 - alpha) * c)
  const boundaryRatio = contrastRatio(composited, surface)
  assert.ok(
    boundaryRatio >= 3,
    `the dialog-field boundary must clear the WCAG 1.4.11 non-text 3:1 floor against the identical --surface panel; measured ${boundaryRatio.toFixed(2)}:1 (white alpha ${alpha})`,
  )
})

test('#2778: the create-org name input relies on the shared rule (no class/style/own background)', () => {
  // Proximity-scoped: the first <input> after the create-org dialog marker is
  // the name field (the upgrade branch holds only buttons). Pin the exact bug
  // shape — that this field must be themed BY THE SHARED RULE, not by itself.
  // #2789 moved the marker: the dialog no longer carries a static aria-label
  // (each mode's heading names it via aria-labelledby), so the anchor is now
  // the modal's aria-labelledby expression.
  const modalIdx = mainJsx.indexOf('create-org-title-limit')
  assert.notEqual(modalIdx, -1, 'the create-organization dialog must still exist')
  const inputStart = mainJsx.indexOf('<input', modalIdx)
  assert.notEqual(inputStart, -1, 'the create-org name input must render after the dialog marker')
  const inputEnd = mainJsx.indexOf('/>', inputStart)
  assert.notEqual(inputEnd, -1, 'the create-org name input must be a self-closing JSX tag')
  const tag = mainJsx.slice(inputStart, inputEnd + 2)
  assert.match(tag, /aria-label="Organization name"/, 'the first input after the create-org marker must be the organization-name field')
  assert.doesNotMatch(tag, /className=/, 'the field must not carry a class — the shared `.modal input` rule is what themes it')
  assert.doesNotMatch(tag, /\bstyle=/, 'the field must not carry an inline style')
  assert.doesNotMatch(
    tag,
    /\bbackground(-color)?\s*:/,
    'the field must not set its own background — an unstyled field inheriting the UA white is exactly the #2778 bug the shared rule must own',
  )
})

test('#2778: .delete-confirm-input out-specifies the shared dialog-field default', () => {
  const rule = cssFlat.match(/([^{}]*\.delete-confirm-input[^{}]*)\{([^}]*)\}/)
  assert.ok(rule, 'the graph-delete confirm input rule must still exist')
  const selector = rule[1].trim()
  // `.modal .delete-confirm-input` is (0,2,0), beating the shared
  // `.modal input` (0,1,1). A bare `.delete-confirm-input` (0,1,0) would
  // lose and strip the destructive red border.
  assert.equal(
    selector,
    '.modal .delete-confirm-input',
    'the confirm-input rule must stay scoped to .modal so it out-specifies the shared dialog-field default',
  )
  assert.match(
    rule[2],
    /border: 1px solid var\(--red/,
    'the destructive confirm field must keep its red border',
  )
})
