---
title: "#11656 scope — endpoint-kind binding for declared pack relations"
type: engineering
domain: platform
doc_status: live
created: 2026-11-09
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: expansion packs, ontology enforcement, write path, AX
issue: daniel-ospina/tortoise#11656
epic: daniel-ospina/tortoise#7949
depends_on: daniel-ospina/tortoise#11000
stage: task-scope
---

# #11656 — scope

**Input:** `docs/research/2026-11-09-11656-kindpair-enforcement.md` (Parts I + II).
**Rulings:** owner, 2026-11-09 — D1, D2, D3, D5 recorded on #11656 (comment 6753057867).
**Revision 2** — rewritten after a fresh review found 1 P0 + 5 P1 at the revision-1 head (PR #11671
comment 6754329984). Every number below was re-measured un-truncated; the three claims that were wrong are
named in §10 so the correction is auditable.

---

## 1. Confirmed problem

A pack declares `relations[]: {predicate, mechanism, fromKind, toKind}`. **Nothing checks that a written
edge's endpoints have those kinds.** 36 declared relations across 7 packs; 35 carry `fromKind`+`toKind`.

> ⚠️ **Re-measured at `3d63339d` after `main` was merged into this branch.** The merge brought three new
> kind-bearing declarations (`pm:blocks` → `pm:card→pm:card`, `pm:partOfSprint` → `pm:sprint→pm:card`,
> `pm:assignedTo` → `pm:card→Subject`, all `IMPL`) plus `pm:onBoard`, which declares one `from`/`to` and no kinds. The figures below were first measured at `e809803e` (32 kind-bearing, 31 `IMPL` + 1 `NAND`,
> 30 direct-shape (26 Object→Object), 7 disabled by §5.2) and are restated here at **this** head: **35 kind-bearing,
> 34 `IMPL` + 1 `NAND`, 28 Object→Object (33 direct-shape), 8 disabled by §5.2**. The argument does not move — every
> kind-bearing declaration still rides the operator path, and `create_edge` is still the wrong seam — but the
> counts are what a lane implements against and what pins 1, 8 and 16 assert, so they are stated at the head
> they were measured on. Reproduce with the one-liner in the research brief §2.1. The `.py:line` citations
> below are verified at the same merged head; they are line numbers, so a later `main` will move them.

### 1.1 Three doors, not one — and the direct door is the dominant one

⚠️ **Revision 1 said two doors and excluded `create_direct_edge`. That was wrong, and it was a hole in the
issue's own target.** Measured:

| door | how a declared **label** reaches it |
|---|---|
| `create_operator` | `create_operator(op_type, src, [dst], label=<predicate>)` — and `ingest`'s reified arm |
| `create_edge` / `create_about_edge` | the structural predicates only |
| **`create_direct_edge`** | **`link_entities` `sdk.py:18070-18078`**: `if upper in ("IMPL","NAND") and operator is not True:` → `create_direct_edge(upper, from, to, …, label=label, …)`. **`ingest` reaches it too** (`sdk.py:28497`, the "operator-less per §8" arm) |

`create_direct_edge` takes `label` and validates **nothing** about declarations or kinds — its guards are
`op_type ∈ {IMPL,NAND}`, endpoints differ, confidence range, direction, endpoint exists / not-operator /
not-terminal, and the claim-class override. `undeclared_relation` fires only in `create_operator`
(`sdk.py:23196`).

