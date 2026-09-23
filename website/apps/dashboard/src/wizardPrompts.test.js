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
  WIZARD_CAPTIONS,
  wizardPromptText,
  wizardWorkflowsText,
} from './wizardPrompts.js'
import {
  HARNESS_CAPTURE_INSTALL,
  HARNESS_INTRO,
  HARNESS_NAMES,
  HARNESS_STEPS,
  SKILLS_INSTALL_URL,
  SKILLS_LIST,
  UNIVERSAL_COMMAND,
  harnessFamilyOf,
} from './harnesses.js'

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
  // The capture-install snippets point at the source repo
  // (`github.com/daniel-ospina/tortoise`) — a legitimate reference, surfaced only
  // once that surface was actually scanned (#4820 review, issue 2).
  'github.com',
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
  assert.deepEqual(snapshot.captions, WIZARD_CAPTIONS,
    'the rendered wizard captions drifted from the snapshot')
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
  // #4880: the live JSX captions and copy labels. They were the last
  // agent-facing prose NO invariant observed — a caption claiming onboarding
  // arrives as a skill reached the user with both suites green, because the
  // only guard that could see it was reading main.jsx as source text.
  for (const [name, text] of Object.entries(WIZARD_CAPTIONS)) {
    surfaces.push({ id: `caption.${name}`, text })
  }
  // The other live, data-driven render surfaces. harnesses.test.js covers some
  // of these with its OWN list; the two lists were not supersets of the live
  // render, and every remaining false green came from that seam.
  for (const [id, text] of Object.entries(HARNESS_INTRO)) surfaces.push({ id: `HARNESS_INTRO.${id}`, text })
  // HARNESS_CAPTURE_INSTALL is live as well — main.jsx:10025 renders each entry in
  // a <pre>. It was on NO list, so a hostile URL pasted into a capture-install
  // snippet reached the user with both suites green (#4820 review, issue 2).
  for (const [id, text] of Object.entries(HARNESS_CAPTURE_INSTALL)) {
    if (typeof text === 'string') surfaces.push({ id: `HARNESS_CAPTURE_INSTALL.${id}`, text })
  }
  for (const harness of Object.keys(HARNESS_NAMES)) {
    // NOT HARNESS_SKILLS: its only consumers are inside LEGACY_WIZARD_ARCHIVED
    // (main.jsx:7857/7864, flag=false), so it is not a live render and pinning
    // it would red the gate for an edit to dead code.
    //
    // HARNESS_STEPS(harness, key) ALREADY selects the harness — it ends
    // `})[harness]` (harnesses.js:154) and returns the step ARRAY. Reading
    // `steps[harness]` off that array is always `undefined`, so this block never
    // ran and none of the surfaces below were ever scanned, while the comment
    // above it claimed they were. Found by an independent review of #4820
    // (issue 1): the dead branch was invisible because the block it guards is
    // the only consumer, and no assertion counted the surfaces.
    const list = HARNESS_STEPS(harness, KEY)
    if (Array.isArray(list)) {
      for (const [n, entry] of list.entries()) {
        if (typeof entry === 'string') surfaces.push({ id: `HARNESS_STEPS.${harness}[${n}]`, text: entry })
        else if (entry && typeof entry === 'object') {
          for (const [k, v] of Object.entries(entry)) {
            if (typeof v === 'string') surfaces.push({ id: `HARNESS_STEPS.${harness}[${n}].${k}`, text: v })
          }
        }
      }
    }
  }
  for (const harness of COMMAND_HARNESSES) {
    surfaces.push({ id: `UNIVERSAL_COMMAND.${harness}`, text: UNIVERSAL_COMMAND[harness](KEY) })
  }
  return surfaces
}

// Every "install … skills (<parenthetical>)" statement, with what FOLLOWS it.
// The phrase is GENERALIZED, not the literal "install the Tortoise skills": a
// reworded claim ("Also install the Tortoise helper skills (agent-memory) from
// …") reintroduced the #4365 defect class while never matching the literal.
function setStatements(text) {
  const out = []
  const re = /install[^\n]{0,60}?skills?\s*\(([^)]*)\)/gi
  let m
  while ((m = re.exec(text)) !== null) {
    out.push({ at: m.index, inner: m[1], end: m.index + m[0].length })
  }
  return out
}

const EXPECTED_SET = ` (${SKILLS_LIST})`
const SKILL_NAMES = SKILLS_LIST.split(', ')

