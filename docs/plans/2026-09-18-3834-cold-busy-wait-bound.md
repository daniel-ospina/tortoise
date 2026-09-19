---
title: "#3834/#3993 Implementation Plan — the cold/busy ask wait bound + legible refusal + retry"
type: engineering
domain: platform
doc_status: draft
created: 2026-09-18
subjects.team: organisation-design-team
aboutSubjects: tortoise
aboutObjects: hosted-api
---

<!-- issue: https://github.com/daniel-ospina/tortoise/issues/3834 -->
<!-- issue: https://github.com/daniel-ospina/tortoise/issues/3993 -->
<!-- scope: docs/plans/2026-09-18-3834-cold-busy-wait-bound-scope.md -->

# Implementation plan (cycle 5) — #3834/#3993: cold/busy wait bound + legible refusal + retry + measurement

Worktree: `.worktrees/feat/3834-cold-busy-wait-bound` (branch from `origin/main` @ `984815753`).

> **Scope of this document's code references.** This is a dated plan and review record. Any
> `<file>.py:<line>` reference below is **as of the section that cites it** and is expected to drift as
> the branch moves — a reading aid, not a maintained claim. The **code** citations this change adds or
> rewrites (comments and docstrings) are symbol-based, so they cannot go stale. (Pre-existing references of
> that kind elsewhere in this repo, whose targets shift whenever the target file is edited, are the
> subject of #4049.) A `## Cycle-*` section or a
> plan-body sentence is likewise the record **as it stood when written** — including the mandated
> capped-exit disclosure and the plan-review tallies that belong to that record. Where a later section
> corrects an earlier one, the later section governs.

Design authority: `docs/plans/2026-09-18-3834-cold-busy-wait-bound-scope.md` (AC4 amended twice:
cycle-7 and cycle-8). Domain **(not adversarial)**; complexity **standard**. The implementable unit is
**#3993** — its ACs are owned as follows: **AC1 = the latency-bound test in S10** (not S1), **AC2 = S5/S6/S7**
(the effective/canonical refusal on all three surfaces + the tests), **AC3 = S1 + S8** (the constant and the
retry leg), **AC4 = S5** (off-path `duration_ms` emission / allowlist). **#3834** is the owner-decision issue
the plan answers (the value + the retry question).

**Cycle 5 is the owner-authorized final cycle.** It folds **all 14 proportional residues** from the
capped cycle 4 (reviewer #1: R1-1…R1-6; reviewer #2: R2-1…R2-8) plus the four advisory Reviewer-#5
items the owner directed in (`R5-1`, `R5-2`, `R5-4`, `R5-5`) — see `## Cycle-5 changelog`. **`R5-3` is
deliberately NOT folded** and is filed as its own issue (it spans two transports + `schemas.py`; the
plan-pinned refusal already satisfies the legibility requirement). The three harm/race items
(**R1-1**, **R1-2**, **R2-1**) are pre-implementation fixes, not follow-ups.

## Design decisions (settled at scope)
- **D1** Bound = **10 s** (`_ASK_TIMEOUT_S`, from 60); refusal advertises **2 s**
  (`ASK_BUSY_RETRY_AFTER_S`).
- **D2** Refusal status/code **unchanged** (`504` + `timeout`) — plan-pinned. Legibility added:
  `Retry-After` header + body `retry_after` + static `message`.
- **D3** Retry ships in `tortoise/sdk.py::_post_ask` **through**
  `tortoise/retry.py::call_with_predicate` (`marker_armed=False`, `retries=ASK_RETRY_ATTEMPTS-1`,
  `delay_for`, `deadline`), with the plan's "no auto-retry v1" pin reopened in its own home.
- **D4** The MCP transport's retry is **not** here (agent-infra #1174); the **boundary** is.
- **D5** `duration_ms` persisted with the existing writer, off the request path, event `ask_request`,
  allowlist `+duration_ms`.
- **D6** `SLO_MS`/`/v1/context` and the reflex fail-open path untouched.

## Steps

**S1 · `tortoise/quota.py` — the bound**
- `_ASK_TIMEOUT_S = 10` (from 60); `ASK_BUSY_RETRY_AFTER_S = 2`.
- The `_ASK_TIMEOUT_S` comment carries ALL of: the derivation (exact counts: **1 of 573** measured asks
  > 10 s, and it is the 21.759 s max, a *successful* slow ask), the transport caveat (MCP
  per-tool-call, not the REST wait), the **15 s anchor named exactly** (*the narrowest budget any hosted
  client exposes — its connect budget; D-12's anchor — NOT an ask caller's per-call timeout*), the
  departure from `SLO_MS = 300` naming `/v1/context`, the exec-floor consequence (`acquire_timeout`
  55 s → 5 s; a started ask keeps ≥5 s), and the Option-C trade (the 10–60 s band is deliberately
  refused).
- Update `AskBoundedTimeoutError` + `run_ask_bounded` docstrings, the `quota.py:662` "no real 60s
  sleeps" comment, and the `:767-780` window comments.

**S2 · `tortoise/schemas.py`** — `ASK_BUSY_MESSAGE` (**no literal number**) + `__all__`.
- The pin is `assert not any(ch.isdigit() for ch in ASK_BUSY_MESSAGE)` (**R5-5**). The earlier
  formulation — checking only the two digit-strings `"10"` / `"60"` — is **false as pinned**: the
  spelled-out forms `"ten seconds"` / `"1 minute"` would pass it while still carrying a literal
  number. The single source of truth for the number is `_ASK_TIMEOUT_S`, so no digit may appear at all.

**S3 · `tortoise/exceptions.py`** — `AskTimeout.retry_after` optional kwarg (default `None`) +
docstring. **No new exception type.** (`:232-236`; keyword-only with a default ⇒ additive.)

**S4 · `tortoise/retry.py` — the primitive learns a floor, a deadline, and injectable seams**
- New module-level **seams**, defined as **call-time indirection functions, never import-time aliases**
  (an alias would silently defeat the existing `monkeypatch.setattr(retry_mod, "time", shim)` shims):
  ```python
  def _sleep(seconds: float) -> None:
      """Injectable seam — tests patch ``retry._sleep``; ``time`` is read at call time so the
      existing ``retry.time`` shims (tests/test_ingest_v2_parallel.py:_silence_retry_sleep) keep working."""
      time.sleep(seconds)

  def _monotonic() -> float:
      return time.monotonic()
  ```
  `retry.py:163` is currently the **only** `time.` use (`time.sleep(wait)`); it becomes `_sleep(wait)`.
  `_monotonic` is new (the deadline). Both are additive and inert for every existing caller (every
  `call_with_predicate` call site passes **keyword** arguments, so appending params shifts nothing).
- Keyword-only `delay_for: Callable[[BaseException], float | None] | None = None`,
  `deadline: float | None = None`. Defaults inert.
- Exact arithmetic (guards spelled out):
  ```python
  wait = min(base ** attempt, cap) * (0.5 + random.random() / 2)
  if delay_for is not None:
      advertised = delay_for(e)
      if advertised is not None:
          try:
              floor = max(0.0, min(float(advertised), cap))   # nan/-5.0 -> 0.0; inf -> cap
          except (TypeError, ValueError):
              floor = 0.0                                      # a malformed hint never escapes
          wait = floor + random.random() * floor               # >= floor, <= 2*floor
  if deadline is not None:
      remaining = deadline - _monotonic()
      if remaining <= 0:
          raise                       # bare: re-raise the caught exception, unwrapped
      wait = min(wait, remaining)
  ```
  `max(0.0, min(...))` is required **and its argument order is load-bearing** (`max(nan, 0.0) is nan`;
  `max(0.0, nan) == 0.0`), because `time.sleep(nan)`/`time.sleep(-5)` raise an untyped `ValueError`.
- Docstring: the deviation from Full Jitter (the advertised value is an RFC 9110 §10.2.3 minimum, so
  jitter is additive above the floor); **`cap` bounds the floor, so a single honoured sleep is ≤ 2×cap**;
  the deadline semantics ("no retry sleep *begins* at/after this instant", sleep end clamped); defaults
  inert.
- Tests (`tests/test_retrieval.py`) using the **new seams**: floor honoured
  (`floor <= wait <= 2*floor`); `-5.0` / `nan` / a **non-numeric** string floor (`"not-a-number"`) →
  treated as `0.0`, never raised; **deadline stop** driven via
  `monkeypatch.setattr(retry_mod, "_monotonic", …)` so no real clock advance is needed, asserting the
  **original** exception is re-raised; defaults path unchanged.
  - **R1-3:** the input must be named as a **non-numeric** string. `"a string floor → 0.0"` is wrong for
    a *numeric* string — `float("3") == 3.0`, so it reaches the floor path rather than the coercion
    path. Coercion of the numeric string `"3"` is **pinned separately** (`floor == 3.0`) so the two
    behaviours cannot be confused again.
  - **R1-5:** the finiteness check in `_ask_advertised_delay` / the 504 parse uses the **no-import**
    form `0 <= ra < _ASK_RETRY_AFTER_CEILING_S` (**`inf` is NOT a builtin** — see the
    implementation note below; the earlier "`inf` is a builtin" wording was wrong).
  - **Jitter must be PROVEN, not merely bounded (cycle-5 findings).** `floor <= wait <= 2*floor` is
    satisfied by a **constant** `wait == floor`, so it does not test the `random.random()` term at all.
    The retry test records **two** sleeps via the `_sleep` seam and asserts both that each lies in
    `[floor, 2*floor]` **and** that they are **not all equal** — so a mutated-away jitter term goes RED.
  - **The deadline clamp can reduce a sleep BELOW the advertised floor, and that precedence is stated.**
    The floor docstring claims `Retry-After` is an RFC 9110 §10.2.3 *minimum*, but the deadline branch
    does `wait = min(wait, remaining)` — with 0.5 s remaining and a 2 s floor the sleep is 0.5 s. The
    docstring says so explicitly (**the deadline clamp wins**; the floor is a minimum for the *sleep
    that is taken*, not a licence to sleep past the caller's deadline), and one unit test pins the
    overlap case (`remaining < floor` → the sleep is `remaining`).
  - **The retry's per-org IN-FLIGHT-cap interaction is stated (cycle-5 finding).** A 504 leaves the
    **shielded** reader future running, so `run_ask_bounded` does not release the org's in-flight slot
    until that reader completes. A retry 2–4 s later can therefore be refused with **429
    `in_flight_limit`** — predicate-FALSE, so it propagates immediately as `AskInFlightLimit` and the
    caller sees a *different* refusal than the advertised `timeout`. Recorded in Risks and in the
    `_post_ask` docstring; **never asserted as a count** (it races the shielded future).
- **Compatibility pin** (regression guard for the seam form): one test asserts a
  `monkeypatch.setattr(retry_mod, "time", _shim)` still suppresses the sleep — i.e. the indirection is
  *not* an import-time alias.

**S5 · `tortoise/hosted_api.py` — REST refusal + off-path emission**
- Ask route (`:5305-5335`) `except AskBoundedTimeoutError:` → `HTTPException(504, detail=CODE_TIMEOUT,
  headers={"Retry-After": str(ASK_BUSY_RETRY_AFTER_S)})`; emit the refusal's own elapsed (`t0`, in scope)
  off-path with `status="timeout"`, `error_kind="ask_bounded_timeout"`, **before** re-raising. The
  emission arms are exactly the success site and this handler — the `AskValidationError` / `AskQuota`
  / `AskInFlightLimit` / reader / retrieval arms emit nothing.
- Success site: after `result["duration_ms"] = …` (`:5335`), emit off-path (`status="ok"`).
- Allowlist `_ALLOWED_ANALYTICS_PROPS`: add `"duration_ms"` **with its own per-key comment** — the
  set already carries per-family comments (`# #889: MCP tool-call telemetry`, `# #3359: capture_cost`),
  so the declaration has a home that exists. The comment must state the **per-surface** split (R5-6/
  suppressed-#5 item): `duration_ms` = **hosted REST request wall-clock** (receipt → response);
  `latency_ms` = **MCP tool-call** duration; the SDK-local lane does **not** write this row; the two keys
  are **different quantities and must never be aggregated or asserted equal**. (Existing assertion in
  `test_capture_cost_measurement.py:295` is a **subset** check → cannot break; and one new test asserts a
  `latency_ms`-producing event never carries `duration_ms` and vice versa.)
- **R5-4 — make the allowlist's silent strip AUDIBLE — and do NOT break the never-raise contract.**
  `_track_analytics_event` drops every property key not on the allowlist with **no signal at all**; that
  silent-strip class has already been "fixed" twice by *extending the allowlist* (#3359, commits
  `9003debbf`, `ce1bf9716`), so the third instance this PR adds (`duration_ms`) must not be repaired the
  same silent way. Three corrections the cycle-5 review forced, all of them load-bearing:
  - **The symbol is `_logger`, not `_log`.** `hosted_api.py:106` binds `_logger = logging.getLogger(__name__)`;
    `_log` exists **only in `mcp_server.py:39`** (and as a function-local at `hosted_api.py:1765`). A
    `_log.debug(...)` in `_track_analytics_event` is a **`NameError`** — and because that function is
    documented *"Never raises"* and its strip branch fires on **keys that exist today** (`plan`/`tier`
    from the Stripe caller at `:23469`, neither of which is allowlisted), the NameError escapes the
    writer, turns the Stripe webhook's 500 path loose, and — the event marker already having been
    claimed — **drops the billing notification permanently**. The strip log must therefore be inside its
    own `with suppress(Exception)`/`try` so the never-raise contract is preserved by construction.
  - **The "comprehension's failure path" does not exist.** A dict-comprehension filter has no `else`.
    The filter is restructured into an explicit loop:
    ```python
    props = {}
    for k, v in (properties or {}).items():
        if k in _ALLOWED_ANALYTICS_PROPS:
            props[k] = v
        else:
            # #3993 / R5-4: the strip was SILENT and this class recurred twice
            # (#3359) — an unlisted measured field vanishes with no trace.
            _logger.warning("analytics prop stripped (not allowlisted): %s", k)
    ```
  - **`.debug` is not audible.** `hosted_api` configures no log level (no `setLevel`/`basicConfig`), so a
    DEBUG line is emitted nowhere by default — the "silent to audible" claim would be unenforced. The
    level is **`_logger.warning`**, matching the sibling pattern the R5-2 fold already mirrors
    (`_capture_slot_decrement`, `:240-252`), and it **is** covered by a test (S10).
- **R5-1 — the dispatcher is the MODULE'S OWN idiom, not a copy from another module.** The plan
  previously reproduced `mcp_server._pending_telemetry` (a *set* of futures + `add_done_callback`
  discard). `hosted_api` already owns that job: **`_retain_feed_task(key, task)` (`:4341`, 7 call sites,
  retained fire-and-forget + done-callback pruning, *in the module being edited*)**. The ask emission
  uses it:
  ```python
  fut = loop.run_in_executor(None, _write)          # SUBMITS the write now
  _retain_feed_task(f"ask-telemetry-{id(fut)}", fut)
  ```
  - **ONE form, pinned (cycle-5 finding F1 — three contradictory statements removed).**
    `run_in_executor` is chosen over `create_task(asyncio.to_thread(_write))` deliberately:
    `run_in_executor` **submits the worker to the executor synchronously**, whereas
    `create_task(to_thread(...))` only *schedules* a coroutine — if the loop never ticks again the write
    never happens **and the inflight counter is never decremented**, which is exactly the leak the
    counter fixes exist to prevent. Synchronous submission makes the drain's "counter reached 0"
    guarantee true by construction.
  - **`_retain_feed_task`'s annotation widens from `asyncio.Task` to `asyncio.Future`** (and
    `_SIGNUP_FEED_TASKS`'s value type with it). A `Task` **is** a `Future`, so every existing call site
    still type-checks — this is a strict widening, not a new contract. The docstring additionally
    declares the **two families** now in the store (`"signup-"`/`"block-"+ip` and
    `"ask-telemetry-<id>"`) and the rule that it is mutated **only from the event-loop thread**.
  - The key is **per-dispatch unique** (`id(fut)`), because `_retain_feed_task`'s same-key-replacement
    semantics (documented at `:4344-4350`) would otherwise drop the *only strong reference* to an earlier
    concurrent dispatch when a later one replaced the entry — i.e. exactly the asyncio-GC loss the
    retention exists to prevent. Unique keys make each done-callback pop its own entry, so the dict is
    bounded by in-flight dispatches.
  - **The daemon-thread branch does NOT go through `_retain_feed_task`** (cycle-5 finding F5): a
    `threading.Thread` has no `add_done_callback`, and it does not need retention — `threading._active`
    holds it alive for its lifetime. This is stated, not left implicit, because the previous wording
    ("retains the dispatch") was false for one of the two branches.
  - This **deletes `_ASK_TELEMETRY_FUTURES`** from the design: there is one retained registry in the
    module (`_SIGNUP_FEED_TASKS`, reached only through `_retain_feed_task`), not two.
  - **Recurrence guard — scoped HONESTLY (cycle-5 finding F4).** One contract test asserts, for the
    **three off-loop dispatchers this unit knows about** (`_emit_ask_latency_off_path`,
    `mcp_server._emit_mcp_tool_call_telemetry`, the `:8940` capture_cost `to_thread` site), that the
    write body runs on a thread **≠** the calling loop thread. Two honesty constraints on it:
    (i) thread identity proves *off-loop*, **not** *non-blocking* — the `:8940` site is an **awaited**
    `to_thread`, so it passes this guard while still gating the response; the guard is therefore an
    off-loop guard only, and S10's (a-ii) bounded-release probe is what proves non-blocking for the new
    helper; (ii) it must **not** claim to cover "every in-repo analytics dispatcher" — five pre-existing
    sites call the blocking writer **inline on the loop** (`hosted_api.py:18692`, `:19753`,
    `:19961`, `:20037`, `:23469`); that pre-existing defect is **filed as its own issue**, not silently
    absorbed into a guard that would go red if it were made family-complete.
- `_emit_ask_latency_off_path(org_id, duration_ms, status, error_kind=None)`:
  - builds `{"duration_ms", "status"[, "error_kind"]}`;
  - **increments `_ASK_TELEMETRY_INFLIGHT` exactly once** (under `_ASK_TELEMETRY_LOCK`) and decrements it
    exactly once — in the worker's own `finally`;
  - **R1-2 (race) — BOTH sides take the lock, and the lock's TYPE is pinned.** The increment was pinned
    under `_ASK_TELEMETRY_LOCK` but the decrement was not: a lost update leaves the counter reading `-1`
    while a write is still in flight, and the `<= 0` drained rule then returns early → the JSONL row may
    not exist when the test reads it, silently defeating the hermetic fixture's whole purpose. Fix:
    `_inc()` / `_dec()` **helpers that both take the lock**; the `<= 0` rule stays as *defence*,
    explicitly **not** as the race fix.
    - **`_ASK_TELEMETRY_LOCK = threading.Lock()`** — pinned explicitly (cycle-5 finding): the decrement
      runs **inside the worker thread**, so an `asyncio.Lock` would be unusable there. `hosted_api`
      contains both conventions (`_SIGNUP_LOCK` is `asyncio.Lock`; `_CAPTURE_IN_FLIGHT_LOCK` at `:184`
      is `threading.Lock`); this one mirrors `_CAPTURE_IN_FLIGHT_LOCK`.
  - **R5-2 — the decrement keeps the module's AUDIBLE signal.** `_capture_slot_decrement`
    (`:240-252`) *logs* an under-flow; the plan's silent decrement + the fixture reset would make a
    leak invisible. `_dec()` therefore mirrors it: a decrement with nothing in flight logs a
    `_logger.warning` naming the counter (never a bare clamp) — and S10 asserts the warning fires.
  - **R1-1 + R1-6 (harm) — the dispatch failure NEVER propagates.** The plan's failure path `raise`d,
    which at the two unguarded call sites replaces the pinned **504 with a 500** (or a 200 with a 500)
    — contradicting the very precedent the plan cites (*"Never raises, never blocks"*,
    `mcp_server.py`'s `_emit_mcp_tool_call_telemetry`). Both dispatch branches **swallow + log**, and the keyed decrement stays
    inside:
    ```python
    fut = None
    try:
        fut = loop.run_in_executor(None, _write)
        _retain_feed_task(f"ask-telemetry-{id(fut)}", fut)
        return
    except BaseException:
        if fut is None:          # only when no future was constructed
            _dec()               # the worker's own finally never runs
        _logger.warning("ask latency telemetry schedule failed", exc_info=True)
        return                   # never re-raised — telemetry must not change a status code
    ```
    - The logger is **`_logger`** (the module's own binding, `:106`) — **never** `_log`, which is
      `mcp_server.py`'s name. A `NameError` raised inside this `except BaseException:` block would
      escape the guard and produce the exact 500 the guard exists to prevent (cycle-5 findings F1/F2:
      the same class as the `math.isfinite` NameError the plan already fixed).
    - **R1-6 / R2-8 — the daemon branch gets its OWN keyed guard, not "the same" one.** The executor
      guard keys on `fut is None`, and no `fut` exists on the thread path; `threading.Thread(...)` is
      constructed **before** `start()`, so a naive port sees a non-`None` object and skips `_dec()` — the
      exact `+1` leak R1-6 was filed to close. Pinned explicitly:
      ```python
      started = False
      try:
          t = threading.Thread(target=_write, daemon=True)
          t.start()
          started = True
      finally:
          if not started:
              _dec()
              _logger.warning("ask latency telemetry thread start failed")
      ```
  - dispatches `_write()` via `asyncio.get_running_loop()` → `run_in_executor`, else the daemon thread
    (**not retained** — see above; the daemon branch is a **defensive mirror**, unreachable from the
    async route — see S10).
- `_drain_ask_telemetry(timeout=5.0)`: blocks the CALLING thread until the counter reaches 0 — **truthy
  only for `> 0`, so `<= 0` counts as drained** (defensive against a historical leak) — and on expiry
  **raises `AssertionError` naming the leftover count** (a silent return would let a leak cascade and
  let the hermetic fixture restore the real path while a write is in flight). No event loop required.
  - **R5-2 justification — why a SYNC drain and not `_flush_mcp_telemetry`:** `_flush_mcp_telemetry`
    (`mcp_server.py:~240`) is `async` and therefore needs a running loop; the ask fixture's teardown
    runs on the main thread with **no loop alive** (the route completed), and the drain must also be
    callable from any thread. A blocking counter-wait needs neither a loop nor a future to await, and
    it is the *counter reaching 0* — not the futures completing — that guarantees the write's `finally`
    has run, so the JSONL row is on disk when the assertion reads it. The sync drain is the honest
    primitive for that guarantee; `_flush_mcp_telemetry` cannot supply it without a loop.
- `_reset_ask_telemetry_for_tests()`: counter → 0 **only**. It must **not** clear the shared retained
  registry: the ask entries self-prune on completion, and a prefix-based wipe would drop another family's
  (**signup**) only strong reference — the exact harm the registry exists to prevent. The key namespace
  (`"ask-telemetry-<id>"` vs `"signup-"`/`"block-"+ip`) is stated at `_retain_feed_task` so a future
  reset cannot do it by accident. Used by the fixture's teardown `finally` so one leak cannot cascade
  through a file.
- Path-scoped handler: mirror `retry_after` from the `Retry-After` header **generically** (429
  `quota_exceeded` behaviour unchanged) and add `message` only when `detail == CODE_TIMEOUT`. The
  `in_flight_limit` 429 carries no header → body stays exactly `{"error": {"code": …}}`.
- Hosted ask handler docstring (`:5255-5268`): update the `_ASK_TIMEOUT_S → 504` sentence to name the new
  bound (the **"60/min"** budget rate is a *different* number and stays).

**S6 · selfhost parity** — `selfhost_api.py`: same `Retry-After` header on its 504; `selfhost.py`: same
body mirror (`retry_after` + `message`); `selfhost_api.py`'s ask docstring "60s" → the new bound.

**S7 · `tortoise/mcp_server.py`** — the `except AskBoundedTimeoutError` arm (`:1259-1260`) →
`{"error": {"code": CODE_TIMEOUT, "retry_after": ASK_BUSY_RETRY_AFTER_S, "message": ASK_BUSY_MESSAGE}}`
(import `ASK_BUSY_MESSAGE` from `schemas`, `ASK_BUSY_RETRY_AFTER_S` from `quota` — the arm already
imports `run_ask_bounded` from `quota` and `CODE_TIMEOUT` from `schemas`); `tortoise_ask` docstring
("60s", `:1195`) updated to the new bound.

**S8 · `tortoise/sdk.py` — the retry leg**
- Split the POST/parse/map body (`:13922` onward) into `_post_ask_once(...)` (faithful: `_val_err`, the
  429 header→body `Retry-After` fallback, 502, 504, 402, 404, generic 4xx, residual 5xx,
  `r.raise_for_status()`); `_post_ask(...)` wraps it:
  ```python
  return call_with_predicate(
      lambda: self._post_ask_once(question, question_type=..., question_date=...),
      predicate=_ask_timeout_retryable, retries=ASK_RETRY_ATTEMPTS - 1,
      what="ask POST /v1/ask", base=ASK_RETRY_BASE_S, cap=ASK_RETRY_CAP_S,
      marker_armed=False, delay_for=_ask_advertised_delay,
      deadline=_monotonic() + ASK_RETRY_DEADLINE_S)   # sdk.py:21 already binds "monotonic as _monotonic"
  ```
  (`_post_ask` is gated by `if os.environ.get("TORTOISE_API_URL"):`, so the local/hosted-server lane
  never enters it — the reason AC4's client/server split is coherent.)
- `_post_ask_once` 504 branch: parse `retry_after` from the `Retry-After` header, else from the body
  field (mirroring the existing 429 parse), admit it only when **finite and ≥ 0** (else `None`), then
  `raise AskTimeout(..., source="server", retry_after=ra)`. So no unparseable / fake-server hint can
  reach the predicate or the primitive.
  - **R1-5:** written as `if ra is not None and not (0 <= ra < _ASK_RETRY_AFTER_CEILING_S):` —
    **no `import math`** (`sdk.py` has none, so `math.isfinite` would `NameError`). **`inf` is not a
    builtin** (the earlier wording here was wrong): the module defines
    `_ASK_RETRY_AFTER_CEILING_S = float("inf")`. The comparison is NaN-safe (`0 <= nan` is `False`)
    and import-free.
- `_ask_timeout_retryable(e)`: `isinstance(e, AskTimeout) and e.source == "server" and
  e.retry_after is not None`. `_ask_advertised_delay(e)`: `getattr(e, "retry_after", None)`.
  `requests.exceptions.Timeout`/`ConnectionError` raise `AskTimeout(source="client")` /
  `AskReaderUnavailable` respectively — both predicate-FALSE, so **neither is retried**.
- Constants: `ASK_RETRY_ATTEMPTS = 3`, `ASK_RETRY_DEADLINE_S = 25`, `ASK_RETRY_BASE_S = 2.0`,
  `ASK_RETRY_CAP_S = 30.0`, and **pin `ASK_RETRY_CAP_S >= ASK_BUSY_RETRY_AFTER_S`**.
- Fix `ASK_SDK_TIMEOUT_S` (`:53`) comment + `_post_ask` docstring (new bound, the retry, per-attempt vs
  envelope).

**S9 · docs**
- `docs/plans/2026-08-29-1987-ask-reader.md`: dated AMENDMENT with both `OVERRIDES:` lines (text from
  the scope) + in-place supersede annotations at every enumerated site — retry `:41/:73/:229/:496`;
  bound `:42/:83/:90/:204/:267/:277/:282/:436` + changelog rows `:462/:472/:502/:541`.
- `docs/product/answer-surface.md`: `:198-199` prose, `:211` body shape, `:230` 504 row, `:241-243`
  retry note.

**S10 · tests**
- `tests/test_ask_api.py`
  - **Hermetic analytics autouse fixture** — `_clean_ask_state(tmp_path, monkeypatch)`
    (`:56`, currently takes neither): setup calls the three existing resets **only** — the telemetry
    counter is **not** reset here (see R2-2 below);
    `monkeypatch.setattr(ha_mod, "_ANALYTICS_FALLBACK_PATH", str(tmp_path/"analytics_fallback.jsonl"))`;
    **`monkeypatch.delenv("SUPABASE_URL", raising=False)`** + `SUPABASE_SERVICE_KEY` /
    `SUPABASE_SERVICE_ROLE_KEY` (`raising=False`) — the **URL** deletion is the load-bearing one
    (`_track_analytics_event` short-circuits on `if url and key:`; `_service_key()` also honours
    `SUPABASE_SERVICE_ROLE_KEY`, so the two-name list alone is not hermetic).
    Teardown: `ha_mod._drain_ask_telemetry()` inside `try/finally` whose `finally` calls
    `_reset_ask_telemetry_for_tests()` (bounds a leak's blast radius to one test) **before** monkeypatch
    undo. Safe ordering note: the worker is a plain sync function decrementing in its own `finally`, so
    the drain does not need the loop alive. Without this, ~90 synthetic `/v1/ask` rows per run append to
    the developer's real `~/.tortoise/analytics_fallback.jsonl` — the very evidence the bound came from.
    - **R2-2 — the setup `assert inflight == 0` is DROPPED; the guard is stated, not implied.** As
      specified it could not go red: placed *after* `_reset_ask_telemetry_for_tests()` it is tautological
      (the reset just wrote 0), and placed *before* it fires only on a benign race the test did not
      cause — i.e. an assertion that cannot fail for the reason it appears to exist for. The **real**
      guard is the teardown drain: `_drain_ask_telemetry()` **raises `AssertionError` naming the
      leftover count** on timeout, so a leak is reported as an ERROR at the leaking test rather than as
      a mystery failure later; the `finally` reset then bounds the blast radius to one test. Stated
      explicitly in the plan so no reader believes a red-able assertion exists where none does.
  - **AC1's OWNING TEST — the real upper bound on `/v1/ask` latency (#3993 AC1, cycle-5 finding).**
    `test_reader_timeout_504` is named as that test and extended: it already induces the delay
    (monkeypatched 0.5 s bound + a bounded hung reader), so it also asserts the **upper bound** —
    `elapsed = time.monotonic() - t0` must be `< _ASK_TIMEOUT_S + 2.5` (bound + a stated margin for
    thread start + teardown), and `duration_ms` must be `<= (bound + margin) * 1000`. This is red-able:
    with the bound removed (or `_ASK_TIMEOUT_S` left at 60) the request takes the reader's sleep and the
    assertion fails. The header's step-mapping claim is corrected accordingly: **S1 = the constant
    (AC3), S5 = refusal + emission (AC2/AC4), S8 = the retry (#3834), and this test = AC1** — every
    AC now has an owner.
  - `test_reader_timeout_504` (`:360`) additionally pins the new contract: 504, `Retry-After == "2"`,
    body `{"error": {"code": "timeout", "retry_after": 2, "message": ASK_BUSY_MESSAGE}}` (currently
    `:383` asserts the bare body), header == body.
  - **Bound pins (all four scope-AC1 relations, not three):** `_ASK_TIMEOUT_S == 10`,
    `_ASK_TIMEOUT_S + ASK_BUSY_RETRY_AFTER_S < 15`, `_ASK_TIMEOUT_S > _ASK_EXEC_FLOOR_S > 0`,
    `ASK_RETRY_CAP_S >= ASK_BUSY_RETRY_AFTER_S`, **and `ASK_SDK_TIMEOUT_S > _ASK_TIMEOUT_S`** — the
    per-attempt transport invariant that guarantees a breach is always received as the typed
    `AskTimeout` and never as a socket timeout. The S8 comment fix does **not** substitute for the pin;
    the assertion lives in `tests/test_ask_sdk.py`, where both constants are in scope.
  - **R1-1 / R1-6 — the harm fix is TESTED, not just described.** One test drives the route through the
    bounded-timeout arm with the **dispatch** patched to raise (and, separately, with the worker
    handed a raising `_track_analytics_event`) and asserts (i) the response is still **504** — not 500
    — and (ii) the in-flight counter drains to 0. A second variant drives the **daemon-thread** branch's
    `start()` failure from a sync context (R1-6/R2-8) and asserts the same two properties. Without
    these, a mutation reverting the swallow to `raise` — or the `_logger`/`_log` NameError itself —
    survives the suite.
  - **R5-4 / R5-2 — the two AUDIBLE signals are asserted (cycle-5 findings).** `caplog` assertions that
    (a) `_track_analytics_event` logs the stripped key at WARNING for a non-allowlisted property while
    still sending only allowlisted ones, and that the never-raise contract holds for the Stripe caller's
    `plan`/`tier` props; and (b) an under-flowing `_dec()` logs the counter rather than silently
    clamping.
  - **The per-surface key split is asserted, not just commented:** one test asserts a `latency_ms`
    (MCP tool-call) event never carries `duration_ms` and vice versa.
  - **(a-i) off-the-loop, at the real route (thread identity — hang-free):** `seen = {}`;
    `monkeypatch.setattr(ha_mod, "_track_analytics_event", _recorder)` where `_recorder` is
    **non-blocking** and records `seen["emit_thread"] = threading.get_ident()` plus the event/props;
    sample the handler thread by patching **the module the late import reads** —
    `import tortoise.quota as quota_mod; real = quota_mod.run_ask_bounded;
    monkeypatch.setattr(quota_mod, "run_ask_bounded", _inline)` with
    `async def _inline(*a, **k): seen["handler_thread"] = threading.get_ident(); return await real(*a, **k)`
    (**not** `ha_mod.run_ask_bounded` — `hosted_api.py:5271-5276` imports it inside the function body,
    so there is no module-level attribute to patch). POST → 200 → drain → assert the precedent's
    non-vacuity guard first (`assert "handler_thread" in seen and "emit_thread" in seen, "the probe
    never ran"`), then `emit_thread != handler_thread`, then `ask_request` + `duration_ms >= 0`.
  - **(a-ii) non-blocking, at the helper (bounded, guaranteed release):** in an `asyncio.run(_probe())`,
    with `started`/`release` events and a recorder `started.set(); release.wait(timeout=10)`, time
    `ha_mod._emit_ask_latency_off_path(TEST_ORG_ID, 12.0, "ok")`, then
    `begin = await asyncio.to_thread(started.wait, 2.0)`; assert `begin` (the write really reached the
    recorder — non-vacuous) and the call returned while the write was still blocked; then
    `release.set()` + `_drain_ask_telemetry()` in a `finally`. An inline/awaited implementation
    blocks ≈10 s → RED, and the recorder's own timeout guarantees termination (no hang).
    - **R2-6 — the elapsed bound is MEASURED, and the margin is stated.** `t0 = time.monotonic()` is
      read immediately before `_emit_ask_latency_off_path(...)` and the assertion is
      `time.monotonic() - t0 < 0.5`: the margin is **≈20× the expected cost and ≈1/20 of the ≈10 s the
      recorder holds**, so CI jitter cannot reach it while an awaited/inline implementation cannot pass
      it. The `release.set()` **and** the drain go in a `finally` **inside** the coroutine — outside
      `asyncio.run`, loop shutdown blocks on the default executor while the recorder is still parked
      (its 10 s timeout is the backstop, not the design).
  - **R2-5 — every 200-path probe NAMES its reader injection.** A 200-path test with no provider key
    makes the reader build fail → 502 → the emission never fires → the assertion fails for the wrong
    reason. So each one states `_FakeReaderFactory(reply=…).install(monkeypatch)` (the file's own seam,
    `:81`) and `_seed_point(client)` where retrieval must hit — the (a-i) route probe included.
  - **Refusal-path emission:** short monkeypatched bound + a **bounded** hung reader (the existing
    `time.sleep(5)` precedent — never an unbounded block: `run_ask_bounded` shields the reader and the
    default executor threads are non-daemon, so an unbounded reader hangs the `client` teardown) →
    504 → assert `status="timeout"` recorded.
  - Real-writer path: with the fixture's tmp fallback, drain and assert **exactly one** `ask_request`
    row carrying `duration_ms` (proves the allowlist carries the value). N-attempts⇒N-rows is **not**
    asserted here (the AC4 composition).
  - `_emit_ask_latency_off_path` **daemon-thread branch**: unit-test it directly from a **sync** context
    (no running loop) and assert the write lands off the calling thread (the branch is dead from the
    async route, so it needs its own test or should be dropped — the plan keeps it as a defensive
    mirror).
  - Strengthen `test_in_flight_cap_429`: assert the body has **no** `retry_after`/`message`.
  - `assert not any(ch.isdigit() for ch in ASK_BUSY_MESSAGE)` (**R5-5** — the earlier
    `"10" not in … and "60" not in …` form is false as pinned: `"ten seconds"` passes it).
- `tests/test_ask_sdk.py`
  - **Extend `_FakeAskServer` opt-in only**: `self.sequence: list[tuple[int, dict, dict]] | None = None`
    (constructor default `None`); `_handle` reads it **first** —
    `if self.sequence is not None:` / `status, payload, headers = (self.sequence.pop(0) if len(self.sequence) > 1 else self.sequence[0])`
    — leaving the sticky scalar path (`:801-807`) byte-identical. The **scalar path already serves
    eternal refusal** (sticky `status` + `responses[0]`), so the only test that needs `sequence` is the
    **504→200 recovery** case; `sequence` is not needed for "exactly 3 requests on eternal refusal".
    - **R2-4 — the snippet must be valid Python and its placement pinned.** The earlier form
      (`self.sequence.pop(0) if len>1 else …`) does not compile (`len` is a builtin, not an expression),
      and its placement was ambiguous. Written as `len(self.sequence) > 1`, and the plan states that
      `requests.append(...)` stays the **first statement** of `_handle` — the branch replaces only the
      **response selection**, so a sequence-driven test cannot make the "exactly 3 requests" assertion
      pass (or fail) for the wrong reason.
  - Retry tests with `monkeypatch.setattr(retry_mod, "_sleep", recorder)` (the new seam — **never** the
    process-wide stdlib `time.sleep`, and the ask tests run a live `ThreadingHTTPServer`): advertised
    504 → retried with `floor <= wait <= 2*floor` and exactly 3 `server.requests` on eternal refusal,
    ending in the **original** `AskTimeout` carrying `retry_after`; 504→200 recovery via `sequence` →
    one result, 2 requests; bare 504 **not** retried; client-fired `AskTimeout` (`source="client"`) not
    retried; 429/502/code-less 503 not retried; the **deadline** stop re-raises the original (drive it
    by patching `retry_mod._monotonic` past the deadline — no real waiting).
- `tests/test_mcp_server_auth_modes.py` — **the MCP refusal test lives here**, not in
  `test_mcp_server.py` (which has **zero** `tortoise_ask` references and no ask harness). Reuse the
  file's own `TestAskExposureGating` harness: `tc = make_client(auth_mode="none", tool_group="ask")` →
  `self._call_tool(tc, "tortoise_ask", {"question": "q"})` → `json.loads(self._result_text(body))`.
  - **R2-1 (harm) — MAKE THE TEST HERMETIC FIRST, and state the ACTUAL mechanism.** `TestAskExposureGating`
    has **no DB fixture**, so the ask tool would open a graph on the **developer's real
    `~/.tortoise/tortoise.db`** — reading (and anchoring) a real store, and able to 502 spuriously.
    **The load-bearing mechanism is the ENV pin, not the `sdk` swap** (cycle-5 correction: three
    reviewers independently disproved the earlier claim): `auth_mode="none"` sets
    `_current_org_id = "selfhost"` (`mcp_auth.py:570`), so `_get_org_sdk()` takes the non-`None` branch and
    **directly constructs `TortoiseSDK(namespace="selfhost")`** (`mcp_auth.py:77-100`) — it never
    consults `mcp_server.sdk`. Use the file's OWN idiom (`:110-111`, `:142-143`):
    `monkeypatch.delenv("TORTOISE_DB_URI", raising=False)` (force embedded) +
    `monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "ask-mcp.db"))`, which `config.resolve_db_path()`
    honours (`sdk.py:1801-1824`) and is therefore what actually isolates the namespace branch.
    `monkeypatch.setattr(mcp_mod, "sdk", TortoiseSDK(db_path=…))` is kept **only** for the
    `org_id is None` / stdio base branch (`_get_base_sdk()`), and the plan says so rather than
    over-claiming. Plus `sdk_mod._reset_ask_reader_cache_for_tests()`.
    - **Add a non-vacuity assertion that the tmp store is the one exercised** (assert the resolved db path),
      so the hermeticity cannot silently regress. The earlier "first test in that file to touch the graph"
      claim is **dropped** — `TestAskConnectedAssemblyExposure` already touches it under docker.
    - This is a **pre-implementation fix**, not a follow-up.
  - Injection recipe (stated, not implied): `_reset_ask_reader_cache_for_tests()`;
    `monkeypatch.setattr(quota_mod, "_ASK_TIMEOUT_S", 0.5)`;
    `monkeypatch.setattr(quota_mod, "_ASK_EXEC_FLOOR_S", 0.1)`;
    `monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", _BoundedHungReader)`.
  - **R2-3 — assert the FULL error body**, not only `retry_after`/`message`: a mutation of the MCP arm's
    `CODE_TIMEOUT` would otherwise survive the test. The tool error is
    `{"error": {"code": "timeout", "retry_after": 2, "message": ASK_BUSY_MESSAGE}}` — all three keys
    asserted by value.
- `tests/test_selfhost_rest.py::TestAsk`: parity — 504 + `Retry-After` + body (`monkeypatch`
  `quota_mod._ASK_TIMEOUT_S = 0.5`, `_ASK_EXEC_FLOOR_S = 0.1`, bounded hung reader via
  `sdk_mod._default_ask_reader_factory`). **Cycle-5 corrections:**
  - **`_reset_ask_reader_cache_for_tests()` in setup AND teardown** — the selfhost ask lane caches its
    reader, and a leaked cache is exactly why `TestAskConnectedAssemblyExposure` exists
    (`test_mcp_server_auth_modes.py:462-465`); a warm cached reader would let the ask complete (200)
    instead of advertising 504, i.e. the test would pass for the wrong reason.
  - **A non-vacuity assertion that the injected hung reader was actually reached** (the factory was
    called / the reader's `complete()` was entered), so a reader-build failure cannot masquerade as the
    504 under test.
  - **Open issue #3759 is acknowledged, not ignored:** `test_ask_returns_200_shape` is documented as
    502-ing with a fake reader installed, and `selfhost_api.ask_question` maps an unexpected/reader-build
    failure to **502 `reader_unavailable`**. S11 therefore states the **lane/URI** for the targeted
    selfhost run (embedded: `TORTOISE_DB_URI=""` + a tmp `TORTOISE_DB_PATH`, as the file's own
    `_client_for_env` does) so the test cannot be read as green in an environment where it 502s for an
    unrelated reason.
- `tests/test_retrieval.py`: S4's primitive tests + the seam-form compatibility pin.

**S11 · verify + ship**
- `uv run ruff check` + `uv run mypy` on touched files.
- Targeted pytest: `tests/test_ask_api.py tests/test_ask_sdk.py tests/test_mcp_server_auth_modes.py
  tests/test_retrieval.py tests/test_eval_ingest_retry.py tests/test_ingest_v2_parallel.py
  tests/test_selfhost_rest.py tests/test_w4_why_enrichment.py` **plus the reflex suites AC5 requires
  green**: `tests/test_hosted_volunteer_context.py tests/test_selfhost_volunteer_context.py
  tests/test_volunteer_contract.py`.
  - **The selfhost leg runs on the EMBEDDED lane explicitly** (`TORTOISE_DB_URI=""` + a tmp
    `TORTOISE_DB_PATH`, the file's own `_client_for_env`), so a 502 from a reader-build failure in a
    URI-configured environment cannot be read as the 504 under test (see #3759).
  - **R1-4:** `tests/test_w4_why_enrichment.py` is added because `:576` calls
    `asyncio.run(ms.tortoise_ask(...))` — an **MCP-surface consumer of the changed function** that the
    earlier sweep missed by scoping itself to `/v1/ask`. It exercises the same S7 arm, so it is in the
    targeted set rather than merely noted.
  (`test_ingest_v2_parallel.py` is in the list because it shims `retry_mod.time` — the S4 seam form is
  what keeps it green; `test_mcp_server_auth_modes.py` because that is where the MCP refusal test lands.
  A repo-wide sweep for `/v1/ask` finds exactly `test_ask_api.py`, `test_ask_gating.py`,
  `test_ask_sdk.py`, `test_selfhost_rest.py`, `test_mcp_server_auth_modes.py`; `test_ask_gating.py`
  asserts a **401** before the handler body → unaffected; `_ALLOWED_ANALYTICS_PROPS` appears only as a
  **subset** assertion in `test_capture_cost_measurement.py` → the added key cannot break it.)
- The two `rg` sweeps from AC6/AC7, output recorded in the PR.
  - **R2-7 — AC6/AC7 have NO red-able test, and the plan says so.** They are `rg` sweeps whose output is
    recorded in the PR body; they are **not** test coverage and must not be read as such. State that
    explicitly in the PR so a future reader cannot mistake a recorded grep for an assertion.
- **Post both `OVERRIDES:` lines as comments on #3834 AND #1987** (`gh issue comment`); record the two
  comment URLs in the PR body (verify the comment exists, not that the send returned OK).
- `commit-workflow`: PR (body = justification + the verbatim decisive argument + precision note + the two
  `OVERRIDES:` lines) → code-review gate → `bash scripts/admin-merge.sh <PR>`. Record the review AFTER
  the last PR-body edit; post-merge results in a COMMENT.

## Risks / watch-items
- `_ASK_TIMEOUT_S` is monkeypatched widely — keep the name.
- `asyncio.shield` in `run_ask_bounded` ⇒ a breached reader keeps running ⇒ its `record_ask_usage`
  lands after the 504 and a retry adds another ⇒ N requests ⇒ N usage records. **Stated as a
  consequence, never asserted as a count** (it races the shielded future).
- `ASK_BUSY_MESSAGE` carries no literal number (single source of truth = `_ASK_TIMEOUT_S`); pinned.
- **Emission must be off the loop AND non-blocking** — guarded by **two** tests (thread identity at the
  route; the bounded-release probe at the helper). Thread identity alone cannot see an awaited
  `to_thread`.
- Non-finite/negative `Retry-After` must never reach `_sleep` (S4 + S8 finite filter).
- **R2-1's over-claim is corrected** and the hermetic recipe names the env pin as load-bearing.
- **AC1 (the real latency upper bound) has an owning, red-able test.**
- **The refusal cannot become a 500** — tested, with the `_logger`/`_log` binding pinned (a `NameError`
  inside the guard would have produced exactly the 500 the guard exists to prevent).
- **One dispatch form**, `run_in_executor` + `_retain_feed_task` (synchronous submission); the
  `create_task`/`to_thread` and bare-prose variants are deleted.
- **The daemon-thread branch does not retain** (a `Thread` has no `add_done_callback`) and carries its
  **own** keyed decrement guard.
- `_ASK_TELEMETRY_LOCK` is a **`threading.Lock`** (the decrement runs in the worker thread).
- **Jitter is proven** (two recorded sleeps, not all equal) and the **deadline-below-floor** precedence
  is stated and pinned.
- **`ASK_SDK_TIMEOUT_S > _ASK_TIMEOUT_S`** is asserted (the fourth scope-AC1 relation).
- **Pre-existing on-loop analytics writers** (`hosted_api.py:18692`, `:19753`, `:19961`, `:20037`,
  `:23469` — synchronous `httpx.Client` POSTs inside `async def` handlers, the #2988/#3498 class) are
  **out of this unit's scope**, named as such, and **filed as their own issue** — the recurrence guard
  does not silently absorb them.
- `retry.py` seams must stay **call-time indirections** (an import-time alias breaks
  `tests/test_ingest_v2_parallel.py`'s `retry_mod.time` shim); pinned by a test.
- Any hung reader used in a test must be **bounded** (shielded future + non-daemon executor threads ⇒
  an unbounded reader hangs the `client` teardown).

## Cycle-4 changelog (cycle-3 plan-review findings → disposition)
| # | Finding | Disposition |
|---|---|---|
| 1 (P1, R1+R2) | `monkeypatch.setattr(ha_mod, "run_ask_bounded", …)` patches a **non-existent** module attribute (`hosted_api.py:5271-5276` imports it inside `ask_question`) → `AttributeError` with `raising=True`, silent no-op (vacuous assertion) with `raising=False` | **Accepted — plan fixed; scope AC4(a) reworded to name the property, not the seam.** Seam is `monkeypatch.setattr(quota_mod, "run_ask_bounded", _inline)`; added the precedent's "the probe actually ran" guard so it can never go vacuous. |
| 2 (P2, R2) | `_sleep = time.sleep` as an **import-time alias** silently defeats `tests/test_ingest_v2_parallel.py::_silence_retry_sleep` (which patches `retry_mod.time`), causing real 1–2 s sleeps in a file absent from S11 | **Accepted.** Seams defined as call-time indirection **functions**; `test_ingest_v2_parallel.py` added to S11; a test pins the indirection form. |
| 3 (P2, R2) | Thread identity proves *off the loop*, not *non-blocking* (an awaited `to_thread` passes) — so the Risks claim it is "the guard" was false | **Accepted.** Added the (a-ii) bounded-release non-blocking probe; Risks and AC4 corrected. |
| 4 (P2, R1+R2) | Residual double-decrement if the dispatch-failure decrement is a blanket handler (worker's `finally` also decrements) → negative counter → drain can never reach `== 0` | **Accepted.** Decrement keyed on future creation (`if fut is None`); drain treats `<= 0` as drained. |
| 5 (P2, R1) | AC4(c) "one row per hosted request" over-broad — validation/quota/reader errors legitimately emit nothing | **Accepted — scope amended** to "each hosted request that terminates in success or bounded timeout". |
| 6 (P2, R1+R2) | The refusal-path test must pin a **bounded** hung reader (unbounded ⇒ non-daemon executor thread hangs `client` teardown) | **Accepted.** Stated explicitly; the `time.sleep(5)` precedent. |
| 7 (P2, R2) | `delenv(..., raising=False)` unspecified; the two-name list is not what makes the fixture hermetic (`SUPABASE_SERVICE_ROLE_KEY` is honoured; **`SUPABASE_URL`** is load-bearing) | **Accepted.** All three `raising=False`, and the plan says why the URL deletion is the load-bearing one. |
| 8 (P2, R2) | A raise in an **autouse** teardown cascades to every later test and reports as pytest **ERROR**; decrement location unpinned | **Accepted.** `try/finally` with `_reset_ask_telemetry_for_tests()`; `assert inflight == 0` at setup; decrement location pinned to the worker's own `finally`; drain needs no live loop. |
| 9 (P2, R2) | The MCP timeout test was assigned to a file with no ask harness and no stated breach mechanism | **Accepted.** Moved to `tests/test_mcp_server_auth_modes.py::TestAskExposureGating` (its own `_call_tool`, `tool_group="ask"`); injection recipe stated; file added to S11. |
| 10 (P2, R1) | S8's `deadline=time.monotonic() + …` → `NameError` (`sdk.py:21` binds `monotonic as _monotonic`; no module-level `import time`) | **Accepted.** `deadline=_monotonic() + ASK_RETRY_DEADLINE_S`. |
| 11 (P3, R2) | `_FakeAskServer.sequence` under-justified — the sticky scalar path already serves eternal refusal; must not alter the default path | **Accepted.** Named the sole consumer (504→200 recovery); guard at the top of `_handle`; scalar path byte-identical; constructor default `None`. |

**Research backing for this cycle's fixes (the half-budget rule is in force — Max Cycles 3, so half is spent):**

| Finding | Backing |
|---|---|
| 1 (seam does not exist) | **`internal-only, no external source`** — verified by direct read of `hosted_api.py:5271-5276` (the late `from tortoise.quota import … run_ask_bounded`) against the working precedent in `tests/test_ask_api.py:363`. |
| 2 (seam form), 3 (non-blocking), 4 (double-decrement), 6 (bounded reader), 11 (sequence) | **`internal-only, no external source`** — in-repo conventions (the `retry_mod.time` shim in `tests/test_ingest_v2_parallel.py:116-129`; the `mcp_server._pending_telemetry` retained-future pattern; the `time.sleep(5)` bounded-reader precedent in `tests/test_ask_api.py:369`; the sticky `_FakeAskServer._handle` at `tests/test_ask_sdk.py:801-807`). |
| 7 (fixture hermeticity), 8 (teardown cascade), 9 (MCP test home), 10 (`_monotonic`) | **`internal-only, no external source`** — verified against `hosted_api.py:19670` (`if url and key:`), `supabase_control.py:73` (`SUPABASE_SERVICE_ROLE_KEY` precedence), pytest's fixture-teardown ordering, `test_mcp_server_auth_modes.py::TestAskExposureGating`'s existing harness, and `sdk.py:21` (`monotonic as _monotonic`). |
| S4's jitter/floor design (carried from the scope) | External, already cited in the scope: the AWS builders' library jitter guidance and **RFC 9110 §10.2.3** (`Retry-After` as a *minimum* wait) — the reason jitter is **additive above** the advertised floor rather than Full Jitter around it. |

**Reviewer #5 (Duplication & Architecture) is DISPATCHED this cycle** — the plan introduces a **new
shared-state owner** (`_ASK_TELEMETRY_INFLIGHT` behind a `threading.Lock`, owned by a new helper;
retained-dispatcher state lives in the module's existing registry reached through `_retain_feed_task`)
and a **new write path** (off-path analytics emission), and **extends an existing vocabulary**
(`Retry-After` onto the 504 refusal). Its verdict is advisory and does not affect convergence.

## Cycle-4 review results — UNRESOLVED (escalation payload)

Cycle 4 was the orchestrator's sanctioned one-shot deep-fix re-run after the cycle-3 cap. The four
cycle-3 fixes it was meant to verify **were verified sound** by both reviewers (the `quota_mod.
run_ask_bounded` seam; the call-time `_sleep`/`_monotonic` indirection preserving
`test_ingest_v2_parallel.py`; the `fut is None` decrement keying; the fixture/teardown ordering). Both
proportional reviewers returned **no P0/P1**. The loop still may not close: the cap is spent and
issues remain, so this plan is **not clean** and implementation must not start on it as-is.

### Proportional residue — reviewer #1 (Structural & Efficiency): 2 P2, 4 P3
| # | Sev | Step | Finding | Fix |
|---|---|---|---|---|
| R1-1 | **P2 (harm)** | S5 | The dispatch-failure `raise` in `_emit_ask_latency_off_path` propagates to unguarded call sites → an executor dispatch failure replaces the **504 with a 500** (or a 200 with a 500). Contradicts the precedent the plan cites ("Never raises, never blocks", `mcp_server.py`'s `_emit_mcp_tool_call_telemetry`). | Swallow + log (`except Exception: _log.debug(..., exc_info=True)`), keep `if fut is None: _dec()` inside; add a test where `run_in_executor` raises. |
| R1-2 | **P2 (race)** | S5 | The **increment** is pinned under `_ASK_TELEMETRY_LOCK` but the **decrement** is not → lost update → counter reads `-1` while a write is still in flight → `<= 0` drained rule returns early → the JSONL row may not exist when the test reads it (defeats the fixture's whole purpose). | `_inc()`/`_dec()` helpers both taking the lock; keep `<= 0` as defence, not as the race fix. |
| R1-3 | P3 | S4 tests | "a string floor → 0.0" is wrong for **numeric** strings (`float("3") == 3.0`). | Name the input (`"not-a-number"`); separately pin coercion of `"3"` if intended. |
| R1-4 | P3 | S11 | `tests/test_w4_why_enrichment.py:576` calls `asyncio.run(ms.tortoise_ask(...))` — an MCP-surface consumer of the changed function, missing because the sweep was scoped to `/v1/ask`. | Add to S11 or state the exclusion reason. |
| R1-5 | P3 | S8 | "finite and ≥ 0" needs `math.isfinite`; `sdk.py:14-22` has **no `import math`**. | Name the import, or use `0 <= ra < inf` comparisons. |
| R1-6 | P3 | S5 | The **daemon-thread** branch's `start()` failure is unguarded → counter leaks `+1` → drain raises. | Same keyed guard as the executor path. |

### Proportional residue — reviewer #2 (Integration & Testability): 2 P2, 6 P3
| # | Sev | Step/AC | Finding | Fix |
|---|---|---|---|---|
| R2-1 | **P2 (harm)** | S10 MCP test | The MCP refusal test is **non-hermetic**: `TestAskExposureGating` has no DB fixture, so `_get_org_sdk()` resolves `~/.tortoise/tortoise.db` — the test reads (and anchors) the **developer's real graph** and can 502 spuriously. It is the first test in that file to touch the graph. | Pin an isolated store (`monkeypatch.setenv TORTOISE_DB_PATH` to `tmp_path`, or a fresh UUID URI) + reset the selfhost anchor/reader cache, as `test_selfhost_rest.py:15-22` does. |
| R2-2 | **P2** | S10 fixture | `assert inflight == 0` **cannot go red** as specified: placed after `_reset_ask_telemetry_for_tests()` it is tautological; placed before, it trips on benign races. | Pin the order — assert before the reset and reset only in `finally` — or drop it and document teardown-drain as the guard. |
| R2-3 | P3 | S10 MCP test | Does not pin `code == "timeout"` (only `retry_after`/`message`) → a mutation of the MCP arm's `CODE_TIMEOUT` would survive. | Assert the full error body. |
| R2-4 | P3 | S10 `_FakeAskServer` | The `_handle` snippet is **not valid Python** (`pop(0) if len>1 else …` — `len` is a builtin) and its placement is ambiguous; if the branch precedes `requests.append`, the "exactly 3 requests" assertion fails for the wrong reason. | Write `len(self.sequence) > 1`; state the append stays the first statement and the branch replaces only response selection. |
| R2-5 | P3 | S10 (a-i)/(b)/(c) | The 200-path tests don't name the **fake-reader injection**; with no provider key the reader build fails → 502 → the emission never fires → assertions fail for the wrong reason. | Name `_FakeReaderFactory.install` / `_seed_point`. |
| R2-6 | P3 | S10 (a-ii) | `elapsed < 0.5` is an unspecified real-time bound (CI flake), and the plan doesn't say the `release.set()` + drain `finally` must be **inside** the coroutine (outside → loop shutdown blocks on the default executor). | Measure with `time.monotonic()`, state the margin, pin the `finally` inside `asyncio.run(_probe())`. |
| R2-7 | P3 | AC6/AC7 | AC6/AC7 have **no red-able test** (they are `rg` sweeps recorded in the PR). | State that explicitly rather than implying test coverage. |
| R2-8 | P3 | S5 | Daemon-thread dispatch-failure decrement unguarded (same as R1-6). | As R1-6. |

### Reviewer #5 (Duplication & Architecture) — ADVISORY, 4 P1 + 1 P2, verdicts recorded

**Owner direction (#3834, binding, 2026-09-18):** the advisory set is dispositioned by the owner, not
by the loop — `R5-1`, `R5-2`, `R5-4`, `R5-5` are **folded into this cycle**; `R5-3` is **NOT folded** and
is filed as its own issue. See `## Cycle-5 changelog` for each disposition.
Dispatched this cycle (trigger: new shared-state owner + new write path + extended wire vocabulary).
Per the skill's disposition table these do **not** enter the fix cycle and do **not** affect
convergence; they are recorded here with their verdicts for the human. **Tortoise source was
UNAVAILABLE** (`mcp_load tortoise` → `-32001 Unauthorized`) — repo-search + git-history only;
registry-dependent novelty claims are low confidence.

| # | Sev | Location | Verdict | Finding (condensed) |
|---|---|---|---|---|
| R5-1 | P1 | S5 dispatch | `unify-contract-keep-drivers` | The off-path **dispatcher** is a 4th/5th copy. `hosted_api._retain_feed_task` (`:4341`, retained fire-and-forget + done-callback pruning, 7 call sites, *in the module being edited*) already does this job; the plan copied `mcp_server._pending_telemetry` from another module instead. Recurrence guard proposed: one contract test asserting every dispatcher hands the write to a non-loop thread. |
| R5-2 | P1 | S5 counter/drain | `unify-contract-keep-drivers` | The in-flight counter duplicates an existing pattern but **drops its audible signal**: `_capture_slot_decrement` (`hosted_api.py:240-252`) *logs* an under-flow leak; the plan's `<= 0` drained rule plus the fixture reset makes a leak invisible. Fix: log the leak; justify the sync drain vs `_flush_mcp_telemetry`. |
| R5-3 | P1 | S5/S6/S7 refusal vocab | `unify-contract-keep-drivers` | The `Retry-After`/`retry_after`/`message` contract exists only as **prose** (`docs/product/answer-surface.md:211`) — no code declaration, and **no test asserts the 429 and 504 shapes agree or that hosted == selfhost == MCP**. The two existing "mirrors" already disagree (hosted 429-conditioned; selfhost has no 429 branch). Fix: declare it once in `schemas.py` (e.g. `build_ask_error_body`) + one agreement test. |
| R5-4 | P1 | S5 allowlist / S11 | `unify` | **D6 recurrence**: the same defect class was "fixed" twice by #3359 (commits `9003debbf`, `ce1bf9716`) by extending the hardcoded allowlist; the plan repairs the third instance and reads a **per-producer subset check** as a guarantee. Fix: make `_track_analytics_event` **log the key it strips** — one line converting the class from silent to audible. |
| R5-5 | P2 | S2 / Risks | `unify` (A6) | "`ASK_BUSY_MESSAGE` carries no literal number … pinned" is **false as pinned** — the check covers two digit-strings ("10"/"60"), so "ten seconds"/"1 minute" pass. Fix: `assert not any(ch.isdigit() for ch in ASK_BUSY_MESSAGE)`. |

(+3 suppressed under #5's 5-finding cap: two retry layers compose without a stated interaction
(provider failover × the new outer retry); `duration_ms` has two definitions (SDK local lane vs hosted
request) with no per-surface consistency assertion; `latency_ms` vs `duration_ms` separation recorded
in the scope but not at the allowlist entry. **Disposition (owner, 2026-09-18):** the retry-layer
interaction is recorded in Risks below; the two `duration_ms` definitions are named in the S5 allowlist
entry as a **per-surface** disclosure (hosted request wall-clock) and are not asserted equal — the
SDK-local lane does not write the row; `latency_ms` vs `duration_ms` is split by **surface** (MCP
tool-call vs hosted request), stated at the allowlist entry so the two cannot be aggregated.)

## Cycle-5 review results — the owner-authorized FINAL cycle

Cycle 4 was the orchestrator's one-shot deep-fix re-run after the cycle-3 cap: the four cycle-3 fixes
it verified were found **sound** by both reviewers (the `quota_mod.run_ask_bounded` seam; the call-time
`_sleep`/`_monotonic` indirection preserving `test_ingest_v2_parallel.py`; the `fut is None` decrement
keying; the fixture/teardown ordering), and **no P0/P1** was returned — but 14 proportional residues
remained, so the loop could not close clean.

**Cycle 5 folds all 14 proportional residues plus the four owner-directed Reviewer-#5 items.** It is
the **last authorized cycle**; the exit is **capped**, not clean, and is disclosed as such in the PR.

| # | Sev | Where | Disposition |
|---|---|---|---|
| **R1-1** | P2 (harm) | S5 dispatch | **Accepted.** Both dispatch branches swallow + log; the keyed `if fut is None` decrement stays; a test where the dispatch raises asserts the route still returns its pinned status. |
| **R1-2** | P2 (race) | S5 counter | **Accepted.** `_inc()`/`_dec()` **both** take `_ASK_TELEMETRY_LOCK`; `<= 0` documented as defence, not the race fix. |
| **R1-3** | P3 | S4 tests | **Accepted.** The floor input is named `"not-a-number"`; numeric-string coercion (`"3"` → `3.0`) pinned separately. |
| **R1-4** | P3 | S11 | **Accepted.** `tests/test_w4_why_enrichment.py` added to the targeted set (MCP-surface consumer). |
| **R1-5** | P3 | S4/S8 | **Accepted.** Written `0 <= ra < inf` — no `import math` (`sdk.py` has none; `math.isfinite` would `NameError`). |
| **R1-6** | P3 | S5 dispatch | **Accepted.** The daemon-thread `start()` failure gets the same keyed guard (also **R2-8**). |
| **R2-1** | P2 (harm) | S10 MCP test | **Accepted — pre-implementation.** The test pins an isolated store via the file's own idiom (`delenv TORTOISE_DB_URI` + `setenv TORTOISE_DB_PATH` + the `mcp_server.sdk` swap point + reader-cache reset) before it may touch the graph. |
| **R2-2** | P2 | S10 fixture | **Accepted — by DROPPING the assertion.** The setup `assert inflight == 0` cannot go red either way; the teardown drain (raising `AssertionError` naming the leftover count) is stated as the guard. |
| **R2-3** | P3 | S10 MCP test | **Accepted.** The full error body (`code` + `retry_after` + `message`) is asserted. |
| **R2-4** | P3 | S10 `_FakeAskServer` | **Accepted.** `len(self.sequence) > 1` (valid Python); `requests.append` pinned as the first statement so only response selection branches. |
| **R2-5** | P3 | S10 200-path tests | **Accepted.** Each 200-path probe names `_FakeReaderFactory(...).install(monkeypatch)` / `_seed_point`. |
| **R2-6** | P3 | S10 (a-ii) | **Accepted.** `time.monotonic()` delta < 0.5 with the margin stated; `release.set()` + drain inside the coroutine's `finally`. |
| **R2-7** | P3 | AC6/AC7 | **Accepted.** Stated explicitly that the two `rg` sweeps are recorded evidence, **not** test coverage. |
| **R2-8** | P3 | S5 dispatch | **Accepted.** Same as R1-6. |
| **R5-1** | P1 adv | S5 dispatch | **Accepted (owner-directed).** The dispatcher is `hosted_api._retain_feed_task` (the module's own idiom, 7 call sites) via `run_in_executor` + a unique per-dispatch key; `_ASK_TELEMETRY_FUTURES` is **deleted**; the helper's annotation widens `Task` → `Future`; the recurrence guard is scoped to the three known off-loop dispatchers and names the five pre-existing on-loop sites as out of scope. |
| **R5-2** | P1 adv | S5 counter/drain | **Accepted (owner-directed).** `_dec()` logs the under-flow (mirroring `_capture_slot_decrement`); the sync drain vs `_flush_mcp_telemetry` is **justified** (no loop alive in teardown; the counter reaching 0 is the guarantee — a loop-bound async flush cannot supply it). |
| **R5-3** | P1 adv | S5/S6/S7 vocab | **NOT FOLDED — filed as its own issue.** The `Retry-After`/`retry_after`/`message` contract exists only as prose (`docs/product/answer-surface.md:211`); declaring it once in `schemas.py` + an agreement test spans two transports **and** `schemas.py`, and the plan-pinned refusal already satisfies the owner's legibility requirement. Out of this unit's scope; the issue carries the full finding. |
| **R5-4** | P1 adv | S5 allowlist | **Accepted.** `_track_analytics_event` logs the key it strips (one line) — the third instance of the silent-strip class is made audible instead of being repaired by extending the allowlist again. |
| **R5-5** | P2 adv | S2 | **Accepted.** `assert not any(ch.isdigit() for ch in ASK_BUSY_MESSAGE)` — the `"10"/"60"` form is false as pinned. |

## Cycle-5 review results — findings and disposition (the AUTHORIZED final cycle)

Cycle 5 was dispatched fresh (reviewers #1 Structural&Efficiency, #2 Integration, #5 Duplication &
Architecture — #5 triggered by the new shared-state owner + new write path + extended vocabulary).
**Reviewer #5 reported DEGRADED** (Tortoise graph unreachable, `-32001 Unauthorized`); its
registry-dependent novelty claims carry **low confidence** and the review rests on repo-search +
git-history evidence, which is what the findings below depend on.

**The cycle was NOT clean.** It returned **6 P1 + 12 P2** proportional findings plus **6 advisory
findings (F1–F6)**. They are folded below. Because the authorized cycle is spent, the loop **cannot
re-verify these folds with a further fresh cycle** — so this exit is **CAPPED**, and that is disclosed
in the PR body. No re-review was claimed and none is implied.

### The findings that mattered (all folded)
| # | Sev | Where | Finding | Disposition |
|---|---|---|---|---|
| C1 | **P1** | S5, S10 | `_log` is **not bound** in `hosted_api.py` (it is `_logger`, `:106`; `_log` is `mcp_server.py:39`). The R5-4 strip log would raise **`NameError: '_log' is not defined`** inside a function documented *"Never raises"* — and its strip branch fires on keys that exist today (`plan`/`tier` from the Stripe caller at `:23469`), so it escapes the writer, reddens the webhook 500 path, and — the event marker already claimed — **drops the billing notification permanently**. | **Accepted.** All symbols repointed to `_logger`; the strip log wrapped so the never-raise contract holds by construction; `caplog` tests added. |
| C2 | **P1** | S5 | The dispatcher was pinned **three contradictory ways** (`create_task(to_thread(...))` vs `run_in_executor` vs prose); `_retain_feed_task` is typed `asyncio.Task` while `run_in_executor` returns a `Future`; the `create_task` form only *schedules*, so a loop that stops ticking never runs the write and never decrements the counter — the very leak the counter fixes prevent. | **Accepted — one form pinned.** `run_in_executor` (synchronous submission) + `_retain_feed_task`, annotation widened `Task`→`Future` (a Task IS a Future — strictly additive), rationale stated. |
| C3 | **P1** | S5 | The **daemon-thread** branch cannot go through a `Task`-typed helper (`Thread` has no `add_done_callback`), and "the same keyed guard" does not port: `Thread(...)` exists **before** `start()`, so `if fut is None` never fires → `+1` leak. | **Accepted.** Own keyed guard pinned (`started` flag); retention explicitly **not** claimed for the daemon branch (`threading._active` holds it). |
| C4 | **P1** | S10 | **#3993 AC1 had no owning test.** The plan left `duration_ms >= 0` untouched at both cited sites and even re-asserted it in the new (a-i) probe — the exact anti-pattern AC1 exists to kill. | **Accepted.** AC1's owning test named (the extended `test_reader_timeout_504`) with a real upper bound + `duration_ms` consistency, red-able by removing the bound; the header's step→AC mapping corrected. |
| C5 | **P1** | S10 | The **R1-1 harm fix was untested** — the cycle-5 disposition promised a dispatch-failure test that S10 never specified. | **Accepted.** Both variants added (executor dispatch failure; daemon `start()` failure) asserting 504-not-500 **and** a drained counter. |
| C6 | **P1** | S10 | The selfhost 504 test ignored **open #3759** (fake reader → 502) and omitted `_reset_ask_reader_cache_for_tests()`, so a warm cache would make it pass for the wrong reason. | **Accepted.** Reader-cache reset in setup+teardown; non-vacuity assertion that the hung reader was reached; the embedded lane/URI stated in S11. |
| C7 | P2 | S5 | `_ASK_TELEMETRY_LOCK`'s **type was unspecified**, and the decrement runs in a worker thread → an `asyncio.Lock` is unusable. | **Accepted.** `threading.Lock()` pinned, mirroring `_CAPTURE_IN_FLIGHT_LOCK`. |
| C8 | P2 | S10 | Jitter was **not actually tested** — `floor <= wait <= 2*floor` passes for a constant `wait == floor`. | **Accepted.** Two recorded sleeps, in-range **and** not all equal. |
| C9 | P2 | S4 | The **deadline clamp can push a sleep below the advertised floor**; the plan never stated the precedence while the docstring called the floor a minimum. | **Accepted.** Precedence stated (deadline wins) + an overlap-case unit test. |
| C10 | P2 | S10 | `ASK_SDK_TIMEOUT_S > _ASK_TIMEOUT_S` (the 4th scope-AC1 relation) was a comment, not a pin. | **Accepted.** Asserted in `test_ask_sdk.py`. |
| C11 | P2 | S10 | R2-1's **stated mechanism was factually wrong**: `auth_mode="none"` sets `_current_org_id="selfhost"`, so `_get_org_sdk()` builds `TortoiseSDK(namespace="selfhost")` directly and **never** consults `mcp_server.sdk`; the env pin is what isolates it. The "first test to touch the graph" claim was also false. | **Accepted.** Corrected: env pin load-bearing, `sdk` swap scoped to the base/stdio branch, over-claim dropped, resolved-path assertion added. |
| C12 | P2 | S5 | R5-4/R5-2's two **audible signals were untested**. | **Accepted.** `caplog` assertions for both. |
| C13 | P2 | S5 | The per-surface `duration_ms` / `latency_ms` disclosure the disposition claimed was "named in S5" **was not in S5**. | **Accepted.** Declared at the allowlist entry (with its own comment), plus a test that the two keys never co-occur. |
| C14 | P2 | S8/Risks | The retry's **per-org in-flight-cap** interaction was unstated: a shielded reader holds the slot, so a retry can surface `429 in_flight_limit` instead of the advertised `timeout`. | **Accepted.** Stated in Risks + the `_post_ask` docstring, as a consequence (never a count). |
| C15 | P2 | Header | The Reviewer-#5 preamble still described `_ASK_TELEMETRY_FUTURES`, which S5 deletes. | **Accepted.** Corrected — the plan now has one description of the shared state. |
| **F1–F5** | **P1 adv** | S5/S10 | Reviewer #5's advisory set: the three-form dispatch contradiction (F1); the `NameError`+inaudible-level+nonexistent-failure-path in R5-4 (F2); the R5-3 exclusion **reason** being only partly sound (F3); the recurrence guard's predicate being blind to the awaited-`to_thread` case and its family being 8 sites, not 3 (F4); `_SIGNUP_FEED_TASKS` being a signup-named, unlocked home for a second family, and the daemon branch not being retainable (F5). | **All folded** except the parts the owner ruled out: F1/F2/F5 folded (C2/C3/C1/C12/C13); **F3 accepted as a correction to the RECORDED REASON** (the 429 half is genuinely beyond this unit — R5-3 stays filed, see the filed issue); **F4 folded as an HONEST SCOPE** — the guard is asserted for the three known off-loop dispatchers, thread identity is documented as an off-loop (not non-blocking) proof, and the **five pre-existing on-loop writer sites are filed as their own issue** rather than absorbed. |
| F6 | P2 adv | S5 | The `duration_ms`/`latency_ms` split was declared with no record where the code can see it. | **Accepted** (= C13). |

**Cycle-5 review honesty notes:** Reviewer #5's `NO ISSUES FOUND — DEGRADED (Tortoise unavailable)` token
is **not** a pass and is not reported as one. The `ASK_BUSY_MESSAGE` digit-form pin admits a
*spelled-out* number ("ten seconds") while catching `"1 minute"` — recorded so the claim is not read as
"no number in any form". Two `retry.py` floors and one deadline clamp now have explicitly stated
precedence rather than an implied one.

<!-- plan-review: cycles=5, status=capped, version=2.3.0 -->
<!-- plan-review-exit: capped (owner-authorized final cycle). Cycle 5 returned 6 P1 + 12 P2 proportional
     findings plus 6 advisory (F1–F6); all are folded, but the authorized cycle is spent so the folds are
     NOT re-reviewed by a fresh cycle. Escalation exit — never reported as clean. Reviewer #5: DEGRADED
     (Tortoise unreachable, -32001). R5-3 filed separately. Pre-existing on-loop writer sites filed. -->

---

## Implementation notes (post-implementation, honest record)

Written after S10/S11. Records what the folded tests actually caught, every deviation from this
plan's letter, and the two pre-existing bugs the work surfaced.

### 1. Two implementation bugs the folded tests caught (both were plan-specified, both were real)

1. **`inf` is not a builtin → `NameError` on every advertised 504.** The R1-5 wording here (two
   places, corrected above) asserted the finiteness filter could be written `0 <= ra < inf` "because
   `inf` is a builtin". It is not: `inf` lives in `math`, which `sdk.py` does not import. The result
   was a `NameError` raised inside the SDK's 504 arm — i.e. **exactly on the refusal this ticket
   ships**, and only when a `Retry-After` was present (a bare 504 short-circuits before the compare).
   Invisible to the in-process route tests; caught the first time a test drove the SDK's HTTP client
   over a 504-with-advertisement (`test_ask_retry_exhaustion_reraises_the_original_unwrapped`).
   Fixed with a module constant `_ASK_RETRY_AFTER_CEILING_S = float("inf")`.
2. **The daemon-thread branch did not swallow.** `_emit_ask_latency_off_path`'s sync branch had a
   bare `try/finally` (decrement in `finally`) with **no `except`** — and `finally` does not swallow.
   A `Thread.start()` failure therefore propagated out of a function whose whole contract is *never
   raises*, which on the refusal arm turns the pinned 504 into a **500** (the R1-1 harm, reached by a
   second route) and on the success arm turns a 200 into a 500. Caught by the plan-specified
   R1-6/R2-8 test (`test_ask_emission_daemon_thread_start_failure`). Fixed by adding
   `except BaseException` + the warning, mirroring the loop branch.

Both are recorded because they are the argument *for* the pre-implementation test requirement: each
was a small, plausible-looking omission in code this plan wrote, and neither was visible to the
route-level tests that existed.

### 2. Deviations from this plan's letter (each with its reason)

| Plan said | Built | Why |
|---|---|---|
| MCP test: hermetic via `delenv TORTOISE_DB_URI` + `setenv TORTOISE_DB_PATH(tmp)` + a hung reader, plus "assert the resolved db path" | `_get_org_sdk` is **stubbed** (`TestAskBoundBreachRefusal._stub_bound`), so no graph is opened at all; the bound is breached by patching `run_ask_bounded` to raise | Strictly stronger than isolating the store: no DB, no reader, no LLM. With the SDK stubbed a hung reader is unreachable, so the plan's db-path non-vacuity check is N/A (there is no store to resolve). The full error body (R2-3) is asserted by value. |
| (a-i) off-loop probe on the **200** path with `_FakeReaderFactory` + `_seed_point` | asserted on the **refusal** arm (which emits its own row) | Identical property under test; the 200 path in that file is the fragile fake-reader shape (see §3). The non-vacuity guard is kept, checked FIRST. |
| `_FakeAskServer.sequence: list \| None = None` | `sequence: list = []` | Identical behaviour (the branch tests truthiness). `requests.append(...)` remains the first statement of `_handle`, as required. |
| SDK deadline stop driven by patching `retry_mod._monotonic` | driven by `ASK_RETRY_DEADLINE_S = 0` | Both avoid real waiting; the knob version additionally pins the constant. |
| AC1 margin expressed as `_ASK_TIMEOUT_S + 2.5` | literal `0.5 + 2.5` | `test_reader_timeout_504` monkeypatches the constant to 0.5; reading it back would test the monkeypatch. |
| "One contract test asserts, for the **three** off-loop dispatchers this unit knows about (`_emit_ask_latency_off_path`, `mcp_server._emit_mcp_tool_call_telemetry`, the capture_cost `to_thread` site), that the write body runs on a thread **≠** the calling loop thread" (§ recurrence guard; repeated in the cycle-5 changelog) | the thread-identity assertion for `_emit_ask_latency_off_path` is this unit's (`test_ask_emission_is_handed_off_the_event_loop`, `…_non_blocking`, `…_daemon_thread_branch`) | **Not built — recorded as an open deviation, not a reached deliverable.** R5-1/F4's recurrence guard is therefore **partial**: the **MCP** dispatcher (`_emit_mcp_tool_call_telemetry`) is the one left without a thread-identity assertion — it is covered only by an `inspect.getsource` proxy (`test_ask_api.py::test_ask_per_surface_keys_never_combine`), which pins identifiers rather than the property. The capture-cost dispatcher is **covered**, by a pre-existing test this unit did not add (`tests/test_capture_cost_measurement.py::test_emission_write_is_handed_off_the_event_loop`, asserting `emit_thread != loop_thread`). Carried as a follow-up. |

### 3. Pre-existing bugs this work surfaced (filed, not fixed here)

- **#4017 — ambient `TORTOISE_API_URL` delegates the ask suite to production.** The fleet shell
  exports `TORTOISE_API_URL=https://api.premiselabs.co`; `sdk.ask()` takes the REMOTE branch whenever
  it is set, so three test files POSTed real requests at the production API and never resolved their
  fake-reader seam. **This inverted a premise of this plan:** base `tests/test_ask_api.py` is
  **29/29 green** with the var cleared, and 20/29 with it set — the "9 pre-existing environmental
  failures" this work originally carried forward were entirely the leak. `test_ask_sdk.py` 36/36 vs
  17/36; `test_selfhost_rest.py::TestAsk` 3/3 vs 1 failing. Worked around before the ask path is
  exercised in **five** places — `test_ask_api.py::_clean_ask_state` and
  `test_ask_sdk.py::_clean_ask_state` (every test in each file),
  `test_selfhost_rest.py::_client_for_env`,
  `test_mcp_server_auth_modes.py::TestAskBoundBreachRefusal._stub_bound`, and
  `test_mcp_server_auth_modes.py::TestAskConnectedAssemblyExposure._run`; the suite-wide fix
  is in the issue (any other test that drives `sdk.ask()` without clearing the var is that
  issue's scope).
  - Consequence for the plan's #3759 acknowledgement: with the SDK local, `test_ask_returns_200_shape`
    **passes** — the 502 it was documented as hitting was the leak, not the reader-build failure.
- **#4018 — `test_in_flight_cap_429` is not runnable in isolation.** Run alone it either fails at
  `gate.wait` (the four asks never reach the reader) or **segfaults** in the embedding lane
  (`tortoise_fts_query` → `sentence_transformers.encode()` under 4-way thread concurrency). Base
  behaviour reproduced; unrelated to this ticket's code (the failing stage is upstream of the reader).

### 4. Final verification (S11)

- `ruff check` on all 14 touched files: **clean** (10 findings introduced during implementation were
  fixed, not noqa'd; 8 auto-fixed, 2 hand-fixed as `contextlib.suppress` / f-string).
- `mypy` on the 9 touched source files: **clean, 0 issues**. (The tool is not in the repo venv —
  `uv run mypy` fails — so this was run as `mypy 1.18.2` + `types-PyYAML` installed into `.venv` as a
  local dev-env action; the repo's CI owns the pinned pass. First run reported 8 `import-untyped`
  errors for `yaml`, all stubs-only and all pre-existing.)
- Targeted pytest, docker lane: `test_ask_api.py test_ask_sdk.py test_mcp_server_auth_modes.py
  test_retrieval.py test_eval_ingest_retry.py test_ingest_v2_parallel.py` → **191 passed**;
  `test_w4_why_enrichment.py test_hosted_volunteer_context.py test_selfhost_volunteer_context.py
  test_volunteer_contract.py test_capture_cost_measurement.py test_ask_gating.py` → **126 passed**.
- Selfhost leg, embedded lane (`TORTOISE_TEST_CARVE_OUT=1`): `test_selfhost_rest.py` → **12 passed**
  (base: 11 passed; +1 is the new parity test).
- **Total: 329 passed, 0 failed.** Run with the fleet's `TORTOISE_API_URL` unset (unset in the two
  ask files by their own fixtures; unset in the environment for the rest — #4017).
- AC6/AC7 sweeps reproduced: `/v1/ask` consumers = exactly the five named files; `_ALLOWED_ANALYTICS_PROPS`
  appears only in `hosted_api.py`, `tests/test_ask_api.py`, and a **subset** assertion in
  `test_capture_cost_measurement.py`.
- Import smoke green; bound constants read back as `_ASK_TIMEOUT_S=10`, `ASK_BUSY_RETRY_AFTER_S=2`,
  `ASK_SDK_TIMEOUT_S > _ASK_TIMEOUT_S`, message digit-free.

### 5. Capped-exit disclosure (unchanged)

The plan-review cycle was **capped at the owner-authorized single cycle** (Option A + amendment). That
cycle's 6 P1 + 12 P2 + 6 advisory findings and the subsequent fold were **not re-reviewed** — the
authorization was for one cycle, and no second cycle was run. Everything in §1 was found by *tests*,
not by review.

---

## Cycle-6 code review (commit-workflow Step 2) — findings, dispositions

### Fixed in this cycle

| # | Sev | Where | Finding | Fix |
|---|---|---|---|---|
| 1 | P2 (×2: #1, #3) | `sdk.py` 504 arm comment | The comment still said the finite filter is "Written with the ``inf`` builtin" — i.e. it recommended the exact construct whose `NameError` this PR fixes, and contradicted the comment 18 lines below it. | Rewritten to name `_ASK_RETRY_AFTER_CEILING_S` and to state that `inf` is **not** a builtin. |
| 2 | P2 (×2: #2, #11) | `sdk.py` 504 body parse (and the 429 sibling) | `float()` on a **JSON integer** is arbitrary-precision: `float(int("9"*400))` raises `OverflowError`, which is an `ArithmeticError` and **not** a `ValueError`, so it escaped the `except (TypeError, ValueError)` and left `ask()` as an untyped error instead of the documented typed `AskTimeout`. Mechanism verified (`isinstance(e, ValueError) is False`). | `OverflowError` added to both parses; regression row added with a 400-digit body `retry_after`. |
| 3 | P2 (#2) | `retry.py` `delay_for` clamp | Same hole in the shared primitive, whose docstring promises "a malformed hint never escapes". | `OverflowError` added; a `10**400` row added to the malformed-hint test. |
| 4 | P2 (#1) | `hosted_api.py` `_emit_ask_latency_off_path` | Both `except` branches called `_logger.warning(...)` **outside** any suppression — the only statements in a documented "NEVER RAISES" function not inside a swallowing guard, so a raising handler/stream would escape (turn the pinned 504 into a 500). The same PR suppresses exactly this hazard for the strip log. Two standards for one hazard in one file. | Both wrapped in `contextlib.suppress(Exception)`, matching the strip log. |
| 5 | P2 (×2: #6, #7) | `schemas.ASK_BUSY_MESSAGE` | The message told the reader to retry "after the delay in the Retry-After header" — but it also ships as the **MCP tool error**, which has no HTTP response and therefore no header. On exactly the surface the measurement came from, the primary instruction named a field that cannot exist. | Reworded transport-neutrally (names the advertised value, then says where it lives per surface); still digit-free, so `ASK_BUSY_RETRY_AFTER_S` stays the single source. |
| 6 | P2 (#7) | 1987 doc: 12 `SUPERSEDED` markers + AMENDMENT; scope doc ×2 | The docs claimed the refusal "carries `Retry-After` … on hosted REST, selfhost REST **and MCP**" — a header is structurally impossible on MCP. A future reader would test for something that cannot exist. | Corrected in all **15 places cycle 6 searched** (12 `SUPERSEDED` markers + the AMENDMENT + scope-doc ×2). A **16th** site existed in production code (the `AskBoundedTimeoutError` docstring in `quota.py`), which cycle 6 did not search and cycle 8 did — see the cycle-7 and cycle-8 logs. |
| 7 | **P1** (#3) | `quota.py` bound rationale | The comment said the max "sits **above** the 15s budget … converts an **opaque client-side timeout**" and then, two lines later, said the 15s is the **CONNECT** budget and "explicitly **not** an ask caller's per-call timeout" — a self-contradiction, and the scope record had marked the abandonment reading **Withdrawn**. | The owner's memo directs that this framing be preserved, so it is **kept** — but the following paragraph now scopes it explicitly (the 15s is the narrowest client's CONNECT budget; the max is *inside* every ask caller's per-call budget and was a **successful** slow ask; nothing claims a per-call abandonment). The claim no longer contradicts itself. See the decision note below. |
| 8 | P2 (#7) | `tests/test_ask_api.py` | I had added a `set(props) <= _ALLOWED_ANALYTICS_PROPS` line next to the exact-set assertion. A subset check is a **tautology** after the allowlist filter has run — it cannot fail for the reason its comment claims. | Dropped; the exact-set assertion (which can fail) is kept. |

### Dispositioned, not fixed (with reason)

| Where | Finding | Disposition |
|---|---|---|
| `hosted_api` vs `mcp_server` emitters | Two divergent off-path telemetry pumps (counter+sync-drain vs future-set+async-drain, `BaseException` vs `Exception` swallows, two retention registries); neither drain sees the other's writes, and a fix to one does not reach the other — demonstrated by this PR having to fix a swallow in only one of them. | **Filed #4023.** Extracting a shared cross-module emitter is a refactor of `mcp_server`'s telemetry machinery, which is beyond the owner's R5-1 scope ("reuse the module's own `_retain_feed_task`; not scope growth"). |
| Three refusal builders + the coercion asymmetry | `int(float(header))` truncates on the two REST surfaces while MCP emits the raw constant, so the "single source" is only nominal for a non-integral value. | **Appended to #4013** (the filed R5-3 home), with the asymmetry and a suggested canonical-type fix. R5-3 was explicitly not to be folded. |

### Decision note — the withdrawn 15 s claim (P1 #7)

The scope doc marks the "60 > 15 ⇒ the client abandons" reading **Withdrawn** (the 15 s is the
narrowest client's CONNECT budget; the ask caller's own budget is wider: 75 s SDK, unbounded MCP).
The owner's work order for this implementation directs that the *framing* travel verbatim:
"the 21.759 s max sits above the 15 s client budget, so a bound converts an opaque client-side
timeout into a legible refusal while the server stops burning the work." **The owner ruling wins
over the scope doc's withdrawal** — a recorded decision outranks a prior artifact — so the framing
is preserved in the PR body as directed, and the code comment now carries it *plus* the measurement
scoping in one coherent paragraph rather than two contradictory ones. The ten-second bound itself
does not rest on this: it is justified from the distribution (above the p99, cutting only the single
long tail) and from D-12.

### Clean in this cycle (checked, not reported)

Retry primitive additivity (`ingest_v2.py` callers byte-identical); exactly-once counter discipline
under a 50-emit concurrency probe; the predicate rejecting bare 504 / client-fired timeout / 429 /
502; the deadline clamping the sleep rather than sleeping past it; retention self-pruning; the
constant relations (`10 > 5 > 0`, `10 + 2 < 15`, `75 > 10`) and `SLO_MS = 300` untouched; no
header-injection or tenant leak on the new body/header mirror; the four surviving-schema consumers
filtering by `event_name` so the new `ask_request` rows never enter their populations; and all four
prior artifacts the change amends verified to exist and to say what the PR claims (#4013, #4015,
#4017, #4018).

---

## Cycle-7 code review (re-review of the cycle-6 fix commit) — findings, dispositions

### ⛔ Process failure found by this cycle — the verification gate verified the wrong artifact

**The gate passed and the commit still shipped the defect.** Sequence: the 11 files were staged →
VGATE **failed** on the 1987-doc AMENDMENT → the AMENDMENT was corrected **in the working tree** →
VGATE was re-dispatched and **PASSED**, returning `349b42f9…` for that doc → `git commit -F` was run —
and `git commit` records the **INDEX**, not the working tree. The index still held the pre-fix copy, so
`93b4165e7` shipped the uncorrected text while the gate had certified the corrected text. Two
independent reviewers (Bug pass 2 and History) found it by reading `git status` / `git grep … HEAD`.

**This is the fleet's own "verify the ARTIFACT, not the send" rule failing in a new place.** A gate
whose input is the working tree cannot certify a commit whose input is the index. The residual risk is
general: **every VGATE-then-commit sequence in this repo has the same hole**, because re-verification
after a finding inherently edits a tree that is already staged. Filed for the mechanism (not this PR's
code) as **agent-infra #1232**, with three candidate fixes; the smallest is to compare the staged blob
(`git show :<path>`) against the verified hashes at git-op time and refuse when they differ. **Recorded
here because the cycle-6 log's claim
was, at the pushed HEAD, false** — the honest record is that cycle 6's fix for that site did not ship
until cycle 7.

### Fixed in this cycle

| # | Sev | Where | Finding | Fix |
|---|---|---|---|---|
| 1 | **P1** (#1) | `quota.py` bound rationale | Cycle 6's reconciliation did not resolve the self-contradiction: the retained sentence still asserted the *withdrawn* causal reading ("converts an opaque client-side timeout into a legible refusal") while the paragraph below denied any abandonment, so the closing "NOTHING here claims…" was falsified by the text above it. | The owner's framing is retained **as a directive** (quoted, and labelled as product intent — "NOT a measured claim"), with the measurement scoping following it. The two paragraphs no longer assert and deny the same thing. |
| 2 | **P1** (#2, #3) | `docs/plans/2026-08-29-1987-ask-reader.md` AMENDMENT §1 + the cycle-6 count claim | The AMENDMENT still claimed the MCP tool surface carries a `Retry-After` **header** — there were **15** offending sites, not 14; the AMENDMENT was the one missed. The commit message and this plan's cycle-6 table both claimed completeness, so the record was wrong as well as the doc. | The AMENDMENT now scopes the header to the two REST surfaces and states MCP is body-only. Cycle-6 row 6's count corrected to 15; this section records why the earlier claim was false at HEAD. |
| 3 | P2 (#2) | `sdk.py` 429 arm | The finite/≥0 filter that makes the 504 sibling safe was **absent** on the 429 arm, so the fix commit's `OverflowError` catch gave that arm `None` for a huge **int** while a huge **float** (`1e400` → `inf`) or an `inf`/`nan` header/body survived verbatim on `AskQuotaExceeded.retry_after` — a caller honouring it dies with the very error just caught, and the MCP lane would mirror non-standard `Infinity`/`NaN` into JSON. | The same filter applied after the header→body fallback (identical shape to the 504 arm). New parametrized test `test_ask_429_advertised_hint_is_sanitised`; **4 of its 7 rows verified RED without the filter** (stash-verified), so it pins the bug rather than the line. The commit message's "a regression row each" is now true. |
| 4 | P2 (#2) | `retry.py` comment | My own new comment said a huge "int/Decimal" raises `OverflowError` from `float()`. Only `int` does — `float(Decimal('9'*400))` returns `inf`, so a huge Decimal takes the clamp path, not the floor path the comment describes, contradicting the docstring 30 lines above. | Corrected to `int`, with the Decimal distinction stated. |
| 5 | P2 (#1) | `hosted_api.py` `_emit_ask_latency_off_path` | Cycle 6's comment claimed the loop-branch log "is the one statement in this function NOT already inside a swallowing guard". False twice: there are **two** such logs, and the fallback `_ask_telemetry_decrement()` calls (in the `except BaseException` body and the bare `finally`) were likewise unguarded — and that function itself emits an **unsuppressed** `_logger.warning`, i.e. it raises exactly where the log does. So the "Never raises" docstring still held only incidentally. | Both fallback decrements now suppressed; the comment scoped to what is actually true; the docstring states the claim's exact scope (logs + fallback decrements guarded; a `MemoryError`-class failure is not pretended to be handled). New parametrized test `test_ask_emission_failure_path_cannot_escape` (4 cases); **the two `decrement` cases verified RED without the fix** (stash-verified). |

### Clean in this cycle (checked, not reported)

Receiver-side parse hardening (all hostile hints reach a defined outcome: huge int → `None`,
`inf`/`nan`/negative/HTTP-date → `None`; the sanitized value is used only as a delay, and `min(cap)`
plus the deadline clamp bound the actual sleep ≤ 25 s); retry amplification (attempts hard-bounded at 3,
per-delay cap 30 s, deadline enforced, jitter additive over the floor, bare 504 never retried);
information disclosure (the 504 body is a static constant + int + fixed string;
`_ALLOWED_ANALYTICS_PROPS` untouched by the fix commit; the suppressions remove no log record and leak
no internals); audit trail unaffected (the suppressed warnings are analytics dispatch, not
`_async_audit`); resource bounds (the retained-future registry self-prunes on completion; `/v1/ask` is
rate-limited; the `asyncio.Future` widening is safe — a `Task` is a `Future`); commit-message signature
integrity (balanced backticks, no `$()`/`${}` holes, no double-space tells); no prior-art churn (nothing
re-added or see-sawed); doc-affiliation clean on all 3 docs.

### Not re-litigated (settled, unchanged)

10 s server bound; refusal staying 504 + `timeout`; retry through `call_with_predicate`; `duration_ms`
persisted off the request path; `SLO_MS = 300` untouched; R5-3 filed as #4013 rather than folded; the
two off-path emitters filed as #4023; the capped plan-review exit disclosed as capped.

---

## Cycle-8 code review (re-review of the cycle-7 fix commit) — findings, dispositions

Security: **NO ISSUES FOUND** — it verified the 429 filter against a hostile corpus (`inf`, `-inf`, `nan`, `"nan"`, `"Infinity"`,
`"1e400"`, `10**400`, `"9"*400`, `-5`, `[]`, `{}`, `None`, HTTP-date, NUL — all → `None`), re-traced the
value flow to every consumer, and confirmed the suppressed decrements cannot hide a real leak (the
decrement logs *before* it can raise, and only on the branch where no decrement was owed).

### Fixed in this cycle

| # | Sev | Where | Finding | Fix |
|---|---|---|---|---|
| 1 | P2 (#1, #2) | `quota.py::AskBoundedTimeoutError` docstring | **Production code still carried the MCP-header over-claim** — "`Retry-After: ASK_BUSY_RETRY_AFTER_S` + a body `retry_after`/`message` on **all three surfaces (hosted REST, selfhost REST, MCP)**". This is the **16th** site, and it is not a doc: it is the exception's own contract, in the file this work already edits. Cycles 6 and 7 had corrected only the plan/doc copies of this claim and each declared completeness. | Rescoped to header on the two REST surfaces, body-only on MCP — the same split `ASK_BUSY_MESSAGE` documents. The cycle-6 row now records its 15-place scope and names the production site it missed. |
| 2 | P1→P2 (#2) | `sdk.py` 429 comment | The filter's justification claimed it stops a caller's `time.sleep(exc.retry_after)` from dying with `OverflowError`. It is a **well-formedness** filter, not a magnitude bound: a finite-but-absurd hint (`1e308`) is admitted and `time.sleep(1e308)` does raise `OverflowError`. The protection was real but **partial**, and the comment (and the commit message) overstated it. | The comment now states the exact scope — non-finite/NaN/negative rejected; a finite hint is passed through unchanged as the server's advertisement, and nothing in-repo sleeps on a 429's value. Behaviour unchanged (clamping the value would misreport what the server said). |
| 3 | P2 (#1) | `sdk.py` 429 comment | A code span read `float("9"*400")` — a stray quote, a `SyntaxError` if a reader pastes it, which defeats the point of quoting the exact expression. | Corrected to `float("9"*400)`. |
| 4 | P2 (#1) | `quota.py` bound rationale | The cycle-7 replacement said the earlier revision stated the claim as fact and denied it "two paragraphs later" — false against **both** prior revisions (same paragraph in `fb787e641`, next paragraph in `93b4165e7`), i.e. a claim about the comment's own edit history that no artifact binds and that can only re-stale. | Replaced with the durable reason (the abandonment reading is **Withdrawn** in the scope record while the owner directed the framing be kept), which needs no history. |

### Convergence note

**Enumerate the set before claiming completeness over it.** An assertion can be true of the files that
were searched and false of the set that was not.

### Not re-litigated

All cycle-7 dispositions stand; the `header="inf", body=42 → None` case is pre-existing and consistent
with the settled 504-arm ordering (no usable value is lost — the pre-delta code kept `inf` and never
consulted the body either).

---

## Cycle-10 code review (re-review of the cycle-9 fix commit) — findings, dispositions

| # | Sev | Where | Finding | Fix |
|---|---|---|---|---|
| 1 | P2 | plan doc, convergence note | The cycle-9 rewrite still narrated the journey and still claimed completeness ("took three cycles", "cycle 8 found **the last site**"). A claim about process has no artifact to check it against, so it can only re-stale; the fix is deletion, not better narration. | Reduced to the rule alone: enumerate the set before claiming completeness over it. |
| 2 | P2 | `tests/test_ask_api.py` | The comment cited `hosted_api.py:23469` for the Stripe analytics caller — a number this PR's own insertions invalidated, so the reference pointed at unrelated code. | Replaced with the symbol and the call shape. |
| 3 | P2 | `tests/test_selfhost_rest.py` | Same class: `sdk.py:13950` for the `TORTOISE_API_URL` remote delegation. | Replaced with the expression (`if os.environ.get("TORTOISE_API_URL")` in `sdk.ask`). |
| 4 | P2 | `tortoise/hosted_api.py` | Same class: `mcp_server.py:188-224` for the telemetry "never raises" contract, shifted by this PR's import in `mcp_server.py`. | Replaced with the symbol name (`_emit_mcp_tool_call_telemetry`). |
| 5 | P2 | `docs/plans/2026-09-18-3834-cold-busy-wait-bound.md` | The cycle-10 entry itself carried the same two classes it had just removed from the convergence note (reviewer tally, a completeness claim, cycle accounting), and its own table cited a line number (`hosted_api.py:23712`) that the fix commit shifted; two further `mcp_server.py:188-224` references survived in the plan body. | Entry reduced to the findings table; the line number dropped; both surviving references repointed to the symbol. |
