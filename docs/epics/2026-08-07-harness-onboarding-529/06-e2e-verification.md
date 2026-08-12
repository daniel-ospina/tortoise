---
title: "E2E Verification — Epic #529: Harness-specific onboarding variants"
type: log
domain: capability
doc_status: live
subjects.team: epistemic-team
created: 2026-08-11
aboutSubjects: tortoise
aboutObjects: tortoise
---

# E2E Verification — Epic #529 (issue #968, plan Substep 7 / W3)

**Machine:** macOS (cmux swarm host), python3.12 (uv), pi v0.84.1 + agent-infra mcp-client (MCP SDK 1.29.0).
**Throwaway tenant:** `epic529-e2e-variants@premiselabs.co` via `POST https://api.premiselabs.co/v1/register` → team `8b3d7a0f41b043d5bc3a1d7f77`, key `tt_bde263cd…` (test-only; delete team after epic close).

## T8 — Pi variant (scope E2E-5)

| Leg | Result | Evidence |
|---|---|---|
| Variant `.mcp.json` resolved by pi mcp-client | **PASS** | `[mcp-client] Resolved .mcp.json: /private/tmp/e2e-529-pi/.mcp.json (searched up from /private/tmp/e2e-529-pi …)` — the exact Block A env-form file from welcome.html, dropped into an empty scratch dir |
| AGENTS.md variant loads (staged artifact) | **PASS (structural)** | `stage_variants.py --out …/staged` → `onboarding/pi.md` copied as scratch AGENTS.md; 10,490 bytes; pi session ingests it (session ran with it as the project AGENTS.md) |
| Server handshake with tenant key | **PASS** | `POST /mcp/ initialize` → 200, `"serverInfo":{"name":"tortoise","version":"3.4.6"}` |
| `tortoise_health` | **PASS** | tools/call → structured result (status field reports `degraded/no_sdk_registered` — health-surface reporting artifact in HTTP mode; call itself succeeded) |
| `tortoise_create_point` → **first memory** | **PASS** | tools/call → `{"id":"19ff459d2a0-32b82f250cd2","content":"E2E first memory — epic #529 automated server-side leg (Pi variant tenant)","pointKind":"decision",…}` |
| Read-back by ID (persistence) | **PASS** | `tortoise_get(type="point", id="19ff459d2a0-32b82f250cd2")` returned the same point — UUID-shaped ID grep PASS |
| pi client end-to-end connect (`mcp__tortoise__*` tools in a pi session) | **PASS (post-deploy, two fixes)** | Attempt 1 (post-#977 deploy) still failed — instrumented SDK probe exposed a second chain: POST /mcp → 307 with scheme-downgraded Location (http://) → Fly http→https 301 → fetch-spec POST→GET conversion → GET /mcp/ → 405. Fixed by trailing-slash URLs everywhere (PR #984). Attempt 2 with slash URL: `[mcp-client] Resolved .mcp.json: /private/tmp/e2e-529-pi/.mcp.json` → `Connected to 'tortoise' (1329ms)` → `Connected to 1/1 servers`; HEALTH ok; CREATE → point `19ff4c3589e-3b269a98833c`; READBACK by id returned identical content/kind; `EVIDENCE: 19ff4c3589e-3b269a98833c` grep PASS. Variant artifacts used verbatim: Block A `.mcp.json` (env indirection, no literal key) + staged `onboarding/pi.md` as AGENTS.md |

**Verdict:** ALL T8 legs PASS (after two server-side compatibility fixes discovered BY this test — exactly the failure mode the "tested paste path" indicator exists to catch). E2E-5 (Pi variant, scope): landing artifact → `.mcp.json` + AGENTS.md drop-in → automated pi session → first memory + read-back. No human required.

## T9 — Claude Code / Codex CLI config legs (scope E2E-2/E2E-3)

| Leg | Result | Evidence |
|---|---|---|
| `claude mcp add --transport http …` execution | **HUMAN-REQUIRED** | This machine has cmux shims only — the real Claude Code CLI is not installed (`Error: claude not found in PATH` from the wrapper). Syntax verified against official docs (research brief §3/§7: verbatim docs example match). |
| `codex mcp add tortoise --url … --bearer-token-env-var …` execution | **HUMAN-REQUIRED** | Same — no real Codex CLI on this machine. Flags verified in `openai/codex` source (`codex-rs/cli/src/mcp_cmd.rs`). |
| Chat-paste trigger legs (both) | **HUMAN-REQUIRED** | Behavioral trigger (align AL-7b); includes exercising the fallback line `Start Tortoise onboarding`. |

**Owner checklist (Claude Code):** (1) run the Block A one-liner from the welcome page; (2) `claude mcp list` shows tortoise; (3) paste Block B into chat; (4) agent asks Q1; answer through to Q6; (5) verify a Point exists (dashboard or `tortoise_get`); (6) if the flow didn't start, paste the fallback line and confirm it starts.
**Owner checklist (Codex):** (1) `export TORTOISE_API_KEY=tt_…`; (2) run Block A; (3) `codex mcp list` shows tortoise (env-var NAME stored, never the secret — check `~/.codex/config.toml`); (4–6) same as Claude.

## T10 — Cursor structural validation (scope E2E-4)

| Leg | Result | Evidence |
|---|---|---|
| `.cursor/mcp.json` artifact valid | **PASS** | `test_welcome_harness_configs_are_optimal` extracts the marker-delimited JSON, `json.loads` OK, shape `{mcpServers:{tortoise:{url,headers}}}`, `${env:TORTOISE_API_KEY}` present, NO literal `tt_` |
| `.mdc` rule artifact valid | **PASS** | `test_staged_cursor_variant_is_valid_mdc`: staged cursor variant opens with YAML frontmatter containing `alwaysApply: true` |
| Live Cursor chat leg | **HUMAN-REQUIRED** | No Cursor on this machine. Owner checklist: save both files → open chat → agent connects + starts onboarding WITHOUT any chat paste → first Point created. |

## T12 — Paste-action count audit (scope E2E-7)

| Harness | Action 1 | Action 2 | Total | Trigger model |
|---|---|---|---|---|
| Claude Code | run CLI one-liner in terminal | paste variant prompt into chat | **2** | behavioral (chat paste) |
| Codex | run export + `codex mcp add` (env export counted within action 1 — counting convention verbatim from 03-scope.md E2E-7) | paste variant prompt into chat | **2** | behavioral (chat paste) |
| Cursor | save `.cursor/mcp.json` (+ export key) | save `.cursor/rules/tortoise-onboarding.mdc` | **2** | structural (alwaysApply rule) |
| Pi | save `.mcp.json` (merge if exists; export key) | append variant block to AGENTS.md | **2** | structural (AGENTS.md auto-load) |

**All harnesses ≤ 2 actions.** ✅

## T11 — Post-deploy URL checks (capstone #969, runs after merge)

Pending: `https://premiselabs.co/onboarding/{claude-code,codex,cursor,pi}.md` + `/onboarding-prompt.md` → expect 200 ×5, each variant body ends with the canonical body; fallback negative leg (404 a variant URL in devtools → Block B renders canonical prompt).

Also post-deploy: (a) re-run the T8 pi-session connect leg (needs the GET-405 fix live); (b) tighten tests/e2e/test_welcome_page.py's four dual-shape assertions to new-shape-only (transition comments mark them).

## Environment notes (for reproducibility)

- `hosted_api` TestClient suites hang on this machine (pre-existing: unchanged `tests/test_onboarding_endpoints.py` reproduces; congested redislite environment from the parallel swarm). CI is the venue for T5–T7.
- Claude/Codex CLIs: cmux shims only — real binaries not installed.
- MCP URL trailing slash is load-bearing: POST /mcp 307-redirects with a scheme-downgraded Location (http://) behind the proxy; some HTTP stacks (Node fetch observed) convert the follow-up http→https 301 POST→GET and land on the GET metadata route instead of JSON-RPC. All Block A URLs ship with the trailing slash (follow-up fix PR); server-side proxy-headers/redirect hardening filed separately.
