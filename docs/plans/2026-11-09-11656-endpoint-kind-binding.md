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
depends_on: daniel-ospina/tortoise#11000, daniel-ospina/tortoise#11484
stage: task-scope
---

# #11656 — scope

**Input:** `docs/research/2026-11-09-11656-kindpair-enforcement.md` (the research brief, Parts I + II).
**Rulings:** owner, 2026-11-09 — D1, D2, D3, D5 recorded on #11656 (comment 6753057867).

---

## 1. Confirmed problem

A pack declares `relations[]: {predicate, mechanism, fromKind, toKind}`. **Nothing checks that a written
edge's endpoints have those kinds.** 36 declared relations across 7 packs; 32 carry `fromKind`+`toKind`;
`fromKind`/`toKind` are acted on in exactly one place (`domain_validators.py:396-410`, to derive one pack's
portfolio-shape rule).

**Two facts fix the shape of the fix** (both measured, brief §2):

- **`create_edge` is the wrong seam.** ⚠️ **All 32 kind-bearing declarations carry a core mechanism** —
  31 `IMPL` + 1 `NAND` — which `relation_write_paths()` (`pack_registry.py:534`) binds to the **operator**
  path. `hasPart` and `dependsOn` also declare `mechanism: IMPL`; they are the only two that **additionally**
  bind the structural path. A gate on `create_edge` alone therefore covers at most **2 of 32 — 6 %**; a gate
  on `create_operator` covers **32 of 32**.
- **A declaration cannot be keyed on the predicate name.** `dependsOn` is declared once (`dev:api →
  dev:database`) and has 17 live edges, **0 matching**; `hasPart` is declared 4 different ways and has 47
  edges, **0 matching**. Those are core structural predicates a pack *also declares*. Keying globally would
  refuse truthful edges — and the standards agree (multiple `rdfs:domain` **conjoin**;
  `schema:domainIncludes` exists because a globally-named property cannot carry per-vocabulary meaning).

## 2. The rulings this implements

| # | ruling |
|---|---|
| **D1** | **Refuse** where the pair is declared **and both endpoint kinds are known**. **Never refuse** on an unknown / `unclassified` kind. **Severity declared by the pack; the policy applied by the graph.** |
| **D2** | A declared parent kind **accepts its declared subclasses** — closure over the declared kind, both spelling forms admitted, `equivalentTo` included. |
| **D3** | A declaration's binding scope is **the declaring pack's vocabulary**, not the predicate name. |
| **D5** | The **prevention half lives in this issue** (not a peer). |

