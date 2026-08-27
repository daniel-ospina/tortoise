// #1643: per-harness MCP onboarding data (ported from welcome.html's
// HARNESS_* constants). Single source for the wizard's harness chooser;
// env-indirection configs keep the raw key out of config files (#529 J5/T7b).
const MCP_URL = 'https://api.premiselabs.co/mcp/'

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
})[harness]

// #1710: per-harness short instruction shown ABOVE the snippet — what to
// do with the copy (run in a terminal / paste into the config file /
// paste into the agent). NOT part of the copied content.
export const HARNESS_INTRO = {
  claude: 'Run these commands in your terminal:',
  'claude-desktop': 'Edit ~/Library/Application Support/Claude/claude_desktop_config.json (macOS) — or Claude > Settings > Developer in the app — and add the tortoise block below. Merge into an existing mcpServers section — don\'t replace the whole file. The key stays literal here — keep the file private. Restart Claude after saving.',
  codex: 'Run these commands in your terminal:',
  pi: 'Paste this into your Pi agent:',
}

export const HARNESS_NAMES = {
  claude: 'Claude Code',
  'claude-desktop': 'Claude Desktop',
  'claude-web': 'Claude Web',
  codex: 'Codex',
  cursor: 'Cursor',
  pi: 'Pi',
}

// Harnesses with no local file system for the file-based skills or shell
// profile (Claude Desktop/Web connect from the app/cloud — MCP only).
export const HARNESS_SKILLLESS = ['claude-desktop', 'claude-web']

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

# Session capture (#1727 T1): the hooks file every session to Tortoise Cloud
# (only when your team has enabled it in the dashboard — Memory sources >
# Agent sessions; the server enforces a 403 otherwise). Install from your
# Tortoise checkout (github.com/daniel-ospina/tortoise):
mkdir -p .claude/hooks
cp <path-to-tortoise>/tortoise/claude-hooks/session-start.sh .claude/hooks/session-start.sh
cp <path-to-tortoise>/tortoise/claude-hooks/session-end.sh .claude/hooks/session-end.sh
chmod +x .claude/hooks/session-start.sh .claude/hooks/session-end.sh
# then merge into .claude/settings.json:
# { "hooks": { "SessionStart": [{ "matcher": "", "hooks": [{ "type": "command", "command": ".claude/hooks/session-start.sh" }] }], "SessionEnd": [{ "matcher": "", "hooks": [{ "type": "command", "command": ".claude/hooks/session-end.sh" }] }] } }`,
  'claude-desktop': (key) =>
    `${JSON.stringify({ mcpServers: { tortoise: { url: MCP_URL, headers: { Authorization: `Bearer ${key}` } } } }, null, 2)}`,
  'claude-web': () => {
    const base = `You have Tortoise connected (the 'tortoise' MCP tools). Follow these workflows:\n\n1) Writing to the graph — Tortoise stores knowledge as points with edges: IMPL means 'supports', NAND means 'contradicts'. Mitigations reduce confidence (range 0.10–0.50). To change a point, supersede it and clean up its active edges rather than editing in place. Prefer structural claims over labels and always cite provenance.\n\n2) Decisions — to make a decision, first refine it, then research the options, the criteria that matter, and the findings/evidence, then wire IMPL/NAND edges from findings and criteria to options (mitigate an edge, range 0.10–0.50, when it's true but matters less), and rank the options by EP confidence.\n\n3) Research findings — when I share a research finding, ingest it as a point, check for existing related claims first, and surface connections to what we already know.`
    // The session-filing paragraph is gated on HARNESS_CAPTURE_SUPPORT — the
    // single source of truth (web is currently false: disabled-with-reason).
    const filing = HARNESS_CAPTURE_SUPPORT['claude-web']
      ? `\n\n4) Session filing — ONLY if your team has enabled session capture (Memory sources > Agent sessions in the dashboard): at the end of a conversation, call tortoise_session_capture(conversation=<this conversation>, harness='claude-web') to file it. Capture only runs when you call it; nothing is recorded otherwise. If the call fails (not enabled, quota, or provider limits), tell me it wasn't filed and don't retry.`
      : ''
    return base + filing
  },
  codex: (key) =>
    `export TORTOISE_API_KEY=${key}\ncodex mcp add tortoise --url ${MCP_URL} --bearer-token-env-var TORTOISE_API_KEY`,
  cursor: () =>
    `${JSON.stringify(CURSOR_MCP_CONFIG_ENV, null, 2)}`,
  pi: (key) =>
    `Set up Tortoise for this project:\n1. Add TORTOISE_API_KEY=${key} to my shell profile (~/.zshrc or ~/.bashrc).\n2. Create or merge .mcp.json in this project with:\n${JSON.stringify(PI_MCP_CONFIG_ENV, null, 2)}\n3. Run: curl -fsSL ${SKILLS_INSTALL_URL} | bash -s -- --harness pi\n4. Verify the Tortoise MCP server is configured and the three skills (how-to-use-tortoise, tortoise-decide, tortoise-file-finding) are installed, then tell me what you did.\n5. Session capture (#1727 T1): enable session capture in the Pi extension settings (Memory sources > Agent sessions must also be enabled in the dashboard — the server enforces a 403 otherwise). The extension fires an install-probe on load (harness + timestamp only, no content) and files sessions to Tortoise Cloud when capture is enabled. Backfill past sessions with: tortoise sessions import --harness pi --file <session.jsonl> (local receipt written only on a 2xx).`,
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
}

// #1694: per-harness label for the post-copy Continue affordance — for
// harnesses with manual UI steps (Claude Web), copying ≠ setup done, so
// the button says what copying actually achieved.
export const HARNESS_CONTINUE_LABEL = {
  'claude-web': "I've pasted it — Continue →",
}

// #1710: bare command with a comment lead-in — paste-safe in a terminal.
export const HARNESS_PERSIST = (key) =>
  `# Persist the key for future sessions — add this line to your shell profile (~/.zshrc, ~/.bashrc, or equivalent):\nexport TORTOISE_API_KEY=${key}`

export const HARNESS_ORDER = ['claude', 'claude-desktop', 'claude-web', 'codex', 'cursor', 'pi']
