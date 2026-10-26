---
title: "#8486 — LongMemEval-S end-to-end QA, FIRST EXECUTED RUN: pilot measured (QA 0.66, official recall_all@5 0.78), graded by DeepSeek — NOT comparable to the published figures"
type: evidence
domain: platform
doc_status: live
created: 2026-10-25
subjects.team: epistemic-team
aboutSubjects: LongMemEval-S, longmem_eval harness, reader, answer-check judge
aboutObjects: "#8486, #2105, #2080, #9097, tools/longmem_eval/*, docs/benchmarks/longmemeval-500q/run-report.md"
---

# `#8486` — LongMemEval-S end-to-end QA: the first executed run

**Measured 2026-10-25** on `4c63488719540398dc631cb5f5d496150f88e37b` (== `origin/main` when the run
started, re-checked with `git fetch origin main && git rev-parse origin/main`) **plus this PR's patch
in the working tree**. The run's own `methodology.git_sha` therefore records the BASE commit; the exact
tree it ran on is `4c63488` + this PR's `git diff`, which was
`sha256:ce74075bc8a1034292c4beef52b9f9a852bcd72c3e433c369bc9597f3ceddc33` at run time. (The code was
necessarily uncommitted: this repo's single-commit discipline puts the report in the same commit as the
code it reports on, so no run of this kind can record its own final sha. Recorded rather than papered
over.) **One post-run change to that diff exists and is disclosed here:** `ruff --fix` moved the
`tortoise.models` import in `tools/longmem_eval/reader.py` (I001) and dropped a redundant local alias in
`judge.py` — import order only, no behaviour change; every number below was produced by the tree at the
sha above.

