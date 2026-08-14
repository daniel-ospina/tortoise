---
title: "<!-- issue-scoping: v5.1 double diamond + verify -->"
type: decisions
domain: capability
doc_status: live
created: 2026-08-13
ownedBy: epistemic-team
---

<!-- issue-scoping: v5.1 double diamond + verify -->
# Scoping — #318: Multi-tenant Pack Isolation (hosted)

**Issue:** daniel-ospina/tortoise#318 — "feat: hosted architecture — multi-tenant pack isolation, graph-per-customer provisioning" (RE-SCOPED 2026-08-13)
**Tier:** standard · **Epic:** #7618 (eldato, closed — Tortoise Ontology Expansion Pack Architecture)
**Date:** 2026-08-13 · **Skill:** issue-scoping v5.1 (streamlined mode; diamond phases inline, verification gates via fresh-context verifier sub-agents)

## Confirmed Problem

Tortoise hosted has **no per-tenant pack state**. Pack vocabulary resolves from process-global singletons
(`domain_loader._registry`, `sdk._get_kind_expander()`), and tenant provisioning (`provision_team` RPC /
`/internal/provision`) creates only a FalkorDB graph — no pack activation, no pack record, no pack access
surface. The slice's real work:

1. **Tenant-scoped pack model with automated starter-pack activation at signup** — idempotent, works in
   BOTH control-plane modes (Supabase `provision_team` RPC — production; registry-mode `/internal/provision` —
   selfhost), replacing a "manual" process that is in fact **absent** (no script exists in the repo).
2. **A pack introspection surface whose cross-tenant access returns empty/403** — today there is NO pack
   access API, so the O/I/T "cross-tenant pack access returns empty/403" test cannot be written against
   anything. A minimal read-only surface (hosted REST + MCP tool) is required for the automated isolation test.
3. **Enterprise pack governance hooks (kind lifecycle, schema versioning) — EXPLICITLY DEFERRED.**
   Phantom work: `product/pricing.json` tiers are `free|solo|pro|team` (no enterprise tier) and pack manifest
   `tier` validation accepts only `free|premium` (`pack_registry.py:734-735`). Prior adversarial research
   (2026-08-13 research-brief): "governance hooks without an enterprise tier are phantom work."

### Why This Framing (evidence)

