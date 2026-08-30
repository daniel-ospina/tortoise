# Expansion Packs — Authoring Guide

> Epic #1891 (#1932) · Applies to Tortoise self-hosted and hosted · Companion to `packs/_template/manifest.yaml` (the machine-checkable schema) and ONTOLOGY §9 (the canonical spec). This guide is about **behavior** — what a pack expresses, how to test it, and when to write one.

## What is an expansion pack?

An expansion pack extends Tortoise's core ontology with a **domain vocabulary** plus the **business logic** that governs it. Packs are declarative YAML (`manifest.yaml`) — no code runs on load. They give the extractor the kinds it can mint, the chains it must respect, and the `memory_granularity` guidance for what to keep vs strip.

The four starter packs shipped by default: `dev`, `marketing`, `product-strategy`, `pm`, and `agent-ops` (rules-with-why). Your custom packs install alongside them.

## When to write a pack (vs using core kinds)

- **Write a pack** when your domain has recurring nouns the core ontology doesn't name (`contract`, `rule`, `epic`, `useCase`), a relationship structure worth enforcing (chains), or extractor guidance worth declaring (`memory_granularity`, `retry` on confusable kinds).
- **Don't write a pack** for one-off concepts — a `statement` Point about anything is always valid. Packs pay for themselves with repeated extraction over a bounded vocabulary.

## Anatomy of a manifest

```yaml
namespace: mydomain          # REQUIRED — camelCase, no colons, not a reserved starter name
name: My Domain Pack
version: "0.1.0"
tier: free                   # free | premium
description: One paragraph.

ontology:
  extends: core              # always 'core'
  objectKinds: [contract]    # entity nouns → Object nodes
  pointKinds: [rationale]    # claims/decisions → Point nodes
  eventKinds: [ruleRevised]  # what happened → Event nodes
  documentKinds: []          # document subclasses
  subclassOf: {}             # e.g. {epic: Project}
  equivalentTo: {}           # e.g. {issue: [pm:issue]} — cross-pack identity
  kindDefs:                  # extractor prompt material (description IS prompt text)
    contract:
      description: A commercial agreement between parties
      synonyms: [deal, agreement]
      nearMisses: [standard]   # confusable kinds → classifier guidance
  relations:                 # epistemic edge declarations (IMPL/NAND)
    - predicate: groundedIn
      mechanism: IMPL
      fromKind: mydomain:rule
      toKind: mydomain:rationale
      extractable: true      # true → the extractor may mint this edge
  chains:                    # ordered kind paths the extractor must respect
    - id: ruleLifecycle
      steps: [rule, rationale, ruleRevised]
      enforcement: warn      # warn | retry (block is reserved)
  memory_granularity: 'Durable: ... Ephemeral: ...'   # UNDER ontology: (top-level is ignored)

extraction:
  active: true
  sourceTypes: [conversation]   # conversation | document | ...
  enforcement:
    kinds:
      rule: retry               # kind-level retry on near-miss
```

**The two most common mistakes:**

1. **`memory_granularity` at the top level** — the value-brief compiler reads `ontology.memory_granularity`. Top-level is silently ignored. (The `_template` teaches the correct placement — copy it.)
2. **A `kindDefs` key not declared in `objectKinds`/`pointKinds`/etc.** — the validator rejects it. Every kindDefs key must be a declared kind.

## The enforcement ladder (warn | retry)

`warn` and `retry` are the operative levels (a hard `block` is reserved for future per-pack opt-in):

- **warn** (default) — the extractor emits a structured warning and proceeds. Use for soft constraints (chain bypasses, near-misses).
- **retry** — the kind classifier retries (bounded) on a near-miss before accepting a fallback classification. Use for kinds that are important and confusable (e.g. `rule` vs `standard`).

Resolution order for a kind: `kindDefs[].enforcement` → `extraction.enforcement.kinds` → `extraction.enforcement.default` → `warn`.

## Chains

A chain is an ordered kind path the extractor respects when connecting `about_entities`. Example: the agent-ops `ruleLifecycle` chain `[rule, rationale, ruleRevised]` — a rule must be grounded in its rationale. The chain enforcer rewires reverse-order pairs deterministically (never invents, never drops) and warns on violations. Steps resolve to your pack's kinds, core kinds, or exactly-one other pack's kinds (ambiguous bare names are validation errors — use `ns:kind`).

## Testing a pack

1. **Validate** — `tortoise pack validate <dir>` (or `--json` for machines). This runs the same schema + cross-pack validation as the daemon.
2. **Install** — self-hosted: put the pack under `TORTOISE_PACKS_DIR` and restart (or use the packaged `packs/` dir on a clone). Hosted: `POST /v1/packs/manifests` / `tortoise_pack_install` (ontology-only — no connectors/tools on tenant packs).
3. **Extract** — mine a representative transcript with the offline/mock model and check the minted kinds (`tortoise list-kinds`, queries on the graph). The extractor injects pack kinds into its prompts keyword-selectively — content must contain your domain's words for the vocabulary to activate.
4. **Iterate** — fix near-misses by adding `nearMisses` and `synonyms`; tighten chains with `enforcement: warn`; declare `retry` where the classifier hesitates.

**The offline testing recipe:** run extraction with a deterministic model (the test suite's `MockModel` / the CLI's offline default) — no network, no cost — and assert the kinds/edges the plan requires.

## Self-host vs hosted

| Surface | Install | Author |
|---|---|---|
| Self-host | `TORTOISE_PACKS_DIR` (filesystem) | `tortoise pack new` / `pack validate` (CLI) |
| Hosted | `POST /v1/packs/manifests` / `tortoise_pack_install` (MCP) | same manifest format; ontology-only v1 (no connectors/tools) |

Both surfaces run the same shared validator. A pack that validates locally installs on either.

## Reserved namespaces

`dev`, `pm`, `marketing`, `product-strategy`, `agent-ops` are reserved starter namespaces — both the CLI scaffold and the hosted upload reject them (one guard, both surfaces).

## Reference

- Machine-checkable schema: `packs/_template/manifest.yaml` (the template scaffolds from this)
- Canonical spec: `docs/ONTOLOGY.md` §9
- Governance research (enforcement layering): `docs/research/2026-08-05-expansion-pack-governance-surfaces.md`
- Worked example: `packs/agent-ops/manifest.yaml` (rules-with-why)
