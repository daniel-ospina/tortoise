// dialogContainmentRemainderTripwire.test.js — #8670 static tripwire
// (`node --test src/*.test.js`, zero-dep, same convention as
// dialogContainmentTripwire.test.js).
//
// WHY THIS FILE EXISTS ALONGSIDE #2577's. `dialogFocus.test.js` proves the
// containment helpers are CORRECT and `dialogContainmentTripwire.test.js` pins
// the FOUR surfaces #2577/#2728 wired (create-team, reveal-key, ReauthDialog,
// Configured-packs). Neither can see the rest of the family: after #2577 landed,
// six more dialogs went on declaring `aria-modal="true"` with no Tab wrap and no
// inert background — the card said "modal" while the page behind it stayed
// reachable to Tab AND to a screen reader's virtual cursor. That is the exact
// defect #2577 closes, so this file pins the remainder:
//
//   captured-session delete   main.jsx      aria-label="Delete captured session"
//   key-create                main.jsx      aria-label="Create API key"
//   graph delete              main.jsx      aria-label="Delete graph"
//   integration coming-soon   main.jsx      aria-labelledby="integration-modal-title"
//   GitHub disconnect         githubDisconnect.js   'aria-label': 'Disconnect GitHub'
//   account deletion          accountDeletion.js    'aria-label': 'Delete account'
//
// Shape is pinned here; behaviour is pinned where it can be EXECUTED. The two
// extracted modules are `createElement` modules, so the last two tests drive
// their real `onKeyDown` through the real `containTabFocus` and assert on
// `.focus()` + `preventDefault()`, not on source text. main.jsx has no React
// runtime harness — its half is the source tripwire (the repo's established
// lane for that file, and the layer the issue's Verification Checklist names).
//
// ONE STRUCTURAL PIN IS LOAD-BEARING: the inert effect must be keyed on a flag
// that changes when the card UNMOUNTS, not merely when the id clears. The
// session-delete and graph-delete dialogs resolve their subject from the live
// list and render null once it is gone while the id stays set — an effect keyed
// on the id would leave the whole page `inert` with no dialog left to release
// it. That is a page-wide keyboard lockout, so it is pinned explicitly below.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { GithubDisconnectControl } from './githubDisconnect.js'
import { DeleteAccountSection } from './accountDeletion.js'

const HERE = dirname(fileURLToPath(import.meta.url))
const read = (f) => readFileSync(join(HERE, f), 'utf8')
const mainJsx = read('main.jsx')
const profileJsx = read('profile.jsx')
const githubJs = read('githubDisconnect.js')
const accountJs = read('accountDeletion.js')

/** The source window starting at `needle` (asserted present). */
function windowFrom(src, needle, after) {
  const i = src.indexOf(needle)
  assert.ok(i >= 0, `expected source to contain ${JSON.stringify(needle)}`)
  return src.slice(i, i + after)
}

/** Number of non-overlapping occurrences of `needle`. */
function count(src, needle) {
  return src.split(needle).length - 1
}

/** The full right-hand side of `const <name> = …` in `src`, multi-line safe.
 * Stops at the end of the INITIALIZER: it continues while brackets are open, or
 * the line ends on a continuation operator, or the NEXT line STARTS on one (the
 * repo's prettier style puts `&&` at the start of the wrapped line). Returns the
 * expression text so it can be EVALUATED, not merely matched. */
function constExpr(src, name) {
  const marker = `const ${name} = `
  const start = src.indexOf(marker)
  assert.ok(start >= 0, `expected main.jsx to declare \`${marker.trim()}\``)
  const lines = src.slice(start + marker.length).split('\n')
  const out = []
  let depth = 0
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i]
    out.push(line)
    for (const ch of line) {
      if ('([{'.includes(ch)) depth += 1
      else if (')]}'.includes(ch)) depth -= 1
    }
    const next = (lines[i + 1] ?? '').trim()
    const continues = depth > 0
      || /(?:&&|\|\||[?:+.,(])$/.test(line.trim())
      || /^(?:&&|\|\||\?|:|\.|\+)/.test(next)
    if (!continues) break
  }
  // Drop any trailing line comment so the expression stays evaluable.
  return out.join('\n').replace(/\/\/+[^\n]*$/gm, '').trim()
}

