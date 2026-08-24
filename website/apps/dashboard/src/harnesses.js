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
  codex: 'Codex',
  cursor: 'Cursor',
  pi: 'Pi',
}

export const HARNESS_INSTALL = {
  claude: (key) =>
    `Run this command:\nclaude mcp add --transport http tortoise ${MCP_URL} --header "Authorization: Bearer ${key}"`,
  codex: (key) =>
    `Run these commands:\nexport TORTOISE_API_KEY=${key}\ncodex mcp add tortoise --url ${MCP_URL} --bearer-token-env-var TORTOISE_API_KEY`,
  cursor: (key) =>
    `Create .cursor/mcp.json in this project with:\n${JSON.stringify(CURSOR_MCP_CONFIG_ENV, null, 2)}\n(First: export TORTOISE_API_KEY=${key} — the config references the env var, not the key.)`,
  pi: (key) =>
    `Create or merge .mcp.json in this project with:\n${JSON.stringify(PI_MCP_CONFIG_ENV, null, 2)}\n(First: export TORTOISE_API_KEY=${key} — the config references the env var, not the key.)`,
}

// #1643: the first-party skill installer (agent-infra, public) — installs
// the 3 core skills into the harness's project-scoped skills dir (personal
// for Pi). Appended to each harness's copy.
export const SKILLS_INSTALL_URL =
  'https://raw.githubusercontent.com/daniel-ospina/agent-infra/main/scripts/install-tortoise-skills.sh'

export const HARNESS_SKILLS = (harness) =>
  `\n\nInstall the Tortoise skills (how-to-use-tortoise, tortoise-decide, tortoise-file-finding):\ncurl -fsSL ${SKILLS_INSTALL_URL} | bash -s -- --harness ${harness}`

export const HARNESS_PERSIST = (key) =>
  `Persist the key for future sessions — add this line to your shell profile\n(~/.zshrc, ~/.bashrc, or equivalent):\nexport TORTOISE_API_KEY=${key}`

export const HARNESS_ORDER = ['claude', 'codex', 'cursor', 'pi']
