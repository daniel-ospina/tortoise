---
title: "Epic #1891 — Scope: Expansion packs as a configurable product"
type: decisions
domain: product
doc_status: draft
created: 2026-08-28
ownedBy: epistemic-team
---

> **Findings date:** 2026-08-28

# Epic Scope — Expansion packs as a configurable product (hosted + self-hosted)

> Issue: #1891 · Pipeline stage: Scope · Skill: epic-scope

## Axis Research Notes

Per issue #231 D11 — granular queries fired only where the brief is too broad for boundary decisions. All four complexity axes below are covered by the research brief at sufficient granularity; justified skips (no external queries spent):

| Axis (expected rating) | Justified skip — brief section |
|---|---|
| Architecture (high) | Tech Stack Research (setuptools package-data vs data_files; Docker COPY; shared validator) + Workflow Pattern Research (#318 per-tenant precedent, export/import carryover, #1154 singletons) |
| Ontology (medium) | Strategy Context (Letta block model, why-less memory) + UX Pattern Research (VS Code trust model → ontology-only v1) + Assumptions Register row 7 (catalog relocation) |
| UX (low) | UX Pattern Research (env-var config precedent, authoring UX gap, distribution-before-creation anti-pattern) |
| Accessibility (low) | CLI-first v1 with no new UI surface — no accessibility surface introduced (documented in Complexity below) |

## Scope Boundaries

### In Scope

1. **Pack catalog ships in all install artifacts** — wheel (`[tool.setuptools.package-data]` via `package_dir` mapping shipping `tortoise/packs/`), `Dockerfile.selfhost`, `Dockerfile.hosted` (each `COPY packs/`); pack catalog resolution order `TORTOISE_PACKS_DIR` → packaged default → repo root (dev). CI smoke test on the built wheel + both built images asserts `PackRegistry.load_all() >= 5` (the starter set incl. agent-ops). Also ships `tests/sample_transcript.txt` via package-data so the documented `mine-conversation` example works on wheel installs.
2. **`TORTOISE_PACKS_DIR` env override** (self-host custom pack directory) — honors the existing env-var + packaged-default pattern (`TORTOISE_STARTER_PACKS`, `TORTOISE_ROUTING_CONFIG`).
3. **Pack authoring tooling** — `tortoise pack new <namespace>` (scaffold from `_template`), `tortoise pack validate <dir>` (reuses `PackRegistry._validate` + cross-pack checks).
4. **`docs/EXPANSION_PACKS.md`** authoring guide (manifest capabilities, enforcement ladder, chains, memory_granularity, offline-model testing recipe) + quickstart references (both quickstarts get a pack mention).
5. **Agent-ops rules-with-why starter pack** — objectKind `rule` (subclass of core `standard`), pointKind `rationale`, relation `rule -IMPL-> rationale` (extractable), eventKind `ruleRevised`, chain `rule → rationale → event`, `memory_granularity` declaring the reasoning durable. Shipped as a starter pack on both surfaces.
6. **Enforcement ladder operationalization** — kind-level `retry` wired into the kind classifier; relation-level write validation hook in `create_operator` (warn-not-block, per 2026-08-05 D1/D2); chain enforcement reads chain severity levels. No regression to the integrated deterministic chain pass.
7. **Hosted per-tenant custom packs (slice 4)** — resolve #1154 (process-global registry singletons → per-tenant-safe); per-tenant manifest upload → validate (schema + cross-pack, shared validator) → serve from the tenant's namespace; **ontology-only v1** (connectors/tools entrypoints rejected in tenant uploads); `GET /v1/packs` + `tortoise_packs_list` extended to include tenant packs; cross-tenant isolation negative test. **Export/import carries pack config** (or explicit loud mismatch — never silent).

### Out of Scope

- **Dashboard pack-management UI** — defer to a future issue (sequenced after #557 sub-tenancy; D1 selection UI was explicitly deferred in #318). *Queue item pending demand — no issue filed.*
- **Pack marketplace / community distribution** — defer; tracked as a **queue item pending demand** (no issue yet — deliberately: no distribution before creation; re-open when ≥2 external pack authors request it).
- **Large-deploy rate card** — separate pricing issue (blocked on extraction cost data; queue item — the 2026-08-28 prospect request is the demand signal).
- **First-party Letta/chat-export connector** — defer; tracked as a **queue item pending demand** (the prospect said he'd bring his own exporter; file when a second customer needs it).
- **Pack version/update/removal governance** (beyond install record) — defer (tiered per #318 D3; `PackInstall.version` record already exists). *Queue item pending enterprise-tier demand.*
- **Governance app** (violations dashboard, overrides, kind lifecycle) from 2026-08-05 D4 — tiered later, not this epic. *Queue item — the violations-event shape committed by this epic's enforcement slice is its data contract.*
- **`block` enforcement level** — stays `warn`/`retry` only (adversarial over-constraint risk; block is per-pack opt-in at most). *Queue item pending demand + constraint-rule confidence.*

### Boundary Rationale

Cut principles: (a) **parity** — every in-scope capability works on both hosted and self-hosted (the requirement from the epic owner); (b) **revenue-first ordering** — Slice 1 (packaging + packs-dir + demo pack + docs) unblocks the live channel prospect and the silent-broken-default defect; (c) **no distribution before creation** — authoring tooling precedes any marketplace/UI; (d) **security gated** — hosted custom packs are ontology-only in v1 because connector/tool entrypoints are the only code-execution surface; (e) **deferred work always names its follow-up** — no dangling "later" without a target issue/epic.

## Customer Value Map

| Scoped Capability | User-Visible Value |
|-------------------|--------------------|
| Packs ship in wheel + Docker images | A new user can self-host the documented way and the extractor actually uses pack vocabulary — the "expansion packs exist" promise stops silently failing |
| `TORTOISE_PACKS_DIR` | A self-host operator adds a custom pack by pointing at a directory — no repo editing, survives upgrades |
| `tortoise pack new` / `pack validate` | A customer (or their agent) scaffolds a valid pack in minutes instead of hand-writing YAML blind |
| Authoring guide + quickstart references | A customer can learn what a pack expresses and how to test it without digging through source |
| Agent-ops rules-with-why pack | The exact sales-case pattern ships: agents store rules + the why behind them, and rewrite rules with the reasoning intact |
| Enforcement operationalized | Pack authors' declared business logic (retry near-misses, warn bad relations) actually takes effect — packs stop being advisory-only |
| Hosted per-tenant custom packs | A hosted tenant installs their own ontology per team; cross-tenant data stays isolated; a self-host prototype migrates to hosted with its vocabulary intact |

## Complexity Ratings

| Axis | Rating | Rationale |
|------|--------|-----------|
| UX | low | CLI-first authoring + env config; no new UI surface (dashboard explicitly out) — no accessibility impact |
| Architecture | high | Multi-surface: packaging, daemon resolution, MCP, hosted API, export/import; security surface on hosted uploads; #1154 singleton resolution; cross-system consistency |
| Ontology | medium | Pack manifest schema evolution (per-tenant custom packs, catalog relocation) + new agent-ops pack kinds/relations |
| Accessibility | low | No UI change; CLI output follows existing `--json` machine-contract conventions |

## High-Level E2E Test Cases

### E2E-1: Fresh self-host install loads starter packs
**Given:** a clean host with Docker, and a `docker compose up` Tortoise daemon built from the released image (wheel-install and hosted-image variants in detailed tests)
**When:** an agent calls `tortoise_packs_list` over MCP
**Then:** the starter set (dev, marketing, product-strategy, pm, **agent-ops**) is reported (CI smoke bound: registry ≥ **5** on the built wheel, selfhost image, and hosted image)
**And:** extraction prompts on a captured session contain pack vocabulary/chains (spot-check via a mining run)

> **Bound amendment (recorded):** the starter set grows from 4 to 5 — the agent-ops pack (in-scope item 5) ships and activates by default; all CI smoke bounds are ≥5. Also in-scope item 1 packaging includes shipping `tests/sample_transcript.txt` via package-data so the documented `mine-conversation` example works on wheel installs.

### E2E-2: Self-host operator configures a custom pack directory
**Given:** a running daemon with `TORTOISE_PACKS_DIR` set to a dir containing a valid custom pack
**When:** the daemon restarts and the agent lists packs
**Then:** the custom pack appears alongside the starter set
**And:** a malformed pack in that dir is isolated (registry still loads the rest; error surfaced in `errors`)

### E2E-3: Author scaffolds, validates, and uses a pack
**Given:** a self-host operator with the `tortoise` CLI
**When:** they run `tortoise pack new mydomain`, edit kinds, run `tortoise pack validate`
**Then:** scaffold validates against the registry schema
**And:** after install (packs dir + restart), extraction mints the pack's kinds on matching content

### E2E-4: Rules-with-why lifecycle (agent-ops pack)
**Given:** the agent-ops pack active and a session where a rule is stated with its reasoning
**When:** the session is captured and mined
**Then:** a `rule` Object and `rationale` Points with `IMPL` edges are created, linked to the session Event
**And:** superseding the rule (rewrite) re-propagates EP confidence and the old rule retains its argument tree

### E2E-5: Enforcement takes effect
**Given:** a pack declaring `enforcement: retry` on a kind and an undeclared relation write attempt
**When:** the classifier near-misses the kind, and an SDK caller creates the undeclared relation
**Then:** the classifier retries (and succeeds or records the near-miss), and the relation write warns-not-blocks with a structured warning
**And:** the deterministic chain pass behavior is unchanged (existing battery passes)

### E2E-6: Hosted per-tenant custom packs are isolated
**Given:** hosted with tenants A and B
**When:** tenant A uploads a valid custom manifest; tenant B attempts to read it; a malformed manifest is uploaded by A
**Then:** tenant A's pack is active and listed only for A; B sees nothing (masking); the malformed manifest is rejected with a validation error and never activates

### E2E-7: Export/import carries pack configuration
**Given:** a self-host graph with a custom pack installed
**When:** `tortoise export` produces an artifact and hosted import consumes it
**Then:** the custom pack's vocabulary is present on hosted (or the import fails loudly with an explicit pack-config mismatch — never a silent partial import)

## Epic Scope Ready for Review

**Scope:** 7 in-scope capabilities (packaging, packs-dir config, authoring CLI, authoring docs, agent-ops pack, enforcement, hosted custom packs + export/import carryover); 7 explicit out-of-scope items each with a named deferral target.
**Customer value map:** 7 capabilities mapped, each with a one-line user-visible value.
**E2E test cases:** 7 drafted (E2E-1…E2E-7), behavioral not presentational.
**Complexity:** UX low, Architecture high, Ontology medium, Accessibility low.

Review the scope boundaries, customer value map, and E2E test cases. Reply "proceed" to continue to detailed planning, or give feedback.
