# Pre-registration — does telling extraction what already exists reduce what it creates? (#10463 / #5087)

**Written before any run of the two-arm ablation.** This document is frozen by its commit; the probe
reads its bars out of that COMMIT (`git show <sha>:<this file>`), never out of the worktree, so a
threshold edited after the numbers are in cannot be served. `preregistration.json` beside it is the
machine record the probe consumes, and it pins this document's sha256.

## 0. The hypothesis, stated so it can fail

The extractor epic's central hypothesis (`#5087`, *"Not yet measured"*):

> Telling extraction what already exists in the graph (S3 prior-injection) **reduces what it creates**.

An **arm** is one full replay of the frozen corpus through `TortoiseSDK.capture_session` — the real
entry point — onto a **fresh, disposable graph**:

| arm | S3 (`extractor_v2.search_graph`) | what the writer is told |
|---|---|---|
| **OFF** | **provably does not run**: replaced, in-process, by a stub returning an empty prior set for every session (`queries_run=0`, `reason` names `OFF-ARM`) | nothing exists |
| **ON** | the real `search_graph`, unmodified | whatever the graph already holds |

The OFF arm is not "a degraded lookup": a degraded lookup renders *"assume everything is new"*, which
is semantically the treatment removed but arrives through a failure. The stub returns a **healthy,
zero-result** search (`degraded=False`, `entities/points/events` empty), so the OFF arm differs from
the ON arm in exactly one thing — whether priors were injected — and in nothing else.

Sessions replay in fixture order into the same per-arm graph, so from session 2 onward the ON arm has
prior content to find. Session 1 is blind in BOTH arms (fresh graph, nothing exists); that is stated
here so its near-zero delta is not read as a treatment effect after the fact.

## 1. The corpus (frozen, hashed)

`tests/eval/write_path`, **7 sessions**, `fixtures_hash`
`sha256:dd7e85200f4809f701b9e9c11816b541acea63afd0b2472998975e2ebcb035dc` — the corpus `#9075`
pre-registered. The probe re-derives the hash from the tree and **REFUSES** a different one. `n = 7`
sessions, 14 measured session-arms (7 per arm).

## 2. The metrics

* `created_new_nodes` — **extraction nodes** written per session: extraction-claim `Point`s plus
  the `Object` and `Event` nodes the run minted, read off
  `docs/evidence/7949-capstone/probes/capstone_measure.py::census` (imported **read-only**; no second
  census exists) as the delta of `point_composition.extraction_claims + per_label.Object.nodes +
  per_label.Event.nodes` across the session boundary, on a fresh graph, so every node counted was
  written by this run. Turn-row Points (`is_episodic`) and operator Points are **excluded on
  purpose**: both arms write them under the same rules, and counting them pulls the ratio toward 1.0
  for reasons unrelated to prior-injection. The wider `Σ label_counts` delta and each per-label
  delta are printed beside it as a disclosed aside so a label-inflation artefact is visible rather
  than argued about.
* `real_facts` — the corpus's planted units recovered by content, scored with the corpus's own
  matcher `tests.eval.write_path.grading.macro_survival_counts(gold, points)` over the **arm's whole
  Point set at the end of the replay**, not over the Points written in one session: the hypothesis
  predicts FOLDING onto an existing canonical Point, which writes no new Point, so a
  written-in-session scope would charge the ON arm for exactly the behaviour under test. A unit is
  recovered when any Point in the arm's graph retains its anchor. Denominator: the **90** planted
  units.
* `operator_edges` — a **graph-wide count of operator-type edges**
  (`IMPL/NAND/SUPPORTS/MITIGATES/NEGATE/SUPERSEDE/INPUT`) in the arm's graph, read off the same
  census (`operator_relationships`). This is an edge-count guard on operator volume, **not** a
  measurement of recovery of the corpus's planted edges; the **15** planted operator edges are the
  corpus's reference scale (and `#7949`'s E4 denominator), and the comparison is ON vs OFF.
