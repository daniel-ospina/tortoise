// modalInputContrastTripwire.test.js — #2778 static tripwires (CI-run via
// dashboard-js-tests, node --test zero-dep convention, mirroring the other
// main.jsx/index.css tripwires). The create-organization dialog's name
// <input> shipped with NO class and NO inline style; the global
// `input, textarea, select` rule sets only color/caret-color, so the field
// fell back to the UA white background under the dark theme's light text —
// measured 1.48:1 (WCAG AA needs 4.5:1). The checks below pin the fix's
// load-bearing shapes so an unstyled dialog field can never ship again:
//   1. a shared `.modal input/textarea/select` rule that sets a dark
//      background and a border whose composited contrast clears the WCAG
//      1.4.11 non-text 3:1 floor against the panels's own --surface;
//   2. the create-org name input is structurally NESTED inside that `.modal`
//      element (a matching-close scan on comment-stripped source, so moving
//      the input out of the dialog fails even if a comment contains `<div`);
//   3. the destructive `.delete-confirm-input` still out-specifies the
//      shared default (its red border must not be stripped).
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

// WCAG contrast helpers — the border check below computes the real ratio
// rather than trusting a token name, so a future edit cannot restore the
// 1.41:1 boundary while keeping the guard green.
const hexToRgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))
const srgb = (c) => (c / 255 <= 0.04045 ? c / 255 / 12.92 : ((c / 255 + 0.055) / 1.055) ** 2.4)
const luminance = ([r, g, b]) => 0.2126 * srgb(r) + 0.7152 * srgb(g) + 0.0722 * srgb(b)
const contrastRatio = (a, b) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

test('#2778: a shared .modal input/textarea/select rule themes dialog fields', () => {
  const rule = cssFlat.match(/\.modal input, \.modal textarea, \.modal select \{([^}]*)\}/)
  assert.ok(
    rule,
    'index.css must keep a shared `.modal input, .modal textarea, .modal select` rule — the create-org dialog rendered a bare <input> that relied on it',
  )
  const decls = rule[1]
  // Sanity check that the rule was declared in the modal section of the sheet
  // (after the base `.modal` panel rule), not somewhere unrelated.
  const modalBaseIdx = cssFlat.indexOf('.modal {')
  assert.ok(modalBaseIdx !== -1, 'the base `.modal` panel rule must exist')
  assert.ok(
    rule.index > modalBaseIdx,
    'the dialog-field default must be declared after the base `.modal` panel rule',
  )
  // The exact bug: no background was set, so the UA white showed through.
  assert.match(
    decls,
    /background: var\(--surface/,
    'the dialog-field default must set a dark `background` (var(--surface)) — without it the UA white returns',
  )
  assert.doesNotMatch(
    decls,
    /background: (#fff|#ffffff|white)\b/i,
    'the dialog-field default must never resolve to a white background',
  )
  // color/border complete the themed field (the issue measured a default
  // grey border, not just the white background).
  assert.match(decls, /color: var\(--text/, 'the dialog-field default must set `color`')
  // WCAG 1.4.11: the field fill is the same --surface as the .modal panel, so
  // the border is the only cue identifying the control and must clear 3:1.
  // Compute the real composited ratio — a token-name check alone would let
  // a literal 0.12-alpha value (1.41:1) slip through.
  const surfaceHex = indexCss.match(/--surface:\s*(#[0-9a-fA-F]{6})/)
  assert.ok(surfaceHex, 'the --surface token must be defined')
  const surface = hexToRgb(surfaceHex[1])
  const border = decls.match(/border:\s*1px solid rgba\(255,\s*255,\s*255,\s*([0-9.]+)\)/)
  assert.ok(
    border,
    'the dialog-field border must be a white-alpha border (the panel background is --surface; a var(--border) token measures only 1.41:1 there)',
  )
  const alpha = Number(border[1])
  const composited = surface.map((c) => alpha * 255 + (1 - alpha) * c)
  const boundaryRatio = contrastRatio(composited, surface)
  assert.ok(
    boundaryRatio >= 3,
    `the dialog-field boundary must clear the WCAG 1.4.11 non-text 3:1 floor against the identical --surface panel; measured ${boundaryRatio.toFixed(2)}:1 (white alpha ${alpha})`,
  )
})

test('#2778: the organization-name input is nested inside the create-org .modal', () => {
  // Structural containment: strip comments (a `{/* ... <div ... */}` comment
  // must not inflate the tag count), then scan for the .modal element's
  // MATCHING close and assert the input sits before it. A raw proximity
  // window false-passed when the input was moved out of the dialog.
  const jsx = mainJsx.replace(/\/\*[\s\S]*?\*\//g, ' ')
  const modalStart = jsx.indexOf(
    '<div className="modal" role="dialog" aria-modal="true" aria-label="Create a new organization"',
  )
  assert.notEqual(modalStart, -1, 'the create-organization dialog must still exist')
  const inputIdx = jsx.indexOf('aria-label="Organization name"', modalStart)
  assert.notEqual(inputIdx, -1, 'the organization-name input must render after the dialog opens')

  const tag = /<div\b[^<>]*?(\/?)>|<\/div>/g
  tag.lastIndex = modalStart
  let depth = 0
  let modalClose = -1
  let m
  while ((m = tag.exec(jsx))) {
    if (m[0].startsWith('</div')) {
      depth -= 1
      if (depth === 0) {
        modalClose = m.index
        break
      }
    } else if (m[1] !== '/') {
      depth += 1
    }
  }
  assert.notEqual(modalClose, -1, 'the create-org `.modal` element must have a matching close')
  assert.ok(
    inputIdx < modalClose,
    `the organization-name input must be nested inside the .modal element (the shared rule only reaches .modal descendants); input at ${inputIdx}, .modal closes at ${modalClose}`,
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