// What may legitimately follow a set statement. ANCHORED, because a bare
// "any whitespace" probe cannot tell the legitimate ` from <installer URL>.`
// from a hostile ` plus agent-memory:`.
//
// `:` is a legitimate terminus ONLY when it ends the line or the surface — a
// step LABEL (`Install the Tortoise skills (a, b, c):` with the command on the
// next line, HARNESS_STEPS.cursor) ends there. It is NOT allowed to be followed
// by more text, or `: plus agent-memory` would slip through.
const TAIL_OK = [
  /^$/,                                    // the statement ends the surface
  /^\n/,                                   // ends the line
  /^:(?:\n|$)/,                            // a label terminus
  new RegExp(`^ from ${SKILLS_INSTALL_URL.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\.`),
]

// Is this parenthetical an ENUMERATION of capabilities? If it is, it must be
// exactly the shipped set. Exact-matching a hint instead ("(run in a terminal)")
// FALSE-REDded a legitimate rewording of instructional prose, so the hint is
// recognised by SHAPE: prose is allowed, an enumeration is not.
const hasSkillIdShape = (inner) =>
  /(?:^|[\s,])([a-z][a-z0-9]*(?:-[a-z0-9]+)+)(?=$|[\s,)]|\s)/.test(inner)
const looksLikeEnumeration = (inner) =>
  inner.includes(',') || SKILL_NAMES.some((n) => inner.includes(n)) || hasSkillIdShape(inner)

test('#4365: every rendered shipped-set statement is EXACTLY the shipped set', () => {
  // The old guard compared only the text INSIDE the parentheses, so a superset
  // appended after `)` was invisible in EITHER form (`, plus X` and ` plus X`).
  let seen = 0
  for (const { id, text } of allRenderedSurfaces()) {
    const stmts = setStatements(text)
    assert.ok(stmts.length <= 1,
      `${id}: at most one shipped-set statement per surface (found ${stmts.length})`)
    for (const { inner, end } of stmts) {
      const whole = ` (${inner})`
      if (whole === EXPECTED_SET) {
        seen += 1
      } else {
        assert.ok(!looksLikeEnumeration(inner),
          `${id}: a statement that enumerates capabilities must enumerate exactly `
          + `"${SKILLS_LIST}" — got ${JSON.stringify(whole)}`)
      }
      const after = text.slice(end)
      assert.ok(TAIL_OK.some((form) => form.test(after)),
        `${id}: nothing but a line end, a label colon or " from ${SKILLS_INSTALL_URL}." `
        + `may follow the set statement — got ${JSON.stringify(after.slice(0, 40))}`)
    }
  }
  assert.ok(seen > 0, 'precondition: the rendered surfaces carry set statements')
  // The four config-writing prompts each state the set exactly once.
  for (const harness of ['pi', 'cursor', 'claude', 'codex']) {
    const stmts = setStatements(wizardPromptText(harness, 1, KEY, 'included'))
    assert.equal(stmts.length, 1, `${harness}: the step-1 prompt states the shipped set exactly once`)
    assert.equal(` (${stmts[0].inner})`, EXPECTED_SET,
      `${harness}: the step-1 prompt enumerates the shipped set`)
  }
})

