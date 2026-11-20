# Capstone #13038 — the state model, before and after this strand

The instrument is described in `README.md` one level up and was built in #13038.
This directory holds the two runs that bracket the implementation work, so the
"6 right" claim is checkable from the tree rather than from a report.

## What the board measures

Six questions a customer would ask of a business memory, scored right/wrong
against a frozen narrative (`narrative.json`), and judged **against that
narrative as the key** — not by substring matching and not by internal fields.
Each row carries a "what a wrong answer means" line, so a verdict is about what
the answer does to a customer.

## Before — `3 right · 3 wrong · 0 blocked · 0 unjudged`

`scoreboard-before.md` · `run-before.json` · product **`9e9fc232`**, run
**2026-11-18T16:40:50Z → 16:44:05Z** (local target, 195.2 s).

Per-question verdicts: **Q1, Q5, Q6 wrong**; Q2, Q3, Q4 right.

Three disclosures, because they bound what this run can be used to claim:

- **The judge was `--judge file`, not a fresh reader.** `run-before.json` →
  `judge: {mode: "file", file: "…/verdicts-baseline.json"}`, and that verdict
  file's own `_provenance` records it as a **fresh-reader output of a different
  run at a different product** (`capstone13038_20261117t184234z`, product
  `446bd09a`). So the verdict prose here is a re-used baseline; where it
  disagrees with this run's payload, trust the payload.
- **The read path was degraded in every read** — `vector` 11/11
  (`load_timeout`, `load_in_flight`), `fallback` 9/9, `fts` 4/11. The instrument
  says explicitly that a row failing because unrelated content came back is the
  read path, not the model.
- Consequence: this run is evidence that the board **was** 3/3 at that product,
  not a clean measurement of *why*.

## After — `6 right · 0 wrong · 0 blocked · 0 unjudged`

`scoreboard-after.md` · `run-after.json` · product **`28ca66878d`**, run
**2026-11-20T17:10:09Z → 17:12:51Z** on `main` (local target, 161.5 s), judge
**fresh** (`pi -p --no-session --no-tools`, 84.0 s).

**Every retrieval leg reported ok** on this run — `vector` 9/9, `fts` 9/9,
`structural` 9/9, `valid_time` 5/5, zero degraded legs.

⚠️ **But the read path is not clean, and the board says so six times.** Six
row-level signals read *"a degraded read ranked the corpus rather than
answering"* — two each on Q1, Q5 and Q6 (`current commitments`, `onboarding
flow`, `what was planned and never happened`, `thursday partner call`, `has
anything we relied on been withdrawn`, `beach club confirmed booking`). So the
accurate statement is: **the six answers are right while several of the queries
that produce them still surface rows with no relation to the question.** A green
`read_status` on every leg does not make those queries clean, and calling this a
"healthy read path" would overstate it.

## What the delta can and cannot be attributed to

Only slices **absent from the before product** can explain 3 → 6. Verified by
ancestry, that is:

| slice | issue / PR | status in the before product |
|---|---|---|
| a MENTION of a plan is not its OCCURRENCE | **#13578** | **absent** — merged 2026-11-18T19:15Z, after the before run |
| the writer refuses a kind's RENDERING, not its `__eq__` | **#13586** | **absent** — merged 2026-11-18T21:31Z |
| the occurrence-kind spellings are EVIDENCE, both legal forms matched | **#13604** | **absent** — merged 2026-11-18T23:24Z |
| commitments by kind; endings reached by asking | **#13833** | **absent** — merged 2026-11-19T21:49Z |

These were **already in** `9e9fc232`, so they cannot account for the delta and
should not be credited with it: the currency rule reading the position axis
(#13034 / #13075, merged 2026-11-17), `goalState`↔`status` consistency
(#13035 / #13065, merged 2026-11-17), and plans-vs-events (#13100 / #13115,
merged 2026-11-17). Note #13034 itself is still **OPEN** — its measured fix (the
predicate reading the position axis) shipped in #13075.

## How to re-run

```bash
bash docs/evidence/13038-capstone/rerun.sh --out /tmp/capstone-now --judge fresh
```

~2–4 minutes against a throwaway local FalkorDB. The wrapper refuses to publish
an unjudged board into this bundle, because an all-`unjudged` run is not a
baseline.

## Honest limits

- **Six questions**, chosen to exercise the shapes the state model governs. Not
  a general quality score: 6/6 means these six are right, not that every read
  path is.
- The judge is an **LLM reader**, so a verdict is a judgement, not an assertion.
  The per-question evidence — what the product returned, which reads ran, which
  legs degraded, and each row-level signal — is in the `.json` beside each
  board, so a disputed verdict can be re-read rather than argued.
- The two runs are **different products**, so this is a comparison of two states
  of the product, not a controlled A/B. Anything else that landed between them
  is in the delta and not attributed here.
