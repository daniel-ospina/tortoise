---
title: "Runbook — draining the parked capture spool after #14261"
type: operations
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: capture-spool, session-drain, capture-ingest
aboutObjects: tortoise, "#14232", "#14261", "#14342", "#11081", tools/spool_drain_rehearsal.py, tortoise/capture_spool.py, tortoise/hosted_api.py
created: 2026-11-23
updated: 2026-11-23
---

# Runbook — draining the parked capture spool after `#14261`

> **What this is for.** `#14232`'s defect (fixed by `#14261`, merged 2026-11-23)
> was that `POST /v1/sessions` waited for extraction and died at the 10 s
> transport bound (`#3834`) with `stage=unmarked`. The capture spool on the
> affected box grew to hundreds of sessions that could never be filed
> (`~18.6M` tokens of extraction debt). Now that the endpoint ACKs inside the
> budget and extracts behind it, that backlog CAN be drained — and this runbook
> is the path, rehearsed end to end against a LOCAL graph before it is pointed at
> anything real.
>
> **Who can run it.** Someone who has read nothing else. Every command is literal;
> every stage has one observable and one pass/fail reading.
>
> **Rehearsal evidence:** `docs/evidence/14232-spool-drain-rehearsal/` —
> `SUMMARY.md`, one JSON per mode, and `corpus-cost.json`. Regenerate with
> `uv run python tools/spool_drain_rehearsal.py all`.

---

## ⛔ The five decisions this runbook obeys (and where each is enforced)

| # | Decision | Enforced by |
|---|---|---|
| 1 | **Never delete a spooled transcript.** The spool is the only copy. | `capture_spool`'s own rule; `flush_spool` never unlinks a terminal entry. Rehearsal: `tools/spool_drain_rehearsal.py` copies and fingerprints the source (before/after must be identical). |
| 2 | **Never drain against production to test.** | Rehearsal refuses a non-loopback `TORTOISE_DB_URI`, refuses `api.premiselabs.co`, refuses `live-v5`. |
| 3 | **Never write to `live-v5`.** | Same gate: a graph name containing `live-v5` is refused. |
| 4 | **Never raise the 10 s wait bound** (`#3834`). | Nothing in the runbook sets it. The ACK split is what makes the bound irrelevant, not a bigger bound. |
| 5 | **No capture caps** (`#4010`). | No cap is introduced; a `capture_capacity_saturated` refusal is a *retryable deferral*, never a discard. |

A sixth, operational: **the drain is idempotent by `capture_key`**, so a re-run is
always safe and is the recovery path for every interruption (see *When it goes
wrong*).

---

## Stage −1 — know what you are draining (read-only, 10 s)

```bash
SPOOL="${TORTOISE_CAPTURE_SPOOL_DIR:-$HOME/.tortoise/capture-spool}"
ls "$SPOOL/entries"/*.meta.json | wc -l          # entries
ls "$SPOOL/entries"/*.turns.jsonl | wc -l        # transcripts (must equal the above)
du -sh "$SPOOL"
python3 - "$SPOOL" <<'PY'
import json, glob, sys
c = {"filed": 0, "unfiled": 0, "terminal": 0}
for p in glob.glob(sys.argv[1] + "/entries/*.meta.json"):
    m = json.load(open(p))
    c["filed" if m.get("filed_key") else "unfiled"] += 1
    if m.get("terminal_failure"):
        c["terminal"] += 1
print(c)
PY
```

**Expected / pass:** `meta == jsonl`. `filed + unfiled == entries`.

⚠️ **`filed` is not zero on a real spool, and it changes the arithmetic of the
whole drain**: an entry that already carries `filed_key` is short-circuited
BEFORE the network (that is the idempotency contract), so it will not appear as a
POST. **Expected POSTs = `unfiled`**, not `entries`. Measured on the rehearsal
corpus: 265 entries = 264 unfiled + 1 filed → exactly 264 POSTs. If you read the
drain line's `skipped N` as loss, you will chase a non-defect.

**Rollback:** none needed — this stage only reads.

