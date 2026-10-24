---
title: "`how-to-use-tortoise` — Step 1 write-pipeline eval (the skill-less baseline)"
type: evidence
domain: epistemic
doc_status: live
created: 2026-10-22
issue: 9675
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise, how-to-use-tortoise
---

# Step 1 — the skill-less write-pipeline eval (#9675)

> **Status: Step 1 complete — instrument, frozen gold, ten runs and the derived
> failure list all landed.** Every number is read from the committed run records
> in `eval/how-to-use-tortoise/step1/runs/`, with each agent's own `write.py`
> beside it. Nothing here is a review verdict — the measurement is the artifact.
> **Steps 2–3 (the rewrite) remain gated behind #4282 Phase 3.**

## Why this eval exists

Issue #9675 replaces a job-by-job framing of `how-to-use-tortoise` with **one
operation**: the write. *"If events are classified as objects and subjects as
points, the graph doesn't work. If mitigations are not understood as relevance
mitigations, and hence not used, then there's no proper logic used and the
outputs of the graph's calculation are wrong."*

The method the issue prescribes is the opposite of how the document was built:
run the agent **without** the skill, record the real failures, and only then
write — because a document authored first records **imagined** failures. We never
did that for this document, and this eval is the first time the outline is a
function of measurement rather than of imagination.

**Step 1 is the only scope #9675 releases today.** Steps 2–3 (the rewrite) are
gated behind #4282 Phase 3, because the rewrite must cite the 27-tool surface's
names, and writing against the 81 would document names about to be retired.

## What is frozen, and when

`eval/how-to-use-tortoise/step1/inputs.json` holds ten representative inputs and
their **gold structure**, frozen **before** any run. The gold is derived from
`docs/ONTOLOGY.md` — the definitions owner (§4.1 Point, §4.2 Subject, §4.3
Object, §4.5 Event, §4.6 Source, §5 state-centric vocabulary + the #7813 owner
ruling on the point/object/event overlap). This document deliberately does **not**
restate those definitions; two ontologies that drift is the failure class the
issue names.

Only the scoring **directives** have ever been added after a run: the
three-valued operator rules (`expect_nand` / `expect_mitigation`, corrections
6 and 12) and the endpoint shape an edge item is graded on (correction 29).
Each one can only ADD a finding, never excuse one, and no expectation, layer or
kind has moved since the freeze.

Each input names the **adjacent layer pair it is designed to confuse** — the
issue's own requirement: *"pairwise discriminator tests … because the failures
are confusions between adjacent layers — a per-layer positive test does not catch
the agent that saw the right word and picked the neighbouring layer."*

| # | Input cell | Confusion under test |
|---|---|---|
| 1 | portfolio update | Object-vs-Event, Event-vs-Object |
| 2 | meeting transcript | Point-vs-Event, Event-vs-Object |
| 3 | research paper | Subject-vs-Point, Object-vs-Point |
| 4 | decision request | Object-vs-Point, Event-vs-Object |
| 5 | correction to an existing claim | Point-vs-Event, truth-vs-relevance |
| 6 | duplicate entity | Object-vs-Point (identity) |
| 7 | regulatory filing | Source-vs-Object, Point-vs-Source |
| 8 | person appointment | Subject-vs-Point, Subject-vs-Event |
| 9 | contradicting claim | truth-vs-relevance (NAND-vs-mitigation) |
| 10 | product spec / commitment | Object-vs-Point, state-vs-occurrence |

## Method

**The condition is "the tools and no skill".** Each input is handed to an agent
that (a) has the write surface, (b) is given the input text and a one-line
instruction to record it, and (c) is **not** given the skill, this document, or
`docs/ONTOLOGY.md`. Inputs 5, 6, 9 and 10 are *incremental* — a pre-built graph
holds the pre-existing claim/entity they act on, because the interesting failures
are the ones against existing knowledge (supersede vs edit; duplicate vs update;
NAND vs mitigate).

**The record is read off the graph, never off the agent's summary.**
`tools/ax_write_eval.py record` opens the graph the agent wrote and records every
node's **label** (the layer), its kind property, the operator points and
`op_type`, every edge, and the Sources. `tools/ax_write_eval.py score` compares
that record against the frozen gold and emits P0/P1 findings with the
severity rules the review skills use:

