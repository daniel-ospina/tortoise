---
title: "#1789 — the S4 re-emit tax, measured: −93.8 % output tokens on real geometry"
type: evidence
domain: platform
doc_status: final
created: 2026-11-07
subjects.team: epistemic-team
aboutSubjects: S4 gap-review stage
aboutObjects: "#1789, PR #8041, #4917, #2408, plan docs/plans/2026-08-27-1789-s4-delta-contract.md"
---

# `#1789` — the S4 re-emit tax, measured

**Measured on:** 2026-11-07, `feat/1789-s4-reeemit-tax-measurement` (base
`origin/main` @ `23d227c53`), model **requested** `deepseek-v4-flash` via the
direct DeepSeek adapter, 7 real recorded sessions, **2 recorded runs, both
produced by this instrument as it stood when they ran** (fields added to it
afterwards — `calls`, `sessions_failed`, `partial`, `classify_later`,
`templates` — are named below, and are absent from the artifacts for that
reason): `ab-20261107T023958Z` (primary) and
`ab-20261107T024226Z` (re-run, same code, same inputs). ⚠️ One field of BOTH artifacts was
corrected AFTER the runs: `caveats[2]` still carried the refuted "S2 has already been
written, so S4 sees it" reading, so that one string was replaced — with the
**intermediate** caveat, which still ends "…so the measured saving is a CEILING and the
delta arm's `added_by_s4` / `s4_items` read as a floor." That one-sided wording is itself
superseded by *What this does not measure* below (it is a caveat, never a measurement, and
no measured field was touched); the instrument at this head emits a corrected string, pinned by
`test_the_emitted_caveats_carry_the_corrected_population_and_direction`. ⚠️ **A second
artifact field was corrected after the runs, for the same reason**: `caveats[0]` in
BOTH artifacts said *"the prompt inputs are identical across arms, so the delta is the
output contract's effect"* — which the artifacts' own `prompt_tokens` rows contradict
(the delta arm's prompt is **+242 tokens in every session of both runs**, see the
input-side paragraph under *Verdict*). The string now states the same input CONTENT,
the small POSITIVE input cost, and that the measured delta is the whole contract's
effect; the instrument emits the same sentence plus its tax-surface scoping, and the
pair is pinned by `test_the_emitted_caveats_carry_the_corrected_population_and_direction`.
⚠️ The **caveats
list** also postdates the artifacts in a second sense: they carry **5** entries and the
instrument now emits **6** — ONE ADDED plus TWO EDITED. Added: the replayed-list narrowing
(266 of the 311 recorded items; `chain_notes` 7 + `link_before_create` 38 dropped). Edited:
`CAVEATS[0]` (scoped to "the 4 tax-surface sections", and the input-side sentence above)
and `CAVEATS[3]`, which absorbed the
231-of-266 offered population and the 35 never-offered `operators` while replacing the
one-sided CEILING wording — the artifacts' replacement of that entry carries neither count. So
a reader who trusts the artifact's own caveats alone gets neither population statement. **No measured field was touched** — every token count,
composition channel and reduction is byte-identical to what the runs emitted (only the
`caveats` strings above and the `caveats[0]` sentence differ); re-running
needs a paid key and would move the numbers for reasons unrelated to the correction. The
filenames are each
run's START time; `measured_at` inside is its measured end. The model is not
deterministic even at `temperature=0.0`, which is why two runs are recorded and
the spread (`−93.78 %` / `−93.96 %`) is stated rather than hidden.

