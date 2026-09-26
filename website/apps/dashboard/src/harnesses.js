// #1643: per-harness MCP onboarding data (ported from welcome.html's
// HARNESS_* constants). Single source for the wizard's harness chooser;
// env-indirection configs keep the raw key out of config files (#529 J5/T7b).
// #984/#2849: the KEYED-harness URL — the CLI self-install surfaces
// (claude / codex / codexDesktop / cursor / pi) register this slashed form
// deliberately: it is a #984-era convention pinned by six assertions in
// tests/test_harness_mcp_config.py. It is NOT the Claude connector URL.
export const MCP_URL = 'https://api.premiselabs.co/mcp/'

// #2865 (lifted from #2864's plan, Task 2 Step 3): the CANONICAL CONNECTOR
// URL — no trailing slash. This is the exact value of the OAuth resource
// indicator (`tortoise/oauth.py::mcp_resource_url` = base + '/mcp') that the
// protected-resource-metadata document advertises, so a connector configured
// with it matches `resource` byte-for-byte. Before #2864 the PRM said '/mcp'
// while every connector surface said '/mcp/' — that mismatch is what #2849 was
// filed for. CONNECTOR surfaces (the two Claude leaves + ChatGPT) must use
// THIS value; keyed surfaces keep MCP_URL above. #2864 removed the
// /mcp → /mcp/ 307 and `parse_resource` accepts both forms, so an existing
// connector with the slashed URL keeps working — no re-add is required.
export const CANONICAL_MCP_URL = 'https://api.premiselabs.co/mcp'

// #1701: the name the shipped chatgpt copy still uses — same value.
const CHATGPT_MCP_URL = CANONICAL_MCP_URL

// #1701: the skills-as-prompt body shared by every filesystem-less harness
// that therefore has no local skills to install — Claude Web, Claude Desktop
// (#2827) and ChatGPT. ONE constant so the user-facing prompts can never
// drift. Composed by wizardWorkflowsText in the dashboard.
export const WORKFLOWS_PROMPT =
  `You have Tortoise connected (the 'tortoise' MCP tools). Follow these workflows:\n\n1) Writing to the graph — Tortoise stores knowledge as points with edges: IMPL means 'supports', NAND means 'contradicts'. Mitigations reduce confidence (range 0.10–0.50). To change a point, supersede it and clean up its active edges rather than editing in place. Prefer structural claims over labels and always cite provenance.\n\n2) Decisions — to make a decision, first refine it, then research the options, the criteria that matter, and the findings/evidence, then wire IMPL/NAND edges from findings and criteria to options (mitigate an edge, range 0.10–0.50, when it's true but matters less), and rank the options by EP confidence.\n\n3) Research findings — when I share a research finding, ingest it as a point, check for existing related claims first, and surface connections to what we already know.`

// #1727 (Task 13): per-harness session-capture support gate — the single
// source of truth consumed by BOTH the dashboard's per-harness sessions
// toggle AND the conditional claude-web prompt paragraph below (flipped in
// the same slice/commit as the dist rebuild).
//
// #3575: `true` is DERIVED from HARNESS_CAPTURE_SEAM below, never asserted.
// A harness is capturable only when the product actually INSTALLS a capture
// step: (a) HARNESS_CAPTURE_SEAM names an in-repo artifact, (b) that artifact
// is committed, and (c) HARNESS_INSTALL[h] installs it. A `true` with no
// installed step is a false PASS — the #3575 defect (Pi advertised capture
// while HARNESS_INSTALL.pi delivered MCP config + skills only). All three
// legs are pinned by harnesses.test.js and tests/test_harness_mcp_config.py.
//
// The Claude-Web filing-path spike verdict (Slice 2, Task 13): the MCP
// custom-connector path CAN expose tortoise_session_capture to claude.ai
// workflows prompts, but the plan pins that disclosure-only is NOT a
// terminal state — the web row stays disabled-with-reason until a
// SERVER-VISIBLE web signal is confirmed (a web install-probe variant or
// observed web-harness POSTs; workflows-prompt presence alone is client-
// side and unpinnable). Until then web = false.
//
// The capture-INSTALL SEAM (#1727 T1): harness → the in-repo artifact
// HARNESS_INSTALL[h] installs. A harness with a seam entry fires a
// server-visible install-probe and files sessions with the same harness +
// session_id + conversation shape. Which harnesses have it:
//   claude = tortoise/claude-hooks/session-{start,end}.sh copied into
//            .claude/hooks + wired in .claude/settings.json
//            (SessionStart install-probe + SessionEnd capture)
//   codex  = tortoise/codex-hooks/session-end.sh copied into
//            $CODEX_HOME/hooks + wired in $CODEX_HOME/hooks.json
//            (SessionEnd capture; the detaching hook is Codex 0.154.0's
//            ~1 s SessionEnd budget, measured live)
//   pi     = tortoise/pi-hooks/tortoise-capture.ts copied into
//            ~/.pi/agent/extensions/ (extension session_start install-probe +
//            session_shutdown capture; recording ON by default)
//   cursor = tortoise/cursor-hooks/session-end.sh copied into
//            ~/.cursor/hooks + wired in hooks.json under
//            "sessionEnd" (Cursor 3.20.21 reads hooks.json from the HOME-scoped
//            .cursor/ dir; the entry is a FLAT {"command": …} object).
//            IDE-ONLY: cloud agents have no editor-lifetime session boundary.
// No other harness has a seam: claude-desktop is backfill-import only, and
// web/chatgpt are cloud-hosted.
export const HARNESS_CAPTURE_SEAM = {
  claude: 'tortoise/claude-hooks/session-end.sh',
  codex: 'tortoise/codex-hooks/session-end.sh',
  cursor: 'tortoise/cursor-hooks/session-end.sh',
  pi: 'tortoise/pi-hooks/tortoise-capture.ts',
}

const CAPTURE_SEAM_HARNESSES = new Set(Object.keys(HARNESS_CAPTURE_SEAM))

export const HARNESS_CAPTURE_SUPPORT = {
  claude: CAPTURE_SEAM_HARNESSES.has('claude'),
  'claude-desktop': false,  // backfill import only (Task 15) — no live install path
  'claude-web': false,      // disabled-with-reason pending the Task 13 spike signal
  codex: CAPTURE_SEAM_HARNESSES.has('codex'),
  // #3819: capture is possible for LOCAL/IDE sessions (sessionEnd + a
  // transcript_path/agent-transcripts store). `true` here is DERIVED from the
  // seam, never asserted; the IDE-only limitation is disclosed on the install
  // surface, not encoded as a false here.
  cursor: CAPTURE_SEAM_HARNESSES.has('cursor'),
  pi: CAPTURE_SEAM_HARNESSES.has('pi'),
  chatgpt: false,        // #1701: cloud-hosted — no server-visible filing signal
}

