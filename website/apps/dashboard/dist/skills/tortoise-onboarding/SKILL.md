---
name: tortoise-onboarding
description: "Install and connect Tortoise for your agent — self-adjudicate your harness (Claude Code / Cursor / Codex / Pi = self-install; Claude Desktop / Claude Web = teach-human), write the MCP config, verify with tortoise_health, checkpoint harness-connected, and reach the decide protocol without any local skill file. Successor to AGENT_ONBOARDING.md (archived)."
domain: capability
type: Workflow
status: live
tags: [tortoise, onboarding, mcp, harness, install, connect, onboarding-state, decide]
summary: "The ONE live Tortoise onboarding script — reads onboarding state, self-adjudicates the harness, installs/connects, verifies via tortoise_health, checkpoints harness-connected, and runs the generic MCP-tool decide protocol."
created: 2026-09-02
updated: 2026-09-02
allowed-tools: read write bash
---

> ⛔ **This is the single live onboarding script.** `AGENT_ONBOARDING.md` and its deployed copies are ARCHIVED under `tortoise/onboarding/archive/` (M8, epic #1976) — never create a second live onboarding script. Edit THIS file; the deployed mirror (`website/apps/dashboard/public/skills/tortoise-onboarding/SKILL.md`) is byte-identical by test.

# Tortoise Onboarding — install and connect your agent

Successor to the archived `AGENT_ONBOARDING.md` question flow. Instead of a
paste-the-prompt Q&A, onboarding is now: **read state → pick your harness →
connect → verify → checkpoint → (later) seed + decide**. The dashboard wizard
hands you ONE universal command; this skill is what your agent follows after
you run or paste it.

## When to use

- The user pastes the dashboard's universal setup command into you (any of
  the 6 harnesses) or runs it in a terminal.
- The Setup guide card / Overview says the organization is waiting on
  "Connect your agent".
- You are a fresh agent pointed at a Tortoise organization and need to know
  how to reach its graph.

## 1. Read OnboardingState first (resume, never restart)

Before installing anything, read the organization's onboarding state so you
resume where the flow left off — onboarding is stateful and idempotent:

- **Hosted, MCP-connected agents:** call `tortoise_onboarding_state` (the MCP
  read tool) when it is listed. It returns the FLOW projection: `fork`
  (`'self' | 'build' | null`), `status` (`'active' | 'complete'`), `compact`,
  `completed_steps[]` (canonical ids: `team-named`, `harness-connected`,
  `first-points-filed`, `decide-completed`, `capture-disclosed`,
  `catalog-presented`).
- **Hosted, CLI agents (no MCP tool listed yet):** `curl -s
  https://api.premiselabs.co/v1/onboarding/state -H "Authorization: Bearer
  $TORTOISE_API_KEY"` (same projection). If the org is grandfathered (node
  absent) the FLOW keys serve defaults — treat them as read-only.
- **Self-hosted:** there is no hosted onboarding REST surface — skip the
  state read and checkpoint steps below; complete the install + verify, and
  let W12's self-hosted init own the node (`fork='self'` default, no fork
  card).

Branch on what you find:

| State | Action |
|---|---|
| `completed_steps` already contains `harness-connected` | Tell the user their agent is already connected; stop (idempotent). Post-completion re-entry is a no-op — the onboarding tools retire from tools/list once the org completes. |
| `fork` is `null` (never chosen) | **Do NOT guess or persist a fork** — the fork card is a human decision, once per organization (presentation fork, never a billing gate). Tell the user the fork card is waiting in the dashboard wizard and re-read the state after they choose. |
| `fork` is `'build'` | Connect as usual; the build fork's completion gate is catalog-based (catalog-presented), not decide-based — no decide nudge required later. |
| `fork` is `'self'` | Connect as usual; the decide nudge (section 4) applies later. |
| First connect on a fresh org | Proceed to section 2. |

## 2. Harness self-adjudication (which agent are you?)

Identify which of the six supported harnesses you are. There is **no
harness-chooser UI** — you adjudicate from the table, then follow YOUR row.

| # | Harness | Class | You can… |
|---|---|---|---|
| 1 | **Claude Code** | self-install (config-write) | run shell commands; write project files |
| 2 | **Cursor** | self-install (config-write) | write project files |
| 3 | **Codex** | self-install (config-write) | run shell commands; write project files |
| 4 | **Pi** | self-install (config-write) | write project files |
| 5 | **Claude Desktop** | teach-human | **no local filesystem** — guide the human |
| 6 | **Claude Web** | teach-human | **no local filesystem, no shell** — guide the human |

