# `#11534` — the partial-gap detector, scored on the F3 population

**What this record is.** The detector `tortoise/vet_gate.py::partial_gaps` adds
answers one question `check_batch` could not: *which* narrative span does this
batch not account for? `coverage_suspect` fires only on a zero-candidate batch
(`bool(statements and total == 0)`), so a narrative that yielded 44 candidates
and left one statement uncovered was invisible to every instrument in the tree
— and an ADD-capable successor to `run_s4` cannot be built on an instrument
that can only count.

It reports. It never acts. The **action** is a separate, flag-gated stage
(`extractor_v2.run_partial_gap_fill`, S2.2b, `TORTOISE_PARTIAL_GAP_FILL`,
**default OFF**), and this record exists because that stage's value depends
entirely on how good the detector is — a stage that "fills" a gap that was not
a gap adds cost and noise.

**Reproduce:**

```bash
TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_11534_partial_gap.py -q
```

The pins that carry the numbers below are
`test_the_detector_s_flag_rate_on_the_population`,
`test_the_detector_s_recall_against_what_s4_actually_added` and
`test_the_recorded_population_is_still_the_one_we_measured`.

---

## 1. What was measured, and on what

| | |
|---|---|
| **population** | the 7 recorded sessions of the F3 corpus — the same population `#11002`'s F3 falsifier was measured on |
| **narratives + S2 lists** | `tests/eval/write_path/receipts/7947-arms-2026-10-18/*.arms.json` (`stories.narrative`, `embed_list.narrative`) |
| **labels** | S4's own `added_items`, `legacy` arm (`TORTOISE_S4_DELTA=0`), from both committed runs: `ab-20261107T023958Z.json` and `ab-20261107T024226Z.json` |
| **runs scored** | 7 sessions × 2 recorded runs = 14 |

The `legacy` arm is the shipped-pre-retry arm, which is what keeps F3's "no
retry fired" clause true by construction (`#11002`'s comment on the issue
records the same restriction). Both runs are scored because the population's own
record says they differ: wp01 and wp07 add in one run and not the other.

**An overlap is a hit when ≥30% of the ADDITION's content tokens appear in a
flagged span.** That is deliberately generous to the detector — an addition is a
paraphrase of the span, not a copy of it — so a miss is a real miss.

## 2. The detector's coverage of the narrative (the false-positive direction)

| measure | value |
|---|---|
| clause segments scored | **844** |
| segments flagged | **70** |
| flag rate | **8.3%** |
| per session (both runs agree — the detector is deterministic) | wp01 1 · wp02 10 · wp03 8 · wp04 2 · wp05 5 · wp06 1 · wp07 8 |

Tuning, and why these values: coverage is **idf-weighted** (`log((N+1)/(df+0.5))`
over the batch's own item texts) so a word the batch uses everywhere ("stage",
"lease") is near-worthless as evidence and a word it never uses scores highest;
`coverage_floor = 0.55` sits in the trough of a bimodal distribution (per-segment
coverage: median 0.85, p10 0.40); `min_content_tokens = 4` drops markdown
headings and bold labels, which are the population's dominant flag class below
it.

**What the flagged spans actually are, read end to end** (all 35 from the
primary run) — this is the honest precision picture, because the labels above
cannot produce a precision number:

