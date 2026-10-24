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
extends: docs/epics/1402-eval-battery/03-scope.md (the APPROVED scope), 01-align.md (decision), 06-verify.md (pipeline result), docs/claims/agent-reasoning-verdict.md (the filed verdict)
---

# Scope Reconciliation — Epic #1402 (delta scope)

> **This document does not re-scope epic #1402.** The epic's scope was written, human-approved, and
> 10 of its 12 children have closed (9 of them carrying the `implemented` label). This brief exists because an audit reported #1402 as having
> "zero references from any issue or PR" and therefore no scope and no children — **that premise is
> false**, and acting on it would have produced a second, competing scope document against an
> approved one. What follows is (a) the evidence that falsifies the premise, (b) the root cause of
> the false audit, and (c) the genuinely remaining *delta* scope, now much smaller than "an epic".

**Status:** awaiting owner gate (`G-1402-DELTA`). Nothing here is dispatched, created, or implemented.

---

## 1. Align decision

**Decision: REDIRECT — do not re-run the planning pipeline for #1402; execute the delta.**

The epic-workflow was already run to completion for this epic in August 2026. All six stage
artifacts are committed, the scope and plan human gates were passed, decomposition was
MECE-verified, and 10 of 12 children closed on 2026-08-18 (9 of them labelled `implemented`;
#1407 closed with `scoped`/`planned`). Re-running Align →
Research → Scope would regenerate docs that already exist and contradict an owner-approved basis.

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
4. **Cost.** The one real run cost **$1.07** at `deepseek-v4-flash`. Order-of-magnitude: dollars, not thousands.

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
| "zero references from any issue or PR" | **20 tracked files** under `docs/epics/1402-eval-battery/`; **24 commits** on `origin/main` match `1402` (29 across all refs); epic body carries a 12-row wiring table |
| "no scope" | `03-scope.md` — 9 in-scope deliverables, 4 out, 7 high-level E2E, integration-surface map S1–S8 |
| "no plan" | `04-plan.md` — 25 detailed E2Es; **Human gate #2 recorded: approved by owner 2026-08-14 ("go")** |
| "no children" | `05-decompose.md` — MECE-verified; **11 child issues #1406–#1416 + capstone #1419** (12 wiring rows) |
| pipeline never ran | `06-verify.md` — `Status: READY`, `Review Gates: 12/12 passed`, `Coherence: 0 issues`, `Artifacts: 5/5` |

**Child state (verified via `gh issue view`):** #1406, #1407, #1408, #1409, #1410, #1411, #1412, #1413,
#1414, #1415 — **CLOSED** (2026-08-18, labels `implemented`/`planned`). #1416 (battery execution +
verdict) and #1419 (capstone) — **OPEN**, both re-labelled `not-launch-relevant` on 2026-10-10.

**Code that exists:** `battery/` — 95 tracked files (runner, 6 arm adapters, 5 probe modules, 6 streams,
4 differential legs, parity leg, report/verdict assembler, exposure + liveness, golds), **57 battery test
modules**. The harness is not a plan; it ships.

### Root cause of the false "zero children" audit

`gh issue view 1402 --json subIssues,parent` →

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
  then **#2702** (envelope contract — since CLOSED) and **#2525** (comparator-arm data).

**The state has changed since that filing — this is the whole reason a delta scope is needed now:**

| Blocker named by the verdict | State today |
|---|---|
| #2740 derive/gold truth-emission leg (blocks R1/R3/R5) | **CLOSED** |
| #2874 / #2906 spend-meter price basis (receipt integrity) | **CLOSED** |
| exclusion-rate fix lineage #2717/#2721/#2697 (43.6% → 6.4%) | landed |
| #2525 comparator-arm real-run readiness | **still OPEN** |
| #2702 `cal`/`bct` empty-position envelope contract | **CLOSED** 2026-09-12 |

⇒ **The epic's entire remaining critical path is one re-run that the filed verdict already specifies,
now unblocked.** The verdict doc is stale in a way that reads against the product claim.

---

## 4. Delta scope (the only thing in scope for this brief)

### In scope

1. **Re-run tier-1 on both arms** — `--arms a4,a0 --executor real --seed 7` at current `main`, with the
   #2740 emitter leg live, producing real `metric_values` for R1/R3/R5/R4 on the product arm.
