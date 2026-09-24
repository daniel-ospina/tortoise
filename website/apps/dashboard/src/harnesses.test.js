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
  HARNESS_CAPTURE_STATUS_LABEL, HARNESS_CAPTURE_SUPPORT, HARNESS_CAPTURE_SEAM,
  PI_CAPTURE_INSTALL,
  HARNESS_OAUTH, CANONICAL_MCP_URL, ONBOARDING_INSTRUCTIONS_URL, SKILLS_CLAIM,
  SKILLS_LIST,
  HARNESS_FAMILIES, HARNESS_FAMILY_IDS, harnessFamilyOf, preferredSurface,
  harnessDisplayName, knownHarnessName,
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
  // #3428/#2937 (lane B3, review cycle 1 P2-3): the dashboard Continue click no
  // longer writes the checkpoint (the human writer is deleted), so the copy must
  // name the real writer instead of promising the click does it.
  assert.match(desktop, /first successful write/, 'desktop: the real checkpoint writer is named')
  assert.ok(!desktop.includes('harness-connected checkpoint'),
    'desktop: the deleted human writer (#3428) must not be promised')
  const web = UNIVERSAL_COMMAND['claude-web'](KEY)
  assert.match(web, /Connectors/, 'web: connector steps')
  assert.match(web, /Server URL/, 'web: server URL step')
  assert.match(web, /https:\/\/api\.premiselabs\.co\/mcp[^\/]/, 'web: canonical connector URL (no slash)')
  assert.match(web, /Authorize/, 'web: OAuth Authorize step')
  assert.match(web, /tortoise_health/, 'web: agent verifies')
  // #3428/#2937 (lane B3, review cycle 1 P2-3): retargeted off the deleted
  // human writer — the copy now names the agent's first successful write.
  assert.match(web, /first successful write/, 'web: the real checkpoint writer is named')
  assert.ok(!web.includes('harness-connected checkpoint'),
    'web: the deleted human writer (#3428) must not be promised')
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
  // #3428/#2937 (lane B3, review cycle 1 P2-3): retargeted off the deleted
  // human writer — the copy now names the agent's first successful write.
  assert.match(cmd, /first successful write/, 'chatgpt: the real checkpoint writer is named')
  assert.ok(!cmd.includes('harness-connected checkpoint'),
    'chatgpt: the deleted human writer (#3428) must not be promised')
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
    // The install step may live in the MCP-setup copy (Pi/Codex embed it) or
    // in the capture-install surface (Cursor's copy is a JSON file, so its
    // capture step is a connect-wizard step + the Memory-sources row).  Either
    // surface must name the declared artifact — capability is not a claim.
    const surface = `${HARNESS_INSTALL[h](KEY)}\n${HARNESS_CAPTURE_INSTALL[h] || ''}`
    assert.ok(
      surface.includes(artifact),
      `HARNESS_INSTALL/${h} capture-install surface must install its declared seam ${artifact}`,
    )
  }
})

test('#3575: HARNESS_INSTALL.pi installs the in-repo Pi capture extension', () => {
  const pi = HARNESS_INSTALL.pi(KEY)
  assert.match(pi, /tortoise\/pi-hooks\/tortoise-capture\.ts/)
  assert.match(pi, /\.pi\/agent\/extensions/)
  // the Memory-sources inline row installs the SAME seam (one shared constant)
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
