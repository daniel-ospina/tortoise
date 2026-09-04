<!-- research-path: docs/plans/2026-09-03-llm-usage-cost-longmemeval.md -->

# LLM Token Usage + Dollar Cost in longmem_eval Reports — Implementation Plan

> **For Pi:** implement task-by-task (TDD where behavioral). Scope: see issue #2185 scoping comment (2026-09-03) + `.longmemeval_cache/scoping/2185-scope.md`.

**Goal:** Every real LLM call in a LongMemEval run (reader, judge, extractor_v2) records prompt/completion token usage; reports expose per-question + per-run token/cost aggregates from a versioned per-provider pricing map; mock runs are byte-unchanged (usage keys absent when no LLM is called).

**Team:** epistemic-team · **Issue:** #2185 · **Branch:** `feat/2185-longmem-usage-cost` · **TIER:** standard (Level: task).

**Architecture (3 additive layers, from verified scope):**
1. **Capture seam** — optional `usage_sink` callback (default None, inert) fired at each chat adapter's response-parse site (response-local data, per billed attempt, carrying the *serving* provider/model). Product touches: `tortoise/model_adapters.py` (OpenRouterModel/DeepSeekDirectModel/VeniceModel), `tortoise/models.py` (OpenAICompatModel — sink attribute only, NO mirrors; shared product class), `tools/longmem_eval/judge.py` (OfficialJudgeModel, eval-owned). Plus a ~3-line **context propagation fix** in `tortoise/extractor_v2.py::_call_once` (`contextvars.copy_context()` before `Thread.start()`; body under `ctx.run(...)`) — contextvars do NOT propagate to new threads in CPython (repo-verified quota.py ~739).
2. **Harness collector** — new `tools/longmem_eval/usage.py`: run-level collector, qid-keyed buckets by `(stage, provider, model)`, a question-key ContextVar (set as FIRST statement of the per-question task body), registration walkers, drain helpers. Per-outcome conditional `llm_usage` envelope; durable checkpoint `usage_overhead` payload for failure/preflight spend (atomic with `_upsert_failure`, merged on resume, #1764 sweep).
3. **Pricing + report** — new `tools/longmem_eval/costing.py` (versioned per-(provider,model) USD/1M map, documented sources, `estimated` flags, unpriced-lane policy) and conditional aggregation in `report.py`/`run.py` (raw stays in outcomes; cost computed at report time; never mutates nested dicts).

### Pattern Research

> **Findings date:** 2026-09-03
> **Gate skipped: plan touches zero third-party deps** — pure in-repo change (Python stdlib `urllib`/`contextvars`/`threading` + existing in-repo adapters). No library docs preflight, no Perplexity buckets apply (02-research-intake Step B skip rule: zero third-party deps).
> **Scoping-side research skip:** issue declares "Research needed: None"; scoping verified no in-repo precedent for eval usage capture, confirmed product metering.py is ×1.5-over-cover estimator (not exact), and the adapter mirrors pattern exists in-repo (model_adapters.py last_* fields). Pricing-map VALUES are verified with targeted web lookups during Task 5 implementation (scope Amendment 8: mandatory per-lane verification of usage-detail fields + current list prices) — value-only, no architecture impact.

### Integration Surface Map

| Surface | Type | Data flow | Contract | Test layer |
|---|---|---|---|---|
| Provider chat HTTP (openrouter/deepseek/venice/gemini/openai base URLs) | External API | Out/In | `data["usage"]` dict (prompt/completion_tokens, optional cache-detail fields); content in `choices[0].message.content` | Unit (stub adapters) + existing adapter tests unchanged |
| `tortoise/model_adapters.py` adapters | Code seam | In | sink fires only when `usage_sink` set; payload `(provider, served_model_id, usage_dict_snapshot, usage_present)`; **no behavior change when unset** | Unit — sink fire assertions; forwarding tests (test_model_adapters_routing.py:1171-1198) must stay green |
| `tortoise/models.py::OpenAICompatModel` | Code seam (shared product class — sdk.py/ingest.py/mining.py use it) | In | sink attribute only; NO last_* mirrors | Unit + grep-guard (no other reader of new attrs) |
| `tortoise/extractor_v2.py::_call_once` | Code seam (daemon thread) | In | ctx.run fix only; sink fires inside `complete()` with response-local data | Threaded unit (daemon spawn attribution) |
| Question key attribution | Concurrency | Internal | ContextVar set first-statement of task body; workers>1 pool workers share ambient context — never rely on propagation | Threaded unit: workers=2 no cross-question leak |
| Checkpoint/resume | State mutation (file, flocked) | Both | `usage_overhead` top-level payload: qid→buckets + `__preflight__` sentinel; `.get()`-additive; merged additively; atomic with `_upsert_failure` write; #1764 sweep on load | Unit: two-process resume fixture; merge tests |
| Report artifacts | Schema | Out | `llm_usage` per outcome (conditional projection, `rerank_pass` pattern); conditional top-level usage/cost block; methodology pricing snapshot | Unit: 258-pin holds; mock runs emit nothing |
| Mock reader/judge/extractor | Internal | In | never call complete() → no rows → no keys | Existing mock tests unchanged |

**Bug-pattern flags:** shared-mutable capture state under workers>1 (F4 #1780 class) → sink fires response-local, collector drains under lock with swap-then-aggregate; post-drain late daemon fires = documented bounded loss (never double-counted). Deadline-killed calls leave no usage (existing `deadline_aborts` counter is the bound). Retried calls (parse-ladder, `_call_with_backoff`, R2) each bill → each transport-successful response fires once.

### Verification Plan (routed: code domain, standard → unit + targeted stubbed-integration; no UX/DB/e2e/content/config)

Coverage required (from issue checklist + scope Am 11/14/17/27):
- Usage round-trip via stubbed provider response (sink fired; envelope sums correct).
- Mock runs: no `llm_usage`/cost keys; exact 258-pin holds with `llm_calls: 3` + no usage rows (emission = sink rows / overhead presence, widened per Am 21).
- Aggregation: per-question × per-run totals == Σ raw usage × pricing map; dual wire-form keys; unpriced lane loud `priced: false`; `usage_present=false` lanes visible.
- Threaded: daemon-spawn attribution (ctx.run), workers=2 no-leak, session_workers>1 live path.
- Failure paths: failed-question + breaker-open spend → report overhead (no evidence-bearing numerator); two-process resume fixture; #1764 sweep; preflight rows → overhead only.
- Existing `tests/test_longmem_*.py` + product adapter tests green unmodified; full `uv run pytest tests/ -q` at end.

**Task list (8 tasks):**

### Task 1: Capture seam — model_adapters adapters + OpenAICompatModel + extractor_v2 ctx.run

**Intent:** Give every real chat adapter a no-op-by-default `usage_sink` that fires at the response-parse point with response-local usage, so the harness can meter calls without re-implementing transports or touching measured request shapes.
**Acceptance:** Sink attribute exists on OpenRouterModel/DeepSeekDirectModel (VeniceModel inherits), OpenAICompatModel; firing is unit-tested with a stub adapter; when unset, zero behavior change (all existing adapter/forwarding/extractor tests pass); `inspect.signature(model.complete)` and isinstance(fingerprint) code untouched.
**Files:**
- Modify: `tortoise/model_adapters.py` (OpenRouterModel.__init__/complete ~line 100-155; DeepSeekDirectModel.complete ~197-256; VeniceModel inherits)
- Modify: `tortoise/models.py` (OpenAICompatModel ~29-90)
- Modify: `tortoise/extractor_v2.py` (`_call_once` ~4140-4200 — ctx.run fix)
- Test: `tests/test_longmem_usage_seam.py` (new) + existing green

**Step 1:** Write failing tests in `tests/test_longmem_usage_seam.py`:
- `test_openrouter_sink_fires_with_response_local_usage`: construct OpenRouterModel with a monkeypatched `_session` whose `.post()` returns a fake response (`r.json()` → `{"usage": {"prompt_tokens": 11, "completion_tokens": 7, "prompt_cache_hit_tokens": 5}, "choices": [...]}`); set `model.usage_sink = record`; call `complete(system=, user=)`; assert record got `(provider, model.id, usage_dict_incl_cache, True)` and content unchanged.
- `test_sink_none_noop`: same without sink → no exception, mirrors still set (existing last_* convention).
- `test_openai_compat_model_sink`: OpenAICompatModel with stubbed `urllib.request.urlopen` (monkeypatch) → sink fired with usage; no `last_*` attributes added (assert `not hasattr(model, "last_prompt_tokens")`).
- `test_call_once_ctx_run_attribution` (threaded): set a ContextVar in the calling thread, stub a model whose `complete()` asserts inside the daemon thread that the ContextVar == set value (pre-fix: fails — child context empty; post-fix: passes).
Run: `uv run pytest tests/test_longmem_usage_seam.py -v` → expect FAIL (attributes/sink missing).
**Step 2:** Implement. In each adapter `complete()`, at the existing usage-parse block: after computing `usage`/`content`, `sink = getattr(self, "usage_sink", None); if sink is not None: sink(provider=getattr(self, "provider", None), model_id=self.id, usage=usage, usage_present=bool(usage))` — getattr on ALL four sites (OpenAICompatModel/OfficialJudgeModel have no `.provider`; A1). OpenAICompatModel: parse usage from `data` first; NO last_* mirrors. Constructor sets `self.usage_sink = None` (subclasses keep super().__init__ chain — VeniceModel inherits OpenRouterModel.__init__; DeepSeekDirectModel sets its own). Do NOT add mirrors on OpenAICompatModel. In `_call_once`: capture `ctx = contextvars.copy_context()` before `Thread.start()` (add `import contextvars` — module-level or local matching the existing local `import threading` convention); wrap the `_run` body call under `ctx.run(...)` — keep the existing try/except INSIDE so `box["exc"]` semantics unchanged.
**Step 3:** Run seam tests → PASS. Run `uv run pytest tests/test_model_adapters_routing.py tests/test_extractor_reliability.py -q` + any test importing these adapters (`tests/test_extractor_v2.py`, `tests/test_longmem_runner.py -q` → suite unchanged) → PASS.
**Step 4:** Commit: `git add -A && git commit -m "feat(usage): additive usage_sink capture seam on chat adapters + _call_once ctx propagation (#2185)"`.

### Task 2: Judge + reader sink registration hooks

**Intent:** Ensure the judge transport (eval-owned, must stay byte-verbatim for benchmark comparability) can fire usage, and reader/judge expose the model instance for harness registration.
**Acceptance:** `OfficialJudgeModel` has `usage_sink` attr + fires with usage dict; `build_judge`/`build_reader` unchanged in behavior (sink None default); official judge call shape untouched (single user message, t=0, max_tokens=10, no response_format).
**Files:**
- Modify: `tools/longmem_eval/judge.py` (OfficialJudgeModel ~268-321)
- Test: `tests/test_longmem_usage_seam.py` (extend)

**Step 1:** Failing test: `test_official_judge_sink_fires` — OfficialJudgeModel with stubbed `urlopen` returning judge-shaped JSON (content "yes", usage) → sink fired with usage dict; response/content path unchanged. Run → FAIL.
**Step 2:** Implement: mirror the sink pattern in `OfficialJudgeModel.complete` (parse `usage` from `data`; fire when set; constructor default None).
**Step 3:** PASS; run judge tests (`tests/test_longmem_runner.py -k judge -q`) → green.
**Step 4:** Commit.

### Task 3: Harness collector + registration (tools/longmem_eval/usage.py)

**Intent:** Central run-level collector that buckets sink fires by (stage, provider, model) per question and provides the drain primitives used by run.py and report.py.
**Acceptance:** `usage.py` exposes: `QuestionKey` ContextVar + `set_question_key(qid)/clear_question_key()`; module-level collector singleton with `reset()` (A2); `UsageCollector` (lock-guarded; `attach(model, *, stage, provider)` walking RoutingModel.primary/fallback, RotatingModel.providers, or single adapter — assigns `usage_sink` on walked members (A6), no-op only for mocks/non-adapter objects with no `complete()` path; `drain_question(qid)` → envelope or None; `drain_overhead()` → buckets for keyless rows; `qids_with_outcomes`/`move_failed_qid_to_overhead(qid)`; `reset()`); envelope schema `{stage: {provider: {model: {"prompt_tokens": int, "completion_tokens": int, "calls": int, "usage_present": bool}}}}` plus flat `total` convenience; JSON-safe values only.
**Files:**
- Create: `tools/longmem_eval/usage.py`
- Test: `tests/test_longmem_usage.py` (new)

**Step 1:** Write tests: envelope accumulation across two stages; same (stage,provider,model) key sums; drain swaps atomically (threaded: two threads firing into one collector + drain → no lost/duplicated rows — deterministic via lock); drain-swap never-double-counted: drain → late fire into same qid → second drain returns late row once; re-drain after completed qid returns nothing; mock/no-call → `drain_question` returns None; attach walks RoutingModel members (two stub adapters WITHOUT pre-set sinks, carrying `.provider`) and wires+fires; attach with a registered provider when the model stub has NO `.provider` attr → rows land under the REGISTERED provider key (A1); attach no-op on MockReader-like object; values JSON-serializable (sanitized subset: prompt_tokens/completion_tokens/total_tokens/reasoning_tokens/prompt_cache_hit_tokens/prompt_cache_miss_tokens/prompt_tokens_details.cached_tokens) + loud warning when usage has ONLY unknown keys. Run → FAIL.
**Step 2:** Implement usage.py per contract. Bucket keys = attach-time registered `(stage, provider)` — NEVER derived from the payload provider alone (A1); the sink's payload provider is used only when the registered provider is None (model_adapters lanes). Envelope rows from sink payload `(provider, model_id, usage, usage_present)`: prompt = usage.prompt_tokens etc. (usage_present False → tokens 0 but `usage_present=False` marker; still counts a call). Cache-detail keys preserved when present. Loud warning when a usage dict contains ONLY unknown keys (dropped by the sanitizer).
**Step 3:** PASS.
**Step 4:** Commit.

### Task 4: run.py wiring — registration, question key, outcome drain, checkpoint usage_overhead

**Intent:** Connect the collector into the run: register sinks on reader/judge/extractor models, key every question, drain into outcomes/failure entries/checkpoint so no billed spend is dropped in-process or across resume.
**Acceptance:** Real run outcomes carry conditional `llm_usage`; failure entries persist usage atomically; `usage_overhead` rides the checkpoint (write/merge/load + #1764 sweep); mock runs and retrieval-only runs produce none of the above; `--workers` 1 and >1 both correct (threaded test); preflight rows → overhead.
**Files:**
- Modify: `tools/longmem_eval/run.py` (registration after builds ~5081-5096; question key first-statement of per-question task body; drain at outcome construction ~3560-3610 incl. breaker-open dropped-outcome path ~3717-3738; `_upsert_failure` ~883 (atomic usage param); `_save_checkpoint`/`_write_checkpoint_locked`/`_merge_checkpoint`/`_load_checkpoint` (~1722-2140) for the `usage_overhead` payload + #1764 sweep; failure-entry construction ~819 for drain-to-overhead; collector init before `run_preflight` ~5123; pass `usage_overhead` to `outcomes_to_report` ~3929)
- Test: `tests/test_longmem_usage.py` (extend) + `tests/test_longmem_runner.py` (new fixtures, no edits to existing assertions)

**Step 1:** Failing tests (unit-level where possible; extract the run-loop bookkeeping into testable helpers where the function is too monolithic — keep helpers module-private in usage.py or run.py):
- `test_outcome_drain_llm_usage`: after a question's ingest/reader/judge phases against a sink-equipped stub extractor model + stub reader/judge, the outcome dict carries the expected `llm_usage` envelope; breaker-open dropped outcome likewise; failed question (retryable → failure entry) → NO outcome but entry/overhead carries the usage.
- `test_failure_entry_atomic_usage`: `_upsert_failure`-path helper persists entry + usage in one write (assert on written file).
- `test_checkpoint_usage_overhead_roundtrip`: save with overhead → load → merged additively across two segments; pre-fix checkpoint (no key) loads fine (`.get`).
- `test_resume_gate_sweep`: #1764-skipped outcome's llm_usage swept to overhead accumulator.
- `test_workers2_no_cross_question_leak` (threaded).
Run → FAIL.
**Step 2:** Implement. Registration: after `build_reader`/`build_judge`/extractor-model build in `run_evaluation`/`run_main`, `collector.attach(reader._model, stage="reader", provider=reader.provider)` (skip MockReader), `collector.attach(judge._model, stage="judge", provider=judge-provider)`, `collector.attach(extractor_model, stage="ingest", provider=None)` (member adapters carry own provider). Question key: first statement of the per-question worker body. Drain: at outcome construction (success + breaker-open dropped), `usage = collector.drain_question(qid)`; if not None add `outcome["llm_usage"] = usage`. Failure terminal path: `collector.move_failed_qid_to_overhead(qid)` and persist with the failure entry atomically. Checkpoint: extend payload + merge (additive per qid + sentinel `__preflight__`), load returns it; #1764 sweep at load. Preflight: collector initialized before `run_preflight`; after run, keyless rows moved to overhead at save/report.
**Step 3:** PASS targeted; `uv run pytest tests/test_longmem_usage.py tests/test_longmem_errors.py -q` green.
**Step 4:** Commit.

### Task 5: costing.py — versioned pricing map + pricing engine

**Intent:** Convert raw token buckets to USD at report time with a documented, versioned pricing map that is honest about unverified prices and unpriced lanes.
**Acceptance:** `costing.py` exposes `price_usage_envelope(envelope) -> (cost_usd, priced: bool, breakdown)`; map covers every reachable eval lane with documented source + date; out-of-map lane → `priced=False` loud marker (never crash/`estimated`/silent $0); cache-detail handling documented (cache-hit priced at reduced rate ONLY where verified; else prompt leg flagged `estimated`); reasoning tokens not double-added (completion_tokens covers them — methodology note).
**Files:**
- Create: `tools/longmem_eval/costing.py`
- Test: `tests/test_longmem_costing.py` (new)

**Step 1:** Web-verify current list prices + usage-detail fields for the reachable lanes (2-3 targeted `web_search` calls during this task): openrouter `deepseek/deepseek-v4-flash` & `deepseek-v4-pro` per-1M prompt/completion; deepseek direct (`deepseek-v4-flash/pro`) API prices + `prompt_cache_hit_tokens` semantics; openai `gpt-4o-2024-08-06`; venice deepseek-v4-flash. Record findings with source + date in the module docstring and map entries (`source`, `verified_on`, `estimated: bool`).
**Step 2:** Failing tests: known-lane price math (`Σ prompt×rate/1e6 + completion×rate/1e6` rounded to 6dp); dual wire-form keys (`deepseek/deepseek-v4-flash` and `deepseek-v4-flash` both resolve); out-of-map lane → `priced=False` with no exception; cache-hit-priced lane math where verified.
**Step 3:** Implement module (pure functions; map = module constant dict keyed `(provider, model_id_bare_or_prefixed)` → `{"prompt_per_1m": float, "completion_per_1m": float, "source": str, "verified_on": str, "estimated": bool}`; `provider` normalization table for reader/judge lanes (openrouter/deepseek/openai/gemini names from `_PROVIDERS` keys in tortoise/ingest.py); model-id family fallback: exact match → family match → unpriced).
**Step 4:** PASS; commit.

### Task 6: Report — conditional projection + usage/cost block + methodology

**Intent:** Surface per-question and per-run token/cost aggregates in the saved report, conditionally (mock reports byte-identical), with reproducible methodology and honest overhead/coverage semantics.
**Acceptance:** Outcomes projection emits `llm_usage` only when present (conditional `rerank_pass` pattern, run.py ~4140); `report["usage"]` appears iff ≥1 outcome has usage OR overhead/preflight rows exist (Am 21) — exactly ONE new top-level key when emitting, none when not (A7); contains per-question aggregates keyed by qid, per-run totals, overhead section (Σ usage_overhead incl. preflight + dropped/breaker + failed spend — Am 20 reclassification), per-data-point cost over evidence-bearing outcomes (`evidence_written>0`) with coverage marker when 0<n_with_usage<n_outcomes; methodology records pricing-map snapshot (git sha + `verified_on`); pricing does NOT mutate outcome `llm_usage`; mock 258-pin + all existing report tests unchanged.
**Files:**
- Modify: `tools/longmem_eval/run.py` (projection allowlist conditional entry; forward `usage_overhead`/`usage` to outcomes_to_report ~4028/3929)
- Modify: `tools/longmem_eval/report.py` (build_report aggregation ~682+; summary assembly; methodology)
- Test: `tests/test_longmem_costing.py`/`tests/test_longmem_usage.py` extend; new report fixtures

**Step 1:** Failing tests: report from outcomes-with-usage + overhead → conditional top-level block with correct per-question/per-run totals, per-point cost over evidence-bearing subset, overhead separated; report from usage-free outcomes → no new keys (258-pin shape); mixed outcomes (some with usage) → coverage marker; unpriced lane → `priced: false` in block; pricing does NOT mutate outcome `llm_usage` (assert deep-copy equality before/after build_report).
**Step 2:** Implement per Acceptance. Projection: append `"llm_usage"` to the projection tuple but emit via the conditional pattern (build dict, then `if o.get("llm_usage") is not None: row["llm_usage"] = ...`). report.py: aggregation pure function over outcomes + `usage_overhead` + pricing map → block dict; wire into build_report output only when non-empty; methodology snapshot.
**Step 3:** PASS targeted + full `tests/test_longmem_runner.py` (pin + fixtures) green.
**Step 4:** Commit.

### Task 7: Mock invariance + failure-path verification sweep

**Intent:** Prove mock/no-call runs are byte-identical and all mandated failure-path/threaded/reconciliation behaviors hold.
**Acceptance:** All scope verification items green: 258-pin; mock run report key set identical pre/post; `llm_calls ≥ usage rows` reconciliation test; failed-question → overhead fixture; breaker-open → overhead-section fixture; two-process resume fixture; #1764 sweep; preflight-only overhead; threaded daemon + workers=2 tests; usage_present=false lane visible; unpriced loud.
**Files:**
- Test: extend `tests/test_longmem_usage.py`, `tests/test_longmem_costing.py`, `tests/test_longmem_runner.py` fixtures

**Step 1:** Add the tests (list in Acceptance) against current code → RED where behavior missing (fix gaps in Tasks 4/6 code as needed).
**Step 2:** Full targeted suite: `uv run pytest tests/test_longmem_usage.py tests/test_longmem_costing.py tests/test_longmem_usage_seam.py -v` → PASS.
**Step 3:** Commit.

### Task 8: Full-suite verification + docs + mock smoke report diff

**Intent:** Prove no regression anywhere (product adapter tests especially) and document the new artifact surfaces + boundaries.
**Acceptance:** `uv run pytest tests/ -q` green (or pre-existing failures unchanged — record baseline first); mock smoke run before/after produces identical report top-level shape; methodology/README notes updated (out-of-coverage producers full_context/spot-check; COGS forward-only; usage semantics notes).
**Files:**
- Modify: `tools/longmem_eval/README.md` (+ report schema notes)
- Modify (if present): docs section listing longmemeval report fields

**Step 1:** Baseline: `uv run pytest tests/ -q` on the worktree BEFORE changes were made? (baseline recorded at task start — if not, record now from main via `git stash`-free: run in the OTHER checkout is impossible (guard); accept: run suite now, note failures, re-run after Task 7 — delta is what matters).
**Step 2:** Mock smoke: run the existing mock-integrity run command (from README/run_protocol smoke) in a scratch dir; assert report top-level keys == pre-change set (diff against a main-branch-generated report artifact if one exists in `.longmemeval_cache/`; else rely on 258-pin + key-set assertions already green).
**Step 3:** Update README: usage/cost block schema, pricing map source/verification process, coverage marker semantics, out-of-coverage producers (full_context/spot-check), COGS forward-only note.
**Step 4:** Full suite → green/no-new-failures. Commit.
**Step 5:** VERIFY gate: re-run `tests/test_longmem_*.py` + product adapter tests; assemble proof for the #2185 checklist.

---
## Plan-Review Fold-In (cycle 1 — 2 independent verifiers, 0 P0; binding resolutions)

The resolutions below are PART of the plan and override/refine the task text above where they differ.

- **A1 (P1) Provider-less seam keying.** Sink fire passes `provider=getattr(self, "provider", None)` on ALL four sites (OpenAICompatModel/OfficialJudgeModel have no `.provider` — verified). The collector NEVER keys buckets from the payload provider alone: bucket key = attach-time registered `(stage, provider)` from `attach()`; the payload provider is used only when the registered provider is None (model_adapters lanes carry their own). Consequence: reader/judge rows land under the registered provider (openai for official judge) even though the model class has no provider attr. Task 3's attach test MUST use stubs WITHOUT `.provider` and assert rows land under the registered provider.
- **A2 (P1) Collector lifecycle.** `usage.py` hosts a module-level collector singleton with `reset()`; lazy-init on first sink attach. `_run_main` (run.py ~5090-5124) inits + registers reader/judge/extractor sinks BEFORE the `run_preflight` call (~5123); `run_evaluation` + `_load_checkpoint` consume the SAME singleton (import) — no parameter threading, no second collector. In-process double-run guard per Am 19 (reset at run_evaluation entry only when a prior run's drains are complete). Task 4 gains one integration-style failing test: preflight ping + one question loop → preflight rows land in report overhead.
- **A3 (P2) Judge provider source.** `build_judge` sets `self.provider = _resolved_provider` on the returned `LLMJudge` — `_resolve_provider()` (reader.py:135) returns the provider-name STRING, so NOT `[0]` (first-char bug). `LLMJudge.__init__` (judge.py:324) gains `provider: str | None = None` (mirrors `LLMReader`, reader.py:519). Registration uses `provider=judge.provider`. Never re-derive provider in run_main; do NOT hard-code the judge lane as openai — build_judge calls `_resolve_provider()` WITHOUT `named=provider`, so in multi-key envs the judge lane resolves to the priority-first configured provider (pricing tests must not assume openai).
- **A4 (P2) Kill-9-window read-back.** `_load_checkpoint`: when a failure entry carries usage totals for a qid absent from the `usage_overhead` payload, fold the FULL entry totals into the in-memory overhead accumulator; when the payload holds a partial amount, fold ONLY the shortfall (entry − payload per qid/model bucket) — never the overlap (idempotent on resume). Task 7 fixture: checkpoint whose failures entry has usage but whose `usage_overhead` key is absent → resumed report shows the spend in overhead.
- **A5 (P2) session_workers>1 live path.** Explicit Task 7 fixture `test_session_workers_live_path_attribution` (Am 17c — question pool `workers` ≠ ingest `session_workers`; live def ingest_v2.py:899 is sequential; attribution regression there would pass unnoticed without this).
- **A6 (P3) attach wires the sink.** `collector.attach` ASSIGNS `usage_sink` on each walked member (RoutingModel `.primary`/`.fallback`, RotatingModel `.providers`, or plain adapter); no-op ONLY for mocks/non-adapter objects with no `complete()` path (MockReader/MockJudge). Task 3 attach test uses stubs WITHOUT pre-set sinks → passes only if attach wired them. (Amends Task 3 Acceptance sentence "no-op ... for models lacking sink attr" — after A6 a sink-less chat adapter is WIRED, not skipped.)
- **A7 (P3) Canonical report key.** One new top-level key: `report["usage"]` (no `cost`-block alternative), nested `{per_question: {qid: {...}}, totals: {...}, overhead: {...}, cost: {...}, coverage, priced, pricing: {map_version, git_sha, verified_on}}`. Pin tests assert: exactly ONE new top-level key when emission is on; the byte-exact 16-key set when off (258-pin).
- **A8 (P3) contextvars import.** extractor_v2.py needs `import contextvars` (module-level or inside `_call_once` matching the existing local `import threading` convention) — Task 1's "3 lines" becomes 4 incl. import.
- **A9 (P3) Pinned smoke command.** Task 8 Step 2 uses exactly: `uv run python -m tools.longmem_eval.run_protocol smoke --mock` (writes `smoke.report.json`; run_protocol.py 816-833). `--mock` mocks ONLY reader/judge — the extractor is REAL, so the post-change smoke report GAINS the usage key: assert exactly +1 top-level key (report["usage"]), not equality.
- **A10 (P4) Extra tests:** VeniceModel `usage_sink` inheritance one-liner (Task 1); drain-swap never-double-counted test — drain → late fire same qid → second drain returns late row once, re-drain after completed qid returns nothing (Task 3); keep-both retry-failed fixture (Task 7, Am 14); loud note when a usage dict contains ONLY unknown keys (Task 3 sanitizer).

---
**Round-2 code-review amendments (PR #2250 review cycle; all binding):**

- **R1 (P1→fixed) ruff clean:** the review round surfaced 20 ruff violations on the head (CI lint red) — fixed (E741 rename, I001 sort, UP037, B009 constant-getattr, RUF100, RUF059 unused unpacks, F841/B007 dead scaffold dropped) + a regression-test suite appended proving each fix.
- **R2 (P2) exception-safe seam:** `_emit_usage_sink` wraps the sink fire in `contextlib.suppress(Exception)` (models.py — shared by all four fire sites) — a raising/poisoned metering observer can never flip a valid call into a failure/retry; `_sanitize_usage` hardens to total-failure semantics: non-dict usage → {} (never `.items()` crash), every accepted scalar finite + |v| ≤ 1e300 via `_bounded` (mirrors report._numeric), warning sort is key-stringified.
- **R3 (P2) merge/fold union keys:** `_merge_buckets` + `fold_replica` sum over the UNION of scalar keys — `reasoning_tokens` / flattened `prompt_tokens_details_cached_tokens` survive the overhead store / checkpoint folds (a fixed-key list silently dropped them).
- **R4 (P2) cumulative A4 replica:** `drain_to_overhead` now returns the FULL cumulative qid envelope (payload rows already in the overhead store + the just-drained rows). A `--retry-failed` re-attempt that burns FEWER tokens than the persisted payload folds its exact un-saved delta on the next resume (shortfall fold unchanged — idempotent).
- **R5 (P2) key-clear on question exit:** run.py clears the question-key ContextVar after each terminal drain (success/breaker/failure) — a later main-thread straggler call lands under `__no_key__` overhead, never on the last question's evidence bucket (daemon late-fires keep their copy_context snapshot).
- **R6 (P2) mixed-key judge env priced:** PRICING_MAP openrouter gains `gpt-4o-2024-08-06` (OpenRouter list = OpenAI list + ~5% fee; `estimated: True`; bare-id and `openai/`-prefixed spellings both resolve) — the judge lane served via the openrouter transport in a both-keys env no longer reports $0/priced:false. `PRICING_MAP_VERSION` bumped to 2026-09-04 with the map change (module contract).
- **R7 (P2) conservative unknown-spend disclosure:** bucket `usage_present` stays AND (any usage-less row → lane unpriced, never silently priced) AND the bucket/lane now carry `calls_without_usage` (count of rows whose response had no usage block) so the reader sees exactly how many calls were unknown. Costing `_price_lane` + report `_bounded_int` degrade poison token values to 0 (never OverflowError/ValueError at report assembly).
- **R8 (P3) stable per-question `estimated`:** report row emits `bool(breakdown.estimated)` (was list-or-False type-unstable).
- **R9 (P3) plan acceptance reconciliation:** Task-7 acceptance (a) `llm_calls ≥ usage rows` fixture-level reconciliation pin added to the report suite (`test_llm_calls_never_below_usage_rows` — both sides fixture-derived, so it pins the COUNTERS' CONSISTENCY semantics only; a full-teeth cross-layer check needs a live multi-worker run, deferred with the A5 fixture). A5's `session_workers>1` LIVE fixture was NOT added — the ingest def is sequential in every env this plan exercises (def ingest_v2.py:899; fixture would require a live multi-worker provider harness with no unit-test surface); the drift pin above plus the existing `_call_once` copy_context tests cover the attribution contract. Success-path drain→save kill window (drain happens before the trailing checkpoint write) remains a DOCUMENTED bounded-loss case (resume re-runs the question and re-bills; report counts the re-run only) — same status as the documented post-drain late-fire bound in the README overhead semantics.

---
**Runtime prereqs:** none new (no deps, no DB schema, no env vars). Real-provider verification runs need existing provider keys (out of scope for CI; unit tests are stubbed).
