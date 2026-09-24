# W2 Write-Path Planted-Gold Corpus (issue #2097, W2-a)

Frozen planted-gold corpus for the **write-path benchmark** (epic #2080,
W2): fictional agent sessions whose transcripts are planted with salient
units carrying verbatim anchors, true-but-routine distractors, and
attribution hazards. The corpus is a **fixture generator** (hermetic,
deterministic seeds) — not a test suite. It produces the committed fixtures +
sealed gold + `_manifest.json` + first-run-pending baseline that the W2-b
benchmark runner (#2098) consumes (E2E-2: write-path planted-gold survival;
test-design surfaces S4 + S15).

Design source: plan DM-3/4/5 (`docs/planning/2026-09-01-2080-gbrain-plan.md`
§4.3.1–4.3.3), the W1 learnings map Cat-35 ADOPT row
(`docs/research/2026-08-31-gbrain-learnings/learnings-map.md`), and the
research brief W2 row (raw-notes 10:10Z — the gbrain-evals Cat-35 gold
shape / `_manifest.json` / judge-blindness line-refs).

## Layout

```
tests/eval/write_path/
  generate_corpus.py        # deterministic corpus generator + authoring spec
  schema.py                 # canonical fixture/gold/baseline schemas + validators
  corpus.py                 # paths, fixtures_hash, manifest verification, pending baseline
  fixtures/<session>.json         # {session_id, harness, conversation} ONLY
  gold/<session>.gold.json        # SEALED — the entire answer key
  _manifest.json                  # sha256 of every fixture + gold file
  baselines/main.json             # committed PRODUCT-lane baseline (posture llm)
  baselines/m2.json               # committed CI-lane baseline (posture m2, deterministic)
  receipts/                       # validated run receipts (llm + m2 lanes)
  test_write_path_corpus.py       # contract tests (S4/S15)
```

## Schema summary (DM-3/4/5)

**Fixture (DM-3):** `{session_id, harness, conversation: [{role, content}]}`
— adapter-visible fields ONLY (gbrain-evals Cat-35 rule). Harness values are
the session-capture boundary set (`claude`, `claude-desktop`, `claude-web`,
`codex`, `cursor`, `pi`). A `gold` key anywhere inside a fixture is a
**validation error** — answer-key content lives only in the sealed gold file.

**Gold (DM-4):** sealed per-session answer key in a separate dir:

* `planted_units` — `{id, kind (fact|idea|decision|vibe|entity),
  verbatim_anchor, notability (high|medium|low), depth_bucket, planted_turn}`.
  `kind`/`notability` follow the Cat-35 vocabulary; `depth_bucket` is the
  third of the session the unit was planted in — **early|middle|late**
  (research-grounded Cat-35 enumeration; the plan §4.3.2 note explains the
  earlier-draft "explicit" value was illustrative, not an enum member). The bucket is DERIVED from `planted_turn` vs
  session length and coherence-checked by the validator.
* `distractors` — true-but-routine content present in the session that must
  NOT surface as salient (leakage probes), each with a grounded `anchor` +
  `planted_turn`.
* `attribution_hazards` — attribution traps: `{quote, source, planted_turn}`
  where the quote grounds in a **user-spoken** transcript line and the trap
  is misattributing it to anyone other than `source` (the named human
  operator who spoke it).  Emitted gold ids (`wp01_quarry_debug_u_01`,
  `..._h_01`) are **globally unique** — session-stem prefixed — so the W2-b
  runner can aggregate across sessions without bare-id collisions.