> ## The number, with its judge
>
> **50 questions** (the protocol's type-stratified pilot slice), `deterministic` ingest, reader
> `deepseek:deepseek-flash`, judge **`deepseek:deepseek-v4-pro`**:
>
> | | value |
> |---|---|
> | **QA accuracy (end-to-end)** | **0.6600** (33/50), 95% CI [0.522, 0.776] |
> | task-averaged accuracy | 0.6597 |
> | **`recall_all@5` (OFFICIAL metric formula)** | **0.7800** (50/50 eligible, non-`_abs`) |
> | `recall_all@5` — multi-session gate row | **0.3333** (n=9) |
> | `recall_all@5` — knowledge-update gate row | 0.6250 (n=8) |
> | `recall_all@5` — single-session-preference gate row | 1.0000 (n=8) |
> | per-question fraction @5 (labelled variant, NOT official) | 0.8867 |
> | wall clock | **80 min 52 s** (4 851.69 s) |
> | LLM spend | **$0.0620** measured by tokens; the runner's own `cost.usd` reads $0.008456 — see §8.3 |
>
> ⛔ **This is not a LongMemEval-S score and must never be published as one.** The official benchmark
> grades with `gpt-4o-2024-08-06`; this box has no `OPENAI_API_KEY` and no `OPENROUTER_API_KEY`, so the
> run was graded with DeepSeek. §6 is the comparability statement, and it is the reason this number is
> usable as an *instrument reading* and not as a *competitive claim*.
>
> **Judge in one line, where a reader reads it:** `judge: deepseek-v4-pro`, **therefore NOT comparable
> to the published figures, which use `gpt-4o-2024-08-06`.**

## 0. Why this document exists

`#8486` is P1 because the number it asks for has never existed: `docs/benchmarks/longmemeval-500q/run-report.md`
says `RUN STATUS: PENDING — full-run NOT executed`, so every competitor comparison is unfalsifiable in
both directions. Two lanes stopped on the same wall — the judge's provider key — and the wall is real
(no OpenAI key, no OpenRouter key on this box).

**The position taken here, and the reason this run exists at all:** a measured number with a stated,
non-default judge is worth more than a pending one, *provided the report says exactly what judged it*.
So this run produced (a) an end-to-end pipeline proof on an honest slice, (b) a cost / wall-clock /
failure-mode measurement nobody had, and (c) a QA number that is labelled *unpinned* wherever it
appears. It does **not** fill the `PENDING` rows of the sealed 500-Q report; §7 says what would.

## 1. What already existed (this lane wrote no benchmark)

The harness was already built and is not a stub — `tools/longmem_eval/` is a ~30-module measurement
layer over the product: `dataset.py` (official `xiaowu0162/longmemeval-cleaned`, split S),
`ingest.py` / `ingest_v2.py` (two ingest modes), `retrieve.py` (the production hybrid search: RRF of
FTS + vector + structural), `reader.py` (a thin re-export of the product reader), `judge.py` (the
official `get_anscheck_prompt` templates copied verbatim from `evaluate_qa.py`), `report.py` (integrity
block, per-category accuracy, recall@k, latency, token meter), `run_protocol.py` (the 9-step run
protocol), `w7_publish.py` (official metric derivation + receipt), plus the usage/cost meter. Gates 1–4
of the protocol (corpus seal, answer-key manifest, dataset audit, judge pin) were already recorded as
passed in `docs/benchmarks/longmemeval-500q/gate-evidence.json`.

**What did not exist: an executed run.** The lane's job was to execute, not to build.

## 2. What was run, exactly

```bash
# 1. the step-3 pilot slice, built by the COMMITTED selector (never a head slice — #2516)
PYTHONPATH=. .venv/bin/python docs/evidence/2026-10-25-longmemeval-pilot-s/probes/build_pilot_slice.py
#    -> /tmp/8486/run/pilot_stratified_50.json  (50 questions, 6 of 6 question types)

# 2. the run
PYTHONPATH=. /Users/danielospina/Documents/GitHub/tortoise/.venv/bin/python \
  -m tools.longmem_eval.run \
    --data /tmp/8486/run/pilot_stratified_50.json \
    --ingest-mode deterministic \
    --reader-model deepseek:deepseek-flash \
    --judge-model  deepseek:deepseek-v4-pro \
    --workers 4 \
    --checkpoint /tmp/8486/run/pilot50.checkpoint.json \
    --output     /tmp/8486/run/pilot50.report.json

# 3. the official metric derivation + receipt (committed code, not hand arithmetic)
PYTHONPATH=. .venv/bin/python -m tools.longmem_eval.w7_publish \
  --report /tmp/8486/run/pilot50.report.json \
  --dataset ~/.cache/tortoise-longmemeval/longmemeval_s_cleaned.json \
  --commit 4c63488719540398dc631cb5f5d496150f88e37b \
  --corpus-hash sha256:d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442 \
  --cost-usd 0.062 \
  --out /tmp/8486/run/w7a-pilot50.json
```

Nothing else: no `--db` (embedded per-question graph), no knob overrides, no `--mock`, no
`--skip-preflight`, no `--limit` (a head slice is the #2516 defect), no seed poke of any kind.

The worktree has no `.venv` of its own; the interpreter is the main checkout's (Python 3.12.13), and
`PYTHONPATH=.` makes it import **this** tree (`tortoise.__file__` verified to resolve inside the
worktree before the run).

### 2.1 Slice composition (written by the committed selector, printed by `run_protocol`)

The pilot is the protocol's **type-stratified** 50, not the split's head (the head is ~100%
`single-session-user` — the 2026-09-07 artifact that measured a near-ceiling, zero-signal slice):

| question_type | n |
|---|---|
| single-session-user | 9 |
| multi-session | 9 |
| knowledge-update | 8 |
| single-session-preference | 8 |
| single-session-assistant | 8 |
| temporal-reasoning | 8 |

⛔ **The slice contains no `_abs` (abstention) questions**, so `abstention accuracy` is `n=0` in this
run — one of the paper's five categories is *unmeasured* here. That is a property of the stratified
selector, not of the run.

### 2.2 Environment

| | |
|---|---|
| corpus | `xiaowu0162/longmemeval-cleaned`, split S, cached; sha256 `d6f21ea9…8c3a442` — **identical** to `corpus_hash` in `gate-evidence.json` (gate 1 re-verified from this worktree) |
| embedder | `BAAI/bge-small-en-v1.5`, sentence-transformers 5.7.0 — real dense leg (`vector_strategy: enabled`, not waived) |
| retriever | `hybrid` RRF (FTS + vector + structural); `leg_mix` = 100% `rrf`, 1660 hits |
| graph | embedded per-question (no docker, no FalkorDB); mean pool 750.8 points/question (p95 840.8) |
| ingest | `deterministic` — turn points + turn-granular raw chunks, **no operators/NANDs**, belief layer `inert` |
| reader | `deepseek:deepseek-flash` (direct `api.deepseek.com`), `reader_pinned: false` |
| judge | `deepseek:deepseek-v4-pro` (direct), rubric hash `82d07b0de05daa48` |
| workers | 4 |
| python | 3.12.13 |

## 3. What the pipeline measured

### 3.1 QA accuracy (end-to-end: retrieval → reader → judged)

| category | accuracy | n | 95% CI |
|---|---|---|---|
| **overall** | **0.6600** | 50 | [0.522, 0.776] |
| Information Extraction | 0.7200 | 25 | [0.524, 0.857] |
| Temporal Reasoning | 0.7500 | 8 | [0.409, 0.929] |
| Knowledge Updates | 0.6250 | 8 | [0.306, 0.863] |
| Multi-Session Reasoning | 0.4444 | 9 | [0.189, 0.733] |
| Abstention | n/a | **0** | — |

| raw question_type | accuracy | n |
|---|---|---|
| single-session-user | 0.8889 | 9 |
| single-session-assistant | 1.0000 | 8 |
| temporal-reasoning | 0.7500 | 8 |
| knowledge-update | 0.6250 | 8 |
| multi-session | 0.4444 | 9 |
| single-session-preference | 0.2500 | 8 |

**Scoring code named:** the judge is `tools/longmem_eval/judge.py` — the official
`get_anscheck_prompt` templates copied verbatim from LongMemEval's `evaluate_qa.py`, one user message,
`n=1, temperature=0, max_tokens=10`, no system message, no JSON mode, label =
`'yes' in response.lower()`. Rubric hash `82d07b0de05daa48` — **identical to the pinned value in
`docs/benchmarks/longmemeval-500q/run-report.md`**, i.e. the *rubric* is the official one and only the
*grading model* differs (§6).

The two weakest cells are the two the issue predicted: **multi-session 0.44** and
**single-session-preference 0.25**. Preference questions are the rubric-style long-answer class where a
strict binary judge most easily says "no".

### 3.2 Retrieval

| metric | @5 | @10 | @20 |
|---|---|---|---|
| **`recall_all@5` (OFFICIAL formula, via `w7_publish`)** | **0.7800** | — | — |
| per-question session fraction (labelled variant) | 0.8867 | 0.9250 | 0.9617 |
| turn / evidence recall (labelled variant) | 0.4943 | 0.6283 | 0.7933 |
| evidence vacuity rate (no usable evidence in context) | 0.3600 | 0.2400 | 0.1400 |

`recall_all@5` by gate category (the receipt's required rows, `coverage 3/3`): multi-session
**0.3333** (n=9), knowledge-update 0.6250 (n=8), single-session-preference 1.0000 (n=8), full mix
0.7800 (n=50). The gap between the all-or-nothing official binary and the per-question fraction is the
point of the metric: **39 of 50 questions carry every answer session in the top 5** (0.78) while the
11 that do not are far from complete — they retrieve **0.4848** of their gold sessions on average
(sum of their fractions `5.3333` / 11; their individual values are the rows with
`recall_all_at_5 = 0` in `per-question-outcomes.tsv`), and the multi-session row is where that gap is
fatal (0.630 fraction vs **0.333** all-or-nothing).

The two means are different populations and must not be quoted for each other: **0.8867 is the mean
over ALL 50** (§3.2 column, `sum(fraction)/50`), **0.4848 is the mean over the 11 imperfect ones**.

### 3.3 Cost, wall-clock, tokens

| | reader | judge | overhead (preflight, 2 lanes) | total |
|---|---|---|---|---|
| calls | 50 | 50 | 2 | 102 |
| prompt tokens | 437 253 (60 928 cache-hit) | 9 530 | 189 (reader 122 + judge 67) | **446 972** |
| completion tokens | 2 330 | 50 | 67 (reader 57 + judge 10) | **2 447** |
| cost (computed, §8.3) | $0.0535085 | $0.0083781 | $0.0001087 (reader $0.0000330 + judge $0.0000757) | **$0.0619953 ≈ $0.0620** |

**The token rows sum to the totals and the cost row does too** — recomputed from the report's exact
token counts at the map's own DeepSeek rates (`tools/longmem_eval/costing.py`, version `2026-09-04`:
flash-family in `0.14` / out `0.28` / cache-hit `0.0028` per 1M; `deepseek-v4-pro` in `0.87` / out
`1.74`, flagged `ESTIMATED`):
`0.0535085 + 0.0083781 + 0.0000330 + 0.0000757 = 0.0619953`. ⚠️ The report's OWN
`usage.overhead.cost_usd` reads **`7.6e-05`**, which prices only the judge overhead lane — the reader
overhead lane is `priced: false, reason: "unknown_model"` for the same reason as §8.3 — so anyone
recomputing the total from that field alone lands on `$0.06196` and is ~$0.00004 short. The table above
prices both lanes.

| wall clock | value |
|---|---|
| total run | **4 851.69 s = 80 min 52 s** (22:04:27Z → 23:25:39Z) |
| per question, wall (4 workers) | **97.0 s** |
| per question, summed stages | 379.9 s mean (ingest 375.5, retrieval 0.9, reader 1.5, judge 1.2) |
| ingest p95 | 717.9 s/question |

**The wall clock was dominated by local embedding under fleet load, not by the LLM calls.** Ingest
(750 points/question embedded by `bge-small` on 10 CPUs) averaged 375 s/question at load average
19–34, versus 156 s/question measured on the same slice in a quieter moment; the reader and judge
together are ~2.7 s/question, i.e. **0.7% of the run**. That is the single most important shape fact
for planning the 500-Q run (§7).

Failure accounting: `integrity.valid: true`, `n_attempted 50 / n_valid 50 / n_invalid 0`, zero
failures, empty error census, zero retries.

## 4. The two transport defects this run found — and fixed

Both are one root cause: **the only reachable provider's models now reason by DEFAULT, and the
benchmark's call shapes have fixed completion budgets.** A reasoning model spends that budget on hidden
reasoning and returns empty `content`. Both defects are invisible in a score — an empty answer is
graded as a wrong answer.

### 4.1 The judge would have graded EVERY question wrong

Measured with the official judge call shape (`n=1, temperature=0, max_tokens=10`) on the two DeepSeek
ids, both arms, `probes/judge-call-shape-2026-10-25.log`:

```text
=== deepseek:deepseek-flash   extra=-                              content: ''   finish: length
      usage: {"completion_tokens": 10, "completion_tokens_details": {"reasoning_tokens": 10}}
=== deepseek:deepseek-v4-pro  extra=-                              content: ''   finish: length
=== deepseek:deepseek-flash   extra={'thinking': {'type':'disabled'}}  content: 'yes'  finish: stop  completion_tokens: 1
=== deepseek:deepseek-v4-pro  extra={'thinking': {'type':'disabled'}}  content: 'yes'  finish: stop  completion_tokens: 1
```

The label rule is `'yes' in response.lower()`, so the empty-content arm returns `False` for **every**
question: a run under it would publish **QA accuracy 0.0000** — a transport artifact indistinguishable
from a catastrophic result, and precisely the kind of false measurement `#8486` must not produce.

**Fixed here:** `OfficialJudgeModel` sends the vendor's own documented `thinking:{"type":"disabled"}`
**only** when the configured endpoint's provider is in `THINKING_BY_DEFAULT_KEY_ENVS` (currently
`DEEPSEEK_API_KEY`). The official judge's request stays byte-identical to `evaluate_qa.py` (pinned by a
new assertion in `tests/test_longmem_runner.py`), and `report.py`'s `judge_call_shape` now states the
conditional addition. Precedent: `tortoise/analyze.py`'s `#1790` deepseek thinking-disable.