---

## Stage 0 — take the spool's fingerprint (30 s, and you will keep it)

```bash
SPOOL="${TORTOISE_CAPTURE_SPOOL_DIR:-$HOME/.tortoise/capture-spool}"
find "$SPOOL/entries" -type f | sort | xargs sha256sum > /tmp/spool-before.txt
sha256sum "$SPOOL"/entries/*.turns.jsonl | sort | sha256sum   # THE transcript digest
```

Record the file count (`wc -l < /tmp/spool-before.txt`) and the transcript
digest.

**Pass:** you have both numbers written down somewhere outside the box.

⛔ **Do this before anything else touches the spool.** The acceptance criterion
for the whole operation is that the **transcript** files (`*.turns.jsonl`) are
byte-identical afterwards — **NOT the whole tree**. A real drain rewrites each
entry's `meta.json` (`filed_key`, `attempts`, `next_attempt_at_ms`), so a
whole-tree aggregate WILL change, and that is the drain working rather than data
loss (Stage 4 says the same thing from the other end). The transcript is the only
copy; the bookkeeping beside it is not.

---

## Stage 1 — a private graph (never the shared instance)

The drain itself does not need a graph — it POSTs to an API. **You need a graph
only if you are rehearsing.** Where the real graph lives is decided by the API
you point at, not by this runbook.

```bash
# REHEARSAL ONLY — a throwaway FalkorDB owned by this worktree (#5084)
eval "$(uv run python tools/test_lane.py uri)"
echo "$TORTOISE_DB_URI"        # must be 127.0.0.1:<ephemeral port>
```

