---
title: "Plan — #8098 the scheduled webhook retry sweep (epic #2080 W4 phase 2, #2081 follow-up)"
type: engineering
domain: capability
doc_status: draft
created: 2026-10-18
subjects.team: epistemic-team
aboutSubjects: epistemic-team
ownedBy: epistemic-team
governingAgreement: "#8098 (epic #2080 W4 phase 2); predecessor #2081 / docs/plans/2026-10-11-2081-webhook-delivery.md"
aboutObjects: "webhook-delivery, graph-event-stream, epistemic-team"
---

# Plan — #8098: the scheduled webhook retry sweep

**Issue:** #8098 · **Branch:** `feat/8098-tm` · **Depends on:** the delivery layer from #2081
(`tortoise/webhook_delivery.py`, merged).

---

## 1. The problem, re-measured (not inherited)

The issue body's diagnosis reproduced exactly, so no correction was needed: the delivery layer
dispatches at the END of `hosted_api._dream_worker` and ONLY there
(`grep -n "_dispatch_webhooks" tortoise/hosted_api.py` → one call site, inside `_dream_worker`).
That is the right cadence for NEW events — a write enqueues the dream that pushes it — but it is a
**write-triggered** cadence. `dispatch_pending` gates on `next_attempt_at` (durable, correct), and
the only process that would notice the backoff elapsed is a dream that may never run for an IDLE
tenant. So a subscriber that was down for 30 seconds could stay undelivered indefinitely.

**The bookkeeping was never the gap. The trigger was.** That distinction shaped every decision
below: nothing in the retry/backoff/cursor state machine needed to change.

## 2. Solution considered, and why this one

| Option | Verdict |
|---|---|
| A timer per subscription ("sleep until `next_attempt_at`") | ❌ Process-local: a restart loses every timer, and the durable `next_attempt_at` is then unread until the tenant writes. The state is in the GRAPH; a trigger that lives in RAM cannot be its authority. |
| A global queue fed at failure time | ❌ Same defect: the enqueue is in RAM. Also splits the state machine across two homes. |
| Make `dispatch_pending` re-armed by any tenant's dream | ❌ Cross-tenant: one busy tenant would deliver for everyone, and an idle FLEET would still retry nothing. |
| **A scheduled sweep: enumerate tenant graphs → one cheap due read per graph → dispatch the graphs that owe a delivery** | ✅ **Chosen.** The graph is the source of truth (`next_attempt_at` decides), so a restart is harmless; cost is bounded and measurable; no state machine change; additive to the dream trigger rather than replacing it. |

## 3. What shipped