| Severity | Rule |
|---|---|
| **P0** | layer confusion (an item filed on a different label than its gold layer, and the expected layer does **not** hold it) · item absent/absorbed · no Source at all · a contradicting claim filed with **no NAND** (an IMPL or a mitigation does not attack the truth of a Point; one act, one finding) · change written without supersession |
| **P1** | a kind **no vocabulary reads on the layer it was written to** — the four sources the instrument consults are the §5 canonical sets, a loaded pack, the kinds `tortoise.sdk` registers with `register_kind`, and the legacy flat registry behind `domain_loader.kind_is_known(kind, bucket)`; a kind that only the FLAT registry accepts *and* that belongs to another axis (`meeting` on a Point, `issue`/`incident` on a Point, `workflow`) is reported as exactly that, never blessed as declared · a kind written on **another layer's property** (`subjectKind` on an Object — nothing on the Object layer reads it) · the graph-wide census of all of the above beyond the gold · **two homes** for one item (the expected layer holds it *and* a second layer does) · a declared-but-wrong kind · duplicate nodes for one entity · Source nodes with zero claim→source edges · a NAND where nothing contradicts · a node with no kind property |

The matcher is deliberately dumb (token overlap). A fuzzy matcher would *hide*
the exact failure being measured: a `Northwind Logistics Ltd` that became a
SECOND node must read as a duplicate, not be silently unified with the first.

Reproduce (one input, or all ten):

```bash
# one input, standalone graph
uv run python tools/ax_write_eval.py score \
    --db /tmp/ax-eval/portfolio_update.db \
    --gold eval/how-to-use-tortoise/step1/inputs.json \
    --id portfolio_update

# the four incremental inputs need their before-graph first
uv run python eval/how-to-use-tortoise/step1/setup.py \
    --id contradicting_claim --db /tmp/ax-eval/contradicting_claim.db --fresh
```

