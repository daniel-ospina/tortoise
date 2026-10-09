---
title: "Lift the one-time embedder load out of the transport wait bound (#4055) — Implementation Plan"
type: engineering
domain: capability
doc_status: draft
created: 2026-10-08
subjects.team: epistemic-team
aboutSubjects: Tortoise
aboutObjects: "#4055, #3834, #3993, #6960"
---

<!-- research-path: https://github.com/daniel-ospina/tortoise/issues/4055 -->

# Lift the one-time embedder load out of the transport wait bound (#4055) Implementation Plan

## Problem (verified at `origin/main` `d1ac1699`)

The issue was filed against `quota._ASK_TIMEOUT_S` (lowered 60s → 10s by PR #4020) and the
selfhost ask route. Both are now gone: `_ASK_TIMEOUT_S == 60`, every product caller of
`run_ask_bounded` was removed in #3849 (only `tests/test_quota.py` drives it), and `selfhost.py`
serves no ask route. The specific defect as written is obsolete.

The **same root** the issue's own follow-up comments re-homed onto is live:
``_TRANSPORT_WAIT_BOUND_S = 10.0`` (`tortoise/mcp_auth.py`) wraps the *whole* REST request
(`hosted_api.WaitBoundMiddleware`) and the *whole* MCP tool dispatch
(`mcp_server._await_under_mcp_wait_bound`). The first embedding request after a process start
runs `EmbeddingModel.get()`, whose one-time load is documented at
``EmbeddingModel._LOAD_TIMEOUT_S = 90.0`` (270.0 in the test lane, #6960) and measured at
~27s cold — far above the 10s bound. So the first embedding request is refused 504 while the
load is still running.

The bound's pre-fix shape on `origin/main` is a bare
``asyncio.wait_for(asyncio.shield(task), _TRANSPORT_WAIT_BOUND_S)``, so ANY handler that runs
the load past the bound is refused 504 — pinned deterministically by the new
``test_rest_request_running_a_cold_setup_is_not_refused`` (200 only because of this fix). The
real-load end-to-end case is env-dependent — a warm HF cache on a fast box finishes the load
inside 10s — which is why `main`'s `python-ci` red **rotates between unrelated tests**: every
one of them is the first bounded embedding request of its shard.

## Owner decision

Issue #4055, owner comment 5845912938: **Option 2 — lift the one-time load out of the bounded
section.** The bound itself (10s) is the owner's ruling and is not in question; only the
*placement* of process initialization relative to it changes. "It times the *waiting*, not the
*setting up*."

## Approach

Make the one-time embedder setup **clock-exempt** at the two transport-bound seams: the bound
measures the request's own wait, and a load that runs during the wait is drained outside it.

This is Option 2 read literally against the live surface. It is deliberately **not** a startup
pre-warm (Option 1, which the owner rejected: it changes the selfhost startup contract and needs
no change here) and **not** a route/tool allowlist (which would drift and would force a model load
on shards that never embed).

1. `tortoise/embeddings.py` — `EmbeddingModel` records the one-time load's wall clock
   (`_one_time_setup_started_at`) and whether it is running (`threading.Event`), and exposes
   read-only helpers. Both are stamped around the load in `__init__`; cleared in `_reset()`.
   A `background_load()` context marks a load as a pre-warm (invoked by `warm_up()` and the
   hosted `_prewarm_embeddings` thread), so a load started for process setup is NOT stamped:
   it belongs to no request, and a request that merely overlaps it must not be exempted.
2. `tortoise/mcp_auth.py` — one shared async helper `await_under_wait_bound(task, timeout,
   *, reference)` (the bound's constants already live here because both surfaces import this
   module). It awaits the shielded task; on a would-be breach it grants **one** extension when a
   **request-owned** one-time embedder load started during this request (`reference` = the
   request's arrival), drains the load (bounded by the embedder's own `_LOAD_TIMEOUT_S`), then
   re-arms the full bound for the request's own work. A genuine breach (no request-owned setup)
   raises `TimeoutError` exactly as before. A background pre-warm never qualifies.
3. `tortoise/hosted_api.py` — `WaitBoundMiddleware` uses the helper with `reference=t0`.
4. `tortoise/mcp_server.py` — `_await_under_mcp_wait_bound` uses the helper with the transport
   arrival (or seam entry off-HTTP) as `reference`.

### Why clock-exempt rather than pre-warm

- No startup change (honours the owner's rejection of Option 1).
- No load for requests that never embed — the reverse of a route allowlist, which would have to
  be kept in sync with 126 routes / ~80 tools and would load a model on shards that never embed.
- `TORTOISE_EMBEDDER_WARMUP=0` (the test-lane opt-out, #7015) is honoured: the load happens only
  because a request demanded it, exactly as it does today. The change is *when* it is charged,
  not *whether* it runs.

## Acceptance criteria

- A bounded request whose handler starts a one-time embedder load outlasting the bound is **not**
  refused: it completes (load drained outside the bound), then the bound applies to its own work.
- A bounded request with no one-time setup in flight is refused at the bound, unchanged
  (`test_transport_wait_bound.py` suite stays green).
- A **background pre-warm** does not stamp the setup clock, so an unrelated slow request that
  merely overlaps a pre-warm is still refused at the bound (pinned at the embedder and at the
  middleware).
- `_TRANSPORT_WAIT_BOUND_S` stays `10.0` for warm asks (the existing pin is untouched).
- The exemption is granted **at most once** per request.
- No MCP tool or `TortoiseSDK` public method added/removed/renamed.

## Tests

- `tests/test_transport_wait_bound.py` (new):
  - helper unit: extension granted once when a request-owned one-time setup started during the
    wait; the total wait is setup + a full bound; a task outliving setup + bound still breaches.
  - helper unit: no request-owned setup → breach at the bound (regression pin for every existing
    case). A load started BEFORE the request, even while in progress, is not exempted.
  - embedder unit: a `background_load()` load stamps nothing; a request load stamps.
  - REST: middleware over a fake app that starts the one-time embedder load mid-flight and
    outlives a fast bound → 200, not 504; and the same app without the load → 504.
  - MCP: the seam over a tool whose dispatch starts/outlives the load → no refusal.
- `tests/test_graph_write_loop_responsiveness.py` — the existing `[points]` case is the
  end-to-end acceptance check (run manually; it is slow by construction because it pays the real
  load).

## Rejected alternatives

1. **Selfhost lifespan pre-warm** (Option 1). Owner rejected: changes the selfhost startup
   contract (a boot now depends on a model download). Would also be inert in the test lane
   (`TORTOISE_EMBEDDER_WARMUP=0`), so it would not fix the rotating CI red.
2. **Route/tool allowlist for pre-warming.** Drift-prone across 126 routes and the tool registry;
   loads a model for shards that never embed; requires a new registry field.
3. **Raise `_TRANSPORT_WAIT_BOUND_S` while the embedder is cold.** Breaks
   `tests/test_transport_wait_bound.py` (its `fast_bound` patch would be overridden while the
   embedder is cold) and re-derives the owner's pinned constant.
4. **Test-only fix** (pin pre-warm in the loop-responsiveness test, as #3993 did for the selfhost
   shape test). Leaves the product defect (selfhost MCP first tool call, hosted REST first write)
   in place, which is what #4055 asks to fix.

## Out of scope (filed separately if not already tracked)

- The transport bound's **contention-blindness** under CI test-DB concurrency (issue #4055
  comment 5882110069). This change does not re-derive the bound; it only keeps process
  initialization from being charged to it.
