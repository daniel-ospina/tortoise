---
title: "Plan — #1530 P2 provider routing: DeepSeek-direct primary + OpenRouter fallback"
type: plan
domain: capability
doc_status: planned
created: 2026-08-20
ownedBy: epistemic-team
governingAgreement: "#1530 (epic #1509, P2)"
---

# Plan — #1530 P2: Provider routing (DS-direct primary + OR fallback)

## Context

- **Issue:** #1530 — P2 provider routing. Epic #1509 (Extractor V3), contract: 03-scope P2 + 04-plan §6 (`TORTOISE_EXTRACTOR_PROVIDER = deepseek-direct | openrouter`; gate checks exactly what the adapter consumes; 401/402/403 fatal; no flip-flop) + 05-detailed-e2e E2E-8 failover variant.
- **Test alignment:** test-design #1515 surfaces 1 (DS direct), 2 (OR fallback), 21 (provider failover). Complexity: **complex**.
- **Dependencies:** P3 (rebase to origin/main + CI drift gate) is a global first dependency for all epic work; P1 (fail-closed capture) shares the `_extract_session_v2` return contract and the `extraction_mode` response field — see ⛔ Conditional Gate 1.
- **Role:** this plan is the **ERROR-CLASS TAXONOMY EXPORTER** — M2 (pre-flight) and M3 (retry/backoff) import the taxonomy from `tortoise/model_adapters.py`. The taxonomy contract is fixed here; M2/M3 must not fork it.
- **Research:** in-repo only, zero new third-party deps (`requests`/`urllib` already used). Research intake gate: Step A (prior research) only — the epic brief's Provider finding (02-research-brief §Provider + 00-scope item 6, owner-confirmed DS-direct production) covers the decision surface. No external queries.

## Current state (verified)

