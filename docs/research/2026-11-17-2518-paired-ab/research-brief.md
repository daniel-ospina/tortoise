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

The re-measurement posted on `#2518` after `#13118` merged — [owner comment,
2026-11-17T05:35:36Z](https://github.com/daniel-ospina/tortoise/issues/2518#issuecomment-6939661408) —
compared two **independent** eval runs of the expansion and called the result *"a wash with a regression
in it"*. ⛔ `#13118`'s own body is NOT the source of the after-half: it prints the pre-baseline
(`28 / 88 % / 75 %`, which it reports as **not reproducible on its box**) and carries none of
76.3 / 85.0, none of the phrase, and none of the two standard errors below:

| | before (load 124) | after (load 100) |
|---|---|---|
| recall @3 | 75.0 % | 76.3 % |
| recall @15 | 88.0 % | **85.0 %** |
| absent at any k | 28 | 25 |

At 300 per arm the @15 delta is 3.0 pp at SE 2.8 pp (**z = 1.08**), and the @3 delta 1.3 pp at SE 3.5 pp
(**z = 0.37**) — a WIDER standard error, which is why the @3 move is the less resolvable of the two (the two SEs
differ because they are computed at different k, not because one is a typo).
Neither is distinguishable from noise, and the tool now **reproduces the sample sizes those figures
implied** from the formula rather than trusting them: with p̄ ≈ 0.865 and n = 300 an independent design
resolves **7.82 pp**; 5 pp needs **~733 per arm** and 3 pp **~2,037 per arm** (an earlier estimate of
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
effect is reported as **not estimable** (`null`, with a `mde_note`) rather than `0` — an all-agree run
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
   not a member identity". The reason a paired re-run was impossible is **`#11901`**'s finding, not
   `#13118`'s: *"pairing needs the sampled population written out (`--population-file`), which this
   harness does not do"*. **That file is this tool's job.**
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
the n actually used** — **reported beside** the exact test, at every k, and **not** pre-stated: π is this
run's own discordant share, so it does not exist until both arms have been read (`#13267` review 4 F3, which
found "stated before any verdict" contradicted by the tool's own *"discordance is measured, not assumed"*).
What is fixed in advance is `n`; the MDE says what that `n` could resolve at 80 % power, so a delta **below**
it is an observation the design was not powered to resolve, and no such `k` is called a winner.

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
   (Δ −0.54) and the object control by **11.25 → 11.25** (Δ −0.01 — the 2-dp bracket hides the
   movement its own recorded delta shows). `window_modes` recorded per arm; a
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
60-item control) — `#13267` review 2, finding 1. Expansion demonstrably fired on **293 of 300** items.
⛔ **There is NO determinism result for the committed runs, and an earlier revision of this sentence said
there was** (`#13267` review 4 F2): the `repeat` arm was compared on each item's **target rank**, which is
null on both sides for every point-arm item because the target is selected to be lexically unreachable —
`None == None` 300 times, a constant. Both point artifacts now carry `repeat_identical: null` and a
`repeat_check` block saying what was actually compared; the tool compares per-side **ranked-list digests**
so a re-run measures the read, not the target's presence.

| k | OFF hits | ON hits | b (miss→hit) | c (hit→miss) | McNemar exact p | paired Δ | 95 % bootstrap CI | **MDE at this n** |
|---|---|---|---|---|---|---|---|---|
| **3** | **0/300** | **5/300** | 5 | 0 | 0.0625 | **+1.67 pp** | [+0.33, +3.33] | **2.08 pp** |
| **5** | 0/300 | 6/300 | 6 | 0 | 0.0313 | +2.00 pp | [+0.67, +3.67] | 2.28 pp |
| 10 | 0/300 | 13/300 | 13 | 0 | **0.00024** | +4.33 pp | [+2.33, +6.67] | 3.35 pp |
| **15** | 0/300 | **17/300** | 17 | 0 | **0.00002** | **+5.67 pp** | [+3.33, +8.33] | 3.84 pp |

⛔ **Three rows' bootstrap intervals exclude zero while their OWN exact test does not, and the exact test
governs (`#13267` reviews 10 F1 and 11 F1–F3).** The affected cells are **the headline's k = 3** and **the
short-query arm's k = 10 and k = 15**. In every one of them `b = 5`, `c = 0`, `n = 300`, so the percentile
bootstrap's 2.5th percentile is **one** discordant item — 1/300 = 0.33 pp — and the interval excludes zero
**by that discreteness, not by evidence**: `P(a 300-item resample contains zero discordant items) =
(1 − 5/300)³⁰⁰ = 0.0065`, which is below 0.025, so the resamples that show no gain at all are cut off
below the 2.5th percentile. The interval is therefore ANTI-CONSERVATIVE exactly where the exact test is
conservative, and by duality an interval excluding 0 and a two-sided exact p of 0.0625 cannot both be right
about the same test — so **those three cells are NOT separated from the null by the exact test, and their
intervals must not be read as separating them.**
⛔ **The headline's k = 5 is NOT one of these cells**: its exact p is 0.0313, which does separate it — it is
excluded from the win rows by the **MDE** rule (`+2.00` against `2.28 pp`), a different falsifier, and the
2.5th percentile there is two discordant items, not one. And the headline's k = 10 and k = 15 clear **both**
their interval and their exact test; that agreement is why the win is claimed there and nowhere else.

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
  the MDE rule is this brief's.
- **MDE at the n used, reported per k BESIDE the exact test — and NOT pre-stated**: 2.08 / 2.28 / 3.35 /
  3.84 pp. ⛔ π is this run's own discordant share, which does not exist until both arms have been read, so
  the figure cannot have been fixed before the outcome; only `n` is fixed in advance (`#13267` review 4 F3).
  The MDE **rises with k**, because the discordant count rises with k (b = 5 → 6 → 13 → 17), so this design
  is *most* powered at low k: at n = 300 the run **can** resolve a 3 pp effect at k = 3 and k = 5 (MDE
  2.08 / 2.28 pp) and **cannot** at k = 10 or k = 15 (MDE 3.35 / 3.84 pp).
- **The winner rule, applied to every row.** A winner is claimable only where the *observed* delta clears
  its **own** MDE: k = 10 (4.33 > 3.35) and k = 15 (5.67 > 3.84) do; k = 3 (1.67 < 2.08) does **not**, and
  neither does k = 5 (2.00 < 2.28). Those two rows are therefore also the only two whose **p-values** are
  printed in **bold** above — the **p** cell's bold means "this row's delta clears its own MDE", not
  "p < 0.05", so a reader cannot mistake a low-power observation for a resolved effect (`#13267` review 4
  F3: the rule was invoked at k = 3 alone and k = 5's p was bolded with no caveat). ⚠️ **Scope:** only the
  **p** column is mechanical. The k = 3 row also bolds its delta and its MDE — the row that does **not**
  clear — and the k column is bold for 3/5/15 but not 10; that emphasis is historical and carries no
  meaning, so it must not be read as the convention.

### 4.1 The harm side — measured, and one arm where it is measurable

The merged pass is a **re-rank**, not an append: `_entity_key_expansion_pass` writes the expanded run
first and lets pass-1-only ids follow, so a correct pass-1 hit can be **demoted** (the docstring says so;
it was unmeasured before).

| measure (n = 300) | k = 3 | k = 15 |
|---|---|---|
| OFF top-k ids **displaced from the ON top-k** (`off_ids[:k]` absent from `on_ids[:k]`) | **727 / 900 (80.8 %)** | **3,350 / 4,500 (74.4 %)** |
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
of the k-window, 2 demoted but kept** — which is the **same order** as the 1.67 pp gained. ⚠️ "out of the
k-window" is `seed_lost_from_window`'s own definition (`off_rank < k` and `on_rank` null or `>= k`), not
"gone from the read": a seed that fell to rank 20 of the 60-wide read is counted. On this arm **the lever's cost
and its benefit are indistinguishable in size**, and **neither side is separated from the null at
n = 300 by the exact test** — the gain's exact McNemar p is 0.0625 (its bootstrap interval excludes zero,
and ⛔ that interval is the anti-conservative one described at the §3 table: with 5 of 300 discordant and every one of them
miss→hit, the percentile lower bound is one discordant item; the exact test governs, so this arm's gain is
**not** established), and the 3 seeds lost are a bare count (1 % of items)
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
| **the detectable effect size for the n used is reported beside every k, and no k whose delta is below it is called a winner** | **MET** | MDE @ n=300: **2.08 / 2.28 / 3.35 / 3.84 pp** for k = 3/5/10/15 — the MDE **rises** with k, each figure beside its own exact p. The winner rule is the MDE one applied to every row: k = 10 (+4.33 against 3.35) and k = 15 (+5.67 against 3.84) clear their own MDE; k = 3 (+1.67 < 2.08) and k = 5 (+2.00 < 2.28, p = 0.0313) do **not**, and neither is reported as a win. ⛔ **The MDE is reported, not pre-stated** (`#13267` review 4 F3): π is this run's own discordant share, so the figure cannot exist before both arms are read — only `n` is fixed in advance. The published comparison's independent MDE at p̄=0.865 is **7.82 pp** ⇒ ~733/arm for 5 pp and **~2,037/arm** for 3 pp, DERIVED from the formula — replacing an earlier estimate of ~755/~2,100 (quoted rather than derived, and published nowhere this repo can point to). |
| **`window_modes` recorded per arm; any contamination named** | **MET** | `keyword_only` 300/300 in both arms (no embedder on this box) ⇒ uniform, no contamination, and **not** reported as a hybrid measurement. `load 13.09 → 12.55` (Δ −0.54) across both arms in one process — the artifact's own `environment` block, not a transcription (`#13267` F3: an earlier revision quoted a trace that appears in NO committed artifact, and the load moves between runs, so a trace is only ever valid for the artifact it came from — the pin in `tests/test_paired_expansion_ab_2518.py` compares this number against the artifact **at every site it is quoted**, `#13267` review 4 F5). ⛔ **No `repeat` determinism result is claimed for these runs** (review 4 F2): the `repeat` arm was compared on the target's **rank**, which is null on both sides for every point-arm item, so the point artifacts carry `repeat_identical: null` and a `repeat_check` block naming the basis (`target_rank`) and the zero items actually compared. The tool now compares ranked-list digests, so a re-run measures the read. |
| **⛔ no retrieval behaviour changed** | **MET** | `git diff --name-only origin/main...HEAD` touches exactly these paths: `tools/paired_expansion_ab_2518.py`, `tests/test_paired_expansion_ab_2518.py`, and the brief and its artifacts under `docs/research/2026-11-17-2518-paired-ab/` — plus the repairs of main's own reds this branch carries, which are unrelated to the expansion lever and named here only because the set must match what that command prints: `tools/capstone_state_value.py`, `tools/dense_leg_probe_13108.py`, `tools/measure_13436_write_costs.py`, `tests/test_m3_fts_leg_recall_12298.py`, `tests/test_rerank_pool.py`, `tests/test_window_quality_m2.py`, `docs/architecture/CYPHER-INVENTORY.md`, `docs/architecture/SEARCH-ARCHITECTURE-CHANGELOG.md`, `docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md`, `docs/research/2026-11-18-13420-vector-placement/dossier.md`. Every one of them is wrong on origin/main, and what this branch does to each is repair it (or a comment in the same file) — with the exceptions stated rather than rounded up. One is the dossier's cite of STORAGE-PICTURE-2026-11-17.md line 158 is stale on origin/main too (the row it quotes sits at line 166 there), yet nothing reds on it — no test names that dossier or that cite, and the one guard that reads such cites reds only where a cite lands on whitespace, which main's line 158 (a blockquote sentence) is not — so re-pointing it is a drive-by re-derivation this branch carries, not a repaired red. The other exception is a path this branch ADDS without repairing any red on it: `tests/test_window_quality_m2.py` is GREEN on origin/main, and what this branch does there is add a pin — the mirror's lever-flag set asserted against extractor_v2._fts_rows, a contract the file's own intentional_keep row cited in prose while nothing checked it — so it is an improvement named here so the set matches, and it is deliberately NOT counted among the repairs. None of them is on the retrieval path — no path under tortoise/ — so none can move the two arms or the order they are compared in. No change to `tortoise/`, no index, no `TOOL_REGISTRY`, no public SDK method; the tool **refuses** when `TORTOISE_ENTITY_KEY_DENSE` is armed. ⛔ **`#13267` itself landed by SQUASH** (merge commit `f462ad3acb`), which is why the three paths it carried that this branch no longer changes — `MEMORY.md`, `config/ci-surfaces.yml`, `docs/00_index.md` — are named here in prose and not in the enumeration above: their content is in `main`, so the command at the head of this cell does not print them, and a cell that listed them would over-claim the diff it quotes. The enumeration is therefore exactly what that command printed at the head this record was written at — ⛔ not at any later head: the command was run on the AUTHORING branch, and at any subsequent head it prints a different set (measured at `#13746`'s head: that command prints 16 paths and this cell covers **two** of them — `tests/test_paired_expansion_ab_2518.py` by name and this brief through the directory token; the other fourteen are files that branch repairs and this cell never describes — eleven under `docs/epics/`/`docs/evidence/`, plus `docs/00_index.md`, `docs/architecture/ENTITY-VERDICT-BANDS.md` and `tests/test_7949_capstone_measure.py`) — and it still carries no path under `tortoise/` — which is the falsifier's whole claim. ⛔ The **`git diff --stat` insertion/deletion tally is deliberately not quoted**, and neither is a total file count: both move whenever the evidence itself is committed and whenever `main` is merged in, so a quoted tally is stale on the very next commit while the touched-path set is not — the previous revision of this cell quoted one, and the head already prints a different number. The **path set is the falsifier**, because that is what "no retrieval behaviour changed" means, and it is reproducible with the command at the head of this cell; a tally that cannot be reproduced is not a falsifier. (`#13267` F3, three generations on: a 3-file list the tree does not produce, then a `--stat` tally that went stale on the next commit, now the set itself. ⛔ **`#13267` review 4 F6:** an earlier revision of this sentence asserted a set-level test pin here and no such pin existed — what the suite enforced was only that the cell NAME the reproducing command and avoid the tally vocabulary. The pin now checks the enumerated set in BOTH directions against this lane's **recorded** enumeration — frozen, reds on any path under `tortoise/`, and needs no git (so it is armed on the suite job's shallow checkout too) — because a diff-keyed pin failed BOTH ways (measured, `#13572` rounds 1–3): it SKIPPED on every branch touching neither the brief nor the probe — dead code on exactly the heads where this cell cannot have gone stale — and FALSE-REDDED the heads that do touch them (`extra` = 12 armed by the brief alone, 11 by the probe alone or by both, since the probe is itself one of the twelve), which is exactly the lane maintaining this record. No diff-keyed trigger is right here. ⚠️ And what the pin IS, since a frozen reference invites the wrong reading: a **lock on the record**, not a re-derivation of it — it makes an edit to this cell visible in a second place, and it cannot reproduce the authoring diff's file list because that diff is merged and the tree has moved. The live numbers are additionally re-read by a reviewer at each head.) |

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
   resolvable while the cost is **unrecorded** there (§4.1); **in the short-query arm neither is**
   (1.67 pp against a 2.08 pp MDE).
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
                                    # `object`, so omitting it leaves the repeat unimplemented — and a
                                    # re-run is NOT comparable to this artifact on that field anyway (see
                                    # the annotation note below: the committed `repeat_identical` is `null`)