const CURSOR_MCP_CONFIG_ENV = {
  mcpServers: {
    tortoise: {
      url: MCP_URL,
      headers: { Authorization: 'Bearer ${env:TORTOISE_API_KEY}' },
    },
  },
}

// pi's mcp-client expands plain ${VAR} only — an env: prefix would
// yield an empty Bearer header (verified against the mcp-client extension).
const PI_MCP_CONFIG_ENV = {
  mcpServers: {
    tortoise: {
      url: MCP_URL,
      headers: { Authorization: 'Bearer ${TORTOISE_API_KEY}' },
    },
  },
}

// #1694: per-harness UI steps shown above the snippet (NOT part of the
// copied content). The user follows these, then copies the payload below
// (prompt / commands / file JSON). Step objects support { label, code,
// copy } — label text, an inline <code> value, and a one-click Copy
// button for that value. A function of (harness, key) so steps can embed
// the real key (Cursor's export step).
export const HARNESS_STEPS = (harness, key) => ({
  // #2865: Claude Web connects key-less over OAuth (HARNESS_OAUTH) — no
  // Request-headers field, no API key. The canonical no-slash URL matches the
  // server's resource indicator.
  'claude-web': [
    'Go to claude.ai > Settings > Connectors',
    'Add custom connector and name it "Tortoise"',
    { label: 'Server URL', code: CANONICAL_MCP_URL, copy: CANONICAL_MCP_URL },
    'No API key is needed — leave Request headers empty.',
    'Sign in to Tortoise when Claude opens the authorization page and click Authorize, then pick your Organization.',
    'Paste the prompt below into a Claude Web chat — it gives Claude the Tortoise workflows:',
    '(Claude connects from its own cloud — the authorization lives with Anthropic, not on your machine. No local skills on web — the prompt gives Claude the workflows.)',
  ],
  cursor: [
    { label: 'Export the key — add this line to your shell profile (~/.zshrc or ~/.bashrc) so it persists:', code: `export TORTOISE_API_KEY=${key}`, copy: `export TORTOISE_API_KEY=${key}` },
    'Create .cursor/mcp.json in this project with the JSON below — the config references the env var, not the key:',
    { label: `${SKILLS_CLAIM}:`, code: `curl -fsSL ${SKILLS_INSTALL_URL} | bash -s -- --harness cursor`, copy: `curl -fsSL ${SKILLS_INSTALL_URL} | bash -s -- --harness cursor` },
    // #3819 (owner ruling): the IDE-only limitation is disclosed on the
    // install/connect surface, not buried in a footnote.
    { label: 'Install session capture (the sessionEnd hook):', code: 'tortoise install cursor', copy: 'tortoise install cursor' },
    "Session capture is IDE-ONLY: local desktop-editor sessions are captured, but CURSOR CLOUD AGENT sessions are not — Cursor's docs: 'Cloud agents have no editor-lifetime session boundary. sessionEnd is tied to the IDE session, not a cloud agent chat.'",
  ],
  chatgpt: [
    'Enable Developer mode: chatgpt.com → Settings → Security and login → Developer mode (Plus/Pro/Business/Enterprise/Education).',
    'Open chatgpt.com/plugins → the + button → create a Developer-mode app.',
    { label: 'MCP server URL', code: CHATGPT_MCP_URL, copy: CHATGPT_MCP_URL },
    "Choose OAuth — ChatGPT discovers Tortoise's authorization server (no API key needed).",
    'Scan Tools — sign in to Tortoise when prompted and click Authorize.',
    'The tortoise_* tools appear (Developer mode); write actions ask for confirmation in chat.',
    'Paste the prompt below into a ChatGPT chat — it gives ChatGPT the Tortoise workflows.',
  ],
})[harness]

// #1710: per-harness short instruction shown ABOVE the snippet — what to
// do with the copy (run in a terminal / paste into the config file /
// paste into the agent). NOT part of the copied content.
export const HARNESS_INTRO = {
  claude: 'Run these commands in your terminal:',
  // #2865: both Claude connector surfaces are key-less OAuth — the intro names
  // the sign-in, never a Bearer request header.
  'claude-desktop': 'Open Claude Desktop → Settings → Connectors → Add custom connector, name it "Tortoise" and enter the Server URL. No API key is needed: Claude opens Tortoise\u2019s sign-in page on the first connection — sign in and click Authorize. (For local stdio servers only, advanced users can still edit ~/Library/Application Support/Claude/claude_desktop_config.json on macOS, or %APPDATA%\\Claude\\claude_desktop_config.json on Windows, from Settings → Developer in the app.)',
  // #2361 review-r1 (UX-3): Claude Web had no intro — the copy button said
  // 'Copy prompt' but nothing explained where the prompt goes.
  'claude-web': 'Start a new chat at claude.ai (or the Claude web app) and paste the prompt below into it — that conversation becomes your connected agent.',
  codex: 'Run these commands in your terminal:',
  // #2328: Desktop variant intro (no terminal).
  codexDesktop: 'Edit ~/.codex/config.toml (create it if missing) — Codex Desktop and the CLI share this file. Copy the block, paste it in, then fully quit and reopen Codex Desktop:',
  pi: 'Paste this into your Pi agent:',
  chatgpt: 'Start a new chat at chatgpt.com and paste the prompt below — that conversation becomes your connected agent. ChatGPT has no local skills — the prompt gives it the Tortoise workflows.',
}

export const HARNESS_NAMES = {
  claude: 'Claude Code',
  'claude-desktop': 'Claude Desktop',
  'claude-web': 'Claude Web',
  codex: 'Codex',
  cursor: 'Cursor',
  pi: 'Pi',
  chatgpt: 'ChatGPT',
}

// #2912: the connect step's title/label for a leaf id. 'codexDesktop' is a
// UI-only leaf (the Codex Desktop surface — #2328) that intentionally has no
// HARNESS_NAMES entry (it is not a separate install vocabulary), so the
// chooser needs one display name for it.
const HARNESS_EXTRA_NAMES = { codexDesktop: 'Codex Desktop' }

export const harnessDisplayName = (id) => HARNESS_NAMES[id] || HARNESS_EXTRA_NAMES[id] || id

// #3428 (lane B3, review cycle 1 P2-1): the connect step's fallback sentence
// ("Head back to your agent") is reserved for a harness we genuinely cannot
// name. `HARNESS_NAMES[id] || 'your agent'` sent a Codex Desktop user to the
// neutral fallback even though the surface IS known (HARNESS_EXTRA_NAMES), and
// `harnessDisplayName` is unusable unguarded because it falls back to the RAW
// id. This returns a display name for a KNOWN harness and null otherwise — the
// caller owns the user-facing fallback copy.
export const knownHarnessName = (id) => HARNESS_NAMES[id] || HARNESS_EXTRA_NAMES[id] || null

