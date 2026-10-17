// dialogContainmentTripwire.test.js — #2577 static tripwire (node --test,
// zero-dep, same convention as the other main.jsx/index.css tripwires).
//
// WHY A SOURCE PIN AND NOT ONLY THE UNIT TESTS. dialogFocus.test.js proves the
// containment helpers are CORRECT; it cannot prove any dialog CALLS them. A
// helper that is never wired is a no-op, and the regression it hides is exactly
// what #2577 closes: three aria-modal dialogs that announce themselves as modal
// while their background stays reachable to Tab and to a screen reader's virtual
// cursor. This file pins the three call sites in the app shell.
//
// It pins SHAPE, not behaviour: the real DOM behaviour of containment + the
// `inert` attribute lives in dialogContainmentDom.test.jsx (the vitest/jsdom
// lane), and the pure logic in dialogFocus.test.js. The extractor helpers are
// self-tested so this tripwire cannot silently rot into a no-op.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')
const profileJsx = readFileSync(join(here, 'profile.jsx'), 'utf8')
const focusJs = readFileSync(join(here, 'dialogFocus.js'), 'utf8')

/** The source window starting at `needle` (asserted present). */
function windowFrom(src, needle, after) {
  const i = src.indexOf(needle)
  assert.ok(i >= 0, `expected source to contain ${JSON.stringify(needle)}`)
  return src.slice(i, i + after)
}

/** The source window ending just after `needle` (asserted present). */
function windowBefore(src, needle, before) {
  const i = src.indexOf(needle)
  assert.ok(i >= 0, `expected source to contain ${JSON.stringify(needle)}`)
  return src.slice(Math.max(0, i - before), i + needle.length)
}

/** Number of non-overlapping occurrences of `needle`. */
function count(src, needle) {
  return src.split(needle).length - 1
}

test('#2577: the tripwire extractor is not a no-op', () => {
  const src = 'aa START middle END zz'
  assert.equal(windowFrom(src, 'START', 12), 'START middle')
  assert.equal(count(src, 'START'), 1)
  assert.equal(count(src, 'MISSING'), 0)
  assert.throws(() => windowFrom(src, 'NOPE', 5), /expected source to contain/)
})