- **Root cause, not symptom:** the issue frames "pack isolation" as a copy/visibility problem; the codebase
  shows there is no per-tenant pack state at all — the root cause is the **absence of a tenant-scoped pack
  model + resolution seam**, and a latent process-global singleton leak (`domain_loader._registry`,
  `sdk._get_kind_expander` are module-level caches — the exact async-context-loss/singleton-leak failure
  class the prior research flagged from Autonoma's multi-tenancy testing).
- **Shared catalog + install-state is the canonical pattern** (AWS SaaS Lens; Crystallize Plugin Registry;
  Stella Ops `plugin_instances`; Paperclip `plugins`+`plugin_config`; LaunchMyStore `AppInstallation`;
  Shopify; Salesforce metadata overlay; VS Code Marketplace). "Copies per tenant" is an anti-pattern
  (Kong install-state parity; Spree "no per-tenant installation"; SchemaSmith "no separate provisioning
  script pipeline"). The issue's indicator "copies starter packs" should be re-interpreted as **activation
  records (install-state)**, not file copies.
- **The tenant-context seam already exists:** `tortoise/mcp_auth.py` ships `_current_team_id` /
  `_current_team_limits` ContextVars set per-request by `TeamResolutionMiddleware`, and `_get_team_sdk()`
  → `TortoiseSDK(namespace=team_id)`. Per-request tenant context is stdlib `contextvars` (PEP 567) —
  no new dependency; in-repo precedent.
- **Provisioning is dual-mode** (`supabase_control.py:82-92`): `TORTOISE_CONTROL_PLANE=supabase` →
  atomic `provision_team` RPC (migration 0010, production); `=registry` → `/internal/provision`
  (`hosted_api.py:546-697`). Any pack-activation hook must land in both (or in the shared graph creation,
  which both modes execute).

### Rejected Alternative Framings

- **F1 — Per-tenant packs_dir copies (original framing):** copy `packs/` into per-tenant dirs,
  `PackRegistry(packs_dir)` per tenant. Rejected: (a) copy drift — upstream pack updates never reach
  tenants (Kong install-state parity problem, Spree "no per-tenant installation"); (b) consumers are
  global singletons, so per-tenant dirs would still need the same contextvars rework; (c) storage
  duplication; (d) contradicts the competitor consensus. *When it WOULD have been better:* a paid silo
  tier (FedRAMP/HIPAA residency) requiring physical vocabulary isolation — not this slice.
- **F4 — Do-nothing (pack isolation is premature):** starter packs are shared-by-design; no tenant data
  in packs; nothing breaks today. Accepted for its challenge on enterprise governance (deferred) but
  rejected as a whole: provisioning automation + a tenant-scoped state model ARE real, shippable value,
  and the issue's O/I/T (automated provisioning + isolation test) is implementable.
- **F2 — Shared catalog + install-state** was NOT rejected — it is the chosen definition (above).

### Falsification Check

The confirmed definition is wrong if any of: (a) hosted provisioning already activates per-tenant packs
(codebase: no pack code in `hosted_api.py`/`supabase_control.py` — verified absent); (b) pack kind
resolution is already tenant-scoped (`domain_loader._registry` and `sdk._get_kind_expander` are global —
verified); (c) an enterprise tier exists (`pricing.json` + manifest tier validation — verified absent).
None hold → framing stands.

### Revised O/I/T (reconciled with confirmed problem — do NOT measure against the stale original body)

- **Objective (unchanged):** Hosted customers get isolated packs — tenant A cannot see tenant B's packs;
  starter packs provisioned per customer.
- **Indicator 1 (re-based):** Customer signup activates starter packs automatically (idempotent install-state
  records, both control-plane modes). "Copies" is superseded by activation — file copies are the rejected F1.
  (The issue's "currently manual script" premise is false — no script exists; verified by repo-wide grep for
  pack/seed/provision scripts in `graph-scripts/`, `scripts/`, `tortoise/`.)
- **Indicator 2 (re-based):** Shared catalog + per-tenant pack install-state; a new read-only pack
  introspection surface (REST + MCP) returns empty for cross-tenant access (anti-enumeration — see
  404-vs-403 note). "pack_registry uses per-tenant packs_dir" is superseded — per-tenant dirs are the
  rejected F1 mechanism.
- **Indicator 3 (deferred):** Enterprise governance hooks (kind lifecycle, schema versioning) deferred until
  an enterprise tier exists (pricing/GTM decision; phantom work today).
- **Targets (unchanged):** Cross-tenant isolation verified by automated access test; provisioning automated.

### Confidence: 80/100

Strong codebase evidence + prior-research consensus. Residual 20% = product/GTM uncertainty (pack tier
modeling, starter-pack default-set vs selectable catalog) and whether the deferred custom-pack authoring
path changes the isolation model — a human/GTM decision, surfaced in Clarifications. Would move to 90+ if
GTM confirms starter packs are a default set for all tenants.

---

## Verification Gates

### problem-verify: 1 cycle, PASS (0 P0, 0 P1; P2×4 incorporated) | 0 issues remain
### solution-verify: 1 cycle, PASS (0 P0, 0 P1; P2×2 + P3×5 incorporated) | 0 issues remain
### coherence (Phase 5.6): [QWEN-GATE] substitute reviewer used (qwen3.8-max blocked 401) — 1 cycle, P1×2 FIXED, no re-run (per constraint: fix once, document)

**coherence — Cycle 1 (substitute reviewer):**
- Findings: P0=0, P1=2, P2=2, P3=2, P4=2
- [QWEN-GATE] P1-1 (self-heal masks eager-path defects; never-activated semantics ambiguous): FIXED —
  AC1 now asserts direct-graph PackInstall nodes post-provision pre-GET; test-only
  `PACK_STATE_DISABLE_SELF_HEAL=1` flag; never-activated = first-read self-heal with REST/MCP parity
  asserted on the HEALED result (section 4).
- [QWEN-GATE] P1-2 (cross-tenant request shape undefined): FIXED — scoping model PINNED to auth-only
  (no tenant selector); anti-enumeration reworded to same-tenant no-installs → empty + two-token
  no-bleed test (section 4 + AC2).
- P2-1 (run_in_executor context loss): FIXED — pinned asyncio.to_thread-only + threadpool regression
  test (section 4). P2-2 (3 sites unenumerated): FIXED — enumerated with mode mapping (section 3).
  P3-1 (401-on-None + response matrix): FIXED (section 4). P3-2 (additive-only removal semantics):
  FIXED (section 6). P4s: recorded (tenant-teardown invariant; rejected variants: Postgres table /
  pure env-derived / MCP-as-HTTP-client).

**solution-verify — Cycle 1:**
- Verifier A: P0=0, P1=0, P2=2, P3=3, P4=1
- Verifier B: P0=0, P1=0, P2=0, P3=2, P4=3
- Controller action: gate PASSES on P2+ only. Incorporated: P2-A1 concurrency model (MERGE atomicity
  statement + concurrency test + partial-failure convergence test + FalkorDB-server semantics note);
  P2-A2 env validation (unknown names skip+warn, empty→default, read-at-call-time contract, tests);
  P3s: REST/MCP ensure-then-read symmetry + AC3 never-activated scenario; read-path failure semantics
  (503-on-outage, partial-return, team_id-None fail-closed — no default-namespace fallback); explicit
  Risks-of-A block; validation/ label check (RESOLVED: no graph-label constraints — EP/license
  validators only); direct backfill partial-install test; graph-deletion behavior recorded. No
  re-dispatch (no P0/P1).

**problem-verify — Cycle 1:**
- Verifier A: P0=0, P1=0, P2=2, P3=4, P4=1
- Verifier B: P0=0, P1=0, P2=2, P3=4, P4=2
- Controller action: gate PASSES on P2+ only. Incorporated: P2-A1 revised-O/I/T block (done above);
  P2-A2 existing-tenant backfill (added to plan — idempotent re-run of activation); P2-B1 REST tenant
  identity binding (VERIFIED resolved: `get_current_team` FastAPI dependency, hosted_api.py:820, `Depends`
  on REST endpoints — the new surface's auth is the existing dependency); P2-B2 singleton-consumer blast
  radius (scope note added: THIS slice keeps vocabulary global — only the introspection/enforcement
  surface is tenant-scoped; per-consumer tenant-scoping deferred to the custom-pack slice). P3/P4 all
  incorporated (404-vs-403 note, lifecycle boundary, A2 tag fix, F4-value honesty, confidence residual,
  UX justification, source URLs, falsification method). No re-dispatch (no P0/P1).

---

## Clarifications (for human)

1. **Pack tier model / GTM:** are starter packs the *default set for all tenants* (activate-on-signup,
   no tenant choice) or a *selectable catalog*? This determines whether provisioning needs a per-tenant
   choice surface now (recommendation: default-set now; selection UI is a later slice).
2. **"Copies starter packs" semantics:** activation records (install-state) are recommended over file
   copies (drift). Confirm acceptance — the O/I/T wording "copies" should be read as "activates".
3. **Enterprise governance deferral:** confirm deferral until an enterprise tier exists (pricing/GTM
   decision, not engineering). The pack manifest `tier: enterprise` is currently a validation error.
4. **Pack introspection surface scope:** adding a minimal read-only packs endpoint + MCP tool is
   required to make the isolation test meaningful. Confirm this API surface is in scope (it is small).
5. **Existing-tenant backfill:** if hosted already has tenants, the idempotent activation routine is
   re-run per existing team (operator script). Confirm acceptable.
6. **Existence-masking semantics:** cross-tenant pack queries return **empty/404** (mask existence,
   Digitorn precedent) rather than 403; 403 on an anonymous probe would leak that a tenant/pack exists.
   The O/I/T says "empty/403" — recommend empty (no error) as the default posture.

---

## External Research (Phase 1.5 artifact)

### Axis Research

**Axis ratings (derived — issue body has no Complexity Rating section):** Architecture=high,
Ontology=low, UX=low, Library-deps=none-new.

- **Architecture (high)** — deduplicated against PRIOR_RESEARCH (epic research-brief
  `docs/epics/2026-08-03-tortoise-hosted-platform/research-brief.md`, entries 2026-08-13T15:10:00Z +
  15:35:00Z — canonical: AWS SaaS Lens logical-isolation/tier-based; competitor: Crystallize Plugin
  Store/Registry install-state + revision locking, Stella Ops `plugin_instances` 9 lifecycle states,
  DuploCloud namespace isolation, Spree/Kong install-state parity; pitfalls: Autonoma async-context-loss
  + cache leakage + singleton leak, Payload CMS version drift; adversarial: silo isolation = paid
  compliance tier, governance hooks w/o enterprise tier = phantom work; counter-evidence: Salesforce
  OrgID metadata overlay, Shopify install-state, VS Code central immutable catalog).
  **Fresh queries (3, post-dedup, exa MCP):**
  - [canonical/pitfalls — Python contextvars (PEP 567) per-request tenant context]: contextvars is the
    canonical per-request tenant-context mechanism for async (OneUptime FastAPI middleware:
    `current_tenant_var.set` → `finally: reset`); **GOTCHA: `loop.run_in_executor` does NOT propagate
    contextvars (cpython#78195, won't-fix) — `asyncio.to_thread()` DOES (py3.9+)**; hosted_api.py
    already uses `asyncio.to_thread` exclusively (verified ~14 call sites). In-repo precedent:
    `tortoise/mcp_auth.py` ContextVars. Sources: https://docs.python.org/3/library/contextvars.html,
    https://peps.python.org/pep-0567/, https://oneuptime.com/blog/post/2026-01-23-build-multi-tenant-apis-python/view,
    https://github.com/python/cpython/issues/78195, github.com/adcontextprotocol/adcp-client-python
    (registry_cache keyed on `(tenant_id, lookup_key)` from `current_tenant` contextvar).
  - [competitor-precedent — per-tenant plugin install-state schema]: Paperclip `plugins` table
    (manifest_json JSONB, version, api_version, status, install_order) + `plugin_config` +
    `plugin_entities` scoped by companyId ("omitting companyId would return the first row regardless
    of tenant — unsafe"); LaunchMyStore `AppInstallation` (appId, storeId, status, installedVersion,
    pinnedVersion, autoUpdate); Digitorn scope-aware installs — wrong-owner → **404 not 403, by
    design, to mask existence** (informs the isolation test semantics: empty/404 is the stronger
    anti-enumeration posture than 403); Crystallize installations pin revisionId, never auto-migrate.
    Sources: https://crystallize.com/docs/developer/plugins, github.com/getpaperclipai/paperclip
    (packages/db/src/schema/plugins.ts, server/src/services/plugin-registry.ts),
    https://docs.launchmystore.io/api-reference/apps/installations, https://docs.digitorn.ai/docs/language/multi-tenant.
  - [competitor-precedent/pitfalls — idempotent starter-pack/template seeding at signup]: Backlex
    template apply is idempotent-additive (skip existing by slug; sample rows only into freshly-created
    collections; best-effort — failure never blocks signup); PerpetualSoftware/pad title-based
    idempotency with partial-init recovery + retry-safety; opendecree/decree idempotent config reseed
    (ErrAlreadyExists → skip); SchemaSmith per-tenant schema fan-out — ONE shared template keeps every
    tenant in sync, "no separate provisioning script pipeline"; Granit `TenantCreatedEvent` → idempotent
    seeder (at-least-once safe) + explicit backfill for existing tenants; b2b-strawman ADR-087 boot-seed
    via the SAME REST provisioning code path as production. Sources: https://backlex.com/docs/templates/,
    github.com/PerpetualSoftware/pad#144, github.com/opendecree/decree#140,
    https://schemasmith.com/documentation/concepts/multi-tenant-deployments.html,
    https://granit-fx.dev/dotnet/guides/parties-tenant-seeding/, b2b-strawman ADR-087.
- **Ontology (low)** — deduplicated: no vocabulary change this slice (manifest v3 + kind partitioning
  landed in #949/#950/#951); kind lifecycle/schema-versioning governance deferred per prior dedup note
  ("Re-open when custom-pack authoring is scoped"). Re-open if this slice grows custom-pack authoring.
- **UX (low)** — no new user-facing UI: activation is internal to provisioning (backend/API), the new
  introspection surface is API/MCP-only. Onboarding UX is unaffected at the surface level.
- **Library-deps (none new)** — stdlib `contextvars`; zero new third-party deps (see Integration Docs).

### Integration Docs (drafted at solution-converge)

- **`contextvars`** — stdlib (PEP 567), Python 3.11+. In-repo precedent: `tortoise/mcp_auth.py`
  (`_current_team_id`, `_current_team_limits` ContextVars + `TeamResolutionMiddleware`). No new dep.
- **No new third-party dependencies** in the chosen approach.
- **Supabase RPC seam** — `provision_team` (migration 0010, `supabase_control.py:1018+`): atomic
  idempotent upserts; pack activation must ride the same transaction OR be an idempotent post-step
  (recommendation: idempotent post-step with retry-safe semantics, since it touches the tenant graph —
  a different store than the Supabase RPC transaction).
- **FalkorDB graph per tenant** — `graph_name = team_{id}` (hosted_api.py:595, 1649; hosted_backup
  graph_name conventions). Tenant graph is the natural home for per-tenant pack install-state
  (graph-native; works in BOTH control-plane modes).

---

## Rejected Alternatives (solution diamond)

**Problem-diamond rejects (recorded for traceability):**
- **F1 per-tenant packs_dir copies** — drift (Kong parity / Spree no-per-tenant-installation), global
  singleton consumers wouldn't reach per-tenant dirs anyway, storage duplication. *Would be better for:*
  a paid silo compliance tier (FedRAMP/HIPAA residency) requiring physical vocabulary isolation.
- **F4 do-nothing** — provisioning automation + state model are real value. Rejected on its enterprise-
  governance challenge being *accepted* (deferred) but its core claim (nothing to ship) falsified by the
  absent automation + untestable isolation O/I/T. **Honest value note:** this slice is FOUNDATION work —
  the isolation surface and activation automation are enabling infrastructure for the deferred
  custom-pack authoring path; near-term user-visible value is limited (nothing breaks today).

**Boundary/lifecycle decisions (from problem-verify):**
- Pack install-state lives in the tenant graph (`graph_name=team_{id}`). Graph deletion wipes
  install-state — acceptable: tenant deletion implies pack-state deletion. Re-provisioning with the same
  team_id re-activates via the idempotent path. Recorded as an explicit decision, not a gap.
- Vocabulary (kind expansion) stays process-global for this slice (shared catalog). Tenant-scoping of
  `domain_loader._registry` / `sdk._get_kind_expander()` consumers (extractor, SDK kind validation,
  hosted validators) is DEFERRED to the custom-pack slice — blast radius of that work is enumerated
  there; this slice deliberately does not thread per-tenant kind resolution.
- 404-vs-403 existence masking: empty result (no error) is the default posture for cross-tenant pack
  queries (anti-enumeration, Digitorn precedent); 403 reserved for unauthenticated/unauthorized API
  calls at the auth layer (already enforced by `get_current_team`).

### Solution approaches (Phase 4 — diverge)

**Approach A — Graph-native install-state + idempotent activation + read-only introspection surface (CHOSEN):**
`(:PackInstall {namespace, version, status, source, installed_at})` nodes in the tenant graph
(`graph_name=team_{id}` — the LANDED isolation boundary). Single `ensure_tenant_packs(sdk)` idempotent
routine (MERGE per namespace): eager after graph creation in all three provisioning sites, self-healing
in the introspection surface, operator backfill for existing tenants. New read-only `GET /v1/packs`
(REST, `Depends(get_current_team)`) + `packs_list` MCP tool (`tool_registry` http_policy=True, uses
`_get_team_sdk()` contextvar seam). Vocabulary stays global (shared catalog).

**Approach B — Supabase `pack_installs` table + RLS:** new migration; rows inserted in the atomic
`provision_team` RPC; RLS enforces tenant scope (403 semantics); introspection reads table.

**Approach C — Per-tenant packs_dir copies (issue's literal mechanism):** copy `packs/` to
`/data/packs/team_{id}/` at provision; per-tenant PackRegistry via contextvars.

**Approach D (variant, merged into A) — Pure lazy/on-demand activation** (no eager hook; activate on
first pack introspection).

### Convergence rationale (Phase 5 — quality over convenience)

**Chosen: Approach A.** Evaluated on outcome quality, edge-case handling, failure-mode coverage, and
future extensibility — NOT on diff size:

1. **Dual-mode coverage (edge case):** A works identically in Supabase-mode (production: provision_team
   RPC + graph creation in hosted_api) and registry-mode (selfhost). B requires Postgres control plane
   (Supabase-only) — the registry mode would need a parallel path (fragmentation). C needs filesystem
   provisioning + the singleton rework anyway.
2. **Existing-tenant backfill (failure mode):** A self-heals — the introspection surface ensures
   install-state on read, so pre-existing tenants converge automatically without a separate migration
   step; B requires an RPC backfill; C requires directory copy backfill. (P2-A2 from problem-verify
   resolved by design.)
3. **No new infra / deps (future extensibility):** A reuses the tenant graph + contextvar seam
   (mcp_auth) + existing auth (`get_current_team`) — zero new third-party deps (Integration Docs).
4. **Isolation is architectural, not bolted-on:** install-state lives inside the already-isolated
   graph; cross-tenant access is impossible by construction (a query with tenant B's identity reads
   tenant B's graph). The automated test asserts the introspection surface binds to the correct graph.
5. **Pitfall compliance:** avoids copy drift (C), async-context-loss (singletons untouched), and
   RLS-misconfiguration class bugs (no RLS surface).

**Rejected alternatives (with when-they-WOULD-be-better):**
- **B (Supabase table + RLS)** — would be better when: the dashboard (Supabase-hosted) must read pack
  state directly, or cross-tenant pack analytics/billing queries are needed, or pack state must survive
  graph deletion. Not now: dual-mode fragmentation, RLS surface = new attack surface for marginal gain.
- **C (per-tenant packs_dir copies)** — would be better when: a PAID SILO tier (FedRAMP/HIPAA
  residency) demands physical vocabulary isolation per tenant. That is a GTM/pricing decision
  (premium tier), not this slice — see adversarial research (silo-as-paid-tier).
- **D (pure lazy)** — would be better when: provisioning flow changes are frozen/forbidden. Merged
  into A as the self-healing mechanism (best of both).

### Plan (Phase 5 draft)

**1. PackRegistry catalog helpers** (`tortoise/pack_registry.py`): expose read-only pack summaries
(namespace, name, version, tier, description) — `list_packs()` already exists (~line 928); add a stable
`pack_summaries()` dict for catalog joins. No behavior change.

**2. New `tortoise/pack_state.py`** (thin, imports SDK + pack_registry):
- Constants: `PACK_INSTALL_LABEL = "PackInstall"`, default starter set from
  `TORTOISE_STARTER_PACKS` env (default `dev,marketing,product-strategy,project-management`).
- `ensure_tenant_packs(sdk, starter=None) -> list[dict]` — idempotent: `MERGE (p:PackInstall
  {namespace}) SET p.version=..., p.status='active', p.source='starter', p.installed_at=coalesce(...)`
  per starter pack; best-effort semantics (logs, never raises into the provisioning path).
  **Concurrency model (explicit):** MERGE on `{namespace}` is atomic per statement in
  Cypher/FalkorDB — concurrent ensures (provision hook + introspection self-heal) converge to
  exactly ONE node per namespace regardless of interleaving; a multi-namespace loop may expose a
  transient partial set, which self-heal converges on next read. Asserted by a dedicated
  concurrency test (N parallel ensures → one PackInstall per namespace).
  **Env validation:** starter names are validated against `PackRegistry.pack_summaries()` at call
  time (not cached at import) — unknown names are skipped with a logged warning, never fail
  provisioning; unset/empty `TORTOISE_STARTER_PACKS` → built-in default set; read-at-call-time is
  a stated contract (enables the per-tenant test-injection seam). Tests for typo'd/empty env.
- `get_tenant_packs(sdk) -> list[dict]` — read install-state from the tenant graph, join catalog
  metadata from the global registry; sorted; empty list on no installs.

**3. Provisioning hooks** (`tortoise/hosted_api.py`): call `ensure_tenant_packs` after graph creation
at the three provisioning sites — **enumerated with mode mapping (coherence P2 fix):**
1. Registry-mode `/internal/provision` (~line 640) — serves `TORTOISE_CONTROL_PLANE=registry`
   (selfhost).
2. Self-service key provisioning (~line 1660) — Supabase mode (calls `provision_team` RPC then
   creates graph).
3. `v1/teams` team-create (~line 2906) — Supabase mode (same RPC + graph).
Sites 2+3 are the Supabase-mode paths; site 1 is registry-mode. The single `ensure_tenant_packs`
helper is called at each graph-creation site — one hook function, three call sites, both modes
covered (reconciles with the Integration Docs finding: activation hook lives in hosted_api post-RPC).
Best-effort (Backlex: failure never blocks signup).

**4. Introspection surface (single ensure-then-read core for BOTH surfaces — REST/MCP symmetry):**
- **Scoping model (PINNED): auth-only — no tenant_id selector parameter.** Team identity comes
  EXCLUSIVELY from auth (`get_current_team` REST dependency / `_current_team_id` MCP contextvar).
  Consequence: cross-tenant access is **structurally impossible** (no request can name another
  tenant's graph), ensure can NEVER be triggered against a foreign team (no selector to abuse),
  and the anti-enumeration requirement rewords to: same-tenant no-installs → empty; two-token test
  asserts no bleed.
- `GET /v1/packs` (hosted_api): `Depends(get_current_team)`; **rejects with 401 when `team_id is
  None`** (SKIP_AUTH/background paths — fail closed, never default-namespace fallback; consistent
  with existing `get_current_team` 401). **Response matrix (pinned):** no auth → 401; auth + graph
  unreachable → 503 (never empty-on-outage); auth + no installs (starter set empty/unset or ensure
  failed) → empty list; auth + installs → tenant's pack list. **Self-heal:** on first read with no
  installs, ensure-then-return (convergence safety net).
- MCP `packs_list` tool (`tool_registry.py` + `mcp_server.py`): `http_policy=True` (per #454
  allow-list), resolves SDK via `_get_team_sdk()` (mcp_auth contextvar seam), same ensure-then-read
  core as REST (no REST/MCP divergence); fail-closes identically on None team.
- **Async-context constraint (PINNED, from research):** all ensure-then-read execution goes through
  `asyncio.to_thread` (propagates contextvars, py3.9+) — NEVER `loop.run_in_executor` (does NOT
  propagate; cpython#78195). Regression test exercises the MCP path under threadpool execution
  asserting correct team scoping.
- **Eager-path observability (PINNED, coherence P1 fix):** AC1 asserts `(:PackInstall)` nodes
  DIRECTLY in the tenant graph immediately post-provision (pre-GET, bypassing the surface) so the
  eager path is proven independent of introspection. Self-heal is disabled under a test-only flag
  (`PACK_STATE_DISABLE_SELF_HEAL=1`) so AC1 can exercise the pure eager path. Never-activated
  semantics: first introspection self-heals; AC3 asserts REST/MCP parity of the HEALED result.
  Empty response occurs only when the starter set is empty/unset or ensure failed (503 path).

**5. Backfill script** `scripts/backfill_pack_installs.py` (standalone, repo script convention):
iterate existing teams (Supabase teams table or registry graph per mode), `ensure_tenant_packs` each;
idempotent — safe to re-run (Granit backfill precedent). **Direct test:** pre-seed a tenant with a
partial install (some namespaces present), run the script, assert convergence with no duplicate
`PackInstall` nodes and no re-seed of already-active namespaces.

**6. Env/config:** `.env.example` — document `TORTOISE_STARTER_PACKS` (comma-separated default set;
unknown names skipped with warning; unset/empty → built-in default). **Removal semantics (pinned):**
ensure is ADDITIVE-only by design (research: Backlex/decree reseed-no-op) — removing a pack from
`TORTOISE_STARTER_PACKS` does NOT uninstall existing installs (non-destructive deactivation-by-
config-change); introspection continues to report the install with its stored version. Explicit
uninstall/deactivation semantics belong to the deferred governance slice.

**7. Governance hooks (kind lifecycle, schema versioning): DEFERRED** — documented in code comment at
pack_state.py + this issue; no code this slice (phantom work without an enterprise tier).

**8. Label compatibility check:** `PackInstall` label vs `validation/` rules — RESOLVED during scoping:
`validation/` contains only EP-algorithm + license-surface validators; no graph-label/ontology
constraints exist. Plan includes a one-line CI-safe verification that graph writes are not validated
against an ontology whitelist that would reject the new label.

**Risks/limits of Approach A (explicit — consciously accepted):**
- **Graph-lifecycle coupling:** install-state dies with the tenant graph. Accepted: deletion of a
  tenant graph discards pack install-state; the introspection self-heal reinstalls the starter set
  on next access (recorded decision, not an accident).
- **Side-effect-on-GET:** the read path performs a write (self-heal) when installs are absent. Cost
  envelope: one MERGE batch per pack namespace on first access per tenant, then zero writes on
  subsequent reads (install-state present). Bounded and idempotent; acceptable for the starter-only
  set. (A future read-only mode can skip self-heal when strict GET purity is needed.)
- **New graph label namespace:** `PackInstall` joins the tenant graph label space; prefixing is
  deliberate (`PackInstall`, not bare `Pack`) to avoid collision with pack-related domain kinds.
- **Phantom-work guard:** governance hooks are intentionally absent this slice — no drift toward
  half-built lifecycle state machines without an enterprise tier.
- **Tenant teardown (pinned):** tenant deletion removes PackInstall state with the graph; NO install
  records exist outside tenant graphs (isolation invariant stated as a property, not an accident).

**Rejected implementation variants (coherence devil's-advocate, recorded):**
- **Postgres installs table** — rejected for graph co-location (state lives beside the data it
  governs) + zero new deps; acknowledged cost: no transactional backfill and install-state dies on
  graph outage (the 503-on-outage surface is the price of the in-graph choice).
- **Pure env-derived reads (no install-state at all)** — rejected: loses version/status semantics
  that the research supports (Crystallize revision pinning, LaunchMyStore version fields).
- **MCP as thin HTTP client of GET /v1/packs** — NOT chosen: MCP calls the shared ensure-then-read
  core directly (in-process), so team context stays on the contextvar seam and there is no
  HTTP-in-HTTP indirection.

**Testing strategy:**
- `tests/test_pack_state.py` — idempotency (run ensure twice → identical state, no dupes); activation
  writes correct nodes; get_tenant_packs returns starter set + metadata; empty graph self-heals;
  unknown/removed pack from starter list doesn't orphan installs (removal = no-op, upgrade-in-place);
  **concurrency** (N parallel ensures → exactly one PackInstall per namespace — MERGE atomicity,
  asserted against FalkorDB server semantics, not just FalkorDBLite); **partial-failure convergence**
  (one MERGE fails → next ensure converges without dupes); **env-misconfig** (typo'd/empty
  `TORTOISE_STARTER_PACKS` → skipped-with-warning / default, never fails provisioning, never creates
  ghost installs).
- **Cross-tenant isolation test** (O/I/T indicator 2): two tenants (two SDK namespaces); inject distinct
  starter sets; assert tenant A introspection returns ONLY A's set; tenant B's token/namespace on A's
  surface → empty (no error, no leak — anti-enumeration).
- **Provisioning tests** (indicator 1): registry-mode provision → PackInstall nodes present
  (extend `test_provisioning_edge_function.py` or new); self-service + v1/teams flows → active.
- **MCP tool test**: `packs_list` over HTTP mode with auth returns tenant-scoped set; excluded without
  http_policy (regression guard).
- Full suite: `python -m pytest tests/ -v` (FalkorDBLite embedded, no Docker).

**Verification plan:** AC1–AC6 below + wiring check table (Phase 6).

**Runtime prerequisites:** none new — existing FalkorDB/FalkorDBLite, Supabase (optional), stdlib.

**Acceptance criteria:**
- **AC1 (provisioning automated):** new tenant via ANY provisioning path ends with the starter pack set
  active in its graph — asserted by DIRECT `(:PackInstall)` graph query immediately post-provision
  (pre-GET, self-heal disabled via test flag) so the eager path is proven independent of the surface.
- **AC2 (isolation):** automated two-token test — tenant A's `GET /v1/packs` returns A's set; tenant B's
  token on the same endpoint returns B's set (no bleed, never A's packs); same-tenant no-installs →
  empty. (Auth-only scoping: no tenant selector exists, so cross-tenant access is structurally
  impossible; the test asserts no token can observe another tenant's pack state.)
- **AC3 (MCP):** `packs_list` (HTTP mode) returns the same tenant-scoped set as REST — INCLUDING the
  never-activated-tenant scenario (both surfaces self-heal via the same ensure-then-read core).
- **AC4 (idempotent):** re-running activation (provision retry, self-heal, backfill) is a no-op.
- **AC5 (backfill):** operator script converges existing tenants without duplicating installs.
- **AC6 (no drift/no deps):** no third-party deps added; no per-tenant pack copies; full suite green.

---

## Wiring Check

**HARD-GATE: PASS — all touch points covered** (no gaps). Related in-flight work noted, not absorbed.

| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| Tenant FalkorDB graph (`team_{id}`) — `PackInstall` nodes | Data store | Plan §2 (`pack_state.py`), §3 (hooks at graph creation) | ✅ |
| pack_registry catalog (shared) — `pack_summaries()` helper | Data store/code | Plan §1 | ✅ |
| `GET /v1/packs` endpoint | API | Plan §4 (auth-only, response matrix) | ✅ |
| Provisioning hooks — 3 sites: registry-mode `/internal/provision` (~640), self-service (~1660), `v1/teams` (~2906) | API | Plan §3 (enumerated + mode-mapped) | ✅ |
| Auth (REST) — `get_current_team` dependency, 401/503 semantics | Auth | Plan §4 | ✅ |
| Auth (MCP) — `mcp_auth` ContextVars + `_get_team_sdk()` seam | Auth | Plan §4 | ✅ |
| MCP `packs_list` + `tool_registry` http_policy allow-list (#454) | MCP | Plan §4 | ✅ |
| Supabase `provision_team` RPC (migration 0010) — external seam | External service | Plan §3 (activation post-RPC in hosted_api layer, both modes) | ✅ |
| `TORTOISE_STARTER_PACKS` env + `.env.example` | Cross-cutting | Plan §6 | ✅ |
| Operator backfill (`scripts/backfill_pack_installs.py`) | Ops | Plan §5 | ✅ |
| `validation/` schema rules vs `PackInstall` label | Cross-cutting | Plan §8 (RESOLVED: no graph-label constraints — EP/license validators only) | ✅ |
| #1120 (provisioning off browser path) — hook lives server-side in hosted_api; client-path move does not change server-side graph creation | Related issue | Monitor, not absorbed | ⚠️ noted |
| #557 (sub-tenancy epic, per-end-user) — different tenancy level (team vs end-user) | Related issue | Out of scope, not absorbed | ⚠️ noted |
| #1026 (pack template → extractor) — pack CONTENT slice, not isolation | Related issue | Out of scope, not absorbed | ⚠️ noted |

**Extra issues filed (do-not-absorb):** #1154 — tech-debt: process-global pack registry singletons, latent cross-tenant leak when custom-pack authoring lands (see Finalize section).

---

## Review Cycle Log

Full per-gate cycle logs are in the **Verification Gates** section above (problem-verify C1, solution-verify C1, coherence C1). No re-dispatch cycles were needed — all three gates passed on first cycle with P2+ only, per the skill's pass-through rule.

---

## Finalize

**Extra issues filed during scoping (do-not-absorb):**
- **#1154** — tech-debt: process-global pack registry singletons (`domain_loader._registry`, `sdk._get_kind_expander`) are a latent cross-tenant leak when custom-pack authoring lands. Trigger: the custom-pack authoring slice. Proposed approach: per-tenant registry cache keyed by `team_id` via the existing `mcp_auth` ContextVar seam; `asyncio.to_thread`-only execution. Related: #318.

**Parallel-work checkpoint (skill-mandated `parallel_work_check`):** SKIPPED — infra tooling not present in this environment; noted per streamlined-run constraint.

---

## Complexity

| Domain | Rating | Rationale |
|--------|--------|-----------|
| Architecture | high | Multi-tenant isolation boundary, resolution seam, dual-mode provisioning |
| Ontology | low | No vocabulary change (dedup note); kind governance deferred |
| UX | low | No UI surface |
| Library-deps | none | stdlib contextvars only |
| Data | medium | New pack install-state persistence (graph-native nodes) |
| Security | medium | Cross-tenant access enforcement + anti-enumeration semantics |
| Testing | medium | Automated cross-tenant isolation test (O/I/T indicator 2) |
