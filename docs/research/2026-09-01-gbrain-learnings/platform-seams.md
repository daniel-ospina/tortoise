# Platform Seams Matrix — per-turn volunteering-memory injection

Epic #2080 end-state delivery (issues #2119–#2126). Each seam = a thin
registration over the ONE shared per-turn reflex:

```
UserPromptSubmit hook  →  volunteer-turn.sh <harness>  →  `tortoise volunteer`
                                                          (hosted POST /v1/context
                                                           or local SDK volunteer_context
                                                           — one pipeline, tortoise/volunteer.py)
```

Install (agent-first onboarding, #2123/#2124 mandate): `tortoise install
<harness>` for codex / claude / cline. Everything else in this matrix is the
documented manual registration.

## Contracts (verified against harness primary docs)

| # | Harness | Hook event | Registration target | Output contract | Wave-1 status |
|---|---------|-----------|--------------------|-----------------|---------------|
| #2126 | Claude Code / Desktop | `UserPromptSubmit` | `.claude/settings.json` `hooks` | `hookSpecificOutput.additionalContext` | shipped (`install claude`); MCP priming in `mcp_server.py` |
| #2123 | Codex | `UserPromptSubmit` | `<repo>/.codex/hooks.json` (also `~/.codex/hooks.json`) | `hookSpecificOutput.additionalContext` (stdin JSON has `prompt`) | shipped (`install codex`) |
| #2124* | Cline | `UserPromptSubmit` | `.cline/hooks/UserPromptSubmit` (project) or `~/.cline/hooks` (global; enable Hooks in settings) | `contextModification.context` | shipped (`install cline`) |
| #2124* | Devin | `UserPromptSubmit` | `.devin/hooks.v1.json` | `hookSpecificOutput.additionalContext` (Claude-hooks-shaped) | shared script supports `devin`; registration pending primary-doc re-fetch |
| #2119 | Pi (pi-coding-agent) | session lifecycle only (no per-turn prompt hook in the extension API today) | `~/.pi/agent/extensions/` TS extension | n/a — per-turn injection not reachable via extension API | MCP pull + session-start digest (existing) + documented limitation |
| #2122 | Cursor | `UserPromptSubmit` (Claude Code-compatible) | `.cursor/hooks.json` (Claude Code-compatible file) | `hookSpecificOutput.additionalContext` | shared script `claude` arg compatible; registration file documented |
| #2120 | OpenClaw | per-turn hook surface = AgentRequest hook | `~/.openclaw/*.claw` AgentRequest handler → `volunteer-turn.sh openclaw` | `handle_AgentRequest` → context dict | hook output mapping documented; harness-store parser scaffolded |
| #2121 | Hermes | `pre_llm_call` (agents-hub) | `.agents.yml` actions | tool-result text = the block | hook output mapping documented; parser entry added |
| #2124* | Gemini CLI / OpenCode / other Claude-hooks-compatibles | `UserPromptSubmit`/equivalent | harness-specific | harness-specific | shared script's default emits the bare block (harmless where unsupported) |
| #2125 | Frameworks (SDK-level middleware: Vercel AI SDK / LangChain / Mastra / OpenAI Agents SDK) | per-request middleware | project code import `tortoise/framework.py` | tool-role message with the block | middleware helpers in `tortoise/framework.py` |

\* #2124 (harness adapters) is demand-gated per the issue — shared script
coverage lands first; per-harness registration files follow demand.

## Fail-open / fail-closed posture (all seams)

- **Content fail-open**: any reflex failure (unreachable, misconfig, empty
  graph, nothing above the confidence gate) → the hook emits NOTHING and
  exits 0. The agent turn proceeds untouched.
- **Auth fail-closed**: hosted capture/auth channels keep their own deny
  rules; the hook never carries credentials (it inherits the shell env /
  local `.tortoise` config only).
- W4 user-exposure opt-in: per-turn injection ships behind the `install`
  command — nothing auto-registers.

## Verification

W3 harness seam grade (tests/eval/harness or tests/test_*): executes the
SHIPPED `volunteer-turn.sh` against a seeded embedded graph and asserts the
per-harness output contract carries the reflex block (see the wave-1 seam
test in tests/).
