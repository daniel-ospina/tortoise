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
// profile (Claude Desktop/Web connect from the app/cloud — MCP only).
export const HARNESS_SKILLLESS = ['claude-desktop', 'claude-web']

// Harnesses whose install copy embeds the skill-install step in a self-
// contained prompt (Pi) — nothing extra is appended after the copy.
export const HARNESS_SKILLS_IN_PROMPT = ['pi']

export const HARNESS_INSTALL = {
  claude: (key) =>
    `Run this command:\nclaude mcp add --transport http tortoise ${MCP_URL} --header "Authorization: Bearer ${key}"`,
  'claude-desktop': (key) =>
    `Edit ~/Library/Application Support/Claude/claude_desktop_config.json (macOS) — or Claude > Settings > Developer in the app — and add the tortoise block\n${JSON.stringify({ mcpServers: { tortoise: { url: MCP_URL, headers: { Authorization: `Bearer ${key}` } } } }, null, 2)}\n(Claude Desktop keeps this key literal in the file — keep that file private. If you already have an mcpServers section, merge this into it — don't replace the whole file. Restart Claude after saving.)`,
  'claude-web': (key) =>
    `No command needed — do it in the browser:\n1. Go to claude.ai > Settings > Connectors\n2. Add custom connector → ${MCP_URL}\n3. In Request headers (advanced): Authorization: Bearer <your-key>\n(Claude connects from its own cloud — your key is stored by Anthropic. No local skills on web — instead, paste the prompt below into a Claude Web chat so it knows the Tortoise workflows.):\n\nYou have Tortoise connected (the 'tortoise' MCP tools). Follow these workflows:\n\n1) Writing to the graph — Tortoise stores knowledge as points with edges: IMPL means 'supports', NAND means 'contradicts'. Mitigations reduce confidence (range 0.10–0.50). To change a point, supersede it and clean up its active edges rather than editing in place. Prefer structural claims over labels and always cite provenance.\n\n2) Decisions — to make a decision, first refine it, then research the options and the criteria that matter, then wire IMPL/NAND/mitigation edges between criteria and options, and rank the options by EP confidence.\n\n3) Research findings — when I share a research finding, ingest it as a point, check for existing related claims first, and surface connections to what we already know.`,
  codex: (key) =>
    `Run these commands:\nexport TORTOISE_API_KEY=${key}\ncodex mcp add tortoise --url ${MCP_URL} --bearer-token-env-var TORTOISE_API_KEY`,
  cursor: (key) =>
    `Create .cursor/mcp.json in this project with:\n${JSON.stringify(CURSOR_MCP_CONFIG_ENV, null, 2)}\n(First: export TORTOISE_API_KEY=${key} — the config references the env var, not the key.)`,
  pi: (key) =>
    `Paste this into your Pi agent (replace <your-key> with the API key above):\n\nSet up Tortoise for this project:\n1. Add TORTOISE_API_KEY=<your-key> to my shell profile (~/.zshrc or ~/.bashrc).\n2. Create or merge .mcp.json in this project with:\n${JSON.stringify(PI_MCP_CONFIG_ENV, null, 2)}\n3. Run: curl -fsSL ${SKILLS_INSTALL_URL} | bash -s -- --harness pi\n4. Verify the Tortoise MCP server is configured and the three skills (how-to-use-tortoise, tortoise-decide, tortoise-file-finding) are installed, then tell me what you did.`,
}

// #1643: the official skill installer — served from the product site (the
// public source of truth is github.com/daniel-ospina/tortoise-skills-and-
// integrations). Installs the 3 core skills into the harness's project-
// scoped skills dir (personal for Pi). Appended to each harness's copy.
export const SKILLS_INSTALL_URL =
  'https://app.premiselabs.co/install-tortoise-skills.sh'

export const HARNESS_SKILLS = (harness) =>
  HARNESS_SKILLLESS.includes(harness) || HARNESS_SKILLS_IN_PROMPT.includes(harness)
    ? ''
    : `\n\nInstall the Tortoise skills (how-to-use-tortoise, tortoise-decide, tortoise-file-finding):\ncurl -fsSL ${SKILLS_INSTALL_URL} | bash -s -- --harness ${harness}`

export const HARNESS_PERSIST = (key) =>
  `Persist the key for future sessions — add this line to your shell profile\n(~/.zshrc, ~/.bashrc, or equivalent):\nexport TORTOISE_API_KEY=${key}`

export const HARNESS_ORDER = ['claude', 'claude-desktop', 'claude-web', 'codex', 'cursor', 'pi']
