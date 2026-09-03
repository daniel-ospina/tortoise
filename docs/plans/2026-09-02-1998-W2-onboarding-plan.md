<!-- research-path: docs/epics/2026-08-29-agent-driven-onboarding-1976/02-research-brief.md -->

# W2 Agent Install Skill + Universal Command + Fork Card — Implementation Plan (rev 2, post-verify)

> **Issue:** #1998 (W2 of epic #1976, agent-driven onboarding) · **Branch:** feat/1998-W2-onboarding
> **Complexity:** standard (Architecture standard / UX standard) — epic child; scope anchored on the epic plan (§1 J1/J2, §2 WF-1, §3 P4, §4 DM-1/DM-3, §6 I-3/I-4, §8 R2-10) — the epic double-diamond is NOT re-run.
> **Team:** epistemic-team · **Epic:** #1976 · **Depends on (merged):** W5 (#2001 state.py + checkpoint), W1 (#1997 wizard shell)
> **Rev 2:** integrated findings from 2 parallel plan verifiers (2026-09-02). Where a verifier claim contradicted direct code reads, the code read is authoritative and the plan notes it.

## Scope (surfaces 4/5/6 of test-design #1992; DE2E-1/5/8/12 targets)

1. **SKILL.md successor to AGENT_ONBOARDING.md** (surface 6) — the ONE live onboarding script; owns the generic MCP-tool decide protocol (I-4; DE2E-5: works on all 6 harnesses WITHOUT the local `tortoise-decide` skill file) and the capture-announcement COPY CONTRACT (W6 owns trigger + Settings — epic §8 timing pin).
2. **Universal setup command** (surface 5) — one command per harness, all 6 (4 self-install: claude/codex/cursor/pi; 2 teach-human: claude-desktop/claude-web); agent self-adjudicates its harness from the SKILL.md table, verifies via `tortoise_health`; the harness-connected checkpoint is set (dashboard Continue + agent REST — see D2).
3. **Fork card semantics + persistence** (surface 4) — W1 renders the shell; W2 owns/asserts semantics: set-once `fork` persisted in OnboardingState; once per org; org B inherits (never re-asks on compact); never a billing gate.

## Verified ground truth (direct code reads — the verifiers' load-bearing claims checked)

