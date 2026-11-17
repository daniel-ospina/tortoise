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
resolves **7.82 pp**; 5 pp needs **~734 per arm** and 3 pp **~2,037 per arm** (an earlier estimate of
"~755 / ~2,100" — quoted, not derived, and published nowhere this repo can point to).

⛔ **And the comparison was worse than underpowered — it was structurally blind, which the call site
fixes and this control reproduces as a gate, not as a measured effect.** `entity_key_expansion` arms only
when `entity_type == "point"` (`TortoiseSDK.tortoise_fts_query` in `tortoise/sdk.py`: both halves sit
behind `entity_key_expansion and entity_type == "point"`), and `tools/candidate_recall_11901.py` measures the
**OBJECT** leg. The control below ran the object arm on the same frozen corpus and the same box, back to
back (`paired-object-60-control.json`, n = 60) — over an **independent draw** from the same 1,000-name
population, not the headline 300 (`items.name_digest` `1776aaa9…`, against the point arms' `06373b44…`):
its claim is structural, so the items need not be the 300. Its two switch states are one code path, so
the tool flags the arm `structurally_blind: true` and **refuses** to publish its null as a result (exit 3,
artifact still written).

⛔ **What that control does NOT carry, and this brief previously said it did.** Its
`expansion_identical_items: 60` was derived from `(ids or []) == (ids or [])` while `read_arm` recorded
ranked lists for the **point** arm only: both sides were `None`, so the "identical ranked list in both
switch states" was `None == None` for every item — a constant no retrieval behaviour can move, published
as a measurement (`#13267` F1). The identity is TRUE, and it is a **call-site fact**
(`entity_key_expansion and entity_type == "point"`, both halves of
`TortoiseSDK.tortoise_fts_query` — cited by predicate and symbol, never by `file:line`, which had already
drifted when this was re-reviewed, `#13267` F3), not something that artifact measured. The **committed**
artifact carries `identity_measured: false` with those counts `null` rather than a number — a post-hoc
annotation on a legacy run, not something a re-run would emit: the tool now emits `identity_measured`
itself and records each item's ranked list on **both** sides (with a per-side digest in the artifact), so
a re-run *measures* the identity (and reports `identity_measured: true`) instead of asserting it. Its
minimum detectable
effect is reported as **not estimable** (`nan`, with a `mde_note`) rather than `0` — an all-agree run
carries no information about the difference, and an MDE of 0 beside a null reads as "infinitely powered",
which is the opposite of what it means.

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
   directory returning **−10.8 % to −11.0 %** fewer distinct names ~2 h later. At the measurement the same
   directory held **23 files / 14,225 lines**, against the **14 files** `#13118` recorded on this box —
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
   competition; 300 anchors (the query points) **+ 300 targets = the 600 points that carry the**
   `aboutObject` link, plus 500 distractors = 1,100 points. ⚠️ `anchors` is a SUB-count of
   `linked_points`, not a third group — `seed_graph` sets `anchors = len(items)` and
   `linked_points = 2 * len(items)` and merges the link onto both ids per item, so adding the two
   double-counts the anchors (it would read 1,400, against the artifact's `points_total: 1100`).
4. **One read per item per arm** at `--read-limit`, so every k is a **prefix** of one ranked list and the
   arms cannot differ by read width.
5. **Both arms in one process, back to back**: the headline run is bracketed by load **13.09 → 12.55**
   (Δ −0.54) and the object control by **11.25 → 11.25** (Δ −0.01). `window_modes` recorded per arm; a
   mixed mode is refused as contamination. ⛔ The figures are the artifacts' own `environment` block — a
   re-run moves them, so they are quoted from the committed evidence and must be re-read when it moves
   (`#13267` F3: an earlier revision quoted a trace that appears in NO committed artifact — the
   numbers had been transposed in from another run — and this row is the reason the figures are now read
   out of the artifact rather than remembered).

⛔ **The robustness arm (see §4.1) moves TWO knobs, and this brief previously named one.** It is
`--query-chars 200` **and** `--read-limit 60`, against the headline's default query cap (500) and
`--read-limit 30`. An earlier revision of this section and of §4.1 attributed the whole difference to the
query cap; the committed artifacts could not settle it either, because `construction` recorded
`read_limit`, `distractors` and `keys_source` but **not** `query_chars` (`#13267` F5). Both are the
treatment: the honest statement is *"a shorter query **in a wider read window** makes harm measurable"*,
and the tool now records `construction.query_chars`/`key_tokens`/`min_query_tokens` so the next run's
artifact alone says what it ran.

⚠️ **`window_modes`: `keyword_only` 300/300 in BOTH arms**, uniform. This box has no embedder
(`EmbeddingModel.get() → None`), so the dense leg never ran — the measured mechanism is the **sparse half**
only, which is the shipped default (`TORTOISE_ENTITY_KEY_DENSE` OFF). Not contaminated; also not a hybrid
measurement, and it is not reported as one.

---

## 4. Result — the headline (n = 300, frozen corpus, one sample, two arms)

**`paired-point-300.json`** · arm `point` (the leg the expansion arms) · 300 items, ONE list read by both
arms in one process (`items.with_item = 300`) · **the discriminating digest is `items.name_digest`
`06373b442bc93182…`, which equals `population.names_sha256`** — the paired evidence is that both arms come
from one drawn sample, and that digest is what pins WHICH names it was. ⛔ `items.id_digest`
`4d6899d3d0b62543…` is **not** pairing evidence and was cited as if it were: it digests the synthetic
`item-NNNNNN` ids every draw assigns, so it is the constant for ANY 300-item sample (`9eeb9c4f…` for the
60-item control) — `#13267` review 2, finding 1. Expansion demonstrably fired on **293 of 300** items;
repeat of the OFF side returned the **identical** outcome for every item (determinism check).

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
  ⇒ **no winner at k = 3 is claimable**. ⚠️ The TOOL does not decide that — `_print_summary` prints the
  point estimate (`delta=`) beside both recalls, the CI and both MDEs, and emits no winner verdict at all;
  the MDE rule is this brief's, and it is applied to every row below rather than to k = 3 alone.
- **MDE stated before the verdict, per k**: 2.08 / 2.28 / 3.35 / 3.84 pp — and it **rises with k**,
  because the discordant count rises with k (b = 5 → 6 → 13 → 17), so this design is *most* powered at
  low k. At n = 300 the run **can** resolve a 3 pp effect at k = 3 and k = 5 (MDE 2.08 / 2.28 pp) and
  **cannot** at k = 10 or k = 15 (MDE 3.35 / 3.84 pp).
- **The winner rule, applied to every row.** A winner is claimable only where the *observed* delta clears
  its **own** MDE: k = 10 (4.33 > 3.35) and k = 15 (5.67 > 3.84) do; k = 3 (1.67 < 2.08) does **not**, and
  **neither does k = 5 (2.00 < 2.28)** — even though its exact p is 0.0313 and is bolded above. The
  p-value and the MDE answer different questions; the rule this brief declares is the MDE one, and it is
  applied to all four rows rather than to k = 3 alone.

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

⚠️ **The seed rows are 0/0, and these artifacts do not settle WHY.** `seed_at_top1` (0 → 0) is a
**top-1** count: it says no item's seed was rank 1 in either arm, and it says **nothing** about ranks
1–29. The artifact records no per-item `seed_rank` at all (`per_item` carries `off_rank`/`on_rank` plus the
**two provenance counts**, `provenance_off`/`provenance_on` — four keys, none of them a seed rank), so its
four zeroes (`seed_at_top1` 0 → 0, `seed_lost_from_window` 0, `seed_demoted_but_kept` 0,
`seed_rank_shift_sum` 0) are equally consistent with *"the seed never entered the 30-wide window"* and
with *"it entered at an unchanged rank in both arms"*. The one labelled harm signal is therefore
**unrecorded at this scale — not measured absent**, and this brief does not claim to have measured its
absence (the short-query arm below runs a 60-wide window, where the seed **does** move and the signal
becomes recordable). The displacement counts above that are labelled "churn" are exactly that — **churn,
not harm**: the OFF top-k here is mostly unrelated competition, and there are no qrels for it.

**So the robustness arm exists to make harm measurable** (`paired-point-300-short-query.json`, query capped
at 200 chars **and** read window 60 — see the §3 note: both knobs differ from the headline, so "once the
query is short" is shorthand for that pair of changes, not a single-variable attribution), same names,
same snapshot, n = 300):

| k | OFF | ON | b | c | p | Δ | MDE | seed lost from window | seed demoted |
|---|---|---|---|---|---|---|---|---|---|
| 3 | 0/300 | 2/300 | 2 | 0 | 0.50 | +0.67 pp | 1.32 pp | 1 | 3 |
| 10 | 0/300 | 5/300 | 5 | 0 | 0.0625 | +1.67 pp | 2.08 pp | 2 | 2 |
| **15** | 0/300 | **5/300** | 5 | 0 | 0.0625 | **+1.67 pp** | 2.08 pp | **3** | **2** |

**Why this is the result worth keeping.** With a short query the gain is smaller (**+1.67 pp**, not
significant) and the harm becomes visible: **5 of 300 items (1.7 %) had their own best match moved — 3 out
of the window entirely** — which is the **same order** as the 1.67 pp gained. On this arm **the lever's cost
and its benefit are indistinguishable in size**, and **neither side can be separated from noise at
n = 300** — the gain's exact McNemar p is 0.0625, and the 3 seeds lost are a bare count (1 % of items)
with no interval and no test attached. ⚠️ The 5 is `seed_lost_from_window` 3 **plus**
`seed_demoted_but_kept` 2, and "best match" is shorthand: the artifact records the seed as never rank 1
(`seed_at_top1` 0 → 0), so it is its own *best recorded* match, not a proven top-1.

---

## 5. Falsifiers — each with its output

| falsifier | verdict | output |
|---|---|---|
| **the corpus snapshot exists with its digest and file list; both arms read it** | **MET** | `corpus-snapshot-manifest.json`: digest `18ab6174e440f7b38dbd49b00b519409cedaacef58647f2cf29532b825f325d0`, **23 files**, 14,225 lines, 78,571,361 bytes, per-file sha256/bytes/lines. `verify_snapshot` re-derives and **refuses** on any byte change (pinned by `test_a_tampered_snapshot_is_refused_never_renamed`). |
| **the same sampled items are used in both arms** | **MET** (structural + digest) | One `--population-file` (`population-300.json`, seed 20261117) is read by both arms in ONE process — the pairing is a property of that construction, not a predicate to assert. The discriminating record is **`items.name_digest = 06373b442bc93182…`, equal to `population.names_sha256`** in both point artifacts. ⛔ `items.id_digest` (`4d6899d3…`) is a function of the sample SIZE and settles nothing — the `ids_match_all_arms` predicate this row used to cite was `True` for every reachable input (`#13267` F6) and is now a `pairing` block that says so. A population drawn against another snapshot is **refused** (pinned). |
| **the result is paired, with the test named and its p-value** | **MET** | McNemar exact two-sided per k: **p = 0.0625** (@3), **0.0313** (@5), **0.00024** (@10), **0.00002** (@15); paired bootstrap CI (2,000 resamples, seed 20261117) reported beside each. |
| **the detectable effect size for n is stated before the conclusion** | **MET** | MDE @ n=300: **2.08 / 2.28 / 3.35 / 3.84 pp** for k = 3/5/10/15 — the MDE **rises** with k. The winner rule is the MDE one applied to every row: k = 10 (+4.33 against 3.35) and k = 15 (+5.67 against 3.84) clear their own MDE; k = 3 (+1.67 < 2.08) and k = 5 (+2.00 < 2.28, p = 0.0313) do **not**, and neither is reported as a win. The published comparison's independent MDE at p̄=0.865 is **7.82 pp** ⇒ ~734/arm for 5 pp and **~2,037/arm** for 3 pp, which replaces an earlier estimate of ~755/~2,100 (quoted
rather than derived, and published nowhere this repo can point to). |
| **`window_modes` recorded per arm; any contamination named** | **MET** | `keyword_only` 300/300 in both arms (no embedder on this box) ⇒ uniform, no contamination, and **not** reported as a hybrid measurement. `load 13.09 → 12.55` (Δ −0.54) across both arms in one process — the artifact's own `environment` block, not a transcription (`#13267` F3: an earlier revision quoted a trace that appears in NO committed artifact, and the load moves between runs, so a trace is only ever valid for the artifact it came from — the pin in `tests/test_paired_expansion_ab_2518.py` compares this number against the artifact); `repeat` of the OFF side returned identical outcomes for all 300 items. |
| **⛔ no retrieval behaviour changed** | **MET** | `git diff --name-only origin/main...HEAD` touches exactly these paths: `tools/paired_expansion_ab_2518.py`, `tests/test_paired_expansion_ab_2518.py`, `MEMORY.md`, `config/ci-surfaces.yml`, `docs/00_index.md`, and the brief plus its four artifacts under `docs/research/2026-11-17-2518-paired-ab/`. No change to `tortoise/`, no index, no `TOOL_REGISTRY`, no public SDK method; the tool **refuses** when `TORTOISE_ENTITY_KEY_DENSE` is armed. ⛔ The **`git diff --stat` insertion/deletion tally is deliberately not quoted**, and neither is a total file count: both move whenever the evidence itself is committed and whenever `main` is merged in, so a quoted tally is stale on the very next commit while the touched-path set is not — the previous revision of this cell quoted one, and the head already prints a different number. The **path set is the falsifier**, because that is what "no retrieval behaviour changed" means, and it is reproducible with the command at the head of this cell; a tally that cannot be reproduced is not a falsifier. (`#13267` F3, three generations on: a 3-file list the tree does not produce, then a `--stat` tally that went stale on the next commit, now the set itself — the pin in `tests/test_paired_expansion_ab_2518.py` keeps this cell to the path set.) |

---

## 6. What this does NOT claim

- **Not a production recall figure.** The item class is constructed to be unreachable without the
  expansion, so `OFF = 0/300` is a property of the construction. The number is **class-conditional**.
- **Not a blind-expansion ceiling.** The target's `search_keys` are its own salient tokens, which is the
  shipped harvest's own shape (the pool is the linked points' keys) — so the lift is the mechanism's
  behaviour **under a clean key set**, not under production's noisy E3 keys.