// Harnesses with no local file system for the file-based skills or shell
// profile (Claude Desktop/Web connect from the app/cloud — MCP only).
export const HARNESS_SKILLLESS = ['claude-desktop', 'claude-web', 'chatgpt']

// Harnesses whose install copy embeds the skill-install step in a self-
// contained prompt (Pi) — nothing extra is appended after the copy.
export const HARNESS_SKILLS_IN_PROMPT = ['pi']

// #3575: the Pi capture-INSTALL step — the in-repo extension that makes Pi
// sessions land in Tortoise Cloud. Shared by HARNESS_INSTALL.pi (the setup
// prompt) and HARNESS_CAPTURE_INSTALL.pi (the Memory-sources inline row) so
// the two surfaces can never drift. Recording is ON by default (ToS-covered
// — the same default as the Claude hooks); the server refuses the capture
// POST with a 409 while the organization has agent sessions switched off
// (Memory sources > Agent sessions). The extension fires an install-probe on
// load (harness + timestamp only, no content) and files the session when it
// ends. It has no agent-infra dependency and needs no local tortoise CLI.
export const PI_CAPTURE_INSTALL = `# Session capture for Pi (#1727 T1, #3575): install the in-repo capture
# extension. Recording is ON by default (ToS-covered) — switch it off in
# Memory sources > Agent sessions (the server then returns a 409). The
# extension probes on load (harness + timestamp only, no content) and files
# each session on exit. Install from your Tortoise checkout
# (github.com/daniel-ospina/tortoise):
mkdir -p ~/.pi/agent/extensions
# #3713 collision guard: Pi's loader treats a top-level tortoise-capture.ts
# AND a tortoise-capture/index.ts as TWO extensions (no basename dedupe).
# A pre-existing agent-infra tortoise-capture/ registers its own agent_end
# capture, so both POST the same session_id — doubled work, and the loser can
# 409. Disable the legacy entry before installing this one. NON-DESTRUCTIVE:
# unlink a symlink (the agent-infra checkout is untouched), or move a real
# directory to a dot-prefixed name Pi's loader SKIPS (it ignores dotfiles) —
# never a recursive delete.
if [ -L ~/.pi/agent/extensions/tortoise-capture ]; then
  rm ~/.pi/agent/extensions/tortoise-capture
elif [ -d ~/.pi/agent/extensions/tortoise-capture ]; then
  mv ~/.pi/agent/extensions/tortoise-capture ~/.pi/agent/extensions/.tortoise-capture.disabled
fi
cp <path-to-tortoise>/tortoise/pi-hooks/tortoise-capture.ts ~/.pi/agent/extensions/tortoise-capture.ts
# Backfill past Pi sessions with:
tortoise sessions import --harness pi --file <session.jsonl>`

// #3818: the Codex capture-INSTALL step — the in-repo SessionEnd hook that
// makes Codex sessions land in Tortoise Cloud. Shared by HARNESS_INSTALL.codex
// (the setup prompt) and HARNESS_CAPTURE_INSTALL.codex (the Memory-sources
// inline row) so the two surfaces can never drift. The registration is
// HOME-scoped ($CODEX_HOME/hooks.json): verified live against Codex CLI
// 0.154.0, a project-local .codex/hooks.json fires nothing. Codex runs a hook
// only once it is trusted; the CLI's SessionEnd budget is ~1 s, so the shipped
// hook detaches the capture POST and returns immediately.
export const CODEX_CAPTURE_INSTALL = `# Session capture (#3818): recording is on by default (ToS-covered); the
# SessionEnd hook files every session to Tortoise Cloud unless your
# organization switches it off (Memory sources > Agent sessions — the server
# returns a 409 while disabled). Codex reads hook registrations from
# $CODEX_HOME/hooks.json — the CODEX_HOME override moves the whole config
# tree, default ~/.codex — and NOT from a project .codex/, so this seam is
# home-scoped. Install from your Tortoise checkout
# (github.com/daniel-ospina/tortoise):
mkdir -p "\${CODEX_HOME:-$HOME/.codex}/hooks"
cp <path-to-tortoise>/tortoise/codex-hooks/session-end.sh "\${CODEX_HOME:-$HOME/.codex}/hooks/tortoise-session-end.sh"
chmod +x "\${CODEX_HOME:-$HOME/.codex}/hooks/tortoise-session-end.sh"
# then merge a SessionEnd command hook into "\${CODEX_HOME:-$HOME/.codex}/hooks.json"
# (create the file if missing). The command MUST be the script's ABSOLUTE path
# — Codex runs the hook from the session's cwd — and the entry is Codex's
# nested matcher-group shape (the exact JSON is in the shipped hook's header).
# Codex resolves no "timeout" key; the shipped hook detaches its capture POST
# and returns immediately, which is what fits Codex's ~1 s SessionEnd budget.
# Codex runs a hook only after you trust it: the first interactive run shows a
# review prompt (Hooks menu). Non-interactive runs need
#   codex exec --dangerously-bypass-hook-trust
# Or just run: tortoise install codex`

// #3819: the Cursor capture-INSTALL step — the in-repo sessionEnd hook that
// makes LOCAL Cursor desktop-editor sessions land in Tortoise Cloud. Shown in
// the Memory-sources inline row; the connect-wizard step (HARNESS_STEPS.cursor)
// carries the same disclosure. The registration is HOME-scoped
// (`~/.cursor/hooks.json`, Cursor's own user-scoped hook source; Cursor
// has NO config-dir env var — `CURSOR_HOME` does not exist) — a
// project-local `.cursor/hooks.json` is gated on workspace trust. Cursor's
// entry is a FLAT {"command": …} script object; its validator rejects a
// nested matcher group and invalidates the whole hooks.json.
export const CURSOR_CAPTURE_INSTALL = `# Session capture for Cursor (#3819): recording is on by default (ToS-covered);
# the sessionEnd hook files each LOCAL desktop-editor session to Tortoise Cloud
# unless your organization switches it off (Memory sources > Agent sessions —
# the server returns a 409 while disabled). Install from your Tortoise checkout
# (github.com/daniel-ospina/tortoise):
tortoise install cursor
# ...or by hand: copy tortoise/cursor-hooks/session-end.sh into
# "~/.cursor/hooks/" and add it under "sessionEnd" in
# "~/.cursor/hooks.json" as a FLAT script object:
#   {"command": "<abs-path>/tortoise-session-end.sh"}
# (Cursor's validator rejects a NESTED matcher-group entry and then loads
#  NO hooks at all — the entry must be flat.) The document ALSO needs a
#  numeric "version" (e.g. "version": 1): without it Cursor rejects the
#  whole hooks.json and loads no hooks. Run "tortoise hooks upgrade --harness
#  cursor" to set the version and the flat entry for you.
#
# ** IDE-ONLY — Cursor CLOUD AGENT sessions are NOT captured. **
# Cursor's docs: "Cloud agents have no editor-lifetime session boundary.
# sessionEnd is tied to the IDE session, not a cloud agent chat." The desktop
# editor is the supported capture surface for this seam; a cloud-agent chat
# has no sessionEnd to hook.`

