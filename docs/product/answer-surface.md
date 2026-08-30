---
title: "Ask Answer Surface (#1987)"
type: operations
domain: operations
doc_status: live
created: 2026-08-29
ownedBy: epistemic-team
---

# Ask Answer Surface (#1987)

> The product answer surface: an LLM reader that ANSWERS questions about
> captured memory — one bounded retrieve-then-read pass, built from the
> LongMemEval-benchmarked two-phase reader (0.83 accuracy on the
> integrity-valid run; the eval now re-exports the product reader so prompt
> drift is impossible by construction).
>
> ⛔ **#2013 PRODUCT-GATING (2026-08-30): the HOSTED ask EXPOSURE is OFF by
> default.** The READER ships (it is the eval's reader — the 500-Q
> LongMemEval benchmark runs through it); the customer-facing ask exposure
> is gated until the reader-model decision is made (the benchmark will use
> a strong reader model). No `/v1/ask` route and no `tortoise_ask` in the
> default hosted MCP tool group unless `TORTOISE_ENABLE_ASK=1` (tests/dev).
> The capability stays in the codebase, tested, ready.

## Surfaces

| Surface | Where | Notes |
|---|---|---|
| `POST /v1/ask` (hosted) | `tortoise/hosted_api.py` | **GATED (#2013): not registered unless `TORTOISE_ENABLE_ASK=1` — 404 by default.** Team-scoped (tt_ key or session JWT), budgeted (60/min/team → 429 + Retry-After), metered per-query |
| `TortoiseSDK.ask()` | `tortoise/sdk.py` | **GATED/EXPERIMENTAL (#2013): the eval's reader path — stays shipped, not for production use until the reader-model decision.** Local lane (embedded/selfhost, BYOK) — full pipeline, no hosted API; hosted mode when `TORTOISE_API_URL` is set |
| MCP `tortoise_ask` | `tortoise/mcp_server.py` | **GATED (#2013): own curation group `"ask"`, excluded from the default hosted /mcp surface unless `TORTOISE_ENABLE_ASK=1`; an explicit `tool_group="ask"` server (dev/eval) serves it.** Read-classified; same budget bounds. The selfhost /mcp DEFAULT surface is gated identically (list hidden + call ERR_EXCLUDED); selfhost MCP opt-in is `tool_group="ask"` (`TORTOISE_TOOL_GROUP=ask`), while selfhost REST /v1/ask stays unmetered |
| `POST /v1/ask` (self-host REST) | `tortoise/selfhost_api.py` | Stays — the local-lane REST parity surface. Static-key auth; local lane; NO team budget; unmetered (zero records) |
| `GET /v1/team` ask-usage | `tortoise/hosted_api.py` | `ask_calls/ask_tokens_in/ask_tokens_out/ask_cost_usd` for the current period; zeros for fresh teams |

**both-not-either:** `tortoise_search` / `GET /v1/search` / `tortoise_recall`
stay LLM-free and unmetered; `/v1/ask` is the distinct metered answer
surface — never the implicit answer path for search.

## Request schema (`AskRequest`)

```json
{ "question": "what did we decide about the API?",   // required, 1..2000 chars
  "question_type": "temporal-reasoning",             // optional closed enum
  "question_date": "2026-08-29" }                    // optional YYYY-MM-DD
```

- **`question`** — required; empty/whitespace → 400; max length **2000 chars**
  (`question_too_long` at 2001); control chars (U+0000–U+001F) or
  zero-width-only text (U+200B/U+00A0) rejected at ANY position
  (`invalid_question`). Validate-then-reject — no sanitize-then-send.
- **`question_type`** — closed enum: `temporal-reasoning`,
  `knowledge-update`, `multi-session`, `single-session-preference`, or
  omitted (None → the generic baseline prompt). Anything else → 400
  `invalid_question_type` with the valid list. When omitted, the
  deterministic `detect_question_type` (ordered precedence
  TR→KU→MS→SSP→None) picks the fragment.
- **`question_date`** — `YYYY-MM-DD` with a real-calendar check (month
  00/13, day 00/30/31-vs-month, non-leap Feb 29 → 400
  `invalid_question_date`). Future dates accepted — **no time-travel v1**.
  Default: **server-now-UTC** `YYYY-MM-DD` (computed at request time; feeds
  `render_context` date markers + the KU fragment's point-in-time
  resolution; the pool stays the live graph). The response's
  `question_date` is ALWAYS the RESOLVED value — the server-now-UTC default
  when omitted, the caller override when provided.

## Response schema (12 fields)

| Field | Meaning |
|---|---|
| `answer` | The reader text; `NO_EVIDENCE_TEXT` ("The memory context does not contain the information needed to answer this question.") when abstained |
| `abstained` | **Best-effort heuristic label** (phrase list over the answer text) — NOT the abstention guarantee; the two-phase prompt is authoritative |
| `question_type` | The detected or caller-override type; None possible |
| `question_date` | The resolved value (see above) |
| `evidence` | The assembled context the reader saw (trust property as a response field) — **32 KiB byte bound**, `len(evidence.encode("utf-8")) ≤ 32768`, ENFORCED AT ASSEMBLY (whole-hit drop; never splits a character); ≤ 8000 estimated tokens |
| `context_tokens` | `estimate_tokens_ask(rendered_context)` — a conservative ESTIMATE, not a raw count; ≤ 8000 |
| `model` | The RESOLVED spec (the serving lane's wire id — bare `deepseek-v4-flash` on the direct lane, the full spec on OpenRouter) |
| `provider` / `route` | The lane that actually served — a FAILOVER answer reports the SURVIVING lane; recovery reports the primary lane again |
| `cost_estimate_usd` | An ESTIMATE at the ×1.5 over-cover rate (see Cost) — never an exact bill |
| `duration_ms` | Wall-clock: local lane = `ask()` entry → response; hosted HTTP = request receipt → response; hosted MCP = in-process local-lane wall-clock (the MCP handler runs the SDK local lane in-process). Reader-only breakdown deferred |
| `retrieval_degraded` | True when any retrieval leg degraded or D8 decoration was unavailable → 200 success with degraded evidence, **metered as success**; raised retrieval/annotation failure → 502 `retrieval_unavailable` |

`evidence` is present in ALL successful responses INCLUDING abstained ones
(the caller-visible reason for the abstention).

## The abstention contract

- **v1 ships NO LLM-skip pre-gate.** Every ask is exactly ONE LLM call —
  including empty/decoy/near-miss context. Rationale: the vacuity finding —
  vacuous-retrieval questions are still answered correctly at high rates
  (legacy 0.889 / re-calibrated 0.778, both > 0.5) — so skipping the LLM on
  low-signal retrieval would kill correct answers.
- The **two-phase reader abstention** (presence-commit → abstain-on-genuine-
  absence, #1775) IS the no-evidence answer. `abstained` is best-effort
  heuristic sugar, NEVER a gate. **Do not market abstention as a trust
  guarantee before the Task 12 gate passes.**
- **429 is quota, explicitly NOT an abstention.** The abstention census is
  the graded `_abs` eval run only; production 429s/abstained labels never
  pollute it.
- The `abstained` heuristic has two error modes: a false positive (a
  confident answer whose phrasing hits the phrase list) and a false
  negative (a genuine abstention phrased off-list). Treat the label as
  advisory, paired with the answer text + `evidence`. The ONE deterministic
  case: blank/whitespace output → abstained with the canonical
  `NO_EVIDENCE_TEXT`. A structured abstention-reason field is the recorded
  follow-up making the label a real gate.
- **Documented follow-up (not v1):** an OPT-IN empty-pool-only skip (fires
  only when the deduped pool is empty), gated on an abstention-grading arm.
- **Superseded/terminal evidence:** the ask lane always retrieves with
  `include_terminal=True` — superseded points are included WITH their
  `[SUPERSEDED BY]` markers so the reader stays honest about staleness
  (cost-bounded by the same 8k/40 caps).

## Cost & budget

- **Per-query cost ≤ $0.01 target is structural:** 8000-token context cap +
  40-item cap + 500-token output cap + the 60/min/team budget. Worst case
  ~$0.0014–0.0023/query at the over-covered rate (5–7× under target).
- **Rates:** `ASK_METER_RATES = {"prompt_per_1m": 0.21, "completion_per_1m":
  0.42}` — verified deepseek-direct $0.14/$0.28 × a documented ×1.5 safety
  factor (covers the OpenRouter fallback markup). The meter over-covers.
- **Budget:** `MAX_ASK_LLM_PER_MIN = 60` per team, per process (a
  multi-worker uvicorn deployment scales the bound ×workers). Past budget →
  **429 `quota_exceeded` + Retry-After** (the window self-heals). Per-team
  in-flight cap 4 → 429 `in_flight_limit`. Global Semaphore(8) + 60s total
  per-request bound → 504 `timeout` (queueing counts against the clock).
- **Metering:** per-query record via `record_ask_usage` (best-effort,
  non-fatal — metering failures never block the answer). Recorded when the
  SDK call completes successfully (the single call site: the SDK local lane
  with an explicit `team_id`); zero records when the reader/retrieval call
  FAILS. Selfhost (HTTP MCP + REST + stdio) records nothing — the
  transport-keyed `_selfhost_transport` exemption, never a value-keyed
  "selfhost" check (a hosted team literally named "selfhost" records usage
  and is budget-charged).

## Error vocabulary (10 codes)

Standard non-leaking error body — `{"error": {"code": …, "retry_after": …}}`
with NO provider/model internals (#329 scrub). The canonical body ships
ONLY on `/v1/ask`; ALL other endpoints keep FastAPI's default
`{"detail": …}` — callers must NOT depend on a uniform body shape. The 403
suspended-team on `/v1/ask` passes through untranslated as the
`_suspended_detail()` dict — `{"detail": {"code": "SUSPENDED", …}}` (no
11th code; the SDK maps a code-less 403 via its status-derived fallback).

| Status | Code | Meaning |
|---|---|---|
| 400 | `invalid_question` | empty/whitespace/missing/wrong-type/control-char/malformed-JSON question |
| 400 | `question_too_long` | > 2000 chars |
| 400 | `invalid_question_type` | unknown question_type (valid list included) |
| 400 | `invalid_question_date` | malformed/calendar-impossible date |
| 401 | `unauthorized` | missing/invalid/expired key or session |
| 429 | `quota_exceeded` | per-minute ask budget spent (Retry-After present) |
| 429 | `in_flight_limit` | per-team in-flight cap (4) full (Retry-After omitted) |
| 502 | `reader_unavailable` | LLM reader failed with no surviving lane |
| 502 | `retrieval_unavailable` | retrieval/annotation/context assembly failed wholesale |
| 504 | `timeout` | bounded section exceeded 60s (queue or reader) |

## SDK mapping (`_post_ask`)

Hosted-mode `ask()` maps statuses to typed exceptions
(`tortoise/exceptions.py`, re-exported from `tortoise/schemas.py`):
429 → `AskQuotaExceeded` (retry_after) / `AskInFlightLimit`; 400/401/403/422
→ `AskValidationError` (code carried; code-less → status-derived default
400→`invalid_question`, 401/403→`unauthorized`); code-less 402 →
`AskReaderUnavailable` (server-side provider-billing condition); 502 →
`AskReaderUnavailable`/`AskRetrievalUnavailable`; 504 → `AskTimeout`
(`source` marks client-fired vs server-504-fired). No auto-retry v1
(`tortoise/retry.py` is the documented follow-up). The SDK-side timeout is
75s — strictly greater than the server's 60s — so the server's 504 is
always receivable.

## Notes

- **JSON-mode pin:** the ask lane pins `json_mode=False` STRUCTURALLY (the
  `_should_send_json_mode` content heuristic would fire on "json" inside
  user-controlled retrieved memory and mangle free-text answers); the
  extraction lane is unchanged (default None).
- **Standalone context (`GET /v1/context`, G7)** is the documented follow-up
  — `evidence` in the ask response delivers the trust property today.
- **Tier-based ask budgets are OUT of v1** — the budget is per-team-flat.
- **Abstention measurement:** the graded `_abs` eval run is the census
  authority (product `abstained` label count + the judge-marker subset
  both reported); see `docs/runbook/1987-ask-abstention-check.md`.
