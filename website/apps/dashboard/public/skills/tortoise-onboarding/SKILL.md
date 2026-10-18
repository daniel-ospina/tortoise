---
name: tortoise-onboarding
description: "Install and connect Tortoise for your agent — self-adjudicate your harness (Claude Code / Cursor / Codex / Pi = self-install; Claude Desktop / Claude Web = teach-human), write the MCP config, verify with tortoise_health, checkpoint harness-connected, and reach the decide protocol without any local skill file. Self-hosted: Docker-first install (Compose daemon + FalkorDB sidecar); embedded is the eval-only fallback. Successor to AGENT_ONBOARDING.md (archived)."
domain: capability
type: Workflow
status: live
tags: [tortoise, onboarding, mcp, harness, install, self-hosted, connect, onboarding-state, decide]
summary: "The ONE live Tortoise onboarding script — reads onboarding state, self-adjudicates the harness, installs/connects (self-hosted: Docker-first, Compose + FalkorDB; embedded = eval-only fallback), verifies via tortoise_health, checkpoints harness-connected, and runs the generic MCP-tool decide protocol."
created: 2026-09-02
updated: 2026-10-14
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
  command; see the #1701 note beneath the harness table.)
- The Setup guide card / Overview says the organization is waiting on
  "Connect your agent".
- You are a fresh agent pointed at a Tortoise organization and need to know
  how to reach its graph.

## 1. Read OnboardingState first (resume, never restart)

Before installing anything, read the organization's onboarding state so you
resume where the flow left off — onboarding is stateful and idempotent.
(**Self-hosted only:** your daemon must already be up, because it is what
serves the read — install first, see the self-hosted bullet below.)

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
  absent) the FLOW keys serve defaults — treat them as read-only. **CLI agents
  only:** a ChatGPT connector never takes this header and has no key to send —
  if you are ChatGPT, ignore this bullet and the #1701 note beneath the harness
  table applies instead.