// #1710: the copyable payload is EXACTLY what the user pastes into the
// harness target (terminal / config file / chat). The lead-in instructions
// ("Run this command:", "Paste this into...") live in HARNESS_INTRO /
// HARNESS_STEPS above the snippet — never in the copied text.
export const HARNESS_INSTALL = {
  claude: (key) =>
    `claude mcp add --transport http tortoise ${MCP_URL} --header "Authorization: Bearer ${key}"

# Session capture (#1727 T1): recording is on by default (ToS-covered); the
# hooks file every session to Tortoise Cloud unless your organization switches it
# off (Memory sources > Agent sessions — the server returns a 409 while
# disabled). Install from your
# Tortoise checkout (github.com/daniel-ospina/tortoise):
mkdir -p .claude/hooks
cp <path-to-tortoise>/tortoise/claude-hooks/session-start.sh .claude/hooks/session-start.sh
cp <path-to-tortoise>/tortoise/claude-hooks/session-end.sh .claude/hooks/session-end.sh
cp <path-to-tortoise>/tortoise/claude-hooks/session-turn.sh .claude/hooks/session-turn.sh
chmod +x .claude/hooks/session-start.sh .claude/hooks/session-end.sh .claude/hooks/session-turn.sh
# then merge into .claude/settings.json:
# #3754: the explicit timeout is load-bearing — Claude Code cancels a SessionEnd
# hook at its 1.5s default; the budget rises to the highest per-hook timeout (60
# is the documented ceiling). session-end.sh measured 9.26s on a real run.
# { "hooks": { "SessionStart": [{ "matcher": "", "hooks": [{ "type": "command", "command": ".claude/hooks/session-start.sh", "timeout": 60 }] }], "SessionEnd": [{ "matcher": "", "hooks": [{ "type": "command", "command": ".claude/hooks/session-end.sh", "timeout": 60 }] }], "UserPromptSubmit": [{ "matcher": "", "hooks": [{ "type": "command", "command": ".claude/hooks/session-turn.sh", "timeout": 30 }] }] } }`,
  // #2827: these constants are NOT wired to any live surface — they are only
  // reachable from the archived LEGACY_WIZARD_ARCHIVED render in main.jsx and
  // from harnesses.test.js. A remote HTTP MCP server must NOT be documented as
  // an mcpServers JSON block regardless: claude_desktop_config.json accepts
  // local stdio servers only, so a "type"/"headers" shape silently does
  // nothing (no server, no error). These connector field values are what the
  // user actually pastes into the Connectors UI.
  // #2865: key-less OAuth — deleting the Bearer line is the whole point of the
  // issue (Anthropic's "Request headers" field is beta and absent on many
  // accounts). The `key` argument is kept so every caller keeps one signature.
  'claude-desktop': () =>
    `Tortoise — Claude Desktop (OAuth, no API key)\nClaude Desktop reaches a remote MCP server through the Connectors UI:\n1. Open Claude Desktop → Settings → Connectors → Add custom connector.\n2. Name: Tortoise\n3. Server URL: ${CANONICAL_MCP_URL}\n4. Leave Request headers empty — no API key is needed. Claude opens\n   Tortoise's sign-in page on the first connection: sign in, click Authorize,\n   then pick the Organization you're onboarding.\n5. Start a new chat and paste the Tortoise workflows prompt — it gives Claude\n   the Tortoise workflows. Then say "Set up Tortoise" so the agent calls\n   tortoise_health to verify, and click "I've connected it — Continue" in the\n   dashboard connect step (the click only advances — the connection itself is\n   confirmed by your agent's first successful write).`,
  'claude-web': () => {
    const base = WORKFLOWS_PROMPT
    // The session-filing paragraph is gated on HARNESS_CAPTURE_SUPPORT — the
    // single source of truth (web is currently false: disabled-with-reason).
    const filing = HARNESS_CAPTURE_SUPPORT['claude-web']
      ? `\n\n4) Session filing — recording is on by default (ToS-covered); if your team switched it off (Memory sources > Agent sessions in the dashboard), the server returns a 409. At the end of a conversation, call tortoise_session_capture(conversation=<this conversation>, harness='claude-web') to file it. Capture only runs when you call it; nothing is recorded otherwise. If the call fails (disabled, quota, or provider limits), tell me it wasn't filed and don't retry.`
      : ''
    return base + filing
  },
  codex: (key) =>
    `export TORTOISE_API_KEY=${key}\ncodex mcp add tortoise --url ${MCP_URL} --bearer-token-env-var TORTOISE_API_KEY\n\n${CODEX_CAPTURE_INSTALL}`,
  cursor: () =>
    `${JSON.stringify(CURSOR_MCP_CONFIG_ENV, null, 2)}`,
  pi: (key) =>
    `Set up Tortoise for this project:
1. Add TORTOISE_API_KEY=${key} to my shell profile (~/.zshrc or ~/.bashrc).
2. Create or merge .mcp.json in this project with:
${JSON.stringify(PI_MCP_CONFIG_ENV, null, 2)}
3. Run: curl -fsSL ${SKILLS_INSTALL_URL} | bash -s -- --harness pi
   (Onboarding is NOT a skill — it is not installed. Your agent follows the
   instructions at ${ONBOARDING_INSTRUCTIONS_URL}.)

${PI_CAPTURE_INSTALL}

4. Restart Pi from a NEW terminal — quit Pi fully, open a new terminal
   window, and start Pi there. A "/reload" is NOT enough: Pi reads the key
   from the environment of the shell that LAUNCHED it, so a reload (or a
   restart in the same old terminal) silently keeps the stale or empty
   value. You get a 401, or connect to a previous organization with no
   warning at all. ('/reload' re-scans configs, skills, and MCP
   registrations, and tortoise connects eagerly at startup — the config
   doesn't mark it lazy — so no separate connect step is needed.)
   Then call tortoise_health — when it passes, tell me "Tortoise is
   connected". The first time you write a memory or file a decision,
   onboarding auto-completes (no separate ceremony needed).`,
  // #1701: ChatGPT — key-less OAuth harness. Copy = WORKFLOWS_PROMPT only;
  // the connector steps live in HARNESS_STEPS / HARNESS_INTRO above the
  // snippet (never in the copied text).
  chatgpt: () => WORKFLOWS_PROMPT,
}

