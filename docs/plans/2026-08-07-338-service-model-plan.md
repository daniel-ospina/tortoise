<!-- research-path: docs/research/2026-08-07-338-service-model.md -->

# #338 Service-Model Migration Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Ship Tortoise as a first-class service — installable self-host daemon (Docker/GHCR), consistent BSL 1.1 + $5M AUG license, service-first docs, and a connector that proves "connect, don't import."

**Team:** Premise Labs
**Tier:** Standard (scoping doc: `docs/plans/2026-08-07-338-service-model-scoping.md`, D1–D7 pinned)
**Architecture:** Thin FastAPI self-host daemon (`tortoise/selfhost.py`) reusing `mcp_server.create_http_app()` with a new `auth_mode` param ("tenant" | "static" | "none"). Client layer wraps fastmcp's shipped client (zero new deps). Two parallel work tracks (service/consumer + license) converge in the docs rewrite. No Supabase, no hosted machinery in the self-host image.

---

## 1. Approach Decision — **A-hybrid: "Daemon-first, consumer-validated, license-in-parallel"**

**Choice:** Approach **A's architecture** (thin daemon reusing `create_http_app` + `auth_mode` param, NO Supabase — exactly as scope D1 pins) **plus C's client-first insight absorbed** (a real MCP consumer — the bridge — validates the daemon contract before the image ships), **with the license track running in parallel from Phase 0 instead of first or last.**

