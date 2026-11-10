---
title: "Per-pack relation census — #11083, declared → writable → sense → written"
type: evidence
domain: platform
doc_status: live
created: 2026-11-08
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "#11083, epic #5087, packs/*/manifest.yaml, ONTOLOGY-CHANGELOG.md v3.32, tools/pack_relation_census.py"
---

# The per-pack relation census (#11083)

**What this artifact is.** One row per DECLARED connection — `relations[]` and
`hierarchies[]`, both surfaces — for every installed pack, with the layer of each
cell stated: `declared → writable → effective direction → written`.

**Re-derive it, do not edit it.**

```bash
uv run python tools/pack_relation_census.py --check                 # artifact == a live hermetic re-derivation
uv run python tools/pack_relation_census.py --write-probe --out docs/evidence/11083/pack-relation-census.json
```

The second command **mints a throwaway embedded store** and deletes every node it
minted before it returns. It does **not** read `TORTOISE_DB_URI`: a real graph
must be named with `--uri` (that variable is this repo's documented graph setting
and in the fleet it usually names the shared docker container — an implicit read
was a write into an unspecified target with no confirmation).

`--check` compares only the cells a hermetic re-derivation can answer. `written`
is a GRAPH fact, so it is compared by `tests/test_pack_logic_writeprobe_11083.py`
instead — a check that needs a database is run rarely, and that is how an
artifact rots.

## Baseline at this stamp — 7 packs, 45 declared (39 relations + 6 hierarchies), 41 distinct connections, 0 load errors

⚠️ **Rows ≠ connections.** `pack_hierarchy` calls a `hierarchies[]` entry and a
`relations[]` row over the same pack + pair + meaning *"one declaration spelled
twice"*, and **4 of the 6 hierarchy rows are exactly that** (`dev` theme→epic,
`dev` epic→issue, `marketing` campaign→content, `product-strategy`
product→feature). The artifact derives both numbers from each hierarchy row's own
`restates_relation` (`population.declared` / `population.restated_hierarchy_rows`
/ `population.distinct_connections`) rather than leaving the subtraction to a
paragraph: 45 rows are 41 distinct connections, and the 2 that no `relations[]`
row declares **in the same direction** are venture's.

⚠️ **The join is ORDERED** — `pack_hierarchy._matching_row` matches the same
`(fromKind, toKind)` in that order, plus the same predicate or the same semantics
— and the order is load-bearing rather than incidental: venture's
`Investment → Program` (`hasPart`) states the same FACT as its `relations[]`
`partOf` (`program → investment`, `semantics: hasPart`) in the **inverse**
direction, so the ordered join does not count it. Counting inverse-named ranks
would need a predicate-inversion table, i.e. a second parser this artifact
deliberately does not have, so the rule is STATED (here and in
`population.note`) instead of the number being widened — and the note states the
RULE only, because only a computed cell may carry a count.

| | before #11083 | after #11083 |
|---|---|---|
| `writable` | 32 | **38** (`#11001` added `dev:ownedBy`, `dev.addresses` ×2) |
| `declaration-only` | 0 | **1** (`pm:onBoard`) |
| `predicate-not-in-allowlist` | 4 (all `pm`) | **0** |
| declared population | 42 | 45 — **nothing was deleted to make a count green** |