### 4.2 The reader silently answered nothing on 1 of 3 pre-fix questions

Same root, 500-token budget. In the 3-question probe run **before** the fix, question `0a995998`
(multi-session, 11 352 prompt tokens) came back with `completion_tokens = 500` and
`hypothesis = ""`, was recorded **`valid: True` with no error class**, and scored 0.

**Fixed here:** the eval's reader transport (`_ThinkingDisabledTransport` in
`tools/longmem_eval/reader.py`) sends the same control for that provider and prints a line saying so;
everything else about the reader request is the official gen.py shape (system+user, temperature 0,
`max_tokens` 500, no `response_format`). After the fix the same question produced a real answer
(68 completion tokens, 500 → 68).

⛔ **The silent-empty-answer *class* is NOT fixed — §8.1.**

### 4.3 The judge's endpoint now follows the provider its spec NAMES (found in review)

The two fixes above keyed the thinking-disable off the *resolved* endpoint's key. Review of this PR
found that the judge resolved that endpoint **by priority** (`openrouter` first) rather than by the
provider the spec names: on a box holding an `OPENROUTER_API_KEY`, a `judge: deepseek:deepseek-v4-pro`
spec would have been **served by OpenRouter** while the run's receipt named DeepSeek, and
`disables_thinking` would have been read off OpenRouter's key env — i.e. §4.1's unguarded gap would have
re-opened on exactly the box that has both keys, silently.