**What was already true:** the delta contract (PR #8041) shipped and is
default-ON (`_s4_delta_enabled()` = `env_flag("TORTOISE_S4_DELTA", True)`;
`S4_TMPL_DELTA` derived from the byte-identical legacy constants).

**What was missing — this document:** the issue's own last comment (2026-11-01)
called the ticket **PARTIAL** and restated the remainder as *"run the S4-token
measurement against a frozen baseline, then compare to ≥40 %."* The plan's
Task 10 (fresh 50-question E2E) was never run, so the delta contract's
**magnitude was unmeasured**.

---

## Verdict

| issue target | measured | verdict |
|---|---|---|
| **S4 output size per chunk drops ≥ 40 %** | **−93.8 %** token-weighted (32 787 → 2 038 output tokens over 7 sessions); median **−96.9 %**, worst session **−85.16 %**, best **−98.3 %** | **MET**, 2.3× the threshold; each of the 7 sessions clears 40 % on its own. ⚠️ **This is not production's saving, and its DIRECTION is not established by this artifact** — see *What this does not measure*: the reconstructed S3 block offers the delta arm **231 of the 266** S2 items as already existing — the block renders entities/points/events only, so the 35 `operators` items are never offered (in production too), and their correction channel is structurally zero — which is the **maximum** correction opportunity for those 231 (the arm took none: 0 collisions, which is therefore *not* evidence about operator corrections) while it is the **minimum** for gaps — so one channel is measured at its most favourable input and the other at its least, and the aggregate could be either |
| truncation rate → near 0 | `truncated=False` on **14/14** arm calls, `attempts=1` | ⚠️ **NOT EVIDENCED.** The largest output in either arm of either run (**7 814** tokens, the re-run's; this table's own primary run tops out at **6 521**) is **under half** the 16 000-token default (`_S2_S4_MAX_TOKENS`), so this geometry has no plausible route to truncation and a null here is an artefact of the harness, not a measurement of the rate. ⚠️ The effective cap is `_stage_cap(_S2_S4_MAX_TOKENS)`, which is env-overridable (`TORTOISE_EXTRACTOR_MAX_TOKENS`) and is **not recorded in the artifact** — so the margin is stated against the DEFAULT, and a box with the override set would need its own reading. What the artifact does record is the outcome (`truncated: false` on every call). That target needs the eval lane's geometry. |
| accuracy ≥ 0.826 / chunk@20 ≥ 0.738 | **not measured here** — the eval lane's gates | see *What this does not measure* |

Re-run (`ab-20261107T024226Z`): 33 912 → 2 047 tokens, **−93.96 %**, median
−96.87 %, worst −85.19 %, best −98.1 %. The two runs differ by 0.18 points, and
the worst session in either run is −85.16 %, so the effect is not carried by
outliers.

**The other half of the bill, which a reader would otherwise never see.** Both
artifacts record `prompt_tokens` per arm, and the switch made the prompt
*slightly dearer* — the delta template is longer, so **every one of the 7
sessions pays exactly +242 input tokens, in both runs**:
**109 201 → 110 895 prompt tokens, +1 694** over the seven. Total tokens
(input + output) therefore fall **141 988 → 112 933 = −20.5 %** in the primary
run and **143 113 → 112 942 = −21.1 %** in the re-run — not −93.8 %. The issue's
target is the **output** side (*S4 output size per chunk*), which is what the
verdict row above measures and what the index row routes on; the saving a
production operator would book is the **total**, and it is **4.6×** smaller in the
primary run (**4.5×** in the re-run — the same spread this document states rather than
hides for the output pair).
Stated here because it is the one number this document could have been read
without: −93.8 % is the figure in the title.

## The method — one variable

The tax is a property of the **output contract**, not of the pipeline, so it can
be isolated instead of inferred. For each session, `run_s4()` is called **twice
over identical inputs**:

| arm | template | what the contract asks for |
|---|---|---|
| `legacy` | `S4_TMPL` (`delta=False` ⇒ today's `TORTOISE_S4_DELTA=0`) | re-emit the COMPLETE embed list |
| `delta` | `S4_TMPL_DELTA` (`delta=True` ⇒ the shipped default) | gap ADDITIONS + full-item CORRECTIONS only |

Same story, same S2 embed list, same reconstructed S3 search block, same model,
same cap — `run_s4` threads `delta` and nothing else (pinned by
`test_both_arms_get_identical_inputs`). The number is the provider's own
`usage.completion_tokens`, read from `stats["cost"]["completion_tokens"]`, the
**per-call SUM** — so an escalated retry or a parse-retry re-prompt is counted
rather than overwritten. An arm is recorded as **unmeasured** (`null`) — never
as 0 — when the provider reported no usage for any call (rejected count, absent
usage block, or a deadline-aborted call), or when the recovery accumulator cannot
be shown to be complete. ⚠️ **The one residual this cannot catch:** an attempt that
RAISED before returning a response is counted nowhere, and the instrument does not
claim to detect it — `attempts` is the running total of per-call attempt numbers (a single
429-retried call reports `attempts=2` while contributing one count) and the raising
call's own write clobbers it, so a gap against `cost["calls"]` is neither sound nor
complete. **Both numbers are recorded on every row** — `attempts` and `calls`, on the
success path AND the error path — so a reader can see a discrepancy even though the
instrument cannot classify it. ⚠️ The two recorded runs PREDATE the `calls` field: they
carry `attempts=1` on all 14 arm calls, and `calls` is recorded from the next run on
(the instrument at this head writes both; hand-adding the key to the committed
artifacts would be inventing a measurement, which is why it is stated here instead). For THEM the check is therefore an inference,
not a proof: `attempts=1` with no `error` on all 14 arm calls *implies* one
contributing call per arm — it cannot exclude a raise that was silently retried.
The class of attempt that escapes every counter stays a production follow-up
(`_complete` has no `calls_raised` counter). `sessions_failed`, `partial` and
`classify_later`/`templates` postdate them for the same reason: the instrument
at this head writes them, the committed artifacts do not carry them.

**Inputs are real, not synthetic.** The sessions come from the recorded
write-path eval receipts (`tests/eval/write_path/receipts/7947-arms-2026-10-18/`):
real S1 stories and real S2 embed lists (29–49 items per session across
entities/events/points/operators; the recorded S2 output those sessions cost is
**3 263–5 453 tokens, median 4 187**, per `inputs[].s2_reference`). ⚠️ The embed
list handed to `run_s4` is the **4 tax-surface sections** — 266 of the 311 items
the receipts record; `chain_notes` (7) and `link_before_create` (38) are not in
the prompt. Both arms see the same narrowed list, so the A/B stays
single-variable, but the narrowing shrinks the legacy arm's re-emit target and so
**understates** the measured reduction. ⚠️ The `id`
values in the reconstructed search block are **synthetic** (`s2-ent-0`, …) — the
receipts record no graph ids — so an output field that COPIES an id can only be
an echo of the instrument's own alias, and is discounted below.

**Nothing is lost by construction.** The delta arm's omissions are preserved by
the E4 union: `merged_items` is 272 vs an S2 base of 266 — the delta added 6
gaps and dropped none of the 266 S2 items (the legacy arm merged 278).

## What the delta arm stopped paying for

Summed over the 7 sessions (`composition`, primary run):

| channel | legacy | delta | reading |
|---|---|---|---|
| `verbatim_reemissions` — items re-typed **unchanged** (the tax) | **201** | **0** | pure typing cost; nothing about them was new |
| `corrected_by_s4` — S2 items S4 re-emitted at all | 266 | 0 | legacy re-touched every S2 item it saw; delta touched none |
| of which a field changed (census: `corrected_by_s4 − verbatim` = 65) | **65** | **0** | see the residual — the pairs are in the artifact |
| `added_by_s4` — the gap channel (`added_items`) | 12 | 6 | see the residual |
| `s4_items` emitted | 278 | 6 | 272 fewer items per 7 sessions |

(Re-run: legacy 201 verbatim / 266 collisions / 9 added / **275** emitted; delta
0 / 266 kept with no collision, 0 verbatim, 6 added / **6** emitted — i.e. 269
fewer items.)

The artifact records **64 correction pairs** against the census's 65, and — this
is the point of recording both numbers — **no list reached the 40-item cap in
either run** (`cap_hit: false` on every row; the largest list is 17). Each item
dict carries `count` (census), `candidates` (what the classification mask could
enumerate), `recorded`, `cap`, `cap_hit`, `cap_dropped` and `unclassified`, so a
CAP drop and a MASK deficit (the two independent reasons a list can come up
short) are never conflated: here `cap_dropped` is 0 everywhere and
`unclassified` is 0 on 6 of the 7 rows and 1 on the remaining one — the
census counting a change in a field outside `_CORRECTION_FIELDS`.

## ⚠️ The residual, partitioned from the artifact (do not read −94 % as a free win)

The plan's Task 10 Step 5 asks for the corrections partition: content-changing /
kind-lifecycle-only / re-emission-noise. **The artifact carries the pairs, so it
can be answered without another paid run, and the answer is in the data:**

| bucket | primary | re-run |
|---|---|---|
| `content` / `name` changed (**a rewritten claim**) | **0** | **0** |
| `lifecycle` only | 62 | 62 |
| `slots` only | 1 | 1 |
| `supersedes` only | 1 | 1 |

Every lifecycle change is the **same transition**: `created`/`changed` →
`unchanged` (61 + 1). In other words the legacy arm's entire correction channel
is *"this entity/point already exists in the graph — mark it unchanged"*, i.e.
de-duplication bookkeeping, **not** a rewrite of any claim: **zero** corrections
changed an item's `content` or `name` in either arm (the one `slots`-only
correction does change a slot's KIND — `core:code` → `dev:code` — which is a
re-type, not a rewritten claim), and the single `supersedes` change is the only
one that copied an id — from the instrument's own synthetic alias, so it is an
artefact by construction.

So, stated with its numbers:

- **Corrections.** Up to **65 corrections per 7 sessions** are no longer made
  under delta (the delta arm had **0 collisions in its own 7 calls**), of which
  62 are lifecycle de-duplications, 1 a `slots` change and 1 the synthetic-id
  echo; the 65th is the census's extra, unclassified by the mask. No claim text
  was ever corrected by either arm, so the accuracy risk from this channel is
  *narrower than the issue assumed*: what disappears is the `created → unchanged`
  signal, which is reconstructible from the graph at write time rather than from
  S4's opinion.
- **Gaps.** legacy `added_by_s4` 12 (primary) / 9 (re-run) vs delta 6 / 6, and the
  delta arm emitted **no new tax-surface item** in 5 of 7 sessions in both runs.
  The narrower statement matters: `s4_delta_empty` — production's predicate over
  EVERY emitted section, not the four tax sections — is **False on all 7 delta
  rows in both runs**, i.e. those arms still carried *some* section. **Which**
  section is not recorded in this artifact, so "graph-search bookkeeping" would
  be a guess and is not claimed here. ⚠️ Nor does `False` mean the **graph**
  received a section: production's `_s4_section_is_content` counts a
  model-invented section key as content for any non-empty list, while
  `merge_embed_lists` iterates a fixed section tuple and drops such a key — so
  on these rows `False` is equally consistent with a real non-tax section and
  with an invented key that contributed nothing. Recording per-row section names
  and lengths is the follow-up that would settle it.

Two readings fit the gap channel, and nothing in this artifact separates them:

1. the legacy arm's extra items are artefacts of it re-typing 266 items — a
   paraphrase lands as a new identity — so they are the tax's own noise; or
2. the delta contract suppressed genuine gap-finding, i.e. up to 12 gaps per 7
   sessions are now missed.

Separating those needs the gap items classified, and the artifact supports that as a
reading task rather than another measurement — **with one gap**: `added_items` records
content and kind for entities, events and points, but an OPERATOR carries neither
(`_item_record` reads `content`/`name` and `pointKind`/`kind`; an operator is keyed by
`src`/`dst`/`op_type`), so an operator gap is recorded with `content: ""`, `kind: null`
and its identity only inside the 160-char `key`. In the primary run 5 of 12 legacy gaps
and 2 of 6 delta gaps are operators, so that partition needs the receipt, not just the
artifact.

## What this does **not** measure

- **Accuracy / `chunk_evidence@20`** — the eval lane's gates (#1789 target 3).
- **The truncation rate** — no call truncated and none came within half of the
  default cap (see the verdict table), which is *not* the rate.
- **The effective stage cap, per row** — the artifact records `truncated` but not
  the cap it was measured against, so the margin above is against the default.
- **Model compliance in the wild** — one call per arm per session, not a
  production run. Non-compliance is *observable* per row
  (`verbatim_reemissions`, `corrected_by_s4`, `s4_delta_reemit_suspect`).
- **Which model actually served the calls** — the artifact records the model id
  REQUESTED; the adapter logs a substitution warning rather than persisting it.
- **The S3 live-graph read** — the search block is reconstructed (with synthetic
  ids) from the session's own S2 list; both arms see the same block, so the
  comparison holds, but id-copying output is not real. ⛔ **And it is not what
  production S4 sees.** `extract_session_v2` runs S3 (`search_graph`) *before*
  S4 and writes the session's items only afterwards (`execute_embed`), so
  production S4 reads **previous-graph** hits — bounded at `max_queries=15` ×
  `limit=3`, with the capture's own turn echoes dropped. That matters in BOTH
  directions, and they point opposite ways:

  - the **correction** channel is measured under a MAXIMALLY CORRECTION-FRIENDLY
    input — every one of the 231 offered items is labelled already-existing. The
    bound that survives is the FLOOR, not a maximum:
    `S4_TMPL_DELTA` emits a correction only where the search shows the item
    already exists, and the reconstruction offers 231 of the 266 items as
    existing (the block renders entities/points/events only — the 35 `operators`
    are never shown, in production too) — the
    delta arm nevertheless emitted **0** corrections in all 7 sessions of both
    runs. **0 is the minimum a correction channel can emit**, so that channel cannot
    emit FEWER than this block did, in production either, whatever its block offers —
    the comparison needs no claim about which items production shows, and the "fewer"
    reading does not hold anyway:
    this block's 231 is a 7-session total while production's search is bounded
    PER SESSION (`max_queries=15` x `limit=3` = 45), and this block offers 25–45 items
    per session — so in 6 of the 7 sessions production's cap is the LARGER number and
    in the seventh the two are equal (45).
  - the **gap** channel is measured at its *minimum*: an input that says
    everything already exists leaves a "emit only what is missing" contract
    nothing to find, which is consistent with the delta arm's narrower gap
    channel (6 vs 12/9) — and equally consistent with the contract itself
    suppressing gap-finding, which this artifact cannot separate.

  So the aggregate's direction is **not** established, and it does not reduce to
  one sign: the measured −93.8 % is the contract's effect under ONE fixed input.
  Take the two arms separately, because the two channels do not share a sign.
  Inside the **delta** arm the correction channel cannot *shrink its output* below
  its floor — it already emits nothing (0), which is the minimum a correction
  channel can emit, so production cannot go BELOW it (it can carry more to correct:
  that channel is measured on a maximally correction-friendly input for the 231
  items the block can offer — the 35 `operators` are never rendered, in production
  too, so that part is structurally zero in both) — while
  its gap channel can only *grow it*, since that channel is measured at its
  **minimum**. So production cannot emit LESS of the two channels this section names than
  the delta arm did: the delta RESPONSES that carried tax-surface items cost **1 498**
  (primary) / **1 480** (re-run) in whole-output terms. ⚠️ Whole-output terms is all this
  artifact can offer — `out_tokens` is the provider's `completion_tokens` for the whole
  response and the artifact records item COUNTS, never a per-section token split. So that
  figure is an upper bound on the DELTA ARM's tax-surface component and an EXACT total for
  those responses' whole output — it bounds production from neither side; the residual is visible
  in the data the artifact does keep: wp05 and wp07 emitted the same channel COUNTS (3
  gap items, 0 corrections each) and cost 968 vs 530 (958 vs 522 in the re-run), and the
  other **540** / **567** tokens
  sit on the 5 rows whose `s4_items` is 0, with `s4_delta_empty` `false` on all 7 delta
  rows — a third channel this instrument does not classify, present on every row and
  therefore not excluded from the figures above. The **legacy** arm carries the SAME gap channel, and it
  is the larger one measured here — 12 / 9 gap additions in the primary / re-run
  against the delta arm's 6 / 6 — so the same production input moves its cost as
  well. Neither arm's sensitivity to the real geometry is measured, so the
  ratio, which is the saving, is undetermined in BOTH signs: **neither a ceiling
  nor a floor**. Settling it needs a
  production-geometry run.

  ⚠️ **What the committed artifacts actually say.** Their `caveats[2]` ends
  "…Relabelling the session's own list as already-in-the-graph is the maximally
  gap-SUPPRESSING input for a delta contract, so the measured saving is a CEILING
  and the delta arm's `added_by_s4` / `s4_items` read as a floor." That is the
  one-sided reading this section replaces: it names only the gap channel and
  concludes a ceiling, when the same input also makes the correction channel
  *maximally* exercisable — and the legacy arm's cost moves with the input too,
  so neither sign is established (neither a ceiling nor a floor). The string
  stands as the **disclosed hand-correction**
  left it — word-for-word the instrument's own caveat from `10b8d52cb` until this
  change — and it is left alone now for the same reason it was corrected then:
  it is a caveat, never a measurement. Editing it again to match this section
  would be the third hand-write of a provenance field, and a reader is better
  served by an artifact that keeps its history visible than by one that silently
  agrees with the latest argument. The instrument at this head emits the
  corrected wording, so the next run carries it.

## Reproduce

```bash
# zero-cost, no network — the input-side geometry + the vacuity pin
uv run python tools/s4_reeemit_tax.py dry-run

# the measurement (needs DEEPSEEK_API_KEY; ~2.4 min and a few cents for 7 sessions)
DEEPSEEK_API_KEY=… uv run python tools/s4_reeemit_tax.py run \
    --out docs/evidence/1789-s4-reeemit-tax/ab-$(date -u +%Y%m%dT%H%M%SZ).json
```

Artifacts: `ab-20261107T023958Z.json` (primary — per-arm rows, the added and
corrected items with the cap/census accounting above, composition totals, and the
per-arm `s4_delta_empty` / `s4_delta_reemit_suspect` flags derived with
production's own predicates) and `ab-20261107T024226Z.json` (the re-run).

Instrument tests: `tests/test_s4_reeemit_tax.py` (**37** tests, offline, fake
model) — they pin the ways a number like this goes wrong silently: a vacuous A/B, the
two arms receiving different inputs, a mis-attributed cost (a per-call total
rather than the final call; `null` rather than 0 when the provider reports
nothing, when any of the three "counted nowhere" counters fires, or when the
recovery accumulator is provably incomplete), a "measured zero" pretending to be
a measurement, a cap and a mask deficit conflated into one flag, a corrections
channel an items-not-in-S2 filter drops, and `s4_delta_empty` diverging from
production's predicate. Two more came from review:
`test_a_failed_arm_keeps_the_SPEND_it_incurred` — `_complete` accumulates cost
on its SUCCESS return *before* the parse, so a failed arm often HAS incurred
spend; hard-coding `null` on the error path discarded it, and the arm it
discarded is the expensive (truncating / unparseable) one, which biased the
comparison toward the arm that never fails. A genuinely tokenless failure is
still `null`, never 0. And the counterpart —
`test_a_failed_arm_that_SPENT_is_still_excluded_from_the_reduction` — keeps that
spend out of the *headline*: a pair with a failed arm is not a measurement of
output size, so it is excluded from the sums and the median while its spend and
`error` stay on the row and `sessions_failed` keeps the excluded population
visible.

Two of them cover this bundle. `test_every_committed_artifact_reconciles_with_its_own_rows`
pins each artifact's **internal** consistency and hard-codes no figure, so a
legitimate regeneration does not red it.
`test_the_readme_figures_reproduce_from_the_committed_artifacts` is its
literal-figure counterpart: it reads both
committed JSONs and asserts every headline figure above is *recomputed* from
them — the token sums, both reductions and medians, the worst and best sessions,
**each composition channel in its own table cell** (a substring pin would stay
green when the wrong cell changed: `201`, `12` and `62` each occur several times
in this document), the census-vs-mask deficit as the sentence that states it, the
62/1/1 bucket table, `cap_hit`/`cap_dropped` on every capped list of every arm,
and `s4_delta_empty` on every delta row. The other pin — the internal-consistency
one — additionally reconciles `summary.rows` against `arms` pairwise and each
composition total against the measured arms, so an artifact assembled from two
different runs cannot stay green.

⚠️ It does **not** pin this document's PROSE, and that gap is where this file
went wrong four times: an arithmetic slip between the two arms' `s4_items`, and
a sentence claiming no correction changed a kind while the `slots` pair beside
it changed one — both survived every test and were caught only by reading the
artifact. Two prose claims are checkable, and are checked: this file may not
re-assert that no correction touched an item's name or re-typed its kind (the
claim the `slots` pair contradicts), and it must state the `core:code` →
`dev:code` re-type. Everything else in the prose still rests on a reader opening
the artifact.