If you are unsure which row applies (e.g. a wrapper/terminal agent), assume
the config-writing class — you can verify after writing (section 3, failure
mode → teach-human fallback).

## 3. Connect — one command per harness

The universal setup command is ONE copy block that works for any of the six
harnesses: for the four config-writing harnesses it is a shell/config-write
recipe your agent executes; for Claude Desktop/Claude Web it is the manual
teach-human path. Follow your harness row. Keep the API key OUT of any file
that could be committed (project-scoped configs reference `$TORTOISE_API_KEY`
or `${TORTOISE_API_KEY}`); Desktop/Web configs stay literal-with-privacy-note
(private user-machine / cloud-held — no commit surface).

### Claude Code (self-install)

```bash
claude mcp add --transport http tortoise https://api.premiselabs.co/mcp/ \
  --header "Authorization: Bearer ${TORTOISE_API_KEY}"
```

`$TORTOISE_API_KEY` must be exported in your shell profile first
(`export TORTOISE_API_KEY=<key>` in `~/.zshrc` / `~/.bashrc`). Validate the
config was written (`claude mcp list` shows `tortoise`).

### Cursor (self-install)

Create/merge `.cursor/mcp.json` in the project:

```json
{ "mcpServers": { "tortoise": { "type": "http", "url": "https://api.premiselabs.co/mcp/", "headers": { "Authorization": "Bearer ${env:TORTOISE_API_KEY}" } } } }
```

Set `TORTOISE_API_KEY` in your environment (Cursor settings or shell
profile). Restart Cursor so it picks up the config.

### Codex (self-install)

```bash
export TORTOISE_API_KEY=<key>
codex mcp add tortoise --url https://api.premiselabs.co/mcp/ --bearer-token-env-var TORTOISE_API_KEY
```

Persist the export in your shell profile. Verify with `codex mcp list`.

### Pi (self-install)

Create/merge `.mcp.json` in the project (MERGE — never replace an existing
`mcpServers` block):

```json
{ "mcpServers": { "tortoise": { "type": "http", "url": "https://api.premiselabs.co/mcp/", "headers": { "Authorization": "Bearer ${TORTOISE_API_KEY}" } } } }
```

Pi's mcp-client expands plain `${TORTOISE_API_KEY}` (no `env:` prefix).

### Claude Desktop (teach-human)

You cannot edit local files. Walk the human through:

1. Open `~/Library/Application Support/Claude/claude_desktop_config.json`
   (macOS) — or Claude > Settings > Developer in the app.
2. MERGE the `mcpServers` block below into the existing config (never replace
   the whole file — the key stays literal here; keep the file private):

```json
{ "mcpServers": { "tortoise": { "type": "http", "url": "https://api.premiselabs.co/mcp/", "headers": { "Authorization": "Bearer <TORTOISE_API_KEY>" } } } }
```

3. Restart Claude Desktop. The `tortoise` MCP tools appear in this session.

### Claude Web (teach-human)

Guide the human through:

1. claude.ai > Settings > Connectors > Add custom connector, name it
   "Tortoise".
2. Server URL: `https://api.premiselabs.co/mcp/`; Request headers
   (advanced): `Authorization: Bearer <TORTOISE_API_KEY>` (stored by
   Anthropic — your key, their cloud).
3. The connector exposes the `tortoise_*` MCP tools to claude.ai workflows.

## 4. Verify, then checkpoint harness-connected

1. Call `tortoise_health` (MCP tool, all 6 harnesses once connected). It
   must report the graph reachable + your organization context.
2. On failure: retry once; then give an honest diagnostic — config write
   invalid (harness broken)? Offer the teach-human fallback (the config is a
   manual file for Desktop, or the connector steps for Web) or re-run the
   universal command. Never claim connected on a failed `tortoise_health`.
3. On success — **write the harness-connected checkpoint** (idempotent
   first-write-wins keyed-MERGE; replay is a no-op, so the dashboard's
   Continue button and this write can both fire safely):
   - Hosted CLI agents: `curl -s -X POST
     https://api.premiselabs.co/v1/onboarding/state/checkpoint -H
     "Authorization: Bearer $TORTOISE_API_KEY" -H "Content-Type:
     application/json" -d '{"step":"harness-connected"}'`
   - Claude Desktop / Claude Web: you have no REST/curl surface — the human
     clicks **"I've set it up — Continue"** in the dashboard connect step;
     that click writes the same checkpoint (session-authed). Tell them to do
     that once `tortoise_health` succeeds here.
