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

* **Class:** `code`
* **R1** — two concurrent UNTARGETED drains cannot both POST to one spool; the survivor is a no-op that reports who holds it, and it writes no durable status.
* **R2** — a claim is reclaimed when its holder is provably gone or its lease expires, and a slow holder can never delete a successor's claim.
* **R3** — a `429` capacity refusal charges no entry (`attempts`, `next_attempt_at_ms` untouched) and ends the pass; on the Python leg the pass retries the *same* entry after a bounded yield, and on the Pi leg it ends and lets the next opportunity continue (its pass is `await`ed on `session_shutdown`, so it must not sleep). Both legs charge nothing — that is the rule; the yield is the Python leg's implementation of it.
* **R4** — no behaviour change for a spool whose box is not saturated and whose drain is the only one running (guarded: the free-server measurement and the existing suites).
* **R5** — nothing here writes a raw captured turn to the graph, moves extraction into the drain, or changes the spool's on-disk format.
* **R6** — both legs implement ONE protocol; a divergence is a defect. **The one deliberate asymmetry is R3's yield** (Python sleeps and retries, Pi ends the pass), stated here rather than left for a reviewer to re-derive as a defect; everything else — the claim record and its version, the staleness rules, the compare-and-delete takedown, the capacity rule, the on-disk unit, the refusal's rendering — is the same on both sides and pinned by cross-leg tests that run one leg's predicate under the other's runtime.

## Tests

```text
# 55 cases — the claim protocol, the capacity rule, the falsifier, 4 CROSS-LEG
TORTOISE_TEST_CARVE_OUT=1 uv run python -m pytest tests/test_14350_drain_convergence.py -q
55 passed

# capture family + install-contract pins (generation 33) + store_sync + doctor
TORTOISE_TEST_CARVE_OUT=1 uv run python -m pytest tests/test_capture_spool.py \
    tests/test_14350_drain_convergence.py tests/test_12483_12487_spool_integrity.py \
    tests/test_12992_capture_capacity_typed_refusal.py tests/test_capture_backlog_4197.py \
    tests/test_12379_unrecorded_trace.py tests/test_capture_install.py \
    tests/test_store_sync_worker.py tests/test_doctor.py -q
744 passed

# the four cross-leg cases run the REAL TypeScript predicate under `node`, so the
# seam is graded by both implementations rather than by one leg's opinion of it
node --test tortoise/pi-hooks/tortoise-capture.test.ts tortoise/pi-hooks/tortoise-volunteer.test.ts
312 pass / 0 fail
```

Every new case names the mutation that reds it. `WORKTREE-REPORT.md` carries the commands verbatim.

Two existing cases needed a stated reason, and neither is weakened:

* **#12603's confirm-read race** now races a **targeted** filing, because a whole-spool peer is excluded by the claim — and a targeted filing (`session capture`, the shutdown flush) is the only racer left, and a real one.
* **#2100's quota case** let a never-answering POST hang across a later process, which now correctly reads `heldByClaim`; it releases the wedged pass first and additionally asserts the claim is released — a new invariant.

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

