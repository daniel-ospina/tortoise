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
  baselines/main.json             # committed baseline (first-run-pending state)
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
  (research-grounded Cat-35 enumeration; the plan §4.3.2 "explicit" example
  value is illustrative). The bucket is DERIVED from `planted_turn` vs
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
* `salient_units` — 1:1 with `planted_units`, carrying **point-level**
  `survival` semantics (the unit of analysis is the POINT — the
  research-brief/plan write-path unit assumption; NOT eval-spec §5's
  loopy-NAND "A1" adversarial test, and NOT page-level): `via_anchor` (the
  survival predicate = verbatim-anchor substring present in a surviving
  point), `accepts_rephrase_linked` (a REPHRASE-linked point counts as
  survival; false for date/numeric-critical anchors whose paraphrase would
  not preserve the claim — this corpus's claim-preservation carve-out on the
  REPHRASE-link concept borrowed from `docs/epistemic-layer-eval-spec.md`
  §P5 dedup-without-deletion), `provenance_required`, `ep_update_required`.
* `distractor_leakage_tolerance: 1` — research-recommended ≤1/run (gbrain
  measured 1/86); supersedes the epic's literal "zero" wording.

**Baseline (DM-5):** committed `baselines/main.json`, first-run-pending:
empty `metrics`/`history`, null `judge_pin`/`justification`. This is the
**benchmark-first** posture — NO preset quality bar. The W2-b runner
publishes the first (expected-bad) number per the fix-wave protocol and
blesses a real baseline with a `justification` (`schema.bless_baseline`).
The `--compare` verdict vocabulary is `pass | regression | inconclusive`;
corpus-hash or resolved-config mismatch ⇒ `inconclusive`, never a
rubber-stamp; blessing a regression REQUIRES a non-null `justification`
string.

## Regeneration protocol (fix-wave / corpus-bless)

```bash
uv run python tests/eval/write_path/generate_corpus.py            # idempotent write
uv run python tests/eval/write_path/generate_corpus.py --check    # drift check (exit 1 on drift)
uv run python tests/eval/write_path/generate_corpus.py --validate # full committed-dir validation
```

* Re-running the generator is **byte-deterministic** (sorted keys, fixed
  indent, no timestamps) for the frozen corpus = `fixtures/` + `gold/` +
  `_manifest.json` — the fix-wave guarantee (re-run the SAME frozen corpus +
  pinned judge).  `baselines/main.json` is deliberately OUTSIDE that drift
  scope: it changes legitimately when W2-b blesses a published run, and the
  generator never clobbers a published (non-pending) baseline.
* A **gold-only edit** changes `_manifest.json` + `baselines/main.json`
  `fixtures_hash` ⇒ committed baselines are invalidated (E2E-2 negative gate:
  mismatch ⇒ `inconclusive`, never a silent pass).
* Intentional fixture change = **corpus-bless** (deliberate regeneration
  reviewed in the PR diff); blessing a regression requires `justification`.

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

Floors (issue targets): ≥ 4 fictional sessions, ≥ 60 planted salient units
with verbatim anchors — chosen so E2E-2's percentage-based assertions
(macro ≥ target / strict ≥ target) have stable denominators. Current corpus:
5 sessions / 72 units.

All people, companies, and systems are fictional (Peregrine Systems, quarry /
lumen / ember / aurora, Halcyon Retail, Bluepeak Logistics, and the named
engineers). No gbrain/gbrain-evals corpus files are vendored (MIT ideas only
— learnings-map licensing gate). Lineage note: `wp01` deliberately
re-embodies the write-path failure archetype of gbrain's Cat-35 real-gold
example (a duplicate-ingest batch race — the raw-notes 10:10Z gold item) in
a freshly re-authored fictional session; ideas reimplemented carry no license
obligation, and no corpus file or verbatim gold text is copied.
