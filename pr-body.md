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
| **One drainer** | `tortoise/drain_claim.py` (new): a lease on `<spool>/drain.claim`, taken by an **untargeted** `flush_spool` pass. A second drainer returns immediately for the SPOOL (`held_by_claim`, zero spool POSTs) — nothing is lost, the holder is working the same oldest-first list. ⚠️ It does **not** skip the separate session-events leg of the same command; see the residual below, and #12992 for that leg's contention. |
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
* **R3** — a `429` capacity refusal charges no entry (`attempts`, `next_attempt_at_ms` untouched) and ends the pass; the pass retries the *same* entry after a bounded yield.
* **R4** — no behaviour change for a spool whose box is not saturated and whose drain is the only one running (guarded: the free-server measurement and the existing suites).
* **R5** — nothing here writes a raw captured turn to the graph, moves extraction into the drain, or changes the spool's on-disk format.
* **R6** — both legs implement ONE protocol; a divergence is a defect.

## Tests

```text
# 50 cases — the claim protocol, the capacity rule, the falsifier, 4 CROSS-LEG,
# the three that pin the renew CAS, and the fail-open directions cycle 5 closed
TORTOISE_TEST_CARVE_OUT=1 uv run python -m pytest tests/test_14350_drain_convergence.py -q
50 passed

# capture family + install-contract pins (generation 33) + store_sync + doctor
TORTOISE_TEST_CARVE_OUT=1 uv run python -m pytest tests/test_capture_spool.py \
    tests/test_14350_drain_convergence.py tests/test_12483_12487_spool_integrity.py \
    tests/test_12992_capture_capacity_typed_refusal.py tests/test_capture_backlog_4197.py \
    tests/test_12379_unrecorded_trace.py tests/test_capture_install.py \
    tests/test_store_sync_worker.py tests/test_doctor.py -q
739 passed

# the four cross-leg cases run the REAL TypeScript predicate under `node`, so the
# seam is graded by both implementations rather than by one leg's opinion of it
node --test tortoise/pi-hooks/tortoise-capture.test.ts tortoise/pi-hooks/tortoise-volunteer.test.ts
302 pass / 0 fail
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
| **P2** `renew`'s post-write read-back does not close the takeover race it claimed to — the replace IS the clobber | **superseded by cycle 5b, then FIXED**: a second, independent reviewer of the SAME head found the same race and rated it P1 (see below), so the disposition moved from "record corrected" to a code fix. ⛔ The "rename + read on the per-entry refresh path" cost this row cited is measured FALSE: the per-entry hot path is `renew_if_due`, which returns before touching disk unless a third of the TTL has gone, so `renew` — and its compare-and-swap — runs at most once per ~40 s per holder. |
| **P2** `DRAIN_MAX_IN_FLIGHT` is read by no code and bounds 1 POST per PASS, while a TARGETED pass takes no claim | **record corrected**: the constant's docstring now says the ceiling is per pass, that the claim is what serialises passes, that the box's worst case is **2** in that overlap, and that no test asserts the per-pass property itself. |

**Cycle 5b** (a THIRD fresh reviewer, independently, also at `ffc6d3fa`) — 1 **P1**, 1 P2, four
non-blocking. Two reviewers of one head disagreed on severity for the same race, and the fix landed:

| finding | disposition |
|---|---|
| **P1** `renew` could still overwrite a successor: read the record, then `os.replace` its own, and the read-back read back THE BYTES THE PROCESS HAD JUST WRITTEN — so it returned `True` while two drainers POSTed. R1's whole point | **fixed, both legs.** Renewal is now a real compare-and-swap, reusing the takeover's own arbiter: `_takedown` moves the record aside (exactly one contender can), the bytes are verified IN QUARANTINE, and the refreshed record is installed with `link` — which fails `EEXIST` when a peer installed a claim in that window, so the SUCCESSOR WINS. Expiry is judged with a clock read taken AFTER the takedown, so a lease that ran out during the window is never resurrected; the expired branch RESTORES the stale record rather than deleting it, so the peer finds the claim it expects instead of a hole; and a failed install restores the record and reports through `_claim_still_ours`. Three new Python cases and two new TS cases, each mutation-checked: dropping the quarantine token check reds; installing with `replace`/`rename` reds; disposing instead of restoring reds. |
| **P2** the loser of the claim is not a no-op as printed: `held_by_claim` only PRINTS, and the same command still runs the session-events leg, which posts to `/v1/sessions` and consumes capture slots. | **corrected, not coded.** The spool half of R1 holds — the spool is serialized and the loser makes no spool POST — but the session-events leg is a different substrate whose serialization is outside D1's scope (and stopping it early would starve its backlog on a short holder pass). The `One drainer` row now says "zero SPOOL POSTs", a residual states the leg, and the evidence is recorded on **#12992**, the open root for "normal fleet activity saturates a gate of 8", rather than filed as a peer issue. |

The four non-blocking observations: the Python leg reporting an unreadable claim without naming a
holder (the TS leg names it) is a real honesty gap, **filed as #14437** rather than fixed here; the
corrupt-record `boot_id` rule divergence is unreachable from either writer; `renew_if_due` returning
`True` without an ownership check is the documented design; and the Python transport confirming a
`429` before `capacity_refusal` inspects it is pre-existing behaviour that costs one round trip and
changes no verdict.

## Residuals, stated

* **Generation 33 is introduced by this PR and has never shipped**, so its recorded
  seam digest in `test_capture_install.py` was updated in place at each head
  (capture half `314c4dc7…`, re-cut while 33 is still unreleased — once for the export-only
  test seam above, and once on the merge that united two cycle-5 reviews, where the CAS `renew`
  and the FIFO/release/lease-format fixes are all real bytes; the pinned value is the MERGED
  tree's, measured by the suite, never either parent's. The read half moved `708b6a…`→`904d2742…`
  because its generation MARKER moved 32→33 with the pair) instead of spending a generation 34 on
  a refinement of the same unreleased seam. From the
  moment 33 reaches `main` the entries are frozen and a byte change needs 34.
* **The claim serializes the SPOOL, not the whole `session drain` command.** When a
  second drainer loses the claim it makes no spool POST and says so, but the same
  command still runs the separate session-events leg (`tortoise/__main__.py`,
  `drain_session_events`), which posts to `/v1/sessions` and therefore still holds
  capture slots. That leg has no claim of its own and never had one; serializing it
  is out of D1's scope (stopping it early would starve its backlog on a short holder
  pass), and it is recorded with evidence on **#12992**, whose root it shares.
* **The read → replace window is CLOSED, not stated.** Earlier cuts of this record
  carried it as a residual ("two drainers can coexist until the loser's next
  `renew_if_due`"); the cycle-5b fix made renewal the takeover's own compare-and-swap,
  so a successor installed in that window now WINS and the slow holder reports "not
  ours" instead of clobbering it. The only cost is that the claim path is briefly
  absent during a renewal (once per ~40 s), where a peer may install its own claim —
  which is the safe direction: one holder, never two.
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