**Pass:** the URI is loopback and the port is NOT `6379` (that is the shared
instance every lane shares; graphs from other lanes' runs pollute the counts).

**Rollback:** `uv run python tools/test_lane.py down` from THIS worktree. The
container holds nothing you need — the source of truth is the record, and the
spool is untouched.

---

## Stage 2 — rehearse against the local API (the whole point)

```bash
uv run python tools/spool_drain_rehearsal.py all      # 4 modes, ~10 min
uv run python tools/spool_drain_rehearsal.py run --mode head-mock   # or one
```

A single-mode `run` re-measures only that mode and MERGES: the other three
`<mode>.json` files and their summary sections are kept, so you cannot lose the
committed evidence by re-running one mode against `--out`. `corpus-cost.json` is
rewritten only when the run drained the WHOLE corpus — `pre-14261` defaults to a
3-entry slice and `backpressure` to 4, so those keep the recorded cost (and
refuse when there is none, rather than describe a corpus no mode drained).

The tool, in order: fingerprints the source spool → copies it to a temp dir →
seeds a local tenant and mints a key → starts `hosted_api` on an ephemeral
loopback port → runs the REAL `tortoise session drain --retry-terminal` against
the copy → reads the spool's own bookkeeping and the graph back → runs a SECOND
drain pass to prove idempotency → re-fingerprints the source.

**Pass, all four:** `invariant_source_untouched: true` in every `<mode>.json`,
and the printed summary reports the expectations in the table below.

| mode | what it proves | expected |
|---|---|---|
| `head-keyless` | the drain on a box with no extraction provider | `POST`s == `unfiled` (measured 264 of 265 entries, 1 already filed); `filed_key` == entries MINUS those a `429` parked (measured **215/265**); `terminal 0`; graph `capture_ok 0`, `extracted points 0`, `entities 0`. ⚠️ An unpaced run IS expected to park a share of the corpus on the key budget — that is the Stage 3b finding, not a failure |
| `head-mock` | the same with the extraction pipeline RUNNING (offline `MockModel`, zero spend) | `POST`s == `unfiled`, all `200`, **zero 504s**, `phase in flight → 0` on its own, extracted points > 0 |
| `pre-14261` | what the old defect looks like now | `504` with a `stage=capture_*` string (not `stage=unmarked`). ⛔ The entry is **NOT** deferred: the `504` arrives AFTER the record landed, so the client's confirm path files it — measured `filed_key 3/3`, `backoff_waiting 0`, `attempts_max 0`. A `504` is a lost **response**, not a lost capture, and the spool row above is where you read which |
| `backpressure` | the `#12992` refusal from ONE drainer against a 1-slot ceiling, and that it is retryable | `429 capture_capacity_saturated`, entries deferred with `attempts` > 0, **terminal 0**, nothing removed |

⚠️ **`graph extraction.status: stored` is not an observable of this tool** —
`_graph_state` reads `sessions`, `turn points`, `extracted points`, `capture_ok`,
`phase in flight` and `entities`, and `extraction.status` is never read, so a
table row asking you to grade it cannot be graded. Grade `head-keyless` on
`capture_ok 0` / `extracted points 0` / `entities 0`, which is what the tool
prints.

⚠️ **The 50 park in `head-keyless` is 19 % of the corpus, not a rounding
error.** The command above is the unpaced fast run, so the key budget refuses a
slice of it and parks those entries for ~6 h — the single operational finding
this rehearsal produced (Stage 3b). A `head-keyless` run that files everything
means the corpus was small enough to stay under 100 POST/min, not that the
drain behaves differently.

**Rollback:** none — nothing outside the temp dir and the throwaway graph was
written.

---

## Stage 3 — the real drain (the operation)

Point the drain at the **real** API. Two credentials are needed and they are not
the same thing:

```bash
# the API the drain POSTs to, and a key with capture permission
export TORTOISE_API_URL='https://api.premiselabs.co'
export TORTOISE_API_KEY='…'          # must be a WRITE-capable key

export TORTOISE_CAPTURE=1            # consent — the drain is a TRANSMISSION verb
```

### Prove the KEY can write — with a capture probe, not the GitHub preflight

⛔ `tools/gh_write_preflight.py` proves the **GitHub** credential can write
repository contents. It measures **nothing** about `TORTOISE_API_KEY`, and a
`GH-WRITE-OK` line over an unmeasured key is exactly the false PASS this section
exists to prevent. The drain's credential is proven by one capture POST that is
then deleted:

```bash
PROBE_ID="runbook-key-probe-$(date -u +%s)"
code=$(curl -sS -o /tmp/key-probe.json -w '%{http_code}' \
  -X POST "$TORTOISE_API_URL/v1/sessions" \
  -H "Authorization: Bearer $TORTOISE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$PROBE_ID\",\"harness\":\"runbook\",
       \"source\":\"runbook-key-probe\",
       \"conversation\":[{\"role\":\"user\",\"content\":\"key write probe — safe to delete\"}]}")
echo "$code"; cat /tmp/key-probe.json
# 200/201 = the key holds graphs:write and the capture door accepted it.
# 401/403 = wrong or read-only key → STOP.  402 = quota.  422 = body shape.
curl -sS -X DELETE "$TORTOISE_API_URL/v1/sessions/$PROBE_ID" \
  -H "Authorization: Bearer $TORTOISE_API_KEY"
```

**Pass:** the POST answered `200`/`201` and the DELETE answered 2xx. A `404` on
the DELETE means the server assigned a different id than the one sent — read
`/tmp/key-probe.json`, delete that id, and treat the probe as unresolved until it
is gone.

⚠️ A **read** probe proves nothing here: `GET /v1/sessions` (and `tortoise
session list`) answers 200 for a key minted `graphs:read`, and that key 403s on
every drain POST. Two credentials, two different tools — if this drain is run
from a lane that must also push a branch, prove the *GitHub* credential
separately with `uv run python tools/gh_write_preflight.py --repo .`; its green
run is not a pass for the API key above.

⛔ **`session drain` is gated on capture consent** (`TORTOISE_CAPTURE`). Without
it the drain prints `spool drain: capture declined — …` and exits **0**. That is
a no-op, not a success: check the line, not the exit code.

Run it in the foreground first (the SessionStart hook runs a silent, backgrounded
drain; for the one-shot backlog you want to see the output):

```bash
tortoise session drain --retry-terminal 2>&1 | tee /tmp/drain-$(date -u +%H%M).log
```

### The stages, their observable, and their pass/fail

| # | Stage | Observable (where) | PASS | FAIL — and what it is |
|---|---|---|---|---|
| 1 | terminal re-arm | `spool drain: cleared terminal state on N entries` | N is any number (0 is normal) | — |
| 2 | POST batch | server-side: `POST /v1/sessions` → `200` | all `200` | `504` → extraction outran the ack window; the entry is **deferred, kept**; re-run (idempotent) |
| 3 | ACK honesty | `spool drain: outcome unrecorded N (…)` | N may equal the filed count — this is the ACK path saying extraction is behind the answer | ⛔ not a failure. It becomes one only if it is still `N` after Stage 5 |
| 4 | drain summary | `spool drain: filed F, deferred D, skipped S …, terminal T, discarded X` | `X == 0`; `T == 0`; `S == entries − unfiled` | `discarded > 0` → READ the `spool discard (<reason>)` lines; `terminal > 0` → a PERMANENT 4xx, see below |
| 5 | completion (no client action) | `GET /v1/sessions/{id}` → `extraction.outcome_known: true`, no `phase` | every session terminal | still `phase` after minutes → extraction worker stuck; the capture is still durable — do NOT re-drain blindly |
| 6 | the record | graph: `Session` count, turn `Point`s, extracted points | sessions == POSTs; turn points ≈ turns | fewer sessions than POSTs → **read `#14342` D2 before re-running** |
| 7 | idempotency | run the drain a second time | `filed 0`, `deferred 0`, seconds not minutes | a second drain that re-POSTs = the `capture_key` short-circuit is broken |

⛔ **Every failure above is recorded by the DRAIN, never by deleting anything.**
The single most important property of this operation: a failed entry stays on the
spool with `attempts` incremented and a backoff timestamp. Nothing in the drain
unlinks a transcript — `terminal_failure` marks it and KEEPS it.

---

## Stage 3b — pace it: the 100/min budget is the real ceiling (measured)

⛔ **This is the one thing the rehearsal found that changes how the drain must be
run, so it is not a footnote.** `hosted_api` installs
`RateLimitMiddleware(max_per_minute=100)` keyed on the **API key** — a sliding
60 s window that answers `429 {"detail":"Rate limit exceeded. 100 points/minute
per API key."}` with `Retry-After: 60`.

A drain posts as fast as it can. Measured on the SAME corpus, same box, in the
same rehearsal (`docs/evidence/14232-spool-drain-rehearsal/SUMMARY.md`):

| run | observed rate | outcome |
|---|---|---|
| `head-keyless` (no extraction, so fast) | **124.3 POST/min** | `214 × 200`, **`50 × 429`** |
| `head-mock` (extraction runs, so slower) | **80.6 POST/min** | `264 × 200`, **zero 429** |

Same work. The only difference is the pace, and the pace is what the limiter acts
on. **A 265-entry drain at full speed loses ~19 % of its POSTs to the limiter;
allocate for ~100/min, i.e. ≈6 min for 500 entries, not the ~2 min an unthrottled
run takes.**

### ⛔ The trap: a refusal parks the entry for up to SIX HOURS

A refused entry is deferred (kept, no loss) — and the writer sets
`next_attempt_at_ms = now + backoff_delay(attempts)`, where
`backoff_delay` = `min(30 · 2^(attempts−1), RETRY_MAX_SECONDS)` and
`RETRY_MAX_SECONDS` is **6 h** (`tortoise/capture_spool.py`).

**A parked spool carries high `attempts` already** — the entries here were at
7–11 from the production 402/429 storm — so ONE new refusal saturates the cap:

- measured: 50 entries parked at **21 471 s** (5.96 h) in `head-keyless`;
  2 entries at **21 596 s** (6.0 h) in `backpressure`.
- and a re-run immediately after does nothing, verbatim:
  `spool drain: filed 0, deferred 0, skipped 265 (50 waiting on backoff)`.

⚠️ **`--retry-terminal` does not help here.** It clears `terminal_failure` and
resets `attempts`/`next_attempt_at_ms` — but ONLY for entries that carry a
terminal marker (`clear_terminal_failure` returns `False` when the key is
absent). A rate-limited entry has no terminal marker, so there is **no supported
verb that re-arms a deferred entry**. The only supported recovery is to wait out
the window.

**Therefore: pace the first pass, or pay hours.** Practically:

1. Run the drain with a pace the key budget can carry (~90/min). If your client
   has no pacing knob, run it in **batches** — stop the process after ~90 POSTs
   (`Ctrl-C` is safe), wait 60 s for the window to slide, and run again. Each
   pass only attempts entries that are not in backoff, so progress is monotone.
2. If you do get rate-limited, **do not hammer it.** A second pass inside the
   window cannot succeed — the entries are held by backoff, not by the limiter.
   Check `spool drain: … skipped N (M waiting on backoff)`: `M > 0` means "come
   back later", not "retry harder".
3. The share that lands while extraction runs (`head-mock`) is the honest
   forecast for a real drain: an extracting session is slower per POST, and that
   slowness is what keeps it under the limiter.

### Reading "deferred" correctly

`deferred N` means **retryable, still on disk, will be attempted again**. There
are three shapes and they are all recoverable:

- `429 capture_capacity_saturated` (`#12992`) — the in-flight ceiling. A capture
  holds its admission slot for its WHOLE extraction, so the next POST of the
  SAME drain is refused while the previous capture is still extracting (that is
  what `backpressure` measured: ONE client, ceiling 1, 2 s extraction → 2 of 4
  refused with `in_flight 1 / limit 1`). It clears on its own — wait. A second
  drainer only makes it likelier; the rehearsal never ran two.
- `504` at the transport bound — the ack window was missed. Re-run.
- a transport error (`status=None`) — the spool backs off exponentially
  (`backoff_delay(attempts)`), so a later pass picks it up.

### Reading "terminal" correctly

`terminal N` means a **permanent** 4xx (402/403/422-class) was recorded and the
entry is deliberately not retried. The entry is still on disk. The supported exit
is `--retry-terminal` (which is why the command above carries it) **after** the
cause is fixed — a corrected lane/harness, a lifted quota, a re-minted key.

---

## Stage 4 — prove it (5 min)

```bash
# 1. the TRANSCRIPTS are byte-identical (this is the thing that must never change)
sha256sum "$SPOOL"/entries/*.turns.jsonl | sort | sha256sum   # compare to Stage 0
```

⚠️ **The aggregate digest of the WHOLE tree WILL change, and that is correct.**
The drain rewrites each entry's `meta.json` (`filed_key`, `attempts`,
`next_attempt_at_ms`), which is bookkeeping, not content. So a whole-tree
compare — `find … -type f | xargs sha256sum`, then `diff` of the two lists —
reports a difference for every filed entry and its `&& echo CONTENT IDENTICAL`
can never fire. It is not a stricter check; it is a check that always fails.
**Compare transcript digests, not the tree.**

