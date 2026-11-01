---
title: "#10463 — does telling extraction what already exists reduce what it creates? MEASURED: no"
type: evidence
domain: platform
doc_status: live
created: 2026-11-01
updated: 2026-11-01
subjects.team: epistemic-team
aboutSubjects: extraction prior-injection (S3), write-path corpus, pre-registered two-arm ablation
aboutObjects: "#10463, #5087, tools/s3_prior_ablation.py, tests/eval/write_path, docs/evidence/7949-capstone/probes/capstone_measure.py"
---

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
* per session, the two OFF replays differed by up to **±31 %** (`wp07`: 39 vs 27; `wp02`: 39 vs 51)
  — those are the only two sessions whose per-replay counts were written down (see the disclosure
  below), so read that as *at least* ±31 %, not as the corpus-wide maximum.

The measured treatment effect (13 % at corpus level) sits **above** the corpus-level replication
band (4 %) but **below** the pre-registered bar (25 %), and not every session moved the same way:
one rose (`wp05`, +1), one was flat (`wp07`, 0), and a third moved by a single node (`wp04`, −1). A per-session verdict at this n would be a coin flip; the corpus-total comparison is
the only stable one, and it is what the pre-registration made primary.

⚠️ **Disclosure — replay A is an aborted run, not a second measurement.** The first invocation of the
probe aborted after its OFF arm on a probe defect (the summary loop indexed a string: `arm["arm"]` on
an `str`), and its S3 witness was also wrong for the ON arm (see the receipt finding below). It
produced **no ratio and no verdict** — no bar was compared to any number. Its OFF-arm counts are used
here for exactly one purpose, as a **noise estimate**, after the defect was fixed and the bars were
already frozen. ⚠️ **What replay A leaves behind, exactly:** its corpus total (**273** created
nodes — replay B, the measured OFF arm, is **284**) and the two per-session pairs quoted above. Its
full output was **never captured
to a file** and is **not preserved anywhere** — not in this bundle, and not in the PR description,
which carries only those summary figures (an earlier revision of this README said otherwise; it was
wrong, and the pointer did not resolve). One bundle carries one measured log, so replay A stays
unscored: the 4 % corpus-level band and the two quoted session swings are checkable against this
bundle's numbers, and the wider per-session noise distribution is **not reproducible** and is claimed
nowhere.

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

The measurement above was produced by `tools/s3_prior_ablation.py` **as an uncommitted working
copy** whose content is what `325f2a1a5` commits — **not** by the probe as committed at the log's
`head=a1075dfc2`. ⚠️ That distinction is checkable, and it was a real hole rather than a wording
nicety: the log prints a `S3 WITNESS` line that **does not exist** in the probe at `a1075dfc2`
(`git show a1075dfc2:tools/s3_prior_ablation.py | grep -c "S3 WITNESS"` → `0`, `325f2a1a5` → `1`),
so its `head=` line names the commit the bars were read from, not the tool bytes that ran. The pin
could not see the difference — it compared commits — which is why item 9 now refuses a dirty tree and
the log carries the probe's own `sha256`. Review of this PR then found eight further hardening gaps
in the probe. **All were fixed AFTER the run, and none of them changes a reported number** — the
arithmetic (`created_counts`, `real_facts`,
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
   refused, and the corpus is guarded by its `fixtures_hash` instead of by path. ⚠️ A bare prefix
   allowlist was too wide in the other direction too: the artifacts that **compute** the reported
   metrics all live under those prefixes — the census
   (`docs/evidence/7949-capstone/probes/capstone_measure.py::census` → `created_new_nodes` and
   `operator_edges`), `tests/eval/write_path/grading.py::macro_survival_counts` → `real_facts`, and
   `tests/eval/write_path/corpus.py::compute_fixtures_hash` → the corpus guard itself — so they could
   have differed between the pinned `source_sha` and `HEAD` without a refusal, which is the tuning
   path the pin exists to close. They are now pinned **explicitly** (`METRIC_ARTIFACT_PREFIXES`): a
   run whose metric-definition code differs from `source_sha` is **REFUSED**, so the pinned sha is the
   code that computed the numbers and not merely the code that ran. **No number moves** — the measured
   run's own changed-file line lists three files (`PRE-REGISTRATION.md`, `preregistration.json`,
   `tools/s3_prior_ablation.py`) and none of them is a metric artifact, so the run **as recorded**
   would still be admitted by the new pin; it is a later re-run at a later head that the pin now
   holds to the same metric code.