* `cost` — model calls, from each receipt's `stats.llm.calls`.
* **arm integrity** — from the RUN RECEIPT, never from the flag being set: each session's receipt
  `search` block must show `mode="real"`, `degraded=False` and `queries_run ≥ 1` in ON, and
  `queries_run == 0` with the `OFF-ARM` reason in OFF. (`queries_failed` is not a receipt field at
  this head: `search_graph` degrades the whole search on the FIRST query failure, so
  `queries_failed == 0` is asserted as `degraded == False`. The probe says so in the log.)

## 3. The bars — stated BEFORE the run

| id | metric | denominator | threshold |
|---|---|---|---|
| **E1** | `created_new_nodes` (extraction-claim Points + minted Object/Event nodes) in the S3-ON arm ÷ the same count in the S3-OFF arm, summed over the 7 sessions — **the primary bar** | the S3-OFF arm's `created_new_nodes` total over the 7 sessions (floor ≥ 30 nodes) | `<= 0.75` (a fall of ≥ 25 %) |
| **E2a** | planted units recovered by content (`macro_survival_counts().survived`) against the ARM's whole Point set — **the guard: real-fact count may not fall** | the S3-OFF arm's recovered count, over the **90** planted units | `>= 1.00` × the OFF arm's count |
| **E2b** | sessions whose `created_new_nodes` fell relative to the matched OFF session (corroboration; a qualifier, never the primary bar; session 1 is blind in both arms and is expected ≈ 0) | the **7** matched session pairs | `>= 4 of 7` |
| **E3** | session-arms whose receipt proves the arm's S3 state — ON: real `search_graph` executed (counter ≥ 1) with `mode="real"`, `degraded=False`, `queries_run>=1`; OFF: `queries_run==0`, `degraded=False`, an `OFF-ARM` reason, and **zero** real-function calls — **arm integrity, asserted first** | the **14** session-arms (7 per arm) | `= 14 of 14` |
| **E4** | graph-wide operator-type edge count (`operator_relationships`) in the ON arm — **the guard: operator volume may not fall** (an edge count, NOT recovery of the planted edges) | the S3-OFF arm's operator-edge count; the **15** planted operator edges are the corpus's reference scale | `>= 1.00` × the OFF arm's count |
| **E5** | model calls spent by the ON arm (cost disclosure) | the S3-OFF arm's total `stats.llm.calls` | `<= 2.00` × the OFF arm's calls |

**The verdict mapping is fixed here, before the numbers:**

1. `E3` fails → **`ARM-INVALID(<which>)`**. No ratio is reported from an arm whose S3 did not resolve;
   a degraded ON lookup is a measurement of the degradation, not of the hypothesis.
2. A denominator floor unmet (`created_new_nodes(OFF) < 30`, OR `real_facts(OFF) < 20`, OR the
   session count ≠ 7 — the three floors in `denominator_floors` below) → **`REFUSED(<why>)`**.
   Never 0. All three floors are listed HERE so the document and the machine record cannot refuse
   on different ones.
3. `E1` met **and** both guards met (`E2a`, `E4`) → **`HYPOTHESIS-SUPPORTED`**.
4. `E1` met but a guard violated → **`HYPOTHESIS-FALSIFIED (ratio X, GUARD-VIOLATED: <metric>)`**.
   The epic's own tension rule makes this a falsification, not a partial win: volume bought by
   extracting less real content is not "creating less", it is "measuring less".
5. `E1` not met (ratio > 0.75, in particular `≈ 1.0`) → **`HYPOTHESIS-FALSIFIED (ratio X)`**. This is
   a valid close and it changes the epic's plan. **It is never re-run until it agrees.**

`E2b` does not change the verdict; it qualifies it (a failing `E2b` is reported as
`(not-majority-of-sessions)` beside the verdict, never folded into the ratio). `E5` does not change
the verdict; it is disclosed.

