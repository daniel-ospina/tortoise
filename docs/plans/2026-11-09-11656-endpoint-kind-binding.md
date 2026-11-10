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
> kind-bearing declarations (`pm:card→pm:card`, `pm:sprint→pm:card`, `pm:card→Subject`, all `IMPL`) plus
> `pm:assignedTo`. The figures below were first measured at `e809803e` (32 kind-bearing, 31 `IMPL` + 1 `NAND`,
> 30 Object→Object, 7 disabled by §5.2) and are restated here at **this** head: **35 kind-bearing,
> 34 `IMPL` + 1 `NAND`, 32 Object→Object, 8 disabled by §5.2**. The argument does not move — every
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

**And the direct door is the *dominant* shape for Object↔Object, not a corner.** `_point_mediated_route`
(`sdk.py:25041`) reifies only when a `:Point` is involved — *"Object↔Object keeps the direct shape"* — and
**32 of the 35** kind-bearing declarations are Object→Object. So a declared IMPL label over a mismatched
Object pair is writable today with no gate. **⇒ `create_direct_edge` is IN scope, gating the `label` only**
(its epistemic semantics are untouched).

### 1.2 Five call sites, and one of them is the replay fold

1. `sdk.create_operator` — and `ingest`'s reified arm inherits it.
2. `sdk.create_direct_edge` — and `link_entities` + `ingest`'s plain-operator arm inherit it.
3. `projection/edges.py::_EdgeHandlers.create_edge`
4. `projection/edges.py::_EdgeHandlers.create_about_edge`
5. `projection/edges.py::_EdgeHandlers._create_edges` (**the replay fold**, `:1497`, from
   `projection/entities.py:3021`) — it MERGEs operator edges directly and never calls `create_edge`. A
   live-only gate **diverges on `rebuild_all`**: the `derived == replay(journal)` class. The code says the
   two write one shape (*"#7902 fold-parity"*).

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

## 3. Alternatives considered and rejected

