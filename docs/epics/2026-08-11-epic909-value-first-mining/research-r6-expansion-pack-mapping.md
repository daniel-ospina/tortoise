---
title: "Epic #909 Research — R6: Expansion-Pack Mapping (extractor-readable pack business logic)"
type: research
domain: engineering
doc_status: draft
created: 2026-08-11
ownedBy: epistemic-team
aboutObjects: tortoise, expansion-packs, pack-registry, extractor, ontology
epic: value-first mining system (#909)
focus: R6 — entities typed by the expansion packs' business logic (read + softly enforced); pack mapping in scope
---

# Research + Design Brief — R6: Expansion-Pack Mapping

**Focus area:** R6 of the mining-system requirements
(`docs/drafts/2026-08-09-mining-system-requirements.md`). The extractor must classify
extracted entities into the packs' business-logic vocabulary (the product-strategy
chain `useCase → feature → userJourney → workflow → requirement → architecture`);
enforcement is strong but NOT a 100% hard gate; **whether packs currently encode
their logic extractor-readably is in scope** — it is not assumed solved.

**Verdict up front:** the packs do NOT encode extractor-readable business logic
today. Three concrete failures: (1) kind **semantics** (descriptions, examples,
confusables) exist nowhere a prompt can read — the LLM gets bare strings;
(2) the **business-logic chain** the owner cited is not encoded anywhere and its
kinds are split across packs with two of them (`workflow`, `architecture`) not
registered where they're needed; (3) the extractor's own kind-resolution code path
is **dead** (verified: `domain_kinds()` and 2-arg `known_kinds()` calls raise
TypeError, silently swallowed). The fix is a minimal, backward-compatible manifest
extension + a compiled "value brief" the extractor validates against, with a
warn/retry/block ladder. Design below.

---

## 1. Current state — gap analysis

### 1.1 What the manifest encodes today (`packs/*/manifest.yaml`, `pack_registry.py`)

| Aspect | Encoded | Readable by an LLM for classification? |
|---|---|---|
| Kind registration (`objectKinds`, `eventKinds`, `pointKinds`, `documentKinds`) | Names only, namespaced `ns:kind` | ✗ — bare strings, zero semantics |
| Kind semantics (definition, examples, synonyms) | **Not encoded anywhere**; core descriptions hardcoded in `extractor.py:355-367` (`_DEFAULT_POINT_KIND_DESCRIPTIONS`) | ✗ |
| Inheritance (`subclassOf`) | Yes, one level, parent must be a core PascalCase kind | Partial — parent chain not compiled for prompts |
| Equivalence (`equivalentTo`) | Yes, bidirectional, cross-pack | Partial |
| Relations (predicate, fromKind, toKind, mechanism, semantics, cardinality) | Yes, for ~10 binary edges | Partial — no "may the extractor assert this?" marker |
| `hierarchies` (path strings) | Parsed + validated, **never loaded into `PackManifest`** (no field) | ✗ — UI-only intent, dead |
| Business-logic chains (ordered kind sequences) | **Not encoded anywhere** | ✗ |
| Extraction-active vocabulary (which kinds per source type) | **Not encoded** | ✗ |
| Enforcement levels (warn/retry/block per kind/relation) | **Not encoded** (write-side SDK warns on unknown kinds; REST API blocks; inconsistent) | ✗ |

### 1.2 Empirical failures verified in the repo

1. **The pack vocabulary never reaches the extractor prompt.** `extractor.py:745-746`
   calls `domain_kinds(domain, "pointKind")` and `:879-885` calls
   `known_kinds("pointKind")` — neither function exists in `domain_loader.py`
   (verified: `AttributeError`/`TypeError`, swallowed by bare `except` "ponytail"
   fallbacks). `_warn_unrecognized_kinds` (`extractor.py:383-396`) calls
   `kind_is_known(k, "pointKind")` with two args — `kind_is_known()` takes one
   (verified TypeError). **Every domain-kind path in the extractor is dead code.**
2. **When kinds DO reach the prompt they're bare strings.** `_build_pointkind_prompt(['useCase','userJourney'])`
   → `- useCase\n- userJourney`. No descriptions, no examples, no confusable
   guidance. The owner's complaint — *"'Object:product are not identifying the
   product' … 'value-first extraction' no idea what that is"* — is exactly this:
   the vocabulary is opaque to the classifier.
