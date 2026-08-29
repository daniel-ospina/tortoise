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

## Results (2026-08-29 — in-session implementation run)

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

**Merge is BLOCKED until the parent completes the full (a) graded `_abs`
verdict and the (d) live-judge spot-check ≥ 0.8** — per the plan's Task 12
hard gate ("the gate never skips its members") and the parent's
implementation note (live QA spot-check may be infeasible in-session).