// #1643: the official skill installer — served from the product site (the
// public source of truth is github.com/daniel-ospina/tortoise-skills-and-
// integrations). Installs the 3 core skills into the harness's project-
// scoped skills dir (personal for Pi). Appended to each harness's copy.
export const SKILLS_INSTALL_URL =
  'https://app.premiselabs.co/install-tortoise-skills.sh'

// #4365: the shipped skill set, stated ONCE. Every surface in THIS module that
// enumerates it interpolates these — SKILL_INSTALL, the HARNESS_SKILLS block,
// and the HARNESS_STEPS.cursor label — so the copy cannot drift from the
// installer's own `SKILLS=(...)` array (pinned by test on both sides). The
// four LIVE dashboard wizard prompts interpolate SKILLS_LIST too — from
// wizardPrompts.js, since #4880 moved them out of main.jsx so the guards can
// assert the RENDERED string instead of parsing JSX source. The rendered copy
// and the installer's array are pinned together by
// website/apps/dashboard/src/wizardPrompts.test.js and by
// tests/test_onboarding_variants.py::test_4365_served_connect_copy_names_
// three_skills_plus_the_instructions — the constant is not its own guard.
export const SKILLS_LIST =
  'how-to-use-tortoise, tortoise-decide, tortoise-file-finding'
export const SKILLS_CLAIM = `Install the Tortoise skills (${SKILLS_LIST})`

// #4365: onboarding is NOT one of the installed skills — it is a one-time
// setup FLOW delivered as INSTRUCTIONS. This is the served instruction set
// the agent follows after the connect command (also printed by `tortoise
// init` as onboarding_prompt_url); it is byte-identical to its repo source
// (tortoise/onboarding/SKILL.md) under the parity gate.
export const ONBOARDING_INSTRUCTIONS_URL =
  'https://app.premiselabs.co/skills/tortoise-onboarding/SKILL.md'

// #1710: harnesses whose skills + persist are rendered as HARNESS_STEPS
// (with per-step Copy buttons) instead of being appended to the copy —
// the copy is a JSON file body (Cursor), and a curl/export appended to it
// would break the file paste.
export const HARNESS_SKILLS_IN_STEPS = ['cursor']

// #1710: bare command with a comment lead-in only (paste-safe in a
// terminal — the prose used to be plain text inside the copy, which
// errored when pasted as-is).
export const HARNESS_SKILLS = (harness) =>
  HARNESS_SKILLLESS.includes(harness) || HARNESS_SKILLS_IN_PROMPT.includes(harness) || HARNESS_SKILLS_IN_STEPS.includes(harness)
    ? ''
    : `\n\n# ${SKILLS_CLAIM}:\ncurl -fsSL ${SKILLS_INSTALL_URL} | bash -s -- --harness ${harness}`

// #1694: per-harness label for the Copy action (Claude Web/Pi copy a
// prompt to paste into the agent, not a setup command).
export const HARNESS_COPY_LABEL = {
  'claude-web': 'Copy prompt',
  pi: 'Copy prompt',
  codexDesktop: 'Copy instructions',
  chatgpt: 'Copy prompt',
}

// #1694: per-harness label for the post-copy Continue affordance — for
// harnesses with manual UI steps (the Claude connectors, Claude Web), copying
// ≠ setup done, so the button says what copying actually achieved.
// #2865: both Claude connector leaves are OAuth now — the user completes the
// connector + Authorize in Claude, so "I've connected it" is the honest label.
export const HARNESS_CONTINUE_LABEL = {
  'claude-desktop': "I've connected it — Continue →",
  'claude-web': "I've connected it — Continue →",
  chatgpt: "I've connected it — Continue →",
}

// #1728 Slice 3 (Task 16): the session-CAPTURE install steps shown INLINE in
// the Memory-sources rows when a harness is install-pending (extracted from
// HARNESS_INSTALL so the row shows only the capture step, not the full MCP
// setup). claude = in-repo hooks install; pi = extension copy-install.
export const HARNESS_CAPTURE_INSTALL = {
  claude: `# Session capture (#1727 T1): recording is on by default (ToS-covered); the
# hooks file every session to Tortoise Cloud unless switched off (Memory
# sources > Agent sessions — the server returns a 409 while disabled).
# Install from your Tortoise checkout:
mkdir -p .claude/hooks
cp <path-to-tortoise>/tortoise/claude-hooks/session-start.sh .claude/hooks/session-start.sh
cp <path-to-tortoise>/tortoise/claude-hooks/session-end.sh .claude/hooks/session-end.sh
cp <path-to-tortoise>/tortoise/claude-hooks/session-turn.sh .claude/hooks/session-turn.sh
chmod +x .claude/hooks/session-start.sh .claude/hooks/session-end.sh .claude/hooks/session-turn.sh
# then merge into .claude/settings.json:
# #3754: the explicit timeout is load-bearing — Claude Code cancels a SessionEnd
# hook at its 1.5s default; the budget rises to the highest per-hook timeout (60
# is the documented ceiling). session-end.sh measured 9.26s on a real run.
# { "hooks": { "SessionStart": [{ "matcher": "", "hooks": [{ "type": "command", "command": ".claude/hooks/session-start.sh", "timeout": 60 }] }], "SessionEnd": [{ "matcher": "", "hooks": [{ "type": "command", "command": ".claude/hooks/session-end.sh", "timeout": 60 }] }], "UserPromptSubmit": [{ "matcher": "", "hooks": [{ "type": "command", "command": ".claude/hooks/session-turn.sh", "timeout": 30 }] }] } }`,
  // #3575: the SAME constant HARNESS_INSTALL.pi installs — the in-repo
  // extension, not an agent-infra settings toggle. Shared so the Memory-
  // sources row and the setup prompt can never drift.
  pi: PI_CAPTURE_INSTALL,
  // #3818: the Codex SessionEnd capture hook — same sharing rule as Pi.
  codex: CODEX_CAPTURE_INSTALL,
  // #3819: the Cursor sessionEnd capture hook — same sharing rule.
  cursor: CURSOR_CAPTURE_INSTALL,
}

