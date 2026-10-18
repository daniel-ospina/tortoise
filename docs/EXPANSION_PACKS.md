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
  eventKinds: [ruleRevised, contractSigned]  # what happened → Event nodes
  documentKinds: []          # document subclasses
  subclassOf: {}             # e.g. {epic: Project}
  equivalentTo: {}           # e.g. {issue: [pm:issue]} — cross-pack identity
  kindDefs:                  # extractor prompt material (description IS prompt text)
    contract:
      description: A commercial agreement between parties
      synonyms: [deal, agreement]
      nearMisses: [standard]   # confusable kinds → classifier guidance
      statuses: [draft, signed, terminated]   # this kind's status vocabulary (#2729)
      folds:                  # event kind → status (must be in `statuses`)
        - event: contractSigned
          status: signed
      pipeline:                # #2792: optional stage lifecycle for a stage-driven kind
        defaultStage: stg_draft
        stages:
          - {id: stg_draft,  label: Draft,  order: 1, slot: entry}
          - {id: stg_active, label: Active, order: 2, slot: active}
          - {id: stg_done,   label: Done,   order: 3, slot: terminal, outcome: won}
      values:                  # #2818: the value properties this kind carries
        - name: committedAmountMinor   # BARE name (no namespace)
          valueKind: money             # money|percent|multiple|duration|quantity
          required: true
          cardinality: one             # optional: one (default) | many
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

## Declaring a per-kind stage pipeline (#2792)

A stage-driven kind (a deal, an investment, a tranche, an action item) can declare its **default
lifecycle** under `kindDefs[<kind>].pipeline`. The pack ships sensible defaults; each graph may
**override** them (add / rename / reorder / retire a stage) without a code change or a pack release —
the CRM model (Salesforce stages / HubSpot deal pipelines / Jira workflows).

The model has two tiers: a small **canonical slot** vocabulary the graph understands, and the
**tenant stage list** a graph owns.

| field | required | meaning |
|---|---|---|
| `stages[].id` | ✅ | stable, immutable identity. Anchoring keys on this, never on the label, so a rename never moves an Object. Unique within the pipeline; must not contain the composite stage key's separator (`&#124;`). |
| `stages[].label` | ✅ | the tenant-visible name (renameable). |
| `stages[].order` | ✅ | integer; the forward/regression judgement for future moves. Values must be **unique and non-negative** — a tie would make a move between the two stages neither forward nor backward, so it is refused on the declaration path and by `add_stage(..., order=…)` alike. |
| `stages[].slot` | ✅ | one of `entry`, `active`, `suspended`, `terminal`. Canonical and fixed. |
| `stages[].outcome` | terminal only | required iff `slot: terminal`, one of `won`, `lost`, `withdrawn`, `completed`. |
| `defaultStage` | optional | the declared default stage id. **Declaration-only in this change**: it is validated and stored, and read back by the mutators (it cannot be retired), but nothing applies it to a new Object yet — the stage a new Object enters is set by `advance_stage`, and a null stage reference is a no-op. The Graph Settings surface that consumes it is #2728. |

State and history: the **current** stage is a property on the Object (`pipelineStageId`,
`pipelineSlot`, `pipelineOutcome`, `pipelineStageSince`, `isClosed`) so a query always returns one
answer; the **history** is the dated journal. A terminal stage is **sticky** — a passing later
mention cannot resurrect a closed thing; only an explicit reopen does. Stickiness keys on the
Object's durable `isClosed` marker **as well as** the resolved stage's slot, so a pack release that
reclassifies the stage's slot cannot resurrect a closed Object. A stage reported by
extraction that is not in the effective list is **captured for review** (`pipelineStagePending`),
never dropped and never auto-adopted.

Drift fails closed: an Object whose stored stage no longer resolves in the effective pipeline is
**refused**, not advanced (skipping the guards is the resurrection vector), and a pipeline key that
does not equal the Object's namespaced `objectKind` is refused too. That refusal deliberately has no
public escape hatch in this change — a repair verb belongs to the Graph Settings surface (#2728).

The canonical slot/outcome vocabulary and the shape rules live in `tortoise/pipeline.py`; the pack
key is opt-in and validated exactly like the other `kindDefs` keys. The Settings UI that edits a
graph's stages is #2728; the hardcoded connector `Object.status` fold is #2729 (a separate axis —
pipeline stage is not `Object.status`).

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