/** Evaluate one of main.jsx's derived flags with caller-supplied inputs.
 *
 * A SOURCE PIN CANNOT PROVE THIS. The pre-fix flags were `!!id && list.some(…)`
 * — text that satisfies any regex for “derived from the live list” while
 * ignoring the rest of the card's render gate (#8670 review P1). Running the
 * real initializer against the states that matter is the only check that goes
 * RED on that shape, so the page-lockout guard below drives it. */
function evalFlag(expr, names, args) {
  // An expression lifted from the repository's own source, never external input.
  // eslint-disable-next-line no-new-func
  const fn = new Function(...names, `return (${expr})`)
  return fn(...args)
}

/** The identifier a component's RENDER GATE uses in the JSX — the `{flag && (`
 * immediately in front of `<Component`. Returns the name so the guard can
 * evaluate the flag's REAL initializer; throws when the gate is an inline
 * expression (unliftable) or the component is not rendered at all (the #9170
 * P1 closed circle deleted both the gate and the opener, so "not found" is a
 * failure, never a skip). */
function renderGateIdent(src, component) {
  const re = new RegExp(`\\{\\s*([A-Za-z_$][\\w$]*)\\s*&&\\s*\\(\\s*<${component}\\b`)
  const m = re.exec(src)
  assert.ok(m, `expected ${component} to render behind a named gate \`{flag && <${component}\``)
  return m[1]
}

// Both spellings the dashboard uses: JSX `aria-modal="true"` and the
// createElement modules' `'aria-modal': 'true'`. Deliberately NOT the bare word —
// `dialogFocus.js` and this change's own comments discuss aria-modal in prose
// (one even writes `aria-modal=true` unquoted). A FRESH regex per use: a shared
// /g regex carries `lastIndex` between `test()` calls, which would make the
// census pass or fail depending on what ran before it.
const ARIA_MODAL_ATTR_SRC = 'aria-modal="true"|\'aria-modal\': \'true\''
const ariaModalRe = (flags = '') => new RegExp(ARIA_MODAL_ATTR_SRC, flags)

function ariaModalSurfaces() {
  const out = []
  for (const f of readdirSync(HERE)) {
    if (!/\.jsx?$/.test(f) || /\.test\.jsx?$/.test(f)) continue
    const src = read(f)
    for (const m of src.matchAll(ariaModalRe('g'))) out.push({ file: f, index: m.index, src })
  }
  return out
}

test('#8670: the tripwire extractors are not a no-op', () => {
  assert.equal(windowFrom('aa START middle END zz', 'START', 12), 'START middle')
  assert.equal(count('a a a', 'a'), 3)
  assert.equal(count('a', 'ZZ'), 0)
  assert.throws(() => windowFrom('nope', 'START', 5), /expected source to contain/)
  assert.equal(renderGateIdent('{gateX && (\n  <Thing prop={1} />', 'Thing'), 'gateX')
  assert.throws(() => renderGateIdent('{gateX && (<Other />', 'Thing'), /expected Thing to render behind a named gate/)
  assert.throws(() => renderGateIdent('{a && b && (<Thing />', 'Thing'), /expected Thing to render behind a named gate/)
  assert.match('role="dialog" aria-modal="true"', ariaModalRe())
  assert.match("h('div', { 'aria-modal': 'true' })", ariaModalRe())
  assert.doesNotMatch('the aria-modal family owes containment', ariaModalRe())
  assert.doesNotMatch('the dialogs declare aria-modal=true', ariaModalRe())
})