```

⛔ **A re-run is not byte-reproducible from this repo alone.** The frozen snapshot lived only in a
box-local `/tmp` and the live corpus has since drifted (23 files at the measurement, 14 before it), and the
drawn `population-300.json` was never committed — so `items.name_digest` can be checked for CONSISTENCY
(it equals `population.names_sha256`) but the names behind it cannot be re-derived here. The artifacts say
so rather than implying otherwise.

⛔ **And SIX field groups in these three files are ANNOTATIONS, not run output** (this count has been
wrong three times — `#13267` reviews 6 F6 and 7 F2 — so each one is listed, and they are counted at
the end rather than guessed at the start). The control's
`identity_measured: false` + its `identity_note`, **and a hand-added clause inside its `why`** —
"⛔ and this particular run did not MEASURE that identity (see `identity_note`); it is a call-site fact" —
which no version of the tool emits (`_build_artifact` builds `why` from the measured counts and stops at
"not a null effect"), so it is a hand annotation that no count of FIELD GROUPS can see — which is why it
is named here rather than folded into the total; the two POINT files' `ranked_lists_compared_note`, which
*asserts* "fired/identical was a real element-wise comparison" and which **no version of the tool
emits** — the other hand-added field that described a measurement rather than its absence; all three files'
`construction_unrecorded: {fields: [query_chars, key_tokens, min_query_tokens]}` (their runs predate those
fields); all three files' `repeat_identical: null` + `repeat_check` block; and all three files'
`provenance` block — whose `tool_sha256: null` cannot be emitted by any version (`_tool_provenance`
returns `None` only on `OSError`) and whose `tool_uncommitted: true` is a claim about a run-time working
tree that nothing in these files measures — were added by hand beside the run. They mean "this run did not
record/measure it", which is a fact about THESE FILES — no version of the tool emitted them (`#13267`
review 5, F5). The `repeat_check` case is the sharpest: their `repeat` arm compared the target's **rank**,
which is null on both sides for every point-arm item, so the `true` those files used to carry was
`None == None` — a constant. The annotation records the basis (`target_rank`), the zero items actually
compared, and that it is not a determinism check (`#13267` review 4 F2). ⛔ The CONTROL is a
different case and its own block says so: its rank is present on all 60 items in both reads, so it
records `compared: 60` and a real 60-of-60 equality of target PRESENCE — still not a determinism
check of the read, but **not** zero items compared (`#13267` review 5 F5). The tool emits
`identity_measured` and `repeat_check` itself now, compares ranked-list **digests**, and the suite's
artifact-shape helper holds for a tool-produced artifact as well as these three, so a correct
re-generation is not punished by the pins that document the older shape.