**The value brief is the only seam.** `compile_value_brief()` (`tortoise/value_extractor.py`) compiles the installed manifests — every `packs/*/manifest.yaml`, plus the hosted `/v1/packs/manifests` overlay — into the **value brief**: `{namespaced kind: {description, nearMisses, values?}}` plus each pack's `ontology.memory_granularity`. (`values` is the #2818 per-kind value-field declaration; the key is present only for a kind that declares one — see "Per-kind value fields" below.) Everything domain-specific the extractor can be offered arrives through that one compiled artefact. The engine holds the pipeline mechanics and the core ontology; it holds no pack's domain.

The brief is compiled **per graph**: pass `installed_namespaces` and only the namespaces that graph has installed contribute kinds or granularity (the #2714 APPROVAL gate). The core vocabulary is never gated.

| stage | what it reads |
|---|---|
| **S1 NARRATE** | the brief's `memory_granularity` only — one pack-authored input, rendered by `_granularity_text()` |
| **S2 MAP / S4 REVIEW** | the master list: the brief's pack `kindDefs` **description** (plus a declared `values` segment, #2818), the core sections, `memory_granularity`, and the engine-side chains |
| **S3 SEARCH** | nothing pack-authored — it derives FTS queries from the S2 embed list and the story, and carries no kind vocabulary |
| **the closed-kind gate** | `_object_kind_forms()` / `_event_kind_forms()` — the master's kind forms **plus** the packs' declared `objectKinds`/`documentKinds`/`eventKinds` |

⚠️ **For the shared filesystem catalog the gate is therefore WIDER than the prompt, not the same vocabulary.** A manifest may declare a kind in `objectKinds`/`documentKinds` with no `kindDef` (`dev` declares `api`, `database`, `software` and `infrastructure` in `objectKinds`, and `apiSpec` in `documentKinds`, all without a `kindDef`; `marketing` declares `keyword` and `competitorContent`; `project-management` declares `kanbanBoard`). `_object_kind_forms()`/`_event_kind_forms()` union those in through `_PACK_OBJECT_FORMS`/`_PACK_EVENT_FORMS`, so such a kind is accepted and written although the shared-catalog brief renders no semantics for it.

**This asymmetry does not arise on the hosted overlay path.** `compile_value_brief` synthesizes an empty description for every declared-but-kindDefs-less kind, so the kind does reach the brief there — `_PACK_*_FORMS` serve the shared catalog only, never tenant packs. The narrow-prompt gap is thus specific to the shared catalog, and that is what `compile_value_brief`'s own docstring records: a gated graph's prompt offers a **narrower** vocabulary than the system will accept or classify into.

With `TORTOISE_CLASSIFY_LATER=1` the pack vocabulary and the chains leave the S2/S4 prompt entirely (`_render_master_core_only`); the post-extraction kind classifier then assigns the pack kind.

**Pack kinds reach the master list by namespace, not by an allowlist.** Every brief key that is neither `core:`-namespaced nor `memory_granularity` is a pack kind (`_build_master_from_brief()`). #5165 removed the hardcoded `PACK_NS` tuple that had to be edited before a new pack could install — the brief's own key set is the authority. `nearMisses` are carried on the brief but are **not** rendered into the S2/S4 prompt — they are consumed by the post-extraction kind index (`kind_index.py`, `kind_classifier.py`). A declared `values` (#2818) is the opposite case: it IS rendered into the S2/S4 prompt, and is kept OUT of the kind index.

**Two engine-side enumerations remain. They are the exceptions to "no domain in the engine", and they are named here rather than left implicit:**

- **`_PACK_TRIGGERS`** (`tortoise/extractor_v2.py`) maps the shipped catalog namespaces to story keywords. It is consulted only by the compact prompt mode (`TORTOISE_EXTRACTOR_PROMPT=compact`; the default render is verbose), and selection **fails open** — a namespace with no trigger row is included in every compact prompt. A trigger-less pack is therefore never silently dropped; it is injected whole until a row is added in engine code. Recorded on #1026 (residual from #5165).
- **`CHAINS`** (`tortoise/extractor_v2.py`) holds the chain topologies for `epicToCode` (declared by `dev`), `productDelivery` (`product-strategy`) and `campaignToChannel` (`marketing`), plus `ventureFundingFlow`, `venturePortfolioFlow` and `ventureActionLoop` (`venture`) — six in all. Each chain is named **beside the manifest that declares it** rather than as two lists read positionally: the two orders differ, so a positional list can mis-pair and read as correct. A pack chain whose id is in `CHAINS` is skipped by `validate_chain_completeness()`, which treats the engine copy as canonical. Note that chains are **not** narrowed by an approval gate — `master["chains"]` is built from this constant on every path, so a graph with a single pack installed still sees all six.

## The enforcement ladder (warn | retry)

`warn` and `retry` are the operative levels (a hard `block` is reserved for future per-pack opt-in):

- **warn** (default) — the extractor emits a structured warning and proceeds. Use for soft constraints (chain bypasses, near-misses).
- **retry** — the kind classifier retries (bounded) on a near-miss before accepting a fallback classification. Use for kinds that are important and confusable (e.g. `rule` vs `standard`).

Resolution order for a kind: `kindDefs[].enforcement` → `extraction.enforcement.kinds` → `extraction.enforcement.default` → `warn`.

## Per-kind status folds — pack-declared Object state (#2729)

An Object has a current state (`Object.status`), folded from the lifecycle events that name it.
Before #2729 the fold vocabulary was a hand-typed producer→status map in the engine
(`github.issue.open`/`reopened` → `in_progress`; `github.issue.closed`/`pm:cardCompleted` →
`completed`), so a domain pack could not declare its own kind's lifecycle. A pack now declares it:

```yaml
kindDefs:
  tranche:
    statuses: [pending, conditions-met, released, denied]   # closed vocabulary
    folds:
      - event: disbursement      # a bare event kind of THIS pack (or self_ns:event)
        status: released         # must be one of `statuses`
```

The rules (`tortoise/status_folds.py` is the single implementation; `tortoise/pack_registry.py`
validates it at load):

- **`statuses`** is a non-empty list of unique strings — the kind's closed status vocabulary.
  The declaration is machine-read by the fold dispatcher and by `PackRegistry.status_fold_index`.
- **`folds`** is a non-empty list of `{event, status}`. `status` must be in the same kind's
  `statuses`; `event` must be an event kind **this pack declares** (bare `disbursement` or
  self-namespaced `mydomain:disbursement`). An unknown status or an orphan event (a fold key no
  pack declares) is a validation **error** — the pack is isolated, exactly as for an undeclared
  `kindDefs` key.
- **A kind that declares `statuses` owns its state.** The built-in connector folds still apply to
  every Object whose kind declares **no** lifecycle (so a pack-less graph behaves exactly as
  before), but they are **suppressed** for a lifecycle-declaring kind. This is deliberate: the
  connector vocabulary already spells `open`→`in_progress` and `closed`→`completed`, so a new kind
  reusing those words would otherwise silently inherit the wrong lifecycle.
- **Dispatch is on the namespaced kind** (`mydomain:tranche`), so two packs declaring the same
  bare kind name do not collide for an Object whose stored `objectKind` carries the namespace. The
  index is also **form-widened** (a bare-spelled `objectKind` folds by the same rule — see
  "Event-kind spelling" below), so if two packs declare a lifecycle for the **same bare kind
  name**, a bare-spelled Object matches both and the last declaring pack (catalog order) wins.
  That cross-pack collision is recorded as a **warning** on `PackRegistry.lifecycle_warnings` at
  load; the shipped catalog has none. A namespaced `objectKind` is unambiguous, so use the
  namespaced spelling when a bare name is shared.
- **Reusing a shared vocabulary word is DETECTED.** If `statuses` contains `open`, `closed`,
  `active`, `pending`, `in_progress` or `completed`, the registry records an explicit
  **warning** (`PackRegistry.lifecycle_warnings`) — not an error, because an owner-approved
  lifecycle may legitimately reuse the spelling with a different meaning, and the kind owns its
  state either way. Rename the status, or record why the overlap is intended.
- **A declared fold whose explicit `object` target names no Object of its kind is reported** —
  the kind's #1934 enforcement level is resolved and recorded in the violation detail, and the
  violation is emitted with code `status_fold_unapplied`, rather than silently doing nothing.
  ⚠️ The level does not currently change behaviour: `emit_violation` logs at WARNING and nothing
  consumes the code, so `warn`/`retry`/`block` are identical here (there is no retry loop on the
  projection write path). It is a recorded level, not a routed ladder. An `about_entities` target
  that does not match the declaring kind is folded silently and is **not** a violation. A miss is
  suppressed when another declared fold for the **same event** folded *any* target (two packs that
  share a bare event kind name both appear under the widened event key): the non-matching kind is
  simply not this Object's kind, so it is not a real signal.

**Scope of the declaration.** The fold index is compiled from the process **catalog** (every
loaded pack), not from a graph's `:PackInstall` approval set. That is deliberate: a kind already
stored in a graph keeps folding even if the pack's approval is later revoked, because the
declaration describes the kind's lifecycle and a status fold is a cache over the events that name
the Object — stranding existing state would be a data bug, not a security win. The graph's approved
pack set still gates **extraction** (which kinds may be minted), unchanged.

Because the index is catalog-only, a **hosted (graph-native) tenant pack cannot declare
`statuses`/`folds`**: it lives as a `:PackManifest` in the tenant graph and is compiled per graph by
`compile_value_brief(tenant_manifests=…)`, so it never enters a `PackRegistry`. The tenant upload
route therefore **refuses** the declaration with an error naming the limitation rather than
accepting it and installing an inert pack — acceptance is the signal that the declaration will
work. Install such a pack self-hosted under `TORTOISE_PACKS_DIR` (or remove the two keys).

**Event-kind spelling.** A fold's `event` may be declared bare (`disbursement`) or self-namespaced
(`venture:disbursement`), and the **runtime** event kind may be either spelling, in any case: the
fold index is keyed case-insensitively on both forms, because the classification write gate admits
both (`_event_kind_forms`) and the extraction commit strips the namespace before writing
(`sdk.py::_extract_session_v2`). The same widening applies to `objectKind`, so a bare-spelled
Object is folded by the same rule as its namespaced twin and is equally suppressed from the built-in
connector fold. An **extracted** event (a payload event carrying `about_entities` and no `object`)
folds a declared rule onto any `about_entities` target that is an Object of the declaring kind;
a miss is reported only for the event's **explicit** `object`.

The terminal-status guard is unchanged: a `superseded`/`deprecated`/`archived`/`retracted` Object is
never resurrected by any fold (#2164/#3309). Objects are name-keyed, so a state change that is a
**new** thing is a supersession, not a fold.

## Per-kind value fields — pack-declared value properties (#2818)

A kind can declare which **value properties it carries**, so a domain pack can say *"a
`fundingTranche` carries `committedAmountMinor` as a money value"* instead of that fact being
hardcoded in engine code:

```yaml
kindDefs:
  fundingTranche:
    description: A tranche of a financing round
    values:
      - name: committedAmountMinor
        valueKind: money        # money | percent | multiple | duration | quantity
        required: true
      - name: trancheLabel
        valueKind: quantity
        cardinality: many       # one (default) | many
```

- **`values` is a CLOSED, validated declaration — not an open props bag.** Each entry may carry
  exactly `name`, `valueKind`, `required` (bool, default `false`) and `cardinality`
  (`one` | `many`, default `one`). An unknown entry key, an unknown `valueKind`, a non-list
  `values`, a duplicate `name`, or a **namespaced** name is a validation **error** with a message
  naming the offending entry — the pack is isolated exactly as for any other bad `kindDefs` key.
  The vocabulary and shape rules live in `tortoise/pack_values.py` (one home); `PackRegistry._validate`
  calls them, so `tortoise pack validate`, CI and the hosted tenant install all read the same rule.
- **`valueKind` is WS-C's value *shape*, never a storage type.** `money` is a shape over
  `amountMinor` + `currency` + `minorUnitExponent`, `percent` is `basisPoints`, and so on. The
  scalar storage type of the underlying props is the value model's business (#2782/#2817), not
  this declaration's.
- **Property names are BARE** (`committedAmountMinor`, never `venture:committedAmountMinor`): the
  pack namespaces its kinds, not its properties (WS-B OD2). A declared name must be a bare
  identifier — letters, digits and underscores, not starting with a digit — because it is rendered
  raw into the prompt; a colon, whitespace or punctuation is refused.
- **The declaration reaches the extraction prompt.** `compile_value_brief()` carries a declared
  kind's `values` on its brief entry, and the S2/S4 master renders the kind as
  `- venture:fundingTranche — A tranche of a financing round [values: committedAmountMinor(money,
  required), trancheLabel(quantity, many)]`. The shared S2/S4 rule slot also states the policy:
  keep a declared value EXACT, use the declared name, and never invent a field or a value kind.
  With `TORTOISE_CLASSIFY_LATER=1` the pack vocabulary leaves the S2/S4 prompt
  (`_render_master_core_only`) and the value declarations leave with it — the post-extraction
  classifier assigns the kind in that mode, so a namespaced pack kind must not reappear through its
  declarations.
- **It does NOT reach the classification index.** `compile_kind_index_spec()` reads
  `description`/`synonyms`/`examples`/`nearMisses`; rendering value schemas into the index `text`
  would rotate `kind_index.cache_key_for` on every install and silently re-embed the whole kind
  index. A test pins the index spec byte-identical with and without a declaration.
- **A props-less pack renders byte-identically.** The brief key is absent when a kind declares
  nothing, so the brief, the master's `pack_kinds` section and every rendered prompt are
  byte-for-byte what they were before #2818 for the entire shipped catalog (the master gains one
  empty `pack_values` section — the same always-present shape as `pack_hints`; a test pins the
  renders and all six starter packs still validate unchanged).
- **The hosted overlay carries it too.** Unlike `statuses`/`folds` (catalog-only — a tenant
  manifest never enters a `PackRegistry`), `values` is compiled by the same loop in
  `compile_value_brief(tenant_manifests=…)`, so a hosted tenant pack's declaration reaches its own
  graph's prompt. It is a prompt-facing declaration, not a catalog-index declaration.

**What this declaration does NOT do yet.** It declares and renders; it stores nothing. The write
path for a declared value field is #2782, and the shape's companion props (`currency`,
`minorUnitExponent`, …) plus the reserved-name guard belong to the core property register
(WS-A `POINT_PROPS` / WS-C D12), which is not in code yet. Declaring a shape is therefore necessary
but not sufficient to make a value durable — see #2818's residuals.

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
writes one audit record. The read also carries **`available`** — the shared
catalog plus this graph's own tombstones, minus what is already listed — so the
UI's add-list comes from the server rather than a second copy of the catalog in
the browser. That list is what keeps *removal* from being a one-way door:
removal is a desired-state OMIT, and a **tenant-authored** pack appears in no
shared catalog, so without the tombstone leg it would be un-addable once
removed. Its rows carry the **`source` the write would store** — a namespace
this graph already has an install record for keeps that record's `source`
(which may also be `custom`), and one it does not gets the `starter`/`manual`
derivation the write itself uses — and the read carries **`catalog_ok`**:
`false` means the shared catalog could not be read (the read degrades rather
than `503`-ing), so an empty `available` must be shown as *"the catalog could
not be read"* — never as the claim that everything is already on the graph.

