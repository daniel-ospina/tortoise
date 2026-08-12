# Epic Scope — Harness-specific onboarding variants (#529)

**Date:** 2026-08-11
**Status:** approved via documented self-review (dispatch directive: no human pauses; align+research reviewer gates cleared)
**Inputs:** Align Decision (PROCEED, `01-align.md`) + Research Brief (`02-research-brief.md`)

---

## 1. Scope Boundaries

### In Scope

1. **Claude Code variant** — Block A becomes the canonical CLI one-liner `claude mcp add --transport http tortoise https://api.premiselabs.co/mcp --header "Authorization: Bearer tt_KEY"` (plus documented `.mcp.json` file alternative with `${VAR}` env expansion); Block B = chat-paste variant prompt carrying the fallback line + documented `CLAUDE.md` persistent alternative.
2. **Codex variant** — Block A = `codex mcp add tortoise --url https://api.premiselabs.co/mcp --bearer-token-env-var TORTOISE_API_KEY` + `export TORTOISE_API_KEY=tt_KEY` (config.toml snippet alternative documented); Block B = chat-paste variant prompt with fallback line + documented `AGENTS.md` persistent alternative.
3. **Cursor variant** — Block A = `.cursor/mcp.json` file pair member with `${env:TORTOISE_API_KEY}` indirection (literal-key alternative documented); Block B = `.cursor/rules/tortoise-onboarding.mdc` with `alwaysApply: true` (structural trigger — no chat paste needed).
4. **Pi variant** — Block A = `.mcp.json` entry with `${TORTOISE_API_KEY}` expansion (agent-infra mcp-client convention, named explicitly); Block B = AGENTS.md onboarding block (structural trigger).
5. **Variant prompt artifacts + drift-proof deployment** — hand-written per-harness wrapper headers (`tortoise/onboarding/variants/<harness>-header.md`, delivery instructions + fallback line where applicable) concatenated at deploy time with the canonical `AGENT_ONBOARDING.md` body → staged to `website/onboarding/<harness>.md` by `deploy-pages.yml`, served at `https://premiselabs.co/onboarding/<harness>.md`. The canonical body is never forked.
6. **Welcome page per-harness surfaces** — `website/welcome.html`: harness tabs keep their names; Block A content becomes harness-optimal per items 1–4; Block B fetches the per-harness variant prompt; per-harness 2-step delivery instructions; copy actions fire the repaired beacon.
7. **Measurement (align P1-1 fix)** — beacon repair (Authorization header + JSON body) + `harness`/`section` optional fields on `OnboardingStatePatchRequest` + `artifact_copied` analytics emit from the PATCH handler (fields popped from state merge, `email`-pattern).
8. **Tests** — variant integrity refs (headers contain no question flow; staged artifacts embed canonical body verbatim; chat-paste variants contain the fallback line), welcome.html refs (command shapes per harness, beacon auth+body), PATCH analytics unit test, staging-script test.
9. **E2E verification doc** — per variant: machine-verified legs executed from this machine (incl. fully-automated Pi variant E2E: scratch-dir `.mcp.json` + key → pi sub-agent session → first memory) + explicit human-in-harness checklist for Cursor live chat and Claude/Codex chat-paste legs.

### Out of Scope

