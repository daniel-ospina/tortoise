---
title: "Research — Agent-Harness Landscape for the Volunteering-Memory Reflex (#2080)"
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

# Research — Agent-Harness Landscape: End-State Compatibility Targets for the Volunteering-Memory Reflex

**Findings date:** 2026-09-01
**Epic:** #2080 (W4 volunteering-memory reflex) — feeds the end-state platform matrix for `POST /v1/context` / SDK `volunteer_context()` distribution ("we start shipping without all platforms, but we do need to include them at the end — goal is good distribution coverage").
**Question:** which agent platforms/harnesses should Tortoise include as end-state compatibility targets — and which need a per-turn push/injection seam vs which work via MCP pull alone?
**Method:** external web research (Perplexity sonar, accessed 2026-09-01; primary-source docs pages fetched where they existed — cursor.com/docs/hooks, cursor.com/docs/reference/third-party-hooks, blog.gitbutler.com/cursor-hooks-deep-dive, learn.chatgpt.com/docs/hooks, geminicli.com/docs/hooks/reference, docs.devin.ai CLI hooks/plugins, adk.dev callbacks, learn.microsoft.com Semantic Kernel filters) + prior art (`delivery-shape.md` Topology A–D + harness seams table, `ux-research.md` injection seams, `research-brief.md` gbrain seam table). No code touched.
**Scope discipline:** this is a distribution/surface map for a scope decision, not an implementation plan. No files beyond this doc.
**Confidence tags (research protocol §5a):** **[HIGH]** = primary-source docs or 2+ independent sources; **[MEDIUM]** ⚠️ = 2 sources; **[LOW]** ⚠️ = single source. URLs in ## Raw Notes.

---

## Executive Summary

1. **MCP pull is now universal — the tiering question is entirely about push seams.** Every harness on the landscape (Copilot, Cursor, Codex, Gemini CLI→Antigravity, Zed, Warp, Goose, OpenHands, Devin, Windsurf, Cline, Roo Code, Aider, Continue, OpenCode, Void, Droid) is an MCP *client* as of 2026. Tortoise's shipped MCP server (`tortoise_search`/`recall`/`analyze`/`session_capture`) therefore reaches all of them with zero per-harness build; the end-state matrix only decides **which per-turn push seams to build**, not which platforms to support.
2. **The industry converged on the Claude Code hook shape (`UserPromptSubmit` → `additionalContext`)** — Codex, Devin CLI, Cline, and Cursor's Claude-hook mapping all mirror it; Cursor and Devin even auto-load `.claude/` hook configs. One Claude-hooks-compatible reflex script + per-harness registration files covers nearly the whole Tier-1/2 seam surface (gbrain already proved this with its claude-code seam).
3. **Two prior decisions are now stale and should be re-decided:** (a) **Codex is no longer "parked"** — Codex CLI shipped a real hooks engine (experimental v0.114 → stable v0.124) with `UserPromptSubmit` → `hookSpecificOutput.additionalContext` injection (plus `additionalContextLimit`, `SessionStart`, `Stop`, MCP-tool hooks, and opt-in MCP since v0.147.0). gbrain's cautionary-tale row describes a pre-hooks Codex and is outdated; (b) **Cursor is no longer "MCP pull-only"** — Cursor now has hooks, but the per-turn path (`beforeSubmitPrompt`) can *observe/block only* — it cannot inject; only `sessionStart` supports `additional_context` injection (with community-reported delivery reliability issues). Cursor stays a weak per-turn push target.
4. **Distribution is not stars.** The corporate/default channels dominate end-user reach — GitHub Copilot (~20M users / 4.7M paid, 77K enterprise customers), Cursor ($2B ARR, 1M+ DAU), Codex (ChatGPT + enterprise), Gemini CLI→Antigravity (Google), Devin+Windsurf (Cognition), Goose (Block), Amp (Sourcegraph). The open-source harnesses (OpenCode ~172–202K★, Gemini CLI ~98–105K★, OpenHands ~78–86K★, Cline ~59–67K★, Goose ~48–54K★, Aider ~39–46K★, Roo Code ~24K★ archived) matter for **developer mindshare** — the people who build agents are the P1 integrators — but the tier-1 distribution decision hinges on the closed platforms' seams.
5. **Recommended end state:** **Tier 1 (build):** Claude Code (primary), **Codex (un-park)**, Pi (own extension), OpenClaw, Hermes. **Tier 2 (build later / adapter):** Devin CLI, Gemini CLI→Antigravity, OpenCode, Cline, Cursor (session-start only for now), plus framework seams Google ADK (`before_model_callback`), LangGraph (pre-model node), Semantic Kernel (prompt-render filter). **Tier 3 (MCP pull only — verify, no build):** Copilot, Zed, Warp, Goose, OpenHands, Aider, Continue, Windsurf, Droid, CrewAI, Void. **Tier 4 (park/skip):** Amp, Roo Code, Koding, Melty, + anything unverified.

---

## 1. Harness Landscape Table