```bash
# 2. sessions the API actually holds
curl -sS -H "Authorization: Bearer $TORTOISE_API_KEY" \
  "$TORTOISE_API_URL/v1/sessions" | python3 -c \
  'import json,sys; d=json.load(sys.stdin); print(len(d["sessions"]))'
```

**Pass:** transcript digest identical; session count rose by (approximately) the
drain's `filed` count; `discarded == 0`.

---

## Cost — what the drain spends, and against which allowance

Two separate costs, and conflating them is how a budget surprises you:

1. **Capture POSTs — near zero.** The record write is turns + a Session row. No
   model call happens inside the ACK (§2.0: the record is truth; extraction is
   behind it).
2. **Extraction — the real spend, one model call pair per session** (points +
   relations). This is the `18.6M`-token figure the backlog is described by, and
   it lands on the **session-extraction provider allowance** (`DEEPSEEK_API_KEY`
   / `TORTOISE_SESSION_LLM_MODEL`), not on the hosted API's own quota.

**Do not estimate it per-session from another box's spool.** Session size is
wildly corpus-dependent — measured on the rehearsal corpus (265 `pi` hook
sessions): **median 1 615 tokens, p90 2 977, p99 4 759, max 5 486, total 423 k**
(`tiktoken:o200k_base`). A different corpus's median is `36 415` (`#14277`) —
**~23× larger**. So:

```bash
# measure THIS box's spool before quoting a number (no network, ~10 s)
uv run python - <<'PY'
from pathlib import Path
from tools.spool_drain_rehearsal import _corpus_cost
print(_corpus_cost(Path.home() / ".tortoise" / "capture-spool"))
PY
```

`tokens_per_session.sum` is the extraction-cost estimate for that spool;
`top_decile_share_of_tokens` names how much of it the largest 10 % of sessions
carry (measured 21.7 % here — so "sample the big ones first" is not a saving,
it is the same cost in a different order).

### The 516-entry figure, stated both ways

The backlog is quoted as **516 sessions** at a **36 415-token** median
(`#14277`) ⇒ **≈18.8M tokens** of extraction, landing on the **session-extraction
provider allowance** (the key behind `TORTOISE_SESSION_LLM_MODEL` /
`DEEPSEEK_API_KEY`), **not** on the hosted API's own quota — the capture POST
itself is turns + a Session row and makes no model call inside the ACK.

If the production size distribution has the skew this corpus shows (top 10 %
≈ 22 % of tokens), then of that ~18.8M roughly **4.1M sits in the largest ~52
sessions** and **~14.7M in the remaining ~464** — i.e. the cost is broad, not
concentrated, and batching by size does not change the total. ⚠️ That is a
**projection from a different corpus's skew**, not a measurement: measure the
drain box's own spool with the command above before committing a budget.