3. **The owner's chain is not encoded and its kinds are fragmented.** The chain
   `useCase → feature → userJourney → workflow → requirement → architecture`
   spans: `useCase`/`userJourney` = product-strategy pointKinds; `feature` =
   product-strategy objectKind; `workflow` = **core** objectKind (canonical — packs
   may not register it); `requirement` = dev pointKind; `architecture` = **not a
   kind anywhere** (dev only has the *document* kind `architectureDoc`). No manifest
   declares the chain, and product-strategy's own `pointKinds` (`jobToBeDone,
   useCase, userJourney, valueProposition`) appear in **zero** relations — the
   pack's epistemic kinds are disconnected from its semantic kinds.
4. **Three competing kind sources, none canonical.** `pack_registry.py` (packs),
   `domain_loader._BASE_KINDS` (hardcoded: `workflow, requirement, issue, session,
   milestone, incident, concept, policy…`), and `config/domain_manifest.yaml`
   (`kind_values`). `sdk._validate_kind` warns ("open-ended vocabularies — any
   string accepted") while `hosted_api.CreatePointRequest.valid_kind` **blocks**
   unknown kinds. Enforcement posture is inconsistent across surfaces.
5. **Write-side governance research exists but is not wired.** 
   `docs/research/2026-08-05-expansion-pack-governance-surfaces.md` (live) already
   recommends Manifest v2 with `requiredPath`/`severity`, SDK-level validation,
   warn-not-block defaults for hierarchy bypasses. R6 is the **read-side**
   complement: the same declarative source compiled for the extractor. The two
   must share one compiled vocabulary (see §5.4).

### 1.3 Gap summary (what the extractor needs but the manifest lacks)

1. **Kind semantics** — per-kind `description`, `examples`, `synonyms`, `nearMisses`
   (confusable groups), so classification is a judgment call, not a name match.
2. **First-class chains** — the ordered business-logic sequences (product-strategy's
   useCase→…→architecture is the flagship; dev's epic→issue→code and marketing's
   campaign→content→channel are latent examples), with cross-pack step references
   (core kinds allowed bare).
3. **Extraction activation** — per-pack `active` + `sourceTypes` (conversation vs
   document vs github_issue may activate different packs), and per-kind
   `extractable`/`storeAs` (entity vs tag vs claim).
4. **Relation extractability** — `extractable: true` on relations the extractor may
   assert between typed entities (default false → backward compat).
5. **Enforcement configuration** — per-kind/relation/chain `warn|retry|block` with a
   pack default, so "strong but not 100% hard" is declarative, not prompt folklore.

---

## 2. External best practices — schema-driven extraction vocabularies

> Method: 2+ independent sources per cluster; confidence noted per row.

### 2.1 KAG / OpenSPG (schema-constrained construction) — [HIGH, 4 sources]
KAG runs **two extraction modes on the same corpus**: `SchemaFreeExtractor` and
`SchemaConstraintExtractor` (kg-builder component registry, DeepWiki). The schema is
a **first-class project artifact** written in SPG schema-mark language (project-level
SPG Classes with built-in/expert-defined properties and relations), consumed
directly by the constrained extractor — the LLM never sees an ad-hoc prompt, it
sees the compiled schema. KAG's paper (arXiv 2409.13731) frames this as
"LLM-friendly schema representation": expert schema + text mutual-indexed so
extraction output stays graph-shaped. **Takeaway: the pack manifest must compile to
an LLM-readable schema artifact, not be re-described per call.**

### 2.2 Neo4j LLM Knowledge Graph Builder (neo4j-graphrag-python) — [HIGH, 4 sources]
The closest production template to what R6 needs. Schema is a plain dict:
`{node_types: [{label, description, properties}], relationship_types: [{label,
description}], patterns: [(src_label, rel_label, dst_label)], additional_node_types:
False}`. Three properties worth copying:
- **Descriptions ride with the schema** (`"label": "House", "description": "Family
  the person belongs to"`) — the description is prompt material, exactly what the
  packs lack today;
