// wizardPrompts.test.js — the #4365 onboarding-copy gate.
//
// WHY THIS FILE EXISTS. The onboarding copy gate used to read main.jsx as SOURCE
// TEXT, because main.jsx is JSX and `node --test` cannot import it. A guard that
// decides from the SHAPE of the source is defeated by any source of a different
// shape, and that mechanism produced five distinct false greens (#4365 review,
// cycle 11): a whole live prompt body that was never scanned at all, a shipped
// set that was only ever checked for PRESENCE, and a shared declaration matched
// after stripping the very interpolation that could hide a second host.
//
// The builders now live in wizardPrompts.js — no JSX, no side effects — so every
// assertion below reads the RENDERED string the agent actually receives.
// Two layers:
//   1. an exact rendered snapshot (wizardPrompts.snapshot.json), so any copy
//      change is a reviewed diff;
//   2. invariants over every rendered surface, so a divergence FAILS rather
//      than merely being visible.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import {
  ONBOARDING_INSTRUCTIONS,
  wizardPromptText,
  wizardWorkflowsText,
} from './wizardPrompts.js'
import { SKILLS_INSTALL_URL, SKILLS_LIST, UNIVERSAL_COMMAND } from './harnesses.js'

const here = dirname(fileURLToPath(import.meta.url))
const snapshot = JSON.parse(readFileSync(join(here, 'wizardPrompts.snapshot.json'), 'utf8'))

// Harnesses with a live wizard surface (chatgpt renders on none — #2698).
const HARNESSES = ['pi', 'cursor', 'claude', 'codex', 'claude-desktop', 'claude-web']
// Every harness the universal command covers, including the teach-human trio.
const COMMAND_HARNESSES = Object.keys(UNIVERSAL_COMMAND)
const KEY = 'tk_snapshot'
const ONBOARDING_URL = 'https://app.premiselabs.co/skills/tortoise-onboarding/SKILL.md'

// The surfaces that MUST render, pinned by HAND — never derived from the renders
// themselves, because a deleted prompt would then simply drop out of the
// enumeration and pass (mutation-verified: deleting pi's step-2 return).
const REQUIRED_SURFACES = [
  'pi/1', 'pi/2',
  'cursor/1', 'cursor/2',
  'claude/1', 'claude/2',
  'codex/1', 'codex/2',
  'claude-desktop/2', 'claude-web/2',
]

// The ONLY hosts the agent-facing copy may send an agent to, pinned so that
// introducing a new one is a reviewed change. `claude.ai` and `chatgpt.com` are
// the connector leaves' own setup pages.
const APPROVED_HOSTS = new Set([
  'api.premiselabs.co',
  'app.premiselabs.co',
  'tortoise.premiselabs.co',
  'claude.ai',
  'chatgpt.com',
])

// ── 1. The rendered snapshot ───────────────────────────────────────────────
// Regenerate with: node scripts/gen-wizard-prompts-snapshot.mjs
test('#4880: the rendered wizard copy matches the committed snapshot', () => {
  const rendered = {}
  for (const harness of HARNESSES) {
    for (const step of [1, 2]) {
      const text = wizardPromptText(harness, step, KEY, 'included')
      if (text) rendered[`${harness}/${step}`] = text
    }
  }
  assert.deepEqual(rendered, snapshot.prompts,
    'the rendered wizard prompts drifted from src/wizardPrompts.snapshot.json — '
    + 'if the copy change is intended, regenerate the snapshot and have it reviewed')
  assert.equal(snapshot.workflows, wizardWorkflowsText(KEY, 'included'),
    'the rendered workflows prompt drifted from the snapshot')
  const renderedCommands = {}
  for (const harness of COMMAND_HARNESSES) renderedCommands[harness] = UNIVERSAL_COMMAND[harness](KEY)
  assert.deepEqual(snapshot.commands, renderedCommands,
    'the rendered universal commands drifted from the snapshot')
  assert.equal(snapshot.onboardingInstructions, ONBOARDING_INSTRUCTIONS,
    'the shared onboarding sentence drifted from the snapshot')
  assert.equal(snapshot.skillSet, SKILLS_LIST, 'the shipped set drifted from the snapshot')
})

test('#4880: every required wizard surface still renders, and the snapshot covers exactly them', () => {
  for (const id of REQUIRED_SURFACES) {
    const [harness, step] = id.split('/')
    assert.ok(wizardPromptText(harness, Number(step), KEY, 'included'),
      `${id}: this surface no longer renders a prompt — a deleted surface must fail, not vanish`)
  }
  assert.deepEqual(Object.keys(snapshot.prompts).sort(), [...REQUIRED_SURFACES].sort(),
    'the snapshot must cover exactly the required surfaces')
})