**Why A-hybrid wins (evidence from codebase):**
- `create_http_app()` (mcp_server.py, ~:896 — cite by symbol, line drifts) already bakes multi-tenant auth into its middleware stack: `TeamResolutionMiddleware` demands `Bearer tt_` tokens, lazily imports `hosted_api._make_sdk`, and resolves against the Supabase-backed registry (`apikey_verify`). Approach B ships this + the dream queue + `/internal/*` in the self-host image — the exact multi-tenant coupling class behind the 2026-08-05 incident and the verifier findings. D1 pins against it.
- The `auth_mode` param is a small, **backwards-compatible additive** change: default `"tenant"` reproduces hosted byte-for-byte; `"static"`/`"none"` simply omit `TeamResolutionMiddleware` from the middleware list (no dead code in the image — it's not imported at all in those modes). Hosted regression suite is the gate.
- fastmcp 3.4.6 (pinned) ships `fastmcp.client.Client` + `StreamableHttpTransport` + `BearerAuth` (verified installed). The thin MCP client wraps it — **zero new third-party deps**, so the research-intake gate is justifiably skipped. C's client is genuinely cheap and seeds #526.
- The bridge (D5) is a real consumer: converting it first proves AC7 (MIT products integrate without importing) with production code, not docs prose. Client-first de-risks the daemon contract before the GHCR image commits to it.
- License is the owner's headline "Why" but has **zero technical dependency** on the daemon — it runs as a parallel track, so the #1 Indicator (installable service) isn't delayed behind a human-dependent CLA audit, and the "Why" isn't deferred last. Both move from day one.

### Rejected alternatives (and when each WOULD have been better)

| Alternative | Verdict | When it would have been better |
|---|---|---|
| **B — One-app factory** (`create_hosted_app(tenant_mode=...)`, self-host = same 1207-line app) | Rejected. Ships TeamResolutionMiddleware/Supabase registry auth/dream queue//internal in the image (security surface + license-boundary blur); refactor touches the deployed production app construction = highest single risk in the issue; contradicts pinned D1. | If hosted and self-host were meant to share a single evolving codebase long-term (self-host as a stepping stone to tenant-mode flags), or if touching hosted construction were already on #338's critical path (it isn't). Also if feature-parity-drift between hosted/self-host were the dominant risk. |
| **C — Contract-first, client-driven, license/docs LAST** | Partially absorbed (client-first sub-step). Rejected as a whole: license-last delays the owner's headline blocker behind the CLA-audit schedule risk, and the daemon's contract is already canonical (#510 tool_registry) — there's no interface uncertainty that only a real consumer could resolve. | If the MCP tool surface were still contested/unstable, client-first would de-risk the contract before daemon implementation. That uncertainty does not exist: `tool_registry.py` #510 is merged and canonical. |
| **A as-stated — license/docs FIRST, then daemon** | Rejected. Docs-first delays the #1 Indicator (installable service smoke test) behind a human-dependent audit with zero technical dependency on the daemon. The controller's own note flags this. | If the daemon's design were still contested (docs could lock the story first), or if the license were the issue's sole deliverable. Neither holds — D1 already pins the daemon shape. |

---

## 2. Problem Statement (confirmed, from scope)

1. pip ships the entire engine (`pyproject.toml` packages `tortoise*` wholesale; sdk.py = 3,593 lines wrapping FalkorDB); adopters embed engine + license.
2. Four-file license tri-state: README=BSL 1.1, LICENSE=AGPL-3.0, pyproject=MIT, index.md=AGPLv3+CLA — blocks MIT products (David Waring).
3. Nothing is installable as a service (no GHCR, no adopter-facing self-host guide; only the hosted-operator runbook).
4. Integrations embed engine access: `integrations/crm/twenty/bridge.py` does `from tortoise.sdk import TortoiseSDK` (2 call sites, lines 221, 233).
5. Owner decision (fixed): MongoDB-style service model — daemon + connect-via-MCP/REST; dual offering self-host (BSL + $5M AUG, MPL-2.0 after 4 years) + hosted (commercial, outside grant).

---

## 3. License Clause → Precedent Mapping (owner mandate, D3)

LICENSE = committed legal instrument (canonical BUSL-1.1 + parameters + AUG, machine-parseable). `docs/license-notes.md` = provenance split (precedent URLs, parameter rationale, audit findings). Drafting borrows clause language per mapping:

| Clause in our LICENSE | Precedent (borrow language from) |
|---|---|
| SPDX identifier + canonical BSL text (verbatim) | SPDX `BUSL-1.1` canonical text — https://spdx.org/licenses/BUSL-1.1.html |
| Additional Use Grant (AUG) paragraph structure | Couchbase Server BSL 1.1 LICENSE (AUG pattern) |
| Quantitative threshold — "$5M USD trailing 12-month revenue" | MariaDB MaxScale BSL 1.1 AUG (threshold phrasing) |
| Grant exclusions — hosted-as-a-service excluded, anti-resale | Sentry FSL $5M grant language (licensee revenue cap + service exclusion); CockroachDB BSL (exclusion mechanics) |
| Parameter block (Licensor / Licensed Work / Additional Use Grant / Change Date) | HashiCorp BSL 1.1 parameter-block formatting |
| Change Date = 4 years → Mozilla Public License 2.0 (MPL-2.0) conversion | Redis BSL→Apache-2.0 conversion (precedent); Elasticsearch conversion mechanism |

**Mechanics:** `LICENSE` (canonical text + filled parameters + AUG, nothing else) + `docs/license-notes.md` (provenance: each clause → precedent URL, parameter rationale, CLA-audit findings, FAQ drafting notes). **Precondition:** CLA/copyright audit (Phase 0) — git history shows a single human author, but exact counts must be reconciled in P0.1: main has ~508 commits = 505 `daniel-ospina` + 1 `Daniel Ospina` (case-variant author string, same human — must be reconciled) + 2 Fly bot commits. Low risk, must be documented.

---

## 4. Phased Implementation

### Phase 0 — Preconditions (parallel, ~0.5 day)
- **P0.1 CLA audit (D3 dep):** `git log --format='%an' | sort -u` + LICENSE history + CONTRIBUTORS check → findings into `docs/license-notes.md` §Audit. **Explicitly reconcile `daniel-ospina` vs `Daniel Ospina` author strings (same human, different git config — count them as one contributor).**
- **P0.2 Merge main into feat/338 (ACTION, not verify):** branch base predates #510 (tool_registry.py canonical ToolDefinition/RestSpec — the client/driver contract) and #516 (embeddings cache path fix that T4.1 depends on). `git fetch origin main && git cherry-pick <base>..origin/main` (or PR base update), then re-run the FULL hosted suite. Post-merge: re-verify `HTTP_ALLOWED` is registry-derived (moved from hardcoded frozenset `mcp_auth.py:63`), re-derive T1.4 `tools/list` assertions against the registry, and confirm `create_http_app` signature drift (currently `mcp_server.py:896`). THEN edit `create_http_app` (T1.1).
- **P0.3 ASGI-spike:** confirm `create_http_app()` serves via httpx `ASGITransport` (no socket) — decides whether daemon tests are ASGI-level (preferred) or subprocess-level. Output: one line in plan-review.
- **P0.4 Conditional-import spike (T1.1 risk):** verify Python import caching cannot produce false-positive `TeamResolutionMiddleware` imports in static/none modes under ASGI worker reuse (same process previously ran tenant mode). **If uncertain → structural fallback:** keep `create_http_app` tenant-mode-only (unchanged) and add `create_selfhost_app()` calling a lower-level factory without tenant middleware. The `auth_mode` param is preferred but not worth a hosted-outage risk; decision recorded in plan-review.

### Phase 1 — Daemon (D1, Indicator path — highest-risk code first)
**GATE G1:** daemon boots + serves MCP + `/health`; hosted byte-identical (full existing suite green).

- **T1.1 `auth_mode` param on `create_http_app`** — `tortoise/mcp_server.py` (cite by symbol, line drifts; ~:896 at plan time; P0.2 merge shifts lines). Signature: `auth_mode: Literal["tenant","static","none"]="tenant", api_key: str | None = None`. Middleware list conditional: `TeamResolutionMiddleware` only when `"tenant"`; `"static"` adds small `StaticKeyMiddleware` (constant-time compare, lives in `tortoise/mcp_auth.py` next to siblings, one-directional dep); `"none"` = security headers/body-size/rate-limit only. **Guard the function-level `from tortoise.mcp_auth import (TeamResolutionMiddleware, ...)` import (mcp_server.py ~:914, currently unconditional) behind the tenant branch** so static/none modes never reference it. Default preserves hosted exactly. Tests: `tests/test_mcp_server_auth_modes.py` — all three modes: 401 without key, 200 with, tenant-mode regression (existing auth tests unchanged).
- **T1.2 `tortoise/selfhost.py`** (new, ~120 lines) — thin FastAPI app: mounts MCP app at `/mcp`, `/health` (liveness) + `/health/ready` (DB reachability). **Must replicate hosted_api's `_lifespan` composition (`async with mcp_http_app.lifespan(mcp_http_app): yield`) — Starlette `Mount` does NOT propagate sub-app lifespan (hosted_api.py:69–79); without it the MCP handshake fails silently (no `StreamableHTTPSessionManager`).** T1.4's ASGI initialize test is the deliberate backstop. Env: `TORTOISE_DB_URI` (durable FalkorDB) or `TORTOISE_DB_PATH` (embedded FalkorDBLite eval), `TORTOISE_API_KEY` (**mapping pinned:** set → auth_mode="static"; unset → auth_mode="none" — document the footgun: a non-localhost `TORTOISE_HOST` bind with no key exposes an unauthenticated engine), `TORTOISE_HOST=127.0.0.1`, `TORTOISE_PORT=8000`, `TORTOISE_RATE_LIMIT` (default **100 req/min per IP** — matches MCP SSE burst patterns where a streaming `tools/call` generates ~5–10 requests; documented in T5.1 env table). **`allowed_origins` decision (pin):** selfhost passes `allowed_origins=["http://localhost:8000"]` so browser/web clients (MCP Inspector, web UIs) work; CLI clients (`claude mcp add`, `codex mcp add`) send no Origin and pass regardless. Add one test with an Origin-bearing request asserting the intended behavior (create_http_app defaults `allowed_origins=[]` + `host_origin_protection=True` → 403 for Origin senders if unset). **Embedded eval prints a prominent startup warning** — not durable, backup procedure documented (2026-08-05 incident #101: 5,748 points lost; AOF-off + no backups). **Grep gate:** no `supabase`, `hosted_api`, `TeamResolutionMiddleware` imports reachable from selfhost path (test-asserted).
- **T1.3 `deployment.py`** — add `serve_http()` (uvicorn on selfhost app); `[project.scripts]` + **`tortoise-serve http [--host HOST] [--port PORT] [--api-key KEY]`** (CLI flags override env vars; stdio `serve` kept).
- **T1.4 `tests/test_selfhost.py`** — ASGI: `/health` 200, `/health/ready` 200 with embedded DB, MCP `initialize` + `tools/list`; subprocess smoke: `python -m tortoise.selfhost` on ephemeral port, real HTTP handshake. **Note: `_HTTPToolFilter` applies to selfhost too — operator-only tools (`team_create`, `backfill_v25`, `ingest_corpus`) remain HTTP-hidden (stdio-only); document in the daemon README.**

### Phase 2 — Consumer validation (C absorbed: D5 + D6) — depends on Phase 1
**GATE G2:** bridge pushes points through the daemon over MCP with **zero engine imports**; `.mcp.json` connects to self-host.

- **T2.1 `tortoise/mcp_client.py`** (new, ~100 lines) — thin wrapper over `fastmcp.client.Client` + `StreamableHttpTransport` + `BearerAuth`; tool names/params derived from `tool_registry.py` #510 (single source of truth); config `TORTOISE_MCP_URL` (default `http://localhost:8000/mcp`); graceful degradation (daemon down → `tortoise_unavailable` status, exit 0). Seeds #526. Tests: `tests/test_mcp_client.py` against in-process daemon fixture (FalkorDBLite).
- **T2.2 bridge conversion** — `integrations/crm/twenty/bridge.py:221,233`: replace both `from tortoise.sdk import TortoiseSDK` sites with mcp_client; idempotency (content_hash) + review-queue logic untouched. **Static test:** AST check asserts zero `from tortoise.sdk import` in `integrations/`. `tests/test_bridge_mcp.py`: daemon fixture + bridge push → verify point via MCP read; daemon-down → graceful skip path.
- **T2.3 `.mcp.json` (D6)** — tortoise entry → `http://localhost:8000/mcp` (dev-local, docs-marked); remove hardcoded absolute cwd (`/Users/danielospina/Documents/GitHub/tortoise`); hosted endpoint noted as alternative. **Accepted drift: README's .mcp.json snippet is updated later in T5.1 (G2–G5 window where docs lag config — noted, not blocking).**

### Phase 3 — License track (D3, owner "Why") — parallel with Phases 1–2
**GATE G3:** four-file license consistency + owner/legal approval (human gate).

- **T3.1 `LICENSE`** — canonical BUSL-1.1 text + filled parameter block + AUG per §3 mapping. **`docs/license-notes.md`** — provenance split (clause→precedent URLs, rationale, audit findings).
- **T3.2 `pyproject.toml`** — `license = "BUSL-1.1"`; classifiers: replace MIT classifier with `License :: OSI Approved :: Business Source License 1.1` (fallback `License :: Other/Proprietary License` if classifier absent from PyPI enum — check at implementation time).
- **T3.3 `validation/check-license-surface.py`** (new; repo-local — `scripts/` is an agent-infra symlink) — asserts all four surfaces (LICENSE/README/pyproject/index.md) declare BSL 1.1 + $5M AUG + Mozilla Public License 2.0 (MPL-2.0) conversion. **Placement:** repo-local `.ci-checks/check-license-surface.sh` (precedent: `.ci-checks/check-test-isolation.sh`) — do NOT wire into `python-ci.yml`, which is a symlink to agent-infra templates (cross-repo blast radius + agent-infra version-sync pre-commit). **CI activation deferred to T5.3** (README/index not yet converged until Phase 5 — wiring at T3.3 leaves CI red through Phase 3–4). Prevents the tri-state from re-occurring (root cause: graph decision never synced to files).

### Phase 4 — Image + compose (D2, the #1 Indicator) — depends on Phase 1; overlaps Phase 2
**GATE G4:** `docker run` → `/health` 200 → MCP `initialize` handshake in CI; compose variant boots.

- **T4.1 `Dockerfile.selfhost`** (new) — modeled on `Dockerfile.hosted` (bookworm, requirements, `pip install -e .`); embeddings cache via `[embeddings]` extra (parity; note: optional at runtime).
- **T4.2 `.github/workflows/publish-selfhost.yml`** (new) — GHCR: `on: push tags v* + workflow_dispatch`; jobs: build/publish (packages: write) + **smoke job**: build → `docker run` embedded eval → `/health` → MCP `initialize` handshake. **Pin image ref: `ghcr.io/daniel-ospina/tortoise-selfhost:${GITHUB_REF_NAME}` (single string referenced by README T5.1 + compose T4.3).** Model on `Dockerfile.hosted`/`entrypoint.sh` (build) + `python-ci.yml` job structure — `deploy-hosted.yml` is pure flyctl deploy and has no scaffold; `python-ci.yml` is an agent-infra symlink → read-only reference, create this workflow fresh.
- **T4.3 `docker-compose.yml`** (root) — daemon + `falkordb` sidecar (AOF on, named volume, healthcheck) = documented durable path. **Do NOT bundle FalkorDB inside the Tortoise image** (official image SSPLv1 — license interaction + incident history).

### Phase 5 — Docs convergence (D4) + graph (D7) — depends on G3 + G4
**GATE G5:** all acceptance criteria green; code-review gate (commit-workflow) passes.

- **T5.1 `README.md` service-first rewrite** — Install → Connect → Query (MongoDB Atlas pattern); **hosted AND self-host are BOTH first-class quickstart paths — no "coming soon" debt, no pre-launch coordination check. Owner direction (2026-08-07): hosted is being built now (epic #235 + #518/#519/#292) and Tortoise has ZERO external users pre-launch — there is no one to hit a dead end, so both paths ship fully and launch together.** Include an env-var table (`TORTOISE_DB_URI`/`TORTOISE_DB_PATH`, `TORTOISE_API_KEY` footgun, `TORTOISE_HOST/PORT/RATE_LIMIT` = 100 req/min per IP) + operator-tools note (HTTP-hidden, stdio-only) as the daemon's doc home: hosted signup (free tier) OR `docker run`/`docker compose`; `claude mcp add tortoise http://localhost:8000/mcp` / `codex mcp add` / `.mcp.json` snippet; query = MCP tools + REST (#525); `pip install` demoted to "SDK for local dev/scripting"; **License/FAQ block** (D3 content: BSL + $5M AUG self-host; hosted = commercial outside grant; MIT products connect via MCP/REST and never inherit terms; Mozilla Public License 2.0 (MPL-2.0) conversion in 4 years; BSL-threshold + enterprise-blocklist framing per carried-forward risks).
- **T5.2 `index.md`** — service-first ordering (MCP server → connectors → SDK) + license statement; `docs/00_index.md` routing updated if present.
- **T5.3 License-surface CI wiring (moved from T3.3):** enable `check-license-surface` in CI here — only after T5.1/T5.2 converge all four files (README License/FAQ + index.md license line land in Phase 5). Wiring it at T3.3 would leave CI red from Phase 3 to 5 (README/index still BSL-only/AGPL until T5.2), contradicting the parallel-track design.
- **T5.3b Docs behavioral grep** — no README/docs path instructs importing tortoise into a consumer distribution.
- **T5.4 D7 graph supersede** — via `@how-to-use-tortoise`: file BSL 1.1 + $5M AUG decision (context `licensing-decision-compare`), supersede DEC-002 AGPLv3-dual; requires FalkorDB up (else document + tracked follow-up).
- **T5.5 Release mechanics** — bump `pyproject.toml` version (currently 0.1.0), add CHANGELOG entry for the service release, THEN tag `v*` (image tags track the package version).

---

## 5. Integration Surface Map (test-design, per surface)

| # | Surface | Type | Data Flow | Test Layer | Contract | Key Failure Modes |
|---|---------|------|-----------|-----------|----------|-------------------|
| 1 | `create_http_app` middleware stack (shared hosted/self-host path) | Auth boundary + state | Guard | Integration (ASGI) | auth_mode default "tenant" = hosted byte-identical | 401/403/503 paths; mode-branch leakage (tenant middleware in static mode) |
| 2 | Daemon ↔ FalkorDB (URI) / FalkorDBLite (path) | DB | Both | Integration | `TORTOISE_DB_URI` vs `TORTOISE_DB_PATH` resolution | DB down → `/health/ready` 503 not 500; embedded eval durability caveat |
| 3 | mcp_client ↔ daemon MCP endpoint | External API (internal) | Out | Integration | Streamable HTTP JSON-RPC (initialize → tools/list → tools/call); tools from #510 | daemon down (conn refused); timeout; wrong URL; 401 (bad key) → graceful degradation |
| 4 | bridge.py ↔ mcp_client | State mutation | Out | Integration (E2E) | content_hash idempotency preserved; push result shape | daemon unavailable → skip status (existing pattern); duplicate push = no dup points |
| 5 | `.mcp.json` ↔ daemon | Config | Out | E2E (manual dogfood) | `http://localhost:8000/mcp` reachable; no absolute paths | stale cwd entries; dev pointing at dead endpoint |
| 6 | GHCR image build → run → health → connect | External infra | Out | E2E (CI smoke) | image boots embedded eval; `/health` 200 | embeddings cache missing (hosted fix #160-followup pattern); entrypoint exit 1 |
| 7 | License surface (4 files) | Config | Guard | Config (script) | BUSL-1.1 + $5M AUG + MPL-2.0 in LICENSE/README/pyproject/index.md | tri-state regression (root cause: graph unsynced) |

### Bug Pattern Flags
- **Conditional guards** (auth_mode branches): boundary tests for all three modes; both sides of every branch (flag: tenant middleware leaking into static/none).
- **Silent function skips** (bridge): verify push reaches the real MCP call — no hardcoded fallback/early return (existing `tortoise_available()` pattern must become a real connectivity check, not a silent pass).
- **Race/process** (subprocess smoke): embedded DB process leaks (prior `redislite-process-leak` plan exists in this dir) — subprocess tests must terminate uvicorn cleanly.
- **N+1 / duplicates**: bridge idempotency re-test after conversion (content_hash path must survive the SDK→MCP swap).

## 6. Testing Strategy (AGENTS.md)

- **Unit/integration:** pytest, embedded FalkorDBLite (`tests/conftest.py` pattern) — `python -m pytest tests/ -v`.
- **Daemon:** ASGI-level (httpx `ASGITransport`, no socket) + one subprocess smoke (real HTTP, ephemeral port, clean teardown).
- **Hosted regression:** entire existing suite must pass unchanged — proves auth_mode default preserves hosted behavior.
- **Connector:** in-process daemon fixture + bridge E2E; static AST no-import check.
- **Image smoke:** in `publish-selfhost.yml` (build → run → `/health` → MCP `initialize` handshake) — CI job, not pytest.
- **License:** `validation/check-license-surface.py` (repo-local; `scripts/` is an agent-infra symlink) invoked by repo-local `.ci-checks/check-license-surface.sh`, wired into `.github/workflows/ci.yml` at T5.3 (NOT `python-ci.yml` — agent-infra template symlink).

> **Owner overrides (2026-08-07, after plan review):** (1) the "hosted = coming soon if #338 ships first" mitigation (Qwen P1-001 fix) is REVERSED — hosted is a first-class path in T5.1, being built now (epic #235 + #518/#519/#292), and must not be disabled in docs. (2) The pre-merge delivery-coordination check is DROPPED — Tortoise has zero external users pre-launch, so no dead-end risk exists; both options ship fully and launch together. **Priority: SAP — full D1–D7 scope (both options working), then launch.**

## 7. Verification Plan (test-routing: code domain, standard)

- **Architecture=medium → unit + integration** for DB/auth surfaces (map rows 1–2, 4–5) — integration-weighted, per test-design trophy.
- **Critical path e2e** = daemon boot → health → MCP connect at both subprocess (Phase 1) and image (Phase 4) level.
- **Config domain → config-validation:** license-surface check script + CI workflow validation.
- **No UX/content/research surfaces** (no UI; no editorial content; research already verified CLEAN in scope).
- **Human gates:** G3 (owner/legal license approval), G5 (commit-workflow code-review, mandatory per AGENTS.md).

## 8. Acceptance Criteria (behavioral, from scope)

1. **AC1** `docker run`/`docker compose` quickstart yields a working MCP endpoint: `/health` 200 + MCP `initialize` handshake + `tools/list`.
2. **AC2** No README/docs path instructs a consumer to import tortoise into their distribution (grep gate).
3. **AC3** LICENSE/README/pyproject/index.md all declare BSL 1.1 + $5M AUG + Mozilla Public License 2.0 (MPL-2.0) conversion (four-file consistency, CI-enforced).
4. **AC4** `bridge.py` connects via MCP — zero `from tortoise.sdk import` in `integrations/`.
5. **AC5** `.mcp.json` points at the self-host daemon (dev) or hosted; no hardcoded absolute paths.
6. **AC6** Hosted platform unchanged: existing suite green; `auth_mode` default = tenant.
7. **AC7** MIT products integrate without importing — demonstrated by the bridge conversion (real consumer).
8. **AC8** `tortoise-serve http` documented/extended.
9. **AC9** GHCR publish on tag; compose reference boots with falkordb sidecar.
10. **AC10** D7: graph supersedes DEC-002 with the BSL decision (or tracked follow-up documented).

## 9. Runtime Prerequisites

- Dev/eval: Python 3.11+, `pip install -e .` (embeddings extra for vector/dream); embedded FalkorDBLite (no Docker) — durability caveat + backup procedure documented.
- Durable self-host: `docker compose` (falkordb, AOF on, volume) or `TORTOISE_DB_URI` → FalkorDB (self-managed or Cloud).
- Image publish: GHCR enabled on repo, `packages: write` on GITHUB_TOKEN; no Fly required for self-host.
- D7: FalkorDB reachable (else defer with documented follow-up).
- Env vars: `TORTOISE_DB_URI` / `TORTOISE_DB_PATH`, `TORTOISE_API_KEY` (optional, LAN), `TORTOISE_HOST/PORT/RATE_LIMIT`.

## 10. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| `auth_mode` touches shared MCP path (hosted outage) | Additive param, default = tenant byte-identical; full hosted suite is G1 gate; param reviewed in code-review gate |
| CLA audit stalls (human dep) | Phase 0 (earliest); single human author (~508 commits, 1 case-variant string to reconcile); audit documented in license-notes.md; doesn't block Track 1 |
| BSL threshold perception (Akka $25M backlash) | FAQ + conversion-timer messaging in README (D4); 4-year change date |
| Enterprise blocklists treat BSL like SSPL/AGPL | Network-boundary framing in License FAQ ("boundary, not license"); connect-via-MCP = no inheritance |
| FalkorDBLite durability (2026-08-05: 5,748 points lost) | Eval mode flagged + backup procedure; compose path AOF-on; no FalkorDB inside Tortoise image (SSPLv1) |
| mcp_client scope-creeps into #526 engine split | Keep it a client only (~100 lines); #526 explicitly deferred |
| Embeddings cache breaks image boot (hosted fix #160-followup) | Copy hosted Dockerfile pattern verbatim (build-time download + path check); smoke job catches it |
| `.mcp.json` stdio removal breaks dev workflows | Entry updated to daemon; `python -m tortoise.mcp_server` stdio still exists for scripting (#478 ambiguity noted in docs) |
| D7 blocked (FalkorDB down at closeout) | Documented tracked follow-up; license-consistency CI check is the backstop (root-cause fix) |

## 11. Handoff

Save → run `plan-review` (mandatory, Standard tier) → apply `planned` label → execute via `executing-plans` (TDD per task, commit-workflow gates). Order-of-magnitude estimate: ~5–7 working days total (parallel tracks).