- **Fork inheritance IS wired under the wizard's org-create lane.** `_create_onboarding_team_lane` (hosted_api.py:11912) routes through `provision_team` (supabase mode → `_ensure_onboarding_node_after_provision`, supabase_control.py:1769, computes prior memberships of `p_user_id`, excludes the new team, applies `resolve_init_fork_compact`) or `sdk.team_create` (registry mode, sdk.py:12035-12042 same discriminator). So org B created via `POST /v1/onboarding/team` DOES inherit fork + compact. (Verifier 2's P1-2 — "wizard lane has zero inheritance" — was a wrapper-only scan and is WRONG.) Caveats: the supabase hook is best-effort (swallows exceptions) and the wizard lane is one-shot per org (team_created guard) — so the second-org test drives the sdk lane (W5 fixture pattern) and asserts the WIRE shape.
- **No MCP checkpoint-write tool exists** (mcp_server.py `_ONBOARDING_TOOL_NAMES` = demo_create/state/session_recording/github_* only). The agent-side checkpoint write must use REST (`POST /v1/onboarding/state/checkpoint`, dual-auth, hosted) — CLI harnesses only. The MCP read tool `tortoise_onboarding_state` exists (hidden post-completion, Epic #888).
- `test_onboarding_variants.py` is registered under `onboarding:` (ci-surfaces.yml:373) AND `tier1:` (:459) — NOT in `carve_out:` (510-532; mirrored by TEST_NO_REDIRECT_STEMS). Do NOT add it to carve_out (embedded-by-design set). It carries BOTH variant/staging integrity tests AND 4 wizard-copy contract tests (harnesses.js/main.jsx scans) that must survive the M8 rewrite.
- The wizard connect step renders 6 `.harness-tab` elements (HARNESS_ORDER); the e2e's count-4 assertion (tests/e2e/test_dashboard_onboarding.py:166) is stale-today (opt-in suite, never CI-red).

## Design decisions

### D1 — The one live script: canonical SKILL.md + deploy mirror + installer

| Artifact | Path |
|---|---|
| Canonical live script | `tortoise/onboarding/SKILL.md` — frontmatter `name: tortoise-onboarding` (deploy identity), repo skill format (name/description/type/status/domain/tags/summary/allowed-tools + body) |
| Deploy mirror | `website/apps/dashboard/public/skills/tortoise-onboarding/SKILL.md` (served at `https://app.premiselabs.co/skills/tortoise-onboarding/SKILL.md`) |
| Installer | `website/apps/dashboard/public/install-tortoise-skills.sh`: add `tortoise-onboarding` to `SKILLS`, bump `SKILLS_VERSION` v1→v2 (its name-check greps `^name: tortoise-onboarding$` on the mirror — satisfied) |
| Parity pin | repo test: mirror == canonical byte-for-byte (drift-proofing carried forward from stage_variants) |

**SKILL.md body:** (1) frontmatter + identity (successor to AGENT_ONBOARDING.md — archived; exactly ONE live onboarding script); (2) **Read OnboardingState first** — resume-never-restart: agent prefers the `tortoise_onboarding_state` MCP read tool; CLI fallback `GET /v1/onboarding/state` (tt_ key). `fork: null` → NEVER guess/persist a fork (human-card territory) — tell the human the fork card is waiting on the dashboard and re-read. Post-completion re-entry = no-op (MCP read tool retired — Epic #888). SELF-HOSTED branch (no hosted REST): skip state-read + checkpoint steps, complete install+verify; W12 owns self-hosted node init (fork='self' default). (3) **Harness self-adjudication table** (6 rows: claude/cursor/codex/pi = config-writing self-install; claude-desktop/claude-web = teach-human — no local filesystem); (4) **connect + config-write** per row (env-var key indirection for project-scoped configs: claude/cursor/codex/pi; Desktop/Web configs stay literal-with-privacy-note — they are private user-machine/cloud-held, no commit surface); config-write validation + teach-human fallback on failure; (5) **verify** `tortoise_health` → retry with diagnostic → honest error; (6) **report** harness-connected checkpoint (REST POST, CLI harnesses) OR the human's dashboard Continue covers it (FWW keyed-MERGE — replay no-op; no double-completion); (7) **generic MCP-tool decide protocol** — point kinds (option/criterion/evidence), confirm-with-user before wiring, criteria→options IMPL/NAND, mitigations on operators, options↔options NAND, `tortoise_compute_confidence` ranking (≥1 IMPL edge prerequisite), `tortoise_check_structure` sanity — via existing tools ONLY (`tortoise_create_point`, `tortoise_create_operator`, `tortoise_mitigate_operator`, `tortoise_annotate_operator`, `tortoise_compute_confidence`, `tortoise_check_structure`, `tortoise_health`); (8) **capture-announcement COPY CONTRACT** verbatim: *"Heads up: I'll remember this session so you can recall it later. View/delete in Settings → Memory sources."* — fires at FIRST CAPTURE, one line, non-blocking; `capture-disclosed` is a node checkpoint, NEVER a card row (setupGuide.js `counted:false`); W6 owns the trigger + Settings view/delete (hook-driven auto-capture has no in-conversation turn — W6's trigger placement covers it); (9) pointers (seed = W3; how-to-use-tortoise / tortoise-file-finding for graph hygiene).

### D2 — Universal command (surface 5)

- `harnesses.js` — **additive only** (all legacy exports `HARNESS_INSTALL/HARNESS_STEPS/HARNESS_SKILLS/HARNESS_PERSIST/HARNESS_SKILLLESS/HARNESS_SKILLS_IN_PROMPT/HARNESS_SKILLS_IN_STEPS/HARNESS_COPY_LABEL/HARNESS_CONTINUE_LABEL/HARNESS_CAPTURE_*` PRESERVED — the ARCHIVED legacy wizard render (main.jsx, A0 rollback / DE2E-1 archived-not-deleted) imports them; deleting them breaks the restorable render):
  - `HARNESS_SELF_INSTALL = ['claude','codex','cursor','pi']`, `HARNESS_TEACH_HUMAN = ['claude-desktop','claude-web']` (disjoint; union == HARNESS_ORDER's 6).
  - `UNIVERSAL_COMMAND` (per-harness, fn of key): the NEW connect content = config-write (env-var indirection) + skill install (tortoise-onboarding + 3 core) + "verify with tortoise_health" instruction + teach-human manual steps (Desktop: `~/Library/Application Support/Claude/claude_desktop_config.json` mcpServers merge + restart; Web: claude.ai Settings → Connectors custom connector + the connector prompt). It does NOT embed the literal key (project-scoped configs) and does NOT embed MCP-tool tokens in shell commands (a shell command cannot call an MCP tool — the agent does that after install; verifier-2 P1-1 half-right, resolved by layering).
- **Connect step (main.jsx `wizardStep === 3`, W1's shell — tabs/buttons/classes preserved):** snippet = `UNIVERSAL_COMMAND[harness](key)`; a `.universal` note states the command covers all 6 harnesses (self-install vs teach-human).
- **harness-connected checkpoint surface (all 6):** the LIVE connect step's "I've set it up — Continue" handler (`main.jsx` ~4291, `wizardStep === 3`, currently `setWizardStep(4)`) now POSTs `{step: 'harness-connected'}` (session dual-auth; W2's connect-step ownership — W1's table left it "W2 owns"). FWW keyed-MERGE → replay no-op (repeat clicks + agent-side write both safe). Failure handling mirrors `handleWizardFork`: busy/disable the button, await the POST, on 503/error stay on step 3 with a retry message, advance on 2xx — Desktop/Web have NO agent-side REST fallback, so a fire-and-forget would silently strand their gate. ⛔ Hedge: main.jsx has TWO identical "…Continue" handlers (live step-3 at ~4291 targeting `setWizardStep(4)`; ARCHIVED twin at ~4404 targeting `setWizardStep(1)`) — modify ONLY the live one; leave the archived render byte-untouched (wizardArchived.test.js tripwire). The SKILL.md agent flow ALSO writes it (REST, CLI harnesses) after `tortoise_health` passes. Desktop/Web reach the gate via the human's Continue after the agent's teach-human steps + connector verify.

### D3 — M8 archive (never two live scripts)

1. `git mv tortoise/onboarding/AGENT_ONBOARDING.md → tortoise/onboarding/archive/AGENT_ONBOARDING.md` + ARCHIVED banner (superseded by SKILL.md; A0 rollback note). Variant headers → `tortoise/onboarding/archive/variants/*-header.md` (same banner).
2. Delete `tortoise/onboarding/stage_variants.py` (it staged ONLY the old prompt/variants). deploy-pages.yml: drop the "Stage onboarding prompt + harness variants" step; trigger paths `tortoise/onboarding/**` (archive move + SKILL.md both under it — fires the Pages + dashboard rebuilds; harmless).
3. Runtime fetchers → new live URL `https://app.premiselabs.co/skills/tortoise-onboarding/SKILL.md`:
   - `tortoise/__main__.py` `ONBOARDING_PROMPT_URL` (+ both print sites + ~2834) — copy rewritten from "paste this prompt" to "install/serve the tortoise-onboarding skill" (self-hosted flows).
   - `website/self-hosted.html` link + its paragraph (describes the OLD Q0–Q6 behavior — rewrite to the skill flow).
   - `website/website_architecture.md` ~156 (describes staging) — annotate.
   - `docs/quickstart-selfhosted.md` ~515 — repoint to the SKILL.md.
   - `tools/ci_selection.py` onboarding pattern tuple: drop the retired `"website/onboarding-prompt.md"` literal (website/ is filtered pre-pattern, so this is inert — removed for sweep hygiene).
   - `website/functions/_middleware.ts:56` comment names the retired artifact — sweep.
4. Tests → M8 contract (filesystem/URL asserts — tier1-safe):
   - `tests/test_onboarding_variants.py` — REWRITE the variant/staging half to M8 filesystem asserts (constants point at archive/; stage-script tests → assert the script is gone + SKILL.md is the live script + mirror == canonical + the one-live-script glob sweep: `tortoise/onboarding/*.md` (top level) == exactly `{SKILL.md}`; no AGENT_ONBOARDING.md / `*-header.md` outside archive/; no staging script). KEEP the 4 wizard-copy contract tests (they scan harnesses.js/main.jsx — still valid; legacy exports preserved).
   - `tests/test_onboard_prompt_ref.py` URL assert → new URL.
   - `tests/test_init_apikey_validation.py` (:199/:263/:368 — THREE asserts pinned to the old URL/`onboarding-prompt.md`, tier1) → assert the module constant / new URL.
   - `tests/e2e/test_welcome_page.py` (:41 PROMPT_URL + `test_onboarding_prompt_serves_markdown` — live-prod fetch in welcome-e2e-monitor, would 404-red after the staging step drops) → retarget to `https://app.premiselabs.co/skills/tortoise-onboarding/SKILL.md` (markdown + `name:` frontmatter asserts).
   - `tests/test_ci_selection.py` sample paths → the live `tortoise/onboarding/SKILL.md` (still maps to the onboarding surface).
   - `tests/test_onboarding_variants.py` REWRITE also adds the decide-contract scan: every `tortoise_*` token in SKILL.md's decide-protocol section ⊆ registered MCP tool names (mcp_server.py `def tortoise_*` set) + SKILL.md reads-OnboardingState step present (filesystem-only — fits onboarding + tier1 lanes).

### D4 — Fork semantics + universal-command + M8 tests

New `tests/test_onboarding_w2_fork_card.py` (docker-lane; module-level skip when `TORTOISE_DB_URI` unset — mirrors split-test guard; registered under `onboarding:` in ci-surfaces.yml). API/wire-shape assertions complementing W5's raw-writer + sdk-inheritance tests (W5 already covers sdk-level inheritance — this file covers the DASHBOARD call sequence + wire shape):
- first org (register fixture) → GET `/v1/onboarding/state`: `fork` null; checkpoint `{fork:'self'}` → 200 + GET shows self; replay same → 200 no-op (never re-asks); `{fork:'build'}` → 409 (set-once).
- second org (sdk lane with prior membership + fork=build on first — W5 fixture pattern) → GET shows inherited `fork:'build'` + `compact:true`; fork re-POST on org B → 200 no-op (inherited — org B never re-asks).
- negative: absent-node org first FLOW write (create-on-write seam) → node materializes with `compact:false` (never fabricated compact — guards a hook regression).
- fork write never touches jsonb operational keys (`session_recording` unchanged after fork checkpoint).
- build-fork gate via checkpoint surface: harness-connected + catalog-presented + first-points-filed (fork build) → status complete; capture-disclosed alone never completes.

New JS: `harnesses.test.js` (node --test, pure module — harnesses.js has no imports): HARNESS_SELF_INSTALL ∪ HARNESS_TEACH_HUMAN == HARNESS_ORDER (6, disjoint); UNIVERSAL_COMMAND per harness: config-write path token present (claude: `claude mcp add --transport http`, codex: `codex mcp add tortoise --url` + `--bearer-token-env-var`, cursor: `.cursor/mcp.json`, pi: `.mcp.json`, desktop: `claude_desktop_config.json`, web: connector + prompt); `tortoise_health` token; no `tt_` literal in project-scoped configs; legacy exports (A0 rollback data) still exported. `wizardFlow.js` gains pure `forkStepState(fork)` ('ask' | 'set') + tests; wizardFlow.test.js additions (universal-command mention in connect copy; fork copy unchanged).

### D5 — Lint + registration + tier-2

- SKILL.md lint: `frontmatter-validate.mjs` = the grammar gate (pass). `check-skill-lint.mjs --skills-dir tortoise/onboarding` is EXPECTED to exit 1 on exactly ONE documented P0 — `name: tortoise-onboarding` != dir `onboarding` (the dir is the domain package, as AGENT_ONBOARDING.md's was; the DEPLOYED mirror at `public/skills/tortoise-onboarding/` satisfies dir==name for harness installs). tortoise CI skips skill-lint (ci.yml:106-114); the staged-file hook gates parse/description only. The canonical cannot carry `name: onboarding` (would break the installer's `^name: tortoise-onboarding$` check + collide with Claude Code's built-in onboarding skill).
- ci-surfaces.yml: register the new `test_onboarding_w2_fork_card.py` under `onboarding:` (docker lane — NOT carve_out). `test_onboarding_variants.py` stays `onboarding:` + `tier1:` (no membership change; its rewrite stays filesystem-only).
- Tier-2 preserved: touched python = hosted_api.py (connect-step is JS; no hosted_api change needed unless the Continue checkpoint write is client-only — it is), supabase_control.py NOT touched, `__main__.py` (URL + copy only — NOT on the shared-module list), tests, tools/ci_selection.py. NO sdk.py/ep.py/exceptions.py/tool_registry.py/mcp_server.py/projection//conftest.py edits.

## Integration surface map (test-design)

| Surface | Layer | Verification |
|---|---|---|
| 6 skill | repo test + lint | SKILL.md at defined path; reads OnboardingState (MCP read tool + REST fallback); M8 one-live-script glob; decide protocol section + `tortoise_*` token ⊆ registered MCP tools contract test; mirror == canonical |
| 5 universal command | JS unit + contract | harnesses.test.js coverage (6 harnesses, self-install/teach-human split, config tokens, tortoise_health, no literal key) |
| 4 fork card | docker-lane API + JS unit | D4 |
| M8 archive | python unit | D3.4 |
| dist deploy mirror | build | dist rebuilt + committed (#1148 convention) |

## Tasks

1. **M8 archive** (D3): moves, banner, stage_variants.py deletion, deploy-pages.yml, __main__.py, self-hosted.html, website_architecture.md, quickstart, ci_selection.py + tests.
2. **SKILL.md + mirror + installer** (D1): author skill; copy to public/skills/tortoise-onboarding/; installer SKILLS + version.
3. **Universal command** (D2): harnesses.js exports; main.jsx connect step (payload + Continue checkpoint write); harnesses.test.js; wizardFlow forkStepState + tests.
4. **Python tests + ci-surfaces** (D4).
5. **e2e alignment**: test_dashboard_onboarding.py — fix the stale count-4 → 6; keep selectors (tabs/buttons unchanged).
6. **Verify + commit**: node --test; docker-lane pytest (new file + split + state); carve-out pytest; ruff; frontmatter lint; dist build; final `git merge origin/main`; commit-workflow (VGATE; registry "<worktree>::<file>").

## Risks
- W4 (merged to main) main.jsx overlap → final merge reconcile; regions disjoint (connect step vs Overview/Settings).
- e2e not runnable locally → selector-stable changes + stale-count fix only.
- SKILL.md name-vs-dir lint P0 → documented expected-fail (D5); mirror lint-clean.
- Best-effort supabase hook → negative test (create-on-write never fabricates compact) + W5's sdk-lane tests.
