---
name: tortoise-onboarding
description: "Install and connect Tortoise for your agent — self-adjudicate your harness (Claude Code / Cursor / Codex / Pi = self-install; Claude Desktop / Claude Web = teach-human), write the MCP config, verify with tortoise_health, checkpoint harness-connected, and reach the decide protocol without any local skill file. Self-hosted: Docker-first install (Compose daemon + FalkorDB sidecar); embedded is the eval-only fallback. Successor to AGENT_ONBOARDING.md (archived)."
domain: capability
type: Workflow
status: live
tags: [tortoise, onboarding, mcp, harness, install, self-hosted, connect, onboarding-state, decide]
summary: "The ONE live Tortoise onboarding script — reads onboarding state, self-adjudicates the harness, installs/connects (self-hosted: Docker-first, Compose + FalkorDB; embedded = eval-only fallback), verifies via tortoise_health, checkpoints harness-connected, and runs the generic MCP-tool decide protocol."
created: 2026-09-02
updated: 2026-09-12
allowed-tools: read write bash
---

> ⛔ **This is the single live onboarding script.** `AGENT_ONBOARDING.md` and its deployed copies are ARCHIVED under `tortoise/onboarding/archive/` (M8, epic #1976) — never create a second live onboarding script. Edit THIS file; the deployed mirror (`website/apps/dashboard/public/skills/tortoise-onboarding/SKILL.md`) is byte-identical by test.
>
> **#4365 — delivered as INSTRUCTIONS, never installed as a skill.** Onboarding is a
> one-time setup FLOW, not a reusable capability, so this document is READ — at the
> served URL (`https://app.premiselabs.co/skills/tortoise-onboarding/SKILL.md`),
> printed by `tortoise init` as `onboarding_prompt_url`, or named by the dashboard's
> connect command — and is never copied into a harness's skills namespace. The skill
> installer ships the three reusable capabilities only (`how-to-use-tortoise`,
> `tortoise-decide`, `tortoise-file-finding`); reach across all six harnesses comes
> from reading this document, not from a local install.

# Tortoise Onboarding — install and connect your agent

Successor to the archived `AGENT_ONBOARDING.md` question flow. Instead of a
paste-the-prompt Q&A, onboarding is now: **read state → pick your harness →
install/connect (self-hosted: Docker Compose first — §3a) → verify →
checkpoint → (later) seed + decide**. The dashboard wizard
hands you ONE universal command; the instructions below are what your agent
follows after you run or paste it — nothing is installed into a skills dir.

## When to use

- The user pastes the dashboard's universal setup command into you (any of
  the 6 harnesses this document covers — 4 config-writing harnesses that run
  the skill installer, plus 2 teach-human leaves) or runs it in a terminal.
  (A 7th harness — ChatGPT — connects key-less via OAuth and never runs this
  command; see the §2 note.)
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
  `completed_steps[]` (canonical ids: `team-named`, `connection-written`,
  `harness-connected`, `first-points-filed`, `decide-completed`,
  `capture-disclosed`, `catalog-presented`), and the DERIVED `restart_pending`
  (`true` iff `connection-written` is present and `harness-connected` is not —
  §3 records the first; §4 records the second). Like the other FLOW keys it
  serves the literal string `'unavailable'` during a graph outage — it is
  `true` ONLY as the boolean `true`. Never truthiness-test it: a non-empty
  sentinel would read as "the config is already written".
- **Hosted, CLI agents (no MCP tool listed yet):** `curl -s
  https://api.premiselabs.co/v1/onboarding/state -H "Authorization: Bearer
  $TORTOISE_API_KEY"` (same projection). If the org is grandfathered (node
  absent) the FLOW keys serve defaults — treat them as read-only.
- **Self-hosted:** there is no hosted onboarding REST surface — skip the
  state read and checkpoint steps. Install the SUPPORTED path first —
  Docker Compose daemon + FalkorDB sidecar (section 3a) — then connect +
  verify. Embedded without Docker is the EVAL-ONLY fallback (section 3a),
  never a durable setup. W12's self-hosted init owns the onboarding node
  (`fork='self'` default, no fork card).

Branch on what you find:

| State | Action |
|---|---|
| `completed_steps` already contains `harness-connected` | Tell the user their agent is already connected; stop (idempotent). Post-completion re-entry is a no-op — the onboarding tools retire from tools/list once the org completes. |
| `restart_pending` is the boolean `true` (`completed_steps` has `connection-written` but not `harness-connected`) | The MCP config is already WRITTEN — the file is on disk; only the restart verification is outstanding. Do **NOT** re-walk §2–§3 (and do not re-litigate the collision protocol against a config that already holds the answer). Confirm the config is intact, tell the user to relaunch from a NEW shell, then proceed to §4. |
| `restart_pending` is `'unavailable'` | The server could not read the graph. This is NEITHER "pending" NOR "not pending" — a graph outage must never be read as "the config is already written". Re-read before acting, and do not tell the user the config is written. |
| `fork` is `null` (never chosen) | **Do NOT guess or persist a fork** — the fork card is a human decision, once per organization (presentation fork, never a billing gate). Tell the user the fork card is waiting in the dashboard wizard and re-read the state after they choose. |
| `fork` is `'build'` | Connect as usual; the build fork completes on the two acts the server OBSERVES — `harness-connected` + `first-points-filed` — never on a catalog render, and not decide-based — so no decide nudge is required later. |
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
| 4 | **Pi** | self-install (config-write) | run shell commands; write project files |
| 5 | **Claude Desktop** | teach-human | **no local filesystem** — guide the human |
| 6 | **Claude Web** | teach-human | **no local filesystem, no shell** — guide the human |

If you are unsure which row applies (e.g. a wrapper/terminal agent), assume
the config-writing class — you can verify after writing (section 3, failure
mode → teach-human fallback).

