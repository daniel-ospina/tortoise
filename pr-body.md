## Summary

**D1 of #14342 — "the drain converges".** Absorbs #12992's starvation half and the unfiled "serialize the drain" finding (handoff §7.3). Builds on D2 (#14347, merged) and changes **nothing** about the spool's on-disk format.

One sentence: *the drain is now ONE process per spool, holding ONE of the server's eight capture slots, and a capacity refusal is charged to nobody — so the backlog head can only fall.*

### The defect, restated from the code

Two independent mechanisms, both measured:

1. **N drainers.** `session-start.sh:831` backgrounds `tortoise session drain` on **every** SessionStart; `session-end.sh` takes a final flush; `agent_end` (Pi) takes another; `store_sync` drains the same spool as one of its legs; and both legs (Python + Pi) share one directory. Every one of those POSTs into **one** server gate — `hosted_api.py:447` `_CAPTURE_MAX_IN_FLIGHT = 8`, refused at `:1729` with `capture_capacity_saturated`. The drain competes with itself and with the user's live captures.
2. **A capacity refusal was charged to the ENTRY.** `_flush_one` treated a non-ok outcome uniformly: `attempts += 1` and `next_attempt_at_ms = now + backoff_delay(attempts)` (30 s → 6 h). A capacity refusal says nothing about the entry — it says eight captures are in flight on this box right now — but one saturated pass charged **every** pending entry, and exponential backoff then pushed most of the spool out of the attempt window. That is how `attempts` reached 10–17 on the measured 908-entry spool, and why the head stopped falling while every surface reported a drain in progress.

## The fix

| | |
|---|---|
| **One drainer** | `tortoise/drain_claim.py` (new): a lease on `<spool>/drain.claim`, taken by an **untargeted** `flush_spool` pass. A second drainer returns immediately (`held_by_claim`, zero POSTs) — nothing is lost, the holder is working the same oldest-first list. |
| **A stated ceiling** | `DRAIN_MAX_IN_FLIGHT = 1` POST in flight, *per box*. It is a cap on how much of the shared 8-slot budget the drain may hold (7 stay available to live captures), not a throughput knob, and no configuration raises it. |
| **Capacity charges nobody** | `capacity_refusal()` + `_flush_one`'s early return: no `attempts` increment, no backoff armed, and the **pass stops**. It then yields, honouring the server's own `retry_after`, bounded at `DRAIN_CAPACITY_YIELDS = 3`, and retries **the same** entry — so a busy box starves nothing and a head never rises. |
| **Named, durably** | The refusal is recorded as `blocked` + 429 + the code, so the health line reads `capture: BLOCKED (n queued, 429 capture capacity)` instead of a box that cannot file looking healthy. Self-healing on both ends. |
| **Both legs** | The Pi seam implements the same file, the same three staleness rules and the same capacity rule — including the same on-disk timestamp UNIT, which a review cycle proved was not true at first (seconds vs milliseconds: each leg stole the other's live claim). Hook generation 32 → 33. A lock one leg cannot take is a lock the other leg does not have, and four cross-leg tests now RUN the real TypeScript predicate under `node` against a claim this leg wrote (and the reverse) rather than trusting either leg's opinion of the other. |

### Why a lease and not a lockfile or a config promise

* `flock(2)` is kernel-released and stronger, but **Node has no flock** and the Pi leg drains the same directory — a lock it cannot take is a lock it ignores.
* "Run exactly one drainer service" has no mechanical enforcement; the hook that fires the drain is installed per harness.

So: an atomically-created record (`os.link`/`linkSync` — create-if-absent, and the target appears **with its full content**; `rename` would *replace*, letting two processes both win, and plain `wx` exposes a zero-length claim a reader would call corrupt) that the holder refreshes, and that is stale the moment its holder is **provably** gone (same host + same `boot_id` + dead pid) or its **lease expires** (`DEFAULT_TTL_S = 120`, derived: one POST is bounded at 30 s by `urlopen(timeout=30)` + local work, ×4). Takeover is a **compare-and-delete**, so a slow holder can never delete a successor's claim. That is the issue's own requirement — *"a stale claim is re-verifiable, not a permanent block"* — and "one drainer" cannot become a wedge.

## The measurement (the ceiling is justified, not chosen)

`tools/measure_drain_convergence.py` — the same script, the same fake server (it obeys `_reserve_capture_slot`: 8 slots, then 429 + `retry_after`), run against **two trees**. Scenario `saturated`: the box's eight slots are held by live captures, 12 drain opportunities pass (backoff clock advanced 35 s between them, the cadence of successive SessionStarts), then the slots free and **one** more pass runs. 60 spooled entries, 10 concurrent drains.

| | `origin/main` @ f7b3e9a5 | this branch |
|---|---|---|
| POSTs during the 12 saturated passes | **2,399** (~200/pass) | **48** (4/pass) |
| POSTs in the whole run (incl. the free passes) | 2,400 | **108** |
| of those, refused at the ceiling | **2,399** — every one | **48** |
| max `attempts` reached (high-water) | **4** | **0** |
| the free pass: pending | **60 → 60** | **60 → 0** |
| a further cycle: pending | **60** | **0** |
| entries still inside a backoff window after it | **60** (30 s escalates to 6 h) | **0** |
| 10-process claim race | n/a (no claim) | **1 winner** |

And the same script's `baseline` scenario (server NOT saturated: the drain competes
only with itself), which shows the ceiling's own effect:

| | `origin/main` | this branch |
|---|---|---|
| POSTs for 60 entries | **600** (one per drainer) | **60** |
| peak capture slots the drain held | **9** — over the server's 8 | **1** |
| refusals provoked by the drain itself | **5** | **0** |

⛔ **Both readings are the HIGH-WATER one** (`max_attempts_high_water`, sampled
after every round). Reading `attempts` only at the end is a false PASS in this
branch: filing clears the meta, so a converged run reads 0 whatever the saturated
rounds charged. The receipt carries both numbers and names which one the
falsifier uses.

The last rows matter as much as the first: on today's main the free server is **never used** — the backlog is frozen by the windows its own saturated passes armed — and the drain's 9-way concurrency is itself what provokes refusals against the user's captures.

**The falsifier (issue body), measured:** *"from a non-zero spool, the head does not reach zero and stay there for a full cycle with no entry exceeding `attempts = 3`"* → `test_the_falsifier_holds` runs two drainers against the real 8-slot gate: head **0**, `attempts` max **0**, `high_water == 1`, and a second pass files and defers nothing. It passes.

## Architecture constraints (verified — #14342's research mandate)

| constraint | how this PR obeys it |
|---|---|
| `docs/architecture/STORAGE-ARCHITECTURE.md` §2.0 — Postgres is the record, FalkorDB a derived cache | untouched. This is the local spool, **upstream** of both; **no raw turn enters the graph**, and record and cache are not collapsed. The change only decides *which process* files, and *who is charged* for a refusal. |
| `docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md` — extraction is the only producer of the logical model | untouched: **no extraction logic in the drain**, and this change adds none. A capacity refusal does not touch extraction's state at all; the entry stays on the spool byte-identical. |
| D2's contract — the spool's on-disk format is not changed | `drain.claim` lives in the spool **ROOT** (beside `status.json`), never `entries/`, so it is invisible to `_entry_bytes`, `prune_spool`, `count_pending_captures` and both ceilings — pinned by a test. No meta or turn-log field was added, removed or re-read. |
| Raising the global ceiling is NOT the fix | the server's `_CAPTURE_MAX_IN_FLIGHT` is untouched; the drain is bounded to 1 of 8. |

## Capstone conditions — honest state

