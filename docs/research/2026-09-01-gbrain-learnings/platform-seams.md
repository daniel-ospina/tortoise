---
title: "Research — Platform Seams Matrix for the Volunteering-Memory Reflex (#2080)"
type: product
issue: "#2080"
date: 2026-09-01
created: 2026-09-01
domain: product
doc_status: draft
subjects.team: epistemic-team
aboutSubjects: tortoise-memory, tortoise-volunteering-reflex
aboutObjects: tortoise-mcp, tortoise-why-recall
---

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
<<<<<<< origin/main
<harness>` for codex / claude / cline / devin. Everything else in this matrix
is the documented manual registration.

One shape note for reading the table: `codex`, `claude` and `devin` are all
Claude-hooks-shaped **JSON** seams, but Devin's file is the only one with **no
`"hooks"` wrapper key** — `.devin/hooks.v1.json` *is* the hooks object
(`{"UserPromptSubmit": [{"hooks": [{"type": "command", "command": …}]}]}`),
while `.codex/hooks.json` and `.claude/settings.json` nest theirs under
`"hooks"`. Devin also reads `.claude/settings.json` hooks when
`read_config_from.claude` is on (the default), which is why `install devin`
warns when a claude registration would make the reflex fire twice per turn.
||||||| 1abe17a40
<harness>` for codex / claude / cline. Everything else in this matrix is the
documented manual registration.
=======
<harness>` for codex / claude / cline / cursor. Everything else in this matrix
is the documented manual registration.
>>>>>>> fe5f4d97eb

## Contracts (verified against harness primary docs)

| # | Harness | Hook event | Registration target | Output contract | Wave-1 status |
|---|---------|-----------|--------------------|-----------------|---------------|
| #2126 | Claude Code / Desktop | `UserPromptSubmit` | `.claude/settings.json` `hooks` | `hookSpecificOutput.additionalContext` | shipped (`install claude`); MCP priming in `mcp_server.py` |
| #2123 | Codex | `UserPromptSubmit` | `<repo>/.codex/hooks.json` (also `~/.codex/hooks.json`) | `hookSpecificOutput.additionalContext` (stdin JSON has `prompt`) | shipped (`install codex`) |
| #2124* | Cline | `UserPromptSubmit` | `.cline/hooks/UserPromptSubmit` (project) or `~/.cline/hooks` (global; enable Hooks in settings) | `contextModification.context` | shipped (`install cline`) |
| #2124* | Devin | `UserPromptSubmit` | `.devin/hooks.v1.json` (also `~/.config/devin/config.json` `hooks`) | `hookSpecificOutput.additionalContext` | shipped (`install devin`); primary docs re-fetched 2026-10-11 — file IS the hooks object (no `"hooks"` wrapper), stdin carries `prompt`; `matcher` omitted (tool-events only) |
| #2119 | Pi (pi-coding-agent) | session lifecycle only (no per-turn prompt hook in the extension API today) | `~/.pi/agent/extensions/` TS extension | n/a — per-turn injection not reachable via extension API | MCP pull + session-start digest (existing) + documented limitation |
| #2122 | Cursor | `sessionStart` → `additional_context`; per-turn `beforeSubmitPrompt` is observe/block ONLY | `~/.cursor/hooks.json` (flat script entries, absolute command; project `.cursor/hooks.json` is workspace-trust-gated) | `{"additional_context": …}` (snake_case) on `sessionStart` | **shipped** — `tortoise install cursor` registers the `sessionStart` cold-context hook (contract v4) *plus* the `sessionEnd` capture hook; per-turn push stays Cursor-blocked (watch item) |
| #2120 | OpenClaw | per-turn hook surface = AgentRequest hook | `~/.openclaw/*.claw` AgentRequest handler → `volunteer-turn.sh openclaw` | `handle_AgentRequest` → context dict | hook output mapping documented; harness-store parser scaffolded |
| #2121 | Hermes | `pre_llm_call` (agents-hub) | `.agents.yml` actions | tool-result text = the block | hook output mapping documented; parser entry added |
| #2124* | Gemini CLI / OpenCode / other Claude-hooks-compatibles | `UserPromptSubmit`/equivalent | harness-specific | harness-specific | shared script's default emits the bare block (harmless where unsupported) |
| #2125 | Frameworks (SDK-level middleware: Vercel AI SDK / LangChain / Mastra / OpenAI Agents SDK) | per-request middleware | project code import `tortoise/framework.py` | tool-role message with the block | **not wave-1** — demand-gated per the issue; wave-1 shared script's bare-block default is the interim |

\* #2124 (harness adapters) is demand-gated per the issue — shared script
coverage + the per-harness READ registration land first (codex / claude /
cline / devin shipped); session-store PARSERS for the newly-added harnesses
and the remaining registration files (gemini, opencode) follow demand. Do not
build a speculative parser for a platform with unproven distribution.

## Correction log

### #2122's Cursor row (re-measured 2026-10-11)

The row read *"Hook event `UserPromptSubmit` (Claude Code-compatible)"* /
*"`hookSpecificOutput.additionalContext`"* / *"registration file
documented"* — and the wave-1 comment on #2122 (*"a thin registration over the
shared surface … register the shipped `volunteer-turn.sh`"*) rested on the
first two. **Both are wrong for Cursor**, and the third described work that had
not been done:

| Claim | Re-measurement (Cursor's own hook reference + third-party-hooks page, 2026-10-11) |
|---|---|
| `UserPromptSubmit`/`beforeSubmitPrompt` can inject | **No.** `beforeSubmitPrompt` output is `{continue, user_message}` — it observes and BLOCKS a prompt; there is no `additional_context` (nor Claude's `additionalContext`) on it. Mapping Claude's `UserPromptSubmit` onto it (third-party-hooks) inherits exactly that limit, so registering the shared per-turn reflex at Cursor would be claiming an unshipped seam (indicator 3 of #2122). |
| Output contract `hookSpecificOutput.additionalContext` | **Wrong field for Cursor.** The one Cursor event that injects is `sessionStart`, and it reads `additional_context` (snake_case). Cursor's config/output validation is strict in this repo's measurements (#3819: an unknown key invalidates the whole document), so the shipped hook emits the native field only. |
| "Registration file documented" | The registration is now BUILT: `tortoise/cursor-hooks/session-start.sh` + the `sessionStart` entry in `~/.cursor/hooks.json`, installed by `tortoise install cursor` (contract generation 3→4), graded by `tests/test_cursor_capture_hook.py`. |

Two further findings from the same pass:

1. **A second injection seam exists that wave-1 did not know about**: `postToolUse`
   output carries `additional_context` ("extra context injected into the
   conversation after the tool result"). That is a *post-tool* seam, not the
   pre-prompt reflex, so it is NOT claimed here — filed as a follow-up issue
   (#8109).
2. **Delivery is still Cursor's side of the seam.** `sessionStart` runs
   fire-and-forget and the field is documented, but community reports of
   `additional_context` not reaching the model are unresolved. We therefore
   claim the EMITTED CONTRACT (what the hook prints, graded by tests), not
   delivery to the model, and no per-hook `timeout` is written: Cursor
   documents "Timeout configuration" support without documenting the unit or
   the kill semantics for a fire-and-forget session event, and a value read as
   milliseconds would silently kill the digest.
3. **Indicator 1's tool list is stale too.** #2122 names
   `tortoise_search`/`tortoise_ask`/`tortoise_recall`/`tortoise_analyze` as the
   enriched pull surface, but `tortoise_ask` was removed from the MCP / SDK /
   REST surface under #3849/#3929 (it is eval-only), so the pull surface is
   `tortoise_search` / `tortoise_recall` / `tortoise_analyze` — verified over
   MCP stdio on 2026-10-11: 82 tools exposed, all three present,
   `tortoise_ask` absent. The why-contract on those three is graded by
   `tests/test_w4_why_enrichment.py` (the #2101 successor).

## Fail-open / fail-closed posture (all seams)

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
test in tests/).  The Cursor `sessionStart` injection half (#2122) is graded
the same way in `tests/test_cursor_capture_hook.py`: the shipped
`tortoise/cursor-hooks/session-start.sh` is executed from an INSTALLED path
with a fake reflex, and the emitted `{"additional_context": …}` contract, the
broken-PATH-shim fallback (#8106's class, closed for this seam), the
empty-answer silence and the inert-with-evidence branch are asserted.
