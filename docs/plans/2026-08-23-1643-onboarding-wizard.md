# #1643 — In-dashboard onboarding wizard: implementation plan

> **For Pi:** implement task-by-task (TDD: failing test → implement → pass → commit).

**Goal:** A 7-step in-dashboard wizard (workspace → plan → key → harness → skills → data sources → STATE seed) replacing the welcome card's dead links + the graph-missing curl card, ending at an activation moment with a live overview.

**Base:** origin/main. Worktree `feat-1591-onboarding`.

## Task 1 — Fix `graph-scripts/decide.py` (the D3 blocker)

**Acceptance:** decide.py no longer passes `context=` to `create_point` (TypeError since #49 — every point soft-failed → stub-operator corruption #334). Points use anchors per #49; the script's option/criterion/finding/edge/truth/relevance flow writes real nodes + operators + computes EP confidence.
**Files:** `graph-scripts/decide.py`, `tests/test_decide.py` (+ a smoke test that the full input shape lands points + operators, no stubs).
**Step 1:** write the smoke test (fails on the current TypeError). **Step 2:** run (FAIL). **Step 3:** fix decide.py (drop `context=`; use `dedup=True` + content anchors; verify `compute_confidence`/`check_structure` calls). **Step 4:** run (PASS). **Step 5:** commit.

## Task 2 — New backend: `POST /v1/objects` + `about_object` on the point write (D5)

**Acceptance:** `POST /v1/objects` (session-gated) wraps `sdk.create_object(name, objectKind, ...)` — deterministic id by name → idempotent; returns the object. The point-write path gains an `about_object` option that wires `(p)-[:aboutObject]->(o)` (an EDGE, never a bare prop). Unit tests: create + idempotency + the aboutObject edge visible to traversal.
**Files:** `tortoise/hosted_api.py` (+CreateObjectRequest, +POST /v1/objects, +about_object on CreatePointRequest + handler), `tortoise/sdk.py` (expose the about-edge wiring if not public), `tests/test_hosted_api.py`.
**Step 1:** tests. **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS. **Step 5:** commit.

## Task 3 — Tortoise-decide SKILL.md (D3 packaging)

**Acceptance:** `skills/tortoise-decide/SKILL.md` — a TOOL-based agent skill (MCP: create_point/create_operator/compute_confidence/check_structure) AUTHORING the 7-step workflow (refine decision → research options/criteria → check with user → connect criteria→options → research IMPL/NAND mitigations → sub-mitigations → option relations → EP ranking); decide.py documented as the self-host variant. A static test pins the skill's existence + the 7 steps.
**Files:** `skills/tortoise-decide/SKILL.md`, `tests/test_website_static.py` (pin).
**Step 1:** test. **Step 2:** FAIL. **Step 3:** write the skill. **Step 4:** PASS. **Step 5:** commit.

## Task 4 — Wizard state + STEP 0 (workspace)

**Acceptance:** the wizard renders when a first-timer completes provisioning OR a returning empty-graph user lands; state via the existing `/v1/onboarding/state` model (no new marker); STEP 0: team confirm/rename (needs a rename endpoint OR a minimal PATCH — check; fallback: show + create-another + select), create-another (POST /v1/teams), multi-team SELECT for returning users. Wizard gated on `authed && !mountError` (#1559); claim funnel (#1511) runs first.
**Files:** dashboard `src/main.jsx` + `src/index.css`; possibly `hosted_api.py` (team rename).
**Step 1:** e2e for STEP 0. **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS. **Step 5:** commit.

## Task 5 — STEPS 1–3 (plan → key → harness chooser)

**Acceptance:** STEP 1 plan (free default, skippable — existing checkout CTAs); STEP 2 key reveal (A13, exactly once — existing reveal); STEP 3 harness chooser ported from welcome.html's HARNESS_* data (Claude/Codex/Cursor/Pi one-click + Copy; copy analytics wired to /v1/onboarding/state) into `src/harnesses.js`.
**Files:** `src/harnesses.js` (+ shared data), `main.jsx`, `index.css`.
**Step 1:** e2e. **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS. **Step 5:** commit.

## Task 6 — STEP 4 (skills primer)

**Acceptance:** the primer teaches both skills in context (post-harness): how-to-use-tortoise (passive, links the shipped skill) + tortoise-decide (invoke, links the new SKILL.md) — each with a one-line "what it does" + a "try it" pointer to STEP 6.
**Files:** `main.jsx` + `index.css`.
**Step 1:** e2e. **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS. **Step 5:** commit.

## Task 7 — STEP 5 (connect GitHub via the EXISTING OAuth+indexer surface)

**Acceptance:** connect = OAuth popup/redirect via `POST /v1/onboarding/github/connect` + state restore (GITHUB_STATES TTL); status via `GET /v1/onboarding/github/status`; a first-index via `POST /v1/index/github` + job polling; preview shows the repo count/ingested state; unconnected state is a clear "connect GitHub" CTA (no error card). Uses the per-team encrypted token — NO new token surface.
**Files:** `main.jsx` + the onboarding GitHub endpoints' test seams (httpx-client mock).
**Step 1:** e2e with mocked OAuth+indexer. **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS. **Step 5:** commit.

## Task 8 — STEP 6 (seed STATE) + STEP 7 (done)

**Acceptance:** STEP 6 offers (a) "ask your agent" (the primer's try-it) and (b) the guided seed: `POST /v1/objects` ({workspace}, in_progress) + the aboutObject point (authored by the user, tags ["onboarding"]) — idempotent; graph_ready=false means this write ALSO recovers the graph (define the failure fallback: a clear error + retry). STEP 7: completion writes onboarding_complete + the overview shows the Object/Point (activation).
**Files:** `main.jsx` + `index.css` (+ Task 2's endpoints).
**Step 1:** e2e (the seed lands an Object + aboutObject Point; completion state). **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS. **Step 5:** commit.

## Task 9 — Re-entry + full journey e2e

**Acceptance:** returning empty-graph users re-enter at STEP 4 (steps 4–7 available); completed users never see the wizard; multi-graph caveat handled (default-graph gate); the claim funnel + #1559 error paths don't conflict. The full journey e2e (steps 0–7, harness copy, skills, source connect, STATE seed) passes.
**Files:** `tests/e2e/test_dashboard_onboarding.py` (+ the existing dashboard e2e harness).
**Step 1:** the e2e. **Step 2:** FAIL. **Step 3:** implement re-entry. **Step 4:** PASS. **Step 5:** commit.

## Task 10 — Docs + commit-workflow

- Update `docs/auth-architecture.md` (or a new `docs/onboarding.md`) with the wizard + the onboarding-state model.
- Commit-workflow: PR, code-review (guidance+bug+security+UX), VGATE per commit, merge (admin override for the pre-existing CI reds), deploy, post-deploy verification of the journey, close #1643, cleanup.
