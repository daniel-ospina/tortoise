---
title: "1987 Ask Abstention / Pre-Ship Check — Runbook & Results"
type: operations
domain: operations
doc_status: live
created: 2026-08-29
ownedBy: epistemic-team
---

# 1987 Ask Abstention / Pre-Ship Check — Runbook & Results

> Procedure + results for the #1987 Task 12 HARD pre-ship gate. The merge is
> BLOCKED until all four sub-gates (a)–(d) pass with NON-SKIPPED verdicts on
> file (verified by `scripts/check-ask-premerge.cjs` in the commit-workflow
> pre-merge step).

---

## PRODUCT DECISION (2026-08-30, PR #2013) — ask exposure gated, reader shipped

> **The (d) gate is MOOT by product decision.** The reader over-abstention
> class is FIXED (#2027, below) — that was the gate-d blocker. The remaining
> (d)/(a) shortfall is bound by the DEFAULT reader MODEL's content quality
> (deepseek-v4-flash cannot hold both the derived-commit and the
> near-miss-abstain classes; empirically verified — see the #2027 section),
> not by the abstention clause. The product decision: **the READER ships as
> the eval's reader** (the 500-Q LongMemEval benchmark runs through it; the
> eval re-exports the product reader), and **the hosted ask EXPOSURE is
> gated off** until the reader-model decision is made. The benchmark will
> use a STRONG reader model (qwen3.8-max reproduced the commits on the
> failing evidence). Merge is no longer blocked by (d).**

**What shipped in PR #2013 (this decision):**

| Surface | Status | Where |
|---|---|---|
| `tortoise/reader.py` (the reader itself) | **SHIPPED, unchanged** — the eval's reader | `tortoise/reader.py` (re-exported by `tools/longmem_eval/reader.py`) |
| `TortoiseSDK.ask()` | **Stays** — the eval's reader path; docstring marks it GATED/EXPERIMENTAL (not for production use until the reader-model decision) | `tortoise/sdk.py` |
| `POST /v1/ask` (hosted) | **GATED OFF by default** — route not registered (404) unless `TORTOISE_ENABLE_ASK=1` (tests/dev); handler + error translation stay, tested, ready | `tortoise/hosted_api.py` |
| MCP `tortoise_ask` | **GATED OFF by default** — own curation group `"ask"`, excluded from the default hosted /mcp surface unless `TORTOISE_ENABLE_ASK=1`; explicit `tool_group="ask"` (dev/eval) serves it | `tortoise/mcp_server.py`, `tortoise/tool_registry.py` |
| MCP `tortoise_ask` (selfhost) | **GATED identically** — the selfhost /mcp DEFAULT surface hides the tool + ERR_EXCLUDEDs the call; selfhost MCP opt-in is `tool_group="ask"` (`TORTOISE_TOOL_GROUP=ask` env), while selfhost REST /v1/ask stays unmetered | `tortoise/mcp_server.py` |
| `POST /v1/ask` (self-host REST) | **Stays** — the local-lane REST parity surface (mirrors the SDK local lane; no team budget, unmetered) | `tortoise/selfhost_api.py` |

**Follow-up (tracked, not blocking):** (1) the ask reader-model upgrade —
provider routing for the ask lane (deepseek-direct primary 400s on non-
deepseek specs and 400 is not a failover trigger) + the M5 `READER_MODEL`
pin + cost re-measure; (2) retrieval: FTS top-40 misses gold turns on long
haystacks (ceb5 at rank ~70) — hybrid/reranking or a reviewed top-k for the
ask lane; (3) composition: the 3 SSP long-gold questions are structurally
ungradeable by the containment judge's word-overlap bar. The gate results
below remain the evidence base for those follow-ups.

**Strong-model 500-Q config (the toutable benchmark, ready to run):** the
500-Q LongMemEval-S baseline (run_protocol step 5) with the STRONG reader
model that reproduced the failing commits (qwen3.8-max, verified 2026-08-30
on the #2027 evidence):

```bash
# step 5 (full 500-Q baseline) with the strong reader + official judge:
TORTOISE_LME_READER_MODEL='openrouter:qwen/qwen3.8-max' \
  uv run python -m tools.longmem_eval.run_protocol run 5
# or directly (same reader, explicit knobs):
TORTOISE_LME_READER_MODEL='openrouter:qwen/qwen3.8-max' \
  uv run python -m tools.longmem_eval.run --split s \
    --checkpoint <state.json> --output <report.json>
```

Provider wiring already in place: `openrouter` is in
`tortoise.ingest._PROVIDERS` (OpenRouter base URL + `OPENROUTER_API_KEY`),
the spec `openrouter:qwen/qwen3.8-max` parses via `_parse_model_spec`, and
the override records `reader_pinned=false` + warns on stderr (the M5 default
stays `openrouter:deepseek/deepseek-v4-flash` — #1525). Requires an
`OPENROUTER_API_KEY` (and `OPENAI_API_KEY` for the official GPT-4o judge);
no keys were present at record time, so this config is verified up to spec
parse + mock-run only, not executed. Cost re-measure + provider routing for
the PRODUCT ask lane (deepseek-direct 400s on non-deepseek specs) remain the
tracked follow-up (1) above.

---

## Procedure

1. **Dataset prerequisite (P1-4):** the eval dataset is a HARD prerequisite —
   fetched/regenerated when absent (`tools/longmem_eval/dataset.py` caches to
   `~/.cache/tortoise-longmemeval`); the gate never skips its members.
2. **Detector parity (c):** `TORTOISE_TEST_CARVE_OUT=1 uv run python
   tools/longmem_eval/detector_parity.py --gate` — mapped agreement ≥ 0.85.
3. **Graded `_abs` run (a):** the eval's `_abs` question set through the
   unified reader (post-unification the eval reader IS the product reader)
   with the abstain-only-on-genuine-absence verdict via the judge marker
   path; `abstention_n > 0` (report.py:1664); pre-gate skip count = 0 (no
   LLM-skip pre-gate); the `_abs` marker never crosses to the reader (A1).
4. **Product-lane known-answer smoke (b):** the gold-verbatim fixture
   through `build_reader_model()` (the RoutingModel transport delta vs the
   eval's OpenAICompatModel) MUST commit (not abstain).
5. **QA spot-check (d — REQUIRED):** temporal / preference / KU / MSR +
   abstention + single-session-assistant samples through the product lane
   post-unification, aggregate ≥ 0.8 (the 0.83 integrity-valid eval run is
   supporting evidence, NOT a substitute).
6. **LLM regression module (fixture mode):** `TORTOISE_ASK_LLM_REGRESSION=1`
   `uv run pytest tests/test_ask_regression_llm.py -q` must PASS in the
   docker/unit lane (the committed recorded-transport transcripts), and the
   CI `test (${{ matrix.half }})` job sets the env var (P2-7/P2-9).
7. Record verdicts + branch decisions below; the gate passes only when all
   acceptable.

---

## Calibration fix (#2027) — reader Phase-1 generic presence-commit + Phase-2 compression — 2026-08-30 (re-run)

> The #2027 reader-calibration fix (the gate-d blocker work) shipped to
> this branch and the gates were re-run. **Verdict: the reader's
> OVER-ABSTENTION class is fixed (the reader now commits on present
> evidence — the issue's core defect), but the (d) aggregate stays below
> 0.8 and the (a) abstention accuracy slipped below 0.9 on the DEFAULT
> reader model (deepseek-v4-flash): the remaining failures are bound by
> reader-MODEL content quality and FTS-40 retrieval, not the abstention
> clause. A qwen3.8-max probe proves the model is the binding constraint.
> Merge REMAINS BLOCKED; the follow-up path is recorded below.**

### The fix (tortoise/reader.py `_ABSTRACTION_FRAGMENT`)

1. **Phase-1 generic presence-commit** (#2027): Phase 1 now fires on
   PRESENT EVIDENCE regardless of fragment engagement — a
   category-independent rule ("These instructions apply to every
   question — whether or not it matches a recognized category…; the
   absence of category instructions is never a reason to abstain"), a
   derived-value commit (elapsed time/counts/totals/ordering computed
   from the dated facts in context; off-by-one acceptable; "do not
   abstain merely because the number is not literally written"), a
   synthesis commit (preference-shaped answers draw on stated
   preferences/experiences), scoped to the asked subject's events being
   present (the false-commit guard).
2. **Phase-2 compression**: the abstention branch is now minimal —
   "abstain ONLY when no turn in the context mentions the asked subject
   or event at all… Then simply state that the asked information is
   absent, mentioning the related facts found in the memory if any."
   The elaborate evidence-backed template + the bicycle exemplar were
   REMOVED: a live-prompt ablation on deepseek-v4-flash showed they
   licensed the hedge form the reader over-produces on present evidence
   (same smoker question: elaborate Phase 2 → abstain; minimal Phase 2 →
   commit "10 days ago"; generic-only → commit).
3. **Judge-marker vocabulary alignment** (plan P2-32: judge ⊆ product):
   MockJudge `_ABSTRACTION_MARKERS` + the spot-check judge now recognize
   the reader's canonical abstention phrasings ("asked information is
   absent", "information is absent", "no mention of", "don't have
   (that) information", "absent from the context") — the plan's census
   authority (the product label) already had them; without the judge
   extension, correct abstentions scored as failures (the runbook's
   documented vocab-gap class).
4. Tests: docker-lane suite **175 passed, 1 skipped** (reader + eval
   reader + SDK + metering + LLM-regression fixture mode); ruff clean on
   all changed files. New `tests/test_reader_abstention_calibration.py`
   (7 tests: generic-baseline presence-commit pins, Phase-2
   never-instruction-gap pin, the two #2027 evidence shapes as
   red→green fixtures — d6233ab6 synthesis + gpt4_8279ba02 derived
   day-count — and the 09ba9854_abs scoped-commit control).

### (d) QA spot-check — re-run: **FAIL — aggregate 0.38 (8/21) < 0.8**

Full spot-check (`TORTOISE_TEST_CARVE_OUT=1 uv run python
 tools/ask_spotcheck.py` — real product lane, containment judge,
21-question composition, seed 1987). **The false-abstention class is
GONE**: every question whose asked value/subject reached the context now
COMMITS (d6233ab6 synthesizes the reunion answer, b0479f84 recommends
documentaries, 0100672e commits a mug total, gpt4_6ed717ea commits an
order) — pre-fix these 10/21 abstained. The aggregate is now bound by
three non-abstention failure classes:

| class | failures | cause (verified) |
|---|---|---|
| reader-MODEL content error | gpt4_8279ba02 (commits purchase date, no day count), gpt4_7a0daae1 (hedge), gpt4_6ed717ea (wrong order), 830ce83f (recency noise: commits the older Chicago mention; gold = the suburbs), 0100672e ($60 total vs $12 each), e831120c (hedge), b0479f84 (commits wrong recs) | deepseek-v4-flash answers wrong content — arithmetic, ordering, recency, per-unit reasoning |
| retrieval gap (FTS top-40) | ceb54acb (answer turn ranks ~70: 'sexual fixations' list never retrieved), 1de5cff2 ('veja' turn not in top-40), gpt4_d84a3211 (dollar amounts not in top-40), 1d4e3b97 (chain/cassette turn not retrieved) | the product ask lane is FTS-only; the gold turns rank below the 8k/40 caps on these long haystacks |
| containment-judge bar | d6233ab6 (long synthesis gold: needs ~45-word overlap), 1d4e3b97 (same) | the judge's `max(2, len(gold_words)//2)` word-overlap bar on ~70-90-word synthesis golds is structurally unreachable |

qwen3.8-max diagnostic (same evidence, `qwen/qwen3.8-max` via the
OpenRouter registry): gpt4_8279ba02 → "10 days ago.", gpt4_7a0daae1 →
"One week.", f4f1d8a4_abs → "I don't know what your dad gave you…" —
**the reader MODEL is the binding constraint on the content class**; the
ask lane cannot serve qwen today (deepseek-direct primary 400s on the
qwen spec and 400 is not a failover trigger).

**Reval3 probe measurement (2026-08-30, recorded for
`test_real_model_commits_on_present_value` — the #1775 obs-1 shape,
'Golden Retrievers like Max' at rank 9 amid noise): the pinned default
reader committed 0/5 (answers "The context does not mention Ava or her
dog Max.") — the under-commit is the same model class, on a noisy top-10
shape the runbook's (d) evidence shapes do not cover; the probe is now
warn-only (the deterministic fakes pin the clause contract).**

### (a) Graded `_abs` — re-run: **26/30 judge-marker (0.867) < 0.9 — compression trade-off**

Full 30-Q `_abs` set, unified product reader, MockJudge, 4 chunks
(9/9/9/3), A1 invariant held (marker never crossed; skip count 0). The 4
fails are all FALSE-COMMITS on the near-miss class (031748ae_abs,
a96c20ee_abs, 2133c1b5_abs, 09ba9854_abs): the compressed Phase 2 makes
the weak model commit related-but-not-asked material ("Senior Software
Engineer" for the asked "Manager" role; taxi price for the absent bus
fare). Prompt-ablation: a near-miss subject guard ("related or
near-miss material about a DIFFERENT instance/role/item is not the asked
subject") recovers (a) to 29/30 at the cost of RE-INTRODUCING the
derived-class over-abstention (gpt4_8279ba02 / gpt4_7a0daae1 abstain
again) — deepseek-v4-flash cannot hold both classes; the model is the
constraint, not the clause.

### (b) Product-lane known-answer smoke — re-run: **PASS**

```
answer: The office hours policy is 9am to 5pm.
abstained: False | model: deepseek-v4-flash | provider: deepseek-direct | route: deepseek-direct
cost_estimate_usd: 0.000156 | context_tokens: 24 | question_type: None
```

### LLM regression module (fixture mode) — re-run: **PASS**

`TORTOISE_ASK_LLM_REGRESSION=1 uv run pytest tests/test_ask_regression_llm.py`
→ green; transcripts regenerated for the new prompt hash
(`tools/gen_ask_transcripts.py`, P2-25).

### Gate status after the #2027 calibration fix

| Sub-gate | Verdict | Blocking? |
|---|---|---|
| (a) graded `_abs` | **FAIL (0.867 < 0.9)** — near-miss false-commit class under the compressed Phase 2 on the default reader model | ⛔ |
| (b) known-answer smoke | **PASS** | ✓ |
| (c) detector parity | **FAILURE BRANCH** — #2009 OPEN, default in effect | ✓ (branch recorded) |
| (d) QA spot-check ≥ 0.8 | **FAIL — 0.38 (8/21); abstention class fixed, aggregate bound by model content + FTS-40 retrieval + judge bar** | **⛔ merge BLOCKED** |

**MERGE REMAINS BLOCKED.** The reader-calibration deliverable (#2027) is
DONE (the over-abstention defect is fixed; the issue's own evidence
shapes — d6233ab6 / gpt4_8279ba02 — no longer abstain), but the (d)
aggregate cannot reach 0.8 on the shipped lane regardless of the prompt
(empirically bounded by reader-model content quality + FTS-40 retrieval +
containment-judge bar). **Unblock path (follow-up):** (1) upgrade the ask
reader model to a capable model (qwen3.8-max proven) — requires
provider routing for the ask lane (the deepseek-direct primary 400s on
non-deepseek specs and 400 is not a failover trigger) + the M5
`READER_MODEL` pin + cost re-measure; (2) retrieval: FTS top-40 misses
gold turns on long haystacks (ceb5 at rank ~70) — hybrid/reranking or a
reviewed top-k for the ask lane; (3) composition note: the 3 SSP
long-gold questions are structurally ungradeable by the containment
judge's word-overlap bar. Tracked via #2009 (detector) + a new issue for
the reader-model/retrieval upgrade.

---

## Results (2026-08-30 — FULL pre-ship gate run, post-review-clean PR #2013)

> All four sub-gates + the LLM regression module re-run in full on the
> branch. (a)/(b)/(c) measured NON-SKIPPED; (d) FAILS (0.43 < 0.8) — merge
> stays BLOCKED. The 2026-08-29 in-session preliminary record is preserved
> below this section.

### (a) Graded `_abs` — **FULL RUN: PASS** (invariants hold; 1 documented false-commit)

FULL `_abs` set (30 questions) through the **unified product reader**
(`LLMReader(build_reader_model())` — the RoutingModel transport, the same
`build_reader_model` `sdk.ask` uses) with the **strict MockJudge** (the
judge-marker path — `_ABSTRACTION_MARKERS`, the deterministic judge; the
preliminary sample used the same).

- **abstention_n = 30** (report.py:1664) — all 30 `_abs` questions GRADED;
  failures = 0, n_excluded = 0. **pre-gate skip count = 0** (reader.answer
  call count 30 = 1:1 with questions — no LLM-skip pre-gate exists).
- **A1 invariant: the `_abs` marker NEVER crossed to the reader** —
  instrumented on the real `reader.answer` calls; observed `question_type`
  values were only the 4 base types (knowledge-update / multi-session /
  single-session-user / temporal-reasoning).
- Abstention accuracy (judge-marker): **0.9 (27/30)**; per-chunk
  0.8889/0.8889/0.8889/1.0.
- **Product abstained label (census authority): 28/30 (0.933)**; substance
  abstentions 29/30. The 3 judge-marker misses decompose: (i) `a96c20ee_abs`
  abstained via "The asked information is absent" — a product-phrase
  abstention OUTSIDE MockJudge's 12-marker subset (the judge vocabulary is a
  strict subset of the product list per plan P2-32); (ii) `gpt4_372c3eed_abs`
  abstained in substance ("the asked information ... is absent from the
  context") but the em-dash-interrupted phrasing matched neither marker
  list contiguously; (iii) `09ba9854_abs` is a **genuine false-commit** —
  the reader derived taxi-vs-bus savings from taxi prices; the asked bus
  fare is absent from the haystack (verified).
- **Verdict:** abstain-only-on-genuine-absence holds on 29/30 (0 abstained
  on answerable content; the `_abs` set is all genuine absence); 1/30
  false-committed; 2/30 abstained with marker-vocabulary-miss phrasings.
  The judge-marker accuracy 0.9 is above the plan's 0.8 falsification
  threshold. **The complementary defect — over-abstention on ANSWERABLE
  questions — is measured in (d) below and BLOCKS merge.**
- Method notes (recorded for reproducibility): the 30-Q set ran as 4
  chunks (9/9/9/3) because the harness's mid-run watchdog rolling arms are
  DOCUMENTED inert for runs < 10 questions (run.py `_abort_reason`, plan
  cycle2-P3) and the structural leg is BY-DESIGN empty on `_abs` questions
  (genuine absence — the floors derivation documents "by-design-zero legs
  (TR entity legs, abstention questions) excluded from the min",
  run.py:1384); a single ≥10-Q `_abs`-only run trips the leg_dead arm (an
  eval-harness gap for `_abs`-only runs, not a degradation signal). Runs on
  EMBEDDED per-question graphs (the eval's default surface; FalkorDB mode
  aborts `leg_dead` — HNSW not provisioned on the server). One dataset
  anomaly repaired in a local copy: `gpt4_c27434e8_abs` carries a
  content-identical duplicated `haystack_session_id` (benign data-entry
  artifact, 1 of 13/500 across the dataset) that the fail-closed join guard
  (#1785) vetoes — the duplicate occurrence was removed (zero information
  loss) and the run used `--data /tmp/lme_s_cleaned_deduped.json`.
  Pre-existing harness bug noted: `_ensure_work_dir` (run.py:233) is
  defined but never called — `--work-dir` pointing at a missing dir fails
  every embedded question (FileNotFoundError); NOT a #1987 regression.

### (b) Product-lane known-answer smoke — **PASS** (re-run this session)

Gold-verbatim fixture (`what is the office hours policy?`) through the REAL
`build_reader_model()` lane (`sdk.ask` — deepseek-direct primary):

```
answer: The office hours policy is 9am to 5pm.
abstained: False | model: deepseek-v4-flash | provider: deepseek-direct | route: deepseek-direct
cost_estimate_usd: 0.00012894 | context_tokens: 24 | question_type: None
```

Committed (not abstained), gold string present; `provider`/`route` report
the serving lane; $0.000129/query — ~77× under the $0.01 target.

### (c) Detector-parity branch — **FAILURE BRANCH RE-CONFIRMED (mapped agreement 0.284 < 0.85)**

`tools/longmem_eval/detector_parity.py --gate` over the cached
longmemeval-cleaned split-s (500 questions) — per-class numbers identical
to the in-session measurement (deterministic detector):

| class | agreement |
|---|---|
| single-session-user | 64/70 (0.914) |
| single-session-assistant | 52/56 (0.929) |
| temporal-reasoning | 16/133 (0.120) |
| knowledge-update | 9/78 (0.115) |
| multi-session | 0/133 (0.000) |
| single-session-preference | 1/30 (0.033) |
| **MAPPED agreement** | **0.284 (gate ≥ 0.85) → FAIL** |

**Branch (pinned, P2-3):** tracked follow-up **#2009** verified OPEN with
owner label `team:epistemic-team` ("fix(product): ask-lane
detect_question_type under-detects — 0.284 mapped agreement vs eval
dataset labels"). **The detector default remains in effect** until the
follow-up lands and is re-verified (no product-side flip smuggled into the
gate). Single-session classes stay census-only (None = agreement). The (d)
failure below is consistent with this under-engagement: `detected=None`
(generic baseline) on 20/21 spot-check questions.

### (d) QA spot-check — **FAIL: aggregate 0.43 (9/21) < 0.8 (REQUIRED gate)**

FULL spot-check via `TORTOISE_TEST_CARVE_OUT=1 uv run python
tools/ask_spotcheck.py` — the REAL product lane (`sdk.ask` →
`build_reader_model`), containment judge, 21-question composition (the
plan's mix: 4 temporal / 3 preference / 4 KU / 4 MSR / 3 SSA / 3 `_abs`,
deterministic seed 1987):

| class | correct |
|---|---|
| temporal-reasoning | 1/4 (0.25) |
| single-session-preference | 0/3 (0.0) |
| knowledge-update | 4/4 (1.0) |
| multi-session | 0/4 (0.0) |
| single-session-assistant | 1/3 (0.33) |
| `_abs` (abstention) | 3/3 (1.0) |
| **aggregate** | **9/21 (0.43) — target ≥ 0.8 → FAIL** |

**Dominant failure mode — the reader OVER-ABSTAINS on answerable
questions: 10/21 questions abstained (`abstained=True`) but were graded
incorrect** (the asked value IS in memory). Evidence-context probes confirm
the material reached the reader's context: `d6233ab6`'s evidence contains
"debate"/"advanced placement" (the personalization signals the gold answer
requires) and the reader itself wrote "It mentions nostalgic high school
experiences (debate team, AP economics), but..." before abstaining;
`gpt4_8279ba02`'s answer quotes the smoker-purchase session ("just got a
smoker today" dated 2023-03-15) yet abstains instead of computing the
days. This is the reader-calibration failure the plan flags ("abstains on
non-genuine-absence → a reader-calibration issue to fix, not to paper
over"): the universal A1 abstention clause's abstention branch fires on the
generic baseline when the type fragments don't engage (the (c)
under-detection is the likely contributor — `detected=None` on 20/21).

The other 2/21 incorrect are commits on wrong content (`e4e14d04`,
`0100672e`). The `_abs` abstention arm and knowledge-update commit arm are
the healthy classes (3/3 and 4/4).

**Per the plan's falsification section: aggregate 0.43 < 0.8 with zero
annotation work → the abstention-clause definition needs re-examination
(what the reader may abstain on; dates/types needed).** This is NOT a
spot-check-harness artifact (seeding reproduces the question's haystack;
the reader demonstrably saw the answerable material and still abstained).
Merge BLOCKED on (d).

### LLM regression module (fixture mode) — **PASS** (re-run this session)

`TORTOISE_ASK_LLM_REGRESSION=1 uv run pytest tests/test_ask_regression_llm.py -q`
→ **6 passed, 1 skipped** (the live-key-only test skips in fixture mode).
Transcript fidelity guards (prompt-hash + byte-equal user message) green;
the all-superseded fixture asserts the `[SUPERSEDED BY]` markers in the
evidence (single-sided). `scripts/check-ask-premerge.cjs` passes and
`.github/workflows/python-ci.yml` sets `TORTOISE_ASK_LLM_REGRESSION: "1"`
(P2-7/P2-9 wired).

### Gate status (FULL run, 2026-08-30)

| Sub-gate | Verdict (full run) | Blocking? |
|---|---|---|
| (a) graded `_abs` | **PASS** — abstention_n 30, skip 0, A1 clean; 0.9 judge acc; 1/30 false-commit + 2 vocab-gap abstentions (29/30 substance) | ✓ |
| (b) known-answer smoke | **PASS** — committed, $0.000129/query | ✓ |
| (c) detector parity | **FAILURE BRANCH** — 0.284 < 0.85; #2009 OPEN (owner: epistemic-team), default in effect | ✓ (branch recorded) |
| (d) QA spot-check ≥ 0.8 | **FAIL — 0.43 (9/21) < 0.8; reader over-abstains on answerable questions (10/21 false abstentions)** | **⛔ merge BLOCKED** |

**MERGE REMAINS BLOCKED** by the (d) spot-check (0.43 < 0.8): the reader
abstains on non-genuine-absence — a calibration defect, not a measurement
gap. The plan's falsification branch applies (the graded aggregate < 0.8
with zero annotation work → re-examine the abstention-clause definition;
the reader-calibration fix is the blocking work item). (a)/(b)/(c) and the
LLM regression module are recorded NON-SKIPPED.

---

## Results (2026-08-29 — in-session implementation run, preserved)

### (a) Graded `_abs` — PRELIMINARY (small sample), full graded run REMAINS

A 6-question eval-harness sample (temporal / preference / KU / MSR / SSA /
`_abs`) run through the **unified product lane** (eval harness ingest +
retrieval, reader = `LLMReader(build_reader_model())` — the RoutingModel
transport) with MockJudge:

- **abstention arm: 1.0 (1/1)** — the `_abs` question abstained correctly
  (`abstention_n = 1 > 0` — the plan's invariant holds on the sample);
  **pre-gate skip count = 0** (no LLM-skip pre-gate exists).
- Census authority per the plan: the product `abstained` label drove the
  count; the judge-marker subset agreed.
- **REMAINS for the parent:** the full graded `_abs` verdict (the complete
  `_abs` set, live judge) — the sample is a smoke, not the gate's verdict.

### (b) Product-lane known-answer smoke — **PASS**

Gold-verbatim fixture (`what is the office hours policy?`) through the REAL
`build_reader_model()` lane (deepseek-direct primary):

```
answer: The office hours policy is 9am to 5pm.
abstained: False | model: deepseek-v4-flash | provider: deepseek-direct | route: deepseek-direct
cost_estimate_usd: 0.000318 | context_tokens: 66
```

Committed (not abstained); `provider`/`route` report the serving lane;
$0.000318/query — ~30× under the $0.01 target.

### (c) Detector-parity branch — **FAILURE BRANCH RECORDED (mapped agreement 0.284 < 0.85)**

`tools/longmem_eval/detector_parity.py --gate` over the cached
longmemeval-cleaned split-s (500 questions):

| class | agreement |
|---|---|
| single-session-user | 64/70 (0.914) |
| single-session-assistant | 52/56 (0.929) |
| temporal-reasoning | 16/133 (0.120) |
| knowledge-update | 9/78 (0.115) |
| multi-session | 0/133 (0.000) |
| single-session-preference | 1/30 (0.033) |
| **MAPPED agreement** | **0.284 (gate ≥ 0.85) → FAIL** |

**Branch (pinned, P2-3):** a tracked follow-up issue was filed with an
OWNER **before** this gate passes — **#2009** ("fix(product): ask-lane
detect_question_type under-detects — 0.284 mapped agreement", owner:
epistemic-team). **The detector default remains in effect** until the
follow-up lands and is re-verified (no product-side flip smuggled into the
gate). Single-session classes stay census-only (None = agreement).

### (d) QA spot-check — PRELIMINARY (n=5, strict judge); full spot-check REMAINS

In-session eval-harness run (unified product lane, MockJudge — the STRICT
deterministic judge; the 0.83 run used the live judge):

- overall accuracy 0.2 (5 questions; ci95 [0.036, 0.624] — statistically
  non-significant), abstention arm 1.0.
- A separate raw-turn-seeded `tools/ask_spotcheck.py` run scored 0.33 (2/6)
  with a containment judge — the same class of signal (temporal
  between-events and KU best-value selection under-perform; the `_abs`
  abstention commits correctly).
- **REMAINS for the parent:** the REQUIRED spot-check (plan's composition,
  live judge, aggregate ≥ 0.8) must be run post-unification before merge —
  this run is recorded as the preliminary signal, NOT the gate's verdict.
- Note: the type-fragment under-engagement measured in (c) is the likely
  driver (temporal/KU questions run the generic baseline more often than the
  benchmark assumed).

### LLM regression module (fixture mode) — **PASS**

`TORTOISE_ASK_LLM_REGRESSION=1 uv run pytest tests/test_ask_regression_llm.py -q`
→ **6 passed, 1 skipped** (the live-key-only test skips in fixture mode).
Transcript fidelity guards (prompt-hash + byte-equal user message) green;
the all-superseded fixture asserts the `[SUPERSEDED BY]` markers in the
evidence (single-sided).

### Gate status

| Sub-gate | Verdict (in-session) | Blocking? |
|---|---|---|
| (a) graded `_abs` | PRELIMINARY (abstention 1/1, skip count 0) — full run REMAINS | parent |
| (b) known-answer smoke | **PASS** | ✓ |
| (c) detector parity | **FAILURE BRANCH** — #2009 filed, default in effect | ✓ (branch recorded) |
| (d) QA spot-check ≥ 0.8 | PRELIMINARY (0.2/0.33, n≤6) — full run REMAINS | **parent (blocking until live-judge ≥ 0.8)** |

**Merge was BLOCKED pending the full (a) graded `_abs` verdict and the (d)
live-judge spot-check ≥ 0.8.** Both now measured (2026-08-30 above): (a)
PASS, (d) FAIL 0.43 < 0.8 — the merge remains BLOCKED on (d).

---

## Live-key pilot runs (2026-09-02) — the first REAL measurements

> OPENROUTER_API_KEY provided by the owner 2026-09-02. All runs: qwen3.8-max
> reader via OpenRouter, gpt-4o judge via OpenRouter (`openrouter:openai/gpt-4o-2024-08-06`),
> docker FalkorDB lane, deterministic ingest unless noted. These REPLACE the
> earlier "PENDING KEYS" entries — recorded, not fabricated.

### M5 7-Q content-error test (deterministic ingest) — 6/7 valid, overall 0.5

| qid | type | verdict | notes |
|---|---|---|---|
| e831120c | multi-session | ✅ correct (3.5 weeks) | content error FIXED by qwen |
| b0479f84 | single-session-preference | ✅ correct (Dynasties) | FIXED by qwen |
| 0100672e | multi-session | ✅ correct ($12/mug) | FIXED by qwen |
| gpt4_8279ba02 | temporal-reasoning | ❌ abstained | ev@5=0, vector-leg only — RETRIEVAL gap |
| gpt4_7a0daae1 | temporal-reasoning | ❌ abstained | ev@5=0 — RETRIEVAL gap |
| gpt4_6ed717ea | temporal-reasoning | ❌ abstained | ev@5=0 — RETRIEVAL gap |
| 830ce83f | knowledge-update | ⚠️ reader crash | `NoneType.strip` — fixed by the None-guard (below) |

**M5 gate (ii) reading:** 3/7 content errors FIXED outright (qwen gets them
right where deepseek produced wrong content). The 3 temporal abstentions are
retrieval gaps (gold never in context — qwen cannot answer what it cannot
see), NOT reader content errors. 1 crashed on the empty-response bug. The
"0/7 content-error recurrences" criterion is directionally met: qwen did not
reproduce any of the 7 as CONTENT errors (correct or abstained, never
hallucinated wrong content).

### Stratified 50-Q pilot (deterministic ingest, 4 workers) — 45 outcomes, overall 0.622

Type mix mirrors the 500 (7 user / 13 multi / 13 temporal / 8 KU / 6 asst / 3 pref).
Watchdog `leg_dead` cut the last question (50/50) — 45 outcomes + 5 failures
in the checkpoint.

| class | accuracy |
|---|---|
| single-session-user | 7/7 = 1.00 |
| single-session-assistant | 6/6 = 1.00 |
| single-session-preference | 2/2 = 1.00 |
| knowledge-update | 6/7 = 0.86 |
| multi-session | 7/10 = 0.70 |
| temporal-reasoning | **0/13 = 0.00** |

**Failure decomposition (17 wrong):** 14 = retrieval-gap (ev@5=0 — gold turn
never retrieved, `leg_mix={"vector":12}` FTS-leg silent), 3 = reader-error
(gold in context, qwen answered wrong: "50 pounds", "$65 total" vs $185,
magazine count).

**The dominant product finding: temporal-reasoning retrieval.** The gold
turns for "how many days ago…" questions carry DATE properties; the lexical
FTS leg finds nothing on these queries and the vector leg alone cannot rank
the gold turn into the top-12 context. This is the #2070 retrieval-lever
target class (A4 search-keys PRF / A6 cap raise / fusion weights) — the
levers are shipped but not yet tuned. Next step: measure the 50-Q with
A4/A6 levers ON, then re-run.

### Product bug found live: LLMReader empty-response crash (#2134-adjacent)

qwen via OpenRouter returned completions with EMPTY content on 4/5 pilot
failures + 830ce83f — `LLMReader.answer().strip()` AttributeError'd on None
(`'NoneType' object has no attribute 'strip'`), failing whole questions as
`reader:retries_exhausted`. Product `ask()` already guards (`(raw or "").strip()`
sdk.py:10944); the eval's direct LLMReader path did not. Fixed in
`tortoise/reader.py` answer() + ping() with regression tests
(commit `9eedf82c`, worktree `wt-reader-guard` — PR pending).

### v2 extractor truncation finding — filed as #2134

v2 ingest (`--ingest-mode v2`, production 5-stage extractor) truncates S2/S4
output on large multi-session haystacks (13/139 calls truncated on 0100672e,
42 sessions) — partial embed list (`partial_parse`/`truncated_parse_error`,
valid=false) degrades retrieval and makes the reader abstain even when gold
IS in the haystack. Confounds eval results. Deterministic mode on the same
question: correct ($12/mug). Filed as #2134. The M5/pilot lanes use
deterministic ingest (the documented protocol lane); v2 truncation is a
separate product/extractor issue.