`written`, measured by the write probe (one node per declared KIND, cached and
reused; a row whose `from_kind` and `to_kind` are the same kind mints its
destination its own node — so a same-kind row is measured between TWO nodes —
the row's own write path invoked, the graph read back):

| shape | rows | what it means |
|---|---|---|
| `direct` | 9 | a real `(:Object)-[:p]->(:Object)` (or `->(:Subject)`) edge — 3 relations (`dev:hasPart`, `dev:dependsOn`, `dev:ownedBy`) + 6 declared hierarchies |
| `mediated` | 35 | the relation IS persisted and the two Objects ARE connected — **through an operator Point**: `(:Object)-[:INPUT]->(:Point {label:p})-[:IMPL]->(:Object)`. A different graph from a direct edge, which is why this column exists |
| `none` | 1 | `pm:onBoard` — `declaration-only`, reason printed in the row |

⚠️ **A census that asked only "written?" would report 44/45 and hide that the
direct Object→Object class is 9/45 and that 35 rows are operator bridges.**
That is the whole argument for the three-way column.

**Why `direct` is 9 and not 11.** A Layer-3 domain word can only be written
through the operator path: `create_edge` is STRICT and keyed on the edge's own
predicate, so `blocks`/`partOfSprint` are not structural edge types (only
`dependsOn`/`hasPart`/`ownedBy` are — `ownedBy` is the direct row this pack
declares). Keeping the words — which the ruling requires —
therefore moves those two `pm` rows from `direct` to `mediated`. That is the
measurement being honest, not a regression: the same two declarations, written
through the writer their own vocabulary selects.

⚠️ **A `from_kind == to_kind` row is measured between TWO nodes.** The probe
caches one node per KIND, so `pm:blocks` (`pm:card → pm:card`) used to hand the
same id to both sides and measure a **self-loop** — a real edge, but not the
declared connection, and a false-positive pathway (the read-back matches
`(a {id:$s})-[r]->(b {id:$d})` with `s == d`, so a refused two-card edge would
still read `direct`). The cache is keyed on the **namespaced** kind for the same
reason: `dev:issue` and `pm:issue` are different kinds that share a label, and a
label-keyed cache handed both the same node.

⚠️ **`written_direction` has three readings, not two.** A `mediated` row carries
the direction the graph recorded (`<-` for `venture:holds`); a `direct` row reads
`n/a: direct edge …` because a plain `(a)-[:p]->(b)` has no direction attribute —
the predicate is the sense; and `written: "none"` is the only state that leaves
the cell empty, because nothing was written to read. A `None` in that column
would otherwise be indistinguishable from "not measured" (#10995's rule, applied
to a column).

### The probe's cleanup contract is a CELL, not a claim

`probe` in the artifact records the store, the endpoint rule, how many ids were
minted, how many were deleted, and `probe.leftovers` — the ids a delete FAILED on.
The committed artifact's `leftovers` is `[]`, which is what an operator pointing
`--uri` at a real graph needs to check before trusting the run. It is a graph
fact, so `--check` does not compare it (like the `written` cells).

## `pm`'s four rows — resolved, not silenced (#2766's three-layer convention)

The rows were declared in the legacy `from`/`to` shape, so they carried no
`mechanism` and bound no write path. Three named real project-management facts;
one named a view. Population stays four.

| declared word | disposition | reason |
|---|---|---|
| `blocks` | stays the LABEL; `semantics: dependsOn` + `direction: "<-"` (`pm:card → pm:card`) | "A blocks B" means B depends on A — a `blocks` row carrying `dependsOn`'s `->` would transfer confidence blocker → blocked. The ruled direction flip, carried by the row's own `direction` |
| `partOfSprint` | stays the LABEL; `semantics: hasPart` (`pm:sprint → pm:card`) | ONTOLOGY §3.7's *"no separate `partOf` edge"* is about the STRUCTURAL edge type; the label rides the operator path, which is exactly what `venture:partOf` already ships |
| `assignedTo` | keeps its label, `semantics: managedBy` | §3.6: `managedBy` = operational responsibility (RACI Responsible), `ownedBy` = accountability. The same three-layer shape `venture` already ships |
| `onBoard` | `declarationOnly: true` + reason, declaration KEPT | a board is a **view** over cards, recomputed from `cardMoved` events; storing `card → board` is a second source of truth for one fact and goes stale on the next move (owner ruling 2026-11-08) |

**Every one of the four keeps its DOMAIN WORD.** The ruling (#11083 comment
6714848519) is *"keep each domain word as the **label**, give it the canonical
layer-2 meaning, and it becomes writable"* — not "rename the row to its meaning".
A rename deletes a word from the declared vocabulary (`CORE_OPERATOR_LABELS`
unions `SEMANTICS_VALUES` with each pack's `relations[].predicate`), so a caller
emitting `blocks` would be told it is undeclared. Pinned by
`tests/test_pack_registry_gaps.py::test_shipped_catalog_reports_the_legacy_relations`.

`declarationOnly: true` is an author opt-in that SILENCES the not-writable
warning, so `declarationOnlyReason` is part of the declaration and the census
prints it. A marker without a reason is a silencer, not a disposition.

## What this census reports and cannot close

| gap | status here | owner |
|---|---|---|
| the pipeline's EMISSION of declared relations is unmeasured — the CONSUMER now exists (`apply_relation_edges`, called from `sdk.py` on `extract_session_v2`'s `relations`), so what is unmeasured is whether that channel emits | `UNMEASURED` | **#11237** |
| `apply_hierarchy_edges` has **zero production callers** | the CAPABILITY is wired; the symbol is still test-only | **#11237 delivered the wiring, not this symbol** — `tortoise/relation_write.py:631` calls `pack_hierarchy.plan_hierarchy_edges`, and the production leg writes through the structural/operator writers itself. `apply_hierarchy_edges` remains called only by `tests/test_pack_hierarchy_11001.py` and named by the capstone pin. That is now a **shape** question (two seams for one job: a plan the pipeline consumes and an applier only tests use), not a `written` gap |
| the commitment spine: `core:goal` (and `plan`, `strategy`, `target`) is named as an endpoint by **0 of 42** declared connections | ⛔ was `dangling`; `goal` and `dev:indicator` are **out of the list at this stamp** | **delivered by #11001** — KIN-2's three connections are declared in `packs/dev/manifest.yaml` and measured `written` above (`goal --ownedBy--> Subject` = `direct`, the two `addresses` rows = `mediated`). `plan`/`strategy`/`target` remain dangling and are NOT KIN-2's |
| `dev:initiative` as a real kind | out of scope | **#11000** |
| `funds` as a Layer-2 meaning | ⛔ **withdrawn** | #7848's correction of 2026-11-08 |

### `funds` — the one item in the issue body that this lane deliberately did NOT implement

The body's checklist requires `funds → (->, IMPL)`. The owner withdrew that on
2026-11-08 (#7848, applying #2766's *"does the machine have to ACT on it?"*
test): nothing in the machine changes behaviour because a relation is a funding
relation, so **`funds` is a Layer-3 label**, it stays a key of
`SEMANTICS_TRANSITION`, and the durable `funds` relation rides on an existing
meaning with `funds` as its predicate. Implementing the checklist row as written
would have re-landed a withdrawn ruling. The `produces` half of the same
2026-11-05 note **stands** and is implemented: production is not causation, and
its direction is `<-` (target → source).

## Two properties this artifact is built to protect

1. **`producedBy` and the `Program → Asset` `hasPart` path are BOTH written and are
   NOT reported as duplicates.** The owner ruled (2026-11-05) that production and
   containment are different assertions. The census carries the pair under
   `duplicate_check.pairs_that_must_NOT_be_flagged` with an empty
   `flagged_duplicates`, so a future lane that "improves" the census into a
   duplicate detector reds a test instead of shipping the regression.
2. **The denominator is DERIVED** (`Σ relations + Σ hierarchies`), never a
   hard-coded 42 — a disposition change moves it, and a stale literal would be a
   false total.

## The one latent class, reported not fixed

`relation_write_verdict`'s `structural` path is the UNION of three writers'
predicate sets, while `create_edge` accepts a narrower one. A declared predicate
in `STRUCTURAL_WRITE_PREDICATES` but not in `_VALID_EDGE_PREDICATES` would be
reported writable by `tortoise pack validate` and refused by the writer it would
use — the pair `pack_hierarchy._binding` had to separate
(`aboutPoint`/`extractedFrom` read as `structural` there and are refused here).
No shipped row hits it today; every row carries `path_disagreement` so the class
cannot become invisible.
