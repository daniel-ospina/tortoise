---
title: "#14304 — the ownedBy DIRECTION fix: the flip, the fix, and how to re-measure"
type: evidence
domain: platform
doc_status: live
created: 2026-11-27
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "#14304, #14592, #14606, #11656, #14285, tortoise/relation_write.py, tortoise/relation_guards.py, packs/dev/manifest.yaml, docs/ONTOLOGY.md §3.6"
---

# #14304 — the `ownedBy` direction fix: what was measured, what changed, how to re-measure

**The defect.** On a fresh graph with a fixed corpus (`#14592`, merged as
`#14606`), a document stating *"Marta Reyes owns the cold-chain temperature
log"* produced **8 `ownedBy` rows written `Subject -> Object`**. Under
`ownedBy`'s documented sense — `docs/ONTOLOGY.md` §3.6 and
`pack_registry.SEMANTICS_DIRECTIONS["ownedBy"] = "->"`, where `->` means **the
TARGET owns the source** — that asserts **the log owns Marta**. The fact is
present; its direction is reversed. `#11656`'s endpoint leg cannot see it (both
endpoints are legal kinds) and its `reversed_pair` outcome cannot either (no
pack declares `ownedBy` for `core:naturalPerson`↔`Object` in *either*
orientation, so the measured write is `undeclared_pair` both ways).

## 1. Where the meaning is flipped (file:line)

| step | what it does | orientation? |
|---|---|---|
| `extractor_v2.py:1773` (`OUTPUT_CONTRACT`, `relations[]`) | the model emits `{src, dst, predicate}` | **word order** — `src` is the sentence's subject. Unlike `operators[]` (`:1763-1769`, *"src/dst ORDER IS PART OF THE MEANING … NOT interchangeable"*), the relations contract states **no** orientation. |
| `extractor_v2.py:10759-10848` (`_assemble_relations`) | validates/normalises endpoint NAMES, dedups | none — names only |
| **`relation_write.py:812-830`** (`plan_relation_edges`, structural arm; the direct arm at `:861` likewise) | builds `RelationEdge(src_id=…, dst_id=…)` **from the stated order**, with no reference to the predicate's declared sense | **← THE FLIP** |
| `relation_write.py:969` (`apply_relation_edges`) | `sdk.link_entities(predicate, edge.src_id, edge.dst_id)` | passes it through verbatim |
| `projection/edges.py::create_edge` | `MERGE (a {id:$src})-[r:ownedBy]->(b {id:$dst})` | the stored edge |

So the extractor's word order is *a legitimate reading of "owns"*; the step that
turns it into a graph assertion **in the wrong direction** is the planner. That
is where the fix is.