test('#8670: no new aria-modal surface escapes containment (family census)', () => {
  const surfaces = ariaModalSurfaces()
  // #2577's four (3 in main.jsx + ReauthDialog in profile.jsx) plus #8670's six.
  // A new dialog that declares modality lands here and must be wired + pinned.
  assert.equal(surfaces.length, 10,
    `expected 10 aria-modal surfaces (4 from #2577/#2728 + 6 from #8670), found ${surfaces.length}: `
    + surfaces.map((s) => `${s.file}@${s.index}`).join(', '))
  const files = [...new Set(surfaces.map((s) => s.file))].sort()
  assert.deepEqual(files, ['accountDeletion.js', 'githubDisconnect.js', 'main.jsx', 'profile.jsx'],
    'aria-modal must only be declared in the four files whose dialogs are pinned')
  // Every one of them must wrap Tab shortly after its own declaration — the
  // window is generous because the handler sits in the same opening tag, and the
  // per-dialog pins below assert the exact association.
  for (const { file, index, src } of surfaces) {
    const win = src.slice(index, index + 1600)
    assert.match(win, /containTabFocus\(e\.currentTarget/,
      `${file}@${index}: this aria-modal dialog does not wrap Tab — it announces modality without containing the keyboard`)
  }
})

test('#8670: each of the six remainder dialogs wraps Tab/Shift+Tab via containTabFocus', () => {
  // Anchored on each dialog's own identity, never on attribute adjacency.
  assert.match(windowFrom(mainJsx, 'aria-label="Delete captured session"', 700),
    /containTabFocus\(e\.currentTarget, \{ shiftKey: e\.shiftKey \}\)/,
    'captured-session delete: Tab containment')
  assert.match(windowFrom(mainJsx, 'aria-label="Create API key"', 1200),
    /containTabFocus\(e\.currentTarget, \{ shiftKey: e\.shiftKey \}\)/,
    'key-create: Tab containment')
  assert.match(windowFrom(mainJsx, 'aria-label="Delete graph"', 700),
    /containTabFocus\(e\.currentTarget, \{ shiftKey: e\.shiftKey \}\)/,
    'graph delete: Tab containment')
  assert.match(windowFrom(mainJsx, 'aria-labelledby="integration-modal-title"', 900),
    /containTabFocus\(e\.currentTarget, \{ shiftKey: e\.shiftKey \}\)/,
    'integration coming-soon: Tab containment')
  assert.match(windowFrom(githubJs, "'aria-label': 'Disconnect GitHub'", 1400),
    /containTabFocus\(e\.currentTarget, \{ shiftKey: e\.shiftKey \}\)/,
    'GitHub disconnect: Tab containment')
  assert.match(windowFrom(accountJs, "'aria-label': 'Delete account'", 1400),
    /containTabFocus\(e\.currentTarget, \{ shiftKey: e\.shiftKey \}\)/,
    'account deletion: Tab containment')
})

test('#8670: each of the six dialogs makes the background inert while open', () => {
  assert.match(mainJsx, /import \{[^}]*containTabFocus[^}]*applyBackgroundInert[^}]*\} from '\.\/dialogFocus\.js'/)
  // The four inline dialogs: one ref'd container + one effect each, keyed on a
  // value that flips when the card opens/closes.
  const inline = [
    ['captured-session delete', 'sessionDeleteDialogRef', 'applyBackgroundInert(el, document)', 'sessionDeleteArmed'],
    ['key-create', 'keyModalDialogRef', 'applyBackgroundInert(el, document)', 'keyModalOpen'],
    ['graph delete', 'graphDeleteDialogRef', 'applyBackgroundInert(el, document)', 'graphDeleteArmed'],
    ['integration coming-soon', 'comingSoonDialogRef', 'applyBackgroundInert(el, document)', 'comingSoonKey'],
  ]
  for (const [name, ref, call, dep] of inline) {
    assert.ok(mainJsx.includes(`ref={${ref}}`), `${name}: the dialog needs its own ref`)
    assert.match(windowFrom(mainJsx, `ref={${ref}}`, 80), /tabIndex=\{-1\}/,
      `${name}: the container must be focusable for the all-controls-disabled state`)
    const effect = windowFrom(mainJsx, `if (!${dep} || typeof document === 'undefined')`, 500)
    assert.match(effect, new RegExp(call.replace(/[().]/g, '\\$&')), `${name}: inert effect`)
    // …applied to THAT dialog's node. Without this, an effect that computed its
    // node from anywhere else (or a dead expression) still satisfies the shape.
    assert.match(effect, new RegExp(`const el = ${ref}\\.current`),
      `${name}: the effect must inert the dialog's own node (${ref})`)
    assert.match(effect, /return \(\) => \{[^}]*release\(\) \}/, `${name}: cleanup returned`)
    assert.ok(mainJsx.includes(`}, [${dep}])`), `${name}: effect keyed on ${dep}`)
  }
  // The two extracted modules stay hook-free (their guards call them as plain
  // functions — a hook there throws "Invalid hook call"), so the parent owns the
  // ref + effect and hands the node down. The key is a DERIVED `…Armed` flag,
  // not the bare open-bit: each card also sits behind a tab-conditional gate in
  // this parent's JSX, which unmounts the card without touching the open-bit
  // (see the page-lockout guard below).
  for (const [name, src, state, ref] of [
    ['GitHub disconnect', githubJs, 'githubDisconnectArmed', 'githubDisconnectDialogRef'],
    ['account deletion', accountJs, 'deleteAccountArmed', 'deleteAccountDialogRef'],
  ]) {
    assert.match(src, /ref: dialogRef/, `${name}: the module must accept the parent's dialog ref`)
    const effect = windowFrom(mainJsx, `if (!${state} || typeof document === 'undefined')`, 500)
    assert.match(effect, /applyBackgroundInert\(el, document\)/, `${name}: inert effect`)
    assert.match(effect, new RegExp(`const el = ${ref}\\.current`),
      `${name}: the effect must inert the dialog's own node (${ref})`)
    assert.match(effect, /return \(\) => \{[^}]*release\(\) \}/, `${name}: cleanup returned`)
    assert.ok(mainJsx.includes(`dialogRef={${ref}}`), `${name}: the parent must pass its ref down`)
    assert.ok(mainJsx.includes(`}, [${state}])`), `${name}: effect keyed on ${state}`)
  }
})

