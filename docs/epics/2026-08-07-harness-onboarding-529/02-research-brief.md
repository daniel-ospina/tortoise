<!-- research-path: docs/epics/2026-08-07-hosted-onboarding-235/01-research-brief.md -->

# Epic Research Brief — Harness-specific onboarding variants (#529)

**Date:** 2026-08-11
**Status:** draft
**Research depth:** light (epic scope — reuses #235's research brief; the harness matrix exists)
**Domain:** engineering + ux
**Inputs:** Align Decision (PROCEED, `01-align.md`) + #235 research brief + fresh harness-docs verification (this session)

---

## 1. Strategy Context

Inherited from #235 (CLOSED, shipped): the hosted platform is the monetization path; onboarding is the conversion lever; time-to-first-memory is the churn determinant. #529 completes the setup surface #235 shipped by giving each harness its optimal paste path. No new strategy findings — see `../2026-08-07-hosted-onboarding-235/01-research-brief.md` §1.

## 2. What #235 actually shipped (ground-truth inventory, verified @ ea9be54)

| Surface | Location | State |
|---|---|---|
| Welcome page harness tabs | `website/welcome.html` (`switchHarness`, `HARNESS_CONFIGS` JS map, lines ~404–440, ~830–880) | ✅ Live — 4 tabs (Claude/Codex/Cursor/Pi); Block A = per-harness MCP config; Block B = ONE shared prompt for all harnesses |
| Canonical onboarding prompt | `tortoise/onboarding/AGENT_ONBOARDING.md` (Q0–Q6, error recovery, tool-dependency table) | ✅ Single source of truth (#496) |
| Prompt deployment | `.github/workflows/deploy-pages.yml` stages `AGENT_ONBOARDING.md` → `website/onboarding-prompt.md` → Cloudflare Pages | ✅ Live — `https://premiselabs.co/onboarding-prompt.md` returns 200 with canonical content (verified this session) |
| Hosted onboarding endpoints | `tortoise/hosted_api.py`: `/v1/register` (SKIP_AUTH), `/v1/onboarding/state` GET/PATCH, `/v1/onboarding/session-recording`, `/v1/onboarding/team`, `/v1/onboarding/github/{connect,callback,status}` | ✅ Live |
| Onboarding MCP tools | `tortoise/mcp_server.py`: 6× `tortoise_onboarding_*` | ✅ Live, retire per-team after `onboarding_complete` (#888) |
| Self-hosted prompt ref | `tortoise/__main__.py` prints `https://premiselabs.co/onboarding-prompt.md` after `tortoise onboard` | ✅ Covered by `tests/test_onboard_prompt_ref.py` |
| Analytics | `_track_analytics_event` + `_ALLOWED_ANALYTICS_PROPS` (already contains `harness`, `section`) | ✅ Pipeline exists; **copy beacon is dead** — `copyMcpConfig()` fires `fetch(…, {method:"PATCH"})` with no body AND no Authorization header; the PATCH handler requires Bearer auth (`get_current_team`) → 401, nothing ever recorded |
| MCP endpoint | `https://api.premiselabs.co/mcp/` | ✅ Verified this session: `/health` → ok, `/mcp/` → `{"status":"ok","protocol":"mcp","transport":"streamable-http","endpoint":"/mcp"}` |
| Landing page | `website/index.html` (premiselabs.co) | Thin marketing page; single CTA → tortoise.premiselabs.co (same Pages project). Onboarding entry = signup → welcome. **Not modified by this epic** (another agent may be working on it for a CAPTCHA issue; zero-conflict posture) |

## 3. Harness matrix — verified paste surfaces (fresh verification, 2026-08-11)

> Verified by research sub-agents against official docs/source with citations; machine-verified where possible. See `01-align.md` AL-1..AL-8.

### Claude Code
- **Config (action 1):** `claude mcp add --transport http tortoise https://api.premiselabs.co/mcp --header "Authorization: Bearer tt_KEY"` — syntax verified against official docs (`--transport http` current; SSE deprecated; `--header`/`-H` supported). Scopes: `local` (default, `~/.claude.json` per-project), `project` (`.mcp.json`, VCS-shared, one-time approval), `user` (all projects).
- **File alternative:** `.mcp.json` entry with `"type": "http"` (alias `"streamable-http"` accepted), `url`, `headers`; **`${VAR}` env expansion supported in `url` and `headers`**. A `url` without `type` is a config error (server skipped).
- **Instruction (action 2):** paste prompt in chat. Persistent alternative: `CLAUDE.md` auto-loads at session start (project root + upward walk; **Claude Code reads CLAUDE.md, NOT AGENTS.md** — official bridge is an `@AGENTS.md` import line).
- Current welcome-page gap: shows a raw `mcpServers` JSON blob (user must hand-merge into `.mcp.json`); no CLI one-liner; no instruction surface.

### Codex CLI
- **Config (action 1):** `codex mcp add tortoise --url https://api.premiselabs.co/mcp --bearer-token-env-var TORTOISE_API_KEY` — verified in `openai/codex` source (`codex-rs/cli/src/mcp_cmd.rs`). Writes `[mcp_servers.tortoise]` to `~/.codex/config.toml`; stores the env-var NAME, user must `export TORTOISE_API_KEY=tt_KEY`.
- **File alternative:** `~/.codex/config.toml` snippet `[mcp_servers.tortoise] url = "…" bearer_token_env_var = "TORTOISE_API_KEY"` (also `http_headers`, `env_http_headers` available).
- **Instruction (action 2):** paste prompt in chat. Persistent alternative: Codex auto-reads `AGENTS.md` ("Codex reads AGENTS.md files before doing any work": global `~/.codex/AGENTS.md`, then project root→cwd walk, 32 KiB combined cap).
- Current welcome-page gap: shows export + CLI pair — shape is right but untested as a paste path.

### Cursor
- **Config (action 1):** `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global). Remote server shape: `{"mcpServers": {"tortoise": {"url": "…", "headers": {"Authorization": "Bearer …"}}}}` — NO `type` field for url servers; `headers` supported.
- **Env expansion:** YES but **`${env:NAME}` syntax** (not `${VAR}`) — resolved in `command`, `args`, `env`, `url`, `headers`. Docs' canonical example is exactly Bearer-auth-on-url-server with `${env:…}`. → Cursor variant can avoid literal key on disk (AL-3 resolved YES).
- **Instruction (action 2):** `.cursor/rules/tortoise-onboarding.mdc` with frontmatter `alwaysApply: true` → injected at the start of EVERY chat session (exactly the onboarding trigger needed). Must be `.mdc` (plain `.md` ignored). Best-practice <500 lines. Simpler alternative: project-root `AGENTS.md` (also supported).
- Current welcome-page gap: only the `mcpServers` JSON shown; NO instruction surface at all — the Cursor agent is never told to run onboarding. **This is the biggest gap in the shipped surface.**

### Pi
- **Config (action 1):** pi core has no MCP support; the agent-infra `mcp-client` extension (this org's standard) resolves `.mcp.json` — upward walk cwd→git-toplevel, first hit wins, fallback `~/.pi/agent/.mcp.json`. Shape: `{"mcpServers": {"tortoise": {"url": "…", "headers": {"Authorization": "Bearer ${TORTOISE_API_KEY}"}}}}`. **`${VAR}` and `${VAR:-default}` expansion verified in extension source** (`expandExpr`; applied to `env`, `headers`, `cwd`) — no literal key needed.
- **Instruction (action 2):** `AGENTS.md` auto-loads (global `~/.pi/agent/AGENTS.md`, parent-walk, cwd) — standing instructions; or a skill for a gated flow.
- Current welcome-page gap: only the `mcpServers` JSON shown; no instruction surface.
- ⚠️ Flag: pi MCP is extension-provided and undocumented in official pi docs; two alternative community extensions use different file names (`mcp.json`, no dot). The variant doc names the agent-infra `mcp-client` convention explicitly.

### Cross-harness notes
- Root-level `.mcp.json` is read by pi's extension and Claude Code (project scope uses the same file name) but **invisible to Cursor** (`.cursor/mcp.json`) and Codex (`config.toml`). One shared repo file cannot serve all four — per-harness artifacts are required (confirms #235 A10).
- All four harnesses support Bearer auth on Streamable HTTP: Claude/Pi/Cursor via `headers`, Codex via `bearer_token_env_var`.

## 4. E2E verification legs available from this machine (verified)

1. **Tenant provisioning:** `POST /v1/register` is in SKIP_AUTH and returns a `tt_` key — a throwaway E2E tenant can be created programmatically.
2. **Pi variant:** this machine runs pi + agent-infra mcp-client. A scratch dir with the variant `.mcp.json` + `TORTOISE_API_KEY` env → task sub-agent session → `mcp__tortoise__*` tools appear → `tortoise_health` → `tortoise_create_point` = REAL first memory. Fully automatable.
3. **Claude/Codex CLIs:** present on this machine (cmux shims) — `mcp add` commands can be executed for real; agent-side chat legs depend on CLI auth state (best-effort, document outcome).
4. **Cursor:** no CLI path — file artifacts validated structurally (JSON schema + mdc frontmatter); human-in-harness required for the live leg.
5. **Server-side first memory:** MCP initialize + `tools/call tortoise_create_point` over HTTP with the Bearer key — provable with curl for ALL harnesses independent of client availability.

## 5. Tech Stack Research

No new dependencies. Changes land in: `tortoise/onboarding/variants/` (new header files), `.github/workflows/deploy-pages.yml` (stage variant files), `website/welcome.html` (per-harness Block A/B + beacon repair), `tortoise/hosted_api.py` (PATCH model + analytics emit), `tests/` (ref tests + endpoint test). Variant deployment pattern = the existing #540 staging pattern (canonical file → deployed copy at deploy time), generalized to N artifacts.

## 6. Assumptions Register

| # | Assumption | Confidence | Source | Validation Plan |
|---|-----------|-----------|--------|-----------------|
| R1 | Claude Code `claude mcp add --transport http … --header …` connects to the hosted server | HIGH (syntax verified vs docs) / MEDIUM (live connect) | §3 | Execute the command on this machine; live tools/list if CLI auth permits |
| R2 | Codex `mcp add --url … --bearer-token-env-var` writes a working config | HIGH (verified in source) | §3 | Execute; inspect `~/.codex/config.toml` |
| R3 | Cursor `.cursor/mcp.json` (no `type`, `headers`, `${env:VAR}`) + `.mdc` `alwaysApply: true` rule works as designed | HIGH (official docs, verbatim examples) | §3 | Structural validation; human-in-harness live leg |
| R4 | Pi `.mcp.json` + `${VAR}` expansion + AGENTS.md auto-load works with hosted server | HIGH (extension source + this machine proves the mechanism) | §3 | Full automated E2E via task sub-agent |
| R5 | Deploy-time concatenation (harness header + canonical body) prevents prompt drift | HIGH | §5 | Test: headers contain no `## Questions`; staged artifacts embed canonical body verbatim |
| R6 | `POST /v1/register` provisions a usable tenant from this machine | HIGH (endpoint in SKIP_AUTH, response model includes api_key) | §4 | E2E run |
| R7 | Beacon repair (auth + body + harness/section fields) produces attributable `artifact_copied` events | HIGH (props already allowed; handler pattern exists for `email` pop) | Align P1-1 fix | Unit test on PATCH handler |
| R8 | Human-in-harness legs remain for: Cursor live chat, Claude/Codex chat-paste trigger behavior | HIGH | §4 | Documented in E2E verification doc as human-required |
| R9 | **AL-7b carried forward:** chat-paste variants (Claude/Codex) trigger the flow only if the user pastes the prompt (behavioral); file-based variants (Cursor `.mdc` alwaysApply, Pi AGENTS.md) make the trigger structural | MEDIUM (file) / LOW (chat) | §3, AL-7b | Per-harness paste-trigger check in E2E doc (AL-6) + assert the fallback line ("If the agent doesn't start the flow, paste: 'Start Tortoise onboarding'") is present in every chat-paste variant artifact. Note: the fallback line is a #529 artifact — confirmed absent from `AGENT_ONBOARDING.md` today |

## 7. Sources (harness docs, verified 2026-08-11)

- Claude Code MCP (add command, `--transport http`, `--header`, scopes, `.mcp.json` shape + `${VAR}` expansion): https://code.claude.com/docs/en/mcp
- Claude Code memory (CLAUDE.md auto-load, `@AGENTS.md` import bridge, 200-line guidance): https://code.claude.com/docs/en/memory
- Codex CLI `mcp add` flags + `~/.codex/config.toml` write path (source): https://github.com/openai/codex/blob/main/codex-rs/cli/src/mcp_cmd.rs
- Codex manual (CLI examples): https://developers.openai.com/codex/codex-manual.md
- Codex AGENTS.md guide (auto-read, discovery order, 32 KiB cap): https://developers.openai.com/codex/guides/agents-md
- Codex MCP config reference (`bearer_token_env_var`, `http_headers`, `env_http_headers`): https://learn.chatgpt.com/docs/extend/mcp
- Cursor MCP (`.cursor/mcp.json`, url-server shape, `${env:NAME}` interpolation): https://cursor.com/docs/mcp
- Cursor rules (`.mdc` frontmatter, `alwaysApply` behavior matrix): https://cursor.com/docs/context/rules
- Pi: no official MCP docs (flagged); agent-infra `mcp-client` extension source on this machine (`resolveMcpJsonPath`, `expandExpr` — `${VAR}`/`${VAR:-default}`), plus pi.dev package registry entries `pi-mcp-extension` / `@nklisch/pi-mcp-adapter` (alternative conventions).
