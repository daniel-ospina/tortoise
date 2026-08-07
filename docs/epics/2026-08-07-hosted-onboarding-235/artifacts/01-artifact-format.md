# One-Artifact Trigger Design — Format Decision

**Issue:** [#495](../../../../issues/495) — tortoise#495
**Epic:** [#235](../../../../issues/235) — Hosted Onboarding Journey
**Date:** 2026-08-08
**Status:** draft (awaiting #496 question-set finalization)
**Inputs:** 01-research-brief.md, 03-plan.md (Tasks 1, 4), CLAUDE.tortoise.md, premise-labs/welcome.html

---

## Decision: Two-block artifact presented as one (MCP config snippet + onboarding prompt)

**Chosen format:** A single "copy setup" action on the welcome page that delivers TWO blocks:
1. **Block A — MCP config snippet:** Streamable HTTP transport config for the user's harness.
2. **Block B — Onboarding prompt:** Canonical markdown prompt (`AGENT_ONBOARDING.md`) that the user pastes into their agent chat.

The welcome page presents them as a unified "Get started" section with numbered steps (1. Copy config → 2. Copy prompt → 3. Paste both). The user sees ONE flow, but the two surfaces (CLI/settings + chat) are acknowledged.

### Why not a single literal artifact?

The plan's research brief identified the key constraint (Section 2, UX Pattern Research):

| Harness | Config surface | Prompt surface | Can a single block cover both? |
|---------|---------------|----------------|-------------------------------|
| Claude Code | CLI (`claude mcp add`) | Chat | No — CLI is a terminal command, chat is in-app |
| Codex | CLI (`codex mcp add`) | Chat | No — same CLI/chat split |
| Cursor | `.cursor/mcp.json` file | `.cursor/rules/` or chat | Closer — both are files, but different paths |
| Pi | `.pi/mcp.json` or extension | Chat | Extension can bundle config + instructions |

**Conclusion:** No single paste operation spans both the MCP config surface AND the agent chat surface. A "one-artifact" that pretends otherwise would confuse users when the config doesn't work as a prompt or vice versa. Instead, we deliver TWO tightly coupled artifacts as ONE UX flow.

### Alternatives considered

| Option | Description | Verdict |
|--------|-------------|---------|
| **A: MCP config only** | User pastes config, discovers tools, but no guided flow | ❌ No guided onboarding — user must figure out tool names |
| **B: Onboarding prompt only** | User pastes prompt, but agent has no MCP tools → hallucinates | ❌ Agent invents tool names, onboarding fails |
| **C: Combined literal artifact** | One paste-able block with both config + prompt | ❌ Config surface ≠ prompt surface in most harnesses |
| **D: Two-block artifact (CHOSEN)** | Config + prompt as separate copy targets, presented as one flow | ✅ Works across all harnesses, clear instructions |

---

## Block A: MCP Config Snippet (per harness)

All configs use **Streamable HTTP transport** (`url` + `headers`), not stdio. Hosted users do not install the Python package; the agent talks to `api.premiselabs.co` over HTTP.

### Claude Code

```bash
claude mcp add --transport http tortoise https://api.premiselabs.co/mcp --header "Authorization: Bearer tt_YOUR_KEY"
```

### Codex

```bash
codex mcp add tortoise https://api.premiselabs.co/mcp --bearer-token-env-var TORTOISE_API_KEY
```
Then set the env var:
```bash
export TORTOISE_API_KEY="tt_YOUR_KEY"
```

### Cursor

Add to `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "tortoise": {
      "transport": "http",
      "url": "https://api.premiselabs.co/mcp",
      "headers": {
        "Authorization": "Bearer tt_YOUR_KEY"
      }
    }
  }
}
```

### Pi

Add to `.pi/mcp.json`:
```json
{
  "mcpServers": {
    "tortoise": {
      "transport": "http",
      "url": "https://api.premiselabs.co/mcp",
      "headers": {
        "Authorization": "Bearer tt_YOUR_KEY"
      }
    }
  }
}
```

> **Note:** `docs/mcp-clients.md` (planned for #236) does not exist yet. When created, it should document these four configs as the canonical MCP client reference. This section is authoritative until that file exists.

---

## Block B: Onboarding Prompt

The canonical prompt lives at `tortoise/onboarding/AGENT_ONBOARDING.md` (this repo). It is:

- **Self-contained:** When pasted into any agent with Tortoise MCP tools available, it drives the yes/no flow without additional instruction.
- **Harness-agnostic:** Same markdown works for Claude Code, Codex, Cursor, and Pi.
- **Single source of truth:** #502 deploys this file to a stable URL; #496 finalizes the question wording by editing this file. No divergent copies.

### Prompt structure

1. **Connection confirmation:** Agent calls `tortoise_health` to verify the MCP connection works.
2. **Question routing:** ≤6 yes/no questions asked ONE AT A TIME (dependencies: Q2 only appears if Q1=yes).
3. **Execution paths:** Each "yes" → specific tool call; each "no" → skip to next.
4. **Error handling:** Tool failures are reported, flow continues.
5. **Verification:** Final step calls `tortoise_health` + `tortoise_context` and displays memory digest.

Full content: see `tortoise/onboarding/AGENT_ONBOARDING.md`.

---

## Trigger Mechanism

### Primary trigger: paste-to-agent

The onboarding flow starts when the user pastes the onboarding prompt into their agent after configuring MCP. This is the **only trigger in v1**.

```
User signup → welcome page shows key + artifact blocks →
User copies MCP config → pastes into terminal/settings →
User copies onboarding prompt → pastes into agent chat →
Agent reads prompt → calls tortoise_health → asks Q1
```

### Why not auto-detect on MCP install?

The research brief identified this as "Best UX if it worked" but noted no standard MCP "on-connect" hook exists. Options evaluated:

| Trigger | How it works | Verdict |
|---------|-------------|---------|
| MCP install auto-detect | Agent detects new tools, offers onboarding | ❌ No standard hook — Claude Code doesn't notify on tool list change |
| Welcome page redirect | After key provisioning, redirect to agent | ❌ No inter-app communication (browser → terminal) |
| **Paste-to-agent (CHOSEN)** | User copies prompt, pastes into agent | ✅ Simple, works across all harnesses, same flow everywhere |

### Future triggers (not in scope)

- **Pi extension auto-start:** A Pi extension could detect Tortoise MCP tools on session start and auto-inject the onboarding prompt. This would remove the manual paste step for Pi users only.
- **Codex/Cursor rule auto-load:** Cursor `.cursor/rules/tortoise-onboarding.md` could be documented as an alternative — if the user creates that file, Cursor auto-loads it. Same content, different delivery.

---

## Welcome Page Presentation (design reference for #502)

The welcome page should present the artifact as:

```
┌─────────────────────────────────────────┐
│ 🚀 Set up your agent                     │
│                                          │
│ [Claude Code] [Codex] [Cursor] [Pi]     │ ← Harness tabs
│                                          │
│ ┌─ Step 1: Add Tortoise to your agent ─┐│
│ │ [harness-specific MCP config]  [Copy]││
│ └──────────────────────────────────────┘│
│                                          │
│ ┌─ Step 2: Start onboarding ───────────┐│
│ │ [AGENT_ONBOARDING.md content]  [Copy]││
│ └──────────────────────────────────────┘│
│                                          │
│ Step 3: Paste both into your agent       │
│ Step 4: Answer ≤6 questions (under 5min) │
└─────────────────────────────────────────┘
```

Each harness tab shows the correct config format. The "Copy" button for Step 2 fetches the canonical prompt from `tortoise/onboarding/AGENT_ONBOARDING.md` (or its deployed URL).

---

## Per-Harness Paste Instructions

### Claude Code
1. Open terminal, run the CLI command shown in the config tab.
2. Open Claude Code chat, paste the onboarding prompt.
3. Agent confirms connection and asks Q1.

### Codex
1. Open terminal, run the CLI command + export the env var.
2. Open Codex chat, paste the onboarding prompt.
3. Agent confirms connection and asks Q1.

### Cursor
1. Add the JSON snippet to `.cursor/mcp.json` (create if needed).
2. (Optional) Add the onboarding prompt to `.cursor/rules/tortoise-onboarding.md` for auto-load.
3. Alternatively, paste the prompt directly in Cursor chat.
4. Agent confirms connection and asks Q1.

### Pi
1. Add the JSON snippet to `.pi/mcp.json`.
2. Paste the onboarding prompt in the Pi session.
3. Agent confirms connection and asks Q1.

---

## Validation

| Criterion | Status |
|-----------|--------|
| One copy-paste artifact (UX) | ✅ Two blocks, one flow |
| Works for Claude Code/Codex/Cursor/Pi | ✅ Per-harness configs + same prompt |
| Triggers yes/no flow | ✅ Prompt instructs agent to ask Q1 after health check |
| Under 5 minutes | ✅ Designed for ≤6 questions, each one tool call |
| Uses docs/mcp-clients.md configs | ⚠️ File does not exist yet — this doc is the reference |
| Single source of truth for prompt | ✅ `tortoise/onboarding/AGENT_ONBOARDING.md` |

---

## Dependencies

- **#496:** Finalizes the 6 question wordings in `AGENT_ONBOARDING.md`.
- **#502:** Deploys `AGENT_ONBOARDING.md` to a stable URL and updates the welcome page.
- **#236 (or future):** Creates `docs/mcp-clients.md` as the canonical MCP config reference.