// Install-family verb that is NOT negated in its own clause. Clause-scoped on
// purpose: "Onboarding is not a skill — install it" carries `not` in an EARLIER
// clause and must still be caught, while "Onboarding is not installed" must not.
const NEGATION = /\b(not|never|no|without|isn't|aren't)\b/i
const CLAUSE_BREAK = /[—;.,:!?()\-]/
function unnegatedInstall(line) {
  // EVERY install verb, not just the first: returning on the first match let
  // "Onboarding is not installed; install the tortoise-onboarding skill" pass —
  // the second, unnegated verb was never examined.
  for (const m of line.matchAll(/\b\w*install\w*\b/gi)) {
    const clause = line.slice(0, m.index).split(CLAUSE_BREAK).pop() ?? ''
    if (!NEGATION.test(clause)) return m[0]
  }
  return null
}

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
    for (const m of text.matchAll(/\b[a-z0-9_-]*tortoise[a-z0-9_-]*\b/gi)) {
      const raw = m[0]
      const before = text[m.index - 1] ?? ''
      const after = text.slice(m.index + raw.length)
      // A PATH, a <placeholder> or a FILENAME is not a skill NAME. The
      // capture-install snippets legitimately carry `<path-to-tortoise>/…`,
      // `extensions/tortoise-capture` and `tortoise-session-end.sh`; flagging
      // those was a false red the moment this surface was actually scanned
      // (#4820 review, issue 1/2 — the block that would have scanned it was
      // dead, so the false red had never been observed).
      if ('/<.~'.includes(before)) continue
      if (after.startsWith('/') || /^\.[a-z0-9]+/i.test(after)) continue
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
  // A bare token counts as a host when its last label is a plausible public TLD.
  // The earlier "three or more labels" rule let a 2-label host through
  // (`evil.com`), while a pure filename (`SKILL.md`, `config.toml`) must stay
  // out — which the TLD test gives for free, since `md`/`toml` are not TLDs.
  const PUBLIC_TLD = /^(?:com|org|net|io|co|dev|app|ai|info|xyz|me|us|uk|de|fr|es|to|cc|site|online|cloud|premiselabs)$/i
  // Extensions that would otherwise read as a ccTLD (`install-tortoise-skills.sh`).
  const FILE_EXT = /^(?:md|sh|json|js|mjs|jsx|ts|tsx|py|txt|toml|yml|yaml|html|css|lock|cfg|ini|log|csv|svg|png|jpg)$/i
  const BARE_DOMAIN = /(?<![\w.-])((?:[a-z0-9-]+\.){1,}[a-z]{2,})(?![\w-])/gi
  // An IP LITERAL is a bare token too, and it has no alphabetic last label — so
  // every rule above skips it. `https://1.2.3.4/mcp` was caught by URL_HOST, but
  // a scheme-less `Or connect at 1.2.3.4/mcp` reached the user with both suites
  // green (#4820 review, issue 3). Octet range is NOT validated: this is an
  // addressability gate, and over-matching a 4-group decimal is harmless here.
  const BARE_IPV4 = /(?<![\w.-])((?:\d{1,3}\.){3}\d{1,3})(?![\w-])/g
  for (const { id, text } of allRenderedSurfaces()) {
    for (const m of text.matchAll(URL_HOST)) {
      assert.ok(APPROVED_HOSTS.has(m[1].toLowerCase()),
        `${id}: a URL points at a non-approved host ${JSON.stringify(m[0])}`)
    }
    for (const m of text.matchAll(BARE_DOMAIN)) {
      const host = m[1].toLowerCase()
      const lastLabel = host.split('.').pop()
      if (FILE_EXT.test(lastLabel)) continue // install-tortoise-skills.sh
      if (!PUBLIC_TLD.test(lastLabel)) continue // a decimal, a nested key, an abbreviation
      assert.ok(APPROVED_HOSTS.has(host),
        `${id}: a bare domain does not match an approved host ${JSON.stringify(host)}`)
    }
    for (const m of text.matchAll(BARE_IPV4)) {
      assert.ok(APPROVED_HOSTS.has(m[1]),
        `${id}: a bare IP literal is not an approved host ${JSON.stringify(m[1])}`)
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

test('#4365: a line that calls onboarding a skill must negate it', () => {
  // "Onboarding is also available as a skill." uses no install verb and never
  // names `tortoise-onboarding`, so BOTH earlier rules missed it. harnesses.js's
  // own Guard 3 rejects that wording, but only on ITS surfaces — these are the
  // wizard surfaces, which its list did not reach.
  for (const { id, text } of allRenderedSurfaces()) {
    for (const raw of text.split('\n')) {
      // the document URL's own path contains /skills/ — not prose
      const line = raw.split(ONBOARDING_URL).join('')
      if (!/onboarding/i.test(line) || !/\bskills?\b/i.test(line)) continue
      assert.match(line, /\b(not|never|no)\b[^—;.]*\bskills?\b/i,
        `${id}: a line that mentions onboarding and the word "skill" must say it is `
        + `NOT one — got ${JSON.stringify(line)}`)
    }
  }
})

test('#4365: onboarding is only ever named as the document, never as a package', () => {
  // Two ways to present onboarding as an installable WITHOUT using an install
  // verb — both were GREEN against the previous revision:
  //   (a) naming it as something packaged: "A copy is also packaged as the
  //       tortoise-onboarding skill."  → caught here: the name may appear only
  //       inside the approved document URL.
  //   (b) splitting the claim across lines: an install line whose object is not
  //       the shipped set, immediately above the document URL. Caught here too.
  for (const { id, text } of allRenderedSurfaces()) {
    const outsideUrl = text.split(ONBOARDING_URL).join('')
    assert.ok(!/tortoise-onboarding/i.test(outsideUrl),
      `${id}: onboarding is named outside the approved document URL — `
      + `it may only be the instructions document, never a package or skill`)
    const lines = text.split('\n')
    for (let n = 0; n + 1 < lines.length; n += 1) {
      if (!unnegatedInstall(lines[n])) continue
      const statesSet = setStatements(lines[n]).some((st) => ` (${st.inner})` === EXPECTED_SET)
      if (statesSet) continue
      assert.ok(!lines[n + 1].includes(ONBOARDING_URL),
        `${id}: an un-negated install verb on one line with the onboarding document `
        + `on the next reads as "install the onboarding skill": `
        + `${JSON.stringify(lines[n])} / ${JSON.stringify(lines[n + 1])}`)
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