⛔ **The sixth and last annotation group is the derived-figure declaration, and this is the residual it
names.** All three files also carry
`derived_recomputable: []` / `derived_unpinned: [...]` / `derived_note`. These runs recorded no per-item
seed rank and no per-item ranked-list id digests, so **`seed_effect`, `expansion_fired_items` and
`ranked_lists_compared` are the run's own output but CANNOT be recomputed from the artifact**: the suite
holds them to "the brief repeats the artifact" only, so a *consistent* re-staling of the figure in BOTH
files is invisible to it (`#13267` review 5 F1 — measured: `seed_lost_from_window: 3 → 0` in the artifact
and the brief together SURVIVED, as did `expansion_fired_items: 293 → 1` together). ⚠️ **`off_topk_displacement`
is not re-derivable even for a new run**: it needs the ranked-list IDS and the artifact records only their
digests, so its only protection is the range invariants plus the CHURN framing. The tool now records
`per_item.off_seed_rank` / `on_seed_rank` and declares per arm which figures it makes re-derivable, and the
suite RE-DERIVES every declared one; the committed files declare none, and say so, rather than implying a
protection they do not have.

⛔ **These three files were RE-SERIALIZED once, mechanically, and it is not a seventh annotation group.**
Their runs wrote `NaN` tokens into four KINDS of cell (`mde_paired`, `mde_independent`,
`correlation.rho`, `correlation.se_reduction`) — all four per `k` in the control (16 tokens), but only
the two correlation cells per `k` in each point artifact (8 each: their `mde_paired` /
`mde_independent` are finite — `0.0383…` at k=15 in `paired-point-300.json`, `0.0208…` in
`paired-point-300-short-query.json`, so quoting one figure for both is wrong) — and RFC 8259 does not
admit `NaN` in its number
grammar, so the committed evidence could not be loaded by any strict parser at all (node's `JSON.parse`
rejected the whole file) and the tolerant ones mapped the token to `null` — the same cell an unrecorded
field occupies (`#13267` review 12, P2). They were re-encoded with the tool's own writer (`_write_json`
→ `_json_safe`), which maps every non-finite float to `null`; **it changes no other byte, adds no field
and loses no information** — but the carrier differs by cell, and the earlier wording here said
otherwise. The MDE's null is carried by the `mde_note` beside it ("NOT ESTIMABLE: no discordant pair was
observed …"). The `correlation` block's `rho`/`se_reduction` nulls carry **no note**: both keys are
always written, so key PRESENCE is what separates "recorded, not estimable" from "never recorded", and
the reason is `outcome_correlation`'s documented one — one or both arms have zero outcome variance
(measured: every committed correlation null is that case; the point artifacts' off-arm hits are all 0).
The suite pins that key-presence separation for the control and a point artifact. So the count above
stays SIX annotation FIELD
groups: this is a re-encoding of run output, not an annotation of it, and a reader can verify it by
diffing the three files against the run that produced them.

⚠️ The re-encoding corrected the FILES; the three artifacts' own `provenance.git_head_note` went on
describing the old bytes — it said the control's populated `mde_note` "sits beside a `nan`
`mde_paired`" while the cell beside it is `null`, i.e. the one carrier a reader uses to tell WHICH
null a cell is named a token the file no longer contains (`#13267` review 13 F2). Those three notes
were corrected BY HAND and are the only part of this change that is not mechanical: they are
hand-carried, nothing regenerates them (the tool's current writer emits a different, generic note), so
this is a semantic correction of the evidence's prose, not a re-serialization.
`test_no_artifact_note_claims_a_token_the_file_no_longer_carries` holds a note that names the token
to the `null` encoding it became.

The tool records, in the artifact's own `provenance` block, the sha256 of its own bytes and whether those
bytes were committed (`tool_sha256` / `tool_uncommitted`) — because `git_head` alone does **not** identify
the producing code (`#13267` F4): these three artifacts record `9b537d875a` while carrying the `mde_note`
key (the control's populated, beside a `null` `mde_paired`; the two point artifacts' `""` with a finite
`mde_paired`), which only `5898385f2e`'s code emits (that fix was uncommitted when the run happened),
and the earlier generation recorded `44a1b82` while carrying fields first committed in `9b537d8`.

`tests/test_paired_expansion_ab_2518.py` pins the statistics to hand-computed tails and to the brief's own
sample-size arithmetic, the snapshot/population refusals, the item construction, the refusal CLASSES (so
`--allow-mixed-modes` cannot excuse a blind or inert run), the ON-vs-OFF delta being a comparison of
recorded ranked lists rather than `None == None`, **this brief's own citations against the committed
artifacts**, and one live end-to-end run in which the point arm reaches the lever and the object arm
provably cannot. ⛔ The case count moves with every review round — run the file (or
`tools/ci_selection.py --integrity` for the registration) rather than trusting a number quoted here.
