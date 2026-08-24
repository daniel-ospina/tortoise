// #1643: per-harness MCP onboarding data (ported from welcome.html's
// HARNESS_* constants). Single source for the wizard's harness chooser;
// env-indirection configs keep the raw key out of config files (#529 J5/T7b).
const MCP_URL = 'https://api.premiselabs.co/mcp/'

const CURSOR_MCP_CONFIG_ENV = {
  mcpServers: {
    tortoise: {
      url: MCP_URL,
      headers: { Authorization: 'Bearer ${env:TORTOISE_API_KEY}' },
    },
  },
}

const PI_MCP_CONFIG_ENV = {
  mcpServers: {
    tortoise: {
      url: MCP_URL,
      headers: { Authorization: 'Bearer ${env:TORTOISE_API_KEY}' },
    },
  },
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
// profile (Claude Web connects from Anthropic's cloud — MCP only).
export const HARNESS_SKILLLESS = ['claude-web']

export const HARNESS_INSTALL = {
  claude: (key) =>
    `Run this command:\nclaude mcp add --transport http tortoise ${MCP_URL} --header "Authorization: Bearer ${key}"`,
  'claude-desktop': (key) =>
    `Edit ~/Library/Application Support/Claude/claude_desktop_config.json (macOS) — or Claude > Settings > Developer in the app — and add:\n${JSON.stringify({ mcpServers: { tortoise: { url: MCP_URL, headers: { Authorization: `Bearer ${key}` } } } }, null, 2)}\n(Claude Desktop keeps this key literal in the file — keep that file private. Restart Claude after saving.)`,
  'claude-web': () =>
    `No command needed — do it in the browser:\n1. Go to claude.ai > Settings > Connectors\n2. Add custom connector → ${MCP_URL}\n3. In Request headers (advanced): Authorization: Bearer <your-key>\n(Claude connects from its own cloud — your key is stored by Anthropic, and the file-based skills don't apply on web.)`,
  codex: (key) =>
    `Run these commands:\nexport TORTOISE_API_KEY=${key}\ncodex mcp add tortoise --url ${MCP_URL} --bearer-token-env-var TORTOISE_API_KEY`,
  cursor: (key) =>
    `Create .cursor/mcp.json in this project with:\n${JSON.stringify(CURSOR_MCP_CONFIG_ENV, null, 2)}\n(First: export TORTOISE_API_KEY=${key} — the config references the env var, not the key.)`,
  pi: (key) =>
    `Create or merge .mcp.json in this project with:\n${JSON.stringify(PI_MCP_CONFIG_ENV, null, 2)}\n(First: export TORTOISE_API_KEY=${key} — the config references the env var, not the key.)`,
}

// #1643: the official skill installer — served from the product site (the
// public source of truth is github.com/daniel-ospina/tortoise-skills-and-
// integrations). Installs the 3 core skills into the harness's project-
// scoped skills dir (personal for Pi). Appended to each harness's copy.
export const SKILLS_INSTALL_URL =
  'https://app.premiselabs.co/install-tortoise-skills.sh'

export const HARNESS_SKILLS = (harness) =>
  HARNESS_SKILLLESS.includes(harness)
    ? ''
    : `\n\nInstall the Tortoise skills (how-to-use-tortoise, tortoise-decide, tortoise-file-finding):\ncurl -fsSL ${SKILLS_INSTALL_URL} | bash -s -- --harness ${harness}`

export const HARNESS_PERSIST = (key) =>
  `Persist the key for future sessions — add this line to your shell profile\n(~/.zshrc, ~/.bashrc, or equivalent):\nexport TORTOISE_API_KEY=${key}`

export const HARNESS_ORDER = ['claude', 'claude-desktop', 'claude-web', 'codex', 'cursor', 'pi']