2. **Re-file the verdict** — `battery report` → re-write `docs/claims/agent-reasoning-verdict.md` per the
   branch the measured profile actually supports; keep the falsification-accepting structure (§1–§7)
   and the `report_status` honesty carrier.
3. **Matched-recall is wired — the comparator DATA is what is missing.** `match_recall` has a production
   call site (`battery/recall/prepass.py:231`, invoked from `battery/runner/run.py:1080`) and the
   INCONCLUSIVE outcome is a real exit code (`battery/enums.py:37`), so the control variable *can* be
   enforced. (The 2026-09-10 verdict doc's "no production call site" line was superseded by #3327 /
   PR #3439 on 2026-09-13; #1413 is CLOSED/implemented.) What remains is **real comparator-arm data** for
   the A1/A2/A2b/A3 arms, which is #2525's scope in the #2515 umbrella. This corrects a claim in the
   verdict doc — the delta is to fix that claim, not to build a missing producer.
4. **Capstone** — #1419 walks scope E2E-1…7 on the real run and gates epic completion.
5. **Test-design map closure** — #1404 (open) is cited as the verification basis by #1416/#1419.

### Out of scope (unchanged from the approved `03-scope.md` §Out of Scope)

Adaptive/evolving test generator (SEAL-style); public/commercial benchmark packaging; new extraction
work; product UI; marketing/positioning changes before the verdict. **Also out of scope for this brief:
any new child issue creation, any implementation, any second scope document.**

### Non-goal correction

A full Tier-2 + Tier-3 six-arm differential sweep (`E2E-3`) is **not** part of the near-term delta. It
remains the epic's target end-state, but the honest sequencing is: tier-1 with real numbers on both
arms first (cheap, unblocked) → real comparator-arm data (#2525) → then the 6-arm sweep. The 1/14-family
profile in the filed verdict was **not** caused by the sweep — no sweep was attempted (that run was
tier-1, `--arms a0` only); it was caused by the missing Task-9 derive/gold leg, now closed as #2740.

---

## 5. High-level E2E test cases (delta only)

> Registered as **additions** to the approved `03-scope.md` E2E-1…7. They do not renumber or replace
> those; E2E-6.1 and E2E-7.2 remain #1416's registered cases.

### E2E-D1: Re-run produces real numbers on both arms

**Given** the #2740 emitter leg landed and the tier-1 corpus with sealed golds
**When** `battery run --tier 1 --arms a4,a0 --executor real --seed 7` executes
**Then** `family_R1/R3/R5` carry non-empty `metric_values` with `n ≥ threshold` for both arms
**And** `report_status` is `complete` (not `incomplete_emitter_gap`), or the residual gap is itemised
per family with the emitting field named.

### E2E-D2: Verdict re-file is falsification-accepting and reproducible

**Given** the completed run artifact directory
**When** `battery report` assembles the profile and the claim doc is re-filed
**Then** all 14 families report a status for both arms (measured · `insufficient_n` · blocked-by-#issue —
never blank)
**And** the outcome is the branch the measured profile supports, and a **no-data profile can never
render as a substantive branch** (the `MECHANISM-NOT-UNIQUE`-as-fallback hazard is closed or explicitly
carried by `report_status`)
**And** the receipt's spend figure is provider-reported (post-#2906 basis), not a price constant.

### E2E-D3: Matched-recall regime is enforced on real comparator arms

**Given** the factual probe subset and the wired pre-pass (`battery/recall/prepass.py`)
**When** the differential pre-pass runs over `{a1,a2,a2b,a3,a4}` (a0 excluded as positive control, #3327)
**Then** the symmetric trigger evaluates and the branch taken (enforced / `inconclusive`) is recorded in
`profile.json` together with the top-K F1 values per arm
**And** the profile does not silently claim a differential win when the regime was `inconclusive`.

### E2E-D4: Capstone walks the epic's own E2E set

**Given** a completed run + re-filed verdict
**When** the capstone executes as a user would (`battery run` → `battery parity` → `battery report`)
**Then** scope E2E-1…7 pass on the real run or are explicitly documented as gated with the gate named
**And** no silent-fallback episodes appear in the artifact set.

---

## 6. Complexity ratings (delta)

| Axis | Rating | Rationale |
|---|---|---|
| Architecture | **standard** | Harness, arms, probes, streams, parity and report all shipped; the delta is execution + a claim-doc re-file |
| Research | **standard** | Verdict interpretation and claim wording against a pre-committed rule; the rubric/threshold work is done |
| Content | low | One claim doc re-file with measured numbers |
| Ontology | low | No schema change |
| UX | low | Internal tooling, JSON + docs surfaces |

**Overall: standard** — down from `complexity:complex` on the epic issue. The complexity was consumed by
the 10 children that shipped; re-stating the epic as *complex* now mis-sizes the remaining work.

---

## 7. MECE remaining-work list — **propose, do not create**

Every unit below already exists as an issue **except** the last two rows. The recommendation is to
create **nothing** and instead fix edges/labels. Listed for the owner gate.

| # | Work unit | O/I/T (one line) | State |
|---|---|---|---|
| 1 | **#1416** battery execution + verdict re-file | O: run tier-1 `a4,a0` on the real lane and re-file the claim doc / I: real numbers for R1–R5 both arms, `report_status` complete-or-itemised / T: E2E-D1, D2 | OPEN — **the delta's critical path** |
| 2 | **#2525** comparator-arm real-run readiness | O: run A1/A2/A2b/A3 on the real lane with matched-recall data / I: every comparator arm has real numbers + A2b go/no-go / T: E2E-D3 | OPEN (in #2515 umbrella) |
| 3 | **#1419** capstone clickthrough | O: walk scope E2E-1…7 on the real run / I: all pass or gated-with-name, artifacts reproducible / T: E2E-D4 | OPEN |
| 4 | **#1404** test-design integration-surface map | O: close the S1–S8 verification map the children cite / I: every surface has an assigned layer / T: cited by #1416/#1419 checklists | OPEN |
| 5 | **#2702** `cal`/`bct` envelope contract | O: fix the empty-position envelope class / I: long-prompt deadline class no longer aborts calibration / T: calibration leg clean | CLOSED 2026-09-12 — no action |
| 6 | *(new, optional)* re-label #1416 not-launch-relevant → launch-relevant | O: reflect the reclassification trigger in labels / I: one label change with the 01-align trigger quoted / T: n/a | — |
| 7 | *(new, mandated)* fleet audit instrument: sub-issue relation | O: make epic→child linkage read reliably by audits / I: an audit of epics reports true child counts / T: §2 evidence | **#9701** (filed 2026-10-22) |

**MECE check:** (1) is execution + claim re-file, (2) is comparator data, (3) is verification, (4) is the
verification basis, (5) is closed history — no two own the same artifact. (1) and (2) touch the same
battery but own different artifacts (the tier-1 verdict vs the 6-arm profile), and (2) is a dependency
of the *sweep*, not of the tier-1 re-run: the tier-1 delta can land without it.

**Explicit non-duplicate:** #2515 / #2522 / #2524 are a *consumer* umbrella (publish rows, sequence
temporal legs); they consume #1416's verdict output and do not re-own the battery.

---

## 8. Gate `G-1402-DELTA` — owner decision required

| Option | What happens | Cost |
|---|---|---|
| **A (recommended)** | Execute the delta: #1416 re-run → re-file verdict → #2525 → #1419. Epic closes with a measured profile. | ~$1–2 per run + a few sessions |
| **B** | Declare the reclassification trigger fired (positioning leads with reasoning-uniqueness) and promote #1416 to launch-relevant, which by 01-align's own wording moves the epic Do-now. | Same work, different priority/labelling |
| **C** | Defer again: leave the verdict as the stale no-data doc. | Cheapest now; the claim stays unsaid while the doc reads as a negative finding |

**Recommendation: A.** The single measurement that gates the product's core differentiator is one
unblocked re-run away; leaving `docs/claims/agent-reasoning-verdict.md` in its current state actively
misleads — it says `MECHANISM-NOT-UNIQUE` when the truth is "no data".

**What remains blocked until the owner answers:** any child issue creation, any implementation, any
re-labelling of #1416, and this document advancing past `doc_status: draft`.

---

## 9. What this run did NOT do

No child issues created · no implementation code · no new battery run · no changes to
`docs/epics/1402-eval-battery/*` · no re-labeling · no reversal of `01-align.md`'s recorded decision ·
no `docs/claims/` edit.
