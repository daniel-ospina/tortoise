// harnesses.test.js — run with node --test (Node 20+, zero deps: pure module,
// no jsdom/React needed) (#1998 W2 — universal command, surface 5).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  HARNESS_ORDER, HARNESS_NAMES, HARNESS_SELF_INSTALL, HARNESS_TEACH_HUMAN,
  UNIVERSAL_COMMAND, UNIVERSAL_COMMAND_HARNESSES,
  HARNESS_INTRO, HARNESS_INSTALL, HARNESS_STEPS, HARNESS_SKILLS, HARNESS_PERSIST,
  HARNESS_SKILLLESS, HARNESS_SKILLS_IN_PROMPT, HARNESS_SKILLS_IN_STEPS,
  HARNESS_COPY_LABEL, HARNESS_CONTINUE_LABEL,
  HARNESS_CAPTURE_INSTALL, HARNESS_CAPTURE_REASON,
  HARNESS_CAPTURE_STATUS_LABEL, HARNESS_CAPTURE_SUPPORT,
  HARNESS_OAUTH, CANONICAL_MCP_URL,
  HARNESS_FAMILIES, HARNESS_FAMILY_IDS, harnessFamilyOf, preferredSurface,
  harnessDisplayName,
} from './harnesses.js'

const KEY = 'tt_w2_test_key'

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

test('UNIVERSAL_COMMAND covers all 7 harnesses (one command per harness)', () => {
  assert.deepEqual(UNIVERSAL_COMMAND_HARNESSES, HARNESS_ORDER)
  for (const h of HARNESS_ORDER) {
    assert.equal(typeof UNIVERSAL_COMMAND[h], 'function', `${h} universal command`)
    const cmd = UNIVERSAL_COMMAND[h](KEY)
    assert.ok(cmd && cmd.length > 0, `${h} command non-empty`)
  }
})

// #2912: the wizard's two-level chooser is a UI grouping over the SAME leaf
// vocabulary — this pins that invariant (a family/surface id that is not a real
// leaf would render a chooser entry with no payload behind it).
test('#2912: HARNESS_FAMILIES groups the leaf vocabulary (Claude 3 + Codex 2 + Cursor/Pi)', () => {
  assert.deepEqual(HARNESS_FAMILY_IDS, ['claude', 'codex', 'cursor', 'pi'])
  const surfaceIds = HARNESS_FAMILIES.flatMap((f) => f.surfaces.map((s) => s.id))
  const claude = HARNESS_FAMILIES.find((f) => f.id === 'claude')
  const codex = HARNESS_FAMILIES.find((f) => f.id === 'codex')
  assert.deepEqual(claude.surfaces.map((s) => s.id), ['claude', 'claude-desktop', 'claude-web'])
  assert.deepEqual(codex.surfaces.map((s) => s.id), ['codex', 'codexDesktop'])
  // Cursor/Pi are single-choice: family id == leaf id, no surface row.
  for (const id of ['cursor', 'pi']) {
    assert.deepEqual(HARNESS_FAMILIES.find((f) => f.id === id).surfaces, [])
  }
  // Every chooser id resolves to a real leaf — 'codexDesktop' is the ONE
  // UI-only leaf (#2328) and is the only id outside HARNESS_ORDER.
  for (const id of [...HARNESS_FAMILY_IDS, ...surfaceIds]) {
    assert.ok(HARNESS_ORDER.includes(id) || id === 'codexDesktop',
      `${id} must be a real leaf (HARNESS_ORDER member or the Codex Desktop surface)`)
  }
  // The families cover the wizard's whole leaf surface (chatgpt is OAuth-only).
  const covered = new Set([
    ...HARNESS_FAMILIES.flatMap((f) => (f.surfaces.length ? f.surfaces.map((s) => s.id) : [f.id])),
  ])
  for (const h of HARNESS_ORDER.filter((h) => h !== 'chatgpt')) {
    assert.ok(covered.has(h), `${h} must be reachable from the chooser`)
  }
})

