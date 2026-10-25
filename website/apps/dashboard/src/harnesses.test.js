// harnesses.test.js — run with node --test (Node 20+, zero deps: pure module,
// no jsdom/React needed) (#1998 W2 — universal command, surface 5).
import { test } from 'node:test'
import * as HARNESS_MODULE from './harnesses.js'
import {
  ADVERTISED_VERIFY_FILE_STEP_TEXT,
  LEGACY_VERIFY_HARNESSES,
  WIZARD_STEP2_TEXT,
  verifyFileStepText,
  wizardPromptText,
  wizardWorkflowsText,
} from './wizardPrompts.js'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  HARNESS_ORDER, HARNESS_NAMES, HARNESS_SELF_INSTALL, HARNESS_TEACH_HUMAN,
  CODEX_DESKTOP_COMMAND,
  HARNESS_INTRO, HARNESS_INSTALL, HARNESS_STEPS, HARNESS_SKILLS, HARNESS_PERSIST,
  HARNESS_SKILLLESS, HARNESS_SKILLS_IN_PROMPT, HARNESS_SKILLS_IN_STEPS,
  HARNESS_COPY_LABEL, HARNESS_CONTINUE_LABEL,
  HARNESS_CAPTURE_INSTALL, HARNESS_CAPTURE_REASON,
  HARNESS_CAPTURE_STATUS_LABEL, HARNESS_CAPTURE_SUPPORT, HARNESS_CAPTURE_SEAM,
  PI_CAPTURE_INSTALL, CLAUDE_CAPTURE_NOTE, CODEX_CAPTURE_INSTALL,
  HARNESS_OAUTH, CANONICAL_MCP_URL, ONBOARDING_INSTRUCTIONS_URL, SKILLS_CLAIM,
  SKILLS_LIST,
  CAPTURE_OPT_IN_ENV, CAPTURE_OPT_IN_LINE, CLAUDE_WEB_FILING, HARNESS_CAPTURE_REQUIRES_OPT_IN,
  HARNESS_FAMILIES, HARNESS_FAMILY_IDS, harnessFamilyOf, preferredSurface,
  harnessDisplayName, knownHarnessName,
  HARNESS_ANALYTICS_ALIASES, analyticsHarnessOf,
  SERVED_SKILLS_ORIGIN, servedSkillUrl, WORKFLOWS_PROMPT, SKILLS_INSTALL_URL,
} from './harnesses.js'

const KEY = 'tt_w2_test_key'

// #4365: the served installer ships THREE capabilities — onboarding is delivered
// as INSTRUCTIONS (a document the agent reads), never installed into a harness's
// skills namespace.
//
// EXACT assertions only. This file previously also guarded the same prose SHAPE
// the wizardPrompts gate guarded (an approved-host allowlist, a "no hand-named
// skill" sweep, install-verb/negation clause heuristics, a set-statement tail
// rule). EIGHT independent adversarial reviews found ~41 defects in that net and
// NONE in the product; one cycle introduced a bypass in the net while fixing the
// net. The shape net is therefore removed from both gates — a net whose gaps are
// silent reads as coverage, which is worse than no net. The completeness classes
// it was reaching for live in #4885. What remains is what can be demonstrated:
// the set itself, and that the claim is DERIVED from it rather than restated.
test('#4365: the shipped set is exactly three capabilities, and the claim is derived from it', () => {
  assert.deepEqual(SKILLS_LIST.split(', '),
    ['how-to-use-tortoise', 'tortoise-decide', 'tortoise-file-finding'])
  assert.equal(SKILLS_CLAIM, `Install the Tortoise skills (${SKILLS_LIST})`,
    'SKILLS_CLAIM must be built from SKILLS_LIST — never a second literal')
  assert.ok(!/onboarding/i.test(SKILLS_LIST),
    'the shipped set must never include onboarding — it is not a skill')
})

test('DE2E-5: the 7-harness vocabulary — self-install (4) + teach-human (3, incl. OAuth chatgpt) cover HARNESS_ORDER exactly', () => {
  assert.equal(HARNESS_ORDER.length, 7)
  assert.deepEqual([...HARNESS_ORDER].sort(), [...Object.keys(HARNESS_NAMES)].sort())
  const split = [...HARNESS_SELF_INSTALL, ...HARNESS_TEACH_HUMAN].sort()
  assert.deepEqual(split, [...HARNESS_ORDER].sort(), 'self-install ∪ teach-human == all 7')
  const overlap = HARNESS_SELF_INSTALL.filter((h) => HARNESS_TEACH_HUMAN.includes(h))
  assert.equal(overlap.length, 0, 'self-install and teach-human are disjoint')
  assert.deepEqual(HARNESS_SELF_INSTALL.sort(), ['claude', 'codex', 'cursor', 'pi'].sort())
  // #1701: chatgpt joins the teach-human vocabulary (no local shell/cloud —
  // the HUMAN completes the connector steps) AND is an OAuth-only harness.
  // #2865: the two Claude connector leaves join it — their recipe is key-less
  // OAuth now, so HARNESS_OAUTH is the vocabulary the live connect step reads.
  assert.deepEqual(HARNESS_TEACH_HUMAN.sort(), ['claude-desktop', 'claude-web', 'chatgpt'].sort())
  assert.deepEqual(HARNESS_OAUTH, ['claude-desktop', 'claude-web', 'chatgpt'])
  assert.equal(HARNESS_OAUTH.filter((h) => HARNESS_SELF_INSTALL.includes(h)).length, 0,
    'OAuth harnesses are never self-install (no key-mint surface)')
  assert.equal(HARNESS_OAUTH.filter((h) => !HARNESS_TEACH_HUMAN.includes(h)).length, 0,
    'OAuth harnesses are teach-human')
})

test('#3430: the ONE rendered config block survives; the unrendered per-harness blocks are gone', () => {
  // #3430: `UNIVERSAL_COMMAND` carried one block per harness, but only the Codex
  // Desktop leaf was ever rendered — `main.jsx` imports and renders exactly that
  // one call — measured at `6b38829fc` (the parent of the #3430 commit), where
  // `rg -n 'UNIVERSAL_COMMAND' src/main.jsx` returns exactly two hits: the import
  // and render site :8780. Naming the sha matters: at `da525339a` the same command
  // returns nothing, so "the parent commit" would not reproduce. The other seven
  // were copy no user saw,
  // while `DE2E-5` asserted against them: the suite could keep passing while the
  // product said something else. The constant is now that one block.
  assert.equal(typeof CODEX_DESKTOP_COMMAND, 'function')
  const cmd = CODEX_DESKTOP_COMMAND(KEY)
  assert.ok(cmd && cmd.length > 0, 'codexDesktop command non-empty')
  // Re-adding a per-harness block is a DECISION (render it in main.jsx first),
  // not a tidy-up — so pin the deletion rather than a leaf count.
  for (const gone of ['UNIVERSAL_COMMAND', 'UNIVERSAL_COMMAND_HARNESSES']) {
    assert.equal(HARNESS_MODULE[gone], undefined, `${gone} must stay deleted (#3430)`)
  }
})