* **genuine clause-level elisions** — wp03 "This directly attacks the
  ownership-lookup failure that drives the unactionable-page rate", "This
  mitigates the obvious weakness of an ownership-based router (services with no
  owner row would otherwise fall through…", "as opposed to merely routing noise
  better"; wp05 "Aisha pushed the team to check the config diff instead", "the
  config-diff visibility action is directly supported by the investigation's
  failure mode"; wp07 "leaving a window where two workers could claim one
  batch", "per-service flags let each service's flag be pulled independently".
  Every one of these is a claim the batch does not carry, and S4 independently
  added an item for four of them.
* **non-claims that pass the lexical check** — wp05 "A retro room is booked for
  Friday morning…", wp07 "The on-call handoff was smooth with no pages
  overnight", wp03 "(The on-call room has been quiet lately…", wp02 "the team had
  signed off on the sketch the previous Friday (2026-10-02)". These are the
  reason the ADD stage is armed OFF: a model handed them must be free to answer
  "no claim here", and the stage's prompt says so explicitly.
* **structural fragments** — a bold label split by the sentence splitter
  (`**The problem state that triggered the redesign`), wp04's two flags are both
  this class.

## 3. The detector's recall against what S4 actually added (the finding)

| run | session | clauses | flagged | S4 additions | reached |
|---|---|---|---|---|---|
| `…023958Z` | wp01_quarry_debug | 76 | 1 | 3 | 0 |
| `…023958Z` | wp02_lumen_refactor | 73 | 10 | 0 | — |
| `…023958Z` | wp03_ember_design | 61 | 8 | 3 | 2 |
| `…023958Z` | wp04_aurora_perf | 52 | 2 | 0 | — |
| `…023958Z` | wp05_retro_writeup | 75 | 5 | 4 | 2 |
| `…023958Z` | wp06_quarry_rollout | 35 | 1 | 0 | — |
| `…023958Z` | wp07_bluepeak_followup | 50 | 8 | 2 | 1 |
| `…024226Z` | wp01_quarry_debug | 76 | 1 | 0 | — |
| `…024226Z` | wp02_lumen_refactor | 73 | 10 | 0 | — |
| `…024226Z` | wp03_ember_design | 61 | 8 | 3 | 2 |
| `…024226Z` | wp04_aurora_perf | 52 | 2 | 3 | 0 |
| `…024226Z` | wp05_retro_writeup | 75 | 5 | 3 | 2 |
| `…024226Z` | wp06_quarry_rollout | 35 | 1 | 0 | — |
| `…024226Z` | wp07_bluepeak_followup | 50 | 8 | 0 | — |

**Totals: 9 of 21 additions reached — 8 of 12 `points`, 1 of 9 `operators`.**

### Why the operator half is not a tuning problem

Read the missed ones. Every missed operator is a **generalisation of a claim the
batch already carries**:

* wp01 — "a retry path that does not re-establish ownership before processing
  silently defeats any downstream completion check; ownership must be claimed
  before work" — generalised from a point the session already has ("The retry
  loop added in the batch runner bypassed the batch ownership check…").
* wp03 — "a service with no owner row falls to the default rotation and is
  flagged in the weekly ownership report" — from a *fallback decision* the batch
  covers.
* wp04 — "the cache entry for a team is invalidated whenever a write lands" —
  from a remediation bullet the batch covers.

No narrative span is uncovered in those cases, because the content IS covered;
what is new is the item's **abstraction level** (a rule derived from an
observation). A coverage detector cannot reach that by construction, and neither
can a stage triggered by coverage.

The two missed `points` are the same shape (`wp01` "The hydrate stage shares the
same code as the wire stage…" — the clause is only 2 content tokens, below the
floor; `wp05` "The Bluepeak retro is published company-wide" — the content is in
a covered statement, the item is a re-shaping of it).

### What this means for `run_s4`

`#11534` asks whether the ADD stage "recovers the items S4 adds in the 5
sessions where S4's contribution is non-zero". **Measured: it recovers the
coverage half (8/12 points) and not the generalisation half (1/9 operators).**
So:

* the removal precondition in `EXTRACTOR-V4-ARCHITECTURE.md` §S2.2's drift marker
  — *"the pre-condition for removal is a stage that can ADD"* — is **met for
  ADD-from-an-uncovered-span** and **not met for ADD-by-generalisation**;
* the removal therefore stays gated (`#11534` target 3), and the next step is a
  stage that can ADD at a **different abstraction level**, not a better coverage
  detector;
* the architecture record now states both halves instead of the universal *"no
  successor stage can emit a candidate a coverage check found missing"*, which
  this work falsifies.

## 4. Bounds — what this record does NOT establish

* **Precision is not measured, and cannot be, from these labels.** S4's own
  additions are the only ground truth in the tree, and S4 did not find every gap
  either; the flag rate plus the read-through in §2 is what is offered instead,
  and §2 lists the non-claim classes rather than hiding them in the rate.
* **The ADD stage's yield on real sessions is not measured.** Its measured cost
  is knowable (one call per session that flags anything — 6 of 7 sessions in this
  population) but its benefit depends on whether a model, handed a flagged span,
  emits the right item, which needs live sessions and a key. That is why the
  stage ships armed OFF and why the default is the thing a future measurement
  has to beat.
* **The clause split is crude by design.** `,`/`:`/`—`/`but`/`so`/`though`/
  `while`/`which`/`because`/`and` — a claim split differently than that is
  judged as a larger or smaller unit, and a two-token elision is below the
  floor. Stated, not hidden: the address is `(statement, clause)` + a char span,
  and both are stable only for the narrative they were computed on.
