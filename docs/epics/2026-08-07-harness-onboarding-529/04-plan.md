<!-- research-path: docs/epics/2026-08-07-harness-onboarding-529/02-research-brief.md -->

# Harness-specific Onboarding Variants (#529) — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.
> **Epic-level plan.** Decomposes into child issues via `epic-decompose` after coherence review.

**Review-gate record (Substeps 1–7, fresh-context reviewers, 5 parallel dispatches):** 2×P1 + 13×P2 found, ALL fixed in this doc — J1 drafting artifact; missing journey edge cases (fallback line, CLAUDE.md/config.toml alternatives); J4 merge-not-append; state note; `section` enum gains `both` per #235 schema; workflow paths gain staging script; pi shape drops `type`; key-already-shown Block A note; T1 gains exactly-2-steps + alternatives + exact-fallback-string assertions; T3 rewritten as semantic fragments + JSON-parse + no-literal-key negatives; T6 parameterized (invalid harness AND section); T10 source pinned to welcome.html extraction; T5/T6 capture mechanics pinned (fallback path + Supabase delenv); T8 dispatch cwd + evidence contract (UUID + read-back); T9 scratch-dir + cleanup + CLI-list-primary; T11 gains fallback-behavior negative leg. Coherence review (cycle 1): 4×P2 fixed — T1 gains four edge-case content assertions; Task B pins the `TORTOISE_CFG_BEGIN/END` marker convention; T7b added for key-already-shown state + Task D gains re-visit/retired-tools clickthrough items; T3 gains paste-session-only label + claude `"type": "http"` assertions and T5 gains the valid-`both` case.

**Goal:** Every supported harness (Claude Code, Codex, Cursor, Pi) gets its optimal onboarding paste path — config + prompt — on the existing welcome page, reaching first memory in ≤2 copy-paste actions, with all variants sharing the generalized #235 core by construction.

**Architecture posture:** content + static-file staging + one PATCH-model extension + one analytics emit + one JS map update. No new endpoints, no new MCP tools, no new infra. Complexity: UX=medium, Arch=low, Ontology=low, A11y=low (scope doc §3).

## Integration Surface Map (test-design gate)

> The epic-workflow Test-Design Gate requires this map as a child issue; per dispatch gh-budget it is created at Decompose stage (issue body carries this map verbatim). Recorded deviation: map embedded here first, issue filed in the same gh batch as the other children. **Filed as #965.**

| # | Surface | System A | System B | Type | Test Layer | Failure Modes |
|---|---------|----------|----------|------|------------|---------------|
| 1 | welcome.html Block A configs | `HARNESS_CONFIGS` JS | harness CLI/file formats (external contracts) | JS literals → user paste | Ref test (pytest string assertions on welcome.html) | CLI syntax rot; wrong flag order; missing env-export step |
| 2 | welcome.html Block B fetch | page JS | Pages static `/onboarding/<h>.md` | fetch → render | Ref test (URL convention) + post-deploy live check (capstone) | 404 on variant URL; fetch error → must fall back to canonical prompt URL |
| 3 | Variant staging | `deploy-pages.yml` + staging script | `tortoise/onboarding/variants/*` + canonical body | build-time concat | Unit test: run script → tmp dir, assert outputs | Empty output (`test -s` catches); drift (suffix test catches) |
| 4 | Beacon PATCH | welcome.html fetch | `PATCH /v1/onboarding/state` | JS → FastAPI (Bearer) | Unit test (TestClient) + JS ref test | 401 if auth dropped again; body schema mismatch |
| 5 | Analytics emit | PATCH handler | `_track_analytics_event` | internal call | Unit test (JSONL fallback capture) | Props stripped (must stay within `_ALLOWED_ANALYTICS_PROPS`); state pollution if fields not popped |
| 6 | Pi variant runtime | scratch `.mcp.json` + AGENTS.md | pi mcp-client + hosted MCP | live MCP session | Automated E2E (task sub-agent) | Extension absent; `${VAR}` expansion fails; key unset |
| 7 | Claude/Codex CLI config | `claude/codex mcp add` | `~/.claude.json`, `~/.codex/config.toml` | CLI exec | Real CLI run on this machine + human chat leg | CLI auth state; shim behavior; config write path changes |
| 8 | Cursor artifacts | `.cursor/mcp.json` + `.mdc` | Cursor runtime | file artifacts | Structural validation tests (JSON parse, mdc frontmatter) + human live leg | `${env:}` unsupported in old Cursor; wrong file extension ignored |

