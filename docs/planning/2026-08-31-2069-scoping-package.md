# Scoping Package — Issue #2069 (tortoise, daniel-ospina)

**Title (proposed):** fix(model-adapters): provider-capability-aware ask-lane routing so the strong reader (qwen3.8-max via OpenRouter) is servable on the product lane — + rate re-baseline + gated M5 pin flip
**Team:** epistemic-team · **Complexity:** complex · **Epic:** docs/plans/2026-08-29-1987-ask-reader.md · **Depends on:** #2013 (merged)
**Scoped by:** scoping-agent (double-diamond, full run) · **Date:** 2026-08-31 · **Nothing posted to GitHub.**

---

### Confirmed Problem

**The ask lane's reader-model selection is provider-blind: `build_reader_model` (tortoise/model_adapters.py:845-862) routes through `build_extractor_model`, which builds EVERY provider in the pool (`resolve_extractor_provider` → `_build_single`) with the SAME model spec — so a spec valid only on OpenRouter (`qwen/qwen3.8-max`) is posted to the deepseek-direct primary (`model: "qwen3.8-max"` at api.deepseek.com → HTTP 400), and 400 is correctly classified FATAL_CONFIG (fatal → re-raise, no failover, model_adapters.py:289/341/541), making the proven strong reader unservable on the product lane and the reader-model decision (M5 pin flip + cost re-measure) unmeasurable.**

Verified end-to-end in code (see Codebase Explorer Findings):
`TORTOISE_ASK_MODEL=qwen/qwen3.8-max` + `DEEPSEEK_API_KEY` (the production config; runbook (b) smoke records `provider: deepseek-direct`) → `_direct_wire_id("qwen/qwen3.8-max")` → `"qwen3.8-max"` → DeepSeek 400 (official DeepSeek semantics: 400 = Invalid Format, "do not retry unchanged") → `classify_llm_error` → `FATAL_CONFIG` → `is_fatal(e)` → `RoutingModel.complete` raises → `AskReaderUnavailable` → 502. `RotatingModel` (3-provider pool) re-raises identically (`if is_fatal(e) and not billing: raise`). **The 400 classification is CORRECT; the defect is that the routing never should have sent a non-deepseek spec to the deepseek lane in the first place.**

---

### Alternative Framings (problem-diverge)

