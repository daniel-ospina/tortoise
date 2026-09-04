# W3-b why-layer suite — planted-conflict gold + A11 surfaced-context grading (issue #2100, epic #2080)

The Tortoise-original why-layer eval: grade how well the **W4 why-block
assembly** answers the four why-questions from the **surfaced context
alone**.  This is the A11 pilot — the harness and the product surface are
the SAME artifact: the canonical §3.1.4 why-block (`{point_id,
support_chain, ep, conflicts, supersession, tradeoffs, dig_deeper}`)
produced by `tortoise.why.assemble_why_blocks` is all the grader ever sees.

| | |
|---|---|
| Issue | #2100 (W3-b) — epic #2080 (gbrain measurable-memory adoption) |
| Graded artifact | REAL `tortoise.why.assemble_why_blocks` (the W4 assembly, #2101) — the same artifact the search/ask/analyze/MCP surfaces consume |
| Seeding | Shared E2E-1/E2E-7 planted-conflict corpus — 40 fictional points (30 conflicted incl. 10 P9 / 5 decision / 5 superseded subsets + 10 clean), content-mirrored from W4-a's `_seed_e2e1_corpus` (`tests/test_w4_why_enrichment.py`) and pinned by the jointly-pinned `corpus_manifest.json` |
| Judge pin | `judge_why_suite_v1` — static prompt file (`judge_why_suite_v1.txt`) whose sha256 is folded into the baseline `judge_pin` (`judge_why_suite_v1:<hex>`); asserted in the grading pre-step; a prompt/grader/gold change is a PROTOCOL change (re-pin + `--bless-protocol`), never a silent compare |
| Lanes | deterministic m2 (`TORTOISE_SESSION_EXTRACTOR=m2`) — the CI can-fail gate, byte-reproducible; llm posture (`main.json`) records the same numbers (this suite is zero-LLM end to end) |
| A11 gate | if the surfaced context can't answer ≥ 0.95, the W4 ASSEMBLY changes first — a plan change, not a test fix (epic R2) |

## What it grades (E2E-7 / the four why-questions)

For each of the 40 planted points the runner assembles the canonical
why-block and grades it from the block dict alone (`grading.py` — pure
functions; no grader ever touches a graph handle):

* **Conflict-surfacing** — does the surfaced context identify the planted
  contradiction?  (`conflicts.contested: true` + ≥ 1 NAND + a dig-deeper
  `nand` pointer.)  Bar: **≥ 0.95** over the 30 conflicted points.
* **Dig-deeper navigation** — do the `{label, kind, target}` pointers land
  on the correct planted points (supports → the record, nand → the
  counterargument, superseded → the successor, tradeoff → the EP-favored
  alternative)?  Bar: **≥ 0.95** over every gold-expected target.
* **Support-chain sufficiency** — "why is this believed?" answerable from
  `support_chain` + `ep` (measured).
* **Trade-off sufficiency** — "which alternative does EP favor?" answerable
  from `tradeoffs` (+ `ep_weight`s; the favored alternative is the
  max-ep_weight one).
* **False-positive arm** — clean points must NOT invent contradictions
  (no conflicts / contested / nand pointer).  Bar: **0 false positives**.

Rates are recorded + compared against posture-scoped baselines
(`baselines/main.json` + `baselines/m2.json`, pending first publish), with
validated receipts (`receipts/` — per-point rows tie the aggregate metrics
to the graded points).

**A4 A/B arm** (`a4_ab.py`, eval-phase, NEVER gating): contested-boost vs
confidence-only ordering over deterministic same-content twin pairs
(variance tiers incl. an at-threshold control), reusing W4-b's pair-set
pre-assertions.  Recorded in the run report + receipt notes; feeds W7-b.
*Current status on this corpus:* the boost fires on the contested tiers but
the ordering is regime-independent on the naive twins (the ranker's
confidence weighting dominates) — the arm records NOT-measured + the precise
calibration gap (the E2E-1 When-3 calibrated pair set is open work; a
measured delta is never faked).

## Corpus discipline (DM-9 — the planted-conflict gold conventions)

* `corpus_manifest.json` — the **jointly-pinned corpus manifest** (fixture
  side: harness-visible seeding spec only — composition + topic keys +
  planted role templates shared with W4-a's E2E-1 seeding).  A `gold` key
  inside it is a VALIDATION ERROR (answer-key contamination — sealed gold
  lives in `gold/`).
* `gold/why_suite.gold.json` — SEALED: per-planted-point
  `expected.conflict_surfacing` + `expected.dig_deeper_targets`
  (`{kind, target_role}`) + sufficiency expectations.  `fixtures_hash`
  covers manifest AND gold (a gold-only edit changes the hash ⇒ invalidates
  committed baselines).
* **Gold resolves against the manifest at contract-validation time**: an
  entry referencing a topic the seed never plants, a pointer kind the
  family's plant never surfaces (a clean point expecting a nand target, a
  non-superseded topic expecting the successor), or a target_role the
  manifest doesn't plant is a validation error — schema conformance alone
  cannot catch seed → planted point drift.
* **Joint pin vs W4-a**: the seeding drift test (`test_why_suite_benchmark`)
  re-derives W4-a's REAL `_seed_e2e1_corpus` on a hermetic graph and
  asserts identical composition + planted content sets.
* No holdout split: the whole 40-point corpus is graded every run (E2E-1/
  E2E-7 denominators are corpus-wide; the suite itself is the A11 pilot
  gate — nothing downstream needs untouched data).

## Run it

```bash
# corpus integrity (byte-identical render + full validation)
uv run python tests/eval/why_suite/generate_corpus.py --check
uv run python tests/eval/why_suite/generate_corpus.py --validate

# deterministic m2 lane (no LLM; embedded hermetic graph)
TORTOISE_SESSION_EXTRACTOR=m2 \
  uv run python tests/eval/why_suite/runner.py --compare

# docker lane (server graph, wipe-on-open)
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/why_suite_matrix' \
  uv run python tests/eval/why_suite/runner.py --compare

# publish (at a clean committed head, pending baseline)
... runner.py --bless --justification "first publish (m2 numbers at the bar)"
... runner.py --bless-corpus --justification "intentional manifest/gold regen"
... runner.py --bless-protocol --justification "judge_why_suite_v1 → v2 re-pin"

# tests
uv run pytest tests/eval/why_suite/ -q        # hermetic unit tests (docker lane)
TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/eval/why_suite/test_why_suite_schema.py \
  tests/eval/why_suite/test_why_suite_grading.py tests/eval/why_suite/test_why_suite_ab.py -q
```

## Failure classes

* **assembly-cannot-answer** — any standing bar below the floor
  (conflict-surfacing < 0.95 / navigation < 0.95 / clean false positives):
  the W4 assembly's surfaced context can't answer the why-questions — the
  ASSEMBLY changes first (epic R2), never the grading.
* **corpus drift** — manifest/gold edit without corpus-bless ⇒ the gate is
  `inconclusive` (hash mismatch); `generate_corpus.py --check` names the
  drifted file.
* **judge drift** — a `judge_why_suite_v1.txt` edit without a re-pin fails
  the pre-step as `judge_pin_mismatch` (protocol change = new pin + re-run).
* **a4-not-measured** — recorded (never gating) until the calibrated When-3
  pair set lands.
