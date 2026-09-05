# Phase G product-lane re-bless — DEFERRED (2026-09-05)

Epic #2080 W5 Phase G (#2104). The product-lane (LLM-extractor) re-run that
would re-measure `tests/eval/write_path/baselines/main.json` could NOT be
completed today: four consecutive sealed runs ended `inconclusive`
(`run_status: failed`, `failure_origin: runner_error`) because the OpenRouter
LLM extractor produced an EMPTY capture for exactly one session per run
(a different session each time — wp02, then wp03, then wp01/wp02/wp03/wp04,
then wp01). Run receipts are preserved in this directory:

| Receipt | Sessions emitting | Notes |
|---|---|---|
| w2b-phaseg-llm-2026-09-05.json  | 4/5 (wp02 lost) | macro 0.097 |
| w2b-phaseg-llm-2026-09-05b.json | 4/5 (wp03 lost) | macro 0.292 |
| w2b-phaseg-llm-2026-09-05c.json | 1/5 (wp05 only) | macro 0.014 |
| w2b-phaseg-llm-2026-09-05d.json | 4/5 (wp01 lost) | macro 0.083 |

## Diagnosis (evidence, not hypothesis)

- Small OpenRouter completions are healthy (3/3 direct probes, ~1.5 s).
- The hazard is the documented #1549 pattern: large session-transcript
  completions stall mid-chunked-response; the extractor's deadline/retry
  machinery eventually aborts → the capture degrades to EMPTY *without an
  exception* → the runner correctly marks the session non-emitting and voids
  the run (`verdict: inconclusive`). No product-side regression is present:
  every run's emitting sessions carry clean quote_fidelity=1.0 and
  provenance_accuracy=1.0, and the failure class is uniformly
  content_missing on the empty session.
- `cost_usd: 0.0` on every run is a separate usage-ATTRIBUTION gap in this
  lane (real LLM content is produced — emitting sessions write 10-21 points
  with transcript-accurate content), tracked in the follow-up below.

## What IS verified (the CI-gate half of Phase G)

The deterministic m2/echo lane gate passed and its baseline is blessed
(`tests/eval/write_path/baselines/m2.json`, receipt
w2b-m2-lane-2026-09-03.json): macro 0.9722, verdict PASS on clean replay —
the can-fail CI gate for write-path regression. Phase C's fix-wave #1
(ep_update_missing) shipped; the write-path baseline main.json (0.25) with
its named failure classes (content_missing = extractor recall) remains the
published W4 survival-target justification.

## Follow-up (opened)

Re-run `tests/eval/write_path/runner.py run` under stable OpenRouter
conditions (or after the #1549 extractor retry/deadline hardening lands) to
completion — a run with 5/5 sessions emitting is REQUIRED before blessing a
revised main.json; also fix the lane's usage-attribution zero (cost_usd).
Until then Phase G's product-lane blessing stays deferred; the epic gate's
CI half stands.