**Why the declared orientation is not a matter of taste.** Every row the packs
ship for a verb with a label-spelled end puts the `:Subject` on the **target**
side — `packs/dev` `ownedBy: core:goal -> Subject` (*"its `->` reads 'the
subject owns the goal'"*), `packs/project-management` `assignedTo: pm:card ->
Subject`, `packs/venture` `holds: venture:investment -> Subject`. `ONTOLOGY.md`
§3.5 gives the canonical row as `(:Object "Customer Profile")-[ownedBy]->(:Subject
"Daniel")`; `projection/edges.create_owned_by(entity_id, subject_id)` writes
entity→subject; and `SDK._get_owned_entities(subject_id)` reads
`(s:Subject)<-[:ownedBy]-(e)`. An inverted row is therefore not merely odd — it
is **invisible to the read door** that answers *"what does this person own?"*.

## 2. What changed

* `tortoise/relation_guards.py` — a new **ORIENTATION** leg beside the endpoint
  leg: `orient_declared_pair` / `declared_label_sides`, outcomes
  `as_asked` / `reversed_orientation` / `abstain`. It reads the LABEL-SPELLED
  side of the declarations and nothing else, and abstains wherever the
  declarations cannot answer (both ends are kinds; a `:Subject` on *both* ends).
* `tortoise/relation_write.py` — the planner consumes that verdict **before** it
  chooses an arm, so both the structural and the direct arm carry the declared
  orientation. A reversed pair is **redirected, not dropped**
  (`docs/ax/ax-principles.md` §1): the ends are swapped, the predicate and its
  priced meaning are untouched, and the correction is **declared** on the receipt
  row (`RelationEdge.orientation`), in `warnings` (`narrative relation
  RE-ORIENTED (#14304)`), and in an additive `relation_write.reoriented` count.
* `tests/test_relation_orientation_14304.py` — 19 assertions on the verdict and
  on the plan/write path.

## 3. Proof

### 3a. The assertions are NOT vacuous — they red on the parent tree

Measured in a detached worktree at the parent commit `32ce876fbffa`
(`git worktree add /tmp/prefix-14304 --detach HEAD`), with a 3-test probe that
imports only what exists there:

```text
$ uv run python tools/test_lane.py run -- tests/prefix_probe_14304.py -q
_________________ test_the_corpus_pair_is_written_owned_first __________________
>       assert (edges[0].src_id, edges[0].dst_id) == (ACCOUNT, DANA)
E       AssertionError: assert ('subj-dana',...ordicfreight') == ('obj-nordicf..., 'subj-dana')
______________________ test_the_writer_receives_the_declared_orientation ______
>       assert [c[:3] for c in writer.calls] == [("ownedBy", ACCOUNT, DANA)]
E       AssertionError: assert [('ownedBy', ...rdicfreight')]
________________________ test_the_receipt_declares_the_correction _____________
>       assert result["edges"][0]["orientation"] is not None
E       KeyError: 'orientation'
3 failed in 41.22s
```

That is the inversion, at the seam: the plan hands `link_entities` the pair in
the order the sentence was written.

### 3b. The same emission, on the fixed code

`replay_14592_relations.py` replays the `#14592` run's **own recorded rows**
(`out/ingest-receipt.json` → `relation_write.edges[]`) against the kinds the
committed reads resolved (`out/census.json` → `recall_people`), through the fixed
planner. It invents nothing: where no committed read resolved an endpoint's kind
the row is reported `UNRESOLVED` and the guard abstains, exactly as it does in
production.

```text
$ uv run python docs/evidence/14304-ownedby-direction/replay_14592_relations.py
resolved endpoints from the committed reads: 5
  1a21036f576-4ff0e1fbd786  'Marta Reyes'  kind='core:naturalPerson'
  1a21038124d-aa5c6c6cfc8f  'cold-chain temperature log'  kind='object'
  …

── ownedBy: 8 row(s) written by the #14592 run
   REPAIRED  core:naturalPerson -> object  ==>  object -> core:naturalPerson
             [relation-orientation-reversed, evidence=declared-endpoint-label]
   REPAIRED  core:naturalPerson -> object  ==>  object -> core:naturalPerson
             [relation-orientation-reversed, evidence=declared-endpoint-label]
   1a21037b619-… -> 1a21037b672-…: UNRESOLVED src kind — the guard abstains
   …

TOTAL  repaired=2  unchanged=0  unresolved(abstained)=7
```

The two REPAIRED rows are exactly the two the `#14592` report cites as its
witness (§G2's table: Marta→log, Dev→capital spending approvals), and they are
the two whose kinds the committed reads resolve. The remaining six `ownedBy`
rows and the one `managedBy` row have at least one endpoint whose kind is not in
the bundle; the report itself says so (*"their kind is not re-derivable from this
bundle"*). Their pre-fix shape is the same (`src` = a `:Subject` person), so
production will repair them too — this evidence does not claim what the bundle
cannot show.

**Reading the direction.** `account --ownedBy--> Dana` is the declared direction,
not `Dana --ownedBy--> account`. The brief's acceptance says *"`Dana
--ownedBy--> account` (or the pack's declared direction)"* — those two are
mutually exclusive, and the evidence resolves it: a row `Dana --ownedBy--> the
NordicFreight account` asserts **the account owns Dana**, which is the very
inversion `#14304` reports. The pack's declared direction (`core:goal ->
Subject`, i.e. owned-thing → `:Subject` owner) and the read door
(`_get_owned_entities`) agree, so the fixed graph holds
`NordicFreight account --ownedBy--> Dana`, and *"who owns the account?"* now
answers **Dana**.

## 4. The backfill question — existing graphs already hold inverted rows

**This change does not rewrite history, and it must not be silently replayed.**
Rows already written by the narrative relation leg before this fix keep the
orientation they were written with: the seam decides at write time, and there is
no journal record that says what the extractor *stated*, only what was written.
So the answer is **detect-and-re-derive, not migrate**:

1. **Count** the inverted rows (read-only):

   ```cypher
   MATCH (a:Subject)-[r:ownedBy|managedBy|authoredBy]->(b)
   WHERE NOT b:Subject
   RETURN type(r) AS predicate, count(*) AS inverted
   ```

   Anything this returns is a responsibility edge whose SOURCE is a Subject —
   the shape this fix no longer produces. (Run it through the graph's own query
   door for the graph in question; on the hosted graph that is the MCP surface,
   per the repo's mcp-first rule.)

2. **Re-derive, do not flip.** The orientation is a fact about the *statement*,
   and the statement is only in the source corpus. Re-capturing the session
   (`mine_knowledge_from_session`) writes the edge in the declared orientation
   now; the projection's `MERGE` makes the corrected edge idempotent, and the
   inverted row then has to be deleted (`match … delete r`) in the same
   operation, because a MERGE on `(a)-[r:T]->(b)` cannot rewrite an existing
   `(b)-[r:T]->(a)`.

3. ⛔ **Do not blank-flip a graph.** A bulk `swap` would corrupt the legally
   `<-` rows §3.6 allows (`investment --ownedBy--> company`, where the SOURCE
   owns the target as a stake) and every row a pack declares with
   `direction: "<-"`. That is why the read in step 1 is *the shape check*
   (`a:Subject`, `b:NOT Subject`) rather than "everything" — and even it should
   be reviewed row by row before a delete.

4. **Nothing in `live-v5` is touched by this PR.** No write of any kind runs in
   this lane against the hosted graph.

## 5. Residuals this fix does NOT close (stated, not hidden)

* **`managedBy` / `authoredBy` are not declared as relations by any shipped
  pack** (`declared_relation_pairs()` has rows for `ownedBy`, `assignedTo`,
  `holds`, `hasPart`, `dependsOn` only), so the orientation leg abstains on them.
  `_create_object(..., managedBy=…)` writes through the entity door, which
  already orients correctly (`entity -[:managedBy]-> subject`). Closing the
  narrative-route case is a **pack declaration**, not a verb list in code.
* **Labels other than `Subject`** (`Object`, `Event`, `Point`, `Source`,
  `Document`) have no kind-level test in the planner — it holds `(id, kind)`,
  not the node's label — so a pack declaring one of those as a relation endpoint
  gets no orientation opinion. No shipped row spells any of them today.
* **`ingest`'s first-class relation route** writes through the projection
  primitive and was not changed here; it is where `#11656`'s pair judgement
  already runs. Same pairing rule, different writer — tracked with `#14285`.
* **Extraction prompt.** `OUTPUT_CONTRACT`'s `relations[]` still states no
  orientation. The deterministic guarantee is the write seam (this PR); teaching
  the model the predicate's sense would cost prompt tokens on every capture and
  buys no guarantee, so it is deliberately out of scope (the brief's "one token
  budget"). The guard is idempotent if a future prompt change makes the model
  emit the declared order already — a correct statement is planned unchanged
  (`test_an_already_correct_pair_is_planned_unchanged`).