> The dashboard Settings → **Configured packs** home (`settings-packs-heading`)
> renders this contract, with a graph selector and a modal that lists each
> pack's name, namespace, version, kind count, source and status and adds /
> turns off / removes them. The Graphs tab carries the same dialog per row as
> the graph-contextual door. Its decisions are derived, not invented:
> `src/packs.js` holds the status grouping, the add-list diff, the PUT body and
> the per-failure classification, so the surface cannot drift from the route's
> semantics (the server's `removed` is never sent as an input status — omission
> is what removes). `configured: false` is stated in words, with the resolved
> set's count qualified in parentheses beside them, because such a graph still
> resolves to the whole catalog: "Not configured yet — the full catalog applies
> (2 active packs · 1 off)" — the count describes the rows the payload carries,
> never a bare number a reader could mistake for the enforced set.

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
**One declared exception:** `statuses`/`folds` (#2729) are a filesystem-catalog surface — the fold
index is compiled from the process catalog, which a graph-native tenant manifest never enters — so
the hosted install route **refuses** them with an error naming the limitation instead of accepting a
declaration that would be inert.

## Reserved namespaces

`dev`, `pm`, `marketing`, `product-strategy`, `agent-ops` are reserved starter namespaces — both the CLI scaffold and the hosted upload reject them (one guard, both surfaces). `venture` ships in the catalog but is **not** a starter and is not in the reserved list.

## Reference

- Machine-checkable schema: `packs/_template/manifest.yaml` (the template scaffolds from this)
- Canonical spec: `docs/ONTOLOGY.md` §9
- Governance research (enforcement layering): `docs/research/2026-08-05-expansion-pack-governance-surfaces.md`
- Worked example: `packs/agent-ops/manifest.yaml` (rules-with-why)
- Domain pack example: `packs/venture/manifest.yaml` (fund/portfolio state model — Objects hold state, Events are dated, claims are Points)