> Traction = rough order of magnitude, mid-2026 snapshots (see ## Raw Notes for sources). "Push seam" = a documented mechanism to inject context into the model's context *before* it answers a turn (the reflex's requirement). "MCP client" = the harness can consume Tortoise's MCP server today.

| Harness | MCP client | Push seam mechanism (per-turn unless noted) | Traction / backing | Distribution tier |
|---|---|---|---|---|
| **Claude Code** (Anthropic) | ✅ native | `UserPromptSubmit` / `SessionStart` hooks → `additionalContext`; hooks can be shell commands, **HTTP endpoints**, MCP tool calls, LLM prompts, subagents; ≤10 000-char cap; fail-open exit-0 | ~79–143K★; largest paid CLI+IDE agent base; Anthropic default | **Tier 1 (primary, decided)** |
| **Codex** (OpenAI) | ✅ opt-in since v0.147.0 (2026-08-07) | **SHIPPED hooks engine** (stable v0.124+): `UserPromptSubmit` / `SessionStart` / `Stop` / `PreCompact` → `hookSpecificOutput.additionalContext` (+`additionalContextLimit`, default 2500-token threshold, large-output truncation); MCP-tool hooks; plugins can bundle hooks | ChatGPT ecosystem + enterprise; OpenAI default; huge CLI/cloud base | **Tier 1 (UN-PARK — prior "parked" is stale)** |
| **Pi** (this harness) | ✅ | Extension seam (decided, epic issue) | internal | **Tier 1 (decided)** |
| **OpenClaw** | ✅ (MCP host) | In-process plugin: `registerContextEngine` → `systemPromptAddition` every `assemble()` (gbrain production seam) | memory-agent niche, gbrain-proven | **Tier 1 (decided, done)** |
| **Hermes** | ✅ (MCP host) | `pre_llm_call` plugin hook (decided, epic issue) | memory-agent niche | **Tier 1 (decided, done)** |
| **Devin** (Cognition; owns Windsurf) | ✅ (org-scoped, X-Org-Id) | CLI hooks: `UserPromptSubmit` → `hookSpecificOutput.additionalContext`; **auto-loads existing `.claude/` hooks**; `.devin/hooks.v1.json`; cloud-session hooks best-effort/fail-open; plugins in closed beta | Cognition-backed commercial agent; CLI/desktop/cloud; high enterprise mindshare | **Tier 2** |
| **Gemini CLI → Antigravity CLI** (Google) | ✅ (`--mcp-config`, extensions) | `BeforeModel` hook fires pre-LLM-call with mutable `llm_request` (messages) → per-turn injection; `SessionStart`; hooks bundleable in extensions; Gemini CLI service ends 2026-06-18 for consumer tier — **target Antigravity** (keeps hooks/skills/MCP; extensions → plugins) | ~98–105K★ (Gemini CLI); Google default channel | **Tier 2** |
| **OpenCode** (sst) | ✅ (MCP via plugins/config) | Plugin hooks: `chat.message` (transform outgoing messages), system-prompt transforms, compaction-context injection; JS/TS plugin API | **~172–202K★ — most-starred OSS agent**; gbrain's third seam (already in prior art) | **Tier 2** |
| **Cline** (VS Code) | ✅ | `UserPromptSubmit` hook → `contextModification` (inject text into conversation context); plugin SDK `beforeModel` / `beforeRun` hooks; file-based lifecycle hooks | ~59–67K★; top OSS VS Code agent; huge Marketplace installs | **Tier 2** |
| **Cursor** (Anysphere) | ✅ | Hooks exist but **per-turn injection NOT supported**: `beforeSubmitPrompt` = observe/block only (output limited to `continue`/`user_message`); `sessionStart` → `additional_context` (snake_case) injects at session start only, with reported delivery bugs; loads Claude Code hooks but honors `additional_context`, not `additionalContext` | **$2B ARR (Feb 2026), 1M+ DAU, 50K businesses** | **Tier 2 (session-start only today; MCP pull per-turn)** |
| **GitHub Copilot** | ✅ tools only (no MCP resources/prompts) | **None.** `.github/copilot-instructions.md` + rules are static-per-session; no per-turn hook documented | **~20M users / 4.7M paid, 77K enterprise customers**; VS Code/JetBrains default | **Tier 3 (huge reach, pull-only)** |
| **Zed** (Zed Industries) | ✅ native | None per-turn; MCP context servers + slash commands (user-triggered pull); no pre-prompt hook | ex-Atom team; growing dev mindshare | **Tier 3** |
| **Warp** | ✅ native (Settings → Agents → MCP) | None; MCP tools surfaced to Agent mode | AI-native terminal; VC-backed | **Tier 3** |
| **Goose** (Block) | ✅ MCP-first (extensions = MCP servers) | None; recipes/skills package prompts+extensions (static per-run) | ~48–54K★; Block/Square backing | **Tier 3** |
| **OpenHands / OpenDevin** (All Hands AI) | ✅ (JSON MCP config, OAuth) | None per-turn; "microagents" inject knowledge as static context; cloud/enterprise | ~78–86K★, 565 contributors; All Hands AI cloud | **Tier 3** |
| **Aider** | ⚠️ experimental (PR #3672/#3937) | None; repo map + edit formats are static-ish | ~39–46K★; OSS terminal staple | **Tier 3** |
| **Continue** (VS Code/JetBrains) | ✅ (MCP context provider + tools) | None per-turn; rules files + lifecycle hooks are post-processing, not pre-model injection | ~3.0M VS Code Marketplace installs | **Tier 3** |
| **Windsurf / Cascade** (Codeium → Cognition) | ✅ | `pre_mcp_tool_use` / `post_mcp_tool_use` hooks = MCP-governance only (block/allow), no prompt injection | acquired by Cognition (Devin) — large editor install base | **Tier 3** |
| **Droid** (Factory) | ✅ | None documented; terminal agent + cloud, BYOK | proprietary; Terminal-Bench leader; low stars | **Tier 3/4** |
| **CrewAI** (framework) | ✅ (`MCPServerAdapter`) | Task/crew lifecycle hooks (kickoff, task start/end) — *task-level*, not per-LLM-call; weaker seam | ~25K★+; OSS multi-agent framework | **Tier 3 (framework)** |
| **Void** (VS Code fork) | ✅ (v1.4.1+) | None documented | OSS Rust/TS editor; niche | **Tier 4** |
| **Amp** (Sourcegraph) | ✅ (connects to SG MCP server) | None documented | enterprise agent platform | **Tier 4** |
| **Roo Code** (RooVetGit → RooCodeInc) | ✅ | None shipped per-turn; custom TS/JS tools + modes; hooks still a discussion (#2415); **repo reported archived ~24.3K★** ⚠️ | ~24K★ (archived signal ⚠️) | **Tier 4** |
| **Koding** | unverified ⚠️ | unverified ⚠️ | unclear — could not confirm the agent | **Tier 4** |
| **Melty** | ⚠️ "MCP-compatible context bridging" (single 3rd-party claim) | None documented | OSS chat-first editor; tiny | **Tier 4** |
| **Google ADK** (framework) | ✅ (MCP toolkits) | **`before_model_callback`** — runs before each LLM call with mutable `LlmRequest` (messages); can modify or short-circuit; `SessionService` per-session state | Google framework; GCP/Gemini enterprise | **Tier 2 (framework seam)** |
| **LangGraph / LangChain** (framework) | ✅ (`langchain-mcp-adapters`, tool interceptors) | **Graph-native**: insert a pre-model node that appends volunteered context to state/messages (the standard pattern); `create_agent`-level pre-model hooks; MCP `tool_interceptors` for pull-time context | dominant OSS agent framework | **Tier 2 (framework seam)** |
| **Semantic Kernel** (Microsoft) | ✅ first-class (host + server) | **Prompt Render Filter** modifies the rendered prompt before the model call; Function Invocation Filter; MCP plugins | Microsoft/Azure enterprise | **Tier 2 (framework seam)** |

---

## 2. Distribution Analysis — Which Channels Matter

### 2.1 The three audiences, with different weights

| Channel | Who | What they can consume today | Why it matters |
|---|---|---|---|
| **(a) Developer mindshare — who *builds* agents (P1 integrators)** | Users of OpenCode, Cline, OpenHands, Aider, Continue, LangGraph, ADK, CrewAI, Semantic Kernel | MCP pull (all); real push seams on OpenCode, Cline, and the three frameworks | These are the people who will integrate `POST /v1/context` / `volunteer_context()` into *custom apps* — the P1 persona. Tortoise's why-block differentiation gets adopted here first. OSS harness reach is star-driven but this is the build audience. |
| **(b) End-user install base — who *runs* agents** | Copilot (20M users), Cursor (1M+ DAU), Claude Code, Codex (ChatGPT), Gemini/Antigravity, Zed, Warp, Devin/Windsurf | MCP pull; push only where seams exist (Claude Code, Codex, Devin CLI, Gemini, Cursor session-start) | This is the "good distribution coverage" goal. The reflex's value is per-turn memory injection — on pull-only platforms it degrades to the second-best "call a tool every turn and trust the reflex to say nothing" shape (delivery-shape Topology D). |
| **(c) Corporate/default channels** | Copilot (GitHub/Microsoft), Codex (OpenAI), Gemini/Antigravity (Google), Devin+Windsurf (Cognition), Goose (Block), Amp (Sourcegraph enterprise), Semantic Kernel (Microsoft) | Same as (b); enterprise policy controls (managed hooks in Codex `requirements.toml`, Cursor Enterprise hooks, Devin org plugins) | Default channels are where memory becomes a *standard*; they also gate on security (managed hooks = an admin can enforce Tortoise's reflex org-wide — a real enterprise distribution path worth noting for Codex/Cursor/Devin). |

### 2.2 What the numbers say

1. **MCP pull is a solved problem end-to-end.** Every row in the landscape table is an MCP client. Tortoise's existing MCP server covers 100% of the harness surface for *pull* — the "Tier 3 verify-only" rows are free distribution already shipped. No build, only verification (W3 harness already grades MCP seams).
2. **The push-seam market settled on one shape.** `UserPromptSubmit → additionalContext` (or an equivalent pre-model mutate) is now implemented by Claude Code, Codex, Devin CLI, Cline (as `contextModification`), Cursor (mapping, but drop-injection), and Gemini CLI (`BeforeModel` `llm_request` mutation). A single Claude-hooks-format reflex script is portable across the majority of Tier 1/2 with per-harness registration files (`settings.json`, `.codex/hooks.json`, `.devin/hooks.v1.json`, `.cursor/hooks.json`, `hooks.json` for Gemini/Antigravity). gbrain's claude-code seam already is this script — the port is a rename + registration per harness, not N bespoke seams.
3. **Reach ≠ seam.** The three largest install bases split: Copilot (20M users) has **no** push seam → Tier 3 pull-only; Cursor (1M+ DAU) has session-start-only push → Tier 2 with a per-turn caveat; Codex (ChatGPT/enterprise) **now has the full seam** → Tier 1 un-park. Tortoise's per-turn reflex is most valuable exactly where per-turn seams exist — that's the distribution insight that puts Codex above Cursor in tier priority despite Cursor's larger DAU.
4. **OSS harnesses are the integration audience, not the install-base win.** OpenCode/Cline/OpenHands/Continue/Aider drive P1 builds; they are where `volunteer_context()` gets embedded into custom apps. OpenCode and Cline get real seams (Tier 2) because the build audience is there; the others ride MCP pull.
5. **Windsurf and Devin are one company (Cognition).** Windsurf's Cascade hooks (MCP governance only) and Devin CLI hooks (full injection) are the same org's surfaces — a Cognition adapter covers both, but only Devin CLI has the per-turn seam.

---

## 3. Recommended End-State Platform Matrix

> Principle: **a platform gets a push-seam build only when (seam is real and documented) × (distribution or mindshare justifies it).** Everything else is Tier 3 MCP-pull (verify only) or Tier 4 (park with reason). Tier 1/2 rows name the concrete seam mechanism.

### Tier 1 — Must-have push seams (build now or already decided/built)

| Platform | Concrete seam mechanism | Status / note |
|---|---|---|
| **Claude Code** (primary) | `UserPromptSubmit` + `SessionStart` hooks (`~/.claude/settings.json`) → script/stdin-JSON → `POST /v1/context` → `hookSpecificOutput.additionalContext` on stdout; HTTP-endpoint hooks as an alternative (`POST /v1/context` directly, no SDK). Fail-open exit-0; ≤800 ms / ≤10 000 chars. | **Decided + primary.** gbrain port already exists (`tortoise/claude-hooks/`). Keep as the reference seam; W3 grades it. |
| **Codex** (UN-PARK) | `.codex/hooks.json` / `config.toml [hooks]`: `UserPromptSubmit` → `hookSpecificOutput.additionalContext` (same shape as Claude Code); `additionalContextLimit` (default 2500-token threshold — keep blocks ≤ ~1–2K tokens); `SessionStart`/`Stop` for lifecycle; plugins can bundle hooks. | **Re-decide from "parked":** shipped hooks engine (stable v0.124+), MCP opt-in v0.147.0. gbrain's cautionary tale describes a pre-hooks Codex (contract-only fragments) — the W3 harness should grade the real Codex seam instead. Block budget must respect `additionalContextLimit`. |
| **Pi** (own harness) | Extension seam (epic issue, decided). | Decided. |
| **OpenClaw** | In-process plugin: `registerContextEngine` → `systemPromptAddition` per `assemble()` (gbrain production seam, byte-for-byte). | Decided + done (gbrain precedent). |
| **Hermes** | `pre_llm_call` plugin hook (epic issue, decided). | Decided + done. |

### Tier 2 — Should-have push seams (build after Tier 1; adapter-level work)

| Platform | Concrete seam mechanism | Why Tier 2 (not 1) |
|---|---|---|
| **Devin CLI** | `.devin/hooks.v1.json` (or reuse `.claude/settings.json` — Devin auto-loads `.claude/` hooks): `UserPromptSubmit` → `hookSpecificOutput.additionalContext`. Cloud-session hooks are best-effort/fail-open and fire only while the session machine is up. | Real seam + Cognition's enterprise reach; but cloud-session hooks are explicitly best-effort (not guardrail-grade) and plugins are closed beta. Adapter = register the same Claude-hooks script. |
| **Gemini CLI → Antigravity CLI** | `settings.json` hooks: `BeforeModel` fires pre-LLM-call with mutable `llm_request.messages` → append pointer block; `SessionStart` for session digest. Target **Antigravity** (consumer successor; keeps hooks/MCP/skills; extensions → plugins) — Gemini CLI service ends 2026-06-18 for the consumer tier. | Real per-turn seam + Google default channel; but transition turbulence (Gemini CLI → Antigravity) and hooks API still "🔬 experimental" in docs. Register once, verify on both CLIs. |
| **OpenCode** | Plugin (JS/TS): `chat.message` hook transforms outgoing messages pre-send; system-prompt transforms; compaction-context injection. | Most-starred OSS agent (~172–202K★) = the P1 build audience; gbrain already targets it as a third seam. Not Tier 1 because it's OSS-mindshare, not install-base. |
| **Cline** | `UserPromptSubmit` hook → `contextModification` (inject text as conversation context); or plugin SDK `beforeModel`/`beforeRun`. | Top OSS VS Code agent + the same hook shape; mindshare channel. |
| **Cursor** | `sessionStart` → `additional_context` (snake_case) session-start injection only; **no per-turn injection** (`beforeSubmitPrompt` observe/block only — community feature request open). MCP pull covers per-turn recall today. | Huge base ($2B ARR) justifies a session-start adapter; per-turn push is blocked by the harness — **watch the feature request; re-tier if `additional_context` lands on `beforeSubmitPrompt`**. Also note Cursor honors `additional_context`, not Claude Code's `additionalContext` — a compat seam must emit both. |
| **Google ADK** (framework) | `before_model_callback` on the runner/agent: mutate `LlmRequest` messages per LLM call; `SessionService` for per-session continuity. | Framework seam for agents built on Google's stack (P1 builds); distribution through GCP/Gemini. |
| **LangGraph / LangChain** (framework) | Graph-native: add a pre-model node that appends `volunteer_context` output to `messages` state (the standard LangGraph pattern); MCP `tool_interceptors` for pull-time enrichment. | Dominant OSS agent framework — highest P1-integration leverage among frameworks. |
| **Semantic Kernel** (Microsoft) | **Prompt Render Filter** (mutates the rendered prompt before the model call); MCP plugins (host + server). | Microsoft/Azure enterprise channel; filter-based seam is clean. |

### Tier 3 — MCP pull only (no build; verify via W3 harness)

Copilot (tools-only MCP; ~20M users — the biggest reach, deliberately pull-only because **no push seam exists**), Zed, Warp, Goose, OpenHands, Aider (MCP experimental), Continue, Windsurf (Cascade), Droid, CrewAI (task-level hooks are weaker than per-LLM-call; treat as pull + optional task-context later), Void.

- **Verification duty (not build):** confirm `tortoise_search`/`recall`/`analyze`/`session_capture` connect and behave in each (transport quirks: Copilot supports MCP *tools* only — no resources/prompts; Aider MCP is experimental; Cursor/Continue/Windsurf want stdio vs HTTP per config). The W3 harness should include a "pull-only roster" suite so these never regress.
- **Watch list (may move up):** Cursor `beforeSubmitPrompt` injection (open feature request); Copilot hooks (not shipped — a native hooks system would make the largest install base a Tier 2 overnight); Aider MCP going stable.

### Tier 4 — Parked / skip (with reason)

| Platform | Reason |
|---|---|
| **Amp (Sourcegraph)** | Enterprise agent platform; MCP client with no documented push seam; low standalone distribution vs Copilot/Cursor in the same enterprise slot. Park; revisit if Sourcegraph ships hooks. |
| **Roo Code** | No shipped per-turn seam (hooks still a GitHub discussion); repo **reported archived** ⚠️ (~24K★ at archive). Skip until the project's direction is clear. |
| **Koding** | Could not confirm the agent/harness identity or MCP support from research ⚠️. Verify before any commitment. |
| **Melty** | Open-source chat-first editor, tiny traction; MCP support only claimed by one third-party review ⚠️. Skip. |
| Anything not in this doc | No harness with meaningful distribution was found missing; if a new one ships a Claude-hooks-compatible seam, it inherits the Tier-2 adapter for free. |

---

## 4. Gaps / Uncertainty

1. **Cursor `sessionStart` injection reliability** — community threads report `additional_context` accepted by the hook runner but not always delivered to the model (timing issues); verify empirically before promising Cursor session-start push.
2. **Cursor `beforeSubmitPrompt`** is the watched item: if Cursor adds `additional_context` (or honors `additionalContext`) there, Cursor moves to a full Tier-2 per-turn seam with 1M+ DAU behind it. There is an active forum feature request.
3. **Codex hook maturity on cloud vs CLI** — CLI hooks are documented and stable; Codex cloud/IDE behavior may differ (hook execution environment, `additionalContextLimit` enforcement). The W3 harness should grade the CLI seam first, cloud second.
4. **Antigravity transition** — Gemini CLI is being replaced for the consumer tier (2026-06-18); Antigravity keeps hooks/MCP but config paths and API details are still settling. Target both CLIs; re-verify against Antigravity stable docs before locking the adapter.
5. **Devin cloud-session hooks are best-effort/fail-open** — fine for a memory reflex (fail-open is the reflex's native posture), but means no reliability guarantee in cloud sessions; document as such.
6. **Star counts are snapshots from mid-2026 roundups with wide reported ranges** (e.g., OpenCode 128–202K, Claude Code 79–143K depending on date/methodology) — treat as order-of-magnitude only; they are cited to rank mindshare, not to measure.
7. **Koding and Melty identity** — low confidence; both were researched but could not be firmly confirmed as active harnesses with MCP support. Flagged Tier 4 pending verification rather than dropped silently.
8. **MCP notifications / streaming** — if MCP's push-ish surfaces (notifications, resource subscriptions) mature, some Tier 3 platforms could gain a weak push path without a harness hook; out of scope for this matrix (delivery-shape Topology D note stands: MCP remains pull).

---

## 5. Reconciliation with Prior Decisions

| Prior decision (mission context / epic) | 2026-09-01 finding | Action |
|---|---|---|
| Claude Code — native HTTP hooks, primary | Confirmed; still the reference seam; HTTP-endpoint hooks make `POST /v1/context` directly consumable | Keep Tier 1 primary |
| Codex — **parked** (no shipped injection path per gbrain cautionary tale) | **Stale.** Codex shipped a hooks engine (stable v0.124+) with `UserPromptSubmit` → `additionalContext`; MCP opt-in since v0.147.0 (2026-08-07) | **Un-park → Tier 1.** gbrain's row describes pre-hooks Codex; W3 should grade the real seam. The cautionary tale's deeper lesson (contract-only seams teach nothing) still applies — grade the real Codex seam, not a contract. |
| Cursor — MCP pull-only (no push hook documented) | **Partially stale.** Hooks shipped; `beforeSubmitPrompt` observe/block only (no injection); `sessionStart` → `additional_context` injection with reliability caveats; Claude-hook compat honors `additional_context` naming | Tier 2 with session-start-only seam; per-turn remains MCP pull. Watch the feature request. |
| OpenClaw (plugin), Hermes (pre_llm_call), Pi (extension) | Confirmed | Tier 1, done/decided |
| Custom app via SDK `volunteer_context()` / `POST /v1/context` | Confirmed (delivery-shape Topology A/B) | The push seam every harness above ultimately calls — one shared implementation |

---

## Raw Notes

> Append-only evidence ledger. Source tag `[web]` = external, accessed 2026-09-01. Star counts are mid-2026 snapshot ranges from roundup sources; treat as order-of-magnitude.

### 2026-09-01 — [web] Landscape overview & traction roundups

- agentic-patterns / harness taxonomy: winder.ai/ai-agent-harness-comparison/ (2026 comparison, model lock-in, stack tradeoffs); explainx.ai/blog/top-10-open-closed-source-agent-harnesses-2026; linkedin.com/pulse/harness-landscape-path-forward (harnesses as orchestration layers: slash commands, subagents, hooks, MCP servers); arXiv 2602.14690 (harness engineering: skills with scripts, hooks, MCP).
- Star roundups: blog.arcbjorn.com/state-of-cli-coding-agents-2026 (OpenCode 182K, Claude Code 134K, Factory Droid low-stars proprietary); codepick.dev/en/compare/cline-vs-roo-code/; swarm.beetlix.com/compare/cline-vs-roo-code-2026; openhands.dev (85.8K★, 565 contributors); theaiagentindex.com/blog/openhands-review-2026 (77K+★, MCP on all plans); harnesses.sh (OpenHands ~79.9K★).
- Star ranges (2026 snapshots): OpenCode 128–202K (sst/opencode), Gemini CLI 98–105K (google-gemini/gemini-cli), Claude Code 79–143K (anthropics/claude-code), Cline 59–67K (cline/cline), Goose 48–54K (block/goose), Aider 39–46K (Aider-AI/aider), Roo Code ~24.3K (RooCodeInc/Roo-Code — **reported archived** ⚠️).
- User/ARR signals: Copilot ~20M total users (Jul 2025) / 4.7M paid subscribers (Jan 2026) / ~77K enterprise customers; usage-based billing from 2026-06-01 (aibusinessweekly.net/p/github-copilot-statistics; github.blog/news-insights/company-news/github-copilot-is-moving-to-usage-based-billing/). Cursor $2B+ ARR (Feb 2026), 1M+ DAU, 50K businesses (getpanto.ai/blog/cursor-ai-statistics; objectwire.org/technology/cursor; saastr.com). Continue.dev ~3.02M VS Code Marketplace installs (bodegaone.ai/blog/ides-are-dead-debate-misdirection).

### 2026-09-01 — [web] Cursor hooks (primary source: cursor.com/docs/hooks; cursor.com/docs/reference/third-party-hooks; blog.gitbutler.com/cursor-hooks-deep-dive)

- Cursor hooks: `hooks.json` (project `.cursor/hooks.json` + user `~/.cursor/hooks.json` + Enterprise/Team managed); events: sessionStart/sessionEnd, preToolUse/postToolUse/postToolUseFailure, subagentStart/subagentStop, beforeShellExecution/afterShellExecution, beforeMCPExecution/afterMCPExecution, beforeReadFile/afterFileEdit, **beforeSubmitPrompt**, preCompact, stop, afterAgentResponse/afterAgentThought, Tab + workspaceOpen hooks. Command-based (shell, JSON stdin/stdout) + prompt-based (LLM) types. Exit 0 = succeed; exit 2 = block; other = fail-open. Docs list "inject context at session start" as a capability.
- **beforeSubmitPrompt**: fires after user hits send, before backend request; GitButler deep dive (beta) noted output was NOT respected (no stop/context in beta); current docs + forum: output supports only `continue` / `user_message` when blocking — **no `additionalContext`** (forum.cursor.com/t/add-additional-context-to-beforesubmitprompt-hook-output/157231 — open feature request).
- **sessionStart**: documented `additional_context` (snake_case) optional string added to initial system context; reliability reported shaky (forum.cursor.com/t/cursor-sdk-1-0-28-sessionstart-runs-but-additional-context-never-reaches-the-model/168441; /t/sessionstart-hook-additional_context-is-never-injected-into-agents-initial-system-context/158452; /t/gaps-in-claude-code-sessionstart-support/153739 — "Cursor honors `additional_context` rather than Claude Code's `additionalContext`; suggest outputting both fields").
- **Claude Code hook compat** (third-party-hooks): Cursor loads `.claude/settings.json` (project/user/local) when "Include third-party Plugins, Skills" enabled; maps PreToolUse→preToolUse, PostToolUse→postToolUse, **UserPromptSubmit→beforeSubmitPrompt**, Stop→stop, SubagentStop→subagentStop, SessionStart→sessionStart, SessionEnd→sessionEnd, PreCompact→preCompact; both nested `hookSpecificOutput` and flat formats supported; exit code 2 blocking; prompt hooks supported; WebFetch/WebSearch tool names not mapped. **Implication: a Claude Code UserPromptSubmit hook's `additionalContext` field does NOT inject in Cursor** (beforeSubmitPrompt has no injection output) — compat is observation/blocking, not injection.

### 2026-09-01 — [web] Codex hooks (primary source: learn.chatgpt.com/docs/hooks — official, fetched .md)

- Hooks framework: events `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `UserPromptSubmit`, `SubagentStop`, `Stop`, `SessionStart`, `SubagentStart`, `SessionEnd`. Config: `hooks.json` or inline `[hooks]` in `config.toml` at `~/.codex/` or `<repo>/.codex/`; plugins can bundle hooks (developers.openai.com/plugins/build/plugins#bundled-mcp-servers-and-lifecycle-hooks). Trust flow: `/hooks` review-and-trust per hash; managed hooks (system/MDM/cloud/requirements.toml) trusted by policy; `--dangerously-bypass-hook-trust` for CI.
- **UserPromptSubmit**: input fields `turn_id` (Codex extension), `prompt`; stdout JSON `{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"..."}}` — **"That additionalContext text is added as extra developer context."** Blocking: `{"decision":"block","reason":...}` or exit code 2 + stderr. Plain text stdout also added as extra developer context.
- **additionalContextLimit**: default 2500-token threshold; above it Codex saves full text to disk and sends a shorter preview; set per-handler; only applies to `additionalContext` (tool feedback/continuation exempt). Large-output warning: "The context limit is enforced only for matching command hooks that return additionalContext."
- Maturity timeline (secondary): hooks experimental in v0.114.0 (SessionStart/Stop), **stable in v0.124.0**, newer events incl. UserPromptSubmit; MCP opt-in support since **v0.147.0 (released 2026-08-07, MCP 2026-07-28 revision + Agent Plugin config parsing)**; Codex CLI 0.149.1 current (mcp-news.com/news/codex-cli-adds-opt-in-2026-07-28-support; toolsbase.dev/en/reference/codex-commands; blakecrosley.com/guides/codex; codex.danielvaughan.com/2026/04/15/codex-cli-hooks-complete-guide-events-policy-patterns/).
- MCP: Codex supports MCP servers in CLI + IDE extension (`codex mcp add`); Codex CLI itself can run as an MCP server (multi-agent orchestration) (kingy.ai/news/the-complete-guide-to-openai-codex/; codex.danielvaughan.com/2026/05/10/codex-cli-agents-sdk-mcp-server-multi-agent-orchestration/).
- Note: Codex docs also show "Build skills / Build plugins / Site tools (WebMCP) / Hooks" under Extend and automate — plugin + skill surfaces exist for Codex too.

### 2026-09-01 — [web] Gemini CLI / Antigravity hooks (primary source: geminicli.com/docs/hooks/reference; antigravity.google/docs)

- Gemini CLI hooks in `settings.json` under `hooks` object: events **BeforeModel**, BeforeToolSelection, AfterModel (model hooks); BeforeTool/AfterTool (tool hooks, regex matchers incl. `mcp_<server>_<tool>`); BeforeAgent/AfterAgent; SessionStart/SessionEnd/Notification/PreCompress (lifecycle). Command type only (stdin JSON → stdout JSON; exit 2 = block).
- **BeforeModel**: "Fires before sending a request to the LLM. Operates on a stable, SDK-agnostic request format." Input `llm_request` {model, messages, config}; output `hookSpecificOutput.llm_request` overrides parts of the outgoing request (change model/temperature/messages), `hookSpecificOutput.llm_response` = synthetic response (skips LLM call), `decision: deny` blocks the turn. **→ per-turn injection by mutating `llm_request.messages`.**
- Extensions: package prompts, MCP servers, custom commands, themes, hooks, sub-agents, skills; `contextFileName` (GEMINI.md) at session start (geminicli.com/docs/extensions/reference).
- **Antigravity transition**: "Unpaid tier and Google One users: Gemini CLI will be replaced by Antigravity CLI on June 18th" (geminicli.com banner; developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/). Antigravity keeps Agent Skills, Hooks, Subagents, Extensions (extensions → plugins); `hooks.json` in customization dir (e.g., `~/.gemini/config/`); plugins bundle skills/agents/rules/MCP servers/hooks; `/mcp` command (antigravity.google/docs/hooks/, /cli/reference/, /cli/features/).

### 2026-09-01 — [web] Devin / Windsurf (primary source: docs.devin.ai)

- **Devin CLI hooks** (docs.devin.ai/cli/extensibility/hooks): `.devin/hooks.v1.json` (project-level, discovered up to repo root) + user-level; events PreToolUse/PostToolUse/PermissionRequest/**UserPromptSubmit**/Stop/PostCompaction/SessionStart/SessionEnd; **"Existing hooks in `.claude/` directories are also picked up automatically."** Output: `hookSpecificOutput.additionalContext` — "Text injected into the agent's context (for UserPromptSubmit, SessionStart, PostToolUse)". `updatedInput` merge for PreToolUse; decision approve/block; exit 0/2/other.
- **Devin plugins** (docs.devin.ai/cli/extensibility/plugins/overview): closed beta; bundle skills, rules, hooks.json, .mcp.json, custom subagents; hooks run in every session where installed; **cloud-session command hooks best-effort/fail-open** ("don't rely on them for crucial guardrails yet"); support every event except SessionStart/SessionEnd; prompt-type hooks CLI/local-only; plugins provide `.mcp.json` MCP servers.
- Devin MCP (docs.devin.ai/work-with-devin/devin-mcp): org-scoped MCP access, `X-Org-Id` header for org-level resources; MCP Marketplace; MCP OAuth improvements in release notes.
- **Windsurf Cascade hooks** (docs.devin.ai/desktop/cascade/hooks — note: on devin.ai domain, confirming Cognition owns Windsurf): `pre_mcp_tool_use` / `post_mcp_tool_use` (pre-hooks can block with exit 2) — **MCP-governance hooks only, no prompt injection**; Windsurf MCP config `~/.codeium/windsurf/mcp_config.json` (docs.windsurf.com; mcpyet.com/connect/windsurf/).

### 2026-09-01 — [web] OSS VS Code / terminal agents

- **Cline** (docs.cline.bot/customization/hooks; deepwiki.com/cline/cline/7.3-hooks-system; deepwiki.com/cline/sdk): file-based lifecycle hooks — TaskStart/TaskResume/**UserPromptSubmit**/PreToolUse/TaskComplete in global + workspace hook dirs; docs: "**contextModification** can inject text into the conversation as context"; TaskStart can add project context before work; SDK plugin hooks beforeRun/afterRun/**beforeModel**/beforeTool/onEvent; `before_agent_start` common stage for injecting context; MCP support (cline.bot: "custom tools and lifecycle hooks via the SDK", "plug in MCP servers").
- **Roo Code**: MCP support + custom TS/JS tools (roocodeinc.github.io/Roo-Code); per-turn hooks still a discussion (github.com/RooVetGit/Roo-Code/discussions/2415 — "programmatic pre/post hooks" requested); RooCodeInc/Roo-Code **reported archived ~24.3K★** ⚠️.
- **Aider**: repo map auto-context (aider.chat/docs/faq.html); MCP support experimental via PR #3672/#3937 (`~/.aider.conf.yml` stdio servers) (github.com/Aider-AI/aider/issues/2525, issues/3314; blog.aheymans.xyz/post/aider_with_mcp/).
- **Continue**: MCP context provider + MCP tools (docs.continue.dev/customize/deep-dives/mcp, /customize/mcp-tools, /reference); rules files for static context; no documented per-turn injection hook.

### 2026-09-01 — [web] Terminal/enterprise agents

- **Goose (Block)**: MCP-first — extensions ARE MCP servers (block.github.io/goose/docs/guides/custom-extensions/); recipes/skills package prompts+extensions+params (academy.kspl.tech/blog/ai-tool-deep-dive-goose; block-goose.mintlify.app). No per-turn injection hook found.
- **OpenHands**: MCP support ("simplified JSON MCP configuration", "mcp oauth support" in releases; openhands.dev "Use MCP to connect internal tools, APIs, and data sources"); microagents for static knowledge; cloud + OSS.
- **Warp**: native MCP via Settings → Agents → MCP servers (docs.warp.dev/agents/capabilities/mcp/; docs.orq.ai/docs/ai-studio/integrations/code-assistants/warp; warp.dev/blog/launch-log-2 — URL-in-request context inclusion); no per-turn hook found.
- **Amp (Sourcegraph)**: connects to MCP servers — `amp mcp add sg https://sourcegraph.example.com/.api/mcp` (sourcegraph.com/mcp; /changelog/mcp-ga; /changelog/mcp-dcr); no per-turn injection hook found.
- **Droid (Factory)**: terminal agent, MCP support, BYOK (harnesses.sh; terminaltrove.com/compare/ai-coding-agents/; factory.com/news/terminal-bench — #1 on Terminal-Bench); proprietary.
- **Zed**: MCP client via Settings → AI → MCP Servers; extensions provide `context_servers` (zed.dev/docs/ai/mcp, /docs/extensions/mcp-extensions, /blog/mcp — slash commands pull external context); no per-turn pre-prompt injection hook.
- **Void**: VS Code fork; MCP support added v1.4.1 (voideditor.com/changelog; deepwiki.com/voideditor/void/3.4-tools-service-and-terminal-integration; github issues #701/#705 — rough edges).
- **Melty**: open-source chat-first editor (github.com/meltylabs/melty); "MCP-compatible context bridging" claimed by one third-party review (cursor-alternatives.com/ai-ides/melty-cursor-alternative/) ⚠️ low confidence; VS Code Marketplace presence.
- **Koding**: could not confirm an agent/harness by this name with MCP support ⚠️ (closest hit: Kode CLI ~5.2K★, github.com/bradagi/awesome-cli-coding-agents).

### 2026-09-01 — [web] Frameworks

- **Google ADK** (adk.dev/callbacks/): **`before_model_callback`** — "runs before each LLM call and can inspect, modify, or short-circuit the request by returning a response"; receives CallbackContext + mutable LlmRequest (model/messages); Python param names must match exactly (`callback_context`); session service (InMemorySessionService etc.) for per-session state (github.com/google/adk-docs/blob/main/examples/python/snippets/callbacks/before_model_callback.py). ADK supports MCP toolkits.
- **LangGraph/LangChain** (langchain-ai.github.io/langgraph/reference/mcp/; docs.langchain.com/oss/python/langchain/mcp): MCP adapter (langchain-mcp-adapters); **tool interceptors** bridge runtime context into MCP tool calls (`MCPToolCallRequest.override(...)`, `request.runtime` from `ainvoke(context=...)`) — pull-time enrichment; per-turn push = graph-native pre-model node appending to messages state (standard pattern); MCP tools loadable into nodes/subgraphs (leanware.co/insights/langgraph-mcp-building-powerful-agents-with-mcp-integration; generect.com/blog/langgraph-mcp/).
- **Semantic Kernel** (learn.microsoft.com/en-us/semantic-kernel/concepts/enterprise-readiness/filters): filter types incl. **Prompt Render Filter** (access/modify the rendered prompt before it reaches the model) + Function Invocation Filter; first-class MCP support as host AND server (devblogs.microsoft.com/agent-framework/semantic-kernel-adds-model-context-protocol-mcp-support-for-python/; /building-a-model-context-protocol-server-with-semantic-kernel/; learn.microsoft.com/en-us/semantic-kernel/concepts/plugins/adding-mcp-plugins).
- **CrewAI** (docs.crewai.com MCP pages; deepwiki.com/crewAIInc/crewAI/6.3-model-context-protocol-(mcp)): `@CrewBase` auto-manages `MCPServerAdapter` lifecycle (lazy init, singleton, cleanup on kickoff); Streamable HTTP transport recommended; lifecycle hooks are crew/task-level (kickoff start/end, task start/end) — weaker than per-LLM-call seams.

### 2026-09-01 — [web] GitHub Copilot

- MCP: Copilot CLI (local + remote MCP), Copilot app (repo/CLI-configured servers), GitHub.com (repo-level MCP for cloud agent + code review), VS Code GA since 2025-07-14; **coding agent supports MCP tools only — no resources or prompts** (docs.github.com/en/copilot/concepts/context/mcp; /how-tos/copilot-sdk/features/mcp; /concepts/agents/coding-agent/mcp-and-coding-agent; github.blog/changelog/2025-07-14...).
- Push: `.github/copilot-instructions.md` + rules = session/static context; **no UserPromptSubmit-style per-turn injection hook found** in docs (2026-09-01).
- Traction: ~20M users / 4.7M paid (Jan 2026) / 77K enterprise customers; usage-based billing from 2026-06-01.

### 2026-09-01 — [synthesis] Reconciliation for the epic's platform list

- The epic's decided list (Claude Code primary, Pi, OpenClaw, Hermes, Cursor pull-only, Codex parked, custom-app SDK/HTTP) holds for 4 of 7; **two rows flip**: Codex (parked → Tier 1, real `UserPromptSubmit` seam + MCP since 0.147.0) and Cursor (pull-only → session-start push possible, per-turn still pull-only; Claude-hook compat is observation-only for `UserPromptSubmit`). Both flips are primary-source-documented above.
- The "one Claude-hooks-shaped reflex script" argument is the load-bearing distribution insight: Codex (identical `hookSpecificOutput.additionalContext`), Devin CLI (same shape + auto-loads `.claude/`), Cline (`contextModification`), Cursor (`additional_context` naming — emit both), Gemini/Antigravity (`BeforeModel` message mutation) all accept the same reflex logic with per-harness registration and small output-format tweaks. gbrain's claude-code seam is the reference implementation (delivery-shape.md seams table).
- Tier 3's job is verification, not build: the W3 harness (epic W3, Cat-34 port) already grades MCP seams — extend the roster with the pull-only list so "works via MCP pull" stays true per harness across releases (Copilot tools-only MCP, Aider experimental MCP, Cursor/Windsurf/Continue stdio-vs-HTTP quirks).
- Enterprise-managed hooks (Codex `requirements.toml` managed hooks, Cursor Enterprise/Team hooks, Devin org plugins) mean the reflex can be *enforced* org-wide on the corporate channels — a distribution path worth one line in the eventual pitch (admin installs the hook policy, every engineer's agent gets memory).