4. Report to the user: "✅ Tortoise is connected and verified." The Setup
   guide card on the dashboard advances.

**Failure modes:** config write invalid → teach-human fallback (above);
connection verify fails → retry with diagnostic + honest error (never a
silent skip); state read unavailable (self-hosted) → skip checkpoint (W12
owns the self-hosted node).

## 5. Next steps (do NOT do them in this session unless asked)

- **Seed** (files your Organization + User as Subjects, linked `memberOf`) —
  owned by the W3 seed skill; the Setup guide card asks when it is that
  step's turn.
- **Decide** — when the user makes a real decision, run the generic
  MCP-tool decide protocol below. It requires NO local skill file (works on
  all 6 harnesses); the local `tortoise-decide` skill is a convenience, not
  a prerequisite.
- **Capture disclosure** — see the copy contract in section 6 (fired at the
  user's first capture, not during install).

### The generic MCP-tool decide protocol (options → criteria → findings → EP ranking)

Use ONLY the standard Tortoise MCP tools (all always-listable during
onboarding; none require a local skill file). Verify the live connection
first with `tortoise_health` — a failed health check means the decide
cannot reach the graph (never decide against a dead connection):

1. **Refine the decision with the user.** Write it as a short domain label
   (e.g. `2026-Q3-db-migration`). Get the options right first — the user owns
   the option set.
2. **File the decision parts as points** with `tortoise_create_point`:
   - one `decision` point per option (`kind="decision"`, content = the
     option),
   - one `criterion` point per decision criterion,
   - `evidence` points for findings that bear on the choice.
   Use stable ids (returned ids or your own) — the graph is edge-based and
   the ranking reads the wiring.
3. **Confirm with the user before wiring:** list the criteria (for value)
   and the options (for completeness).
4. **Wire criteria → options** with `tortoise_create_operator`:
   - `criterion -[IMPL]-> option` — the criterion argues FOR the option,
   - `criterion -[NAND]-> option` — the criterion argues AGAINST the option.
   A ranking needs ≥1 IMPL edge before `tortoise_compute_confidence` can
   produce signal.
5. **Mitigate, don't NAND, for fit.** When a finding is true but matters
   less, express it on the OPERATOR with `tortoise_mitigate_operator`
   (strength 0.10–0.50), never NAND the option for a bad fit. Annotate bias /
   precision with `tortoise_annotate_operator` when useful.
6. **Options can IMPL/NAND each other** — two go well together (IMPL), three
   are mutually exclusive (NAND).
7. **Rank + sanity-check.** Run `tortoise_compute_confidence` (anchors = the
   decision/options) → present the ranked options with EP confidence AND the
   *why*: the top edges that moved each option. Run `tortoise_check_structure`
   to confirm no orphaned operators.

## 6. Capture-announcement COPY CONTRACT (W6 implements the trigger)

Owned here (epic #1976 §3 + §8 timing pin). At the user's FIRST capture —
the first time a session/conversation is filed to the graph — the agent says
ONE line, non-blocking:

> "Heads up: I'll remember this session so you can recall it later. View/delete in Settings → Memory sources."

Contract notes:
- **Timing:** first capture only, in-conversation, one line, non-blocking.
  Recording is default-ON (ToS-covered); this is disclosure, NOT a consent
  ceremony (no re-gate — the off-switch stays quiet-409, #1927).
- **Checkpoint:** the announcement's completion writes the `capture-disclosed`
  NODE CHECKPOINT (`{"step":"capture-disclosed"}` via the checkpoint
  surface) — it is never a card-counted step (the Setup guide renders it
  uncounted).
- **Ownership:** W2 owns this copy; W6 owns the trigger placement +
  Settings view/delete (hook-driven auto-capture has no in-conversation turn
  at capture time — W6's trigger covers it). Do not drift the wording.

## Pointers

- **Graph-write hygiene** (before ANY create/operator/mitigation write):
  the `how-to-use-tortoise` skill — edge semantics, supersession, provenance.
- **Research-finding ingestion:** the `tortoise-file-finding` skill
  (ingest → check related claims → surface connections).
- **Self-hosted:** see `docs/quickstart-selfhosted.md`; the self-hosted
  onboarding slice (W12) inits the OnboardingState node at SDK/API init with
  `fork='self'` and never surfaces the fork card.

---
> **Archived:** `AGENT_ONBOARDING.md` + variant headers live under
> `tortoise/onboarding/archive/` (A0 rollback path — do not delete; never
> re-promote while this skill is live).