// #2912: the wizard's two-level chooser is a UI grouping over the SAME leaf
// vocabulary — this pins that invariant (a family/surface id that is not a real
// leaf would render a chooser entry with no payload behind it).
test('#2912: HARNESS_FAMILIES groups the leaf vocabulary (Claude 3 + Codex 2 + Cursor/Pi/ChatGPT)', () => {
  // #9660 owner ruling: 'chatgpt' joined as a single-choice family.
  assert.deepEqual(HARNESS_FAMILY_IDS, ['claude', 'codex', 'cursor', 'pi', 'chatgpt'])
  const surfaceIds = HARNESS_FAMILIES.flatMap((f) => f.surfaces.map((s) => s.id))
  const claude = HARNESS_FAMILIES.find((f) => f.id === 'claude')
  const codex = HARNESS_FAMILIES.find((f) => f.id === 'codex')
  assert.deepEqual(claude.surfaces.map((s) => s.id), ['claude', 'claude-desktop', 'claude-web'])
  assert.deepEqual(codex.surfaces.map((s) => s.id), ['codex', 'codexDesktop'])
  // Cursor/Pi/ChatGPT are single-choice: family id == leaf id, no surface row.
  for (const id of ['cursor', 'pi', 'chatgpt']) {
    assert.deepEqual(HARNESS_FAMILIES.find((f) => f.id === id).surfaces, [])
  }
  // Every chooser id resolves to a real leaf — 'codexDesktop' is the ONE
  // UI-only leaf (#2328) and is the only id outside HARNESS_ORDER.
  for (const id of [...HARNESS_FAMILY_IDS, ...surfaceIds]) {
    assert.ok(HARNESS_ORDER.includes(id) || id === 'codexDesktop',
      `${id} must be a real leaf (HARNESS_ORDER member or the Codex Desktop surface)`)
  }
  // The families cover the wizard's whole leaf surface — since the #9660 owner
  // ruling that is EVERY leaf, chatgpt included (it used to be the exception).
  const covered = new Set([
    ...HARNESS_FAMILIES.flatMap((f) => (f.surfaces.length ? f.surfaces.map((s) => s.id) : [f.id])),
  ])
  for (const h of HARNESS_ORDER) {
    assert.ok(covered.has(h), `${h} must be reachable from the chooser`)
  }
})

test('#2912: family resolution + preferred surface + display names', () => {
  assert.equal(harnessFamilyOf('claude-desktop').id, 'claude')
  assert.equal(harnessFamilyOf('claude-web').id, 'claude')
  assert.equal(harnessFamilyOf('codexDesktop').id, 'codex')
  assert.equal(harnessFamilyOf('cursor').id, 'cursor')
  assert.equal(harnessFamilyOf('chatgpt').id, 'chatgpt')  // #9660 owner ruling
  const claude = HARNESS_FAMILIES.find((f) => f.id === 'claude')
  const cursor = HARNESS_FAMILIES.find((f) => f.id === 'cursor')
  // re-clicking the active family keeps the user's surface (non-destructive)
  assert.equal(preferredSurface(claude, 'claude-web'), 'claude-web')
  // switching families lands on the terminal/self-install surface first
  assert.equal(preferredSurface(claude, 'pi'), 'claude')
  assert.equal(preferredSurface(cursor, 'pi'), 'cursor')
  assert.equal(harnessDisplayName('codexDesktop'), 'Codex Desktop')
  assert.equal(harnessDisplayName('claude-desktop'), 'Claude Desktop')
  // #3428 (lane B3, review cycle 1 P2-1): `knownHarnessName` is the user-facing
  // lookup — a KNOWN leaf (including the HARNESS_EXTRA_NAMES-only Codex
  // Desktop) resolves, and an unknown id returns null so the CALLER owns the
  // neutral fallback (unlike harnessDisplayName, which falls back to the raw
  // id and would leak "codexDesktop" into the sentence).
  assert.equal(knownHarnessName('codexDesktop'), 'Codex Desktop')
  assert.equal(knownHarnessName('claude-desktop'), 'Claude Desktop')
  assert.equal(knownHarnessName('pi'), 'Pi')
  assert.equal(knownHarnessName('not-a-harness'), null)
})

// ── #2936: the copy-attribution vocabulary bridge ──────────────────────────
// The beacon may only name a harness the SERVER's frozen analytics vocabulary
// accepts. This module owns the exception table; the server owns the
// vocabulary itself, and the cross-surface contract that every chooser leaf
// resolves INTO it lives in
// `tests/test_onboarding_endpoints.py::test_cross_surface_harness_vocab_contract`
// (a JS test cannot restate a Python Literal without duplicating it here,
// which is the drift the contract test exists to prevent).
test('#2936: analyticsHarnessOf maps the UI-only leaf, and refuses the unattributable one', () => {
  // The one SURFACE leaf: its copy installs the `codex` capture seam, so it is
  // attributed to `codex` — the server Literal is deliberately NOT widened
  // (#1701's recorded decision).
  assert.equal(HARNESS_ANALYTICS_ALIASES.codexDesktop, 'codex')
  assert.equal(analyticsHarnessOf('codexDesktop'), 'codex')
  // A leaf with no attributable value returns null, so the caller emits
  // NOTHING — never a value the server drops (and reports, #3821).
  // #9660 owner ruling: chatgpt IS a chooser leaf now, but it stays
  // DELIBERATELY non-attributable — its beacons emit nothing, because
  // attribution needs a member in the server's session vocabulary and #1701
  // recorded that ChatGPT never files a session. Reachable ≠ attributable; the
  // two are pinned separately (harnesses.test.js + the cross-surface contract).
  assert.equal(HARNESS_ANALYTICS_ALIASES.chatgpt, null)
  assert.equal(analyticsHarnessOf('chatgpt'), null)
  // The identity-valued leaves (the server's own vocabulary) are unchanged.
  for (const h of HARNESS_ORDER.filter((h) => h !== 'chatgpt')) {
    assert.equal(analyticsHarnessOf(h), h, `${h} is its own analytics value`)
  }
  // Defensive: no leaf, no value.
  assert.equal(analyticsHarnessOf(null), null)
  assert.equal(analyticsHarnessOf(undefined), null)
  // EVERY leaf the LIVE chooser can select is attributable — a new surface
  // whose id is not a capture harness needs an alias entry (or the server
  // vocabulary would have to grow, which #1701 forbids). This is the client
  // half of the drift guard; the server half asserts the resolved set EQUALS
  // the server vocabulary.
  const chooserLeaves = new Set([
    ...HARNESS_FAMILIES.map((f) => f.id),
    ...HARNESS_FAMILIES.flatMap((f) => f.surfaces.map((s) => s.id)),
  ])
  assert.deepEqual([...chooserLeaves].sort(),
    ['chatgpt', 'claude', 'claude-desktop', 'claude-web', 'codex', 'codexDesktop', 'cursor', 'pi'],
    'the chooser leaf population moved — re-check the analytics mapping for the new leaf')
  for (const leaf of chooserLeaves) {
    if (leaf === 'chatgpt') continue  // #1701: deliberately inert (see above)
    assert.ok(analyticsHarnessOf(leaf) != null,
      `${leaf} is reachable from the chooser but has no analytics value — the beacon would emit nothing`)
  }
  // Exactly ONE chooser leaf may be non-attributable, and it is named here so a
  // second one cannot appear unnoticed (#9660: the exception moved from "unreachable
  // leaf" to "reachable but inert").
  assert.deepEqual([...chooserLeaves].filter((l) => analyticsHarnessOf(l) == null), ['chatgpt'])
})