**Fixed here:** `build_judge` resolves with `_resolve_provider(named=provider)`, the same endpoint-truth
rule `build_reader` already applies (M5 #1525), and pins it with a factory-level test (the
direct-transport test could not see the divergence).

⚠️ **This fix does not change any number in this document, and no figure here was re-measured.** The
run was measured on a box with **only** `DEEPSEEK_API_KEY` set (`probes/README.md` fingerprint), where
priority order and the named provider resolve to the *same* endpoint (`api.deepseek.com/v1`) — so the
fix is a no-op for this run's arithmetic and a guard for every box that has an OpenRouter or OpenAI key
as well. Re-running the 50-question pilot to demonstrate that would cost ~80 minutes of wall clock and
another ~$0.062 for an identical result, so it was not re-run.

The report also now records the resolved endpoint as `methodology.judge_provider` (additive, exactly
like `reader_provider`), so the id and the endpoint cannot disagree in the artifact either. **The
report this document quotes predates that field** — it was produced on `4c63488`, which recorded
`judge_model: "deepseek-v4-pro"` and no provider; §2.2 states the endpoint from the run's own key
provenance instead.

## 5. Anti-gaming: the receipt this run produced is WRONG on `main` — and why it is published here

`w7_publish` on `main@4c63488` printed:

```text
[w7_publish] official_recall_all@5 = 0.78 (n=50)
[w7_publish] receipt valid: True
```

and wrote a receipt that says, about a run graded by DeepSeek:

```json
"judge_pin": "longmemeval-official-anscheck-gpt4o-2024-08-06-v1",
"judge_model": "deepseek-v4-pro",
"run_status": "completed", "verdict": "pass", "receipt_valid": true
```

⛔ **Read that block as a defect, not a result:** `judge: deepseek-v4-pro`, **therefore NOT comparable
to the published figures, which use `gpt-4o-2024-08-06`** — and on the commit that produced it the
receipt could not say so.

On this base the pin is a **constant** (`w7_publish.py:572` passes `JUDGE_PIN`, declared at `:92`), so
any substituted-judge run publishes under the OFFICIAL pin with `verdict: pass` and a clean receipt.
`validate_receipt` reports `[]`. **R12 — the "judge is pinned and never substituted" rule — is
enforced nowhere on the publication door at this commit**, and this is its first *real* (non-mock)
demonstration rather than a synthetic one.

**Why this receipt is committed at all:** as the artifact of that defect, not as a publication. It is
the evidence that the door would have dressed this number as an official gpt-4o measurement. The file
name carries that verdict — `w7a-pilot50.variants-and-receipt-INVALID-official-judge-pin.json` — and
its first key is a `_WARNING` saying the same thing, so neither a directory listing nor a reader who
opens the JSON can mistake it for a publication (§9.1 has the one-line diff from `w7_publish`'s own
output).

**Fixed on `main` while this run was in flight, not here:** PR **#9992** (`fix/8486-judge-pin-derived`)
merges at `2026-10-25T23:09:35Z` as `84887a613` and derives *both* the pin and the verdict from the
judge the report records — an allow-list, `unpinned:<model>` for anything else, and only an
official-pinned completed run may record `pass` (`tools/longmem_eval/w7_publish.py::judge_pin_for` on
`origin/main`). Re-implementing it here would duplicate another lane's work on the same rule, so this
lane reports it and does not.

The accurate present-tense statement is therefore about **historical artifacts, not `main`**: a receipt
produced by `w7_publish` *before* `28560a662` cannot be distinguished from an official-judge receipt —
which is why the one in this directory is named and marked INVALID rather than quietly re-pinned.