test('#8670: the inert effect cannot outlive its dialog (page-lockout guard)', () => {
  // The session-delete and graph-delete cards resolve their subject from the LIVE
  // list and return null once it is gone, while the id state stays set — so an
  // effect keyed on the id would never clean up and the page would stay `inert`
  // with no dialog to release it. The flag must be DERIVED from the live list.
  //
  // #8670 review P1 — “the live list” is only PART of the card's render gate.
  // Both cards also sit behind other conditions that can flip mid-dialog with the
  // id and the list untouched: the session card is the deepest arm of
  // `!sessionsLoading && state && sessionsOn && sessionsLoaded`, and the graph
  // card is gated on the owner/admin role AND on the Settings home's
  // `tab === 'graphs'` block. A flag blind to those does not change
  // on the transition that unmounts the card, so its effect neither re-runs nor
  // cleans up and the page keeps `inert` with no dialog left. The guard therefore
  // does two things: it pins that the flag IS the render gate (one value, so the
  // two cannot drift), and it EVALUATES the real initializer against every state
  // in that gate — the assertions the pre-fix expressions fail.
  assert.match(mainJsx, /\{sessionDeleteArmed && \(\(\) => \{/,
    'the session-delete card must RENDER from the same flag its inert effect is keyed on')
  assert.match(mainJsx, /\{graphDeleteArmed && \(\(\) => \{/,
    'the graph-delete card must RENDER from the same flag its inert effect is keyed on')

  // ── session delete: every condition of the render gate clears the flag ──
  const sessionFlag = constExpr(mainJsx, 'sessionDeleteArmed')
  const sessionArmed = (over = {}) => evalFlag(
    sessionFlag,
    ['confirmSessionId', 'sessions', 'sessionsOn', 'sessionsLoaded', 'sessionsLoading', 'state'],
    Object.entries({
      confirmSessionId: 's7',
      sessions: [{ id: 's7' }, { id: 's8' }],
      sessionsOn: true,
      sessionsLoaded: true,
      sessionsLoading: false,
      state: { session_recording: true },
      ...over,
    }).map(([, v]) => v))
  assert.equal(sessionArmed(), true, 'the literal: an armed dialog on a loaded, recording home')
  // The P1 itself: a failed background `loadAll` sets `sessionsLoaded = false`
  // and changes NEITHER `sessions` NOR `confirmSessionId` — the card unmounts.
  assert.equal(sessionArmed({ sessionsLoaded: false }), false,
    'a failed sessions reload unmounts the card — the flag must clear (page-lockout guard)')
  // A background `refreshOnboarding()` landing `session_recording: false`.
  assert.equal(sessionArmed({ sessionsOn: false }), false,
    'a recording flip unmounts the card — the flag must clear (page-lockout guard)')
  assert.equal(sessionArmed({ sessionsLoading: true }), false,
    'the home’s own loading arm does not render the card')
  assert.equal(sessionArmed({ state: null }), false,
    'the “couldn’t load capture status” arm does not render the card')
  // …and the shapes the flag already covered must stay covered.
  assert.equal(sessionArmed({ confirmSessionId: null }), false, 'no id, no dialog')
  assert.equal(sessionArmed({ sessions: [{ id: 's8' }] }), false,
    'a row that leaves the live list unmounts the card')

  // ── graph delete: the role and the OPEN TAB are part of the render gate too ──
  // The card is nested in the Settings home's `tab === 'graphs'` block, and that
  // tab moves without a click: the #2509 popstate/hashchange handler calls
  // `setTab` straight from browser Back / an address-bar edit, which `inert`
  // cannot block. So the tab belongs to the flag exactly as the role does.
  const graphsTabAt = mainJsx.indexOf("{tab === 'graphs' && (")
  const graphCardAt = mainJsx.indexOf('{graphDeleteArmed && (() => {')
  assert.ok(graphsTabAt >= 0 && graphCardAt > graphsTabAt,
    'the graph-delete card renders INSIDE the `tab === \'graphs\'` block — so the tab is part of its render gate')
  const graphFlag = constExpr(mainJsx, 'graphDeleteArmed')
  const graphArmed = (over = {}) => evalFlag(
    graphFlag,
    ['roleIsOwnerAdmin', 'confirmDeleteId', 'graphs', 'tab'],
    Object.entries({
      roleIsOwnerAdmin: () => true,
      confirmDeleteId: 'g1',
      graphs: [{ graph_id: 'g1' }],
      tab: 'graphs',
      ...over,
    }).map(([, v]) => v))
  assert.equal(graphArmed(), true, 'the literal: an armed dialog for an owner/admin on the Graphs tab')
  // A demotion / org switch re-derives the role and unmounts the card.
  assert.equal(graphArmed({ roleIsOwnerAdmin: () => false }), false,
    'a role change unmounts the card — the flag must clear (page-lockout guard)')
  // Browser Back / an address-bar hash edit while the dialog is open.
  assert.equal(graphArmed({ tab: 'overview' }), false,
    'leaving the Graphs tab unmounts the card — the flag must clear (page-lockout guard)')
  assert.equal(graphArmed({ confirmDeleteId: null }), false, 'no id, no dialog')
  assert.equal(graphArmed({ graphs: [{ graph_id: 'g2' }] }), false,
    'a row that leaves the live list unmounts the card')

  // ── the two MODULE dialogs: their gate lives in this parent's JSX ──
  // Same class, one step worse: neither card is rendered at the effect's own site
  // — `GithubDisconnectControl` is inside `<SettingsTab>` (mounted only under
  // `tab === 'settings' && team`) and `DeleteAccountSection` under
  // `tab === 'profile' && authMode === 'session' && identityInv &&
  // !identityInv.unsupported`. The effect's OWNER (this component) stays mounted
  // through every one of those flips, so a bare open-bit key leaks `inert`.
  // THE REGRESSION PIN first, EXECUTED: `DeleteAccountSection` owns BOTH the
  // `Delete account` opener and the dialog (`accountDeletion.js` renders
  // `open ? <backdrop> : null` after the button), so a render gate that includes
  // the open-bit is a closed circle — the only setter of `deleteAccountOpen =
  // true` is a prop of the component that would then not render. #9170 review P1:
  // that shipped in an earlier commit and the Profile tab lost its entire
  // delete-account home (heading, warning copy, the only opener) while the
  // dialog's inert effect could never run.
  //
  // The gate is read off the JSX rather than named here, so the pin survives a
  // rename and cannot be satisfied by a comment; it must be a `const` identifier
  // so its REAL initializer can be evaluated — that is the half a source regex
  // cannot decide.
  const accountGateName = renderGateIdent(mainJsx, 'DeleteAccountSection')
  assert.notEqual(accountGateName, 'deleteAccountArmed',
    'the card must not render from the open-bit-carrying effect flag')
  assert.doesNotMatch(mainJsx, /\{deleteAccountArmed && \(/,
    'the account-deletion card must render from its GATE, not from the effect flag')
  const accountGate = constExpr(mainJsx, accountGateName)
  const accountGateAt = (over = {}) => evalFlag(
    accountGate,
    ['tab', 'authMode', 'identityInv', 'deleteAccountOpen'],
    Object.entries({
      tab: 'profile',
      authMode: 'session',
      identityInv: { login_methods: 1, unsupported: false },
      deleteAccountOpen: false,
      ...over,
    }).map(([, v]) => v))
  // CLOSED is the state the opener must be reachable from. Pre-fix (the gate WAS
  // the armed flag) this evaluated to `false`.
  assert.equal(accountGateAt(), true,
    'the Delete account home (and its only opener) must render with the dialog CLOSED — a gate that reads the open-bit is a closed circle')
  assert.equal(accountGateAt({ deleteAccountOpen: true }), true,
    'the card stays mounted while its dialog is open')
  assert.equal(accountGateAt({ tab: 'overview' }), false,
    'the gate still excludes every other tab')
  assert.equal(accountGateAt({ authMode: 'apikey' }), false,
    'the gate still excludes the API-key auth mode')
  assert.equal(accountGateAt({ identityInv: null }), false,
    'the gate still follows an identity read landing null')
  assert.equal(accountGateAt({ identityInv: { unsupported: true } }), false,
    'the gate still excludes the unsupported-identity arm')

  // ── and the EFFECT's flag is the gate PLUS the open-bit ──
  // Its non-open terms are the gate itself (evaluated here from that same
  // initializer, so the two cannot drift), so a gate flip still clears the flag
  // and the effect cleans up (the page-lockout guard); the open-bit makes a
  // dismissal clear it as well.
  const accountFlag = constExpr(mainJsx, 'deleteAccountArmed')
  assert.match(accountFlag, new RegExp(`(?:^|\\W)${accountGateName}\\b`),
    'the inert flag must be derived from the render gate, not re-implement it')
  assert.match(accountFlag, /deleteAccountOpen/,
    'the inert flag must add the open-bit to the gate — otherwise the effect never re-runs on close')
  const accountArmed = (over = {}) => evalFlag(
    accountFlag,
    ['tab', 'authMode', 'identityInv', 'deleteAccountOpen', accountGateName],
    [...Object.values({
      tab: 'profile',
      authMode: 'session',
      identityInv: { login_methods: 1, unsupported: false },
      deleteAccountOpen: true,
      ...over,
    }), accountGateAt(over)])
  assert.equal(accountArmed(), true, 'the literal: an open Delete-account dialog on Profile')
  // The card UNMOUNTS if any gate condition flips — the flag must clear with it.
  assert.equal(accountArmed({ tab: 'overview' }), false,
    'leaving the Profile tab unmounts the card — the flag must clear (page-lockout guard)')
  // The MEASURED one: `fetchIdentity()` re-runs on window focus and fails CLOSED
  // to `setIdentityInv(null)` — no navigation, no click, dialog gone.
  assert.equal(accountArmed({ identityInv: null }), false,
    'an identity read landing null unmounts the card — the flag must clear (page-lockout guard)')
  assert.equal(accountArmed({ identityInv: { unsupported: true } }), false,
    'the unsupported-identity arm does not render the card')
  assert.equal(accountArmed({ authMode: 'apikey' }), false,
    'the API-key auth mode does not render the card')
  assert.equal(accountArmed({ deleteAccountOpen: false }), false, 'closed dialog')

  assert.match(mainJsx, /\{tab === 'settings' && team && \(/,
    'GithubDisconnectControl renders under the Settings tab gate — that gate belongs to its flag')
  const githubFlag = constExpr(mainJsx, 'githubDisconnectArmed')
  const githubArmed = (over = {}) => evalFlag(
    githubFlag,
    ['tab', 'team', 'githubDisconnect'],
    Object.entries({
      tab: 'settings',
      team: { org_id: 'team_e2e' },
      githubDisconnect: { open: true, busy: false, error: '', result: null },
      ...over,
    }).map(([, v]) => v))
  assert.equal(githubArmed(), true, 'the literal: an open disconnect dialog on the Settings tab')
  assert.equal(githubArmed({ tab: 'overview' }), false,
    'leaving the Settings tab unmounts the card — the flag must clear (page-lockout guard)')
  assert.equal(githubArmed({ team: null }), false,
    'an org transition unmounts SettingsTab — the flag must clear (page-lockout guard)')
  assert.equal(githubArmed({ githubDisconnect: { open: false } }), false, 'closed dialog')

  // The predicates the flags are built from must be the LIVE ones: a flag that
  // re-implemented the role test locally would pass the scenarios above while
  // the render gate used a different expression.
  assert.match(mainJsx, /const isOwnerAdmin = roleIsOwnerAdmin\(\)/,
    'the render gate and the inert flag must read ONE role predicate')
  assert.match(mainJsx, /function roleIsOwnerAdmin\(\) \{[^}]*t\.role === 'owner' \|\| t\.role === 'admin'/,
    'the shared role predicate must stay the owner/admin test')

  assert.doesNotMatch(mainJsx, /if \(!confirmSessionId \|\| typeof document === 'undefined'\)/,
    'keying the inert effect on the ID would leak `inert` when the row leaves the list')
  assert.doesNotMatch(mainJsx, /if \(!confirmDeleteId \|\| typeof document === 'undefined'\)/,
    'keying the inert effect on the ID would leak `inert` when the graph leaves the list')
  // the modal really does return null on a vanished subject (the reason above)
  assert.ok(mainJsx.includes('if (!ds) return null'), 'session-delete renders null once its session is gone')
  assert.ok(mainJsx.includes('if (!dg) return null'), 'graph-delete renders null once its graph is gone')
})

test('#8670: every close path releases the inert BEFORE it restores focus', () => {
  // An element inside an inert subtree is not focusable, so a restore that runs
  // first silently no-ops (#2577) - the failure mode is invisible in jsdom, which
  // does not model `inert`, so the ORDER is pinned in source. Each window starts
  // at its own close helper and reaches its own restore (`InertRef.current()` is
  // matched inside the named ref).
  for (const [name, re] of [
    ['captured-session delete',
      /const sessionDeleteClose = \(\) => \{[\s\S]{0,400}?[Ii]nertRef\.current\(\)[\s\S]{0,300}?restoreFocus\(sessionDeleteRestoreRef\)/],
    ['key-create',
      /function dismissKeyModal\(\) \{[\s\S]{0,900}?[Ii]nertRef\.current\(\)[\s\S]{0,300}?restoreFocus\(keyModalRestoreRef\)/],
    ['graph delete',
      /const graphDeleteClose = \(\) => \{[\s\S]{0,400}?[Ii]nertRef\.current\(\)[\s\S]{0,300}?restoreFocus\(graphDeleteRestoreRef\)/],
    ['GitHub disconnect',
      /function closeGithubDisconnect\(\) \{[\s\S]{0,1600}?[Ii]nertRef\.current\(\)[\s\S]{0,300}?restoreFocus\(githubDisconnectRestoreRef\)/],
  ]) {
    assert.match(mainJsx, re,
      `${name}: the inert background must be released before the focus restore`)
  }
  // The three whose close helpers are arrow functions (not declarations).
  assert.match(mainJsx,
    /const closeComingSoon = \(\) => \{[\s\S]{0,600}?comingSoonInertRef\.current\(\)[\s\S]{0,300}?requestAnimationFrame\(\(\) => restoreFocus\(comingSoonRestoreRef\)\)/,
    'integration coming-soon: release before the deferred restore')
  assert.match(mainJsx,
    /onCancel=\{\(\) => \{[\s\S]{0,700}?deleteAccountInertRef\.current\(\)[\s\S]{0,200}?restoreFocus\(deleteAccountRestoreRef\)/,
    'account deletion: release before the restore')
  // and the restore half actually exists on each (#2392 minimal set): the three
  // inline dialogs had none, so the containment would otherwise drop focus on
  // <body> when the card unmounts.
  for (const [name, needle] of [
    ['captured-session delete', 'rememberFocusedTrigger(sessionDeleteRestoreRef)'],
    ['key-create', 'rememberFocusedTrigger(keyModalRestoreRef)'],
    ['graph delete', 'rememberFocusedTrigger(graphDeleteRestoreRef)'],
  ]) {
    assert.ok(mainJsx.includes(needle), `${name}: the opening gesture must capture the restore target`)
  }
})

test('#8670: existing Escape / busy wiring is preserved on every wired dialog', () => {
  // The Escape guards that already existed must survive the containment edit —
  // a busy surface must never be dismissable mid-flight.
  assert.match(windowFrom(mainJsx, 'aria-label="Delete captured session"', 2600),
    /e\.key === 'Escape' && !sessionBusy/, 'captured-session delete: Escape stays busy-guarded')
  assert.match(windowFrom(mainJsx, 'aria-label="Delete graph"', 2600),
    /e\.key === 'Escape' && !graphBusy/, 'graph delete: Escape stays busy-guarded')
  assert.match(windowFrom(mainJsx, 'aria-label="Create API key"', 1200),
    /e\.key === 'Escape' && !keyModalBusy/, 'key-create: Escape is busy-guarded')
  assert.match(windowFrom(githubJs, "'aria-label': 'Disconnect GitHub'", 1400),
    /e\.key === 'Escape' && !busy/, 'GitHub disconnect: Escape stays busy-guarded')
  assert.match(windowFrom(accountJs, "'aria-label': 'Delete account'", 1400),
    /e\.key === 'Escape' && !busy/, 'account deletion: Escape stays busy-guarded')
  assert.match(windowFrom(mainJsx, 'aria-labelledby="integration-modal-title"', 900),
    /e\.key === 'Escape'/, 'integration coming-soon: Escape handler preserved')
  // …and the modals still announce themselves as modal: an inert-trapped dialog
  // that stopped claiming modality would be a silent a11y regression.
  assert.equal(count(mainJsx, 'aria-modal="true"'), 7)
  assert.equal(count(profileJsx, 'aria-modal="true"'), 1)
})

// ── EXECUTED behaviour for the two extracted dialogs ──────────────────────
// Their guards render them as plain functions (no jsdom), but containment is pure
// enough to drive: `containTabFocus` only reads `querySelectorAll`, `focus()`,
// `hasAttribute`/`getAttribute` and `parentElement`, so fakes exercise the REAL
// wiring — including that the handler consumes the key (preventDefault), which is
// the difference between "wraps" and "moves focus and also lets the browser move
// it out again".
function fakeControl(name) {
  return {
    name,
    focused: false,
    disabled: false,
    parentElement: null,
    focus() { this.focused = true },
    hasAttribute: () => false,
    getAttribute: () => null,
  }
}

function driveTab(componentFn, props, { cycle = false } = {}) {
  const tree = componentFn(props)
  const dialog = findDialog(tree)
  assert.ok(dialog, 'the dialog element must render while open')
  assert.equal(dialog.props.tabIndex, -1, 'the container must be focusable (tabIndex -1)')
  const first = fakeControl('first')
  const last = fakeControl('last')
  const container = {
    querySelectorAll: () => (cycle ? [first, last] : []),
    ownerDocument: { activeElement: cycle ? last : null },
    focus() { this.focused = true },
  }
  let prevented = 0
  dialog.props.onKeyDown({
    key: 'Tab', shiftKey: false, currentTarget: container,
    preventDefault: () => { prevented += 1 },
  })
  return { prevented, first, last, container, dialog }
}

function findDialog(node, acc) {
  if (node === null || node === undefined || typeof node !== 'object') return acc || null
  if (Array.isArray(node)) {
    for (const child of node) {
      const hit = findDialog(child)
      if (hit) return hit
    }
    return null
  }
  if (!('props' in node)) return null
  if (node.type === 'div' && node.props && node.props.role === 'dialog') return node
  return findDialog(node.props && node.props.children)
}

test('#8670: the GitHub disconnect dialog keeps Tab inside (executed)', () => {
  const props = {
    connected: true, open: true, busy: false, error: '', result: null,
    onOpen() {}, onClose() {}, onConfirm() {},
  }
  // last control focused → the wrap must move focus to the FIRST and consume the key
  const cycled = driveTab(GithubDisconnectControl, props, { cycle: true })
  assert.equal(cycled.first.focused, true, 'Tab off the last control must land on the first')
  assert.equal(cycled.prevented, 1, 'the handler must preventDefault so the browser does not also move out')
  // no focusable control left (all disabled while busy) → keep focus on the container
  const parked = driveTab(GithubDisconnectControl, { ...props, busy: true })
  assert.equal(parked.container.focused, true, 'with no focusable control the container holds focus')
  assert.equal(parked.prevented, 1, 'the container case must consume the key too')
})

test('#8670: the account-deletion dialog keeps Tab inside (executed)', () => {
  const props = {
    open: true, busy: false, error: '', result: null,
    onOpen() {}, onCancel() {}, onConfirm() {}, onDone() {},
  }
  const cycled = driveTab(DeleteAccountSection, props, { cycle: true })
  assert.equal(cycled.first.focused, true, 'Tab off the last control must land on the first')
  assert.equal(cycled.prevented, 1, 'the handler must preventDefault so the browser does not also move out')
  const parked = driveTab(DeleteAccountSection, { ...props, busy: true })
  assert.equal(parked.container.focused, true, 'with no focusable control the container holds focus')
  assert.equal(parked.prevented, 1, 'the container case must consume the key too')
  // a non-Tab key must NOT be swallowed (Escape has its own busy-guarded branch)
  let prevented = 0
  cycled.dialog.props.onKeyDown({
    key: 'Enter', shiftKey: false, currentTarget: cycled.container,
    preventDefault: () => { prevented += 1 },
  })
  assert.equal(prevented, 0, 'only Tab is consumed by the containment branch')
})
