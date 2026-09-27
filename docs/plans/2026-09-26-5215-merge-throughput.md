<!-- research-path: docs/scoping/2026-09-26-5215-merge-throughput.md -->

# Merge Throughput Implementation Plan — eligibility, cycle time, and failure cost

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Raise the merge rate from a measured **~2.1 PRs/hr** toward the epic's ~200/day target by
fixing **eligibility** (why ~105 of 135 open PRs cannot enter the queue) and then **throughput**
(`batch × concurrent ÷ cycle`) — cutting CI time **without** eliminating any safety property, and
**attributing** the gap between the queue's configured ceiling and its observed rate.

**Team:** organisation-design-team
**Role:** (not set in this session)
**Plan-review:** **Cycle 10 — the cycle-9 residual set fixed; fresh re-review dispatched from this lane.**
Cycles 1–5 ran 5/3 reviewers; **cycle 6** found 2 P0s + 8 P1s; **cycle 7** found 3 P0s; **cycle 8**
(adversarial gate, vacuity, failure-mode, contract) returned **ISSUES — NOT `THREAT SURFACE COVERED`** and
showed **I10 was not a real detector** (a union-only comparison let a second `queue_rule` through, a
config-only comparison let a second `python-ci-gate` emitter through, the record was content-free, and
`git diff "$BASE"...HEAD` has **no merge base** in the depth-1 checkout); **cycle 9** returned **ISSUES**
again and found the sharper form of the same root — the record was **still content-free** for a non-check
condition change (a `-draft` removal plus a `verified_at` bump passed), the emitter map parsed `*.yml` only,
and `base_sha == github.event.pull_request.base.sha` **differs on the Mergify queue head**, which would have
**permanently deadlocked the queue for exactly the gate-definition PRs**, including #5384a. **Cycle 10
replaced the whole diff/base-ref approach with a head-computed `gate_digest`:** the record must carry
`sha256(canonical(entry-gate projection))` over every `queue_rules[*]` (names **and** full condition lists),
`merge_conditions`, the effective mode, `autoqueue`/`auto_merge_conditions`, and the emitter map
(`*.yml` **and** `*.yaml`), and I10 asserts **`gate_digest(head) == record.gate_digest`** — no base ref, no
diff, no sha, no fetch, computable on every event. Cycle 10 also fixed two self-inflicted freezes the cycle-9
review found (new test files are unregistered in `config/ci-surfaces.yml`, which fails
`manifest-integrity --integrity` on the plan's own Wave-0 PR; and shipping the record with
`settings_home_consistent: true` would red main on every push). The verdict of record is
`docs/plans/2026-09-26-5215-merge-throughput.cycle-status.yaml`. Because the plan declares an
`### Adversarial Threat Surface`, the domain's acceptance is a fresh reviewer returning
**`THREAT SURFACE COVERED`** — **not** `NO ISSUES FOUND`. **The bound is stated per vector (§7):** TH1/TH3
(I8 ABSENT, #5433/D1), TH4(b) (validator correctness, #5570) and TH7 (#5649) are **out of bound with a reason
and a filed owner**; the in-bound vectors (TH2, TH4(a), TH5, TH6) are each covered by a named clause and
fixture. The live re-read (I1) is **D14** — owned and dated. Remaining owner decisions are enumerated in the
cycle-status board.
`[ADVERSARIAL-BOUND] cycles=10 threats=7 vectors=8 covered=4 out-of-bound=4(TH1,TH3→#5433/D1 · TH4b→#5570 · TH7→#5649) residuals=#5433,#5570,#5649,D14`

**Architecture:** A **measurement-led** plan. It (1) reopens the queue's input via the eligibility fix;
(2) builds **one** instrument that **owns the pass/fail contract itself** — success criteria are exit
codes from the tool, never shell pipelines, because a `jq` comparison over a sentinel is how a
criterion passes for the reason it exists to fail (§11.1); (3) resolves the **unattributed gap** before
raising any knob; and (4) makes the safety contract mechanical: one authoritative invariant list (§3),
asserted by one guard, reusing the assertions that already exist rather than duplicating them.

**Tech Stack:** Mergify merge queue (`.mergify.yml`); GitHub Actions (`python-ci.yml`, `ci.yml`,
`ci-timing.yml`); GitHub REST via `gh api`; `git merge-tree`; pytest (no xdist today);
`tools/ci_selection.py` + `config/ci-surfaces.yml`; `tools/ci_timing.py`; the rail
`scripts/admin-merge.sh` (**agent-infra via an absolute symlink — not resolvable on a runner**).

---

## 1. Problem definition

The full double diamond, alternative framings, assumption map, falsification check and axis research
live in `docs/scoping/2026-09-26-5215-merge-throughput.md` (#5215 carried **no** `issue-scoping`
signature; this lane authored the scoping artifact).

- **P1 ELIGIBILITY** — why most open PRs cannot *enter* the queue. Measured 2026-09-26: **135 open
  PRs**, **30** genuinely in the queue (all sampled `Mergify Merge Queue = in_progress`), **44**
  conflicting at merge time (**33** on three shared artifacts), **0** auto-queued in 7 days, **8** PRs
  holding an inert `queue-accelerator` label.
- **P2 THROUGHPUT** — why those 30 drain at ~2/hr. Cycle `test (a)` 36.4/37.2 min; Mergify's own ETA
  for a PR entering 20:05Z is **2026-09-27 10:19Z (~14.2 h)** ⇒ **≈2.1 PRs/hr**; observed batches are
  **size 1** (`Merge of #5397` 21:44Z, `Merge of #5383` 22:23Z).

**They are separate workstreams.** Fixing P2 while P1 is broken buys nothing; fixing P1 while P2 is
broken only fills the lot.

> **Epic status note.** Epic task 1 ("fix the merge queue") is **DONE** — **#3558 is closed**
> (2026-09-25), and the queue now shows real activity. The epic's "0 Merged / 7 days" reading is
> superseded. The queue is **revived but slow**, which is why this plan targets eligibility +
> measurement rather than "the queue is dead".

### 1.1 The arithmetic — and four corrections to the evidence base

`rate = PRs_per_batch × concurrent_batches ÷ cycle_time`

| reading | value | source |
|---|---|---|
| observed drain | **≈2.1 PRs/hr** | live, Mergify check-run ETA (30 queued, ~14.2 h) |
| observed batch | **1** | live, `mergify/merge-queue/*` messages |
| cycle | **37 min** | corpus §3 |
| ceiling at the documented default (`batch × parallel ÷ cycle` = `2 × 5 ÷ 37`) | **≈16/hr** | vendor docs |
| **gap** | **≈8× at parallel 5; ≈3× if the effective value is 2** | — |

**⟨C1⟩ "parallel 1 (serial)" is not a config fact.** Mergify's docs: `mode` defaults to `serial`
(merge *ordering* only), **`max_parallel_checks` defaults to 5**. Corroborated live: two queue branches
coexisted and three PRs hold different states. The corpus read **demand starvation as a config limit**.
Consequence: **#5527 setting `max_parallel_checks: 3` may be a *reduction* from an effective ≥5, not a
3× gain.** Its defensible value is a CI-capacity **cap** plus `priority_rules`. **#5527 must not be
described as 3× before M3 — and the same caution applies to this plan's own "8×", which is a
ceiling-at-default, not a measurement (S13).**

**⟨C2⟩ The durations map exists; the *collector* has never run — and two artifacts have no bridge.**
- The fast split reads **`config/ci-surfaces.yml`** (`ci_selection.py:51 MANIFEST`), which declares
  `durations:` with **688 entries**; `ci_selection.py --integrity` reports **"halves consistent"**.
- `config/ci-surfaces.yml:1682` records that those durations came from a **one-off sweep** of
  2026-09-22 junit artifacts — i.e. an **unnamed driver**, not the collector.
- `tools/ci_timing.py` (`SCHEMA_VERSION 1`) + `.github/workflows/ci-timing.yml` exist, and
  `docs/ci-timing.json` is `{"schema_version": 1, "history": []}` — **never sampled**; **#5393 is a fix
  to that collector, not a new one**.
- **Nothing moves collector output into `config/ci-surfaces.yml:durations`.** So "make the collector
  run" does **not** by itself refresh the map the balancer packs by. That missing bridge is the real
  T-B defect (Reviewer #5 D3), and it is why the 38/28 split is **unexplained**: the pack says
  balanced, the wall clock says otherwise.

**⟨C3⟩ Batching's cost model — corrected twice.** Exact expectation (**the first draft
double-counted the initial run**):

```
runs/PR(b, p) = [ P(green)·1 + P(red)·(1 + log₂ b) ] / b  =  (1 + P·log₂ b) / b ,  P = 1−(1−p)^b
```

| `p` | `P(batch red)` | b=2 | b=4 | vs unbatched |
|---|---|---|---|---|
| 0.05 | 0.185 | 0.549 | **0.343** | 2.9× cheaper |
| 0.30 | 0.760 | 0.755 | **0.630** | 1.6× cheaper |

**So b=4 is cheaper than b=2 and than unbatched even at p=0.30** — the earlier "batching stops paying"
claim was overstated. The real reasons to sequence `batch_size` are **(i) latency, not runs** (each red
costs `1+log₂b` *cycles*, and #5397 is bisecting live); **(ii) a confounder — the observed batch is 1,
so `batch_size` is not the binding variable until M5 explains why batches don't pair**; and **(iii) a
term this model omits** — serial mode is cumulative, so a red invalidates the speculative batches above
it, wasting up to `max_parallel_checks−1` cycles (measured by M6; **not** assumed).

**⟨C4⟩ main's red is a real defect, not a flake — and #5597 does not "remove a test".** The earlier
draft recorded this as "a real defect, not a regression" in one place and as "removes the failing test"
in another; both are wrong. `#5597` changes `tortoise/search_engine.py` so the index branch stops
returning the engine's **distance** as a **similarity** (root **#5583**) and adds a polarity guard,
rewriting five tests that pinned the inverted reading. The **expression** is embedder/config-dependent
(hence "intermittent"), but the **cause is a defect**; corpus §7 explicitly warns not to dismiss the
two nodeids as flake. This matters because the failure-cost axis and E1b's gate rest on `p`: a real
defect needs a fix, not quarantine.

### 1.2 Assumptions

| Assumption | Status | Evidence / falsifier |
|---|---|---|
| The heavy suite is the entry blocker, not the five cheap checks | **validated** | #5384 patch: the five report `success` (never `skipped`) across every sampled PR |
| Effective `max_parallel_checks` ≥2 | **validated (weak)** | 2 concurrent queue branches; differing ETAs for same-entry-time PRs |
| Effective value is 5 | **unverified** | only ≥2 observed → **M3** |
| Conflicts halve batch formation | **unverified; confounded** | 1-PR batches observed, but `batch_max_wait_time: 5 min` + starved arrivals also produce them → **M5** |
| main's red is a **defect** whose expression is config-dependent | **validated** | #5583 root, #5597 mechanism (⟨C4⟩) |
| Runner capacity binds above some concurrency | **validated** | load 49–171; jobs failed to start; oldest queued run 1 h 15 m |
| A job in a required aggregate's `needs:` is load-bearing | **validated** | `python-ci.yml:2096`; already asserted by `tests/test_ci_selection.py::test_drift_gate_cannot_skip_the_test_matrix` |
| Five of six required contexts have **no push-to-main signal** | **validated** | `ci.yml` is `on: pull_request:` **only**; `python-ci.yml` is `push:[main]` + `pull_request` |

**Falsification check.** Wrong if, with main green and the queue populated, the drain rate does **not**
rise above ~4/hr — then eligibility was never the constraint. S3 measures exactly this.

**Confidence: 76/100.**

---

## 2. Lever register — every lever, with its protection answer

**Rule (owner's constraint, non-negotiable):** cut CI time **without** eliminating safety. Every lever
answers **"what does this stop protecting, and why is that safe?"** An item that cannot answer is
**rejected**, not softened. `☑` answered · `⚠` conditional (condition named) · `⛔` rejected.

### 2.1 P1 — eligibility

| # | Lever | Measured input it moves | Arithmetic effect | Owner | Risk | Protection answer |
|---|---|---|---|---|---|---|
| **E1** | **#5384a** — `branch_protection_injection_mode: merge` | Entry no longer requires `python-ci-gate`; entry cost ~37 min → ~2 min | **Not** a multiplier: raises the *rate* by removing the per-PR heavy run, and **decouples entry from main's health** so a future red main cannot close the gate | PR **#5384** | A PR that breaks the heavy suite burns a **batch slot** rather than being stopped at its own head | ☑ **Conditional, and the residual is asserted (I2b + I4), not merely stated.** Stops protecting *"the heavy suite's verdict on the PR head alone"* — **strictly weaker** than its replacement: `python-ci-gate` stays a `merge_conditions` with `check-success` and is re-run on the **queue branch, the tree that lands**. **Residual:** under `merge` injection the five cheap contexts are enforced at *entry* by explicit `check-success` but at *merge* only by injected conditions, which **accept neutral/skipped** (I2b). **Note the benefit is not double-counted:** with main green, the 105 PRs satisfy the *default* `queue` entry conditions anyway (that is #5597's job); E1's own contribution is entry **cost**, not eligibility restoration. |
| **E1b** | **#5384b** — `batch_size: 2→4` | PRs per batch | 2× **if** batches pair | PR **#5384** | Latency per red (⟨C3⟩) | ⚠ **Not yet binding** (observed batch = 1). Gated on **M5** + flake/defect attribution (⟨C4⟩). Stops protecting *bisection cheapness*; safe on runs even at p=0.30 (0.63 vs 1), **not** obviously safe on wall-clock latency while main is red. |
| **E2** | **#5570** — `merge=union` + validator + untrack the generated table | Removes **33 of 44** conflict instances | Makes the long tail eligible; also raises batch formation | PR **#5570** | `union` can duplicate/interleave keys — a silently *wrong* merge | ☑ **SAFE. The first draft's "P0 finding" was WRONG and is retracted:** the validator sits in `manifest-integrity`, which **is** on the gate via `python-ci-gate.needs` (`python-ci.yml:2096`) → a duplicate-key union **does** redden the merge. "Non-required context" ≠ "not on the gate" (I5). |
| **E2b** | *(derived)* Conflicts → batch size 1 | Batches are size 1 while `batch_size: 2` | Couples P1 to P2 | **M5** | **Confounders:** starved arrivals; `batch_max_wait_time: 5 min` | ☑ Measurement only. M5 records batch-arrival timestamps + queue depth at formation **in the same window** as the conflicted set. |
| **E3** | Dead weight — **#5453, #5455, #5460, #5190** | 4 PRs × 37-min runs + queue slots | Frees slots; correct resolution yields a tree **byte-identical to main** | **Resolved per-PR by Task 7** (session+branch+first-message owner, `owner_evidence` column) — **not** "other lanes" | None to safety | ☑ Stops protecting: nothing. |
| **E4** | Contract-bound drafts — **#5463, #5464, #5466** | 3 PRs × 37-min runs | As E3 | **Resolved per-PR by Task 7**; contract = **verbatim preservation** | Their bodies say *"NOT review-ready … must not enter the queue"* | ☑ Stops protecting: nothing. **Condition:** preservation, not deletion — and S15 therefore excludes `draft` (see §11). |
| **E5** | Terminal conflicts — **#5196, #5285, #4963** | 3 permanently unmergeable PRs | Permanent queue slots | **Per-PR owner via Task 7**; **D5** sets the decision date | They assert the **opposite** contract to main (`Document` vs `:Source`) | ⚠ **Not a rebase.** A hand resolution would silently reverse a contract. |
| **E6** | Auto-queue verification | Whether `auto_merge_conditions: true` ≡ `autoqueue` | If false, **#3016/#5443** reopen | #5424 (landed) + **#5443** | Cannot run while main is red | ☑ Observation; removes no check. Unblocked by S1. |
| **E7** | **HARD-STOP surfaces** #5136, #5461, #5465, #5467, #5468 | 5 PRs | — | **Human decision owner (D12)**, surfaced by Task 7 as `hard_stop: true` | A wrong resolution **silently disables a check** | ⛔ **Out of scope and out of the closure pipeline.** Excluded from S15: it is a **decision queue**, not rot — and it must not sit unowned, so D12 names an owner **and a date**. |

### 2.2 P2 — throughput

| # | Lever | Measured input it moves | Effect | Owner | Risk | Protection answer |
|---|---|---|---|---|---|---|
| **T-A** | Resolve the gap (M1/M3/M5/M6) | — | Makes every number credible | **this plan** | None | ☑ Pure measurement. |
| **T-B** | **Duration refresh** — **build the bridge** from collector → the map the balancer reads (#5393 fixes the collector; #5050 tracks consolidation) | `test (a)` 38 m vs `test (b)` 28 m — cause **unknown** (⟨C2⟩) | If weight-driven: cycle 37 → ~33 min (~12%) | PR **#5393** + **Task 4b** (the bridge is unowned) | A stale/bogus duration creates a starved shard | ☑ Stops protecting **nothing** (pure bin-packing; the test set is unchanged). Conditions: **M2 must attribute the split first**, and the refresh must **value-validate** durations (#5050). **Does not** claim the `test_agent_signup.py` "coverage hole" as a safety gain — that file is **by design** in `ENV_BROKEN_FILES`, and `fast_files_absent_from_halves` is deliberately informational because closing it "would push 100+ files into the fast gate and blow the watchdog budget" (`ci_selection.py:1327-1333`). **Its executing leg is `flip-gate`** (`.github/workflows/ci.yml:1394` → `.github/scripts/verify-cutover:133`). |
| **T-C** | **#5414** lazy `redislite` import | 959 ms → **16.5 ms**/invocation | Multiplies across ~200 embedded servers/run | PR **#5414** | A lazy import returning `None` silently degrades | ⚠ Stops protecting **nothing** if the lazy path raises on use. Condition: a test asserting `ImportError` (not `None`) on cold use. |
| **T-D** | **#4767** move the most expensive file out of the fast lane | `test (a)`, the critical path | Small direct | PR **#4767** | A file moved into a lane that never runs it | ☑ Provided selection still reconciles — `ci_selection.py` fails closed on slow-leak/dead/imbalanced halves. Every fast file must be either in a half or in a **declared exclusion set** (`ENV_BROKEN_FILES` / carve-out / slow / bench) — an *unclassified* file is the failure, not a by-design exclusion. The mechanism is **S8's `fast-files-unclassified` conjunct** (plus `ci_selection.py --integrity`), not M8. |
| **T-E** | **Failure-cost axis as throughput work** — **#5469** verdicts, **#5474** canonical diff, **#5476** docs check actually lints, **#5597** the real defect | defect rate `p`; attribution latency | Sets the value of E1b; removes a full cycle + bisection per red (#5397 is bisecting **live**) | PRs **#5469/#5474/#5476/#5597** | Quarantine can hide a real regression | ☑ **#5476 is safety-POSITIVE:** the `docs` job's only real content gate today is a conflict-marker check (markdownlint/lychee are skipped on PRs). **Quarantine would stop protecting *"a red always blocks"* — and no in-repo quarantine set exists today** (I7 = ABSENT → **D11**). |
| **T-F** | **#5527** `max_parallel_checks: 3` + `priority_rules` | concurrent batches | **May be a reduction from the default 5** (⟨C1⟩). Real value: a deliberate **cap** + accelerators jumping the queue | PR **#5527** | 8 PRs hold `queue-accelerator`; `allow_checks_interruption: true` **cancels running checks** — and a `cancelled` queue-branch run blocks Mergify's `check-success` while the rail calls it non-red (I6b) | ⚠ **Blocked on M3 + M4.** Stops protecting nothing directly, but mislabelling it "3×" would justify raising it later without evidence. `priority_rules` is a value judgement (**D6**). |
| **T-G** | **Runner capacity** (epic task 3) | concurrent CI runs | The **real ceiling**: concurrency multiplies concurrent runs (~10 jobs each). Live **63 queued / 12 in progress / oldest 1 h 15 m** | **D4 (cost)** | Starvation ⇒ SIGKILL; a starved job must never read green | ☑ Enforced by the **rail + Mergify** (`check-success`), not by this plan's instrument (I6). **Gated, not just measured:** I9 refuses a configured parallelism that is **`≥ capacity_at_first_failure`** (equivalently `headroom < 1`) — the I9 predicate, used verbatim here. |
| **T-H** | Path-filtered / selected legs (epic task 4) | checks per change | Large for docs-only changes | **D7** | Over-narrow selection lets a real break through | ⚠ Stops protecting *"the full suite runs on every change"*. Safe only with fail-closed selection **plus a full-suite trip** (main/nightly). |
| **T-I** | **xdist** | `test (a)` wall time | up to ~2× | **D3** | Duplicate fixtures, shared DB/port collisions, module-state leaks, port-holding subprocesses outliving the process | ⚠ **Rejected for advance-now.** Stops protecting **test isolation**; admitted only behind a one-shard proof (`--dist loadscope`, per-worker resources, leak-count assertion). |
| **T-J** | **#5426** — `ai-review-gate` red by construction, never required | a permanently-red, never-required surface | No direct rate effect | **D8** | A red that can never clear trains readers to ignore red | ⚠ Its stops-protecting answer *is* the point: "red" stops signalling action — the habit that makes a *real* red (main's) ignorable. |
| **T-K** | Bypass the gate / drop a required check / `--admin` | — | Would "work" | — | — | ⛔ **NEVER AN OPTION.** |

---

## 3. Protection invariants — one authoritative list

**Asserted, not narrated.** §2 references these by number and does **not** restate them (the first draft
kept three hand-copied ledgers, which would drift). Every row cites a mechanism that can **fail**, or is
marked **ABSENT**.

| # | Invariant | Enforced by |
|---|---|---|
| **I1** | **Check-condition** sets agree: `{c.split('=',1)[1] for c in queue_conditions+merge_conditions if c.startswith('check-success=')} == set(live required contexts)`. **The RHS is the BARE-NAME set** (`required_status_checks.contexts`) — no `.split` (cycle 5: applying the same `.split('=',1)[1]` to bare names raises `IndexError` on `"docs"`, so the invariant was again unsatisfiable). Non-check conditions leak in nowhere: the LHS predicate excludes `base=main`/`-draft` | Task 4 **live clause** — **operational, admin credential, never in the required path** — persists `{"gate_digest": <I10's digest of the read's own tree>, "required_contexts": [bare names], "emitters": {<context>: [[workflow, job], …]}, "verified_at": ..., "read_sha": <the main sha the read was taken at>}` to `docs/ci/required-contexts.json` — **one schema, I10's keys included (cycle 10: the cycle-9 cells gave this file three incompatible shapes, so an I1 re-write would have dropped the keys I10 reads; `read_sha` is provenance ONLY and is never an equality target; the digest is recomputed, never copied)**. **I1's freshness window is its OWN: quarterly** (cycle 5: a quarterly cadence can never satisfy a 7-day window ⇒ permanently red); it is the **only** I1 freshness rule (cycle 10: the cycle-9 cell stated it twice). **Fixtures:** the CURRENT correct `.mergify.yml` + live bare-name set ⇒ SATISFIED; one name removed ⇒ DIVERGED; empty LHS ⇒ DIVERGED (I2 guarantees `python-ci-gate ∈ merge_conditions`, closing the `∅ == ∅` escape). **Fail conditions:** artifact absent; `verified_at` past the quarterly window; check-name sets differ. `UNAVAILABLE` is non-clean, never satisfied |
| **I2** | **Mode-aware list structure.** No check named twice **within one list**; `python-ci-gate ∈ merge_conditions`; and either `merge_conditions ⊆ queue_conditions` **when `branch_protection_injection_mode` is `queue` (or absent)**, or `queue ∩ merge == ∅` **when it is `merge`**. **⛔ Any OTHER value (`"Merge"`, a future vendor value) ⇒ exit 2** — a fail-closed guard on the entry gate must not fall through to 0 on an unrecognised mode (cycle 5) | Task 4 **static clause (i)** (no duplicate within a list; **`python-ci-gate ∈ merge_conditions`** — the conjunct I1's `∅ == ∅` escape depends on) **and clause (ii)** (mode-aware, fixtures for BOTH config states + an unknown-mode fixture); fixtures: a duplicate name within one list ⇒ **1**; `merge_conditions` lacking `python-ci-gate` ⇒ **1** |
| **I2b** | Under `merge` injection, the five cheap contexts are explicit `check-success` **entry** conditions (the merge-time injected path accepts neutral/skipped — the named residual of E1) | Task 4 **static clause (iv)** — the clause E1 cites |
| **I3** | No `check-pending` / `-check-failure`; every **check** condition is `check-success`. **Scoped to check-conditions:** `queue_conditions` legitimately carries non-check conditions (`base=main`, `-draft`), so "every condition is check-success" read literally would false-fail the live config | Task 4 static clause |
| **I4** | Every check named in either list **reports on a `mergify/merge-queue/*` head** (the deadlock guard) | **The instrument, one form only: `check assert-queue-head-checks`** over the newest queue head — **exit 1 on a missing name, exit 2 when no queue head exists** (cycle 7: the flag form and the check-name form were both cited and never reconciled; ownership is the instrument's alone, and S12 consumes it) |
| **I5** | A red job inside the required aggregate's `needs` reddens the aggregate — **required-ness is the aggregate, not the check name** | **Already asserted** by `tests/test_ci_selection.py::test_drift_gate_cannot_skip_the_test_matrix` (+ `python-ci.yml:2096`) — **cite it; do not re-implement** |
| **I6** | Runner starvation cannot read as green | The **rail** (`admin-merge.sh:1762` `NON_RED_CONC`) and Mergify — **mirrored, not owned** |
| **I6b** | The instrument's `main_gate` (allow-list) and Mergify's `mergify_mergeable` (`check-success`: only `success` satisfies) are **distinct fields**, and a disagreement on `cancelled`/`neutral`/`skipped` is surfaced — **and `mergify_mergeable` is CONSUMED by S1, not merely computed** (cycle 4: a tested-but-unread field is a fail-vain instrument output) | Task 1 — both fields + a unit test per token; **S1 reads the strict field** |
| **I7** | A quarantined test carries an expiry + a named owner | **ABSENT — no in-repo quarantine set exists.** Decision **D11** |
| **I8** | The review record is read server-side before merge | **ABSENT — Decision D1.** The one genuine safety gap (#5433) |
| **I9** | **One predicate, stated once (cycle 5: the headline `configured ≤ headroom` and the definition `headroom = capacity_at_first_failure − configured` are different predicates).** **The refusal condition is `configured_max_parallel_checks ≥ capacity_at_first_failure`** (equivalently `headroom < 1`). `headroom = capacity_at_first_failure − configured`. `capacity_at_first_failure` = the concurrency at which a run **demonstrably failed to acquire a runner**; **if the M4 window contains no such sample, it is UNKNOWN ⇒ exit 2** (refuse, never pass). "Time-bounded fallback" applies to *measurement scheduling*, **never** to authorizing a value. T-G, Task 4b, S11 (`--min-headroom 1`) and S14 all use THIS predicate | Task 4b / M4, S11, S14 |
| **I10** | **The entry gate's DEFINITION is committed to a DIGEST, and the record must match the head.** `docs/ci/required-contexts.json` must carry `gate_digest = sha256(canonical(entry-gate projection))`, where the projection is **all** `queue_rules[*]` (names **and** full condition lists), `merge_conditions`, the effective `branch_protection_injection_mode` (`queue` when absent), `autoqueue`, `merge_protections_settings.auto_merge_conditions`, **and the emitter map** (`context name → sorted [(workflow, job)]`, parsed from `.github/workflows/*.yml` **and** `*.yaml`). I10 asserts **`gate_digest(head) == record.gate_digest`** plus a fresh `verified_at`; anything else exits 1. **HEAD-ONLY — no base ref, no diff, no sha (cycle 10).** The cycle-7/8/9 base-ref forms were each defeated: a union-only comparison missed a second `queue_rule`; a config-only comparison missed a second emitter; the record was content-free so a `verified_at` bump passed; and `github.event.pull_request.base.sha` is a **moving target that is different on the Mergify queue head**, so the equality could never hold there — a **permanent queue deadlock for exactly the gate-definition PRs**, while the plan's own first lever (#5384a) is one of them. A digest of the head has none of those failure modes: **any** change to the gate's definition changes the digest, so the record must be re-cut; a re-stamp cannot fake it; and it is computable on **every** event, including a queue head. **Not a live read** (a committed file is still hand-writable — the authorship control is **I1/D14**), and **not TH7** (#5649: a PR that rewrites an emitting job's *body*). **This is TH6's mechanical detector, and it is green today** | Task 4 static clause **(viii)(a)** — fixtures: a second `queue_rule` ⇒ **1**; a second emitter job for a required name (`*.yml` **or** `*.yaml`) ⇒ **1** (clause (vi) uniqueness ⇒ **1**); a removed `-draft` ⇒ **1**; a `verified_at`-only re-stamp with an unchanged digest ⇒ **1**; a changed definition + re-cut digest + fresh `verified_at` ⇒ **0**; on a `mergify/merge-queue/*` head ⇒ **computed from that head** (a batch that does not touch the gate passes) |
| **I11** | **The required-set declaration home is not silently divergent.** `.github/settings.yml` — the one machine-readable home #3467 designated — declares `contexts: [redis-guard]` while the live set is the six bare names (`pricing-artifact, docs, test-isolation, license-surface, legal-e2e, python-ci-gate`, verified live 2026-09-26), and **no workflow or test reads the file**. The **artifact** (`docs/ci/required-contexts.json`) is the committed home; the file's reconciliation is **D9**, which lands **#3467's own fix** — resolve its **Open Question 3** (live or vestigial), a **named write step** to the intended set, and a **consistency test** asserting file == artifact == live read-back (none exists today) | Task 4 static clause **(viii)(b)** + **D9** (owner Daniel, by 2026-10-10). **Deliberately NOT a TH6 detector** (cycle 6: attaching the file's equality assertion to TH6 made it unsatisfiable until the write lands — it is a declaration-home item) |

**Duplication discipline (Reviewer #5).** Consume, do not re-implement:
- **Check-run polarity/grouping** — the rule lives in the rail (`admin-merge.sh:1762` `NON_RED_CONC`)
  and `scripts/ci-failure-set.sh`; AGENTS.md names the rail as the merge-gate authority. **⛔ THE RAIL IS
  AUTHORITATIVE; the instrument's `main_gate` is an OBSERVATION that never overrides it** (cycle 4: the plan
  previously duplicated the token set with no shared declaration and only "surfaced" a disagreement). The
  rail is an absolute symlink to agent-infra, unresolvable on a runner, and its rule is embedded in a bash
  heredoc — so the parity test **cannot run in CI as a file read**. Resolution: (a) Task 1 pins the token set
  in one committed list **with a parity test against the rail at agent-infra's pinned ref** (#3467's
  pinned-ref pattern can deliver the shell helper — the earlier "cannot run in CI" claim overstated it), and
  (b) the authoritative-wins rule is stated in I6/I6b, not left implicit.
- **The fast-pool definition** — `tools/ci_selection.py::fast_pool()` is already the single source of truth
  (excludes `slow + ENV_BROKEN_FILES + carve_out`). **⛔ `fast-files-unclassified` MUST consume `fast_pool()`**
  (cycle 4: the plan had invented a *third* definition with a different exclusion set, silently disagreeing
  with `fast_files_absent_from_halves` on a real file).
- **The timing contract** — `tools/ci_timing.py` is the one collector; **`config/ci-surfaces.yml:durations`
  is the one artifact the balancer reads**; the bridge between them is the missing piece (T-B / Task 4b).
  **⚠️ `tools/ci_timing.py:17` currently asserts "Measurement only — never gates CI"** — Task 4b makes it the
  writer of the weights `split_fast_gate` packs by, which #3395 showed can red `python-ci-gate` with zero test
  failures. **Task 4b must amend that in-code invariant in the same change** (the plan previously left it false
  in the very file it edits).
- **The `config/ci-surfaces.yml` FILE has a second writer** — `ci_selection.py::register_tests` writes it
  **text-preservingly** (`:1024`, `:1063`). Task 4b's `--refresh-durations` must reuse that line-edit
  discipline (never a whole-file `yaml.safe_dump`, which would strip the hand-curated comment header at
  `ci_selection.py:80-84`); a test asserts a refresh preserves comments and still passes `--integrity`.
- **The `needs:`-closure assertion (I5)** — already exists in `tests/test_ci_selection.py`; cite it.
- **Base-ref pinning** — `tests/test_surface_guard_pin_4606.py` is the existing pattern for "a PR cannot
  weaken the guard in the same commit"; **Task 4's guard reuses it** as an **inventory + base-self-consistency**
  pin — the base ref is **fetched before the read** (a shallow checkout otherwise makes `git cat-file -e`
  fail as "not a valid object name", which the pin would misread as *absent at base*), and clause logic stays
  fixable so one wrong clause cannot freeze the repo.

**Required-set declaration homes.** Five declarers exist; **#3467 records the owner decision** —
`.github/settings.yml` is the **one machine-readable home**, with `fly-token-hardening.md`'s `PUT`
fenced as a historical excerpt and `1970-main-hygiene.md`'s executable step given a superseded note;
`redislite-process-leak.md` carries a **mutating** create/revert instruction. **This plan does not
reopen that decision** (contradiction test: an owner decision outranks a plan's convenience): **D9
becomes "land #3467's recorded consolidation"**, not "neutralize `.github/settings.yml`" as the first
draft proposed — the first draft silently reversed a recorded choice.

---

## 4. Advance now / blocked on / decisions needed

### 4.1 Advance now — unblocked **today**, owned by this lane

**Tasks: 1, 2, 3, 4, 4b, 5, 6, 7, 8** — **nine dispatch units**, so §13 declares **Parallel Session**
(the `writing-plans` >8 threshold). The first draft miscounted this as 8 in §13 while listing nine here.

**Parallelism map** — **explicit waves, and ONE WRITER PER FILE PER WAVE** (cycle 7: the earlier map put Task 3 and Task 5 — both writers of `tools/merge_throughput.py` + its test file — in one wave, and put Task 3 in the same wave as Task 2, whose *new* doc Task 3 must modify):
**Wave 0:** Task 1 — it implements its **full declared grammar**, including `--triage`/`--emit rows` and `--watch-queue`/`--observe-capacity` (the synopsis comment at §11.1 that credits `--watch-queue`/`--observe-capacity` to Task 3 is a pre-cycle-7 residue — cycle 10), so that after Wave 0 **no other task writes `tools/merge_throughput.py` in Wave 0** (later waves add checks — Task 4b's durations-map conjunct, Task 8's `NO_MAIN_SIGNAL`/job-id mapping; the rule is **one writer per file per wave**, not once ever — cycle 10) →
**Wave 1:** Task 2 (creates the measurement doc) →
**Wave 2:** Task 3 (M3–M6; consumes Task 1's flags and **modifies Task 2's doc** — it must follow its creator) ∥ Task 5 (triage rows; it writes only the test file, never the tool) →
**Wave 3:** Task 4 (guard) ∥ Task 4b (needs Task 1 + Task 2/M2 + Task 3/M4) →
**Wave 4:** Task 8 (needs Task 1 + Task 4) → **Wave 5:** Task 7 (needs Task 5) → **Wave 6:** Task 6 (last).

**Task-number references are to §10's headings only.** The guard is **Task 4** (`tools/mergify_config_guard.py`);
Task 5 is *eligibility triage*. Five sites in the first draft attributed the guard to Task 5.

### 4.2 Advance now — other lanes' PRs, in dependency order (named, **not touched from here**)

| Order | PR | Why here |
|---|---|---|
| **0** | **#5597** | main's `python-ci-gate` is red on `877fa52d16`. **Bootstrap, stated explicitly:** #5597 is an *ordinary* PR whose union `main ∪ branch` is **green because it fixes the defect** (⟨C4⟩) — it satisfies entry and drains normally. **No gate bypass is needed or used.** |
| 1 | **#5384a** (`branch_protection_injection_mode: merge`) | The eligibility/entry-cost precondition (P1 before P2). #5384 is **split**: the injection-mode half here; the `batch_size` half at order 6. |
| **2** | **#5570** | Removes 33/44 conflicts — an **eligibility** lever, so it precedes the P2 cycle-shorteners (the first draft had this inverted). Note it touches `config/ci-surfaces.yml`, the same file the durations map lives in — sequence against Task 4b. |
| 3 | **#5414, #4767, #5476** | Low-risk, safety-neutral-or-positive; shorten the critical path. |
| 4 | **#5393** + **Task 4b** | Collector fix **+ the bridge** → refresh → **re-measure** (M2). |
| 5 | **#5469, #5474** | Failure attribution → unlocks E1b's latency case. |
| 6 | **#5527** | Only after **M3 + M4**. E1b (`batch_size: 4`) only after **M5** + attribution. |

### 4.3 Blocked on — named dependency

| Item | Blocked on | Unblock trigger |
|---|---|---|
| `batch_size` ↑ (E1b) | **M5** + attribution | M5 records a window with ≥2 eligible PRs that did not pair |
| `max_parallel_checks` (T-F) | **M3** + **M4** | M3 reports the effective value; M4 reports headroom; **I9 then refuses an unsafe value mechanically** |
| Auto-queue verification (E6) | green main | S1 passes |
| xdist (T-I) | one-shard isolation proof | proof lands with a leak-count assertion |
| Path-filtered legs (T-H) | **D7** | decision + a full-suite trip |
| Duration refresh (T-B) | **M2** | M2 says the split is weight-driven, not overhead |
| Declaring `fast_files_in_no_half == 0` | — | **removed**: not a criterion (T-B) |

### 4.4 Decisions needed — owner (each with a date; "trigger-only" is called out)

**D1 — #5433: the review attestation is read by NO server-side step.** *(The one genuine safety gap —
promoted out of the table.)*
- **Context:** Mergify merges server-side; `review-enforcer` / `atomic-land.sh` are local. Cutting CI
  time must not be funded by this.
- **Option A — enforce server-side.** Publish a signed check-run or PR-comment marker + a required check
  that reads it, head-binding preserved (TH3). **Cost: a project** — new publish path, new required
  check, forgery/replay design.
- **Option B — dated acceptance** that a queue merge is reviewed **by convention only**. **Cost: low.
  Residual risk: real and permanent** — nothing detects an unreviewed landing.
- **Recommendation: A, phased** — land the marker-publishing check non-required, promote once it reports
  `success` on a queue head. Do not silently take B.
- **Owner: Daniel · Decision-by 2026-10-03 · Artifact: comment on #5433.**

| # | Decision | Options & analysis | Recommendation | Owner / by / artifact |
|---|---|---|---|---|
| **D2** | `batch_size` policy | Fixed vs dynamic `{min: 1, max: 4}` | **Dynamic**, after M5 + attribution | **Daniel** / **2026-10-10** / comment on #5384 |
| **D3** | Admit `xdist`? | One-shard proof, or defer | **Admit one shard only**, `--dist loadscope` + leak assertion | **Daniel** / **2026-10-10** / comment on **#5215** |
| **D4** | Runner capacity: self-hosted vs quota | Cost > $10/mo ⇒ owner | Measure (M4), then choose **(trigger-only: gated on M4 completing)** | **Daniel** / **after M4, by 2026-10-17** / comment on #5215 |
| **D5** | Terminal conflicts #5196/#5285/#4963 | Rebase vs author adjudication | **Author adjudicates; no silent rebase** — owner resolved by Task 7's `owner_evidence` | **Daniel** / **2026-10-03** / per-PR comment (Task 7) |
| **D6** | `queue-accelerator` policy (8 PRs) | Publish a criterion, or drop the label | Publish **"unblocks ≥1 other PR"** + require the label to name what it unblocks | **Daniel** / **with #5527** / comment on #5527 **(trigger-only: gated on #5527)** |
| **D7** | Path-filtered legs | Ship with a full-suite trip, or defer | Ship with fail-closed selection + a main/nightly trip | **Daniel** / **2026-10-17** / comment on #5215 |
| **D8** | #5426 `ai-review-gate` | Required / neutral-when-unreviewed / delete | **Neutral-when-unreviewed** — a permanently-red non-required check destroys the meaning of red | **Daniel** / **2026-10-03** / comment on #5426 |
| **D9** | Required-set homes | **#3467 already decides**: `.github/settings.yml` is the one machine-readable home; the other acting mirrors are neutralized. **Its own plan is explicit that the home is stale as committed** (`contexts: [redis-guard]`; live is the six bare names — verified 2026-09-26) and that its **Open Question 3 (is the file live or vestigial?)** must be resolved *first*; its fix is a **named write step** bringing the file to the intended set **plus a consistency test** asserting **file == live read-back** — and **no such test exists today** | **Land #3467's consolidation** (do not reverse it): resolve Open Question 3, land the write step and its consistency test. **This plan does not write the file** (a contexts-only write could flip `required_pull_request_reviews` — a new merge blocker) and **I11** does not assert equality before the write exists | **Daniel** / **2026-10-10** / comment on #3467 — `retrofit_audit_required:` `redislite-process-leak.md`'s mutating instruction is still live |
| **D10** | `ci-timing.yml` outcome | Fix + reuse, or retire | **Fix and reuse** (#5393), and **add the bridge** to `config/ci-surfaces.yml:durations` (Task 4b) | **Daniel** / **with #5393** / comment on #5393 **(trigger-only: gated on #5393)** |
| **D11** | Quarantine (I7) | Adopt Test Insights quarantine with expiry + owner, or record that no quarantine exists | **Do not adopt silently.** `#5597` is a **defect fix** (⟨C4⟩), not a quarantine candidate | **Daniel** / **2026-10-10** / comment on #5215 |
| **D12** | HARD-STOP decision queue (E7) | Adjudicate, or hold | **Adjudicate** — 5 PRs may sit for weeks otherwise; needs a date, not just an owner | **Daniel** / **2026-10-03** / comment on #5215 listing the 5 |
| **D13** | Five required contexts have no push-to-main signal | Add a nightly/on-demand main check (cheap), add push triggers (costly), or record the gap | **Nightly/on-demand** — a per-push trigger would cost exactly the CI time this plan exists to cut | **Daniel** / **2026-10-10** / comment on #5215 |
| **D14** | The required-contexts live re-read (I1) has no owner and no trigger | Quarterly operational re-read by an admin, or fold it into D9/#3467's consolidation | **Quarterly, owned** — I1's freshness is its own cadence, and an unowned quarterly step is the mechanism this plan requires of every other deferred item (**cycle 7**: I1 previously had neither owner nor trigger, which is how a quarterly window becomes a permanent `UNAVAILABLE`) | **Daniel** / quarterly, first by **2026-12-31** / the `verified_at` record in `docs/ci/required-contexts.json` |

**Ownership is named, not deferred (cycle 3):** every row above names **Daniel** as the accountable human.
The first draft wrote the literal string "owner" in 11 of 13 rows, which is not an owner. **Trigger-only
rows** (D4, D6, D10) are flagged: their decision date is set by a measurement or a PR landing, not by the
calendar.

---

## 5. Measurement plan — the things nobody has measured

**Each row names a task-owner, a trigger, and the record's home.** §5's records carry a **capture timestamp**
and the **resolved `origin/main` SHA**; **the validity window is PER ROW** — **7 days** for M1/M2/M3/M5/M6,
**14 days** for M4 and M8 (a weekly cadence cannot satisfy a 7-day window; cycle 7), and I1's quarterly window
is its own. The instrument's `check baseline-fresh` is the **implementation of `--require-fresh`** — every criterion that carries that flag invokes it, so it has a live consumer (cycle 9: it was defined and consumed nowhere, the exact shape the plan removed `safety-invariants` for); a criterion without the flag performs no freshness test.

| # | Measurement | Method | Owner / trigger |
|---|---|---|---|
| **M1** | Gap decomposition: (a) effective parallel, (b) effective batch, (c) cycle incl. retries, (d) bisection time, **(e) main-push head-of-line serialization** (the epic's "tell"), **(f) `batch_max_wait_time` inflation** | instrument `--json .gap` | Task 1 / on demand + when S13 fails |
| **M2** | **Cycle-time attribution (first-class).** Split `test (a)` into collection / execution / teardown / watchdog-recovery, summing to wall time (±30 s). Cause of 38/28 is **unknown** (⟨C2⟩) | **Consume `tools/ci_timing.py` / `docs/ci-timing.json`**; fall back to the job log only if unusable — then `gh api --allow-escape-sequences` (0 bytes ≠ empty). **Abort as UNKNOWN on an empty read** | Task 2 / once per cycle-relevant change |
| **M3** | Effective `max_parallel_checks` | Derive from **timestamped queue check-run / branch create-delete events** rather than only 60 s polling (polling aliases: short-lived branches are invisible, biasing the max **downward**). Record per-sample exit status; non-zero/empty ⇒ **UNKNOWN, never 0**. If no ≥2 co-existence is observed, record UNKNOWN — **never "max observed" alone** | Task 3 / next window with ≥2 batches, else a fallback bounded to **14 days**; **M3's record window is 7 days**, matching S13's `--require-fresh` |
| **M4** | Runner capacity + headroom | Concurrent `queued`/`in_progress` runs + oldest queued age across a busy window; **plus the failed-to-start detection command**; emit `capacity.headroom` for **I9** | Task 3 / weekly; **M4's record window is 14 days** — a weekly cadence cannot satisfy a 7-day window, the mismatch I1 already avoided (cycle 7) |
| **M5** | Why batches are size 1 | Batch-arrival timestamps + queue depth at formation + the conflicted set **in the same window** — separating conflict-capping from `batch_max_wait_time` and demand starvation | Task 3 / when ≥2 eligible PRs fail to pair |
| **M6** | Speculative-invalidation waste | From M3's samples: discarded speculative batches per red (⟨C3⟩ term iii) | Task 3 / each cycle |
| **M8** | Duration-map vs observed wall time | Compare `config/ci-surfaces.yml:durations` against the latest completed run's Jobs-API per-job times, **and** the map's capture age. **`shard_imbalance_minutes` is observed wall time, not the map** (a balanced map with a 10-min observed split is the whole point) | Task 4b / weekly |

---

## 6. Out of scope — and why

| Out of scope | Why |
|---|---|
| **Bypassing the gate / dropping a required check / `--admin`** | Never an option. |
| GitHub **native merge queue** (#4798) | Org migration (one-way door) + `merge_group:` in **0 of 25** workflows ⇒ deadlock. |
| Restoring `strict: true` | Already tried; did not fix the dead queue. A singleton lock. |
| N-PR evidence aggregation | Undesigned (#3057 solved the 1-PR case). |
| **HARD-STOP surfaces** #5136, #5461, #5465, #5467, #5468 | A wrong resolution **silently disables a check**. A decision queue (D12), excluded from S15. |
| `.github/settings.yml` as the wrong home | **#3467 already decided** — this plan defers to it (D9). |
| Other lanes' PRs, and the hub checkout | This lane names owners and ordering; it does not mutate other lanes' work. |
| MCP/SDK surface · content / UX / accessibility | Untouched / no user-facing surface. |

---

## 7. Adversarial Threat Surface

Threat classes use `TH` so they cannot collide with task IDs (the first draft's `T4` meant both).

- **TH1** unreviewed change lands · **TH2** unverified change lands (a check may be *relocated*, never
  *removed*; no `check-pending`/`-check-failure`) · **TH3** attestation forged/replayed (stays
  head-bound) · **TH4** fail-open registry union · **TH5** false green from runner starvation ·
  **TH6** silent entry-gate change (`branch_protection_injection_mode` changed without re-reading the
  live required set) · **TH7** a PR weakens its own guard in the same commit.

**Out of scope:** correctness of the tests themselves; content/UX surfaces (none touched).

**Declared bound — stated per vector (cycle 7).** TH1–TH7 are the declared classes. The bound covers the
vectors **this plan's own diff can introduce or weaken and can test in-repo**; every other vector is
**outside it**, named with its reason and its filed owner — an explicit boundary, not a hidden gap. Four
classes are out of bound: **TH1** and **TH3** (I8 ABSENT — this diff neither publishes nor reads the review
attestation; owner **#5433 / D1**, decision-by 2026-10-03), **TH4(b)** (validator correctness — #5570's own
test), **TH7** (#5649, below). Out of bound is **not** "not a threat": each has a reason, an owner and a
filed artifact.

**At risk today:** **TH6** (E1 changes the entry gate now), **TH1** (I8 absent, D1 pending), **TH5**
(starvation is live: 63 queued / oldest 1 h 15 m).

**TH6 — SILENT ENTRY-GATE CHANGE: COVERED as declared (I10; mechanism redesigned cycle 10).** TH6's word is
**silent**. I10 makes it non-silent, mechanically and green today, by committing the gate's **definition** to a
digest. `docs/ci/required-contexts.json` carries `gate_digest = sha256(canonical(entry-gate projection))` over
**all** `queue_rules[*]` (names **and** full condition lists), `merge_conditions`, the effective
`branch_protection_injection_mode`, `autoqueue`/`auto_merge_conditions`, and the **emitter map**
(`context name → sorted [(workflow, job)]`, from `.github/workflows/*.yml` **and** `*.yaml`); I10 asserts
`gate_digest(head) == record.gate_digest` plus a fresh `verified_at`. **Why a digest, and not a diff
(cycle 10 — this replaced the base-ref forms):** every earlier form was defeated. A union-of-conditions
comparison let a second `queue_rule` through (cycle 8); a config-only comparison let a second `python-ci-gate`
emitter through with no config diff (cycle 8); the record was **content-free**, so a `verified_at`-only
re-stamp passed (cycle 9); and `github.event.pull_request.base.sha` is a **moving target whose value on the
Mergify queue head is main's tip at batch creation, not the author's base** — so the equality could never
hold there and would have **permanently deadlocked the queue for exactly the gate-definition PRs**
(cycle 10), including the plan's own first lever #5384a. HEAD-only has none of those modes: **any**
definition change changes the digest; a re-stamp cannot fake it; and it is computable on **every** event,
including a queue head. **I10 is NOT a live read** — a committed file is still hand-writable, so the live
comparison stays where it must (I1, the operational admin-credential step, owned as **D14**), and the
**authorship** limit is disclosed globally in §5. **Nor is it TH7** — a PR that rewrites an emitting job's
*body* to pass trivially is **#5649** (below), not TH6. `.github/settings.yml` is the declaration-home item
**I11 / D9**; I2 still exits 2 on an unrecognised mode.

**TH7 — A PR WEAKENS ITS OWN GUARD: OUT OF THE BOUND, FILED #5649 (cycle 7).** One class, two vectors: a PR
that edits `.github/workflows/python-ci.yml` can **stub the guard's implementation** *or* **delete its
invocation**, and can delete the assertion that pins either — in the same commit, with the required
`python-ci-gate` still green. Only a server-side rule could close it, and that mechanism family is
**rejected by the owner**. Filed as **#5649**, with an owner, **not claimed covered**. What the plan does
instead is raise the bar and keep the guard fixable:
- **The pin is an INVENTORY + BASE-SELF-CONSISTENCY check — it does not execute the base guard against the
  head config.** The pinned step runs the BASE guard against the **base** config and asserts the head
  guard's clause **inventory** is a superset of the base's, so a PR cannot **delete or rename** a clause.
  It deliberately does **not** run the base guard against the head config: that is exactly what would
  deadlock a clause fix (the #5192 lesson the plan itself cites when refusing to pin
  `tools/surface_manifest.py`).
- **Clause logic is therefore fixable by an ordinary PR — no bypass, no repo-wide freeze.** If a clause is
  wrong, the fix is a normal PR that edits its body (inventory unchanged), and the required step re-evaluates
  the head guard. A red clause blocks only the config change under review, and the author's two
  bypass-free exits are *fix the clause* or *revert the config change*. A clause that is red on the
  **landing** tree is caught before merge by the green-on-landing proof (Task 4 Step 1). *(This supersedes
  the cycle-6 design, in which the pin executed the base guard — a required, unfixable step that one wrong
  clause would have turned into a repo-wide merge freeze.)*
- **The base read must FETCH the base ref first, and an EMPTY base ref means HEAD — not a failure.**
  `manifest-integrity` runs unconditionally on `push:[main]` **and** `pull_request`, and `github.base_ref` is
  **empty on a push**. The step therefore mirrors `surface-guard`'s pin (`python-ci.yml:1962-2007`) exactly:
  `if [ -n "${{ github.base_ref }}" ]; then git fetch --no-tags --depth=1 origin "${{ github.base_ref }}"; base=FETCH_HEAD; else base=HEAD; fi`.
  **Empty base ref ⇒ `base=HEAD`** (the checkout *is* the protected ref, so the pin is trivially inactive);
  **a fetch failure or an unresolvable base object ⇒ non-zero exit.** (Cycle 7: reading
  "base-ref-unavailable ⇒ fail closed" literally on a push would red `manifest-integrity` — and the required
  `python-ci-gate` — on **every post-merge push**, self-inflicting the exact queue deadlock this plan exists
  to fix.)

**TH4 — FAIL-OPEN REGISTRY UNION: COVERED at this plan's boundary.** Split by vector:
- **(a) a `merge=union` landing with no *fail-propagating* validator invocation inside the gate — COVERED,
  fail-closed.** Clause (vii) is a **hard exit 1**: whenever `.gitattributes` carries `merge=union`, a
  validator invocation must run inside a job in `python-ci-gate.needs` **as a fail-propagating step** — no
  `|| true`, no `if: false`, no `--help`-only form, and no step- or job-level `continue-on-error` (cycle 7,
  adversarial finding: presence alone is satisfiable by `python tools/registry_integrity.py --help`).
  Fixtures: union + no invocation ⇒ **1**; union + `--help`-only invocation ⇒ **1**; union + fail-propagating
  invocation ⇒ **0**; `.gitattributes` absent ⇒ **0**. Today `.gitattributes` is **absent**, so the clause is
  green by construction and the moment the union lands it demands a real validator. This plan makes **no**
  `.gitattributes` change.
- **(b) the validator's own correctness (does it redden on duplicate keys) — OUT of this plan's change
  surface; #5570's own test.** `tools/registry_integrity.py` **does not exist** in the repo and is
  referenced nowhere; the union + validator is unlanded **#5570**'s content, and its fail-closed property
  lives in its own `test_validated_set_equals_unioned_set`. The plan requires only the interface clause (vii)
  depends on — a CLI the guard can invoke — and **delegates** the correctness property.

---

## 8. Integration Surface Map (`test-design`)

| Surface | Boundary | Test layer | Failure modes (≥2) |
|---|---|---|---|
| GitHub REST (`gh api`) | external, read-only | unit on fixtures + live command | partial pagination (`--paginate` = one object/page) truncates silently; a 429/5xx mid-page yields a shape-identical shorter list |
| Check-run grouping | external | unit fixtures | **duplicate job names across workflows are real** (`changes` in ci.yml+python-ci.yml; `packaging-smoke` in 3; `welcome-e2e`, `deploy`, `drill` in 2 each) ⇒ grouping by `(app, name)` hides a red; null/undocumented conclusion |
| `git merge-tree` | local subprocess | unit on a scratch repo | branch deleted mid-sweep ⇒ `UNKNOWN`, never "no conflict"; `origin/main` moves between fetch and sweep ⇒ false green |
| `.mergify.yml` | server-consumed config | **fail-closed** static guard (**Task 4**) + Mergify's own check-run | required check named nowhere ⇒ deadlock; **`queue_rules[].autoqueue` + `merge_protections_settings.auto_merge_conditions` ⇒ config rejected**; `pull_request_rules` inert |
| Branch protection required contexts | **server state** | live assertion (**operational**, admin credential) | config/live divergence; a reapply from a stale declaration home rewrites the set (**D9**) |
| **Required context → emitter** | CI workflows | static clause (**Task 4**) + live I4 + **D13** | **Cycle-3 correction (the first draft was empirically false).** Those five contexts *are* emitted by `ci.yml` (`on: pull_request:` **only**), and because a `mergify/merge-queue/*` head **is a PR**, they **do** report on a queue head — verified live on queue batch PR **#5639** (head `mergify/merge-queue/28df8c0772`): all five `success`. **No queue-head deadlock**; **I4 is satisfied today**. The real defect is narrower: no post-merge ***main*** signal ⇒ **D13 / Task 8**. **Cycle-4 correction:** Task 4 now owns an **emission clause** (§10 Task 4, clause vi) mapping every check named in either list to a job in a `pull_request`-triggered workflow, so the "static assertion" the map promises actually exists |
| `mergify/merge-queue/*` heads | server-created refs | observation (M3) + I4 | transient empty read misread as parallel=1; stale branch read as activity; a named check absent from the head |
| Runner capacity | external | observation (M4) + I9 | starvation ⇒ SIGKILL; a **never-started** job is non-green only via `check-success` semantics (I6b) |
| `.github/workflows/inbound-relay.yml` | workflow keyed on the queue contract | **Task 8 assertion** | it exempts `mergify[bot]` / `mergify/merge-queue/` heads so the relay does not close Mergify's draft batch PR (#3558). **Cycle-4 correction:** the `if:` has **two independent belts** — `github.actor != 'mergify[bot]'` **and** `startsWith(head.ref, 'mergify/merge-queue/')`; either alone exempts, so **only a change removing BOTH re-breaks it** (the earlier "a rename **or** an actor change re-breaks it" overstated the risk). Task 8 asserts both clauses |
| Review attestation path | enforcement | **none — D1 (I8)** | no server-side reader |
| **Durations map → CI selection** | artifact | static clause (Task 1 grammar / S8) | the map is well-formed but empty/under-sampled ⇒ the selector picks wrong halves of the test matrix; a capture age inferred from the file's commit date is reset by any unrelated edit (cycle 7) |
| **`main-health-nightly.yml` → `ci.yml` (`workflow_call`)** | workflow-call boundary | Task 8 assertion + mapping test | `workflow_call` does **not** inherit secrets by default; `changes` must honour the `main_health` input or its empty-diff fallback re-runs all ~20 gates nightly (cycle 7/8) |
| **`docs/ci/required-contexts.json`** | committed record (I10 / I1) | static clause (Task 4 **viii(a)**) + instrument `baseline-fresh` | a hand-written record passes with no live read (I10 removes the *silence*, not the need for **D14**'s read); a stale `verified_at`/sha passes unless `--require-fresh` is used (cycle 7) |
| **Existing pin/closure assertions** | repo tests | reuse | `tests/test_ci_selection.py::test_drift_gate_cannot_skip_the_test_matrix` (I5), `tests/test_surface_guard_pin_4606.py` (TH7) |

## 9. Verification Plan (`test-routing`)

Domain = **config + tooling** (no UI, no SQL business logic, no auth change).

| Layer | Applies | Depth |
|---|---|---|
| Unit | ✅ | polarity incl. unknown/null conclusion; **per-surface `UNKNOWN` for every check**; `main_gate` vs `mergify_mergeable` on `cancelled`/`neutral`/`skipped`; **`--strict` selects the strict polarity**; **S1 needs ≥1 observed `success`**; cross-workflow duplicate names **+ the null-`details_url` fallback (never GREEN)**; empty-vs-unknown; **→ the cycle-10 digest parses `*.yml` AND `*.yaml`; and a 200-OK `incomplete_results: true` ⇒ exit 2 for the PR-enumeration checks (never 0)**. **transport: 0-byte / non-JSON / HTML-error body ⇒ UNKNOWN**; partial pagination **unconditional**; **`collector_keys ⊆ yml_keys` + the collector-side floor (empty projection ⇒ 2)**; **conjunct aggregate `[1,0] ⇒ 1`**; **`--or-artifact` rejects a float / duplicate key / out-of-section key / date / issue-number / bare digit**; **`--min-population` catches `{total_count: 0, items: []}`**; **`--min-population` also catches a self-consistent 1-item read (`MIN_OPEN_PR_POPULATION`)**; **`--require-fresh` fails on a moved SHA and on an out-of-window `verified_at`**; **`git merge-tree` staleness (branch deleted ⇒ UNKNOWN, `origin/main` moved between fetch and sweep ⇒ 1)**; **a starved/never-started job ⇒ non-green, and `capacity --max-oldest-minutes` fires**; **I10's full-condition-set + change-set fixtures (removed `-draft` ⇒ 1; two-commit change set ⇒ 0)**; each `check <name>` exits **2** on sentinel/missing/empty and **1** on a threshold miss |
| Integration | ✅ (light) | Task 4 static clause in CI; live clause operational; `ci_selection.py --integrity` retained; **the “every S-criterion is the instrument's own exit code” claim is corrected to EXCEPT S12**, which invokes the guard directly |
| E2E / UX / Accessibility | ❌ | no user-facing surface |
| Config domain | ✅ | fail-closed static guard + Mergify's config check-run |
| Live observation | ✅ | M1–M6, M8 — and every S-criterion **except S12** is the instrument's own exit code (S12 invokes the guard directly) |

---

## 10. Tasks

### Task 1: The instrument — `tools/merge_throughput.py` (**owns the pass/fail contract**)

**Intent:** Every number here must have a reproducible command, and **every success criterion must be
able to fail**. The first draft expressed criteria as `| jq -e` pipelines; that class is vacuous —
`"UNKNOWN" >= 12` is **true** in jq, `null <= 120` is **true**, `{} | .conflicts.total != "UNKNOWN"` is
**true**, and without `pipefail` the tool's own non-zero exit is discarded. So the tool owns the
contract instead.
**Acceptance — the CLI contract is complete and self-consistent; every check an S-criterion cites is a
check this task must implement** (cycle 3: seven criteria cited checks/flags that appeared nowhere here):

```
merge_throughput.py check <name> [--min N] [--max N] [--pr N] [--min-depth N]
                                 [--min-population N] [--exclude a,b,c]
                                 [--or-artifact PATH#ANCHOR]
                                 [--strict] [--max-oldest-minutes M]
                                 [--min-headroom H] [--max-age-days D]
                                 [--require-complete] [--require-fresh]
                                 [--and <check-name> <that check's threshold flag> …]
merge_throughput.py --json [<name>|<field-path>]   # emit all fields (never a threshold judgement)
merge_throughput.py --triage [--emit rows] # never issues a mutating request
merge_throughput.py --watch-queue | --observe-capacity   # Task 3 additions
merge_throughput.py --sweep-concurrency N  # bounded merge-tree sweep (Step 3)
```
**Cycle 7:** the synopsis enumerates **every** flag the semantics table defines; `--json`'s argument is a
**check name or a dotted field path** (e.g. `.gap`).
exits **0** only on a real, numeric, threshold-satisfying value; **1** on a threshold miss; **2** when
the value is UNKNOWN/unavailable/absent — **never 0**.

| grammar element | semantics |
|---|---|
| `--strict` | **selects the STRICT polarity (`mergify_mergeable`): only `success` is green; `cancelled`/`neutral`/`skipped`/unknown ⇒ exit 2 with the token named.** Without it the lax allow-list `main_gate` applies (diagnostic only). Required by S1 |
| `--and <check-name>` | begins a **conjunct**: **the following threshold flag OF THAT CHECK (`--min`, `--max`, `--max-age-days`, …) binds to it** (cycle 7: the production admitted only `--min/--max`, so S8's `--and durations-map --max-age-days 14` was unparseable). **Grammar: `--and <check-name>`, NOT `--and check <name>`**. **Aggregate exit code is defined for any conjunct: `2` if ANY conjunct is 2, else `1` if ANY is 1, else `0`** (cycle 4: an implementation returning the *last* conjunct's code passed a mixed `[1, 0]`) |
| `--pr N` | scopes to one PR (required by `queue-entry`, `attribution`) |
| `--min-depth N` | **the queue/ETA population floor** (exit 2 below it) — the flag the grammar BINDS to `queue-eta`, used by S5 (`--min-depth 1`). Cycle 5: S5 previously used the wrong flag, leaving `--min-depth` dead. **Cycle 7: also bound to `cycle` — S7 requires ≥ 5 qualifying runs, not one**. **Cycle 9: also bound to `batch-size` (S6).** |
| `--min-population N` | **an OPTIONAL RAISE of a floor the check derives itself** (cycle 7): for the PR-enumeration criteria (`conflicts`, `no-languish`) the check requires `len(items) == total_count`, `total_count ≥ 1`, **and `total_count ≥ MIN_OPEN_PR_POPULATION`** — a committed **sanity floor of 10** (cycle 9: a value near the ~135 backlog baseline would make S9/S15 refuse exactly when the plan succeeds) — so a *self-consistent* 1-item read fails, not only an empty one (cycle 7: a purely structural ≥1 is satisfied by any mis-filtered read). `N` may only **tighten** the floor |
| `--max-oldest-minutes M` | thresholds `capacity.oldest_minutes` (S11) |
| `--min-headroom H` | thresholds the **I9** `headroom` (`capacity_at_first_failure − configured`); UNKNOWN ⇒ exit 2 (S11/S14) |
| `--max-age-days D` | the durations-map freshness bound (S8's `durations-map` conjunct): **exit 2 when the Jobs read is empty/0-byte or the projection is empty (`sampled_keys ≥ 1` required)** (a well-formed `{"jobs": []}` projection is neither missing nor 0-byte — cycle 7); **the 0.90 coverage floor is a MANIFEST-side test (Task 4b / `DURATION_COVERAGE_MIN`), never applied to the top-15 collector projection** (cycle 10: on the projection it is unsatisfiable — a `--durations=15` map can never enumerate all 654 fast files, so every legitimate refresh would exit 2); exit 1 when the map's capture age exceeds `D` or a sampled key diverges from observed per-job time beyond tolerance. **The capture age comes from a machine-readable `durations_captured_at` key written by `--refresh-durations`** — never inferred from the file's git commit date, which any unrelated edit would reset (cycle 7) |
| `--exclude a,b,c` | names **Task 5 schema keys** only (`hard_stop`, `terminal_decision`, `draft`, `superseded_by`) — an unknown key is exit **2** |
| `--or-artifact P#A` | passes on the threshold **or** a **typed INTEGER ceiling** in the anchored `#A` section (`ceiling_prs_per_day: <int>`), range-validated (`0 < v ≤ 200000`). **A float, a duplicated key, a key outside the anchored section, a date, an issue number, or a bare digit all exit 2** (cycle 5) |
| `--require-complete` | the enumerated population must reconcile to its total (no partial page) |
| `--require-fresh` | **scoped per record type (cycle 7):** for a **stored measurement record**, the test is **the record's OWN window** (`verified_at` inside it — 7 days for M3/S13, **14 days for M4/S11/S14**, quarterly for I1); for an assertion whose values are **live reads in the same invocation**, the test is **the record's own head binding** — `record.sha == live_main_sha` for a main-surface read, and **the PR's current head sha** for a PR-bound read (S2) (cycle 9: S2 cites this flag, but a PR head is never `live_main_sha`, so the single form made S2 unsatisfiable). The cycle-5 form applied the sha test to stored records, which made a weekly producer permanently red as `main` moved (a SHA has no window) |

**Checks (complete list — 18; `durations-map` added on cycle 7 to fail-close the #3395 stale-weight shape; `safety-invariants` was removed on cycle 5 — it had no consumer and S12 invokes the guard directly, so a second call path to the same clause was pure duplication):** `main-gate`, `drain-rate`, `prs-per-day`, `queue-entry`, `queue-eta`,
`batch-size`, `cycle`, `shard-balance`, `fast-files-unclassified`, `conflicts`, `attribution`, `capacity`,
`parallelism-headroom`, `gap`, `no-languish`, `baseline-fresh`, `assert-queue-head-checks`, `durations-map`.
`--json` also emits: `main_sha` + `live_main_sha` (from a **fetch**, asserted equal), **`main_gate`**
(allow-list polarity) **and `mergify_mergeable`** (`check-success`: only `success`), `max_batch_size`,
`shard_imbalance_minutes` (**observed wall time**), `queue_depth` (**Mergify check-run state, not the
label**), `fast_files_unclassified` (not "in no half"), `.gap.{value,terms{}}`, `capacity.{queued,
in_progress,oldest_minutes,headroom}`, `durations_map.{age_days,sampled_keys,tolerance}`. **`--triage` never issues a mutating request.**

**Files:** Create `tools/merge_throughput.py`; Test `tests/test_merge_throughput.py`; **Modify `config/ci-surfaces.yml`** — register the new test file, or `manifest-integrity`'s `ci_selection.py --integrity` fails and **the creating PR can never go green** (cycle 10: `integrity()` requires every `tests/**/test_*.py` to be listed in a `surfaces` entry, and it runs unconditionally on every event — a self-inflicted merge freeze on the plan's own Wave-0 PR).

**Step 1 — failing tests.** Every check gets a three-way test (0 / 1 / 2):

```python
# sentinel cannot pass
def test_check_threshold_is_not_satisfied_by_unknown():
    assert run_check("drain-rate", json={"merges_per_hour": "UNKNOWN"}, min=12) == 2
# missing key / container cannot pass
def test_check_missing_container_exits_2():
    assert run_check("conflicts", json={}, max=5) == 2
def test_check_missing_capacity_container_exits_2():
    assert run_check("capacity", json={}) == 2
# a real miss is 1, not 2
def test_check_real_miss_is_1():
    assert run_check("queue-eta", json={"max_eta_minutes": 852}, max=120) == 1
# empty is UNKNOWN, never 0 — a failed enumeration must NOT read as "no conflicts" (cycle-4 P0)
def test_empty_enumeration_is_unknown_not_zero():
    assert run_check("conflicts", json={"items": [], "total_count": 0, "read_ok": False}, max=5) == 2
def test_conflicts_requires_a_population_floor():
    assert run_check("conflicts", json={"items": [], "total_count": 0, "read_ok": True}, max=5, min_population=100) == 2
# transport: a 0-byte / non-JSON body is UNKNOWN, never a zero value
def test_zero_byte_body_is_unknown():
    assert parse_api_body(b"") is UNKNOWN
def test_html_error_body_is_unknown():
    assert parse_api_body(b"<html>502</html>") is UNKNOWN
# grouping fallback: an unresolvable workflow must NOT collapse two groups into a GREEN
def test_null_details_url_is_not_green():
    runs = [{"app":{"slug":"github-actions"},"name":"changes","status":"completed","conclusion":"failure","id":2,"details_url":None},
            {"app":{"slug":"github-actions"},"name":"changes","status":"completed","conclusion":"success","id":3,"details_url":None}]
    assert verdict_from_check_runs(runs) in ("RED", "UNKNOWN")   # never GREEN
# conjunct aggregate: [1, 0] must be non-zero (cycle 4)
def test_conjunct_aggregate_any_miss_is_nonzero():
    assert aggregate([1, 0]) == 1 and aggregate([2, 0]) == 2
# polarity: unknown and null conclusions are RED (a deny-list impl passes null, fails this)
def test_unknown_conclusion_is_red():
    assert verdict_from_check_runs([{"name":"x","status":"completed","conclusion":"weird_new"}]) == "RED"
# grouping must include the WORKFLOW (duplicate job names are real in this repo).
# DISCRIMINATING fixture: the (app, name) grouping picks the globally-newest attempt
# (id 3 = success) and returns GREEN; only (app, workflow, name) sees workflow A's red.
def test_grouping_must_not_shadow_a_red_behind_a_newer_success_in_another_workflow():
    runs = [{"app":{"slug":"github-actions"},"name":"changes","status":"completed","conclusion":"failure","id":2,"details_url":".../runs/2"},   # workflow A
            {"app":{"slug":"github-actions"},"name":"changes","status":"completed","conclusion":"success","id":3,"details_url":".../runs/3"}]   # workflow B
    assert verdict_from_check_runs(runs) == "RED"   # an (app,name) impl returns GREEN → test fails it
# main_gate and mergify_mergeable must disagree on cancelled
def test_cancelled_is_non_red_for_main_gate_but_not_mergeable():
    r = [{"name":"python-ci-gate","status":"completed","conclusion":"cancelled"}]
    assert main_gate(r) == "NON_RED" and mergify_mergeable(r) == "BLOCKED"
```

**Step 2** — `uv run pytest tests/test_merge_throughput.py -v` → FAIL.

**Step 3 — implement.** Non-negotiables: **`main_sha` and `live_main_sha` are INDEPENDENT sources** (cycle 5: one read locally after `git fetch`, one read independently from `repos/{o}/{r}/commits/main`) — otherwise the equality is true by construction and detects nothing; **a failed/partial fetch is exit 2**, with a Step-1 test; **transport invariant:** a non-2xx, **0-byte**, or non-JSON-parseable body is `UNKNOWN` (exit 2) for **every** check; exhausted retries exit 2 with the status recorded — never a partial fallback; **partial pagination is UNCONDITIONALLY non-zero** — not gated behind `--require-complete`;
**bounded** parallel `merge-tree` — the bound is a constant (`--sweep-concurrency`, default 4), is
validated against `nproc` with a hard ceiling, and has a test that N+1 refs never exceed the bound;
per-branch exit codes; **no `mergeStateStatus`/`mergeable_state`**; no
`/commits/<sha>/status`; group by `(app.slug, workflow, job name)` resolved from `details_url`, **with the fallback stated: an unresolvable `details_url` makes that group `UNKNOWN` (exit 2), never GREEN**; newest attempt by `id`; allow-list polarity (`main_gate`) **plus the strict `mergify_mergeable`**; `gh api` only; Mergify ETA from the check-run summary with a staleness bound.

**Also:** `drain-rate` is the **count of `merged:true` PRs per hour over a stated window** (REST, not check-run prose) — cycle 4: an ETA-derived rate rises with a *config* change and zero merges. **Concrete floors (cycle 5): window ≥ 24 h, minimum observed merges ≥ 5.**

**Also:** `batch-size` is derived from `mergify/merge-queue/*` **batch events within a fresh window**, never from the configured `batch_size:` value; `cycle` is the **median wall time of `python-ci-gate` runs whose HEAVY leg concludes `success`** — **the run's own conclusion is insufficient** (cycle 5: a docs-only selector-skipped run *concludes* `success` because the heavy jobs are skipped, so a 3-minute docs-only success must be excluded by the **heavy leg's** conclusion), over a fresh window of **≥ 5 runs**. A fixture whose heavy jobs are all `skipped` must not count toward the median.

**Step 4** PASS · **Step 5** live smoke + record the baseline · **Step 6** commit.

### Task 2: Cycle-time attribution — M2 (**consume the existing collector**)

**Intent:** The 38/28 split drives a claimed ~4-minute cycle win and is **unexplained** (⟨C2⟩).
**Acceptance:** the measurement doc records collection / execution / teardown / watchdog-recovery
minutes **summing to wall time (±30 s)**, citing run id, resolved SHA, and the raw **byte count** read;
states whether the split is **weight-driven or overhead-driven**; **aborts as `UNKNOWN` on an empty
read** rather than writing `teardown := 0`.
**Constraint:** consume `tools/ci_timing.py` / `docs/ci-timing.json`; a second log parser is a defect.
**Files:** Create `docs/ci/merge-throughput-measurements.md`; Modify `docs/00_index.md`. **Sequencing:** before Task 3, which modifies the doc it creates (one writer per file per wave).

### Task 3: Parallelism + capacity + batch-formation observation — M3/M4/M5/M6

**Intent:** Settle whether #5527 raises or lowers the ceiling (⟨C1⟩), and why batches are size 1.
**Acceptance:** the doc records, over ≥1 window: per-sample exit status; concurrency derived from
**timestamped queue events** (not polling alone) with a stated sampling caveat; **`capacity.headroom`**;
batch-arrival timestamps + queue depth at formation + the conflicted set **in the same window**;
speculative invalidations. A non-zero exit or empty output is **`UNKNOWN`, never 0**; "max observed"
alone is never reported as the effective value. Time-bounded fallback so T-F cannot sit blocked forever.
**Files:** Modify the measurement doc (created by Task 2 — it must land first); **no write to `tools/merge_throughput.py`** — Wave 0 gives Task 1 the full grammar, including `--watch-queue`/`--observe-capacity`. **Sequencing:** after Task 2; it also feeds Task 4b's **M4** dependency (I9's `capacity_at_first_failure` is M4, not M2 — cycle 9), so it must land before Wave 3.

### Task 4: The protection-invariant guard — `tools/mergify_config_guard.py`

**Intent:** Make §3 mechanical; reuse existing assertions instead of duplicating them.
**Interface (cycle 4: S12 cited `--static`/`--live`/`DIVERGED` which task 4 never defined — the same contract gap fixed for Task 1, relocated to the guard):**
```
mergify_config_guard.py --static    # exit 0 = all static clauses pass; 1 = a clause violated; 2 = unparseable/absent config
mergify_config_guard.py --live      # admin credential; exit 0 = I1 SATISFIED; 1 = DIVERGED; 2 = UNAVAILABLE
                                    # (I4 is the instrument's: check assert-queue-head-checks)
```
**Result tokens: `SATISFIED` / `DIVERGED` / `UNAVAILABLE`** — a fixture test per token, and `UNAVAILABLE` must be distinguishable from `DIVERGED` (S12).

**Acceptance:**
- **Static clause (CI, fail-closed), with NUMBERED sub-clauses** (E1 cites "clause (iv)"; the first draft
  never numbered them): **(i)** I2 structural well-formedness — no check named twice *within one list* **and `python-ci-gate ∈ merge_conditions`** (the conjunct I1's `∅ == ∅` escape depends on); fixture: a `merge_conditions` list lacking `python-ci-gate` ⇒ **1**;
  **(ii)** I2 mode-aware subset/disjointness, **fixtures for BOTH config states**, keyed on
  `branch_protection_injection_mode`; **(iii)** I3 — every condition whose key begins `check-` is
  `check-success`; **classification is by condition PREFIX (`check-*` / `base` / `-draft` / label / file),
  NOT by an exception list** (cycle 4: an exception list of examples false-fails a future legitimate
  non-check condition), with fixtures for the condition types the vendor documents; **(iv)** I2b — under
  `merge` injection the five cheap contexts are explicit `check-success` **entry** conditions; **(v)** the
  `autoqueue` ↔ `auto_merge_conditions` schema exclusion; **(vi)** **emission** — every check named in
  `queue_conditions`/`merge_conditions` maps to a job id in a workflow with a `pull_request` trigger
  (structural parse of `.github/workflows/*.yml`), so §8's "static assertion" exists; **(vii)** the TH4 check — **the STRUCTURAL rule, not an evasion list**: whenever `.gitattributes` carries
  `merge=union`, the validator invocation must be the step's **sole command** — a single-statement `run` (the
  parsed shell command list has length 1) with no trailing or compound operator (`;`, `&&`, `||`, `&`, `|`, a
  newline continuation), no `shell:` override, no `if:` that is false on `pull_request`, and no
  `continue-on-error` — **and it CONSUMES #5570's existing workflow-step assertion instead of restating a
  shorter list** (§3's consume-don't-re-implement rule; #5570's assertion already rejects `|| true`,
  `; exit 0`, `continue-on-error` and a `shell:` override). **Cycle 8 closed three evasions of the structural rule itself:** (a) the precondition scans **every** managed attributes file (`git ls-files -z '*.gitattributes' '.gitattributes'` plus `$GIT_DIR/info/attributes`), not only the repo root — a nested `docs/product/.gitattributes` carrying `merge=union` with the root file absent no longer escapes; (b) the **containing JOB** must have a `pull_request`-true path (an `if:` that is not `false`/schedule-only, and its `needs` must not exclude it) — a step-level-only rule is satisfied by a job gated `if: github.event_name == 'schedule'`; (c) the invocation must **name the real path/config set** (`--paths <the unioned registries>`, non-empty), never `/dev/null` — a single no-op statement satisfies the shape rule while validating nothing. Fixtures: union + no invocation ⇒ **1**;
  `--help`-only ⇒ **1**; `python … || exit 0` ⇒ **1**; a trailing `echo done` (two statements) ⇒ **1**; a
  single-statement fail-propagating invocation ⇒ **0**; `.gitattributes` absent ⇒ **0** (no union is active). This covers TH4's **presence** vector only — the validator's own
  **correctness** is #5570's `test_validated_set_equals_unioned_set`, and this plan requires only that #5570
  expose a CLI the guard can invoke. **(viii)** **I10 and I11** — **(viii)(a) I10, TH6's silence detector
  (green today; a HEAD-COMPUTED DIGEST — cycle 10):** compute `gate_digest =
sha256(canonical(entry-gate projection))` over the head, where the projection is **all** `queue_rules[*]`
(names **and** full condition lists), `merge_conditions`, the effective `branch_protection_injection_mode`
(`queue` when absent), `queue_rules[*].autoqueue`, `merge_protections_settings.auto_merge_conditions`, **and
the emitter map** (`context name → sorted [(workflow, job)]`, from `.github/workflows/*.yml` **and**
`*.yaml`). Assert **`gate_digest(head) == docs/ci/required-contexts.json.gate_digest`** and a fresh
`verified_at`; else **exit 1**. **HEAD-ONLY: no base ref, no `git diff`, no sha, no fetch, no queue-head
exception.** **Why (cycle 10):** every diff-based form was defeated — a union-of-conditions trigger let a
second `queue_rule` through (cycle 8); a config-only trigger let a second `python-ci-gate` emitter through
with no config diff (cycle 8); a content-free record let a `verified_at`-only re-stamp pass (cycle 9);
`git diff "$BASE"...HEAD` has **no merge base** in the job's depth-1 checkout (cycle 8, verified `fatal: no
merge base`); and `github.event.pull_request.base.sha` **differs on the Mergify queue head** from the value
the author committed, so the equality could never hold there — a **permanent queue deadlock for exactly the
gate-definition PRs**, including #5384a (cycle 10). A digest of the head is computable on every event,
changes on **any** definition change, and cannot be satisfied by a stale re-stamp. **Not a live read** (a
committed file is hand-writable) and **not TH7** (an emitting job's *body* is #5649). Fixtures: a second
`queue_rule` ⇒ **1**; a second emitter job for a required name (`*.yml` or `*.yaml`) ⇒ **1**; a removed
`-draft` ⇒ **1**; a `verified_at`-only re-stamp ⇒ **1**; changed definition + re-cut digest ⇒ **0**; a
`mergify/merge-queue/*` head that does not touch the gate ⇒ **0**; a push event ⇒ **computed from the pushed
commit**.
  **(viii)(b) I11 — the declaration home cannot become silently live:** assert (1) **no** workflow or test
  reads `.github/settings.yml` (verified today: a `grep` over `.github/`, `tests/`, `tools/` returns nothing
  beyond the file itself) — an in-repo reader appearing is **exit 1**, because it would make the stale
  one-context declaration authoritative; and (2) if `docs/ci/required-contexts.json` carries
  `settings_home_consistent: false` (or the key absent — **the default MUST be false/absent, stated explicitly**; cycle 10: shipping it `true` while `.github/settings.yml` still declares `[redis-guard]` would exit 1 on **every** event, including `push:[main]`, and permanently red the required `python-ci-gate`), the file's declared contexts must equal the artifact's required set,
  else **exit 1**. The full equality check therefore activates in the same change that lands **D9 / #3467**'s
  write step, and the clause is green today via (1) alone. Fixture for (1): a scratch repo in which a
  workflow references the file ⇒ **1**. **Every numbered clause carries a named fixture** (cycle 7, contract
  finding): (i) a name appearing twice within one list ⇒ **1**; (iv) `merge` mode with a cheap context
  missing from `queue_conditions` ⇒ **1**; (v) both `autoqueue` and `auto_merge_conditions` set ⇒ **1**;
  (vi) a check named in neither workflow's `pull_request` jobs ⇒ **1**. Parses `.mergify.yml` **structurally**
  (no comment-text parsing).
  **Does NOT re-implement I5** — it cites **both** existing assertions:
  `tests/test_ci_selection.py::test_drift_gate_cannot_skip_the_test_matrix` and
  `tests/test_ci_selection.py::test_required_gate_covers_the_long_legs` (the latter already asserts the
  long legs sit inside the gate's `needs:` closure).
- **Live clause (operational, NOT in the required path — and NOT run by CI):** I1, run by a human
  with an explicit admin credential (`Administration: read`), **result persisted with a timestamp** to
  `docs/ci/required-contexts.json`; `UNAVAILABLE` is recorded as non-clean, never as satisfied. A fail-closed
  admin read **inside CI would deadlock every PR** (`GITHUB_TOKEN` cannot hold `Administration: read`; no
  workflow reads protection today). **Cycle-4 correction:** the earlier draft made **Task 8's nightly CI job**
  this clause's re-verifier — an admin read inside CI, which Task 4 itself forbids. **Resolution: I1's
  freshness is a MANUAL, dated OPERATIONAL step** (quarterly, recorded in `docs/ci/required-contexts.json`
  with an explicit staleness acceptance), and Task 8's nightly covers **only** the five main signals. No
  admin-credential secret is introduced into Actions.
- **TH7 — the guard must not be weakenable by the PR it guards. OUT OF THE BOUND (filed #5649); the design
  goal here is to raise the bar WITHOUT creating an unfixable gate (cycle 7).** The pin runs the **base**
  guard against the **base** config and asserts the head guard's clause **inventory** is a superset of the
  base's — so a PR cannot delete or rename a clause, and a clause whose logic is wrong stays **fixable** by
  an ordinary PR (inventory unchanged). It deliberately does **not** run the base guard against the head
  config: that is the form that deadlocks a clause fix (`#5192`, the lesson the plan itself cites when
  refusing to pin `tools/surface_manifest.py`), and in a required job it would turn one wrong clause into a
  repo-wide merge freeze. **No base read is needed at all (cycle 10).** The cycle-7/8/9 fetch machinery is **deleted**: the inventory
assertion is taken against the **head's own** workflow tree (a self-consistency check, not a base
comparison), and the I10 digest is head-computed — so `manifest-integrity` needs neither a base fetch nor
`fetch-depth: 0` (it keeps its depth-1 checkout and its 5-minute budget), and there is no push/PR asymmetry
to special-case. The residual
  (a PR that edits the workflow to stub or delete the guard, or deletes the assertion that pins it) is
  **#5649** — filed from this lane, disclosed, and **outside the bound** (§7), not claimed closed.
**Files:** Create `tools/mergify_config_guard.py`; Create `tests/test_mergify_config_guard.py`; **Modify `config/ci-surfaces.yml`** (register the new test file — same `--integrity` freeze as Task 1; cycle 10); Create
`docs/ci/required-contexts.json`; Modify **`.github/workflows/python-ci.yml`** — `manifest-integrity` is at
`python-ci.yml:295`, **not `ci.yml`**; Modify **`tests/test_ci_selection.py`** (fixtures for the new
clauses, and the pinned `manifest-integrity` step-shape contract that constrains how the guard is invoked);
Modify **`tests/test_surface_guard_pin_4606.py`** (its `JOB` and step-name constants are hardcoded to
`surface-guard` / "Pin the gate's tooling", so the new pin cannot be covered by it unmodified).
**Rollout is a SINGLE required step + a bypass-free fix path (cycle 7).** The guard is **required from its
landing commit — no `continue-on-error`, no soak, no promotion window**, because its static clauses must be
**green on the current tree**, and a clause that is red there is a *wrong clause*, not a rollout problem.
The green-on-landing proof is a required deliverable of Task 4 Step 1: `mergify_config_guard.py --static` run
against the committed `.mergify.yml` and against the base ref's version, both `0`, via the same fixtures the
clauses carry. `continue-on-error` at the **job** level remains forbidden (it violates the pinned #2656
property in `test_drift_gate_cannot_skip_the_test_matrix`).
1. **One PR:** land the tool + its required `manifest-integrity` invocation + the inventory/self-consistency
   pin (TH7 above).
2. **A wrong clause is fixed by an ordinary PR** — edit the clause body, inventory unchanged — not by a
   bypass, and **not** by a revert PR — the earlier claim that "the still-red guard would block" the revert
   was **false** (cycle 10: Actions evaluates the workflow from the PR's merge ref, so a revert that removes
   the guard step and its pin runs the reverted `python-ci.yml` and merges). Recovery is therefore *broader*
   than the plan claimed, which matters: an operator who believes the revert is blocked will reach for
   `--admin` (forbidden by T-K) instead. The revert command is recorded in
   the PR body for the case where the *rollout* itself must be undone (TH6).
3. **If a clause is red on the current tree:** fix the clause (or file the divergence with an owner and a
   date), never soften the rollout — a fail-vain guard is how a green check stops meaning anything.
4. **Recovery is rehearsed, not asserted:** on a scratch repo, land a deliberately-wrong clause as required,
   then assert the documented fix path restores a green gate **with no bypass** (`--admin` is forbidden by
   §2.2 T-K).

### Task 4b: The durations bridge + the shard metric — T-B / M8 / I9

**Intent:** "Make the collector run" does **not** refresh the map the balancer reads (⟨C2⟩): two artifacts,
no bridge, no signal on divergence. And `shard_imbalance_minutes` must measure **observed wall time**,
not the map — otherwise it reports ≤3 min forever while the 10-minute split persists.
**Acceptance — ONE named writer, fail-closed (cycle 3: the first draft left the writer optional and made
key agreement a fail-OPEN disjunction):**
- **`tools/ci_timing.py` is the sole writer of the `durations` KEY.** The one-off 2026-09-22 manual sweep is
  **retired**: `--refresh-durations` is the only path that may emit into `config/ci-surfaces.yml:durations`,
  and `.github/workflows/ci-timing.yml` is its only scheduler. **⚠️ The FILE has a second writer**
  (`ci_selection.py::register_tests` writes it text-preservingly at `:1024`/`:1063`), so the refresh must reuse
  that **line-edit discipline — never a whole-file `yaml.safe_dump`**, which would strip the hand-curated
  comment header the sweep basis lives in (`ci_selection.py:80-84`). A test asserts a refresh preserves
  comments/unknown keys **and** still passes `--integrity`.
- **Key agreement is FAIL-CLOSED and ONE-WAY**, **with a COLLECTOR-side floor** (cycle 5: `collector_keys ⊆ yml_keys` is satisfied by **∅** for any manifest, and `duration_coverage_issues` returns `[]` on an EMPTY map — so a totally failed collector read passed both and the bridge silently no-oped). **`collector_keys ⊆ yml_keys`** (a collector key not already in the manifest exits **non-zero**) **AND `len(collector_keys) ≥ max(1, floor(0.90 · len(fast_pool())))`** — a zero-key projection is **UNKNOWN ⇒ exit 2**, never 0. **The manifest side is governed by `duration_coverage_issues` (`DURATION_COVERAGE_MIN = 0.90`)**, a hard failure inside `--integrity` (`ci_selection.py:2230`); un-sampled manifest keys are **carried forward (merge, not replace)**; a test pins that no refresh lowers coverage below 0.90 **and** that an empty collector output exits 2.
- **`tools/ci_timing.py:17`'s in-code invariant must be amended in the same change** — it currently says
  "Measurement only — never gates CI", which becomes false once it writes the weights `split_fast_gate` packs
  by (#3395: a stale weight reddened `python-ci-gate` with zero test failures).
- **Partiality is stated, not hidden:** `ci_timing.py` derives from each job's `--durations=15` section
  (`tools/ci_timing.py:9-10,271-274`), so files whose tests are all below the top-15 cutoff are **invisible**
  and red runs are truncated — a refresh is a **partial** projection, bounded by the 0.90 floor above.
- `shard-balance` is computed from the **Jobs-API per-job times** of the latest completed run (observed wall
  time, **not** the map); **I9** refuses a configured parallelism that is **`≥ capacity_at_first_failure`** (⟺ `headroom < 1`) — the same predicate as §3 I9 and S14; and
  `fast-files-unclassified` **consumes `ci_selection.py::fast_pool()`** (the declared single source of truth)
  rather than defining a third exclusion set.
**Files:** Modify `tools/ci_timing.py` (the sole writer), **`.github/workflows/ci-timing.yml`**,
`config/ci-surfaces.yml`, `tools/merge_throughput.py`, `tests/test_ci_timing.py`.
**Sequencing:** depends on Task 1's `shard-balance` check **and on M2 and M4** naming the 38/28 split as
weight-driven; it must not land before #5393 (the collector fix it consumes).

### Task 5: Eligibility triage — ordered buckets, owners, no mutation

**Intent:** Turn "~105 PRs cannot enter" into a per-PR table with an accountable owner.
**Acceptance:** one row per open PR with **exactly these schema keys** (the S-criteria may only
`--exclude` names from this set): `number, bucket, eligible?, conflict?, conflicted_paths,
superseded_by, draft?, hard_stop?, terminal_decision?, owner, owner_evidence, owning_issue`.
**`bucket` is first-match-wins** — `hard_stop > terminal_decision > dead_weight > draft > conflicting >
eligible` — because categories **overlap by construction** (the first draft's "exactly one bucket"
partition was impossible). **`hard_stop` and `terminal_decision` are DISTINCT, and both are by-design**
(`terminal_decision` = E5, awaiting D5's dated adjudication) — so both are excludable under §11 S15, which
is why they are separate keys, not one flag. Assert exactly-one *first-match* bucket, counts reconciling to the open-PR
total, and the boolean columns **independently**. **`owner` is never derived from the PR author** —
`owner_evidence` must cite session + branch + first user message (all sessions authenticate as
`daniel-ospina`), sourced from `map-sessions.py` / `collision_preflight.py`; a test asserts `owner !=
pr_author_login` unless evidence says otherwise. **`--triage` issues no mutating request** (mocked
transport).
**Files:** Modify **`tests/test_merge_throughput.py` only** — the `--triage`/`--emit rows` surface is implemented in Wave 0 (Task 1), so Task 5 is **not** a second writer of the instrument (cycle 7).

### Task 6: Record the baseline and the decisions

**Intent:** The numbers must be re-checkable; the decisions must land where they are read.
**Acceptance:** the measurement doc holds the baseline + M1–M6, M8 with capture timestamp, resolved SHA
and the **per-row** windows of §5 (7 d M1/M2/M3/M5/M6; 14 d M4/M8); **§3 is authoritative and the measurement doc is its rendering** (Task 4 asserts against
§3's clauses, so the two cannot drift — "the ledger doc" was undefined; **there is no separate ledger**);
the measurement doc **and** `docs/plans/2026-09-26-5215-merge-throughput.md` are both registered in
`docs/00_index.md`; **D1–D14 each carry owner / by / artifact**, with any trigger-only decision flagged.
**Also (cycle 4): the `--or-artifact` anchor has a producer** — Task 2 or Task 6 must write a `## ceiling`
section carrying the machine-readable `ceiling_prs_per_day: <int>` key (§11 S4's `--or-artifact`), so S4's
artifact route is not unreachable.
**Files:** Modify `docs/ci/merge-throughput-measurements.md`; Modify `docs/00_index.md`.

### Task 7: Route the triage output to owners

**Intent:** Task 5 is report-only; without delivery, the dead-weight PRs never close and the terminal
conflicts never reach a decision.
**Acceptance:** each bucket names an accountable owner from Task 5's `owner_evidence` (not "PR authors",
not "other lanes"). **Delivery is per-PR, one next action per bucket** (cycle 3: the first draft delivered
nothing to `dead_weight`/`draft` and gave `conflicting` no action): a per-bucket summary comment on
**#5215**; the `dead_weight` PRs (#5453/#5455/#5460/#5190) each get a **close-or-keep** request; the
`draft` PRs (#5463/#5464/#5466) each get a **preserve-verbatim** confirmation (they must never be queued);
a comment on each of #5196/#5285/#4963 (D5) and the five HARD-STOP PRs (D12); and the `conflicting`
bucket gets the `git merge-tree` resolution handoff. **Ownership is never inferred from the PR author.**
**Files:** none — GitHub comments only (the per-PR comment drafts are recorded as an artifact under
`docs/ci/triage/` only if a dry-run is requested).
**Sequencing:** after Task 5.

### Task 8: Main-signal for the five `pull_request`-only required contexts — D13

**Intent:** `ci.yml` is `pull_request:`-only, so `pricing-artifact`/`docs`/`test-isolation`/
`license-surface`/`legal-e2e` have **no post-merge main signal** — main's health for 5 of 6 required
contexts is unobserved, and `.main_gate` cannot be GREEN for them (this is the epic's "main's lint red
was invisible to every PR author" class).
**Acceptance:** either a **nightly/on-demand** main-health check covering the five (cheap — a per-push
trigger would cost the CI time this plan exists to cut), or a recorded decision (D13) with the gap
documented; the instrument reports `NO_MAIN_SIGNAL` distinctly from RED/GREEN for those contexts, and
`main-gate` never counts `NO_MAIN_SIGNAL` as green (cycle 7: `NO_MAIN_SIGNAL` is **reported and excluded**
from the verdict, with ≥1 observed `success` as the non-vacuity guard — see S1).
**⚙️ CYCLE-7/8 CORRECTION — the emission mechanism must be NAMED, and it must not re-run the whole
matrix.** No workflow in `.github/workflows/` declares `workflow_call` today, so a nightly job cannot
emit check-runs named `docs`/`pricing-artifact`/`test-isolation`/`license-surface`/`legal-e2e` by
calling `ci.yml`, and re-declaring those jobs would recreate the duplicate-job-name hazard §8
documents. **Decided: add `on.workflow_call` to `ci.yml` with an input `main_health: true`, and call
it from `main-health-nightly.yml`.** The five required jobs carry no `needs`/`if`, so they always run,
but **`changes` must honour the input** — under `main_health` it selects **only the five surface
jobs**, never the `git diff "$BASE...HEAD"` empty-diff fallback, which on a `schedule` selects all
~20 gates and would re-run the whole matrix nightly (cycle 7). The caller passes **`secrets: inherit`**
(the five jobs' steps need the same secrets a PR run does, and `workflow_call` does **not** inherit by
default — cycle 7). **Coverage caveat (cycle 9):** `docs`'s content checks are gated on
`steps.changed.outputs.files != ''`, and that list comes from a `pull_request.base.sha` diff that is empty on
a `schedule`, so the nightly proves the `docs` **job runs**, not that markdownlint/lychee ran. The reusable
call must set `fetch-depth: 0` **and** honour `main_health` in that gate — otherwise the board must record the
reduced coverage rather than claim "the five main signals". **And the instrument maps the called run's job ids to the five context names**,
with a test that
pins the mapping. If that path proves impossible, the fallback is D13's recorded decision — never a silent
duplication.
**⚠️ CYCLE-4 CORRECTION — scope boundary.** The earlier draft also made this workflow the re-verifier of
**I1** by "re-running the Task 4 live clause". **It must not**: that clause needs `Administration: read`,
which `GITHUB_TOKEN` cannot hold, so the job would record `UNAVAILABLE` forever and I1's freshness guard
would be dead on arrival — and introducing an admin PAT into a public repo's Actions is a security decision
this plan does not take. **I1's re-verification is a manual, dated OPERATIONAL step** (Task 4's live clause),
**not** a CI job. This workflow covers **only** the five `pull_request`-only main signals.
**Also:** an assertion that `inbound-relay.yml` still exempts **both** signals — `mergify[bot]` **and** the
`mergify/merge-queue/` head prefix (the two are independent belts; only removing both re-breaks the relay and
deques every PR — #3558).
**Files:** Create **`.github/workflows/main-health-nightly.yml`** (schedule + `workflow_dispatch` only; **no
`Administration: read`, no admin secret**); Modify **`.github/workflows/ci.yml`** (add `on.workflow_call`);
Modify `tools/merge_throughput.py` (the `NO_MAIN_SIGNAL` classification **and the job-id→context mapping**);
Modify `tests/test_merge_throughput.py`.
**Sequencing:** Task 8 depends on **Task 1** (it consumes the instrument's classification) **and on Task 4** — it must not be started before either. **Cycle 7:** the Task 4 edge is the guard/instrument polarity contract, **not** clause (vi) — this workflow is `schedule`/`workflow_dispatch`-only and so is not an emitter clause (vi) can see.

---

## 11. Measurable success criteria — exit codes, not pipelines

### 11.1 Why not `jq`
The first draft's criteria were `| jq -e` pipelines and were **vacuous**: in jq `"UNKNOWN" >= 12` is
**true** (strings order above numbers), `null <= 120` is **true**, `{} | .conflicts.total != "UNKNOWN"`
is **true** (a missing key is `null`), `jq '.gap'` exits **0** always, and without `pipefail` the tool's
non-zero exit is discarded. `"UNKNOWN"` is precisely the state these criteria exist to catch. **Each
criterion is therefore the instrument's own exit code — EXCEPT S12, which invokes the guard directly:**
`0` satisfied · `1` threshold miss · `2`
UNKNOWN/absent. A test asserts every check exits **2** on sentinel/missing input.

> **Non-vacuity rule (cycle 4, applied to every S-row below):** a criterion may not exit 0 on an **empty, sentinel, partial, or failed** read. Where a criterion counts a population, it carries `--require-complete`, and the check derives its own `total_count ≥ MIN_OPEN_PR_POPULATION` floor — an optional `--min-population` may only **raise** it (completeness alone cannot catch `0 == 0`; an unset/empty shell value cannot lower the floor). Every session-derived quantity carries `--require-fresh`.

| # | Criterion | Command (exit-code contract) |
|---|---|---|
| **S1** | main's required gate green — **read with `mergify_mergeable` semantics** (`--strict`: only `success` is green; `cancelled`/`neutral`/`skipped` ⇒ exit 2, token named), **and at least one required context must be OBSERVED `success`** (cycle 5: an all-`NO_MAIN_SIGNAL` read must not pass). **Verdict arithmetic (cycle 7):** `NO_MAIN_SIGNAL` is **REPORTED and EXCLUDED** from the green/red verdict — it is a coverage gap (D13), not a red — so the verdict is non-green only for a context that **exists** with a non-`success` conclusion; **≥1 observed `success` is the non-vacuity guard**; an absent/unread check-run for a push-triggered context is **UNKNOWN** | `python3 tools/merge_throughput.py check main-gate --strict --require-fresh` — **not `--require-complete`** (the five `NO_MAIN_SIGNAL` contexts are not a completeness failure). Fixtures (cycle 7): 1 `success` + 5 `NO_MAIN_SIGNAL` ⇒ **0**; all `NO_MAIN_SIGNAL` ⇒ **2**; a required context present with `conclusion: failure` ⇒ **1** |
| **S2** | a green PR auto-queues **unaided** | `python3 tools/merge_throughput.py check queue-entry --pr <n> --require-fresh` (reads the Mergify check-run's `Entered queue` line; the trigger verse must be `auto_merge_conditions`, not a human `@mergifyio queue`). **Cycle 8:** the check-run must be on the PR's **current head sha**, and `verified_at` within **S2's own 7-day criterion window** (S2 is not one of §5's M-rows, so no row window supplies one — cycle 9) — otherwise an old qualifying check-run satisfied a criterion about *now* |
| **S3** | drain ≥12/hr — **`drain-rate` = count of `merged:true` PRs per hour over a stated window (REST search, NOT check-run ETA)**, with a minimum observed-merge count and window length (else exit 2) and `--require-fresh` | `… check drain-rate --min 12 --require-fresh` (baseline **2.1**). **Cycle 4: an ETA-derived rate would rise with a *config* change and zero merges** — making S3 satisfiable by the very knob (T-F) the plan exists to justify. The ETA comparison is a diagnostic inside `.gap` only. **Three thresholds, distinct:** **12/hr** the fixed criterion (`2 × 5 ÷ 37 ≈ 16` discounted); **~4/hr** the falsifier floor; **200/day ≈ 8.3/hr** the epic's aspiration (S4 accepts a documented numeric ceiling instead) |
| **S4** | epic outcome: 200 PRs/day **or** a documented ceiling **with its number** (an artifact check, not a self-set boolean) | `... check prs-per-day --min 200 --or-artifact docs/ci/merge-throughput-measurements.md#ceiling` - the `#ceiling` section must carry a **typed key** `ceiling_prs_per_day: <int>`, range-validated (`0 < v ≤ 200000`); **a date, an issue number, or a bare digit all exit 2** (cycle 4: "contains a number" is prose with punctuation). **The anchor now has a producer** — Task 6 writes it (cycle 4). **Cycle 7 (vacuity): the ceiling must be a DERIVED value, not a self-set integer** — the anchored section must also carry `ceiling_source` naming `.gap.terms` keys that exist in the emitted terms, and the check requires **`ceiling_prs_per_day == round(effective_parallel × effective_batch × 1440 / cycle_minutes)` — **`cycle_minutes` in MINUTES and the tolerance stated NUMERICALLY (±10%)** (cycle 10: the cycle-8 unit bug was fixed, but the *input* unit and the tolerance were both unpinned, so a seconds-valued `cycle` could certify a wrong ceiling while the documented one failed). Cycle 9: `60/cycle` is **PRs per hour** — the cycle-7 form certified a per-hour value as the per-day ceiling, 24× understated, and rejected the *correct* one — **else 2**, plus a fresh `verified_at`; it exits **1** if the measured `prs-per-day` exceeds the stated ceiling, and **2** for a bare `ceiling_prs_per_day: 1` with no `ceiling_source` |
| **S5** | oldest queue ETA ≤2 h, over a **complete, non-empty** ETA population | `… check queue-eta --max 120 --require-complete --min-depth 1` — **cycle 5: uses the flag the grammar BINDS to `queue-eta` (`--min-depth`)** — the earlier `--min-population 1` was either ignored or an unknown flag, leaving the empty-ETA case at `0 == 0`. The 30-PR *demand* floor stays removed. An empty queue is **exit 2** |
| **S6** | batch ≥2 within a **fresh, named window** | `… check batch-size --min 2 --min-depth 1 --require-fresh` (baseline **1**). **Cycle 4:** derived from `mergify/merge-queue/*` **batch events in the window**, never from the configured `batch_size: 2` nor from any historical batch ever seen (the earlier form was an unbounded existential over history — one past batch of two made it green forever). **Cycle 7:** the window's maximum is the `--json` field `max_batch_size` (it was defined and cited nowhere). **Cycle 8:** the window must hold **≥ `--min-depth` batch events** (an empty/short window ⇒ **2**), so the criterion cannot pass on a window containing no batches |
| **S7** | cycle ≤30 min — **median wall time of `python-ci-gate` runs whose HEAVY leg concludes `success`** (cycle 5: the run's own conclusion is `success` for a docs-only selector-skipped run, so the heavy leg's conclusion — **not** the run's — is the discriminator), over a fresh window of **≥ 5 runs** | `… check cycle --max 30 --require-fresh --min-depth 5` (baseline **37**) — **cycle 7: the ≥5-run floor is bound to `--min-depth`**, not left as prose, so a 1-sample window exits **2**. A fixture whose heavy jobs are all `skipped` must **not** count toward the median. **Cycle 8: `--min-depth` counts QUALIFYING runs only** (heavy leg `success`), so a window of 5 selector-skipped runs ⇒ **2**, never 0 |
| **S8** | shard imbalance ≤3 min (**observed wall time**, not the map); zero **unclassified** fast files; the durations map is fresh and value-accurate | `… check shard-balance --max 3 --and fast-files-unclassified --max 0 --and durations-map --max-age-days 14` (cycle 5: the grammar is `--and <check-name>`; **cycle 7: `shard-balance` requires BOTH heavy legs present and `success`** — a docs-only selector-skipped run has no leg samples, so it exits **2**, never 0; and **`durations-map`** fail-closes the #3395 shape: exit 2 on a missing/0-byte Jobs read **or an empty projection** (`sampled_keys ≥ 1` is required; the **0.90 coverage floor belongs on the MANIFEST side** — Task 4b / `ci_selection.py`'s `DURATION_COVERAGE_MIN` — never on a top-15 collector projection, which can never enumerate all 654 fast files, so the cycle-7/8 form made every legitimate `--refresh-durations` exit 2: cycle 10), exit 1 on age > 14 d or a sampled key diverging from observed beyond tolerance) |
| **S9** | conflicts ≤5 **over a complete, closed-universe enumeration** | `… check conflicts --max 5 --require-complete` — **cycle 7: the floor is STRUCTURAL, not caller-supplied.** The check derives `total_count` itself and requires `len(items) == total_count`, **`total_count ≥ 1`**, **and `total_count ≥ MIN_OPEN_PR_POPULATION`** (a committed sanity floor, so a *self-consistent* 1-item read ⇒ **2**, cycle 7; **cycle 9: a 200-OK `incomplete_results: true` response ⇒ 2** — a self-consistent truncated index passes `len(items) == total_count`); `--min-population N` may only **raise** the floor, so an unset/empty shell value cannot reinstate the cycle-4 P0-2 vacuity (`{items: [], total_count: 0}` ⇒ **2**; `--min-population ''` ⇒ **2**). **The item universe is PRs** (needed for the `total_count` reconciliation); the pair count `25+8+4 = 37` is **provenance only** — “33 of 44 conflicting **PRs**”. Baseline **44 / 33** |
| **S10** | a red also red on main identified automatically, **<5 min** | `… check attribution --pr <n> --max 5 --require-fresh` — the clock is **red first-observed → attribution recorded**, both timestamps read from check-run/comment metadata by the tool (cycle 4: the "<5 min" was decorative — no `--max`, no clock). **A PR with no main red is exit 2** (not applicable), never a vacuous pass. **"Owning lane"** = the lane from Task 5's `owner_evidence` — never the PR author; a main red attributable to no PR is commented on **#5215** |
| **S11** | capacity is not the new ceiling | `… check capacity --max-oldest-minutes 120 --min-headroom 1 --require-complete --require-fresh` (baseline **63 / 12 / 75 min**). `--max-oldest-minutes` and `--min-headroom` are **defined in Task 1's grammar**; `--min-headroom` is **the I9 headroom** (`capacity_at_first_failure − configured`), UNKNOWN ⇒ exit 2. **Cycle 7:** `--require-fresh` was missing, so a 30-day-old M4 record satisfied S11 while S14 on the same headroom exited 2 — the two criteria that “all use” I9 must agree; **M4's record window is 14 days**. **Cycle 8: `--require-fresh` validates each of the three fields' own record** — one fresh M4 record cannot vouch for a stale `capacity_at_first_failure` |
| **S12** | no safety property lost - **invokes the guard directly** (the one criterion not expressed as `check <name>`; §9 is corrected to except it) | `python3 tools/mergify_config_guard.py --static` → 0, covering **every numbered clause (i)-(viii)** - including **I10's entry-gate-diff fixture** and **clause (vii)'s union-without-validator fixture**; `--live` (admin credential, operational) reports I1, **and S12 also runs the instrument's `check assert-queue-head-checks` for I4** (exit 1 on a missing name, **2** when no queue head exists); **and** a fixture proves `UNAVAILABLE` is distinguishable from `DIVERGED` |
| **S13** | the gap is decomposed and attributed | `... check gap --max 2 --require-fresh` - `.gap.value` **independently measured** (ceiling ÷ observed, not recomputed from `.gap.terms`, so the reconciliation can fail) with the six terms **each carrying a `source`**; **`.gap.terms.effective_parallel.source == "M3"`** with M3's `verified_at` (cycle 5: without provenance the config default passes, and a self-computed `value` made the “reconciliation” a tautology); **cycle 7: the term's value must be READ FROM a named persisted record (path + `verified_at`), and `--require-fresh` validates THAT record** — a `source: "M3"` string with no M3 record on disk ⇒ **2**, so attribution cannot be *silently* self-declared (cycle 9: `--require-fresh` proves a record is **fresh**, not that a human did not author it — the authorship control is **I1/D14**); tolerance stated numerically; every term numeric; UNKNOWN ⇒ exit 2 |
| **S14** | configured parallelism **below** the failure concurrency (**I9**) — **self-contained** (the caller supplies no threshold) | `… check parallelism-headroom --require-fresh --require-complete` — exits **1** when **`configured_max_parallel_checks ≥ capacity_at_first_failure`** (⟺ `headroom < 1`) and **2** when headroom is UNKNOWN (refuse, never pass). **Cycle 7:** the earlier “configured parallelism ≤ measured headroom” was a *different* predicate from I9 and could refuse a value I9 authorizes; discriminating fixture: capacity 8 / configured 5 ⇒ **0**, capacity 8 / configured 8 ⇒ **1** (cycle 4: `--max <capacity.headroom>` fed the check its own output; M4's window is 14 days) |
| **S15** | no PR languishes, **excluding by-design states** | `… check no-languish --exclude hard_stop,terminal_decision,draft,superseded_by --require-complete --require-fresh` — a row **not** in an excluded state with no queue movement **in the window**; **the `conflicting` bucket COUNTS**; `superseded_by` of `""`/`"null"` is unset-or-exit-2. **Cycle 7 (P0): the floor is STRUCTURAL** — like `conflicts`, the check derives `total_count` itself and requires `len(items) == total_count`, **`total_count ≥ 1`**, **and `total_count ≥ MIN_OPEN_PR_POPULATION`** (cycle 7: a structural ≥1 alone passes a mis-filtered 1-item read; **cycle 9: `incomplete_results: true` ⇒ 2**), with `--min-population N` only able to raise it; the earlier caller-supplied shell floor was empty-expandable to `0`, reinstating `0 == 0`. **And the non-excluded row count must be ≥ 1** (an all-`draft`/`hard_stop` classification cannot pass). So: empty read ⇒ **2**; all-excluded read ⇒ **2**; `--min-population ''` ⇒ **2**. `--require-fresh` was added on cycle 5 |

**Definition of done for this plan's own scope:** S1, S12, S13, S14 and M1–M6/M8 exist with their
commands. The rest are metrics the lever PRs are judged against.

---

## 12. Pattern Research

> **Findings date:** 2026-09-26

**Library docs (preflight)** — Mergify vendor docs fetched as the authority for config semantics:
`docs.mergify.com/merge-queue/{queue-modes,lifecycle,batches,rules,performance}`, `mergify.com/pricing`.

**Library version & API surface** — 3 framings
- *Canonical:* `mode` ∈ {serial (default), parallel, isolated}; serial = one batch **merges** at a time,
  cumulative, **but parallel checks still run**; `max_parallel_checks` default **5**; `batch_size`
  default **1**; dynamic `batch_size: {min, max}` supported.
- *Competitor variance:* GitHub's native queue allows up to ~100 parallel speculative checks and
  **removes the failing PR with no bisection**; Trunk/Mergify **bisect**. Mergify: batches of 4 ≈ 25% as
  many CI runs, at the cost of one extra bisection round.
- *Known pitfalls:* `pull_request_rules` **inert** without Workflow Automation; `autoqueue` deprecated
  and **schema-exclusive** with `merge_protections_settings.auto_merge_conditions`;
  `branch_protection_injection_mode` **decides what the entry gate is** (default `queue` re-injects every
  required context for entry, so deleting a line from `queue_conditions` is a no-op); injected conditions
  **accept neutral/skipped**; `allow_inplace_checks` deprecated/computed; `allow_queue_branch_edit`
  removed after **2026-12-31** (an externally edited batch branch then **dequeues its PRs**).
- *Plan tiers:* parallel checks are **not** plan-gated.

**Idiomatic usage patterns** — 3 framings
- *Canonical:* `batch_size` + `batch_max_wait_time` trade latency against CI cost; dynamic sizing spreads
  queued PRs across available parallel checks.
- *Competitor variance:* GitHub removes the culprit; Trunk/Mergify bisect into up to
  `max_parallel_checks` parts.
- *Known pitfall:* batching's value is a function of the red rate (⟨C3⟩).

**Library/framework pitfalls** — 3 framings
- *Canonical:* `pytest-xdist` runs one session per worker, so session-scoped fixtures execute once per
  worker.
- *Competitor variance:* per-worker DB/schema, or `--dist loadscope` to keep conflicting tests together.
- *Known pitfall:* shared DB/port collisions, module-state leaks, and **port-holding subprocesses that
  outlive the test process** — this repo leaks redislite servers by construction (T-I).

> **Bucket degradation (recorded, not silently skipped):** the initial parallel Perplexity batch
> returned **429 (rate limit)**; findings were re-obtained on serial retries. The Mergify
> competitor-variance framing is sourced from a cross-tool comparison plus the vendor's own pages.

**In-repo prior art (consume, do not re-implement)** — `tools/ci_timing.py` + `ci-timing.yml` +
`docs/ci-timing.json` (the collector; `history: []`); `config/ci-surfaces.yml:1681` `durations:`
(**688 entries**, one-off sweep, read via `ci_selection.py:51`); `scripts/admin-merge.sh:1762`
`NON_RED_CONC` (**agent-infra, via an absolute symlink — not runner-resolvable**);
`tests/test_ci_selection.py::test_drift_gate_cannot_skip_the_test_matrix` (I5);
`tests/test_surface_guard_pin_4606.py` (TH7); `.github/settings.yml` (the home **#3467 decided**);
`docs/plans/2026-09-14-3467-trunk-green-honest-gate.md` (the recorded consolidation decision, D9).

---

## 13. Execution mode

**9 dispatch units → Parallel Session** (`writing-plans` step 5: the >8 threshold). The first draft
declared "8 tasks → Subagent-Driven" while §4.1 listed nine — a miscount, corrected here. Dispatch one
fresh sub-agent per unit plus review, per §4.1's parallelism map; Task 1 must land before Tasks 2/3/4b/5,
and Task 8 depends on Tasks 1 and 4.

---

## 14. Changelog

| Date | Change |
|---|---|
| 2026-09-26 | Initial draft. |
| 2026-09-26 | **Cycle 1 (5 reviewers) — applied.** Retracted the false E2 "P0" (`manifest-integrity` **is** on the gate via `python-ci-gate.needs`); corrected the batching cost model's double-count; re-corrected ⟨C2⟩ (the `durations` map has 688 entries; the *collector* has never run; the 38/28 split is unexplained); replaced the false "exactly one list" invariant; fixed the workflow path to `python-ci.yml`; split the guard into static/live (a fail-closed admin read in CI would deadlock every PR); replaced three drifting ledgers with one authoritative invariant list; made Task 2 consume `tools/ci_timing.py`; added #5426, M6, Task 7, S13, S4, D1–D10, TH1–TH6. |
| 2026-09-26 | **Cycle 2 (5 reviewers) — applied.** **(i) The criteria were vacuous:** all `\| jq -e` pipelines passed on the `"UNKNOWN"` sentinel (`"UNKNOWN" >= 12` is true; `null <= 120` is true; a missing container passes `!= "UNKNOWN"`), `jq '.gap'` always exited 0, and no `pipefail` discarded the tool's exit — **the instrument now owns the exit-code contract** (`check <name>` → 0/1/2) with a three-way test per check. **(ii) I2 was still false** — `merge_conditions ⊆ queue_conditions` breaks under #5384a, the plan's own first lever — now **mode-aware** with fixtures for both states. **(iii) Grouping must be `(app, workflow, name)`** — duplicate job names across workflows are real here (`changes`, `packaging-smoke`, `welcome-e2e`, `deploy`, `drill`). **(iv) The rail parity test cannot run in CI** (`scripts/` is an absolute symlink, unresolvable on a runner) — reclassified as an operational check. **(v) S8 was wrong**: `test_agent_signup.py` is by design in `ENV_BROKEN_FILES` (its leg is `welcome-e2e-monitor` + `legal-e2e`) and `fast_files_absent_from_halves` is deliberately informational — the metric is now *unclassified* files, and the "safety gain" claim is dropped. **(vi) D9 silently reversed a recorded owner decision** — #3467 designated `.github/settings.yml` as the one home; the plan now defers to it. **(vii) ⟨C4⟩**: main's red is a **defect** (#5597 fixes `search_engine.py`'s distance-as-similarity and rewrites five tests — it does not "remove a test"). **(viii) Added:** the durations **bridge** (Task 4b) since no driver moves collector output into the map; **I9** (parallelism ≤ measured headroom, gated not just measured); **Task 8** + **D13** (five required contexts have no push-to-main signal); TH7 (a PR must not be able to weaken its own guard — reuse `test_surface_guard_pin_4606.py`); `mergify_mergeable` vs `main_gate` for `cancelled`; Task 5 `owner`/`owner_evidence` (ownership is never the PR author); M3 derived from timestamped events, not polling alone; §4.1 parallelism map; S11→S15 reference fixes; `draft` excluded from S15; quantified→dated decision gates (D2–D13); the scoping-doc formula and the 0.185 rounding; and the #5570 ordering inversion. |
| 2026-09-26 | **Cycle 3 (5 reviewers) — applied. No P0 recurred (the cycle-2 P0 class is closed); the findings were specification tightening.** **(i) The CLI contract was incomplete:** seven S-criteria cited checks/flags Task 1 never required (`queue-entry`, `fast-files-unclassified`, `parallelism-headroom`, `--pr`, `--min-depth`, `--exclude`, `--or-artifact`, `--and`) — Task 1's acceptance now carries the **complete 17-check list, the full grammar, and the semantics of each flag**; `--or-artifact` **requires a numeric ceiling**. **(ii) An empirical error corrected:** the claim that the five `pull_request`-only contexts "never report on a queue head ⇒ deadlock" is **false** — verified live on queue batch PR **#5639**, where all five report `success`. The real defect is only the missing post-merge **main** signal (D13/Task 8); I4 is satisfied today. **(iii) Task-count contradiction resolved:** 9 dispatch units ⇒ **Parallel Session** (§13 said 8 ⇒ Subagent-Driven while §4.1 listed nine). **(iv) The guard was attributed to Task 5 in five sites** — all now Task 4. **(v) I1 named a "scheduled job" no task produced** ⇒ Task 8 now *is* that job (nightly main-health workflow, re-running the Task 4 live clause), with explicit fail conditions; **I9's `capacity.headroom` is now defined with a unit and a fail-closed refusal**; **I3 is scoped to check-conditions** (`base=main`/`-draft` would have false-failed). **(vi) Task 4b's writer is now single and named** (`tools/ci_timing.py`, manual sweep retired, `ci-timing.yml` in Files), key agreement is **fail-closed** (the disjunctive "or one is authoritative" is deleted), and the **top-15 partiality** plus the existing `DURATION_COVERAGE_MIN = 0.90` floor (`ci_selection.py:2230`) are named as its acceptance test. **(vii) The grouping test was non-discriminating** (both groupings returned RED) — replaced with a fixture where `(app,name)` returns GREEN and only `(app,workflow,name)` returns RED. **(viii) Also:** the bounded `merge-tree` sweep now has a stated bound + test; Task 4's static clause has **numbered sub-clauses** (E1 cited "clause (iv)" which did not exist); Task 4 cites the second existing assertion (`test_required_gate_covers_the_long_legs`); **TH7 is mitigated, not closed** (#4606 residual); S1 drops `--require-complete` (would exit 2 forever with five `NO_MAIN_SIGNAL` contexts); **S3/S4/falsifier thresholds reconciled** (12/hr criterion vs ~4/hr falsifier vs 200/day aspiration); S10's "owning lane" defined with a #5215 fallback; **S15's exclude keys aligned to Task 5's schema** (`superseded_by`, and `terminal_decision` handled consistently); Task 7 delivers to `dead_weight`/`draft`/`conflicting` (not just terminal); Task 8 given a Files line and explicit sequencing; **all 13 decision rows name Daniel** (11 said the literal string "owner") with trigger-only rows flagged; `test_agent_signup.py`'s leg corrected to **`flip-gate`**; the dangling `M7` reference in the cycle-1 changelog fixed; and the scoping doc resynced (the `--integrity` warning is the by-design `ENV_BROKEN_FILES` signal, not "a real safety hole"; main's red is a **defect**, not "not a real regression"; confidence 78 → **76**; `surface-manifest.yml` → **`ci-surfaces.yml`** for durations; TH1–TH7). |
| 2026-09-26 | **Cycle 4 (5 reviewers) — NOT CLEAN; 2 fresh P0s + ~18 P1s, three of them INTRODUCED by the cycle-3 fixes. This is an ESCALATION exit (see `docs/plans/2026-09-26-5215-merge-throughput.cycle-status.yaml`), not a completion — the adversarial-domain cap (2) was exceeded at cycle 4 and the reviewers' threat-surface verdict was NOT COVERED.** **(P0-1) I1 could never pass:** `set(live contexts) == set(queue_conditions ∪ merge_conditions)` mixed Mergify non-check conditions (`base=main`, `-draft`, `check-success=` prefixes) with GitHub's bare status contexts — a permanently-red invariant whose own detector (TH6) would read as a permanent failure. Now **check-condition-only**, with a **green fixture built from the current correct `.mergify.yml`**. **(P0-2) S9 was vacuous:** `conflicts --max 5` with `summarize_conflicts([]) == 0` and no population floor meant a rate-limited/mis-filtered read — the plan's own §8 failure mode — passed exit 0, and a unit test **pinned** that vacuity; both inverted, plus a `--min-population` floor. **(P1) Three cycle-3 self-inflicted defects:** (a) **Task 8 was made I1's re-verifier, i.e. an admin read inside CI** — which Task 4 forbids, and the credential was never named; **I1's freshness is now a manual dated operational step** and Task 8 covers only the five main signals; (b) **Task 4b's bidirectional key agreement was unsatisfiable** (a `--durations=15` map can never enumerate all 688 keys) — now **one-way `collector_keys ⊆ yml_keys`** with the existing **`DURATION_COVERAGE_MIN = 0.90`** floor as the manifest-side criterion; (c) **TH7's home was wrong — `#4606` is CLOSED (2026-09-25)** and D9/#3467 does not close it; the residual was **filed as #5649** and cited. **(P1) Task 4's base-ref pin deadlocked the PR that creates the guard** (`git show base:<new file>` fails closed) — now a **two-step rollout**. **(P1) Task 4's rollout was prose with no revert** — now `continue-on-error` soak then promote, revert recorded (TH6). **(P1) Criteria made non-vacuous:** S1 now consumes the strict `mergify_mergeable` (a cancelled gate read GREEN); S3 is a `merged:true` count (an ETA-derived rate rose with the config knob, not merges); S5's 30-PR *demand* floor became a population floor (it failed exactly when the plan succeeded); S6 got a fresh window (was an unbounded historical existential); S7 a success-concluded median (a 3-min cancelled run passed); S10 a clock (`--max 5`) and exit-2 on not-applicable; S11 thresholds (had none); S13 M3-sourced `effective_parallel` + arithmetic reconciliation (the ⟨C1⟩ misreading passed it at 1.54); S14 self-contained + fresh (was circular, `headroom ≤ headroom`); S15 the `conflicting` bucket now counts and the definition matches the flags. **(P1) I9 redefined** (the old headroom was self-referential — identically 0 or unbounded). **(P1) Instrument:** transport rule (0-byte/non-JSON ⇒ UNKNOWN) promoted to all checks; partial pagination **unconditional**; `main_sha == live_main_sha` no longer satisfiable on a failed fetch; grouping fallback (null `details_url` ⇒ never GREEN); `--and` form fixed + conjunct aggregate defined. **(P1) Duplication (#5):** `fast-files-unclassified` now **consumes `fast_pool()`** (it was a third definition with a divergent exclusion set); `ci_timing.py:17`'s "never gates CI" invariant must be amended; the **rail is authoritative** and the token set gets a shared parity check; the `ci-surfaces.yml` **file** has a second (text-preserving) writer, so the refresh must not `safe_dump`. **Also:** check count 17 → **18**; `--json`/`--triage`/`--sweep-concurrency`/`--watch-queue` added to the grammar; Task 6's undefined "ledger doc" resolved + Files line; Task 7 Files line; `§4.1` map → explicit waves; `test_agent_signup.py` leg corrected to **`flip-gate`** at §2.2 (the cycle-3 changelog claimed this fix but the body was not resynced); TH4 **delegated to #5570** (its `registry_integrity.py` does not exist) with a new clause (vii); scoping doc TH7 bullet added; inbound-relay's two independent belts corrected. |
| 2026-09-26 | **Cycle 5 (3 reviewers: adversarial gate + vacuity + contract) — ESCALATION EXIT, NOT CLEAN.** The adversarial reviewer returned **ISSUES**, not `THREAT SURFACE COVERED` (TH6 not covered; TH4 detect-only; TH7 fail-vain until promotion). **A NEW P0 was found:** S7's cycle filter used the *run's* conclusion — a docs-only selector-skipped run **concludes `success`**, so a 3-minute docs-only run passed `cycle ≤ 30`; fixed to key on the **heavy leg's** conclusion with N ≥ 5. **Recurrence confirmed a self-inflicted pattern:** four sites this lane had *claimed* fixed in cycle 4 were only half-fixed — S8's `--and check` form (§11 not resynced), the `§2.2` `welcome-e2e-monitor` leg (a correction was **appended** beside the stale text instead of replacing it), the scoping-doc TH6 union form, and the scoping-doc TH7 bullet (claimed added, never added). All four now **replaced**, not appended. **Also fixed:** I1's RHS applied `.split('=',1)[1]` to bare names (`IndexError` on `"docs"`) — now `set(live required contexts)`, with I1's **own quarterly** freshness window separated from `--require-fresh`'s 7-day; I2 now exits 2 on an unrecognised `branch_protection_injection_mode`; I9's two conflicting predicates unified (`configured ≥ capacity_at_first_failure`); the grammar gained `--strict`, `--max-oldest-minutes`, `--min-headroom`, `--min-population` (concrete value = the open-PR total), and `--require-fresh` split into two tests (window **and** `sha == live_main_sha`); `--or-artifact` now requires a **typed int**, single occurrence, inside the anchored section; S1 requires ≥1 **observed** `success`; S5 uses `--min-depth` (the flag its grammar binds); S11/S13 given thresholds and provenance; Task 4b gained a **collector-side floor** (a zero-key projection no longer no-ops); Task 4's rollout promotion is now an **owned deliverable** (N=10, owner, artifact) with `continue-on-error` **step-level only** (the pinned #2656 test forbids job-level); Task 4 clause (vii) reworded to a validator **invocation** and **downgraded to detect-only**; clause (viii) added for `.github/settings.yml`; `safety-invariants` dropped (18 → **17** checks) as a duplicate call path; Task 8's Sequencing now names Task 4. **Exit reason:** the adversarial domain's **cap is 2 cycles**; this was cycle 5, and the fresh verdict was not `COVERED`, so the loop exits as an **escalation** — never reported as clean. Remaining issues are enumerated in `docs/plans/2026-09-26-5215-merge-throughput.cycle-status.yaml`. |
| 2026-09-26 | **Cycle 6 (6 reviewers) — 2 P0s + 8 P1s, all fixed.** A fresh adversarial gate returned **ISSUES, not `THREAT SURFACE COVERED`**, and found a **P0**: I10 claimed to force a live re-read of the required contexts, but a committed record is hand-writable, so it removes the *silence* of an entry-gate change, not the need for the live read. The vacuity reviewer found a **P0**: S15's floor was a caller-supplied shell value (`"$OPEN_TOTAL"`) that empty-expands to 0, reinstating `0 == 0`. The failure-mode reviewer found a **P0 introduced by cycle 6 itself**: a single required step running the base guard against the head config makes one wrong clause a repo-wide merge freeze with no bypass-free recovery. **Fixed:** I10 keyed to the **base ref's** sha and reframed as a silence detector, with the live re-read promoted to **D14** (owned, dated); S15/S9/S7 given **structural** floors (`total_count ≥ 1`, `--min-depth 5`); the TH7 pin reworked to **inventory + base-self-consistency** (clause logic stays fixable, and the base ref is **fetched** before the read, since `manifest-integrity`'s checkout is shallow); TH1/TH3/TH4(b)/TH7 declared **out of bound** with owners and reasons; **I9's predicate unified** in S14/T-G/Task 4b (they had stated a different, non-equivalent rule); **S8** given heavy-leg + `durations-map` requirements; **S11** gained `--require-fresh`; `--require-fresh` **scoped per record type**; **S1**'s `NO_MAIN_SIGNAL` arithmetic stated; **Task 8**'s emission mechanism named (`on.workflow_call`); the **wave map** given a one-writer-per-file rule; `assert-queue-head-checks` given one form; the grammar synopsis completed; and the stale "every S-criterion is the instrument's exit code" claim replaced at all four sites (the append-not-replace pattern cycle 5 identified). |
| 2026-09-26 | **Cycle 7 (6 reviewers: adversarial gate, structural, integration, failure-mode, vacuity, contract) — ISSUES, the adversarial verdict again NOT `THREAT SURFACE COVERED`; 3 P0s + ~12 P1s.** **(P0-1, two reviewers independently)** §3's I10 row still read `sha == live_main_sha` while §7 / clause (viii)(a) / the scoping doc said **the base ref's sha** — the cycle-5/6 append-not-replace defect recurring at the *authoritative* site; fixed by replacing the row. **(P0-2, adversarial)** I10's trigger compared only `branch_protection_injection_mode` + the `check-success` name set, so deleting a **non-check entry condition** (`-draft`, with `auto_merge_conditions: true` silently auto-queuing drafts) left **no record diff** — TH6 was not covered; the compared set is now the **FULL** condition list. **(P0-3, failure-mode + integration)** `manifest-integrity` also runs on `push:[main]`, where `github.base_ref` is **empty** — the literal "base-ref-unavailable ⇒ fail closed" reading would have reddened every post-merge push; the step now mirrors `surface-guard`'s pin (**empty base ref ⇒ `base=HEAD`**, fetch failure/unresolvable object ⇒ non-zero) and the granularity is the **PR change set**, not one commit. **(P1s fixed)** clause (vii) rewritten from an evasion list to a **structural sole-command rule** consuming #5570's existing assertion; `durations-map` now fail-closes an **under-sampled** projection (`sampled_keys < max(1, floor(0.90·len(fast_pool())))`), not just a 0-byte one; `--min-population` gained a committed **`MIN_OPEN_PR_POPULATION`** floor so a self-consistent 1-item read fails; S4's ceiling must be **derived** from `.gap.terms` (`== round(parallel × batch × 60 / cycle)`), not a self-set integer; Task 8's `workflow_call` now carries a **`main_health` input** (`changes` must not select all ~20 gates) plus **`secrets: inherit`**; the `--and` production binds **any** threshold flag of the conjunct (S8's `--and durations-map --max-age-days 14` was unparseable); §5's blanket 7-day window became **per-row** (7 d M1/M2/M3/M5/M6, 14 d M4/M8); I2's `python-ci-gate ∈ merge_conditions` conjunct given clause (i) + a fixture; Task 6 `D1–D13 → D1–D14`; §3 I4's owner narrowed to the instrument; S1/S2/S6/S7/S11/E2 and the header counts resynced; §8 gained the durations-bridge, `workflow_call` and `required-contexts.json` rows; §9 gained merge-tree-staleness, starved-job and I10 change-set fixtures. |
| 2026-09-26 | **Cycle 8 (4 reviewers: adversarial gate, vacuity, failure-mode, contract) — ISSUES; the adversarial verdict still NOT `THREAT SURFACE COVERED`; 3 P0s (two independent) + ~21 P1/P2s.** The adversarial and failure-mode reviewers **independently** showed **I10 was not a real detector**: a union-only condition comparison let a **second `queue_rule`** through, a config-only comparison let a **second job named `python-ci-gate`** through with **no config diff**, the record requirement was **content-free** (a `verified_at`-only re-stamp passed), and `git diff "$BASE"...HEAD` has **NO MERGE BASE** in the depth-1 `manifest-integrity` checkout (`fatal: no merge base`) — in a required job that is either a **total merge freeze** or, under the repo's `|| true` idiom, a **permanently dead detector**. The vacuity reviewer found a **P0 arithmetic bug**: S4's `round(parallel × batch × 60 / cycle)` is **PRs per HOUR**, so the reconciliation certified a 24×-understated ceiling and **rejected the correct one**. **Cycle 9 fixed the root, not the symptoms:** I10 is **scoped to what it can see — the gate's DEFINITION** (any byte of `.mergify.yml` + the emitter map, **content-matched to the head**, keyed to the **PR's own base commit**, **merge-base-safe** with `fetch-depth: 0`, **inactive on `push`**), with the **guard-absent-at-base** pass restored so Task 4's own landing PR is never blocked; clause (vii) now scans **every** managed `.gitattributes`, pins the **containing JOB's** `pull_request` path, and requires the invocation to name the **real path set**; S4's unit is **1440**; S13's provenance claim is **honest** (freshness ≠ authorship; I1/D14 is the authorship control); `incomplete_results: true ⇒ 2`; `baseline-fresh` gained a real consumer (it **is** `--require-fresh`'s implementation); `--min-depth` is bound to `batch-size`; `--require-fresh` has a **PR-head** form; `MIN_OPEN_PR_POPULATION` is a **sanity floor of 10** (a ~135 floor would refuse at the plan's own target); and the T-D cell, Task 3/4b's **M4** dependency, the `.gap.terms` map type, the header bound line (`covered=4`), Task 6's window and the scoping doc's base-ref wording were resynced. |
| 2026-09-26 | **Cycle 9 (4 reviewers: adversarial gate, vacuity, failure-mode, contract) — ISSUES; the adversarial verdict still NOT `THREAT SURFACE COVERED`; 4 P0s + ~14 P1/P2s.** The adversarial and vacuity reviewers showed the cycle-9 I10 was **still not a detector**: its pass condition compared only `required_contexts`/`emitters` to the head, so a **non-check entry condition** change (a removed `-draft`, with `auto_merge_conditions: true` auto-queuing drafts — the E4 hazard) carried a **matching** record and passed on a `verified_at` bump; the emitter map parsed `*.yml` only, so a `*.yaml` workflow could shadow a required context; and **two NEW freeze vectors** were found — (a) the plan's own creating PRs are blocked because `manifest-integrity` runs `ci_selection.py --integrity`, which fails on any `tests/**/test_*.py` not listed in `config/ci-surfaces.yml`, and no Files line registered the new test files; (b) `base_sha == github.event.pull_request.base.sha` **cannot hold on a `mergify/merge-queue/*` head** (its base is main's tip at batch creation, not the author's), so I10 would have red `python-ci-gate` **permanently on the queue for exactly the gate-definition PRs** — including the plan's own first lever #5384a. The failure-mode reviewer also showed the claimed recovery ("a revert PR would be blocked") is **false** (Actions evaluates the workflow from the PR's merge ref), and that shipping the record with `settings_home_consistent: true` would red main on every push. The vacuity reviewer found **S6 still omitted the `--min-depth` its own grammar bound**, S8's 0.90 floor was **unsatisfiable on a top-15 projection**, S4's tolerance and term units were unpinned, and `--require-fresh` was undefined for S3/S6/S7/S9/S15. The contract reviewer found the board stale, the `required-contexts.json` schema split three ways, and the scoping-doc residues. |
| 2026-09-26 | **Cycle 10 — the cycle-9 residual set fixed by REDESIGN (I10 is now a head-computed `gate_digest`), plus the two freeze fixes; re-review dispatched from this lane.** I10's entire diff/base-ref apparatus is **deleted** and replaced by `gate_digest = sha256(canonical(entry-gate projection))` over all `queue_rules[*]` (names **and** full condition lists), `merge_conditions`, the effective `branch_protection_injection_mode`, `autoqueue`/`auto_merge_conditions`, and the emitter map (`.github/workflows/*.yml` **and** `*.yaml`) — asserted `gate_digest(head) == record.gate_digest`, HEAD-only, with a fresh `verified_at`. This closes the content-free re-stamp, the second-`queue_rule`/second-emitter evasions, and the queue-head deadlock **at once**; no base fetch and no `fetch-depth: 0` is needed, so `manifest-integrity` keeps its depth-1 checkout and 5-minute budget. **Freeze fixes:** `config/ci-surfaces.yml` registration added to Task 1's and Task 4's Files lines (`--integrity` would otherwise block the plan's own Wave-0 PR), and `settings_home_consistent` is explicitly defaulted **false/absent** with the reason. Also: clause (vii) scans **every** `.gitattributes`, pins the **containing job's** `pull_request` path (a job gated off `mergify[bot]` no longer escapes), and requires the real `--paths` set; the revert-recovery claim corrected; `required-contexts.json` unified to **one** schema (`gate_digest`/`required_contexts`/`emitters`/`verified_at`/`read_sha`, `read_sha` provenance-only); S6 gains `--min-depth 1`; S8's floor becomes a **non-empty projection** with the 0.90 coverage floor moved to the manifest side; S4 pins `cycle_minutes` and a **±10%** tolerance numerically; `incomplete_results: true ⇒ 2` added to the instrument contract; `--require-fresh` given a stored-record/live-read/PR-head form set; the Wave-0 "no other task writes" claim corrected to **per wave**; Task 4b sequencing names **M2 + M4**; and the scoping doc's base-ref, conflict-arithmetic and TH6 residues resynced. |