> **#1701 — ChatGPT is a 7th harness, outside this table.** ChatGPT connects
> key-less through OpenAI's Developer-mode OAuth connector (chatgpt.com →
> Settings → Security and login → Developer mode, then chatgpt.com/plugins →
> new app → MCP server URL → OAuth → Scan Tools → paste the workflows prompt).
> There is no ChatGPT surface in the dashboard chooser (#2698), so this is the
> path a ChatGPT user takes. It has no local filesystem, shell, or skill
> installer, so it never runs these instructions and has no row here — these
> six rows are the harnesses this document covers (4 config-writing, 2
> teach-human). (If you
> are ChatGPT and already have the
> tortoise MCP tools via OAuth, skip install: section 4's tortoise_health
> verify still applies.)

## 3. Install + connect — Docker-first for self-hosted, hosted below

> **Self-hosted? Start at §3a** — the supported install is Docker Compose
> (daemon + FalkorDB sidecar); embedded without Docker is the EVAL-ONLY
> fallback, never a setup you present as durable. **Hosted? Skip to §3b**
> (the dashboard universal command + per-harness rows).

### 3a. Self-hosted install: Docker Compose + FalkorDB first (the supported path)

The supported self-hosted path is **Docker Compose with the real FalkorDB
server**: `docker-compose.yml` ships the daemon + a FalkorDB sidecar
(AOF on, named volume, healthcheck) with `TORTOISE_DB_URI` wired. Run it
from the repo root:

```bash
git clone https://github.com/daniel-ospina/tortoise.git && cd tortoise
docker compose up -d          # daemon on http://localhost:8000 (MCP at /mcp)
```

- **One transport:** the daemon speaks MCP over **HTTP** at
  `http://localhost:8000/mcp` — never stdio for the Docker path.
- **Key setup:** set a strong `TORTOISE_API_KEY` in `docker-compose.yml`
  before exposing the daemon beyond localhost; the connect rows below send
  it as a Bearer header.
- Full walkthrough + variants: `docs/quickstart-selfhosted.md` — Option A
  (compose, recommended), Option B (bare container), Option C (embedded,
  eval only).

**Self-hosted connect delta — the §3b harness rows apply with two
substitutions:**

1. MCP url → `http://localhost:8000/mcp` (the local daemon, not
   `https://api.premiselabs.co/mcp/`);
2. `Authorization: Bearer $TORTOISE_API_KEY` → the daemon's key from
   `docker-compose.yml` (optional while the daemon stays loopback-bound).

**No Docker? Embedded is the EVAL-ONLY fallback — gated, never silent:**
with `TORTOISE_DB_URI` unset and no explicit embedded choice, `tortoise
init` / `tortoise onboard` land on embedded FalkorDBLite
(`~/.tortoise/tortoise.db`, auto-created) and print a loud notice —
embedded is SINGLE-WRITER / EVAL ONLY (concurrent writers lose data), for
one agent evaluating Tortoise, not a team deployment (quickstart Option C).
(An explicitly chosen embedded path — `TORTOISE_DB_PATH` or `--path`,
quickstart Option C — carries the eval-only label on init's success line
instead of the default-fallback notice: the user chose it.) A no-Docker run
must never be reported as a durable setup.

### 3b. Hosted connect — one command per harness

The universal setup command is ONE copy block that works for any of the six
harnesses: for the four config-writing harnesses it is a shell/config-write
recipe your agent executes; for Claude Desktop/Claude Web it is the manual
teach-human path. Follow your harness row. Keep the API key OUT of any file
that could be committed (project-scoped configs reference `$TORTOISE_API_KEY`
or `${TORTOISE_API_KEY}`); Desktop/Web configs stay literal-with-privacy-note
(private user-machine / cloud-held — no commit surface). The rows below are
the HOSTED connect — self-hosted agents apply the §3a delta to the same
rows.

**Transport `type` (canonical):** the hosted endpoint is Streamable HTTP.
Where a client requires a `type`, use `"http"` — the protocol's spec name is
Streamable HTTP, but `"streamable-http"` is only a Claude Code alias:
Cursor's IDE may tolerate it while the Cursor CLI can drop the whole config
file, and Pi ignores `type` entirely. Never teach `"streamable-http"`;
`"http"` is the only universally safe value.
Claude Code **requires** `"type": "http"` in a JSON `.mcp.json` entry (a
`url` with no `type` is read as stdio and the server is skipped); Cursor and
Pi infer the transport from `url` and carry **no** `type` — that is also the
tested shape in `tortoise/__main__.py::_harness_mcp_config` and the
dashboard wizard (`website/apps/dashboard/src/harnesses.js`).

> **After the config WRITE, and before you hand the restart to the user,
> checkpoint `connection-written`.** The write is the one step no server can
> verify — the config file is on the user's disk and the server cannot see it
> — so it is the CLIENT's act to record, with an AGENT credential (a
> session/browser JWT is refused `403 agent_credential_required`):
>
> ```bash
> curl -sS -X POST https://api.premiselabs.co/v1/onboarding/state/checkpoint \
>   -H "Authorization: Bearer $TORTOISE_API_KEY" \
>   -H "Content-Type: application/json" -d '{"step":"connection-written"}'
> ```
>
> Use the key THIS session already holds — the one the config was just written
> with. A profile-only export does not reach a running process, and `-s` would
> hide a 401, so **confirm the response names `connection-written`** in
> `created_steps`/`noop_steps`; if it does not, surface the failure rather than
> treating the write as recorded (an unrecorded write is exactly the
> "indistinguishable" case this step exists to remove).
>
> It is what makes a **restart-pending** install distinguishable from one that
> never happened (§1's `restart_pending` row). It does NOT claim the harness is
> connected: `harness-connected` remains §4's job, and only after
> `tortoise_health` succeeds in the NEW session. **Teach-human harnesses
> (Claude Desktop / Claude Web) have no config write and no REST surface — they
> never record this step.**

### Claude Code (self-install)

```bash
claude mcp add --transport http tortoise https://api.premiselabs.co/mcp/ \
  --header "Authorization: Bearer ${TORTOISE_API_KEY}"
```

`$TORTOISE_API_KEY` must be exported in your shell profile first
(`export TORTOISE_API_KEY=<key>` in `~/.zshrc` / `~/.bashrc`). Validate the
config was written (`claude mcp list` shows `tortoise`).

> ⏸ **One-time approval (not a failure):** servers registered at **project
> scope** (`.mcp.json` — `claude mcp add --scope project`, the default in
> older clients) show as **Pending approval** in `claude mcp list` until the
> human approves once — have them start `claude` in the project and allow
> the prompt (or use `/mcp`). The tools stay disabled until then. (The
> current `claude mcp add` default is *local* scope — active immediately,
> no approval.)

### Cursor (self-install)

Create/merge `.cursor/mcp.json` in the project:

```json
{ "mcpServers": { "tortoise": { "url": "https://api.premiselabs.co/mcp/", "headers": { "Authorization": "Bearer ${env:TORTOISE_API_KEY}" } } } }
```

Set `TORTOISE_API_KEY` in your environment (Cursor settings or shell
profile). Restart Cursor so it picks up the config.

### Codex CLI (self-install)

```bash
export TORTOISE_API_KEY=<key>
codex mcp add tortoise --url https://api.premiselabs.co/mcp/ --bearer-token-env-var TORTOISE_API_KEY
```

Persist the export in your shell profile. The skill installer writes Codex
skills to `.agents/skills` (Codex's documented skill root — NOT `.codex/skills`,
which Codex never loads) and adds a repo-root AGENTS.md standing-instructions
block:

```bash
curl -fsSL https://app.premiselabs.co/install-tortoise-skills.sh | bash -s -- --harness codex
```

Verify with `codex mcp list`, then check `/skills` in a Codex session.

### Codex Desktop (GUI, no terminal)

The Desktop app shares `~/.codex/config.toml` with the CLI and does NOT read
shell exports. Onboarding from the dashboard's connect step selects **Desktop
(no terminal)** and shows the terminal-less path: add an `[mcp_servers.tortoise]`
block to `~/.codex/config.toml` (`bearer_token_env_var = "TORTOISE_API_KEY"`,
with the variable placed into the app's environment via `launchctl setenv` /
`setx`), or the literal `http_headers` fallback for a fully shell-less machine
(private file — never commit). Skills load from `.agents/skills` when the
project folder is open; the Desktop flow defers the installer to a one-time
terminal or the agent running it inside the project. First-time MCP calls may
prompt for approval — `tortoise_health` and the read tools are safe to allow.

### Pi (self-install)

Pi is a config-write harness, but the config is only half the story: the key
comes from the **launching shell's** environment, and the config file is found
by walking **up from the current directory**. Both have silent failure modes.
Follow these four steps in order — each one is load-bearing, the first two are
yours to execute, step 3 is a handoff to the user, and step 4 is the check.

**1. Export the key to your shell profile.** Pi expands `${VAR}` from
`process.env` **at process start**, so a missing export produces an empty
bearer token (`Authorization: Bearer`) and a 401 — not a config error:

```bash
# Idempotent: a bare `>>` stacks a second export on every re-run.
# Shell profiles are often version-controlled — if yours is, keep the key
# out of it and use a non-committed include instead.
grep -q 'export TORTOISE_API_KEY=' ~/.zshrc || echo 'export TORTOISE_API_KEY=<key>' >> ~/.zshrc   # or ~/.bashrc
```

**2. Create/merge `.mcp.json` in the project** (MERGE — never replace an
existing `mcpServers` block; if the EFFECTIVE config already has a `tortoise`
entry — even one that only lives in the home/base config — run the collision
protocol below BEFORE writing):

```json
{ "mcpServers": { "tortoise": { "url": "https://api.premiselabs.co/mcp/", "headers": { "Authorization": "Bearer ${TORTOISE_API_KEY}" } } } }
```

Pi's mcp-client expands plain `${TORTOISE_API_KEY}` (no `env:` prefix).

> **Resolution order — a project `.mcp.json` SHADOWS the home config.** Pi
> walks up from the current directory (to the git top-level) and takes the
> FIRST `.mcp.json` it finds, falling back to `~/.pi/agent/.mcp.json`
> (`resolveMcpJsonPath`, agent-infra #104). Writing only
> `~/.pi/agent/.mcp.json` is therefore a silent no-op inside any repo that
> has its own `.mcp.json` — write the PROJECT file.
>
> ⛔ **Collision protocol — a `tortoise` entry already exists.** `mcpServers`
> is a JSON object, so writing the `tortoise` key over an existing `tortoise`
> key silently REPLACES it. "MERGE" protects the *other* servers, not this
> one. Developers commonly have several `tortoise` entries declared across
> more than one backend, and a local or self-hosted one can hold a POPULATED
> graph — so a blind write is a backend switch with no warning, no data
> check, and no way back. Work
> through these five steps before writing — step 1 can stop you early if the
> entry is already correct:
>
> 1. **Detect** — resolve which `.mcp.json` is EFFECTIVE first (the first one
>    found walking up from cwd, else `~/.pi/agent/.mcp.json` — a project file
>    shadows the home config), then read that file's `tortoise` entry before
>    writing. Never write blind. An entry that looks right in a SHADOWED file
>    does not count: if the effective file has no `tortoise`, the connection
>    is absent. If the effective entry already matches the intended shape
>    (same `url` AND `Authorization: Bearer ${TORTOISE_API_KEY}`), report
>    "already correct — no repoint needed" and STOP: no confirm, no preserve,
>    no rewrite. Same `url` with a DIFFERENT KEY VARIABLE (e.g.
>    `${TORTOISE_MCP_API_KEY}`) is a **policy difference, not a defect**: a
>    profile may deliberately name its MCP credential separately. It is no
>    longer required for that purpose — hosted capture is gated on EXPLICIT
>    consent (`TORTOISE_CAPTURE=1`), never on the credential (#3615). Report it
>    and ASK; do not rewrite it on your own initiative, and treat "this profile
>    intentionally defines that variable" as the human's decision rather than a
>    misconfiguration to repair. Only a
>    genuinely BROKEN entry (wrong `url`, missing or headerless
>    `Authorization`) is yours to correct — and only after the confirm gate.
>    A home/base entry is left untouched and merely shadowed, so nothing is
>    overwritten and there is no preserve step.
> 2. **Report** — tell the human the existing entry's backend (its `url`, or
>    the local stdio `command`) and its graph size when that backend is
>    reachable (local/self-hosted: query the node count). If it is
>    unreachable, report the size as unknown — never guess.
> 3. **Confirm** — get explicit human confirmation before repointing. This is
>    a data-routing change, and it is the ONE human gate inside the otherwise
>    one-block universal command: the copy block stays one block, and YOU ask.
>    Never absorb the switch silently.
> 4. **Preserve** — only when the existing `tortoise` entry lives in the SAME
>    file you are about to write into (an overwrite in place). Rename it to a
>    name that is FREE in that file: the obvious `tortoise-local` may already
>    be taken — check before writing, and walk to the next free name
>    (`tortoise-local-2`, `tortoise-local-<backend>`, …).
>    Never write onto an already-present key. Set `"lazy": true` on the
>    preserved entry so it is not started eagerly at launch. ⛔ **A preserved
>    LOCAL STDIO server must not inherit the hosted key.** Pi passes the parent
>    environment to every stdio child, and a local stdio `tortoise` REFUSES TO
>    START when `TORTOISE_API_KEY` is set and no `TORTOISE_SECRET_PEPPER` is
>    configured — the hosted/cloud key is rejected by the local server on
>    purpose (`tortoise/auth.py`). Because step 1 exports
>    exactly that variable to the profile, a preserved stdio entry without
>    `"TORTOISE_API_KEY": ""` in its `env` block will fail at import, so it is
>    NOT "loadable on demand" — do not promise that. Add the blanking key, or
>    if you cannot edit the preserved entry, tell the user it will need it.
>    Then confirm the preserved entry still loads before reporting the switch
>    complete. When the effective entry lives in a DIFFERENT
>    file (the home/base config, which your project write only SHADOWS), there
>    is nothing to preserve — skip this step; Report + Confirm still apply,
>    and the base entry stays intact and recoverable by deleting the project
>    entry.
> 5. **Write** — only now add the hosted `tortoise` entry above. Prefer the
>    PROJECT `.mcp.json`; write the home/base config only when no project file
>    is in play, since a project file always shadows it and a home write is
>    then a silent no-op (see the resolution-order note).
>
> Preserving the prior entry does **not** certify it: a preserved local stdio
> entry can be broken in more than one way — a wrong port or password, and the
> hosted-key rejection above. agent-infra #639 covers both. Preserve it so the
> switch stays recoverable in one step — not as an endorsement of it.

**3. Restart Pi from a NEW shell** (hand this to the user — the running Pi
process cannot restart itself). Not `/reload`, and not a restart of an
already-open terminal: expansion reads the **launching shell's** env, so a
reload (or a restart that reuses the old process env) silently keeps the
stale or absent value. Tell the user: quit Pi, open a new terminal, and start
Pi again from it.

**4. Verify in that new session** — call `tortoise_health` (§4); it must name
the organization you expect. The pre-restart session cannot verify: its MCP
client was built before the export.

> ⛔ **The env var — not the config file — decides which organization you
> connect to.** A Pi process launched with a stale `TORTOISE_API_KEY` connects
> to the *previous* org and returns data from the wrong graph with no error.
> Observed live 2026-09-12: a stale key quietly reached a different, fully
> populated organization — every tool answered normally, none of it from the
> graph the user thought they were querying. If `tortoise_health` reports
> an org you did not expect, the process env is stale — relaunch from a new
> shell; editing `.mcp.json` will not help.

### Claude Desktop (teach-human)

You cannot edit local files. Walk the human through the Connectors flow:

1. Open Claude Desktop → **Settings → Connectors → Add custom connector**.
2. Name: `Tortoise`
3. Server URL: `https://api.premiselabs.co/mcp/`
4. Request headers: `Authorization: Bearer <TORTOISE_API_KEY>`

Restart is not required — the connector's `tortoise_*` MCP tools appear in a
new chat. The config file at
`~/Library/Application Support/Claude/claude_desktop_config.json` only accepts
**local stdio** servers and silently does nothing for this remote HTTP server;
it can still be edited via **Settings → Developer → Edit Config** for
advanced/local stdio setups only.

### Claude Web (teach-human)

Guide the human through:

1. claude.ai > Settings > Connectors > Add custom connector, name it
   "Tortoise".
2. Server URL: `https://api.premiselabs.co/mcp/`; Request headers:
   `Authorization: Bearer <TORTOISE_API_KEY>` (stored by
   Anthropic — your key, their cloud).
3. The connector exposes the `tortoise_*` MCP tools to claude.ai workflows.

## 4. Verify, then checkpoint harness-connected

1. Call `tortoise_health` (MCP tool, all 6 harnesses once connected). It
   must report the graph reachable + your organization context.
2. On failure: retry once; then give an honest diagnostic — config write
   invalid (harness broken)? Offer the teach-human fallback (the connector
   steps for Desktop/Web) or re-run the
   universal command. Never claim connected on a failed `tortoise_health`.
3. On success — **write the harness-connected checkpoint** (idempotent
   first-write-wins keyed-MERGE; replay is a no-op, so re-running this write
   is safe):
   - Hosted CLI agents: `curl -s -X POST
     https://api.premiselabs.co/v1/onboarding/state/checkpoint -H
     "Authorization: Bearer $TORTOISE_API_KEY" -H "Content-Type:
     application/json" -d '{"step":"harness-connected"}'`
   - Claude Desktop / Claude Web: you have no REST/curl surface, and **no
     dashboard click connects anything** — the server writes this same
     checkpoint itself on your first successful graph write
     (`tortoise_create_point` / `tortoise_file_decision` →
     `_maybe_onboarding_auto_complete()`). File a first memory and the
     dashboard reflects it on its own: the done step (step 3) shows Connected
     the moment your agent files. The connect step does NOT advance by itself
     — the poll runs only on the done step, so the user still clicks
     Continue/Skip to leave the connect step. Never tell the human a click
     connects them.
4. Report to the user: "✅ Tortoise is connected and verified." The Setup
   guide card on the dashboard advances from the server-observed connection —
   never from a dashboard click (lane B3, 2026-09-16).

**Failure modes:** config write invalid → teach-human fallback (above);
connection verify fails → retry with diagnostic + honest error (never a
silent skip); self-hosted daemon unreachable → the compose stack is not up
(or TORTOISE_DB_URI points at a dead sidecar) — run `docker compose up -d`
(section 3a) and re-verify, never silently "fix" it by pointing at
embedded; state read unavailable (self-hosted) → skip checkpoint (W12 owns
the self-hosted node).

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

   **Calibration is automatic (#2199) — no promote/calibrate chores.**
   Decision parts are HUMAN-authored judgment, so they are born LIVE with an
   explicit, provenance-recorded starting belief: omit `credibility` and the
   system applies its standard starting belief (medium = Beta(3,1), mean
   0.75, provenance `system-default` — visible per point in
   `tortoise_calibrate_summary`). Pass `credibility="high"` (ladder: gold /
   high / medium / low / unverified) when you have a real belief — it is
   recorded as `set-by-author`. The documented flow below ranks on the FIRST
   attempt with zero undocumented calls (0 CalibrationError).
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

   **Mitigation semantics (single source: tortoise/weights.py module
   docstring, #2315):** `strength` is the graded DAMPENER of the
   operator's effective EP weight — sanctioned band 0.10–0.50, 0.50 =
   major counter-evidence (strongest); never >0.50 (would invert the
   claim — use NAND). Formula `w_eff = w * (1 - strength)`: a 0.30
   mitigation keeps 70% of the operator's weight; 0.50 keeps 50% —
   dampened, never refuted. It is NOT how true the reason is and is NOT
   fused into the mitigation's belief: the mitigation POINT itself is
   calibrated like any other decision part (omit `credibility` → system
   starting belief medium; pass it → set-by-author). EP reads
   `mitigation_strength` via `compute_operator_weight`.
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

> "Heads up: I'll remember this session so you can recall it later. View/delete in Settings → Captured sessions."

Contract notes:
- **Two layers (#3615).** *Server recording policy* is per-organization,
  default-ON (ToS-covered) with a quiet 409 off-switch (#1927) — unchanged.
  *Client transmission authorization* is per-host and requires the EXPLICIT
  opt-in `TORTOISE_CAPTURE=1`; a credential (`TORTOISE_API_KEY`) never enables
  capture. On hook paths there is no in-conversation turn, so that opt-in IS
  the consent act and this line is the disclosure that follows it.
- **Timing:** first capture only, in-conversation, one line, non-blocking.
  The off-switch stays quiet-409 (no re-gate, #1927).
- **Destination (#2002):** `Settings → Captured sessions` — the capture
  view/delete home (`<h3 id="settings-capture-heading">`). NOT
  `Settings → Memory sources`, which is the SIBLING home holding the four
  source toggles: it lists no sessions and offers no delete. The epic design
  docs (#1976 §W4/W6) describe it as "Memory sources → Agent sessions", but
  #2180 shipped both homes as siblings, so the announcement names the
  view/delete home directly. Do not "restore" the older wording.
- **Checkpoint:** the announcement's completion writes the `capture-disclosed`
  NODE CHECKPOINT (`{"step":"capture-disclosed"}` via the checkpoint
  surface) — it is never a card-counted step (the Setup guide renders it
  uncounted).
- **Ownership:** W2 owns this copy; W6 owns the trigger placement +
  Settings view/delete. Hook-driven auto-capture has no in-conversation turn
  at capture time — it runs only under the explicit client opt-in (#3615),
  and W6's trigger covers the in-conversation surfaces. Do not drift the
  wording.

## Pointers

- **Graph-write hygiene** (before ANY create/operator/mitigation write):
  the `how-to-use-tortoise` skill — edge semantics, supersession, provenance.
- **Research-finding ingestion:** the `tortoise-file-finding` skill
  (ingest → check related claims → surface connections).
- **Self-hosted:** `docs/quickstart-selfhosted.md` — Docker Compose +
  FalkorDB is the supported path (Option A/B); embedded (Option C) is the
  EVAL-ONLY fallback the CLI/onboard wizard gates loudly. The self-hosted
  onboarding slice (W12) inits the OnboardingState node at SDK/API init with
  `fork='self'` and never surfaces the fork card.

---
> **Archived:** `AGENT_ONBOARDING.md` + variant headers live under
> `tortoise/onboarding/archive/` (A0 rollback path — do not delete; never
> re-promote while this document is live).