7. **The log did not say which probe revision produced it** — the log's `head=a1075dfc` names the
   commit the bars were read from, not the tool revision, and the producing working copy was
   **uncommitted** (see the note above; this README previously claimed the run was "as committed in
   `325f2a1a5`", which is not what the log can support). Two things now make that impossible to hide
   again: the log carries `probe_sha256=<sha256 of the probe file>` — the bytes that ran, committed
   or not, the way the bundle already records `fixtures_hash` — and `assert_clean_tree` refuses a run
   whose working copy differs from its commit over the trees that can move a number (item 9).
8. **The log was written ONCE, at the end** — every line was buffered in memory and the file was
   written only on a clean exit, so an aborted run left no file at all. That is precisely why replay
   A's output does not exist. Each line is now written and flushed as it is logged, so a run that
   dies mid-way still leaves the sessions it already measured on disk. The one-measured-log refusal
   is unaffected (the file is still created once; it just no longer needs the process to co-operate).
   **No number moves** — the measured log is unchanged and was already complete.
9. **The pin could not see the WORKING TREE**, so "the pinned sha must be the tree that runs" was
   unenforced for exactly the case that produced this bundle: `assert_source_sha` diffs two
   **commits**, so an uncommitted edit to production extraction code, to a metric artifact, or to
   this probe was admitted silently while the log still read as a clean-tree assertion (item 7's
   `S3 WITNESS` line that its own `head=` commit does not contain is that hole, observed). The tree
   is now asserted clean over `tortoise tests/eval/write_path docs/evidence/7949-capstone/probes
   tools/s3_prior_ablation.py` (`git status --porcelain -- …` must be empty, refused at `exit 3`
   before any model call), and the log prints `tree=<git describe --dirty>` beside the changed-file
   line so it can never read as a clean-tree assertion it did not make. **No number moves** — the
   measured run predates the guard and both arms ran in one process from one working copy.
10. **Two fail-open ORDERINGS in `main`, neither of which changed a number.** (a) The log file was
   opened and its eleven header lines written **before** the `DEEPSEEK_API_KEY` and
   `TORTOISE_DB_URI` refusals, so a keyless or URI-less invocation exited 3 having left a
   header-only `*.log` inside this bundle — a file that reads as the setup of a run that never
   started, and the next attempt with the same `--out` was then refused at exit 2 by the one-log
   rule while the documented reproduce command passes no `--overwrite`. Both prerequisites are now
   checked **before anything is written**, so a refused run leaves **no file** at all. (b) The
   document↔record cross-check was `if preregistration.json is a file`, so **deleting the record
   switched the only check that the bars came from the document off** — no refusal, no line in the
   log saying it had not run. A missing record, a record with no `document_sha256`, and an
   unparseable record all **refuse** at `exit 3` now: absence is not an exemption.

No bar, denominator, corpus or metric definition was touched by any of this, and the run was **not**
repeated to get a friendlier number. The one-log rule is enforced from the FILE, not from the flag
honour system: an existing `--out` is refused, and `--overwrite` — which exists for an aborted,
unmeasured run such as replay A — is itself refused when the existing log carries a `RATIO:` or
`VERDICT:` line, because only a run that reached a verdict can have written one.

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

⛔ **The command below is admitted only at `a1075dfc2` — the head the measured run was taken at**
(the commit `--prereg` names, and the `head=` the committed log prints). The pin refuses a head
whose diff from `source_sha` (`718ee3b49`) touches any file outside `docs/`, `tools/` and `tests/`,
and `config/` moves on nearly every merge — this PR itself touches two of those files — **so at a
later head the first command REFUSES at `exit 3`, by design**, naming the offending files. That
refusal is the pin working, not a broken checkout: it is what stops *"reproduce it at whatever head
is handy"* from becoming *"reproduce it with code the measurement never ran"*. Reproducing at a
later head therefore needs a **new pre-registration** (that is the cost of the frozen §6 pin, and
it is the property that makes the recorded bars untunable).

Check the admission first — **empty output means the pin admits this head**:

```bash
git diff --name-only 718ee3b497539540ea10269e7ccfe9b0867d010f HEAD   # must print nothing
```

The working copy must be clean too — over `tortoise`, `tests/eval/write_path`,
`docs/evidence/7949-capstone/probes` and `tools/s3_prior_ablation.py` — because a commit id cannot
certify a dirty tree, and a probe edited in place is not the probe the bars were read for:

```bash
git status --porcelain -- tortoise tests/eval/write_path \
    docs/evidence/7949-capstone/probes tools/s3_prior_ablation.py   # must print nothing
```

Then, at an admitted head, with a private FalkorDB lane:

```bash
uv run python tools/test_lane.py run -- true          # a private FalkorDB lane
export TORTOISE_DB_URI=... TORTOISE_TEST_DOCKER_PORT=... TORTOISE_TEST_DOCKER_PASSWORD=...
# A NEW filename: the committed log is NEVER overwritten (the probe refuses an existing --out).
uv run python tools/s3_prior_ablation.py \
    --prereg a1075dfc2a964a6e9e1a49ec990ebadf8c9a180a \
    --out "docs/evidence/5087-hypothesis/s3-ablation-$(git rev-parse --short HEAD).log"
uv run python tools/capstone_preregistration_check.py --dir docs/evidence/5087-hypothesis
```

The log is written **incrementally** (every line flushed as it is logged), so a run that aborts
mid-way still leaves the sessions it already measured on disk — the gap that lost replay A's output.

`tools/s3_prior_ablation.py` reads its bars out of the **pre-registration commit**
(`git show <sha>:docs/evidence/5087-hypothesis/PRE-REGISTRATION.md`), re-derives the corpus
`fixtures_hash`, refuses an `--out` that escapes this bundle (or one that already carries a
`RATIO:`/`VERDICT:`, even under `--overwrite`), and refuses a `source_sha` whose
non-production files differ from `HEAD` **or whose metric-definition code differs at all**
(`METRIC_ARTIFACT_PREFIXES`). It also refuses a **dirty working copy** over the trees that can move a
number (`assert_clean_tree`), and logs `tree=` (`git describe --dirty`) and `probe_sha256=` so a run
names both the commit and the bytes it executed. Every refusal happens **before the log file is
created**, so a refused run leaves nothing behind, and the document is cross-checked against
`preregistration.json`'s `document_sha256` — **a bundle whose record is missing, unparseable or
carries no digest is refused**, never admitted with the check silently skipped. The node census is
`docs/evidence/7949-capstone/probes/capstone_measure.py::census`, imported read-only — there is no
second census.
