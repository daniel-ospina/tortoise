---
title: "One authoritative per-commit CI verdict — Implementation Plan"
type: engineering
domain: platform
doc_status: live
created: 2026-09-26
subjects.team: organisation-design-team
ownedBy: organisation-design-team
aboutSubjects: organisation-design-team
---

<!-- research-path: none (no research brief — the finding is entirely in-repo: #4877's measured payload) -->

# One authoritative per-commit CI verdict — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Replace the fleet's many per-consumer re-derivations of "is this commit green?" with one
per-commit verdict object whose states are `green` / `red` / `in-flight` / `no-verdict`, computed from
the latest attempt per `(app.slug, workflow identity, job name)` group on the exact sha, so that
absence is never coercible into a red and a re-run never leaves a red behind.

**Team:** organisation-design-team (issue `**Team:**` = unknown → fallback; no team SOR)
**Role:** implementation

**Architecture:** A single Python module (`tools/ci_verdict.py`) owns the reading. All grouping and
aggregation is a pure function over already-fetched GitHub payloads; a thin `gh`-fetch layer feeds it;
a CLI exposes the verdict to shell consumers. Consumers (`scripts/admin-merge.sh`, the exemption
path, the required review check) migrate to reading the verdict in dependency order, and the
mechanisms the verdict replaces (the per-name rollup, the rate/envelope exemption) are **deleted**,
not guarded. Increment 1 (this PR) lands the object + its tests + this plan; the consumer migration is
Tasks 2–9.

### Decisions recorded during increment 1

The issue leaves five design decisions open. Recorded here so no consumer migrates against an
unstated interface. Each carries the alternative it declined.

| # | Decision | Chosen | Declined / note |
|---|----------|--------|-----------------|
| 1 | Where the verdict object lives | **Derived on read** from GitHub, bound to the exact sha; the module is the algorithm, there is no stored artifact | A stored JSON artifact and a verdict check-run of its own were declined: a stored artifact needs an invalidation story for a re-run, and a check-run of its own adds a surface to the very surface it reads. Consequence consumers must accept: **two reads of the same sha can disagree if an attempt lands between them.** |
| 2 | Coverage authority | Deferred to Task 2 (a decision task now precedes it) | `config/ci-surfaces.yml` describes selection, not coverage. |
| 3 | Ordering of the exemption replacement | Deferred to Task 7, gated on Task 6's attribution substrate | The rate/envelope logic is deleted only once the verdict can carry a measured baseline. |
| 4 | Review check source | Deferred to Task 8 | The head-bound review record (agent-infra #1224) vs the CI verdict are **different objects** — a review verdict is not a CI verdict. |
| 5 | Legacy `/commits/<sha>/status` | **A consumer refusal, not an assertion of emptiness**, recorded in Task 9: every production reader must either not read it or treat it as a read failure. The rail's statuses half is explicitly **retained until Task 5 migrates it** (see Task 5), never silently dropped. | The legacy endpoint returns `pending` with zero statuses here, which is a false-red source (#4877). |

Polarity decisions the increment's own code makes (all pinned by a named test):

| Decision | Rule | Test |
|---|---|---|
| A **named** non-completed status (`queued`/`in_progress`/`waiting`/`requested`/`pending`) | `in-flight`, never red | `test_not_completed_named_in_flight_status_is_in_flight_not_red` |
| An **unrecognised** status spelling | classify by the conclusion — a present negative conclusion is RED; no conclusion is `in-flight` | `test_unrecognised_status_with_a_failure_conclusion_is_red` |
| An unrecognised / null conclusion on a **completed** check | red (fail-closed) | `test_completed_with_an_undocumented_conclusion_is_red` |
| `skipped` / `neutral` | green (AGENTS.md polarity); an all-skipped surface reads green, not `no-verdict` | `test_skipped_and_neutral_are_green_and_all_skipped_reads_green` |
| An absent group with **no earlier red**, alongside a green group | green; cancelled-ONLY reads `no-verdict` | `test_absent_group_plus_green_reads_green_recorded_policy`, `test_cancelled_only_reads_no_verdict` |
| A red followed by a **cancelled** attempt in the same group | **RED** — the group carries a *voided red*; a measured failure is only cleared by a later completed green attempt | `test_red_then_cancelled_in_the_same_group_reads_red`, `test_voided_red_with_another_green_group_is_not_green`, `test_red_then_cancelled_then_green_reads_green` |
| Workflow identity | the run's `workflow_id` when present; the **run id** otherwise (never the display name); empty for non-Actions checks | `test_same_named_workflows_stay_separate_by_workflow_id`, `test_run_without_workflow_id_does_not_collapse_by_name`, `test_unresolved_workflow_does_not_collapse_two_runs` |
| A short (<40-hex) sha | read failure (exit 2) — the `head_sha` listing matches only the full sha | `test_short_sha_is_a_read_failure` |
| CLI exit status | `0` green, `1` red, `2` unreadable, `3` in-flight, `4` no-verdict — a shell consumer cannot merge on exit 0 alone | `test_cli_red_exits_nonzero`, `test_cli_in_flight_exits_nonzero`, `test_cli_json_is_the_full_object_and_no_verdict_exits_nonzero` |

**⛔ OVERRIDES (to be posted on issue #5042 by Task 2; not yet posted):** `scripts/admin-merge.sh`'s
#1353 rule fails an unrecognised non-completed status closed to RED, and unions
`cancelled`/`stale` into its non-red set (so a cancelled-only surface reads GREEN). This verdict (a)
reads `cancelled`/`stale` as ABSENT (`no-verdict`), and (b) classifies an unrecognised status by its
conclusion rather than assuming absence. Both departures are deliberate: absence must never be
coercible into failure, and a measured red must not be cleared by a cancellation. Task 5 must
reconcile the rail to **both** axes — the second (cancelled-only rail-green vs verdict `no-verdict`)
is currently a silent divergence with no signal.

### Pattern Research

> **Findings date:** 2026-09-26

**Library docs (preflight)** — no third-party deps in plan — skipped. Stdlib only (`argparse`,
`json`, `re`, `subprocess`, `dataclasses`), matching `tools/ci_timing.py`.

**Library version & API surface** — skipped: no library surface; the only external contract is the
GitHub REST API, verified live 2026-09-26 (see `### Integration Surface Map`).

**Idiomatic usage patterns** — skipped: the pattern is already proven twice in-repo —
`tools/ci_timing.py` (fetch via `gh api`, parse, deterministic render) and `scripts/admin-merge.sh`'s
inline parser (concatenated `--paginate` JSON decoding, latest-attempt selection).

**Library/framework pitfalls** — skipped per the pitfall-bucket skip rule: the one real pitfall —
`gh api --paginate` emits **one JSON object per page**, not an array — is handled by the shared
`_decode_pages` decoder.

> Gate skipped: no third-party deps in plan — stdlib only.

### Integration Surface Map

| Surface | Kind | Contract / failure modes | Test layer |
|---------|------|--------------------------|------------|
| `GET /repos/{o}/{r}/commits/{sha}/check-runs?filter=all&per_page=100` | External API (read) | Concatenated pages; every attempt; `name` is the JOB name; `details_url` → `…/actions/runs/<id>/job/<id>`. Failures: network/auth error (loud, exit 2); **pagination shortfall vs `total_count`** (read failure); empty body (read failure); non-object page (read failure) | Unit + CLI via `collect_items`; `_gh_api` seams monkeypatched; live smoke |
| `GET /repos/{o}/{r}/actions/runs?head_sha={sha}&per_page=100` | External API (read) | `id → (workflow_id, name)`. Failures: run absent / listing empty (fall back to `run:<id>` identity — closed by `test_unresolved_workflow_does_not_collapse_two_runs`); partial read (the second read failing is a loud exit 2) | Unit + CLI; live smoke |
| `GET /repos/{o}/{r}/commits/{sha}/status` (legacy) | External API (read) | **Not read by the verdict.** The rail reads it today with `NON_RED_STATE={success}`. Task 5 decides: ingest into the verdict or record a consumer refusal — never a silent drop | Rail harness (Task 5) |
| `compute_verdict(sha, check_runs, runs, repo)` | Pure logic | Grouping key `(app.slug, workflow identity, job)`; workflow identity is `workflow_id`, else the run id (never the display name); latest by check-run `id`; per-entry (unique per occurrence) for unnamed/placeholder/id-less/duplicate-id and for an Actions check whose run id did not resolve; voided-red for a red superseded by absence. Failures: non-Mapping payload, null id, duplicate names, empty surface, short sha (enforced at the compute entry point, so the offline seam cannot bypass it) | Unit — 58 named cases |
| Verdict CLI (`gh` fetch + render) | Process boundary | Exit `0` green, `1` red, `2` unreadable, `3` in-flight, `4` no-verdict (loud, no verdict on 2). Offline mode warns when Actions check-runs are present but no runs listing was supplied | CLI tests via `--check-runs-json`/`--runs-json` + monkeypatched seams |
| `scripts/admin-merge.sh` `check_surface_probe` | Consumer | **No harness exists today** (`scripts/admin-merge.test.sh` is absent; nothing in `tests/` invokes the rail). Task 5 must create or name one before its acceptance can pass. Failure modes: partial read → PARTIAL refusal, both endpoints unreadable, unresolved event → blocks, empty surface = unmeasured ≠ green, per-entry identity for unnamed checks | Rail harness (created in Task 5) |
| `scripts/admin-merge.sh` lane-tested precondition (`LANE_RUN_JQ`, "EXECUTED = success/failure/timed_out") | Consumer | A coverage-parity question ("did this head's lane execute the shard main executes?") the four-state verdict does not answer. Disposition: `keep separate` with that reason, and Task 9 enumerates it | Rail harness (Task 5) |
| `scripts/ci-failure-set.sh` (`--pr`, `--commit`, `--main-union[-rates|-signatures]`, `--exclude`) | Producer | Multi-run/union/rate/signature baselines across shas; supersede key `(sha, workflowDatabaseId, workflowName, event)`; emits nothing when no run exercised the suite (fail-closed). Must compose from per-sha verdicts. **No harness exists today** | New harness (Task 6) |
| `scripts/check-lane-tested.sh` + the post-merge detector | Consumer | `ci-failure-set.sh`'s second consumer (its own header: "ONE PARSER, TWO CONSUMERS"); re-derives "did the lane TEST this commit" from the run-report counters. Task 6 must not change the shared run-listing half without enumerating it | Task 6/9 |
| `scripts/atomic-land.sh` (`commits/<sha>/check-runs` terminality wait) | Consumer | A terminality wait, not a verdict reading (`keep separate`). **Pre-existing fail-open worth recording:** it calls `check-runs` with no `per_page`/`--paginate`, so it sees at most GitHub's default 30 — a commit with more can be reported terminal while later pages are pending. Task 9's inventory line must require the module's `collect_items` truncation contract (or file it) | Task 9 inventory |
| `tools/ci_timing.py` | Consumer | `keep separate`: it samples one `event=push&branch=main` run for duration statistics and is explicitly never a gate (`ci_timing.py`: "Measurement only — never gates CI"). It answers "how long does a lane take", not "is this commit green" | Task 9 inventory |
| `scripts/ci_exemption.py` `decide()` | Consumer | Consumes `--pr-failures` (`<nodeid>\t<failures>\t<runs>\t<signature>`), `--main-rates`, `--main-signatures`. **No tests exist today** | New harness (Task 7) |
| `.github/workflows/ai-review-gate.yml` (marker regex at `sha_ok_re`/`marker_re`, `clean(-micro|-low)?`) | Consumer (required check) | Reads an HMAC-signed **review** vocabulary, not a CI verdict. Task 8 corrects this conflation | `.github/scripts/ai-review-gate.test.sh` |
| `.github/workflows/*` required checks (producers) | Producer | Task 2 modifies `python-ci.yml` to emit a coverage declaration | Workflow-parsing test |
| `config/ci-surfaces.yml` | Config authority | Selection, not coverage. Task 3 decides the coverage authority | Config test (Task 3) |

**Bug Pattern Flags:** external-API error coercion (a read failure treated as "nothing failed");
absence coercion (cancelled/never-scheduled → green); attempt-order loss (a superseded red masking);
group collapse (two workflows or two runs sharing a job name); truncation (a dropped page hiding a
red).

**Checklist Notes:** no DB/RLS/pgTAP surface; no UX surface.

### Verification Plan

| Layer | Applies | Depth |
|-------|---------|-------|
| Unit (pytest) | Yes | Full — checklist + polarity + group separation + read failure + truncation |
| Integration (recorded payloads) | Yes | Fetch/CLI boundary via `collect_items` and the CLI offline path |
| E2E | No | No user journey; the consumer is a merge rail |
| UX | No | No user-facing surface |
| pgTAP / DB | No | No DB surface |
| Consumer integration (rail/exemption/review gate) | Deferred | Tasks 5, 6, 7 — each must create its harness (`admin-merge`, the failure-set producer, and `ci_exemption` have none today; `ai-review-gate.test.sh` exists) |

### Journey Test Map

Skip — no user-facing journeys.

**Tech Stack:** Python 3.12 stdlib; `gh` CLI (authed on runners); GitHub REST.

---

## Task Dependencies

| Task | Depends on | Parallel with |
|------|-----------|---------------|
| 1 (verdict + tests + plan) | — | — |
| 2 (decision: coverage authority + review-check source, decisions 2 & 4) | — | 1 |
| 3 (verdict gains coverage) | 2 | — |
| 4 (verdict gains rail fields: event, anchor) | 1 | 5 (different file) |
| 5 (rail reads the verdict) | 3, 4 | — |
| 6 (failure-set producer composes from verdicts) | 3, 4 | — |
| 7 (exemption replaced) | 3, 5, 6 | 8 |
| 8 (required review check) | 2 (decision 4) | 7 |
| 9 (consumer inventory) | 5, 6, 7, 8 | — |
| 10 (checklist traceability matrix) | 1–9 | — |

Tasks 3 and 4 both touch `tools/ci_verdict.py` and `tests/test_ci_verdict.py`, so they are **serialized**
(3 before 4), not parallel — a dispatcher reading only the table would otherwise dispatch them
concurrently and collide. Tasks 5 and 6 share no file and may run in parallel once 3 and 4 land.

## Member Traceability

| Member | Owned by | Note |
|--------|----------|------|
| #4877 false red / attempt order | Task 1 | Implemented + tested |
| #4757 no verdict (cancelled/pending) | Tasks 1, 5 | `no-verdict`/`in-flight` states |
| #4831 cancelled shard reddens main | Tasks 1, 5 | absent state |
| #4723 non-terminating run unattributable | Task 5 (lane-tested precondition) | |
| #4448 no lane scheduled reads as pending | Task 5 | `no-verdict` surfaced as an infrastructure verdict |
| #4950 / #4993 unexemptable guard-step | Task 7 | measured-baseline substrate |
| #4755 review-gate marker regex | Task 8 | corrected: review vocabulary ≠ CI verdict |
| #4745 two engines, two verdicts | Task 3 | coverage declaration (engine scope) |
| #3582 / #4482 shared red cross-attributed | Task 9 | inventory |
| #4961 residual (tmpdir signature) | Task 6 | signature normalizer |
| #4279 lanes stop running | Task 5 | lane-tested precondition |
| #4339 no CI run for a head | Task 5 | same |
| #4819 watchdog kill reported as test failure | **not consolidated** | The verdict cannot distinguish a watchdog kill from a failure: a killed leg concludes `failure` with 0 junit failures. Distinguishing them needs a junit/log signal the verdict does not read; kept separate (so as not to pin `timed_out`/`failure` as if it were handled). |
| **Explicitly NOT consolidated** (#4547, #4995, #4942, #4844, #4876, #5035) | — | distinct causes, per the issue body |
| #4153/#4159/#4166 (ruff I001), #4301/#4484/#4485 (deploy probes), #4449 (injection), #4604 (paths filter), #4463 (frozen-set e2e) | — | distinct causes, **not consolidated** (extends the issue body's list) |
| #5250/#5152/#5257 (PR rate measured from n=1), #2133 (record-review stale sha), #4829 (timeout/pin assertion), #3135 (ruff RUF100), #3395 (watchdog cap) | — | carried by the package-disposition comment / folded members; **not consolidated** here unless a task adopts them. Task 10's matrix reconciles the full member set against the issue's comments. |

---

## Task 1: The verdict object, its tests, and this plan (INCREMENT 1 — this PR)

**Intent:** Establish the single authoritative reading as a pure, testable object so no later consumer
re-derives one.
**Acceptance:** `tools/ci_verdict.py` returns one of `green`/`red`/`in-flight`/`no-verdict`;
`tests/test_ci_verdict.py` covers the checklist, the polarity decisions, group separation, read
failure and truncation; the CLI exits 2 (never a verdict) when the surface cannot be read.
**Files:**
- Create: `tools/ci_verdict.py`
- Create: `tests/test_ci_verdict.py`
- Create: `docs/plans/2026-09-26-5042-per-commit-ci-verdict.md` (this file)

**Implemented invariants (all named tests, 58 cases):** red-then-green → green; cancelled-only →
no-verdict; stale-only → no-verdict; in-flight → in-flight; red outranks in-flight; empty surface →
no-verdict; unknown completed conclusion → red; unknown status with a negative conclusion → red;
named in-flight status → in-flight; all-skipped → green; **an absent group with no earlier red
alongside a green reads green** (recorded policy); **red-then-cancelled (or -stale) in the same group
→ RED (voided red)**; **a voided red is not cleared by an unrelated green group**;
red→cancelled→green → green; two workflows sharing a job name stay separate (workflow id); a run
without a workflow id does not collapse by name (run id); an unresolved workflow keeps runs separate;
two apps sharing a job name stay separate (app axis); non-Actions checks separate by job name; an
Actions check whose run id cannot be resolved is per-entry (an unresolved URL never joins the
`(app, None, job)` fallback group, where two workflows could collapse); a run id followed by
`?`/`#`/end still resolves; latest attempt by id, not `started_at`; unnamed and placeholder-named
checks per-entry; id-less checks per-entry; duplicate-id checks per-entry; sha+repo binding; short
sha → read failure **at the compute entry point as well as the fetch layer** (so the offline CLI
seam is covered); pagination shortfall → read failure; missing `total_count` → read failure; empty
body → read failure; partial read → exit 2; CLI exit codes 0/1/2/3/4.

**Step 4 smoke (recorded 2026-09-26; sha corrected 2026-09-26 review):**
`python3 tools/ci_verdict.py --repo daniel-ospina/tortoise <full-40-hex-sha>` —
`main@99a98ddc5a37304b80232ba61d1a0a70f4fcf026` → `red` (24 groups, 2 red — a real pre-existing
`Post-merge validation / lint` base red); PR 5406 head → `red` (48 groups). A **short** sha
(e.g. `99a98ddc5`) is refused with exit 2 (`_require_full_sha`) — the earlier record quoted the
abbreviated sha, which the tool had not yet begun refusing.

---

## Task 2: Decision — the coverage authority (decisions 3 and 4)

**Intent:** `config/ci-surfaces.yml` describes *selection*, not *coverage*; the review check's source
is open.
**Acceptance:** A recorded decision names where the coverage list (surfaces/engines) lives, who owns
it, and whether the required review check reads the CI verdict or the head-bound review record
(agent-infra #1224); each with an `OVERRIDES:` line if it departs from a default.
**Files:** Modify: this plan (decisions table) + an issue comment.

---

## Task 3: Declare coverage in the verdict (surfaces + engines)

**Intent:** A verdict must say what it covers, so a lane-scoped green cannot masquerade as a commit
verdict (O/I/T indicator 3; #4745).
**Acceptance:** `Verdict` carries a coverage declaration naming each surface/engine exercised,
populated from the Task 2 authority; `Verdict.coverage`'s "non-absent ≠ measured" distinction is
resolved into a real measurement predicate shared with the rail's `MEASURING_CONC`; a commit whose
only green is the docker lane does not read as a bare `green` (a named test).
**Files:** Modify: `tools/ci_verdict.py`, `tests/test_ci_verdict.py`, `.github/workflows/python-ci.yml`.

---

## Task 4: The verdict gains the rail's fields (event + surface anchor)

**Intent:** The rail's green/red predicate also encodes the non-code-event (`schedule`/`issues`)
exemption and the surface-production anchor. Those fields must live in the verdict **before** the rail
reads it, or the migration changes merge behaviour.
**Acceptance:** The verdict carries each group's triggering `event` (a third fetch surface,
`actions/runs[].event`, with its own failure mode) and the surface-anchor time; tests pin both.
**Files:** Modify: `tools/ci_verdict.py`, `tests/test_ci_verdict.py`.

---

## Task 5: Migrate `scripts/admin-merge.sh` to read the verdict

**Intent:** The rail's `check_surface_probe` is the largest re-derivation site; it must read the
verdict, not a per-name rollup.
**Acceptance:**
- The probe's green/red/pending decision is the verdict, and the rail feeds its **already-fetched**
  payload through the pure function (the CLI's `--check-runs-json`/`--runs-json` seam). The run map
  must be built **unconditionally** (not only on reds) and must carry `workflowDatabaseId` as well as
  the workflow name, because the group key depends on it; the verdict takes one check-runs page-set
  and one run-map, so "one fetch" is per-endpoint, not one HTTP call total.
- **The legacy `/commits/<sha>/status` surface is explicitly handled, not dropped:** either the
  verdict ingests the statuses half or the rail retains it with a recorded refusal. Dropping it
  silently is a fail-open and is not acceptable.
- The group-key change (rail `(app, name)` → verdict `(app, workflow identity, job)`) is called out
  and tested: a commit with two workflows sharing a job name (old red / new green) must read red.
- The #1353 unknown-status polarity is reconciled on **both** axes of the OVERRIDES note (unrecognised
  status, and `cancelled`/`stale` treatment), with named tests, or the divergence is emitted as a
  signal.
- **Full 40-hex shas only** at this boundary: the rail must reject a short sha as a read failure rather
  than let the `head_sha` listing silently return zero runs (a false no-verdict).
- **The producer side is closed too:** `.github/workflows/python-ci.yml` (and any other workflow whose
  shards can be cancelled) must be evaluated for a cancellation that GitHub reports as `cancelled`
  rather than a red — the verdict treats a post-red cancellation as a voided red, but it cannot undo a
  cancellation the workflow itself requests.
- The lane-tested precondition (`LANE_RUN_JQ`, "no run actually TESTED head") is migrated or recorded
  (#4279/#4339).
- **A rail harness is created (or named) first** — none exists today, so "the rail's tests pass" is
  currently vacuous.
**Files:**
- Modify: `scripts/admin-merge.sh` (`check_surface_probe` ~L1676–2130; step 4.6 ~L2928; lane-tested precondition)
- Create/Name: the rail harness (`scripts/admin-merge.test.sh` or a `tests/` entry point)
- Test: that harness

**Why not in increment 1:** the predicate folds event classification, the surface anchor, and the
legacy-status surface together; swapping only the green/red decision would change behaviour in both
directions. Task 4 moves the fields in first; this task migrates the predicate.

---

## Task 6: Migrate the failure-set producer to compose from per-sha verdicts

**Intent:** `ci-failure-set.sh` and the rail must agree on "a failing check".
**Acceptance:** The producer's per-commit run list is the verdict's per-group reds; the multi-run
composition (`--main-union[-rates|-signatures]` over N runs / multiple shas) is defined in terms of
N per-sha verdicts, and `no-verdict` maps to the documented fail-closed "emit nothing" behaviour;
the exemption threshold logic is unchanged (owner-authorized Option B — read through only). A harness
is created (none exists today).
**Files:** Modify: `scripts/ci-failure-set.sh` (run-listing half only); Create: its harness.

---

## Task 7: Replace the rate/envelope exemption with the verdict

**Intent:** Rate and envelope exemptions are **replaced by** the verdict, not stacked (issue's open
decision 3).
**Acceptance:** Before `ci_exemption.decide()` is deleted, the verdict carries the attribution
substrate `decide()` consumes — per-failure identity, main-side baseline, and stable signature — so
"a guard-step failure with a measured baseline is attributable" and "a never-measured guard-step reads
as unmeasured with a defined clearing path" remain expressible. Only then is `decide()` and the
rate/envelope machinery **deleted** (not reduced to a wrapper). In-flight runs defer (they do not
block). A harness is created (none exists today).
**Files:** Modify: `scripts/ci_exemption.py`, `scripts/admin-merge.sh`, `tools/ci_verdict.py`; Create: exemption harness.

---

## Task 8: The required review check and the verdict

**Intent:** `.github/workflows/ai-review-gate.yml` re-derives a **review** vocabulary from marker text
in four sites (#4755) — a different object from the CI verdict.
**Acceptance:** The gate's marker check stops regex-parsing a verdict vocabulary and reads the
head-bound review record (decision 4); an out-of-vocabulary marker still fails; the HMAC signature
check is preserved or replaced by an equally strong check. The CI verdict is **not** conflated with
the review verdict — if the intent is only that the gate stops re-implementing a vocabulary, say so.
**Files:** Modify: `.github/workflows/ai-review-gate.yml`; Test: `.github/scripts/ai-review-gate.test.sh`.

---

## Task 9: Consumer inventory — zero re-derivations

**Intent:** Assert O/I/T indicator 2 across all three classes.
**Acceptance:** A config/lint check enumerates every reader of per-commit CI state and fails if any
**re-derives** from (a) a per-name/attempt-agnostic rollup, (b) the legacy `/commits/<sha>/status`
endpoint, or (c) a hardcoded verdict vocabulary. Enumerate the complete set: `admin-merge.sh`
(probe + lane-tested precondition), `ci_failure_set.sh`, `ci_exemption.py`, `atomic-land.sh` (declared
`keep separate` — terminality wait), `ai-review-gate.yml`, `tools/ci_timing.py`. Zero findings, or a
recorded refusal per site.
**Files:** Create: `tests/test_ci_verdict_consumers.py`.

---

## Task 10: Checklist traceability matrix

**Intent:** Close the epic against the issue's Verification Checklist without re-running suites.
**Acceptance:** A matrix maps each of the issue's five checklist rows → task → named test → command,
and asserts every row resolves; any row with no test surfaces as a gap.
**Files:** Modify: this plan (append the matrix).

---

## Definition of Done

- [ ] `tools/ci_verdict.py` returns one of the four states for any sha, with coverage.
- [ ] Every checklist case is a named test; every polarity decision is pinned.
- [ ] 0 production consumers re-derive a verdict (Task 9 inventory, all three classes).
- [ ] 0 consumers convert "not completed" / "never ran" into failure.
- [ ] 0 regex-hardcoded verdict vocabularies in required checks.
- [ ] The mechanisms replaced (per-name rollup, rate/envelope exemption) are **deleted**, not guarded.
- [ ] The legacy commit-status surface is explicitly migrated or refused, never dropped.

### Adversarial Threat Surface

Gate code whose failure mode is **fail-open** (a false green or a masked red). Declared classes:

| # | In-scope bypass class | Covered by |
|---|-----------------------|------------|
| T1 | A superseded red masked by a newer attempt from a different group (job/workflow/run collapse) | `test_two_workflows_sharing_a_job_name_do_not_collapse`, `test_unresolved_workflow_does_not_collapse_two_runs`, `test_same_named_workflows_stay_separate_by_workflow_id`, `test_run_without_workflow_id_does_not_collapse_by_name`, `test_check_run_without_a_resolvable_workflow_groups_under_its_app`, `test_an_unnamed_red_is_not_masked_by_a_newer_unnamed_green`, `test_placeholder_named_check_is_treated_as_unnamed`, `test_named_check_with_a_missing_id_is_grouped_per_entry`, `test_three_duplicate_ids_cannot_mask_a_red` |
| T2 | Absence coerced into green (cancelled / never-scheduled / non-completed) | `test_cancelled_only_reads_no_verdict`, `test_empty_surface_reads_no_verdict_never_green`, `test_absent_group_plus_green_reads_green_recorded_policy`, `test_not_completed_named_in_flight_status_is_in_flight_not_red`, `test_in_flight_group_reads_in_flight`, `test_red_is_final_even_with_a_later_in_flight_group` |
| T3 | A read failure treated as an empty or green surface | `test_paginated_shortfall_is_a_read_failure`, `test_missing_total_count_is_a_read_failure`, `test_collect_items_rejects_a_non_object_page`, `test_gh_api_treats_an_empty_body_as_a_read_failure`, `test_cli_read_failure_is_exit_2_and_emits_no_verdict`, `test_cli_partial_read_failure_is_exit_2`, `test_cli_refuses_a_short_sha` |
| T4 | An unrecognised vocabulary read as green, or as a false red | `test_completed_with_an_undocumented_conclusion_is_red`, `test_unrecognised_status_with_a_failure_conclusion_is_red`, `test_not_completed_named_in_flight_status_is_in_flight_not_red` |
| T5 | Attempt-order loss (a newer non-measuring attempt masking or resurrecting a measurement) | `test_newest_attempt_is_decided_by_check_run_id_not_started_at`, `test_red_then_cancelled_in_the_same_group_reads_red`, `test_voided_red_with_another_green_group_is_not_green`, `test_red_then_cancelled_then_green_reads_green` |
| T6 | A short (`<40`-hex) sha silently yielding an empty `head_sha` listing, read as no-verdict | `test_short_sha_is_a_read_failure` |
| T7 | A verdict that cannot be consumed (offline WITHOUT the runs map silently drops the Actions
group identity, or the CLI exits 0 for a non-green state) | `test_cli_offline_without_runs_json_warns`, `test_cli_red_exits_nonzero`, `test_cli_in_flight_exits_nonzero`, `test_cli_json_is_the_full_object_and_no_verdict_exits_nonzero` |

Out of scope: GitHub API auth compromise; the correctness of the check-runs GitHub returns; exemption
threshold policy (owner-authorized Option B, agent-infra #1209).

[ADVERSARIAL-BOUND] cap=2 — see `proportional-gates` §Review Cycles, adversarial domain.

---

## Review Cycle Log (plan-review)

Proportional gate: standard+ task, adversarial domain → 4 reviewers (structural, integration,
failure-mode, duplication) + 1 adversarial acceptance pass; UX reviewer N/A (no user-facing surface).
Cap 2 cycles; acceptance = every declared threat class covered by a named test + green CI.

| Cycle | Reviewers | Issues found | Resolution |
|-------|-----------|--------------|------------|
| 1 | structural, integration, failure-mode, duplication | P0: group collapse when the runs map is empty (fail-open — an unresolved workflow collapsed distinct runs). P1: red→cancelled erased the red; truncation not reconciled against `total_count`; polarity divergence from rail #1353; plan Task-ordering and member-traceability contradictions; Task 5's "one fetch" impossible; short-sha false reds | Code: workflow identity from `workflow_id`→else run id; `voided_red` rule; `collect_items` requires a non-empty page list AND an int `total_count`; unrecognised status classified by conclusion; `_require_full_sha`. Plan: Task Dependencies serialized; member table reconciled; Task 5 acceptance rewritten. Tests added per class |
| 2 | fresh instances of all four + adversarial acceptance | P0: red→cancelled in one group was dropped from the state set, so ANY unrelated green group flipped the commit green. P1: `workflow_id`-less runs collapsed by name; unrecognised non-completed status with a negative conclusion; missing `total_count`; duplicate-id per-entry uniqueness; member-table contradictions (#4279/#4339, #4819); Task 5 run map lacked `workflowDatabaseId` and was red-only; short-sha → false no-verdict; rail/verdict shared-vocabulary divergence unrecorded | Code: per-entry grouping for unnamed/placeholder/id-less/duplicate-id; `group_state` fail-closed on unknown conclusion; `build_run_workflow_map` → `(workflow_id_or_None, name)`; `voided_red` + `test_voided_red_with_another_green_group_is_not_green`; short-sha assertion strengthened. Plan: this section + the Acceptance/Decisions/Integration/Threat edits above. **Threat classes T1–T7 now each covered by at least one named test; the 58-case suite is green and ruff-clean; a 7-mutation sabotage run failed 12 tests (none survived).** |

Residuals (not chased — out of increment scope, enumerated for later tasks): the rail's cancelled-only →
GREEN vs the verdict's `no-verdict` (Task 5); the `atomic-land.sh` unpaginated `check-runs` read (Task 9
inventory); `ci_exemption`'s attribution substrate (Task 7); #4819's watchdog-kill attribution (a
junit/log signal no attempt-level verdict carries) — kept separate rather than pinned.

---

> plan-review: gate=adversarial; domains=adversarial,integration,structure,duplication; cycles=2; cap=2; acceptance=THREAT SURFACE COVERED (T1–T7 each test-covered, no in-scope bypass reproduced); status=clean
> Reviewed-by: plan-review (4 fresh-context reviewers × 2 cycles + adversarial acceptance)

---

## Review Cycle Log (increment-1 code review — 2026-09-26 crash-recovery session)

Fresh-context `task` reviewers on the exact diff (`tools/ci_verdict.py`, `tests/test_ci_verdict.py`,
this plan) against the increment's declared contract and the T1–T7 threat surface. Cap 2 cycles
(adversarial-domain bound).

| Cycle | Reviewers | Issues found | Resolution |
|-------|-----------|--------------|------------|
| 1 | 2 fresh-context (adversarial fail-open hunter; contract/test-integrity) | **P1 fail-open**: an Actions check whose `details_url` did not parse (`run_id=None`, `workflow_key=None`) shared the `(app, None, job)` fallback group, so two same-named workflows collapsed and a newer green masked an older red — an unpatched T1 member. **P1 test-integrity**: the `app` axis was untested (dropping `app` from the group key survived all 53 tests). **P2 test-integrity**: `stale` never exercised (removing it from `ABSENT_CONCLUSIONS` survived). **P2 contract**: the full-40-hex sha invariant lived only in the fetch layer, so the offline CLI seam accepted a short sha and emitted a verdict. **P2 plan-drift**: the smoke record quoted a short sha the tool now refuses, and the "41 cases" count was stale | Code: `_is_provably_non_actions` + per-entry for an unresolved **Actions** check; `RUN_ID_RE` boundary relaxed to `[/?#]`/end; `_require_full_sha` moved into `compute_verdict` (the single entry point for online + offline). Tests: `test_unresolved_actions_url_does_not_collapse_two_workflows`, `test_two_apps_with_the_same_job_name_do_not_collapse`, `test_stale_only_reads_no_verdict`, `test_red_then_stale_in_the_same_group_reads_red`, `test_cli_offline_refuses_a_short_sha` (58 total). Plan: smoke sha corrected, counts updated. **Each new test was sabotage-verified: reverting its fix fails that test (5/5 mutations killed).** |
| 1 (rejected) | as above | **P2 over-engineering**: collapse `group_state`'s non-completed branch to `else: return GROUP_IN_FLIGHT` and delete `IN_FLIGHT_STATUSES`/`NEGATIVE_CONCLUSIONS`, on the grounds that GitHub never emits a non-completed status with a conclusion | **Rejected.** That branch is a *recorded polarity decision* (the table above: an unrecognised status is classified by its conclusion; a present negative conclusion is RED) added in plan-review cycle 2, and it is the fail-closed guard for an unrecognised-vocabulary schema drift — the class #4877/#4831 are. Adopting it would silently reverse a recorded decision; the route would be a reopen, not a review nit. The branch costs ~4 lines; the module keeps it. |
| 2 | fresh-context adversarial acceptance pass on the FIXED diff | **P1 test-integrity**: the unnamed/placeholder and id-less per-entry guards were not load-bearing in their own tests — `check_run()`'s default `details_url` is an unresolvable Actions URL, so the per-entry routing came from the cycle-1 unresolved-workflow clause; deleting either guard left the suite fully green while a payload with a RESOLVABLE run id flipped red→green | Tests: all three cases given a resolvable run id + runs map, so each guard is now the sole reason the test passes. **Mutation-verified: removing the unnamed/placeholder guard fails 2 tests; removing the id-less guard fails 1.** No new in-scope fail-open reproduced; the review bound (2) was reached, so the fix was closed out by a final fresh-context verification rather than a further cycle. |

> increment-1-code-review: gate=adversarial; cycles=2; cap=2; final acceptance=THREAT SURFACE COVERED (fresh-context verification of the cycle-2 fix: 58 green; every T1–T7 class test-covered and mutation-killed); rejected=1 (a recorded polarity decision, not an open defect); status=clean.