## 6. Comparability — what this number is, and is not

**One sentence:** *these are measurements of our own pipeline, produced end-to-end by the committed
harness on the official dataset with the official rubric, graded by `deepseek-v4-pro` instead of
`gpt-4o-2024-08-06` and reading a raw-content `deterministic` graph rather than the v2 belief layer, so
they are **not comparable** to the published LongMemEval-S figures (HydraDB's 90.79% overall, mem0's
own numbers) and must never be placed in that table.*

Each divergence, so a reader can discount in the right direction:

1. **Judge model — the decisive one.** Official `gpt-4o-2024-08-06`; here `deepseek-v4-pro`. The
   *rubric* is byte-identical (hash `82d07b0de05daa48`), the *grader* is not. Both directions of bias
   are possible (a stricter grader deflates, a more permissive one inflates) and this run measured
   neither — so the direction is **unknown**, not "conservative".
2. **Ingest mode.** `deterministic` writes turn points + raw chunks and **no operators/NANDs**, and the
   belief layer is `inert` (`posture.belief_layer: "inert"`, `questions_with_ep: 0`). This measures a
   raw-content retrieval baseline, **not** Tortoise's epistemic layer. Per this issue's own rule the
   ingest mode must be reported next to the number — it is, everywhere.
3. **Retrieval scope.** Isolated per-question corpus (each question's haystack in a fresh graph), not
   the official full-corpus indexing where every question searches every history. The harness's own
   `methodology.retrieval_scope` states this; recall numbers are therefore not paper-comparable by
   construction.
4. **Reader.** `deepseek:deepseek-flash` on the direct endpoint, `reader_pinned: false` — the pinned
   reader spec is `openrouter:deepseek/deepseek-v4-flash`, and there is no OpenRouter key here. Same
   model family, different route; the vendor states `deepseek-flash` is now served by
   DeepSeek-V4.1-Flash-1017.
5. **n and slice.** 50 type-stratified questions, not 500, and **no abstention questions** — so the
   95% CI on the overall accuracy is ±0.13 wide ([0.522, 0.776]) and one of five paper categories is
   unmeasured. **No ranking claim of any kind is supportable from n=50.**
6. **What IS official here:** the dataset (corpus hash re-verified), the answer-check rubric, the
   answer-key seal, the metric *formula* (`recall_all@5` computed by the committed `w7_publish`, with
   the `_abs` exclusion and the per-category gate rows), and the integrity/census machinery. The
   *instrument* is the benchmark's; only the grader is substituted.

## 7. What the full run would cost at the measured rate

| cell | LLM spend | wall clock | basis |
|---|---|---|---|
| **500-Q `deterministic`, this configuration** | **≈ $0.62** | **≈ 13.5 h** at the measured 97 s/question-wall (4 workers, fleet load 19–34); **≈ 10 h** at the quieter-box 73 s/question-wall measured in the same session; **≈ 2.5–3 h** only if the box is idle enough for ingest to run at its 156 s/question floor with ~8 workers | 10× this run's tokens; wall clock is ingest/embedding-bound, not LLM-bound (§3.3) |
| `v2` (extractor + `--activate-beliefs`) 500-Q | **not measured here** | **≈ 37.5 h single-worker** by the only committed datapoint (~4.5 min/question extraction, README §"Ingest cache + fast-cycle protocol", #2080); shardable, the state machine is resumable | extractor spend is token-heavy and unmeasured by this run; the issue's own ladder says "tens of dollars" |

**The recorded ladder's `$5 / 3.6 h` for the 500-Q cell does not match this measurement**, and the
difference is informative rather than a contradiction: the LLM spend came in **~8× lower** ($0.62 vs
$5 — the ladder appears to have assumed a pricier reader route), while the wall clock came in **~3×
higher** at fleet load (13.5 h vs 3.6 h). The 3.6 h figure corresponds to ~26 s/question-wall, which
this box reached only at ~156 s/question of pure ingest with ≥6 concurrent workers; at a load average of
19–34 the same work takes 375 s/question. **Wall clock for this benchmark on this box is a function of
embedding concurrency and fleet load, not of the benchmark.**

## 8. Found, not fixed (with the reason)

### 8.1 ⛔ An empty reader answer is a silent wrong answer — the integrity block certifies it

Measured pre-fix: a question whose reader call returned `content: ""` after burning its whole 500-token
budget was recorded `valid: True`, `error_classes: {}`, `llm_truncated: 0` and scored 0. Nothing in the
report distinguishes "the reader was starved / returned nothing" from "the reader answered and was
wrong", and `integrity.valid` is `true` either way. §4.2 removed the *cause* on this provider; the
**detection** does not exist, so the next transport change (a proxy, a refusal, a different reasoning
model) can re-open it silently. Not fixed here because the fix is a new census class
(`reader:empty_answer`) and therefore a change to the integrity contract, its rate-limit semantics and
a set of pinned report tests — a scope of its own, not a rider on a measurement lane.

### 8.2 ⛔ The publication door stamps the OFFICIAL judge pin on a substituted judge (§5)

Real, reproduced on this run, and **fixed on `main` by PR #9992 (`84887a613`, merged
`2026-10-25T23:09:35Z`, i.e. while this run was in flight)** — re-implementing it here would duplicate
another lane's work, so this PR does not touch the rule. Precise scope of what remains: *any* receipt
produced by `w7_publish` **before** `28560a662` claims the official gpt-4o pin regardless of who
graded, which is a property of those historical artifacts, not of `main` today. The receipt in this
directory is one of them, which is why it is named and marked INVALID.

### 8.3 The cost meter silently reports a partial total as the run total

`usage.cost.usd` = **$0.008456** while the run actually spent **$0.0620**. The reader lane is priced at
`usd: 0.0` with `priced: false, reason: "unknown_model"` because the served id `deepseek-flash` has no
entry in `PRICING_MAP` (it carries the retired `deepseek-v4-flash`, which DeepSeek documents as still
accepted and billed at the Flash price). So the field named `cost.usd` **excludes 87% of the spend**,
and `priced: false` is the only signal. Not fixed here: adding the id to the pricing map is a
versioned-rate assertion (the map's own discipline says an out-of-map lane is never guessed), and it
belongs with a verification of the vendor's current rate card rather than inside a measurement lane.
The arithmetic used instead is in `probes/README.md`. The same unpriced-model hole is why the report's
`usage.overhead.cost_usd` (`7.6e-05`) counts only its judge lane (§3.3): pricing both overhead lanes
gives $0.0620, not $0.06192.

### 8.4 `tools/run-with-eval-keys.sh` cannot be used on this box

The documented way to make a run's key provenance explicit *strips the ambient provider keys and loads
the repo `.env` with override* — and this repo's `.env` contains **no provider key** (one
`BLOG_AGENT_KEY` only). Running through the wrapper would therefore delete the only working key. The
run instead billed the ambient `DEEPSEEK_API_KEY` (fingerprint in `probes/README.md`), which is exactly
the key-provenance fog the wrapper exists to prevent. Not fixed: the wrapper is correct for a box whose
`.env` holds the eval keys; the defect is the assumption, not the code.

### 8.5 Gemini is not a usable fallback judge on this key

`gemini-2.5-flash` / `-pro` / `-flash-lite` now answer **404 "no longer available to new users"**
(redirecting to 3.x), and the 3.x models on this key answer **429 quota-exceeded** or **503 high
demand** (`probes/probe_judge_raw.py` output). Recorded so the next lane does not rediscover it as a
"viable alternative judge".

### 8.6 37 539 stderr warnings of one kind in one 50-question run

`create_point received non-ULID id='lme:<qid>:s<n>:c<m>'` is emitted per ingested point (~750/question)
by `#4129`'s id guard, producing an **8 MB log** for a 50-question run. It is warning noise on a
deliberate, documented eval id scheme, not a defect — noted because it makes the run log unusable for
anything but grep and it inflates the artifact size, which matters for a 500-question run.

---

## 9. Artifacts in this directory

| file | what it is |
|---|---|
| `pilot50-summary.log` | verbatim stdout summary of the run (integrity, score, latency, methodology), extracted from the 8 MB log with the `create_point` warning lines removed |
| `per-question-outcomes.tsv` | the **50 per-question rows** every aggregate in this document is computed from — `judge_label` (33 `True`) and `session_recall_at_5` (39 rows at `1.000000`) — so `0.66`, `0.78`, `0.8867` and `0.4848` are recomputable from the repository alone (§9.2) |
| `w7a-pilot50.variants-and-receipt-INVALID-official-judge-pin.json` | `w7_publish` output: official-metric variants, per-category gate rows, answer-key seal and the receipt — **committed as evidence of §5 / §8.2, NOT as a publication**. `judge: deepseek-v4-pro`, therefore **NOT comparable to the published figures, which use `gpt-4o-2024-08-06`**; the name and the `_WARNING` key say so inside the file too |
| `probes/probe_judge_raw.py` | the judge-transport falsifier (both arms: default and `thinking:disabled`), runnable |
| `probes/judge-call-shape-2026-10-25.log` | that probe's output — the §4.1 measurement |
| `probes/probe_judge.py` | the label-level probe (4 sanity cases, 4 candidate models) |
| `probes/build_pilot_slice.py` | the exact slice builder (committed selector, into a scratch dir) |
| `probes/README.md` | how to reproduce each probe, and the key-provenance fingerprint |

### 9.1 The INVALID receipt is `w7_publish`'s output plus exactly ONE line

The artifact was renamed and marked, **not rewritten**. Its body is the tool's own output: dropping
the single inserted `_WARNING` line reproduces the original file byte-for-byte, and that digest is
what a fresh `w7_publish` run on this run's report produces (verified by re-running the committed tool):

```bash
cd docs/evidence/2026-10-25-longmemeval-pilot-s
{ head -1 w7a-pilot50.variants-and-receipt-INVALID-official-judge-pin.json
  tail -n +3 w7a-pilot50.variants-and-receipt-INVALID-official-judge-pin.json
} | shasum -a 256
# 7d9166553108d81988e2df561fcdcf45e415c665e8a4e172d19b2dd1bda1000a
```

Nothing inside `receipt` was edited — `judge_pin`, `verdict: "pass"` and `receipt_valid: true` are
**the defect's real output**, kept as evidence (§5, §8.2), which is exactly why the marker is needed:
`validate_receipt` on this file still returns `[]`.

### 9.2 Re-deriving every aggregate from `per-question-outcomes.tsv`

The runner's report (`pilot50.report.json`) lived in a scratch `/tmp` directory and is **not**
committed; the per-question rows it produced are. The accuracy and retrieval figures in §3.1 and §3.2
(and every per-type cell behind them) derive from those rows:

```bash
cd docs/evidence/2026-10-25-longmemeval-pilot-s
python3 -c '
import csv
rows=[r for r in csv.DictReader((l for l in open("per-question-outcomes.tsv")
                                if not l.startswith("#")), delimiter="\t")]
fr=[float(r["session_recall_at_5"]) for r in rows]
bad=[x for x in fr if x < 1.0]
print("QA            ", sum(r["judge_label"]=="True" for r in rows), "/", len(rows))   # 33 / 50
print("recall_all@5  ", sum(int(r["recall_all_at_5"]) for r in rows), "/", len(rows))  # 39 / 50
print("mean over all ", round(sum(fr)/len(fr), 4))                                    # 0.8867
print("n imperfect   ", len(bad), "mean", round(sum(bad)/len(bad), 4))                # 11 mean 0.4848
'
```

That prints `33 / 50`, `39 / 50`, `0.8867`, `11 mean 0.4848` — the four figures the headline and §3.2
quote, with **no dependence on the uncommitted report** and no hand arithmetic. The two means are over
different populations: `0.8867` is over all 50 questions, `0.4848` over the 11 imperfect ones.

One caveat about the TSV: `recall_all_at_5` is the *official* all-or-nothing binary — exactly `1` when
`session_recall_at_5 == 1.000000` and `0` otherwise (0 mismatched rows in all 50) — while the fraction
is the labelled per-question variant. That is why the two aggregate means differ: the official metric
gives no credit to a partially-retrieved question, the fraction does. Verify the column agreement
itself in one line:

```bash
python3 -c 'import csv; rows=[r for r in csv.DictReader((l for l in open("per-question-outcomes.tsv") if not l.startswith("#")), delimiter="\t")]; print(sum((float(r["session_recall_at_5"])==1.0)!=(r["recall_all_at_5"]=="1") for r in rows))'   # 0
```
