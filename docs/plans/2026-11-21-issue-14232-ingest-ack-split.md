---
title: "#14232 — POST /v1/sessions acknowledges inside the budget; extraction completes behind it"
type: plan
domain: platform
doc_status: live
created: 2026-11-21
ownedBy: platform-team
aboutSubjects: tortoise
aboutObjects: tortoise-hosted-api, tortoise-capture, extractor-v4
---
<!-- research-path: issue daniel-ospina/tortoise#14232 comment carrying `<!-- research: v1 -->` -->
<!-- plan-review: cycles=3, status=as-built, version=3.0.1 -->

# #14232 — capture ACKs fast, extraction completes on the worker

## 1. Confirmed problem

`POST /v1/sessions` runs the **whole** capture synchronously inside one request: admission, quota
estimate, session/turn write, mint + Source materialize, **the LLM extraction**, the provenance
stamp, the entity-link pass, the receipt write and the `capture_ok` record. The transport wait bound
(`mcp_auth._TRANSPORT_WAIT_BOUND_S`, 10 s, owner decision `#3834`) therefore expires at the extraction
and answers **504** for a capture whose record is already durable.

- Hosted: `hosted_api.py:21120-21121` — `extracted, meta = await _run_capture_bounded(` / `slot, sdk._extract_session_v2,`
- The measured production sequence is in the issue: 402 (cap, lifted) → 401 (no key in env) → 429 (`in_flight 8/8`, raised to 16) → **504 at 10 s with `stage=unmarked spent_ms={}`**.

Two facts follow from that trace, and they are the whole problem:

1. **Nothing about the request needs the extraction's result to answer.** The record is written
   before extraction; the receipt only reports what extraction did.
2. **The path cannot say what was slow.** `read_stages.ReadStages.mark()` is called nowhere on this
   path, so a breach publishes `unmarked` with an empty `spent_ms` — the diagnosis stops at "10 s".

**Falsifier (carried from the research block):** if ack were already inside the budget, the
production trace would have shown 200s rather than a run of 504s on the identical client; it did not.
**Confidence:** high — the trace is a direct measurement, and the code path is single-return.

## 2. Alternatives considered

| # | Alternative | Why not |
|---|---|---|
| A | **Raise the wait bound** for `/v1/sessions` | ⛔ Owner decision `#3834` forbids it; a longer bound only moves the cliff and hides the slowness. |
| B | **New persistent queue** (Redis/RQ/Celery) for extraction | Invents a second durable store beside the graph record; `#4010`/§2.0 say the record is truth, so a queue is a second truth. The `_CAPTURE_EXECUTOR` pool already exists, is bounded (`_CAPTURE_MAX_IN_FLIGHT`, `:427`) and already carries the write half (`:20796`). |
| C | **Ack before the record write** (accept, then persist+t extract) | Breaks re-derivability (§2.0): a crash between ack and write loses the capture while the client was told it landed, and the spool would be the only copy. |
| D | **Split at the extraction wait, keep today's tail code verbatim** — the same code moves behind the ack, run as one completion coroutine | Chosen, **with the bounded join of E**. One copy of the logic, the record write stays inside the ack, the pool does the model call, and the tail keeps its `await`s by running as a task on the loop it already used. |
| E | **D + a bounded join on the ack deadline**: run the completion coroutine as a task, then `await wait_for(shield(task), <remaining budget to the ack deadline>)`; if it finishes in time, return **today's full receipt**; if not, return the ack and let the task finish behind it | **Chosen.** D alone changes the endpoint from "returns what extraction did" to "returns a receipt" for *every* capture, including the fast ones — which breaks the meaning of a pinned response contract for 34 test files and for the capture clients, and buys nothing: when completion lands inside the ack window, the caller can have it. E keeps the ack bounded by construction (the `wait_for` is the bound) and keeps the sync body byte-identical whenever extraction is fast. ⛔ **E applies to the REST transport only** (`request is not None`): `_capture_session_impl` is shared with the MCP tool `tortoise_session_capture`, whose callers expect the result and whose transport already has its OWN bound (`_await_under_mcp_wait_bound`) — so deferring there would change an MCP-facing behaviour for no benefit and is explicitly out. |

## 3. Complexity + domains

