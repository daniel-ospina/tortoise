# #10463 — does telling extraction what already exists reduce what it creates? **MEASURED: no**

The extractor epic's (`#5087`) central hypothesis, measured as a **pre-registered two-arm ablation**
on the frozen `tests/eval/write_path` corpus.

**VERDICT: `HYPOTHESIS-FALSIFIED (ratio 0.870)`** — telling extraction what already exists in the
graph cut created nodes by **13 %**, not the pre-registered **≥ 25 %**; and it did so while recovering
**fewer** real facts and wiring **fewer** operator edges, so the cut was not a saving at all.

| | S3-**OFF** (no priors injected) | S3-**ON** (real `search_graph`) |
|---|---|---|
| `created_new_nodes` (extraction claims + minted Object/Event) | **284** | **247** |
| ratio | — | **0.8697** (bar: `<= 0.75`) |
| real facts recovered (planted units, of 90) | **47** | **40** ← guard FELL |
| operator-type edges | **171** | **139** ← guard FELL |
| model calls | **21** | **27** (1.29 ×) |
| arm integrity (proven / 14 session-arms) | **7/7** suppressed (`queries_run=0`, 0 real calls) | **7/7** resolved (`mode=real`, `degraded=False`, `queries_run>=1`, 7 real calls) |

## Per-session breakdown (the honesty the n=7 bar needs)

| session | OFF | ON | Δ | ON/OFF |
|---|---:|---:|---:|---:|
| wp01_quarry_debug | 45 | 39 | −6 | 0.867 |
| wp02_lumen_refactor | 51 | 36 | **−15** | 0.706 |
| wp03_ember_design | 43 | 34 | −9 | 0.791 |
| wp04_aurora_perf | 42 | 41 | −1 | 0.976 |
| wp05_retro_writeup | 34 | 35 | +1 | 1.029 |
| wp06_quarry_rollout | 42 | 35 | −7 | 0.833 |
| wp07_bluepeak_followup | 27 | 27 | 0 | 1.000 |

