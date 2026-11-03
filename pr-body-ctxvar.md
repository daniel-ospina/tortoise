## What

`contextvars.Context` is **single-entry**: `Context.run` raises
`RuntimeError: cannot enter context: <…> is already entered` while any other run
on that same object is still on a stack — this thread's or another's (PEP 567,
[stdlib docs](https://docs.python.org/3/library/contextvars.html)). A stored
`Context` is usable **exactly once**, and the offload idiom stored one:

```python
ctx = contextvars.copy_context()
return await _cp_offload(functools.partial(ctx.run, fn), ...)
```

That is correct per submission and a trap the moment the same wrapped callable
runs **twice** — which a bounded offload produces by design: the bound cancels
the **await**, never the running thread (CPython #87185). Measured at #10612's
head (`a4c131175`, comment 6586045159) on the #4055 cold-embedder re-arm:

```
bound miss; concurrent future cancelled? False done? False
immediately after bound miss, first run still executing: True
RETRY RAISED: RuntimeError : cannot enter context: <_contextvars.Context object at 0x...> is already entered
```

That `RuntimeError` is **not** a `ControlPlaneOffloadError`, so it escaped the
offload seam's fail-closed mapping and landed on `/v1/search` and
`/v1/topics/{t}/summary`'s blanket `except Exception` as HTTP **500**
`"Search failed"` / `"Topic summary failed"` — no `error_code`, **not
retryable**, and a worse status than the misnamed 503 it replaced.

Reproduced on `origin/main` at the seam, with main's own primitive
(`run_control_plane_call` + `_await_future`), before any change:

```
$ uv run python /tmp/repro_ctxvar.py
bound miss: refused=False
worker still inside ctx.run after the bound miss: True
RETRY RAISED: RuntimeError : cannot enter context: <_contextvars.Context object at 0x7f6033494d80> is already entered
```

## Fix the mechanism, not the loop

* **`monitoring.confine_context(fn)`** — returns a callable that re-materialises
  a **fresh copy of the submitting context on every invocation**. Drop-in for
  the idiom it replaces: the snapshot is still taken in the **submitting**
  thread (so a var the callable sets stays confined to that invocation and
  cannot survive into the next request a process-lifetime pool thread serves —
  the isolation `copy_context` existed for), but a retry, a re-submission or an
  overlapping worker can no longer collide. Cost: one `Context.copy()` per
  invocation — O(1) over a shared HAMT, measured ~0.06 µs flat vs ~2.3 µs @ 10
  vars and ~600 µs @ 1000 vars for the linear `items()` + `var.set()`
  re-materialisation it first used.
* **`hosted_api._submit_off_loop`** and **`hosted_api._graph_offload`** — the two
  places the copy was applied — now go through it, so no caller can reintroduce
  the one-shot shape without deleting a documented seam.
* **`ContextConfinementError` fails CLOSED, by OWNERSHIP.** If the context cannot be
  reproduced, `_cp_offload` maps it onto the same **named, retryable 503** a
  refused submission gets (`graph_unavailable` / `control_plane_unavailable`).
  It is never swallowed into a plausible answer and never runs under the
  worker's **ambient** context — on a process-lifetime pool thread that still
  carries the previous request's actor/tenant vars, i.e. it would answer another
  tenant.

## Why this is not a duplicate of #10612's branch fix

The branch fixes its own loop (re-copy per attempt). This removes the trap from
the **shared seam main owns**, so `_read_offload` cannot reintroduce it and the
next caller cannot inherit it. It also does not re-litigate the branch's
disposition of #10614 / #10613 / #10621.

## Tests — `tests/test_context_confinement.py` (9 passed, 4.1–4.9 s)

1. `test_the_one_shot_idiom_cannot_be_resubmitted` — **the recorded
   reproduction**, pinned as a permanent mechanism test: one `Context`, a bound
   miss over a still-running callable, then a resubmission → `RuntimeError:
   already entered` (verbatim above). This is the arm that must keep raising.
2. `test_confined_context_survives_a_resubmission_while_the_first_runs` — the
   same two overlapping submissions through `confine_context`: both complete.
3. `test_concurrent_requests_keep_their_own_tenant_context` — two concurrent
   requests on the graph pool, forced to overlap by a barrier, each reading
   `mcp_auth._current_org_id` / `_current_graph_id`: each sees its own tenant,
   and a var one invocation writes never appears in the other or back in the
   request context.
4. `test_unreproducible_context_fails_closed_and_named` — when the context
   genuinely cannot be reproduced: `ContextConfinementError` naming the callee,
   `fn` **never runs**, and at the seam the response is the labelled retryable
   **503 `graph_unavailable`** — not a bare 500, not a silent default.
5. `test_graph_offload_hands_over_a_resubmittable_callable` — takes the callable
   `_graph_offload` actually hands the pool and invokes it twice, overlapping.
   **Call-site mutation:** revert `_graph_offload` to
   `functools.partial(copy_context().run, fn)` → this test alone goes red
   (`RuntimeError` at the second invocation).
6. `test_submit_off_loop_preserves_positional_and_keyword_args` — the
   `partial` → `*args, **kwargs` refactor keeps arity, kwargs and the
   off-thread execution.
7. `test_a_context_error_from_inside_the_callable_is_a_domain_error` — a
   `ContextConfinementError` raised from INSIDE the callable is a domain error
   and propagates unchanged; the seam's 503 arm must not claim "no work was
   done" about work that had started.
8. `test_a_NESTED_wrapper_failure_keeps_propagating` — the same, with the real
   nesting: two confined wrappers, the second one's context failing while the
   outer has already executed a step. Pins that `_cp_offload` maps by OWNERSHIP
   (`exc.origin is fn`), not by type.
9. `test_submit_off_loop_still_propagates_the_caller_context` — the seam's
   documented reason to exist ("preserving contextvars"): the caller's value
   reaches the worker and the worker's write does not leak back. A dropped copy
   would be SILENT (`graph_ops._ACTIVE` defaults to `None`, so graph-op
   accounting becomes a no-op), not loud.

### Review round 2 (all P2 — accuracy / coverage; mechanism unchanged)

* `_fresh_context` now uses **`Context.copy()`** (3.7+, O(1), shared HAMT)
  instead of a manual `items()` loop — and the false claim that justified the
  loop ("the supported CPython versions have no `Context.copy()`") is gone.
* The docstrings that read as "main's seam produced this 500" are corrected: it
  is a **latent** trap here (one wrap, one submission — it cannot fire) and a
  **measured** one on #10612's re-arm loop, which is where the blast radius is.
* `confine_context`'s "two other in-repo sites" is **three**
  (`extractor_v2._call_once` also does `copy_context()` + `run` per call). All
  three build their `Context` per call, so none can re-enter.

**Helper mutation (red):** re-introduce the one-shot context inside
`confine_context` (`ctx = snapshot`) → 3 of 9 red with the measured error:

```
FAILED tests/test_context_confinement.py::test_confined_context_survives_a_resubmission_while_the_first_runs
FAILED tests/test_context_confinement.py::test_unreproducible_context_fails_closed_and_named
FAILED tests/test_context_confinement.py::test_graph_offload_hands_over_a_resubmittable_callable
E  RuntimeError: cannot enter context: <_contextvars.Context object at 0x...> is already entered
   tortoise/monitoring.py:2433: RuntimeError
```

**Neighbouring suites:** `tests/test_control_plane_offload_3498.py`,
`tests/test_monitoring.py`, `tests/test_health_ready_nonblocking.py`,
`tests/test_read_routes_loop_responsiveness.py` → 315 passed in one batch run;
a later batch run went red on 2 tests + 1 error with redislite's
`Instance quit answering … /tmp/tt_*/redis.socket` (box at 2 GB free RAM), and
all three re-run **green in isolation** (16 passed) — environmental, not a diff
regression. `ruff check` clean; `tools/ci_selection.py --integrity` exit 0
(the new test file is registered on both the `api` and `core` surfaces, with a
`durations:` row).

## Production

`api.premiselabs.co` proves the route is live but not the defect: `GET /health`
→ `200`, `GET /v1/search` → `401 {"detail":"Missing Authorization header"}`.
The trigger (a re-arm loop reusing one `Context`) exists only on #10612's
unmerged branch, so there is **no live symptom to photograph** on either side;
this change is behaviour-preserving for every current call site (each submits
once) and removes the trap before the branch lands. `DEPLOY-OWED`: the next
`main` deploy carries the seam fix; nothing else is needed from the owner.

Refs #10612
Refs #10602