// #1728 Slice 3 (Task 16/17): per-harness disabled-with-reason copy for the
// sessions rows — pinned in the plan (web = "session capture for web is in
// progress — not available yet" until the Task 13 spike verdict flips
// HARNESS_CAPTURE_SUPPORT; claude-desktop = backfill import only until an
// install path exists (codex got one in #3818, cursor in #3819)). The
// reason map covers the DISABLED harnesses only — a supported harness renders
// the capture step, never a reason. Never hidden rows — disabled with an
// honest reason.
export const HARNESS_CAPTURE_REASON = {
  'claude-desktop': 'backfill import only — no live install path yet',
  'claude-web': 'session capture for web is in progress — not available yet',
  chatgpt: "ChatGPT connects from its own cloud — session capture isn't available for it",
}

// #1728 (Task 17): receipt/probe labels for the 4-state capture status
// (shared by the wizard step-1 and the dashboard panel).
//
// #3700: two states in this table are derived from a per-harness onboarding
// key whose harness is the CALLER's DECLARATION, not a server observation:
//   * `active` reads `session_capture_receipt_<harness>` — `body.harness` on a
//     fresh session (an authenticated agent self-report), or the Session's
//     STORED harness on a re-capture when it has one (itself recorded from the
//     declaration that first stamped it); a Session with no stored harness
//     falls back to the current caller's declaration (`stored or claimed`);
//   * `waiting` reads `install_probe_<harness>` — `body.harness` on the
//     install-probe POST the installed artifact fires.
// Either way the harness is a caller declaration. No credential→harness binding
// exists (a `tt_`/`tk_` key carries no harness), so the server never OBSERVES
// which harness captured or installed; it observes that a credential reached
// it. So the attribution is NOT baked into these state words — it is rendered
// by `harnessAttributionForHarness` (captureStatus.js) next to the harness name,
// which is where a self-reported harness belongs. The state VOCABULARY, the key
// spellings and these state words are unchanged.
//
// `install-pending` is not in this group: it is the fall-through when NEITHER
// per-harness STATE key (`session_capture_receipt_<h>` / `install_probe_<h>`) is
// present, so its LABEL carries no attribution — hedging "not installed yet" as
// agent-reported would invent a signal the server does not have. A row in this
// state can still disclose one: a recorded per-harness FAILURE
// (`session_capture_last_error_<h>`) is itself a per-harness signal, and
// `harnessAttributionForHarness` attributes the row for it.
export const HARNESS_ATTRIBUTION = 'harness reported by your agent'
export const HARNESS_CAPTURE_STATUS_LABEL = {
  off: 'off',
  'install-pending': 'not installed yet',
  waiting: 'installed — waiting for first capture',
  active: 'active',
}

// #3700: the per-harness FAILURE sub-line's wording — the sibling of the labels
// above, kept in this module (the
// derivation reads state and guards the null case; it authors no copy). No
// attribution here: this sentence renders inside a `role="alert"` live region,
// where an assertive announcement must carry only the failure the user has to
// act on, and where a trailing caveat would collide with server detail that
// itself ends in a parenthesis or a full stop. The row already carries the
// attribution (see above).
//
// The sentence deliberately names no HARNESS either, even though an assertive
// announcement then reaches the screen reader without the row it belongs to:
// the harness is the caller's own declaration, so naming it here would restate
// a declared label OUTSIDE the disclosure above, in a region that cannot carry
// it — re-creating the #3700 misreading the disclosure exists to prevent. The
// alert is a child of the row, so its harness is the row's, named in the
// head beside that disclosure.
export const HARNESS_CAPTURE_LAST_ATTEMPT = (detail) =>
  `Last attempt — ${detail}`

// #1710: bare command with a comment lead-in — paste-safe in a terminal.
export const HARNESS_PERSIST = (key) =>
  `# Persist the key for future sessions — add this line to your shell profile (~/.zshrc, ~/.bashrc, or equivalent):\nexport TORTOISE_API_KEY=${key}`

export const HARNESS_ORDER = ['claude', 'claude-desktop', 'claude-web', 'codex', 'cursor', 'pi', 'chatgpt']

// ── #2912: the wizard's TWO-LEVEL harness chooser ─────────────────────────
// The connect step used to render 6 flat tabs (Claude Code / Claude Desktop /
// Claude Web / Codex / Cursor / Pi), so one product filled half the row while
// Codex hid its CLI/Desktop split behind an in-body toggle (#2328). Codex had
// the better shape; this lifts it to every multi-surface harness:
//
//   level 1 — the HARNESS (the product you use): Claude | Codex | Cursor | Pi
//   level 2 — the SURFACE you connect (only when there is a real choice):
//             Claude → Code, Desktop, Web;  Codex → CLI, Desktop
//
// `surface.id` is the existing HARNESS_ORDER/`wizardHarness` LEAF id, so every
// per-harness payload (UNIVERSAL_COMMAND, wizardPromptText, HARNESS_NAMES,
// capture support) stays keyed exactly as before — this is a UI grouping, not
// a vocabulary change (DE2E-5's 7-harness contract is untouched).
// ChatGPT stays out: it has no Claude/Codex-style surface split and its
// Developer-mode path is not reachable from the dashboard chooser (#2698).
// #2865: Claude's Desktop/Web surfaces ARE key-less OAuth (HARNESS_OAUTH) and
// stay here, so every role can reach them — including members.
export const HARNESS_FAMILIES = Object.freeze([
  Object.freeze({
    id: 'claude',
    name: 'Claude',
    surfaces: Object.freeze([
      Object.freeze({ id: 'claude', name: 'Claude Code', hint: 'Terminal' }),
      Object.freeze({ id: 'claude-desktop', name: 'Claude Desktop', hint: 'Desktop app' }),
      Object.freeze({ id: 'claude-web', name: 'Claude Web', hint: 'claude.ai' }),
    ]),
  }),
  Object.freeze({
    id: 'codex',
    name: 'Codex',
    surfaces: Object.freeze([
      Object.freeze({ id: 'codex', name: 'Codex CLI', hint: 'Terminal' }),
      Object.freeze({ id: 'codexDesktop', name: 'Codex Desktop', hint: 'No terminal needed' }),
    ]),
  }),
  Object.freeze({ id: 'cursor', name: 'Cursor', surfaces: Object.freeze([]) }),
  Object.freeze({ id: 'pi', name: 'Pi', surfaces: Object.freeze([]) }),
])

// The leaf id a surface click selects: single-surface families ARE their
// surface (Cursor stays 'cursor'), multi-surface families keep the surface's
// own id. Keeps HARNESS_FAMILIES.surfaces[].id == a HARNESS_ORDER leaf.
export const HARNESS_FAMILY_IDS = HARNESS_FAMILIES.map((f) => f.id)

// Resolve which top-level family a leaf id belongs to (the chooser's selected
// family when the user is on a Claude Desktop / Codex Desktop surface).
export function harnessFamilyOf(leaf) {
  for (const family of HARNESS_FAMILIES) {
    if (family.id === leaf) return family
    if (family.surfaces.some((s) => s.id === leaf)) return family
  }
  return null
}