**AX basis** (`docs/ax/ax-principles.md`, #10183 — a recorded owner ruling): a refusal must carry three parts
— that it refused, exactly what failed, **exactly what to do** — and *"if the third part cannot be written,
the surface must not refuse."* That test is what makes "refuse" correct for a contradicted declaration (the
remedy is writable) and "never refuse" correct for an unclassifiable endpoint (the remedy is *"go classify a
node you did not create, in a different step"* — not performable by this caller, `#10158`'s shape).

## 3. Alternatives considered and rejected

| alternative | why rejected |
|---|---|
| Gate `create_edge` only (the issue's own prescription) | binds 2 of 32 declarations — 6 %. Would look complete and be false. |
| Predicate-name-global constraint | refuses 64 truthful edges (17 `dependsOn` + 47 `hasPart`); the modelling error RDFS names explicitly. |
| Refuse on unknown kind too | contradicts `EXTRACTOR-V4-ARCHITECTURE.md` §4.4 S2.3 — *"FAIL-OPEN, never raises … a guessed kind corrupts BOTH resolution and enforcement silently, while `unclassified` is honest"*; and the AX admission test forbids it. |
| Warn-only everywhere | produces the permanently-degraded signal §9.2 of the brief describes; graph already holds `pt_656aa190…` *"partial enforcement reads as fixed"*. |
| New manifest key + new per-graph policy record | unnecessary: `extraction.enforcement.relations` + `resolve_enforcement` already exist (see §5.4). |
| A second expansion/kind table | `expand_kind` is the one authority; a second table is the drift the packs exist to prevent. |

## 4. The rule (one function, every writer)

```
kind_pair_verdict(*, predicate, from_kind, to_kind, installed_ns) -> Verdict
```

`Verdict` is one of:

| verdict | when | caller behaviour |
|---|---|---|
| `unbound` | no installed pack declares `predicate`, **or** the declaration does not bind (endpoints outside the declaring pack's vocabulary) | write; existing `undeclared_relation` warn path unchanged |
| `ok` | a declaration binds and both actual kinds are in `expand_kind(declared_kind)` (D2 closure, both spellings, `equivalentTo`) | write |
| `unknown_kind` | an actual kind is missing or `unclassified` | **write**, and report it as its **own** state (counted, never merged with a refusal) |
| `mismatch` | a declaration binds and an actual kind is outside the closure | **refuse**, three parts: the predicate, the declared from/to kinds, the actual kinds, **and the declared predicates available for the actual pair** |

Endpoint kinds are normalised with **`status_folds.kind_forms_for` ∪ `PackRegistry.expand_kind`** — neither is
a superset (they disagree, measured: `kind_forms_for('dev:issue')` has `issue` but not `pm:issue`;
`expand_kind` has `pm:issue` but not `issue`), and both sides are case-folded because kinds are stored
**verbatim** (`domain_validators.py:562` documents the trap).

**Call sites — one rule, four writers:**

1. `sdk.create_operator` — primary (32/32). The declared pair is `(source_id, each target_id)`; the kinds come
   from the **existing** per-label probe at `sdk.py:22523` — add the kind property to that `RETURN`, the same
   zero-extra-round-trip shape `#2351` already uses for `claimClass`. The synthetic operator node has no kind;
   **never** try to read the pair off the stored typed edges.
2. `projection/edges.py::_EdgeHandlers.create_edge` — kinds already in hand: `_resolve_entity` returns
   `properties` (already consumed for the `aboutDocument` target refusal at `edges.py:2933`).
3. `projection/edges.py::_EdgeHandlers.create_about_edge` — same.
4. `projection/edges.py::_EdgeHandlers._create_edges` (**the replay fold**, `:1497`) — **must call the rule
   too**, or `rebuild_all` diverges from live. The code says the two write one shape (*"#7902 fold-parity"*);
   a live-only gate is the `derived == replay(journal)` failure class.

`ingest` is a **router** (`relation` → `create_edge`; `operator` → `create_operator` / `create_direct_edge`), so
it inherits all of the above. **`create_direct_edge` is out of scope** — it writes IMPL/NAND epistemic edges,
not declared relations.

## 5. Implementation

### 5.1 The installed-pack filter (net-new, small)
`list_relations()` is whole-catalog and already called on every labelled `create_operator`
(`sdk.py:22253`), so the rows are free; the missing piece is a filter:
`relations_for_namespaces(names) -> rows`, honouring `ResolvedPackSet`'s **tri-state**
(`None` ⇒ catalog union; `frozenset()` ⇒ deliberately empty; a set ⇒ recorded ∪ data). Reuse
`pack_state.resolved_pack_set_for_graph` — it exists for bare-graph seams, which `create_edge` is.

### 5.2 The binding test (D3)
A declaration binds when **both** actual kinds resolve into the **declaring pack's** kind universe. A write
between kinds of another vocabulary is `unbound` — which is the whole reason the 64 live `dependsOn`/`hasPart`
edges survive.

### 5.3 Layer-valued declarations (the defect found in the confirmation pass)
`packs/venture` declares `assignedTo … toKind: Subject` and `holds … toKind: Subject`. `Subject` is a
**layer** (`CORE_ENTITY_TYPES`), not a kind; the manifest validator already accepts it as a core kind
(`pack_registry.py:1669`), and `pack_registry.py:2294` already treats entity types as hierarchy parents, so
the reading is **"a Subject or any subtype"** — the W3C SHACL UCR rule verbatim (*"…or of one of their
subtypes"*).

⚠️ **This is the hard dependency on #11000.** `expand_kind('Subject') == ['Subject']` today because
ONTOLOGY §6's core hierarchy is not registered in the expansion table, so those two declarations would
**refuse truthful writes**. Until #11000's §6 leg lands: treat a declaration whose endpoint is a
`CORE_ENTITY_TYPES` member as **`unbound`** and pin it with a test naming the dependency — do **not** ship it
as `mismatch`.

### 5.4 Severity (D1 clause 3) — reuse, do not invent
`extraction.enforcement.relations: {predicate: warn|retry|block}` already exists and is validated
(`pack_registry.py:227`, `:1120` `enforcement_for_relation`, `enforcement.py:56` `resolve_enforcement`).
Read the level through that seam. `block` is already a legal declared value (#1934 marked it *reserved* only
because nothing implemented it).

⚠️ **Fix the seam's scope in the same change:** `resolve_enforcement` iterates `reg.packs.values()` — the
**whole catalog**, taking the **max** severity across every pack — so one pack's `block` would block another
pack's unrelated edge on the same predicate. Resolve over the **installed** set (same authority as §5.1).
This bug is live today for the label-leg check.

### 5.5 The prevention half (D5)
Offer the **declared predicates for the actual `(from_kind, to_kind)` pair** to the extraction model, derived
from `ontology.relations[]` (32 populated pairs) — **not** `extraction.relationTemplates[]` (declared but
unshipped; only `packs/_template` has it, commented out).

- Reuse `extractor_v2.build_master_list` (already carries the per-graph, tenant-narrowed kind vocabulary).
- **Guidance, not a closed list.** #7852's owner ruling makes the Layer-3 `label` **free**; hard-forbidding an
  off-list label would contradict it. Off-list stays legal → it is the *undeclared* case → **warn** (as now).
- **Extend a WIRED capability, do not activate a dead one.** `value_extractor._relation_endpoint_refs`
  already computes a kind→kind adjacency and is called twice inside `compile_value_brief`
  (`value_extractor.py:395`, `:458`); its output rides `pack_refs` into the brief and
  `extractor_v2._select_pack_kinds` (`:1052`, `:1292`) consumes it to choose **which kinds to offer
  together**. The delta is: use that same map to choose **which relation labels to offer for a pair**.
  Do not write a second adjacency.
- ⚠️ Counter-evidence to respect (brief §10.4): over-constraining at decode **costs recall**, so the guidance
  is per-pair and permissive where the pair is undeclared.

## 6. Verification

Per `#11454`'s tier rule — CI holds only offline, deterministic, sub-second tests.

**CI (offline, sub-second) — the pins:**

| # | pin | the failure it prevents |
|---|---|---|
| 1 | declared pair contradicted → `mismatch`, and the message names predicate + declared kinds + actual kinds + the permitted alternatives | a refusal the caller cannot act on (AX rule 2) |
| 2 | declared pair matched → `ok` | false refusal |
| 3 | **`unclassified` / missing kind → `unknown_kind`, never `mismatch`** | contradicting the S2.3 fail-open decision |
| 4 | declared parent kind + declared **subclass** → `ok` (D2) | refusing truthful edges |
| 5 | bare `dev:issue` **and** `issue` spellings both → `ok` | the verbatim-spelling trap |
| 6 | `equivalentTo` sibling (`dev:issue` ↔ `pm:issue`) → `ok` | refusing truthful edges |
| 7 | a pack **not installed** on the graph cannot authorise its relations | a graph-wide catalog leak (D3) |
| 8 | `{predicate: block}` in `extraction.enforcement.relations` → refusal; absent → the shipped default | the ruling being unreachable |
| 9 | `resolve_enforcement` resolves over **installed** packs, not the catalog max | one pack blocking another's predicate |
| 10 | the 64-edge case: `dependsOn` between two non-`dev` kinds → `unbound`, **not** `mismatch` | the predicate-name-global modelling error |
| 11 | **mutation**: deleting the binding test reds ≥1 pin | the guard being asserted by prose |
| 12 | a layer-valued declaration (`toKind: Subject`) is `unbound` (pinned to the #11000 dependency) | false refusals, and a silent un-pin when §6 lands |
| 13 | the **replay fold** calls the rule — a live/replay pair produces the same verdict | `rebuild_all` divergence |
| 14 | the prevention guidance lists the declared pairs for a given endpoint pair and omits none | a silent gap between gate and guidance |

**Tier E (#11454, never a merge gate):** on a real corpus, a declared relation reaches the graph only between
the declared kinds; and a read-side census reports the pre-existing population.

**Do not** assert on `create_edge` only — **32 of 32** declarations ride the operator path, so a
`create_edge`-only pin leaves **31** of them untested. **No** extractor call
counts as evidence for the prevention half: `extractor_v2.py` has **0** `create_edge` / **0**
`create_operator` calls today, so pin the enumeration as a **unit/contract** test, not an end-to-end count.

## 7. Wiring

| touch point | type | covered by |
|---|---|---|
| `sdk.create_operator` | write path | §4.1 |
| `projection/edges.py` `create_edge` / `create_about_edge` / `_create_edges` | write path | §4.2–4.4 |
| `pack_state.resolved_pack_set_for_graph` | DB read | reused (its `graph_kind_namespaces` leg is O(nodes) — **must not** run per write; cache keyed on the data leg or derive from `:PackInstall` alone) |
| `enforcement.resolve_enforcement` | decision seam | fixed in §5.4 |
| `pack_registry.expand_kind` / `status_folds.kind_forms_for` | vocabulary | reused, union |
| `extractor_v2.build_master_list` / `value_extractor._relation_endpoint_refs` | prompt guidance | §5.5 |
| `docs/EXPANSION_PACKS.md` §"Declaring a relation" | docs | state that a declaration now **binds**, and what `unbound`/`unknown_kind` mean |
| **#11000 §6 core hierarchy** | dependency | blocks only the layer-valued case (§5.3) |

## 8. Risks

| # | risk | handling |
|---|---|---|
| R1 | a refusal on a **legitimate** legacy write | grandfathered: the rule is a write gate, never retro-applied; `rebuild_all` must not refuse a committed record |
| R2 | the O(nodes) installed-pack read per write | §7 — never per write; key on the `:PackInstall` leg |
| R3 | live/replay divergence | pin 13 |
| R4 | the gate reads as "done" while nothing emits business relations | declared in §6; prevention half pinned as a unit test |
| R5 | a false refusal from the layer case before #11000 lands | §5.3 — `unbound` + a pin naming the dependency |

## 9. Out of scope

- `create_direct_edge` (IMPL/NAND epistemic edges — not declared relations).
- Retro-active repair of existing edges (§8 R1).
- Any change to `docs/ONTOLOGY.md` (owner-gated by its own `CRITICAL RULE`).
- Choosing between the epic's route (A) operator-emission and route (B) structural-emission for business
  relations — `04-plan.md` §5.3a reserves that for the owner; this scope is route-independent.