| # | condition | this PR |
|---|---|---|
| 1 | **Backlog converges** (head → 0, `attempts ≤ 3`) | **moved, measured**: 60 → 0 with `attempts` 0, on the first pass with a free server; before, 60 → 60. |
| 2 | **Nothing is lost** (N in, N out) | **not claimed here** — D2 moved the loss paths. This drain files every entry it attempts, and a capacity stop leaves each entry byte-identical on disk, but **N-in/N-out is not measured end-to-end on a real 908-entry spool** (this box has no capture spool at all; the handoff's figures are per-box). |
| 3 | **Live captures not starved** | **moved mechanically**: the drain holds 1 of the server's 8 slots (`high_water = 1` measured; `9` before), and it *yields* on a capacity refusal instead of fighting. Not shown against the hosted API — no server is reachable from this box. |
| 4 | **The §7.4 contradiction demo** | **NOT moved, owed.** It needs the extraction path end to end; the drain deliberately does not touch extraction. |

**So the programme is still open.** This PR closes the drain's half of conditions 1 and 3 and publishes the numbers; it does not make the capstone.

## Review Scope Contract

- **Class:** `code`
- **R1** — two concurrent UNTARGETED drains cannot both POST to one spool; the survivor is a no-op that reports who holds it, and it writes no durable status.
- **R2** — a claim is reclaimed when its holder is provably gone or its lease expires, and a slow holder can never delete a successor's claim.
- **R3** — a `429` capacity refusal charges no entry (`attempts`, `next_attempt_at_ms` untouched) and ends the pass; the pass retries the *same* entry after a bounded yield.
- **R4** — no behaviour change for a spool whose box is not saturated and whose drain is the only one running (guarded: the free-server measurement and the existing suites).
- **R5** — nothing here writes a raw captured turn to the graph, moves extraction into the drain, or changes the spool's on-disk format.
- **R6** — both legs implement ONE protocol; a divergence is a defect.

## Tests

```
# 45 cases — the claim protocol, the capacity rule, the falsifier, 4 CROSS-LEG
TORTOISE_TEST_CARVE_OUT=1 uv run python -m pytest tests/test_14350_drain_convergence.py -q
45 passed

# capture family + install-contract pins (generation 33) + store_sync + doctor
TORTOISE_TEST_CARVE_OUT=1 uv run python -m pytest tests/test_capture_spool.py \
    tests/test_14350_drain_convergence.py tests/test_12483_12487_spool_integrity.py \
    tests/test_12992_capture_capacity_typed_refusal.py tests/test_capture_backlog_4197.py \
    tests/test_12379_unrecorded_trace.py tests/test_capture_install.py \
    tests/test_store_sync_worker.py tests/test_doctor.py -q
407 passed

# the four cross-leg cases run the REAL TypeScript predicate under `node`, so the
# seam is graded by both implementations rather than by one leg's opinion of it
node --test tortoise/pi-hooks/tortoise-capture.test.ts tortoise/pi-hooks/tortoise-volunteer.test.ts
294 pass / 0 fail
```

Every new case names the mutation that reds it. `WORKTREE-REPORT.md` carries the commands verbatim.

Two existing cases needed a stated reason, and neither is weakened:
- **#12603's confirm-read race** now races a **targeted** filing, because a whole-spool peer is excluded by the claim — and a targeted filing (`session capture`, the shutdown flush) is the only racer left, and a real one.
- **#2100's quota case** let a never-answering POST hang across a later process, which now correctly reads `heldByClaim`; it releases the wedged pass first and additionally asserts the claim is released — a new invariant.

## Review cycle log

**Cycle 1** (fresh-context reviewer at `b742bc32`) — 1 P0, 4 P1, 6 P2, all
dispositioned:

| finding | disposition |
|---|---|
| **P0** HEAD fails its own `test_a_BARE_429_is_also_a_box_level_refusal` | **already fixed** at the reviewed commit's successor: the reviewer's scratch worktree was cut before `b742bc32`, where `capacity_refusal` sets `code` only for a typed body. Verified at head: 28/28 of that file passed before this cycle's additions. The divergence it flagged (health line `429 429`) is what the fix removed. |
| **P1** `_remove_if_unchanged` takeover race → two live holders | **fixed, both legs.** `rename`-to-quarantine + compare-in-quarantine + restore-by-`link`. Reproduced deterministically; 2 tests red on the old order. |
| **P1** `renew` resurrects an expired lease over a successor | **fixed, both legs**, + the test that was supposed to catch it was itself broken (the mutation survived — see the second commit). |
| **P1** targeted pass yields on capacity → hook killed mid-pass | **fixed** (Python only; the Pi leg never slept). Test asserts a targeted pass makes one POST and sleeps 0. |
| **P1** measurement: `max_attempts` read after filing is a false PASS; 3-round default can't distinguish the trees | **fixed**: `max_attempts_high_water` sampled per round (both numbers emitted, the falsifier named), default 12 rounds, and the arithmetic for why is in the docstring. |
| **P1** spool root mkdir 0755 | **fixed** (0700), with a mode test. |
| **P2** `readDrainClaim` stricter than `claimIsStale` → fail-open beside a live holder | **fixed**: the contract is now explicit and pinned by a test ("whatever the predicate can judge, the reader can read"). |
| **P2** `CLAIM_VERSION` never read | **fixed** in the staleness predicate, both legs. |
| **P2** `only_session_id == ""` skips the claim but drains everything | **fixed** on both sides of the split, both legs. |
| **P2** `renew` conflates "write failed" with "not ours" | **fixed**: re-read; still ours means keep draining on the un-refreshed lease. |
| **P2** `_summary_dict` receipt missing claim/capacity fields | **fixed** (`claim_unavailable`, `lost_claim` added to the two already there). |
| **P2** `attempted`/`deferred` count one entry per yield | **documented, not changed**: they count POSTs and deferral *events*, and a pass that retried one entry four times made four POSTs. The counter says what it counts; the capacity line names the yields beside it. |

**Cycle 2** (fresh reviewer at `d532a67d`) — 1 **P0**, 1 P1, 5 P2:

| finding | disposition |
|---|---|
| **P0** the legs write `expires_at` in DIFFERENT UNITS — each steals the other's live claim | **fixed.** Python wrote seconds, the Pi leg milliseconds, and neither normalised: a live Python claim read as expired to the Pi leg (measured: `acquireDrainClaim` returned a claim over a live holder), and a Pi claim read here was live for ~55,000 years. One unit on disk (milliseconds, `TIMESTAMP_UNIT_MS`), `CLAIM_VERSION` 1→2 so a surviving v1 record is unattributable rather than misevaluated, and **two cross-leg tests that run the real TypeScript predicate under `node`** against a claim this leg wrote (and the reverse) — the class of test whose absence let it ship. Both red on a revert to seconds. |
| **P1** an unreadable claim became a PERMANENT block | **fixed** (both legs), see cycle 3 — cycle 2's fix was incomplete and cycle 3 found it. |
| **P2** `renew` was read-then-replace with no compare | **fixed**: verify-after-write, both legs. |
| **P2** a pass that RECOVERED from a capacity refusal still recorded `blocked`/429 | **fixed**: the durable record and the operator line key on `capacity_stopped`. |
| **P2** `capacity_refusal` relabelled the server's `ingest_rate_limited` 429 | **fixed** in Python (server's `detail.code` wins); the Pi leg was found still hardcoding it in cycle 3 and fixed with a both-legs test. |
| **P2** the Pi leg's restore path was untested (240/240 green with it deleted) | **fixed**: the slow-holder-vs-successor test is ported; it reds when the restore is removed. |
| **P2** the receipt hardcoded `max_attempts: 0` in its "further cycle" round | **fixed**: measured. |

