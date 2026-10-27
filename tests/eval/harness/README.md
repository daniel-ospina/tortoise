# W3 harness — volunteering-memory real-seam eval (issue #2099, epic #2080)

The Cat-34-shaped **scripted-conversation harness**: replay committed
conversations through the REAL product seams on hermetic per-cell graphs and
grade the written/recalled surface with mechanical graders.

| | |
|---|---|
| Issue | #2099 (W3-a) — epic #2080 (gbrain measurable-memory adoption) |
| Replay seam | REAL `session_import` parser → REAL `capture_session` write path → REAL `recall_state` read path |
| Extractor lanes | `m2` deterministic echo (`TORTOISE_SESSION_LLM_MOCK=1` + `TORTOISE_SESSION_EXTRACTOR=m2`) — the CI can-fail gate; `llm` product lane (real provider key) — the publish lane |
| Reflex seam | graded decision layer (`tortoise.volunteer.decide`) over an EP-warmed cell — `config.reflex: "graded"` in the committed baselines; `null` remains a debug lane (#8125) |
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
carries `config.reflex: "graded"`**. #8125 flipped that decision (reflex
null→graded, marked `reflex_graded` in history) so the bars are live instead
of inactive, and replaced the null reflex with the REAL
`tortoise.volunteer.decide` layer replayed per user turn.

One bar is conditioned further: **push precision** grades POINTER IDENTITY,
and the committed push gold names symbolic ids (`pt_lumen_boundary`, …) that
no replay cell can mint — every precision the graded reflex can produce is
0.000 by construction. The runner computes this from the gold + the cell
(`runner.push_gold_grounding`) and `schema.compare_run` applies the push
floor only when the gold can name a point. Until the corpus carries
content anchors, a run's notes and its receipt's `reflex_gate` block say
`push_pointer_identity_evaluable: false` (fix-wave: #9081); the floor turns
itself on when the gold does, with no flag to flip.

## Graded reflex lane + EP warm-start (#8125)

`--reflex graded` (the default, from `corpus.BASELINE_CONFIG`) replays every
USER turn of a know_to_ask / push session through the product reflex — real
`volunteer.decide`, real hybrid search, the canonical prior-context block
carried forward so cross-turn suppression is exercised — and grades the
injected pointers in the harness vocabulary. `--reflex null` injects nothing
and exists only as a debug lane.

A captured cell carries **no persisted prior**: the echo lane's extractor
mints no claim/operator, so `_promote_and_dream` no-ops and every point reads
the Beta(1,1) neutral 0.5 — under the reflex's 0.7 EP gate, so nothing can
fire (the named under-fire class: 4 of 4 know-to-ask turns missed at
`d1f2d345c`). `runner.ep_warm_start` gives the graded cell the epistemic
state the product write path builds:

1. the canonical #2199 `system-default` starting belief (Beta(3,1)) on every
   live belief point that has NO persisted prior — a declared prior-only
   state the gate reads at its prior mean (it never overwrites a real
   posterior or a baseline);
2. the REAL EP pass (`sdk.dream(mode="full")`), so operator structure
   propagates (a scan on an operator-less echo cell — reported, not assumed).

It runs on the GRADED cells only, and every session result carries its
`ep_warm_start` disclosure (`seeded`, prior, dream mode/affected/scanned), so
the baseline's numbers state the exact protocol that produced them.
Measured on the frozen corpus after the warm-start: `know_to_ask_failure_rate
0.000`, `false_fire_rate 0.000` — and with the warm-start disabled the same
lane reads `know_to_ask_failure_rate 1.000` (REGRESSION), so the mechanism is
load-bearing and CI fails if it regresses.

## Baselines + receipts

`baselines/main.json` (llm product lane) and `baselines/m2.json`
(deterministic echo lane — the CI can-fail gate, byte-reproducible PASS at
clean replay).  `m2.json` carries `config.reflex: "graded"` (#8125).
`main.json` still carries `reflex: "null"`: it holds the last llm-lane
number measured under the config that measured it, and re-blessing it needs
a keyed product-lane run — until then a llm-posture run reports
INCONCLUSIVE (`origin=config_mismatch`), which is honest, not a pass
(follow-up: #9082).  Bless only at a CLEAN COMMITTED head (a receipt pinning a
pre-runner commit is rejected in review); every publish needs a
justification naming the failure class; `--bless-corpus` (fixture/gold
regeneration) and `--bless-protocol` (judge/reflex re-pin) are the
sanctioned markers.  Receipts live in `receipts/` (§6.6 shape: run_status /
verdict / failure_origin / commit / corpus_hash / judge_pin /
resolved_config / cost_usd + per-session rows).  Completed receipts carry
per-session rows (an explicit `session_results_elided` marker covers the
pre-persistence llm-lane receipt — the product lane's per-session detail is
regenerated on its next re-run; the deterministic m2 receipts are fully
reproducible).

## Run it

```bash
# deterministic CI lane (no LLM, byte-reproducible) — graded reflex (default)
TORTOISE_SESSION_LLM_MOCK=1 TORTOISE_SESSION_EXTRACTOR=m2 \
  uv run python tests/eval/harness/runner.py --compare

# the same lane on the null reflex (debug only — the bars go inactive)
TORTOISE_SESSION_LLM_MOCK=1 TORTOISE_SESSION_EXTRACTOR=m2 \
  uv run python tests/eval/harness/runner.py --compare --reflex null

# product lane (real provider key + docker URI for server graphs)
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/<db>' \
  uv run python tests/eval/harness/runner.py --compare --progress

# publish (at a clean committed head)
... runner.py --bless --justification "..."        # first/fix-wave numbers
... runner.py --bless-protocol --justification "..."  # reflex null→graded / judge re-pin
... runner.py --bless-protocol --reflex null --justification "..."  # back to the debug lane
... runner.py --bless-corpus --justification "..."    # intentional corpus regen

# corpus integrity
uv run python tests/eval/harness/generate_corpus.py --check   # byte-identical
uv run python tests/eval/harness/generate_corpus.py --validate

# tests
uv run pytest tests/eval/harness/ -q          # schema+corpus+grading 52 hermetic
TORTOISE_SESSION_LLM_MOCK=1 TORTOISE_SESSION_EXTRACTOR=m2 \
  uv run pytest tests/eval/harness/test_harness_benchmark.py -q   # real replay (9)
```

## Failure classes (fix-wave protocol)

* **no-reflex** — kta failure 1.0 / push 0.0 on a `--reflex null` debug run
  (named in that run's notes; the honest pre-graded-reflex numbers).
* **under-fire (no persisted prior)** — a graded cell whose points carry no
  prior reads the Beta(1,1) neutral 0.5 under the reflex's 0.7 EP gate and
  never fires. Fixed by `runner.ep_warm_start` (#8125); the can-fail test
  disables the warm-start and requires the run to REGRESS, so a silent
  return of this class fails CI.
* **push-identity-not-evaluable** — the push gold's pointer ids are symbolic
  and resolve to no cell point, so push precision/recall record honestly but
  the push precision floor is not applied (disclosed in the run's notes, the
  receipt's `reflex_gate.push_bar_applied=false`, and the blessed baseline's
  history). Fix-wave: #9081.
* **structural echo leakage** — the m2 echo lane copies every distractor
  turn; its write_back/continuity numbers are echo-lane numbers, never the
  product bar (the product-lane `main.json` is the llm number).
* Isolation violations > 0 = misrouted rig or a product team-scoping gap —
  the E2E-4 gate, ALWAYS live.
