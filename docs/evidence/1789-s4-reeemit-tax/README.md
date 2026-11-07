---
title: "#1789 — the S4 re-emit tax, measured: −93.8 % output tokens on real geometry"
type: evidence
domain: extraction
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
produced by the committed instrument**: `ab-20261107T023958Z` (primary) and
`ab-20261107T024226Z` (re-run, same code, same inputs). ⚠️ One field of BOTH artifacts was
corrected AFTER the runs: `caveats[2]` still carried the refuted "S2 has already been
written, so S4 sees it" reading, so that one string was replaced with the instrument's
current caveat (#11385 review). **No measured field was touched** — every token count,
composition channel and reduction is byte-identical to what the runs emitted; re-running
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
| **S4 output size per chunk drops ≥ 40 %** | **−93.8 %** token-weighted (32 787 → 2 038 output tokens over 7 sessions); median **−96.9 %**, worst session **−85.16 %**, best **−98.3 %** | **MET**, 2.3× the threshold; each of the 7 sessions clears 40 % on its own. ⛔ **Read this as a CEILING, not as production's saving** — see *What this does not measure*: the reconstructed S3 block tells the delta arm that the session's own items already exist, which is the input under which a delta contract has the least to emit, so the real saving can only be smaller |
| truncation rate → near 0 | `truncated=False` on **14/14** arm calls, `attempts=1` | ⚠️ **NOT EVIDENCED.** The largest output either arm produced is 7 814 tokens against a 16 000-token cap (`_S2_S4_MAX_TOKENS`), so this geometry cannot truncate and a null here is an artefact of the harness, not a measurement of the truncation rate. That target needs the eval lane's geometry. |
| accuracy ≥ 0.826 / chunk@20 ≥ 0.738 | **not measured here** — the eval lane's gates | see *What this does not measure* |

Re-run (`ab-20261107T024226Z`): 33 912 → 2 047 tokens, **−93.96 %**, median
−96.87 %, worst −85.19 %. The two runs differ by 0.18 points, and the worst
session in either run is −85.16 %, so the effect is not carried by outliers.

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
be shown to be complete.

**Inputs are real, not synthetic.** The sessions come from the recorded
write-path eval receipts (`tests/eval/write_path/receipts/7947-arms-2026-10-18/`):
real S1 stories and real S2 embed lists (29–49 items per session across
entities/events/points/operators; the recorded S2 output those sessions cost is
**3 263–5 453 tokens, median 4 187**, per `inputs[].s2_reference`). ⚠️ The `id`
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

(Re-run: legacy 201 verbatim / 266 collisions / 9 added; delta 0 / 0 / 6.)

The artifact records **64 correction pairs** against the census's 65, and — this
is the point of recording both numbers — **no list reached the 40-item cap in
either run** (`cap_hit: false` on every row; the largest list is 17). Each item
dict carries `count` (census), `candidates` (what the classification mask could
enumerate), `recorded`, `cap`, `cap_hit`, `cap_dropped` and `unclassified`, so a
CAP drop and a MASK deficit (the two independent reasons a list can come up
short) are never conflated: here `cap_dropped` is 0 everywhere and
`unclassified` is 0 on six rows and 1 on one — the census counting a change in a
field outside `_CORRECTION_FIELDS`.

## ⚠️ The residual, partitioned from the artifact (do not read −94 % as a free win)

The plan's Task 10 Step 5 asks for the corrections partition: content-changing /
kind-lifecycle-only / re-emission-noise. **The artifact carries the pairs, so it
can be answered without another paid run, and the answer is in the data:**

| bucket | primary | re-run |
|---|---|---|
| `content` / `name` / kind changed (**a rewritten claim**) | **0** | **0** |
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
  be a guess and is not claimed here.

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
- **The truncation rate** — this geometry cannot truncate (see the verdict table).
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
  `limit=3`, with the capture's own turn echoes dropped. Relabelling the
  session's own list as "already in the graph" supplies the input under which a
  delta contract has the *least* to emit, so the delta arm's `added_by_s4` /
  `s4_items` read as a **floor**, and "the contract suppressed gap-finding" is a
  live alternative explanation this instrument cannot separate from "the input
  left it nothing to find". Both arms see the same block, so the A/B stays
  single-variable — but the magnitude is the contract's effect **under a
  gap-suppressing input**, not production's effect.

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

Instrument tests: `tests/test_s4_reeemit_tax.py` (offline, fake model)
— they pin the ways a number like this goes wrong silently: a vacuous A/B, the
two arms receiving different inputs, a mis-attributed cost (a per-call total
rather than the final call; `null` rather than 0 when the provider reports
nothing, when any of the three "counted nowhere" counters fires, or when the
recovery accumulator is provably incomplete), a "measured zero" pretending to be
a measurement, a cap and a mask deficit conflated into one flag, a corrections
channel an items-not-in-S2 filter drops, and `s4_delta_empty` diverging from
production's predicate.
