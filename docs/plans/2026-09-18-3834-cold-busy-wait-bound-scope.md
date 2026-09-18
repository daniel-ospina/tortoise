---
title: "#3834/#3993 Scope — the cold/busy ask wait bound + legible refusal + retry"
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
<!-- plan: docs/plans/2026-09-18-3834-cold-busy-wait-bound.md -->

# Scope (cycle 4) — #3834: the cold/busy wait bound + the retry story (task, standard)

**Unit:** owner-assigned #3834. Work order: `~/.pi/agent/state/lane-reports/B4-3834-WORK-ORDER-2026-09-18.md`
— the owner took both the *value* **and the retry question** onto his own plate (*"the flat 15 s with NO
RETRY is now a DESIGN QUESTION I OWN, not a given"*). **Direction decided (D-12-family): a server-side
wait deadline under the caller's budget + retry/resilience. Not re-litigated.**

**Cycle-5 note:** cycles 1–4 were each rejected by the scope verifier; every finding has been P0/P1 and
progressively narrower, and cycle 4 returned **no design-resting issue** — 10 P2 disclosure/precision
gaps, all closed below. See `## Cycle-5 changelog`.

## Domain
**(not adversarial)** — no gate/enforcement code whose correctness is "an attacker cannot make it fail
open". Latency/refusal-contract code. (The retry envelope is a bounded amplification surface, capped by
an attempt count + a deadline; it is not a fail-open gate.)