**If no provider key is configured the extraction does not run at all**
(`extraction: {"status": "stored", "extractor": "none"}`), so extraction costs
nothing and the graph shows `extracted: 0` — that is a legitimate terminal state,
and it is the PATH `head-keyless` measures. It is **not** a claim that the drain
files everything: on the rehearsal corpus the same mode filed 215 of 265 entries,
because the key budget parked 50 of them (Stage 3b). Check the status string
before believing a token estimate, and do not read "keyless" as "costs nothing
and files everything".

---

## When it goes wrong

| Symptom | What it is | Do this |
|---|---|---|
| `spool drain: capture declined` | consent off — the drain did nothing | `export TORTOISE_CAPTURE=1`, re-run |
| `filed 0` everything | consent, auth, or an unreachable URL | `spool drain: nothing filed — …` names it; `tortoise session verify` reads the install |
| `404`/`401` on every POST | wrong host or a read-only key | re-check `TORTOISE_API_URL`; re-run the capture-POST probe in Stage 3 (NOT `gh_write_preflight.py` — that one proves the GitHub credential) |
| `429 Rate limit exceeded. 100 points/minute per API key.` | the KEY's request budget, not the capture ceiling | wait 60 s; drain slower / in batches (Stage 3b). Entries are deferred, not lost |
| `429 capture_capacity_saturated` | the in-flight ceiling (`#12992`): the previous capture's extraction still holds its slot | wait (`retry_after` 30 s); drain with ONE drainer paced under its own extraction latency (that is `#14342` D1). The rehearsal reproduced this with ONE drainer, so it is not evidence that a second client is needed to hit it |
| `… skipped N (M waiting on backoff)` | M entries parked by `backoff_delay`, up to 6 h | this is not a failure to fix by retrying — wait, or the first pass should have been paced |
| `504` with `stage=capture_extract` | ack window missed — the extraction outran it | re-run; idempotent. The entry is confirmed and filed anyway when the record landed |
| `terminal N > 0` | permanent 4xx recorded | fix the cause, then `--retry-terminal` |
| `discarded X > 0` | a permanent, non-retryable defect was recorded in `discarded.jsonl` | read `~/.tortoise/capture-spool/discarded.jsonl`; the entry is KEPT |
| sessions fewer than POSTs | the record gap (`#14342` D2) | stop; report on `#14342` |
| drain hangs | server-side, not the client | the client's POST timeout is 30 s; a hang past that is the server — stop with Ctrl-C (safe: nothing is deleted) |

