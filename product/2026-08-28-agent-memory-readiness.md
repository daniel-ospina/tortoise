---
title: "Product Readiness — Agent Memory with Rules-Why (Letta-founder prospect)"
type: analysis
domain: product
doc_status: draft
created: 2026-08-28
aboutSubjects: tortoise
aboutObjects: tortoise-selfhost, expansion-packs, tortoise-mcp
---

# Product Readiness — "Agent memory + the why behind agent rules" use case

## Source

Sales conversation (2026-08-28) with a Letta-classic-based agent-harness operator
(a prospective channel partner). Prospect profile:

- Runs stateful agents with infinite memory (Letta legacy fork on Postgres;
  sleep-time shadow agents for memory normalization — a modality they have
  already built and are actively looking to replace parts of).
- Wants: agent **rules/job-descriptions that agents can rewrite themselves**,
  **plus the WHY behind each rule** (the situation + logic that created it) so
  rewrites stay accurate and drift is correctable ("if it understands the logic
  behind it… it can go back and examine that instance and rewrite the rule").
- Wants to map: "what's current + the whys around that + events associated
  with them" — i.e., current-state, supporting argumentation, and a timeline.
- Interested in: self-hosted first, hosted later; local/sovereign inference
  (Venice TEE), multi-user deployment; a 5,000-seat hospital deal via them
  (channel); needs a rate card for large self-host deployments.
- Use case to test: 4 years of multi-project conversational history (companion
  agent → project coordinator / product-manager spec-builder), where memory
  curation must not degrade into "diluted average of blobs."

This doc assesses readiness to serve that use case end-to-end, with an
emphasis on the **expansion-pack-as-a-product** question the conversation
raised. Everything below was verified against `origin/main` @ `6e80e06f`.

---

## 1. Verdict

| Dimension | Readiness | One-line |
|---|---|---|
| Core epistemic engine (the "why") | **Strong — it is the thesis** | Points + IMPL/NAND/mitigations + EP confidence + cascading invalidation + supersede all shipped and tested. |
| Rules-with-why modeling primitives | **Strong primitives, no packaged pattern** | Core ontology has `role`/`standard`/`agreement`/`workflow` + `statement` Points + decision-as-event — everything the use case needs is expressible; **no pack or docs teach the pattern**. |
| Memory curation / "sleep-time" loop | **Strong** | `tortoise/dream.py` is the explicit memory-consolidation analogue (`tortoise_dream` / `tortoise_dream_health` MCP tools); extractor is model-agnostic (ollama/deepseek/…). |
| Self-host onboarding | **Good** | 5-min docker-compose path, embedded single-writer eval path, migration to hosted (`tortoise export/import`). |
| Expansion pack authoring by customers | **Not ready — the pitch is broken in the recommended install path** | Packs **ship only in a git clone**. Neither Docker image copies `packs/`, and the wheel ships none (`pyproject.toml` package-data = `config/routing.yaml` only). The recommended installs (`docker compose up`, `pip install tortoise-graph`) run with an **empty pack registry**. |
| Expansion pack install surface | **Read-only** | One MCP tool (`tortoise_packs_list`, admin group) + `GET /v1/packs`. No create/install/update surface anywhere. Hosted tenants cannot add packs at all (by design, #318 D2; custom-pack authoring = #1154, unbuilt). |
| Enforcement of pack "business logic" | **Partial** | Chain enforcement (`chain_enforcer.validate_and_rewire`) is real and runs on every extraction arm. The manifest `enforcement` ladder (`warn|retry|block`) is validated at load but **never consumed** (`PackManifest.enforcement_for*` have zero call sites). Relation-level validation (predicate/kind-pair/cardinality on write) is not wired. |

**Bottom line:** the graph can absolutely serve "rules + why + current-state +
timeline" — that is the product's core hypothesis. What is *not* ready is the
**customer-facing pack story**: a prospect cannot, today, self-host the
recommended way and have packs at all, and cannot create/install their own
pack without editing the repo. This is the single biggest gap between the
conversation's promise ("you can just tell an agent to build your own
expansion pack") and what a prospect experiences.

---

## 2. How the use case maps to the product (fit analysis)

Prospect's need, mapped onto the four-ontology model (ONTOLOGY v3.6 §2):

| Prospect's need | Tortoise primitive | Status |
|---|---|---|
| "What's current" (agent rules, job descriptions, project state) | **Object** (objectKind `standard` / `agreement` / `workflow`; core vocab) — state carries lifecycle (promoted/deprecated/superseded via events) + derived confidence | ✅ exists |
| "The whys around that" | **Point** (pointKind `statement`) wired `aboutObject` → the rule Object; **IMPL** operators among Points (the argument tree); **NAND**/mitigations for counter-arguments ("sometimes two things can be true simultaneously… hence mitigations" — the exact concept already in the ontology) | ✅ exists |
| Belief strength on a rule | **EP confidence** — propagated through the operator graph (`tortoise/ep.py`; `tortoise_compute_confidence`, `tortoise_belief_timeline`) | ✅ exists |
| "Events associated with them" | **Event** nodes + decision-as-event (`eventKind: decision`), procedural status projection ("status is derived, not stored") | ✅ exists |
| Agent rewrites a rule correctly *because it knows the why* | **Supersede / CORRECTS** + cascading invalidation (§10.5): rewrite = supersede old rule → `_mark_dirty` → EP re-propagation → contested-claim detection. The old rule keeps its argument tree as provenance. This is the mechanism that prevents "diluted average of blobs" drift | ✅ exists |
| Periodic memory curation (sleep-time shadow agent) | **`tortoise/dream.py`** — background EP stabilization, "the Tortoise analogue of memory consolidation / sleep consolidation"; plus the extractor's value-brief (`memory_granularity` tells it what to keep vs strip) | ✅ exists |
| 4 years of chat history ingest | `tortoise session capture`, `tortoise index directory` (idempotent corpus sweep), `tortoise mine-conversation` (transcript → Events + draft Points), connector framework (`connector_loader.py`, packs declare connectors) | ⚠️ ingest exists; a Letta/ChatGPT-export *connector* is not built (framework is) |
| Local/sovereign inference | Extractor model specs are `provider:model` — `ollama:llama3.2:3b`, `deepseek:...` etc.; no cloud dependency in the self-host daemon | ✅ exists |

**What's missing for this exact prospect (not the engine — the packaging):**

1. **No pack encodes the "agent rules with why" pattern.** Core kinds cover the
   primitives but nothing *operationalizes* rules-with-why (e.g. a mini pack
   with `rule`/`policy` kinds, a `rationale` point kind, a `rule → rationale`
   relation, a `ruleRevised` event, a chain, and a `memory_granularity`
   declaration so the extractor keeps the reasoning). The prospect literally
   needs this pack ("I might need to build an expansion pack for that").
2. **No Letta/chat-export connector** (the prospect's 4-year history is in
   Letta/Postgres + ChatGPT/Claude exports; he already built an export
   extraction framework — he'd bring his own, but a first-party connector
   would be the demo).
3. **Pack authoring + install is developer-hostile today** (see §4).

---

## 3. What the expansion-pack system IS today (verified)

- **Declarative YAML manifests** (`packs/<namespace>/manifest.yaml`), loaded at
  startup by `PackRegistry` (`tortoise/pack_registry.py`). No code execution on
  load. Manifest v3 supports: namespaced kinds (object/event/point/document/
  action), `subclassOf`, `equivalentTo`, typed `relations` with epistemic
  mechanism (`IMPL`/`NAND`), **chains** (business-logic paths, e.g.
  product-strategy's `productDelivery`), `kindDefs` (extractor prompt material:
  description/synonyms/examples/nearMisses/storeAs), `extraction` config
  (active/sourceTypes/enforcement), `connectors`, `tools`, `depends_on`,
  `memory_granularity`. Validation is strict (schema + cross-pack referential
  integrity, isolation of broken packs). Template: `packs/_template/manifest.yaml`.
- **Extractor integration is real:** pack kinds are injected into extraction
  prompts (keyword-selective via `_select_pack_kinds`), chains render into the
  mapping prompt, and **`chain_enforcer.validate_and_rewire` runs deterministically
  on every extraction arm** (reverse-chain pairs rewired through nearest valid
  chain position; never invents, never drops).
- **Per-tenant activation on hosted** (#318): `PackInstall` records live in each
  tenant graph; `TORTOISE_STARTER_PACKS` (default `dev,marketing,product-strategy,pm`);
  idempotent MERGE; `GET /v1/packs` + `tortoise_packs_list` (read-only, D6
  masking). Isolation is structural (tenant graph namespace).
- **Validation CLI exists:** `tortoise validate --domain <namespace>` (advisory,
  graph-global integrity for a pack's kinds/relations).
- **Governance research recorded** (2026-08-05, 5/5 decisions): layered defense
  — SDK write-time validation + MCP pre-flight + pack skills + governance app.
  Only the chain piece is shipped so far.

## 4. The readiness gaps (evidence-backed)

### G1 — Packs do not ship in any packaged artifact (critical)

- `Dockerfile.selfhost` (used by `docker compose up` — the **recommended**
  self-host path) copies `tortoise/`, `pyproject.toml`, `requirements.txt`
  only. **No `COPY packs/`.** In the image, `domain_loader._get_registry()`
  resolves `Path(__file__).parent.parent / "packs"` → `/app/packs` → does not
  exist → **registry is empty**: no pack vocabulary in extraction prompts, no
  chains, no starter packs.
- `Dockerfile.hosted` (Fly production) — same: no packs.
- PyPI wheel (`publish-pypi.yml` → `python -m build`): `[tool.setuptools.
  package-data]` ships only `tortoise = ["config/routing.yaml"]`. A
  `pip install tortoise-graph` (the documented self-host path) has zero packs.
- Packs exist only in a **git clone** (repo-root `packs/`; editable installs
  resolve it via `__file__`). `tortoise/__main__.py` explicitly acknowledges
  "packs-less environment (pip wheel without packs/) sees an empty registry."
- **Impact on the prospect:** the "self-host and use expansion packs" promise
  silently degrades to a bare core ontology on every documented install path.

### G2 — No packs-directory configuration

- `_PACKS_DIR` is a test-only injection; there is **no `TORTOISE_PACKS_DIR`
  env var**. A self-host operator cannot point the daemon at their own packs
  directory (the natural custom-pack mechanism) without editing
  repo/site-packages and restarting — and their edits are lost on upgrade.

### G3 — No pack-authoring surface (create/install/update)

- No CLI (`tortoise pack new`), no MCP tool to create or install a pack (the
  only pack tool is read-only `tortoise_packs_list`, admin group), no
  dashboard UI (`website/apps/dashboard/src/` has zero pack code).
- Hosted: tenants cannot add packs by design (#318 D2: shared catalog is
  read-only). Custom-pack authoring is tracked as **#1154 (unbuilt)**;
  builders' per-tenant packs as **#557 sub-tenancy (scoped, sequenced
  post-launch)**.
- The conversation's onboarding promise ("you can just tell an agent to build
  an expansion pack based on the packs in the repo") works only for a git
  clone with repo-write access, and even then the agent authors a YAML blind
  (no scaffold, no pack-local lint beyond `tortoise validate`).

### G4 — Enforcement ladder is dead config

- `PackManifest.enforcement_for*` (`warn|retry|block`, kind/relation/chain
  levels) is validated at load but **has zero call sites**. The extractor's
  "retry" machinery (M3) is LLM-call retry, not kind-enforcement retry.
  Chains are enforced (deterministic rewire) but chain *severity* levels are
  not read. Relation-level write validation (predicate/kind-pair/cardinality)
  is unbuilt (`label` remains free-form). The founder's own caveat in the call
  — "the business logic in the expansion packs being robust enough and
  implemented in the extractor hard enough is still embryonic" — is accurate.

### G5 — No authoring documentation

- No `EXPANSION_PACKS.md` how-to. The template (`packs/_template/manifest.yaml`)
  and ONTOLOGY §9 (spec) are the only references. Neither quickstart mentions
  packs, and the in-dashboard onboarding wizard (#1643) has no pack step.

### G6 — Secondary readiness items surfaced by the call

- **Export/import carries points + edges, not pack config.** A self-host
  graph built with a custom pack imports to hosted without that pack's
  vocabulary (hosted catalog is fixed) — the custom-pack migration story is
  undefined (#1154 must define it).
- **Cost data for pricing:** hosted extraction cost at scale (4 years of
  history) is unmeasured; `product/pricing.json` is write-op-based; the
  "pricing for 5,000-seat self-host" ask has no rate card yet.
- **Channel/BSL mechanics:** BSL grant (free < $5M revenue self-host) matches
  the conversation; resale/service wrapping is excluded by the license — the
  channel-partner model needs a hosted-partner path (sub-tenancy #557 feeds it).

---

## 5. Recommendations (prioritized)

### P0 — Make "expansion packs" real in every install (unblocks the pitch)

1. **Ship packs everywhere.** Add `packs/` to both Dockerfiles
   (`COPY packs/ packs/` in `Dockerfile.selfhost` and `Dockerfile.hosted`) and
   to the wheel (`[tool.setuptools.package-data]` — move `packs/` under the
   package tree or add an explicit data-dir entry; alternatively resolve packs
   from `sys.prefix`-relative paths). Add a **CI smoke test** that boots the
   built image/wheel and asserts `PackRegistry.load_all() >= 4` — this gap was
   silent because dev runs from a clone.
2. **Add `TORTOISE_PACKS_DIR`** (env override in `domain_loader`, fallback to
   the packaged dir, then repo root). This is the minimal custom-pack install
   path for self-hosters and the foundation for everything else.
3. **Authoring tooling:** `tortoise pack new <namespace>` (scaffold from
   `_template`) + `tortoise pack validate <dir>` (reuse `_validate` +
   cross-pack checks). Cheap — the validator and template already exist. This
   is what makes "tell an agent to build a pack" actually work for customers.
4. **Authoring doc:** `docs/EXPANSION_PACKS.md` — what a manifest expresses,
   the enforcement ladder, chains semantics, `memory_granularity`, and a
   testing recipe (offline mock models). Reference from both quickstarts.

### P1 — Land the prospect's use case

5. **Build the agent-ops / rules-with-why pack** (ship as a starter pack):
   objectKinds `rule` (or subclass `standard`), pointKinds `rationale`;
   relations `rule -IMPL-> rationale`, eventKind `ruleRevised`; a chain
   (`rule → rationale → event`); `memory_granularity` declaring the reasoning
   durable ("what supports/undermines each rule; the situation that created
   it"). This doubles as the sales demo and as the test for "does a small
   pack improve extraction or just constrain it."
6. **Wire the enforcement ladder** into the kind-classifier (start
   warn→retry for kinds like `useCase`/`userJourney` that already declare
   `enforcement: retry`), and hook relation-level write validation into
   `create_operator` per the 2026-08-05 governance decisions (warn-not-block
   default).
7. **Define the hosted custom-pack model (#1154)** — v1: per-tenant manifest
   upload, validated, served from the tenant's namespace; and make
   self-host → hosted export/import carry pack configuration (or at least
   document the mismatch loudly). Sequence with #557 for the channel play.

### P2 — Completeness

8. Dashboard pack management (activate/deactivate starter packs per tenant —
   D1 explicitly deferred selection UI).
9. Pack version/update semantics (PackInstall already records version; define
   upgrade + removal behavior).
10. First-party Letta/chat-export connector (or a documented "bring your own
    exporter" path) to make the 4-year-history ingest a one-liner demo.
11. Rate card for large self-host deployments (the 5,000-seat ask) once
    extraction cost data exists.

---

## 6. What I would tell the prospect this week

- **The engine is ready for the use case.** Rules as state (Objects), whys as
  Points with IMPL/NAND/mitigations, EP confidence, supersede/cascading
  invalidation for correct rewrites, decision-as-event for the timeline, and
  `dream` as the sleep-time curation loop — this is literally the product's
  thesis. Ingest of 4 years of history is doable today via session capture /
  transcript mining.
- **Self-host onboarding works** (docker compose, or embedded for eval), and
  the BSL grant makes it free under $5M revenue.
- **The honest caveat:** right now packs only work from a git clone (the
  packaged installs ship without them), custom packs require editing the repo,
  and pack enforcement is partially wired. Expect the packaging + authoring
  gaps (P0 above) closed first — that is the difference between "works for
  the founder" and "works for a customer."