1. **(a) The real problem is RotatingModel/RoutingModel failover policy; routing is the symptom.** Treat provider-mismatch 400s as retryable/failover-eligible (error-body sniffing: message mentions "model"/"not found"). — **Counter-evidence:** DeepSeek's own docs define 400 as invalid request format and explicitly say do-not-retry-unchanged (external research); the repo's own #1530 discipline is "no flip-flop on 4xx". A blanket 400→failover would mask genuine config bugs (malformed body would silently fail over to OpenRouter and return a plausible-but-wrong answer instead of a loud 502) and violates the M2/M3 taxonomy contract. The 400 is not the defect — the provider-blind pool construction is.
2. **(b) The real problem is the M5 pin defaulting to a weak model (deepseek-v4-flash).** If the default were qwen3.8-max the lane would "just work". — **Counter-evidence:** the DEFAULT lane (deepseek-v4-flash, deepseek-direct) works today and is deliberately cheap (#1350/#1790 production decision); the blocker is that an explicit override cannot be served, not that the default is weak. Flipping the default before routing exists would ship a 502-by-default.
3. **(c) Cost/quality governance — how do we decide the reader model at all?** The routing bug is just the first blocker on a missing decision process (no model-comparison arm; the $0.21/$0.42 envelope is deepseek-only). — **Partial truth:** the decision IS the O/I/T arc (measure → decide → flip pin), but the CODE deliverable is the mechanism; the governance is the acceptance gate.
4. **(d) Single-provider architecture assumption.** The adapter set is provider-specific (DeepSeekDirectModel can only serve deepseek family) while routing is provider-agnostic — the architecture implicitly assumes all models are deepseek-family. — **This framing is the closest to the true root cause** (see Converge rationale); the fix generalizes to every non-deepseek family (upstage/solar-pro4, anthropic/claude-opus-5, etc.), not just qwen.

---

### Assumptions (with [validated]/[unverified])

| # | Assumption | Status | Evidence |
|---|-----------|--------|----------|
| A1 | `build_reader_model` uses a deepseek-direct primary that 400s on non-deepseek specs | [validated] | model_adapters.py:845-862 (inherits `resolve_extractor_provider`; default primary deepseek-direct when `DEEPSEEK_API_KEY` set); `_direct_wire_id` model_adapters.py:585 |
| A2 | 400 is NOT a failover trigger in RoutingModel/RotatingModel | [validated] | `FATAL_CONFIG_STATUS_CODES={400,404}` (line 289); `is_fatal` (341); `RoutingModel.complete` re-raise (541); `RotatingModel.complete` re-raise (≈686) |
| A3 | qwen3.8-max reproduces the failing commits on the #2027 evidence | [validated] | runbook: gpt4_8279ba02 → "10 days ago.", gpt4_7a0daae1 → "One week." (2026-08-30 probe) |
| A4 | The ask lane cannot serve qwen today | [validated] | A1+A2 code path; no other lane change since (main @ b6510945) |
| A5 | The 7 content-error failures (gpt4_8279ba02, gpt4_7a0daae1, gpt4_6ed717ea, 830ce83f, 0100672e, e831120c, b0479f84) are reader-MODEL-bound | [validated] | runbook's (d) class table attributes exactly these to deepseek-v4-flash content quality (distinct from the 4 retrieval-gap + 2 judge-bar classes) |
| A6 | The $0.21/$0.42 per-M rates are the deepseek ×1.5 envelope | [validated] | metering.py:246-247 `ASK_METER_RATES` + docstring |
| A7 | qwen3.8-max's OpenRouter price is $2.00/M in, $6.00/M out | [verified via web] | OpenRouter model page + 3+ independent sources (2026-08-31) |
| A8 | A 500-Q/spot-check run with the strong reader will show the content-error class reduced (0/7) | [unverified — this IS the indicator to measure] | qwen reproduced 2/7 evidence shapes; the full-class claim is a target, not a fact; retrieval/judge classes will NOT be fixed by this issue |
| A9 | OpenRouter serves qwen3.8-max cleanly at the ask-lane call shape (temp 0, max 500, no response_format, 60s bound) | [unverified] | MODELS['qwen3.8-max'] (line 214) is tuned for GATE JUDGES (max_tokens 8000, thinking_budget 2000) — reader shape differs; needs a live smoke |
| A10 | The 500-Q strong-reader config (runbook, PR #2067) is executable | [partially verified] | eval lane routes by spec (`openrouter:` prefix → `_PROVIDERS`), so the EVAL config works; the PRODUCT lane (`TORTOISE_ASK_MODEL`) has no provider-prefix parsing — that gap is the issue |
| A11 | Venice cannot serve non-deepseek specs | [validated] | VeniceModel docstring: serves deepseek-v4-flash; "verify the venice catalog before enabling" (#1549); a qwen spec on venice would 400/404 → fatal → pool-kill |
| A12 | DeepSeek's 400 error body does NOT carry a machine-readable "model not found" discriminator | [validated] | official error-code docs map 400 → Invalid Format with free-text message; no structured discriminator documented (external research) |
| A13 | Production has `DEEPSEEK_API_KEY` set (so the default primary is deepseek-direct) | [validated] | runbook (b) smoke: `provider: deepseek-direct | route: deepseek-direct` (both the 2026-08-29 and -30 runs) |

---

### Adversarial Queries + Pre-mortem

**Adversarial web_search queries (seeking alternative root causes / disconfirmation):**

1. **"LLM provider routing failover 400 error handling best practices multi-provider fallback"** → Industry consensus: permanent 4xx (other than 429/malformed) should NOT retry/failover; failover is for transient + definitive provider failures; circuit breakers + cooldowns for degraded providers (LiteLLM docs, devopsness, Tetrate, Maxim AI). **Disconfirms blanket 400-failover.**
2. **"DeepSeek API error when passing non-deepseek model id 400 unknown model — is deepseek-direct 400 retryable fallback or config error"** → DeepSeek official docs: 400 = "Invalid Format", a client/request config problem; "do not retry an unchanged 400"; an unknown model ID surfaces as 400. **Confirms the taxonomy is right and the defect is upstream (routing).**
3. **"DeepSeek API error response JSON body format 'model' 'does not exist'/'not found' 400"** → No documented machine-readable discriminator; error is a free-text `message`; community examples show varied 400 messages ("response_format type unavailable", "prompt must contain json", …). **Disconfirms reliable error-body sniffing as the primary mechanism.**
4. **"LiteLLM router fallback 400/404 semantics"** → LiteLLM treats 404 as non-retryable within a group but can still escalate to cross-group fallbacks; retries-then-fallbacks architecture. **Precedent for capability-scoped routing tables.**
5. **"OpenRouter fallback models route parameter providers"** → OpenRouter offers client-side `models` fallback lists + `route:"fallback"` + `allow_fallbacks`/`order`/`require_parameters` — provider failover INSIDE the gateway request. **Precedent for gateway-routing approach (c).**
6. **"Vercel AI SDK / LangChain fallback"** → Vercel AI Gateway: `models` array fallback + provider `order`/`only`; LangChain: `init_chat_model("provider:model")` + `.with_fallbacks([...])` — **spec-declared provider convention** (identical to the repo's eval `openrouter:` prefix). **Confirms approach (b)'s convention is industry-standard.**
7. **"qwen3.8-max OpenRouter pricing"** → $2.00/M in, $6.00/M out (cache read $0.25/M, cache write $2.50/M). **Cost axis: the strong lane is 14-21× the deepseek-direct rate.**

**Pre-mortem — "we shipped the routing fix and it didn't work" (3 scenarios):**

1. **The 500-Q/spot-check shows the content-error class NOT reduced (target 0/7 fails).** Why: the 7 failures are partly retrieval-coupling (the FTS-40 pool must surface the same evidence; ceb5's answer turn ranks ~70 — if the product spot-check's pool misses gold turns, a stronger model has nothing to commit to). The indicator is scoped to reader-MODEL content errors — the fix must run the SAME evidence shape (spot-check seed 1987, product lane) and report per-class, not the aggregate, which will STILL be bound by retrieval (4) + judge-bar (2) classes. Mitigation: acceptance measures the 7 named failures on the same evidence, explicitly NOT the 0.8 aggregate.
2. **The routing fix works but cost explodes silently.** qwen at $2/$6 is 9-10× the deepseek envelope; if `ASK_METER_RATES` stays 0.21/0.42, every strong-lane ask under-counts by ~10× and the per-team dollar blast radius at 60/min grows from ~$0.14 to ~$1.28/min worst case — unbounded under the count-based budget. Mitigation: the rate re-baseline is IN the same change (Step 3), and the $0.01/query-target break surfaces as a recorded owner decision.
3. **The routing fix breaks the deepseek lane.** A capability filter mis-parses (`deepseek/deepseek-v4-flash` misclassified, or `TORTOISE_EXTRACTOR_PROVIDER` inheritance change) → the (b) known-answer smoke (`provider: deepseek-direct`) regresses, or the extraction lane is touched. Mitigation: `build_extractor_model` stays byte-identical (param default None); regression pins: `test_build_reader_model_resolves_env_and_reports_spec` + the runbook (b) smoke must stay green with the default config.

**Boundary check (OUTSIDE scope):**
- ❌ #2009 detector parity (separate failure class, separate issue, owner-assigned).
- ❌ Retrieval top-k / hybrid / reranking (runbook follow-up (2)).
- ❌ Containment-judge bar / SSP long-gold composition (runbook follow-up (3)).
- ❌ Extraction-lane routing changes (`TORTOISE_EXTRACTOR_PROVIDER` semantics preserved).
- ❌ Any change to the FATAL/TRANSIENT taxonomy export contract (M2/M3 consumers: retry.py, extractor_v2) — a mismatch predicate, if landed, is internal to the routing path only.
- ❌ LiteLLM proxy deployment / new third-party deps (zero-deps rule).
- ✅ IN SCOPE: provider-capability pool construction for the ASK lane, ask-scoped provider knob, meter-rate re-baseline, cost re-measure record, and the M5 `READER_MODEL` pin flip **gated on the measurement**.

---

### Problem-Converge Rationale + Falsification + Confidence

**Best problem definition:** *The ask lane's reader routing is provider-blind — every pool provider is built with the same spec, so a spec valid only on OpenRouter hard-fails on the deepseek-direct primary with a correctly-fatal 400, making the proven strong reader unservable and the M5/cost decision unmeasurable on the product lane.*

**Why this framing:** (1) it names the actual defect (provider-blind pool construction in `build_extractor_model`'s reuse) and preserves the CORRECT 400 taxonomy — the fix is architectural (route by capability), not a classification weakening; (2) it explains both symptoms (deepseek lane fine, qwen lane 502) with one mechanism; (3) it generalizes to every non-deepseek family (solar-pro4, claude-opus-5), so it closes the whole class, not just qwen; (4) it reuses the repo's OWN proven convention — the eval lane already routes `openrouter:qwen/qwen3.8-max` via spec-prefix parsing (`_parse_model_spec`/`_resolve_provider`, tools/longmem_eval/reader.py) — the product lane just never adopted it.

**Rejected alternatives with rationale:**
- (a) "failover policy is the problem" → rejected as the primary fix: contradicts DeepSeek's documented 400 semantics + the repo's #1530 no-flip-flop discipline; would retry guaranteed-fatal requests and mask config bugs. Only defensible as a NARROW defense-in-depth layer (mismatch-400 → cooldown + failover) with a recorded body fixture.
- (b) "weak default model is the problem" → rejected: the default deepseek lane works and is deliberately cheap; flipping the default before routing exists ships a 502-by-default.
- (d) "single-provider architecture" as a rewrite → rejected as too broad: deepseek-direct's cheap lane is a deliberate production decision; the fix must KEEP it for deepseek specs, not flatten the architecture.

**Falsification check:** this definition is wrong if (i) a qwen spec served via OpenRouter fails for a NON-routing reason (OpenRouter rejects the reader's call shape — reasoning/thinking params, response_format, temperature) — then the blocker is adapter-compatibility, not routing; (ii) DeepSeek's 400 body turns out to carry a reliable machine-readable model-mismatch discriminator — then error-sniffing failover becomes robust and the routing rewrite is over-engineered (this REFINES, not refutes: capability filtering still prevents the wasted call); (iii) production actually lacks `DEEPSEEK_API_KEY` (no deepseek-direct primary at all) — refuted by the runbook's own (b) smoke records (`provider: deepseek-direct`). A graded-run control: with the fix, `TORTOISE_ASK_MODEL=qwen/qwen3.8-max` must serve via `provider: openrouter` with the SAME evidence the qwen probe committed on — if it still 502s, the falsification (i) branch is live.

**Confidence: 82/100.** Root cause is fully code-verified (A1-A6, A11-A13); residual 18% = the unmeasured A8 (full-class reduction), A9 (OpenRouter behavior at the reader call shape), and A12's error-body nuance.

---

### Axis Research (per axis, with citations + sources)

#### Architecture axis

**Codebase-first precedent scan (grep of tortoise/):**
- `RoutingModel` (primary+fallback, transient-only failover, sticky-forward, cooldown) + `RotatingModel` (weighted round-robin, per-provider cooldown, 402-only rotation) — model_adapters.py:493-754.
- `FATAL_CONFIG_STATUS_CODES = {400, 404}` + `is_fatal` — the M2/M3 taxonomy export contract (model_adapters.py:286-389).
- **The eval lane's spec-prefix routing IS the repo precedent:** `_parse_model_spec("openrouter:qwen/qwen3.8-max")` → provider "openrouter" → `_PROVIDERS["openrouter"] = (base_url, key_env)` → `OpenAICompatModel` (tools/longmem_eval/reader.py:107-204, tortoise/ingest.py:33-38). The product lane never adopted this.
- `_REGISTRY_KEY_TO_ID` (model_adapters.py:631-673): `qwen3.8-max`/`solar-pro4`/`claude-opus-5` "intentionally pass through — judge/reader-only" — so a MODELS-key spec currently reaches `build_extractor_model` as a raw spec (correct: qwen must NOT be registry-mapped to a deepseek id).

**External (canonical + competitor-precedent + pitfalls):**
- **LiteLLM** (docs.litellm.ai/docs/routing, /docs/router_architecture, /docs/proxy/reliability): retries within a model group first, then cross-group fallbacks **in order**; 404 treated non-retryable; per-deployment cooldown. Precedent: routing tables + ordered fallback; "non-retryable ≠ never-route-elsewhere" (a 404 on ONE deployment escalates to another model group).
- **OpenRouter** (openrouter.ai/docs/guides/routing/model-fallbacks; OpenRouterTeam/schemas; /docs/guides/routing/provider-selection): client-side `models` fallback list (up to 3, priority order), `route:"fallback"`, provider selection with `order`/`allow_fallbacks`/`require_parameters` — provider-level failover INSIDE the gateway request; single model served by multiple providers auto-fails-over on 5xx/rate-limit.
- **Vercel AI Gateway** (vercel.com/docs/ai-gateway/models-and-providers/model-fallbacks): `models` array fallback in `providerOptions.gateway`, tried in order; provider order via `order`/`only`/`sort`; billing follows the successful model.
- **LangChain/LangGraph** (langchain-ai.github.io/langgraph/agents/models/; reference.langchain.com): `init_chat_model("anthropic:claude-3-5-haiku-latest")` — **spec-declared provider** convention — + `.with_fallbacks([...])`; `max_retries` for transients; 400s are surfaced as `BadRequestError` (a LangChain issue documents 400 from reasoning-block params — i.e., 400s are call-shape errors, not failover triggers).
- **Pitfalls (consensus):** permanent 4xx except 429/malformed should not retry (devopsness.com/blog/multi-provider-llm-routing-failover; tetrate.io/learn/ai/llm-failover-multi-provider); circuit breakers + cooldown + periodic primary probe (promptunit.ai, getmaxim.ai); simulate provider failure in staging (Tetrate).

**Finding:** industry consensus supports the repo's fatal-400 taxonomy. The two patterns that solve "spec valid on only one provider" are **(1) provider-declared specs + capability-aware selection** (LangChain's `provider:model`, the repo's own eval lane) and **(2) gateway-level model fallbacks** (OpenRouter/Vercel). The repo's eval lane already implements (1); the product lane's fix is to adopt the same convention for the ask lane.

#### Research axis

**Codebase-first:** runbook's (d) class table (7 reader-MODEL content errors vs 4 retrieval-gap vs 2 judge-bar); qwen3.8-max probe results on 3 evidence shapes; `MODELS['qwen3.8-max']` = OpenRouterModel('qwen/qwen3.8-max', max_tokens=8000, temperature=0.0, thinking_budget=2000) — gate-judge tuning, NOT a reader spec; eval `READER_MODEL = "openrouter:deepseek/deepseek-v4-flash"` (M5 pin).

**External (competitor research — how RAG/memory products pick answer models):**
- **Mem0** (docs.mem0.ai/core-concepts/memory-operations/search; prior repo research docs/research/2026-08-29-reader-answer-surface-competitors.md): retrieval-only read path (vector+BM25+entity, LLM-free search); LLM used at WRITE time (extraction) with a single provider config; no hosted QA answer surface.
- **Zep/Graphiti** (github.com/getzep/graphiti; help.getzep.com/retrieving-context): 0-credit LLM-free retrieval + Context Block for the caller's LLM; `mode="summary"` is write-side pre-computed context summarization.
- **Cognee** (docs.cognee.ai, cognee.ai): recall-only; "build structured context BEFORE generating an answer" — the answer is the downstream app's LLM.
- **Letta/LangMem/LangGraph Store, OpenAI/Anthropic memory**: agent-driven or model-driven — the calling agent's LLM answers; memory vendors externalize answer quality/cost.
- **Together/Groq/Fireworks:** single-provider endpoints; no client-side cross-provider failover — provider redundancy is the gateway's job (OpenRouter/Vercel/LiteLLM).
- **DeepSeek-direct on foreign specs:** official 400 "Invalid Format" (api-docs.deepseek.com/quick_start/error_codes/) — "do not retry unchanged".

**Finding:** NO competitor ships a hosted, abstention-disciplined memory-QA answer surface with multi-provider failover — tortoise's ask lane is open space (the prior research doc's verdict). Consequently there is no external benchmark for reader-model selection; the decision basis is INTERNAL (the runbook's probe + the 500-Q indicator). The config precedent the repo already has: `TORTOISE_LME_READER_MODEL='openrouter:qwen/qwen3.8-max'` (eval lane, documented in the runbook via PR #2067, "verified up to spec parse + mock-run only, not executed").

#### Config axis

**Codebase-first:** `TORTOISE_ASK_MODEL` (product; family-prefixed spec; provider-agnostic routing); `TORTOISE_LME_READER_MODEL` (eval; `provider:model` spec; provider-aware routing); `TORTOISE_EXTRACTOR_PROVIDER` (extractor lane primary/fallback); `TORTOISE_EXTRACTOR_FAILOVER_COOLDOWN` (flap guard); `.env.example:392-404` ask-surface block.

**External:**
- **LiteLLM**: config-driven (models.yaml deployment lists + model-group routing) — a declarative provider map.
- **OpenRouter**: per-request `models`/provider options — no config file, routing declared in the call.
- **Vercel AI Gateway**: per-request `providerOptions.gateway`.
- **LangChain**: `provider:model` spec strings + env-driven key resolution.
- **DeepSeek-direct behavior:** 400 Invalid Format; unknown-model 400s carry a free-text message with no documented machine discriminator (A12).

**Pitfalls:** (i) misrouting a MALFORMED request to a fallback provider masks the config bug (wrong-answer risk) — fail-closed on unambiguously-malformed 400s is the safer default; (ii) a provider knob that inherits the extractor's env couples two lanes with different needs (the reader lane needs an ask-scoped knob); (iii) family-prefix parsing must run AFTER `_REGISTRY_KEY_TO_ID` normalization and must treat bare ids as deepseek-family for back-compat.

**Finding:** the repo's own spec convention already encodes the provider (`qwen/` is an OpenRouter-only family; `deepseek/` is served by deepseek-direct/venice/openrouter). A capability-derived provider map needs **no new config surface**; only an optional ask-scoped override (`TORTOISE_ASK_PROVIDER`, default `auto`) is warranted — mirroring `TORTOISE_EXTRACTOR_PROVIDER` without coupling the lanes.

#### Cost axis

**Codebase-first:** `ASK_METER_RATES = {"prompt_per_1m": 0.21, "completion_per_1m": 0.42}` (deepseek-direct $0.14/$0.28 × 1.5 documented over-cover; metering.py:246-247); `estimate_ask_cost_usd` (249-260); `MAX_ASK_LLM_PER_MIN = 60` (quota.py); worst case ~9.2k in + 500 out ≈ $0.0014-0.0023/query vs the $0.01/query structural target (epic plan Cost Metering Design; research doc's verified math).

**External (verified pricing):**
- **deepseek-v4-flash direct:** $0.14/M in, $0.28/M out, $0.0028/M cached (api-docs.deepseek.com/quick_start/pricing; repo prior research).
- **qwen3.8-max via OpenRouter:** **$2.00/M in, $6.00/M out**; cache read $0.25/M, cache write $2.50/M (openrouter.ai model page + 3 independent sources, 2026-08-31).

**Re-measure math (to be recorded in the runbook):**
| Lane | Worst-case (9.2k in + 500 out) | Typical (~3.5k in, 150 out) |
|---|---|---|
| deepseek-direct (current envelope ×1.5) | ≈ $0.0021 | ≈ $0.0009 |
| qwen3.8-max via OpenRouter (real rates) | **≈ $0.0214** | ≈ $0.0080 |
| qwen with cache-read on the static prefix | ≈ $0.0195 | ≈ $0.0055 |

**Findings:** (1) the strong lane is **~9-10× the deepseek envelope** and BREAKS the $0.01/query worst-case target; (2) the ×1.5 over-cover convention should be re-based to the qwen spec (e.g., `{3.00, 9.00}`) so `cost_estimate_usd` stays honest (never under-count); (3) the 60/min count-based budget's dollar blast radius grows from ~$0.14 to ~$1.28/min/team worst case — an owner decision (tighten the strong lane's context cap, exploit OpenRouter $0.25/M cache-read, or re-baseline the $0.01 target for the strong lane); (4) the issue's target is "re-measured and recorded" — the record must surface this break explicitly before the M5 flip.

---

### Integration Docs

- **Dependencies added:** NONE (zero-deps rule preserved — stdlib + `requests` only). The OpenRouter lane reuses the existing `OPENROUTER_API_KEY` env + the existing `OpenRouterModel` adapter (requests.Session, timeout (10,60), JSON body shape). No LiteLLM/proxy/new SDK.
- **API surface touched:**
  - `build_reader_model(model_id=None, *, max_tokens=500, temperature=0.0)` — signature UNCHANGED; resolution behavior changes (ask-scoped provider, capability-filtered pool). Returns `RoutingModel` (1-2 providers) or `RotatingModel` (3+) exactly as today.
  - NEW env: `TORTOISE_ASK_PROVIDER` (`auto` | `deepseek-direct` | `openrouter` | `venice`; default `auto` = family-derived). Mirrors `TORTOISE_EXTRACTOR_PROVIDER`'s fail-closed rule (explicit provider without its key → ValueError).
  - NEW helper (private): `_providers_can_serve(model_id) -> set[str]` — family-prefix capability map; `resolve_reader_provider(model_id) -> tuple[str | None, list[str]]`.
  - `build_extractor_model` UNCHANGED by default (new private `provider_scope` param default None, or a shared private pool-builder — extraction behavior byte-identical).
  - `tortoise/metering.py`: `ASK_METER_RATES` kept for the deepseek lane; NEW `ASK_METER_RATES_STRONG = {"prompt_per_1m": 3.00, "completion_per_1m": 9.00}` (qwen ×1.5); `estimate_ask_cost_usd` unchanged (rates passed in); `sdk.ask` selects rates by the resolved spec family (deepseek → 0.21/0.42; else → 3.00/9.00).
  - `tools/longmem_eval/reader.py:83` `READER_MODEL` — GATED flip to `openrouter:qwen/qwen3.8-max` (Step 4).
  - `.env.example:392-404` — `TORTOISE_ASK_PROVIDER` + updated `TORTOISE_ASK_MODEL` comment (spec format: family-prefixed; provider auto-derived).
- **Call-shape compatibility check (A9):** the reader calls `complete(system, user)` with temp 0, max_tokens 500, `json_mode=False` pin (no response_format). The OpenRouterModel path sends `reasoning: {effort: none}` only when `disable_reasoning=True` — the reader's `build_reader_model` does NOT set `disable_reasoning`, so no reasoning params are sent; qwen3.8-max must be verified live to not burn the 500-token budget on internal reasoning (the gate-judge tuning uses thinking_budget=2000 for a reason — #946 observed all-reasoning collapses). **Live smoke is a prerequisite test (Step 1).**

---

### Codebase Explorer Findings

**AFFECTED_FILES (paths + lines):**
- `tortoise/model_adapters.py` — `build_reader_model` (845-862), `build_extractor_model` (797-841), `_build_single` (573-588), `resolve_extractor_provider` (397-455), `RoutingModel.complete` (527-546), `RotatingModel.complete` (≈668-707), `classify_llm_error`/`is_fatal` (312-389), `FATAL_CONFIG_STATUS_CODES` (289), `_direct_wire_id` (585), `_REGISTRY_KEY_TO_ID` (631-673), `MODELS['qwen3.8-max']` (214).
- `tortoise/metering.py` — `ASK_METER_RATES` (246-247), `estimate_ask_cost_usd` (249-260), `record_ask_usage` (≈278-330).
- `tortoise/sdk.py` — `_ask_reader_cache`/`_default_ask_reader_factory` (43-96), `_LockedReader` (98-140), `ask()` (10505+; rate selection ~10560-10600 region where cost_estimate_usd is computed).
- `.env.example` (392-404) — ask-surface env block.
- `tools/longmem_eval/reader.py` — `READER_MODEL` (83), `build_reader` (165-204) — M5 pin + the spec-prefix routing precedent.
- Docs: `docs/runbook/1987-ask-abstention-check.md` (record results + cost re-measure), `docs/product/answer-surface.md` (cost/lane docs), `docs/plans/2026-08-29-1987-ask-reader.md` (Task 3 failover policy notes reference).
- Tests: `tests/test_model_adapters_routing.py` (880-1100: json_mode pin, `test_build_reader_model_resolves_env_and_reports_spec` 922, `test_wrapper_forwards_usage_and_model` 964, `test_failover_policy_pin` 1002, concurrent rotation 1040); `tests/test_ask_sdk.py`, `tests/test_ask_api.py`, `tests/test_ask_regression_llm.py`, `tests/test_reader_abstention_calibration.py`.

**PATTERNS_OBSERVED (existing failover/routing patterns anywhere in tortoise/):**
1. **Eval lane spec-prefix routing** (tools/longmem_eval/reader.py `_parse_model_spec`/`_resolve_provider` + `_PROVIDERS` registry at tortoise/ingest.py:33-38) — `provider:model` → base_url/key_env. **The exact pattern the product lane lacks.**
2. RoutingModel/RotatingModel failover with per-provider cooldown + sticky-forward (model_adapters.py:493-754).
3. `_FAILOVER_COOLDOWN` process-local flap guard keyed by provider name (model_adapters.py:457-484).
4. The M2/M3 taxonomy contract (`LlmErrorClass`, FATAL/FATAL_CONFIG/TRANSIENT/UNKNOWN) consumed by `tortoise/retry.py` + `tortoise/extractor_v2.py` — MUST NOT drift.
5. Ask-lane per-namespace LRU reader cache + `_LockedReader` serialization (sdk.py:43-140) — model config is process-env-wide; the cache is namespace-keyed (per-team instances share one env-resolved model).
6. Fail-closed philosophy on explicit provider envs (`resolve_extractor_provider` raises ValueError on explicit-provider-without-key).

**PARTIAL_IMPLEMENTATIONS:**
- `build_reader_model` exists with `json_mode=False` pin + `TORTOISE_ASK_MODEL` resolution — but provider-blind.
- `MODELS['qwen3.8-max']` OpenRouter entry exists — but tuned for gate judges (8000 tok / thinking_budget 2000), not the reader call shape.
- The runbook's 500-Q strong-reader config (PR #2067) is documented but unexecuted (eval lane; "verified up to spec parse + mock-run only").
- 429 is already a failover trigger (`TRANSIENT_STATUS_CODES` incl. 429, model_adapters.py:288) — the ask lane's transient failover story is complete; only the FATAL_CONFIG-on-wrong-provider class is mis-routed.

**RECOMMENDED_TESTS (see Plan Draft → Testing for full detail):**
- Provider-capability pool per spec family; registry-key normalization before family parse; bare-id back-compat.
- `TORTOISE_ASK_PROVIDER` explicit-without-key fail-closed; `openrouter` forced override for deepseek specs.
- FATAL-400 preservation on correctly-routed primaries (taxonomy contract); optional mismatch-400 failover with a RECORDED DeepSeek body fixture.
- Real-factory fake-transport: `TORTOISE_ASK_MODEL=qwen/qwen3.8-max` → request hits OpenRouter base_url with `model: qwen/qwen3.8-max`, exactly one `complete()`, response `model/provider/route` report the openrouter lane; 502 only when the openrouter lane also fails.
- Regression pins: `test_build_reader_model_resolves_env_and_reports_spec` unchanged; runbook (b) known-answer smoke still `provider: deepseek-direct`; `test_ask_regression_llm.py` fixture mode green.
- Meter rates: `estimate_ask_cost_usd` at STRONG rates bounds; `sdk.ask` selects rates by resolved spec family; per-query cost re-measure record.
- Live smoke (opt-in): qwen3.8-max at the reader call shape (A9) — no reasoning-budget collapse, commits on the gpt4_8279ba02 evidence.

**DEPENDENCIES:** none new. Runtime prerequisites: `OPENROUTER_API_KEY` for the qwen lane; `DEEPSEEK_API_KEY` for the default lane; eval dataset + docker lane for the 500-Q/spot-check indicator; the runbook's LLM regression module env.

---

### Solution Approaches (diverge — 2-3 distinct)

**Approach (a) — Narrow provider-mismatch failover in the adapters.**
- *Name:* "mismatch-400 fails over" (error-body-classified).
- *Description:* add `_is_provider_mismatch(exc)` (400/404 + body markers like "model" + not-found/invalid); in `RoutingModel.complete` and `RotatingModel.complete`, treat THAT subclass as failover-eligible (+ cooldown the primary, latch like a normal failover); all other 400/404 stay fatal.
- *Files:* tortoise/model_adapters.py (routing path only), tests.
- *Architecture:* keeps env-chosen provider-blind routing; the adapter self-heals on foreign specs.
- *Risks:* heuristic body parsing (A12 — DeepSeek documents no discriminator); every foreign spec still pays a dead primary call (latency + one wasted request); a false positive (e.g., a genuinely malformed request whose message mentions "model") would fail over and mask a config bug; taxonomy drift risk if the predicate leaks (must stay internal).
- *Tradeoffs:* minimal code; general safety net for ANY future spec; but fragile and masks config bugs.
- *Best-fit-if:* we want a safety net WITHOUT any config/convention change and accept heuristics + the dead-call cost.

**Approach (b) — Provider-capability-aware pool construction (spec-declares-provider).** ✅ recommended
- *Name:* "route by capability, ask-scoped."
- *Description:* in `build_reader_model` (ask lane only), derive the servable provider set from the spec's family prefix (`qwen/` → openrouter-only; `deepseek/`/bare → deepseek-direct + openrouter + venice as today); build the pool only from servable providers; add `TORTOISE_ASK_PROVIDER` (`auto` default) for explicit overrides; `build_extractor_model` unchanged (private shared pool-builder or a `provider_scope=None` param). Optional defense-in-depth: (a)'s mismatch-400 failover gated on a recorded-body fixture.
- *Files:* tortoise/model_adapters.py, tortoise/metering.py (rates — Step 3), tortoise/sdk.py (rate selection), .env.example, tools/longmem_eval/reader.py (gated M5 flip), docs, tests.
- *Architecture:* correct by construction — the deepseek-direct primary is structurally absent from non-deepseek pools (a deepseek-direct "400" becomes impossible for qwen); taxonomy contract untouched; extraction lane untouched; deepseek's cheap lane preserved.
- *Risks:* family-prefix parsing edge cases (**registry keys normalize via `_ASK_MODELS_KEY_SPECS` BEFORE family parse; bare non-deepseek keys fail loud; bare ids = deepseek back-compat; venice catalog**); `TORTOISE_ASK_PROVIDER` must not couple to the extractor env; the spec convention must be documented; qwen spec with NO `OPENROUTER_API_KEY` → **build-time ValueError naming the key (Step 1 empty-intersection guard)**.
- *Tradeoffs:* more code than (a); zero wasted calls; mirrors the eval's proven `provider:model` convention (LangChain/OpenRouter use the same namespace); generalizes to ALL non-deepseek families.
- *Best-fit-if:* the spec prefix is a reliable provider declaration (it is — the eval already relies on it), and we want correctness + zero dead calls + preserved cheap lane.

**Approach (c) — Gateway routing: the reader lane always serves via OpenRouter (spec-agnostic), with gateway-level model/provider fallbacks.**
- *Name:* "OpenRouter-primary reader."
- *Description:* `build_reader_model` returns an OpenRouterModel (or OpenRouter-primary RoutingModel) regardless of spec; redundancy via OpenRouter's `models` fallback list + `route:"fallback"`/`allow_fallbacks` in the request body (deepseek spec fallback to qwen or vice versa); `provider`/`route` report `openrouter` (or the gateway's chosen lane).
- *Files:* tortoise/model_adapters.py (build_reader_model simplified), .env.example, tests.
- *Risks:* loses the deepseek-direct cheap lane ($0.14/$0.28 vs OpenRouter markup — the runbook's (b) smoke shows $0.000129/query on the direct lane); contradicts the #1350/#1790 production decision (deepseek-direct primary for cost + resilience under load — #1350's OpenRouter connection-error collapse class); failover semantics move into a third party (provider/route transparency degrades to "openrouter"); the 400-on-wrong-provider class disappears only because everything goes through one provider.
- *Tradeoffs:* simplest client code (one adapter), proven lane (eval ran `openrouter:deepseek/deepseek-v4-flash`); but every deepseek ask gets more expensive and route observability is coarser.
- *Best-fit-if:* deepseek-direct's cost advantage is immaterial for the ask surface, or provider instability demands gateway-managed provider failover.

*(Variant (d) — explicit config-driven provider map (`family → provider` env/dict): folded into (b) as the `TORTOISE_ASK_PROVIDER` override; standalone it adds config surface for no benefit since the prefix already encodes the family.)*

**NO winner declared yet — see Solution-Converge.**

---

### Solution-Converge Rationale + Rejected Alternatives

**Best approach: (b) provider-capability-aware pool construction + ask-scoped provider knob + meter-rate re-baseline + gated M5 flip** — with (a)'s mismatch-failover as an OPTIONAL defense-in-depth layer, gated on a deterministic recorded-body fixture.

**Rationale (QUALITY-OVER-CONVENIENCE):**
1. **Outcome quality:** (b) is correct by construction — the deepseek-primary-400s-qwen state becomes structurally unreachable (the deepseek adapter is not in qwen pools). (a) only recovers AFTER the failure, per-request, with heuristic classification. (c) removes the failure but destroys the cheap lane.
2. **Edge cases:** (b) handles registry keys (**normalize via `_ASK_MODELS_KEY_SPECS` before family parse; unknown bare non-deepseek → ValueError**), bare ids (deepseek back-compat), venice catalog (deepseek-only), missing keys (**empty-intersection guard → build-time ValueError naming the key**), and generalizes to every non-deepseek family. (a) has no answer for body-parsing ambiguity (A12). (c) has no answer for the cost edge case.
3. **Failure modes:** (b) preserves the documented fatal-400 semantics for genuine config bugs (a malformed request on a correctly-routed primary still 502s loudly). (a) risks masking config bugs behind failover. (c) moves failure modes into a gateway with coarser observability.
4. **Extensibility:** (b) needs zero code for any future family (the prefix declares the provider) — matching the eval lane's existing `provider:model` convention and LangChain's `init_chat_model` pattern. (a) needs a new heuristic per provider. (c) needs gateway-config management.
5. **Cost honesty:** only (b) preserves the deepseek lane AND enables a spec-aware meter re-baseline (Step 3) so `cost_estimate_usd` never under-counts on the strong lane.

**Rejected alternatives + when each WOULD have been better:**
- **(a) narrow mismatch-failover as the PRIMARY fix:** rejected — heuristic (DeepSeek documents no discriminator), pays a dead call per foreign spec, risks masking config bugs, and the taxonomy contract's "no flip-flop on 4xx" discipline is a deliberate production decision. **Better when:** DeepSeek ships a machine-readable model-mismatch error code, or as defense-in-depth alongside (b) once a recorded 400-body fixture proves the discriminator — adopt ONLY then.
- **(c) OpenRouter-gateway reader:** rejected — cost (deepseek markup on every deepseek ask; the (b) smoke's $0.000129/query on the direct lane is the documented baseline), contradicts #1350/#1790 (deepseek-direct primary was chosen for cost AND because OpenRouter hit connection errors under load — the #1350 collapse class), and route transparency degrades. **Better when:** deepseek-direct's cost advantage is immaterial for the ask surface, OR provider instability returns and gateway-managed `allow_fallbacks` becomes the resilience requirement — then the client shrinks to a single adapter.
- **(d) explicit config-driven provider map:** folded into (b)'s `TORTOISE_ASK_PROVIDER` override; standalone it duplicates what the prefix convention already encodes. **Better when:** arbitrary family→provider overrides beyond the prefix (e.g., routing deepseek via OpenRouter for cache economics) or a non-prefixed spec format is required.
- **Flipping the M5 pin before the measurement:** rejected — the issue's own targets gate the flip on the cost re-measure + the content-error run; flipping first ships a 502-by-default or an unmeasured cost.

---

### Plan Draft (implementation steps, testing, acceptance criteria, prerequisites)

**Problem statement:** see Confirmed Problem.

**Proposed solution:** ask-lane routing by provider capability + ask-scoped provider knob + spec-aware meter rates + gated M5 pin flip.

**Implementation steps (ordered):**

1. **Step 1 — Capability model + ask-scoped resolution (`tortoise/model_adapters.py`).**
   - Add `_SPEC_FAMILY_PROVIDERS` map + `_providers_can_serve(model_id) -> set[str]`: family = spec's prefix (`qwen/`, `upstage/`, `anthropic/`, … → `{"openrouter"}`); `deepseek/` and bare ids → `{"deepseek-direct", "openrouter", "venice"}`; **unknown family prefixes → fail-loud `ValueError` naming the family (NOT "→ all")** — an unrecognized family (e.g. `mistral/mistral-large`, a typo'd family, a future family not yet mapped) must never fall through to deepseek-direct and re-introduce the exact defect (deepseek 400 on a foreign spec). Run AFTER `_REGISTRY_KEY_TO_ID` normalization. **Precedence: the MODELS-key normalization and colon-form rejection below run BEFORE this family parse, so a bare `qwen3.8-max` or `openrouter:qwen/qwen3.8-max` never reaches the unknown-family branch; every recognized or unknown family resolves fail-loud, never to a wrong provider.**
   - **ASK-LANE MODELS-KEY NORMALIZATION (verifier-fix, REQUIRED):** bare non-deepseek MODELS registry keys (`qwen3.8-max`, `solar-pro4`, `claude-opus-5` — passed through unmapped by `_REGISTRY_KEY_TO_ID` at model_adapters.py:758, which maps deepseek-* keys only) are NOT deepseek-family and MUST NOT route to deepseek-direct. Resolve BEFORE family parse: `_ASK_MODELS_KEY_SPECS = {"qwen3.8-max": "qwen/qwen3.8-max", "solar-pro4": "upstage/solar-pro4", "claude-opus-5": "anthropic/claude-opus-5", …}` — derivable from the `MODELS` registry's target slugs (verified: qwen3.8-max→qwen/qwen3.8-max, solar-pro4→upstage/solar-pro4, claude-opus-5→anthropic/claude-opus-5); a bare key absent from this map with a non-deepseek name → **fail-loud `ValueError` naming the required family-prefixed form** (never silently route to deepseek-direct). Bare `deepseek-*` ids keep back-compat → deepseek family. Regression pin `test_build_reader_model_resolves_env_and_reports_spec` (test_model_adapters_routing.py:922, deepseek-family only) stays green; **NEW test**: bare `qwen3.8-max` either resolves to `qwen/qwen3.8-max` via OpenRouter or fails loudly — never hits deepseek-direct.
   - **COLON-FORM REJECTION (verifier-fix):** the eval lane's `provider:model` format (`openrouter:qwen/qwen3.8-max` — the documented `TORTOISE_LME_READER_MODEL` value) must NOT fall into "unknown prefix → all" on the ask lane (prefix `openrouter:qwen` is unknown → deepseek-direct in pool → `_direct_wire_id` → `qwen3.8-max` → 400 → 502). The ask lane accepts `family/model` (product) format ONLY; a colon-form spec → `ValueError` pointing at the family-prefixed form. (The eval lane keeps its own `_parse_model_spec` untouched.)
   - Add `TORTOISE_ASK_PROVIDER` (`auto` default) — ask-scoped primary override; explicit value without its key → ValueError (fail-closed, mirror `resolve_extractor_provider`).
   - Add `resolve_reader_provider(model_id) -> tuple[str | None, list[str]]` — env/keys-resolved order INTERSECTED with `_providers_can_serve`, honoring `TORTOISE_ASK_PROVIDER`.
   - **Empty-intersection guard (verifier-fix):** if `_providers_can_serve(model_id) ∩ resolved-keys = ∅` (e.g. qwen spec with NO `OPENROUTER_API_KEY` in auto mode, or explicit `TORTOISE_ASK_PROVIDER=venice` with a qwen spec) → **build-time `ValueError` naming the missing key** (fail-fast, NOT a silent misbuild that 401s at call time). Preserve `build_extractor_model`'s lenient no-key default (`pool_names=["openrouter"]`) on the shared pool-builder so no-key ask behavior is unchanged.
   - `build_reader_model` switches to `resolve_reader_provider`; `build_extractor_model` gains a private `provider_scope` param default None (or a shared private pool-builder) — extraction lane byte-identical.
   - **Step 1 gate:** the runbook (b) known-answer smoke (default config) still reports `provider: deepseek-direct`; `test_build_reader_model_resolves_env_and_reports_spec` unchanged.
2. **Step 2 — (Optional, recommended) narrow provider-mismatch failover** (`model_adapters.py` routing path + `tests/fixtures/deepseek_400_model_body.json` recorded fixture). `_is_provider_mismatch(exc)` (400/404 + recorded body markers); in RoutingModel/RotatingModel: mismatch on the primary → cooldown + failover (latch). **Include ONLY if the recorded-body test proves the discriminator deterministic; otherwise ship Step 1 alone** (the capability filter makes the mismatch path unreachable for declared families).
3. **Step 3 — Meter-rate re-baseline (`tortoise/metering.py`, `tortoise/sdk.py`, docs).** Keep `ASK_METER_RATES` {0.21, 0.42}; add `ASK_METER_RATES_STRONG` {3.00, 9.00} (qwen $2/$6 × 1.5, same over-cover convention); `sdk.ask` selects rates by the SERVING wire id's family (`model.model` via `_LockedReader` — prefixed spec → STRONG; bare/back-compat deepseek → default; deepseek spec forced to openrouter STAYS on default rates, the ×1.5 envelope documents OpenRouter markup, metering.py:246-247). **Pin BOTH `estimate_ask_cost_usd` call sites** (sdk.py:10663-10667 record + 10683-10688 response) so they cannot drift; response `cost_estimate_usd` honest on both lanes. Re-measure and RECORD the per-query cost in the runbook + `docs/product/answer-surface.md`; surface the $0.01-target break (real rates worst ~$0.021; METERED at STRONG {3.00, 9.00} worst ~$0.032, typical ~$0.012 — record both) with the three owner options (tighten strong-lane context cap / exploit OpenRouter $0.25/M cache-read / re-baseline the target for the strong lane).
4. **Step 4 — Gated M5 pin flip.** Flip `tools/longmem_eval/reader.py:83` `READER_MODEL` → `openrouter:qwen/qwen3.8-max` ONLY after: (i) Step 1's qwen-serves-via-OpenRouter test passes, (ii) the 500-Q/spot-check indicator run shows 0/7 content-error recurrences (targets), (iii) the cost re-measure is recorded and accepted. Update `tests/test_longmem_reader_pinning.py` expectations + the runbook.
5. **Step 5 — Docs + env.** `.env.example`: `TORTOISE_ASK_PROVIDER` + updated `TORTOISE_ASK_MODEL` comment (family-prefixed spec ONLY — `qwen/qwen3.8-max`, not `openrouter:qwen/qwen3.8-max` (eval format, rejected on the ask lane), not bare `qwen3.8-max` (resolved via `_ASK_MODELS_KEY_SPECS` or fail-loud); provider auto-derived; OpenRouter-only families require `OPENROUTER_API_KEY`). **Epic acceptance annotation:** `docs/plans/2026-08-29-1987-ask-reader.md:379` pins `cost_estimate_usd ≤ $0.01 (structural caps)` on `/v1/ask` — the strong lane makes that hosted acceptance permanently unsatisfiable at real qwen rates (metered worst ~$0.032 at STRONG {3.00, 9.00}, ~$0.021 at real $2/$6); Step 3's docs deliverable must explicitly amend/annotate that epic row (not just the runbook + answer-surface.md), so the epic's own merge gate doesn't break. Runbook: record the routing fix, the 500-Q result, the cost re-measure, the pin decision. `docs/00_index.md` if a new research artifact is created.

**Testing strategy:**
- **Unit (offline, fake transport):** pool construction per family (deepseek → deepseek-primary with both keys; qwen → openrouter-only, no deepseek adapter in the pool); registry-key normalization precedes family parse (**NEW: bare `qwen3.8-max` MODELS key → `qwen/qwen3.8-max` via `_ASK_MODELS_KEY_SPECS` → openrouter-only, NEVER deepseek-direct; unknown non-deepseek bare key → ValueError; `deepseek-flash-direct` maps to the deepseek family**); colon-form `openrouter:qwen/qwen3.8-max` → ValueError on the ask lane (eval lane untouched); bare `deepseek-v4-flash` → deepseek family; `TORTOISE_ASK_PROVIDER=openrouter` forces openrouter for deepseek specs too; explicit-without-key → ValueError; **auto-mode empty intersection (qwen spec, no OPENROUTER_API_KEY) → build-time ValueError naming the key**; `build_extractor_model` unchanged (default path).
- **Failover-policy preservation:** a genuine request-shape 400 (malformed-body stub) on a correctly-routed primary still re-raises (taxonomy contract pinned); if Step 2 lands: a recorded DeepSeek model-not-found 400 body → failover + cooldown + recovery (next ask hits primary again for the transient class — for mismatch the latch is correct since the primary can never serve the spec).
- **Integration (docker lane):** `sdk.ask` with `TORTOISE_ASK_MODEL=qwen/qwen3.8-max` through the real factory + fake transport → request hits the OpenRouter base_url with `model: qwen/qwen3.8-max`; exactly ONE `complete()`; response `model/provider/route` = qwen/qwen3.8-max/openrouter/openrouter; 502 `reader_unavailable` only when the openrouter lane also fails; `cost_estimate_usd` uses the STRONG rates; per-namespace cache + `_LockedReader` unchanged.
- **Regression:** `tests/test_model_adapters_routing.py` existing pins; `tests/test_ask_*.py`; `tests/test_ask_regression_llm.py` (fixture mode; add a committed qwen gold-verbatim transcript if the live key is available); the runbook (b) smoke via the real lane.
- **Verification (indicator 2):** 500-Q strong-reader run — eval lane `TORTOISE_LME_READER_MODEL='openrouter:qwen/qwen3.8-max'` (runbook config, PR #2067) AND the product-lane spot-check composition (seed 1987) with `TORTOISE_ASK_MODEL=qwen/qwen3.8-max`; report per-class, targeting the 7 named reader-MODEL failures on the same evidence; the aggregate stays bound by the out-of-scope retrieval/judge classes (documented, NOT a regression).
- **Live smoke (A9):** qwen3.8-max at the reader call shape (temp 0, max 500, no response_format) commits on gpt4_8279ba02's evidence without reasoning-budget collapse.

**Acceptance criteria (mapped to O/I/T):**
- **O1/I1:** `TORTOISE_ASK_MODEL=qwen/qwen3.8-max` + production keys → `sdk.ask` serves via OpenRouter with zero manual intervention (`model: qwen/qwen3.8-max`, `provider: openrouter`, `route: openrouter`); the deepseek-direct primary structurally cannot hard-fail non-deepseek specs (excluded from non-deepseek pools / fails over via Step 2 if landed). Automated: real-factory fake-transport test + docker-lane integration test.
- **O2/I2:** 500-Q / spot-check with the strong reader: **0 of the 7 recorded content-error failures** (gpt4_8279ba02, gpt4_7a0daae1, gpt4_6ed717ea, 830ce83f, 0100672e, e831120c, b0479f84) recur on the same evidence (target). Recorded in the runbook with per-class counts.
- **T3:** per-query cost re-measured and RECORDED (qwen envelope vs the $0.21/$0.42 deepseek ×1.5) BEFORE the M5 pin flips; `ASK_METER_RATES_STRONG` ships so `cost_estimate_usd` never under-counts on the strong lane; the $0.01-target impact documented with an owner decision. The M5 `READER_MODEL` flip lands only after (i)-(iii) of Step 4.

**Runtime prerequisites:** `OPENROUTER_API_KEY` (+ `DEEPSEEK_API_KEY` for the default lane); the LongMemEval dataset (fetched/regenerated) + docker lane for the 500-Q indicator; `TORTOISE_ASK_LLM_REGRESSION=1` for the fixture-mode regression module; live keys for the opt-in live smoke. No new dependencies.

---

### Wiring Check

| Touch point | Covered by | Status |
|---|---|---|
| Provider keys (DEEPSEEK / OPENROUTER / VENICE) | Step 1 `resolve_reader_provider` key-gating + fail-closed on explicit `TORTOISE_ASK_PROVIDER` without key (mirror `resolve_extractor_provider`) | ✅ |
| `.env.example` ask block (392-404) | Step 5 (`TORTOISE_ASK_PROVIDER` + spec-format docs) | ✅ |
| Metering registry + Supabase seam | Step 3 rate parameterization; `record_ask_usage` MERGE path unchanged (rates only) | ✅ |
| Ask budget bucket (`tortoise/quota.py` 60/min) | Unchanged (count-based); **dollar blast-radius re-baseline (~$0.14 → ~$1.28/min/team worst case) is a documented owner decision in Step 3** | ⚠️ owner decision |
| `sdk.ask` reader cache + `_LockedReader` | Transparent — factory returns the new routing; cache keyed by namespace, model env-wide; no cache change | ✅ |
| Taxonomy export contract (M2/M3: `tortoise/retry.py`, `tortoise/extractor_v2.py`) | UNCHANGED — Step 1 filters the pool (never reclassifies); Step 2's mismatch predicate stays internal to the routing path | ✅ |
| `build_extractor_model` / extraction lane | Byte-identical (default path; private `provider_scope` param or shared helper) | ✅ |
| Eval lane (`READER_MODEL`, `build_reader`, `OpenAICompatModel`) | Untouched until the GATED M5 flip (Step 4) — eval lane keeps `openrouter:deepseek/deepseek-v4-flash` default meanwhile | ✅ |
| Eval dataset / 500-Q indicator run | Step 4 gate + verification (dataset FETCHED/REGENERATED; docker lane; live keys) | ⚠️ runtime prerequisite |
| Test infra (docker lane, fake transports, LLM regression fixtures) | Per-step tests; `test_ask_regression_llm.py` fixture mode stays green; new qwen transcript optional | ✅ |
| `docs/product/answer-surface.md` + `docs/00_index.md` | Step 5 (cost/lane docs + index row if a research artifact is added) | ✅ |
| `$0.01/query` structural target vs qwen worst case (~$0.021) | Step 3 re-measure + three documented owner options (tighten context cap / OpenRouter cache-read / re-baseline) — **BLOCKS the M5 pin flip until decided** | ⛔ resolve before Step 4 |

**Unresolved gaps (block on owner):**
1. **The $0.01/query target break** (qwen worst case ≈ $0.021 at the current 8k/500 caps) — owner must pick: tighten the strong lane's context cap, exploit OpenRouter $0.25/M cache-read, or re-baseline the target. The CODE (routing + rates) is not blocked; the M5 flip IS.
2. **A9 live-smoke sign-off** — qwen3.8-max at the reader call shape (no reasoning-budget collapse at max_tokens 500) must pass before the flip; without keys at scoping time this is a runtime prerequisite, not a design decision.
3. **Step 2 inclusion decision** — include the mismatch-400 defense-in-depth only if a recorded DeepSeek 400-body fixture proves the discriminator deterministic; otherwise ship Step 1 alone (capability filter already makes the mismatch unreachable for declared families).

---

*Package complete — all phases run: problem-diverge (4 framings, 13 assumptions mapped, 7 adversarial queries, 3 pre-mortem scenarios, boundary check), problem-converge (82/100 confidence + falsification), external research (4 axes, ≥4 competitors + DeepSeek-direct behavior + memory products, cost re-measure math), codebase explorer (real files, line-level), solution-diverge (3 distinct approaches), solution-converge (plan draft, acceptance mapped to O/I/T, rejected alternatives with when-better), wiring check (13 touch points, 3 resolved gaps). Nothing posted to GitHub.*