- **Not a dense-leg measurement.** No embedder on this box ⇒ `keyword_only`; the dense half
  (`TORTOISE_ENTITY_KEY_DENSE`) is OFF by default and refused if armed.
- **Not a proof of no harm.** Harm is measured only in the short-query arm, where it is the same order
  as the benefit. In the headline arm the one labelled harm signal is **unrecorded** at that scale — see
  §4.1: the artifacts record no per-item seed rank, so "does not enter the window" is not a claim this
  evidence makes.
- **Not a general "pairing needs fewer samples" demonstration.** Measured ρ is **undefined** here (the OFF
  arm is at floor, zero variance), so the pairing bought **no** variance reduction in this design — its
  value was that the **exact** test is the only valid test at 5–17 discordant pairs.
- **No LongMemEval number**, no production duplicate rate, no phase-2 verdict.

---

## 7. The decision the evidence supports

1. **The published before/after comparison is retired, not reinterpreted.** On the object-leg instrument
   the lever's two switch states are one code path (the call-site citation above: the object leg never
   reaches `entity_key_expansion and entity_type == "point"`), so that comparison measured corpus drift
   and load, and no n would have fixed it. ⛔ The retired identical-`count` is **not** evidence here —
   it was `None == None` (`#13267` F1, and this restatement was `#13267` F4).
