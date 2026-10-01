# Scoping — #5215: merge throughput bounded by CI capacity, not human attention

**Epic:** #5215 — *enable ~200 PRs/day — bound the merge rate by CI capacity, not by human attention*
**Date:** 2026-09-26
**Tier:** Complex (novel, cross-cutting: CI, queue server, git artifacts, governance)
**Status:** problem definition. **#5215 carried no `<!-- issue-scoping -->` signature** (0 in the
one comment on the issue), so this document *is* the scoping artifact for the plan at
`docs/plans/2026-09-26-5215-merge-throughput.md` (writing-plans `01-prerequisite-check.md` step 2
would otherwise halt on a missing signature; the owner directed the plan to be produced in-lane).

> **Evidence base.** `/tmp/merge-throughput-corpus.md` (measured this session) is the prior research
> and is used, not re-derived. Three of its readings are **corrected below** where live measurement or
> the vendor's own documentation contradicts them. Corrections are marked **⟨CORRECTED⟩**.

---

## Confirmed problem (root cause, verified)

> **Merge rate is limited by two *different* problems that the current framing conflates:**
> **(P1) ELIGIBILITY** — why the majority of open PRs cannot *enter* the queue at all; and
> **(P2) THROUGHPUT** — why the PRs that *are* in the queue drain at ~2/hr.

They have different root causes, different owners and different fixes. Fixing P2 while P1 is broken
buys nothing (an empty queue has no throughput to raise); fixing P1 while P2 is broken converts a
parking lot into a slow-moving parking lot. **The plan must treat them as separate workstreams with
separate success criteria.**

### P1 — Eligibility (measured)