* per-session ratio **range 0.706 … 1.029**; **5 of 7** sessions fell (E2b's `>= 4 of 7` met),
  one rose (`wp05`, +1) and one was flat (`wp07`, 0), so the effect is not uniform across the corpus.
* concentration (largest session's share of the **38-node** total reduction — reductions are floored
  at 0, so `wp05`'s +1 does not shrink the total): **0.395** — below the
  pre-registered `0.60` threshold, so **no `SINGLE-SESSION-DRIVEN` qualifier** applies. wp02 alone
  supplies 15 of 38 nodes.
* **wp01 is blind in both arms** (fresh graph, nothing exists yet) and still shows −6 (−13 %). That
  is the run's own estimate of **run-to-run noise**, and it is the size of the effect that remains.

## Why the falsification is not just "not big enough"

The epic's own **tension rule** (registered as E2a/E4 before the run) says volume may not be bought
by extracting less real content. Both guards fell, in the same direction as the create count:

* real facts **47 → 40** (−15 %) and created nodes **284 → 247** (−13 %): created-per-real-fact is
  **6.04 → 6.18**, i.e. slightly WORSE. The treatment did not remove redundancy; it removed content.
* operator edges **171 → 139** (−19 %), so the cut is not confined to duplicate entities — the
  claim-to-claim structure thinned out more than the node count did.
* cost went **up** (21 → 27 model calls): prior injection gives S4 more to review.

So the honest reading is: **S3 prior-injection at this head reduces creates by about 13 % and reduces
recovered real content by about 15 % — it is a smaller extraction, not a leaner one.** Whether the
7-session corpus can carry the 25 % bar at all is answered below.

## Can the corpus carry a ±25 % bar at n = 7? Measured, not assumed

**Not at session level, and only barely at corpus level.** Two independent replays of the SAME OFF
arm (identical code, fresh graphs) were available:

* total created nodes: **273** (replay A) vs **284** (replay B) — a **4 %** corpus-level difference;
* per session the two OFF replays differed by up to **±31 %** (wp07: 39 vs 27; wp02: 39 vs 51).

The measured treatment effect (13 % at corpus level) sits **above** the corpus-level replication
band (4 %) but **below** the pre-registered bar (25 %), and not every session moved the same way:
one rose (`wp05`, +1), one was flat (`wp07`, 0), and a third moved by a single node (`wp04`, −1). A per-session verdict at this n would be a coin flip; the corpus-total comparison is
the only stable one, and it is what the pre-registration made primary.

⚠️ **Disclosure — replay A is an aborted run, not a second measurement.** The first invocation of the
probe aborted after its OFF arm on a probe defect (the summary loop indexed a string: `arm["arm"]` on
an `str`), and its S3 witness was also wrong for the ON arm (see the receipt finding below). It
produced **no ratio and no verdict** — no bar was compared to any number. Its OFF-arm counts are used
here for exactly one purpose, as a **noise estimate**, after the defect was fixed and the bars were
already frozen. Its full output is preserved in the PR description, not in this bundle: one bundle
carries one measured log.

## Instrument findings (for `#5087`, not fixed in this PR)

1. **`capture_session`'s receipt carries NO `search` block.** `_commit_session_v2` surfaces S3's
   report (`tortoise/sdk.py:7200`-ish, the step witness); the `capture_session` → `_extract_session_v2`
   lane does not, so `#5087`'s indicator 3 ("asserted from the run receipt") is **not satisfiable on
   the capture lane** at this head. This probe therefore asserts the arm's S3 state from **the value
   the S3 function actually returned** during each session, recorded by a wrapper around the real
   function (ON) and the stub (OFF) — the function's own output, never a flag. That is a deviation
   from the pre-registration's wording, disclosed in the log, and the receipt gap is a follow-up.
2. **`queries_failed` is not a receipt field.** `search_graph` degrades the whole search on the first
   query failure, so `queries_failed == 0` is asserted as `degraded == False`; the log says so beside
   the number instead of implying a key that does not exist.

## Post-run instrument hardening — disclosed, and **no number moved**

The measurement above was produced by `tools/s3_prior_ablation.py` as committed in `325f2a1a5`.
Review of this PR then found eight hardening gaps in the probe. **All eight were fixed AFTER the
run, and none of them changes a reported number** — the arithmetic (`created_counts`, `real_facts`,
`concentration`, `decide`'s bar comparisons) is unchanged, and the run's own log already printed
each session's S3 state at the moment of that session's capture. What the hardening does is make
one *observed* fact **machine-enforced**:

1. **Arm integrity could be inherited.** The S3 witness was cleared only inside the wrappers, so a
   session that never reached the S3 call site could be "proven" from the previous session's dict;
   and the real-call count was an ARM total (7 calls over 7 sessions could be 7 in one session and 0
   in six). Now the witness is cleared **per session**, the count is read as a **per-session delta**,
   and an empty witness is unprovable however healthy the blob reads. ⚠️ **What the measured run
   does and does not establish about this:** every session's own printed line read `s3=resolved`
   (ON) / `suppressed (OFF-ARM)` (OFF) immediately after its capture, and the ON arm made 7 real
   calls across 7 sessions. That is *consistent with* one real call per session — it is **not proof
   of it**, because the pre-fix instrument carried only an ARM total and could let a session with no
   call inherit the previous session's witness. So the reported `14 of 14` is what the **pre-fix**
   instrument recorded; the per-session proof is enforced only for future runs.
2. **`denominator_floors.sessions` was never checked** — now it refuses.
3. **Two pinned bars were parsed and never compared** — `guard_ratio_min` (the guards hardcoded
   `>=`) and `corroboration_min_sessions` (E2b's qualifier was computed into the payload and never
   emitted). Both are now compared, and E2b's failure prints `not-majority-of-sessions` beside the
   verdict instead of vanishing.
4. **A refusal exited 1**, indistinguishable from a crash; refusals are now exit **3**, and an
   `--out` escaping the bundle is exit 2.
5. **An existing `--out` was silently overwritten** — the residual tuning path (re-run, replace the
   evidence). It now refuses without `--overwrite`, and `--prereg`'s document is cross-checked
   against `preregistration.json`'s `document_sha256` so a re-cut argument beside a stale record is
   refused.
6. **The source-sha allowlist was `docs/evidence/` + `tools/`**, which made this probe's own
   documented reproduce command REFUSE at this PR's head (the PR also touches `docs/architecture/`
   and `tests/`). It is now `docs/`, `tools/`, `tests/` — production code under `tortoise/` is still
   refused, and the corpus is guarded by its `fixtures_hash` instead of by path.
7. **The log did not say which probe revision produced it** — the log's `head=a1075dfc` names the
   commit the bars were read from, not the tool revision; this README now states it (`325f2a1a5`).

No bar, denominator, corpus or metric definition was touched by any of this, and the run was **not**
repeated to get a friendlier number (the probe now refuses to overwrite the log at all).

⚠️ **Two emissions therefore post-date the measured log**, which the log cannot carry because it was
written before they existed — both are computed from the numbers that ARE in it, and both are printed
here with their pre-registered bar so a reader does not have to take them on trust:
**E2b** `SESSIONS WHOSE CREATES FELL: 5 of 7` (bar `>= 4`, **met** — so no
`not-majority-of-sessions` qualifier); **E5** `model calls 27 vs 21 = 1.286 ×` (bar `<= 2.00 ×`,
**met**). A cycle-2 review also found that the per-session integrity counter was incremented by the
OFF **stub**, which would have made every OFF session unprovable by construction on any re-run
(`ARM-INVALID` 0 of 7 always) — fixed to count only calls to the REAL function, which is what E3's
registered wording asks for. The measured run predates that defect, so its `14 of 14` stands.

## Reproduce

```bash
uv run python tools/test_lane.py run -- true          # a private FalkorDB lane
export TORTOISE_DB_URI=... TORTOISE_TEST_DOCKER_PORT=... TORTOISE_TEST_DOCKER_PASSWORD=...
# A NEW filename: the committed log is NEVER overwritten (the probe refuses an existing --out).
uv run python tools/s3_prior_ablation.py \
    --prereg a1075dfc2a964a6e9e1a49ec990ebadf8c9a180a \
    --out "docs/evidence/5087-hypothesis/s3-ablation-$(git rev-parse --short HEAD).log"
uv run python tools/capstone_preregistration_check.py --dir docs/evidence/5087-hypothesis
```

`tools/s3_prior_ablation.py` reads its bars out of the **pre-registration commit**
(`git show <sha>:docs/evidence/5087-hypothesis/PRE-REGISTRATION.md`), re-derives the corpus
`fixtures_hash`, refuses an `--out` that escapes this bundle, and refuses a `source_sha` whose
non-evidence files differ from `HEAD`. The node census is
`docs/evidence/7949-capstone/probes/capstone_measure.py::census`, imported read-only — there is no
second census.