* `planted_operators` — **(issue #2514, layer-2 gold; grown by #2552)** optional
  sealed section naming the operator EDGES the extractor must wire between
  planted claims: `{id, expected_kind (SUPERSEDE|NEGATE|MITIGATES|SUPPORTS),
  from: {verbatim_anchor, planted_turn, session_id?}, to: {verbatim_anchor,
  planted_turn, session_id?}, relation_turn, reason}`.  `session_id` defaults
  to the gold's own session; the SUPERSEDE is planted CROSS-SESSION
  (wp07 → wp06) because a point-level supersession (CORRECTS) only forms when
  the superseded claim already exists in-graph.  Ontology ambiguity for
  SUPERSEDE + MITIGATES is flagged, not resolved (scoping note
  `docs/scoping/2026-09-07-2514-operator-corpus.md`, findings F1/F2).

  **#2552 measurement power:** the section grew **4 → 15 edges**, now carried
  by ALL SEVEN sessions (wp01 2, wp02 1, wp03 3, wp04 3, wp05 2, wp06 1,
  wp07 3 · SUPPORTS 4 / MITIGATES 8 / NEGATE 2 / SUPERSEDE 1).  The 4-edge
  denominator swung **0, 1, 1, 1, 2 / 4 on IDENTICAL code** — it could not
  separate a fix from LLM variance, which is why the issue never closed.
  `generate_corpus.MIN_PLANTED_OPERATOR_KINDS` is the authority for the
  floors — one entry per kind (SUPPORTS 4 / MITIGATES 8 / NEGATE 2 /
  SUPERSEDE 1), enforced on every fresh render and on the committed corpus
  (`_operator_floor_issues`, one contract, two adapters).
  `MIN_PLANTED_OPERATOR_EDGES` and `REQUIRED_OPERATOR_KINDS` are **derived**
  from it, so the three cannot describe different corpora.

  ⛔ **A test or lane needing a DENOMINATOR must use
  `corpus.planted_operator_count()`** — the ACTUAL gold-derived count. The
  floor is a **lower bound** (a sum of minimums), so comparing an audit's
  `planted` against it stops tying the audit to the corpus and would accept a
  silent per-session shrink that still clears every floor.  Pinning a literal
  (the original `== 4`, and a later private `15`) is the same defect class in
  its other direction: it reddens every lane the moment the gold grows.

  Known limitation: SUPERSEDE and
  NEGATE remain thin (1 and 2 instances), and the corpus plants only the
  kinds the write path already supports — it measures recall, not the F1/F2
  ontology mapping.
* `salient_units` — 1:1 with `planted_units`, carrying **point-level**
  `survival` semantics (the unit of analysis is the POINT — the
  research-brief/plan write-path unit assumption; NOT eval-spec §5's
  loopy-NAND "A1" adversarial test, and NOT page-level): `via_anchor` (the
  survival predicate = verbatim-anchor substring present in a surviving
  point), `accepts_rephrase_linked` (a REPHRASE-linked point counts as
  survival; false for any anchor whose paraphrase would not preserve the
  claim — commonly date/numeric-critical, also named-entity ownership,
  decisions, root-cause facts; this corpus's claim-preservation carve-out on
  the REPHRASE-link concept borrowed from `docs/epistemic-layer-eval-spec.md`
  §P5 dedup-without-deletion), `provenance_required`, `ep_update_required`.
* `distractor_leakage_tolerance: 1` — research-recommended ≤1/run (gbrain
  measured 1/86); supersedes the epic's literal "zero" wording.

**Baseline (DM-5):** TWO posture-keyed committed baselines (posture split,
#2098 round 2): `baselines/main.json` = the PRODUCT lane (extractor posture
`llm`, real v2 extractor — the number the W4 gate / W7 publication consume,
gated manually at bless with `justification`), and `baselines/m2.json` = the
deterministic CI lane (extractor posture `m2`, TORTOISE_SESSION_EXTRACTOR=m2
echo seam — byte-reproducible, compared on every write-path PR: verdict PASS
on clean replay, REGRESSION on parser/write-back/provenance/event-stamp/
session-emission regressions). Each starts first-run-pending: empty
`metrics`/`history`, null `judge_pin`/`justification` — the
**benchmark-first** posture, NO preset quality bar. The W2-b runner
publishes the first (expected-bad) number per the fix-wave protocol and
blesses a real baseline with a `justification` (`schema.bless_baseline`).
The `--compare` verdict vocabulary is `pass | regression | inconclusive`;
corpus-hash, resolved-config, or extractor-posture mismatch ⇒
`inconclusive`, never a rubber-stamp; blessing a regression REQUIRES a
non-null `justification` string. Cross-posture compares are config
mismatches (never a silent cross-extractor pass/regression); the standing
leakage bar (≤1/run) is a PRODUCT-lane bar (the m2 echo lane structurally
leaks — its gate is determinism/reproduction).

## Regeneration protocol (fix-wave / corpus-bless)

```bash
uv run python tests/eval/write_path/generate_corpus.py            # idempotent write
uv run python tests/eval/write_path/generate_corpus.py --check    # drift check (exit 1 on drift)
uv run python tests/eval/write_path/generate_corpus.py --validate # full committed-dir validation
```

* Re-running the generator is **byte-deterministic** (sorted keys, fixed
  indent, no timestamps) for the frozen corpus = `fixtures/` + `gold/` +
  `_manifest.json` — the fix-wave guarantee (re-run the SAME frozen corpus +
  pinned judge).  `baselines/{main,m2}.json` are deliberately OUTSIDE that
  drift scope: they change legitimately when W2-b blesses a published run,
  and the generator never clobbers a published (non-pending) baseline.
* A **gold-only edit** changes `_manifest.json` + BOTH baselines'
  `fixtures_hash` ⇒ committed baselines are invalidated (E2E-2 negative gate:
  mismatch ⇒ `inconclusive`, never a silent pass).
* Intentional fixture change = **corpus-bless** (`--corpus-bless`, deliberate
  regeneration reviewed in the PR diff — the history entry's
  `corpus_change: true` marker is what reviewers check); intentional judge-
  protocol bump = **protocol-bless** (`--protocol-bless`, `protocol_change`
  marker); blessing a regression requires `justification`.

## Sealed-key discipline

The answer key's only on-disk home is `gold/`. Judges (W2-b) never see
verbatim anchors (judge-blindness — the salience judge gets paraphrase-level
statements only, so scoring cannot degrade into lexical matching). Per-item
paraphrase-level statements are NOT committed in this gold (DM-4 deliberately
carries verbatim anchors + survival flags only, per issue #2097 indicator 2):
W2-b must supply paraphrase-level judge inputs WITHOUT leaking anchors — any
synthesized paraphrase step must be pinned inside the judge prompt version
(`judge_pin`) so the fix-wave protocol stays reproducible (re-run SAME frozen
corpus + pinned judge). Authoring
content lives in `generate_corpus.py`; every anchor/quote is verified against
its planted turn at render time, so fixture/gold drift cannot ship silently.

## Corpus inventory

| Session | Harness | Scenario | Units | Salient | Distractors | Hazards |
|---|---|---|---|---|---|---|
| `wp01_quarry_debug` | codex | quarry backfill stall root-cause + fix | 16 | 16 | 3 | 3 |
| `wp02_lumen_refactor` | codex | lumen per-graph-key auth migration | 13 | 13 | 2 | 2 |
| `wp03_ember_design` | pi | ember alert-routing redesign + on-call review | 15 | 15 | 2 | 2 |
| `wp04_aurora_perf` | pi | aurora dashboard latency investigation | 13 | 13 | 2 | 2 |
| `wp05_retro_writeup` | claude-desktop | Bluepeak incident retro + follow-ups | 15 | 15 | 2 | 2 |

| `wp06_quarry_rollout` | codex | quarry lease-fix rollout decision; chaos run supports scope-independence (SUPPORTS planted) | 9 | 9 | 2 | 2 |
| `wp07_bluepeak_followup` | codex | duplicate anomaly after the rollout; flag hypothesis counter-claimed (NEGATE), skew risk closed (MITIGATES), rollout decision overturned (SUPERSEDE, cross-session) | 9 | 9 | 2 | 2 |

Floors (issue targets): ≥ 4 fictional sessions, ≥ 60 planted salient units
with verbatim anchors — chosen so E2E-2's percentage-based assertions
(macro ≥ target / strict ≥ target) have stable denominators. Current corpus:
7 sessions / 90 units.  Issue-#2514 operator floor: all four planted-operator
kinds (SUPERSEDE/NEGATE/MITIGATES/SUPPORTS) are planted ≥ 1× — grown by #2552
to **15 edges** (`generate_corpus.MIN_PLANTED_OPERATOR_EDGES`; the corpus-level
grades live on every run's `operator_audit` — see below).

All people, companies, and systems are fictional (Peregrine Systems, quarry /
lumen / ember / aurora, Halcyon Retail, Bluepeak Logistics, and the named
engineers). No gbrain/gbrain-evals corpus files are vendored (MIT ideas only
— learnings-map licensing gate). Lineage note: `wp01` deliberately
re-embodies the write-path failure archetype of gbrain's Cat-35 real-gold
example (a duplicate-ingest batch race — the raw-notes 10:10Z gold item) in
a freshly re-authored fictional session; ideas reimplemented carry no license
obligation, and no corpus file or verbatim gold text is copied.

## Additive banded semantic judge (issue #5085)

The mechanical anchor metrics above stay **authoritative** for the gated
`metrics` vocabulary — that is the recorded grading hierarchy
(`runner.py`, plan R-row, gbrain Cat-35 ADOPT).  Issue #5085 adds a
judged **knowledge-preservation** number *beside* them (never replacing
them), because the anchor bar scores a paraphrase-only rewrite of a planted
fact as 0 even when the fact is present in different words.

```bash
# product/llm lane only — the m2 CI lane is REFUSED (determinism)
PYTHONPATH=$PWD TORTOISE_TEST_CARVE_OUT=1 tools/run-with-eval-keys.sh \
  .venv/bin/python -m tests.eval.write_path.runner run \
    --judge semantic --judge-samples 5 --out <receipt>
```

* **Blind** — the judge gets paraphrase-level probes + the stored memory
  points; it NEVER sees the verbatim anchor.  The guard
  (`judge.BandedSalienceJudge._guard`) is fail-closed on the PROBE block
  (the memory block is observed data and may legitimately contain a stored
  point's verbatim wording); a leak raises `JudgeBlindnessError` and fails
  the run (plan §J4: blindness is load-bearing).
* **Earned probability** — one binary preservation question, asked
  `--judge-samples` (default 5) times per unit in EACH of the two prompt
  orders; the unit's probability is the agreement fraction over the pooled
  votes, and the two orders are averaged to blunt documented position bias
  (arXiv 2406.07791).  No verbalized confidence is trusted (arXiv
  2512.22245, 2508.06225); token logprobs, if a seam ever exposes them, are
  recorded as a cross-check only.
* **Bands** (owner-specified, #5085 D14): `same_fact` ≥ 0.80 · `likely`
  ≥ 0.70 · `maybe` ≥ 0.50 · `likely_not` below.  The receipt carries the
  per-unit band, the **band distribution**, and a **band-weighted score**
  (mean of each band's interval midpoint) — so a reader can tell wording
  shortfall from omission.
* **Protocol pin** — `judge.SEMANTIC_JUDGE_PIN`
  (`w2-semantic-banded-v1+w2-salience-paraphrase-v1`), recorded in the
  receipt's `semantic_judge` block.  The receipt's `judge_pin` stays
  `JUDGE_PIN_MECHANICAL`, so committed baselines remain comparable and a
  mechanical-lane receipt is byte-identical to the pre-#5085 shape.
* **Judge model ≠ extractor model** — the default judge is `solar-pro4`
  (upstage), deliberately outside the deepseek extractor family
  (self-preference bias).  The run records both and states any residual
  overlap.

## Calibration (κ/α pending human labels)

```bash
.venv/bin/python -m tests.eval.write_path.runner calibrate \
  --receipt <receipt> --out tests/eval/write_path/calibration/<name>.json
.venv/bin/python -m tests.eval.write_path.runner calibrate --sample <sample.json>
```

`calibration.build_calibration_sample` builds a deterministic, band-stratified
sample (owner floor n ≥ 30, spanning all four bands) whose items carry the
probe, the judge's band/probability and the session's memory notes, with
`human_band: null`.  Until every item is labelled the report is
`labels_pending: true` with `cohen_kappa: null` / `krippendorff_alpha: null`
— **no κ is ever invented.**  Once labelled, `cohen_kappa` (judge vs human)
and `krippendorff_alpha` (≥2 coders) are computed.

**Deterministic NLI pre-screen:** NOT added — the repo has no NLI/entailment
dependency and adding a transformer stack is a policy decision, so the
SummaC/FactCC/AlignScore-lineage screen is a filed follow-up (see the issue
referenced in the PR) and the judge path ships without it.

## Layer-2 operator-edge audit (issue #2514)

Every completed run carries an additive `operator_audit` on the report +
receipt: `{planted, edge_correct, content_ok}` graded mechanically over the
session snapshot's operator surface (`operator_edges` — reified
IMPL/NAND/MITIGATES nodes touching the session's memory points;
`direct_edges` — the Point→Point CORRECTS supersession edge; `mitigations` —
mitigation Points on operators).  Kind → graph-form mapping + the ontology
findings live in the scoping note.  The audit is NOT a `METRIC_VALUES` member
(no baseline re-bless of the metric vocabulary): the m2 echo lane has no
relation extraction — its cue-word heuristic matches only a few planted edges
(the measured corpus reads 2/15 `edge_correct`) — so its number is structural,
and the operator bar is a product-lane (llm) bar — same posture split as the
standing leakage bar.

### ⛔ Three lanes, three different questions (#2552)

The audit denominator is ``corpus.planted_operator_count()`` — **15** planted
edges today.  It is DERIVED, never a literal: a lane or test that needs the
number calls the helper (see the rule above).  Do not read one lane's number as
another's:

| Lane | What it grades | How to run |
|---|---|---|
| **WIRE** (`test_write_path_operator_lane.py`) | capture → `create_operator` → retrievable memory → grader, on a **gold-derived emission** that MONKEYPATCHES `extract_session_v2` | `pytest tests/eval/write_path/test_write_path_operator_lane.py` |
| **FOLD** (same file, `test_fold_lane_mints_every_planted_operator_endpoint`) | `execute_embed` — the operator-FORMING step — fed the gold's endpoints with NO endpoint points emitted | same file |
| **PRODUCT** (llm lane) | the real model end to end | `runner run` with a provider key |

A green WIRE lane is the correct write-path result and is **never** evidence
that the behavioural half of #2552 is fixed: it bypasses `execute_embed`
entirely.  The FOLD lane measures the fold deterministically — it is the lane
that resolves a fix from variance.

### Baseline re-pin for this corpus change

`baselines/m2.json` was re-measured by a real deterministic replay on the
expanded gold; `baselines/main.json` carries its published llm numbers
forward with an explicit not-re-measured justification (operator gold is
GOLD-ONLY — fixtures, planted units and anchors are untouched, so the gated
metrics remain measured on the same corpus content).  A sealed llm run
(corpus-bless + protocol-bless v1→v2, with the first comparable operator-edge
numbers on the 15-edge denominator) is REQUIRED before the llm lane is
comparable again — see the scoping note's "Sealed run required to activate".

> ⚠️ **Start the llm lane through `tools/run-with-eval-keys.sh`** (#2718 /
> #4860): this runner reads provider keys from the process env and never loads
> the repo `.env`, so an ambient shell key is what gets billed. That is how the
> 2026-09-23 sealed run billed an exhausted fleet key and 403'd 7/7 while
> `.env` held a healthy evals key. The wrapper strips the ambient provider
> keys, loads `.env` with override, and prints the source + fingerprint of
> every key it set — paste that into the receipt.
