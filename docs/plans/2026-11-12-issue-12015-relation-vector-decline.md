---
title: "A relationship may not carry a stored vector — the cache projection's edge door (#12015)"
type: plan
domain: engineering
doc_status: approved
created: 2026-11-12
updated: 2026-11-12
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "issue #12015, issue #11747, issue #11641, issue #5090, cache projection, relation statement, stored vector, DoD row 2"
---

<!-- research-path: https://github.com/daniel-ospina/tortoise/issues/12015#issuecomment-6818282266 -->
<!-- plan-review: cycles=1, status=clean, version=2.3.0 -->

# A relationship may not carry a stored vector (#12015)

**Status:** approved — PLAN-VERIFY clean (1 round, 1 verifier, 6 findings answered)
**Complexity:** Standard
**Issue:** #12015
**Research:** the `<!-- research: v1 -->` comment on #12015
**Scope + plan (CI check (b)):** the `<!-- issue-scoping: merged scope+plan -->` comment on #12015

## Why

`public.relation` rows carry the same open-set `props` bag as record nodes, and
`cache_projection.relation_statement` writes it verbatim onto the EDGE
(`MERGE (src)-[r:TYPE {idx:$idx}]->(tgt) SET r += $props`). The row's own CHECK
(`chk_relation_props_no_derived`) refuses only the four EP names, so `embedding` is a
legal key in that bag — and a vector stored on a RELATIONSHIP has **no label at all**:
`run_vector_query` validates an `entity_type` and derives a NODE label from it, so
nothing can ever read it. That is `#5090` DoD row 2's forbidden shape, and exactly the
defect `#11641` already ruled on at the DR door (*"a RELATIONSHIP IS THE SAME DEFECT
WITH NO LABEL AT ALL"*).

Reproduced at `origin/main` `8300c102c8`: a relation row with
`props={'embedding': [0.5]*8}` produced a statement whose `params['props']` still held
the vector, and `RecordRelation` had no way to say so.

## What changes

1. **One declaration** — `cache_projection.relation_vector_declined_properties()`,
   which calls `stored_vector_props()` **on every call**, so a rebind of THIS module's
   `STORED_VECTOR_PROPS` moves the rule immediately; the only import-time part is the
   owner→module binding (`from .stored_form import …`). ⛔ Not an "import-time snapshot":
   `06-cache-projection.md` §0.5c owns that wording (and names the natural repair this
   phrasing would invite — hoisting it to a module-level `frozenset` — which reds
   `test_the_declined_edge_property_set_is_DERIVED_not_a_second_list`). It is a function,
   not a constant, because there is no arithmetic to compute, both edge doors should read
   one spelling, and the instrument's falsifier needs a name to replace. The node rule
   stays a set difference; an edge has no label to subtract. The report KEY format gets
   its own single owner too: `relationship_target` / `is_relationship_target` /
   `RELATIONSHIP_TARGET_PREFIX`.
2. **Both edge doors drop and RECORD it** —
   `relation_from_row` (row → view, so a record read cannot advertise bytes the writer
   refuses) and `relation_statement` (its own check, because `record_plan` is public and
   a caller may hand it a `RecordRelation` it built).
3. **The plan reports it** — `ProjectionPlan.dropped_vectors()` adds
   `<relationship:TYPE>` keys (`#11641`'s spelling), with the node loop bounded by
   `len(self.nodes)` so a relation statement can never be read as a node's.
4. **The operator's line names the right survivor** — `ProjectionReport.render()` says
   "unserved label(s) / relationship type(s)"; `record_restore.RestoreOutcome.render()`
   and `rebuild_from_record`'s warning say "target(s) with no query path" (they name no
   object kind), and the survivor clause reads "the edges are restored" where an edge is
   what survived.
5. **The instrument measures the edge door** — `dod_5090_probe`'
   `cache_projection_store_labels()` gains `relationships`, built by running both edge
   doors over every allowlisted predicate, and row 2 unions `<relationship:TYPE>` into
   its orphan set. The predicate had to become bag-aware: a relation statement nests the
   whole bag as ONE param, so the old `prop in stmt.params` read answered "no vector" on
   a tree that stored one.

## Residue (stated, not discovered)

`relation_statement` is additive (`SET r += $props`), and `SET r += map` merges keys
without REMOVIng one it no longer carries — so a legacy cache that already holds
`r.embedding` keeps it across any number of incremental re-projections.

⛔ **Do not state a single remedy without these three facts** (this section said "the ONE
remedy is a full `record-restore`" until round 4, and that is REFUSED on every production
cache — §0.5c of `06-cache-projection.md` is the owner of the wording, this is a summary):

1. an edge property-clear CLAUSE exists in this tree (`ep.py`'s
   `MATCH ()-[r:IMPL|NAND]->() REMOVE r.msg_*`), but no verb clears a DECLINED VECTOR
   property on an edge IN PLACE — measured on FalkorDB, `SET r += {embedding: null}` does clear one,
   so a future in-place route is buildable and is not built;
2. a full `record-restore` DOES clear it, but `_assert_test_graph` (L2) refuses a server
   graph not named `test_*`/`tortoise_test`, which is every production cache — it is a
   remedy for a disposable TEST graph and for embedded mode (per-call token);
3. so on a production cache the operator's only remedy is the explicit property clear
   (`MATCH ()-[r:TYPE]->() REMOVE r.embedding`) — the wholesale route is (2)'s
   `record-restore`, and (2) is why it is refused on a production graph rather than a remedy.

Nothing on the read side reports it — `record_serve` has no relation read (`NOT_SERVED`),
`parity_difference` is label-scoped, `record_reconcile` compares nodes. Same residue as
the `#11747` node rule.

## Out of scope

Which labels get a vector index (`#11901`); whether a relationship should EVER be
searchable (a reopen of `#11641`); the `#11747` node rule.

## Tasks

### Task 1: the declaration

**Intent:** give the rule a name both edge doors read, derived from the store seam.
**Acceptance:** `relation_vector_declined_properties() == stored_vector_props()`, and
emptying it makes the instrument's edge arm see a store.
**Files:** modify `tortoise/cache_projection.py`; test `tests/test_cache_projection.py`.

### Task 2: the row door

**Intent:** a record READ must not advertise a vector the writer refuses.
**Acceptance:** `relation_from_row` returns props without the vector, `dropped_vectors`
records it with a `<relationship:TYPE>` reason, other open-set props untouched.
**Files:** `tortoise/cache_projection.py`; `tests/test_cache_projection.py`.

### Task 3: the writer door

**Intent:** a directly-built `RecordRelation` is refused too.
**Acceptance:** `relation_statement(...)` has no vector in `params['props']`, records
the drop, and this test reds if the writer's loop is deleted.
**Files:** `tortoise/cache_projection.py`; `tests/test_cache_projection.py`.

### Task 4: the plan report

**Intent:** a rebuild DELETES the vector, so the count must reach the operator.
**Acceptance:** `{"<relationship:IMPL>": 1}` for an edge; mixed node+edge plan reports
both; two edges aggregate; empty plan reports nothing.
**Files:** `tortoise/cache_projection.py`; `tests/test_cache_projection.py`;
`tortoise/projection/__init__.py`; `tortoise/record_restore.py`;
`tests/test_9556_record_restore_verb.py`.

### Task 5: the instrument

**Intent:** row 2 must SEE an edge vector, or it keeps printing `none` over one.
**Acceptance:** live tree → `relationships == []`, row `MET`; decline emptied →
`"IMPL" in relationships`, row `NOT MET` with `<relationship:IMPL>` in
`stored-without-a-query-path`.
**Files:** `tools/dod_5090_probe.py`; `tests/test_dod_5090_probe.py`.

### Task 6: docs

**Intent:** the rule, its residue and its falsifier are readable where the node rule is.
**Acceptance:** `06-cache-projection.md` §0.5c exists and states the residue;
`S3-VECTOR-CENSUS.md` §6 re-cuts the collected count (the file's own pin enforces it).
**Files:** both docs.

## Verification

```text
uv run python tools/test_lane.py run -- tests/test_cache_projection.py \
    tests/test_dod_5090_probe.py tests/test_9556_record_restore_verb.py -q
→ 317 passed, 1 skipped

uv run python tools/test_lane.py run -- tests/test_cache_projection_live.py \
    tests/test_record_serve.py tests/test_reconciliation.py \
    tests/test_record_reconcile_trigger.py tests/test_11641_runtime_vector_label.py \
    tests/test_embed_writer_register.py -q
→ 338 passed, 3 skipped
```

⛔ Both figures are readings of a TREE, so they are true only of the head they were taken at: THIS
branch's head, which INCLUDES the 11 tests this change adds — 9 in block 1 (`6 + 2 + 1` across its
three files) and 2 in block 2. Compare them with your own run of the same two commands at the same
head, never with `main`, and re-derive rather than carrying any number forward: `main` also gains
tests under these files over time. `uv run ruff check` is green at the same head.
