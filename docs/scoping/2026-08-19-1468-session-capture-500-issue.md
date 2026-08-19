**O/I/T:**
- Objective: `POST /v1/sessions` session capture must return 200 and persist the session + extracted Points, so agents can capture sessions into hosted graphs.
- Indicators: (1) hosted-e2e `test_10_session_capture::test_session_capture_and_extraction` passes; (2) a manual capture round-trip (register → capture → list sessions → points exist) succeeds.
- Targets: (1) `RUN_HOSTED_E2E=1 uv run pytest tests/e2e/hosted/test_10_session_capture.py -q` → 4 passed; (2) no 500 on the capture endpoint.

**Team:** epistemic-team
**Complexity:** standard

**Epic:** standalone
**Research:** none — see reproduction below
**Depends on:** none
**Components:** tortoise/hosted_api.py (capture_session), tortoise/sdk.py (_extract_session_v2), tests/e2e/hosted/test_10_session_capture.py

### Context
**Pre-existing failure, NOT introduced by #1460/#1467.** `test_10_session_capture` fails **identically on the base commit** (origin/main `61b6a071`, verified via a base-commit worktree: `1 failed, 3 passed`). It was surfaced by PR #1467's hosted-e2e CI run — main's deploy workflow does not run hosted-e2e on merges, so the failure slipped onto main undetected.

**Symptom:** `POST /v1/sessions` returns **HTTP 500 Internal Server Error** (no detail body). 48/49 hosted-e2e tests pass; only `test_10_session_capture_and_extraction` fails. The other session-auth tests (incl. the ES256 claim suite) pass.

**Diagnostic leads (observed):**
- Server boot log: `event retention sweep failed: No module named 'tortoise.registry'` — `tortoise/hosted_api.py:280` imports `from tortoise.registry import registry_sdk` but `tortoise/registry.py` does not exist anywhere in git history (dead import from the merge-train clobber fix `20f86d99`; try/except-wrapped so it only warns at boot). Possibly related, possibly not — the capture path doesn't touch it directly.
- `sdk._extract_session_v2()` works in isolation with `TORTOISE_SESSION_LLM_MOCK=1` (EXTRACT OK: 1 point) — the 500 is elsewhere in the hosted capture flow (quota check, Event node stamping, or the per-turn graph writes).
- Capture handler flow: quota estimate → Session MERGE → per-turn Point MERGEs → `_extract_session_v2` → `create_event` (sessionCaptured) → eventId stamping → commit.

### Research Needed
**RESOLVED — see ROOT CAUSE below.**