### Rollback — how to stop without losing a transcript

**There is no destructive step to roll back.** In order of escalation:

1. **Ctrl-C / kill the drain process.** Safe at any point: each entry's state is
   written after its attempt, and an interrupted entry simply keeps its previous
   `attempts`/`filed_key`. Nothing is unlinked.
2. **Stop the API** (only if you are running the local rehearsal: `test_lane.py
   down`).
3. **Re-run the drain.** Idempotent by `capture_key`: a session already filed is
   short-circuited before the network, so a re-run files only what is missing.
   This is the recovery path for every failure in the table above.
4. **`--retry-terminal`** to re-arm entries that a permanent 4xx marked terminal.
5. **If you believe an entry is lost:** `~/.tortoise/capture-spool/entries/
   <key>.turns.jsonl` is the transcript and the drain never deletes it. Check it
   exists; grep `discarded.jsonl` for the session id. If the file is there, the
   content is not lost — only its filing is in question.

⛔ **Never `rm` a spool entry to "clean up".** An entry that keeps failing is a
finding to report (`#14232`, `#14342`), not garbage.

---

## Appendix — what the rehearsal actually measured

See `docs/evidence/14232-spool-drain-rehearsal/SUMMARY.md` (regenerate with
`uv run python tools/spool_drain_rehearsal.py all`). Verbatim headline numbers,
265-entry corpus, private local FalkorDB, `hosted_api` at `#14261`'s merge:

| mode | POSTs | statuses | rate | ack p50 / p95 | spool after |
|---|---|---|---|---|---|
| `head-keyless` | 264 | 214 × 200, **50 × 429 (key budget)** | 124/min | 331 / 412 ms | 215 filed, 50 parked ~6 h |
| `head-mock` | 264 | **264 × 200** | 81/min | 707 / 916 ms | **265/265 filed**, 0 parked |
| `pre-14261` | 3 | **3 × 504 @ 10 002 ms** | 9/min | — | 3/3 filed via the confirm path |
| `backpressure` | 4 | 2 × 200, **2 × 429 capacity (1/1)** | 70/min | 341 / 502 ms | 2/4 filed, 2 parked ~6 h |

What each result establishes:

1. **At HEAD the drain works.** `head-mock`: every unfiled entry POSTed, zero
   refusals, zero discards, zero terminal, `265/265` `filed_key`, extraction
   reached terminal **on its own in 2.0 s**, and the second pass filed nothing
   (`sessions Δ 0`) — the `capture_key` idempotency proof.
2. **The count arithmetic is the trap, not the drain**: `entries (265) −
   already-filed (1) == POSTs (264)`, and `skipped` is that subtraction.
3. **The old defect is reproducible and now says WHERE**: with the ACK join
   disabled and a 12 s extraction, all three POSTs answered `504` at
   10 002–10 006 ms with `"stage":"capture_extract"` and a `stages_ms`
   breakdown — where production used to report `stage=unmarked spent_ms={}`.
   The 504 arrived AFTER the record had landed, so the client's confirm path
   filed all three anyway: **a 504 is not a lost capture.**
4. **The refusal vocabulary is distinct and must be read apart**: the key-budget
   429 (`Rate limit exceeded. 100 points/minute`) and the capacity 429
   (`capture_capacity_saturated` with `in_flight`/`limit`) are different
   conditions with different fixes.
5. **The source spool was byte-identical after every mode**
   (`invariant_source_untouched: true` in all four JSONs).

⚠️ **What the rehearsal CANNOT show.** No provider key is configured on this
box, so extraction ran through the offline `MockModel`/v2 path: points appear
(`526 → 528`), **entities stay 0**, and therefore the entity/edge half of
`#14232`'s criterion 4 is unproven here. It is also why the cost figures are
this corpus's, not production's.