**standard** · domains: api (hosted), read-projection (session status), instrumentation
(`read_stages`), tests. No schema migration; no MCP/SDK surface change (`TOOL_REGISTRY` and the
public `TortoiseSDK` method set are untouched — the SDK keeps calling the shared writer, not HTTP).

## 4. Integration Surface Map

| Surface | Layer | Why |
|---|---|---|
| `POST /v1/sessions` (HTTP body) | integration (ASGI, real app) | The ack receipt shape; existing keys preserved, new keys additive |
| `_capture_session_impl` tail (graph writes) | integration (real graph) | Record durability before ack; terminal `capture_ok` after |
| `GET /v1/sessions` · `GET /v1/sessions/{id}` · `sdk.get_session` | integration (real graph) | The `extraction` block gains an additive `phase` |
| `read_stages` → wait-bound refusal body/log | unit + integration | `stage=`/`spent_ms=` populated on this path |
| `_CaptureSlot` (capacity + in-flight key) | unit | Session key must outlive the tail (#3129) |
| drain of 50 sessions | acceptance script (`tools/`) | p95 ack, zero 504s, terminal states, points/entities/edges |

## 5. Research

Both the git history and the current tree were checked; the comparables are recorded in the research
comment on the issue (`Mem0` < 50 ms ack + queue, `Zep` declared async delay, `Hindsight` 500–2 000 ms
per batch).

⚠️ **The two docs this brief cites did not exist at this branch's base and DO exist now** — they landed
on `main` as `#14216` while this lane ran, and an earlier revision of this section said they did not
exist at all (`git ls-tree -r origin/main` genuinely returned nothing for either name at `b4da1f5c01`).
Corrected here rather than silently dropped, because the correction is load-bearing: they contain the
destination this split is a step toward, and one of their gates is **stricter than this lane's**:

- `PLAN-private-beta-and-scale.md` **F2** (Track F) — *"write ack ≤ 100 ms p95; projection lag bounded
  and exported"* — and **G2** measures that same figure. This lane's target is the brief's **1 s**, and
  its measured p95 is 924 ms (in-process rehearsal).
- `SCALING-ROADMAP.md` §1/§2.5 — Mem0's **<50 ms ack, queue, scale on depth**; Zep's async ingest; and
  §3's convergence line: *"they never make a read wait for extraction"*. That is precisely this lane's
  split, so the comparables now have a home in the repo and this section no longer quotes them second
  hand from `#14219`.
- `PLAN-private-beta-and-scale.md` **D3** names this very traffic (`6 × POST /v1/sessions, all 429` in a
  9 s window), which is what the 429 leg of this lane's gate trace was.

⛔ **The gap between 924 ms and F2's 100 ms is NOT closed by this PR and is not claimed to be**: the ACK
still carries the gates, the `:Session` MERGE, the turn `:Point`s, the `sessionCaptured` Event and the
`:Source` materialization, plus the receipt. What this PR removes from the wait is the model call — the
term that grew without bound. Closing the rest is F2's remaining synchronous-capture work (`#4625`), and
the honest statement of the split's contribution is **"ack no longer waits on extraction"**, not "ack is
now fast".

## 6. Wiring

| Call site / consumer | What it does there | Change |
|---|---|---|
| `hosted_api.capture_session` (endpoint) | admission slot, `CancelledError` → abandoned marker, `finally: slot.release()` | unchanged |
| `hosted_api._capture_session_impl` | gates → write → **split point** → tail; **shared with the MCP tool** | tail becomes `_complete_capture()`; ack returned for the extracting **REST** lane. The deferral is gated on `request is not None`, so `tortoise_session_capture` (`request=None`) keeps the synchronous result contract — no MCP/SDK surface behaviour change |
| `_run_capture_bounded` | submits the extraction to `_CAPTURE_EXECUTOR`, hands the slot off | unchanged (still the only submit path) |
| `_CaptureSlot` | capacity + session-key release gates | +`hand_off()` at defer time (so the request teardown cannot double-decrement) and +`tail_done()` gate so the session key outlives the tail |
| `_capture_abandoned_marker` | `capture_ok=false` + lane on cancellation | reused by the completion task's cancel/raise arm |
| `session_projection.session_extraction_status` | the ONE status derivation, 3 consumers | +`phase`/`phase_at` kwargs; the 3-member outcome set untouched |
| `GET /v1/sessions` (`hosted_api:25319`) | serves `extraction` per row | +2 columns, appended last |
| `GET /v1/sessions/{id}` (`hosted_api:25925`) | serves `extraction` for one session | +2 columns, appended last |
| `sdk.get_session` (`sdk.py:58507`) | SDK read of the same projection | +2 columns, appended last |
| `read_stages.STAGES` | closed stage vocabulary + `mark()` | +4 capture stages; search stages untouched |
| `WaitBoundMiddleware` / `_emit_wait_bound_breach` | publishes `stage`/`spent_ms` on refusal | unchanged (vocabulary validated against `STAGE_SET`) |
| `tests/test_10893_read_stage_attribution.py` | pins vocabulary completeness | extended for the capture stages |
| `tortoise/session_confirm.py` (CLI 504→confirm) | reads `extraction.outcome_known` | unchanged; `phase` is additional |
| `tortoise/__main__.py:9391` `outcome_unrecorded(body)` + the spool drain | renders a 2xx whose extraction outcome is unknown, and writes no priced deferral | **unchanged code** — the ack carries the same marker they already read (`capture_spool.UNRECORDED_OUTCOME_KEY`) |

## 7. Implementation steps (each with the test that proves it)

1. **`read_stages`: capture stages.** Add `capture_gates`, `capture_write`, `capture_extract`,
   `capture_tail` to `STAGES` (closed vocabulary, one timeline — the refusal line is shared, so the
   marks must live in the same set). *Test:* `STAGE_SET` completeness + `mark()` accepts each and
   `spent_ms` reports it.
2. **`session_projection`: additive phase.** `SESSION_EXTRACTION_PHASES = ("queued", "extracting")`
   declared beside the outcome set; `session_extraction_status(..., *, phase=None, phase_at=None)`
   publishes `phase`/`phase_at` **whenever the record carries one** — ABSENT, not `null`, on a terminal
   row (the phase is CLEARED in the same statement that records the outcome), so the `#3520`
   exact-key-set pins (`set(row["extraction"]) == {"status", "extractor", "outcome_known"}`,
   `tests/test_session_capture_status.py:194`) stay true for a processed session and the 3-member set
   is untouched.
   ⛔ **NOT `capture_ok is None and phase`** — that was the first cut and round 1 removed it: the `#2335`
   TRUE-retry lane re-extracts a session whose record ALREADY carries `capture_ok = False`, so guarding
   on `capture_ok is None` suppressed the phase on the one lane where a reader most needs to see that
   extraction is running, and left the ACK and its own `status_url` disagreeing about the same capture.
   The shipped guard is `if phase:` (`session_projection.py`), pinned by
   `test_a_retry_in_flight_says_so_on_the_ack_AND_on_the_status_url`. A fixer must not reinstate the
   `capture_ok is None` guard from an earlier revision of this plan.
   ⛔ **The persisted property is `capture_extraction_phase`, NOT `capture_phase`** — `capture_phase`
   is already an occupied name on this exact path: `graph_ops.PHASES` (four members,
   `graph_ops.py:101`) and the `graph_ops.capture_phase()` context manager that RAISES on an unknown
   phase, which `@phased("extract")` wraps around `sdk._extract_session_v2` — the very function this
   change defers (`sdk.py:21007`). Two closed vocabularies of different cardinality under one word,
   inside one request, is a trap; the node property is renamed rather than explained. *Test:* the
   three-member set is untouched; a terminal outcome never publishes a phase; the phase vocabulary
   is closed and `assert`ed.
3. **Read surfaces.** Append `s.capture_extraction_phase, s.capture_extraction_phase_at` to the three reads and pass them.

   *Test (real graph):* a session at `capture_extraction_phase='extracting'` reads back
   `phase: "extracting"` + `outcome_known: false` on ALL THREE surfaces (list, detail, SDK); a session
   with `capture_ok=true` and a phase left behind (the retry lane) reads `stored-and-processed` WITH the
   phase and `outcome_known: false` — the phase is published whenever the record carries one, and
   `outcome_known` is what carries the "not yet" half.
4. **The split (bounded join).** Inside `_capture_session_impl`, after the record write and before
   the extraction branches, arm `state["attempted"]/["proj"]/["lane"]` — **at the defer point, never
   above the branch chain** (see 4b) — then:
   - non-extracting lanes (replay / store-only / no-provider / extraction-off) →
     `return await _report_capture(await _extract_capture())` — **byte-identical response to today**;
   - the MCP lane (`request is None`) → the same inline call — unchanged contract;
   - the extracting REST lane → `slot.hand_off()`, `slot.tail_pending()`, write
     `capture_extraction_phase='queued'`, `asyncio.create_task(_extract_capture())` (registered in
     `_CAPTURE_TAIL_TASKS`), then `await asyncio.wait_for(asyncio.shield(task), grace)`:
     finished in time → `return await _report_capture(result)` (today's body); else
     `_schedule_capture_completion(...)` and return `build_write_verb(status=STATUS_ACCEPTED, extra=ack)`.
   ⛔ **The tail is TWO coroutines, and the join covers only the first.** `_extract_capture` is the
   model call; `_report_capture(result)` is everything the extraction makes reportable (provenance
   stamp, entity links, receipt, the `capture_ok` record). Joining the whole tail made the
   compatibility half unreachable for a reason that has nothing to do with the extractor's speed: the
   post-extraction bookkeeping alone exceeded the window, so a fast extraction still answered with
   the ack. Splitting at that boundary is what makes "completion arrived in time" mean "the model
   was fast".
   **The ack deadline** is `arrival + _CAPTURE_ACK_DEADLINE_S`, read from
   `request.state["_wait_bound_t0"]` (the same arrival clock the bound uses), clamped by
   `min(_CAPTURE_ACK_DEADLINE_S, _TRANSPORT_WAIT_BOUND_S / 2)` and floored at 0, so the response can
   never approach `#3834`'s bound. **`_CAPTURE_ACK_DEADLINE_S = 0.5`**, and the number is a
   measurement rather than a round figure: the window is paid IN FULL on every capture whose
   extraction outlives it (a real extraction is a network model call and always does), so the ack's
   latency is `record phase + window`. Measured on the in-process drain at a 1 s window: **p95 ack
   1.14 s** against this issue's 1 s target — the window bought nothing at 1 s, because a 1 s
   extraction is not what the compatibility half is for. At 0.5 s the fast path (replayed / cached /
   small capture) still gets the receipt and the ack clears the target. ⛔ The record write happens
   BEFORE this point, so no ack can precede durability (R1) even when the record phase has already
   spent the whole window.
   *Test:* with a parked extractor, the POST answers inside the deadline with
   `protocol_version=memory_write_v1`, `status="accepted"`, `extracted=0`, `points=[]`,
   `extraction.status="stored"`, `outcome_known=false`, `phase` in the declared set,
   `extraction_outcome="unknown"`, `status_url`, `graph`, and HTTP 200 — and **no `complete` key**
   (it was proposed in v2.1 and dropped: `memory_write_v1` has no such member, and a boolean invented
   for the ack would be a second way to ask what `status` already answers);
   with the mock (fast) extractor the POST still answers today's full body (`extracted >= 1`,
   `extraction_mode="llm:mock"`, `first_capture` present, **no `extraction` block**). The
   `extraction_outcome` key is not an invention: it is `capture_spool.UNRECORDED_OUTCOME_KEY/VALUE`,
   the marker `tortoise/__main__.py` already renders as *"outcome not recorded yet — it may still be
   running"* and the spool drain already reads, so the ack needs **no client change** to be read
   honestly.
4b. **Task ownership, and who releases what.** Two module symbols carry it: `_CAPTURE_TAIL_TASKS`
   (the live completion coroutines — a bare `create_task` can be garbage-collected mid-flight) and
   `_schedule_capture_completion(task, report, slot, proj, session_id, state)`, ONE place that
   attaches the continuation's callbacks (`_capture_tail_failed`, `slot.worker_done`,
   `slot.complete_tail`).
   ⛔ **The marker arm is gated on a GENUINE ATTEMPT, exactly as the existing arming guard is.**
   `state["attempted"] = True` is set AT THE DEFER POINT, which by construction is
   `not store_only and (not session_existed or retry_failed_capture)` — never unconditionally above
   the branch chain: the replay/store-only branches deliberately arm nothing
   (`hosted_api.py:20949-20951` — *"arm the abandoned-capture marker for a genuine FRESH attempt
   only … must not arm a write that could downgrade a succeeded prior to capture_ok=False"*), and
   `state["finalized"]` is only ever set inside `if _record_session_state:`. A callback keyed on
   `not finalized` alone would write `capture_ok=false` over an ALREADY-PROCESSED session on a
   replay, and the next re-POST would take the #2335 TRUE-retry and re-extract over it.
   ⛔ **The marker is scoped to CANCELLATION, not to failure** — `_capture_abandoned_marker`'s own
   docstring, and `test_hosted_api.py::TestSessionActorStamp2600`'s raise-shape (ii) pins that a
   re-POST of a raise-shaped (NULL) session must replay, not re-extract. A raised tail is REPORTED
   (exception retrieved, ERROR log), never marked.
   ⛔ **One owner of the slot at every instant**, and it is decided once per request:
   - joined + reported → the request releases (`complete_tail` + `worker_done`) in a `finally`;
   - deferred → `_schedule_capture_completion` owns both, on the continuation, because the
     extraction future finishes MID-capture (releasing there would leave `_report_capture` writing
     `capture_ok` with the key already free — the #3129 window this gate exists to close);
   - **request cancelled mid-join** → cancel the coroutine, `complete_tail` only. Capacity stays with
     the WORKER: `_run_capture_bounded` bound `slot.worker_done` to the concurrent future precisely
     because a cancelled `await` cannot cancel a parked pool thread (#3060, pinned by
     `test_capture_loop_responsiveness.py::test_capture_slot_is_held_until_the_worker_finishes`).
     The endpoint's `CancelledError` arm writes the abandoned marker, as today;
   - **the extraction raises inside the join** → `finally` releases both, because on that arm the
     `worker_done` call is idempotent with the future's own binding and the alternative is what was
     measured: a **409 "already in flight"** on the re-POST where raise-shape (ii) says replay.
   Each of those four is a test in `tests/test_14232_ingest_ack_split.py`, because each one was a
   real defect found by an existing suite rather than by reading the code.
5. **Record durability is inside the ack.** *Test:* at the moment the ack arrives, the `:Session`,
   the turn `:Point`s and the `:Source` exist in the graph (queried by the test right after the POST).
   This is R1's evidence and the §2.0 re-derivability claim.
6. **Phase transitions.** `queued` at ack → `extracting` when the worker starts → cleared in the
   same statement that records `capture_ok` (FalkorDB `SET n.prop = null` deletes). *Test:* a fake
   extractor observes `extracting` while it runs; after completion the property is absent and
   `capture_ok` is set.
7. **Terminal state + capacity + in-flight key.** *Test:* after the tail completes, capacity is back
   to 0 and a second same-session POST during the tail gets `409` (the #3129 arm), then after
   completion a re-POST replays.
8. **Stage attribution.** Mark `capture_gates` (entry), `capture_write` (record write),
   `capture_extract` (worker), `capture_tail` (post-extraction), APPENDED to `STAGES` after
   `response` so the read path's declared order is untouched. *Test:* a slow phase-1 capture under a
   short bound answers 504 whose body is FLAT — `body["stage"] == "capture_write"` (the stage still
   OPEN at the deadline; `stages_ms` carries only stages that FINISHED, `read_stages.py:423`) and
   `body["stages_ms"]["capture_gates"]` is present. Mutation-proved by deleting the
   `capture_write` mark: `stage` must fall back to `capture_gates`.
9. **Acceptance drain (50 sessions).** `tools/acceptance_14232_drain.py`: `--base-url` + `--api-key`
   drains the SPOOLED corpus against a deployment (the real acceptance run); with no `--base-url` it
   runs in process against a private embedded graph and says **rehearsal** on its first line, with the
   host load beside it, because latency and extractor are then this box's. It grades four criteria on
   one run — ack p95 ≤ 1 s, zero refusals, every session terminal on its own, and extraction
   demonstrably behind the ack (points, plus entities/edges where the extractor emits them; the
   offline seam emits a bare claim with no entity, measured, so requiring entities there would fail a
   healthy run — a deployment run passes `--require-entities`). It writes a `--json` report and exits
   non-zero on any criterion, so the acceptance claim is reproducible rather than asserted.

## 8. Review Scope Contract

**Class:** `code` (+ `protocol` only where the receipt shape is described; no `gate-enforcement` code).

### Risk surface

- **R1 — ack before the record is durable** (a crash loses the capture while the client was told it
  landed; §2.0 re-derivability) → caught by step 5's graph assertion at ack time.
- **R2 — extraction silently never completes, and the session sits non-terminal forever** → caught by
  step 7 (terminal `capture_ok` recorded) + the cancel/raise arm's marker test.
- **R3 a client reads the ack as "the capture extracted nothing"** — the #12362 defect class,
  reintroduced by a body that says `extracted: 0` while extraction is running. `STATUS_ACCEPTED` is
  *declared* in the protocol's status vocabulary (`write_verb.py:65`) but has **no emitter anywhere
  in the tree until this change**, so the ack is the first emission on a pinned wire contract. →
  caught by **value** assertions, not key presence: `status == "accepted"`,
  `extracted == 0`, `points == []`, `extraction_mode is None`, `errors == []`,
  `extraction.status == "stored"`, `outcome_known is False`,
  `phase` in the declared set, HTTP 200; **plus** the ack carries the client's own
  unrecorded-outcome marker (`extraction_outcome: "unknown"` — `capture_spool
  .UNRECORDED_OUTCOME_KEY/VALUE`, the ONE definition `tortoise/__main__.py:9391` and the spool drain
  already read), so `tortoise session capture` prints its existing honest *"outcome not recorded
  yet — it may still be running"* sentence and writes **no priced deferral**. Tested at BOTH ends:
  the wire body's values, and the CLI sentence that body produces.
- **R3b the response body is decided by a wall-clock race** (alternative E returns today's full
  receipt when completion lands inside the ack window, the ack otherwise) → caught by two
  deterministic tests: the ack path is driven by commuting `_capture_ack_grace_s` to **0** (so "did the
  caller get the ack" is a fact about the code path, not about load), and the receipt path runs with
  the real window and asserts today's body. Neither asserts through a sleep.
- **R3c the SUITE races the window** — the same risk one level up, and it was REAL: with the
  production 1 s window in place, `assert body["extracted"] >= 1` became a question about machine
  load (the offline `MockModel` seam's capture measured **0.008 s to 0.95 s** on one host depending on
  the test before it), and two pre-existing tests flaked exactly there. `#5049` rule 1 forbids a
  deadline expiry producing a verdict, so `tests/conftest.py` opens the window for the whole suite
  (the transport bound still clamps it) and clears `_CAPTURE_TAIL_TASKS` — whose entries can belong
  to a loop that no longer exists.

- **R4 — session in-flight key released before the tail finishes** (a same-session retry replays a
  200 over a live tail — the #3129 residual) → caught by step 7's 409-during-tail test.
- **R5 — stage vocabulary drift** (`STAGES` closed set, `read_stage` analytics validation) → caught by
  the existing vocabulary tests plus step 1's additions.
- **R6 — capacity leak** (slot never released → every later capture 429) → caught by step 7's
  capacity-return assertion, the existing leak-log test, and the two arms that only exist after this
  change: a completion that dies BEFORE submitting (`_run_capture_bounded` raising — the slot was
  already handed off, so nothing else would ever release it) and a cancelled request (releases the
  tail claim but must NOT release capacity out from under a parked worker, which is R6's inverse).

**Out of scope — must NOT be raised** (note; file; do not chase):

- `PLAN-private-beta-and-scale.md` **F2**'s remaining synchronous-capture work (the ack still carries the
  record writes; `#4625` owns it) — this lane removes the model call from the wait, not the ack's whole
  cost;
- the `_session_extraction_estimate` quota gate's full-graph `count_org_usage` scan (pre-existing,
  `#4625`-class, bounded by `_graph_offload`);
- per-user limits (`#11758`), capture caps (`#4010`), the document-pack surface itself (`#14219`);
- the sync registry-CAS residual declared at the receipt write (`#14086` review);
- the CLI's 504→confirmation path (`session_confirm.py`) — it stays as the legibility net for the
  *other* 504 causes.

**Acceptance:** every `R<n>` has its named evidence, plus the 50-session drain (p95 ack posted, zero
504s, all terminal, extraction visible).
