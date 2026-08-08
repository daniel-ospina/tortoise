# Issue Scoping — #338: Migrate Tortoise from Library to Service Model

**Date:** 2026-08-07
**Status:** in progress (double diamond, problem-verify cycle 2)
**Tier:** Standard
**Branch:** feat/338-service-model (PR #517 — research merged into this branch)
**Primary evidence:** `docs/research/2026-08-07-338-service-model.md` (verified CLEAN, 3 review cycles)

---

## 1. Confirmed Problem

Tortoise is **library-first today, and the gap is distribution + license boundary, not just copy**:

1. **The pip package ships the ENTIRE engine.** `pyproject.toml` packages `tortoise*` wholesale; `sdk.py` is 3,593 lines wrapping FalkorDB directly. Any adopter who `pip install tortoise` embeds the full engine (and its license) inside their own distribution.
2. **The license boundary blocks MIT products.** README declares BSL 1.1, LICENSE is AGPL-3.0, pyproject declares MIT, index.md says "AGPLv3 + CLA" — a four-file, three-license tri-state. Whichever license governs, importing it into an MIT-licensed product (David Waring's) is a blocker. The license boundary sits inside the adopter's distribution.
3. **Nothing is installable as a service.** No GHCR workflow, zero Docker mentions in README, quickstart leads with `pip install -e .`, and the only infra doc (`docs/infra-runbook.md`) is the **hosted-platform operator runbook** (Fly.io + FalkorDB Cloud + Cloudflare) — there is **no adopter-facing self-host guide at all**.
4. **Integrations embed engine access.** `integrations/crm/twenty/bridge.py` does `from tortoise.sdk import TortoiseSDK` directly; the integrations README diagram shows `bridge.py → Tortoise/FalkorDB`.

**Owner decision (2026-08-07, fixed input):** MongoDB-style service model. Tortoise runs as a daemon (self-hosted locally or hosted on api.premiselabs.co); clients **connect** via MCP/REST — they never import the engine. Dual offering, both first-class: **self-hosted** (BSL 1.1 + $5M USD revenue-threshold AUG, Mozilla Public License 2.0 (MPL-2.0) conversion after 4 years) and **hosted** (commercial subscription with free tier, NOT covered by the BSL grant, anti-resale clause). SDK kept importable as a demoted local-dev/scripting layer.

### Rejected alternative framings (documented per skill)

| Framing | Verdict | Rationale |
|---|---|---|
| **A. "License-boundary architecture problem" → engine/client package split** | Rejected for #338, filed as follow-up **#526** | Physically correct (pip still ships engine under BSL post-#338), but the acceptance criterion "MIT products integrate without importing" is satisfiable behaviorally — they connect via MCP/REST and never pip-install. Split is the deferred second half; #338 is the first half. |
| **B. "Distribution problem only" (docs/rebrand, no packaging)** | Rejected | No installable service = the mongod analogy fails on day one and the trust/self-host story is fiction. Packaging (Docker image) is IN scope. |
| **C. "Misdiagnosis — hosted exists with zero users, gap is onboarding" (Agent B)** | Rejected with rationale | Evidence for the counter-framing is real (open P1s: **#518** no public tenant-provisioning journey, **#519** dashboard is a non-functional shell, **#292** API 500 regression) — hosted is NOT yet provisionable by external users. But the owner decision fixes the service model, and the self-host absence + license tri-state are independently verified. Correct sequencing: #338 (service model + self-host + license), onboarding/provisioning tracked via **#518/#519/#292** and epic **#235**. |
| **D. "Interface-curation problem" (58 MCP tools past performance cliff)** | Rejected for #338, filed as **#523** | Real and evidence-backed (tool-selection accuracy degrades past ~20 tools), but it is continuous product work on an already-canonical registry (tool_registry.py #510) — not a blocker for the repositioning itself. |
| **E. "Embedded/in-process library wins" (Mem0, LangMem, DuckDB, SQLite)** | Rejected | The owner has decided the service model (trust + architecture + licensing). Noted as the strongest market counter-thesis; the EP differentiator (belief propagation on a shared graph) argues for a server-shared memory store over per-process embedding. |

**Falsification check:** The confirmed problem is wrong if — despite installable self-host, service-first docs, and consistent BSL+AUG — MIT-licensed products still decline integration AND solo-dev adoption stays at zero for reasons attributable to the *interface* (not distribution/licensing). Pre-registered: measure adoption of `docker run`/compose quickstart + hosted free tier signups post-ship.

**Confidence: 85/100** (owner decision + verified codebase claims; residual risk = adoption assumptions, which are unverified by definition).

---

## 2. Scope

### In scope (#338)

| # | Deliverable | Key decisions |
|---|---|---|
| **D1** | **Self-host daemon** — new thin FastAPI entrypoint (e.g., `tortoise.selfhost:app`) reusing `mcp_server.create_http_app()` (MCP Streamable HTTP) + `/health`; **NO** Supabase provisioning (hosted-only). `tortoise-serve` (deployment.serve, stdio) documented or extended for HTTP. | **Entrypoint pinned:** MCP-HTTP-first daemon, single-tenant. **Auth:** localhost bind by default, optional `TORTOISE_API_KEY` for LAN. **DB topology (incident-informed):** (a) single `docker run` = embedded FalkorDBLite for eval/dev — with documented durability caveats + backup procedure (per 2026-08-05 data-loss incident (#101): AOF-off + no automated backups + empty-state RDB re-save failed to prevent the wipe (5,748 points lost; root causes #99/#100/#101); self-host falkordb sidecar removed; FalkorDB Cloud is the only production DB); (b) **recommended durable path = `docker compose`**: falkordb (AOF on, backups) + tortoise daemon. **Do NOT bundle FalkorDB inside the Tortoise image** (its official image is SSPLv1 — license interaction + incident history). *(Incident context: the HOSTED platform's self-managed falkordb sidecar was removed post-#101; FalkorDB Cloud is now the only production DB for hosted. For self-host, the recommended durable path IS a compose FalkorDB sidecar — AOF on, named volume.)* |
| **D2** | **Docker image publish** — GHCR workflow (build on tag/branch → publish → smoke test: build → run → `/health` → MCP `tools/connect`), modeled on `deploy-hosted.yml`. Two artifacts: `tortoise` daemon image; `docker-compose.yml` reference. | Image = daemon only (D1). Compose = daemon + falkordb sidecar. |
| **D3** | **License: BSL 1.1 LICENSE file** drafted by borrowing clause language from precedent (SPDX BUSL-1.1 canonical text, Couchbase AUG, MariaDB MaxScale quantitative threshold, CockroachDB BSL, **Sentry FSL $5M grant language**, HashiCorp parameter block, Redis/Elastic Mozilla Public License 2.0 (MPL-2.0) conversion). Parameters: Licensor = Premise Labs / Daniel Ospina; Licensed Work = Tortoise; AUG = $5M USD revenue threshold (trailing 12mo) + anti-resale clause + hosted-excluded; Change Date = 4 years → Apache 2.0. Fix the tri-state across **all four files**: `LICENSE`, `README.md` License section, `pyproject.toml` (`license` field + classifiers), `index.md`. Add short License/FAQ block to README: BSL + $5M AUG (self-host), hosted = commercial w/ free tier (outside grant), MIT products connect via MCP/REST and never inherit terms, Mozilla Public License 2.0 (MPL-2.0) conversion in 4 years. | **Precedent-borrowing mandate (owner):** clause → precedent mapping table in plan (deliverable of this scoping → writing-plans). **Dependency:** contributor/CLA audit before drafting (LICENSE currently AGPL with CLA note — verify copyright-cleanliness of historical contributions; single holder Daniel Ospina reduces risk). |
| **D4** | **README + docs service-first rewrite** — "Install → Connect → Query" (MongoDB Atlas pattern, onboarding epic #235 research). Quickstart: **hosted signup (free tier) AND `docker run`/`docker compose` self-host — BOTH first-class** (owner direction: hosted is being built now via epic #235/#518/#519/#292; docs must NOT mark it "coming soon"); connect via `claude mcp add tortoise https://api.premiselabs.co/mcp` / `codex mcp add` / `.mcp.json` snippet; query = MCP tools (58) + REST (as #525 lands). `pip install` demoted to "SDK for local dev/scripting". | **Acceptance (behavioral):** no README/docs path instructs importing tortoise into a consumer distribution. **root `index.md`** (canonical index — also a license surface) updated to service-first ordering (MCP server → connectors → SDK); `docs/00_index.md` referenced if present (AGENTS.md routing index). **No pre-merge coordination check (owner 2026-08-07):** zero external users pre-launch — no dead-end risk; both paths ship fully and launch together. |
| **D5** | **Thin connectors** — `integrations/crm/twenty/bridge.py` converted from direct SDK access to **MCP** (pinned surface — REST is partial; full REST = #525). Consume `tool_registry.py` ToolDefinitions/RestSpec (#510, canonical). integrations README diagram updated to `→ Tortoise (MCP)`. | Connector gains config/connect-failure paths → add tests per AGENTS.md (pytest, embedded FalkorDBLite). |
| **D6** | **`.mcp.json` sync** — committed config currently points at stdio `python3 -m tortoise.mcp_server` with hardcoded absolute PYTHONPATH + `docker://localhost:16379`. Update to the self-host daemon (`http://localhost:8000/mcp`) or hosted endpoint; mark dev-local explicitly (related **#478** ambiguity). | Keeps repo dogfooding aligned with the quickstart. |
| **D7** | **Graph decision supersede** — file the BSL 1.1 + $5M AUG licensing decision to the Tortoise graph (context `licensing-decision-compare`, superseding DEC-002 AGPLv3-dual) via how-to-use-tortoise skill, when FalkorDB is up. | Prevents the next license drift — the tri-state exists because the graph decision was never synced to files. |

### Explicitly out of scope (filed separately — no silent absorption)

| Exclusion | Tracked as |
|---|---|
| Engine/client package split (pip = thin driver) | **#526** |
| MCP tool-surface curation (58 → role-scoped ≤20) | **#523** |
| OAuth 2.1 + DCR for remote MCP | **#524** |
| REST API completeness (from tool_registry RestSpec) | **#525** (legacy #7717 CLOSED in eldato — superseded) |
| Hosted provisioning/onboarding/dashboard | **#518 #519 #292** + epic **#235** |
| Pricing/billing infra | product planning (not yet an issue) |
| FalkorDB upstream risk (fork of EOL'd RedisGraph, SSPLv1 image) | Named dependency risk — surfaced here, owned by D1/D2 decisions |

### Carried-forward risks (from problem-diverge, not dropped)

1. **Solo-dev ops burden** — two-process durable path vs one-command eval path; quickstart must be genuinely one-command. Mitigation: embedded eval mode + compose for durable.
2. **BSL threshold perception** (Akka $25M backlash precedent) — FAQ + conversion-timer messaging in README (D3/D4).
3. **Enterprise blocklists treat BSL like SSPL/AGPL** — the network-boundary framing ("boundary, not license") is the counter; documented in License FAQ.
4. **Internal SDK consumers** (skills, graph-scripts, tests, .mcp.json) — SDK stays importable, so they keep working; D6 aligns .mcp.json; engine split (#526) is the eventual migration.
5. **Hosted cannibalization** — self-host free under $5M vs hosted free tier; differentiated managed value (backups/SSO/scaling) needed eventually — noted for product planning.

---

## 3. Problem-Verify Gate (cycle 2 — after controller fixes)

Cycle 1 found 3 P1s (self-host daemon shape unspecified; exclusions unfiled; misdiagnosis rejection undocumented). Controller fixes applied: D1 decision block (entrypoint/auth/DB-topology pinned); #523/#524/#525/#526 filed; rejected framings + falsification + confidence documented above. Re-verifying.

---

## 4. Solution Diamond + Verification Gate Summaries

**Chosen approach: A-hybrid "Daemon-first, consumer-validated, license-in-parallel"** (full plan: `docs/plans/2026-08-07-338-service-model-plan.md`):
- Thin `tortoise/selfhost.py` daemon reusing `create_http_app()` with additive `auth_mode` param ("tenant" default keeps hosted byte-identical; static/none omit TeamResolutionMiddleware) — NO Supabase, NO hosted machinery in the self-host image.
- Consumer-first: `mcp_client.py` (fastmcp 3.4.6 built-in client — zero new deps) + twenty bridge conversion proves "connect, don't import" before the image ships.
- License as parallel track (P3): BSL 1.1 LICENSE from precedent (SPDX BUSL-1.1, Couchbase, MariaDB MaxScale, CockroachDB, Sentry FSL $5M, HashiCorp, Redis/Elastic Mozilla Public License 2.0 (MPL-2.0) conversion) + `docs/license-notes.md` provenance split.
- P4: GHCR image (`ghcr.io/daniel-ospina/tortoise-selfhost`) + compose (falkordb AOF-on sidecar, no SSPL bundling). P5: README/index docs convergence + graph supersede.

**Rejected alternatives:** B (one-app factory — multi-tenant machinery + production refactor risk), C wholesale (contract-first license-last — owner blocker deferred), A as-stated (docs-first delays installable service). All with "when it would have been better" criteria in the plan §1.

### Verification Gates
- **problem-verify: 2 cycles** — Cycle 1: 3 P1s (self-host daemon shape unspecified; exclusions unfiled; misdiagnosis rejection undocumented) → fixed (D1 decision block pinned; #523/#524/#525/#526 filed; rejected-framings table + falsification + confidence 85/100). Cycle 2: clean (no P0/P1; P2/P3 incorporated: docs/index.md→root index.md, incident phrasing).
- **solution-verify: 2 cycles** — Cycle 1: 1 P1 (merge-main must be explicit P0.2 action — branch 12 commits behind, #510/#516 missing) + 5 P2 + 5 P3 → fixed (P0.2 ACTION; lifespan/origins/CLA/CI-wiring P2s; all P3s). Cycle 2: clean (no P0/P1; 2 new P2s + P3s incorporated: scripts-symlink→validation/, symbol citations, tenant-import guard, release mechanics, image ref, env table, drift note).
- **Qwen coherence (deepseek-v4-pro): 2 cycles** — Cycle 1: 2 P1s (hosted signup dead-end gated on #518/#235; conditional-import spike) + 3 P2s → fixed (self-host primary quickstart; P0.4 import spike + structural fallback; rate-limit unit; CLI contract; sidecar phrasing). Cycle 2: 1 P2 (T3.3/T5.3 CI-wiring wording) → fixed. **Coherence: clean.** ⚠️ *Post-review owner overrides (2026-08-07): (1) "hosted = coming soon" fix REVERSED — hosted built now (epic #235 + #518/#519/#292), docs treat it as first-class, no "coming soon" debt. (2) Pre-merge coordination check DROPPED — zero external users pre-launch, no dead-end risk; both options ship fully and launch together. Priority: SAP.*

### Wiring Check
| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| create_http_app middleware stack (shared hosted/self-host) | Auth boundary | T1.1 auth_mode + G1 hosted suite + integration map row 1 | ✅ |
| Daemon ↔ FalkorDB / FalkorDBLite | DB | T1.2 env resolution, /health/ready 503-not-500, T4.3 compose AOF-on | ✅ |
| mcp_client ↔ daemon MCP endpoint | External API | T2.1 + integration map row 3 (initialize→tools/list→tools/call, #510 contract) | ✅ |
| bridge.py ↔ mcp_client | State mutation | T2.2 + idempotency re-test + AST no-import gate | ✅ |
| .mcp.json ↔ daemon | Config | T2.3 (daemon URL, no absolute paths) + #478 note | ✅ |
| GHCR image build → run → health → connect | External infra | T4.1/T4.2 + smoke job (G4) | ✅ |
| License surface (LICENSE/README/pyproject/index.md) | Config | T3.1/T3.2 + validation/check-license-surface.py + ci.yml at T5.3 | ✅ |
| README/docs quickstart paths | Docs | T5.1 (hosted + self-host both first-class, ship fully + launch together) + T5.3b grep gate | ✅ |
| Graph licensing decision | Cross-cutting | T5.4 (supersede DEC-002, context licensing-decision-compare, FalkorDB-down fallback) | ✅ |
| Release mechanics | Cross-cutting | T5.5 (version bump + CHANGELOG + tag v*) | ✅ |

### Complexity
| Domain | Rating | Notes |
|---|---|---|
| Architecture | medium | Auth-mode param + daemon + image; no engine split (deferred #526) |
| UX | low | Docs rewrite + quickstart; no UI component work |
| Ontology | none | No entity/edge changes |
| Total | **standard** | Per issue; ~5–7 working days, parallel tracks |

