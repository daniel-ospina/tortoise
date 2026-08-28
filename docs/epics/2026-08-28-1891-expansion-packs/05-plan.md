---
title: "Epic #1891 — Implementation Plan: Expansion packs as a configurable product"
type: plan
domain: product
doc_status: draft
created: 2026-08-28
ownedBy: epistemic-team
---

# Epic Plan — Expansion packs as a configurable product (hosted + self-hosted)

> Issue: #1891 · Pipeline stage: Plan · Skill: epic-plan
> Inputs: `01-align.md`, `02-research-brief.md`, `03-scope.md`, test-design `04-test-design.md` (filed as #1898)
> Test-Design issue: **#1898** (integration-surface map — every child issue cites it)

---

## 1. User Journeys

### Personas

| ID | Persona | Context |
|----|---------|---------|
| P1 | **Self-host operator** (the Letta prospect) | Runs the daemon on his own infra; wants packs working on the documented path; will author a vertical pack; sovereign inference |
| P2 | **Pack author** (hacker-type early user) | Builds domain packs from the template; iterates locally; expects agent-assistable authoring |
| P3 | **Hosted tenant admin** | Manages a team on api.premiselabs.co; wants per-team ontology config without self-hosting |
| P4 | **Agent** (the graph consumer) | Extraction consumes pack vocabulary; rules-with-why lifecycle runs on the graph |
| P5 | **New user** (onboarding) | Follows the quickstart; must see packs working without a git clone |

### Journeys

**J1 — Install and see packs (P1, P5) → E2E-1**
Entry: fresh host, docker installed. Exit: `tortoise_packs_list` reports the starter set; a mined session shows pack vocabulary.
Steps: docker compose up → connect MCP → list packs → capture+mine a session → spot-check extraction output.
Edge: image without packs (defect) → smoke test must fail loudly in CI, not silently ship.

**J2 — Configure a custom pack dir (P1) → E2E-2**
Entry: daemon running. Exit: custom pack active alongside starter set.
Steps: create `packs/mydomain/manifest.yaml` → set `TORTOISE_PACKS_DIR` → restart → verify pack appears → verify a broken pack is isolated.
Edge: env typo → warn-skip with logged warning, never empty-degrade silently.

**J3 — Author a pack (P2) → E2E-3**
Entry: CLI installed. Exit: a validated pack producing the intended kinds in extraction; the authoring guide (`docs/EXPANSION_PACKS.md`) is the reference the journey depends on (guide delivery is an explicit exit condition).
Steps: `tortoise pack new mydomain` → edit kinds/chains → `tortoise pack validate` → install → capture matching content → verify kinds minted.
Edge: namespace collision, invalid kind casing, canonical-kind conflict → validate errors with actionable messages.

**J4 — Rules-with-why lifecycle (P4, agent) → E2E-4**
Entry: agent-ops pack active; a session where a rule is stated with reasoning. Exit: rule + rationale + IMPL in the graph; a rewrite supersedes cleanly with EP re-propagation.
Steps: capture → mine → verify rule Object + rationale Points + IMPL → agent rewrites rule (supersede) → verify confidence cascade + old argument tree retained.
Edge: session states a rule with no reasoning → rationale never minted → chain incomplete (warn per chain enforcement); supersede re-propagation fails → contested-claim detection surfaces it; rule kind near-miss without a `retry` declaration → classified as core kind with warning.

**J5 — Hosted per-team pack install (P3) → E2E-6**
Entry: hosted tenant A. Exit: A's custom pack active and visible only to A.
Steps: upload manifest (namespace read from the manifest itself — no redundant payload field) → validation pass → activation → list packs → negative test from tenant B.
Edge: malformed manifest → 4xx with validation details; oversized → 413; concurrent upload → idempotent single activation; manifest omits required `name`/`namespace` → 422.

**J6 — Migrate self-host → hosted with packs (P1, P3) → E2E-7**
Entry: self-host graph with custom pack. Exit: hosted graph answers queries with the pack's vocabulary (or a loud mismatch error).
Steps: `tortoise export` → hosted import → verify pack vocabulary present → verify structure parity.
Edge: artifact predates pack-config → import fails loudly (never silent partial).

**J7 — Enforcement in action (P2, P4) → E2E-5**
Entry: a pack declaring `enforcement: retry` on a kind; a session where the classifier near-misses it. Exit: the classifier retries (bounded) and either classifies or records the near-miss; an undeclared relation write warns-not-blocks.
Steps: author declares enforcement → capture content that near-misses → observe bounded retry → SDK caller writes undeclared relation → structured warning returned, write proceeds → chain pass behavior unchanged.
Edge: repeated near-miss → bounded (M3 caps), no infinite loop; relation warning shape stable (structured list).

## 2. Workflows

**WF-1 Packaging & release (system)**
build wheel (`python -m build`) → **CI smoke: clean-venv install, `PackRegistry.load_all() >= 5` (the starter set incl. agent-ops)** → publish; docker build (selfhost + hosted) → **CI smoke: boot image, registry >= 5** → publish. Gate: both smokes are REQUIRED before publish (the G1 regression guard).
Failure: smoke fails → publish blocked. This is the fix for the silent empty-registry defect.

**WF-2 Registry resolution at startup (system)**
Startup → resolve packs dir: `TORTOISE_PACKS_DIR` (set+valid) → packaged default (`tortoise/packs/` in the install) → repo root (dev/editable) → load all manifests → validation with per-pack isolation (R-16) → compile vocabulary (kind expansions, chains, kindDefs).
Failure: env dir missing OR set-but-empty (zero manifests) → warn + fallback (the empty-registry defect class in a new costume — never degrade silently); malformed manifest → that pack isolated, others load.

**WF-3 Authoring loop (P2 + agent)**
`pack new` scaffold (also updates `packs/_template/manifest.yaml` to include `memory_granularity` — the template currently lags the shipped packs) → author/edit (human or agent) → `pack validate` (schema + cross-pack refs) → install (packs dir + restart self-host; upload hosted) → capture content → extractor mints pack kinds → iterate.
Manual-intervention trigger: validate errors; extraction near-miss enforcement (`retry`).

**WF-4 Hosted tenant pack lifecycle (P3 + system)**
`POST /v1/packs/manifests` (body: `manifest_yaml` only — namespace read from the manifest's REQUIRED field) → authenticate (team scope) → size/format limits → shared validator (schema + cross-pack vs core+starter) → **reject connector/tool entrypoints (ontology-only v1)** → store manifest in tenant graph (`:PackManifest` node) → activate (`PackInstall` MERGE, per-(graph,namespace) lock #1307) → serve via `GET /v1/packs` / `tortoise_packs_list` (tenant-scoped, D6 masking).
Failure: any validation failure → 4xx, no activation; tenant isolation is structural (graph namespace); #1154 singletons resolved before this ships.

**WF-5 Export/import with pack config (P1 → P3)**
`tortoise export` → artifact includes pack-config block (manifest YAMLs + activation records) → hosted import → validate → apply tenant packs → structure parity check. If artifact predates pack-config: import must FAIL LOUDLY on mismatch, never silently drop vocabulary.

**WF-6 Enforcement paths (P4, agent + SDK)**
Extraction: kind classifier near-misses a `retry`-declared kind → bounded retry (M3 caps) → succeed or record near-miss. Write path: `create_operator` with undeclared relation/kind-pair → **warn-not-block** (write proceeds, structured warning returned) → violation event logged (feeds future governance app, out of scope). Chain pass: severity levels read from manifest; deterministic rewire unchanged.

## 3. Prototype (markdown — non-GUI epic)

### CLI surface

```
$ tortoise pack new mydomain
→ scaffolded packs/mydomain/manifest.yaml (namespace: mydomain, from _template)
$ tortoise pack validate packs/mydomain
→ OK (or: validation errors with actionable messages, e.g.
   "ontology.objectKinds: 'Domain' should be camelCase (lowercase first letter)")

$ env TORTOISE_PACKS_DIR=/etc/tortoise/packs tortoise-serve ...
→ startup log: "loaded 6 packs (5 starter + 1 from TORTOISE_PACKS_DIR)"
→ invalid manifest in dir: "pack 'broken' failed validation — isolated (see registry.errors)"
```

### MCP surface (hosted)

```
tortoise_packs_list
→ [{namespace, name, version, tier, description, status, source}...]  # starter + tenant packs
tortoise_pack_install {manifest_yaml}
→ {installed: true, namespace, validation_errors: []}
→ {installed: false, validation_errors: ["connector entrypoints not allowed on tenant packs"]}
```

### REST surface (hosted)

```
POST /v1/packs/manifests   {manifest_yaml}   # namespace read from the manifest (REQUIRED field)
→ 201 {activated: true, namespace} | 422 {errors:[...]} | 413 {error:"manifest exceeds 64KB"}
GET /v1/packs              # extended: starter + tenant packs (D6 masking)
```

### Agent-ops rules-with-why pack (manifest sketch)

```yaml
namespace: agent-ops
name: Agent Operations
version: 0.1.0
tier: free
ontology:
  extends: core
  objectKinds: [rule]
  subclassOf: {rule: standard}
  pointKinds: [rationale]
  eventKinds: [ruleRevised]
  relations:
    - predicate: groundedIn
      mechanism: IMPL
      fromKind: agent-ops:rule
      toKind: agent-ops:rationale
      extractable: true
  chains:
    - id: ruleLifecycle
      steps: [rule, rationale, ruleRevised]
      enforcement: warn
  memory_granularity: 'Durable: the rule text, the situation that created it, and the
    reasoning that supports/undermines it. Ephemeral: rule mechanics, approval logistics.'
extraction:
  active: true
  sourceTypes: [conversation]
  enforcement:
    kinds:
      rule: retry   # kind-level retry — the E2E-5 fixture relies on this
```

> Schema note: `memory_granularity` lives under `ontology:` (as in the shipped packs; `value_extractor.py` reads `ontology.memory_granularity`). The `_template` must be updated to include it before `pack new` ships (see WF-3).

---

> Plan continues — substeps 4–8 in the next sections.

---

## 4. Data Model

> `### Data Model Research Notes` — justified skip: the schema decisions are covered by the research brief (Workflow Pattern Research §#318 per-tenant graph-native precedent; Tech Stack Research §shared validator) + test-design #1898 surface 8. No novel schema pattern requiring external queries.

### Entities

| Entity | Where | Fields | Notes |
|--------|-------|--------|-------|
| `PackInstall` (existing, #318) | tenant graph | namespace, version, status, source, installed_at | source gains `custom` value; otherwise unchanged; idempotent additive MERGE; per-(graph,namespace) lock (#1307) |
| **`PackManifest` (NEW, hosted)** | tenant graph | namespace, name, version, yaml (full manifest text), sha256, status (active/disabled), installed_at | Per-tenant pack storage — graph-native per #318 "tenant graph IS the isolation boundary"; no new storage surface. One per (tenant, namespace); MERGE-keyed |
| Pack catalog (shared, read-only) | filesystem (`tortoise/packs/` packaged) | manifest.yaml files | Relocated from repo root into the package tree; resolution order: `TORTOISE_PACKS_DIR` → packaged → repo root (dev) |
| `tortoise-export-v1` artifact | file | + pack-config block (schema_version, packs: [{namespace, yaml, activated}]) | Backward-compatible additive block (importers predating it fail loudly on mismatch) |
| Agent-ops pack kinds | ontology (manifest) | rule (Object ⊂ standard), rationale (Point), ruleRevised (Event), groundedIn (rule -IMPL-> rationale, extractable), ruleLifecycle chain | New pack content, no schema change |

### Integrity constraints
- Hosted `PackManifest`: manifest must pass shared validator (schema + cross-pack vs core+starter) BEFORE write; connector/tool entrypoints rejected (ontology-only v1); size cap 64 KB.
- **Reserved-namespace rejection:** a tenant manifest whose `namespace` equals a starter-pack namespace (`dev`, `pm`, `marketing`, `product-strategy`, `agent-ops`) → **422** with an explicit reserved-namespace error. (Without this, a tenant pack named `dev` would collide with the starter `dev` `PackInstall` record and produce two packs claiming one namespace with no precedence rule — `PackRegistry._validate`'s duplicate check only exists in the directory `load_all()`, not for single-manifest uploads.)
- `PackInstall.source='custom'` requires a matching `PackManifest` (referential integrity in the tenant graph).
- Isolation: all pack state lives in the tenant's own graph namespace — no cross-tenant access surface (structural, per #318).

### #1154 resolution (process-global singleton leak)
- `domain_loader._registry` (module-global) is the leak: a tenant-scoped registry view built once globally would cross-contaminate tenants. Fix: split into (a) **shared catalog** — cached global, read-only (safe to share), and (b) **tenant view** — compiled per tenant from shared catalog + that tenant's `PackManifest` nodes; never cached globally, never mutated after first write. The current `_get_registry`/`PackRegistry` path stays for self-host (single-tenant by construction).
- **Tenant-view memoization (perf — #1350 class):** `build_master_list` is memoized in the extractor because per-call manifest re-reads cost 12.2s of a 14.3s 60-chunk run (#1350). The tenant view must be memoized per `(tenant_identity, pack_config_version)` where `pack_config_version` is a hash of the tenant's `(namespace, version, sha256)` PackManifest tuples (or `max(installed_at)`), invalidated on `:PackManifest` write / activation. Isolation is preserved (cache key includes tenant identity; per-(graph,namespace) write lock #1307). The tenant-view compile reuses `compile_value_brief(packs_dir=...)` (`value_extractor.py`) which already accepts a dir parameter — no parallel compile path.
- **Hosted consumer wiring (required — the split is papering until consumers thread the view):** every registry consumer that must see tenant packs gets a tenant-scoped accessor, not the global: `known_kinds()` / `domain_kinds()` / `domain_kind_semantics()` / `domain_chain_spec()` / `kind_is_known()` in `domain_loader`, the extractor's `build_master_list` compile (hosted extraction must include the tenant's pack kinds/chains in prompts), and the SDK write-path validation (`create_operator` relation check). On hosted these are called with the tenant identity (from the authenticated request); the global `_get_registry` path is never used for tenant-scoped reads. **Verification:** cross-tenant negative test (test-design surface 8) extended to assert tenant A's pack kinds appear in A's extraction prompts and NOT in B's — plus an integration test that a hosted extraction run with a tenant pack mints the tenant's kinds.

---

## 5. Architecture

> `### Architecture Research Notes` — justified skip: research brief Tech Stack Research (setuptools package-data vs data_files; shared validator; #318 precedent) + test-design surfaces 1–10 cover the boundary decisions. No novel service topology.

### Component boundaries

```
┌─ Packaging (slice 1) ─────────────────────────────────────────────┐
│ pyproject.toml package_dir mapping → wheel ships tortoise/packs/  │
│ Dockerfile.selfhost/.hosted → COPY packs/ packs/ (build context)  │
│ CI smoke: built wheel + BOTH images → registry.load_all() >= 5    │
└───────────────────────────────────────────────────────────────────┘

┌─ Registry (core, shared) ─────────────────────────────────────────┐
│ pack_registry.PackRegistry  — parse-only, R-16 isolation,         │
│    schema + cross-pack validation (the SHARED validator)          │
│ domain_loader: resolution order (TORTOISE_PACKS_DIR → packaged →  │
│    repo root); self-host path (unchanged single-tenant)           │
└───────────────────────────────────────────────────────────────────┘

┌─ Authoring (slice 2) ─────────────────────────────────────────────┐
│ CLI: tortoise pack new / pack validate (argparse subparsers)      │
│    — reuses PackRegistry._validate + _template (updated with      │
│      memory_granularity)                                          │
└───────────────────────────────────────────────────────────────────┘

┌─ Hosted (slice 4) ────────────────────────────────────────────────┐
│ hosted_api: POST /v1/packs/manifests → validate (shared) →        │
│    write :PackManifest → activate (pack_state)                    │
│ pack_state: ensure_tenant_packs extended (custom source)          │
│ #1154: shared catalog cached-global; tenant view per-request      │
│ abuse: size cap 64KB + rate limit (abuse.py precedent)            │
└───────────────────────────────────────────────────────────────────┘

┌─ Enforcement (slice 3) ───────────────────────────────────────────┐
│ ONE shared seam: resolve_enforcement(kind|relation|chain) →        │
│   warn|retry|block — consumes PackManifest.enforcement_for*        │
│   + domain_validators.resolve_rule_severity (existing primitives)  │
│ dispatcher: warn → structured warning; retry → M3-bounded retry +  │
│   near-miss census; block → out of scope (rejected at validation)  │
│ kind_classifier + sdk.create_operator both consume the SAME seam   │
└───────────────────────────────────────────────────────────────────┘

┌─ Export/import (slice 4) ─────────────────────────────────────────┐
│ export.py: + pack-config block (v1.1 additive)                    │
│ hosted import: apply tenant packs or fail loudly                  │
└───────────────────────────────────────────────────────────────────┘
```

### Packaging decisions
- **Agent-ops pack ships and activates by default:** the agent-ops rules-with-why pack joins the shipped catalog AND `DEFAULT_STARTER_PACKS` (defaults become 5: dev, marketing, product-strategy, pm, agent-ops); the CI smoke bound moves to `>= 5`. E2E-4's precondition "agent-ops pack active" holds on a fresh install with no configuration.
- **Catalog packaging via `package_dir` mapping (no physical move):** `[tool.setuptools] package_dir = {"tortoise.packs": "packs"}` + `package-data` for `tortoise.packs = ["**/*.yaml"]` — ships the wheel with `tortoise/packs/` while repo-root `packs/` stays the single source of truth (no git mv, no test-path churn; dev/editable resolves the SAME directory, so the repo-root fallback is belt-and-suspenders). Docker still needs `COPY packs/ packs/` in both Dockerfiles (today neither copies it — the G1 root cause). Resolution order: `TORTOISE_PACKS_DIR` (set+valid) → packaged default → repo root (transition). E2E-1 CI smoke remains the durable guard on all three surfaces.
- **Tenant manifests live in the tenant graph** (not control-plane Postgres): zero new storage/backup surfaces; isolation structural; export/import can read them naturally.
- **Ontology-only v1 for tenant packs:** connector/tool entrypoints rejected — the only code-execution surface stays allowlisted (starter packs).
- **Enforcement in the SDK** (shared write surface per 2026-08-05 D2), warn-not-block default; `block` level out of scope.
- **Failure modes:** packaging regression → CI smoke gates publish; upload abuse → size+rate limits; tenant drift → per-request tenant view (no global state); export/import → loud mismatch.

---

## 6. Interfaces

> Light research hook — justified skip: interface contracts follow existing in-repo conventions (MCP tool_registry shape, REST hosted_api shape, argparse CLI, export artifact versioning). No novel contract format.

### Environment
| Var | Type | Default | Semantics |
|-----|------|---------|-----------|
| `TORTOISE_PACKS_DIR` | path | unset → packaged → repo root | Self-host custom pack directory; warn+fallback on missing/empty (never silent degrade) |

### CLI (argparse subparsers in `tortoise/__main__.py`)
```
tortoise pack new <namespace> [--dir PATH]      # scaffold from _template; validates name rules AND
                                                # rejects reserved/colliding namespaces (starter set) at scaffold time
                                                # (same guard as hosted upload's 422)
tortoise pack validate <dir> [--db URI] [--json] # reuses PackRegistry validation; --json machine contract
```

### MCP (tool_registry + mcp_server)
| Tool | Group | Request | Response |
|------|-------|---------|----------|
| `tortoise_packs_list` (extended) | admin + team (tenant-scoped view) | {} | [{namespace, name, version, tier, description, status, source}] — starter + tenant packs (D6 masking); team group sees the tenant's own view, admin sees all |
| `tortoise_pack_install` (NEW, hosted only) | team | {manifest_yaml} | {installed, namespace, validation_errors: []} |

> **Self-host parity note:** the hosted-only MCP/REST install surface is mirrored on self-host by the filesystem packs dir (`TORTOISE_PACKS_DIR`) + CLI (`pack new`/`pack validate`) — the parity requirement is met by different surfaces per deployment (API on hosted, filesystem+CLI on self-host), both reachable by the same agent workflows.

### REST (hosted_api)
```
POST /v1/packs/manifests   body: {manifest_yaml}   # namespace read from manifest
  201 {activated: true, namespace}
  422 {errors: [{field, message}, ...]}   # incl. reserved-namespace rejection
  413 {error: "manifest exceeds 64KB"}
  429 rate-limited (abuse.py precedent — same shape as the signup limiter)
  401/403 team-scoped auth (existing)
GET /v1/packs   # extended: starter + tenant packs (D6 masking)

POST /v1/teams/{team_id}/import   # EXISTING endpoint — E2E-7 depends on it
  auth: owner-scoped session JWT (owner-only, like export)
  headers: Content-Type: application/vnd.tortoise.export.v1, X-Tortoise-Import-Key: <key_b64>
  body: encrypted export artifact bytes (from `tortoise export`)
  200 {imported: true, already: false, id, restored: {nodes, edges}}
  200 {imported: false, already: true}   # idempotent re-import
  422 quarantine (failed/tampered artifact never touches the live graph)
```

### Export artifact
`tortoise-export-v1` (v1.1 additive): `pack_config` block placed **inside the encrypted payload** (covered by the same integrity hash as the graph dump — envelope siblings would escape payload integrity coverage): `pack_config: {schema_version: 1, packs: [{namespace, yaml, activated}]}`. Importers without support: accept-and-error loudly on mismatch — never silent partial import.

### Enforcement contracts
- ONE shared seam: `resolve_enforcement(kind|relation|chain) → warn|retry|block` consuming `PackManifest.enforcement_for*` + `domain_validators.resolve_rule_severity`; dispatcher: warn → structured warning, retry → M3-bounded retry + near-miss census. No parallel resolution paths.
- `create_operator` returns `{..., warnings: [{code, message}]}` — undeclared relation/kind-pair → warn-not-block; warning shape stable (consumers may surface or ignore); the warn path emits a structured violations event (shape committed now for the future governance app — see §8).
- Kind classifier `retry`: bounded by existing M3 caps; repeated near-miss records a `near_miss` census entry (no infinite loop).
- Chain enforcement: severity read from the shared seam (`enforcement_for_chain`) — the rewire behavior (`validate_and_rewire`) stays BEHAVIORALLY equivalent (graph-visible outcomes; battery against committed baseline), never asserted byte-identical (serialization equality breaks on refactor).

---

## 7. Detailed E2E Test Cases

Fleshed out from scope E2E-1…E2E-7; each maps to test-design #1898 surfaces. **Test fixtures** (new files in this epic, under `tests/fixtures/expansion-epic/`): `rules_with_why.txt` (a rule stated WITH reasoning — "destructive actions require a verbal token acknowledgement because a prior incident…"), `rules_no_reasoning.txt` (same rule, no reasoning), `near_miss.txt` (content confusable between `agent-ops:rule` and core `standard`). **Mock models** use the existing MockModel precedent (injected in tests; no network). **Cleanup rule for all scenarios:** each run uses a fresh scratch graph and removes `/tmp` pack dirs / deletes `:PackManifest` nodes it created — the "starter 5" count must never drift across runs.

### E2E-1: Fresh self-host install loads starter packs (surface 1–2, CI smoke)
**Setup:** (a) clean venv install of the BUILT wheel (no source tree) — `tests/sample_transcript.txt` is shipped via package-data (one-line pyproject addition; also fixes the documented `mine-conversation tests/sample_transcript.txt` example on wheel installs); (b) `docker compose up` from the BUILT selfhost image; (c) boot the BUILT **hosted** image (`Dockerfile.hosted`) — the hosted surface must not silently ship without packs either.
**Steps:** on each surface: `PackRegistry(<packaged dir>).load_all()` → assert count ≥5 (dev, marketing, product-strategy, pm, agent-ops); then `tortoise mine-conversation tests/sample_transcript.txt --db <scratch graph>` (deterministic offline rule path).
**Assert:** registry count ≥5 on wheel AND selfhost image AND hosted image (the G1 guard on every surface); mining exits 0 and emits points (content-dependent kind assertions live in E2E-3/E2E-4 where fixtures control content — E2E-1 only proves the pipeline runs with the packaged catalog).
**Fail:** publish blocked (CI gate).

### E2E-2: Self-host operator configures a custom pack directory (surface 3)
**Setup:** `/tmp/packs/tenant-ops/manifest.yaml` = a valid minimal manifest scaffolded from `packs/_template/manifest.yaml` (namespace `tenant-ops`, one objectKind `contract`); second run with `/tmp/packs/broken/manifest.yaml` violating a rule (kind name `BadCase` — camelCase violation).
**Steps:** daemon with `TORTOISE_PACKS_DIR=/tmp/packs` → restart → `tortoise_packs_list`; also empty-dir and missing-dir variants.
**Assert:** user-visible outcome — `tortoise_packs_list` shows the valid custom pack alongside the starter 5 and the startup log confirms the load; the malformed pack is ABSENT from the list while the others load, with a startup warning (`registry.errors` kept as a supplementary diagnostic only); empty/missing dir → warn + packaged fallback (never silent empty).

### E2E-3: Author scaffolds, validates, uses a pack (surface 6 + docs delivery)
**Setup:** CLI install, no repo checkout; content sample matching the fixture pack's extraction config (sourceTypes conversation).
**Steps:** `tortoise pack new mydomain` → add a kind → `tortoise pack validate <dir>` (clean); mutate (camelCase violation) → `pack validate` (broken, actionable message) → install via packs dir → capture+mine matching content with a mock model → verify kinds minted. **Docs delivery assertion:** the built artifact ships `docs/EXPANSION_PACKS.md` (required sections: manifest capabilities, enforcement ladder, chains, memory_granularity, testing recipe) and both quickstarts reference packs (link check).
**Assert:** scaffold validates; clean pack passes; broken pack fails with actionable message; extraction mints `mydomain` kinds on matching content; docs artifact present with required sections + live quickstart references.

### E2E-4: Rules-with-why lifecycle (agent-ops pack) (surface 11)
**Setup:** fresh install (agent-ops active by default); transcript fixture `rules_with_why.txt`.
**Steps:** `tortoise mine-conversation tests/fixtures/expansion-epic/rules_with_why.txt --db <scratch graph>` (mock model) → assert rule Object + rationale Points + groundedIn IMPL edges linked to the session Event (query via `tortoise list` / `tortoise search`); then agent rewrite: create the new rule Point via MCP `tortoise_create_point` → `tortoise_supersede old→new` (MCP against the daemon — harness: the existing MCP test client; observation queries: `tortoise_get_confidence`, `tortoise_get_events` for the status projection).
**Assert (happy path):** supersede re-propagates EP confidence (cascade per `tortoise/ep.py` §10.5 — `_mark_dirty` → reverse-BFS → re-persist), the old rule retains its argument tree (rationale Points attached via provenance), status projection shows the new rule live.
**Assert (negative variants):** (a) `rules_no_reasoning.txt` → rationale NOT minted, chain-completeness warning produced (chain enforcement warn); (b) supersede with a failed re-propagation (unreachable graph) → contested-claim detection surfaces elevated variance (`get_contested_claims`) — the failure path is asserted, not just the happy path.

### E2E-5: Enforcement takes effect (surface 10)
**Setup:** agent-ops pack declares `enforcement: retry` on `rule`; content fixture `near_miss.txt` confusable between `agent-ops:rule` and core `standard` (nearMisses from the pack's kindDefs).
**Steps:** run extraction with the mock model → observe classifier retry (bounded: `_COMPLETE_RETRIES = 2` → ≤3 attempts, extractor_v2 M3 caps) → near-miss classified or recorded (`near_miss` census); then SDK `create_operator` with an undeclared relation pair.
**Assert:** retry bounded (no infinite loop); undeclared relation write proceeds WITH structured warning (warn-not-block); **regression guard is behavioral, not tautological:** the extraction battery runs against a COMMITTED baseline (golden outputs in `battery/`, snapshot recorded at this epic's start) — assert graph-visible rewire OUTCOMES for non-enforcement packs are unchanged (not byte-identical internal serialization, which breaks on refactor).

### E2E-6: Hosted per-tenant custom packs are isolated (surface 7–8)
**Setup:** two provisioned hosted teams A and B (existing provision path, `tt_` keys); valid tenant manifest = `tenant-ops` (NOT in the starter set — the agent-ops sketch in this doc is reserved and would correctly 422, so it is not the 201 fixture).
**Steps:** A: `POST /v1/packs/manifests` (valid) → 201; `GET /v1/packs` AND hosted MCP `tortoise_packs_list` (tenant-scoped view) show it for A; B sees nothing on both surfaces (D6 masking); A uploads: malformed → 422, namespace `dev` → 422 reserved, connector entrypoint → 422 ontology-only, >64KB → 413; concurrent double-upload (sync barrier) → exactly one activation (idempotent MERGE + #1307 lock); prompt-inspection probe: A's kinds appear in A's extraction prompts (test hook into `build_master_list`) and NOT in B's.
**Assert:** all status codes; cross-tenant negative on REST, MCP, AND extraction prompts; single activation under concurrency.

### E2E-7: Export/import carries pack configuration (surface 9)
**Setup:** self-host scratch graph with the `tenant-ops` custom pack installed and data minted under it.
**Steps:** `tortoise export --db <graph> --output graph.tortoise` → decrypt-verify the artifact (`verify_blob` / decrypt step in the export tooling) → assert `pack_config` block present INSIDE the encrypted payload → `POST /v1/teams/{team_id}/import` (owner session JWT, `X-Tortoise-Import-Key` header, artifact bytes — endpoint contract in §6) → verify the hosted graph answers queries with `tenant-ops` vocabulary (structure parity incl. activation) → re-import → `{imported: false, already: true}`.
**Assert:** pack config carried (or loud mismatch error on a pre-v1.1 artifact — fixture: hand-built v1.0 artifact containing a `tenant-ops`-typed point → import 422s loudly, never silent partial); idempotent re-import; parity preserved.

---

## 8. Coherence Review + Risk Analysis

### Cross-substep coherence checkpoints (pre-review)
- **Scope ↔ Plan:** all 7 in-scope items have a journey (J1–J7), a workflow (WF-1–WF-6), interfaces (§6), and detailed tests (E2E-1–7). Docs slice anchored at J3 exit + E2E-3 delivery assertion.
- **Test-design (#1898) ↔ Plan:** all 12 surfaces map to ≥1 detailed E2E + a verification-checklist row. Surface 8 (hosted upload) covers E2E-6; surface 9 (export/import) covers E2E-7.
- **Data model ↔ Workflows:** `:PackManifest` supports WF-4; `PackInstall.source='custom'` referential integrity; #1154 consumer wiring enumerated (domain_loader accessors + extractor master-list + SDK write path).
- **Interfaces ↔ Architecture:** env/CLI/MCP/REST/artifact contracts match the component boundaries; no surface in §5 lacks a contract in §6.
- **Journeys ↔ E2E:** every journey edge (no-reasoning, env-typo, reserved namespace, ontology-only, oversized, concurrency, pre-v1.1 artifact) has a test assertion.

### Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| R1 | Packaging regression recurs (packs missing from a future artifact) | medium | high (silent feature loss — the G1 class) | CI smoke on built wheel + both images is a publish GATE (E2E-1); committed once, enforced thereafter |
| R2 | Hosted custom-pack upload becomes an abuse/DoS surface | low | high | 64KB cap + rate limit (429, abuse.py precedent) + validation before any write; ontology-only v1 (no code entrypoints) |
| R3 | #1154 singleton leak resurfaces via a missed consumer | medium | high (cross-tenant contamination) | Consumer wiring enumerated in §4; cross-tenant negative test on REST + MCP + extraction prompts (E2E-6) |
| R4 | Enforcement wiring regresses extraction quality (over-constraint → refusal) | medium | medium | warn-not-block default; `retry` bounded (M3 caps); behavioral battery baseline committed at epic start (E2E-5) |
| R5 | Export/import version bump breaks existing importers or silently drops pack config | low | medium | Additive v1.1 block inside the encrypted payload; loud mismatch on pre-v1.1 (E2E-7); idempotent re-import |
| R6 | Catalog relocation breaks the dev (git-clone) workflow | low | medium | Resolution-order fallback (env → packaged → repo root); dev-regression test in E2E-2 surface 4; no hardcoded relative-path survivors |
| R7 | Agent-ops pack content drifts from the schema (memory_granularity placement bug class) | medium | medium | Template updated in the same slice (WF-3); validator extended to reject unknown top-level keys? — NO, leave open (backward compat); instead: extractor-level test asserting memory_granularity reaches the value brief |
| R8 | Starter-set change (agent-ops added) breaks existing tenant installs | low | low | Additive-only activation (PackInstall MERGE); `TORTOISE_STARTER_PACKS` env override; **convergence mechanism is named: `ensure_tenant_packs` runs on pack-list/extraction access (the existing self-heal read path) — asserted by an upgrade test: an existing tenant with the old 4-pack starter set converges to agent-ops active after upgrade with no manual intervention** |
| R9 | Parallel subagents collide on the packaging files (Dockerfiles, pyproject) | low | medium | Worktree-per-agent isolation (issue-workflow gate); one owner per slice |
| R10 | Agent workflow targets a hosted-only MCP tool on self-host (parity break) | medium | medium | Deployment-gated tool visibility: `tortoise_pack_install` registered ONLY on hosted; self-host exposes the filesystem+CLI path (pack new/validate + packs dir) instead — the authoring guide documents the correct surface per deployment. E2E asserts the DOCUMENTED agent install workflow (install → activate → list) succeeds on BOTH surfaces with equivalent outcomes (self-host: E2E-3; hosted: E2E-6) |
| R11 | Doc drift after ship (EXPANSION_PACKS.md / quickstarts vs schema + behavior) | low | medium | Doc-consistency check in the packaging CI gate (R1 gate re-used): validates EXPANSION_PACKS.md required sections + quickstart pack references resolve; ONTOLOGY §9 example updated to the v3 manifest shape in the same slice (no third copy of the format — guide = behavior, template/§9 = schema) |

### Improvement opportunities (noted, not committed)
- Shared-validator exposure as an MCP dry-run tool (`tortoise_pack_dryrun`) — deferred (post-epic, follows the 2026-08-05 Layer-2 pre-flight pattern).
- **Violations-event shape committed NOW** (cheap structured log line in the warn-not-block path: `{event: violation, code, kind/relation, pack, actor, ts}`) so the future governance app (tiered D4) has a stable contract — the plan's §6 enforcement contract references it.
- Quick wins folded into slices: `pack new` rejects reserved namespaces at scaffold time (CLI guard mirrors the hosted 422); slice 2 (authoring CLI) is thin glue — `PackRegistry._validate` + `_validate_cross_pack_refs` already implement validation; tenant-view compile reuses `compile_value_brief(packs_dir=...)`.

### Plan readiness
Plan complete for decomposition. Child issues will be generated MECE-first from the 7 in-scope capabilities with per-issue verification checklists derived from #1898 surfaces.
