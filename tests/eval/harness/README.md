# W3 harness — volunteering-memory real-seam eval (issue #2099, epic #2080)

The Cat-34-shaped **scripted-conversation harness**: replay committed
conversations through the REAL product seams on hermetic per-cell graphs and
grade the written/recalled surface with mechanical graders.

| | |
|---|---|
| Issue | #2099 (W3-a) — epic #2080 (gbrain measurable-memory adoption) |
| Replay seam | REAL `session_import` parser → REAL `capture_session` write path → REAL `recall_state` read path |
| Extractor lanes | `m2` deterministic echo (`TORTOISE_SESSION_LLM_MOCK=1` + `TORTOISE_SESSION_EXTRACTOR=m2`) — the CI can-fail gate; `llm` product lane (real provider key) — the publish lane |
| Reflex seam | graded decision layer (know_to_ask / push) — ships with W4 (#2102); this issue publishes the honest NULL-reflex baseline |
| Judge pin | `w3-volunteering-memory-mechanical-v1` (the deterministic graders ARE the judge; a grader/gold change is a protocol change) |

## Corpus (18 sessions, 5 suites)

`fixtures/<sid>.json` (harness-visible fields only — a `gold` key inside a
fixture is a VALIDATION ERROR) + sealed `gold/<sid>.gold.json` + a
`_manifest.json` (sha256 over fixture AND gold files; a gold-only edit
changes `fixtures_hash` ⇒ invalidates committed baselines).

* **know_to_ask** — per-turn `should_retrieve` labels; courtesy /
  re-mention / below-notability turns MUST NOT fire (false-fire anti-gaming).
* **push** — pointer-budget precision/recall (gold-acceptable pointer ids).
* **write_back** — planted anchors that must survive session→graph
  write-back with provenance (gradeable TODAY).
* **continuity** — writer fixture → write-back → READER fixture; the reader
  cell's real `recall_state` must surface the planted decision (gradeable
  TODAY; writers replay before readers).
* **isolation** — `team_a`/`team_b` fixtures with overlapping entity names
  (Mercury / Atlas / Orion) but disjoint facts.  The E2E-4 gate is THIS
  issue's own pass gate: cross-team content in the wrong team's CELL GRAPH
  (whole-cell snapshot — per-session eventId snapshots would hide a
  misrouted write) is a violation; the gate is live from day one.

Holdout (~15%) is **pinned per fixture** (never seed-derived): a frozen
evaluation set for the W4 reflex.

## The 7-metric snapshot

`know_to_ask_failure_rate` (0.00 target), `false_fire_rate` (≤ 0.03),
`push_precision` (≥ 1.000), `push_recall`, `write_back_fidelity`,
`continuity_recall`, `source_isolation_violations` (= 0, always-live gate).

Standing kta/false-fire/push bars activate **only when a committed baseline
carries `config.reflex: "graded"`** — under the null-reflex baselines the
numbers record honestly (fix-wave) but don't gate; W4 lands the graded
reflex and re-blesses with `--bless-protocol` (reflex null→graded is a
protocol change, marked `reflex_graded` in history).

## Baselines + receipts

`baselines/main.json` (llm product lane) and `baselines/m2.json`
(deterministic echo lane — the CI can-fail gate, byte-reproducible PASS at
clean replay).  Bless only at a CLEAN COMMITTED head (a receipt pinning a
pre-runner commit is rejected in review); every publish needs a
justification naming the failure class; `--bless-corpus` (fixture/gold
regeneration) and `--bless-protocol` (judge/reflex re-pin) are the
sanctioned markers.  Receipts live in `receipts/` (§6.6 shape: run_status /
verdict / failure_origin / commit / corpus_hash / judge_pin /
resolved_config / cost_usd + per-session detail).

## Run it

```bash
# deterministic CI lane (no LLM, byte-reproducible)
TORTOISE_SESSION_LLM_MOCK=1 TORTOISE_SESSION_EXTRACTOR=m2 \
  uv run python tests/eval/harness/runner.py --compare

# product lane (real provider key + docker URI for server graphs)
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/<db>' \
  uv run python tests/eval/harness/runner.py --compare --progress

# publish (at a clean committed head)
... runner.py --bless --justification "..."        # first/fix-wave numbers
... runner.py --bless-protocol --justification "..."  # W4 reflex null→graded
... runner.py --bless-corpus --justification "..."    # intentional corpus regen

# corpus integrity
uv run python tests/eval/harness/generate_corpus.py --check   # byte-identical
uv run python tests/eval/harness/generate_corpus.py --validate

# tests
uv run pytest tests/eval/harness/ -q          # schema 39 + corpus 10 hermetic
TORTOISE_SESSION_LLM_MOCK=1 TORTOISE_SESSION_EXTRACTOR=m2 \
  uv run pytest tests/eval/harness/test_harness_benchmark.py -q   # real replay
```

## Failure classes (fix-wave protocol)

* **no-reflex** — kta failure 1.0 / push 0.0 until the W4 reflex lands
  (named in every null-reflex run's notes; the honest first numbers).
* **structural echo leakage** — the m2 echo lane copies every distractor
  turn; its write_back/continuity numbers are echo-lane numbers, never the
  product bar (the product-lane `main.json` is the llm number).
* Isolation violations > 0 = misrouted rig or a product team-scoping gap —
  the E2E-4 gate, ALWAYS live.
