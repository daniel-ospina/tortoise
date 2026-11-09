# W3-b why-layer suite — planted-conflict gold + A11 surfaced-context grading (issue #2100, epic #2080)

> **Two corpora, two lanes.** `why_suite` (v1, the A11 pilot) and
> `why_suite_adversarial` (#2275, the edge-case corpus) are SEPARATE jointly-pinned
> fixture sets with separate baselines. See
> [Which corpus can falsify what](#which-corpus-can-falsify-what) — the two answer
> genuinely different questions, and neither subsumes the other.

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
| Judge pin | `judge_why_suite_v3` — static prompt file (`judge_why_suite_v3.txt`) whose sha256 is folded into the baseline `judge_pin` (`judge_why_suite_v3:<hex>`) together with `grading.py` + `schema.py` + `adversarial.py` (the adversarial corpus's grading arms are protocol code too); asserted in the grading pre-step; a prompt/grader/gold change is a PROTOCOL change (new protocol name + `--bless-protocol`), never a silent compare |
| Lanes | deterministic m2 (`TORTOISE_SESSION_EXTRACTOR=m2`) — the CI can-fail gate, byte-reproducible; llm posture (`main.json`) records the same numbers (this suite is zero-LLM end to end) |
| A11 gate | if the surfaced context can't answer ≥ 0.95, the W4 ASSEMBLY changes first — a plan change, not a test fix (epic R2) |

## What it grades (E2E-7 / the four why-questions)

For each of the 40 planted points the runner assembles the canonical
why-block and grades it from the block dict alone (`grading.py` — pure
functions; no grader ever touches a graph handle):

* **Conflict-surfacing** — does the surfaced context identify the planted
  contradiction?  (`conflicts.contested: true` + ≥ 1 NAND + a dig-deeper
  `nand` pointer.)  Bar: **≥ 0.95** over the 25 LIVE conflicted points
  (issue #2490 — the 5 superseded predecessors are RESOLVED, not live
  disputes; their graded arm is the resolved arm below).
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
* **Resolved arm** (issue #2490) — a superseded (terminal) claim's
  why-block must present it as RESOLVED: `supersession.status ==
  "superseded"` is served and neither `ep.contested` nor
  `conflicts.contested` reads true — the ANTI-GHOST (a decayed terminal
  must never surface as a live open dispute).  The conflict STRUCTURE
  (nands + successor pointers) is still graded via dig-deeper navigation;
  `support_chain_sufficient` is not expected (its belief is not measured —
  `has_ep=false`).

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

# the adversarial corpus is the SAME runner, a different lane (--corpus)
TORTOISE_SESSION_EXTRACTOR=m2 \
  uv run python tests/eval/why_suite/runner.py --compare --corpus why_suite_adversarial

# docker lane (server graph, wipe-on-open)
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/why_suite_matrix' \
  uv run python tests/eval/why_suite/runner.py --compare

# publish (at a clean committed head, pending baseline)
... runner.py --bless --justification "first publish (m2 numbers at the bar)"
... runner.py --bless-corpus --justification "intentional manifest/gold regen"
... runner.py --bless-protocol --justification "judge_why_suite_v3 re-pin (illustrative)"
# the adversarial lane blesses ITS OWN baseline (never v1's)
... runner.py --corpus why_suite_adversarial --bless --justification "..."

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
* **judge drift** — a `judge_why_suite_v3.txt` / `grading.py` / `schema.py` /
  `adversarial.py` edit without a re-pin fails the pre-step as
  `judge_pin_mismatch` (protocol change = new protocol name + re-run).
* **adversarial-arm failure** — `nand_recovery_rate` below its cap-bound
  floor, or `high_severity_nand_recovery` / `contested_classification_accuracy`
  below 0.95: the assembly stopped applying its own selection rules (severity
  ordering, `>` threshold, immediate-successor supersession, EP-favored
  tradeoff) — the ASSEMBLY changes first, never the fixtures.
* **a4-not-measured** — recorded (never gating) until the calibrated When-3
  pair set lands.

## Which corpus can falsify what

The suite grades TWO corpora.  Neither is a superset of the other: v1 asks
"does the assembly surface anything at all on its own authored fixtures?",
the adversarial corpus asks "does it still apply its documented SELECTION
rules when several of them compete?".

| | `why_suite` (v1, A11 pilot) | `why_suite_adversarial` (#2275) |
|---|---|---|
| Files | `corpus_manifest.json` + `gold/why_suite.gold.json` | `corpus_manifest_adversarial.json` + `gold/why_suite_adversarial.gold.json` |
| Points | 40 (30 conflicted + 10 clean) | 23 (21 conflicted + 2 clean) |
| Families | p9 / decision / superseded / plain / clean | near_tie / multi_nand / chain / wide_decision / precedence / boundary / clean |
| Baselines | `baselines/{main,m2}.json` | `baselines/adversarial_{main,m2}.json` |
| Metrics | the 5 E2E-7 metrics | the same 5 + `nand_recovery_rate`, `high_severity_nand_recovery`, `contested_classification_accuracy` |
| Seeding | mirrors W4-a's E2E-1 corpus (jointly pinned drift gate) | derived from the assembly's documented contracts; NOT the W4-a twin |

**`why_suite` (v1) CAN falsify:** the TOTAL ABSENCE of surfacing — a
regression that stops the assembly from producing conflicts / pointers /
support chains / tradeoffs at all, and any drift between the eval gold and
W4-a's real E2E-1 seeding.

**`why_suite` (v1) CANNOT falsify** (it is same-team authored on the
assembly's own supported surface, and its planted weights are spread apart
by design): pointer PRECEDENCE, tie handling under real posterior variance,
PARTIAL conflict surfacing (a point with more NANDs than the ≤2 cap),
supersede chains longer than one link, decisions with 3+ alternatives, and
malformed-surface tolerance.  Those are exactly the adversarial corpus's
families (below).

**`why_suite_adversarial` CAN falsify** (each family is a structure a naive
assembly gets wrong — see `adversarial.degrade_to_naive`, which the
benchmark test uses to prove the corpus discriminates):

* `near_tie` — three supports at 0.700 / 0.705 / 0.720 with the strongest
  planted LAST: the `supports` pointer must follow EP weight, not row order.
* `multi_nand` — three NANDers per point (two medium planted first, one high
  last) against the ≤2 cap: the high-severity one must survive the cap
  (`high_severity_nand_recovery`).  `nand_recovery_rate` is the standing bar
  that is NOT structurally 1.0 — the cap makes it 19/23 here.
* `chain` — `old ← successor ← grand_successor` stacked on a conflict: the
  `superseded` pointer must land on the IMMEDIATE successor and the resolved
  presentation must still hold.
* `precedence` — a decision that was later superseded, i.e. four candidate
  pointers (supports / nand / tradeoff / superseded) against the ≤3 cap: the
  tradeoff pointer must survive and the superseded one must be dropped.
* `wide_decision` — four mitigated alternatives with the EP favorite planted
  THIRD: the tradeoff pointer must be the favorite, not the first option.
* `boundary` — a posterior of Beta(2.625, 2.625), variance EXACTLY 0.04 ==
  `CONTESTED_VARIANCE_THRESHOLD`: the contract is `>`, so it must NOT read as
  a live dispute (`contested_classification_accuracy`).
* `clean` — two controls, so the 0-false-positive arm keeps a denominator.

**`why_suite_adversarial` CANNOT falsify:** real-memory recall semantics —
these fixtures are still SYNTHETIC and still authored by this team, so they
pin the assembly's documented selection rules rather than a real
conversation's graph.  A corpus derived from a captured session is still
open work; the comparison-systems rows in
`docs/benchmarks/comparison-systems.md` (§5 receipts) scope what a
real-memory comparison would need.