## Scope boundary — what this fixes, and what it does NOT
| Fixed here | NOT fixed here (named, so the PR cannot over-claim) |
|---|---|
| The ask lane's wait deadline (`_ASK_TIMEOUT_S`) is set to the owner's Option-C product choice, under the fleet's narrowest client budget with headroom. | The **startup/connect** stall and the zero-byte 503 on the MCP connect path — the auth plane (#3144 → #3851); the general cold-start case is **#3498 / #3284**. |
| The bound-breach refusal becomes machine-actionable on hosted REST, selfhost REST and MCP (`Retry-After` + `retry_after` + `message`). | The **MCP client's** retry itself — Pi's `mcp-client`, a different repo: **agent-infra #1174**. The boundary (an advertised delay in the tool result) ships here. |
| A bounded, jittered, deadline-guarded retry ships for the ask REST lane (`_post_ask`), recovering **transient/queued** slowness. | Tail recovery on the **measured MCP transport** (depends on #1174) and the **10–60 s genuinely-slow band**, which Option C deliberately refuses (see C.1/C.3). |
| The ask lane's wall-clock becomes persisted (off the response path) so the bound is re-derivable. | The absent perf lane (#3992) — this does not become that lane. |

## Problem diamond

### Divergent framings
1. **"The bound never received the recorded 15 s anchor."** `_ASK_TIMEOUT_S = 60` (`tortoise/quota.py`).
   D-12 (the lane's recorded deadline-propagation decision: *"the server bound sits under the caller's
   budget … our narrowest uncontrolled client holds 15 s flat"*) was applied to the connect path, not to
   the ask lane's wait deadline. **Honest scope of what that means (cycle-4 correction):** it is **not**
   a D-12 *correctness* violation on the ask lane — the ask callers' own budgets are **75 s** (SDK) and
   **effectively unbounded** (the MCP client's ask tool call passes `timeout: 3_600_000`), both above
   60 s, and today's ask 504 *is* receivable. What is unapplied is the **product choice** behind the
   anchor: a bounded wait whose expiry is advertised as a retry signal.
2. **"The refusal is illegible."** On breach, `run_ask_bounded` raises `AskBoundedTimeoutError`, mapped
   to **504** `{"error": {"code": "timeout"}}` with **no `Retry-After`** and no retry guidance — on the
   hosted REST route, the selfhost REST route and the MCP tool surface alike.
3. **"Nothing recovers."** No ask client we ship retries: `_post_ask` carries the documented *"no
   auto-retry v1"* pin; `tortoise/mcp_client.py` does not implement the ask wire contract at all.
4. **"We cannot re-derive the bound."** The ask route computes `duration_ms`, returns it to the caller,
   and **never persists it** (#3993).

### Correction carried from cycle 1 (P0) — what 15 s is
Cycle 1 asserted *"60 > 15 ⇒ the client abandons."* **Withdrawn.** 15 s is the **connect** budget in the
client #3851 cites (`connectWithTimeout(..., timeoutMs = 15000)`; `DEFAULT_CONNECTION_TIMEOUT_MS =
15000`); that client's **ask tool call** passes `{ timeout: 3_600_000 }`, and `ASK_SDK_TIMEOUT_S = 75`.
So today's ask 504 *is* receivable and the 21.759 s max was a **successful slow ask**.

**15 s, stated once and used consistently:** the *narrowest budget any hosted client exposes to the
service* — its connect budget — and the number D-12's recorded constraint is written against. It is
**not** asserted anywhere to be an ask caller's per-call timeout, and **no** claim in this scope, the
tests, or the PR says an ask is abandoned at 15 s.

### Root cause (converged)
Four legs of one defect on the ask lane: **(1)** the ask wait deadline was never set to the owner's
bounded-wait product choice (60 s ⇒ slow work is neither cut nor advertised — the caller is held for a
minute with no signal, and a turn that cannot finish inside any interactive budget burns the slot);
**(2)** an illegible refusal; **(3)** no recovery leg; **(4)** no persisted measurement. The bound is what
makes a legible refusal *reachable*, the refusal is what makes a retry *possible*, and the persisted
duration is what keeps the bound *derivable*.

**Note on leg (1):** changing 60 → 10 is a **product decision (Option C)**, not a bug fix — it trades the
10–60 s genuinely-slow band away (see C.1/C.3) in exchange for a bounded wait with an advertised retry
signal. The work order hands that choice to this lane. The *measurement* is what justifies the value.

**Symptom-vs-root-cause check:** the issue text describes a stall plus an empty error. The converged
definition targets the ask lane's four legs and explicitly excludes the connect/auth symptom and the
reflex fail-open path.

## Evidence (measured; not re-measured here)
`~/.tortoise/analytics_fallback.jsonl` (local JSONL fallback of `analytics_events`), `tortoise_ask`
**n = 573**: p50 **1 ms**, p95 **346 ms**, p99 **3177 ms**, max **21759 ms**. All tool calls (n = 7070):
p50 26 ms, p95 2775 ms, p99 22476 ms, max 147967 ms.

> **CAVEAT that travels with these numbers:** this is the **MCP transport, per tool call — not the REST
> HTTP request wait.** It is the best available proxy, not the same quantity. (The two rows also use
> different quantile conventions — the ask row's p95/p99 are lower-interpolated observed values, the
> all-tools p95 is nearest-rank — so the p95s are not directly comparable. The decisive counts in C.1 are
> exact, not extrapolated.)

**Why the proxy is admissible (verified):** `run_ask_bounded` is *"the ONE wrapper the hosted HTTP
handler, the hosted MCP handler, and the selfhost REST handler all await"* — a bound set inside it
governs all three surfaces, and the measured MCP latency is a superset view of the ask wait.

## Solution diamond

### Alternatives
| | Approach | Consequence | Verdict |
|---|---|---|---|
| **A** | Client retry only; keep the 60 s hold | The refusal stays illegible, so the retry has **nothing to key on** — no advertised delay to honour, and a client-side invention ignores the server's state; the caller is still held for up to a minute with no signal | **Rejected** |
| **B** | Server bound only; no retry | A shorter but equally **illegible** failure — the brief's explicit rejection (*"a visible failure with no recovery"*) | **Rejected** |
| **C** | **Bound + legible refusal + bounded retry + off-path measurement** | Waits bounded and advertised; the refusal is machine-actionable on all three surfaces; transient/queued slowness recovers; the bound becomes re-derivable | **Chosen** |

### Converged approach (C)

**C.1 — Server bound = 10 s.**
`_ASK_TIMEOUT_S` becomes **10** (from 60). Derivation from the measured distribution
(**exact counts, recomputed from the JSONL — not extrapolated from a percentile**): of **n = 573**
`tortoise_ask` calls, **exactly 1 (0.175 %) exceeds 10 s** (8 exceed 3 s; 6 exceed the 3.177 s p99).
So 10 s keeps **572/573 = 99.825 %** of measured asks, and **the single ask it cuts is the 21.759 s max.
That is the decisive cost in numbers: Option C spends one measured call — a *successful slow ask today*,
not an abandoned one — plus any unmeasured 10–60 s tail, to buy the bounded wait and the advertised
retry signal.** (The 21.759 s call is **inside** the ask caller's budget — 3 600 000 ms in the client
#3851 cites; nothing here claims it was abandoned.)
It leaves **~5 s** for the response to travel and render — the property the owner named (*"the client
must still be able to receive something before its own deadline"*), and D-12's *server ≤ the narrowest
client budget, with headroom*. `ASK_BUSY_RETRY_AFTER_S = 2`, so
`_ASK_TIMEOUT_S + ASK_BUSY_RETRY_AFTER_S = 12 < 15`.
**What Option C trades away, stated plainly:** every ask whose *work* takes 10–60 s becomes a refusal:
measured, that is the one 21.759 s call (today successful) plus any unmeasured slower tail. That is the
decision, not an accident; the retry leg is what keeps the *transient* part of that band recoverable
(C.3).
**Second-order effect, disclosed:** `_ASK_EXEC_FLOOR_S = 5.0` is unchanged, so the **semaphore queue
window** (`acquire_timeout = _ASK_TIMEOUT_S − _ASK_EXEC_FLOOR_S`) shrinks 55 s → **5 s**, and a started
ask still gets a ≥5 s execution guarantee. 5 s of queue wait is at the measured p99 (3.177 s) — a caller
that has already queued that long is at the tail, so refusing is honest rather than premature. Pinned by
a test (`_ASK_TIMEOUT_S > _ASK_EXEC_FLOOR_S > 0`).
**Explicit reasoned departure, constraint named:** the value differs from `SLO_MS = 300`
(`tortoise/volunteer.py`, pinned `docs/planning/2026-09-01-2080-gbrain-plan.md:1239`), which governs
**`/v1/context`** (reflex path; ceiling `SLO_MS*8/1000 = 2.4 s`; breach → fail-open
`degraded_response(DEGRADED_TIMEOUT)`, **never a 503**). **Not ours to move; not moved.** Different route,
different workload, own bound.

**C.2 — Legible refusal: 504 preserved, made actionable, disclosed as a body extension.**
The ask-lane bound breach is **pinned to `504` + code `timeout`** by a recorded plan
(`docs/plans/2026-08-29-1987-ask-reader.md`: acceptance + test matrix pin *"timeout → 504 `timeout`"*;
the 10-code vocabulary with *"never an 11th code in v1"*; body `{"error": {"code": …, "retry_after": …}}`).
**A 503 + new `ask_busy` code was considered and REJECTED** — it would contradict that recorded contract,
and no reopen is warranted because the requirement is satisfiable without moving the status. What changes:
- **REST (hosted + selfhost):** `504` + `Retry-After: 2` + body
  `{"error": {"code": "timeout", "retry_after": 2, "message": <ASK_BUSY_MESSAGE>}}`.
- **MCP:** the tool result carries the same fields (the surface is consumed as a tool result, not HTTP).
- `ASK_BUSY_MESSAGE` is a static, non-leaking constant in `tortoise/schemas.py`, worded to cover both
  breach causes honestly (the client cannot distinguish a queued ask from a slow one — see C.3) and
  **carrying no literal number**, so the value lives in exactly one place (`_ASK_TIMEOUT_S`):
  *"The ask exceeded the server's wait deadline (the service was cold or busy). Retry after the advertised delay; the answer was not produced."*
- **Disclosed body-shape extension:** `message` is additive. The unit's requirement is *"machine-actionable
  (status + Retry-After + a clear body)"* — that phrasing is **the unit's, not an owner quote**; the
  owner's recorded words are *"hand back the retry signal"* / *"a 'come back in N seconds' signal that the
  client is expected to honour"*. `code` and status are unchanged.
- **RFC note + marker (1 of 2):** RFC 9110 §10.2.3 normatively associates `Retry-After` with `503`; we
  send it on `504` because the status is plan-pinned and the field is the only machine-readable delay
  signal. Against-the-grain ⇒ an **`OVERRIDES:` line**, concrete text pinned below.

**C.3 — Retry leg: the plan's own primitive, with the plan's pin reopened in its own home.**
- **Reopen (marker 2 of 2).** The 1987 plan pins *"no auto-retry v1"* for `_post_ask` (Integration
  Surface Map row 6 `:41`; Journey step 6 `:73`; Task 5 acceptance `:229`) and names
  `tortoise/retry.py::call_with_predicate` as *"the documented follow-up"*. The owner's later work order
  retires that pin. This PR therefore **(a)** appends a dated **AMENDMENT** to
  `docs/plans/2026-08-29-1987-ask-reader.md` carrying BOTH `OVERRIDES:` lines, **(b)** marks **every**
  superseded plan site in place (the plan's own convention), and **(c)** posts both lines on **#3834**
  (this lane's issue) **and on #1987** (the closed decision issue — `AGENTS.md` puts the marker on the
  artifact a lane reads; the plan doc is also updated as the decision's home). The sites are enumerated
  in AC7 with a post-amendment `rg` check, so "marked superseded" is verifiable rather than asserted.

  **The two `OVERRIDES:` lines, text pinned (AGENTS.md: named default + one sentence of reason):**
  - `OVERRIDES:` this plan's `_post_ask` **"no auto-retry v1"** pin (Integration Surface Map row 6,
    Journey step 6, Task 5 acceptance) — the owner took the retry decision onto his own plate in the
    #3834 work order (*"the flat 15 s with NO RETRY is now a DESIGN QUESTION I OWN, not a given"*), and
    the retry ships through this plan's own designated primitive `tortoise/retry.py::call_with_predicate`.
  - `OVERRIDES:` **`Retry-After` on a `503`** (RFC 9110 §10.2.3's normative association) — we send the
    field on the plan-pinned `504` because the status is a recorded decision and the header is the only
    machine-readable delay signal; changing the status to `503` would contradict the plan's 10-code
    vocabulary (*"never an 11th code in v1"*).
- **Implementation uses the designated primitive** — `tortoise/retry.py::call_with_predicate`, the repo's
  single-source bounded jittered retry loop — with `marker_armed=False` so an exhausted retry re-raises
  the **original** `AskTimeout` unwrapped (never the `WriteStageRetriesExhausted` write sentinel; an ask
  is a read).
- **Two additive, default-inert parameters** are added to that primitive so this unit needs no second
  loop: `delay_for(exc) -> float | None` (the **server-advertised floor**) and `deadline` (no retry sleep
  may *begin* at or after this instant). Defaults `None` leave every existing caller and test
  byte-identical.
- **Honouring `Retry-After`, with jitter preserved and the cap respected.** When the server advertises a
  delay the primitive's exponential base is **replaced** by that floor, **clamped by the primitive's own
  `cap`** (`floor = min(float(advertised), cap)` — so a malformed/absurd `Retry-After` cannot produce an
  unbounded single sleep), and the jitter is added **above** it: `wait = floor + random() * floor` (an
  advertised 2 s yields 2–4 s). This is a **documented deviation from Full Jitter** (which assumes no
  server hint): RFC 9110 §10.2.3 makes the advertised value a minimum, so jitter must be additive above
  the floor, not a `max()` that collapses to the floor and re-synchronises retries. Without an advertised
  delay the primitive's existing `min(base**attempt, cap) * (0.5 + random()/2)` applies unchanged.
- **Bounds — named for what they are.** `ASK_RETRY_ATTEMPTS = 3` (total `fn()` invocations; the call site
  passes `retries = ASK_RETRY_ATTEMPTS - 1` because the primitive's `retries` is the *retry count*, not
  the attempt count) and `ASK_RETRY_DEADLINE_S = 25`. **`ASK_RETRY_DEADLINE_S` is a sleep deadline, not a
  total envelope** (cycle-4 finding 3): it stops a retry *sleep* from beginning at/after the instant, and
  the honoured sleep end is itself clamped to it (`wait = min(wait, deadline − now)`). It does **not**
  bound the HTTP attempt — that is `ASK_SDK_TIMEOUT_S = 75` per attempt. **Disclosed worst case:**
  3 attempts × 75 s + 2 sleeps ≤ ~229 s, bounded and typed, never unbounded. The name says so.
  **Cap semantics, stated exactly** (cycle-5 finding 4): the primitive's `cap` bounds the **floor**, so a
  single honoured sleep is ≤ **2 × cap** (bounded, not unbounded), further clamped by the deadline — the
  pre-existing path's sleep is ≤ `cap`.
- **Only an advertised refusal is retried.** Predicate: `isinstance(e, AskTimeout) and e.source ==
  "server" and e.retry_after is not None` **plus the deadline guard** — nothing else. A `504` with no
  `retry_after` keeps today's behaviour (one attempt); `429 quota_exceeded`/`in_flight_limit`, `502`, and
  code-less `503` keep their existing non-retryable mappings.
- `AskTimeout` gains an optional `retry_after` attribute — **no new exception type**.
- **What a stop raises.** Both stop paths — attempt cap exhausted **and** the deadline guard — re-raise
  the **caught exception unwrapped** (a bare `raise` inside the `except`), so the caller always receives
  a typed `AskTimeout` (with `retry_after`), never a sentinel or a fresh error.
- **Recovery claim, qualified:** the retry recovers **transient/queued** slowness (a slot frees, a stalled
  reader lane returns). It does **not** rescue the **10–60 s genuinely-slow band** — that work re-breaches
  on every attempt and ends in the advertised refusal, which is Option C working as decided. The caller
  ends with a legible, actionable refusal instead of an opaque one.
- **Known costs, disclosed:** (a) the client cannot tell a queue-wait breach from a reader breach — both
  are `AskBoundedTimeoutError` → the same refusal — so a retry under sustained overload re-hits the
  overload signal, bounded to 3 attempts with de-synchronised waits; (b) a retried budgeted ask consumes
  another per-minute budget slot; (c) `run_ask_bounded` **shields** the reader future, so a reader already
  in flight completes and **yet another `record_ask_usage`/cost record can be written per attempt** (the
  plan's honest-metering invariant already allows *"the 504 case may still record usage"*) — so a
  504-then-retry can produce N meter records. Pinned by a test.
- **MCP transport (different repo).** The measured tail is the MCP tool call, whose client is Pi's
  `mcp-client` — **agent-infra #1174**. **The boundary ships here** (the tool result carries
  `retry_after` + `message`) and the PR states exactly what #1174 must add. `tortoise/mcp_client.py` is
  **not** a retry site because it is a **generic fastmcp tool caller that does not implement the ask wire
  contract at all** — `_post_ask` is the repo's only ask REST client.

**C.4 — Off-response-path measurement.**
The ask route's already-computed `duration_ms` is persisted through the **existing** writer
`_track_analytics_event` (blocking `httpx` POST), dispatched **off the request path** exactly as
`tortoise/mcp_server.py` does (`asyncio.get_running_loop()` → `run_in_executor`, else daemon thread).
Event `ask_request`, props `{"duration_ms", "status"[, "error_kind"]}`, with a pending-set + flush seam
so the off-path property is testable. **The allowlist MUST change:** `_ALLOWED_ANALYTICS_PROPS` carries
`latency_ms` but **not `duration_ms`**; an unchanged allowlist would strip the value (the #3359
"measured fields must survive the PII filter" failure). `duration_ms` (not `latency_ms`) is the honest
key — REST request wall-clock is a different quantity from the MCP tool-call `latency_ms`.
Emitted on **success** (`status="ok"`) and on the **refusal** (`status="timeout"`); the refusal path
never reaches the success site, so it computes its own elapsed from `t0` in the `except`. No new table,
no endpoint.

## Out of scope (explicit)
- `SLO_MS = 300` / `/v1/context` — recorded decision; untouched.
- The reflex path's fail-open design — untouched (breach stays `DEGRADED_TIMEOUT`, never a 503).
- The connect/auth 503 retry (#1174) and the startup stall (#3498/#3284).
- `tortoise/mcp_client.py` retry — it does not implement the ask wire contract.
- #3992 (perf lane) — separate.

## Acceptance criteria
1. `_ASK_TIMEOUT_S` is **10**, and tests pin: `_ASK_TIMEOUT_S + ASK_BUSY_RETRY_AFTER_S < 15` (comment
   states **exactly what 15 s is** — the narrowest client budget we expose / D-12's anchor); the
   **exec-floor** relation `_ASK_TIMEOUT_S > _ASK_EXEC_FLOOR_S > 0`; and the **per-attempt** transport
   invariant — `ASK_SDK_TIMEOUT_S > _ASK_TIMEOUT_S`, i.e. the SDK's per-attempt `requests` timeout
   outlives one server wait, so a breach is always received as the typed `AskTimeout` and never as a
   socket timeout. **Explicitly not claimed:** that 75 s bounds the retry envelope — `ASK_RETRY_DEADLINE_S`
   governs only whether another *retry* is started, and the worst-case envelope (~3×75 s + sleeps) is
   documented in code and the PR. The value's derivation (and the exact 1-of-573 count) is recorded in
   code + PR with the transport caveat.
2. The bound breach returns **504 + `Retry-After` + a body carrying `code`, `retry_after` and a static
   actionable `message`** on hosted REST, selfhost REST and MCP; a test pins all three, so it cannot
   silently revert to a bare 504.
3. `_post_ask` retries an **advertised** refusal through `tortoise/retry.py::call_with_predicate` with
   `retries = ASK_RETRY_ATTEMPTS - 1`; tests prove (a) it retries and honours `Retry-After` as a **floor**
   with jitter added above it (waits are not all equal), (b) exactly **3** `fn()` invocations when the
   attempts are exhausted, and the deadline guard stops a further sleep, (c) an **advertised** `504` is
   retried while a **bare** `504`, a **client-fired** `AskTimeout` (`source='client'` — the wire
   timeout the `source` field exists to exclude), `429`, `502` and code-less `503` are **not**, and
   (d) **both** stop paths — exhaustion and the deadline guard — surface the **original** `AskTimeout`
   unwrapped (no sentinel). **Validated on the SDK/REST lane only**; the recovers-claim is qualified to
   transient/queued slowness.
4. `duration_ms` is persisted via `_track_analytics_event`, **off the request path**, for **each hosted
   request that terminates in success (`status="ok"`) or bounded refusal (`status="timeout"`)** — a
   request that ends in validation/quota/in-flight/reader/retrieval error emits **no** row (those arms
   raise before either emission site; the AC states this rather than a blanket "per request"). Tests
   prove, as **two distinct properties**:
   - **(a-i) off the event loop**, asserted **behaviourally** by thread identity at the **real route** —
     the repo's `test_emission_write_is_handed_off_the_event_loop` precedent, hang-free, with the
     precedent's "the probe actually ran" guard so the assertion can never go vacuous;
   - **(a-ii) non-blocking**, asserted by a **bounded, guaranteed-release** recorder (sets a `started`
     event, blocks on `release.wait(timeout=…)`) proving the helper returns while the write is still in
     flight — thread identity alone cannot distinguish fire-and-forget from an awaited `to_thread`;
   - **(b)** the allowlist carries the value (the #3359 failure class);
   - **(c)** **exactly one** `ask_request` row per committing hosted request.
   **The retry's effect on row count is stated as the composition of two separately-tested facts, never
   as one end-to-end assertion:** the SDK client makes exactly N attempts on an advertised refusal (a
   client-side test against the fake server, which never touches `hosted_api`), and the server emits one
   row per committing request (a server-side test) — so N attempts ⇒ N rows. The **metering**
   consequence of the shielded reader future is likewise stated as a consequence, not asserted as a
   count.
5. The reflex fail-open path and its tests are untouched and green; `SLO_MS` / `/v1/context` unchanged.
6. Stale references reconciled — the sweep is enumerated and post-checked:
   `ASK_SDK_TIMEOUT_S` comment + `_post_ask` docstring, `AskTimeout` docstring,
   `AskBoundedTimeoutError`/`run_ask_bounded` docstrings, **`tortoise/quota.py`'s `_ASK_TIMEOUT_S`
   comment ("no real 60s sleeps")**, `tortoise/mcp_server.py`'s `tortoise_ask` docstring ("60s"),
   `tortoise/selfhost_api.py`'s ask docstring, the hosted ask handler docstring, and
   **`docs/product/answer-surface.md`** — the `504`/`timeout` row (`:230`), the budget prose
   (**`:198-199` "Global Semaphore(8) + 60s total per-request bound"**), the body shape (`:211`) and the
   retry-follow-up note (`:241-243`). Checks, run at implementation time and recorded in the PR:
   (i) `rg -n "60 ?s|\(60\)|, 60\)" ` over the enumerated ask-lane lines returns no un-annotated stale
   hit — the pattern **must include `(60)`**, which is the form at `tortoise/sdk.py:53`; unrelated
   non-ask-lane hits (TTL/redis/Fly constants) are excluded by inspection and named as such.
7. `docs/plans/2026-08-29-1987-ask-reader.md` gets a dated **AMENDMENT** with **both `OVERRIDES:` lines**
   (text pinned in C.3), **in-place supersede markers on every reversed site**, and the same two lines
   posted on **#3834** and **#1987**. Reversed sites, enumerated (cycle-4 findings 1 and 8; cycle-5
   finding 2):
   **retry pin** — Integration Surface Map row 6 (`:41`), Journey step 6 (`:73`), Task 5 acceptance
   (`:229`), changelog row 74 (`:496`); **bound pins** — surface row 7 (`:42`), Journey 16 (`:83`),
   Failure Modes (`:90`), `:204`, `:267`, `:277`, `:282`, `:436`, changelog rows 40 (`:462`), 50
   (`:472`), **80 (`:502`)**, 119 (`:541`). Checks, run at implementation time: after the amendment,
   (i) `rg -n "60 ?s|\(60\)|, 60\)"` over the plan returns only the AMENDMENT's own supersede
   annotations, and (ii) `rg -n "auto.retry"` over the plan likewise returns only the AMENDMENT's own
   annotations — so both sweeps are verifiable, not asserted.
8. The existing `tests/test_ask_api.py` timeout pin is updated to the new contract (not weakened);
   `tortoise/retry.py`'s new parameters get their own tests (floor + deadline + inertness), so the shared
   primitive stays covered and existing callers stay green.

## Wiring check
- `tortoise/quota.py` — `_ASK_TIMEOUT_S` (10) + `ASK_BUSY_RETRY_AFTER_S`; docstrings.
- `tortoise/schemas.py` — `ASK_BUSY_MESSAGE` (+ `__all__`).
- `tortoise/hosted_api.py` — ask route: `Retry-After` header on the 504 + refusal-path `duration_ms`
  emission; path-scoped handler: mirror `retry_after` from the header generically, add `message` for
  `timeout`; analytics allowlist `+duration_ms`; `_emit_ask_latency_off_path` + flush seam.
- `tortoise/selfhost_api.py` — same `Retry-After` header; `tortoise/selfhost.py` — same body mirror.
- `tortoise/mcp_server.py` — `tortoise_ask` timeout branch carries `retry_after` + `message`; docstring.
- `tortoise/exceptions.py` — `AskTimeout.retry_after` (optional; exception set unchanged).
- `tortoise/retry.py` — additive `delay_for` + `deadline` parameters (defaults inert).
- `tortoise/sdk.py` — `_post_ask` retry via `call_with_predicate`; **parse `Retry-After`/body
  `retry_after` on the 504 branch** (today it parses them only on the 429 branch); `ASK_RETRY_*`;
  docstrings.
- `docs/plans/2026-08-29-1987-ask-reader.md` + `docs/product/answer-surface.md` — amendments.
- Tests: `test_ask_api.py`, `test_ask_sdk.py`, `test_mcp_server.py`, `test_retrieval.py` (primitive),
  selfhost parity.

## Cycle-4 changelog (cycle-3 findings)
| # | Finding (cycle 3) | Disposition |
|---|---|---|
| 1 (P1) | Root-cause leg (1) claims D-12 violated, refuted by the artifact's own correction | **Accepted.** Reframed as the owner's **Option C product choice**, not a correctness fix; the "may never get a verdict" claim dropped. |
| 2 (P1) | "A bounded retry recovers" overstates | **Accepted.** Qualified to transient/queued slowness in C.1, C.3 and the Scope-boundary table. |
| 3 (P2) | Queue 504 retried like a reader 504; fixed Retry-After collapses jitter | **Accepted.** Reported honestly; jitter made additive-above-floor. |
| 4 (P2) | `docs/product/answer-surface.md` missing | **Accepted.** AC6 + wiring. |
| 5 (P2) | Plan's literal "60s" pins unmarked | **Accepted** (cycle 4 widened the sweep — see below). |
| 6 (P2) | `OVERRIDES:` home doesn't satisfy AGENTS.md | **Accepted.** Posted on #1987 **and** #3834 + plan AMENDMENT. |
| 7 (P2) | One marker named, two rulings exist | **Accepted.** Both lines required, with concrete text pinned. |
| 8 (P2) | Queue-wait budget shrinks 55 s → 5 s | **Accepted.** Disclosed + justified; relation pinned. |
| 9 (P2) | Retries can duplicate meter records | **Accepted.** Disclosed + AC4 test. |
| 10 (P2) | AC3/wiring gaps (non-advertised statuses, 504 parse) | **Accepted.** AC3(c) + wiring. |
| 11 (P2) | AC1's envelope inequality over-claimed | **Accepted.** Restated as per-attempt + explicit envelope disclosure. |

## Cycle-5 changelog (cycle-4 findings — all P2, none design-resting)
| # | Finding (cycle 4) | Disposition |
|---|---|---|
| 1 | AC7's "60s" list incomplete | **Accepted.** Full site list + `rg` check (pattern extended for `(60)`). |
| 2 | AC6 misses `answer-surface.md:198-199` and `quota.py`'s comment | **Accepted.** Both added. |
| 3 | `ASK_RETRY_TOTAL_BUDGET_S` is not a total budget | **Accepted.** Renamed `ASK_RETRY_DEADLINE_S`; sleeps clamped; envelope disclosed. |
| 4 | Deadline-stop exception unspecified | **Accepted.** Both stop paths re-raise the caught exception; AC3(d). |
| 5 | Off-by-one `ASK_RETRY_ATTEMPTS` vs `retries` | **Accepted.** Call site pinned to `- 1`. |
| 6 | `delay_for` bypasses `cap` | **Accepted.** Floor clamped; cap semantics stated exactly. |
| 7 | Client-fired timeout non-retry case missing | **Accepted.** AC3(c). |
| 8 | Retry pin sites lack in-place markers | **Accepted.** `:41/:73/:229` + a second `rg` check. |
| 9 | "99.8 %" not derivable | **Accepted.** Recomputed exact counts. |
| 10 | Message hardcodes "10s"; OVERRIDES text unpinned | **Accepted.** Both fixed. |

## Cycle-6 changelog (cycle-5 findings)
| # | Finding (cycle 5) | Disposition |
|---|---|---|
| 1 (P1) | C.1 re-asserted the withdrawn "already exceeds the 15 s budget" claim | **Accepted.** Replaced with the honest form: the sole >10 s ask was a **successful slow ask**, inside the caller's budget; Option C spends it to buy the bounded wait + advertised retry. No 15 s abandonment claim remains anywhere in the artifact. |
| 2 (P2) | AC7 missed the fourth retry-pin site (`:496`, changelog row 74); its check could not surface retry sites | **Accepted.** Site added; a second `rg -n "auto.retry"` check added. |
| 3 (P2) | AC6's check pattern missed `(60)` (the `sdk.py:53` form) and was over-inclusive | **Accepted.** Pattern extended to `\(60\)`; the check scoped to enumerated ask-lane lines; unrelated hits named as exclusions. |
| 4 (P2) | The cap bounds only the floor (sleep ≤ 2×cap) | **Accepted.** Stated exactly in C.3. |
| — | Quantile-convention mismatch across the two evidence rows | **Accepted.** Noted in the evidence caveat. |

## Adversarial Threat Surface
(none — declared `(not adversarial)`; no fail-open gate is introduced or modified.)

## Cycle-7 changelog (plan-stage findings that required a scope amendment)
| # | Finding | Disposition |
|---|---|---|
| 1 (P1, both plan reviewers) | AC4's original "a 504-then-retry can emit one record per attempt" is **not expressible** on the named surfaces — the retry lives in the SDK *client* (`_post_ask`, exercised against a fake server that never touches `hosted_api`), while the emission lives in the hosted *server* route, which has no client retry leg; a single hosted request can never emit two rows | **Accepted.** AC4 rewritten as the honest composition (N client attempts × one row per committing request, each separately tested). |

## Cycle-8 changelog (plan-stage findings that required a second scope amendment)
| # | Finding | Disposition |
|---|---|---|
| 1 (P1, both plan reviewers) | AC4(a-i) framed the off-loop proof around "an awaited inline seam" without naming one; the plan's first candidate (`ha_mod.run_ask_bounded`) **does not exist** — `hosted_api.ask_question` imports `run_ask_bounded` *inside the function body* (`hosted_api.py:5271-5276`), so there is no module-level binding to patch (`raising=True` → `AttributeError`; `raising=False` → silent no-op, making the assertion vacuous) | **Accepted.** AC4(a-i) now states the property (off the loop) rather than the seam; the plan names the working seam (`quota_mod.run_ask_bounded`) plus the precedent's "the probe actually ran" guard. |
| 2 (P2, reviewer #2) | Thread identity proves *off the loop*, not *non-blocking* — an awaited `to_thread`/`run_in_executor` satisfies it while still gating the response on a blocking write, so AC4(a) as written did not guard the property the Risks section claimed | **Accepted.** AC4(a) split into (a-i) off-loop by thread identity and (a-ii) non-blocking by a bounded guaranteed-release probe. |
