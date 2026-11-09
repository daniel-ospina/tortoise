---
title: "#11656 — endpoint-kind binding for declared pack relations: research brief and decision request"
type: research
domain: platform
doc_status: live
created: 2026-11-09
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: expansion packs, ontology enforcement, write path, extractor v4
issue: daniel-ospina/tortoise#11656
epic: daniel-ospina/tortoise#7949
stage: scoping-research
---

# #11656 — a declared relation must only connect the kinds it declares

**Status: research complete, blocked on two owner decisions (§5). No implementation proposed yet.**

The issue's O/I/T is sound as a *goal statement*. Its **prescribed location is wrong**, and two of its
"open decisions" are not the only ones — the sharpest question is one the body does not ask. This brief
records what was measured, what the field does, what already exists in the repo, and the four decisions
that must be settled before a scope can converge.

---

## 1. What the issue gets right

- The enforcement chain is broken, and this is the third link. Confirmed at `origin/main`:
  `create_edge` validates the **predicate** (`projection/edges.py:2910-2911`) and never reads a kind.
- `fromKind`/`toKind` are acted on in **three** places, and none of them is a general write gate.
  ⚠️ **Correction (revision 2):** this said "exactly one place". The three are —
  `domain_validators.py:396-410` (one pack's portfolio-shape rule); `sdk._resolve_traversal_path`
  (`:41450`, live at `:37465`) which resolves a **kind pair → `{predicate, fromKind, toKind}`**, i.e. D5's own
  job (but by bare suffix, first-match-wins, so unusable as a gate); and
  `value_extractor._relation_endpoint_refs` (`:44`), the kind→kind adjacency wired to kind co-selection.
- The rule belongs on the write path, not in a per-pack validator: a customer's pack ships tomorrow and
  has no validator.

## 2. What the issue gets WRONG — and this changes the scope

### 2.1 The declared relations do not ride `create_edge`

Measured over all 7 shipped packs (`packs/*/manifest.yaml`): **36 declared relations, 32 carrying
`fromKind`+`toKind`.** ⚠️ **All 32 carry a CORE mechanism** — 31 `IMPL` plus 1 `NAND`
(`product-strategy:competesWith`) — and `relation_write_paths()` (`pack_registry.py:534`) binds the
**operator** path on `mechanism ∈ CORE_PREDICATES`. That includes `hasPart` and `dependsOn`, which also
declare `mechanism: IMPL`; they are the only two that **additionally** bind the structural path.

| write path | what it writes | declared relations that use it |
|---|---|---|
| **operator** — `create_operator(op_type, source, targets, label)` | `(source)-[:INPUT]->(op:Point)-[:IMPL\|:NAND]->(target)` | **32 of 32** |
| **structural** — `create_edge` / `create_about_edge` | a plain edge `(a)-[:pred]->(b)` | **2 of 32**, *in addition* — `hasPart`, `dependsOn` |

⇒ **A gate at `create_edge` alone binds at most 2 of 32 declared relations — 6 % — while `create_operator`
and `create_direct_edge` together reach all 32.** The issue's target ("zero declared relations writable on
mismatched endpoint kinds") would be satisfiable on paper and false in practice. The epic's
`docs/epics/2026-11-04-7949-extractor-v4-capstone/04-plan.md` §5.3a point 3 already states this routing; this
is its consequence for #11656.

### 2.2 On the operator path the declared pair is not on any edge

`create_operator` creates a **synthetic operator node**, and that node carries **no kind**. Measured on
the live graph (21,369 nodes): **5,472 operators, 5,452 of them kindless (99.6 %)**. The typed edges are
`(op)-[:IMPL]->(target)` and `(source)-[:INPUT]->(op)` — so the declared `(fromKind, toKind)` pair is
visible **only as call arguments**, never as an edge.

Consequences, both load-bearing:

1. `create_operator` is the correct seam, and it is also the *cheap* seam: kinds are already in hand there
   (§4.1). The two seams were written anticipating this change and name it — `sdk.py:22242` (*"the
   kind-pair leg is an epic-level gap"*) and `sdk.py:22268` (*"(3) endpoint kinds → `direction_basis
   'kind-pair'` — a SEPARATE scoped change, not implemented here"*).
2. **A post-hoc census over stored edges cannot see a violation at all** without reconstructing
   `(source, target)` from the `INPUT idx 0` + `IMPL` pair. This is why the sixth capstone row (#11083)
   measures *written*, not *correctly-kinded*.

### 2.3 `ingest` is a router — and that is why THREE primitives are needed, not two

`ingest` connections take `relation` → `create_edge`, `operator` + `reify` → `create_operator`, plain
`operator` → **`create_direct_edge`**. ⚠️ **Correction (revision 2):** this section used to conclude "two
primitives". It is **three**, because the third one takes a `label` — and a label *is* a declared
`relations[].predicate`. See §2.4. `ingest` is still a router (it adds no writer of its own), so gating the
three primitives covers it automatically; the point is that the third primitive is a real door.

### 2.4 The third door, and why revision 1 of the scope got it wrong (the P0)

`create_direct_edge(op_type, source_id, target_id, *, …, label=…)` (`sdk.py:28036`) accepts a **`label`** — and
a label *is* a declared `relations[].predicate`. It validates **nothing** about declarations or kinds: its
guards are `op_type ∈ {IMPL,NAND}`, endpoints differ, confidence range, direction, endpoint exists /
not-an-operator / not-terminal, and the claim-class override. `undeclared_relation` fires only in
`create_operator` (`sdk.py:22259`).

Two callers reach it with a declared label:

- `link_entities` (`sdk.py:17163-17171`): `if upper in ("IMPL","NAND") and operator is not True:` →
  `create_direct_edge(upper, from, to, …, label=label, …)`.
- `ingest`'s plain-operator arm (`sdk.py:27493`).

**And it is the dominant shape, not a corner.** `_point_mediated_route` (`sdk.py:24066`) reifies only when a
`:Point` is involved — *"Object↔Object keeps the direct shape"* — and **30 of the 32** kind-bearing
declarations are Object→Object. So a declared IMPL label over a mismatched Object pair is writable today with
no gate, and a scope that excludes this door leaves the issue's own target false. It is **in scope**, gating
the `label` only (its epistemic semantics are untouched).

## 3. Three measured facts that decide refuse-vs-warn

> ⛔ **CALIBRATION (owner, 2026-11-09) — read §8 first.** §3 records the state of DATA WRITTEN BY THE
> PROCESSES BEING REPLACED. It is legitimate evidence about *migration risk*; it is **not** evidence about
> the viability of the intended design, and no design conclusion in this brief rests on it any more. The
> viability question is answered in **§8** against `EXTRACTOR-V4-ARCHITECTURE.md` and `docs/ONTOLOGY.md`,
> and the refuse/warn decision is re-derived there in AX terms (**§9**). Section §3 is kept because the
> grandfathering decision (§5 D4) still has to answer for the data that exists.

### 3.1 Three quarters of the graph has no kind

| measurement | value |
|---|---|
| nodes | 21,369 |
| nodes with **no** kind property at all | **16,524 (77 %)** |
| operators with no kind | 5,452 of 5,472 (99.6 %) |

`create_operator`'s own mechanism-mismatch leg is warn-not-block partly because of this. A gate that
**refuses when the endpoint's kind is unknown** refuses the majority of the graph — it is not an edge
case, it is the normal case, and it is the direct consequence of link 1 (classification) only now being
turned on.

### 3.2 Existing legitimate writes would be refused — 64 of them, on the live graph

| declared predicate | declared pairs | live edges | edges matching a declaration |
|---|---|---|---|
| `dependsOn` | `dev:api → dev:database` (**1** declaration) | **17** | **0** — all between `evidence`, `option`, `question`, `plan`, `incident`, `risk` |
| `hasPart` | `dev:theme → dev:epic` (**1** declaration) | **47** | **0** — `evidence→operator`, `observation→operator`, `evidence→evidence` |

⚠️ **Correction (revision 2).** An earlier version of this table said `hasPart` was declared by *"4 packs,
4 different pairs"*. **It is declared once.** The four were `semantics: hasPart` on **different predicates**
(`decomposesInto`, `contains` ×2, `partOf`) — `semantics` was read as `predicate`. The 47 live `hasPart`
edges are the operator **part/whole shape** (`edge_type = "hasPart" if op_type not in ("IMPL","NAND")`),
which is why the single declaration matches none of them.

**The multiplicity case that actually carries this argument is `addresses` — declared 6 times, 6 distinct
pairs**, four of them inside `product-strategy` alone:

```text
addresses  dev:issue→dev:incident · dev:pullRequest→dev:issue
           product-strategy:feature→product-strategy:customerSegment
           product-strategy:useCase→product-strategy:feature
           product-strategy:requirement→product-strategy:architecture
           product-strategy:feature→product-strategy:customerProblem
contains   2 declarations, 2 pairs      targets  2 declarations, 2 pairs
```

`dependsOn` and `hasPart` are **core structural predicates** that a pack happens to *also* declare for one
specific pair; `addresses` shows the same word carrying six. Keying a constraint on the **predicate name**
therefore does not describe a rule — it describes one pack's use of a shared word, and enforcing it globally
would refuse edges that were never about that pack. This is the sharpest finding and it is **not in the issue
body**.

### 3.3 A gate at the two live primitives leaves the replay fold ungated

`create_operator` (live) and `_EdgeHandlers._create_edges` (`projection/edges.py:1497`, replay/fold,
reached from `projection/entities.py:2993`) are the two writers of the *same* operator-edge shape, and
the code says so (*"#7902 fold-parity: live create_operator writes the same typed + INPUT edges this
replay does"*). A live-only gate **diverges on `rebuild_all`** — the documented `derived == replay(journal)`
failure class. Beyond that, a two-door gate cannot reach six further writers: `_link_source`,
`hosted_api`'s raw `MERGE` sites (8 of them), `near_dup_merge`, `cache_projection`, `hosted_backup`.

**No extraction path emits either primitive today** (`extractor_v2.py`: **0** `create_edge`, **0**
`create_operator` calls). So the gate's live population today is agent/tool writes — SDK, MCP (3 call
sites), `decide_wiring`, `mining`, connectors — and the largest future producer does not exist yet.

## 4. What the field does (external research, 7 queries + W3C primary sources)

### 4.1 Refusal at write time is not the canonical mechanism — anywhere

| mechanism | semantics | blocks a write? |
|---|---|---|
| OWL / `rdfs:domain` / `rdfs:range` | **inference rules, not constraints** | **never** — asserting `Bob teaches Scooter` *entails* Bob is a Teacher, it does not reject an untyped Bob |
| SHACL `sh:class` | closed-world constraint → **report** | **no** — `sh:severity` *"has no impact on the validation"*; it is a label for the tool |
| Neo4j | uniqueness / existence / property-type / key | **no endpoint-kind constraint type exists at all** |
| Kafka Schema Registry | compatibility on **schema registration** | blocks the **schema**, never an individual record |

Sources: W3C SHACL (fetched, primary — §1.4 example, §"Severity", §`sh:class`/`sh:subclassOf`);
W3C RDF Schema 1.1; Protégé guide via SAREF portal #23 (*"a common misconception is that rdfs:domain and
rdfs:range act as a form of constraint… we generally advise against doing this"*); ACM *Lessons Learned
from the Combined Development of OWL and SHACL*; `neo4j.com/docs/cypher-manual/current/schema/constraints/`.

**Consequence:** a hard refusal would be stricter than every W3C mechanism and stricter than the leading
property-graph database. The standard place for the block/no-block decision is the *application* (SHACL
`sh:severity` exists for exactly that).

### 4.2 An untyped endpoint is a *violation*, not a pass — which makes §3.1 a decision, not a detail

W3C SHACL §1.4 is literally this case: `ex:Calvin ex:worksFor ex:UntypedCompany` against `sh:class
ex:Company` ⇒ `sh:Violation`. There is no canonical "unknown passes" option. So the three honest postures
are: target untyped nodes and flag them; don't target them and enforce nothing there; or infer the type
(OWL), which **masks** the gap — *"a pipeline that runs entailment before validation will happily report
clean data that is missing information."*

### 4.3 Subclass closure is part of the constraint's definition, and Tortoise's is one-directional

SHACL requires the validator to compute the `rdfs:subClassOf` **transitive closure itself**, no reasoner
required — `sh:class` *"reads `rdfs:subClassOf` straight from the data graph, so a constraint demanding
`ex:Person` accepts an `ex:Employee`"*.

Tortoise's `expand_kind` **only widens parent → children**, one level, and **not transitively**. Measured:

```text
dev:issue   -> ['dev:issue', 'pm:issue']         ← parent 'WorkItem' ABSENT (#11000)
WorkItem    -> ['WorkItem','dev:issue','dev:pullRequest','marketing:content',
                'product-strategy:feature','product-strategy:requirement',
                'pm:issue','pm:card','venture:actionItem']
dev:epic    -> ['dev:epic']                      ← 'Project' ABSENT
```

The bug is visible in the code — `pack_registry.py:2847` binds `parent` and never reads it (`# noqa: B007`)
— under a comment that claims the opposite. **Good news for this issue:** the check needs
`actual ∈ expand_kind(declared_parent)`, which works today. `#11000` is therefore **not** a blocker, provided
the direction is pinned.

### 4.4 A shared predicate carrying multiple declarations is a modelling defect, not a feature

`rdfs:domain`/`rdfs:range` are stated **on the property, globally**, and multiple values **conjoin**.
`schema:domainIncludes` / `rangeIncludes` were invented precisely to express union intent instead — and
constraining `addresses` by its **name** would force an endpoint to satisfy all six declared pairs
simultaneously (nothing passes). This is the standard-semantics confirmation of §3.2: **the declaration must
be scoped to the vocabulary it names, not to the predicate name.**

### 4.5 Grandfathering: the convergent practice is warn-first, with the violating set persisted

| tool | default | mechanism |
|---|---|---|
| dbt | `error` default, **`warn` available** | `warn_if`/`error_if` graduated thresholds; `store_failures: true` materialises failing rows |
| Kafka Schema Registry | `BACKWARD` | `NONE` mode is a documented *deliberate* bypass; the block is on schema registration |
| Neo4j | — | **refuses to CREATE the constraint** if existing data violates it |

Least-data-loss ranking from the sources: **warn-and-write + materialised violating set** → quarantine →
refuse (the only mode irreversible at the moment of the event). Documented practice on exactly this case:
*"`severity: warn` … useful when you know dirty data exists historically and you're cleaning it
incrementally"*; *"start new checks at warn until you've watched them for two weeks."*

**A structural argument makes grandfathering mandatory here, not optional:** SHACL is non-monotonic and the
declaration set is a moving target (packs toggle per graph; a node's kind can be attached later). The same
write is legal today and illegal tomorrow with **no change to the writer** — which is also why Neo4j would
refuse to install this constraint on our graph at all, given §3.2.

### 4.6 The one place the literature *does* block — and why it does not cover us

The strongest pro-gate source found (arXiv 2608.24921, PostgreSQL-native GraphRAG) gates on **well-formedness
of the record** (vague predicates, bare quantities, placeholder edges) — *"dropped, not guessed at"* — and
notably blocks only where the alternative is unrepairable (*"once written a placeholder edge is
indistinguishable from a genuine one"*). Semantic plausibility of a kind pair is the other class, and that
is where severity/warn is standard.

⚠️ **Not verified:** a published "ontology too strict → ingest refused → data loss" post-mortem. The
anti-strictness case in the literature is made on **soundness** and **maintenance-drift** grounds
(*"the graph quickly turns into 'sophisticated chaos'"*), not on a cited incident. Treat
"refusal causes loss" as reasoned, not sourced.

## 5. Decisions required (owner)

### D1 — On a kind mismatch in a **declared** relation: refuse or warn?

**Why it is a decision and not an engineering call.** #1934 (closed, landed) states its indicator 2 as
*"`create_operator` with an **undeclared** relation/kind-pair returns a structured warning and the write
proceeds (warn-not-block)"*. #9963's own test file goes further: *"#1934's ruling (governance D1/D2) is a
recorded decision and this file pins it … **A refusal here is a regression, not a fix.**"*

**The distinction that makes D1 live rather than settled:** #1934 covers a pair the vocabulary says nothing
about. A **declared** relation whose endpoints contradict its own declaration is the opposite case — the
vocabulary says exactly this, and the write contradicts it. One reading is that refusing the second is a
*narrowing* of #1934, not a reversal; the other is that #1934's warn covers this class too, since the
kind-pair leg was never built and the epic records it as warn-not-block.

| option | what it means | evidence for | evidence against |
|---|---|---|---|
| **A. Warn-not-block** (status quo contract) | structured `warnings[]`, write proceeds, violation event emitted | consistent with #1934/#9963 and with the entire field (§4.1); nothing is lost and promotion to error stays a config change | the graph stays wrong; §3.1 means most writes warn, so the signal may be ignored |
| **B. Refuse for a declared relation, warn for an undeclared pair** (the issue's recommendation) | `ValueError` on the operator path / a raise on the structural one, naming predicate + declared kinds + actual kinds | a false structural fact is not a weak fact; it is a lie EP will then propagate | reverses behaviour #1934/#9963 pin; no W3C/Neo4j precedent; §3.2 says it would refuse 64 existing truthful edges unless scoped; §3.1 means it refuses most untyped writes |
| **C. Refuse only where the declaration *binds* — both endpoint kinds resolve into the declaring pack's own vocabulary — and warn everywhere else** | a narrow, evidence-backed refusal; the §3.2 edges fall outside the binding and warn | gets B's protection where the ontology genuinely speaks, without refusing shared-predicate writes; matches §4.4 (scope to the vocabulary, not the name) | more machinery: needs the "does this kind belong to this pack" test, and a stated answer for §3.1 |

**Recommendation: C**, with A as the fallback for everything the declaration does not bind. Rationale: it is
the only option that survives all three measured facts at once — §3.1 (77 % untyped), §3.2 (64 truthful
edges under shared predicates), §4.1 (refusal is an application policy, so make its scope precise rather
than broad). **If the owner prefers B, it must be recorded as a reopen of #1934 in that issue's own home**,
with the §3.1/§3.2 measurements in front of them — per the contradiction-test rule, evidence and argument,
not a quiet adoption.

**Second-order, and part of D1:** what happens when the endpoint's kind is **unknown** (§3.1). Options:
skip silently (fail-open, invisible); skip but count, so the typing debt is visible; warn. Refusing is not
on the table unless the owner wants the graph to reject 77 % of writes.

### D2 — Does a declared parent kind accept a declared subclass?

ONTOLOGY §6 + `#11000`. The direction matters and only one works today:

- `actual ∈ expand_kind(declared_parent)` → **works** (`WorkItem` finds `dev:issue`).
- `declared_parent ∈ expand_kind(actual)` → **does not work** (`dev:issue` does not find `WorkItem`,
  `#11000`), and is not transitive.

**Recommendation: yes — a declared parent kind accepts a declared subclass, computed as
`expand_kind(declared)` expanded transitively (closure), per §4.3.** Two riders that must be decided with
it, because getting either wrong refuses truthful edges:

1. **Spelling.** Stored kinds are **verbatim**, and both `dev:issue` and bare `issue` are legal spellings
   (`domain_validators.py:562` already documents the trap). The check must case-fold both sides and admit
   both forms, or it refuses truthful writes.
2. **`equivalentTo`.** `expand_kind` already widens equivalences (`dev:issue → pm:issue`) — should a
   declared `dev:issue` accept `pm:issue`? Recommendation: **yes**, it is already in the closure and there is
   no reason to carve it out.

### D3 — What is the binding scope of a declaration? (the question the issue does not ask)

§3.2 and §4.4 both say a declaration cannot be keyed on the predicate name globally. Options were folded
into D1 option C; recorded separately because it is a **scope-defining** decision, not just a
refuse/warn modifier:

- **A. Predicate-keyed globally** — refuses the live `dependsOn`/`hasPart` edges and most of `addresses`'
six pairs; needs a data migration.
- **B. Scoped to the vocabulary the declaration names** — binds when each endpoint kind resolves into the
  namespaces the declaration itself names (the declaring pack ∪ any it names explicitly).
- **C. Predicate-keyed with core predicates exempt** — a core predicate is never constrained by a pack's
  declaration of it.

**Recommendation: B**, with C as an additional guard if B leaves a gap for a core predicate a pack
re-declares over its own kinds.

### D4 — Existing violating edges: grandfather, flag, or refuse retroactively?

Recommendation: **leave them; never retro-refuse.** Report them from a read-side census (Tier E), and never
make `rebuild_all` refuse a committed record. §4.5 is the field's answer and §3.2 is why it is the only
safe one here. No decision needed unless the owner wants a migration.

## 6. Scope corrections this research establishes (no owner decision needed)

| issue says | measured | effect |
|---|---|---|
| gate `create_edge` / `create_direct_edge` / `ingest` | ⚠️ **Corrected (revision 2): `create_direct_edge` is IN scope.** It takes a `label` (`sdk.py:28036`), `link_entities` passes one (`:17163-17169`) and `ingest` reaches it too (`:27493`), it validates nothing about declarations, and `_point_mediated_route` keeps Object↔Object on the direct shape — 30 of 32 declarations. See §2.4 | gate **`create_operator`** + **`create_direct_edge`** (both label doors) + `create_edge`/`create_about_edge` (**2/32**, which also bind the operator path) + the replay fold; `ingest` inherits all of them |
| "one write gate, not two" | live and replay are **two writers of one shape** | the rule is one *function*; the fold (`_create_edges`) must call it too, or `rebuild_all` diverges |
| reads `pack_state.graph_installed_namespaces` | that read is **O(nodes)** (8 whole-graph property scans; ~6 ms at 300 nodes, linear) and deliberately uncached | must not run per write. `resolved_pack_set_for_graph` exists for bare-graph seams; a cache must be data-leg-keyed or it reintroduces #5163 |
| depends on `#11484` (corrected) | ⚠️ **I10 does not carry the pair map.** `docs/epics/2026-11-04-7949-extractor-v4-capstone/04-plan.md:628` defines `allowed_predicates(graph)` as `predicate -> {admitted_by, verb, record_carried}` with `verb ∈ {create_edge, create_about_edge, ingest}` — **no `fromKind`/`toKind` field**, and the verb enum excludes `create_operator`, the door 32/32 ride. I10 supplies the predicate→verb/namespace map **only.** | #11656 derives the pair map itself from `list_relations()` (32 populated pairs). **Not a blocking dependency.** If the owner wants I10 to own the pairs, that is an I10 amendment — do not assume it. |
| `domain_validators` as a component | it is the **pattern to follow** (call-time read, fail-open-but-named, manifest-pinned test), for a *read-side* grader; a write gate must not fail open to a stale constant | keep both: the validator catches pre-existing damage, the gate prevents new |

**Existing capability to reuse rather than re-implement** (duplication review, file:line evidence in the
review): `graph_census.kind_prop_for` (label → kind property), `StatusFoldIndex.kind_forms_for` +
`expand_kind` (**take the union — neither is a superset**; they disagree, measured),
`resolved_pack_set_for_graph` (tri-state `None`/empty/set), `enforcement.emit_violation` + the
`{code, message, **extras}` warning shape, `list_relations()` (already called on every labelled
`create_operator` at zero marginal cost). **Net-new:** only a graph-scoped filter over those relations.

## 7. What "done" looks like once D1–D3 are settled (CI shape — still valid)

- **CI (offline, sub-second, per #11454's tier rule):** mismatched declared pair → the decided outcome, with
  a reason naming predicate + declared kinds + actual kinds; matched pair → accepted; unknown kind → the
  decided behaviour; subclass accepted; equivalent kind accepted; bare vs namespaced spelling accepted;
  a pack not installed cannot authorise its own relations; **deleting the check reds a test**.
- **CI, dogfooded on the repo's own declarations:** the 7 shipped packs' 32 kind-bearing relations, so a
  manifest edit that makes a declaration unsatisfiable reds rather than rots.
- **Tier E (#11454):** on a real corpus, a declared relation reaches the graph only between the declared
  kinds — and the read-side census reports the pre-existing population.

**Known false-green to avoid:** asserting on `create_edge` only. **32 of 32** declarations ride the operator
path, so a `create_edge`-only pin leaves **30** of them untested.

---

## PART II — viability of the INTENDED design, and the AX framing

**Added 2026-11-09 after an owner calibration:** the 77 %-unkinded figure is **legacy data from the
processes being replaced**, so it cannot carry a design conclusion. What follows re-derives the decision
from (a) the intended extractor architecture, (b) `docs/ONTOLOGY.md`, (c) the repo's own recorded AX
ruling (`docs/ax/ax-principles.md`, #10183), and (d) SOTA for type enforcement.

### 8. Does the intended design even have the kinds at write time?

**Yes — and it also has a legitimate "no kind" state, by decision.** Both halves matter, and both are
recorded, so neither is an inference from legacy data.

#### 8.1 The design puts the whole write in ONE mechanical step, and the model never writes

`EXTRACTOR-V4-ARCHITECTURE.md` §S5/§S6:

> *"Both only ever **decide** — nothing is written here. The writing is S6."*
> *"Division of labour: the decision model decides, code writes. Asking a model to emit a graph write
> directly is how you get an untyped or half-formed edge. The model answers a narrow question; code turns
> the answer into a write."*

S6 COMMIT is **mechanical, fail-closed, atomic**. So the intended pipeline has a place where **code holds
both endpoints and their kinds at the moment of the write** — which is exactly the seam a kind-pair check
needs, and it is the seam that does not exist in the `create_edge`/`create_operator` pair the issue names.

#### 8.2 "No kind" is a DESIGNED state, not only a legacy one

§4.4's failure policy, S2.3 CLASSIFY (verbatim):

> *"**FAIL-OPEN, never raises** — the candidate keeps `unclassified`; the run records it in its census.
> A wrong kind is worse than no kind: `_find_existing_entity` uses `kind` as its exact-match
> discriminator, and `_select_pack_kinds` plus the chain enforcer key on kinds — so a guessed kind
> corrupts BOTH resolution and enforcement silently, **while `unclassified` is honest**."*

⇒ In the intended design a committed node may carry `unclassified` **on purpose**, and that is the
correct state, not a defect. The design has already answered "what do we do when we don't know the kind":
*say so, record it, and keep going.* **A write gate that refuses on an unknown kind contradicts a
recorded architectural decision.** This is the design-grounded form of §3.1 — and it is much stronger,
because it does not depend on how much legacy data exists.

#### 8.3 The design already has the right third disposition — `HOLD`

§4.4 defines `HOLD` explicitly and distinguishes it from a verdict:

> *"`HOLD` is a run-level disposition, not a new VET outcome… a `HOLD` is **not a judgment about
> validity** — it is **the absence of one**."*

That is precisely the disposition a kind mismatch needs when the system cannot adjudicate it: *not*
"write it anyway and warn" (a judgment that it is acceptable) and *not* "refuse" (a judgment that it is
wrong), but **the absence of a judgment**, carried forward. **The intended design therefore has three
dispositions where this issue assumed two.**

#### 8.4 Kind-pairing already exists in the pipeline — wired to a narrower job than this issue needs

`tortoise/value_extractor.py::_relation_endpoint_refs` builds a **kind → kinds** adjacency from both
declaration sites (`ontology.relations[].fromKind/toKind` and `extraction.relationTemplates[]`), i.e.
exactly the "which relations may exist between which kinds" map. Its own docstring records a measured
finding that is *the* argument for per-pair scoping:

> *"A relation is emittable only when BOTH endpoints are offered … which is exactly what co-selecting one
> foreign kind produces: a `dev` story gained `product-strategy:feature` but no other
> `product-strategy` kind, so five of that namespace's own declared edges became half-offered
> (measured: 2 → 6, i.e. the fix made the metric it exists for WORSE for `dev`)."*

⚠️ **It is WIRED — for a narrower job than this issue needs, and the difference is the whole point.** It is
called twice inside `compile_value_brief` (`value_extractor.py:395`, `:458`), its output rides `pack_refs`
into the brief, and `extractor_v2._select_pack_kinds` (`:1052`, `:1292`) consumes it to decide **which KINDS
to offer together** (one side of a declared pair pulls the other). What it does **not** do is constrain
**which RELATION LABEL** is offered for a given endpoint pair. So the prevention half (D5) is an
**extension of a wired capability**, not the activation of a dead one — materially cheaper, and the
difference matters for how §5.5 of the scope is written.

*(An earlier draft of this brief said "dead in production". That was wrong: it came from a `grep … | head -5`
that stopped before the two production call sites, i.e. a truncated completeness check.)*

**Viability verdict: the intended design is not merely compatible with endpoint-kind binding — it is
already shaped for it.** The gap is that the check was never wired into S6's mechanical write, and the
pairing closure — wired for **kind co-selection** (§8.4) — is not consulted for **relation-label** binding
at write time.

### 9. The AX framing — machines are the users here

`docs/ax/ax-principles.md` is a **recorded owner ruling (2026-10-27, #10183)**. It is a ruling about
*surfaces a caller can be wrong on*, and a write gate is exactly that. Two rules and one admission test:

- **Rule 1** — a legible intent in an illegible form is **self-healed, not rejected** (re-spelling is
  silent; supplying a meaning is echoed in the reply).
- **Rule 2** — an illegible intent **is** refused, with **three parts**: that it refused, exactly what
  failed, exactly what to do to fix it.
- **The admission test** — *"if the third part cannot be written, the surface must not refuse."* A refusal
  whose remedy is not performable is *the system failing the caller*.

#### 9.1 What the test yields for a kind mismatch (the decision, re-derived)

| the case | is the intent legible? | is the third part writable, **by this caller, about this write**? | outcome the AX ruling requires |
|---|---|---|---|
| **kinds known, declared pair matched** | yes | — nothing failed | write |
| **kinds known, declared pair mismatched** | **yes** — we know exactly what it meant and why it is wrong | **yes**: *"`funds` is declared `investor → investment`; you wrote from `core:tool` to `core:person`; use one of the predicates allowed for that pair, or the node of the declared kind"* | **refuse, three parts** — rule 2 |
| **endpoint kind `unclassified`** (designed state, §8.2) | **no — we cannot establish what the node IS** | **no**: the repair is *"go classify a node you did not create, in a different step"* — not performable here. This is the #10158 shape verbatim (*"the prescribed repair is not performable for this record"*) | **must not refuse** — accept, record, report as a distinct state |
| **endpoint kind missing (legacy)** | no | no | as above, and the census names it |

**This resolves D1 without a global binary, and it resolves it on recorded decisions rather than on
measurements of legacy data:**

- the issue's recommendation (**refuse**) is **correct for the case it actually describes** — a declared
  relation written between the wrong kinds — and it is *AX-permissible* because the remedy is writable;
- warn-not-block survives **exactly where it should** — the unclassifiable case, where refusing would
  demand a repair the caller cannot perform;
- and the two are not in tension, because they are different cases rather than two policies for one case.

#### 9.2 Why "warn" is not the automatically-safe option (the trap the issue did not name)

The AX ruling's converse bites: *"the reported state must not flatten different failures into one label."*
A warning stream that mixes "you contradicted your own declaration" with "the classifier declined to
decide" collapses a **caller error** and a **system limitation** into one signal — the caller cannot tell
which one it must act on.

The operational literature is blunt about where that ends (see §10.6): a check that fires on the
population it was not designed for *"fails on every run, and the team learns to ignore it"*. The graph
already holds this conclusion independently: **`pt_656aa190…` — *"Partial enforcement reads as fixed
while leaving the invariant violable."*** So a warn-only implementation is not the conservative choice; it
is the choice that produces a permanently-degraded signal.

### 10. SOTA for type enforcement (external research)

Search tool: `perplexity_research` (rung 1) — **20 queries attempted / 20 returned**, plus `web_fetch` of
W3C SHACL for primary definitions. Graph: **12 hits over 4 queries**; built on `pt_656aa190…` and
`pt_727969bd…` (the latter: *"the closure must be over kinds, iterated to a fixpoint"*).

#### 10.1 The mechanism that dissolves refuse-vs-warn: severity declared, policy applied

**SHACL 1.2 Core (W3C, primary)** — a focus node conforms *iff* its results contain **no result whose
severity is in the set of disallowed levels**. So:

- `sh:severity` on the constraint is **the author's declaration** (`sh:Violation` / `sh:Warning` / `sh:Info`);
- the **set of disallowed levels is the deployment's policy**;
- the block/no-block decision is therefore **data, not code**, and can differ per graph.

**This is the single most reusable finding.** It maps 1:1 onto our model: the **pack declares the
severity per relation**; the **graph's policy set** decides what blocks. It removes the need for a global
answer to D1, and it makes the transition (warn today, refuse tomorrow) a policy edit rather than a code
change.

#### 10.2 A real KG store *does* refuse at write — and cannot retrofit

**GraphDB (Ontotext)**: `ShaclSail` *"validates the data changes on `commit()`"* and **throws** with a
validation report. So refusal-at-write is a shipped behaviour in the leading triplestore — the careful
version of §4.1's "nobody refuses" is *"the constraint language reports; a store may be configured to
reject"*.

⚠️ **And the grandfathering cost is explicit**: *"A repository with SHACL validation must be created from
scratch… **You cannot modify an already existing repository by enabling the validation afterwards.**"*
The one comparable that blocks at write **cannot be switched on over existing data**. That is the
strongest available evidence for §5 D4 (never retro-refuse; the enforceable population is new writes).

#### 10.3 Attribution/blame is the signal that makes a refusal useful

Gradual-typing research on blame (Wadler & Findler's blame calculus; the *Blame-Subtyping Theorem*:
*"statically typed regions are never to blame"*; Lazarek et al.'s rational-programmer evaluation finding
blame *"provides significantly superior debugging hints"* when combined with checks). The lesson that
transfers: **a type failure is only actionable when it names the component that is at fault**, and a
well-typed component must never be blamed for an untyped one's failure.

Applied here, this is the same conclusion as §9.1, reached independently: an unclassifiable node makes
**the writer** "not to blame", so blaming the write is a false accusation — the mechanical definition of a
refusal the caller cannot act on.

#### 10.4 Prevention beats detection — and the SOTA does BOTH, not one

| mechanism | schema-valid output | cost |
|---|---|---|
| prompt only | 85–95 % | — |
| post-validate + repair | 95–98 % | a retry per failure |
| **constrained decoding** | **99.9 %+** | 5–10 % per-token latency |

Constrained decoding masks invalid tokens so *"the generated output cannot violate the schema because
invalid continuations are not sampleable"*. Widely reported: tool-call schema accuracy **92 % → 100 %**
(Ubicloud/Kimi K2.6), with the compounding warning that *"a five-call plan has roughly a one-in-three
chance that at least one call is malformed."*

⛔ **But the same sources are explicit about the boundary:** *"Constrained decoding guarantees syntax, not
semantics… A perfectly-formed call to the wrong function is still wrong."* A kind-pair violation is a
**semantic** violation, so decoding alone does not fix it.

**The pattern that does** — and it exists in the KG-extraction literature, not just the LLM-API world.
The LREC 2026 schema-constrained framework:

> *"Relation predicates can optionally be enforced at decode time by **dynamically constructing a Pydantic
> enumeration from the ontology labels**; when enabled, the structured generation backend restricts the
> token space so that only schema-compliant predicates can be produced. When this constraint is disabled,
> model outputs are parsed into typed response models and **revalidated against the ontology post-hoc**,
> with out-of-schema predictions explicitly flagged."*

⇒ **The catalogue turns a semantic check into a structural one**: if the *allowed predicates for this
`(from_kind, to_kind)` pair* are enumerated into the model's output schema, the invalid pair becomes
unsampleable and the write gate becomes a **backstop** rather than the primary mechanism. This is the
single largest error-rate and cost lever available, and it is exactly what #11484's per-graph derived
vocabulary + I10 make possible.

⚠️ **Counter-evidence, and it must be respected:** strict schema conformance at decode **costs recall** —
the AEVS paper attributes a competitor's weaker result to constrained generation that *"enforces strict
schema conformance but struggles to handle literals… that do not conform to predefined entity types"*.
Over-constraining the decoder trades extraction coverage for conformance. So the enumeration must be
**per pair, and permissive where the pair is undeclared** — never a closed global list.

**And the priority is right:** in that paper *"Unsupported Relation"* — both endpoints grounded, relation
wrong — *"dominated across all configurations, accounting for 97–100 % of detected errors."* Relation-level
correctness is the dominant error class in KG extraction, which is the justification for doing this work
at all.

#### 10.5 Retry economics — why the refusal's wording is a cost control

- *"a retry that includes the exact validation error converges; a retry that repeats the prompt just
  re-rolls the dice."*
- *"Cap attempts at 2–3: each retry costs a full round trip and re-sends the growing conversation, so an
  uncapped loop turns one bad input into a bill spike."*
- prompt-based formatting failures *"require retry logic that **doubles inference cost** for the affected
  requests."*

⇒ **AX rule 2's third part is not politeness; it is the difference between one retry and a loop.** A
refusal naming the declared pair and the actual kinds converges in one round trip. A refusal saying
"invalid relation" burns the budget.

**Machine-readable error shape (SOTA):** RFC 9457 `application/problem+json`, and specifically the
*agent-facing* extension set — `retryable`, `error_category`, content negotiation, token efficiency. That
is the shape the warning/refusal payload should take: the caller must be able to branch on **retryable**
without parsing prose.

#### 10.6 Grandfathering and rollout — shadow → warn → block

| practice | source |
|---|---|
| new checks start at `warn`, promote on evidence; known-dirty history is explicitly a warn case | dbt `severity` + `warn_if`/`error_if` + `store_failures` |
| block lives on **schema registration**, with `NONE` as a documented deliberate bypass | Kafka Schema Registry |
| run the rule in **shadow**, log fires, calibrate, *then* route to pager | alerting rollout guidance (2 independent sources) |
| **separate an Issue (a detected violation) from an Incident (a managed remediation)**; group recurring violations so the first detection opens an incident and later ones only update it | DQOps |
| a check calibrated on an assumption fails on legitimate legacy data → *"the team learns to ignore it"* → *"one false alarm undermines the credibility of the rest"* | DQOps |

⇒ The transition path that satisfies both the AX ruling and the evidence: **shadow-count → warn (grouped,
not per-write) → refuse only where §9.1 says the remedy is writable.** And the warn phase must report
**counts per class**, never one undifferentiated stream (§9.2).

### 11. Revised recommendation (supersedes §5 D1's option table)

**D1 — on a kind mismatch, refuse or warn? → Neither, globally. The disposition is a function of
legibility, per the AX admission test:**

1. **kinds known + declared pair contradicted → REFUSE**, three parts, naming predicate + declared kinds +
   actual kinds + the allowed alternatives for the actual pair. *(AX rule 2; the remedy is writable.)*
2. **endpoint `unclassified` / missing → do NOT refuse.** Record, report as its own state, and carry the
   write (or `HOLD` where the design already allows it). *(AX admission test + §8.2's recorded decision.)*
3. **the severity of (1) is declared per relation by the pack and applied by the graph's policy**, so the
   warn→refuse transition is a policy change, not a code change. *(§10.1 — the SHACL pattern.)*

This is **not** a reversal of #1934: #1934's warn contract covers the *undeclared* pair, and §9.1 keeps
warn exactly for the case where no remedy is performable. It should still be **stated on #1934** so the
distinction is recorded where the next lane reads it, with a line naming the default it departs from
(refuse-everything).

**D2 — subclass acceptance: unchanged from §5** (closure over `expand_kind(declared)`, both spellings
folded, `equivalentTo` included). The graph already holds the confirming constraint: *"the closure must be
over kinds, iterated to a fixpoint"* (`pt_727969bd…`).

**D3 — binding scope: unchanged from §5** (the declaring pack's vocabulary), now with a second,
independent confirmation: `_relation_endpoint_refs` is already **per-pair**, and its docstring records
that namespace-granular scoping was *measured* to make the metric worse.

**New — D5 (scope addition):** #11656 as written is the **detection** half only.
The **prevention** half is `allowed_relations(from_kind, to_kind)` enumerated into the extractor's output
schema — which is where the SOTA gets 99.9 % vs 95–98 %. The repo's `_relation_endpoint_refs` already
computes the kind→kind adjacency and is wired to **kind co-selection**; extending it to per-pair **relation**
guidance is the delta. Recommendation: #11656 keeps the write gate; the enumeration is named as its paired
deliverable, because a gate without it is the *"partial enforcement reads as fixed"* failure the graph
already holds.
