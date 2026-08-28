---
title: "Epic #1891 — Test-Design: Integration Surface Map"
type: engineering
domain: platform
doc_status: draft
created: 2026-08-28
ownedBy: epistemic-team
---

> **Findings date:** 2026-08-28
> Input: epic scope `03-scope.md` customer value map + E2E cases E2E-1…E2E-7.

# Integration Surface Map — Expansion packs as a configurable product

## Integration Surface Map

| # | Surface | Type | Data Flow | Test Layer | Contract | Key Failure Modes |
|---|---------|------|-----------|-----------|----------|-------------------|
| 1 | Wheel build (`pyproject.toml` package-data; `package_dir` mapping ships `tortoise/packs/`) | Build artifact | Out | CI smoke (integration) | `pip install <wheel>` in clean venv → `PackRegistry.load_all() >= 5` (starter set incl. agent-ops); packs resolvable from installed location | Pack files missing from wheel (silent — this is the G1 defect class); glob too narrow; sdist/wheel divergence |
| 2 | Docker images (`Dockerfile.selfhost`, `Dockerfile.hosted` — `COPY packs/`) | Build artifact | Out | CI smoke (e2e) | Boot built image → `tortoise_packs_list`/registry reports starter set ≥5; extraction prompt includes pack vocabulary | Image without packs (current G1); COPY path mismatch with registry resolution; hosted image drifts from selfhost |
| 3 | Registry resolution (`domain_loader._get_registry`, `TORTOISE_PACKS_DIR` env, fallback chain) | State/Config | In | Unit + integration | Resolution order: env → packaged default → repo root (dev); unknown starter names warn-skip | Env var unset/missing dir → wrong fallback; typo dir silently degrades to empty; env vs packaged precedence regressed |
| 4 | Pack catalog relocation (repo root `packs/` → packaged tree) | State/Config | Both | Integration | Git-clone dev path still resolves repo-root packs (no dev regression); packaged path resolves packaged packs | Dev workflow broken (editable install); duplicate pack namespaces across dirs; hardcoded relative path survivors |
| 5 | `PackRegistry` validation (schema + cross-pack refs, R-16 isolation) | DB-independent logic | In | Unit (existing) + integration | Malformed manifest → that pack isolated, others load; cross-pack refs validated; errors queryable | Validator regression; isolation loop dropping healthy packs (prior bug class); new manifest v3.1 fields unvalidated |
| 6 | CLI `tortoise pack new` / `pack validate` (`tortoise/__main__.py`) | Internal surface | In | Integration | Scaffold matches `_template`; validate reuses `_validate`; namespace rules (camelCase, no colon, no canonical conflict) enforced | Scaffold emits invalid manifest; namespace collision; validate false-negative on cross-pack refs; CLI crash on empty dir |
| 7 | MCP tool surface (`tortoise_packs_list` extended; hosted install tool) | API (MCP) | Both | Integration + contract | `packs_list` returns active+starter packs with metadata; install tool validates then activates; **additive team group for the tenant-scoped view (admin retains full view — gating additive, not a regression)** | Read-only paths regressed; install tool bypasses validation; tool-group gating broken; masking (D6) broken |
| 8 | Hosted per-tenant pack upload (`POST /v1/packs` or equivalent) | API (HTTP) | In | Integration + contract | Manifest upload → schema+cross-pack validation → per-tenant activation (PackInstall); ontology-only v1 (connector/tool entrypoints rejected); size limit | Malformed YAML → 5xx instead of 4xx; oversized upload; tenant A pack visible to B (isolation); concurrent provision race (pack_state lock precedent #1307); #1154 singleton leak |
| 9 | Export/import pack config (`tortoise-export-v1` + hosted import) | External artifact | Both | E2E (extends `test_parity_export_import`) | Export artifact includes pack-config block; hosted import applies or fails loudly | Silent partial import (custom pack lost without error); artifact version bump breaks old importers; pack config not round-trip stable |
| 10 | Enforcement: kind classifier `retry` + `create_operator` relation validation (ONE shared `resolve_enforcement` seam) | Internal logic | Both | Unit + integration (battery) | `retry` fires on near-miss kinds, bounded (M3 caps); undeclared relation write warns-not-blocks + structured warning; **chain pass behaviorally equivalent (graph-visible outcomes vs committed battery baseline — never byte-identical serialization)** | Retry loop unbounded; warn becomes block; chain rewire behavior changes (regression to integrated #1695 pass); enforcement_for* still dead code |
| 11 | Agent-ops rules-with-why pack (manifest + extraction behavior) | Ontology content | Both | Integration (extraction with offline mock models) + ontology validation | `rule`/`rationale`/`ruleRevised`/chain/`memory_granularity` valid per registry; mining a rules-with-why session mints rule + rationale + IMPL edge; supersede re-propagates EP | Manifest fails validation; extractor mints wrong kinds; supersede drops argument tree; memory_granularity not in prompts |
| 12 | `docs/EXPANSION_PACKS.md` + quickstart references | Docs | Out | Content check (lint + link) | Authoring guide exists, referenced from both quickstarts, template-example consistent | Doc drift vs manifest schema; quickstart links dead |

## Bug Pattern Flags

- **Silent function skips (HIGH — the G1 defect class):** packaging shipped without packs silently for the entire product history because dev runs from a clone. **Required verification:** CI smoke on the *built* wheel AND *built* image (surfaces 1–2), not the source tree. This is the regression guard that must exist before the epic closes.
- **Race conditions (MEDIUM):** hosted per-tenant pack install can race with provisioning (pack_state #1307 precedent — 8-thread duplicate PackInstall). **Required verification:** concurrent-install test asserting exactly one activation per (tenant, namespace); reuse the existing per-(graph, namespace) lock.
- **Conditional guards (MEDIUM):** resolution-order chain (env → packaged → repo root) is a conditional-guard surface. **Required verification:** boundary tests for each fallback trigger (env unset, env dir missing, packaged missing, both present).
- **N+1 / SQL logic:** none — pack state uses single MERGE statements (no Postgres functions in this epic).

## Checklist Notes

- **Atomicity:** hosted activation uses existing idempotent additive MERGE (PackInstall) — re-run is a no-op; test double-upload.
- **Boundary values:** manifest upload size limits (0-byte, max, max+1); namespace length/character edges; pack count at 0/1/max.
- **Contract:** the hosted upload endpoint contract (request shape, validation error shape, activation response) must be fixed at Plan stage — it is the one surface with a genuinely new contract.
- **Failure modes needing explicit cases:** wheel-missing-packs (regression smoke), tenant-cross-read (isolation negative), loud-mismatch path for export/import (not silent), retry-loop bound.