test('#2912: family resolution + preferred surface + display names', () => {
  assert.equal(harnessFamilyOf('claude-desktop').id, 'claude')
  assert.equal(harnessFamilyOf('claude-web').id, 'claude')
  assert.equal(harnessFamilyOf('codexDesktop').id, 'codex')
  assert.equal(harnessFamilyOf('cursor').id, 'cursor')
  assert.equal(harnessFamilyOf('chatgpt'), null)
  const claude = HARNESS_FAMILIES.find((f) => f.id === 'claude')
  const cursor = HARNESS_FAMILIES.find((f) => f.id === 'cursor')
  // re-clicking the active family keeps the user's surface (non-destructive)
  assert.equal(preferredSurface(claude, 'claude-web'), 'claude-web')
  // switching families lands on the terminal/self-install surface first
  assert.equal(preferredSurface(claude, 'pi'), 'claude')
  assert.equal(preferredSurface(cursor, 'pi'), 'cursor')
  assert.equal(harnessDisplayName('codexDesktop'), 'Codex Desktop')
  assert.equal(harnessDisplayName('claude-desktop'), 'Claude Desktop')
})

test('#2328/#2329: Codex Desktop variant — terminal-less config path, .agents/skills, no .codex/skills', () => {
  const cli = UNIVERSAL_COMMAND.codex(KEY)
  assert.match(cli, /codex mcp add tortoise --url/, 'codex CLI: codex mcp add')
  assert.match(cli, /--bearer-token-env-var TORTOISE_API_KEY/, 'codex CLI: env-var bearer')
  // the CLI copy surfaces the approval reality (#2330) and points Desktop
  // users at the no-terminal variant
  assert.match(cli, /approval/, 'codex CLI states first-call approval reality')
  assert.match(cli, /Codex Desktop/, 'codex CLI points Desktop users at the variant')
  const desktop = UNIVERSAL_COMMAND.codexDesktop(KEY)
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
  for (const t of [desktop, cli]) {
    if (t.includes('.codex/skills')) {
      assert.ok(t.includes('not .codex/skills'), '.codex/skills only as negated disambiguation')
    }
  }
  assert.equal(HARNESS_COPY_LABEL.codexDesktop, 'Copy instructions')
  assert.ok(HARNESS_INTRO.codexDesktop && HARNESS_INTRO.codexDesktop.includes('~/.codex/config.toml'), 'desktop intro names the config file')
})