test('#2328/#2329: Codex Desktop variant — terminal-less config path, .agents/skills, no .codex/skills', () => {
  // #3430: the Codex CLI block that used to sit beside this one was rendered
  // nowhere (an agent-driven harness gets a prompt, not a paste-able block —
  // #3232), so only the Desktop block remains. The CLI mechanics are the AGENT's
  // job now, which is why nothing here names `codex mcp add`.
  const desktop = CODEX_DESKTOP_COMMAND(KEY)
  assert.ok(desktop && desktop.length > 0, 'codexDesktop command non-empty')
  assert.match(desktop, /\[mcp_servers\.tortoise\]/, 'desktop: config.toml mcp_servers block')
  assert.match(desktop, /bearer_token_env_var = "TORTOISE_API_KEY"/, 'desktop: env-var-name bearer')
  assert.match(desktop, /launchctl setenv TORTOISE_API_KEY/, 'desktop: macOS env path')
  assert.match(desktop, /setx TORTOISE_API_KEY/, 'desktop: Windows env path')
  assert.match(desktop, /http_headers = \{ Authorization/, 'desktop: literal-header fallback')
  assert.match(desktop, /\.agents\/skills/, 'desktop: skills in .agents/skills (#2329)')
  assert.match(desktop, /--harness codex/, 'desktop: skill installer deferral command')
  // .codex/skills may appear ONLY as the disambiguation "(not .codex/skills)"
  // — never as a target path or install instruction.
  if (desktop.includes('.codex/skills')) {
    assert.ok(desktop.includes('not .codex/skills'), '.codex/skills only as negated disambiguation')
  }
  assert.equal(HARNESS_COPY_LABEL.codexDesktop, 'Copy instructions')
  assert.ok(HARNESS_INTRO.codexDesktop && HARNESS_INTRO.codexDesktop.includes('~/.codex/config.toml'), 'desktop intro names the config file')
})

test('DE2E-5: the RENDERED self-install prompts carry the MCP URL and the skill install', () => {
  // #3430: retargeted off the unrendered `UNIVERSAL_COMMAND.*` mechanics copy
  // onto the surfaces `main.jsx` actually renders. The mechanics (which `mcp add`
  // subcommand, which config file) became the AGENT's job at #3232, so pinning
  // them pinned a promise the product no longer makes.
  for (const h of HARNESS_SELF_INSTALL) {
    const text = [wizardPromptText(h, 1, KEY, 'included'), wizardPromptText(h, 2, KEY, 'included')]
      .filter(Boolean).join('\n')
    assert.ok(text.length > 0, `${h}: renders a connect prompt`)
    assert.match(text, /https:\/\/api\.premiselabs\.co\/mcp\/?/, `${h}: rendered prompt names the MCP URL`)
    assert.match(text, /install-tortoise-skills\.sh/, `${h}: rendered prompt names the skill installer`)
  }
})

test('DE2E-5: EVERY harness verifies through an ADVERTISED tool — the clause is 7 harnesses, not 4', () => {
  // #3430 review, cycle 1: the deleted universal commands for the SIX
  // agent/connector harnesses each ended in a `tortoise_health` verify. Dropping
  // them silently narrowed a CONTRACT; `HARNESS_SELF_INSTALL` is only 4 of the
  // harnesses that render a prompt, and the two connector leaves
  // (claude-desktop, claude-web) were left covered by nothing but a snapshot
  // `deepEqual` — which passes for any text change regenerated in the same
  // commit, so it cannot protect a semantic claim. Asserted here on the RENDERED
  // text instead.
  //
  // #9660: `chatgpt` was the one exclusion (`HARNESS_ORDER.filter(h => h !==
  // 'chatgpt')`), and that exclusion hid a real defect — its first job's verify
  // half named `tortoise_health`, which a tool-grant client cannot call because
  // `_RetiredToolTransform` strips retired names from `tools/list`. The leaf this
  // PR makes live was the leaf the guard skipped. It is now IN the population,
  // with its own expectation: the ADVERTISED name, not the retired one.
  const rendered = [...HARNESS_ORDER]
  assert.deepEqual(rendered.slice().sort(),
    ['chatgpt', 'claude', 'claude-desktop', 'claude-web', 'codex', 'cursor', 'pi'],
    'the verify contract covers every harness with a rendered surface')
  // The legacy list is ASSERTED, not inferred: a leaf that is not named here must
  // get the advertised call, so a future grant-based harness cannot silently
  // inherit the retired name (the fail-open the reviewer found in the first cut
  // of this guard, where `else` REQUIRED `tortoise_health` of any non-chatgpt
  // harness — the guard itself pushed the next author at the wrong name).
  assert.deepEqual([...LEGACY_VERIFY_HARNESSES].sort(),
    ['claude', 'claude-desktop', 'claude-web', 'codex', 'cursor', 'pi'],
    'the leaves that keep the legacy `tortoise_health` body, listed by name')
  for (const h of rendered) {
    const step2 = wizardPromptText(h, 2, KEY, 'included') || ''
    assert.match(step2, /tortoise_(health|overview)/, `${h}: rendered step 2 names the verify tool`)
    assert.match(step2, /tortoise_create_point/, `${h}: rendered step 2 files the first memory`)
    if (LEGACY_VERIFY_HARNESSES.includes(h)) {
      assert.match(step2, /tortoise_health/, `${h}: the shared body keeps the legacy name`)
    } else {
      assert.match(step2, /tortoise_overview\(section="health"\)/,
        `${h}: the verify call is the ADVERTISED tool (tools/list), not the retired alias`)
      assert.ok(!step2.includes('tortoise_health'),
        `${h}: never the retired tortoise_health — it is absent from tools/list (#3883)`)
      // the same body must be what its step-1 first job ends with, or the
      // copyable prompt and the pinned step disagree.
      assert.ok(wizardPromptText(h, 1, KEY, 'included').endsWith(step2),
        `${h}: the first-job prompt must end with that same verify/file body`)
    }
  }
  // POLARITY: an unregistered leaf gets the call EVERY client can make. This is
  // the mutation the old `else` could not catch — a new harness whose branch
  // calls verifyFileStepText() and is never classified here.
  for (const unclassified of ['a-future-harness', 'codexDesktop']) {
    assert.equal(verifyFileStepText(unclassified), ADVERTISED_VERIFY_FILE_STEP_TEXT,
      `${unclassified}: an unregistered leaf must get the ADVERTISED verify call, `
      + 'not the retired one — the default is the safe side')
  }
  for (const legacy of LEGACY_VERIFY_HARNESSES) {
    assert.equal(verifyFileStepText(legacy), WIZARD_STEP2_TEXT,
      `${legacy}: a config-writing leaf keeps the shared legacy body`)
  }
  // …and the connector leaves' other delivery surface: the workflows prompt. This is
  // belt-and-braces on the SAME fact, not independent coverage — WORKFLOWS_PROMPT
  // alone carries no `tortoise_health`; the match comes from the embedded
  // `wizardPromptText('claude-web', 2, …)` already asserted above.
  assert.match(wizardWorkflowsText(KEY, 'included'), /tortoise_health/,
    'the workflows prompt (the connector leaves\' delivery surface) names the verify tool')
})

test('DE2E-5: teach-human harnesses are key-less OAuth on BOTH connector surfaces', () => {
  // #3430: the `UNIVERSAL_COMMAND['claude-desktop'|'claude-web']` blocks were
  // rendered nowhere. The LIVE recipe is JSX in `main.jsx` and IS pinned — by
  // `wizardConnectTripwire.test.js`: the canonical `<code>{CANONICAL_MCP_URL}</code>`
  // (:1576), `Authorize` (:1579), "Leave Request headers empty" (:1581), and the
  // beta caveat's absence (:1591/:1598). What is asserted HERE is the data half:
  // every connector surface is key-less OAuth and never carries a Bearer recipe.
  for (const h of ['claude-desktop', 'claude-web']) {
    const intro = HARNESS_INTRO[h]
    assert.ok(intro && intro.length > 0, `${h}: rendered intro present`)
    assert.ok(!/beta/i.test(intro), `${h}: intro carries no beta caveat`)
    assert.ok(!intro.includes(KEY), `${h}: intro never embeds the key`)
    assert.equal(HARNESS_CONTINUE_LABEL[h], "I've connected it — Continue →")
  }
  // The full recipe (Connectors UI, Authorize, org chooser) renders in JSX for
  // BOTH surfaces; only the Desktop intro restates it in data.
  const desktop = HARNESS_INTRO['claude-desktop']
  assert.match(desktop, /Connectors/, 'desktop: intro names the Connectors UI')
  assert.match(desktop, /Authorize/, 'desktop: intro names the OAuth Authorize step')
  assert.match(desktop, /No API key is needed/, 'desktop: intro says no key is needed')
})

test('#1701 DE2E-5: chatgpt is the key-less OAuth harness — steps + skills-as-prompt copy', () => {
  // #9660: the connector steps name the CURRENT surface. Read off OpenAI's live
  // UI on 2026-10-23 (Plus account): `Plugins → Add → Add custom MCP server` is
  // the entry point, there is NO Developer-mode prerequisite and no such toggle
  // in Settings, and the creation dialog has NO "Scan Tools" button. The OAuth
  // grant is a separate `Connect` on the installed plugin's detail page. The
  // retired literals are asserted ABSENT (see the `stale` list below — it names
  // every one of them exactly, including the old step 2, so a partial regression
  // is caught, not just the step-1 form).
  const steps = HARNESS_STEPS('chatgpt', KEY)
  assert.ok(Array.isArray(steps) && steps.length >= 6, 'chatgpt step list present')
  assert.ok(steps.some((s) => typeof s === 'string' && s.includes('Add custom MCP server')),
    'steps: the custom-MCP-server entry point (#9660)')
  assert.ok(steps.some((s) => typeof s === 'string' && s.includes('Create as a plugin')),
    'steps: the creation dialog\'s primary button (#9660)')
  assert.ok(steps.some((s) => typeof s === 'string' && s.includes('settings/plugins-settings')),
    'steps: where the installed plugin (and its Connect) lives (#9660)')
  assert.ok(steps.some((s) => typeof s === 'string' && s.includes('View details')),
    'steps: the observed control on the plugin row (#9660)')
  assert.ok(steps.some((s) => typeof s === 'string' && s.includes('Upload from your computer')),
    'steps: the skills hand-off is ChatGPT\'s own Upload flow (#9660)')
  assert.ok(steps.some((s) => typeof s === 'string' && s.includes('Connect')),
    'steps: the OAuth grant is a separate act (#9660)')
  assert.ok(steps.some((s) => typeof s === 'string' && s.includes('chatgpt.com/plugins')), 'steps: plugins surface')
  for (const stale of ['Security and login', 'Scan Tools', 'Developer mode', '+ button', 'Developer-mode app']) {
    assert.ok(!steps.some((s) => typeof s === 'string' && s.includes(stale)),
      `steps: must not carry the retired path ${JSON.stringify(stale)} (#9660)`)
  }
  const urlStep = steps.find((s) => typeof s === 'object' && s.copy === 'https://api.premiselabs.co/mcp')
  assert.ok(urlStep, 'steps: MCP URL copy step (exact, no slash)')
  assert.ok(steps.some((s) => typeof s === 'string' && s.includes('OAuth')), 'steps: OAuth choice named')
  // the legacy HARNESS_INSTALL copy for chatgpt is the shared skills-as-prompt
  // body ONLY — byte-identical with Claude Web's (drift pin on WORKFLOWS_PROMPT)
  const prompt = HARNESS_INSTALL.chatgpt()
  assert.equal(prompt, HARNESS_INSTALL['claude-web'](), 'claude-web and chatgpt share the identical workflows body')
  assert.match(prompt, /Follow these workflows/, 'prompt: workflows marker')
  // #3430: the self-contained `UNIVERSAL_COMMAND.chatgpt` block is rendered
  // nowhere — chatgpt's live surfaces are the dashboard connect journey (#9660)
  // and the setup docs page (website/docs.html#chatgpt). Only the LIVE half is
  // asserted here.
  assert.equal(CANONICAL_MCP_URL, 'https://api.premiselabs.co/mcp')
  assert.ok(urlStep, 'the rendered steps carry the canonical no-slash URL')
  assert.equal(HARNESS_COPY_LABEL.chatgpt, 'Copy prompt')
  assert.equal(HARNESS_CONTINUE_LABEL.chatgpt, "I've connected it — Continue →")
  assert.ok(HARNESS_INTRO.chatgpt && HARNESS_INTRO.chatgpt.includes('skills you installed above'), 'chatgpt intro names the installed skills')
  assert.ok(HARNESS_INTRO.chatgpt.includes('paste the prompt below instead'), 'chatgpt intro keeps the paste as the fallback (#9660)')
  // session capture: disabled-with-reason (cloud-hosted — no local signal)
  assert.equal(HARNESS_CAPTURE_SUPPORT.chatgpt, false)
  assert.ok(HARNESS_CAPTURE_REASON.chatgpt && HARNESS_CAPTURE_REASON.chatgpt.length > 0,
    'chatgpt capture row renders a reason (never undefined)')
})

test('the RENDERED wizard prompts never inline a Bearer header or a project config', () => {
  // #3430: retargeted onto rendered surfaces. The dead `UNIVERSAL_COMMAND.cursor`
  // / `.pi` blocks were the old home of this check; the config files they
  // described are now written by the AGENT from the rendered prompt, so what is
  // asserted is what the human/agent actually reads.
  //
  // ⛔ KEY is itself `tt_w2_test_key`, so "does the prompt contain tt_" is TRUE
  // by construction and proves nothing — the property is WHERE the key lands:
  // the shell profile (env indirection), never an Authorization header or a
  // project config file that gets committed.
  const piPrompt = wizardPromptText('pi', 1, KEY, 'included')
  assert.match(piPrompt, /shell profile/, 'pi prompt sends the key to the shell profile')
  // ⛔ `["\s:=]*` — the `=` matters: the shipped literal-header form is
  // `Authorization = "Bearer …"` (CODEX_DESKTOP_COMMAND offers it as the documented
  // fallback), and a class without `=` cannot see it. Measured: the narrow class
  // returns false for exactly the form the product ships.
  assert.ok(!/Authorization["\s:=]*Bearer/i.test(piPrompt), 'pi prompt never inlines a Bearer header')
  assert.ok(!piPrompt.includes('.mcp.json'), 'pi prompt names no project config file — the agent writes it')
  // Scope: EVERY wizard prompt — all six harnesses that render one, not just the
  // four self-install leaves. `HARNESS_SELF_INSTALL` alone left the two connector
  // leaves (claude-desktop/claude-web) uncovered here, while the title named the
  // whole population (review cycle 3). The Codex Desktop block is deliberately
  // OUT of scope: it offers `Authorization = "Bearer ${key}"` as the documented
  // no-shell fallback, in a private file the copy tells the human to chmod 600 —
  // a designed exception, not an oversight.
  const withPrompts = HARNESS_ORDER.filter((h) => h !== 'chatgpt')
  assert.equal(withPrompts.length, 6, 'six harnesses render a wizard prompt')
  for (const h of withPrompts) {
    for (const step of [1, 2]) {
      const text = wizardPromptText(h, step, KEY, 'included') || ''
      assert.ok(!/Authorization["\s:=]*Bearer/i.test(text),
        `${h}/${step}: rendered copy never inlines a Bearer header`)
    }
  }
})

test('DE2E-2 copy sweep: rendered connect copy never says "team"/"workspace"', () => {
  // #3430: this swept the deleted universal commands; sweep what renders now —
  // the one surviving config block, every rendered prompt (both live modes are
  // covered by wizardPrompts.test.js), and every rendered intro.
  const parts = [CODEX_DESKTOP_COMMAND(KEY)]
  for (const h of HARNESS_ORDER) {
    for (const step of [1, 2]) parts.push(wizardPromptText(h, step, KEY, 'included') || '')
    parts.push(HARNESS_INTRO[h] || '')
  }
  const all = parts.join(' ')
  assert.ok(!/\bteam\b/i.test(all), 'no "team" in rendered connect copy')
  assert.ok(!/workspace/i.test(all), 'no "workspace" in rendered connect copy')
})

test('A0 rollback: legacy HARNESS_* exports preserved (archived #1643 wizard + capture rows depend on them)', () => {
  assert.equal(typeof HARNESS_INSTALL, 'object')
  assert.equal(typeof HARNESS_STEPS, 'function')
  assert.equal(typeof HARNESS_SKILLS, 'function')
  assert.equal(typeof HARNESS_PERSIST, 'function')
  assert.ok(Array.isArray(HARNESS_SKILLLESS))
  assert.ok(Array.isArray(HARNESS_SKILLS_IN_PROMPT))
  assert.ok(Array.isArray(HARNESS_SKILLS_IN_STEPS))
  assert.equal(typeof HARNESS_COPY_LABEL, 'object')
  assert.equal(typeof HARNESS_CONTINUE_LABEL, 'object')
  assert.equal(typeof HARNESS_CAPTURE_INSTALL, 'object')
  assert.equal(typeof HARNESS_CAPTURE_REASON, 'object')
  assert.equal(typeof HARNESS_CAPTURE_STATUS_LABEL, 'object')
  assert.equal(typeof HARNESS_CAPTURE_SUPPORT, 'object')
  // the legacy exports still render a per-harness command for the archived surface
  //
  // #3802: this is the A0 ROLLBACK contract, and it is the only thing the
  // assertion is: `HARNESS_INSTALL` is READ ONLY by the archived
  // `{LEGACY_WIZARD_ARCHIVED && …}` render (`src/wizardArchived.test.js` pins
  // that census), so a green here means "a rollback to the legacy wizard would
  // render this", NOT "the user receives this". What a user receives on the
  // live surface is asserted where it renders: the agent prompt
  // (`wizardPrompts.test.js`, byte-exact snapshot of `wizardPromptText`) and the
  // capture row (`HARNESS_CAPTURE_INSTALL`, asserted below and by
  // `tests/test_harness_mcp_config.py`). An edit meant to reach users belongs
  // there first — #3754 had to patch both copies for exactly this reason.
  assert.match(HARNESS_INSTALL.claude(KEY), /claude mcp add/)
})

// #3575: the capture-INSTALL seam — `HARNESS_CAPTURE_SUPPORT[h] === true` is a
// capability claim, and it is only honest when the product actually INSTALLS a
// capture step. These pin the three legs (declared seam ⟺ in-repo artifact ⟺
// install step) so the Pi false PASS — `pi: true` with no capture install —
// cannot regress.
test('#3575: capture support is derived from the seam, and every supported harness installs it', () => {
  const seamHarnesses = Object.keys(HARNESS_CAPTURE_SEAM)
  for (const h of HARNESS_ORDER) {
    assert.equal(
      HARNESS_CAPTURE_SUPPORT[h],
      seamHarnesses.includes(h),
      `${h}: HARNESS_CAPTURE_SUPPORT must equal seam presence (derived, not asserted)`,
    )
    if (!HARNESS_CAPTURE_SUPPORT[h]) continue
    const artifact = HARNESS_CAPTURE_SEAM[h]
    assert.match(artifact, /^tortoise\//, `${h}: seam artifact must be in-repo`)
    // #3802: the claim must be satisfied by the surface a user RECEIVES.
    // `HARNESS_CAPTURE_INSTALL[h]` is the recipe the SETTINGS mount renders,
    // collapsed, inside its `details.manual-install` disclosure
    // (`harnessStatusCard.jsx`) — a Settings-first user's only path to it. It is
    // NOT a row in `main.jsx`: since #3513 §2 the shared capture row renders
    // `CAPTURE_INSTALL_POINTER` on every surface and `main.jsx` has zero reads of
    // this constant, so the recipe is not "the copy a user actually pastes" —
    // the agent prompt installs the recorder (`tortoise install <h>`, served
    // onboarding §5). All four live copies name their seam on their own.
    // `HARNESS_INSTALL[h]` is deliberately NOT OR'd in: it renders only inside
    // the archived `LEGACY_WIZARD_ARCHIVED` block, so the old OR made this
    // capability claim satisfiable by copy no user can see.
    const recipe = HARNESS_CAPTURE_INSTALL[h] || ''
    assert.ok(
      recipe.includes(artifact),
      `HARNESS_CAPTURE_INSTALL.${h} — the recipe the Settings manual-install disclosure renders — ` +
        `must install its declared seam ${artifact}`,
    )
  }
})

test('#3575: HARNESS_INSTALL.pi installs the in-repo Pi capture extension', () => {
  const pi = HARNESS_INSTALL.pi(KEY)
  assert.match(pi, /tortoise\/pi-hooks\/tortoise-capture\.ts/)
  assert.match(pi, /\.pi\/agent\/extensions/)
  // the Settings disclosure renders the SAME seam (one shared constant) — the
  // shared row itself carries only the pointer (#3513 §2)
  assert.equal(HARNESS_CAPTURE_INSTALL.pi, PI_CAPTURE_INSTALL)
  assert.match(HARNESS_CAPTURE_INSTALL.pi, /tortoise\/pi-hooks\/tortoise-capture\.ts/)
})

// #3818 (P2-4): the copy-paste Codex capture install must honour the SAME
// `$CODEX_HOME` override the installer does. A hardcoded `~/.codex` command
// writes the hook into a file Codex never reads on a non-default setup — the
// silent no-capture the seam exists to prevent, on the surface most likely to
// be read.
test('#3818: the Codex capture install copy honours $CODEX_HOME', () => {
  const codex = HARNESS_CAPTURE_INSTALL.codex
  assert.ok(codex, 'HARNESS_CAPTURE_INSTALL.codex present')
  assert.match(codex, /\$\{CODEX_HOME:-\$HOME\/\.codex\}/,
    'the copy must use the ${CODEX_HOME:-$HOME/.codex} default the installer honours')
  // Pin the COMMANDS, not just the prose: a comment naming $CODEX_HOME left
  // the actual mkdir/cp/chmod lines hardcoded to ~/.codex.
  const commandLines = codex.split('\n').filter((l) => /^(mkdir|cp|chmod)\b/.test(l))
  assert.ok(commandLines.length >= 3, commandLines)
  for (const line of commandLines) {
    assert.ok(line.includes('CODEX_HOME'),
      `command ignores $CODEX_HOME: ${line}`)
    assert.ok(!line.includes('~/.codex'),
      `command hardcodes ~/.codex: ${line}`)
  }
})

// #3819: the Cursor capture install. Cursor's MCP copy is a JSON file (so the
// capture step lives in HARNESS_CAPTURE_INSTALL + HARNESS_STEPS.cursor), the
// registration is HOME-scoped at `~/.cursor` (Cursor has no config-dir env
// var), and the IDE-ONLY limitation is disclosed — Cursor's own docs say
// cloud agents have no editor-lifetime session boundary, and the disclosure
// must sit where the user chooses Cursor, not in a footnote.
test('#3819: the Cursor capture install is home-scoped, flat, and discloses the IDE-only limit', () => {
  const cursor = HARNESS_CAPTURE_INSTALL.cursor
  assert.ok(cursor, 'HARNESS_CAPTURE_INSTALL.cursor present')
  assert.match(cursor, /tortoise\/cursor-hooks\/session-end\.sh/,
    'the capture step must name the declared seam artifact')
  assert.match(cursor, /~\/\.cursor\/hooks\.json/,
    'the copy must name ~/.cursor/hooks.json, the one path Cursor reads')
  assert.doesNotMatch(cursor, /CURSOR_HOME:-/,
    'Cursor has NO config-dir env var — do not teach a ${CURSOR_HOME:-…} default')
  assert.match(cursor, /sessionEnd/, 'the copy must name the sessionEnd event')
  // the flat entry shape is load-bearing — a nested matcher group invalidates
  // Cursor's WHOLE hooks.json (its validator rejects a non-string command)
  assert.match(cursor, /FLAT/,
    'the copy must warn that the entry is flat (a nested entry disables all Cursor hooks)')
  // Cursor's validator also requires a document `version`
  assert.match(cursor, /version/,
    'the copy must warn that hooks.json needs a numeric version')
  // the IDE-only limitation is disclosed on the install surface
  assert.match(cursor, /IDE-ONLY/)
  assert.match(cursor, /[Cc]loud [Aa]gent/,
    'the disclosure must name cloud agents explicitly')
  assert.match(cursor, /no editor-lifetime session boundary/,
    'the disclosure quotes the constraint that makes the gap real')
})

// #3713 P2-3 (review of #3721): Pi loads a top-level `tortoise-capture.ts` AND
// a `tortoise-capture/index.ts` as TWO extensions (no basename dedupe), so a
// pre-existing agent-infra `tortoise-capture/` double-POSTs every session_id
// alongside the seam. The install step must disable the legacy entry, and it
// must do so non-destructively. Structure-only: this pins the guard text, not
// the shell's behaviour (the guard is a copy-paste snippet, not an executed
// unit). Removing any leg REDs this test.
test('#3713: the Pi install disables a pre-existing tortoise-capture/ (no double-register)', () => {
  const pi = HARNESS_INSTALL.pi(KEY)
  // the colliding legacy path is named...
  assert.match(pi, /~\/\.pi\/agent\/extensions\/tortoise-capture\b/,
    'the guard must name the legacy entry Pi loads as a second extension')
  // ...a symlink (the usual agent-infra bootstrap shape) is unlinked...
  assert.match(pi, /\[ -L ~\/\.pi\/agent\/extensions\/tortoise-capture \]/,
    'the symlink leg must be guarded by -L (unlink the link, never the target)')
  // ...and a real directory is renamed to a name the loader SKIPS (dotfile).
  assert.match(pi, /\.tortoise-capture\.disabled/,
    'a real directory must be renamed to a dot-prefixed name `collectAutoExtensionEntries` skips')
  // non-negotiable: never recursively delete user files from the install snippet.
  assert.doesNotMatch(pi, /rm\s+-/,
    'the collision guard must never `rm` with flags — a bare `rm` can only unlink the symlink')
})

// #3615 made every in-repo hook fail closed on an explicit per-machine opt-in,
// but the six hand-authored instalments of capture copy kept telling users that
// capture simply happened: the built bundle shipped the old claim and
// `TORTOISE_CAPTURE` zero times (#3661). The fix states the truth once
// (`captureInstallNote`) and this test makes the drift unrepeatable.
//
// Mutation-checked: restoring the old sentence in the source (sed) REDs the
// negative leg; the built bundle went from 0 → 1 `TORTOISE_CAPTURE` with the old
// claim going to 0. Note the claude-web surface is deliberately NOT in the
// opt-in list: capture there is the agent's own tortoise_session_capture call,
// refused by the team off-switch — demanding the env var there would be the
// mirror of the bug this test exists for (an overstatement of the gate).
test('#3661: every capture surface names the opt-in, and none claims capture happens by default', () => {
  // The surface set is DERIVED from the module, not hand-listed: a new capture
  // seam that forgot the variable must RED, and a hand-maintained list would
  // silently stop covering it. `HARNESS_CAPTURE_INSTALL` is the capture-snippet
  // registry, and `HARNESS_CAPTURE_REQUIRES_OPT_IN` declares per seam whether
  // the opt-in gates it (Pi's extension is the one that does not).
  // The name is pinned as a LITERAL, not compared only against the module's own
  // constant: `text.includes(CAPTURE_OPT_IN_ENV)` alone stays green if someone
  // renames the constant on both sides, telling users to export a variable the
  // hook never reads. The literal is pinned cross-language on the Python side
  // (tests/test_session_capture_e2e.py passes it straight into
  // `capture_consent_enabled`), so both ends agree on the spelling.
  const OPT_IN_NAME = 'TORTOISE_CAPTURE'
  assert.equal(CAPTURE_OPT_IN_ENV, OPT_IN_NAME,
    'the disclosed variable must be the one tortoise/capture_consent.py reads')
  assert.equal(CAPTURE_OPT_IN_LINE, `${OPT_IN_NAME}=1`)
  assert.deepEqual(Object.keys(HARNESS_CAPTURE_REQUIRES_OPT_IN).sort(),
    Object.keys(HARNESS_CAPTURE_INSTALL).sort(),
    'every capture seam must declare whether the per-machine opt-in gates it')
  // The VALUES are the fact, not the map's private business: this map decides the
  // shipped success-screen sentence, so a wrong value is a false promise. Pin the
  // whole map — otherwise `pi: true` or `claude: false` are unfalsifiable
  // (the hook seams file nothing until TORTOISE_CAPTURE=1; installing the Pi
  // extension IS its opt-in — tests/test_pi_capture_hooks.py pins that decision).
  assert.deepEqual(HARNESS_CAPTURE_REQUIRES_OPT_IN,
    { claude: true, codex: true, cursor: true, pi: false },
    'the per-seam opt-in map decides the capture PROMISE — its values are load-bearing')
  for (const [h, gated] of Object.entries(HARNESS_CAPTURE_REQUIRES_OPT_IN)) {
    const snippet = HARNESS_CAPTURE_INSTALL[h]
    assert.ok(snippet.includes(OPT_IN_NAME),
      `HARNESS_CAPTURE_INSTALL.${h} must name ${OPT_IN_NAME} — after #3615 the hooks file no session without it (#3661)`)
    if (gated) {
      // naming the variable is not enough: the user has to be told the syntax.
      assert.ok(snippet.includes(CAPTURE_OPT_IN_LINE),
        `HARNESS_CAPTURE_INSTALL.${h} must show the line to run (${CAPTURE_OPT_IN_LINE})`)
    }
    // A wizard snippet that carries ANY capture copy inherits the duty — the
    // trigger is the copy itself, not an exact match with the row's snippet:
    // HARNESS_INSTALL.claude's install block differs by a comment line from
    // HARNESS_CAPTURE_INSTALL.claude, so an `includes(snippet)` test silently
    // skipped the PRIMARY seam (caught in review). Cursor's wizard body is MCP
    // config only, so it is correctly skipped.
    const install = HARNESS_INSTALL[h]
    if (typeof install === 'function') {
      const body = install(KEY)
      if (/capture/i.test(body)) {
        assert.ok(body.includes(OPT_IN_NAME),
          `HARNESS_INSTALL.${h} carries capture copy, so it must name ${OPT_IN_NAME}`)
      }
    }
  }
  // The wizard snippets that embed a capture step must carry the SAME note the
  // Memory-sources row shows — the de-duplication is the claim, so these are
  // pinned against the constant ITSELF, not a substring match. (A substring test
  // silently skipped claude: its install block differs from the row's snippet by
  // one comment line, so dropping the note from HARNESS_INSTALL.claude left the
  // suite green — caught in review.) Cursor is deliberately absent: its wizard
  // body is MCP config only, and its capture step is a wizard STEP, asserted
  // through HARNESS_STEPS.cursor above.
  assert.ok(HARNESS_INSTALL.claude(KEY).includes(CLAUDE_CAPTURE_NOTE),
    'HARNESS_INSTALL.claude must embed the shared Claude capture note')
  assert.ok(HARNESS_INSTALL.codex(KEY).includes(CODEX_CAPTURE_INSTALL),
    'HARNESS_INSTALL.codex must embed the shared Codex capture step')
  assert.ok(HARNESS_INSTALL.pi(KEY).includes(PI_CAPTURE_INSTALL),
    'HARNESS_INSTALL.pi must embed the shared Pi capture step')
  // #3802: the three legs above are the A0 ROLLBACK copies — `HARNESS_INSTALL`
  // renders only inside the archived block. The claim that matters here ("the
  // capture step the user gets is the SHARED one, so it cannot drift from the
  // Memory-sources row") must therefore also be asserted on the surface that
  // IS rendered: `HARNESS_CAPTURE_INSTALL`. Without this leg the de-duplication
  // claim is pinned on copy no user receives — the #3754 shape, where a fix had
  // to be applied twice and only one application was reachable.
  assert.ok(HARNESS_CAPTURE_INSTALL.claude.startsWith(CLAUDE_CAPTURE_NOTE),
    'the RENDERED claude capture row must carry the same shared note as the rollback copy')
  assert.equal(HARNESS_CAPTURE_INSTALL.codex, CODEX_CAPTURE_INSTALL,
    'the RENDERED codex capture row must be the shared constant (one source, no drift)')
  assert.equal(HARNESS_CAPTURE_INSTALL.pi, PI_CAPTURE_INSTALL,
    'the RENDERED pi capture row must be the shared constant (one source, no drift)')
  // The connect-wizard steps (the archived LEGACY_WIZARD_ARCHIVED render, a
  // retained rollback path) carry the Cursor scope disclosure in prose.
  assert.ok(HARNESS_STEPS('cursor', KEY)
    .map((s) => (typeof s === 'string' ? s : JSON.stringify(s))).join('\n').includes(OPT_IN_NAME),
  'HARNESS_STEPS.cursor carries the Cursor capture disclosure, so it must name the opt-in')
  // The local-spool claim is PER SEAM: only the Claude Code seam spools without
  // consent (session-turn.sh runs `tortoise session spool` ungated). Codex and
  // Cursor route through `sessions import`, which refuses BEFORE it can write —
  // telling those users their transcript is in a Tortoise spool would be this
  // issue's own defect class, so pin the asymmetry.
  assert.match(HARNESS_CAPTURE_INSTALL.claude, /capture-spool/,
    'the Claude seam DOES spool locally without consent — its copy must disclose that')
  for (const h of ['codex', 'cursor']) {
    assert.doesNotMatch(HARNESS_CAPTURE_INSTALL[h], /capture-spool/,
      `HARNESS_CAPTURE_INSTALL.${h} must not claim a local spool — its seam refuses before anything is written`)
    // The same claim could reach users through the archived wizard's prose
    // steps, so the asymmetry is pinned THERE too — as a FORWARD guard.
    // Only Cursor has a step list today (`HARNESS_STEPS` is a lookup, not a
    // registry: it returns undefined for claude/codex/pi, i.e. there is no step
    // to pin). Asserting on the empty string would be false assurance — it can
    // never fail — so the absent case is skipped explicitly, and a future
    // Codex step that claims a Tortoise spool REDs here instead.
    const steps = HARNESS_STEPS(h, KEY)
    if (!Array.isArray(steps)) continue
    assert.doesNotMatch(steps
      .map((s) => (typeof s === 'string' ? s : JSON.stringify(s))).join('\n'), /capture-spool/,
    `HARNESS_STEPS.${h} must not claim a local spool either — same seam, same refusal`)
  }
  // The negative half is a SOURCE scan, not a list of rendered values: the
  // claude-web filing paragraph is gated off (HARNESS_CAPTURE_SUPPORT['claude-web']
  // is false — disabled-with-reason), so no rendered-value assertion and no
  // snapshot can reach it, and a hand-maintained surface list silently stops
  // covering a newly added one. Scanning the module covers every branch, live or
  // gated. LIMIT, stated: it recognises the two phrasings the stale copy actually
  // used, so a fresh PARAPHRASE evades it — this repo deliberately rejects
  // semantic copy-nets (wizardPrompts.test.js header: eight reviews found ~41
  // defects in the net and none in the product), so the exact-match snapshot is
  // the guard for rendered copy and this scan is the backstop for gated branches.
  // The server-policy sentence is unaffected — it says `default-ON`, which is
  // TRUE of the organization toggle and is not the old claim.
  const src = readFileSync(new URL('./harnesses.js', import.meta.url), 'utf8')
  assert.doesNotMatch(src, /recording is\s+ON by default|files every session/i,
    'harnesses.js still claims capture happens by default somewhere — #3615 made ' +
    'every in-repo hook fail closed on the explicit opt-in; state it via captureInstallNote')
  // The gated-off claude-web paragraph is reachable by no render, so it is pinned
  // as the exported constant itself (the source scan above is the only other net).
  assert.match(CLAUDE_WEB_FILING, /nothing is filed unless you call it/,
    'the claude-web filing paragraph must state that filing is the agent\'s own call')
  // The refusal fact and the variable's ABSENCE are load-bearing: this path is
  // gated by the agent's own call plus the team toggle, NOT by TORTOISE_CAPTURE,
  // so the paragraph must keep the 409 and must never name the hook's variable
  // (a phrase-shape regex alone left "can refuse the file" green after the 409
  // was deleted — caught in review).
  assert.match(CLAUDE_WEB_FILING, /409/,
    'the claude-web filing paragraph must keep the server-side refusal (409) — the toggle can only refuse')
  assert.doesNotMatch(CLAUDE_WEB_FILING, /TORTOISE_CAPTURE/,
    'the claude-web path is not gated by TORTOISE_CAPTURE — its paragraph must not name the variable')
  assert.doesNotMatch(CLAUDE_WEB_FILING, /on by default/i,
    'the claude-web filing paragraph must not claim capture happens by default')
  // The one deliberate exception, pinned so a future edit cannot quietly
  // generalize the hook wording onto Pi: its extension reads no
  // TORTOISE_CAPTURE (installing it IS the opt-in), so its copy must state the
  // install IS the opt-in rather than telling the user to export the variable.
  assert.match(HARNESS_CAPTURE_INSTALL.pi, /Installing this extension IS the opt-in/,
    'the Pi seam has no consent gate — its copy must say installing it IS the opt-in')
  assert.doesNotMatch(HARNESS_CAPTURE_INSTALL.pi, /Filing is OFF until this machine opts in/,
    'the Pi seam must not carry the hook wording: it reads no TORTOISE_CAPTURE')
})

test('#9660: WORKFLOWS_PROMPT is a pointer to the served skills, never a second rendering', () => {
  // The defect this pins: the constant used to be a hand-written PROSE SUMMARY
  // of the three served skills — content whose source of truth is the
  // sha256-pinned mirror (`config/served-skills-upstream.yml`, #4643), restated
  // one layer up where nothing could re-derive it, so it could only rot.
  //
  // EXACT, DERIVED assertions only — deliberately no prose sweep over rule
  // words. This file records why (see the #4365 note at the top): a shape net
  // over agent-facing prose produced ~41 defects IN THE NET and none in the
  // product across eight reviews. What is demonstrable is the SET and the
  // ORIGIN, so that is all that is asserted.
  const served = SKILLS_LIST.split(', ')
  assert.equal(served.length, 3, 'precondition: the shipped set is three skills')
  for (const name of served) {
    assert.ok(WORKFLOWS_PROMPT.includes(servedSkillUrl(name)),
      `the prompt must point at the served skill '${name}' — the set it names is SKILLS_LIST's`)
  }
  // Every URL the prompt carries must be a served skill document on the ONE
  // origin; a second host (or a docs/raw-github link) would be a second source.
  const urls = WORKFLOWS_PROMPT.match(/https:\/\/[^\s)]+/g) || []
  assert.equal(urls.length, served.length,
    `the prompt must carry exactly one URL per served skill, got ${urls.length}: ${urls}`)
  for (const url of urls) {
    assert.ok(url.startsWith(`${SERVED_SKILLS_ORIGIN}/skills/`),
      `unexpected origin in the workflows prompt: ${url}`)
  }
  // The module declares the served origin ONCE. These two literals predate it
  // (`_dashboard_constant` reads them in single-quoted form), so they are pinned
  // to it rather than rewritten — one origin, no second host.
  assert.ok(SKILLS_INSTALL_URL.startsWith(`${SERVED_SKILLS_ORIGIN}/`),
    'SKILLS_INSTALL_URL must live on SERVED_SKILLS_ORIGIN')
  assert.equal(ONBOARDING_INSTRUCTIONS_URL, servedSkillUrl('tortoise-onboarding'),
    'ONBOARDING_INSTRUCTIONS_URL must be the servedSkillUrl form of the same document')
  // The live connector leaves still receive the pointer (Claude Desktop/Web via
  // wizardWorkflowsText), so the user-facing prompt cannot lose it silently.
  assert.ok(wizardWorkflowsText('', 'included').includes(WORKFLOWS_PROMPT),
    'the live workflows prompt must still start from WORKFLOWS_PROMPT')
})