// ── the subjects of every invariant below ─────────────────────────────────
// The whole point of the cycle-11 fix: this list is derived from the RENDER, so
// a surface cannot be silently left out the way `step2Text` was.
function allRenderedSurfaces() {
  const surfaces = []
  for (const harness of HARNESSES) {
    for (const step of [1, 2]) {
      for (const mode of ['included', 'deferred']) {
        const text = wizardPromptText(harness, step, KEY, mode)
        if (text) surfaces.push({ id: `${harness}/${step}/${mode}`, text })
      }
    }
  }
  surfaces.push({ id: 'workflows', text: wizardWorkflowsText(KEY, 'included') })
  for (const harness of COMMAND_HARNESSES) {
    surfaces.push({ id: `UNIVERSAL_COMMAND.${harness}`, text: UNIVERSAL_COMMAND[harness](KEY) })
  }
  return surfaces
}

// Every occurrence of "install the Tortoise skills", with what FOLLOWS the set.
function setStatements(text) {
  const out = []
  const re = /install the Tortoise skills/gi
  let m
  while ((m = re.exec(text)) !== null) {
    out.push({ at: m.index, tail: text.slice(m.index + m[0].length) })
  }
  return out
}

const EXPECTED_SET = ` (${SKILLS_LIST})`

// Parentheticals that are legitimately NOT the shipped set: the Cursor command
// tells the user to run the installer in a terminal instead of enumerating the
// skills (its skills ride the steps). Exact-matched rather than pattern-matched,
// so a new "hint" that is really a skill name still fails.
const NON_SET_HINTS = new Set(['run in a terminal'])

// What may legitimately follow the set's `)`. ANCHORED, because a bare
// "any whitespace" probe cannot tell the legitimate ` from <installer URL>.`
// from a hostile ` plus agent-memory:` — the space-separated variant of the
// #4365 defect class slipped through an earlier revision of this gate.
const TAIL_FORMS = [':\n', ` from ${SKILLS_INSTALL_URL}.`]

// Install-family verb that is NOT negated in its own clause. Clause-scoped on
// purpose: "Onboarding is not a skill — install it" carries `not` in an EARLIER
// clause and must still be caught, while "Onboarding is not installed" must not.
const INSTALL_VERB = /\b\w*install\w*\b/i
const NEGATION = /\b(not|never|no|without|isn't|aren't)\b/i
const CLAUSE_BREAK = /[—;.,:!?()\-]/
function unnegatedInstall(line) {
  const m = INSTALL_VERB.exec(line)
  if (!m) return null
  const clause = line.slice(0, m.index).split(CLAUSE_BREAK).pop() ?? ''
  return NEGATION.test(clause) ? null : m[0]
}

test('#4365: every rendered shipped-set statement is EXACTLY the shipped set', () => {
  // The old guard compared only the text INSIDE the parentheses, so a superset
  // appended after `)` was invisible in EITHER form (`, plus X` and ` plus X`).
  let seen = 0
  for (const { id, text } of allRenderedSurfaces()) {
    const stmts = setStatements(text)
    assert.ok(stmts.length <= 1,
      `${id}: at most one shipped-set statement per surface (found ${stmts.length})`)
    for (const { tail } of stmts) {
      const close = tail.indexOf(')')
      assert.ok(close > -1, `${id}: the set statement has no closing paren: ${JSON.stringify(tail.slice(0, 60))}`)
      const whole = tail.slice(0, close + 1)
      if (whole === EXPECTED_SET) {
        seen += 1
      } else {
        assert.ok(NON_SET_HINTS.has(whole.slice(2, -1)),
          `${id}: the set statement must enumerate exactly "${SKILLS_LIST}" — got `
          + `${JSON.stringify(tail.slice(0, 60))}`)
      }
      const after = tail.slice(close + 1)
      assert.ok(TAIL_FORMS.some((form) => after.startsWith(form)),
        `${id}: only ${JSON.stringify(TAIL_FORMS)} may follow the set statement — `
        + `got ${JSON.stringify(after.slice(0, 40))}`)
    }
  }
  assert.ok(seen > 0, 'precondition: the rendered surfaces carry set statements')
  // The four config-writing prompts each state the set exactly once.
  for (const harness of ['pi', 'cursor', 'claude', 'codex']) {
    const stmts = setStatements(wizardPromptText(harness, 1, KEY, 'included'))
    assert.equal(stmts.length, 1, `${harness}: the step-1 prompt states the shipped set exactly once`)
    assert.equal(stmts[0].tail.slice(0, EXPECTED_SET.length), EXPECTED_SET,
      `${harness}: the step-1 prompt enumerates the shipped set`)
  }
})

test('#4365: no rendered surface presents onboarding as an installed skill', () => {
  // Cycle 11 found the onboarding declaration was matched AFTER its `${…}`
  // interpolation was stripped, so an interpolated second host on the same line
  // was invisible. Read the rendered line instead, and require any install verb
  // on an onboarding line to be negated in its own clause.
  for (const { id, text } of allRenderedSurfaces()) {
    for (const line of text.split('\n')) {
      if (!/onboarding/i.test(line)) continue
      const verb = unnegatedInstall(line)
      assert.equal(verb, null,
        `${id}: onboarding is presented as an installable — the install verb `
        + `${JSON.stringify(verb)} is not negated in its own clause: ${JSON.stringify(line)}`)
    }
  }
})

