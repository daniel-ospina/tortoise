# Expansion Packs — Authoring Guide

> Epic #1891 (#1932) · Applies to Tortoise self-hosted and hosted · Companion to `packs/_template/manifest.yaml` (the machine-checkable schema) and ONTOLOGY §9 (the canonical spec). This guide is about **behavior** — what a pack expresses, how to test it, and when to write one.

## What is an expansion pack?

An expansion pack extends Tortoise's core ontology with a **domain vocabulary** plus the **business logic** that governs it. Packs are declarative YAML (`manifest.yaml`) — no code runs on load. They give the extractor the kinds it can mint, the chains it must respect, and the `memory_granularity` guidance for what to keep vs strip.

The five starter packs shipped by default: `dev`, `marketing`, `product-strategy`, `pm`, and `agent-ops` (rules-with-why). Your custom packs install alongside them.

Two further packs ship in the catalog **without** being starters:

- `venture` (fund / venture-builder / accelerator vocabulary — portfolio stakes, programmes, assets, funding agreements, tranches and their gating conditions, disbursement events, action items).
- `portfolio` (#9663) — an **overlay on `venture`** (`depends_on: [venture]`) for the portfolio manager's own vocabulary: the `mandate` that bounds what may be held, the `allocationTarget` that states the per-class/sector weight the portfolio is steered toward (and the benchmark it is measured against — `values: targetPct (percent)`), and the `valuationMark` event. It renames nothing venture owns: the position stays `venture:investment`, referenced **namespaced**, and the overlay declares no `equivalentTo`. Every admitted kind passes one bullet of the pack value test, and every rejected candidate names the home that absorbs it — see `tests/test_investment_portfolio_manager_pack.py`, which re-derives each passing bullet from the manifest instead of trusting the comment.

Neither is auto-installed, and `docs/`-visible per-graph pack selection is #2728.

> ⚠️ **Interim caveat — the ungated paths are still catalog-wide (#2714/#2728).** Shipping a pack under `packs/` puts its vocabulary into the **catalog union**, and the union is what a graph gets whenever no per-graph gate is resolved: its kinds enter that graph's value brief and kind index, its `memory_granularity` enters the S1 prompt, and its chains are rendered as chain guidance. Per-graph `:PackInstall` records now gate the classifier and the write gate as well as the pack-state introspection surfaces — but only where a gate is actually resolved. Two paths resolve none: a graph that has **no** `:PackInstall` records falls back to the catalog union by contract (#2714 indicator 3), and the `sdk=None` path is the union by contract (#5163). A third does not resolve one for the whole session: the SDK v2 extraction master (`_commit_session_v2` calls `extract_session_v2` with no `master`) falls through to `build_master_list()` for S1/S2/S4 **and the S5 write gate**, while the classify-later index in that same call IS gated (#5202). So "installed" narrows a graph's vocabulary exactly where records exist, and nowhere else — **except chains**, which are never narrowed (see the `CHAINS` note below), and **except the SDK v2 extraction master**, whose index is gated but whose prompt and write gate are not. The two pack-fit layers remain separate — **approval** (which packs a graph allows, per graph) and **classification** (which approved kind an item gets, per item) — and making approval gate classification on **every** path is #2714/#2728. Do not read "not a starter" as "not active".

## When to write a pack (vs using core kinds)

- **Write a pack** when your domain has recurring nouns the core ontology doesn't name (`contract`, `rule`, `epic`, `useCase`), a relationship structure worth enforcing (chains), or extractor guidance worth declaring (`memory_granularity`, `retry` on confusable kinds).
- **Don't write a pack** for one-off concepts — a `statement` Point about anything is always valid. Packs pay for themselves with repeated extraction over a bounded vocabulary.

## Anatomy of a manifest

```yaml
namespace: mydomain          # REQUIRED — camelCase, no colons, no whitespace, not a reserved starter name
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

### One bare kind name, one category (#2786)

A bare kind name belongs to **exactly one** of `objectKinds`, `eventKinds`,
`pointKinds`, `documentKinds`, `actionKinds` — and a name core already owns cannot be
declared at all, on any of those five axes. Both are load errors that drop the pack:

- the same name in two categories of one manifest claims two meanings, and
  `store_as()` resolves the collision by silent precedence for the point-vs-rest
  pairs (a `:Point` for a kind the manifest also declared an `:Object`), so the store
  target would be chosen for the author instead of by the author. The rule is
  per-manifest: the same name in the *same* category across two packs is legal, and a
  repeated entry inside one list is redundant, not a deformation;
- a core name declared under a different category is the deformation #7848 fixed by
  hand (the `dev` pack shipped `deployment` — an ONTOLOGY §5 **eventKind** — in
  `objectKinds`). It is not inert: `pack.object_kinds` feeds the extractor's writable
  kind forms and the classification index, so the deformed name was writable under
  the wrong axis. The action axis counts too (#122 retired its *canonical* status, but
  `implement`/`deploy`/`meet`… still resolve and expand as core).

A declared kind is a **bare identifier** — no whitespace, no `:` (the registry prefixes
it with the manifest's namespace), so `" deployment"` and `"dev:deployment"` are
refused rather than silently defeating the collision check.

Reference core kinds by name instead of re-declaring them — `nearMisses` and chain
steps resolve any core kind directly. A `subclassOf` **parent** is narrower by design:
ONTOLOGY §6 subclasses Objects, so the parent must be a core entity type (`Document`,
`Event`, `Point`… the `CORE_ENTITY_TYPES`) or a core `objectKind` (`WorkItem`,
`Project`, `goal`…) — a lowercase core event or point *kind* (`meeting`, `occurrence`)
is not a subclass parent.
`equivalentTo` is what links two packs that declare the same name in the *same*
category. `decision` is core-owned on two axes (point **and** event), so a collision
error names every owner.

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

**Pack kinds reach the master list by namespace, not by an allowlist.** Every brief key that is neither `core:`-namespaced nor in `value_extractor.RESERVED_BRIEF_KEYS` (`memory_granularity`, `pack_hints`, `pack_refs`) is a pack kind (`_build_master_from_brief()`). #5165 removed the hardcoded `PACK_NS` tuple that had to be edited before a new pack could install — the brief's own key set is the authority. `nearMisses` are carried on the brief but are **not** rendered into the S2/S4 prompt — they are consumed by the post-extraction kind index (`kind_index.py`, `kind_classifier.py`). A declared `values` (#2818) is the opposite case: it IS rendered into the S2/S4 prompt, and is kept OUT of the kind index.

**Two engine-side enumerations remain. They are the exceptions to "no domain in the engine", and they are named here rather than left implicit:**

- **`_PACK_TRIGGERS`** (`tortoise/extractor_v2.py`) maps the shipped catalog namespaces to story keywords. It is consulted only by the compact prompt mode (`TORTOISE_EXTRACTOR_PROMPT=compact`; the default render is verbose), and selection **fails open** — a namespace with no trigger row is included in every compact prompt. A trigger-less pack is therefore never silently dropped; it is injected whole until a row is added in engine code. Recorded on #1026 (residual from #5165).

  **…closed over each selected pack's own declared REFERENCES (#9733).** The narrowed list is rendered under "EVERY kind you emit MUST come from this list", so a pack whose relations name another namespace's kind would offer a vocabulary its own declarations could not be spelled in — `packs/investment-portfolio-manager` (`depends_on: [venture]`) points `marks` and `constrainedBy` at `venture:investment`, and three of the four `portfolio:` trigger words are not also `venture:` triggers (only the bare `portfolio` one is, and it is also a `pm:` trigger). `compile_value_brief` therefore carries a reserved `pack_refs` section — `{kind: [every kind its own declared relations pair it with]}`, collected **undirected and including same-namespace partners** by `value_extractor._relation_endpoint_refs` from `ontology.relations[].fromKind/toKind` and `extraction.relationTemplates[].fromKind/toKind` (the legacy `from`/`to` spelling carries entity types, never a namespaced kind) — and `_select_pack_kinds` closes over it **to a fixpoint**: a selected kind pulls the partners of its own declared pairings, and a partner the closure adds pulls *its* partners in turn. The fixpoint is load-bearing, not tidiness: a first cut keyed by declaring namespace and kept only foreign refs, which offered `product-strategy:feature` on a `dev` story without the rest of `product-strategy` and so made five of that namespace's own edges unspellable (half-offered relations on a `dev` story went 2 → 6 — *worse*). Measured over the shipped catalog, the fixpoint leaves every story narrow — 1-24 of the 47 shipped kinds across all 53 trigger words, never the all-packs set — and leaves **zero** half-offered relations **among the endpoints the brief carries**. That last qualifier is load-bearing: a kind declared in `objectKinds`/`documentKinds` with no `kindDef` never reaches the catalog brief (see the narrow-prompt asymmetry above), so a relation pointing at one of those cannot be closed by any selector — the measured residuals are **three** relations, all pre-existing and all pinned by `TestCompactReferenceClosure::test_declared_endpoints_absent_from_the_brief_are_known` (the endpoints) and `…::test_every_residual_relation_is_named_in_the_docs` (this paragraph, read back): `marketing:content → marketing:keyword` and `agent-ops:rule → agent-ops:rationale` are **half**-offered (`keyword` is a declared `objectKind` with no `kindDef`; `rationale` is a `pointKind`), while `dev:api → dev:database` is **wholly** unspellable — both of its endpoints are declared object kinds with no `kindDef` (`packs/dev/manifest.yaml:14-15`), so neither side is offered by any selector, in any render mode. The second pin reads this paragraph, so this enumeration cannot fall behind the measurement again.

  It is a closure over REFERENCES, **not** over `depends_on` namespaces: four shipped packs declare a dependency and `dev` ↔ `product-strategy` is a cycle, so transitive namespace closure would collapse the narrowing this mode exists for (a `marketing:` story would pull 33 of the 47 shipped kinds, a `dev:` story 29). A referenced kind absent from the brief (a graph that has not installed the dependency) is simply not added — fail-open, like every other branch.
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
  `- <namespace>:<kind>[ — <description>] [values: <field>(<kind>[, required][, many]), …]` —
  the annotation is exactly what `tortoise/pack_values.py::value_field_annotations` emits: a
  token is present only when it applies (`required` for a required field, `many` for
  `cardinality: many`; there is no `optional` token — an optional field simply omits it). The
  `[ — <description>]` gloss belongs to the RENDER, not to that annotation, and the two render
  modes differ: the default (verbose) one always prints it, while under
  `TORTOISE_EXTRACTOR_PROMPT=compact` `tortoise/extractor_v2.py::_needs_gloss` withholds it
  unless the description disambiguates. The shape below is ILLUSTRATIVE — a two-field tenant
  kind, not a shipped one, and it is the VERBOSE render's shape:
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
- **A props-less KIND renders byte-identically; a declaring PACK does not.** The brief key is
  absent when a kind declares nothing, so that kind's `pack_kinds` line and its brief entry are
  byte-for-byte what they were before #2818. The shipped catalogue is no longer props-less as a
  whole: since #9663 `portfolio:allocationTarget` declares `targetPct (percent, required)`, which
  adds one `[values: …]` segment to the PACK KINDS list and the `KIND VALUE FIELDS` policy block to
  the S2/S4 rule slot — on the gated path only when that pack is installed, and on the catalog
  UNION path (a graph with no `:PackInstall` records, #2714/#5163) for everyone else. The other six
  shipped packs still declare nothing, so their entries are unchanged; a test pins the catalogue's
  declared set to exactly that ONE field and asserts the segment count is 1.
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
- **Resolution contract** (`pack_state.resolved_pack_set`) — THREE arms, and
  only the first is the catalog union:
  - no marker **and no `:PackInstall` records** → the catalog union (the
    back-compat fallback — every pre-#318, self-hosted and restored graph,
    #2714 indicator 3);
  - no marker **but records present** → `recorded(any status)` ∪ the namespaces
    in the graph's data. This is the arm a **hosted default graph** lands in:
    provisioning seeds the starter set (#318), so the graph is
    `configured: false` and already NARROWED — a catalog pack that exists but is
    not installed is not in the set (#9691);
  - marker present → the `active` records **∪** the namespaces already in the
    graph's data, `configured: true`. The data leg is what keeps historical
    points writable after a pack is disabled.

So `configured: false` is **not** a synonym for "the whole catalog applies" —
the RECORDS are the predicate. Read it as "never explicitly configured".

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
- **Provisioning can configure at birth (#9667).** `POST /v1/graphs` accepts an
optional `packs: [{namespace, status}]` alongside `org_id`/`name`/`scopes`, so
create-and-configure is ONE call and an unconfigured graph becomes a *choice*
rather than the only possibility. Three behaviours to keep straight, because
they are deliberately different: **field absent** → no pack decision, so the graph
is born UNCONFIGURED with no `:PackInstall` records — the catalog-union arm until
records exist (the first packs read self-heals the starter set, which narrows it);
**`packs: []`** → born CONFIGURED with an empty set, the narrowest deliberate
act; **a list** → exactly that set. The 201 envelope then carries the resolved
set under `packs` (`{namespaces, revision, configured}`) so the caller never
has to re-read, and the response is otherwise byte-identical to before. It is
**atomic**: entries are validated before any write, and a namespace the catalog
does not carry (or a `removed` status) fails the CREATION with a `422` and the
graph rolled back — never a graph plus a follow-up call. It is also the
MANAGEMENT plane, exactly like `PUT`: `packs` on a create requires the
`owner`/`admin` role, so the create form is not a back door around the
per-graph route. `expected_revision` does not apply (the namespace is new, so
its revision is 0 by construction — `set_graph_packs`' documented
"internal callers that own the graph" arm), and the write is attributed to the
creating session user on the `:PackSelection` marker. The **key-driven**
provisioning route (`POST /v1/organizations/{org_id}/graphs`) deliberately does
NOT take `packs` yet: it is the app-builder flow, which #9667 leaves to the
customer-facing territory that owns per-graph permissions.

> The dashboard Settings → **Configured packs** home (`settings-packs-heading`)
> renders this contract, with a graph selector and a modal that lists each
> pack's name, namespace, version, kind count, source and status and adds /
> turns off / removes them. The Graphs tab carries the same dialog per row as
> the graph-contextual door. Its decisions are derived, not invented:
> `src/packs.js` holds the status grouping, the add-list diff, the PUT body and
> the per-failure classification, so the surface cannot drift from the route's
> semantics (the server's `removed` is never sent as an input status — omission
> is what removes). `configured: false` is stated in words, and WHICH words
> depend on the **records**, never on the flag alone (#9691): only the
> record-less graph resolves to the whole catalog, so only it is told so — "Not
> configured yet — this graph holds no packs, so the full catalog applies.
> Saving pins the set." The arm every starter-seeded default graph is in gets
> the other sentence — "Not configured yet — this graph uses the packs it
> already holds (2 active packs · 1 off). Saving pins the set." The count in
> parentheses describes the rows the payload carries, never a bare number a
> reader could mistake for the enforced set, and it is omitted in the
> record-less arm rather than printed as a structurally-0 "0 active packs"
> beside the catalog claim.
> A catalog that could not be READ (`catalog_ok: false`) changes NOTHING about
> this line, and that is deliberate. The resolution is derived from the RECORDS
> and the marker (`resolved_pack_set`), never from the catalog read —
> `_packs_payload` reads `resolved` first and `catalog_ok` from a separate
> `_pack_catalog_rows()` — so the union is the union whether or not the catalog
> could be enumerated. The degraded fact is about the ADD-LIST's enumeration, and
> it surfaces there (`ADD_LIST_DEGRADED_LINE`, in the modal beside that list).
> Suppressing a TRUE sentence on the home — which renders no degraded signal at
> all — would leave a user concluding that no pack applies, which is the inverse
> of the surprise #9691 exists to remove. `packs.test.js` pins the
> state-independence directly.
> `packs.js::usesCatalogUnion` is that predicate — `configured: false` **and**
> zero records (a tombstone counts; the catalog arm of `available` does not).
> ⚠️ The predicate derives the record set from the two lists the payload carries
> rather than from the resolved set itself, so it cannot see a tombstone the
> catalog arm offers as `available` instead of `removed`. **That shape is
> reachable today, and a REBUILD produces it:** `:PackSelection` is not enrolled
> in the projection's `_config_classes()`, so `rebuild_all` keeps the
> tombstoned `:PackInstall` row and drops the marker. Measured — before
> `configured: true, revision: 1`; after `configured: false, revision: 0` with a
> `removed` row, which reads as the catalog union while the resolved set is that
> one removed namespace. The same marker loss silently RE-ADMITS a pack an admin
> removed and resets `revision`. Preserving the marker fixes both — #9752 — and
> the SHAPE of that fix matters: `:PackSelection` is ONE node per graph with no
> identity property, so it is a SECTION like `:TeamMeta`, NOT a
> `_config_classes()` row. The registry keys on a single string identity property
> and `_capture_config_snapshot` silently skips a class whose identity value is
> not a string, so "enrol it as a config class" would capture nothing and leave
> both defects in place. Surfacing the record count — or `namespaces is None`
> itself — on the payload would make the client exact in the meantime. Separately,
> `pack_manifest_store.delete_tenant_manifest` sets `removed` without moving the
> marker and has no caller or route today — latent, and the same preservation
> covers it.

### The write-gate closure — which writes are gated, and which are not (#5475/#9068)

The closure property from #5475 is **no write may widen a graph's approved pack set**.
That property is worth reading precisely, because the surfaces it does *not*
cover are deliberate and a reader who takes "the write path is closed" literally
will either trust a guarantee that is broader than the code, or try to "fix" one
of the exceptions and break it.

**Gated — every live door that persists a `pack_state._KIND_PROP_KEYS`
property:** the SDK entity funnel (`_create_entity`), the point door
(`create_point`), `update_point` / `update_entity`, `EventAPI._emit`,
`sdk._apply_and_journal` (the door the **GitHub indexer** writes through —
`issue_to_object` carries `object_kind='pm:issue'`, so an ungated chokepoint
let the polling entry point install `pm` on a `dev`-only graph), `sdk._doc_write`,
`sdk.ingest_corpus` (its `eventKind` keyword on a raw-Cypher writer),
`sdk._journal_event_raw_write` (the raw `:Event` seam, #9197),
the conversation miner's emitter (`mining.ConversationMiner._emit_event`),
`link_source_to_entity` (through the **SDK wrapper** — the projection method
behind it is NOT gated; see the per-call-site caveat below),
and the REST object/subject/commit doors. That list is the human-readable
summary; the EXECUTABLE inventory — and the one to read before adding a door —
is `tests/test_9068_pack_gate_residuals.py::_DOORS` (19 doors at this head),
which a new gate call site reds rather than silently widening. The refusal
is an **input constraint**: a *namespaced* kind may only use a namespace the
resolved set already holds, and an unapproved spelling is REJECTED (422 /
`UnapprovedKindNamespace`), never silently normalised — a rewrite would store a
kind the caller did not write.

Every one of those doors reads the SAME predicate: a pre-built record (the
SDK's live-write doors and the connector folds) goes through
`pack_state.kind_spellings_in_payload` →
`pack_state.refuse_unapproved_kinds_in_payload` — which screens the mined key
set in BOTH spellings (the fold's snake aliases `object_kind` / `subject_kind` /
`document_kind`, and `uses[].kind`) — and the `create_*`-style doors screen
their props with the same key set **and the same spelling leg**
(`create_object(props={"object_kind": "pm:issue"})` was a live bypass of the
camel-only props screen until #9068: the fold reads the snake spelling and
writes the mined camel property; the props door now reads
`pack_state._KIND_PROP_SPELLINGS`, the same declaration). A per-door list is how
the widening survived the first closure, so a door that gates individually is a
bug, not a variation.
Doors on a hot path (`_apply_and_journal`, `_emit`, the connector batches, the
`update_*` props) run a cheap pre-check first (`pack_state.payload_names_pack`,
`connectors._gate_kinds`): a record whose kinds name no pack pays **no graph
read at all**, so the common case (core kinds, bare spellings) costs the same
as before the gate — which matters most on the webhook arms, where the "batch"
is one delivered message (the pre-check walks the record once and hands the
pairs to the gate, so it is never walked twice).

**Gated — the connector folds (#9068).** `github` / `linear` / `slack` fold
records with a bare `proj.apply(...)` and never meet an SDK door.
`FalkorProjection.apply` refuses a direct fold only on a **journal-backed**
projection (§17.1's single-writer guard), and there the cache is a *derived
image* of the journal. On a journal-less projection the cache **is** the store,
so the fold is a real write: each connector `ingest` (and both webhook paths)
now pre-validates its whole batch against ONE approval-set read before the
first `apply`, so a refusal leaves the graph untouched rather than
half-imported. Before that gate, github's `object_kind='pm:issue'` put `pm`
into a `dev`-only graph's approved set. On the webhook arms the gate runs
**before** the caller's `on_event` callback (a consumer is told about writes
that landed, not about deliveries that were refused) and before Slack's
per-message permalink enrichment (a refused batch must not spend an API call
per message). A refusal there is answered **422** with the refusal's own message
— the same posture `hosted_api` takes — not 500: the payload refuses identically
on every retry, so a 500 buys a retry loop and discards the remedy text.

**The mining leg reads a generic `kind` only off ontology labels (#9068).**
Seven of the eight kind properties are spelled in the ontology's own vocabulary
(`objectKind`, `pointKind`, …), so a value under one of them is an ontology kind
wherever it is found. The bare `kind` slot is not: `:Pipeline` stores its
namespaced **pipeline key** there (`venture:deal`) and `:Graph` stores its own
`kind`. Reading those as ontology kinds made materialising a pipeline override
grant the `venture` pack; the fix is in the reader, not in the pipeline writer
(the key is a real pipeline name, so gating that writer would start refusing
legitimate pipeline edits).

**Decision (relayed to #9068):** this is **Option A's READER half** — the
mining leg stops treating the slot as ontology-owned. The other half of A
(dropping the duplicate `p.kind` write) is deliberately NOT taken here: it
cannot pay the upgrade cost below, because every already-stored `:Pipeline` row
carries `kind` and the scoped reader ignores it whether or not new rows keep
writing it. The two halves have identical upgrade cost, so the write-drop is a
pipeline-model clean-up (removing a duplicate of `pipelineKey`), not a security
control — and nothing in the repo reads `:Pipeline.kind`. `pipeline._write_pipeline`
now says so at the write site.

⚠️ **Upgrade effect, stated because it NARROWS a back-compat union**
(`#2714` indicator 2): a graph that holds `:PackInstall` records and whose ONLY
kind-bearing node for a namespace is a `:Pipeline` row loses that namespace from
the approval set — writes naming it become refusals until the pack is installed
(`PUT /v1/graphs/{graph_id}/packs`). The `p.kind` write is deliberately left in
place (dropping it is a pipeline-model change: every stored override carries the
property, and nothing in the repo reads it), so this narrowing is a property of
the READER on both new and existing rows, not of the write.

⚠️ **The connector gate is per CALL SITE, not at the projection seam.**
`FalkorProjection.apply` still gates a direct fold only when `_journal_backed`,
so a THIRD-PARTY connector — or any caller outside `tortoise/connectors/` — that
folds straight into a journal-less projection is still ungated. The projection
has a **second** ungated sink with WIDER exposure: `link_source_to_entity`
writes the caller's `source_kind` into the mined `sourceKind` slot
(`projection/edges.py`'s `ON CREATE SET s.sourceKind=$sk`) and has **no
`_journal_backed` check at all**, so a direct call widens on BOTH shapes — the
refusal for that leg lives only in the `sdk.link_source_to_entity` **wrapper**.
Both gaps are measured and pinned (`tests/test_9068_pack_gate_residuals.py`'s
`test_a_journal_less_fold_does_not_widen_the_approval_set`: `apply-fold` on a
journal-less projection, `source-link` on journal-less AND on journal-backed) —
a residual statement that named only `apply` would be narrower than the
measurement, and closing `apply` alone would read as closing the seam. Neither
is claimed closed here.

**Deliberately NOT gated — and why:**

| Writer | Why it is not gated |
|---|---|
| `rebuild_all` / `recover_from_log` (replay) | The record came FROM the journal. Refusing at replay would make a committed record unfoldable — strictly worse than the widening. |
| `migrate_kinds.py` (raw Cypher over a live graph) | An **operator act**: a deliberate re-typing of the graph's own historical rows, with every target spelling a reviewed literal in the file. It is the sanctioned way a pack namespace enters data written *before* the pack existed, so widening the mined set is the point of the run — a gate would refuse the very row the migration exists to rewrite. |
| `retype.py` / `tools/retype_snapshot.py` | Offline: they transform a dump and never touch a live graph. They enforce the target kind against the **declared vocabulary** instead (`RetypeError`). |
| `hosted_api._seed_demo_graph` | Raw Cypher for the sample content, but every kind it writes is a **bare** `pointKind` literal from the reviewed `_DEMO_*` tables (`observation`, `statement`, `event`, `workflow`). A bare `pointKind` names no pack, so it cannot widen the approved set. (Note the asymmetry: a bare **`eventKind`** IS attributable, `_ATTRIBUTABLE_BARE_KIND_KEYS` — this writer sets none.) |
| `sdk._emit_event` (post-mutation payload append) | The pre-write doors are gated instead. The mutations it describes (`PointPromoted`, `Dedupe*`, `create_operator`) have already written their node, so a refusal at the append could only split the journal from the cache. NOT to be confused with `mining.ConversationMiner._emit_event`, which builds its own `EventRecorded` envelope and IS gated before its append (#5475/#8982). |
| `FalkorProjection.apply` on a journal-less projection | The #9068 gate sits at the five connector CALL SITES, not at this sink (see the per-call-site caveat above). A third-party connector folding directly is still ungated, and that residual is pinned as an XFAIL rather than declared closed. The sink's own refusal is `_journal_backed`-conditional, so on a journal-backed projection it DOES refuse. |
| `FalkorProjection.link_source_to_entity` on ANY projection | Same per-call-site gate, and unlike `apply` this sink has **no `_journal_backed` check at all**: the caller's `source_kind` reaches the mined `sourceKind` slot (`projection/edges.py`) on a journal-backed projection too. The only refusal on that leg is the `sdk.link_source_to_entity` WRAPPER, so a direct projection call widens on BOTH shapes. Pinned XFAIL, one case per shape, not declared closed. |
| `pipeline.py`'s `SET p.kind = <pipeline_key>` | The value is a real namespaced **pipeline key** (`venture:deal`), not a pack kind — gating the writer would start refusing legitimate pipeline edits. What had to change was the READER: the mining leg no longer treats a bare `kind` on `:Pipeline`/`:Graph` as an ontology kind (#9068, above), so this write stays ungated and no longer widens the approved set. |

A re-typing that DOES need a namespace the graph has not approved is an
operator decision: run the migration, or add the pack
(`PUT /v1/graphs/{graph_id}/packs`). Nothing on a tenant surface can widen the
set by writing — but "tenant surface" is load-bearing in that sentence, so read
the table above as the list of what it excludes, not as a footnote.

## Chains

A chain is an ordered kind path the extractor respects when connecting `about_entities`. Example: the agent-ops `ruleLifecycle` chain `[rule, rationale, ruleRevised]` — a rule must be grounded in its rationale. The chain enforcer rewires reverse-order pairs deterministically (never invents, never drops) and warns on violations. Steps resolve to your pack's kinds, core kinds, or exactly-one other pack's kinds (ambiguous bare names are validation errors — use `ns:kind`).

## Testing a pack

1. **Validate** — `tortoise pack validate <dir>` (or `--json` for machines). This runs the same schema + cross-pack validation as the daemon, and prints the **relation write-path verdict table** (`writable` / `declaration-only` / `predicate-not-in-allowlist`; `--json` carries the full list under `relation_verdicts`). The exit code covers **both** registry buckets, and scopes both to the pack you asked about: a manifest that is a typo is *isolated* (`✗ <ns>`), and a pack that loads but whose `depends_on` — or a reference — names a pack that is **not installed** is reported under `⚠ unresolved references` and fails the run too (`--json` carries them under `unresolved_refs`). The pack itself stays loaded and its own kinds, relations and chains stay usable; the references it declares *into* the absent pack do not resolve, which is what the hosted install path refuses as well. That has one exception in the other direction: a relation `nearMisses` target is resolved against predicates and semantics values, which are declared bare by contract (#2030), so a namespaced spelling there fails as `✗ <ns>` rather than `⚠` — the one way such a ref resolves is a pack that itself declares that exact namespaced predicate (nothing rejects that declaration today, and no shipped pack does it, #9760). Scoping is by the pack's **namespace**, read from its manifest, not by its directory name — `packs/investment-portfolio-manager` declares `portfolio` and `packs/project-management` declares `pm`, and a directory-name filter reported both as `✓ Clean` for a manifest the registry had already isolated (#9663). A SIBLING's gap never becomes your pack's verdict.
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

`dev`, `pm`, `marketing`, `product-strategy`, `agent-ops` are reserved starter namespaces — both the CLI scaffold and the hosted upload reject them (one guard, both surfaces). `venture` and `portfolio` ship in the catalog but are **not** starters and are not in the reserved list.

## Reference

- Machine-checkable schema: `packs/_template/manifest.yaml` (the template scaffolds from this)
- Canonical spec: `docs/ONTOLOGY.md` §9
- Governance research (enforcement layering): `docs/research/2026-08-05-expansion-pack-governance-surfaces.md`
- Worked example: `packs/agent-ops/manifest.yaml` (rules-with-why)
- Domain pack example: `packs/venture/manifest.yaml` (fund/portfolio state model — Objects hold state, Events are dated, claims are Points)
- Overlay example: `packs/investment-portfolio-manager/manifest.yaml` (declares `depends_on`, references another pack's kind namespaced, and states its value test per kind)
