# Plan — #1676: offload query embedding + search off the event loop (launch capacity)

> task-workflow-standard · complexity: standard · team: epistemic-team · launch-week ship

## Problem
Single uvicorn worker; `/v1/search` (hosted_api.py:2509) + `/v1/topics/{topic}/summary` (hosted_api.py:2548) call blocking SDK methods synchronously in async handlers. `model.encode([query])` runs inline (sdk.py:9586, CPU ~10-50ms for bge-small) + 3 DB legs block the event loop → requests serialize on CPU. Launch target: 15 concurrent users (#1656).

## Approach
Offload the blocking SDK calls to worker threads via `asyncio.to_thread` (46 in-file precedents). Torch encode releases the GIL, so concurrent searches overlap on the default thread pool (`min(32, cpu+4)` threads) while the event loop stays free. Workers stay at uvicorn default 1 (zero infra change — workers=2 machinery deferred to #1677 with a data-driven trigger).

### Integration Surface Map
| Surface | Test Layer | Expected Verification |
|---------|-----------|----------------------|
| `/v1/search` handler (hosted_api.py:2509) | integration (handler-level) | to_thread offload; concurrent searches overlap (barrier test) |
| `/v1/topics/{topic}/summary` handler (hosted_api.py:2548) | integration | to_thread offload + kwargs forwarding + sdk.close; smoke test (was zero-coverage) |
| SDK per-request lifecycle | integration | `finally: sdk.close()` in both handlers (siblings dream/dream_health pattern) |
| EmbeddingModel.encode thread-safety | unit | 2+ threads encode concurrently → correctness (cosine≈1); gated on real model |
| Degrade path (no embedder) | regression | explicit assert: stub get→None / encode raising → 200 + FTS results (`_vec_reason="encode_failed"` swallow, sdk.py:9588-9590) |

### Test Strategy
1. **Concurrency test** (handler-level — TestClient serializes). Exact wiring (plan-verify P1 + round-2 P1):
   - Call the handler directly with ALL FastAPI markers passed explicitly: `asyncio.gather(search(q1, limit=10, team=TEST_TEAM), search(q2, limit=10, team=TEST_TEAM))` — the raw `Depends`/`Query` markers pass through as objects; an unresolved `Query` `limit` TypeErrors at the sdk.py:9541 range check BEFORE the encode (call-counter == 0, false fail). `team` + `limit` both explicit.
   - Temp-DB redirect: reuse `_patch_tortoise_sdk_init` (the #1502 cross-test contamination fix — clears `_FALLBACK_KEEPALIVE` + redirects to a temp DB), NOT raw `_make_sdk` (which resolves to the shared `/tmp/tortoise.db`).
   - Sequential pre-warm before the gather (redislite split-brain avoidance, concurrency_harness.py:23-27). **Install the barrier stub AFTER the pre-warm** (a pre-warm routed through the stub would consume/break the shared barrier and collapse the wall-time discriminator).
   - Barrier stub: `monkeypatch.setattr(EmbeddingModel, "get", stub)` — stub `.encode` = `time.sleep(0.5)` then `barrier.wait()` then returns `np.zeros((1,384))`. **ONE shared `threading.Barrier(2, timeout=3)` created at test scope** (a per-call Barrier makes both threads wait on separate barriers → false fail). Call-counter == 2 (informational); **the wall-time bound is the discriminator**: `0.4s ≤ t < 1.5s` (measured pass-path 0.52s warm / 0.91s cold — the cap must clear the cold path to avoid flaking green code; regression ≈ 4.55s, a ~3× margin).
   - Failure mechanism: BrokenBarrierError is SWALLOWED by the encode try/except (sdk.py:9583-9590) → degrade 200, NOT 500. With offload REMOVED, both encodes run sequentially on the loop; the FIRST wait times out (3s), leaving the barrier BROKEN, so the second wait raises BrokenBarrierError in µs → regression ≈ 0.5+3+0.5 ≈ 4.55s. Use `asyncio.wait_for(gather, timeout=15)` (4.55s regression must not race a 10s cap). The call-counter stays == 2 in BOTH cases — it is NOT the discriminator.
   - Tests are SYNC `def test_*` using `asyncio.run(...)` (pyproject has no asyncio_mode → strict mode; an unmarked `async def` runs as sync → coroutine never awaited → vacuous pass).
   - Loop-responsiveness: trivial `asyncio.sleep(0.05)` task completes while encodes are barrier-blocked (with offload); times out if the loop is blocked.
   - Use the in-file precedent: `test_last_used_at_set_on_successful_auth` (test_hosted_api.py:296-340) — sync + `asyncio.run` + `_patch_tortoise_sdk_init` + direct handler call.
2. **topic_summary smoke test** (plan-verify P1 — was zero behavioral coverage): call `topic_summary` directly with ALL markers explicit — `topic_summary(topic, max_seeds=50, max_hops=1, include_relationships=True, team=TEST_TEAM)` (unresolved `Query` objects bind-fail as cypher params) + `_patch_tortoise_sdk_init`, assert 200 + dict shape + sdk.close idempotency.
3. **Degrade-path assert**: stub `EmbeddingModel.get → None` (and encode raising) → search returns **200 (not 500)**; on an empty temp DB FTS returns `[]`, so the meaningful assert is the status + `count == 0` (or seed one Point via the SDK first if FTS-hit coverage is wanted).
4. **Thread-safety test**: 2+ threads encode concurrently → same input → cosine ≈ 1 (correctness). Gate via `_require_model` pattern (test_cross_lens.py:361 — copy the module-level `_MODEL_CACHE_DIR` + `_require_model` helper into the test file if it lands in test_hosted_api.py) + `pytest.importorskip("sentence_transformers")`. No DB needed — pure encode.
5. **Existing suites**: `tests/test_hosted_api.py` (registered, has TEST_TEAM + helpers), `tests/test_cross_lens.py`, `tests/test_search_engine.py` + `test_search_engine_gaps.py` + `test_search_promoted_fields.py` + `test_index_surfacing.py` (the FTS surface — Task 1 doesn't touch search-engine internals but these guard the path). All tests go in the EXISTING test_hosted_api.py (integrity-safe; a new file would need manifest registration).

### Verification Plan (test-routing)
- Architecture: standard → integration layer (handler-level concurrency + thread-safety), regression suite
- Config: low → no config change; skip config-validation
- E2E: not required (no new user journey; onboarding E2E unaffected — workers stay 1)

## Tasks

### Task 1: Offload /v1/search + /v1/topics/{topic}/summary + close SDKs
**Acceptance:** hosted_api.py:2509 and 2548 wrap the SDK calls in `await asyncio.to_thread(...)`; both handlers have `finally: sdk.close()`; `python3 -m py_compile tortoise/hosted_api.py` clean; no conflict markers.
- `search`: `results = await asyncio.to_thread(sdk.tortoise_fts_query, q, limit=limit)` in the existing try; add `finally: sdk.close()`.
- `topic_summary`: `await asyncio.to_thread(sdk.topic_summarize, topic, max_seeds=..., max_hops=..., include_relationships=...)`; add `finally: sdk.close()` (kwargs forwarded exactly; returns materialized dict so close-before-serialize is safe).

### Task 2: Concurrency + smoke + degrade tests (in tests/test_hosted_api.py)
**Acceptance:** (a) concurrency test proves 2 concurrent searches overlap on CPU — wall-time ≈ 1× encode not 2×, via the barrier-stub + ALL-markers-explicit (`limit=10, team=TEST_TEAM`) + `_patch_tortoise_sdk_init` wiring, sync-form with asyncio.run; fails in ~4.55s via the wall-time bound (not hang, not a 500) if the offload is removed; (b) topic_summary smoke test (all markers explicit, 200 + dict shape + close idempotency); (c) degrade-path assert (get→None → 200 not 500).
- Use the in-file precedent (test_hosted_api.py:296) for asyncio.run + direct handler call.
- Call-counter == 2; wall-time `0.4s ≤ t < 1.5s`; Barrier(2, timeout=3); asyncio.wait_for(gather, timeout=15); loop-responsiveness sleep assert.

### Task 3: Encode thread-safety test
**Acceptance:** 2+ threads encode concurrently → cosine ≈ 1, gated on real-model availability (skips on embedder-less CI, no false-pass).
- `_require_model` pattern + `pytest.importorskip("sentence_transformers")`; copy the module-level helpers if landing in test_hosted_api.py; no DB needed.

### Task 4: Full regression + commit
**Acceptance:** the enumerated suites (test_hosted_api.py, test_cross_lens.py, test_search_engine.py, test_search_engine_gaps.py, test_search_promoted_fields.py, test_index_surfacing.py) green; `tools/ci_selection.py --integrity` green; ruff clean on changed files; commit via commit-workflow.
