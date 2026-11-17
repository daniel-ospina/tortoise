# #12184 — the entity-row ranker, measured on the noun population

**Lane:** `fix/12184-ranker-entity-signals` · **Head at measure:** `8b9355b28574` (the fix commit)
**Instrument:** `tools/candidate_recall_11901.py --entity-rerank` (ARM 6, added by this lane)
**Graph:** run-scoped, written through the real projection (`_upsert_object`); `TORTOISE_DB_URI` from
`tools/test_lane.py uri` (never the ambient one).

## What the two artifacts are

| file | corpus | why both |
|---|---|---|
| `measurement-12184-sess9.json` | the 9 session logs `2026-10-27 … 2026-11-04` | **comparable to the committed baseline**: that is the file set behind `docs/research/2026-11-12-11901-candidate-recall/measurement.json` |
| `measurement-12184.json` | all 22 logs on the box (`… 2026-11-17`) | the current population, for a re-run that moves the corpus |

Both are `hybrid` throughout (`window_modes: {hybrid: N, keyword_only: 0}`), declared per the instrument's
refusal on a mixed run.

## What they report (the falsifier's four fields, separately)

| artifact | k=3 `in_window` | k=3 `miss_past_k` | k=3 `miss_absent_at_max_k` | k=15 `in_window` | k=15 `miss_past_k` |
|---|---|---|---|---|---|
| `…-sess9.json` | 296 / 300 | **4** | **0** | 300 / 300 | 0 |
| `measurement-12184.json` | 294 / 300 | **6** | **0** | 300 / 300 | 0 |

## The past_k case set, and what the entity ranker does to it

`arms.entity_rerank.15.cases` IS the case set the brief asks for: one entry per `past_k` mention, giving the
correct row's identity, its **before** rank, its **after** rank, and — for every row in the pool — the
signals it actually carried (`degree`, `confidence`, `identity_match`, `final_score`). No cap, no head
slice.

| artifact | case set | moved up | gained | **lost** | recall@3 before → after | variant pair top-3 before → after | variant lost |
|---|---|---|---|---|---|---|---|
| `…-sess9.json` | 4 | 4 | 4 | **0** | 296/300 → **300/300** | 438 → **445** (of 446) | 0 |
| `measurement-12184.json` | 6 | 6 | 6 | **0** | 294/300 → **300/300** | 412 → **420** (of 422) | 0 |

`lost: 0` on BOTH populations, exact and variant, is the "no previously-correct top-3 result is displaced"
half of the falsifier. The variant population is the harder control: an identity boost on a WRONG row is
exactly how a reorder would push a variant's canonical out, and it does not (it goes up).

## ⛔ What this measurement does NOT show — read before quoting it

1. **The graph terms are inert here BY CONSTRUCTION.** The eval graph holds one `:Object` per name and no
   claims, so every row answers `degree: 0` / `confidence: null` — `signals_informative: false` and
   `signal_coverage: 0.0` in both artifacts. The movement above is the **identity dimension** (exact
   normalised-name equality) plus the fused relevance. An entity's DEGREE and its claims' mean belief need a
   graph whose entities are *referred to*; that half is measured in
   `tests/test_12184_ranker_entity_signals.py` on a live graph, not here.
2. **A ranker cannot create recall.** The ceiling of `recall_3_after` is `in_window` at the pool width, and
   the arm only reorders — it never adds a row the legs did not return.
3. **The population is a NAME SAMPLE, not a census.** See the instrument's own declared limits.
4. **The `past_k` class is small on this box today (4–6 of 300), and that is a CHANGE SINCE THE BASELINE,
   not a property of the fix.** The committed baseline at the same 9-file corpus measured
   `184/300` in-window at k=3 with `81` absent at any k; the same corpus now measures `296/300` with `0`
   absent. This lane did not touch the retrieval path (its change is `tortoise/ranking.py` plus this
   instrument's opt-in arm), so the delta belongs to the heads in between — recorded here rather than
   attributed, because attributing it needs its own bisect. `legs_seen` differs too (`structural: True`
   now, `False` in the baseline artifact).
