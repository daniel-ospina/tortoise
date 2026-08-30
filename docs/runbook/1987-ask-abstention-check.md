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