| Item | Reason | Defer to |
|------|--------|----------|
| New backend endpoints | #235's surface suffices; only one existing model extends | N/A |
| New MCP tools | Onboarding tools live (#235) and retire post-completion (#888) — untouched | N/A |
| Changes to `AGENT_ONBOARDING.md` content | Single source of truth (#496); variants wrap, never fork | Future epic only via #496 process |
| `curl \| bash` installer variant | Trust barrier + four config writers in one script (align Step 1) | Future epic if file-pairs prove insufficient |
| MCP on-connect auto-trigger | No standard hook exists (#235 research) | Future epic if MCP standardizes it |
| Landing page (`website/index.html`) changes | Zero-conflict posture (parallel CAPTCHA work); onboarding entry stays signup→welcome | Future epic |
| Self-hosted `tortoise onboard` flow changes | Already prints the canonical URL (#544); self-hosted harness config shapes handled by parallel PR #970 | N/A |
| Codex/Claude desktop-GUI variants | CLI surfaces are the documented, testable paths | Future epic |
| OAuth-based MCP auth | Bearer `tt_` keys suffice (A12, HIGH) | Future security epic |
| Dashboard onboarding wizard | Welcome page is the surface (#235 decision) | Future epic |

### Boundary Rationale

**Guiding principle: wrappers, not forks.** The generalized core (#235) is proven and live; every variant reuses the SAME canonical question flow, the SAME hosted endpoints, and the SAME welcome page — varying only the two surfaces each harness actually differs on: (a) where the MCP config lives and in what shape, (b) how the agent receives the instruction to run onboarding (chat paste vs auto-loaded file). Nothing is built twice; nothing diverges in feature set (epic indicator 3 enforced by construction: the canonical body is physically concatenated at deploy time and asserted verbatim in tests).

## 2. Customer Value Map

| Scoped Capability | User-Visible Value |
|-------------------|--------------------|
| Claude Code variant (item 1) | Claude Code user pastes ONE terminal command (no JSON hand-merging) and one chat prompt, reaches first memory |
| Codex variant (item 2) | Codex user gets the exact `codex mcp add` invocation + env-var pattern, reaches first memory without guessing flags |
| Cursor variant (item 3) | Cursor user drops two files; the agent is told automatically what to do — no need to remember to paste a prompt (biggest shipped gap closed) |
| Pi variant (item 4) | Pi user drops two files with no literal key on disk; onboarding starts automatically in the next session |
| Variant artifacts + deployment (item 5) | Every harness gets its own stable, linkable onboarding URL that can never drift from the canonical flow |
| Welcome page per-harness surfaces (item 6) | Signup→setup is two clearly-numbered actions per harness instead of "figure out which blob goes where" |
| Measurement (item 7) | Team can see, per harness, how many users copy the setup — first attributable funnel data for onboarding |
| Tests (item 8) | Harness syntax rot and prompt drift are caught in CI before users hit them |
| E2E verification doc (item 9) | Each variant's paste path is proven, and exactly what still needs a human in the harness is written down — no unknowns at launch |

## 3. Complexity Ratings

| Axis | Rating | Rationale |
|------|--------|-----------|
| UX | **medium** | Four tab surfaces each with two blocks and per-harness delivery instructions; the patterns exist (tabs/labels already shipped) but the content quadruples and must stay consistent. |
| Architecture | **low** | No new endpoints, no new tools, no new infra: static file staging (existing #540 pattern generalized), one model extension + one analytics emit, one JS map + fetch URLs in welcome.html. |
| Ontology | **low** | No graph entities, no entity-class changes; onboarding state keys unchanged except two non-persisted request fields. |
| Accessibility | **low** | Static page patterns already accessible (aria-pressed tabs exist); variant artifacts are plain markdown/JSON/TOML; must preserve existing labels on any changed controls. |

## 4. High-Level E2E Test Cases

> Written BEFORE user journeys, per epic-scope. Behavioral — no UI implementation details.

### E2E-1: Welcome page serves every harness's optimal paste path
**Given:** A user has completed signup and their `tt_` key is displayed on the welcome page
**When:** They select each harness tab in turn
**Then:** Block A shows that harness's optimal config (Claude = CLI one-liner; Codex = CLI + env export; Cursor = `.cursor/mcp.json` JSON; Pi = `.mcp.json` JSON) with the user's key materialized in the literal-key forms (Claude CLI flag, Codex export) and env-var indirection shown with the key-export instruction in the file forms (Cursor, Pi)
**And:** Block B shows that harness's variant onboarding prompt with 2 numbered delivery steps
**And:** every variant prompt URL (`/onboarding/<harness>.md`) returns 200 with the variant content

### E2E-2: Claude Code variant → first memory
**Given:** A user with Claude Code CLI and a `tt_` key
**When:** They run the Block A command, then paste the Block B prompt into chat
**Then:** Claude Code lists Tortoise tools and the agent starts the onboarding flow (trigger is behavioral — chat paste)
**And:** a first Point is created (first memory)
**And:** if the flow doesn't start, the documented fallback line ("Start Tortoise onboarding") starts it

### E2E-3: Codex variant → first memory
**Given:** A user with Codex CLI and a `tt_` key
**When:** They export `TORTOISE_API_KEY`, run the Block A `codex mcp add` command, then paste the Block B prompt
**Then:** `~/.codex/config.toml` gains the tortoise server entry (env-var name, not the secret)
**And:** Codex lists Tortoise tools and the onboarding flow starts; a first Point is created

### E2E-4: Cursor variant → first memory (structural trigger)
**Given:** A user with Cursor who created `.cursor/mcp.json` and `.cursor/rules/tortoise-onboarding.mdc` from the variant artifacts
**When:** They open a chat session
**Then:** the agent connects to Tortoise AND begins onboarding WITHOUT any chat paste (the alwaysApply rule injects the instruction)
**And:** a first Point is created
**And:** no literal `tt_` key is stored when the `${env:TORTOISE_API_KEY}` form is used

### E2E-5: Pi variant → first memory (structural trigger, fully automated)
**Given:** A scratch directory containing the variant `.mcp.json` (with `${TORTOISE_API_KEY}`) and the AGENTS.md onboarding block, with `TORTOISE_API_KEY` exported
**When:** a pi session starts in that directory
**Then:** `mcp__tortoise__*` tools are discovered; `tortoise_health` returns OK
**And:** `tortoise_create_point` succeeds — first memory written
**And:** this entire leg runs from CI/agent automation without a human

### E2E-6: Variants share the generalized core (no divergence)
**Given:** All variant artifacts (headers + staged full prompts) and the canonical `AGENT_ONBOARDING.md`
**When:** integrity checks run
**Then:** every staged variant embeds the canonical body verbatim (byte-identical suffix)
**And:** no header file contains its own question flow (`## Questions` absent)
**And:** the welcome page's Block B content equals the deployed variant files

### E2E-7: ≤2 copy-paste actions per harness
**Given:** The documented paste path for each variant
**When:** counted from landing (welcome page) to first memory
**Then:** Claude Code = 2 actions (CLI paste + prompt paste); Codex = 2 (CLI paste + prompt paste; env export documented as part of action 1); Cursor = 2 (two file pastes, both structural); Pi = 2 (two file pastes, both structural)
**And:** no harness requires more than 2

### E2E-8: Copy events are attributable per harness
**Given:** The welcome page with a valid displayed key
**When:** the user copies a harness's config or prompt
**Then:** an authenticated beacon PATCHes `/v1/onboarding/state` with `{harness, section}`
**And:** the server records an `artifact_copied` analytics event with exactly those props
**And:** onboarding state itself is unchanged (harness/section are popped, not persisted)

## 5. Human Approval Gate

Per dispatch directive (epic filed + execution authorized), the human approval gate is satisfied by documented self-review: scope boundaries derive directly from the epic body's four variants + indicators; reviewer gate (below) provides the adversarial check. Decision recorded: **proceed to planning.**

## Scope Review Gate Record

Fresh-context reviewer (cycle 1): one P2 — E2E-1 "key materialized" clause contradicted the no-literal-key design for Cursor/Pi; fixed verbatim as prescribed (see E2E-1 Then-clause above). Reviewer verdict after fix: proceed to planning. Gate CLEARED.
