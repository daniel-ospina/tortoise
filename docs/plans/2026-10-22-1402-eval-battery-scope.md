---
title: "Scope Reconciliation — Epic #1402 Agent-Reasoning Eval Battery (delta scope, not a new scope)"
type: decisions
domain: product
doc_status: draft
created: 2026-10-22
subjects.team: epistemic-team
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
extends: docs/epics/1402-eval-battery/03-scope.md, docs/epics/1402-eval-battery/01-align.md, docs/epics/1402-eval-battery/06-verify.md, docs/claims/agent-reasoning-verdict.md
---

# Scope Reconciliation — Epic #1402 (delta scope)

> **This document does not re-scope epic #1402.** The epic's scope was written, human-approved, and
> 10 of its 12 children have closed (9 of them carrying the `implemented` label). This brief exists because an audit reported #1402 as having
> "zero references from any issue or PR" and therefore no scope and no children — **that premise is
> false**, and acting on it would have produced a second, competing scope document against an
> approved one. What follows is (a) the evidence that falsifies the premise, (b) the root cause of
> the false audit, and (c) the genuinely remaining *delta* scope, now much smaller than "an epic".

**Status:** awaiting owner gate (`G-1402-DELTA`). Nothing here is dispatched, created, or implemented — with the one exception stated in §2 so the claim is not read as covering it: the fleet-audit defect was filed during this brief as **#9701** (a fleet-instrument bug, not a child of #1402 and not a work unit this brief executes).

---

## 1. Align decision

**Decision: REDIRECT — do not re-run the planning pipeline for #1402; execute the delta.**

The epic-workflow was already run to completion for this epic in August 2026. All six stage
artifacts are committed, the scope and plan human gates were passed, decomposition was
MECE-verified, and 10 of 12 children closed on 2026-08-18 (9 of them labelled `implemented`; #1407
closed with `scoped`/`planned`). Re-running Align → Research → Scope would regenerate docs that
already exist and contradict an owner-approved basis.

### Contradiction test (per AGENTS.md, run FIRST)

- **Recorded decision in play:** `docs/epics/1402-eval-battery/01-align.md:14` —
  *"Decision: PROCEED (conditional) — scheduled, not urgent; the verdict is a differentiation
  PROFILE (all metrics scored on all arms) … Reclassification trigger: if launch messaging includes
  the reasoning-uniqueness positioning, this epic moves to Do-now."*
- **Does a fresh scope contradict it?** Yes — a new scope doc with new boundaries/E2E numbering would
  silently supersede the approved `03-scope.md`, which is exactly the adoption-over-a-decision hazard
  the rule names. **This brief therefore reconciles; it does not adopt over, and it does not reverse.**
- **The recorded decision is obeyed as-is:** the epic stays *scheduled, claim-gated, not launch-gated*
  unless the reclassification trigger fires (see gate option B).

### Adversarial test — what could still make this not worth doing

1. **Re-run now, or wait for the extractor?** The battery consumes the product pipeline (#1350 v2, closed
   2026-08-20). Re-running is the only way to convert the no-data verdict into evidence, and it is now
   cheap. Waiting adds no information.