test('#2577: dialogFocus exports the containment helpers', () => {
  assert.match(focusJs, /export const FOCUSABLE_SELECTOR/)
  assert.match(focusJs, /export function focusableWithin\(/)
  assert.match(focusJs, /export function containTabFocus\(/)
  assert.match(focusJs, /export function applyBackgroundInert\(/)
})

test('#2577: all three dialogs wrap Tab/Shift+Tab via containTabFocus', () => {
  assert.equal(count(mainJsx, 'containTabFocus(e.currentTarget'), 2,
    'create-team + reveal-key must each call containTabFocus on Tab')
  // anchored on each dialog's own identity, not on attribute adjacency: a new
  // attribute must not be able to red this wiring pin
  assert.ok(mainJsx.includes('ref={createTeamDialogRef}'), 'create-team inert ref present')
  assert.match(windowFrom(mainJsx, 'ref={createTeamDialogRef}', 80), /tabIndex=\{-1\}/,
    'create-team: the dialog container can hold focus when every control is disabled')
  assert.ok(mainJsx.includes('ref={revealKeyDialogRef}'), 'reveal-key inert ref present')
  assert.match(windowFrom(mainJsx, 'ref={revealKeyDialogRef}', 80), /tabIndex=\{-1\}/,
    'reveal-key: the dialog container can hold focus when every control is disabled')
  assert.match(
    windowFrom(mainJsx, "aria-labelledby={createTeamMode", 1200),
    /containTabFocus\(e\.currentTarget/,
    'create-team dialog: Tab containment')
  assert.match(
    windowFrom(mainJsx, 'aria-label="New key — shown once"', 700),
    /containTabFocus\(e\.currentTarget/,
    'reveal-key dialog: Tab containment')
  assert.equal(count(profileJsx, 'containTabFocus(e.currentTarget'), 1)
  assert.ok(profileJsx.includes('ref={dialogRef}'), 'ReauthDialog inert ref present')
  assert.match(windowFrom(profileJsx, 'ref={dialogRef}', 80), /tabIndex=\{-1\}/,
    'ReauthDialog: the dialog container can hold focus when every control is disabled')
  assert.match(
    windowFrom(profileJsx, 'aria-label="Confirm it\'s you"', 700),
    /containTabFocus\(e\.currentTarget/,
    'ReauthDialog: Tab containment')
})

test('#2577: all three dialogs make the background inert while open, cleanup-guarded', () => {
  assert.equal(count(mainJsx, 'applyBackgroundInert(el, document)'), 2)
  assert.equal(count(profileJsx, 'applyBackgroundInert(el, document)'), 1)
  // each effect is guarded on a real document (node/SSR) and returns a
  // cleanup — a missing cleanup leaves the page inert forever
  for (const [name, src, open] of [
    ['create-team', mainJsx, 'createTeamOpen'],
    ['reveal-key', mainJsx, 'revealKey'],
  ]) {
    const effect = windowFrom(src, `if (!${open} || typeof document === 'undefined')`, 460)
    assert.match(effect, /applyBackgroundInert\(el, document\)/, `${name}: inert effect`)
    assert.match(effect, /return \(\) => \{[^}]*release\(\) \}/, `${name}: cleanup returned`)
  }
  // the close paths release inert BEFORE restoreFocus — an inert element is
  // not focusable, so a synchronous restore would otherwise silently no-op
  assert.match(mainJsx, /function closeCreateTeam\(\) \{[^}]*createTeamInertRef\.current\(\)[^}]*restoreFocus\(createTeamRestoreRef\)/,
    'create-team: inert released before focus restore')
  assert.match(mainJsx, /function closeRevealKey\(\) \{[^}]*revealKeyInertRef\.current\(\)[^}]*restoreFocus\(revealRestoreRef\)/,
    'reveal-key: inert released before focus restore')
  const reauth = windowFrom(profileJsx, 'applyBackgroundInert(el, document)', 200)
  assert.match(reauth, /\}, \[open\]\)/, 'ReauthDialog: effect keyed on open')
  assert.match(
    windowFrom(profileJsx, 'if (!open || typeof document === \'undefined\')', 400),
    /return applyBackgroundInert\(el, document\)/,
    'ReauthDialog: cleanup returned')
  // ReauthDialog's onClose already defers restore with rAF (closeReauth), so
  // its focus restore lands after this cleanup — pinned where it is defined.
  assert.match(mainJsx, /requestAnimationFrame\(\(\) => restoreFocus\(reauthRestoreRef\)\)/,
    'ReauthDialog: restore stays deferred past the inert cleanup')
})

test('#2577: #2392 focus-restore semantics are preserved', () => {
  // the minimal set must still be there: capture + restore at the close paths
  assert.match(mainJsx, /import \{[^}]*restoreFocus[^}]*\} from '\.\/dialogFocus\.js'/)
  assert.match(mainJsx, /function closeCreateTeam\(\)/)
  assert.match(mainJsx, /function closeRevealKey\(\)/)
  assert.match(mainJsx, /restoreFocus\(createTeamRestoreRef\)/)
  assert.match(mainJsx, /restoreFocus\(revealRestoreRef\)/)
  // and the dialogs are still declared modal — an inert-trapped dialog must
  // keep announcing itself as one (a11y-relevant, not cosmetic)
  assert.ok(count(mainJsx, 'aria-modal="true"') >= 3,
    'the aria-modal dialogs are still declared modal')
  assert.match(windowBefore(mainJsx, 'aria-labelledby={createTeamMode', 160), /aria-modal="true"/)
  assert.match(windowBefore(profileJsx, 'aria-label="Confirm it\'s you"', 140), /aria-modal="true"/)
})