## Substep 1 — User Journeys

**Personas:** P1 = backend dev evaluating agent memory in Claude Code; P2 = OpenAI-stack dev in Codex; P3 = IDE-first dev in Cursor; P4 = pi power-user. All: technical, key in hand post-signup, churn risk = setup friction.

### J1 — Claude Code (chat-paste variant)
Entry: welcome page, key displayed. Steps: (1) Claude tab → copy CLI one-liner → run in terminal (action 1); (2) copy variant prompt → paste into Claude Code chat (action 2); (3) agent runs Q0–Q6 flow; (4) first Point created. Exit: memory digest shown. Edge cases: flow doesn't start after paste → fallback line "Start Tortoise onboarding" (documented in variant header); invalid key → `tortoise_health` fails → prompt's Step-0 error path; project-scope `.mcp.json` alternative requires one-time approval (documented); persistent alternative = `CLAUDE.md` block (documented in header; Claude Code auto-loads CLAUDE.md, not AGENTS.md).
### J2 — Codex (chat-paste variant)
Steps: (1) Codex tab → copy block (export + `codex mcp add`) → run (action 1, env export counted within it); (2) copy variant prompt → paste (action 2); (3) flow; (4) first Point. Edge cases: user skips export → Codex connects with no bearer → health fails → variant header names the exact fix (`export TORTOISE_API_KEY=…`); flow doesn't start after paste → fallback line "Start Tortoise onboarding" (in variant header); persistent alternatives = `~/.codex/config.toml` snippet + `AGENTS.md` block (both documented in header; Codex auto-reads AGENTS.md).
### J3 — Cursor (file-pair variant, structural trigger)
Steps: (1) Cursor tab → copy `.cursor/mcp.json` (with `${env:TORTOISE_API_KEY}` + export instruction, literal-key alternative shown) → save file (action 1); (2) copy `.mdc` rule content → save `.cursor/rules/tortoise-onboarding.mdc` (action 2); (3) open any chat — rule auto-injected, agent connects + starts flow WITHOUT chat paste; (4) first Point. Edge: `.md` instead of `.mdc` → ignored by Cursor — warning line in instructions; `${env:}` unset → connect error, fix documented.
### J4 — Pi (file-pair variant, structural trigger)
Steps: (1) Pi tab → copy `.mcp.json` entry (`${TORTOISE_API_KEY}`) → save to project root, or MERGE into the `mcpServers` object of an existing `~/.pi/agent/.mcp.json` (never literal-append — instructions say "if a `.mcp.json` exists, merge — do not append") (action 1); (2) copy AGENTS.md block → append to project AGENTS.md (action 2); (3) next pi session auto-loads both; flow starts; (4) first Point. Edge: mcp-client extension absent → header points at agent-infra bootstrap; key unset → health fails with guidance.
### J5 — Returning user (all harnesses)
Onboarding already complete → `tortoise_onboarding_*` tools retired from tools/list (#888). Variant prompt's Q-actions fail gracefully → prompt's error-recovery paths skip ahead; connection + memory still verified by Q6. No journey breakage.

**Gate self-check:** journeys cover all in-scope items 1–4 (+6 via welcome steps, +9 via exit states); edge cases per journey included. Page states (loading/error/key-already-shown) unchanged per Substep 3; Block B fetch failure falls back to the canonical prompt URL (Substep 5) — covered in W2's beacon failure mode.

## Substep 2 — Workflows

**W1 — Deploy-time variant staging.** Trigger: push to `main` touching `website/**`, `tortoise/onboarding/AGENT_ONBOARDING.md`, `tortoise/onboarding/variants/**`, or `tortoise/onboarding/stage_variants.py` (script changes must redeploy — otherwise deployed artifacts silently drift from the format CI asserts). Steps: checkout → run staging script (concatenate each `variants/<h>-header.md` + canonical body → `website/onboarding/<h>.md`; also stage canonical → `website/onboarding-prompt.md` as today) → `test -s` each output → existing DNS step → wrangler deploy. Failure modes: script error/empty output blocks deploy (`test -s` + CI integrity tests pre-merge).
**W2 — Copy attribution.** User clicks copy → JS fires `PATCH /v1/onboarding/state` with `Authorization: Bearer <displayed key>` + `{harness, section}` → handler validates enum membership → emits `artifact_copied` (allowed props only) → Supabase or JSONL fallback. Invalid/missing values → no event, no error, no state change. Failure modes: key already hidden (re-visit) → beacon cannot auth → silently skipped (acceptable; event semantics = copy-with-key-visible).
**W3 — E2E verification.** Register throwaway tenant (`POST /v1/register`) → materialize each variant into its native surface (scratch dirs / real CLIs) → execute machine legs → record PASS/FAIL + evidence per leg in `06-e2e-verification.md` → mark human-in-harness legs as HUMAN-REQUIRED with exact steps.
**W4 — Drift guard.** Any PR touching canonical prompt or variant headers runs integrity tests (headers contain no `## Questions`; staging output suffix == canonical body; fallback line present in chat-paste variants). Failure blocks merge.

**Gate self-check:** workflows align with journeys (W1↔J1–J4 artifacts, W2↔measurement, W3↔verification); handoffs explicit; failure modes documented.

## Substep 3 — Prototype

Non-GUI treatment (no new screens; existing tab UI reused). Markdown wireframe of the updated tab content:

```
┌ Use Tortoise — connect your agent ─────────────────────────┐
│ [Claude Code] [Codex] [Cursor] [Pi]        (tabs, existing) │
│                                                             │
│ Step 1 — Add Tortoise to <harness>            [Copy]        │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ <harness-optimal Block A: CLI one-liner | export+CLI |  │ │
│ │  .cursor/mcp.json JSON | .mcp.json JSON>                │ │
│ └─────────────────────────────────────────────────────────┘ │
│ Step 2 — Start onboarding                     [Copy]        │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ <harness-optimal Block B: chat prompt | chat prompt |   │ │
│ │  .mdc rule file content | AGENTS.md block>              │ │
│ └─────────────────────────────────────────────────────────┘ │
│ 2-step delivery line per harness (e.g. Cursor: "Save as     │
│ .cursor/rules/tortoise-onboarding.mdc — loads automatically │
│ in every chat. No paste into chat needed.")                 │
└─────────────────────────────────────────────────────────────┘
```

States: loading (unchanged), ready (above), key-already-shown (Block A offers env-indirection forms only where literal key unavailable — documented), error (unchanged). Design system: existing `.card`/`.snippet`/`.btn-copy`/tab classes; aria-pressed preserved.

## Substep 4 — Data Model

No new entities, no graph changes. One request-model extension:

```python
class OnboardingStatePatchRequest(BaseModel):  # tortoise/hosted_api.py
    ...existing fields...
    harness: str | None = None   # "claude"|"codex"|"cursor"|"pi" — analytics only, NOT persisted
    section: str | None = None   # "config"|"prompt"|"both" — analytics only, NOT persisted
```

Behavior contract: handler pops `harness`/`section` before `_update_onboarding_state` (same pattern as `email`); if `harness ∈ {claude,codex,cursor,pi}` and `section ∈ {config,prompt,both}` (subset-of-#235-enum validated; `both` accepted for a future copy-both action per #235's schema), emit `_track_analytics_event(team_id, "artifact_copied", {"harness":…, "section":…})`; invalid values → no event, no error. Analytics props already allowed (`_ALLOWED_ANALYTICS_PROPS` contains both). Integrity constraints: enum check server-side; state keys unchanged (`_ALLOWED_STATE_KEYS` untouched).

**Enum↔slug mapping (coherence fix):** analytics/beacon harness ENUM values are `{claude, codex, cursor, pi}` (short form, #235 schema); variant ARTIFACT slugs are `{claude-code, codex, cursor, pi}` (file names). welcome.html owns the single explicit mapping `claude → claude-code` (identity for the other three) when building the Block B fetch URL; T3 asserts the mapping's presence.

## Substep 5 — Architecture

```
tortoise/onboarding/
  AGENT_ONBOARDING.md            (canonical — UNCHANGED)
  variants/
    claude-code-header.md        NEW (delivery instructions + fallback line)
    codex-header.md              NEW
    cursor-header.md             NEW
    pi-header.md                 NEW
  stage_variants.py              NEW (concat script; used by workflow + tests)
        │  (deploy-pages.yml runs this, then `test -s` each output)
        ▼
website/onboarding/{claude-code,codex,cursor,pi}.md   (staged in the workflow only — not committed — like onboarding-prompt.md today)
        │
        ▼ Cloudflare Pages (existing deploy step)
https://premiselabs.co/onboarding/<harness>.md
        ▲ fetch (Block B, per active tab; fallback → /onboarding-prompt.md)
website/welcome.html  (HARNESS_CONFIGS updated; Block B per-harness; beacon repaired)
        │ PATCH {harness, section} + Bearer
        ▼
tortoise/hosted_api.py PATCH /v1/onboarding/state → _track_analytics_event("artifact_copied")
```

Boundaries: staging script is the ONLY place canonical+headers combine (single concat point = single drift-control point). welcome.html never concatenates; it fetches finished artifacts. Failure handling: variant fetch 404/error → JS falls back to canonical prompt URL (existing fetch pattern) with unchanged UX; beacon failure silently caught (existing `try/catch`), never blocks copy.

## Substep 6 — Interfaces

**Static URLs (GET, public):**
| URL | 200 body | 404 |
|---|---|---|
| `/onboarding/claude-code.md` | claude header + `\n\n---\n\n` + canonical body | pre-deploy/missing |
| `/onboarding/codex.md` | codex header + separator + canonical body | same |
| `/onboarding/cursor.md` | cursor header + separator + canonical body | same |
| `/onboarding/pi.md` | pi header + separator + canonical body | same |
| `/onboarding-prompt.md` | canonical body (unchanged) | — |

**PATCH /v1/onboarding/state** — extended request per Substep 4; response unchanged (`{onboarding, email}`); error behavior: auth as today (401 without Bearer); harness/section never error (ignore-and-continue).

**Variant header contract (tested):**
1. Markdown; starts with `# Tortoise Onboarding — <Harness> setup`.
2. Contains a `## How to use` section with exactly 2 numbered delivery steps.
3. Does NOT contain `## Questions` (question flow lives only in canonical body).
4. Chat-paste variants (claude-code, codex): MUST contain the exact fallback line: `If the agent doesn't start the flow, paste: "Start Tortoise onboarding"` (double quotes are the contract; research brief R9's single-quote rendering is non-normative).
5. File-pair variants (cursor, pi): MUST name the exact file paths (`.cursor/rules/tortoise-onboarding.mdc` / project `AGENTS.md`) and state that onboarding starts automatically.

**Staging script contract:** `python3 tortoise/onboarding/stage_variants.py [--out DIR]` → writes 4 files + canonical copy; deterministic byte output: `header.rstrip() + "\n\n---\n\n" + canonical_body.lstrip()`; exit non-zero if any input missing. Analytics event contract: `artifact_copied {harness ∈ {claude,codex,cursor,pi}, section ∈ {config,prompt,both}}` — #235's schema enum verbatim (align cycle-3 fix).

## Substep 7 — Detailed E2E Tests

> Maps 1:1 to scope E2E-1..8. Machine-executable unless marked HUMAN-LEG.

| ID | Scope ref | Test | Layer | Setup / Assertions |
|----|-----------|------|-------|--------------------|
| T1 | E2E-6 | `tests/test_onboarding_variants.py::test_headers_have_no_question_flow` | unit | Parse each `variants/*-header.md`: no `## Questions`; `## How to use` present with EXACTLY 2 numbered steps (`^\d+\.` count == 2); EXACT fallback string `If the agent doesn't start the flow, paste: "Start Tortoise onboarding"` in claude-code/codex headers; file paths named in cursor/pi headers; documented alternatives present — claude-code header names `.mcp.json` + `${VAR}` expansion + `CLAUDE.md` + project-scope one-time approval note; codex header names `config.toml` + `AGENTS.md` + the skip-export fix `export TORTOISE_API_KEY=`; cursor header carries the `.md`-vs-`.mdc` warning; pi header carries the agent-infra mcp-client bootstrap link for the extension-absent case |
| T2 | E2E-6 | `test_stage_variants_embeds_canonical_verbatim` | unit | Run `stage_variants.py --out tmp` via subprocess with repo-root cwd; for each output: `content.endswith(canonical_body.lstrip())`; header prefix matches source file; canonical copy == source |
| T3 | E2E-1 | `test_welcome_harness_configs_are_optimal` | ref test | Semantic fragments (individually, NOT one frozen command string — tolerates flag reorder/whitespace): claude block has `claude mcp add`, `--transport http`, `https://api.premiselabs.co/mcp`, `Authorization: Bearer`; codex block has `codex mcp add`, `--url https://api.premiselabs.co/mcp`, `--bearer-token-env-var TORTOISE_API_KEY`, `export TORTOISE_API_KEY=`; cursor/pi JSON blocks EXTRACTED between the `/* TORTOISE_CFG_BEGIN:<harness> */`/`END` markers (Task B convention) and `json.loads`-parsed: shape `{mcpServers:{tortoise:{url,headers}}}`, cursor has `${env:TORTOISE_API_KEY}`, pi has `${TORTOISE_API_KEY}`; file paths `.cursor/mcp.json`, `.cursor/rules/tortoise-onboarding.mdc`, `.mcp.json` named; per-harness variant URLs `/onboarding/<slug>.md` present + enum→slug mapping (`claude-code`); claude `.mcp.json` file-alternative fragment asserts `"type": "http"` (missing type = server skipped per research §3) + `${VAR}` header form; literal-key alternatives for cursor/pi are labeled `paste-session only` (substring adjacent to the literal form); NEGATIVE: cursor and pi env-form JSON blocks contain NO literal `tt_` key |
| T4 | E2E-8 | `test_welcome_beacon_authenticated_with_body` | ref test | welcome.html copy-beacon fetch includes `Authorization: Bearer` and a JSON body containing `harness` and `section` |
| T5 | E2E-8 | `tests/test_onboarding_analytics_patch.py::test_patch_harness_section_emits_event` | unit (TestClient) | Capture mechanics PINNED (pattern: tests/test_mcp_telemetry.py:295): monkeypatch `hosted_api._ANALYTICS_FALLBACK_PATH` → `tmp_path/"analytics_fallback.jsonl"` + `monkeypatch.delenv("SUPABASE_URL")`/`("SUPABASE_SERVICE_KEY")`; auth fixture per tests/test_onboarding_endpoints.py. Authed PATCH `{harness:"cursor", section:"config"}` → 200; exactly ONE `artifact_copied` line with exactly those props; response `onboarding` contains no `harness` key. Plus one valid-`both` case: PATCH `{harness:"cursor", section:"both"}` → event with `section:"both"` (#235 enum member exercised) |
| T6 | E2E-8 | `test_patch_invalid_harness_or_section_ignored` | unit | Parameterized: PATCH `{harness:"vim", section:"config"}` AND `{harness:"cursor", section:"bogus"}` → both 200; no event; no state change |
| T7 | E2E-8 | `test_patch_without_auth_still_401` (in `tests/test_onboarding_analytics_patch.py`) | regression | Unauthenticated PATCH → 401 (beacon failure mode stays visible, not silent-200) |
| T7b | E2E-1/J5 | `test_key_already_shown_env_indirection` | ref test | Key-already-shown branch (welcome.html) renders env-indirection Block A forms (same marker-extraction helper as T3) with NO literal `tt_` — pins the #745c behavior change from Task B |
| T8 | E2E-5 | Pi variant automated E2E | E2E (task sub-agent) | Scratch dir + variant `.mcp.json` + AGENTS.md block + `TORTOISE_API_KEY` from throwaway tenant. Dispatch mechanics PINNED: task sub-agent runs with `cwd=<scratch dir>` (mcp-client upward walk resolves the variant `.mcp.json`) and `TORTOISE_API_KEY` exported. Evidence contract (anti-confabulation): sub-agent returns (a) `tortoise_health` raw output, (b) `tortoise_create_point` raw output INCLUDING the created Point UUID, (c) a READ-BACK leg — query/fetch the point by that ID and confirm it exists (proves persistence, not just API return). Orchestrator greps the evidence for a UUID-shaped ID before marking PASS. Recorded in 06-e2e-verification.md |
| T9 | E2E-2/E2E-3 | Claude/Codex CLI config legs | E2E (this machine) | Run both legs IN A SCRATCH DIR (claude local scope stays there); primary assertion = CLI's own list output (`claude mcp list` / `codex mcp list`) showing the tortoise entry (Codex: env-var NAME only, never the secret); config files kept as diagnostic artifacts; CLEANUP: remove tortoise entries + scratch dirs afterwards, recorded in 06-e2e-verification.md. Chat-paste legs = HUMAN-LEG checklist (must include exercising the fallback line "Start Tortoise onboarding") |
| T10 | E2E-4 | Cursor structural validation | unit + HUMAN-LEG | SOURCE PINNED (no free-standing fixture — drift hazard): extract the Cursor JSON + `.mdc` content from `HARNESS_CONFIGS` in welcome.html via the `TORTOISE_CFG_BEGIN/END` marker convention (Task B), then `json.loads` (valid JSON, url/headers shape, `${env:` form, NO literal `tt_`) and parse mdc frontmatter (`alwaysApply: true`); live Cursor chat = HUMAN-LEG checklist |
| T11 | E2E-1 | Post-deploy variant URLs + fallback behavior | capstone (manual) | `curl -s -o /dev/null -w "%{http_code}"` each of the 5 URLs → 200; each variant body ends with canonical body; NEGATIVE leg: block/404 a variant URL (devtools) → Block B renders the canonical prompt via fallback fetch, UX unchanged |
| T12 | E2E-7 | Paste-action count audit | doc assertion | 06-e2e-verification.md table: per-harness action count ≤2 with counting convention verbatim from scope E2E-7 |

Negative cases covered: T6 (invalid enum), T7 (auth regression), T1-drift assertions, Block B fetch fallback (T3 asserts fallback URL presence in JS; T11 behavior leg).

## Substep 8 — Coherence Review + Risks

**Risks & mitigations:**
| Risk | L×I | Mitigation |
|------|-----|-----------|
| Harness CLI syntax churn (claude/codex flags change) | M×M | Exact commands live in ONE JS map + ref tests (T3); sources cited in research brief §7; capstone re-verifies post-deploy |
| Prompt drift (variants fork the question flow) | L×H | Generation-by-concatenation + T1/T2 byte assertions; canonical file unchanged |
| Literal key on disk (Cursor/Pi configs committed to user repos) | M×M | Env-indirection is PRIMARY form for both; literal alternative labeled "paste-session only"; docs warn against committing |
| Beacon regression (auth/body dropped again) | L×M | T4 ref test pins both; T7 pins 401 semantics |
| `${env:}` support varies by Cursor version | L×L | Literal-key alternative documented in same block |
| Pi MCP convention ambiguity (3 community extensions) | M×L | Variant names agent-infra mcp-client convention explicitly; header links bootstrap |
| Pages deploy of new `/onboarding/` path fails silently | L×M | `test -s` in workflow + T11 post-deploy check in capstone |
| Throwaway E2E tenants accumulate in prod | L×L | Use one tenant for all legs; document cleanup (team deletion) in E2E doc |
| Enum↔slug drift (analytics `claude` vs artifact `claude-code`) | L×M | Mapping owned in ONE place (welcome.html, Substep 4 contract); T3 asserts it |

**Coherence self-check:** journeys (1) ↔ workflows (2): every journey artifact produced by W1/W2; prototype (3) shows exactly the Blocks journeys copy; data model (4) serves only W2; architecture (5) has one concat point matching interface contracts (6); detailed tests (7) cover every surface row (map above: surfaces 1→T3, 2→T3/T11, 3→T1/T2, 4→T4/T7, 5→T5/T6, 6→T8, 7→T9, 8→T10).

**Coherence + MECE review record:** fresh-context combined gate (cycle 1): MECE CLEAN; one coherence P2 (enum↔slug mapping) — fixed in Substep 4 + risks table + T3.

## Task Breakdown (feeds Decompose)

| Task | Content | Files | Depends |
|------|---------|-------|---------|
| A — Variant artifacts + staging | 4 header files, `stage_variants.py`, deploy-pages.yml wiring, T1+T2 | `tortoise/onboarding/variants/*`, `tortoise/onboarding/stage_variants.py`, `.github/workflows/deploy-pages.yml`, `tests/test_onboarding_variants.py` | — |
| B — Welcome page + measurement | HARNESS_CONFIGS harness-optimal rewrite with explicit extraction markers: each JSON literal wrapped in `/* TORTOISE_CFG_BEGIN:<harness> */` … `/* TORTOISE_CFG_END:<harness> */` comments (the delimiter convention T3/T10 parse against). Pi block uses research-brief shape (`url`+`headers`, NO `type` field — today's `type: "streamable-http"` is dropped), Block B per-harness fetch+fallback, beacon repair, PATCH model + analytics emit, T3–T7b. Note: key-already-shown state (#745c) now shows env-indirection Block A forms instead of hiding cards — J5 must not become a dead journey | `website/welcome.html`, `tortoise/hosted_api.py`, `tests/test_onboarding_analytics_patch.py` (+ welcome refs in test_onboarding_variants.py or sibling) | A (URL convention; buildable in parallel once interface frozen) |
| C — E2E verification | Throwaway tenant, T8 Pi automated leg, T9 CLI legs, T10 structural checks, T12 audit, `06-e2e-verification.md` | `docs/epics/2026-08-07-harness-onboarding-529/06-e2e-verification.md` | A, B |
| D — Capstone | Post-deploy URL checks (T11), full clickthrough landing→signup→welcome→per-harness legs INCLUDING a re-visit (key-already-shown) pass and a retired-tools pass (team with `onboarding_complete` → variant prompt's error-recovery skips ahead per J5), human-leg checklists handed to owner | capstone issue | A, B, C |
| TD — Test-design issue | Carries the Integration Surface Map verbatim (gate artifact) | issue body only | — |

## Verification Plan

**Pre-deploy:** `python -m pytest tests/ -q` (all green incl. new tests); staging script run locally; welcome.html renders all tabs (browser check in capstone).
**Post-deploy:** T11 URL checks; one live copy-beacon observation (analytics fallback or Supabase row).
**Owner acceptance:** human-leg checklists for Cursor live chat + Claude/Codex chat-paste (exact steps in 06-e2e-verification.md).
