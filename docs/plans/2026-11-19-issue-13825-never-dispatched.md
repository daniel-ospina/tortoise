<!-- research-path: https://github.com/daniel-ospina/tortoise/issues/13825#issuecomment-7017382356 -->
<!-- plan-review: cycles=3, status=threat-surface-covered, version=2.3.0 -->
# #13825 — a never-dispatched queued run is not a waiter (v2, after PLAN-VERIFY round 1)

**Issue:** tortoise#13825 · **Branch:** `fix/13825-never-dispatched` · **Base:** `38802821cf`
**Tier:** complex · **Class:** gate-enforcement (an alarm's exclusion rule)

> **v2 changes** (each traceable to a verified round-1 finding; two findings were **falsified** and are
> recorded as such in §9): a four-condition proof gate (§3.2), the re-run hole (§3.3), state-file
> validation (§3.4), the corrected budget arithmetic + a wall-clock deadline (§3.5), the `pr_queued=0`
> override (§3.6), `global_max` over live runs (§3.7), two new reported complements (§3.8), the
> line-pin re-anchor (§5 step 10), and (b)(4) answered (§3.9).

## 1. Confirmed problem

`tools/ci_queue_health.py` treats every `status=queued` PR-event run as a waiter. **128 of the 163
queued runs on this repo (created 2026-11-05/11) hold ZERO job records** — GitHub accepted them and
never dispatched them. Measured at the head this plan is written on:

```
CI: PR-BACKLOGGED(queued=165, pr_running=4, pr_queued=155, pr_pending=10, oldest=1215486s)
congests_pr=True
```

`oldest=1215486s` is a 14-day zero-job run; the oldest **live** waiter is ≈4 886 s, under the 5 400 s
budget. So the alarm is permanently on, and a real starvation is indistinguishable from the residue.

**Falsifier (one):** a genuinely dispatched run that reads `total_count: 0` and later produces jobs
**without `updated_at` moving** would make any exclusion suppress a real waiter. **Measured twice,
independently:** `updated_at` moves on job creation *often but not reliably* — 27 of 164 queued runs
have `updated_at > created_at` (one of them `+15 s` with `total_count: 6`), **and** three have
`updated_at == created_at` **with `total_count: 1`**. So the stamp is corroborating evidence, **never**
a sufficient guard, and it may not be the primary one. This is why §3.2 gates the proof on four
independent conditions and keeps a TTL.

**Confidence:** high on the defect and the direction; the residual risks are named, bounded and each
has an arm (§7).

## 2. Alternatives considered

| # | Alternative | Why not |
|---|---|---|
| A | **Stillness** as the discriminator | Falsified in the previous pass: `tests/test_ci_queue_health.py:46-59` models a legitimate frozen waiter; 15 arms red. |
| B | `pull_requests` empty | Falsified: 8 of 30 live `pull_request` runs are also empty. |
| C | **Direct proof: zero job records**, gated, bounded, persisted | **CHOSEN** — the only positive evidence. |
| D | Probe every queued run every pass | ≈18 000 requests/day for the same answer. |
| E | A seventh state token | `verdict` drives exit semantics (`ci-queue-health.yml:236-241`) and `ci_check_repair`'s gate; a **reported field** changes less and says more. |

## 3. Design

### 3.1 The read

`read_run_job_count(repo, run_id)` → `int | None` from
`/actions/runs/{id}/jobs?filter=latest&per_page=1`, returning `total_count` **only when
`type(total) is int`**. Absent / non-int / failed read / malformed payload → `None` = UNPROVEN.
`per_page=1` because `total_count` is the whole quantity and the module already treats an int
`total_count` as authoritative (`:2246-2253`); the payload stays one row wide.

### 3.2 ⛔ FOUR conditions before `0` may prove anything

A `total_count == 0` is accepted as a never-dispatched proof **only when all four hold**; otherwise the
run stays `never_dispatched=None`, i.e. **a WAITER**:

1. `run.run_attempt == 1` (from the projection) — see §3.3;
2. `run.updated_at == run.created_at` — the run object never moved after creation;
3. `_settled_still(run.updated_at, now)` — **reusing `JOB_SETTLE_SECONDS` (300 s, `:499`,`:1222`)**,
   so the proof cannot be taken inside the window in which a dispatched run's job record has not landed
   yet (measured latency 0–15 s; the constant's own rationale is "far above any matrix expansion,
   far below the 7-day staleness of the frozen runs");
4. the persisted/returned count is the int `0`.

Condition 3 is the one round 1 called missing, and it is what makes "probe a 0.5 s-old run" impossible.

### 3.3 The re-run hole

`filter=latest` scopes the jobs view to the **latest attempt**. A queued re-run (`run_attempt ≥ 2`)
whose attempt-2 jobs have not landed therefore reads `total_count: 0` — the module's own pinned
payload (`read_run_live_job`'s `latest` note). Taking that as "never dispatched" would suppress a
genuine waiter that `ci_check_repair` itself created. Guards: condition 1 above, plus condition 2 (a
re-run updates the run object, so a re-run's `updated_at` should move).

⛔ **Honest status of each guard.** Condition 1 is the primary and its residual is *named*: `_project`
coerces a missing/malformed `run_attempt` to `1` (`:1295`), so an API payload that omitted the field
would project a re-run as attempt 1 — that residual is R11. Condition 2 closes it *if* a re-run moves
`updated_at`, which is **UNMEASURED** (no queued run in today's population carries `run_attempt > 1`);
it is therefore carried as a **conservative additional** condition, never as the attempt guard, and its
cost is stated: a run that moved once at creation and then froze (2 of the 129 zombies measured in a
previous pass) can never be proved, so it stays counted as a waiter — the fail-closed direction.

### 3.4 The persisted proof — validation is a security boundary, not hygiene

A proof entry is honoured only when **all** hold; anything else **drops the entry** (⇒ waiter):

- `entry["never_dispatched"] is True` — **identity, not truthiness** (`1`, `"true"`, `"false"`, `[]`
  must all be rejected; a truthiness test is the fail-open shape this repo has already been bitten by);
- `type(entry["run_id"]) is int`;
- `entry["proved_at"]` and `entry["updated_at"]` both parse via `_parse_time` (`:1215`);
- `0 <= now - proved_at < NEVER_DISPATCHED_REPROOF_SECONDS` — a **future** `proved_at` is rejected
  outright, because `now - proved_at < TTL` is satisfied for ever by any future stamp (the
  skill-enforcer's `READ_PERSIST_FUTURE_SKEW_MS` rule, and `_settled_still`'s own future-stamp note);
- the run is **still in the queued feed**, and its **`updated_at` is unchanged** (a moved stamp ⇒
  re-prove now).

Pruning to the feed happens **on load and on save**. Writes are atomic (`tmp` + `os.replace`) so a
killed pass cannot leave a half file. Two writers exist (`ci-queue-health` and
`ci_check_repair.read_queue_health`); a stale snapshot can only *resurrect* a proof, which the TTL and
the validation above bound — stated, not hand-waved (R2).

**Why a TTL at all, given four conditions?** Because the measurement says the stamp is not a reliable
signal, and a proof with no expiry would be immortal. The TTL is the **second, independent** bound; it
is not the primary guard any more (round 1 was right that "6 h > the 90 min budget" is too loose on its
own).

### 3.5 Budget — a count **and** a wall clock (round-1 arithmetic corrected)

The v1 derivation was wrong by 2× because `main()` takes a **second** reading when it acts — the
module's own comment (`:435-439`) states `2 × 8 × 15 s = 240 s of a 600 s job budget`. Corrected:

- `DEFAULT_MAX_QUEUE_PROBES = 24` (count cap; converges 128 proofs in ~6 passes),
- `QUEUE_PROBE_DEADLINE_SECONDS = 90` (**wall clock** cap on the queued probe phase in one reading),
- worst case `2 × (8×15 + 90) = 2 × 210 = 420 s` of the 600 s job, leaving 180 s for the four feed
  legs and checkout/setup. The **deadline**, not the count, is what makes the arithmetic hold.
- Probes are spent **oldest-first by `created_at`** (an unparseable stamp sorts last). ⛔ This key is
  about the run's **lifetime**, not its wait age, and is deliberately **not** `wait_age_seconds`
  (`:1464` forbids `created_at` for a wait): the question here is "has this run existed long without
  ever being dispatched", and the key is used by no age clause.
- A run the budget/deadline never reaches stays `None` ⇒ **waiter**, and is counted in the new
  `pr_never_dispatched_unproven` complement so "budget-starved" is distinguishable from "measured
  clear" (the asymmetry the module already refuses for `pr_running_unmeasured`).
- The second consumer (`ci_check_repair.read_queue_health`, a 15-min job) inherits both caps, adding
  ≤90 s to a budget the module already flags as unaccounted for (`:476-479`) — named in R8.

### 3.6 Where the exclusion is applied, and the one override it must fix

One join point: `wait_runs` (`:1430`). Everything downstream (`ages`, `oldest_pr_age`,
`wait_witness_ids`, `starved_wait`, `backlogged_wait`, `pr_wait_measured`, `congests_pr`) derives from
it.

⛔ **`pr_queued` must stop being hard-coded to 0 in the "nothing was waiting" seam.** `:1962` is
`return health(OK, pr_queued=0, oldest_pr_queued_age_seconds=None, pr_wait_measured=False)`. Today that
override is harmless (the clause is `if not wait_runs:` and, without an exclusion, `pr_queued_runs` is
then empty too). With the exclusion it becomes **false**: 128 PR-event runs queued and `pr_queued=0`.
Changed to `pr_queued=len(pr_queued_runs)`, which is **byte-identical for every pre-existing reading**
(the override's value was already 0 in every case that reaches it) and honest in the new one.

`pr_queued` and `queued_total` otherwise keep their present meaning (feed truth). The new
`pr_never_dispatched` + `never_dispatched_ids` are additive, and the state line gains
`, never_dispatched=N` (and `, never_dispatched_unproven=M`) **only when non-zero**, so every existing
fixture renders byte-identically.

### 3.7 `global_max` is computed over LIVE runs

`global_tally` (`:1448`) currently counts every queued run, so a residue of 128 never-dispatched PR runs
pins `global_max=128` and the FLOOD clause's `offender_queued >= global_max` comparison can never be
satisfied — FLOOD becomes unreachable on exactly the queue this issue is about. It is computed over
**live** (non-excluded) queued runs. `offender`/`non_pr_tally` are unaffected (zombies are PR-event and
non-PR runs are never probed). FLOOD could not fire before on such a queue and can now; that is a
deliberate, stated behaviour change (R13), and it only reaches a `TRIM_SAFE` workflow's excess.

### 3.8 Two reported complements, no silent asymmetry

- `pr_never_dispatched` — PROVEN, i.e. a **lower bound** that says so;
- `pr_never_dispatched_unproven` — eligible-but-not-proven (budget, deadline, or an abstaining read).

### 3.9 Decision (b)(2) and (b)(4) from the issue

**(b)(2) — a queue of ONLY never-dispatched runs reads `OK`**, with `never_dispatched=N` on the line,
`pr_wait_measured=False` and `congests_pr=None`. The `OK` is a claim about the **live** population
(nothing is waiting), which is true; the residue has no lever from our side (`/cancel` answers "Cannot
cancel a workflow run that is completed" about a run that reads `queued`), and an always-on red for an
unservable population is this same defect one layer up. It is not silent (the count is in the string
the fleet samples) and a destructive gate still refuses (`congests_pr is None`).

**(b)(4) — `pr_wait_measured` narrows, and the narrowing is stated + pinned.** It used to certify "every
queued/pending PR-event run was READ"; it now certifies "every **live** one was read", where live
excludes proven-never-dispatched runs. The identity `len(wait_runs) == pr_queued + pr_pending -
pr_never_dispatched` is asserted as an arm, so the field cannot drift from the population it certifies.
`pr_wait_budget_seconds` is unchanged.

## 4. Integration surface map

| Surface | Layer | Verification |
|---|---|---|
| `classify_queue` (pure) | unit | exclusion at `wait_runs`; `oldest`/witnesses/clauses/`global_max` move together |
| `read_health` (probe + state) | unit, `gh` monkeypatched | four conditions, count+deadline caps, cache write/validate/prune |
| state file | unit, hermetic tmp | corrupt / wrong-type / future-dated / foreign / left-feed all drop the entry |
| `--json` / `state_line` | contract | new keys present, none removed, zombie-free readings byte-identical |
| `ci-queue-health.yml` | contract | cache key **and** `restore-keys`; annotation names the counts |
| `ci_check_repair` consumer | contract | `congests_pr` False/None path traced; `queue_congestion_gate` still refuses on `None` |
| line pins (`ci_check_repair.py:195`, `:225`, `tests/test_ci_check_repair.py:1863`) | static | re-anchored + the pin's own test run |

## 5. Implementation steps

1. Vocabulary paragraph in the module docstring's state table, correcting "Waiting WILL clear it" to
   name its population (the `QUEUED` entry is `:28-29`).
2. `QueuedRun.never_dispatched: bool | None = None` — `True` proven, `None` unproven **and counted as
   waiting**; the three-valued doc states the direction of abstention.
3. `read_run_job_count()` (`type(total) is int`).
4. State store: load (validate identity/stamps/future/TTL/feed/`updated_at`), save (atomic, prune).
5. `read_health(max_queue_probes=24, queue_probe_deadline=90, state_file=…)`: eligibility (all four
   conditions) → oldest-first by `created_at` → probe under both caps → persist.
6. `classify_queue`: exclusion at `wait_runs`; `pr_never_dispatched` (+`_unproven`), `never_dispatched_ids`;
   `global_max` over live runs; the `:1962` override fix; reason line; `state_line`; `to_dict`.
7. CLI: `--max-queue-probes`, `--queue-probe-deadline`, `--state-file`.
8. Workflow: `actions/cache` (unique key + prefix `restore-keys`), `CI_QUEUE_HEALTH_STATE`, annotation text.
9. Arms + mutations (each R row has one).
10. **Re-anchor the three `ci_queue_health.py:354` citations** to the new line of
    `DEFAULT_MAX_PR_WAIT_SECONDS` and run the pin's own test; also re-anchor the drift-tolerant
    `tests/test_ci_queue_health.py:2952` coordinate if it is cheap to do so.

## 6. Wiring

| Call site / consumer | What it does there |
|---|---|
| `read_health` → `classify_queue` | passes the gated projection (one new field) |
| `main()` → `read_health` (twice when acting) | new kwargs; the budget arithmetic covers both readings |
| `.github/workflows/ci-queue-health.yml` | carries the state file across runs via `actions/cache` (unique key + `restore-keys` prefix) |
| `tools/ci_check_repair.py:1810` (`read_queue_health`) | second `read_health` caller; inherits both caps (≤90 s added) |
| `tools/ci_check_repair.py:1120` (`crowded = queue_starved if queue_congested is None else …`) | `congests_pr` flipping True→False/None changes its input; verified the `None` branch maps to `starves_pr`, i.e. **no** refusal and no exit-1 escalation (see §9, falsified finding) |
| `tools/ci_check_repair.py:1193` (`if crowded:`) | the zero-job repair gate; authorising it when the queue is *measured* not congested is the intended behaviour |
| `docs/ops/ci-wall-clock.md` | quotes the queue number; no edit needed once the number is real |

## 7. Risks — the Review Scope Contract's `R` rows

R1 an exclusion silences a real waiter → every value but `is True` is a waiter by construction; arms for
   unproven/None and for the zombie; mutation deletes the exclusion and reds the zombie arm.
R2 a persisted proof outlives reality → TTL arm + `updated_at`-move arm + the four-condition gate.
R3 a corrupt/absent/unparseable state file → warning reason + normal reading, never a crash.
R4 the proof is taken inside the pre-dispatch window → `_settled_still` floor arm (a 74 s-old run
   returning 0 stays unproven).
R5 the exclusion lands in one clause but not another → single join point + an arm asserting `oldest`,
   `wait_witness_ids`, `pr_queued`, `global_max` and the verdict move together.
R6 a consumer loses a JSON key or a pre-existing reading changes byte → key-set arm + byte-identical
   arm on a zombie-free fixture.
R7 a proof is cached for a run that left the feed → prune-on-load arm.
R8 worst-case probe time exceeds the job budget → count **and** deadline; derivation in the constant's
   comment (`2 × (8×15 + 90) = 420 s`), plus the second consumer's cap named.
R9 a JSON `true` / string count reads as an int → `type(total) is int` arm.
R10 `per_page=1` with an absent `total_count` abstains instead of reading 0 → arm.
R11 the **re-run hole**: `filter=latest` returns 0 for a queued attempt-2 run → `run_attempt == 1` +
   `updated_at == created_at`; arm for a queued re-run; residual = a payload that omits `run_attempt`
   (projection defaults to 1) is bounded by condition 2 and the TTL, stated.
R12 state-file races / stale `actions/cache` restore → validation-on-load (identity, stamps, future
   rejection, prune) bounds it; the cache's exact key/`restore-keys` pairing is stated in the workflow.
R13 `global_max` over live runs makes FLOOD reachable on a residue-dominated queue → arm pins the FLOOD
   clause on a residue-dominated fixture (and the absence of any new trim on a non-`TRIM_SAFE` offender).
R14 `pending`/`requested` zombies are out of scope → stated in the docstring, not silently half-fixed.
R15 the `:354` citation pin moves → re-anchor all three sites and run the pin's test (`R15` is the row
   §4's "line pins" line owns).

**Threat classes** (gate-enforcement): `T1` exclusion suppresses a legitimate waiter (R1, R2, R4, R11);
`T2` a proof trusted past its evidence (R2, R7, R12); `T3` an undecidable input abstaining toward
silence instead of alarming (R1, R10); `T4` a fail-open read turning a failed/short/oddly-typed payload
into `0` (R9, R10). No argv/path/symlink surface: the state path is operator-supplied, never
attacker-supplied.

**Out of scope — must NOT be raised** (note, file, do not chase): pre-existing defects outside the diff ·
style/prose with no behaviour change · the `#12906` population and the missing `/cancel` lever ·
disk/cache/docker classes · a `pending`-leg never-dispatched rule (R14) · re-litigating `MEM_PER_RUNNER`
or any other module's calibration.

**Acceptance:** every `R<n>` has its named evidence, and no open item remains on the declared surface.

## 8. Rounds

**Rounds:** 3 (verifiers: 2, 2, 1 — class: gate-enforcement).

⚠️ **Adversarial bound:** this change declares the adversarial domain, whose bound is 2 cycles; rounds 1
and 2 were spent there and round 3 was the coverage re-check the P0 fixes in §10.2/§10.5 required (a
changed discriminator has not been shown covered until it is re-checked). No round-3 finding was chased
beyond the two P0s and one P1, each of which was a defect the fixes themselves introduced.
`[ADVERSARIAL-BOUND] cycles=3 threats=4 covered=4 residuals=none` — see §10.9 (T1-T4) and PROVE below.

## 8b. PROVE

**Tests.** `pytest tests/test_ci_queue_health.py tests/test_ci_check_repair.py -q` → **365 passed**.
Adjacent suites: `tests/test_ci_selection.py tests/test_ci_verdict.py` → **584 passed, 1 skipped**;
`tests/test_runner_tool_contract.py tests/test_ci_selection_space_separated_10409.py` → **67 passed**.

**Mutation proof — 21/21 CAUGHT, 0 missed.** Each row deletes or inverts one guarded branch and names
the arm that goes red:
M1 the exclusion removed from `wait_runs` · M2 the abstention flipped to exclude (`None` becomes a proof)
· M3 the 5 400 s floor lowered to `JOB_SETTLE_SECONDS` · M4 the re-run attempt guard removed · M5 the
raw-payload attempt provenance removed · M6 the unmoved-stamp guard removed · M7 `type(total) is int`
weakened to `isinstance` · M8 the deadline break removed · M9 the probe-attempted flag derived from the
answers · M10 a future-dated proof accepted · M11 the TTL dropped · M12 prune-on-load dropped · M13 the
identity check replaced by truthiness · M14 the store version/repo check removed · M15 the state-line
token emitted unconditionally · M16 the FLOOD clause back on the feed tally · M17
`PR-NEVER-DISPATCHED` degraded to `OK` · M18 the per-run deadlock-breaker branch removed · M19 the
consumer's per-run measured-ness disabled · M21 the consumer exemption back to one measured-ness · M22 a
probed-but-dispatched run counted as UNREAD.

⛔ **THREE MUTATIONS SURVIVED THE FIRST RUN AND EACH WAS A REAL DEFECT, NOT A TEST NUISANCE** (recorded
because "the mutation passed" is the only evidence that an arm tests the guard it names):
- **M3 survived** because the only "young" fixture was 74 s — below BOTH candidate floors, so the arm
  never discriminated 300 from 5 400. Fixed with fixtures at 1 000 s and 4 000 s (inside the measured
  p50 queue wait, outside the 300 s window).
- **M13 survived** because the truthy-not-`True` records carried an unparseable `updated_at`, so the
  STAMP check dropped them and the identity guard was never reached. Fixed with parseable stamps.
- **M20 (the escalation guard) survived** because the code was UNREACHABLE: `apart_measured is True`
  implies `apart is False`, so no refusal can be recorded on such a reading and the guard could never
  fire. It was **removed** rather than kept — dead weight masquerading as protection is the defect
  `_settled_still`'s own comment names.


## 9. Round-1 findings that were FALSIFIED (recorded so they are not re-applied)

- **"`congests_pr is None` gives `ci_check_repair` a permanent exit 1."** Falsified: `repair_refused` is
  appended at exactly one site (`ci_check_repair.py:1244`), inside `if crowded:`, and `crowded` is
  `queue_starved if queue_congested is None else queue_congested` (`:1120`) — for an `OK` verdict
  `starves_pr` is `False`, so no refusal is recorded and the `congestion is None and any(repair_refused)`
  escalation at `:2405` cannot fire. The finding's *shape* (a consumer consequence) is real and is kept
  as the R-row work in §6/§3.9, but the exit-1 mechanism is not.
- **"§3.5's `pr_queued` claim is fine because it keeps feed truth."** Falsified in the opposite
  direction from the finding's framing: the override at `:1962` *does* force `pr_queued=0`, so the
  claim was wrong and the code is fixed (§3.6). Applied.

---

## 10. v3 — after PLAN-VERIFY round 2 (section numbers above are kept for the audit trail; **this section supersedes** §3.1-§3.9, §5, §6, §7 and §9 where they conflict)

### 10.1 ⛔ A round-1 finding I "falsified" is REAL — the falsification is retracted

Round 1 said: *"`congests_pr is None` gives `ci_check_repair` a permanent exit 1."* I rejected it after
reading `crowded = queue_starved if queue_congested is None else queue_congested`
(`ci_check_repair.py:1120`). **That reading was wrong, and it was wrong for a reason worth recording:
the `None` branch is dead code at the real entry point.** `main()` builds the value through
`queue_congestion_gate`, whose entire body is `return congestion is not False`
(`ci_check_repair.py`, verified) — so `queue_congested` is `True`/`False` and never `None`. Therefore
`crowded` is `True` on an unmeasured reading, the `if crowded:` branch (`:1193`) refuses, `repair_refused`
is appended (`:1244`), and `:2405` (`args.apply and code == 0 and congestion is None and
any(p.repair_refused ...)`) **does** escalate to exit 1.

Cost of my error had it shipped: the change would have moved a shape that today repairs (3 aged
zero-job runs → `PR-STARVED` → `congests_pr True` → `apart_from False` → repair allowed) into
`congests_pr None` → refused → **exit 1 every 30 minutes with the required check never produced** —
which `docs/ci/ci-check-repair-red-surface.md` §Fact 2 declares unsound, and which is the very defect
class this issue was filed about. The lesson: a branch that *can* be read is not a branch that *is*
reachable; trace the argument to its producer, not to its `if`.

### 10.2 The eligibility floor is `ZERO_JOB_MIN_AGE_S` (5 400 s), not 300 s

Round 2 measured the window properly: `docs/ops/ci-wall-clock.md` §1 puts created → first job start at
p50 **29.9 min** / p95 77.0 / max 81.9, so a 300 s floor is ~1/6 of the p50 and lets a legitimately
queued run be "proved" dead. And the sibling instrument already owns this question: `ci_check_repair`'s
`ZERO_JOB_MIN_AGE_S = 5400` is its floor for **the same class** ("young zero-job runs … the normal
progressive-job-creation lag"). Two instruments must not answer the same question differently.

**Eligibility (all four; anything else ⇒ `never_dispatched=None` ⇒ a WAITER):**
1. `run.run_attempt == 1` **and the payload reported it** — from the RAW row, not the coerced projection
   (new `QueuedRun.attempt_reported: bool`, set in `_project` from `type(attempt) is int and attempt > 0`,
   closing the `int(True)==1` / `"2"` / absent-field coercion at `:1297`);
2. `run.updated_at == run.created_at` and both are non-empty (a re-run moves the run object; a frozen
   first attempt does not);
3. `_settled_still(run.updated_at, now, NEVER_DISPATCHED_MIN_AGE_SECONDS)` — **the same helper**, with
   `NEVER_DISPATCHED_MIN_AGE_SECONDS = ci_check_repair.ZERO_JOB_MIN_AGE_S` (5 400 s), so the future /
   unreadable / merely-recent stamp routes all abstain for free;
4. `type(total) is int and total == 0`.

**Measured support for the floor (2026-11-19T18:1xZ, `gh run list --status queued --limit 200` + one
`jobs?filter=latest&per_page=1` per sampled run):** of 33 live queued runs sampled across six age bands
(0-300 / 300-900 / 900-1800 / 1800-3600 / 3600-5400 s), **every one holds ≥1 job record** — including
two at 143-145 s with `updated_at == created_at` (`total_count: 1`) — and **no live queued run reaches
5 400 s**. The zombie population is 7.9-14 d. So at the floor the two populations do not overlap, and
condition 3 (a 90-minute window) is ~14× the measured p95 queue wait.

**Consequence, stated:** a run becomes *provable* at exactly the age at which it becomes *alarming*. The
first pass that can prove it may already have warned. That is intended: the alarm fires, then the proof
refines "waiting" into "dead, and here is the count".

### 10.3 The TTL is a persistence bound only — shortened, and said so

Round 2 is right that the TTL cannot *shorten* a wrong proof: the only event that invalidates one is
dispatch, which also prunes the entry. So the TTL is not a second safety bound and must not be sold as
one. It is **`NEVER_DISPATCHED_REPROOF_SECONDS = 7 200 s` (2 h)**, derived from convergence, not taste:
`128 proofs / 24 per pass × 10 min cadence × 2 (headroom) ≈ 107 min → 2 h`. Its job is to force
re-observation; the **eligibility floor in §10.2 is the safety guard**.

### 10.4 A seventh verdict token — `PR-NEVER-DISPATCHED`

Both round-2 verifiers independently reached this, and the issue's own (b)(2) note prefers it: emitting
`OK` over a queue of 128 proven-dead PR runs presents "nothing is waiting" as health in the one string
the fleet samples, and §3.9's "the count makes it honest" under-reads how `OK` is consumed.

- `PR_NEVER_DISPATCHED`, **`EXIT_OK`** in `EXIT_FOR` — no exit semantic changes, so the issue's
  "must not silently become a seventh exit semantic" holds.
- `starves_pr = False`, `delays_pr = False`, and `congests_pr` **`None`** (§10.5) — never a clearance.
- Emitted when `pr_never_dispatched > 0` and the wait population is empty of live waiters; the state
  line is `CI: PR-NEVER-DISPATCHED(queued=N, pr_running=M, pr_queued=P, pr_pending=Q,
  never_dispatched=X, never_dispatched_unproven=Y)`.
- A `::warning` arm in the workflow, alongside the existing four.

### 10.5 `congests_pr` and the #10641 deadlock-breaker (the round-2 P0)

`congests_pr` must never be `False` on the strength of an exclusion (round 2's attack: one live waiter +
127 proofs ⇒ `False` ⇒ the destructive gate is skipped entirely). So:

- `congests_pr` returns `None` whenever `pr_never_dispatched > 0`;
- **but** `congests_pr_apart_from`, whose whole purpose is to break the per-run deadlock, must not
  short-circuit into uselessness. It gains exactly one branch:

```python
if self.congests_pr is None:
    # A reading whose ONLY degradation is that every waiter was PROVEN never
    # dispatched: no LIVE witness exists, so congestion cannot be witnessed
    # independently of the target. Guarded on the verdict so a truncated or
    # unwitnessed reading (which can also have an empty witness set) never
    # reaches this clearance.
    if (self.verdict == PR_NEVER_DISPATCHED and self.pr_never_dispatched
            and not self.wait_witness_ids
            and not self.truncated and not self.pending_truncated):
        return False
    return None
```

Why `False` is right there and not a fail-open: the question is *"is anything OTHER than this run
congested?"*, and the answer requires a live witness. With none, and the only population proved dead by
four conditions at a 90-minute floor, "not independently congested" is a measurement of the live queue.
The repo-wide `congests_pr` stays `None`, so no repo-wide destructive act is authorised.

This restores the repair path for the shape it was built for **and** removes the exit-1 route §10.1
retracted.

### 10.6 Reported fields (all additive; `pr_queued`/`queued_total` stay feed truth)

`pr_never_dispatched`, `never_dispatched_ids`, `pr_never_dispatched_unprobed`,
`pr_never_dispatched_unread` (the #12037 split — two causes, two remedies; a disjunction is not a
diagnosis), and `global_max_live_queued`. Both unproven counts are emitted **only when
`queue_probe_attempted`** — a new `classify_queue` parameter mirroring `probe_attempted`
(`:1375`/`:2440`), because `classify_queue` otherwise cannot distinguish "no probe pass" from
"budget-starved", and every frozen fixture would otherwise gain a token and red the byte-identical arms.

`global_max_queued` **keeps its present meaning** (feed truth); the FLOOD clause compares against the new
`global_max_live_queued`, so no existing key is re-scoped under a consumer.

### 10.7 State file — a versioned, repo-scoped document

`{"version": 1, "repo": "<owner/name>", "entries": {<run_id>: {"never_dispatched": true, "run_id": …,
"repo": …, "proved_at": <epoch>, "updated_at": …}}}`. On load: reject the whole file on a
`version`/`repo` mismatch; drop any entry that is not a `Mapping`, whose `never_dispatched` is not
`is True`, whose `run_id` is not an `int`, whose stamps are not `str` or do not parse (**the loader type-
checks before calling `_parse_time`, which raises `AttributeError` on a non-str**), whose
`proved_at` is in the future, whose TTL has expired, or whose run has left the feed / moved its stamp.
Atomic write, prune on load **and** save. Cache key pinned to `${{ github.repository }}` in both the key
and the `restore-keys` prefix.

### 10.8 Budget (unchanged in shape; one clamp added)

`DEFAULT_MAX_QUEUE_PROBES = 24` + `QUEUE_PROBE_DEADLINE_SECONDS = 90`, and **each queued probe's timeout
is `min(JOB_PROBE_TIMEOUT_SECONDS, deadline - elapsed)`** so the phase cannot overrun by one probe:
worst case `2 × (8×15 + 90) = 420 s of 600 s`. Second consumer (`ci_check_repair.read_queue_health`,
15-min job) inherits both caps (≤90 s added).

### 10.9 Contract deltas (these replace the same-numbered rows in §7)

- **R1** unchanged (proven-only exclusion; every other value is a waiter).
- **R2** rewritten: the guard is the **5 400 s eligibility floor** (§10.2) + four conditions; the TTL is
  a persistence bound and is **not** claimed as a safety bound (§10.3). Arms: a 4 000 s zero-job run
  stays unproven; a 5 400 s zero-job run with all four conditions is proved.
- **R5** rewritten: `oldest`, `wait_witness_ids`, the verdict and `never_dispatched_ids` move together;
  `pr_queued`, `queued_total` and `global_max_queued` stay feed truth (byte-identical), and the FLOOD
  clause compares against `global_max_live_queued`.
- **R11** rewritten: the re-run guard is `run_attempt == 1` **from the raw payload** plus
  `updated_at == created_at`; the residual (a re-run that moves no stamp *and* reports no attempt) is
  bounded by **condition 3's 90-minute floor alone** — the TTL bounds persistence, not acquisition.
- **R8** rewritten with the clamp (§10.8).
- **R13** rewritten: `global_max_queued` keeps feed truth; the FLOOD clause moves to
  `global_max_live_queued`; arm pins that a residue-dominated queue with no live non-PR offender still
  cannot fire FLOOD, and that a 7-run live queue with a `TRIM_SAFE` offender past `min_dominant_queued`
  can.
- **R16** (new): the #10641 deadlock-breaker still works for its population — arm through
  `classify_queue` on the 3-aged-zero-job shape asserting `congests_pr_apart_from(...) is False`, plus an
  arm asserting `congests_pr is None` on the same reading (no repo-wide clearance).
- **R17** (new): a `PR-NEVER-DISPATCHED` reading exits **0** (`EXIT_FOR`) and never `OK`; arm on both.
- **R18** (new): the issue's Indicator 2 fixture is pinned at the issue's own measured numbers
  (`oldest` 15 622 s for the live leg, 1 214 984 s for the zombie) — a hermetic fixture, not a live
  reading, so it cannot rot; today's live `oldest` (≈4 886 s) is a dated observation, not the pin.
- **R19** (new): every measurement the plan relies on carries its command + timestamp in §10.10.

### 10.10 Measurement provenance (round-2 Q2 — every load-bearing number, with its command)

| Claim | Command / source | When |
|---|---|---|
| 128 zombies of 163; stillness 680 364 s min vs 3 871 s max live | `gh run list --status queued --limit 400 --json databaseId,createdAt,updatedAt` + `now-updatedAt` | 18:02Z |
| zombies have `total_count: 0` | `gh api .../actions/runs/{42067212986,43098610530}/jobs?filter=latest&per_page=100` | 17:58Z |
| **every** live queued run sampled has ≥1 job, incl. two at 143-145 s with `ua == ca` | 6 age bands × up to 4 sampled runs, `jobs?filter=latest&per_page=1` | 18:16Z |
| no live queued run reaches 5 400 s | same read | 18:16Z |
| `oldest=1215486s`, `PR-BACKLOGGED`, `congests_pr=True` | `python3 tools/ci_queue_health.py --json` at `38802821cf` | 17:53Z |
| job-creation latency and the 90-minute floor | `docs/ops/ci-wall-clock.md` §1 (in-tree, re-read, not re-measured) | — |
| `ZERO_JOB_MIN_AGE_S = 5400` | `tools/ci_check_repair.py:246` | — |

## 10.11 Live verification (2026-11-26) — and the residue it exposed

Run read-only against the live repo (`--json`, no `--apply`), proof store in `/tmp`:

| pass | `pr_queued` | proved never dispatched | `oldest` | verdict |
|---|---|---|---|---|
| pre-fix `38802821cf` | 155 | — | 1 215 486 s | PR-BACKLOGGED |
| 1 (budget 24) | 151 | 24 | 1 217 882 s | PR-BACKLOGGED |
| 3 (budget 24) | 90 | 48 | 1 217 950 s | PR-BACKLOGGED |
| one pass, budget 200 | **157 → 93** | **121** | 1 218 172 s | PR-BACKLOGGED |
| snapshot, all eligible proved | 93 | 61 | 1 218 181 s | PR-BACKLOGGED |

**What the fix delivers.** The queued-leg overcount is gone: 121 runs are proved never
dispatched in a single pass (`unproven=0`, `unread=0`) and the excluded population is
stable across passes (proofs persist and re-observe on the 2 h TTL — 24 → 48 → 121 as
the budget allowed). The proof store's prune-on-load is why the count oscillates between
passes: the `status=queued` feed itself swings (`queued=98` … `167` on consecutive reads),
so a run absent from *this* read loses its proof and is re-proven later. That is the
fail-closed direction (a lost proof counts a run as waiting, never the reverse).

**What it does NOT deliver — indicator 2 of the issue, and why that is correct.** `oldest`
stays at ~14 days after the exclusion, because the witness moved legs. Measured:

```
pending 42062387969  Python CI          is_pr=True   attempt=1   0 jobs  created 2026-11-05T16:14:34Z  updated 16:14:36Z
pending 42066444542  ci-queue-health    is_pr=False  attempt=1   0 jobs
pending 42065475308  Inbound relay      is_pr=False  attempt=1   0 jobs
```

The one PR-event witness is a **`pending`** run, 14 days old, `attempt=1`, **0 job records** —
the same never-dispatched signature as the queued zombies — but it fails eligibility on
`updated_at == created_at` (it moved +2 s at creation), and no stronger proof exists on that
leg: `run_started_at` is set for all three, and per-commit check-runs are shared across
workflows (26/1/41 on commits with one job workflow each), so neither separates "held by a
concurrency group" from "never dispatched". **Round 3's P0 is therefore vindicated, not
overturned**: the pending leg has no decidable proof at any age, and guessing would exclude a
legitimately-held run — a false PASS on the destructive path.

So the fix does the two things it can prove (the count and the exclusion), and §10.5's
demotion holds: `congests_pr` never goes `False` from an exclusion. The reading now *names*
the witness leg so the residue cannot be mistaken for the unfixed defect:

```
· the witness is a 'pending' leg run (#42062387969) — a `pending`/`requested` run, on a leg
  where zero job records is not a proof (a concurrency hold has none either), so it is
  counted as WAITING
```

**Residual, filed rather than chased:** a pending-leg run with zero job records that its
`updated_at` moved on is still counted as a waiter. Removing that needs a decidable signal
this tool does not have (a job-less pending run's concurrency holder), and the arm for it
would have to be a new probe, not a new predicate. Filed as a follow-up on the same defect
family so the next lane reads it with this measurement in hand, rather than re-deriving the
`updated_at` finding that already cost one review round.
