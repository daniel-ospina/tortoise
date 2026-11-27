---
title: "Capture ingest converges — one intake queue, and an accepted turn that is never refused"
type: engineering
domain: platform
doc_status: live
created: 2026-11-25
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise, pi
issue: daniel-ospina/tortoise#14342
objective: daniel-ospina/tortoise#11081
---

# Capture ingest — the map, and the shape it must take

This is the plan #14342 asked for **before** any code: the dependency graph of the capture family, the
existing queue that already exists (and why this path does not use it), the decision points, and the
chosen option for each with its alternative written down.

The premise is not re-derived here — it is measured and owner-verified in #14342's own comment
("THE CAPTURE PICTURE, WRITTEN DOWN", 2026-11-25): **the API refuses the 17th concurrent capture
(`429 capture_capacity_saturated`), the refusal makes the `reflect-hook` park a recovery copy in
`~/.tortoise/session-events/`, and nothing ever went back for those copies** — 106 MB / 22 day-files /
17 894 records re-measured on the box (the original 978 MB / 123 545 was a different box-day). The
parking lot is not a second store; **it is undelivered work**.

---

## 1. The map — every writer, queue, sink, drain and retry path

**⚠️ Code citations — revision pin (#14707 review P3).** Every `file.py:NNNN` reference in this document was
written against the tree this plan was drafted in, and it DRIFTED before the code landed (`:22793` is the
route's `:23135` here, `:24112` is the tail's `:24112`-equivalent moved, and the intake door inserted ~370
lines above them). Follow STORAGE-ARCHITECTURE's rule: **the symbol name is authoritative, the line number is
a convenience.** Read `POST /v1/sessions` / `_reserve_capture_slot` / `_capture_session_impl` /
`_capture_intake_*` by name, never by number.

### 1.1 The producers (two, and they are being reduced to one)

| Producer | Writes | When | Where it is decided |
|---|---|---|---|
| `tortoise/pi-hooks/tortoise-capture.ts` — the product seam | `~/.tortoise/capture-spool/entries/` (`SPOOL_DIR`, `:1148`) | per turn, then drained | the normal path (#3575) |
| `agent-infra/extensions/reflect-hook.ts` — the second producer | `~/.tortoise/session-events/` | **only on a failed POST** | `session_events_drain.py:160` |

`#14342`'s second comment already proved, turn-for-turn, that the second producer is a **duplicate**
(265/265 spool sessions also present there; 179 byte-identical, the other 86 differ only in the
truncation marker). Its removal is PR #14695 (product side) with the durable fix in
`agent-infra#1671`. **This plan does not re-open it**; it depends on it, because a parking lot that no
longer gets written is what makes "with a queue there is nothing to park" true.

### 1.2 The client half — where a turn can be lost before the network even matters

| Surface | Code | Role |
|---|---|---|
| spool entry lifecycle | `tortoise/capture_spool.py` — `CAPTURE_CAPACITY_CODE:165`, `DRAIN_MAX_IN_FLIGHT:184`, `DRAIN_CAPACITY_YIELDS`, `attempts`/backoff, "nothing charged to an attempt" at `:621` | the disk queue; an unfiled entry is the only copy of a conversation |
| drain claim | `tortoise/drain_claim.py` — `refs`-free claim file, one drainer per box | **serialises** the drain (D1, `da82ece422`) |
| legacy parking-lot drain | `tortoise/session_events_drain.py` — `CURSOR_FILE` | the only thing that could recover `session-events/`; has never completed a pass (`store_sync` reported `cli_missing`) |
| store-sync backstop | `tortoise/store_sync.py` — `cli_missing` | the "no CLI on this box" failure that left the parking lot un-drained |

D1 has already fixed the **client's** pace: a capacity refusal is no longer charged to `attempts`, the
drainer yields on `retry_after` up to `DRAIN_CAPACITY_YIELDS` and then reports `capacity_stopped` with
the head still monotone. That is the right client behaviour **for a server that refuses**. It is also
the reason the *server* is now the only place the loss can be removed: a polite client still has to
retry, and the retry is what fills the parking lot.

### 1.3 The server half — the refusal, and the ACK seam that is already a queue for everything EXCEPT admission

| Surface | Code | What it does |
|---|---|---|
| capacity | `hosted_api.py:448` `_CAPTURE_MAX_IN_FLIGHT = max(1, min(env, 16))`, default 8 | bound on concurrent captures |
| admission gate | `hosted_api.py:1706` `_reserve_capture_slot`, refusal at `:1730` | **refuses** at capacity, typed body (`_CAPTURE_CAPACITY_CODE:486`), counted for `/health` |
| route | `hosted_api.py:22793` `POST /v1/sessions`; admission at `:22825` **before any state is written** | the door |
| record phase | `:23914` — `_write_session_and_turns` (:Session MERGE + turn :Point nodes), sessionCaptured `:Event` mint, `:Source` materialization | **durable, and it completes before the ack** |
| ack/complete split | #14261 — `:25816` phase `queued`, `:25870`+ returns `status: accepted`, `extraction.phase`, `status_url`, HTTP 200 | ack without waiting for the model call |
| tail | `:24112` `_extract_capture` — a closure over the record-phase locals (`body.conversation`, `_turn_texts`) | the ~19 s model call |
| tail registry | `_CAPTURE_TAIL_TASKS` (in-process `asyncio` set) | keeps the task alive; **not durable** |
| phase retirement | `:23000` `_clear_capture_phase`, `_capture_abandoned_marker` (`capture_ok=false`) | every exit retires the claim (#14232 review F2) |
| worker pool | `_CAPTURE_EXECUTOR` = `TORTOISE_CAPTURE_WORKERS`, default **4**, max 8 | the real execution concurrency |
| read surface | `GET /v1/sessions/{id}` → `session_projection.session_extraction_status` | **per-item status already exists** |

### 1.4 The sink (unchanged by this plan)

`tortoise/event_store.py` → `tortoise/record_projection.py` (the record) → `tortoise/dream.py` (EP
weights). Per `docs/architecture/STORAGE-ARCHITECTURE.md` §2.0 the record is the system of record and
FalkorDB is the derived hot cache; capture writes to the record, and raw turns are never the thing the
graph holds as memory. **Nothing in this plan writes derived claims at ingest.**

### 1.5 Where a turn is actually lost today — the one path this plan removes

```text
client: turn → spool entry (durable)
client: drain POST /v1/sessions
server: _reserve_capture_slot  ── in_flight >= 8 ──► 429 capture_capacity_saturated
                                                     │
client: entry stays unfiled, attempts+1 (pre-D1)     │
reflect-hook: parks a recovery copy in session-events/  ◄── the parking lot
nothing: never drained
```

Every arrow after the 429 is a **consequence of the refusal**, not an independent defect. Remove the
refusal and the parking lot stops being written; the 17 894 records that already exist are a separate,
gated recovery (decision D4).

---

## 2. The question the brief says to answer first: does the queue already exist?

**Partly — and the part that is missing is exactly the part that loses turns.**

There are **three** queue-shaped things in the repo, and only one of them answers "accepted but not yet
processed".

| Candidate | What it is | Verdict for this path |
|---|---|---|
| **#14261's ACK/async seam** (`hosted_api.py:25816`+) | After the record phase, the request answers `status: accepted` / `phase: queued` and the extraction runs as an `asyncio` task; `GET /v1/sessions/{id}` serves the phase. | **The status half is right and is REUSED.** The storage half is in-process only (`_CAPTURE_TAIL_TASKS`), so it does not survive a restart — and **it is downstream of admission**, so it never sees what the refusal turned away. It queues the *tail*, not the *intake*. |
| **`tortoise/batch_ingest.py`** (#14219 Track F4) | A full durable per-item queue: `BatchStore`/`GraphBatchStore` rows, a per-item state machine, `BatchWorker`, `BATCH_ITEMS_IN_FLIGHT=2`, an injected `ItemRunner`, resumability, cancel, content-hash dedup. | **Not reusable as the capture queue** — its items are documents, its runner is the per-file indexer, and its spend metering is the document half of the one token budget. Its **shape** is the precedent this plan copies (durable row → state machine → worker), and it is cited as such rather than re-invented. |
| **the client spool** (`capture_spool.py`) | A durable queue on the *client*, with claim, attempts and backoff. | Already the right shape — and it is precisely why the server may refuse: the client keeps the bytes. **But it is not the product's memory**, and a background agent that moved past its turn cannot retry what the server bounced. |

**Answer: no capture *intake* queue exists. The ACK seam supplies acceptance semantics and per-item
status, and this plan reuses both; the missing piece is a durable place for work the server has accepted
but not yet processed — which today is "nowhere", because the work is refused at the door instead of
being accepted.**

---

## 3. Decision points, chosen option, and the alternative that was rejected

### D1 — Where accepted-but-unprocessed work lives

**Chosen:** a **graph-backed intake row** — `:CaptureIntake` in the record, holding the exact
`SessionRequest` payload, `state`, `enqueued_at`, `attempts`, `item_id` — written at the door, drained
by a worker, deleted only after the item reaches a terminal state.

**Why:** the record is the system of record (STORAGE-ARCHITECTURE §2.0). ⛔ **CORRECTION (#14707 review P1): the row as built does NOT land
in the record.** It lands in the tenant's GRAPH — the derived hot cache — through the projection's handle,
which is the same substrate `batch_ingest.GraphBatchStore` uses. So it survives the ACCEPTING PROCESS (a
restart, a deploy, a dead drain task: the failure the client's in-line retry could not survive) and it does
**not** survive a graph rebuild/re-provision, which drops every queued row while the client has already
pruned its spool copy on our 2xx. This D1's original sentence — "durable by the same rule that makes a
capture durable" — was FALSE: a capture is durable because the RECORD holds it. Record-backing the row
(declared record type + `supabase/migrations/` + journal append) is **`#14712`**, and until it lands the
guarantee above is the one the code actually provides. The row costs disk, not RAM (the payload is up to ~2.5 MB —
500 turns × 5 000 chars — which is precisely why the current code refuses rather than queues: *"every
later capture then waits forever while holding its stored-window transcript (~2 MBs) on a 4 GB VM"*,
`hosted_api.py:420`). Same shape as `batch_ingest.GraphBatchStore`, deliberately.

**Rejected:** (a) *reuse #14261's in-process task registry* — not durable, lost on restart, and
downstream of admission so it cannot absorb a refusal; (b) *an in-memory `asyncio.Queue`* — the same
loss, plus an OOM risk the current comment is right about; (c) *a local disk spool on the API host* —
durable across a process restart but **not** across a Fly machine replacement, and the client has
already dropped its copy by the time it sees a 2xx.

### D2 — What the agent sees on acceptance, and what it can query

**Chosen:** HTTP **202** with `status: "accepted"` (the existing `memory_write_v1` vocabulary member),
`extraction.phase: "queued"`, `status_url: /v1/sessions/{id}` (already served), and
`intake: {item_id, position, depth}`. The in-budget path is **unchanged** — 200 and today's receipt —
so this is additive.

**Why 202 and not 429:** 202 is in the client's `landed` band (`tortoise-capture.ts:3684`,
`2xx`; `capture_spool` treats any 2xx as filed), so both legs file the entry without a retry, and the
body says the same thing the ACK already says. `item_id` is what an operator quotes when asking "where
did turn N go"; `position` is the queue depth ahead of it, which is the only honest ETA we have.

**Rejected:** *200 with the same body* — semantically "processed", which is the false-PASS class this
family keeps paying for; *no id* — then the only handle is the session id, and a session can be
captured more than once.

### D3 — Backpressure by depth, and what happens at the ceiling

**Chosen:** the ceiling is a **depth**, not a refusal, and **it never refuses**. `_CAPTURE_QUEUE_MAX_DEPTH`
(default 512) is a **PUBLISHING** threshold: below it the drain runs at `_CAPTURE_MAX_IN_FLIGHT`; at or above
it the drain keeps running (nothing is dropped) and the condition is **published** on the 202's `intake`
block (`depth`, `over_depth`, `position`) so the shape is visible instead of turning into a 429 the agent
cannot act on. ⛔ **NOT on `/health`** (#14707 review P3): `_capture_health` publishes the counters only, and
§6 records why that promise is withdrawn — a deploy-wide depth would scan every tenant's graph on a health
path that collects no I/O. An earlier revision of this D3 named `/health`'s `intake` block as a second
publisher, which §6 had already withdrawn; the two disagreed until this line was corrected.
⛔ **What bounds intake GROWTH is the door's per-org ingest window** (`commit_ingest_admit`, charged in
the entry-gate preflight BEFORE the accept is written — see §4), not this number: an earlier revision of this
D3 called the threshold "pacing" while nothing read it except an `over_depth` flag, i.e. no bound of any kind
(#14707 review P3).

**Why a queue depth and not a byte budget as the trigger:** depth is the thing that predicts latency;
bytes are bounded by `BATCH_MAX_INLINE_BYTES`-style request limits already enforced at the door. Both
are reported on the 202; only depth is the published over-threshold flag.

**The rule that outranks the ceiling:** *a captured turn is never lost* (#10479 — a full spool refuses
new work rather than destroying undelivered work). The only refusal this design may produce is
**503 when the record write itself fails**, i.e. when we genuinely cannot accept — and then the client
still holds its spool entry, which is the safe direction. A depth ceiling that dropped items would be
the exact defect this epic exists to remove.

**Rejected:** *refuse at a high depth* — that is the same loss with a bigger number; *block the request
until a slot frees* — forbidden by #3834 (the 10 s wait bound must not be raised, and a queue is the
alternative to waiting longer).

### D4 — `session-events/`: delete, archive, or drain-then-remove

**Chosen:** **keep as a read-only archive; drain once, then remove — gated.** Order: (1) intake ships
(no more parking), (2) `#14691`'s five noise tests pass, (3) drain the 17 894 records **once** through
the product's own store-sync leg, (4) then `tools/seal_session_events.py` retention (seal/compress,
never delete a day-file that has not been replayed and counted).

⛔ **Not replayed here.** Draining 17 894 records into an extractor whose noise behaviour is unproven is
the failure that produced the previous graphs (`#14691` is the gate, and this plan spends **zero**
extraction tokens).

**Rejected:** *delete now* — destroys the only local copy of 2026-11-05 → 2026-11-25 (#10479 again);
*drain now* — spends the one token budget (#14275) on unproven noise behaviour.

### D5 — Which recorded decisions this work is bound by (contradiction test)

| Decision | Source | How this plan honours it |
|---|---|---|
| ⛔ a captured turn is never lost | #10479 (full spool refuses new work rather than destroying undelivered work) | the queue **accepts**; the only refusal is "the record write failed", and then the client keeps its entry |
| ⛔ do not raise the 10 s wait bound | #3834 | a queue replaces waiting; the 202 is immediate, nothing joins the model call |
| ⛔ one token budget | #14275 | replay of the parked pile is gated (D4); the intake drain re-enters the **same** path, so it spends through the same meter |
| ⛔ no replay of the parked pile until #14691 passes | #14342 comment | D4 ordering; nothing here drains `session-events/` |
| ⛔ graph storage and token allowance are separate budgets | owner, #14219 (e) | the intake row is graph storage; it never trades against tokens |
| ⛔ no MCP/SDK surface change without Daniel's approval | AGENTS.md | **no** new MCP tool and **no** new public SDK method — the surface is unchanged (`POST /v1/sessions` keeps its body; 202 is an additive status) |
| ⛔ never print or copy a credential | AGENTS.md | the intake row stores a **whitelist** of org fields (`org_id`, `graph_id`); no key, no token, no hash |
| extraction is the only producer of the logical model | EXTRACTOR-V4-ARCHITECTURE | the drain **re-enters `_capture_session_impl`**; it contains no extraction logic of its own |

---

## 4. The shape, in one picture

```text
POST /v1/sessions
  ├─ gates that are about the ENTRY (403 scope, 409 recording/off, 400 turn cap, 402 quota,
  │  402 cohort, 429 per-org ingest window) — unchanged, and they run BEFORE the accept
  ├─ slot free ─────────────────────────────► today's path (record + in-window extraction, 200)
  └─ slot saturated
        ├─ RUN THE ENTRY GATES FIRST (`_capture_session_impl(gate_only=True)`, same impl,
        │  stops at the first-write boundary): a 402/409/400/429 the caller must act on keeps
        │  its refusal — it is NOT converted into an acceptance
        ├─ write :CaptureIntake row (payload, state=queued)   ← survives the process, NOT a rebuild (#14712)
        └─ 202 accepted + status_url + intake{item_id, position, depth}

intake worker (asyncio task; kicked by an accept, and by a 60 s sweep over tenants this
               process has served)
  ├─ claim oldest queued row (state → extracting, attempts+1)
  ├─ acquire a capture slot WITH THE SESSION KEY (#3129's duplicate-session guard), bounded wait
  ├─ re-enter _capture_session_impl(..., deferred_resume=True, ingest_charged=True)
  │     └─ the same record/extract/report path; no second extraction implementation
  └─ terminal → delete the row; TRANSIENT fault (capacity, same-session in flight, store 503)
     → requeue with NO attempt charged; VERDICT (402/409/400/422) → park `failed` at once,
     visible; unclassifiable → requeue with backoff up to the attempt bound
```

`deferred_resume=True` exists for exactly one hazard: a capture that was **recorded** and then abandoned
before extraction finalized would, on re-entry, take the #1727 replay branch (200, 0 extracted) — silent
loss of the extraction. The flag routes a re-entered intake item to the #2335 **TRUE-retry** lane, which
already re-extracts on the convergent ids. That is the same decision #3129's abandoned-capture marker
already makes for a cancelled request, extended to a queued one.

---

## 5. Falsifiers — what would show this plan wrong

1. **A burst of N > `_CAPTURE_MAX_IN_FLIGHT` is refused** → the refusal was not removed. (Test: the burst
   test in §6.)
2. **A turn disappears under a mid-flight kill** → an accepted item was deleted before terminal, or the
   drain could not resume. (Test: kill between accept and drain; every accepted item must complete.)
3. **A queued item re-extracts twice** → the replay/TRUE-retry predicate is wrong. (Test: drain the same
   item twice; second pass must be a no-op by `capture_key`.)
4. **Depth grows without bound while the drain makes no progress** → backpressure is not being applied
   where the work actually waits.
5. **`session-events/` grows while intake is live** → the second producer is still writing (PR #14695 /
   `agent-infra#1671` not in effect on this box).

## 6. Evidence this plan owes, and where it lands

| Claim | Evidence |
|---|---|
| a burst past the old ceiling is accepted, not refused | one test: N concurrent captures against a pinned ceiling of 4, **all 2xx**, none 429 |
| every accepted turn is eventually processed | the same test: N in, N out, count published |
| nothing is lost on a mid-flight kill | a test that kills the drain between accept and process, then drains again: N in, N out |
| per-item status is answerable | `GET /v1/sessions/{id}` returns phase `queued` while the item waits — **delivered**: the route consults the tenant's queue when no `:Session` exists yet, so the 202's `status_url` does not 404 for the whole queued period |
| the queue is bounded by depth, published | `/health` publishes `intake: {enabled, accepted, completed, failed}` — **counters only, and restated here** (#14707 review P3): a deploy-wide DEPTH would have to scan every tenant's graph on a health path that collects no I/O, and rows are tenant-scoped, so the depth an operator can read for free is the 202's `intake.depth`/`over_depth` and the tenant's own status address. This table previously promised `intake_depth`/`intake_oldest_age_s` on `/health`; that promise is withdrawn, not quietly dropped |
| the intake survives a rebuild | **NOT DELIVERED — `#14712`.** The row lives in the graph (the derived cache), so a rebuild drops it while the client has pruned its copy |
| a failed accept does not spend the org's ingest window | **NOT DELIVERED — accepted residual, stated (#14707 review P3).** The entry-gate preflight takes `commit_ingest_admit` BEFORE the intake row is written (it must: the drain re-enters the capture path with `ingest_charged=True`, so a charge taken later would either be missed or double-charged). So a store failure at `accept()` answers 503 with the window already spent, and every client retry spends it again — which under a sustained store outage can surface as `ingest_rate_limited` rather than the 503. The safe order is not available without a refundable charge (`quota.commit_ingest_admit` has no refund and a negative charge would let an org overspend) or an accepted-row-then-charge walk that would queue work the caller was refused. **No turn is lost by this**: a 503 is retryable and both client legs keep their spool entry; the cost is DELAY under an already-degraded condition, which is why it is recorded here rather than fixed by a fallback |

## 7. What this plan explicitly does NOT do

- It does **not** drain the 17 894 parked records (gated on `#14691`, D4).
- It does **not** change the MCP or SDK surface (D5).
- It does **not** change extraction quality, the extractor lane, or the token meter.
- It does **not** touch the 16-cap clamp (`TORTOISE_CAPTURE_IN_FLIGHT`): the clamp bounds *execution*
  concurrency, which stays; what changes is that surplus work is **accepted and queued** instead of
  refused.
