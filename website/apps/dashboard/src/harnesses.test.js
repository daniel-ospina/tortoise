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
} from './harnesses.js'

const KEY = 'tt_w2_test_key'

test('DE2E-5: the 6-harness vocabulary — self-install (4) + teach-human (2) cover HARNESS_ORDER exactly', () => {
  assert.equal(HARNESS_ORDER.length, 6)
  assert.deepEqual([...HARNESS_ORDER].sort(), [...Object.keys(HARNESS_NAMES)].sort())
  const split = [...HARNESS_SELF_INSTALL, ...HARNESS_TEACH_HUMAN].sort()
  assert.deepEqual(split, [...HARNESS_ORDER].sort(), 'self-install ∪ teach-human == all 6')
  const overlap = HARNESS_SELF_INSTALL.filter((h) => HARNESS_TEACH_HUMAN.includes(h))
  assert.equal(overlap.length, 0, 'self-install and teach-human are disjoint')
  assert.deepEqual(HARNESS_SELF_INSTALL.sort(), ['claude', 'codex', 'cursor', 'pi'].sort())
  assert.deepEqual(HARNESS_TEACH_HUMAN.sort(), ['claude-desktop', 'claude-web'].sort())
})

test('UNIVERSAL_COMMAND covers all 6 harnesses (one command per harness)', () => {
  assert.deepEqual(UNIVERSAL_COMMAND_HARNESSES, HARNESS_ORDER)
  for (const h of HARNESS_ORDER) {
    assert.equal(typeof UNIVERSAL_COMMAND[h], 'function', `${h} universal command`)
    const cmd = UNIVERSAL_COMMAND[h](KEY)
    assert.ok(cmd && cmd.length > 0, `${h} command non-empty`)
  }
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
    assert.match(cmd, /harness-connected/, `${h}: harness-connected checkpoint`)
  }
})

test('DE2E-5: 2 teach-human harnesses carry exact manual steps + verify handoff', () => {
  const desktop = UNIVERSAL_COMMAND['claude-desktop'](KEY)
  assert.match(desktop, /claude_desktop_config\.json/, 'desktop: config file named')
  assert.match(desktop, /mcpServers/, 'desktop: mcpServers block')
  assert.match(desktop, /Restart Claude Desktop/, 'desktop: restart step')
  assert.match(desktop, /tortoise_health/, 'desktop: agent verifies')
  const web = UNIVERSAL_COMMAND['claude-web'](KEY)
  assert.match(web, /Connectors/, 'web: connector steps')
  assert.match(web, /Server URL/, 'web: server URL step')
  assert.match(web, /tortoise_health/, 'web: agent verifies')
  assert.match(web, /harness-connected/, 'web: checkpoint handoff (dashboard Continue)')
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
