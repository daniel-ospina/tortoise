---
title: "Ask Answer Surface (#1987)"
type: operations
domain: operations
doc_status: live
created: 2026-08-29
ownedBy: epistemic-team
aboutSubjects: tortoise-memory
aboutObjects: tortoise-ask, tortoise-search
---

# Ask Answer Surface (#1987)

> ⛔ **EVAL-ONLY (#3849, owner-directed).** The ask lane is NOT a product
> surface: there is **no `/v1/ask` REST route** (hosted or self-host), **no
> SDK `ask()`/`ask_assembled()` method**, and **no MCP ask tool** — removal,
> not a gated dormant surface. It survives as the eval-only entry point
> `tortoise/ask_lane.py`, driven by the LongMemEval A/B assembly arm
> (`tests/longmem_eval/test_assembly_arm.py`) and `tools/ask_spotcheck.py`.
>
> The READER still ships (`tortoise/reader.py`): it is the eval's reader, and
> the 500-Q LongMemEval benchmark measures it directly; the eval re-exports
> the product reader so prompt drift is impossible by construction (0.83
> accuracy on the integrity-valid run).
>
> The pipeline below describes that eval lane (behaviour unchanged). The
> removed product surface — the `TORTOISE_ENABLE_ASK` gate, the per-team ask
> budget/metering, and the `_post_ask` hosted client — is gone.

## Surfaces

| Surface | Where | Notes |
|---|---|---|
| `tortoise/ask_lane.py` (eval-only lane) | `tortoise/ask_lane.py` | **The ONLY home of the ask pipeline (#3849).** `run_ask_lane(sdk, …)` (13-field response) + `run_ask_assembled(sdk, …)` (connected assembly). Local graph only — hosted client mode (`TORTOISE_API_URL`) raises. Callers: eval/test code only — the LongMemEval A/B arm, `tools/ask_spotcheck.py` and the lane's own suites |
| `POST /v1/ask` (hosted) | — | **REMOVED (#3849)** — no route, no handler, no path-scoped error translation |
| `TortoiseSDK.ask()` / `.ask_assembled()` | — | **REMOVED (#3849)** — no ask ENTRY POINT: the names do not resolve. The ask-path helpers the lane calls (`annotate_ask_hits`, the A1/A4 retrieval knobs) remain in `tortoise/sdk.py` |
| MCP ask tool | — | **REMOVED (#3849)** — no registry entry, so absent from `tools/list` and `tools/call`; the `"ask"` curation group is gone |
| `POST /v1/ask` (self-host REST) | — | **REMOVED (#3849)** alongside the hosted route |
| `GET /v1/team` ask-usage | `tortoise/hosted_api.py` | `ask_calls/ask_tokens_in/ask_tokens_out/ask_cost_usd` for the current period; zeros for fresh teams (telemetry-follow-up scope) |

**both-not-either:** `tortoise_search` / `GET /v1/search` / `tortoise_recall`
stay LLM-free and unmetered; the eval-only ask lane is never the implicit
answer path for search.

## Question schema (eval-only lane)

> **Status codes in this section are the retired wire framing.** The eval-only
> lane is a Python entry point and never returns an HTTP status — it raises
> `AskValidationError` carrying the same code as `.code`.

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

## Response schema (13 fields)

| Field | Meaning |
|---|---|
| `answer` | The reader text; `NO_EVIDENCE_TEXT` ("The memory context does not contain the information needed to answer this question.") when abstained |
| `abstained` | **Best-effort heuristic label** (phrase list over the answer text) — NOT the abstention guarantee; the two-phase prompt is authoritative |
| `question_type` | The detected or caller-override type; None possible |
| `question_date` | The resolved value (see above) |
| `evidence` | The assembled context the reader saw (trust property as a response field) — bounded by BOTH the resolved token cap (default 16 000 estimated tokens, #4105) AND the resolved `TORTOISE_ASK_CONTEXT_BYTE_CAP` byte bound (default derived as `max(32768, token_cap × 8)` = 128 000 bytes), ENFORCED AT ASSEMBLY (whole-hit drop; never splits a character) |
| `context_tokens` | `estimate_tokens_ask(rendered_context)` — a conservative ESTIMATE, not a raw count; bounded by the resolved `TORTOISE_ASK_CONTEXT_TOKEN_CAP` (default 16 000, #4105) |
| `model` | The RESOLVED spec (the serving lane's wire id — bare `deepseek-v4-flash` on the direct lane, the full spec on OpenRouter) |
| `provider` / `route` | The lane that actually served — a FAILOVER answer reports the SURVIVING lane; recovery reports the primary lane again |
| `cost_estimate_usd` | An ESTIMATE at the ×1.5 over-cover rate (see Cost) — never an exact bill |
| `duration_ms` | Wall-clock: `run_ask_lane` entry → response |
| `retrieval_degraded` | True when any retrieval leg degraded or D8 decoration was unavailable → the lane answers on the degraded evidence; a raised retrieval/annotation failure → `AskRetrievalUnavailable` (`.code = retrieval_unavailable`) |
| `retrieved_session_ids` | The DISTINCT session ids DERIVED for the assembled `evidence`, in the order the evidence presents them (the legacy lane's own post-dedup ranking order — post-boost, and when enabled post-rerank (A7) / post-package (A8) — **not** raw RRF once `apply_evidence_boost` or the A7 rerank reorders the pool; the subject-major `post_cap_lines` order of the connected-assembly fired branch) — built from the same hit list the evidence was rendered from, never inferred from an id's shape. Derived means read from an explicit identity only: an explicit `session_id` already on the hit (the `annotate_ask_hits` Event join — which also supplies a Point's own camel `sessionId` prop whenever the Event arm yields no `sessionId`: no matching Event, or a matching Event without the prop), else the hit's `sessionId` (the Point's own prop, else the `:Session` `CONTAINS` edge). A hit whose identity cannot be derived contributes nothing, so `[]` is honest. Independently, the value is sanitized: anything outside `[A-Za-z0-9._:@+\-]{1,128}` after stripping surrounding whitespace is **dropped** on both surfaces — reported as unknown whenever no other safe identity source exists for that hit (a safe sibling source is used instead). That is a sanitizer rule, not a divergence. This sanitizer covers the **session id only** — `session_date` and `speaker` are interpolated into the same annotation zone unsanitized (a pre-existing, separately-tracked gap: #3844). **The field is NOT a mirror of the tags — only the honest set of identities the retrieved hits carry.** The two should be read together, because a hit carrying the eval/assembly lanes' `lme_session_index` keeps its historical tag (byte-identical to the pre-change expression per the R17 assembly goldens) regardless of the id it names, in both directions: (a) a RENDERING index (`>= 0`) shows `[session <index>]`, so the field can be empty while the evidence names sessions, or can name a session the reader was never shown — the D3 defect (the evidence does not name the true session) still stands for such rows, because the index wins the tag; (b) a NON-RENDERING index (the eval lane's `-1` sentinel, or an explicit `None` — the connected-assembly spine's spelling) shows `[session ?]` while the field still names the id. Consequence for the whole-hit caps — BYTE cap only: the derived tag is part of the byte accounting (`assemble_context` accounts `len(_render_block(h).encode())`), so `[session <uuid>]` is ~35 B wider than `[session ?]` and a pool already at the resolved byte ceiling can admit **fewer** hits than a pool below it (measured at the pre-#4105 production cap — 40 items / 8K tokens / 32 KiB — 40 → 38 of the same 40-hit pool; the resolved default byte ceiling is 128 000 bytes, derived from 16 000 tokens, so the span is wider and the shrink is not expected at the default). The token cap is unaffected — it accounts `len(block.split())` plus the non-ASCII surcharge, and the ASCII tag replaces one word with one word, so it adds bytes, not whitespace words. The post-cap byte outcome is the accepted price of naming the session. |

`/v1/search`'s `sessionId` is populated from the Point's `sessionId` prop, else the `:Session` `CONTAINS` edge, and passes through the same sanitizer (as does the `entity_type='document'` branch of the same agent-consumed MCP/`tortoise_search` path). Because the ask lane additionally prefers an explicit `session_id`, the two surfaces can name a different (both graph-derived) session for the same point. The **self-host** `/v1/search` response (`selfhost_api.PointResponse` = `id`/`content`/`kind`/`created_at`) does not carry a session id — a deliberate pre-existing narrow contract, out of scope for this change.

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
- **No production 429 path exists any more** — the 60/min ask budget retired
  with the product surface (#3849; the machinery's purge is tracked as §7
  D5), so `quota_exceeded`/`in_flight_limit` have no raiser. The abstention
  census is the graded `_abs` eval run only; `abstained` labels never pollute
  it.
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
  (cost-bounded by the resolved caps, default 200/200/16000/derived).

## Retrieval (ask lane — #2070 optimisation loop)

The ask lane retrieves evidence with an optimisation-loop design: each lever
is env-gated and measured against the baseline in `tools/ask_recall_bench.py`
(baseline → lever → measure → keep/revert). All knobs default to the
search-lane byte-identical posture unless the runbook's follow-up (2)
measurement justifies a change.

- **A1 `TORTOISE_ASK_NUMERIC_TOKENS` (default 1):** keep all-digit tokens
  (money/quantity) in the ask lane's sparse query — SAME-VALUE money
  questions become retrievable. The search lane never passes it (numeric
  tokens still dropped there). Honest scope: a question with NO numeric
  tokens (e.g. the #2070 `gpt4_d84a3211` sum question) is served by the
  vector leg, not this knob.
- **A4 `TORTOISE_ASK_SEARCH_KEYS_PRF` (default 1):** additive `search_keys`
  pseudo-relevance-feedback expansion — a bounded second FTS pass whose
  OR-union reserves the original query's token slots and appends aliases
  from the retrieved pool's top-5 hits (max 12+8 terms). Never replaces
  original tokens (the regression guard).
- **A5 `TORTOISE_ASK_EVIDENCE_BOOST` (default 1):** evidence-mark boost
  before assembly (stored `has_answer` marks). Product graphs currently
  carry zero marks (the extractor does not write them) — zero marks = a
  no-op, byte-identical order. Fixture/bench seeding writes marks so the
  lever is measurable.
- **A3 `TORTOISE_ASK_FUSION_WEIGHTS` / `TORTOISE_ASK_FUSION_K`:** RRF
  weights/damping overrides. Default None/60 = the shared global
  (`TORTOISE_FUSION_WEIGHTS` → `{"vector": 1.5}`) unchanged.
- **A6 `TORTOISE_ASK_RETRIEVAL_LIMIT` / `_CONTEXT_ITEM_CAP` /
  `_CONTEXT_TOKEN_CAP` / `_CONTEXT_BYTE_CAP` / `_POOL_SIZE` (default
  200/200/16000/derived/200, #4105):** the retrieval-window limit (the
  `result_ids[:limit]` cut inside `tortoise_fts_query`) is threaded IN
  TANDEM with the assembly caps AND the pool floor — `limit >= item_cap`
  and `pool_size >= limit` always hold, so raising one can never be
  silently half-applied. The BYTE ceiling is resolved, not a literal: when
  `_CONTEXT_BYTE_CAP` is unset it is DERIVED from the token cap
  (`max(32768, token_cap × 8)`), so a token raise cannot be neutralised by
  a fixed 32 KiB ceiling; `assemble_context` reports which bound dropped
  hits and the lane warns when bytes are the binding constraint.
  Defaults raised from 40/120/40/8000/32 KiB to the values measured on the
  frozen D3 fixture (#4105).
- **A7 `TORTOISE_ASK_RERANK` (default OFF):** gated phase-2 eval-lane
  cross-encoder rerank (eval R6 port, `tortoise/rerank.py` — the ONE
  implementation, re-exported by the eval lane). Truthy-only; needs the
  `embeddings` extra. Degrades to untouched on any scorer failure (never
  raises). Context/token budget guard (#2976): the measured lever costs
  ~6.6× context, so a reranked set over the SAME resolved token / byte caps
  `assemble_context` enforces is refused WHOLE — the pool degrades to the
  unreranked order with a declared `reranked-set-exceeds-context-budget`
  reason (logged), never a silent truncation of the reranked set.
  Pre-packaging note: the guard runs before the A8 evidence package, which
  can only shrink the pool — so the guard is deliberately conservative (it
  may over-refuse, never under-refuse).
- **Vector leg (A2):** a documented runtime requirement for ask quality —
  the lexical-trio retrieval class needs the `embeddings` extra. NEVER
  enforced: a degraded lane keeps `retrieval_degraded=true` honestly (no
  silent success).

## Cost & budget

- **Per-query cost ≤ $0.01 target is structural:** 16 000-token context cap +
  200-item cap + 500-token output cap (the 60/min/team LLM budget was
  retired with the product surface in #3849 — see *Budget* below).
  Worst case ~$0.0034/query at the over-covered rate (≈3× under target;
  the pre-#4105 8000-token cap ran ~$0.0014–0.0023, 5–7× under).
- **Rates:** `ASK_METER_RATES = {"prompt_per_1m": 0.21, "completion_per_1m":
  0.42}` — verified deepseek-direct $0.14/$0.28 × a documented ×1.5 safety
  factor (covers the OpenRouter fallback markup). The meter over-covers.
- **#2069 — strong-lane rates (spec-aware selection):** the ask lane meters
  at the SERVING wire id's family: a family-prefixed strong-family spec
  (`qwen/qwen3.8-max`, `upstage/solar-pro4`, `anthropic/claude-opus-5` —
  OpenRouter-only families) → `ASK_METER_RATES_STRONG = {"prompt_per_1m":
  3.00, "completion_per_1m": 9.00}` (verified qwen3.8-max $2.00/$6.00 × the
  same ×1.5 over-cover); everything else (bare ids, `deepseek/*` — incl. a
  deepseek spec forced to openrouter via `TORTOISE_ASK_PROVIDER`) stays on
  the default envelope. Both `estimate_ask_cost_usd` call sites in
  `tortoise/ask_lane.py` (`run_ask_lane`: the metering record + the response
  `cost_estimate_usd`) select
  via `select_ask_meter_rates(model.model)` — a strong-lane query never
  under-counts at the deepseek envelope (~10× under-count pre-fix).
- **#2069 — the strong lane BREAKS the $0.01/query structural target:**
  metered worst ~$0.032 (9.2k in + 500 out at STRONG {3.00, 9.00}), real
  qwen rates worst ~$0.021; typical ~$0.012 at STRONG. Recorded owner
  decision (pending, runbook §#2069): tighten the strong lane's context
  cap, exploit OpenRouter's $0.25/M cache-read, or re-baseline the target
  for the strong lane. At the then-live 60/min budget the dollar blast
  radius would have grown from ~$0.14 to ~$1.28/min/team worst case; that
  budget retired with the product surface (#3849 — see Budget below), so the
  exposure is now bounded only by the eval lane's own call volume.
- **Budget (RETIRED with the product surface, #3849):** `MAX_ASK_LLM_PER_MIN
  = 60` per team, per process, the per-team in-flight cap 4, and the global
  Semaphore(8) + 60s bound all lived on the hosted `/v1/ask` and MCP ask
  handlers, which no longer exist. `quota.run_ask_bounded` and the budget
  helpers now have no PRODUCT caller (the only caller left of
  `run_ask_bounded` is `tests/test_quota.py`, which pins its exec-floor
  guarantee), so no product path can emit 429 `quota_exceeded` /
  `in_flight_limit` or 504 `timeout`. The eval-only lane
  (`ask_lane.run_ask_lane`) is unbudgeted. The orphaned
  `quota.py`/`metering.py` ask cluster is retained pending the #3849 §7 D5
  purge follow-up.
- **Metering (retained mechanism, no PRODUCT caller passes an org):** per-query
  record via `record_ask_usage` (best-effort, non-fatal — metering failures
  never block the answer). The single call site is now
  `ask_lane.run_ask_lane` (the SDK held it before #3849); the lane's real
  callers (`tools/ask_spotcheck.py`, the LongMemEval assembly arm) pass no
  `org_id`, so nothing is metered on a real run — `tests/test_ask_sdk.py`
  drives the record path with an explicit `org_id` to pin it. Zero records when
  the reader/retrieval call FAILS. Selfhost records nothing — the
  transport-keyed `_selfhost_transport` exemption, never a value-keyed
  "selfhost" check (a hosted team literally named "selfhost" was
  record-and-budget-charged before #3849; no product caller meters today).

  ⚠️ **The window-resolution raise is a SIGNAL, not a refusal** (#3825/#3981).
  `record_ask_usage` RAISES `QuotaCheckError` when an org's metering window is
  unresolvable (it will not key the row to a calendar month). That raise does
  NOT reach the user as a refusal: the answer has already been produced, the
  caller absorbs the raise, **serves the answer anyway**, and reports the
  dropped increment to the operator (ERROR, `lane=ask_ledger`). A bookkeeping
  fault of ours never becomes a user-facing 500. The user-facing refusal —
  where one exists — is the pre-spend admission gate (the armed cohort cost
  cap), which runs *before* any spend; a raise downstream of a completed
  operation cannot refuse it. Do not read "raises" here as "enforced".

## Error vocabulary

The canonical codes below are the ask vocabulary, defined in
`tortoise/exceptions.py`. Only the rows marked *(lane)* can be raised by the
eval-only lane (`tortoise/ask_lane.py`) and carried as `.code`; the rows
marked *(RETIRED)* belonged to the removed product surfaces and have no
raiser left. No HTTP body ships any of them any more: the `/v1/ask` route
and its path-scoped 400/429/502/504 translation were removed in #3849.

| Status | Code | Meaning |
|---|---|---|
| 400 | `invalid_question` | *(lane)* empty/whitespace/missing/wrong-type/control-char question |
| 400 | `question_too_long` | *(lane)* > 2000 chars |
| 400 | `invalid_question_type` | *(lane)* unknown question_type (valid list included) |
| 400 | `invalid_question_date` | *(lane)* malformed/calendar-impossible date |
| 401 | `unauthorized` | *(RETIRED)* removed HTTP auth path — no raiser |
| 429 | `quota_exceeded` | *(RETIRED)* retired ask budget — no raiser |
| 429 | `in_flight_limit` | *(RETIRED)* retired per-team in-flight cap — no raiser |
| 502 | `reader_unavailable` | *(lane)* LLM reader failed with no surviving lane |
| 502 | `retrieval_unavailable` | *(lane)* retrieval/annotation/context assembly failed wholesale |
| 504 | `timeout` | *(RETIRED)* retired bounded section — no raiser |

## Hosted delegation (removed)

Earlier revisions mapped hosted `/v1/ask` statuses to typed exceptions in a
`_post_ask` client. That path is **removed (#3849)**: the eval-only lane
requires a local graph, and `run_ask_lane` raises `AskRetrievalUnavailable`
when `TORTOISE_API_URL` is set. The ask lane's auto-retry-v1 follow-up is
closed with it (the underlying `tortoise/retry.py` SDK write-path wiring
remains open — see `docs/parity/2026-08-29-retrieval-inversion.md`).

## Notes

- **Connected-assembly branch (#2165 Task 6, `TORTOISE_ASK_CONNECTED_ASSEMBLY`, default OFF):** the `run_ask_lane()` local lane slots a deterministic pre-retrieval branch AFTER validation. Flag ON + a routed shape (current-state / ordering / interval) + BOTH subject halves resolved → the evidence is ASSEMBLED from typed slices (state header `STATE (couch): superseded by sofa on 2026-09-01` + chronological dated spine) instead of the legacy FTS pool; flag OFF / unrouted / unresolved → legacy byte-identical by construction. `ask_lane.run_ask_assembled()` exposes the same pipeline in PURE-ASSEMBLY mode (no reader; the eval arm reads `post_cap_lines` for gold-id admission) or with a `_reader_factory`. The fired path passes `[]` to the D8-decoration-unavailable gate → a fired render NEVER reports `retrieval_degraded` (R11). Response shape is `retrieved_session_ids`-inclusive (13 fields).
- **Real-lane caveats (documented, R12/R17 DA P2-4):** the assembled evidence trusts write-side state: (1) the supersession fold's `supersededAt` is authoritative for state headers but object-level evidence carries neutral-0.5 EP until write-side EP lands; (2) sparse extractor `when` (D3) makes many rows tier-created/undated — chronological spines reflect `createdAt`/`startedAt` where `when` is absent; (3) alias cold-start — the resolver's alias leg needs anchored `search_keys` on the Object (high/FTS legs are alias-independent). Byte-golden tests pin the fixture's dated rows; real graphs with wall-clock timestamps should set `question_date` explicitly for deterministic as-of windows.
- **JSON-mode pin:** the ask lane pins `json_mode=False` STRUCTURALLY (the
  `_should_send_json_mode` content heuristic would fire on "json" inside
  user-controlled retrieved memory and mangle free-text answers); the
  extraction lane is unchanged (default None).
- **Standalone context (`GET /v1/context`, G7)** is the documented follow-up
  — `evidence` in the ask response delivers the trust property today.
- **Tier-based ask budgets were OUT of v1** — the budget was per-team-flat
  (60/min). RETIRED with the product surface in #3849 — see *Budget* above.
- **Abstention measurement:** the graded `_abs` eval run is the census
  authority (product `abstained` label count + the judge-marker subset
  both reported); see `docs/runbook/1987-ask-abstention-check.md`.