| reading | value | source |
|---|---|---|
| open PRs | **135** | `search/issues` live |
| PRs genuinely in Mergify's queue | **30** (label `queued`; all 8 sampled show `Mergify Merge Queue = in_progress`) | live |
| PRs conflicting at merge time | **44** (of which **21** by `git merge-tree`) | corpus §1 |
| of those, conflicted on 3 shared artifacts | **33 PRs** — the shared-artifact pair counts are **ci-surfaces 25, sdk-rename-table 8, surface-manifest 4 = 37 pairs**; **37 pairs ≠ 33 PRs** (plan S9 states the split; cycle 10) | corpus §1, §8 |
| PRs auto-queued in 7 days | **0** | corpus §7 |
| labelled `queue-accelerator` (label is **inert** — #5527 unlanded) | **8** | live |

Three independent causes of ineligibility, each with a different fix:

1. **Conflicts on append-only registries** — 33 of 44. The repo has **no `.gitattributes`**
   (verified: `origin/main:.gitattributes` does not exist), so two PRs appending to the same registry
   conflict on every pair. `docs/product/sdk-rename-table.md` is **GENERATED and still tracked**
   (verified). Fix in the queue: **#5570**.
2. **The entry gate is closed for everyone while main's required gate is red.** Under Mergify's
   default `branch_protection_injection_mode: queue`, *every* required context is injected as a
   **queueing** condition — so deleting a line from `queue_conditions` is a no-op (documented in
   #5384's own patch). PR checks run against `main ∪ branch`; with `check-success=python-ci-gate` red
   on main, **no PR can satisfy entry**. Entry cost is also ~37 min (the heavy suite runs before
   entry). Fix in the queue: **#5384**.
3. **Dead weight** — PRs that can never land, each burning a 37-minute CI run: superseded
   (#5453/#5455/#5460/#5190), contract-bound draft snapshots (#5463/#5464/#5466), and terminal
   conflicts needing an author decision, not a rebase (#5196, #5285, #4963).

### P2 — Throughput (measured)

`rate = PRs_per_batch × concurrent_batches ÷ cycle_time`

| input | measured value | source |
|---|---|---|
| cycle time (real head) | `test (a)` 36.4/37.2 min; `python-ci-gate` ~36.8 min | corpus §3 |
| observed drain | **≈2.1 PRs/hr** — Mergify's own ETA: a PR entering 20:05Z is predicted to merge 2026-09-27 10:19Z (~14.2 h) with 30 queued | live, Mergify check-run |
| observed batch size | **1** (`mergify/merge-queue/*` = "Merge of #5397" 21:44Z, "Merge of #5383" 22:23Z — 39 min apart, one PR each) | live |
| PRs merged last 24 h | 26 | live (`search` `merged:>2026-09-25`) |

**⟨CORRECTED⟩ The corpus's arithmetic baseline is wrong.** Corpus §2 asserts "parallel 1 (serial)"
today and that #5527 therefore gives ~3×. Mergify's own documentation states:

- `merge_queue.mode` defaults to **`serial`** — but *serial is about merge ordering, not check
  parallelism*: "merges happen in queue order, one batch at a time, but the testing that precedes them
  does not";
- **`merge_queue.max_parallel_checks` defaults to 5** — "so Mergify tests up to five batches at once";
- `batch_size` defaults to 1; the repo sets 2.

Corroborated live: **two `mergify/merge-queue/*` branches existed concurrently** (21:44Z and 22:23Z)
and three queued PRs simultaneously hold different Mergify states (`Preparing checks` on #5527/#5476,
`Bisecting` on #5397). Parallel checks are therefore already active; the corpus read demand-starvation
as a config limit.

**Consequence — the headline number:** the config-derived ceiling is `2 × 5 ÷ 37 min ≈ **16/hr**`,
against a measured **2.1/hr**. **An ~8× gap between the configured ceiling and the observed rate is
unattributed.** That gap, not `max_parallel_checks`, is the throughput problem. Naming it is this
scoping's main contribution; measuring it is the plan's first task.

**⟨CORRECTED⟩ The teardown attribution is stale — and the shard-balance cause is *unknown*.** Corpus
§4 infers "plausibly ~15 minutes is teardown". `.github/workflows/python-ci.yml:130-134` already sets
**`TORTOISE_FAST_ATEXIT: "1"`**, described there as killing "the ~10-15 min atexit teardown tail"
(#1371). The 15-minute teardown is already mitigated; the surviving, unexplained signal is the
**10-minute `test (a)` (38 m) vs `test (b)` (28 m) imbalance**.

**Corpus §5's diagnosis of that imbalance is wrong — and so was the plan's first draft.** The shard
balancer *does* have a durations map, and it *is* used:
- `tools/ci_selection.py:51` — `MANIFEST = REPO / "config" / "ci-surfaces.yml"`; the fast split reads
  **`ci-surfaces.yml`**, not `surface-manifest.yml` (`config/surface-manifest.yml` genuinely has no
  `durations` key — true, but that is simply the file the corpus checked, and not the one used);
- `config/ci-surfaces.yml:1681` declares `durations:` with **688 entries** — populated;
- `ci_selection.py --integrity` reports **"halves consistent"** — the LPT pack *is* duration-balanced.

So the 38/28 split is **not** explained by a missing map: either those durations are stale/wrong, or
the imbalance is fixed overhead (collection, teardown, watchdog recovery), or it is load variance — a
**first-class measurement (plan M2)**, not a known fix. What corpus §5 is right about is the
*collector*: `.github/workflows/ci-timing.yml` + `tools/ci_timing.py` exist, and `docs/ci-timing.json`
is `{"schema_version": 1, "history": []}` — it has **never produced a sample**, and **#5393 is a fix
to that collector, not a new one**. The same `--integrity` run prints a `⚠️ 1 manifest fast files are in
NO half …: test_agent_signup.py` line — **and that warning is by design, not a safety hole** (cycle-3
correction: the first draft called it "a real safety hole", which is wrong). `test_agent_signup.py` sits in
`ENV_BROKEN_FILES` deliberately (it needs an environment the fast gate does not provide) and **is** executed —
in the **`flip-gate`** leg (`.github/scripts/verify-cutover` lists it) — while
`fast_files_absent_from_halves` is deliberately informational because closing it "would push 100+ files into
the fast gate and blow the watchdog budget" (`ci_selection.py:1327-1333`). The real, *unclaimed* shard
question is the **38/28 imbalance itself** (plan M2).

### Why this framing (problem-diverge → converge)

Alternative framings considered and rejected:

- **"Merging is human-driven; automate the click" (the epic's own framing).** Real, but **#5424 landed
  `auto_merge_conditions: true` and 0 of 21 PRs auto-queued** — because the *entry condition* is
  unsatisfiable while main is red. Automating the click into a closed gate removes no bottleneck.
  Rejected as the primary framing; retained as a **verification** (the experiment that settles whether
  `auto_merge_conditions` is equivalent to `autoqueue`).
- **"CI is too slow; make it faster."** Halving a 37-minute cycle at batch 1 moves 1.6/hr → 3.2/hr.
  Across 135 PRs that is still ~40 h. Real, but second-order next to eligibility. Rejected as *primary*,
  retained as a workstream.
- **"Raise parallelism."** ⟨CORRECTED⟩ Already at the default of 5. Raising it without runner capacity
  (load 49–171; **63 runs queued, 12 in progress, oldest queued 1 h 15 m**; jobs have failed to start
  because runners could not be acquired) trades a queue problem for a runner problem. Rejected as a
  first move.
- **"Batch harder."** Quantitatively conditional. A red batch costs `1 + log₂(N)` runs versus 1 for a
  single PR; at `batch_size 4` and a 5% per-PR red rate, expected runs/PR ≈ 0.343 (2.9× cheaper), but at
  a 30% red rate ≈ 0.630. **The value of batching falls as the red rate rises** — so batch size is
  sequenced behind attribution. (The plan's cycle-2 correction notes that an earlier expectation
  double-counted the initial run and read 0.39 / 0.82.)
- **"Bypass the gate / drop required checks."** **Rejected absolutely.** Never an option; recorded in the
  plan's protection ledger as a rejected item.

**Assumptions.**

| Assumption | Status | Evidence / falsifier |
|---|---|---|
| The heavy suite is the entry blocker, not the five cheap checks | **validated** | #5384 patch states the five "report `success` (never `skipped`) across every sampled PR" |
| Mergify's effective `max_parallel_checks` is ≥2 | **validated (weak)** | 2 concurrent queue branches; different ETAs for same-entry-time PRs |
| Effective parallelism is 5 | **unverified** | only ≥2 observed; **must be measured** (plan M3) |
| Conflicts halve batch formation (batches of 1) | **unverified; confounded** | 1-PR batches observed (#5397, #5383) and #5384 records two prior 1-PR batches — but `batch_max_wait_time: 5 min` and starved arrivals also produce 1-PR batches, so conflating them would mis-attribute a demand effect to a conflict cause (plan M5) |
| main's red is a **real defect** whose *expression* is embedder/config-dependent — **not** a flake and **not** "not a real regression" | **validated** | #5583 root, #5597 mechanism (rewrites five tests pinning an inverted similarity); live `test (a)` failure on `877fa52d16` |
| runner capacity binds above some concurrency | **validated** | load 49–171; jobs failed to start; oldest queued run 1 h 15 m |

**Falsification check.** This framing is wrong if, with main green and the queues cleared, the drain
rate does **not** rise above ~4/hr — then the binding constraint was never eligibility and the ~8× gap
is a pure throughput defect. The plan schedules exactly this measurement (S3/S4).

**Confidence: 76/100.** Below 100 because the gap is measured but unattributed, effective
parallelism is inferred from two observations rather than read from the dashboard, and the shard-split
cause is now known to be *unknown* rather than explained by a missing durations map.

---

## Confirmed solution direction (solution-diverge → converge)

Two workstreams, ordered by leverage, with the queue's own semantics as the architecture:

- **W1 Eligibility first** — (a) lands #5384's `branch_protection_injection_mode: merge` so entry stops
  depending on main's heavy-suite health; (b) lands #5570 to remove the append-only conflict class;
  (c) clears dead weight; (d) routes terminal conflicts to authors as decisions.
- **W2 Throughput second, measurement-led** — resolve the ~8× gap before tuning any knob; then
  rebalance shards (#5393 durations), then raise batch size **only** after flake attribution, and raise
  concurrency **only** against measured runner capacity.
- **W3 Failure cost as a throughput workstream** — a red batch costs a full cycle plus a bisection
  (#5397 is bisecting *live*). Flake attribution (#5469 verdicts, #5474 canonical diff, #5597 the
  actual red, Test Insights quarantine) is therefore throughput work, not hygiene.
- **W4 Governance, not cost-cutting** — #5426 and #5443 are red-by-construction / inert surfaces that
  train people to ignore red; #5433 is the one **genuine safety gap** (no server-side read of the review
  record) and is surfaced as an owner decision, never silently traded for speed.

Alternatives rejected (with when they *would* have been right):

| Rejected | Why | When it would be right |
|---|---|---|
| GitHub native merge queue | requires migrating the repo to an organization (one-way door retiring the `daniel-ospina/tortoise` slug) **and** `merge_group:` in **0 of 25** workflows → deadlock (#4798) | if the org migration were already decided |
| Restoring `strict: true` | already tried and did not fix the dead queue (#3016, #3558) | never for throughput; it is a singleton lock |
| N-PR evidence aggregation | undesigned (#3057 solved the 1-PR case) | after the queue is measurably healthy |
| Dropping a required check to cut entry time | eliminates safety, not CI time | never |
| Raising `batch_size` now | collapses in value as the red rate rises (above) | after flake attribution lands |
| **xdist / intra-job parallelism** | multiplies the leaked-redislite problem and adds shared-state, port-collision and module-state-leak failure modes; **`xdist` is not a dependency today** (verified) | after an isolation proof on one shard |

---

## Adversarial Threat Surface

> **Threat classes are `TH1–TH7`, not `T1–T6`** — the plan's tasks are `Task 1…8`, its levers `E*`/
> `T-A…T-K`, and its threats `TH*`. A `T`-prefixed threat list collides with the plan's task IDs, so a
> cross-reference such as "plan T4" cannot be resolved by a reader.

The plan's own subject is the merge gate, so its changes are **gate-integrity** changes. The declared
in-scope threat classes are exactly the ways a throughput change could let something land that should
not. **The bound covers the vectors this plan's own diff can introduce or weaken; where a vector is
outside it, that is stated per threat below, with the reason and the filed owner — a declared boundary,
not a hidden gap.**

- **TH1 — unreviewed change lands.** No change may remove the review path or make it optional.
- **TH2 — unverified change lands.** A check may be *relocated* (entry → merge) but never *removed*; no
  required context may be dropped; `check-success` must never become `check-pending`/`-check-failure`.
- **TH3 — attestation forged or replayed.** The head-bound review record must not become
  head-independent.
- **TH4 — fail-open registry union.** `merge=union` must redden on duplicate/conflicting keys rather than
  silently merging; a validator that cannot fail closed is a violation. **Split by vector:** (a) *a union
  landing with no validator invocation inside the gate* — **covered, fail-closed** by plan Task 4 clause
  (vii) (hard exit 1; fixture: union + no invocation ⇒ 1; `.gitattributes` is **absent** today, so the clause
  is green until a union appears — and this plan makes no `.gitattributes` change); (b) *the validator's own
  correctness* — **out of this plan's change surface**, it is #5570's own
  `test_validated_set_equals_unioned_set`, since `tools/registry_integrity.py` **does not exist** and the
  union + validator is unlanded #5570's content.
- **TH5 — false green from capacity.** Runner starvation must never read as success (a `cancelled` or
  never-started job is not a pass).
- **TH6 — silent entry-gate change.** `branch_protection_injection_mode` must not be changed without the
  live required-context set re-read and compared, **on check-conditions only**:
  `{c.split('=',1)[1] for c in queue_conditions+merge_conditions if c.startswith('check-success=')} ==
  set(live required contexts)` — `base=main`/`-draft` are not status contexts and every check entry carries
  a `check-success=` prefix. **Detectors:** plan §3 **I10** (a change to the mode, to **any non-check entry
  condition (`base=main`, `-draft`)**, or to the `check-success` name set, must carry a refreshed
  `docs/ci/required-contexts.json`'s **`gate_digest` matching the head**, else exit 1 —
  mechanical and **green today**), I2's exit 2 on an unrecognised mode, and I1's live comparison. **Cycle 7:
  I10 removes the *silence*, not the live read** — the live comparison stays I1, now **owned and dated as
  D14** (quarterly, first by 2026-12-31). **Cycle 8: the compared set is the FULL condition list** — a mode+check-name trigger alone let `-draft` be
  deleted silently (with `auto_merge_conditions: true` that auto-queues drafts, the exact state E4 forbids),
  so the detector now diffs every entry condition, and its granularity is the **PR change set**, not one
  commit. **Cycle 9: the detector is scoped to the gate's DEFINITION** — it compares **any byte of
  `.mergify.yml`** and the **emitter map** (`required context name → (workflow, job)`), because a union-only
  condition comparison let a second `queue_rule` through and a config-only comparison let a second job named
  for a required context through with no config diff; the record's **content must match the head**
  (`required_contexts` + `emitters`), so a bare `verified_at` re-stamp fails. **Cycle 10 — the mechanism is
  now a HEAD-COMPUTED DIGEST, not a diff at all:** `docs/ci/required-contexts.json` carries
  `gate_digest = sha256(canonical(entry-gate projection))` over all `queue_rules[*]` (names **and** full
  condition lists), `merge_conditions`, the effective `branch_protection_injection_mode`,
  `autoqueue`/`auto_merge_conditions`, and the emitter map (`.github/workflows/*.yml` **and** `*.yaml`); I10
  asserts `gate_digest(head) == record.gate_digest`. Every diff-based form was defeated: the record was
  content-free (a `verified_at` bump passed), `git diff "$BASE"...HEAD` has **no merge base** in the job's
  depth-1 checkout, and `github.event.pull_request.base.sha` **differs on the Mergify queue head** from the
  author's committed value — so it could never hold there, **permanently deadlocking the queue for exactly
  the gate-definition PRs (including #5384a)**. A digest of the head has none of those failure modes and is
  computable on every event. A PR that rewrites an emitting job's *body* to pass trivially is **TH7/#5649**,
  not TH6.
  The `.github/settings.yml` staleness (`contexts: [redis-guard]` against the live six) is **not** a TH6
  vector: it is the declaration-home item **I11**, owned by **D9 / #3467** (its Open Question 3, its named
  write step, its consistency test). See plan §3 I1/I10/I11 — **the plan is authoritative.**
- **TH7 — a PR weakens its own guard in the same commit.** **OUT OF THE BOUND, FILED #5649 (cycle 7).** Both
  vectors — *stubbing the guard's implementation* and *deleting its invocation from the required job* — are
  defeated by the same capability (a PR that edits `.github/workflows/python-ci.yml` can also delete the
  assertion that pins it), no in-repo test can close that, and the only mechanism that could — a server-side
  rule — is **rejected by the owner**. Disclosed, not claimed closed. **What the plan does instead** (plan
  §7, §10 Task 4): the pin is an **inventory + base-self-consistency** check (a clause cannot be deleted or
  renamed; a wrong clause stays **fixable** by an ordinary PR, so a red clause cannot freeze the repo), and
  the base read **fetches the base ref first** (the `manifest-integrity` checkout is shallow). **Cycle 10:
  the base read is DELETED entirely** — the pin is a **head-self-consistency** check against the head's own
  workflow tree, and I10's digest is head-computed, so `manifest-integrity` needs no base fetch and no
  `fetch-depth: 0` (it keeps its depth-1 checkout and 5-minute budget), and there is no push/PR asymmetry to
  special-case. **Cycle 9 (superseded): an
  EMPTY base ref means `HEAD`, not a failure** — on `push:[main]` there is no base, and the pin is trivially
  inactive; the fail-closed branch is reserved for a **fetch failure** or a **present-but-unreadable** base
  object (and, when the base has no guard file at all — Task 4's own landing PR — the inventory assertion is
  trivially satisfied). Only that narrower reading is correct; "any unavailable base ref ⇒ fail" would red
  `manifest-integrity` on every post-merge push.
- **TH1 / TH3 — out of the bound (same reason).** I8 is ABSENT: this diff neither publishes nor reads the
  review attestation, so no test here can cover forging or replaying it. Owner **#5433 / D1**,
  decision-by 2026-10-03. Out of bound is a stated boundary, not silence.

Out of scope for the adversarial bound: the correctness of the tests themselves, and content/UX
surfaces (none touched).

Every lever in the plan must answer the **protection question** — *"what does this stop protecting, and
why is that safe?"* — in a table. **An item that cannot answer is rejected, not softened.**

---

## Axis research (Phase 1.5 artifact)

**Trigger assessment:** Architecture = high (queue/CI/enforcement), Library-deps = high
(Mergify config semantics are load-bearing and were misread once already), UX = low, Ontology = low.

**Library docs preflight** — vendor documentation fetched and used as the authority for config
semantics: `docs.mergify.com/merge-queue/{queue-modes,lifecycle,batches,rules,performance}/`,
`mergify.com/pricing`.

### Axis: Mergify queue semantics (canonical / competitor-precedent / pitfalls)

- **Canonical.** `mode` ∈ {serial (default), parallel, isolated}. Serial = one batch merges at a time,
  cumulative; parallel checks still run. `max_parallel_checks` default **5**, global ceiling across
  scopes; `batch_size` default **1**; `batch_max_wait_time` — max wait for a batch to fill.
  `batch_size` may be dynamic (`{min, max}`), which spreads queued PRs across available parallel checks.
- **Competitor variance.** GitHub's native queue offers up to 100 parallel speculative checks and
  **removes the failing PR with no bisection**; Trunk and Mergify **bisect** to keep healthy PRs
  moving. Mergify's own guidance: batches of 4 ≈ 25% as many CI runs, at the cost of one extra
  bisection round.
- **Pitfalls.** (i) `pull_request_rules` is **inert** without Workflow Automation — Mergify's own
  check-run confirms it; (ii) `queue_rules[].autoqueue` is deprecated and **mutually exclusive by
  schema** with `merge_protections_settings.auto_merge_conditions` — setting both is rejected;
  (iii) `branch_protection_injection_mode` **decides what the entry gate is** — under the default
  `queue`, deleting a check from `queue_conditions` is a no-op because it is re-injected for entry;
  (iv) injected branch-protection conditions **accept neutral/skipped**, so they are weaker than an
  explicit `check-success` condition; (v) `allow_inplace_checks` is deprecated and computed — enabled
  only when `max_parallel_checks == 1` and `batch_size == 1` and CI is single-step; (vi)
  `allow_queue_branch_edit` is being removed after 2026-12-31, after which an externally edited batch
  branch **dequeues its PRs**.
- **Plan tiers.** "Every plan includes CI Insights, Test Insights, Merge Queue, and Merge Protections"
  — **parallel checks are not plan-gated**, so the default of 5 is not suppressed by billing.

### Axis: failure cost of batching (pitfalls, quantitative)

Red batch ⇒ 1 initial run + ~`log₂(N)` bisection runs. Mergify splits a failed batch into up to
`max_parallel_checks` parts. Exact expectation: `runs/PR = (1 + P·log₂ b)/b`, `P = 1−(1−p)^b`. At b=4:
`1-(1-p)⁴` ⇒ 0.185 at p=0.05 → 0.343 runs/PR; 0.760 at p=0.30 → 0.630 runs/PR. **Batching's value is a
function of the red rate** — the quantitative basis for sequencing `batch_size` behind attribution.
**(Correction:** an earlier version of this line carried `runs/PR ≈ 1/b + P(batch red)·(1+log₂ b)/b`
and the values 0.39 / 0.82, which double-counted the initial run.**)**

### Axis: intra-job parallel pytest (pitfalls) — *bucket partially degraded*

> **Bucket degradation:** the initial parallel Perplexity batch returned **429 (rate limit)**; findings
> below were re-obtained on a single retried query. Recorded rather than silently skipped.

`pytest-xdist` pitfalls: each worker collects and runs its own subset, so **session-scoped fixtures run
once per worker**; shared databases/ports collide (the remedy is a per-worker DB/schema, or `--dist
loadscope` to keep conflicting tests together); module-level state leaks and **port-holding
subprocesses that survive the test process** produce flaky tests. This repo's embedded lane leaks
redislite servers by construction, so xdist multiplies the exact resource the suite already leaks —
which is why it is admitted only behind an isolation proof.

---

## Wiring check

| Touch point | Type | Covered by | Status |
|---|---|---|---|
| `.mergify.yml` (queue semantics) | config | W1 (#5384, #5527), plan Task 4 guard | covered |
| branch protection required contexts (server state) | external state | plan Task 4 live clause | covered |
| `.gitattributes` + registry validator | repo artifact + required job | #5570 | covered |
| `config/ci-surfaces.yml` `durations:` map (**688 entries**, read via `ci_selection.py:51`) — the one artifact the balancer packs by | config | #5393 / #5050 + plan **Task 4b** (the missing bridge; no driver moves collector output into it) | covered |
| `.github/workflows/python-ci.yml` | CI config | #5414, #4767, plan M2 | covered |
| `tools/ci_selection.py` | tooling | #5393 | covered |
| review attestation path | enforcement | #5433 (owner decision) | **gap → decision** |
| runner capacity | external capacity | plan M4, epic task 3 | covered |
| measurement instrument | tooling | plan Task 1 | covered |

---

## Complexity

| Domain | Rating |
|---|---|
| Architecture | high |
| Library-deps | high |
| Ontology | low |
| UX | low |
| Accessibility | low |
| Reversibility | medium — queue config is revertible; `merge=union` is one-time delete-vs-modify cost |

<!-- issue-scoping: v5.1 double diamond + verify — authored in-lane for #5215 (no prior signature) -->
