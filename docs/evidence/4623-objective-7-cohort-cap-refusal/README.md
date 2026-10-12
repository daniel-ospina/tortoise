---
title: "Objective-7 exit artifact — the cohort cost cap refuses an over-cap cohort (#4623)"
type: engineering
domain: platform
doc_status: live
created: 2026-10-12
subjects.team: epistemic-team
issue: "#4623"
aboutSubjects: epistemic-team
aboutObjects: cohort cost cap, tests/test_cohort_cost_cap.py, tortoise/cohort_cost.py, tortoise/hosted_api.py
---

# Objective-7 exit artifact — the cap refuses an over-cap cohort

**The done-state clause this artifact discharges** (objective 7, instrumentation & cost cap):

> *the cap **refuses** an over-cap cohort.*

**Instrument of record:** `tests/test_cohort_cost_cap.py` — 21 tests, all driving the **real**
hosted admission path (`hosted_api._capture_session_impl`) over an armed cap and **real** ledger
rows written through `metering.record_capture_usage`.

**The test that carries the clause:** `test_over_cap_cohort_capture_refused_402_no_extraction_no_write`
(`tests/test_cohort_cost_cap.py:246`).

Measured at `feat/4623-tm@5fb41c7d536f107feb3242375c6749cd505c3237` (`== origin/main`) on
2026-10-12, on the local **embedded (carve-out)** lane.

## 1. What the test asserts — all four things the clause needs