**And the direct door is the *dominant* shape, not a corner.** `_point_mediated_route` (`sdk.py:25041`)
reifies only when a `:Point` is involved — *"`Object↔Object` keeps the direct shape … `Point↔Object` is
operator-mediated … `Subject`/`Event` endpoints are not Point-bearing and take the Object path"*
(`sdk.py:25058-25060`) — and the 35 kind-bearing declarations partition as **28 Object↔Object**, **5 more
direct-shape but not Object↔Object** (3 `Object→Subject`, 1 `Object→Event`, 1 `Event→Object`), and **2
Point-involved** (`agent-ops:groundedIn` via `pointKinds: rationale`, `product-strategy:addresses`
`useCase→feature` via `pointKinds: useCase`) — so **33 of the 35** take the direct shape through
`link_entities` and through `ingest` alike, and **the other 2 are reified by `ingest` only**. ⛔ The Point gate
is `ingest`'s, not `create_direct_edge`'s: `_effective_route` is called only inside `ingest`/its helpers
(`sdk.py:27455`, `:28378`, `:28445`), and `create_direct_edge`'s own docstring says so — *"`ingest()` adds the
terminal-endpoint guard below PLUS the Point-mediated route gate (#8499)"* (`sdk.py:29066-29067`). So
`link_entities` writes those 2 as Object→Point direct edges today, which is why pin 14's `link_entities` arm
must cover a Point-bearing pair. ⚠️ The earlier "32 of 35" figure was the
**non-`Subject`** count relabelled (35 − 3 `Subject`), which absorbed the four non-`Subject` non-Object rows.
So a declared IMPL label over a mismatched **direct-shape** pair is writable today with no gate. **⇒ `create_direct_edge` is IN scope, gating the `label` only**
(its epistemic semantics are untouched).

### 1.2 Six call sites, and **two** of them are replay folds

1. `sdk.create_operator` — and `ingest`'s reified arm inherits it.
2. `sdk.create_direct_edge` — and `link_entities` + `ingest`'s plain-operator arm inherit it.
3. `projection/edges.py::_EdgeHandlers.create_edge`
4. `projection/edges.py::_EdgeHandlers.create_about_edge`
5. `projection/edges.py::_EdgeHandlers._create_edges` (**the operator replay fold**, `:1497`, from
   `projection/entities.py:3021`) — it MERGEs operator edges directly and never calls `create_edge`. A
   live-only gate **diverges on `rebuild_all`**: the `derived == replay(journal)` class. The code says the
   two write one shape (*"#7902 fold-parity"*).
6. `projection/__init__.py`, the **`DirectEdgeCreated` replay fold** (`rebuild_all` pass-2b, fold starts
   `:16955`; it reads `etype = ev.get("edge_type")` at `:17027` and MERGEs it at `:17109`, *"mirroring
   create_direct_edge byte-for-byte on the edge shape"*) — the second fold, for the **other** label door.
   ⛔ **Where the predicate lives in a record at this fold is PRODUCER-DEPENDENT, and getting it wrong
   makes the gate a no-op:**
   - **structural-edge lane** (`sdk._record_structural_edge`, `sdk.py:12079`) journals `edge_type=rel`
     (`sdk.py:12142`), so `edge_type` **is** the declared predicate — `ownedBy`/`hasPart`/`dependsOn`, which
     the fold's own code names (*"the structural-edge lane's `ownedBy`/`hasPart`/`dependsOn`/… (#9553
     class 4)"*, `:17065`);
   - **`create_direct_edge`** does **not** put its label in the rel type: it writes
     `rel_type = op_type  # IMPL / NAND` (`sdk.py:29214`) and carries the declared predicate as an edge
     **property** — `attrs["label"] = label` (`sdk.py:29238`), restored by this fold through
     `DIRECT_EDGE_ATTR_KEYS` (`edges.py:90-91`, applied at `__init__.py:17126`).

   ⇒ the rule at this fold must read the predicate as **`ev["label"]` when present, else `ev["edge_type"]`**.
   A gate keyed on `edge_type` alone sees `IMPL`/`NAND` — a predicate no pack declares — returns `unbound`, and
   never enforces the dominant door. Gating item 2 live and this fold not gated reproduces the same divergence
   as item 5, on the door §1.1 says is dominant.

⚠️ **This list is an inventory, NOT a completeness claim — and that distinction is the point.** The scoping
lane's own handoff on #11656 (comment 3020037081) records the failure class: four rounds each asserted a writer
set ("two" → three → five → six) and each was wrong, because a hand-count over `MERGE (a)-[:{rel}]->(b)` sites
misses one. So completeness is **measured, not claimed**: pin 18 re-derives the set of parameterised-rel-type
Cypher writers from the tree on every CI run and reds when one of them does not route through the rule. A lane
implements pins 1–18; it does **not** treat this list as the specification.

### 1.3 A declaration cannot be keyed on the predicate name

⚠️ **Revision 1's evidence for this was wrong** (it read `semantics: hasPart` as `predicate: hasPart`). The
claim holds, on different evidence:

```text
addresses   declared 6×, 6 DISTINCT pairs   (4 of them inside product-strategy alone)
contains    2×, 2 pairs                     (marketing, product-strategy)
targets     2×, 2 pairs
hasPart     1×                                   <- NOT a multiplicity case
```

So one predicate legitimately carries six different declared pairs. Enforcing a declaration on the predicate
**name** globally would refuse most of them — which is the modelling error RDFS names when it says multiple
`rdfs:domain` values **conjoin**, and the reason `schema:domainIncludes` exists.

The live-graph counts stand but must be read correctly: 17 `dependsOn` edges and 47 `hasPart` edges exist, and
the **single** `hasPart` declaration with kinds (`dev:theme → dev:epic`) matches **none** of the 47 — because
those 47 are the operator part/whole shape (`edge_type = "hasPart" if op_type not in ("IMPL","NAND")`), not
the declared relation.

## 2. The rulings this implements

| # | ruling |
|---|---|
| **D1** | **Refuse** where the pair is declared **and both endpoint kinds are known**. **Never refuse** on an unknown / `unclassified` kind. **Severity declared by the pack; the policy applied by the graph.** |
| **D2** | A declared parent kind **accepts its declared subclasses** — closure over the declared kind, both spelling forms admitted, `equivalentTo` included. |
| **D3** | A declaration's binding scope is **the vocabulary it names**, not the predicate name. ⛔ **This DEPARTS from the ruling as recorded on the issue** ("the declaring pack's vocabulary") and is **reopened there, pending one line from the owner**: [#11656 comment 6754927533](https://github.com/daniel-ospina/tortoise/issues/11656#issuecomment-6754927533). Read it before implementing — the two readings differ in outcome (§5.2: the pack-scoped reading leaves **8 of the 35** declarations permanently `unbound`). |
| **D5** | The **prevention half lives in this issue**. |

### 2.1 D1's precedence, stated — the contradiction in revision 1

Revision 1 left `mismatch → refuse` (unconditional) beside "read the level through `resolve_enforcement`",
whose fallback is `warn`. **No shipped pack declares any per-relation level** — every
`extraction.enforcement.relations` is `{}` or absent, and three packs set `default: 'warn'`. So the two rules
mandated opposite outcomes for the same input. The precedence is:

```text
severity = enforcement.relations[predicate]        # an EXPLICIT pack declaration wins
        ?? BLOCK                                   # the kind-pair default — NOT the enforcement map's 'warn'
```

The enforcement map's `default: warn` was set for the **label-declaredness** question (#1934: an *undeclared*
label is a vocabulary gap, so warn). A **declared** pair contradicted by the write is a different question
with a different answer, so it gets its own default. Both are pinned (§6 pins 8, 9).

> **OVERRIDES:** #1934's warn-not-block default, which is what `resolve_enforcement` falls back to — a
> *declared* pair contradicted by the write blocks instead of warning, because #1934 answered the
> **undeclared-label** question (a vocabulary gap, where warn is right) and this default answers the
> declared-pair question; the later owner ruling D1 (`#11656`, 2026-11-09) rules refuse-where-declared.
> Recorded here so the departure reads as intentional at the point a lane implements it, and so the
> next reader holding #1934's closed ruling cannot mistake this default for an accident.

## 3. Alternatives considered and rejected

| alternative | why rejected |
|---|---|
| Gate `create_edge` only (the issue's own prescription) | covers 2 of 35 — 6 %. |
| Exclude `create_direct_edge` (revision 1) | **false**: it takes a `label`, and it is the dominant shape — 33 of the 35 declarations are direct-shape (§1.1). |
| Predicate-name-global constraint | refuses most of `addresses`' six pairs (and the 17/47 live edges). The modelling error RDFS names. |
| Refuse on unknown kind too | contradicts `EXTRACTOR-V4-ARCHITECTURE.md` §4.4 S2.3 (*"FAIL-OPEN, never raises … `unclassified` is honest"*) and the AX admission test. |
| Warn-only everywhere | the permanently-degraded signal (brief §9.2); graph holds `pt_656aa190…` *"partial enforcement reads as fixed"*. |
| New manifest key + new per-graph policy record | unnecessary — `extraction.enforcement.relations` + `resolve_enforcement` exist; only the kind-pair **default** is new (§2.1). |
| Reuse `_resolve_traversal_path` as the gate | it matches by **bare suffix, case-insensitive, first-match-wins** — ambiguous by construction, and it returns ONE relation. Fine for a query hint, wrong for enforcement. |

## 4. The rule (one function, six call sites — two of them folds)

```python
kind_pair_verdict(*, predicate, from_kind, to_kind, bound_ns) -> Verdict
```

| verdict | when | caller behaviour |
|---|---|---|
| `unbound` | no installed pack declares `predicate`, **or** the declaration does not bind (§5.2) | write; existing `undeclared_relation` warn path unchanged |
| `ok` | a declaration binds and both actual kinds resolve inside `expand_kind(declared)` | write |
| `unknown_kind` | an actual kind is missing or `unclassified` | **write**, reported as its **own** state, counted — never merged with a refusal |
| `mismatch` | a declaration binds and an actual kind falls outside the closure | **refuse** unless the pack declares a non-blocking level (§2.1), with the three AX parts: the predicate, the declared from/to kinds, the actual kinds, **and the declared predicates for the actual pair** |

⛔ **At a replay fold (§1.2 items 5 and 6) the `mismatch` arm does NOT raise, and does NOT skip.**
`rebuild_all` must never refuse a committed record — R1 and the brief's D4 — and it must not *omit* the write
either, because a skip is exactly the live≠replay divergence this work exists to prevent. The fold
**computes the same verdict, emits and counts it, and writes anyway**. Getting this wrong in either direction is
a shipped defect, so it is stated here rather than left to the implementation: "refuse" is a *live-write*
disposition, and the fold's disposition is "classify and report" (the same shape
`pack_state.graph_kind_namespaces` / #9068 uses for the same reason).

⚠️ **And the pins must be WRITE-aware, or they cannot see a skip.** A fold that computes `mismatch`, emits and
counts it, then `continue`s before its `MERGE` still "produces the same verdict as live" — so verdict equality
alone is green over exactly the defect this paragraph forbids. Pins 15 and 15b therefore assert **verdict
equality AND edge-presence equality** (the committed edge is still written); pin 18 cannot cover it either,
because a fold that classifies then skips still "routes through" the rule.

Endpoint kinds are normalised with **`status_folds.kind_forms_for` ∪ `PackRegistry.expand_kind`** — neither is
a superset (measured: `kind_forms_for('dev:issue')` has `issue` but not `pm:issue`; `expand_kind` has
`pm:issue` but not `issue`), both sides case-folded because kinds are stored **verbatim**
(`domain_validators.py:562`).

**Where each call site gets its kinds — no extra round trip at any of them:**

| site | source |
|---|---|
| `create_operator` | the existing per-label probe (`sdk.py:23460`) — add the kind property to its `RETURN`, the same zero-extra-round-trip shape `#2351` uses for `claimClass` |
| `create_direct_edge` | its own existing probe (`sdk.py:29142-29146`) returns `id, is_operator, status, claimClass` — add the kind property |
| `create_edge` / `create_about_edge` | `_resolve_entity` already returns `properties` (consumed for the `aboutDocument` refusal at `edges.py:2933`) |
| `_create_edges` (operator fold) | it already resolves the descriptor's endpoints |
| **`DirectEdgeCreated` fold** | its per-label endpoint probe (`projection/__init__.py:17090-17093`) returns **only `p.id`** — the label sweep supplies the node LABEL (`Point`/`Object`/`Subject`/`Event`), not the kind property. It must add the kind property to that `RETURN`, the same shape as the two rows above; without it the rule is called with no kind and the fold's gate reports `unknown_kind` for every record |

**`create_operator`'s synthetic node carries no kind** — the pair is `(source_id, each target_id)` from the
**arguments**; never read it off the stored typed edges.

## 5. Implementation

### 5.1 The installed-pack filter (net-new, small)

`list_relations()` is whole-catalog and already reached on every labelled `create_operator` — through
`pack_registry.declared_relation_predicates()` (#2766), which `create_operator` calls at
`sdk.py:23188` when it builds the declared set —
so the rows are free; the missing piece is `relations_for_namespaces(names) -> rows`, honouring
`ResolvedPackSet`'s **tri-state** (`None` ⇒ catalog union; `frozenset()` ⇒ deliberately empty; a set ⇒
recorded ∪ data). Reuse `pack_state.resolved_pack_set_for_graph` — it exists for bare-graph seams.

### 5.2 The binding test (D3) — 8 of 35 span universes, not 2

⚠️ **Revision 1's rule ("both kinds resolve into the *declaring pack's* universe") would have silently
disabled 8 of the 35 declarations** — never enforced, never warned about. They are:

```text
dev:implements               dev:issue              -> product-strategy:feature
dev:deploysTo                dev:code               -> core:deployment
portfolio:constrainedBy      venture:investment     -> portfolio:mandate
portfolio:marks              portfolio:valuationMark-> venture:investment
product-strategy:manifests   dev:incident           -> product-strategy:customerProblem
pm:assignedTo                pm:card                -> Subject          (a LAYER, §5.3)
venture:assignedTo           venture:actionItem     -> Subject          (a LAYER, §5.3)
venture:holds                venture:investment     -> Subject          (a LAYER, §5.3)
```

**Rule:** a declaration binds when each endpoint kind resolves into **the namespaces the declaration itself
names** — the declaring pack ∪ any namespace it names explicitly (`core:` counts) — not the declaring pack
alone. `portfolio:marks` is the load-bearing case: `domain_validators._portfolio_mark_contract`
(`:396-410`) reads it.

### 5.3 Layer-valued declarations (dependency on #11000)

`Subject` is a **layer** (`CORE_ENTITY_TYPES`), not a kind; the manifest validator already accepts it as a
core kind (`pack_registry.py:2041`), and `:2725` (`core_parents = CANONICAL_OBJECT_KINDS | CORE_ENTITY_TYPES`) already treats entity types as hierarchy parents — so the
reading is *"a Subject or any subtype"* (W3C SHACL UCR, verbatim).

⚠️ `expand_kind('Subject') == ['Subject']` today because ONTOLOGY §6's core hierarchy is not in the expansion
table. Until **#11000's §6 leg** lands, a declaration whose endpoint is a `CORE_ENTITY_TYPES` member is
**`unbound`**, pinned by a test naming the dependency. (#11000's lane has already landed that leg on its
branch — check before assuming it is still needed.)

### 5.4 Severity — reuse, and fix the seam's scope

Read `enforcement.relations[predicate]` through the existing seam (§2.1). ⚠️ **Fix in the same change:**
`resolve_enforcement` (`enforcement.py:56`) iterates `reg.packs.values()` — the **whole catalog** — and takes
the **max** severity, so one pack's `block` would block another pack's unrelated edge on the same predicate.
Resolve over the **installed** set. Both arms are affected, differently: the max-severity arm
(`enforcement.py:92-108`) scans `reg.packs.values()` for a **bare** kind or relation and has **no
production caller today**; the two live `kind=` callers (`projection/entities.py:341`,
`kind_classifier.py:364`) pass **namespaced** kinds and so resolve exclusively through
`reg.get_pack(ns)` (`enforcement.py:83-91`) — still against the **whole catalog**, so a pack this graph
does not install still decides the level. The label leg never reaches this seam at all:
`create_operator` does its own inline membership check (`enforcement.py:6`).

### 5.5 The prevention half (D5) — extend, and do not add a fourth resolver

⚠️ **Revision 1 said `fromKind`/`toKind` are "acted on in exactly one place". That is false** — there are
**three** production readers, and one of them is D5's own job:

- `domain_validators.py:396-410` — one pack's portfolio shape.
- `sdk._resolve_traversal_path` (`sdk.py:42773`; the live call is `sdk.py:38605`) — **kind pair → `{predicate, fromKind, toKind}`**,
  i.e. exactly `allowed_relations(from_kind, to_kind)`. ⛔ **Not reusable as the gate** (bare-suffix,
  first-match-wins, returns one relation) — but it must be **named**, or a lane will implement D5 twice.
- `value_extractor._relation_endpoint_refs` (`:44`) — the kind→kind adjacency, called twice inside
  `compile_value_brief` (`:395`, `:458`); its output rides `pack_refs` and is consumed by
  `extractor_v2._select_pack_kinds` (`extractor_v2.py:999`, called at `:1322`) to choose **which kinds to offer together**. **Wired**,
  for a narrower job than this issue needs.

**The delta:** use that same map to choose **which relation labels to offer for a pair**, derived from
`ontology.relations[]` (36 declared relations, **35 of them kind-bearing** — `pm:onBoard` is `declarationOnly`
and carries no kinds, so it maps no pair — which collapse to **34** distinct `(fromKind, toKind)` pairs:
`venture:asset→venture:program` is declared twice, by `protects` and `producedBy`) — **not** `extraction.relationTemplates[]` (declared but
unshipped; only `packs/_template` has it, commented out). Reuse `extractor_v2.build_master_list`, which
already carries the per-graph tenant-narrowed kind vocabulary.

**Guidance, not a closed list.** #7852's owner ruling makes the Layer-3 `label` **free**; hard-forbidding an
off-list label would contradict it. Off-list stays legal → the *undeclared* case → **warn** (as now).
Over-constraining at decode **costs recall** (brief §10.4), so it is per-pair and permissive where the pair is
undeclared.

## 6. Verification — CI pins (offline, deterministic, sub-second per #11454)

| # | pin | the failure it prevents |
|---|---|---|
| 1 | declared pair contradicted → `mismatch`, message names predicate + declared kinds + actual kinds + the permitted alternatives | a refusal the caller cannot act on (AX rule 2) |
| 2 | declared pair matched → `ok` | false refusal |
| 3 | **`unclassified` / missing kind → `unknown_kind`, never `mismatch`** | contradicting S2.3's fail-open decision |
| 4 | declared parent + declared **subclass** → `ok` (D2) | refusing truthful edges |
| 5 | bare `dev:issue` **and** `issue` spellings both → `ok` | the verbatim-spelling trap |
| 6 | `equivalentTo` sibling (`dev:issue` ↔ `pm:issue`) → `ok` | refusing truthful edges |
| 7 | a pack **not installed** on the graph cannot authorise its relations | a graph-wide catalog leak (D3) |
| 8 | **no** `enforcement.relations` entry → `mismatch` **BLOCKS** (the kind-pair default) | the D1 ruling being unreachable (the §2.1 defect) |
| 9 | an explicit `{predicate: warn}` → warns and writes; `{predicate: block}` → refuses | the override being unreachable |
| 10 | **each of the 8 cross-universe declarations binds** — `portfolio:marks`, `dev:implements`, … (§5.2) | 22 % of declarations silently disabled |
| 11 | `addresses` between two non-declared kind pairs → `unbound`, **not** `mismatch` | the predicate-name-global error (six pairs) |
| 12 | a layer-valued declaration (`toKind: Subject`) is `unbound` **and** the pin names #11000 | false refusals, and a silent un-pin when §6 lands |
| 13 | **mutation**: deleting the binding test reds ≥1 pin | the guard asserted by prose |
| 14 | **`create_direct_edge`** refuses a declared mismatched label — **both** the `link_entities` arm (including a **Point-bearing** pair: that route has no Point gate, §1.1) and the `ingest` arm | the P0 — an ungated third door |
| 15 | the **operator** replay fold (`_create_edges`) produces the same verdict as live **and still writes the committed edge** (verdict equality alone is satisfied by a fold that classifies then `continue`s before its `MERGE`) | `rebuild_all` divergence |
| 15b | the **`DirectEdgeCreated`** replay fold (`projection/__init__.py` pass-2b) produces the same verdict as live **and still writes the committed edge**, and it reads the predicate from `ev["label"]` when present, else `ev["edge_type"]` (§1.2 item 6) | the same divergence on the second fold; a verdict-only pin is green over a skip; and a gate keyed on `edge_type` alone sees only `IMPL`/`NAND` for `create_direct_edge` records and silently never enforces the dominant door |
| 16 | `resolve_enforcement` resolves over **installed** packs, not the catalog max | one pack blocking another's predicate |
| 17 | the prevention guidance lists the declared pairs for an endpoint pair and omits none | a silent gap between gate and guidance |
| 18 | **writer census** — a mechanical re-derivation of every parameterised-rel-type Cypher write site under `tortoise/` (regex over `MERGE`/`CREATE (…)-[:{rel}]->`, filtered to a non-literal rel type), asserting each routes through `kind_pair_verdict` or is named in a declared exemption list | **a new writer silently bypassing the gate** — the completeness claim that failed four review rounds on #11656 (§1.2). A hand-counted list cannot prevent this; a census can |

**Do not** assert on `create_edge` only — it covers 2 of 35 and leaves **33** untested. **Do not** gate one
replay fold: there are **two** (pins 15, 15b), and each covers a different door. **No** extractor call counts
as evidence for the prevention half: `extractor_v2.py` has **0** `create_edge` / **0** `create_operator` calls
today, so pin 17 is a **unit/contract** test. Pin 18 is what makes pins 1–17's *population* trustworthy: it
asserts the writer set rather than restating it.

**Tier E (#11454, never a merge gate):** on a real corpus, a declared relation reaches the graph only between
the declared kinds; a read-side census reports the pre-existing population.

## 7. Wiring

| touch point | type | covered by |
|---|---|---|
| `sdk.create_operator` / `sdk.create_direct_edge` | write path | §4 |
| `projection/edges.py` `create_edge` / `create_about_edge` / `_create_edges` | write path | §4 (pin 15) |
| `projection/__init__.py` `DirectEdgeCreated` fold (pass-2b, `:16955`/`:17109`) | write path | §4 (pin 15b) — the second replay fold |
| `pack_state.resolved_pack_set_for_graph` | DB read | reused — its `graph_kind_namespaces` leg is **O(nodes)**; **must not** run per write (derive from the `:PackInstall` leg, or cache keyed on the data leg) |
| `enforcement.resolve_enforcement` | decision seam | fixed in §5.4 |
| `pack_registry.expand_kind` / `status_folds.kind_forms_for` | vocabulary | reused, union |
| `extractor_v2.build_master_list` / `value_extractor._relation_endpoint_refs` | prompt guidance | §5.5 |
| `docs/EXPANSION_PACKS.md` §"Declaring a relation" | docs | state that a declaration now **binds**, and what `unbound`/`unknown_kind` mean |
| `docs/00_index.md` §5 | docs ledger | add this scope + the research brief |
| **#11000 §6 core hierarchy** | dependency | blocks only the layer case (§5.3) |

## 8. Risks

| # | risk | handling |
|---|---|---|
| R1 | a refusal on a **legitimate** legacy write | the rule is a write gate, never retro-applied; `rebuild_all` must not refuse a committed record |
| R2 | the O(nodes) installed-pack read per write | §7 — never per write |
| R3 | live/replay divergence | pins 15 **and 15b** — one per replay fold |
| R4 | the gate reads "done" while nothing emits business relations | pin 17 is a unit/contract test |
| R5 | a false refusal from the layer case before #11000 lands | §5.3 — `unbound` + pin 12 |
| R6 | blocking by default surprises an existing caller | it is the ruling; `enforcement.relations[predicate] = warn` is the declared escape, and pin 9 proves it |

## 9. Out of scope

- Retro-active repair of existing edges (R1).
- Any change to `docs/ONTOLOGY.md` (owner-gated by its own `CRITICAL RULE`).
- Choosing between the epic's route (A) operator-emission and (B) structural-emission for business relations
  — `docs/epics/2026-11-04-7949-extractor-v4-capstone/04-plan.md` §5.3a reserves that for the owner; this
  scope is route-independent.
- `#11484`'s I10 (`predicate -> {admitted_by, verb, record_carried}`) supplies the predicate→verb/namespace
  map **only**; it carries **no** `fromKind`/`toKind` field, so the pair map is derived **inside this issue**
  from `list_relations()`. Not a blocking dependency.

## 10. Errata carried from revision 1 (auditable)

| revision 1 claim | the truth | how it was found |
|---|---|---|
| "two writers: `create_operator` + `create_edge`" | **three** — `create_direct_edge` takes a `label` and validates nothing | fresh review (P0) |
| "`hasPart` declared by 4 packs with 4 different pairs" | `hasPart` as a predicate is declared **once**; the **five** rows that produced the miscount carried `semantics: hasPart` over **four** different predicates (`decomposesInto`, `contains` ×2, `partOf`, `partOfSprint`). Real evidence: `addresses` ×6, `contains` ×2, `targets` ×2 | fresh review (P1), confirmed by recount |
| "`fromKind`/`toKind` acted on in exactly one place" | **three** readers, incl. `_resolve_traversal_path` which is D5's own job | fresh review (P1) |
| "`_relation_endpoint_refs` has no production consumer" | it is **wired**, for kind co-selection | `[VGATE]` round 1 |
| "30 of 32 declare a mechanism" | **35 of 35** (34 IMPL + 1 NAND), incl. `hasPart`/`dependsOn` | `[VGATE]` round 1 |
| "`mismatch` → refuse, unconditionally" beside "read the level through `resolve_enforcement`" | contradictory — the map's default is `warn` and no pack declares a level. Fixed by §2.1 | fresh review (P1) |
| "both kinds in the declaring pack's vocabulary" | disables **8** of 35 cross-universe declarations. Fixed by §5.2 | fresh review (P1) |
| "leaves 31 untested" | `create_edge` covers 2 ⇒ leaves **33** | fresh review (P2) |
| "five call sites" / "four writers" (revision 3) | **six**, and **two** of them are replay folds — the operator fold *and* the `DirectEdgeCreated` fold (`projection/__init__.py` pass-2b), which carries declared predicates from **two** producers: rel type for the structural-edge lane, a `label` property for `create_direct_edge`. Fixed by §1.2 item 6, §4, pins 15/15b | fresh review (P1) — found by opening the second fold's own `#9553 class 4` note |
| the list of call sites presented as the specification | an **inventory**, not a completeness claim: four review rounds each asserted a writer set and each was wrong. Completeness is now **measured** (pin 18) | the scoping lane's handoff on #11656, confirmed by a fresh review |
