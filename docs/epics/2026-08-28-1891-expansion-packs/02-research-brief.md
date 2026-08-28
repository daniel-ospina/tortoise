---
title: "Epic #1891 — Research Brief: Expansion packs as a configurable product"
type: research
domain: product
doc_status: draft
created: 2026-08-28
ownedBy: epistemic-team
---

> **Findings date:** 2026-08-28

# Epic Research Brief — Expansion packs as a configurable product (hosted + self-hosted)

> Issue: #1891 · Pipeline stage: Research · Skill: epic-research

## Strategy Context

- **The differentiation is the epistemic layer.** Competitor memory products (internal registry: mem0, Zep, Hindsight, Honcho) are flat or graph-based *semantic* memory: mem0 has "no ontology — flat key-value memory," "facts are asserted, not contested," "no belief propagation — no mechanism for why should I believe this" (product/competition/mem0.md); Zep is a temporal knowledge graph but without an epistemic/argument layer. The "why behind a rule" is precisely the axis none of them model. Expansion packs are the vehicle that makes this extensible per domain without the core team building every vertical ontology.
- **Letta's memory model is block-based, flat, and why-less** (external, letta.com/docs + blog): memory = labeled blocks (label, value, limit, description), read-write by default, edited by agents via `memory_rethink`/`memory_replace`/`memory_insert`, attachable/shared across agents, ≤20 blocks per agent recommended. The prospect's "rules" are block values; the *situation + logic that created each rule* has no representation — it lives in the conversation history. Tortoise's state-Object + statement-Point + IMPL/CORRECTS model is the direct structural answer, and an agent-ops pack is the packaged pattern.
- **Channel/BSL mechanics:** BSL grant (free < $5M revenue self-host) matches a partner-led funnel; resale of hosted Tortoise is excluded, so the channel path runs through hosted sub-tenancy (#557). Custom packs on hosted (this epic's slice 4) is a prerequisite for partners to offer vertical memory products.
- **Pricing readiness:** extraction cost data at scale is unmeasured; the rate card (separate issue) is blocked on it. This epic does not price packs; it makes them installable/configurable.

## UX Pattern Research

- **Plugin/extension trust model — VS Code is the precedent** (external): install-time publisher-trust prompt (1.97+), signature verification at install, enterprise allowlist (`trustedExtensionAuthAccess` / `VSCODE_TRUSTED_EXTENSIONS`). Transfer to Tortoise hosted custom packs: since pack manifests are **declarative and never executed on load** (verified in `pack_registry.py` — parse-only), the trust surface is much smaller than VS Code. The remaining code surfaces are **connector/tool entrypoints** (code references). v1 recommendation: hosted custom packs = **ontology-only** (no connectors/tools) — zero execution surface; connectors/tools stay allowlisted (starter packs or reviewed).
- **Self-host config precedent:** env-var-driven config with a packaged default is the existing pattern (`TORTOISE_STARTER_PACKS`, `TORTOISE_ROUTING_CONFIG` fallback to packaged default). `TORTOISE_PACKS_DIR` follows the same shape — packaged defaults first, user override wins, missing/invalid falls back with a logged warning. This is the least-surprise UX for self-host operators.
- **Authoring UX gap:** the promise "tell an agent to build a pack" needs a scaffold (`tortoise pack new`) — the template (`packs/_template/manifest.yaml`) is the raw material; the onboarding wizard (#1643) has no pack step and neither quickstart mentions packs. Low-cost UX surface: CLI-first for v1 (dashboard pack UI is an explicit non-goal).
- **Anti-pattern:** marketplace/curation UI before local authoring works — distribution before creation inverts the funnel (VS Code shipped local extensions + sideloading before the Marketplace).

## Workflow Pattern Research

- **How config ships in packaged artifacts:** setuptools **discourages `data_files`** and recommends **package-data (files inside the package directory)** (packaging.python.org, setuptools docs). Current gap: `packs/` lives at repo root outside the package, and package-data ships only `config/routing.yaml`. Recommended shape: relocate the pack catalog under the package tree (e.g., `tortoise/packs/` shipped via package-data globs) with a resolution order: `TORTOISE_PACKS_DIR` (user) → packaged default → repo root (dev). Both Dockerfiles `COPY` the same tree.
- **Operationalization precedent — enforcement is the 2026-08-05 5/5-decision roadmap:** layered defense (manifest constraints → SDK write-time validation → MCP pre-flight → pack skills → governance app), warn-not-block default, enforcement lives in the SDK (shared write surface), not MCP-only. Only the chain piece (`chain_enforcer.validate_and_rewire`) is shipped; `enforcement_for*` has zero call sites (verified). Wiring must not regress the now-integrated deterministic chain pass (classify-later #1695 completed 2026-08-27).
- **Hosted per-tenant config precedent (#318):** shared catalog + per-tenant `PackInstall` activation records in the tenant graph; `GET /v1/packs` + `tortoise_packs_list` read-only; D6 masking. Custom packs extend this: per-tenant manifest upload → validate (schema + cross-pack) → serve from the tenant's namespace. Must first resolve #1154 (process-global pack registry singletons = latent cross-tenant leak).
- **Export/import carryover:** `tortoise export` → hosted import preserves points/edges; pack config is not in `tortoise-export-v1` (verified: export captures graph content). Slice-4 must define pack-config representation (or an explicit loud mismatch).

## Tech Stack Research

- **Packaging:** `[tool.setuptools.package-data]` globs under a package dir (recommended); `data_files` discouraged. Both Dockerfiles need `COPY packs/` (or the relocated `tortoise/packs/`). CI smoke test on the built wheel + image is the regression guard that was missing (this gap was silent because dev runs from a clone — `tortoise/__main__.py` explicitly acknowledges the "packs-less environment").
- **YAML validation:** `PackRegistry._validate` already covers schema + cross-pack referential integrity + per-pack isolation (R-16). No new library needed for v1 authoring (CLI reuses it). Hosted upload adds: size limits, per-tenant storage, and re-run of the same validation in the request path (shared validator = single source).
- **CLI:** argparse-based CLI (`tortoise/__main__.py`) — `tortoise pack new`/`pack validate` follow the existing `validate --domain` shape; no new framework needed.
- **Enforcement hook point:** `create_operator` (SDK) is the write-path validation surface per the 2026-08-05 decision; kind-classifier (`kind_classifier.py`) is the extraction-side hook for `retry`.
- **Risk note (adversarial):** over-constraint causes extraction refusal / entity inflation (arXiv 2605.21974, cited in the 2026-08-05 governance research) — enforcement must stay warn-not-block by default.

## Assumptions Register

| Assumption | Confidence | Source | Validation Plan |
|---|---|---|---|
| The prospect follows through on self-host + building a pack | medium | Sales call 2026-08-28 | Slice-1 demo pack + packaging fix; check trial completion within 30 days |
| Custom-pack authoring is a real need beyond this prospect (hacker-type early users build their own) | medium | Product thesis (connectors precedent); prospect statement | Beta cohort: count pack-related support asks / PRs once authoring ships |
| Packaging fix is bounded (~days) | high | Verified gap is a COPY + package-data + env override | Slice-1 CI smoke test |
| Enforcement wiring improves extraction without regressing the integrated chain pass | medium | Governance research warn-not-block + #1695 completion | A/B on existing extraction evals (battery) before/after |
| Hosted custom packs are demanded before sub-tenancy | low | No paying hosted tenants; #557 is the channel path | Gate slice 4 on a hosted paying tenant or a signed partner |
| Hosted custom packs can be ontology-only for v1 (no connectors/tools) | medium | Manifest is parse-only; connector/tool entrypoints are the only code surface | Scope slice 4; escalate if a prospect needs tools |
| Pack catalog can relocate into the package tree without breaking dev workflow | high | Registry takes a dir path; dev default = repo root | Migration check in slice 1 (git-clone path must still resolve repo-root packs) |

## Raw Notes

- `[2026-08-28 — epic-research]` Tortoise repo (origin/main @ 6e80e06f) verified: `pack_registry.py` parse-only load, no code execution; `enforcement_for*` zero call sites; `packs/` absent from `Dockerfile.selfhost`/`Dockerfile.hosted` and from wheel package-data (only `config/routing.yaml`); `_PACKS_DIR` test-only injection, no env var; export (`tortoise-export-v1`) carries points/edges, not pack config; `tortoise_packs_list` (admin group) is the only pack MCP tool; dashboard has zero pack code. Evidence: repo grep + file reads.
- `[2026-08-28 — epic-research]` Internal prior research deduped (not re-queried): `docs/research/2026-08-05-expansion-pack-governance-surfaces.md` (enforcement layering, 5/5 decisions — Section "Workflow Pattern Research" builds on it); `product/competition/*` (mem0, Zep, Hindsight, Honcho — Section "Strategy Context" builds on it); #318 pack-isolation plan/scoping; #557 sub-tenancy scoping.
- `[2026-08-28 — external]` Letta memory blocks (docs.letta.com/v1-sdk/memory/memory-blocks/, memory-blocks blog): blocks = label/value/limit/description, read-write by default, agent-editable via memory tools, attach/share across agents, <20 blocks recommended. Source-tag: LETTA-MEM.
- `[2026-08-28 — external]` Python packaging: setuptools discourages `data_files`, recommends package-data inside package dirs (packaging.python.org/guides/distributing-packages-using-setuptools/; setuptools.pypa.io pyproject_config + datafiles pages). Source-tag: PY-PKG.
- `[2026-08-28 — external]` VS Code extension trust: install-time publisher-trust prompt, signature verification, enterprise allowlist (code.visualstudio.com extension-runtime-security; developer.microsoft.com marketplace security blog). Source-tag: VSC-TRUST.
- `[2026-08-28 — external]` Adversarial: over-constraint in KG construction causes extraction refusal/entity inflation (arXiv 2605.21974) — carried from 2026-08-05 governance research Raw Notes lineage; keep warn-not-block.
