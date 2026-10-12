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
<harness>` for codex / claude / cline / devin / cursor. Everything else in this matrix
is the documented manual registration.

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
| #2122 | Cursor | `sessionStart` → `additional_context`; per-turn `beforeSubmitPrompt` is observe/block ONLY | `~/.cursor/hooks.json` (flat script entries, absolute command; project `.cursor/hooks.json` is workspace-trust-gated) | `{"additional_context": …}` (snake_case) on `sessionStart` | **shipped** — `tortoise install cursor` registers the `sessionStart` cold-context hook (contract v4) *plus* the `sessionEnd` capture hook; per-turn push stays Cursor-blocked (watch item) |
| #2120 | OpenClaw | per-turn hook surface = AgentRequest hook | `~/.openclaw/*.claw` AgentRequest handler → `volunteer-turn.sh openclaw` | `handle_AgentRequest` → context dict | hook output mapping documented; harness-store parser scaffolded |
| #2121 | Hermes | `pre_llm_call` (agents-hub) | `.agents.yml` actions | tool-result text = the block | hook output mapping documented; parser entry added |
| #2124* | Gemini CLI / OpenCode / other Claude-hooks-compatibles | `UserPromptSubmit`/equivalent | harness-specific | harness-specific | shared script's default emits the bare block (harmless where unsupported) |
| #2125 | Frameworks (SDK-level middleware: Google ADK / LangGraph / Semantic Kernel) | ADK `before_model_callback`; LangGraph pre-model node; SK Prompt Render Filter | project code imports `tortoise/framework.py` and registers the returned hook | the reflex `block` appended to the prompt (ADK: request instructions; LangGraph: one appended message; SK: `rendered_prompt` prefix) | shipped, **opt-in** — `tortoise/framework.py` + the `#2125` seam grade |

\* #2124 (harness adapters) is demand-gated per the issue — shared script
coverage + the per-harness READ registration land first (codex / claude /
cline / devin shipped); session-store PARSERS for the newly-added harnesses
and the remaining registration files (gemini, opencode) follow demand. Do not
build a speculative parser for a platform with unproven distribution.

> **#2125 framework-name drift (recorded 2026-10-15):** this row previously
> listed *Vercel AI SDK / LangChain / Mastra / OpenAI Agents SDK*. Issue #2125's
> title + scope — **Google ADK, LangGraph, Semantic Kernel** — are the
> authoritative pair (they trace to `platform-landscape.md` §3, "Tier 2 —
> adapters"), and they are what `tortoise/framework.py` implements. The old
> list was a sketch written before the issue was filed; the issue wins.

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
   delivery to the model, and no per-hook `timeout` is written: Cursor's
   reference documents the unit (`Execution timeout in seconds`) but not the
   kill semantics for a fire-and-forget session event, and an omitted key
   inherits Cursor's own default rather than a budget we would have to defend.
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

## Frameworks (#2125) — SDK-level middleware, injection only

A framework app owns its own sessions and files them with the EXISTING
ingestion (`POST /v1/sessions`) — there is no harness session format to parse.
The only missing piece per framework is the per-turn **injection** hook:
`tortoise/framework.py` supplies one thin, duck-typed, fail-open adapter per
documented extension point, and every adapter calls the ONE canonical reflex
(`tortoise/volunteer.py`) through a provider the app supplies.

| Framework | Extension point (primary docs) | Adapter |
|---|---|---|
| Google ADK | `before_model_callback(callback_context, llm_request)` — mutate the `LlmRequest` before the call | `adk_before_model_callback(reflex)` |
| LangGraph | a node immediately before the model node, appending to `messages` | `langgraph_pre_model_node(reflex)` |
| Semantic Kernel | Prompt Render Filter — override `context.rendered_prompt` after `await next(context)` | `semantic_kernel_prompt_render_filter(reflex)` |

One-line agent-installable registration (the app already has `tortoise-graph`
installed — no extra package, no framework dependency pulled in by Tortoise):

```python
# Google ADK
from tortoise.framework import adk_before_model_callback, sdk_reflex
agent = LlmAgent(name="...", model="...",
                 before_model_callback=adk_before_model_callback(sdk_reflex(sdk)))

# LangGraph
from tortoise.framework import langgraph_pre_model_node, sdk_reflex
graph.add_node("volunteer", langgraph_pre_model_node(sdk_reflex(sdk)))
graph.add_edge("volunteer", "model")           # immediately before the model node

# Semantic Kernel
from semantic_kernel.filters import FilterTypes
from tortoise.framework import semantic_kernel_prompt_render_filter, sdk_reflex
kernel.add_filter(FilterTypes.PROMPT_RENDERING,
                  semantic_kernel_prompt_render_filter(sdk_reflex(sdk)))
```

Providers: `sdk_reflex(sdk)` (in-process `TortoiseSDK.volunteer_context()`) or
`http_reflex(base_url, api_key)` (`POST /v1/context`). Endpoint and credential
are EXPLICIT arguments — embedded middleware carries no ambient identity
resolution; the #2369 D1 chain belongs to the hook process, not the app.

Posture, identical to the harness seams: **content fail-open** (degraded,
clean-empty, unreachable, or a framework object of an unexpected shape injects
NOTHING and never raises into the turn; Semantic Kernel's own `next` error is
the app's and DOES propagate) and **W4 opt-in** (importing
`tortoise.framework` pulls in no framework and registers nothing — the app
registers the hook explicitly).

## Verification

W3 harness seam grade (tests/eval/harness or tests/test_*): executes the
SHIPPED `volunteer-turn.sh` against a seeded embedded graph and asserts the
per-harness output contract carries the reflex block (see the wave-1 seam
test in tests/).

The Cursor `sessionStart` injection half (#2122) is graded
the same way in `tests/test_cursor_capture_hook.py`: the shipped
`tortoise/cursor-hooks/session-start.sh` is executed from an INSTALLED path
with a fake reflex, and the emitted `{"additional_context": …}` contract, the
broken-PATH-shim fallback (#8106's class, closed for this seam), the
empty-answer silence and the inert-with-evidence branch are asserted.

The framework seams are graded the same way by
`tests/test_framework_middleware.py` (#2125): one seeded embedded graph drives
all three adapters through the REAL `sdk_reflex` provider and asserts the
identical block lands in each framework's prompt, plus the fail-open / opt-in /
window-bounding contract.
