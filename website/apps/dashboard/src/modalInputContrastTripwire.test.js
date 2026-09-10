// modalInputContrastTripwire.test.js — #2778 static tripwires (CI-run via
// dashboard-js-tests, node --test zero-dep convention, mirroring the other
// main.jsx/index.css tripwires). The create-organization dialog's name
// <input> shipped with NO class and NO inline style; the global
// `input, textarea, select` rule sets only color/caret-color, so the field
// fell back to the UA white background under the dark theme's light text —
// measured 1.48:1 (WCAG AA needs 4.5:1). The greps below pin the fix's
// load-bearing shapes so an unstyled dialog field can never ship again:
//   1. a shared `.modal input/textarea/select` rule that sets a dark
//      background (the field the bug fell through);
//   2. the create-org name input actually living inside that `.modal`, so
//      the shared rule covers it;
//   3. the destructive `.delete-confirm-input` still out-specifying the
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

test('#2778: a shared .modal input/textarea/select rule themes dialog fields', () => {
  const rule = cssFlat.match(/\.modal input, \.modal textarea, \.modal select \{([^}]*)\}/)
  assert.ok(
    rule,
    'index.css must keep a shared `.modal input, .modal textarea, .modal select` rule — the create-org dialog rendered a bare <input> that relied on it',
  )
  const decls = rule[1]
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
  assert.match(decls, /border: 1px solid var\(--border/, 'the dialog-field default must set a themed `border`')
})

test('#2778: the organization-name input lives inside the create-org .modal', () => {
  const modalAnchor = mainJsx.indexOf('aria-label="Create a new organization"')
  assert.notEqual(modalAnchor, -1, 'the create-organization dialog must still exist')
  // Proximity-scoped: the org-name field must render within this dialog's
  // block, not somewhere the shared `.modal input` rule cannot reach.
  const modalSlice = mainJsx.slice(modalAnchor, modalAnchor + 2500)
  assert.match(
    modalSlice,
    /<input[\s\S]{0,400}aria-label="Organization name"/,
    'the organization-name input must render inside the create-org .modal (the shared rule is scoped to .modal descendants)',
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