- **`additional_node_types: False` closes the vocabulary** — the LLM is grounded to
  the allowed types, the closed-set signal is explicit;
- **`patterns` are typed triples** `(Person, PARENT_OF, Person)` — the extractor may
  only emit schema-declared edge shapes; a graph pruner + entity resolver
  (merge same label+name) run after extraction.
Also: **structured output (JSON Schema) enforcement** where the provider supports it
— API-level conformance, i.e., R8 layer 1 done right.

### 2.3 OntoGPT / SPIRES + LinkML — [HIGH, 3 sources]
Schema-first extraction where **every class and slot carries a description that IS
the prompt**: OntoGPT "custom schemas" define `classes:` (the things to extract)
with `attributes:` (each with `description` passed verbatim into the prompt,
`multivalued`, `range` = class restriction → typed edges, `is_a` inheritance),
plus **enums = closed value sets** (the controlled-vocabulary mechanism) and
`annotators`/`id_prefixes` for grounding to external vocabularies. SPIRES generates
the full extraction prompt from the schema, then grounds names to existing
vocabularies — LLM emits names, deterministic grounding assigns IDs (same as our
pipeline's "no LLM-minted identifiers" rule, S5). **Takeaway: description is a
first-class schema field because it is literally prompt content; inheritance
(`is_a`) is one level in practice.**

### 2.4 Zep Graphiti — [HIGH, 5 sources]
Custom entity/edge types via **Pydantic models** — the schema is a validation
artifact, not just a prompt: extracted entities are validated against it before
write. Extraction order is `extract entities → dedupe entities → extract facts →
dedupe facts` with an entity-resolution prompt; "without schema is just storage"
(dev.to, Zep blog). Constraints: bounded entity-type/field sets; dedup +
contradiction detection post-extraction. **Takeaway: schema doubles as the
validation gate (deterministic, post-LLM) — matching R8 layer 1's contract gate.**

### 2.5 Over-constraint warning (adversarial) — [MEDIUM, 2 sources + prior internal research]
Format-constraint coupling in KG construction (arXiv 2605.21974, cited in our
governance research): rigidly applied constraints amplify errors — "extraction
refusal" and entity inflation; KG-RAG research warns unenforced schemas cause
constraint-blind traversal. Our own 2026-08-05 governance research already
concluded **warn-not-block default** for bypasses. **Takeaway: the enforcement
ladder (§4) is the mitigation — block only true hazards, warn/retry the rest.**

### 2.6 Synthesis — the representation pattern
Across all four systems, "what can be extracted" = **a closed set of typed kinds
with descriptions + a closed set of typed relation shapes + explicit inheritance**,
compiled into (a) the LLM prompt AND (b) a deterministic validator. None of them
encode *ordered business chains* (useCase→feature→…), which is Tortoise's
distinctive need (owner R6) — we extend the pattern with `chains:` (§3.2).

---

## 3. Proposed manifest extension (Manifest v3 — backward compatible)

Backward compatibility rule: **all existing fields keep their meaning; a manifest
without the new sections behaves exactly as today** (all kinds extractable,
relations non-extractable, enforcement = warn). `pack_registry` validation stays
strict on the new fields.

### 3.1 `ontology.kindDefs` — per-kind extractor metadata

Map keyed by the pack-local kind name; keys must be declared in one of the pack's
`*Kinds` lists. All fields optional except `description` (encouraged but not
mandatory — enforcement defaults apply).

```yaml
kindDefs:
  useCase:
    description: "A concrete scenario in which a user achieves a goal using the product"
    synonyms: ["use case"]                 # normalized aliases (lowercase, strip punctuation) — deterministic near-miss catch
    examples: ["As a PM I can see the roadmap so I can plan releases"]
    nearMisses: [userJourney, jobToBeDone] # confusable kinds — prompt guidance + WARN/retry trigger
    extractable: true                      # default true; false = registered but never minted by extraction
    storeAs: claim                         # claim|decision|entity|tag|event — default inferred from list placement
    enforcement: retry                     # warn|retry|block — overrides pack default (§3.5)
```

`storeAs` inference: `pointKinds → claim` (decision/vision/strategy/plan/goal/
target/humanApproval → `decision`), `objectKinds → entity`, `subjectKinds → entity`,
`documentKinds → entity`, `eventKinds → event`. Explicit override wins.

### 3.2 `ontology.chains` — first-class business-logic chains

The owner's chain as a declarative ordered sequence. Steps may reference this
pack's kinds (bare), other packs (`ns:kind`), or **core kinds bare** (`workflow`).
`edges` are optional and must reference declared relations or core predicates;
omitted edges default to the step order (step i → step i+1, predicate
`addresses`/IMPL).

```yaml
chains:
  - id: productDelivery
    name: "Product Delivery Chain"
    description: "How product strategy flows into shipped architecture"
    steps: [useCase, feature, userJourney, workflow, requirement, architecture]
    enforcement: warn        # chain-bypass severity: warn (default) | retry | block
```

Validation: every step resolves to a known kind (core or pack, cross-pack checked
post-load like relations); `enforcement` in {warn, retry, block}; `id` unique.

### 3.3 `ontology.relations` — `extractable` flag (additive)

New optional boolean on existing relation objects. Default `false` → the extractor
never asserts typed domain edges unless a pack opts in (backward compat; S3 of the
pipeline only emits IMPL/NAND today).

```yaml
- predicate: addresses
  mechanism: IMPL
  semantics: addresses
  fromKind: product-strategy:useCase
  toKind: product-strategy:feature
  extractable: true          # NEW — may be asserted by the extractor
```

### 3.4 `extraction` — top-level activation + enforcement config

```yaml
extraction:
  active: true                              # default true; false = pack kinds never enter the brief
  sourceTypes: [conversation, document]     # empty/default = all source types activate this pack
  enforcement:
    default: warn                           # pack-wide default level
    kinds: { useCase: retry, userJourney: retry }
    relations: { addresses: warn }
    chains: { productDelivery: warn }
```

Semantics: a source type activates a pack iff `active` and (`sourceTypes` empty or
contains the type). Enforcement resolution order: kindDefs[].enforcement →
extraction.enforcement.kinds → extraction.enforcement.default → `warn`.

### 3.5 Validation additions to `pack_registry._validate`

- `kindDefs` keys ⊆ declared kinds of the pack; values are maps with allowed keys
  `{description, synonyms, examples, nearMisses, extractable, storeAs, enforcement}`;
  `storeAs ∈ {claim, decision, entity, tag, event}`; `enforcement ∈ {warn, retry,
  block}`; `nearMisses`/`synonyms` are lists of strings; `nearMisses` targets must
  be known kinds (cross-pack checked post-load).
- `chains`: `id`/`steps` required; steps resolve (post-load cross-pack pass);
  `enforcement` validated.
- `extraction.sourceTypes` ⊂ known source-kind vocabulary
  (`conversation, document, github_issue, slack_message, linear_card…` — the
  `sourceKind` extensible vocabulary, ONTOLOGY §4.6); unknown source types → error
  (typo protection) with an escape hatch list for future connectors.
- `relations[].extractable` must be a boolean.
- **Template (`packs/_template/manifest.yaml`) gains commented sections** for all
  four additions.

### 3.6 What we deliberately do NOT add (scope discipline)

- No SPG/LinkML-style property schemas per kind (typed attributes) — v1 extraction
  is name + kind + content; properties are a later evolution (the
  `properties` slot pattern from Neo4j/OntoGPT is noted for the manifest v4 line).
- No multi-hop `requiredPath` enforcement (governance research deferred this;
  chains v1 = adjacency between steps).
- No kind lifecycle/versioning (deferred in governance research D3/D4).

---

## 4. Worked example — product-strategy manifest extended

Full diff-shaped example. The chain requires two kinds product-strategy does not
own today: `workflow` resolves to the **core** kind (bare reference — packs cannot
re-register canonical kinds, `pack_registry` enforces); `requirement` is declared
with `equivalentTo: [dev:requirement]` (semantic unification, bidirectional); and
`architecture` is **new** (objectKind, subclassOf Document — dev's `architectureDoc`
remains the *document kind* of architecture artifacts; flagged as an open decision
in §6.1).

```yaml
namespace: product-strategy
name: "Product Strategy"
version: "0.2.0"          # bumped: extractor-readable business logic (R6)
tier: free
description: "Product strategy concepts — products, features, customers, competitors, markets"

ontology:
  extends: core

  objectKinds:
    - product
    - feature
    - customer
    - competitor
    - customerSegment
    - market
    - requirement        # NEW — chain step 5; equivalent to dev:requirement
    - architecture       # NEW — chain step 6; the design/structural artifact

  subclassOf:
    feature: WorkItem
    requirement: WorkItem
    architecture: Document

  equivalentTo:
    requirement: [dev:requirement]

  pointKinds:
    - jobToBeDone
    - useCase
    - userJourney
    - valueProposition

  documentKinds:
    - competitiveAnalysis
    - marketResearch
    - productSpec
    - featureSpec

  # ── NEW: per-kind extractor metadata ─────────────────────────────────────
  kindDefs:
    product:
      description: "A marketable offering the company sells"
      examples: ["Tortoise", "El Dato"]
      nearMisses: [feature]
    feature:
      description: "A capability of the product that delivers value to a customer segment"
      synonyms: ["capability", "function"]
      nearMisses: [product, requirement]
    customer:
      description: "An organization or person who buys or uses the product"
      nearMisses: [customerSegment]
    customerSegment:
      description: "A group of customers with shared needs the product targets"
      synonyms: ["segment", "target market"]
      nearMisses: [customer, market]
    market:
      description: "The competitive arena the product operates in"
      nearMisses: [customerSegment]
    competitor:
      description: "An organization offering a product that competes with ours"
    useCase:
      description: "A concrete scenario in which a user achieves a goal using the product"
      synonyms: ["use case"]
      examples: ["As a PM I can see the roadmap so I can plan releases"]
      nearMisses: [userJourney, jobToBeDone, requirement]
      enforcement: retry      # confusable group → corrective retry before accept
    userJourney:
      description: "The ordered steps a user takes to complete a goal with the product"
      synonyms: ["journey", "flow"]
      examples: ["signup → first import → first query → subscribe"]
      nearMisses: [useCase, workflow]
      enforcement: retry
    jobToBeDone:
      description: "The underlying job a customer hires the product to do"
      synonyms: ["jtbd"]
      nearMisses: [useCase]
    valueProposition:
      description: "Why a customer segment should choose this product over alternatives"
      synonyms: ["value prop"]
    workflow:
      description: "A reusable procedural sequence (core kind; chain step 4)"
      nearMisses: [userJourney]
    requirement:
      description: "A stated need a feature must satisfy (≡ dev:requirement)"
      nearMisses: [useCase, feature]
    architecture:
      description: "The design and structural decisions for how a solution is built"
      synonyms: ["architecture design"]
      nearMisses: [feature]

  # ── NEW: the owner's business-logic chain, first-class ──────────────────
  chains:
    - id: productDelivery
      name: "Product Delivery Chain"
      description: "How product strategy flows into shipped architecture"
      steps: [useCase, feature, userJourney, workflow, requirement, architecture]
      enforcement: warn       # bypass → warn (block is opt-in per pack)

  # ── relations: existing 4 + 2 new chain edges (extractable) ─────────────
  relations:
    - predicate: contains
      mechanism: IMPL
      semantics: hasPart
      fromKind: product-strategy:product
      toKind: product-strategy:feature
      cardinality: one_to_many
    - predicate: addresses
      mechanism: IMPL
      semantics: addresses
      fromKind: product-strategy:feature
      toKind: product-strategy:customerSegment
    - predicate: competesWith
      mechanism: NAND
      semantics: opposes
      fromKind: product-strategy:feature
      toKind: product-strategy:competitor
    - predicate: targets
      mechanism: IMPL
      semantics: addresses
      fromKind: product-strategy:product
      toKind: product-strategy:customerSegment
    - predicate: addresses                     # NEW — chain edges the extractor may assert
      mechanism: IMPL
      semantics: addresses
      fromKind: product-strategy:useCase
      toKind: product-strategy:feature
      extractable: true
    - predicate: addresses
      mechanism: IMPL
      semantics: addresses
      fromKind: product-strategy:requirement
      toKind: product-strategy:architecture
      extractable: true

  hierarchies:
    - path: "Product → Feature"
      type: hasPart

# ── NEW: extraction activation + enforcement config ───────────────────────
extraction:
  active: true
  sourceTypes: [conversation, document]
  enforcement:
    default: warn
    kinds:
      useCase: retry
      userJourney: retry

connectors: []
tools: []
depends_on: []
```

**What the extractor now reads** (compiled brief, ~900 tokens for this pack):

```
POINT/CLAIM KINDS (closed set — do not invent kinds):
- product-strategy:useCase: a concrete scenario in which a user achieves a goal
  using the product. e.g. "As a PM I can see the roadmap so I can plan releases".
  Confusable with: userJourney, jobToBeDone, requirement.
- product-strategy:userJourney: the ordered steps a user takes to complete a goal…
  Confusable with: useCase, workflow.
…
ENTITY KINDS: product-strategy:product (a marketable offering…), feature (a
capability…), customer, customerSegment, market, competitor, requirement, architecture
CORE: workflow (reusable procedural sequence)
BUSINESS CHAIN productDelivery (order matters — place entities in it):
useCase → feature → userJourney → workflow → requirement → architecture
ALLOWED TYPED EDGES: (useCase)-[addresses/IMPL]->(feature); (requirement)-[addresses/IMPL]->(architecture)
ENFORCEMENT: unknown kind = BLOCK (drop + reason); near-miss = retry once then accept-with-flag; chain bypass = warn
```

---

## 5. Compilation + enforcement design (buildable)

### 5.1 New module: `tortoise/value_brief.py` (compiler)

```python
def compile_value_brief(source_type: str = "conversation",
                        namespaces: list[str] | None = None,
                        max_tokens: int = 2000) -> ValueBrief
```

- Walks `PackRegistry.packs`, applies §3.4 activation (`active`, `sourceTypes`),
  merges `kindDefs` (across packs + core defaults — migrate
  `extractor._DEFAULT_POINT_KIND_DESCRIPTIONS` into a core-kind-defs table here),
  resolves chains (bare → core or single-namespace; ambiguous bare names → error at
  load), filters `extractable: true` relations.
- `ValueBrief` dataclass: `{pointKinds[], objectKinds[], subjectKinds[],
  eventKinds[], chains[], relations[], enforcement{default, perKind, perRelation,
  perChain}}` — serialized to the prompt block (§4 example) and to a JSON form for
  the validator.
- Token cap 2000: compile core kinds + chain kinds + top-N by declaration order;
  overflow → truncate non-chain kinds (chains are never truncated).

### 5.2 New module: `tortoise/extraction_enforcer.py` (deterministic validator)

Runs AFTER the LLM stage (S2/S3 outputs), BEFORE write (S5/S6) — zero LLM cost.

```python
def validate_item(item: ExtractedItem, brief: ValueBrief) -> EnforcementVerdict
# EnforcementVerdict = {level: pass|warn|retry|block, kind, suggestion, reason}
```

Item-level granularity: **a blocked item is dropped with a logged reason; the run
continues** (only R8 stream-shape failures fail the run — see §5.5). Every
non-pass verdict writes a violation event (event-log `type: ExtractionViolation`,
payload `{class, kind, suggestion, source_ref}`) feeding the governance/review
surface (2026-08-05 layered-defense layer 3) and the R8 layer-2 measurement loop.

### 5.3 Enforcement taxonomy — warn / retry / block, per error class

| # | Error class | Example | Detection (deterministic) | Level (default) | Action on failure |
|---|---|---|---|---|---|
| E1 | **Kind mint** (LLM-invented) | `productRoadmap` | membership in compiled vocab | **BLOCK** | retry once with brief re-emphasized → still unknown → drop + reason + violation event. Hard rule (R6: never LLM-minted kinds) |
| E2 | **Kind near-miss** (alias/typo) | `usecase`, `user-journey`, `capabilty` | synonym map + normalized edit-distance ≤1 | **RETRY** | deterministic fix if unique alias; else one corrective retry → accept-as-fixed or **WARN**-accept with flag |
| E3 | **Confusable choice** | `userJourney` vs `useCase` genuinely ambiguous | kind ∈ nearMisses group of chosen kind | **WARN** | accept with flag + suggestion; no retry (both defensible; retry burns cost with no ground truth) |
| E4 | **Entity-type mismatch** | objectKind value in claims stream | kindDefs `storeAs` vs stream bucket | **BLOCK** | auto-route if unambiguous (kind is only in one bucket) → else drop + reason |
| E5 | **Undeclared relation** | `(useCase)-[implements]->(competitor)` | relation catalog (extractable set) | **WARN** | do not write the edge; log intent + suggestion; violation event |
| E6 | **Chain bypass** | `useCase → architecture` (skips 4 steps) | chain step positions | **WARN** (chain `enforcement: block` opt-in) | do not write the skip edge; attach nearest valid intermediate kinds if determinable; violation event |
| E7 | **Out-of-scope kind** | valid kind, pack inactive for source type | activation per §3.4 | **WARN** | demote: store as Tag on the Source / parent-kind entity; log |
| E8 | **Missing provenance** | no `source_ref`/span | R8 layer-1 fields | **BLOCK** | retry once (re-ask for source) → drop + reason (R4: provenance is non-negotiable) |
| E9 | **Malformed stream** | missing required fields, wrong types | R8 layer-1 schema | **BLOCK** | retry once → **run fails** with reason (this is the one run-level class — R8 contract) |
| E10 | **Vague superclass** | `statement` chosen while a specific kind exists | heuristic: specific kind exists + low confidence | **WARN** | accept + suggestion; feeds prompt-improvement loop |

**Ladder semantics:** `warn` = write/keep with flag + violation event (never blocks);
`retry` = one corrective cheap-model call, then success → PASS, failure → escalate
per class (E2 → WARN-accept, E1/E8 → BLOCK); `block` = item dropped with reason
(not written), run continues. **BLOCK is reserved for E1, E4, E8, E9** — the true
out-of-schema hazards; everything else is a near-miss that warns or retries. This
satisfies "strong but NOT a 100% hard gate."

**Thresholds/caps (cost + degradation guards, consistent with the pipeline spec):**
- ≤1 retry per item; ≤5 retries per session; retries only on the cheap model.
- Block rate per session > 15% → fail closed to extract-nothing for the session +
  alert (same spirit as the keep-ratio >40% guard; a vocabulary that blocks
  constantly is misconfigured, not the LLM being wrong).
- Near-miss (E2/E3/E10) rates tracked per kind per pack → calibration loop: kinds
  with sustained confusability get better `description`/`nearMisses`/`examples`
  (R8 layer 2 semantic thresholds feed this).

### 5.4 Read/write unification (scoping note)

`domain_loader` becomes a **thin adapter over `PackRegistry`** (single compiled
vocabulary): fix the dead calls (`domain_kinds()`, arg-taking `known_kinds()`) in
`extractor.py`; `sdk._validate_kind` and `hosted_api` validators both consume the
same brief (SDK keeps warn for humans writing directly; REST keeps block; the
extractor uses the §5.3 ladder). `config/domain_manifest.yaml` `kind_values` migrate
into pack manifests (or are marked legacy) — three kind sources collapse to one.

### 5.5 R8 reconciliation (contract vs soft enforcement)

R8 layer 1 lists "kind ∈ closed vocabulary" as a deterministic contract field.
Reconciliation, made explicit: **the closed-vocabulary check IS the E1 block**, but
enforcement granularity is the *item*: a single minted kind drops that item, not
the run. The run-level contract gate (retry-once → fail) applies only to **stream
shape** (E9) — malformed JSON, missing required fields, non-atomic points. This
keeps R8's CI-blocker property (shape) while honoring R6's "not 100% hard gate"
(classification). Both layers get their thresholds set in scoping; the brief
recommends: contract = 100% shape-conformant streams (deterministic), semantic
(R6) = near-miss/block rates within budget per the §5.3 thresholds.

---

## 6. Scope notes for planning

### 6.1 Open decisions (for scoping, not resolved here)

1. **`architecture` kind placement**: objectKind subclassOf Document (worked example)
   vs pointKind vs "resolve to dev:architectureDoc". Recommendation: objectKind +
   `equivalentTo` later if dev declares one. `workflow` stays core (canonical).
2. **`requirement` ownership**: ps-declared + `equivalentTo dev:requirement` (worked
   example) vs chain referencing `dev:requirement` directly. Either is valid;
   equivalentTo keeps the chain self-contained.
3. **Source-type vocabulary**: `extraction.sourceTypes` values come from the
   `sourceKind` vocabulary — v1 restrict to `[conversation, document]` + allowlist;
   connectors' sourceKinds register later.
4. **Violation-event destination**: event log vs dedicated store (deferred in
   2026-08-05 governance research; v1 = event log with `ExtractionViolation` type).

### 6.2 Deliverables (small, buildable slices)

1. **Pack schema v3** — `pack_registry.py`: `kindDefs`/`chains`/`extractable`/
   `extraction` parsing + validation + template update + tests
   (`tests/test_pack_kinds.py` style). Self-contained, no pipeline dependency.
2. **`value_brief.py` compiler** + core-kind-defs table (migrates the hardcoded
   extractor descriptions) + `ValueBrief` serialization + token cap.
3. **Pack content updates** — product-strategy (worked example §4), dev
   (`chains: [epic→issue→code]`, `requirement` description, architectureDoc link),
   marketing (campaign→content→channel chain), pm (issue/sprint/card eventKinds
   enrichment). Each is a small manifest PR with registry-test coverage.
4. **`extraction_enforcer.py`** + ladder (§5.3) + violation events + threshold
   guards. Wired at S2/S3/S5 output validation in `value_extractor.py`; fixes the
   dead `domain_kinds`/`known_kinds` calls.
5. **domain_loader unification** (§5.4) — collapse three kind sources to one.

### 6.2a Verified compatibility note (worked example tested against current registry)

The §4 manifest was loaded through the **current** `PackRegistry` (temp dir with all 4 real
packs + the extended product-strategy): 4 packs load, **zero validation errors** — the
extension is backward compatible by construction. Two findings from the run:

1. `architecture: Document` **validates but does not expand**: `expand_kind('Document')`
   returns `['Document']` only, because `_build_kind_expansions` builds subclass expansion
   for `CANONICAL_*` parents but `Document`/`Source` (core entity types, not canonical
   objectKinds) are not in that loop. Trivial fix in slice 1: add the core entity types
   (`Document`, `Source`, `Subject`, `Point`, `Event`) as expansion parents. Alternative:
   declare `architecture: WorkItem` (expands today — verified) — but Document is
   semantically correct, so fix the registry.
2. `expand_kind('WorkItem')` correctly includes `ps:feature`, `ps:requirement`, plus all
   other packs' WorkItem subclasses — subclass + equivalence semantics keep working
   with the new kinds.

### 6.3 Non-goals (explicit)

- Per-kind typed properties (manifest v4 line — Neo4j/OntoGPT property patterns).
- Multi-hop `requiredPath` enforcement; kind lifecycle/versioning.
- Auto-schema generation from text (Neo4j `EXTRACTED` mode) — packs stay
  human-authored (they encode business logic, which cannot be inferred from text).

---

## 7. Source confidence summary

| Claim | Confidence | Sources |
|---|---|---|
| Packs today carry no kind semantics; LLM sees bare strings | High (verified in repo) | extractor.py `_build_pointkind_prompt`, manifests |
| Extractor domain-kind code path is dead (TypeError, swallowed) | High (verified empirically) | `python -c` run against `domain_loader` |
| Owner's chain is not encoded; kinds fragmented across 3 sources; `architecture` missing | High (verified in repo) | pack manifests + `pack_registry` canonical sets |
| Schema = closed kinds + descriptions + typed patterns, compiled to prompt AND validator | High | Neo4j KG builder (4), OntoGPT/LinkML (3), KAG (4), Graphiti (5) |
| Warn-not-block default for bypasses; rigid constraints amplify extraction errors | Medium-High | arXiv 2605.21974, KG-RAG research, internal governance research (2026-08-05) |
| Ordered business chains as first-class schema objects | Novel (no external precedent found) | owner R6 requirement — design proposal §3.2 |