2. **Is #2515 a rival epic?** No — #2515 is explicitly *downstream*: *"consume #1402 machinery (#1416
   verdict), publish missing rows (#2522-#2525)"*. It is a consumer umbrella, not a duplicate.
3. **Is the whole epic waste if the verdict is "not unique"?** No — align §"the differentiation PROFILE"
   makes a negative profile a deliverable, and the profile is what the comparison-systems table consumes.
4. **Cost.** The one real run's **published** figure of `$1.07` at `deepseek-v4-flash` is retracted by the verdict doc this brief extends: that number came from a price basis matching no provider price, and re-pricing the run's OWN token counts puts the true cost at **≈$0.17–0.29** (`docs/claims/agent-reasoning-verdict.md:59–79`, #2874/#2906 — the published figure *"overstates the leg by 3.7-6.4x"*). Order-of-magnitude: tens of cents per arm-run, not thousands. That range is derived from the executed run's arm **`a0` alone**; the `a4` leg was deliberately not run (`docs/claims/agent-reasoning-verdict.md:93`), and `a4` also drives the product extraction path per episode, so its token count is unmeasured and strictly larger — §8 prices the two-arm delta as a floor, not a symmetric estimate.

### Eisenhower / profit alignment

**Important / Not urgent (changed).** When 01-align was written the epic was Important/Not-Urgent. The
reclassification trigger named in that decision has arguably fired: positioning now leads with the graph
as a reasoning medium, and a filed claim doc (`docs/claims/agent-reasoning-verdict.md`) currently states
*"the uniqueness claim does NOT ship"* — an honest no-data state that is now **stale** because its single
blocker closed. Profit chain: correct measurement → defensible positioning → credibility with buyers who
ask "how is this different from Mem0/Zep" → conversion. An unmeasured claim either ships (misleading) or
stays unsaid (lost differentiation).

---

## 2. Evidence that the premise is false

All commands run against `origin/main` @ `df0d0888b` (2026-10-22).

| Claim in the audit | Verified reality |
|---|---|
| "zero references from any issue or PR" | **20 tracked files** under `docs/epics/1402-eval-battery/` (`git ls-tree -r --name-only origin/main docs/epics/1402-eval-battery`); **23 commits** on `main` match `1402` (GitHub commit search for `repo:daniel-ospina/tortoise+1402`, read 2026-10-25); epic body carries a 12-row wiring table |
| "no scope" | `03-scope.md` — 9 in-scope deliverables, 5 out, 7 high-level E2E, integration-surface map S1–S8 |
| "no plan" | `04-plan.md` — 25 detailed E2Es; **Human gate #2 recorded: approved by owner 2026-08-14 ("go")** |
| "no children" | `05-decompose.md` — MECE-verified; **11 child issues #1406–#1416 + capstone #1419** (12 wiring rows) |
| pipeline never ran | `06-verify.md` — `Status: READY`, `Review Gates: 12/12 passed`, `Coherence: 0 issues`, `Artifacts: 5/5` |

**Child state (verified via `gh issue view`):**

- #1406, #1407, #1408, #1409, #1410, #1411, #1412, #1413, #1414, #1415 — **CLOSED** (2026-08-18, labels `implemented`/`planned`).
- #1416 (battery execution + verdict) and #1419 (capstone) — **OPEN**, both re-labelled `not-launch-relevant` on 2026-10-10.

**Code that exists:** `battery/` — 95 tracked files (runner, 6 arm adapters, 5 probe modules, 6 streams,
4 differential legs, parity leg, report/verdict assembler, exposure + liveness, golds), **57 battery test
modules**. The harness is not a plan; it ships.

### Root cause of the false "zero children" audit

`gh issue view 1402 --json subIssues,parent,subIssuesSummary` →

```json
{"parent":null,"subIssues":{"nodes":[],"totalCount":0},
 "subIssuesSummary":{"completed":0,"percentCompleted":0,"total":0}}
```

**The children are not linked to the epic as GitHub sub-issues.** `epic-decompose` recorded the wiring as
a **prose table in the issue body** and in `05-decompose.md`; it never established the `subIssues`
relation. Any audit that reads the sub-issue relation field — the obvious mechanical query — reports
**zero children for a fully decomposed epic**. This is the most likely explanation for "8 of 10 open
epics have thin or zero child issues", and it is a fleet-wide measurement instrument defect, not eight
under-planned epics. Filed as **#9701** (see §7).

---

## 3. What already happened — and why the verdict doc matters

`docs/claims/agent-reasoning-verdict.md` (filed 2026-09-10, #1416) is a **real executed run**:

- `verdict.outcome = MECHANISM-NOT-UNIQUE`, but explicitly **a fallback branch, not a finding** —
  `report_status = incomplete_emitter_gap`, `families_measured / families_expected = 1 / 14`.
- Root cause measured, not estimated: the Task-9 derive/gold truth-emission leg was missing, so
  `family_R1/R3/R5` returned `insufficient_n` with empty value lists; only R4 was derivable (n=5).
- The harness behaved correctly — `probe_scorer.py` emits a no-data sentinel rather than scoring an
  uncovered log ("an honest gap … never a silent pass").
- Blockers were sequenced: **#2740** (emitter leg) → re-run `--arms a4,a0` → re-file verdict;
  then **#2702** (envelope contract — since CLOSED) and **#2525** (comparator-arm data). (That pair of arms
  is two single-arm invocations, not one multi-arm run — see §4, "In scope" item 1.)

**The state has changed since that filing — this is the whole reason a delta scope is needed now:**

| Blocker named by the verdict | State today |
|---|---|
| #2740 derive/gold truth-emission leg (blocks R1/R3/R5) | **CLOSED** |
| #2874 / #2906 spend-meter price basis (receipt integrity) | **CLOSED** |
| exclusion-rate fix lineage #2717/#2721/#2697 (43.6% → 6.4%) | landed |
| #2525 comparator-arm real-run readiness | **still OPEN** |
| #2702 `cal`/`bct` empty-position envelope contract | **CLOSED** 2026-09-12 |

⇒ **The re-run the filed verdict specifies is unblocked — but it is not yet runnable from the CLI, and it
will still return the no-data sentinel for R2/R3/R5.** Three gaps sit between "the blocker closed" and "a
measured profile". None is a regression; each is delta work (§7 rows 8–9):

1. **No production truth judge.** `RunConfig.truth_judge` (`battery/runner/run.py:116,164`) is the seam the
   #2740 emitter leg reads (`battery/runner/run.py:1158` → `probe_scorer.derive_judged_truth`,
   `battery/runner/probe_scorer.py:494–513`), and that function returns immediately on `judge is None`. The
   only assignments in the tree are in tests: **nothing in production supplies one** — no CLI flag, no
   runner default — so R2's `coverage_subscore`, R3's `outcomes` and R5's `update_correct_direction` stay
   absent from any CLI run and score `insufficient_n`.
2. **Probe aggregation is single-arm in this slice.** `battery/runner/run.py:619–622` raises `ConfigError`
   for a probe-scorer run whose arms span more than one, and `battery/runner/probe_scorer.py:754–758`
   raises again at aggregate time (*"aggregation is single-arm in this slice … multi-arm probe runs land
   with the Task 9 executor"*). `--arms a4,a0` **with any probe scorer** dies before the attempt dir exists
   — so "both arms in one run" is not a command that runs today.
3. **The default scorer emits no family files.** `_family_payloads` returns nothing unless the scorer
   exposes `family_reports`, and *"the harness scorer never does"* (`battery/runner/run.py:1182–1185`). A
   verbatim `run` with no `--scorer` therefore reproduces the 0/14-families shape this brief exists to fix.

The verdict doc is still stale in a way that reads against the product claim — that part of the finding
stands unchanged.

---

## 4. Delta scope (the only thing in scope for this brief)

### In scope

1. **Re-run tier-1 — one invocation per arm**, at current `main`, with the #2740 emitter leg live. There is
   **no `battery` console script** (`pyproject.toml` `[project.scripts]` ships only `tortoise*`; the entry
   point is `python -m battery.cli`), and probe scoring is single-arm (`battery/runner/run.py:619–622`), so
   the two arms are two runs, not one `--arms a4,a0`:

   ```bash
   python -m battery.cli run --tier 1 --arms a4 --executor real --seed 7 \
     --scorer battery.probes.r1_contradiction --scorer battery.probes.r2_coverage \
     --scorer battery.probes.r3_calibration --scorer battery.probes.r4_defeat \
     --scorer battery.probes.r5_update
   python -m battery.cli run --tier 1 --arms a0 --executor real --seed 7 \
     --scorer battery.probes.r1_contradiction --scorer battery.probes.r2_coverage \
     --scorer battery.probes.r3_calibration --scorer battery.probes.r4_defeat \
     --scorer battery.probes.r5_update
   ```

   The `--scorer` list is **not** optional: with the default `harness` scorer nothing writes `family_*.json`
   (`battery/runner/run.py:1182–1185`). **R2 is passed too, and omitting it would be an exclusion by
   omission:** `R2` is one of the 14 `METRIC_FAMILIES` (`battery/differential/d1_sweep.py:14–18`), it has
   an implemented probe and `tier: probe` scenarios (`battery/probes/r2_coverage.py`, `family: R2` in
   `battery/config/corpus.yaml`), and the 2026-08-14 owner ruling names "ALL probes (R1–R5; L1–L6; D2–D4)" — so a
   four-scorer command silently drops one of the five R probes. What a CLI run can produce **today** is
   R1/R4 per-family `values`/`cells` in `family_R1.json`/`family_R4.json` on each arm plus the no-data
   sentinel for the judge-gated families
   (R3 `outcomes`, R5 `update_correct_direction`, and R2's `coverage-subscore`, which the probe reads from
   a validated-rubric judge: `battery/probes/r2_coverage.py:1–7,33–37`); those need §7 row 9 (the
   production truth judge) first. That limit is a property of the harness at this head, not of the corpus,
   and it is why this row's indicator is stated per family rather than as "R1–R5". Passed, a judge-gated
   family writes its `family_*.json` and reports `insufficient_n`; NOT passed, it is not reported at all,
   which is the silence E2E-D2 forbids.

   ⚠️ **Two invocations write two attempt directories, and `report` reads ONE.** Each `run` stamps its own
   attempt dir and writes that run's `family_*.json` into it (`battery/runner/run.py:762–763`, `:1093`),
   while `report` resolves the **latest completed** attempt dir only (`battery/cli.py:597–604` →
   `battery/report/assemble.py::attempt_dir_resolve`, which returns `candidates[-1]`). A single
   `profile.json` carrying BOTH arms is therefore not producible by two invocations — the one-run shape is
   exactly what §7 row 8 (the single-arm probe guard) turns on, and until it lands the two-arm evidence is
   read from the two per-arm attempt dirs, one `report` per dir (`--out <attempt-dir>`, which
   `attempt_dir_resolve` resolves to itself).
2. **Re-file the verdict** — `python -m battery.cli report` → re-write
   `docs/claims/agent-reasoning-verdict.md` per the
   branch the measured profile actually supports; keep the falsification-accepting structure (§1–§7)
   and the `report_status` honesty carrier.
3. **Matched-recall is wired — the comparator DATA is what is missing.** `match_recall` has a production
   call site (`battery/recall/prepass.py:231`, invoked from `battery/runner/run.py:1080`) and the
   INCONCLUSIVE outcome is a real exit code (`battery/enums.py:37`), so the control variable *can* be
   enforced. (The 2026-09-10 verdict doc's "no production call site" line was superseded by #3327 /
   PR #3439 on 2026-09-13; #1413 is CLOSED/implemented.) What remains is **not data-only**, and the
   MECE list says so: **#3327 is OPEN** (its own title: four open decision points, *blocks #2525
   indicator 2*), and **#3437 is OPEN** — `a1` is in the pre-pass trigger population but its
   pre-battery factual recall is **0.0 by construction** (`battery/arms/a1_longctx.py` resets
   `_history`, which `record()` only fills during episodes, i.e. after the pre-pass), so for `a1` the
   matched control may be unreachable and would re-create the constant-True trigger #3327 removed.
   Comparator-arm **readiness** for A1/A2/A2b/A3 — which can run, at what cost ceiling, and the
   matched-recall data for each RUNNABLE arm — is #2525's scope in the #2515 umbrella; the sweep itself is
   #1416's Tier-3 wave, **and the readiness package inherits those two blockers**. This corrects a claim in the
   verdict doc — the delta is to fix that claim, not to build a missing producer.
4. **Capstone** — #1419 walks scope E2E-1…7 on the real run and gates epic completion.
5. **Test-design map closure** — #1404 (open) is cited as the verification basis by #1416/#1419.

### Out of scope (unchanged from the approved `03-scope.md` §Out of Scope)

Adaptive/evolving test generator (SEAL-style); public/commercial benchmark packaging; new extraction
work; product UI; marketing/positioning changes before the verdict. **Also out of scope for this brief:
any new child issue creation, any implementation, any second scope document.**

### Sequencing, not a narrowing (the 2026-08-14 owner override stands)

A full Tier-2 + Tier-3 six-arm differential sweep (`E2E-3`) is **not** part of the near-term delta — but it is
neither dropped, nor demoted, nor re-defined. The recorded owner override on this epic
(`docs/epics/1402-eval-battery/01-align.md:57`, 2026-08-14) REJECTED the reviewer's de-tilt and set the rule
as *"ALL probes (R1–R5; L1–L6; D2–D4) are scored on ALL arms and reported in a full delta profile …
exclusions hide information"*; #1402's own Targets field repeats it (*"full differentiation profile (all
metrics × all arms, no exclusions — owner decision"*). This brief orders work INSIDE that ruling; it does not
reverse it. The sequencing is: tier-1 with real per-arm numbers (cheap in dollars, blocked on §7 rows 8–9) →
real comparator-arm data (#2525) → the six-arm sweep (`03-scope.md` deliverables 4/5/6) → and **#1402 stays
OPEN until all of it is measured** (gate A, §8).

An earlier framing of this section treated the approved Tier-2/Tier-3 deliverables as out of scope under the
heading *"Non-goal correction"*. That is an exclusion-by-omission of the owner's ruling, which is the exact
hazard §1 says this document avoids; the deliverables are in, sequenced. The 1/14-family profile in the filed
verdict was **not** caused by the sweep either: no sweep was attempted (that run was tier-1, `--arms a0`
only); it was caused by the missing Task-9 derive/gold leg, which is now closed as issue #2740.

---

## 5. High-level E2E test cases (delta only)

> Registered as **additions** to the approved `03-scope.md` E2E-1…7. They do not renumber or replace
> those; E2E-6.1 and E2E-7.2 remain #1416's registered cases.

### E2E-D1: The re-run produces real numbers on each arm

**Given** the #2740 emitter leg landed, a **production** truth judge wired (§7 row 9), and the tier-1 corpus
with sealed golds
**When** the two single-arm invocations of §4 "In scope" item 1 execute (`python -m battery.cli run --tier 1 --arms a4 …`,
then the same for `a0`, each with the probe `--scorer` list) — from the repo root, because there is no
`battery` console script, and one arm per run, because probe aggregation refuses more
(`battery/runner/run.py:619–622`) — and `report` runs **once per attempt dir**, because the two invocations
write two attempt dirs and `report` resolves only the latest (§4 item 1)
**Then** each per-arm profile carries `family_R1`, `family_R2`, `family_R3`, `family_R4` and
`family_R5` — the probe scorer is passed for all five (§4 item 1), so a family can never be ABSENT — and
for each family the payload's own `cells[primary]` is read and recorded, because that is the field that
decides measuredness (`battery/runner/probe_scorer.py:783–786`; the report counts a family measured only
on it, `battery/cli.py:700`). `R1` and `R4` must read `"measured"` with a non-empty `values[primary]`
(`:791–801`). `R2`, `R3` and `R5` must read `"measured"` once the `Given`'s judge is wired; if the wired
judge does not cover them they read `insufficient_n` and the row names the judge that is missing — what
is forbidden is a silent zero or an absent family. `n` is a per-metric MAP, and no `n >= threshold`
readable is claimed here: there is no minimum-n policy in `battery/config/thresholds.yaml`.
`metric_values` is the **per-episode** artifact field (`battery/runner/artifacts.py:86`), not a family
field, so an assertion written against it reds on a healthy run.
**And** `report_status` is **not `complete` on this delta run**, and the value it carries names its cause: the base rule
sets `incomplete_missing_metrics` when any expected family is unmeasured
(`battery/report/assemble.py:113–117`), and `compose_run_status` OVERRIDES it with an `incomplete_*` —
`incomplete_real_partial` whenever any cell is `insufficient_n` OR any episode is excluded (`:218–219`,
applied over the base rule at `:116–117`), which an honest real run legitimately reads (this brief records
the exclusion rate as fixed to 6.4% — §3's blocker table, :131). **`complete` is reachable in general** —
`REPORT_STATUS_OK = "complete"` (`battery/report/assemble.py:49`) is what the base rule returns whenever
no expected family is missing (`:115`), and `compose_run_status` returns `None` in several branches
(`:166–220`), leaving that rule to decide. It is unreachable **for this delta run** for one measured
reason: the expected set is the fixed 14-family `METRIC_FAMILIES` (`battery/cli.py:551`) and this run
measures five of them, so the base rule's missing set is non-empty by construction. A criterion demanding
`complete` on such a run is unsatisfiable, so the only way to satisfy it would be to emit a false status —
the exact failure this case exists to catch. A future full-battery run that genuinely measures all 14
families SHOULD read `complete`, and that is not a defect. What IS required of the status: it is an `incomplete_*` whose cause is
recorded, and whenever it is `incomplete_real_partial` the excluded episodes / `insufficient_n` cells are
itemised by family.
**And** a `complete` claim, an itemised per-family residual presented AS the pass, or a family that is
neither `measured` nor explicitly named in the deferred set (L1–L6, D2–D4) or the judge-gated set (R2/R3/R5
— present as `insufficient_n`, never absent) is a **FAILED** result, and the failure must name the blocking
issue per family — itemisation is the diagnostic, never the pass, because a criterion that accepts it
cannot fail on a run whose families were never measured.

### E2E-D2: Verdict re-file is falsification-accepting and reproducible

**Given** the completed run artifact directories (one per arm)
**When** `python -m battery.cli report` assembles each arm's profile (§4 item 1: one `report` per attempt
dir) and the claim doc is re-filed
**Then** the **re-filed claim doc's** 14-family table reports a status for both arms — `measured` ·
`insufficient_n` · `blocked-by-#issue` · `deferred-by-gate-G-1402-DELTA` — and never blank. (The deferred
status is a status of that TABLE, i.e. a doc surface, not a new `report_status` value: `profile.json` keeps
the harness's own vocabulary, and an honest `incomplete_*` — `incomplete_missing_metrics`, or
`incomplete_real_partial` when cells are `insufficient_n` or episodes were excluded — is what a
correctly-scoped delta run reads —
see E2E-D1.) The deferred status is the one this
delta adds, and it exists for L1–L6 and D2–D4: they are approved `03-scope.md` deliverables that this delta
SEQUENCES out of the first slice, not exclusions, so "not measured yet" must render as a named status rather
than as an absent row or a false `blocked-by`. A family that is neither measured nor explicitly deferred
fails this criterion.

**OVERRIDES:** the omission-oriented eval-harness reporting default (a task the harness did not run is
simply absent from the results file — e.g. `lm-evaluation-harness`'s `results.json`, which documents no
"not run" status vocabulary) — a gap must never read as a finding, so every family this delta does not
measure gets a NAMED row (`deferred-by-gate` / `insufficient_n` / `blocked-by-#issue`), not a blank.
(Default checked 2026-10-25 via `web_search` (rung 2); the literal marker is also recorded on #1402,
where the next lane reads it.)
**And** the outcome is the branch the measured profile supports, and a **no-data profile can never
render as a substantive branch** (the `MECHANISM-NOT-UNIQUE`-as-fallback hazard is closed or explicitly
carried by `report_status`)
**And** the receipt's spend figure is **labelled with its basis** — `provider_reported` when the response
carries `usage.cost`, otherwise `estimated` with the declared price basis named
(`battery/config/prices.py:16–19`, post-#2906). The criterion is the LABEL, not an unconditional
provider-reported figure: a provider that reports no cost legitimately falls back to `estimated`, and
requiring otherwise would fail an honest run.

### E2E-D3: Matched-recall regime is enforced on real comparator arms

**Given** the factual probe subset and the wired pre-pass (`battery/recall/prepass.py`)
**When** the differential pre-pass runs over `{a1,a2,a2b,a3,a4}` (a0 excluded as positive control, #3327)
**Then** the symmetric trigger evaluates and the branch taken (`matched` / `inconclusive` — the only two
values `RecallResult.outcome` admits, `battery/recall/matcher.py:100,103–104`; `profile.json` serializes
that value as-is, `battery/report/assemble.py:352`) is recorded in `profile.json` together with the top-K F1
values per arm
**And** the profile does not silently claim a differential win when the regime was `inconclusive`.

### E2E-D4: Capstone walks the epic's own E2E set

**Given** a completed run + re-filed verdict
**When** the capstone executes as a user would (`python -m battery.cli run` → `python -m battery.cli parity`
→ `python -m battery.cli report`)
**Then** scope E2E-1…7 pass on the real run or are explicitly documented as gated with the gate named
**And** no silent-fallback episodes appear in the artifact set.

---

## 6. Complexity ratings (delta)

| Axis | Rating | Rationale |
|---|---|---|
| Architecture | **standard** | Harness, arms, probes, streams, parity and report all shipped; the delta is two small code units (§7 rows 8–9: lift a single-arm guard, wire an existing injection seam) + execution + a claim-doc re-file |
| Research | **standard** | Verdict interpretation and claim wording against a pre-committed rule; the rubric/threshold work is done |
| Content | low | One claim doc re-file with measured numbers |
| Ontology | low | No schema change |
| UX | low | Internal tooling, JSON + docs surfaces |

**Overall: standard** — down from `complexity:complex` on the epic issue. The complexity was consumed by
the 10 children that shipped; re-stating the epic as *complex* now mis-sizes the remaining work.

---

## 7. MECE remaining-work list — **propose, do not create**

Every unit below already exists as an issue **except** row 6, which is a label change with no issue of
its own, row 7, filed during this brief as issue #9701, and rows 8–9, which are **sub-units of #1416** and
therefore take no issue number of their own. The recommendation is to create **nothing** and instead fix
edges/labels. Listed for the owner gate.

| # | Work unit | O/I/T (one line) | State |
|---|---|---|---|
| 1 | **#1416** battery execution + verdict re-file | O: run tier-1 `a4,a0` on the real lane (one invocation per arm, all five probe scorers — R2 included) and re-file the claim doc / I: `cells[primary] == "measured"` for R1/R4 (R2/R3/R5 present as `insufficient_n` until §7 row 9's judge, never absent) on each arm's own profile, and `report_status` an honest `incomplete_*` naming its cause (E2E-D1 — `complete` is unreachable for THIS delta run, not for the harness, and is not the criterion) / T: E2E-D1, D2 | OPEN — **the delta's critical path**. Its own DoD is the full battery (Tier-1, Tier-2, Tier-3, parity) and is **unchanged** by this brief |
| 2 | **#2525** comparator-arm real-run readiness | O: produce the comparator-arm readiness package — backend state, cost ceiling, determinism and the `ArmUnavailable` path per arm, plus matched-recall pre-pass data (factual top-K F1, K=5) per RUNNABLE arm / I: every comparator arm is runnable-with-evidence or honestly declared unavailable EX ANTE, plus a dated A2b go/no-go; **the sweep itself is #1416's Tier-3 wave, not this row's** / T: E2E-D3 | OPEN (in #2515 umbrella) — **not data-only**: blocked on **#3327** (four open decision points; its title says it blocks #2525 indicator 2) and **#3437** (`a1`'s pre-pass factual recall is 0.0 by construction, so the matched control may be unreachable for `a1` — §4 item 3) |
| 3 | **#1419** capstone clickthrough | O: walk scope E2E-1…7 on the real run / I: all pass or gated-with-name, artifacts reproducible / T: E2E-D4 | OPEN |
| 4 | **#1404** test-design integration-surface map | O: close the S1–S8 verification map the children cite / I: every surface has an assigned layer / T: cited by #1416/#1419 checklists | OPEN |
| 5 | **#2702** `cal`/`bct` envelope contract | O: fix the empty-position envelope class / I: long-prompt deadline class no longer aborts calibration / T: calibration leg clean | CLOSED 2026-09-12 — no action |
| 6 | *(new, optional)* re-label #1416 not-launch-relevant → launch-relevant | O: reflect the reclassification trigger in labels / I: one label change with the 01-align trigger quoted / T: n/a | — |
| 7 | *(new, mandated)* fleet audit instrument: sub-issue relation | O: make epic→child linkage read reliably by audits / I: an audit of epics reports true child counts / T: §2 evidence | **#9701** (filed 2026-10-22) |
| 8 | *(sub-unit of #1416 — no new issue)* lift the single-arm probe guard | O: let one probe-scored run aggregate across arms / I: `--arms a4,a0` with probe scorers yields per-arm families instead of `ConfigError` before the attempt dir (`battery/runner/run.py:619–622`, `battery/runner/probe_scorer.py:754–758`) / T: the ONE-run, two-arm `profile.json` that §4 item 1 says two invocations cannot produce | OPEN — **code**; until it lands, "both arms in one run" is not a command that exists |
| 9 | *(sub-unit of #1416 — no new issue)* wire a production truth judge | O: set `RunConfig.truth_judge` from something other than a test / I: a CLI run emits R3 `outcomes`, R5 `update_correct_direction` **and R2 `coverage_subscore`** — the third judge-gated family this row unlocks, which §4 item 1 and E2E-D1 both state — with evidence, or refuses naming the missing judge (`battery/runner/run.py:1158`; `battery/runner/probe_scorer.py:418`, `:494–513`) / T: E2E-D1's R3/R5/R2 `cells[primary] == "measured"` / `values[primary]`, with the per-family payloads they unlock | OPEN — **code**; this is why "one re-run" is not yet enough |

**MECE check:** (1) is execution + claim re-file, (2) is comparator data, (3) is verification, (4) is the
verification basis, (5) is closed history — no two own the same artifact. (1) and (2) touch the same
battery but own different artifacts (the tier-1 verdict vs the 6-arm profile), and (2) is a dependency
of the *sweep*, not of the tier-1 re-run: the tier-1 delta can land without it. **Rows 8 and 9 are not
peers of (1) — they are its sub-units**: they own no artifact (1) does not own, they are the two code
deltas that make (1)'s indicator REACHABLE, which is why they take no issue number and why (1) cannot be
called "one re-run away" without them.

**Explicit non-duplicate:** #2515 / #2522 are a *consumer* umbrella (publish rows); they consume that
epic's verdict output and do not re-own the battery. **#2524 is NOT a consumer — it PRECEDES row 1.**
Its own indicator (3) files a sequencing recommendation on #1416 *before* its Tier-2 execution wave, and
row 1's DoD names the full battery, so #2524's runnable/blocked split gates row 1's Tier-2 half. It owns
the split and the sealed fixtures, never the run — which is why it is not a tenth work unit here.

---

## 8. Gate `G-1402-DELTA` — owner decision required

| Option | What happens | Cost |
|---|---|---|
| **A (recommended)** | Execute the delta as a SEQUENCE, not as a closure: #1416 re-run (one invocation per arm) → re-file verdict → #2525 → #1419. **#1402 does NOT close here** — it stays OPEN until `03-scope.md` deliverables 4/5/6 (Tier-2, the Tier-3 six-arm sweep, parity) are measured, per the 2026-08-14 owner override (all probes × all arms, no exclusions). The delta is how that ruling gets started, not a substitute for it. | **≈$0.34–0.58 is a FLOOR** — 2 × ≈$0.17–0.29, where the per-arm figure is `a0`-derived (the `a4` leg was deliberately not run — `docs/claims/agent-reasoning-verdict.md:93`) and `a4` drives the extraction path per episode, so its token count is unmeasured and strictly larger. Basis is the corrected **declared/estimated** price (`battery/config/prices.py`, 0.084/0.168), NOT `provider_reported`: the receipt carries no `usage.cost`, which is the gap #2906 closes (§1, adversarial test item 4; verdict doc `:59–79`). Plus §7 rows 8–9 (code, not spend) + a few sessions |
| **B** | Declare the reclassification trigger fired (positioning leads with reasoning-uniqueness) and promote #1416 to launch-relevant, which by 01-align's own wording moves the epic Do-now. | Same work, different priority/labelling |
| **C** | Defer again: leave the verdict as the stale no-data doc. | Cheapest now; the claim stays unsaid while the doc reads as a negative finding |

**Recommendation: A.** The profile that gates the product's core differentiator is **two named code deltas
(§7 rows 8–9) and a re-run** away, on a spend basis three orders of magnitude below "thousands" — and
leaving `docs/claims/agent-reasoning-verdict.md` in its current state actively misleads: it says
`MECHANISM-NOT-UNIQUE` when the truth is "no data". What A does NOT do is close #1402: an epic-wide
differentiation profile is all-metrics × all-arms by owner decision, and this delta measures one tier of it.

**What remains blocked until the owner answers:** any child issue creation, any implementation, any
re-labelling of #1416, and this document advancing past `doc_status: draft`.

---

## 9. What this run did NOT do

No child issues created · no implementation code · no new battery run · no changes to
`docs/epics/1402-eval-battery/*` · no re-labeling · no reversal of `01-align.md`'s recorded decision ·
no `docs/claims/` edit.