`--fresh` deletes, so it refuses anything it cannot positively identify as its
own graph: a **directory** (an embedded `TORTOISE_DB_PATH` is always a file, so
a directory is a mistyped `--db`), a file with no `.ax-setup.json` marker
beside it, or a symlink. It exits non-zero naming the reason and deletes
nothing, so a one-level-short `--db` cannot cost the directory it points at
(#9695). The marker is written by `setup.py` itself when it seeds.

The same ownership rule governs a **plain seed** (no `--fresh`), and that is
what makes the delete guard mean anything: if the unflagged run were allowed to
seed into an unmarked file, it would write that very marker beside it, so the
next `--fresh` could no longer tell the operator's store from the tool's. Both
paths refuse an existing file the tool cannot identify, and neither writes
anything when it refuses.

The instrument's scoring rules are pinned by
`tests/test_ax_write_eval.py` — no database, no network, no embedding model:

```bash
TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_ax_write_eval.py -q
```

The count is deliberately not restated here: it is the one number in this
document that nothing can recompute from a committed artifact, so a prose count
is exactly the kind of stale flag this eval's own review rounds spent their time
removing (#9695 review P2).

## Results — measured 2026-10-22, ten runs

Every number in the two tables below is recomputed from the graph each agent
wrote, with two exceptions that no committed artifact re-derives and that are
labelled where they appear rather than trusted: the item matcher's partial
scores (`3 of 4 stems … 0.375`), quoted from the matcher and explained under
*Instrument limitations and corrections* below, and the call-shape count of runs
that reached provenance through `create_point(extractedFrom=[…])` rather than
`create_edge` (`seven`), which is a reading of each run's own `write.py` and not
of the graph. Each run
directory holds **both** halves — `graph.json` (every node's label, kind and id,
every edge) and `score.json` (the verdicts read from it) — beside the agent's own
`write.py`. Re-scoring a committed `graph.json` reproduces its `score.json`
exactly — one command, no database:

```bash
uv run python tools/ax_write_eval.py score \
    --graph eval/how-to-use-tortoise/step1/runs/contradicting_claim/graph.json \
    --id contradicting_claim --out /tmp/rescore
```

That is what makes these numbers auditable rather than asserted, and
`test_every_committed_run_reproduces_from_its_own_graph` pins it for all ten
runs. A `graph.json` written before the kind-axis fix carries one `kind` per
node and no `kind_prop`/`foreign_kinds`; the scorer falls back to the layer's
own property, so old records stay scorable and the ten committed verdicts are
unchanged by that fix.

| # | Input cell | P0 | P1 | Layers written | The failure |
|---|---|---|---|---|---|
| 1 | portfolio update | 2 | 5 | 5 Point · 3 Subject · 2 Object · 3 Event · 1 Source | ARR and headcount (state) written as **Points**; `subjectKind: company\|investor` undeclared; the release event's `eventKind: disbursement` is a declared *pack* kind but not the kind this occurrence is |
| 2 | meeting transcript | 2 | 7 | 7 Point · 3 Subject · 4 Object · 1 Event · 1 Source | the **decision has no Event of its kind** — only a Point (the meeting Event is the session, not the decision); `subjectKind: person` ×3, `objectKind: offering`, `pointKind: issue`; one **unexplained NAND** (two edges, onto the comms plan and onto the slip forecast) |
| 3 | research paper | 0 | 4 | 6 Point · 1 Subject · 3 Object · 1 Event · 1 Source | `sourceKind: research` — that is the **documentKind (genre) axis**; `subjectKind: authorGroup` undeclared; the findings use the §5 **core** `observation` kind where the state-centric ruling says `statement` (not a legacy kind — `observation` is canonical, the ruling is about which claim-kind fits) |
| 4 | decision request | 3 | 1 | 7 Point · 1 Subject · 4 Object · 2 Event · 2 Source | **two homes** for the request (an Event names it *and* a Point holds it); the **goal** is a Point not an Object; the **deadline** is an Event; the **estimate** is a Point. Its `option`/`criterion` Points are **declared** (`tortoise.sdk` registers both) — a correction: they used to be billed as inventions |
| 5 | correction to a claim | 0 | 1 | 6 Point · 1 Event · 1 Source | both gold items perfect — supersession with the old claim kept, provenance written — and two undeclared kinds elsewhere (`eventKind: correction`, plus `pointKind: incident`, which the product's legacy registry *does* accept and is therefore a correction, not a finding) |
| 6 | duplicate entity | 0 | 3 | 3 Point · 1 Subject · 1 Object · 1 Event · 1 Source | the **alias was correctly folded onto the existing node** (no duplicate — the measurable this input exists for); the renewal lands on an Event, but with `eventKind: customerCommitment` (undeclared) and a `subjectKind: person` |
| 7 | regulatory filing | 0 | 2 | 5 Point · 2 Subject · 1 Object · 1 Event · 1 Source | no layer failure and no undeclared kind: Source + subject kinds correct (`core:organization` is the **declared** spelling); the two figures use the §5 **core** `observation` kind where the state-centric ruling says `statement` |
| 8 | person appointment | 2 | 4 | 4 Point · 2 Subject · 5 Object · 1 Event · 1 Source | the **organisation and the role** written as Objects; `subjectKind: person` ×2; the appointment Event carries an undeclared `eventKind: employmentStart` |
| 9 | contradicting claim | 2 | 2 | 7 Point · 1 Event | **no NAND** — the contradiction was answered by *mitigating the IMPL operator*: relevance dampened where truth was at issue. No Source written at all |
| 10 | product spec | 2 | 2 | 4 Point · 1 Subject · 2 Object · 1 Event · 1 Source | **two homes** for the ship date (plan Object + target Point); the success bar is only a Point; v1's dates are Points, not on the v1 Object |

**Aggregate: 13 P0 and 31 P1 across the ten inputs; 6 of 10 inputs carry at
least one P0, and no input is clean graph-wide.** Four inputs carry no P0 at
all, and two of those are near-clean — the correction (0 P0, one undeclared
kind elsewhere) and the duplicate (0 P0, the alias fold correct). The failures
cluster where a *judgement* has to be made, which is the reason the document's
steps are decisions rather than rules.

### Failure classes (each count recomputed from the ten `score.json`s)

| Class | Count | What it looked like |
|---|---|---|
| **Undeclared kind** (a kind the product's own vocabulary does not know on that layer) | 14 scored items across 5 inputs | `subjectKind` ∈ {`person` ×6, `company` ×2, `investor`, `authorGroup`} · `objectKind: offering` · `sourceKind: research` · `eventKind: customerCommitment`, `employmentStart`. Every one accepted by the write path (warn-not-block, #1934). **Not** counted: `core:organization` (the declared key), `disbursement`/`tranche`/`estimate` (the venture/pm packs declare them), `option`/`criterion`/`evidence` (the SDK's `register_kind` declares them, and no other axis does), and `pointKind: issue` ×2 — **the flat registry** accepts `issue` on the point bucket, so it is not "invented"; it is declared-for-the-Object-axis, which is the wrong-but-declared class below (corrected in round 6) |
| **Uncatalogued kinds beyond the gold** (the graph-wide census, 7 inputs) | 7 findings | the gold names some items; the census asks the whole graph, which is what keeps a near-miss from reading 0/0. Among them (×N = how many times the kind was **written** across those inputs, a node the gold resolved included): `objectKind: openQuestion`/`governanceSeat`/`role` ×3/`deliverable`/`productSurface`/`organization` ×2, `eventKind: correction`/`planRevision`/`observation`, `sourceKind: spec`/`note`/`contract`/`portfolioUpdateMemo`/`meetingTranscript` — plus **one kind declared for another axis**: `pointKind: incident` on a Point (`incident` is an Object kind; the flat registry accepts it on the point bucket, so it is reported as a wrong axis, not as an invention) |
| **Layer confusion — only the wrong layer holds the item** | 9 items across 5 inputs | portfolio: ARR + headcount as Points · decision request: goal, deadline, estimate · meeting: the decision · person appointment: organisation, role · product spec: the success bar |
| **Two homes — the right layer holds it AND a second one does** | 2 items across 2 inputs | decision request: the request (the Event names it, a Point also holds it) · product spec: the ship date (plan Object + target Point). Weaker than an absence, and a different instruction: express the second reading as a claim *about* the node |
| **Wrong-but-declared kind** (legacy-valid, not invented) | 7 | `pointKind: observation` where the state-centric ruling says `statement`; `eventKind: disbursement` where the occurrence is an occurrence/milestone; `pointKind: issue` where the item is a statement ×2. A different lesson from an invented kind, which is why the instrument separates them |
| **Edge on the wrong endpoints** | 0 | the two gold edge items (`reports to`, `owned by`) are graded on BOTH ENDS as well as the relation, and the gold must declare both: `reportsTo` written Point → Point satisfies a relation-name test while saying the opposite of the item, so the shape the gold's `why` always stated is now declared as `endpoints` and scored. Both committed runs wired it correctly (`reportsTo` Subject → Subject, `ownedBy` Object → Subject). Correction 30: `owned by` had declared only its `to` end, so its `from` end was ungraded — an `ownedBy` written Point → Subject scored `OK`; `_edge_endpoints` now refuses a one-sided declaration |
| **Item absent from the graph** | 2 | meeting: the 80% slip forecast (the run wrote a Point about it, but the node says "probability" where the item says "chance" — a synonym, leaving 3 of 4 stems and a 0.375 partial score under the 0.5 floor) and product spec: v1's dates. Both are itemised below |
| **Truth vs relevance** | 1 input, 1 finding | contradicting claim: no NAND, with the dampener named **inside** that finding (the record's `mitigations` shows the `mitigated_by` edge). One act, one finding — it used to be charged twice |
| **NAND where nothing contradicts** | 1 | meeting: one NAND operator with two edges (onto the comms plan, onto the slip forecast). A judgment call, and the instrument treats it as a finding because a contradiction operator on a claim nobody attacked computes a wrong confidence |
| **Provenance** | 1 | contradicting claim wrote **no Source at all**. Of the other nine, eight wrote their own Source (the correction's was seeded by the fixture) and **seven stamped the edge with `create_point(extractedFrom=[url])`** — the create argument, not a separate `create_edge` call, which the structural verb refuses for this predicate. Two runs (correction, person appointment) reached provenance through `create_edge("wasDerivedFrom"\|"references", …)` instead, and the instrument credits any provenance edge with a Source endpoint — so the fixture's stated measurable ("does the agent find the create argument") is **not** what the class measures |
| **Duplicate identity** | 0 | the one input built to produce a duplicate produced none: the alias was folded onto the existing Object. This is the measurable the document must keep |

### Instrument limitations and corrections, stated rather than hidden

This section exists because **eight rounds of fresh-context review found the
instrument lying about its own subject** — each round also found the sentences
below still quoting the pre-fix causes — and the numbers above are what remains
after every fix. The corrections are recorded, not quietly absorbed: a
measurement artifact that hides its own revisions teaches the wrong thing.

Round 1:

1. **The same-layer preference had no floor** — a same-layer mention scoring
   0.08 beat a different-layer containment match at 0.83, so findings accused
   nodes the agent never wrote the item as. Fixed with a floor and a margin rule.
2. **Pack-declared kinds read as undeclared** — `_vocab()` imported only the
   core §5 sets. The instrument now answers **CORE / PACK / UNDECLARED** from the
   loaded pack registry, and only the third is a finding.
3. **`core:organization` was called "an invented namespace prefix"** — it is the
   *declared* key (`subject_binding.subject_kind_names()`). The subject
   vocabulary is imported now.
4. **`"clean end-to-end"` was true only of the gold items** — `score()` audits
   the nodes the gold names. The graph-wide census makes "no undeclared kind"
   auditable; it does **not** make a run clean (a declared-but-wrong kind or a
   wrong layer beyond the gold is still unaudited — stated so the claim is not
   read wider than it is).
5. **The committed run file was the score output**, so the graph evidence the
   Method describes could not be audited. `score --out DIR` writes `graph.json`
   beside `score.json`.
6. **`expect_mitigation: false` was boilerplate on nine inputs**, turning the
   research input's sanctioned mitigation of an under-evidenced inference into a
   P0. Both operator rules are three-valued: absent = not scored.
7. **`duplicate_expected` was declared and implemented nowhere.** Implemented,
   restricted to entity layers and legal-form suffixes.

Round 2:
8. **"written as a Point" was asserted where the expected layer held the item
   too.** Three P0s (the decision request, the meeting decision, the plan's ship
   date) accused an absence the graph disproved: an Event / an Object existed
   beside the Point. Fixed twice over — a crude stemmer so `join`/`joins` and
   `renew`/`renewal` compare equal, and a **two-homes** verdict (P1) that names
   both nodes instead of claiming the item is absent. The layer count fell from
   12 to 8 and the two-homes class appeared; the totals moved to 13/33.
9. **The census was unpinned and double-counted.** It now skips the nodes a gold
   item already resolved to, and it has its own test — deleting the census used
   to leave 21/21 green.
10. **The document contradicted itself** ("nineteen P0s" beside an 18 headline,
    "four MISSING verdicts" beside three, an enumeration summing to 12 for a
    total of 14). Every number in this section is now recomputed from the
    records and pinned by `test_the_reported_totals_match_the_committed_records`.

Round 3:
11. **The "two homes" bar was a third of the item's stems**, which let the
    meeting *session* Event (`Pricing & self-serve decision meeting`, 2 of 4
    stems) stand in for the kill decision — turning a real absence into a weaker
    finding. The bar is now two thirds, with ties broken toward a node that is
    not superseded (on the product-spec input both plan Objects clear the bar on
    the same two topic stems, only one of them is still live, and the live one is
    the plan the run acted on).
12. **Two rules were still unpinned**: removing the stemmer, or re-adding
    `expect_mitigation: false` boilerplate to the gold, each left every test
    green. Both are pinned now, including one assertion on the GOLD FILE itself.
13. **The report's own arithmetic drifted again** (a 7-input claim over 6 inputs;
    an enumeration that did not add up; a citation of a draft that never
    existed). The class counts are pinned to the records now, not just the
    headline.

Round 4:
14. **One write was charged twice.** `contradicting_claim` carried a P0 for the
    missing NAND *and* a second P0 for the mitigation that replaced it — one act,
    two findings, so the aggregate counted it twice. The dampener is now named
    **inside** the missing-NAND finding (and in the record's `mitigations`), the
    standalone dampener rule fires only when no NAND-less contradiction already
    accounts for the write, and the totals moved 14/32 → **13/32**.
15. **The test that was supposed to catch the instrument's own worst false
    positive could not fail.** Its two nodes tied at 0.80, so list order decided
    the verdict and the whole same-layer preference could be deleted with the
    suite still green. The fixture now makes the wrong-layer node an exact match
    (1.0) against a 0.867 correct-layer one, and the test asserts the raw ranking
    puts the wrong-layer node first — deleting the preference now reds it.
16. **`KIND_PROP` was declared and referenced nowhere.** The record read the kind
    through one `coalesce` across all five kind properties, so it could not say
    WHICH property carried it — and a kind on the wrong axis (`eventKind` on an
    Object) read back as a legal-looking kind. Each layer is now read from its
    own property, a foreign-axis kind is kept as `foreign_kinds`, and it is
    reported as its own **P1 cross-axis** finding (gold-named nodes) or inside
    the graph-wide census. Old `graph.json` files still score identically.

Round 5:
17. **The re-score gate did not cover the case its docstring named.** Two
    *compensated* edits — one record gaining a wrong-but-declared kind while
    another loses one — leave the totals and every class aggregate intact, so the
    report's per-input table could contradict the records with the suite green.
    The per-input counts are pinned now, table row and record together.
18. **Five sentences still quoted pre-fix causes** (the provenance count in the
    outline, the 80% forecast's kind class, a "measurable" the gold never stated,
    the tie-break justification, the round count). Corrected against the records.

Round 6:
19. **The instrument called DECLARED kinds inventions.** `_vocab()` imported only
    the §5 canonical sets, so `option`/`criterion` — which `tortoise.sdk`
    registers with `register_kind` and the hosted write validator accepts — read
    as undeclared, and the census billed them. `kind_status()` now answers a
    fourth status, **LEGACY**, from the product's own predicate
    (`domain_loader.kind_is_known(kind, bucket)`), and the class definition says
    what is actually measured. The totals moved 13/32 → **13/31** and the
    census 8 inputs → 7; `pointKind: issue` ×2 moved from the invented-kind class
    to the wrong-but-declared one.
20. **`-e` plurals read as a different word**, because `es` was stripped before
    the word's own trailing `e`: `values`→`valu` while `value` stayed `value`, so
    a correct write could score under the 0.5 floor and be reported as a P0
    **absent** item. Both sides are now canonicalised to the `e`-dropped stem
    (which also keeps every overlap ratio the same size — an "add the other form"
    fix would have inflated the denominators).
21. **One kind-less write was still charged twice**, and the first finding
    described the opposite act ("declared, but not the kind this item is" about a
    kind nobody wrote). The empty-own-property case now has its own branch
    (`KIND-MISSING`, honest text) and the graph-wide rule skips a node whose
    missing kind was already charged — keyed on the CHARGE, so an unkinded node
    the gold never named is still reported.
22. **The census dropped a node's own undeclared kind when it also carried a
    foreign-axis kind**, inverting the two-acts rule the item level holds. Both
    are reported now.
23. **`_match` had a second definition of "one identity"** (a bare name-prefix
    test), so `Acme` and `Acme Capital` could read as a duplicate — and it
    resolved ties by list order against a query with no `ORDER BY`. Both halves
    now share `_same_identity`, and the sort key breaks ties explicitly (expected
    layer, not superseded, tighter text, text).
24. **The reproduction gate was coupled to the LIVE pack tree**: `packs` was
    compared as part of the whole document, so any PR adding a pack anywhere
    reddened a `core` test on main and forced a rewrite of ten evidence files
    (it already happened once — #9694's `portfolio` pack, every verdict
    byte-identical). `packs` is still recorded as provenance, but the equality
    excludes it and pins its shape separately.
25. **The gold's `duplicate_entity` before-state note disagreed with the only
    fixture that builds it** (`objectKind organization` in the note, `other` in
    `setup.py`). The note is corrected to what the fixture seeds.
26. **The test count in this document was the one number nothing pinned** — it
    said 37 where the file collected 38, and it will drift with every test added.
    The count is gone rather than restated; the command remains.
27. **The registry answer needed its own residue class.** The legacy flat bucket
    is FLAT, so `known_kinds("pointKind")` also accepts kinds that belong to
    another axis (`meeting`, `issue`, `incident`, `workflow`) — trusting it
    wholesale would have blessed "an occurrence filed as a belief", the exact
    failure #9675 quotes, and no committed record would have shown it. A kind
    that only the flat registry accepts AND that another axis declares is now
    `OTHER-AXIS` and is reported by the census with the axis named
    (`correction_existing_claim`'s `pointKind: incident`), so the class survived
    the correction instead of being absorbed by it.
28. **The reproduction gate was still coupled to the LIVE vocabulary** — the
    `packs` exclusion removed pack-list deltas only, so a pack that declared a
    kind a committed graph carries (or a registry change) would still red a
    `core` test on main, with a message accusing the EVIDENCE of drift. The
    remaining coupling is disclosed rather than hidden: every `score.json` now
    records `kind_sources` (the vocabulary it was scored under, per kind it
    used), and the gate checks that map FIRST — a red now says "the product's
    kind vocabulary moved under this record, re-score it", which is the
    actionable fact, instead of the false one. A full freeze of the vocabulary
    per run (so an unrelated vocabulary change cannot red the gate at all) would
    need a committed snapshot artifact with its own pin; that is not this
    issue's scope and is recorded here as the known residual.

**Round 7 — correction 29: an edge item was graded by relation NAME alone.**
The two edge items state their shape in prose ("an ORG edge between Subjects",
"an EDGE to a Subject") and `score()` checked only the relation, so an `ownedBy`
written Point → Point answered `OK`: a false PASS in the number Step 3 reads as
its acceptance delta, on a branch that had no fixture at all. The gold now
declares `endpoints`, the instrument scores the ends as well as the name, and it
REFUSES to grade an edge item that declares neither end — or only one — refusing to answer beats
answering the wrong question. Both committed runs are unchanged (they were wired
correctly, and the class row above is 0). The list numbering above is left as
the source of record: rendering these as one ordered list would need every
prefix to start at 1, which the cross-references to correction numbers rule out.

**Round 8 — correction 30: `owned by` declared only ONE end, so the other was ungraded.**
Correction 29 required an edge item to declare `endpoints` and refused one that
declared neither — but it graded whichever sides were present. `owned by`
declared `to: [Subject]` and no `from`, so `_edge_fits` constrained only `to`
and an `ownedBy` written **Point → Subject** answered `OK`. That is the same
false PASS correction 29 exists to close, one round later and half-closed: the
class row above claimed both ends were graded, and for one of the two edge items
they were not. The gold now declares `from: [Subject, Object, Source]`
(`docs/ONTOLOGY.md` §3.6 — `ownedBy | Entity → Subject`), `_edge_endpoints`
refuses a one-sided declaration instead of half-grading it, and
`test_an_ownedBy_written_FROM_a_Point_is_not_ok` is the fixture for the write it
used to bless. No committed number moves: both runs wired `Object → Subject`,
which is inside `Entity`.

Still true, and stated as a limit rather than a rule: the matcher is token
overlap with a crude stemmer, and **the class table above is the MACHINE
reading**. Two of its verdicts are known to be the matcher's, and they are
itemised here rather than subtracted from the headline:

* meeting, "80% chance we slip a week" — reported **absent**. The run wrote a
  Point ("Priya estimates an 80% probability …"), i.e. the *expected* layer. The
  cause is a synonym: `chance` vs `probability` leaves 3 of 4 stems, and a
  partial overlap scores `0.5 × 3/4 = 0.375`, under the 0.5 acceptance floor.
  Human reading: correct layer, and a **pack-declared** kind (`estimate`, which
  the pm pack declares) where the item is a `statement` — the
  wrong-but-declared class, not the invented-kind class.
* product spec, "v1 said 2026-10-15" — reported **absent**. v1's dates live on
  Points, and the v1 Object was superseded; human reading: the state is on the
  wrong layer, which is the same lesson as the two Points that *were* caught.

The raw verdicts stay in `score.json` untouched.

Two tool-surface defects were hit by the eval agents and **filed as #9696**, not
worked around: `create_event(aboutPoint=[id])` — the **list** form — writes no
edge and raises nothing (the string form writes it), the `about*` props wire
nothing at all outside the Event create path, and `aboutPoint` is refused by
`create_edge` while `TortoiseSDK` exposes no `create_about_edge`. A silent write
failure in the surface this document teaches is a finding in its own right;
fixing it is not this issue's scope.

## The failure list = the document's outline

Derived from the classes above, in the order an agent has to decide.

1. **Layer first, and it is a decision about the ITEM, not the sentence.**
   Eleven verdicts are layer failures: nine where only the wrong layer holds the
   item (ARR and headcount as Points; the goal, the deadline and the estimate;
   the decision; the organisation and the role; the success bar) and two where
   the right layer holds it **and** a second one does (the request, the ship
   date). The second kind is the subtler half: two homes is not "nearly right",
   it is a fact no query can answer, because neither node is authoritative. The
   discriminator the document must teach: *does this hold now (Object), did it
   happen at a time (Event), is it a belief we hold (Point), is it an actor
   (Subject), is it something we read it from (Source)?*
2. **An occurrence is not a belief.** The decision in the transcript became a
   Point with **no Event of its own**, and the "request" in the decision request
   exists as both. Rule: if it happened on a date, it is an Event with an
   `eventKind` — the belief layer records what we *conclude*, not what
   *occurred*; a Point *about* the occurrence is fine only when it is wired to it
   (the appointment input did exactly that: `Point -aboutEvent-> Event`, and no
   finding flags it).
3. **A person, an organisation and a role are Subjects — a role is not an
   Object.** One run wrote the employer and the role as Objects. Rule: `Subject`
   for anything that can act or be acted upon, `Object` only for the thing whose
   state changes.
4. **Kinds come from the declared vocabulary — and the two axes are not one.**
   The most common single failure (14 scored items across 5 of 10 inputs) is a
   kind the product does not know on that layer: `subjectKind: person`,
   `company`, `investor`, `authorGroup`. And the paper got `sourceKind: research`,
   which is the *genre* axis
   (`documentKind`). Rule: pick the kind the layer's vocabulary declares — the §5
   core, a kind `tortoise.sdk` registers (`option`, `criterion`, `evidence`), or a
   pack kind spelled the way the pack declares it — and remember that the legacy
   flat registry accepting a name on a bucket does **not** make it the right axis:
   `meeting` is an Event kind, `issue` and `incident` are Object kinds, and
   `pointKind: incident` is reported as a wrong axis rather than as a declaration.
   Never confuse `sourceKind` (what kind of source) with `documentKind` (what
   genre it is).
5. **Truth attacks the Point; relevance dampens the operator.** The one
   contradicting input was answered by mitigating an `IMPL` and nothing attacked
   the Point. Rule: a claim that is *wrong* gets a `NAND`; a claim that is *less
   relevant* gets `mitigate_operator` (0.10–0.50). A mitigation where a NAND
   belongs leaves the wrong confidence computed, not missing.
6. **Change is supersession with the old claim kept.** The one class the agents
   already got right unaided (both supersession inputs, and the alias fold on the
   duplicate input) — keep the *why* (the before-state stays answerable) and do
   not spend lines re-teaching the mechanics.
7. **Provenance is stamped by the create** (`create_point(extractedFrom=[url])`),
   not by a separate `create_edge` call (the structural edge verb refuses
   `extractedFrom`; `wasDerivedFrom`/`references` are accepted, and one run used
   them alone). The counts: eight runs wrote their own Source, **seven** stamped
   the edge with the create argument, one reached it only through `create_edge`,
   one had its Source pre-seeded by the fixture — and the tenth, the input where
   the claim was being attacked, wrote **no Source at all**. Rule: every claim
   written from something we read gets its source at write time, and a Source
   with no claim→source edge is a scrapbook entry.
8. **The silent-failure warning.** Every failure above succeeded — the call
   returned, the node was created, and nothing disclosed the error. That is the
   sentence the document opens with, and it is the reason the checklist exists.

## What remains

* **Steps 2–3** (the rewrite itself) stay gated behind **#4282 Phase 3**
  (live 81 → 27). Re-running this exact eval — same inputs, same gold — is
  Step 3's acceptance criterion: *the delta per input, not a review verdict*.
* The served mirror is a **derived** artifact; the rewrite is authored upstream
  and pulled with `uv run python tools/served_skills_parity.py --sync`
  (absorbed issue #4358), never edited by hand here.
