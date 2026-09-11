// #1643: per-harness MCP onboarding data (ported from welcome.html's
// HARNESS_* constants). Single source for the wizard's harness chooser;
// env-indirection configs keep the raw key out of config files (#529 J5/T7b).
const MCP_URL = 'https://api.premiselabs.co/mcp/'

// #1701: ChatGPT's custom-connector URL — NO trailing slash. OpenAI's
// connector validates an '/mcp' suffix, and bare POST /mcp dispatches
// directly into the mounted MCP app (no 307). MCP_URL above keeps its
// slash for the 6 keyed harnesses (byte-identical copy).
const CHATGPT_MCP_URL = 'https://api.premiselabs.co/mcp'

// #1701: the skills-as-prompt body shared by every filesystem-less harness
// that therefore has no local skills to install — Claude Web, Claude Desktop
// (#2827) and ChatGPT. ONE constant so the user-facing prompts can never
// drift. Composed by wizardWorkflowsText in the dashboard.
export const WORKFLOWS_PROMPT =
  `You have Tortoise connected (the 'tortoise' MCP tools). Follow these workflows:\n\n1) Writing to the graph — Tortoise stores knowledge as points with edges: IMPL means 'supports', NAND means 'contradicts'. Mitigations reduce confidence (range 0.10–0.50). To change a point, supersede it and clean up its active edges rather than editing in place. Prefer structural claims over labels and always cite provenance.\n\n2) Decisions — to make a decision, first refine it, then research the options, the criteria that matter, and the findings/evidence, then wire IMPL/NAND edges from findings and criteria to options (mitigate an edge, range 0.10–0.50, when it's true but matters less), and rank the options by EP confidence.\n\n3) Research findings — when I share a research finding, ingest it as a point, check for existing related claims first, and surface connections to what we already know.`