| Piece | Home | Contract |
|---|---|---|
| The due predicate | `webhook_delivery._DUE_PREDICATE` + `_BEHIND_PREDICATE` | TWO clauses, both read by BOTH the dispatcher (`_due_subscriptions`) and the sweep's gate (`due_subscription_count`): **due by time** (`disabled_at IS NULL AND (next_attempt_at IS NULL OR next_attempt_at <= now)`) **and behind the stream** (`cursor < head`). The cursor clause is load-bearing, not an optimization: `_mark_delivered` resets `next_attempt_at` to NULL on every success, so the temporal clause alone is TRUE for the whole life of every healthy subscription — a gate without it admitted every tenant that had EVER registered a webhook on every pass, for an SDK open the dispatcher then no-op'd. The parity test drives a **4-state** matrix (fresh / backing-off / disabled / **declared-and-caught-up**) through both, and the caught-up row is the one that would have caught the always-due gate. Residual over-admission the gate cannot see without reading the stream: a `types`-filtered subscription whose pending events are all of a type it does not want, a subscription whose unread events were purged, and a subscription sitting behind a **missed seq** (`next_seq` handed a number out but nothing was appended under it, so the counter sits above `max(:GraphEvent.seq)`) — see `_BEHIND_PREDICATE`, which enumerates all three and states that none is bounded by the retry ladder. |
| The cheap gate | `webhook_delivery.due_subscription_count(graph)` | Takes a **raw graph handle** (`db.select_graph(name)`), not a projection: no SDK, no journal bind, no embedder warm-up, no index, no node. TWO reads: `event_head_seq` (ONE `:GraphEventMeta` node read — the counter `event_store.next_seq` bumps) plus the due count. `event_head_seq` falls back to `max(:GraphEvent.seq)` — the label aggregate the steady path avoids — on a graph with NO counter **OR a counter that is not a trustworthy position**: a non-number (including a numeric string, which `next_seq` refuses to coerce), a negative, a `bool` (`int(True) == 1`), or a non-integral float (`2.9`). ⚠️ That fallback is a **conditional third read** per pass, and it RECURS for as long as the counter stays untrustworthy — `next_seq` refuses the non-number/negative/bool classes and leaves them unchanged, and it does not refuse the non-integral float at all (`2.9` is bumped to `3.9`, re-issuing a seq: the #4653 class). It is deliberately over-admission rather than the alternative: reading such a counter as 0 made every subscription look caught up (`s.cursor < $head` with `cursor: 0`), so the graph was never dispatched again — silently, by the sweep AND the dream cycle. Measured (embedded, 300 calls after warm-up, p50/p95 ms): count read alone 0.5/2.1 empty · 0.5–1.4/1.4–4.3 with 2000 unrelated nodes · 0.5–0.7/3.9–5.7 at the 25-subscription cap; head read 0.6–1.9/3.7–9.8; **full gate 1.4–3.5/10.5–17.4** — flat in graph size (the head is one node, the count is the label's node set, which `MAX_SUBSCRIPTIONS` = 25 bounds). For SCALE, the term the cursor clause REMOVES on the same box: one `TortoiseSDK` + `_get_proj()` + `dispatch_pending` for a caught-up subscription — the per-graph, per-interval cost the always-true gate paid for every subscriber tenant — is **20.7/76.9 ms**. ~1–2 ms buys ~20 ms (p50) to ~77 ms (p95) of avoided SDK open per subscribed tenant per interval. |
| The knobs | `webhook_delivery.sweep_interval_s` / `sweep_concurrency` / `sweep_enabled` | `TORTOISE_WEBHOOK_SWEEP_INTERVAL` (default 60s; `0`/negative REFUSED — `asyncio.sleep(0)` would sweep the fleet in a busy loop), `TORTOISE_WEBHOOK_SWEEP` (tristate, default ON), `TORTOISE_WEBHOOK_SWEEP_CONCURRENCY` (default 4, clamped to 16). The `TORTOISE_WEBHOOK_DISPATCH` kill switch is honoured by the sweep too — an operator who turned egress off must not keep paying a fleet-wide pass. |
| The enumeration | `hosted_api._webhook_retry_targets` (BLOCKING, on a worker) | `backup_sweep.enumerate_orgs` + `_sweep_graph_list` (NOT a bare `enumerate_org_graphs`: `_sweep_graph_list` SYNTHESIZES the default graph from the authoritative name seam, so pre-#2083 orgs — which have no `Graph` nodes at all — are swept instead of silently skipped). Existence-gated by ONE `db.list_graphs()` (#2251: a query against an ABSENT graph MATERIALIZES it). **Control-plane cost, which the per-graph table does not cover and which scales with TENANTS:** the walk is SERIAL over every org, and on the Supabase lane `_sweep_graph_list` is ~4 PostgREST round trips per org (`org_graph_name` 1 + `graph_metadata` ~3), i.e. ~4N/min at the 60 s default against the SAME control plane the auth path uses. A similar walk already runs HOURLY (`registry-cron.sh` → `POST /v1/internal/backups/sweep`) — but NOT the identical one: the hourly endpoint enumerates ELIGIBLE orgs (`enumerate_eligible_orgs`: tier ≠ free AND backup_enabled), while this one enumerates EVERY org (`enumerate_orgs`), so this is that walk at 60× the cadence over a strict SUPERSET of the population; and once ~4N exceeds the interval the knob's bound (“a retry resumes within ≤ one interval”) degrades to “≤ one pass duration”. The loop still cannot overlap itself, so the failure mode is latency, not storming. Two things it does NOT have: the URI lane's per-org `MATCH (g:Graph {org_id: $tid})` has no index in `_ensure_registry_indexes`, and there is no per-org timeout — filed as **#9048** rather than paid down here (an index on a shared registry graph plus a concurrency decision). The §3 per-graph figures bound the DISPATCH phase only. |
| The pass | `hosted_api._webhook_retry_sweep_pass` | Bounded concurrency on its OWN `ThreadPoolExecutor` (never the loop's shared default pool — #3060/#3498), per-graph failure isolation, and an in-flight set holding the "one in-flight dispatch per graph" invariant (a guard against a FUTURE concurrent-pass shape: today the loop `await`s each pass and `_start_liveness` cancels a prior task, so a pass longer than the interval delays the next pass rather than re-entering one). The pool's workers are NON-daemon and are joined at interpreter exit, so `_dispose_webhook_sweep_executor` is registered on `threading._register_atexit` (`shutdown(wait=False, cancel_futures=True)`, the #3087 pattern) — otherwise a SIGTERM during a pass waits out every queued dispatch's POSTs. |
| The lifecycle | `hosted_api._webhook_retry_sweep_loop` + `_lifespan` | Armed as a BACKGROUND task in the startup half (#2953: never awaited before `lifespan.startup()` returns, or the machine accepts no traffic until a fleet-wide pass finishes), in its OWN `try` (a bad interval disables the sweep only — sharing the retention `try` would take event retention, the purge and the OAuth GC down with it), registered in `_LIVENESS_TASK_ATTRS` so shutdown cancels it and a re-entry cannot orphan a second copy. |
| Control plane on a worker thread | `hosted_api._control_plane_source_on_worker` | A sibling of `_control_plane_source`, differing ONLY in the registry lane's lifetime: `_registry_anchor()` (process-lifetime) instead of a fresh-and-never-closed handle per call — an hourly operator can afford the fresh handle, a 60s pass cannot. That lifetime is real on BOTH lanes: URI mode now delegates to the #4661 URI-keyed `_registry_probe_sdk()` cache (one handle per process, closed on a URI change, reset by the suite's hook) instead of constructing a fresh `TortoiseSDK(namespace="registry")` per call — the old URI branch was the lane that SHIPS (`entrypoint.sh` derives `TORTOISE_DB_URI`), so the claim was false exactly where it mattered, and each call also re-ran 9 `CREATE INDEX` statements against the fleet-shared `registry_control_plane`. The #669 half is unchanged (Supabase mode never constructs the registry). |

## 4. The one refactor this needed

`_dispatch_webhooks` was split into a blocking body (`_dispatch_webhooks_sync`) plus two async
wrappers, because the two triggers must offload to DIFFERENT lanes and must not duplicate the
SDK-build/close discipline. The dream path keeps `asyncio.to_thread` **unchanged** (its historical
lane, and its blast radius is not this issue's); the sweep uses the bounded executor. Both share
one body, so a future change to the SDK-open rules lands once.

## 5. Verification (the issue's own checklist)

| Surface | Layer | Test |
|---|---|---|
| sweep trigger | integration | `test_an_idle_tenant_is_retried_by_the_sweep_and_its_cursor_advances` — fail a delivery at `now-2h`, write NOTHING to the tenant graph, run one pass, read the cursor back from the store (asserts it advanced, attempts reset, and the `:GraphEvent` count is unchanged). |
| cost | unit | `test_the_gate_reads_only_and_creates_no_index` + `test_a_graph_with_a_caught_up_subscription_stays_byte_identical` + `test_a_graph_that_does_not_exist_is_never_selected_or_read` (diffs nodes and `CALL db.indexes()`). |
| a corrupt counter must not starve delivery | unit | `test_the_head_read_does_not_starve_a_graph_written_without_the_counter` + `test_a_corrupt_counter_cannot_starve_delivery` (6 params — `-1`, `'not-a-number'`, `True`, `2.9`, `'1'`, `' 2 '` — each a shape the pre-fix `max(0, int(value))` body turns into a head BELOW the real one; asserts the head reaches the real max seq, the gate admits the behind subscription, AND `dispatch_pending` delivers, at all three layers). |
| the head read stays ONE node read | unit | `test_an_absent_counter_falls_back_and_the_counter_stays_one_node_read` (a query-counting handle must issue exactly one `:GraphEventMeta` read on a healthy counter — the corrupt fallback must not tax the steady path). |
| the gate is NOT always due | unit | `test_a_declared_and_caught_up_tenant_is_not_a_sweep_target` + `test_the_gate_counts_exactly_what_the_dispatcher_would_dispatch` (its DECLARED-AND-CAUGHT-UP row) — deliver everything, so the cursor reaches the head and `next_attempt_at` is NULL again, then require `due_subscription_count == 0`, `_due_subscriptions` empty, and the real gate's target list empty (the state every subscription spends its life in; pre-fix the gate returned 1 forever). |
| shutdown | unit | `test_the_sweep_task_is_in_the_shared_liveness_tuple_and_shutdown_cancels_it` + `test_the_sweep_is_armed_as_a_background_task_not_awaited_at_boot` + `test_the_sweep_pool_is_disposed_at_thread_shutdown`. |
| the trigger is actually ARMED | unit | `test_the_periodic_loop_arms_the_sweep` (drives the real loop — the #1890/#4241 shape: a definition-only test is green for the life of the bug) and `test_a_raising_pass_cannot_end_the_loop` (#5381). |

**Mutation-checked** (a test that cannot fail is not evidence): removing the existence gate reds
the absent-graph test; dropping either predicate clause from the gate query reds the parity test
(the temporal clause by the fresh-while-not-behind row, the cursor clause by the caught-up row).

**This table is machine-checked** (#8098 review, P3): an earlier revision of it cited a test that
did not exist at head, so `test_every_test_the_plan_cites_exists` resolves every `test_…` name this
plan names against the test tree and reds on a stale citation. Renaming a test means updating this
table in the same commit.

## 6. Deliberately NOT in scope

* **Cursor monotonicity under two concurrent passes.** The sweep introduces a second trigger, so a
  dream-cycle pass and a sweep pass for the same graph can overlap. The contract already covers it
  (at-least-once + a stable `X-Tortoise-Delivery` id ⇒ the consumer must be idempotent), and the
  in-flight set removes the sweep-vs-sweep overlap. A monotone `cursor` write (`CASE WHEN s.cursor
  < $cursor`) would close the dream-vs-sweep duplicate window too — filed as **#9032** rather than
  smuggled into a trigger PR, because it changes the delivery engine's write, not its schedule.
* **The same outage envelope on the OTHER registry call sites.** `_require_owner` and the bare
  `_org_node` callers have no `except Exception → _raise_503_if_cp_outage` envelope, so a
  registry-lane outage there is a raw 500 rather than 503 `control_plane_unavailable`. PRE-EXISTING
  (the eager anchor bind made it observable, it did not cause it); filed as **#9041** rather than
  widened into this PR.
* **Caching the org→graph walk across passes.** The per-org control-plane fan-out (§3, “The
  enumeration”) is the cost that scales with TENANTS, at 60× the cadence of the hourly backup sweep
  that performs the same walk. A TTL (or every-K-intervals) reuse is the obvious follow-up; NOT done
  here because the cache's invalidation contract belongs with #2081's backup operator, and a stale
  graph list in a *retry trigger* would silently drop a newly created graph's deliveries.
* **Indexing and bounding that walk.** On the URI lane the per-org
  `MATCH (g:Graph {org_id: $tid})` has NO index (`tortoise/sdk.py::_ensure_registry_indexes`
  carries no `("Graph", "org_id")` entry), so it is a label scan per org per pass; and there is no
  per-org timeout. Filed as **#9048** rather than fixed here — an index on a shared registry graph
  plus a concurrency decision is not a review-fix. The per-graph cost rows in §3 bound the
  DISPATCH phase only.
* **Per-graph time bound.** A pass is bounded by the executor size and `TIMEOUT_S` per POST, not by
  a wall clock; a graph with 25 healthy-but-slow subscriptions can outlive the interval. The
  in-flight set makes that safe (the next pass skips it) rather than queued.

## 7. Review-fix records (the defects the last review rounds found)

* **An unusable event counter must not read as head 0.** `event_head_seq()` is not only the
  sweep's cheap gate: `dispatch_pending` reads the head once and threads it into
  `_due_subscriptions`, whose `s.cursor < $head` clause decides whether a subscription is
  returned at all. Reading a corrupt `:GraphEventMeta.last_seq` as 0 therefore STOPPED DELIVERY on
  that graph — dream cycle and sweep alike, silently — where the pre-fix code was harmless only
  because the delivery path never read the counter. A corrupt counter now falls back to the
  events' own `max(:GraphEvent.seq)`, whose head is at or above every deliverable seq (so it can
  only over-admit, never stall). "Corrupt" is the same domain `event_store.next_seq` refuses on the
  write side — a non-number (a numeric STRING included, since the store is explicit that a
  property of another type is refused, never coerced), a negative, a `bool` (an `int` subclass,
  so `int(True) == 1` would pass as a trustworthy head of 1) — plus a NON-INTEGRAL float, which
  `next_seq` does not refuse (it bumps `2.9` to `3.9` and re-issues a seq over an existing event,
  the #4653 class), so it never self-heals. Pinned by
  `test_a_corrupt_counter_cannot_starve_delivery` across all six shapes (`-1`, `'not-a-number'`,
  `True`, `2.9`, `'1'`, `' 2 '`); the healthy counter still costs exactly one node read
  (`test_an_absent_counter_falls_back_and_the_counter_stays_one_node_read`).
* **`_registry_anchor()` binds eagerly in URI mode, so it is resolved INSIDE the outage map.**
  The anchor returns the cached `_registry_probe_sdk()`, whose `_get_proj()` IS the connect, so
  the first registry access of a process raises from the anchor call rather than from the first
  query. Resolved one line above `_membership_role`'s `try`, that failure skipped
  `_raise_503_if_cp_outage` and reached the global handler as a raw 500, falsifying the #2380
  contract stated three lines above the code. Pinned by
  `test_registry_anchor_connect_failure_503` (which raises from the ANCHOR, not from
  `_get_registry()` — the sibling outage tests all raise from the query and cannot see the
  placement) plus its non-outage control.