**Cycle 3** (fresh reviewer at `c214f341`) — 1 **P0**, 1 P1, 5 P2:

| finding | disposition |
|---|---|
| **P0** a transient READ error deleted a LIVE claim | **fixed.** `_read_raw` answered `None` for every `OSError`, and `None` routed to "clear the obstruction", which unlinked any regular file — so EMFILE/EIO/EACCES on a perfectly good claim handed the spool to a second process while the first was still draining (reproduced with one read failure). The decision is now taken from `lstat`'s TYPE: a regular file we could not read is honoured, only a non-regular object is cleared. Both legs. |
| **P1** the Pi leg hardcoded the capacity code, relabelling `ingest_rate_limited` in the SHARED `status.json` | **fixed** (`serverRefusalCode`), with a test that feeds ONE refusal body to BOTH legs and compares. |
| **P2** a FIFO at the claim path hung every drain forever | **fixed**: reads are `lstat`-gated to regular files, so a FIFO answers immediately and is cleared as an object. |
| **P2** `holder(root)` was called twice → `AttributeError` on the contention path | **fixed**. |
| **P2** `only_session_id == ""` untested (the cycle-1 fix could be reverted silently) | **fixed**: one case per leg; `""` drains everything, so it must take the claim. |
| **P2** `renew` advanced `started_at` here but not in the Pi leg | **fixed**: pinned in the cross-leg test. |
| **P2** this PR record was stale (counts, cycle log, the POST row, the digest) | **fixed** (this update). |

