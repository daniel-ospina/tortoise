# Platform Seams Matrix — per-turn volunteering-memory injection

Epic #2080 end-state delivery (issues #2119–#2126). For the
**Claude-hooks-compatible family** each seam is a thin registration over the
ONE shared per-turn reflex:

```text
UserPromptSubmit hook  →  volunteer-turn.sh <harness>  →  `tortoise volunteer`
                                                          (hosted POST /v1/context
                                                           or local SDK volunteer_context
                                                           — one pipeline, tortoise/volunteer.py)
```

**That model does NOT cover every harness.** Pi (#2119) and OpenClaw (#2120)
have no shell-hook surface: their seam is an in-process plugin/extension, so
`tortoise install <harness>` writes a package and the "thin registration"
framing does not apply to them. Each row below states its own mechanism; do
not infer one from this diagram.

Install (agent-first onboarding, #2123/#2124 mandate): `tortoise install
<harness>` for codex / claude / cline / devin / pi / openclaw. Everything else
in this matrix is the documented manual registration.

One shape note for reading the table: `codex`, `claude` and `devin` are all
Claude-hooks-shaped **JSON** seams, but Devin's file is the only one with **no
`"hooks"` wrapper key** — `.devin/hooks.v1.json` *is* the hooks object
(`{"UserPromptSubmit": [{"hooks": [{"type": "command", "command": …}]}]}`),
while `.codex/hooks.json` and `.claude/settings.json` nest theirs under
`"hooks"`. Devin also reads `.claude/settings.json` hooks when
`read_config_from.claude` is on (the default), which is why `install devin`
warns when a claude registration would make the reflex fire twice per turn.

## Contracts (verified against harness primary docs)

| # | Harness | Hook event | Registration target | Output contract | Wave-1 status |
|---|---------|-----------|--------------------|-----------------|---------------|
| #2126 | Claude Code / Desktop | `UserPromptSubmit` | `.claude/settings.json` `hooks` | `hookSpecificOutput.additionalContext` | shipped (`install claude`); MCP priming in `mcp_server.py` |
| #2123 | Codex | `UserPromptSubmit` | `<repo>/.codex/hooks.json` (also `~/.codex/hooks.json`) | `hookSpecificOutput.additionalContext` (stdin JSON has `prompt`) | shipped (`install codex`) |
| #2124* | Cline | `UserPromptSubmit` | `.cline/hooks/UserPromptSubmit` (project) or `~/.cline/hooks` (global; enable Hooks in settings) | `contextModification.context` | shipped (`install cline`) |
| #2124* | Devin | `UserPromptSubmit` | `.devin/hooks.v1.json` (also `~/.config/devin/config.json` `hooks`) | `hookSpecificOutput.additionalContext` | shipped (`install devin`); primary docs re-fetched 2026-10-11 — file IS the hooks object (no `"hooks"` wrapper), stdin carries `prompt`; `matcher` omitted (tool-events only) |
| #2119 | Pi (pi-coding-agent) | session lifecycle only (no per-turn prompt hook in the extension API today) | `~/.pi/agent/extensions/` TS extension | n/a — per-turn injection not reachable via extension API | MCP pull + session-start digest (existing) + documented limitation |
| #2122 | Cursor | `UserPromptSubmit` (Claude Code-compatible) | `.cursor/hooks.json` (Claude Code-compatible file) | `hookSpecificOutput.additionalContext` | shared script `claude` arg compatible; registration file documented |
| #2120 | OpenClaw | **native plugin** — per-turn hook `before_prompt_build` → `{prependContext}` (in-process; OpenClaw has NO shell-hook surface) | `~/.openclaw/extensions/tortoise/` (plugin dir; `openclaw.plugin.json` + entry `index.mjs`) → `before_prompt_build`; capture via the `llm_output` hook → `$OPENCLAW_STATE_DIR/tortoise/turns/<session>.jsonl` (one file per session) → `tortoise sessions import --harness openclaw <file>` | `PluginHookBeforePromptBuildResult.prependContext` | **shipped** — `tortoise install openclaw` writes the plugin + `plugins.entries.tortoise.enabled`; injection graded by `tortoise/openclaw-plugin/index.test.mjs`, install by `tests/test_openclaw_seam.py` |
| #2121 | Hermes | `pre_llm_call` (agents-hub) | `.agents.yml` actions | tool-result text = the block | hook output mapping documented; parser entry added |
| #2124* | Gemini CLI / OpenCode / other Claude-hooks-compatibles | `UserPromptSubmit`/equivalent | harness-specific | harness-specific | shared script's default emits the bare block (harmless where unsupported) |
| #2125 | Frameworks (SDK-level middleware: Vercel AI SDK / LangChain / Mastra / OpenAI Agents SDK) | per-request middleware | project code import `tortoise/framework.py` | tool-role message with the block | **not wave-1** — demand-gated per the issue; wave-1 shared script's bare-block default is the interim |

\* #2124 (harness adapters) is demand-gated per the issue — shared script
coverage + the per-harness READ registration land first (codex / claude /
cline / devin shipped); session-store PARSERS for the newly-added harnesses
and the remaining registration files (gemini, opencode) follow demand. Do not
build a speculative parser for a platform with unproven distribution.

## Fail-open / fail-closed posture (all seams)

### Correction log — #2120's OpenClaw row (re-measured 2026-10-11)

The row above previously read *"per-turn hook surface = AgentRequest hook"* /
*"`~/.openclaw/*.claw` AgentRequest handler"* / *"harness-store parser
scaffolded"*. **All three were wrong**, and the wave-1 comment on #2120 —
*"a thin registration over the shared surface"* — rested on them:

| Claim | Re-measurement |
|---|---|
| `~/.openclaw/*.claw` AgentRequest handler | No `.claw` file extension and no `handle_AgentRequest` symbol exist anywhere in `openclaw/openclaw` (code search: 0 hits; no mention in `docs/plugins/*`). |
| "Thin registration" of `volunteer-turn.sh` | OpenClaw has **no shell-hook surface at all**. Its real per-turn seam is an in-process plugin hook (`before_prompt_build` → `prependContext`, `docs/plugins/hooks/prompt-and-session.md`), so the seam is a plugin PACKAGE (manifest + entry + config enablement), not a script registration. |
| "harness-store parser scaffolded" | No OpenClaw parser existed (`PARSERS` had codex/claude-desktop/cursor/pi). Neither had Hermes (#2121) — the matrix's per-row statuses for unbuilt seams were optimistic and are now only claimed where code exists. |

Verified sources (2026-10-11): `docs/plugins/hooks/prompt-and-session.md`,
`docs/plugins/manifest.md`, `docs/plugins/manifest/package-json.md`,
`src/plugins/hook-types.ts` (`PluginHookBeforePromptBuildEvent`,
`PluginHookLlmOutputEvent`), `extensions/memory-core/index.ts` (a shipped
reference for the same hook shape).

A second consequence of the re-measurement: OpenClaw keeps its runtime
transcript in SQLite (`~/.openclaw/agents/<agentId>/agent/openclaw-agent.sqlite`)
with no documented per-session JSONL, so ingestion does **not** ride a
harness-store file parser. The plugin writes its own capture spool — **one
file per session** (`$OPENCLAW_STATE_DIR/tortoise/turns/<session>.jsonl`),
because `tortoise sessions import` files one session per file and a shared
spool would file every session as one merged conversation — and
`tortoise sessions import --harness openclaw` parses THAT, which keeps consent,
receipts, provenance and REPHRASE dedup in the one existing import pipeline.

- **Content fail-open**: any reflex failure (unreachable, misconfig, empty
  graph, nothing above the confidence gate) → the hook emits NOTHING and
  exits 0. The agent turn proceeds untouched.
- **Auth fail-closed**: hosted capture/auth channels keep their own deny
  rules. The hook process carries no credentials of its own, but the reflex
  it invokes transmits the Bearer identity resolved from ONE source chain
  (#2369 D1, hardened): a file-sourced key transmits only to the SAME
  file's `api_url` or the built-in default — the env `TORTOISE_API_URL`
  override applies only to env-sourced keys; the transmitting identity
  resolves from the user-global `~/.tortoise/credentials.json` only (a
  repo/cwd `.tortoise` can never supply it — repo config feeds only local,
  non-sending mode); and a hosted-against-file run prints a stderr
  endpoint-mode note (`tortoise: hosted-mode note:`) that the shipped hook
  relays to the harness log, so a future redirect is loud at run time.
  Env alone (a bare `TORTOISE_API_KEY`) still never flips the hook to
  hosted mode (#1708 D1b).
- W4 user-exposure opt-in: per-turn injection ships behind the `install`
  command — nothing auto-registers.

## Verification

W3 harness seam grade (tests/eval/harness or tests/test_*): executes the
SHIPPED `volunteer-turn.sh` against a seeded embedded graph and asserts the
per-harness output contract carries the reflex block (see the wave-1 seam
test in tests/).