| Surface | Today | Problem |
|---|---|---|
| `tortoise/sdk.py::_model_adapter` (14143) | **OpenRouter-only**: hardcodes `OPENROUTER_API_KEY` + `OPENROUTER_BASE_URL`; sends family-prefixed id; `raise_for_status()` surfaces `requests.HTTPError` unclassified | No DS-direct route; no fallback; no error classes |
| `tortoise/sdk.py::_extract_session_v2` gate (1979) | Accepts `OPENROUTER_API_KEY` / `DEEPSEEK_API_KEY` / **`OPENAI_API_KEY`** | **Gate mismatch**: `OPENAI_API_KEY` opens the gate but `_model_adapter` cannot consume it → silent empty-key 401 at call time (the #1468 failure class, openai-only deploys) |
| `tortoise/sdk.py::_extract_session_v2` model build (1990–2004) | `_model_adapter(configured)` or `_model_adapter("deepseek/deepseek-v4-flash", max_tokens=None)` | No `TORTOISE_EXTRACTOR_PROVIDER` routing in production at all |
| `tortoise/sdk.py::capture_session` (1846) + `hosted_api.py::capture_session` (3548) | `_extract_session_v2` returns `list`; response hardcodes `"extraction_mode": "llm"` | Route not recorded; P1 needs errors + route surfaced |
| `tortoise/hosted_api.py::_llm_provider_available` (3401) | Broad (any `_LLM_PROVIDER_KEYS` key or mock seam) | Must STAY broad — pinned by `test_provider_key_parity_all_keys` / `test_sdk_and_hosted_availability_agree` (M2 path uses openai/gemini legitimately). Routing gate is a separate **inner** gate |
| `tests/model_adapters.py` | `DeepSeekDirectModel` + `OpenRouterModel` + `MODELS` (eval-proven; #1350) | Lives in `tests/` — production image cannot import it (#1468 guard `test_no_tests_imports_in_production.py`) |
| `tools/longmem_eval/run.py` (455–471) | Bespoke env selection: `TORTOISE_EXTRACTOR_PROVIDER=="openrouter"` → OR; `DEEPSEEK_API_KEY` → direct; else OR | Duplicated routing logic — must delegate to the production module |
| `tortoise/extractor_v2.py::_complete` (1566) | Thread + deadline, exceptions re-raised; no retry, no classification | M3 owns retry/backoff; this plan exports the classes it needs |

Pinned regression tests that constrain the design: `test_capture_session_v2_default_adapter_is_uncapped` and `test_capture_session_v2_extract_model_override_stays_capped` (monkeypatch `_model_adapter(model_id, max_tokens=4000, temperature=0.0)` and assert the exact call shapes), `TestModelAdapterBounds` in `tests/test_value_extractor.py` (calls `_model_adapter` with **no key set**, asserts body `model == "deepseek/deepseek-v4-flash"`), `test_provider_key_parity_all_keys`, `test_sdk_and_hosted_availability_agree`.

## Design decisions

### D1 — Port the eval adapters into a new production module `tortoise/model_adapters.py`

Port `OpenRouterModel` + `DeepSeekDirectModel` (keyword-only `complete(*, system, user)` contract, usage tracking) + a `MODELS` registry from `tests/model_adapters.py`, adding provider metadata:

- `OpenRouterModel.provider = "openrouter"`; base URL `https://openrouter.ai/api/v1/chat/completions`, key `OPENROUTER_API_KEY`.
- `DeepSeekDirectModel.provider = "deepseek-direct"`; base URL `https://api.deepseek.com/v1/chat/completions`, key `DEEPSEEK_API_KEY`.
- `tests/model_adapters.py` becomes a **re-export shim** (`from tortoise.model_adapters import ...`), so the eval harness (`tools/longmem_eval/run.py`, `tests/eval_harness.py`, `tests/e018_harness.py`, `tools/judge_harness.py`, `tools/probe_extractor.py`, `tools/experiments/extractor-v2/*`) keeps working unchanged. Production imports only `tortoise.model_adapters` — `test_no_tests_imports_in_production.py` stays green by construction.

### D2 — Routing contract

`TORTOISE_EXTRACTOR_PROVIDER` selects the **primary**; the other provider is the **fallback when its key is configured**. Resolution function `resolve_extractor_provider() -> (primary: str, fallback: str | None)`:

| `TORTOISE_EXTRACTOR_PROVIDER` | Key state | Primary | Fallback |
|---|---|---|---|
| `"deepseek-direct"` | DEEPSEEK set | deepseek-direct | openrouter (if OPENROUTER set) |
| `"openrouter"` | OPENROUTER set | openrouter | deepseek-direct (if DEEPSEEK set) |
| `"deepseek-direct"` | DEEPSEEK **absent** | — | **ValueError** (fail closed: explicit provider names a key that isn't set — never silently route elsewhere) |
| `"openrouter"` | OPENROUTER **absent** | — | **ValueError** (same) |
| unset | DEEPSEEK set | deepseek-direct | openrouter (if set) |
| unset | only OPENROUTER set | openrouter | None |
| unset | neither | — | None (caller gates fail closed, as today) |
| any other value | — | — | **ValueError** listing valid values |

Unset inference orders DEEPSEEK first — the owner-confirmed production decision (00-scope item 6: "DeepSeek direct = primary production extractor provider, OpenRouter = fallback").

### D3 — Gate checks exactly what the adapter consumes

- **Inner gate (`_extract_session_v2`):** requires the mock seam OR a routing-usable key: `DEEPSEEK_API_KEY` / `OPENROUTER_API_KEY` (via `resolve_extractor_provider()` returning non-None). **Remove `OPENAI_API_KEY` from this gate** — `_model_adapter` cannot consume it; accepting it is the #1468-style gate/consumer mismatch. Error message names the routing env + valid keys.
- **Outer gate (`hosted_api._llm_provider_available` / `_session_llm_provider`):** unchanged (broad). Pinned by the parity tests; the M2 seam (`TORTOISE_SESSION_EXTRACTOR=m2`) legitimately consumes openai/gemini via `OpenAICompatModel`.
- **Divergence handling (the #1468 lesson — outer/inner drift must not 500):** `hosted_api.capture_session` catches `ValueError` from `_extract_session_v2` and converts to **HTTP 503** with the gate message (clean fail-closed; never an uncaught 500).
- `_model_adapter` itself builds **leniently** (no-key default → OpenRouter, back-compat for `TestModelAdapterBounds` and direct callers); fail-closed is enforced at the pipeline gates, not the adapter constructor.

### D4 — Fatal-4xx guard: failover triggers only on the TRANSIENT class

`RoutingModel.complete()`: try primary; classify the exception:

- **FATAL (401/402/403)** → re-raise immediately. No retry, **no failover**. (E2E-8 owned negative.)
- **FATAL_CONFIG (400/404 + unknown 4xx)** → re-raise. Deterministic request/config errors; retrying/failover burns money without changing the outcome.
- **TRANSIENT (408/425/429/5xx/connection/timeout)** → fail over to the fallback for the remainder of this extraction (D5 stickiness). If no fallback configured → re-raise.
- **UNKNOWN** (non-HTTP, unclassified) → treated as TRANSIENT-safe (retry/failover permitted once; M3 may cap).

### D5 — No flip-flop

- **Per-extraction stickiness:** one `RoutingModel` instance per `extract_session_v2` call (the v2 pipeline already passes one model object through S1–S4). Once a call fails over, `last_route` flips to the fallback and **stays** there for the rest of that extraction — forward-only, never back mid-extraction.
- **In-process cooldown (flap guard):** module-level `{provider: last_failure_ts}` with a `threading.Lock`; a primary in cooldown is skipped (fallback used directly). `TORTOISE_EXTRACTOR_FAILOVER_COOLDOWN` seconds, **default 300**, `0` = disabled. This is the #1350 protection: under a sustained OpenRouter collapse the eval saw 476/500 connection failures — without a cooldown every extraction pays a dead-primary attempt first. Process-local by design (per-worker); cross-process coordination is out of scope (see Open Questions).

### D6 — Model-id normalization per route

Canonical id in config stays family-prefixed (`deepseek/deepseek-v4-flash` — `TORTOISE_EXTRACT_MODEL` default and the #1468 test pin it). Wire normalization: direct route sends the bare id (strip family prefix — `deepseek-v4-flash`, matching the eval's `DeepSeekDirectModel`); openrouter route sends unchanged.

### D7 — `_model_adapter` keeps its signature; routing is internal

`_model_adapter(model_id, max_tokens=4000, temperature=0.0)` returns a `RoutingModel`. All existing call sites (`_default_byok_model` → summary/construct paths at sdk 1631/1691/1706, `_extract_session_v2`, value_extractor tests) are untouched at the call level. Consequence: the summary/construct BYOK paths also route (DS-direct primary when `DEEPSEEK_API_KEY` is the configured/primary key) — one env, one adapter contract (see Open Questions 2). `max_tokens=None` keeps the uncapped semantics (#1468).

### D8 — Route + errors recorded on the capture response

- Adapter exposes `provider`, `route` (resolved endpoint identity), `last_route`, `failover_used`.
- `_extract_session_v2` return contract changes to `(extracted: list[dict], meta: dict)` where `meta = {"provider", "route", "failover_used", "errors": out["errors"], "warnings": out["warnings"]}`. Two internal call sites (sdk 1846, hosted 3548); no direct test calls it.
- Capture responses (SDK + hosted, parity by construction) gain `"extraction_mode": f"llm:{route}"` and `"extraction_provider": provider`. P1 owns surfacing `meta["errors"]` (non-200 or additive `warnings`) — shared contract fixed here (⛔ Gate 1).

### D9 — Eval delegates to production routing

`tools/longmem_eval/run.py` v2 model selection: `--extractor-model` explicit override stays (M5 pinning — the run pins the extractor model); the unset case delegates to the production router via the shim (single source of truth; removes the bespoke env logic). Reader/judge provider resolution (`OpenAICompatModel` via `_PROVIDERS`) is untouched — surfaces 3/4 are M2/M5's.

## Error-class taxonomy (the M2/M3 export contract)

Module: `tortoise/model_adapters.py`. **M2 and M3 import from here — do not fork.**

```python
class LlmErrorClass(enum.Enum):
    FATAL = "fatal"              # deterministic, permanent — abort, no retry, no failover
    FATAL_CONFIG = "fatal_config"  # request/config shape bug — abort, no retry, no failover
    TRANSIENT = "transient"      # rate/network/server — retry (M3), failover-eligible (P2)
    UNKNOWN = "unknown"          # unclassified — treated as TRANSIENT-safe

FATAL_STATUS_CODES = frozenset({401, 402, 403})
FATAL_CONFIG_STATUS_CODES = frozenset({400, 404})        # provider-independent request-shape errors
TRANSIENT_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

def classify_llm_error(exc: BaseException) -> LlmErrorClass: ...
def is_transient(exc: BaseException) -> bool: ...   # True → M3 may retry, P2 may fail over
def is_fatal(exc: BaseException) -> bool: ...       # True → M3 aborts immediately, P2 never fails over
```

Classification table (must be exhaustively unit-tested):

| Input | Class | Rationale |
|---|---|---|
| HTTP status 401 / 402 / 403 | `FATAL` | auth / billing / forbidden — permanent; pre-flight (M2) catches |
| HTTP status 400 / 404 | `FATAL_CONFIG` | malformed request / bad route or model id — retrying can't fix (OR 404 = bare route-shape, `_session_llm_model_shape_warning` precedent) |
| HTTP status 408 / 425 / 429 / 500 / 502 / 503 / 504 | `TRANSIENT` | rate limit / server — retry + failover eligible |
| Other 4xx (409, 422, …) | `FATAL_CONFIG` | unknown 4xx = deterministic client error — never retry ("no flip-flop on 4xx" rule) |
| Other 5xx | `TRANSIENT` | server-side, may clear |
| `requests.ConnectionError` / `requests.Timeout` / `urllib.error.URLError` / `socket.timeout` / network `OSError` | `TRANSIENT` | the #1350 collapse class (OR connection errors under load) |
| `TimeoutError` from `_complete`'s thread deadline | `TRANSIENT` | wall-clock timeout, may clear |
| Non-HTTP anything else (parse errors, KeyError on body) | `UNKNOWN` → TRANSIENT-safe | fail-safe: allow one retry/failover; M3 caps retry count |

Consumer wiring (owned by M2/M3 issues, contract fixed here):
- **M2 pre-flight:** pings each provider from `resolve_extractor_provider()`; probe failure classified — 402 → fatal with the billing-vs-cap message; uses `classify_llm_error`/`is_fatal`.
- **M3 retry/backoff:** `extractor_v2._complete` (and eval `_call_with_backoff`) gate retries on `is_transient(exc)`; FATAL/FATAL_CONFIG abort immediately — no backoff burn.

## Implementation steps

Ordered; each step independently verifiable.

### Step 1 — `tortoise/model_adapters.py` (new)
Port from `tests/model_adapters.py`:
- `OpenRouterModel` (provider="openrouter", OR URL + key; keyword-only `complete(*, system, user)`; `last_prompt_tokens`/`last_completion_tokens`/`last_cost` usage tracking).
- `DeepSeekDirectModel(OpenRouterModel)` (provider="deepseek-direct", DS URL + key; bare model-id normalization).
- `MODELS` registry (production copy: `deepseek-flash`, `deepseek-flash-direct`, `deepseek-v4-pro`, `deepseek-v4-pro-direct`, `deepseek-r1-xhigh`, `qwen3.8-max`, `claude-opus-5`, … matching the eval registry names).
- Taxonomy: `LlmErrorClass`, the three status-code frozensets, `classify_llm_error`, `is_transient`, `is_fatal`.
- `resolve_extractor_provider()` per D2; `TORTOISE_EXTRACTOR_PROVIDER` parsing + ValueError on invalid/missing-key.
- `RoutingModel(primary, fallback=None, *, cooldown_s=300)` per D4/D5 — thread-safe cooldown state, `last_route`, `failover_used`, `errors` list.
- `build_extractor_model(model_id=None, *, max_tokens=4000, temperature=0.0)` → `RoutingModel` (production entry).

### Step 2 — `tests/model_adapters.py` → re-export shim
Replace the class bodies with `from tortoise.model_adapters import OpenRouterModel, DeepSeekDirectModel, MODELS, ...` (keep module-level names + `OLLAMA_MODELS` unchanged). Eval harness smoke: `tests/eval_harness.py` / `tests/e018_harness.py` import it.

### Step 3 — `tortoise/sdk.py`
- Rewrite `_model_adapter` body: `RoutingModel` via `build_extractor_model(model_id, max_tokens=max_tokens, temperature=temperature)`. Signature unchanged. Lenient build (D3).
- `_extract_session_v2` gate: mock seam OR `resolve_extractor_provider() is not None` (DEEPSEEK/OPENROUTER only); remove `OPENAI_API_KEY`; message names `TORTOISE_EXTRACTOR_PROVIDER` + valid keys.
- `_extract_session_v2` build: unchanged call shapes (configured override capped 4000; default `"deepseek/deepseek-v4-flash"` uncapped) — both route internally now.
- Return `(extracted, meta)` with route + errors/warnings passthrough (D8).
- `capture_session` (sdk, ~1846): unpack tuple; response `"extraction_mode": f"llm:{meta['route']}"`, `"extraction_provider": meta["provider"]`.

### Step 4 — `tortoise/hosted_api.py`
- `capture_session` (~3548): unpack `(extracted, meta)`; wrap `_extract_session_v2` in `try/except ValueError` → HTTP 503 with the gate message (D3 divergence handling).
- Response: same `extraction_mode`/`extraction_provider` shape as the SDK path (parity).

### Step 5 — `tools/longmem_eval/run.py`
`--ingest-mode v2` model selection: keep `--extractor-model` explicit override (registry lookup, unchanged); unset case delegates to the production router (`build_extractor_model`) — delete the bespoke env branch. `extractor_model` becomes optional; `run_evaluation` unchanged.

### Step 6 — env docs
`.env.example` entries deferred by scope ("P4 adjacency; fold in later") — do NOT touch in this issue. The three vars (`TORTOISE_EXTRACTOR_PROVIDER`, `TORTOISE_EXTRACTOR_FAILOVER_COOLDOWN`; `TORTOISE_EXTRACT_MODEL` exists) are documented in the module docstring.

## Tests

### Unit — taxonomy (`tests/test_model_adapters_taxonomy.py`, new)
- Every status code in each frozenset → its class (parameterized).
- Unknown 4xx → `FATAL_CONFIG`; unknown 5xx → `TRANSIENT`.
- `requests.HTTPError` (status 401/402/403 → FATAL; 429 → TRANSIENT), `urllib.error.HTTPError`, `requests.ConnectionError`, `requests.Timeout`, `socket.timeout`, generic `Exception` → UNKNOWN, plain `TimeoutError` → TRANSIENT.
- `is_transient`/`is_fatal` boolean contract per class.

### Unit — routing (`tests/test_model_adapters_routing.py`, new)
- `resolve_extractor_provider` full D2 table (explicit, inferred, invalid value, key-absent fail-closed).
- Model-id normalization: direct sends bare id, openrouter sends family-prefixed (monkeypatched `requests.post`, assert body["model"] and URL).
- `RoutingModel`: primary raises ConnectionError → fallback called, `last_route` = fallback, `failover_used=True`; primary raises `HTTPError` 401 → exception propagates, **fallback never called**; sticky (second `complete` still on fallback); cooldown (primary skipped while in cooldown; `cooldown_s=0` disables).

### Integration — capture path (`tests/test_capture_session.py` additions + `tests/test_session_extraction_modes.py` additions)
- **Gate match:** `OPENAI_API_KEY` alone → `_extract_session_v2` raises ValueError with the routing message (was: gate passed). `DEEPSEEK_API_KEY` alone → direct adapter used (assert request URL). `TORTOISE_EXTRACTOR_PROVIDER=deepseek-direct` with only OPENROUTER key → ValueError.
- **Hosted 503 conversion:** openai-only key + v2 default extractor → HTTP 503 (clean fail-closed), not 500.
- **Route recording:** capture response has `extraction_mode == "llm:deepseek-direct"` (or `"llm:openrouter"` per env) + `extraction_provider`.
- **E2E-8 failover variant (integration):** primary adapter monkeypatched to raise `requests.ConnectionError` on first call → extraction succeeds via fallback, `extraction_mode` records the fallback route, `failover_used=True`; fatal-4xx negative: primary raises 401 → capture fails closed, no fallback attempt (owned negative); no-flip-flop negative: after failover, subsequent calls stay on fallback (no primary re-try mid-extraction).
- **Regression pins (must stay green):** `test_capture_session_v2_default_adapter_is_uncapped` (call shape + uncapped), `test_capture_session_v2_extract_model_override_stays_capped`, `TestModelAdapterBounds` (no-key lenient build, body model id), `test_provider_key_parity_all_keys`, `test_sdk_and_hosted_availability_agree`, `test_no_tests_imports_in_production`, `test_provider_availability`/`test_provider_availability_mock_seam`.

### Eval shim smoke
- `tests/eval_harness.py` / `tests/e018_harness.py` imports resolve via the shim; `tools/longmem_eval/run.py --ingest-mode v2 --mock` still starts (no network).

## Cross-lane interfaces

| Consumer | Contract consumed | Owned by |
|---|---|---|
| M2 pre-flight | `tortoise.model_adapters.classify_llm_error` / `is_fatal` + `resolve_extractor_provider()` for the per-provider ping list | M2 issue |
| M3 retry/backoff | `tortoise.model_adapters.is_transient` — retry gate in `extractor_v2._complete` + eval `_call_with_backoff`; FATAL aborts, no backoff burn | M3 issue |
| P1 fail-closed capture | `_extract_session_v2` → `(extracted, meta)`; `meta["errors"]`/`meta["warnings"]` surfacing; `extraction_mode` value format | P1 issue (coordinate — Gate 1) |
| P4 parity | Both capture paths updated here symmetrically (SDK + hosted `extraction_mode`/`extraction_provider`) — parity by construction | P2 (this plan) |
| E2E-8 failover variant | Integration test above (surfaces 18/21) | P2 (this plan) |
| Reader/judge (surfaces 3/4) | Unchanged — `OpenAICompatModel` via `_PROVIDERS` | M2/M5 |
| M2 seam | `TORTOISE_SESSION_EXTRACTOR=m2` untouched (openai/gemini still valid there) | — |

Ordering note: P2 and P1 both touch `_extract_session_v2` and the capture response. Land the shared `(extracted, meta)` contract in P2 first (or jointly); P1 then only consumes `meta["errors"]`/`["warnings"]` and finalizes error surfacing — no re-shape.

## ⛔ Conditional gates

1. **Capture-response contract change** (`extraction_mode: "llm"` → `"llm:<route>"` + new `extraction_provider` field): API-surface change — needs owner sign-off on the value format, and **must be coordinated with P1** (which owns truthfulness). Not a graph-schema change; response-body only.
2. **openai-only deployments:** after the gate match, a deployment with ONLY `OPENAI_API_KEY` can no longer run the v2 extractor (today: 503-gate passes → empty-key 401 at call time). Confirm no production deployment relies on openai-only v2 extraction. The M2 seam + `TORTOISE_SESSION_EXTRACTOR=m2` still supports openai.
3. **Both-keys deployments flip primary:** a deploy with both `DEEPSEEK_API_KEY` and `OPENROUTER_API_KEY` and no `TORTOISE_EXTRACTOR_PROVIDER` silently switches primary from OpenRouter to deepseek-direct. This IS the owner-confirmed decision (00-scope item 6) — flagged for migration impact, not a new decision.
4. **No ontology change** — verified: no new kinds/edges/point-properties. `extraction_mode`/`extraction_provider` are API response fields; the three env vars are additive and reversible (unset + single-key deploys behave as today). Architecture: one new module + routing inside the existing `_model_adapter`; reversible via env.
5. **Cooldown default (300s, process-local):** behavioral knob — confirms flap-protection latency trade-off. `0` disables. Confirm default with owner.
6. **`.env.example` deferred** per scope (P4 adjacency) — not touched here; documented in module docstring.

## Open questions

1. **Cooldown scope:** process-local per worker is fine for a single capture host, but multi-worker deployments each keep their own flap state. Acceptable for V3, or does the owner want a shared (DB/Redis) breaker? (Recommend: process-local now; shared breaker as a follow-up if the #1350 collapse recurs.)
2. **Summary/construct paths route too:** `_model_adapter` is shared — `execute_embed`/`construct_graph` BYOK calls (sdk 1631/1691/1706) also go DS-direct when `DEEPSEEK_API_KEY` is primary. Intended (one env, one adapter), but confirm the owner doesn't want routing scoped to the v2 extractor only.
3. **Eval delegation:** delete the bespoke env logic in `run.py` entirely (delegate to production routing), keeping only `--extractor-model` for M5 pinning — confirm the eval operator is fine losing the ability to force a provider via env independently of production config.
4. **Malformed-200 classification:** unparseable body on a 200 → `FATAL_CONFIG` (deterministic, don't retry) vs TRANSIENT. Recommended FATAL_CONFIG; M3 should confirm (its retry caps make either safe).
5. **P1 surfacing shape:** does P1 want `meta["errors"]` as additive `warnings` on the 200 response or a non-200 (4xx/5xx) extraction-failure status? This plan carries both through `meta`; P1 picks the wire form.