Every fix has a test that reds without it. Cycle 3 reverted **16 primitives** and
all 16 red — and that check is how the broken first version of the expiry test
(cycle 1) and the untested quarantine name were found.

## Residuals, stated

- **Generation 33 is introduced by this PR and has never shipped**, so its recorded
  seam digest in `test_capture_install.py` was updated in place at each head
  (capture half `d0e8e41d…`; the read half moved `708b6a…`→`904d2742…` because its
  generation MARKER moved 32→33 with the pair) instead of
  spending a generation 34 on a refinement of the same unreleased seam. From the
  moment 33 reaches `main` the entries are frozen and a byte change needs 34.
- **A wedged pass holds the claim until its lease (≤120 s).** That is what a lease is; the bound is derived from the transport (`REQUEST_TIMEOUT_MS` 10 s / `urlopen` 30 s), and a real fetch always ends. A hung *mock* does not — which is why the #2100 case above had to release it.
- **A claim we cannot READ is HONOURED, not taken over** (both legs): a transient
  local error on a regular claim file is not evidence about its holder, and the
  same reasoning applies to anything at the claim path that is not a regular file
  (it is cleared as an object, never read — reading a FIFO blocks forever). The
  cost is that a permanently unreadable claim file blocks the drain until a human
  looks, which is the fail-closed direction the issue's "never two drainers" rule
  prefers over the alternative.
- **A targeted filing is exempt from the claim.** It is one POST, so the ceiling (1 slot) is respected; the exemption buys the live session's own shutdown filing never being queued behind a backlog.
- **The Pi leg does not yield on a capacity stop** — it ends the pass and lets the next opportunity continue, because its pass is `await`ed on `session_shutdown` and must not sleep. Both legs charge nothing. This asymmetry is deliberate and is stated in the code.
- **A non-`capture_capacity_saturated` 429 is also treated as box-level** (status triggers, code names). Narrowing to the typed category would leave the defect alive on any server that does not send one.
- **`retry_after` is capped at `RETRY_BASE_SECONDS`** when yielding, so a corrupt body cannot park the drain (and hold the claim) for hours.
- **The convergence measurement is a reproduction of the MECHANISM, not of the production backlog** (60 entries vs 908): this box has no capture spool, and the handoff's counts are per-box.

Part of #14342. Closes #14350 (D1) · #12992's starvation half.

**Tracking:** this PR is the implementation record for **D1 (#14350)**.

## Contribution license

- [x] I donate this contribution under the BSD 3-Clause License (fleet-authored change).
