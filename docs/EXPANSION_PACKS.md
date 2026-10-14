# Expansion Packs — Authoring Guide

> Epic #1891 (#1932) · Applies to Tortoise self-hosted and hosted · Companion to `packs/_template/manifest.yaml` (the machine-checkable schema) and ONTOLOGY §9 (the canonical spec). This guide is about **behavior** — what a pack expresses, how to test it, and when to write one.

## What is an expansion pack?

An expansion pack extends Tortoise's core ontology with a **domain vocabulary** plus the **business logic** that governs it. Packs are declarative YAML (`manifest.yaml`) — no code runs on load. They give the extractor the kinds it can mint, the chains it must respect, and the `memory_granularity` guidance for what to keep vs strip.

The five starter packs shipped by default: `dev`, `marketing`, `product-strategy`, `pm`, and `agent-ops` (rules-with-why). Your custom packs install alongside them.

One further pack ships in the catalog **without** being a starter: `venture` (fund / venture-builder / accelerator vocabulary — portfolio stakes, programmes, assets, funding agreements, tranches and their gating conditions, disbursement events, action items). It is deliberately not auto-installed, and `docs/`-visible per-graph pack selection is #2728.

> ⚠️ **Interim caveat — the ungated paths are still catalog-wide (#2714/#2728).** Shipping a pack under `packs/` puts its vocabulary into the **catalog union**, and the union is what a graph gets whenever no per-graph gate is resolved: its kinds enter that graph's value brief and kind index, its `memory_granularity` enters the S1 prompt, and its chains are rendered as chain guidance. Per-graph `:PackInstall` records now gate the classifier and the write gate as well as the pack-state introspection surfaces — but only where a gate is actually resolved. Two paths resolve none: a graph that has **no** `:PackInstall` records falls back to the catalog union by contract (#2714 indicator 3), and the `sdk=None` path is the union by contract (#5163). A third does not resolve one for the whole session: the SDK v2 extraction master (`_commit_session_v2` calls `extract_session_v2` with no `master`) falls through to `build_master_list()` for S1/S2/S4 **and the S5 write gate**, while the classify-later index in that same call IS gated (#5202). So "installed" narrows a graph's vocabulary exactly where records exist, and nowhere else — **except chains**, which are never narrowed (see the `CHAINS` note below), and **except the SDK v2 extraction master**, whose index is gated but whose prompt and write gate are not. The two pack-fit layers remain separate — **approval** (which packs a graph allows, per graph) and **classification** (which approved kind an item gets, per item) — and making approval gate classification on **every** path is #2714/#2728. Do not read "not a starter" as "not active".

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

## Declaring a relation — writable, or explicitly declaration-only (#2766)

A `relations[]` entry is a **claim** that your pack's vocabulary includes an edge. Whether the
relation's own declaration binds a runtime write path is a separate question, and the pack author is
told the answer rather than having to discover it from a silent drop. **Every declared relation gets a
write-path verdict**, computed by one resolver (`tortoise/pack_registry.py::relation_write_verdict`) and
reported in three places that therefore cannot disagree: a warning at pack load, the table `tortoise
pack validate` prints, and `sdk.list_relations()` (`write_verdict` + `write_paths`).

**There are exactly two declared write paths.** A relation is `writable` when its declaration binds at
least one of them:

| path | surface | what makes the declaration bind it |
|---|---|---|
| `operator` | `sdk.create_operator(op_type=<mechanism>, label=<predicate>)` — the path `sdk.ingest` uses for `connections[].label` | the relation declares a **`mechanism`** (`IMPL`/`NAND`) — the mechanism is what tells a caller which `op_type` the verb means. The predicate is a free Layer-3 label by owner ruling (ONTOLOGY §8, #7852) — `fixes`, `implements`, `deploysTo` all ride one mechanism and stay distinct by their verb — so the declared **`mechanism` is what binds the verb to the operator path**, not a predicate allowlist |
| `structural` | `sdk.create_edge(<predicate>, from_id, to_id)` and the other structural writers (`create_about_edge`, `sdk.ingest`) | the predicate is one of the structural edge predicates the writers accept (`performs`, `produces`, `uses`, `authoredBy`, `ownedBy`, `managedBy`, `hasMember`, `holdsRole`, `memberOf`, `reportsTo`, `participatesIn`, `hasPart`, `related`, `dependsOn`, `references`, `wasDerivedFrom`, `aboutSubject`, `aboutObject`, `aboutEvent`, `aboutPoint`, `aboutDocument`, `aboutSource`, `aboutAction`, `extractedFrom`) |

⛔ **The verdict is about the DECLARATION, not about whether the writer refuses the write.** The two
writers are not symmetric, and the difference is measured (2026-10-02, #2766):

- `create_edge` is **strict** — a predicate outside the structural list is refused with
  `Unknown predicate`.
- `create_operator` is **warn-not-block** (#1934): it accepts **any** label, and warns only when the
  label is not a declared predicate. So a relation reported `predicate-not-in-allowlist` is still
  writable by a caller who passes an explicit `op_type`; what the verdict names is a **declaration
  gap** — the relation binds no mechanism, so nothing declares what that `op_type` should be. It is
  not a statement that the runtime drops the edge.

The verdict vocabulary:

| verdict | meaning | what to do |
|---|---|---|
| `writable` | the declaration binds ≥1 write path | nothing |
| `declaration-only` | no declared write path, and **you said so** with `declarationOnly: true` | nothing — the marker is the point |
| `predicate-not-in-allowlist` | no declared write path and **no marker** → the declaration binds no mechanism | declare `mechanism: IMPL`/`NAND` (usually the answer), use a structural predicate, or mark it `declarationOnly: true` |

**The verdict is warn-not-block** (the W2 contract the Layer-2 `semantics` check already uses, the
repo's precedent for an open-vocabulary path): a `predicate-not-in-allowlist` relation is named at
load and printed by `tortoise pack validate`, and the pack still loads and `validate` still exits 0.
Refusing would drop the pack — and every pack sharing its kinds — over one declaration mid-alignment.

```yaml
  relations:
    # writable — the operator path; `mechanism` is what the declaration binds
    - predicate: groundedIn
      mechanism: IMPL
      fromKind: myapp:rule
      toKind: myapp:rationale
    # writable — the structural path; this verb is in the writer's allowlist
    - predicate: hasPart
      fromKind: myapp:concept
      toKind: myapp:subconcept
    # NOT writable, said on purpose — no warning
    - predicate: funds
      declarationOnly: true   # the declaration binds no mechanism; say so on purpose
      from: Object
      to: Object
```

`declarationOnly: true` is a statement about the **declaration** — it silences the warning above. It
does **not** stop a caller from writing the label: `create_operator` accepts any label (only
`create_edge` is strict). What the marker records is that the pack declares no mechanism for the verb.

`declarationOnly: true` on a relation whose declaration *does* bind a write path is reported: the
runtime will write it anyway, and a marker that cannot take effect is the same silent divergence the
verdict exists to remove.

> ⚠️ **`relations[].extractable` is declaration-only today.** No runtime consumer reads it
> (`PackManifest.relation_is_extractable` is exercised by tests only), so `extractable: true` does not
> by itself change what the extractor mints. It is a carried intent (#2787 → the extractor-v4
> write-path work, #4894). The verdict above — not this flag — is what tells you whether the
> relation's declaration binds a write path.
>
> **Why a marker and not a third list.** The alternative — a manifest-level mapping table from domain
> predicate to writable predicate — is a **second** source of truth for a vocabulary that already has
> one, which is the defect (#2766: four surfaces each derived the relation vocabulary and contradicted
> each other). Adding a *new* structural predicate is an ontology change (owner territory), not a pack
> change. The written-down mapping is the `mechanism` field itself, plus the Layer-3 verb.

## The extractor contract — how a pack reaches the engine

**The value brief is the only seam.** `compile_value_brief()` (`tortoise/value_extractor.py`) compiles the installed manifests — every `packs/*/manifest.yaml`, plus the hosted `/v1/packs/manifests` overlay — into the **value brief**: `{namespaced kind: {description, nearMisses}}` plus each pack's `ontology.memory_granularity`. Everything domain-specific the extractor can be offered arrives through that one compiled artefact. The engine holds the pipeline mechanics and the core ontology; it holds no pack's domain.

The brief is compiled **per graph**: pass `installed_namespaces` and only the namespaces that graph has installed contribute kinds or granularity (the #2714 APPROVAL gate). The core vocabulary is never gated.

| stage | what it reads |
|---|---|
| **S1 NARRATE** | the brief's `memory_granularity` only — one pack-authored input, rendered by `_granularity_text()` |
| **S2 MAP / S4 REVIEW** | the master list: the brief's pack `kindDefs` **description only**, the core sections, `memory_granularity`, and the engine-side chains |
| **S3 SEARCH** | nothing pack-authored — it derives FTS queries from the S2 embed list and the story, and carries no kind vocabulary |
| **the closed-kind gate** | `_object_kind_forms()` / `_event_kind_forms()` — the master's kind forms **plus** the packs' declared `objectKinds`/`documentKinds`/`eventKinds` |

⚠️ **For the shared filesystem catalog the gate is therefore WIDER than the prompt, not the same vocabulary.** A manifest may declare a kind in `objectKinds`/`documentKinds` with no `kindDef` (`dev` declares `api`, `database`, `software` and `infrastructure` in `objectKinds`, and `apiSpec` in `documentKinds`, all without a `kindDef`; `marketing` declares `keyword` and `competitorContent`; `project-management` declares `kanbanBoard`). `_object_kind_forms()`/`_event_kind_forms()` union those in through `_PACK_OBJECT_FORMS`/`_PACK_EVENT_FORMS`, so such a kind is accepted and written although the shared-catalog brief renders no semantics for it.

**This asymmetry does not arise on the hosted overlay path.** `compile_value_brief` synthesizes an empty description for every declared-but-kindDefs-less kind, so the kind does reach the brief there — `_PACK_*_FORMS` serve the shared catalog only, never tenant packs. The narrow-prompt gap is thus specific to the shared catalog, and that is what `compile_value_brief`'s own docstring records: a gated graph's prompt offers a **narrower** vocabulary than the system will accept or classify into.

With `TORTOISE_CLASSIFY_LATER=1` the pack vocabulary and the chains leave the S2/S4 prompt entirely (`_render_master_core_only`); the post-extraction kind classifier then assigns the pack kind.

**Pack kinds reach the master list by namespace, not by an allowlist.** Every brief key that is neither `core:`-namespaced nor `memory_granularity` is a pack kind (`_build_master_from_brief()`). #5165 removed the hardcoded `PACK_NS` tuple that had to be edited before a new pack could install — the brief's own key set is the authority. `nearMisses` are carried on the brief but are **not** rendered into the S2/S4 prompt — they are consumed by the post-extraction kind index (`kind_index.py`, `kind_classifier.py`).

**Two engine-side enumerations remain. They are the exceptions to "no domain in the engine", and they are named here rather than left implicit:**

- **`_PACK_TRIGGERS`** (`tortoise/extractor_v2.py`) maps the shipped catalog namespaces to story keywords. It is consulted only by the compact prompt mode (`TORTOISE_EXTRACTOR_PROMPT=compact`; the default render is verbose), and selection **fails open** — a namespace with no trigger row is included in every compact prompt. A trigger-less pack is therefore never silently dropped; it is injected whole until a row is added in engine code. Recorded on #1026 (residual from #5165).
- **`CHAINS`** (`tortoise/extractor_v2.py`) holds the chain topologies for `epicToCode` (declared by `dev`), `productDelivery` (`product-strategy`) and `campaignToChannel` (`marketing`), plus `ventureFundingFlow`, `venturePortfolioFlow` and `ventureActionLoop` (`venture`) — six in all. Each chain is named **beside the manifest that declares it** rather than as two lists read positionally: the two orders differ, so a positional list can mis-pair and read as correct. A pack chain whose id is in `CHAINS` is skipped by `validate_chain_completeness()`, which treats the engine copy as canonical. Note that chains are **not** narrowed by an approval gate — `master["chains"]` is built from this constant on every path, so a graph with a single pack installed still sees all six.

## The enforcement ladder (warn | retry)

`warn` and `retry` are the operative levels (a hard `block` is reserved for future per-pack opt-in):

- **warn** (default) — the extractor emits a structured warning and proceeds. Use for soft constraints (chain bypasses, near-misses).
- **retry** — the kind classifier retries (bounded) on a near-miss before accepting a fallback classification. Use for kinds that are important and confusable (e.g. `rule` vs `standard`).

Resolution order for a kind: `kindDefs[].enforcement` → `extraction.enforcement.kinds` → `extraction.enforcement.default` → `warn`.

## Per-graph pack selection (#2728)

Which packs a graph allows is **per graph**, and stored graph-natively: one
`(:PackInstall {namespace, version, status, source})` per namespace, plus one
`(:PackSelection {revision})` marker whose **presence** means "deliberately
configured". The marker is the whole point — without it an explicit *empty* set
is indistinguishable from "never configured", which is what made "remove all"
unreachable before #2728.

- **Statuses.** `active` = approved and enforced; `disabled` = approved but
extraction-off; `removed` = a tombstone — absent from the introspection list,
kept for audit. **Removal is expressed by omitting the namespace from the
desired set**, never by a status the caller sends.
- **Resolution contract** (`pack_state.resolved_pack_set`): no marker → the
catalog union (the back-compat fallback — every pre-#318, self-hosted and
restored graph, #2714 indicator 3); marker present → the `active` records **∪**
the namespaces already in the graph's data. The data leg is what keeps
historical points writable after a pack is disabled.
- **`set_graph_packs` is the only path that moves the revision.** Every seeding
path (`get_tenant_packs`'s self-heal *and* `ensure_tenant_packs` — the
provision retry, the Supabase control plane, and
`graph-scripts/backfill_pack_installs.py`) skips a graph that carries a marker,
so a re-run cannot resurrect a pack the user removed or disabled.
  ⚠️ **Still open — out-of-band writers change the configurable set WITHOUT
  moving the marker.** `POST /v1/packs/manifests`
  (`pack_manifest_store.upsert_tenant_manifest`, widens) and
  `hosted_api._apply_import_pack_config` (re-activates on restore — the
export/import gap the PR body names) write `:PackInstall` directly. On a graph
that has both a `:PackSelection` marker and one of these writers, a `PUT`
holding revision `N` can therefore still return `200` while erasing a
namespace added after its read. Until they route through `set_graph_packs` (or
bump the marker under `_pack_selection_lock`), `revision` is not a *complete*
change signal, and *"no write path may alter the configurable set without
moving the marker"* is the target these paths must meet — not a property that
holds today. (`pack_manifest_store.delete_tenant_manifest` narrows the set the
same way, but it currently has NO caller and no route — a latent writer to bind
when a delete surface is added, not a live gap.)
- **Concurrency:** both the first configuration and every later one move the
marker with a conditional compare-and-swap (`_cas_first_selection` /
`_bump_selection`) *before* any install record is written, so a worker that
loses a race gets a `409` and has written nothing.
- **Management surface:** `GET` / `PUT /v1/graphs/{graph_id}/packs`. Owner/admin
(session) or a key carrying `team:manage`; a graph-bound key is refused on
mutation, so C5 #2114's boundary holds. `PUT` is a full desired-state replace
and **requires** `expected_revision` — a mismatch is a `409` with the current
revision, so two admins cannot silently erase each other's set. Each change
writes one audit record.

> The dashboard Settings → **Configured packs** view (the modal that lists,
> adds, disables and removes packs per graph) is #2728's remaining product
> surface. The state model and the REST surface above are what it renders.

## Chains

A chain is an ordered kind path the extractor respects when connecting `about_entities`. Example: the agent-ops `ruleLifecycle` chain `[rule, rationale, ruleRevised]` — a rule must be grounded in its rationale. The chain enforcer rewires reverse-order pairs deterministically (never invents, never drops) and warns on violations. Steps resolve to your pack's kinds, core kinds, or exactly-one other pack's kinds (ambiguous bare names are validation errors — use `ns:kind`).

## Testing a pack

1. **Validate** — `tortoise pack validate <dir>` (or `--json` for machines). This runs the same schema + cross-pack validation as the daemon, and prints the **relation write-path verdict table** (`writable` / `declaration-only` / `predicate-not-in-allowlist`; `--json` carries the full list under `relation_verdicts`).
2. **Install** — self-hosted: put the pack under `TORTOISE_PACKS_DIR` and restart (or use the packaged `packs/` dir on a clone). Hosted: `POST /v1/packs/manifests` / `tortoise_pack_install` (ontology-only — no connectors/tools on tenant packs).
3. **Extract** — mine a representative transcript with the offline/mock model and check the minted kinds (`tortoise list-kinds`, queries on the graph). The default render is verbose and carries every installed pack's kinds; keyword selection applies only to `TORTOISE_EXTRACTOR_PROMPT=compact`, and even there a namespace with no engine-side trigger row is injected whole (see "The extractor contract" above).
4. **Iterate** — fix near-misses by adding `nearMisses` and `synonyms`; tighten chains with `enforcement: warn`; declare `retry` where the classifier hesitates.

**The offline testing recipe:** run extraction with a deterministic model (the test suite's `MockModel` / the CLI's offline default) — no network, no cost — and assert the kinds/edges the plan requires.

## Self-host vs hosted

| Surface | Install | Author |
|---|---|---|
| Self-host | `TORTOISE_PACKS_DIR` (filesystem) | `tortoise pack new` / `pack validate` (CLI) |
| Hosted | `POST /v1/packs/manifests` / `tortoise_pack_install` (MCP) | same manifest format; ontology-only v1 (no connectors/tools) |

Both surfaces run the same shared validator. A pack that validates locally installs on either.

## Reserved namespaces

`dev`, `pm`, `marketing`, `product-strategy`, `agent-ops` are reserved starter namespaces — both the CLI scaffold and the hosted upload reject them (one guard, both surfaces). `venture` ships in the catalog but is **not** a starter and is not in the reserved list.

## Reference

- Machine-checkable schema: `packs/_template/manifest.yaml` (the template scaffolds from this)
- Canonical spec: `docs/ONTOLOGY.md` §9
- Governance research (enforcement layering): `docs/research/2026-08-05-expansion-pack-governance-surfaces.md`
- Worked example: `packs/agent-ops/manifest.yaml` (rules-with-why)
- Domain pack example: `packs/venture/manifest.yaml` (fund/portfolio state model — Objects hold state, Events are dated, claims are Points)