### ROOT CAUSE (confirmed 2026-08-19)
`tortoise/sdk.py:1926` (`_extract_session_v2`, the #1350 5-stage extractor — the DEFAULT capture extractor) does:

```python
from tests.model_adapters import MODELS   # ← PRODUCTION CODE imports a TEST-ONLY module
```

`tests/model_adapters.py` does not exist in the production image (Fly /app) and is not importable from the hosted server subprocess → `ModuleNotFoundError` → **HTTP 500** on every `POST /v1/sessions` capture (reproduced in-process with `raise_server_exceptions`: `ModuleNotFoundError: No module named 'tests.model_adapters'` at `sdk.py:1926`).

- **Production impact: P0** — every session capture 500s on the hosted platform (v2 extractor is the default since #1350). The e2e test was correctly catching a live production bug; main's CI doesn't run hosted-e2e on merges, so it slipped.
- **Why my earlier isolated `_extract_session_v2` probe passed:** plain `python` from the repo root puts cwd on sys.path, making the namespace-package `tests/` importable — masking the bug.
- **Blast radius:** single import site (`grep` confirms only `sdk.py:1926` imports `tests.model_adapters`; `extractor_v2.py:37` only mentions it in a docstring).
- **Secondary (same class):** `tortoise/hosted_api.py:280` — `from tortoise.registry import registry_sdk` — `tortoise/registry.py` does not exist (dead import, try/except-wrapped → warn-only at boot, but same broken-import class).

### Fix direction
1. `sdk.py:1924-1926` — remove the `tests.model_adapters` import; replace the `MODELS["deepseek-flash"]()` fallback with a production-safe adapter construction (extend the in-module `_model_adapter` / `_Compat` BYOK path or add a production `OpenRouterModel`), preserving the uncapped-output-budget semantics the flash fallback provided.
2. `hosted_api.py:280` — remove the dead `from tortoise.registry import registry_sdk` import (comment says "not used").
3. Regression guard: a static test asserting no `tortoise/` module imports `tests.*` (mirror the `test_cross_subdomain_cookie_sync.py` static-test pattern) — the guard that would have caught this.
4. Verify: `RUN_HOSTED_E2E=1 uv run pytest tests/e2e/hosted/test_10_session_capture.py -q` → 4 passed; unit tests for the adapter fallback.

### Verification Checklist
| Surface | Test Layer | Expected Verification |
|---------|-----------|----------------------|
| POST /v1/sessions capture | e2e | test_10 passes (4/4) |
| Event node stamping | e2e | capture produces a sessionCaptured Event with eventId provenance |
| extraction | unit | _extract_session_v2 mock-mode round-trip |

### Complexity (domain-aware)
| Domain | Rating | Rationale |
|--------|--------|-----------|
| Architecture | standard | Data-plane capture path, multi-step (quota, graph writes, extraction, events) |
| Research | standard | Root cause not yet located |

### Fractal Fields
- **Level:** task
- **OIT:** see above
- **E2E:** TBD
- **Verification:** TBD
- **Wiring:** TBD

### RESOLUTION (2026-08-19, PR fix/1468)

**Design choice — fallback adapter (requirement 1):** extended the in-module
`_model_adapter`/`_Compat` BYOK adapter to accept `max_tokens: int | None`
(`None` = UNCAPPED — the cap is omitted from the request body entirely), and
the v2 DEFAULT adapter is `_model_adapter("deepseek/deepseek-v4-flash",
max_tokens=None, temperature=0.0)`. This delivers the exact semantics of the
removed tests-only `MODELS["deepseek-flash"]()` (OpenRouterModel with
max_tokens=None), with zero new files and no tests import.

**Reviewer-driven corrections (code-review gate, 3 reviewers, 0 P0/P1):**
- *Fallback reachability:* the original `_default_byok_model() or
  _model_adapter(...)` form left the uncapped branch dead in production
  (`_Compat` is always truthy) — v2 would have run the capped 4000-token
  adapter and still truncated. Reworked so the uncapped adapter is the LIVE
  default; an explicit `TORTOISE_EXTRACT_MODEL` override keeps the bounded
  4000-token default (summary/construct posture, T13 #1272).
- *Mock normalization:* the gate and mock selection now use the shared
  `_session_llm_mock_enabled()` helper (strip + lower), matching the sibling
  gates — a padded env value (" 1 ") previously diverged outer vs inner gate
  → ValueError → the same 500 class.

**Rejected alternatives:**
- *Keep the tests import* — impossible: `tests/` is absent from the
  production image (Fly /app); it was the 500 itself.
- *Move `tests/model_adapters.py` into `tortoise/`* — drags a test-only
  module (OllamaModel, judge tunings, unused model registry) into production
  surface; larger diff, unclear ownership.
- *New production `OpenRouterModel` class in tortoise/* — viable but
  duplicates `_Compat`'s contract; extending the existing in-module adapter
  is the smaller, single-source change.

**Second defect found in the same path (required for e2e 4/4):**
`_extract_session_v2`'s provider gate checked ONLY the three provider keys and
raised ValueError even when `TORTOISE_SESSION_LLM_MOCK=1` — contradicting its
own error message and the hosted handler (`_llm_provider_available` counts the
seam as configured). The hosted-e2e subprocess scrubs provider keys and runs
the seam, so capture 500'd on the gate BEFORE reaching extraction (this was
the residual 1-failed after the tests-import fix; the #1460 investigation had
seen ModuleNotFoundError only because its in-process repro inherited dev-shell
keys). Fixed: the mock seam now satisfies the gate (matches the v1 path's
`_build_session_llm_extractor` semantics).

**Follow-ups (pre-existing / adjacent, out of scope for this P0):**
- v2 provider gate checks OPENROUTER/DEEPSEEK/OPENAI but hosted's outer gate
  (`_llm_provider_available`) also counts GEMINI — a GEMINI-only hosted
  deployment passes the outer gate then 500s (fail-closed direction, no
  security issue). Proper fix needs a hosted-layer posture decision
  (503 for non-OpenRouter v2 providers), not just the sdk gate.
- With the mock seam now honored, a misconfigured PROD deploy that leaves
  `TORTOISE_SESSION_LLM_MOCK=1` writes fabricated points silently instead of
  failing loudly — a boot-time warning when the seam is active in hosted
  would surface this in Fly logs.

**Verification (all green):**
- `RUN_HOSTED_E2E=1 uv run pytest tests/e2e/hosted/test_10_session_capture.py -q -p no:randomly` → **4 passed**
- `tests/test_no_tests_imports_in_production.py` (new static guard) → 1 passed
- `tests/test_capture_session.py` (incl. 2 new #1468 tests) + `tests/test_value_extractor.py` (incl. uncapped-bounds test) → 65 passed
- `tests/test_extractor.py test_session_extraction_modes.py test_extractor_doc.py test_extractor_priors.py test_capture_session.py` → 114 passed