test('#4365: no rendered surface names a tortoise-shaped skill outside the set', () => {
  // Cycle 11: a non-tortoise-shaped extra name (e.g. `agent.memory`) escaped a
  // set-shaped check; this is the complementary rule.
  const ALLOWED = new Set([
    'tortoise',                 // the MCP server name, and the product
    'tortoise-onboarding',      // the instructions document
    'tortoise_health',
    'tortoise_create_point',
    'tortoise_api_key',
    'your-tortoise-api-key',    // the Cursor command's placeholder key
    'how-to-use-tortoise',
    'tortoise-decide',
    'tortoise-file-finding',
    'install-tortoise-skills',  // the installer script
  ])
  for (const { id, text } of allRenderedSurfaces()) {
    const tokens = text.match(/\b[a-z0-9_-]*tortoise[a-z0-9_-]*\b/gi) ?? []
    for (const raw of tokens) {
      // `tortoise_*` names the MCP tool FAMILY (a wildcard), not a skill — the
      // trailing separator is stripped before the check, so a real name like
      // `tortoise_rebuild` is still caught.
      const token = raw.toLowerCase().replace(/[_-]+$/, '')
      assert.ok(ALLOWED.has(token),
        `${id}: names a skill the shipped set does not contain: ${JSON.stringify(raw)}`)
    }
  }
})

test('#4365: every endpoint on every rendered surface is an approved host', () => {
  // Uniform over ALL surfaces and ALL lines — no "onboarding line" filter. An
  // earlier revision gated this on /onboarding/i, so a hostile host substituted
  // into the step-2 verify prompt's OWN `Docs:` line (a live surface for four
  // harnesses) was never observed.
  //
  // Two unambiguous classes, because a bare dotted token is otherwise
  // indistinguishable from a filename or a decimal: the enumeration here really
  // does contain `SKILL.md`, `config.toml`, `0.10` and `servers.tortoise`.
  const URL_HOST = /(?:https?:\/\/|\/\/)([a-z0-9.-]+)/gi
  // A bare domain is only treated as one with THREE or more labels — a 2-label
  // token is genuinely ambiguous (`SKILL.md`, `claude.ai`, `evil.com`).
  const BARE_DOMAIN = /(?<![\w.-])((?:[a-z0-9-]+\.){2,}[a-z]{2,})(?![\w-])/gi
  for (const { id, text } of allRenderedSurfaces()) {
    for (const m of text.matchAll(URL_HOST)) {
      assert.ok(APPROVED_HOSTS.has(m[1].toLowerCase()),
        `${id}: a URL points at a non-approved host ${JSON.stringify(m[0])}`)
    }
    for (const m of text.matchAll(BARE_DOMAIN)) {
      assert.ok(APPROVED_HOSTS.has(m[1].toLowerCase()),
        `${id}: a bare domain does not match an approved host ${JSON.stringify(m[1])}`)
    }
  }
  // …and the reach invariant: an onboarding surface names the document itself.
  for (const { id, text } of allRenderedSurfaces()) {
    const mentions = text.split('\n').filter((line) => /onboarding/i.test(line)).length
    if (mentions > 0) {
      assert.ok(text.includes(ONBOARDING_URL),
        `${id}: an onboarding surface must name the approved instructions URL`)
    }
  }
})

test('#4365: onboarding is delivered on every live surface, as instructions', () => {
  // The reach invariant: the four config-writing prompts carry it inline; the
  // two connector leaves (no installer, no skills directory) get it in the
  // workflows prompt — their only delivery surface.
  for (const harness of ['pi', 'cursor', 'claude', 'codex']) {
    assert.ok(wizardPromptText(harness, 1, KEY, 'included').includes(ONBOARDING_INSTRUCTIONS),
      `${harness}: the step-1 prompt must carry the onboarding instructions`)
  }
  assert.ok(wizardWorkflowsText(KEY, 'included').includes(ONBOARDING_INSTRUCTIONS),
    'the workflows prompt (the connector leaves\' only surface) must carry the instructions')
  assert.ok(ONBOARDING_INSTRUCTIONS.includes(ONBOARDING_URL),
    'the shared sentence names the served instructions document')
  // The document itself must never be named as an installable skill — asserted
  // with the SAME clause rule as every rendered surface, so a legitimate
  // negated reference ("Onboarding is not installed") is not a false red.
  assert.equal(unnegatedInstall(ONBOARDING_INSTRUCTIONS), null,
    'the onboarding sentence must not present onboarding as an installable')
  assert.ok(!SKILLS_LIST.includes('onboarding'),
    'onboarding must not be a member of the shipped set')
})