// #1727 (Task 13): per-harness session-capture support gate — the single
// source of truth consumed by BOTH the dashboard's per-harness sessions
// toggle AND the conditional claude-web prompt paragraph below (flipped in
// the same slice/commit as the dist rebuild). Values:
//   true  — the harness has an executable filing path AND a server-visible
//           signal (claude = SessionStart install-probe + end-hook capture;
//           pi = extension-on-load probe + capture)
//   false — disabled-with-reason: no install/capture path confirmed yet.
// The Claude-Web filing-path spike verdict (Slice 2, Task 13): the MCP
// custom-connector path CAN expose tortoise_session_capture to claude.ai
// workflows prompts, but the plan pins that disclosure-only is NOT a
// terminal state — the web row stays disabled-with-reason until a
// SERVER-VISIBLE web signal is confirmed (a web install-probe variant or
// observed web-harness POSTs; workflows-prompt presence alone is client-
// side and unpinnable). Until then web = false.
export const HARNESS_CAPTURE_SUPPORT = {
  claude: true,
  'claude-desktop': false,  // backfill import only (Task 15) — no live install path
  'claude-web': false,      // disabled-with-reason pending the Task 13 spike signal
  codex: false,             // backfill import only (Task 15) — no live install path
  cursor: false,            // cursor spike verdict: unsupported for capture
  pi: true,
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
  'claude-web': [
    'Go to claude.ai > Settings > Connectors',
    'Add custom connector and name it "Tortoise"',
    { label: 'Server URL', code: MCP_URL, copy: MCP_URL },
    'In Request headers (advanced): Authorization: Bearer <your-key>',
    'Paste the prompt below into a Claude Web chat — it gives Claude the Tortoise workflows:',
    '(Claude connects from its own cloud — your key is stored by Anthropic. No local skills on web — the prompt gives Claude the workflows.)',
  ],
  cursor: [
    { label: 'Export the key — add this line to your shell profile (~/.zshrc or ~/.bashrc) so it persists:', code: `export TORTOISE_API_KEY=${key}`, copy: `export TORTOISE_API_KEY=${key}` },
    'Create .cursor/mcp.json in this project with the JSON below — the config references the env var, not the key:',
    { label: 'Install the Tortoise skills (how-to-use-tortoise, tortoise-decide, tortoise-file-finding):', code: `curl -fsSL ${SKILLS_INSTALL_URL} | bash -s -- --harness cursor`, copy: `curl -fsSL ${SKILLS_INSTALL_URL} | bash -s -- --harness cursor` },
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
  'claude-desktop': 'Open Claude Desktop → Settings → Connectors → Add custom connector, then enter the Server URL and the Authorization: Bearer <key> request header. (For local stdio servers only, advanced users can still edit ~/Library/Application Support/Claude/claude_desktop_config.json on macOS, or %APPDATA%\\Claude\\claude_desktop_config.json on Windows, from Settings → Developer in the app.)',
  // #2361 review-r1 (UX-3): Claude Web had no intro — the copy button said
  // 'Copy prompt' but nothing explained where the prompt goes.
  'claude-web': 'Start a new chat at claude.ai (or the Claude web app) and paste the prompt below into it — that conversation becomes your connected agent. The prompt keeps the key literal: do not share the chat or the key.',
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

// Harnesses with no local file system for the file-based skills or shell
// profile (Claude Desktop/Web connect from the app/cloud — MCP only).
export const HARNESS_SKILLLESS = ['claude-desktop', 'claude-web', 'chatgpt']

// Harnesses whose install copy embeds the skill-install step in a self-
// contained prompt (Pi) — nothing extra is appended after the copy.
export const HARNESS_SKILLS_IN_PROMPT = ['pi']

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
chmod +x .claude/hooks/session-start.sh .claude/hooks/session-end.sh
# then merge into .claude/settings.json:
# { "hooks": { "SessionStart": [{ "matcher": "", "hooks": [{ "type": "command", "command": ".claude/hooks/session-start.sh" }] }], "SessionEnd": [{ "matcher": "", "hooks": [{ "type": "command", "command": ".claude/hooks/session-end.sh" }] }] } }`,
  // #2827: these constants are NOT wired to any live surface — they are only
  // reachable from the archived LEGACY_WIZARD_ARCHIVED render in main.jsx and
  // from harnesses.test.js. A remote HTTP MCP server must NOT be documented as
  // an mcpServers JSON block regardless: claude_desktop_config.json accepts
  // local stdio servers only, so a "type"/"headers" shape silently does
  // nothing (no server, no error). These connector field values are what the
  // user actually pastes into the Connectors UI.
  'claude-desktop': (key) =>
    `Connector name: Tortoise\nServer URL:   ${MCP_URL}\nRequest headers:\n  Authorization: Bearer ${key}`,
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
    `export TORTOISE_API_KEY=${key}\ncodex mcp add tortoise --url ${MCP_URL} --bearer-token-env-var TORTOISE_API_KEY`,
  cursor: () =>
    `${JSON.stringify(CURSOR_MCP_CONFIG_ENV, null, 2)}`,
  pi: (key) =>
    `Set up Tortoise for this project:
1. Add TORTOISE_API_KEY=${key} to my shell profile (~/.zshrc or ~/.bashrc).
2. Create or merge .mcp.json in this project with:
${JSON.stringify(PI_MCP_CONFIG_ENV, null, 2)}
3. Run: curl -fsSL ${SKILLS_INSTALL_URL} | bash -s -- --harness pi
4. Reload Pi (run "/reload" — tortoise connects eagerly at startup).
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
    : `\n\n# Install the Tortoise skills (how-to-use-tortoise, tortoise-decide, tortoise-file-finding):\ncurl -fsSL ${SKILLS_INSTALL_URL} | bash -s -- --harness ${harness}`

// #1694: per-harness label for the Copy action (Claude Web/Pi copy a
// prompt to paste into the agent, not a setup command).
export const HARNESS_COPY_LABEL = {
  'claude-web': 'Copy prompt',
  pi: 'Copy prompt',
  codexDesktop: 'Copy instructions',
  chatgpt: 'Copy prompt',
}

// #1694: per-harness label for the post-copy Continue affordance — for
// harnesses with manual UI steps (Claude Web), copying ≠ setup done, so
// the button says what copying actually achieved.
export const HARNESS_CONTINUE_LABEL = {
  'claude-web': "I've pasted it — Continue →",
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
chmod +x .claude/hooks/session-start.sh .claude/hooks/session-end.sh
# then merge into .claude/settings.json:
# { "hooks": { "SessionStart": [{ "matcher": "", "hooks": [{ "type": "command", "command": ".claude/hooks/session-start.sh" }] }], "SessionEnd": [{ "matcher": "", "hooks": [{ "type": "command", "command": ".claude/hooks/session-end.sh" }] }] } }`,
  pi: `5. Session capture (#1727 T1): enable session capture in the Pi extension
settings. The extension fires an install-probe on load (harness + timestamp
only, no content) and files sessions to Tortoise Cloud when capture is
enabled. Backfill past sessions with:
tortoise sessions import --harness pi --file <session.jsonl>
(local receipt written only on a 2xx).`,
}

// #1728 Slice 3 (Task 16/17): per-harness disabled-with-reason copy for the
// sessions rows — pinned in the plan (web = "session capture for web is in
// progress — not available yet" until the Task 13 spike verdict flips
// HARNESS_CAPTURE_SUPPORT; codex/claude-desktop = backfill import only until
// an install path exists; cursor = spike verdict). Never hidden rows —
// disabled with an honest reason.
export const HARNESS_CAPTURE_REASON = {
  'claude-desktop': 'backfill import only — no live install path yet',
  'claude-web': 'session capture for web is in progress — not available yet',
  codex: 'backfill import only — no live install path yet',
  cursor: 'unsupported for session capture',
  chatgpt: "ChatGPT connects from its own cloud — session capture isn't available for it",
}

// #1728 (Task 17): receipt/probe labels for the 4-state capture status
// (shared by the wizard step-1 and the dashboard panel).
export const HARNESS_CAPTURE_STATUS_LABEL = {
  off: 'off',
  'install-pending': 'not installed yet',
  waiting: 'installed — waiting for first capture',
  active: 'active',
}

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
// ChatGPT stays out: it is key-less OAuth (#2698) and has its own path.
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
// stay the single 7-harness vocabulary; the harness table in the
// tortoise-onboarding SKILL.md is the agent-side self-adjudication source
// (the chooser's successor). chatgpt is key-less/OAuth (HARNESS_OAUTH) and
// renders through a dedicated wizard branch, not this universal command —
// UNIVERSAL_COMMAND.chatgpt exists for total-loop/roundtrip consumers only.
//
// Contract (DE2E-5): every harness reaches a connected state verifiable via
// tortoise_health; the tortoise-onboarding skill takes over from the command
// (verify → harness-connected checkpoint). The command NEVER embeds the API
// key in a project-scoped/committable config (env-var indirection); CLI
// one-liners carry the key in the shell call only. These exports are
// ADDITIVE — the legacy HARNESS_* exports stay (the ARCHIVED #1643 wizard
// render + Memory-sources capture rows depend on them; A0 rollback path).
export const HARNESS_SELF_INSTALL = ['claude', 'codex', 'cursor', 'pi']

export const HARNESS_TEACH_HUMAN = ['claude-desktop', 'claude-web', 'chatgpt']

// #1701: OAuth-only harnesses (ChatGPT) — no API key, no tt_ token. The
// wizard connect step renders these key-less (no key-mint, no universal
// command).
export const HARNESS_OAUTH = ['chatgpt']

// The skill installer line every config-writing harness command appends
// (v2 SKILLS includes tortoise-onboarding + the 3 core skills).
const SKILL_INSTALL = (harness) =>
  `# Install the Tortoise skills (how-to-use-tortoise, tortoise-decide, tortoise-file-finding, tortoise-onboarding):\ncurl -fsSL ${SKILLS_INSTALL_URL} | bash -s -- --harness ${harness}`

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

# Then say "Set up Tortoise" in Codex Desktop — it verifies with
# tortoise_health and reports the checkpoint. First-time MCP calls may prompt
# for approval — tortoise_health and the read-only tools are safe to allow.`,
  cursor: () =>
    `# Tortoise — universal setup command (Cursor)\n# 1. Export the key — add this line to your shell profile so it persists:\nexport TORTOISE_API_KEY=<your-tortoise-api-key>\n# 2. Create .cursor/mcp.json in this project with:\n${JSON.stringify(CURSOR_MCP_CONFIG_ENV, null, 2)}\n# 3. Install the Tortoise skills (run in a terminal):\ncurl -fsSL ${SKILLS_INSTALL_URL} | bash -s -- --harness cursor\n# 4. Restart Cursor, then tell your agent: "Set up Tortoise" — it verifies\n#    with tortoise_health and reports the harness-connected checkpoint.\n#    (The config references the env var, never the key.)`,
  pi: (key) =>
    `Set up Tortoise for this project (universal setup command — Pi):
1. Add TORTOISE_API_KEY=${key} to my shell profile (~/.zshrc or ~/.bashrc).
2. Create or merge .mcp.json in this project with (the file references the
   env var, never the key):
${JSON.stringify(PI_MCP_CONFIG_ENV, null, 2)}
3. Run: curl -fsSL ${SKILLS_INSTALL_URL} | bash -s -- --harness pi
4. Reload Pi (run \"/reload\" — tortoise connects eagerly at startup).
   Then call tortoise_health — when it passes, tell me "Tortoise is
   connected".`,
  'claude-desktop': (key) =>
    `# Tortoise — universal setup command (Claude Desktop — manual setup)
# Claude Desktop is filesystem-less for remote MCP: use the Connectors UI, NOT
# claude_desktop_config.json (that file only accepts local stdio servers, so an
# mcpServers block with a type/headers remote shape silently does nothing).
# YOU complete these steps, then the agent verifies:
1. Open Claude Desktop → Settings → Connectors → Add custom connector.
2. Connector name: Tortoise
3. Server URL:   ${MCP_URL}
4. Request headers:
     Authorization: Bearer ${key}
5. Start a new chat, paste the Tortoise workflows prompt, then say "Set up
   Tortoise" — the agent calls tortoise_health to verify. Click "I've set it up
   — Continue" in the dashboard connect step when it passes (that writes the
   harness-connected checkpoint).`,
  'claude-web': (key) =>
    `Tortoise — universal setup command (Claude Web — manual setup)\nClaude Web runs in Anthropic's cloud — no local files. Complete the connector\nsteps below, then the agent (with the connector's tortoise_* tools) verifies:\n1. Go to claude.ai > Settings > Connectors > Add custom connector, name it "Tortoise".\n2. Server URL: ${MCP_URL}\n3. Request headers (advanced): Authorization: Bearer ${key}  (stored by Anthropic)\n4. In a Claude Web chat, say "Set up Tortoise" — the agent calls tortoise_health\n   to verify, then click "I've pasted it — Continue" in the dashboard connect\n   step (that writes the harness-connected checkpoint).`,
  chatgpt: () =>
    `Tortoise — ChatGPT (Developer mode, OAuth)\n1. Enable Developer mode: chatgpt.com → Settings → Security and login →\n   Developer mode (Plus/Pro/Business/Enterprise/Education).\n2. Open chatgpt.com/plugins → the + button → create a Developer-mode app.\n3. MCP server URL: ${CHATGPT_MCP_URL}  (no API key — choose OAuth; ChatGPT\n   discovers Tortoise's authorization server automatically).\n4. Click Scan Tools — sign in to Tortoise when prompted and click Authorize.\n   When Tortoise prompts you to choose an organization, pick the one you're onboarding for.\n5. The tortoise_* tools appear (Developer mode). In the SAME ChatGPT chat,\n   paste the prompt below — it gives ChatGPT the Tortoise workflows:\n\n${WORKFLOWS_PROMPT}\n\nAfter you paste it, ask ChatGPT a Tortoise question (e.g. "are we connected?")\nand confirm it answers from the connected MCP tools, then click "I've\nconnected it — Continue →" in the dashboard connect step (that writes the\nharness-connected checkpoint).`,
}

export const UNIVERSAL_COMMAND_HARNESSES = HARNESS_ORDER