2. **On the leg the lever arms, it helps on its own class** (+5.67 pp @15, p = 2 × 10⁻⁵) and **its cost is
   a re-rank of the same size** (+1.67 pp gain against 5 of 300 items whose seed moved in the short-query
   arm — 3 out of the k-window, 2 demoted but kept). **In the headline arm** the k = 15 benefit is
   resolvable while the cost is not; **in the short-query arm neither is** (1.67 pp against a 2.08 pp MDE).
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
# the robustness arm — ⛔ TWO knobs differ from the headline run (query cap AND
# read window), and that pair is the treatment (see §3):
uv run python tools/paired_expansion_ab_2518.py --snapshot /tmp/snap-2518 \
    --population 1000 --sample 300 --distractors 500 \
    --read-limit 60 --query-chars 200 --seed 20261117 --arms point \
    --repeat-arm point \
    --out /tmp/point-300-short-query.json
# the control (a different sample size is enough — its claim is structural):
uv run python tools/paired_expansion_ab_2518.py --snapshot /tmp/snap-2518 \
    --population 1000 --sample 60 --distractors 500 --read-limit 30 --arms object \
    --repeat-arm object --out /tmp/object-60.json   # `--repeat-arm` defaults to POINT and never equals
                                    # `object`, so omitting it emits `repeat_identical: null` and does
                                    # NOT reproduce this artifact (`repeat_identical: true`)