**Cycle 4** (the #14423 review record at `d532a67d`) — 1 **P0**, 1 P1, 1 P2:

| finding | disposition |
|---|---|
| **P0** the two legs wrote `expires_at` in DIFFERENT UNITS, so each stole the other's live claim | **already fixed at `c214f34`** (before this head): one on-disk unit (milliseconds, `TIMESTAMP_UNIT_MS`), `CLAIM_VERSION` 1→2 so a surviving v1 record is unattributable rather than misevaluated, and two cross-leg tests that run the real TypeScript predicate under `node` against a claim this leg wrote (and the reverse). Both red on a revert to seconds. |
| **P1** `tests/test_14350_drain_convergence.py` was registered in NO surface, so the D1 suite ran nowhere and `manifest-integrity` (a required leg) refused the PR | **already fixed at `bc157acb`**: dual-registered `api` + `core`. `tools/ci_selection.py --integrity` reports every test file classified. |
| **P2** the takedown test's stated mutant cannot red it, and the acquisition-time restore call site was never reached | **fixed here, both halves.** The stated mutant was wrong: compare-then-unlink DOES take over an expired claim with a single writer, so it produces exactly the asserted state; the line now names the mutant that does red it (dropping the takeover arm). And the acquisition path's own `disposeQuarantine(moved.quarantine, moved.raw, path, raw)` — the one the review named — is now covered by a test that drives `takedownClaim`/`disposeQuarantine` directly (exported as the test seam, mirroring the Python twin's direct drive of `_takedown`/`_dispose_quarantine`): a successor claim installed underneath the taker is RESTORED byte-for-byte, and one the taker did judge is dropped. Measured: deleting the restore arm reds this test **and** the release-path twin added at `c214f34`. |

The review's last mutation-table row ("`tortoise-capture.test.ts` … stays GREEN") is
what the P2 row above removes: the row now names the behaviour it protects, and
the mutant reds it.

The branch's own lint findings are cleared at `b1822f8`: `ruff check` over every Python
file this diff touches reports **no findings** (the 13 the `agent-infra-ci / lint` job
listed were 12 here, now fixed, plus `tests/test_13098_cypher_inventory.py`'s `I001`, which is
**already red on `main`** and is tracked as #14426 — the decision there is to keep it out of
unrelated PR diffs, so it is named rather than fixed here). That context is **not** in the
required set, so it cannot block the merge; it stays red only on that `main`-side row.

**Cycle 5** (a second fresh reviewer, at `ffc6d3fa`, posted as review comment `7114708794`) —
1 **P1**, 6 P2. Five fixed here, two corrected in the record:

| finding | disposition |
|---|---|
| **P1** `read_claim` was not `lstat`-gated, so a FIFO/socket at the claim path blocked FOREVER | **fixed**: it now routes through `_read_raw`, the gated reader `acquire` already used — measured before the fix: `read_claim` never returned (killed at 8 s) while the TypeScript twin answered in ~0 s, and `read_claim` is what `renew`, `_claim_still_ours` and `holder()` call, i.e. the drain, the health line and every later pass. |
| **P2** the FIFO test asserted on an ABSENT path (a tautology — the mutant it named was the shipped code) | **fixed**: the read runs under a watchdog while the FIFO is still at the path. Verified by running the mutant: an ungated `read_claim` REDS it. |
| **P2** `release` could dispose of a claim it did not own (three-process interleaving) | **fixed**: it reads the token first and returns `False` without touching the file when the claim is not ours; the takedown stays for the ours branch. A new test pins that a not-ours release does not even change the file's inode/mtime. |
| **P2** an out-of-range-but-finite `expires_at` raised `OverflowError` out of `flush_spool` (`time.gmtime` outside its try/finally) and, being honoured by rule 2 as never-expiring, blocked the spool PERMANENTLY | **fixed**: the format is guarded, and a test builds that record end-to-end and asserts the operator line still renders. |
| **P2** the Pi leg could run UNCLAIMED when both takeover attempts lost a race (path momentarily empty → read as "cannot create") | **fixed**: `acquireDrainClaim` reports `state.raced` and `flushSpool` fails CLOSED on it, as the Python leg does for any `None` from `acquire`. A test pins the OTHER arm (an unmakeable root is NOT a lost race) so the documented "run unclaimed and say so" cannot be swallowed by the flag. |
| **P2** `renew`'s post-write read-back does not close the takeover race it claimed to — the replace IS the clobber | **record corrected, not changed.** Reproduced (a peer record installed between the guard read and the replace is lost; `renew` returns `True`). The comment now states exactly what the read-back covers and what it does not; the residual is below. ⚠️ **SUPERSEDED at cycle 6**: the residual below is CLOSED on both legs rather than documented — see the closed residual. |
| **P2** `DRAIN_MAX_IN_FLIGHT` is read by no code and bounds 1 POST per PASS, while a TARGETED pass takes no claim | **record corrected**: the constant's docstring now says the ceiling is per pass, that the claim is what serialises passes, that the box's worst case is **2** in that overlap, and that no test asserts the per-pass property itself. |

**Cycle 6** (a fresh-context reviewer at `a61c123644`, merged with a peer lane's
overlapping cycle-5 work at `818505a9`) — the two lanes reviewed the same branch
concurrently and fixed overlapping findings; this is the merged state, with each
finding fixed once and the duplication removed rather than merged as-is.

| finding | disposition |
|---|---|
| **P1** an UNCLEARABLE obstruction at the claim path collapsed into *contention*, so the drain wedged forever naming a holder that does not exist, and `claim_unavailable` was unreachable | **fixed** (this cycle only): the `DrainClaimUnavailable` arm branches on the object's TYPE — a regular file we cannot read stays `held_by_claim`, anything that cannot be a claim record (non-empty directory, FIFO, device) sets `claim_unavailable`, runs the pass unclaimed, and names the path in the new `claim_unavailable_reason`. The operator line prints that instead of a hard-coded "unwritable spool root", which was a false diagnosis of a real failure. Two `flush_spool`-level tests; the mutant (`S_ISREG` → `True`) reds the obstruction case and the fail-closed case is pinned separately. |
| **P1** `renew`'s verify-after-write reads back the bytes it just wrote, so it cannot close the race its own comment claims — a peer's fresh claim is clobbered and two drainers hold the spool | **fixed, both legs** — see the closed residual below. The peer lane had recorded this as a residual ("record corrected, not changed"); the measurement shows the cost argument does not hold, so it is closed instead of documented. |
| **P2** the Pi leg rendered the capacity cause as the raw code (`429 capture_capacity_saturated`) while Python rendered `429 capture capacity` — over the same `status.json` | **fixed**: `refusalLabel` gained the capacity arm. A cross-leg test renders the health line on the Pi side and asserts the words; the mutant reds it with the raw code. |
| **P2** `""` was "untargeted" for the claim and the filter but "targeted" for the probe guard, so an empty session id drained probes too | **fixed**: one predicate (`not only_session_id`) at all three sites, which is what the docstring above already claimed held. |
| **P2** the `flush_spool` docstring was left grammatically broken where the `""` paragraph was inserted | **fixed**: the two fragments are re-joined and the untargeted/targeted rule reads in one sentence. |
| **P2** R3/R6 were stated as applying to *both legs* while only Python yields and retries | **fixed in the contract**: R3 and R6 now state the per-leg yield and why, so a future reviewer cannot re-derive the asymmetry as a defect. |
| **P2** the measurement harness carried two dead constants and cited a constant that does not exist (`_CAPACITY_CAPACITY_RETRY_AFTER_S` for `_CAPTURE_CAPACITY_RETRY_AFTER_S`) | **fixed**: the unused `RETRY_AFTER`/`CAPACITY_BODY` are deleted and the name is corrected. |
| **P2** the receipts in this record were stale | **fixed**: re-measured at this head (51 / 740 / 302), and `WORKTREE-REPORT.md` carries the commands verbatim. |

**Cycle 7** (a fresh-context reviewer at `745b0cd505`, i.e. on the merged state) —
1 **P1**, 4 P2. The P1 is a real defect the merge INTRODUCED: each lane had fixed
one leg, and the pair disagreed. All five are fixed here; three of the five were
false claims in this record, which is the half that matters as much as the code.

| finding | disposition |
|---|---|
| **P1** the Pi leg had no answer for an UNCLEARABLE obstruction at the claim path: a non-empty directory cannot be cleared (`rmdir` → `ENOTEMPTY`) and cannot be a claim record, but the caller read `existsSync` as "somebody holds it" and reported a **phantom holder** — every pass, forever. The Python leg had just been taught to discriminate; the merge left the two legs disagreeing on the very property R6 names. | **fixed, mirroring the Python leg exactly**: `acquireDrainClaim` now names the obstruction on its existing `state` seam, `flushSpool` answers it BEFORE the fail-closed arm (run the pass UNCLAIMED, `claimUnavailable` + `claimUnavailableReason` naming the path), and the operator line prints that reason instead of a hard-coded "the spool root is not writable", which was a false diagnosis. Three causes, three answers, both legs: unreadable REGULAR file → contention (fail closed); unclearable stranger → unclaimed + named; nothing at the path → unclaimed + named. Two new tests; each arm's mutant reds it (dropping the obstruction arm, and deleting the fail-closed arm). |
| **P2** the Pi leg's `renew` CAS had **no test that could fail**: the only test called `linkSync`/`renameSync` directly on its own files, so reverting the install to `renameSync` left the suite green | **fixed for real**: `renew` now honours the same `takedown` seam `release` uses, and a new test stands INSIDE the window (take the claim, install a peer's record, assert `renew` returns false and the peer's token is on disk). Measured red on `linkSync` → `renameSync`. The primitive test keeps its own honest claim and says it is the primitive. |
| **P2** `test_the_claim_is_written_COMPLETE_never_visible_empty` could not see its own mutant: both assertions were post-hoc, and a create-then-write claim satisfies them | **fixed**: the assertion is now the ORDER — a wrapper around `os.link` reads the SOURCE bytes at the instant the name becomes visible, so the test asserts that what a racing reader could see is a complete record (and that a `link` happened at all). Measured red on the create-in-place mutant. |
| **P2** the takedown test named a mutant that cannot red it (a revert of `acquire`'s takeover leaves the primitives it drives unchanged) | **corrected in the record, not in the code**: the stated mutant is now the one that does red it (dropping `_dispose_quarantine`'s byte compare), and the line says plainly which test covers the acquire path instead. |
| **P2** the convergence measurement counted winners over a population it never proved overlapped: the winner held a FIXED 1.0 s, so on a loaded box a slow starter legitimately took the released claim and was counted as a second winner | **fixed**: the processes now rendezvous on a barrier before attempting and the winner holds until every peer has FINISHED its attempt, so the count is over a population that provably overlapped. ⚠️ Measured both ways: with the barrier removed the run reports **9 winners of 10** — the exact false "two drainers" verdict a correct protocol would have been blamed for; with it, **1 of 10**. The receipt now also publishes `overlapped` so a run that did not overlap says so. |

⚠️ The Pi-leg byte changes are another re-cut of generation 33's digest — see the
note beside `_PINNED_PI_SEAM_INSTALL_CONTRACT`.

**A further fold** of the peer lane brought two more of its fixes: a lease that lapsed
DURING our own scan is re-armed rather than reported as a takeover (the pass was
stopping itself over its own clock), and the required aggregate's `needs:` citations were
re-cut after `main` moved them. Both kept as-is; the census pins and Appendix A are
re-cut on this merged tree (1 327 rows, **0 rows drifted**, because the fold changed no
statement site), and `SWEEP_RESIDUE` stays `(11901, "953c70c0…")` — measured on a
detached `origin/main` worktree too, so it is `main`'s drift and not this branch's.

**Cycle 8** (a fresh-context reviewer at `5e9e89d0` — the merged head) — 1 **P1**, 4 P2.
The P1 is the one that mattered most, because it was a hole in THIS PR's own fix:
the exclusion being off was reported only on a stderr that nobody reads.

| finding | disposition |
|---|---|
| **P1** the "run unclaimed and say so" answer said so ONLY on stderr, and the primary firing path discards it (`session-start.sh`: `nohup … session drain … >/dev/null 2>&1 &`). A pass that ran without the one-drainer exclusion could file NOTHING, so no other rule wrote a record either — and the box reported `capture: OK`/`DRAINING` while N drainers POSTed into the same 8-slot ceiling. | **fixed on both legs, durably**: the status record gains `claim_unavailable` + `claim_unavailable_reason`, DECORATED onto whatever state the pass earned (a quota-blocked box with a broken claim path keeps both facts), and the health line carries `, NO drain exclusion (<reason>)` in byte-identical words on both legs. Self-healing in the other direction — a pass that HELD the claim clears it — because a one-way latch teaches an operator to ignore the line. Two tests per leg; both mutants red (dropping the record arm; keeping the stale field on every write). |
| **P2** `_dispose_quarantine`'s failed restore deleted a live claim with no trace, and the sentence justifying it ("the displaced holder … stops at its next refresh, which is the safe direction") was FALSE — it keeps draining until that refresh, up to TTL/3 | **fixed**: the failure now WARNS with the cause on both legs, and the sentence states what actually happens (a bounded double-drain, `ttl/3`, not "the safe direction"). The bound is quoted by a function (`_ttl_third_hint`) so the prose and `DEFAULT_TTL_S / RENEW_FRACTION` cannot drift — the earlier comment named a constant that does not exist under that name. |
| **P2** the measurement's `overlapped` field could never report what its comment claimed (`min(arrivals, done + winners)` is always `processes`), and a winner that timed out released EARLY — after which a straggler legitimately took the claim and was counted as a second winner | **fixed**: the winner prints the bound it observed (`RELEASED <bounded>`), an early release makes the run **no verdict** (`bounded: false`) instead of a false "two drainers", and `overlapped` reports the population that actually attempted. |
| **P2 / P2** two stale citations in the test docstrings — one named a capacity test that never existed under that name, the other named an `acquire`-path test that does not exist at all | **corrected**: both now cite tests that exist, and the second says plainly that the nearest cover asserts the GIVE-UP rather than the restore. That is the same overclaim class this PR has now corrected four times; a citation to a nonexistent test is how it survives a review. |

## Residuals, stated

* **Generation 33 is introduced by this PR and has never shipped**, so its recorded
  seam digest in `test_capture_install.py` was updated in place at each head
  (capture half `3eef418b…`, re-cut at every byte change: for the export-only test seam, for
  the peer lane's `release`/`raced` behaviour fixes, for this leg's behaviour
  changes, and for the MERGE of the two — a merge is a byte set neither side
  hashed, so the digest is always derived from the merged tree; the read half moved
  `708b6a…`→`904d2742…` because its generation MARKER moved 32→33 with the pair)
  instead of
  spending a generation 34 on a refinement of the same unreleased seam. From the
  moment 33 reaches `main` the entries are frozen and a byte change needs 34.
* ~~**A `renew` that lands inside the read → replace window can overwrite a peer's
  fresh claim**~~ — **CLOSED at cycle 6, on both legs.** Cycle 5 recorded this as a
  residual on the argument that a compare-and-swap would "add a rename + read to
  every renew". That argument does not survive the measurement: `renew` runs from
  `renew_if_due` at TTL/3 intervals (≈40 s), NOT per entry, so the added `rename`
  the takedown already performs is not on any hot path — and the cost of being
  wrong was two drainers on one spool, which R1 forbids outright. The refresh is
  now a real CAS on the inode (takedown-aside → byte compare → `link` install;
  `EEXIST` means the peer's claim stands), so the window is gone rather than
  bounded. A test drives the real `_takedown`, injects the peer's claim, and
  asserts the peer's wins; the mutant (`link` → `os.replace`) reds it.
* **The per-pass ceiling is 1 POST, and a box can hold 2** when a TARGETED pass (a
  live session's own shutdown filing, which takes no claim by design) overlaps the
  claim holder. `DRAIN_MAX_IN_FLIGHT` is a stated ceiling read by no code; the claim
  is what serialises UNTARGETED passes.
* **`acquireDrainClaim`'s `state.raced` flag has no forcing test.** The flag is set
  at the three sites where a takeover is lost and consumed by one `if` in
  `flushSpool`; the arm that IS tested is the neighbouring one (an unmakeable root
  is not a race). Forcing the interleaving needs a peer process inside the two
  acquisition attempts, which this suite does not stage.
* **A wedged pass holds the claim until its lease (≤120 s).** That is what a lease is; the bound is derived from the transport (`REQUEST_TIMEOUT_MS` 10 s / `urlopen` 30 s), and a real fetch always ends. A hung *mock* does not — which is why the #2100 case above had to release it.
* **A claim we cannot READ is HONOURED, not taken over** (both legs): a transient
  local error on a regular claim file is not evidence about its holder, and the
  same reasoning applies to anything at the claim path that is not a regular file
  (it is cleared as an object, never read — reading a FIFO blocks forever). The
  cost is that a permanently unreadable claim file blocks the drain until a human
  looks, which is the fail-closed direction the issue's "never two drainers" rule
  prefers over the alternative.
* **A targeted filing is exempt from the claim.** It is one POST, so the ceiling (1 slot) is respected; the exemption buys the live session's own shutdown filing never being queued behind a backlog.
* **The Pi leg does not yield on a capacity stop** — it ends the pass and lets the next opportunity continue, because its pass is `await`ed on `session_shutdown` and must not sleep. Both legs charge nothing. This asymmetry is deliberate and is stated in the code.
* **A non-`capture_capacity_saturated` 429 is also treated as box-level** (status triggers, code names). Narrowing to the typed category would leave the defect alive on any server that does not send one.
* **`retry_after` is capped at `RETRY_BASE_SECONDS`** when yielding, so a corrupt body cannot park the drain (and hold the claim) for hours.
* **The convergence measurement is a reproduction of the MECHANISM, not of the production backlog** (60 entries vs 908): this box has no capture spool, and the handoff's counts are per-box.

Part of #14342. Closes #14350 (D1) · #12992's starvation half.

**Tracking:** this PR is the implementation record for **D1 (#14350)**.

## Contribution license

* [x] I donate this contribution under the BSD 3-Clause License (fleet-authored change).