// The surface a family switch should land on: stay on the surface the user
// already picked when it belongs to the new family (re-clicking a family is
// non-destructive), else the family's preferred default — the terminal/CLI
// surface first (self-install, no manual connector steps).
export function preferredSurface(family, current) {
  if (!family) return current
  if (family.surfaces.some((s) => s.id === current)) return current
  if (family.surfaces.length > 0) return family.surfaces[0].id
  return family.id
}

// ── #1998 (W2): universal setup command (epic #1976 I-3, surface 5) ────────
// The connect step's ONE command per harness — all 7 covered, 4 self-install
// (config-write) + 3 teach-human (desktop/web/chatgpt — web/chatgpt have no
// local shell, so the human completes the steps). HARNESS_NAMES/HARNESS_ORDER
// stay the single 7-harness vocabulary; the harness table in the SERVED
// onboarding instructions (#4365: an instruction document, not an installed
// skill) is the agent-side self-adjudication source (the chooser's
// successor). chatgpt is key-less/OAuth (HARNESS_OAUTH) and the live chooser
// cannot select it: #2698 deleted its dashboard tab and #2912's 4-family
// HARNESS_FAMILIES excludes it. (The archived #1643 render at the bottom of this
// block maps the full HARNESS_ORDER, chatgpt included, but it is gated on
// `LEGACY_WIZARD_ARCHIVED && welcomeOriented` — both false.) Its LIVE carrier is
// the public setup docs page (#4836): website/docs.html#chatgpt names the
// Developer-mode path, the canonical connector URL and the onboarding
// instructions URL. The block below embeds the workflows prompt; the page hands
// over the onboarding document. This block's `chatgpt` copy must omit
// `tortoise_health` — it verifies in the chat instead (pinned in
// harnesses.test.js).
//
// Contract (DE2E-5): every harness reaches a connected state verifiable via
// tortoise_health; the served onboarding instructions take over from the
// command (verify → harness-connected checkpoint). The command NEVER embeds the API
// key in a project-scoped/committable config (env-var indirection); CLI
// one-liners carry the key in the shell call only. These exports are
// ADDITIVE — the legacy HARNESS_* exports stay (the ARCHIVED #1643 wizard
// render + Memory-sources capture rows depend on them; A0 rollback path).
export const HARNESS_SELF_INSTALL = ['claude', 'codex', 'cursor', 'pi']

export const HARNESS_TEACH_HUMAN = ['claude-desktop', 'claude-web', 'chatgpt']

// OAuth-only harnesses — no API key, no tt_ token: the wizard connect step
// renders these key-less (no key-mint, no key row). #1701 added chatgpt;
// #2865 added the two Claude connector leaves, whose Bearer recipe was
// unreachable on accounts without Anthropic's beta "Request headers" field.
// This is a VOCABULARY export, not a render gate — the live connect step
// branches on it directly (main.jsx), so a change here must be mirrored there.
export const HARNESS_OAUTH = ['claude-desktop', 'claude-web', 'chatgpt']

// The skill installer line every config-writing harness command appends.
// #4365: it names the THREE capabilities the installer actually ships (v3) —
// onboarding is not among them. Onboarding is the instructions the agent
// reads at ONBOARDING_INSTRUCTIONS_URL, plus the MCP config in the block above.
const SKILL_INSTALL = (harness) =>
  `# ${SKILLS_CLAIM}:\ncurl -fsSL ${SKILLS_INSTALL_URL} | bash -s -- --harness ${harness}\n\n# Onboarding is NOT a skill — it is the instructions below plus the config above.\n# Read and follow them here: ${ONBOARDING_INSTRUCTIONS_URL}`

