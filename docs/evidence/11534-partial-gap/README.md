---
title: "Evidence — #11534 the partial-gap detector, scored on the F3 population"
type: evidence
domain: platform
doc_status: final
created: 2026-11-08
subjects.team: epistemic-team
aboutSubjects: Tortoise extraction pipeline, S2.2 coverage
aboutObjects: "#11534, #11002, #2335, #5087, run_s4, EXTRACTOR-V4-ARCHITECTURE.md §S2.2"
---

# `#11534` — the partial-gap detector, scored on the F3 population

**What this record is.** The detector `tortoise/vet_gate.py::partial_gaps` adds
answers one question `check_batch` could not: *which* narrative span does this
batch not account for? `coverage_suspect` fires only on a zero-candidate batch
(`bool(statements and total == 0)`), so a narrative that yielded 44 candidates
and left one statement uncovered was invisible to every instrument in the tree
— and an ADD-capable successor to `run_s4` cannot be built on an instrument
that can only count.

It reports. It never acts. The **action** is a separate, flag-gated stage
(`extractor_v2.run_partial_gap_fill`, S2.2b-ADD, `TORTOISE_PARTIAL_GAP_FILL`,
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
`coverage_floor = 0.55` cuts a two-mode distribution — over the 7 sessions (one
run; the detector is deterministic and the S2 list is the same object in both
artifacts, so the second run only doubles every count) **147 of the 210 segments
that clear the token floor sit at exactly 1.0** and **35 fall below 0.55**;
median **1.0**, p10 **0.355**, p25 **0.736**. The floor's defensible
justification is that mass/tail split. Its *placement* is corroborated, not
uniquely determined: in 0.1-wide bands `[0.5, 0.6)` holds 4 segments and is the
**local minimum of the three bands straddling the floor** (6 · 4 · 14), but it is
not the thinnest band in the distribution at all — `[0.2, 0.3)` holds 3 and
`[0.8, 0.9)` holds 4 — and one of its four values (0.586) is above the floor, so
0.55 cuts three of the four. An earlier draft called it "the thinnest band" and
that was false on this very population (review round 4). Those figures are re-derived by
`test_the_coverage_floor_is_justified_by_the_measured_distribution`, because an
earlier draft of this paragraph carried pre-correction percentiles that nothing
pinned (round 2's finding); `min_content_tokens = 4` drops `**bold label**` and
date-fragment segments, which are the class it removes below it. Measured, on the
seven recordings: 68 of the 212 sub-floor segments carry a bold label (32.1%) — a
plurality, not a dominant class — and headings are masked out *before* segmentation,
so they can never be a flag class at all (0 of the 103 flags at
`min_content_tokens=1` begin with `#`). Both figures are re-derived by
`test_the_content_floor_s_removal_class_is_measured_not_asserted`, because an earlier
draft of this very sentence restated them from a review comment and the numbers did
not reproduce.

**Stemming is symmetric on purpose, and the first version was not.** Coverage
compares *base forms*, so the stemmer must fold both sides of a pair: an
asymmetric rule (strip a suffix only when the remainder is long enough) leaves
``share``/``shares``, ``stage``/``stages``, ``state``/``stated`` unequal, which
**lowers** coverage — the direction that invents a gap. Measured before the fix:
2 of the 70 flagged spans were that class. The shipped stemmer normalises
``-ies``→``-y`` and drops a trailing ``e`` from both sides; the residual
asymmetry (``analysis``/``analyses``) can only add a flag, never hide one.

**What the flagged spans actually are, read end to end** — the classes below
enumerate **14 of the primary run's 35** flagged spans; the other 21 are NOT
classified here, so read this as a worklist of what the flags ARE, never as a
precision figure (the labels above cannot produce one):

* **genuine clause-level elisions** — wp03 "This directly attacks the
  ownership-lookup failure that drives the unactionable-page rate", "This
  mitigates the obvious weakness of an ownership-based router (services with no
  owner row would otherwise fall through…", "as opposed to merely routing noise
  better"; wp05 "Aisha pushed the team to check the config diff instead", "the
  config-diff visibility action is directly supported by the investigation's
  failure mode"; wp07 "leaving a window where two workers could claim one
  batch", "per-service flags let each service's flag be pulled independently".
  Every one of these is a claim the batch does not carry, and S4 independently
  added items that reach some of them — **read §3's `reached` column for the
  count, not this bullet**: the bullets are a worklist of what the flags ARE, and
  a numeral restated here would be a number nothing re-checks (it was, until
  review round 8: the sentence said "four of them", while the primary run's
  additions reach three of the seven spans listed above — wp03 ×2, wp05 ×1 — with
  the fourth DISTINCT span reached landing on wp07's lease span, which this list
  does not classify).
* **non-claims that pass the lexical check** — wp05 "A retro room is booked for
  Friday morning…", wp07 "The on-call handoff was smooth with no pages
  overnight", wp03 "(The on-call room has been quiet lately…", wp02 "the team had
  signed off on the sketch the previous Friday (2026-10-02)". These are the
  reason the ADD stage stays OFF together with the unmeasured yield (§4): a model
  handed them must be free to answer "no claim here", and the prompt says so.
* **structural fragments** — a bold label split by the sentence splitter
  (`**The problem state that triggered the redesign`), wp04's two flags are both
  this class. Named precisely, the splitter's fragment classes are two and
  neither is the data's: (a) a terminator immediately followed by MARKDOWN
  (`.** The`), because `narrative_segments` breaks on every `.`/`;` while
  `_SENTENCE_SPLIT` breaks only where whitespace or end-of-string follows, and
  (b) a DECIMAL POINT (`hit 4.2 second` splits into `… hit 4` / `2 second`).
  Both are why `flagged[].statement` is an ADDRESS and never comparable with
  `narrative_statements`; they are disclosed as a detector limitation, not
  repaired here, because repairing them moves every number in this record.

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

**Four `points` are missed** (the totals above are 8 of 12). Below are examples of
the two MISS mechanisms, not an inventory of the class: `wp01` — the addition "The
hydrate stage shares the same code as the wire stage…" carries 8 content tokens, and
the clause it paraphrases ("it shares the same code") only 2, so its best overlap is
2 of 8 = 0.25, under the record's 0.30 rule. The miss is the **overlap threshold**,
not `GAP_MIN_CONTENT_TOKENS`: at `min_content_tokens=1` the clause is flagged and
recall is unchanged at 9 of 21; `wp05` "The Bluepeak retro is published company-wide"
restates content a covered statement already carries, so the item is a re-shaping
of it, which is the generalisation class the paragraph above defines as
unreachable by construction. ⚠️ The four is arithmetic off the pinned totals; how
many are named here is deliberately NOT stated as a number, because a typed count
in this record has gone stale every time one was written.

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
  is knowable (one call per session that flags anything — **7 of 7** sessions in
  this population; §2's per-session counts are `1 · 10 · 8 · 2 · 5 · 1 · 8`, so
  every session pays, not six of seven — round 4 corrected the earlier undercount) but its benefit depends on whether a model, handed a flagged span,
  emits the right item, which needs live sessions and a key. That is why the
  stage ships armed OFF and why the default is the thing a future measurement
  has to beat.
* **The stage is silent on the zero-candidate population, and that is now
  RECORDED rather than implied.** `TORTOISE_PARTIAL_GAP_FILL` is armed only when
  a story and an S2 list exist, so `#11534`'s target (a *partial* gap) excludes
  the empty-S2 case — which is exactly the session `run_s4` rescues
  (`s4_full_rescues`, `#2335`). The stage records `armed: true, skipped:
  "empty embed_list"` there, so a measurement run can tell "armed and skipped"
  from "not armed" (#9116's ambiguity), and the exclusion is visible instead of
  being read as "the stage found nothing". The predicate is
  `_gap_fill_sees_candidates` — the DETECTOR's own count
  (`check_batch(embed_list, "")["candidates"]`), which walks `vet_gate.SECTIONS`
  (THREE sections; `operators` is not one of them) — not the mapping, and not
  `_batch_has_candidates`. That helper walks four sections (it is S4's
  `s4_full_rescues` predicate) and on an operator-only batch it answers True
  where the detector counts 0, which is exactly the divergence this predicate
  closes. An armed `route_mentions` pass writes `mention_stats` onto that same
  mapping, and a truthiness test would have read such a batch as non-empty and
  fired a billed call at the population this bullet says is excluded (all three
  forms pinned, armed, by
  `test_the_skip_also_fires_when_the_mention_channel_wrote_bookkeeping` and
  `test_an_operator_only_batch_is_skipped_with_no_model_call`).
* **The clause split is crude by design.** `,`/`:`/`—`/`but`/`so`/`though`/
  `while`/`which`/`because`/`and` — a claim split differently than that is
  judged as a larger or smaller unit, and a two-token elision is below the
  floor. Stated, not hidden: the address is `(statement, clause)` + a char span,
  and both are stable only for the narrative they were computed on.