| assertion | line | what it pins |
|---|---|---|
| `r.status_code == 402` | `:263` | the refusal happens **at the point of spend** (the `POST /v1/sessions` response) |
| `detail["code"] == "cohort_cost_cap"` | `:272` | the refusal is **legible to the caller** and is **not** `quota_exceeded` — a spend cap read as a plan limit would send the user to buy a plan that cannot lift it (#4614) |
| `capture_env.extraction_calls == []` | `:284` | refused **before** any extraction is dispatched |
| `_session_count(COHORT_ORG) == 0` | `:286` | **nothing is written** |

The cap is **armed for real**, not read from a config: the fixture sets
`TORTOISE_COHORT_COST_CAP_USD=5.0` + `TORTOISE_COHORT_COST_SINCE` (`:216-221`), provisions registry
orgs carrying a real `created_at`, and writes a real `:MeteringRecord` row at `CAP_USD + 1.0`
(`_spend` → `metering.record_capture_usage`). A cap that is *configured* but not *enforced* is the
false PASS this file exists to prevent.

## 2. Measurement — GREEN

```bash
env TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_cohort_cost_cap.py -q -p no:cacheprovider
# 21 passed, 1 warning in 204.43s
```

All 21 pass — the refusal tests **and** the three negative controls that keep the gate from being an
over-eager one (§4).

## 3. Measurement — mutation-verified (removing the check turns it RED)

A claimed mutation is not a measured one. Both mutations were applied to the tree, run, and reverted;
`git status` was clean after each and the mutated file's sha256 was restored to its pre-run value.

### Mutation A — neutralise the cap call at the admission block

```diff
tortoise/hosted_api.py:12776
-            await asyncio.to_thread(enforce_cohort_cost_cap, org)
+            pass  # MUTATION-4623: the cap call neutralised
```

```text
>       assert r.status_code == 402, r.text
E       AssertionError: {"protocol_version":"memory_write_v1","status":"ok", … "extracted":1, …
E           "points":[{"id":"pt_f07a9c…","kind":"statement","text":"the new strategy is durable", …}]}
E       assert 200 == 402
tests/test_cohort_cost_cap.py:263: AssertionError
# fixture: extraction_calls == ['_extract_session_v2']
1 failed in 146.67s
```

With the cap call gone the over-cap cohort is **served, extraction runs and a Point is written** —
verbatim the failure the objective is about. `tortoise/hosted_api.py` sha256 restored to
`67195dea0dd6739627ed32a1c3dec3ec1f34130771651faf23c44db36fc9ff3c`.

### Mutation B — invert the comparison at the gate

```diff
tortoise/cohort_cost.py:458
-    if spent >= resolved.cap_usd:
+    if spent < resolved.cap_usd:  # MUTATION-4623: comparison inverted
```

Both directions RED, which is the point — the gate is pinned, not just the call site:

```text
FAILED tests/test_cohort_cost_cap.py::test_over_cap_cohort_capture_refused_402_no_extraction_no_write
    assert 200 == 402            (over-cap cohort served)
FAILED tests/test_cohort_cost_cap.py::test_below_cap_cohort_capture_proceeds_and_extracts
    assert 402 == 200            (below-cap cohort refused)  ← the negative control
2 failed in 153.30s
```

The negative control's failure also printed the real refusal body — the caller-legible half of the
clause, verbatim:

```json
{"detail":{"code":"cohort_cost_cap","message":"Cohort LLM spend cap reached for this billing
period. This request started no extraction and wrote no capture data — re-POST the same session
after 2026-11-01T00:00:00+00:00 and it will extract normally. Need more? Contact
support@premiselabs.co."}}
```

`tortoise/cohort_cost.py` sha256 restored to
`a39138f124c0ba5cc2c159baa8901971bfb382aa6244866278cd71ad729c4088`.

## 4. The negative controls (why this is a refusal, not a wall)

| test | line | what it forbids |
|---|---|---|
| `test_below_cap_cohort_capture_proceeds_and_extracts` | `:379` | an under-cap cohort must be **served** |
| `test_org_outside_the_cohort_is_not_capped` | `:400` | the cap is cohort-SCOPED — a pre-cohort org is never caught |
| `test_replay_of_a_captured_session_is_never_capped` | `:421` | a replay writes nothing, so it can never trip the cap |

Adjacent coverage in the same file: the MCP lane
(`test_mcp_capture_reports_err_quota_when_cohort_is_over_cap`, `:589`), the operator-observable
firing (`test_over_cap_refusal_is_observable_as_an_alert_incident`, `:289`), and the fail-closed
paths (`:1129`, `:1159`).

## 5. What this artifact is NOT — the residuals, stated rather than implied

- **Not the free-tier cliff.** The accidental 402 of 2026-09-22 (`#4614`, points 9998/10000) is a
  **per-team plan quota**, refused **silently** client-side. It is evidence that a refusal path
  exists — not evidence that the cap refuses. `#4614` is CLOSED; this artifact does not re-use it.
- **Not a claim that the cap is armed in production.** `#4008` (**open**) records that the only
  cap-arming instruction names the wrong migration (`.env.example` → `20260917000001`, while the
  anchor read needs `20260918000001`). A wrongly-armed cap is a different gap from an unrefused
  over-cap cohort; this artifact discharges the latter only.
- **Not the UX of the refusal.** `#3826` (**open**) owns what the user sees and the team is told.
- **Not a production observation.** `#3823` (**open**) owns observing the instrumentation in
  production — merged is not shipped.
- **Lane honesty.** Measured in the embedded carve-out lane. The Docker lane was **not** run (no
  Docker daemon on the measuring host); it is not a semantic gap for this file, because every test
  binds its own embedded DB through `tests._http_fixtures.patched_tortoise_sdk` regardless of any
  `TORTOISE_DB_URI`, and the file is registered for the Docker selection via `core`/`api`. The same
  embedded-lane measurement on 2026-09-26 was **3 of 19 RED** (redislite residue, filed as `#2875`,
  not a defect of this file); re-measured here there were **0** orphaned `redis-server` processes
  and **21/21** pass.

## 6. Stale provenance corrected at measurement time

`#4623`'s body lists four open issues on this path. Re-checked 2026-10-12:

| issue | body said | measured |
|---|---|---|
| `#3825` metering_records is monthly-granular | open | **CLOSED** 2026-10-05 |
| `#3981` the fail-closed window refusal is swallowed | open | **CLOSED** 2026-09-23 |
| `#4008` the cap-arming instruction names the wrong migration | open | **OPEN** (unchanged) |
| `#3826` what the user sees when the cap refuses | open | **OPEN** (unchanged) |

## 7. CI registration

`config/ci-surfaces.yml` registers `test_cohort_cost_cap.py` **dual** — under `api` (`:493`) and
under `core` (`:1462`) — because the gate, the ledger write, the reader and the RPC wrappers live in
`tortoise/hosted_api.py`, `tortoise/metering.py` and `tortoise/supabase_control.py`, which SELECT the
`api` surface, and a named-surface match REPLACES the `core` fallback. Verified:

```bash
.venv/bin/python tools/ci_selection.py --integrity
# ✅ integrity: all test files classified; slow_files consistent; shards consistent
```

## 8. Re-run recipe

```bash
uv sync --extra embeddings --extra parity
env TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_cohort_cost_cap.py -q -p no:cacheprovider
```