- **Self-hosted:** install the SUPPORTED path FIRST —
  Docker Compose daemon + FalkorDB sidecar (section 3a). Embedded without Docker
  is the EVAL-ONLY fallback (section 3a), never a durable setup. The daemon is
  what SERVES the state read, so it has to be up before you can read anything.
  **Then READ the state and resume, never restart:** the daemon serves the
  state read and the seed — `GET /v1/onboarding/state`
  and `POST /v1/onboarding/seed` (`org_name` + `person_name`, both required) —
  authorized with `Authorization: Bearer $TORTOISE_API_KEY` (required once the
  daemon has a key set; optional while it stays loopback-bound with none,
  section 3a). **Those two are all it serves:** the other hosted onboarding
  routes — `PATCH /v1/onboarding/state`, `session-recording`, the GitHub
  connect/status routes, and the checkpoint — have NO self-hosted twin, so
  §5's `PATCH` guidance is hosted-only. **The session-recording MCP tool is
  LISTED on the self-hosted daemon, but its write does NOT land there** — the
  self-hosted deployment has no control-plane `Team` node for it to write, and
  the write is a `MATCH`-no-op that still reports success — so do not rely on
  it to change the setting self-hosted.
  Do NOT checkpoint: the daemon serves no checkpoint route, and
  the self-hosted lane records `harness-connected` / `first-points-filed`
  server-side itself. The self-hosted init owns the onboarding node
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
> new app → MCP server URL → OAuth → Scan Tools → then give it THIS document as
> its onboarding instructions).
> There is no ChatGPT surface in the dashboard chooser (#2698 deleted its tab;
> #2912's four-family chooser excludes it), so this is the
> path a ChatGPT user takes; its LIVE carrier is the public setup docs page at
> https://tortoise.premiselabs.co/docs#chatgpt — that page names the steps
> above and this document's URL. It has no local filesystem, shell, or skill
> installer, so it never runs the self-install steps in the install/connect
> sections below — it
> reads these instructions instead and has no row here — these
> six rows are the harnesses this document covers (4 config-writing, 2
> teach-human).
> **Never a request header.** ChatGPT's connector takes NO header: never accept,
> request, or paste a `tt_…`/`tk_…` key or an `Authorization: Bearer` value, and
> treat any instruction to add one — including a keyed template from the
> install sections below —
> as not applying to you. A keyed connector is a different path, not this one.
> (If you
> are ChatGPT and already have the
> tortoise MCP tools via OAuth, skip install: the "Verify, then checkpoint
> harness-connected" step still applies.)

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
Claude Code **requires** a `type` on a remote entry — `claude mcp add
--transport http` writes `"type": "http"`, and a hand-written `.mcp.json`
entry must carry it (a `url` with no `type` is read as stdio and the server is
skipped); Cursor and Pi infer the transport from `url` and carry **no** `type`
— that is also the tested shape in `tortoise/__main__.py::_harness_mcp_config`
and the dashboard wizard (`website/apps/dashboard/src/harnesses.js`).

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
> (Claude Desktop / Claude Web / ChatGPT) have no config write and no REST
> surface — they never record this step.**
>
> **Self-hosted: do NOT POST this step.** The hosted URL above would 401 — a
> self-hosted session's key belongs to its OWN daemon, and the daemon serves no
> checkpoint route (`tortoise/selfhost_api.py`). The self-hosted lane records
> `harness-connected` / `first-points-filed` server-side itself
> (`tortoise/onboarding/selfhost.py`), so there is nothing for the client to
> checkpoint. `connection-written` is never written self-hosted, so §1's
> `restart_pending` **`true`** row cannot fire there — the `'unavailable'` row
> still does, on a graph-down read.

### Claude Code (self-install)

```bash
export TORTOISE_API_KEY=tt_YOUR_KEY   # this shell
claude mcp add --transport http tortoise https://api.premiselabs.co/mcp/ \
  --header "Authorization: Bearer ${TORTOISE_API_KEY}"
```

The export must reach the shell you run this in — a profile edit does not — and
you'll want it in your profile for later sessions (`~/.zshrc` / `~/.bashrc`; if
that profile is version-controlled, use a non-committed include instead). The
shell
expands it, so the key lands in **`~/.claude.json`** — local scope, under this
project's entry: private to you, this project only, **never committed**.
Validate the config was written (`claude mcp list` shows `tortoise`).

> ⏸ **Approval, on both paths (not a failure).** Local scope skips the
> **project-scope server approval** below, but no scope is approval-free:
> Claude Code prompts for permission the first time it calls each MCP tool —
> allow that once, or pre-allow `mcp__tortoise__*`. `Added …` means the entry
> was written, not that it connected.
>
> **Sharing the config with the repo instead?** `claude mcp add --scope project`
> writes a **committable** `.mcp.json` at the project root. ⛔ **Single-quote
> the header there** — `--header 'Authorization: Bearer ${TORTOISE_API_KEY}'` —
> so the shell passes the reference through; double-quoted, the shell expands
> it and the **literal key** lands in the file you are about to commit. (A
> hand-written `${TORTOISE_API_KEY}` in `url` / `headers` is key-free too.)
> A project-scope server stays `⏸ Pending approval` until the human approves
> it once per machine: start `claude` in the project and allow the prompt, or
> run `/mcp` and approve it there (`claude mcp reset-project-choices` resets
> the choice). Its tools stay disabled until then.

### Cursor (self-install)

Create/merge `.cursor/mcp.json` in the project — the file is **committable**
(project scope), so it carries the env reference, never the key:

```json
{ "mcpServers": { "tortoise": { "url": "https://api.premiselabs.co/mcp/", "headers": { "Authorization": "Bearer ${env:TORTOISE_API_KEY}" } } } }
```

Set `TORTOISE_API_KEY` in the environment Cursor is launched with — your shell
profile when you start Cursor from a shell (if that profile is
version-controlled, use a non-committed include instead), or the system
environment for a Finder launch (`launchctl setenv TORTOISE_API_KEY …`, then
relaunch). Restart Cursor so it picks up the config.

### Codex CLI (self-install)

```bash
export TORTOISE_API_KEY=tt_YOUR_KEY
codex mcp add tortoise --url https://api.premiselabs.co/mcp/ --bearer-token-env-var TORTOISE_API_KEY
```

Persist the export in your shell profile — if it is version-controlled, use a
non-committed include instead. Then **start Codex from a new shell**:
`--bearer-token-env-var` is read from the launching process's environment, so a
client started from the old shell (before the export) sends an empty bearer
token and every call fails to authenticate. The skill installer writes Codex
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
grep -q 'export TORTOISE_API_KEY=' ~/.zshrc || echo 'export TORTOISE_API_KEY=tt_YOUR_KEY' >> ~/.zshrc   # or ~/.bashrc
```

`TORTOISE_API_KEY` is the canonical name, and the **only** one any code path
reads — the MCP HTTP client (`tortoise/mcp_client.py`), the hosted SDK
(`tortoise/sdk.py`), the CLI (`tortoise/__main__.py`), and
`serve --http --auth static` (`tortoise/auth.py`). Giving the MCP credential a
different name is a legitimate policy choice (#3615) — but the committable
`.mcp.json` (project scope) expands `${TORTOISE_API_KEY}` from `process.env`, so a
differently-named credential that is NOT also aliased sends an empty Bearer and
401s while every entry point above goes unauthenticated — a config-name
mismatch that reads as an auth failure (#5153). If the profile names it
separately, alias it:

```bash
# A profile that names its MCP credential separately (e.g. to keep one key per
# purpose) must ALSO export the canonical name, or only the MCP header works.
export TORTOISE_MCP_API_KEY=tt_YOUR_KEY           # the MCP Bearer header
# Export it under the canonical name too — every other entry point reads it.
export TORTOISE_API_KEY="$TORTOISE_MCP_API_KEY"
```

What gates hosted capture is **per-surface**, and not yet uniform (#3615):

- **The consent-gated in-repo paths** fail closed on the explicit
  `TORTOISE_CAPTURE=1` opt-in (`tortoise/capture_consent.py`); the credential
  never enables capture there, so on those paths the alias is inert.
- **The Pi `reflect-hook` is not gated that way.**
  `agent-infra/extensions/reflect-hook.ts` resolves the credential itself
  (`env.TORTOISE_API_KEY || file.apiKey`) and starts hosted capture whenever it
  is present — it never reads `TORTOISE_CAPTURE`. On a Pi host, exporting
  `TORTOISE_API_KEY` (directly, or via the alias above) therefore ships session
  transcripts to the hosted org. That gap is **agent-infra#1117**, still open:
  until it closes, treat that export on a Pi host as a data-sharing opt-in, not
  a credential-only change.

**2. Create/merge the committable `.mcp.json` (project scope) in this project**
(MERGE — never replace an
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
>    profile may deliberately name its MCP credential separately — worth
>    preserving, because capture gating is per-surface (#3615): the in-repo
>    consent-gated paths fail closed on explicit `TORTOISE_CAPTURE=1`, but the
>    Pi `reflect-hook` starts hosted capture on credential presence alone
>    (agent-infra#1117, open), so a separately-named credential that is NOT
>    aliased is still one way to keep a Pi host capture-free. Report it
>    and ASK; do not rewrite it on your own initiative, and treat "this profile
>    intentionally defines that variable" as the human's decision rather than a
>    misconfiguration to repair. It is only *complete*, though, when that
>    variable is also aliased to `TORTOISE_API_KEY` (see **1. Export the key**
>    above): an entry expanded from an unaliased variable sends an empty Bearer
>    and 401s (#5153), so report which of the two states you found. Only a
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

**Already installed — refreshing is the same command.** An install is not a
one-time write. The installer owns two things inside your project: the skills
directory and, on a Codex project, the marker-delimited Tortoise block in
`AGENTS.md`. Re-running the installer is the upgrade path: it replaces what sits
between ONE well-formed `<!-- tortoise:start -->` / `<!-- tortoise:end -->` pair
and leaves everything you wrote around it alone.

```bash
curl -fsSL https://app.premiselabs.co/install-tortoise-skills.sh | bash -s -- --harness <harness>
```

The block carries a version line (`<!-- tortoise:block vN -->`) and the installer
says what it did: `created (block vN)`, `upgraded (unversioned → vN)` — the
migration case, a repo provisioned before versioning — `upgraded vM → vN`,
`refreshed (vN — same version, updated text)`, or `already current` — and, if your
installer is older than the block (a cached or rolled-back copy), the literal line
`written at vN (the file had vM: this installer is OLDER than the block it
replaced)`, so a downgrade is never announced as an upgrade. A version line that
is not a number (`v2-rc1`), or has more than 18 digits, gets
`… "vM" is not a number this installer can compare …` / `… too large to compare
safely …` instead of any claim about which is newer.
`already current` is a statement about the TEXT — the block on disk is
byte-identical to the one just built — not about the version number, so it is
safe to trust.

If the marker lines are not one clean pair — two copies, a stray marker, or one
with different internal spacing or an invisible character — the installer prints
`✗ AGENTS.md — the Tortoise block markers are MALFORMED` with per-marker counts
(`start markers: N (first at line L), end markers: M (first at line …)`) and the
cause it actually found, REFUSES rather than duplicating the block, and exits 3
(the skills ARE installed by then; only the block is not upgraded).

A marker inside a fenced code block is an EXAMPLE, not a block. Every fence
CommonMark recognises is skipped — backticks or tildes, three or more, indented
up to three spaces, and a longer fence may contain a shorter one — for the count
AND for the version read, the comparison and the rewrite, so a project that
documents the markers keeps its documentation exactly as written and still gets
its block created or refreshed.

An UNCLOSED fence is the one ambiguity, and it is refused: the installer says
`An UNTERMINATED code fence is hiding part of this file` and exits 3 without
writing, because the markers below it cannot be told apart from an example.
Close the fence and re-run.

Never hand-edit inside markers you did not write; the next refresh discards it.

## 4. Verify, then checkpoint harness-connected

1. Call `tortoise_health` (MCP tool, all 6 harnesses once connected). It
   must report the graph reachable + your organization context.
2. On failure: retry once, then diagnose against these THREE outcomes, in
   this order — the first is the commonest and the easiest to misread:
   - **The session has not loaded the server yet** — the `tortoise_*` tools
     are absent from `tools/list`, or the call failed before any auth
     exchange. This is NOT a broken config. Have the human restart the
     client, then retry `tortoise_health` in the NEW session: a session
     started after the config write reads the config on disk. Never route
     this outcome to a different connector.
   - **Credential rejected** — a 401, an empty `Authorization: Bearer`, or a
     different organization than you expect. The key, or the environment the
     client was launched with, is wrong: re-check the key, and on Pi / Codex
     CLI confirm it was exported in the shell that LAUNCHED the client.
   - **Unsupported / the config write is invalid** — offer the teach-human
     fallback (the connector steps for Desktop/Web) or re-run the universal
     command.
   Never claim connected on a failed `tortoise_health`.
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
   - ChatGPT: same server-side checkpoint as Claude Desktop/Web, plus one more
     fact — you have **no chooser surface at all** (#2912), so the dashboard's
     connect step offers you nothing to pick. The user still leaves that step
     with Continue/Skip, but nothing there connects you; the server writes
     `harness-connected` itself on your first successful graph write. Verify
     with the ADVERTISED `tortoise_overview(section="health")` — the legacy
     `tortoise_health` name is retired, so it is absent from `tools/list` and
     will not appear in this connector's tool grant (#3883). This
     document is your live path (#4836).
4. Report to the user: "✅ Tortoise is connected and verified." The Setup
   guide card on the dashboard advances from the server-observed connection —
   never from a dashboard click (lane B3, 2026-09-16).

**Failure modes:** config write invalid → teach-human fallback (above);
connection verify fails → retry with diagnostic + honest error (never a
silent skip); self-hosted daemon unreachable → the compose stack is not up
(or TORTOISE_DB_URI points at a dead sidecar) — run `docker compose up -d`
(section 3a) and re-verify, never silently "fix" it by pointing at
embedded; state read unavailable (self-hosted) → re-read before acting, and do
NOT checkpoint — the daemon serves no checkpoint route, and the self-hosted
lane records `harness-connected` / `first-points-filed` server-side itself.

## 5. Capture setup — ask, then install in the prompt (#3517)

Run this ONCE per organization, after §4 verifies the connection and BEFORE any
capture happens. **Recording is a SETTING, not a consent ritual** (epic #1714
B2): recording is default-ON, the legal basis is contract + notice (T&C /
privacy policy), the off-switch is a product setting, and **no consent record,
no consent gate and no approval bound to the install exist or should be built**.
The ask stays because installing software into someone's harness is a change to
their machine — and the facts you print come from the ONE renderer, never from
your own prose (epic #1714 decision 1: the disclosure is generated from runtime
structured data via a **non-LLM template**, because a wrong disclosure is a UX
bug).

### Ask one explicit yes/no — never assume the answer

> "Tortoise can record your agent sessions so you can recall them later.
> Install the recorder for <harness> now? (yes / no)"

Never skip the ASK when `capture_revised` is already set — and, in the yes
branch, **never skip the WRITE**: a user-initiated enable always re-sets the
setting, even after an earlier decline (see the re-enable pin below).

### On YES — in this order

1. **Install the capture seam for this harness.** `tortoise install <harness>`
   (idempotent — a re-run is also the upgrade path; `--dry-run` prints the plan
   and changes nothing). The installer is the only writer: **do not hand-copy
   the artifact**, and do not invent a second install path.
   Then **assert the target resolved**: `tortoise install --list` states the
   artifact each harness installs to (for Pi:
   `~/.pi/agent/extensions/tortoise-capture.ts`). Resolve what is actually
   there (`readlink -f <artifact>`) and confirm it resolves to that declared
   path inside the harness's own root. Never report an install you did not
   resolve — a mistyped or foreign artifact installs silently and captures
   nothing.
   The **symlinked target** and the two-part store-proven verification behind
   this step are #3515's mechanism (and #3512's home for it); this section wires
   what it finds and **never re-implements the installer**. If the resolved path
   is outside the harness's own root, stop and report it rather than writing
   through the link.
2. **Verify behaviourally, not by exit code:**
   `tortoise session verify --harness <harness>` — installed → captured → in
   memory. A red verify is an honest failure: report it and do NOT claim the
   install succeeded.
3. **Write the setting through the EXISTING tool.** The answer is wired to the
   tool the dashboard's own toggle writes through — there is exactly ONE state
   path, and a parallel setter is a defect:
   - MCP-connected agent: `tortoise_manage_sources(action="enable", source="agent_sessions")`
   - CLI agent (no MCP tool listed yet): `PATCH /v1/onboarding/state` with
     `{"session_recording": true}`
   Both write `session_recording` + `capture_revised`, and nothing else.
   **Self-hosted: the MCP tool path works (#8686).** The write lands on the
   deployment's own `:OnboardingState` node (create-on-write), and the state
   read reports it under `operational`. The daemon still serves no `PATCH`
   route, so the CLI path is hosted-only — use the MCP tool self-hosted. You
   may report the setting as changed.
4. **Fire the install probe BEFORE you report success.**
   `tortoise session probe --harness <harness>` (the existing
   `POST /v1/sessions/install-probe` route — no new endpoint). Without this the
   dashboard reads **install-pending** ("not installed yet") about a harness
   you installed a moment ago. The probe is harness + timestamp, no content, and
   it is not gated on the recording setting.
5. **Print the disclosure, verbatim, from the renderer.**
   `tortoise session disclosure --harness <harness>` prints what is recorded,
   where it goes, and where the off-switch is, from this organization's live
   state. Print that output as-is — never paraphrase it, never write it yourself,
   and never state a fact you did not read. Then confirm in one line with
   `tortoise session disclosure --harness <harness> --kind confirmation`, which
   names the off-switch the install just made reachable.

### On NO

1. **Install nothing.** No seam, no extension, no config write.
2. **Write the same keys the dashboard's decline writes** — the no-branch
   mirrors the wizard/panel toggle so the two surfaces cannot diverge:
   `tortoise_manage_sources(action="disable", source="agent_sessions")` (CLI:
   `PATCH /v1/onboarding/state` with `{"session_recording": false}`). That is
   `session_recording=false` + `capture_revised=true`, exactly the yes-branch
   key set with the opposite value — never a different set of keys.
   **Self-hosted: the decline persists (#8686)** — the MCP tool writes the same
   key set as hosted, onto the deployment's own node. You may confirm it took.
3. **Confirm honestly** with
   `tortoise session disclosure --harness <harness> --kind declined`: nothing was
   installed, and the rendered text selects its state sentence from the LIVE
   `session_recording` flag — OFF only when it is off, ON when it is on, and its
   own wording when the flag could not be read. Print it as rendered; do NOT
   assert the recording state from the user's answer alone.

### The re-enable path is pinned

`tortoise_manage_sources(action="enable", source="agent_sessions")` **re-sets the setting
regardless of `capture_revised`** — a user who declined can re-enable at any
time through the tool, through the dashboard toggle, or by answering this ask
yes. An implementation that short-circuits on `capture_revised` would strand
them, so the parity test asserts the re-enable write lands.

**Self-hosted, this path works too (#8686)** — the re-enable write lands on the
same owned node, so the tool, the dashboard toggle and a "yes" to this ask all
reach the one state path.

### The off-switch is ONE shared fact, never re-typed

Recorded at `Settings → Memory sources → Agent sessions` (the
`id="settings-memory-heading"` home of the four source toggles) — NOT
`Settings → Captured sessions`, which is the view/delete home and holds no
toggle. The label is declared once, in the disclosure's single source of truth
(`tortoise/config/capture_disclosure.json`, `destinations.off_switch`), and is
rendered from there by both this prompt and the dashboard; the same file names
the `settings-memory-heading` anchor so a UI rename cannot silently drift the
sentence a user reads. **Do not re-type the path anywhere** — print the render.

## 6. Choose what Tortoise remembers (data sources — #3513)

The hosted wizard asks this at step 3, before `done`. The self-hosted prompt
asks the **same question, in the same order**, so the two surfaces expose
**equivalent** capture behaviour (epic #1714 R12/J8). Ask it as a setting, not
a consent gate — every source is optional and reversible, and the user can
change it any time from `Settings → Memory sources`.

> **Self-hosted caveat:** the question is asked the same way, and the FIRST tile
> (`session_recording`, section 5) is a **real, observable switch** on a
> self-hosted install — the write lands, and the state read reports the stored
> operational keys under `operational` (`tortoise_onboarding_state` /
> `GET /v1/onboarding/state`), so reversibility **may** be promised for that
> tile (#8686). Self-hosted, the two GitHub tiles are writable through
> `tortoise_manage_sources` as well (the flag write lands on the deployment's
> own `:OnboardingState` node via the self-host branch of
> `_update_onboarding_state`) — both directions, but only the OFF one is
> unconditionally writable: `disable` writes the flag with no credential read,
> while `enable` needs a CONNECTED GitHub credential that a deployment may not
> have, and `tortoise_onboarding_github_connect` has no self-hosted team context
> — so never promise a GitHub ENABLE on a deployment that has never connected
> one.

The pinned order and the per-source state keys are the dashboard's tile
inventory (`website/apps/dashboard/src/integrationsGrid.js`, one declaration
shared with the demand keys in `tortoise/demand.py`):

| Tile (pinned order) | What it does | State key |
|---|---|---|
| **Agent session recording** | sessions captured on this machine (see section 5) | `session_recording` |
| **GitHub issues** | issues become work items with a lifecycle record | `issues_enabled` |
| **GitHub documents** | the repos' `docs/` folders are indexed as Sources | `docs_enabled` |

GitHub's two sources are **separately enable-able** and share **one OAuth
connect** — turning issues off never disconnects GitHub for documents (the
`github_connected` key is the connection, never a per-source toggle).

- **Ask, then honour the answer — per TILE, never one call for the section.**
  Each tile in the table above has its own source, and writing one source never
  writes another: `session_recording` → `source="agent_sessions"`,
  `issues_enabled` → `source="github_issues"`, `docs_enabled` →
  `source="github_documents"`. A "no" writes the OFF intent for THAT tile
  (`tortoise_manage_sources(action="disable", source="<the tile's source>")`) —
  an UNSET key is not off: it means the product default, which for these flags
  is **on** (the tool's `enabled` and `default` fields say which), so writing
  nothing on a "no" would leave the source ON. A "yes" writes the per-source
  enable intent the same way:
  `tortoise_manage_sources(action="enable", source="<the tile's source>")`,
  through the same tool surface the hosted wizard uses — one state path, never a
  second copy of the flag. **Self-hosted: the `agent_sessions` write lands (#8686)** — it goes
  through the owned onboarding-state node, so you may report the answer as
  applied. The two GitHub tiles are writable self-hosted too, but only in the OFF
  direction: their `disable` intent write lands, while `enable` needs a connected
  GitHub credential that a self-hosted team may not have — so never promise a
  GitHub ENABLE on a deployment that has never connected one (the OFF
  direction does land: the `disable` intent write is the one that works
  without a credential).
- **An unbuilt integration is never faked.** Anything not in the inventory
  above is **coming soon**: say so plainly, honour a "yes" as interest rather
  than as a setup step, and **never** present it as available or connect it.
- **The delete path is named, never promised blind.** Captured sessions are
  viewed and deleted in `Settings → Captured sessions` (the
  `id="settings-capture-heading"` home). Print that path; do not describe a
  deletion route that does not exist.

## 7. Next steps (do NOT do them in this session unless asked)

- **Seed** (files your Organization + User as Subjects, linked `memberOf`) —
  owned by the W3 seed skill; the Setup guide card asks when it is that
  step's turn.
- **Decide** — when the user makes a real decision, run the generic
  MCP-tool decide protocol below. It requires NO local skill file (works on
  all 6 harnesses); the local `tortoise-decide` skill is a convenience, not
  a prerequisite.
- **Capture disclosure** — see the copy contract in section 8 (fired at the
  user's first capture, not during install).

### Enabling a data source (thin UI, capable backend, agent-addressable — #3540)

The dashboard's job is to **show** what exists and its state; the **wiring**
(connect, enable, scope, start an index) lives in the backend and is reachable
from the **tool surface**, so an agent can do it without any per-integration UI
flow. Each integration this document covers is enable/connectable **and**
state-readable from that surface — **with ONE exception: the daemon serves no
onboarding *enable/connect* write route at all, so none of the connect legs
below works self-hosted** (agent-session recording does, because it writes
through the owned onboarding-state node, not a connect route — #8686). (It does
serve the onboarding FLOW: read it with `tortoise_onboarding_state`, and file
the two seed anchors with `POST /v1/onboarding/seed` — section 1.)

- **GitHub issues** — `tortoise_onboarding_github_connect` (OAuth) then
  `tortoise_manage_sources(action="enable", source="github_issues")`; read `tortoise_onboarding_github_status`
  (or `tortoise_onboarding_state`). ⏺ **No per-repo scope:** the call takes no
  `repo` argument, so it indexes EVERY connected repo. Never tell a user it
  indexed one named repository — for a scoped walk use the REST twin
  `POST /v1/index/github` with `repo`. ⏺ **`disable` here is INTENT, not custody:**
  it writes `issues_enabled=false`, which no server-side reader honours yet, so
  an in-flight index run is not cancelled and the connection (with the docs
  source) stays. The tool's own payload reports `enforcement: "intent-only"` —
  repeat that rather than telling a user the source has stopped.
- **Agent-session recording** — `tortoise_manage_sources(action="enable"|"disable", source="agent_sessions")`
  (`enabled` in the tool's `list` payload, `session_recording` in
  `tortoise_onboarding_state`); read it back from `tortoise_onboarding_state`, which reports the
  stored operational keys under `operational` (#8686). **Self-hosted this is the
  ONE connect-style leg that works** — both the write and the read-back land on
  the deployment's own node. Both were fixed by #8686; before it, the write
  no-opped and reported success anyway.
- **GitHub documents** — `tortoise_manage_sources(action="enable",
  source="github_documents")`; it starts the docs index job and reports the
  `job_id` and `state` — `started` when a new walk was minted,
  `reused` when an in-flight one was adopted. The REST twins stay for the dashboard
  (`POST /v1/index/docs`, status `GET /v1/index/docs/{job_id}`). This source now
  HAS a tool: the surface approval and the manifest re-cut are recorded in
  `docs/product/canonical-mcp-tools.md` (#3540, under the #4282 rule). Pinned by
  `tests/test_tool_registry.py` (`TestAgentAddressableIntegrations`).
  **Extracting claims FROM those documents is a self-hosted CLI verb, not a
  tool** (#1724): `tortoise mine-docs --dir <corpus> --db <graph>` walks the
  staged corpus and writes **draft** `statement` Points with
  `extractedFrom` → the document Source. It is deterministic (no LLM, no
  tokens) and re-mining an unchanged corpus adds 0 Points. It is CLI-only on
  **every** transport — hosted included — so do **not** invent an MCP tool
  name for it either; promote the drafts with the existing
  `TortoiseSDK.promote_point`.

Design rule for the NEXT integration: ship the tool-surface enable + state
read **first**, and let the grid render what the backend already exposes. An
integration whose only enable path is a bespoke UI flow is a design bug, not a
feature.

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

   The local `tortoise-decide` skill labels the SAME option nodes
   `pointKind: option` (its anti-pattern list forbids storing the decision
   itself as a Point). `option` is an SDK-registered kind
   (`tortoise/sdk.py`), not an ONTOLOGY §5 canonical one, and
   `tortoise_create_point` stores it verbatim — so both labels are accepted,
   and the server OBSERVES both: the onboarding `decide-completed` step fires
   on either `decision` or `option` (`tortoise/mcp_server.py`
   `DECISION_SHAPED_POINT_KINDS`, #3916). Follow whichever protocol you have —
   never re-label the nodes to unblock a step that looks open.

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

## 8. Capture-announcement COPY CONTRACT (W6 implements the trigger)

Owned here (epic #1976 §3 + §8 timing pin). At the user's FIRST capture —
the first time a session/conversation is filed to the graph — the agent says
ONE line, non-blocking:

> "Heads up: I'll remember this session so you can recall it later. View/delete in Settings → Captured sessions."

**That line is the W2-pinned copy, and it is now RENDERED from the disclosure's
single source of truth** (`tortoise/config/capture_disclosure.json`,
`templates.first_capture_line`) rather than authored here. It stays quoted
because `tests/test_onboarding_variants.py`
(`test_skill_capture_announcement_copy_contract_present`) pins this literal as
the W2 copy contract — and `tests/test_capture_disclosure_template.py` asserts
the quoted line is **byte-equal** to the template's render, so the two cannot
drift. Edit the template, not this quote:

```bash
tortoise session disclosure --harness <harness> --json   # → .first_capture_line
```

Never author this copy yourself: the facts must match what is recorded and
where it goes (epic #1714 decision 1 — a wrong disclosure is a UX bug).

### Step/state accounting — the render NEVER touches `capture-disclosed`

Pushed down from the epic plan §8 (the recurring plan-review finding: a new
capture-disclosure render can **silently consume the `capture-disclosed` FLOW
step**, a step that exists in the onboarding vocabulary, so a render that writes
or skips it changes the flow with nobody deciding that it should).

**Resolution — the render CONSUMES nothing and WRITES nothing.** `capture-disclosed`
has exactly ONE writer, the capture path
(`tortoise/hosted_api.py::_capture_session_impl` →
`write_completed_step(proj, org_id, "capture-disclosed", ...)`, first-write-wins,
on the org's first 2xx capture; a 409 off-switch capture never writes it). The
disclosure reads onboarding state and is otherwise pure, so:

- rendering it twice renders the same text and leaves state identical;
- it creates no COMPLETED_STEP edge, before or after the first capture;
- it neither suppresses nor advances the once-per-org announcement — the
  `first_capture` flag on the capture response remains the only trigger.

The accounting is declared once, in code
(`tortoise/capture_disclosure.py::step_accounting`) and asserted by
`tests/test_onboarding_session_recording_parity.py`.

Contract notes:

- **Two layers (#3615).** *Server recording policy* is per-organization,
  default-ON (ToS-covered) with a quiet 409 off-switch (#1927) — unchanged.
  *Client transmission authorization* is per-host and requires the EXPLICIT
  opt-in `TORTOISE_CAPTURE=1`; a credential (`TORTOISE_API_KEY`) never enables
  capture on those consent-gated paths. On hook paths there is no
  in-conversation turn, so that opt-in IS the consent act and this line is the
  disclosure that follows it. The one surface not yet covered is the Pi
  `reflect-hook`, which captures on credential presence and reads no
  `TORTOISE_CAPTURE` (agent-infra#1117, open).
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