**The concentration statistic, defined before the run.** For each matched session pair, the
reduction is `max(0, created(OFF) − created(ON))` (floored at 0, so a session that *increased*
cannot subsidise another's share). `share = largest single-session reduction ÷ Σ reductions`. When
`Σ reductions ≤ 0` the statistic is **undefined** and is reported as `None` — never a 0 that reads
like a clean result — and no qualifier is applied.

**`required_criteria_rows: [E1, E2, E3, E4, E5]` is the checker's slot vocabulary**, not a sixth
criterion: `tools/capstone_preregistration_check.py` requires those exact slot names and allows the
`E2` slot to be carried by the `E2a`/`E2b` sub-rows. No row named `E2` exists in the table above by
design.

## 4. Analysis rules, registered against the known traps of a percentage bar at n = 7

Read before the run (Lakens, *Improving Your Statistical Inferences* §13 — pre-register outlier rules
and analysis deviations; outlier-management guidance that recommends pre-registering the rule and
reporting with and without the outlier). What that changed here:

* **A single session can carry a 7-session mean.** So the per-session ratios are ALL printed, with
  their min/max range; and the concentration statistic — the largest single session's share of the
  total create reduction — is reported. If that share exceeds **0.60**, the verdict carries the
  qualifier **`(SINGLE-SESSION-DRIVEN)`**. Registering the qualifier before the run is what stops it
  being a post-hoc excuse, and refusing to *drop* the session is what stops it being a post-hoc save.
* **No outlier is dropped, winsorised or re-weighted.** The primary bar is computed over all 7
  sessions. A second, disclosed "without the largest contributor" figure may be printed for the
  reader, and it is labelled as an aside that decides nothing.
* **Session 1 is blind in both arms** (§0). Its expected delta is ≈ 0 and it is disclosed as such.
* **Any deviation from this document is reported as a deviation in the log and in the PR**, with what
  changed and why, never silently absorbed.
* **No run is repeated to obtain a friendlier number.** One run of the pair is the measurement; a
  second run is a new pre-registration.

## 4b. Amendment 1 — made BEFORE any arm was run (review-driven)

The first review of this document (dispatched before any run, per the lane's §1b rule) found that
four statements were underspecified and could have given a tuning path. All four were fixed **before
the first model call**, and nothing in this list was chosen with a result in view (no arm had run):

1. `real_facts` was scoped to the Points **written in the session**, which would have charged the ON
   arm for folding onto an existing canonical Point (a fold writes no new Point) — the guard would
   have violated the treatment by construction. Now scored over the arm's whole Point set (§2).
2. `created_new_nodes` was `Σ label_counts`, which includes turn-row Points and operator Points that
   both arms write identically, pulling the ratio toward 1.0. Now extraction nodes only (§2), with
   the wider delta printed as an aside.
3. `operator_edges` claimed a denominator of "the 15 planted edges" while the metric is a graph-wide
   edge count — the denominator is now stated as the OFF arm's count with the 15 as reference scale
   (§2, §3 E4).
4. The E3 ON witness was `queries_run >= 1` alone, which cannot distinguish "the real function ran"
   from a skip path. Now a call counter on the real function plus the receipt fields (§3 E3); and the
   concentration statistic, the `E2b` qualifier, and all three floors are now stated explicitly
   (§3 rules 2 and `E2b`).

The BARS did not move: `0.75`, `1.00`, `4 of 7`, `14 of 14`, `15`, `0.60`, `2.00` are the numbers
this document was committed with in its first commit, and the commit that carries this amendment
changes only definitions and floors — never a bar.

## 5. Floors, cost, and the machine record

The run refuses rather than scoring when a denominator floor is unmet (`denominator_floors` below).
The cost is reported in model calls, per arm and per session, so the reader can price the treatment.

```yaml prereregistration-pins
corpus:
  root: tests/eval/write_path
  fixtures_hash: "sha256:dd7e85200f4809f701b9e9c11816b541acea63afd0b2472998975e2ebcb035dc"
  sessions: 7
  seed: 42
  schema_version: 1
  planted_units: 90
  value_bearing_units: 14
  planted_operator_edges: 15
  hedge_scope_sentences: 29
  hedge_scope_sentences_total: 343
  hedge_scope_lexemes:
    - probably
    - maybe
    - might
    - likely
    - possibly
    - seem
    - appear
    - estimate
    - roughly
    - approximately
    - at least
    - up to
    - no more than
    - unless
    - except
    - only
    - until
    - tentative
    - pending
    - assuming
    - depending on
    - should
    - must
    - need to
  value_excluded_unit_ids:
    - wp02_lumen_refactor_u_08
    - wp04_aurora_perf_u_04
salience_labels:
  rows: 25
  in_scope: 15
  positively_classified: 9
  deferred: 6
  detected_classes:
    - definite_description
split:
  tune: [wp01_quarry_debug, wp02_lumen_refactor, wp03_ember_design, wp06_quarry_rollout]
  held_out: [wp04_aurora_perf, wp05_retro_writeup, wp07_bluepeak_followup]
  tune_counts: {units: 53, operator_edges: 7, value_bearing_units: 5, hedge_scope_sentences: 21}
  held_out_counts: {units: 37, operator_edges: 8, value_bearing_units: 9, hedge_scope_sentences: 8}
criteria: [E1, E2a, E2b, E3, E4, E5]
required_criteria_rows: [E1, E2, E3, E4, E5]
e4_denominator: 15
denominator_floors:
  created_nodes_off: 30
  real_facts_off: 20
  sessions: 7
source_sha: "718ee3b497539540ea10269e7ccfe9b0867d010f"
hypothesis_bars:
  primary_ratio_max: 0.75
  guard_ratio_min: 1.00
  corroboration_min_sessions: 4
  sessions_total: 7
  arm_integrity_required: 14
  concentration_max: 0.60
  cost_ratio_max: 2.00
falsifier: "ratio ≈ 1.0 (or any ratio > 0.75) falsifies the hypothesis, closes #10463 and changes the epic's plan"
rerun_policy: "one run of the pair is the measurement; a repeat requires a NEW pre-registration"

# ⛔ The ordering disclosure (#10531). `s3-ablation-a1075dfc.log` and this document entered the
# repository in ONE commit: `#10468` was merged as a SQUASH, which collapsed the two branch commits
# (`a1075dfc2` wrote the bars, a later one added the log) into `cfa3de857`. `git log
# --diff-filter=A` therefore reports the same epoch for both, and no epoch comparison can order
# them. The disclosure below is BOUND to this document by `document_sha256` in
# `preregistration.json`, and it is printed on every run as `ATOMIC-ADD declared`.
atomic_add_ordering:
  s3-ablation-a1075dfc.log:
    reason: >
      The bars were committed BEFORE any arm ran, and the log records that fact itself: it names
      `prereg=a1075dfc2` — the commit its thresholds were read from, not the worktree — and states
      `changed files (3, all evidence/tooling)` between `source_sha` and that head. No bar moved
      between `a1075dfc2` and the merged document (0.75 / 1.00 / 4-of-7 / 14-of-14 / 15 / 0.60 /
      2.00 are the numbers both commits carry), and `a1075dfc2`'s own message says so. The squash
      destroyed the COMMIT ordering; it could not touch the content, which is why the ordering
      claim rests on the content and is disclosed here rather than asserted by a rewritten history.
    branch_commits:
      - "a1075dfc2 prereg(10463): amend definitions before any run — the bars do not move (adds PRE-REGISTRATION.md; 00:12:17Z)"
      - "325f2a1a5 measure(10463): the S3 prior-injection hypothesis is measured and FALSE (adds s3-ablation-a1075dfc.log; 00:26:01Z)"
    squash_merge: "cfa3de857 measure(10463): ... ratio 0.870 (#10468) — both branch commits, collapsed"
    verifiable_by: >
      `git diff --stat a1075dfc2 325f2a1a5 -- docs/evidence/5087-hypothesis/` lists ONLY README.md
      and the log, so PRE-REGISTRATION.md is byte-identical at the commit that froze the bars and at
      the commit that added the measurement: no bar moved between them. The pair is therefore
      ordered by CONTENT, which a squash merge cannot rewrite — it could only destroy the commit
      ordering, which is what it did.
```

## 6. The source sha

`source_sha` above is the commit whose **tree** the run executes. The probe asserts it is an ancestor
of `HEAD` and that every file differing between `source_sha` and `HEAD` is inside `docs/evidence/` or
`tools/` — i.e. **no production code changed** between the pinned sha and the measured run, so the
pinned sha really is what the numbers describe. The file list is printed in the log.
