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

For a harness whose per-turn surface is a native PLUGIN rather than a shell
command (Hermes, #2121), the callback invokes the same `tortoise volunteer`
CLI directly — the arrow on the left is the only part that differs.

Install (agent-first onboarding, #2123/#2124 mandate): `tortoise install
<harness>` for codex / claude / cline / devin / cursor / hermes / pi / openclaw. Everything else
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
| #2124* | Cline | `UserPromptSubmit` | `.cline/hooks/UserPromptSubmit` (project) or `~/.cline/hooks` (global; enable Hooks in settings) | `contextModification.context` | shipped (`install cline`) — but the contract can no longer be re-verified from Cline's docs (2026-10-18: the Hooks page is a stub, and the SDK-plugin hook list has no `UserPromptSubmit`); graded by our tests only, delivery unverified — see the correction log |
| #2124* | Devin | `UserPromptSubmit` | `.devin/hooks.v1.json` (also `~/.config/devin/config.json` `hooks`) | `hookSpecificOutput.additionalContext` | shipped (`install devin`); primary docs re-fetched 2026-10-11 — file IS the hooks object (no `"hooks"` wrapper), stdin carries `prompt`; `matcher` omitted (tool-events only) |
| #2119 | Pi (pi-coding-agent) | session lifecycle only (no per-turn prompt hook in the extension API today) | `~/.pi/agent/extensions/` TS extension | n/a — per-turn injection not reachable via extension API | MCP pull + session-start digest (existing) + documented limitation |
| #2122 | Cursor | `sessionStart` → `additional_context`; per-turn `beforeSubmitPrompt` is observe/block ONLY | `~/.cursor/hooks.json` (flat script entries, absolute command; project `.cursor/hooks.json` is workspace-trust-gated) | `{"additional_context": …}` (snake_case) on `sessionStart` | **shipped** — `tortoise install cursor` registers the `sessionStart` cold-context hook (contract v4) *plus* the `sessionEnd` capture hook; per-turn push stays Cursor-blocked (watch item) |
| #2120 | OpenClaw | **native plugin** — per-turn hook `before_prompt_build` → `{prependContext}` (in-process; OpenClaw has NO shell-hook surface) | `~/.openclaw/extensions/tortoise/` (plugin dir; `openclaw.plugin.json` + entry `index.mjs`) → `before_prompt_build`; capture via the `llm_output` hook → `$OPENCLAW_STATE_DIR/tortoise/turns/<session>.jsonl` (one file per session) → `tortoise sessions import --harness openclaw <file>` | `PluginHookBeforePromptBuildResult.prependContext` | **shipped** — `tortoise install openclaw` writes the plugin + `plugins.entries.tortoise.enabled`; injection graded by `tortoise/openclaw-plugin/index.test.mjs`, install by `tests/test_openclaw_seam.py` |
| #2121 | Hermes | `pre_llm_call` (directory plugin: `register(ctx)` + `ctx.register_hook`) | `<dir>/.hermes/plugins/tortoise-volunteer/` (`plugin.yaml` + `__init__.py`, written by `tortoise install hermes`) | the callback RETURNS `{"context": "<block>"}`, injected into the current turn's user message | written by `install hermes`, but **INERT until two opt-ins** (`HERMES_ENABLE_PROJECT_PLUGINS=1` + `hermes plugins enable tortoise-volunteer`) — the installer prints both; ingestion: `tortoise sessions import --harness hermes --file <state.db>` (reads `$HERMES_HOME/state.db` read-only) |
| #2124* | Gemini CLI | **`BeforeAgent`** ("after user submits prompt, before planning") — the model hook `BeforeModel`/`llm_request` also exists, but it fires per LLM CALL | `.gemini/settings.json` (project) > `~/.gemini/settings.json` (user) > `/etc/gemini-cli/settings.json` (system) > extensions, under `hooks` — the SAME matcher-group shape codex/claude/devin use; `timeout` is MILLISECONDS (default 60000) | `hookSpecificOutput.additionalContext` (the Claude field) | **NOT built — demand-gated.** A thin registration, not an unbuilt adapter (re-measured 2026-10-18 — see the correction log); `tortoise install gemini` does not exist yet |
| #2124* | OpenCode | plugin `chat.message` (`output.parts` → append a Part) / `experimental.chat.system.transform` (`output.system`) | `.opencode/plugins/*.ts` (project) or `~/.config/opencode/plugins/` (global) — a TS plugin module like OpenClaw's, NOT a shell registration | none published in prose; the hook exists in the published TYPE definitions | **NOT built — demand-gated.** `chat.message` is absent from OpenCode's docs hook list and present in `@opencode-ai/plugin@1.19.3` types (measured 2026-10-18) |
| #2125 | Frameworks (SDK-level middleware: Google ADK / LangGraph / Semantic Kernel) | ADK `before_model_callback`; LangGraph pre-model node; SK Prompt Render Filter | project code imports `tortoise/framework.py` and registers the returned hook | the reflex `block` appended to the prompt (ADK: request instructions; LangGraph: one appended message; SK: `rendered_prompt` prefix) | shipped, **opt-in** — `tortoise/framework.py` + the `#2125` seam grade |

\* #2124 (harness adapters) is **demand-gated by a recorded decision on the
issue** — "do not build speculative parsers for platforms with unproven
distribution … a full adapter is built when a user/team actually runs that
harness against Tortoise". Shipped: the shared reflex script plus the per-turn
READ registration for **codex / claude / cline / devin**. The two remaining
harnesses are **measured but deliberately unbuilt**: Gemini CLI's `BeforeAgent`
(its `BeforeModel`/`llm_request` model hook is real too — `BeforeAgent` is the
per-turn seam, so it is the one to register) and OpenCode's plugin hooks are
thin registrations on a real surface (see the correction log), so when demand
arrives the work is a REGISTRATION, not a research pass — their session-store
parsers stay deferred with them, and a parser target is recorded only where it
was VERIFIED (Gemini CLI's is, in the correction log; OpenCode's is not, and
stays unrecorded). The sentence that stood here — "the shared script's default
emits the bare block (harmless where unsupported)" — is **retracted**: at
Gemini CLI a bare-text emission is not an injection at all (non-JSON stdout →
`systemMessage`, shown to the user, never the model's context; see the
correction log).

> **#2125 framework-name drift (recorded 2026-10-15):** this row previously
> listed *Vercel AI SDK / LangChain / Mastra / OpenAI Agents SDK*. Issue #2125's
> title + scope — **Google ADK, LangGraph, Semantic Kernel** — are the
> authoritative pair (they trace to `platform-landscape.md` §3, "Tier 2 —
> adapters"), and they are what `tortoise/framework.py` implements. The old
> list was a sketch written before the issue was filed; the issue wins.

## Correction log

### #2124's Gemini / OpenCode / Cline rows (re-measured 2026-10-18)

Three sibling lanes (#2120, #2121, #2122) each found this matrix's per-row
status optimistic — a row describing a contract nobody had built. This pass
re-measured #2124's remaining rows against PRIMARY sources *before* a lane
builds on them. The issue's demand gate still holds (neither adapter is built
here); what changed is what a future lane would have to build, and which
claims were wrong.

| Claim | Re-measurement |
|---|---|
| Gemini CLI: *"`BeforeModel` mutates `llm_request.messages`"* (issue #2124 body) | **The seam is real; the CHOICE of seam was wrong.** `BeforeModel` / `llm_request` DOES exist in Gemini CLI — re-fetched 2026-10-18 from `docs/hooks/reference.md` (`google-gemini/gemini-cli` @ main), the same page `platform-landscape.md:192-193` records it from: *"Fires before sending a request to the LLM. Operates on a stable, SDK-agnostic request format"*, input `llm_request` {model, messages, config}, output `hookSpecificOutput.llm_request` *"overrides parts of the outgoing request (for example, changing models or temperature)"*. What #2124's row got wrong is the SEAM it named: `BeforeModel` is a **per-LLM-CALL model hook**, while Gemini CLI ships a first-class **per-turn AGENT hook**, `BeforeAgent` — *"Fires after a user submits a prompt, but before the agent begins planning"* — whose output carries **`hookSpecificOutput.additionalContext`**, *"Text that is appended to the prompt for this turn only"*, the same field Claude/Codex/Devin use, and no message rewriting to construct. Gemini CLI's hooks system (`docs/hooks/index.md` + `docs/hooks/reference.md`, `google-gemini/gemini-cli` @ main) is 11 events, **including BOTH** `BeforeAgent` and `BeforeModel`. `llm_request` is also Google ADK's parameter name (#2125's `before_model_callback(callback_context, llm_request)`): the name collides across two different harnesses, which is how this row was misread once already — the collision is not identity in either direction. Config layers: `.gemini/settings.json` (project) > `~/.gemini/settings.json` (user) > `/etc/gemini-cli/settings.json` (system) > extensions, in the same `{"hooks": {Event: [{matcher, hooks: [{type, command, timeout}]}]}}` matcher-group shape. Two traps for the next lane: Gemini's `timeout` is **milliseconds** (default 60000), and matchers are REGEX for tool events but EXACT STRINGS for lifecycle events. |
| *"shared script's default emits the bare block (harmless where unsupported)"* | **Retracted for Gemini CLI — it is not an injection there.** Gemini's stdio rule is strict JSON: *"If stdout contains non-JSON text, parsing will fail. The CLI will default to 'Allow' and treat the entire output as a `systemMessage`"*, and its reference defines `systemMessage` as *"Displayed immediately to the user in the terminal"*. A bare block therefore reaches the TERMINAL, not the model's context — a silent non-injection that must not be read as the fail-open posture. The shared script's `claude` arm already emits the right FIELD; it carries `hookEventName: "UserPromptSubmit"` rather than `"BeforeAgent"`, so a `gemini` arm is a small addition once demand arrives. |
| OpenCode: *"(`chat.message` plugin hooks)"*, session store *"`.opencode/sessions` JSONL"* | **The hook is real; the store claim is unsupported.** `@opencode-ai/plugin@1.19.3` `dist/index.d.ts` declares `"chat.message"` (`output: {message, parts}` — mutable, so a plugin can append a Part), `"experimental.chat.system.transform"` (`output.system: string[]`) and `"experimental.session.compacting"` (`output.context: string[]`). The DOCS page (`opencode.ai/docs/plugins`) documents plugin loading (`.opencode/plugins/`, `~/.config/opencode/plugins/`) and the `event` subscription list — it does NOT list `chat.message`, so the per-turn surface is TYPED but undocumented in prose. The `.opencode/sessions` JSONL store is **not verified**: nothing in this pass corroborates that path, so the parser target is left UNRECORDED rather than recorded wrong. |
| Cline: shipped seam, *"`UserPromptSubmit` → `contextModification.context`"* | Injection seam is shipped and graded (`tests/test_platform_seams.py::test_install_cline_writes_hook_file`, `::test_volunteer_turn_hook_cline_contract`), but the CONTRACT is no longer re-verifiable from Cline's docs (2026-10-18): `docs.cline.bot/customization/hooks.md` is now a stub — *"See details under SDK Plugins page"* — and that page documents `hooks: {beforeRun, afterRun, beforeModel, afterModel, beforeTool, afterTool, onEvent}` with writing-plugins guidance to *"use lifecycle hooks for observation …, not for modifying agent behavior"*. Neither `UserPromptSubmit` nor `contextModification` appears in the current index (`docs.cline.bot/llms.txt`). The seam is graded by OUR tests only; indicator 2 of #2124 (primary-source verification) stays OPEN for Cline until the docs are re-fetched or the contract is re-grounded. |
| Devin CLI registration, *"pending a primary-doc re-fetch"* (the wave-1 comment) | **Shipped** (#8108), re-fetched 2026-10-11. No action. |

**Reconciliation with the sibling record.** This correction does not supersede
`docs/research/2026-09-01-gbrain-learnings/platform-landscape.md` (`:47`, `:85`,
`:111`, `:192-194`, `:239` — the live Gemini CLI record, most recently
maintained by #8110): the two now say the SAME thing, **because the sibling's
Gemini rows were corrected in this same pass (2026-10-18)**. Before that edit it
recommended the seam this correction retracts — `:47` "`BeforeModel` … → per-turn
injection", `:111` "mutable `llm_request.messages` → append pointer block … Real
per-turn seam", `:193` "→ per-turn injection by mutating
`llm_request.messages`", `:239` "`BeforeModel` message mutation" — so a reader
arriving from the sibling doc still got the wrong seam. What both records now
say: Gemini CLI's `BeforeModel` hook takes `llm_request` (per LLM CALL), AND
`BeforeAgent` → `hookSpecificOutput.additionalContext` is the per-turn seam to
register, because `#2125`'s ADK callback independently names its own parameter
`llm_request`. An earlier draft of this row concluded from that shared
parameter name that the seam belonged to a different harness; that reading was
WRONG, is corrected in the row above, and is not carried forward. The agreement
is pinned per LINE, not by symbol presence
(`tests/test_platform_seams.py::test_seam_matrix_pins_the_measured_gemini_contract`)
— symbol presence passes on the contradicting text, because the lines that
carried the wrong recommendation also contained the two symbol names. If either
doc is re-measured and they diverge again, fix BOTH here in one pass.

Two facts a future Gemini adapter needs for its INGESTION half (measured
2026-10-18 in `packages/core/src/services/chatRecordingService.ts` and
`packages/core/src/config/storage.ts`): the transcript is
`<projectTempDir>/chats/session-<timestamp>-<shortId>.jsonl` (older `.json`
files are normalized to `.jsonl`), subagent sessions nest under
`chats/<parentSessionId>/`, and the project directory is a **slug** resolved
through the `projects.json` registry under the global runtime dir — the legacy
per-project HASH directory is migrated away, so a parser that hashes the
project root would read a directory Gemini no longer writes.

Sources (fetched 2026-10-18): `google-gemini/gemini-cli@main`
`docs/hooks/index.md`, `docs/hooks/reference.md`,
`packages/core/src/services/chatRecordingService.ts`,
`packages/core/src/config/storage.ts`; the `@opencode-ai/plugin@1.19.3`
tarball's `dist/index.d.ts`; `opencode.ai/docs/plugins`;
`docs.cline.bot/llms.txt`, `customization/hooks.md`, `sdk/plugins.md`,
`sdk/guides/writing-plugins.md`.

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

### #2121 Hermes — corrected contract (2026-10-11, primary source)

The wave-1 row above previously read "`pre_llm_call` (agents-hub) /
`.agents.yml` actions / tool-result text = the block / hook output mapping
documented; **parser entry added**". Two claims in it were wrong and are
corrected here:

1. **No parser entry existed.** A fleet-wide search for a Hermes parser
   returned nothing (`git log --all -S"parse_hermes"` is empty; no
   `hermes` reference in `tortoise/` or `tests/` outside unrelated
   fixtures), so the status was aspirational, not shipped. The parser
   (`tortoise/session_import/parsers.py::parse_hermes`) and the
   `--harness hermes` CLI choice land with #2121.
2. **`.agents.yml` actions is not a Hermes surface.** Code search across
   `NousResearch/hermes-agent` returns 0 hits for `agents.yml` /
   `agents-hub`; the injection surface is the `pre_llm_call` PLUGIN hook,
   which is REGISTERED FROM PYTHON, not from a YAML action, and whose
   RETURN VALUE (not a tool result) carries the block:
   * `website/docs/developer-guide/plugins/index.md` §"`pre_llm_call`
     context injection" — "Per-hook context is capped at `10,000`
     characters by default. Anything above the cap is written to
     `$HERMES_HOME/hook_outputs/<session_id>/<uuid>.txt`";
   * `plugins/AGENTS.md` — general plugins live in `plugins/<name>/`,
     `~/.hermes/plugins/`, `./.hermes/plugins/`; `register(ctx)` registers
     `pre_llm_call`;
   * `agent/turn_context.py::_collect_pre_llm_call_context` — the callback's
     `{"context": ...}` (or string) is appended to the CURRENT turn's user
     message; a raising callback is caught and logged (fail-open);
   * `hermes_state.py` — `DEFAULT_DB_PATH = get_hermes_home() / "state.db"`,
     and `hermes_state_common.py` carries the `sessions` / `messages` DDL
     the parser reads (live transcript = `messages.active = 1`, ordered by
     `id`, which the store itself uses — `timestamp` is not monotonic).

Because Hermes' read seam is a Python plugin rather than a shell command,
the seam does NOT add a `hermes` arm to `volunteer-turn.sh` (that would be
dead code) — the plugin invokes the SAME `tortoise volunteer` CLI the shell
hook does, with the harness `session_id` passed through for the reflex's
cross-turn re-mention suppression. Ingestion rides `tortoise sessions import`
as the wave-1 note on #2121 prescribes (no hook-driven live capture).

**Activation is TWO opt-ins, and the installer names both (correction
2026-10-12).** A `tortoise install hermes` write is not by itself a live
seam, because Hermes gates plugin loading twice
(`hermes_cli/plugins_discovery.py` @ main): project plugins under
`./.hermes/plugins/` are scanned only when `HERMES_ENABLE_PROJECT_PLUGINS`
is enabled (disabled by default), and a non-bundled manifest is loaded only
when its key is in `plugins.enabled` (`gate_manifest` — "not enabled in
config (run `hermes plugins enable <lookup_key>` to activate)"; upstream
docs: "plugins are opt-in"). So the install prints the two required steps
(`HERMES_ENABLE_PROJECT_PLUGINS=1` and
`hermes plugins enable tortoise-volunteer`), and the plugin writes the same
`capture-errors/<harness>-install.json` (`kind: install-inert`) breadcrumb the
shipped shell hooks write when its reflex cannot resolve — an inert install
must leave evidence, never read as a working seam (#4314). Caveat
(2026-10-13): unlike the shell seams, whose `session-start.sh` renders the
record, a capture-less Python plugin has **no automated reader** — `session
verify` enumerates `capture_install.CAPTURE_SEAM` (no `hermes` arm) and the
Python renderer owns only `capture-failure`; the record is operator-inspectable
at its canonical slot, and the reader is tracked by #8360.

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

* **Content fail-open**: any reflex failure (unreachable, misconfig, empty
  graph, nothing above the confidence gate) → the hook emits NOTHING and
  exits 0. The agent turn proceeds untouched.
* **Auth fail-closed**: hosted capture/auth channels keep their own deny
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
* W4 user-exposure opt-in: per-turn injection ships behind the `install`
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

The Hermes seam grades the same way at its own surface:
`tests/test_hermes_seam.py` installs the shipped plugin into a temp root,
imports it, fakes the reflex CLI, and asserts the `register(ctx)` →
`pre_llm_call` → `{"context": ...}` path (plus the cap, fail-open, and the
`parse_hermes` ingest leg over a real SQLite `state.db`).