| alternative | why rejected |
|---|---|
| Gate `create_edge` only (the issue's own prescription) | covers 2 of 32 — 6 %. |
| Exclude `create_direct_edge` (revision 1) | **false**: it takes a `label`, and it is the dominant shape for the 32 Object→Object declarations. |
| Predicate-name-global constraint | refuses most of `addresses`' six pairs (and the 17/47 live edges). The modelling error RDFS names. |
| Refuse on unknown kind too | contradicts `EXTRACTOR-V4-ARCHITECTURE.md` §4.4 S2.3 (*"FAIL-OPEN, never raises … `unclassified` is honest"*) and the AX admission test. |
| Warn-only everywhere | the permanently-degraded signal (brief §9.2); graph holds `pt_656aa190…` *"partial enforcement reads as fixed"*. |
| New manifest key + new per-graph policy record | unnecessary — `extraction.enforcement.relations` + `resolve_enforcement` exist; only the kind-pair **default** is new (§2.1). |
| Reuse `_resolve_traversal_path` as the gate | it matches by **bare suffix, case-insensitive, first-match-wins** — ambiguous by construction, and it returns ONE relation. Fine for a query hint, wrong for enforcement. |

## 4. The rule (one function, five call sites)

```python
kind_pair_verdict(*, predicate, from_kind, to_kind, bound_ns) -> Verdict
```

| verdict | when | caller behaviour |
|---|---|---|
| `unbound` | no installed pack declares `predicate`, **or** the declaration does not bind (§5.2) | write; existing `undeclared_relation` warn path unchanged |
| `ok` | a declaration binds and both actual kinds resolve inside `expand_kind(declared)` | write |
| `unknown_kind` | an actual kind is missing or `unclassified` | **write**, reported as its **own** state, counted — never merged with a refusal |
| `mismatch` | a declaration binds and an actual kind falls outside the closure | **refuse** unless the pack declares a non-blocking level (§2.1), with the three AX parts: the predicate, the declared from/to kinds, the actual kinds, **and the declared predicates for the actual pair** |

Endpoint kinds are normalised with **`status_folds.kind_forms_for` ∪ `PackRegistry.expand_kind`** — neither is
a superset (measured: `kind_forms_for('dev:issue')` has `issue` but not `pm:issue`; `expand_kind` has
`pm:issue` but not `issue`), both sides case-folded because kinds are stored **verbatim**
(`domain_validators.py:562`).

**Where each call site gets its kinds — no new query at any of them:**

| site | source |
|---|---|
| `create_operator` | the existing per-label probe (`sdk.py:23460`) — add the kind property to its `RETURN`, the same zero-extra-round-trip shape `#2351` uses for `claimClass` |
| `create_direct_edge` | its own existing probe (`sdk.py:28140+`) returns `id, is_operator, status, claimClass` — add the kind property |
| `create_edge` / `create_about_edge` | `_resolve_entity` already returns `properties` (consumed for the `aboutDocument` refusal at `edges.py:2933`) |
| `_create_edges` (fold) | it already resolves the descriptor's endpoints |

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
core kind (`pack_registry.py:2041`), and `:2294` already treats entity types as hierarchy parents — so the
reading is *"a Subject or any subtype"* (W3C SHACL UCR, verbatim).

⚠️ `expand_kind('Subject') == ['Subject']` today because ONTOLOGY §6's core hierarchy is not in the expansion
table. Until **#11000's §6 leg** lands, a declaration whose endpoint is a `CORE_ENTITY_TYPES` member is
**`unbound`**, pinned by a test naming the dependency. (#11000's lane has already landed that leg on its
branch — check before assuming it is still needed.)

### 5.4 Severity — reuse, and fix the seam's scope

Read `enforcement.relations[predicate]` through the existing seam (§2.1). ⚠️ **Fix in the same change:**
`resolve_enforcement` (`enforcement.py:56`) iterates `reg.packs.values()` — the **whole catalog** — and takes
the **max** severity, so one pack's `block` would block another pack's unrelated edge on the same predicate.
Resolve over the **installed** set. **This bug is live today** for the label-leg check.

### 5.5 The prevention half (D5) — extend, and do not add a fourth resolver

⚠️ **Revision 1 said `fromKind`/`toKind` are "acted on in exactly one place". That is false** — there are
**three** production readers, and one of them is D5's own job:

- `domain_validators.py:396-410` — one pack's portfolio shape.
- `sdk._resolve_traversal_path` (`:41450`, live at `:37465`) — **kind pair → `{predicate, fromKind, toKind}`**,
  i.e. exactly `allowed_relations(from_kind, to_kind)`. ⛔ **Not reusable as the gate** (bare-suffix,
  first-match-wins, returns one relation) — but it must be **named**, or a lane will implement D5 twice.
- `value_extractor._relation_endpoint_refs` (`:44`) — the kind→kind adjacency, called twice inside
  `compile_value_brief` (`:395`, `:458`); its output rides `pack_refs` and is consumed by
  `extractor_v2._select_pack_kinds` (`:1052`, `:1291`) to choose **which kinds to offer together**. **Wired**,
  for a narrower job than this issue needs.

**The delta:** use that same map to choose **which relation labels to offer for a pair**, derived from
`ontology.relations[]` (35 populated pairs) — **not** `extraction.relationTemplates[]` (declared but
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
| 14 | **`create_direct_edge`** refuses a declared mismatched label (both `link_entities` and `ingest` arms) | the P0 — an ungated third door |
| 15 | the **replay fold** produces the same verdict as live | `rebuild_all` divergence |
| 16 | `resolve_enforcement` resolves over **installed** packs, not the catalog max | one pack blocking another's predicate |
| 17 | the prevention guidance lists the declared pairs for an endpoint pair and omits none | a silent gap between gate and guidance |

**Do not** assert on `create_edge` only — it covers 2 of 35 and leaves **33** untested. **No** extractor call
counts as evidence for the prevention half: `extractor_v2.py` has **0** `create_edge` / **0**
`create_operator` calls today, so pin 17 is a **unit/contract** test.

**Tier E (#11454, never a merge gate):** on a real corpus, a declared relation reaches the graph only between
the declared kinds; a read-side census reports the pre-existing population.

## 7. Wiring

| touch point | type | covered by |
|---|---|---|
| `sdk.create_operator` / `sdk.create_direct_edge` | write path | §4 |
| `projection/edges.py` `create_edge` / `create_about_edge` / `_create_edges` | write path | §4 |
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
| R3 | live/replay divergence | pin 15 |
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
| "`hasPart` declared by 4 packs with 4 different pairs" | `hasPart` as a predicate is declared **once**; the four were `semantics: hasPart` on different predicates. Real evidence: `addresses` ×6, `contains` ×2, `targets` ×2 | fresh review (P1), confirmed by recount |
| "`fromKind`/`toKind` acted on in exactly one place" | **three** readers, incl. `_resolve_traversal_path` which is D5's own job | fresh review (P1) |
| "`_relation_endpoint_refs` has no production consumer" | it is **wired**, for kind co-selection | `[VGATE]` round 1 |
| "30 of 32 declare a mechanism" | **35 of 35** (34 IMPL + 1 NAND), incl. `hasPart`/`dependsOn` | `[VGATE]` round 1 |
| "`mismatch` → refuse, unconditionally" beside "read the level through `resolve_enforcement`" | contradictory — the map's default is `warn` and no pack declares a level. Fixed by §2.1 | fresh review (P1) |
| "both kinds in the declaring pack's vocabulary" | disables **8** of 35 cross-universe declarations. Fixed by §5.2 | fresh review (P1) |
| "leaves 31 untested" | `create_edge` covers 2 ⇒ leaves **33** | fresh review (P2) |