```

⛔ **A re-run is not byte-reproducible from this repo alone.** The frozen snapshot lived only in a
box-local `/tmp` and the live corpus has since drifted (23 files at the measurement, 14 before it), and the
drawn `population-300.json` was never committed — so `items.name_digest` can be checked for CONSISTENCY
(it equals `population.names_sha256`) but the names behind it cannot be re-derived here. The artifacts say
so rather than implying otherwise.

⛔ **And two fields in these three files are ANNOTATIONS, not run output.** The control's
`identity_measured: false` + its `identity_note`, and all three files'
`construction_unrecorded: {fields: [query_chars, key_tokens, min_query_tokens]}` (their runs predate those
fields) were added by hand beside the run. They mean "this run did not record/measure it", which is a fact
about THESE FILES — no version of the tool emitted them (`#13267` review 5, F5). The tool emits
`identity_measured` itself now, and the suite's artifact-shape helper holds for a tool-produced artifact
as well as these three, so a correct re-generation is not punished by the pins that document the older
shape.

The tool records, in the artifact's own `provenance` block, the sha256 of its own bytes and whether those
bytes were committed (`tool_sha256` / `tool_uncommitted`) — because `git_head` alone does **not** identify
the producing code (`#13267` F4): these three artifacts record `9b537d875a` while carrying `mde_note` and a
`nan` `mde_paired`, which only `5898385f2e`'s code emits (that fix was uncommitted when the run happened),
and the earlier generation recorded `44a1b82` while carrying fields first committed in `9b537d8`.

`tests/test_paired_expansion_ab_2518.py` pins the statistics to hand-computed tails and to the brief's own
sample-size arithmetic, the snapshot/population refusals, the item construction, the refusal CLASSES (so
`--allow-mixed-modes` cannot excuse a blind or inert run), the ON-vs-OFF delta being a comparison of
recorded ranked lists rather than `None == None`, **this brief's own citations against the committed
artifacts**, and one live end-to-end run in which the point arm reaches the lever and the object arm
provably cannot. ⛔ The case count moves with every review round — run the file (or
`tools/ci_selection.py --integrity` for the registration) rather than trusting a number quoted here.
