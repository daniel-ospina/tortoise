# Sealed ask-lane corpus (issue #9097, epic #2281 Q3 exit bar)

The **sealed** corpus behind the ask lane's published exit measurement. It is
the promotion of the #9095 instrument (`tools/ask_spotcheck.py --distilled`),
which could *print* a number, into the repo's pre-registered sealed-benchmark
discipline, so a number can be *published*.

Runner: `tools/longmem_eval/measure_ask_distilled.py`.

## Layout

```text
tests/eval/ask/
  generate_corpus.py   # the AUTHORED corpus spec + deterministic renderer
  corpus.py            # paths, corpus hash, manifest verification, sealed loaders
  fixtures/<case>.json        # RUNNER-visible: ingest inputs + the question ONLY
  gold/<case>.gold.json       # SEALED — the grading key (answer + judge template)
  _manifest.json              # sha256 of every fixture AND gold file
  baselines/main.json         # first-run-pending: NO preset quality bar
  preregistration.json        # the protocol, committed BEFORE the first run
  test_ask_corpus.py          # corpus contract tests
```

## Two classes, two different bars

| Class | Shape | Graded by |
|---|---|---|
| `knowledge-update` | a planted old→new supersession (3 cases) | the D8 gate: no superseded claim rendered LIVE, every planted claim OBSERVED. The current-state RATE is **reported, never thresholded** (D8: the first published run sets the benchmark-side X set). |
| `verbatim` | a fact stated verbatim, no supersession (3 cases) | the **paired** within-run comparison: a case the `raw` arm commits and the `distilled` arm loses is a FAIL, not a trade. No number is needed — the comparison is internal to one run. |

The verbatim class exists because the exit bar's "no regression on
verbatim/raw" half was **unmeasurable** without it: the #9095 corpus is
knowledge-update only, so there was no case to regress and no paired
comparison to lose one. Adding these controls is not a competing benchmark —
the KU corpus identity is #9095's, promoted here unchanged.

## Invariant that makes the comparison mean something

Every distilled claim's text appears **verbatim** in a turn of its own session
(enforced in `generate_corpus._issues` and pinned by
`test_ask_corpus.py::test_every_distilled_claim_is_verbatim_in_its_own_session`).
The two arms therefore hold the *same fact* and differ in **form** — narrative
turn vs distilled claim — so a raw-vs-distilled difference measures the
distillation, never a corpus difference.

## Sealed-key discipline

The answer key's only on-disk home is `gold/`. The **run** step is handed
`corpus.load_fixtures`, which globs `fixtures/*.json` only and refuses any
fixture carrying a grading key (`answer` / `gold` / `grading` / `expected`) **at
any depth** (`find_grading_keys` — a top-level check is walked past by a nested
key); the **score** step is the one that opens `gold/`. Blindness is a property
of the API, not a promise — `tests/test_measure_ask_distilled.py::
test_the_runner_cannot_read_the_sealed_gold` proves it with a sentinel.

**What "sealed" does not mean.** It is structural, not cryptographic: for a
knowledge-update case the answer is one of the planted claims anyway, and the
full `corpus_hash` digests `gold/`, so it would act as a weak oracle. That is
why the **run** record carries `fixtures_hash` (fixtures only) and the **score**
step computes the full `corpus_hash` — the bar binds the run to the corpus
through `fixtures_hash` and the corpus to the protocol through `corpus_hash`.

A **gold-only** edit changes the tree-wide `corpus_hash`, which voids the
pre-registration: a run over the re-keyed corpus reports `inconclusive`
(`hash_mismatch`), never a silent pass.

## Regeneration protocol

⛔ **Generator-owned — never hand-edit `fixtures/` or `gold/`.** Add or change a
case in `generate_corpus._CASES` and re-run (the #7947 lesson: a hand-added
section in a sealed gold reddened 18 tests).

```bash
uv run python -m tests.eval.ask.generate_corpus             # idempotent write
uv run python -m tests.eval.ask.generate_corpus --check     # exit 1 on drift
uv run python -m tests.eval.ask.generate_corpus --validate  # committed-dir validation
```

Re-running is byte-deterministic (sorted keys, fixed indent, no timestamps).
Changing the corpus **intentionally** means re-writing the pre-registration
(`measure_ask_distilled preregister`) in its own commit — the pre-registration
that fixed the bar is required to precede the run it governs.

## Why this is a separate tree from `tests/eval/write_path/`

Mechanical, not stylistic: the write-path corpus's own integrity gate derives
its file set from *its* `FIXTURE_GLOB`/`GOLD_GLOB` and requires `not extra`, so
an ask fixture dropped into that tree fails the write-path benchmark and changes
its single tree-wide hash (invalidating its committed baselines). Two
benchmarks, two trees, two hashes.