test('DE2E-5: 4 self-install harnesses carry a config-write command + skill install + tortoise_health verify', () => {
  const claude = UNIVERSAL_COMMAND.claude(KEY)
  assert.match(claude, /claude mcp add --transport http tortoise/, 'claude: claude mcp add --transport http')
  assert.match(claude, /https:\/\/api\.premiselabs\.co\/mcp\//, 'claude: MCP url')
  const codex = UNIVERSAL_COMMAND.codex(KEY)
  assert.match(codex, /codex mcp add tortoise --url/, 'codex: codex mcp add')
  assert.match(codex, /--bearer-token-env-var TORTOISE_API_KEY/, 'codex: env-var bearer')
  const cursor = UNIVERSAL_COMMAND.cursor()
  assert.match(cursor, /\.cursor\/mcp\.json/, 'cursor: config file path')
  assert.match(cursor, /\$\{env:TORTOISE_API_KEY\}/, 'cursor: env: indirection')
  const pi = UNIVERSAL_COMMAND.pi(KEY)
  assert.match(pi, /\.mcp\.json/, 'pi: config file path')
  assert.match(pi, /\$\{TORTOISE_API_KEY\}/, 'pi: plain ${VAR} indirection')
  for (const h of HARNESS_SELF_INSTALL) {
    const cmd = UNIVERSAL_COMMAND[h](KEY)
    assert.match(cmd, /install-tortoise-skills\.sh/, `${h}: skill install line`)
    assert.match(cmd, /tortoise_health/, `${h}: tortoise_health verify`)
  }
  // Post-#593 (auto-complete on first agent write): the 3 key-config
  // harnesses still hand off with the harness-connected checkpoint phrase;
  // Pi's copy dropped the checkpoint ceremony (onboarding auto-completes on
  // the first graph write) — it must end on the eager-connect verify instead.
  for (const h of ['claude', 'codex', 'cursor']) {
    const cmd = UNIVERSAL_COMMAND[h](KEY)
    assert.match(cmd, /harness-connected/, `${h}: harness-connected checkpoint`)
  }
  assert.match(pi, /connected/, 'pi: connect verify sentence (no checkpoint ceremony)')
  assert.ok(!pi.includes('harness-connected'), 'pi: checkpoint handoff copy removed (#593)')
})

test('DE2E-5: teach-human harnesses carry exact manual steps + verify handoff (Claude Desktop/Web)', () => {
  // #2865: both Claude connector surfaces are key-less OAuth — no `Authorization`
  // request header, no API key, and the canonical NO-slash connector URL that
  // matches the server's RFC 8707 resource indicator (#2864).
  const desktop = UNIVERSAL_COMMAND['claude-desktop'](KEY)
  assert.match(desktop, /Connectors/, 'desktop: Connectors UI named')
  assert.match(desktop, /Server URL/, 'desktop: server URL field')
  assert.match(desktop, /https:\/\/api\.premiselabs\.co\/mcp[^\/]/, 'desktop: canonical connector URL (no slash)')
  assert.match(desktop, /Authorize/, 'desktop: OAuth Authorize step')
  assert.match(desktop, /tortoise_health/, 'desktop: agent verifies')
  assert.ok(!desktop.includes('Authorization'), 'desktop: no Bearer recipe (that is the blocker)')
  assert.match(desktop, /Leave Request headers empty/, 'desktop: the field is named only to say it stays empty')
  assert.ok(!/beta/i.test(desktop), 'desktop: no beta caveat')
  assert.ok(!desktop.includes(KEY), 'desktop: never embeds the key')
  const web = UNIVERSAL_COMMAND['claude-web'](KEY)
  assert.match(web, /Connectors/, 'web: connector steps')
  assert.match(web, /Server URL/, 'web: server URL step')
  assert.match(web, /https:\/\/api\.premiselabs\.co\/mcp[^\/]/, 'web: canonical connector URL (no slash)')
  assert.match(web, /Authorize/, 'web: OAuth Authorize step')
  assert.match(web, /tortoise_health/, 'web: agent verifies')
  assert.match(web, /harness-connected/, 'web: checkpoint handoff (dashboard Continue)')
  assert.ok(!web.includes('Authorization'), 'web: no Bearer recipe')
  assert.ok(!web.includes(KEY), 'web: never embeds the key')
  // #2865: the Continue label tells the truth on a manual connector surface.
  assert.equal(HARNESS_CONTINUE_LABEL['claude-desktop'], "I've connected it — Continue →")
  assert.equal(HARNESS_CONTINUE_LABEL['claude-web'], "I've connected it — Continue →")
})

test('#1701 DE2E-5: chatgpt is the key-less OAuth harness — OAuth connector steps + skills-as-prompt copy', () => {
  // connector steps (rendered above the snippet) name the Developer-mode
  // surface and the OAuth choice; the URL is the NO-slash form
  const steps = HARNESS_STEPS('chatgpt', KEY)
  assert.ok(Array.isArray(steps) && steps.length >= 7, 'chatgpt step list present')
  assert.ok(steps.some((s) => typeof s === 'string' && s.includes('Developer mode')), 'steps: Developer mode')
  assert.ok(steps.some((s) => typeof s === 'string' && s.includes('chatgpt.com/plugins')), 'steps: plugins surface')
  const urlStep = steps.find((s) => typeof s === 'object' && s.copy === 'https://api.premiselabs.co/mcp')
  assert.ok(urlStep, 'steps: MCP URL copy step (exact, no slash)')
  assert.ok(steps.some((s) => typeof s === 'string' && s.includes('OAuth')), 'steps: OAuth choice named')
  // the legacy HARNESS_INSTALL copy for chatgpt is the shared skills-as-prompt
  // body ONLY — byte-identical with Claude Web's (drift pin on WORKFLOWS_PROMPT)
  const prompt = HARNESS_INSTALL.chatgpt()
  assert.equal(prompt, HARNESS_INSTALL['claude-web'](), 'claude-web and chatgpt share the identical workflows body')
  assert.match(prompt, /Follow these workflows/, 'prompt: workflows marker')
  // the self-contained UNIVERSAL_COMMAND block embeds the connector steps +
  // prompt + a USER-FACING in-chat verify (no server signal — chatgpt has no
  // tortoise_health call)
  const cmd = UNIVERSAL_COMMAND.chatgpt()
  assert.match(cmd, /Developer mode/, 'command: Developer mode')
  assert.match(cmd, /chatgpt\.com\/plugins/, 'command: plugins surface')
  assert.match(cmd, /https:\/\/api\.premiselabs\.co\/mcp[^\/]/, 'command: exact MCP URL (no trailing slash)')
  assert.match(cmd, /OAuth/, 'command: OAuth')
  assert.match(cmd, /Follow these workflows/, 'command: workflows prompt embedded')
  assert.match(cmd, /are we connected\?/, 'command: in-chat verify question')
  assert.match(cmd, /harness-connected/, 'command: checkpoint handoff (dashboard Continue)')
  assert.ok(!cmd.includes('tt_'), 'chatgpt copy must never carry a key prefix')
  assert.ok(!cmd.includes(KEY), 'chatgpt copy must never embed the test key')
  assert.ok(!cmd.includes('tortoise_health'), 'chatgpt copy verifies IN CHAT — never tortoise_health')
  assert.ok(!cmd.includes('api.premiselabs.co/mcp/'), 'chatgpt copy must not use the slash URL form')
  // #2865: ONE canonical connector constant — chatgpt, claude-desktop and
  // claude-web all carry the no-slash form (the keyed MCP_URL stays slashed).
  assert.equal(CANONICAL_MCP_URL, 'https://api.premiselabs.co/mcp')
  for (const h of ['chatgpt', 'claude-desktop', 'claude-web']) {
    assert.ok(UNIVERSAL_COMMAND[h](KEY).includes(CANONICAL_MCP_URL),
      `${h}: connector copy carries the canonical no-slash URL`)
  }
  assert.equal(HARNESS_COPY_LABEL.chatgpt, 'Copy prompt')
  assert.equal(HARNESS_CONTINUE_LABEL.chatgpt, "I've connected it — Continue →")
  assert.ok(HARNESS_INTRO.chatgpt && HARNESS_INTRO.chatgpt.includes('paste the prompt'), 'chatgpt intro points at the chat paste')
  // session capture: disabled-with-reason (cloud-hosted — no local signal)
  assert.equal(HARNESS_CAPTURE_SUPPORT.chatgpt, false)
  assert.ok(HARNESS_CAPTURE_REASON.chatgpt && HARNESS_CAPTURE_REASON.chatgpt.length > 0,
    'chatgpt capture row renders a reason (never undefined)')
})

test('no literal tt_ key in project-scoped/committable configs (env-var indirection)', () => {
  // cursor + pi configs are project files (.cursor/mcp.json, .mcp.json) —
  // the JSON blocks rendered inside the commands reference the env var,
  // never the key. The full pi copy legitimately carries the key once (the
  // profile export line — same as claude/codex CLI commands); the
  // committable JSON stays key-free.
  const cursorCmd = UNIVERSAL_COMMAND.cursor()
  assert.ok(!cursorCmd.includes('tt_'), 'cursor command must not embed the key')
  assert.ok(!cursorCmd.includes(KEY), 'cursor command must not embed the key')
  assert.match(cursorCmd, /\$\{env:TORTOISE_API_KEY\}/, 'cursor JSON references env:')
  const piCmd = UNIVERSAL_COMMAND.pi(KEY)
  // the key appears EXACTLY once — the profile export line; the committable
  // .mcp.json JSON block references the env var, never the key
  const keyLines = piCmd.split('\n').filter((l) => l.includes(KEY))
  assert.equal(keyLines.length, 1, 'pi copy must carry the key exactly once (export line)')
  assert.match(keyLines[0], /Add TORTOISE_API_KEY=/, 'the single key use is the profile export')
  assert.ok(piCmd.includes('"Bearer ${TORTOISE_API_KEY}"'), 'pi JSON references the env var')
})

test('DE2E-2 copy sweep: universal command copy never says "team"/"workspace"', () => {
  const all = HARNESS_ORDER.map((h) => UNIVERSAL_COMMAND[h](KEY)).join(' ')
  assert.ok(!/\bteam\b/i.test(all), 'no "team" in universal command copy')
  assert.ok(!/workspace/i.test(all), 'no "workspace" in universal command copy')
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
  assert.match(HARNESS_INSTALL.claude(KEY), /claude mcp add/)
})
