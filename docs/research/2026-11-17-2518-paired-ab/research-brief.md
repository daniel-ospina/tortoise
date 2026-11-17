---
title: "Does the #2518 entity-key expansion help or hurt? A paired A/B on a frozen corpus (#2518)"
type: research
domain: platform
doc_status: draft
created: 2026-11-17
updated: 2026-11-17
subjects.team: epistemic-team
issue: "#2518"
aboutSubjects: C2 entity/fact-augmented key expansion, sparse expansion pass, candidate recall@k, paired IR evaluation
aboutObjects: "#2518, #13118, #12186, #11901, tools/paired_expansion_ab_2518.py"
---

# Does the entity-key expansion help or hurt?

**Issue:** [#2518](https://github.com/daniel-ospina/tortoise/issues/2518) · **Supersedes the reading in**
[#13118](https://github.com/daniel-ospina/tortoise/pull/13118) — not its measurements, its *conclusion* ·
**Instrument:** `tools/paired_expansion_ab_2518.py` · **Artifacts, this directory:**
`paired-point-300.json` (headline), `paired-point-300-short-query.json` (robustness),
`paired-object-60-control.json` (the control that shows the published comparison was blind),
`corpus-snapshot-manifest.json`.

---

## 0. The claim this replaces, and the arithmetic that retires it

`#13118` compared two **independent** eval runs of the expansion and published *"a wash with a regression
in it"*:

| | before (load 124) | after (load 100) |
|---|---|---|
| recall @3 | 75.0 % | 76.3 % |
| recall @15 | 88.0 % | **85.0 %** |
| absent at any k | 28 | 25 |

At 300 per arm the @15 delta is 3.0 pp at SE 2.8 pp (**z = 1.08**), the @3 delta 1.3 pp (**z = 0.37**).
Neither is distinguishable from noise, and the tool now **reproduces the sample sizes those figures
implied** from the formula rather than trusting them: with p̄ ≈ 0.865 and n = 300 an independent design
resolves **7.82 pp**; 5 pp needs **~734 per arm** and 3 pp **~2,034 per arm** (the brief's "~755 / ~2,100").

⛔ **And the comparison was worse than underpowered — it was structurally blind, which this run
demonstrates rather than asserts.** `entity_key_expansion` arms only when `entity_type == "point"`
(`sdk.SDK.search_graph`: both halves sit behind `entity_key_expansion and entity_type == "point"`), and
`tools/candidate_recall_11901.py` measures the **OBJECT** leg. Measured here on the same frozen corpus,
same items, same box, back to back (`paired-object-60-control.json`, n = 60):

| object arm, k | OFF hits | ON hits | discordant | identical ranked lists |
|---|---|---|---|---|
| 3 / 5 / 10 / 15 | **60/60** | **60/60** | b = 0, c = 0 | **60 of 60 items, element-wise** |

So between those two published runs there was **no code difference to measure** on that instrument: the
expansion's two switch states are one code path there. Whatever produced 75.0 → 76.3 and 88.0 → 85.0 was
corpus drift, machine load and sampling noise. The tool flags the arm `structurally_blind: true` and
**refuses** to publish its null as a result (exit 3, artifact still written).

---

## 1. Research — pass 1: the internal canon

Read in full: `docs/research/2026-11-12-11901-candidate-recall/research-brief.md` (§2 "the corpus is
LIVE, and the two runs did NOT sample the identical population"; §2.1 the mode pairing; §2.2 the
`o.name`-on-create gap), `docs/research/2026-11-17-2518-entity-augmented-queries/research-brief.md`,
`#13118`'s PR body and its five artifacts, `#12186`'s A4 row, `tortoise/sdk.py`'s
`_entity_key_alias_harvest` / `_entity_key_expansion_pass` / `_entity_key_dense_pass`,
`tests/test_entity_key_expansion.py` (the probe technique `#13121` established: import the code, drive it,
record `leg_trace` — not read a diff).

**What the harness samples and what it records** (`tools/candidate_recall_11901.py`): names harvested from
`~/.tortoise/session-events` by three declared rules (definite descriptions, identifiers, paths) into a
1,000-name population, a stratified 300-name sample, seeded through the real projection as `:Object`
nodes, then read by the exact leg `_fts_rows` runs. It records `retrieval.window_modes` per window and a
population digest **only in the new artifact schema** — the two committed artifacts predate both.

**What makes two runs incomparable, from that canon and confirmed here:**

1. **The corpus is live and NOT append-only in effect.** `#11901` measured a re-harvest of the same
   directory returning **−10.8 % to −11.0 %** fewer distinct names ~2 h later. Measured today, the same
   directory holds **23 files / 14,225 lines**, against the **14 files** `#13118` recorded on this box —
   the corpus grew, which is the benign direction and still a different condition.
2. **The sample is redrawn per run.** `#13118` recorded the population digest differing in *all five* of
   its runs (`2323ddf7…` / `7c08a430…` / `eec5fcf1…` / `81c6ff3d…` / `cd275d71…`) — "a count identity is
   not a member identity" — and that pairing was impossible because *"pairing needs the sampled population
   written out (`--population-file`), which this harness does not do"*. **That file is this tool's job.**
3. **Load moves the dense leg.** `#13108` measured 45 % of eval windows losing the dense leg under load
   (`window_modes {hybrid: 2371, keyword_only: 1973}`), so two runs at load 124 and load 100 are two
   different retrieval systems.

---

## 2. Research — pass 2: best practice, and the test this run uses

**Search tool:** rung 1 (`mcp__seo-intelligence__perplexity_search`) is **not on this agent's surface**
(no MCP tool), rung 2 (`web_search`) is not exposed. The credentialed Perplexity API was called directly,
model named explicitly on every request (`"model": "sonar"`; never `sonar-deep-research` /
`sonar-reasoning-pro`). **4 queries attempted / 4 returned.**

| finding | source | what it decides here |
|---|---|---|
| **Paired comparison removes between-topic difficulty.** Var(paired) = (σ²ₓ+σ²ᵧ−2ρσₓσᵧ)/n vs Var(unpaired) = (σ²ₓ+σ²ᵧ)/n ⇒ variance reduction **1−ρ**, effective sample-size multiplier **1/(1−ρ)** | Perplexity (sonar), corroborated by standard paired-design theory | Why this lane is paired: the pairing unit is the ITEM, and the reduction is **measured** (`correlation.rho`) rather than assumed |
| **The McNemar power equation is implicit in δ**: √n·\|δ\| = z₁₋α/₂·√p_D + z₁₋β·√(p_D−δ²), with the small-effect form δ ≈ (z₁₋α/₂+z₁₋β)√(p_D/n); **p_D = P(discordant)**; for <~10 discordant pairs prefer EXACT McNemar over the normal approximation | Perplexity (sonar), citing PASS/Stata power procedures and McNemar references | This is the MDE the tool computes, solved by fixed point; and it is why the **exact** test is used, not χ² |
| **Reproducible IR evaluation = a content-addressed snapshot**: a manifest of document ids and content hashes, the index/analyzer configuration, a versioned query set **with the sampling seed**, and per-run seeds — *"same query IDs + same document manifest + same index state + same configuration = comparable rerun"*; a live corpus changes corpus-wide statistics (document frequency), so a re-run differs by **dataset drift** | Perplexity (sonar), IR reproducibility guidance | Exactly the snapshot + `--population-file` + recorded seed design below, adopted **because** it contradicts no recorded decision (this is measurement only) |

**The test, and why.** Per item, two binary outcomes (target found / not found at k), same items both arms
⇒ **McNemar's exact two-sided test** on the discordant pairs (b = miss→hit, c = hit→miss). Exact, not
χ², because the discordant totals here are 5–17. Beside it: a **seeded paired bootstrap** (2,000 resamples,
same seed as the draw) for a CI on the recall delta, the **measured** outcome correlation, and the **MDE for
the n actually used** — stated before any verdict.

**Contradiction test.** Measurement only: no retrieval behaviour, window, index or expansion parameter is
changed; `entity_key_expansion` keeps `False`, `TORTOISE_ENTITY_KEY_DENSE` keeps OFF (the tool **refuses**
if it is armed, because that is a second conflated lever). No MCP/SDK surface change. Nothing here
contradicts the 2026-11-10 KEEP ruling (`#11395`), `SEARCH-ARCHITECTURE.md` V1, or §14.3 V3.

---

## 3. Method

1. **Freeze the corpus.** `~/.tortoise/session-events` → a fixed path, with `MANIFEST.json`
   recording every file's name, bytes, line count and sha256 plus an aggregate digest.
   **Digest `18ab6174e440f7b3…`**, 23 files, 14,225 lines, 78,571,361 bytes. Every arm reads the snapshot.
   A re-freeze **verifies**; a modified snapshot is **refused**, never re-copied over.
2. **Sample ONCE.** Population 1,000 → stratified sample 300 (60/30/10 class quotas, largest-remainder
   shares), drawn with a recorded seed (`20261117`) and written to `--population-file`. Both arms read that
   file. A snapshot-digest or names-digest mismatch is **refused** — that is the "same items" falsifier.
3. **The item.** Per name: a QUERY turn (a session that names it) and a TARGET record in a **different
   session that does not contain the name at all** (median query-token overlap **0.0**), linked
   `aboutObject` to the anchor with `search_keys` — `#2518`'s stated class verbatim: *a same-subject point
   from a session the one-shot top-k starves, whose own E3 keys are its only join surface*
   (`tests/test_entity_key_expansion.py` proves this shape). 500 real corpus records are seeded as
   competition; 300 anchors + 600 linked points + 500 distractors = 1,100 points.
4. **One read per item per arm** at `--read-limit`, so every k is a **prefix** of one ranked list and the
   arms cannot differ by read width.
5. **Both arms in one process, back to back**: load 10.36 → 10.27 (Δ −0.09). `window_modes` recorded per
   arm; a mixed mode is refused as contamination.

⚠️ **`window_modes`: `keyword_only` 300/300 in BOTH arms**, uniform. This box has no embedder
(`EmbeddingModel.get() → None`), so the dense leg never ran — the measured mechanism is the **sparse half**
only, which is the shipped default (`TORTOISE_ENTITY_KEY_DENSE` OFF). Not contaminated; also not a hybrid
measurement, and it is not reported as one.

---

## 4. Result — the headline (n = 300, frozen corpus, one sample, two arms)

**`paired-point-300.json`** · arm `point` (the leg the expansion arms) · 300 items, ids identical across
arms (`ids_match_all_arms: true`, `id_digest 4d6899d3d0b62543`) · expansion demonstrably fired on **293 of
300** items · repeat of the OFF side returned the **identical** outcome for every item (determinism check).

| k | OFF hits | ON hits | b (miss→hit) | c (hit→miss) | McNemar exact p | paired Δ | 95 % bootstrap CI | **MDE at this n** |
|---|---|---|---|---|---|---|---|---|
| **3** | **0/300** | **5/300** | 5 | 0 | 0.0625 | **+1.67 pp** | [+0.33, +3.33] | **2.08 pp** |
| **5** | 0/300 | 6/300 | 6 | 0 | **0.0313** | +2.00 pp | [+0.67, +3.67] | 2.28 pp |
| 10 | 0/300 | 13/300 | 13 | 0 | **0.00024** | +4.33 pp | [+2.33, +6.67] | 3.35 pp |
| **15** | 0/300 | **17/300** | 17 | 0 | **0.00002** | **+5.67 pp** | [+3.33, +8.33] | 3.84 pp |

**Reading it.**

- **On its own target class the expansion helps, and the pairing detects it.** At k = 15 it recovers 17 of
  300 targets that plain sparse retrieval cannot reach at all, p = 2 × 10⁻⁵.
- ⛔ **OFF is 0/300 at every k BY CONSTRUCTION** — the target was selected to be lexically unreachable.
  This is therefore **class-conditional recall, not corpus recall**: it says the mechanism does the job it
  was designed for on the class it targets, and says nothing about the product's recall on real questions.
  `#11901` declared the same residual for its generated variant arm.
- **The k = 3 delta is BELOW this n's MDE.** Observed +1.67 pp against a 2.08 pp minimum detectable effect
  ⇒ **no winner at k = 3 is claimable**, and the tool says so rather than reporting the point estimate.
- **MDE stated before the verdict, per k**: 2.08 / 2.28 / 3.35 / 3.84 pp. At n = 300 this run **can**
  resolve a 3 pp effect at k ≥ 10 and **cannot** at k = 3.

### 4.1 The harm side — measured, and one arm where it is measurable

The merged pass is a **re-rank**, not an append: `_entity_key_expansion_pass` writes the expanded run
first and lets pass-1-only ids follow, so a correct pass-1 hit can be **demoted** (the docstring says so;
it was unmeasured before).

| measure (n = 300) | k = 3 | k = 15 |
|---|---|---|
| OFF top-k ids **displaced** from the ON window | **727 / 900 (80.8 %)** | **3,350 / 4,500 (74.4 %)** |
| items whose OFF top-1 survived in the ON window | 68 / 300 | 126 / 300 |
| **seed** (the query's own point — the one labelled id) lost from the window | 0 | 0 |
| seed demoted but kept | 0 | 0 |

⚠️ **The seed rows are 0/0 here for a measured reason, not because nothing moved:** with full-turn
queries over this corpus (an agent transcript corpus whose records are near-duplicates of each other), the
seed does not enter a 30-wide window in **either** arm (`seed_at_top1` 0 → 0), so the one labelled harm
signal is **unmeasurable at this scale**. The displacement counts above that are labelled "churn" are
exactly that — **churn, not harm**: the OFF top-k here is mostly unrelated competition, and there are no
qrels for it.

**So the robustness arm exists to make harm measurable** (`paired-point-300-short-query.json`, query capped
at 200 chars, same names, same snapshot, read window 60, n = 300):

| k | OFF | ON | b | c | p | Δ | MDE | seed lost from window | seed demoted |
|---|---|---|---|---|---|---|---|---|---|
| 3 | 0/300 | 2/300 | 2 | 0 | 0.50 | +0.67 pp | 1.32 pp | 1 | 3 |
| 10 | 0/300 | 5/300 | 5 | 0 | 0.0625 | +1.67 pp | 2.08 pp | 2 | 2 |
| **15** | 0/300 | **5/300** | 5 | 0 | 0.0625 | **+1.67 pp** | 2.08 pp | **3** | **2** |

**Why this is the result worth keeping.** With a short query the gain is smaller (**+1.67 pp**, not
significant) and the harm becomes visible: **5 of 300 items (1.7 %) had their own best match moved — 3 out
of the window entirely** — which is the **same order** as the 1.67 pp gained. On this arm **the lever's cost
and its benefit are indistinguishable in size**, and neither is significant at n = 300.

---

## 5. Falsifiers — each with its output

| falsifier | verdict | output |
|---|---|---|
| **the corpus snapshot exists with its digest and file list; both arms read it** | **MET** | `corpus-snapshot-manifest.json`: digest `18ab6174e440f7b38dbd49b00b519409cedaacef58647f2cf29532b825f325d0`, **23 files**, 14,225 lines, 78,571,361 bytes, per-file sha256/bytes/lines. `verify_snapshot` re-derives and **refuses** on any byte change (pinned by `test_a_tampered_snapshot_is_refused_never_renamed`). |
| **the same sampled items are used in both arms** | **MET** | One `--population-file` (`population-300.json`, seed 20261117, names digest recorded); artifact `items.id_digest = 4d6899d3d0b62543`, `items.with_item = 300`, `ids_match_all_arms: true`. A population drawn against another snapshot is **refused** (pinned). |
| **the result is paired, with the test named and its p-value** | **MET** | McNemar exact two-sided per k: **p = 0.0625** (@3), **0.0313** (@5), **0.00024** (@10), **0.00002** (@15); paired bootstrap CI (2,000 resamples, seed 20261117) reported beside each. |
| **the detectable effect size for n is stated before the conclusion** | **MET** | MDE @ n=300: **2.08 / 2.28 / 3.35 / 3.84 pp** for k = 3/5/10/15. The k=3 point estimate (+1.67 pp) is **below** it and is **not** reported as a win. The published comparison's independent MDE at p̄=0.865 is **7.82 pp** ⇒ ~734/arm for 5 pp and ~2,034/arm for 3 pp — the brief's ~755/~2,100, reproduced from the formula. |
| **`window_modes` recorded per arm; any contamination named** | **MET** | `keyword_only` 300/300 in both arms (no embedder on this box) ⇒ uniform, no contamination, and **not** reported as a hybrid measurement. `load 10.36 → 10.27` (Δ −0.09) across both arms in one process; `repeat` of the OFF side returned identical outcomes for all 300 items. |
| **⛔ no retrieval behaviour changed** | **MET** | `git diff --stat origin/main...HEAD` = `tools/paired_expansion_ab_2518.py`, `tests/test_paired_expansion_ab_2518.py`, `MEMORY.md`. No change to `tortoise/`, no index, no `TOOL_REGISTRY`, no public SDK method; the tool **refuses** when `TORTOISE_ENTITY_KEY_DENSE` is armed. |

---

## 6. What this does NOT claim

- **Not a production recall figure.** The item class is constructed to be unreachable without the
  expansion, so `OFF = 0/300` is a property of the construction. The number is **class-conditional**.
- **Not a blind-expansion ceiling.** The target's `search_keys` are its own salient tokens, which is the
  shipped harvest's own shape (the pool is the linked points' keys) — so the lift is the mechanism's
  behaviour **under a clean key set**, not under production's noisy E3 keys.
- **Not a dense-leg measurement.** No embedder on this box ⇒ `keyword_only`; the dense half
  (`TORTOISE_ENTITY_KEY_DENSE`) is OFF by default and refused if armed.
- **Not a proof of no harm.** Harm is measurable only in the short-query arm, where it is the same order
  as the benefit. In the headline arm the one labelled harm signal does not enter the window at all.
- **Not a general "pairing needs fewer samples" demonstration.** Measured ρ is **undefined** here (the OFF
  arm is at floor, zero variance), so the pairing bought **no** variance reduction in this design — its
  value was that the **exact** test is the only valid test at 5–17 discordant pairs.
- **No LongMemEval number**, no production duplicate rate, no phase-2 verdict.

---

## 7. The decision the evidence supports

1. **The published before/after comparison is retired, not reinterpreted.** On the object-leg instrument
   the lever's two switch states are one code path (60/60 items identical): that comparison measured
   corpus drift and load, and no n would have fixed it.
2. **On the leg the lever arms, it helps on its own class** (+5.67 pp @15, p = 2 × 10⁻⁵) and **its cost is
   a re-rank of the same size** (+1.67 pp gain against 1.7 % of items losing their own best match in the
   short-query arm). Neither the benefit nor the cost is resolvable at k = 3 with n = 300.
3. **Do not arm it on this evidence.** `entity_key_expansion` stays caller-gated `False`; the honest
   summary is *"the mechanism works on its class and its displacement cost is unquantified at production
   scale"*, not *"it helps"*. A decision to arm it needs the harm side measured on items whose relevance
   is known — which needs qrels this corpus does not have.
4. **The next measurement, if this is picked up:** run the same paired design over
   `tools/longmem_eval/` (the only production-shaped surface that reaches the point leg, per `#13118`
   decision 2), where the gold answers exist and the harm side becomes measurable on real questions.

---

## 8. Reproduce

```bash
uri="$(uv run python tools/test_lane.py uri)" || exit 1
eval "$uri"
uv run python tools/paired_expansion_ab_2518.py \
    --sessions-dir ~/.tortoise/session-events \
    --freeze /tmp/snap-2518 \
    --population 1000 --sample 300 --distractors 500 \
    --read-limit 30 --seed 20261117 --arms point --repeat-arm point \
    --out /tmp/point-300.json
# the control (a different sample size is enough — its claim is structural):
uv run python tools/paired_expansion_ab_2518.py --snapshot /tmp/snap-2518 \
    --population 1000 --sample 60 --distractors 500 --read-limit 30 --arms object \
    --out /tmp/object-60.json        # exits 3: it REFUSES to publish the blind null
```

`tests/test_paired_expansion_ab_2518.py` pins the statistics to hand-computed tails and to the brief's own
sample-size arithmetic, the snapshot/population refusals, the item construction, and one live end-to-end
run in which the point arm reaches the lever and the object arm provably cannot — **18 passed**.