// One copyable block per harness. The wizard renders + copies exactly this.
export const UNIVERSAL_COMMAND = {
  claude: (key) =>
    `# Tortoise — universal setup command (Claude Code)\n# Works with every supported agent — your agent figures out which one it is\n# during setup. You can re-run this in any new project folder.\nexport TORTOISE_API_KEY=${key}\nclaude mcp add --transport http tortoise ${MCP_URL} --header "Authorization: Bearer ${'${TORTOISE_API_KEY}'}"\n\n${SKILL_INSTALL('claude')}\n\n# Then tell your agent: "Set up Tortoise" — it verifies with tortoise_health\n# and reports the harness-connected checkpoint.`,
  codex: (key) =>
    `# Tortoise — universal setup command (Codex CLI)
export TORTOISE_API_KEY=${key}
codex mcp add tortoise --url ${MCP_URL} --bearer-token-env-var TORTOISE_API_KEY

${SKILL_INSTALL('codex')}

# Run from your project root — the installer writes skills to .agents/skills
# here (Codex's project skill root) and AGENTS.md for this repo.
# Then tell your agent: "Set up Tortoise" — it verifies with tortoise_health
# and reports the harness-connected checkpoint.
# First-time calls may prompt for approval in Codex — tortoise_health and the
# read-only tools are safe to allow (granular auto-approve for read tools).
# On Codex Desktop (no terminal)? Use the Desktop variant on the dashboard
# tab instead — it configures ~/.codex/config.toml with no shell.`,
  // #2328/#2329/#2330: Codex Desktop (the GUI app) has NO terminal and does
  // not inherit shell exports — `codex mcp add` is a CLI subcommand it cannot
  // run. Desktop shares ~/.codex/config.toml with the CLI, so the terminal-
  // less path is a config-file block: bearer_token_env_var (with the var
  // placed into the app's environment via launchctl/setx) OR the literal
  // http_headers fallback (private file, chmod 600 — never committed).
  // Skills live in .agents/skills (Codex's documented skill root, #2329) and
  // load when the project folder is open — the Desktop flow defers the
  // installer to a one-time terminal (or the agent itself inside the project).
  codexDesktop: (key) =>
    `# Tortoise — Codex Desktop (no terminal needed)
# Codex Desktop shares ~/.codex/config.toml with the CLI. Add this block to
# that file (create it if missing — never put the key in a committed file):

[mcp_servers.tortoise]
url = "${MCP_URL}"
bearer_token_env_var = "TORTOISE_API_KEY"

# TORTOISE_API_KEY must exist in Codex Desktop's environment — GUI apps do
# not read your shell profile. Pick ONE:
#   macOS:    launchctl setenv TORTOISE_API_KEY ${key}   (then fully quit +
#             reopen Codex Desktop; lasts until logout)
#   Windows:  setx TORTOISE_API_KEY ${key}   (sets your user environment;
#             relaunch Codex Desktop)
# No shell available at all? Then don't use the env var — REPLACE the
# bearer_token_env_var line above with a literal header (private file —
# chmod 600, never commit it). Keep ONE key line, never both:
#   http_headers = { Authorization = "Bearer ${key}" }
# (Settings → Integrations → MCP servers can also add Tortoise, but stores
# the token literally — the env-var form above is preferred.)

# Skills: Codex loads skills from .agents/skills in the project folder you
# have open (not .codex/skills). Run this once from any terminal (or ask
# your agent to run it inside the project):
#   curl -fsSL ${SKILLS_INSTALL_URL} | bash -s -- --harness codex
# Restart Codex Desktop (or start a new session) after the skills install so
# the new skills appear.
# Onboarding is NOT a skill (it is not installed) — your agent follows the
# instructions at ${ONBOARDING_INSTRUCTIONS_URL} after you say "Set up Tortoise".

# Then say "Set up Tortoise" in Codex Desktop — it verifies with
# tortoise_health and reports the checkpoint. First-time MCP calls may prompt
# for approval — tortoise_health and the read-only tools are safe to allow.`,
  cursor: () =>
    `# Tortoise — universal setup command (Cursor)\n# 1. Export the key — add this line to your shell profile so it persists:\nexport TORTOISE_API_KEY=<your-tortoise-api-key>\n# 2. Create .cursor/mcp.json in this project with:\n${JSON.stringify(CURSOR_MCP_CONFIG_ENV, null, 2)}\n# 3. Install the Tortoise skills (run in a terminal):\ncurl -fsSL ${SKILLS_INSTALL_URL} | bash -s -- --harness cursor\n# 4. Restart Cursor, then tell your agent: "Set up Tortoise" — it verifies\n#    with tortoise_health and reports the harness-connected checkpoint.\n#    Onboarding is NOT a skill (it is not installed) — the instructions your\n#    agent follows are at ${ONBOARDING_INSTRUCTIONS_URL}.\n#    (The config references the env var, never the key.)`,
  pi: (key) =>
    `Set up Tortoise for this project (universal setup command — Pi):
1. Add TORTOISE_API_KEY=${key} to my shell profile (~/.zshrc or ~/.bashrc).
2. Create or merge .mcp.json in this project with (the file references the
   env var, never the key):
${JSON.stringify(PI_MCP_CONFIG_ENV, null, 2)}
3. Run: curl -fsSL ${SKILLS_INSTALL_URL} | bash -s -- --harness pi
   (Onboarding is NOT a skill — it is not installed. Your agent follows the
   instructions at ${ONBOARDING_INSTRUCTIONS_URL}.)
4. Restart Pi from a NEW terminal (quit Pi fully, open a new terminal
   window, and start Pi there). A \"/reload\" is NOT enough — Pi reads the
   key from the environment of the shell that launched it, so a reload
   keeps the stale or empty value. ('/reload' re-scans configs, skills, and
   MCP registrations, and tortoise connects eagerly at startup — the config
   doesn't mark it lazy — so no separate connect step is needed.)
   Then call tortoise_health — when it passes, tell me "Tortoise is
   connected".`,
  // #2865: key-less OAuth. `key` is accepted (one signature for every harness)
  // but deliberately unused — neither of these surfaces takes a credential.
  'claude-desktop': () =>
    `# Tortoise — universal setup command (Claude Desktop — OAuth, no API key)
# Claude Desktop is filesystem-less for remote MCP: use the Connectors UI, NOT
# claude_desktop_config.json (that file only accepts local stdio servers, so an
# mcpServers block with a type/headers remote shape silently does nothing).
# YOU complete these steps, then the agent verifies:
1. Open Claude Desktop → Settings → Connectors → Add custom connector.
2. Connector name: Tortoise
3. Server URL:   ${CANONICAL_MCP_URL}
4. Leave Request headers empty — no API key is needed. On the first
   connection Claude opens Tortoise's sign-in page: sign in, click Authorize,
   then pick the Organization you're onboarding.
5. Start a new chat, paste the Tortoise workflows prompt, then say "Set up
   Tortoise" — the agent calls tortoise_health to verify. Click "I've connected
   it — Continue" in the dashboard connect step when it passes (the click only
   advances — the agent's first successful write is what confirms it).
   No local skills here — your agent follows the onboarding instructions at
   ${ONBOARDING_INSTRUCTIONS_URL}.`,
  'claude-web': () =>
    `Tortoise — universal setup command (Claude Web — OAuth, no API key)\nClaude Web runs in Anthropic's cloud — no local files. Complete the connector\nsteps below, then the agent (with the connector's tortoise_* tools) verifies:\n1. Go to claude.ai > Settings > Connectors > Add custom connector, name it "Tortoise".\n2. Server URL: ${CANONICAL_MCP_URL}\n3. Leave Request headers empty — no API key is needed. On the first connection\n   Claude opens Tortoise's sign-in page: sign in, click Authorize, then pick the\n   Organization you're onboarding.\n4. In a Claude Web chat, say "Set up Tortoise" — the agent calls tortoise_health\n   to verify, then click "I've connected it — Continue" in the dashboard connect\n   step (the click only advances — the agent's first successful write is what\n   confirms it).\n   No local skills here — your agent follows the onboarding instructions at\n   ${ONBOARDING_INSTRUCTIONS_URL}.`,
  chatgpt: () =>
    `Tortoise — ChatGPT (Developer mode, OAuth)\n1. Enable Developer mode: chatgpt.com → Settings → Security and login →\n   Developer mode (Plus/Pro/Business/Enterprise/Education).\n2. Open chatgpt.com/plugins → the + button → create a Developer-mode app.\n3. MCP server URL: ${CHATGPT_MCP_URL}  (no API key — choose OAuth; ChatGPT\n   discovers Tortoise's authorization server automatically).\n4. Click Scan Tools — sign in to Tortoise when prompted and click Authorize.\n   When Tortoise prompts you to choose an organization, pick the one you're onboarding for.\n5. The tortoise_* tools appear (Developer mode). In the SAME ChatGPT chat,\n   paste the prompt below — it gives ChatGPT the Tortoise workflows:\n\n${WORKFLOWS_PROMPT}\n\nAfter you paste it, ask ChatGPT a Tortoise question (e.g. "are we connected?")\nand confirm it answers from the connected MCP tools. The dashboard connect\nstep has no ChatGPT option to pick (#2698 deleted its tab; #2912's chooser has\nno chatgpt family), so the agent's first successful write is what confirms the\nconnection.\nNo local skills here — your agent's onboarding instructions are the document at\n${ONBOARDING_INSTRUCTIONS_URL}.`,
}

export const UNIVERSAL_COMMAND_HARNESSES = HARNESS_ORDER
